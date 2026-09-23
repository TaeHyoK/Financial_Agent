"""Subdata can inform interpretation without changing facts or losing sources."""
import copy
import json
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "src"), str(ROOT / "src/Agent_Team/YFinance_Agent")]
from jsonschema import Draft202012Validator
from shared.subdata_guidance import (
    CONTEXT_USAGE, COMMON_GUIDANCE, context_issue_schema, flatten_context_issues, validate_context_refs,
)
from shared.evidence_contracts import SECONDARY_CONTEXT_EFFECTS, validate_secondary_context_assessments
from Agent_Team.Financial_Agent.financial_analysis_agent import build_financial_request
from Agent_Team.News_Agent.analysis_agent import build_llm_request
from Agent_Team.YFinance_Agent.reporting import build_market_request
from Agent_Team.Strategy_Agent.context_links import build_context_links
from Agent_Team.Strategy_Agent.packet import _card, _attach_secondary_context
from Agent_Team.Strategy_Agent.decision import build_strategy_context_package
from Agent_Team.Strategy_Agent.agent import sanitize_strategy_input_report
from shared.evidence_cards import assert_no_opaque_ids
from Agent_Team.Writer_Agent.writer_handoff import _reader_observation


def issue(number=1, **updates):
    return {
        "context_id": f"issue_{number}", "source_domain": "news", "effect": "corroborates",
        "usage": CONTEXT_USAGE, "statement": "공시 추세와 후속 실적 보도의 방향이 일치한다.",
        "judgment_impact": "실적 흐름의 지속성 해석을 보강한다.",
        "primary_evidence_ids": ["E001"], "secondary_evidence_ids": ["NEWS_PERIOD_1"],
        "limitation": "공시 확인 실적과 보도된 실적의 확정 수준은 구분한다.", **updates,
    }


def news_context():
    return {"status": "available", "input_type": "monthly_news_summaries", "evidence_catalog": {
        "NEWS_PERIOD_1": {"evidence_id": "NEWS_PERIOD_1", "domain": "news", "origin_type": "model_summarized",
                          "metric": "monthly_news_context", "source_ref": "news_periods.2025_11",
                          "period": "2025-10-06/2025-11-05", "source_date": "2025-11-05",
                          "text": "3분기 매출 1900억원, 영업이익 700억원이 보도됐다."}
    }}


