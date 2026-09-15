"""LLM-only metadata projection preserves source packets, observations and citations."""
import copy
import json
import unittest

import test_annual_context  # Configures local agent imports.
from shared.subdata import evidence_catalog_for_llm, secondary_context_for_llm
from Agent_Team.News_Agent.analysis_agent import _compact_news_evidence_for_llm, build_llm_request
from Agent_Team.News_Agent.context_export import _build_llm_summary_request, _load_llm_user_payload
from Agent_Team.Financial_Agent.financial_analysis_agent import build_financial_request, build_financial_llm_packet
from Agent_Team.YFinance_Agent.reporting import build_market_request, build_llm_evidence_packet


def context_fixture():
    return {'financial': {
        'version': 'audit_v1', 'status': 'available', 'statement_scope': 'consolidated',
        'periods': {'current': {'period_end': '2025-06-30', 'basis': 'YTD',
                               'receipt_date': '2025-08-14', 'receipt_no': 'audit_receipt'}},
        'evidence_catalog': {'FIN_X': {'evidence_id': 'FIN_X', 'source_ref': 'local.metric.x',
            'domain': 'financial', 'origin_type': 'deterministic_derived', 'source_date': '2025-08-14',
            'metric': 'operating_profit', 'unit': '억원', 'value': -20,
            'values_by_period': {'previous': None, 'current': -20}, 'missing_reasons': {'previous': 'unavailable'}}}},
        'news': {'version': 'audit_v1', 'status': 'available', 'evidence_catalog': {
            'NEWS_M': {'evidence_id': 'NEWS_M', 'source_ref': 'local.news.period',
                'domain': 'news', 'origin_type': 'model_summarized', 'metric': 'monthly_news_context',
                'source_date': '2025-06-30', 'period_start': '2025-06-01', 'period_end': '2025-06-30',
                'text': '검증그룹의 실적 개선에 검증기업의 해외 판매가 기여했다.', 'source_event_count': 15}}}}


class LLMInputMetadataTests(unittest.TestCase):
    def test_only_known_audit_fields_removed_without_changing_facts_or_source(self):
        original = context_fixture()
        before = copy.deepcopy(original)
        result = secondary_context_for_llm(original)
        expected = copy.deepcopy(original)
        for context in expected.values():
            context.pop('version')
            for period in context.get('periods', {}).values():
                period.pop('receipt_no')
            for row in context['evidence_catalog'].values():
                row.pop('evidence_id')
                row.pop('source_ref')
                row.pop('source_event_count', None)
        self.assertEqual(result, expected)
        self.assertEqual(original, before)
        result['financial']['evidence_catalog']['FIN_X']['values_by_period']['current'] = 999
        self.assertEqual(original, before)
        self.assertEqual(secondary_context_for_llm({}), {})
        self.assertEqual(secondary_context_for_llm({'news': {'status': 'unavailable', 'evidence_catalog': {}}}),
                         {'news': {'status': 'unavailable', 'evidence_catalog': {}}})

    def test_primary_catalog_keeps_source_identity_status_and_mismatched_ids(self):
        catalog = {'N1': {'evidence_id': 'N1', 'source_ref': 'internal.path', 'source': '언론사',
                         'source_date': '2025-06-01', 'status': 'available', 'value': 0},
                   'N2': {'evidence_id': 'mismatch', 'value': -1}}
        result = evidence_catalog_for_llm(catalog)
        self.assertEqual(set(result), set(catalog))
        self.assertEqual(result['N1'], {'source': '언론사', 'source_date': '2025-06-01', 'status': 'available', 'value': 0})
        self.assertEqual(result['N2']['evidence_id'], 'mismatch')
        self.assertIn('source_ref', catalog['N1'])

    def test_news_retains_snippet_timeline_and_source_attributes_not_collection_qa(self):
        row = {'source_date': '2025-06-02', 'title': '검증그룹 실적 발표', 'snippet': '검증기업 판매가 그룹 성과에 기여했다.',
               'source': '언론사', 'mention_count': 2, 'coverage': {'article_count': 2, 'unique_publisher_count': 2,
               'publisher_names': ['언론사', '다른언론사'], 'primary_source_present': False,
               'deduplicated_article_count': 2, 'coverage_quality': 'verified'},
               'event_timeline': [{'date': '2025-06-01', 'title': '실적 발표'}, {'date': '2025-06-02', 'title': '후속 보도'}]}
        before = copy.deepcopy(row)
        result = _compact_news_evidence_for_llm(row)
        self.assertEqual(result['coverage'], {'publisher_names': ['언론사', '다른언론사'], 'primary_source_present': False})
        self.assertEqual({k: v for k, v in result.items() if k != 'coverage'}, {k: v for k, v in row.items() if k != 'coverage'})
        self.assertEqual(row, before)

    def test_summary_keeps_source_ids_without_unused_counts(self):
        source = {'metadata': {}, 'periods': [{'period': '2025-06', 'event_count': 1,
                  'events': [{'event_id': 'E1', 'mention_count': 3, 'title': '검증그룹 실적 발표'}]}]}
        before = copy.deepcopy(source)
        request = _build_llm_summary_request(source, 'gpt-5.4-mini')
        period = _load_llm_user_payload(request)['periods'][0]
        self.assertNotIn('event_count', period)
        self.assertEqual(period['events'], [{'event_id': 'E1', 'title': '검증그룹 실적 발표'}])
        self.assertEqual(source, before)

    def test_all_domain_request_boundaries_share_projection_without_mutating_packets(self):
        contexts = context_fixture()
        expected = secondary_context_for_llm(contexts)
        financial = {'target_company': '검증기업', 'secondary_context': contexts,
                     'strategy_handoff': {'key_evidence': [{'evidence_id': 'E001', 'source_ref': 'local.f', 'value': 10}]}}
        market = {'company_name': '검증기업', 'market_summary': {'latest_snapshot': {'date': '2025-06-30'}},
                  'primary_evidence_catalog': {'YF_1': {'evidence_id': 'YF_1', 'source_ref': 'local.y', 'value': 20}},
                  'secondary_context': contexts}
        news = {'target_entity': {'company_name': '검증기업'}, 'input_policy': {}, 'news_context': {},
                'evidence_map': {'N1': {'domain': 'news', 'title': '기사'}}, 'secondary_context': contexts}
        original = copy.deepcopy((financial, market, news))
        f = json.loads(build_financial_request(financial, model='gpt-5.4-mini')['input'][1]['content'])
        y = json.loads(build_market_request(market, model='gpt-5.4-mini')['input'][1]['content'])
        n = json.loads(build_llm_request(input_payload=news, model='gpt-5.4-mini')['input'][1]['content'])['input_payload']
        for body in (f, y, n):
            self.assertEqual(body['secondary_context'], expected)
        self.assertNotIn('source_ref', f['primary_financial_evidence']['E001'])
        self.assertNotIn('source_ref', y['primary_market_evidence']['YF_1'])
        self.assertIn('source_ref', build_financial_llm_packet(financial)['primary_financial_evidence']['E001'])
        self.assertIn('source_ref', build_llm_evidence_packet(market)['primary_market_evidence']['YF_1'])
        self.assertEqual((financial, market, news), original)


if __name__ == '__main__':
    unittest.main()
