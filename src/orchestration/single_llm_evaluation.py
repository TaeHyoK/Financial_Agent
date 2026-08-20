"""Blind cross-order evaluation of Revised Full versus Single-LLM reports."""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from orchestration.config import DEFAULT_ENV_FILE, load_project_env, safe_label
from orchestration.final_report_evaluation_bundle import (
    assert_candidate_neutral,
    build_common_evidence_bundle,
    extract_visible_report,
    file_sha256,
)
from orchestration.final_report_evaluation_cli import (
    _load_or_call_judgment,
    _write_json,
    _write_request_preview,
    _write_text,
    render_summary_markdown,
    summarize_evaluation_usage,
)
from orchestration.final_report_evaluation_metrics import (
    AXES,
    aggregate_pair_results,
    aggregate_recommendation_records,
    reconcile_cross_order_judgments,
)
from orchestration.final_report_pairwise_judge import (
    DEFAULT_JUDGE_MODEL,
    DEFAULT_PROMPT_PATH,
    EVIDENCE_SCOPES,
    build_judge_request,
    call_pairwise_judge,
    request_fingerprint,
)
from orchestration.paths import PROJECT_ROOT
from shared.llm_clients import compact_json


DEFAULT_OUTPUT_ROOT = (
    PROJECT_ROOT / "Output_total" / "Evaluation" / "Final_Report_Single_LLM"
)
DEFAULT_EVALUATION_ID = "paper_revised_full_vs_single_llm_v3"


@dataclass(frozen=True)
class SingleLLMPair:
    suite_id: str
    case_id: str
    company_name: str
    replicate: int
    full_report: Path
    single_report: Path
    full_packet: Path
    single_bundle: Path
    single_validation: Path
    single_manifest: Path
    full_recommendation: str
    single_recommendation: str

    @property
    def pair_id(self) -> str:
        return safe_label(
            f"{self.suite_id}__{self.case_id}__single_llm__r{self.replicate:02d}"
        )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--revised-suite-root", action="append", required=True, type=Path)
    parser.add_argument("--single-experiment-root", required=True, type=Path)
    parser.add_argument("--judge-model", default=DEFAULT_JUDGE_MODEL)
    parser.add_argument("--prompt-path", type=Path, default=DEFAULT_PROMPT_PATH)
    parser.add_argument(
        "--evidence-mode",
        choices=EVIDENCE_SCOPES,
        default="candidate_specific",
        help=(
            "candidate_specific sends each candidate's accessible evidence; "
            "union_blind exposes only the common union for fact checking."
        ),
    )
    parser.add_argument(
        "--candidate-snapshot-root",
        type=Path,
        default=None,
        help=(
            "Prior Single-LLM evaluation root containing comparisons/<pair_id>/"
            "candidate_{full,single_llm}_visible.json. When supplied, every pair "
            "must use the archived Judge-visible reports."
        ),
    )
    parser.add_argument("--env-file", type=Path, default=DEFAULT_ENV_FILE)
    parser.add_argument("--output-root", type=Path, default=DEFAULT_OUTPUT_ROOT)
    parser.add_argument("--evaluation-id", default=DEFAULT_EVALUATION_ID)
    parser.add_argument("--timeout-seconds", type=float, default=300.0)
    parser.add_argument("--transport-retries", type=int, default=1)
    parser.add_argument("--bootstrap-samples", type=int, default=10_000)
    parser.add_argument("--bootstrap-seed", type=int, default=20260806)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--fail-fast", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    summary = run_single_llm_evaluation(args)
    print(json.dumps(summary["counts"], ensure_ascii=False))
    return 0 if summary["status"] in {"success", "dry_run"} else 1


