from Agent_Team.YFinance_Agent.SY_Agent import sy_agent as sy


def test_yfinance_report_dates_reject_future_valuation_period() -> None:
    report = {
        "as_of_date": "2025-10-31",
        "valuation_snapshot": {"latest_period": {"valuation_date": "2025-12-31"}},
    }

    assert sy.report_dates_valid(report, "2025-10-31") is False


def test_premarket_selected_date_accepts_prior_market_date() -> None:
    report = {
        "selected_date": "2025-10-31",
        "as_of_date": "2025-10-30",
        "valuation_snapshot": {"market_date": "2025-10-30"},
    }

    assert sy.report_dates_valid(report, report["selected_date"]) is True


def test_evidence_catalog_produces_stable_path_refs() -> None:
    report = {"as_of_date": "2025-10-31", "main_view": {"direction": "상승"}}
    records = [
        {"date": "2025-10-01", "stock_close": 100.0, "stock_return_20d": 0.01},
        {"date": "2025-10-31", "stock_close": 110.0, "stock_return_20d": 0.10},
    ]

    first = sy.build_evidence_catalog(report, market_records=records)
    second = sy.build_evidence_catalog(report, market_records=records)

    assert first == second
    assert all(item["origin_type"] in {"raw_source", "deterministic_derived"} for item in first.values())
    assert all("main_view" not in item["source_ref"] for item in first.values())
    assert {item["metric"] for item in first.values()} >= {
        "stock_close",
        "stock_return_20d",
        "stock_period_return",
    }


def test_evidence_catalog_includes_cutoff_market_row_before_selected_date() -> None:
    report = {
        "selected_date": "2025-10-31",
        "as_of_date": "2025-10-30",
    }
    records = [
        {"date": "2025-10-29", "stock_close": 100.0},
        {"date": "2025-10-30", "stock_close": 110.0},
        {"date": "2025-10-31", "stock_close": 120.0},
    ]

    catalog = sy.build_evidence_catalog(report, market_records=records)

    close = next(item for item in catalog.values() if item["metric"] == "stock_close")
    assert close["source_date"] == "2025-10-30"
    assert close["value"] == 110.0


def test_evidence_catalog_never_uses_generated_narrative() -> None:
    report = {
        "as_of_date": "2025-10-31",
        "main_view": {"summary": "생성된 요약", "direction": "상승"},
        "valuation_snapshot": {"status": "available", "trailing_pe": 10.0},
    }

    catalog = sy.build_evidence_catalog(
        report,
        market_records=[{"date": "2025-10-31", "stock_close": 100.0}],
    )

    serialized = str(catalog)
    assert "생성된 요약" not in serialized
    assert "valuation_snapshot" not in serialized


def test_yfinance_normalization_excludes_claim_without_valid_refs() -> None:
    claim = {
        "claim_id": "main_direction",
        "section": "main_view.direction",
        "claim": "상승",
        "required_evidence_domains": ["market"],
        "candidate_evidence_ids": [],
    }
    result = sy.normalize_evaluation(
        claim,
        {"blockers": []},
        {
            "evidence_use": "strong",
            "evidence_ids": ["missing"],
            "reason_ko": "근거 없음",
            "limitations": [],
        },
        {},
    )

    assert result["evidence_use"] == "exclude"


def test_semantic_request_contains_only_primary_market_evidence() -> None:
    state = {
        "llm_model": "test-model",
        "report": {
            "target_company": "테스트",
            "ticker": "000000.KS",
            "as_of_date": "2025-10-31",
        },
        "evidence_catalog": {
            "YF_STOCK_RETURN_20D": {
                "evidence_id": "YF_STOCK_RETURN_20D",
                "domain": "market",
                "source_domains": ["market"],
            }
        },
        "secondary_context_catalog": {
            "NEWS_RAW_1": {"evidence_id": "NEWS_RAW_1", "domain": "news"}
        },
        "deterministic_checks": {"main": {"blockers": []}},
    }
    claim = {
        "claim_id": "main",
        "section": "main_view.direction",
        "claim": "상승",
        "required_evidence_domains": ["market"],
        "candidate_evidence_ids": ["YF_STOCK_RETURN_20D"],
    }

    request = sy.build_semantic_request(state, [claim])
    content = request["messages"][1]["content"]

    assert "YF_STOCK_RETURN_20D" in content
    assert "NEWS_RAW_1" not in content
    assert "output_schema" not in content
    assert request["response_format"]["type"] == "json_schema"
    evaluations = request["response_format"]["json_schema"]["schema"]["properties"][
        "evaluations_by_claim_id"
    ]
    assert evaluations["required"] == ["main"]
    assert evaluations["properties"]["main"]["properties"]["evidence_ids"]["items"][
        "enum"
    ] == ["YF_STOCK_RETURN_20D"]
