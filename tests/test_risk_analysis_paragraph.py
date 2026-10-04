"""The risk section carries Writer prose above the deterministic risk table."""

from __future__ import annotations

import copy
from pathlib import Path
import tempfile
import unittest

from jsonschema import Draft202012Validator

from shared.evidence_cards import card_content_sha256
from test_annual_context import strategy_fixture
from Agent_Team.Strategy_Agent.decision import align_strategy_decision_evidence_plan
from Agent_Team.Writer_Agent.formatted_html_renderer import build_complete_html
from Agent_Team.Writer_Agent.html_report_spec import REPORT_SECTIONS, RISK_DISPLAY_COLUMNS
from Agent_Team.Writer_Agent.html_report_validator import validate_html_report
from Agent_Team.Writer_Agent.html_report_writer import (
    _evidence_display_columns,
    _output_contract,
    _writer_report_schema,
    normalize_report_payload,
)
from Agent_Team.Writer_Agent.writer_handoff import build_writer_editorial_packet
from real_report_evaluation.extract import extract_html_body


RISK_CLAIM = "가장 큰 위험은 매출 성장의 지속성이며, 성장 지속을 전제로 한 해석의 강도를 낮춘다."
SWITCH_CLAIM = "성장세가 다음 분기에도 확인되면 현재 의견을 다시 검토할 근거가 생긴다."
LIMIT_KEY = "valuation.selected_date"


def risk_handoff(*, risks: bool = True, limitation: str = "성장세가 이어지면 의견이 바뀔 수 있다."):
    """Build a Writer packet whose decision limitation cites a card no risk uses."""

    packet, context, decision, provenance = strategy_fixture()
    limit_card = {
        "card_key": LIMIT_KEY, "domain": "valuation", "card_type": "valuation", "label": "가치평가",
        "primary_observation": {"trailing_pe": 10.0}, "evidence_family": "valuation",
        "observation_basis": "point_in_time", "comparison_scope": "none",
        "decision_use": "primary", "reader_limitations": [],
    }
    packet["cards"][LIMIT_KEY] = limit_card
    context["evidence_cards"][LIMIT_KEY] = limit_card
    context["coverage_dimensions"]["valuation"] = [LIMIT_KEY]
    decision["evidence_plan"]["report_context_cards"] = [
        {"card_key": LIMIT_KEY, "importance": "medium",
         "investment_implication": "가치평가는 판단의 전환 조건을 설명한다."}
    ]
    decision["evidence_plan"]["coverage_assessment"]["valuation"] = {
        "status": "used", "card_keys": [LIMIT_KEY], "reason": "제공된 자료의 범위다."}
    decision["strategy_brief"]["decision_limitation"] = {
        "text": limitation, "card_keys": [LIMIT_KEY] if limitation else []}
    if not risks:
        decision["key_risks"] = []
    provenance["cards"][LIMIT_KEY] = {
        "strategy_card_sha256": card_content_sha256(limit_card), "source_evidence_ids": [],
        "source_paths": [], "source_files": []}
    decision = align_strategy_decision_evidence_plan(decision, context=context)
    handoff, _ = build_writer_editorial_packet(
        strategy_packet=packet, strategy_decision=decision, strategy_provenance=provenance)
    return handoff


