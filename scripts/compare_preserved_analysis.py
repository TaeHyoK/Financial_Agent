"""Opt-in development arm: retain analysis, generate decision only, then edit with Writer.

Reuses each previous two-stage analysis to isolate the downstream handoff change.
The production pipeline and its output contract are not changed.
"""
import argparse
import copy
import hashlib
import json
import os
from pathlib import Path
import sys
import time

sys.path.insert(0, str(Path(__file__).resolve().parent))
from compare_strategy_stages import (
    ROOT, MODEL, DECISION_SYSTEM, strict_object, analysis_response_format,
    decision_payload, digest, save, call_openai, parse_llm_json, compact_json,
    decision_prompt_v5, strategy_decision_response_format_v5,
    validate_strategy_context_package_v5, align_strategy_decision_v5_evidence_plan,
    validate_strategy_decision_v5, summarize_execution_usage, Draft202012Validator,
)

FIELDS = ("earnings_review", "outlook", "price_assessment", "alternative_interpretation")
DECISION_TASK = """## 최종 의견과 수정 사항 작성
앞선 preliminary_analysis는 보존되는 독립 분석 산출물이다. 이번 호출은 분석 문단 전체를 다시 작성하지 않는다.
원래 관측 전체와 분석을 대조해 최종 의견과 그 이유, 핵심 근거 및 위험을 작성한다.
분석은 추가 관측이나 독립 증거가 아니다. 의견은 사전에 정해져 있지 않다.
- strategy_brief의 decision_rationale에는 무엇이 판단을 갈랐는지와 가장 설득력 있는 대안보다 선택한 의견이 적절한 이유를 설명한다.
- counterview는 가장 설득력 있는 대안 의견과 전제 차이를 설명한다. 분석의 alternative_interpretation을 그대로 반복할 필요는 없다.
- recommendation, headline, thesis는 위 비교에 따른 의견과 핵심 이유다. thesis는 요약이고 decision_rationale는 필요한 설명을 충분히 작성한다.
- decision_limitation은 의견의 방향·확신을 실질적으로 제한하는 추론상 취약점이 있을 때만 작성한다. 없으면 빈 text와 빈 card_keys다. 정상적 공시 시차나 예측치 부재를 이유로 채우지 않는다.
- evidence_sufficiency와 decision_confidence는 입력 충실도와 판단 확신을 구분한다.
- key_risks에는 실제 사업상 위험과 현재 전망에 미치는 의미를 작성한다. 노출과 실제 훼손을 구분하며 같은 위험을 중복 계산하지 않는다.
- report_insights는 빈 배열이다. 상세 분석은 별도 보존되어 Writer에 전달된다.

analysis_corrections는 분석에 사실 오류, 근거보다 강한 해석, 잘못된 단위, 내부 식별자 또는 부자연스러운 문장이 있을 때만 작성한다.
각 수정에는 section, 원문에서 정확히 한 번 나오는 연속 구절 original_quote, replacement_text, reason, 근거 card_keys를 제공한다.
수정 대상은 오류가 있는 최소한의 문장 또는 구절이다. 수정하지 않은 분석은 그대로 보존된다.
단순히 짧게 만들거나 선택한 의견에 맞추기 위해 유효한 비교·가정을 삭제하지 않는다. 수정이 없으면 빈 배열이다.
겹치는 구절을 여러 번 수정하지 않는다. 문장 삭제가 필요하면 replacement_text를 빈 문자열로 둘 수 있다.
원자료의 수치·기간·단위를 유지하며, 근거 없이 시장 기대나 실적의 주가 반영 정도를 단정한 해석은 관측 범위로 바로잡는다.
수정 근거는 기존 카드에 연결한다. 표현만 바로잡는 수정은 빈 card_keys가 가능하다. 상세 내부 사고 과정이 아니라 독자용 결론과 간단한 편집 사유만 작성한다.

schema_revision은 기존 전달용 버전이며, 이번 실제 생성 계약은 preserved_analysis_decision_v1이다.
"""


def decision_only_prompt():
    base = decision_prompt_v5("annual")
    before, rest = base.split("## 분석 결과와 의견 작성", 1)
    _, evidence = rest.split("## 근거 계약", 1)
    return before + DECISION_TASK + "\n## 근거 계약" + evidence


def decision_only_format(context):
    fmt = copy.deepcopy(strategy_decision_response_format_v5(context, required_horizon="12개월"))
    fmt["json_schema"]["name"] = "preserved_analysis_decision_v1"
    schema = fmt["json_schema"]["schema"]
    brief = schema["properties"]["strategy_brief"]
    for field in FIELDS[:3]:
        del brief["properties"][field]
        brief["required"].remove(field)
    schema["properties"]["report_insights"]["maxItems"] = 0
    schema["properties"]["analysis_corrections"] = {
        "type": "array", "items": strict_object({
            "section": {"type": "string", "enum": list(FIELDS)},
            "original_quote": {"type": "string", "minLength": 1},
            "replacement_text": {"type": "string"},
            "reason": {"type": "string", "minLength": 1},
            "card_keys": {"type": "array", "items": {"type": "string", "enum": sorted(context["evidence_cards"])}}
        })}
    schema["required"].append("analysis_corrections")
    return fmt


