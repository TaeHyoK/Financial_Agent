from __future__ import annotations

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
from shared.llm_clients import execute_with_telemetry
from tqdm.auto import tqdm

from .io.storage import save_json


DEFAULT_MODEL = "gpt-5.4-mini"
DEFAULT_GRANULARITY = "day"
SUMMARY_MONTH_COUNT = 12
RECENT_RAW_MONTH_COUNT = 3
SUMMARY_DAY_COUNT = 14
RECENT_RAW_DAY_COUNT = 1
DEFAULT_MAX_RAW_EVENTS_PER_PERIOD = 40
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
    parser.add_argument("--granularity", default=DEFAULT_GRANULARITY, choices=["day", "month"], help="Input granularity.")
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
    period_summaries = _load_json(paths.period_summaries_path)
    summary_prompt_input = _load_json(paths.summary_prompt_input_path)
    recent_raw = _load_json(paths.recent_raw_path)
    selected_periods, summary_periods, raw_periods, summary_rule, raw_rule = _resolve_analysis_periods(paths, as_of_date)

    selected_summaries = _select_period_summaries(period_summaries, summary_periods)
    summary_raw = _select_recent_raw_events(
        summary_prompt_input,
        summary_periods,
        max_events_per_period=max_raw_events_per_period,
    )
    selected_raw = _select_recent_raw_events(
        recent_raw,
        raw_periods,
        max_events_per_period=max_raw_events_per_period,
    )
    source_ids_by_period = {
        str(item.get("period") or ""): [
            str(event.get("evidence_id"))
            for event in item.get("events") or []
            if event.get("evidence_id")
        ]
        for item in summary_raw
    }
    for summary in selected_summaries:
        summary["source_evidence_ids"] = source_ids_by_period.get(str(summary.get("period") or ""), [])
    all_raw = [*summary_raw, *selected_raw]
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
        selected_raw=all_raw,
        secondary_context={
            "financial": financial_context,
            "market": market_context,
        },
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
            "selected_periods": selected_periods,
            "summary_rule": summary_rule,
            "recent_raw_rule": raw_rule,
            "max_raw_events_per_period": max_raw_events_per_period,
            "secondary_context_enabled": include_secondary_context,
            "investment_decision_allowed": False,
        },
        "source_paths": {
            "context_export_manifest": str(paths.context_manifest_path),
            "period_summaries": str(paths.period_summaries_path),
            "summary_prompt_input": str(paths.summary_prompt_input_path),
            "recent_raw": str(paths.recent_raw_path),
            "dart_lightweight": str(paths.dart_lightweight_path),
            "market_summary": str(paths.market_summary_path),
        },
        "news_context": {
            "older_period_summaries": selected_summaries,
            "recent_raw_events": selected_raw,
        },
        "secondary_context": {
            "financial": financial_context,
            "market": market_context,
        },
        "evidence_map": evidence_map,
        "evidence_map_path": str(paths.evidence_map_path),
    }


