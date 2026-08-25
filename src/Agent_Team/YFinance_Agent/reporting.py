"""Analyst-style report generation for YFinance market outputs."""

from __future__ import annotations

import json
import math
import os
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

import pandas as pd
from shared.evidence_contracts import (
    SECONDARY_CONTEXT_EFFECTS,
    SECONDARY_CONTEXT_USAGE,
    canonical_evidence_id,
    validate_evidence_catalog,
    validate_secondary_context_assessments,
)
from shared.llm_clients import compact_json, execute_with_telemetry

from valuation import build_valuation_snapshot, unavailable_direct_valuation


AGENT_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = AGENT_DIR.parents[2]
DEFAULT_OUTPUT_DIR = PROJECT_ROOT / "Output_total" / "Y_Finance"
DEFAULT_MARKET_JSON = DEFAULT_OUTPUT_DIR / "market_full_dataset.json"
DEFAULT_DART_JSON: Path | None = None
DEFAULT_NEWS_JSON: Path | None = None
DEFAULT_REPORT_MD = DEFAULT_OUTPUT_DIR / "yfinance_analyst_report.md"
DEFAULT_REPORT_JSON = DEFAULT_OUTPUT_DIR / "yfinance_analyst_report.json"
DEFAULT_OPENAI_MODEL = "gpt-5.4-mini"
SECONDARY_FINANCIAL_METRICS = (
    "revenue",
    "revenue_growth",
    "contribution_margin",
    "sga_margin",
    "operating_profit",
    "net_income",
    "operating_cash_flow",
    "total_equity",
    "eps",
)


@dataclass(frozen=True)
class ReportPaths:
    """Paths written by the analyst report generator."""

    markdown: Path
    json: Path


def generate_analyst_report(
    *,
    market_json: Path,
    dart_json: Path,
    news_json: Path,
    report_md: Path,
    report_json: Path,
    valuation_json: Path | None = None,
    company_name: str | None = None,
    ticker: str | None = None,
    model: str | None = None,
    primary_data_only: bool = False,
) -> ReportPaths:
    """Create Markdown and JSON reports with YFinance as the primary dataset."""

    market = load_market_dataset(market_json)
    dart = {} if primary_data_only else _load_json(dart_json)
    news = {} if primary_data_only else _load_json(news_json)
    company = company_name or _news_company_name(news) or _infer_company_name(news_json) or "분석 대상 기업"

    market_summary = build_market_summary(market)
    market_date = datetime.strptime(market_summary["latest_snapshot"]["date"], "%Y-%m-%d").date()
    direct_valuation = (
        _load_json(valuation_json)
        if valuation_json is not None and valuation_json.exists()
        else unavailable_direct_valuation(
            ticker=ticker or "unknown",
            selected_date=market_date,
            reason="valuation_snapshot_file_not_available",
        )
    )
    valuation_snapshot = build_valuation_snapshot(
        market_summary=market_summary,
        dart_payload=dart,
        direct_valuation=direct_valuation,
    )
    monthly = build_monthly_market_table(market)
    primary_evidence_catalog = build_market_primary_evidence_catalog(market_summary)
    primary_evidence_catalog.update(build_daily_market_evidence_catalog(market))
    secondary_context = {
        "financial": build_dart_secondary_context(dart),
        "news": build_news_secondary_context(news, source_path=news_json),
    }

    payload = {
        "created_at": datetime.now().isoformat(timespec="seconds"),
        "company_name": company,
        "data_policy": {
            "primary_dataset": str(market_json),
            "valuation_dataset": str(valuation_json) if valuation_json is not None else None,
            "analysis_concept": (
                "시장 판단과 문맥은 YFinance 전용 데이터만 사용합니다."
                if primary_data_only
                else (
                    "시장 판단은 YFinance 근거만 사용하고, 뉴스 날짜별 요약과 DART 자료는 "
                    "표현 강도와 한계 점검용 보조 문맥으로만 사용합니다."
                )
            ),
            "primary_data_only": primary_data_only,
        },
        "market_summary": market_summary,
        "monthly_market_news": monthly,
        "valuation_snapshot": valuation_snapshot,
        "primary_evidence_catalog": primary_evidence_catalog,
        "secondary_context": secondary_context,
    }
    agent_report = generate_agent_json_report_with_llm(payload, ticker=ticker, model=model)
    selected_date = str(
        direct_valuation.get("selected_date")
        or valuation_snapshot.get("selected_date")
        or ""
    )
    if selected_date:
        agent_report["selected_date"] = selected_date
        agent_report["selected_date_policy"] = "before_market_open"
    agent_report["valuation_snapshot"] = valuation_snapshot
    agent_report["primary_evidence_catalog"] = primary_evidence_catalog
    agent_report["secondary_context_catalog"] = _combined_secondary_catalog(secondary_context)
    markdown = render_agent_markdown_report(agent_report)

    report_md.parent.mkdir(parents=True, exist_ok=True)
    report_json.parent.mkdir(parents=True, exist_ok=True)
    report_md.write_text(markdown, encoding="utf-8")
    with report_json.open("w", encoding="utf-8") as file:
        json.dump(_json_safe(agent_report), file, ensure_ascii=False, indent=2, allow_nan=False)
        file.write("\n")

    return ReportPaths(markdown=report_md, json=report_json)


