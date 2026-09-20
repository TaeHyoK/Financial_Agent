"""End-to-end evaluator for Full versus four ablation conditions."""

from __future__ import annotations

import argparse
import csv
import json
import os
import sys
from pathlib import Path
from typing import Any, Callable

from ablation_suite.config import (
    ABLATION_ROOT,
    DEFAULT_ENV_FILE,
    DEFAULT_SELECTED_DATE,
    DEFAULT_SOURCE_ROOTS,
    FINAL_SRC,
)
from ablation_suite.utils import SuiteLock, safe_label, utc_now, write_json
from ablation_suite.recommendations import explicit_rating_comparison, read_explicit_rating

from .bundle import (
    build_evidence_bundle,
    build_union_evidence_bundle,
    extract_visible_report,
    file_sha256,
    payload_sha256,
)
from .inputs import (
    DEFAULT_ABLATIONS,
    PairSpec,
    aggregate_generation_efficiency,
    discover_evaluation_inputs,
)
from .judge import (
    DEFAULT_JUDGE_MODEL,
    DEFAULT_PROMPT_PATH,
    build_request,
    call_judge,
    request_fingerprint,
)
from .metrics import AXES, aggregate_pair_results, reconcile_cross_order


if str(FINAL_SRC) not in sys.path:
    sys.path.insert(0, str(FINAL_SRC))

from shared.llm_clients import measure_request  # noqa: E402


DEFAULT_ABLATION_SUITE = ABLATION_ROOT / "experiments" / "ablation_20251031"
DEFAULT_FULL_SUITE = ABLATION_ROOT / "experiments" / "full_replicates_20251031"
DEFAULT_OUTPUT_ROOT = ABLATION_ROOT / "evaluations"
DEFAULT_EVALUATION_ID = "ablation_20251031_union_blind"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Blind AB/BA LLM Judge for Full versus the four agreed ablations."
    )
    parser.add_argument("--ablation-suite", type=Path, default=DEFAULT_ABLATION_SUITE)
    parser.add_argument("--full-suite", type=Path, default=DEFAULT_FULL_SUITE)
    parser.add_argument(
        "--full-r1-root",
        action="append",
        type=Path,
        default=None,
        help="Frozen Full r01 Output_total root; repeat for fallbacks.",
    )
    parser.add_argument(
        "--conditions",
        default=",".join(DEFAULT_ABLATIONS),
        help="Comma-separated ablation conditions.",
    )
    parser.add_argument("--selected-date", default=DEFAULT_SELECTED_DATE,
                        help="YYYYMMDD, or 'report' for company dates and newly generated Full replicates.")
    parser.add_argument("--judge-model", default=DEFAULT_JUDGE_MODEL)
    parser.add_argument("--prompt-path", type=Path, default=DEFAULT_PROMPT_PATH)
    parser.add_argument("--env-file", type=Path, default=DEFAULT_ENV_FILE)
    parser.add_argument("--output-root", type=Path, default=DEFAULT_OUTPUT_ROOT)
    parser.add_argument("--evaluation-id", default=DEFAULT_EVALUATION_ID)
    parser.add_argument("--timeout-seconds", type=_positive_float, default=300.0)
    parser.add_argument("--transport-retries", type=_non_negative_int, default=1)
    parser.add_argument("--bootstrap-samples", type=_positive_int, default=10_000)
    parser.add_argument("--bootstrap-seed", type=int, default=20251031)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--fail-fast", action="store_true")
    return parser


def normalize_args(args: argparse.Namespace) -> argparse.Namespace:
    requested = tuple(
        dict.fromkeys(item.strip() for item in str(args.conditions).split(",") if item.strip())
    )
    unknown = sorted(set(requested) - set(DEFAULT_ABLATIONS))
    if unknown or not requested:
        raise ValueError(
            f"Conditions must be a non-empty subset of {list(DEFAULT_ABLATIONS)}; unknown={unknown}"
        )
    args.conditions = requested
    args.full_r1_root = args.full_r1_root or list(DEFAULT_SOURCE_ROOTS)
    for name in ("ablation_suite", "full_suite", "prompt_path", "env_file", "output_root"):
        setattr(args, name, Path(getattr(args, name)).expanduser().resolve())
    args.full_r1_root = [Path(path).expanduser().resolve() for path in args.full_r1_root]
    args.evaluation_id = safe_label(args.evaluation_id)
    return args


