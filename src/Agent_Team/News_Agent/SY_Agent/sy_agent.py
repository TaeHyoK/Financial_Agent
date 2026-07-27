#!/usr/bin/env python3
"""Evidence-admissibility validation for News Agent output."""

from __future__ import annotations

import argparse
import copy
import json
import math
import os
import re
import time
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any, TypedDict

from langgraph.graph import END, START, StateGraph
from openai import OpenAI
from shared.evidence_contracts import (
    validate_evidence_catalog,
    validate_secondary_context_assessments,
)
from shared.llm_clients import compact_json, execute_with_telemetry, partition_by_prompt_budget
from tqdm.auto import tqdm


DEFAULT_MODEL = "gpt-5.4-mini"
SEMANTIC_BATCH_TARGET_TOKENS = 100_000
EVIDENCE_USE_VALUES = {"strong", "context_only", "exclude"}
EVIDENCE_ID_PATTERN = re.compile(
    r"\b(?:NEWS_RAW_\d{4}-\d{2}(?:-\d{2})?_\d+|DART_[A-Z0-9_]+|YF_[A-Z0-9_]+)\b"
)
GRAPH_FLOW = [
    "Input Specialist Output",
    "Claim and Evidence Extraction",
    "Deterministic Evidence Checks",
    "Semantic Batch Evaluation",
    "Admissibility Ledger Output",
]


def _project_root() -> Path:
    return Path(__file__).resolve().parents[4]


@dataclass(frozen=True)
class OutputPaths:
    output_dir: Path
    verified_output: Path
    verified_report: Path
    audit_trace: Path


class NewsSYState(TypedDict, total=False):
    handoff_path: Path
    paths: OutputPaths
    model: str
    claim_limit: int
    timeout_seconds: float
    started_at: float
    handoff_document: dict[str, Any]
    source_output: dict[str, Any]
    evidence_map: dict[str, Any]
    secondary_context_assessments: list[dict[str, Any]]
    secondary_context_catalog: dict[str, dict[str, Any]]
    claims: list[dict[str, Any]]
    deterministic_checks: dict[str, dict[str, Any]]
    evaluations: list[dict[str, Any]]
    llm_calls: list[dict[str, Any]]
    verified: dict[str, Any]
    verified_handoff: dict[str, Any]


def main() -> None:
    args = build_parser().parse_args()
    project_root = _project_root()
    _load_env_file(project_root / ".env")
    if args.env_path:
        _load_env_file(Path(args.env_path).expanduser())
    handoff_path = Path(args.input).expanduser().resolve()
    output_dir = Path(args.output_dir).expanduser().resolve() if args.output_dir else handoff_path.parent / "sy_agent"
    paths = OutputPaths(
        output_dir=output_dir,
        verified_output=output_dir / "sy_claim_validations.json",
        verified_report=output_dir / "news_agent_verified_handoff.json",
        audit_trace=output_dir / "sy_audit_trace.json",
    )
    model = args.model or os.getenv("NEWS_SY_AGENT_LLM_MODEL") or os.getenv("NEWS_AGENT_LLM_MODEL") or DEFAULT_MODEL
    result = run_sy_agent(
        handoff_path=handoff_path,
        paths=paths,
        model=model,
        claim_limit=args.news_claim_limit,
        timeout_seconds=args.timeout_seconds,
        show_progress=True,
    )
    print("News SY evidence validation complete")
    print(paths.verified_output)
    print(json.dumps(result["summary"], ensure_ascii=False, indent=2))


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run News evidence-admissibility validation.")
    parser.add_argument("--input", required=True)
    parser.add_argument("--output-dir", default=None)
    parser.add_argument("--model", default=None)
    parser.add_argument("--env-path", default=None)
    parser.add_argument("--news-claim-limit", type=int, default=10)
    parser.add_argument("--timeout-seconds", type=float, default=300.0)
    return parser


def run_sy_agent(
    *,
    handoff_path: Path,
    paths: OutputPaths,
    model: str,
    claim_limit: int,
    timeout_seconds: float,
    show_progress: bool = False,
) -> dict[str, Any]:
    if show_progress:
        tqdm.write("News SY: deterministic checks and semantic batch evaluation")
    final_state = build_news_sy_graph().invoke(
        {
            "handoff_path": handoff_path,
            "paths": paths,
            "model": model,
            "claim_limit": claim_limit,
            "timeout_seconds": timeout_seconds,
            "started_at": time.monotonic(),
            "llm_calls": [],
        }
    )
    return final_state["verified"]


