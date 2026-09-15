import copy
from datetime import date, timedelta
from pathlib import Path
import json
import tempfile
import unittest

from shared.news_selection import (select_monthly_news, validate_monthly_news, MONTHLY_NEWS_POLICY,
                                   MONTHLY_NEWS_LABEL, event_period)
from shared.time_windows import monthly_windows
from Agent_Team.News_Agent import context_export


class MonthlyNewsTests(unittest.TestCase):
    def fixture(self):
        end = date(2025, 11, 6)
        rows = [{"event_id": f"m{i}_{j}", "time": w["period_start"], "title": f"사건{i}_{j}"}
                for i, w in enumerate(monthly_windows(end)) for j in range(3)]
        return end, rows

    def test_two_per_aligned_month_not_global_twenty(self):
        end, rows = self.fixture()
        before = copy.deepcopy(rows)
        selected = select_monthly_news(rows, end_exclusive=end, time_of=lambda r:r["time"], id_of=lambda r:r["event_id"])
        self.assertEqual(len(selected), 24)
        self.assertTrue(all(not r["event_id"].endswith("_2") for r in selected))
        self.assertEqual(rows, before)
        self.assertEqual(selected, sorted(selected, key=lambda r:(r["time"],r["event_id"])))

    def test_sparse_months_do_not_borrow_or_duplicate(self):
        end, rows = self.fixture()
        selected = select_monthly_news(rows[:1] + rows[6:9], end_exclusive=end,
                                      time_of=lambda r:r["time"], id_of=lambda r:r["event_id"])
        self.assertEqual(len(selected), 3)

    def test_boundaries_invalid_dates_and_duplicates(self):
        end, rows = self.fixture()
        windows = monthly_windows(end)
        self.assertEqual(event_period("2025-10-05",windows), windows[-2]["period"])
        self.assertEqual(event_period("2025-10-06",windows), windows[-1]["period"])
        self.assertIsNone(event_period("2025-11-06",windows))
        self.assertIsNone(event_period("invalid",windows))
        selected = select_monthly_news([rows[0],rows[0]], end_exclusive=end,
                                      time_of=lambda r:r["time"], id_of=lambda r:r["event_id"])
        self.assertEqual(len(selected), 1)

    def test_validator_rejects_overflow_not_silent_cut(self):
        end, rows = self.fixture()
        with self.assertRaises(ValueError):
            validate_monthly_news(rows[:3],end_exclusive=end,time_of=lambda r:r["time"],id_of=lambda r:r["event_id"])

    def test_export_preserves_24_and_keeps_broader_summary_pool(self):
        end, rows = self.fixture()
        events=[{"event_id":r["event_id"],"mention_count":1,"representative":{
            "time":r["time"],"title":r["title"],"snippet":"보도된 내용"},"scores":{"final_score":.5}} for r in rows]
        selected=select_monthly_news(events,end_exclusive=end,time_of=lambda r:r["representative"]["time"],id_of=lambda r:r["event_id"])
        report={"collect_date":(end-timedelta(days=1)).isoformat(),"company":{"company_name":"검증기업"},
                "news_selection":{"raw_news_policy":MONTHLY_NEWS_POLICY,"monthly_top_k":2},
                "news_events_weekly":events,"news_events_final":selected}
        with tempfile.TemporaryDirectory() as tmp:
            p=Path(tmp)/"report.json"; p.write_text(json.dumps(report))
            result=context_export.build_context_exports(report_context_path=p,output_dir=Path(tmp)/"exports",
                                                       granularity="month",period_count=12)
            raw=json.loads(Path(result["recent_raw_input_path"]).read_text())
            summary=json.loads(Path(result["summary_prompt_input_path"]).read_text())
            self.assertEqual(len(raw["events"]),24)
            self.assertEqual(raw["selection"],MONTHLY_NEWS_LABEL)
            self.assertEqual(sum(len(x["events"]) for x in summary["periods"]),36)
            from Agent_Team.News_Agent import analysis_agent
            out=Path(tmp)/"exports"
            packet={"output":{"periods":[{"period":w['period'],"period_summary":"월별 관측 내용이다.","issues":[]} for w in monthly_windows(end)]}}
            (out/'llm_period_summaries.json').write_text(json.dumps(packet))
            (out/'empty.json').write_text('{}')
            paths=analysis_agent.AnalysisPaths(context_export_dir=out,context_manifest_path=out/'context_export_manifest.json',
                period_summaries_path=out/'llm_period_summaries.json',summary_prompt_input_path=out/'summary_prompt_input.json',
                recent_raw_path=out/'recent_raw_input.json',dart_lightweight_path=out/'empty.json',market_summary_path=out/'empty.json',
                output_dir=out,input_payload_path=out/'input.json',llm_request_path=out/'request.json',handoff_path=out/'handoff.json',evidence_map_path=out/'evidence.json')
            payload=analysis_agent.build_analysis_input_payload(company_name='검증기업',ticker=None,corp_code=None,
                as_of_date=end,paths=paths,max_raw_events_per_period=24)
            request=analysis_agent.build_llm_request(input_payload=payload,model='gpt-5.4-mini')
            llm_input=json.loads(request['input'][1]['content'])['input_payload']
            self.assertEqual(len(llm_input[MONTHLY_NEWS_LABEL]),24)
            self.assertNotIn('기업 관련 뉴스 상위 20건',llm_input)
            self.assertTrue(all('relevance_rank' not in v for v in llm_input[MONTHLY_NEWS_LABEL].values()))
            with self.assertRaisesRegex(ValueError,'24-event'):
                analysis_agent.build_analysis_input_payload(company_name='검증기업',ticker=None,corp_code=None,
                    as_of_date=end,paths=paths,max_raw_events_per_period=20)


if __name__ == "__main__":
    unittest.main()
