from __future__ import annotations

from shared.subdata import financial_subdata, market_subdata, news_subdata, secondary_context_for_llm
from shared.subdata_guidance import context_guidance, context_ref_schema, validate_context_refs, CONTEXT_POLICY_VERSION

import argparse
import copy
import json
import math
import os
import re
import time
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path
from typing import Any

from openai import OpenAI
from shared.evidence_contracts import (
    SECONDARY_CONTEXT_EFFECTS,
    SECONDARY_CONTEXT_USAGE,
    canonical_evidence_id,
    validate_evidence_catalog,
    validate_secondary_context_assessments,
)
from shared.domain_llm import domain_request, call_domain_response
from shared.news_selection import (MONTHLY_NEWS_POLICY, MONTHLY_NEWS_LABEL, ANNUAL_NEWS_LIMIT,
    SUMMARY_CITED_NEWS_POLICY, SUMMARY_CITED_NEWS_LABEL, validate_monthly_news)
from tqdm.auto import tqdm

from .io.storage import save_json


DEFAULT_MODEL = "gpt-5.4-mini"
DEFAULT_GRANULARITY = "month"
SUMMARY_MONTH_COUNT = 12
RECENT_RAW_MONTH_COUNT = 3
SUMMARY_DAY_COUNT = 14
RECENT_RAW_DAY_COUNT = 1
SUMMARY_WEEK_COUNT = 14
COMPANY_NEWS_TOP_K = ANNUAL_NEWS_LIMIT
DEFAULT_MAX_RAW_EVENTS_PER_PERIOD = COMPANY_NEWS_TOP_K
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

def _project_root() -> Path:
    return Path(__file__).resolve().parents[3]


@dataclass(frozen=True)
class AnalysisPaths:
    context_export_dir: Path
    context_manifest_path: Path
    period_summaries_path: Path
    summary_prompt_input_path: Path
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
        include_secondary_context=not args.primary_data_only,
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
    include_secondary_context: bool = True,
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
        include_secondary_context=include_secondary_context,
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
        help="Context export directory that contains the granularity folder.",
    )
    parser.add_argument("--granularity", default=DEFAULT_GRANULARITY, choices=["day", "week", "month"], help="Input granularity.")
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
        help="Compatibility cap for the globally ranked company-news input (maximum 20).",
    )
    parser.add_argument(
        "--primary-data-only",
        action="store_true",
        help="Use News evidence only and omit DART/market secondary context.",
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
    include_secondary_context: bool = True,
) -> dict[str, Any]:
    from shared.news_articles import ARTICLE_NEWS_POLICY, article_catalog
    top_news_input = _load_json(paths.recent_raw_path)
    article_only = (top_news_input.get("metadata") or {}).get("raw_news_policy") == ARTICLE_NEWS_POLICY
    period_summaries = {} if article_only else _load_json(paths.period_summaries_path)
    raw_policy = (top_news_input.get("metadata") or {}).get("raw_news_policy", "global_top_k_legacy")
    if raw_policy == MONTHLY_NEWS_POLICY:
        validate_monthly_news(top_news_input.get("events") or [], end_exclusive=as_of_date,
            time_of=lambda e: e.get("time", ""), id_of=lambda e: e.get("event_id", ""))
        if max_raw_events_per_period < ANNUAL_NEWS_LIMIT:
            raise ValueError("Monthly news requires a 24-event annual capacity; do not truncate to legacy top-20")
    selected_periods, summary_periods, raw_periods, summary_rule, raw_rule = _resolve_analysis_periods(paths, as_of_date)

    if article_only:
        selected_periods = raw_periods = [row["period"] for row in top_news_input["periods"]]
        summary_periods = []
        summary_rule = ""
        raw_rule = "All weekly selected articles; no summary or citation filter."
    selected_summaries = _select_period_summaries(period_summaries, summary_periods)
    selected_raw = _select_company_top_news(
        top_news_input,
        raw_periods,
        max_events=(len(top_news_input.get("events") or []) if raw_policy == SUMMARY_CITED_NEWS_POLICY or article_only
                    else min(max(max_raw_events_per_period, 1), COMPANY_NEWS_TOP_K)),
    )
    if raw_policy == SUMMARY_CITED_NEWS_POLICY:
        raw_ids = {(row["period"], event["event_id"]): event["evidence_id"]
                   for row in selected_raw for event in row["events"]}
        cited = set()
        for summary in selected_summaries:
            for issue in summary["issues"]:
                ids = [(summary["period"], str(key)) for key in issue["source_event_ids"]]
                if not ids or any(key not in raw_ids for key in ids):
                    raise ValueError("Summary citation missing from same-period raw news")
                issue["source_evidence_ids"] = [raw_ids[key] for key in ids]
                cited.update(ids)
        if cited != set(raw_ids) or len(raw_ids) != len(top_news_input.get("events") or []):
            raise ValueError("Cited-news input must contain exactly the unique articles cited by summaries")
    financial_context = (
        _compact_financial_context(_load_json(paths.dart_lightweight_path))
        if include_secondary_context
        else {"status": "unavailable", "evidence_catalog": {}}
    )
    market_context = (
        _compact_market_context(_load_json(paths.market_summary_path))
        if include_secondary_context
        else {"status": "unavailable", "evidence_catalog": {}}
    )
    evidence_map = _build_evidence_map(
        selected_raw=selected_raw,
        secondary_context={
            "financial": financial_context,
            "market": market_context,
        },
    )

    if article_only:
        evidence_map = {**article_catalog(top_news_input),
                        **financial_context["evidence_catalog"], **market_context["evidence_catalog"]}
    # Monthly evidence makes the whole annual context usable, with its summarized origin explicit.
    for summary in selected_summaries:
        key = canonical_evidence_id("news", "period_" + str(summary["period"]))
        summary["evidence_id"] = key
        evidence_map[key] = {
            "evidence_id": key, "domain": "news", "source_domain": "news", "origin_type": "model_summarized",
            "source_ref": "news_periods." + str(summary["period"]).replace("/", "_"),
            "source_date": summary.get("period_end") or str(summary["period"]).split("/")[-1],
            "period": summary["period"], "source_type": "monthly_news_context",
            "period_start": summary.get("period_start"), "period_end": summary.get("period_end"),
            "date_precision": "period",
            "title": "기간 뉴스 흐름", "snippet": summary.get("period_summary"),
            "source_event_ids": summary.get("source_event_ids") or [],
            "coverage": {"primary_source_present": False},
        }
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
            "raw_news_policy": raw_policy,
            "summary_periods": summary_periods,
            "recent_raw_periods": raw_periods,
            "selected_periods": selected_periods,
            "summary_rule": summary_rule,
            "recent_raw_rule": raw_rule,
            "max_raw_events_per_period": None if article_only else max_raw_events_per_period,
            "company_related_news_top_k": None if article_only or raw_policy == SUMMARY_CITED_NEWS_POLICY else COMPANY_NEWS_TOP_K,
            "secondary_context_enabled": include_secondary_context,
            "investment_decision_allowed": False,
        },
        "source_paths": {
            "context_export_manifest": str(paths.context_manifest_path),
            "period_summaries": "" if article_only else str(paths.period_summaries_path),
            "summary_prompt_input": "" if article_only else str(paths.summary_prompt_input_path),
            "recent_raw": str(paths.recent_raw_path),
            "dart_lightweight": str(paths.dart_lightweight_path),
            "market_summary": str(paths.market_summary_path),
        },
        "news_context": {
            "monthly_summaries": selected_summaries,
            "company_related_top_news": selected_raw,
        },
        "secondary_context": {
            "financial": financial_context,
            "market": market_context,
        },
        "evidence_map": evidence_map,
        "evidence_map_path": str(paths.evidence_map_path),
    }