def load_handoff_node(state: NewsSYState) -> NewsSYState:
    document = _load_json(state["handoff_path"])
    source_output = _unwrap_handoff_output(document)
    state["handoff_document"] = document
    state["source_output"] = source_output
    state["evidence_map"] = dedupe_evidence_catalog(_load_evidence_map(source_output, state["handoff_path"]))
    validate_evidence_catalog(state["evidence_map"])
    primary_ids = {
        evidence_id
        for evidence_id, evidence in state["evidence_map"].items()
        if evidence.get("domain") == "news"
    }
    secondary_catalog = {
        evidence_id: evidence
        for evidence_id, evidence in state["evidence_map"].items()
        if evidence.get("domain") in {"financial", "market"}
    }
    state["secondary_context_catalog"] = secondary_catalog
    state["secondary_context_assessments"] = validate_secondary_context_assessments(
        source_output.get("secondary_context_assessment") or [],
        primary_evidence_ids=primary_ids,
        secondary_catalog=secondary_catalog,
        allowed_source_domains={"financial", "market"},
    )
    return state


def extract_claims_node(state: NewsSYState) -> NewsSYState:
    claims = extract_claims(state["source_output"], news_claim_limit=state["claim_limit"])
    annotate_claims_with_evidence(claims, state["evidence_map"])
    state["claims"] = claims
    return state


def deterministic_checks_node(state: NewsSYState) -> NewsSYState:
    selected_date = str((state["source_output"].get("target_entity") or {}).get("as_of_date") or "")
    state["deterministic_checks"] = {
        claim["claim_id"]: deterministic_claim_checks(
            claim=claim,
            evidence_map=state["evidence_map"],
            selected_date=selected_date,
        )
        for claim in state["claims"]
    }
    return state


def deterministic_claim_checks(
    *,
    claim: dict[str, Any],
    evidence_map: dict[str, Any],
    selected_date: str,
) -> dict[str, Any]:
    declared = [value for value in claim.get("declared_evidence_ids", []) if value in evidence_map]
    catalog_dates_valid = all(evidence_date_valid(item, selected_date) for item in evidence_map.values())
    numeric_values_valid = all(finite_nested_numbers(item) for item in evidence_map.values())
    return {
        "declared_evidence_ids": declared,
        "declared_evidence_ids_valid": len(declared) == len(claim.get("declared_evidence_ids", [])),
        "allowed_evidence_domains": list(claim.get("allowed_evidence_domains", [])),
        "catalog_dates_valid": catalog_dates_valid,
        "numeric_values_valid": numeric_values_valid,
        "blockers": [] if catalog_dates_valid and numeric_values_valid else [
            value
            for value, failed in (
                ("future_or_invalid_evidence_date", not catalog_dates_valid),
                ("invalid_numeric_value", not numeric_values_valid),
            )
            if failed
        ],
    }


def evidence_date_valid(evidence: Any, selected_date: str) -> bool:
    if not isinstance(evidence, dict) or not selected_date:
        return True
    try:
        selected = date.fromisoformat(selected_date)
    except ValueError:
        return False
    for key in ("time", "period", "source_date", "as_of_date"):
        raw = str(evidence.get(key) or "")
        if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", raw):
            continue
        try:
            if date.fromisoformat(raw) >= selected:
                return False
        except ValueError:
            return False
    return True


def finite_nested_numbers(value: Any) -> bool:
    if isinstance(value, bool) or value is None or isinstance(value, str):
        return True
    if isinstance(value, (int, float)):
        return math.isfinite(float(value))
    if isinstance(value, dict):
        return all(finite_nested_numbers(child) for child in value.values())
    if isinstance(value, list):
        return all(finite_nested_numbers(child) for child in value)
    return True


