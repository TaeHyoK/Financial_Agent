"""CLI for FinRpt-adapted blind evaluation of Full versus ablation reports."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import tempfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from orchestration.ablation_experiment import code_identity
from orchestration.config import DEFAULT_ENV_FILE, load_project_env, safe_label
from orchestration.final_report_evaluation_bundle import (
    build_common_evidence_bundle,
    build_union_evidence_bundle,
    extract_visible_report,
    file_sha256,
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
from shared.llm_clients import measure_request


DEFAULT_ABLATIONS = ("no_sy", "no_competitor", "primary_only")
DEFAULT_OUTPUT_ROOT = PROJECT_ROOT / "Output_total" / "Evaluation" / "Final_Report_Ablation"


@dataclass(frozen=True)
class PairSpec:
    suite_id: str
    case_id: str
    company_name: str
    replicate: int
    ablation_condition: str
    baseline_report: Path
    ablation_report: Path
    common_packet: Path
    ablation_packet: Path
    baseline_recommendation: str
    ablation_recommendation: str

    @property
    def pair_id(self) -> str:
        return safe_label(
            f"{self.suite_id}__{self.case_id}__{self.ablation_condition}__r{self.replicate:02d}"
        )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Blind A/B and B/A LLM Judge evaluation for final ablation reports."
    )
    parser.add_argument(
        "--suite-root",
        action="append",
        required=True,
        type=Path,
        help="Ablation suite root containing ablation_summary.json; repeatable.",
    )
    parser.add_argument("--baseline-condition", default="full")
    parser.add_argument(
        "--ablation",
        action="append",
        default=[],
        help=(
            "Ablation condition to compare with Full; repeatable. Defaults to "
            "no_sy, no_competitor, and primary_only."
        ),
    )
    parser.add_argument("--judge-model", default=DEFAULT_JUDGE_MODEL)
    parser.add_argument("--prompt-path", type=Path, default=DEFAULT_PROMPT_PATH)
    parser.add_argument(
        "--evidence-mode",
        choices=EVIDENCE_SCOPES,
        default="candidate_specific",
        help=(
            "candidate_specific preserves the coverage-aware main evaluation; "
            "union_blind uses the union only for fact checking and hides candidate access."
        ),
    )
    parser.add_argument("--env-file", type=Path, default=DEFAULT_ENV_FILE)
    parser.add_argument("--output-root", type=Path, default=DEFAULT_OUTPUT_ROOT)
    parser.add_argument("--evaluation-id", default="")
    parser.add_argument(
        "--candidate-snapshot-root",
        action="append",
        default=[],
        type=Path,
        help=(
            "Prior evaluation root containing comparisons/<pair_id>/candidate_*_visible.json; "
            "repeatable. When supplied, every pair must resolve to exactly one snapshot."
        ),
    )
    parser.add_argument("--timeout-seconds", type=_positive_float, default=300.0)
    parser.add_argument("--transport-retries", type=_non_negative_int, default=1)
    parser.add_argument("--bootstrap-samples", type=_positive_int, default=10_000)
    parser.add_argument("--bootstrap-seed", type=int, default=20260728)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--force", action="store_true", help="Ignore matching cached judgments.")
    parser.add_argument("--fail-fast", action="store_true")
    return parser


def run_evaluation(
    args: argparse.Namespace,
    *,
    judge_call: Callable[..., dict[str, Any]] = call_pairwise_judge,
) -> dict[str, Any]:
    ablations = tuple(dict.fromkeys(args.ablation or DEFAULT_ABLATIONS))
    if args.evidence_mode == "union_blind" and (
        Path(args.prompt_path).expanduser().resolve() == DEFAULT_PROMPT_PATH.resolve()
    ):
        raise ValueError(
            "union_blind requires an explicit neutral --prompt-path; the default prompt "
            "permits rewarding wider candidate-specific evidence coverage."
        )
    evaluation_id = safe_label(args.evaluation_id or _new_evaluation_id(), "evaluation")
    output_dir = Path(args.output_root).expanduser().resolve() / evaluation_id
    output_dir.mkdir(parents=True, exist_ok=True)
    usage_manifest = output_dir / "llm_usage_manifest.jsonl"
    manifest_path = output_dir / "experiment_manifest.json"
    summary_path = output_dir / "evaluation_summary.json"
    summary_md_path = output_dir / "evaluation_summary.md"

    env_status = load_project_env(args.env_file)
    if not args.dry_run and not os.getenv("OPENAI_API_KEY"):
        raise RuntimeError("OPENAI_API_KEY is required unless --dry-run is used.")
    pair_specs, recommendation_records = discover_pair_specs(
        args.suite_root,
        baseline_condition=args.baseline_condition,
        ablations=ablations,
    )
    if not pair_specs:
        raise ValueError("No valid Full-to-ablation report pairs were discovered.")

    os.environ["LLM_USAGE_MANIFEST"] = str(usage_manifest)
    os.environ["LLM_EXECUTION_ID"] = evaluation_id
    os.environ["LLM_RUN_ROLE"] = "evaluation"
    manifest: dict[str, Any] = {
        "evaluation_id": evaluation_id,
        "status": "running",
        "created_at": _utc_now(),
        "output_dir": str(output_dir),
        "request": {
            "suite_roots": [str(Path(path).expanduser().resolve()) for path in args.suite_root],
            "baseline_condition": args.baseline_condition,
            "ablations": list(ablations),
            "judge_model": args.judge_model,
            "prompt_path": str(Path(args.prompt_path).expanduser().resolve()),
            "prompt_sha256": file_sha256(args.prompt_path),
            "evidence_mode": args.evidence_mode,
            "candidate_snapshot_roots": [
                str(Path(path).expanduser().resolve())
                for path in args.candidate_snapshot_root
            ],
            "dry_run": bool(args.dry_run),
            "cross_order": True,
            "bootstrap_samples": args.bootstrap_samples,
            "bootstrap_seed": args.bootstrap_seed,
        },
        "environment": env_status,
        "code": code_identity(),
        "evaluation_implementation_sha256": {
            filename: file_sha256(Path(__file__).resolve().parent / filename)
            for filename in (
                "final_report_evaluation_bundle.py",
                "final_report_evaluation_metrics.py",
                "final_report_pairwise_judge.py",
                "final_report_evaluation_cli.py",
            )
        },
        "planned_pairs": len(pair_specs),
        "expected_judge_calls": len(pair_specs) * 2,
        "pairs": [],
    }
    _write_json(manifest_path, manifest)

    pair_results: list[dict[str, Any]] = []
    for spec in pair_specs:
        print(f"[judge] START {spec.pair_id}", flush=True)
        snapshot = _load_candidate_snapshot(spec.pair_id, args.candidate_snapshot_root)
        result = evaluate_pair(
            spec,
            output_dir=output_dir,
            model=args.judge_model,
            prompt_path=args.prompt_path,
            timeout_seconds=args.timeout_seconds,
            transport_retries=args.transport_retries,
            dry_run=args.dry_run,
            force=args.force,
            judge_call=judge_call,
            evidence_mode=args.evidence_mode,
            baseline_visible_override=(snapshot or {}).get("full"),
            ablation_visible_override=(snapshot or {}).get("ablation"),
            candidate_snapshot_provenance=(snapshot or {}).get("provenance"),
        )
        pair_results.append(result)
        manifest["pairs"] = pair_results
        _write_json(manifest_path, manifest)
        print(f"[judge] END {spec.pair_id} status={result['status']}", flush=True)
        if result["status"] == "failed" and args.fail_fast:
            break

    successful = [item for item in pair_results if item.get("status") == "success"]
    failed = [item for item in pair_results if item.get("status") == "failed"]
    dry_runs = [item for item in pair_results if item.get("status") == "dry_run"]
    aggregation = aggregate_pair_results(
        pair_results,
        bootstrap_samples=args.bootstrap_samples,
        seed=args.bootstrap_seed,
    )
    recommendation_analysis = aggregate_recommendation_records(recommendation_records)
    usage = summarize_evaluation_usage(usage_manifest, execution_id=evaluation_id)
    cache_hits = sum(
        bool(cached)
        for item in successful
        for cached in (item.get("cache") or {}).values()
    )
    usage.update(
        {
            "planned_judgments": len(pair_specs) * 2,
            "completed_judgments": len(successful) * 2,
            "cache_hits": cache_hits,
            "transported_judgments": usage["observed_logical_calls"],
        }
    )
    status = (
        "dry_run"
        if dry_runs and not successful and not failed
        else "complete_with_failures"
        if failed
        else "success"
    )
    summary = {
        "evaluation_id": evaluation_id,
        "status": status,
        "counts": {
            "planned_pairs": len(pair_specs),
            "completed_pairs": len(pair_results),
            "successful_pairs": len(successful),
            "failed_pairs": len(failed),
            "dry_run_pairs": len(dry_runs),
        },
        "aggregation": aggregation,
        "recommendation_analysis": recommendation_analysis,
        "llm_usage": usage,
        "pairs": pair_results,
    }
    manifest["status"] = status
    manifest["completed_at"] = _utc_now()
    manifest["llm_usage"] = usage
    _write_json(manifest_path, manifest)
    _write_json(summary_path, summary)
    _write_text(summary_md_path, render_summary_markdown(summary))
    return summary


def discover_pair_specs(
    suite_roots: list[Path],
    *,
    baseline_condition: str,
    ablations: tuple[str, ...],
) -> tuple[list[PairSpec], list[dict[str, Any]]]:
    """Discover validated report pairs from one or more ablation suites."""

    pairs: list[PairSpec] = []
    recommendations: list[dict[str, Any]] = []
    for root_value in suite_roots:
        suite_root = Path(root_value).expanduser().resolve()
        summary_path = suite_root / "ablation_summary.json"
        summary = _load_json(summary_path)
        suite_id = str(summary.get("suite_id") or suite_root.name)
        rows = [item for item in summary.get("runs") or [] if isinstance(item, dict)]
        by_condition_rep = {
            (str(item.get("condition") or ""), int(item.get("replicate") or 0)): item
            for item in rows
        }
        replicates = sorted(
            replicate
            for condition, replicate in by_condition_rep
            if condition == baseline_condition and replicate > 0
        )
        for replicate in replicates:
            baseline = by_condition_rep[(baseline_condition, replicate)]
            _require_valid_row(baseline, label=f"{suite_id}:{baseline_condition}:r{replicate}")
            baseline_report = Path(str(baseline["report_html"])).expanduser().resolve()
            baseline_manifest = _load_json(Path(str(baseline["pipeline_manifest"])))
            outputs = (
                baseline_manifest.get("outputs")
                if isinstance(baseline_manifest.get("outputs"), dict)
                else {}
            )
            common_packet = Path(
                str(outputs.get("strategy_compact_packet_v2") or "")
            ).expanduser().resolve()
            if not common_packet.exists():
                raise ValueError(f"Full Strategy packet is missing: {common_packet}")
            run_key = baseline_report.parent.name
            case_id = f"{suite_id}:{run_key}"
            bundle = build_common_evidence_bundle(common_packet)
            company_name = str(
                (bundle.get("target_company") or {}).get("company_name") or run_key
            )
            recommendations.append(
                {
                    "case_id": case_id,
                    "company_name": company_name,
                    "condition": "full",
                    "replicate": replicate,
                    "recommendation": baseline.get("recommendation"),
                }
            )
            for condition in ablations:
                ablation = by_condition_rep.get((condition, replicate))
                if ablation is None:
                    raise ValueError(f"Missing condition {condition!r} replicate {replicate} in {suite_id}.")
                _require_valid_row(ablation, label=f"{suite_id}:{condition}:r{replicate}")
                ablation_report = Path(str(ablation["report_html"])).expanduser().resolve()
                ablation_manifest = _load_json(Path(str(ablation["pipeline_manifest"])))
                ablation_outputs = (
                    ablation_manifest.get("outputs")
                    if isinstance(ablation_manifest.get("outputs"), dict)
                    else {}
                )
                ablation_packet = Path(
                    str(ablation_outputs.get("strategy_compact_packet_v2") or "")
                ).expanduser().resolve()
                if not ablation_packet.is_file():
                    raise ValueError(
                        f"Ablation Strategy packet is missing: {ablation_packet}"
                    )
                pairs.append(
                    PairSpec(
                        suite_id=suite_id,
                        case_id=case_id,
                        company_name=company_name,
                        replicate=replicate,
                        ablation_condition=condition,
                        baseline_report=baseline_report,
                        ablation_report=ablation_report,
                        common_packet=common_packet,
                        ablation_packet=ablation_packet,
                        baseline_recommendation=str(baseline.get("recommendation") or ""),
                        ablation_recommendation=str(ablation.get("recommendation") or ""),
                    )
                )
                recommendations.append(
                    {
                        "case_id": case_id,
                        "company_name": company_name,
                        "condition": condition,
                        "replicate": replicate,
                        "recommendation": ablation.get("recommendation"),
                    }
                )
    return pairs, _deduplicate_records(recommendations)


def evaluate_pair(
    spec: PairSpec,
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
    baseline_visible_override: dict[str, Any] | None = None,
    ablation_visible_override: dict[str, Any] | None = None,
    candidate_snapshot_provenance: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Evaluate one Full/ablation report pair in both presentation orders."""

    pair_dir = output_dir / "comparisons" / spec.pair_id
    judgments_dir = pair_dir / "judgments"
    pair_dir.mkdir(parents=True, exist_ok=True)
    judgments_dir.mkdir(parents=True, exist_ok=True)
    baseline_visible = (
        baseline_visible_override
        if baseline_visible_override is not None
        else extract_visible_report(spec.baseline_report)
    )
    ablation_visible = (
        ablation_visible_override
        if ablation_visible_override is not None
        else extract_visible_report(spec.ablation_report)
    )
    if (baseline_visible_override is None) != (ablation_visible_override is None):
        raise ValueError("Full and ablation candidate snapshots must be supplied together.")
    if evidence_mode not in EVIDENCE_SCOPES:
        raise ValueError(f"Unsupported evidence mode: {evidence_mode}")
    full_evidence = build_common_evidence_bundle(spec.common_packet)
    ablation_evidence = build_common_evidence_bundle(spec.ablation_packet)
    evidence = build_union_evidence_bundle([spec.common_packet, spec.ablation_packet])
    full_card_keys = _bundle_card_keys(full_evidence)
    ablation_card_keys = _bundle_card_keys(ablation_evidence)
    _write_json(pair_dir / "common_evidence_bundle.json", evidence)
    _write_json(pair_dir / "candidate_full_visible.json", baseline_visible)
    _write_json(pair_dir / "candidate_ablation_visible.json", ablation_visible)
    _write_json(
        pair_dir / "identity_map.json",
        {
            "candidate_identity_hidden_from_judge": True,
            "evidence_mode": evidence_mode,
            "baseline_condition": "full",
            "ablation_condition": spec.ablation_condition,
            "source_reports": {
                "full": str(spec.baseline_report),
                "ablation": str(spec.ablation_report),
            },
        },
    )
    base_result: dict[str, Any] = {
        "pair_id": spec.pair_id,
        "suite_id": spec.suite_id,
        "case_id": spec.case_id,
        "company_name": spec.company_name,
        "replicate": spec.replicate,
        "ablation_condition": spec.ablation_condition,
        "baseline_recommendation": spec.baseline_recommendation,
        "ablation_recommendation": spec.ablation_recommendation,
        "evidence_mode": evidence_mode,
        "evidence_scope": {
            "full_card_count": len(full_card_keys),
            "ablation_card_count": len(ablation_card_keys),
            "judge_union_card_count": len(_bundle_card_keys(evidence)),
            "candidate_access_metadata_sent": evidence_mode == "candidate_specific",
        },
        "candidate_input": (
            candidate_snapshot_provenance
            if candidate_snapshot_provenance is not None
            else {"mode": "live_report_extraction"}
        ),
        "source_hashes": {
            "full_report": file_sha256(spec.baseline_report),
            "ablation_report": file_sha256(spec.ablation_report),
            "full_candidate_visible": _json_payload_sha256(baseline_visible),
            "ablation_candidate_visible": _json_payload_sha256(ablation_visible),
            "full_strategy_packet": file_sha256(spec.common_packet),
            "ablation_strategy_packet": file_sha256(spec.ablation_packet),
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
                candidate_a=baseline_visible,
                candidate_b=ablation_visible,
                candidate_a_available_card_keys=full_card_keys,
                candidate_b_available_card_keys=ablation_card_keys,
                candidate_a_evidence_bundle=full_evidence,
                candidate_b_evidence_bundle=ablation_evidence,
                **common_request_args,
            )
            request_ba = build_judge_request(
                candidate_a=ablation_visible,
                candidate_b=baseline_visible,
                candidate_a_available_card_keys=ablation_card_keys,
                candidate_b_available_card_keys=full_card_keys,
                candidate_a_evidence_bundle=ablation_evidence,
                candidate_b_evidence_bundle=full_evidence,
                **common_request_args,
            )
        else:
            request_ab = build_judge_request(
                candidate_a=baseline_visible,
                candidate_b=ablation_visible,
                **common_request_args,
            )
            request_ba = build_judge_request(
                candidate_a=ablation_visible,
                candidate_b=baseline_visible,
                **common_request_args,
            )
        _write_request_preview(judgments_dir / "order_ab_request.json", request_ab)
        _write_request_preview(judgments_dir / "order_ba_request.json", request_ba)
        if dry_run:
            return {
                **base_result,
                "status": "dry_run",
                "request_fingerprints": {
                    "order_ab": request_fingerprint(request_ab),
                    "order_ba": request_fingerprint(request_ba),
                },
            }

        os.environ["LLM_RUN_ID"] = f"{spec.pair_id}:order_ab"
        os.environ["LLM_COMPANY_NAME"] = spec.company_name
        order_ab, cached_ab = _load_or_call_judgment(
            judgments_dir / "order_ab.json",
            request_ab,
            force=force,
            judge_call=judge_call,
            timeout_seconds=timeout_seconds,
            transport_retries=transport_retries,
        )
        os.environ["LLM_RUN_ID"] = f"{spec.pair_id}:order_ba"
        order_ba, cached_ba = _load_or_call_judgment(
            judgments_dir / "order_ba.json",
            request_ba,
            force=force,
            judge_call=judge_call,
            timeout_seconds=timeout_seconds,
            transport_retries=transport_retries,
        )
        card_keys = [
            str(card.get("card_key"))
            for card in evidence.get("cards") or []
            if isinstance(card, dict) and card.get("card_key")
        ]
        reconciled = reconcile_cross_order_judgments(
            order_ab,
            order_ba,
            allowed_card_keys=card_keys,
        )
        result = {
            **base_result,
            **reconciled,
            "cache": {"order_ab": cached_ab, "order_ba": cached_ba},
        }
        _write_json(pair_dir / "pairwise_result.json", result)
        return result
    except Exception as exc:
        result = {
            **base_result,
            "status": "failed",
            "error": {"type": type(exc).__name__, "message": str(exc)},
        }
        _write_json(pair_dir / "pairwise_result.json", result)
        return result


def summarize_evaluation_usage(path: Path, *, execution_id: str) -> dict[str, Any]:
    rows: list[dict[str, Any]] = []
    if path.exists():
        for line in path.read_text(encoding="utf-8").splitlines():
            try:
                item = json.loads(line)
            except json.JSONDecodeError:
                continue
            if (
                isinstance(item, dict)
                and item.get("execution_id") == execution_id
                and item.get("run_role") == "evaluation"
            ):
                rows.append(item)
    successful = [item for item in rows if item.get("status") == "ok"]
    logical = {
        (
            item.get("run_id"),
            item.get("step"),
            (item.get("request") or {}).get("request_sha256"),
        )
        for item in successful
        if isinstance(item.get("request"), dict)
    }
    usage_keys = (
        "input_tokens",
        "cached_input_tokens",
        "output_tokens",
        "reasoning_tokens",
        "total_tokens",
    )
    usage = {
        key: sum(int((item.get("usage") or {}).get(key) or 0) for item in rows)
        for key in usage_keys
    }
    return {
        "execution_id": execution_id,
        "source": str(path),
        "observed_logical_calls": len(logical),
        "transport_attempts": len(rows),
        "error_attempts": len(rows) - len(successful),
        "usage": usage,
    }


def render_summary_markdown(summary: dict[str, Any]) -> str:
    counts = summary.get("counts") or {}
    lines = [
        "# Final report ablation LLM Judge",
        "",
        f"- Evaluation: `{summary.get('evaluation_id')}`",
        f"- Status: `{summary.get('status')}`",
        f"- Pairs: `{counts.get('successful_pairs', 0)}/{counts.get('planned_pairs', 0)}`",
        "",
        "| Ablation | Valid/Attempted | Full W/T/L | Adjusted Win Rate | 95% CI | Order consistency |",
        "| --- | ---: | ---: | ---: | --- | ---: |",
    ]
    by_condition = ((summary.get("aggregation") or {}).get("by_condition") or {})
    for condition, result in by_condition.items():
        overall = result.get("overall") or {}
        ci = overall.get("ci_95") or [None, None]
        ci_text = (
            f"[{ci[0]:.3f}, {ci[1]:.3f}]"
            if len(ci) == 2 and all(isinstance(value, (int, float)) for value in ci)
            else str(overall.get("ci_status") or "N/A")
        )
        lines.append(
            f"| {condition} | {result.get('valid_pairs')}/{result.get('attempted_pairs')} | "
            f"{overall.get('full_win')}/{overall.get('tie')}/{overall.get('ablation_win')} | "
            f"{_format_float(overall.get('adjusted_win_rate_for_full'))} | {ci_text} | "
            f"{_format_float(result.get('mean_order_consistency'))} |"
        )
    lines.extend(
        [
            "",
            "## Axis-level results",
            "",
            "| Ablation | Axis | Full W/T/L | Adjusted Win Rate | 95% CI |",
            "| --- | --- | ---: | ---: | --- |",
        ]
    )
    for condition, result in by_condition.items():
        for axis in AXES:
            axis_result = (result.get("axes") or {}).get(axis) or {}
            ci = axis_result.get("ci_95") or [None, None]
            ci_text = (
                f"[{ci[0]:.3f}, {ci[1]:.3f}]"
                if len(ci) == 2 and all(isinstance(value, (int, float)) for value in ci)
                else str(axis_result.get("ci_status") or "N/A")
            )
            lines.append(
                f"| {condition} | {axis} | {axis_result.get('full_win')}/"
                f"{axis_result.get('tie')}/{axis_result.get('ablation_win')} | "
                f"{_format_float(axis_result.get('adjusted_win_rate_for_full'))} | {ci_text} |"
            )
    return "\n".join(lines).rstrip() + "\n"


def _load_or_call_judgment(
    path: Path,
    request_payload: dict[str, Any],
    *,
    force: bool,
    judge_call: Callable[..., dict[str, Any]],
    timeout_seconds: float,
    transport_retries: int,
) -> tuple[dict[str, Any], bool]:
    fingerprint = request_fingerprint(request_payload)
    if not force and path.exists():
        cached = _load_json(path)
        if cached.get("request_fingerprint") == fingerprint and isinstance(
            cached.get("judgment"), dict
        ):
            return cached["judgment"], True
    judgment = judge_call(
        request_payload,
        timeout_seconds=timeout_seconds,
        transport_retries=transport_retries,
    )
    _write_json(
        path,
        {
            "request_fingerprint": fingerprint,
            "judge_model": request_payload.get("model"),
            "completed_at": _utc_now(),
            "judgment": judgment,
        },
    )
    return judgment, False


def _write_request_preview(path: Path, request_payload: dict[str, Any]) -> None:
    measurement = measure_request(
        request_payload,
        model=str(request_payload.get("model") or ""),
    )
    preview = {
        "request_fingerprint": request_fingerprint(request_payload),
        "request_measurement": measurement.as_dict(),
        "model": request_payload.get("model"),
        "messages": request_payload.get("messages"),
        "response_format": request_payload.get("response_format"),
    }
    _write_json(path, preview)


def _require_valid_row(row: dict[str, Any], *, label: str) -> None:
    required_passes = (
        row.get("status") == "success",
        row.get("gate_a") == "pass",
        row.get("gate_b") == "pass",
        row.get("writer_gate") == "pass",
    )
    if not all(required_passes):
        raise ValueError(f"Report pair input did not pass all generation gates: {label}")
    report = Path(str(row.get("report_html") or "")).expanduser().resolve()
    manifest = Path(str(row.get("pipeline_manifest") or "")).expanduser().resolve()
    if not report.exists() or not manifest.exists():
        raise ValueError(f"Report or pipeline manifest is missing: {label}")


def _deduplicate_records(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    unique: dict[tuple[Any, Any, Any], dict[str, Any]] = {}
    for item in records:
        key = (item.get("case_id"), item.get("condition"), item.get("replicate"))
        unique[key] = item
    return list(unique.values())


def _bundle_card_keys(bundle: dict[str, Any]) -> list[str]:
    return sorted(
        {
            str(card.get("card_key"))
            for card in bundle.get("cards") or []
            if isinstance(card, dict) and card.get("card_key")
        }
    )


def _load_candidate_snapshot(
    pair_id: str,
    roots: list[Path],
) -> dict[str, Any] | None:
    """Load the exact Judge-visible reports archived by a prior evaluation."""

    if not roots:
        return None
    matches: list[tuple[Path, Path, Path]] = []
    for root_value in roots:
        root = Path(root_value).expanduser().resolve()
        pair_dir = root / "comparisons" / pair_id
        full_path = pair_dir / "candidate_full_visible.json"
        ablation_path = pair_dir / "candidate_ablation_visible.json"
        if full_path.exists() or ablation_path.exists():
            if not full_path.is_file() or not ablation_path.is_file():
                raise ValueError(f"Incomplete candidate snapshot for {pair_id}: {pair_dir}")
            matches.append((root, full_path, ablation_path))
    if len(matches) != 1:
        raise ValueError(
            f"Expected exactly one candidate snapshot for {pair_id}, found {len(matches)}"
        )
    root, full_path, ablation_path = matches[0]
    full = _load_json(full_path)
    ablation = _load_json(ablation_path)
    return {
        "full": full,
        "ablation": ablation,
        "provenance": {
            "mode": "frozen_judge_visible_snapshot",
            "snapshot_root": str(root),
            "full_path": str(full_path),
            "ablation_path": str(ablation_path),
            "full_sha256": _json_payload_sha256(full),
            "ablation_sha256": _json_payload_sha256(ablation),
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


def _load_json(path: str | Path) -> dict[str, Any]:
    resolved = Path(path).expanduser().resolve()
    payload = json.loads(resolved.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"JSON artifact must be an object: {resolved}")
    return payload


def _write_json(path: Path, payload: Any) -> None:
    _write_text(path, json.dumps(payload, ensure_ascii=False, indent=2) + "\n")


def _write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=str(path.parent))
    try:
        with open(descriptor, "w", encoding="utf-8", closefd=True) as handle:
            handle.write(text)
        Path(temporary).replace(path)
    finally:
        temporary_path = Path(temporary)
        if temporary_path.exists():
            temporary_path.unlink()


def _format_float(value: Any) -> str:
    return f"{float(value):.3f}" if isinstance(value, (int, float)) else "N/A"


def _positive_int(value: str) -> int:
    parsed = int(value)
    if parsed < 1:
        raise argparse.ArgumentTypeError("value must be >= 1")
    return parsed


def _non_negative_int(value: str) -> int:
    parsed = int(value)
    if parsed < 0:
        raise argparse.ArgumentTypeError("value must be >= 0")
    return parsed


def _positive_float(value: str) -> float:
    parsed = float(value)
    if parsed <= 0:
        raise argparse.ArgumentTypeError("value must be > 0")
    return parsed


def _new_evaluation_id() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    summary = run_evaluation(args)
    print(json.dumps(summary["counts"], ensure_ascii=False), flush=True)
    return 1 if summary["status"] == "complete_with_failures" else 0


if __name__ == "__main__":
    raise SystemExit(main())