def run_single_llm_evaluation(
    args: argparse.Namespace,
    *,
    judge_call: Callable[..., dict[str, Any]] = call_pairwise_judge,
) -> dict[str, Any]:
    if args.evidence_mode == "union_blind" and (
        Path(args.prompt_path).expanduser().resolve() == DEFAULT_PROMPT_PATH.resolve()
    ):
        raise ValueError(
            "union_blind requires an explicit neutral --prompt-path; the default "
            "prompt permits candidate-specific evidence-coverage judgments."
        )
    evaluation_id = safe_label(args.evaluation_id, "evaluation")
    output_dir = args.output_root.expanduser().resolve() / evaluation_id
    output_dir.mkdir(parents=True, exist_ok=True)
    usage_manifest = output_dir / "llm_usage_manifest.jsonl"
    manifest_path = output_dir / "experiment_manifest.json"
    summary_path = output_dir / "evaluation_summary.json"
    pairs, recommendation_records = discover_single_llm_pairs(
        args.revised_suite_root,
        single_experiment_root=args.single_experiment_root,
    )
    if not pairs:
        raise ValueError("No complete Revised Full-to-Single-LLM pairs were discovered.")
    env_status = load_project_env(args.env_file)
    if not args.dry_run and not os.getenv("OPENAI_API_KEY"):
        raise RuntimeError("OPENAI_API_KEY is required unless --dry-run is used.")
    os.environ["LLM_USAGE_MANIFEST"] = str(usage_manifest)
    os.environ["LLM_EXECUTION_ID"] = evaluation_id
    os.environ["LLM_RUN_ROLE"] = "evaluation"
    manifest: dict[str, Any] = {
        "schema_version": "single_llm_pairwise_evaluation_v1",
        "evaluation_id": evaluation_id,
        "status": "running",
        "created_at": _now(),
        "request": {
            "revised_suite_roots": [
                str(path.expanduser().resolve()) for path in args.revised_suite_root
            ],
            "single_experiment_root": str(
                args.single_experiment_root.expanduser().resolve()
            ),
            "baseline_condition": "revised_full",
            "comparison_condition": "single_llm",
            "judge_model": args.judge_model,
            "prompt_path": str(args.prompt_path.expanduser().resolve()),
            "prompt_sha256": file_sha256(args.prompt_path),
            "evidence_mode": args.evidence_mode,
            "candidate_snapshot_root": (
                str(args.candidate_snapshot_root.expanduser().resolve())
                if args.candidate_snapshot_root is not None
                else None
            ),
            "cross_order": True,
            "cross_order_reconciliation": "both orders must select the same identity; otherwise tie",
            "evidence_policy": (
                "candidate-neutral union is used only for fact checking and candidate "
                "access metadata is hidden"
                if args.evidence_mode == "union_blind"
                else "candidate-neutral union with candidate-specific access metadata"
            ),
            "bootstrap_samples": args.bootstrap_samples,
            "bootstrap_seed": args.bootstrap_seed,
            "dry_run": bool(args.dry_run),
        },
        "environment": env_status,
        "planned_pairs": len(pairs),
        "expected_judge_calls": len(pairs) * 2,
        "pairs": [],
    }
    _write_json(manifest_path, manifest)
    results: list[dict[str, Any]] = []
    for pair in pairs:
        print(f"[single-llm-judge] START {pair.pair_id}", flush=True)
        snapshot = _load_candidate_snapshot(pair.pair_id, args.candidate_snapshot_root)
        result = evaluate_single_llm_pair(
            pair,
            output_dir=output_dir,
            model=args.judge_model,
            prompt_path=args.prompt_path,
            timeout_seconds=args.timeout_seconds,
            transport_retries=args.transport_retries,
            dry_run=bool(args.dry_run),
            force=bool(args.force),
            judge_call=judge_call,
            evidence_mode=args.evidence_mode,
            full_visible_override=(snapshot or {}).get("full"),
            single_visible_override=(snapshot or {}).get("single_llm"),
            candidate_snapshot_provenance=(snapshot or {}).get("provenance"),
        )
        results.append(result)
        manifest["pairs"] = results
        _write_json(manifest_path, manifest)
        print(
            f"[single-llm-judge] END {pair.pair_id} status={result['status']}",
            flush=True,
        )
        if result["status"] == "failed" and args.fail_fast:
            break
    successful = [item for item in results if item.get("status") == "success"]
    failed = [item for item in results if item.get("status") == "failed"]
    dry_runs = [item for item in results if item.get("status") == "dry_run"]
    aggregation = aggregate_pair_results(
        results,
        bootstrap_samples=args.bootstrap_samples,
        seed=args.bootstrap_seed,
    )
    recommendation_analysis = aggregate_recommendation_records(recommendation_records)
    usage = summarize_evaluation_usage(usage_manifest, execution_id=evaluation_id)
    usage.update(
        {
            "planned_judgments": len(pairs) * 2,
            "completed_judgments": len(successful) * 2,
        }
    )
    status = (
        "complete_with_failures"
        if failed
        else "dry_run"
        if dry_runs and not successful
        else "success"
    )
    summary = {
        "schema_version": "single_llm_pairwise_summary_v1",
        "evaluation_id": evaluation_id,
        "status": status,
        "counts": {
            "planned_pairs": len(pairs),
            "completed_pairs": len(results),
            "successful_pairs": len(successful),
            "failed_pairs": len(failed),
            "dry_run_pairs": len(dry_runs),
        },
        "aggregation": aggregation,
        "recommendation_analysis": recommendation_analysis,
        "single_llm_generation": summarize_single_generation(pairs),
        "llm_usage": usage,
        "pairs": results,
    }
    manifest["status"] = status
    manifest["completed_at"] = _now()
    manifest["llm_usage"] = usage
    _write_json(manifest_path, manifest)
    _write_json(summary_path, summary)
    _write_text(output_dir / "evaluation_summary.md", render_summary_markdown(summary))
    _write_csv_tables(output_dir, summary)
    return summary