def semantic_evaluation_node(state: NewsSYState) -> NewsSYState:
    claims = [
        claim
        for claim in state["claims"]
        if not state["deterministic_checks"][claim["claim_id"]].get("blockers")
    ]
    chunks = partition_by_prompt_budget(
        claims,
        build_request=lambda chunk: build_semantic_request(state, chunk),
        model=state["model"],
        target_input_tokens=SEMANTIC_BATCH_TARGET_TOKENS,
    ) if claims else []
    evaluations_by_id: dict[str, dict[str, Any]] = {}
    for index, chunk in enumerate(chunks, start=1):
        request_payload = build_semantic_request(state, chunk)
        parsed, usage, elapsed = _call_openai_json(
            request_payload,
            timeout_seconds=state["timeout_seconds"],
            step=f"news_sy:semantic_batch:{index}",
        )
        evaluations = parsed.get("evaluations_by_claim_id")
        if not isinstance(evaluations, dict):
            raise RuntimeError("News SY response must contain evaluations_by_claim_id.")
        for claim_id, evaluation in evaluations.items():
            if isinstance(evaluation, dict):
                evaluations_by_id[str(claim_id)] = evaluation
        state.setdefault("llm_calls", []).append(
            _llm_call_record(f"Semantic Batch Evaluation:{index}", request_payload, usage, elapsed)
        )

    normalized = []
    for claim in state["claims"]:
        claim_id = claim["claim_id"]
        checks = state["deterministic_checks"][claim_id]
        if checks.get("blockers"):
            evaluation = {
                "claim_id": claim_id,
                "evidence_use": "exclude",
                "reason_ko": "결정론적 근거 검사에서 기준일 또는 수치 정합성을 충족하지 못했다.",
                "evidence_ids": [],
                "limitations": checks["blockers"],
            }
        else:
            evaluation = evaluations_by_id.get(claim_id)
            if not isinstance(evaluation, dict):
                raise RuntimeError(f"News SY response missing claim id: {claim_id}")
        normalized.append(normalize_evaluation(claim, checks, evaluation, state["evidence_map"]))
    state["evaluations"] = normalized
    return state


def build_semantic_request(state: NewsSYState, claims: list[dict[str, Any]]) -> dict[str, Any]:
    referenced_evidence_ids = {
        evidence_id
        for claim in claims
        for evidence_id in claim.get("declared_evidence_ids", [])
    }
    evidence_catalog = {
        evidence_id: evidence
        for evidence_id, evidence in state["evidence_map"].items()
        if evidence.get("source_domain") == "news"
        and evidence_id in referenced_evidence_ids
    }
    request_payload = {
        "model": state["model"],
        "temperature": 0.0,
        "response_format": _news_sy_response_format(claims),
        "messages": [
            {
                "role": "system",
                "content": (
                    "당신은 News evidence admissibility evaluator입니다. 보고서를 수정하거나 새 투자 문장을 쓰지 않습니다. "
                    "각 claim과 실제 News evidence catalog의 연결만 평가하세요. strong은 기사 근거가 문장 전체를 직접 지지할 때, "
                    "context_only는 일부 근거만 있거나 기대·인과 해석이 기사보다 강할 때 사용합니다. 입력 자료에 상업 조건이나 재무 영향이 없다는 "
                    "한계와 불확실성은 별도 부정 증거를 요구하지 말고 context_only로 분류하세요. "
                    "exclude는 구체 사실·수치·인과를 발명했거나 입력과 충돌할 때 사용하며, 효과가 아직 불명확하다는 이유만으로 제외하지 마세요. "
                    "claim_kind는 fact, interpretation, data_limitation, hypothetical 중 하나로 분류하세요. 입력에 특정 정보가 없거나 "
                    "시점·비교가 제한된다는 문장은 data_limitation이며, 이 유형은 부재 자체를 위한 별도 evidence id 없이 context_only가 가능합니다. "
                    "각 claim의 event_status, company_specificity, materiality_status, financial_link_status를 실제 제목과 snippet에 맞게 검증해 반환하세요. "
                    "전망은 occurred로, 산업 일반 기사는 direct로, 미확인 재무 기여는 observed로 승격하지 마세요. "
                    "선택한 evidence_ids는 catalog에 실제 존재해야 합니다. Buy/Hold/Sell과 목표주가를 만들지 말고 JSON만 출력하세요."
                ),
            },
            {
                "role": "user",
                "content": compact_json(
                    {
                        "claims": [
                            _compact_news_sy_claim(claim)
                            for claim in claims
                        ],
                        "evidence_catalog": evidence_catalog,
                    }
                ),
            },
        ],
    }
    return request_payload


