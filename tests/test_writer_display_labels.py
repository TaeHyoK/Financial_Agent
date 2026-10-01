"""Reader-visible English grade labels and internal field names stay out of reports."""

import copy
import json
from pathlib import Path
import tempfile
import unittest

from test_annual_context import strategy_fixture
from test_optional_limits import writer_fixture
from Agent_Team.Strategy_Agent.decision import align_strategy_decision_evidence_plan
from Agent_Team.Writer_Agent.writer_handoff import build_writer_editorial_packet
from Agent_Team.Writer_Agent.html_report_writer import (
    MISSING_VALUE,
    _structured_observation_text,
    normalize_report_payload,
)
from Agent_Team.Writer_Agent.html_report_spec import (
    reader_label_leaks,
    replace_english_grade_labels,
)
from Agent_Team.Writer_Agent.formatted_html_renderer import (
    _visible_text,
    build_complete_html,
    render_formatted_html_report,
)
from Agent_Team.Writer_Agent.html_report_validator import validate_html_report


INTERPRETATION = "반등을 보여줘 Sell을 피하게 했지만, 근거가 부족해 Hold 쪽으로 균형을 이동시켰다."
RISK_IMPACT = "가격 매력이 약해 Buy 강도를 낮춘다."


def graded_writer_fixture():
    packet, context, decision, provenance = strategy_fixture("Hold")
    decision["strategy_brief"]["headline"] = "실적 반등에도 Hold 유지"
    decision["evidence_plan"]["decision_basis_cards"][0]["investment_implication"] = INTERPRETATION
    decision["key_risks"][0]["current_implication"] = RISK_IMPACT
    decision = align_strategy_decision_evidence_plan(decision, context=context)
    handoff, _ = build_writer_editorial_packet(
        strategy_packet=packet, strategy_decision=decision, strategy_provenance=provenance
    )
    _, raw = writer_fixture()
    return handoff, raw


class EnglishGradeLabelTests(unittest.TestCase):
    def test_whole_word_grades_are_replaced_with_agreeing_particles(self):
        self.assertEqual(
            replace_english_grade_labels(INTERPRETATION),
            "반등을 보여줘 매도를 피하게 했지만, 근거가 부족해 중립 쪽으로 균형을 이동시켰다.",
        )
        self.assertEqual(replace_english_grade_labels("BUY 또는 SELL"), "매수 또는 매도")
        self.assertEqual(replace_english_grade_labels("Hold를 유지"), "중립을 유지")

    def test_grade_inside_english_word_is_not_replaced(self):
        text = "Holdings의 Buyback과 Seller 계약, HOLDCO"
        self.assertEqual(replace_english_grade_labels(text), text)

    def test_evidence_risk_cells_and_headline_use_korean_grades(self):
        handoff, raw = graded_writer_fixture()
        report = normalize_report_payload(raw, writer_handoff=handoff)
        self.assertEqual(report["metadata"]["report_title"], "실적 반등에도 중립 유지")
        self.assertEqual(report["metadata"]["recommendation"], "Hold")
        evidence_row = report["sections"]["key_evidence_table"]["evidence_table"]["rows"][0]
        self.assertIn("매도를 피하게", evidence_row["투자 판단에 미치는 의미"])
        self.assertIn("중립 쪽으로", evidence_row["투자 판단에 미치는 의미"])
        risk_row = report["sections"]["risk_monitoring_matrix"]["risk_monitoring_table"]["rows"][0]
        self.assertEqual(risk_row["투자 판단에 미치는 영향"], "가격 매력이 약해 매수 강도를 낮춘다.")

        html = build_complete_html(report)
        self.assertEqual(reader_label_leaks(_visible_text(html)), [])
        self.assertIn('name="investment-recommendation" content="Hold"', html)
        result = validate_html_report(report_payload=report, html_content=html, writer_handoff=handoff)
        self.assertNotIn("paraphrased", json.dumps(result, ensure_ascii=False))

    def test_render_safety_net_covers_model_prose(self):
        handoff, raw = writer_fixture()
        report = normalize_report_payload(raw, writer_handoff=handoff)
        report["sections"]["investment_call_thesis"]["section_analysis"]["paragraphs"] = [
            "향후 12개월 기준 Hold 의견을 유지한다."
        ]
        html = build_complete_html(report)
        self.assertIn("<p>향후 12개월 기준 중립 의견을 유지한다.</p>", html)


class ObservationFallbackTests(unittest.TestCase):
    PROVIDER_OBSERVATION = {
        "valuation_date": "2025-09-30",
        "metrics": {
            "market_cap": {"value": 6120000000000.0, "unit": "KRW", "status": "ok"},
            "trailing_pe": {"unit": "times", "status": "unavailable"},
            "price_to_book": {"value": 0.75, "unit": "times", "status": "ok"},
        },
        "date_policy": "valuation_period_before_selected_date",
    }

    def test_metric_fallback_shows_labels_not_field_names(self):
        text = _structured_observation_text(copy.deepcopy(self.PROVIDER_OBSERVATION))
        self.assertEqual(text, "기준일: 2025-09-30\n시가총액: 61,200.0억원 · P/B: 0.75배")
        self.assertEqual(reader_label_leaks(text), [])

    def test_prose_observation_is_preferred_over_cited_sources(self):
        observation = {
            "observation": "영업현금흐름 유출 폭이 확대됐다.",
            "interpretation": "현금 전환이 약하다.",
            "source_domains": ["financial", "news"],
            "cited_sources": [{"origin_type": "raw_source", "source_date": "2025-05-28"}],
        }
        self.assertEqual(_structured_observation_text(observation), "영업현금흐름 유출 폭이 확대됐다.")

    def test_reader_labels_are_kept_and_internal_only_dict_is_missing(self):
        self.assertEqual(
            _structured_observation_text({"기준일": "2025-06-30", "EPS": "1,000원"}),
            "기준일: 2025-06-30\nEPS: 1,000원",
        )
        self.assertEqual(
            _structured_observation_text({"origin_type": "raw_source", "status": "ok"}),
            MISSING_VALUE,
        )

    def test_render_warns_when_internal_names_remain(self):
        handoff, raw = writer_fixture()
        report = normalize_report_payload(raw, writer_handoff=handoff)
        report["sections"]["investment_call_thesis"]["section_analysis"]["paragraphs"] = [
            "origin_type: raw_source"
        ]
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertLogs("Agent_Team.Writer_Agent.formatted_html_renderer", "WARNING") as logs:
                render_formatted_html_report(report, Path(tmp))
        self.assertIn("origin_type", logs.output[0])


if __name__ == "__main__":
    unittest.main()
