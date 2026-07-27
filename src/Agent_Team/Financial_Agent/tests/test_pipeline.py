"""Deterministic tests for the DART collection pipeline core rules."""

from __future__ import annotations

import copy
import sys
import unittest
from datetime import date
from pathlib import Path
from unittest.mock import patch

from bs4 import BeautifulSoup

ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = Path(__file__).resolve().parents[3]
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from Agent_Team.Financial_Agent.handoff_builder import (
    build_2y_handoff,
    build_single_report_canonical,
    build_trend_canonical,
)
from Agent_Team.Financial_Agent.financial_index_calculator import calculate_financial_index
from Agent_Team.Financial_Agent.langgraph_flow import build_financial_trends
from Agent_Team.Financial_Agent import build_run_key
from Agent_Team.Financial_Agent.main import _build_master, _resolve_output_dir, _write_outputs, collect_report
from Agent_Team.Financial_Agent.models import Filing, PipelineInput, SectionMap, TargetReport
from Agent_Team.Financial_Agent.normalizer import normalize_primary_report
from Agent_Team.Financial_Agent.report_resolver import (
    build_available_report_candidates,
    build_targets,
    resolve_report_set,
)
from Agent_Team.Financial_Agent.report_selector import parse_period_end_from_report_name, select_latest_valid_filing
from Agent_Team.Financial_Agent.revenue_breakdown_extractor import extract_revenue_breakdown
from Agent_Team.Financial_Agent.share_information_extractor import extract_share_information
from Agent_Team.Financial_Agent.SY_Agent.run_pipeline import resolve_pipeline_output_dir
from Agent_Team.Financial_Agent.table_parser import parse_table_matrix


class ReportResolutionTests(unittest.TestCase):
    """Verify selected_date maps to theoretical fiscal targets."""

    def test_selected_date_mapping(self) -> None:
        cases = [
            (date(2025, 1, 15), "annual", date(2024, 12, 31), date(2023, 12, 31)),
            (date(2025, 4, 1), "q1", date(2025, 3, 31), date(2024, 12, 31)),
            (date(2025, 7, 31), "half", date(2025, 6, 30), date(2024, 12, 31)),
            (date(2025, 10, 31), "q3", date(2025, 9, 30), date(2024, 12, 31)),
        ]
        for selected_date, primary_type, primary_end, secondary_end in cases:
            with self.subTest(selected_date=selected_date):
                primary, secondary = build_targets(selected_date)
                self.assertEqual(primary.period_type, primary_type)
                self.assertEqual(primary.period_end, primary_end)
                self.assertEqual(secondary.period_type, "annual")
                self.assertEqual(secondary.period_end, secondary_end)

    def test_available_candidates_are_ordered_by_period_end(self) -> None:
        candidates = build_available_report_candidates(date(2025, 10, 31))

        self.assertEqual(
            [(item.period_type, item.period_end) for item in candidates[:4]],
            [
                ("q3", date(2025, 9, 30)),
                ("half", date(2025, 6, 30)),
                ("q1", date(2025, 3, 31)),
                ("annual", date(2024, 12, 31)),
            ],
        )

    def test_report_set_falls_back_to_half_year_and_excludes_future_q3(self) -> None:
        client = _PointInTimeFakeClient()

        resolved = resolve_report_set(
            client=client,
            company_code="00878696",
            selected_date=date(2025, 10, 31),
        )

        self.assertEqual(resolved["primary"][0].period_type, "half")
        self.assertEqual(resolved["primary"][1].rcept_no, "20250814001203")
        self.assertEqual(resolved["same_period_previous"][0].period_end, date(2024, 6, 30))
        self.assertEqual(resolved["annual_history"][0].period_end, date(2024, 12, 31))
        self.assertTrue(all(end_de <= "20251031" for end_de in client.requested_end_dates))

    def test_q3_is_not_available_on_its_receipt_date_without_receipt_time(self) -> None:
        resolved = resolve_report_set(
            client=_PointInTimeFakeClient(),
            company_code="00878696",
            selected_date=date(2025, 11, 14),
        )

        self.assertEqual(resolved["primary"][0].period_type, "half")
        self.assertEqual(resolved["primary"][1].rcept_no, "20250814001203")

    def test_q3_becomes_available_on_the_day_after_receipt(self) -> None:
        resolved = resolve_report_set(
            client=_PointInTimeFakeClient(),
            company_code="00878696",
            selected_date=date(2025, 11, 15),
        )

        self.assertEqual(resolved["primary"][0].period_type, "q3")
        self.assertEqual(resolved["primary"][1].rcept_no, "20251114001570")


