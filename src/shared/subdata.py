"""Recipient-independent, source-linked subdata; no LLM interpretation here."""

from __future__ import annotations

import copy
import math
from typing import Any

from .evidence_contracts import canonical_evidence_id, validate_evidence_catalog

SUBDATA_VERSION = "annual_context_v1"
FINANCIAL_METRICS = (
    "revenue", "revenue_growth", "operating_profit", "operating_margin",
    "net_income", "operating_cash_flow", "total_equity", "debt_ratio",
)
MARKET_METRICS = tuple(
    f"{prefix}_{months}m"
    for prefix in ("stock_return", "kospi_return", "stock_excess_return")
    for months in (1, 3, 6, 12)
) + ("stock_volatility_1y", "stock_max_drawdown_1y", "stock_current_drawdown_1y", "stock_volume_ratio_5_60")


def evidence_catalog_for_llm(catalog: dict[str, Any]) -> dict[str, Any]:
    """Keep citation keys and observations; omit duplicated IDs and local lookup paths."""
    return {
        evidence_id: {
            key: copy.deepcopy(value)
            for key, value in row.items()
            if key != "source_ref" and not (key == "evidence_id" and value == evidence_id)
        }
        for evidence_id, row in catalog.items()
    }


def secondary_context_for_llm(contexts: dict[str, Any]) -> dict[str, Any]:
    """Project only the LLM-facing copy; provenance remains in stored source packets."""
    result = copy.deepcopy(contexts)
    for context in result.values():
        context.pop("version", None)
        for period in (context.get("periods") or {}).values():
            period.pop("receipt_no", None)
        if context.get("input_type") == "monthly_news_articles":
            from .news_articles import articles_for_llm
            context["evidence_catalog"] = articles_for_llm(context["evidence_catalog"])
            continue
        if "evidence_catalog" in context:
            context["evidence_catalog"] = evidence_catalog_for_llm(context["evidence_catalog"])
            for row in context["evidence_catalog"].values():
                if row.get("metric") == "monthly_news_context":
                    row.pop("source_event_count", None)
    return result


def finite(value: Any) -> bool:
    return isinstance(value, (float, int)) and not isinstance(value, bool) and math.isfinite(value)


def _context(domain: str, kind: str, catalog: dict, **metadata: Any) -> dict:
    validate_evidence_catalog(catalog, allowed_domains={domain})
    return {"version": SUBDATA_VERSION, "status": "available" if catalog else "unavailable",
            "input_type": kind, **metadata, "evidence_catalog": catalog}


def financial_subdata(payload: dict[str, Any]) -> dict[str, Any]:
    """Eight rows, three distinct fiscal years and the latest interim comparison."""
    periods = payload.get("periods") or {}
    metrics = payload.get("metrics_by_key") or {}
    annual = sorted(
        [k for k, p in periods.items() if k != "ttm" and p.get("basis") in {"FY", "FULL_YEAR"}],
        key=lambda k: str(periods[k].get("period_end") or ""), reverse=True,
    )
    seen: set[str] = set()
    selected = []
    for key in annual:
        end = str(periods[key].get("period_end") or "")
        if end not in seen and len(selected) < 3:
            selected.append(key)
            seen.add(end)
    selected.reverse()
    for key in ("same_period_previous_year", "current_fiscal_year"):
        if key in periods and str(periods[key].get("period_end") or "") not in seen:
            selected.append(key)
            seen.add(str(periods[key].get("period_end") or ""))
    compact_periods = {
        k: {name: periods[k].get(name) for name in
            ("label", "period_end", "basis", "receipt_date", "receipt_no")}
        for k in selected
    }
    catalog = {}
    for key in FINANCIAL_METRICS:
        metric = metrics.get(key) or {}
        values = metric.get("values_by_period") or {}
        unit = "%" if key in {"revenue_growth", "operating_margin", "debt_ratio"} else "억원"
        scale = 1e-8 if unit == "억원" else 100.0
        history = {}
        statuses = {}
        for period in selected:
            item = values.get(period) or {}
            value = item.get("value")
            if key == "revenue_growth":
                comparison = next((c for c in (metric.get("comparisons") or {}).values()
                                   if c.get("current_period_key") == period), {})
                value = comparison.get("value")
                item = comparison
            history[period] = round(value * scale, 4) if finite(value) else None
            if not finite(value):
                statuses[period] = item.get("reason") or "unavailable"
        if not any(value is not None for value in history.values()):
            # Keep an explicit missing row when the source itself exists.
            if not periods:
                continue
        evidence_id = canonical_evidence_id("financial", key)
        catalog[evidence_id] = {
            "evidence_id": evidence_id, "domain": "financial",
            "origin_type": "deterministic_derived",
            "source_ref": f"dart_lightweight.metrics_by_key.{key}",
            "source_date": str((periods.get("current_fiscal_year") or {}).get("receipt_date") or ""),
            "metric": key, "unit": unit, "values_by_period": history,
            "value": history.get("current_fiscal_year"),
            "missing_reasons": statuses,
        }
    return _context("financial", "financial_trend_table", catalog, periods=compact_periods,
                    statement_scope=(payload.get("collection_context") or {}).get("statement_scope", "unknown"),
                    comparison_policy="FULL_YEAR vs prior FULL_YEAR; interim YTD vs same prior-year YTD; balance-sheet values are period-end")


