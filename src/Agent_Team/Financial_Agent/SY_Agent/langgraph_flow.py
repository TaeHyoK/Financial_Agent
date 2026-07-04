#!/usr/bin/env python3
import argparse
import copy
import json
import os
from pathlib import Path
from typing import Any, Dict, List, TypedDict
from urllib import error, request

from langgraph.graph import END, START, StateGraph

try:
    from .run_validation import (
        evidence_by_claim,
        get_claims,
        get_key_evidence,
        get_target_entity,
    )
except ImportError:  # pragma: no cover - supports direct script execution
    from run_validation import (
        evidence_by_claim,
        get_claims,
        get_key_evidence,
        get_target_entity,
    )


try:
    from .. import DEFAULT_ENV_FILE, PROJECT_ROOT
except ImportError:  # pragma: no cover - supports direct script execution
    PROJECT_ROOT = Path(__file__).resolve().parents[4]
    DEFAULT_ENV_FILE = PROJECT_ROOT / "configs" / ".env"


DEFAULT_OPENAI_MODEL = "gpt-5.4-mini"


GRAPH_FLOW = [
    "Input Specialist Output",
    "Claim Extraction Node",
    "DART Source Context Node",
    "SY Question 1 Node",
    "Specialist Answer 1 Node",
    "SY Question 2 Node",
    "Specialist Answer 2 Node",
    "SY LLM Evaluation Node",
    "Revision Brief Node",
    "Specialist Report Rewrite Node",
    "Specialist Final Rewrite Node",
    "Verified Handoff Output",
]


class SYGraphState(TypedDict, total=False):
    input_path: str
    env_file: str
    use_llm: bool
    llm_provider: str
    llm_model: str
    llm_timeout: int
    dart_main_path: str
    dart_master_path: str
    report_rewritten: bool
    revision_brief: List[Dict[str, Any]]
    rewrite_history: List[Dict[str, Any]]
    source_output: Dict[str, Any]
    source_context: Dict[str, Any]
    evidence_map: Dict[str, List[Dict[str, Any]]]
    extracted_claims: List[Dict[str, Any]]
    dialogue_trace: List[Dict[str, str]]
    llm_calls: List[Dict[str, Any]]
    llm_evaluation_checks: List[Dict[str, Any]]
    question_1_map: Dict[str, str]
    answer_1_map: Dict[str, str]
    question_2_map: Dict[str, str]
    answer_2_map: Dict[str, str]
    claim_validations: List[Dict[str, Any]]
    final_output: Dict[str, Any]
    verified_financial_report: Dict[str, Any]


def load_env_file(path: str) -> None:
    env_path = Path(path)
    if not env_path.exists():
        return
    for raw_line in env_path.read_text().splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = value


def resolve_llm_provider(provider: str) -> str:
    if provider == "none":
        return "none"
    if provider not in {"auto", "openai"}:
        raise RuntimeError(f"Unsupported LLM provider: {provider}. Only openai is supported.")
    if provider == "openai":
        return "openai"
    if os.getenv("OPENAI_API_KEY"):
        return "openai"
    return "none"


def resolve_llm_model(provider: str, model: str) -> str:
    if model and model != "auto":
        return model
    if provider == "openai":
        return os.getenv("OPENAI_MODEL", DEFAULT_OPENAI_MODEL)
    return model or DEFAULT_OPENAI_MODEL


