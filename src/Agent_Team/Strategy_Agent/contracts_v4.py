"""Agent-led Strategy context and minimally constrained decision contract."""

from __future__ import annotations

import copy
from typing import Any

from shared.evidence_cards import assert_no_opaque_ids

from .contracts_v2 import _dict


CONTEXT_VERSION = "strategy_context_package_v4"
DECISION_VERSION = "strategy_decision_output_v4"
STRATEGY_CACHE_VERSION = "6"
MAX_SELECTED_BASIS_CARDS = 6
MAX_TARGET_PEER_CONTEXTS = 2
MAX_TARGET_PEER_METRICS = 2
_INTERNAL_COMPARISON_CARD_KEYS = {"peer.agent_analysis"}

_CARD_POLICY_FIELDS = {"allowed_sections", "decision_use", "eligibility"}
_PAIR_POLICY_FIELDS = {"allowed_interpretation", "preferred_direction"}
_HANDOFF_INTERNAL_FIELDS = {
    "anchor_evidence_id",
    "primary_anchor_evidence_id",
    "secondary_anchor_evidence_id",
    "evidence_ids",
    "source_evidence_ids",
    "source_paths",
    "evidence_map_path",
}


def build_strategy_context_package_v4(
    packet: dict[str, Any],
    *,
    input_bundle: dict[str, Any],
) -> dict[str, Any]:
    """Build a neutral context package without pre-assigning analytical use."""

    source_cards = _dict(packet.get("cards"))
    reports = _dict(input_bundle.get("target_reports"))
    context = {
        "context_version": CONTEXT_VERSION,
        "target_company": copy.deepcopy(packet.get("target_company") or {}),
        "selected_date_policy": packet.get("selected_date_policy"),
        "evidence_scope": copy.deepcopy(packet.get("evidence_scope") or {}),
        "domain_handoffs": {
            "financial": _financial_handoff(_dict(reports.get("financial"))),
            "news": _news_handoff(_dict(reports.get("news"))),
            "market": _market_handoff(_dict(reports.get("yfinance"))),
        },
        "evidence_cards": {
            card_key: _neutralize_card(_dict(card))
            for card_key, card in source_cards.items()
        },
        "data_limitations": copy.deepcopy(packet.get("reader_limitations") or []),
        "coverage_summary": copy.deepcopy(packet.get("coverage_summary") or {}),
    }
    for domain, linked in _dict(packet.get("context_links")).items():
        if domain in context["domain_handoffs"]:
            context["domain_handoffs"][domain]["cross_domain_assessments"] = copy.deepcopy(linked["assessments"])
            context["domain_handoffs"][domain]["conclusion_card_keys"] = copy.deepcopy(linked["conclusion_card_keys"])
    validate_strategy_context_package_v4(context)
    return context


def validate_strategy_context_package_v4(context: dict[str, Any]) -> None:
    """Validate transport integrity without evaluating investment meaning."""

    if context.get("context_version") != CONTEXT_VERSION:
        raise ValueError(f"Strategy context_version must be {CONTEXT_VERSION}.")
    target = _dict(context.get("target_company"))
    if not str(target.get("company_name") or "").strip():
        raise ValueError("Strategy context requires target_company.company_name.")
    cards = _dict(context.get("evidence_cards"))
    if not cards:
        raise ValueError("Strategy context requires at least one evidence card.")
    for card_key, card in cards.items():
        if card_key != card.get("card_key"):
            raise ValueError(f"Strategy context card key mismatch: {card_key}")
        if any(field in card for field in _CARD_POLICY_FIELDS):
            raise ValueError(f"Strategy context contains a policy field: {card_key}")
        if not str(card.get("domain") or "").strip():
            raise ValueError(f"Strategy context card has no domain: {card_key}")
        if "primary_observation" not in card:
            raise ValueError(f"Strategy context card has no observation: {card_key}")
    assert_no_opaque_ids(context, location="strategy_context_package_v4")


