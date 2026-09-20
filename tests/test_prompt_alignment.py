"""Offline prompt/contract checks; no model calls or opinion scoring."""
import copy
import json
import unittest
from pathlib import Path
from jsonschema import Draft202012Validator, ValidationError
from test_optional_limits import writer_fixture
from html_report_writer import _writer_report_schema, normalize_report_payload
from html_report_writer import _editorial_system_prompt, FREE_FORM_WRITER_MODE, DETERMINISTIC_WRITER_MODE
from html_report_validator import _validate_card_key_coverage, _validate_claim_card_grounding
from test_annual_context import strategy_fixture
from Agent_Team.Financial_Agent.financial_analysis_agent import financial_analysis_json_schema
from Agent_Team.YFinance_Agent.reporting import yfinance_agent_json_schema
from Agent_Team.Strategy_Agent.decision import strategy_decision_response_format, validate_strategy_decision
from Agent_Team.News_Agent.context_export import (
    _build_llm_summary_request, _attach_source_event_ids,
    _load_llm_user_payload, _build_period_llm_requests,
)
from Agent_Team.Strategy_Agent.decision import align_strategy_decision_evidence_plan
from shared.evidence_cards import card_content_sha256
from writer_handoff import build_writer_editorial_packet, _reader_observation
from Agent_Team.News_Agent.analysis_agent import build_llm_request as build_news_request
from shared.news_selection import MONTHLY_NEWS_POLICY, MONTHLY_NEWS_LABEL


class NewsSynthesisTests(unittest.TestCase):
    def test_shared_instructions_preserve_inputs_across_selection_and_subdata_conditions(self):
        baseline = None
        for condition in ("full", "random", "no_sub"):
            with self.subTest(condition=condition):
                payload = {
                    "target_entity": {"company_name": "검증기업"},
                    "input_policy": {"raw_news_policy": MONTHLY_NEWS_POLICY},
                    "news_context": {}, "secondary_context": {},
                    "evidence_map": {"NEWS_RAW_1": {
                        "domain": "news", "title": "공급계약 발표",
                        "snippet": "공급계약을 발표했으나 금액은 공개하지 않았다.",
                        "ablation_selection": condition}},
                }
                if condition != "no_sub":
                    payload["secondary_context"] = {"financial": {
                        "evidence_catalog": {"FIN_1": {"domain": "financial", "value": 100}}}}
                    payload["evidence_map"]["FIN_1"] = {"domain": "financial", "value": 100}
                before = copy.deepcopy(payload)
                request = build_news_request(input_payload=payload, model="gpt-5.4-mini")
                body = json.loads(request["input"][1]["content"])
                instructions = (request["input"][0]["content"], body["task"], body["analysis_rules"])
                if baseline is None:
                    baseline = instructions
                self.assertEqual(instructions, baseline)
                self.assertEqual(payload, before)
                packet = body["input_payload"]
                self.assertEqual(packet["secondary_context"], payload["secondary_context"])
                self.assertEqual(packet[MONTHLY_NEWS_LABEL]["NEWS_RAW_1"]["snippet"],
                                 payload["evidence_map"]["NEWS_RAW_1"]["snippet"])
                self.assertNotIn("ablation_selection", json.dumps(packet))
                schema = request["text"]["format"]["schema"]
                self.assertEqual(set(schema["properties"]["overall_assessment"]["properties"]),
                                 {"summary", "primary_evidence_ids", "context_ids"})
        rules = "\n".join(baseline[2])
        for instruction in ("긍정·부정 근거에 같은 기준", "반대 근거가 없으면 만들어내지",
                            "확인된 긍정·부정 사건의 의미를 상쇄", "효과가 크거나 작다고 단정하지",
                            "어떤 재무·시장 관측 때문에", "관련 보조자료가 없으면 연결이나 판단 변화를 만들지"):
            self.assertIn(instruction, rules)
        for instruction in ("대상기업·지주회사·그룹·계열사", "주체를", "연결·별도 기준",
                            "기여를 설명", "기여 원인·규모를 만들지", "내부 검토 문구",
                            "수치 바로 앞에 기업명을", "한 기업의 연속 성장으로 합치지",
                            "그룹 실적을 direct로 분류하지"):
            self.assertIn(instruction, rules)


