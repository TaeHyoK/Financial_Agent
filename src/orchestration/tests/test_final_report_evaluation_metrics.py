from __future__ import annotations

from orchestration.final_report_evaluation_metrics import (
    AXES,
    aggregate_pair_results,
    aggregate_recommendation_records,
    reconcile_cross_order_judgments,
)


def _judgment(winner: str) -> dict:
    return {
        "axes": {
            axis: {
                "winner": winner,
                "reason": "공통 근거에 따른 판정",
                "supporting_card_keys": ["financial.revenue"],
                "candidate_a_error_tags": [],
                "candidate_b_error_tags": [],
            }
            for axis in AXES
        }
    }


def test_cross_order_requires_same_identity_to_win() -> None:
    full_wins = reconcile_cross_order_judgments(
        _judgment("A"),
        _judgment("B"),
        allowed_card_keys=["financial.revenue"],
    )
    inconsistent = reconcile_cross_order_judgments(
        _judgment("A"),
        _judgment("A"),
        allowed_card_keys=["financial.revenue"],
    )

    assert all(item["outcome"] == "full_win" for item in full_wins["axes"].values())
    assert full_wins["order_consistency_rate"] == 1.0
    assert all(item["outcome"] == "tie" for item in inconsistent["axes"].values())
    assert inconsistent["order_consistency_rate"] == 0.0


def test_aggregation_uses_company_clusters_for_confidence_interval() -> None:
    pair_results = []
    for company, outcome in (("회사A", "full_win"), ("회사B", "ablation_win")):
        pair_results.append(
            {
                "status": "success",
                "company_name": company,
                "ablation_condition": "no_sy",
                "order_consistency_rate": 1.0,
                "axes": {
                    axis: {
                        "outcome": outcome,
                        "score_for_full": 1.0 if outcome == "full_win" else 0.0,
                    }
                    for axis in AXES
                },
            }
        )

    aggregate = aggregate_pair_results(pair_results, bootstrap_samples=200, seed=7)
    overall = aggregate["by_condition"]["no_sy"]["overall"]

    assert overall["adjusted_win_rate_for_full"] == 0.5
    assert overall["company_clusters"] == 2
    assert overall["ci_status"] == "cluster_bootstrap"
    assert all(isinstance(value, float) for value in overall["ci_95"])


def test_one_company_does_not_claim_a_confidence_interval() -> None:
    aggregate = aggregate_pair_results(
        [
            {
                "status": "success",
                "company_name": "회사A",
                "ablation_condition": "primary_only",
                "order_consistency_rate": 1.0,
                "axes": {
                    axis: {"outcome": "tie", "score_for_full": 0.5}
                    for axis in AXES
                },
            }
        ]
    )

    overall = aggregate["by_condition"]["primary_only"]["overall"]
    assert overall["adjusted_win_rate_for_full"] == 0.5
    assert overall["ci_95"] == [None, None]
    assert overall["ci_status"] == "insufficient_company_clusters"


def test_recommendation_flip_and_repeat_stability_are_separate() -> None:
    records = [
        {"case_id": "case", "condition": "full", "replicate": 1, "recommendation": "Hold"},
        {"case_id": "case", "condition": "full", "replicate": 2, "recommendation": "Hold"},
        {"case_id": "case", "condition": "no_sy", "replicate": 1, "recommendation": "Buy"},
        {"case_id": "case", "condition": "no_sy", "replicate": 2, "recommendation": "Buy"},
    ]

    result = aggregate_recommendation_records(records)["by_condition"]["no_sy"]

    assert result["flip_rate"] == 1.0
    assert result["flip_directions"] == {"Hold->Buy": 2}
    assert result["unanimous_repeat_rate"] == 1.0
