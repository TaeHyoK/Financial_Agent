"""Reconciliation and aggregation for blind pairwise report judgments."""

from __future__ import annotations

import random
from collections import Counter, defaultdict
from typing import Any, Iterable


AXES: tuple[str, ...] = (
    "financial_numeric",
    "news",
    "company_market_peer",
    "investment",
    "risk",
    "writing",
)
OUTCOMES = ("full_win", "tie", "ablation_win")


def reconcile_cross_order_judgments(
    order_ab: dict[str, Any],
    order_ba: dict[str, Any],
    *,
    allowed_card_keys: Iterable[str],
) -> dict[str, Any]:
    """Map blind A/B decisions back to identities using conservative FinRpt logic."""

    allowed = set(str(key) for key in allowed_card_keys)
    first = validate_judgment(order_ab, allowed_card_keys=allowed)
    second = validate_judgment(order_ba, allowed_card_keys=allowed)
    reconciled: dict[str, Any] = {}
    for axis in AXES:
        first_axis = first["axes"][axis]
        second_axis = second["axes"][axis]
        first_identity = _winner_identity(first_axis["winner"], {"A": "full", "B": "ablation"})
        second_identity = _winner_identity(second_axis["winner"], {"A": "ablation", "B": "full"})
        order_consistent = first_identity == second_identity
        if order_consistent and first_identity == "full":
            outcome = "full_win"
        elif order_consistent and first_identity == "ablation":
            outcome = "ablation_win"
        else:
            outcome = "tie"
        reconciled[axis] = {
            "outcome": outcome,
            "score_for_full": _outcome_score(outcome),
            "order_consistent": order_consistent,
            "order_ab_identity": first_identity,
            "order_ba_identity": second_identity,
            "order_ab_reason": first_axis["reason"],
            "order_ba_reason": second_axis["reason"],
            "supporting_card_keys": sorted(
                set(first_axis["supporting_card_keys"])
                | set(second_axis["supporting_card_keys"])
            ),
            "full_error_tags": sorted(
                set(first_axis["candidate_a_error_tags"])
                | set(second_axis["candidate_b_error_tags"])
            ),
            "ablation_error_tags": sorted(
                set(first_axis["candidate_b_error_tags"])
                | set(second_axis["candidate_a_error_tags"])
            ),
        }
    return {
        "status": "success",
        "axes": reconciled,
        "order_consistency_rate": sum(
            bool(item["order_consistent"]) for item in reconciled.values()
        )
        / len(AXES),
    }


def validate_judgment(
    payload: dict[str, Any],
    *,
    allowed_card_keys: Iterable[str],
) -> dict[str, Any]:
    """Validate semantic constraints in addition to Structured Outputs."""

    if not isinstance(payload, dict) or set(payload) != {"axes"}:
        raise ValueError("Judge output must contain only the 'axes' object.")
    axes = payload.get("axes")
    if not isinstance(axes, dict) or set(axes) != set(AXES):
        raise ValueError(f"Judge output must contain exactly these axes: {list(AXES)}")
    allowed = set(str(key) for key in allowed_card_keys)
    required_fields = {
        "winner",
        "reason",
        "supporting_card_keys",
        "candidate_a_error_tags",
        "candidate_b_error_tags",
    }
    for axis in AXES:
        item = axes[axis]
        if not isinstance(item, dict) or set(item) != required_fields:
            raise ValueError(f"Invalid fields for Judge axis {axis!r}.")
        if item.get("winner") not in {"A", "B", "tie"}:
            raise ValueError(f"Invalid winner for Judge axis {axis!r}.")
        if not str(item.get("reason") or "").strip():
            raise ValueError(f"Judge axis {axis!r} requires a reason.")
        card_keys = item.get("supporting_card_keys")
        if not isinstance(card_keys, list) or any(str(key) not in allowed for key in card_keys):
            raise ValueError(f"Judge axis {axis!r} returned an unknown supporting card key.")
        for key in ("candidate_a_error_tags", "candidate_b_error_tags"):
            if not isinstance(item.get(key), list):
                raise ValueError(f"Judge axis {axis!r} field {key!r} must be an array.")
    return payload


def aggregate_pair_results(
    pair_results: list[dict[str, Any]],
    *,
    bootstrap_samples: int = 10_000,
    seed: int = 20260728,
) -> dict[str, Any]:
    """Aggregate successful pairs and cluster-bootstrap company-level uncertainty."""

    condition_names = sorted(
        {
            str(item.get("ablation_condition") or "")
            for item in pair_results
            if item.get("ablation_condition")
        }
    )
    by_condition: dict[str, Any] = {}
    for condition in condition_names:
        condition_pairs = [
            item
            for item in pair_results
            if item.get("ablation_condition") == condition and item.get("status") == "success"
        ]
        all_values = _score_rows(condition_pairs, axis=None)
        axes = {
            axis: _aggregate_score_rows(
                _score_rows(condition_pairs, axis=axis),
                bootstrap_samples=bootstrap_samples,
                seed=seed + index + 1,
            )
            for index, axis in enumerate(AXES)
        }
        by_condition[condition] = {
            "attempted_pairs": sum(
                item.get("ablation_condition") == condition
                and item.get("status") in {"success", "failed"}
                for item in pair_results
            ),
            "dry_run_pairs": sum(
                item.get("ablation_condition") == condition and item.get("status") == "dry_run"
                for item in pair_results
            ),
            "valid_pairs": len(condition_pairs),
            "evaluation_errors": sum(
                item.get("ablation_condition") == condition
                and item.get("status") == "failed"
                for item in pair_results
            ),
            "overall": _aggregate_score_rows(
                all_values,
                bootstrap_samples=bootstrap_samples,
                seed=seed,
            ),
            "axes": axes,
            "mean_order_consistency": _mean(
                [float(item.get("order_consistency_rate") or 0.0) for item in condition_pairs]
            ),
        }
    return {
        "pair_count": len(pair_results),
        "successful_pair_count": sum(item.get("status") == "success" for item in pair_results),
        "failed_pair_count": sum(item.get("status") == "failed" for item in pair_results),
        "dry_run_pair_count": sum(item.get("status") == "dry_run" for item in pair_results),
        "by_condition": by_condition,
    }


