"""Agent-led Strategy context and minimally constrained decision contract."""

from __future__ import annotations

import copy
from typing import Any

from shared.evidence_cards import (
    assert_no_internal_references_in_reader_text,
    assert_no_opaque_ids,
)

from .contracts_v2 import (
    _dedupe_strings,
    _dict,
    _list,
    _nonempty_string_schema,
    _strict_object,
)


CONTEXT_VERSION = "strategy_context_package_v4"
DECISION_VERSION = "strategy_decision_output_v4"
STRATEGY_CACHE_VERSION = "2"

_CARD_POLICY_FIELDS = {"allowed_sections", "decision_use", "eligibility"}
_PAIR_POLICY_FIELDS = {"allowed_interpretation", "preferred_direction"}
_HANDOFF_INTERNAL_FIELDS = {
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
            "financial": _clean_handoff(
                {
                    "main_view": _dict(reports.get("financial")).get("main_view"),
                    "financial_statement_view": _dict(reports.get("financial")).get(
                        "financial_statement_view"
                    ),
                    "cross_domain_assessments": _dict(reports.get("financial")).get(
                        "secondary_context_assessment"
                    ),
                }
            ),
            "news": _clean_handoff(
                {
                    "analysis_blocks": _dict(
                        _dict(reports.get("news")).get("output")
                    ).get("analysis_blocks"),
                    "cross_domain_assessments": _dict(
                        _dict(reports.get("news")).get("output")
                    ).get("secondary_context_assessment"),
                }
            ),
            "market": _clean_handoff(
                {
                    "main_view": _dict(reports.get("yfinance")).get("main_view"),
                    "time_horizon_view": _dict(reports.get("yfinance")).get(
                        "time_horizon_view"
                    ),
                    "cross_domain_assessments": _dict(reports.get("yfinance")).get(
                        "secondary_context_assessment"
                    ),
                }
            ),
        },
        "evidence_cards": {
            card_key: _neutralize_card(_dict(card))
            for card_key, card in source_cards.items()
        },
        "data_limitations": copy.deepcopy(packet.get("reader_limitations") or []),
        "coverage_summary": copy.deepcopy(packet.get("coverage_summary") or {}),
    }
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


def strategy_decision_response_format_v4(
    context: dict[str, Any],
    *,
    required_horizon: str | None = None,
) -> dict[str, Any]:
    """Return a compact schema that leaves evidence selection to the Strategy LLM."""

    card_keys = sorted(_dict(context.get("evidence_cards")))
    card_ref = {"type": "string", "enum": card_keys}
    card_array = {
        "type": "array",
        "items": card_ref,
        "maxItems": len(card_keys),
    }
    linked_card_array = {**card_array, "minItems": 1}
    brief = _strict_object(
        {
            "horizon": (
                {"type": "string", "enum": [required_horizon]}
                if required_horizon
                else _nonempty_string_schema()
            ),
            "thesis": _nonempty_string_schema(),
            "existing_position_response": _nonempty_string_schema(),
            "new_entry_response": _nonempty_string_schema(),
            "price_context": _nonempty_string_schema(),
            "counterview": _nonempty_string_schema(),
            "limitation_summary": _nonempty_string_schema(),
            "evidence_sufficiency": {
                "type": "string",
                "enum": ["high", "medium", "low"],
            },
            "decision_confidence": {
                "type": "string",
                "enum": ["high", "medium", "low"],
            },
        }
    )
    rationale = _strict_object(
        {
            "point": _nonempty_string_schema(),
            "basis_card_keys": linked_card_array,
        }
    )
    basis_card = _strict_object(
        {
            "card_key": card_ref,
            "role": {
                "type": "string",
                "enum": ["primary", "counter", "monitoring", "context"],
            },
            "usage_reason": _nonempty_string_schema(),
        }
    )
    risk = _strict_object(
        {
            "risk": _nonempty_string_schema(),
            "current_implication": _nonempty_string_schema(),
            "basis_card_keys": linked_card_array,
        }
    )
    schema = _strict_object(
        {
            "decision_version": {"type": "string", "enum": [DECISION_VERSION]},
            "strategy_brief": brief,
            "rationale": {
                "type": "array",
                "items": rationale,
                "maxItems": 4,
            },
            "basis_cards": {
                "type": "array",
                "items": basis_card,
                "minItems": 1,
                "maxItems": len(card_keys),
            },
            "key_risks": {
                "type": "array",
                "items": risk,
                "maxItems": 4,
            },
        }
    )
    return {
        "type": "json_schema",
        "json_schema": {
            "name": "strategy_decision_v4_agent_led",
            "strict": True,
            "schema": schema,
        },
    }