def discover_single_llm_pairs(
    revised_suite_roots: list[Path],
    *,
    single_experiment_root: Path,
) -> tuple[list[SingleLLMPair], list[dict[str, Any]]]:
    single_root = single_experiment_root.expanduser().resolve()
    pairs: list[SingleLLMPair] = []
    recommendations: list[dict[str, Any]] = []
    for root_value in revised_suite_roots:
        suite_root = root_value.expanduser().resolve()
        summary = _load_json(suite_root / "ablation_summary.json")
        suite_id = str(summary.get("suite_id") or suite_root.name)
        full_rows = sorted(
            [
                item
                for item in summary.get("runs") or []
                if isinstance(item, dict)
                and item.get("condition") == "full"
                and item.get("status") == "success"
            ],
            key=lambda item: int(item.get("replicate") or 0),
        )
        for row in full_rows:
            replicate = int(row.get("replicate") or 0)
            full_report = Path(str(row.get("report_html") or "")).expanduser().resolve()
            full_manifest = _load_json(Path(str(row.get("pipeline_manifest") or "")))
            outputs = full_manifest.get("outputs") if isinstance(full_manifest.get("outputs"), dict) else {}
            full_packet = Path(
                str(outputs.get("strategy_compact_packet_v2") or "")
            ).expanduser().resolve()
            if not full_report.is_file() or not full_packet.is_file():
                raise ValueError(f"Invalid Revised Full artifacts: {suite_id} r{replicate:02d}")
            target_run_key = full_report.parent.name
            single_dir = single_root / target_run_key / f"r{replicate:02d}"
            single_manifest_path = single_dir / "run_manifest.json"
            single_validation_path = single_dir / "validation.json"
            single_report_path = single_dir / "report.json"
            single_html = single_dir / "report.html"
            single_bundle = single_dir / "input_bundle.json"
            single_manifest = _load_json(single_manifest_path)
            validation = _load_json(single_validation_path)
            single_report_json = _load_json(single_report_path)
            if single_manifest.get("status") not in {"valid", "validation_failed"}:
                raise ValueError(f"Single-LLM generation is not complete: {single_dir}")
            if validation.get("status") not in {"valid", "invalid"}:
                raise ValueError(f"Single-LLM validation is missing: {single_dir}")
            if not single_html.is_file() or not single_bundle.is_file():
                raise ValueError(f"Single-LLM HTML or bundle is missing: {single_dir}")
            bundle = _load_json(single_bundle)
            company_name = str((bundle.get("target") or {}).get("company_name") or "")
            case_id = f"{suite_id}:{target_run_key}"
            full_recommendation = _normalize_recommendation(row.get("recommendation"))
            single_recommendation = _normalize_recommendation(
                (single_report_json.get("investment_call") or {}).get("recommendation")
            )
            pairs.append(
                SingleLLMPair(
                    suite_id=suite_id,
                    case_id=case_id,
                    company_name=company_name,
                    replicate=replicate,
                    full_report=full_report,
                    single_report=single_html,
                    full_packet=full_packet,
                    single_bundle=single_bundle,
                    single_validation=single_validation_path,
                    single_manifest=single_manifest_path,
                    full_recommendation=full_recommendation,
                    single_recommendation=single_recommendation,
                )
            )
            recommendations.extend(
                [
                    {
                        "case_id": case_id,
                        "company_name": company_name,
                        "condition": "full",
                        "replicate": replicate,
                        "recommendation": full_recommendation,
                    },
                    {
                        "case_id": case_id,
                        "company_name": company_name,
                        "condition": "single_llm",
                        "replicate": replicate,
                        "recommendation": single_recommendation,
                    },
                ]
            )
    return pairs, recommendations


