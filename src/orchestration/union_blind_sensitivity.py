"""Aggregate the six-company union-blind Judge sensitivity experiment."""

from __future__ import annotations

import argparse
import csv
import json
import random
import tempfile
from collections import defaultdict
from pathlib import Path
from typing import Any

from orchestration.final_report_evaluation_bundle import file_sha256
from orchestration.final_report_evaluation_metrics import AXES, aggregate_pair_results
from orchestration.paths import PROJECT_ROOT


CONDITIONS = ("no_peer", "no_subdata")
CONDITION_LABELS = {"no_peer": "Full vs no-peer", "no_subdata": "Full vs no-subdata"}
AXIS_LABELS = {
    "financial_numeric": "재무·수치",
    "news": "뉴스",
    "company_market_peer": "기업·시장·경쟁사",
    "investment": "투자판단",
    "risk": "리스크",
    "writing": "작성품질",
}
ORIGINAL_IDS = (
    "paper_skbiopharm_20251031_revised_nosy_judge_v3",
    "paper_amorepacific_20251031_revised_nosy_judge_v3",
    "paper_coway_20251031_revised_nosy_judge_v4_revenuefix",
    "paper_hyundai_mobis_20251031_revised_nosy_judge_v3",
    "paper_bgf_retail_20251031_revised_nosy_judge_v3",
    "paper_s_oil_20251031_revised_nosy_judge_v3",
)
DEFAULT_BLIND_ID = "paper_six_company_revised_nosy_judge_union_blind_v1"
DEFAULT_OUTPUT_ID = "paper_six_company_revised_nosy_judge_union_blind_sensitivity_v1"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project-root", type=Path, default=PROJECT_ROOT)
    parser.add_argument("--blind-evaluation-id", default=DEFAULT_BLIND_ID)
    parser.add_argument("--output-dir", type=Path, default=None)
    parser.add_argument("--bootstrap-samples", type=int, default=10_000)
    parser.add_argument("--seed", type=int, default=20260807)
    return parser


def build_sensitivity(args: argparse.Namespace) -> dict[str, Any]:
    root = args.project_root.expanduser().resolve()
    evaluation_root = root / "Output_total" / "Evaluation" / "Final_Report_Ablation"
    original_pairs = [
        pair
        for evaluation_id in ORIGINAL_IDS
        for pair in _successful_pairs(evaluation_root / evaluation_id / "evaluation_summary.json")
    ]
    blind_pairs = _successful_pairs(
        evaluation_root / args.blind_evaluation_id / "evaluation_summary.json"
    )
    alignment = _validate_alignment(original_pairs, blind_pairs)
    original_aggregation = aggregate_pair_results(
        original_pairs, bootstrap_samples=args.bootstrap_samples, seed=args.seed
    )
    blind_aggregation = aggregate_pair_results(
        blind_pairs, bootstrap_samples=args.bootstrap_samples, seed=args.seed
    )
    overall_rows = _overall_rows(
        original_pairs,
        blind_pairs,
        original_aggregation,
        blind_aggregation,
        samples=args.bootstrap_samples,
        seed=args.seed,
    )
    axis_rows = _axis_rows(original_aggregation, blind_aggregation)
    company_rows = _company_rows(original_pairs, blind_pairs)
    output_dir = (
        args.output_dir.expanduser().resolve()
        if args.output_dir is not None
        else evaluation_root / DEFAULT_OUTPUT_ID
    )
    output_dir.mkdir(parents=True, exist_ok=True)
    result = {
        "schema_version": "union_blind_sensitivity_v1",
        "design": {
            "original_evaluation_ids": list(ORIGINAL_IDS),
            "blind_evaluation_id": args.blind_evaluation_id,
            "pair_count": len(blind_pairs),
            "cross_order_judgment_count": len(blind_pairs) * 2,
            "paired_bootstrap_unit": "company",
            "bootstrap_samples": args.bootstrap_samples,
            "seed": args.seed,
            "alignment": alignment,
        },
        "original_aggregation": original_aggregation,
        "union_blind_aggregation": blind_aggregation,
        "overall_table": overall_rows,
        "axis_table": axis_rows,
        "company_table": company_rows,
    }
    _write_json(output_dir / "sensitivity_results.json", result)
    _write_csv(output_dir / "table_union_blind_overall.csv", overall_rows)
    _write_csv(output_dir / "table_union_blind_axes.csv", axis_rows)
    _write_csv(output_dir / "table_union_blind_company.csv", company_rows)
    _write_markdown(output_dir, overall_rows, axis_rows, company_rows)
    return result


