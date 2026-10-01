"""Experimental news-date splitting must preserve provenance without mixing dates."""

import copy
import unittest

from Agent_Team.Strategy_Agent.packet import _news_cards


class StrategyNewsDateSplitTests(unittest.TestCase):
    def test_mixed_dates_use_source_specific_summaries_and_ids(self):
        catalog = {
            "EARLY": {"title": "2024년 실적", "source_date": "2025-02-06", "snippet": "2024년 실적 발표"},
            "LATE": {"title": "2025년 3분기 실적", "source_date": "2025-11-05", "snippet": "2025년 3분기 발표"},
            "MONTH": {"title": "기간 요약", "source_type": "monthly_news_context",
                      "period": "2025-11-01/2025-11-06", "snippet": "월별 흐름"},
        }
        claim = {"claim": "2024년 실적과 2025년 3분기 실적이 모두 개선됐다.",
                 "anchor_evidence_id": "EARLY", "evidence_ids": ["EARLY", "LATE", "MONTH"],
                 "event_status": "occurred", "company_specificity": "direct",
                 "materiality_status": "observed", "financial_link_status": "observed"}
        report = {"analysis_blocks": {"news_only": {"positive_signals": [claim]}}}
        before = copy.deepcopy(report)

        original, _ = _news_cards(report, {}, catalog)
        split, _ = _news_cards(report, {}, catalog, split_mixed_dates=True)

        self.assertEqual(len(original), 1)
        self.assertEqual(len(split), 3)
        by_date = {card["primary_observation"]["event_date"]: (card, ids)
                   for card, ids, _ in split}
        self.assertEqual(by_date["2025-02-06"][1], ["EARLY"])
        self.assertEqual(by_date["2025-11-05"][1], ["LATE"])
        self.assertEqual(by_date["2025-02-06"][0]["primary_observation"]["event_summary"], "2024년 실적")
        self.assertNotIn("2025년 3분기", str(by_date["2025-02-06"]))
        self.assertEqual(by_date[None][0]["card_type"], "period_summary")
        self.assertEqual(report, before)

    def test_same_date_articles_and_period_anchor_stay_together(self):
        catalog = {
            "A": {"title": "최초 보도", "source_date": "2025-11-05"},
            "B": {"title": "후속 보도", "source_date": "2025-11-05"},
            "M": {"title": "월별 요약", "source_type": "monthly_news_context", "period": "2025-11"},
        }
        claim = {"claim": "같은 날 보도", "anchor_evidence_id": "A", "evidence_ids": ["A", "B"]}
        report = {"analysis_blocks": {"news_only": {"positive_signals": [claim]}}}
        cards, _ = _news_cards(report, {}, catalog, split_mixed_dates=True)
        self.assertEqual(len(cards), 1)
        claim["anchor_evidence_id"] = "M"
        claim["evidence_ids"] = ["M", "A", "B"]
        cards, _ = _news_cards(report, {}, catalog, split_mixed_dates=True)
        self.assertEqual(len(cards), 1)
        self.assertEqual(cards[0][0]["card_type"], "period_summary")


if __name__ == "__main__":
    unittest.main()
