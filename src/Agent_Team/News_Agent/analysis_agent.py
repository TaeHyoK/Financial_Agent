from __future__ import annotations

import argparse
import json
import os
import re
import time
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any

from openai import OpenAI
from tqdm.auto import tqdm

from .io.storage import save_json


DEFAULT_MODEL = "gpt-5.4-mini"
DEFAULT_GRANULARITY = "month"
SUMMARY_MONTH_COUNT = 12
RECENT_RAW_MONTH_COUNT = 3
DEFAULT_MAX_RAW_EVENTS_PER_PERIOD = 40

def _project_root() -> Path:
    return Path(__file__).resolve().parents[3]


@dataclass(frozen=True)
class AnalysisPaths:
    context_export_dir: Path
    period_summaries_path: Path
    recent_raw_path: Path
    dart_lightweight_path: Path
    market_summary_path: Path
    output_dir: Path
    input_payload_path: Path
    llm_request_path: Path
    handoff_path: Path
    evidence_map_path: Path


def main() -> None:
    args = build_parser().parse_args()
    paths = run_analysis_agent(
        context_export_dir=args.context_export_dir,
        granularity=args.granularity,
        company_name=args.company_name,
        ticker=args.ticker,
        corp_code=args.corp_code,
        as_of_date=args.as_of_date,
        dart_lightweight=args.dart_lightweight,
        market_summary=args.market_summary,
        output_dir=args.output_dir,
        model=args.model,
        env_path=args.env_path,
        timeout_seconds=args.timeout_seconds,
        max_raw_events_per_period=args.max_raw_events_per_period,
        show_progress=True,
    )
    print(f"input_payload={paths.input_payload_path}")
    print(f"llm_request={paths.llm_request_path}")
    print(f"handoff={paths.handoff_path}")
    print(f"evidence_map={paths.evidence_map_path}")


def run_analysis_agent(
    *,
    context_export_dir: str | Path,
    granularity: str = DEFAULT_GRANULARITY,
    company_name: str | None = None,
    ticker: str | None = None,
    corp_code: str | None = None,
    as_of_date: str | None = None,
    dart_lightweight: str | None = None,
    market_summary: str | None = None,
    output_dir: str | None = None,
    model: str | None = None,
    env_path: str | Path | None = None,
    timeout_seconds: float = 300.0,
    max_raw_events_per_period: int = DEFAULT_MAX_RAW_EVENTS_PER_PERIOD,
    show_progress: bool = False,
) -> AnalysisPaths:
    project_root = _project_root()
    _load_env_file(project_root / ".env")
    if env_path:
        _load_env_file(Path(env_path))

    context_export_dir = Path(context_export_dir).expanduser()
    if not context_export_dir.is_absolute():
        context_export_dir = project_root / context_export_dir
    context_export_dir = context_export_dir.resolve()

    progress = tqdm(total=5, desc="News Agent handoff", unit="step", disable=not show_progress)
    company_name, as_of_date_value = _infer_company_and_date(context_export_dir, company_name, as_of_date)
    paths = _resolve_paths(
        project_root=project_root,
        context_export_dir=context_export_dir,
        granularity=granularity,
        as_of_date=as_of_date_value,
        dart_lightweight_path=dart_lightweight,
        market_summary_path=market_summary,
        output_dir=output_dir,
    )
    progress.set_description("News Agent: build input")

    resolved_model = model or os.getenv("NEWS_AGENT_LLM_MODEL") or os.getenv("OPENAI_MODEL") or DEFAULT_MODEL
    input_payload = build_analysis_input_payload(
        company_name=company_name,
        ticker=ticker,
        corp_code=corp_code,
        as_of_date=as_of_date_value,
        paths=paths,
        max_raw_events_per_period=max_raw_events_per_period,
    )
    progress.update(1)
    progress.set_description("News Agent: build request")
    llm_request = build_llm_request(input_payload=input_payload, model=resolved_model)
    progress.update(1)

    progress.set_description("News Agent: save inputs")
    save_json(input_payload, paths.input_payload_path)
    save_json(llm_request, paths.llm_request_path)
    save_json(input_payload.get("evidence_map") or {}, paths.evidence_map_path)
    progress.update(1)

    progress.set_description("News Agent: call LLM")
    handoff = execute_analysis_request(
        llm_request=llm_request,
        input_payload=input_payload,
        model=resolved_model,
        timeout_seconds=timeout_seconds,
    )
    progress.update(1)
    progress.set_description("News Agent: save handoff")
    save_json(handoff, paths.handoff_path)
    progress.update(1)
    progress.close()
    return paths


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run the News Agent handoff analysis.")
    parser.add_argument(
        "--context-export-dir",
        required=True,
        help="Context export directory that contains the month folder.",
    )
    parser.add_argument("--granularity", default=DEFAULT_GRANULARITY, choices=["month"], help="Input granularity.")
    parser.add_argument("--company-name", default=None, help="Override company name inferred from context directory.")
    parser.add_argument("--ticker", default=None, help="Ticker used in target_entity.")
    parser.add_argument("--corp-code", default=None, help="DART corp code used in target_entity.")
    parser.add_argument("--as-of-date", default=None, help="As-of date in YYYY-MM-DD or YYYYMMDD.")
    parser.add_argument("--dart-lightweight", default=None, help="Override DART lightweight JSON path.")
    parser.add_argument("--market-summary", default=None, help="Override YFinance market summary JSON path.")
    parser.add_argument("--output-dir", default=None, help="Override output directory.")
    parser.add_argument("--model", default=None, help="OpenAI model. Defaults to NEWS_AGENT_LLM_MODEL or gpt-5.4-mini.")
    parser.add_argument("--env-path", default=None, help="Optional .env path loaded after News/.env.")
    parser.add_argument("--timeout-seconds", type=float, default=300.0, help="OpenAI request timeout.")
    parser.add_argument(
        "--max-raw-events-per-period",
        type=int,
        default=DEFAULT_MAX_RAW_EVENTS_PER_PERIOD,
        help="Top raw events per recent period sent to the LLM.",
    )
    return parser


