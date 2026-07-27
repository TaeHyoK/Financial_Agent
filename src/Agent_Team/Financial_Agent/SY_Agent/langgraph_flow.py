#!/usr/bin/env python3
"""Evidence-admissibility validation for Financial Analyst output."""

from __future__ import annotations

import argparse
import copy
import json
import math
import os
from datetime import date
from pathlib import Path
from typing import Any, TypedDict
from urllib import error, request

from langgraph.graph import END, START, StateGraph
from shared.evidence_contracts import (
    SECONDARY_CONTEXT_EFFECTS,
    SECONDARY_CONTEXT_USAGE,
    validate_evidence_catalog,
    validate_secondary_context_assessments,
)
from shared.llm_clients import (
    compact_json,
    execute_with_telemetry,
    partition_by_prompt_budget,
)

try:
    from .claim_extraction import evidence_by_claim, get_claims, get_key_evidence, get_target_entity
except ImportError:  # pragma: no cover - direct script execution
    from claim_extraction import evidence_by_claim, get_claims, get_key_evidence, get_target_entity

try:
    from .. import DEFAULT_ENV_FILE, PROJECT_ROOT
except ImportError:  # pragma: no cover - direct script execution
    PROJECT_ROOT = Path(__file__).resolve().parents[4]
    DEFAULT_ENV_FILE = PROJECT_ROOT / "configs" / ".env"


DEFAULT_OPENAI_MODEL = "gpt-5.4-mini"
SEMANTIC_BATCH_TARGET_TOKENS = 100_000
EVIDENCE_USE_VALUES = {"strong", "context_only", "exclude"}
GRAPH_FLOW = [
    "Input Specialist Output",
    "Claim and Evidence Extraction",
    "DART Source Context",
    "Deterministic Evidence Checks",
    "Semantic Batch Evaluation",
    "Admissibility Ledger Output",
]


class SYGraphState(TypedDict, total=False):
    input_path: str
    use_llm: bool
    llm_provider: str
    llm_model: str
    llm_timeout: int
    dart_main_path: str
    dart_master_path: str
    source_output: dict[str, Any]
    source_context: dict[str, Any]
    evidence_map: dict[str, list[dict[str, Any]]]
    secondary_context_catalog: dict[str, dict[str, Any]]
    secondary_context_assessments: list[dict[str, Any]]
    extracted_claims: list[dict[str, Any]]
    deterministic_checks: dict[str, dict[str, Any]]
    claim_validations: list[dict[str, Any]]
    llm_calls: list[dict[str, Any]]
    final_output: dict[str, Any]
    verified_financial_report: dict[str, Any]


def load_env_file(path: str) -> None:
    env_path = Path(path)
    if not env_path.exists():
        return
    for raw_line in env_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = value


def resolve_llm_provider(provider: str) -> str:
    if provider not in {"auto", "openai"}:
        raise RuntimeError(f"Unsupported LLM provider: {provider}. Only openai is supported.")
    if provider == "openai":
        return "openai"
    return "openai" if os.getenv("OPENAI_API_KEY") else "none"


def resolve_llm_model(provider: str, model: str) -> str:
    if model and model != "auto":
        return model
    if provider == "openai":
        return os.getenv("OPENAI_MODEL", DEFAULT_OPENAI_MODEL)
    return model or DEFAULT_OPENAI_MODEL


def uses_max_completion_tokens(model: str) -> bool:
    return model.startswith(("gpt-5", "o1", "o3", "o4"))


def call_openai_json(
    *,
    request_payload: dict[str, Any],
    model: str,
    timeout: int,
    step: str,
) -> tuple[dict[str, Any], dict[str, Any]]:
    api_key = os.getenv("OPENAI_API_KEY")
    if not api_key:
        raise RuntimeError("OPENAI_API_KEY is not set")
    base_url = os.getenv("OPENAI_BASE_URL", "https://api.openai.com/v1").rstrip("/")
    req = request.Request(
        f"{base_url}/chat/completions",
        data=compact_json(request_payload).encode("utf-8"),
        headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
        method="POST",
    )

    def send_request() -> dict[str, Any]:
        try:
            with request.urlopen(req, timeout=timeout) as response:
                return json.loads(response.read().decode("utf-8"))
        except error.HTTPError as exc:
            body = exc.read().decode("utf-8", "ignore")
            raise RuntimeError(f"OpenAI HTTP {exc.code}: {body[:500]}") from exc

    result = execute_with_telemetry(
        send_request,
        request_payload=request_payload,
        model=model,
        step=step,
        usage_getter=lambda response: response.get("usage", {}),
    )
    choices = result.get("choices") or []
    if not choices:
        raise RuntimeError(f"OpenAI returned no choices: {result}")
    content = choices[0].get("message", {}).get("content", "")
    parsed = extract_json_object(content)
    if not parsed:
        raise RuntimeError("Financial SY semantic evaluation returned invalid JSON.")
    return parsed, result.get("usage", {})


