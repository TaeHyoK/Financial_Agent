"""Strategy decision contract with separate decision and report-context evidence."""

from __future__ import annotations

import copy
from typing import Any

from shared.evidence_cards import (
    assert_no_internal_references_in_reader_text,
    assert_no_opaque_ids,
)

from shared.schema import strict_object as _strict_object

from .packet import _dedupe_strings, _dict, _list, _nonempty_string_schema
from .context import build_base_strategy_context


CONTEXT_VERSION = "strategy_context_package_v5"
DECISION_VERSION = "strategy_decision_output_v5"
STRATEGY_CACHE_VERSION = "19"
SCHEMA_REVISION = "12m_v3"
# Counts are editorial guidance, not limits on preserving valid citations.
# The model leaves this duplicate index empty; alignment fills it afterwards.
MAX_MODEL_REPORT_CONTEXT_CARDS = 0

_COVERAGE_DIMENSIONS = (
    "performance",
    "cash_flow",
    "financial_position",
    "market",
    "valuation",
    "events",
    "peer",
)

_NEWS_SELECTION_METADATA = {"relevance_rank", "final_score", "scores", "ablation_selection"}


def _without_news_selection_metadata(value: Any) -> Any:
    """Project news observations for generation; keep selection diagnostics on disk."""
    if isinstance(value, dict):
        return {key: _without_news_selection_metadata(item)
                for key, item in value.items() if key not in _NEWS_SELECTION_METADATA}
    if isinstance(value, list):
        return [_without_news_selection_metadata(item) for item in value]
    return value


def build_strategy_context_package(
    packet: dict[str, Any],
    *,
    input_bundle: dict[str, Any],
) -> dict[str, Any]:
    """Build a compact agent context and retain typed limitation requirements."""

    context = build_base_strategy_context(packet, input_bundle=input_bundle)
    context["context_version"] = CONTEXT_VERSION
    context["evidence_cards"].pop("financial.filing_basis", None)
    for card in context["evidence_cards"].values():
        # Legacy validation labels are not an agent's assessment of importance.
        # Apply the same projection to every domain; retain factual source scope.
        card.pop("evidence_role", None)
        card.pop("machine_blockers", None)
        if card.get("domain") == "news":
            card["primary_observation"] = _without_news_selection_metadata(card["primary_observation"])
            if card["primary_observation"].get("cited_sources"):
                # The same excerpts are now carried with their individual dates.
                card["primary_observation"].pop("representative_excerpts", None)
    if "peer.agent_analysis" in context["evidence_cards"]:
        context["evidence_cards"]["peer.agent_analysis"]["evidence_origin"] = "model_interpreted"
    context["limitation_requirements"] = [
        copy.deepcopy(item)
        for item in _list(packet.get("limitation_requirements"))
        if isinstance(item, dict)
        and item.get("category") not in {"filing_lag", "single_peer_scope", "news_financial_link"}
    ]
    context["coverage_dimensions"] = {
        dimension: _dimension_card_keys(_dict(context.get("evidence_cards")), dimension)
        for dimension in _COVERAGE_DIMENSIONS
    }
    # Preserve content; separate observations, interpretations and applicability.
    # Only exact duplicate notes are merged. No sentiment/materiality filtering.
    notes = context.pop("data_limitations", [])
    shared_notes, scoped_notes = [], {}
    for note in notes:
        if not isinstance(note, dict):
            if note not in shared_notes:
                shared_notes.append(copy.deepcopy(note))
            continue
        key = str(note.get("basis_card_key") or "")
        if key in context["evidence_cards"]:
            values = scoped_notes.setdefault(key, [])
        else:
            values = shared_notes
        if note not in values:
            values.append(copy.deepcopy(note))
    interpretations = context.pop("domain_handoffs")
    context["input_roles"] = {
        "evidence_cards": "인용 가능한 관측·자료 카드다. 원천 수치와 모델 요약을 evidence_origin 등 출처 속성으로 구분한다.",
        "domain_handoffs": "하위 에이전트의 해석이다. 근거와 대조해 수용·보완·수정할 수 있으며 새로운 관측 사실로 취급하지 않는다.",
        "applicability_notes": "자료의 적용 범위다. 관련 판단에만 적용하며 일괄적인 부정 근거나 보고서 필수 문구가 아니다. 카드별 기간·출처 속성도 유지된다.",
    }
    context["domain_handoffs"] = interpretations
    context["applicability_notes"] = {"by_card": scoped_notes, "shared": shared_notes}
    context["context_layout_revision"] = "observations_interpretations_scope_v1"
    validate_strategy_context_package(context)
    return context


def validate_strategy_context_package(context: dict[str, Any]) -> None:
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


