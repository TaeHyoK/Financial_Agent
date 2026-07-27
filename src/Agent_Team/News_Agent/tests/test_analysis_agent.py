from __future__ import annotations

from Agent_Team.News_Agent.analysis_agent import _analysis_response_format


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
