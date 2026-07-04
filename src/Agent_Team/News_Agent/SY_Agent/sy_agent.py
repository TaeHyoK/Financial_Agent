#!/usr/bin/env python3
from __future__ import annotations

import argparse
import copy
import json
import os
import re
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, TypedDict

from langgraph.graph import END, START, StateGraph
from openai import OpenAI
from tqdm.auto import tqdm


DEFAULT_MODEL = "gpt-5.4-mini"


def _project_root() -> Path:
    return Path(__file__).resolve().parents[4]


GRAPH_FLOW = [
    "Input Specialist Output",
    "Claim Extraction Node",
    "SY Question 1 Node",
    "Specialist Answer 1 Node",
    "SY Question 2 Node",
    "Specialist Answer 2 Node",
    "SY LLM Evaluation Node",
    "Revision Brief Node",
    "Specialist Report Rewrite Node",
    "Claim Validation Output",
]
INVESTMENT_DECISION_TERMS = [
    "매수",
    "매도",
    "보유",
    "목표주가",
    "목표가",
    "투자의견",
    "비중확대",
    "비중축소",
    "buy",
    "sell",
    "hold",
    "target price",
    "price target",
]
EVIDENCE_ID_PATTERN = re.compile(
    r"\b(?:NEWS_SUMMARY_\d{4}-\d{2}|NEWS_RAW_\d{4}-\d{2}_\d+|DART_[A-Z0-9_]+|YF_[A-Z0-9_]+)\b"
)
SUPPORT_LEVEL_TO_DECISION = {
    "supported": "keep",
    "weakly_supported": "revise",
    "unsupported": "hallucination_candidate",
    "contradicted": "remove",
}

@dataclass(frozen=True)
class OutputPaths:
    output_dir: Path
    verified_output: Path
    verified_report: Path
    question_answer_log: Path
    audit_trace: Path
    critic_queue: Path


class NewsSYState(TypedDict, total=False):
    handoff_path: Path
    paths: OutputPaths
    model: str
    claim_limit: int
    timeout_seconds: float
    report_rewritten: bool
    revision_brief: list[dict[str, Any]]
    rewrite_history: list[dict[str, Any]]
    started_at: float
    handoff_document: dict[str, Any]
    source_output: dict[str, Any]
    evidence_map: dict[str, Any]
    claims: list[dict[str, Any]]
    questions_1: list[dict[str, Any]]
    answers_1: list[dict[str, Any]]
    questions_2: list[dict[str, Any]]
    answers_2: list[dict[str, Any]]
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
        question_answer_log=output_dir / "sy_question_answer_log.json",
        audit_trace=output_dir / "sy_audit_trace.json",
        critic_queue=output_dir / "critic_queue.json",
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
    print("News SY Agent 검증 완료")
    print(f"입력 파일: {handoff_path}")
    print(f"검증 결과: {paths.verified_output}")
    print(f"Strategy 입력용 검증 handoff: {paths.verified_report}")
    print(f"질문/답변 로그: {paths.question_answer_log}")
    print(f"감사 trace: {paths.audit_trace}")
    print(f"critic queue: {paths.critic_queue}")
    print(json.dumps(result["summary"], ensure_ascii=False, indent=2))


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run News SY Agent question-answer validation.")
    parser.add_argument("--input", required=True, help="news_agent_handoff.json path.")
    parser.add_argument("--output-dir", default=None, help="Defaults to <handoff_dir>/sy_agent.")
    parser.add_argument("--model", default=None, help="Defaults to NEWS_SY_AGENT_LLM_MODEL or gpt-5.4-mini.")
    parser.add_argument("--env-path", default=None, help="Optional .env path loaded after News/.env.")
    parser.add_argument("--news-claim-limit", type=int, default=10, help="Max claims from news_only block.")
    parser.add_argument("--timeout-seconds", type=float, default=300.0, help="OpenAI request timeout.")
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
        tqdm.write("News SY Agent: LangGraph validation and single-pass rewrite flow start")
    app = build_news_sy_graph()
    final_state = app.invoke(
        {
            "handoff_path": handoff_path,
            "paths": paths,
            "model": model,
            "claim_limit": claim_limit,
            "timeout_seconds": timeout_seconds,
            "report_rewritten": False,
            "revision_brief": [],
            "rewrite_history": [],
            "started_at": time.monotonic(),
            "llm_calls": [],
        }
    )
    return final_state["verified"]


def load_handoff_node(state: NewsSYState) -> NewsSYState:
    handoff_path = state["handoff_path"]
    handoff_document = _load_json(handoff_path)
    source_output = _unwrap_handoff_output(handoff_document)
    state["handoff_document"] = handoff_document
    state["source_output"] = source_output
    state["evidence_map"] = _load_evidence_map(source_output, handoff_path)
    return state


def extract_claims_node(state: NewsSYState) -> NewsSYState:
    claims = extract_claims(state["source_output"], news_claim_limit=state["claim_limit"])
    annotate_claims_with_evidence(claims, state["evidence_map"])
    state["claims"] = claims
    state["questions_1"] = build_questions(claims, round_no=1)
    return state


def answer_round_1_node(state: NewsSYState) -> NewsSYState:
    answers, llm_call = ask_news_agent(
        source_output=state["source_output"],
        evidence_map=state["evidence_map"],
        questions=state["questions_1"],
        model=state["model"],
        timeout_seconds=state["timeout_seconds"],
        round_no=1,
    )
    state["answers_1"] = answers
    state.setdefault("llm_calls", []).append(llm_call)
    return state


def answer_round_2_node(state: NewsSYState) -> NewsSYState:
    state["questions_2"] = build_followup_questions(state["claims"], state["answers_1"])
    answers, llm_call = ask_news_agent(
        source_output=state["source_output"],
        evidence_map=state["evidence_map"],
        questions=state["questions_2"],
        model=state["model"],
        timeout_seconds=state["timeout_seconds"],
        round_no=2,
    )
    state["answers_2"] = answers
    state.setdefault("llm_calls", []).append(llm_call)
    return state