def call_openai(
    prompt: str,
    model: str,
    timeout: int,
    *,
    max_tokens: int = 1024,
    response_format_json: bool = False,
) -> Dict[str, Any]:
    api_key = os.getenv("OPENAI_API_KEY")
    if not api_key:
        raise RuntimeError("OPENAI_API_KEY is not set")
    base_url = os.getenv("OPENAI_BASE_URL", "https://api.openai.com/v1").rstrip("/")
    payload = {
        "model": model,
        "messages": [{"role": "user", "content": prompt}],
        "temperature": 0.2,
        "max_tokens": max_tokens,
    }
    if response_format_json:
        payload["response_format"] = {"type": "json_object"}
    req = request.Request(
        f"{base_url}/chat/completions",
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    try:
        with request.urlopen(req, timeout=timeout) as response:
            raw = response.read().decode("utf-8")
    except error.HTTPError as exc:
        body = exc.read().decode("utf-8", "ignore")
        raise RuntimeError(f"OpenAI HTTP {exc.code}: {body[:500]}") from exc
    result = json.loads(raw)
    choices = result.get("choices") or []
    if not choices:
        raise RuntimeError(f"OpenAI returned no choices: {result}")
    text = choices[0].get("message", {}).get("content", "").strip()
    if not text:
        raise RuntimeError("OpenAI returned empty text")
    return {"text": text, "usage": result.get("usage", {})}


def clean_llm_text(text: str) -> str:
    lines = []
    for raw_line in text.replace("```", "").splitlines():
        line = raw_line.strip()
        if line:
            lines.append(line)
    return "\n".join(lines).strip()


def extract_json_object(text: str) -> Dict[str, Any]:
    text = text.strip()
    try:
        parsed = json.loads(text)
        return parsed if isinstance(parsed, dict) else {}
    except json.JSONDecodeError:
        pass
    start = text.find("{")
    end = text.rfind("}")
    if start != -1 and end != -1 and end > start:
        try:
            parsed = json.loads(text[start : end + 1])
            return parsed if isinstance(parsed, dict) else {}
        except json.JSONDecodeError:
            return {}
    return {}


def require_llm(state: SYGraphState, node: str) -> str:
    provider = state.get("llm_provider", "none")
    if not state.get("use_llm") or provider == "none":
        raise RuntimeError(f"{node} requires LLM evaluation. Run with --use-llm and a valid OPENAI_API_KEY.")
    if provider != "openai":
        raise RuntimeError(f"Unsupported LLM provider for {node}: {provider}")
    return provider


def llm_generate_required(
    state: SYGraphState,
    node: str,
    prompt: str,
    *,
    max_tokens: int = 1024,
    response_format_json: bool = False,
) -> str:
    state.setdefault("llm_calls", [])
    provider = require_llm(state, node)
    response = call_openai(
        prompt,
        state.get("llm_model", DEFAULT_OPENAI_MODEL),
        state.get("llm_timeout", 60),
        max_tokens=max_tokens,
        response_format_json=response_format_json,
    )
    text = clean_llm_text(response["text"])
    if not text:
        raise RuntimeError(f"{node} returned empty text")
    state["llm_calls"].append(
        {
            "node": node,
            "provider": provider,
            "model": state.get("llm_model"),
            "used_llm": True,
            "status": "ok",
            "usage": response.get("usage", {}),
        }
    )
    return text


def llm_generate_json_required(
    state: SYGraphState,
    node: str,
    prompt: str,
    *,
    max_tokens: int = 1024,
) -> Dict[str, Any]:
    text = llm_generate_required(
        state,
        node,
        prompt,
        max_tokens=max_tokens,
        response_format_json=True,
    )
    parsed = extract_json_object(text)
    if not parsed:
        raise RuntimeError(f"{node} returned non-JSON text")
    return parsed


def summarize_llm_usage(calls: List[Dict[str, Any]]) -> Dict[str, Any]:
    keys = [
        "promptTokenCount",
        "candidatesTokenCount",
        "thoughtsTokenCount",
        "cachedContentTokenCount",
        "totalTokenCount",
        "prompt_tokens",
        "completion_tokens",
        "total_tokens",
    ]
    summary: Dict[str, Any] = {
        "api_call_count": 0,
        "adopted_llm_call_count": 0,
        "fallback_call_count": 0,
        "by_field": {key: 0 for key in keys},
    }
    for call in calls:
        usage = call.get("usage") or {}
        if usage:
            summary["api_call_count"] += 1
            for key in keys:
                summary["by_field"][key] += int(usage.get(key) or 0)
        if call.get("used_llm"):
            summary["adopted_llm_call_count"] += 1
        elif call.get("status", "").startswith("fallback"):
            summary["fallback_call_count"] += 1
    return summary


def append_dialogue(state: SYGraphState, node: str, role: str, content: str) -> None:
    state.setdefault("dialogue_trace", []).append({"node": node, "role": role, "content": content})


def load_json_if_exists(path_value: str | None) -> Dict[str, Any]:
    if not path_value:
        return {}
    path = Path(path_value)
    if not path.exists():
        return {}
    payload = json.loads(path.read_text(encoding="utf-8"))
    return payload if isinstance(payload, dict) else {}


def compact_dart_main(dart_main: Dict[str, Any]) -> Dict[str, Any]:
    metrics = {}
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
        "periods": dart_main.get("periods", {}),
        "metrics_by_key": metrics,
    }


def compact_dart_master(dart_master: Dict[str, Any]) -> Dict[str, Any]:
    compact: Dict[str, Any] = {}
    for section_key, section in dart_master.items():
        if not isinstance(section, dict):
            continue
        tables = section.get("tables") or []
        first_table = tables[0] if tables and isinstance(tables[0], dict) else {}
        items = {}
        for item_key, item in (first_table.get("items_by_key") or {}).items():
            if not isinstance(item, dict):
                continue
            items[item_key] = {
                "display_name": item.get("display_name"),
                "current_numeric": item.get("current_numeric"),
                "previous_numeric": item.get("previous_numeric"),
                "values_by_period_key": item.get("values_by_period_key", {}),
            }
        compact[section_key] = {
            "statement_name": section.get("statement_name"),
            "unit": first_table.get("unit"),
            "periods": first_table.get("periods", {}),
            "items_by_key": items,
        }
    return compact


def build_source_context(state: SYGraphState) -> Dict[str, Any]:
    dart_main = load_json_if_exists(state.get("dart_main_path"))
    dart_master = load_json_if_exists(state.get("dart_master_path"))
    return {
        "mode": "llm_context_only",
        "source_paths": {
            "dart_main": state.get("dart_main_path", ""),
            "dart_master": state.get("dart_master_path", ""),
        },
        "dart_main": compact_dart_main(dart_main) if dart_main else {},
        "dart_master": compact_dart_master(dart_master) if dart_master else {},
    }


