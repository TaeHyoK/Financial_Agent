"""Build the compact, evidence-layered input consumed by the Writer Agent."""

from __future__ import annotations

import copy
from typing import Any

from shared.evidence_cards import (
    assert_no_internal_references_in_reader_text,
    assert_no_opaque_ids,
    card_content_sha256,
)


LEGACY_EDITORIAL_PACKET_VERSION = "writer_editorial_packet_v2"
EDITORIAL_PACKET_VERSION = "writer_editorial_packet_v3"
LEGACY_WRITER_PROVENANCE_VERSION = "writer_packet_provenance_v2"
WRITER_PROVENANCE_VERSION = "writer_packet_provenance_v3"
FINAL_RECOMMENDATIONS = {"Buy", "Hold", "Sell"}
LEGACY_LABEL_FREE_STRATEGY_VERSION = "strategy_decision_output_v4"
STRATEGY_DECISION_VERSION = "strategy_decision_output_v5"
WRITER_COMPONENTS = (
    "investment_call_thesis",
    "business_market_context",
    "key_evidence_table",
    "catalysts_execution",
    "risk_monitoring_matrix",
    "data_limits",
)

_KRW_UNIT_MULTIPLIERS = {
    "원": 1,
    "KRW": 1,
    "천원": 1_000,
    "백만원": 1_000_000,
    "억원": 100_000_000,
    "100m_KRW": 100_000_000,
}
_FINANCIAL_AMOUNT_CARD_KEYS = {
    "financial.same_period_trend",
    "financial.cash_flow",
    "financial.balance_sheet",
}


