"""Cross-order reconciliation and company-clustered aggregate metrics."""

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
OUTCOMES: tuple[str, ...] = ("full_win", "tie", "ablation_win")
RECOMMENDATION_DIMENSIONS: tuple[str, ...] = ("existing_position", "new_entry")
RECOMMENDATION_LABELS = {
    "existing_position": {"increase", "hold", "reduce", "unclear"},
    "new_entry": {"enter", "wait", "avoid", "unclear"},
}
ERROR_TAGS = {
    "unsupported_numeric",
    "incorrect_unit_or_period",
    "temporal_leakage",
    "comparison_scope_error",
    "unsupported_causal_claim",
    "evidence_omission",
    "recommendation_inconsistency",
    "risk_omission",
    "limitation_omission",
    "verbosity_or_repetition",
    "unclear_writing",
}


def validate_judgment(
    payload: dict[str, Any],
    *,
    allowed_card_keys: Iterable[str],
) -> dict[str, Any]:
    """Validate semantic constraints in addition to the API's strict schema."""

    if not isinstance(payload, dict) or set(payload) != {"axes", "recommendations"}:
        raise ValueError("Judge output must contain exactly axes and recommendations.")
    axes = payload.get("axes")
    if not isinstance(axes, dict) or set(axes) != set(AXES):
        raise ValueError(f"Judge output must contain exactly these axes: {list(AXES)}")
    allowed = {str(key) for key in allowed_card_keys}
    required = {
        "winner",
        "reason",
        "supporting_card_keys",
        "candidate_a_error_tags",
        "candidate_b_error_tags",
    }
    for axis in AXES:
        item = axes[axis]
        if not isinstance(item, dict) or set(item) != required:
            raise ValueError(f"Invalid fields for Judge axis {axis!r}.")
        if item.get("winner") not in {"A", "B", "tie"}:
            raise ValueError(f"Invalid winner for Judge axis {axis!r}.")
        if not str(item.get("reason") or "").strip():
            raise ValueError(f"Judge axis {axis!r} requires a reason.")
        keys = item.get("supporting_card_keys")
        if (
            not isinstance(keys, list)
            or len(keys) != len(set(map(str, keys)))
            or any(str(key) not in allowed for key in keys)
        ):
            raise ValueError(f"Judge axis {axis!r} returned invalid supporting keys.")
        for field in ("candidate_a_error_tags", "candidate_b_error_tags"):
            values = item.get(field)
            if (
                not isinstance(values, list)
                or len(values) != len(set(map(str, values)))
                or any(str(value) not in ERROR_TAGS for value in values)
            ):
                raise ValueError(f"Judge axis {axis!r} field {field!r} is invalid.")

    recommendations = payload.get("recommendations")
    if not isinstance(recommendations, dict) or set(recommendations) != {
        "candidate_A",
        "candidate_B",
    }:
        raise ValueError("Judge recommendations must contain candidate_A and candidate_B.")
    for candidate in ("candidate_A", "candidate_B"):
        item = recommendations[candidate]
        if not isinstance(item, dict) or set(item) != set(RECOMMENDATION_DIMENSIONS):
            raise ValueError(f"Invalid recommendation fields for {candidate}.")
        for dimension in RECOMMENDATION_DIMENSIONS:
            if item.get(dimension) not in RECOMMENDATION_LABELS[dimension]:
                raise ValueError(f"Invalid {dimension} classification for {candidate}.")
    return payload