class EntityAttributionPromptTests(unittest.TestCase):
    def test_summary_preserves_subject_without_new_fields_or_company_specific_example(self):
        request = _build_llm_summary_request({"metadata": {}, "periods": []}, "gpt-5.4-mini")
        body = _load_llm_user_payload(request)
        rules = "\n".join(body["instructions"])
        for instruction in ("주체를 원문에 명시된 기업명", "연결·별도 기준", "기여를 사실 중심",
                            "주체가 불분명한 수치는 임의 귀속하지", "각 실적 문장", "제목에서 기업명이 축약"):
            self.assertIn(instruction, rules)
        self.assertNotIn("아모레", rules)
        self.assertNotIn("subject_entity", json.dumps(body["expected_output_schema"]))

    def test_strategy_and_both_writer_modes_preserve_attribution_and_contribution(self):
        strategy = (Path(__file__).resolve().parents[1] / "src/Agent_Team/Strategy_Agent/prompts/decision_agent.md").read_text()
        for prompt in (strategy, _editorial_system_prompt(writer_mode=FREE_FORM_WRITER_MODE),
                       _editorial_system_prompt(writer_mode=DETERMINISTIC_WRITER_MODE)):
            self.assertIn("대상기업·지주회사·그룹·계열사", prompt)
            self.assertIn("연결·별도 기준", prompt)
            self.assertIn("기여", prompt)
            self.assertIn("내부 검토 문구", prompt)
            self.assertNotIn("아모레", prompt)


class WriterSelectionTests(unittest.TestCase):
    def test_optional_citation_survives_schema_normalization_and_validation(self):
        handoff, raw = writer_fixture()
        key = next(iter(handoff["cards"]))
        component = "catalysts_execution"
        self.assertEqual(handoff["required_card_keys_by_component"][component], [])
        self.assertIn(key, handoff["available_card_keys_by_component"][component])
        item_key = next(iter(raw["sections"][component]))
        schema = _writer_report_schema(handoff)["properties"]["sections"]["properties"][component]["properties"][item_key]
        for selected in ([], [key]):
            payload = copy.deepcopy(raw)
            item = payload["sections"][component][item_key]
            item["card_keys"] = selected
            item["_claim_units"][0]["card_keys"] = selected
            Draft202012Validator(schema).validate(item)
            normalized = normalize_report_payload(payload, writer_handoff=handoff)
            self.assertEqual(normalized["sections"][component][item_key]["card_keys"], selected)
            self.assertEqual(_validate_card_key_coverage(normalized, handoff, []), "pass")
            self.assertEqual(_validate_claim_card_grounding(normalized, handoff, []), "pass")

    def test_unknown_citation_and_missing_core_are_rejected(self):
        handoff, raw = writer_fixture()
        component = "catalysts_execution"
        item_key = next(iter(raw["sections"][component]))
        item = raw["sections"][component][item_key]
        item["card_keys"] = ["invented"]
        schema = _writer_report_schema(handoff)["properties"]["sections"]["properties"][component]["properties"][item_key]
        with self.assertRaises(ValidationError):
            Draft202012Validator(schema).validate(item)
        self.assertEqual(_validate_card_key_coverage(raw, handoff, []), "fail")
        handoff, raw = writer_fixture()
        thesis = next(iter(raw["sections"]["investment_call_thesis"].values()))
        thesis["card_keys"] = []
        self.assertEqual(_validate_card_key_coverage(raw, handoff, []), "fail")