def generate_agent_json_report_with_llm(
    payload: dict[str, Any],
    *,
    ticker: str | None = None,
    model: str | None = None,
) -> dict[str, Any]:
    """Call OpenAI to generate the final Y-Finance Agent JSON report."""

    try:
        from openai import OpenAI
    except ImportError as exc:
        raise RuntimeError(
            "Missing dependency: openai. Install dependencies with "
            f"`python -m pip install -r {AGENT_DIR / 'requirements.txt'}`."
        ) from exc

    if not os.getenv("OPENAI_API_KEY"):
        raise RuntimeError("OPENAI_API_KEY is required to generate the report with an LLM.")

    model_name = model or os.getenv("OPENAI_MODEL") or DEFAULT_OPENAI_MODEL
    evidence = build_llm_evidence_packet(payload, ticker=ticker)
    secondary_catalog = _combined_secondary_catalog(payload.get("secondary_context") or {})
    required_domains = sorted(
        domain
        for domain, context in (payload.get("secondary_context") or {}).items()
        if domain in {"financial", "news"}
        and isinstance(context, dict)
        and context.get("status") == "available"
    )
    secondary_ids_by_domain = {
        domain: sorted(
            evidence_id
            for evidence_id, item in secondary_catalog.items()
            if isinstance(item, dict) and item.get("domain") == domain
        )
        for domain in required_domains
    }
    client = OpenAI()
    request_payload = {
        "model": model_name,
        "input": [
            {
                "role": "system",
                "content": (
                    "You are Y-Finance Agent, a stock price and market data analyst. "
                    "Write in Korean. Use only primary_market_evidence for market conclusions. "
                    "Use financial and news secondary_context only to assess whether it corroborates, "
                    "contradicts, is neutral to, or is insufficient for a primary market observation. "
                    "Match each dated news summary to the same trading date or the next trading session, "
                    "and compare it with the dated daily market evidence. Describe temporal market response, not causality. "
                    "Secondary context is framing_and_limitation_only and cannot change primary evidence status. "
                    "Never claim that a news or financial item caused a price movement. "
                    "Do not infer operating performance, news impact, or accounting outcomes from price data. "
                    "Do not provide buy, sell, hold, target price, portfolio allocation, or personalized investment advice. "
                    "Do not include any score field. Return only JSON matching the schema."
                ),
            },
            {
                "role": "user",
                "content": compact_json(evidence),
            },
        ],
        "text": {
            "format": yfinance_agent_json_schema(
                primary_evidence_ids=sorted((payload.get("primary_evidence_catalog") or {}).keys()),
                secondary_evidence_ids_by_domain=secondary_ids_by_domain,
            )
        },
    }
    response = execute_with_telemetry(
        lambda: client.responses.create(**request_payload),
        request_payload=request_payload,
        model=model_name,
        step="yfinance:analyst_report",
        usage_getter=lambda result: getattr(result, "usage", None),
    )
    report = _parse_response_json(response)
    assessments_by_domain = report.pop("secondary_context_assessment_by_domain", None)
    if not isinstance(assessments_by_domain, dict):
        raise ValueError("YFinance output must contain secondary_context_assessment_by_domain.")
    report["secondary_context_assessment"] = [
        assessments_by_domain[domain]
        for domain in required_domains
        if isinstance(assessments_by_domain.get(domain), dict)
    ]
    _assert_required_report_keys(report)
    _assert_no_score(report)
    report["secondary_context_assessment"] = validate_secondary_context_assessments(
        report.get("secondary_context_assessment"),
        primary_evidence_ids=(payload.get("primary_evidence_catalog") or {}).keys(),
        secondary_catalog=secondary_catalog,
        allowed_source_domains={"financial", "news"},
        required_source_domains=required_domains,
    )
    return report


def build_llm_evidence_packet(payload: dict[str, Any], *, ticker: str | None = None) -> dict[str, Any]:
    """Build a compact primary-market plus secondary-context packet."""

    return {
        "target": {
            "target_company": payload["company_name"],
            "ticker": ticker or "unknown",
            "as_of_date": payload["market_summary"]["latest_snapshot"]["date"],
        },
        "primary_market_evidence": payload["primary_evidence_catalog"],
        "secondary_context": payload.get("secondary_context") or {},
        "context_contract": {
            "effects": sorted(SECONDARY_CONTEXT_EFFECTS),
            "usage": SECONDARY_CONTEXT_USAGE,
            "causal_assertions_allowed": False,
            "may_change_primary_evidence_status": False,
        },
    }


