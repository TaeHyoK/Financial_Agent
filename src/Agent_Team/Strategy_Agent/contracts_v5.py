"""Strategy v5 contract with separate decision and report-context evidence."""

from __future__ import annotations

import copy
from typing import Any

from shared.evidence_cards import (
    assert_no_internal_references_in_reader_text,
    assert_no_opaque_ids,
)

from .contracts_v2 import _dedupe_strings, _dict, _list, _nonempty_string_schema, _strict_object
from .contracts_v4 import build_strategy_context_package_v4


CONTEXT_VERSION = "strategy_context_package_v5"
DECISION_VERSION = "strategy_decision_output_v5"
STRATEGY_CACHE_VERSION = "8"
MAX_DECISION_BASIS_CARDS = 6
MAX_REPORT_CONTEXT_CARDS = 10
MAX_MODEL_REPORT_CONTEXT_CARDS = 0
MAX_TARGET_PEER_METRICS = 2

_INTERNAL_COMPARISON_CARD_KEYS = {"peer.agent_analysis"}
_COVERAGE_DIMENSIONS = (
    "performance",
    "cash_flow",
    "financial_position",
    "market",
    "valuation",
    "events",
    "peer",
)


def build_strategy_context_package_v5(
    packet: dict[str, Any],
    *,
    input_bundle: dict[str, Any],
) -> dict[str, Any]:
    """Build a compact agent context and retain typed limitation requirements."""

    context = build_strategy_context_package_v4(packet, input_bundle=input_bundle)
    context["context_version"] = CONTEXT_VERSION
    context["evidence_cards"].pop("financial.filing_basis", None)
    context["limitation_requirements"] = [
        copy.deepcopy(item)
        for item in _list(packet.get("limitation_requirements"))
        if isinstance(item, dict)
        and item.get("category") not in {"filing_lag", "single_peer_scope"}
    ]
    context["coverage_dimensions"] = {
        dimension: _dimension_card_keys(_dict(context.get("evidence_cards")), dimension)
        for dimension in _COVERAGE_DIMENSIONS
    }
    validate_strategy_context_package_v5(context)
    return context


def validate_strategy_context_package_v5(context: dict[str, Any]) -> None:
    if context.get("context_version") != CONTEXT_VERSION:
        raise ValueError(f"Strategy context_version must be {CONTEXT_VERSION}.")
    target = _dict(context.get("target_company"))
    if not str(target.get("company_name") or "").strip():
        raise ValueError("Strategy context requires target_company.company_name.")
    cards = _dict(context.get("evidence_cards"))
    if not cards:
        raise ValueError("Strategy context requires at least one evidence card.")
    for card_key, card in cards.items():
        if card_key != _dict(card).get("card_key"):
            raise ValueError(f"Strategy context card key mismatch: {card_key}")
        if "primary_observation" not in _dict(card):
            raise ValueError(f"Strategy context card has no observation: {card_key}")
    dimensions = _dict(context.get("coverage_dimensions"))
    if set(dimensions) != set(_COVERAGE_DIMENSIONS):
        raise ValueError("Strategy context coverage_dimensions are incomplete.")
    for dimension, card_keys in dimensions.items():
        unknown = sorted(set(_dedupe_strings(card_keys)) - set(cards))
        if unknown:
            raise ValueError(f"Unknown coverage card(s) for {dimension}: {unknown}")
    assert_no_opaque_ids(context, location="strategy_context_package_v5")


