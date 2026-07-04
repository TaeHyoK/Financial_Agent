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


AGENT_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = AGENT_DIR.parents[2]
DEFAULT_OUTPUT_DIR = PROJECT_ROOT / "Output_total" / "Y_Finance"
DEFAULT_MARKET_JSON = DEFAULT_OUTPUT_DIR / "market_full_dataset.json"
DEFAULT_DART_JSON: Path | None = None
DEFAULT_NEWS_JSON: Path | None = None
DEFAULT_REPORT_MD = DEFAULT_OUTPUT_DIR / "yfinance_analyst_report.md"
DEFAULT_REPORT_JSON = DEFAULT_OUTPUT_DIR / "yfinance_analyst_report.json"
DEFAULT_OPENAI_MODEL = "gpt-5.4-mini"


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
    company_name: str | None = None,
    ticker: str | None = None,
    model: str | None = None,
) -> ReportPaths:
    """Create Markdown and JSON reports with YFinance as the primary dataset."""

    market = load_market_dataset(market_json)
    dart = _load_json(dart_json)
    news = _load_json(news_json)
    company = company_name or _infer_company_name(news_json) or "분석 대상 기업"

    market_summary = build_market_summary(market)
    monthly = build_monthly_market_table(market)
    news_periods = extract_news_periods(news)
    dart_snapshot = extract_dart_snapshot(dart)
    cross_analysis = build_cross_analysis(
        monthly=monthly,
        news_periods=news_periods,
        dart_snapshot=dart_snapshot,
        market=market,
    )

    payload = {
        "created_at": datetime.now().isoformat(timespec="seconds"),
        "company_name": company,
        "data_policy": {
            "primary_dataset": str(market_json),
            "supporting_datasets": [str(news_json), str(dart_json)],
            "analysis_concept": "YFinance 시장 데이터를 중심으로 분석하고 뉴스와 DART는 교차 검증 및 해석 보조 자료로 사용합니다.",
        },
        "market_summary": market_summary,
        "monthly_market_news": monthly,
        "dart_snapshot": dart_snapshot,
        "cross_analysis": cross_analysis,
    }
    agent_report = generate_agent_json_report_with_llm(payload, ticker=ticker, model=model)
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
    client = OpenAI()
    response = client.responses.create(
        model=model_name,
        input=[
            {
                "role": "system",
                "content": (
                    "You are Y-Finance Agent, a stock price and market data analyst. "
                    "Write in Korean. Use YFinance market data as the primary dataset and use News/DART only as supporting context. "
                    "Make cross_data_reconciliation deep and concrete: include news_plus_market, dart_plus_market, "
                    "and news_plus_dart_plus_market. Each section must include a summary, reaction_points, and divergences. "
                    "Do not provide buy, sell, hold, target price, portfolio allocation, or personalized investment advice. "
                    "Do not include any score field. Return only JSON matching the schema."
                ),
            },
            {
                "role": "user",
                "content": json.dumps(evidence, ensure_ascii=False, allow_nan=False),
            },
        ],
        text={"format": yfinance_agent_json_schema()},
    )
    report = _parse_response_json(response)
    _assert_no_score(report)
    return report


def build_llm_evidence_packet(payload: dict[str, Any], *, ticker: str | None = None) -> dict[str, Any]:
    """Build compact evidence for the LLM from the three source datasets."""

    cross = payload["cross_analysis"]
    monthly_with_news = cross["monthly_market_news"]
    return {
        "task": "Create a Y-Finance Agent report in the requested JSON schema, then the app will render Markdown from that JSON.",
        "hard_constraints": [
            "Use YFinance market data as the primary evidence.",
            "Use News and DART only for cross-data reconciliation and supporting context.",
            "For cross_data_reconciliation, write three sections: news_plus_market, dart_plus_market, news_plus_dart_plus_market.",
            "In each cross section, include multiple reaction_points and at least one divergence when evidence supports a mismatch.",
            "Do not include score anywhere.",
            "Do not make buy/sell/hold recommendations.",
            "Do not invent figures not present in the evidence.",
        ],
        "agent_metadata": {
            "agent_name": "Y-Finance Agent",
            "role": "Stock Price / Market Data Analyst",
            "target_company": payload["company_name"],
            "ticker": ticker or "unknown",
            "as_of_date": payload["market_summary"]["latest_snapshot"]["date"],
        },
        "source_paths": payload["data_policy"],
        "primary_yfinance_evidence": {
            "market_summary": payload["market_summary"],
            "monthly_market_table": payload["monthly_market_news"],
            "monthly_market_news_joined": monthly_with_news,
        },
        "supporting_news_evidence": {
            "best_month_with_news": cross["best_month_with_news"],
            "worst_month_with_news": cross["worst_month_with_news"],
            "recent_months": monthly_with_news[-4:],
        },
        "supporting_dart_evidence": {
            "dart_snapshot": payload["dart_snapshot"],
            "dart_market_bridge": cross["dart_market_bridge"],
        },
    }


def yfinance_agent_json_schema() -> dict[str, Any]:
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
    return {
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
                "cross_data_reconciliation": {
                    "type": "object",
                    "properties": {
                        "news_plus_market": _cross_pair_schema(),
                        "dart_plus_market": _cross_pair_schema(),
                        "news_plus_dart_plus_market": _cross_pair_schema(),
                    },
                    "required": ["news_plus_market", "dart_plus_market", "news_plus_dart_plus_market"],
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
                "cross_data_reconciliation",
            ],
            "additionalProperties": False,
        },
    }


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