def build_analysis_input_payload(
    *,
    company_name: str,
    ticker: str | None,
    corp_code: str | None,
    as_of_date: date,
    paths: AnalysisPaths,
    max_raw_events_per_period: int,
) -> dict[str, Any]:
    period_summaries = _load_json(paths.period_summaries_path)
    recent_raw = _load_json(paths.recent_raw_path)
    dart_lightweight = _load_json(paths.dart_lightweight_path)
    market_summary = _load_json(paths.market_summary_path)

    period_keys = _month_window(as_of_date, SUMMARY_MONTH_COUNT)
    raw_periods = period_keys[-RECENT_RAW_MONTH_COUNT:]
    summary_periods = period_keys[:-RECENT_RAW_MONTH_COUNT]

    selected_summaries = _select_period_summaries(period_summaries, summary_periods)
    product_terms = _extract_product_terms(period_summaries, company_name)
    selected_raw = _select_recent_raw_events(
        recent_raw,
        raw_periods,
        company_name=company_name,
        product_terms=product_terms,
        max_events_per_period=max_raw_events_per_period,
    )
    financial_context = _compact_financial_context(dart_lightweight)
    market_context = _compact_market_context(market_summary)
    evidence_map = _build_evidence_map(
        selected_summaries=selected_summaries,
        selected_raw=selected_raw,
        financial_context=financial_context,
        market_context=market_context,
    )

    return {
        "agent_name": "News Agent",
        "output_mode": "analysis_handoff_input",
        "target_entity": {
            "company_name": company_name,
            "ticker": ticker,
            "corp_code": corp_code,
            "as_of_date": as_of_date.isoformat(),
        },
        "input_policy": {
            "summary_periods": summary_periods,
            "recent_raw_periods": raw_periods,
            "summary_rule": "Use monthly LLM summaries for the oldest 9 periods in the 12-month window.",
            "recent_raw_rule": "Use recent raw news events for the latest 3 periods in the 12-month window.",
            "max_raw_events_per_period": max_raw_events_per_period,
            "investment_decision_allowed": False,
        },
        "source_paths": {
            "period_summaries": str(paths.period_summaries_path),
            "recent_raw": str(paths.recent_raw_path),
            "dart_lightweight": str(paths.dart_lightweight_path),
            "market_summary": str(paths.market_summary_path),
        },
        "news_context": {
            "older_period_summaries": selected_summaries,
            "recent_raw_events": selected_raw,
        },
        "cross_domain_context": {
            "financial": financial_context,
            "market": market_context,
        },
        "evidence_map": evidence_map,
        "evidence_map_path": str(paths.evidence_map_path),
    }