def build_llm_request(*, input_payload: dict[str, Any], model: str) -> dict[str, Any]:
    from shared.news_articles import ARTICLE_NEWS_POLICY, ARTICLE_NEWS_LABEL, articles_for_llm
    article_only = input_payload["input_policy"].get("raw_news_policy") == ARTICLE_NEWS_POLICY
    primary_news_catalog = {
        evidence_id: _compact_news_evidence_for_llm(evidence)
        for evidence_id, evidence in (input_payload.get("evidence_map") or {}).items()
        if evidence.get("domain") == "news" and evidence.get("source_type") != "monthly_news_context"
    }
    monthly_summaries = [
        _compact_period_summary_for_llm(item)
        for item in (input_payload.get("news_context") or {}).get("monthly_summaries", [])
        if isinstance(item, dict)
    ]
    llm_input = {
        "target_entity": input_payload["target_entity"],
        "period_scope": {
            "summary_periods": input_payload["input_policy"].get("summary_periods", []),
            "recent_raw_periods": input_payload["input_policy"].get("recent_raw_periods", []),
        },
        "최근 1년 월별 요약 12개": monthly_summaries,
        ({MONTHLY_NEWS_POLICY: MONTHLY_NEWS_LABEL, SUMMARY_CITED_NEWS_POLICY: SUMMARY_CITED_NEWS_LABEL}
         .get(input_payload["input_policy"].get("raw_news_policy"), "기업 관련 뉴스 상위 20건")): primary_news_catalog,
        "secondary_context": _compact_secondary_context_for_llm(
            input_payload.get("secondary_context") or {}
        ),
        "secondary_context_contract": {
            "effects": sorted(SECONDARY_CONTEXT_EFFECTS),
            "usage": SECONDARY_CONTEXT_USAGE,
            "causal_assertions_allowed": False,
            "may_change_primary_evidence_status": False,
            "may_change_interpretation": True,
            "chronology_required": True,
        },
    }
    if article_only:
        primary_news_catalog = articles_for_llm({
            key: row for key, row in input_payload["evidence_map"].items() if row.get("domain") == "news"
        })
        llm_input.pop("최근 1년 월별 요약 12개")
        llm_input.pop("기업 관련 뉴스 상위 20건")
        llm_input["period_scope"] = {"raw_article_periods": input_payload["input_policy"]["recent_raw_periods"]}
        llm_input[ARTICLE_NEWS_LABEL] = [
            {"period": period, "articles": {
                key: row for key, row in primary_news_catalog.items()
                if input_payload["evidence_map"][key]["period"] == period}}
            for period in input_payload["input_policy"]["recent_raw_periods"]
        ]
    return domain_request({
        "model": model,
        "temperature": 0.2,
        "response_format": _analysis_response_format(input_payload),
        "messages": [
            {
                "role": "system",
                "content": (
                    "당신은 한국 상장사 뉴스 분석 에이전트입니다. "
                    "출력은 뉴스 사실·불확실성과 보조자료를 참고한 경제적 해석을 구분한 분석 handoff JSON입니다. "
                    "절대 buy/sell/hold, 매수/매도/보유, 목표주가, 투자판단, 투자 판단 시 같은 문구를 출력하지 마세요. "
                    "입력에 없는 사실이나 수치를 만들지 마세요. " +
                    ("news_only claim에는 입력의 NEWS_RAW evidence ID만 사용하세요. " if article_only else
                     "news_only claim에는 입력의 NEWS_RAW 또는 NEWS_PERIOD evidence ID만 사용하세요. ") +
                    "재무·시장 데이터는 secondary_context_assessment에서 뉴스의 의미·지속성·위험을 "
                    "해석하는 데 사용하고, 이를 overall_assessment에 반영하세요. "
                    "보조자료로 뉴스 사건 자체의 발생을 입증하거나 확인되지 않은 인과관계를 만들지 마세요. "
                    "뉴스 발생일과 재무자료의 대상 기간을 먼저 비교하세요. 재무자료가 뉴스보다 앞서면 "
                    "그 자료에 뉴스 효과가 나타나지 않는 것을 확인 불가나 부정적 신호로 해석하지 말고, "
                    "사건 발생 전의 수익성·현금창출력·재무여력을 바탕으로 사건의 의미를 해석하세요. "
                    "JSON key는 영어로 쓰고 분석 문장은 한국어로 작성하세요."
                ) + context_guidance("news"),
            },
            {
                "role": "user",
                "content": json.dumps(
                    {
                        "task": (
                            ("월 구간별로 배열된 최근 1년 기사를 확인하고 " if article_only else
                             f"{len(monthly_summaries)}개 월별 요약으로 최근 1년 뉴스 흐름을 파악하고 ") +
                            f"제공된 개별 뉴스 {len(primary_news_catalog)}건에서 직접 확인되는 "
                            "사건, 긍정·부정 신호, 위험, 불확실성을 정리하여 "
                            "각 claim에 직접 근거가 되는 evidence ID를 지정하세요. 보조자료와의 연결을 쟁점별로 평가하고 "
                            "overall_assessment에는 핵심 사업 변화와 반대 근거를 비교한 뉴스 종합 해석을 작성하세요."
                        ),
                        "analysis_rules": [
                            "news_only는 뉴스 데이터만 사용합니다.",
                            "월 구간은 자료 배열 단위이지 사건·실적 대상 기간이 아닙니다. 같은 월의 별개 사건을 합치지 않고, 후속 보도는 날짜와 근거를 유지해 연결합니다.",
                            ("anchor_evidence_id는 주장을 실제로 뒷받침하는 대표 기사의 NEWS_RAW ID입니다. 같은 월 또는 같은 기업의 기사라는 이유로 근거를 연결하지 않습니다." if article_only else
                             "anchor_evidence_id는 주장을 실제로 뒷받침하는 대표 근거입니다. 개별 기사에서 확인하면 NEWS_RAW를, 월별 요약에서만 확인하면 NEWS_PERIOD를 지정합니다. 같은 달 또는 같은 기업의 기사라는 이유로 대표 근거를 연결하지 않습니다."),
                            ("news_only claim에는 입력에 있는 NEWS_RAW ID만 사용합니다." if article_only else
                             "news_only claim에는 입력에 있는 NEWS_RAW 또는 NEWS_PERIOD ID만 사용합니다."),
                            "실적을 직접 보도한 기사의 수치와 증감은 보도된 실적으로 활용합니다. 계약·승인·계획에서 입력에 없는 매출·이익·EPS 개선을 만들어내지 않습니다.",
                            "실적을 설명할 때 기사 보도일과 실적 대상 기간을 구분하고, 기업명·분기/반기/연간·누적 또는 단일 기간·연결 또는 별도 기준을 자료에서 확인되는 범위로 명시합니다. 서로 다른 기간이나 기업의 실적을 하나의 실적 발표처럼 합치지 않습니다. 추세 비교가 필요하면 각 실적의 기간·주체·근거를 먼저 구분한 뒤 연결해 설명하며, 보도일이 다르다는 이유만으로 같은 실적의 후속 보도를 별도 사건으로 늘리지 않습니다. 보도일에서 실적 대상 기간을 추정하거나 서로 다른 기준의 수치를 직접 비교하지 않습니다.",
                            "각 실적 claim과 종합 해석의 실적 문장에는 수치·증감의 주체를 해당 기업명으로 명시합니다. 수치 바로 앞에 기업명을 적고 앞 문장의 기업명이나 문장 뒤의 '그룹 기여' 표현으로 대신하지 않습니다. 대상기업·지주회사·그룹·계열사의 실적과 연결·별도 기준을 서로 옮기지 않습니다. 제목의 기업명이 축약됐더라도 스니펫에 수치의 주체가 명시되어 있으면 이를 따릅니다. 기업명 유사성으로 지배관계를 추정하지 않습니다.",
                            "news_only.summary와 overall_assessment.summary에서도 서로 다른 기업의 연간·분기 실적을 한 기업의 연속 성장으로 합치지 않습니다. 같은 기업·회계 기준·비교 가능한 기간의 실적끼리 추세를 설명하고, 그룹 성과와 대상기업의 기여는 기업명을 각각 밝혀 연결합니다. company_specificity의 direct는 대상기업에 귀속되는 사실에 사용합니다. 관계사 성과와 대상기업 기여를 함께 다룬 혼합 주장에는 mixed를 사용하고, 기사에 대상기업 이름이 등장했다는 이유만으로 그룹 실적을 direct로 분류하지 않습니다.",
                            "대상기업의 사업 변화가 그룹 실적에 기여했다는 연결이 입력에 있으면 그 변화와 기여를 설명하고 대상기업의 사업 전망에 주는 의미를 분석합니다. 그룹 수치를 대상기업 수치로 바꾸거나, 입력에 없는 기여 원인·규모를 만들지 않습니다. 기업 구분을 확인하는 내부 검토 문구를 반복하기보다 확인된 사실과 투자 의미를 서술합니다. 주체나 기여 규모가 불명확해도 확인된 사업 변화까지 일반적인 불확실성으로 대체하지 않으며, 남는 불확실성이 판단을 실질적으로 제한할 때만 해당 범위에서 설명합니다.",
                            "기사에 없는 계약 금액, 일정, 상업화 성과, 재무 기여를 만들지 않습니다.",
                            "같은 사건을 여러 신호로 중복 작성하지 않습니다.",
                            "분석할 사건은 기존 사업 상태를 보강하거나 수정하는 정도와 근거의 직접성에 따라 선택합니다. 정기공시 이후의 실적·사업 변화도 해당 기업과 기간을 구분해 검토하고, 종합 의견을 바꾸지 않더라도 사업 전망을 이해하는 데 필요한 변화는 분석에 남깁니다. 최신 기사라는 이유만으로 우선하거나 모든 기사 사용·월별 건수·긍정과 부정의 균형을 맞추지 않습니다.",
                            "같은 기업·제품을 다룬 기사라도 실적 발표, 계약, 허가, 출시, 안전성 조치는 서로 다른 사건입니다. 하나의 쟁점으로 연결할 수 있지만 각 변화의 시점·발생 사실·직접 근거를 구분하고 일반적인 사업 확장 문구 하나로 대체하지 않습니다.",
                            "경제적 해석에서는 사건이 기존 사업의 판매 범위, 고객·제품 구성, 공급 능력, 비용 또는 실행 위험 중 무엇을 바꾸는지 설명합니다. 확인된 변화와 예상 영향 경로, 아직 판단할 수 없는 규모를 구분합니다. 영향 금액이 없다는 이유로 확인된 긍정·부정 사건의 의미를 상쇄하거나 일반적인 불확실성으로 대체하지 않습니다. 반대로 금액이 없는데 효과가 크거나 작다고 단정하지 않습니다. 연결 근거가 없으면 중요성을 만들어내지 않습니다.",
                            "event_timeline이 있으면 날짜별 제목을 시간순 진행 내역으로 읽되, 제목에 없는 변화나 인과관계를 추정하지 않습니다.",
                            ("각 claim의 상태는 실제 인용한 기사의 제목·스니펫·날짜별 진행 내역으로 확인되는 범위에서 분류합니다." if article_only else
                             "각 claim의 상태는 실제 인용한 자료 범위에서 분류합니다. NEWS_RAW는 제목·snippet·날짜별 진행 내역을, NEWS_PERIOD는 해당 기간 요약과 이슈를 사용합니다. 월별 요약을 인용하면서 다른 기사의 제목이나 snippet으로 상태를 대신 판단하지 않습니다."),
                            "materiality_status는 사건의 사업상 영향에 관한 상태입니다. observed는 입력에 구체적인 사업 변화나 성과가 확인됨, plausible_unquantified는 연결 경로는 타당하나 효과 규모는 확인되지 않음, not_established는 그 경로를 뒷받침할 자료가 없음, mixed는 이들이 섞인 경우입니다. 호재·악재 등급이 아닙니다.",
                            "financial_link_status의 observed는 해당 주장에 대응하는 실제 실적 또는 재무 영향이 인용 자료에 명시된 경우입니다. not_observed는 그런 연결이 확인되지 않은 경우이며 효과가 없다는 뜻이 아닙니다. not_applicable은 재무 연결을 주장하지 않는 내용입니다. 보도 실적을 공시 확정치로 바꾸지 않습니다.",
                            "기사의 전망이나 기대는 reported_expectation으로 두고 실제 발생 사실로 승격하지 않습니다.",
                            "reported_expectation은 전망·예상 실적을 뜻하며 '언론에 보도됨'이라는 뜻이 아닙니다. 이미 발생·발표된 실적은 내용에 따라 occurred 또는 announced로 구분합니다. 실적 발표와 전망은 본문뿐 아니라 상태 필드에서도 일치시킵니다.",
                            ("각 수치·일정·사실을 실제로 담고 있는 기사 ID를 인용합니다. 여러 기사를 함께 쓰면 각각이 뒷받침하는 내용을 claim에서 구분합니다." if article_only else
                             "각 수치·일정·사실을 실제로 담고 있는 근거 ID를 인용합니다. 기사와 월별 요약을 함께 쓰면 각각이 뒷받침하는 내용을 claim에서 구분합니다. 요약의 출처 기사 목록은 요약 생성에 사용된 자료이지, 모든 기사가 모든 주장을 입증한다는 뜻은 아닙니다."),
                            "산업 일반 기사는 company_specificity=industry_context로 두며 회사 직접 위험으로 확대하지 않습니다.",
                            "secondary_context_assessment의 judgment_impact에는 어떤 뉴스 사건의 중요도·지속성·위험 해석이 어떤 재무·시장 관측 때문에 강화·약화·수정되거나 유지되는지 적습니다. 의미 있는 연결은 overall_assessment.summary에서도 사건과 관측의 경제적 관계로 설명하고 해당 context_ids를 연결합니다. 단순히 '참고했다'고 쓰지 않으며, 관련 보조자료가 없으면 연결이나 판단 변화를 만들지 않습니다.",
                            "재무자료의 대상 기간이 뉴스 발생일보다 앞서면 후행 사건의 재무 효과를 입증하거나 반박하는 자료로 사용하지 않습니다.",
                            "overall_assessment.summary는 가장 중요한 사업 변화와 이를 약화하는 반대 근거가 있으면 함께 비교합니다. 기사·신호의 건수가 아니라 대상 사업과의 연결, 발생 여부, 영향의 지속성을 입력이 뒷받침하는 범위에서 검토하고 어느 근거를 더 중요하게 보는지 이유를 설명합니다. 긍정·부정 근거에 같은 기준을 적용하며, 단순 나열이나 기계적인 상쇄로 끝내지 않습니다. 반대 근거가 없으면 만들어내지 않고, 우열을 정할 근거가 부족하면 무엇을 판단할 수 있고 무엇이 남는지 설명합니다. 종합 해석에 실제 사용한 뉴스 근거를 primary_evidence_ids에 연결하되 투자 등급·목표가격은 제시하지 않습니다.",
                        ],
                        "input_payload": llm_input,
                    },
                    ensure_ascii=False,
                ),
            },
        ],
    }, domain="news")