def run_evaluation(
    args: argparse.Namespace,
    *,
    judge_call: Callable[..., dict[str, Any]] = call_judge,
) -> dict[str, Any]:
    """Validate the entire matrix first, then evaluate every pair in both orders."""

    pairs, artifacts = discover_evaluation_inputs(
        ablation_suite=args.ablation_suite,
        full_suite=args.full_suite,
        full_r1_roots=args.full_r1_root,
        selected_date=args.selected_date,
        ablations=args.conditions,
    )
    expected_pairs = 6 * 3 * len(args.conditions)
    if len(pairs) != expected_pairs:
        raise AssertionError(f"Expected {expected_pairs} report pairs, found {len(pairs)}")
    if args.selected_date == "report":
        for artifact in artifacts:
            if read_explicit_rating(artifact.report)["label"] == "unclear":
                raise ValueError(f"Annual evaluation requires an explicit Buy/Hold/Sell rating: {artifact.report}")
    env_status = _load_env(args.env_file)
    if not args.dry_run and not os.getenv("OPENAI_API_KEY", "").strip():
        raise RuntimeError("OPENAI_API_KEY is required unless --dry-run is used.")

    output_dir = args.output_root / args.evaluation_id
    output_dir.mkdir(parents=True, exist_ok=True)
    with SuiteLock(output_dir / ".evaluation.lock"):
        return _run_locked(
            args,
            pairs=pairs,
            artifacts=artifacts,
            output_dir=output_dir,
            env_status=env_status,
            judge_call=judge_call,
        )