def reconcile_cross_order(
    order_ab: dict[str, Any],
    order_ba: dict[str, Any],
    *,
    allowed_card_keys: Iterable[str],
) -> dict[str, Any]:
    """Map A/B decisions to identities; every order disagreement becomes a tie."""

    first = validate_judgment(order_ab, allowed_card_keys=allowed_card_keys)
    second = validate_judgment(order_ba, allowed_card_keys=allowed_card_keys)
    axes: dict[str, Any] = {}
    for axis in AXES:
        first_axis = first["axes"][axis]
        second_axis = second["axes"][axis]
        first_identity = _winner_identity(first_axis["winner"], {"A": "full", "B": "ablation"})
        second_identity = _winner_identity(second_axis["winner"], {"A": "ablation", "B": "full"})
        consistent = first_identity == second_identity
        if consistent and first_identity == "full":
            outcome = "full_win"
        elif consistent and first_identity == "ablation":
            outcome = "ablation_win"
        else:
            outcome = "tie"
        axes[axis] = {
            "outcome": outcome,
            "score_for_full": _outcome_score(outcome),
            "order_consistent": consistent,
            "order_ab_identity": first_identity,
            "order_ba_identity": second_identity,
            "order_ab_reason": first_axis["reason"],
            "order_ba_reason": second_axis["reason"],
            "supporting_card_keys": sorted(
                set(first_axis["supporting_card_keys"]) | set(second_axis["supporting_card_keys"])
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

    recommendations: dict[str, Any] = {}
    for dimension in RECOMMENDATION_DIMENSIONS:
        full_first = first["recommendations"]["candidate_A"][dimension]
        full_second = second["recommendations"]["candidate_B"][dimension]
        ablation_first = first["recommendations"]["candidate_B"][dimension]
        ablation_second = second["recommendations"]["candidate_A"][dimension]
        full_consistent = full_first == full_second
        ablation_consistent = ablation_first == ablation_second
        full_label = full_first if full_consistent else "unclear"
        ablation_label = ablation_first if ablation_consistent else "unclear"
        comparable = full_label != "unclear" and ablation_label != "unclear"
        recommendations[dimension] = {
            "full_label": full_label,
            "ablation_label": ablation_label,
            "full_order_consistent": full_consistent,
            "ablation_order_consistent": ablation_consistent,
            "flip": full_label != ablation_label if comparable else None,
        }
    return {
        "status": "success",
        "axes": axes,
        "order_consistency_rate": sum(item["order_consistent"] for item in axes.values())
        / len(AXES),
        "recommendations": recommendations,
    }


def aggregate_pair_results(
    results: list[dict[str, Any]],
    *,
    bootstrap_samples: int = 10_000,
    seed: int = 20251031,
) -> dict[str, Any]:
    """Aggregate by ablation and bootstrap company clusters, not individual repeats."""

    conditions = sorted(
        {
            str(item.get("ablation_condition"))
            for item in results
            if item.get("ablation_condition")
        }
    )
    by_condition: dict[str, Any] = {}
    for condition_index, condition in enumerate(conditions):
        attempted = [item for item in results if item.get("ablation_condition") == condition]
        successful = [item for item in attempted if item.get("status") == "success"]
        by_condition[condition] = {
            "attempted_pairs": len(attempted),
            "valid_pairs": len(successful),
            "failed_pairs": sum(item.get("status") == "failed" for item in attempted),
            "dry_run_pairs": sum(item.get("status") == "dry_run" for item in attempted),
            "overall": _aggregate_rows(
                _score_rows(successful, axis=None),
                bootstrap_samples=bootstrap_samples,
                seed=seed + condition_index * 100,
            ),
            "axes": {
                axis: _aggregate_rows(
                    _score_rows(successful, axis=axis),
                    bootstrap_samples=bootstrap_samples,
                    seed=seed + condition_index * 100 + axis_index + 1,
                )
                for axis_index, axis in enumerate(AXES)
            },
            "mean_order_consistency": _mean_or_none(
                [float(item.get("order_consistency_rate") or 0.0) for item in successful]
            ),
            "recommendation": _aggregate_recommendations(successful),
            "error_tags": _aggregate_error_tags(successful),
        }
    return {
        "pair_count": len(results),
        "successful_pair_count": sum(item.get("status") == "success" for item in results),
        "failed_pair_count": sum(item.get("status") == "failed" for item in results),
        "dry_run_pair_count": sum(item.get("status") == "dry_run" for item in results),
        "by_condition": by_condition,
        "repeat_stability": aggregate_repeat_stability(results),
    }


def _observed_recommendation_dimensions(results: list[dict[str, Any]]) -> tuple[str, ...]:
    present = {key for item in results for key in (item.get("recommendations") or {})}
    return tuple(key for key in (*RECOMMENDATION_DIMENSIONS, "rating") if key in present)


def aggregate_repeat_stability(results: list[dict[str, Any]]) -> dict[str, Any]:
    """Measure stance agreement over three runs, deduplicating repeated Full labels."""

    observed: dict[tuple[str, str, int, str], list[str]] = defaultdict(list)
    for item in results:
        if item.get("status") != "success":
            continue
        company = str(item.get("company_name") or "")
        replicate = int(item.get("replicate") or 0)
        condition = str(item.get("ablation_condition") or "")
        for dimension in _observed_recommendation_dimensions(results):
            recommendation = (item.get("recommendations") or {}).get(dimension) or {}
            observed[("full", company, replicate, dimension)].append(
                str(recommendation.get("full_label") or "unclear")
            )
            observed[(condition, company, replicate, dimension)].append(
                str(recommendation.get("ablation_label") or "unclear")
            )
    labels = {
        key: values[0]
        if values and "unclear" not in values and len(set(values)) == 1
        else "unclear"
        for key, values in observed.items()
    }
    conditions = sorted({key[0] for key in labels})
    output: dict[str, Any] = {}
    for condition in conditions:
        output[condition] = {}
        for dimension in _observed_recommendation_dimensions(results):
            grouped: dict[str, list[str]] = defaultdict(list)
            for (row_condition, company, _replicate, row_dimension), label in labels.items():
                if row_condition == condition and row_dimension == dimension and label != "unclear":
                    grouped[company].append(label)
            eligible = [values for values in grouped.values() if len(values) >= 2]
            output[condition][dimension] = {
                "eligible_company_count": len(eligible),
                "unanimous_repeat_rate": (
                    sum(len(set(values)) == 1 for values in eligible) / len(eligible)
                    if eligible
                    else None
                ),
                "mean_majority_agreement": (
                    _mean_or_none(
                        [max(Counter(values).values()) / len(values) for values in eligible]
                    )
                    if eligible
                    else None
                ),
            }
    return output


def _aggregate_recommendations(results: list[dict[str, Any]]) -> dict[str, Any]:
    output: dict[str, Any] = {}
    for dimension in _observed_recommendation_dimensions(results):
        rows = [
            (item.get("recommendations") or {}).get(dimension) or {}
            for item in results
        ]
        comparable = [row for row in rows if row.get("flip") is not None]
        directions = Counter(
            f"{row.get('full_label')}->{row.get('ablation_label')}"
            for row in comparable
            if row.get("flip")
        )
        output[dimension] = {
            "comparable_pairs": len(comparable),
            "flip_count": sum(bool(row.get("flip")) for row in comparable),
            "flip_rate": (
                sum(bool(row.get("flip")) for row in comparable) / len(comparable)
                if comparable
                else None
            ),
            "flip_directions": dict(sorted(directions.items())),
            "full_order_consistency": _mean_or_none(
                [float(bool(row["full_order_consistent"])) for row in rows if row.get("full_order_consistent") is not None]
            ),
            "ablation_order_consistency": _mean_or_none(
                [float(bool(row["ablation_order_consistent"])) for row in rows if row.get("ablation_order_consistent") is not None]
            ),
        }
    return output


def _aggregate_error_tags(results: list[dict[str, Any]]) -> dict[str, dict[str, int]]:
    full: Counter[str] = Counter()
    ablation: Counter[str] = Counter()
    for result in results:
        for axis in AXES:
            item = (result.get("axes") or {}).get(axis) or {}
            full.update(map(str, item.get("full_error_tags") or []))
            ablation.update(map(str, item.get("ablation_error_tags") or []))
    return {"full": dict(sorted(full.items())), "ablation": dict(sorted(ablation.items()))}


def _score_rows(results: list[dict[str, Any]], axis: str | None) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for result in results:
        axes = result.get("axes") if isinstance(result.get("axes"), dict) else {}
        for axis_name in ([axis] if axis else AXES):
            item = axes.get(axis_name) if isinstance(axes.get(axis_name), dict) else {}
            if item.get("outcome") in OUTCOMES:
                rows.append(
                    {
                        "company": str(result.get("company_name") or "unknown"),
                        "outcome": item["outcome"],
                        "score": _outcome_score(item["outcome"]),
                    }
                )
    return rows


def _aggregate_rows(
    rows: list[dict[str, Any]],
    *,
    bootstrap_samples: int,
    seed: int,
) -> dict[str, Any]:
    counts = Counter(str(item["outcome"]) for item in rows)
    companies = sorted({str(item["company"]) for item in rows})
    company_scores = {
        company: sum(float(item["score"]) for item in rows if item["company"] == company)
        / sum(item["company"] == company for item in rows)
        for company in companies
    }
    score = _mean_or_none(list(company_scores.values()))
    lower = upper = None
    status = "insufficient_company_clusters"
    if len(companies) >= 2 and bootstrap_samples > 0:
        rng = random.Random(seed)
        estimates = [
            sum(company_scores[rng.choice(companies)] for _ in companies) / len(companies)
            for _ in range(bootstrap_samples)
        ]
        lower = _percentile(estimates, 0.025)
        upper = _percentile(estimates, 0.975)
        status = "company_cluster_bootstrap"
    return {
        "n": len(rows),
        "company_clusters": len(companies),
        "full_win": counts["full_win"],
        "tie": counts["tie"],
        "ablation_win": counts["ablation_win"],
        "adjusted_win_rate_for_full": score,
        "ci_95": [lower, upper],
        "ci_status": status,
    }


def _winner_identity(winner: str, mapping: dict[str, str]) -> str:
    return "tie" if winner == "tie" else mapping[winner]


def _outcome_score(outcome: str) -> float:
    return {"full_win": 1.0, "tie": 0.5, "ablation_win": 0.0}[outcome]


def _mean_or_none(values: list[float]) -> float | None:
    return sum(values) / len(values) if values else None


def _percentile(values: list[float], quantile: float) -> float:
    ordered = sorted(values)
    position = (len(ordered) - 1) * quantile
    lower = int(position)
    upper = min(lower + 1, len(ordered) - 1)
    fraction = position - lower
    return ordered[lower] * (1.0 - fraction) + ordered[upper] * fraction


__all__ = [
    "AXES",
    "RECOMMENDATION_DIMENSIONS",
    "aggregate_pair_results",
    "aggregate_repeat_stability",
    "reconcile_cross_order",
    "validate_judgment",
]
