from __future__ import annotations

import json
from pathlib import Path

from Agent_Team.News_Agent.context_export import (
    _attach_source_event_ids,
    build_context_exports,
)


def _event(index: int) -> dict:
    day = "2025-10-30" if index % 2 else "2025-10-29"
    return {
        "event_id": str(index),
        "relevance_rank": index,
        "mention_count": 1,
        "representative": {
            "title": f"뉴스 {index}",
            "snippet": f"뉴스 본문 {index}",
            "source": "테스트 매체",
            "time": day,
            "url": f"https://example.com/{index}",
        },
        "articles": [],
        "scores": {"final_score": float(100 - index)},
    }


def test_context_export_keeps_30_daily_inputs_and_company_top_10(tmp_path) -> None:
    report_path = tmp_path / "report_context.json"
    events = [_event(index) for index in range(1, 13)]
    report_path.write_text(
        json.dumps(
            {
                "collect_date": "2025-10-30",
                "company": {"company_name": "대상기업"},
                "corporate_context": {},
                "news_events_all": events,
                "news_events_topk": events[:10],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    paths = build_context_exports(
        report_context_path=report_path,
        output_dir=tmp_path / "day",
        granularity="day",
        period_count=30,
        min_mention_count=1,
    )

    summary_input = json.loads(
        Path(paths["summary_prompt_input_path"]).read_text(encoding="utf-8")
    )
    top_news_input = json.loads(
        Path(paths["recent_raw_input_path"]).read_text(encoding="utf-8")
    )
    manifest = json.loads(Path(paths["manifest_path"]).read_text(encoding="utf-8"))

    assert len(summary_input["periods"]) == 30
    assert sum(period["event_count"] for period in summary_input["periods"]) == 12
    assert top_news_input["selection"] == "기업 관련 뉴스 top-10"
    assert "url" not in json.dumps(top_news_input, ensure_ascii=False)
    assert [event["event_id"] for event in top_news_input["events"]] == [
        str(index) for index in range(1, 11)
    ]
    assert manifest["summary_periods_for_news_agent"] == [
        period["period"] for period in summary_input["periods"]
    ]
    assert manifest["company_related_news_count"] == 10


def test_period_summary_keeps_source_event_ids_without_urls() -> None:
    output = {
        "periods": [
            {"period": "2025-10-30", "period_summary": "하루 뉴스 요약", "issues": []}
        ]
    }
    request = {
        "messages": [
            {
                "role": "user",
                "content": json.dumps(
                    {
                        "periods": [
                            {
                                "period": "2025-10-30",
                                "events": [
                                    {
                                        "event_id": "1",
                                        "title": "기업 관련 뉴스",
                                        "url": "https://example.com/not-forwarded",
                                    }
                                ],
                            }
                        ]
                    }
                ),
            }
        ]
    }

    _attach_source_event_ids(output, request)

    assert output["periods"][0]["source_event_ids"] == ["NEWS_RAW_2025-10-30_1"]
    assert "url" not in json.dumps(output)
