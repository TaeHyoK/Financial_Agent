"""Offline aggregation for the six-company revised no-SY ablation study."""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from orchestration.final_report_evaluation_metrics import AXES, aggregate_pair_results
from orchestration.paths import PROJECT_ROOT


BOOTSTRAP_SAMPLES = 10_000
BOOTSTRAP_SEED = 20260731
CONDITIONS = ("full", "no_peer", "no_subdata")
ABLATIONS = ("no_peer", "no_subdata")
LABELS = {
    "full": "Revised Full (no SY)",
    "no_peer": "경쟁사 제외",
    "no_subdata": "Sub data 제외",
}
AXIS_LABELS = {
    "financial_numeric": "재무·수치",
    "news": "뉴스",
    "company_market_peer": "기업·시장·경쟁사",
    "investment": "투자판단",
    "risk": "리스크",
    "writing": "작성품질",
}


COMPANIES = (
    (
        "SK바이오팜",
        "paper_skbiopharm_20251031_revised_nosy_ablation_v1",
        "paper_skbiopharm_20251031_revised_nosy_judge_v1",
    ),
    (
        "삼성전자",
        "paper_samsung_electronics_20251031_revised_nosy_ablation_v1",
        "paper_samsung_electronics_20251031_revised_nosy_judge_v1",
    ),
    (
        "아모레퍼시픽",
        "paper_amorepacific_20251031_revised_nosy_ablation_v1",
        "paper_amorepacific_20251031_revised_nosy_judge_v1",
    ),
    (
        "코웨이",
        "paper_coway_20251031_revised_nosy_ablation_v1",
        "paper_coway_20251031_revised_nosy_judge_v1",
    ),
    (
        "현대모비스",
        "paper_hyundai_mobis_20251031_revised_nosy_ablation_v1",
        "paper_hyundai_mobis_20251031_revised_nosy_judge_v1",
    ),
    (
        "BGF리테일",
        "paper_bgf_retail_20251031_revised_nosy_ablation_v1",
        "paper_bgf_retail_20251031_revised_nosy_judge_v1",
    ),
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project-root", type=Path, default=PROJECT_ROOT)
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=PROJECT_ROOT
        / "Output_total"
        / "Evaluation"
        / "Final_Report_Ablation"
        / "paper_six_company_revised_nosy_aggregate_v1",
    )
    parser.add_argument("--bootstrap-samples", type=int, default=BOOTSTRAP_SAMPLES)
    parser.add_argument("--seed", type=int, default=BOOTSTRAP_SEED)
    return parser