def build_market_primary_evidence_catalog(
    market_summary: dict[str, Any],
) -> dict[str, dict[str, Any]]:
    """Normalize raw/latest and deterministic period market observations."""

    latest = market_summary.get("latest_snapshot") or {}
    source_date = str(latest.get("date") or "")
    catalog: dict[str, dict[str, Any]] = {}
    for metric, value in latest.items():
        if metric == "date" or not _is_finite_number(value):
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
            "unit": _market_metric_unit(metric),
        }

    period = market_summary.get("period") or {}
    period_label = "..".join(
        value for value in (str(period.get("start") or ""), str(period.get("end") or "")) if value
    )
    period_metrics = {
        "stock_return": "stock_period_return",
        "kospi_return": "kospi_period_return",
        "stock_excess_vs_kospi": "stock_period_excess_return",
        "fx_return": "fx_period_return",
        "max_drawdown": "stock_max_drawdown",
    }
    performance = market_summary.get("period_performance") or {}
    for source_key, metric in period_metrics.items():
        value = performance.get(source_key)
        if not _is_finite_number(value):
            continue
        evidence_id = canonical_evidence_id("market", metric)
        catalog[evidence_id] = {
            "evidence_id": evidence_id,
            "domain": "market",
            "origin_type": "deterministic_derived",
            "source_ref": f"market_full_dataset.derived.{metric}",
            "source_date": source_date,
            "period": period_label,
            "metric": metric,
            "value": value,
            "unit": "ratio",
        }
    validate_evidence_catalog(catalog, allowed_domains={"market"})
    return catalog


def build_daily_market_evidence_catalog(
    frame: pd.DataFrame,
    *,
    max_rows: int = 30,
) -> dict[str, dict[str, Any]]:
    """Build dated market observations for matching News summaries to trading days."""

    data = frame.sort_values("date").copy()
    stock_price_column = _stock_analysis_price_column(data)
    data["_stock_daily_return"] = data[stock_price_column].pct_change(fill_method=None)
    data["_kospi_daily_return"] = data["kospi_close"].pct_change(fill_method=None)
    data["_fx_daily_return"] = data["fx_close"].pct_change(fill_method=None)
    data = data.tail(max(max_rows, 1))
    catalog: dict[str, dict[str, Any]] = {}
    for _, row in data.iterrows():
        source_date = _date_str(row["date"])
        stock_return = _number(row.get("_stock_daily_return"))
        kospi_return = _number(row.get("_kospi_daily_return"))
        value = {
            "stock_close": _number(row.get("stock_close")),
            "stock_daily_return": stock_return,
            "kospi_daily_return": kospi_return,
            "stock_excess_daily_return": (
                _number(stock_return - kospi_return)
                if stock_return is not None and kospi_return is not None
                else None
            ),
            "stock_volume_ratio_20": _number(row.get("stock_volume_ratio_20")),
            "fx_daily_return": _number(row.get("_fx_daily_return")),
        }
        evidence_id = canonical_evidence_id("market", f"daily_{source_date}")
        catalog[evidence_id] = {
            "evidence_id": evidence_id,
            "domain": "market",
            "origin_type": "deterministic_derived",
            "source_ref": f"market_full_dataset.daily.{source_date.replace('-', '_')}",
            "source_date": source_date,
            "period": source_date,
            "metric": "daily_market_snapshot",
            "value": value,
        }
    validate_evidence_catalog(catalog, allowed_domains={"market"})
    return catalog


def build_dart_secondary_context(payload: dict[str, Any]) -> dict[str, Any]:
    """Build a compact financial fact catalog without generated interpretation."""

    metrics = payload.get("metrics_by_key") or {}
    catalog: dict[str, dict[str, Any]] = {}
    for metric_key in SECONDARY_FINANCIAL_METRICS:
        metric = metrics.get(metric_key)
        if not isinstance(metric, dict):
            continue
        values = metric.get("values_by_period") or {}
        current = values.get("current_fiscal_year") or {}
        previous = values.get("same_period_previous_year") or {}
        comparison = _first_usable_comparison(metric.get("comparisons") or {})
        value = current.get("value")
        if not _is_finite_number(value) and comparison:
            value = comparison.get("value")
        if not _is_finite_number(value):
            continue
        current_period = current.get("period") if isinstance(current.get("period"), dict) else {}
        evidence_id = canonical_evidence_id("financial", metric_key)
        entry: dict[str, Any] = {
            "evidence_id": evidence_id,
            "domain": "financial",
            "origin_type": "deterministic_derived" if metric.get("metric_type") == "comparison" else "raw_source",
            "source_ref": f"dart_lightweight.metrics_by_key.{metric_key}",
            "source_date": str(current_period.get("period_end") or payload.get("as_of_date") or ""),
            "period": str(current_period.get("basis") or ""),
            "metric": metric_key,
            "value": value,
            "unit": metric.get("unit"),
        }
        if _is_finite_number(previous.get("value")):
            entry["previous_value"] = previous.get("value")
        if comparison and _is_finite_number(comparison.get("value")):
            entry["comparison_value"] = comparison.get("value")
            entry["comparison_basis"] = "_vs_".join(
                value
                for value in (
                    str(comparison.get("current_basis") or ""),
                    str(comparison.get("previous_basis") or ""),
                )
                if value
            )
        catalog[evidence_id] = entry
    validate_evidence_catalog(catalog, allowed_domains={"financial"})
    return {
        "status": "available" if catalog else "unavailable",
        "evidence_catalog": catalog,
    }


