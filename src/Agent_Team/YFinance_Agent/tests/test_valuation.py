"""Tests for point-in-time valuation normalization and calculations."""

from __future__ import annotations

import json
import sys
import unittest
from datetime import date
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from valuation import build_valuation_snapshot, normalize_valuation_measures


class HistoricalValuationTests(unittest.TestCase):
    def test_excludes_current_and_periods_after_selected_date(self) -> None:
        frame = pd.DataFrame(
            {
                "Current": ["8.00T", "20.00", "12.00", "10.00"],
                "12/31/2025": ["9.00T", "25.00", "14.00", "12.00"],
                "9/30/2025": ["7.94T", "24.77", "11.75", "11.80"],
                "6/30/2025": ["7.20T", "28.14", "11.61", "12.10"],
            },
            index=["Market Cap", "Trailing P/E", "Price/Sales", "Price/Book"],
        )

        result = normalize_valuation_measures(
            frame,
            ticker="326030.KS",
            selected_date=date(2025, 10, 31),
            retrieved_at="2026-07-10T10:00:00+09:00",
        )

        self.assertEqual(result["latest_period"]["valuation_date"], "2025-09-30")
        self.assertEqual([period["valuation_date"] for period in result["periods"]], ["2025-09-30", "2025-06-30"])
        self.assertEqual(result["latest_period"]["metrics"]["market_cap"]["value"], 7.94e12)
        self.assertEqual(result["filter_validation"]["at_or_after_selected_date_count_excluded"], 1)
        self.assertTrue(result["filter_validation"]["current_snapshot_excluded"])
        self.assertTrue(result["filter_validation"]["all_included_periods_before_selected_date"])
        serialized = json.dumps(result).lower()
        self.assertNotIn("forward p/e", serialized)
        self.assertNotIn("peg ratio", serialized)

    def test_calculates_exact_date_market_cap_and_multiples_from_dart(self) -> None:
        direct = _direct_snapshot()
        result = build_valuation_snapshot(
            market_summary={"latest_snapshot": {"date": "2025-10-31", "stock_close": 115_500}},
            dart_payload=_dart_payload(net_income=329_247_800_153),
            direct_valuation=direct,
        )

        calculated = result["calculated_from_close_and_dart"]
        metrics = calculated["metrics"]
        self.assertEqual(metrics["market_cap"]["value"], 9_045_180_375_000)
        self.assertAlmostEqual(metrics["trailing_pe"]["value"], 27.47225, places=4)
        self.assertAlmostEqual(metrics["price_to_sales"]["value"], 14.74398, places=4)
        self.assertAlmostEqual(metrics["price_to_book"]["value"], 13.38574, places=4)
        self.assertEqual(calculated["inputs"]["shares_outstanding"]["source"]["receipt_no"], "20250814001203")
        self.assertEqual(
            result["validation"]["direct_vs_calculated"]["market_cap"]["status"],
            "different_as_of_dates",
        )
        self.assertFalse(
            result["validation"]["direct_vs_calculated"]["market_cap"]["strong_evidence_eligible"]
        )

    def test_non_positive_ttm_income_makes_pe_null(self) -> None:
        result = build_valuation_snapshot(
            market_summary={"latest_snapshot": {"date": "2025-10-31", "stock_close": 23_000}},
            dart_payload=_dart_payload(net_income=-1_000),
            direct_valuation=_direct_snapshot(),
        )

        pe = result["calculated_from_close_and_dart"]["metrics"]["trailing_pe"]
        self.assertIsNone(pe["value"])
        self.assertEqual(pe["status"], "insufficient_data")
        self.assertEqual(pe["reason"], "non_positive_ttm_net_income")

    def test_missing_denominator_does_not_create_multiple(self) -> None:
        payload = _dart_payload(net_income=1_000)
        payload["metrics_by_key"]["revenue"]["values_by_period"]["ttm"]["value"] = None

        result = build_valuation_snapshot(
            market_summary={"latest_snapshot": {"date": "2025-10-31", "stock_close": 100_000}},
            dart_payload=payload,
            direct_valuation=_direct_snapshot(),
        )

        price_to_sales = result["calculated_from_close_and_dart"]["metrics"]["price_to_sales"]
        self.assertIsNone(price_to_sales["value"])
        self.assertEqual(price_to_sales["reason"], "missing_ttm_revenue")


def _direct_snapshot() -> dict:
    metrics = {
        "market_cap": {"value": 7.94e12, "unit": "KRW", "status": "ok"},
        "trailing_pe": {"value": 24.77, "unit": "times", "status": "ok"},
        "price_to_sales": {"value": 11.75, "unit": "times", "status": "ok"},
        "price_to_book": {"value": 11.8, "unit": "times", "status": "ok"},
    }
    return {
        "status": "available",
        "selected_date": "2025-10-31",
        "latest_period": {"valuation_date": "2025-09-30", "metrics": metrics},
        "periods": [{"valuation_date": "2025-09-30", "metrics": metrics}],
        "filter_validation": {"all_included_periods_before_selected_date": True},
    }


def _dart_payload(*, net_income: int) -> dict:
    def metric(value: int, period_key: str, basis: str) -> dict:
        return {
            "values_by_period": {
                period_key: {
                    "value": value,
                    "period": {
                        "period_end": "2025-06-30",
                        "basis": basis,
                        "receipt_no": "20250814001203",
                        "receipt_date": "2025-08-14",
                    },
                }
            }
        }

    return {
        "share_information": {
            "status": "available",
            "as_of_date": "2025-06-30",
            "shares_outstanding": 78_313_250,
            "source": {
                "provider": "DART",
                "receipt_no": "20250814001203",
                "receipt_date": "2025-08-14",
            },
        },
        "metrics_by_key": {
            "revenue": metric(613_484_732_499, "ttm", "TTM"),
            "net_income": metric(net_income, "ttm", "TTM"),
            "total_equity": metric(675_732_768_162, "current_fiscal_year", "POINT_IN_TIME"),
        },
    }


if __name__ == "__main__":
    unittest.main()