def _compact_news_sy_claim(claim: dict[str, Any]) -> dict[str, Any]:
    payload = {
        "claim_id": claim["claim_id"],
        "claim": claim["claim"],
        "declared_evidence_ids": claim.get("declared_evidence_ids", []),
    }
    supporting_context = supporting_context_for_claim(claim)
    if supporting_context:
        payload["supporting_context"] = supporting_context
    return payload


def _news_sy_response_format(claims: list[dict[str, Any]]) -> dict[str, Any]:
    evaluations: dict[str, Any] = {}
    for claim in claims:
        claim_id = str(claim.get("claim_id") or "")
        declared_ids = list(dict.fromkeys(str(value) for value in claim.get("declared_evidence_ids", [])))
        evidence_item: dict[str, Any] = {"type": "string"}
        if declared_ids:
            evidence_item["enum"] = declared_ids
        evaluations[claim_id] = {
            "type": "object",
            "properties": {
                "evidence_use": {
                    "type": "string",
                    "enum": ["strong", "context_only", "exclude"],
                },
                "reason_ko": {"type": "string"},
                "evidence_ids": {"type": "array", "items": evidence_item},
                "claim_kind": {
                    "type": "string",
                    "enum": ["fact", "interpretation", "data_limitation", "hypothetical"],
                },
                "limitations": {"type": "array", "items": {"type": "string"}},
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
                "evidence_use",
                "reason_ko",
                "evidence_ids",
                "claim_kind",
                "limitations",
                "event_status",
                "company_specificity",
                "materiality_status",
                "financial_link_status",
            ],
            "additionalProperties": False,
        }
    return {
        "type": "json_schema",
        "json_schema": {
            "name": "news_sy_admissibility",
            "strict": True,
            "schema": {
                "type": "object",
                "properties": {
                    "evaluations_by_claim_id": {
                        "type": "object",
                        "properties": evaluations,
                        "required": list(evaluations),
                        "additionalProperties": False,
                    }
                },
                "required": ["evaluations_by_claim_id"],
                "additionalProperties": False,
            },
        },
    }


def supporting_context_for_claim(claim: dict[str, Any]) -> dict[str, Any] | None:
    original = claim.get("original_item")
    if not isinstance(original, dict):
        return None
    compact = {
        key: value
        for key, value in original.items()
        if key not in {"point", "claim", "evidence_ids", "news_evidence_ids", "financial_evidence_ids", "market_evidence_ids"}
        and value not in (None, "", [], {})
    }
    return compact or None


