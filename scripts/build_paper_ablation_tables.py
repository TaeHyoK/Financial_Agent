#!/usr/bin/env python3
"""Build paper-ready tables from the locked six-company ablation outputs.

This command is deliberately offline: it only reads existing experiment and
Judge artifacts and never calls an external API.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

from orchestration.final_report_evaluation_metrics import AXES, aggregate_pair_results


DEFAULT_OUTPUT = (
    "Output_total/Evaluation/Final_Report_Ablation/"
    "paper_six_company_aggregate_v1"
)
EXPECTED_CONDITIONS = ("full", "no_sy", "no_competitor", "primary_only")
ABLATION_CONDITIONS = ("no_sy", "no_competitor", "primary_only")
BOOTSTRAP_SAMPLES = 10_000
BOOTSTRAP_SEED = 20260730

CONDITION_LABELS = {
    "full": "Full",
    "no_sy": "SY 제외",
    "no_competitor": "경쟁사 제외",
    "primary_only": "Sub data 제외",
}
AXIS_LABELS = {
    "financial_numeric": "재무·수치",
    "news": "뉴스",
    "company_market_peer": "기업·시장·경쟁사",
    "investment": "투자판단",
    "risk": "리스크",
    "writing": "작성품질",
}


@dataclass(frozen=True)
class CompanySpec:
    company: str
    industry: str
    peer: str
    suite: str
    evaluation: str
    news_cap: int | None


COMPANIES = (
    CompanySpec(
        company="SK바이오팜",
        industry="제약·바이오",
        peer="일성아이에스",
        suite="paper_skbiopharm_20251031_news40_ablation_v1",
        evaluation="paper_skbiopharm_20251031_news40_judge_v1",
        news_cap=40,
    ),
    CompanySpec(
        company="삼성전자",
        industry="반도체·전자",
        peer="SK하이닉스",
        suite="paper_samsung_electronics_20251031_ablation_v1",
        evaluation="paper_samsung_electronics_20251031_judge_v2",
        news_cap=None,
    ),
    CompanySpec(
        company="아모레퍼시픽",
        industry="화장품·생활소비재",
        peer="LG생활건강",
        suite="paper_amorepacific_20251031_ablation_v1",
        evaluation="paper_amorepacific_20251031_judge_v1",
        news_cap=40,
    ),
    CompanySpec(
        company="코웨이",
        industry="생활가전·렌탈",
        peer="쿠쿠홈시스",
        suite="paper_coway_20251031_ablation_v1",
        evaluation="paper_coway_20251031_judge_v1",
        news_cap=40,
    ),
    CompanySpec(
        company="현대모비스",
        industry="자동차부품",
        peer="HL만도",
        suite="paper_hyundai_mobis_20251031_ablation_v1",
        evaluation="paper_hyundai_mobis_20251031_judge_v2",
        news_cap=40,
    ),
    CompanySpec(
        company="BGF리테일",
        industry="편의점·유통",
        peer="GS리테일",
        suite="paper_bgf_retail_20251031_ablation_v1",
        evaluation="paper_bgf_retail_20251031_judge_v1",
        news_cap=40,
    ),
)


def _read_json(path: Path) -> dict[str, Any]:
    with path.open(encoding="utf-8") as handle:
        payload = json.load(handle)
    if not isinstance(payload, dict):
        raise ValueError(f"Expected a JSON object: {path}")
    return payload


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _find_news_context(suite_root: Path, company: str) -> Path:
    pack_root = (
        suite_root
        / "conditions"
        / "full"
        / "replicate_01"
        / "News"
        / "artifacts"
        / "reports"
        / "packs"
    )
    matches = sorted(pack_root.glob(f"{company}_*/report_context.json"))
    if len(matches) != 1:
        raise ValueError(
            f"Expected exactly one frozen Full news context for {company}, got {matches}"
        )
    return matches[0]


def _validate_suite(
    spec: CompanySpec,
    summary: dict[str, Any],
    evaluation: dict[str, Any],
    manifest: dict[str, Any],
) -> None:
    if summary.get("status") != "success":
        raise ValueError(f"Ablation suite is not successful: {spec.suite}")
    runs = [item for item in summary.get("runs", []) if isinstance(item, dict)]
    successful = [item for item in runs if item.get("status") == "success"]
    counts = Counter(str(item.get("condition")) for item in successful)
    expected = {condition: 3 for condition in EXPECTED_CONDITIONS}
    if dict(counts) != expected:
        raise ValueError(f"Unexpected successful run counts for {spec.company}: {counts}")
    request = summary.get("request") or {}
    if str(request.get("selected_date")) != "20251031":
        raise ValueError(f"Unexpected selected date for {spec.company}")
    if int(request.get("replicates") or 0) != 3 or not request.get("freeze_upstream"):
        raise ValueError(f"Experiment was not frozen with three replicates: {spec.company}")
    actual_cap = request.get("news_total_max_results")
    if spec.news_cap is None:
        if actual_cap not in (None, ""):
            raise ValueError(f"Samsung news cap changed unexpectedly: {actual_cap}")
    elif int(actual_cap or 0) != spec.news_cap:
        raise ValueError(f"Unexpected news cap for {spec.company}: {actual_cap}")

    if evaluation.get("status") != "success":
        raise ValueError(f"Judge evaluation is not successful: {spec.evaluation}")
    pairs = [item for item in evaluation.get("pairs", []) if isinstance(item, dict)]
    successful_pairs = [item for item in pairs if item.get("status") == "success"]
    pair_counts = Counter(str(item.get("ablation_condition")) for item in successful_pairs)
    if len(successful_pairs) != 9 or dict(pair_counts) != {
        condition: 3 for condition in ABLATION_CONDITIONS
    }:
        raise ValueError(f"Unexpected Judge pair counts for {spec.company}: {pair_counts}")
    judge_model = ((manifest.get("request") or {}).get("judge_model"))
    if judge_model != "gpt-5.4":
        raise ValueError(f"Unexpected Judge model for {spec.company}: {judge_model}")


def _news_stats(context: dict[str, Any]) -> dict[str, Any]:
    events = context.get("news_events_topk") or []
    if not isinstance(events, list):
        raise ValueError("news_events_topk must be an array")
    dates: list[str] = []
    urls: set[str] = set()
    for event in events:
        if not isinstance(event, dict):
            continue
        representative = event.get("representative") or {}
        event_date = str(representative.get("time") or "")[:10]
        if event_date:
            dates.append(event_date)
        for url in event.get("members") or []:
            if url:
                urls.add(str(url))
        for article in event.get("articles") or []:
            if isinstance(article, dict) and article.get("url"):
                urls.add(str(article["url"]))
    return {
        "news_event_count": len(events),
        "source_article_count": len(urls),
        "news_date_min": min(dates) if dates else None,
        "news_date_max": max(dates) if dates else None,
        "active_news_days": len(set(dates)),
    }


def _recommendation_distribution(records: Iterable[dict[str, Any]]) -> Counter[str]:
    return Counter(
        str(item.get("recommendation"))
        for item in records
        if item.get("recommendation") in {"Buy", "Hold", "Sell"}
    )


def _distribution_text(distribution: Counter[str]) -> str:
    return (
        f"Buy {distribution['Buy']} / Hold {distribution['Hold']} / "
        f"Sell {distribution['Sell']}"
    )


def _recommendation_rows(records: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    baseline = {
        (str(item["case_id"]), int(item["replicate"])): str(item["recommendation"])
        for item in records
        if item.get("condition") == "full" and item.get("recommendation")
    }
    rows: list[dict[str, Any]] = []
    raw: dict[str, Any] = {}
    for condition in EXPECTED_CONDITIONS:
        selected = [item for item in records if item.get("condition") == condition]
        distribution = _recommendation_distribution(selected)
        grouped: dict[str, list[str]] = defaultdict(list)
        for item in selected:
            grouped[str(item["case_id"])].append(str(item["recommendation"]))
        groups = [values for values in grouped.values() if len(values) == 3]
        unanimous = sum(len(set(values)) == 1 for values in groups)
        majority_rates = [max(Counter(values).values()) / len(values) for values in groups]

        flip_count: int | None = None
        flip_rate: float | None = None
        directions: Counter[str] = Counter()
        if condition != "full":
            comparisons: list[tuple[str, str]] = []
            for item in selected:
                key = (str(item["case_id"]), int(item["replicate"]))
                if key in baseline:
                    comparisons.append((baseline[key], str(item["recommendation"])))
            flip_count = sum(first != second for first, second in comparisons)
            flip_rate = flip_count / len(comparisons) if comparisons else None
            directions.update(
                f"{first}->{second}" for first, second in comparisons if first != second
            )

        row = {
            "condition": CONDITION_LABELS[condition],
            "condition_id": condition,
            "report_count": len(selected),
            "buy": distribution["Buy"],
            "hold": distribution["Hold"],
            "sell": distribution["Sell"],
            "recommendation_distribution": _distribution_text(distribution),
            "flip_count_vs_full": flip_count,
            "flip_rate_vs_full": flip_rate,
            "flip_directions": ", ".join(
                f"{key} {count}" for key, count in sorted(directions.items())
            ),
            "repeat_case_count": len(groups),
            "unanimous_case_count": unanimous,
            "unanimous_repeat_rate": unanimous / len(groups) if groups else None,
            "mean_majority_agreement": (
                sum(majority_rates) / len(majority_rates) if majority_rates else None
            ),
        }
        rows.append(row)
        raw[condition] = row
    return rows, raw


def _fmt_rate(value: float | None) -> str:
    return "—" if value is None else f"{100.0 * value:.1f}%"


def _fmt_ci(values: list[float | None]) -> str:
    if len(values) != 2 or any(value is None for value in values):
        return "—"
    return f"[{100.0 * float(values[0]):.1f}%, {100.0 * float(values[1]):.1f}%]"


def _aggregate_rows(aggregation: dict[str, Any]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    overall_rows: list[dict[str, Any]] = []
    axis_rows: list[dict[str, Any]] = []
    by_condition = aggregation["by_condition"]
    for condition in ABLATION_CONDITIONS:
        condition_data = by_condition[condition]
        overall = condition_data["overall"]
        overall_rows.append(
            {
                "condition": CONDITION_LABELS[condition],
                "condition_id": condition,
                "report_pairs": condition_data["valid_pairs"],
                **overall,
                "ci_95_low": overall["ci_95"][0],
                "ci_95_high": overall["ci_95"][1],
                "mean_order_consistency": condition_data["mean_order_consistency"],
            }
        )
        for axis in AXES:
            axis_data = condition_data["axes"][axis]
            consistent_count = 0
            eligible_count = 0
            # Filled by the caller after aggregation from pair-level records.
            axis_rows.append(
                {
                    "condition": CONDITION_LABELS[condition],
                    "condition_id": condition,
                    "axis": AXIS_LABELS[axis],
                    "axis_id": axis,
                    **axis_data,
                    "ci_95_low": axis_data["ci_95"][0],
                    "ci_95_high": axis_data["ci_95"][1],
                    "order_consistent_count": consistent_count,
                    "order_evaluations": eligible_count,
                    "order_consistency_rate": None,
                }
            )
    return overall_rows, axis_rows


def _fill_axis_consistency(axis_rows: list[dict[str, Any]], pairs: list[dict[str, Any]]) -> None:
    lookup = {(row["condition_id"], row["axis_id"]): row for row in axis_rows}
    for pair in pairs:
        if pair.get("status") != "success":
            continue
        condition = str(pair.get("ablation_condition"))
        for axis in AXES:
            axis_result = (pair.get("axes") or {}).get(axis) or {}
            key = (condition, axis)
            row = lookup[key]
            row["order_evaluations"] += 1
            row["order_consistent_count"] += int(bool(axis_result.get("order_consistent")))
    for row in axis_rows:
        total = row["order_evaluations"]
        row["order_consistency_rate"] = row["order_consistent_count"] / total if total else None


def _write_csv(path: Path, rows: list[dict[str, Any]], fields: list[tuple[str, str]]) -> None:
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=[label for _, label in fields])
        writer.writeheader()
        for row in rows:
            writer.writerow({label: row.get(key) for key, label in fields})


def _markdown_table(headers: list[str], values: list[list[Any]]) -> str:
    lines = [
        "| " + " | ".join(headers) + " |",
        "| " + " | ".join("---" for _ in headers) + " |",
    ]
    for row in values:
        cleaned = [str(value).replace("|", "\\|").replace("\n", " ") for value in row]
        lines.append("| " + " | ".join(cleaned) + " |")
    return "\n".join(lines) + "\n"


def _write_table_1(path: Path, rows: list[dict[str, Any]]) -> None:
    headers = [
        "기업",
        "업종",
        "경쟁사",
        "기준일",
        "뉴스 기간",
        "뉴스 사건군",
        "원문 기사",
        "활성일",
        "뉴스 상한",
        "보고서 분포",
    ]
    values = [
        [
            row["company"],
            row["industry"],
            row["peer"],
            row["selected_date"],
            f"{row['news_date_min']}~{row['news_date_max']}",
            row["news_event_count"],
            row["source_article_count"],
            row["active_news_days"],
            row["news_cap"] if row["news_cap"] is not None else "미적용",
            "Full/각 ablation 3회 (총 12)",
        ]
        for row in rows
    ]
    note = (
        "\n주: 뉴스 사건군은 중복·유사 기사를 군집화한 뒤 보고서 컨텍스트에 저장된 "
        "`news_events_topk`의 수이며, 원문 기사는 사건군 내 고유 URL 수이다. 삼성전자는 "
        "초기 실행 당시 뉴스 상한을 적용하지 않았고, 나머지 기업은 40건 상한을 적용했다.\n"
    )
    path.write_text(_markdown_table(headers, values) + note, encoding="utf-8")


def _write_table_2(path: Path, rows: list[dict[str, Any]]) -> None:
    headers = [
        "Ablation",
        "보고서 쌍",
        "축 판정 n",
        "Full 승/무/패",
        "조정 승률",
        "95% CI",
        "순서 일치율",
    ]
    values = [
        [
            row["condition"],
            row["report_pairs"],
            row["n"],
            f"{row['full_win']}/{row['tie']}/{row['ablation_win']}",
            _fmt_rate(row["adjusted_win_rate_for_full"]),
            _fmt_ci(row["ci_95"]),
            _fmt_rate(row["mean_order_consistency"]),
        ]
        for row in rows
    ]
    note = (
        "\n주: Full 승=1, 무승부=0.5, 패=0으로 점수화하였다. 각 기업 내부의 3회 반복과 "
        "6개 평가축을 먼저 평균한 뒤 6개 기업을 동일 가중해 조정 승률을 계산했다. 95% CI는 "
        "기업을 군집 단위로 재표집한 percentile cluster bootstrap(10,000회) 결과이다.\n"
    )
    path.write_text(_markdown_table(headers, values) + note, encoding="utf-8")


def _write_table_3(path: Path, rows: list[dict[str, Any]]) -> None:
    headers = [
        "조건",
        "보고서 n",
        "추천 분포(B/H/S)",
        "Full 대비 변경",
        "변경률",
        "변경 방향",
        "3회 완전일치 기업",
        "반복 안정성",
        "평균 다수일치율",
    ]
    values = []
    for row in rows:
        changes = "—" if row["flip_count_vs_full"] is None else row["flip_count_vs_full"]
        values.append(
            [
                row["condition"],
                row["report_count"],
                f"{row['buy']}/{row['hold']}/{row['sell']}",
                changes,
                _fmt_rate(row["flip_rate_vs_full"]),
                row["flip_directions"] or "—",
                f"{row['unanimous_case_count']}/{row['repeat_case_count']}",
                _fmt_rate(row["unanimous_repeat_rate"]),
                _fmt_rate(row["mean_majority_agreement"]),
            ]
        )
    note = (
        "\n주: 추천변경률은 같은 기업·같은 반복 번호의 Full과 ablation 추천을 짝지어 계산했다. "
        "반복 안정성은 각 조건에서 동일 기업의 3회 추천이 모두 같은 기업의 비율이다.\n"
    )
    path.write_text(_markdown_table(headers, values) + note, encoding="utf-8")


def _write_table_4(path: Path, rows: list[dict[str, Any]]) -> None:
    headers = [
        "Ablation",
        "평가축",
        "n",
        "Full 승/무/패",
        "조정 승률",
        "95% CI",
        "순서 일치",
    ]
    values = [
        [
            row["condition"],
            row["axis"],
            row["n"],
            f"{row['full_win']}/{row['tie']}/{row['ablation_win']}",
            _fmt_rate(row["adjusted_win_rate_for_full"]),
            _fmt_ci(row["ci_95"]),
            f"{row['order_consistent_count']}/{row['order_evaluations']} "
            f"({_fmt_rate(row['order_consistency_rate'])})",
        ]
        for row in rows
    ]
    note = (
        "\n주: Judge는 후보의 정체와 조건명을 숨긴 상태에서 A/B 및 B/A 순서로 각각 평가했다. "
        "두 순서가 같은 후보를 선택한 경우만 승패로 확정하고, 불일치는 무승부로 보수적으로 "
        "처리했다. Judge 모델은 모든 기업에서 gpt-5.4이다.\n"
    )
    path.write_text(_markdown_table(headers, values) + note, encoding="utf-8")


def _write_results_summary(
    path: Path,
    dataset_rows: list[dict[str, Any]],
    overall_rows: list[dict[str, Any]],
    recommendation_rows: list[dict[str, Any]],
) -> None:
    overall = {row["condition_id"]: row for row in overall_rows}
    recommendation = {row["condition_id"]: row for row in recommendation_rows}
    total_news = sum(row["news_event_count"] for row in dataset_rows)
    total_articles = sum(row["source_article_count"] for row in dataset_rows)
    paragraphs = [
        "# 6개 기업 ablation 통합 결과",
        "",
        "## 실험 설정",
        "",
        (
            "서로 다른 6개 업종의 상장기업을 대상으로 2025년 10월 31일을 분석 "
            "기준일로 설정하였다. 각 기업에서 Full, SY 제외, 경쟁사 제외, Sub data 제외 "
            "조건을 각각 3회 생성하여 총 72개 보고서를 분석했다. 입력 수집 결과는 조건 간 "
            f"고정했으며, 대상기업 뉴스 컨텍스트에는 총 {total_news}개 사건군과 고유 원문 "
            f"{total_articles}건이 포함되었다."
        ),
        "",
        "보고서 품질은 gpt-5.4 기반의 blind pairwise LLM Judge로 평가했다. 각 Full–ablation "
        "보고서 쌍을 A/B와 B/A 두 순서로 평가하고, 두 순서가 같은 후보를 선택할 때만 "
        "승패로 확정했다. 평가지표는 재무·수치, 뉴스, 기업·시장·경쟁사, 투자판단, 리스크, "
        "작성품질의 6개 축이다.",
        "",
        "## 논문 결과 문단 초안",
        "",
        (
            f"경쟁사 정보를 제거했을 때 Full 보고서의 조정 승률은 "
            f"{_fmt_rate(overall['no_competitor']['adjusted_win_rate_for_full'])} "
            f"(95% CI {_fmt_ci(overall['no_competitor']['ci_95'])})로 가장 높았다. "
            f"Sub data를 제거한 조건에서도 Full의 조정 승률은 "
            f"{_fmt_rate(overall['primary_only']['adjusted_win_rate_for_full'])} "
            f"(95% CI {_fmt_ci(overall['primary_only']['ci_95'])})였다. 반면 SY를 제거한 "
            f"조건의 조정 승률은 {_fmt_rate(overall['no_sy']['adjusted_win_rate_for_full'])} "
            f"(95% CI {_fmt_ci(overall['no_sy']['ci_95'])})로 50% 부근에 머물러, 본 실험에서는 "
            "SY 단계가 전반적 보고서 품질을 일관되게 높였다는 증거가 확인되지 않았다."
        ),
        "",
        (
            f"Full 대비 추천변경률은 SY 제외 "
            f"{_fmt_rate(recommendation['no_sy']['flip_rate_vs_full'])}, 경쟁사 제외 "
            f"{_fmt_rate(recommendation['no_competitor']['flip_rate_vs_full'])}, Sub data 제외 "
            f"{_fmt_rate(recommendation['primary_only']['flip_rate_vs_full'])}였다. 3회 반복의 "
            "기업별 완전일치율은 각각 "
            f"{_fmt_rate(recommendation['no_sy']['unanimous_repeat_rate'])}, "
            f"{_fmt_rate(recommendation['no_competitor']['unanimous_repeat_rate'])}, "
            f"{_fmt_rate(recommendation['primary_only']['unanimous_repeat_rate'])}로 나타났다."
        ),
        "",
        "## 해석 시 주의사항",
        "",
        "- 신뢰구간의 독립 단위는 보고서나 평가축이 아니라 기업이며, 기업 수가 6개이므로 "
        "신뢰구간은 탐색적 근거로 해석해야 한다.",
        "- 동일 계열 LLM이 보고서 생성과 평가에 관여할 수 있는 model-family bias가 있으며, "
        "사람 평가는 수행하지 않았다.",
        "- 삼성전자는 초기 실험의 뉴스 425건을 유지한 반면 나머지 5개 기업에는 40건 상한을 "
        "적용했다. 따라서 뉴스 입력량이 완전히 균형화된 실험은 아니다.",
        "- 단일 기준일과 기업별 단일 경쟁사를 사용했으므로 다른 시점·비교군으로의 일반화에는 "
        "추가 검증이 필요하다.",
        "",
        "## 파일 안내",
        "",
        "- `table_1_dataset.md`: 기업·업종·날짜·뉴스 및 보고서 분포",
        "- `table_2_adjusted_win_rate.md`: Full 대비 ablation 조정 승률과 95% CI",
        "- `table_3_recommendation_stability.md`: 추천변경률과 반복 안정성",
        "- `table_4_llm_judge_axes.md`: LLM Judge 평가축별 상세 결과",
        "- 각 표의 `.csv`: 논문 편집용 원자료",
        "- `aggregate_results.json`: 반올림하지 않은 전체 수치",
        "- `aggregate_manifest.json`: 입력 파일, SHA-256, bootstrap 설정",
        "",
    ]
    path.write_text("\n".join(paragraphs), encoding="utf-8")


def build_tables(
    project_root: Path,
    output_dir: Path,
    *,
    bootstrap_samples: int,
    seed: int,
) -> dict[str, Any]:
    suite_base = project_root / "Output_total" / "experiments" / "ablations"
    eval_base = project_root / "Output_total" / "Evaluation" / "Final_Report_Ablation"
    output_dir.mkdir(parents=True, exist_ok=True)

    dataset_rows: list[dict[str, Any]] = []
    recommendation_records: list[dict[str, Any]] = []
    pair_results: list[dict[str, Any]] = []
    sources: list[dict[str, Any]] = []
    company_results: list[dict[str, Any]] = []

    for spec in COMPANIES:
        suite_root = suite_base / spec.suite
        evaluation_root = eval_base / spec.evaluation
        summary_path = suite_root / "ablation_summary.json"
        evaluation_path = evaluation_root / "evaluation_summary.json"
        manifest_path = evaluation_root / "experiment_manifest.json"
        news_context_path = _find_news_context(suite_root, spec.company)
        summary = _read_json(summary_path)
        evaluation = _read_json(evaluation_path)
        manifest = _read_json(manifest_path)
        news_context = _read_json(news_context_path)
        _validate_suite(spec, summary, evaluation, manifest)

        runs = [
            item for item in summary["runs"]
            if isinstance(item, dict) and item.get("status") == "success"
        ]
        company_distribution = _recommendation_distribution(runs)
        stats = _news_stats(news_context)
        dataset_rows.append(
            {
                "company": spec.company,
                "industry": spec.industry,
                "peer": spec.peer,
                "selected_date": "2025-10-31",
                "news_window": str(summary["request"].get("news_window")),
                "news_cap": spec.news_cap,
                "report_count": len(runs),
                "recommendation_distribution": _distribution_text(company_distribution),
                **stats,
            }
        )
        for run in runs:
            recommendation_records.append(
                {
                    "case_id": spec.company,
                    "condition": str(run["condition"]),
                    "replicate": int(run["replicate"]),
                    "recommendation": str(run["recommendation"]),
                }
            )
        pair_results.extend(evaluation["pairs"])
        company_results.append(
            {
                "company": spec.company,
                "suite": spec.suite,
                "evaluation": spec.evaluation,
                "aggregation": evaluation["aggregation"],
                "recommendation_analysis": evaluation["recommendation_analysis"],
            }
        )
        for source_type, source_path in (
            ("ablation_summary", summary_path),
            ("evaluation_summary", evaluation_path),
            ("evaluation_manifest", manifest_path),
            ("news_context", news_context_path),
        ):
            sources.append(
                {
                    "company": spec.company,
                    "type": source_type,
                    "path": str(source_path.relative_to(project_root)),
                    "sha256": _sha256(source_path),
                }
            )

    if len(pair_results) != 54:
        raise ValueError(f"Expected 54 successful report pairs, got {len(pair_results)}")
    if len(recommendation_records) != 72:
        raise ValueError(f"Expected 72 successful reports, got {len(recommendation_records)}")

    aggregation = aggregate_pair_results(
        pair_results,
        bootstrap_samples=bootstrap_samples,
        seed=seed,
    )
    overall_rows, axis_rows = _aggregate_rows(aggregation)
    _fill_axis_consistency(axis_rows, pair_results)
    recommendation_rows, recommendation_analysis = _recommendation_rows(
        recommendation_records
    )

    _write_table_1(output_dir / "table_1_dataset.md", dataset_rows)
    _write_table_2(output_dir / "table_2_adjusted_win_rate.md", overall_rows)
    _write_table_3(output_dir / "table_3_recommendation_stability.md", recommendation_rows)
    _write_table_4(output_dir / "table_4_llm_judge_axes.md", axis_rows)

    _write_csv(
        output_dir / "table_1_dataset.csv",
        dataset_rows,
        [
            ("company", "기업"),
            ("industry", "업종"),
            ("peer", "경쟁사"),
            ("selected_date", "분석 기준일"),
            ("news_date_min", "뉴스 시작일"),
            ("news_date_max", "뉴스 종료일"),
            ("active_news_days", "뉴스 활성일 수"),
            ("news_event_count", "뉴스 사건군 수"),
            ("source_article_count", "고유 원문 기사 수"),
            ("news_cap", "뉴스 상한"),
            ("report_count", "보고서 수"),
            ("recommendation_distribution", "전체 추천 분포"),
        ],
    )
    _write_csv(
        output_dir / "table_2_adjusted_win_rate.csv",
        overall_rows,
        [
            ("condition", "Ablation"),
            ("condition_id", "조건 ID"),
            ("report_pairs", "보고서 쌍"),
            ("n", "평가축 판정 수"),
            ("company_clusters", "기업 군집 수"),
            ("full_win", "Full 승"),
            ("tie", "무승부"),
            ("ablation_win", "Full 패"),
            ("adjusted_win_rate_for_full", "Full 조정 승률"),
            ("ci_95_low", "95% CI 하한"),
            ("ci_95_high", "95% CI 상한"),
            ("ci_status", "CI 방법"),
            ("mean_order_consistency", "평균 순서 일치율"),
        ],
    )
    _write_csv(
        output_dir / "table_3_recommendation_stability.csv",
        recommendation_rows,
        [
            ("condition", "조건"),
            ("condition_id", "조건 ID"),
            ("report_count", "보고서 수"),
            ("buy", "Buy"),
            ("hold", "Hold"),
            ("sell", "Sell"),
            ("flip_count_vs_full", "Full 대비 추천변경 수"),
            ("flip_rate_vs_full", "Full 대비 추천변경률"),
            ("flip_directions", "변경 방향"),
            ("repeat_case_count", "반복 기업 수"),
            ("unanimous_case_count", "3회 완전일치 기업 수"),
            ("unanimous_repeat_rate", "반복 안정성"),
            ("mean_majority_agreement", "평균 다수일치율"),
        ],
    )
    _write_csv(
        output_dir / "table_4_llm_judge_axes.csv",
        axis_rows,
        [
            ("condition", "Ablation"),
            ("condition_id", "조건 ID"),
            ("axis", "평가축"),
            ("axis_id", "평가축 ID"),
            ("n", "판정 수"),
            ("company_clusters", "기업 군집 수"),
            ("full_win", "Full 승"),
            ("tie", "무승부"),
            ("ablation_win", "Full 패"),
            ("adjusted_win_rate_for_full", "Full 조정 승률"),
            ("ci_95_low", "95% CI 하한"),
            ("ci_95_high", "95% CI 상한"),
            ("ci_status", "CI 방법"),
            ("order_consistent_count", "순서 일치 수"),
            ("order_evaluations", "순서 평가 수"),
            ("order_consistency_rate", "순서 일치율"),
        ],
    )

    results = {
        "schema_version": "paper_ablation_aggregate_v1",
        "design": {
            "company_count": len(COMPANIES),
            "conditions": list(EXPECTED_CONDITIONS),
            "replicates": 3,
            "report_count": len(recommendation_records),
            "judge_pair_count": len(pair_results),
            "axes": list(AXES),
            "judge_model": "gpt-5.4",
            "generation_model": "gpt-5.4-mini",
            "selected_date": "2025-10-31",
            "bootstrap_samples": bootstrap_samples,
            "bootstrap_seed": seed,
            "bootstrap_unit": "company",
        },
        "dataset": dataset_rows,
        "aggregation": aggregation,
        "overall_table": overall_rows,
        "axis_table": axis_rows,
        "recommendation_table": recommendation_rows,
        "recommendation_analysis": recommendation_analysis,
        "company_results": company_results,
    }
    (output_dir / "aggregate_results.json").write_text(
        json.dumps(results, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    expected_output_files = [
        "aggregate_manifest.json",
        "aggregate_results.json",
        "paper_results_summary.md",
        "table_1_dataset.csv",
        "table_1_dataset.md",
        "table_2_adjusted_win_rate.csv",
        "table_2_adjusted_win_rate.md",
        "table_3_recommendation_stability.csv",
        "table_3_recommendation_stability.md",
        "table_4_llm_judge_axes.csv",
        "table_4_llm_judge_axes.md",
    ]
    manifest_payload = {
        "schema_version": "paper_ablation_manifest_v1",
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "offline_only": True,
        "bootstrap_samples": bootstrap_samples,
        "bootstrap_seed": seed,
        "bootstrap_unit": "company",
        "source_files": sources,
        "output_files": expected_output_files,
    }
    (output_dir / "aggregate_manifest.json").write_text(
        json.dumps(manifest_payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    _write_results_summary(
        output_dir / "paper_results_summary.md",
        dataset_rows,
        overall_rows,
        recommendation_rows,
    )
    return results


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--project-root",
        type=Path,
        default=Path(__file__).resolve().parents[1],
    )
    parser.add_argument("--output-dir", type=Path, default=Path(DEFAULT_OUTPUT))
    parser.add_argument("--bootstrap-samples", type=int, default=BOOTSTRAP_SAMPLES)
    parser.add_argument("--seed", type=int, default=BOOTSTRAP_SEED)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    project_root = args.project_root.resolve()
    output_dir = args.output_dir
    if not output_dir.is_absolute():
        output_dir = project_root / output_dir
    build_tables(
        project_root,
        output_dir.resolve(),
        bootstrap_samples=args.bootstrap_samples,
        seed=args.seed,
    )
    print(f"Paper tables written to: {output_dir.resolve()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
