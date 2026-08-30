"""LLM-based interpretation for the Financial Agent's factual DART packet."""

from __future__ import annotations

import copy
import json
import os
from typing import Any

from shared.evidence_contracts import (
    SECONDARY_CONTEXT_EFFECTS,
    SECONDARY_CONTEXT_USAGE,
    validate_secondary_context_assessments,
)
from shared.llm_clients import compact_json, execute_with_telemetry


DEFAULT_OPENAI_MODEL = "gpt-5.4"
FINANCIAL_DIMENSIONS = (
    "revenue_growth",
    "profitability",
    "cost_efficiency",
    "eps",
    "cash_flow",
    "balance_sheet",
    "capital_structure",
    "debt",
    "liquidity",
)
DIMENSION_EVIDENCE_IDS = {
    "revenue_growth": ("E001", "E002"),
    "profitability": ("E003", "E006", "E007"),
    "cost_efficiency": ("E004",),
    "eps": ("E005",),
    "cash_flow": ("E008", "E009"),
    "balance_sheet": ("E010",),
    "capital_structure": ("E010",),
    "debt": ("E010",),
    "liquidity": ("E010",),
}
DETAIL_KEY_BY_DIMENSION = {
    "revenue_growth": "revenue",
    "profitability": "margin",
    "cost_efficiency": "expense_efficiency",
    "eps": "eps",
    "cash_flow": "cash_flow",
    "balance_sheet": "balance_sheet",
    "capital_structure": "capital_structure",
    "debt": "debt",
    "liquidity": "liquidity",
}


def build_financial_llm_packet(report: dict[str, Any]) -> dict[str, Any]:
    """Expose facts, periods and typed context without prior semantic labels."""

    secondary_context = report.get("secondary_context") or {}
    raw_collection_context = report.get("collection_context") or {}
    return {
        "target": {
            "company_name": report.get("target_company"),
            "ticker": report.get("ticker"),
            "as_of_date": report.get("as_of_date"),
        },
        "collection_context": {
            key: copy.deepcopy(raw_collection_context.get(key))
            for key in ("latest_available_filing", "reports_used", "statement_scope")
            if raw_collection_context.get(key) not in (None, "", [], {})
        },
        "financial_trends": report.get("financial_trends") or {},
        "revenue_breakdown": report.get("revenue_breakdown") or {},
        "share_information": report.get("share_information") or {},
        "primary_financial_evidence": {
            str(item.get("evidence_id")): copy.deepcopy(item)
            for item in (report.get("strategy_handoff") or {}).get("key_evidence") or []
            if isinstance(item, dict) and str(item.get("evidence_id") or "").strip()
        },
        "dimension_facts": {
            dimension: {
                key: copy.deepcopy(value)
                for key, value in (
                    (report.get("detailed_analysis") or {}).get(
                        DETAIL_KEY_BY_DIMENSION[dimension]
                    )
                    or {}
                ).items()
                if key != "interpretation"
            }
            for dimension in FINANCIAL_DIMENSIONS
        },
        "secondary_context": secondary_context,
        "analysis_contract": {
            "primary_source": "DART",
            "secondary_context_usage": SECONDARY_CONTEXT_USAGE,
            "secondary_context_may_change_primary_assessment": False,
            "causal_assertions_allowed": False,
            "investment_decision_allowed": False,
        },
    }