def build_llm_request(*, input_payload: dict[str, Any], model: str) -> dict[str, Any]:
    expected_output_schema = {
        "agent_name": "News Agent",
        "output_version": "1.0",
        "output_mode": "analysis_handoff",
        "target_entity": input_payload["target_entity"],
        "input_summary": {
            "summary_periods": [],
            "recent_raw_periods": [],
            "cross_domain_inputs": ["financial", "market"],
        },
        "analysis_blocks": {
            "news_only": {
                "summary": "string",
                "positive_signals": [],
                "negative_signals": [],
                "key_risks": [],
                "uncertainties": [],
            },
            "news_plus_financial": {
                "summary": "string",
                "cross_points": [
                    {
                        "point": "string",
                        "cross_analysis": "뉴스 이벤트와 재무 지표를 연결한 해석",
                        "interpretation_limit": "string",
                    }
                ],
                "conflicting_points": [
                    {
                        "point": "string",
                        "cross_analysis": "뉴스 이벤트와 재무 지표가 충돌하거나 연결이 약한 지점",
                        "interpretation_limit": "string",
                    }
                ],
                "financial_context_limits": [
                    {
                        "limit": "string",
                    }
                ],
            },
            "news_plus_market": {
                "summary": "string",
                "reaction_points": [
                    {
                        "point": "string",
                        "cross_analysis": "뉴스 이벤트와 주가/시장 지표를 연결한 해석",
                        "reaction_interpretation": "string",
                    }
                ],
                "divergences": [
                    {
                        "point": "string",
                        "cross_analysis": "뉴스 이벤트와 주가/시장 지표가 다르게 움직이는 지점",
                        "reaction_interpretation": "string",
                    }
                ],
            },
            "news_plus_financial_plus_market": {
                "summary": "string",
                "integrated_signals": [],
                "integrated_risks": [],
                "handoff_notes": [],
            },
        },
        "evidence_map_path": "string path to news_agent_evidence_map.json",
    }
    return {
        "model": model,
        "temperature": 0.2,
        "response_format": {"type": "json_object"},
        "messages": [
            {
                "role": "system",
                "content": (
                    "당신은 한국 상장사 뉴스 분석 에이전트입니다. "
                    "출력은 상위 통합 레이어에 전달할 분석 handoff JSON입니다. "
                    "절대 buy/sell/hold, 매수/매도/보유, 목표주가, 투자판단, 투자 판단 시 같은 문구를 출력하지 마세요. "
                    "입력에 없는 사실이나 수치를 만들지 마세요. "
                    "근거 식별자는 별도 evidence_map_path 파일에서 관리하므로 handoff 본문에는 evidence id를 출력하지 마세요. "
                    "JSON key는 영어로 쓰고 분석 문장은 한국어로 작성하세요."
                ),
            },
            {
                "role": "user",
                "content": json.dumps(
                    {
                        "task": (
                            "뉴스 전용 분석을 먼저 수행하고, 이어서 뉴스+재무, 뉴스+시장, "
                            "뉴스+재무+시장 통합 교차 분석을 수행하세요. "
                            "하위 에이전트이므로 투자 결론을 내리지 말고, 상위 레이어가 판단할 "
                            "근거, 모순, 리스크, 불확실성만 정리하세요."
                        ),
                        "analysis_rules": [
                            "news_only는 뉴스 데이터만 사용합니다.",
                            "news_plus_financial은 재무제표 단독 설명이 아닙니다. 반드시 뉴스 이벤트와 DART 지표의 연결 또는 괴리만 작성합니다.",
                            "news_plus_market은 주가/시장 단독 설명이 아닙니다. 반드시 뉴스 이벤트와 YFinance 지표의 연결 또는 괴리만 작성합니다.",
                            "교차분석 블록의 summary도 보조 도메인 수치 나열이 아니라 뉴스 이벤트와 해당 도메인 데이터의 관계를 요약합니다.",
                            "news_plus_financial_plus_market은 세 도메인을 통합합니다.",
                            "evidence_map은 출력하지 말고 evidence_map_path만 출력합니다.",
                            "final handoff에는 evidence_ids, news_evidence_ids, financial_evidence_ids, market_evidence_ids를 출력하지 않습니다.",
                            "근거 추적은 evidence_map_path의 별도 파일로 처리하므로 본문에는 사람이 읽는 분석만 남깁니다.",
                            "재무제표 수치를 재계산하지 말고 입력에 있는 financial context만 해석합니다.",
                            "DART 비교 항목의 basis_mismatch가 true이면 전년 대비, YoY, 증가, 감소, 개선처럼 같은 기간 비교로 오해될 표현을 쓰지 않습니다.",
                            "basis_mismatch가 true인 DART 항목은 '2025 Q3 YTD와 2024 full-year의 기준이 다른 단순 비교' 또는 '기간 기준이 달라 방향성 참고만 가능'이라고 표현합니다.",
                            "가격 움직임만으로 펀더멘털 개선을 단정하지 않습니다.",
                            "뉴스만으로 매출, 이익, EPS 개선을 단정하지 않습니다.",
                            "주가/시장 분석은 stock return, excess return, volume ratio, relative strength 같은 구체 지표명을 문장으로 언급하되 evidence id 필드는 만들지 않습니다.",
                        ],
                        "expected_output_schema": expected_output_schema,
                        "input_payload": input_payload,
                    },
                    ensure_ascii=False,
                ),
            },
        ],
    }


