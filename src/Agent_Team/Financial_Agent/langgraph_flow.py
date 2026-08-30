#!/usr/bin/env python3
import argparse
import copy
import json
import math
from datetime import date
from pathlib import Path
from typing import Any, Dict, List, TypedDict

from langgraph.graph import END, START, StateGraph
from shared.evidence_contracts import (
    canonical_evidence_id,
    validate_evidence_catalog,
)
from Agent_Team.Financial_Agent.financial_analysis_agent import (
    apply_financial_analysis,
    generate_financial_analysis_with_llm,
)

SECONDARY_MARKET_METRICS = (
    "stock_return_5d",
    "stock_return_20d",
    "stock_return_60d",
    "stock_excess_return_20d",
    "stock_relative_strength_60",
    "stock_volume_ratio_20",
    "stock_volatility_20",
    "stock_rsi_14",
)


class FinancialAnalystGraphState(TypedDict, total=False):
    manifest_path: str
    manifest: Dict[str, Any]
    inputs: Dict[str, Any]
    model: str
    transcript: List[Dict[str, str]]
    financial_analysis_output: Dict[str, Any]
    llm_analysis: Dict[str, Any]
    report_output: Dict[str, Any]
    schema_validation: Dict[str, Any]


def load_json(path: str) -> Dict[str, Any]:
    return json.loads(Path(path).read_text())


def load_input_file(path: str) -> Any:
    return json.loads(Path(path).read_text())


def metric_period(dart: Dict[str, Any], key: str, period_key: str = "current_fiscal_year") -> Dict[str, Any]:
    return dart["metrics_by_key"][key]["values_by_period"][period_key]


def metric_comp(dart: Dict[str, Any], key: str, comp: str = "2025_vs_2024") -> Dict[str, Any]:
    return dart["metrics_by_key"][key]["comparisons"][comp]


def pct(value: float | None) -> str:
    if value is None:
        return "N/A"
    return f"{value:.2%}"


def pct1(value: float | None) -> str:
    if value is None:
        return "N/A"
    return f"{value:.1%}"


def krw_eok(value: int | float | None) -> str:
    if value is None:
        return "N/A"
    return f"{value / 100_000_000:,.0f}억원"


def won(value: int | float | None) -> str:
    if value is None:
        return "N/A"
    return f"{value:,.0f}원"


def safe_div(numerator: int | float | None, denominator: int | float | None) -> float | None:
    if numerator is None or denominator in (None, 0):
        return None
    return numerator / denominator


def first_table(master: Dict[str, Any], section_key: str) -> Dict[str, Any]:
    tables = master.get(section_key, {}).get("tables", [])
    return tables[0] if tables else {}


def item_by_key(master: Dict[str, Any], section_key: str, item_key: str) -> Dict[str, Any]:
    return first_table(master, section_key).get("items_by_key", {}).get(item_key, {})


def current_numeric(master: Dict[str, Any], section_key: str, item_key: str) -> int | float | None:
    value = item_by_key(master, section_key, item_key).get("current_numeric")
    return value if isinstance(value, (int, float)) else None


def numeric_by_display_name(master: Dict[str, Any], section_key: str, display_name: str) -> int | float | None:
    items = first_table(master, section_key).get("items_by_key", {})
    for item in items.values():
        if item.get("display_name") == display_name:
            value = item.get("current_numeric")
            return value if isinstance(value, (int, float)) else None
    return None


def build_financial_position_summary(master: Dict[str, Any]) -> Dict[str, Any]:
    current_assets = current_numeric(master, "4-1", "current_assets")
    cash = current_numeric(master, "4-1", "cash_and_cash_equivalents")
    non_current_assets = current_numeric(master, "4-1", "non_current_assets")
    total_assets = current_numeric(master, "4-1", "total_assets")
    current_liabilities = current_numeric(master, "4-1", "current_liabilities")
    non_current_liabilities = current_numeric(master, "4-1", "non_current_liabilities")
    total_liabilities = current_numeric(master, "4-1", "total_liabilities")
    total_equity = current_numeric(master, "4-1", "total_equity")

    operating_cash_flow = current_numeric(master, "4-4", "cash_flows_from_operating_activities")
    investing_cash_flow = numeric_by_display_name(master, "4-4", "투자활동으로 인한 현금흐름")
    financing_cash_flow = numeric_by_display_name(master, "4-4", "재무활동으로 인한 현금흐름")
    net_cash_change = numeric_by_display_name(master, "4-4", "현금및현금성자산의 순증감")

    return {
        "current_assets": current_assets,
        "cash_and_cash_equivalents": cash,
        "non_current_assets": non_current_assets,
        "total_assets": total_assets,
        "current_liabilities": current_liabilities,
        "non_current_liabilities": non_current_liabilities,
        "total_liabilities": total_liabilities,
        "total_equity": total_equity,
        "debt_to_equity": safe_div(total_liabilities, total_equity),
        "liabilities_to_assets": safe_div(total_liabilities, total_assets),
        "equity_ratio": safe_div(total_equity, total_assets),
        "current_ratio": safe_div(current_assets, current_liabilities),
        "cash_ratio": safe_div(cash, current_liabilities),
        "operating_cash_flow": operating_cash_flow,
        "investing_cash_flow": investing_cash_flow,
        "financing_cash_flow": financing_cash_flow,
        "net_cash_change": net_cash_change,
    }


