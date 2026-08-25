from Agent_Team.News_Agent.SY_Agent import sy_agent as sy
from Agent_Team.News_Agent.analysis_agent import (
    _merge_analysis_anchor_evidence_ids,
    _select_recent_raw_events,
    build_llm_request,
)


def test_news_evidence_date_rejects_future_event() -> None:
    evidence = {"time": "2025-11-01", "source_domain": "news"}

    assert sy.evidence_date_valid(evidence, "2025-10-31") is False


def test_news_evidence_date_rejects_selected_date_event_for_preopen_report() -> None:
    evidence = {"time": "2025-10-31", "source_domain": "news"}

    assert sy.evidence_date_valid(evidence, "2025-10-31") is False


def test_recent_news_events_do_not_infer_relation_from_title_keywords() -> None:
    selected = _select_recent_raw_events(
        {
            "periods": [
                {
                    "period": "2025-10-30",
                    "events": [
                        {
                            "event_id": "1",
                            "title": "대웅제약 바이오 신약 경쟁",
                            "snippet": "기사 원문 요약",
                            "time": "2025-10-30",
                        }
                    ],
                }
            ]
        },
        ["2025-10-30"],
        max_events_per_period=10,
    )

    assert "relation_type" not in selected[0]["events"][0]


def test_news_analysis_anchor_ids_are_merged_into_external_array_contract() -> None:
    output = {
        "analysis_blocks": {
            "news_only": {
                "summary": {
                    "claim": "확인된 사건",
                    "anchor_evidence_id": "NEWS_RAW_1",
                    "evidence_ids": [],
                },
                "positive_signals": [],
                "negative_signals": [],
                "key_risks": [],
                "uncertainties": [],
            }
        },
        "secondary_context_assessment": [
            {
                "primary_anchor_evidence_id": "NEWS_RAW_1",
                "primary_evidence_ids": [],
                "secondary_anchor_evidence_id": "DART_REVENUE",
                "secondary_evidence_ids": [],
            }
        ],
    }

    _merge_analysis_anchor_evidence_ids(output)

    summary = output["analysis_blocks"]["news_only"]["summary"]
    assert summary["evidence_ids"] == ["NEWS_RAW_1"]
    assert "anchor_evidence_id" not in summary
    context = output["secondary_context_assessment"][0]
    assert context["primary_evidence_ids"] == ["NEWS_RAW_1"]
    assert context["secondary_evidence_ids"] == ["DART_REVENUE"]


def test_news_normalization_downgrades_missing_domain() -> None:
    claim = {
        "claim_id": "NCLAIM_001",
        "section": "analysis_blocks.news_plus_market.summary",
        "claim": "뉴스와 시장이 같은 방향이다.",
        "required_evidence_domains": ["news", "market"],
        "declared_evidence_ids": [],
    }
    catalog = {"NEWS_1": {"source_domain": "news"}}
    result = sy.normalize_evaluation(
        claim,
        {"blockers": []},
        {
            "evidence_use": "strong",
            "evidence_ids": ["NEWS_1"],
            "reason_ko": "뉴스 근거만 있다.",
            "limitations": [],
        },
        catalog,
    )

    assert result["evidence_use"] == "context_only"
    assert result["missing_evidence_domains"] == ["market"]


def test_dedupe_news_catalog_keeps_first_matching_event() -> None:
    catalog = {
        "A": {"source_domain": "news", "time": "2025-10-31", "event_id": "1", "title": "same"},
        "B": {"source_domain": "news", "time": "2025-10-31", "event_id": "1", "title": "same"},
    }

    assert list(sy.dedupe_evidence_catalog(catalog)) == ["A"]


def test_data_limitation_can_remain_context_without_evidence_id() -> None:
    claim = {
        "claim_id": "NCLAIM_001",
        "section": "analysis_blocks.news_plus_financial.financial_context_limits[0]",
        "claim": "계약 금액이 공개되지 않았다.",
        "allowed_evidence_domains": ["news", "financial"],
        "required_evidence_domains": [],
        "declared_evidence_ids": [],
    }

    result = sy.normalize_evaluation(
        claim,
        {"blockers": []},
        {
            "evidence_use": "context_only",
            "claim_kind": "data_limitation",
            "evidence_ids": [],
            "applicable_evidence_domains": ["financial"],
            "reason_ko": "입력에 계약 금액이 없다.",
            "limitations": [],
        },
        {},
    )

    assert result["evidence_use"] == "context_only"
    assert result["claim_kind"] == "data_limitation"