def build_news_secondary_context(
    payload: dict[str, Any],
    *,
    source_path: Path,
) -> dict[str, Any]:
    """Load the date-indexed News summaries directly, without News Agent claims."""

    del source_path  # Kept in the public signature for CLI compatibility.
    periods = _news_period_summary_items(payload)[:30]
    catalog: dict[str, dict[str, Any]] = {}
    for period in periods:
        source_date = str(period.get("period") or "")[:10]
        period_summary = str(period.get("period_summary") or "").strip()
        if not source_date or not period_summary:
            continue
        evidence_id = canonical_evidence_id("news", f"daily_{source_date}")
        catalog[evidence_id] = {
            "evidence_id": evidence_id,
            "domain": "news",
            "origin_type": "model_summarized",
            "source_ref": f"news_periods.{source_date.replace('-', '_')}",
            "source_date": source_date,
            "period": source_date,
            "metric": "daily_news_context",
            "text": period_summary,
            "issues": [
                {
                    key: issue.get(key)
                    for key in ("issue", "mention_count", "importance")
                    if issue.get(key) not in (None, "", [], {})
                }
                for issue in period.get("issues") or []
                if isinstance(issue, dict) and str(issue.get("issue") or "").strip()
            ],
            "source_event_ids": [
                str(event_id)
                for event_id in period.get("source_event_ids") or []
                if str(event_id).strip()
            ],
        }
    validate_evidence_catalog(catalog, allowed_domains={"news"})
    return {
        "status": "available" if catalog else "unavailable",
        "input_type": "daily_news_summaries",
        "period_count": len(catalog),
        "evidence_catalog": catalog,
    }


def _news_period_summary_items(payload: dict[str, Any]) -> list[dict[str, Any]]:
    output = payload.get("output") if isinstance(payload.get("output"), dict) else {}
    if isinstance(output.get("periods"), list):
        return [item for item in output["periods"] if isinstance(item, dict)]
    return [
        result["output"]
        for result in payload.get("period_results") or []
        if isinstance(result, dict)
        and result.get("status") in {None, "success"}
        and isinstance(result.get("output"), dict)
    ]


def _combined_secondary_catalog(
    contexts: dict[str, Any],
) -> dict[str, dict[str, Any]]:
    catalog: dict[str, dict[str, Any]] = {}
    for context in contexts.values():
        if not isinstance(context, dict):
            continue
        for evidence_id, evidence in (context.get("evidence_catalog") or {}).items():
            if evidence_id in catalog and catalog[evidence_id] != evidence:
                raise ValueError(f"Conflicting secondary evidence ID: {evidence_id}")
            catalog[evidence_id] = evidence
    validate_evidence_catalog(catalog, allowed_domains={"financial", "news"})
    return catalog


def _first_usable_comparison(comparisons: dict[str, Any]) -> dict[str, Any]:
    for comparison in comparisons.values():
        if isinstance(comparison, dict) and comparison.get("status") in {None, "ok"}:
            return comparison
    return {}


def _news_company_name(payload: dict[str, Any]) -> str | None:
    output = payload.get("output") if isinstance(payload.get("output"), dict) else payload
    company = output.get("company") if isinstance(output, dict) else None
    if isinstance(company, dict):
        name = str(company.get("company_name") or "").strip()
        if name:
            return name
    target = output.get("target_entity") if isinstance(output, dict) else None
    if isinstance(target, dict):
        name = str(target.get("company_name") or "").strip()
        return name or None
    return None


def _market_metric_unit(metric: str) -> str:
    if metric in {"stock_close", "kospi_close", "fx_close"}:
        return "price"
    if "rsi" in metric or "volume_ratio" in metric:
        return "index"
    if any(token in metric for token in ("return", "strength", "volatility", "drawdown", "to_ma", "obv", "bb_width")):
        return "ratio"
    return "number"


def _is_finite_number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(float(value))