def resolve_dart_master_path(paths: Dict[str, str]) -> Path | None:
    explicit_path = paths.get("dart_master")
    if explicit_path:
        return Path(explicit_path)
    dart_main_path = paths.get("dart_main")
    if not dart_main_path:
        return None
    candidate = Path(dart_main_path).with_name("dart_master.json")
    return candidate if candidate.exists() else None


def format_period(period: Dict[str, Any] | None) -> str:
    if not period:
        return "확인 기간"
    fiscal_year = period.get("fiscal_year")
    period_type = period.get("period_type")
    basis = period.get("basis")
    parts = [str(part) for part in (fiscal_year, period_type, basis) if part not in (None, "")]
    if parts:
        return " ".join(parts)
    return str(period.get("label") or period.get("period_end") or "확인 기간")


def current_comparison_pair(dart: Dict[str, Any]) -> Dict[str, Any]:
    pairs = dart.get("comparison_pairs", [])
    for pair in pairs:
        if pair.get("current_period_key") == "current_fiscal_year":
            return pair
    return pairs[0] if pairs else {}


def metric_period_value(dart: Dict[str, Any], key: str, period_key: str = "current_fiscal_year") -> int | float | None:
    try:
        value = metric_period(dart, key, period_key).get("value")
    except KeyError:
        return None
    return value if isinstance(value, (int, float)) else None


def metric_comparison_value(dart: Dict[str, Any], key: str, comparison_key: str | None) -> int | float | None:
    if not comparison_key:
        return None
    try:
        value = metric_comp(dart, key, comparison_key).get("value")
    except KeyError:
        return None
    return value if isinstance(value, (int, float)) else None


def build_financial_trends(dart: Dict[str, Any]) -> Dict[str, Any]:
    """Preserve comparable period values for downstream Strategy and Writer."""

    metric_keys = (
        "revenue",
        "revenue_growth",
        "contribution_margin",
        "sga_margin",
        "operating_profit",
        "net_income",
        "operating_cash_flow",
        "eps",
    )
    periods = dart.get("periods") or {}
    comparison_pairs = dart.get("comparison_pairs") or []

    def values_for_period(period_key: str) -> Dict[str, Any]:
        values: Dict[str, Any] = {}
        for metric_key in metric_keys:
            metric = (dart.get("metrics_by_key") or {}).get(metric_key) or {}
            value = (metric.get("values_by_period") or {}).get(period_key)
            if isinstance(value, dict):
                values[metric_key] = value.get("value")
        return values

    same_period_pair = next(
        (
            pair
            for pair in comparison_pairs
            if pair.get("current_period_key") == "current_fiscal_year"
            and pair.get("previous_period_key") == "same_period_previous_year"
        ),
        {},
    )
    annual_keys = [
        key
        for key in ("previous_fiscal_year", "previous_fiscal_year_2", "previous_fiscal_year_3")
        if key in periods
    ]
    return {
        "current_vs_same_period": {
            "comparison": same_period_pair,
            "current_period": periods.get("current_fiscal_year", {}),
            "previous_period": periods.get("same_period_previous_year", {}),
            "current_values": values_for_period("current_fiscal_year"),
            "previous_values": values_for_period("same_period_previous_year"),
        },
        "annual_history": [
            {
                "period_key": period_key,
                "period": periods.get(period_key, {}),
                "values": values_for_period(period_key),
            }
            for period_key in annual_keys
        ],
        "ttm": {
            "period": periods.get("ttm", {}),
            "values": values_for_period("ttm"),
        },
        "comparison_pairs": comparison_pairs,
    }


def basis_caution(current_period: Dict[str, Any], previous_period: Dict[str, Any]) -> str:
    current_label = format_period(current_period)
    previous_label = format_period(previous_period)
    if current_period.get("basis") and previous_period.get("basis") and current_period.get("basis") != previous_period.get("basis"):
        return f"{current_label}와 {previous_label}는 집계 기준이 달라 동일 기간 YoY로 단정하지 않는다."
    return f"{current_label}와 {previous_label}는 동일 집계 기준일 때만 직접 비교한다."


def build_financial_secondary_context(inputs: Dict[str, Any]) -> Dict[str, Any]:
    """Build weekly News and market context without upstream agent claims."""

    return {
        "news": _news_weekly_summary_context(inputs.get("news_weekly_summaries") or {}),
        "market": _market_secondary_context(inputs.get("yfinance_market_summary")),
    }