def evaluate_single_llm_pair(
    pair: SingleLLMPair,
    *,
    output_dir: Path,
    model: str,
    prompt_path: Path,
    timeout_seconds: float,
    transport_retries: int,
    dry_run: bool,
    force: bool,
    judge_call: Callable[..., dict[str, Any]],
    evidence_mode: str = "candidate_specific",
    full_visible_override: dict[str, Any] | None = None,
    single_visible_override: dict[str, Any] | None = None,
    candidate_snapshot_provenance: dict[str, Any] | None = None,
) -> dict[str, Any]:
    pair_dir = output_dir / "comparisons" / pair.pair_id
    judgments_dir = pair_dir / "judgments"
    judgments_dir.mkdir(parents=True, exist_ok=True)
    full_visible = (
        full_visible_override
        if full_visible_override is not None
        else extract_visible_report(pair.full_report)
    )
    single_visible = (
        single_visible_override
        if single_visible_override is not None
        else extract_visible_report(pair.single_report)
    )
    if (full_visible_override is None) != (single_visible_override is None):
        raise ValueError("Full and Single-LLM candidate snapshots must be supplied together.")
    if evidence_mode not in EVIDENCE_SCOPES:
        raise ValueError(f"Unsupported evidence mode: {evidence_mode}")
    full_evidence = build_common_evidence_bundle(pair.full_packet)
    single_evidence = build_single_llm_evidence_bundle(_load_json(pair.single_bundle))
    evidence = union_evidence_bundles(full_evidence, single_evidence)
    full_keys = _bundle_card_keys(full_evidence)
    single_keys = _bundle_card_keys(single_evidence)
    _write_json(pair_dir / "common_evidence_bundle.json", evidence)
    _write_json(pair_dir / "candidate_full_visible.json", full_visible)
    _write_json(pair_dir / "candidate_single_llm_visible.json", single_visible)
    _write_json(
        pair_dir / "identity_map.json",
        {
            "candidate_identity_hidden_from_judge": True,
            "evidence_mode": evidence_mode,
            "baseline_condition": "revised_full",
            "comparison_condition": "single_llm",
            "source_reports": {
                "revised_full": str(pair.full_report),
                "single_llm": str(pair.single_report),
            },
        },
    )
    base = {
        "pair_id": pair.pair_id,
        "suite_id": pair.suite_id,
        "case_id": pair.case_id,
        "company_name": pair.company_name,
        "replicate": pair.replicate,
        "ablation_condition": "single_llm",
        "baseline_recommendation": pair.full_recommendation,
        "ablation_recommendation": pair.single_recommendation,
        "evidence_mode": evidence_mode,
        "evidence_scope": {
            "full_card_count": len(full_keys),
            "single_llm_card_count": len(single_keys),
            "judge_union_card_count": len(_bundle_card_keys(evidence)),
            "candidate_access_metadata_sent": evidence_mode == "candidate_specific",
        },
        "candidate_input": (
            candidate_snapshot_provenance
            if candidate_snapshot_provenance is not None
            else {"mode": "live_report_extraction"}
        ),
        "source_hashes": {
            "full_report": file_sha256(pair.full_report),
            "single_llm_report": file_sha256(pair.single_report),
            "full_strategy_packet": file_sha256(pair.full_packet),
            "single_llm_input_bundle": file_sha256(pair.single_bundle),
            "full_candidate_visible": _json_payload_sha256(full_visible),
            "single_llm_candidate_visible": _json_payload_sha256(single_visible),
            "evidence_bundle": evidence["bundle_sha256"],
        },
        "output_dir": str(pair_dir),
    }
    try:
        common_request_args = {
            "evidence_bundle": evidence,
            "model": model,
            "prompt_path": prompt_path,
            "evidence_scope": evidence_mode,
        }
        if evidence_mode == "candidate_specific":
            request_ab = build_judge_request(
                candidate_a=full_visible,
                candidate_b=single_visible,
                candidate_a_available_card_keys=full_keys,
                candidate_b_available_card_keys=single_keys,
                candidate_a_evidence_bundle=full_evidence,
                candidate_b_evidence_bundle=single_evidence,
                **common_request_args,
            )
            request_ba = build_judge_request(
                candidate_a=single_visible,
                candidate_b=full_visible,
                candidate_a_available_card_keys=single_keys,
                candidate_b_available_card_keys=full_keys,
                candidate_a_evidence_bundle=single_evidence,
                candidate_b_evidence_bundle=full_evidence,
                **common_request_args,
            )
        else:
            request_ab = build_judge_request(
                candidate_a=full_visible,
                candidate_b=single_visible,
                **common_request_args,
            )
            request_ba = build_judge_request(
                candidate_a=single_visible,
                candidate_b=full_visible,
                **common_request_args,
            )
        _write_request_preview(judgments_dir / "order_ab_request.json", request_ab)
        _write_request_preview(judgments_dir / "order_ba_request.json", request_ba)
        if dry_run:
            return {
                **base,
                "status": "dry_run",
                "request_fingerprints": {
                    "order_ab": request_fingerprint(request_ab),
                    "order_ba": request_fingerprint(request_ba),
                },
            }
        os.environ["LLM_RUN_ID"] = f"{pair.pair_id}:order_ab"
        os.environ["LLM_COMPANY_NAME"] = pair.company_name
        order_ab, cached_ab = _load_or_call_judgment(
            judgments_dir / "order_ab.json",
            request_ab,
            force=force,
            judge_call=judge_call,
            timeout_seconds=timeout_seconds,
            transport_retries=transport_retries,
        )
        os.environ["LLM_RUN_ID"] = f"{pair.pair_id}:order_ba"
        order_ba, cached_ba = _load_or_call_judgment(
            judgments_dir / "order_ba.json",
            request_ba,
            force=force,
            judge_call=judge_call,
            timeout_seconds=timeout_seconds,
            transport_retries=transport_retries,
        )
        reconciled = reconcile_cross_order_judgments(
            order_ab,
            order_ba,
            allowed_card_keys=_bundle_card_keys(evidence),
        )
        result = {
            **base,
            **reconciled,
            "cache": {"order_ab": cached_ab, "order_ba": cached_ba},
        }
    except Exception as exc:
        result = {
            **base,
            "status": "failed",
            "error": {"type": type(exc).__name__, "message": str(exc)},
        }
    _write_json(pair_dir / "pairwise_result.json", result)
    return result


