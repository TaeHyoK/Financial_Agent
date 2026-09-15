"""Deterministic calendar-period price observations, calculated before clipping."""

from __future__ import annotations

import numpy as np
import pandas as pd


def annual_features(frame: pd.DataFrame) -> pd.DataFrame:
    price = pd.to_numeric(frame["close"], errors="coerce")
    returns = price.pct_change(fill_method=None)
    out = pd.DataFrame(index=frame.index)
    for months in (1, 3, 6, 12):
        anchors = frame.index - pd.DateOffset(months=months)
        positions = frame.index.searchsorted(anchors, side="right") - 1
        values = np.full(len(price), np.nan)
        for i, pos in enumerate(positions):
            if pos >= 0 and pd.notna(price.iloc[pos]) and price.iloc[pos] > 0:
                values[i] = price.iloc[i] / price.iloc[pos] - 1
        out[f"return_{months}m"] = values
    volatility, max_dd, current_dd, positions_52w = [], [], [], []
    for i, end in enumerate(frame.index):
        start = end - pd.DateOffset(years=1)
        begin = frame.index.searchsorted(start, side="right") - 1
        if begin < 0:
            volatility.append(np.nan); max_dd.append(np.nan)
            current_dd.append(np.nan); positions_52w.append(np.nan)
            continue
        window = price.iloc[begin:i + 1]
        daily = returns.iloc[begin + 1:i + 1].dropna()
        high, low = window.max(), window.min()
        volatility.append(daily.std(ddof=1) * np.sqrt(252) if len(daily) >= 2 else np.nan)
        max_dd.append((window / window.cummax() - 1).min())
        current_dd.append(price.iloc[i] / high - 1 if high > 0 else np.nan)
        positions_52w.append((price.iloc[i] - low) / (high - low) if high > low else np.nan)
    out["volatility_1y"] = volatility
    out["max_drawdown_1y"] = max_dd
    out["current_drawdown_1y"] = current_dd
    out["position_52w"] = positions_52w
    volume = pd.to_numeric(frame["volume"], errors="coerce")
    out["volume_ratio_5_60"] = volume.rolling(5, min_periods=5).mean() / volume.rolling(60, min_periods=60).mean().replace(0, np.nan)
    for days in (120, 200):
        ma = price.rolling(days, min_periods=days).mean()
        out[f"close_to_ma{days}"] = price / ma.replace(0, np.nan) - 1
        out[f"ma{days}_change_20d"] = ma.pct_change(20, fill_method=None)
    return out
