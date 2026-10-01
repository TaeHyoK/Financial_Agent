"""Experimental source-event cards deduplicate only proven shared sources."""

import copy
import unittest

from Agent_Team.Strategy_Agent.packet import _news_cards, build_compact_strategy_packet


class StrategyNewsSourceEventTests(unittest.TestCase):
    def test_one_card_per_source_cluster_with_period_mentions(self):
        catalog = {
            "A": {"source_type": "recent_raw_event", "event_id": "event-a",
                  "source_date": "2025-11-05", "title": "2025년 3분기 실적 발표",
                  "snippet": "2025년 3분기 매출이 증가했다.",
                  "event_timeline": [{"date": "2025-11-04", "title": "잠정 실적"},
                                     {"date": "2025-11-05", "title": "후속 보도"}]},
            "B": {"source_type": "recent_raw_event", "event_id": "event-b",
                  "source_date": "2025-11-06", "title": "2025년 3분기 실적 후속 해설",
                  "snippet": "다른 기사 클러스터의 해설이다."},
        }
        shared = {"evidence_ids": ["A", "B"], "anchor_evidence_id": "A",
                  "event_status": "occurred", "company_specificity": "direct",
                  "materiality_status": "observed", "financial_link_status": "observed"}
        report = {"analysis_blocks": {"news_only": {
            "positive_signals": [{**shared, "claim": "실적이 개선됐다."}],
            "key_risks": [{**shared, "evidence_ids": ["A"], "claim": "지속성은 미확인이다."}],
        }}}
        original = copy.deepcopy(report)

        split, _ = _news_cards(report, {}, catalog, split_mixed_dates=True)
        canonical, _ = _news_cards(report, {}, catalog, canonical_sources=True)

        self.assertEqual(len(split), 3)
        self.assertEqual(len(canonical), 2)
        self.assertEqual({tuple(ids) for _, ids, _ in canonical}, {("A",), ("B",)})
        a = next(card for card, ids, _ in canonical if ids == ["A"])
        observation = a["primary_observation"]
        self.assertEqual(observation["reported_period_mentions"], ["2025년 3분기"])
        self.assertEqual(observation["source_roles"], ["positive_signals", "key_risks"])
        self.assertEqual(observation["event_identity_scope"], "upstream_source_event_cluster")
        self.assertEqual(len(observation["cited_sources"]), 1)
        self.assertEqual(len(observation["cited_sources"][0]["event_timeline"]), 2)
        self.assertEqual(report, original)

    def test_packet_modes_are_mutually_exclusive(self):
        with self.assertRaisesRegex(ValueError, "Choose one"):
            build_compact_strategy_packet({}, split_mixed_date_news=True, canonical_news_sources=True)

    def test_conflicting_claim_assessments_are_not_promoted_to_source_fact(self):
        catalog = {"A": {"source_type": "recent_raw_event", "event_id": "event-a",
                         "source_date": "2025-11-05", "title": "사건 보도"}}
        report = {"analysis_blocks": {"news_only": {
            "positive_signals": [{"claim": "진행됐다", "anchor_evidence_id": "A", "evidence_ids": ["A"],
                                  "event_status": "occurred", "company_specificity": "direct",
                                  "materiality_status": "observed", "financial_link_status": "observed"}],
            "key_risks": [{"claim": "결과는 미정", "anchor_evidence_id": "A", "evidence_ids": ["A"],
                           "event_status": "reported_expectation", "company_specificity": "direct",
                           "materiality_status": "not_established", "financial_link_status": "not_observed"}],
        }}}
        cards, _ = _news_cards(report, {}, catalog, canonical_sources=True)
        self.assertEqual(len(cards), 1)
        card = cards[0][0]
        self.assertEqual(card["primary_observation"]["event_status"], "mixed")
        self.assertEqual(card["primary_observation"]["materiality_status"], "mixed")
        self.assertEqual(card["primary_observation"]["financial_link_status"], "mixed")
        self.assertEqual(card["evidence_role"], "reference")


if __name__ == "__main__":
    unittest.main()