def input_specialist_output_node(state: SYGraphState) -> SYGraphState:
    data = json.loads(Path(state["input_path"]).read_text())
    state["source_output"] = data
    append_dialogue(state, "Input Specialist Output", "system", "Financial Analyst Agent output을 로드했다.")
    return state


def claim_extraction_node(state: SYGraphState) -> SYGraphState:
    data = state["source_output"]
    state["extracted_claims"] = get_claims(data)
    state["evidence_map"] = evidence_by_claim(get_key_evidence(data))
    append_dialogue(state, "Claim Extraction Node", "system", f"{len(state['extracted_claims'])}개 claim을 추출했다.")
    return state


def dart_source_context_node(state: SYGraphState) -> SYGraphState:
    state["source_context"] = build_source_context(state)
    append_dialogue(
        state,
        "DART Source Context Node",
        "sy",
        "DART 원천 파일을 LLM 평가용 source context로 로드했다. 이 노드는 rule-based 판단을 만들지 않는다.",
    )
    return state


def sy_question_1_node(state: SYGraphState) -> SYGraphState:
    state["question_1_map"] = {claim["claim_id"]: "왜 이런 의견을 냈어?" for claim in state["extracted_claims"]}
    append_dialogue(state, "SY Question 1 Node", "sy", "모든 claim에 대해 '왜 이런 의견을 냈어?' 질문을 적용했다.")
    return state


def specialist_answer_1_node(state: SYGraphState) -> SYGraphState:
    answers: Dict[str, str] = {}
    for claim in state["extracted_claims"]:
        claim_id = claim["claim_id"]
        evidence_items = state["evidence_map"].get(claim_id, [])
        prompt = (
            "너는 Financial Analyst Agent다. SY Agent의 질문 '왜 이런 의견을 냈어?'에 답하라. "
            "입력 claim, evidence, DART source context에 없는 새 근거는 추가하지 말고 한국어 2문장 이내로 답하라. "
            "근거가 부족하면 부족하다고 명시하라.\n\n"
            f"claim={json.dumps(claim, ensure_ascii=False)}\n"
            f"evidence={json.dumps(evidence_items, ensure_ascii=False)}\n"
            f"source_context={json.dumps(state.get('source_context', {}), ensure_ascii=False)[:30000]}"
        )
        answers[claim_id] = llm_generate_required(state, f"Specialist Answer 1 Node:{claim_id}", prompt)
    state["answer_1_map"] = answers
    append_dialogue(state, "Specialist Answer 1 Node", "specialist", "각 claim에 대한 근거 답변을 생성했다.")
    return state


def sy_question_2_node(state: SYGraphState) -> SYGraphState:
    questions: Dict[str, str] = {}
    for claim in state["extracted_claims"]:
        claim_id = claim["claim_id"]
        prompt = (
            "너는 SY Agent다. Financial Analyst의 답변을 double check하는 질문을 한국어로 작성하라. "
            "질문 초점은 입력 데이터와 evidence만으로 답변이 충분한지 여부다. "
            "필요하다고 판단되면 추가 질문을 자율적으로 이어서 작성하되, 질문 개수 제한을 언급하지 말고 검증에 필요한 질문만 출력하라.\n\n"
            f"claim={json.dumps(claim, ensure_ascii=False)}\n"
            f"answer={state['answer_1_map'][claim_id]}"
        )
        questions[claim_id] = llm_generate_required(state, f"SY Question 2 Node:{claim_id}", prompt)
    state["question_2_map"] = questions
    append_dialogue(state, "SY Question 2 Node", "sy", "각 claim 답변에 대해 필요한 경우 추가 질문을 포함한 double check 질문을 생성했다.")
    return state


def specialist_answer_2_node(state: SYGraphState) -> SYGraphState:
    answers: Dict[str, str] = {}
    for claim in state["extracted_claims"]:
        claim_id = claim["claim_id"]
        evidence_items = state["evidence_map"].get(claim_id, [])
        prompt = (
            "너는 Financial Analyst Agent다. SY Agent의 double check 질문과 추가 질문에 답하라. "
            "보고서 내부 claim, evidence, DART source context만 사용하고 한국어 2문장 이내로 답하라. "
            "근거가 부족하거나 숫자 정합성을 확인할 수 없으면 그 한계를 인정하라.\n\n"
            f"question={state['question_2_map'][claim_id]}\n"
            f"claim={json.dumps(claim, ensure_ascii=False)}\n"
            f"evidence={json.dumps(evidence_items, ensure_ascii=False)}\n"
            f"source_context={json.dumps(state.get('source_context', {}), ensure_ascii=False)[:30000]}"
        )
        answers[claim_id] = llm_generate_required(state, f"Specialist Answer 2 Node:{claim_id}", prompt)
    state["answer_2_map"] = answers
    append_dialogue(state, "Specialist Answer 2 Node", "specialist", "각 claim에 대한 double check 답변을 생성했다.")
    return state


