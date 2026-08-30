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
STRATEGY_CACHE_VERSION = "6"
MAX_SELECTED_BASIS_CARDS = 6
MAX_TARGET_PEER_CONTEXTS = 2
MAX_TARGET_PEER_METRICS = 2
_INTERNAL_COMPARISON_CARD_KEYS = {"peer.agent_analysis"}

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

    cards = _dict(context.get("evidence_cards"))
    card_keys = _decision_card_keys(cards)
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
    risk = _strict_object(
        {
            "risk_title": _nonempty_string_schema(),
            "risk": _nonempty_string_schema(),
            "current_implication": _nonempty_string_schema(),
            "basis_card_keys": linked_card_array,
        }
    )
    peer_card_keys = _structured_peer_card_keys(cards)
    peer_metric_keys = sorted(
        {
            str(pair.get("metric_key") or "")
            for card_key in peer_card_keys
            for pair in _list(_dict(cards[card_key].get("primary_observation")).get("pairs"))
            if isinstance(pair, dict) and str(pair.get("metric_key") or "").strip()
        }
    )
    peer_context = _strict_object(
        {
            "metric_keys": {
                "type": "array",
                "items": (
                    {"type": "string", "enum": peer_metric_keys}
                    if peer_metric_keys
                    else {"type": "string"}
                ),
                "minItems": 1,
                "maxItems": MAX_TARGET_PEER_METRICS,
            },
            "decision_role": {
                "type": "string",
                "enum": ["reinforce", "modify", "context"],
            },
            "target_implication": _nonempty_string_schema(),
        }
    )
    peer_context["type"] = ["object", "null"]
    basis_card = _strict_object(
        {
            "card_key": card_ref,
            "role": {
                "type": "string",
                "enum": ["primary", "counter", "monitoring", "context"],
            },
            "usage_reason": _nonempty_string_schema(),
            "target_peer_context": peer_context,
        }
    )
    schema = _strict_object(
        {
            "decision_version": {"type": "string", "enum": [DECISION_VERSION]},
            # Put evidence selection before prose so later references are grounded in
            # the bounded set already chosen by the Strategy model.
            "basis_cards": {
                "type": "array",
                "items": basis_card,
                "minItems": 1,
                "maxItems": min(MAX_SELECTED_BASIS_CARDS, len(card_keys)),
            },
            "strategy_brief": brief,
            "rationale": {
                "type": "array",
                "items": rationale,
                "maxItems": 4,
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


def finalize_strategy_decision_v4(output: dict[str, Any]) -> dict[str, Any]:
    """Move nested peer selections into the persisted downstream handoff shape."""

    finalized = copy.deepcopy(output)
    existing = [
        copy.deepcopy(item)
        for item in _list(finalized.get("target_peer_context"))
        if isinstance(item, dict)
    ]
    derived: list[dict[str, Any]] = []
    for item in _list(finalized.get("basis_cards")):
        if not isinstance(item, dict):
            continue
        peer_context = item.pop("target_peer_context", None)
        if isinstance(peer_context, dict):
            derived.append(
                {
                    "basis_card_key": item.get("card_key"),
                    **copy.deepcopy(peer_context),
                }
            )
    if existing and derived:
        raise ValueError(
            "Strategy decision cannot contain both nested and top-level peer contexts."
        )
    finalized["target_peer_context"] = derived or existing
    return finalized


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
    decision_card_keys = set(_decision_card_keys(cards))
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
        if card_key not in decision_card_keys:
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
    if len(selected) > MAX_SELECTED_BASIS_CARDS:
        raise ValueError(
            "Strategy decision exceeds the evidence-card budget: "
            f"selected={len(selected)}, max={MAX_SELECTED_BASIS_CARDS}"
        )

    peer_contexts = _list(output.get("target_peer_context"))
    if len(peer_contexts) > MAX_TARGET_PEER_CONTEXTS:
        raise ValueError(
            "Strategy decision exceeds the target peer-context budget: "
            f"selected={len(peer_contexts)}, max={MAX_TARGET_PEER_CONTEXTS}"
        )
    structured_peer_cards = set(_structured_peer_card_keys(cards))
    seen_peer_cards: set[str] = set()
    for index, item in enumerate(peer_contexts):
        if not isinstance(item, dict):
            raise ValueError(f"target_peer_context[{index}] must be an object.")
        card_key = str(item.get("basis_card_key") or "")
        if card_key not in structured_peer_cards:
            raise ValueError(
                f"target_peer_context[{index}] uses a non-comparable peer card: {card_key}"
            )
        if card_key not in selected:
            raise ValueError(
                f"target_peer_context[{index}] references an unselected card: {card_key}"
            )
        if card_key in seen_peer_cards:
            raise ValueError(f"target_peer_context contains a duplicate card: {card_key}")
        seen_peer_cards.add(card_key)
        metric_keys = _dedupe_strings(item.get("metric_keys") or [])
        if not metric_keys or len(metric_keys) > MAX_TARGET_PEER_METRICS:
            raise ValueError(
                f"target_peer_context[{index}].metric_keys must contain 1-"
                f"{MAX_TARGET_PEER_METRICS} metrics."
            )
        raw_metric_keys = [str(value) for value in _list(item.get("metric_keys"))]
        if len(metric_keys) != len(raw_metric_keys):
            raise ValueError(
                f"target_peer_context[{index}].metric_keys contains duplicates."
            )
        pairs = [
            pair
            for pair in _list(_dict(cards[card_key].get("primary_observation")).get("pairs"))
            if isinstance(pair, dict)
        ]
        comparable_metric_keys = {
            str(pair.get("metric_key") or "")
            for pair in pairs
            if pair.get("comparability") == "comparable"
        }
        unavailable = sorted(set(metric_keys) - comparable_metric_keys)
        if unavailable:
            raise ValueError(
                f"target_peer_context[{index}] uses unavailable or incomparable metric(s): "
                f"{unavailable}"
            )
        if item.get("decision_role") not in {"reinforce", "modify", "context"}:
            raise ValueError(f"target_peer_context[{index}].decision_role is invalid.")
        if not str(item.get("target_implication") or "").strip():
            raise ValueError(
                f"target_peer_context[{index}].target_implication is required."
            )
    selected_peer_cards = selected.intersection(structured_peer_cards)
    if selected_peer_cards != seen_peer_cards:
        raise ValueError(
            "Every selected structured peer card must have exactly one "
            "target_peer_context entry."
        )

    linked_count = 0
    for collection_name in ("rationale", "key_risks"):
        for index, item in enumerate(_list(output.get(collection_name))):
            if not isinstance(item, dict):
                raise ValueError(f"{collection_name}[{index}] must be an object.")
            if collection_name == "key_risks" and not str(
                item.get("risk_title") or ""
            ).strip():
                raise ValueError(f"key_risks[{index}].risk_title is required.")
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
        key: {"stance": value.get("stance")}
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
            "overall_assessment": news_only.get("summary"),
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


def _decision_card_keys(cards: dict[str, Any]) -> list[str]:
    """Return cards that may be exposed as final Strategy evidence."""

    return sorted(set(cards) - _INTERNAL_COMPARISON_CARD_KEYS)


def _structured_peer_card_keys(cards: dict[str, Any]) -> list[str]:
    """Return pairwise peer cards whose metric rows can be checked directly."""

    return sorted(
        card_key
        for card_key, card in cards.items()
        if str(_dict(card).get("domain") or "") == "peer"
        and card_key not in _INTERNAL_COMPARISON_CARD_KEYS
        and isinstance(_dict(_dict(card).get("primary_observation")).get("pairs"), list)
    )


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
        "target_peer_context": [
            {"target_implication": item.get("target_implication")}
            for item in _list(output.get("target_peer_context"))
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