def generate_financial_analysis_with_llm(
    report: dict[str, Any],
    *,
    model: str | None = None,
) -> dict[str, Any]:
    """Interpret a prepared financial fact packet with one structured LLM call."""

    try:
        from openai import OpenAI
    except ImportError as exc:
        raise RuntimeError("Missing dependency: openai") from exc

    if not os.getenv("OPENAI_API_KEY"):
        raise RuntimeError("OPENAI_API_KEY is required for Financial Agent analysis.")

    model_name = model or os.getenv("OPENAI_MODEL") or DEFAULT_OPENAI_MODEL
    packet = build_financial_llm_packet(report)
    primary_ids = sorted(packet["primary_financial_evidence"])
    secondary_ids_by_domain = _secondary_ids_by_domain(packet.get("secondary_context") or {})
    request_payload = {
        "model": model_name,
        "input": [
            {
                "role": "system",
                "content": (
                    "당신은 공시 재무자료를 해석하는 Financial Agent다. 한국어로 작성한다. "
                    "수치 계산과 기간 정렬은 입력값을 그대로 사용하고, 제공되지 않은 원인·전망·사건 효과를 추정하지 않는다. "
                    "매출, 수익성, 비용 효율, 주당순이익, 현금흐름, 재무상태, 자본구조, 부채, 유동성을 "
                    "각각 판단한 뒤 서로 상충하는 신호의 중요도를 비교하여 종합 방향을 정한다. "
                    "단일 지표의 부호나 고정 임계값을 기계적으로 합산하지 않는다. "
                    "뉴스와 시장 자료는 재무 판단의 문맥과 한계 설명에만 사용하며 DART 근거를 대체하거나 "
                    "인과관계를 단정할 수 없다. 사건일이 재무 보고기간 뒤라면 미반영 자체를 부정적 근거로 보지 않는다. "
                    "기준일 이후 공시가 반영되지 않았다는 일반론, 분석자가 정한 비교기업 수, 단순한 자료 최신성은 "
                    "주요 한계로 쓰지 않는다. 제공된 자료 안에서 실제 해석을 바꿀 수 있는 범위 차이, 누락 항목, "
                    "기간 불일치만 주의사항으로 제시한다. 금액은 억원·조원, 비율은 퍼센트 등 사람이 읽기 쉬운 단위로 표현한다. "
                    "매수·보유·매도, 목표주가, 비중 제안은 작성하지 않는다. "
                    "각 판단은 허용된 evidence ID를 명시하고 구조화 출력 스키마만 반환한다."
                ),
            },
            {"role": "user", "content": compact_json(packet)},
        ],
        "text": {
            "format": financial_analysis_json_schema(
                primary_evidence_ids=primary_ids,
                secondary_evidence_ids_by_domain=secondary_ids_by_domain,
            ),
            "verbosity": "medium",
        },
        "store": False,
    }
    client = OpenAI()
    response = execute_with_telemetry(
        lambda: client.responses.create(**request_payload),
        request_payload=request_payload,
        model=model_name,
        step="financial:analyst_report",
        usage_getter=lambda result: getattr(result, "usage", None),
    )
    if str(getattr(response, "status", "completed") or "") == "incomplete":
        details = getattr(response, "incomplete_details", None)
        raise RuntimeError(f"Financial Agent response was incomplete: {details}")
    analysis = _parse_response_json(response)
    return validate_financial_analysis(
        analysis,
        primary_evidence_ids=primary_ids,
        secondary_context=packet.get("secondary_context") or {},
    )


def apply_financial_analysis(
    report: dict[str, Any],
    analysis: dict[str, Any],
) -> dict[str, Any]:
    """Merge model judgments while preserving deterministic supporting facts."""

    result = copy.deepcopy(report)
    main_view = analysis["main_view"]
    result["main_view"]["summary"] = main_view["summary"]
    result["main_view"]["direction"] = main_view["direction"]
    result["main_view"]["analysis_evidence_ids"] = main_view["primary_evidence_ids"]

    for dimension in FINANCIAL_DIMENSIONS:
        assessment = analysis["dimension_assessments"][dimension]
        result["financial_statement_view"][dimension].update(
            {
                "stance": assessment["stance"],
                "reasoning": assessment["reasoning"],
                "primary_evidence_ids": assessment["primary_evidence_ids"],
            }
        )
        detail_key = DETAIL_KEY_BY_DIMENSION[dimension]
        result["detailed_analysis"][detail_key]["interpretation"] = assessment["reasoning"]
        result["detailed_analysis"][detail_key]["primary_evidence_ids"] = assessment[
            "primary_evidence_ids"
        ]

    result["secondary_context_assessment"] = copy.deepcopy(
        analysis["secondary_context_assessment"]
    )
    result["analysis_metadata"] = {
        "analysis_mode": "llm_structured_financial_reasoning",
        "semantic_rules_applied": False,
    }
    return result