def build_single_llm_evidence_bundle(bundle: dict[str, Any]) -> dict[str, Any]:
    cards = []
    for item in bundle.get("evidence_catalog") or []:
        if not isinstance(item, dict) or not item.get("evidence_id"):
            continue
        cards.append(
            {
                "card_key": str(item["evidence_id"]),
                "domain": item.get("domain"),
                "label": item.get("evidence_type"),
                "evidence_family": item.get("domain"),
                "observation_basis": "frozen_raw_or_deterministic_source",
                "comparison_scope": str(item.get("role") or ""),
                "decision_use": "factor_eligible",
                "as_of_date": item.get("as_of_date"),
                "primary_observation": item.get("payload"),
                "reader_limitations": [],
            }
        )
    target = bundle.get("target") if isinstance(bundle.get("target"), dict) else {}
    evidence = {
        "version": "single_llm_candidate_neutral_evidence_v1",
        "target_company": {
            "company_name": target.get("company_name"),
            "run_key": target.get("run_key"),
            "as_of_date": bundle.get("selected_date"),
            "ticker": target.get("ticker"),
            "corp_code": target.get("corp_code"),
        },
        "selected_date_policy": bundle.get("selected_date_policy"),
        "coverage_summary": {
            "card_count": len(cards),
            "news_selection": bundle.get("news_selection") or {},
        },
        "cards": cards,
        "reader_limitations": [
            "Single-LLM 입력은 고정 token budget에 따라 낮은 순위 뉴스가 결정론적으로 제외될 수 있다."
        ],
        "limitation_requirements": [],
        "source_bundle_sha256": bundle.get("bundle_sha256"),
    }
    evidence["bundle_sha256"] = _payload_hash(evidence)
    assert_candidate_neutral(evidence)
    return evidence