def build_aggregate(args: argparse.Namespace) -> dict[str, Any]:
    project_root = args.project_root.expanduser().resolve()
    output_dir = args.output_dir.expanduser().resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    suite_base = project_root / "Output_total" / "experiments" / "ablations"
    evaluation_base = project_root / "Output_total" / "Evaluation" / "Final_Report_Ablation"
    pair_results: list[dict[str, Any]] = []
    recommendation_records: list[dict[str, Any]] = []
    source_files: list[dict[str, Any]] = []
    company_results: list[dict[str, Any]] = []
    generation_usage = _zero_usage()
    judge_usage = _zero_usage()

    for company, suite_id, evaluation_id in COMPANIES:
        suite_summary_path = suite_base / suite_id / "ablation_summary.json"
        evaluation_summary_path = evaluation_base / evaluation_id / "evaluation_summary.json"
        evaluation_manifest_path = evaluation_base / evaluation_id / "experiment_manifest.json"
        suite = _load_json(suite_summary_path)
        evaluation = _load_json(evaluation_summary_path)
        evaluation_manifest = _load_json(evaluation_manifest_path)
        _validate(company, suite, evaluation, evaluation_manifest)
        runs = [
            row
            for row in suite.get("runs") or []
            if isinstance(row, dict) and row.get("status") == "success"
        ]
        for row in runs:
            recommendation_records.append(
                {
                    "case_id": company,
                    "condition": str(row.get("condition") or ""),
                    "replicate": int(row.get("replicate") or 0),
                    "recommendation": str(row.get("recommendation") or ""),
                }
            )
            if row.get("condition") != "full":
                _add_usage(
                    generation_usage,
                    {
                        "input_tokens": row.get("input_tokens"),
                        "output_tokens": row.get("output_tokens"),
                        "total_tokens": row.get("total_tokens"),
                        "logical_calls": row.get("observed_logical_calls"),
                    },
                )
        pairs = [row for row in evaluation.get("pairs") or [] if isinstance(row, dict)]
        pair_results.extend(pairs)
        evaluation_usage = evaluation.get("llm_usage") if isinstance(evaluation.get("llm_usage"), dict) else {}
        usage_values = evaluation_usage.get("usage") if isinstance(evaluation_usage.get("usage"), dict) else {}
        _add_usage(
            judge_usage,
            {
                "input_tokens": usage_values.get("input_tokens"),
                "cached_input_tokens": usage_values.get("cached_input_tokens"),
                "output_tokens": usage_values.get("output_tokens"),
                "total_tokens": usage_values.get("total_tokens"),
                "logical_calls": evaluation_usage.get("observed_logical_calls"),
            },
        )
        company_results.append(
            {
                "company": company,
                "suite_id": suite_id,
                "evaluation_id": evaluation_id,
                "aggregation": evaluation.get("aggregation") or {},
                "recommendation_analysis": evaluation.get("recommendation_analysis") or {},
            }
        )
        for kind, path in (
            ("ablation_summary", suite_summary_path),
            ("evaluation_summary", evaluation_summary_path),
            ("evaluation_manifest", evaluation_manifest_path),
        ):
            source_files.append(
                {
                    "company": company,
                    "type": kind,
                    "path": str(path.relative_to(project_root)),
                    "sha256": _sha256(path),
                }
            )

    if len(pair_results) != 36:
        raise ValueError(f"Expected 36 successful pairs, got {len(pair_results)}")
    if len(recommendation_records) != 54:
        raise ValueError(f"Expected 54 recommendation records, got {len(recommendation_records)}")
    aggregation = aggregate_pair_results(
        pair_results,
        bootstrap_samples=args.bootstrap_samples,
        seed=args.seed,
    )
    overall_rows = _overall_rows(aggregation)
    axis_rows = _axis_rows(aggregation, pair_results)
    recommendation_rows = _recommendation_rows(recommendation_records)
    result = {
        "schema_version": "revised_nosy_six_company_aggregate_v1",
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "offline_only": True,
        "design": {
            "company_count": len(COMPANIES),
            "conditions": list(CONDITIONS),
            "replicates": 3,
            "report_count": len(recommendation_records),
            "judge_pair_count": len(pair_results),
            "judge_call_count": judge_usage["logical_calls"],
            "axes": list(AXES),
            "judge_model": "gpt-5.4",
            "generation_model": "gpt-5.4-mini",
            "bootstrap_samples": args.bootstrap_samples,
            "bootstrap_seed": args.seed,
            "bootstrap_unit": "company",
        },
        "aggregation": aggregation,
        "overall_table": overall_rows,
        "axis_table": axis_rows,
        "recommendation_table": recommendation_rows,
        "usage": {"generation": generation_usage, "judge": judge_usage},
        "company_results": company_results,
    }
    _write_json(output_dir / "aggregate_results.json", result)
    _write_json(
        output_dir / "aggregate_manifest.json",
        {
            "schema_version": "revised_nosy_six_company_manifest_v1",
            "generated_at_utc": result["generated_at_utc"],
            "offline_only": True,
            "source_files": source_files,
            "output_files": [
                "aggregate_results.json",
                "aggregate_manifest.json",
                "table_2_adjusted_win_rate.csv",
                "table_2_adjusted_win_rate.md",
                "table_3_recommendation_stability.csv",
                "table_3_recommendation_stability.md",
                "table_4_llm_judge_axes.csv",
                "table_4_llm_judge_axes.md",
            ],
        },
    )
    _write_outputs(output_dir, overall_rows, recommendation_rows, axis_rows)
    return result