def strategy_decision_response_format_v5(
    context: dict[str, Any],
    *,
    required_horizon: str | None = None,
) -> dict[str, Any]:
    """Return the strict response schema for the v5 Strategy Agent."""

    cards = _dict(context.get("evidence_cards"))
    decision_keys = sorted(set(cards) - _INTERNAL_COMPARISON_CARD_KEYS)
    all_keys = sorted(cards)
    all_ref = {"type": "string", "enum": all_keys}

    def decision_basis_branch(card_key: str) -> dict[str, Any]:
        card = _dict(cards.get(card_key))
        comparable_metrics = sorted(
            {
                str(pair.get("metric_key") or "")
                for pair in _list(_dict(card.get("primary_observation")).get("pairs"))
                if isinstance(pair, dict)
                and pair.get("comparability") == "comparable"
                and str(pair.get("metric_key") or "").strip()
            }
        )
        if str(card.get("domain") or "") == "peer" and comparable_metrics:
            peer_context_schema = _strict_object(
                {
                    "metric_keys": {
                        "type": "array",
                        "items": {"type": "string", "enum": comparable_metrics},
                        "minItems": 1,
                        "maxItems": min(MAX_TARGET_PEER_METRICS, len(comparable_metrics)),
                    },
                    "decision_role": {
                        "type": "string",
                        "enum": ["reinforce", "modify", "context"],
                    },
                    "target_implication": _nonempty_string_schema(),
                }
            )
        else:
            peer_context_schema = {"type": "null"}
        return _strict_object(
            {
                "card_key": {"type": "string", "enum": [card_key]},
                "relation_to_decision": {
                    "type": "string",
                    "enum": ["supports", "opposes", "limits"],
                },
                "importance": {
                    "type": "string",
                    "enum": ["high", "medium", "low"],
                },
                "investment_implication": _nonempty_string_schema(),
                "target_peer_context": peer_context_schema,
            }
        )

    decision_basis = {
        "anyOf": [decision_basis_branch(card_key) for card_key in decision_keys]
    }
    report_context = _strict_object(
        {
            "card_key": all_ref,
            "purpose": {
                "type": "string",
                "enum": [
                    "performance_detail",
                    "business_context",
                    "event_context",
                    "peer_context",
                    "limitation_context",
                ],
            },
            "report_implication": _nonempty_string_schema(),
        }
    )

    def linked_text(*, allow_empty_refs: bool = False) -> dict[str, Any]:
        refs = {
            "type": "array",
            "items": all_ref,
            "maxItems": MAX_DECISION_BASIS_CARDS + MAX_REPORT_CONTEXT_CARDS,
        }
        if not allow_empty_refs:
            refs["minItems"] = 1
        return _strict_object({"text": _nonempty_string_schema(), "card_keys": refs})

    brief = _strict_object(
        {
            "horizon": (
                {"type": "string", "enum": [required_horizon]}
                if required_horizon
                else _nonempty_string_schema()
            ),
            "thesis": linked_text(),
            "existing_position_response": linked_text(),
            "new_entry_response": linked_text(),
            "price_assessment": linked_text(),
            "counterview": linked_text(),
            "decision_limitation": linked_text(allow_empty_refs=True),
            "evidence_sufficiency": {"type": "string", "enum": ["high", "medium", "low"]},
            "decision_confidence": {"type": "string", "enum": ["high", "medium", "low"]},
        }
    )
    insight = _strict_object(
        {
            "insight_type": {
                "type": "string",
                "enum": [
                    "performance_and_financial_position",
                    "price_and_valuation",
                    "events_and_execution",
                ],
            },
            "text": _nonempty_string_schema(),
            "card_keys": {
                "type": "array",
                "items": all_ref,
                "minItems": 1,
                "maxItems": MAX_DECISION_BASIS_CARDS + MAX_REPORT_CONTEXT_CARDS,
            },
        }
    )
    risk = _strict_object(
        {
            "risk_title": _nonempty_string_schema(),
            "risk": _nonempty_string_schema(),
            "current_implication": _nonempty_string_schema(),
            "card_keys": {
                "type": "array",
                "items": all_ref,
                "minItems": 1,
                "maxItems": MAX_DECISION_BASIS_CARDS + MAX_REPORT_CONTEXT_CARDS,
            },
        }
    )

    coverage_properties: dict[str, Any] = {}
    dimensions = _dict(context.get("coverage_dimensions"))
    for dimension in _COVERAGE_DIMENSIONS:
        available = _dedupe_strings(dimensions.get(dimension) or [])
        status_values = ["used", "considered_not_material"] if available else ["unavailable"]
        refs: dict[str, Any] = {
            "type": "array",
            "items": (
                {"type": "string", "enum": available}
                if available
                else {"type": "string"}
            ),
            "maxItems": len(available),
        }
        coverage_properties[dimension] = _strict_object(
            {
                "status": {"type": "string", "enum": status_values},
                "card_keys": refs,
                "reason": _nonempty_string_schema(),
            }
        )

    schema = _strict_object(
        {
            "decision_version": {"type": "string", "enum": [DECISION_VERSION]},
            "evidence_plan": _strict_object(
                {
                    "decision_basis_cards": {
                        "type": "array",
                        "items": decision_basis,
                        "minItems": 1,
                        "maxItems": min(MAX_DECISION_BASIS_CARDS, len(decision_keys)),
                    },
                    "report_context_cards": {
                        "type": "array",
                        "items": report_context,
                        "maxItems": MAX_MODEL_REPORT_CONTEXT_CARDS,
                    },
                    "coverage_assessment": _strict_object(coverage_properties),
                }
            ),
            "strategy_brief": brief,
            "report_insights": {"type": "array", "items": insight, "maxItems": 3},
            "key_risks": {"type": "array", "items": risk, "maxItems": 4},
        }
    )
    return {
        "type": "json_schema",
        "json_schema": {
            "name": "strategy_decision_v5_agent_led",
            "strict": True,
            "schema": schema,
        },
    }