def apply_explicit_corrections(analysis, corrections):
    """Apply only model-specified exact edits; never infer or repair judgment."""
    effective = copy.deepcopy(analysis)
    for field in FIELDS:
        original = analysis[field]["text"]
        edits = []
        for row in corrections:
            if row["section"] != field:
                continue
            quote = row["original_quote"]
            if not quote or original.count(quote) != 1:
                raise ValueError(f"Correction must identify one exact original span: {field}")
            start = original.index(quote)
            edits.append((start, start + len(quote), row["replacement_text"]))
            effective[field]["card_keys"] = list(dict.fromkeys(effective[field]["card_keys"] + row["card_keys"]))
        edits.sort()
        if any(left[1] > right[0] for left, right in zip(edits, edits[1:])):
            raise ValueError(f"Overlapping corrections: {field}")
        for start, end, replacement in reversed(edits):
            effective[field]["text"] = effective[field]["text"][:start] + replacement + effective[field]["text"][end:]
    return effective


def assemble_writer_projection(context, analysis, decision):
    """Compatibility projection, NOT a fresh LLM-generated Strategy document."""
    effective = apply_explicit_corrections(analysis, decision["analysis_corrections"])
    Draft202012Validator(analysis_response_format(context)["json_schema"]["schema"]).validate(effective)
    projection = copy.deepcopy(decision)
    del projection["analysis_corrections"]
    projection["strategy_brief"].update({field: copy.deepcopy(effective[field]) for field in FIELDS[:3]})
    projection = align_strategy_decision_v5_evidence_plan(projection, context=context)
    # Keep alternative-analysis citations too, even if the decision does not use them.
    plan = projection["evidence_plan"]
    selected = {row["card_key"] for name in ("decision_basis_cards", "report_context_cards") for row in plan[name]}
    for key in effective["alternative_interpretation"]["card_keys"]:
        if key not in selected:
            domain = context["evidence_cards"][key].get("domain")
            plan["report_context_cards"].append({"card_key": key,
                "purpose": "peer_context" if domain == "peer" else "event_context" if domain == "news" else "business_context",
                "report_implication": effective["alternative_interpretation"]["text"]})
            selected.add(key)
    validate_strategy_decision_v5(projection, context=context, required_horizon="12개월")
    return effective, projection


def writer_context_with_analysis(handoff, analysis, effective, decision):
    from html_report_writer import _build_context
    context = _build_context(writer_handoff=handoff)
    context["preserved_analysis"] = copy.deepcopy(effective)
    context["analysis_provenance"] = {"version": "preserved_analysis_handoff_v1",
        "original_sha256": digest(analysis), "corrections": copy.deepcopy(decision["analysis_corrections"])}
    context["decision_only_output"] = copy.deepcopy(decision)
    context["writing_rules"]["preserved_analysis_policy"] = (
        "preserved_analysis는 명시적 오류 수정만 적용한 통합 분석이고 decision_only_output은 최종 의견이다. "
        "독립된 추가 사실이 아니며 기존 카드로 검증한다. 의견은 변경하지 않는다. "
        "실적·전망·가격 문단은 분석의 중요한 지표 관계, 대비, 가정과 관측 범위를 보존하여 편집한다. "
        "최종 의견의 짧은 요약만으로 본문을 대체하지 않는다. 분석 전체를 그대로 붙이거나 모든 문장을 반복할 필요는 없다. "
        "대안 해석은 의견을 이해하는 데 필요한 경우에만 활용한다. 무조건 긍정·부정 균형을 맞추지 않는다. "
        "수정 기록의 원래 오류 문장을 본문에 다시 넣지 않는다. 내부 키와 수정 절차를 독자에게 노출하지 않는다."
    )
    return context