def _run_locked(
    args: argparse.Namespace,
    *,
    pairs: list[PairSpec],
    artifacts: list[Any],
    output_dir: Path,
    env_status: dict[str, Any],
    judge_call: Callable[..., dict[str, Any]],
) -> dict[str, Any]:
    usage_manifest = output_dir / "llm_usage_manifest.jsonl"
    manifest_path = output_dir / "evaluation_manifest.json"
    summary_path = output_dir / "evaluation_summary.json"
    os.environ["LLM_USAGE_MANIFEST"] = str(usage_manifest)
    os.environ["LLM_EXECUTION_ID"] = args.evaluation_id
    os.environ["LLM_RUN_ROLE"] = "evaluation"
    manifest: dict[str, Any] = {
        "evaluation_id": args.evaluation_id,
        "status": "running",
        "created_at": utc_now(),
        "protocol": {
            "candidate_identity_blind": True,
            "evidence_mode": "union_blind",
            "cross_order": ["AB", "BA"],
            "order_disagreement_policy": "tie",
            "axes": list(AXES),
            "conditions": list(args.conditions),
            "companies": 6,
            "replicates": 3,
            "planned_pairs": len(pairs),
            "expected_judge_calls": len(pairs) * 2,
            "bootstrap_unit": "company",
            "bootstrap_samples": args.bootstrap_samples,
            "bootstrap_seed": args.bootstrap_seed,
            "recommendation_dimensions": ["rating"] if args.selected_date == "report" else ["existing_position", "new_entry", "rating_if_present"],
            "annual_recommendation_source": "explicit_html_metadata_not_judge_inference",
        },
        "inputs": {
            "ablation_suite": str(args.ablation_suite),
            "full_suite": str(args.full_suite),
            "full_r1_roots": [str(path) for path in args.full_r1_root],
            "selected_date": args.selected_date,
        },
        "judge": {
            "model": args.judge_model,
            "prompt_path": str(args.prompt_path),
            "prompt_sha256": file_sha256(args.prompt_path),
            "dry_run": bool(args.dry_run),
        },
        "environment": env_status,
        "pairs": [],
    }
    write_json(manifest_path, manifest)

    results: list[dict[str, Any]] = []
    for index, pair in enumerate(pairs, start=1):
        print(f"[JUDGE {index:02d}/{len(pairs)}] START {pair.pair_id}", flush=True)
        result = evaluate_pair(
            pair,
            output_dir=output_dir,
            model=args.judge_model,
            prompt_path=args.prompt_path,
            timeout_seconds=args.timeout_seconds,
            transport_retries=args.transport_retries,
            dry_run=args.dry_run,
            force=args.force,
            judge_call=judge_call,
        )
        results.append(result)
        manifest["pairs"] = results
        write_json(manifest_path, manifest)
        print(f"[JUDGE {index:02d}/{len(pairs)}] END status={result['status']}", flush=True)
        if result["status"] == "failed" and args.fail_fast:
            break

    aggregation = aggregate_pair_results(
        results,
        bootstrap_samples=args.bootstrap_samples,
        seed=args.bootstrap_seed,
    )
    generation_efficiency = aggregate_generation_efficiency(artifacts)
    judge_usage = summarize_judge_usage(usage_manifest, execution_id=args.evaluation_id)
    failed = sum(item.get("status") == "failed" for item in results)
    successful = sum(item.get("status") == "success" for item in results)
    dry_runs = sum(item.get("status") == "dry_run" for item in results)
    if dry_runs == len(results):
        status = "dry_run"
    elif failed:
        status = "complete_with_failures"
    elif successful == len(pairs):
        status = "success"
    else:
        status = "incomplete"
    summary = {
        "evaluation_id": args.evaluation_id,
        "status": status,
        "counts": {
            "planned_pairs": len(pairs),
            "completed_pairs": len(results),
            "successful_pairs": successful,
            "failed_pairs": failed,
            "dry_run_pairs": dry_runs,
            "planned_judge_calls": len(pairs) * 2,
        },
        "aggregation": aggregation,
        "generation_efficiency": generation_efficiency,
        "judge_usage": judge_usage,
        "pairs": results,
    }
    manifest["status"] = status
    manifest["completed_at"] = utc_now()
    manifest["judge_usage"] = judge_usage
    write_json(manifest_path, manifest)
    write_json(summary_path, summary)
    (output_dir / "evaluation_summary.md").write_text(
        render_summary_markdown(summary), encoding="utf-8"
    )
    write_csv_outputs(output_dir, summary)
    return summary


