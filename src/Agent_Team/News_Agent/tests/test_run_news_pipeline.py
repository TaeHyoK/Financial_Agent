from __future__ import annotations

import pytest

from Agent_Team.News_Agent.dart.schemas import RawNewsRecord
from Agent_Team.News_Agent.pipelines.run_news_pipeline import _cap_records_across_window


def _record(article_id: str, article_date: str) -> RawNewsRecord:
    return RawNewsRecord(
        collect_date="2025-10-30",
        article_id=article_id,
        article_date=article_date,
        source="source",
        url=f"https://example.com/{article_id}",
        title=article_id,
        snippet="snippet",
        doc_text=article_id,
        query_used="Target",
        lang="ko",
        fetched_at="2025-10-30T00:00:00Z",
        metadata={"query_day": article_date},
    )


def test_total_window_cap_round_robins_provider_ranked_days() -> None:
    records = [
        _record("new-1", "2025-10-30"),
        _record("new-2", "2025-10-30"),
        _record("new-3", "2025-10-30"),
        _record("old-1", "2025-10-29"),
        _record("old-2", "2025-10-29"),
        _record("old-3", "2025-10-29"),
    ]

    capped = _cap_records_across_window(records, 4)

    assert [record.article_id for record in capped] == [
        "new-1",
        "old-1",
        "new-2",
        "old-2",
    ]


def test_total_window_cap_none_keeps_all_records() -> None:
    records = [_record("one", "2025-10-30"), _record("two", "2025-10-29")]

    assert _cap_records_across_window(records, None) == records


def test_total_window_cap_must_be_positive() -> None:
    with pytest.raises(ValueError, match="must be >= 1"):
        _cap_records_across_window([_record("one", "2025-10-30")], 0)
