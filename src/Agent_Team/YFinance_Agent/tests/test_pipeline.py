"""Tests for output schema and summary-date selection."""

from __future__ import annotations

import json
import sys
import unittest
from datetime import date
from pathlib import Path
from tempfile import TemporaryDirectory

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from pipeline import (
    OUTPUT_COLUMNS,
    PipelineInput,
    build_full_dataset,
    build_summary_dataset,
    resolve_source_start_date,
    write_dataframe_outputs,
)


class PipelineOutputTests(unittest.TestCase):
    def test_build_full_dataset_uses_requested_schema(self) -> None:
        index = pd.bdate_range("2024-11-01", periods=120)
        stock = _make_market_frame(index, close_start=100.0, close_step=1.0, volume_start=1000.0)
        kospi = _make_market_frame(index, close_start=2000.0, close_step=2.0, volume_start=5000.0)
        fx = _make_market_frame(index, close_start=1300.0, close_step=0.5, volume_start=0.0)

        result = build_full_dataset(
            {"stock": stock, "kospi": kospi, "fx_usdkrw": fx},
            pipeline_input=PipelineInput(
                ticker="TEST.KS",
                company_name="Test",
                start_date=index[0].date(),
                end_date=index[-1].date(),
                selected_date=index[-1].date(),
                source_path=Path("/tmp/test_input.json"),
            ),
        )

        self.assertEqual(result.columns.tolist(), OUTPUT_COLUMNS)
        self.assertEqual(result["date"].iloc[0], "2024-11-01")
        self.assertAlmostEqual(result["stock_excess_return_5d"].iloc[-1], result["stock_return_5d"].iloc[-1] - result["kospi_return_5d"].iloc[-1])

    def test_build_full_dataset_uses_pre_range_rows_for_indicator_warmup(self) -> None:
        index = pd.bdate_range("2024-06-03", periods=180)
        output_start = index[90].date()
        stock = _make_market_frame(index, close_start=100.0, close_step=1.0, volume_start=1000.0)
        kospi = _make_market_frame(index, close_start=2000.0, close_step=2.0, volume_start=5000.0)
        fx = _make_market_frame(index, close_start=1300.0, close_step=0.5, volume_start=0.0)

        result = build_full_dataset(
            {"stock": stock, "kospi": kospi, "fx_usdkrw": fx},
            pipeline_input=PipelineInput(
                ticker="TEST.KS",
                company_name="Test",
                start_date=output_start,
                end_date=index[-1].date(),
                selected_date=index[-1].date(),
                source_path=Path("/tmp/test_input.json"),
            ),
        )

        self.assertEqual(result["date"].iloc[0], output_start.isoformat())
        self.assertFalse(pd.isna(result["stock_return_60d"].iloc[0]))
        self.assertFalse(pd.isna(result["stock_close_to_ma60"].iloc[0]))
        self.assertFalse(pd.isna(result["stock_relative_strength_60"].iloc[0]))

    def test_returns_use_adjusted_close_while_display_keeps_raw_close(self) -> None:
        index = pd.bdate_range("2024-01-02", periods=80)
        stock = _make_market_frame(index, close_start=100.0, close_step=1.0, volume_start=1000.0)
        stock["adj_close"] = pd.Series(
            [50.0 + (2.0 * i) for i in range(len(index))],
            index=index,
        )
        kospi = _make_market_frame(index, close_start=2000.0, close_step=2.0, volume_start=5000.0)
        fx = _make_market_frame(index, close_start=1300.0, close_step=0.5, volume_start=0.0)

        result = build_full_dataset(
            {"stock": stock, "kospi": kospi, "fx_usdkrw": fx},
            pipeline_input=PipelineInput(
                ticker="TEST.KS",
                company_name="Test",
                start_date=index[60].date(),
                end_date=index[-1].date(),
                selected_date=(index[-1] + pd.Timedelta(days=1)).date(),
                source_path=Path("/tmp/test_input.json"),
            ),
        )

        expected = stock["adj_close"].pct_change(5).loc[index[-1]]
        self.assertEqual(result["stock_close"].iloc[-1], stock["close"].iloc[-1])
        self.assertEqual(result["stock_adjusted_close"].iloc[-1], stock["adj_close"].iloc[-1])
        self.assertAlmostEqual(result["stock_return_5d"].iloc[-1], expected)

    def test_resolve_source_start_date_uses_two_year_source_window(self) -> None:
        self.assertEqual(resolve_source_start_date(date(2024, 11, 1), date(2025, 10, 31)), date(2023, 11, 1))
        self.assertEqual(resolve_source_start_date(date(2022, 1, 1), date(2025, 10, 31)), date(2022, 1, 1))

    def test_build_summary_dataset_returns_previous_trading_day_when_needed(self) -> None:
        full = pd.DataFrame(
            [
                {"date": "2025-10-30", **{column: 1.0 for column in OUTPUT_COLUMNS if column != "date"}},
                {"date": "2025-10-31", **{column: 2.0 for column in OUTPUT_COLUMNS if column != "date"}},
            ]
        )[OUTPUT_COLUMNS]

        summary = build_summary_dataset(full, selected_date=date(2025, 11, 1))

        self.assertEqual(summary.requested_date, "2025-11-01")
        self.assertEqual(summary.actual_date, "2025-10-31")
        self.assertEqual(summary.match_type, "latest_trading_day_before_selected_date")
        self.assertEqual(summary.frame.columns.tolist(), OUTPUT_COLUMNS)

    def test_build_summary_dataset_excludes_selected_date_row(self) -> None:
        full = pd.DataFrame(
            [
                {"date": "2025-10-30", **{column: 1.0 for column in OUTPUT_COLUMNS if column != "date"}},
                {"date": "2025-10-31", **{column: 2.0 for column in OUTPUT_COLUMNS if column != "date"}},
            ]
        )[OUTPUT_COLUMNS]

        summary = build_summary_dataset(full, selected_date=date(2025, 10, 31))

        self.assertEqual(summary.actual_date, "2025-10-30")
        self.assertEqual(summary.frame.iloc[0]["stock_close"], 1.0)

    def test_write_dataframe_outputs_serializes_missing_values_as_null_in_json(self) -> None:
        frame = pd.DataFrame(
            [
                {"date": "2025-10-31", "stock_close": 100.0, "stock_return_5d": pd.NA},
            ]
        )

        with TemporaryDirectory() as tmp:
            csv_path = Path(tmp) / "out.csv"
            json_path = Path(tmp) / "out.json"
            write_dataframe_outputs(frame, csv_path=csv_path, json_path=json_path)
            with json_path.open(encoding="utf-8") as file:
                data = json.load(file)

        self.assertEqual(data[0]["stock_return_5d"], None)


def _make_market_frame(index: pd.DatetimeIndex, *, close_start: float, close_step: float, volume_start: float) -> pd.DataFrame:
    rows = len(index)
    close = pd.Series([close_start + close_step * i for i in range(rows)], index=index)
    return pd.DataFrame(
        {
            "open": close - 0.5,
            "high": close + 0.5,
            "low": close - 1.0,
            "close": close,
            "adj_close": close,
            "volume": [volume_start + i for i in range(rows)],
        },
        index=index,
    )


if __name__ == "__main__":
    unittest.main()
