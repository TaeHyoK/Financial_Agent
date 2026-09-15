"""Scoring utilities for news events."""

from __future__ import annotations

import math
from datetime import datetime


def mention_score(mention_count: int, transform: str = "log1p") -> float:
    if transform == "log1p":
        return math.log1p(max(mention_count, 0))
    return float(mention_count)


def time_score(article_time: datetime, collect_time: datetime, tau_hours: float) -> float:
    delta_hours = abs((collect_time - article_time).total_seconds()) / 3600.0
    tau = max(tau_hours, 1.0)
    return math.exp(-delta_hours / tau)


def minmax_normalize(values: list[float]) -> list[float]:
    """Normalize auxiliary diagnostic values, never the news selection score."""
    if not values:
        return []
    low, high = min(values), max(values)
    if low == high:
        return [0.5 for _ in values]
    return [(value - low) / (high - low) for value in values]
