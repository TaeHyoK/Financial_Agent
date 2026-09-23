"""Opt-in frozen-context comparison; does not change the production pipeline."""
import argparse
import copy
import hashlib
import json
import os
from pathlib import Path
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from jsonschema import Draft202012Validator
from Agent_Team.Strategy_Agent.agent import (
    call_openai, parse_llm_json, decision_prompt, DEFAULT_OPENAI_MAX_TOKENS,
    build_strategy_report_projection, render_strategy_projection_markdown,
)
from Agent_Team.Strategy_Agent.decision import (
    CONTEXT_VERSION, validate_strategy_context_package, strategy_decision_response_format,
    align_strategy_decision_evidence_plan, validate_strategy_decision,
)
from shared.llm_clients import compact_json
from orchestration.usage_summary import summarize_execution_usage

MODEL = "gpt-5.4-mini"
DECISION_SYSTEM = (
    "당신은 기업 리서치 Strategy Agent다. 제공된 자료를 분석하고 대안 해석을 비교한 뒤 "
    "투자 의견과 선택 이유를 작성한다. 실제 사용한 근거를 연결한 한국어 JSON 객체 하나를 반환한다."
)
ANALYSIS_SYSTEM = "You are a financial research analyst. Return evidence-grounded analytical findings without an investment recommendation."
ANALYSIS_PROMPT = """# 의견 선택 전 통합 분석
입력된 관측 자료와 재무·뉴스·시장 하위 분석을 종합해 기준일부터 향후 12개월의 기업 변화를 분석한다.
이번 단계의 업무는 분석 결과 작성이며 투자 의견, 등급, 매매 행동, 특정 의견의 정당화는 작성하지 않는다.
목표주가·기대수익률·등급 기준점은 필요하지 않다. 상세한 내부 사고 과정이 아니라 근거가 연결된 독자용 분석을 작성한다.

입력 역할:
- evidence_cards의 수치·기간·출처를 보존한다. 모델 요약은 원천 사실과 구분한다.
- domain_handoffs는 하위 해석이다. 관측과 대조해 수용·보완·수정하며 새로운 사실로 취급하지 않는다.
- applicability_notes와 카드별 속성은 관련 추론의 적용 범위다. 모두 반복하거나 악재로 취급하지 않는다.

작성할 분석:
- earnings_review: 최신 동기 비교와 3년 추세에서 무엇이 변했는지 설명한다. 매출·이익·비용·현금흐름 중 서로 뒷받침하거나 엇갈리는 관측을 함께 해석해 변화 원인과 강도를 평가한다. 재무상태는 사업 전망을 감당할 여력과 연결한다. 기업에서 중요한 관계를 선택하며 모든 지표 조합을 채우지 않는다. 입력에 원인이 없으면 만들지 않는다.
- outlook: 앞의 분석에서 이어지는 12개월 기본 사업 전망을 제시한다. 확인된 변화와 전망의 가정을 구분하고 가정을 채택하는 이유를 설명한다. 위험의 노출 범위, 위험이 현실화됐는지, 예상 사업 효과를 구분한다.
- price_assessment: 사업 전망과 가격의 의미를 분리한다. 관측된 주가·상대성과·거래량·가치평가에서 해석 가능한 범위를 설명한다. 과거 주가만으로 시장의 기대나 실적 반영 정도를 단정하지 않는다. 비교 기준 없는 배수로 고저를 단정하지 않는다.
- alternative_interpretation: 같은 자료에서 가능한 가장 설득력 있는 다른 사업·가격 해석과 그 가정 차이를 설명한다. 투자 등급을 대안으로 제시하거나 억지 반대 근거를 만들지 않는다.

관측 근거의 개수나 하위 에이전트 의견의 수를 세지 않는다. 같은 원천을 중복 근거로 취급하지 않는다.
정기공시는 사업 체력의 기준선으로, 뉴스는 기간 중의 사업 변화로 구분한다. 전망에서는 중요한 사건의 구체적 내용과 기존 사업에서 달라진 점, 12개월 전망에 미치는 의미 및 필요한 가정을 설명한다. 공시 실적을 반복한 뒤 재무 기여 미확인만 적는 것으로 대체하지 않는다. 뉴스 개수나 방향을 채울 필요는 없으며 근거가 없는 영향은 만들지 않는다.
누적 재무는 동기끼리 비교하고 기간·시점 자료를 구분한다. 비교기업 한 곳을 업종 평균으로 확대하지 않는다.
뉴스는 보도된 사건과 예상 효과를 구분한다. financial_link_status=not_observed는 확정 기여·훼손의 증거가 아니라는 뜻이며, 예상 영향은 가정과 연결 근거로 설명할 수 있다.
모델 요약의 종료일을 사건일로 바꾸지 않는다. 출처보다 더 구체적인 사실·미래 수치를 만들지 않는다.
각 분석의 card_keys에는 실제 뒷받침하는 입력 카드만 연결하고 개수 때문에 인용을 제외하지 않는다. 자료가 없어 판단할 수 없는 항목은 그 범위만 설명하고 참조를 꾸며내지 않는다.
한국어 -다 문체의 JSON 객체 하나를 반환한다. 독자 문장에 내부 키나 절차 설명을 노출하지 않는다.
"""
MEMO_INSTRUCTION = """

## 의견 선택 전 분석 자료
preliminary_analysis는 같은 입력으로 별도 작성한 의견 없는 통합 분석이다. 원자료가 아니며 추가적인 독립 증거로 세지 않는다.
원래 strategy_context_package의 모든 근거도 함께 제공된다. 분석 자료를 근거와 대조해 수용·보완·수정하고, 그 해석에 따라 최종 의견을 선택한다.
원래 근거를 분석 자료로 대체하지 않는다. 분석 자료에서 활용한 관계와 가정은 최종 분석에도 유지하되, 타당하지 않은 해석은 그대로 승계하지 않는다.
최종 인용은 원래 카드 키에 연결하며 분석 자료 자체를 새로운 카드로 만들지 않는다. 기존 출력 계약을 그대로 따른다.
"""