def execute_analysis_request(
    *,
    llm_request: dict[str, Any],
    input_payload: dict[str, Any],
    model: str,
    timeout_seconds: float,
) -> dict[str, Any]:
    api_key = os.getenv("OPENAI_API_KEY", "").strip()
    if not api_key:
        raise RuntimeError("OPENAI_API_KEY is required in News/.env or --env-path.")

    client = OpenAI(api_key=api_key, timeout=timeout_seconds)
    started_at = time.monotonic()
    response = client.chat.completions.create(
        model=model,
        messages=llm_request["messages"],
        temperature=float(llm_request.get("temperature", 0.2)),
        response_format=llm_request.get("response_format", {"type": "json_object"}),
    )
    elapsed_seconds = time.monotonic() - started_at
    content = response.choices[0].message.content or ""
    parsed_output, parse_warning = _parse_json_content(content)
    if isinstance(parsed_output, dict):
        _rewrite_basis_mismatch_phrases(parsed_output, input_payload)
        _strip_evidence_id_fields(parsed_output)
        parsed_output["evidence_map_path"] = str(input_payload.get("evidence_map_path") or "")
        parsed_output.pop("evidence_map", None)
        parsed_output.pop("validation", None)
    elif parse_warning:
        parsed_output = {"parse_error": parse_warning, "raw_content": content}

    usage = None
    if getattr(response, "usage", None) is not None:
        usage_obj = response.usage
        usage = usage_obj.model_dump() if hasattr(usage_obj, "model_dump") else dict(usage_obj)

    return {
        "description": {
            "purpose": "News Agent handoff analysis generated by a controlled LLM call.",
            "execution_mode": "single_request",
            "elapsed_seconds": round(elapsed_seconds, 3),
        },
        "model": model,
        "usage": usage,
        "output": parsed_output,
        "raw_content": content if not isinstance(parsed_output, dict) else None,
    }