def normalize_evaluation(
    claim: dict[str, Any],
    checks: dict[str, Any],
    evaluation: dict[str, Any],
    evidence_map: dict[str, Any],
) -> dict[str, Any]:
    evidence_use = str(evaluation.get("evidence_use") or "")
    if evidence_use not in EVIDENCE_USE_VALUES:
        raise RuntimeError(f"Invalid News SY evidence_use for {claim['claim_id']}: {evidence_use}")
    requested = evaluation.get("evidence_ids")
    if not isinstance(requested, list):
        requested = []
    allowed_domains = set(claim.get("allowed_evidence_domains") or ["news"])
    evidence_ids = list(
        dict.fromkeys(
            str(value)
            for value in requested
            if str(value) in evidence_map
            and evidence_map[str(value)].get("source_domain") in allowed_domains
        )
    )
    coverage = sorted(
        {
            str(evidence_map[evidence_id].get("source_domain"))
            for evidence_id in evidence_ids
            if evidence_map[evidence_id].get("source_domain")
        }
    )
    allowed = claim.get("allowed_evidence_domains") or claim.get("required_evidence_domains") or ["news"]
    requested_domains = evaluation.get("applicable_evidence_domains")
    if not isinstance(requested_domains, list):
        requested_domains = claim.get("required_evidence_domains") or ["news"]
    applicable = list(dict.fromkeys(str(domain) for domain in requested_domains if str(domain) in allowed))
    if not applicable:
        applicable = ["news"] if "news" in allowed else list(allowed[:1])
    missing = [domain for domain in applicable if domain not in coverage]
    claim_kind = str(evaluation.get("claim_kind") or "interpretation")
    if claim_kind not in {"fact", "interpretation", "data_limitation", "hypothetical"}:
        claim_kind = "interpretation"
    if not evidence_ids and not (claim_kind == "data_limitation" and evidence_use == "context_only"):
        evidence_use = "exclude"
    elif missing and evidence_use == "strong":
        evidence_use = "context_only"
    support_level = {
        "strong": "supported",
        "context_only": "weakly_supported",
        "exclude": "unsupported",
    }[evidence_use]
    decision = {"strong": "keep", "context_only": "revise", "exclude": "remove"}[evidence_use]
    limitations = evaluation.get("limitations") if isinstance(evaluation.get("limitations"), list) else []
    original = claim.get("original_item") if isinstance(claim.get("original_item"), dict) else {}

    def metadata_value(key: str, allowed_values: set[str], fallback: str) -> str:
        value = str(evaluation.get(key) or original.get(key) or "")
        return value if value in allowed_values else fallback

    return {
        "claim_id": claim["claim_id"],
        "section": claim["section"],
        "claim": claim["claim"],
        "allowed_evidence_domains": allowed,
        "applicable_evidence_domains": applicable,
        "required_evidence_domains": applicable,
        "declared_evidence_ids": claim.get("declared_evidence_ids", []),
        "evidence_ids": evidence_ids,
        "evidence_ids_used": evidence_ids,
        "evidence_domain_coverage": coverage,
        "missing_evidence_domains": missing,
        "deterministic_checks": copy.deepcopy(checks),
        "claim_kind": claim_kind,
        "evidence_use": evidence_use,
        "support_level": support_level,
        "decision": decision,
        "sy_reason": str(evaluation.get("reason_ko") or ""),
        "limitations": [str(item) for item in limitations if str(item).strip()],
        "event_status": metadata_value(
            "event_status",
            {"occurred", "announced", "reported_expectation", "allegation", "mixed", "insufficient"},
            "insufficient",
        ),
        "company_specificity": metadata_value(
            "company_specificity",
            {"direct", "product_direct", "industry_context", "mixed", "insufficient"},
            "insufficient",
        ),
        "materiality_status": metadata_value(
            "materiality_status",
            {"observed", "plausible_unquantified", "not_established", "mixed"},
            "not_established",
        ),
        "financial_link_status": metadata_value(
            "financial_link_status",
            {"observed", "not_observed", "not_applicable"},
            "not_observed",
        ),
    }


def build_outputs_node(state: NewsSYState) -> NewsSYState:
    usage = _aggregate_llm_usage(state.get("llm_calls", []))
    counts = {
        status: sum(1 for item in state["evaluations"] if item.get("evidence_use") == status)
        for status in ("strong", "context_only", "exclude")
    }
    verified = {
        "agent_name": "SY Agent",
        "agent_role": "News Evidence Admissibility Validator",
        "output_version": "3.0",
        "output_mode": "news_evidence_admissibility",
        "source_agent": {
            "agent_name": state["source_output"].get("agent_name"),
            "output_version": state["source_output"].get("output_version"),
            "output_path": str(state["handoff_path"]),
        },
        "target_entity": state["source_output"].get("target_entity") or {},
        "graph_flow": GRAPH_FLOW,
        "verification_mode": "deterministic_checks_plus_semantic_batch",
        "model": state["model"],
        "llm_usage": usage,
        "summary": {
            "total_claims": len(state["evaluations"]),
            "evidence_use_counts": counts,
            "llm_call_count": len(state.get("llm_calls", [])),
            "report_rewritten": False,
        },
        "claim_validations": state["evaluations"],
        "secondary_context_assessments": state.get("secondary_context_assessments", []),
        "secondary_context_catalog": state.get("secondary_context_catalog", {}),
        "elapsed_seconds": round(time.monotonic() - state["started_at"], 3),
    }
    state["verified"] = verified
    state["verified_handoff"] = build_verified_handoff_document(
        handoff_document=state["handoff_document"],
        source_output=state["source_output"],
        validation_output=verified,
        validation_path=state["paths"].verified_output,
    )
    return state


def save_outputs_node(state: NewsSYState) -> NewsSYState:
    paths = state["paths"]
    _save_json(state["verified"], paths.verified_output)
    _save_json(state["verified_handoff"], paths.verified_report)
    _save_json(
        {
            "graph_flow": GRAPH_FLOW,
            "source_path": str(state["handoff_path"]),
            "model": state["model"],
            "llm_usage": state["verified"]["llm_usage"],
            "deterministic_checks": state["deterministic_checks"],
            "llm_calls": state.get("llm_calls", []),
            "report_rewritten": False,
        },
        paths.audit_trace,
    )
    return state