def _validate(
    company: str,
    suite: dict[str, Any],
    evaluation: dict[str, Any],
    evaluation_manifest: dict[str, Any],
) -> None:
    if suite.get("status") != "success" or evaluation.get("status") != "success":
        raise ValueError(f"Incomplete source for {company}")
    runs = [row for row in suite.get("runs") or [] if isinstance(row, dict) and row.get("status") == "success"]
    run_counts = Counter(str(row.get("condition") or "") for row in runs)
    if run_counts != Counter({condition: 3 for condition in CONDITIONS}):
        raise ValueError(f"Unexpected run counts for {company}: {run_counts}")
    pairs = [row for row in evaluation.get("pairs") or [] if isinstance(row, dict) and row.get("status") == "success"]
    pair_counts = Counter(str(row.get("ablation_condition") or "") for row in pairs)
    if pair_counts != Counter({condition: 3 for condition in ABLATIONS}):
        raise ValueError(f"Unexpected pair counts for {company}: {pair_counts}")
    request = evaluation_manifest.get("request") if isinstance(evaluation_manifest.get("request"), dict) else {}
    if request.get("judge_model") != "gpt-5.4" or not request.get("cross_order"):
        raise ValueError(f"Unexpected Judge design for {company}")


def _overall_rows(aggregation: dict[str, Any]) -> list[dict[str, Any]]:
    rows = []
    for condition in ABLATIONS:
        data = aggregation["by_condition"][condition]
        overall = data["overall"]
        rows.append(
            {
                "condition_id": condition,
                "condition": LABELS[condition],
                "report_pairs": data["valid_pairs"],
                **overall,
                "ci_95_low": overall["ci_95"][0],
                "ci_95_high": overall["ci_95"][1],
                "mean_order_consistency": data["mean_order_consistency"],
            }
        )
    return rows


