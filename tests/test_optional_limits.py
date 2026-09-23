"""Analytical limitations are optional; source scope and material caveats survive."""
import copy
import json
import unittest
from jsonschema import Draft202012Validator
from test_annual_context import strategy_fixture
from Agent_Team.Strategy_Agent.packet import _news_claim_card, _limitation_requirements
from Agent_Team.Strategy_Agent.decision import (
    validate_strategy_decision, strategy_decision_response_format,
    align_strategy_decision_evidence_plan,
)
from Agent_Team.Writer_Agent.writer_handoff import build_writer_editorial_packet, _reader_observation, _select_limitations, _writer_card
from Agent_Team.Writer_Agent.html_report_writer import normalize_report_payload, _writer_report_schema
from Agent_Team.Writer_Agent.html_report_spec import REPORT_SECTIONS
from Agent_Team.Writer_Agent.formatted_html_renderer import build_complete_html
from Agent_Team.Writer_Agent.html_report_validator import validate_html_report


def writer_fixture(limit="", refs=False):
    packet, context, decision, provenance = strategy_fixture()
    key = next(iter(packet["cards"]))
    decision["strategy_brief"]["decision_limitation"] = {"text": limit, "card_keys": [key] if refs else []}
    decision = align_strategy_decision_evidence_plan(decision, context=context)
    handoff, _ = build_writer_editorial_packet(strategy_packet=packet, strategy_decision=decision,
                                              strategy_provenance=provenance)
    sections = {}
    for section in REPORT_SECTIONS:
        name = section["key"]
        keys = handoff["required_card_keys_by_component"][name]
        text = "향후 12개월 실적 개선의 지속성을 검토한다."
        if name == "data_limits":
            text = "성장 해석은 판매 증가가 이어진다는 가정에 의존한다." if limit else ""
        sections[name] = {
            item: ({"paragraphs": [text] if text else [], "bullets": [], "card_keys": keys,
                    "_claim_units": [{"claim": text, "card_keys": keys, "limitation_categories": []}] if text else [],
                    **({"_limitation_categories": []} if name == "data_limits" else {})}
                   if kind == "text" else {"columns": [], "rows": [], "card_keys": keys})
            for item, _, kind in section["items"]}
    sections["key_evidence_table"]["evidence_table"]["_display_labels"] = [
        {"card_key": key, "display_label": "실적 성장"}]
    return handoff, {"sections": sections}


