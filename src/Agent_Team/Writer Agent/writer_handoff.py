"""Build the compact, evidence-layered input consumed by the Writer Agent."""

from __future__ import annotations

import json
import re
import copy
from copy import deepcopy
from typing import Any

from shared.evidence_cards import (
    assert_no_internal_references_in_reader_text,
    assert_no_opaque_ids,
    card_content_sha256,
)


HANDOFF_VERSION = "1.0"
EDITORIAL_PACKET_VERSION = "writer_editorial_packet_v2"
WRITER_PROVENANCE_VERSION = "writer_packet_provenance_v2"
FINAL_RECOMMENDATIONS = {"Buy", "Hold", "Sell"}
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
    """Build the bounded Writer v2 input and its external provenance map."""

    source_cards = _dict(strategy_packet.get("cards"))
    assessments = {
        str(item.get("card_key") or ""): item
        for item in _list(strategy_decision.get("evidence_assessments"))
        if isinstance(item, dict) and item.get("card_key")
    }
    if set(assessments) != set(source_cards):
        raise ValueError("Writer v2 requires one Strategy assessment for every compact card.")
    decision = _dict(strategy_decision.get("decision"))
    recommendation_bridge = _dict(strategy_decision.get("recommendation_bridge"))
    section_keys = _dict(strategy_decision.get("section_card_keys"))
    risk_factors = [copy.deepcopy(item) for item in _list(strategy_decision.get("decision_risk_factors")) if isinstance(item, dict)]

    key_evidence_keys = _select_key_evidence_cards(source_cards, assessments)
    thesis_keys = _dedupe(
        [
            *_text_list(recommendation_bridge.get("current_price_card_keys")),
            *_text_list(recommendation_bridge.get("forward_support_card_keys")),
            *_text_list(recommendation_bridge.get("valuation_card_keys")),
            *_text_list(decision.get("positive_factor_card_keys")),
            *_text_list(decision.get("negative_factor_card_keys")),
        ]
    )
    business_keys = _select_keys(
        source_cards,
        ("financial.product_breakdown", "market.absolute_trend", "market.relative_performance"),
    )
    catalyst_keys = _eligible_keys(
        section_keys.get("catalyst_view"),
        source_cards,
        domain="news",
    )
    risk_keys = _dedupe(
        card_key
        for item in risk_factors
        for card_key in _text_list(item.get("basis_card_keys"))
        if card_key in source_cards
    )
    limitation_requirements = [
        copy.deepcopy(item)
        for item in _list(strategy_packet.get("limitation_requirements"))
        if isinstance(item, dict) and item.get("category")
    ]
    required_limitation_keys = _dedupe(
        card_key
        for item in limitation_requirements
        for card_key in _text_list(item.get("basis_card_keys"))
        if card_key in source_cards
    )
    data_limit_keys = _dedupe([
        *required_limitation_keys,
        *[
        card_key
        for card_key, card in source_cards.items()
        if card.get("reader_limitations")
        or card.get("evidence_role") == "reference"
        or card.get("eligibility") != "eligible"
        ],
    ])[:8]
    required_by_component = {
        "investment_call_thesis": thesis_keys,
        "business_market_context": business_keys,
        "key_evidence_table": key_evidence_keys,
        "catalysts_execution": catalyst_keys,
        "risk_monitoring_matrix": risk_keys,
        "data_limits": data_limit_keys,
    }
    included_keys = _dedupe(
        card_key
        for component in WRITER_COMPONENTS
        for card_key in required_by_component[component]
    )
    cards = {
        card_key: _writer_card(source_cards[card_key], assessments[card_key])
        for card_key in included_keys
    }
    risk_factors = [
        {
            **risk,
            "reader_summary": risk.get("reader_summary")
            or " ".join(
                str(_dict(assessments.get(card_key)).get("interpretation") or "").strip()
                for card_key in _text_list(risk.get("basis_card_keys"))
                if str(_dict(assessments.get(card_key)).get("interpretation") or "").strip()
            ),
            "basis_cards": [
                {
                    "label": _dict(cards.get(card_key)).get("label"),
                    "strategy_interpretation": _dict(cards.get(card_key)).get("strategy_interpretation"),
                }
                for card_key in _text_list(risk.get("basis_card_keys"))
                if card_key in cards
            ],
        }
        for risk in risk_factors
    ]
    target = _dict(strategy_packet.get("target_company"))
    packet = {
        "packet_version": EDITORIAL_PACKET_VERSION,
        "target": {
            "company_name": target.get("company_name"),
            "run_key": target.get("run_key"),
            "ticker": target.get("ticker"),
            "selected_date": target.get("as_of_date") or target.get("selected_date"),
        },
        "decision": {
            "opinion": decision.get("opinion"),
            "investment_horizon": decision.get("horizon"),
            "data_coverage": decision.get("evidence_sufficiency"),
            "decision_confidence": recommendation_bridge.get("decision_confidence"),
            "positive_factor_card_keys": _text_list(decision.get("positive_factor_card_keys")),
            "negative_factor_card_keys": _text_list(decision.get("negative_factor_card_keys")),
        },
        "recommendation_bridge": copy.deepcopy(recommendation_bridge),
        "required_card_keys_by_component": required_by_component,
        "cards": cards,
        "peer_findings": [
            copy.deepcopy(item)
            for item in _list(strategy_decision.get("peer_findings"))
            if isinstance(item, dict) and item.get("basis_card_key") in cards
        ],
        "risk_factors": risk_factors,
        "general_limitations": copy.deepcopy(_list(strategy_packet.get("reader_limitations"))),
        "required_limitations": limitation_requirements,
    }
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
    provenance = {
        "provenance_version": WRITER_PROVENANCE_VERSION,
        "target_run_key": target.get("run_key"),
        "cards": provenance_cards,
    }
    validate_writer_editorial_packet(
        packet,
        provenance=provenance,
        strategy_packet=strategy_packet,
    )
    return packet, provenance