def _axis_rows(aggregation: dict[str, Any], pairs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    rows = []
    for condition in ABLATIONS:
        for axis in AXES:
            data = aggregation["by_condition"][condition]["axes"][axis]
            selected = [
                (pair.get("axes") or {}).get(axis) or {}
                for pair in pairs
                if pair.get("status") == "success" and pair.get("ablation_condition") == condition
            ]
            consistent = sum(bool(item.get("order_consistent")) for item in selected)
            rows.append(
                {
                    "condition_id": condition,
                    "condition": LABELS[condition],
                    "axis_id": axis,
                    "axis": AXIS_LABELS[axis],
                    **data,
                    "ci_95_low": data["ci_95"][0],
                    "ci_95_high": data["ci_95"][1],
                    "order_consistent_count": consistent,
                    "order_evaluations": len(selected),
                    "order_consistency_rate": consistent / len(selected) if selected else None,
                }
            )
    return rows


def _recommendation_rows(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    baseline = {
        (row["case_id"], row["replicate"]): row["recommendation"]
        for row in records
        if row["condition"] == "full"
    }
    rows = []
    for condition in CONDITIONS:
        selected = [row for row in records if row["condition"] == condition]
        distribution = Counter(row["recommendation"] for row in selected)
        groups: dict[str, list[str]] = defaultdict(list)
        for row in selected:
            groups[row["case_id"]].append(row["recommendation"])
        repeat_groups = [values for values in groups.values() if len(values) == 3]
        unanimous = sum(len(set(values)) == 1 for values in repeat_groups)
        pairs = [
            (baseline[(row["case_id"], row["replicate"])], row["recommendation"])
            for row in selected
            if condition != "full" and (row["case_id"], row["replicate"]) in baseline
        ]
        directions = Counter(f"{left}->{right}" for left, right in pairs if left != right)
        rows.append(
            {
                "condition_id": condition,
                "condition": LABELS[condition],
                "report_count": len(selected),
                "buy": distribution["Buy"],
                "hold": distribution["Hold"],
                "sell": distribution["Sell"],
                "flip_count_vs_full": sum(left != right for left, right in pairs) if condition != "full" else None,
                "flip_rate_vs_full": (sum(left != right for left, right in pairs) / len(pairs)) if pairs else None,
                "flip_directions": dict(sorted(directions.items())),
                "repeat_case_count": len(repeat_groups),
                "unanimous_case_count": unanimous,
                "unanimous_repeat_rate": unanimous / len(repeat_groups) if repeat_groups else None,
                "mean_majority_agreement": (
                    sum(max(Counter(values).values()) / len(values) for values in repeat_groups)
                    / len(repeat_groups)
                    if repeat_groups
                    else None
                ),
            }
        )
    return rows


def _write_outputs(
    output_dir: Path,
    overall: list[dict[str, Any]],
    recommendations: list[dict[str, Any]],
    axes: list[dict[str, Any]],
) -> None:
    _write_csv(output_dir / "table_2_adjusted_win_rate.csv", overall)
    _write_csv(output_dir / "table_3_recommendation_stability.csv", recommendations)
    _write_csv(output_dir / "table_4_llm_judge_axes.csv", axes)
    _write_text(
        output_dir / "table_2_adjusted_win_rate.md",
        _markdown(
            ["Ablation", "보고서 쌍", "Full 승/무/패", "조정 승률", "95% CI", "순서 일치율"],
            [
                [
                    row["condition"],
                    row["report_pairs"],
                    f"{row['full_win']}/{row['tie']}/{row['ablation_win']}",
                    _pct(row["adjusted_win_rate_for_full"]),
                    _ci(row["ci_95"]),
                    _pct(row["mean_order_consistency"]),
                ]
                for row in overall
            ],
        )
        + "\n주: 6개 기업을 동일 가중한 기업-clustered bootstrap 95% CI(10,000회)이다.\n",
    )
    _write_text(
        output_dir / "table_3_recommendation_stability.md",
        _markdown(
            ["조건", "보고서 n", "추천(B/H/S)", "변경 수", "변경률", "3회 완전일치 기업", "반복 안정성"],
            [
                [
                    row["condition"],
                    row["report_count"],
                    f"{row['buy']}/{row['hold']}/{row['sell']}",
                    "—" if row["flip_count_vs_full"] is None else row["flip_count_vs_full"],
                    _pct(row["flip_rate_vs_full"]),
                    f"{row['unanimous_case_count']}/{row['repeat_case_count']}",
                    _pct(row["unanimous_repeat_rate"]),
                ]
                for row in recommendations
            ],
        ),
    )
    _write_text(
        output_dir / "table_4_llm_judge_axes.md",
        _markdown(
            ["Ablation", "평가축", "Full 승/무/패", "조정 승률", "95% CI", "순서 일치"],
            [
                [
                    row["condition"],
                    row["axis"],
                    f"{row['full_win']}/{row['tie']}/{row['ablation_win']}",
                    _pct(row["adjusted_win_rate_for_full"]),
                    _ci(row["ci_95"]),
                    f"{row['order_consistent_count']}/{row['order_evaluations']} ({_pct(row['order_consistency_rate'])})",
                ]
                for row in axes
            ],
        ),
    )


def _zero_usage() -> dict[str, int]:
    return {
        "logical_calls": 0,
        "input_tokens": 0,
        "cached_input_tokens": 0,
        "output_tokens": 0,
        "total_tokens": 0,
    }


def _add_usage(target: dict[str, int], values: dict[str, Any]) -> None:
    for key in target:
        target[key] += int(values.get(key) or 0)


def _write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    fields = list(rows[0]) if rows else []
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def _markdown(headers: list[str], rows: list[list[Any]]) -> str:
    lines = [
        "| " + " | ".join(headers) + " |",
        "| " + " | ".join("---" for _ in headers) + " |",
    ]
    lines.extend("| " + " | ".join(str(value) for value in row) + " |" for row in rows)
    return "\n".join(lines) + "\n"


def _pct(value: float | None) -> str:
    return "—" if value is None else f"{100 * value:.1f}%"


def _ci(value: list[float | None]) -> str:
    return f"[{_pct(value[0])}, {_pct(value[1])}]" if all(item is not None for item in value) else "—"


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _load_json(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"Expected a JSON object: {path}")
    return payload


def _write_json(path: Path, payload: Any) -> None:
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _write_text(path: Path, value: str) -> None:
    path.write_text(value, encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    result = build_aggregate(args)
    print(json.dumps(result["aggregation"]["by_condition"], ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