def sy_llm_evaluation_node(state: SYGraphState) -> SYGraphState:
    validations: List[Dict[str, Any]] = []
    for claim in state["extracted_claims"]:
        claim_id = claim["claim_id"]
        evidence_items = state["evidence_map"].get(claim_id, [])
        prompt = (
            "당신은 SY Agent입니다. Financial Analyst Agent의 claim이 왜 이런 의견을 냈는지 검증합니다.\n"
            "코드 기반 rule 판정은 사용하지 않습니다. 아래 입력만 근거로 의미 판단, 근거 충분성, 숫자 정합성, 과장 여부를 평가하세요.\n"
            "새로운 투자 의견, buy/sell/hold, 목표주가, deterministic price forecast는 만들지 마세요.\n"
            "반드시 JSON 객체 하나만 출력하세요.\n\n"
            "decision 값은 다음 중 하나만 사용하세요.\n"
            "- keep: claim을 그대로 다음 단계로 넘겨도 됨\n"
            "- revise: 근거는 있으나 표현, 범위, 숫자, 기간 설명 보강이 필요함\n"
            "- hallucination_candidate: 입력 근거만으로는 claim을 설명하기 어려움\n"
            "- remove: 입력 근거와 충돌하거나 다음 단계에서 제외해야 함\n\n"
            "출력 형식:\n"
            "{\n"
            '  "claim_id": "string",\n'
            '  "support_level": "supported | weakly_supported | unsupported | contradicted",\n'
            '  "decision": "keep | revise | hallucination_candidate | remove",\n'
            '  "evidence_refs": ["string"],\n'
            '  "source_context_refs": ["string"],\n'
            '  "reason_ko": "string",\n'
            '  "revision_suggestion_ko": "string"\n'
            "}\n\n"
            f"claim={json.dumps(claim, ensure_ascii=False)}\n"
            f"question_1={state['question_1_map'][claim_id]}\n"
            f"answer_1={state['answer_1_map'][claim_id]}\n"
            f"question_2={state['question_2_map'][claim_id]}\n"
            f"answer_2={state['answer_2_map'][claim_id]}\n"
            f"evidence={json.dumps(evidence_items, ensure_ascii=False)}\n"
            f"source_context={json.dumps(state.get('source_context', {}), ensure_ascii=False)[:30000]}"
        )
        evaluation = llm_generate_json_required(state, f"SY LLM Evaluation Node:{claim_id}", prompt)
        decision = str(evaluation.get("decision") or "").strip()
        if decision not in {"keep", "revise", "hallucination_candidate", "remove"}:
            raise RuntimeError(f"Invalid Financial SY decision for {claim_id}: {decision}")
        support_level = str(evaluation.get("support_level") or "").strip()
        if support_level not in {"supported", "weakly_supported", "unsupported", "contradicted"}:
            raise RuntimeError(f"Invalid Financial SY support_level for {claim_id}: {support_level}")
        evidence_refs = evaluation.get("evidence_refs")
        if not isinstance(evidence_refs, list):
            evidence_refs = [item.get("evidence_id") for item in evidence_items if item.get("evidence_id")]
        source_context_refs = evaluation.get("source_context_refs")
        if not isinstance(source_context_refs, list):
            source_context_refs = []
        validations.append(
            {
                "claim_id": claim_id,
                "claim_ko": claim.get("claim_ko", ""),
                "claim_origin": claim.get("claim_origin", ""),
                "section_path": claim.get("section_path", ""),
                "financial_dimension": claim.get("financial_dimension", ""),
                "question_1_ko": state["question_1_map"][claim_id],
                "answer_1_ko": state["answer_1_map"][claim_id],
                "question_2_ko": state["question_2_map"][claim_id],
                "answer_2_ko": state["answer_2_map"][claim_id],
                "evidence_refs": [str(item) for item in evidence_refs],
                "source_context_refs": [str(item) for item in source_context_refs],
                "support_level": support_level,
                "decision": decision,
                "reason_ko": str(evaluation.get("reason_ko") or ""),
                "revision_suggestion_ko": str(evaluation.get("revision_suggestion_ko") or ""),
            }
        )
    state["claim_validations"] = validations
    state["llm_evaluation_checks"] = [
        {
            "check_id": "LLM001",
            "check_name": "LLM-only claim evaluation",
            "status": "completed",
            "summary_ko": "모든 Financial claim의 최종 판단은 LLM 평가 응답으로 생성했다.",
        }
    ]
    append_dialogue(state, "SY LLM Evaluation Node", "sy", "LLM-only keep/revise/hallucination_candidate/remove 판단을 생성했다.")
    return state


def all_claims_keep(state: SYGraphState) -> bool:
    validations = state.get("claim_validations", [])
    return bool(validations) and all(item.get("decision") == "keep" for item in validations)