def yfinance_agent_json_schema(
    *,
    primary_evidence_ids: list[str] | None = None,
    secondary_evidence_ids_by_domain: dict[str, list[str]] | None = None,
) -> dict[str, Any]:
    """Structured Outputs schema for the LLM-generated agent report."""

    str_array = {"type": "array", "items": {"type": "string"}}
    feature_value = {"anyOf": [{"type": "number"}, {"type": "string"}]}
    price_features = _features_schema(
        ["stock_close_to_ma20", "stock_close_to_ma60", "stock_ma5_to_ma20"],
        feature_value,
    )
    momentum_features = _features_schema(
        ["stock_rsi_14", "stock_macd_hist", "stock_macd_hist_change_1d"],
        feature_value,
    )
    volume_features = _features_schema(
        ["stock_bb_width_20", "stock_volatility_20", "stock_volume_ratio_20", "stock_obv_trend"],
        feature_value,
    )
    relative_features = _features_schema(
        ["stock_excess_return_5d", "stock_excess_return_20d", "stock_relative_strength_60"],
        feature_value,
    )
    fx_features = _features_schema(
        ["fx_return_20d", "fx_close_to_ma20", "fx_rsi_14", "fx_volatility_20"],
        feature_value,
    )
    report = {
        "type": "json_schema",
        "name": "yfinance_agent_report",
        "strict": True,
        "schema": {
            "type": "object",
            "properties": {
                "agent_name": {"type": "string"},
                "role": {"type": "string"},
                "target_company": {"type": "string"},
                "ticker": {"type": "string"},
                "as_of_date": {"type": "string"},
                "main_view": {
                    "type": "object",
                    "properties": {
                        "summary": {"type": "string"},
                        "direction": {"type": "string"},
                        "primary_basis": str_array,
                    },
                    "required": ["summary", "direction", "primary_basis"],
                    "additionalProperties": False,
                },
                "time_horizon_view": {
                    "type": "object",
                    "properties": {
                        "short_term": _horizon_schema(),
                        "mid_term": _horizon_schema(),
                        "long_term": {
                            "type": "object",
                            "properties": {
                                "stance": {"type": "string"},
                                "reasoning": {"type": "string"},
                                "key_features": str_array,
                                "data_limitation": {"type": "string"},
                            },
                            "required": ["stance", "reasoning", "key_features", "data_limitation"],
                            "additionalProperties": False,
                        },
                    },
                    "required": ["short_term", "mid_term", "long_term"],
                    "additionalProperties": False,
                },
                "detailed_analysis": {
                    "type": "object",
                    "properties": {
                        "price_trend": _analysis_schema(price_features),
                        "momentum": _analysis_schema(momentum_features),
                        "volatility_and_volume": _analysis_schema(volume_features),
                        "market_relative": _analysis_schema(relative_features),
                        "fx_context": {
                            "type": "object",
                            "properties": {
                                "interpretation": {"type": "string"},
                                "supporting_features": fx_features,
                                "caution": {"type": "string"},
                            },
                            "required": ["interpretation", "supporting_features", "caution"],
                            "additionalProperties": False,
                        },
                    },
                    "required": ["price_trend", "momentum", "volatility_and_volume", "market_relative", "fx_context"],
                    "additionalProperties": False,
                },
                "secondary_context_assessment_by_domain": {
                    "type": "object",
                    "properties": {
                        domain: _secondary_context_assessment_schema(
                            domain=domain,
                            primary_evidence_ids=primary_evidence_ids or [],
                            secondary_evidence_ids=evidence_ids,
                        )
                        for domain, evidence_ids in sorted(
                            (secondary_evidence_ids_by_domain or {}).items()
                        )
                    },
                    "required": sorted((secondary_evidence_ids_by_domain or {}).keys()),
                    "additionalProperties": False,
                },
            },
            "required": [
                "agent_name",
                "role",
                "target_company",
                "ticker",
                "as_of_date",
                "main_view",
                "time_horizon_view",
                "detailed_analysis",
                "secondary_context_assessment_by_domain",
            ],
            "additionalProperties": False,
        },
    }
    return report


def _features_schema(keys: list[str], value_schema: dict[str, Any]) -> dict[str, Any]:
    return {
        "type": "object",
        "properties": {key: value_schema for key in keys},
        "required": keys,
        "additionalProperties": False,
    }


def _analysis_schema(features_schema: dict[str, Any]) -> dict[str, Any]:
    return {
        "type": "object",
        "properties": {
            "interpretation": {"type": "string"},
            "supporting_features": features_schema,
        },
        "required": ["interpretation", "supporting_features"],
        "additionalProperties": False,
    }


def _horizon_schema() -> dict[str, Any]:
    return {
        "type": "object",
        "properties": {
            "stance": {"type": "string"},
            "reasoning": {"type": "string"},
            "key_features": {"type": "array", "items": {"type": "string"}},
        },
        "required": ["stance", "reasoning", "key_features"],
        "additionalProperties": False,
    }