def evaluate_answers_node(state: NewsSYState) -> NewsSYState:
    evaluations, llm_call = evaluate_answers(
        claims=state["claims"],
        answers_1=state["answers_1"],
        answers_2=state["answers_2"],
        model=state["model"],
        timeout_seconds=state["timeout_seconds"],
    )
    state["evaluations"] = evaluations
    state.setdefault("llm_calls", []).append(llm_call)
    return state


def all_news_claims_keep(state: NewsSYState) -> bool:
    evaluations = state.get("evaluations", [])
    return bool(evaluations) and all(item.get("decision") == "keep" for item in evaluations)


def next_after_news_evaluation(state: NewsSYState) -> str:
    if all_news_claims_keep(state):
        return "finalize"
    return "rewrite"


def build_revision_brief_node(state: NewsSYState) -> NewsSYState:
    state["revision_brief"] = [
        {
            "claim_id": item.get("claim_id"),
            "section": item.get("section", ""),
            "decision": item.get("decision"),
            "issue_ko": item.get("sy_reason", ""),
            "evidence_to_preserve": _merge_unique(
                item.get("evidence_ids_used", []),
                item.get("declared_evidence_ids", []),
            ),
            "rewrite_direction_ko": item.get("revision_suggestion")
            or _default_rewrite_direction(item.get("decision")),
            "wording_constraints_ko": (
                "SY 질문/답변을 그대로 붙이지 말고, handoff와 evidence_map으로 설명 가능한 범위에서 "
                "분석 문장에 자연스럽게 반영한다."
            ),
        }
        for item in state.get("evaluations", [])
        if item.get("decision") != "keep"
    ]
    return state


def _default_rewrite_direction(decision: Any) -> str:
    if decision == "revise":
        return "근거가 설명되는 범위로 표현을 약화하고 뉴스, 재무, 시장 근거 연결을 보강한다."
    if decision == "remove":
        return "입력 handoff 또는 evidence_map과 충돌하거나 제외가 필요한 주장은 삭제한다."
    return "입력 근거로 설명하기 어려운 주장은 삭제하거나 보수적 표현으로 완전히 다시 작성한다."


def rewrite_news_handoff_node(state: NewsSYState) -> NewsSYState:
    request_payload = {
        "model": state["model"],
        "temperature": 0.2,
        "response_format": {"type": "json_object"},
        "messages": [
            {
                "role": "system",
                "content": (
                    "당신은 News Agent입니다. SY Agent 검증 결과와 revision brief를 반영해 "
                    "news_agent_handoff의 output 객체를 한 번만 다시 작성합니다. "
                    "반드시 JSON 객체 하나만 출력하고, output 키 아래에 수정된 News Agent output 객체를 넣으세요. "
                    "새로운 외부 사실, 투자 판단, buy/sell/hold, 목표주가를 추가하지 마세요. "
                    "기존 handoff, evidence_map, 두 라운드 답변, SY evaluation feedback, revision brief만 사용하세요. "
                    "SY 질문/답변이나 검증 메타데이터를 본문에 그대로 붙여넣지 말고, 문맥에 맞는 분석 문장으로 자연스럽게 녹여 쓰세요. "
                    "revise claim은 표현과 근거 연결을 보강하고, hallucination_candidate/remove claim은 삭제하거나 "
                    "근거가 있는 보수적 표현으로 완전히 다시 쓰세요."
                ),
            },
            {
                "role": "user",
                "content": json.dumps(
                    {
                        "current_output": state.get("source_output", {}),
                        "revision_brief": state.get("revision_brief", []),
                        "evidence_map": _compact_evidence_map(state.get("evidence_map", {})),
                        "questions_round_1": state.get("questions_1", []),
                        "answers_round_1": state.get("answers_1", []),
                        "questions_round_2": state.get("questions_2", []),
                        "answers_round_2": state.get("answers_2", []),
                        "sy_evaluations": state.get("evaluations", []),
                    },
                    ensure_ascii=False,
                ),
            },
        ],
    }
    parsed, usage, elapsed = _call_openai_json(request_payload, timeout_seconds=state["timeout_seconds"])
    rewritten_output = parsed.get("output") if isinstance(parsed, dict) else None
    if not isinstance(rewritten_output, dict):
        raise RuntimeError("News handoff rewrite payload must include an output object.")
    state.setdefault("llm_calls", []).append(_llm_call_record("Specialist Report Rewrite Node", request_payload, usage, elapsed))
    state.setdefault("rewrite_history", []).append(
        {
            "mode": "single_pass_contextual_rewrite_no_reverification",
            "non_keep_claim_ids": [
                item.get("claim_id")
                for item in state.get("evaluations", [])
                if item.get("decision") != "keep"
            ],
        }
    )
    state["source_output"] = rewritten_output
    state["report_rewritten"] = True
    return state


def build_outputs_node(state: NewsSYState) -> NewsSYState:
    llm_usage = _aggregate_llm_usage(state.get("llm_calls", []))
    verified = build_verified_output(
        source_output=state["source_output"],
        source_path=state["handoff_path"],
        claims=state["claims"],
        questions_1=state["questions_1"],
        answers_1=state["answers_1"],
        questions_2=state["questions_2"],
        answers_2=state["answers_2"],
        evaluations=state["evaluations"],
        model=state["model"],
        llm_usage=llm_usage,
        elapsed_seconds=time.monotonic() - state["started_at"],
    )
    verified["summary"]["report_rewritten"] = bool(state.get("report_rewritten", False))
    verified["summary"]["rewrite_policy"] = (
        "single_pass_contextual_rewrite_no_reverification"
        if state.get("report_rewritten")
        else "no_rewrite_all_claims_kept_or_final_validation_only"
    )
    verified["revision_brief"] = state.get("revision_brief", [])
    verified["rewrite_history"] = state.get("rewrite_history", [])
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
    qa_log = {
        "questions_round_1": state["questions_1"],
        "news_agent_answers_round_1": state["answers_1"],
        "questions_round_2": state["questions_2"],
        "news_agent_answers_round_2": state["answers_2"],
        "sy_evaluations": state["evaluations"],
        "revision_brief": state.get("revision_brief", []),
        "report_rewritten": bool(state.get("report_rewritten", False)),
        "rewrite_history": state.get("rewrite_history", []),
    }
    audit_trace = {
        "graph_flow": GRAPH_FLOW,
        "source_path": str(state["handoff_path"]),
        "model": state["model"],
        "llm_usage": state["verified"].get("llm_usage", {}),
        "report_rewritten": state.get("report_rewritten", False),
        "revision_brief": state.get("revision_brief", []),
        "rewrite_history": state.get("rewrite_history", []),
        "claim_extraction": {
            "news_claim_limit": state["claim_limit"],
            "total_claims": len(state["claims"]),
            "claims": state["claims"],
        },
        "llm_calls": state.get("llm_calls", []),
        "elapsed_seconds": round(time.monotonic() - state["started_at"], 3),
    }
    critic_queue = {
        "source_path": str(state["handoff_path"]),
        "claims": [
            item
            for item in state["evaluations"]
            if item.get("decision") in {"hallucination_candidate", "remove"}
        ],
    }
    _save_json(state["verified"], paths.verified_output)
    _save_json(state["verified_handoff"], paths.verified_report)
    _save_json(qa_log, paths.question_answer_log)
    _save_json(audit_trace, paths.audit_trace)
    _save_json(critic_queue, paths.critic_queue)
    return state


