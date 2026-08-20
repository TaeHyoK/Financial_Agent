"""Calculate financial index metrics from canonical DART statement JSON.

This module consumes the canonical schema produced by ``handoff_builder``.
It does not fetch DART, call external services, or infer values with an LLM.
All metric extraction and calculation is deterministic and rule-based.
"""

from __future__ import annotations

import argparse
import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Literal

try:
    from . import AGENT_DIR, DEFAULT_OUTPUT_ROOT
except ImportError:  # pragma: no cover - supports direct script execution
    AGENT_DIR = Path(__file__).resolve().parent
    DEFAULT_OUTPUT_ROOT = AGENT_DIR.parents[2] / "Output_total" / "Financial"


Number = int | float
MetricUnit = Literal["원", "ratio", "원/주", "times"]

PERIOD_KEY_ORDER = (
    "current_fiscal_year",
    "same_period_previous_year",
    "ttm",
    "previous_fiscal_year",
    "previous_fiscal_year_2",
    "previous_fiscal_year_3",
)

DEFAULT_MASTER_INDEX_FILENAME = "dart_main.json"
DEFAULT_HANDOFF_INDEX_FILENAME = "dart_lightweight.json"
DEFAULT_OUTPUT_DIR = DEFAULT_OUTPUT_ROOT
DEFAULT_INDEX_PATH = AGENT_DIR / "financial_index.json"

_NOTE_REF_RE = re.compile(r"\((?:\s*주\s*)[^)]*\)")
_UNIT_REF_RE = re.compile(r"\(\s*단위\s*[:：]?\s*[^)]*\)")
_LOSS_RE = re.compile(r"\(\s*손실\s*\)")
_SPACES_RE = re.compile(r"[\s\u3000]+")


@dataclass(frozen=True)
class ItemLookupSpec:
    """Rule-based item lookup definition for a canonical DART statement."""

    statement_key: str
    item_keys: tuple[str, ...] = ()
    labels: tuple[str, ...] = ()


@dataclass(frozen=True)
class MetricSeries:
    """Numeric and display values found for one financial item."""

    numeric_by_period: dict[str, Number | None]
    source_value_by_period: dict[str, str | None]
    source_label_by_period: dict[str, str | None]


@dataclass(frozen=True)
class MetricDefinition:
    """Stable agent-facing metadata for one calculated metric."""

    metric_key: str
    display_name: str
    formula: str | None = None
    source_items: tuple[str, ...] = ()
    source_metric_keys: tuple[str, ...] = ()
    comparison_method: str | None = None


REVENUE_SPEC = ItemLookupSpec(
    statement_key="4-2",
    item_keys=("revenue",),
    labels=("매출액", "매출", "영업수익"),
)
COST_OF_OPERATIONS_SPEC = ItemLookupSpec(
    statement_key="4-2",
    item_keys=("cost_of_operations", "cost_of_revenue", "cost_of_sales"),
    labels=("매출원가", "영업비용", "매출비용"),
)
SGA_SPEC = ItemLookupSpec(
    statement_key="4-2",
    item_keys=("selling_general_and_administrative_expenses", "sga"),
    labels=("판매비와관리비", "판매비와 관리비"),
)
OPERATING_PROFIT_SPEC = ItemLookupSpec(
    statement_key="4-2",
    item_keys=("operating_profit",),
    labels=("영업이익", "영업이익(손실)"),
)
NET_INCOME_SPEC = ItemLookupSpec(
    statement_key="4-2",
    item_keys=("net_income",),
    labels=(
        "당기순이익",
        "당기순이익(손실)",
        "분기순이익",
        "분기순이익(손실)",
        "반기순이익",
        "반기순이익(손실)",
    ),
)
OPERATING_CASH_FLOW_SPEC = ItemLookupSpec(
    statement_key="4-4",
    item_keys=("cash_flows_from_operating_activities",),
    labels=("영업활동현금흐름", "영업활동으로 인한 현금흐름"),
)
TOTAL_EQUITY_SPEC = ItemLookupSpec(
    statement_key="4-1",
    item_keys=("total_equity",),
    labels=("자본총계", "자본합계"),
)
EPS_SPEC = ItemLookupSpec(
    statement_key="4-2",
    item_keys=("basic_eps", "eps"),
    labels=("기본주당이익", "기본주당이익(손실)"),
)
DEPRECIATION_AMORTIZATION_SPEC = ItemLookupSpec(
    statement_key="4-4",
    item_keys=(
        "depreciation_and_amortization",
        "depreciation",
        "amortization",
    ),
    labels=(
        "감가상각비",
        "무형자산상각비",
        "상각비",
        "감가상각비와무형자산상각비",
        "감가상각비 및 무형자산상각비",
    ),
)