def extract_json_object(text: str) -> dict[str, Any]:
    cleaned = str(text or "").strip().replace("```json", "").replace("```", "").strip()
    try:
        payload = json.loads(cleaned)
        return payload if isinstance(payload, dict) else {}
    except json.JSONDecodeError:
        start = cleaned.find("{")
        end = cleaned.rfind("}")
        if start < 0 or end <= start:
            return {}
        try:
            payload = json.loads(cleaned[start : end + 1])
        except json.JSONDecodeError:
            return {}
        return payload if isinstance(payload, dict) else {}


def load_json_if_exists(path_value: str | None) -> dict[str, Any]:
    if not path_value:
        return {}
    path = Path(path_value)
    if not path.exists():
        return {}
    payload = json.loads(path.read_text(encoding="utf-8"))
    return payload if isinstance(payload, dict) else {}


def compact_dart_main(dart_main: dict[str, Any]) -> dict[str, Any]:
    metrics: dict[str, Any] = {}
    for metric_key, metric in (dart_main.get("metrics_by_key") or {}).items():
        if not isinstance(metric, dict):
            continue
        metrics[metric_key] = {
            "label": metric.get("label") or metric.get("name") or metric_key,
            "unit": metric.get("unit") or dart_main.get("unit"),
            "values_by_period": metric.get("values_by_period", {}),
            "comparisons": metric.get("comparisons", {}),
        }
    return {
        "unit": dart_main.get("unit"),
        "collection_context": dart_main.get("collection_context", {}),
        "revenue_breakdown": dart_main.get("revenue_breakdown", {}),
        "share_information": dart_main.get("share_information", {}),
        "periods": dart_main.get("periods", {}),
        "metrics_by_key": metrics,
    }


def build_source_context(state: SYGraphState) -> dict[str, Any]:
    dart_main = load_json_if_exists(state.get("dart_main_path"))
    return {
        "source_paths": {
            "dart_main": state.get("dart_main_path", ""),
            "dart_master": state.get("dart_master_path", ""),
        },
        "dart_main": compact_dart_main(dart_main) if dart_main else {},
        "collection_context": dart_main.get("collection_context", {}) if dart_main else {},
    }


def input_specialist_output_node(state: SYGraphState) -> SYGraphState:
    state["source_output"] = json.loads(Path(state["input_path"]).read_text(encoding="utf-8"))
    return state


def claim_extraction_node(state: SYGraphState) -> SYGraphState:
    state["extracted_claims"] = get_claims(state["source_output"])
    state["evidence_map"] = evidence_by_claim(get_key_evidence(state["source_output"]))
    catalog: dict[str, dict[str, Any]] = {}
    for context in (state["source_output"].get("secondary_context") or {}).values():
        if not isinstance(context, dict):
            continue
        for evidence_id, evidence in (context.get("evidence_catalog") or {}).items():
            if evidence_id in catalog and catalog[evidence_id] != evidence:
                raise ValueError(f"Conflicting secondary evidence ID: {evidence_id}")
            catalog[evidence_id] = evidence
    validate_evidence_catalog(catalog, allowed_domains={"news", "market"})
    state["secondary_context_catalog"] = catalog
    return state


def dart_source_context_node(state: SYGraphState) -> SYGraphState:
    state["source_context"] = build_source_context(state)
    return state


def deterministic_checks_node(state: SYGraphState) -> SYGraphState:
    source_numbers = collect_finite_numbers(state.get("source_context", {}).get("dart_main", {}))
    checks: dict[str, dict[str, Any]] = {}
    for claim in state.get("extracted_claims", []):
        claim_id = str(claim.get("claim_id") or "")
        evidence_items = state.get("evidence_map", {}).get(claim_id, [])
        checks[claim_id] = deterministic_claim_checks(
            claim=claim,
            evidence_items=evidence_items,
            collection_context=state.get("source_context", {}).get("collection_context", {}),
            source_numbers=source_numbers,
        )
    state["deterministic_checks"] = checks
    return state