def union_evidence_bundles(*bundles: dict[str, Any]) -> dict[str, Any]:
    identities = {
        (
            str((bundle.get("target_company") or {}).get("company_name") or ""),
            str((bundle.get("target_company") or {}).get("as_of_date") or ""),
        )
        for bundle in bundles
    }
    if len(identities) != 1:
        raise ValueError(f"Evidence bundle identities differ: {identities}")
    cards: dict[str, dict[str, Any]] = {}
    for bundle in bundles:
        for card in bundle.get("cards") or []:
            if isinstance(card, dict) and card.get("card_key"):
                key = str(card["card_key"])
                if key in cards and cards[key] != card:
                    raise ValueError(f"Conflicting evidence card key: {key}")
                cards[key] = card
    target = bundles[0].get("target_company") or {}
    result = {
        "version": "revised_full_single_llm_evidence_union_v1",
        "target_company": target,
        "selected_date_policy": bundles[0].get("selected_date_policy"),
        "coverage_summary": {
            "union_card_count": len(cards),
            "source_bundle_count": len(bundles),
        },
        "cards": [cards[key] for key in sorted(cards)],
        "reader_limitations": _unique_json_items(
            item for bundle in bundles for item in bundle.get("reader_limitations") or []
        ),
        "limitation_requirements": _unique_json_items(
            item for bundle in bundles for item in bundle.get("limitation_requirements") or []
        ),
        "source_bundle_sha256s": sorted(
            str(bundle.get("bundle_sha256") or "") for bundle in bundles
        ),
    }
    result["bundle_sha256"] = _payload_hash(result)
    assert_candidate_neutral(result)
    return result


def summarize_single_generation(pairs: list[SingleLLMPair]) -> dict[str, Any]:
    manifests = [_load_json(pair.single_manifest) for pair in pairs]
    validations = [_load_json(pair.single_validation) for pair in pairs]
    usage_keys = (
        "input_tokens",
        "cached_input_tokens",
        "output_tokens",
        "reasoning_tokens",
        "total_tokens",
    )
    return {
        "valid_reports": sum(item.get("status") == "valid" for item in manifests),
        "report_count": len(manifests),
        "validation_pass_rate": (
            sum(item.get("status") == "valid" for item in validations) / len(validations)
            if validations
            else None
        ),
        "mean_numeric_grounding_precision": (
            sum(float((item.get("numeric_grounding") or {}).get("precision") or 0.0) for item in validations)
            / len(validations)
            if validations
            else None
        ),
        "semantic_generation_calls": sum(
            int(item.get("semantic_generation_attempts") or 0) for item in manifests
        ),
        "usage": {
            key: sum(int((item.get("usage") or {}).get(key) or 0) for item in manifests)
            for key in usage_keys
        },
    }