def validate_financial_analysis(
    analysis: Any,
    *,
    primary_evidence_ids: list[str],
    secondary_context: dict[str, Any],
) -> dict[str, Any]:
    if not isinstance(analysis, dict):
        raise ValueError("Financial Agent output must be an object.")
    main = analysis.get("main_view")
    dimensions = analysis.get("dimension_assessments")
    contexts_by_domain = analysis.get("secondary_context_assessment_by_domain")
    if not isinstance(main, dict) or not isinstance(dimensions, dict):
        raise ValueError("Financial Agent output is missing analysis sections.")
    if main.get("direction") not in {"positive", "mixed", "negative"}:
        raise ValueError("Financial Agent direction is invalid.")
    allowed_primary = set(primary_evidence_ids)
    _validate_primary_refs(main.get("primary_evidence_ids"), allowed_primary, "main_view")
    for dimension in FINANCIAL_DIMENSIONS:
        assessment = dimensions.get(dimension)
        if not isinstance(assessment, dict):
            raise ValueError(f"Missing Financial Agent dimension: {dimension}")
        if assessment.get("stance") not in {"positive", "mixed", "negative", "insufficient"}:
            raise ValueError(f"Invalid stance for Financial Agent dimension: {dimension}")
        _validate_primary_refs(
            assessment.get("primary_evidence_ids"),
            allowed_primary.intersection(DIMENSION_EVIDENCE_IDS[dimension]),
            dimension,
        )

    secondary_catalog = _combined_secondary_catalog(secondary_context)
    required_domains = sorted(_secondary_ids_by_domain(secondary_context))
    if not isinstance(contexts_by_domain, dict):
        raise ValueError("Financial Agent output is missing secondary context assessments.")
    contexts = [
        contexts_by_domain[domain]
        for domain in required_domains
        if isinstance(contexts_by_domain.get(domain), dict)
    ]
    normalized_contexts = validate_secondary_context_assessments(
        contexts,
        primary_evidence_ids=primary_evidence_ids,
        secondary_catalog=secondary_catalog,
        allowed_source_domains={"news", "market"},
        required_source_domains=required_domains,
    )
    result = copy.deepcopy(analysis)
    result.pop("secondary_context_assessment_by_domain", None)
    result["secondary_context_assessment"] = normalized_contexts
    return result