def next_after_evaluation(state: SYGraphState) -> str:
    if all_claims_keep(state):
        return "finalize"
    return "rewrite"


def build_revision_brief_node(state: SYGraphState) -> SYGraphState:
    state["revision_brief"] = [
        {
            "claim_id": item.get("claim_id"),
            "section_path": item.get("section_path", ""),
            "decision": item.get("decision"),
            "issue_ko": item.get("reason_ko", ""),
            "evidence_to_preserve": _merge_unique_strings(
                item.get("evidence_refs", []),
                item.get("source_context_refs", []),
            ),
            "rewrite_direction_ko": item.get("revision_suggestion_ko")
            or _default_rewrite_direction(item.get("decision")),
            "wording_constraints_ko": (
                "SY 질문/답변을 그대로 붙이지 말고, 입력 근거로 설명 가능한 범위에서 "
                "분석 문장에 자연스럽게 반영한다."
            ),
        }
        for item in state.get("claim_validations", [])
        if item.get("decision") != "keep"
    ]
    append_dialogue(
        state,
        "Revision Brief Node",
        "sy",
        "비-keep claim의 SY 근거와 수정 방향을 재작성 브리프로 정리했다.",
    )
    return state


def _merge_unique_strings(*values: Any) -> List[str]:
    merged: List[str] = []
    seen: set[str] = set()
    for value_list in values:
        if not isinstance(value_list, list):
            continue
        for value in value_list:
            text = str(value)
            if text and text not in seen:
                merged.append(text)
                seen.add(text)
    return merged


def _default_rewrite_direction(decision: Any) -> str:
    if decision == "revise":
        return "근거가 설명되는 범위로 표현을 약화하고 숫자, 기간, 근거 연결을 보강한다."
    if decision == "remove":
        return "입력 근거와 충돌하거나 제외가 필요한 주장은 보고서에서 제거한다."
    return "입력 근거로 설명하기 어려운 주장은 삭제하거나 보수적 표현으로 완전히 다시 작성한다."


def specialist_report_rewrite_node(state: SYGraphState) -> SYGraphState:
    prompt = (
        "너는 Financial Analyst Agent다. SY Agent 검증 결과와 revision brief를 반영해 보고서를 한 번만 다시 작성한다.\n"
        "반드시 기존 Financial Analyst report JSON과 같은 top-level 구조를 유지한 JSON 객체 하나만 출력하라.\n"
        "새로운 외부 사실을 추가하지 말고, 제공된 source report, DART source context, SY validation feedback, revision brief만 사용하라.\n"
        "SY 질문/답변이나 검증 메타데이터를 본문에 그대로 붙여넣지 말고, 문맥에 맞는 분석 문장으로 자연스럽게 녹여 써라.\n"
        "revise 항목은 표현, 범위, 숫자, 기간 기준, 근거 연결을 보강하라.\n"
        "hallucination_candidate 또는 remove 항목은 제외하거나, 입력 근거로 설명 가능한 보수적 표현으로 완전히 다시 작성하라.\n"
        "buy/sell/hold, 목표주가, deterministic price forecast는 만들지 마라.\n\n"
        f"source_report={json.dumps(state.get('source_output', {}), ensure_ascii=False)}\n"
        f"revision_brief={json.dumps(state.get('revision_brief', []), ensure_ascii=False)}\n"
        f"claim_validations={json.dumps(state.get('claim_validations', []), ensure_ascii=False)}\n"
        f"source_context={json.dumps(state.get('source_context', {}), ensure_ascii=False)[:30000]}"
    )
    rewritten = llm_generate_json_required(
        state,
        "Specialist Report Rewrite Node",
        prompt,
        max_tokens=12000,
    )
    state.setdefault("rewrite_history", []).append(
        {
            "mode": "single_pass_contextual_rewrite_no_reverification",
            "non_keep_claim_ids": [
                item.get("claim_id")
                for item in state.get("claim_validations", [])
                if item.get("decision") != "keep"
            ],
        }
    )
    state["source_output"] = rewritten
    state["report_rewritten"] = True
    append_dialogue(
        state,
        "Specialist Report Rewrite Node",
        "specialist",
        "SY 검증 근거와 revision brief를 반영해 Financial report를 재작성했다. 재작성본은 다시 SY 검증하지 않는다.",
    )
    return state