def _resolve_paths(
    *,
    project_root: Path,
    context_export_dir: Path,
    granularity: str,
    as_of_date: date,
    dart_lightweight_path: str | None,
    market_summary_path: str | None,
    output_dir: str | None,
) -> AnalysisPaths:
    run_key = context_export_dir.parent.name if context_export_dir.name == "context_exports" else context_export_dir.name
    period_summaries_path = context_export_dir / granularity / "llm_period_summaries.json"
    recent_raw_path = context_export_dir / granularity / "recent_raw_input.json"
    dart_path = (
        Path(dart_lightweight_path)
        if dart_lightweight_path
        else project_root / "Output_total" / "Financial" / run_key / "dart_lightweight.json"
    )
    market_path = (
        Path(market_summary_path)
        if market_summary_path
        else project_root / "Output_total" / "Y_Finance" / run_key / "market_summary.json"
    )
    if output_dir:
        output_path = Path(output_dir)
    else:
        output_root = context_export_dir.parent if context_export_dir.name == "context_exports" else context_export_dir
        output_path = output_root / "output"
    if not output_path.is_absolute():
        output_path = project_root / output_path
    output_path = output_path.resolve()
    return AnalysisPaths(
        context_export_dir=context_export_dir,
        period_summaries_path=period_summaries_path.resolve(),
        recent_raw_path=recent_raw_path.resolve(),
        dart_lightweight_path=dart_path.expanduser().resolve(),
        market_summary_path=market_path.expanduser().resolve(),
        output_dir=output_path,
        input_payload_path=output_path / "news_agent_input_payload.json",
        llm_request_path=output_path / "news_agent_llm_request.json",
        handoff_path=output_path / "news_agent_handoff.json",
        evidence_map_path=output_path / "news_agent_evidence_map.json",
    )


def _infer_company_and_date(
    context_export_dir: Path,
    company_name_override: str | None,
    as_of_date_override: str | None,
) -> tuple[str, date]:
    match = re.match(r"^(?P<company>.+)_(?P<date>\d{8})$", context_export_dir.name)
    inferred_company = match.group("company") if match else context_export_dir.name
    inferred_date = _parse_date(match.group("date")) if match else None
    company_name = company_name_override or inferred_company
    as_of_date = _parse_date(as_of_date_override) if as_of_date_override else inferred_date
    if as_of_date is None:
        raise ValueError("as-of date could not be inferred. Pass --as-of-date.")
    return company_name, as_of_date


def _select_period_summaries(payload: dict[str, Any], periods: list[str]) -> list[dict[str, Any]]:
    by_period = {
        str(item.get("period")): item
        for item in payload.get("period_results", [])
        if isinstance(item, dict) and item.get("status") == "success"
    }
    selected: list[dict[str, Any]] = []
    for period in periods:
        item = by_period.get(period)
        if not item:
            continue
        selected.append(
            {
                "evidence_id": f"NEWS_SUMMARY_{period}",
                "period": period,
                "output": item.get("output") or {},
            }
        )
    return selected


def _select_recent_raw_events(
    payload: dict[str, Any],
    periods: list[str],
    *,
    company_name: str,
    product_terms: set[str] | None = None,
    max_events_per_period: int,
) -> list[dict[str, Any]]:
    by_period = {
        str(item.get("period")): item
        for item in payload.get("periods", [])
        if isinstance(item, dict)
    }
    selected: list[dict[str, Any]] = []
    for period in periods:
        period_payload = by_period.get(period)
        if not period_payload:
            continue
        events = list(period_payload.get("events") or [])
        events.sort(
            key=lambda event: (
                float(event.get("final_score") or 0.0),
                int(event.get("mention_count") or 0),
                str(event.get("time") or ""),
            ),
            reverse=True,
        )
        compact_events = []
        for event in events[: max(max_events_per_period, 1)]:
            event_id = str(event.get("event_id") or "")
            compact_events.append(
                {
                    "evidence_id": f"NEWS_RAW_{period}_{event_id}",
                    "event_id": event_id,
                    "mention_count": int(event.get("mention_count") or 0),
                    "title": str(event.get("title") or ""),
                    "snippet": str(event.get("snippet") or ""),
                    "time": str(event.get("time") or ""),
                    "final_score": float(event.get("final_score") or 0.0),
                    "relation_type": _classify_news_relation(event, company_name, product_terms=product_terms),
                }
            )
        selected.append(
            {
                "period": period,
                "source_event_count": len(events),
                "included_event_count": len(compact_events),
                "events": compact_events,
            }
        )
    return selected