def financial_analysis_json_schema(
    *,
    primary_evidence_ids: list[str],
    secondary_evidence_ids_by_domain: dict[str, list[str]],
) -> dict[str, Any]:
    primary_ref = {"type": "string", "enum": primary_evidence_ids}

    def primary_refs(*, max_items: int = 4) -> dict[str, Any]:
        return {
            "type": "array",
            "items": primary_ref,
            "minItems": 1,
            "maxItems": max_items,
        }

    def dimension_schema(dimension: str) -> dict[str, Any]:
        allowed_ids = [
            evidence_id
            for evidence_id in DIMENSION_EVIDENCE_IDS[dimension]
            if evidence_id in primary_evidence_ids
        ]
        return {
            "type": "object",
            "properties": {
                "stance": {
                    "type": "string",
                    "enum": ["positive", "mixed", "negative", "insufficient"],
                },
                "reasoning": {"type": "string"},
                "primary_evidence_ids": {
                    "type": "array",
                    "items": {"type": "string", "enum": allowed_ids},
                    "minItems": 1,
                    "maxItems": min(4, len(allowed_ids)),
                },
            },
            "required": ["stance", "reasoning", "primary_evidence_ids"],
            "additionalProperties": False,
        }
    schema = {
        "type": "object",
        "properties": {
            "main_view": {
                "type": "object",
                "properties": {
                    "summary": {"type": "string"},
                    "direction": {
                        "type": "string",
                        "enum": ["positive", "mixed", "negative"],
                    },
                    "primary_evidence_ids": primary_refs(max_items=6),
                },
                "required": [
                    "summary",
                    "direction",
                    "primary_evidence_ids",
                ],
                "additionalProperties": False,
            },
            "dimension_assessments": {
                "type": "object",
                "properties": {
                    dimension: dimension_schema(dimension)
                    for dimension in FINANCIAL_DIMENSIONS
                },
                "required": list(FINANCIAL_DIMENSIONS),
                "additionalProperties": False,
            },
            "secondary_context_assessment_by_domain": {
                "type": "object",
                "properties": {
                    domain: _secondary_assessment_schema(
                        domain=domain,
                        primary_evidence_ids=primary_evidence_ids,
                        secondary_evidence_ids=evidence_ids,
                    )
                    for domain, evidence_ids in sorted(
                        secondary_evidence_ids_by_domain.items()
                    )
                },
                "required": sorted(secondary_evidence_ids_by_domain),
                "additionalProperties": False,
            },
        },
        "required": [
            "main_view",
            "dimension_assessments",
            "secondary_context_assessment_by_domain",
        ],
        "additionalProperties": False,
    }
    return {
        "type": "json_schema",
        "name": "financial_agent_analysis",
        "strict": True,
        "schema": schema,
    }


def _secondary_assessment_schema(
    *,
    domain: str,
    primary_evidence_ids: list[str],
    secondary_evidence_ids: list[str],
) -> dict[str, Any]:
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
            "primary_evidence_ids": {
                "type": "array",
                "items": {"type": "string", "enum": primary_evidence_ids},
                "minItems": 1,
                "maxItems": 4,
            },
            "secondary_evidence_ids": {
                "type": "array",
                "items": secondary_item,
                "maxItems": 6,
            },
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


def _secondary_ids_by_domain(
    contexts: dict[str, Any],
) -> dict[str, list[str]]:
    result: dict[str, list[str]] = {}
    for domain in ("news", "market"):
        context = contexts.get(domain)
        if not isinstance(context, dict) or context.get("status") != "available":
            continue
        evidence_ids = sorted(
            str(evidence_id)
            for evidence_id in (context.get("evidence_catalog") or {})
            if str(evidence_id).strip()
        )
        if evidence_ids:
            result[domain] = evidence_ids
    return result


def _combined_secondary_catalog(
    contexts: dict[str, Any],
) -> dict[str, dict[str, Any]]:
    catalog: dict[str, dict[str, Any]] = {}
    for context in contexts.values():
        if not isinstance(context, dict):
            continue
        for evidence_id, evidence in (context.get("evidence_catalog") or {}).items():
            if isinstance(evidence, dict):
                catalog[str(evidence_id)] = evidence
    return catalog


def _validate_primary_refs(value: Any, allowed: set[str], label: str) -> None:
    refs = value if isinstance(value, list) else []
    if not refs or any(str(item) not in allowed for item in refs):
        raise ValueError(f"Invalid primary evidence references for {label}.")


def _parse_response_json(response: Any) -> dict[str, Any]:
    text = getattr(response, "output_text", None)
    if not text:
        fragments: list[str] = []
        for item in getattr(response, "output", None) or []:
            for content in getattr(item, "content", None) or []:
                if getattr(content, "type", "") == "refusal":
                    raise RuntimeError(
                        f"Financial Agent response was refused: {getattr(content, 'refusal', '')}"
                    )
                fragment = getattr(content, "text", None)
                if fragment:
                    fragments.append(str(fragment))
        text = "".join(fragments)
    if not text:
        raise RuntimeError("Financial Agent response did not contain output text.")
    parsed = json.loads(text)
    if not isinstance(parsed, dict):
        raise RuntimeError("Financial Agent response JSON must be an object.")
    return parsed