def build_news_sy_graph():
    graph = StateGraph(NewsSYState)
    graph.add_node("load_handoff", load_handoff_node)
    graph.add_node("extract_claims", extract_claims_node)
    graph.add_node("answer_round_1", answer_round_1_node)
    graph.add_node("answer_round_2", answer_round_2_node)
    graph.add_node("evaluate_answers", evaluate_answers_node)
    graph.add_node("build_revision_brief", build_revision_brief_node)
    graph.add_node("rewrite_news_handoff", rewrite_news_handoff_node)
    graph.add_node("build_outputs", build_outputs_node)
    graph.add_node("save_outputs", save_outputs_node)

    graph.add_edge(START, "load_handoff")
    graph.add_edge("load_handoff", "extract_claims")
    graph.add_edge("extract_claims", "answer_round_1")
    graph.add_edge("answer_round_1", "answer_round_2")
    graph.add_edge("answer_round_2", "evaluate_answers")
    graph.add_conditional_edges(
        "evaluate_answers",
        next_after_news_evaluation,
        {
            "rewrite": "build_revision_brief",
            "finalize": "build_outputs",
        },
    )
    graph.add_edge("build_revision_brief", "rewrite_news_handoff")
    graph.add_edge("rewrite_news_handoff", "build_outputs")
    graph.add_edge("build_outputs", "save_outputs")
    graph.add_edge("save_outputs", END)
    return graph.compile()


def extract_claims(source_output: dict[str, Any], *, news_claim_limit: int) -> list[dict[str, Any]]:
    blocks = source_output.get("analysis_blocks") or {}
    claims: list[dict[str, Any]] = []
    news_claims: list[dict[str, Any]] = []

    news_only = blocks.get("news_only") or {}
    _append_summary_claim(news_claims, news_only, block="news_only")
    for source_key, claim_type in [
        ("positive_signals", "positive_signal"),
        ("negative_signals", "negative_signal"),
        ("key_risks", "key_risk"),
        ("uncertainties", "uncertainty"),
    ]:
        _append_string_list_claims(news_claims, news_only, block="news_only", source_key=source_key, claim_type=claim_type)
    claims.extend(news_claims[: max(news_claim_limit, 0)])

    financial = blocks.get("news_plus_financial") or {}
    _append_summary_claim(claims, financial, block="news_plus_financial")
    _append_object_list_claims(
        claims,
        financial,
        block="news_plus_financial",
        source_key="cross_points",
        claim_type="cross_point",
    )
    _append_object_list_claims(
        claims,
        financial,
        block="news_plus_financial",
        source_key="conflicting_points",
        claim_type="conflicting_point",
    )
    _append_limit_claims(claims, financial, block="news_plus_financial", source_key="financial_context_limits")

    market = blocks.get("news_plus_market") or {}
    _append_summary_claim(claims, market, block="news_plus_market")
    _append_object_list_claims(
        claims,
        market,
        block="news_plus_market",
        source_key="reaction_points",
        claim_type="reaction_point",
    )
    _append_object_list_claims(
        claims,
        market,
        block="news_plus_market",
        source_key="divergences",
        claim_type="divergence",
    )

    integrated = blocks.get("news_plus_financial_plus_market") or {}
    _append_summary_claim(claims, integrated, block="news_plus_financial_plus_market")
    for source_key, claim_type in [
        ("integrated_signals", "integrated_signal"),
        ("integrated_risks", "integrated_risk"),
        ("handoff_notes", "handoff_note"),
    ]:
        _append_string_list_claims(
            claims,
            integrated,
            block="news_plus_financial_plus_market",
            source_key=source_key,
            claim_type=claim_type,
        )

    for idx, claim in enumerate(claims, start=1):
        claim["claim_id"] = f"NCLAIM_{idx:03d}"
    return claims


def annotate_claims_with_evidence(claims: list[dict[str, Any]], evidence_map: dict[str, Any]) -> None:
    for claim in claims:
        declared = _extract_valid_evidence_ids(claim.get("original_item"), evidence_map)
        if not declared:
            declared = _extract_valid_evidence_ids(claim.get("claim", ""), evidence_map)
        required_domains = _required_evidence_domains(claim)
        claim["declared_evidence_ids"] = declared
        claim["required_evidence_domains"] = required_domains


def _extract_valid_evidence_ids(payload: Any, evidence_map: dict[str, Any]) -> list[str]:
    text = json.dumps(payload, ensure_ascii=False, sort_keys=True) if not isinstance(payload, str) else payload
    seen: set[str] = set()
    ids: list[str] = []
    for match in EVIDENCE_ID_PATTERN.findall(text):
        if match in evidence_map and match not in seen:
            ids.append(match)
            seen.add(match)
    return ids


