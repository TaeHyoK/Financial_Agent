"""Offline extraction and valuation regression cases; no API credentials used."""
import copy
from datetime import date
import unittest
from unittest.mock import Mock, patch
from types import SimpleNamespace

import pandas as pd
from test_annual_context import financial_fixture
from Agent_Team.Financial_Agent.section_extractor import extract_financial_statements
from Agent_Team.Financial_Agent.models import Filing, TargetReport
from Agent_Team.Financial_Agent.main import collect_report, _build_matrix_master, supplement_annual_history
from Agent_Team.Financial_Agent.handoff_builder import build_trend_canonical
from Agent_Team.Financial_Agent.handoff_builder import _single_period_table, _annual_history_sources
from Agent_Team.Financial_Agent.financial_index_calculator import calculate_financial_index
from Agent_Team.Financial_Agent.langgraph_flow import infer_statement_scope
from Agent_Team.YFinance_Agent.valuation import build_valuation_snapshot


def section(number, value="1,000"):
    title = "연결재무제표" if number == 2 else "재무제표"
    text = f"<TITLE>{number}. {title}</TITLE>"
    for i, name in enumerate(("재무상태표", "포괄손익계산서", "자본변동표", "현금흐름표"), 1):
        text += (f"<TITLE>{number}-{i}. {name}</TITLE>"
                 "<TABLE><TR><TH>과목</TH><TH>2024.12.31</TH><TH>2023.12.31</TH></TR>"
                 f"<TR><TD>매출액</TD><TD>{value}</TD><TD>900</TD></TR></TABLE>")
    return text


class StatementScopeTests(unittest.TestCase):
    def test_consolidated_preferred_without_separate_table_contamination(self):
        result = extract_financial_statements(section(2) + "<TITLE>3. 연결재무제표 주석</TITLE>" + section(4, "9,000"))
        self.assertTrue(all(s["statement_scope"] == "consolidated" for s in result.values()))
        self.assertTrue(all(len(s["tables"]) == 1 for s in result.values()))
        self.assertEqual(result["4-2"]["tables"][0]["matrix"][1][1], "1,000")

    def test_separate_only_and_explicit_absence(self):
        for prefix in ("", "<TITLE>2. 연결재무제표</TITLE><P>해당사항 없음</P><TITLE>3. 연결재무제표 주석</TITLE>"):
            self.assertEqual(extract_financial_statements(prefix + section(4))["4-2"]["statement_scope"], "separate")

    def test_failed_consolidated_parse_never_falls_back(self):
        with self.assertRaisesRegex(ValueError, "could not be parsed"):
            extract_financial_statements("<TITLE>2. 연결재무제표</TITLE><P>invalid table</P>" + section(4))

    def test_scope_and_receipt_survive_collection_and_canonicalization(self):
        target = TargetReport("primary", 2024, "annual", date(2024, 12, 31), "A001", "사업보고서")
        filing = Filing("202503010001", "사업보고서 (2024.12)", "20250301")
        client = Mock(); client.fetch_document_xml.return_value = section(2) + section(4)
        collected = {"primary": collect_report(client, target, filing)}
        canonical = build_trend_canonical(_build_matrix_master(collected), {"primary": (target, filing)},
                                          selected_date=date(2025, 10, 31), theoretical_target=target)
        self.assertEqual(infer_statement_scope(canonical), "consolidated")
        period = canonical["4-2"]["tables"][0]["periods"]["current_fiscal_year"]
        self.assertEqual(period["statement_scope"], "consolidated")
        self.assertEqual(period["receipt_date"], "2025-03-01")

    def test_generic_title_is_not_proof_of_separate_scope(self):
        self.assertEqual(infer_statement_scope({"4-2": {"tables": [{"table_title": "손익계산서"}]}}), "unknown")

    def test_identical_owner_labels_distinguish_net_and_comprehensive_income(self):
        table = {"statement_scope": "consolidated", "matrix": [
            ["과목", "2024.12.31", "2023.12.31"],
            ["당기연결순이익", "100", "90"], ["지배기업소유주지분", "110", "95"],
            ["당기총포괄이익", "70", "60"], ["지배기업소유주지분", "80", "65"],
        ]}
        period = _single_period_table(table, 2024, statement_key="4-2")
        values = {item["key"]: item["value"] for item in period.items}
        self.assertEqual(values["parent_net_income"], "110")
        self.assertEqual(values["parent_comprehensive_income"], "80")
        target = TargetReport("annual_history", 2024, "annual", date(2024, 12, 31), "A001", "사업보고서")
        annual = _annual_history_sources(table, target, Filing("receipt", "사업보고서", "20250301"),
                                         excluded_years=set(), limit=3, statement_key="4-2")
        self.assertEqual({item["key"]: item["value"] for item in annual[1]["items"]}["parent_net_income"], "95")

    def test_missing_third_year_fetches_older_available_filing_without_overwriting_newer_values(self):
        target = TargetReport("primary", 2024, "annual", date(2024, 12, 31), "A001", "사업보고서")
        filing = Filing("202503010001", "사업보고서 (2024.12)", "20250301")
        client = Mock()
        client.fetch_document_xml.side_effect = [section(2), section(2, "777").replace("2023.12.31", "2022.12.31").replace("2024.12.31", "2023.12.31")]
        collected = {"primary": collect_report(client, target, filing)}
        resolved = {"primary": (target, filing)}
        config = SimpleNamespace(company_code="00000000", selected_date=date(2025, 10, 31))
        older = Filing("202403010001", "사업보고서 (2023.12)", "20240301")
        with patch("Agent_Team.Financial_Agent.main.resolve_single_report", return_value=older) as resolve:
            supplement_annual_history(client, resolved, collected, config)
        self.assertEqual(resolve.call_count, 1)
        self.assertEqual(resolve.call_args.kwargs["as_of_date"], config.selected_date)
        result = build_trend_canonical(_build_matrix_master(collected), resolved,
                                      selected_date=config.selected_date, theoretical_target=target)
        self.assertEqual(result["collection_context"]["annual_history_coverage"]["4-2"]["observed_years"], [2022, 2023, 2024])
        table = result["4-2"]["tables"][0]
        self.assertEqual(table["periods"]["previous_fiscal_year_2"]["source_role"], "annual_history_1")
        self.assertEqual(table["periods"]["previous_fiscal_year"]["receipt_date"], "2025-03-01")
        self.assertEqual(len(table["periods"]), 3)

    def test_scope_change_blocks_growth_and_ttm_but_preserves_values(self):
        source = financial_fixture()
        for section_data in source.values():
            for table in section_data["tables"]:
                for key, period in table["periods"].items():
                    period["statement_scope"] = "consolidated" if key == "current_fiscal_year" else "separate"
        report = calculate_financial_index(source, ["Revenue", "Revenue Growth"])
        self.assertNotIn("ttm", report["periods"])
        comparison = report["metrics_by_key"]["revenue_growth"]["comparisons"]["2025_HALF_vs_2024_HALF"]
        self.assertIsNone(comparison["value"])
        self.assertEqual(comparison["reason"], "statement_scope_mismatch")
        self.assertEqual(report["metrics_by_key"]["revenue"]["values_by_period"]["current_fiscal_year"]["value"], 60e8)


