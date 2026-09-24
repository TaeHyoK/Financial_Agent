"""Analysis-first layout and narrative evidence, without semantic gating."""
import copy
import unittest
from jsonschema import Draft202012Validator
from test_annual_context import strategy_fixture
from test_optional_limits import writer_fixture
from Agent_Team.Strategy_Agent.agent import build_strategy_report_projection
from Agent_Team.Strategy_Agent.context import build_base_strategy_context
from Agent_Team.Strategy_Agent.decision import (
    build_strategy_context_package, strategy_decision_response_format,
    validate_strategy_decision, align_strategy_decision_evidence_plan,
)
from Agent_Team.Writer_Agent.writer_handoff import build_writer_editorial_packet
from Agent_Team.Writer_Agent.html_report_writer import normalize_report_payload, _writer_report_schema, _output_contract
from Agent_Team.Writer_Agent.formatted_html_renderer import build_complete_html
from Agent_Team.Writer_Agent.html_report_validator import validate_html_report


class AnalysisFirstTests(unittest.TestCase):
    def test_observations_and_interpretations_are_unchanged(self):
        packet, _, _, _ = strategy_fixture()
        bundle = {"target_reports": {}}
        old = build_base_strategy_context(packet, input_bundle=bundle)
        new = build_strategy_context_package(packet, input_bundle=bundle)
        self.assertEqual(new["evidence_cards"], old["evidence_cards"])
        self.assertEqual(new["domain_handoffs"], old["domain_handoffs"])
        self.assertLess(list(new).index("evidence_cards"), list(new).index("domain_handoffs"))
        self.assertEqual(set(new["input_roles"]), {"evidence_cards", "domain_handoffs", "applicability_notes"})

    def test_notes_group_by_explicit_key_only_without_semantic_deletion(self):
        packet, _, _, _ = strategy_fixture()
        key = next(iter(packet["cards"]))
        scoped = {"basis_card_key": key, "text": "동일 누적 기간끼리 비교한다."}
        shared = {"basis_card_key": "", "text": "확인되지 않은 사건의 영향을 단정하지 않는다."}
        unresolved = {"basis_card_key": "financial.not_available", "text": "원래 참조가 없어도 주의사항을 보존한다."}
        packet["reader_limitations"] = [scoped, shared, scoped, unresolved, shared]
        before = copy.deepcopy(packet)
        new = build_strategy_context_package(packet, input_bundle={"target_reports": {}})
        self.assertEqual(new["applicability_notes"], {"by_card": {key: [scoped]}, "shared": [shared, unresolved]})
        self.assertNotIn("data_limitations", new)
        self.assertEqual(packet, before)

    def test_schema_produces_analysis_before_opinion_and_evidence_index(self):
        _, context, decision, _ = strategy_fixture()
        schema = strategy_decision_response_format(context)["json_schema"]["schema"]
        Draft202012Validator(schema).validate(decision)
        self.assertLess(list(schema["properties"]).index("strategy_brief"), list(schema["properties"]).index("evidence_plan"))
        keys = list(schema["properties"]["strategy_brief"]["properties"])
        self.assertLess(keys.index("earnings_review"), keys.index("outlook"))
        self.assertLess(keys.index("price_assessment"), keys.index("decision_rationale"))
        self.assertLess(keys.index("decision_rationale"), keys.index("recommendation"))

    def test_projection_retains_scoped_notes(self):
        packet, context, decision, _ = strategy_fixture()
        notes = {"shared": [{"text": "기간 비교 범위를 보존한다."}], "by_card": {}}
        context["applicability_notes"] = notes
        projection = build_strategy_report_projection(decision, input_bundle={"target_company": packet["target_company"]}, context=context)
        self.assertEqual(projection["applicability_notes"], notes)

    def test_no_relation_classifier_in_contract_or_handoff(self):
        for opinion in ("Buy", "Hold", "Sell"):
            packet, context, decision, provenance = strategy_fixture(opinion)
            before = copy.deepcopy(decision)
            decision = align_strategy_decision_evidence_plan(decision, context=context)
            validate_strategy_decision(decision, context=context)
            handoff, _ = build_writer_editorial_packet(strategy_packet=packet, strategy_decision=decision, strategy_provenance=provenance)
            self.assertEqual(handoff["decision"]["opinion"], opinion)
            self.assertEqual(decision["strategy_brief"], before["strategy_brief"])
            self.assertTrue(all("strategy_role" not in card for card in handoff["cards"].values()))
            bad = copy.deepcopy(decision)
            bad["evidence_plan"]["decision_basis_cards"][0]["relation_to_decision"] = "supports"
            with self.assertRaisesRegex(ValueError, "relation_to_decision"):
                validate_strategy_decision(bad, context=context)

    def test_three_column_table_preserves_model_interpretation_and_validates(self):
        handoff, raw = writer_fixture()
        report = normalize_report_payload(raw, writer_handoff=handoff)
        item = report["sections"]["key_evidence_table"]["evidence_table"]
        self.assertEqual(item["columns"], ["핵심 근거", "확인된 수치·사실", "투자 판단에 미치는 의미"])
        for row in item["rows"]:
            self.assertNotIn("판단상 역할", row)
            self.assertNotIn("_strategy_role", row)
            self.assertEqual(row["투자 판단에 미치는 의미"], handoff["cards"][row["_card_key"]]["strategy_interpretation"])
        html = build_complete_html(report)
        self.assertNotIn("판단상 역할", html)
        validation = validate_html_report(report_payload=report, html_content=html, writer_handoff=handoff)
        self.assertEqual(validation["blocking_failures"], [])

    def test_free_form_contract_has_no_role_field(self):
        handoff, _ = writer_fixture()
        schema = _writer_report_schema(handoff, writer_mode="free_form")
        fields = schema["properties"]["sections"]["properties"]["key_evidence_table"]["properties"]["evidence_table"]["properties"]["rows"]["items"]["properties"]
        self.assertIn("투자 판단에 미치는 의미", fields)
        self.assertNotIn("판단상 역할", fields)
        self.assertNotIn("_strategy_role", fields)
        contract = _output_contract(handoff, writer_mode="free_form")
        row = contract["sections"]["key_evidence_table"]["evidence_table"]["rows"][0]
        self.assertNotIn("_strategy_role", row)


if __name__ == "__main__":
    unittest.main()