def _required_evidence_domains(claim: dict[str, Any]) -> list[str]:
    source_block = claim.get("source_block")
    if source_block == "news_only":
        return ["news"]
    if source_block == "news_plus_financial":
        return ["news", "financial"]
    if source_block == "news_plus_market":
        return ["news", "market"]
    if source_block == "news_plus_financial_plus_market":
        return ["news", "financial", "market"]
    return []


def _append_summary_claim(claims: list[dict[str, Any]], block_payload: dict[str, Any], *, block: str) -> None:
    summary = str(block_payload.get("summary") or "").strip()
    if not summary:
        return
    claims.append(
        _claim(
            section=f"analysis_blocks.{block}.summary",
            claim_type="summary",
            claim=summary,
            source_block=block,
            source_key="summary",
            source_index=None,
            original_item=summary,
        )
    )


def _append_string_list_claims(
    claims: list[dict[str, Any]],
    block_payload: dict[str, Any],
    *,
    block: str,
    source_key: str,
    claim_type: str,
) -> None:
    for idx, item in enumerate(block_payload.get(source_key) or [], start=1):
        text = str(item).strip()
        if not text:
            continue
        claims.append(
            _claim(
                section=f"analysis_blocks.{block}.{source_key}[{idx - 1}]",
                claim_type=claim_type,
                claim=text,
                source_block=block,
                source_key=source_key,
                source_index=idx - 1,
                original_item=item,
            )
        )


def _append_object_list_claims(
    claims: list[dict[str, Any]],
    block_payload: dict[str, Any],
    *,
    block: str,
    source_key: str,
    claim_type: str,
) -> None:
    for idx, item in enumerate(block_payload.get(source_key) or [], start=1):
        if not isinstance(item, dict):
            continue
        claim_text = str(item.get("point") or item.get("cross_analysis") or "").strip()
        if not claim_text:
            continue
        claims.append(
            _claim(
                section=f"analysis_blocks.{block}.{source_key}[{idx - 1}]",
                claim_type=claim_type,
                claim=claim_text,
                source_block=block,
                source_key=source_key,
                source_index=idx - 1,
                original_item=item,
                reasoning=item.get("cross_analysis") or "",
                interpretation=item.get("reaction_interpretation") or "",
                limitation=item.get("interpretation_limit") or "",
            )
        )


def _append_limit_claims(
    claims: list[dict[str, Any]],
    block_payload: dict[str, Any],
    *,
    block: str,
    source_key: str,
) -> None:
    for idx, item in enumerate(block_payload.get(source_key) or [], start=1):
        if isinstance(item, dict):
            claim_text = str(item.get("limit") or "").strip()
        else:
            claim_text = str(item).strip()
        if not claim_text:
            continue
        claims.append(
            _claim(
                section=f"analysis_blocks.{block}.{source_key}[{idx - 1}]",
                claim_type="context_limit",
                claim=claim_text,
                source_block=block,
                source_key=source_key,
                source_index=idx - 1,
                original_item=item,
            )
        )


def _claim(
    *,
    section: str,
    claim_type: str,
    claim: str,
    source_block: str,
    source_key: str,
    source_index: int | None,
    original_item: Any,
    reasoning: str = "",
    interpretation: str = "",
    limitation: str = "",
) -> dict[str, Any]:
    return {
        "claim_id": "",
        "section": section,
        "claim_type": claim_type,
        "claim": claim,
        "source_block": source_block,
        "source_key": source_key,
        "source_index": source_index,
        "reasoning": reasoning,
        "interpretation": interpretation,
        "limitation": limitation,
        "original_item": original_item,
    }


def build_questions(claims: list[dict[str, Any]], *, round_no: int) -> list[dict[str, Any]]:
    return [
        {
            **_question_context(claim),
            "question_round": round_no,
            "question": _question_for_claim(claim, round_no=round_no),
        }
        for claim in claims
    ]


def build_followup_questions(claims: list[dict[str, Any]], answers_1: list[dict[str, Any]]) -> list[dict[str, Any]]:
    by_id = {item.get("claim_id"): item for item in answers_1}
    questions: list[dict[str, Any]] = []
    for claim in claims:
        answer = by_id.get(claim["claim_id"], {})
        support = str(answer.get("self_assessed_support") or "")
        missing_domains = answer.get("missing_evidence_domains") or []
        invalid_ids = answer.get("invalid_evidence_ids") or []
        if missing_domains or invalid_ids:
            question = (
                "1차 답변의 evidence_used에 유효하지 않은 id가 있거나 필수 도메인 근거가 빠졌다. "
                f"누락 도메인 {missing_domains}와 invalid evidence {invalid_ids}를 기준으로, "
                "실제 evidence_map에 존재하는 evidence id만 사용해 보강하라. 보강할 수 없으면 표현을 약화하거나 삭제해야 한다고 답하라."
            )
        elif support in {"weak", "insufficient"}:
            question = (
                "1차 답변에서 근거가 약하다고 판단했다. 입력 handoff 내부 문장만 기준으로 "
                "이 주장을 유지할 수 있는지, 표현을 약화해야 하는지, 삭제해야 하는지 설명하라."
            )
        elif claim["source_block"] == "news_plus_financial":
            question = (
                "이 주장이 뉴스 이벤트와 재무 지표를 실제로 연결한 교차분석인지 다시 점검하라. "
                "재무제표 단독 해석으로 흐른 부분이 있다면 한계를 설명하라."
            )
        elif claim["source_block"] == "news_plus_market":
            question = (
                "이 주장이 뉴스 이벤트와 주가·거래량·상대성과를 실제로 연결한 교차분석인지 다시 점검하라. "
                "시장 데이터 단독 해석으로 흐른 부분이 있다면 한계를 설명하라."
            )
        else:
            question = "1차 답변을 바탕으로 이 주장이 과도한 단정인지, 유지 가능한 분석인지 다시 설명하라."
        questions.append({**_question_context(claim), "question_round": 2, "question": question})
    return questions