def market_subdata(payload: Any) -> dict[str, Any]:
    row = payload[0] if isinstance(payload, list) and payload else payload
    row = row if isinstance(row, dict) else {}
    catalog = {}
    for key in MARKET_METRICS:
        value = row.get(key)
        if not row:
            continue
        unit = "times" if key == "stock_volume_ratio_5_60" else "percentage_points" if "excess" in key else "%"
        evidence_id = canonical_evidence_id("market", key)
        catalog[evidence_id] = {
            "evidence_id": evidence_id, "domain": "market", "origin_type": "deterministic_derived",
            "source_ref": f"market_full_dataset.latest.{key}", "source_date": str(row.get("date") or ""),
            "metric": key, "unit": unit,
            "value": round(value * (1 if unit == "times" else 100), 4) if finite(value) else None,
            "status": "available" if finite(value) else "insufficient_history",
        }
    return _context("market", "market_indicator_table", catalog,
                    as_of_date=row.get("date"), latest_close=row.get("stock_close"),
                    benchmark="KOSPI", price_basis="provider split-adjusted close; cash dividends excluded",
                    volatility_basis="sample daily standard deviation times sqrt(252)",
                    drawdown_convention="negative fraction below previous high, displayed as percent")


def news_subdata(payload: dict[str, Any]) -> dict[str, Any]:
    from .news_articles import ARTICLE_NEWS_POLICY, article_catalog
    if (payload.get("metadata") or {}).get("raw_news_policy") == ARTICLE_NEWS_POLICY:
        return _context("news", "monthly_news_articles", article_catalog(payload),
                        article_periods=[row["period"] for row in payload["periods"]])

    output = payload.get("output") or {}
    periods = output.get("periods") or [
        item.get("output") for item in payload.get("period_results") or []
        if isinstance(item, dict) and item.get("status") in (None, "success")
    ]
    catalog = {}
    for item in periods:
        if not isinstance(item, dict):
            continue
        period = str(item.get("period") or "")
        # New summaries contain independent, source-linked issues, not a second
        # unreferenced monthly narrative. Preserve each issue as its own context.
        linked_issues = [issue for issue in item.get("issues") or []
                         if isinstance(issue, dict) and str(issue.get("summary") or "").strip()]
        if period and linked_issues:
            for index, issue in enumerate(linked_issues, 1):
                evidence_id = canonical_evidence_id("news", f"period_{period}_issue_{index}")
                catalog[evidence_id] = {
                    "evidence_id": evidence_id, "domain": "news", "origin_type": "model_summarized",
                    "source_ref": f"news_periods.{period}.issues.{index - 1}",
                    "source_date": item.get("period_end") or period.split("/")[-1],
                    "period": period, "period_start": item.get("period_start"),
                    "period_end": item.get("period_end"), "metric": "monthly_news_context",
                    "text": issue["summary"], "source_event_ids": list(issue.get("source_event_ids") or []),
                }
            continue
        summary = str(item.get("period_summary") or "").strip()
        if not period or not summary:
            continue
        evidence_id = canonical_evidence_id("news", f"period_{period}")
        catalog[evidence_id] = {
            "evidence_id": evidence_id, "domain": "news", "origin_type": "model_summarized",
            "source_ref": f"news_periods.{period.replace('-', '_')}",
            "source_date": item.get("period_end") or period.split("/")[-1],
            "period": period, "period_start": item.get("period_start"), "period_end": item.get("period_end"),
            "metric": "monthly_news_context", "text": summary,
            "source_event_count": len(item.get("source_event_ids") or []),
        }
    return _context("news", "monthly_news_summaries", catalog,
                    period_count=len({row.get("period") for row in periods if isinstance(row, dict) and row.get("period")}))