class AnalysisOrderTests(unittest.TestCase):
    def test_domain_conclusions_follow_analysis_and_secondary_context(self):
        financial = financial_analysis_json_schema(primary_evidence_ids=[], secondary_evidence_ids_by_domain={})["schema"]
        market = yfinance_agent_json_schema(primary_evidence_ids=[], secondary_evidence_ids_by_domain={})["schema"]
        for schema in (financial, market):
            keys = list(schema["properties"])
            self.assertEqual(keys[-1], "main_view")
            self.assertLess(keys.index("secondary_context_assessment_by_domain"), keys.index("main_view"))
            self.assertEqual(schema["required"], keys)

    def test_no_material_alternative_does_not_require_invented_citation(self):
        _, context, decision, _ = strategy_fixture()
        decision["strategy_brief"]["counterview"] = {"text": "제공된 자료에서는 별도의 대안 해석을 뒷받침할 근거가 확인되지 않는다.", "card_keys": []}
        schema = strategy_decision_response_format(context)["json_schema"]["schema"]
        Draft202012Validator(schema).validate(decision)
        validate_strategy_decision(decision, context=context)
        decision["strategy_brief"]["counterview"]["card_keys"] = ["invented"]
        with self.assertRaises(ValueError):
            validate_strategy_decision(decision, context=context)


class SummaryPreservationTests(unittest.TestCase):
    def test_summary_omits_only_coverage_and_selection_metadata_without_mutating_source(self):
        event = {"event_id": "e1", "title": "매출 증가에도 시장 예상 하회",
                 "snippet": "매출은 늘었으나 시장 예상을 밑돌았다.", "time": "2025-10-02",
                 "source": "검증언론", "mention_count": 2,
                 "event_timeline": [{"date": "2025-10-01", "title": "실적 발표"},
                                    {"date": "2025-10-02", "title": "후속 보도"}],
                 "coverage": {"article_count": 2, "publisher_names": ["검증언론", "다른언론"],
                              "primary_source_present": True, "coverage_quality": "verified"},
                 "relevance_rank": 1, "final_score": 0.9, "scores": {"dense": 0.9},
                 "ablation_selection": "full"}
        source = {"metadata": {"company": {"name": "검증기업"}, "granularity": "month"},
                  "periods": [{"period": "2025-10", "events": [event]}]}
        original = copy.deepcopy(source)
        omitted = {"coverage", "relevance_rank", "final_score", "scores", "ablation_selection", "mention_count"}
        expected = {key: value for key, value in event.items() if key not in omitted}
        for condition in ("full", "random"):
            with self.subTest(condition=condition):
                candidate = copy.deepcopy(source)
                candidate["periods"][0]["events"][0]["ablation_selection"] = condition
                request = _build_llm_summary_request(candidate, "gpt-5.4-mini")
                self.assertEqual(_load_llm_user_payload(request)["periods"][0]["events"], [expected])
                self.assertEqual(candidate["periods"][0]["events"][0]["coverage"], event["coverage"])
        self.assertEqual(source, original)

    def test_split_summary_keeps_lean_input_and_source_ids_including_empty_period(self):
        source = {"metadata": {"granularity": "month"}, "periods": [
            {"period": "2025-09", "events": []},
            {"period": "2025-10", "events": [{"event_id": "e1", "title": "검증 기사",
                                               "coverage": {"article_count": 1}}]},
        ]}
        request = _build_llm_summary_request(source, "gpt-5.4")
        split = _build_period_llm_requests(request)
        self.assertEqual(len(split), 2)
        for period, part in split:
            expected = next(p for p in _load_llm_user_payload(request)["periods"] if p["period"] == period)
            self.assertEqual(_load_llm_user_payload(part)["periods"], [expected])
            self.assertTrue(all("coverage" not in e for e in expected["events"]))
            output = {"periods": [{"period": period, "issues": [
                {"summary": "검증 요약", "source_event_ids": [e['event_id']]} for e in expected['events']]}]}
            _attach_source_event_ids(output, part)
            self.assertEqual(output["periods"][0]["source_event_ids"],
                             ["e1"] if expected["events"] else [])

    def test_independent_issues_and_longer_summary_keep_actual_sources(self):
        source = {"metadata": {"company": {"name": "검증기업"}, "granularity": "month"},
                  "periods": [{"period": "2025-10", "period_start": "2025-10-01", "period_end": "2025-10-31",
                               "events": [{"event_id": str(i), "title": f"사건 {i}"} for i in range(5)]}]}
        request = _build_llm_summary_request(source, "gpt-5.4-mini")
        summary = "독립된 사업 사건의 날짜와 진행 상태를 구분한다. " * 35
        output = {"periods": [{"period": "2025-10",
                               "issues": [{"summary": summary + f"독립 사건 {i}",
                                           "source_event_ids": [str(i)]} for i in range(5)]}]}
        _attach_source_event_ids(output, request)
        period = output["periods"][0]
        self.assertEqual(period['issues'][0]['summary'], summary + '독립 사건 0')
        self.assertEqual(len(period["issues"]), 5)
        self.assertEqual(period["source_event_ids"], [str(i) for i in range(5)])
        period["period"] = "unknown"
        with self.assertRaises(ValueError):
            _attach_source_event_ids(output, request)