class ValuationScopeTests(unittest.TestCase):
    def setUp(self):
        source = financial_fixture()
        source["collection_context"] = {"statement_scope": "consolidated"}
        for section_key, total, parent in (("4-2", "net_income", "parent_net_income"), ("4-1", "total_equity", "parent_equity")):
            table = source[section_key]["tables"][0]
            item = copy.deepcopy(table["items_by_key"][total])
            item["item_key"] = parent; item["display_name"] = parent
            item["numeric_values_by_period_key"] = {k: v / 2 for k, v in item["numeric_values_by_period_key"].items()}
            table["items_by_key"][parent] = item; table["item_order"].append(parent)
        self.dart = calculate_financial_index(source, ["Revenue", "Net Income", "Total Equity"])
        self.dart["share_information"] = {"common_issued_shares": 1000000, "share_class": "common_only",
                                         "as_of_date": "2025-06-30", "source": {"receipt_date": "2025-08-14"}}
        self.market = {"latest_snapshot": {"date": "2025-10-30", "stock_close": 10000}}
        self.direct = {"selected_date": "2025-10-31"}

    def value(self, frame=None):
        return build_valuation_snapshot(market_summary=self.market, dart_payload=self.dart,
                                        direct_valuation=self.direct, market_frame=frame)["calculated_from_close_and_dart"]

    def test_parent_denominators_used_and_estimate_disclosed(self):
        result = self.value()
        self.assertAlmostEqual(result["metrics"]["trailing_pe"]["value"], 1e10 / (5.5e8))
        self.assertAlmostEqual(result["metrics"]["price_to_book"]["value"], 1e10 / (55e8))
        self.assertEqual(result["calculation_basis"], "disclosed_share_count_estimate")

    def test_missing_parent_profit_does_not_use_group_total(self):
        del self.dart["metrics_by_key"]["parent_net_income"]
        self.assertIsNone(self.value()["metrics"]["trailing_pe"]["value"])

    def test_future_denominator_and_nonpositive_close_are_not_valuations(self):
        self.dart["periods"]["current_fiscal_year"]["receipt_date"] = "2025-11-01"
        result = self.value()
        self.assertIsNone(result["metrics"]["trailing_pe"]["value"])
        self.assertIsNone(result["metrics"]["price_to_book"]["value"])
        self.market["latest_snapshot"]["stock_close"] = -1
        self.assertIsNone(self.value()["metrics"]["market_cap"]["value"])

    def test_future_receipt_multiple_classes_and_split_block_estimate(self):
        for change in ({"source": {"receipt_date": "2025-10-31"}}, {"share_class": "multiple_or_unknown"}):
            original = copy.deepcopy(self.dart["share_information"])
            self.dart["share_information"].update(change)
            self.assertIsNone(self.value()["metrics"]["market_cap"]["value"])
            self.dart["share_information"] = original
        frame = pd.DataFrame({"date": ["2025-08-01"], "stock_splits": [2]})
        self.assertIn("split_after_disclosed_share_count", self.value(frame)["input_problems"])


if __name__ == "__main__":
    unittest.main()
