from __future__ import annotations

import json

from Agent_Team.News_Agent.analysis_agent import (
    _analysis_response_format,
    build_llm_request,
)


def test_primary_only_schema_requires_empty_secondary_assessment() -> None:
    response_format = _analysis_response_format(
        {
            "evidence_map": {
                "NEWS_RAW_1": {
                    "domain": "news",
                    "title": "확인된 뉴스",
                }
            },
            "secondary_context": {
                "financial": {"status": "unavailable", "evidence_catalog": {}},
                "market": {"status": "unavailable", "evidence_catalog": {}},
            },
        }
    )

    assessment = response_format["json_schema"]["schema"]["properties"][
        "secondary_context_assessment"
    ]
    assert assessment["minItems"] == 0
    assert assessment["maxItems"] == 0


def test_news_request_requires_chronological_use_of_financial_context() -> None:
    request = build_llm_request(
        input_payload={
            "target_entity": {"company_name": "대상기업", "as_of_date": "2025-10-31"},
            "input_policy": {"summary_periods": [], "recent_raw_periods": []},
            "news_context": {"daily_summaries": [], "company_related_top_news": []},
            "secondary_context": {
                "financial": {
                    "status": "available",
                    "evidence_catalog": {
                        "DART_REVENUE": {
                            "period": "2025-06-30 YTD",
                            "metric": "revenue",
                            "value": 100,
                        }
                    },
                }
            },
            "evidence_map": {
                "NEWS_RAW_1": {
                    "domain": "news",
                    "source_date": "2025-10-20",
                    "title": "사업 계획 발표",
                },
                "DART_REVENUE": {
                    "domain": "financial",
                    "period": "2025-06-30 YTD",
                },
            },
        },
        model="test-model",
    )

    assert request["messages"][1]["content"]
    assert "재무자료가 뉴스보다 앞서면" in request["messages"][0]["content"]
    assert request["messages"][1]["content"].find('"chronology_required": true') >= 0


def test_news_request_places_daily_summaries_before_company_top_news() -> None:
    request = build_llm_request(
        input_payload={
            "target_entity": {"company_name": "대상기업", "as_of_date": "2025-10-31"},
            "input_policy": {
                "summary_periods": ["2025-10-30"],
                "recent_raw_periods": ["2025-10-30"],
            },
            "news_context": {
                "daily_summaries": [
                    {"period": "2025-10-30", "period_summary": "하루 뉴스 요약", "issues": []}
                ],
                "company_related_top_news": [],
            },
            "secondary_context": {},
            "evidence_map": {
                "NEWS_RAW_2025-10-30_1": {
                    "domain": "news",
                    "source_date": "2025-10-30",
                    "relevance_rank": 1,
                    "title": "기업 관련 뉴스",
                }
            },
        },
        model="test-model",
    )

    user_payload = json.loads(request["messages"][1]["content"])["input_payload"]
    keys = list(user_payload)
    assert keys.index("날짜별 요약") < keys.index("기업 관련 뉴스 top-10")
    assert len(user_payload["날짜별 요약"]) == 1
    assert list(user_payload["기업 관련 뉴스 top-10"]) == ["NEWS_RAW_2025-10-30_1"]