def _compact_news_evidence_for_llm(evidence: dict[str, Any]) -> dict[str, Any]:
    result = {
        key: evidence.get(key)
        for key in (
            "source_date",
            "title",
            "snippet",
            "source",
            "relation_type",
            "mention_count",
            "event_timeline",
        )
        if evidence.get(key) not in (None, "", [], {})
    }
    # Retain publisher identities and primary-source status, not collection QA counters.
    coverage = {key: copy.deepcopy(value) for key, value in (evidence.get("coverage") or {}).items()
                if key not in {"article_count", "unique_publisher_count", "deduplicated_article_count", "coverage_quality"}}
    if coverage:
        result["coverage"] = coverage
    return result


def _compact_event_timeline(event: dict[str, Any]) -> list[dict[str, str]]:
    timeline = [
        {
            "date": str(item.get("date") or "").strip(),
            "title": str(item.get("title") or "").strip(),
        }
        for item in event.get("event_timeline") or []
        if isinstance(item, dict)
        and str(item.get("date") or "").strip()
        and str(item.get("title") or "").strip()
    ]
    if len(timeline) <= 1:
        return []
    return sorted(timeline, key=lambda item: (item["date"], item["title"]))


def _compact_period_summary_for_llm(summary: dict[str, Any]) -> dict[str, Any]:
    issues = []
    for raw in summary.get("issues") or []:
        if not isinstance(raw, dict) or not str(raw.get("summary") or raw.get("issue") or "").strip():
            continue
        issues.append(
            {
                key: raw.get(key)
                for key in ("summary", "issue", "importance", "source_evidence_ids")
                if raw.get(key) not in (None, "", [], {})
            }
        )
    return {
        key: value
        for key, value in {
            "evidence_id": summary.get("evidence_id"),
            "origin_type": "model_summarized",
            "period": summary.get("period"),
            "period_summary": (None if any("summary" in issue for issue in issues)
                               else summary.get("period_summary")),
            "period_start": summary.get("period_start"),
            "period_end": summary.get("period_end"),
            "issues": issues,
        }.items()
        if value not in (None, "", [], {})
    }


