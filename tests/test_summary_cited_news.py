import copy
import unittest
from datetime import date
from types import SimpleNamespace
from unittest.mock import patch

from Agent_Team.News_Agent import analysis_agent as news
from shared.news_selection import SUMMARY_CITED_NEWS_POLICY
from shared.subdata import news_subdata


class SummaryCitedNewsTests(unittest.TestCase):
    def fixtures(self, count=30):
        period = "2025-10-01/2025-10-31"
        issues = [{"summary": f"독립 사건 {i}", "source_event_ids": [str(i)]} for i in range(count)]
        summaries = {"output": {"periods": [{"period": period, "issues": issues}]}}
        raw = {"metadata": {"raw_news_policy": SUMMARY_CITED_NEWS_POLICY}, "events": [
            {"event_id": str(i), "period": period, "title": f"기사 {i}", "snippet": f"내용 {i}",
             "time": "2025-10-15"} for i in range(count)]}
        paths = SimpleNamespace(period_summaries_path="summaries", recent_raw_path="raw",
            context_manifest_path="manifest", summary_prompt_input_path="summary_input",
            dart_lightweight_path="dart", market_summary_path="market", evidence_map_path="evidence")
        return period, summaries, raw, paths

    def build(self, fixture):
        period, summaries, raw, paths = fixture
        with patch.object(news, "_load_json", side_effect=lambda p: summaries if p == "summaries" else raw), \
             patch.object(news, "_resolve_analysis_periods", return_value=([period], [period], [period], "summary", "raw")):
            return news.build_analysis_input_payload(company_name="검증기업", ticker=None, corp_code=None,
                as_of_date=date(2025, 11, 1), paths=paths, max_raw_events_per_period=24,
                include_secondary_context=False)

    def test_all_cited_articles_and_issue_links_survive_legacy_cap(self):
        fixture = self.fixtures()
        before = copy.deepcopy(fixture[1])
        result = self.build(fixture)
        self.assertEqual(len(result["news_context"]["company_related_top_news"]), 30)
        compact = news._compact_period_summary_for_llm(result["news_context"]["monthly_summaries"][0])
        self.assertEqual(len(compact["issues"]), 30)
        self.assertNotIn("period_summary", compact)
        for issue in compact["issues"]:
            self.assertTrue(issue["summary"])
            self.assertTrue(all(key in result["evidence_map"] for key in issue["source_evidence_ids"]))
        self.assertEqual(fixture[1], before)

    def test_missing_source_fails_instead_of_silent_substitution(self):
        fixture = self.fixtures()
        fixture[2]["events"].pop()
        with self.assertRaisesRegex(ValueError, "missing"):
            self.build(fixture)

    def test_subdata_preserves_separate_events_and_counts_months(self):
        _, summaries, _, _ = self.fixtures()
        result = news_subdata(summaries)
        self.assertEqual(result["period_count"], 1)
        self.assertEqual(len(result["evidence_catalog"]), 30)
        self.assertEqual({row["text"] for row in result["evidence_catalog"].values()},
                         {f"독립 사건 {i}" for i in range(30)})


if __name__ == "__main__":
    unittest.main()