def build_verified_handoff_document(
    *,
    handoff_document: dict[str, Any],
    source_output: dict[str, Any],
    validation_output: dict[str, Any],
    validation_path: Path,
) -> dict[str, Any]:
    verified_handoff = copy.deepcopy(handoff_document)
    if not isinstance(verified_handoff, dict):
        verified_handoff = {"output": copy.deepcopy(source_output)}
    output = copy.deepcopy(source_output)
    verified_handoff["output"] = output
    summary = copy.deepcopy(validation_output.get("summary") or {})
    output["sy_validation"] = {
        "verifier_agent": "SY Agent",
        "verification_mode": validation_output.get("verification_mode"),
        "validation_report_path": str(validation_path),
        "summary": summary,
        "claim_admissibility": [
            {
                "claim_id": item.get("claim_id"),
                "evidence_use": item.get("evidence_use"),
                "evidence_ids": item.get("evidence_ids", []),
                "event_status": item.get("event_status"),
                "company_specificity": item.get("company_specificity"),
                "materiality_status": item.get("materiality_status"),
                "financial_link_status": item.get("financial_link_status"),
            }
            for item in validation_output.get("claim_validations", [])
        ],
        "secondary_context_assessment_count": len(
            validation_output.get("secondary_context_assessments", [])
        ),
    }
    output["report_status"] = "sy_evidence_admissibility_applied"
    verified_handoff["report_status"] = "sy_evidence_admissibility_applied"
    verified_handoff["verification_summary"] = summary
    verified_handoff["verification_report_path"] = str(validation_path)
    return verified_handoff


def build_news_sy_graph():
    graph = StateGraph(NewsSYState)
    graph.add_node("load_handoff", load_handoff_node)
    graph.add_node("extract_claims", extract_claims_node)
    graph.add_node("deterministic_checks", deterministic_checks_node)
    graph.add_node("semantic_evaluation", semantic_evaluation_node)
    graph.add_node("build_outputs", build_outputs_node)
    graph.add_node("save_outputs", save_outputs_node)
    graph.add_edge(START, "load_handoff")
    graph.add_edge("load_handoff", "extract_claims")
    graph.add_edge("extract_claims", "deterministic_checks")
    graph.add_edge("deterministic_checks", "semantic_evaluation")
    graph.add_edge("semantic_evaluation", "build_outputs")
    graph.add_edge("build_outputs", "save_outputs")
    graph.add_edge("save_outputs", END)
    return graph.compile()


def extract_claims(source_output: dict[str, Any], *, news_claim_limit: int) -> list[dict[str, Any]]:
    blocks = source_output.get("analysis_blocks") or {}
    claims: list[dict[str, Any]] = []
    news_claims: list[dict[str, Any]] = []
    news_only = blocks.get("news_only") or {}
    _append_summary_claim(news_claims, news_only, block="news_only")
    for source_key, claim_type in (
        ("positive_signals", "positive_signal"),
        ("negative_signals", "negative_signal"),
        ("key_risks", "key_risk"),
        ("uncertainties", "uncertainty"),
    ):
        _append_string_list_claims(news_claims, news_only, block="news_only", source_key=source_key, claim_type=claim_type)
    claims.extend(news_claims[: max(news_claim_limit, 0)])
    for index, claim in enumerate(claims, start=1):
        claim["claim_id"] = f"NCLAIM_{index:03d}"
    return claims


def annotate_claims_with_evidence(claims: list[dict[str, Any]], evidence_map: dict[str, Any]) -> None:
    for claim in claims:
        declared = _extract_valid_evidence_ids(claim.get("original_item"), evidence_map)
        if not declared:
            declared = _extract_valid_evidence_ids(claim.get("claim", ""), evidence_map)
        claim["declared_evidence_ids"] = declared
        claim["allowed_evidence_domains"] = _allowed_evidence_domains(claim)
        claim["required_evidence_domains"] = ["news"] if claim.get("source_block") == "news_only" else []


def _extract_valid_evidence_ids(payload: Any, evidence_map: dict[str, Any]) -> list[str]:
    text = payload if isinstance(payload, str) else compact_json(payload)
    return list(dict.fromkeys(match for match in EVIDENCE_ID_PATTERN.findall(text) if match in evidence_map))