def deterministic_claim_checks(
    *,
    claim: dict[str, Any],
    evidence_items: list[dict[str, Any]],
    collection_context: dict[str, Any],
    source_numbers: set[float],
) -> dict[str, Any]:
    claim_id = str(claim.get("claim_id") or "")
    ids = [str(item.get("evidence_id") or "") for item in evidence_items if isinstance(item, dict)]
    source_exists = bool(evidence_items)
    evidence_ids_valid = bool(ids) and all(ids) and len(ids) == len(set(ids))
    claim_links_match = all(str(item.get("claim_id") or claim_id) == claim_id for item in evidence_items)
    numeric_values_valid = all(valid_evidence_value(item.get("value")) for item in evidence_items)
    dart_numeric_values = [
        float(item["value"])
        for item in evidence_items
        if str(item.get("source") or "").upper() == "DART"
        and isinstance(item.get("value"), (int, float))
        and not isinstance(item.get("value"), bool)
    ]
    numeric_match = all(number_in_source(value, source_numbers) for value in dart_numeric_values)
    dart_period_bases = {
        str(item.get("period_basis") or "").strip()
        for item in evidence_items
        if str(item.get("source") or "").upper() == "DART" and item.get("period_basis")
    }
    period_comparable = len(dart_period_bases) <= 1
    date_valid = collection_dates_valid(collection_context)
    blockers = []
    if not source_exists:
        blockers.append("missing_evidence")
    if not evidence_ids_valid or not claim_links_match:
        blockers.append("invalid_evidence_reference")
    if not numeric_values_valid:
        blockers.append("invalid_numeric_value")
    if date_valid is False:
        blockers.append("future_or_invalid_filing_date")
    return {
        "source_exists": source_exists,
        "evidence_ids_valid": evidence_ids_valid,
        "claim_links_match": claim_links_match,
        "date_valid": date_valid,
        "period_comparable": period_comparable,
        "numeric_values_valid": numeric_values_valid,
        "numeric_match": numeric_match,
        "evidence_ids": ids,
        "blockers": blockers,
    }


def valid_evidence_value(value: Any) -> bool:
    if value is None or isinstance(value, str):
        return True
    if isinstance(value, bool):
        return False
    if isinstance(value, (int, float)):
        return math.isfinite(float(value))
    return True


def collection_dates_valid(collection_context: dict[str, Any]) -> bool | None:
    selected_raw = str(collection_context.get("selected_date") or "")
    reports = collection_context.get("reports_used")
    if not selected_raw or not isinstance(reports, list):
        return None
    try:
        selected = date.fromisoformat(selected_raw)
    except ValueError:
        return False
    for report in reports:
        if not isinstance(report, dict) or not report.get("receipt_date"):
            continue
        try:
            receipt = date.fromisoformat(str(report["receipt_date"]))
        except ValueError:
            return False
        if receipt >= selected:
            return False
    return True


def collect_finite_numbers(value: Any) -> set[float]:
    numbers: set[float] = set()
    if isinstance(value, bool) or value is None:
        return numbers
    if isinstance(value, (int, float)):
        number = float(value)
        if math.isfinite(number):
            numbers.add(number)
        return numbers
    if isinstance(value, dict):
        for child in value.values():
            numbers.update(collect_finite_numbers(child))
    elif isinstance(value, list):
        for child in value:
            numbers.update(collect_finite_numbers(child))
    return numbers


def number_in_source(value: float, source_numbers: set[float]) -> bool:
    return any(math.isclose(value, candidate, rel_tol=1e-9, abs_tol=1e-9) for candidate in source_numbers)