METRIC_DEFINITIONS_BY_DISPLAY_NAME = {
    "Revenue": MetricDefinition(
        metric_key="revenue",
        display_name="Revenue",
        source_items=("매출액", "매출", "영업수익"),
    ),
    "Revenue Growth": MetricDefinition(
        metric_key="revenue_growth",
        display_name="Revenue Growth",
        formula="(current - previous) / previous",
        source_metric_keys=("revenue",),
        comparison_method="YoY",
    ),
    "Cost of Operations": MetricDefinition(
        metric_key="cost_of_operations",
        display_name="Cost of Operations",
        source_items=("매출원가", "영업비용", "매출비용"),
    ),
    "Contribution Profit": MetricDefinition(
        metric_key="contribution_profit",
        display_name="Contribution Profit",
        formula="Revenue - Cost of Operations",
        source_metric_keys=("revenue", "cost_of_operations"),
    ),
    "Contribution Margin": MetricDefinition(
        metric_key="contribution_margin",
        display_name="Contribution Margin",
        formula="Contribution Profit / Revenue",
        source_metric_keys=("contribution_profit", "revenue"),
    ),
    "SG&A": MetricDefinition(
        metric_key="sga",
        display_name="SG&A",
        source_items=("판매비와관리비", "판매비와 관리비"),
    ),
    "SG&A Margin": MetricDefinition(
        metric_key="sga_margin",
        display_name="SG&A Margin",
        formula="SG&A / Revenue",
        source_metric_keys=("sga", "revenue"),
    ),
    "Operating Profit": MetricDefinition(
        metric_key="operating_profit",
        display_name="Operating Profit",
        source_items=("영업이익", "영업이익(손실)"),
    ),
    "Net Income": MetricDefinition(
        metric_key="net_income",
        display_name="Net Income",
        source_items=("당기순이익", "분기순이익", "반기순이익"),
    ),
    "Operating Cash Flow": MetricDefinition(
        metric_key="operating_cash_flow",
        display_name="Operating Cash Flow",
        source_items=("영업활동현금흐름", "영업활동으로 인한 현금흐름"),
    ),
    "Total Equity": MetricDefinition(
        metric_key="total_equity",
        display_name="Total Equity",
        source_items=("자본총계", "자본합계"),
    ),
    "EPS": MetricDefinition(
        metric_key="eps",
        display_name="EPS",
        source_items=("기본주당이익", "기본주당이익(손실)"),
    ),
}
METRIC_DEFINITIONS_BY_KEY = {
    definition.metric_key: definition
    for definition in METRIC_DEFINITIONS_BY_DISPLAY_NAME.values()
}


def load_metric_order(index_path: str | Path) -> list[str]:
    """Load the requested metric output order from ``financial_index.json``."""

    path = Path(index_path)
    with path.open("r", encoding="utf-8") as file:
        payload = json.load(file)

    metric_order = payload.get("output_metrics_order")
    if not isinstance(metric_order, list) or not all(isinstance(item, str) for item in metric_order):
        raise ValueError(f"{path} must contain a string list at output_metrics_order")
    return metric_order