def _compact_secondary_context_for_llm(contexts: dict[str, Any]) -> dict[str, Any]:
    """Preserve all observations and citation keys, without local audit metadata."""
    return secondary_context_for_llm(contexts)


def _analysis_response_format(input_payload: dict[str, Any]) -> dict[str, Any]:
    evidence_map = input_payload.get("evidence_map") or {}
    primary_ids = sorted(
        evidence_id
        for evidence_id, evidence in evidence_map.items()
        if isinstance(evidence, dict) and evidence.get("domain") == "news"
    )
    secondary_ids = sorted(
        evidence_id
        for evidence_id, evidence in evidence_map.items()
        if isinstance(evidence, dict) and evidence.get("domain") in {"financial", "market"}
    )
    if not primary_ids:
        raise ValueError("News analysis requires at least one primary News evidence ID.")

    string_array = {"type": "array", "items": {"type": "string"}}
    primary_id_array = {
        "type": "array",
        "items": {"$ref": "#/$defs/primary_evidence_id"},
    }
    secondary_id_array = {
        "type": "array",
        "items": {"$ref": "#/$defs/secondary_evidence_id"},
    }
    claim = {
        "type": "object",
        "properties": {
            "claim": {"type": "string"},
            "anchor_evidence_id": {"$ref": "#/$defs/primary_evidence_id"},
            "evidence_ids": primary_id_array,
            "event_status": {
                "type": "string",
                "enum": ["occurred", "announced", "reported_expectation", "allegation", "mixed", "insufficient"],
            },
            "company_specificity": {
                "type": "string",
                "enum": ["direct", "product_direct", "industry_context", "mixed", "insufficient"],
            },
            "materiality_status": {
                "type": "string",
                "enum": ["observed", "plausible_unquantified", "not_established", "mixed"],
            },
            "financial_link_status": {
                "type": "string",
                "enum": ["observed", "not_observed", "not_applicable"],
            },
        },
        "required": [
            "claim",
            "anchor_evidence_id",
            "evidence_ids",
            "event_status",
            "company_specificity",
            "materiality_status",
            "financial_link_status",
        ],
        "additionalProperties": False,
    }
    news_only = {
        "type": "object",
        "properties": {
            "summary": {"$ref": "#/$defs/news_claim"},
            "positive_signals": {"type": "array", "items": {"$ref": "#/$defs/news_claim"}},
            "negative_signals": {"type": "array", "items": {"$ref": "#/$defs/news_claim"}},
            "key_risks": {"type": "array", "items": {"$ref": "#/$defs/news_claim"}},
            "uncertainties": {"type": "array", "items": {"$ref": "#/$defs/news_claim"}},
        },
        "required": [
            "summary",
            "positive_signals",
            "negative_signals",
            "key_risks",
            "uncertainties",
        ],
        "additionalProperties": False,
    }
    context_assessment = {
        "type": "object",
        "properties": {
            "context_id": {"type": "string"},
            "source_domain": {"type": "string", "enum": ["financial", "market"]},
            "effect": {"type": "string", "enum": sorted(SECONDARY_CONTEXT_EFFECTS)},
            "statement": {"type": "string"},
            "judgment_impact": {"type": "string"},
            "primary_anchor_evidence_id": {"$ref": "#/$defs/primary_evidence_id"},
            "primary_evidence_ids": primary_id_array,
            "secondary_anchor_evidence_id": {"$ref": "#/$defs/secondary_evidence_id"},
            "secondary_evidence_ids": secondary_id_array,
            "usage": {"type": "string", "enum": [SECONDARY_CONTEXT_USAGE]},
            "limitation": {"type": "string", "description": "해당 해석을 실질적으로 제한하는 사항만 작성하며 없으면 빈 문자열"},
        },
        "required": [
            "context_id",
            "source_domain",
            "effect",
            "statement",
            "judgment_impact",
            "primary_anchor_evidence_id",
            "primary_evidence_ids",
            "secondary_anchor_evidence_id",
            "secondary_evidence_ids",
            "usage",
            "limitation",
        ],
        "additionalProperties": False,
    }
    schema = {
        "type": "object",
        "properties": {
            "agent_name": {"type": "string", "enum": ["News Agent"]},
            "output_version": {"type": "string", "enum": ["2.0"]},
            "output_mode": {"type": "string", "enum": ["analysis_handoff"]},
            "target_entity": {
                "type": "object",
                "properties": {
                    "company_name": {"type": "string"},
                    "ticker": {"type": "string"},
                    "corp_code": {"type": "string"},
                    "as_of_date": {"type": "string"},
                },
                "required": ["company_name", "ticker", "corp_code", "as_of_date"],
                "additionalProperties": False,
            },
            "input_summary": {
                "type": "object",
                "properties": {
                    "summary_periods": string_array,
                    "recent_raw_periods": string_array,
                },
                "required": ["summary_periods", "recent_raw_periods"],
                "additionalProperties": False,
            },
            "analysis_blocks": {
                "type": "object",
                "properties": {"news_only": news_only},
                "required": ["news_only"],
                "additionalProperties": False,
            },
            "secondary_context_assessment": {
                "type": "array",
                "items": {"$ref": "#/$defs/context_assessment"},
                "minItems": 0,
                **({"maxItems": 0} if not secondary_ids else {}),
            },
            "overall_assessment": {
                "type": "object",
                "properties": {
                    "summary": {"type": "string"},
                    "primary_evidence_ids": {**primary_id_array, "minItems": 1},
                    "context_ids": context_ref_schema(),
                },
                "required": ["summary", "primary_evidence_ids", "context_ids"],
                "additionalProperties": False,
            },
        },
        "required": [
            "agent_name",
            "output_version",
            "output_mode",
            "target_entity",
            "input_summary",
            "analysis_blocks",
            "secondary_context_assessment",
            "overall_assessment",
        ],
        "additionalProperties": False,
        "$defs": {
            "primary_evidence_id": {"type": "string", "enum": primary_ids},
            "secondary_evidence_id": (
                {"type": "string", "enum": secondary_ids}
                if secondary_ids
                else {"type": "string"}
            ),
            "news_claim": claim,
            "context_assessment": context_assessment,
        },
    }
    return {
        "type": "json_schema",
        "json_schema": {
            "name": "news_analysis_handoff",
            "strict": True,
            "schema": schema,
        },
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

    started_at = time.monotonic()
    response = call_domain_response(llm_request, step="news:analysis", timeout_seconds=timeout_seconds)
    elapsed_seconds = time.monotonic() - started_at
    content = response.output_text or ""
    parsed_output, parse_warning = _parse_json_content(content)
    if isinstance(parsed_output, dict) and not parse_warning:
        _merge_analysis_anchor_evidence_ids(parsed_output)
        _validate_news_analysis_output(parsed_output, input_payload)
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


def _merge_analysis_anchor_evidence_ids(output: dict[str, Any]) -> None:
    news_only = ((output.get("analysis_blocks") or {}).get("news_only") or {})
    claim_items = [news_only.get("summary")]
    for key in ("positive_signals", "negative_signals", "key_risks", "uncertainties"):
        claim_items.extend(news_only.get(key) or [])
    for item in claim_items:
        if not isinstance(item, dict):
            continue
        anchor = str(item.get("anchor_evidence_id") or "").strip()
        evidence_ids = item.get("evidence_ids") if isinstance(item.get("evidence_ids"), list) else []
        item["evidence_ids"] = list(
            dict.fromkeys([value for value in [anchor, *map(str, evidence_ids)] if value])
        )

    for item in output.get("secondary_context_assessment") or []:
        if not isinstance(item, dict):
            continue
        for anchor_key, ids_key in (
            ("primary_anchor_evidence_id", "primary_evidence_ids"),
            ("secondary_anchor_evidence_id", "secondary_evidence_ids"),
        ):
            anchor = str(item.get(anchor_key) or "").strip()
            evidence_ids = item.get(ids_key) if isinstance(item.get(ids_key), list) else []
            item[ids_key] = list(
                dict.fromkeys([value for value in [anchor, *map(str, evidence_ids)] if value])
            )


def _validate_news_analysis_output(
    output: dict[str, Any],
    input_payload: dict[str, Any],
) -> None:
    evidence_map = input_payload.get("evidence_map") or {}
    primary_ids = {
        evidence_id
        for evidence_id, evidence in evidence_map.items()
        if isinstance(evidence, dict) and evidence.get("domain") == "news"
    }
    blocks = output.get("analysis_blocks") or {}
    if not isinstance(blocks, dict) or set(blocks) != {"news_only"}:
        raise ValueError("News output must contain only analysis_blocks.news_only.")
    news_only = blocks.get("news_only") or {}
    if not isinstance(news_only, dict):
        raise ValueError("analysis_blocks.news_only must be an object.")

    claim_items: list[Any] = [news_only.get("summary")]
    for key in ("positive_signals", "negative_signals", "key_risks", "uncertainties"):
        values = news_only.get(key)
        if not isinstance(values, list):
            raise ValueError(f"news_only.{key} must be an array.")
        claim_items.extend(values)
    for item in claim_items:
        if not isinstance(item, dict) or not str(item.get("claim") or "").strip():
            raise ValueError("Each News claim must contain claim and evidence_ids.")
        evidence_ids = item.get("evidence_ids")
        if not isinstance(evidence_ids, list) or not evidence_ids:
            raise ValueError("Each News claim must cite at least one raw News evidence ID.")
        if any(str(evidence_id) not in primary_ids for evidence_id in evidence_ids):
            raise ValueError("News claim cited non-News or unknown primary evidence.")
        item["evidence_ids"] = list(dict.fromkeys(str(value) for value in evidence_ids))
        _validate_news_claim_metadata(item)

    secondary_catalog = {
        evidence_id: evidence
        for evidence_id, evidence in evidence_map.items()
        if isinstance(evidence, dict) and evidence.get("domain") in {"financial", "market"}
    }
    output["secondary_context_assessment"] = validate_secondary_context_assessments(
        output.get("secondary_context_assessment"),
        primary_evidence_ids=primary_ids,
        secondary_catalog=secondary_catalog,
        allowed_source_domains={"financial", "market"},
    )
    overall = output.get("overall_assessment")
    if overall is not None:
        refs = overall.get("primary_evidence_ids")
        if not isinstance(refs, list) or not refs or any(ref not in primary_ids for ref in refs):
            raise ValueError("News overall assessment must reference News primary evidence")
        validate_context_refs(overall, output["secondary_context_assessment"])
    output["context_policy_version"] = CONTEXT_POLICY_VERSION
    output["secondary_context"] = copy.deepcopy(input_payload.get("secondary_context") or {})


def _validate_news_claim_metadata(item: dict[str, Any]) -> None:
    allowed = {
        "event_status": {"occurred", "announced", "reported_expectation", "allegation", "mixed", "insufficient"},
        "company_specificity": {"direct", "product_direct", "industry_context", "mixed", "insufficient"},
        "materiality_status": {"observed", "plausible_unquantified", "not_established", "mixed"},
        "financial_link_status": {"observed", "not_observed", "not_applicable"},
    }
    for key, values in allowed.items():
        if item.get(key) not in values:
            raise ValueError(f"Invalid News claim {key}: {item.get(key)!r}")


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
    summary_prompt_input_path = context_export_dir / granularity / "summary_prompt_input.json"
    recent_raw_path = context_export_dir / granularity / "recent_raw_input.json"
    context_manifest_path = context_export_dir / granularity / "context_export_manifest.json"
    from shared.news_articles import ARTICLE_NEWS_POLICY
    manifest = _load_json_if_exists(context_manifest_path)
    if (manifest.get("metadata") or {}).get("raw_news_policy") == ARTICLE_NEWS_POLICY:
        recent_raw_path = context_export_dir / granularity / "selected_articles.json"
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
        context_manifest_path=context_manifest_path.resolve(),
        period_summaries_path=period_summaries_path.resolve(),
        summary_prompt_input_path=summary_prompt_input_path.resolve(),
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
        for item in _period_summary_items(payload)
        if isinstance(item, dict) and item.get("period")
    }
    selected: list[dict[str, Any]] = []
    for period in periods:
        item = by_period.get(period)
        if not item:
            continue
        selected.append(
            {
                "summary_id": f"NEWS_CONTEXT_{period}",
                "period": period,
                "period_summary": item.get("period_summary") or "\n".join(
                    str(issue.get("summary") or "") for issue in item.get("issues") or []),
                "period_start": item.get("period_start"),
                "period_end": item.get("period_end"),
                "source_event_ids": item.get("source_event_ids") or list(dict.fromkeys(
                    str(key) for issue in item.get("issues") or [] for key in issue.get("source_event_ids") or [])),
                "issues": copy.deepcopy(item.get("issues") or []),
            }
        )
    return selected


