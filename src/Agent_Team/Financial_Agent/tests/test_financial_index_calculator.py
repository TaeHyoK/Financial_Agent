"""Tests for deterministic financial index calculation from canonical DART JSON."""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = Path(__file__).resolve().parents[3]
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from Agent_Team.Financial_Agent.financial_index_calculator import (
    DEFAULT_HANDOFF_INDEX_FILENAME,
    DEFAULT_MASTER_INDEX_FILENAME,
    calculate_financial_index,
    calculate_financial_index_files,
)


METRIC_ORDER = [
    "Revenue",
    "Revenue Growth",
    "Cost of Operations",
    "Contribution Profit",
    "Contribution Margin",
    "SG&A",
    "SG&A Margin",
    "EPS",
]
METRIC_KEY_ORDER = [
    "revenue",
    "revenue_growth",
    "cost_of_operations",
    "contribution_profit",
    "contribution_margin",
    "sga",
    "sga_margin",
    "eps",
]


class FinancialIndexCalculatorTests(unittest.TestCase):
    """Verify metric extraction and calculation rules."""

    def test_master_calculates_four_period_values_and_three_yoy_pairs(self) -> None:
        result = calculate_financial_index(_canonical_payload(period_count=4), METRIC_ORDER, source_file="master")

        self.assertEqual(result["schema_name"], "dart_financial_index")
        self.assertEqual(result["schema_version"], "1.0")
        self.assertEqual(result["metric_order"], METRIC_KEY_ORDER)
        self.assertNotIn("metrics", result)
        self.assertNotIn("metrics_order", result)
        self.assertEqual(list(result["periods"].keys()), [
            "current_fiscal_year",
            "previous_fiscal_year",
            "previous_fiscal_year_2",
            "previous_fiscal_year_3",
        ])
        self.assertEqual(
            [pair["comparison_key"] for pair in result["comparison_pairs"]],
            ["2025_vs_2024", "2024_vs_2023", "2023_vs_2022"],
        )

        revenue_metric = result["metrics_by_key"]["revenue"]
        self.assertEqual(revenue_metric["display_name"], "Revenue")
        self.assertEqual(revenue_metric["source_items"], ["매출액", "영업수익"])
        self.assertEqual(revenue_metric["source_labels_observed"], ["매출액 (주18)"])
        revenue = revenue_metric["values_by_period"]
        self.assertEqual(revenue["current_fiscal_year"]["value"], 1000)
        self.assertEqual(revenue["previous_fiscal_year_3"]["value"], 200)

        growth_metric = result["metrics_by_key"]["revenue_growth"]
        self.assertEqual(growth_metric["formula"], "(current - previous) / previous")
        self.assertEqual(growth_metric["source_metric_keys"], ["revenue"])
        self.assertEqual(growth_metric["comparison_method"], "YoY")
        growth = growth_metric["comparisons"]
        self.assertEqual(growth["2025_vs_2024"]["value"], 0.25)
        self.assertEqual(growth["2024_vs_2023"]["value"], 1.0)
        self.assertEqual(growth["2023_vs_2022"]["value"], 1.0)

    def test_calculates_requested_period_metrics_from_available_items(self) -> None:
        result = calculate_financial_index(_canonical_payload(period_count=4), METRIC_ORDER)
        metrics = result["metrics_by_key"]

        self.assertEqual(metrics["cost_of_operations"]["values_by_period"]["current_fiscal_year"]["value"], 400)
        self.assertEqual(metrics["contribution_profit"]["values_by_period"]["current_fiscal_year"]["value"], 600)
        self.assertEqual(metrics["contribution_profit"]["formula"], "Revenue - Cost of Operations")
        self.assertEqual(metrics["contribution_margin"]["values_by_period"]["current_fiscal_year"]["value"], 0.6)
        self.assertEqual(metrics["contribution_margin"]["formula"], "Contribution Profit / Revenue")
        self.assertEqual(metrics["sga"]["values_by_period"]["current_fiscal_year"]["value"], 100)
        self.assertEqual(metrics["sga_margin"]["values_by_period"]["current_fiscal_year"]["value"], 0.1)
        self.assertEqual(metrics["sga_margin"]["formula"], "SG&A / Revenue")
        self.assertEqual(metrics["eps"]["values_by_period"]["current_fiscal_year"]["value"], 10)
        self.assertEqual(metrics["eps"]["values_by_period"]["previous_fiscal_year"]["value"], 8)
        self.assertNotIn("ebitda", metrics)
        self.assertNotIn("ebitda_margin", metrics)
        self.assertNotIn("pe_ratio", metrics)

    def test_handoff_calculates_only_current_vs_previous_pair(self) -> None:
        result = calculate_financial_index(_canonical_payload(period_count=2), METRIC_ORDER, source_file="handoff")

        self.assertEqual(list(result["periods"].keys()), ["current_fiscal_year", "previous_fiscal_year"])
        self.assertEqual(
            [pair["comparison_key"] for pair in result["comparison_pairs"]],
            ["2025_vs_2024"],
        )
        self.assertEqual(
            list(result["metrics_by_key"]["revenue_growth"]["comparisons"].keys()),
            ["2025_vs_2024"],
        )

    def test_unavailable_metrics_are_removed_from_requested_output(self) -> None:
        result = calculate_financial_index(_canonical_payload(period_count=2, include_depreciation=False), METRIC_ORDER)

        self.assertEqual(result["metric_order"], METRIC_KEY_ORDER)
        self.assertNotIn("ebitda", result["metrics_by_key"])
        self.assertNotIn("ebitda_margin", result["metrics_by_key"])
        self.assertNotIn("pe_ratio", result["metrics_by_key"])

    def test_file_calculation_writes_one_result_for_master_and_one_for_handoff(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            master_path = root / "dart_master.json"
            handoff_path = root / "dart_2y_handoff.json"
            index_path = root / "financial_index.json"
            output_dir = root / "outputs"
            master_path.write_text(json.dumps(_canonical_payload(period_count=4), ensure_ascii=False), encoding="utf-8")
            handoff_path.write_text(json.dumps(_canonical_payload(period_count=2), ensure_ascii=False), encoding="utf-8")
            index_path.write_text(json.dumps({"output_metrics_order": METRIC_ORDER}, ensure_ascii=False), encoding="utf-8")

            master_output, handoff_output = calculate_financial_index_files(
                master_path=master_path,
                handoff_path=handoff_path,
                index_path=index_path,
                output_dir=output_dir,
            )

            self.assertTrue(master_output.exists())
            self.assertTrue(handoff_output.exists())
            self.assertEqual(master_output.name, DEFAULT_MASTER_INDEX_FILENAME)
            self.assertEqual(handoff_output.name, DEFAULT_HANDOFF_INDEX_FILENAME)
            master_result = json.loads(master_output.read_text(encoding="utf-8"))
            handoff_result = json.loads(handoff_output.read_text(encoding="utf-8"))
            self.assertEqual(master_result["metric_order"], METRIC_KEY_ORDER)
            self.assertEqual(handoff_result["metric_order"], METRIC_KEY_ORDER)
            self.assertIn("metrics_by_key", master_result)
            self.assertNotIn("metrics", master_result)
            self.assertEqual(len(master_result["comparison_pairs"]), 3)
            self.assertEqual(len(handoff_result["comparison_pairs"]), 1)


def _canonical_payload(*, period_count: int, include_depreciation: bool = True) -> dict:
    period_keys = [
        "current_fiscal_year",
        "previous_fiscal_year",
        "previous_fiscal_year_2",
        "previous_fiscal_year_3",
    ][:period_count]
    periods = {period_key: _period_meta(period_key) for period_key in period_keys}

    income_items = {
        "revenue": _item("매출액 (주18)", period_keys, [1000, 800, 400, 200]),
        "item_cost": _item("매출원가 (주21)", period_keys, [400, 300, 100, 50]),
        "item_sga": _item("판매비와관리비", period_keys, [100, 90, 80, 70]),
        "operating_profit": _item("영업이익", period_keys, [500, 410, 220, 80]),
        "item_eps_current": _item("기본주당이익 (단위 : 원)", period_keys[:1], [10]),
        "item_eps_history": _item("기본주당이익(손실) (단위 : 원)", period_keys[1:], [8, 4, 2]),
    }
    cash_flow_items = {}
    if include_depreciation:
        cash_flow_items = {
            "item_depreciation": _item("감가상각비", period_keys, [10, 20, 30, 40]),
            "item_amortization": _item("무형자산상각비", period_keys, [5, 5, 5, 5]),
        }

    return {
        "4-1": {"statement_name": "재무상태표", "tables": []},
        "4-2": {
            "statement_name": "포괄손익계산서",
            "tables": [
                {
                    "table_key": "income_statement_1",
                    "table_title": "포괄손익계산서",
                    "unit": "원",
                    "periods": periods,
                    "items_by_key": income_items,
                    "item_order": list(income_items.keys()),
                }
            ],
        },
        "4-3": {"statement_name": "자본변동표", "tables": []},
        "4-4": {
            "statement_name": "현금흐름표",
            "tables": [
                {
                    "table_key": "cash_flow_1",
                    "table_title": "현금흐름표",
                    "unit": "원",
                    "periods": periods,
                    "items_by_key": cash_flow_items,
                    "item_order": list(cash_flow_items.keys()),
                }
            ],
        },
    }


def _period_meta(period_key: str) -> dict:
    metas = {
        "current_fiscal_year": {
            "label": "제 15 기 3분기",
            "fiscal_year": 2025,
            "period_type": "Q3",
            "period_end": "2025-09-30",
            "basis": "YTD",
        },
        "previous_fiscal_year": {
            "label": "제 14 기",
            "fiscal_year": 2024,
            "period_type": "ANNUAL",
            "period_end": "2024-12-31",
            "basis": "FULL_YEAR",
        },
        "previous_fiscal_year_2": {
            "label": "제 13 기",
            "fiscal_year": 2023,
            "period_type": "ANNUAL",
            "period_end": "2023-12-31",
            "basis": "FULL_YEAR",
        },
        "previous_fiscal_year_3": {
            "label": "제 12 기",
            "fiscal_year": 2022,
            "period_type": "ANNUAL",
            "period_end": "2022-12-31",
            "basis": "FULL_YEAR",
        },
    }
    return metas[period_key]


def _item(display_name: str, period_keys: list[str], values: list[int]) -> dict:
    return {
        "display_name": display_name,
        "aliases": [display_name],
        "current_value": str(values[0]) if "current_fiscal_year" in period_keys else None,
        "current_numeric": values[0] if "current_fiscal_year" in period_keys else None,
        "previous_value": str(values[period_keys.index("previous_fiscal_year")])
        if "previous_fiscal_year" in period_keys
        else None,
        "previous_numeric": values[period_keys.index("previous_fiscal_year")]
        if "previous_fiscal_year" in period_keys
        else None,
        "values_by_period_key": {period_key: str(value) for period_key, value in zip(period_keys, values)},
        "numeric_values_by_period_key": {period_key: value for period_key, value in zip(period_keys, values)},
    }


if __name__ == "__main__":
    unittest.main()