def aggregate_recommendation_records(records: list[dict[str, Any]]) -> dict[str, Any]:
    """Report Full-to-ablation flips and repeat stability without pseudo-replication."""

    baseline_by_case_rep = {
        (item.get("case_id"), item.get("replicate")): str(item.get("recommendation") or "")
        for item in records
        if item.get("condition") == "full"
    }
    by_condition: dict[str, Any] = {}
    conditions = sorted(
        {str(item.get("condition")) for item in records if item.get("condition") != "full"}
    )
    for condition in conditions:
        condition_records = [item for item in records if item.get("condition") == condition]
        paired: list[tuple[str, str]] = []
        directions: Counter[str] = Counter()
        for item in condition_records:
            baseline = baseline_by_case_rep.get((item.get("case_id"), item.get("replicate")), "")
            candidate = str(item.get("recommendation") or "")
            if baseline and candidate:
                paired.append((baseline, candidate))
                if baseline != candidate:
                    directions[f"{baseline}->{candidate}"] += 1
        repeat_groups: dict[str, list[str]] = defaultdict(list)
        for item in condition_records:
            recommendation = str(item.get("recommendation") or "")
            if recommendation:
                repeat_groups[str(item.get("case_id") or "")].append(recommendation)
        eligible_groups = [values for values in repeat_groups.values() if len(values) >= 2]
        by_condition[condition] = {
            "paired_recommendations": len(paired),
            "flip_count": sum(first != second for first, second in paired),
            "flip_rate": (
                sum(first != second for first, second in paired) / len(paired) if paired else None
            ),
            "flip_directions": dict(sorted(directions.items())),
            "repeat_case_count": len(eligible_groups),
            "unanimous_repeat_rate": (
                sum(len(set(values)) == 1 for values in eligible_groups) / len(eligible_groups)
                if eligible_groups
                else None
            ),
            "mean_majority_agreement": (
                _mean(
                    [
                        max(Counter(values).values()) / len(values)
                        for values in eligible_groups
                    ]
                )
                if eligible_groups
                else None
            ),
        }
    return {"by_condition": by_condition}


def _score_rows(pair_results: list[dict[str, Any]], axis: str | None) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for pair in pair_results:
        axes = pair.get("axes") if isinstance(pair.get("axes"), dict) else {}
        selected_axes = [axis] if axis else list(AXES)
        for axis_name in selected_axes:
            item = axes.get(axis_name) if isinstance(axes.get(axis_name), dict) else {}
            outcome = item.get("outcome")
            if outcome not in OUTCOMES:
                continue
            rows.append(
                {
                    "company": str(pair.get("company_name") or pair.get("case_id") or "unknown"),
                    "outcome": outcome,
                    "score": _outcome_score(outcome),
                }
            )
    return rows


def _aggregate_score_rows(
    rows: list[dict[str, Any]],
    *,
    bootstrap_samples: int,
    seed: int,
) -> dict[str, Any]:
    counts = Counter(str(item["outcome"]) for item in rows)
    companies = sorted({str(item["company"]) for item in rows})
    grouped = {
        company: [item for item in rows if item["company"] == company]
        for company in companies
    }
    company_scores = {
        company: _mean([float(item["score"]) for item in company_rows])
        for company, company_rows in grouped.items()
    }
    score = _mean(list(company_scores.values())) if company_scores else None
    lower: float | None = None
    upper: float | None = None
    ci_status = "insufficient_company_clusters"
    if len(companies) >= 2 and rows and bootstrap_samples > 0:
        rng = random.Random(seed)
        estimates: list[float] = []
        for _ in range(bootstrap_samples):
            sampled = [rng.choice(companies) for _ in companies]
            estimates.append(_mean([company_scores[company] for company in sampled]))
        lower = _percentile(estimates, 0.025)
        upper = _percentile(estimates, 0.975)
        ci_status = "cluster_bootstrap"
    return {
        "n": len(rows),
        "company_clusters": len(companies),
        "full_win": counts["full_win"],
        "tie": counts["tie"],
        "ablation_win": counts["ablation_win"],
        "adjusted_win_rate_for_full": score,
        "ci_95": [lower, upper],
        "ci_status": ci_status,
    }


def _winner_identity(winner: str, mapping: dict[str, str]) -> str:
    return "tie" if winner == "tie" else mapping[winner]


def _outcome_score(outcome: str) -> float:
    return {"full_win": 1.0, "tie": 0.5, "ablation_win": 0.0}[outcome]


def _mean(values: list[float]) -> float:
    return sum(values) / len(values) if values else 0.0


def _percentile(values: list[float], quantile: float) -> float:
    ordered = sorted(values)
    if not ordered:
        raise ValueError("Cannot calculate a percentile of an empty sequence.")
    position = (len(ordered) - 1) * quantile
    lower = int(position)
    upper = min(lower + 1, len(ordered) - 1)
    fraction = position - lower
    return ordered[lower] * (1.0 - fraction) + ordered[upper] * fraction


__all__ = [
    "AXES",
    "aggregate_pair_results",
    "aggregate_recommendation_records",
    "reconcile_cross_order_judgments",
    "validate_judgment",
]
