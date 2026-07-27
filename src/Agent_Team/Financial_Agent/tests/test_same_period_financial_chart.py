from __future__ import annotations

import json

import pytest

from Agent_Team.Financial_Agent.same_period_financial_chart import (
    build_same_period_financial_chart,
    extract_same_period_comparison,
)


def test_extract_same_period_comparison_uses_matching_ytd_periods() -> None:
    comparison = extract_same_period_comparison(_report())
    data = comparison["data"].set_index("metric")

    assert comparison["current_label"] == "2025년 반기 누적"
    assert comparison["previous_label"] == "2024년 반기 누적"
    assert data.loc["revenue", "current_value_100m_krw"] == pytest.approx(35.0)
    assert data.loc["revenue", "previous_value_100m_krw"] == pytest.approx(20.0)
    assert data.loc["revenue", "yoy_change_pct"] == pytest.approx(75.0)


def test_extract_same_period_comparison_rejects_mixed_period_basis() -> None:
    report = _report()
    report["financial_trends"]["current_vs_same_period"]["previous_period"]["basis"] = "FY"

    with pytest.raises(ValueError, match="matching basis"):
        extract_same_period_comparison(report)


def test_standalone_chart_writes_png_csv_and_metadata(tmp_path) -> None:
    source = tmp_path / "final_report.json"
    source.write_text(json.dumps(_report(), ensure_ascii=False), encoding="utf-8")

    result = build_same_period_financial_chart(
        financial_report=source,
        output_dir=tmp_path / "charts",
        output_name="financial.png",
    )

    assert result.chart_path.exists()
    assert result.data_path.exists()
    metadata = json.loads(result.metadata_path.read_text(encoding="utf-8"))
    assert metadata["comparison_basis"] == "YTD"
    assert metadata["integrated_into_final_report"] is False


def _report() -> dict:
    return {
        "target_company": "테스트기업",
        "collection_context": {
            "selected_date": "2025-10-31",
            "selected_date_policy": "prior_day_cutoff_when_receipt_time_unavailable",
            "statement_scope": "separate",
        },
        "financial_trends": {
            "current_vs_same_period": {
                "current_period": {
                    "fiscal_year": 2025,
                    "period_type": "HALF",
                    "period_end": "2025-06-30",
                    "basis": "YTD",
                },
                "previous_period": {
                    "fiscal_year": 2024,
                    "period_type": "HALF",
                    "period_end": "2024-06-30",
                    "basis": "YTD",
                },
                "current_values": {
                    "revenue": 3_500_000_000,
                    "operating_profit": 900_000_000,
                    "net_income": 700_000_000,
                    "operating_cash_flow": 800_000_000,
                },
                "previous_values": {
                    "revenue": 2_000_000_000,
                    "operating_profit": 300_000_000,
                    "net_income": 250_000_000,
                    "operating_cash_flow": 600_000_000,
                },
            }
        },
    }