def _allowed_evidence_domains(claim: dict[str, Any]) -> list[str]:
    return ["news"] if claim.get("source_block") == "news_only" else []


def _append_summary_claim(claims: list[dict[str, Any]], block_payload: dict[str, Any], *, block: str) -> None:
    original = block_payload.get("summary")
    summary = str(original.get("claim") if isinstance(original, dict) else original or "").strip()
    if summary:
        claims.append(_claim(section=f"analysis_blocks.{block}.summary", claim_type="summary", claim=summary, source_block=block, source_key="summary", source_index=None, original_item=original))


def _append_string_list_claims(claims: list[dict[str, Any]], block_payload: dict[str, Any], *, block: str, source_key: str, claim_type: str) -> None:
    for index, item in enumerate(block_payload.get(source_key) or []):
        text = str(item.get("claim") if isinstance(item, dict) else item or "").strip()
        if text:
            claims.append(_claim(section=f"analysis_blocks.{block}.{source_key}[{index}]", claim_type=claim_type, claim=text, source_block=block, source_key=source_key, source_index=index, original_item=item))


def _claim(*, section: str, claim_type: str, claim: str, source_block: str, source_key: str, source_index: int | None, original_item: Any) -> dict[str, Any]:
    return {
        "claim_id": "",
        "section": section,
        "claim_type": claim_type,
        "claim": claim,
        "source_block": source_block,
        "source_key": source_key,
        "source_index": source_index,
        "original_item": original_item,
    }


def dedupe_evidence_catalog(evidence_map: dict[str, Any]) -> dict[str, Any]:
    deduped: dict[str, Any] = {}
    seen: set[str] = set()
    for evidence_id, evidence in evidence_map.items():
        if not isinstance(evidence, dict):
            continue
        identity = compact_json(
            {
                key: evidence.get(key)
                for key in ("source_domain", "period", "time", "event_id", "title", "metric", "value")
            },
            sort_keys=True,
        )
        if identity in seen:
            continue
        seen.add(identity)
        deduped[str(evidence_id)] = evidence
    return deduped


def _call_openai_json(request_payload: dict[str, Any], *, timeout_seconds: float, step: str) -> tuple[dict[str, Any], Any, float]:
    api_key = os.getenv("OPENAI_API_KEY", "").strip()
    if not api_key:
        raise RuntimeError("OPENAI_API_KEY is required for News SY validation.")
    client = OpenAI(api_key=api_key, timeout=timeout_seconds)
    started = time.monotonic()
    response = execute_with_telemetry(
        lambda: client.chat.completions.create(**request_payload),
        request_payload=request_payload,
        model=str(request_payload["model"]),
        step=step,
        usage_getter=lambda result: getattr(result, "usage", None),
    )
    elapsed = time.monotonic() - started
    content = response.choices[0].message.content or ""
    parsed = _extract_json(content)
    usage_obj = response.usage
    usage = usage_obj.model_dump() if hasattr(usage_obj, "model_dump") else usage_obj
    return parsed, usage, elapsed


def _llm_call_record(node: str, request_payload: dict[str, Any], usage: Any, elapsed: float) -> dict[str, Any]:
    return {"node": node, "model": request_payload.get("model"), "elapsed_seconds": round(elapsed, 3), "usage": usage}


def _aggregate_llm_usage(calls: list[dict[str, Any]]) -> dict[str, Any]:
    totals = {"completion_tokens": 0, "prompt_tokens": 0, "total_tokens": 0}
    for call in calls:
        usage = call.get("usage") if isinstance(call.get("usage"), dict) else {}
        for key in totals:
            totals[key] += int(usage.get(key) or 0)
    return {**totals, "call_count": len(calls)}


def _unwrap_handoff_output(document: dict[str, Any]) -> dict[str, Any]:
    output = document.get("output")
    return output if isinstance(output, dict) else document


def _load_evidence_map(source_output: dict[str, Any], handoff_path: Path) -> dict[str, Any]:
    path_raw = source_output.get("evidence_map_path")
    if not path_raw:
        return {}
    path = Path(path_raw).expanduser()
    if not path.is_absolute():
        path = handoff_path.parent / path
    if not path.exists():
        return {}
    payload = _load_json(path)
    return payload if isinstance(payload, dict) else {}


def _extract_json(text: str) -> dict[str, Any]:
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


def _load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _save_json(payload: Any, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


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