def validate_strategy_decision_v5(
    output: dict[str, Any],
    *,
    context: dict[str, Any],
    required_horizon: str | None = None,
) -> dict[str, Any]:
    """Validate structure and evidence references without judging investment meaning."""

    if not isinstance(output, dict) or output.get("decision_version") != DECISION_VERSION:
        raise ValueError(f"Strategy decision_version must be {DECISION_VERSION}.")
    cards = _dict(context.get("evidence_cards"))
    decision_keys = set(cards) - _INTERNAL_COMPARISON_CARD_KEYS
    plan = _dict(output.get("evidence_plan"))
    decision_items = _list(plan.get("decision_basis_cards"))
    context_items = _list(plan.get("report_context_cards"))
    if not decision_items or len(decision_items) > MAX_DECISION_BASIS_CARDS:
        raise ValueError("Strategy v5 requires 1-6 decision basis cards.")
    if len(context_items) > MAX_REPORT_CONTEXT_CARDS:
        raise ValueError("Strategy v5 exceeds the report-context card budget.")

    selected_decision = _unique_plan_keys(decision_items, allowed=decision_keys, location="decision_basis_cards")
    selected_context = _unique_plan_keys(context_items, allowed=set(cards), location="report_context_cards")
    overlap = sorted(selected_decision & selected_context)
    if overlap:
        raise ValueError(f"Decision and report-context cards overlap: {overlap}")
    selected_all = selected_decision | selected_context

    for index, item in enumerate(decision_items):
        row = _dict(item)
        if row.get("relation_to_decision") not in {"supports", "opposes", "limits"}:
            raise ValueError(f"decision_basis_cards[{index}].relation_to_decision is invalid.")
        if row.get("importance") not in {"high", "medium", "low"}:
            raise ValueError(f"decision_basis_cards[{index}].importance is invalid.")
        if not str(row.get("investment_implication") or "").strip():
            raise ValueError(f"decision_basis_cards[{index}].investment_implication is required.")
        _validate_peer_context(row, cards=cards, index=index)
    for index, item in enumerate(context_items):
        row = _dict(item)
        if row.get("purpose") not in {
            "performance_detail",
            "business_context",
            "event_context",
            "peer_context",
            "limitation_context",
        }:
            raise ValueError(f"report_context_cards[{index}].purpose is invalid.")
        if not str(row.get("report_implication") or "").strip():
            raise ValueError(f"report_context_cards[{index}].report_implication is required.")

    brief = _dict(output.get("strategy_brief"))
    if required_horizon is not None and str(brief.get("horizon") or "") != required_horizon:
        raise ValueError(
            f"Strategy decision horizon mismatch: expected={required_horizon}, actual={brief.get('horizon')}"
        )
    for field in (
        "thesis",
        "existing_position_response",
        "new_entry_response",
        "price_assessment",
        "counterview",
        "decision_limitation",
    ):
        linked = _dict(brief.get(field))
        if not str(linked.get("text") or "").strip():
            raise ValueError(f"strategy_brief.{field}.text is required.")
        refs = _validate_refs(linked.get("card_keys"), allowed=selected_all, location=f"strategy_brief.{field}")
        if field != "decision_limitation" and not refs:
            raise ValueError(f"strategy_brief.{field} requires at least one selected card.")
    if brief.get("evidence_sufficiency") not in {"high", "medium", "low"}:
        raise ValueError("strategy_brief.evidence_sufficiency is invalid.")
    if brief.get("decision_confidence") not in {"high", "medium", "low"}:
        raise ValueError("strategy_brief.decision_confidence is invalid.")

    insight_types: set[str] = set()
    for index, item in enumerate(_list(output.get("report_insights"))):
        row = _dict(item)
        insight_type = str(row.get("insight_type") or "")
        if insight_type not in {
            "performance_and_financial_position",
            "price_and_valuation",
            "events_and_execution",
        }:
            raise ValueError(f"report_insights[{index}].insight_type is invalid.")
        if insight_type in insight_types:
            raise ValueError(f"report_insights contains duplicate type: {insight_type}")
        insight_types.add(insight_type)
        if not str(row.get("text") or "").strip():
            raise ValueError(f"report_insights[{index}].text is required.")
        if not _validate_refs(row.get("card_keys"), allowed=selected_all, location=f"report_insights[{index}]"):
            raise ValueError(f"report_insights[{index}] requires at least one selected card.")

    for index, item in enumerate(_list(output.get("key_risks"))):
        row = _dict(item)
        for field in ("risk_title", "risk", "current_implication"):
            if not str(row.get(field) or "").strip():
                raise ValueError(f"key_risks[{index}].{field} is required.")
        if not _validate_refs(row.get("card_keys"), allowed=selected_all, location=f"key_risks[{index}]"):
            raise ValueError(f"key_risks[{index}] requires a selected evidence card.")

    _validate_coverage(
        _dict(plan.get("coverage_assessment")),
        dimensions=_dict(context.get("coverage_dimensions")),
    )
    assert_no_internal_references_in_reader_text(
        _reader_text(output),
        card_keys=cards,
        location="strategy_decision_output_v5.reader_text",
    )
    assert_no_opaque_ids(output, location="strategy_decision_output_v5")
    return {
        "evaluation": "strategy_decision_v5_integrity",
        "status": "pass",
        "decision_version": DECISION_VERSION,
        "available_card_count": len(cards),
        "decision_basis_card_count": len(selected_decision),
        "report_context_card_count": len(selected_context),
        "blocking_failures": [],
    }