def build_llm_request(*, input_payload: dict[str, Any], model: str) -> dict[str, Any]:
    primary_news_catalog = {
        evidence_id: _compact_news_evidence_for_llm(evidence)
        for evidence_id, evidence in (input_payload.get("evidence_map") or {}).items()
        if evidence.get("domain") == "news"
    }
    llm_input = {
        "target_entity": input_payload["target_entity"],
        "period_scope": {
            "summary_periods": input_payload["input_policy"].get("summary_periods", []),
            "recent_raw_periods": input_payload["input_policy"].get("recent_raw_periods", []),
        },
        "period_summaries": [
            _compact_period_summary_for_llm(item)
            for item in (input_payload.get("news_context") or {}).get("older_period_summaries", [])
            if isinstance(item, dict)
        ],
        "primary_news_evidence_catalog": primary_news_catalog,
        "secondary_context": _compact_secondary_context_for_llm(
            input_payload.get("secondary_context") or {}
        ),
        "secondary_context_contract": {
            "effects": sorted(SECONDARY_CONTEXT_EFFECTS),
            "usage": SECONDARY_CONTEXT_USAGE,
            "causal_assertions_allowed": False,
            "may_change_primary_evidence_status": False,
        },
    }
    return {
        "model": model,
        "temperature": 0.2,
        "response_format": _analysis_response_format(input_payload),
        "messages": [
            {
                "role": "system",
                "content": (
                    "당신은 한국 상장사 뉴스 분석 에이전트입니다. "
                    "출력은 뉴스 도메인 사실과 불확실성만 담는 분석 handoff JSON입니다. "
                    "절대 buy/sell/hold, 매수/매도/보유, 목표주가, 투자판단, 투자 판단 시 같은 문구를 출력하지 마세요. "
                    "입력에 없는 사실이나 수치를 만들지 마세요. "
                    "news_only claim에는 NEWS_RAW evidence ID만 사용하세요. "
                    "재무·시장 데이터는 secondary_context_assessment에서 정합성이나 충돌 여부만 평가하고, "
                    "뉴스 사건의 직접 증거나 원인으로 사용하지 마세요. 인과관계를 만들지 마세요. "
                    "JSON key는 영어로 쓰고 분석 문장은 한국어로 작성하세요."
                ),
            },
            {
                "role": "user",
                "content": json.dumps(
                    {
                        "task": (
                            "뉴스 원 이벤트에서 직접 확인되는 사건, 긍정·부정 신호, 위험, 불확실성을 정리하고 "
                            "각 claim에 원 뉴스 evidence ID를 지정하세요. 보조 재무·시장 문맥은 별도 assessment로만 평가하세요."
                        ),
                        "analysis_rules": [
                            "news_only는 뉴스 데이터만 사용합니다.",
                            "period summary는 탐색 문맥이며 evidence가 아닙니다. claim은 NEWS_RAW ID로 뒷받침합니다.",
                            "뉴스만으로 매출, 이익, EPS 개선을 단정하지 않습니다.",
                            "기사에 없는 계약 금액, 일정, 상업화 성과, 재무 기여를 만들지 않습니다.",
                            "같은 사건을 여러 신호로 중복 작성하지 않습니다.",
                            "각 claim의 event_status, company_specificity, materiality_status, financial_link_status를 제목과 snippet 범위 안에서 분류합니다.",
                            "기사의 전망이나 기대는 reported_expectation으로 두고 실제 발생 사실로 승격하지 않습니다.",
                            "산업 일반 기사는 company_specificity=industry_context로 두며 회사 직접 위험으로 확대하지 않습니다.",
                            "secondary context는 framing_and_limitation_only이며 primary claim 상태를 바꾸지 않습니다.",
                        ],
                        "input_payload": llm_input,
                    },
                    ensure_ascii=False,
                ),
            },
        ],
    }


def _compact_news_evidence_for_llm(evidence: dict[str, Any]) -> dict[str, Any]:
    return {
        key: evidence.get(key)
        for key in (
            "source_date",
            "title",
            "snippet",
            "source",
            "relation_type",
            "mention_count",
            "coverage",
        )
        if evidence.get(key) not in (None, "", [], {})
    }


def _compact_period_summary_for_llm(summary: dict[str, Any]) -> dict[str, Any]:
    issues = []
    for raw in summary.get("issues") or []:
        if not isinstance(raw, dict) or not str(raw.get("issue") or "").strip():
            continue
        issues.append(
            {
                key: raw.get(key)
                for key in ("issue", "importance")
                if raw.get(key) not in (None, "", [], {})
            }
        )
    return {
        key: value
        for key, value in {
            "period": summary.get("period"),
            "period_summary": summary.get("period_summary"),
            "issues": issues,
            "source_evidence_ids": summary.get("source_evidence_ids") or [],
        }.items()
        if value not in (None, "", [], {})
    }


def _compact_secondary_context_for_llm(contexts: dict[str, Any]) -> dict[str, Any]:
    compact: dict[str, Any] = {}
    for domain, context in contexts.items():
        if not isinstance(context, dict):
            continue
        catalog = {
            str(evidence_id): {
                key: evidence.get(key)
                for key in (
                    "source_date",
                    "period",
                    "metric",
                    "value",
                    "unit",
                    "previous_value",
                    "comparison_value",
                )
                if evidence.get(key) not in (None, "", [], {})
            }
            for evidence_id, evidence in (context.get("evidence_catalog") or {}).items()
            if isinstance(evidence, dict)
        }
        compact[str(domain)] = {
            "status": context.get("status") or "unavailable",
            "evidence_catalog": catalog,
        }
    return compact


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
            "primary_anchor_evidence_id": {"$ref": "#/$defs/primary_evidence_id"},
            "primary_evidence_ids": primary_id_array,
            "secondary_anchor_evidence_id": {"$ref": "#/$defs/secondary_evidence_id"},
            "secondary_evidence_ids": secondary_id_array,
            "usage": {"type": "string", "enum": [SECONDARY_CONTEXT_USAGE]},
            "limitation": {"type": "string"},
        },
        "required": [
            "context_id",
            "source_domain",
            "effect",
            "statement",
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
                "maxItems": 0 if not secondary_ids else len(secondary_ids),
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

    client = OpenAI(api_key=api_key, timeout=timeout_seconds)
    started_at = time.monotonic()
    response = execute_with_telemetry(
        lambda: client.chat.completions.create(
            model=model,
            messages=llm_request["messages"],
            temperature=float(llm_request.get("temperature", 0.2)),
            response_format=llm_request.get("response_format", {"type": "json_object"}),
        ),
        request_payload=llm_request,
        model=model,
        step="news:analysis",
        usage_getter=lambda result: getattr(result, "usage", None),
    )
    elapsed_seconds = time.monotonic() - started_at
    content = response.choices[0].message.content or ""
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
        anchor = str(item.pop("anchor_evidence_id", "") or "").strip()
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
            anchor = str(item.pop(anchor_key, "") or "").strip()
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
    required_domains = [
        domain
        for domain, context in (input_payload.get("secondary_context") or {}).items()
        if isinstance(context, dict) and context.get("status") == "available"
    ]
    output["secondary_context_assessment"] = validate_secondary_context_assessments(
        output.get("secondary_context_assessment"),
        primary_evidence_ids=primary_ids,
        secondary_catalog=secondary_catalog,
        allowed_source_domains={"financial", "market"},
        required_source_domains=required_domains,
    )


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
                "period_summary": item.get("period_summary"),
                "issues": item.get("issues") or [],
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
                "url": str(event.get("url") or ""),
                "time": str(event.get("time") or ""),
                "final_score": float(event.get("final_score") or 0.0),
                "coverage": copy.deepcopy(event.get("coverage") or {}),
            }
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


