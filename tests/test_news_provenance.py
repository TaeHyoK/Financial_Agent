import copy
import json
import unittest
from unittest.mock import patch
from datetime import date
from types import SimpleNamespace

from Agent_Team.News_Agent import analysis_agent as news
from Agent_Team.Strategy_Agent.packet import _news_cards, build_compact_strategy_packet
from Agent_Team.Strategy_Agent.context import _news_handoff
from Agent_Team.Strategy_Agent.decision import build_strategy_context_package


class NewsProvenanceTests(unittest.TestCase):
    def setUp(self):
        self.catalog = {
            "NEWS_RAW_A": {"title": "국내 허가", "source_date": "2025-10-03"},
            "NEWS_RAW_B": {"title": "공급계약", "source_date": "2025-10-15"},
            "NEWS_PERIOD_M": {"title": "월별 요약", "source_type": "monthly_news_context",
                              "period": "2025-10-01/2025-10-31", "source_date": "2025-10-31",
                              "snippet": "허가와 공급계약, 합작법인 설립 소식이 있었다."},
        }

    def claim(self, text, anchor, ids):
        return {"claim": text, "anchor_evidence_id": anchor, "evidence_ids": ids,
                "event_status": "announced", "company_specificity": "direct",
                "materiality_status": "plausible_unquantified", "financial_link_status": "not_observed"}

    def test_shared_month_and_article_do_not_merge_claims(self):
        positive = [self.claim("국내 허가", "NEWS_RAW_A", ["NEWS_RAW_A", "NEWS_PERIOD_M"]),
                    self.claim("공급계약", "NEWS_RAW_B", ["NEWS_RAW_B", "NEWS_PERIOD_M"])]
        risk = self.claim("허가 이후 판매 규모는 미정", "NEWS_RAW_A", ["NEWS_RAW_A", "NEWS_PERIOD_M"])
        report = {"analysis_blocks": {"news_only": {"positive_signals": positive, "uncertainties": [risk]}}}
        before = copy.deepcopy(report)
        cards, omitted = _news_cards(report, {}, self.catalog)
        self.assertEqual(len(cards), 3)
        self.assertEqual(omitted, [])
        self.assertEqual(report, before)
        self.assertEqual({c["primary_observation"]["event_summary"] for c, _, _ in cards},
                         {x["claim"] for x in [*positive, risk]})
        for card, ids, _ in cards:
            expected = "NEWS_RAW_B" if card["primary_observation"]["event_summary"] == "공급계약" else "NEWS_RAW_A"
            self.assertEqual(ids[0], expected)
            self.assertEqual(card["primary_observation"]["anchor_source"]["title"], self.catalog[expected]["title"])

    def test_period_anchor_is_not_promoted_by_supplementary_article(self):
        claim = self.claim("요약에 따르면 합작법인이 설립됐다", "NEWS_PERIOD_M", ["NEWS_RAW_A", "NEWS_PERIOD_M"])
        cards, _ = _news_cards({"analysis_blocks": {"news_only": {"positive_signals": [claim]}}}, {}, self.catalog)
        card, ids, _ = cards[0]
        self.assertEqual(ids[0], "NEWS_PERIOD_M")
        self.assertEqual(card["card_type"], "period_summary")
        self.assertIsNone(card["primary_observation"]["event_date"])
        self.assertEqual(card["primary_observation"]["anchor_source"]["source_type"], "monthly_news_context")

    def test_packet_preserves_anchor_in_provenance_and_context(self):
        claim = self.claim("국내 허가", "NEWS_RAW_A", ["NEWS_RAW_A", "NEWS_PERIOD_M"])
        bundle = {"target_company": {"company_name": "검증기업"},
                  "target_reports": {"news": {"analysis_blocks": {"news_only": {"positive_signals": [claim]}}}},
                  "evidence_catalogs": {"news": self.catalog}}
        packet, provenance, _, _ = build_compact_strategy_packet(bundle)
        context = build_strategy_context_package(packet, input_bundle=bundle)
        key = next(k for k, c in packet["cards"].items() if c["domain"] == "news")
        self.assertEqual(provenance["cards"][key]["anchor_evidence_id"], "NEWS_RAW_A")
        self.assertEqual(context["evidence_cards"][key]["primary_observation"]["anchor_source"]["title"], "국내 허가")
        self.assertEqual(packet["coverage_summary"]["news_preserved_claims"], 1)

    def test_unknown_anchor_is_not_silently_replaced(self):
        claim = self.claim("공급계약", "NEWS_RAW_UNKNOWN", ["NEWS_RAW_B"])
        with self.assertRaisesRegex(ValueError, "Unknown news anchor"):
            _news_cards({"analysis_blocks": {"news_only": {"positive_signals": [claim]}}}, {}, self.catalog)

    def test_cited_articles_keep_individual_dates_and_summary_keeps_period(self):
        self.catalog['NEWS_RAW_A'].update(snippet='검증기업의 1분기 실적 발표다.',
            event_timeline=[{'date': '2025-10-03', 'title': '실적 발표'},
                            {'date': '2025-10-04', 'title': '같은 실적의 후속 보도'}])
        self.catalog['NEWS_RAW_B']['snippet'] = '검증기업의 3분기 실적 발표다.'
        claim = self.claim('서로 다른 분기의 실적을 비교한다.', 'NEWS_RAW_B',
                           ['NEWS_RAW_A', 'NEWS_RAW_B', 'NEWS_PERIOD_M'])
        bundle = {'target_company': {'company_name': '검증기업'},
                  'target_reports': {'news': {'analysis_blocks': {'news_only': {'positive_signals': [claim]}}}},
                  'evidence_catalogs': {'news': self.catalog}}
        before = copy.deepcopy(bundle)
        packet, provenance, _, _ = build_compact_strategy_packet(bundle)
        context = build_strategy_context_package(packet, input_bundle=bundle)
        key = next(k for k, c in packet['cards'].items() if c['domain'] == 'news')
        observation = context['evidence_cards'][key]['primary_observation']
        sources = observation['cited_sources']
        self.assertEqual([row['source_type'] for row in sources],
                         ['article', 'article', 'monthly_news_context'])
        self.assertEqual([(row['source_date'], row['snippet']) for row in sources[:2]],
                         [('2025-10-15', '검증기업의 3분기 실적 발표다.'),
                          ('2025-10-03', '검증기업의 1분기 실적 발표다.')])
        self.assertEqual(sources[1]['event_timeline'], self.catalog['NEWS_RAW_A']['event_timeline'])
        self.assertEqual(sources[2]['period'], '2025-10-01/2025-10-31')
        self.assertNotIn('source_date', sources[2])
        self.assertEqual(sources[2]['summary'], self.catalog['NEWS_PERIOD_M']['snippet'])
        self.assertEqual(observation['event_date'], '2025-10-15')
        self.assertNotIn('representative_excerpts', observation)
        self.assertIn('representative_excerpts', packet['cards'][key]['primary_observation'])
        self.assertEqual(provenance['cards'][key]['source_evidence_ids'],
                         ['NEWS_RAW_B', 'NEWS_RAW_A', 'NEWS_PERIOD_M'])
        self.assertEqual(bundle, before)
        sources[1]['event_timeline'][0]['title'] = 'changed'
        self.assertEqual(bundle, before)
        self.assertEqual(packet['cards'][key]['primary_observation']['cited_sources'][1]
                         ['event_timeline'][0]['title'], '실적 발표')

    def test_missing_date_is_not_filled_from_anchor_and_equal_snippets_keep_dates(self):
        self.catalog['NEWS_RAW_A']['snippet'] = '같은 문구다.'
        self.catalog['NEWS_RAW_B']['snippet'] = '같은 문구다.'
        self.catalog['NEWS_RAW_C'] = {'title': '날짜 없는 기사', 'snippet': '같은 문구다.'}
        claim = self.claim('후속 보도들을 함께 참고한다.', 'NEWS_RAW_B',
                          ['NEWS_RAW_A', 'NEWS_RAW_B', 'NEWS_RAW_C'])
        cards, _ = _news_cards({'analysis_blocks': {'news_only': {'positive_signals': [claim]}}}, {}, self.catalog)
        sources = cards[0][0]['primary_observation']['cited_sources']
        self.assertEqual([row['source_date'] for row in sources], ['2025-10-15', '2025-10-03', None])
        self.assertEqual(len(sources), 3)

    def test_source_dates_have_no_count_cap(self):
        catalog = {f'NEWS_RAW_{i}': {'title': f'기사 {i}', 'source_date': f'2025-10-{i+1:02d}',
                                   'snippet': f'날짜별 내용 {i}'} for i in range(25)}
        claim = self.claim('기간 중 사건의 진행을 비교한다.', 'NEWS_RAW_0', list(catalog))
        cards, _ = _news_cards({'analysis_blocks': {'news_only': {'positive_signals': [claim]}}}, {}, catalog)
        self.assertEqual(len(cards), 1)  # No date-based claim splitting.
        self.assertEqual(len(cards[0][0]['primary_observation']['cited_sources']), 25)

    def test_news_prompt_distinguishes_reporting_date_from_earnings_period(self):
        request = news.build_llm_request(input_payload={'target_entity': {'company_name': '검증기업'},
            'input_policy': {}, 'news_context': {},
            'evidence_map': {'NEWS_RAW_A': {'domain': 'news', 'title': '실적 발표'}},
            'secondary_context': {}}, model='gpt-5.4')
        body = json.loads(request['input'][1]['content'])
        rules = '\n'.join(body['analysis_rules'])
        self.assertIn('기사 보도일과 실적 대상 기간을 구분', rules)
        self.assertIn('같은 실적의 후속 보도를 별도 사건으로 늘리지 않습니다', rules)

    def test_parser_preserves_anchor_without_duplicate_ids(self):
        claim = self.claim("공급계약", "NEWS_RAW_B", ["NEWS_PERIOD_M", "NEWS_RAW_B"])
        assessment = {"statement": "실적과 관련된다", "primary_anchor_evidence_id": "NEWS_RAW_B",
                      "primary_evidence_ids": ["NEWS_RAW_B"], "secondary_anchor_evidence_id": "DART_X",
                      "secondary_evidence_ids": ["DART_X"]}
        report = {"analysis_blocks": {"news_only": {"positive_signals": [claim]}},
                  "secondary_context_assessment": [assessment]}
        news._merge_analysis_anchor_evidence_ids(report)
        news._merge_analysis_anchor_evidence_ids(report)
        self.assertEqual(claim["anchor_evidence_id"], "NEWS_RAW_B")
        self.assertEqual(claim["evidence_ids"], ["NEWS_RAW_B", "NEWS_PERIOD_M"])
        self.assertEqual(assessment["primary_anchor_evidence_id"], "NEWS_RAW_B")
        self.assertNotIn("primary_anchor_evidence_id", str(_news_handoff(report)))

    def test_summary_keeps_actual_sources_not_same_month_selected_articles(self):
        period = "2025-10-01/2025-10-31"
        summaries = {"output": {"periods": [{"period": period, "period_summary": "합작법인 설립",
                                              "source_event_ids": ["NEWS_RAW_C", "NEWS_RAW_D"]}]}}
        paths = SimpleNamespace(period_summaries_path="summaries", recent_raw_path="raw",
                                context_manifest_path="manifest", summary_prompt_input_path="summary_input",
                                dart_lightweight_path="dart", market_summary_path="market", evidence_map_path="evidence")
        with patch.object(news, "_load_json", side_effect=lambda p: summaries if p == "summaries" else {}), \
             patch.object(news, "_resolve_analysis_periods", return_value=([period], [period], [period], "summary", "raw")), \
             patch.object(news, "_select_company_top_news", return_value=[{"period": period, "events": [{"evidence_id": "NEWS_RAW_A"}]}]), \
             patch.object(news, "_build_evidence_map", return_value={}):
            result = news.build_analysis_input_payload(company_name="검증기업", ticker=None, corp_code=None,
                as_of_date=date(2025, 11, 1), paths=paths, max_raw_events_per_period=24, include_secondary_context=False)
        summary = result["news_context"]["monthly_summaries"][0]
        self.assertEqual(summary["source_event_ids"], ["NEWS_RAW_C", "NEWS_RAW_D"])
        self.assertNotIn("source_evidence_ids", summary)
        compact = news._compact_period_summary_for_llm(summary)
        self.assertNotIn("source_evidence_ids", compact)
        self.assertNotIn("source_event_count", compact)
        self.assertEqual(summary["source_event_ids"], ["NEWS_RAW_C", "NEWS_RAW_D"])


if __name__ == "__main__":
    unittest.main()