class OptionalLimitsTests(unittest.TestCase):
    def test_empty_limitation_valid_without_changing_opinion_or_confidence(self):
        for opinion in ("Buy", "Hold", "Sell"):
            _, context, decision, _ = strategy_fixture(opinion)
            decision["strategy_brief"]["decision_limitation"] = {"text": "", "card_keys": []}
            original = copy.deepcopy(decision)
            validate_strategy_decision(decision, context=context)
            schema = strategy_decision_response_format(context)["json_schema"]["schema"]
            Draft202012Validator(schema).validate(decision)
            self.assertEqual(decision, original)

    def test_empty_limitation_cannot_have_dangling_refs(self):
        _, context, decision, _ = strategy_fixture()
        decision["strategy_brief"]["decision_limitation"]["text"] = ""
        with self.assertRaisesRegex(ValueError, "Empty decision_limitation"):
            validate_strategy_decision(decision, context=context)

    def test_actual_missing_information_can_be_explained_without_inventing_ref(self):
        _, context, decision, _ = strategy_fixture()
        decision["strategy_brief"]["decision_limitation"]["card_keys"] = []
        validate_strategy_decision(decision, context=context)

    def news_card(self, summary=False):
        source = {"source_date": "2025-10-30", "snippet": "신제품 공급계약이 발표됐다.", "source_ref": "news.events.1"}
        if summary:
            source.update(metric="monthly_news_context", period="2025-10-01/2025-10-30")
        candidate = dict(claim="신제품 공급계약 발표", source_key="positive_signals", evidence_ids=["N1"],
                         evidence_use="strong", event_status="announced", company_specificity="direct",
                         materiality_status="observed", financial_link_status="not_observed", limitations=[])
        return _news_claim_card(candidate, {"N1": source})[0]

    def test_news_status_retained_without_automatic_caveat(self):
        card = self.news_card()
        self.assertEqual(card["primary_observation"]["financial_link_status"], "not_observed")
        self.assertNotIn("재무 기여", json.dumps(card.get("reader_limitations"), ensure_ascii=False))
        self.assertNotIn("재무적 영향", _reader_observation(card))
        requirements = _limitation_requirements({card["card_key"]: card}, {})
        self.assertNotIn("news_financial_link", [r["category"] for r in requirements])

    def test_summary_scope_retained_without_blanket_financial_disclaimer(self):
        card = self.news_card(summary=True)
        observation = _reader_observation(card)
        self.assertEqual(observation["자료 유형"], "월별 뉴스 요약")
        self.assertIn("2025-10-01/2025-10-30", observation["요약 기간"])
        self.assertNotIn("재무 기여", json.dumps(observation, ensure_ascii=False))
        self.assertIsNone(card["primary_observation"]["event_date"])

    def test_legacy_news_requirement_is_not_reactivated(self):
        card = self.news_card()
        key = card["card_key"]
        result = _select_limitations([{"category": "news_financial_link", "basis_card_keys": [key]}],
                                        selected_keys=[key], source_cards={key: card})
        self.assertEqual(result, [])

    def test_writer_keeps_news_source_status_without_rendering_caveat(self):
        source = self.news_card()
        card = _writer_card(source, {"investment_implication": "공급계약은 사업 확장의 근거다.", "relation_to_decision": "supports"},
                              evidence_tier="decision_basis")
        self.assertEqual(card["source_metadata"]["financial_link_status"], "not_observed")
        self.assertEqual(card["source_metadata"]["event_status"], "announced")
        self.assertEqual(card["source_metadata"]["event_date"], "2025-10-30")
        self.assertNotIn("재무적 영향", card["reader_observation"])

    def test_empty_writer_section_schema_and_complete_html_validation(self):
        handoff, raw = writer_fixture()
        schema = _writer_report_schema(handoff)
        node = schema["properties"]["sections"]["properties"]["data_limits"]["properties"]["section_analysis"]
        Draft202012Validator(node).validate(raw["sections"]["data_limits"]["section_analysis"])
        report = normalize_report_payload(raw, writer_handoff=handoff)
        html = build_complete_html(report)
        self.assertNotIn('id="data-limits"', html)
        self.assertNotIn("데이터 기준과 한계", html)
        result = validate_html_report(report_payload=report, html_content=html, writer_handoff=handoff)
        self.assertEqual(result["status"], "pass", result)
        self.assertEqual(result["section_h1_count"], "pass")

    def test_writer_paraphrase_is_not_followed_by_strategy_original(self):
        original = "성장 지속 여부가 최종 판단의 핵심 전제다."
        handoff, raw = writer_fixture(original, refs=True)
        report = normalize_report_payload(raw, writer_handoff=handoff)
        html = build_complete_html(report)
        self.assertIn("성장 해석은 판매 증가가 이어진다는 가정에 의존한다.", html)
        self.assertNotIn(original, html)
        result = validate_html_report(report_payload=report, html_content=html, writer_handoff=handoff)
        self.assertEqual(result["status"], "pass", result)

    def test_supplied_material_caveat_cannot_be_silently_omitted(self):
        handoff, raw = writer_fixture("핵심 가정에 대한 실질적 한계")
        raw["sections"]["data_limits"]["section_analysis"].update(paragraphs=[], _claim_units=[])
        report = normalize_report_payload(raw, writer_handoff=handoff)
        result = validate_html_report(report_payload=report, html_content=build_complete_html(report), writer_handoff=handoff)
        self.assertIn("required_limitation_coverage", result["blocking_failures"])

    def test_factual_scope_requirement_is_kept(self):
        packet, _, _, _ = strategy_fixture()
        key = next(iter(packet["cards"]))
        requirements = [{"category": "product_breakdown_scope", "basis_card_keys": [key], "facts": {"scope_label": "공시표 기준"}}]
        self.assertEqual(_select_limitations(requirements, selected_keys=[key], source_cards=packet["cards"]), requirements)

    def test_writer_authors_factual_scope_with_optional_strategy_limitation(self):
        handoff, raw = writer_fixture()
        key = next(iter(handoff["cards"]))
        handoff["required_limitations"] = [{"category": "product_breakdown_scope", "basis_card_keys": [key],
                                            "facts": {"scope_label": "주요 제품·서비스 공시표 기준"}}]
        handoff["required_card_keys_by_component"]["data_limits"] = [key]
        handoff["available_card_keys_by_component"]["data_limits"] = [key]
        text = "제품 매출 구성은 주요 제품·서비스 공시표 기준이다."
        item = {"paragraphs": [text], "bullets": [], "card_keys": [key],
                "_claim_units": [{"claim": text, "card_keys": [key], "limitation_categories": ["product_breakdown_scope"]}],
                "_limitation_categories": ["product_breakdown_scope"]}
        raw["sections"]["data_limits"]["section_analysis"] = item
        schema = _writer_report_schema(handoff)
        Draft202012Validator(schema["properties"]["sections"]["properties"]["data_limits"]["properties"]["section_analysis"]).validate(item)
        report = normalize_report_payload(raw, writer_handoff=handoff)
        html = build_complete_html(report)
        self.assertEqual(html.count(text), 1)
        result = validate_html_report(report_payload=report, html_content=html, writer_handoff=handoff)
        self.assertEqual(result["status"], "pass", result)


if __name__ == "__main__":
    unittest.main()