class QualitativePeerTests(unittest.TestCase):
    def test_qualitative_comparison_reaches_writer_without_fake_metric(self):
        packet, context, decision, provenance = strategy_fixture()
        key = "peer.agent_analysis"
        card = copy.deepcopy(next(iter(packet["cards"].values())))
        card.update(card_key=key, domain="peer", card_type="agent_comparison", label="사업 진행 비교",
                    comparison_scope="selected_peer", evidence_family="selected_peer_analysis",
                    comparison_entities={"target_company": "검증기업", "peer_companies": ["비교기업"]},
                    primary_observation={"comparison_brief": "두 기업의 공급계약 진행 단계가 다르다.",
                                         "comparison_points": [{"finding": "검증기업은 공급 개시, 비교기업은 계약 발표 단계다.",
                                                                "target_implication": "검증기업의 실행 진척을 해석하는 비교 근거다."}]})
        packet["cards"][key] = card
        context["evidence_cards"][key] = card
        context["coverage_dimensions"]["peer"] = [key]
        provenance["cards"][key] = {"strategy_card_sha256": card_content_sha256(card),
                                    "source_evidence_ids": [], "source_paths": [], "source_files": []}
        decision["evidence_plan"]["decision_basis_cards"].append({"card_key": key, "importance": "medium",
            "investment_implication": "공급 개시는 실행 가능성에 관한 판단을 보강한다.", "target_peer_context": None})
        decision["strategy_brief"]["outlook"]["card_keys"].append(key)
        decision["evidence_plan"]["coverage_assessment"]["peer"] = {"status": "used", "card_keys": [key], "reason": "사업 진행 차이"}
        schema = strategy_decision_response_format(context)["json_schema"]["schema"]
        Draft202012Validator(schema).validate(decision)
        aligned = align_strategy_decision_evidence_plan(decision, context=context)
        validate_strategy_decision(aligned, context=context)
        handoff, _ = build_writer_editorial_packet(strategy_packet=packet, strategy_decision=aligned, strategy_provenance=provenance)
        self.assertIn(key, handoff["cards"])
        self.assertEqual(handoff["target_peer_context"], [])
        self.assertEqual(handoff["cards"][key]["primary_observation"], card["primary_observation"])
        self.assertIn("하위 분석", _reader_observation(card)["자료 유형"])
        bad = copy.deepcopy(aligned)
        bad["evidence_plan"]["decision_basis_cards"][-1]["target_peer_context"] = {"metric_keys": ["invented"]}
        with self.assertRaises(ValueError):
            validate_strategy_decision(bad, context=context)


if __name__ == "__main__":
    unittest.main()