def _compact_financial_context(payload: dict[str, Any]) -> dict[str, Any]:
    metric_keys = payload.get("metric_order") or sorted((payload.get("metrics_by_key") or {}).keys())
    metrics_by_key = payload.get("metrics_by_key") or {}
    compact_metrics: dict[str, Any] = {}
    for metric_key in metric_keys:
        metric = metrics_by_key.get(metric_key) or {}
        comparisons = _with_comparability_flags(metric.get("comparisons") or {})
        compact: dict[str, Any] = {
            "evidence_id": f"DART_{str(metric_key).upper()}",
            "display_name": metric.get("display_name"),
            "metric_type": metric.get("metric_type"),
            "unit": metric.get("unit"),
        }
        if metric.get("values_by_period"):
            compact["values_by_period"] = metric.get("values_by_period")
        if comparisons:
            compact["comparisons"] = comparisons
        compact_metrics[str(metric_key)] = compact
    return {
        "schema_name": payload.get("schema_name"),
        "periods": payload.get("periods") or {},
        "comparison_pairs": payload.get("comparison_pairs") or {},
        "metrics_by_key": compact_metrics,
    }


def _compact_market_context(payload: Any) -> dict[str, Any]:
    row = payload[0] if isinstance(payload, list) and payload else payload
    if not isinstance(row, dict):
        return {"metrics_by_key": {}}
    fields = [
        "date",
        "stock_close",
        "stock_return_5d",
        "stock_return_20d",
        "stock_return_60d",
        "stock_close_to_ma20",
        "stock_close_to_ma60",
        "stock_rsi_14",
        "stock_macd_hist",
        "stock_volatility_20",
        "stock_volume_ratio_20",
        "kospi_return_20d",
        "fx_return_20d",
        "stock_excess_return_5d",
        "stock_excess_return_20d",
        "stock_relative_strength_60",
    ]
    metrics_by_key: dict[str, Any] = {}
    for field in fields:
        if field not in row:
            continue
        metrics_by_key[field] = {
            "evidence_id": f"YF_{field.upper()}",
            "field": field,
            "date": row.get("date"),
            "value": row.get(field),
        }
    return {"metrics_by_key": metrics_by_key}


def _build_evidence_map(
    *,
    selected_summaries: list[dict[str, Any]],
    selected_raw: list[dict[str, Any]],
    financial_context: dict[str, Any],
    market_context: dict[str, Any],
) -> dict[str, Any]:
    evidence_map: dict[str, Any] = {}
    for item in selected_summaries:
        evidence_id = str(item.get("evidence_id") or "")
        if not evidence_id:
            continue
        output = item.get("output") or {}
        evidence_map[evidence_id] = {
            "source_domain": "news",
            "source_type": "period_summary",
            "period": item.get("period"),
            "period_summary": output.get("period_summary"),
            "issue_count": len(output.get("issues") or []),
        }

    for period_payload in selected_raw:
        for event in period_payload.get("events") or []:
            evidence_id = str(event.get("evidence_id") or "")
            if not evidence_id:
                continue
            evidence_map[evidence_id] = {
                "source_domain": "news",
                "source_type": "recent_raw_event",
                "period": period_payload.get("period"),
                "event_id": event.get("event_id"),
                "relation_type": event.get("relation_type"),
                "mention_count": event.get("mention_count"),
                "final_score": event.get("final_score"),
                "title": event.get("title"),
                "time": event.get("time"),
            }

    for metric_key, metric in (financial_context.get("metrics_by_key") or {}).items():
        evidence_id = str(metric.get("evidence_id") or "")
        if not evidence_id:
            continue
        evidence_map[evidence_id] = {
            "source_domain": "financial",
            "source_type": "dart_lightweight_metric",
            "metric_key": metric_key,
            "display_name": metric.get("display_name"),
            "metric_type": metric.get("metric_type"),
            "unit": metric.get("unit"),
            "comparability": _metric_comparability_summary(metric),
        }

    for field, metric in (market_context.get("metrics_by_key") or {}).items():
        evidence_id = str(metric.get("evidence_id") or "")
        if not evidence_id:
            continue
        evidence_map[evidence_id] = {
            "source_domain": "market",
            "source_type": "yfinance_market_summary_field",
            "field": field,
            "date": metric.get("date"),
            "value": metric.get("value"),
        }
    return evidence_map