def evaluate_pair(
    pair: PairSpec,
    *,
    output_dir: Path,
    model: str,
    prompt_path: Path,
    timeout_seconds: float,
    transport_retries: int,
    dry_run: bool,
    force: bool,
    judge_call: Callable[..., dict[str, Any]],
) -> dict[str, Any]:
    pair_dir = output_dir / "comparisons" / pair.pair_id
    judgments_dir = pair_dir / "judgments"
    judgments_dir.mkdir(parents=True, exist_ok=True)
    full_visible = extract_visible_report(pair.full.report)
    ablation_visible = extract_visible_report(pair.ablation.report)
    full_evidence = build_evidence_bundle(pair.full.strategy_packet)
    ablation_evidence = build_evidence_bundle(pair.ablation.strategy_packet)
    evidence = build_union_evidence_bundle(
        [pair.full.strategy_packet, pair.ablation.strategy_packet]
    )
    card_keys = sorted(
        str(card.get("card_key"))
        for card in evidence.get("cards") or []
        if isinstance(card, dict) and card.get("card_key")
    )
    write_json(pair_dir / "common_evidence_bundle.json", evidence)
    write_json(pair_dir / "candidate_full_visible.json", full_visible)
    write_json(pair_dir / "candidate_ablation_visible.json", ablation_visible)
    write_json(
        pair_dir / "identity_map.json",
        {
            "hidden_from_judge": True,
            "order_ab": {"A": "full", "B": pair.ablation_condition},
            "order_ba": {"A": pair.ablation_condition, "B": "full"},
            "source_reports": {
                "full": str(pair.full.report),
                "ablation": str(pair.ablation.report),
            },
        },
    )
    base = {
        "pair_id": pair.pair_id,
        "company_key": pair.company_key,
        "company_name": pair.company_name,
        "replicate": pair.replicate,
        "ablation_condition": pair.ablation_condition,
        "evidence_mode": "union_blind",
        "evidence_scope": {
            "full_card_count": len(full_evidence.get("cards") or []),
            "ablation_card_count": len(ablation_evidence.get("cards") or []),
            "judge_union_card_count": len(card_keys),
            "candidate_access_metadata_sent": False,
        },
        "source_hashes": {
            "full_report": file_sha256(pair.full.report),
            "ablation_report": file_sha256(pair.ablation.report),
            "full_visible": payload_sha256(full_visible),
            "ablation_visible": payload_sha256(ablation_visible),
            "full_strategy_packet": file_sha256(pair.full.strategy_packet),
            "ablation_strategy_packet": file_sha256(pair.ablation.strategy_packet),
            "union_evidence": evidence["bundle_sha256"],
        },
        "output_dir": str(pair_dir),
    }
    try:
        request_ab = build_request(
            candidate_a=full_visible,
            candidate_b=ablation_visible,
            evidence_bundle=evidence,
            model=model,
            prompt_path=prompt_path,
        )
        request_ba = build_request(
            candidate_a=ablation_visible,
            candidate_b=full_visible,
            evidence_bundle=evidence,
            model=model,
            prompt_path=prompt_path,
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

        os.environ["LLM_COMPANY_NAME"] = pair.company_name
        os.environ["LLM_RUN_ID"] = f"{pair.pair_id}:order_ab"
        order_ab, cached_ab = _load_or_call(
            judgments_dir / "order_ab.json",
            request_ab,
            force=force,
            judge_call=judge_call,
            timeout_seconds=timeout_seconds,
            transport_retries=transport_retries,
        )
        os.environ["LLM_RUN_ID"] = f"{pair.pair_id}:order_ba"
        order_ba, cached_ba = _load_or_call(
            judgments_dir / "order_ba.json",
            request_ba,
            force=force,
            judge_call=judge_call,
            timeout_seconds=timeout_seconds,
            transport_retries=transport_retries,
        )
        reconciled = reconcile_cross_order(
            order_ab,
            order_ba,
            allowed_card_keys=card_keys,
        )
        rating = explicit_rating_comparison(pair.full.report, pair.ablation.report)
        if rating:
            # The report already declares its rating; do not ask the Judge to
            # reinterpret it as legacy new-entry/existing-holder instructions.
            reconciled["recommendations"] = {"rating": rating}
        result = {**base, **reconciled, "cache": {"order_ab": cached_ab, "order_ba": cached_ba}}
        write_json(pair_dir / "pairwise_result.json", result)
        return result
    except Exception as exc:
        result = {
            **base,
            "status": "failed",
            "error": {"type": type(exc).__name__, "message": str(exc)},
        }
        write_json(pair_dir / "pairwise_result.json", result)
        return result


def _load_or_call(
    path: Path,
    request: dict[str, Any],
    *,
    force: bool,
    judge_call: Callable[..., dict[str, Any]],
    timeout_seconds: float,
    transport_retries: int,
) -> tuple[dict[str, Any], bool]:
    fingerprint = request_fingerprint(request)
    if not force and path.is_file():
        cached = _load_object(path)
        if cached.get("request_fingerprint") == fingerprint and isinstance(
            cached.get("judgment"), dict
        ):
            return cached["judgment"], True
    judgment = judge_call(
        request,
        timeout_seconds=timeout_seconds,
        transport_retries=transport_retries,
    )
    write_json(
        path,
        {
            "request_fingerprint": fingerprint,
            "judge_model": request.get("model"),
            "completed_at": utc_now(),
            "judgment": judgment,
        },
    )
    return judgment, False


def _write_request_preview(path: Path, request: dict[str, Any]) -> None:
    measurement = measure_request(request, model=str(request.get("model") or ""))
    write_json(
        path,
        {
            "request_fingerprint": request_fingerprint(request),
            "request_measurement": measurement.as_dict(),
            "model": request.get("model"),
            "messages": request.get("messages"),
            "response_format": request.get("response_format"),
        },
    )


def summarize_judge_usage(path: Path, *, execution_id: str) -> dict[str, Any]:
    rows: list[dict[str, Any]] = []
    if path.is_file():
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
    return {
        "source": str(path),
        "observed_logical_calls": len(logical),
        "transport_attempts": len(rows),
        "error_attempts": len(rows) - len(successful),
        "usage": {
            key: sum(int((item.get("usage") or {}).get(key) or 0) for item in rows)
            for key in (
                "input_tokens",
                "cached_input_tokens",
                "output_tokens",
                "reasoning_tokens",
                "total_tokens",
            )
        },
    }


def render_summary_markdown(summary: dict[str, Any]) -> str:
    counts = summary.get("counts") or {}
    lines = [
        "# Ablation union-blind LLM Judge",
        "",
        f"- Status: `{summary.get('status')}`",
        f"- Valid pairs: `{counts.get('successful_pairs', 0)}/{counts.get('planned_pairs', 0)}`",
        f"- Planned cross-order calls: `{counts.get('planned_judge_calls', 0)}`",
        "",
        "| Ablation | Valid | Full W/T/L | Adjusted full win rate | 95% company-bootstrap CI | Order consistency |",
        "| --- | ---: | ---: | ---: | --- | ---: |",
    ]
    by_condition = ((summary.get("aggregation") or {}).get("by_condition") or {})
    for condition, result in by_condition.items():
        overall = result.get("overall") or {}
        lines.append(
            f"| {condition} | {result.get('valid_pairs')}/{result.get('attempted_pairs')} | "
            f"{overall.get('full_win')}/{overall.get('tie')}/{overall.get('ablation_win')} | "
            f"{_format_float(overall.get('adjusted_win_rate_for_full'))} | "
            f"{_format_ci(overall.get('ci_95'))} | "
            f"{_format_float(result.get('mean_order_consistency'))} |"
        )
    lines.extend(
        [
            "",
            "## Axis results",
            "",
            "| Ablation | Axis | Full W/T/L | Adjusted full win rate | 95% CI |",
            "| --- | --- | ---: | ---: | --- |",
        ]
    )
    for condition, result in by_condition.items():
        for axis in AXES:
            row = (result.get("axes") or {}).get(axis) or {}
            lines.append(
                f"| {condition} | {axis} | {row.get('full_win')}/{row.get('tie')}/"
                f"{row.get('ablation_win')} | {_format_float(row.get('adjusted_win_rate_for_full'))} | "
                f"{_format_ci(row.get('ci_95'))} |"
            )
    lines.extend(
        [
            "",
            "The recommendation flip/stability and generation efficiency tables are retained in `evaluation_summary.json`.",
        ]
    )
    return "\n".join(lines).rstrip() + "\n"


def write_csv_outputs(output_dir: Path, summary: dict[str, Any]) -> None:
    """Write analysis-ready flat tables alongside the lossless JSON summary."""

    pair_rows: list[dict[str, Any]] = []
    for pair in summary.get("pairs") or []:
        recommendations = pair.get("recommendations") or {}
        for axis in AXES:
            item = (pair.get("axes") or {}).get(axis) or {}
            pair_rows.append(
                {
                    "company_name": pair.get("company_name"),
                    "replicate": pair.get("replicate"),
                    "ablation_condition": pair.get("ablation_condition"),
                    "axis": axis,
                    "status": pair.get("status"),
                    "outcome": item.get("outcome"),
                    "score_for_full": item.get("score_for_full"),
                    "order_consistent": item.get("order_consistent"),
                    "full_existing_position": (
                        recommendations.get("existing_position") or {}
                    ).get("full_label"),
                    "ablation_existing_position": (
                        recommendations.get("existing_position") or {}
                    ).get("ablation_label"),
                    "existing_position_flip": (
                        recommendations.get("existing_position") or {}
                    ).get("flip"),
                    "full_new_entry": (recommendations.get("new_entry") or {}).get(
                        "full_label"
                    ),
                    "ablation_new_entry": (recommendations.get("new_entry") or {}).get(
                        "ablation_label"
                    ),
                    "new_entry_flip": (recommendations.get("new_entry") or {}).get("flip"),
                    "full_rating": (recommendations.get("rating") or {}).get("full_label"),
                    "ablation_rating": (recommendations.get("rating") or {}).get("ablation_label"),
                    "rating_flip": (recommendations.get("rating") or {}).get("flip"),
                }
            )
    _write_csv(output_dir / "pair_axis_results.csv", pair_rows)

    condition_rows: list[dict[str, Any]] = []
    by_condition = ((summary.get("aggregation") or {}).get("by_condition") or {})
    for condition, result in by_condition.items():
        for axis in ("overall", *AXES):
            item = result.get("overall") if axis == "overall" else (result.get("axes") or {}).get(axis)
            item = item or {}
            ci = item.get("ci_95") or [None, None]
            condition_rows.append(
                {
                    "ablation_condition": condition,
                    "axis": axis,
                    "valid_pairs": result.get("valid_pairs"),
                    "n": item.get("n"),
                    "company_clusters": item.get("company_clusters"),
                    "full_win": item.get("full_win"),
                    "tie": item.get("tie"),
                    "ablation_win": item.get("ablation_win"),
                    "adjusted_win_rate_for_full": item.get("adjusted_win_rate_for_full"),
                    "ci_95_low": ci[0] if len(ci) == 2 else None,
                    "ci_95_high": ci[1] if len(ci) == 2 else None,
                    "order_consistency": result.get("mean_order_consistency"),
                }
            )
    _write_csv(output_dir / "condition_axis_summary.csv", condition_rows)

    efficiency_rows = [
        {"condition": condition, **values}
        for condition, values in (
            (summary.get("generation_efficiency") or {}).get("by_condition") or {}
        ).items()
    ]
    _write_csv(output_dir / "generation_efficiency.csv", efficiency_rows)


def _write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fieldnames = list(rows[0]) if rows else []
    with path.open("w", encoding="utf-8", newline="") as handle:
        if fieldnames:
            writer = csv.DictWriter(handle, fieldnames=fieldnames)
            writer.writeheader()
            writer.writerows(rows)


def _load_env(path: Path) -> dict[str, Any]:
    loaded = False
    if path.is_file():
        try:
            from dotenv import load_dotenv

            loaded = bool(load_dotenv(path, override=False))
        except ImportError:
            for raw in path.read_text(encoding="utf-8").splitlines():
                line = raw.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                key, value = line.removeprefix("export ").split("=", 1)
                os.environ.setdefault(key.strip(), value.strip().strip("'\""))
            loaded = True
    return {
        "env_file": str(path),
        "env_file_exists": path.is_file(),
        "loaded": loaded,
        "openai_api_key_present": bool(os.getenv("OPENAI_API_KEY", "").strip()),
    }


def _load_object(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"Expected JSON object: {path}")
    return payload


def _format_float(value: Any) -> str:
    return f"{float(value):.3f}" if isinstance(value, (int, float)) else "N/A"


def _format_ci(value: Any) -> str:
    if isinstance(value, list) and len(value) == 2 and all(
        isinstance(item, (int, float)) for item in value
    ):
        return f"[{value[0]:.3f}, {value[1]:.3f}]"
    return "N/A"


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


def main(argv: list[str] | None = None) -> int:
    args = normalize_args(build_parser().parse_args(argv))
    summary = run_evaluation(args)
    print(json.dumps(summary["counts"], ensure_ascii=False), flush=True)
    return 1 if summary["status"] in {"complete_with_failures", "incomplete"} else 0


if __name__ == "__main__":
    raise SystemExit(main())