def _successful_pairs(path: Path) -> list[dict[str, Any]]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if payload.get("status") != "success":
        raise ValueError(f"Evaluation is not successful: {path}")
    return [
        pair
        for pair in payload.get("pairs") or []
        if isinstance(pair, dict) and pair.get("status") == "success"
    ]


def _validate_alignment(
    original_pairs: list[dict[str, Any]], blind_pairs: list[dict[str, Any]]
) -> dict[str, Any]:
    if len(original_pairs) != 36 or len(blind_pairs) != 36:
        raise ValueError(f"Expected 36 pairs per design, got {len(original_pairs)} and {len(blind_pairs)}")
    original = {str(pair["pair_id"]): pair for pair in original_pairs}
    blind = {str(pair["pair_id"]): pair for pair in blind_pairs}
    if set(original) != set(blind):
        raise ValueError("Original and union-blind pair IDs do not match exactly.")
    changed_html = 0
    for pair_id, left in original.items():
        right = blind[pair_id]
        if right.get("evidence_mode") != "union_blind":
            raise ValueError(f"Pair lacks union-blind provenance: {pair_id}")
        if (right.get("evidence_scope") or {}).get("candidate_access_metadata_sent") is not False:
            raise ValueError(f"Candidate access metadata was sent for {pair_id}")
        for role in ("full", "ablation"):
            filename = f"candidate_{role}_visible.json"
            left_path = Path(str(left["output_dir"])) / filename
            right_path = Path(str(right["output_dir"])) / filename
            if file_sha256(left_path) != file_sha256(right_path):
                raise ValueError(f"Judge-visible report changed for {pair_id}: {role}")
        for key in ("full_strategy_packet", "ablation_strategy_packet"):
            if (left.get("source_hashes") or {}).get(key) != (right.get("source_hashes") or {}).get(key):
                raise ValueError(f"Strategy packet changed for {pair_id}: {key}")
        for key in ("full_report", "ablation_report"):
            if (left.get("source_hashes") or {}).get(key) != (right.get("source_hashes") or {}).get(key):
                changed_html += 1
    return {
        "pair_ids_exactly_aligned": True,
        "judge_visible_reports_exactly_aligned": True,
        "strategy_packets_exactly_aligned": True,
        "candidate_access_metadata_absent": True,
        "source_html_hash_difference_count": changed_html,
    }


def _overall_rows(
    original_pairs: list[dict[str, Any]],
    blind_pairs: list[dict[str, Any]],
    original: dict[str, Any],
    blind: dict[str, Any],
    *,
    samples: int,
    seed: int,
) -> list[dict[str, Any]]:
    rows = []
    for index, condition in enumerate(CONDITIONS):
        left = original["by_condition"][condition]
        right = blind["by_condition"][condition]
        delta, ci = _paired_company_delta(
            original_pairs, blind_pairs, condition=condition, samples=samples, seed=seed + index
        )
        rows.append(
            {
                "condition_id": condition,
                "condition": CONDITION_LABELS[condition],
                **_overall_fields("original", left),
                **_overall_fields("union_blind", right),
                "adjusted_win_rate_delta": delta,
                "delta_ci_95_low": ci[0],
                "delta_ci_95_high": ci[1],
            }
        )
    return rows


