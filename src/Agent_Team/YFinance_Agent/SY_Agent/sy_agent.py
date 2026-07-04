import argparse
import copy
import json
import os
from pathlib import Path
from typing import Dict, Any, List, TypedDict

from dotenv import load_dotenv
from langgraph.graph import StateGraph, END

try:
    from langchain_openai import ChatOpenAI
except ModuleNotFoundError:
    class _OpenAIResponse:
        def __init__(self, content: str) -> None:
            self.content = content

    class ChatOpenAI:
        def __init__(self, model: str, temperature: float = 0) -> None:
            from openai import OpenAI

            self.model = model
            self.temperature = temperature
            self.client = OpenAI()

        def invoke(self, messages: list[dict[str, str]]) -> _OpenAIResponse:
            response = self.client.responses.create(
                model=self.model,
                input=messages,
                temperature=self.temperature,
            )
            return _OpenAIResponse(response.output_text)


PROJECT_ROOT = Path(__file__).resolve().parents[4]
OUTPUT_DIR = PROJECT_ROOT / "Output_total" / "Y_Finance"
DEFAULT_ENV_PATH = PROJECT_ROOT / "configs" / ".env"
if not DEFAULT_ENV_PATH.exists():
    DEFAULT_ENV_PATH = PROJECT_ROOT / ".env"
if not DEFAULT_ENV_PATH.exists():
    DEFAULT_ENV_PATH = Path("/home/agent2/SY/.env")
DEFAULT_OPENAI_MODEL = "gpt-5.4-mini"

BASE_DIR = str(OUTPUT_DIR)
INPUT_PATH = str(OUTPUT_DIR / "yfinance_analyst_report.json")
OUTPUT_PATH = str(OUTPUT_DIR / "sy_verified_yfinance_report.json")
QUESTION_LOG_PATH = str(OUTPUT_DIR / "sy_question_answer_log.json")
STRATEGY_REPORT_FILENAME = "yfinance_verified_report.json"


class SYState(TypedDict):
    input_path: str
    output_path: str
    question_log_path: str
    base_dir: str
    env_file: str
    llm_model: str
    strategy_report_path: str
    report_rewritten: bool
    revision_brief: List[Dict[str, Any]]
    rewrite_history: List[Dict[str, Any]]
    report: Dict[str, Any]
    claims: List[Dict[str, Any]]
    questions: List[Dict[str, Any]]
    questions_round_1: List[Dict[str, Any]]
    questions_round_2: List[Dict[str, Any]]
    yfinance_answers: List[Dict[str, Any]]
    yfinance_answers_round_1: List[Dict[str, Any]]
    yfinance_answers_round_2: List[Dict[str, Any]]
    evaluations: List[Dict[str, Any]]
    verified_report: Dict[str, Any]
    strategy_report: Dict[str, Any]


def load_json(path: str) -> Dict[str, Any]:
    if not os.path.exists(path):
        raise FileNotFoundError(f"파일을 찾을 수 없습니다: {path}")

    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def save_json(data: Any, path: str) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)

    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def extract_json(text: str) -> Dict[str, Any]:
    text = text.strip()

    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass

    start = text.find("{")
    end = text.rfind("}")

    if start != -1 and end != -1 and end > start:
        try:
            return json.loads(text[start:end + 1])
        except json.JSONDecodeError:
            pass

    return {}


def _merge_unique(*values: List[Any]) -> List[Any]:
    merged: List[Any] = []
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


def make_llm(env_file: str | None = None, model: str | None = None) -> ChatOpenAI:
    load_dotenv(Path(env_file).expanduser() if env_file else DEFAULT_ENV_PATH)

    api_key = os.getenv("OPENAI_API_KEY")
    if not api_key:
        raise ValueError("OPENAI_API_KEY가 설정되지 않았습니다.")

    return ChatOpenAI(
        model=model or os.getenv("OPENAI_MODEL", DEFAULT_OPENAI_MODEL),
        temperature=0
    )


def load_report_node(state: SYState) -> SYState:
    state["report"] = load_json(state.get("input_path") or INPUT_PATH)
    return state