def _write_csv_tables(output_dir: Path, summary: dict[str, Any]) -> None:
    by_condition = (summary.get("aggregation") or {}).get("by_condition") or {}
    result = by_condition.get("single_llm") or {}
    overall = result.get("overall") or {}
    overall_rows = [
        {
            "comparison": "Revised Full vs Single LLM",
            "report_pairs": result.get("valid_pairs"),
            "full_win": overall.get("full_win"),
            "tie": overall.get("tie"),
            "single_llm_win": overall.get("ablation_win"),
            "adjusted_win_rate_for_revised_full": overall.get(
                "adjusted_win_rate_for_full"
            ),
            "ci_95_low": (overall.get("ci_95") or [None, None])[0],
            "ci_95_high": (overall.get("ci_95") or [None, None])[1],
            "order_consistency": result.get("mean_order_consistency"),
        }
    ]
    axis_rows = []
    for axis in AXES:
        item = (result.get("axes") or {}).get(axis) or {}
        axis_rows.append(
            {
                "axis": axis,
                "full_win": item.get("full_win"),
                "tie": item.get("tie"),
                "single_llm_win": item.get("ablation_win"),
                "adjusted_win_rate_for_revised_full": item.get(
                    "adjusted_win_rate_for_full"
                ),
                "ci_95_low": (item.get("ci_95") or [None, None])[0],
                "ci_95_high": (item.get("ci_95") or [None, None])[1],
            }
        )
    _write_csv(output_dir / "table_single_llm_overall.csv", overall_rows)
    _write_csv(output_dir / "table_single_llm_axes.csv", axis_rows)
    company_rows = _single_llm_company_rows(summary.get("pairs") or [])
    _write_csv(output_dir / "table_single_llm_company.csv", company_rows)
    _write_markdown_tables(output_dir, overall_rows, axis_rows, company_rows)


def _single_llm_company_rows(pair_results: list[dict[str, Any]]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    companies = sorted(
        {
            str(item.get("company_name") or "")
            for item in pair_results
            if item.get("status") == "success" and item.get("company_name")
        }
    )
    for company in companies:
        selected = [
            item
            for item in pair_results
            if item.get("status") == "success" and item.get("company_name") == company
        ]
        outcomes = [
            str((item.get("axes") or {}).get(axis, {}).get("outcome") or "")
            for item in selected
            for axis in AXES
        ]
        scores = [
            float((item.get("axes") or {}).get(axis, {}).get("score_for_full"))
            for item in selected
            for axis in AXES
        ]
        rows.append(
            {
                "company": company,
                "report_pairs": len(selected),
                "full_win": outcomes.count("full_win"),
                "tie": outcomes.count("tie"),
                "single_llm_win": outcomes.count("ablation_win"),
                "adjusted_win_rate_for_revised_full": (
                    sum(scores) / len(scores) if scores else None
                ),
                "order_consistency": (
                    sum(float(item.get("order_consistency_rate") or 0.0) for item in selected)
                    / len(selected)
                    if selected
                    else None
                ),
            }
        )
    return rows


def _write_markdown_tables(
    output_dir: Path,
    overall_rows: list[dict[str, Any]],
    axis_rows: list[dict[str, Any]],
    company_rows: list[dict[str, Any]],
) -> None:
    overall_lines = [
        "| 비교 | 보고서쌍 | Full 승/무/패 | Full 조정 승률 | 95% CI | 순서 일치율 |",
        "| --- | ---: | ---: | ---: | ---: | ---: |",
    ]
    for row in overall_rows:
        overall_lines.append(
            f"| Revised Full vs Single LLM | {row['report_pairs']} | "
            f"{row['full_win']}/{row['tie']}/{row['single_llm_win']} | "
            f"{_format_percent(row['adjusted_win_rate_for_revised_full'])} | "
            f"[{_format_percent(row['ci_95_low'])}, {_format_percent(row['ci_95_high'])}] | "
            f"{_format_percent(row['order_consistency'])} |"
        )
    _write_text(output_dir / "table_single_llm_overall.md", "\n".join(overall_lines) + "\n")

    axis_labels = {
        "financial_numeric": "재무·수치",
        "news": "뉴스 활용",
        "company_market_peer": "기업·시장·경쟁사",
        "investment": "투자 판단",
        "risk": "리스크",
        "writing": "작성 품질",
    }
    axis_lines = [
        "| 평가축 | Full 승/무/패 | Full 조정 승률 | 95% CI |",
        "| --- | ---: | ---: | ---: |",
    ]
    for row in axis_rows:
        axis_lines.append(
            f"| {axis_labels.get(str(row['axis']), row['axis'])} | "
            f"{row['full_win']}/{row['tie']}/{row['single_llm_win']} | "
            f"{_format_percent(row['adjusted_win_rate_for_revised_full'])} | "
            f"[{_format_percent(row['ci_95_low'])}, {_format_percent(row['ci_95_high'])}] |"
        )
    _write_text(output_dir / "table_single_llm_axes.md", "\n".join(axis_lines) + "\n")

    company_lines = [
        "| 기업 | 보고서쌍 | Full 승/무/패 | Full 조정 승률 | 순서 일치율 |",
        "| --- | ---: | ---: | ---: | ---: |",
    ]
    for row in company_rows:
        company_lines.append(
            f"| {row['company']} | {row['report_pairs']} | "
            f"{row['full_win']}/{row['tie']}/{row['single_llm_win']} | "
            f"{_format_percent(row['adjusted_win_rate_for_revised_full'])} | "
            f"{_format_percent(row['order_consistency'])} |"
        )
    _write_text(output_dir / "table_single_llm_company.md", "\n".join(company_lines) + "\n")


def _format_percent(value: Any) -> str:
    return "—" if value is None else f"{float(value):.1%}"


def _write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]) if rows else [])
        if rows:
            writer.writeheader()
            writer.writerows(rows)


