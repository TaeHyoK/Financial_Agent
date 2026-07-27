from __future__ import annotations

from Agent_Team.Strategy_Agent.agent import validate_strategy_llm_packet
from Agent_Team.Strategy_Agent.evaluate_recommendation_bias import (
    EXPECTED_RECOMMENDATIONS,
    build_counterfactual_packet,
    summarize_mocked_results,
)


def test_counterfactual_packets_satisfy_production_packet_contract() -> None:
    for scenario in EXPECTED_RECOMMENDATIONS:
        packet = build_counterfactual_packet(scenario)
        validate_strategy_llm_packet(packet)
        assert {domain: len(claims) for domain, claims in packet["claim_ledger"].items()} == {
            "financial": 2,
            "news": 1,
            "yfinance": 2,
        }
        assert all(
            claim["evidence_use"] == "strong"
            for claims in packet["claim_ledger"].values()
            for claim in claims
        )


def test_positive_and_negative_scenarios_mirror_core_magnitudes() -> None:
    positive = build_counterfactual_packet("strong_positive")["evidence_catalog"]
    negative = build_counterfactual_packet("strong_negative")["evidence_catalog"]

    assert positive["E_FIN_REVENUE"]["value"] == -negative["E_FIN_REVENUE"]["value"]
    assert positive["E_FIN_CASH_FLOW"]["value"] == -negative["E_FIN_CASH_FLOW"]["value"]
    assert positive["E_NEWS_EVENT"]["value"] == -negative["E_NEWS_EVENT"]["value"]
    assert positive["E_MARKET_RETURN"]["value"]["stock_return_pct"] == -negative[
        "E_MARKET_RETURN"
    ]["value"]["stock_return_pct"]


def test_bias_metrics_detect_extreme_cases_collapsing_to_hold() -> None:
    summary = summarize_mocked_results(
        {
            "strong_positive": "Hold",
            "balanced_mixed": "Hold",
            "strong_negative": "Hold",
        }
    )

    assert summary["passed"] == 1
    assert summary["hold_count"] == 3