def extract_claims_node(state: SYState) -> SYState:
    report = state["report"]
    claims = []

    main_view = report.get("main_view", {})
    if main_view.get("direction"):
        claims.append({
            "claim_id": "main_direction",
            "section": "main_view.direction",
            "claim": main_view["direction"],
            "reasoning": main_view.get("summary", ""),
            "related_data": main_view.get("primary_basis", [])
        })

    time_view = report.get("time_horizon_view", {})
    for horizon in ["short_term", "mid_term", "long_term"]:
        block = time_view.get(horizon, {})
        if block.get("stance"):
            claims.append({
                "claim_id": f"{horizon}_stance",
                "section": f"time_horizon_view.{horizon}",
                "claim": block.get("stance"),
                "reasoning": block.get("reasoning", ""),
                "related_data": block.get("key_features", [])
            })

    detailed = report.get("detailed_analysis", {})
    for section_name, block in detailed.items():
        if block.get("interpretation"):
            claims.append({
                "claim_id": f"detailed_{section_name}",
                "section": f"detailed_analysis.{section_name}",
                "claim": block.get("interpretation"),
                "reasoning": block.get("interpretation"),
                "related_data": block.get("supporting_features", {}),
                "caution": block.get("caution", "")
            })

    cross = report.get("cross_data_reconciliation", {})
    for section_name in ["news_plus_market", "dart_plus_market", "news_plus_dart_plus_market"]:
        block = cross.get(section_name, {})
        if block.get("summary"):
            claims.append({
                "claim_id": f"cross_{section_name}",
                "section": f"cross_data_reconciliation.{section_name}",
                "claim": block.get("summary", ""),
                "reasoning": block.get("summary", ""),
                "related_data": {
                    "reaction_points": block.get("reaction_points", []),
                    "divergences": block.get("divergences", [])
                }
            })

        for idx, item in enumerate(block.get("reaction_points", []), start=1):
            claims.append({
                "claim_id": f"cross_{section_name}_reaction_{idx}",
                "section": f"cross_data_reconciliation.{section_name}.reaction_points",
                "claim": item.get("point", ""),
                "reasoning": item.get("cross_analysis", ""),
                "related_data": item
            })

        for idx, item in enumerate(block.get("divergences", []), start=1):
            claims.append({
                "claim_id": f"cross_{section_name}_divergence_{idx}",
                "section": f"cross_data_reconciliation.{section_name}.divergences",
                "claim": item.get("point", ""),
                "reasoning": item.get("cross_analysis", ""),
                "related_data": item
            })

    state["claims"] = claims
    return state


def generate_questions_node(state: SYState) -> SYState:
    questions = []

    for claim in state["claims"]:
        section = claim["section"]

        if "price_trend" in section:
            question = (
                "왜 가격 추세를 긍정적으로 해석했는가? "
                "stock_close_to_ma20, stock_close_to_ma60, stock_ma5_to_ma20 값을 연결해서 설명하라."
            )
        elif "momentum" in section:
            question = (
                "왜 모멘텀이 강하다고 판단했는가? "
                "RSI, MACD-Hist, MACD-Hist 변화값을 근거로 설명하라. "
                "특히 MACD-Hist 변화가 음수인데도 상승 판단이 가능한 이유를 설명하라."
            )
        elif "volatility_and_volume" in section:
            question = (
                "왜 거래량과 변동성 확대가 주가 상승을 뒷받침한다고 판단했는가? "
                "stock_bb_width_20, stock_volatility_20, stock_volume_ratio_20, stock_obv_trend 값을 근거로 설명하라."
            )
        elif "market_relative" in section:
            question = (
                "왜 단기 상승 판단과 시장대비 약세 판단이 동시에 가능한가? "
                "stock_excess_return_5d, stock_excess_return_20d, stock_relative_strength_60 값을 기준으로 설명하라."
            )
        elif "fx_context" in section:
            question = (
                "왜 환율 영향을 제한적으로 보았는가? "
                "fx_return_20d, fx_close_to_ma20, fx_rsi_14, fx_volatility_20 값을 기준으로 설명하라."
            )
        elif "news_plus_market" in section:
            question = (
                "뉴스와 시장 데이터의 연결 또는 괴리를 왜 그렇게 판단했는가? "
                "뉴스 내용과 실제 가격·거래량·수익률 지표를 연결해서 설명하라."
            )
        elif "news_plus_dart_plus_market" in section:
            question = (
                "뉴스, DART, 시장 데이터를 모두 함께 볼 때 이 교차분석이 왜 타당한가? "
                "뉴스 모멘텀, 재무지표, 주가·거래량·상대성과를 모두 연결해서 설명하라."
            )
        elif "dart_plus_market" in section:
            question = (
                "DART 실적 요약과 시장 데이터의 연결 또는 괴리를 왜 그렇게 판단했는가? "
                "매출 성장, 이익률, EPS 정보가 가격·상대성과 해석과 어떻게 연결되는지 설명하라."
            )
        elif "short_term" in section:
            question = (
                "왜 단기 기술적 강세라고 판단했는가? "
                "20일 수익률, RSI, MACD-Hist, 거래량지수를 근거로 설명하라."
            )
        elif "mid_term" in section:
            question = (
                "왜 중기 회복세라고 판단했는가? "
                "60일 수익률, OBV, 거래량 증가, 시장대비 상대강도를 함께 고려해 설명하라."
            )
        elif "long_term" in section:
            question = (
                "왜 장기적으로 회복 구간이지만 시장대비 약세라고 판단했는가? "
                "연간 누적수익률과 KOSPI 수익률 차이를 기준으로 설명하라."
            )
        else:
            question = (
                "왜 이 주장을 했는가? "
                "보고서 내부 수치 또는 명시된 요약 근거만 사용해서 설명하라."
            )

        questions.append({
            "claim_id": claim["claim_id"],
            "section": claim["section"],
            "claim": claim["claim"],
            "question": question,
            "related_data": claim.get("related_data", {}),
            "original_reasoning": claim.get("reasoning", ""),
            "caution": claim.get("caution", "")
        })

    state["questions_round_1"] = questions
    state["questions"] = questions
    return state