def validate_strategy_decision_v4(
    output: dict[str, Any],
    *,
    context: dict[str, Any],
    required_horizon: str | None = None,
) -> dict[str, Any]:
    """Validate format and evidence links without judging analytical quality."""

    if not isinstance(output, dict) or output.get("decision_version") != DECISION_VERSION:
        raise ValueError(f"Strategy decision_version must be {DECISION_VERSION}.")
    cards = _dict(context.get("evidence_cards"))
    brief = _dict(output.get("strategy_brief"))
    if required_horizon is not None and str(brief.get("horizon") or "") != required_horizon:
        raise ValueError(
            "Strategy decision horizon mismatch: "
            f"expected={required_horizon}, actual={brief.get('horizon')}"
        )
    for field in (
        "thesis",
        "existing_position_response",
        "new_entry_response",
        "price_context",
        "counterview",
        "limitation_summary",
    ):
        if not str(brief.get(field) or "").strip():
            raise ValueError(f"strategy_brief.{field} is required.")
    if brief.get("evidence_sufficiency") not in {"high", "medium", "low"}:
        raise ValueError("strategy_brief.evidence_sufficiency is invalid.")
    if brief.get("decision_confidence") not in {"high", "medium", "low"}:
        raise ValueError("strategy_brief.decision_confidence is invalid.")

    selected: set[str] = set()
    for index, item in enumerate(_list(output.get("basis_cards"))):
        if not isinstance(item, dict):
            raise ValueError(f"basis_cards[{index}] must be an object.")
        card_key = str(item.get("card_key") or "")
        if card_key not in cards:
            raise ValueError(f"basis_cards[{index}] uses an unknown card: {card_key}")
        if card_key in selected:
            raise ValueError(f"basis_cards contains a duplicate card: {card_key}")
        selected.add(card_key)
        if item.get("role") not in {"primary", "counter", "monitoring", "context"}:
            raise ValueError(f"basis_cards[{index}].role is invalid.")
        if not str(item.get("usage_reason") or "").strip():
            raise ValueError(f"basis_cards[{index}].usage_reason is required.")
    if not selected:
        raise ValueError("Strategy decision requires at least one basis card.")

    linked_count = 0
    for collection_name in ("rationale", "key_risks"):
        for index, item in enumerate(_list(output.get(collection_name))):
            if not isinstance(item, dict):
                raise ValueError(f"{collection_name}[{index}] must be an object.")
            refs = _dedupe_strings(item.get("basis_card_keys") or [])
            if not refs:
                raise ValueError(f"{collection_name}[{index}] requires a basis card.")
            unknown = sorted(set(refs) - set(cards))
            if unknown:
                raise ValueError(
                    f"{collection_name}[{index}] uses unknown card(s): {unknown}"
                )
            unselected = sorted(set(refs) - selected)
            if unselected:
                raise ValueError(
                    f"{collection_name}[{index}] references unselected card(s): {unselected}"
                )
            linked_count += len(refs)

    reader_text = _reader_text(output)
    assert_no_internal_references_in_reader_text(
        reader_text,
        card_keys=cards,
        location="strategy_decision_output_v4.reader_text",
    )
    assert_no_opaque_ids(output, location="strategy_decision_output_v4")
    return {
        "evaluation": "strategy_decision_v4_integrity",
        "status": "pass",
        "decision_version": DECISION_VERSION,
        "available_card_count": len(cards),
        "selected_basis_card_count": len(selected),
        "linked_reference_count": linked_count,
        "blocking_failures": [],
    }


def _neutralize_card(card: dict[str, Any]) -> dict[str, Any]:
    neutral = {
        key: copy.deepcopy(value)
        for key, value in card.items()
        if key not in _CARD_POLICY_FIELDS
    }
    observation = neutral.get("primary_observation")
    if isinstance(observation, dict):
        observation.pop("event_materiality", None)
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


def _reader_text(output: dict[str, Any]) -> dict[str, Any]:
    return {
        "strategy_brief": copy.deepcopy(output.get("strategy_brief") or {}),
        "rationale": [
            {"point": item.get("point")}
            for item in _list(output.get("rationale"))
            if isinstance(item, dict)
        ],
        "basis_cards": [
            {"usage_reason": item.get("usage_reason")}
            for item in _list(output.get("basis_cards"))
            if isinstance(item, dict)
        ],
        "key_risks": [
            {key: item.get(key) for key in ("risk", "current_implication")}
            for item in _list(output.get("key_risks"))
            if isinstance(item, dict)
        ],
    }
