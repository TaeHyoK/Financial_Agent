from __future__ import annotations

from Agent_Team.Financial_Agent.langgraph_flow import (
    infer_statement_scope,
    normalized_financial_metrics,
    reconcile_revenue_breakdown,
)


def test_statement_scope_and_revenue_reconciliation_are_typed() -> None:
    scope = infer_statement_scope(
        {"4-1": {"tables": [{"table_title": "재무상태표"}]}}
    )
    breakdown = reconcile_revenue_breakdown(
        {
            "status": "available",
            "current_period_key": "p1",
            "totals_by_period": {"p1": {"revenue_krw": 90}},
            "validation": {},
        },
        financial_statement_revenue_krw=100,
        statement_scope=scope,
    )

    reconciliation = breakdown["validation"]["financial_statement_reconciliation"]
    assert scope == "separate"
    assert breakdown["breakdown_scope"] == "unknown"
    assert reconciliation["coverage_ratio"] == 0.9
    assert reconciliation["reconciliation_status"] == "partial"


def test_normalized_financial_metrics_use_same_period_values() -> None:
    result = normalized_financial_metrics(
        {
            "current_vs_same_period": {
                "current_period": {"basis": "YTD"},
                "previous_period": {"basis": "YTD"},
                "current_values": {
                    "revenue": 100,
                    "operating_profit": 20,
                    "net_income": 10,
                    "operating_cash_flow": 15,
                },
                "previous_values": {
                    "revenue": 80,
                    "operating_profit": 8,
                    "net_income": 4,
                    "operating_cash_flow": 12,
                },
            }
        }
    )

    assert result["current_values"] == {
        "operating_margin": 0.2,
        "net_margin": 0.1,
        "operating_cash_flow_margin": 0.15,
    }
    assert result["previous_values"]["operating_margin"] == 0.1