def _overall_fields(prefix: str, payload: dict[str, Any]) -> dict[str, Any]:
    overall = payload["overall"]
    ci = overall.get("ci_95") or [None, None]
    return {
        f"{prefix}_full_win": overall["full_win"],
        f"{prefix}_tie": overall["tie"],
        f"{prefix}_ablation_win": overall["ablation_win"],
        f"{prefix}_adjusted_win_rate": overall["adjusted_win_rate_for_full"],
        f"{prefix}_ci_95_low": ci[0],
        f"{prefix}_ci_95_high": ci[1],
        f"{prefix}_order_consistency": payload["mean_order_consistency"],
    }


def _axis_rows(original: dict[str, Any], blind: dict[str, Any]) -> list[dict[str, Any]]:
    rows = []
    for condition in CONDITIONS:
        for axis in AXES:
            left = original["by_condition"][condition]["axes"][axis]
            right = blind["by_condition"][condition]["axes"][axis]
            rows.append(
                {
                    "condition_id": condition,
                    "condition": CONDITION_LABELS[condition],
                    "axis_id": axis,
                    "axis": AXIS_LABELS[axis],
                    "original_full_win": left["full_win"],
                    "original_tie": left["tie"],
                    "original_ablation_win": left["ablation_win"],
                    "original_adjusted_win_rate": left["adjusted_win_rate_for_full"],
                    "union_blind_full_win": right["full_win"],
                    "union_blind_tie": right["tie"],
                    "union_blind_ablation_win": right["ablation_win"],
                    "union_blind_adjusted_win_rate": right["adjusted_win_rate_for_full"],
                    "adjusted_win_rate_delta": right["adjusted_win_rate_for_full"] - left["adjusted_win_rate_for_full"],
                }
            )
    return rows