def _with_comparability_flags(comparisons: dict[str, Any]) -> dict[str, Any]:
    output: dict[str, Any] = {}
    for key, comparison in comparisons.items():
        if not isinstance(comparison, dict):
            output[key] = comparison
            continue
        current_basis = str(comparison.get("current_basis") or "")
        previous_basis = str(comparison.get("previous_basis") or "")
        basis_mismatch = bool(current_basis and previous_basis and current_basis != previous_basis)
        output[key] = {
            **comparison,
            "basis_mismatch": basis_mismatch,
            "interpretation_warning": (
                "Current and previous periods use different bases; do not describe as clean YoY."
                if basis_mismatch
                else ""
            ),
        }
    return output


def _metric_comparability_summary(metric: dict[str, Any]) -> dict[str, Any]:
    comparisons = metric.get("comparisons") or {}
    mismatch_keys = [
        key
        for key, comparison in comparisons.items()
        if isinstance(comparison, dict) and comparison.get("basis_mismatch")
    ]
    return {
        "has_basis_mismatch": bool(mismatch_keys),
        "basis_mismatch_comparison_keys": mismatch_keys,
    }


def _classify_news_relation(
    event: dict[str, Any],
    company_name: str,
    *,
    product_terms: set[str] | None = None,
) -> str:
    text = f"{event.get('title') or ''} {event.get('snippet') or ''}".lower()
    company_aliases = _company_aliases(company_name)
    product_aliases = {term.lower() for term in (product_terms or set()) if term}
    market_context_terms = {"제네릭", "경쟁", "브라바탑", "대웅제약"}
    sector_terms = {"제약", "바이오", "신약", "임상", "의약품", "헬스케어"}

    if any(alias and alias in text for alias in company_aliases):
        return "direct_company"
    if any(term in text for term in product_aliases):
        return "partner_or_product"
    if any(term in text for term in market_context_terms):
        return "market_context"
    if any(term in text for term in sector_terms):
        return "sector_context"
    return "low_relevance"


def _company_aliases(company_name: str) -> set[str]:
    base = str(company_name or "").strip().lower()
    compact = re.sub(r"\s+", "", base)
    aliases = {base, compact}
    for removable in ("주식회사", "(주)", "㈜", "co.,ltd.", "co. ltd.", "corp.", "inc."):
        aliases.add(base.replace(removable, "").strip())
        aliases.add(compact.replace(removable.replace(" ", ""), "").strip())
    return {alias for alias in aliases if alias}


def _extract_product_terms(period_summaries: dict[str, Any], company_name: str) -> set[str]:
    """Extract company-specific product/business terms without fixed company assumptions."""

    stopwords = {
        "",
        "뉴스",
        "이슈",
        "실적",
        "성장",
        "개선",
        "확대",
        "사업",
        "프로젝트",
        "플랫폼",
        "서비스",
        "제품",
        str(company_name or "").strip(),
    }
    pattern = re.compile(
        r"([A-Za-z][A-Za-z0-9+._-]{2,}|[가-힣A-Za-z0-9+._-]{2,})"
        r"(?=\s*(?:제품|치료제|신약|서비스|플랫폼|솔루션|브랜드|사업|프로젝트))"
    )
    terms: set[str] = set()
    for item in period_summaries.get("period_results", []):
        output = item.get("output") if isinstance(item, dict) else {}
        if not isinstance(output, dict):
            continue
        texts = [str(output.get("period_summary") or "")]
        for issue in output.get("issues") or []:
            if isinstance(issue, dict):
                texts.append(str(issue.get("issue") or ""))
                texts.append(str(issue.get("rationale") or ""))
        for text in texts:
            for match in pattern.findall(text):
                term = match.strip()
                if term and term not in stopwords:
                    terms.add(term)
    return terms