def align_strategy_decision_v5_evidence_plan(
    output: dict[str, Any],
    *,
    context: dict[str, Any],
) -> dict[str, Any]:
    """Promote reader-text references into the report-context plan.

    Structured Outputs can constrain every card key to the available catalog,
    but JSON Schema cannot express that a later reference must also appear in
    an earlier model-selected array. This alignment changes no judgment text:
    it only makes the model's own references explicit in the evidence plan.
    """

    normalized = copy.deepcopy(output)
    cards = _dict(context.get("evidence_cards"))
    plan = _dict(normalized.get("evidence_plan"))
    decision_items = _list(plan.get("decision_basis_cards"))
    context_items = _list(plan.get("report_context_cards"))
    selected_decision = {
        str(_dict(item).get("card_key") or "") for item in decision_items
    }
    selected_context = {str(_dict(item).get("card_key") or "") for item in context_items}
    selected_decision.discard("")
    selected_context.discard("")

    requested: dict[str, dict[str, str]] = {}

    def request_context(card_keys: Any, *, purpose: str, implication: str) -> None:
        for card_key in _dedupe_strings(card_keys or []):
            if card_key in selected_decision or card_key in selected_context:
                continue
            if card_key not in cards:
                continue
            card = _dict(cards.get(card_key))
            resolved_purpose = purpose
            if card.get("domain") == "peer":
                resolved_purpose = "peer_context"
            elif card.get("domain") == "news" and purpose != "limitation_context":
                resolved_purpose = "event_context"
            requested.setdefault(
                card_key,
                {
                    "purpose": resolved_purpose,
                    "report_implication": implication,
                },
            )

    brief = _dict(normalized.get("strategy_brief"))
    limitation = _dict(brief.get("decision_limitation"))
    request_context(
        limitation.get("card_keys"),
        purpose="limitation_context",
        implication=str(limitation.get("text") or "").strip(),
    )
    for field in (
        "thesis",
        "existing_position_response",
        "new_entry_response",
        "price_assessment",
        "counterview",
    ):
        linked = _dict(brief.get(field))
        request_context(
            linked.get("card_keys"),
            purpose="business_context",
            implication=str(linked.get("text") or "").strip(),
        )
    insight_purposes = {
        "performance_and_financial_position": "performance_detail",
        "price_and_valuation": "business_context",
        "events_and_execution": "event_context",
    }
    for item in _list(normalized.get("report_insights")):
        row = _dict(item)
        request_context(
            row.get("card_keys"),
            purpose=insight_purposes.get(
                str(row.get("insight_type") or ""), "business_context"
            ),
            implication=str(row.get("text") or "").strip(),
        )
    for item in _list(normalized.get("key_risks")):
        row = _dict(item)
        request_context(
            row.get("card_keys"),
            purpose="limitation_context",
            implication=str(row.get("current_implication") or row.get("risk") or "").strip(),
        )
    if len(context_items) + len(requested) > MAX_REPORT_CONTEXT_CARDS:
        raise ValueError(
            "Strategy v5 reader references require more report-context cards than the budget allows: "
            f"selected={len(context_items)}, additional={sorted(requested)}, "
            f"budget={MAX_REPORT_CONTEXT_CARDS}."
        )
    for card_key, metadata in requested.items():
        context_items.append({"card_key": card_key, **metadata})
    plan["report_context_cards"] = context_items
    normalized["evidence_plan"] = plan
    return normalized