def _news_weekly_summary_context(payload: Dict[str, Any]) -> Dict[str, Any]:
    output = payload.get("output") if isinstance(payload.get("output"), dict) else {}
    periods = output.get("periods") if isinstance(output.get("periods"), list) else []
    if not periods:
        periods = [
            item.get("output")
            for item in payload.get("period_results") or []
            if isinstance(item, dict)
            and item.get("status") in {None, "success"}
            and isinstance(item.get("output"), dict)
        ]
    catalog: Dict[str, Dict[str, Any]] = {}
    for item in periods:
        if not isinstance(item, dict):
            continue
        period = str(item.get("period") or "").strip()
        summary = str(item.get("period_summary") or "").strip()
        if not period or not summary:
            continue
        source_date = _weekly_period_start(period)
        evidence_id = canonical_evidence_id("news", f"weekly_{period}")
        catalog[evidence_id] = {
            "evidence_id": evidence_id,
            "domain": "news",
            "origin_type": "model_summarized",
            "source_ref": f"news_periods.{period.replace('-', '_')}",
            "source_date": source_date,
            "period": period,
            "metric": "weekly_news_context",
            "text": summary,
            "issues": [
                {
                    key: issue.get(key)
                    for key in ("issue", "mention_count", "importance")
                    if issue.get(key) not in (None, "", [], {})
                }
                for issue in item.get("issues") or []
                if isinstance(issue, dict)
            ],
            "source_event_ids": [
                str(event_id)
                for event_id in item.get("source_event_ids") or []
                if str(event_id).strip()
            ],
        }
    validate_evidence_catalog(catalog, allowed_domains={"news"})
    status = "available" if catalog else "unavailable"
    return {
        "status": status,
        "input_type": "weekly_news_summaries",
        "period_count": len(catalog),
        "evidence_catalog": catalog,
    }


def _weekly_period_start(period: str) -> str:
    try:
        return date.fromisoformat(period[:10]).isoformat()
    except ValueError:
        pass
    try:
        year_text, week_text = period.split("-W", 1)
        return date.fromisocalendar(int(year_text), int(week_text), 1).isoformat()
    except (TypeError, ValueError):
        return ""


def _market_secondary_context(payload: Any) -> Dict[str, Any]:
    row = payload[0] if isinstance(payload, list) and payload else payload
    if not isinstance(row, dict):
        return {"status": "unavailable", "evidence_catalog": {}}
    source_date = str(row.get("date") or "")
    catalog: Dict[str, Dict[str, Any]] = {}
    for metric in SECONDARY_MARKET_METRICS:
        value = row.get(metric)
        if not _finite_number(value):
            continue
        evidence_id = canonical_evidence_id("market", metric)
        catalog[evidence_id] = {
            "evidence_id": evidence_id,
            "domain": "market",
            "origin_type": "raw_source",
            "source_ref": f"market_full_dataset.latest.{metric}",
            "source_date": source_date,
            "period": "",
            "metric": metric,
            "value": value,
            "unit": "index" if "rsi" in metric or "volume_ratio" in metric else "ratio",
        }
    validate_evidence_catalog(catalog, allowed_domains={"market"})
    return {"status": "available" if catalog else "unavailable", "evidence_catalog": catalog}


def _finite_number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(float(value))


def infer_statement_scope(dart_master: Dict[str, Any]) -> str:
    """Infer the statement scope from canonical DART table titles."""

    titles = [
        str(table.get("table_title") or "")
        for section_key in ("4-1", "4-2", "4-4")
        for table in (dart_master.get(section_key) or {}).get("tables", [])
        if isinstance(table, dict)
    ]
    if any("연결" in title for title in titles):
        return "consolidated"
    if any(title for title in titles):
        return "separate"
    return "unknown"


def reconcile_revenue_breakdown(
    revenue_breakdown: Dict[str, Any],
    *,
    financial_statement_revenue_krw: int | float | None,
    statement_scope: str,
) -> Dict[str, Any]:
    """Attach a typed scope and total reconciliation to a disclosed breakdown."""

    result = copy.deepcopy(revenue_breakdown) if isinstance(revenue_breakdown, dict) else {}
    result["breakdown_scope"] = str(result.get("breakdown_scope") or "unknown")
    result["statement_scope"] = statement_scope
    result["scope_source_text"] = str(
        result.get("scope_source_text")
        or result.get("section_title")
        or (result.get("source") or {}).get("report_name")
        or ""
    )
    current_key = str(result.get("current_period_key") or "")
    current_total = (result.get("totals_by_period") or {}).get(current_key) or {}
    breakdown_total = current_total.get("revenue_krw")
    coverage_ratio = safe_div(breakdown_total, financial_statement_revenue_krw)
    if breakdown_total is None or financial_statement_revenue_krw in (None, 0):
        status = "incomparable"
    elif result["breakdown_scope"] not in {"unknown", statement_scope}:
        status = "scope_mismatch"
    elif coverage_ratio is not None and abs(coverage_ratio - 1.0) <= 0.02:
        status = "matched"
    elif coverage_ratio is not None and 0 < coverage_ratio < 1.0:
        status = "partial"
    else:
        status = "scope_mismatch"
    validation = copy.deepcopy(result.get("validation") or {})
    validation["financial_statement_reconciliation"] = {
        "breakdown_total_krw": breakdown_total,
        "financial_statement_revenue_krw": financial_statement_revenue_krw,
        "coverage_ratio": coverage_ratio,
        "reconciliation_status": status,
        "tolerance_ratio": 0.02,
    }
    result["validation"] = validation
    return result


