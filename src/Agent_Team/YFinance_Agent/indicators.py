"""Technical indicator calculations for OHLCV market data."""

from __future__ import annotations

import numpy as np
import pandas as pd


def add_technical_indicators(
    frame: pd.DataFrame,
    *,
    close_col: str = "close",
    volume_col: str = "volume",
    rsi_window: int = 14,
    bb_window: int = 20,
    macd_fast: int = 12,
    macd_slow: int = 26,
    macd_signal: int = 9,
) -> pd.DataFrame:
    """Return a copy of frame with RSI, Bollinger Band, MACD, and volume indicators."""

    if close_col not in frame.columns:
        raise KeyError(f"Missing close column: {close_col}")
    if volume_col not in frame.columns:
        raise KeyError(f"Missing volume column: {volume_col}")

    output = frame.copy()
    close = pd.to_numeric(output[close_col], errors="coerce")
    volume = pd.to_numeric(output[volume_col], errors="coerce").fillna(0.0)

    output[f"rsi_{rsi_window}"] = calculate_rsi(close, window=rsi_window)

    bb_middle = close.rolling(bb_window, min_periods=bb_window).mean()
    bb_std = close.rolling(bb_window, min_periods=bb_window).std(ddof=0)
    bb_upper = bb_middle + (2.0 * bb_std)
    bb_lower = bb_middle - (2.0 * bb_std)
    output[f"bb_middle_{bb_window}"] = bb_middle
    output[f"bb_upper_{bb_window}"] = bb_upper
    output[f"bb_lower_{bb_window}"] = bb_lower
    output[f"bb_width_{bb_window}"] = (bb_upper - bb_lower) / bb_middle.replace(0, np.nan)

    ema_fast = close.ewm(span=macd_fast, adjust=False, min_periods=macd_fast).mean()
    ema_slow = close.ewm(span=macd_slow, adjust=False, min_periods=macd_slow).mean()
    macd = ema_fast - ema_slow
    macd_signal_line = macd.ewm(span=macd_signal, adjust=False, min_periods=macd_signal).mean()
    output[f"macd_{macd_fast}_{macd_slow}_{macd_signal}"] = macd
    output[f"macd_signal_{macd_fast}_{macd_slow}_{macd_signal}"] = macd_signal_line
    output[f"macd_hist_{macd_fast}_{macd_slow}_{macd_signal}"] = macd - macd_signal_line

    output["ma_5"] = close.rolling(5, min_periods=5).mean()
    output["ma_20"] = close.rolling(20, min_periods=20).mean()
    output["ma_60"] = close.rolling(60, min_periods=60).mean()
    output["close_to_ma20"] = close / output["ma_20"].replace(0, np.nan) - 1.0
    output["close_to_ma60"] = close / output["ma_60"].replace(0, np.nan) - 1.0
    output["ma5_to_ma20"] = output["ma_5"] / output["ma_20"].replace(0, np.nan) - 1.0

    output["volume_ma_5"] = volume.rolling(5, min_periods=5).mean()
    output["volume_ma_20"] = volume.rolling(20, min_periods=20).mean()
    output["volume_ratio_20"] = volume / output["volume_ma_20"].replace(0, np.nan)
    output["volume_change_1d"] = volume.pct_change()
    output["obv"] = calculate_obv(close, volume)
    output["obv_trend"] = output["obv"].diff(5) / volume.rolling(20, min_periods=20).sum().replace(0, np.nan)

    output["return_1d"] = close.pct_change()
    output["return_5d"] = close.pct_change(5)
    output["return_20d"] = close.pct_change(20)
    output["return_60d"] = close.pct_change(60)
    output["macd_hist_change_1d"] = output[f"macd_hist_{macd_fast}_{macd_slow}_{macd_signal}"].diff()
    output["volatility_20"] = output["return_1d"].rolling(20, min_periods=20).std(ddof=0)
    return output


def calculate_rsi(close: pd.Series, *, window: int = 14) -> pd.Series:
    """Calculate Wilder-style RSI."""

    delta = close.diff()
    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)

    avg_gain = gain.ewm(alpha=1 / window, adjust=False, min_periods=window).mean()
    avg_loss = loss.ewm(alpha=1 / window, adjust=False, min_periods=window).mean()
    rs = avg_gain / avg_loss.replace(0, np.nan)
    rsi = 100 - (100 / (1 + rs))

    only_gains = (avg_loss == 0) & (avg_gain > 0)
    no_change = (avg_loss == 0) & (avg_gain == 0)
    rsi = rsi.mask(only_gains, 100.0)
    rsi = rsi.mask(no_change, 50.0)
    return rsi


def calculate_obv(close: pd.Series, volume: pd.Series) -> pd.Series:
    """Calculate on-balance volume."""

    direction = np.sign(close.diff()).fillna(0.0)
    return (direction * volume).cumsum()