def calculate_financial_index(
    canonical_payload: dict[str, Any],
    metric_order: list[str],
    *,
    source_file: str | None = None,
    index_file: str | None = None,
) -> dict[str, Any]:
    """Calculate the requested metrics from one canonical DART JSON payload."""

    periods = _extract_periods(canonical_payload)
    ttm_period = _ttm_period_metadata(periods)
    if ttm_period:
        periods["ttm"] = ttm_period
    period_keys = [period_key for period_key in PERIOD_KEY_ORDER if period_key in periods]
    comparison_pairs = _comparison_pairs(periods, period_keys)
    context = _CalculationContext(
        payload=canonical_payload,
        periods=periods,
        period_keys=period_keys,
        comparison_pairs=comparison_pairs,
    )

    agent_metric_order: list[str] = []
    metrics_by_key: dict[str, Any] = {}
    for requested_metric in metric_order:
        definition = _metric_definition(requested_metric)
        calculated_metric = _calculate_metric(definition.display_name, context)
        agent_metric_order.append(definition.metric_key)
        metrics_by_key[definition.metric_key] = _agent_metric_payload(definition, calculated_metric)

    return {
        "schema_name": "dart_financial_index",
        "schema_version": "1.0",
        "source_file": source_file,
        "index_file": index_file,
        "unit": "원",
        "collection_context": canonical_payload.get("collection_context", {}),
        "revenue_breakdown": canonical_payload.get("revenue_breakdown", {}),
        "share_information": canonical_payload.get("share_information", {}),
        "periods": {period_key: periods[period_key] for period_key in period_keys},
        "comparison_pairs": comparison_pairs,
        "metric_order": agent_metric_order,
        "metrics_by_key": metrics_by_key,
    }


def calculate_financial_index_files(
    *,
    master_path: str | Path,
    handoff_path: str | Path,
    index_path: str | Path,
    output_dir: str | Path,
    master_output_name: str = DEFAULT_MASTER_INDEX_FILENAME,
    handoff_output_name: str = DEFAULT_HANDOFF_INDEX_FILENAME,
) -> tuple[Path, Path]:
    """Calculate and write financial index files for master and handoff inputs."""

    master_path = Path(master_path).expanduser().resolve()
    handoff_path = Path(handoff_path).expanduser().resolve()
    index_path = Path(index_path).expanduser().resolve()
    output_dir = Path(output_dir).expanduser().resolve()
    output_dir.mkdir(parents=True, exist_ok=True)

    metric_order = load_metric_order(index_path)
    master_payload = _load_json(master_path)
    handoff_payload = _load_json(handoff_path)

    master_result = calculate_financial_index(
        master_payload,
        metric_order,
        source_file=str(master_path),
        index_file=str(index_path),
    )
    handoff_result = calculate_financial_index(
        handoff_payload,
        metric_order,
        source_file=str(handoff_path),
        index_file=str(index_path),
    )

    master_output_path = output_dir / master_output_name
    handoff_output_path = output_dir / handoff_output_name
    _dump_json(master_output_path, master_result)
    _dump_json(handoff_output_path, handoff_result)
    return master_output_path, handoff_output_path


@dataclass(frozen=True)
class _CalculationContext:
    payload: dict[str, Any]
    periods: dict[str, dict[str, Any]]
    period_keys: list[str]
    comparison_pairs: list[dict[str, Any]]


def _metric_definition(requested_metric: str) -> MetricDefinition:
    if requested_metric in METRIC_DEFINITIONS_BY_DISPLAY_NAME:
        return METRIC_DEFINITIONS_BY_DISPLAY_NAME[requested_metric]
    if requested_metric in METRIC_DEFINITIONS_BY_KEY:
        return METRIC_DEFINITIONS_BY_KEY[requested_metric]
    return MetricDefinition(metric_key=_stable_metric_key(requested_metric), display_name=requested_metric)


def _agent_metric_payload(definition: MetricDefinition, calculated_metric: dict[str, Any]) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "metric_key": definition.metric_key,
        "display_name": definition.display_name,
        "metric_type": calculated_metric.get("metric_type"),
        "unit": calculated_metric.get("unit"),
        "formula": definition.formula,
        "source_metric_keys": list(definition.source_metric_keys),
        "source_items": list(definition.source_items),
    }
    if definition.comparison_method:
        payload["comparison_method"] = definition.comparison_method

    source_labels = _source_labels_observed(calculated_metric)
    if source_labels:
        payload["source_labels_observed"] = source_labels

    if calculated_metric.get("metric_type") == "comparison":
        payload["comparisons"] = calculated_metric.get("comparisons", {})
    else:
        payload["values_by_period"] = calculated_metric.get("values_by_period", {})
    return payload


