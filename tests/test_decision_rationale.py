"""Rationale evidence survives alignment and reaches the report introduction."""
import copy
import unittest
from jsonschema import Draft202012Validator
from test_annual_context import strategy_fixture
from test_optional_limits import writer_fixture
from shared.evidence_cards import card_content_sha256
from Agent_Team.Strategy_Agent.contracts_v5 import (
    SCHEMA_REVISION, align_strategy_decision_v5_evidence_plan,
    validate_strategy_decision_v5, strategy_decision_response_format_v5,
)
from Agent_Team.Strategy_Agent.agent import build_strategy_report_projection_v5, render_strategy_projection_markdown_v5
from writer_handoff import build_writer_editorial_packet, validate_writer_editorial_packet
from html_report_writer import _build_context, normalize_report_payload
from formatted_html_renderer import build_complete_html
from html_report_validator import validate_html_report


class DecisionRationaleTests(unittest.TestCase):
    def test_new_schema_requires_concise_grounded_rationale(self):
        _, context, decision, _ = strategy_fixture()
        schema = strategy_decision_response_format_v5(context)["json_schema"]["schema"]
        Draft202012Validator(schema).validate(decision)
        self.assertEqual(decision["schema_revision"], SCHEMA_REVISION)
        for invalid in (None, {"text": "", "card_keys": []}, {"text": "説明", "card_keys": []}):
            bad = copy.deepcopy(decision)
            if invalid is None:
                bad["strategy_brief"].pop("decision_rationale")
            else:
                bad["strategy_brief"]["decision_rationale"] = invalid
            with self.assertRaises(ValueError):
                validate_strategy_decision_v5(bad, context=context)

    def test_old_cached_revision_requires_regeneration(self):
        _, context, decision, _ = strategy_fixture()
        decision["schema_revision"] = "12m_v1"
        with self.assertRaisesRegex(ValueError, "regenerate"):
            validate_strategy_decision_v5(decision, context=context)

    def test_no_direction_or_importance_rewriting(self):
        for opinion in ("Buy", "Hold", "Sell"):
            _, context, decision, _ = strategy_fixture(opinion)
            original = copy.deepcopy(decision)
            aligned = align_strategy_decision_v5_evidence_plan(decision, context=context)
            validate_strategy_decision_v5(aligned, context=context)
            self.assertEqual(aligned["strategy_brief"], original["strategy_brief"])
            self.assertEqual(aligned["evidence_plan"]["decision_basis_cards"], original["evidence_plan"]["decision_basis_cards"])

    def test_rationale_only_sources_preserved_without_cap(self):
        packet, context, decision, provenance = strategy_fixture()
        original = copy.deepcopy(next(iter(packet["cards"].values())))
        extra_keys = [f"financial.observation_{i}" for i in range(12)]
        for key in extra_keys:
            card = {**copy.deepcopy(original), "card_key": key, "label": "추가 관측"}
            packet["cards"][key] = card
            context["evidence_cards"][key] = card
            provenance["cards"][key] = {"strategy_card_sha256": card_content_sha256(card), "source_evidence_ids": [],
                                          "source_paths": [], "source_files": []}
        rationale = {"text": "성장의 지속성과 부담 요인의 영향 범위를 비교한 선택 이유다.", "card_keys": extra_keys}
        decision["strategy_brief"]["decision_rationale"] = rationale
        aligned = align_strategy_decision_v5_evidence_plan(decision, context=context)
        validate_strategy_decision_v5(aligned, context=context)
        self.assertEqual({r["card_key"] for r in aligned["evidence_plan"]["report_context_cards"]}, set(extra_keys))
        handoff, _ = build_writer_editorial_packet(strategy_packet=packet, strategy_decision=aligned, strategy_provenance=provenance)
        self.assertEqual(handoff["recommendation_bridge"]["decision_rationale"], rationale["text"])
        self.assertEqual(handoff["recommendation_bridge"]["decision_rationale_card_keys"], extra_keys)
        self.assertTrue(set(extra_keys) <= set(handoff["required_card_keys_by_component"]["investment_call_thesis"]))

    def test_unknown_source_is_not_silently_dropped(self):
        _, context, decision, _ = strategy_fixture()
        decision["strategy_brief"]["decision_rationale"]["card_keys"] = ["financial.unknown"]
        with self.assertRaisesRegex(ValueError, "unknown card"):
            align_strategy_decision_v5_evidence_plan(decision, context=context)

    def test_writer_requires_actual_rationale_handoff(self):
        handoff, _ = writer_fixture()
        context = _build_context(writer_handoff=handoff)
        self.assertIn("decision_rationale", context["writing_rules"]["thesis_policy"])
        for field in ("decision_rationale", "decision_rationale_card_keys"):
            bad = copy.deepcopy(handoff)
            bad["recommendation_bridge"].pop(field)
            with self.assertRaises(ValueError):
                validate_writer_editorial_packet(bad)

    def test_projection_and_markdown_preserve_rationale(self):
        packet, context, decision, _ = strategy_fixture()
        projection = build_strategy_report_projection_v5(decision, input_bundle={"target_company":packet["target_company"]}, context=context)
        self.assertEqual(projection["strategy_brief"]["decision_rationale"], decision["strategy_brief"]["decision_rationale"])
        self.assertIn(decision["strategy_brief"]["decision_rationale"]["text"], render_strategy_projection_markdown_v5(projection))

    def test_report_paraphrase_uses_existing_intro_not_new_section(self):
        handoff, raw = writer_fixture()
        item = raw["sections"]["investment_call_thesis"]["section_analysis"]
        text = "12개월 관점에서 성장의 지속성을 우선 보되, 반대 근거의 적용 범위를 고려해 현재 의견을 선택한다."
        item["paragraphs"] = [text]
        item["_claim_units"] = [{"claim": text, "card_keys": item["card_keys"], "limitation_categories": []}]
        report = normalize_report_payload(raw, writer_handoff=handoff)
        html = build_complete_html(report)
        self.assertIn(text, html)
        self.assertNotIn(handoff["recommendation_bridge"]["decision_rationale"], html)
        result = validate_html_report(report_payload=report, html_content=html, writer_handoff=handoff)
        self.assertEqual(result["status"], "pass", result)


if __name__ == "__main__":
    unittest.main()
