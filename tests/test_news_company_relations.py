"""Generic DART table variations and company identity; no external calls."""
from pathlib import Path
import tempfile
import unittest

from Agent_Team.News_Agent.dart.company_relations import prefix_aliases, read_company_relations
from Agent_Team.News_Agent.collectors.candidate_preparation import CompanyNewsFilter
from test_news_candidate_preparation import XML, record


def section(title, table):
    return f'<SECTION-2><TITLE>{title}</TITLE><TABLE>{table}</TABLE></SECTION-2>'


EMPTY = section('연결대상 종속회사 현황(상세)', '<TR><TH>상호</TH></TR><TR><TD>-</TD></TR>')


class CompanyRelationTests(unittest.TestCase):
    def make_filter(self, xml, name='검증기업'):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        path = Path(temporary.name) / 'report.xml'
        path.write_text(xml)
        return CompanyNewsFilter(name, path)

    def test_malformed_earlier_section_does_not_hide_later_tables(self):
        xml = XML.replace('<DOCUMENT>', '<DOCUMENT><SECTION-2><TITLE>앞부분</TITLE><P>잘못된 &entity;<BR></SECTION-2>')
        f = self.make_filter(xml)
        self.assertIn('별도법인', f.other_names)
        self.assertIn('연결법인', f.subsidiary_names)

    def test_reordered_columns_variable_width_and_non_date_establishment(self):
        affiliate = section('계열회사 현황(상세)', '<TR><TH>기업명</TH></TR><TR><TD>별도법인</TD></TR>')
        subsidiary = section('1. 연결대상 종속회사 현황 ( 상세 )',
            '<TR><TH>주요사업</TH><TH>설립일</TH><TH>비고</TH><TH>상호</TH></TR>'
            '<TR><TD>제조</TD><TD>미기재</TD><TD>연결</TD><TD>연결법인</TD></TR>')
        f = self.make_filter('<DOCUMENT>' + affiliate + subsidiary + '</DOCUMENT>')
        self.assertEqual(f.subsidiary_names, {'연결법인'})
        self.assertEqual(f.other_names, {'별도법인'})

    def test_rowspan_is_resolved_using_header_positions(self):
        f = self.make_filter(XML)
        self.assertEqual(f.other_names, {'검증기업홀딩스', '별도법인'})

    def test_multilevel_header_and_foreign_companies_without_registration(self):
        affiliate = section('2. 계열회사 현황(상세)',
            '<TR><TH COLSPAN="2">회사 정보</TH><TH ROWSPAN="2">비고</TH></TR>'
            '<TR><TH>회사명</TH><TH>소재국</TH></TR>'
            '<TR><TD>Overseas Holdings Ltd.</TD><TD>영국</TD><TD>-</TD></TR>')
        result = read_company_relations(affiliate + EMPTY)
        self.assertEqual(result['names']['affiliates'], {'Overseas Holdings Ltd.'})

    def test_same_registration_connects_target_alias_without_transliteration(self):
        affiliate = section('계열회사 현황(상세)',
            '<TR><TH>법인등록번호</TH><TH>기업명</TH></TR>'
            '<TR><TD>110111-0000002</TD><TD>(주)에이비전자</TD></TR>'
            '<TR><TD>110111-0000002</TD><TD>AB전자(주)</TD></TR>')
        header = '<COMPANY-NAME>AB전자</COMPANY-NAME><EXTRACTION ACODE="CRP_RGS_NO_TEMP">110111-0000002</EXTRACTION>'
        f = self.make_filter(header + affiliate + EMPTY, 'AB전자')
        self.assertEqual(f.targets, {'ab전자', '에이비전자'})
        self.assertEqual(f.classify(record(1, '에이비전자 신규 투자', '공급계약 발표'))['company_scope'], 'target')
        self.assertFalse(f.other_names)

    def test_prefix_ambiguity_is_not_specific_to_one_group(self):
        affiliate = section('계열회사 현황(상세)',
            '<TR><TH>기업명</TH></TR><TR><TD>AB</TD></TR>'
            '<TR><TD>AB전자</TD></TR><TR><TD>독립홀딩스</TD></TR>')
        f = self.make_filter(affiliate + EMPTY, 'AB전자')
        self.assertEqual(f.source['ignored_ambiguous_names'], ['ab'])
        self.assertEqual(f.classify(record(1, 'AB야구단 우승', '스포츠 기사'))['company_scope'], 'undetermined')
        self.assertEqual(f.classify(record(1, '독립홀딩스 실적', '지주회사 기사'))['status'], 'other_related_only')

    def test_confirmed_target_spelling_supports_other_disclosed_names(self):
        affiliate = section('계열회사 현황(상세)',
            '<TR><TH>기업명</TH></TR><TR><TD>에이비화학</TD></TR>')
        f = self.make_filter('<COMPANY-NAME>에이비전자</COMPANY-NAME>' + affiliate + EMPTY, 'AB전자')
        self.assertEqual(f.classify(record(1, 'AB화학 실적 발표', '화학 회사 실적'))['status'], 'other_related_only')
        self.assertEqual(f.source['derived_prefix_aliases']['ab화학'], '에이비화학')
        self.assertNotIn('ab야구', f.other_names)

    def test_spelling_inference_does_not_override_an_existing_name(self):
        names = {'ab전자', '에이비전자', 'ab화학', '에이비화학'}
        self.assertEqual(prefix_aliases({'ab전자', '에이비전자'}, names), {})

    def test_missing_section_is_not_an_empty_company_list(self):
        with self.assertRaisesRegex(ValueError, 'missing.*계열회사'):
            read_company_relations(EMPTY)

    def test_unrecognized_header_is_not_an_empty_company_list(self):
        with self.assertRaisesRegex(ValueError, 'unreadable.*계열회사'):
            read_company_relations(section('계열회사 현황(상세)', '<TR><TD>알 수 없는 형식</TD></TR>') + EMPTY)

    def test_explicit_empty_tables_have_a_distinct_status(self):
        result = read_company_relations(section('계열회사 현황(상세)', '<TR><TH>기업명</TH></TR><TR><TD>-</TD></TR>') + EMPTY)
        self.assertTrue(all(value['status'] == 'empty' for value in result['tables'].values()))
        self.assertEqual(result['names']['subsidiaries'], set())

    def test_header_without_data_is_unreadable_not_explicitly_empty(self):
        with self.assertRaisesRegex(ValueError, 'unreadable'):
            read_company_relations(section('계열회사 현황(상세)', '<TR><TH>기업명</TH></TR>') + EMPTY)

    def test_missing_name_in_a_populated_row_is_not_empty(self):
        with self.assertRaisesRegex(ValueError, 'company-name cell'):
            read_company_relations(section('계열회사 현황(상세)',
                '<TR><TH>기업명</TH><TH>소재국</TH></TR><TR><TD></TD><TD>한국</TD></TR>') + EMPTY)