def _neutralize_card(card: dict[str, Any]) -> dict[str, Any]:
    neutral = {
        key: copy.deepcopy(value)
        for key, value in card.items()
        if key not in _CARD_POLICY_FIELDS
    }
    observation = neutral.get("primary_observation")
    if isinstance(observation, dict):
        observation.pop("event_materiality", None)
        if neutral.get("card_key") == "financial.same_period_trend":
            directions = _same_period_comparison_directions(observation)
            if directions:
                observation["comparison_directions"] = directions
        pairs = observation.get("pairs")
        if isinstance(pairs, list):
            observation["pairs"] = [
                {
                    key: copy.deepcopy(value)
                    for key, value in pair.items()
                    if key not in _PAIR_POLICY_FIELDS
                }
                if isinstance(pair, dict)
                else copy.deepcopy(pair)
                for pair in pairs
            ]
    return neutral


def _same_period_comparison_directions(
    observation: dict[str, Any],
) -> dict[str, str]:
    """Expose factual metric directions so the LLM need not compare raw magnitudes."""

    current = _dict(observation.get("current_values"))
    previous = _dict(observation.get("previous_values"))
    directions: dict[str, str] = {}
    for metric in current:
        if metric not in previous:
            continue
        current_value = current.get(metric)
        previous_value = previous.get(metric)
        if (
            isinstance(current_value, bool)
            or isinstance(previous_value, bool)
            or not isinstance(current_value, (int, float))
            or not isinstance(previous_value, (int, float))
        ):
            continue
        if current_value > previous_value:
            directions[metric] = "증가"
        elif current_value < previous_value:
            directions[metric] = "감소"
        else:
            directions[metric] = "동일"
    return directions


def _clean_handoff(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            key: _clean_handoff(item)
            for key, item in value.items()
            if key not in _HANDOFF_INTERNAL_FIELDS and not key.endswith("_ids")
        }
    if isinstance(value, list):
        return [_clean_handoff(item) for item in value]
    return copy.deepcopy(value)


def _financial_handoff(report: dict[str, Any]) -> dict[str, Any]:
    """Keep the Financial Agent's judgments without repeating card-level figures."""

    main_view = _dict(report.get("main_view"))
    dimensions = {
        key: {
            field: copy.deepcopy(value[field])
            for field in ("stance", "reasoning")
            if value.get(field) not in (None, "", [], {})
        }
        for key, value in _dict(report.get("financial_statement_view")).items()
        if isinstance(value, dict) and str(value.get("stance") or "").strip()
    }
    return _clean_handoff(
        {
            "main_view": {
                key: copy.deepcopy(main_view.get(key))
                for key in ("summary", "direction", "main_cautions")
                if main_view.get(key) not in (None, "", [], {})
            },
            "dimension_assessments": dimensions,
            "cross_domain_assessments": report.get("secondary_context_assessment"),
        }
    )


def _news_handoff(report: dict[str, Any]) -> dict[str, Any]:
    """Keep the News Agent's portfolio-level view; event facts remain in cards."""

    output = _dict(report.get("output")) or report
    news_only = _dict(_dict(output.get("analysis_blocks")).get("news_only"))
    return _clean_handoff(
        {
            "overall_assessment": output.get("overall_assessment") or news_only.get("summary"),
            "cross_domain_assessments": output.get("secondary_context_assessment"),
        }
    )


def _market_handoff(report: dict[str, Any]) -> dict[str, Any]:
    """Keep horizon judgments while leaving repeated feature values in cards."""

    main_view = _dict(report.get("main_view"))
    horizons = {
        key: {
            field: copy.deepcopy(value.get(field))
            for field in ("stance", "reasoning", "data_limitation")
            if value.get(field) not in (None, "", [], {})
        }
        for key, value in _dict(report.get("time_horizon_view")).items()
        if isinstance(value, dict)
    }
    return _clean_handoff(
        {
            "main_view": {
                key: copy.deepcopy(main_view.get(key))
                for key in ("summary", "direction")
                if main_view.get(key) not in (None, "", [], {})
            },
            "horizon_assessments": horizons,
            "cross_domain_assessments": report.get("secondary_context_assessment"),
        }
    )