def test_extract_claims_uses_news_only_block() -> None:
    output = {
        "analysis_blocks": {
            "news_only": {
                "summary": {"claim": "확인된 뉴스 사건", "evidence_ids": ["NEWS_RAW_2025-10-31_1"]}
            },
            "news_plus_market": {"summary": "제거해야 하는 교차 해석"},
        }
    }

    claims = sy.extract_claims(output, news_claim_limit=10)

    assert [claim["claim"] for claim in claims] == ["확인된 뉴스 사건"]


def test_news_llm_request_separates_secondary_context_and_audit_fields() -> None:
    request = build_llm_request(
        input_payload={
            "target_entity": {"company_name": "테스트", "as_of_date": "2025-10-31"},
            "input_policy": {"summary_periods": ["2025-10-30"], "recent_raw_periods": ["2025-10-31"]},
            "news_context": {"daily_summaries": [], "company_related_top_news": []},
            "cross_domain_context": {"financial": {"secret": 1}},
            "secondary_context": {
                "financial": {
                    "status": "available",
                    "evidence_catalog": {"DART_REVENUE": {"value": 100}},
                },
                "market": {"status": "unavailable", "evidence_catalog": {}},
            },
            "source_paths": {"secret": "/tmp/input.json"},
            "evidence_map": {
                "NEWS_RAW_2025-10-31_1": {"domain": "news", "title": "확인된 뉴스"},
                "DART_REVENUE": {"domain": "financial", "value": 100},
            },
        },
        model="gpt-5.4-mini",
    )

    user_content = request["messages"][1]["content"]
    assert "cross_domain_context" not in user_content
    assert "source_paths" not in user_content
    assert "secondary_context" in user_content
    assert "DART_REVENUE" in user_content
    assert "NEWS_RAW_2025-10-31_1" in user_content
    assert "news_plus_market" not in user_content
    assert "expected_output_schema" not in user_content

    response_format = request["response_format"]
    assert response_format["type"] == "json_schema"
    schema = response_format["json_schema"]["schema"]
    assert response_format["json_schema"]["strict"] is True
    assert schema["additionalProperties"] is False
    blocks = schema["properties"]["analysis_blocks"]
    assert set(blocks["properties"]) == {"news_only"}
    assert blocks["additionalProperties"] is False
    assert blocks["properties"]["news_only"]["properties"]["summary"] == {
        "$ref": "#/$defs/news_claim"
    }
    assert schema["$defs"]["primary_evidence_id"]["enum"] == ["NEWS_RAW_2025-10-31_1"]


def test_news_sy_semantic_request_excludes_secondary_evidence() -> None:
    state = {
        "model": "test-model",
        "source_output": {"target_entity": {"as_of_date": "2025-10-31"}},
        "evidence_map": {
            "NEWS_RAW_2025-10-31_1": {
                "source_domain": "news",
                "title": "확인된 뉴스",
            },
            "NEWS_RAW_2025-10-30_2": {
                "source_domain": "news",
                "title": "현재 claim이 참조하지 않는 뉴스",
            },
            "DART_REVENUE": {"source_domain": "financial", "value": 100},
        },
        "deterministic_checks": {"NCLAIM_001": {"blockers": []}},
    }
    claim = {
        "claim_id": "NCLAIM_001",
        "section": "analysis_blocks.news_only.summary",
        "claim": "확인된 뉴스 사건",
        "original_item": {
            "claim": "확인된 뉴스 사건",
            "evidence_ids": ["NEWS_RAW_2025-10-31_1"],
        },
        "allowed_evidence_domains": ["news"],
        "required_evidence_domains": ["news"],
        "declared_evidence_ids": ["NEWS_RAW_2025-10-31_1"],
    }

    request = sy.build_semantic_request(state, [claim])
    content = request["messages"][1]["content"]

    assert "NEWS_RAW_2025-10-31_1" in content
    assert "NEWS_RAW_2025-10-30_2" not in content
    assert "DART_REVENUE" not in content
    assert "output_schema" not in content
    assert request["response_format"]["type"] == "json_schema"
    evaluation_schema = request["response_format"]["json_schema"]["schema"]["properties"][
        "evaluations_by_claim_id"
    ]
    assert evaluation_schema["required"] == ["NCLAIM_001"]
    assert evaluation_schema["properties"]["NCLAIM_001"]["properties"]["evidence_ids"][
        "items"
    ]["enum"] == ["NEWS_RAW_2025-10-31_1"]