def _calculate_metric(metric_name: str, context: _CalculationContext) -> dict[str, Any]:
    if metric_name == "Revenue":
        revenue = _find_item_series(context.payload, REVENUE_SPEC, context.period_keys)
        return _period_value_metric(metric_name, "원", revenue, context)

    if metric_name == "Revenue Growth":
        revenue = _find_item_series(context.payload, REVENUE_SPEC, context.period_keys)
        return _comparison_metric(metric_name, "ratio", revenue, context)

    if metric_name == "Cost of Operations":
        cost = _find_item_series(context.payload, COST_OF_OPERATIONS_SPEC, context.period_keys)
        return _period_value_metric(metric_name, "원", cost, context)

    if metric_name == "Contribution Profit":
        revenue = _find_item_series(context.payload, REVENUE_SPEC, context.period_keys)
        cost = _find_item_series(context.payload, COST_OF_OPERATIONS_SPEC, context.period_keys)
        contribution = _subtract_series(revenue, cost, context.period_keys)
        return _period_value_metric(metric_name, "원", contribution, context)

    if metric_name == "Contribution Margin":
        revenue = _find_item_series(context.payload, REVENUE_SPEC, context.period_keys)
        cost = _find_item_series(context.payload, COST_OF_OPERATIONS_SPEC, context.period_keys)
        contribution = _subtract_series(revenue, cost, context.period_keys)
        margin = _divide_series(contribution, revenue, context.period_keys)
        return _period_value_metric(metric_name, "ratio", margin, context)

    if metric_name == "SG&A":
        sga = _find_item_series(context.payload, SGA_SPEC, context.period_keys)
        return _period_value_metric(metric_name, "원", sga, context)

    if metric_name == "SG&A Margin":
        revenue = _find_item_series(context.payload, REVENUE_SPEC, context.period_keys)
        sga = _find_item_series(context.payload, SGA_SPEC, context.period_keys)
        margin = _divide_series(sga, revenue, context.period_keys)
        return _period_value_metric(metric_name, "ratio", margin, context)

    if metric_name == "EBITDA":
        ebitda = _ebitda_series(context)
        return _period_value_metric(metric_name, "원", ebitda, context)

    if metric_name == "EBITDA Margin":
        revenue = _find_item_series(context.payload, REVENUE_SPEC, context.period_keys)
        ebitda = _ebitda_series(context)
        margin = _divide_series(ebitda, revenue, context.period_keys)
        return _period_value_metric(metric_name, "ratio", margin, context)

    if metric_name == "Operating Profit":
        operating_profit = _find_item_series(context.payload, OPERATING_PROFIT_SPEC, context.period_keys)
        return _period_value_metric(metric_name, "원", operating_profit, context)

    if metric_name == "Net Income":
        net_income = _find_item_series(context.payload, NET_INCOME_SPEC, context.period_keys)
        return _period_value_metric(metric_name, "원", net_income, context)

    if metric_name == "Operating Cash Flow":
        operating_cash_flow = _find_item_series(
            context.payload,
            OPERATING_CASH_FLOW_SPEC,
            context.period_keys,
        )
        return _period_value_metric(metric_name, "원", operating_cash_flow, context)

    if metric_name == "Total Equity":
        total_equity = _find_item_series(
            context.payload,
            TOTAL_EQUITY_SPEC,
            context.period_keys,
            derive_ttm=False,
        )
        return _period_value_metric(metric_name, "원", total_equity, context)

    if metric_name == "EPS":
        eps = _find_item_series(context.payload, EPS_SPEC, context.period_keys, derive_ttm=False)
        return _period_value_metric(metric_name, "원/주", eps, context)

    if metric_name == "PE Ratio":
        return _unsupported_metric(
            metric_name,
            "times",
            context,
            reason="share_price_not_available_in_dart_financial_statement_json",
        )

    return _unsupported_metric(metric_name, "원", context, reason="metric_rule_not_configured")


def _period_value_metric(
    metric_name: str,
    unit: MetricUnit,
    series: MetricSeries,
    context: _CalculationContext,
) -> dict[str, Any]:
    return {
        "metric_name": metric_name,
        "metric_type": "period_value",
        "unit": unit,
        "values_by_period": {
            period_key: _period_value_payload(
                period_key,
                context.periods[period_key],
                series.numeric_by_period.get(period_key),
                series.source_value_by_period.get(period_key),
                series.source_label_by_period.get(period_key),
                unit,
            )
            for period_key in context.period_keys
        },
    }


