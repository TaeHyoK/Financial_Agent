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


def final_score(
    rel_rerank: float,
    mention: float,
    time: float,
    impact: float,
    alpha: float,
    beta: float,
    gamma: float,
    delta: float,
) -> float:
    return alpha * rel_rerank + beta * mention + gamma * time + delta * impact