def strategy_decision_response_format(
    context: dict[str, Any],
    *,
    required_horizon: str | None = None,
) -> dict[str, Any]:
    """Return the strict response schema for the Strategy decision."""

    cards = _dict(context.get("evidence_cards"))
    decision_keys = sorted(cards)
    all_keys = sorted(cards)
    # Reuse the same allowed IDs without repeating the enum across every field.
    # This preserves all evidence and strict validation as the news input grows.
    all_ref = {"$ref": "#/$defs/evidence_card_key"}

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

    def linked_text(*, allow_empty_refs: bool = False, allow_empty_text: bool = False) -> dict[str, Any]:
        refs = {
            "type": "array",
            "items": all_ref,
        }
        if not allow_empty_refs:
            refs["minItems"] = 1
        return _strict_object({"text": {"type": "string"} if allow_empty_text else _nonempty_string_schema(), "card_keys": refs})

    brief = _strict_object(
        {
            "horizon": (
                {"type": "string", "enum": [required_horizon]}
                if required_horizon
                else _nonempty_string_schema()
            ),
            "earnings_review": linked_text(),
            "outlook": linked_text(),
            "price_assessment": linked_text(),
            "counterview": linked_text(allow_empty_refs=True),
            "decision_rationale": linked_text(),
            "recommendation": {"type": "string", "enum": ["Buy", "Hold", "Sell"]},
            "headline": _nonempty_string_schema(),
            "thesis": linked_text(),
            "decision_limitation": linked_text(allow_empty_refs=True, allow_empty_text=True),
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
            "schema_revision": {"type": "string", "enum": [SCHEMA_REVISION]},
            "strategy_brief": brief,
            "evidence_plan": _strict_object(
                {
                    "decision_basis_cards": {
                        "type": "array",
                        "items": decision_basis,
                        "minItems": 1,
                    },
                    "report_context_cards": {
                        "type": "array",
                        "items": report_context,
                        "maxItems": MAX_MODEL_REPORT_CONTEXT_CARDS,
                    },
                    "coverage_assessment": _strict_object(coverage_properties),
                }
            ),
            "report_insights": {"type": "array", "items": insight},
            "key_risks": {"type": "array", "items": risk},
        }
    )
    schema["$defs"] = {"evidence_card_key": {"type": "string", "enum": all_keys}}
    return {
        "type": "json_schema",
        "json_schema": {
            "name": "strategy_decision_v5_agent_led",
            "strict": True,
            "schema": schema,
        },
    }


def validate_strategy_decision(
    output: dict[str, Any],
    *,
    context: dict[str, Any],
    required_horizon: str | None = None,
) -> dict[str, Any]:
    """Validate structure and evidence references without judging investment meaning."""

    if not isinstance(output, dict) or output.get("decision_version") != DECISION_VERSION:
        raise ValueError(f"Strategy decision_version must be {DECISION_VERSION}.")
    cards = _dict(context.get("evidence_cards"))
    decision_keys = set(cards)
    plan = _dict(output.get("evidence_plan"))
    decision_items = _list(plan.get("decision_basis_cards"))
    context_items = _list(plan.get("report_context_cards"))
    if not decision_items:
        raise ValueError("Strategy v5 requires at least one decision-basis card.")

    selected_decision = _unique_plan_keys(decision_items, allowed=decision_keys, location="decision_basis_cards")
    selected_context = _unique_plan_keys(context_items, allowed=set(cards), location="report_context_cards")
    overlap = sorted(selected_decision & selected_context)
    if overlap:
        raise ValueError(f"Decision and report-context cards overlap: {overlap}")
    selected_all = selected_decision | selected_context

    for index, item in enumerate(decision_items):
        row = _dict(item)
        if "relation_to_decision" in row:
            raise ValueError("12m_v3 uses investment_implication, not relation_to_decision labels; regenerate.")
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
    if output.get("schema_revision") != SCHEMA_REVISION:
        raise ValueError(f"Strategy output requires schema_revision={SCHEMA_REVISION}; regenerate old cached decisions.")
    if brief.get("recommendation") not in {"Buy", "Hold", "Sell"}:
        raise ValueError("Strategy recommendation must be Buy/Hold/Sell.")
    if not str(brief.get("headline") or "").strip():
        raise ValueError("Strategy headline is required.")
    if required_horizon is not None and str(brief.get("horizon") or "") != required_horizon:
        raise ValueError(
            f"Strategy decision horizon mismatch: expected={required_horizon}, actual={brief.get('horizon')}"
        )
    for field in (
        "thesis",
        "earnings_review",
        "outlook",
        "price_assessment",
        "counterview",
        "decision_rationale",
        "decision_limitation",
    ):
        linked = _dict(brief.get(field))
        if field != "decision_limitation" and not str(linked.get("text") or "").strip():
            raise ValueError(f"strategy_brief.{field}.text is required.")
        refs = _validate_refs(linked.get("card_keys"), allowed=selected_all, location=f"strategy_brief.{field}")
        if field == "decision_limitation":
            if not isinstance(linked.get("text"), str):
                raise ValueError("strategy_brief.decision_limitation.text must be a string.")
            if not linked["text"].strip() and refs:
                raise ValueError("Empty decision_limitation must not cite cards.")
        if field not in {"decision_limitation", "counterview"} and not refs:
            raise ValueError(f"strategy_brief.{field} requires at least one selected card.")
    if brief.get("evidence_sufficiency") not in {"high", "medium", "low"}:
        raise ValueError("strategy_brief.evidence_sufficiency is invalid.")
    if brief.get("decision_confidence") not in {"high", "medium", "low"}:
        raise ValueError("strategy_brief.decision_confidence is invalid.")

    for index, item in enumerate(_list(output.get("report_insights"))):
        row = _dict(item)
        insight_type = str(row.get("insight_type") or "")
        if insight_type not in {
            "performance_and_financial_position",
            "price_and_valuation",
            "events_and_execution",
        }:
            raise ValueError(f"report_insights[{index}].insight_type is invalid.")
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


def align_strategy_decision_evidence_plan(
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
                raise ValueError(f"Strategy reader text references unknown card: {card_key}")
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
        "earnings_review",
        "outlook",
        "price_assessment",
        "counterview",
        "decision_rationale",
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
    if not metrics or not set(metrics).issubset(available):
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
        "headline": brief.get("headline"),
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
                "earnings_review",
                "outlook",
                "price_assessment",
                "counterview",
                "decision_rationale",
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
    "MAX_MODEL_REPORT_CONTEXT_CARDS",
    "STRATEGY_CACHE_VERSION",
    "build_strategy_context_package",
    "align_strategy_decision_evidence_plan",
    "strategy_decision_response_format",
    "validate_strategy_context_package",
    "validate_strategy_decision",
]