class OutputPathTests(unittest.TestCase):
    """Verify per-company output directories are shared by DART and agent pipeline runs."""

    def test_shared_run_key_uses_company_and_selected_date(self) -> None:
        self.assertEqual(build_run_key("삼성전자", "2025-10-31", "005930"), "삼성전자_20251031")

    def test_dart_output_dir_defaults_to_company_date_folder(self) -> None:
        pipeline_input = PipelineInput(
            company_code="005930",
            company_name="삼성전자",
            ticker="005930.KS",
            report_type="quarterly",
            date_range="20241101-20251031",
            selected_date=date(2025, 10, 31),
            max_retries=1,
        )

        self.assertEqual(_resolve_output_dir(None, pipeline_input).name, "삼성전자_20251031")

    def test_agent_pipeline_output_dir_defaults_to_manifest_company_date_folder(self) -> None:
        import json
        import tempfile

        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            output_root = root / "Output_total" / "Financial"
            manifest_path = root / "manifest.json"
            manifest_path.write_text(
                json.dumps(
                    {
                        "target_entity": {
                            "company_name": "삼성전자",
                            "corp_code": "005930",
                            "as_of_date": "2025-10-31",
                        }
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )

            output_dir = resolve_pipeline_output_dir(financial_manifest=manifest_path, output_root=output_root)

            self.assertEqual(output_dir, output_root / "삼성전자_20251031" / "agent_pipeline")


class ReportSelectorTests(unittest.TestCase):
    """Verify filing period matching uses period-end logic."""

    def test_parse_period_end_from_report_name(self) -> None:
        self.assertEqual(
            parse_period_end_from_report_name("[기재정정]분기보고서 (2025.09)"),
            date(2025, 9, 30),
        )
        self.assertEqual(parse_period_end_from_report_name("반기보고서 (2025.06)"), date(2025, 6, 30))
        self.assertEqual(parse_period_end_from_report_name("사업보고서 (2024.12)"), date(2024, 12, 31))

    def test_select_latest_valid_filing_for_target_period(self) -> None:
        target = TargetReport(
            role="primary",
            fiscal_year=2025,
            period_type="q3",
            period_end=date(2025, 9, 30),
            dart_detail_type="A003",
            report_keyword="분기보고서",
        )
        filings = [
            Filing(rcept_no="1", report_nm="분기보고서 (2025.03)", rcept_dt="20250515"),
            Filing(rcept_no="2", report_nm="분기보고서 (2025.09)", rcept_dt="20251114"),
            Filing(rcept_no="3", report_nm="[기재정정]분기보고서 (2025.09)", rcept_dt="20251120"),
        ]
        selected = select_latest_valid_filing(filings, target)
        self.assertEqual(selected.rcept_no, "3")

    def test_select_latest_valid_filing_applies_as_of_cutoff(self) -> None:
        target = _q3_target()
        filings = [
            Filing(rcept_no="1", report_nm="분기보고서 (2025.09)", rcept_dt="20251114"),
            Filing(rcept_no="2", report_nm="[기재정정]분기보고서 (2025.09)", rcept_dt="20251120"),
        ]

        with self.assertRaises(LookupError):
            select_latest_valid_filing(filings, target, as_of_date=date(2025, 11, 14))

        selected = select_latest_valid_filing(filings, target, as_of_date=date(2025, 11, 15))

        self.assertEqual(selected.rcept_no, "1")

    def test_select_latest_valid_filing_skips_attachment_only_correction(self) -> None:
        target = _annual_target_for_test("annual_history", 2024)
        filings = [
            Filing("20250814002739", "[기재정정]사업보고서 (2024.12)", "20250814"),
            Filing("20250814002850", "[첨부정정]사업보고서 (2024.12)", "20250814"),
        ]

        selected = select_latest_valid_filing(filings, target, as_of_date=date(2025, 10, 31))

        self.assertEqual(selected.rcept_no, "20250814002739")


class TableParserTests(unittest.TestCase):
    """Verify table parsing preserves expanded structure."""

    def test_parse_table_matrix_expands_rowspan_and_colspan(self) -> None:
        html = """
        <table>
          <tr><th rowspan="2">과목</th><th colspan="2">제 15 기 3분기</th></tr>
          <tr><th>3개월</th><th>누적</th></tr>
          <tr><td>매출액</td><td>10</td><td>30</td></tr>
        </table>
        """
        soup = BeautifulSoup(html, "html.parser")
        matrix = parse_table_matrix(soup.find("table"))
        self.assertEqual(
            matrix,
            [
                ["과목", "제 15 기 3분기", "제 15 기 3분기"],
                ["과목", "3개월", "누적"],
                ["매출액", "10", "30"],
            ],
        )


class RevenueBreakdownTests(unittest.TestCase):
    def test_extracts_product_service_revenue_and_share(self) -> None:
        xml = """
        <DOCUMENT><SECTION-2>
          <TITLE>2. 주요 제품 및 서비스</TITLE>
          <TABLE><TR><TD>(단위: 백만원)</TD></TR></TABLE>
          <TABLE>
            <TR><TH>품목</TH><TH colspan="2">제15기 반기말(2025년 06월말)</TH><TH colspan="2">제14기 기말(2024년 12월말)</TH></TR>
            <TR><TH>품목</TH><TH>매출액</TH><TH>비율</TH><TH>매출액</TH><TH>비율</TH></TR>
            <TR><TD>제품 A</TD><TD>304,962</TD><TD>95.1%</TD><TD>531,241</TD><TD>97.0%</TD></TR>
            <TR><TD>서비스 B</TD><TD>15,692</TD><TD>4.9%</TD><TD>16,355</TD><TD>3.0%</TD></TR>
            <TR><TD>계</TD><TD>320,654</TD><TD>100%</TD><TD>547,596</TD><TD>100%</TD></TR>
          </TABLE>
        </SECTION-2></DOCUMENT>
        """
        target = _half_target("primary", 2025)
        filing = Filing("H1-2025", "반기보고서 (2025.06)", "20250814")

        result = extract_revenue_breakdown(xml, target=target, filing=filing)

        self.assertEqual(result["status"], "available")
        self.assertEqual(result["dimension_type"], "product_service")
        self.assertEqual(result["unit"], "백만원")
        self.assertEqual(result["current_period"]["period_end"], "2025-06-30")
        self.assertEqual(result["current_items"][0]["name"], "제품 A")
        self.assertEqual(result["current_items"][0]["revenue"], 304962)
        self.assertEqual(result["current_items"][0]["revenue_krw"], 304_962_000_000)
        self.assertEqual(result["current_items"][0]["revenue_share"], 0.951)
        self.assertTrue(all(result["validation"]["share_sum_within_tolerance"].values()))
        _assert_no_key_recursive(result, "market_share")

    def test_missing_product_section_is_non_fatal(self) -> None:
        result = extract_revenue_breakdown(
            "<DOCUMENT><TITLE>4. 재무제표</TITLE></DOCUMENT>",
            target=_half_target("primary", 2025),
            filing=Filing("H1-2025", "반기보고서 (2025.06)", "20250814"),
        )

        self.assertEqual(result["status"], "not_disclosed")
        self.assertEqual(result["reason"], "section_not_found")
        self.assertEqual(result["current_items"], [])


class ShareInformationTests(unittest.TestCase):
    def test_extracts_outstanding_shares_from_exact_filing_table(self) -> None:
        xml = """
        <DOCUMENT><SECTION-2>
          <TITLE>4. 주식의 총수 등</TITLE>
          <TABLE>
            <TR><TH>구분</TH><TH>구분</TH><TH>의결권 있는 주식</TH><TH>합계</TH></TR>
            <TR><TH>구분</TH><TH>구분</TH><TH>의결권 있는 주식</TH><TH>합계</TH></TR>
            <TR><TD>II. 현재까지 발행한 주식의 총수</TD><TD>II. 현재까지 발행한 주식의 총수</TD><TD>80,000,000</TD><TD>80,000,000</TD></TR>
            <TR><TD>IV. 발행주식의 총수</TD><TD>IV. 발행주식의 총수</TD><TD>78,313,250</TD><TD>78,313,250</TD></TR>
            <TR><TD>V. 자기주식수</TD><TD>V. 자기주식수</TD><TD>1,000</TD><TD>1,000</TD></TR>
            <TR><TD>VI. 유통주식수</TD><TD>VI. 유통주식수</TD><TD>78,312,250</TD><TD>78,312,250</TD></TR>
          </TABLE>
        </SECTION-2></DOCUMENT>
        """

        result = extract_share_information(
            xml,
            target=_half_target("primary", 2025),
            filing=Filing("H1-2025", "반기보고서 (2025.06)", "20250814"),
        )

        self.assertEqual(result["status"], "available")
        self.assertEqual(result["issued_shares"], 78_313_250)
        self.assertEqual(result["treasury_shares"], 1_000)
        self.assertEqual(result["shares_outstanding"], 78_312_250)
        self.assertTrue(result["validation"]["issued_minus_treasury_equals_outstanding"])
        self.assertEqual(result["source"]["receipt_date"], "2025-08-14")

    def test_missing_share_section_is_non_fatal(self) -> None:
        result = extract_share_information(
            "<DOCUMENT><TITLE>4. 재무제표</TITLE></DOCUMENT>",
            target=_half_target("primary", 2025),
            filing=Filing("H1-2025", "반기보고서 (2025.06)", "20250814"),
        )

        self.assertEqual(result["status"], "not_disclosed")
        self.assertIsNone(result["shares_outstanding"])


class NormalizerTests(unittest.TestCase):
    """Verify primary periodic normalization rules."""

    def test_normalize_primary_periodic_report_with_dart_generation_labels(self) -> None:
        raw = _sample_section_map()
        raw_before = copy.deepcopy(raw)
        target = TargetReport(
            role="primary",
            fiscal_year=2025,
            period_type="q3",
            period_end=date(2025, 9, 30),
            dart_detail_type="A003",
            report_keyword="분기보고서",
        )

        normalized = normalize_primary_report(raw, target)

        self.assertEqual(raw, raw_before)
        self.assertEqual(normalized["4-1"]["tables"][0]["matrix"][0], ["", "제 15 기 3분기말"])
        self.assertEqual(normalized["4-1"]["tables"][0]["matrix"][1], ["자산총계", "100"])

        self.assertEqual(normalized["4-2"]["tables"][0]["matrix"][0], ["", "제 15 기 3분기"])
        self.assertEqual(normalized["4-2"]["tables"][0]["matrix"][1], ["", "누적"])
        self.assertEqual(normalized["4-2"]["tables"][0]["matrix"][2], ["매출액", "300"])

        equity_labels = [row[0] for row in normalized["4-3"]["tables"][0]["matrix"]]
        self.assertIn("2025.01.01 (기초자본)", equity_labels)
        self.assertIn("2025.09.30 (분기말자본)", equity_labels)
        self.assertNotIn("2024.01.01 (기초자본)", equity_labels)

        self.assertEqual(normalized["4-4"]["tables"][0]["matrix"][0], ["", "제 15 기 3분기"])
        self.assertEqual(normalized["4-4"]["tables"][0]["matrix"][1], ["영업활동현금흐름", "50"])

    def test_4_1_balance_sheet_keeps_current_period_date_column_for_all_tables(self) -> None:
        raw = _empty_section_map()
        raw["4-1"] = {
            "section_title": "재무상태표",
            "tables": [
                {
                    "table_title": "연결 재무상태표",
                    "matrix": [["과목", "2025-09-30", "2024-12-31"], ["자산총계", "100", "90"]],
                },
                {
                    "table_title": "별도 재무상태표",
                    "matrix": [["과목", "제 15 기 3분기말", "제 14 기말"], ["자산총계", "80", "70"]],
                },
            ],
        }
        normalized = normalize_primary_report(raw, _q3_target())

        self.assertEqual(normalized["4-1"]["tables"][0]["matrix"], [["과목", "2025-09-30"], ["자산총계", "100"]])
        self.assertEqual(normalized["4-1"]["tables"][1]["matrix"], [["과목", "제 15 기 3분기말"], ["자산총계", "80"]])

    def test_4_2_income_statement_keeps_only_current_ytd_date_range(self) -> None:
        raw = _empty_section_map()
        raw["4-2"] = {
            "section_title": "포괄손익계산서",
            "tables": [
                {
                    "table_title": "포괄손익계산서",
                    "matrix": [
                        [
                            "과목",
                            "2025-07-01 ~ 2025-09-30",
                            "2025-01-01 ~ 2025-09-30",
                            "2024-07-01 ~ 2024-09-30",
                            "2024-01-01 ~ 2024-09-30",
                        ],
                        ["매출액", "100", "300", "80", "240"],
                    ],
                }
            ],
        }
        normalized = normalize_primary_report(raw, _q3_target())

        self.assertEqual(
            normalized["4-2"]["tables"][0]["matrix"],
            [["과목", "2025-01-01 ~ 2025-09-30"], ["매출액", "300"]],
        )

    def test_4_3_equity_statement_removes_prior_same_period_row_block(self) -> None:
        raw = _empty_section_map()
        raw["4-3"] = _sample_section_map()["4-3"]
        normalized = normalize_primary_report(raw, _q3_target())

        labels = [row[0] for row in normalized["4-3"]["tables"][0]["matrix"]]
        self.assertEqual(
            labels,
            ["", "", "2025.01.01 (기초자본)", "총포괄손익", "2025.09.30 (분기말자본)"],
        )

    def test_4_4_cash_flow_keeps_only_current_ytd_date_range(self) -> None:
        raw = _empty_section_map()
        raw["4-4"] = {
            "section_title": "현금흐름표",
            "tables": [
                {
                    "table_title": "현금흐름표",
                    "matrix": [
                        [
                            "과목",
                            "2025-01-01 ~ 2025-09-30",
                            "2024-01-01 ~ 2024-09-30",
                        ],
                        ["영업활동현금흐름", "50", "40"],
                    ],
                }
            ],
        }
        normalized = normalize_primary_report(raw, _q3_target())

        self.assertEqual(
            normalized["4-4"]["tables"][0]["matrix"],
            [["과목", "2025-01-01 ~ 2025-09-30"], ["영업활동현금흐름", "50"]],
        )

    def test_primary_annual_normalized_equals_raw(self) -> None:
        raw = _sample_section_map()
        target = TargetReport(
            role="primary",
            fiscal_year=2024,
            period_type="annual",
            period_end=date(2024, 12, 31),
            dart_detail_type="A001",
            report_keyword="사업보고서",
        )
        normalized = normalize_primary_report(raw, target)
        self.assertEqual(normalized, raw)
        self.assertIsNot(normalized, raw)


class HandoffBuilderTests(unittest.TestCase):
    """Verify canonical two-year statement generation."""

    def test_collection_context_marks_same_day_receipt_as_excluded_by_policy(self) -> None:
        target = _half_target("primary", 2025)
        canonical = build_trend_canonical(
            {"primary": _period_section_map("제 15 기 반기", "300")},
            {
                "primary": (
                    target,
                    Filing("H1-2025", "반기보고서 (2025.06)", "20250814"),
                )
            },
            selected_date=date(2025, 8, 14),
            theoretical_target=target,
            annual_history_limit=0,
        )

        context = canonical["collection_context"]
        self.assertEqual(
            context["selected_date_policy"],
            "prior_day_cutoff_when_receipt_time_unavailable",
        )
        self.assertFalse(context["future_filing_excluded"])

    def test_single_report_canonical_keeps_only_current_period(self) -> None:
        current = _empty_section_map()
        current["4-1"] = {
            "section_title": "재무상태표",
            "tables": [{"table_title": "재무상태표", "matrix": [["과목", "제 15 기 3분기말"], ["유동자산", "100"]]}],
        }
        current["4-2"] = {
            "section_title": "포괄손익계산서",
            "tables": [{"table_title": "포괄손익계산서", "matrix": [["과목", "제 15 기 3분기"], ["매출액", "300"]]}],
        }

        handoff = build_single_report_canonical({"primary": current}, _q3_target())

        balance_table = handoff["4-1"]["tables"][0]
        income_table = handoff["4-2"]["tables"][0]
        self.assertEqual(list(balance_table["periods"].keys()), ["current_fiscal_year"])
        self.assertNotIn("previous_fiscal_year", balance_table["periods"])
        self.assertEqual(balance_table["periods"]["current_fiscal_year"]["basis"], "POINT_IN_TIME")
        self.assertEqual(income_table["periods"]["current_fiscal_year"]["basis"], "YTD")
        self.assertEqual(income_table["items_by_key"]["revenue"]["values_by_period_key"], {"current_fiscal_year": "300"})

    def test_trend_canonical_separates_same_period_and_annual_history(self) -> None:
        master = {
            "primary": _period_section_map("제 15 기 반기", "300"),
            "same_period_previous": _period_section_map("제 14 기 반기", "240"),
            "annual_history": _annual_history_section_map(),
        }
        resolved = {
            "primary": (
                _half_target("primary", 2025),
                Filing("H1-2025", "반기보고서 (2025.06)", "20250814"),
            ),
            "same_period_previous": (
                _half_target("same_period_previous", 2024),
                Filing("H1-2024", "반기보고서 (2024.06)", "20240814"),
            ),
            "annual_history": (
                _annual_target_for_test("annual_history", 2024),
                Filing("FY-2024", "사업보고서 (2024.12)", "20250318"),
            ),
        }

        canonical = build_trend_canonical(
            master,
            resolved,
            selected_date=date(2025, 10, 31),
            theoretical_target=_q3_target(),
            annual_history_limit=3,
        )

        periods = canonical["4-2"]["tables"][0]["periods"]
        self.assertEqual(
            list(periods),
            [
                "current_fiscal_year",
                "same_period_previous_year",
                "previous_fiscal_year",
                "previous_fiscal_year_2",
                "previous_fiscal_year_3",
            ],
        )
        self.assertEqual(periods["current_fiscal_year"]["period_type"], "HALF")
        self.assertEqual(periods["same_period_previous_year"]["period_type"], "HALF")
        self.assertEqual(periods["current_fiscal_year"]["receipt_date"], "2025-08-14")
        self.assertEqual(periods["previous_fiscal_year"]["basis"], "FULL_YEAR")
        item = canonical["4-2"]["tables"][0]["items_by_key"]["revenue"]
        self.assertEqual(item["current_value"], "300")
        self.assertEqual(item["previous_value"], "240")
        self.assertEqual(item["values_by_period_key"]["previous_fiscal_year_3"], "300")
        self.assertTrue(canonical["collection_context"]["fallback_applied"])
        self.assertTrue(canonical["collection_context"]["future_filing_excluded"])

        financial_index = calculate_financial_index(canonical, ["Revenue", "Revenue Growth"])
        comparison_keys = [item["comparison_key"] for item in financial_index["comparison_pairs"]]
        self.assertEqual(
            comparison_keys,
            [
                "2025_HALF_vs_2024_HALF",
                "2024_ANNUAL_vs_2023_ANNUAL",
                "2023_ANNUAL_vs_2022_ANNUAL",
            ],
        )
        self.assertNotIn("2025_HALF_vs_2024_ANNUAL", comparison_keys)
        ttm_revenue = financial_index["metrics_by_key"]["revenue"]["values_by_period"]["ttm"]
        self.assertEqual(ttm_revenue["value"], 560)
        self.assertEqual(ttm_revenue["period"]["basis"], "TTM")
        trends = build_financial_trends(financial_index)
        self.assertEqual(trends["current_vs_same_period"]["current_values"]["revenue"], 300)
        self.assertEqual(trends["current_vs_same_period"]["previous_values"]["revenue"], 240)
        self.assertEqual(trends["ttm"]["values"]["revenue"], 560)

    def test_4_1_pairs_items_and_retains_missing_side_items(self) -> None:
        current = _empty_section_map()
        secondary = _empty_section_map()
        current["4-1"] = {
            "section_title": "재무상태표",
            "tables": [
                {
                    "table_title": "재무상태표",
                    "matrix": [
                        ["과목", "제 15 기 3분기말"],
                        ["유동자산", "100"],
                        ["당기만 있는 항목", "7"],
                    ],
                }
            ],
        }
        secondary["4-1"] = {
            "section_title": "재무상태표",
            "tables": [
                {
                    "table_title": "재무상태표",
                    "matrix": [
                        ["과목", "제 14 기", "제 13 기"],
                        ["유동자산", "90", "70"],
                        ["전기만 있는 항목", "5", "4"],
                    ],
                }
            ],
        }
        master = {"primary": current, "secondary": copy.deepcopy(secondary)}
        handoff = build_2y_handoff(master, _secondary_annual_target())

        table = handoff["4-1"]["tables"][0]
        self.assertEqual(table["table_title"], "재무상태표")
        self.assertEqual(table["table_key"], "balance_sheet_1")
        self.assertEqual(
            table["periods"]["current_fiscal_year"],
            {
                "label": "제 15 기 3분기말",
                "fiscal_year": 2025,
                "period_type": "Q3",
                "period_end": "2025-09-30",
                "basis": "POINT_IN_TIME",
            },
        )
        self.assertEqual(
            table["periods"]["previous_fiscal_year"],
            {
                "label": "제 14 기",
                "fiscal_year": 2024,
                "period_type": "ANNUAL",
                "period_end": "2024-12-31",
                "basis": "POINT_IN_TIME",
            },
        )
        self.assertEqual(table["unit"], "원")
        self.assertNotIn("scope", table)
        self.assertIn("items_by_key", table)
        self.assertIn("item_order", table)
        self.assertEqual(table["items_by_key"]["current_assets"]["display_name"], "유동자산")
        self.assertEqual(table["items_by_key"]["current_assets"]["aliases"], ["유동자산"])
        self.assertEqual(table["items_by_key"]["current_assets"]["current_value"], "100")
        self.assertEqual(table["items_by_key"]["current_assets"]["current_numeric"], 100)
        self.assertEqual(table["items_by_key"]["current_assets"]["previous_value"], "90")
        self.assertEqual(table["items_by_key"]["current_assets"]["previous_numeric"], 90)
        current_only = _find_item_by_display_name(table, "당기만 있는 항목")
        previous_only = _find_item_by_display_name(table, "전기만 있는 항목")
        self.assertEqual(current_only["current_value"], "7")
        self.assertIsNone(current_only["previous_value"])
        self.assertIsNone(previous_only["current_value"])
        self.assertEqual(previous_only["previous_value"], "5")

    def test_4_2_uses_ytd_and_full_year_basis_with_item_rows(self) -> None:
        current = _empty_section_map()
        secondary = _empty_section_map()
        current["4-2"] = {
            "section_title": "포괄손익계산서",
            "tables": [{"table_title": "포괄손익계산서", "matrix": [["과목", "제 15 기 3분기"], ["매출액", "300"]]}],
        }
        secondary["4-2"] = {
            "section_title": "포괄손익계산서",
            "tables": [{"table_title": "포괄손익계산서", "matrix": [["과목", "제 14 기", "제 13 기"], ["매출액", "400", "300"]]}],
        }
        handoff = build_2y_handoff({"primary": current, "secondary": secondary}, _secondary_annual_target())

        table = handoff["4-2"]["tables"][0]
        self.assertEqual(table["periods"]["current_fiscal_year"]["basis"], "YTD")
        self.assertEqual(table["periods"]["current_fiscal_year"]["period_type"], "Q3")
        self.assertEqual(table["periods"]["current_fiscal_year"]["period_end"], "2025-09-30")
        self.assertEqual(table["periods"]["previous_fiscal_year"]["basis"], "FULL_YEAR")
        self.assertEqual(table["periods"]["previous_fiscal_year"]["period_type"], "ANNUAL")
        self.assertEqual(table["unit"], "원")
        self.assertEqual(table["items_by_key"]["revenue"]["display_name"], "매출액")
        self.assertEqual(table["items_by_key"]["revenue"]["current_value"], "300")
        self.assertEqual(table["items_by_key"]["revenue"]["current_numeric"], 300)
        self.assertEqual(table["items_by_key"]["revenue"]["previous_value"], "400")
        self.assertEqual(table["items_by_key"]["revenue"]["previous_numeric"], 400)
        self.assertEqual(table["item_order"], ["revenue"])

    def test_4_4_uses_ytd_and_full_year_basis_with_item_rows(self) -> None:
        current = _empty_section_map()
        secondary = _empty_section_map()
        current["4-4"] = {
            "section_title": "현금흐름표",
            "tables": [{"table_title": "현금흐름표", "matrix": [["과목", "제 15 기 3분기"], ["영업활동현금흐름", "50"]]}],
        }
        secondary["4-4"] = {
            "section_title": "현금흐름표",
            "tables": [{"table_title": "현금흐름표", "matrix": [["과목", "제 14 기", "제 13 기"], ["영업활동현금흐름", "60", "40"]]}],
        }
        handoff = build_2y_handoff({"primary": current, "secondary": secondary}, _secondary_annual_target())

        table = handoff["4-4"]["tables"][0]
        self.assertEqual(table["periods"]["current_fiscal_year"]["basis"], "YTD")
        self.assertEqual(table["periods"]["previous_fiscal_year"]["basis"], "FULL_YEAR")
        self.assertEqual(table["unit"], "원")
        key = "cash_flows_from_operating_activities"
        self.assertEqual(table["items_by_key"][key]["display_name"], "영업활동현금흐름")
        self.assertEqual(table["items_by_key"][key]["current_value"], "50")
        self.assertEqual(table["items_by_key"][key]["current_numeric"], 50)
        self.assertEqual(table["items_by_key"][key]["previous_value"], "60")
        self.assertEqual(table["items_by_key"][key]["previous_numeric"], 60)

    def test_4_3_produces_period_blocks_and_trims_older_history(self) -> None:
        current = normalize_primary_report(_sample_section_map(), _q3_target())
        secondary = _secondary_annual_section_map()
        handoff = build_2y_handoff({"primary": current, "secondary": secondary}, _secondary_annual_target())

        table = handoff["4-3"]["tables"][0]
        self.assertEqual(table["unit"], "원")
        self.assertNotIn("scope", table)
        current_block = table["period_blocks"]["current_fiscal_year"]
        previous_block = table["period_blocks"]["previous_fiscal_year"]
        self.assertEqual(current_block["basis"], "YTD")
        self.assertEqual(current_block["label"], "2025.01.01~2025.09.30")
        self.assertEqual(current_block["fiscal_year"], 2025)
        self.assertEqual(current_block["period_type"], "Q3")
        self.assertEqual(current_block["period_end"], "2025-09-30")
        self.assertEqual(previous_block["basis"], "FULL_YEAR")
        self.assertEqual(previous_block["label"], "2024.01.01~2024.12.31")
        self.assertEqual(previous_block["fiscal_year"], 2024)
        self.assertEqual(previous_block["period_type"], "ANNUAL")
        self.assertEqual(previous_block["period_end"], "2024-12-31")
        self.assertIn("columns_by_key", current_block)
        self.assertIn("column_order", current_block)
        self.assertIn("rows_by_key", current_block)
        self.assertIn("row_order", current_block)
        self.assertEqual(current_block["column_order"], ["share_capital", "total_equity"])
        beginning_row = current_block["rows_by_key"]["beginning_balance"]
        self.assertEqual(beginning_row["display_name"], "2025.01.01 (기초자본)")
        self.assertEqual(beginning_row["values_by_column_key"], {"share_capital": "10", "total_equity": "25"})
        self.assertEqual(beginning_row["numeric_values_by_column_key"], {"share_capital": 10, "total_equity": 25})
        previous_row_names = [
            previous_block["rows_by_key"][row_key]["display_name"]
            for row_key in previous_block["row_order"]
        ]
        self.assertEqual(previous_row_names, ["2024.01.01 (기초자본)", "2024.12.31 (기말자본)"])
        self.assertNotIn("2023.01.01 (기초자본)", previous_row_names)

    def test_secondary_historical_columns_are_trimmed_to_immediately_previous_year(self) -> None:
        current = _empty_section_map()
        secondary = _empty_section_map()
        current["4-1"] = {
            "section_title": "재무상태표",
            "tables": [{"table_title": "재무상태표", "matrix": [["과목", "제 15 기 3분기말"], ["자산총계", "100"]]}],
        }
        secondary["4-1"] = {
            "section_title": "재무상태표",
            "tables": [{"table_title": "재무상태표", "matrix": [["과목", "제 14 기", "제 13 기", "제 12 기"], ["자산총계", "90", "80", "70"]]}],
        }
        handoff = build_2y_handoff({"primary": current, "secondary": secondary}, _secondary_annual_target())

        table = handoff["4-1"]["tables"][0]
        self.assertEqual(table["periods"]["previous_fiscal_year"]["label"], "제 14 기")
        self.assertEqual(table["items_by_key"]["total_assets"]["previous_value"], "90")

    def test_safe_alias_merging_for_items(self) -> None:
        current = _empty_section_map()
        secondary = _empty_section_map()
        current["4-2"] = {
            "section_title": "포괄손익계산서",
            "tables": [
                {
                    "table_title": "포괄손익계산서",
                    "matrix": [
                        ["과목", "제 15 기 3분기"],
                        ["영업이익", "30"],
                        ["기초 현금및현금성자산", "10"],
                        ["지분법 자본변동", "5"],
                    ],
                }
            ],
        }
        secondary["4-2"] = {
            "section_title": "포괄손익계산서",
            "tables": [
                {
                    "table_title": "포괄손익계산서",
                    "matrix": [
                        ["과목", "제 14 기"],
                        ["영업이익(손실)", "20"],
                        ["기초현금및현금성자산", "8"],
                        ["지분법자본변동", "4"],
                    ],
                }
            ],
        }
        handoff = build_2y_handoff({"primary": current, "secondary": secondary}, _secondary_annual_target())
        items = handoff["4-2"]["tables"][0]["items_by_key"]

        self.assertEqual(items["operating_profit"]["aliases"], ["영업이익", "영업이익(손실)"])
        self.assertEqual(items["operating_profit"]["current_value"], "30")
        self.assertEqual(items["operating_profit"]["previous_value"], "20")
        self.assertEqual(items["beginning_cash_and_cash_equivalents"]["aliases"], ["기초 현금및현금성자산", "기초현금및현금성자산"])
        self.assertEqual(items["equity_method_capital_changes"]["aliases"], ["지분법 자본변동", "지분법자본변동"])

    def test_revenue_alias_merges_sales_and_revenue_sales_labels(self) -> None:
        current = _empty_section_map()
        previous = _empty_section_map()
        current["4-2"] = {
            "section_title": "포괄손익계산서",
            "tables": [{"table_title": "포괄손익계산서", "matrix": [["과목", "2025 반기"], ["매출액", "300"]]}],
        }
        previous["4-2"] = {
            "section_title": "포괄손익계산서",
            "tables": [{"table_title": "포괄손익계산서", "matrix": [["과목", "2024 반기"], ["수익(매출액)", "240"]]}],
        }

        handoff = build_2y_handoff(
            {"primary": current, "secondary": previous},
            _secondary_annual_target(),
        )

        revenue = handoff["4-2"]["tables"][0]["items_by_key"]["revenue"]
        self.assertEqual(revenue["aliases"], ["매출액", "수익(매출액)"])
        self.assertEqual(revenue["current_value"], "300")
        self.assertEqual(revenue["previous_value"], "240")

    def test_parenthesized_amounts_are_negative_numeric_values(self) -> None:
        current = _empty_section_map()
        secondary = _empty_section_map()
        current["4-2"] = {
            "section_title": "포괄손익계산서",
            "tables": [{"table_title": "포괄손익계산서", "matrix": [["과목", "제 15 기 3분기"], ["영업이익", "(1,234)"]]}],
        }
        secondary["4-2"] = {
            "section_title": "포괄손익계산서",
            "tables": [{"table_title": "포괄손익계산서", "matrix": [["과목", "제 14 기"], ["영업이익(손실)", "2,345"]]}],
        }
        handoff = build_2y_handoff({"primary": current, "secondary": secondary}, _secondary_annual_target())
        item = handoff["4-2"]["tables"][0]["items_by_key"]["operating_profit"]

        self.assertEqual(item["current_value"], "(1,234)")
        self.assertEqual(item["current_numeric"], -1234)
        self.assertEqual(item["previous_value"], "2,345")
        self.assertEqual(item["previous_numeric"], 2345)

    def test_unsafe_labels_are_not_merged_without_registry(self) -> None:
        current = _empty_section_map()
        secondary = _empty_section_map()
        current["4-1"] = {
            "section_title": "재무상태표",
            "tables": [{"table_title": "재무상태표", "matrix": [["과목", "제 15 기"], ["공동기업투자", "10"]]}],
        }
        secondary["4-1"] = {
            "section_title": "재무상태표",
            "tables": [{"table_title": "재무상태표", "matrix": [["과목", "제 14 기"], ["관계기업 및 공동기업투자", "8"]]}],
        }
        handoff = build_2y_handoff({"primary": current, "secondary": secondary}, _secondary_annual_target())
        table = handoff["4-1"]["tables"][0]

        self.assertEqual(len(table["item_order"]), 2)
        current_only = _find_item_by_display_name(table, "공동기업투자")
        previous_only = _find_item_by_display_name(table, "관계기업 및 공동기업투자")
        self.assertEqual(current_only["current_value"], "10")
        self.assertIsNone(current_only["previous_value"])
        self.assertIsNone(previous_only["current_value"])
        self.assertEqual(previous_only["previous_value"], "8")


class MasterOutputTests(unittest.TestCase):
    """Verify output-level behavior for master and secondary normalization."""

    def test_master_output_uses_unified_canonical_schema(self) -> None:
        collected = {
            "primary": {"raw": _sample_section_map(), "normalized": _sample_section_map()},
            "secondary": {"raw": _secondary_annual_section_map(), "normalized": _secondary_annual_section_map()},
        }
        master = _build_master(collected, _secondary_annual_target())

        self.assertEqual(list(master.keys()), ["4-1", "4-2", "4-3", "4-4"])
        self.assertNotIn("primary", master)
        self.assertNotIn("secondary", master)
        _assert_no_key_recursive(master, "matrix")
        _assert_no_key_recursive(master, "scope")
        self.assertIn("items_by_key", master["4-1"]["tables"][0])
        self.assertIn("period_blocks", master["4-3"]["tables"][0])

    def test_master_preserves_four_year_canonical_history_and_handoff_keeps_two_year_slice(self) -> None:
        primary = _sample_section_map()
        secondary = _secondary_annual_section_map()
        collected = {
            "primary": {"raw": _empty_section_map(), "normalized": primary},
            "secondary": {"raw": _empty_section_map(), "normalized": secondary},
        }
        master = _build_master(collected, _secondary_annual_target())
        handoff = build_2y_handoff({"primary": primary, "secondary": secondary}, _secondary_annual_target())

        self.assertEqual(list(master.keys()), list(handoff.keys()))
        master_periods = master["4-1"]["tables"][0]["periods"]
        handoff_periods = handoff["4-1"]["tables"][0]["periods"]
        self.assertEqual(
            list(master_periods.keys()),
            ["current_fiscal_year", "previous_fiscal_year", "previous_fiscal_year_2"],
        )
        self.assertEqual(list(handoff_periods.keys()), ["current_fiscal_year", "previous_fiscal_year"])
        item = master["4-1"]["tables"][0]["items_by_key"]["total_assets"]
        self.assertEqual(item["values_by_period_key"]["current_fiscal_year"], "100")
        self.assertEqual(item["values_by_period_key"]["previous_fiscal_year"], "90")
        self.assertEqual(item["values_by_period_key"]["previous_fiscal_year_2"], "70")
        self.assertEqual(item["numeric_values_by_period_key"]["previous_fiscal_year_2"], 70)

    def test_collect_report_keeps_secondary_normalized_equal_to_raw_internally(self) -> None:
        raw = _secondary_annual_section_map()
        target = TargetReport(
            role="secondary",
            fiscal_year=2024,
            period_type="annual",
            period_end=date(2024, 12, 31),
            dart_detail_type="A001",
            report_keyword="사업보고서",
        )

        class FakeClient:
            def fetch_document_xml(self, *, rcept_no: str) -> str:
                return "<DOCUMENT></DOCUMENT>"

        with patch("Agent_Team.Financial_Agent.main.extract_section_four", return_value=raw):
            collected = collect_report(FakeClient(), target, Filing("1", "사업보고서 (2024.12)", "20250301"))

        self.assertEqual(collected["normalized"], collected["raw"])
        self.assertIsNot(collected["normalized"], collected["raw"])

    def test_write_outputs_generates_financial_index_files_inside_output_dir(self) -> None:
        import json
        import tempfile

        collected = {
            "primary": {"raw": _sample_section_map(), "normalized": _sample_section_map()},
            "secondary": {"raw": _secondary_annual_section_map(), "normalized": _secondary_annual_section_map()},
        }
        master = _build_master(collected, _secondary_annual_target())
        handoff = build_2y_handoff(
            {"primary": collected["primary"]["normalized"], "secondary": collected["secondary"]["normalized"]},
            _secondary_annual_target(),
        )

        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            index_path = root / "financial_index.json"
            index_path.write_text(
                json.dumps({"output_metrics_order": ["Revenue", "Revenue Growth"]}, ensure_ascii=False),
                encoding="utf-8",
            )

            written = _write_outputs(
                output_dir=root / "outputs",
                master=master,
                handoff=handoff,
                financial_index_path=index_path,
                calculate_financial_index=True,
            )

            self.assertTrue(written["master"].exists())
            self.assertTrue(written["handoff"].exists())
            self.assertTrue(written["master_financial_index"].exists())
            self.assertTrue(written["handoff_financial_index"].exists())
            master_index = json.loads(written["master_financial_index"].read_text(encoding="utf-8"))
            handoff_index = json.loads(written["handoff_financial_index"].read_text(encoding="utf-8"))
            self.assertEqual(master_index["metric_order"], ["revenue", "revenue_growth"])
            self.assertEqual(handoff_index["metric_order"], ["revenue", "revenue_growth"])
            self.assertIn("metrics_by_key", master_index)
            self.assertNotIn("metrics", master_index)


class _PointInTimeFakeClient:
    def __init__(self) -> None:
        self.requested_end_dates: list[str] = []

    def list_filings(
        self,
        *,
        corp_code: str,
        bgn_de: str,
        end_de: str,
        pblntf_detail_ty: str,
    ) -> list[Filing]:
        self.requested_end_dates.append(end_de)
        filings = [
            Filing(
                rcept_no="20251114001570",
                report_nm="분기보고서 (2025.09)",
                rcept_dt="20251114",
                corp_code=corp_code,
            ),
            Filing(
                rcept_no="20250814001203",
                report_nm="반기보고서 (2025.06)",
                rcept_dt="20250814",
                corp_code=corp_code,
            ),
            Filing(
                rcept_no="20240814001111",
                report_nm="반기보고서 (2024.06)",
                rcept_dt="20240814",
                corp_code=corp_code,
            ),
            Filing(
                rcept_no="20250318001091",
                report_nm="사업보고서 (2024.12)",
                rcept_dt="20250318",
                corp_code=corp_code,
            ),
        ]
        return [
            filing
            for filing in filings
            if bgn_de <= filing.rcept_dt <= end_de
            and (
                (pblntf_detail_ty == "A001" and "사업보고서" in filing.report_nm)
                or (pblntf_detail_ty == "A002" and "반기보고서" in filing.report_nm)
                or (
                    pblntf_detail_ty == "A003"
                    and "분기보고서" in filing.report_nm
                    and "반기보고서" not in filing.report_nm
                )
            )
        ]


def _find_item_by_display_name(table: dict, display_name: str) -> dict:
    for item_key in table["item_order"]:
        item = table["items_by_key"][item_key]
        if item["display_name"] == display_name:
            return item
    raise AssertionError(f"Item not found: {display_name}")


def _assert_no_key_recursive(value: object, forbidden_key: str) -> None:
    if isinstance(value, dict):
        if forbidden_key in value:
            raise AssertionError(f"Forbidden key found: {forbidden_key}")
        for child in value.values():
            _assert_no_key_recursive(child, forbidden_key)
    elif isinstance(value, list):
        for child in value:
            _assert_no_key_recursive(child, forbidden_key)


def _sample_section_map() -> SectionMap:
    return {
        "4-1": {
            "section_title": "재무상태표",
            "tables": [
                {
                    "table_title": "재무상태표",
                    "matrix": [["", "제 15 기 3분기말", "제 14 기말"], ["자산총계", "100", "90"]],
                }
            ],
        },
        "4-2": {
            "section_title": "포괄손익계산서",
            "tables": [
                {
                    "table_title": "포괄손익계산서",
                    "matrix": [
                        ["", "제 15 기 3분기", "제 15 기 3분기", "제 14 기 3분기", "제 14 기 3분기"],
                        ["", "3개월", "누적", "3개월", "누적"],
                        ["매출액", "100", "300", "80", "240"],
                    ],
                }
            ],
        },
        "4-3": {
            "section_title": "자본변동표",
            "tables": [
                {
                    "table_title": "자본변동표",
                    "matrix": [
                        ["", "자본", "자본"],
                        ["", "자본금", "자본 합계"],
                        ["2024.01.01 (기초자본)", "10", "20"],
                        ["총포괄손익", "0", "5"],
                        ["2024.09.30 (분기말자본)", "10", "25"],
                        ["2025.01.01 (기초자본)", "10", "25"],
                        ["총포괄손익", "0", "6"],
                        ["2025.09.30 (분기말자본)", "10", "31"],
                    ],
                }
            ],
        },
        "4-4": {
            "section_title": "현금흐름표",
            "tables": [
                {
                    "table_title": "현금흐름표",
                    "matrix": [["", "제 15 기 3분기", "제 14 기 3분기"], ["영업활동현금흐름", "50", "40"]],
                }
            ],
        },
    }


def _empty_section_map() -> SectionMap:
    return {
        "4-1": {"section_title": "재무상태표", "tables": []},
        "4-2": {"section_title": "포괄손익계산서", "tables": []},
        "4-3": {"section_title": "자본변동표", "tables": []},
        "4-4": {"section_title": "현금흐름표", "tables": []},
    }


def _period_section_map(label: str, revenue: str) -> SectionMap:
    sections = _empty_section_map()
    sections["4-2"] = {
        "section_title": "포괄손익계산서",
        "tables": [
            {
                "table_title": "포괄손익계산서",
                "matrix": [["과목", label], ["매출액", revenue]],
            }
        ],
    }
    return sections


def _annual_history_section_map() -> SectionMap:
    sections = _empty_section_map()
    sections["4-2"] = {
        "section_title": "포괄손익계산서",
        "tables": [
            {
                "table_title": "포괄손익계산서",
                "matrix": [
                    ["과목", "2024.01.01~2024.12.31", "2023.01.01~2023.12.31", "2022.01.01~2022.12.31"],
                    ["매출액", "500", "400", "300"],
                ],
            }
        ],
    }
    return sections


def _half_target(role: str, fiscal_year: int) -> TargetReport:
    return TargetReport(
        role=role,  # type: ignore[arg-type]
        fiscal_year=fiscal_year,
        period_type="half",
        period_end=date(fiscal_year, 6, 30),
        dart_detail_type="A002",
        report_keyword="반기보고서",
    )


def _annual_target_for_test(role: str, fiscal_year: int) -> TargetReport:
    return TargetReport(
        role=role,  # type: ignore[arg-type]
        fiscal_year=fiscal_year,
        period_type="annual",
        period_end=date(fiscal_year, 12, 31),
        dart_detail_type="A001",
        report_keyword="사업보고서",
    )


def _q3_target() -> TargetReport:
    return TargetReport(
        role="primary",
        fiscal_year=2025,
        period_type="q3",
        period_end=date(2025, 9, 30),
        dart_detail_type="A003",
        report_keyword="분기보고서",
    )


def _secondary_annual_target() -> TargetReport:
    return TargetReport(
        role="secondary",
        fiscal_year=2024,
        period_type="annual",
        period_end=date(2024, 12, 31),
        dart_detail_type="A001",
        report_keyword="사업보고서",
    )


def _secondary_annual_section_map() -> SectionMap:
    return {
        "4-1": {
            "section_title": "재무상태표",
            "tables": [
                {
                    "table_title": "재무상태표",
                    "matrix": [["", "제 14 기", "제 13 기"], ["자산총계", "90", "70"]],
                }
            ],
        },
        "4-2": {
            "section_title": "포괄손익계산서",
            "tables": [
                {
                    "table_title": "포괄손익계산서",
                    "matrix": [["", "제 14 기", "제 13 기"], ["매출액", "400", "300"]],
                }
            ],
        },
        "4-3": {
            "section_title": "자본변동표",
            "tables": [
                {
                    "table_title": "자본변동표",
                    "matrix": [
                        ["", "자본금", "자본 합계"],
                        ["2023.01.01 (기초자본)", "10", "20"],
                        ["2023.12.31 (기말자본)", "10", "21"],
                        ["2024.01.01 (기초자본)", "10", "21"],
                        ["2024.12.31 (기말자본)", "10", "30"],
                    ],
                }
            ],
        },
        "4-4": {
            "section_title": "현금흐름표",
            "tables": [
                {
                    "table_title": "현금흐름표",
                    "matrix": [["", "제 14 기", "제 13 기"], ["영업활동현금흐름", "50", "40"]],
                }
            ],
        },
    }


if __name__ == "__main__":
    unittest.main()