def _company_rows(
    original_pairs: list[dict[str, Any]], blind_pairs: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    rows = []
    companies = sorted({str(pair["company_name"]) for pair in blind_pairs})
    for company in companies:
        for condition in CONDITIONS:
            left = _score_pairs(original_pairs, company=company, condition=condition)
            right = _score_pairs(blind_pairs, company=company, condition=condition)
            rows.append(
                {
                    "company": company,
                    "condition_id": condition,
                    "condition": CONDITION_LABELS[condition],
                    **{f"original_{key}": value for key, value in left.items()},
                    **{f"union_blind_{key}": value for key, value in right.items()},
                    "adjusted_win_rate_delta": right["adjusted_win_rate"] - left["adjusted_win_rate"],
                }
            )
    return rows


def _score_pairs(
    pairs: list[dict[str, Any]], *, company: str, condition: str
) -> dict[str, Any]:
    selected = [
        pair for pair in pairs
        if pair.get("company_name") == company and pair.get("ablation_condition") == condition
    ]
    scores = [
        float((pair.get("axes") or {}).get(axis, {}).get("score_for_full"))
        for pair in selected
        for axis in AXES
    ]
    return {
        "full_win": sum(score == 1.0 for score in scores),
        "tie": sum(score == 0.5 for score in scores),
        "ablation_win": sum(score == 0.0 for score in scores),
        "adjusted_win_rate": sum(scores) / len(scores),
        "order_consistency": sum(float(pair["order_consistency_rate"]) for pair in selected) / len(selected),
    }


def _paired_company_delta(
    original_pairs: list[dict[str, Any]],
    blind_pairs: list[dict[str, Any]],
    *,
    condition: str,
    samples: int,
    seed: int,
) -> tuple[float, list[float]]:
    original_by_id = {str(pair["pair_id"]): pair for pair in original_pairs}
    blind_by_id = {str(pair["pair_id"]): pair for pair in blind_pairs}
    company_deltas: dict[str, list[float]] = defaultdict(list)
    for pair_id, left in original_by_id.items():
        if left.get("ablation_condition") != condition:
            continue
        right = blind_by_id[pair_id]
        for axis in AXES:
            left_score = float(left["axes"][axis]["score_for_full"])
            right_score = float(right["axes"][axis]["score_for_full"])
            company_deltas[str(left["company_name"])].append(right_score - left_score)
    companies = sorted(company_deltas)
    company_means = {company: sum(values) / len(values) for company, values in company_deltas.items()}
    point = sum(company_means.values()) / len(company_means)
    rng = random.Random(seed)
    draws = []
    for _ in range(samples):
        sampled = [rng.choice(companies) for _ in companies]
        draws.append(sum(company_means[company] for company in sampled) / len(sampled))
    draws.sort()
    return point, [_percentile(draws, 0.025), _percentile(draws, 0.975)]


def _percentile(values: list[float], probability: float) -> float:
    index = probability * (len(values) - 1)
    lower = int(index)
    upper = min(lower + 1, len(values) - 1)
    weight = index - lower
    return values[lower] * (1 - weight) + values[upper] * weight


def _write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def _write_markdown(
    output_dir: Path,
    overall: list[dict[str, Any]],
    axes: list[dict[str, Any]],
    companies: list[dict[str, Any]],
) -> None:
    overall_lines = [
        "| 비교 | 기존 승/무/패 | 기존 조정승률 | Union-blind 승/무/패 | Union-blind 조정승률 | 변화 | 변화 95% CI | 순서일치율 |",
        "| --- | --- | --- | --- | --- | --- | --- | --- |",
    ]
    for row in overall:
        overall_lines.append(
            f"| {row['condition']} | {row['original_full_win']}/{row['original_tie']}/{row['original_ablation_win']} | "
            f"{row['original_adjusted_win_rate']:.1%} | {row['union_blind_full_win']}/{row['union_blind_tie']}/{row['union_blind_ablation_win']} | "
            f"{row['union_blind_adjusted_win_rate']:.1%} | {row['adjusted_win_rate_delta']:+.1%}p | "
            f"[{row['delta_ci_95_low']:+.1%}p, {row['delta_ci_95_high']:+.1%}p] | {row['union_blind_order_consistency']:.1%} |"
        )
    _write_text(output_dir / "table_union_blind_overall.md", "\n".join(overall_lines) + "\n")

    axis_lines = [
        "| 비교 | 평가축 | 기존 조정승률 | Union-blind 승/무/패 | Union-blind 조정승률 | 변화 |",
        "| --- | --- | --- | --- | --- | --- |",
    ]
    for row in axes:
        axis_lines.append(
            f"| {row['condition']} | {row['axis']} | {row['original_adjusted_win_rate']:.1%} | "
            f"{row['union_blind_full_win']}/{row['union_blind_tie']}/{row['union_blind_ablation_win']} | "
            f"{row['union_blind_adjusted_win_rate']:.1%} | {row['adjusted_win_rate_delta']:+.1%}p |"
        )
    _write_text(output_dir / "table_union_blind_axes.md", "\n".join(axis_lines) + "\n")

    company_lines = [
        "| 기업 | 비교 | Union-blind 승/무/패 | 조정승률 | 기존 대비 변화 | 순서일치율 |",
        "| --- | --- | --- | --- | --- | --- |",
    ]
    for row in companies:
        company_lines.append(
            f"| {row['company']} | {row['condition']} | {row['union_blind_full_win']}/{row['union_blind_tie']}/{row['union_blind_ablation_win']} | "
            f"{row['union_blind_adjusted_win_rate']:.1%} | {row['adjusted_win_rate_delta']:+.1%}p | "
            f"{row['union_blind_order_consistency']:.1%} |"
        )
    _write_text(output_dir / "table_union_blind_company.md", "\n".join(company_lines) + "\n")


def _write_json(path: Path, payload: Any) -> None:
    _write_text(path, json.dumps(payload, ensure_ascii=False, indent=2) + "\n")


def _write_text(path: Path, text: str) -> None:
    descriptor, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=str(path.parent))
    try:
        with open(descriptor, "w", encoding="utf-8", closefd=True) as handle:
            handle.write(text)
        Path(temporary).replace(path)
    finally:
        temporary_path = Path(temporary)
        if temporary_path.exists():
            temporary_path.unlink()


def main(argv: list[str] | None = None) -> int:
    result = build_sensitivity(build_parser().parse_args(argv))
    print(json.dumps(result["overall_table"], ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