def _compact_financial_context(payload: dict[str, Any]) -> dict[str, Any]:
    metrics_by_key = payload.get("metrics_by_key") or {}
    catalog: dict[str, Any] = {}
    for metric_key in SECONDARY_FINANCIAL_METRICS:
        metric = metrics_by_key.get(metric_key) or {}
        if not isinstance(metric, dict):
            continue
        values = metric.get("values_by_period") or {}
        current = values.get("current_fiscal_year") or {}
        previous = values.get("same_period_previous_year") or {}
        comparison = _first_comparison(metric.get("comparisons") or {})
        value = current.get("value")
        if not _finite_number(value) and comparison:
            value = comparison.get("value")
        if not _finite_number(value):
            continue
        period = current.get("period") if isinstance(current.get("period"), dict) else {}
        evidence_id = canonical_evidence_id("financial", metric_key)
        compact: dict[str, Any] = {
            "evidence_id": evidence_id,
            "domain": "financial",
            "source_domain": "financial",
            "origin_type": "deterministic_derived" if metric.get("metric_type") == "comparison" else "raw_source",
            "source_ref": f"dart_lightweight.metrics_by_key.{metric_key}",
            "source_date": str(period.get("period_end") or ""),
            "period": str(period.get("basis") or ""),
            "metric": metric_key,
            "value": value,
            "unit": metric.get("unit"),
        }
        if _finite_number(previous.get("value")):
            compact["previous_value"] = previous.get("value")
        if comparison and _finite_number(comparison.get("value")):
            compact["comparison_value"] = comparison.get("value")
        catalog[evidence_id] = compact
    validate_evidence_catalog(catalog, allowed_domains={"financial"})
    return {"status": "available" if catalog else "unavailable", "evidence_catalog": catalog}


def _compact_market_context(payload: Any) -> dict[str, Any]:
    row = payload[0] if isinstance(payload, list) and payload else payload
    if not isinstance(row, dict):
        return {"status": "unavailable", "evidence_catalog": {}}
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
    catalog: dict[str, Any] = {}
    source_date = str(row.get("date") or "")
    for field in fields:
        if field == "date" or not _finite_number(row.get(field)):
            continue
        evidence_id = canonical_evidence_id("market", field)
        catalog[evidence_id] = {
            "evidence_id": evidence_id,
            "domain": "market",
            "source_domain": "market",
            "origin_type": "raw_source",
            "source_ref": f"market_full_dataset.latest.{field}",
            "source_date": source_date,
            "period": "",
            "metric": field,
            "value": row.get(field),
            "unit": _market_unit(field),
        }
    validate_evidence_catalog(catalog, allowed_domains={"market"})
    return {"status": "available" if catalog else "unavailable", "evidence_catalog": catalog}


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
                "final_score": event.get("final_score"),
                "title": event.get("title"),
                "snippet": event.get("snippet"),
                "source": event.get("source"),
                "url": event.get("url"),
                "coverage": copy.deepcopy(event.get("coverage") or {}),
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

    period_keys = _month_window(as_of_date, SUMMARY_MONTH_COUNT)
    raw_periods = period_keys[-RECENT_RAW_MONTH_COUNT:]
    summary_periods = period_keys[:-RECENT_RAW_MONTH_COUNT]
    return (
        period_keys,
        summary_periods,
        raw_periods,
        "Use monthly LLM summaries for the oldest 9 periods in the 12-month window.",
        "Use recent raw news events for the latest 3 periods in the 12-month window.",
    )


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