def _month_window(as_of_date: date, count: int) -> list[str]:
    month_index = as_of_date.year * 12 + as_of_date.month - 1
    start_index = month_index - count + 1
    periods = []
    for idx in range(start_index, month_index + 1):
        year = idx // 12
        month = idx % 12 + 1
        periods.append(f"{year:04d}-{month:02d}")
    return periods


def _rewrite_basis_mismatch_phrases(output: dict[str, Any], input_payload: dict[str, Any]) -> list[str]:
    if not _has_dart_basis_mismatch(input_payload):
        return []
    blocks = output.get("analysis_blocks")
    if not isinstance(blocks, dict):
        return []
    changed = _rewrite_strings_in_place(
        blocks,
        replacements={
            "전년 대비": "기간 기준이 다른 단순 비교상",
            "YoY": "동일 기간",
        },
    )
    return []


def _strip_evidence_id_fields(value: Any) -> None:
    if isinstance(value, dict):
        for key in list(value.keys()):
            if key == "evidence_ids" or key.endswith("_evidence_ids"):
                value.pop(key, None)
                continue
            _strip_evidence_id_fields(value[key])
    elif isinstance(value, list):
        for item in value:
            _strip_evidence_id_fields(item)


def _rewrite_strings_in_place(value: Any, *, replacements: dict[str, str]) -> bool:
    changed = False
    if isinstance(value, dict):
        for key, item in list(value.items()):
            if isinstance(item, str):
                new_item = item
                for before, after in replacements.items():
                    new_item = new_item.replace(before, after)
                new_item = re.sub(r"전년\s+([^,.\s]+)\s*대비", r"2024년 연간 \1과 비교해", new_item)
                if new_item != item:
                    value[key] = new_item
                    changed = True
            else:
                changed = _rewrite_strings_in_place(item, replacements=replacements) or changed
    elif isinstance(value, list):
        for idx, item in enumerate(value):
            if isinstance(item, str):
                new_item = item
                for before, after in replacements.items():
                    new_item = new_item.replace(before, after)
                new_item = re.sub(r"전년\s+([^,.\s]+)\s*대비", r"2024년 연간 \1과 비교해", new_item)
                if new_item != item:
                    value[idx] = new_item
                    changed = True
            else:
                changed = _rewrite_strings_in_place(item, replacements=replacements) or changed
    return changed


def _has_dart_basis_mismatch(input_payload: dict[str, Any]) -> bool:
    metrics = (
        ((input_payload.get("cross_domain_context") or {}).get("financial") or {}).get("metrics_by_key")
        or {}
    )
    for metric in metrics.values():
        comparisons = metric.get("comparisons") if isinstance(metric, dict) else None
        if not isinstance(comparisons, dict):
            continue
        for comparison in comparisons.values():
            if isinstance(comparison, dict) and comparison.get("basis_mismatch"):
                return True
    return False


def _parse_json_content(content: str) -> tuple[Any, str | None]:
    try:
        return json.loads(content), None
    except json.JSONDecodeError as exc:
        return {"parse_error": "json_decode_error", "raw_content": content}, f"JSON parse warning: {exc}"


def _parse_date(value: str | None) -> date | None:
    if not value:
        return None
    digits = re.sub(r"\D", "", value)
    if len(digits) != 8:
        raise ValueError(f"Invalid date: {value}")
    return date(int(digits[:4]), int(digits[4:6]), int(digits[6:8]))


def _load_json(path: Path) -> Any:
    if not path.exists():
        raise FileNotFoundError(path)
    return json.loads(path.read_text(encoding="utf-8"))


def _load_env_file(path: Path) -> None:
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        key, value = stripped.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = value


if __name__ == "__main__":
    main()