def semantic_evaluation_node(state: SYGraphState) -> SYGraphState:
    if not state.get("use_llm") or state.get("llm_provider") != "openai":
        raise RuntimeError("Financial SY semantic evaluation requires --use-llm and OPENAI_API_KEY.")
    claims = state.get("extracted_claims", [])
    eligible = [
        claim
        for claim in claims
        if not state.get("deterministic_checks", {}).get(str(claim.get("claim_id")), {}).get("blockers")
    ]
    model = state.get("llm_model", DEFAULT_OPENAI_MODEL)
    chunks = partition_by_prompt_budget(
        eligible,
        build_request=lambda chunk: build_semantic_request(state, chunk),
        model=model,
        target_input_tokens=SEMANTIC_BATCH_TARGET_TOKENS,
    ) if eligible else []

    evaluations_by_id: dict[str, dict[str, Any]] = {}
    context_assessments: Any = []
    state.setdefault("llm_calls", [])
    for index, chunk in enumerate(chunks, start=1):
        request_payload = build_semantic_request(
            state,
            chunk,
            include_secondary_context=index == 1,
        )
        parsed, usage = call_openai_json(
            request_payload=request_payload,
            model=model,
            timeout=state.get("llm_timeout", 300),
            step=f"financial_sy:semantic_batch:{index}",
        )
        evaluations = parsed.get("evaluations_by_claim_id")
        if not isinstance(evaluations, dict):
            raise RuntimeError("Financial SY response must contain evaluations_by_claim_id.")
        for claim_id, evaluation in evaluations.items():
            if isinstance(evaluation, dict):
                evaluations_by_id[str(claim_id)] = evaluation
        if index == 1:
            assessments_by_domain = parsed.get("secondary_context_assessment_by_domain") or {}
            if not isinstance(assessments_by_domain, dict):
                raise RuntimeError(
                    "Financial SY response must contain secondary_context_assessment_by_domain."
                )
            context_assessments = [
                assessment
                for assessment in assessments_by_domain.values()
                if isinstance(assessment, dict)
            ]
        state["llm_calls"].append(
            {"node": f"Semantic Batch Evaluation:{index}", "model": model, "usage": usage}
        )

    validations: list[dict[str, Any]] = []
    for claim in claims:
        claim_id = str(claim.get("claim_id") or "")
        checks = state.get("deterministic_checks", {}).get(claim_id, {})
        evidence_items = state.get("evidence_map", {}).get(claim_id, [])
        if checks.get("blockers"):
            evaluation = {
                "evidence_use": "exclude",
                "reason_ko": "결정론적 근거 검사에서 필수 근거 또는 기준일 정합성을 충족하지 못했다.",
                "limitations": list(checks.get("blockers") or []),
            }
        else:
            evaluation = evaluations_by_id.get(claim_id)
            if not isinstance(evaluation, dict):
                raise RuntimeError(f"Financial SY response missing claim id: {claim_id}")
        validations.append(normalize_validation(claim, evidence_items, checks, evaluation))
    state["claim_validations"] = validations
    primary_ids = {
        str(item.get("evidence_id"))
        for items in state.get("evidence_map", {}).values()
        for item in items
        if isinstance(item, dict)
        and str(item.get("source") or "").upper() == "DART"
        and item.get("evidence_id")
    }
    required_domains = [
        domain
        for domain, context in (state.get("source_output", {}).get("secondary_context") or {}).items()
        if isinstance(context, dict) and context.get("status") == "available"
    ]
    state["secondary_context_assessments"] = validate_secondary_context_assessments(
        context_assessments,
        primary_evidence_ids=primary_ids,
        secondary_catalog=state.get("secondary_context_catalog", {}),
        allowed_source_domains={"news", "market"},
        required_source_domains=required_domains,
    )
    return state