def ask_yfinance_agent_batch(state: SYState, questions: List[Dict[str, Any]], *, round_no: int) -> List[Dict[str, Any]]:
    llm = make_llm(state.get("env_file"), state.get("llm_model"))
    report = state["report"]

    system_prompt = """
당신은 YFinance Agent입니다.

역할:
- 당신은 기존에 작성한 YFinance 분석 보고서에 대해 SY Agent의 질문에 답합니다.
- 새로운 투자 의견, buy/sell/hold 판단은 절대 제시하지 않습니다.
- 외부 사실을 새로 추가하지 않습니다.
- 반드시 제공된 보고서 내부 수치와 문장만 사용해 답변합니다.
- 근거가 부족하면 억지로 설명하지 말고 "보고서 내부 근거만으로는 부족하다"고 말합니다.

반드시 JSON만 출력하세요.

출력 형식:
{
  "answers": [
    {
      "claim_id": "...",
      "answer": "...",
      "evidence_used": ["..."],
      "self_assessed_support": "strong | moderate | weak | insufficient",
      "limitations": "..."
    }
  ]
}
"""

    user_prompt = f"""
다음은 당신이 작성한 YFinance 보고서 전체입니다.

[전체 보고서]
{json.dumps(report, ensure_ascii=False, indent=2)}

SY Agent가 아래 주장들에 대해 {round_no}차 질문을 했습니다.

[questions]
{json.dumps(questions, ensure_ascii=False, indent=2)}

보고서 내부 근거만 사용해서 답변하세요.
"""

    response = llm.invoke([
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_prompt}
    ])

    parsed = extract_json(response.content)
    answers_payload = parsed.get("answers") if isinstance(parsed, dict) else None
    if not isinstance(answers_payload, list):
        raise RuntimeError("YFinance Agent answer payload must be JSON with an answers array.")

    questions_by_id = {question["claim_id"]: question for question in questions}
    answers = []
    answered_ids = set()
    for item in answers_payload:
        if not isinstance(item, dict):
            continue
        claim_id = item.get("claim_id")
        if claim_id not in questions_by_id:
            continue
        question = questions_by_id[claim_id]
        normalized = {
            "claim_id": claim_id,
            "answer": str(item.get("answer") or ""),
            "evidence_used": item.get("evidence_used") if isinstance(item.get("evidence_used"), list) else [],
            "self_assessed_support": str(item.get("self_assessed_support") or "insufficient"),
            "limitations": str(item.get("limitations") or ""),
            "section": question["section"],
            "claim": question["claim"],
            "question": question["question"],
        }
        answers.append(normalized)
        answered_ids.add(claim_id)
    missing_ids = sorted(set(questions_by_id) - answered_ids)
    if missing_ids:
        raise RuntimeError(f"YFinance Agent answer payload missing claim ids: {', '.join(missing_ids)}")

    return answers