def normalized_financial_metrics(financial_trends: Dict[str, Any]) -> Dict[str, Any]:
    """Return scale-neutral margins for target/peer comparison."""

    comparison = financial_trends.get("current_vs_same_period") or {}

    def metrics(values: Any) -> Dict[str, Any]:
        row = values if isinstance(values, dict) else {}
        revenue = row.get("revenue")
        return {
            "operating_margin": safe_div(row.get("operating_profit"), revenue),
            "net_margin": safe_div(row.get("net_income"), revenue),
            "operating_cash_flow_margin": safe_div(row.get("operating_cash_flow"), revenue),
        }

    return {
        "current_period": copy.deepcopy(comparison.get("current_period") or {}),
        "previous_period": copy.deepcopy(comparison.get("previous_period") or {}),
        "current_values": metrics(comparison.get("current_values")),
        "previous_values": metrics(comparison.get("previous_values")),
    }


def build_financial_analyst_output(manifest: Dict[str, Any], inputs: Dict[str, Any]) -> Dict[str, Any]:
    dart = inputs["dart_main"]
    dart_master = inputs.get("dart_master", {})
    collection_context = copy.deepcopy(dart.get("collection_context") or {})
    financial_trends = build_financial_trends(dart)
    statement_scope = infer_statement_scope(dart_master)
    collection_context["statement_scope"] = statement_scope
    current_statement_revenue = (
        (financial_trends.get("current_vs_same_period") or {}).get("current_values") or {}
    ).get("revenue")
    revenue_breakdown = reconcile_revenue_breakdown(
        dart.get("revenue_breakdown") or {},
        financial_statement_revenue_krw=current_statement_revenue,
        statement_scope=statement_scope,
    )
    financial_trends["normalized_metrics"] = normalized_financial_metrics(financial_trends)
    share_information = dart.get("share_information") or {}
    position = build_financial_position_summary(dart_master)
    comparison_current_values = (
        (financial_trends.get("current_vs_same_period") or {}).get("current_values") or {}
    )
    if position.get("operating_cash_flow") is None and _finite_number(
        comparison_current_values.get("operating_cash_flow")
    ):
        # 현금흐름표 master 표의 계정명 매핑이 누락돼도 주 분석 데이터에 이미
        # 정규화된 동일 기간 값이 있으면 같은 공시 원천의 값을 사용한다.
        position["operating_cash_flow"] = comparison_current_values["operating_cash_flow"]

    current_period = dart.get("periods", {}).get("current_fiscal_year", {})
    comparison_pair = current_comparison_pair(dart)
    has_previous_period = bool(comparison_pair)
    previous_period_key = comparison_pair.get("previous_period_key", "previous_fiscal_year")
    previous_period = dart.get("periods", {}).get(previous_period_key, {})
    comparison_key = comparison_pair.get("comparison_key")
    period_label = format_period(current_period)
    previous_period_label = format_period(previous_period)
    period_basis = str(current_period.get("basis") or "period")
    period_caution = (
        basis_caution(current_period, previous_period)
        if has_previous_period
        else f"{period_label} 단일 보고서만 사용해 기간 간 비교는 수행하지 않는다."
    )

    target = manifest.get("target_entity", {})
    company_name = str(target.get("company_name") or "분석 대상 기업")
    ticker = str(target.get("ticker") or "")
    corp_code = str(target.get("corp_code") or target.get("company_code") or "")
    as_of_date = str(target.get("as_of_date") or current_period.get("period_end") or "")

    revenue = metric_period_value(dart, "revenue")
    previous_revenue = metric_period_value(dart, "revenue", previous_period_key) if has_previous_period else None
    revenue_growth = metric_comparison_value(dart, "revenue_growth", comparison_key)
    if revenue_growth is None and revenue is not None and previous_revenue not in (None, 0):
        revenue_growth = (revenue - previous_revenue) / previous_revenue
    contribution_margin = metric_period_value(dart, "contribution_margin")
    previous_contribution_margin = (
        metric_period_value(dart, "contribution_margin", previous_period_key) if has_previous_period else None
    )
    sga_margin = metric_period_value(dart, "sga_margin")
    previous_sga_margin = metric_period_value(dart, "sga_margin", previous_period_key) if has_previous_period else None
    eps = metric_period_value(dart, "eps")
    previous_eps = metric_period_value(dart, "eps", previous_period_key) if has_previous_period else None
    trend_values = (financial_trends.get("current_vs_same_period") or {}).get("current_values") or {}
    previous_trend_values = (financial_trends.get("current_vs_same_period") or {}).get("previous_values") or {}
    normalized = financial_trends.get("normalized_metrics") or {}
    normalized_current = normalized.get("current_values") or {}
    normalized_previous = normalized.get("previous_values") or {}

    revenue_anchor = (
        f"DART 기준 {period_label} 매출은 {krw_eok(revenue)}이고 "
        f"{previous_period_label} 매출은 {krw_eok(previous_revenue)}이다. "
        f"증감률은 {pct(revenue_growth)}이다. {period_caution}"
        if has_previous_period
        else f"DART 기준 {period_label} 매출은 {krw_eok(revenue)}이다. {period_caution}"
    )
    profitability_anchor = (
        f"공헌이익률은 {pct(contribution_margin)}, 비교 기간은 {pct(previous_contribution_margin)}이고 "
        f"판관비율은 {pct(sga_margin)}, 비교 기간은 {pct(previous_sga_margin)}다. "
        f"영업이익률은 {pct(normalized_current.get('operating_margin'))}, 비교 기간은 "
        f"{pct(normalized_previous.get('operating_margin'))}이며 순이익률은 "
        f"{pct(normalized_current.get('net_margin'))}, 비교 기간은 "
        f"{pct(normalized_previous.get('net_margin'))}다."
    )
    eps_anchor = (
        f"DART 기준 {period_label} EPS는 {won(eps)}이고 "
        f"{previous_period_label} EPS는 {won(previous_eps)}이다. {period_caution}"
    )
    financial_position_anchor = (
        f"영업활동현금흐름 {krw_eok(position['operating_cash_flow'])}, "
        f"자산총계 {krw_eok(position['total_assets'])}, 부채총계 {krw_eok(position['total_liabilities'])}, "
        f"자본총계 {krw_eok(position['total_equity'])}, 유동비율 {pct1(position['current_ratio'])}, "
        f"부채비율 {pct1(position['debt_to_equity'])}가 DART에서 확인된다."
    )

    claims = [
        {
            "claim_id": "F001",
            "claim_ko": f"{period_label} 매출 및 전년 동기 성장률",
            "financial_dimension": "growth",
            "status": "active",
            "dart_anchor_summary_ko": revenue_anchor,
            "caution_ko": period_caution,
            "action": "use_with_caution",
        },
        {
            "claim_id": "F002",
            "claim_ko": f"{period_label} 수익성과 비용 효율",
            "financial_dimension": "profitability",
            "status": "active",
            "dart_anchor_summary_ko": profitability_anchor,
            "caution_ko": f"마진과 비용 지표는 {period_basis} 기준이다.",
            "action": "use_normally",
        },
        {
            "claim_id": "F003",
            "claim_ko": f"{period_label} 주당순이익(EPS)",
            "financial_dimension": "eps",
            "status": "caution",
            "dart_anchor_summary_ko": eps_anchor,
            "caution_ko": period_caution,
            "action": "use_with_caution",
        },
        {
            "claim_id": "F004",
            "claim_ko": f"{period_label} 영업현금흐름",
            "financial_dimension": "cash_flow",
            "status": "conditional",
            "dart_anchor_summary_ko": f"영업활동현금흐름 {krw_eok(position['operating_cash_flow'])}가 DART에서 확인된다.",
            "caution_ko": f"현금흐름표는 {period_basis} 누적 기간 기준이다.",
            "action": "use_normally",
        },
        {
            "claim_id": "F005",
            "claim_ko": "기준일 재무상태와 유동성",
            "financial_dimension": "balance_sheet",
            "status": "conditional",
            "dart_anchor_summary_ko": financial_position_anchor,
            "caution_ko": "재무상태표는 시점 기준이며 손익·현금흐름 누적값과 같은 기간 지표로 혼용하지 않는다.",
            "action": "use_normally",
        },
    ]
    evidence = [
        {
            "evidence_id": "E001", "claim_id": "F001", "source": "DART",
            "metric_or_event": "revenue", "period": period_label, "value": revenue,
            "previous_value": previous_revenue,
            "period_basis": period_basis, "interpretation_ko": "현재 기간 매출 원값",
        },
        {
            "evidence_id": "E002", "claim_id": "F001", "source": "DART",
            "metric_or_event": "revenue growth", "period": comparison_key or period_label,
            "value": revenue_growth, "period_basis": period_basis,
            "interpretation_ko": "비교 기간과 함께 사용하는 매출 증감률",
        },
        {
            "evidence_id": "E003", "claim_id": "F002", "source": "DART",
            "metric_or_event": "contribution margin", "period": period_label,
            "value": contribution_margin, "previous_value": previous_contribution_margin,
            "period_basis": period_basis,
            "interpretation_ko": "현재 기간 공헌이익률 원값",
        },
        {
            "evidence_id": "E004", "claim_id": "F002", "source": "DART",
            "metric_or_event": "SG&A margin", "period": period_label, "value": sga_margin,
            "previous_value": previous_sga_margin,
            "period_basis": period_basis, "interpretation_ko": "현재 기간 판관비율 원값",
        },
        {
            "evidence_id": "E005", "claim_id": "F003", "source": "DART",
            "metric_or_event": "EPS", "period": period_label, "value": eps,
            "previous_value": previous_eps,
            "period_basis": period_basis, "interpretation_ko": "현재 기간 EPS 원값",
        },
        {
            "evidence_id": "E006", "claim_id": "F002", "source": "DART",
            "metric_or_event": "operating profit and margin", "period": period_label,
            "value": trend_values.get("operating_profit"),
            "previous_value": previous_trend_values.get("operating_profit"),
            "margin": normalized_current.get("operating_margin"),
            "previous_margin": normalized_previous.get("operating_margin"),
            "period_basis": period_basis, "interpretation_ko": "영업이익과 영업이익률 원값",
        },
        {
            "evidence_id": "E007", "claim_id": "F002", "source": "DART",
            "metric_or_event": "net income and margin", "period": period_label,
            "value": trend_values.get("net_income"),
            "previous_value": previous_trend_values.get("net_income"),
            "margin": normalized_current.get("net_margin"),
            "previous_margin": normalized_previous.get("net_margin"),
            "period_basis": period_basis, "interpretation_ko": "순이익과 순이익률 원값",
        },
        {
            "evidence_id": "E008", "claim_id": "F004", "source": "DART",
            "metric_or_event": "operating cash flow comparison", "period": period_label,
            "value": trend_values.get("operating_cash_flow"),
            "previous_value": previous_trend_values.get("operating_cash_flow"),
            "margin": normalized_current.get("operating_cash_flow_margin"),
            "previous_margin": normalized_previous.get("operating_cash_flow_margin"),
            "period_basis": period_basis, "interpretation_ko": "영업현금흐름과 매출 대비 비율 원값",
        },
        {
            "evidence_id": "E009", "claim_id": "F004", "source": "DART",
            "metric_or_event": "cash flow snapshot", "period": period_label,
            "value": {
                "operating_cash_flow": position["operating_cash_flow"],
                "investing_cash_flow": position["investing_cash_flow"],
                "financing_cash_flow": position["financing_cash_flow"],
                "net_cash_change": position["net_cash_change"],
            },
            "period_basis": period_basis, "interpretation_ko": "현금흐름표 원값 묶음",
        },
        {
            "evidence_id": "E010", "claim_id": "F005", "source": "DART",
            "metric_or_event": "balance sheet and liquidity snapshot",
            "period": current_period.get("period_end") or period_label,
            "value": {
                "total_assets": position["total_assets"],
                "total_liabilities": position["total_liabilities"],
                "total_equity": position["total_equity"],
                "current_ratio": position["current_ratio"],
                "cash_ratio": position["cash_ratio"],
                "debt_to_equity": position["debt_to_equity"],
            },
            "period_basis": "POINT_IN_TIME", "interpretation_ko": "재무상태표 원값 묶음",
        },
    ]

    def statement_view(stance: str, reasoning: str, key_features: List[str]) -> Dict[str, Any]:
        return {"stance": stance, "reasoning": reasoning, "key_features": key_features}

    def detailed_item(interpretation: str, features: Dict[str, Any], caution: str = "") -> Dict[str, Any]:
        item: Dict[str, Any] = {"interpretation": interpretation, "supporting_features": features}
        if caution:
            item["caution"] = caution
        return item

    report = {
        "agent_name": "Financial Analyst Agent",
        "role": "DART-based Financial Statement Analyst",
        "target_company": company_name,
        "ticker": ticker,
        "corp_code": corp_code,
        "as_of_date": as_of_date,
        "collection_context": collection_context,
        "financial_trends": financial_trends,
        "revenue_breakdown": revenue_breakdown,
        "share_information": share_information,
        "main_view": {
            "summary": "",
            "direction": "analysis_pending",
            "primary_basis": [
                f"매출 {krw_eok(revenue)}, 증감률 {pct(revenue_growth)}",
                (
                    f"영업이익률 {pct(normalized_current.get('operating_margin'))}, "
                    f"순이익률 {pct(normalized_current.get('net_margin'))}, "
                    f"공헌이익률 {pct(contribution_margin)}, 판관비율 {pct(sga_margin)}"
                ),
                f"EPS {won(eps)}",
                f"영업활동현금흐름 {krw_eok(position['operating_cash_flow'])}",
                f"유동비율 {pct1(position['current_ratio'])}, 부채비율 {pct1(position['debt_to_equity'])}",
            ],
            "main_cautions": [
                period_caution,
                "재무상태표는 시점 기준이고 손익·현금흐름은 누적 기간 기준이다.",
            ],
            "not_investment_decision": True,
        },
        "financial_statement_view": {
            "revenue_growth": statement_view("analysis_pending", revenue_anchor, ["revenue", "revenue_growth"]),
            "profitability": statement_view(
                "analysis_pending", profitability_anchor,
                ["contribution_margin", "operating_margin", "net_margin"],
            ),
            "cost_efficiency": statement_view("analysis_pending", profitability_anchor, ["sga_margin"]),
            "eps": statement_view("analysis_pending", eps_anchor, ["eps"]),
            "cash_flow": statement_view(
                "analysis_pending", financial_position_anchor,
                [
                    "operating_cash_flow", "previous_operating_cash_flow",
                    "operating_cash_flow_margin", "investing_cash_flow",
                    "financing_cash_flow", "net_cash_change",
                ],
            ),
            "balance_sheet": statement_view(
                "analysis_pending", financial_position_anchor,
                ["total_assets", "current_assets", "non_current_assets", "cash_and_cash_equivalents"],
            ),
            "capital_structure": statement_view(
                "analysis_pending", financial_position_anchor,
                ["total_equity", "total_liabilities", "equity_ratio", "debt_to_equity"],
            ),
            "debt": statement_view(
                "analysis_pending", financial_position_anchor,
                ["liabilities_to_assets", "debt_to_equity", "current_liabilities", "non_current_liabilities"],
            ),
            "liquidity": statement_view(
                "analysis_pending", financial_position_anchor,
                ["current_ratio", "cash_ratio", "current_assets", "current_liabilities"],
            ),
        },
        "detailed_analysis": {
            "revenue": detailed_item(
                "analysis_pending",
                {"revenue": revenue, "previous_revenue": previous_revenue, "revenue_growth": revenue_growth, "period": period_label},
                period_caution,
            ),
            "margin": detailed_item(
                "analysis_pending",
                {
                    "contribution_margin": contribution_margin,
                    "previous_contribution_margin": previous_contribution_margin,
                    "operating_profit": trend_values.get("operating_profit"),
                    "previous_operating_profit": previous_trend_values.get("operating_profit"),
                    "operating_margin": normalized_current.get("operating_margin"),
                    "previous_operating_margin": normalized_previous.get("operating_margin"),
                    "net_income": trend_values.get("net_income"),
                    "previous_net_income": previous_trend_values.get("net_income"),
                    "net_margin": normalized_current.get("net_margin"),
                    "previous_net_margin": normalized_previous.get("net_margin"),
                    "period": period_label,
                },
            ),
            "expense_efficiency": detailed_item(
                "analysis_pending",
                {"sga_margin": sga_margin, "previous_sga_margin": previous_sga_margin, "period": period_label},
            ),
            "eps": detailed_item(
                "analysis_pending",
                {"eps": eps, "previous_eps": previous_eps, "period": period_label},
                period_caution,
            ),
            "cash_flow": detailed_item(
                "analysis_pending",
                {
                    "operating_cash_flow": position["operating_cash_flow"],
                    "previous_operating_cash_flow": previous_trend_values.get("operating_cash_flow"),
                    "operating_cash_flow_margin": normalized_current.get("operating_cash_flow_margin"),
                    "previous_operating_cash_flow_margin": normalized_previous.get("operating_cash_flow_margin"),
                    "investing_cash_flow": position["investing_cash_flow"],
                    "financing_cash_flow": position["financing_cash_flow"],
                    "net_cash_change": position["net_cash_change"],
                    "period": period_label,
                },
                f"현금흐름표는 {period_basis} 기준이다.",
            ),
            "balance_sheet": detailed_item(
                "analysis_pending",
                {
                    "total_assets": position["total_assets"],
                    "current_assets": position["current_assets"],
                    "non_current_assets": position["non_current_assets"],
                    "cash_and_cash_equivalents": position["cash_and_cash_equivalents"],
                    "period_basis": "POINT_IN_TIME",
                },
            ),
            "capital_structure": detailed_item(
                "analysis_pending",
                {
                    "total_equity": position["total_equity"],
                    "total_liabilities": position["total_liabilities"],
                    "equity_ratio": position["equity_ratio"],
                    "debt_to_equity": position["debt_to_equity"],
                    "period_basis": "POINT_IN_TIME",
                },
            ),
            "debt": detailed_item(
                "analysis_pending",
                {
                    "total_liabilities": position["total_liabilities"],
                    "current_liabilities": position["current_liabilities"],
                    "non_current_liabilities": position["non_current_liabilities"],
                    "liabilities_to_assets": position["liabilities_to_assets"],
                    "period_basis": "POINT_IN_TIME",
                },
            ),
            "liquidity": detailed_item(
                "analysis_pending",
                {
                    "current_ratio": position["current_ratio"],
                    "cash_ratio": position["cash_ratio"],
                    "current_assets": position["current_assets"],
                    "current_liabilities": position["current_liabilities"],
                    "cash_and_cash_equivalents": position["cash_and_cash_equivalents"],
                    "period_basis": "POINT_IN_TIME",
                },
            ),
        },
        "secondary_context": build_financial_secondary_context(inputs),
        "strategy_handoff": {
            "financial_claims": claims,
            "key_evidence": evidence,
            "reconciliation_flags": [
                {"flag_ko": period_caution, "severity": "high", "action": "use_with_caution"}
            ],
        },
    }
    return report


