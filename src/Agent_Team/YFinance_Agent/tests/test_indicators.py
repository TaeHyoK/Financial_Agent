"""Tests for deterministic technical indicator calculations."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from indicators import add_technical_indicators, calculate_obv, calculate_rsi


class IndicatorTests(unittest.TestCase):
    def test_add_technical_indicators_adds_expected_columns(self) -> None:
        frame = pd.DataFrame(
            {
                "open": range(1, 41),
                "high": range(2, 42),
                "low": range(0, 40),
                "close": range(1, 41),
                "adj_close": range(1, 41),
                "volume": [100 + i for i in range(40)],
            },
            index=pd.date_range("2024-11-01", periods=40, freq="B"),
        )

        result = add_technical_indicators(frame)

        for column in [
            "ma_5",
            "ma_20",
            "ma_60",
            "close_to_ma20",
            "close_to_ma60",
            "ma5_to_ma20",
            "rsi_14",
            "bb_middle_20",
            "bb_upper_20",
            "bb_lower_20",
            "macd_12_26_9",
            "macd_signal_12_26_9",
            "macd_hist_12_26_9",
            "macd_hist_change_1d",
            "volume_ma_20",
            "volume_ratio_20",
            "obv_trend",
            "obv",
            "return_1d",
            "return_5d",
            "return_20d",
            "return_60d",
            "volatility_20",
        ]:
            self.assertIn(column, result.columns)

        self.assertAlmostEqual(result["bb_middle_20"].iloc[19], sum(range(1, 21)) / 20)
        self.assertAlmostEqual(result["volume_ma_20"].iloc[19], sum(100 + i for i in range(20)) / 20)
        self.assertGreater(result["rsi_14"].iloc[-1], 99.0)

    def test_rsi_for_flat_prices_is_neutral_after_window(self) -> None:
        close = pd.Series([10.0] * 20)
        rsi = calculate_rsi(close, window=14)
        self.assertAlmostEqual(rsi.iloc[-1], 50.0)

    def test_obv_accumulates_signed_volume(self) -> None:
        close = pd.Series([10, 11, 10, 10, 12], dtype=float)
        volume = pd.Series([100, 200, 300, 400, 500], dtype=float)
        obv = calculate_obv(close, volume)
        self.assertEqual(obv.tolist(), [0.0, 200.0, -100.0, -100.0, 400.0])


if __name__ == "__main__":
    unittest.main()