def ask_yfinance_agent_node(state: SYState) -> SYState:
    answers = ask_yfinance_agent_batch(state, state["questions_round_1"], round_no=1)
    state["yfinance_answers_round_1"] = answers
    state["yfinance_answers"] = answers
    return state


def generate_followup_questions_node(state: SYState) -> SYState:
    answers_by_id = {item.get("claim_id"): item for item in state["yfinance_answers_round_1"]}
    questions = []
    for question in state["questions_round_1"]:
        answer = answers_by_id.get(question["claim_id"], {})
        support = str(answer.get("self_assessed_support") or "").lower()
        limitations = str(answer.get("limitations") or "").strip()
        if support in {"weak", "insufficient"} or limitations:
            followup = (
                "1차 답변에서 근거 한계가 드러났다. 보고서 내부 수치와 문장만 기준으로 "
                "이 claim을 유지할 수 있는지, revise가 필요한지, hallucination_candidate 또는 remove가 맞는지 다시 설명하라."
            )
        else:
            followup = (
                "1차 답변을 double check하라. 사용한 근거가 실제 보고서 내부 수치와 문장에 있는지, "
                "claim 표현이 과도하지 않은지, 반대되는 내부 근거가 있는지 확인하라."
            )
        questions.append(
            {
                **question,
                "question_round": 2,
                "question": followup,
                "answer_round_1_summary": answer.get("answer", ""),
                "answer_round_1_evidence_used": answer.get("evidence_used", []),
                "answer_round_1_limitations": limitations,
            }
        )
    state["questions_round_2"] = questions
    return state


def ask_yfinance_agent_round_2_node(state: SYState) -> SYState:
    answers = ask_yfinance_agent_batch(state, state["questions_round_2"], round_no=2)
    state["yfinance_answers_round_2"] = answers
    state["yfinance_answers"] = answers
    return state


def evaluate_answers_node(state: SYState) -> SYState:
    llm = make_llm(state.get("env_file"), state.get("llm_model"))

    system_prompt = """
당신은 SY Agent입니다.

역할:
- YFinance Agent가 자신의 보고서 주장에 대해 답변한 내용을 검증합니다.
- buy/sell/hold 의견은 절대 제시하지 않습니다.
- 새로운 투자 의견을 만들지 않습니다.
- 보고서 내부 데이터, 원래 주장, YFinance 답변만 기준으로 평가합니다.
- 핵심 판단 기준은 "왜 이런 판단을 했는가에 대해 제대로 답했는가?"입니다.

평가 기준:
1. supported:
   답변이 보고서 내부 수치와 논리로 충분히 설명됨.
2. weakly_supported:
   일부 근거는 있으나 표현이 과하거나 설명이 약함.
3. unsupported:
   보고서 내부 근거만으로는 설명 부족. hallucination_candidate.
4. contradicted:
   보고서 내부 수치와 명백히 충돌. 삭제 대상.

decision 기준:
- supported -> keep
- weakly_supported -> revise
- unsupported -> hallucination_candidate
- contradicted -> remove

반드시 JSON만 출력하세요.

출력 형식:
{
  "evaluations": [
    {
      "claim_id": "...",
      "section": "...",
      "claim": "...",
      "question": "...",
      "yfinance_answer": "...",
      "evidence_used": ["..."],
      "support_level": "supported | weakly_supported | unsupported | contradicted",
      "decision": "keep | revise | hallucination_candidate | remove",
      "sy_reason": "...",
      "revision_suggestion": "..."
    }
  ]
}
"""

    by_answer_1 = {item["claim_id"]: item for item in state["yfinance_answers_round_1"]}
    by_answer_2 = {item["claim_id"]: item for item in state["yfinance_answers_round_2"]}
    evaluation_inputs = []
    for question in state["questions_round_1"]:
        claim_id = question["claim_id"]
        evaluation_inputs.append(
            {
                "claim_id": claim_id,
                "section": question.get("section"),
                "claim": question.get("claim"),
                "question_round_1": question.get("question"),
                "answer_round_1": by_answer_1.get(claim_id, {}),
                "question_round_2": next(
                    (item.get("question") for item in state["questions_round_2"] if item.get("claim_id") == claim_id),
                    "",
                ),
                "answer_round_2": by_answer_2.get(claim_id, {}),
            }
        )

    user_prompt = f"""
다음 YFinance Agent의 2라운드 답변들을 SY Agent 관점에서 평가하라.

[evaluation_inputs]
{json.dumps(evaluation_inputs, ensure_ascii=False, indent=2)}

각 claim에 대해 1차 답변과 2차 답변을 함께 보고 "왜 이런 판단을 했는가"에 제대로 답했는지 평가하라.
"""

    response = llm.invoke([
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_prompt}
    ])

    parsed = extract_json(response.content)
    evaluations_payload = parsed.get("evaluations") if isinstance(parsed, dict) else None
    if not isinstance(evaluations_payload, list):
        raise RuntimeError("YFinance SY evaluation payload must be JSON with an evaluations array.")

    answers_by_id = {answer["claim_id"]: answer for answer in state["yfinance_answers_round_2"]}
    evaluations = []
    evaluated_ids = set()
    for item in evaluations_payload:
        if not isinstance(item, dict):
            continue
        claim_id = item.get("claim_id")
        if claim_id not in answers_by_id:
            continue
        support_level = item.get("support_level")
        decision = item.get("decision")
        if support_level not in {"supported", "weakly_supported", "unsupported", "contradicted"}:
            raise RuntimeError(f"Invalid YFinance SY support_level for {claim_id}: {support_level}")
        if decision not in {"keep", "revise", "hallucination_candidate", "remove"}:
            raise RuntimeError(f"Invalid YFinance SY decision for {claim_id}: {decision}")
        answer = answers_by_id[claim_id]
        answer_round_1 = by_answer_1.get(claim_id, {})
        evaluations.append(
            {
                **item,
                "claim_id": claim_id,
                "section": item.get("section") or answer.get("section"),
                "claim": item.get("claim") or answer.get("claim"),
                "question": item.get("question") or answer.get("question"),
                "question_round_1": answer_round_1.get("question", ""),
                "answer_round_1_summary": answer_round_1.get("answer", ""),
                "question_round_2": answer.get("question", ""),
                "answer_round_2_summary": answer.get("answer", ""),
                "yfinance_answer": item.get("yfinance_answer") or answer.get("answer"),
                "evidence_used": item.get("evidence_used") if isinstance(item.get("evidence_used"), list) else _merge_unique(
                    answer_round_1.get("evidence_used", []),
                    answer.get("evidence_used", []),
                ),
            }
        )
        evaluated_ids.add(claim_id)
    missing_ids = sorted(set(answers_by_id) - evaluated_ids)
    if missing_ids:
        raise RuntimeError(f"YFinance SY evaluation payload missing claim ids: {', '.join(missing_ids)}")

    state["evaluations"] = evaluations
    return state