def strict_object(properties):
    return {"type": "object", "properties": properties, "required": list(properties), "additionalProperties": False}


def analysis_response_format(context):
    linked = strict_object({"text": {"type": "string", "minLength": 1},
                            "card_keys": {"type": "array", "items": {"type": "string", "enum": sorted(context["evidence_cards"])}}})
    schema = strict_object({"analysis_version": {"type": "string", "enum": ["predecision_analysis_v1"]},
                            **{key: copy.deepcopy(linked) for key in ("earnings_review", "outlook", "price_assessment", "alternative_interpretation")}})
    return {"type": "json_schema", "json_schema": {"name": "predecision_analysis_v1", "strict": True, "schema": schema}}


def decision_payload(context, memo=None):
    payload = {CONTEXT_VERSION: copy.deepcopy(context)}
    if memo is not None:
        payload["preliminary_analysis"] = copy.deepcopy(memo)
    return payload


def digest(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True).encode()).hexdigest()


def save(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def run(args):
    source = Path(args.context_file).resolve()
    source_hash = hashlib.sha256(source.read_bytes()).hexdigest()
    context = json.loads(source.read_text(encoding="utf-8"))
    validate_strategy_context_package(context)
    output = Path(args.output_dir).resolve()
    output.mkdir(parents=True, exist_ok=False)
    prompt = decision_prompt("annual")
    decision_format = strategy_decision_response_format(context, required_horizon="12개월")
    save(output / "strategy_context.json", context)
    save(output / "decision_schema.json", decision_format)
    (output / "decision_prompt.md").write_text(prompt + (MEMO_INSTRUCTION if args.arm == "two_stage" else ""), encoding="utf-8")
    if args.arm == "two_stage":
        (output / "analysis_prompt.md").write_text(ANALYSIS_PROMPT, encoding="utf-8")
        save(output / "analysis_schema.json", analysis_response_format(context))
    status = {"status": "prepared", "arm": args.arm, "model": MODEL, "source_path": str(source),
              "source_sha256": source_hash, "base_context_sha256": digest(context),
              "decision_prompt_base_sha256": hashlib.sha256(prompt.encode()).hexdigest(),
              "decision_schema_sha256": digest(decision_format),
              "scope": "frozen Strategy input; no collection, domain/peer rerun, Writer call or ablation evaluation"}
    save(output / "status.json", status)
    if not args.live:
        return
    from dotenv import load_dotenv
    load_dotenv(ROOT / "configs/.env")
    if not os.getenv("OPENAI_API_KEY"):
        raise RuntimeError("OPENAI_API_KEY unavailable")
    os.environ.update(LLM_EXECUTION_ID=output.name, LLM_RUN_ID=output.name, LLM_RUN_ROLE="final",
                      LLM_COMPANY_NAME=context["target_company"]["company_name"], LLM_USAGE_MANIFEST=str(output / "usage.jsonl"))
    status["request_policy"] = {"model": MODEL, "max_completion_tokens": int(os.getenv("OPENAI_MAX_TOKENS", str(DEFAULT_OPENAI_MAX_TOKENS))),
                                "temperature": "not supplied", "reasoning_effort": "not supplied", "timeout_seconds": 300}
    start = time.perf_counter()
    try:
        def call(stage, stage_prompt, payload, system, response_format):
            save(output / f"{stage}_input.json", payload)
            tick = time.perf_counter()
            response = call_openai(f"{stage_prompt}\n\nInput JSON:\n{compact_json(payload)}", MODEL, 300,
                                   system_message=system, response_format=response_format)
            save(output / f"{stage}_response.json", response)
            value = parse_llm_json(response["text"])
            save(output / f"{stage}_raw.json", value)
            status.setdefault("stage_elapsed_seconds", {})[stage] = round(time.perf_counter() - tick, 3)
            print(f"{args.arm}: {stage} response saved", flush=True)
            return value

        memo = None
        if args.arm == "two_stage":
            memo = call("analysis", ANALYSIS_PROMPT, decision_payload(context), ANALYSIS_SYSTEM, analysis_response_format(context))
            Draft202012Validator(analysis_response_format(context)["json_schema"]["schema"]).validate(memo)
        payload = decision_payload(context, memo)
        assert payload[CONTEXT_VERSION] == context
        raw = call("strategy", prompt + (MEMO_INSTRUCTION if memo is not None else ""), payload, DECISION_SYSTEM, decision_format)
        Draft202012Validator(decision_format["json_schema"]["schema"]).validate(raw)
        aligned = align_strategy_decision_evidence_plan(raw, context=context)
        validate_strategy_decision(aligned, context=context, required_horizon="12개월")
        save(output / "strategy_decision.json", aligned)
        projection = build_strategy_report_projection(aligned, input_bundle={"target_company": context["target_company"]}, context=context)
        (output / "strategy_report.md").write_text(render_strategy_projection_markdown(projection), encoding="utf-8")
        status.update(status="completed", recommendation=aligned["strategy_brief"]["recommendation"],
                      decision_card_count=len(aligned["evidence_plan"]["decision_basis_cards"]),
                      context_card_count=len(aligned["evidence_plan"]["report_context_cards"]))
    except Exception as exc:
        status.update(status="failed", error=str(exc))
        raise
    finally:
        status["elapsed_seconds"] = round(time.perf_counter() - start, 3)
        status["source_unchanged"] = hashlib.sha256(source.read_bytes()).hexdigest() == source_hash
        save(output / "status.json", status)
        summary = summarize_execution_usage(output / "usage.jsonl", execution_id=output.name, pipeline_completed=status["status"] == "completed",
                                             expected_logical_calls_by_role={"target": 0, "peer": 0, "final": 2 if args.arm == "two_stage" else 1})
        save(output / "usage_summary.json", summary)
        print(json.dumps({"status": status["status"], "cost_usd": summary.get("estimated_api_cost", {}).get("total_cost_usd")}), flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--context-file", required=True)
    parser.add_argument("--arm", choices=("single", "two_stage"), required=True)
    parser.add_argument("--output-dir", required=True)
    parser.add_argument("--live", action="store_true")
    run(parser.parse_args())