def build_semantic_request(
    state: SYGraphState,
    claims: list[dict[str, Any]],
    *,
    include_secondary_context: bool = True,
) -> dict[str, Any]:
    claim_payloads = []
    for claim in claims:
        claim_id = str(claim.get("claim_id") or "")
        claim_payloads.append(
            {
                "claim_id": claim_id,
                "statement": claim.get("claim_ko", ""),
                "claim_origin": claim.get("claim_origin", ""),
                "section_path": claim.get("section_path", ""),
                "financial_dimension": claim.get("financial_dimension", ""),
                "evidence": [
                    _compact_primary_evidence(item)
                    for item in state.get("evidence_map", {}).get(claim_id, [])
                    if str(item.get("source") or "").upper() == "DART"
                ],
                "deterministic_checks": state.get("deterministic_checks", {}).get(claim_id, {}),
            }
        )
    model = state.get("llm_model", DEFAULT_OPENAI_MODEL)
    payload = {
        "model": model,
        "messages": [
            {
                "role": "system",
                "content": (
                    "당신은 Financial evidence admissibility evaluator입니다. 투자 보고서를 수정하거나 새 투자 문장을 쓰지 않습니다. "
                    "각 claim이 제공된 evidence와 deterministic checks로 뒷받침되는지만 평가합니다. "
                    "strong은 문장 범위 전체가 직접 근거로 지지될 때, context_only는 방향성 참고만 가능하거나 표현이 근거보다 강할 때, "
                    "exclude는 근거가 없거나 충돌할 때 사용합니다. News와 Market secondary context는 DART claim의 직접 근거가 아니며 "
                    "primary evidence status를 바꾸지 않습니다. secondary context는 일치·충돌·중립·확인 불가와 표현 한계만 평가하고 "
                    "인과관계를 단정하지 마세요. Buy/Hold/Sell과 목표주가를 만들지 말고 JSON만 출력하세요."
                ),
            },
            {
                "role": "user",
                "content": compact_json(
                    _financial_sy_user_payload(
                        state,
                        claim_payloads,
                        include_secondary_context=include_secondary_context,
                    )
                ),
            },
        ],
        "response_format": _financial_sy_response_format(
            state,
            claims,
            include_secondary_context=include_secondary_context,
        ),
    }
    if uses_max_completion_tokens(model):
        payload["max_completion_tokens"] = 6000
    else:
        payload["temperature"] = 0.0
        payload["max_tokens"] = 6000
    return payload


def _financial_sy_user_payload(
    state: SYGraphState,
    claim_payloads: list[dict[str, Any]],
    *,
    include_secondary_context: bool,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "collection_context": state.get("source_context", {}).get("collection_context", {}),
        "claims": claim_payloads,
    }
    if not include_secondary_context:
        return payload
    primary_catalog = {
        str(item.get("evidence_id")): _compact_primary_evidence(item)
        for items in state.get("evidence_map", {}).values()
        for item in items
        if isinstance(item, dict)
        and str(item.get("source") or "").upper() == "DART"
        and item.get("evidence_id")
    }
    payload["primary_financial_evidence"] = primary_catalog
    payload["secondary_context"] = state.get("source_output", {}).get("secondary_context") or {}
    payload["secondary_context_contract"] = {
        "effects": sorted(SECONDARY_CONTEXT_EFFECTS),
        "usage": SECONDARY_CONTEXT_USAGE,
        "causal_assertions_allowed": False,
        "may_change_primary_evidence_status": False,
    }
    return payload