class SubdataGuidanceTests(unittest.TestCase):
    def requests(self):
        financial_evidence = [{"evidence_id": f"E{i:03d}"} for i in range(1, 11)]
        financial = build_financial_request(
            {"strategy_handoff": {"key_evidence": financial_evidence}, "secondary_context": {"news": news_context()}},
            model="gpt-5.4")
        news = build_llm_request(input_payload={
            "target_entity": {"company_name": "검증기업"}, "input_policy": {}, "news_context": {},
            "secondary_context": {},
            "evidence_map": {"NEWS_RAW_1": {"domain": "news", "title": "실적 발표"}},
        }, model="gpt-5.4")
        market = build_market_request({
            "company_name": "검증기업", "market_summary": {"latest_snapshot": {"date": "2025-11-05"}},
            "primary_evidence_catalog": {"YF_CLOSE": {"value": 100}}, "secondary_context": {"news": news_context()},
        }, model="gpt-5.4")
        return financial, news, market

    def test_all_domains_share_guidance_and_no_obsolete_restriction(self):
        for request in self.requests():
            text = json.dumps(request["input"], ensure_ascii=False)
            self.assertIn(COMMON_GUIDANCE, request["input"][0]["content"])
            self.assertNotIn("framing_and_limitation_only", text)
            self.assertIn("판단을 기본값으로 삼지", text)
        self.assertIn("보도된 실적", self.requests()[1]["input"][0]["content"])

    def test_financial_market_issue_arrays_have_no_count_cap(self):
        for request in (self.requests()[0], self.requests()[2]):
            schema = request["text"]["format"]["schema"]["properties"]["secondary_context_assessment_by_domain"]["properties"]["news"]
            self.assertEqual(schema["type"], "array")
            self.assertNotIn("maxItems", schema)
            self.assertNotIn("maxItems", schema["items"]["properties"]["secondary_evidence_ids"])

    def test_many_issues_and_citations_preserved_without_directional_filter(self):
        rows = [issue(i) for i in range(20)]
        schema = context_issue_schema(domain="news", primary_ids=["E001"], secondary_ids=["NEWS_PERIOD_1"],
                                      effects=SECONDARY_CONTEXT_EFFECTS)
        Draft202012Validator(schema).validate(rows)
        actual = validate_secondary_context_assessments(
            rows, primary_evidence_ids=["E001"], secondary_catalog=news_context()["evidence_catalog"],
            allowed_source_domains={"news"})
        self.assertEqual(actual, rows)

    def test_unused_context_and_absent_context_are_valid(self):
        self.assertEqual(flatten_context_issues({"news": []}, ["news"]), [])
        self.assertEqual(flatten_context_issues({}, []), [])
        validate_context_refs({"context_ids": []}, [])
        with self.assertRaises(ValueError):
            flatten_context_issues({}, ["news"])

    def test_unknown_and_wrong_domain_refs_fail_not_prose_meaning(self):
        with self.assertRaises(ValueError):
            validate_context_refs({"context_ids": ["missing"]}, [issue()])
        with self.assertRaises(ValueError):
            flatten_context_issues({"news": [issue(source_domain="market")]}, ["news"])
        with self.assertRaises(ValueError):
            validate_secondary_context_assessments(
                [issue(secondary_evidence_ids=["unknown"])], primary_evidence_ids=["E001"],
                secondary_catalog=news_context()["evidence_catalog"], allowed_source_domains={"news"})

    def links_fixture(self, rows=None):
        financial = {"main_view": {"summary": "보도 실적을 구분하여 추세를 해석했다.", "context_ids": ["issue_1"]},
                     "secondary_context": {"news": news_context()},
                     "secondary_context_assessment": rows or [issue()]}
        bundle = {"target_reports": {"financial": financial}}
        primary = _card("financial.primary", domain="financial", card_type="trend", label="공시 추세",
                        allowed_sections=("financial_view",), evidence_family="financial",
                        observation_basis="period_comparison", observation={"revenue": 100})
        cards = {primary["card_key"]: primary}
        provenance = {primary["card_key"]: {"source_evidence_ids": ["E001"]}}
        def add(card, *, raw_ids, source_paths):
            cards[card["card_key"]] = card
            provenance[card["card_key"]] = {"source_evidence_ids": list(raw_ids), "source_paths": list(source_paths)}
        links = build_context_links(input_bundle=bundle, cards=cards, provenance=provenance, add_card=add,
                                    card_factory=_card, included_domains={"financial", "news", "yfinance"})
        return bundle, cards, provenance, links

    def test_links_keep_actual_sources_and_reuse_source_cards(self):
        bundle, cards, provenance, links = self.links_fixture([issue(1), issue(2)])
        self.assertEqual(len(cards), 2)
        first, second = links["financial"]["assessments"]
        self.assertEqual(first["primary_card_keys"], ["financial.primary"])
        self.assertEqual(first["secondary_card_keys"], second["secondary_card_keys"])
        self.assertTrue(first["used_in_domain_conclusion"])
        self.assertFalse(second["used_in_domain_conclusion"])
        source_key = first["secondary_card_keys"][0]
        self.assertEqual(provenance[source_key]["source_evidence_ids"], ["NEWS_PERIOD_1"])
        observation = cards[source_key]["primary_observation"]
        self.assertIn("1900", observation["event_summary"])
        self.assertEqual(observation["evidence_origin"], "model_summarized")
        self.assertEqual(observation["date_precision"], "period")
        self.assertEqual(cards["financial.primary"]["primary_observation"], {"revenue": 100})
        assert_no_opaque_ids({"cards": cards, "context_links": links})
        self.assertIn("1900", _reader_observation(cards[source_key])["자료 내용"])

    def test_missing_actual_source_does_not_attach_to_first_card(self):
        with self.assertRaisesRegex(ValueError, "Missing cited context source"):
            self.links_fixture([issue(primary_evidence_ids=["E999"])])
        bundle, cards, provenance, _ = self.links_fixture()
        before = copy.deepcopy(cards)
        _attach_secondary_context(cards, provenance, bundle["target_reports"])
        self.assertEqual(cards, before)

    def test_strategy_handoff_keeps_links_and_joint_summary(self):
        bundle, cards, _, links = self.links_fixture()
        packet = {"cards": cards, "target_company": {"company_name": "검증기업"}, "context_links": links}
        context = build_strategy_context_package(packet, input_bundle=bundle)
        handoff = context["domain_handoffs"]["financial"]
        self.assertEqual(handoff["cross_domain_assessments"], links["financial"]["assessments"])
        self.assertEqual(handoff["main_view"]["summary"], bundle["target_reports"]["financial"]["main_view"]["summary"])
        self.assertNotIn("context_ids", handoff["main_view"])

    def test_production_input_compaction_preserves_interpretation_and_source_scope(self):
        context = news_context()
        context["periods"] = ["2025-10-06/2025-11-05"]
        main = {"summary": "주 자료와 보조자료를 함께 해석한 결과", "context_ids": ["issue_1"]}
        for domain in ("financial", "news", "yfinance"):
            report = {"main_view": main, "overall_assessment": main,
                      "secondary_context": {"news": context},
                      "secondary_context_assessment": [issue()],
                      "context_policy_version": "context_interpretation_v3",
                      "analysis_metadata": {"context_policy_version": "context_interpretation_v3"},
                      "report_status": "obsolete"}
            if domain == "news":
                report = {"output": report}
            original = copy.deepcopy(report)
            cleaned = sanitize_strategy_input_report(report, domain)
            actual = cleaned["output"] if domain == "news" else cleaned
            self.assertEqual(actual["secondary_context"]["news"], context)
            self.assertEqual(actual["secondary_context_assessment"], [issue()])
            self.assertEqual(actual["overall_assessment" if domain == "news" else "main_view"], main)
            self.assertNotIn("report_status", actual)
            self.assertEqual(report, original)

    def test_context_source_card_preserves_recipient_period_metadata(self):
        bundle, cards, provenance, _ = self.links_fixture()
        bundle["target_reports"]["financial"]["secondary_context"]["news"]["periods"] = ["2025-10-06/2025-11-05"]
        # Rebuild without the already-created source card so metadata is read anew.
        cards = {"financial.primary": cards["financial.primary"]}
        provenance = {"financial.primary": provenance["financial.primary"]}
        def add(card, *, raw_ids, source_paths):
            cards[card["card_key"]] = card
            provenance[card["card_key"]] = {"source_evidence_ids": raw_ids}
        links = build_context_links(input_bundle=bundle, cards=cards, provenance=provenance,
                                    add_card=add, card_factory=_card,
                                    included_domains={"financial", "news", "yfinance"})
        key = links["financial"]["assessments"][0]["secondary_card_keys"][0]
        self.assertEqual(cards[key]["primary_observation"]["source_scope"]["periods"],
                         ["2025-10-06/2025-11-05"])


if __name__ == "__main__":
    unittest.main()