def _period_summary_items(payload: dict[str, Any]) -> list[dict[str, Any]]:
    output = payload.get("output") if isinstance(payload.get("output"), dict) else {}
    if isinstance(output.get("periods"), list):
        return [item for item in output["periods"] if isinstance(item, dict)]
    items: list[dict[str, Any]] = []
    for result in payload.get("period_results", []):
        if not isinstance(result, dict) or result.get("status") not in {None, "success"}:
            continue
        value = result.get("output") if isinstance(result.get("output"), dict) else result
        if isinstance(value, dict):
            items.append(value)
    return items


def _select_recent_raw_events(
    payload: dict[str, Any],
    periods: list[str],
    *,
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
            compact_event = {
                "evidence_id": f"NEWS_RAW_{period}_{event_id}",
                "event_id": event_id,
                "mention_count": int(event.get("mention_count") or 0),
                "title": str(event.get("title") or ""),
                "snippet": str(event.get("snippet") or ""),
                "source": str(event.get("source") or ""),
                "time": str(event.get("time") or ""),
                "final_score": float(event.get("final_score") or 0.0),
                "coverage": copy.deepcopy(event.get("coverage") or {}),
            }
            event_timeline = _compact_event_timeline(event)
            if event_timeline:
                compact_event["event_timeline"] = event_timeline
            if event.get("relation_type"):
                compact_event["relation_type"] = str(event["relation_type"])
            compact_events.append(compact_event)
        selected.append(
            {
                "period": period,
                "source_event_count": len(events),
                "included_event_count": len(compact_events),
                "events": compact_events,
            }
        )
    return selected


def _select_company_top_news(
    payload: dict[str, Any],
    periods: list[str],
    *,
    max_events: int,
) -> list[dict[str, Any]]:
    """Select one globally ranked company-news list, not a per-day quota."""

    allowed_periods = set(periods)
    granularity = str((payload.get("metadata") or {}).get("granularity") or "day")
    flat_events = [
        event
        for event in payload.get("events", [])
        if isinstance(event, dict)
    ]
    if not flat_events:
        legacy = _select_recent_raw_events(
            payload,
            periods,
            max_events_per_period=max_events,
        )
        flat_events = [
            event
            for period_payload in legacy
            for event in period_payload.get("events") or []
            if isinstance(event, dict)
        ]
        if flat_events and all(
            int(event.get("relevance_rank") or 0) > 0 for event in flat_events
        ):
            flat_events.sort(key=lambda event: int(event["relevance_rank"]))

    selected: list[dict[str, Any]] = []
    for event in flat_events:
        event_period = _event_period(event, granularity=granularity)
        if not event_period or (allowed_periods and event_period not in allowed_periods):
            continue
        event_id = str(event.get("event_id") or "")
        if not event_id:
            continue
        compact_event = {
            "evidence_id": f"NEWS_RAW_{event_period}_{event_id}",
            "event_id": event_id,
            "relevance_rank": int(event.get("relevance_rank") or 0),
            "mention_count": int(event.get("mention_count") or 0),
            "title": str(event.get("title") or ""),
            "snippet": str(event.get("snippet") or ""),
            "source": str(event.get("source") or ""),
            "time": str(event.get("time") or ""),
            "final_score": float(event.get("final_score") or 0.0),
            "coverage": copy.deepcopy(event.get("coverage") or {}),
        }
        event_timeline = _compact_event_timeline(event)
        if event_timeline:
            compact_event["event_timeline"] = event_timeline
        if event.get("relation_type"):
            compact_event["relation_type"] = str(event["relation_type"])
        selected.append(
            {
                "period": event_period,
                "source_event_count": 1,
                "included_event_count": 1,
                "events": [compact_event],
            }
        )
        if len(selected) >= max(max_events, 1):
            break
    return selected


def _event_period(event: dict[str, Any], *, granularity: str = "day") -> str:
    if event.get("period"):
        return str(event["period"])
    value = str(event.get("time") or "")
    try:
        parsed = date.fromisoformat(value[:10])
    except ValueError:
        return ""
    if granularity == "month":
        return parsed.strftime("%Y-%m")
    if granularity == "week":
        iso_year, iso_week, _ = parsed.isocalendar()
        return f"{iso_year:04d}-W{iso_week:02d}"
    return parsed.isoformat()


def _compact_financial_context(payload: dict[str, Any]) -> dict[str, Any]:
    return financial_subdata(payload)


def _compact_market_context(payload: Any) -> dict[str, Any]:
    return market_subdata(payload)


def _build_evidence_map(
    *,
    selected_raw: list[dict[str, Any]],
    secondary_context: dict[str, Any],
) -> dict[str, Any]:
    evidence_map: dict[str, Any] = {}
    for period_payload in selected_raw:
        for event in period_payload.get("events") or []:
            evidence_id = str(event.get("evidence_id") or "")
            if not evidence_id:
                continue
            evidence_map[evidence_id] = {
                "evidence_id": evidence_id,
                "domain": "news",
                "source_domain": "news",
                "origin_type": "raw_source",
                "source_ref": f"news_events.{period_payload.get('period')}.{event.get('event_id')}",
                "source_date": event.get("time") or period_payload.get("period"),
                "source_type": "recent_raw_event",
                "period": period_payload.get("period"),
                "event_id": event.get("event_id"),
                "relation_type": event.get("relation_type"),
                "mention_count": event.get("mention_count"),
                "relevance_rank": event.get("relevance_rank"),
                "final_score": event.get("final_score"),
                "title": event.get("title"),
                "snippet": event.get("snippet"),
                "source": event.get("source"),
                "coverage": copy.deepcopy(event.get("coverage") or {}),
                "event_timeline": copy.deepcopy(event.get("event_timeline") or []),
                "time": event.get("time"),
            }

    for context in secondary_context.values():
        if not isinstance(context, dict):
            continue
        for evidence_id, evidence in (context.get("evidence_catalog") or {}).items():
            evidence_map[evidence_id] = evidence

    validate_evidence_catalog(evidence_map)
    return evidence_map


def _first_comparison(comparisons: dict[str, Any]) -> dict[str, Any]:
    for comparison in comparisons.values():
        if isinstance(comparison, dict) and comparison.get("status") in {None, "ok"}:
            return comparison
    return {}


def _finite_number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(float(value))


def _market_unit(metric: str) -> str:
    if metric in {"stock_close", "kospi_close", "fx_close"}:
        return "price"
    if "rsi" in metric or "volume_ratio" in metric:
        return "index"
    if any(token in metric for token in ("return", "strength", "volatility", "to_ma", "obv", "bb_width")):
        return "ratio"
    return "number"


def _resolve_analysis_periods(
    paths: AnalysisPaths,
    as_of_date: date,
) -> tuple[list[str], list[str], list[str], str, str]:
    manifest = _load_json_if_exists(paths.context_manifest_path)
    if manifest:
        selected_periods = _string_list(manifest.get("selected_periods"))
        summary_periods = _string_list(manifest.get("summary_periods_for_news_agent"))
        raw_periods = _string_list(manifest.get("raw_periods_for_news_agent"))
        if selected_periods or summary_periods or raw_periods:
            return (
                selected_periods,
                summary_periods,
                raw_periods,
                "Use LLM period summaries selected by context_export_manifest.json.",
                "Use raw news events selected by context_export_manifest.json.",
            )

    granularity = paths.context_manifest_path.parent.name
    if granularity == "day":
        period_keys = _day_window(as_of_date, SUMMARY_DAY_COUNT)
        raw_periods = period_keys[-RECENT_RAW_DAY_COUNT:]
        summary_periods = period_keys[:-RECENT_RAW_DAY_COUNT]
        return (
            period_keys,
            summary_periods,
            raw_periods,
            "Use daily LLM summaries for the older 13 days in the 14-day window.",
            "Use raw news events for the latest 1 day in the 14-day window.",
        )

    if granularity == "week":
        period_keys = _week_window(as_of_date, SUMMARY_WEEK_COUNT)
        return (
            period_keys,
            period_keys,
            period_keys,
            "Use weekly LLM summaries for the 90-day news window.",
            "Use the globally ranked raw news events selected from all weeks.",
        )

    from shared.time_windows import monthly_windows
    period_keys = [window["period"] for window in monthly_windows(as_of_date, SUMMARY_MONTH_COUNT)]
    return (period_keys, period_keys, period_keys,
            "Use twelve monthly summaries for the complete annual window.",
            "Use globally ranked raw news events from the complete annual window.")


def _string_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return [str(item) for item in value if str(item or "").strip()]


def _month_window(as_of_date: date, count: int) -> list[str]:
    month_index = as_of_date.year * 12 + as_of_date.month - 1
    start_index = month_index - count + 1
    periods = []
    for idx in range(start_index, month_index + 1):
        year = idx // 12
        month = idx % 12 + 1
        periods.append(f"{year:04d}-{month:02d}")
    return periods


def _day_window(as_of_date: date, count: int) -> list[str]:
    start = as_of_date - timedelta(days=max(1, count) - 1)
    return [(start + timedelta(days=offset)).isoformat() for offset in range(max(1, count))]


def _week_window(as_of_date: date, count: int) -> list[str]:
    periods: list[str] = []
    for offset in range(max(1, count) - 1, -1, -1):
        current = as_of_date - timedelta(weeks=offset)
        iso_year, iso_week, _ = current.isocalendar()
        periods.append(f"{iso_year:04d}-W{iso_week:02d}")
    return periods


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


def _load_json_if_exists(path: Path) -> Any:
    if not path.exists():
        return None
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