def specialist_final_rewrite_node(state: SYGraphState) -> SYGraphState:
    data = state["source_output"]
    validations = state["claim_validations"]
    kept = [item for item in validations if item["decision"] == "keep"]
    revised = [item for item in validations if item["decision"] == "revise"]
    hallucination_candidates = [item for item in validations if item["decision"] == "hallucination_candidate"]
    removed = [item for item in validations if item["decision"] == "remove"]
    overall_status = "fail" if removed else "needs_revision" if revised or hallucination_candidates else "pass"

    output = {
        "agent_name": "SY Agent",
        "agent_role": "Financial Analyst Output LLM Verifier",
        "output_version": "1.0",
        "output_mode": "financial_analyst_output_validation",
        "target_entity": get_target_entity(data),
        "source_agent": {
            "agent_name": data.get("agent_name", ""),
            "output_version": data.get("output_version", ""),
            "output_path": state["input_path"],
        },
        "graph_flow": GRAPH_FLOW,
        "validation_summary": {
            "overall_status": overall_status,
            "summary_ko": "SY Agent는 Financial Analyst Agent output이 입력 데이터와 source context로 설명 가능한지 LLM-only 방식으로 검증했다.",
            "evaluation_mode": "llm_only_question_answer_verification",
            "total_claims": len(state["extracted_claims"]),
            "kept_claims": len(kept),
            "revised_claims": len(revised),
            "hallucination_candidate_claims": len(hallucination_candidates),
            "removed_claims": len(removed),
        },
        "llm_evaluation_checks": state.get("llm_evaluation_checks", []),
        "source_context": state.get("source_context", {}),
        "dialogue_trace": state["dialogue_trace"],
        "claim_validations": validations,
        "verified_output": {
            "kept_claim_ids": [item["claim_id"] for item in kept],
            "kept_claims": [
                {
                    "claim_id": item["claim_id"],
                    "claim_ko": item["claim_ko"],
                    "claim_origin": item.get("claim_origin", ""),
                    "section_path": item.get("section_path", ""),
                    "financial_dimension": item.get("financial_dimension", ""),
                    "evidence_refs": item["evidence_refs"],
                    "source_context_refs": item.get("source_context_refs", []),
                    "reason_ko": item["reason_ko"],
                }
                for item in kept
            ],
            "revision_claims": [
                {
                    "claim_id": item["claim_id"],
                    "claim_ko": item["claim_ko"],
                    "claim_origin": item.get("claim_origin", ""),
                    "section_path": item.get("section_path", ""),
                    "financial_dimension": item.get("financial_dimension", ""),
                    "evidence_refs": item["evidence_refs"],
                    "source_context_refs": item.get("source_context_refs", []),
                    "revision_reason_ko": item["reason_ko"],
                    "revision_suggestion_ko": item.get("revision_suggestion_ko", ""),
                }
                for item in revised
            ],
            "hallucination_candidate_claims": [
                {
                    "claim_id": item["claim_id"],
                    "claim_ko": item["claim_ko"],
                    "claim_origin": item.get("claim_origin", ""),
                    "section_path": item.get("section_path", ""),
                    "financial_dimension": item.get("financial_dimension", ""),
                    "reason_ko": item["reason_ko"],
                }
                for item in hallucination_candidates
            ],
            "removed_claims": [
                {
                    "claim_id": item["claim_id"],
                    "claim_ko": item["claim_ko"],
                    "claim_origin": item.get("claim_origin", ""),
                    "section_path": item.get("section_path", ""),
                    "financial_dimension": item.get("financial_dimension", ""),
                    "remove_reason_ko": item["reason_ko"],
                }
                for item in removed
            ],
        },
        "revision_brief": state.get("revision_brief", []),
        "report_rewritten": bool(state.get("report_rewritten", False)),
        "confidence": {
            "grade": "low" if removed or hallucination_candidates else "medium" if revised else "high",
            "reason_ko": "검증은 Financial Analyst 답변과 SY LLM 평가 결과만으로 생성했다.",
        },
    }
    state["final_output"] = output
    append_dialogue(state, "Specialist Final Rewrite Node", "specialist", "제외 대상 claim을 제외한 verified output을 생성했다.")
    return state


def verified_handoff_output_node(state: SYGraphState) -> SYGraphState:
    append_dialogue(state, "Verified Handoff Output", "system", "SY Agent 검증 output을 생성했다.")
    state["final_output"]["dialogue_trace"] = state["dialogue_trace"]
    state["verified_financial_report"] = build_verified_financial_report(
        state["source_output"],
        state["claim_validations"],
        state.get("source_context"),
        rewrite_reflected=bool(state.get("report_rewritten", False)),
        revision_brief=state.get("revision_brief", []),
    )
    return state