def append_message(state: FinancialAnalystGraphState, node: str, role: str, content: str) -> None:
    state.setdefault("transcript", []).append({"node": node, "role": role, "content": content})


def report_claims(report: Dict[str, Any]) -> List[Dict[str, Any]]:
    return report.get("strategy_handoff", {}).get("financial_claims", report.get("financial_claims", []))


def input_state_node(state: FinancialAnalystGraphState) -> FinancialAnalystGraphState:
    manifest = load_json(state["manifest_path"])
    paths = manifest["input_paths"]
    dart_master_path = resolve_dart_master_path(paths)
    inputs = {
        "dart_main": load_input_file(paths["dart_main"]),
        "dart_master": load_input_file(str(dart_master_path)) if dart_master_path else {},
        "yfinance_market_summary": load_input_file(paths["yfinance_market_summary"])
        if paths.get("yfinance_market_summary") and Path(paths["yfinance_market_summary"]).exists()
        else {},
        "news_weekly_summaries": load_input_file(paths["news_weekly_summaries"])
        if paths.get("news_weekly_summaries") and Path(paths["news_weekly_summaries"]).exists()
        else {},
    }
    state["manifest"] = manifest
    state["inputs"] = inputs
    append_message(state, "Input State", "system", "financial_dart_inputs_loaded")
    return state