def _question_context(claim: dict[str, Any]) -> dict[str, Any]:
    return {
        "claim_id": claim["claim_id"],
        "section": claim["section"],
        "claim_type": claim["claim_type"],
        "claim": claim["claim"],
        "source_block": claim["source_block"],
        "source_key": claim["source_key"],
        "source_index": claim["source_index"],
        "original_item": claim["original_item"],
        "declared_evidence_ids": claim.get("declared_evidence_ids", []),
        "required_evidence_domains": claim.get("required_evidence_domains", []),
    }


def _question_for_claim(claim: dict[str, Any], *, round_no: int) -> str:
    source_block = claim["source_block"]
    claim_type = claim["claim_type"]

    if source_block == "news_only":
        if claim_type == "positive_signal":
            return (
                "이 항목을 긍정 신호로 본 이유를 설명하라. 과거 월별 요약과 최신 raw 뉴스 중 "
                "어떤 근거가 이 신호를 뒷받침하는지 구분하라."
            )
        if claim_type in {"negative_signal", "key_risk"}:
            return (
                "이 항목을 부정 신호 또는 핵심 리스크로 본 이유를 설명하라. "
                "실제 뉴스 근거와 아직 불확실한 해석을 구분하라."
            )
        if claim_type == "uncertainty":
            return (
                "이 항목을 불확실성으로 분류한 이유를 설명하라. "
                "확정된 사실과 추가 관찰이 필요한 부분을 구분하라."
            )
        return (
            "이 뉴스 요약이 전체 뉴스 흐름을 균형 있게 반영하는지 설명하라. "
            "긍정 신호, 리스크, 불확실성을 구분하라."
        )

    if source_block == "news_plus_financial":
        return (
            "이 주장에서 연결된 뉴스 이벤트와 DART 재무지표를 각각 특정하라. "
            "두 근거가 같은 방향인지, 괴리인지, 단순 병렬 나열인지 구분하고 "
            "재무제표 단독 해석이면 한계를 인정하라."
        )

    if source_block == "news_plus_market":
        return (
            "이 주장에서 연결된 뉴스 이벤트와 시장 지표를 각각 특정하라. "
            "주가 수익률, 초과수익률, 거래량, 상대강도 중 어떤 지표가 "
            "뉴스 반응을 뒷받침하거나 반박하는지 설명하라."
        )

    return (
        "이 통합 해석에서 뉴스, 재무, 시장 세 도메인의 근거를 각각 특정하라. "
        "세 도메인 중 누락된 근거가 있으면 supported가 아니라 약화 또는 보류해야 하는 이유를 설명하라."
    )