def build_verified_financial_report(
    source_report: Dict[str, Any],
    validations: List[Dict[str, Any]],
    source_context: Dict[str, Any] | None = None,
    *,
    rewrite_reflected: bool = False,
    revision_brief: List[Dict[str, Any]] | None = None,
) -> Dict[str, Any]:
    report = copy.deepcopy(source_report)
    summary = {
        "total_claims": len(validations),
        "kept_claims": sum(1 for item in validations if item.get("decision") == "keep"),
        "revised_claims": sum(1 for item in validations if item.get("decision") == "revise"),
        "hallucination_candidate_claims": sum(
            1 for item in validations if item.get("decision") == "hallucination_candidate"
        ),
        "removed_claims": sum(1 for item in validations if item.get("decision") == "remove"),
        "report_rewritten": rewrite_reflected,
        "rewrite_policy": "single_pass_contextual_rewrite_no_reverification"
        if rewrite_reflected
        else "no_rewrite_all_claims_kept_or_final_validation_only",
    }

    if rewrite_reflected:
        report["report_status"] = "sy_revision_reflected_no_reverification"
        report["verification_summary"] = summary
        report["sy_validation"] = {
            "verifier_agent": "SY Agent",
            "verification_mode": "llm_only_question_answer_verification",
            "revision_brief": copy.deepcopy(revision_brief or []),
            "summary": copy.deepcopy(summary),
        }
        if "sy_handoff" in report and isinstance(report.get("sy_handoff"), dict):
            report["sy_handoff"].setdefault("reconciliation_flags", []).append(
                {
                    "flag_ko": "SY 검증 근거와 revision brief를 반영해 보고서를 재작성했다. 재작성본은 추가 SY 검증을 수행하지 않았다.",
                    "severity": "medium",
                    "action_for_sy": "use_rewritten_report_with_recorded_validation_basis",
                }
            )
        if source_context:
            report["source_context_summary"] = {
                "mode": source_context.get("mode", "llm_context_only"),
                "summary_ko": "DART source는 rule-based audit 없이 LLM 평가 참고자료로 사용되었다.",
                "source_paths": source_context.get("source_paths", {}),
            }
        return report

    retained_claim_ids = {
        item["claim_id"]
        for item in validations
        if item.get("decision") in {"keep", "revise"}
    }
    revised_claim_ids = [
        item["claim_id"]
        for item in validations
        if item.get("decision") == "revise"
    ]
    blocked_claim_ids = [
        item["claim_id"]
        for item in validations
        if item.get("decision") in {"hallucination_candidate", "remove"}
    ]

    if "sy_handoff" in report:
        sy_handoff = report.setdefault("sy_handoff", {})
        sy_handoff["financial_claims"] = [
            claim
            for claim in sy_handoff.get("financial_claims", [])
            if claim.get("claim_id") in retained_claim_ids
        ]
        sy_handoff["key_evidence"] = [
            evidence
            for evidence in sy_handoff.get("key_evidence", [])
            if evidence.get("claim_id") in retained_claim_ids
        ]
        if revised_claim_ids:
            sy_handoff.setdefault("reconciliation_flags", []).append(
                {
                    "flag_ko": f"SY 검증에서 표현 보강이 필요한 claim: {', '.join(revised_claim_ids)}",
                    "severity": "medium",
                    "action_for_sy": "revise_before_final_synthesis",
                }
            )
        if blocked_claim_ids:
            sy_handoff.setdefault("reconciliation_flags", []).append(
                {
                    "flag_ko": f"SY 검증에서 근거 부족 또는 충돌로 제외된 claim: {', '.join(blocked_claim_ids)}",
                    "severity": "high",
                    "action_for_sy": "block_specific_claim",
                }
            )
        if source_context:
            sy_handoff.setdefault("reconciliation_flags", []).append(
                {
                    "flag_ko": "SY 검증은 DART 원천 파일을 deterministic audit이 아니라 LLM 평가 context로 사용했다.",
                    "severity": "medium",
                    "action_for_sy": "record_llm_source_context",
                }
            )
    else:
        report["financial_claims"] = [
            claim
            for claim in report.get("financial_claims", [])
            if claim.get("claim_id") in retained_claim_ids
        ]
        report["key_evidence"] = [
            evidence
            for evidence in report.get("key_evidence", [])
            if evidence.get("claim_id") in retained_claim_ids
        ]

    if (revised_claim_ids or blocked_claim_ids) and isinstance(report.get("main_view"), dict):
        main_cautions = report["main_view"].setdefault("main_cautions", [])
        if revised_claim_ids:
            caution = f"SY 검증에서 표현 보강이 필요한 claim은 상위 단계에서 보수적으로 사용한다: {', '.join(revised_claim_ids)}."
            if caution not in main_cautions:
                main_cautions.append(caution)
        if blocked_claim_ids:
            caution = f"SY 검증에서 근거 부족 또는 충돌로 제외된 claim은 최종 리포트 해석에서 제외한다: {', '.join(blocked_claim_ids)}."
            if caution not in main_cautions:
                main_cautions.append(caution)

    if source_context:
        report["source_context_summary"] = {
            "mode": source_context.get("mode", "llm_context_only"),
            "summary_ko": "DART source는 rule-based audit 없이 LLM 평가 참고자료로 사용되었다.",
            "source_paths": source_context.get("source_paths", {}),
        }

    return report


