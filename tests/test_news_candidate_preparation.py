"""No network/model calls: pre-selection text, exclusions and frozen lineage."""
from dataclasses import replace
from datetime import date
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

import requests

from Agent_Team.News_Agent.collectors.candidate_preparation import (
    CANDIDATE_POLICY, CompanyNewsFilter, prepare_candidates, require_common_candidate_pool,
)
from Agent_Team.News_Agent.collectors.google_news_collector import GoogleNewsCollector, SNIPPET_POLICY
from Agent_Team.News_Agent.dart.schemas import RawNewsRecord


XML = '''<DOCUMENT><SECTION-2><TITLE>2. 계열회사 현황(상세)</TITLE><TABLE><TBODY>
<TR><TH>상장여부</TH><TH>회사수</TH><TH>기업명</TH><TH>법인등록번호</TH></TR>
<TR><TD ROWSPAN="3">상장</TD><TD ROWSPAN="3">3</TD><TD>(주)검증기업홀딩스</TD><TD>110111-0000001</TD></TR>
<TR><TD>(주)검증기업</TD><TD>110111-0000002</TD></TR>
<TR><TD>(주)별도법인</TD><TD>110111-0000003</TD></TR>
</TBODY></TABLE></SECTION-2>
<SECTION-2><TITLE>1. 연결대상 종속회사 현황(상세)</TITLE><TABLE><TBODY>
<TR><TH>상호</TH><TH>설립일</TH><TH>주소</TH><TH>주요사업</TH><TH>자산</TH><TH>근거</TH><TH>주요 여부</TH></TR>
<TR><TD>(주)연결법인</TD><TD>2010.01</TD><TD>한국</TD><TD>제조</TD><TD>100</TD><TD>과반수</TD><TD>해당</TD></TR>
</TBODY></TABLE></SECTION-2></DOCUMENT>'''


def record(i, title='검증기업 신규 계약', snippet=''):
    return RawNewsRecord(collect_date='2025-10-30', article_id=str(i), article_date='2025-10-15',
                         source='fixture', url=f'https://example.test/{i}', title=title,
                         snippet=snippet, doc_text=title, query_used='검증기업', lang='ko', fetched_at='2025-10-30')


class CandidatePreparationTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        path = Path(temp.name) / 'report.xml'
        path.write_text(XML)
        self.filter = CompanyNewsFilter('검증기업', path)

    def test_all_candidates_attempted_before_content_filter(self):
        rows = [record(1), record(2, '검증기업 그룹 실적'), record(3),
                record(4, '검증기업홀딩스 실적'), record(5, '다른 회사 실적'), record(6)]
        snippets = ['검증기업은 공급계약을 체결했다고 밝혔다.', '검증기업 그룹이 실적을 발표했다.', '',
                    '지주회사의 실적 발표 내용이다.', '대상기업을 언급하지 않는 기사다.',
                    '검증기업이 별도법인 인수 효과를 설명했다.']
        collector = Mock()
        collector._enrich_records.return_value = [replace(row, snippet=text, metadata={'snippet_policy': SNIPPET_POLICY})
                                                   for row, text in zip(rows, snippets)]
        attempts, eligible, audit = prepare_candidates(rows, collector=collector, company_filter=self.filter, notes=[])
        self.assertEqual(collector._enrich_records.call_args.args[0], rows)
        self.assertEqual([r.article_id for r in eligible], ['1', '2', '5', '6'])
        self.assertEqual(len(attempts), 6)
        self.assertEqual(audit['status_counts'], {'eligible': 4, 'missing_snippet': 1, 'other_related_only': 1})
        self.assertIn(snippets[0], eligible[0].doc_text)
        self.assertTrue(all(not r.snippet for r in rows), 'Preserve original input objects')

    def test_longest_name_does_not_turn_parent_into_target(self):
        result = self.filter.classify(record(1, '검증기업홀딩스', '지주회사 실적'))
        self.assertEqual(result['status'], 'other_related_only')
        self.assertEqual(result['target_names'], [])

    def test_group_word_is_not_an_exclusion_rule(self):
        for text in ['검증기업 그 룹 실적', '검증기업은 아이돌 그룹과 협업한다.']:
            self.assertEqual(self.filter.classify(record(1, snippet=text))['status'], 'eligible')

    def test_target_and_related_company_transaction_survives(self):
        result = self.filter.classify(record(1, '별도법인 사업부 매각', '검증기업이 사업부를 인수한다.'))
        self.assertEqual(result['status'], 'eligible')
        self.assertEqual(result['company_scope'], 'target')
        self.assertEqual(result['related_names'], ['별도법인'])

    def test_subsidiary_only_and_mixed_related_mentions_survive(self):
        for text in ['연결법인 실적 감소', '검증기업홀딩스와 연결법인 사업 거래']:
            result = self.filter.classify(record(1, text, '관련 사업의 변화가 발표됐다.'))
            self.assertEqual(result['status'], 'eligible')
            self.assertEqual(result['company_scope'], 'consolidated_subsidiary')
        self.assertNotIn('연결법인', self.filter.other_names)
        self.assertIn('(주)연결법인', self.filter.source['consolidated_company_names'])

    def test_consolidated_membership_takes_priority_over_affiliate_table(self):
        path = Path(self.filter.source['report_path'])
        path.write_text(XML.replace('(주)별도법인', '(주)연결법인'))
        f = CompanyNewsFilter('검증기업', path)
        self.assertIn('연결법인', f.subsidiary_names)
        self.assertNotIn('연결법인', f.other_names)

    def test_undetermined_subject_is_left_for_ranking(self):
        result = self.filter.classify(record(1, '브랜드의 해외 시장 진출', '새로운 판매 채널을 확보했다.'))
        self.assertEqual(result['status'], 'eligible')
        self.assertEqual(result['company_scope'], 'undetermined')

    def test_missing_snippet_still_excluded_for_target_or_subsidiary(self):
        for title in ['검증기업 실적', '연결법인 실적', '검증기업홀딩스 실적']:
            self.assertEqual(self.filter.classify(record(1, title, '  '))['status'], 'missing_snippet')

    def test_target_can_be_explicit_in_snippet_only(self):
        self.assertEqual(self.filter.classify(record(1, '브랜드 신제품', '검증기업이 신제품을 출시했다.'))['status'], 'eligible')

    def test_unrelated_brand_is_not_automatically_an_affiliate(self):
        self.assertEqual(self.filter.classify(record(1, snippet='검증기업의 브랜드가 신제품을 발표했다.'))['status'], 'eligible')

    def test_filing_legal_name_is_a_target_alias(self):
        path = Path(self.filter.source['report_path'])
        path.write_text(XML.replace('<DOCUMENT>', '<DOCUMENT><COMPANY-NAME>테스트법인</COMPANY-NAME>'))
        f = CompanyNewsFilter('TEST법인', path)
        self.assertEqual(f.classify(record(1, '테스트법인 신규 계약', '공급계약 관련 기사 내용'))['status'], 'eligible')

    def test_current_report_enrichment_never_fetches_after_selection(self):
        from Agent_Team.News_Agent.collectors.report_snippets import enrich_report_snippets
        rep = {'title': '검증기업', 'snippet': '실적 발표 내용', 'snippet_metadata': {'snippet_policy': SNIPPET_POLICY}}
        event = {'event_id': '1', 'representative': rep}
        report = {'collect_date': '2025-10-30', 'company': {'company_name': '검증기업'},
                  'news_selection': {'candidate_preparation': {'policy': CANDIDATE_POLICY}},
                  'news_events_all': [event], 'news_events_weekly': [event], 'news_events_final': [event]}
        collector = Mock()
        audit = enrich_report_snippets([report], collector=collector)
        self.assertEqual(audit['network_requests'], 0)
        collector._enrich_records.assert_not_called()

    def test_old_filter_snapshot_is_not_relabelled_or_fetched(self):
        from Agent_Team.News_Agent.collectors.report_snippets import enrich_report_snippets
        collector = Mock()
        for version in ['all_snippets_then_entity_filter_v1', 'all_snippets_then_entity_filter_v2']:
            report = {'collect_date': '2025-10-30', 'company': {'company_name': '검증기업'},
                      'news_selection': {'candidate_preparation': {'policy': version}}}
            with self.assertRaisesRegex(ValueError, 'Regenerate'):
                require_common_candidate_pool(report)
            with self.assertRaisesRegex(ValueError, 'Regenerate'):
                enrich_report_snippets([report], collector=collector)
            self.assertEqual(report['news_selection']['candidate_preparation']['policy'], version)
        collector._enrich_records.assert_not_called()

    def test_empty_eligible_pool_stops_before_embedding_and_keeps_audit(self):
        from Agent_Team.News_Agent.pipelines import run_news_pipeline as pipeline
        root = Path(self.filter.source['report_path']).parent
        xml = root / 'inputs/dart/id/key/검증기업_latest_periodic.xml'
        xml.parent.mkdir(parents=True)
        xml.write_text(XML)
        collector = Mock()
        collector.collect.return_value = ([record(1)], {})
        collector._enrich_records.return_value = [record(1)]
        with patch.object(pipeline, 'GoogleNewsCollector', return_value=collector), patch.object(pipeline, 'EmbeddingModel') as embedder:
            with self.assertRaisesRegex(RuntimeError, 'No eligible news'):
                pipeline.run_news_window(config={'data_root': str(root/'artifacts'), 'inputs_root': str(root/'inputs'),
                    'news': {'collection_days': 1}}, collect_date=date(2025, 10, 15),
                    company_id='id', company_name='검증기업', report_key='key')
            embedder.assert_not_called()
        audit = json.loads(next((root/'artifacts').rglob('candidate_preparation.json')).read_text())
        self.assertEqual(audit['status_counts'], {'missing_snippet': 1})

    def test_rss_identical_titles_with_different_urls_survive(self):
        rss = '<rss><channel>' + ''.join(f'<item><title>같은 기사 제목</title><link>https://example.test/{i}</link>'
            '<pubDate>Wed, 15 Oct 2025 00:00:00 GMT</pubDate><description>같은 기사 제목</description></item>' for i in [1, 2]) + '</channel></rss>'
        response = requests.Response()
        response.status_code = 200
        response._content = rss.encode()
        response.encoding = 'utf-8'
        collector = GoogleNewsCollector()
        with patch.object(collector._session, 'get', return_value=response):
            rows, _ = collector._collect_via_rss('검증기업', date(2025, 10, 15), enrich=False)
        self.assertEqual(len(rows), 2)

    def test_missing_causes_are_saved_without_exception_urls(self):
        collector = GoogleNewsCollector()
        response = requests.Response()
        response.status_code = 403
        response.url = 'https://example.test/1'
        with patch.object(collector, '_request', return_value=response):
            row = collector._enrich_record(record(1))
        self.assertEqual(row.metadata['snippet_failure_reason'], 'http_403')
        with patch.object(collector, '_request', side_effect=requests.Timeout('sensitive_url')):
            row = collector._enrich_record(record(1))
        self.assertEqual(row.metadata['snippet_failure_reason'], 'request_timeout')
        with patch.object(collector, '_request', return_value=Mock(content=b'<html/>', url='https://example.test/1')):
            row = collector._enrich_record(record(1))
        self.assertEqual(row.metadata['snippet_failure_reason'], 'no_valid_article_excerpt')
        with patch.object(collector, '_decode_google_news_url', return_value=None):
            row = collector._enrich_record(replace(record(1), url='https://news.google.com/rss/articles/unresolved'))
        self.assertEqual(row.metadata['snippet_failure_reason'], 'publisher_url_unresolved')

    def test_frozen_pool_rejects_old_missing_and_modified_selected_text(self):
        rep = {'title': '검증기업', 'snippet': '발표한 내용', 'snippet_metadata': {'snippet_policy': SNIPPET_POLICY}}
        event = {'event_id': '1', 'representative': rep}
        report = {'news_selection': {'candidate_preparation': {'policy': CANDIDATE_POLICY}},
                  'news_events_all': [event], 'news_events_weekly': [event], 'news_events_final': [event]}
        require_common_candidate_pool(report)
        altered = json.loads(json.dumps(report))
        altered['news_events_final'][0]['representative']['snippet'] = '바뀐 내용'
        with self.assertRaisesRegex(ValueError, 'differs'):
            require_common_candidate_pool(altered)
        rep['snippet'] = ''
        with self.assertRaisesRegex(ValueError, 'missing'):
            require_common_candidate_pool(report)
        with self.assertRaisesRegex(ValueError, 'Regenerate'):
            require_common_candidate_pool({})


if __name__ == '__main__':
    unittest.main()