def all_claims_keep(state: SYState) -> bool:
    evaluations = state.get("evaluations", [])
    return bool(evaluations) and all(item.get("decision") == "keep" for item in evaluations)


def next_after_evaluation(state: SYState) -> str:
    if all_claims_keep(state):
        return "finalize"
    return "rewrite"


def build_revision_brief_node(state: SYState) -> SYState:
    state["revision_brief"] = [
        {
            "claim_id": item.get("claim_id"),
            "section": item.get("section", ""),
            "decision": item.get("decision"),
            "issue_ko": item.get("sy_reason", ""),
            "evidence_to_preserve": [
                str(value)
                for value in item.get("evidence_used", [])
                if value
            ],
            "rewrite_direction_ko": item.get("revision_suggestion")
            or _default_rewrite_direction(item.get("decision")),
            "wording_constraints_ko": (
                "SY 질문/답변을 그대로 붙이지 말고, 보고서 내부 수치와 문장으로 설명 가능한 범위에서 "
                "분석 문장에 자연스럽게 반영한다."
            ),
        }
        for item in state.get("evaluations", [])
        if item.get("decision") != "keep"
    ]
    return state


def _default_rewrite_direction(decision: Any) -> str:
    if decision == "revise":
        return "보고서 내부 근거가 설명되는 범위로 표현을 약화하고 근거 연결, 범위, 기간 설명을 보강한다."
    if decision == "remove":
        return "보고서 내부 근거와 충돌하거나 제외가 필요한 주장은 보고서에서 제거한다."
    return "보고서 내부 근거로 설명하기 어려운 주장은 삭제하거나 보수적 표현으로 완전히 다시 작성한다."


