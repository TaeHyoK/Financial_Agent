"""Distinct DART identifiers must never collapse through digit stripping."""
import sys
import unittest
from unittest.mock import MagicMock, patch
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from orchestration.company_resolver import (
    _stock_code, parse_dart_company_directory,
    resolve_company_identity_by_stock_code, CompanyResolutionError,
    resolve_naver_market,
)


class CompanyCodeTests(unittest.TestCase):
    def test_naver_legacy_and_redirected_board_metadata(self):
        for html, expected in (
            ('<img src="btn_kospi.gif">', 'KOSPI'),
            ('{"sosokData":"KOSDAQ"}', 'KOSDAQ'),
            (r'{\"sosokData\":\"KOSPI\"}', 'KOSPI'),
            ('KOSPI KOSDAQ market menu', ''),
            ('{"sosokData":"KOSPI"} {"sosokData":"KOSDAQ"}', ''),
        ):
            with self.subTest(html=html):
                response = MagicMock()
                response.read.return_value = html.encode()
                response.headers.get_content_charset.return_value = 'utf-8'
                response.__enter__.return_value = response
                with patch('urllib.request.urlopen', return_value=response):
                    self.assertEqual(resolve_naver_market('090430'), expected)

    def test_normalization_preserves_alphanumeric_identifiers(self):
        self.assertEqual(_stock_code('0010F0'), '0010F0')
        self.assertEqual(_stock_code('0010v0'), '0010V0')
        self.assertEqual(_stock_code('000100'), '000100')
        self.assertEqual(_stock_code(100), '000100')
        for invalid in ('', None, '000100.KS', '00-0100', '1234567'):
            self.assertEqual(_stock_code(invalid), '')

    def test_directory_and_lookup_do_not_alias_yuhan(self):
        xml = '''<result>
          <list><corp_code>00145109</corp_code><corp_name>유한양행</corp_name><stock_code>000100</stock_code></list>
          <list><corp_code>00871587</corp_code><corp_name>보원케미칼</corp_name><stock_code>0010F0</stock_code></list>
          <list><corp_code>00185301</corp_code><corp_name>제이피아이헬스케어</corp_name><stock_code>0010V0</stock_code></list>
        </result>'''
        directory = parse_dart_company_directory(xml.encode())
        self.assertEqual(len({r['stock_code'] for r in directory}), 3)
        identity = resolve_company_identity_by_stock_code(
            '000100', selected_date='20251106', directory=directory,
            market_resolver=lambda code, day: 'KOSPI',
        )
        self.assertEqual(identity.company_name, '유한양행')
        self.assertEqual(identity.corp_code, '00145109')
        self.assertEqual(identity.ticker, '000100.KS')

    def test_true_ambiguity_still_fails(self):
        directory = [
            {'company_name': name, 'corp_code': str(i), 'stock_code': '000100'}
            for i, name in enumerate(('first', 'second'))
        ]
        with self.assertRaises(CompanyResolutionError):
            resolve_company_identity_by_stock_code('000100', selected_date='20251106', directory=directory)


if __name__ == '__main__':
    unittest.main()