def _comparison_metric(
    metric_name: str,
    unit: MetricUnit,
    series: MetricSeries,
    context: _CalculationContext,
) -> dict[str, Any]:
    comparisons: dict[str, Any] = {}
    for pair in context.comparison_pairs:
        current_key = pair["current_period_key"]
        previous_key = pair["previous_period_key"]
        current_value = series.numeric_by_period.get(current_key)
        previous_value = series.numeric_by_period.get(previous_key)
        comparison_key = pair["comparison_key"]
        if current_value is None or previous_value is None:
            comparisons[comparison_key] = {
                **pair,
                "value": None,
                "display_value": None,
                "status": "insufficient_data",
                "reason": "missing_current_or_previous_value",
            }
            continue
        if previous_value == 0:
            comparisons[comparison_key] = {
                **pair,
                "value": None,
                "display_value": None,
                "status": "insufficient_data",
                "reason": "previous_value_is_zero",
            }
            continue
        value = _round_ratio((float(current_value) - float(previous_value)) / float(previous_value))
        comparisons[comparison_key] = {
            **pair,
            "current_value": current_value,
            "previous_value": previous_value,
            "value": value,
            "display_value": _format_metric_value(value, unit),
            "status": "ok",
        }

    return {
        "metric_name": metric_name,
        "metric_type": "comparison",
        "unit": unit,
        "comparisons": comparisons,
    }


def _period_value_payload(
    period_key: str,
    period_meta: dict[str, Any],
    value: Number | None,
    source_value: str | None,
    source_label: str | None,
    unit: MetricUnit,
) -> dict[str, Any]:
    if value is None:
        return {
            "period_key": period_key,
            "period": period_meta,
            "value": None,
            "display_value": None,
            "source_value": source_value,
            "source_label": source_label,
            "status": "insufficient_data",
        }
    return {
        "period_key": period_key,
        "period": period_meta,
        "value": value,
        "display_value": _format_metric_value(value, unit),
        "source_value": source_value,
        "source_label": source_label,
        "status": "ok",
    }


def _unsupported_metric(
    metric_name: str,
    unit: MetricUnit,
    context: _CalculationContext,
    *,
    reason: str,
) -> dict[str, Any]:
    return {
        "metric_name": metric_name,
        "metric_type": "period_value",
        "unit": unit,
        "values_by_period": {
            period_key: {
                "period_key": period_key,
                "period": context.periods[period_key],
                "value": None,
                "display_value": None,
                "source_value": None,
                "source_label": None,
                "status": "insufficient_data",
                "reason": reason,
            }
            for period_key in context.period_keys
        },
    }


def _find_item_series(
    payload: dict[str, Any],
    spec: ItemLookupSpec,
    period_keys: Iterable[str],
    *,
    combine: Literal["first", "sum"] = "first",
    derive_ttm: bool = True,
) -> MetricSeries:
    numeric_by_period: dict[str, Number | None] = {period_key: None for period_key in period_keys}
    source_value_by_period: dict[str, str | None] = {period_key: None for period_key in period_keys}
    source_label_by_period: dict[str, str | None] = {period_key: None for period_key in period_keys}
    matched_any_by_period: dict[str, bool] = {period_key: False for period_key in period_keys}

    label_keys = {_label_key(label) for label in spec.labels}
    item_key_set = set(spec.item_keys)

    for table in payload.get(spec.statement_key, {}).get("tables", []):
        items_by_key = table.get("items_by_key", {})
        item_order = table.get("item_order") or list(items_by_key.keys())
        for item_key in item_order:
            item = items_by_key.get(item_key, {})
            if not _matches_item(item_key, item, item_key_set, label_keys):
                continue
            values, display_values = _item_period_values(item)
            display_name = str(item.get("display_name") or "")
            for period_key in period_keys:
                value = values.get(period_key)
                if value is None:
                    continue
                matched_any_by_period[period_key] = True
                if combine == "sum":
                    numeric_by_period[period_key] = (numeric_by_period[period_key] or 0) + value
                    source_value_by_period[period_key] = _append_source_value(
                        source_value_by_period[period_key],
                        display_values.get(period_key),
                    )
                    source_label_by_period[period_key] = _append_source_value(
                        source_label_by_period[period_key],
                        display_name,
                    )
                elif numeric_by_period[period_key] is None:
                    numeric_by_period[period_key] = value
                    source_value_by_period[period_key] = display_values.get(period_key)
                    source_label_by_period[period_key] = display_name

    if combine == "sum":
        for period_key, matched in matched_any_by_period.items():
            if not matched:
                numeric_by_period[period_key] = None
                source_value_by_period[period_key] = None
                source_label_by_period[period_key] = None

    if derive_ttm and "ttm" in numeric_by_period:
        current = numeric_by_period.get("current_fiscal_year")
        same_period = numeric_by_period.get("same_period_previous_year")
        annual = numeric_by_period.get("previous_fiscal_year")
        if current is not None and same_period is not None and annual is not None:
            numeric_by_period["ttm"] = annual + current - same_period
            source_value_by_period["ttm"] = f"{annual} + {current} - {same_period}"
            source_label_by_period["ttm"] = "FY + Current YTD - Prior-year Same YTD"

    return MetricSeries(
        numeric_by_period=numeric_by_period,
        source_value_by_period=source_value_by_period,
        source_label_by_period=source_label_by_period,
    )