def build_graph():
    graph = StateGraph(SYGraphState)
    graph.add_node("input_specialist_output", input_specialist_output_node)
    graph.add_node("claim_extraction", claim_extraction_node)
    graph.add_node("dart_source_context", dart_source_context_node)
    graph.add_node("sy_question_1", sy_question_1_node)
    graph.add_node("specialist_answer_1", specialist_answer_1_node)
    graph.add_node("sy_question_2", sy_question_2_node)
    graph.add_node("specialist_answer_2", specialist_answer_2_node)
    graph.add_node("sy_llm_evaluation", sy_llm_evaluation_node)
    graph.add_node("build_revision_brief", build_revision_brief_node)
    graph.add_node("specialist_report_rewrite", specialist_report_rewrite_node)
    graph.add_node("specialist_final_rewrite", specialist_final_rewrite_node)
    graph.add_node("verified_handoff_output", verified_handoff_output_node)

    graph.add_edge(START, "input_specialist_output")
    graph.add_edge("input_specialist_output", "claim_extraction")
    graph.add_edge("claim_extraction", "dart_source_context")
    graph.add_edge("dart_source_context", "sy_question_1")
    graph.add_edge("sy_question_1", "specialist_answer_1")
    graph.add_edge("specialist_answer_1", "sy_question_2")
    graph.add_edge("sy_question_2", "specialist_answer_2")
    graph.add_edge("specialist_answer_2", "sy_llm_evaluation")
    graph.add_conditional_edges(
        "sy_llm_evaluation",
        next_after_evaluation,
        {
            "rewrite": "build_revision_brief",
            "finalize": "specialist_final_rewrite",
        },
    )
    graph.add_edge("build_revision_brief", "specialist_report_rewrite")
    graph.add_edge("specialist_report_rewrite", "specialist_final_rewrite")
    graph.add_edge("specialist_final_rewrite", "verified_handoff_output")
    graph.add_edge("verified_handoff_output", END)
    return graph.compile()


def infer_dart_source_path(input_path: Path, filename: str) -> Path | None:
    candidates = [
        input_path.parent / filename,
        input_path.parent.parent / filename,
    ]
    for candidate in candidates:
        if candidate.exists():
            return candidate.resolve()
    return None


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--dart-main", default=None, help="dart_main.json path for Financial SY LLM source context.")
    parser.add_argument("--dart-master", default=None, help="dart_master.json path for Financial SY LLM source context.")
    parser.add_argument("--skip-source-audit", action="store_true", help="Deprecated. DART files are now used as LLM context only.")
    parser.add_argument("--trace-output")
    parser.add_argument("--verified-output")
    parser.add_argument("--verified-report-output")
    parser.add_argument("--audit-output")
    parser.add_argument("--critic-output")
    parser.add_argument("--env-file", default=str(DEFAULT_ENV_FILE))
    parser.add_argument("--use-llm", action="store_true", default=True, help="LLM evaluation is required for Financial SY.")
    parser.add_argument("--llm-provider", default="openai", choices=["auto", "openai"])
    parser.add_argument("--llm-model", default="auto")
    parser.add_argument("--llm-timeout", type=int, default=300)
    args = parser.parse_args()

    load_env_file(args.env_file)
    provider = resolve_llm_provider(args.llm_provider)
    llm_model = resolve_llm_model(provider, args.llm_model)
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
    app = build_graph()
    final_state = app.invoke(
        {
            "input_path": str(input_path),
            "env_file": args.env_file,
            "use_llm": args.use_llm,
            "llm_provider": provider,
            "llm_model": llm_model,
            "llm_timeout": args.llm_timeout,
            "dart_main_path": str(dart_main_path) if dart_main_path else "",
            "dart_master_path": str(dart_master_path) if dart_master_path else "",
            "report_rewritten": False,
            "revision_brief": [],
            "rewrite_history": [],
            "dialogue_trace": [],
            "llm_calls": [],
        }
    )

    output_path = Path(args.output)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(final_state["final_output"], ensure_ascii=False, indent=2) + "\n")

    optional_outputs = {
        args.verified_output: final_state["final_output"]["verified_output"],
        args.verified_report_output: final_state["verified_financial_report"],
        args.audit_output: {
            "llm_evaluation_checks": final_state["final_output"]["llm_evaluation_checks"],
            "source_context": final_state["final_output"].get("source_context", {}),
            "dialogue_trace": final_state["final_output"]["dialogue_trace"],
            "claim_validations": final_state["final_output"]["claim_validations"],
        },
        args.critic_output: {
            "status": "not_used",
            "reason_ko": "단순화된 SY Agent는 Critic queue를 생성하지 않는다.",
        },
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
        trace = {
            "graph_flow": GRAPH_FLOW,
            "llm": {
                "enabled": args.use_llm,
                "provider": provider,
                "model": llm_model if args.use_llm else None,
                "env_file": args.env_file,
                "api_key_loaded": bool(os.getenv("OPENAI_API_KEY")) if provider == "openai" else False,
            },
            "llm_usage_summary": summarize_llm_usage(final_state.get("llm_calls", [])),
            "llm_calls": final_state.get("llm_calls", []),
            "source_context": final_state.get("source_context", {}),
            "report_rewritten": final_state.get("report_rewritten", False),
            "revision_brief": final_state.get("revision_brief", []),
            "rewrite_history": final_state.get("rewrite_history", []),
            "dialogue_trace": final_state.get("dialogue_trace", []),
        }
        trace_path.write_text(json.dumps(trace, ensure_ascii=False, indent=2) + "\n")

    print(output_path)


if __name__ == "__main__":
    main()
