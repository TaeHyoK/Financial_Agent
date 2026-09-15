"""Monthly raw-news allocation; preserves upstream relevance order and source facts."""
from collections import Counter
from datetime import date
from typing import Callable, TypeVar

from .time_windows import monthly_windows

T = TypeVar("T")
MONTHLY_NEWS_POLICY = "monthly_raw_news_v1"
SUMMARY_CITED_NEWS_POLICY = "summary_cited_articles_v1"
SUMMARY_CITED_NEWS_LABEL = "월별 사건 요약에서 인용한 기사"
MONTHLY_NEWS_LIMIT = 2
ANNUAL_NEWS_LIMIT = 24
MONTHLY_NEWS_LABEL = "월별 기업 뉴스(최대 2건)"


def event_period(stamp: str, windows: list[dict[str, str]]) -> str | None:
    stamp = str(stamp or "")[:10]
    try:
        date.fromisoformat(stamp)
    except ValueError:
        return None
    return next((w["period"] for w in windows if w["period_start"] <= stamp <= w["period_end"]), None)


def select_monthly_news(ranked: list[T], *, end_exclusive: date,
                        time_of: Callable[[T], str], id_of: Callable[[T], str],
                        per_month: int = MONTHLY_NEWS_LIMIT) -> list[T]:
    """Take the best existing ranks in each aligned month, then order chronologically."""
    if per_month != MONTHLY_NEWS_LIMIT:
        raise ValueError("Monthly raw-news protocol requires a maximum of two events per month")
    windows = monthly_windows(end_exclusive, 12)
    counts, seen, selected = Counter(), set(), []
    for row in ranked:
        key = str(id_of(row))
        period = event_period(time_of(row), windows)
        if not key or key in seen or period is None:
            continue
        seen.add(key)
        if counts[period] < per_month:
            selected.append(row)
            counts[period] += 1
    return sorted(selected, key=lambda row: (str(time_of(row)), str(id_of(row))))


def validate_monthly_news(rows, *, end_exclusive, time_of, id_of):
    windows = monthly_windows(end_exclusive, 12)
    counts, seen = Counter(), set()
    for row in rows:
        key, period = str(id_of(row)), event_period(time_of(row), windows)
        if not key or key in seen or period is None:
            raise ValueError("Monthly news contains a duplicate, missing ID or out-of-window date")
        seen.add(key)
        counts[period] += 1
    if any(n > MONTHLY_NEWS_LIMIT for n in counts.values()):
        raise ValueError("Monthly news exceeds two events in a month; rebuild selection rather than truncate")
    return {w["period"]: counts[w["period"]] for w in windows}