def rewrite_yfinance_report_node(state: SYState) -> SYState:
    llm = make_llm(state.get("env_file"), state.get("llm_model"))
    system_prompt = """
당신은 YFinance Agent입니다.

역할:
- SY Agent 검증 결과와 revision brief를 반영해 기존 YFinance report를 한 번만 다시 작성합니다.
- 반드시 기존 YFinance report와 같은 JSON 구조의 객체 하나만 출력합니다.
- 새로운 외부 사실, buy/sell/hold, 목표주가, deterministic price forecast는 만들지 않습니다.
- 기존 report 내부 수치, 기존 claim, YFinance 답변, SY evaluation feedback, revision brief만 사용합니다.
- SY 질문/답변이나 검증 메타데이터를 본문에 그대로 붙여넣지 말고, 문맥에 맞는 분석 문장으로 자연스럽게 녹여 씁니다.
- revise 항목은 표현, 근거 연결, 범위, 기간 설명을 보강합니다.
- hallucination_candidate 또는 remove 항목은 삭제하거나 내부 근거가 있는 보수적 표현으로 완전히 다시 씁니다.
"""
    user_prompt = json.dumps(
        {
            "current_report": state.get("report", {}),
            "revision_brief": state.get("revision_brief", []),
            "questions_round_1": state.get("questions_round_1", []),
            "answers_round_1": state.get("yfinance_answers_round_1", []),
            "questions_round_2": state.get("questions_round_2", []),
            "answers_round_2": state.get("yfinance_answers_round_2", []),
            "sy_evaluations": state.get("evaluations", []),
            "output_requirement": "Return only the rewritten YFinance report JSON object.",
        },
        ensure_ascii=False,
    )
    response = llm.invoke([
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_prompt},
    ])
    rewritten = extract_json(response.content)
    if not rewritten:
        raise RuntimeError("YFinance report rewrite payload must be a JSON object.")
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
    state["report"] = rewritten
    state["report_rewritten"] = True
    return state


def build_verified_report_node(state: SYState) -> SYState:
    report = state["report"]
    evaluations = state["evaluations"]

    verified_claims = []
    revised_claims = []
    hallucination_candidates = []
    removed_claims = []

    for ev in evaluations:
        decision = ev.get("decision")

        if decision == "keep":
            verified_claims.append(ev)
        elif decision == "revise":
            revised_claims.append(ev)
        elif decision == "hallucination_candidate":
            hallucination_candidates.append(ev)
        elif decision == "remove":
            removed_claims.append(ev)
        else:
            hallucination_candidates.append(ev)

    verified_report = {
        "source_agent": report.get("agent_name"),
        "verifier_agent": "SY Agent",
        "target_company": report.get("target_company"),
        "ticker": report.get("ticker"),
        "as_of_date": report.get("as_of_date"),
        "base_dir": state.get("base_dir") or BASE_DIR,
        "verification_mode": "question_answer_based_evidence_verification",
        "verification_policy": {
            "buy_sell_hold_allowed": False,
            "external_fact_addition_allowed": False,
            "core_question": "왜 이런 판단을 했는가?",
            "hallucination_policy": "YFinance Agent가 보고서 내부 수치와 논리로 답변하지 못하면 LLM evaluator가 hallucination_candidate 또는 remove 여부를 판단"
        },
        "summary": {
            "total_claims": len(evaluations),
            "verified_count": len(verified_claims),
            "revised_count": len(revised_claims),
            "hallucination_candidate_count": len(hallucination_candidates),
            "removed_count": len(removed_claims),
            "report_rewritten": bool(state.get("report_rewritten", False)),
            "rewrite_policy": "single_pass_contextual_rewrite_no_reverification"
            if state.get("report_rewritten")
            else "no_rewrite_all_claims_kept_or_final_validation_only",
        },
        "verified_claims": verified_claims,
        "revised_claims": revised_claims,
        "hallucination_candidates": hallucination_candidates,
        "removed_claims": removed_claims,
        "revision_brief": state.get("revision_brief", []),
        "rewrite_history": state.get("rewrite_history", []),
        "question_answer_log_path": state.get("question_log_path") or QUESTION_LOG_PATH,
        "original_report": report
    }

    qa_log = {
        "questions_round_1": state["questions_round_1"],
        "yfinance_answers_round_1": state["yfinance_answers_round_1"],
        "questions_round_2": state["questions_round_2"],
        "yfinance_answers_round_2": state["yfinance_answers_round_2"],
        "sy_evaluations": evaluations,
        "revision_brief": state.get("revision_brief", []),
        "report_rewritten": bool(state.get("report_rewritten", False)),
    }

    save_json(qa_log, state.get("question_log_path") or QUESTION_LOG_PATH)

    state["verified_report"] = verified_report
    return state