def run(args):
    sys.path.insert(0, str(ROOT / "src/Agent_Team/Writer Agent"))
    prior = Path(args.prior_run).resolve()
    sources = [prior / "strategy_context.json", prior / "analysis_raw.json",
               Path(args.domain_run).resolve() / "normalized_domain_bundle.json"]
    hashes = {str(path): hashlib.sha256(path.read_bytes()).hexdigest() for path in sources}
    context, analysis, bundle = [json.loads(path.read_text(encoding="utf-8")) for path in sources]
    validate_strategy_context_package_v5(context)
    Draft202012Validator(analysis_response_format(context)["json_schema"]["schema"]).validate(analysis)
    from Agent_Team.Strategy_Agent.contracts_v2 import build_compact_strategy_packet_v2
    from Agent_Team.Strategy_Agent.contracts_v5 import build_strategy_context_package_v5
    packet, provenance, _, _ = build_compact_strategy_packet_v2(bundle)
    if build_strategy_context_package_v5(packet, input_bundle=bundle) != context:
        raise ValueError("Frozen context does not match domain bundle; cannot compare Writer inputs")
    output = Path(args.output_dir).resolve()
    output.mkdir(parents=True, exist_ok=False)
    fmt, prompt = decision_only_format(context), decision_only_prompt()
    payload = decision_payload(context, analysis)
    for name, value in {"strategy_context": context, "analysis_original": analysis,
                        "decision_input": payload, "decision_schema": fmt}.items():
        save(output / f"{name}.json", value)
    (output / "decision_prompt.md").write_text(prompt, encoding="utf-8")
    status = {"status": "prepared", "model": MODEL, "source_hashes": hashes,
        "base_context_sha256": digest(context), "analysis_sha256": digest(analysis),
        "scope": "reused opinion-free analysis; decision-only + optional Writer; no production migration",
        "analysis_generation_cost_included": False, "with_writer": args.with_writer}
    save(output / "status.json", status)
    if not args.live:
        return
    from dotenv import load_dotenv
    load_dotenv(ROOT / "configs/.env")
    if not os.getenv("OPENAI_API_KEY"):
        raise RuntimeError("OPENAI_API_KEY unavailable")
    os.environ.update(LLM_EXECUTION_ID=output.name, LLM_RUN_ID=output.name, LLM_RUN_ROLE="final",
        LLM_COMPANY_NAME=context["target_company"]["company_name"], LLM_USAGE_MANIFEST=str(output / "usage.jsonl"))
    start = time.perf_counter()
    try:
        response = call_openai(f"{prompt}\n\nInput JSON:\n{compact_json(payload)}", MODEL, 300,
            system_message=DECISION_SYSTEM, response_format=fmt)
        save(output / "decision_response.json", response)
        decision = parse_llm_json(response["text"])
        save(output / "decision_only_raw.json", decision)
        print("Decision-only response saved", flush=True)
        Draft202012Validator(fmt["json_schema"]["schema"]).validate(decision)
        effective, projection = assemble_writer_projection(context, analysis, decision)
        save(output / "analysis_effective.json", effective)
        save(output / "writer_strategy_projection.json", projection)
        save(output / "projection_provenance.json", {"kind": "compatibility_adapter_not_single_model_response",
            "analysis_fields": list(FIELDS[:3]), "decision_fields": list(decision["strategy_brief"]),
            "original_analysis_sha256": digest(analysis), "decision_only_sha256": digest(decision),
            "corrections": decision["analysis_corrections"]})
        status.update(status="decision_completed", recommendation=decision["strategy_brief"]["recommendation"],
            correction_count=len(decision["analysis_corrections"]), decision_elapsed_seconds=round(time.perf_counter()-start, 3))
        if args.with_writer:
            from writer_handoff import build_writer_editorial_packet
            from html_report_writer import (_call_openai_writer, writer_report_response_format,
                                            validate_raw_writer_payload, normalize_report_payload)
            from formatted_html_renderer import build_complete_html
            from html_report_validator import validate_html_report
            handoff, _ = build_writer_editorial_packet(strategy_packet=packet, strategy_decision=projection, strategy_provenance=provenance)
            writer_context = writer_context_with_analysis(handoff, analysis, effective, decision)
            save(output / "writer_handoff.json", handoff)
            save(output / "writer_context.json", writer_context)
            raw, metadata = _call_openai_writer(context=writer_context, model=MODEL, api_key=os.environ["OPENAI_API_KEY"],
                response_format=writer_report_response_format(handoff), include_metadata=True)
            save(output / "writer_raw.json", raw)
            save(output / "writer_call.json", metadata)
            print("Writer response saved", flush=True)
            validate_raw_writer_payload(raw)
            report = normalize_report_payload(raw, writer_handoff=handoff)
            save(output / "report_payload.json", report)
            html = build_complete_html(report)
            company = context["target_company"]["company_name"].replace("/", "_").replace("\\", "_")
            (output / f"report_{company}.html").write_text(html, encoding="utf-8")
            validation = validate_html_report(report_payload=report, html_content=html, writer_handoff=handoff)
            save(output / "validation.json", validation)
            status["status"] = "completed" if validation["status"] == "pass" else "validation_failed"
    except Exception as exc:
        status.update(status="failed", error=str(exc))
        raise
    finally:
        status["source_unchanged"] = all(hashlib.sha256(Path(path).read_bytes()).hexdigest() == value for path, value in hashes.items())
        status["elapsed_seconds"] = round(time.perf_counter()-start, 3)
        save(output / "status.json", status)
        usage = summarize_execution_usage(output / "usage.jsonl", execution_id=output.name,
            pipeline_completed=status["status"] in {"completed", "decision_completed"},
            expected_logical_calls_by_role={"target": 0, "peer": 0, "final": 1 + int(args.with_writer)})
        save(output / "usage_summary.json", usage)
        print(json.dumps({"status": status["status"], "cost_usd": usage.get("estimated_api_cost", {}).get("total_cost_usd")}), flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--prior-run", required=True)
    parser.add_argument("--domain-run", required=True)
    parser.add_argument("--output-dir", required=True)
    parser.add_argument("--live", action="store_true")
    parser.add_argument("--with-writer", action="store_true")
    run(parser.parse_args())