def _secondary_context_assessment_schema(
    *,
    domain: str,
    primary_evidence_ids: list[str],
    secondary_evidence_ids: list[str],
) -> dict[str, Any]:
    primary_item: dict[str, Any] = {"type": "string"}
    if primary_evidence_ids:
        primary_item["enum"] = primary_evidence_ids
    secondary_item: dict[str, Any] = {"type": "string"}
    if secondary_evidence_ids:
        secondary_item["enum"] = secondary_evidence_ids
    return {
        "type": "object",
        "properties": {
            "context_id": {"type": "string", "enum": [f"{domain}_context"]},
            "source_domain": {"type": "string", "enum": [domain]},
            "effect": {"type": "string", "enum": sorted(SECONDARY_CONTEXT_EFFECTS)},
            "statement": {"type": "string"},
            "primary_evidence_ids": {"type": "array", "items": primary_item},
            "secondary_evidence_ids": {"type": "array", "items": secondary_item},
            "usage": {"type": "string", "enum": [SECONDARY_CONTEXT_USAGE]},
            "limitation": {"type": "string"},
        },
        "required": [
            "context_id",
            "source_domain",
            "effect",
            "statement",
            "primary_evidence_ids",
            "secondary_evidence_ids",
            "usage",
            "limitation",
        ],
        "additionalProperties": False,
    }


def _parse_response_json(response: Any) -> dict[str, Any]:
    text = getattr(response, "output_text", None)
    if not text:
        output = getattr(response, "output", None) or []
        fragments: list[str] = []
        for item in output:
            for content in getattr(item, "content", []) or []:
                fragment = getattr(content, "text", None)
                if fragment:
                    fragments.append(fragment)
        text = "".join(fragments)
    if not text:
        raise RuntimeError("OpenAI response did not contain output_text.")
    parsed = json.loads(text)
    if not isinstance(parsed, dict):
        raise RuntimeError("OpenAI response JSON must be an object.")
    return parsed


def _assert_no_score(value: Any) -> None:
    if isinstance(value, dict):
        for key, item in value.items():
            if key == "score":
                raise RuntimeError("LLM report included a forbidden score field.")
            _assert_no_score(item)
    elif isinstance(value, list):
        for item in value:
            _assert_no_score(item)


def _assert_required_report_keys(report: dict[str, Any]) -> None:
    required = {
        "agent_name",
        "role",
        "target_company",
        "ticker",
        "as_of_date",
        "main_view",
        "time_horizon_view",
        "detailed_analysis",
        "secondary_context_assessment",
    }
    missing = sorted(required - set(report))
    if missing:
        raise RuntimeError(f"LLM report is missing required keys: {', '.join(missing)}")


def render_agent_markdown_report(report: dict[str, Any]) -> str:
    """Render the final agent JSON report to Markdown."""

    lines = [
        f"# {report['target_company']} Y-Finance Agent Report",
        "",
        f"- Agent: {report['agent_name']}",
        f"- Role: {report['role']}",
        f"- Ticker: {report['ticker']}",
        f"- As of: {report['as_of_date']}",
        *([f"- Selected date: {report['selected_date']} (before market open)"] if report.get("selected_date") else []),
        "",
        "## Main View",
        "",
        report["main_view"]["summary"],
        "",
        f"- Direction: `{report['main_view']['direction']}`",
        *[f"- {item}" for item in report["main_view"]["primary_basis"]],
        "",
        "## Time Horizon View",
        "",
    ]
    for label, key in [("Short Term", "short_term"), ("Mid Term", "mid_term"), ("Long Term", "long_term")]:
        section = report["time_horizon_view"][key]
        lines.extend(
            [
                f"### {label}",
                "",
                f"- Stance: `{section['stance']}`",
                f"- Reasoning: {section['reasoning']}",
                f"- Key features: {', '.join(section['key_features'])}",
            ]
        )
        if section.get("data_limitation"):
            lines.append(f"- Data limitation: {section['data_limitation']}")
        lines.append("")

    lines.extend(["## Detailed Analysis", ""])
    for title, key in [
        ("Price Trend", "price_trend"),
        ("Momentum", "momentum"),
        ("Volatility And Volume", "volatility_and_volume"),
        ("Market Relative", "market_relative"),
        ("FX Context", "fx_context"),
    ]:
        section = report["detailed_analysis"][key]
        lines.extend([f"### {title}", "", section["interpretation"], ""])
        lines.append("| Feature | Value |")
        lines.append("|---|---:|")
        for feature, value in section["supporting_features"].items():
            lines.append(f"| `{feature}` | {_clean_cell(str(value))} |")
        if section.get("caution"):
            lines.extend(["", f"- Caution: {section['caution']}"])
        lines.append("")

    assessments = report.get("secondary_context_assessment") or []
    if assessments:
        lines.extend(["## Secondary Context Assessment", ""])
        for item in assessments:
            lines.append(
                f"- [{item.get('source_domain')}/{item.get('effect')}] {item.get('statement')}"
            )
            if item.get("limitation"):
                lines.append(f"  - Limitation: {item['limitation']}")
        lines.append("")

    return "\n".join(lines)