def build_strategy_compatible_verified_report(
    verified_report: Dict[str, Any],
    *,
    validation_report_path: str | None = None,
) -> Dict[str, Any]:
    """Return the original YFinance report shape annotated with SY validation."""

    original_report = verified_report.get("original_report") or {}
    report = copy.deepcopy(original_report) if isinstance(original_report, dict) else {}
    summary = copy.deepcopy(verified_report.get("summary") or {})
    attention_items = _compact_yfinance_validation_items(verified_report)
    sy_validation = {
        "verifier_agent": verified_report.get("verifier_agent", "SY Agent"),
        "verification_mode": verified_report.get("verification_mode"),
        "verification_policy": copy.deepcopy(verified_report.get("verification_policy") or {}),
        "validation_report_path": validation_report_path,
        "question_answer_log_path": verified_report.get("question_answer_log_path"),
        "summary": summary,
        "attention_items": attention_items,
        "revision_brief": copy.deepcopy(verified_report.get("revision_brief") or []),
    }

    report["report_status"] = (
        "sy_revision_reflected_no_reverification"
        if summary.get("report_rewritten")
        else "sy_validation_reflected"
    )
    report["sy_validation"] = sy_validation
    report["verification_summary"] = summary
    _append_yfinance_sy_constraints(report, summary, attention_items)
    return report


def _compact_yfinance_validation_items(
    verified_report: Dict[str, Any],
    *,
    limit: int = 10,
) -> List[Dict[str, Any]]:
    attention_items: List[Dict[str, Any]] = []
    for bucket_name in ("removed_claims", "hallucination_candidates", "revised_claims"):
        bucket = verified_report.get(bucket_name) or []
        if not isinstance(bucket, list):
            continue
        for item in bucket:
            if not isinstance(item, dict):
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
                return attention_items
    return attention_items


def _append_yfinance_sy_constraints(
    report: Dict[str, Any],
    summary: Dict[str, Any],
    attention_items: List[Dict[str, Any]],
) -> None:
    cross = report.setdefault("cross_data_reconciliation", {})
    if not isinstance(cross, dict):
        return
    integrated = cross.setdefault("news_plus_dart_plus_market", {})
    if not isinstance(integrated, dict):
        return
    divergences = integrated.setdefault("divergences", [])
    if not isinstance(divergences, list):
        divergences = [divergences] if divergences else []
        integrated["divergences"] = divergences

    total = int(summary.get("total_claims") or 0)
    revised = int(summary.get("revised_count") or 0)
    hallucination = int(summary.get("hallucination_candidate_count") or 0)
    removed = int(summary.get("removed_count") or 0)
    if total and (revised or hallucination or removed):
        divergences.append(
            {
                "point": (
                    "SY 검증 결과 YFinance 핵심 주장 "
                    f"{total}건 중 revise {revised}건, hallucination_candidate {hallucination}건, "
                    f"remove {removed}건으로 분류되어 시장 데이터 기반 강한 주장의 신뢰도는 제한적이다."
                ),
                "cross_analysis": "약화 또는 근거 부족 판정 항목은 투자 판단의 직접 근거로 쓰기에는 제한적이다.",
            }
        )
    for item in attention_items[:5]:
        decision = item.get("decision")
        claim = item.get("claim")
        reason = item.get("sy_reason")
        if decision and claim:
            divergences.append(
                {
                    "point": f"SY {decision}: {claim}",
                    "cross_analysis": str(reason or ""),
                }
            )


def _truncate_text(value: Any, *, limit: int) -> str:
    text = " ".join(str(value or "").split())
    return text if len(text) <= limit else text[: limit - 3] + "..."


def default_strategy_report_path(output_path: str | Path) -> Path:
    return Path(output_path).expanduser().resolve().with_name(STRATEGY_REPORT_FILENAME)


def save_verified_report_node(state: SYState) -> SYState:
    output_path = Path(state.get("output_path") or OUTPUT_PATH).expanduser().resolve()
    strategy_report_path = (
        Path(state.get("strategy_report_path")).expanduser().resolve()
        if state.get("strategy_report_path")
        else default_strategy_report_path(output_path)
    )
    save_json(state["verified_report"], str(output_path))
    strategy_report = build_strategy_compatible_verified_report(
        state["verified_report"],
        validation_report_path=str(output_path),
    )
    save_json(strategy_report, str(strategy_report_path))
    state["strategy_report"] = strategy_report
    state["strategy_report_path"] = str(strategy_report_path)
    return state