def _financial_sy_response_format(
    state: SYGraphState,
    claims: list[dict[str, Any]],
    *,
    include_secondary_context: bool,
) -> dict[str, Any]:
    evaluation_properties: dict[str, Any] = {}
    for claim in claims:
        claim_id = str(claim.get("claim_id") or "")
        evidence_ids = sorted(
            str(item.get("evidence_id"))
            for item in state.get("evidence_map", {}).get(claim_id, [])
            if isinstance(item, dict)
            and str(item.get("source") or "").upper() == "DART"
            and item.get("evidence_id")
        )
        evidence_item_schema: dict[str, Any] = {"type": "string"}
        if evidence_ids:
            evidence_item_schema["enum"] = evidence_ids
        evaluation_properties[claim_id] = {
            "type": "object",
            "properties": {
                "evidence_use": {
                    "type": "string",
                    "enum": sorted(EVIDENCE_USE_VALUES),
                },
                "reason_ko": {"type": "string"},
                "evidence_ids": {
                    "type": "array",
                    "items": evidence_item_schema,
                },
                "limitations": {
                    "type": "array",
                    "items": {"type": "string"},
                },
            },
            "required": ["evidence_use", "reason_ko", "evidence_ids", "limitations"],
            "additionalProperties": False,
        }

    context_properties: dict[str, Any] = {}
    if include_secondary_context:
        primary_ids = sorted(
            str(item.get("evidence_id"))
            for items in state.get("evidence_map", {}).values()
            for item in items
            if isinstance(item, dict)
            and str(item.get("source") or "").upper() == "DART"
            and item.get("evidence_id")
        )
        primary_item_schema: dict[str, Any] = {"type": "string"}
        if primary_ids:
            primary_item_schema["enum"] = primary_ids
        for domain in _available_secondary_context_domains(state):
            secondary_ids = _secondary_evidence_ids_for_domain(state, domain)
            secondary_item_schema: dict[str, Any] = {"type": "string"}
            if secondary_ids:
                secondary_item_schema["enum"] = secondary_ids
            context_properties[domain] = {
                "type": "object",
                "properties": {
                    "context_id": {"type": "string"},
                    "source_domain": {"type": "string", "enum": [domain]},
                    "effect": {
                        "type": "string",
                        "enum": sorted(SECONDARY_CONTEXT_EFFECTS),
                    },
                    "statement": {"type": "string"},
                    "primary_evidence_ids": {
                        "type": "array",
                        "items": primary_item_schema,
                    },
                    "secondary_evidence_ids": {
                        "type": "array",
                        "items": secondary_item_schema,
                    },
                    "usage": {
                        "type": "string",
                        "enum": [SECONDARY_CONTEXT_USAGE],
                    },
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

    schema = {
        "type": "object",
        "properties": {
            "evaluations_by_claim_id": {
                "type": "object",
                "properties": evaluation_properties,
                "required": list(evaluation_properties),
                "additionalProperties": False,
            },
            "secondary_context_assessment_by_domain": {
                "type": "object",
                "properties": context_properties,
                "required": list(context_properties),
                "additionalProperties": False,
            },
        },
        "required": [
            "evaluations_by_claim_id",
            "secondary_context_assessment_by_domain",
        ],
        "additionalProperties": False,
    }
    return {
        "type": "json_schema",
        "json_schema": {
            "name": "financial_sy_admissibility",
            "strict": True,
            "schema": schema,
        },
    }


def _available_secondary_context_domains(state: SYGraphState) -> list[str]:
    return sorted(
        domain
        for domain, context in (state.get("source_output", {}).get("secondary_context") or {}).items()
        if domain in {"news", "market"}
        and isinstance(context, dict)
        and context.get("status") == "available"
    )


def _secondary_evidence_ids_for_domain(state: SYGraphState, domain: str) -> list[str]:
    catalog = state.get("secondary_context_catalog") or {}
    evidence_ids = [
        str(evidence_id)
        for evidence_id, evidence in catalog.items()
        if isinstance(evidence, dict) and evidence.get("domain") == domain
    ]
    if evidence_ids:
        return sorted(evidence_ids)
    context = (state.get("source_output", {}).get("secondary_context") or {}).get(domain) or {}
    return sorted(str(evidence_id) for evidence_id in (context.get("evidence_catalog") or {}))


def _compact_primary_evidence(item: dict[str, Any]) -> dict[str, Any]:
    return {
        key: item.get(key)
        for key in (
            "evidence_id",
            "claim_id",
            "source",
            "metric_or_event",
            "period",
            "value",
            "period_basis",
        )
        if item.get(key) is not None
    }


def normalize_validation(
    claim: dict[str, Any],
    evidence_items: list[dict[str, Any]],
    checks: dict[str, Any],
    evaluation: dict[str, Any],
) -> dict[str, Any]:
    evidence_use = str(evaluation.get("evidence_use") or "").strip()
    if evidence_use not in EVIDENCE_USE_VALUES:
        raise RuntimeError(f"Invalid Financial SY evidence_use for {claim.get('claim_id')}: {evidence_use}")
    if evidence_use == "strong" and (checks.get("numeric_match") is False or not checks.get("period_comparable", True)):
        evidence_use = "context_only"
    available_ids = {
        str(item.get("evidence_id"))
        for item in evidence_items
        if isinstance(item, dict) and item.get("evidence_id")
    }
    requested_ids = evaluation.get("evidence_ids")
    if not isinstance(requested_ids, list):
        requested_ids = list(available_ids)
    evidence_ids = [str(value) for value in requested_ids if str(value) in available_ids]
    if not evidence_ids:
        evidence_ids = sorted(available_ids)
    support_level = {
        "strong": "supported",
        "context_only": "weakly_supported",
        "exclude": "unsupported",
    }[evidence_use]
    decision = {"strong": "keep", "context_only": "revise", "exclude": "remove"}[evidence_use]
    limitations = evaluation.get("limitations")
    if not isinstance(limitations, list):
        limitations = []
    return {
        "claim_id": claim.get("claim_id", ""),
        "claim_ko": claim.get("claim_ko", ""),
        "claim_origin": claim.get("claim_origin", ""),
        "section_path": claim.get("section_path", ""),
        "financial_dimension": claim.get("financial_dimension", ""),
        "evidence_ids": evidence_ids,
        "evidence_refs": evidence_ids,
        "deterministic_checks": copy.deepcopy(checks),
        "evidence_use": evidence_use,
        "support_level": support_level,
        "decision": decision,
        "reason_ko": str(evaluation.get("reason_ko") or ""),
        "limitations": [str(item) for item in limitations if str(item).strip()],
    }


def build_outputs_node(state: SYGraphState) -> SYGraphState:
    validations = state.get("claim_validations", [])
    counts = {
        status: sum(1 for item in validations if item.get("evidence_use") == status)
        for status in ("strong", "context_only", "exclude")
    }
    overall_status = (
        "fail"
        if validations and counts["exclude"] == len(validations)
        else "needs_attention"
        if counts["context_only"] or counts["exclude"]
        else "pass"
    )
    source_summary = {
        "source_paths": state.get("source_context", {}).get("source_paths", {}),
        "collection_context": state.get("source_context", {}).get("collection_context", {}),
    }
    state["final_output"] = {
        "agent_name": "SY Agent",
        "agent_role": "Financial Evidence Admissibility Validator",
        "output_version": "3.0",
        "output_mode": "financial_evidence_admissibility",
        "target_entity": get_target_entity(state["source_output"]),
        "source_agent": {
            "agent_name": state["source_output"].get("agent_name", ""),
            "output_version": state["source_output"].get("output_version", ""),
            "output_path": state["input_path"],
        },
        "graph_flow": GRAPH_FLOW,
        "validation_summary": {
            "overall_status": overall_status,
            "verification_mode": "deterministic_checks_plus_semantic_batch",
            "total_claims": len(validations),
            "evidence_use_counts": counts,
            "llm_call_count": len(state.get("llm_calls", [])),
            "report_rewritten": False,
        },
        "source_context": source_summary,
        "claim_validations": validations,
        "secondary_context_assessments": state.get("secondary_context_assessments", []),
        "secondary_context_catalog": state.get("secondary_context_catalog", {}),
        "report_rewritten": False,
    }
    state["verified_financial_report"] = build_verified_financial_report(
        state["source_output"],
        validations,
        source_summary,
        secondary_context_assessments=state.get("secondary_context_assessments", []),
    )
    return state


def build_verified_financial_report(
    source_report: dict[str, Any],
    validations: list[dict[str, Any]],
    source_context: dict[str, Any] | None = None,
    secondary_context_assessments: list[dict[str, Any]] | None = None,
    **_: Any,
) -> dict[str, Any]:
    """Preserve source prose and filter only claims explicitly marked exclude."""

    report = copy.deepcopy(source_report)
    admissible_ids = {
        str(item.get("claim_id"))
        for item in validations
        if item.get("evidence_use") in {"strong", "context_only"}
    }
    sy_handoff = report.get("sy_handoff")
    if isinstance(sy_handoff, dict):
        sy_handoff["financial_claims"] = [
            claim
            for claim in sy_handoff.get("financial_claims", [])
            if str(claim.get("claim_id")) in admissible_ids
        ]
        sy_handoff["key_evidence"] = [
            evidence
            for evidence in sy_handoff.get("key_evidence", [])
            if str(evidence.get("claim_id")) in admissible_ids
        ]
    counts = {
        status: sum(1 for item in validations if item.get("evidence_use") == status)
        for status in ("strong", "context_only", "exclude")
    }
    report["report_status"] = "sy_evidence_admissibility_applied"
    report["verification_summary"] = {
        "verification_mode": "deterministic_checks_plus_semantic_batch",
        "evidence_use_counts": counts,
        "report_rewritten": False,
    }
    report["sy_validation"] = {
        "verifier_agent": "SY Agent",
        "claim_admissibility": [
            {
                "claim_id": item.get("claim_id"),
                "evidence_use": item.get("evidence_use"),
                "evidence_ids": item.get("evidence_ids", []),
            }
            for item in validations
        ],
        "source_context": copy.deepcopy(source_context or {}),
        "secondary_context_assessment_count": len(secondary_context_assessments or []),
    }
    report["secondary_context_assessment"] = copy.deepcopy(
        secondary_context_assessments or []
    )
    return report


def summarize_llm_usage(calls: list[dict[str, Any]]) -> dict[str, Any]:
    total = {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}
    for call in calls:
        usage = call.get("usage") if isinstance(call.get("usage"), dict) else {}
        for key in total:
            total[key] += int(usage.get(key) or 0)
    return {"api_call_count": len(calls), **total}


def build_graph():
    graph = StateGraph(SYGraphState)
    graph.add_node("input_specialist_output", input_specialist_output_node)
    graph.add_node("claim_extraction", claim_extraction_node)
    graph.add_node("dart_source_context", dart_source_context_node)
    graph.add_node("deterministic_checks", deterministic_checks_node)
    graph.add_node("semantic_evaluation", semantic_evaluation_node)
    graph.add_node("build_outputs", build_outputs_node)
    graph.add_edge(START, "input_specialist_output")
    graph.add_edge("input_specialist_output", "claim_extraction")
    graph.add_edge("claim_extraction", "dart_source_context")
    graph.add_edge("dart_source_context", "deterministic_checks")
    graph.add_edge("deterministic_checks", "semantic_evaluation")
    graph.add_edge("semantic_evaluation", "build_outputs")
    graph.add_edge("build_outputs", END)
    return graph.compile()


def infer_dart_source_path(input_path: Path, filename: str) -> Path | None:
    for candidate in (input_path.parent / filename, input_path.parent.parent / filename):
        if candidate.exists():
            return candidate.resolve()
    return None


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--dart-main", default=None)
    parser.add_argument("--dart-master", default=None)
    parser.add_argument("--skip-source-audit", action="store_true", help="Deprecated compatibility flag.")
    parser.add_argument("--trace-output")
    parser.add_argument("--verified-report-output")
    parser.add_argument("--env-file", default=str(DEFAULT_ENV_FILE))
    parser.add_argument("--use-llm", action="store_true", default=True)
    parser.add_argument("--llm-provider", default="openai", choices=["auto", "openai"])
    parser.add_argument("--llm-model", default="auto")
    parser.add_argument("--llm-timeout", type=int, default=300)
    args = parser.parse_args()

    load_env_file(args.env_file)
    provider = resolve_llm_provider(args.llm_provider)
    model = resolve_llm_model(provider, args.llm_model)
    input_path = Path(args.input).expanduser().resolve()
    dart_main_path = (
        Path(args.dart_main).expanduser().resolve()
        if args.dart_main
        else infer_dart_source_path(input_path, "dart_main.json")
    )
    dart_master_path = (
        Path(args.dart_master).expanduser().resolve()
        if args.dart_master
        else infer_dart_source_path(input_path, "dart_master.json")
    )
    if args.skip_source_audit:
        dart_main_path = None
        dart_master_path = None

    final_state = build_graph().invoke(
        {
            "input_path": str(input_path),
            "use_llm": args.use_llm,
            "llm_provider": provider,
            "llm_model": model,
            "llm_timeout": args.llm_timeout,
            "dart_main_path": str(dart_main_path) if dart_main_path else "",
            "dart_master_path": str(dart_master_path) if dart_master_path else "",
            "llm_calls": [],
        }
    )

    output_path = Path(args.output)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(final_state["final_output"], ensure_ascii=False, indent=2) + "\n")
    optional_outputs = {
        args.verified_report_output: final_state["verified_financial_report"],
    }
    for path_arg, payload in optional_outputs.items():
        if not path_arg:
            continue
        path = Path(path_arg)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n")
    if args.trace_output:
        trace_path = Path(args.trace_output)
        trace_path.parent.mkdir(parents=True, exist_ok=True)
        trace_path.write_text(
            json.dumps(
                {
                    "graph_flow": GRAPH_FLOW,
                    "model": model,
                    "llm_usage_summary": summarize_llm_usage(final_state.get("llm_calls", [])),
                    "llm_calls": final_state.get("llm_calls", []),
                    "deterministic_checks": final_state.get("deterministic_checks", {}),
                    "report_rewritten": False,
                },
                ensure_ascii=False,
                indent=2,
            )
            + "\n"
        )
    print(output_path)


if __name__ == "__main__":
    main()