def financial_agent_execution_node(state: FinancialAnalystGraphState) -> FinancialAnalystGraphState:
    factual_report = build_financial_analyst_output(state["manifest"], state["inputs"])
    llm_analysis = generate_financial_analysis_with_llm(
        factual_report,
        model=state.get("model") or None,
    )
    state["llm_analysis"] = llm_analysis
    state["financial_analysis_output"] = apply_financial_analysis(
        factual_report,
        llm_analysis,
    )
    append_message(
        state,
        "Financial Agent Execution Node",
        "system",
        "financial_llm_analysis_built",
    )
    return state


def financial_report_output_node(state: FinancialAnalystGraphState) -> FinancialAnalystGraphState:
    output = dict(state["financial_analysis_output"])
    state["report_output"] = output
    required = [
        "agent_name",
        "role",
        "target_company",
        "ticker",
        "corp_code",
        "as_of_date",
        "collection_context",
        "financial_trends",
        "revenue_breakdown",
        "share_information",
        "main_view",
        "financial_statement_view",
        "detailed_analysis",
        "secondary_context",
        "secondary_context_assessment",
        "analysis_metadata",
        "strategy_handoff",
    ]
    missing = [key for key in required if key not in output]
    state["schema_validation"] = {
        "status": "pass" if not missing else "fail",
        "missing_keys": missing,
        "claim_count": len(report_claims(output)),
        "claim_count_limit": 10,
    }
    append_message(state, "Financial Report Output Node", "system", f"리포트 출력 생성 완료: {state['schema_validation']['status']}")
    return state