def ask_news_agent(
    *,
    source_output: dict[str, Any],
    evidence_map: dict[str, Any],
    questions: list[dict[str, Any]],
    model: str,
    timeout_seconds: float,
    round_no: int,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    prompt_evidence_map = _compact_evidence_map(
        evidence_map,
        include_ids=_collect_declared_evidence_ids(questions),
    )
    request_payload = {
        "model": model,
        "temperature": 0.2,
        "response_format": {"type": "json_object"},
        "messages": [
            {
                "role": "system",
                "content": (
                    "당신은 News Agent입니다. 자신이 작성한 news_agent_handoff에 대해 SY Agent의 질문에 답합니다. "
                    "새로운 외부 사실, 투자 판단, buy/sell/hold, 목표주가를 절대 추가하지 않습니다. "
                    "반드시 입력 handoff와 evidence_map에 있는 정보만 사용합니다. "
                    "evidence_used에는 evidence_map에 실제 존재하는 id만 넣고, handoff 내부 section path는 넣지 마세요. "
                    "필수 도메인 근거를 evidence_map에서 찾지 못하면 근거가 부족하다고 인정하세요. JSON만 출력하세요."
                ),
            },
            {
                "role": "user",
                "content": json.dumps(
                    {
                        "task": "각 질문에 답변하라.",
                        "round": round_no,
                        "output_schema": {
                            "answers": [
                                {
                                    "claim_id": "string",
                                    "answer": "string",
                                    "evidence_used": ["string"],
                                    "self_assessed_support": "strong | moderate | weak | insufficient",
                                    "limitations": "string",
                                }
                            ]
                        },
                        "news_agent_handoff_output": source_output,
                        "evidence_map": prompt_evidence_map,
                        "valid_evidence_ids": list(prompt_evidence_map.keys()),
                        "questions": questions,
                    },
                    ensure_ascii=False,
                ),
            },
        ],
    }
    parsed, usage, elapsed = _call_openai_json(request_payload, timeout_seconds=timeout_seconds)
    answers = parsed.get("answers") if isinstance(parsed, dict) else None
    if not isinstance(answers, list):
        raise RuntimeError("News Agent answer payload must be JSON with an answers array.")
    answered_ids = {
        item.get("claim_id")
        for item in answers
        if isinstance(item, dict) and item.get("claim_id")
    }
    missing_ids = sorted({item["claim_id"] for item in questions} - answered_ids)
    if missing_ids:
        raise RuntimeError(f"News Agent answer payload missing claim ids: {', '.join(missing_ids)}")
    return _normalize_answers(answers, questions, evidence_map), _llm_call_record(
        "Specialist Answer Node", request_payload, usage, elapsed
    )


def _normalize_answers(
    answers: list[dict[str, Any]],
    questions: list[dict[str, Any]],
    evidence_map: dict[str, Any],
) -> list[dict[str, Any]]:
    by_id = {
        item.get("claim_id"): item
        for item in answers
        if isinstance(item, dict) and item.get("claim_id")
    }
    normalized: list[dict[str, Any]] = []
    for question in questions:
        answer = by_id.get(question["claim_id"]) or {}
        raw_evidence = answer.get("evidence_used") or []
        if not isinstance(raw_evidence, list):
            raw_evidence = [str(raw_evidence)]
        valid_evidence: list[str] = []
        invalid_evidence: list[str] = []
        seen: set[str] = set()
        for evidence_id in raw_evidence:
            evidence_id = str(evidence_id).strip()
            if evidence_id in evidence_map:
                if evidence_id not in seen:
                    valid_evidence.append(evidence_id)
                    seen.add(evidence_id)
            elif evidence_id:
                invalid_evidence.append(evidence_id)
        domains = sorted(
            {
                str(evidence_map[evidence_id].get("source_domain"))
                for evidence_id in valid_evidence
                if evidence_map.get(evidence_id, {}).get("source_domain")
            }
        )
        required = question.get("required_evidence_domains") or []
        missing = [domain for domain in required if domain not in domains]
        normalized.append(
            {
                "claim_id": question["claim_id"],
                "answer": answer.get("answer", ""),
                "evidence_used": valid_evidence,
                "raw_evidence_used": raw_evidence,
                "invalid_evidence_ids": invalid_evidence,
                "evidence_domain_coverage": domains,
                "required_evidence_domains": required,
                "missing_evidence_domains": missing,
                "self_assessed_support": answer.get("self_assessed_support", "insufficient"),
                "limitations": answer.get("limitations", ""),
            }
        )
    return normalized


def evaluate_answers(
    *,
    claims: list[dict[str, Any]],
    answers_1: list[dict[str, Any]],
    answers_2: list[dict[str, Any]],
    model: str,
    timeout_seconds: float,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    by_answer_1 = {item.get("claim_id"): item for item in answers_1}
    by_answer_2 = {item.get("claim_id"): item for item in answers_2}
    evaluation_inputs = [
        {
            "claim": claim,
            "answer_round_1": by_answer_1.get(claim["claim_id"], {}),
            "answer_round_2": by_answer_2.get(claim["claim_id"], {}),
        }
        for claim in claims
    ]
    request_payload = {
        "model": model,
        "temperature": 0.0,
        "response_format": {"type": "json_object"},
        "messages": [
            {
                "role": "system",
                "content": (
                    "당신은 SY Agent입니다. News Agent의 답변이 왜 이런 판단을 했는지 제대로 설명하는지 평가합니다. "
                    "판정값은 keep, revise, hallucination_candidate, remove 중 하나만 사용합니다. "
                    "지원 수준은 supported, weakly_supported, unsupported, contradicted 중 하나만 사용합니다. "
                    "evidence_used의 실제 evidence_map id 여부, invalid_evidence_ids, missing_evidence_domains를 종합해 LLM이 최종 판단합니다. "
                    "교차분석은 뉴스, 재무, 시장 도메인 근거가 어떻게 연결되는지 직접 평가합니다. "
                    "buy/sell/hold 또는 목표주가를 생성하지 않습니다. JSON만 출력하세요."
                ),
            },
            {
                "role": "user",
                "content": json.dumps(
                    {
                        "decision_policy": {
                            "supported": "keep",
                            "weakly_supported": "revise",
                            "unsupported": "hallucination_candidate",
                            "contradicted": "remove",
                        },
                        "strict_evidence_policy": {
                            "valid_evidence_only": "evidence_used must be actual ids from evidence_map, not section paths.",
                            "missing_required_domain": "Consider whether missing required_evidence_domains weakens or invalidates the claim.",
                            "no_valid_evidence": "Consider whether no valid evidence ids makes the claim unsupported or contradicted.",
                            "three_domain_integrated_block": "Evaluate whether news_plus_financial_plus_market actually connects news, financial, and market evidence.",
                        },
                        "output_schema": {
                            "evaluations": [
                                {
                                    "claim_id": "string",
                                    "section": "string",
                                    "claim": "string",
                                    "required_evidence_domains": ["string"],
                                    "evidence_ids_used": ["string"],
                                    "evidence_domain_coverage": ["string"],
                                    "missing_evidence_domains": ["string"],
                                    "invalid_evidence_ids": ["string"],
                                    "support_level": "supported | weakly_supported | unsupported | contradicted",
                                    "decision": "keep | revise | hallucination_candidate | remove",
                                    "sy_reason": "string",
                                    "revision_suggestion": "string",
                                }
                            ]
                        },
                        "evaluation_inputs": evaluation_inputs,
                    },
                    ensure_ascii=False,
                ),
            },
        ],
    }
    parsed, usage, elapsed = _call_openai_json(request_payload, timeout_seconds=timeout_seconds)
    evaluations = parsed.get("evaluations") if isinstance(parsed, dict) else None
    if not isinstance(evaluations, list):
        raise RuntimeError("News SY evaluation payload must be JSON with an evaluations array.")
    evaluations_by_id = {
        evaluation.get("claim_id"): evaluation
        for evaluation in evaluations
        if isinstance(evaluation, dict)
    }
    missing_ids = sorted({claim["claim_id"] for claim in claims} - set(evaluations_by_id))
    if missing_ids:
        raise RuntimeError(f"News SY evaluation payload missing claim ids: {', '.join(missing_ids)}")
    support_by_decision = {value: key for key, value in SUPPORT_LEVEL_TO_DECISION.items()}
    normalized = []
    for claim in claims:
        evaluation = evaluations_by_id.get(claim["claim_id"]) or {}
        answer_1 = by_answer_1.get(claim["claim_id"], {})
        answer_2 = by_answer_2.get(claim["claim_id"], {})
        support_level = evaluation.get("support_level")
        decision = evaluation.get("decision")
        if support_level not in SUPPORT_LEVEL_TO_DECISION:
            support_level = support_by_decision.get(decision)
        if support_level not in SUPPORT_LEVEL_TO_DECISION:
            raise RuntimeError(f"Invalid News SY support_level for {claim['claim_id']}: {support_level}")
        if decision not in set(SUPPORT_LEVEL_TO_DECISION.values()):
            raise RuntimeError(f"Invalid News SY decision for {claim['claim_id']}: {decision}")
        normalized.append(
            {
                **evaluation,
                "claim_id": claim["claim_id"],
                "section": evaluation.get("section") or claim["section"],
                "claim": evaluation.get("claim") or claim["claim"],
                "required_evidence_domains": claim.get("required_evidence_domains", []),
                "declared_evidence_ids": claim.get("declared_evidence_ids", []),
                "evidence_ids_used": _merge_unique(
                    answer_1.get("evidence_used", []),
                    answer_2.get("evidence_used", []),
                ),
                "invalid_evidence_ids": _merge_unique(
                    answer_1.get("invalid_evidence_ids", []),
                    answer_2.get("invalid_evidence_ids", []),
                ),
                "evidence_domain_coverage": _merge_unique(
                    answer_1.get("evidence_domain_coverage", []),
                    answer_2.get("evidence_domain_coverage", []),
                ),
                "missing_evidence_domains": _missing_domains_after_two_rounds(claim, answer_1, answer_2),
                "question_round_1": next(
                    (item.get("question", "") for item in build_questions([claim], round_no=1)),
                    "",
                ),
                "answer_round_1_summary": _truncate_text(answer_1.get("answer", ""), limit=300),
                "answer_round_2_summary": _truncate_text(answer_2.get("answer", ""), limit=300),
                "support_level": support_level,
                "decision": decision,
                "sy_reason": evaluation.get("sy_reason")
                or "SY Agent 평가가 누락되어 unsupported로 처리했다.",
                "revision_suggestion": evaluation.get("revision_suggestion")
                or _default_revision_suggestion(decision),
            }
        )
    return normalized, _llm_call_record("SY Deletion / Revision Decision Node", request_payload, usage, elapsed)


def _default_revision_suggestion(decision: Any) -> str:
    if decision == "keep":
        return "수정 불필요"
    if decision == "revise":
        return "근거가 설명되는 범위로 표현을 약화하거나 보강 필요"
    if decision == "remove":
        return "입력 근거와 충돌하거나 근거가 없으면 삭제 필요"
    return "재검증 또는 보수적 표현 수정 필요"


def _missing_domains_after_two_rounds(
    claim: dict[str, Any],
    answer_1: dict[str, Any],
    answer_2: dict[str, Any],
) -> list[str]:
    coverage = set(_merge_unique(answer_1.get("evidence_domain_coverage", []), answer_2.get("evidence_domain_coverage", [])))
    return [domain for domain in claim.get("required_evidence_domains", []) if domain not in coverage]


def _merge_unique(*values: list[Any]) -> list[Any]:
    merged: list[Any] = []
    seen: set[str] = set()
    for value_list in values:
        if not isinstance(value_list, list):
            continue
        for value in value_list:
            key = str(value)
            if key not in seen:
                merged.append(value)
                seen.add(key)
    return merged


def _truncate_text(text: Any, *, limit: int) -> str:
    normalized = re.sub(r"\s+", " ", str(text or "")).strip()
    return normalized if len(normalized) <= limit else normalized[: limit - 3] + "..."


def build_verified_output(
    *,
    source_output: dict[str, Any],
    source_path: Path,
    claims: list[dict[str, Any]],
    questions_1: list[dict[str, Any]],
    answers_1: list[dict[str, Any]],
    questions_2: list[dict[str, Any]],
    answers_2: list[dict[str, Any]],
    evaluations: list[dict[str, Any]],
    model: str,
    llm_usage: dict[str, Any],
    elapsed_seconds: float,
) -> dict[str, Any]:
    decision_counts = {
        "keep": sum(1 for item in evaluations if item.get("decision") == "keep"),
        "revise": sum(1 for item in evaluations if item.get("decision") == "revise"),
        "hallucination_candidate": sum(
            1 for item in evaluations if item.get("decision") == "hallucination_candidate"
        ),
        "remove": sum(1 for item in evaluations if item.get("decision") == "remove"),
    }
    return {
        "agent_name": "SY Agent",
        "agent_role": "News Agent Output Questioner",
        "output_version": "1.0",
        "output_mode": "news_agent_output_validation",
        "source_agent": {
            "agent_name": source_output.get("agent_name"),
            "output_version": source_output.get("output_version"),
            "output_path": str(source_path),
        },
        "target_entity": source_output.get("target_entity") or {},
        "graph_flow": GRAPH_FLOW,
        "verification_mode": "question_answer_based_evidence_verification",
        "verification_policy": {
            "decision_values": ["keep", "revise", "hallucination_candidate", "remove"],
            "buy_sell_hold_allowed": False,
            "external_fact_addition_allowed": False,
            "original_handoff_mutation_allowed": True,
            "output_scope": "claim_validations_and_optional_single_pass_contextual_rewrite",
            "core_question": "왜 이런 판단을 했는가?",
        },
        "model": model,
        "llm_usage": llm_usage,
        "summary": {
            "total_claims": len(evaluations),
            "verified_count": decision_counts["keep"],
            "revised_count": decision_counts["revise"],
            "hallucination_candidate_count": decision_counts["hallucination_candidate"],
            "removed_count": decision_counts["remove"],
            "decision_counts": decision_counts,
        },
        "claim_validations": evaluations,
        "elapsed_seconds": round(elapsed_seconds, 3),
    }


def build_verified_handoff_document(
    *,
    handoff_document: dict[str, Any],
    source_output: dict[str, Any],
    validation_output: dict[str, Any],
    validation_path: Path,
) -> dict[str, Any]:
    """Return a Strategy-compatible News handoff annotated with SY validation."""

    verified_handoff = copy.deepcopy(handoff_document)
    if not isinstance(verified_handoff, dict):
        verified_handoff = {"output": copy.deepcopy(source_output)}

    output = copy.deepcopy(source_output)
    verified_handoff["output"] = output

    summary = copy.deepcopy(validation_output.get("summary") or {})
    attention_items = _compact_news_validation_items(validation_output)
    sy_validation = {
        "verifier_agent": validation_output.get("agent_name", "SY Agent"),
        "verification_mode": validation_output.get("verification_mode"),
        "verification_policy": copy.deepcopy(validation_output.get("verification_policy") or {}),
        "validation_report_path": str(validation_path),
        "summary": summary,
        "attention_items": attention_items,
        "revision_brief": copy.deepcopy(validation_output.get("revision_brief") or []),
    }

    status = (
        "sy_revision_reflected_no_reverification"
        if summary.get("report_rewritten")
        else "sy_validation_reflected"
    )
    verified_handoff["report_status"] = status
    verified_handoff["verification_summary"] = summary
    verified_handoff["verification_report_path"] = str(validation_path)
    output["sy_validation"] = sy_validation
    output["report_status"] = status

    _append_news_sy_strategy_notes(output, summary, attention_items)
    return verified_handoff


def _compact_news_validation_items(validation_output: dict[str, Any], *, limit: int = 10) -> list[dict[str, Any]]:
    validations = validation_output.get("claim_validations") or []
    if not isinstance(validations, list):
        return []
    decision_priority = {
        "remove": 0,
        "hallucination_candidate": 1,
        "revise": 2,
        "keep": 3,
    }
    prioritized = sorted(
        [item for item in validations if isinstance(item, dict)],
        key=lambda item: decision_priority.get(str(item.get("decision")), 9),
    )
    attention_items = []
    for item in prioritized:
        if item.get("decision") == "keep":
            continue
        attention_items.append(
            {
                "claim_id": item.get("claim_id"),
                "section": item.get("section"),
                "decision": item.get("decision"),
                "support_level": item.get("support_level"),
                "claim": _truncate_text(item.get("claim"), limit=240),
                "sy_reason": _truncate_text(item.get("sy_reason"), limit=240),
                "revision_suggestion": _truncate_text(item.get("revision_suggestion"), limit=240),
            }
        )
        if len(attention_items) >= limit:
            break
    return attention_items


def _append_news_sy_strategy_notes(
    output: dict[str, Any],
    summary: dict[str, Any],
    attention_items: list[dict[str, Any]],
) -> None:
    blocks = output.setdefault("analysis_blocks", {})
    if not isinstance(blocks, dict):
        return
    integrated = blocks.setdefault("news_plus_financial_plus_market", {})
    if not isinstance(integrated, dict):
        return
    notes = integrated.setdefault("strategy_handoff_notes", [])
    if not isinstance(notes, list):
        notes = [str(notes)] if notes else []
        integrated["strategy_handoff_notes"] = notes

    total = int(summary.get("total_claims") or 0)
    revised = int(summary.get("revised_count") or 0)
    hallucination = int(summary.get("hallucination_candidate_count") or 0)
    removed = int(summary.get("removed_count") or 0)
    if total and (revised or hallucination or removed):
        notes.append(
            "SY 검증 결과 News Agent 핵심 주장 "
            f"{total}건 중 revise {revised}건, hallucination_candidate {hallucination}건, "
            f"remove {removed}건으로 분류되어 뉴스 기반 강한 주장의 신뢰도는 제한적이다."
        )
    for item in attention_items[:5]:
        decision = item.get("decision")
        claim = item.get("claim")
        reason = item.get("sy_reason")
        if decision and claim:
            notes.append(f"SY {decision}: {claim} ({reason})")


def _call_openai_json(request_payload: dict[str, Any], *, timeout_seconds: float) -> tuple[dict[str, Any], Any, float]:
    api_key = os.getenv("OPENAI_API_KEY", "").strip()
    if not api_key:
        raise RuntimeError("OPENAI_API_KEY is required in News/.env or --env-path.")
    client = OpenAI(api_key=api_key, timeout=timeout_seconds)
    started_at = time.monotonic()
    response = client.chat.completions.create(**request_payload)
    elapsed = time.monotonic() - started_at
    content = response.choices[0].message.content or ""
    parsed = _extract_json(content)
    usage_obj = response.usage
    usage = usage_obj.model_dump() if hasattr(usage_obj, "model_dump") else usage_obj
    return parsed, usage, elapsed


def _llm_call_record(node: str, request_payload: dict[str, Any], usage: Any, elapsed: float) -> dict[str, Any]:
    return {
        "node": node,
        "model": request_payload.get("model"),
        "elapsed_seconds": round(elapsed, 3),
        "usage": usage,
    }


def _aggregate_llm_usage(llm_calls: list[dict[str, Any]]) -> dict[str, Any]:
    totals = {
        "completion_tokens": 0,
        "prompt_tokens": 0,
        "total_tokens": 0,
    }
    call_summaries: list[dict[str, Any]] = []
    for call in llm_calls:
        usage = call.get("usage") or {}
        call_summary = {
            "node": call.get("node"),
            "model": call.get("model"),
            "completion_tokens": int(usage.get("completion_tokens") or 0),
            "prompt_tokens": int(usage.get("prompt_tokens") or 0),
            "total_tokens": int(usage.get("total_tokens") or 0),
        }
        for key in totals:
            totals[key] += call_summary[key]
        call_summaries.append(call_summary)
    return {
        **totals,
        "calls": call_summaries,
    }


def _collect_declared_evidence_ids(questions: list[dict[str, Any]]) -> list[str]:
    seen: set[str] = set()
    collected: list[str] = []
    for question in questions:
        for evidence_id in question.get("declared_evidence_ids") or []:
            if evidence_id not in seen:
                collected.append(evidence_id)
                seen.add(evidence_id)
    return collected


def _compact_evidence_map(
    evidence_map: dict[str, Any],
    *,
    include_ids: list[str] | None = None,
    limit: int = 80,
) -> dict[str, Any]:
    items = list(evidence_map.items())
    selected: list[tuple[str, Any]] = []
    seen: set[str] = set()
    for evidence_id in include_ids or []:
        if evidence_id in evidence_map and evidence_id not in seen:
            selected.append((evidence_id, evidence_map[evidence_id]))
            seen.add(evidence_id)
    preferred = [
        (key, value)
        for key, value in items
        if key not in seen
        if value.get("source_domain") in {"financial", "market"}
        or value.get("relation_type") in {"direct_company", "partner_or_product", "market_context"}
    ]
    selected.extend(preferred)
    seen.update(key for key, _ in preferred)
    if len(selected) < limit:
        selected.extend((key, value) for key, value in items if key not in seen)
    return dict(selected[:limit])


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
    try:
        payload = json.loads(text)
        return payload if isinstance(payload, dict) else {}
    except json.JSONDecodeError:
        pass
    start = text.find("{")
    end = text.rfind("}")
    if start != -1 and end != -1 and end > start:
        try:
            payload = json.loads(text[start : end + 1])
            return payload if isinstance(payload, dict) else {}
        except json.JSONDecodeError:
            return {}
    return {}


def _load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _save_json(payload: Any, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


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