def build_writer_editorial_packet(
    *,
    strategy_packet: dict[str, Any],
    strategy_decision: dict[str, Any],
    strategy_provenance: dict[str, Any],
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Build the bounded Writer input from the two-tier Strategy evidence plan."""

    if strategy_decision.get("decision_version") != STRATEGY_DECISION_VERSION:
        raise ValueError(
            f"Writer requires a {STRATEGY_DECISION_VERSION} Strategy decision."
        )
    source_cards = _dict(strategy_packet.get("cards"))
    brief = _require_dict(strategy_decision.get("strategy_brief"), "strategy_brief")
    plan = _require_dict(strategy_decision.get("evidence_plan"), "evidence_plan")
    decision_rows = [
        copy.deepcopy(item)
        for item in _list(plan.get("decision_basis_cards"))
        if isinstance(item, dict) and str(item.get("card_key") or "") in source_cards
    ]
    context_rows = [
        copy.deepcopy(item)
        for item in _list(plan.get("report_context_cards"))
        if isinstance(item, dict) and str(item.get("card_key") or "") in source_cards
    ]
    decision_keys = _dedupe(item.get("card_key") for item in decision_rows)
    context_keys = _dedupe(item.get("card_key") for item in context_rows)
    if not decision_keys:
        raise ValueError("Writer requires at least one Strategy v5 decision-basis card.")
    overlap = sorted(set(decision_keys) & set(context_keys))
    if overlap:
        raise ValueError(f"Writer decision and report-context cards overlap: {overlap}")

    decision_by_key = {str(item["card_key"]): item for item in decision_rows}
    context_by_key = {str(item["card_key"]): item for item in context_rows}
    selected_keys = _dedupe([*decision_keys, *context_keys])
    limitations = _select_limitations(
        _list(strategy_packet.get("limitation_requirements")),
        selected_keys=selected_keys,
        source_cards=source_cards,
    )
    limitation_keys = _dedupe(
        card_key
        for item in limitations
        for card_key in _text_list(item.get("basis_card_keys"))
        if card_key in source_cards
    )
    included_keys = _dedupe([*selected_keys, *limitation_keys])

    cards: dict[str, dict[str, Any]] = {}
    for card_key in included_keys:
        if card_key in decision_by_key:
            cards[card_key] = _writer_card(
                source_cards[card_key],
                decision_by_key[card_key],
                evidence_tier="decision_basis",
            )
        elif card_key in context_by_key:
            cards[card_key] = _writer_card(
                source_cards[card_key],
                context_by_key[card_key],
                evidence_tier="report_context",
            )
        else:
            cards[card_key] = _writer_card(
                source_cards[card_key],
                {
                    "report_implication": "자료의 기준일과 적용 범위를 구분한다.",
                    "purpose": "limitation_context",
                },
                evidence_tier="limitation_context",
            )

    if strategy_decision.get("schema_revision") == "12m_v3":
        for card in cards.values():
            card.pop("strategy_role", None)
            card.pop("decision_use", None)

    def linked_keys(field: str) -> list[str]:
        return [
            key
            for key in _text_list(_dict(brief.get(field)).get("card_keys"))
            if key in cards
        ]

    insight_rows = [
        copy.deepcopy(item)
        for item in _list(strategy_decision.get("report_insights"))
        if isinstance(item, dict)
    ]
    risk_rows = [
        copy.deepcopy(item)
        for item in _list(strategy_decision.get("key_risks"))
        if isinstance(item, dict)
    ]
    risk_keys = _dedupe(
        key
        for item in risk_rows
        for key in _text_list(item.get("card_keys"))
        if key in cards
    )
    thesis_keys = _dedupe([*linked_keys("thesis"), *linked_keys("decision_rationale")])
    data_limit_keys = _dedupe([*linked_keys("decision_limitation"), *limitation_keys])
    required_by_component = {
        "investment_call_thesis": thesis_keys or decision_keys,
        "business_market_context": [],
        "key_evidence_table": decision_keys,
        "catalysts_execution": [],
        "risk_monitoring_matrix": risk_keys,
        "data_limits": data_limit_keys,
    }
    # Source preservation is not a requirement to repeat every citation in prose.
    # Core rationale, table rows and explicit scope notes remain mandatory.
    available_by_component = copy.deepcopy(required_by_component)
    for component in ("investment_call_thesis", "business_market_context", "catalysts_execution"):
        available_by_component[component] = list(selected_keys)

    risk_factors = []
    for item in risk_rows:
        keys = [key for key in _text_list(item.get("card_keys")) if key in cards]
        if not keys:
            continue
        risk_factors.append(
            {
                "category": _risk_category_from_card(cards[keys[0]]),
                "display_title": str(item.get("risk_title") or "").strip(),
                "basis_card_keys": keys,
                "risk_summary": str(item.get("risk") or "").strip(),
                "reader_summary": str(item.get("risk") or "").strip(),
                "current_implication": str(item.get("current_implication") or "").strip(),
            }
        )

    target = _dict(strategy_packet.get("target_company"))
    peer_contexts = []
    for row in decision_rows:
        peer_context = row.get("target_peer_context")
        if not isinstance(peer_context, dict):
            continue
        card_key = str(row.get("card_key") or "")
        card = _dict(cards.get(card_key))
        peer_contexts.append(
            {
                "basis_card_key": card_key,
                "metric_keys": _text_list(peer_context.get("metric_keys")),
                "decision_role": peer_context.get("decision_role"),
                "target_implication": peer_context.get("target_implication"),
                "target_company": _dict(card.get("comparison_entities")).get(
                    "target_company"
                ) or target.get("company_name"),
                "peer_companies": copy.deepcopy(
                    _dict(card.get("comparison_entities")).get("peer_companies") or []
                ),
                "reader_observation": copy.deepcopy(card.get("reader_observation") or {}),
            }
        )

    packet = {
        "packet_version": EDITORIAL_PACKET_VERSION,
        "strategy_contract_version": STRATEGY_DECISION_VERSION,
        "target": {
            "company_name": target.get("company_name"),
            "run_key": target.get("run_key"),
            "ticker": target.get("ticker"),
            "selected_date": target.get("as_of_date") or target.get("selected_date"),
        },
        "schema_revision": strategy_decision.get("schema_revision"),
        "decision": {
            "opinion": brief["recommendation"],
            "headline": brief["headline"],
            "judgment": _dict(brief.get("thesis")).get("text"),
            "earnings_review": _dict(
                brief.get("earnings_review")
            ).get("text"),
            "outlook": _dict(brief.get("outlook")).get("text"),
            "investment_horizon": brief.get("horizon"),
            "data_coverage": brief.get("evidence_sufficiency"),
            "decision_confidence": brief.get("decision_confidence"),
        },
        "recommendation_bridge": {
            "thesis": _dict(brief.get("thesis")).get("text"),
            "thesis_card_keys": linked_keys("thesis"),
            "decision_rationale": _dict(brief.get("decision_rationale")).get("text"),
            "decision_rationale_card_keys": linked_keys("decision_rationale"),
            "earnings_review": _dict(
                brief.get("earnings_review")
            ).get("text"),
            "earnings_review_card_keys": linked_keys("earnings_review"),
            "outlook": _dict(brief.get("outlook")).get("text"),
            "outlook_card_keys": linked_keys("outlook"),
            "price_context": _dict(brief.get("price_assessment")).get("text"),
            "price_context_card_keys": linked_keys("price_assessment"),
            "counterview": _dict(brief.get("counterview")).get("text"),
            "counterview_card_keys": linked_keys("counterview"),
            "residual_uncertainty": _dict(brief.get("decision_limitation")).get("text"),
            "residual_uncertainty_card_keys": linked_keys("decision_limitation"),
            "decision_confidence": brief.get("decision_confidence"),
        },
        "evidence_plan": {
            "decision_basis_card_keys": decision_keys,
            "report_context_card_keys": context_keys,
            "coverage_assessment": copy.deepcopy(plan.get("coverage_assessment") or {}),
        },
        "report_insights": insight_rows,
        "required_card_keys_by_component": required_by_component,
        "available_card_keys_by_component": available_by_component,
        "cards": cards,
        "peer_findings": [],
        "target_peer_context": peer_contexts,
        "risk_factors": risk_factors,
        "general_limitations": copy.deepcopy(_list(strategy_packet.get("reader_limitations"))),
        "required_limitations": limitations,
    }
    provenance = _writer_provenance_for_cards(
        cards=cards,
        source_cards=source_cards,
        strategy_provenance=strategy_provenance,
        target_run_key=target.get("run_key"),
        provenance_version=WRITER_PROVENANCE_VERSION,
    )
    validate_writer_editorial_packet(
        packet,
        provenance=provenance,
        strategy_packet=strategy_packet,
    )
    return packet, provenance


def _select_limitations(
    requirements: list[Any],
    *,
    selected_keys: list[str],
    source_cards: dict[str, Any],
) -> list[dict[str, Any]]:
    """Route only limitations that apply to evidence selected by the Strategy decision."""

    selected = set(selected_keys)
    selected_domains = {
        str(_dict(source_cards.get(card_key)).get("domain") or "")
        for card_key in selected
    }
    result = []
    for raw in requirements:
        if not isinstance(raw, dict):
            continue
        category = str(raw.get("category") or "")
        basis = [key for key in _text_list(raw.get("basis_card_keys")) if key in source_cards]
        include = False
        selected_basis = [key for key in basis if key in selected]
        if category == "filing_lag":
            # 기준일 현재 이용 가능한 최신 정기공시를 사용했다는 사실은 자료
            # 기준에 표시하되, 그 자체를 투자 판단의 실질적 한계로 반복하지 않는다.
            include = False
        elif category == "valuation_input_date_mix":
            include = "valuation" in selected_domains
        elif category == "news_financial_link":
            # Legacy packets may still contain this automatic requirement.
            # Unquantified impact alone does not require an analytical caveat.
            include = False
        elif category == "single_peer_scope":
            # 비교기업 수는 실험 설계의 범위이며 공개 보고서의 판단 한계로 쓰지 않는다.
            include = False
        else:
            include = bool(selected_basis)
            basis = selected_basis
        if not include:
            continue
        facts = copy.deepcopy(_dict(raw.get("facts")))
        result.append(
            {
                "category": category,
                "basis_card_keys": basis,
                "facts": facts,
            }
        )
    return result


def _writer_card(
    source: dict[str, Any],
    plan_row: dict[str, Any],
    *,
    evidence_tier: str,
) -> dict[str, Any]:
    peer_context = plan_row.get("target_peer_context")
    source_for_reader = copy.deepcopy(source)
    primary_observation = copy.deepcopy(source.get("primary_observation") or {})
    if isinstance(peer_context, dict):
        selected_metrics = set(_text_list(peer_context.get("metric_keys")))
        pairs = [
            copy.deepcopy(pair)
            for pair in _list(primary_observation.get("pairs"))
            if isinstance(pair, dict)
            and str(pair.get("metric_key") or "") in selected_metrics
            and pair.get("comparability") == "comparable"
        ]
        if {str(pair.get("metric_key") or "") for pair in pairs} != selected_metrics:
            raise ValueError(
                f"Writer peer context contains unavailable metric(s): {sorted(selected_metrics)}"
            )
        primary_observation["pairs"] = pairs
        source_for_reader["primary_observation"] = primary_observation
    reader_observation = _reader_observation(source_for_reader)
    if str(source.get("domain") or "") == "news" and reader_observation:
        primary_observation = copy.deepcopy(reader_observation)
    relation = str(plan_row.get("relation_to_decision") or "")
    interpretation = (
        _dict(peer_context).get("target_implication")
        if isinstance(peer_context, dict)
        else plan_row.get("investment_implication")
        or plan_row.get("report_implication")
    )
    card = {
        "card_key": source.get("card_key"),
        "axis": source.get("card_type"),
        "domain": source.get("domain"),
        "label": source.get("label"),
        "primary_observation": primary_observation,
        "strategy_interpretation": interpretation,
        "strategy_role": {
            "supports": "supports_decision",
            "opposes": "opposes_decision",
            "limits": "limits_confidence",
        }.get(relation, "context"),
        "evidence_tier": evidence_tier,
        "context_purpose": plan_row.get("purpose"),
        "importance": plan_row.get("importance"),
        "evidence_family": source.get("evidence_family"),
        "observation_basis": source.get("observation_basis"),
        "comparison_scope": source.get("comparison_scope"),
        "decision_use": source.get("decision_use"),
    }
    for key in ("comparison_label", "comparison_entities", "reader_limitations"):
        if key in source:
            card[key] = copy.deepcopy(source[key])
    if source.get("domain") == "news":
        # Reader display omits stock caveats, but the LLM still needs the
        # original status/date/origin facts to preserve epistemic boundaries.
        raw_observation = _dict(source.get("primary_observation"))
        card["source_metadata"] = {
            key: copy.deepcopy(raw_observation[key])
            for key in ("financial_link_status", "event_status", "company_specificity",
                        "materiality_status", "evidence_origin", "source_periods",
                        "date_precision", "event_date", "source_scope", "coverage")
            if key in raw_observation
        }
    if reader_observation:
        card["reader_observation"] = reader_observation
    if source.get("secondary_context"):
        card["secondary_context"] = copy.deepcopy(source["secondary_context"])
    if isinstance(peer_context, dict):
        card["target_peer_decision_role"] = peer_context.get("decision_role")
        card["target_peer_metric_keys"] = _text_list(peer_context.get("metric_keys"))
    return card


def _risk_category_from_card(card: dict[str, Any]) -> str:
    return {
        "financial": "financial",
        "market": "market",
        "valuation": "valuation",
        "news": "business",
        "peer": "business",
    }.get(str(card.get("domain") or ""), "business")


def _writer_provenance_for_cards(
    *,
    cards: dict[str, Any],
    source_cards: dict[str, Any],
    strategy_provenance: dict[str, Any],
    target_run_key: Any,
    provenance_version: str = LEGACY_WRITER_PROVENANCE_VERSION,
) -> dict[str, Any]:
    source_provenance = _dict(strategy_provenance.get("cards"))
    provenance_cards: dict[str, Any] = {}
    for card_key, card in cards.items():
        source_entry = _dict(source_provenance.get(card_key))
        source_hash = str(source_entry.get("strategy_card_sha256") or "")
        actual_source_hash = card_content_sha256(source_cards[card_key])
        if source_hash != actual_source_hash:
            raise ValueError(f"Strategy provenance hash mismatch before Writer handoff: {card_key}")
        provenance_cards[card_key] = {
            "source_strategy_card_sha256": source_hash,
            "writer_editorial_card_sha256": card_content_sha256(card),
            "source_evidence_ids": copy.deepcopy(_list(source_entry.get("source_evidence_ids"))),
            "source_paths": copy.deepcopy(_list(source_entry.get("source_paths"))),
            "source_files": copy.deepcopy(_list(source_entry.get("source_files"))),
        }
    return {
        "provenance_version": provenance_version,
        "target_run_key": target_run_key,
        "cards": provenance_cards,
    }


def validate_writer_editorial_packet(
    packet: dict[str, Any],
    *,
    provenance: dict[str, Any] | None = None,
    strategy_packet: dict[str, Any] | None = None,
) -> None:
    """Validate the Writer editorial packet without judging free-form Korean prose."""

    packet_version = str(_dict(packet).get("packet_version") or "")
    if packet_version not in {LEGACY_EDITORIAL_PACKET_VERSION, EDITORIAL_PACKET_VERSION}:
        raise ValueError(
            "writer editorial packet version must be "
            f"{LEGACY_EDITORIAL_PACKET_VERSION} or {EDITORIAL_PACKET_VERSION}."
        )
    target = _require_dict(packet.get("target"), "target")
    for key in ("company_name", "run_key", "selected_date"):
        if not str(target.get(key) or "").strip():
            raise ValueError(f"writer editorial target.{key} is required.")
    decision = _require_dict(packet.get("decision"), "decision")
    strategy_version = packet.get("strategy_contract_version")
    label_free = strategy_version in {LEGACY_LABEL_FREE_STRATEGY_VERSION, STRATEGY_DECISION_VERSION}
    is_strategy_decision = strategy_version == STRATEGY_DECISION_VERSION
    if label_free:
        if not str(decision.get("judgment") or "").strip():
            raise ValueError("writer editorial decision.judgment is required for label-free Strategy.")
        if packet.get("schema_revision") in {"12m_v1", "12m_v2", "12m_v3"}:
            if decision.get("opinion") not in FINAL_RECOMMENDATIONS:
                raise ValueError("Annual Writer decision requires Buy/Hold/Sell.")
        elif "opinion" in decision:
            raise ValueError("legacy label-free Writer decision cannot contain opinion.")
    elif decision.get("opinion") not in FINAL_RECOMMENDATIONS:
        raise ValueError("writer editorial decision.opinion must be Buy/Hold/Sell.")
    if not str(decision.get("investment_horizon") or "").strip():
        raise ValueError("writer editorial decision.investment_horizon is required.")
    if decision.get("data_coverage") not in {"high", "medium", "low"}:
        raise ValueError("writer editorial decision.data_coverage is invalid.")
    if decision.get("decision_confidence") not in {"high", "medium", "low"}:
        raise ValueError("writer editorial decision.decision_confidence is invalid.")
    bridge = _require_dict(packet.get("recommendation_bridge"), "recommendation_bridge")
    if packet.get("schema_revision") in {"12m_v2", "12m_v3"}:
        if not str(bridge.get("decision_rationale") or "").strip():
            raise ValueError("Writer requires the Strategy decision rationale.")
        rationale_keys = _text_list(bridge.get("decision_rationale_card_keys"))
        thesis_keys = _text_list(_dict(packet.get("required_card_keys_by_component")).get("investment_call_thesis"))
        if not rationale_keys or not set(rationale_keys) <= set(thesis_keys):
            raise ValueError("Decision rationale references must reach the thesis component.")
    if bridge.get("decision_confidence") != decision.get("decision_confidence"):
        raise ValueError("Writer recommendation bridge confidence mismatch.")
    cards = _require_dict(packet.get("cards"), "cards")
    required = _require_dict(packet.get("required_card_keys_by_component"), "required_card_keys_by_component")
    if set(required) != set(WRITER_COMPONENTS):
        raise ValueError("writer editorial packet has invalid component routing.")
    for component, card_keys in required.items():
        if not isinstance(card_keys, list):
            raise ValueError(f"required_card_keys_by_component.{component} must be a list.")
        unknown = sorted(set(_text_list(card_keys)) - set(cards))
        if unknown:
            raise ValueError(f"Unknown Writer card key(s) for {component}: {unknown}")
    if "available_card_keys_by_component" in packet:
        available = _require_dict(packet.get("available_card_keys_by_component"), "available_card_keys_by_component")
        if set(available) != set(required):
            raise ValueError("Writer available component routing is incomplete.")
        for component, keys in available.items():
            if not isinstance(keys, list) or not set(keys) <= set(cards):
                raise ValueError(f"Invalid available Writer cards for {component}")
            if not set(required[component]) <= set(keys):
                raise ValueError(f"Required Writer cards are not available for {component}")
    for card_key, card in cards.items():
        if not isinstance(card, dict) or card.get("card_key") != card_key:
            raise ValueError(f"Writer card map key mismatch: {card_key}")
        if not isinstance(card.get("primary_observation"), dict):
            raise ValueError(f"Writer card observation is required: {card_key}")
        if not str(card.get("strategy_interpretation") or "").strip():
            raise ValueError(f"Writer card Strategy interpretation is required: {card_key}")
        if is_strategy_decision:
            if packet.get("schema_revision") != "12m_v3" and card.get("strategy_role") not in {
                "supports_decision",
                "opposes_decision",
                "limits_confidence",
                "context",
            }:
                raise ValueError(f"Writer card Strategy role is invalid: {card_key}")
            if card.get("evidence_tier") not in {
                "decision_basis",
                "report_context",
                "limitation_context",
            }:
                raise ValueError(f"Writer card evidence tier is invalid: {card_key}")
        elif label_free:
            if card.get("strategy_role") not in {"primary", "counter", "monitoring", "context"}:
                raise ValueError(f"Writer card Strategy role is invalid: {card_key}")
        elif card.get("investment_effect") not in {"positive", "negative", "mixed", "neutral", "reference"}:
            raise ValueError(f"Writer card investment effect is invalid: {card_key}")
        if not str(card.get("evidence_family") or "").strip():
            raise ValueError(f"Writer card evidence_family is required: {card_key}")
    for index, risk in enumerate(_list(packet.get("risk_factors"))):
        if not isinstance(risk, dict):
            raise ValueError(f"risk_factors[{index}] must be an object.")
        if label_free and not str(risk.get("display_title") or "").strip():
            raise ValueError(
                f"risk_factors[{index}].display_title is required for label-free Strategy."
            )
        unknown = sorted(set(_text_list(risk.get("basis_card_keys"))) - set(cards))
        if unknown:
            raise ValueError(f"Risk factor references cards omitted from Writer packet: {unknown}")
    peer_contexts = _list(packet.get("target_peer_context"))
    seen_peer_cards: set[str] = set()
    for index, item in enumerate(peer_contexts):
        if not isinstance(item, dict):
            raise ValueError(f"target_peer_context[{index}] must be an object.")
        card_key = str(item.get("basis_card_key") or "")
        card = _dict(cards.get(card_key))
        if not card or card.get("domain") != "peer":
            raise ValueError(
                f"target_peer_context[{index}] references an omitted or non-peer card."
            )
        if card_key in seen_peer_cards:
            raise ValueError("target_peer_context contains duplicate cards.")
        seen_peer_cards.add(card_key)
        metric_keys = _text_list(item.get("metric_keys"))
        if not metric_keys or metric_keys != _text_list(card.get("target_peer_metric_keys")):
            raise ValueError(
                f"target_peer_context[{index}] metric selection does not match its card."
            )
        if item.get("decision_role") != card.get("target_peer_decision_role"):
            raise ValueError(
                f"target_peer_context[{index}] decision role does not match its card."
            )
        implication = str(item.get("target_implication") or "").strip()
        if not implication or implication != str(card.get("strategy_interpretation") or "").strip():
            raise ValueError(
                f"target_peer_context[{index}] implication does not match Strategy meaning."
            )
    if is_strategy_decision:
        selected_peer_cards = {
            card_key
            for card_key, card in cards.items()
            if isinstance(card, dict)
            and card.get("domain") == "peer"
            and card.get("evidence_tier") == "decision_basis"
            and card.get("target_peer_metric_keys")
        }
        if selected_peer_cards != seen_peer_cards:
            raise ValueError(
                "Every structured Strategy v5 peer basis requires one target_peer_context entry."
            )
    elif label_free:
        selected_peer_cards = {
            card_key
            for card_key, card in cards.items()
            if isinstance(card, dict) and card.get("domain") == "peer"
        }
        if selected_peer_cards != seen_peer_cards:
            raise ValueError(
                "Every label-free Writer peer card requires one target_peer_context entry."
            )
    limitation_categories: set[str] = set()
    for index, limitation in enumerate(_list(packet.get("required_limitations"))):
        if not isinstance(limitation, dict) or not str(limitation.get("category") or "").strip():
            raise ValueError(f"required_limitations[{index}] is invalid.")
        category = str(limitation["category"])
        if category in limitation_categories:
            raise ValueError(f"Duplicate Writer limitation category: {category}")
        limitation_categories.add(category)
        unknown = sorted(set(_text_list(limitation.get("basis_card_keys"))) - set(cards))
        if unknown:
            raise ValueError(f"Writer limitation references omitted cards: {unknown}")
    assert_no_internal_references_in_reader_text(
        _writer_reader_text(packet),
        card_keys={
            *cards,
            *(
                card_key
                for key, values in bridge.items()
                if str(key).endswith("_card_keys")
                for card_key in _text_list(values)
            ),
        },
        location=f"{packet_version}.reader_text",
    )
    assert_no_opaque_ids(packet, location=packet_version)

    if provenance is None:
        return
    provenance_cards = _require_dict(provenance.get("cards"), "provenance.cards")
    if set(provenance_cards) != set(cards):
        raise ValueError("Writer provenance card coverage mismatch.")
    source_cards = _dict(_dict(strategy_packet).get("cards")) if strategy_packet else {}
    for card_key, card in cards.items():
        entry = _require_dict(provenance_cards.get(card_key), f"provenance.cards.{card_key}")
        if entry.get("writer_editorial_card_sha256") != card_content_sha256(card):
            raise ValueError(f"Writer editorial card hash mismatch: {card_key}")
        if strategy_packet:
            source_card = _dict(source_cards.get(card_key))
            if not source_card or entry.get("source_strategy_card_sha256") != card_content_sha256(source_card):
                raise ValueError(f"Writer source Strategy card hash mismatch: {card_key}")


def _writer_reader_text(packet: dict[str, Any]) -> dict[str, Any]:
    """Select Strategy prose that Writer may render without changing its meaning."""

    bridge = _dict(packet.get("recommendation_bridge"))
    return {
        "headline": _dict(packet.get("decision")).get("headline"),
        "recommendation_bridge": {
            key: bridge.get(key)
            for key in (
                "thesis",
                "decision_rationale",
                "existing_position_response",
                "new_entry_response",
                "earnings_review",
                "outlook",
                "price_context",
                "counterview",
                "current_price_rationale",
                "forward_support",
                "valuation_counterweight",
                "residual_uncertainty",
            )
        },
        "cards": [
            {"strategy_interpretation": card.get("strategy_interpretation")}
            for card in _dict(packet.get("cards")).values()
            if isinstance(card, dict)
        ],
        "peer_findings": [
            {"finding": item.get("finding")}
            for item in _list(packet.get("peer_findings"))
            if isinstance(item, dict)
        ],
        "target_peer_context": [
            {"target_implication": item.get("target_implication")}
            for item in _list(packet.get("target_peer_context"))
            if isinstance(item, dict)
        ],
        "risk_factors": [
            {
                key: item.get(key)
                for key in (
                    "display_title",
                    "risk_summary",
                    "reader_summary",
                    "monitoring_point",
                    "current_implication",
                )
            }
            for item in _list(packet.get("risk_factors"))
            if isinstance(item, dict)
        ],
    }


def _reader_observation(
    source: dict[str, Any],
    *,
    financial_source_unit: str = "원",
) -> dict[str, Any]:
    card_key = str(source.get("card_key") or "")
    observation = _dict(source.get("primary_observation"))
    if source.get("card_type") == "context_source" and source.get("domain") == "news":
        return {
            "요약 기간": " · ".join(observation.get("source_periods") or []),
            "자료 내용": observation.get("event_summary") or observation.get("text"),
            "자료 유형": "월별 뉴스 요약" if observation.get("evidence_origin") == "model_summarized" else "뉴스 자료",
        }
    if card_key == "peer.agent_analysis":
        entities = _dict(source.get("comparison_entities"))
        return {
            "대상 기업": entities.get("target_company"),
            "비교 기업": entities.get("peer_companies") or [],
            "종합 비교": observation.get("comparison_brief"),
            "비교 내용": [
                {"확인된 차이": point.get("finding"), "대상기업에 대한 의미": point.get("target_implication")}
                for point in observation.get("comparison_points") or [] if isinstance(point, dict)
            ],
            "자료 유형": "하위 분석에 근거한 비교 해석",
        }
    if str(source.get("domain") or "") == "news" or card_key.startswith("news."):
        if observation.get("evidence_origin") == "model_summarized":
            return {
                "요약 기간": " · ".join(observation.get("source_periods") or []),
                "기간 뉴스 흐름": observation.get("event_summary") or source.get("label"),
                "자료 유형": "월별 뉴스 요약",
            }
        coverage = _dict(observation.get("coverage"))
        article_count = coverage.get("deduplicated_article_count") or coverage.get("article_count")
        publisher_count = coverage.get("unique_publisher_count")
        coverage_parts = []
        if article_count is not None:
            coverage_parts.append(f"중복 제거 후 {article_count}건")
        if publisher_count is not None:
            coverage_parts.append(f"{publisher_count}개 매체")
        return {
            "보도일": observation.get("event_date"),
            "사건 요약": observation.get("event_summary") or source.get("label"),
            "보도 범위": " · ".join(coverage_parts),
        }
    if card_key == "financial.same_period_trend":
        result = {
            "비교 기준": _period_pair_display(observation),
            "당기": _financial_value_display(
                _dict(observation.get("current_values")),
                source_unit=financial_source_unit,
            ),
            "전년 동기": _financial_value_display(
                _dict(observation.get("previous_values")),
                source_unit=financial_source_unit,
            ),
        }
        change_display = _financial_change_display(_dict(observation.get("change_rates")))
        if change_display:
            result["전년 동기 대비"] = change_display
        margin_display = _financial_margin_display(
            _dict(observation.get("current_margins")),
            _dict(observation.get("previous_margins")),
            _dict(observation.get("margin_changes")),
        )
        if margin_display:
            result["수익성"] = margin_display
        return result
    if card_key == "financial.annual_trend":
        return {
            _period_display(_dict(item.get("period"))): {
                "영업이익률": _ratio_percent(_dict(item.get("values")).get("operating_margin")),
                "자본총계": _krw_100m(_dict(item.get("values")).get("total_equity"), source_unit=financial_source_unit),
                "부채비율": _ratio_percent(_dict(item.get("values")).get("debt_ratio")),
                "매출": _krw_100m(
                    _dict(item.get("values")).get("revenue"),
                    source_unit=financial_source_unit,
                ),
                "영업이익": _krw_100m(
                    _dict(item.get("values")).get("operating_profit"),
                    source_unit=financial_source_unit,
                ),
                "순이익": _krw_100m(
                    _dict(item.get("values")).get("net_income"),
                    source_unit=financial_source_unit,
                ),
                "영업현금흐름": _krw_100m(
                    _dict(item.get("values")).get("operating_cash_flow"),
                    source_unit=financial_source_unit,
                ),
            }
            for item in _list(observation.get("annual_history"))
            if isinstance(item, dict)
        }
    if card_key == "financial.cash_flow":
        result = {
            "비교 기준": _period_pair_display(observation),
            "당기 영업현금흐름": _krw_100m(
                observation.get("current_operating_cash_flow"),
                source_unit=financial_source_unit,
            ),
            "전년 동기 영업현금흐름": _krw_100m(
                observation.get("previous_operating_cash_flow"),
                source_unit=financial_source_unit,
            ),
        }
        if observation.get("operating_cash_flow_change_rate") is not None:
            result["전년 동기 대비"] = _signed_percent(
                observation.get("operating_cash_flow_change_rate")
            )
        current_margin = observation.get("current_operating_cash_flow_margin")
        previous_margin = observation.get("previous_operating_cash_flow_margin")
        if current_margin is not None:
            margin_text = _ratio_percent(current_margin)
            if previous_margin is not None:
                margin_text += f" (전년 동기 {_ratio_percent(previous_margin)})"
            result["영업현금흐름률"] = margin_text
        return result
    if card_key == "financial.balance_sheet":
        values = _dict(observation.get("values"))
        return {
            "기준일": observation.get("as_of_date"),
            "총자산": _krw_100m(values.get("total_assets"), source_unit=financial_source_unit),
            "총부채": _krw_100m(values.get("total_liabilities"), source_unit=financial_source_unit),
            "총자본": _krw_100m(values.get("total_equity"), source_unit=financial_source_unit),
            "유동비율": _ratio_percent(values.get("current_ratio")),
            "현금비율": _ratio_percent(values.get("cash_ratio")),
            "부채비율": _ratio_percent(values.get("debt_to_equity")),
        }
    if card_key == "financial.product_breakdown":
        unit = str(observation.get("unit") or "")
        return {
            "공시 기준": _period_display(_dict(observation.get("period"))),
            "공시 단위": unit,
            "제품": [
                {
                    "제품명": item.get("name"),
                    "매출액": " ".join(
                        value
                        for value in (str(item.get("revenue_disclosed") or "").strip(), unit)
                        if value
                    ),
                    "비중": item.get("revenue_share_disclosed"),
                }
                for item in _list(observation.get("items"))
                if isinstance(item, dict)
            ],
            "범위": "주요 제품·서비스 공시표 기준",
        }
    if card_key == "market.absolute_trend":
        metrics = _dict(observation.get("metrics"))
        return {
            "기준일": observation.get("as_of_date"),
            "종가": _price_display(metrics.get("stock_close")),
            **{f"{m}개월 수익률": _ratio_percent(metrics[f"stock_return_{m}m"]) for m in (1, 3, 6, 12) if f"stock_return_{m}m" in metrics},
            **{f"{d}일 이동평균 대비": _ratio_percent(metrics[f"stock_close_to_ma{d}"]) for d in (120, 200) if f"stock_close_to_ma{d}" in metrics},
            **{f"{d}일 이동평균의 최근 20거래일 변화": _ratio_percent(metrics[f"stock_ma{d}_change_20d"]) for d in (120, 200) if f"stock_ma{d}_change_20d" in metrics},
            "5거래일 수익률": _ratio_percent(metrics.get("stock_return_5d")),
            "20거래일 수익률": _ratio_percent(metrics.get("stock_return_20d")),
            "60거래일 수익률": _ratio_percent(metrics.get("stock_return_60d")),
            "20일 이동평균 대비": _ratio_percent(metrics.get("stock_close_to_ma20")),
            "60일 이동평균 대비": _ratio_percent(metrics.get("stock_close_to_ma60")),
        }
    if card_key == "market.momentum_volume":
        metrics = _dict(observation.get("metrics"))
        return {
            "기준일": observation.get("as_of_date"),
            **{label: _ratio_percent(metrics[key]) for key, label in {"stock_volatility_1y": "1년 연율화 변동성", "stock_max_drawdown_1y": "1년 최대 낙폭", "stock_current_drawdown_1y": "1년 고점 대비 하락률", "stock_position_52w": "1년 가격 범위 내 위치"}.items() if key in metrics},
            **({"최근 5일 / 60일 평균 거래량": _times_display(metrics["stock_volume_ratio_5_60"])} if "stock_volume_ratio_5_60" in metrics else {}),
            "14일 RSI": _number_display(metrics.get("stock_rsi_14")),
            "MACD 히스토그램": _number_display(metrics.get("stock_macd_hist")),
            "MACD 히스토그램 전일 대비 변화": _number_display(
                metrics.get("stock_macd_hist_change_1d")
            ),
            "20일 변동성": _ratio_percent(metrics.get("stock_volatility_20")),
            "20일 평균 대비 거래량": _times_display(metrics.get("stock_volume_ratio_20")),
        }
    if card_key == "valuation.selected_date":
        labels = {
            "market_cap": "시가총액",
            "trailing_pe": "P/E",
            "price_to_sales": "P/S",
            "price_to_book": "P/B",
        }
        metrics = _dict(observation.get("metrics"))
        return {
            "기준일": observation.get("as_of_date"),
            "지표": {
                label: _valuation_display(key, _dict(metrics.get(key)).get("value"))
                for key, label in labels.items()
                if _dict(metrics.get(key)).get("value") is not None
            },
        }
    if card_key == "market.relative_performance":
        benchmark_name = str(
            _dict(source.get("comparison_entities")).get("benchmark_name")
            or observation.get("benchmark_name")
            or "시장지수"
        )
        labels = {
            **{f"stock_excess_return_{m}m": f"{m}개월 {benchmark_name} 대비 초과수익률(%p)" for m in (1, 3, 6, 12)},
            **{f"kospi_return_{m}m": f"{m}개월 {benchmark_name} 수익률" for m in (1, 3, 6, 12)},
            "stock_excess_return_5d": f"5일 {benchmark_name} 대비 초과수익률",
            "stock_excess_return_20d": f"20일 {benchmark_name} 대비 초과수익률",
            "stock_relative_strength_60": f"60일 {benchmark_name} 상대강도",
            "stock_period_excess_return": f"조회기간 {benchmark_name} 대비 초과수익률",
        }
        return {
            "기준일": observation.get("as_of_date"),
            "지표": {
                labels.get(key, key): (_signed_percentage_point(value) if "excess_return" in key else _ratio_percent(value))
                for key, value in _dict(observation.get("metrics")).items()
            },
        }
    if card_key.startswith("peer."):
        unit = str(observation.get("unit") or "")
        metric_labels = {
            "revenue_growth_pct": "매출 성장률",
            "operating_margin_pct": "영업이익률",
            "net_margin_pct": "순이익률",
            "operating_cash_flow_margin_pct": "영업현금흐름 마진",
            "contribution_margin_pct": "기여마진",
            "sga_margin_pct": "판매관리비율",
            "debt_ratio_pct": "부채비율",
            "current_ratio_pct": "유동비율",
            "cash_ratio_pct": "현금비율",
            "equity_ratio_pct": "자기자본비율",
            **{f"stock_return_{m}m_pct": f"{m}개월 주가수익률" for m in (1, 3, 6, 12)},
            **{f"stock_excess_return_{m}m_pct": f"{m}개월 시장 초과수익률" for m in (1, 3, 6, 12)},
            "stock_return_20d_pct": "20일 주가수익률",
            "stock_return_60d_pct": "60일 주가수익률",
            "stock_excess_return_20d_pct": "20일 시장 초과수익률",
            "stock_relative_strength_60_pct": "60일 시장 상대강도",
            "trailing_pe": "P/E",
            "price_to_book": "P/B",
            "price_to_sales": "P/S",
        }
        return {
            "대상 기업": observation.get("target_company"),
            "비교 기업": observation.get("peer_companies") or [observation.get("peer_company")],
            "지표": [
                {
                    "지표명": metric_labels.get(
                        str(pair.get("metric_key") or ""),
                        pair.get("metric_key"),
                    ),
                    "비교 기업명": pair.get("peer_company"),
                    "비교 기준": _peer_basis_display(pair.get("target_basis")),
                    "대상": _metric_display(pair.get("target_value"), unit),
                    "비교 기업": _metric_display(pair.get("peer_value"), unit),
                }
                for pair in _list(observation.get("pairs"))
                if isinstance(pair, dict) and pair.get("comparability") == "comparable"
            ],
        }
    return {}


def _peer_basis_display(value: Any) -> str:
    text = str(value or "").strip()
    if text == "POINT_IN_TIME":
        return "재무상태 시점 기준"
    return text


def _financial_value_display(
    values: dict[str, Any],
    *,
    source_unit: str = "원",
) -> dict[str, str]:
    labels = {
        "revenue": "매출",
        "operating_profit": "영업이익",
        "net_income": "순이익",
        "operating_cash_flow": "영업현금흐름",
    }
    displayed = {
        label: _krw_100m(values.get(key), source_unit=source_unit)
        for key, label in labels.items()
        if values.get(key) is not None
    }
    if values.get("eps") is not None:
        displayed["EPS"] = f"{float(values['eps']):,.0f}원"
    return displayed


def _financial_change_display(values: dict[str, Any]) -> dict[str, str]:
    labels = {
        "revenue": "매출",
        "operating_profit": "영업이익",
        "net_income": "순이익",
        "operating_cash_flow": "영업현금흐름",
        "eps": "EPS",
    }
    return {
        labels[key]: _signed_percent(value)
        for key, value in values.items()
        if key in labels and value is not None
    }


def _financial_margin_display(
    current: dict[str, Any],
    previous: dict[str, Any],
    changes: dict[str, Any],
) -> dict[str, str]:
    labels = {
        "operating_margin": "영업이익률",
        "net_margin": "순이익률",
        "operating_cash_flow_margin": "영업현금흐름률",
        "contribution_margin": "공헌이익률",
        "sga_margin": "판매비와관리비율",
    }
    result: dict[str, str] = {}
    for key, label in labels.items():
        if current.get(key) is None:
            continue
        text = _ratio_percent(current.get(key))
        if previous.get(key) is not None:
            text += f" (전년 동기 {_ratio_percent(previous.get(key))}"
            if changes.get(key) is not None:
                text += f", {_signed_percentage_point(changes.get(key))}"
            text += ")"
        result[label] = text
    return result


def _signed_percent(value: Any) -> str:
    try:
        return f"{float(value) * 100:+.2f}%"
    except (TypeError, ValueError):
        return "데이터 추가 필요"


def _signed_percentage_point(value: Any) -> str:
    try:
        return f"{float(value) * 100:+.2f}%p"
    except (TypeError, ValueError):
        return "데이터 추가 필요"


def _period_pair_display(observation: dict[str, Any]) -> str:
    return f"{_period_display(_dict(observation.get('current_period')))} vs {_period_display(_dict(observation.get('previous_period')))}"


def _period_display(period: dict[str, Any]) -> str:
    year = str(period.get("fiscal_year") or "").strip()
    period_type = str(period.get("period_type") or "").upper()
    labels = {
        "Q1": "1분기 누적",
        "HALF": "반기 누적",
        "Q3": "3분기 누적",
        "ANNUAL": "연간",
        "FULL_YEAR": "연간",
    }
    return " ".join(value for value in (f"{year}년" if year else "", labels.get(period_type, period_type)) if value)


def _krw_100m(value: Any, *, source_unit: str = "원") -> str:
    try:
        value_krw = float(value) * _krw_unit_multiplier(source_unit)
        return f"{value_krw / 100_000_000:,.1f}억원"
    except (TypeError, ValueError):
        return "데이터 추가 필요"


def _krw_unit_multiplier(source_unit: str) -> int:
    unit = str(source_unit or "").strip()
    if unit not in _KRW_UNIT_MULTIPLIERS:
        raise ValueError(f"Unsupported KRW source unit: {source_unit}")
    return _KRW_UNIT_MULTIPLIERS[unit]


def _ratio_percent(value: Any) -> str:
    try:
        return f"{float(value) * 100:.2f}%"
    except (TypeError, ValueError):
        return "데이터 추가 필요"


def _price_display(value: Any) -> str:
    try:
        return f"{float(value):,.0f}원"
    except (TypeError, ValueError):
        return "데이터 추가 필요"


def _number_display(value: Any) -> str:
    try:
        return f"{float(value):,.2f}"
    except (TypeError, ValueError):
        return "데이터 추가 필요"


def _times_display(value: Any) -> str:
    try:
        return f"{float(value):,.2f}배"
    except (TypeError, ValueError):
        return "데이터 추가 필요"


def _valuation_display(metric_key: str, value: Any) -> str:
    if metric_key == "market_cap":
        return _krw_100m(value)
    return _metric_display(value, "times")


def _metric_display(value: Any, unit: str) -> str:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return "데이터 추가 필요"
    suffix = {"%": "%", "times": "배", "ratio": ""}.get(unit, f" {unit}" if unit else "")
    return f"{number:,.2f}{suffix}"


def _dedupe(values: Any) -> list[str]:
    return list(dict.fromkeys(str(value) for value in values if str(value).strip()))


def _text_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return [str(item).strip() for item in value if str(item).strip()]


def _dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _require_dict(value: Any, label: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ValueError(f"writer_handoff.{label} must be an object.")
    return value