def build_graph():
    graph = StateGraph(FinancialAnalystGraphState)
    graph.add_node("input_state", input_state_node)
    graph.add_node("financial_agent_execution", financial_agent_execution_node)
    graph.add_node("financial_report_output", financial_report_output_node)

    graph.add_edge(START, "input_state")
    graph.add_edge("input_state", "financial_agent_execution")
    graph.add_edge("financial_agent_execution", "financial_report_output")
    graph.add_edge("financial_report_output", END)
    return graph.compile()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--manifest", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--trace-output")
    parser.add_argument("--model", default=None)
    args = parser.parse_args()

    app = build_graph()
    final_state = app.invoke(
        {
            "manifest_path": args.manifest,
            "model": args.model or "",
            "transcript": [],
        }
    )

    output_path = Path(args.output)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(final_state["report_output"], ensure_ascii=False, indent=2) + "\n")

    if args.trace_output:
        trace_path = Path(args.trace_output)
        trace_path.parent.mkdir(parents=True, exist_ok=True)
        trace = {
            "fixed_node_flow": [
                "Input State",
                "Financial Agent Execution Node",
                "Financial Report Output Node",
            ],
            "schema_validation": final_state["schema_validation"],
            "execution_mode": "deterministic_fact_preparation_plus_llm_interpretation",
            "llm_call_count": 1,
            "transcript": final_state["transcript"],
        }
        trace_path.write_text(json.dumps(trace, ensure_ascii=False, indent=2) + "\n")

    print(output_path)


if __name__ == "__main__":
    main()