def load_market_dataset(path: Path) -> pd.DataFrame:
    """Load the records-oriented YFinance dataset."""

    data = _load_json(path)
    if not isinstance(data, list) or not data:
        raise ValueError(f"market_json must contain a non-empty list of records: {path}")

    frame = pd.DataFrame(data)
    if "date" not in frame.columns or "stock_close" not in frame.columns:
        raise ValueError("market_json must include at least date and stock_close columns.")

    frame = frame.copy()
    frame["date"] = pd.to_datetime(frame["date"], errors="coerce")
    frame = frame.dropna(subset=["date"]).sort_values("date").reset_index(drop=True)
    for column in frame.columns:
        if column != "date":
            frame[column] = pd.to_numeric(frame[column], errors="coerce")
    if frame.empty:
        raise ValueError(f"market_json contains no valid dated rows: {path}")
    return frame


def build_market_summary(frame: pd.DataFrame) -> dict[str, Any]:
    """Summarize the primary YFinance dataset."""

    start = frame.iloc[0]
    latest = frame.iloc[-1]
    stock_price_column = _stock_analysis_price_column(frame)
    stock_return = _safe_ratio(latest[stock_price_column], start[stock_price_column])
    kospi_return = _safe_ratio(latest.get("kospi_close"), start.get("kospi_close"))
    fx_return = _safe_ratio(latest.get("fx_close"), start.get("fx_close"))
    drawdown = _max_drawdown(frame[stock_price_column])
    high_idx = frame[stock_price_column].idxmax()
    low_idx = frame[stock_price_column].idxmin()

    latest_snapshot = {
        "date": _date_str(latest["date"]),
        "stock_close": _number(latest.get("stock_close")),
        "stock_adjusted_close": _number(latest.get("stock_adjusted_close")),
        "stock_return_5d": _number(latest.get("stock_return_5d")),
        "stock_return_20d": _number(latest.get("stock_return_20d")),
        "stock_return_60d": _number(latest.get("stock_return_60d")),
        "stock_close_to_ma20": _number(latest.get("stock_close_to_ma20")),
        "stock_close_to_ma60": _number(latest.get("stock_close_to_ma60")),
        "stock_ma5_to_ma20": _number(latest.get("stock_ma5_to_ma20")),
        "stock_rsi_14": _number(latest.get("stock_rsi_14")),
        "stock_macd_hist": _number(latest.get("stock_macd_hist")),
        "stock_macd_hist_change_1d": _number(latest.get("stock_macd_hist_change_1d")),
        "stock_bb_width_20": _number(latest.get("stock_bb_width_20")),
        "stock_volatility_20": _number(latest.get("stock_volatility_20")),
        "stock_volume_ratio_20": _number(latest.get("stock_volume_ratio_20")),
        "stock_obv_trend": _number(latest.get("stock_obv_trend")),
        "stock_excess_return_5d": _number(latest.get("stock_excess_return_5d")),
        "stock_excess_return_20d": _number(latest.get("stock_excess_return_20d")),
        "stock_relative_strength_60": _number(latest.get("stock_relative_strength_60")),
        "kospi_return_5d": _number(latest.get("kospi_return_5d")),
        "kospi_return_20d": _number(latest.get("kospi_return_20d")),
        "fx_return_20d": _number(latest.get("fx_return_20d")),
        "fx_close_to_ma20": _number(latest.get("fx_close_to_ma20")),
        "fx_rsi_14": _number(latest.get("fx_rsi_14")),
        "fx_volatility_20": _number(latest.get("fx_volatility_20")),
    }

    return {
        "period": {
            "start": _date_str(start["date"]),
            "end": _date_str(latest["date"]),
            "trading_rows": int(len(frame)),
        },
        "period_performance": {
            "stock_return": stock_return,
            "kospi_return": kospi_return,
            "stock_excess_vs_kospi": _number(stock_return - kospi_return) if stock_return is not None and kospi_return is not None else None,
            "fx_return": fx_return,
            "start_close": _number(start["stock_close"]),
            "end_close": _number(latest["stock_close"]),
            "start_adjusted_close": _number(start.get("stock_adjusted_close")),
            "end_adjusted_close": _number(latest.get("stock_adjusted_close")),
            "return_price_basis": "adjusted_close",
            "high_close": {
                "date": _date_str(frame.loc[high_idx, "date"]),
                "value": _number(frame.loc[high_idx, stock_price_column]),
                "price_basis": "adjusted_close",
            },
            "low_close": {
                "date": _date_str(frame.loc[low_idx, "date"]),
                "value": _number(frame.loc[low_idx, stock_price_column]),
                "price_basis": "adjusted_close",
            },
            "max_drawdown": drawdown,
        },
        "latest_snapshot": latest_snapshot,
        "signals": {
            "trend": _trend_label(latest_snapshot),
            "relative_strength": _relative_strength_label(latest_snapshot),
            "risk_volume": _risk_volume_label(latest_snapshot),
        },
    }