def validate_writer_editorial_packet(
    packet: dict[str, Any],
    *,
    provenance: dict[str, Any] | None = None,
    strategy_packet: dict[str, Any] | None = None,
) -> None:
    """Validate the v2 Writer input without judging free-form Korean prose."""

    if not isinstance(packet, dict) or packet.get("packet_version") != EDITORIAL_PACKET_VERSION:
        raise ValueError(f"writer editorial packet version must be {EDITORIAL_PACKET_VERSION}.")
    target = _require_dict(packet.get("target"), "target")
    for key in ("company_name", "run_key", "selected_date"):
        if not str(target.get(key) or "").strip():
            raise ValueError(f"writer editorial target.{key} is required.")
    decision = _require_dict(packet.get("decision"), "decision")
    if decision.get("opinion") not in FINAL_RECOMMENDATIONS:
        raise ValueError("writer editorial decision.opinion must be Buy/Hold/Sell.")
    if not str(decision.get("investment_horizon") or "").strip():
        raise ValueError("writer editorial decision.investment_horizon is required.")
    if decision.get("data_coverage") not in {"high", "medium", "low"}:
        raise ValueError("writer editorial decision.data_coverage is invalid.")
    if decision.get("decision_confidence") not in {"high", "medium", "low"}:
        raise ValueError("writer editorial decision.decision_confidence is invalid.")
    bridge = _require_dict(packet.get("recommendation_bridge"), "recommendation_bridge")
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
    for card_key, card in cards.items():
        if not isinstance(card, dict) or card.get("card_key") != card_key:
            raise ValueError(f"Writer card map key mismatch: {card_key}")
        if not isinstance(card.get("primary_observation"), dict):
            raise ValueError(f"Writer card observation is required: {card_key}")
        if not str(card.get("strategy_interpretation") or "").strip():
            raise ValueError(f"Writer card Strategy interpretation is required: {card_key}")
        if card.get("investment_effect") not in {"positive", "negative", "mixed", "neutral", "reference"}:
            raise ValueError(f"Writer card investment effect is invalid: {card_key}")
        if not str(card.get("evidence_family") or "").strip():
            raise ValueError(f"Writer card evidence_family is required: {card_key}")
    for index, risk in enumerate(_list(packet.get("risk_factors"))):
        if not isinstance(risk, dict):
            raise ValueError(f"risk_factors[{index}] must be an object.")
        unknown = sorted(set(_text_list(risk.get("basis_card_keys"))) - set(cards))
        if unknown:
            raise ValueError(f"Risk factor references cards omitted from Writer packet: {unknown}")
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
        location="writer_editorial_packet_v2.reader_text",
    )
    assert_no_opaque_ids(packet, location="writer_editorial_packet_v2")

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
        "recommendation_bridge": {
            key: bridge.get(key)
            for key in (
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
        "risk_factors": [
            {
                key: item.get(key)
                for key in ("risk_summary", "reader_summary", "monitoring_point")
            }
            for item in _list(packet.get("risk_factors"))
            if isinstance(item, dict)
        ],
    }


def _writer_card(source: dict[str, Any], assessment: dict[str, Any]) -> dict[str, Any]:
    card = {
        "card_key": source.get("card_key"),
        "axis": source.get("card_type"),
        "domain": source.get("domain"),
        "label": source.get("label"),
        "primary_observation": copy.deepcopy(source.get("primary_observation") or {}),
        "strategy_interpretation": assessment.get("interpretation"),
        "investment_effect": assessment.get("investment_effect"),
        "materiality": assessment.get("materiality"),
        "evidence_family": source.get("evidence_family"),
        "observation_basis": source.get("observation_basis"),
        "comparison_scope": source.get("comparison_scope"),
        "decision_use": source.get("decision_use"),
    }
    for key in ("comparison_label", "comparison_entities"):
        if source.get(key):
            card[key] = copy.deepcopy(source[key])
    reader_observation = _reader_observation(source)
    if reader_observation:
        card["reader_observation"] = reader_observation
    if source.get("secondary_context"):
        card["secondary_context"] = copy.deepcopy(source["secondary_context"])
    if source.get("reader_limitations"):
        card["reader_limitations"] = copy.deepcopy(source["reader_limitations"])
    return card


def reformat_financial_reader_observations(
    packet: dict[str, Any],
    *,
    source_unit: str,
) -> dict[str, Any]:
    """Rebuild deterministic financial displays without regenerating LLM prose."""

    _krw_unit_multiplier(source_unit)
    reformatted = deepcopy(packet)
    for card_key, card in _dict(reformatted.get("cards")).items():
        if card_key not in _FINANCIAL_AMOUNT_CARD_KEYS or not isinstance(card, dict):
            continue
        card["reader_observation"] = _reader_observation(
            card,
            financial_source_unit=source_unit,
        )
    return reformatted


def _reader_observation(
    source: dict[str, Any],
    *,
    financial_source_unit: str = "원",
) -> dict[str, Any]:
    card_key = str(source.get("card_key") or "")
    observation = _dict(source.get("primary_observation"))
    if card_key == "financial.same_period_trend":
        return {
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
    if card_key == "financial.cash_flow":
        return {
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
            "stock_excess_return_5d": f"5일 {benchmark_name} 대비 초과수익률",
            "stock_excess_return_20d": f"20일 {benchmark_name} 대비 초과수익률",
            "stock_relative_strength_60": f"60일 {benchmark_name} 상대강도",
            "stock_period_excess_return": f"조회기간 {benchmark_name} 대비 초과수익률",
        }
        return {
            "기준일": observation.get("as_of_date"),
            "지표": {
                labels.get(key, key): _ratio_percent(value)
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


def _select_key_evidence_cards(
    cards: dict[str, Any],
    assessments: dict[str, dict[str, Any]],
) -> list[str]:
    priorities = (
        "financial.same_period_trend",
        "financial.cash_flow",
        "financial.balance_sheet",
        "financial.product_breakdown",
        "valuation.selected_date",
        "market.relative_performance",
        "peer.revenue_growth",
        "peer.valuation",
    )
    selected = _select_keys(cards, priorities)
    if len(selected) < 5:
        selected.extend(
            card_key
            for card_key, assessment in assessments.items()
            if card_key not in selected
            and assessment.get("materiality") in {"decisive", "supporting"}
            and cards[card_key].get("evidence_role") == "primary"
            and cards[card_key].get("eligibility") == "eligible"
        )
    return _dedupe(selected)[:8]


def _select_keys(cards: dict[str, Any], priorities: tuple[str, ...]) -> list[str]:
    return [card_key for card_key in priorities if card_key in cards]


def _eligible_keys(value: Any, cards: dict[str, Any], *, domain: str) -> list[str]:
    return [
        card_key
        for card_key in _text_list(value)
        if card_key in cards
        and cards[card_key].get("domain") == domain
        and cards[card_key].get("eligibility") == "eligible"
    ]


def _dedupe(values: Any) -> list[str]:
    return list(dict.fromkeys(str(value) for value in values if str(value).strip()))


def build_writer_handoff(
    *,
    strategy_report: dict[str, Any],
    strategy_input_bundle: dict[str, Any],
    decision_basis_by_section: dict[str, Any],
) -> dict[str, Any]:
    """Create the bounded Writer contract without generating analytical prose."""

    target = _dict(strategy_input_bundle.get("target_company"))
    target_reports = _dict(strategy_input_bundle.get("target_reports"))
    financial = _dict(target_reports.get("financial"))
    yfinance = _dict(target_reports.get("yfinance"))
    financial_trends = _dict(financial.get("financial_trends"))
    revenue_breakdown = _dict(financial.get("revenue_breakdown"))
    valuation_snapshot = _dict(yfinance.get("valuation_snapshot"))
    decision_balance = _dict(strategy_report.get("decision_balance"))
    recommendation = _dict(strategy_report.get("final_recommendation"))
    opinion = str(recommendation.get("opinion") or "").strip()

    handoff = {
        "handoff_version": HANDOFF_VERSION,
        "target": {
            "company_name": target.get("company_name") or strategy_report.get("target_company_name"),
            "run_key": target.get("run_key") or strategy_report.get("target_run_key"),
            "ticker": target.get("ticker") or financial.get("ticker") or yfinance.get("ticker"),
            "selected_date": _selected_date(strategy_input_bundle, financial, yfinance),
        },
        "decision": {
            "opinion": opinion,
            "summary": recommendation.get("summary"),
            "investment_horizon": recommendation.get("investment_horizon"),
            "evidence_sufficiency": recommendation.get("evidence_sufficiency"),
            "evidence_sufficiency_reason": recommendation.get("evidence_sufficiency_reason"),
            "investment_thesis": deepcopy(_dict(strategy_report.get("investment_thesis"))),
            "final_rationale": deepcopy(_dict(strategy_report.get("final_rationale"))),
        },
        "decisive_positive_evidence": _text_list(decision_balance.get("positive_evidence")),
        "decisive_negative_evidence": _text_list(decision_balance.get("negative_evidence")),
        "contrary_evidence": _contrary_evidence(opinion, decision_balance),
        "business_context": {
            "strategy_view": deepcopy(_dict(strategy_report.get("business_mix_view"))),
        },
        "financial_trend": {
            "strategy_view": deepcopy(_dict(strategy_report.get("financial_view"))),
            "latest_available_filing": deepcopy(
                _dict(_dict(financial.get("collection_context")).get("latest_available_filing"))
            ),
            "future_filing_excluded": _dict(financial.get("collection_context")).get("future_filing_excluded"),
            "current_vs_same_period": deepcopy(_dict(financial_trends.get("current_vs_same_period"))),
            "annual_history": deepcopy(_list(financial_trends.get("annual_history"))),
            "ttm": deepcopy(_dict(financial_trends.get("ttm"))),
        },
        "revenue_breakdown": {
            "status": revenue_breakdown.get("status"),
            "dimension_type": revenue_breakdown.get("dimension_type"),
            "unit": revenue_breakdown.get("unit"),
            "current_period": deepcopy(_dict(revenue_breakdown.get("current_period"))),
            "current_items": deepcopy(_list(revenue_breakdown.get("current_items"))),
            "source": deepcopy(_dict(revenue_breakdown.get("source"))),
            "validation": deepcopy(_dict(revenue_breakdown.get("validation"))),
        },
        "valuation": {
            "strategy_view": deepcopy(_dict(strategy_report.get("valuation_view"))),
            "status": valuation_snapshot.get("status"),
            "selected_date": valuation_snapshot.get("selected_date"),
            "calculated_from_close_and_dart": deepcopy(
                _dict(valuation_snapshot.get("calculated_from_close_and_dart"))
            ),
            "provider_direct_latest": deepcopy(
                _dict(_dict(valuation_snapshot.get("direct_yfinance")).get("latest_period"))
            ),
            "provider_direct_date_policy": _dict(valuation_snapshot.get("direct_yfinance")).get("date_policy"),
            "validation": deepcopy(_dict(valuation_snapshot.get("validation"))),
            "data_limits": _text_list(valuation_snapshot.get("data_limits")),
        },
        "market_context": {
            "strategy_view": deepcopy(_dict(strategy_report.get("market_price_view"))),
            "main_view": deepcopy(_dict(yfinance.get("main_view"))),
            "time_horizon_view": deepcopy(_dict(yfinance.get("time_horizon_view"))),
            "detailed_analysis": deepcopy(_dict(yfinance.get("detailed_analysis"))),
        },
        "peer_comparison": {
            "strategy_view": deepcopy(_dict(strategy_report.get("peer_competitor_positioning"))),
            "peer_groups": deepcopy(_dict(_dict(strategy_input_bundle.get("peer_comparison")).get("peer_groups"))),
            "metrics": deepcopy(_list(_dict(strategy_input_bundle.get("peer_comparison")).get("metrics"))),
            "comparison_limits": _text_list(
                _dict(strategy_input_bundle.get("peer_comparison")).get("comparison_limits")
            ),
        },
        "catalysts": _text_list(_dict(strategy_report.get("catalyst_view")).get("observed_catalysts")),
        "risks": deepcopy(_list(_dict(strategy_report.get("risk_view")).get("observed_risks"))),
        "data_limits": deepcopy(_dict(strategy_report.get("limitations"))),
        "evidence_refs": _compact_evidence_refs(decision_basis_by_section),
    }
    handoff = _remove_path_metadata(handoff)
    validate_writer_handoff(handoff)
    return handoff


def validate_writer_handoff(handoff: dict[str, Any]) -> None:
    """Validate the Writer contract and reject path or truncation leakage."""

    if not isinstance(handoff, dict):
        raise ValueError("writer_handoff must be an object.")
    if handoff.get("handoff_version") != HANDOFF_VERSION:
        raise ValueError(f"writer_handoff.handoff_version must be {HANDOFF_VERSION}.")
    target = _require_dict(handoff.get("target"), "target")
    for key in ("company_name", "run_key", "selected_date"):
        if not str(target.get(key) or "").strip():
            raise ValueError(f"writer_handoff.target.{key} is required.")
    decision = _require_dict(handoff.get("decision"), "decision")
    if decision.get("opinion") not in FINAL_RECOMMENDATIONS:
        raise ValueError("writer_handoff.decision.opinion must be Buy/Hold/Sell.")
    for key in (
        "decisive_positive_evidence",
        "decisive_negative_evidence",
        "contrary_evidence",
        "catalysts",
        "risks",
        "evidence_refs",
    ):
        if not isinstance(handoff.get(key), list):
            raise ValueError(f"writer_handoff.{key} must be a list.")
    for key in (
        "business_context",
        "financial_trend",
        "revenue_breakdown",
        "valuation",
        "market_context",
        "peer_comparison",
        "data_limits",
    ):
        _require_dict(handoff.get(key), key)
    serialized = json.dumps(handoff, ensure_ascii=False, default=str)
    if re.search(r'"(?:source_path|source_files|opinion_index|truncated)"\s*:', serialized):
        raise ValueError("writer_handoff contains forbidden path, audit-index, or truncation metadata.")
    if re.search(r"(?:^|[\s\"'])/(?:home|Users|tmp)/", serialized):
        raise ValueError("writer_handoff contains an absolute file path.")


def handoff_json_size(handoff: dict[str, Any]) -> int:
    """Return the serialized UTF-8 byte size used for prompt-budget checks."""

    return len(json.dumps(handoff, ensure_ascii=False, separators=(",", ":")).encode("utf-8"))


def _selected_date(
    bundle: dict[str, Any],
    financial: dict[str, Any],
    yfinance: dict[str, Any],
) -> str:
    return str(
        _dict(financial.get("collection_context")).get("selected_date")
        or yfinance.get("as_of_date")
        or _dict(bundle.get("target_company")).get("as_of_date")
        or ""
    )


def _contrary_evidence(opinion: str, decision_balance: dict[str, Any]) -> list[dict[str, str]]:
    positive = _text_list(decision_balance.get("positive_evidence"))
    negative = _text_list(decision_balance.get("negative_evidence"))
    if opinion == "Buy":
        return [{"direction": "supports_sell", "statement": item} for item in negative[:3]]
    if opinion == "Sell":
        return [{"direction": "supports_buy", "statement": item} for item in positive[:3]]
    return [
        *({"direction": "supports_buy", "statement": item} for item in positive[:3]),
        *({"direction": "supports_sell", "statement": item} for item in negative[:3]),
    ]


def _compact_evidence_refs(payload: dict[str, Any]) -> list[dict[str, Any]]:
    basis_map = _dict(payload.get("decision_basis_by_section"))
    refs: list[dict[str, Any]] = []
    for strategy_path, raw_entry in basis_map.items():
        entry = _dict(raw_entry)
        sources = []
        for raw_source in _list(entry.get("source_evidence")):
            source = _dict(raw_source)
            compact_source = {
                "agent": source.get("agent"),
                "claim_id": source.get("claim_id"),
                "source_section": source.get("source_section"),
                "evidence_ids": _text_list(source.get("evidence_ids")),
            }
            if any(value for value in compact_source.values() if value):
                sources.append(compact_source)
        refs.append(
            {
                "id": entry.get("opinion_id"),
                "strategy_path": strategy_path,
                "sources": sources,
            }
        )
    return refs


def _remove_path_metadata(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            key: _remove_path_metadata(child)
            for key, child in value.items()
            if key not in {"source_path", "source_files", "opinion_index"}
        }
    if isinstance(value, list):
        return [_remove_path_metadata(item) for item in value]
    return value


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