def _cross_pair_schema() -> dict[str, Any]:
    point_schema = {
        "type": "object",
        "properties": {
            "point": {"type": "string"},
            "cross_analysis": {"type": "string"},
            "reaction_interpretation": {"type": "string"},
        },
        "required": ["point", "cross_analysis", "reaction_interpretation"],
        "additionalProperties": False,
    }
    return {
        "type": "object",
        "properties": {
            "summary": {"type": "string"},
            "reaction_points": {"type": "array", "items": point_schema},
            "divergences": {"type": "array", "items": point_schema},
        },
        "required": ["summary", "reaction_points", "divergences"],
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


def render_agent_markdown_report(report: dict[str, Any]) -> str:
    """Render the final agent JSON report to Markdown."""

    lines = [
        f"# {report['target_company']} Y-Finance Agent Report",
        "",
        f"- Agent: {report['agent_name']}",
        f"- Role: {report['role']}",
        f"- Ticker: {report['ticker']}",
        f"- As of: {report['as_of_date']}",
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

    reconciliation = report["cross_data_reconciliation"]
    lines.extend(["## Cross Data Reconciliation", ""])
    for title, key in [
        ("News Plus Market", "news_plus_market"),
        ("DART Plus Market", "dart_plus_market"),
        ("News Plus DART Plus Market", "news_plus_dart_plus_market"),
    ]:
        section = reconciliation[key]
        lines.extend([f"### {title}", "", section["summary"], ""])
        lines.extend(["#### Reaction Points", ""])
        for item in section["reaction_points"]:
            lines.extend(
                [
                    f"- **{item['point']}**",
                    f"  - Cross analysis: {item['cross_analysis']}",
                    f"  - Reaction interpretation: {item['reaction_interpretation']}",
                ]
            )
        lines.extend(["", "#### Divergences", ""])
        if section["divergences"]:
            for item in section["divergences"]:
                lines.extend(
                    [
                        f"- **{item['point']}**",
                        f"  - Cross analysis: {item['cross_analysis']}",
                        f"  - Reaction interpretation: {item['reaction_interpretation']}",
                    ]
                )
        else:
            lines.append("- 명시적 괴리 없음")
        lines.append("")
    return "\n".join(lines)


def build_agent_json_report(payload: dict[str, Any], *, ticker: str | None = None) -> dict[str, Any]:
    """Render the structured analysis payload as the Y-Finance Agent JSON schema."""

    market = payload["market_summary"]
    latest = market["latest_snapshot"]
    performance = market["period_performance"]
    cross = payload["cross_analysis"]
    dart_metrics = {item["metric_key"]: item for item in payload["dart_snapshot"]["metrics"]}
    news_alignment = _news_alignment(cross)
    dart_alignment = _dart_alignment(cross)

    return {
        "agent_name": "Y-Finance Agent",
        "role": "Stock Price / Market Data Analyst",
        "target_company": payload["company_name"],
        "ticker": ticker or "unknown",
        "as_of_date": latest["date"],
        "main_view": {
            "summary": _main_summary(latest),
            "direction": _direction(latest),
            "primary_basis": _primary_basis(latest),
        },
        "time_horizon_view": {
            "short_term": {
                "stance": _short_term_stance(latest),
                "reasoning": _short_term_reasoning(latest),
                "key_features": [
                    "stock_return_5d",
                    "stock_excess_return_5d",
                    "stock_rsi_14",
                    "stock_macd_hist_change_1d",
                ],
            },
            "mid_term": {
                "stance": _mid_term_stance(latest),
                "reasoning": _mid_term_reasoning(latest),
                "key_features": [
                    "stock_return_20d",
                    "stock_excess_return_20d",
                    "stock_close_to_ma20",
                    "stock_close_to_ma60",
                ],
            },
            "long_term": {
                "stance": _long_term_stance(latest),
                "reasoning": _long_term_reasoning(latest, performance),
                "key_features": [
                    "stock_return_60d",
                    "stock_relative_strength_60",
                    "max_drawdown",
                ],
                "data_limitation": "현재 YFinance 전용 데이터에는 MA120/MA240 같은 장기 이동평균 지표가 없어 장기 추세 판단은 60일 수익률, 60일 상대강도, 기간 최대낙폭 중심으로 제한적으로 해석한다.",
            },
        },
        "detailed_analysis": {
            "price_trend": {
                "interpretation": _price_trend_interpretation(latest),
                "supporting_features": {
                    "stock_close_to_ma20": _pct_number(latest.get("stock_close_to_ma20")),
                    "stock_close_to_ma60": _pct_number(latest.get("stock_close_to_ma60")),
                    "stock_ma5_to_ma20": _pct_number(latest.get("stock_ma5_to_ma20")),
                },
            },
            "momentum": {
                "interpretation": _momentum_interpretation(latest),
                "supporting_features": {
                    "stock_rsi_14": _round_number(latest.get("stock_rsi_14"), 1),
                    "stock_macd_hist": _round_number(latest.get("stock_macd_hist"), 2),
                    "stock_macd_hist_change_1d": _round_number(latest.get("stock_macd_hist_change_1d"), 2),
                },
            },
            "volatility_and_volume": {
                "interpretation": _volatility_volume_interpretation(latest),
                "supporting_features": {
                    "stock_bb_width_20": _round_number(latest.get("stock_bb_width_20"), 3),
                    "stock_volatility_20": _round_number(latest.get("stock_volatility_20"), 3),
                    "stock_volume_ratio_20": _round_number(latest.get("stock_volume_ratio_20"), 2),
                    "stock_obv_trend": _obv_label(latest.get("stock_obv_trend")),
                },
            },
            "market_relative": {
                "interpretation": _market_relative_interpretation(latest),
                "supporting_features": {
                    "stock_excess_return_5d": _pct_number(latest.get("stock_excess_return_5d")),
                    "stock_excess_return_20d": _pct_number(latest.get("stock_excess_return_20d")),
                    "stock_relative_strength_60": _pct_number(latest.get("stock_relative_strength_60")),
                },
            },
            "fx_context": {
                "interpretation": _fx_interpretation(latest),
                "supporting_features": {
                    "fx_return_20d": _pct_number(latest.get("fx_return_20d")),
                    "fx_close_to_ma20": _pct_number(latest.get("fx_close_to_ma20")),
                    "fx_rsi_14": _round_number(latest.get("fx_rsi_14"), 1),
                    "fx_volatility_20": _round_number(latest.get("fx_volatility_20"), 3),
                },
                "caution": "환율 영향은 기업의 수출입 구조, 비용 구조, 외화부채에 따라 달라질 수 있으므로 Y-Finance Agent 단독으로 확정 판단하지 않는다.",
            },
        },
        "cross_data_reconciliation": {
            "news_plus_market": _news_plus_market_block(cross, latest, news_alignment),
            "dart_plus_market": _dart_plus_market_block(dart_metrics, cross, latest, dart_alignment),
            "news_plus_dart_plus_market": _triple_cross_block(dart_metrics, cross, latest),
        },
    }


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
    stock_return = _safe_ratio(latest["stock_close"], start["stock_close"])
    kospi_return = _safe_ratio(latest.get("kospi_close"), start.get("kospi_close"))
    fx_return = _safe_ratio(latest.get("fx_close"), start.get("fx_close"))
    drawdown = _max_drawdown(frame["stock_close"])
    high_idx = frame["stock_close"].idxmax()
    low_idx = frame["stock_close"].idxmin()

    latest_snapshot = {
        "date": _date_str(latest["date"]),
        "stock_close": _number(latest.get("stock_close")),
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
            "high_close": {
                "date": _date_str(frame.loc[high_idx, "date"]),
                "value": _number(frame.loc[high_idx, "stock_close"]),
            },
            "low_close": {
                "date": _date_str(frame.loc[low_idx, "date"]),
                "value": _number(frame.loc[low_idx, "stock_close"]),
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
        stock_return = _safe_ratio(last.get("stock_close"), first.get("stock_close"))
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
                "end_rsi_14": _number(last.get("stock_rsi_14")),
                "end_volume_ratio_20": _number(last.get("stock_volume_ratio_20")),
                "end_stock_return_20d": _number(last.get("stock_return_20d")),
            }
        )
    return rows


def extract_news_periods(payload: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """Return news summaries keyed by YYYY-MM."""

    output_periods = payload.get("output", {}).get("periods") if isinstance(payload.get("output"), dict) else None
    candidates = output_periods or [
        item.get("output") for item in payload.get("period_results", []) if isinstance(item, dict)
    ]
    periods: dict[str, dict[str, Any]] = {}
    for item in candidates or []:
        if not isinstance(item, dict) or not item.get("period"):
            continue
        issues = item.get("issues") if isinstance(item.get("issues"), list) else []
        top_issues = sorted(
            [issue for issue in issues if isinstance(issue, dict)],
            key=lambda issue: (_importance_rank(issue.get("importance")), int(issue.get("mention_count") or 0)),
            reverse=True,
        )
        periods[str(item["period"])] = {
            "period": str(item["period"]),
            "period_summary": item.get("period_summary"),
            "top_issues": top_issues[:3],
        }
    return periods


def extract_dart_snapshot(payload: dict[str, Any]) -> dict[str, Any]:
    """Extract compact financial context from DART lightweight output."""

    metrics = payload.get("metrics_by_key", {})
    periods = payload.get("periods", {})
    metric_order = payload.get("metric_order") or list(metrics)
    extracted = []
    for key in metric_order:
        metric = metrics.get(key)
        if not isinstance(metric, dict):
            continue
        extracted.append(_extract_metric(metric))

    return {
        "schema_name": payload.get("schema_name"),
        "unit": payload.get("unit"),
        "periods": periods,
        "metrics": extracted,
    }


def build_cross_analysis(
    *,
    monthly: list[dict[str, Any]],
    news_periods: dict[str, dict[str, Any]],
    dart_snapshot: dict[str, Any],
    market: pd.DataFrame,
) -> dict[str, Any]:
    """Combine primary market data with supporting news and DART context."""

    monthly_with_news = []
    for row in monthly:
        news = news_periods.get(row["period"], {})
        top_issue = None
        if news.get("top_issues"):
            top_issue = news["top_issues"][0].get("issue")
        monthly_with_news.append(
            {
                **row,
                "news_summary": news.get("period_summary"),
                "top_news_issue": top_issue,
                "top_news_issues": news.get("top_issues", []),
            }
        )

    best = _extreme_month(monthly_with_news, maximum=True)
    worst = _extreme_month(monthly_with_news, maximum=False)
    dart_market = _dart_market_bridge(dart_snapshot=dart_snapshot, market=market)

    return {
        "monthly_market_news": monthly_with_news,
        "best_month_with_news": best,
        "worst_month_with_news": worst,
        "dart_market_bridge": dart_market,
        "interpretation": _build_interpretation(best, worst, dart_market),
    }


def render_markdown_report(payload: dict[str, Any]) -> str:
    """Render the structured report payload as Korean Markdown."""

    company = payload["company_name"]
    market = payload["market_summary"]
    performance = market["period_performance"]
    latest = market["latest_snapshot"]
    signals = market["signals"]
    cross = payload["cross_analysis"]
    dart_metrics = {item["metric_key"]: item for item in payload["dart_snapshot"]["metrics"]}
    monthly_rows = cross["monthly_market_news"][-12:]

    lines = [
        f"# {company} YFinance 중심 애널리스트 보고서",
        "",
        f"- 생성 시각: {payload['created_at']}",
        f"- 전용 데이터: `{payload['data_policy']['primary_dataset']}`",
        "- 보조 데이터:",
        *[f"  - `{path}`" for path in payload["data_policy"]["supporting_datasets"]],
        f"- 분석 기간: {market['period']['start']} ~ {market['period']['end']} ({market['period']['trading_rows']}거래일)",
        "",
        "## Executive View",
        "",
        f"- 주가는 기간 중 {_fmt_pct(performance['stock_return'])} 움직였고, KOSPI 대비 초과수익률은 {_fmt_pct(performance['stock_excess_vs_kospi'])}입니다.",
        f"- 최신 종가 { _fmt_number(latest['stock_close']) }원 기준 20일 수익률은 {_fmt_pct(latest['stock_return_20d'])}, 60일 수익률은 {_fmt_pct(latest['stock_return_60d'])}입니다.",
        f"- 기술적 위치는 {signals['trend']}입니다. RSI는 {_fmt_number(latest['stock_rsi_14'], digits=1)}, 20일 거래량 비율은 {_fmt_number(latest['stock_volume_ratio_20'], digits=2)}배입니다.",
        f"- 뉴스와 DART는 보조 자료로 사용했습니다. 핵심 해석은 `{cross['interpretation']}`",
        "",
        "## 1. YFinance 전용 데이터 분석",
        "",
        "### 가격과 상대성과",
        "",
        f"- 시작 종가 {_fmt_number(performance['start_close'])}원에서 최신 종가 {_fmt_number(performance['end_close'])}원으로 이동했습니다.",
        f"- 기간 고점은 {performance['high_close']['date']}의 {_fmt_number(performance['high_close']['value'])}원, 저점은 {performance['low_close']['date']}의 {_fmt_number(performance['low_close']['value'])}원입니다.",
        f"- 최대 낙폭은 {_fmt_pct(performance['max_drawdown'])}로, 상승 구간 중에도 가격 변동성이 누적됐는지 확인해야 합니다.",
        f"- 같은 기간 KOSPI 수익률은 {_fmt_pct(performance['kospi_return'])}, USD/KRW 변동률은 {_fmt_pct(performance['fx_return'])}입니다.",
        "",
        "### 모멘텀과 수급",
        "",
        f"- 최신 5일 초과수익률은 {_fmt_pct(latest['stock_excess_return_5d'])}, 20일 초과수익률은 {_fmt_pct(latest['stock_excess_return_20d'])}입니다.",
        f"- 종가는 20일 이동평균 대비 {_fmt_pct(latest['stock_close_to_ma20'])}, 60일 이동평균 대비 {_fmt_pct(latest['stock_close_to_ma60'])} 위치에 있습니다.",
        f"- MACD 히스토그램은 {_fmt_number(latest['stock_macd_hist'], digits=1)}이고 전일 변화는 {_fmt_number(latest['stock_macd_hist_change_1d'], digits=1)}입니다.",
        f"- 20일 변동성은 {_fmt_pct(latest['stock_volatility_20'])}, OBV 추세값은 {_fmt_number(latest['stock_obv_trend'], digits=3)}입니다.",
        "",
        "## 2. 보조 데이터와의 교차 분석",
        "",
        "### 뉴스 교차분석",
        "",
        _month_sentence("가장 강한 월간 주가 반응", cross["best_month_with_news"]),
        _month_sentence("가장 약한 월간 주가 반응", cross["worst_month_with_news"]),
        "",
        "### DART 교차분석",
        "",
        _dart_sentence(dart_metrics),
        _dart_bridge_sentence(cross["dart_market_bridge"]),
        "",
        "## 3. 월별 시장-뉴스 매핑",
        "",
        "| 월 | 주가수익률 | KOSPI 대비 | 월말 RSI | 핵심 뉴스 이슈 |",
        "|---|---:|---:|---:|---|",
    ]

    for row in monthly_rows:
        lines.append(
            "| {period} | {stock_return} | {excess} | {rsi} | {issue} |".format(
                period=row["period"],
                stock_return=_fmt_pct(row.get("stock_return")),
                excess=_fmt_pct(row.get("stock_excess_vs_kospi")),
                rsi=_fmt_number(row.get("end_rsi_14"), digits=1),
                issue=_clean_cell(row.get("top_news_issue") or "뉴스 이슈 없음"),
            )
        )

    lines.extend(
        [
            "",
            "## 4. 분석가 판단",
            "",
            "YFinance 전용 데이터 기준으로는 단기 절대 모멘텀과 중기 상대성과를 분리해서 봐야 합니다. 최신 구간의 5일 성과는 양호하지만 20일 KOSPI 대비 성과는 약해, 개별 호재가 시장 전체 강세를 완전히 앞서지는 못한 모습입니다.",
            "",
            "뉴스 데이터는 해당 기업의 주요 사업 이슈와 리스크 요인을 제공하고, DART 데이터는 실적과 비용 구조의 근거를 제공합니다. 따라서 주가 해석은 단순 기술적 반등이 아니라 실제 재무 지표와 뉴스 이벤트가 가격에 얼마나 반영되었는지 확인하는 방식으로 접근해야 합니다.",
            "",
            "다음 확인 포인트는 20일 상대성과 회복 여부, 거래량 동반 상승 지속 여부, 주요 뉴스 이슈가 실제 DART 실적 지표로 이어지는 속도입니다.",
            "",
        ]
    )
    return "\n".join(lines)


def _main_summary(latest: dict[str, Any]) -> str:
    trend = _mid_term_stance(latest)
    caution = _short_term_caution(latest)
    if trend == "positive" and caution:
        return "주가는 KOSPI 대비 단기 반등은 확인되지만 20일 상대성과가 약해, 절대 모멘텀과 시장 대비 모멘텀을 분리해서 볼 필요가 있다."
    if trend == "positive":
        return "주가는 주요 이동평균을 상회하고 중기 수익률도 양호해 시장 데이터 기준 긍정적인 흐름을 보인다."
    if trend == "negative":
        return "주가는 주요 이동평균과 상대성과 측면에서 약세가 확인되어 보수적인 해석이 필요하다."
    return "주가는 일부 모멘텀 지표는 우호적이나 상대성과와 기술적 신호가 혼재되어 중립적인 해석이 필요하다."


def _direction(latest: dict[str, Any]) -> str:
    if _mid_term_stance(latest) == "positive" and _short_term_caution(latest):
        return "positive_with_short_term_caution"
    if _mid_term_stance(latest) == "positive":
        return "positive"
    if _mid_term_stance(latest) == "negative":
        return "negative"
    return "neutral"


def _primary_basis(latest: dict[str, Any]) -> list[str]:
    return [
        f"stock_return_20d가 {_fmt_pct(latest.get('stock_return_20d'))}이고 KOSPI 20일 수익률 {_fmt_pct(latest.get('kospi_return_20d'))} 대비 초과수익률은 {_fmt_pct(latest.get('stock_excess_return_20d'))}",
        f"stock_close_to_ma20이 {_fmt_pct(latest.get('stock_close_to_ma20'))}, stock_close_to_ma60이 {_fmt_pct(latest.get('stock_close_to_ma60'))}로 주요 이동평균 대비 위치를 확인",
        f"stock_rsi_14가 {_fmt_number(latest.get('stock_rsi_14'), digits=1)}로 단기 과열 또는 침체 여부를 점검",
        f"stock_macd_hist_change_1d가 {_fmt_number(latest.get('stock_macd_hist_change_1d'), digits=2)}로 모멘텀 변화 방향을 확인",
    ]


def _short_term_stance(latest: dict[str, Any]) -> str:
    excess = latest.get("stock_excess_return_5d")
    stock_return = latest.get("stock_return_5d")
    if _positive(stock_return) and _positive(excess) and not _short_term_caution(latest):
        return "positive"
    if _positive(stock_return) or _positive(excess):
        return "neutral_positive"
    if _negative(stock_return) and _negative(excess):
        return "negative"
    return "neutral"


def _mid_term_stance(latest: dict[str, Any]) -> str:
    positives = sum(
        1
        for key in ["stock_return_20d", "stock_close_to_ma20", "stock_close_to_ma60"]
        if _positive(latest.get(key))
    )
    excess = latest.get("stock_excess_return_20d")
    if positives >= 2 and (excess is None or excess >= -0.03):
        return "positive"
    if positives == 0 and _negative(excess):
        return "negative"
    return "neutral"


def _long_term_stance(latest: dict[str, Any]) -> str:
    stock_return = latest.get("stock_return_60d")
    strength = latest.get("stock_relative_strength_60")
    if _positive(stock_return) and _positive(strength):
        return "conditional_positive"
    if _negative(stock_return) and _negative(strength):
        return "conditional_negative"
    return "neutral"


def _short_term_reasoning(latest: dict[str, Any]) -> str:
    return (
        f"5일 수익률은 {_fmt_pct(latest.get('stock_return_5d'))}이고 KOSPI 대비 5일 초과수익률은 "
        f"{_fmt_pct(latest.get('stock_excess_return_5d'))}이다. RSI는 {_fmt_number(latest.get('stock_rsi_14'), digits=1)}, "
        f"MACD histogram 변화는 {_fmt_number(latest.get('stock_macd_hist_change_1d'), digits=2)}로 단기 속도 조절 가능성을 함께 본다."
    )


def _mid_term_reasoning(latest: dict[str, Any]) -> str:
    return (
        f"20일 수익률은 {_fmt_pct(latest.get('stock_return_20d'))}이고 KOSPI 대비 20일 초과수익률은 "
        f"{_fmt_pct(latest.get('stock_excess_return_20d'))}이다. 종가는 MA20 대비 {_fmt_pct(latest.get('stock_close_to_ma20'))}, "
        f"MA60 대비 {_fmt_pct(latest.get('stock_close_to_ma60'))} 위치에 있어 중기 추세 판단의 핵심 근거로 사용한다."
    )


def _long_term_reasoning(latest: dict[str, Any], performance: dict[str, Any]) -> str:
    return (
        f"60일 수익률은 {_fmt_pct(latest.get('stock_return_60d'))}, 60일 상대강도는 "
        f"{_fmt_pct(latest.get('stock_relative_strength_60'))}이다. 분석 기간 최대낙폭은 "
        f"{_fmt_pct(performance.get('max_drawdown'))}로, 장기 추세의 안정성은 추가 장기 지표 없이 제한적으로 판단한다."
    )


def _price_trend_interpretation(latest: dict[str, Any]) -> str:
    if _positive(latest.get("stock_close_to_ma20")) and _positive(latest.get("stock_close_to_ma60")):
        return "종가는 MA20과 MA60을 모두 상회하고 있어 단기 및 중기 가격 추세는 우호적으로 해석된다."
    if _negative(latest.get("stock_close_to_ma20")) and _negative(latest.get("stock_close_to_ma60")):
        return "종가는 MA20과 MA60을 모두 하회하고 있어 가격 추세는 약세로 해석된다."
    return "종가의 이동평균 대비 위치가 혼재되어 가격 추세는 중립적으로 해석한다."


def _momentum_interpretation(latest: dict[str, Any]) -> str:
    rsi = latest.get("stock_rsi_14")
    macd_change = latest.get("stock_macd_hist_change_1d")
    if rsi is not None and rsi >= 70:
        return "RSI가 과열권에 진입해 단기 상승 피로가 커질 수 있다."
    if rsi is not None and rsi >= 65 and _negative(macd_change):
        return "RSI는 과열권에 근접했고 MACD histogram 변화가 음수여서 상승 모멘텀 둔화 가능성이 있다."
    if _positive(latest.get("stock_macd_hist")) and _positive(macd_change):
        return "MACD histogram과 전일 변화가 모두 양수라 단기 모멘텀은 개선 방향이다."
    return "모멘텀 지표는 뚜렷한 한쪽 방향보다 속도 조절 여부 확인이 필요한 상태다."


def _volatility_volume_interpretation(latest: dict[str, Any]) -> str:
    volume = latest.get("stock_volume_ratio_20")
    volatility = latest.get("stock_volatility_20")
    if volume is not None and volume >= 1.5:
        return "거래량이 20일 평균 대비 높은 수준이어서 최근 가격 움직임의 수급 확인 신호로 볼 수 있다."
    if volatility is not None and volatility >= 0.03:
        return "20일 변동성이 확대되어 추세 지속과 단기 변동성 리스크를 함께 고려해야 한다."
    return "거래량과 변동성은 과도한 위험 신호보다는 통제 가능한 범위로 해석된다."


def _market_relative_interpretation(latest: dict[str, Any]) -> str:
    if _positive(latest.get("stock_excess_return_5d")) and _positive(latest.get("stock_excess_return_20d")):
        return "종목은 최근 5일과 20일 모두 KOSPI 대비 초과성과를 기록해 시장 대비 강한 흐름을 보인다."
    if _positive(latest.get("stock_excess_return_5d")) and _negative(latest.get("stock_excess_return_20d")):
        return "단기 5일 기준으로는 KOSPI를 상회하지만 20일 기준으로는 하회해 최근 반등과 중기 상대열위가 공존한다."
    if _negative(latest.get("stock_excess_return_5d")) and _negative(latest.get("stock_excess_return_20d")):
        return "최근 5일과 20일 모두 KOSPI 대비 열위로, 시장 대비 모멘텀은 약하다."
    return "시장 대비 성과는 기간별로 혼재되어 있다."


def _fx_interpretation(latest: dict[str, Any]) -> str:
    fx_return = latest.get("fx_return_20d")
    if _positive(fx_return):
        return "원/달러 환율 상승은 수출기업에는 일부 우호적일 수 있으나, 시장 위험회피와 동반될 경우 주가 변동성 확대 요인이 될 수 있다."
    if _negative(fx_return):
        return "원/달러 환율 하락은 외환 부담을 낮출 수 있으나 수출 민감 기업의 원화 환산 매출 기대에는 중립 또는 부담 요인이 될 수 있다."
    return "환율 지표는 뚜렷한 방향성보다 보조적인 시장 환경 변수로 해석한다."


def _news_alignment(cross: dict[str, Any]) -> str:
    best = cross.get("best_month_with_news")
    if best and best.get("top_news_issue") and _positive(best.get("stock_return")):
        return "aligned"
    if best and best.get("top_news_issue"):
        return "partially_aligned"
    return "insufficient_evidence"


def _dart_alignment(cross: dict[str, Any]) -> str:
    bridge = cross.get("dart_market_bridge", {})
    stock_after = bridge.get("stock_return_after_period_end")
    excess_20d = bridge.get("latest_stock_excess_return_20d")
    if _positive(stock_after) and _positive(excess_20d):
        return "aligned"
    if _positive(stock_after) or _positive(excess_20d):
        return "partially_aligned"
    if stock_after is None and excess_20d is None:
        return "insufficient_evidence"
    return "not_aligned"


def _news_reconciliation_explanation(cross: dict[str, Any], latest: dict[str, Any]) -> str:
    best = cross.get("best_month_with_news") or {}
    issue = best.get("top_news_issue") or "주요 뉴스 이슈"
    return (
        f"뉴스 요약에서 `{issue}`가 확인되며, 해당 월 주가수익률은 {_fmt_pct(best.get('stock_return'))}이다. "
        f"다만 최신 20일 KOSPI 대비 초과수익률은 {_fmt_pct(latest.get('stock_excess_return_20d'))}이므로 뉴스 모멘텀이 항상 시장 대비 우위로 연결된 것은 아니다."
    )


def _used_news_summary(cross: dict[str, Any]) -> str | None:
    best = cross.get("best_month_with_news") or {}
    return best.get("news_summary")


def _news_plus_market_block(cross: dict[str, Any], latest: dict[str, Any], alignment: str) -> dict[str, Any]:
    best = cross.get("best_month_with_news") or {}
    worst = cross.get("worst_month_with_news") or {}
    issue = best.get("top_news_issue") or "주요 뉴스 이슈"
    return {
        "summary": (
            f"뉴스 흐름에서는 `{issue}`가 확인되고, 시장 데이터에서는 최신 5일 수익률 "
            f"{_fmt_pct(latest.get('stock_return_5d'))}, 20일 수익률 {_fmt_pct(latest.get('stock_return_20d'))}, "
            f"20일 초과수익률 {_fmt_pct(latest.get('stock_excess_return_20d'))}이 관찰된다. "
            f"뉴스와 가격 반응의 정합성은 `{alignment}`로 해석한다."
        ),
        "reaction_points": [
            {
                "point": f"{issue}와 월간 주가 반응",
                "cross_analysis": (
                    f"{best.get('period', '해당 월')}의 핵심 뉴스는 `{issue}`이며 월간 주가수익률은 "
                    f"{_fmt_pct(best.get('stock_return'))}, KOSPI 대비 성과는 {_fmt_pct(best.get('stock_excess_vs_kospi'))}이다."
                ),
                "reaction_interpretation": "긍정 뉴스가 단기 가격 관심과 일부 연결될 수 있으나, 같은 기간 시장 대비 성과까지 함께 확인해야 한다.",
            },
            {
                "point": "거래량과 뉴스 관심의 연결",
                "cross_analysis": (
                    f"최신 20일 거래량 비율은 {_fmt_number(latest.get('stock_volume_ratio_20'), digits=2)}배이고 "
                    f"RSI는 {_fmt_number(latest.get('stock_rsi_14'), digits=1)}이다."
                ),
                "reaction_interpretation": "뉴스 모멘텀이 거래 활성화와 연결될 수 있지만 RSI가 높을 경우 단기 속도 조절 가능성도 같이 본다.",
            },
        ],
        "divergences": [
            {
                "point": "긍정 뉴스와 시장 대비 상대성과의 괴리",
                "cross_analysis": (
                    f"뉴스상 긍정 이슈가 확인되지만 최신 20일 초과수익률은 "
                    f"{_fmt_pct(latest.get('stock_excess_return_20d'))}이다."
                ),
                "reaction_interpretation": "절대 주가 상승과 KOSPI 대비 상대성과는 다를 수 있어 뉴스만으로 가격 반응을 단정하지 않는다.",
            },
            {
                "point": "약세 월간 구간의 존재",
                "cross_analysis": (
                    f"{worst.get('period', '약세 구간')}에는 `{worst.get('top_news_issue') or '뉴스 이슈'}`가 있었지만 "
                    f"월간 주가수익률은 {_fmt_pct(worst.get('stock_return'))}였다."
                ),
                "reaction_interpretation": "뉴스가 긍정적이어도 시장/수급/차익실현 변수로 가격이 다르게 반응할 수 있다.",
            },
        ],
    }


def _dart_plus_market_block(
    metrics: dict[str, dict[str, Any]],
    cross: dict[str, Any],
    latest: dict[str, Any],
    alignment: str,
) -> dict[str, Any]:
    bridge = cross.get("dart_market_bridge", {})
    return {
        "summary": (
            f"DART 보조 데이터는 {_dart_used_summary(metrics)} 시장 데이터에서는 DART 기준일 이후 주가수익률 "
            f"{_fmt_pct(bridge.get('stock_return_after_period_end'))}, 최신 20일 초과수익률 "
            f"{_fmt_pct(latest.get('stock_excess_return_20d'))}이 확인된다. 정합성은 `{alignment}`로 해석한다."
        ),
        "reaction_points": [
            {
                "point": "DART 실적 지표와 기준일 이후 주가 반응",
                "cross_analysis": (
                    f"DART 현재기간 종료일 {bridge.get('period_end')} 이후 {bridge.get('latest_date')}까지 "
                    f"주가는 {_fmt_pct(bridge.get('stock_return_after_period_end'))}, KOSPI는 "
                    f"{_fmt_pct(bridge.get('kospi_return_after_period_end'))} 움직였다."
                ),
                "reaction_interpretation": "재무 지표가 개선 방향이면 가격 반응의 배경이 될 수 있으나, KOSPI 대비 성과로 상대적 강도를 재확인해야 한다.",
            },
            {
                "point": "수익성 지표와 중기 모멘텀",
                "cross_analysis": (
                    f"DART 요약은 공헌이익률 {_display_current(metrics.get('contribution_margin', {}))}, "
                    f"판관비율 {_display_current(metrics.get('sga_margin', {}))}, EPS {_display_current(metrics.get('eps', {}))}를 제공한다. "
                    f"시장 데이터의 20일 수익률은 {_fmt_pct(latest.get('stock_return_20d'))}이다."
                ),
                "reaction_interpretation": "수익성 개선은 중기 가격 회복의 해석 근거가 될 수 있지만, 가격은 기대를 선반영할 수 있다.",
            },
        ],
        "divergences": [
            {
                "point": "재무 개선과 상대성과 부진의 괴리",
                "cross_analysis": (
                    f"DART 지표가 개선 방향이어도 최신 20일 초과수익률은 "
                    f"{_fmt_pct(latest.get('stock_excess_return_20d'))}이다."
                ),
                "reaction_interpretation": "실적 개선 기대가 절대 주가에는 반영되더라도 시장 전체 강세보다 약할 수 있다.",
            }
        ],
    }


def _triple_cross_block(
    metrics: dict[str, dict[str, Any]],
    cross: dict[str, Any],
    latest: dict[str, Any],
) -> dict[str, Any]:
    best = cross.get("best_month_with_news") or {}
    bridge = cross.get("dart_market_bridge", {})
    issue = best.get("top_news_issue") or "주요 뉴스 이슈"
    return {
        "summary": (
            f"뉴스에서는 `{issue}`가 확인되고, DART는 {_dart_used_summary(metrics)} "
            f"시장 데이터는 최신 5일 수익률 {_fmt_pct(latest.get('stock_return_5d'))}, "
            f"20일 수익률 {_fmt_pct(latest.get('stock_return_20d'))}, 20일 초과수익률 "
            f"{_fmt_pct(latest.get('stock_excess_return_20d'))}을 보여준다. 세 데이터는 성장 서사와 가격 반응을 함께 설명하지만, 상대성과 약화 여부는 별도 리스크로 남는다."
        ),
        "reaction_points": [
            {
                "point": "뉴스 성장 모멘텀, DART 실적, 주가 반응의 삼각 연결",
                "cross_analysis": (
                    f"`{issue}` 뉴스, 매출 성장률 {_first_comparison_display(metrics.get('revenue_growth', {})) or '확인 불가'}, "
                    f"DART 기준일 이후 주가수익률 {_fmt_pct(bridge.get('stock_return_after_period_end'))}을 함께 비교한다."
                ),
                "reaction_interpretation": "뉴스와 재무 데이터가 같은 방향의 성장 근거를 제공할 때 주가 반응의 설명력이 높아진다.",
            },
            {
                "point": "거래량과 실적 뉴스의 동시 확인",
                "cross_analysis": (
                    f"최신 거래량 비율은 {_fmt_number(latest.get('stock_volume_ratio_20'), digits=2)}배이며, "
                    f"뉴스와 DART 모두 핵심 제품 및 실적 개선 맥락을 제공한다."
                ),
                "reaction_interpretation": "수급 활성화가 단순 기술적 움직임인지, 실적·뉴스 모멘텀과 결합된 관심 증가인지 함께 검토한다.",
            },
        ],
        "divergences": [
            {
                "point": "성장 서사와 KOSPI 대비 상대성과 간 괴리",
                "cross_analysis": (
                    f"뉴스와 DART는 성장·수익성 근거를 제시하지만, 최신 20일 초과수익률은 "
                    f"{_fmt_pct(latest.get('stock_excess_return_20d'))}이다."
                ),
                "reaction_interpretation": "기업 고유 모멘텀이 긍정적이어도 시장 전체 강세, 밸류에이션, 차익실현 때문에 상대성과가 약해질 수 있다.",
            }
        ],
    }


def _dart_reconciliation_explanation(metrics: dict[str, dict[str, Any]], cross: dict[str, Any], latest: dict[str, Any]) -> str:
    revenue_growth = _first_comparison_display(metrics.get("revenue_growth", {})) or "확인 불가"
    bridge = cross.get("dart_market_bridge", {})
    return (
        f"DART 보조 데이터는 매출 성장률 {revenue_growth}와 수익성 지표를 제공해 중기 가격 흐름의 재무적 배경을 점검하게 한다. "
        f"DART 현재기간 종료일 이후 주가수익률은 {_fmt_pct(bridge.get('stock_return_after_period_end'))}, 최신 20일 초과수익률은 {_fmt_pct(latest.get('stock_excess_return_20d'))}이다."
    )


def _dart_used_summary(metrics: dict[str, dict[str, Any]]) -> str:
    return _dart_sentence(metrics).removeprefix("- ")


def _conflict_points(latest: dict[str, Any], cross: dict[str, Any]) -> list[str]:
    conflicts = []
    if _positive(latest.get("stock_return_20d")) and _negative(latest.get("stock_excess_return_20d")):
        conflicts.append("가격 지표는 20일 절대 상승을 보이지만, KOSPI 대비 20일 초과수익률은 음수여서 시장 대비 강도는 약하다.")
    if _short_term_caution(latest):
        conflicts.append("단기 수익률은 양호할 수 있으나 RSI 과열 근접 또는 MACD histogram 둔화는 단기 조정 가능성을 시사한다.")
    worst = cross.get("worst_month_with_news")
    if worst and worst.get("top_news_issue"):
        conflicts.append("뉴스 요약에 긍정 이슈가 있어도 일부 월간 구간에서는 주가가 약세를 보일 수 있어 가격 반응을 별도로 확인해야 한다.")
    if not conflicts:
        conflicts.append("뉴스, DART, 가격 지표 사이의 큰 충돌은 제한적이나, 보조 데이터는 가격 지표의 원인으로 단정하지 않는다.")
    return conflicts


def _short_term_caution(latest: dict[str, Any]) -> bool:
    rsi = latest.get("stock_rsi_14")
    macd_change = latest.get("stock_macd_hist_change_1d")
    return (rsi is not None and rsi >= 65) or _negative(macd_change)


def _obv_label(value: Any) -> str:
    number = _number(value)
    if number is None:
        return "unknown"
    if number > 0.02:
        return "upward"
    if number < -0.02:
        return "downward"
    return "flat"


def _positive(value: Any) -> bool:
    number = _number(value)
    return number is not None and number > 0


def _negative(value: Any) -> bool:
    number = _number(value)
    return number is not None and number < 0


def _pct_number(value: Any) -> float | None:
    number = _number(value)
    if number is None:
        return None
    return round(number * 100, 2)


def _round_number(value: Any, digits: int) -> float | None:
    number = _number(value)
    if number is None:
        return None
    return round(number, digits)


def _extract_metric(metric: dict[str, Any]) -> dict[str, Any]:
    values = metric.get("values_by_period", {})
    comparisons = metric.get("comparisons", {})
    return {
        "metric_key": metric.get("metric_key"),
        "display_name": metric.get("display_name"),
        "metric_type": metric.get("metric_type"),
        "unit": metric.get("unit"),
        "current": _compact_period_value(values.get("current_fiscal_year")),
        "previous": _compact_period_value(values.get("previous_fiscal_year")),
        "comparisons": comparisons,
    }


def _compact_period_value(value: Any) -> dict[str, Any] | None:
    if not isinstance(value, dict):
        return None
    return {
        "period_label": value.get("period", {}).get("label") if isinstance(value.get("period"), dict) else None,
        "period_end": value.get("period", {}).get("period_end") if isinstance(value.get("period"), dict) else None,
        "basis": value.get("period", {}).get("basis") if isinstance(value.get("period"), dict) else None,
        "value": _number(value.get("value")),
        "display_value": value.get("display_value"),
        "status": value.get("status"),
    }


def _dart_market_bridge(*, dart_snapshot: dict[str, Any], market: pd.DataFrame) -> dict[str, Any]:
    current_period = dart_snapshot.get("periods", {}).get("current_fiscal_year", {})
    period_end = current_period.get("period_end") if isinstance(current_period, dict) else None
    if not period_end:
        return {"period_end": None, "market_return_after_period_end": None}

    target = pd.to_datetime(period_end, errors="coerce")
    if pd.isna(target):
        return {"period_end": period_end, "market_return_after_period_end": None}

    after = market.loc[market["date"] >= target]
    if after.empty:
        return {"period_end": period_end, "market_return_after_period_end": None}
    start = after.iloc[0]
    latest = market.iloc[-1]
    return {
        "period_end": period_end,
        "market_start_date": _date_str(start["date"]),
        "latest_date": _date_str(latest["date"]),
        "stock_return_after_period_end": _safe_ratio(latest.get("stock_close"), start.get("stock_close")),
        "kospi_return_after_period_end": _safe_ratio(latest.get("kospi_close"), start.get("kospi_close")),
        "latest_stock_return_20d": _number(latest.get("stock_return_20d")),
        "latest_stock_excess_return_20d": _number(latest.get("stock_excess_return_20d")),
    }


def _build_interpretation(best: dict[str, Any] | None, worst: dict[str, Any] | None, dart_bridge: dict[str, Any]) -> str:
    parts = []
    if best:
        parts.append(f"{best['period']} 강세는 주요 뉴스 '{best.get('top_news_issue') or '이슈'}'와 함께 나타납니다")
    if worst:
        parts.append(f"{worst['period']} 약세는 뉴스 모멘텀보다 시장/수급 변수의 영향까지 점검해야 합니다")
    after_dart = dart_bridge.get("stock_return_after_period_end")
    if after_dart is not None:
        parts.append(f"DART 기준일 이후 주가 수익률은 {_fmt_pct(after_dart)}입니다")
    return "; ".join(parts) if parts else "보조 데이터와의 직접 연결고리는 제한적이며 YFinance 지표 중심 판단이 필요합니다."


def _month_sentence(label: str, row: dict[str, Any] | None) -> str:
    if not row:
        return f"- {label}: 산출할 수 없습니다."
    return (
        f"- {label}: {row['period']} 주가수익률 {_fmt_pct(row.get('stock_return'))}, "
        f"KOSPI 대비 {_fmt_pct(row.get('stock_excess_vs_kospi'))}. "
        f"동월 핵심 뉴스는 `{row.get('top_news_issue') or '뉴스 이슈 없음'}`입니다."
    )


def _dart_sentence(metrics: dict[str, dict[str, Any]]) -> str:
    revenue = metrics.get("revenue", {})
    growth = metrics.get("revenue_growth", {})
    contribution_margin = metrics.get("contribution_margin", {})
    sga_margin = metrics.get("sga_margin", {})
    eps = metrics.get("eps", {})
    growth_value = _first_comparison_display(growth)
    return (
        "- DART 보조지표 기준 매출은 "
        f"{_display_current(revenue)}, 매출 성장률은 {growth_value or '확인 불가'}입니다. "
        f"공헌이익률은 {_display_current(contribution_margin)}, 판관비율은 {_display_current(sga_margin)}, "
        f"EPS는 {_display_current(eps)}입니다."
    )


def _dart_bridge_sentence(bridge: dict[str, Any]) -> str:
    if bridge.get("stock_return_after_period_end") is None:
        return "- DART 기준일과 시장 데이터의 직접 연결 수익률은 산출하지 못했습니다."
    return (
        f"- DART 현재기간 종료일({bridge['period_end']}) 이후 {bridge['latest_date']}까지 "
        f"주가는 {_fmt_pct(bridge['stock_return_after_period_end'])}, KOSPI는 {_fmt_pct(bridge['kospi_return_after_period_end'])} 움직였습니다. "
        f"최신 20일 초과수익률은 {_fmt_pct(bridge['latest_stock_excess_return_20d'])}입니다."
    )


def _display_current(metric: dict[str, Any]) -> str:
    current = metric.get("current") if isinstance(metric, dict) else None
    if not current:
        return "확인 불가"
    value = current.get("display_value")
    basis = current.get("basis")
    return f"{value}({basis})" if basis and value else value or "확인 불가"


def _first_comparison_display(metric: dict[str, Any]) -> str | None:
    comparisons = metric.get("comparisons") if isinstance(metric, dict) else None
    if not isinstance(comparisons, dict):
        return None
    for item in comparisons.values():
        if isinstance(item, dict) and item.get("display_value"):
            return item["display_value"]
    return None


def _extreme_month(rows: list[dict[str, Any]], *, maximum: bool) -> dict[str, Any] | None:
    valid = [row for row in rows if row.get("stock_return") is not None]
    if not valid:
        return None
    return max(valid, key=lambda row: row["stock_return"]) if maximum else min(valid, key=lambda row: row["stock_return"])


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


def _fmt_pct(value: Any) -> str:
    number = _number(value)
    if number is None:
        return "N/A"
    return f"{number * 100:.2f}%"


def _fmt_number(value: Any, *, digits: int = 0) -> str:
    number = _number(value)
    if number is None:
        return "N/A"
    return f"{number:,.{digits}f}"


def _clean_cell(value: str) -> str:
    return str(value).replace("|", "/").replace("\n", " ")


def _importance_rank(value: Any) -> int:
    return {"high": 3, "medium": 2, "low": 1}.get(str(value).lower(), 0)


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