def build_graph():
    graph = StateGraph(SYState)

    graph.add_node("load_report", load_report_node)
    graph.add_node("extract_claims", extract_claims_node)
    graph.add_node("generate_questions", generate_questions_node)
    graph.add_node("ask_yfinance_agent", ask_yfinance_agent_node)
    graph.add_node("generate_followup_questions", generate_followup_questions_node)
    graph.add_node("ask_yfinance_agent_round_2", ask_yfinance_agent_round_2_node)
    graph.add_node("evaluate_answers", evaluate_answers_node)
    graph.add_node("build_revision_brief", build_revision_brief_node)
    graph.add_node("rewrite_yfinance_report", rewrite_yfinance_report_node)
    graph.add_node("build_verified_report", build_verified_report_node)
    graph.add_node("save_verified_report", save_verified_report_node)

    graph.set_entry_point("load_report")

    graph.add_edge("load_report", "extract_claims")
    graph.add_edge("extract_claims", "generate_questions")
    graph.add_edge("generate_questions", "ask_yfinance_agent")
    graph.add_edge("ask_yfinance_agent", "generate_followup_questions")
    graph.add_edge("generate_followup_questions", "ask_yfinance_agent_round_2")
    graph.add_edge("ask_yfinance_agent_round_2", "evaluate_answers")
    graph.add_conditional_edges(
        "evaluate_answers",
        next_after_evaluation,
        {
            "rewrite": "build_revision_brief",
            "finalize": "build_verified_report",
        },
    )
    graph.add_edge("build_revision_brief", "rewrite_yfinance_report")
    graph.add_edge("rewrite_yfinance_report", "build_verified_report")
    graph.add_edge("build_verified_report", "save_verified_report")
    graph.add_edge("save_verified_report", END)

    return graph.compile()


def main():
    parser = argparse.ArgumentParser(description="Run YFinance SY Agent validation")
    parser.add_argument("--input", default=INPUT_PATH, help="YFinance analyst report JSON path.")
    parser.add_argument("--output", default=OUTPUT_PATH, help="Verified YFinance report JSON path.")
    parser.add_argument(
        "--strategy-output",
        default=None,
        help="Strategy-compatible verified YFinance report JSON path. Defaults to yfinance_verified_report.json next to --output.",
    )
    parser.add_argument("--question-log", default=QUESTION_LOG_PATH, help="Question/answer log JSON path.")
    parser.add_argument("--env-file", default=str(DEFAULT_ENV_PATH), help="Env file containing OPENAI_API_KEY.")
    parser.add_argument("--model", default=None, help="OpenAI model for YFinance SY validation. Defaults to OPENAI_MODEL or gpt-5.4-mini.")
    args = parser.parse_args()

    input_path = str(Path(args.input).expanduser().resolve())
    output_path = str(Path(args.output).expanduser().resolve())
    strategy_report_path = (
        str(Path(args.strategy_output).expanduser().resolve())
        if args.strategy_output
        else str(default_strategy_report_path(output_path))
    )
    question_log_path = str(Path(args.question_log).expanduser().resolve())
    env_file = str(Path(args.env_file).expanduser().resolve())
    base_dir = str(Path(output_path).parent)

    app = build_graph()

    initial_state: SYState = {
        "input_path": input_path,
        "output_path": output_path,
        "question_log_path": question_log_path,
        "base_dir": base_dir,
        "env_file": env_file,
        "llm_model": args.model or "",
        "strategy_report_path": strategy_report_path,
        "report_rewritten": False,
        "revision_brief": [],
        "rewrite_history": [],
        "report": {},
        "claims": [],
        "questions": [],
        "questions_round_1": [],
        "questions_round_2": [],
        "yfinance_answers": [],
        "yfinance_answers_round_1": [],
        "yfinance_answers_round_2": [],
        "evaluations": [],
        "verified_report": {},
        "strategy_report": {},
    }

    result = app.invoke(initial_state)

    print("SY Agent 검증 완료")
    print(f"입력 파일: {input_path}")
    print(f"검증 결과: {output_path}")
    print(f"Strategy 입력용 검증 보고서: {strategy_report_path}")
    print(f"질문/답변 로그: {question_log_path}")
    print(json.dumps(result["verified_report"]["summary"], ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
