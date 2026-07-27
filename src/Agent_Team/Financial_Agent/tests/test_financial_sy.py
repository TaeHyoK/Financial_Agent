from Agent_Team.Financial_Agent.SY_Agent import langgraph_flow as sy


def test_collection_dates_rejects_same_day_or_future_filing() -> None:
    context = {
        "selected_date": "2025-10-31",
        "reports_used": [{"receipt_date": "2025-10-31"}],
    }

    assert sy.collection_dates_valid(context) is False


def test_deterministic_checks_validate_evidence_links_and_numbers() -> None:
    claim = {"claim_id": "F001"}
    evidence = [
        {
            "evidence_id": "E001",
            "claim_id": "F001",
            "source": "DART",
            "value": 100,
            "period_basis": "YTD",
        }
    ]
    checks = sy.deterministic_claim_checks(
        claim=claim,
        evidence_items=evidence,
        collection_context={
            "selected_date": "2025-10-31",
            "reports_used": [{"receipt_date": "2025-08-14"}],
        },
        source_numbers={100.0},
    )

    assert checks["blockers"] == []
    assert checks["numeric_match"] is True
    assert checks["date_valid"] is True


def test_semantic_evaluation_uses_one_batch_and_does_not_rewrite(monkeypatch) -> None:
    calls = []

    def fake_call_openai_json(**kwargs):
        calls.append(kwargs["request_payload"])
        return (
            {
                "evaluations_by_claim_id": {
                    "F001": {
                        "evidence_use": "strong",
                        "reason_ko": "직접 근거가 있다.",
                        "evidence_ids": ["E001"],
                        "limitations": [],
                    }
                },
                "secondary_context_assessment_by_domain": {},
            },
            {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
        )

    monkeypatch.setattr(sy, "call_openai_json", fake_call_openai_json)
    state = {
        "use_llm": True,
        "llm_provider": "openai",
        "llm_model": "gpt-5.4-mini",
        "llm_timeout": 10,
        "extracted_claims": [
            {
                "claim_id": "F001",
                "claim_ko": "매출이 증가했다.",
                "financial_dimension": "growth",
            }
        ],
        "evidence_map": {
            "F001": [
                {
                    "evidence_id": "E001",
                    "claim_id": "F001",
                    "source": "DART",
                    "value": 100,
                    "period_basis": "YTD",
                }
            ]
        },
        "deterministic_checks": {
            "F001": {
                "blockers": [],
                "numeric_match": True,
                "period_comparable": True,
            }
        },
        "source_context": {"collection_context": {}},
        "llm_calls": [],
    }

    result = sy.semantic_evaluation_node(state)

    assert len(calls) == 1
    assert result["claim_validations"][0]["evidence_use"] == "strong"
    assert "rewrite" not in calls[0]["messages"][0]["content"].lower()


def test_verified_report_filters_only_excluded_claims() -> None:
    source = {
        "main_view": {"summary": "원문을 유지한다."},
        "sy_handoff": {
            "financial_claims": [{"claim_id": "F001"}, {"claim_id": "F002"}],
            "key_evidence": [{"claim_id": "F001"}, {"claim_id": "F002"}],
        },
    }
    validations = [
        {"claim_id": "F001", "evidence_use": "strong", "evidence_ids": ["E001"]},
        {"claim_id": "F002", "evidence_use": "exclude", "evidence_ids": []},
    ]

    report = sy.build_verified_financial_report(source, validations)

    assert report["main_view"]["summary"] == "원문을 유지한다."
    assert report["sy_handoff"]["financial_claims"] == [{"claim_id": "F001"}]
    assert report["verification_summary"]["report_rewritten"] is False


def test_financial_sy_request_separates_dart_and_secondary_context() -> None:
    state = {
        "llm_model": "gpt-5.4-mini",
        "source_context": {"collection_context": {}},
        "source_output": {
            "secondary_context": {
                "news": {
                    "status": "available",
                    "evidence_catalog": {
                        "NEWS_RAW_2025-10-31_1": {
                            "evidence_id": "NEWS_RAW_2025-10-31_1",
                            "domain": "news",
                        }
                    },
                }
            }
        },
        "evidence_map": {
            "F001": [
                {
                    "evidence_id": "E001",
                    "claim_id": "F001",
                    "source": "DART",
                    "metric_or_event": "revenue",
                    "value": 100,
                    "period_basis": "YTD",
                    "interpretation_ko": "LLM 입력에서 제거돼야 한다.",
                }
            ]
        },
        "deterministic_checks": {"F001": {"blockers": []}},
    }
    claim = {"claim_id": "F001", "claim_ko": "매출이 증가했다."}

    with_context = sy.build_semantic_request(state, [claim])
    primary_only = sy.build_semantic_request(
        state,
        [claim],
        include_secondary_context=False,
    )
    with_context_content = with_context["messages"][1]["content"]
    primary_only_content = primary_only["messages"][1]["content"]

    assert "NEWS_RAW_2025-10-31_1" in with_context_content
    assert "interpretation_ko" not in with_context_content
    assert "NEWS_RAW_2025-10-31_1" not in primary_only_content
    assert "E001" in primary_only_content
    assert "output_schema" not in with_context_content
    assert with_context["response_format"]["type"] == "json_schema"
    schema = with_context["response_format"]["json_schema"]["schema"]
    assert schema["additionalProperties"] is False
    context_schema = schema["properties"]["secondary_context_assessment_by_domain"]
    assert context_schema["required"] == ["news"]
    assert context_schema["properties"]["news"]["properties"]["secondary_evidence_ids"][
        "items"
    ]["enum"] == ["NEWS_RAW_2025-10-31_1"]