def _unique_plan_keys(
    items: list[Any],
    *,
    allowed: set[str],
    location: str,
) -> set[str]:
    keys: list[str] = []
    for index, item in enumerate(items):
        if not isinstance(item, dict):
            raise ValueError(f"{location}[{index}] must be an object.")
        card_key = str(item.get("card_key") or "")
        if card_key not in allowed:
            raise ValueError(f"{location}[{index}] uses an unknown card: {card_key}")
        keys.append(card_key)
    if len(keys) != len(set(keys)):
        raise ValueError(f"{location} contains duplicate cards.")
    return set(keys)


def _validate_refs(values: Any, *, allowed: set[str], location: str) -> list[str]:
    refs = _dedupe_strings(values or [])
    raw = [str(value) for value in _list(values)]
    if len(refs) != len(raw):
        raise ValueError(f"{location}.card_keys contains duplicates.")
    unknown = sorted(set(refs) - allowed)
    if unknown:
        raise ValueError(f"{location} references unselected card(s): {unknown}")
    return refs


def _validate_peer_context(row: dict[str, Any], *, cards: dict[str, Any], index: int) -> None:
    card_key = str(row.get("card_key") or "")
    peer_context = row.get("target_peer_context")
    card = _dict(cards.get(card_key))
    pairs = [
        pair
        for pair in _list(_dict(card.get("primary_observation")).get("pairs"))
        if isinstance(pair, dict) and pair.get("comparability") == "comparable"
    ]
    is_structured_peer = str(card.get("domain") or "") == "peer" and bool(pairs)
    if not is_structured_peer:
        if peer_context is not None:
            raise ValueError(f"decision_basis_cards[{index}].target_peer_context must be null.")
        return
    if not isinstance(peer_context, dict):
        raise ValueError(f"decision_basis_cards[{index}].target_peer_context is required.")
    metrics = _dedupe_strings(peer_context.get("metric_keys") or [])
    available = {str(pair.get("metric_key") or "") for pair in pairs}
    if not metrics or len(metrics) > MAX_TARGET_PEER_METRICS or not set(metrics).issubset(available):
        raise ValueError(f"decision_basis_cards[{index}].target_peer_context metrics are invalid.")
    if peer_context.get("decision_role") not in {"reinforce", "modify", "context"}:
        raise ValueError(f"decision_basis_cards[{index}].target_peer_context role is invalid.")
    if not str(peer_context.get("target_implication") or "").strip():
        raise ValueError(f"decision_basis_cards[{index}].target_peer_context implication is required.")