def fake_writer_response(handoff, *, risk_paragraphs=None, risk_units=None):
    """Return an offline Writer response that fills every text item it is asked for."""

    sections = {}
    for section in REPORT_SECTIONS:
        name = section["key"]
        keys = handoff["required_card_keys_by_component"][name]
        text = "향후 12개월 실적 개선의 지속성을 검토한다."
        if name == "data_limits":
            text = "이번 판단은 제공된 분기 자료 범위에 한정된다."
        items = {}
        for item, _title, kind in section["items"]:
            if kind == "table":
                columns = (list(RISK_DISPLAY_COLUMNS) if name == "risk_monitoring_matrix"
                           else list(_evidence_display_columns(handoff)))
                items[item] = {"columns": columns, "rows": [], "card_keys": keys}
                continue
            unit_keys = keys
            if name == "data_limits":
                unit_keys = handoff["required_card_keys_by_component"]["data_limits"]
            items[item] = {"paragraphs": [text], "bullets": [], "card_keys": unit_keys,
                           "_claim_units": [{"claim": text, "card_keys": unit_keys,
                                             "limitation_categories": []}]}
            if name == "data_limits":
                items[item]["_limitation_categories"] = []
        sections[name] = items
    risk_keys = handoff["required_card_keys_by_component"]["risk_monitoring_matrix"]
    switch_keys = handoff["recommendation_bridge"]["residual_uncertainty_card_keys"]
    units = risk_units if risk_units is not None else [
        {"claim": RISK_CLAIM, "card_keys": risk_keys, "limitation_categories": []},
        {"claim": SWITCH_CLAIM, "card_keys": switch_keys, "limitation_categories": []},
    ]
    sections["risk_monitoring_matrix"]["section_analysis"] = {
        "paragraphs": risk_paragraphs if risk_paragraphs is not None else [RISK_CLAIM, SWITCH_CLAIM],
        "bullets": [],
        "card_keys": list(dict.fromkeys(key for unit in units for key in unit["card_keys"])),
        "_claim_units": units,
    }
    sections["key_evidence_table"]["evidence_table"]["_display_labels"] = [
        {"card_key": key, "display_label": "실적 성장"}
        for key in handoff["required_card_keys_by_component"]["key_evidence_table"]
    ]
    return {"metadata": {"report_title": "검증 보고서"}, "sections": sections}


