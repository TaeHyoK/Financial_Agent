"""Action-oriented Strategy decision contract without rating labels."""

from __future__ import annotations

import copy
from typing import Any

from shared.evidence_cards import (
    PRODUCT_DISCLOSURE_SCOPE_LABEL,
    assert_no_internal_references_in_reader_text,
    assert_no_opaque_ids,
    validate_provenance_map,
)
from .contracts_v2 import (
    INVESTMENT_EFFECTS,
    MATERIALITY_VALUES,
    STRATEGY_SECTIONS,
    _assessment_schema_for_card,
    _card_array_schema,
    _dedupe_strings,
    _dict,
    _is_price_bridge_card,
    _list,
    _nonempty_string_schema,
    _peer_finding_schema,
    _peer_pair_direction,
    _reject_duplicate_strings,
    _requires_product_scope_label,
    _strict_object,
)


DECISION_VERSION = "strategy_decision_output_v3"
STRATEGY_CACHE_VERSION = "1"

def strategy_decision_response_format_v3(
    packet: dict[str, Any],
    *,
    required_horizon: str | None = None,
) -> dict[str, Any]:
    """Return the strict, label-free Strategy output schema."""

    cards = _dict(packet.get("cards"))
    card_keys = sorted(cards)
    card_ref = {"type": "string", "enum": card_keys}
    card_array = {"type": "array", "items": card_ref, "maxItems": len(card_keys)}
    grounded_card_array = {**card_array, "minItems": 1}
    price_card_keys = sorted(
        card_key
        for card_key, card in cards.items()
        if _is_price_bridge_card(_dict(card))
    )
    price_card_array = _card_array_schema(
        allowed_card_keys=price_card_keys,
        fallback_card_keys=card_keys,
    )
    keyed_assessments = _strict_object(
        {
            card_key: _assessment_schema_for_card(
                card_key,
                _dict(cards.get(card_key)),
                include_card_key=False,
            )
            for card_key in card_keys
        }
    )
    peer_finding = _peer_finding_schema(cards)
    peer_card_available = any(
        isinstance(card, dict) and card.get("domain") == "peer"
        for card in cards.values()
    )
    decision = _strict_object(
        {
            "horizon": (
                {"type": "string", "enum": [required_horizon]}
                if required_horizon
                else _nonempty_string_schema()
            ),
            "judgment": _nonempty_string_schema(),
            "current_response": _nonempty_string_schema(),
            "decisive_reason": _nonempty_string_schema(),
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
    decision_basis = _strict_object(
        {
            "judgment_card_keys": grounded_card_array,
            "current_response_card_keys": grounded_card_array,
            "decisive_reason_card_keys": grounded_card_array,
            "counter_evidence": _nonempty_string_schema(),
            "counter_evidence_card_keys": card_array,
            "current_price_context": _nonempty_string_schema(),
            "current_price_card_keys": price_card_array,
        }
    )
    reassessment_condition = _strict_object(
        {
            "signal": _nonempty_string_schema(),
            "response_if_confirmed": _nonempty_string_schema(),
            "response_if_not_confirmed": _nonempty_string_schema(),
            "basis_card_keys": grounded_card_array,
        }
    )
    risk_factor = _strict_object(
        {
            "category": {
                "type": "string",
                "enum": [
                    "business",
                    "financial",
                    "regulatory",
                    "market",
                    "valuation",
                    "execution",
                ],
            },
            "basis_card_keys": grounded_card_array,
            "risk_summary": _nonempty_string_schema(),
            "monitoring_point": _nonempty_string_schema(),
        }
    )
    schema = _strict_object(
        {
            "decision_version": {"type": "string", "enum": [DECISION_VERSION]},
            "decision": decision,
            "decision_basis": decision_basis,
            "reassessment_conditions": {
                "type": "array",
                "items": reassessment_condition,
                "minItems": 1,
                "maxItems": 4,
            },
            "evidence_assessments": keyed_assessments,
            "peer_findings": {
                "type": "array",
                "items": peer_finding,
                "maxItems": 8 if peer_card_available else 0,
            },
            "decision_risk_factors": {
                "type": "array",
                "items": risk_factor,
                "maxItems": 8,
            },
        }
    )
    return {
        "type": "json_schema",
        "json_schema": {
            "name": "strategy_decision_v3_action_oriented",
            "strict": True,
            "schema": schema,
        },
    }


def finalize_strategy_decision_v3(
    output: dict[str, Any],
    packet: dict[str, Any],
) -> dict[str, Any]:
    """Materialize keyed assessments and deterministic audit fields."""

    finalized = copy.deepcopy(output)
    finalized.pop("strategy_report", None)
    cards = _dict(packet.get("cards"))
    raw_assessments = finalized.get("evidence_assessments")
    if isinstance(raw_assessments, dict):
        assessments = [
            {
                "card_key": card_key,
                **{
                    key: value
                    for key, value in _dict(raw_assessments.get(card_key)).items()
                    if key not in {"card_key", "direction"}
                },
            }
            for card_key in sorted(raw_assessments)
        ]
    else:
        assessments = [item for item in _list(raw_assessments) if isinstance(item, dict)]
    for assessment in assessments:
        assessment["direction"] = assessment.get("investment_effect")
    finalized["evidence_assessments"] = assessments

    peer_findings: list[dict[str, Any]] = []
    seen_peer_findings: set[tuple[str, str, str]] = set()
    for item in _list(finalized.get("peer_findings")):
        if not isinstance(item, dict):
            continue
        identity = (
            str(item.get("basis_card_key") or ""),
            str(item.get("peer_company") or ""),
            str(item.get("metric_key") or ""),
        )
        if identity in seen_peer_findings:
            continue
        seen_peer_findings.add(identity)
        finding = copy.deepcopy(item)
        card = _dict(cards.get(str(finding.get("basis_card_key") or "")))
        pair = next(
            (
                row
                for row in _list(_dict(card.get("primary_observation")).get("pairs"))
                if isinstance(row, dict)
                and row.get("metric_key") == finding.get("metric_key")
                and row.get("peer_company") == finding.get("peer_company")
            ),
            None,
        )
        if pair:
            finding["comparison_basis"] = pair.get("target_basis")
            finding["direction"] = _peer_pair_direction(pair)
        peer_findings.append(finding)
    finalized["peer_findings"] = peer_findings

    assessment_by_key = {
        str(item.get("card_key") or ""): item
        for item in assessments
        if item.get("card_key")
    }
    risks = [
        copy.deepcopy(item)
        for item in _list(finalized.get("decision_risk_factors"))
        if isinstance(item, dict)
    ]
    for risk in risks:
        basis = _dedupe_strings(risk.get("basis_card_keys") or [])
        risk["basis_card_keys"] = basis
        risk["reader_summary"] = " ".join(
            str(_dict(assessment_by_key.get(card_key)).get("interpretation") or "").strip()
            for card_key in basis
            if str(_dict(assessment_by_key.get(card_key)).get("interpretation") or "").strip()
        )
        risk["scope_qualifier"] = (
            PRODUCT_DISCLOSURE_SCOPE_LABEL
            if any(_requires_product_scope_label(_dict(cards.get(card_key))) for card_key in basis)
            else "not_applicable"
        )
    finalized["decision_risk_factors"] = risks

    basis = _dict(finalized.get("decision_basis"))
    for key in (
        "judgment_card_keys",
        "current_response_card_keys",
        "decisive_reason_card_keys",
        "counter_evidence_card_keys",
        "current_price_card_keys",
    ):
        basis[key] = _dedupe_strings(basis.get(key) or [])
    finalized["decision_basis"] = basis
    for condition in _list(finalized.get("reassessment_conditions")):
        if isinstance(condition, dict):
            condition["basis_card_keys"] = _dedupe_strings(
                condition.get("basis_card_keys") or []
            )

    section_card_keys = {section: [] for section in STRATEGY_SECTIONS}
    for assessment in assessments:
        section = str(assessment.get("section") or "")
        card_key = str(assessment.get("card_key") or "")
        if section in section_card_keys and card_key not in section_card_keys[section]:
            section_card_keys[section].append(card_key)
    finalized["section_card_keys"] = section_card_keys
    return finalized


def validate_strategy_decision_v3(
    output: dict[str, Any],
    *,
    packet: dict[str, Any],
    provenance: dict[str, Any],
    required_horizon: str | None = None,
    experimental_prose_gate: bool = False,
) -> dict[str, Any]:
    """Evaluate v3 integrity with optional experimental prose rules."""

    if not isinstance(output, dict) or output.get("decision_version") != DECISION_VERSION:
        raise ValueError(f"Strategy decision_version must be {DECISION_VERSION}.")
    cards = _dict(packet.get("cards"))
    validate_provenance_map(cards, provenance)
    decision = _dict(output.get("decision"))
    if required_horizon is not None and str(decision.get("horizon") or "") != required_horizon:
        raise ValueError(
            "Strategy decision horizon mismatch: "
            f"expected={required_horizon}, actual={decision.get('horizon')}"
        )
    for key in ("judgment", "current_response", "decisive_reason"):
        if not str(decision.get(key) or "").strip():
            raise ValueError(f"decision.{key} is required.")
    if decision.get("evidence_sufficiency") not in {"high", "medium", "low"}:
        raise ValueError("decision.evidence_sufficiency is invalid.")
    if decision.get("decision_confidence") not in {"high", "medium", "low"}:
        raise ValueError("decision.decision_confidence is invalid.")
    assessments = _list(output.get("evidence_assessments"))
    assessment_by_key: dict[str, dict[str, Any]] = {}
    for index, assessment in enumerate(assessments):
        if not isinstance(assessment, dict):
            raise ValueError(f"evidence_assessments[{index}] must be an object.")
        card_key = str(assessment.get("card_key") or "")
        if card_key in assessment_by_key:
            raise ValueError(f"Duplicate Strategy assessment card_key: {card_key}")
        card = cards.get(card_key)
        if not isinstance(card, dict):
            raise ValueError(f"Unknown Strategy assessment card_key: {card_key}")
        section = str(assessment.get("section") or "")
        if section not in (card.get("allowed_sections") or []):
            raise ValueError(f"Card {card_key} is not allowed in Strategy section {section}")
        effect = str(assessment.get("investment_effect") or "")
        if effect not in INVESTMENT_EFFECTS or assessment.get("direction") != effect:
            raise ValueError(f"Invalid investment effect for {card_key}: {effect}")
        if assessment.get("materiality") not in MATERIALITY_VALUES:
            raise ValueError(f"Invalid materiality for {card_key}")
        if not str(assessment.get("interpretation") or "").strip():
            raise ValueError(f"Strategy interpretation is required for {card_key}")
        if card.get("eligibility") == "incomparable" and (
            effect not in {"neutral", "reference"}
            or assessment.get("materiality") != "context"
        ):
            raise ValueError(f"Incomparable card cannot drive the decision: {card_key}")
        if card.get("evidence_role") == "reference" and (
            effect not in {"neutral", "reference"}
            or assessment.get("materiality") != "context"
        ):
            raise ValueError(f"Reference card cannot be decisive: {card_key}")
        if card.get("decision_use") == "context_only" and assessment.get("materiality") == "decisive":
            raise ValueError(f"Context-only card cannot be decisive: {card_key}")
        assessment_by_key[card_key] = assessment
    if set(assessment_by_key) != set(cards):
        missing = sorted(set(cards) - set(assessment_by_key))
        extra = sorted(set(assessment_by_key) - set(cards))
        raise ValueError(f"Strategy assessment coverage mismatch: missing={missing}, extra={extra}")

    basis = _dict(output.get("decision_basis"))
    judgment_keys = _validate_known_card_keys(
        basis.get("judgment_card_keys"),
        location="decision_basis.judgment_card_keys",
        cards=cards,
        required=True,
    )
    judgment_factor_keys = [
        card_key
        for card_key in judgment_keys
        if _is_grounded_factor(card_key, cards=cards, assessments=assessment_by_key)
    ]
    if not judgment_factor_keys:
        raise ValueError(
            "decision_basis.judgment_card_keys requires at least one decision factor."
        )
    grounded_groups = {"judgment_card_keys": judgment_factor_keys}
    for key in ("current_response_card_keys", "decisive_reason_card_keys"):
        grounded_groups[key] = _validate_grounded_factor_keys(
            basis.get(key),
            location=f"decision_basis.{key}",
            cards=cards,
            assessments=assessment_by_key,
            required=True,
        )
    counter_keys = _validate_known_card_keys(
        basis.get("counter_evidence_card_keys"),
        location="decision_basis.counter_evidence_card_keys",
        cards=cards,
    )
    if not str(basis.get("counter_evidence") or "").strip():
        raise ValueError("decision_basis.counter_evidence is required.")
    price_keys = _validate_known_card_keys(
        basis.get("current_price_card_keys"),
        location="decision_basis.current_price_card_keys",
        cards=cards,
    )
    if any(not _is_price_bridge_card(_dict(cards.get(key))) for key in price_keys):
        raise ValueError("decision_basis.current_price_card_keys must use price evidence.")
    if not str(basis.get("current_price_context") or "").strip():
        raise ValueError("decision_basis.current_price_context is required.")

    conditions = _list(output.get("reassessment_conditions"))
    if not conditions:
        raise ValueError("At least one reassessment condition is required.")
    for index, condition in enumerate(conditions):
        if not isinstance(condition, dict):
            raise ValueError(f"reassessment_conditions[{index}] must be an object.")
        for key in ("signal", "response_if_confirmed", "response_if_not_confirmed"):
            if not str(condition.get(key) or "").strip():
                raise ValueError(f"reassessment_conditions[{index}].{key} is required.")
        _validate_known_card_keys(
            condition.get("basis_card_keys"),
            location=f"reassessment_conditions[{index}].basis_card_keys",
            cards=cards,
            required=True,
        )

    peer_findings = _validate_peer_findings(output, cards)
    risks = _validate_risk_factors(output, cards)
    section_card_keys = _dict(output.get("section_card_keys"))
    if set(section_card_keys) != set(STRATEGY_SECTIONS):
        raise ValueError("section_card_keys must contain every canonical Strategy section.")
    for section, card_keys in section_card_keys.items():
        _reject_duplicate_strings(card_keys, f"section_card_keys.{section}")
        for card_key in _dedupe_strings(card_keys or []):
            card = cards.get(card_key)
            if not isinstance(card, dict) or section not in (card.get("allowed_sections") or []):
                raise ValueError(f"Invalid section card reference: {section} -> {card_key}")

    reader_text = _strategy_reader_text(output)
    if experimental_prose_gate:
        from .experimental_prose_gate import validate_experimental_prose

        validate_experimental_prose(
            reader_text=reader_text,
            current_response=str(decision.get("current_response") or ""),
        )
    assert_no_internal_references_in_reader_text(
        reader_text,
        card_keys=cards,
        location="strategy_decision_output_v3.reader_text",
    )
    assert_no_opaque_ids(output, location="strategy_decision_output_v3")
    return {
        "evaluation": "strategy_decision_v3_integrity",
        "status": "pass",
        "decision_version": DECISION_VERSION,
        "card_count": len(cards),
        "assessment_count": len(assessments),
        "judgment_factor_count": len(grounded_groups["judgment_card_keys"]),
        "response_factor_count": len(grounded_groups["current_response_card_keys"]),
        "counter_factor_count": len(counter_keys),
        "reassessment_condition_count": len(conditions),
        "peer_finding_count": len(peer_findings),
        "risk_factor_count": len(risks),
        "experimental_prose_gate": experimental_prose_gate,
        "blocking_failures": [],
        "advisory_count": 0,
        "advisories": [],
    }


def _validate_grounded_factor_keys(
    raw_keys: Any,
    *,
    location: str,
    cards: dict[str, Any],
    assessments: dict[str, dict[str, Any]],
    required: bool,
) -> list[str]:
    keys = _validate_known_card_keys(
        raw_keys,
        location=location,
        cards=cards,
        required=required,
    )
    for card_key in keys:
        if not _is_grounded_factor(card_key, cards=cards, assessments=assessments):
            raise ValueError(f"{location} contains non-decision evidence: {card_key}")
    return keys


def _is_grounded_factor(
    card_key: str,
    *,
    cards: dict[str, Any],
    assessments: dict[str, dict[str, Any]],
) -> bool:
    card = _dict(cards.get(card_key))
    assessment = _dict(assessments.get(card_key))
    return (
        card.get("eligibility") == "eligible"
        and card.get("evidence_role") == "primary"
        and card.get("decision_use") == "factor_eligible"
        and assessment.get("materiality") in {"decisive", "supporting"}
        and assessment.get("investment_effect") not in {"neutral", "reference"}
    )


def _validate_known_card_keys(
    raw_keys: Any,
    *,
    location: str,
    cards: dict[str, Any],
    required: bool = False,
) -> list[str]:
    _reject_duplicate_strings(raw_keys, location)
    keys = _dedupe_strings(raw_keys or [])
    if required and not keys:
        raise ValueError(f"{location} requires at least one card.")
    unknown = sorted(set(keys) - set(cards))
    if unknown:
        raise ValueError(f"Unknown card(s) in {location}: {unknown}")
    return keys


def _validate_peer_findings(output: dict[str, Any], cards: dict[str, Any]) -> list[Any]:
    findings = _list(output.get("peer_findings"))
    seen: set[tuple[str, str, str]] = set()
    for index, finding in enumerate(findings):
        if not isinstance(finding, dict):
            raise ValueError(f"peer_findings[{index}] must be an object.")
        card_key = str(finding.get("basis_card_key") or "")
        card = cards.get(card_key)
        if not isinstance(card, dict) or card.get("domain") != "peer":
            raise ValueError(f"Invalid peer finding card: {card_key}")
        metric_key = str(finding.get("metric_key") or "")
        peer_company = str(finding.get("peer_company") or "")
        identity = (card_key, peer_company, metric_key)
        if identity in seen:
            raise ValueError(f"Duplicate peer finding: {identity}")
        seen.add(identity)
        pair = next(
            (
                row
                for row in _list(_dict(card.get("primary_observation")).get("pairs"))
                if isinstance(row, dict)
                and row.get("metric_key") == metric_key
                and row.get("peer_company") == peer_company
            ),
            None,
        )
        if not pair or pair.get("comparability") != "comparable":
            raise ValueError(f"Peer finding uses an incomparable metric: {identity}")
        target_basis = str(pair.get("target_basis") or "")
        if (
            target_basis != str(pair.get("peer_basis") or "")
            or finding.get("comparison_basis") != target_basis
            or finding.get("direction") != _peer_pair_direction(pair)
        ):
            raise ValueError(f"Peer finding basis mismatch: {identity}")
        if not str(finding.get("finding") or "").strip():
            raise ValueError(f"peer_findings[{index}].finding is required.")
    return findings


def _validate_risk_factors(output: dict[str, Any], cards: dict[str, Any]) -> list[Any]:
    risks = _list(output.get("decision_risk_factors"))
    for index, risk in enumerate(risks):
        if not isinstance(risk, dict):
            raise ValueError(f"decision_risk_factors[{index}] must be an object.")
        for key in ("risk_summary", "monitoring_point", "reader_summary"):
            if not str(risk.get(key) or "").strip():
                raise ValueError(f"decision_risk_factors[{index}].{key} is required.")
        basis = _validate_known_card_keys(
            risk.get("basis_card_keys"),
            location=f"decision_risk_factors[{index}].basis_card_keys",
            cards=cards,
            required=True,
        )
        if any(_requires_product_scope_label(_dict(cards.get(key))) for key in basis) and (
            risk.get("scope_qualifier") != PRODUCT_DISCLOSURE_SCOPE_LABEL
        ):
            raise ValueError(
                f"decision_risk_factors[{index}] must preserve the product disclosure scope."
            )
    return risks


def _strategy_reader_text(output: dict[str, Any]) -> dict[str, Any]:
    decision = _dict(output.get("decision"))
    basis = _dict(output.get("decision_basis"))
    return {
        "decision": {
            key: decision.get(key)
            for key in ("judgment", "current_response", "decisive_reason")
        },
        "decision_basis": {
            key: basis.get(key)
            for key in ("counter_evidence", "current_price_context")
        },
        "reassessment_conditions": [
            {
                key: item.get(key)
                for key in ("signal", "response_if_confirmed", "response_if_not_confirmed")
            }
            for item in _list(output.get("reassessment_conditions"))
            if isinstance(item, dict)
        ],
        "evidence_assessments": [
            {"interpretation": item.get("interpretation")}
            for item in _list(output.get("evidence_assessments"))
            if isinstance(item, dict)
        ],
        "peer_findings": [
            {"finding": item.get("finding")}
            for item in _list(output.get("peer_findings"))
            if isinstance(item, dict)
        ],
        "decision_risk_factors": [
            {
                key: item.get(key)
                for key in ("risk_summary", "reader_summary", "monitoring_point")
            }
            for item in _list(output.get("decision_risk_factors"))
            if isinstance(item, dict)
        ],
    }