def _validate_coverage(
    coverage: dict[str, Any],
    *,
    dimensions: dict[str, Any],
) -> None:
    if set(coverage) != set(_COVERAGE_DIMENSIONS):
        raise ValueError("coverage_assessment is incomplete.")
    for dimension in _COVERAGE_DIMENSIONS:
        row = _dict(coverage.get(dimension))
        available = set(_dedupe_strings(dimensions.get(dimension) or []))
        refs = _validate_refs(
            row.get("card_keys"),
            allowed=available,
            location=f"coverage_assessment.{dimension}",
        )
        status = row.get("status")
        if available:
            if status not in {"used", "considered_not_material"}:
                raise ValueError(f"coverage_assessment.{dimension}.status is invalid.")
            if status == "used" and not refs:
                raise ValueError(f"coverage_assessment.{dimension} used status requires cards.")
        elif status != "unavailable" or refs:
            raise ValueError(f"coverage_assessment.{dimension} must be unavailable.")
        if not str(row.get("reason") or "").strip():
            raise ValueError(f"coverage_assessment.{dimension}.reason is required.")


def _dimension_card_keys(cards: dict[str, Any], dimension: str) -> list[str]:
    result = []
    for card_key, raw_card in cards.items():
        card = _dict(raw_card)
        domain = str(card.get("domain") or "")
        card_type = str(card.get("card_type") or "")
        include = False
        if dimension == "performance":
            include = card_key in {"financial.same_period_trend", "financial.annual_trend"}
        elif dimension == "cash_flow":
            include = card_key == "financial.cash_flow"
        elif dimension == "financial_position":
            include = card_key in {"financial.balance_sheet", "financial.product_breakdown"}
        elif dimension == "market":
            include = domain == "market"
        elif dimension == "valuation":
            include = domain == "valuation"
        elif dimension == "events":
            include = domain == "news"
        elif dimension == "peer":
            include = domain == "peer"
        if include and card_type != "filing_basis":
            result.append(card_key)
    return sorted(result)


def _reader_text(output: dict[str, Any]) -> dict[str, Any]:
    plan = _dict(output.get("evidence_plan"))
    brief = _dict(output.get("strategy_brief"))
    return {
        "decision_basis_cards": [
            {
                "investment_implication": item.get("investment_implication"),
                "target_peer_context": {
                    "target_implication": _dict(item.get("target_peer_context")).get(
                        "target_implication"
                    )
                }
                if isinstance(item.get("target_peer_context"), dict)
                else None,
            }
            for item in _list(plan.get("decision_basis_cards"))
            if isinstance(item, dict)
        ],
        "report_context_cards": [
            {"report_implication": item.get("report_implication")}
            for item in _list(plan.get("report_context_cards"))
            if isinstance(item, dict)
        ],
        "strategy_brief": {
            key: {"text": _dict(brief.get(key)).get("text")}
            for key in (
                "thesis",
                "existing_position_response",
                "new_entry_response",
                "price_assessment",
                "counterview",
                "decision_limitation",
            )
        },
        "report_insights": [
            {"text": item.get("text")}
            for item in _list(output.get("report_insights"))
            if isinstance(item, dict)
        ],
        "key_risks": [
            {
                key: item.get(key)
                for key in ("risk_title", "risk", "current_implication")
            }
            for item in _list(output.get("key_risks"))
            if isinstance(item, dict)
        ],
    }


__all__ = [
    "CONTEXT_VERSION",
    "DECISION_VERSION",
    "MAX_DECISION_BASIS_CARDS",
    "MAX_REPORT_CONTEXT_CARDS",
    "MAX_MODEL_REPORT_CONTEXT_CARDS",
    "STRATEGY_CACHE_VERSION",
    "build_strategy_context_package_v5",
    "align_strategy_decision_v5_evidence_plan",
    "strategy_decision_response_format_v5",
    "validate_strategy_context_package_v5",
    "validate_strategy_decision_v5",
]