def _bundle_card_keys(bundle: dict[str, Any]) -> list[str]:
    return sorted(
        str(item.get("card_key"))
        for item in bundle.get("cards") or []
        if isinstance(item, dict) and item.get("card_key")
    )


def _load_candidate_snapshot(
    pair_id: str,
    root_value: Path | None,
) -> dict[str, Any] | None:
    """Load the exact Judge-visible reports archived by the prior evaluation."""

    if root_value is None:
        return None
    root = root_value.expanduser().resolve()
    pair_dir = root / "comparisons" / pair_id
    full_path = pair_dir / "candidate_full_visible.json"
    single_path = pair_dir / "candidate_single_llm_visible.json"
    if not full_path.is_file() or not single_path.is_file():
        raise ValueError(f"Incomplete Single-LLM candidate snapshot: {pair_dir}")
    full = _load_json(full_path)
    single = _load_json(single_path)
    return {
        "full": full,
        "single_llm": single,
        "provenance": {
            "mode": "frozen_judge_visible_snapshot",
            "snapshot_root": str(root),
            "full_path": str(full_path),
            "single_llm_path": str(single_path),
            "full_sha256": _json_payload_sha256(full),
            "single_llm_sha256": _json_payload_sha256(single),
        },
    }


def _json_payload_sha256(payload: Any) -> str:
    serialized = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(serialized.encode("utf-8")).hexdigest()


def _payload_hash(payload: dict[str, Any]) -> str:
    content = {key: value for key, value in payload.items() if key != "bundle_sha256"}
    return hashlib.sha256(
        compact_json(content, sort_keys=True).encode("utf-8")
    ).hexdigest()


def _unique_json_items(values: Any) -> list[Any]:
    unique: dict[str, Any] = {}
    for value in values:
        unique[compact_json(value, sort_keys=True)] = value
    return [unique[key] for key in sorted(unique)]


def _normalize_recommendation(value: Any) -> str:
    normalized = str(value or "").strip().upper()
    return {"BUY": "Buy", "HOLD": "Hold", "SELL": "Sell"}.get(
        normalized, str(value or "")
    )


def _load_json(path: str | Path) -> dict[str, Any]:
    resolved = Path(path).expanduser().resolve()
    payload = json.loads(resolved.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"Expected a JSON object: {resolved}")
    return payload


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


if __name__ == "__main__":
    raise SystemExit(main())