def build_monthly_market_table(frame: pd.DataFrame) -> list[dict[str, Any]]:
    """Aggregate YFinance records into a monthly table."""

    data = frame.copy()
    data["period"] = data["date"].dt.to_period("M").astype(str)
    rows: list[dict[str, Any]] = []
    for period, group in data.groupby("period", sort=True):
        first = group.iloc[0]
        last = group.iloc[-1]
        stock_price_column = _stock_analysis_price_column(group)
        stock_return = _safe_ratio(last.get(stock_price_column), first.get(stock_price_column))
        kospi_return = _safe_ratio(last.get("kospi_close"), first.get("kospi_close"))
        fx_return = _safe_ratio(last.get("fx_close"), first.get("fx_close"))
        rows.append(
            {
                "period": str(period),
                "start_date": _date_str(first["date"]),
                "end_date": _date_str(last["date"]),
                "stock_return": stock_return,
                "kospi_return": kospi_return,
                "stock_excess_vs_kospi": _number(stock_return - kospi_return) if stock_return is not None and kospi_return is not None else None,
                "fx_return": fx_return,
                "end_stock_close": _number(last.get("stock_close")),
                "end_stock_adjusted_close": _number(last.get("stock_adjusted_close")),
                "return_price_basis": "adjusted_close",
                "end_rsi_14": _number(last.get("stock_rsi_14")),
                "end_volume_ratio_20": _number(last.get("stock_volume_ratio_20")),
                "end_stock_return_20d": _number(last.get("stock_return_20d")),
            }
        )
    return rows


def _stock_analysis_price_column(frame: pd.DataFrame) -> str:
    if "stock_adjusted_close" in frame.columns and frame["stock_adjusted_close"].notna().any():
        return "stock_adjusted_close"
    return "stock_close"


def _trend_label(snapshot: dict[str, Any]) -> str:
    score = 0
    for key in ["stock_return_5d", "stock_return_20d", "stock_return_60d", "stock_close_to_ma20", "stock_close_to_ma60"]:
        value = snapshot.get(key)
        if value is not None:
            score += 1 if value > 0 else -1
    rsi = snapshot.get("stock_rsi_14")
    if rsi is not None and rsi > 70:
        return "과열권에 가까운 상승 추세"
    if score >= 3:
        return "상승 우위"
    if score <= -3:
        return "하락 우위"
    return "중립 혼조"


def _relative_strength_label(snapshot: dict[str, Any]) -> str:
    excess_20d = snapshot.get("stock_excess_return_20d")
    strength_60d = snapshot.get("stock_relative_strength_60")
    if excess_20d is not None and strength_60d is not None:
        if excess_20d > 0 and strength_60d > 0:
            return "단기와 중기 모두 시장 대비 우위"
        if excess_20d < 0 and strength_60d < 0:
            return "단기와 중기 모두 시장 대비 열위"
    return "상대성과 혼재"


def _risk_volume_label(snapshot: dict[str, Any]) -> str:
    volume = snapshot.get("stock_volume_ratio_20")
    volatility = snapshot.get("stock_volatility_20")
    if volume is not None and volume >= 1.5:
        return "거래량 확대 구간"
    if volatility is not None and volatility >= 0.03:
        return "변동성 확대 구간"
    return "거래량과 변동성은 통제 가능한 범위"


def _max_drawdown(close: pd.Series) -> float | None:
    close = pd.to_numeric(close, errors="coerce").dropna()
    if close.empty:
        return None
    running_high = close.cummax()
    drawdown = close / running_high - 1.0
    return _number(drawdown.min())


def _safe_ratio(current: Any, base: Any) -> float | None:
    current_num = _number(current)
    base_num = _number(base)
    if current_num is None or base_num in (None, 0):
        return None
    return _number(current_num / base_num - 1.0)


def _number(value: Any) -> float | None:
    if value is None:
        return None
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    if math.isnan(result) or math.isinf(result):
        return None
    return result


def _date_str(value: Any) -> str:
    if isinstance(value, pd.Timestamp):
        return value.strftime("%Y-%m-%d")
    return str(value)


def _clean_cell(value: str) -> str:
    return str(value).replace("|", "/").replace("\n", " ")


def _infer_company_name(path: Path) -> str | None:
    for part in path.parts:
        if "_" in part and any(char.isdigit() for char in part) and any("가" <= char <= "힣" for char in part):
            return part.split("_")[0] or None
    return None


def _load_json(path: Path) -> Any:
    with path.open("r", encoding="utf-8") as file:
        return json.load(file)


def _json_safe(value: Any) -> Any:
    if isinstance(value, dict):
        return {key: _json_safe(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_json_safe(item) for item in value]
    if isinstance(value, float):
        return value if math.isfinite(value) else None
    if isinstance(value, pd.Timestamp):
        return _date_str(value)
    return value