class RiskAnalysisParagraphTests(unittest.TestCase):
    def test_spec_places_text_item_before_risk_table(self):
        risk = next(s for s in REPORT_SECTIONS if s["key"] == "risk_monitoring_matrix")
        self.assertEqual([(key, kind) for key, _title, kind in risk["items"]],
                         [("section_analysis", "text"), ("risk_monitoring_table", "table")])

    def test_risk_paragraph_may_cite_decision_limitation_basis(self):
        handoff = risk_handoff()
        risk_keys = handoff["required_card_keys_by_component"]["risk_monitoring_matrix"]
        available = handoff["available_card_keys_by_component"]["risk_monitoring_matrix"]
        self.assertEqual(risk_keys, ["financial.same_period_trend"])
        self.assertEqual(available, ["financial.same_period_trend", LIMIT_KEY])
        schema = _writer_report_schema(handoff)
        risk_schema = schema["properties"]["sections"]["properties"]["risk_monitoring_matrix"]["properties"]
        table_keys = risk_schema["risk_monitoring_table"]["properties"]["card_keys"]
        self.assertEqual(table_keys["items"]["enum"], risk_keys)
        self.assertEqual(table_keys["minItems"], len(risk_keys))
        text_keys = risk_schema["section_analysis"]["properties"]["card_keys"]
        self.assertEqual(text_keys["items"]["enum"], available)
        self.assertEqual(text_keys["minItems"], 1)
        contract = _output_contract(handoff)["sections"]["risk_monitoring_matrix"]["section_analysis"]
        self.assertEqual(len(contract["paragraphs"]), 2)
        self.assertEqual(contract["card_keys"], available)

    def test_rendered_paragraph_precedes_table_and_validates(self):
        handoff = risk_handoff()
        raw = fake_writer_response(handoff)
        Draft202012Validator(_writer_report_schema(handoff)).validate(raw)
        report = normalize_report_payload(raw, writer_handoff=handoff)
        html = build_complete_html(report)
        section_html = html[html.index('id="risk-monitoring-matrix"'):]
        section_html = section_html[:section_html.index("</section>")]
        self.assertIn('id="risk-monitoring-matrix-section-analysis"', section_html)
        self.assertLess(section_html.index(RISK_CLAIM), section_html.index("<table"))
        self.assertLess(section_html.index(SWITCH_CLAIM), section_html.index("<table"))
        result = validate_html_report(report_payload=report, html_content=html, writer_handoff=handoff)
        self.assertEqual(result["status"], "pass", result)
        self.assertEqual(result["claim_card_grounding"], "pass")
        self.assertEqual(result["claim_visibility"], "pass")
        self.assertEqual(result["compact_text_sections"], "pass")
        self.assertEqual(result["card_key_coverage"], "pass")

    def test_risk_paragraph_is_part_of_claim_grounding_and_visibility(self):
        handoff = risk_handoff()
        risk_keys = handoff["required_card_keys_by_component"]["risk_monitoring_matrix"]
        ungrounded = fake_writer_response(handoff)
        ungrounded["sections"]["risk_monitoring_matrix"]["section_analysis"]["_claim_units"][1]["card_keys"] = []
        report = normalize_report_payload(ungrounded, writer_handoff=handoff)
        result = validate_html_report(report_payload=report, html_content=build_complete_html(report),
                                      writer_handoff=handoff)
        self.assertIn("claim_card_grounding", result["blocking_failures"])
        self.assertTrue(any("risk_monitoring_matrix.section_analysis claim card coverage" in note
                            for note in result["notes"]))

        unmatched = fake_writer_response(
            handoff,
            risk_paragraphs=[RISK_CLAIM, "표에 없는 설명 문단이다."],
            risk_units=[{"claim": RISK_CLAIM, "card_keys": risk_keys, "limitation_categories": []}],
        )
        report = normalize_report_payload(unmatched, writer_handoff=handoff)
        result = validate_html_report(report_payload=report, html_content=build_complete_html(report),
                                      writer_handoff=handoff)
        self.assertEqual(result["claim_visibility"], "warning")
        self.assertTrue(any("risk_monitoring_matrix.section_analysis paragraph 1" in note
                            for note in result["advisories"]))

        missing = fake_writer_response(handoff, risk_paragraphs=[], risk_units=[])
        report = normalize_report_payload(missing, writer_handoff=handoff)
        result = validate_html_report(report_payload=report, html_content=build_complete_html(report),
                                      writer_handoff=handoff)
        self.assertIn("claim_card_grounding", result["blocking_failures"])

    def test_judge_text_extraction_includes_risk_paragraph(self):
        handoff = risk_handoff()
        report = normalize_report_payload(fake_writer_response(handoff), writer_handoff=handoff)
        html = build_complete_html(report)
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "report.html"
            path.write_text(html, encoding="utf-8")
            body = extract_html_body(path)
        self.assertIn(RISK_CLAIM, body)
        self.assertIn(SWITCH_CLAIM, body)
        # Table rows stay out of the judge text; only the prose carries the risk.
        self.assertNotIn("매출 성장의 지속성이 불확실하다.", body)

    def test_paragraph_is_optional_without_risks_or_switch_conditions(self):
        handoff = risk_handoff(risks=False, limitation="")
        schema = _writer_report_schema(handoff)
        text_schema = schema["properties"]["sections"]["properties"]["risk_monitoring_matrix"][
            "properties"]["section_analysis"]["properties"]
        self.assertEqual(text_schema["paragraphs"]["minItems"], 0)
        raw = fake_writer_response(handoff, risk_paragraphs=[], risk_units=[])
        Draft202012Validator(schema).validate(raw)
        report = normalize_report_payload(raw, writer_handoff=handoff)
        html = build_complete_html(report)
        self.assertNotIn('id="risk-monitoring-matrix-section-analysis"', html)
        result = validate_html_report(report_payload=report, html_content=html, writer_handoff=handoff)
        self.assertEqual(result["status"], "pass", result)
        self.assertEqual(result["compact_text_sections"], "pass")

    def test_saved_payload_without_risk_paragraph_renders_table_alone(self):
        handoff = risk_handoff()
        report = normalize_report_payload(fake_writer_response(handoff), writer_handoff=handoff)
        legacy = copy.deepcopy(report)
        legacy["sections"]["risk_monitoring_matrix"].pop("section_analysis")
        html = build_complete_html(legacy)
        self.assertNotIn('id="risk-monitoring-matrix-section-analysis"', html)
        self.assertIn('id="risk-monitoring-matrix-risk-monitoring-table"', html)


if __name__ == "__main__":
    unittest.main()