def _matches_item(
    item_key: str,
    item: dict[str, Any],
    item_key_set: set[str],
    label_keys: set[str],
) -> bool:
    if item_key in item_key_set:
        return True
    labels = [str(item.get("display_name") or "")]
    labels.extend(str(alias) for alias in item.get("aliases", []))
    return any(_label_key(label) in label_keys for label in labels)


def _item_period_values(item: dict[str, Any]) -> tuple[dict[str, Number | None], dict[str, str | None]]:
    numeric_values = item.get("numeric_values_by_period_key")
    display_values = item.get("values_by_period_key")
    if isinstance(numeric_values, dict):
        return (
            {str(key): _coerce_number(value) for key, value in numeric_values.items()},
            {str(key): _coerce_display(display_values.get(key) if isinstance(display_values, dict) else None) for key in numeric_values},
        )

    values: dict[str, Number | None] = {}
    displays: dict[str, str | None] = {}
    if "current_numeric" in item:
        values["current_fiscal_year"] = _coerce_number(item.get("current_numeric"))
        displays["current_fiscal_year"] = _coerce_display(item.get("current_value"))
    if "previous_numeric" in item:
        values["previous_fiscal_year"] = _coerce_number(item.get("previous_numeric"))
        displays["previous_fiscal_year"] = _coerce_display(item.get("previous_value"))
    return values, displays


def _subtract_series(left: MetricSeries, right: MetricSeries, period_keys: Iterable[str]) -> MetricSeries:
    numeric_by_period: dict[str, Number | None] = {}
    source_value_by_period: dict[str, str | None] = {}
    source_label_by_period: dict[str, str | None] = {}
    for period_key in period_keys:
        left_value = left.numeric_by_period.get(period_key)
        right_value = right.numeric_by_period.get(period_key)
        if left_value is None or right_value is None:
            numeric_by_period[period_key] = None
            source_value_by_period[period_key] = None
            source_label_by_period[period_key] = None
            continue
        numeric_by_period[period_key] = left_value - right_value
        source_value_by_period[period_key] = f"{left_value} - {right_value}"
        source_label_by_period[period_key] = "Revenue - Cost of Operations"
    return MetricSeries(numeric_by_period, source_value_by_period, source_label_by_period)


def _divide_series(numerator: MetricSeries, denominator: MetricSeries, period_keys: Iterable[str]) -> MetricSeries:
    numeric_by_period: dict[str, Number | None] = {}
    source_value_by_period: dict[str, str | None] = {}
    source_label_by_period: dict[str, str | None] = {}
    for period_key in period_keys:
        top = numerator.numeric_by_period.get(period_key)
        bottom = denominator.numeric_by_period.get(period_key)
        if top is None or bottom in (None, 0):
            numeric_by_period[period_key] = None
            source_value_by_period[period_key] = None
            source_label_by_period[period_key] = None
            continue
        numeric_by_period[period_key] = _round_ratio(float(top) / float(bottom))
        source_value_by_period[period_key] = f"{top} / {bottom}"
        source_label_by_period[period_key] = "ratio"
    return MetricSeries(numeric_by_period, source_value_by_period, source_label_by_period)


def _ebitda_series(context: _CalculationContext) -> MetricSeries:
    operating_profit = _find_item_series(context.payload, OPERATING_PROFIT_SPEC, context.period_keys)
    depreciation_amortization = _find_item_series(
        context.payload,
        DEPRECIATION_AMORTIZATION_SPEC,
        context.period_keys,
        combine="sum",
    )

    numeric_by_period: dict[str, Number | None] = {}
    source_value_by_period: dict[str, str | None] = {}
    source_label_by_period: dict[str, str | None] = {}
    for period_key in context.period_keys:
        op_value = operating_profit.numeric_by_period.get(period_key)
        da_value = depreciation_amortization.numeric_by_period.get(period_key)
        if op_value is None or da_value is None:
            numeric_by_period[period_key] = None
            source_value_by_period[period_key] = None
            source_label_by_period[period_key] = None
            continue
        numeric_by_period[period_key] = op_value + da_value
        source_value_by_period[period_key] = f"{op_value} + {da_value}"
        source_label_by_period[period_key] = "Operating Profit + Depreciation/Amortization"
    return MetricSeries(numeric_by_period, source_value_by_period, source_label_by_period)


def _extract_periods(payload: dict[str, Any]) -> dict[str, dict[str, Any]]:
    for statement_key in ("4-2", "4-1", "4-4"):
        for table in payload.get(statement_key, {}).get("tables", []):
            periods = table.get("periods")
            if isinstance(periods, dict) and periods:
                return {
                    period_key: periods[period_key]
                    for period_key in PERIOD_KEY_ORDER
                    if period_key in periods and isinstance(periods[period_key], dict)
                }

    for table in payload.get("4-3", {}).get("tables", []):
        period_blocks = table.get("period_blocks")
        if isinstance(period_blocks, dict) and period_blocks:
            return {
                period_key: {
                    key: value
                    for key, value in period_blocks[period_key].items()
                    if key not in {"columns_by_key", "column_order", "rows_by_key", "row_order"}
                }
                for period_key in PERIOD_KEY_ORDER
                if period_key in period_blocks and isinstance(period_blocks[period_key], dict)
            }
    return {}


def _ttm_period_metadata(periods: dict[str, dict[str, Any]]) -> dict[str, Any] | None:
    current = periods.get("current_fiscal_year") or {}
    same_period = periods.get("same_period_previous_year") or {}
    annual = periods.get("previous_fiscal_year") or {}
    if not current or not same_period or not annual:
        return None
    if current.get("basis") != "YTD" or same_period.get("basis") != "YTD":
        return None
    if annual.get("basis") != "FULL_YEAR":
        return None
    if current.get("period_type") != same_period.get("period_type"):
        return None
    return {
        "label": f"TTM through {current.get('period_end') or 'current period'}",
        "fiscal_year": current.get("fiscal_year"),
        "period_type": "TTM",
        "period_end": current.get("period_end"),
        "basis": "TTM",
        "derivation": "previous_fiscal_year + current_fiscal_year - same_period_previous_year",
        "component_period_keys": [
            "previous_fiscal_year",
            "current_fiscal_year",
            "same_period_previous_year",
        ],
    }


def _comparison_pairs(periods: dict[str, dict[str, Any]], period_keys: list[str]) -> list[dict[str, Any]]:
    pairs: list[dict[str, Any]] = []
    candidate_pairs: list[tuple[str, str]] = []
    if "current_fiscal_year" in periods and "same_period_previous_year" in periods:
        candidate_pairs.append(("current_fiscal_year", "same_period_previous_year"))

    annual_keys = [
        key
        for key in ("previous_fiscal_year", "previous_fiscal_year_2", "previous_fiscal_year_3")
        if key in periods
    ]
    if periods.get("current_fiscal_year", {}).get("period_type") == "ANNUAL":
        annual_keys.insert(0, "current_fiscal_year")
    candidate_pairs.extend(zip(annual_keys, annual_keys[1:]))

    for current_key, previous_key in candidate_pairs:
        current_year = periods.get(current_key, {}).get("fiscal_year")
        previous_year = periods.get(previous_key, {}).get("fiscal_year")
        current_type = periods.get(current_key, {}).get("period_type")
        previous_type = periods.get(previous_key, {}).get("period_type")
        if current_year is not None and previous_year is not None and current_type and previous_type:
            comparison_key = f"{current_year}_{current_type}_vs_{previous_year}_{previous_type}"
        else:
            comparison_key = f"{current_key}_vs_{previous_key}"
        pairs.append(
            {
                "comparison_key": comparison_key,
                "current_period_key": current_key,
                "previous_period_key": previous_key,
                "current_fiscal_year": current_year,
                "previous_fiscal_year": previous_year,
                "current_basis": periods.get(current_key, {}).get("basis"),
                "previous_basis": periods.get(previous_key, {}).get("basis"),
            }
        )
    return pairs


def _source_labels_observed(calculated_metric: dict[str, Any]) -> list[str]:
    labels: list[str] = []
    for period_payload in calculated_metric.get("values_by_period", {}).values():
        label = period_payload.get("source_label")
        if label and label not in labels:
            labels.append(str(label))
    return labels


def _stable_metric_key(metric_name: str) -> str:
    text = re.sub(r"[^0-9A-Za-z가-힣]+", "_", metric_name.strip().lower())
    text = text.strip("_")
    return text or "unknown_metric"


def _label_key(label: str) -> str:
    value = str(label or "").strip()
    value = _NOTE_REF_RE.sub("", value)
    value = _UNIT_REF_RE.sub("", value)
    value = _LOSS_RE.sub("", value)
    value = value.replace("（", "(").replace("）", ")")
    value = _SPACES_RE.sub("", value)
    value = value.replace("ㆍ", "").replace("·", "")
    return value


def _coerce_number(value: Any) -> Number | None:
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, int | float):
        return value
    text = str(value).strip()
    if not text:
        return None
    try:
        if "." in text:
            return float(text.replace(",", ""))
        return int(text.replace(",", ""))
    except ValueError:
        return None


def _coerce_display(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value)
    return text if text != "" else None


def _append_source_value(existing: str | None, value: str | None) -> str | None:
    if not value:
        return existing
    if not existing:
        return value
    if value in existing.split(" + "):
        return existing
    return f"{existing} + {value}"


def _format_metric_value(value: Number, unit: MetricUnit) -> str:
    if unit == "ratio":
        return f"{round(float(value) * 100, 2):,.2f}%"
    if unit == "times":
        return f"{round(float(value), 2):,.2f}x"
    if isinstance(value, float) and not value.is_integer():
        return f"{value:,.4f}"
    return f"{int(value):,}"


def _round_ratio(value: float) -> float:
    return round(value, 10)


def _load_json(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as file:
        payload = json.load(file)
    if not isinstance(payload, dict):
        raise ValueError(f"{path} must contain a JSON object")
    return payload


def _dump_json(path: Path, payload: dict[str, Any]) -> None:
    with path.open("w", encoding="utf-8") as file:
        json.dump(payload, file, ensure_ascii=False, indent=2)
        file.write("\n")


def main() -> None:
    """CLI for calculating financial index files from existing DART outputs."""

    parser = argparse.ArgumentParser(description="Calculate financial index JSON from canonical DART outputs")
    parser.add_argument("--master", required=True, help="Path to dart_master.json")
    parser.add_argument("--handoff", required=True, help="Path to dart_2y_handoff.json")
    parser.add_argument("--index", default=str(DEFAULT_INDEX_PATH), help="Path to financial_index.json")
    parser.add_argument(
        "--output-dir",
        default=None,
        help="Directory for calculated financial index outputs. Defaults to the --master parent directory.",
    )
    parser.add_argument(
        "--master-output-name",
        default=DEFAULT_MASTER_INDEX_FILENAME,
        help=f"Output filename for master metrics. Default: {DEFAULT_MASTER_INDEX_FILENAME}",
    )
    parser.add_argument(
        "--handoff-output-name",
        default=DEFAULT_HANDOFF_INDEX_FILENAME,
        help=f"Output filename for handoff metrics. Default: {DEFAULT_HANDOFF_INDEX_FILENAME}",
    )
    args = parser.parse_args()

    master_output_path, handoff_output_path = calculate_financial_index_files(
        master_path=args.master,
        handoff_path=args.handoff,
        index_path=args.index,
        output_dir=args.output_dir or Path(args.master).expanduser().resolve().parent,
        master_output_name=args.master_output_name,
        handoff_output_name=args.handoff_output_name,
    )
    print(f"Wrote {master_output_path}")
    print(f"Wrote {handoff_output_path}")


if __name__ == "__main__":
    main()
