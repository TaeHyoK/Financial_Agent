"""Self-contained Strategy compact packet and deterministic semantic gates."""

from __future__ import annotations

import copy
import re
from collections import defaultdict
from collections.abc import Iterable, Mapping
from typing import Any

from shared.evidence_cards import (
    PRODUCT_DISCLOSURE_SCOPE_LABEL,
    SECONDARY_CONTEXT_USAGE,
    assert_no_opaque_ids,
    card_content_sha256,
    validate_provenance_map,
    validate_self_contained_card,
)
from shared.llm_clients import compact_json, estimate_text_tokens, measure_top_level_fields
from shared.coerce import as_dict as _dict, as_list as _list, is_finite_number as _finite
from orchestration.ablation import config_from_mapping
from .context_links import build_context_links
from shared.subdata_guidance import CONTEXT_USAGE


PACKET_VERSION = "strategy_compact_packet"
PROVENANCE_VERSION = "strategy_packet_provenance"

STRATEGY_SECTIONS = (
    "investment_thesis",
    "financial_view",
    "business_mix_view",
    "catalyst_view",
    "risk_view",
    "market_price_view",
    "valuation_view",
    "cross_agent_consistency_check",
    "peer_competitor_positioning",
    "decision_balance",
    "limitations",
)
CANONICAL_SECTIONS = frozenset(
    {
        *STRATEGY_SECTIONS,
        "final_recommendation",
        "final_rationale",
        "investment_call_thesis",
        "business_market_context",
        "key_evidence_table",
        "catalysts_execution",
        "risk_monitoring_matrix",
        "data_limits",
    }
)

# Historical sizing targets, now used only for telemetry advisories.
CARD_BUDGETS = {
    "financial": 7,
    "news": 8,
    "market": 3,
    "valuation": 2,
    "peer": 6,
}
INVESTMENT_EFFECTS = frozenset({"positive", "negative", "mixed", "neutral", "reference"})
MATERIALITY_VALUES = frozenset({"decisive", "supporting", "context"})
COMPARISON_SCOPES = frozenset(
    {"none", "company_history", "market_benchmark", "selected_peer", "industry_aggregate"}
)
OBSERVATION_BASES = frozenset(
    {"point_in_time", "period_snapshot", "period_comparison", "time_series", "event", "pairwise_comparison", "reference"}
)
DECISION_USES = frozenset({"factor_eligible", "context_only"})


class PacketOverflowError(ValueError):
    """Raised before an LLM call when mandatory card coverage cannot fit."""


def build_compact_strategy_packet(
    input_bundle: dict[str, Any],
    *,
    model: str = "",
) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any], dict[str, Any]]:
    """Build the LLM packet without count-based loss of cited agent evidence."""

    reports = _dict(input_bundle.get("target_reports"))
    ablation = config_from_mapping(input_bundle.get("ablation"))
    included_domains = set(ablation.included_domains)
    validations = _dict(input_bundle.get("target_validation_evidence"))
    source_files = _dict(input_bundle.get("input_metadata"))
    target = copy.deepcopy(_dict(input_bundle.get("target_company")))
    cards: dict[str, dict[str, Any]] = {}
    provenance_rows: dict[str, dict[str, Any]] = {}
    machine_records: list[dict[str, Any]] = []

    def add_card(card: dict[str, Any], *, raw_ids: Iterable[str], source_paths: Iterable[str]) -> None:
        card_key = str(card.get("card_key") or "")
        if card_key in cards:
            raise ValueError(f"Duplicate card_key: {card_key}")
        cards[card_key] = card
        provenance_rows[card_key] = {
            "source_evidence_ids": _dedupe_strings(raw_ids),
            "source_paths": _dedupe_strings(source_paths),
            "source_files": _source_files_for_domain(source_files, str(card.get("domain") or "")),
        }
        for blocker in card.get("machine_blockers") or []:
            if isinstance(blocker, dict):
                machine_records.append({"card_key": card_key, **copy.deepcopy(blocker)})

    financial_report = _dict(reports.get("financial"))
    for card, raw_ids, source_paths in _financial_cards(financial_report):
        add_card(card, raw_ids=raw_ids, source_paths=source_paths)

    news_report = _dict(reports.get("news"))
    news_validation = _dict(validations.get("news"))
    news_catalog = _dict(_dict(input_bundle.get("evidence_catalogs")).get("news"))
    selected_news, omitted_news = _news_cards(news_report, news_validation, news_catalog)
    for card, raw_ids, source_paths in selected_news:
        add_card(card, raw_ids=raw_ids, source_paths=source_paths)
        if card["primary_observation"].get("anchor_source"):
            provenance_rows[card["card_key"]]["anchor_evidence_id"] = raw_ids[0]

    yfinance_report = _dict(reports.get("yfinance"))
    for card, raw_ids, source_paths in _market_cards(yfinance_report):
        add_card(card, raw_ids=raw_ids, source_paths=source_paths)
    for card, raw_ids, source_paths in _valuation_cards(yfinance_report):
        add_card(card, raw_ids=raw_ids, source_paths=source_paths)

    for card, raw_ids, source_paths in build_peer_pair_cards(_dict(input_bundle.get("peer_comparison"))):
        if _peer_card_source_domain(card) in included_domains:
            add_card(card, raw_ids=raw_ids, source_paths=source_paths)

    peer_analysis_card = build_peer_analysis_card(
        _dict(input_bundle.get("peer_comparison_analysis")),
        included_domains=included_domains,
    )
    if peer_analysis_card is not None:
        card, raw_ids, source_paths = peer_analysis_card
        add_card(card, raw_ids=raw_ids, source_paths=source_paths)

    context_links = build_context_links(
        input_bundle=input_bundle, cards=cards, provenance=provenance_rows,
        add_card=add_card, card_factory=_card, included_domains=included_domains,
    )
    _attach_secondary_context(
        cards,
        provenance_rows,
        reports,
        included_source_domains=included_domains,
    )
    peer_comparison = _dict(input_bundle.get("peer_comparison"))
    packet_peer_comparison = (
        peer_comparison
        if any(card.get("domain") == "peer" for card in cards.values())
        else {}
    )
    reader_limitations = _reader_limitations(
        cards,
        input_bundle.get("decision_constraints") or [],
        packet_peer_comparison.get("comparison_limits") or [],
    )
    limitation_requirements = _limitation_requirements(cards, packet_peer_comparison)
    section_inputs = _build_section_inputs(cards)
    packet = {
        "agent_name": "Strategy Agent",
        "packet_version": PACKET_VERSION,
        "target_company": target,
        "selected_date_policy": "market_data_strictly_before_selected_date",
        "evidence_scope": ablation.as_dict(),
        "section_inputs": section_inputs,
        "cards": cards,
        "context_links": context_links,
        "reader_limitations": reader_limitations,
        "limitation_requirements": limitation_requirements,
        "coverage_summary": {
            "card_counts": _card_counts(cards),
            "news_total_claims": len(selected_news) + len(omitted_news),
            "news_preserved_claims": len(selected_news),
            "news_omitted_claims": len(omitted_news),
        },
    }
    provenance = {
        "provenance_version": PROVENANCE_VERSION,
        "target_run_key": target.get("run_key"),
        "cards": provenance_rows,
    }
    for card_key, card in cards.items():
        provenance_rows[card_key]["strategy_card_sha256"] = card_content_sha256(card)

    telemetry = _packet_telemetry(packet, model=model)
    input_summary = {
        "contract": "strategy_input",
        "status": "pass",
        "packet_version": PACKET_VERSION,
        "machine_limitations": machine_records,
        "omitted_news_events": omitted_news,
        "telemetry": telemetry,
    }
    validate_compact_strategy_packet(packet, provenance)
    return packet, provenance, telemetry, input_summary


def validate_compact_strategy_packet(
    packet: dict[str, Any],
    provenance: dict[str, Any],
) -> None:
    """Validate packet construction, card routing, and provenance."""

    if packet.get("packet_version") != PACKET_VERSION:
        raise ValueError(f"Unsupported Strategy packet version: {packet.get('packet_version')}")
    cards = _dict(packet.get("cards"))
    if not cards:
        raise ValueError("Strategy compact packet requires at least one card.")
    for card_key, card in cards.items():
        if card_key != card.get("card_key"):
            raise ValueError(f"Card map key mismatch: {card_key}")
        validate_self_contained_card(card, allowed_section_names=CANONICAL_SECTIONS)
        _validate_card_semantics(card)
    limitation_rows = _list(packet.get("limitation_requirements"))
    categories: set[str] = set()
    for index, row in enumerate(limitation_rows):
        if not isinstance(row, dict) or not str(row.get("category") or "").strip():
            raise ValueError(f"Invalid limitation_requirements[{index}].")
        category = str(row["category"])
        if category in categories:
            raise ValueError(f"Duplicate limitation category: {category}")
        categories.add(category)
        unknown = sorted(set(_dedupe_strings(row.get("basis_card_keys") or [])) - set(cards))
        if unknown:
            raise ValueError(f"Unknown limitation basis card(s): {unknown}")
    for section, card_keys in _dict(packet.get("section_inputs")).items():
        if section not in STRATEGY_SECTIONS:
            raise ValueError(f"Unknown Strategy section: {section}")
        for card_key in card_keys or []:
            card = cards.get(card_key)
            if not isinstance(card, dict) or section not in (card.get("allowed_sections") or []):
                raise ValueError(f"Card {card_key} is not allowed in section {section}")
    assert_no_opaque_ids(packet, location=PACKET_VERSION)
    validate_provenance_map(cards, provenance)


def _requires_product_scope_label(card: dict[str, Any]) -> bool:
    if card.get("card_key") != "financial.product_breakdown":
        return False
    reconciliation = _dict(_dict(card.get("primary_observation")).get("reconciliation"))
    return reconciliation.get("reconciliation_status") != "matched"


def _nonempty_string_schema() -> dict[str, Any]:
    """Use a Structured Outputs-compatible pattern instead of unsupported minLength."""

    return {"type": "string", "pattern": r"\S"}


def _financial_cards(report: dict[str, Any]) -> list[tuple[dict[str, Any], list[str], list[str]]]:
    trends = _dict(report.get("financial_trends"))
    comparison = _dict(trends.get("current_vs_same_period"))
    current_period = _dict(comparison.get("current_period"))
    previous_period = _dict(comparison.get("previous_period"))
    current_values = _dict(comparison.get("current_values"))
    previous_values = _dict(comparison.get("previous_values"))
    evidence = [item for item in _list(_dict(report.get("strategy_handoff")).get("key_evidence")) if isinstance(item, dict)]

    def ids_for(*tokens: str) -> list[str]:
        lowered = tuple(token.lower() for token in tokens)
        return [
            str(item.get("evidence_id"))
            for item in evidence
            if item.get("evidence_id")
            and any(token in str(item.get("metric_or_event") or "").lower() for token in lowered)
        ]

    cards: list[tuple[dict[str, Any], list[str], list[str]]] = []
    collection = _dict(report.get("collection_context"))
    latest = _dict(collection.get("latest_available_filing"))
    if latest:
        filing_limitations = []
        if collection.get("fallback_applied"):
            filing_limitations.append("기준일 당시 최신 사용 가능 공시가 이론상 목표 기간보다 이전 기간이다.")
        cards.append(
            (
                _card(
                    "financial.filing_basis",
                    domain="financial",
                    card_type="filing_basis",
                    label="공시 기준",
                    allowed_sections=("financial_view", "limitations"),
                    evidence_family="filing_basis",
                    observation_basis="reference",
                    role="reference",
                    observation={
                        "selected_date": collection.get("selected_date"),
                        "latest_available_filing": _pick(
                            latest,
                            "fiscal_year",
                            "period_type",
                            "period_end",
                            "receipt_date",
                            "report_name",
                        ),
                        "theoretical_target": _pick(
                            _dict(collection.get("theoretical_target")),
                            "fiscal_year",
                            "period_type",
                            "period_end",
                        ),
                        "fallback_applied": bool(collection.get("fallback_applied")),
                        "statement_scope": collection.get("statement_scope") or "unknown",
                    },
                    reader_limitations=filing_limitations,
                ),
                [],
                ["financial.collection_context"],
            )
        )
    if current_values and previous_values:
        blockers = _period_pair_blockers(current_period, previous_period)
        change_rates = _period_change_rates(
            current_values,
            previous_values,
            ("revenue", "operating_profit", "net_income", "operating_cash_flow", "eps"),
        )
        current_margins = {
            **_pick(
                _dict(_path(report, "financial_trends.normalized_metrics.current_values")),
                "operating_margin",
                "net_margin",
                "operating_cash_flow_margin",
            ),
            **_pick(current_values, "contribution_margin", "sga_margin"),
        }
        previous_margins = {
            **_pick(
                _dict(_path(report, "financial_trends.normalized_metrics.previous_values")),
                "operating_margin",
                "net_margin",
                "operating_cash_flow_margin",
            ),
            **_pick(previous_values, "contribution_margin", "sga_margin"),
        }
        cards.append(
            (
                _card(
                    "financial.same_period_trend",
                    domain="financial",
                    card_type="same_period_trend",
                    label="동일기간 재무 추세",
                    allowed_sections=(
                        "investment_thesis",
                        "financial_view",
                        "cross_agent_consistency_check",
                        "decision_balance",
                        "key_evidence_table",
                    ),
                    evidence_family="financial_performance",
                    observation_basis="period_comparison",
                    comparison_scope="company_history",
                    comparison_label="전년 동기 대비",
                    observation={
                        "current_period": _period(current_period),
                        "previous_period": _period(previous_period),
                        "current_values": _pick(
                            current_values,
                            "revenue",
                            "operating_profit",
                            "net_income",
                            "operating_cash_flow",
                            "eps",
                        ),
                        "previous_values": _pick(
                            previous_values,
                            "revenue",
                            "operating_profit",
                            "net_income",
                            "operating_cash_flow",
                            "eps",
                        ),
                        "change_rates": change_rates,
                        "current_margins": current_margins,
                        "previous_margins": previous_margins,
                        "margin_changes": _margin_changes(current_margins, previous_margins),
                    },
                    eligibility="incomparable" if blockers else "eligible",
                    machine_blockers=blockers,
                ),
                ids_for(
                    "revenue",
                    "operating profit",
                    "net income",
                    "cash flow",
                    "eps",
                    "contribution margin",
                    "sg&a margin",
                ),
                ["financial.financial_trends.current_vs_same_period"],
            )
        )
    annual = [
        {
            "period": _period(_dict(item.get("period"))),
            "values": _pick(
                _dict(item.get("values")),
                "revenue",
                "operating_profit",
                "net_income",
                "operating_cash_flow",
                "operating_margin",
                "total_equity",
                "debt_ratio",
            ),
        }
        for item in _list(trends.get("annual_history"))
        if isinstance(item, dict)
    ][:3]
    if annual:
        cards.append(
            (
                _card(
                    "financial.annual_trend",
                    domain="financial",
                    card_type="annual_trend",
                    label="3개년 연간 재무 추세",
                    allowed_sections=("investment_thesis", "financial_view", "decision_balance"),
                    evidence_family="financial_performance",
                    observation_basis="time_series",
                    comparison_scope="company_history",
                    comparison_label="과거 연간 추세",
                    observation={"annual_history": annual},
                ),
                [],
                ["financial.financial_trends.annual_history"],
            )
        )
    if current_values.get("operating_cash_flow") is not None:
        cards.append(
            (
                _card(
                    "financial.cash_flow",
                    domain="financial",
                    card_type="cash_flow",
                    label="영업현금흐름",
                    allowed_sections=("investment_thesis", "financial_view", "risk_view", "decision_balance"),
                    evidence_family="cash_generation",
                    observation_basis=(
                        "period_comparison"
                        if previous_values.get("operating_cash_flow") is not None
                        else "period_snapshot"
                    ),
                    comparison_scope=(
                        "company_history"
                        if previous_values.get("operating_cash_flow") is not None
                        else "none"
                    ),
                    comparison_label=(
                        "전년 동기 대비"
                        if previous_values.get("operating_cash_flow") is not None
                        else ""
                    ),
                    observation={
                        "current_period": _period(current_period),
                        "current_operating_cash_flow": current_values.get("operating_cash_flow"),
                        "previous_period": _period(previous_period),
                        "previous_operating_cash_flow": previous_values.get("operating_cash_flow"),
                        "operating_cash_flow_change_rate": _change_rate(
                            current_values.get("operating_cash_flow"),
                            previous_values.get("operating_cash_flow"),
                        ),
                        "current_operating_cash_flow_margin": _normalized_metric(
                            report,
                            "operating_cash_flow_margin",
                        ),
                        "previous_operating_cash_flow_margin": _normalized_metric(
                            report,
                            "operating_cash_flow_margin",
                            period="previous_values",
                        ),
                    },
                ),
                ids_for("cash flow"),
                ["financial.financial_trends.current_vs_same_period.current_values.operating_cash_flow"],
            )
        )
    balance_evidence = next(
        (item for item in evidence if "balance sheet" in str(item.get("metric_or_event") or "").lower()),
        {},
    )
    balance_values = copy.deepcopy(_dict(balance_evidence.get("value")))
    if balance_values:
        cards.append(
            (
                _card(
                    "financial.balance_sheet",
                    domain="financial",
                    card_type="balance_sheet",
                    label="재무상태와 유동성",
                    allowed_sections=("investment_thesis", "financial_view", "risk_view", "decision_balance"),
                    evidence_family="financial_position",
                    observation_basis="point_in_time",
                    observation={
                        "as_of_date": balance_evidence.get("period"),
                        "period_basis": balance_evidence.get("period_basis") or "POINT_IN_TIME",
                        "values": balance_values,
                    },
                ),
                ids_for("balance sheet"),
                ["financial.strategy_handoff.key_evidence.balance_sheet"],
            )
        )
    breakdown = _dict(report.get("revenue_breakdown"))
    if breakdown.get("status") == "available":
        reconciliation = _dict(_dict(breakdown.get("validation")).get("financial_statement_reconciliation"))
        status = str(reconciliation.get("reconciliation_status") or "incomparable")
        limitation = []
        if status != "matched":
            limitation.append("주요 제품·서비스 공시표 기준이며 재무제표 전체 매출 구성으로 확대할 수 없다.")
        items = [
            _pick(item, "name", "revenue", "revenue_disclosed", "revenue_krw", "revenue_share", "revenue_share_disclosed")
            for item in _list(breakdown.get("current_items"))
            if isinstance(item, dict)
        ]
        blockers = [] if status == "matched" else [{"code": "revenue_breakdown_scope_unreconciled", "reason": status}]
        cards.append(
            (
                _card(
                    "financial.product_breakdown",
                    domain="financial",
                    card_type="product_breakdown",
                    label="주요 제품·서비스 매출",
                    allowed_sections=("business_mix_view", "risk_view", "limitations"),
                    evidence_family="business_mix",
                    observation_basis="period_snapshot",
                    observation={
                        "period": _period(_dict(breakdown.get("current_period"))),
                        "unit": breakdown.get("unit"),
                        "items": items,
                        "statement_scope": breakdown.get("statement_scope") or "unknown",
                        "breakdown_scope": breakdown.get("breakdown_scope") or "unknown",
                        "reconciliation": reconciliation,
                    },
                    eligibility="eligible" if status == "matched" else "reference_only",
                    reader_limitations=limitation,
                    machine_blockers=blockers,
                ),
                [],
                ["financial.revenue_breakdown"],
            )
        )
    return cards


def _market_cards(report: dict[str, Any]) -> list[tuple[dict[str, Any], list[str], list[str]]]:
    catalog = _dict(report.get("primary_evidence_catalog"))
    benchmark_name = _market_benchmark_name(report)

    def metrics(keys: Iterable[str]) -> tuple[dict[str, Any], list[str]]:
        values: dict[str, Any] = {}
        units: dict[str, Any] = {}
        dates: set[str] = set()
        periods: dict[str, str] = {}
        raw_ids: list[str] = []
        for evidence_id, evidence in catalog.items():
            if not isinstance(evidence, dict):
                continue
            metric = str(evidence.get("metric") or "")
            if metric not in keys:
                continue
            values[metric] = copy.deepcopy(evidence.get("value"))
            if evidence.get("unit"):
                units[metric] = evidence.get("unit")
            if evidence.get("source_date"):
                dates.add(str(evidence.get("source_date")))
            if evidence.get("period"):
                periods[metric] = str(evidence.get("period"))
            raw_ids.append(str(evidence_id))
        observation: dict[str, Any] = {"metrics": values}
        if units:
            observation["units"] = units
        if len(dates) == 1:
            observation["as_of_date"] = next(iter(dates))
        elif dates:
            observation["as_of_dates"] = sorted(dates)
        if periods:
            observation["periods"] = periods
        return observation, raw_ids

    specs = (
        (
            "market.absolute_trend",
            "절대 가격 추세",
            ("stock_close", "stock_return_1m", "stock_return_3m", "stock_return_6m", "stock_return_12m", "stock_close_to_ma120", "stock_close_to_ma200", "stock_ma120_change_20d", "stock_ma200_change_20d", "stock_return_20d", "stock_return_60d"),
            ("investment_thesis", "market_price_view", "decision_balance"),
            "market_price_performance",
            "time_series",
            "company_history",
            "기간별 주가 추세",
        ),
        (
            "market.relative_performance",
            "시장 상대성과",
            ("stock_excess_return_1m", "stock_excess_return_3m", "stock_excess_return_6m", "stock_excess_return_12m", "kospi_return_1m", "kospi_return_3m", "kospi_return_6m", "kospi_return_12m", "stock_excess_return_20d", "stock_relative_strength_60"),
            ("investment_thesis", "market_price_view", "risk_view", "decision_balance"),
            "market_price_performance",
            "time_series",
            "market_benchmark",
            f"{benchmark_name} 대비",
        ),
        (
            "market.momentum_volume",
            "연간 변동 위험과 거래량",
            ("stock_volatility_1y", "stock_max_drawdown_1y", "stock_current_drawdown_1y", "stock_volume_ratio_5_60", "stock_position_52w", "stock_rsi_14", "stock_volatility_20", "stock_volume_ratio_20"),
            ("market_price_view", "risk_view", "decision_balance"),
            "market_technical",
            "point_in_time",
            "none",
            "",
        ),
    )
    cards = []
    for card_key, label, metric_keys, sections, family, basis, scope, comparison_label in specs:
        observation, raw_ids = metrics(metric_keys)
        if not observation.get("metrics"):
            continue
        if scope == "market_benchmark":
            observation["benchmark_name"] = benchmark_name
        cards.append(
            (
                _card(
                    card_key,
                    domain="market",
                    card_type=card_key.rsplit(".", 1)[-1],
                    label=label,
                    allowed_sections=sections,
                    evidence_family=family,
                    observation_basis=basis,
                    comparison_scope=scope,
                    comparison_label=comparison_label,
                    comparison_entities=(
                        {"benchmark_name": benchmark_name}
                        if scope == "market_benchmark"
                        else {}
                    ),
                    observation=observation,
                ),
                raw_ids,
                [f"yfinance.primary_evidence_catalog.{key}" for key in observation["metrics"]],
            )
        )
    return cards


def _valuation_cards(report: dict[str, Any]) -> list[tuple[dict[str, Any], list[str], list[str]]]:
    snapshot = _dict(report.get("valuation_snapshot"))
    cards: list[tuple[dict[str, Any], list[str], list[str]]] = []
    calculated = _dict(snapshot.get("calculated_from_close_and_dart"))
    calculated_metrics = _dict(calculated.get("metrics"))
    if calculated.get("status") in {"available", "partial"} and calculated_metrics:
        calculated_metrics = {key: row for key, row in calculated_metrics.items()
                              if _dict(row).get("status") == "ok" and _finite(_dict(row).get("value"))}
        blockers = []
        for key, metric in calculated_metrics.items():
            row = _dict(metric)
            if row.get("status") != "ok" or not _finite(row.get("value")):
                blockers.append({"code": "invalid_calculated_valuation", "reason": str(key)})
            if key == "trailing_pe" and _finite(row.get("value")) and float(row["value"]) <= 0:
                blockers.append({"code": "invalid_loss_company_pe", "reason": "trailing_pe_non_positive"})
        cards.append(
            (
                _card(
                    "valuation.selected_date",
                    domain="valuation",
                    card_type="selected_date_calculated",
                    label="공시 주식 수 기준 가치평가 추정",
                    allowed_sections=("investment_thesis", "valuation_view", "risk_view", "decision_balance"),
                    evidence_family="valuation",
                    observation_basis="point_in_time",
                    observation={
                        "as_of_date": calculated.get("as_of_date"),
                        "method": calculated.get("calculation_basis", "disclosed_share_count_estimate"),
                        "statement_scope": calculated.get("statement_scope"),
                        "data_limits": calculated.get("data_limits", []),
                        "metrics": copy.deepcopy(calculated_metrics),
                        "inputs": _compact_valuation_inputs(_dict(calculated.get("inputs"))),
                    },
                    eligibility="incomparable" if blockers else "eligible",
                    machine_blockers=blockers,
                ),
                [
                    evidence_id
                    for evidence_id, evidence in _dict(report.get("primary_evidence_catalog")).items()
                    if isinstance(evidence, dict) and evidence.get("metric") == "stock_close"
                ],
                ["yfinance.valuation_snapshot.calculated_from_close_and_dart"],
            )
        )
    direct = _dict(snapshot.get("direct_yfinance"))
    latest = _dict(direct.get("latest_period"))
    if direct.get("status") == "available" and latest:
        cards.append(
            (
                _card(
                    "valuation.provider_reference",
                    domain="valuation",
                    card_type="provider_direct_reference",
                    label="제공자 표시 밸류에이션 참고값",
                    allowed_sections=("valuation_view", "limitations"),
                    evidence_family="valuation",
                    observation_basis="reference",
                    role="reference",
                    observation={
                        "valuation_date": latest.get("valuation_date"),
                        "metrics": {
                            key: _pick(_dict(metric), "value", "unit", "status")
                            for key, metric in _dict(latest.get("metrics")).items()
                            if key in {"market_cap", "trailing_pe", "price_to_sales", "price_to_book", "enterprise_value_to_revenue", "enterprise_value_to_ebitda"}
                        },
                        "date_policy": direct.get("date_policy"),
                    },
                    eligibility="reference_only",
                    reader_limitations=["제공업체의 과거 배수는 기준일 당시 공개된 값임이 보장되지 않으므로 주된 가치평가 근거로 사용하지 않는다."],
                ),
                [],
                ["yfinance.valuation_snapshot.direct_yfinance.latest_period"],
            )
        )
    return cards


def build_peer_pair_cards(
    peer_comparison: dict[str, Any],
) -> list[tuple[dict[str, Any], list[str], list[str]]]:
    """Convert target/peer scalars into typed, date-preserving comparison cards."""

    rows = [item for item in _list(peer_comparison.get("metrics")) if isinstance(item, dict)]
    target = next((row for row in rows if row.get("peer_group") == "target"), None)
    peers = [row for row in rows if row.get("peer_group") != "target"]
    if not target or not peers:
        return []

    specs = (
        (
            "peer.revenue_growth",
            "동일기간 매출 성장률 비교",
            "rate",
            (("financial_metrics.revenue_growth_pct", "%", "higher", "growth_direction"),),
            "financial_period",
            "financial_performance",
        ),
        (
            "peer.profitability",
            "수익성과 현금창출력 비교",
            "margin",
            (
                ("financial_metrics.operating_margin_pct", "%", "higher", "profitability"),
                ("financial_metrics.net_margin_pct", "%", "higher", "profitability"),
                ("financial_metrics.operating_cash_flow_margin_pct", "%", "higher", "cash_generation_quality"),
                ("financial_metrics.contribution_margin_pct", "%", "higher", "profitability"),
                ("financial_metrics.sga_margin_pct", "%", "lower", "cost_efficiency"),
            ),
            "financial_period",
            "financial_performance",
        ),
        (
            "peer.financial_position",
            "재무상태와 유동성 비교",
            "ratio",
            (
                ("financial_metrics.debt_ratio_pct", "%", "lower", "leverage"),
                ("financial_metrics.current_ratio_pct", "%", "higher", "liquidity"),
                ("financial_metrics.cash_ratio_pct", "%", "higher", "liquidity"),
                ("financial_metrics.equity_ratio_pct", "%", "higher", "capital_structure"),
            ),
            "balance_sheet_basis",
            "financial_position",
        ),
        (
            "peer.market_performance",
            "시장 성과 비교",
            "market_relative",
            (
                ("market_metrics.stock_return_3m_pct", "%", "higher", "peer_relative"),
                ("market_metrics.stock_return_6m_pct", "%", "higher", "peer_relative"),
                ("market_metrics.stock_return_12m_pct", "%", "higher", "peer_relative"),
                ("market_metrics.stock_return_60d_pct", "%", "higher", "peer_relative"),
                ("market_metrics.stock_excess_return_12m_pct", "%", "higher", "market_relative"),
                ("market_metrics.stock_relative_strength_60_pct", "%", "higher", "market_relative"),
            ),
            "market_date",
            "market_price_performance",
        ),
        (
            "peer.valuation",
            "동일 날짜 계산 밸류에이션 비교",
            "valuation",
            (
                ("valuation_metrics.trailing_pe", "times", "lower_multiple", "multiple_gap"),
                ("valuation_metrics.price_to_book", "times", "lower_multiple", "multiple_gap"),
                ("valuation_metrics.price_to_sales", "times", "lower_multiple", "multiple_gap"),
            ),
            "calculated_as_of_date",
            "valuation",
        ),
    )
    results: list[tuple[dict[str, Any], list[str], list[str]]] = []
    peer_names = _dedupe_strings(peer.get("company_name") for peer in peers)
    comparison_label = (
        f"{peer_names[0]} 대비" if len(peer_names) == 1 else "선정 비교기업 대비"
    )
    for card_key, label, comparison_type, metric_specs, basis_field, evidence_family in specs:
        pairs = []
        blockers = []
        for peer in peers:
            peer_name = str(peer.get("company_name") or "").strip()
            for metric_path, unit, preferred, interpretation in metric_specs:
                target_value = _path(target, metric_path)
                peer_value = _path(peer, metric_path)
                metric_key = metric_path.rsplit(".", 1)[-1]
                if not _finite(target_value) and not _finite(peer_value):
                    blockers.append(
                        {
                            "code": "peer_metric_unavailable_for_both",
                            "reason": f"{peer_name}:{metric_key}",
                        }
                    )
                    continue
                target_basis, peer_basis = _peer_basis(target, peer, metric_path, basis_field)
                reasons = []
                if not _finite(target_value) or not _finite(peer_value):
                    reasons.append("missing_or_invalid_value")
                if target_basis and peer_basis and target_basis != peer_basis:
                    reasons.append("basis_mismatch")
                if comparison_type == "valuation" and (
                    float(target_value or 0) <= 0 or float(peer_value or 0) <= 0
                ):
                    reasons.append("non_positive_valuation_multiple")
                comparability = "comparable" if not reasons else "incomparable"
                pair = {
                    "metric_key": metric_key,
                    "peer_company": peer_name,
                    "target_value": target_value,
                    "peer_value": peer_value,
                    "target_basis": target_basis,
                    "peer_basis": peer_basis,
                    "comparability": comparability,
                    "allowed_interpretation": interpretation,
                    "preferred_direction": preferred,
                }
                if reasons:
                    blockers.extend(
                        {
                            "code": f"peer_{reason}",
                            "reason": f"{peer_name}:{metric_key}",
                        }
                        for reason in reasons
                    )
                pairs.append(pair)
        comparable_count = sum(pair["comparability"] == "comparable" for pair in pairs)
        role = "reference" if comparison_type == "scale" else "primary"
        eligibility = "reference_only" if comparison_type == "scale" else "eligible" if comparable_count else "incomparable"
        first_pair = pairs[0] if pairs else {}
        results.append(
            (
                _card(
                    card_key,
                    domain="peer",
                    card_type=comparison_type,
                    label=label,
                    allowed_sections=("investment_thesis", "peer_competitor_positioning", "risk_view", "decision_balance"),
                    evidence_family=evidence_family,
                    observation_basis="pairwise_comparison",
                    comparison_scope="selected_peer",
                    comparison_label=comparison_label,
                    comparison_entities={
                        "target_company": target.get("company_name"),
                        "peer_companies": peer_names,
                        "peer_count": len(peer_names),
                    },
                    role=role,
                    observation={
                        "target_company": target.get("company_name"),
                        "peer_company": peer_names[0] if peer_names else None,
                        "peer_companies": peer_names,
                        "peer_count": len(peer_names),
                        "unit": metric_specs[0][1],
                        "target_basis": first_pair.get("target_basis"),
                        "peer_basis": first_pair.get("peer_basis"),
                        "pairs": pairs,
                    },
                    eligibility=eligibility,
                    machine_blockers=blockers,
                ),
                [],
                [f"peer_comparison.metrics.{metric_path}" for metric_path, *_ in metric_specs],
            )
        )
    return results


def build_peer_analysis_card(
    peer_analysis: dict[str, Any],
    *,
    included_domains: set[str],
) -> tuple[dict[str, Any], list[str], list[str]] | None:
    """Expose the comparison agent's judgment as one evidence-linked peer card."""

    if not peer_analysis:
        return None
    selected_cards = {
        str(item.get("card_key") or ""): item
        for item in _list(peer_analysis.get("selected_basis_cards"))
        if isinstance(item, dict) and str(item.get("card_key") or "").strip()
    }
    allowed_card_keys = {
        key
        for key, item in selected_cards.items()
        if _comparison_source_domain(str(item.get("domain") or "")) in included_domains
    }
    points = []
    used_keys: set[str] = set()
    for item in _list(peer_analysis.get("comparison_points")):
        if not isinstance(item, dict):
            continue
        basis = [entry for entry in _list(item.get("basis")) if isinstance(entry, dict)]
        basis_keys = {str(entry.get("card_key") or "") for entry in basis}
        if not basis_keys or not basis_keys.issubset(allowed_card_keys):
            continue
        points.append(copy.deepcopy(item))
        used_keys.update(basis_keys)
    if not points:
        return None
    target_company = str(peer_analysis.get("target_company") or "").strip()
    peer_company = str(peer_analysis.get("peer_company") or "").strip()
    all_domains_included = included_domains == {"financial", "news", "yfinance"}
    observation = {
        "comparison_points": points,
        "selected_basis": [
            {
                "card_key": key,
                "label": selected_cards[key].get("label"),
                "company_scope": selected_cards[key].get("company_scope"),
                "domain": selected_cards[key].get("domain"),
            }
            for key in sorted(used_keys)
        ],
        "comparison_limitations": _dedupe_strings(
            peer_analysis.get("comparison_limitations") or []
        ),
    }
    if all_domains_included:
        observation["comparison_brief"] = str(peer_analysis.get("comparison_brief") or "").strip()
    card = _card(
        "peer.agent_analysis",
        domain="peer",
        card_type="agent_comparison",
        label="대상기업과 선정 비교기업 종합 비교",
        allowed_sections=(
            "investment_thesis",
            "peer_competitor_positioning",
            "risk_view",
            "decision_balance",
        ),
        evidence_family="selected_peer_analysis",
        observation_basis="pairwise_comparison",
        comparison_scope="selected_peer",
        comparison_label=f"{peer_company} 대비" if peer_company else "선정 비교기업 대비",
        comparison_entities={
            "target_company": target_company,
            "peer_companies": [peer_company] if peer_company else [],
            "peer_count": 1 if peer_company else 0,
        },
        observation=observation,
        reader_limitations=observation["comparison_limitations"],
    )
    source_path = str(peer_analysis.get("source_path") or "").strip()
    return card, [], [source_path] if source_path else []


def _comparison_source_domain(domain: str) -> str:
    return "yfinance" if domain in {"market", "valuation"} else domain


def _news_cards(
    report: dict[str, Any],
    validation: dict[str, Any],
    catalog: dict[str, Any],
) -> tuple[
    list[tuple[dict[str, Any], list[str], list[str]]],
    list[dict[str, Any]],
]:
    output = _dict(report.get("output")) or report
    news_only = _dict(_dict(output.get("analysis_blocks")).get("news_only"))
    validation_by_section = {
        str(item.get("section") or ""): item
        for item in _list(validation.get("claims"))
        if isinstance(item, dict)
    }
    candidates = []
    for source_key in ("positive_signals", "negative_signals", "key_risks", "uncertainties"):
        for index, item in enumerate(_list(news_only.get(source_key))):
            if not isinstance(item, dict):
                continue
            section = f"analysis_blocks.news_only.{source_key}[{index}]"
            validation_row = _dict(validation_by_section.get(section))
            evidence_use = str(validation_row.get("evidence_use") or "context_only")
            if evidence_use == "exclude":
                continue
            anchor_id = str(item.get("anchor_evidence_id") or "")
            if anchor_id and anchor_id not in catalog:
                raise ValueError(f"Unknown news anchor evidence ID: {anchor_id}")
            raw_ids = _dedupe_strings([
                *([anchor_id] if anchor_id else []),
                *(item.get("evidence_ids") or validation_row.get("evidence_ids") or []),
            ])
            raw_ids = [value for value in raw_ids if value in catalog]
            if not raw_ids:
                continue
            candidates.append(
                {
                    "source_key": source_key,
                    "section": section,
                    "claim": str(item.get("claim") or "").strip(),
                    "anchor_evidence_id": anchor_id,
                    "evidence_ids": raw_ids,
                    "evidence_use": evidence_use,
                    "event_status": _metadata(item, validation_row, "event_status", "insufficient"),
                    "company_specificity": _metadata(item, validation_row, "company_specificity", "insufficient"),
                    "materiality_status": _metadata(item, validation_row, "materiality_status", "not_established"),
                    "financial_link_status": _metadata(item, validation_row, "financial_link_status", "not_observed"),
                    "limitations": _dedupe_strings(validation_row.get("limitations") or []),
                }
            )
    # Preserve each analytical claim, including different interpretations of
    # the same article. A shared source does not make two claims one event.
    cards = [_news_claim_card(candidate, catalog) for candidate in candidates]
    seen_keys: dict[str, int] = {}
    for card, _raw_ids, _source_paths in cards:
        base_key = str(card["card_key"])
        seen_keys[base_key] = seen_keys.get(base_key, 0) + 1
        if seen_keys[base_key] > 1:
            card["card_key"] = f"{base_key}.{seen_keys[base_key]}"
    cards.sort(key=lambda value: _news_card_priority(value[0]), reverse=True)
    # These are citations already selected by the News Agent, not the raw
    # collection pool. Do not perform a second count-based news selection here.
    return cards, []


def _news_claim_card(
    main: dict[str, Any],
    catalog: dict[str, Any],
) -> tuple[dict[str, Any], list[str], list[str]]:
    raw_ids = _dedupe_strings(main["evidence_ids"])
    evidence_rows = [_dict(catalog.get(evidence_id)) for evidence_id in raw_ids]
    evidence_rows = [item for item in evidence_rows if item]
    period_rows = [row for row in evidence_rows if row.get("source_type") == "monthly_news_context"
                   or row.get("metric") == "monthly_news_context"]
    event_rows = [row for row in evidence_rows if row not in period_rows]
    anchor = _dict(catalog.get(main.get("anchor_evidence_id")))
    summary_only = anchor in period_rows if anchor else bool(period_rows) and not event_rows
    source_periods = sorted({str(row.get("period") or "") for row in period_rows if row.get("period")})
    dates = sorted(
        str(item.get("source_date") or item.get("time") or "")
        for item in ([anchor] if anchor and not summary_only else event_rows if not summary_only else [])
        if str(item.get("source_date") or item.get("time") or "")
    )
    event_date = dates[-1] if dates else None
    excerpts = _dedupe_strings(
        str(item.get("snippet") or item.get("text") or item.get("title") or "").strip()
        for item in evidence_rows
    )
    # Keep each cited source's date attached to its own text. The anchor date
    # describes only the representative article, not all events in the claim.
    cited_sources = []
    for row in evidence_rows:
        if row in period_rows:
            cited_sources.append({
                "source_type": "monthly_news_context",
                "period": row.get("period"),
                "title": str(row.get("title") or ""),
                "summary": str(row.get("snippet") or row.get("text") or row.get("title") or "").strip(),
            })
        else:
            cited_sources.append({
                "source_type": "article",
                "source_date": row.get("source_date") or row.get("time") or None,
                "title": str(row.get("title") or ""),
                "snippet": str(row.get("snippet") or row.get("text") or row.get("title") or "").strip(),
                **({"event_timeline": copy.deepcopy(row["event_timeline"])} if row.get("event_timeline") else {}),
            })
    coverage_rows = [_dict(item.get("coverage")) for item in evidence_rows]
    publisher_names = {
        str(name)
        for row in coverage_rows
        for name in row.get("publisher_names") or []
        if str(name)
    }
    if not publisher_names:
        publisher_names = {
            str(row.get("source") or "").strip().lower()
            for row in evidence_rows
            if str(row.get("source") or "").strip()
        }
    coverage = {
        "article_count": sum(int(item.get("article_count") or row.get("mention_count") or 0) for item, row in zip(coverage_rows, evidence_rows)),
        "unique_publisher_count": len(publisher_names),
        "deduplicated_article_count": sum(int(item.get("deduplicated_article_count") or row.get("mention_count") or 0) for item, row in zip(coverage_rows, evidence_rows)),
        "primary_source_present": any(bool(item.get("primary_source_present")) for item in coverage_rows),
        "coverage_quality": "verified" if coverage_rows and all(item.get("coverage_quality") == "verified" for item in coverage_rows) else "partial",
    }
    statuses = {
        key: str(main.get(key) or "")
        for key in (
            "event_status",
            "company_specificity",
            "materiality_status",
            "financial_link_status",
        )
    }
    event_materiality = _news_event_materiality(statuses, coverage)
    if summary_only:
        event_materiality = "operational_context"
        coverage.update(article_count=None, unique_publisher_count=None, deduplicated_article_count=None,
                        primary_source_present=False, coverage_quality="summarized")
    source_roles = [str(main["source_key"])]
    blockers = []
    if summary_only:
        blockers.append({"code": "news_monthly_summary", "reason": "period_context_without_direct_event_date"})
    if statuses["company_specificity"] in {"industry_context", "insufficient", "mixed"}:
        blockers.append({"code": "news_company_specificity_not_direct", "reason": statuses["company_specificity"]})
    if statuses["event_status"] in {"reported_expectation", "allegation", "insufficient", "mixed"}:
        blockers.append({"code": "news_event_not_observed", "reason": statuses["event_status"]})
    if statuses["materiality_status"] in {"not_established", "mixed"}:
        blockers.append({"code": "news_materiality_not_established", "reason": statuses["materiality_status"]})
    role = "primary" if not blockers and main.get("evidence_use") == "strong" else "reference"
    eligibility = "eligible" if role == "primary" else "reference_only"
    limitations = _dedupe_strings(
        [
            *(["월별 뉴스 흐름의 요약이며 요약 종료일은 개별 사건의 발생일이 아니다."] if summary_only else []),
            *(main.get("limitations") or []),
        ]
    )
    title = str(main.get("claim") or evidence_rows[0].get("title") or "뉴스 사건")
    date_key = event_date or ("period_" + "_".join(source_periods) if source_periods else "unknown_date")
    card_key = f"news.{_semantic_slug(date_key)}.{_semantic_slug(title)}"
    return (
        _card(
            card_key,
            domain="news",
            card_type="period_summary" if summary_only else "event",
            label=title[:36],
            allowed_sections=(
                "investment_thesis",
                "catalyst_view",
                "risk_view",
                "cross_agent_consistency_check",
                "decision_balance",
            ),
            evidence_family=f"corporate_event:{_semantic_slug(title)}",
            observation_basis="period_snapshot" if summary_only else "event",
            decision_use=(
                "factor_eligible"
                if event_materiality in {"confirmed_financial", "probable_financial"}
                else "context_only"
            ),
            role=role,
            observation={
                "event_date": event_date,
                "date_precision": "period" if summary_only else "publication_date",
                "source_periods": source_periods,
                "evidence_origin": "model_summarized" if summary_only else "raw_source_with_period_context" if period_rows else "raw_source",
                "event_summary": title,
                "representative_excerpts": excerpts,
                "cited_sources": cited_sources,
                **({"anchor_source": {
                    "source_type": "monthly_news_context" if anchor in period_rows else "article",
                    "title": str(anchor.get("title") or ""),
                    "source_date": anchor.get("source_date") or anchor.get("time"),
                }} if anchor else {}),
                **statuses,
                "event_materiality": event_materiality,
                "coverage": coverage,
                "source_roles": source_roles,
            },
            eligibility=eligibility,
            reader_limitations=limitations,
            machine_blockers=blockers,
        ),
        raw_ids,
        [str(item.get("source_ref") or "news.evidence_map") for item in evidence_rows],
    )


def _attach_secondary_context(
    cards: dict[str, dict[str, Any]],
    provenance: dict[str, dict[str, Any]],
    reports: dict[str, Any],
    *,
    included_source_domains: set[str] | None = None,
) -> None:
    for origin_domain, report in reports.items():
        if not isinstance(report, dict):
            continue
        source = _dict(report.get("output")) or report
        assessments = _list(source.get("secondary_context_assessment"))
        domain_cards = [key for key, card in cards.items() if card.get("domain") == _canonical_domain(origin_domain)]
        for assessment in assessments:
            if not isinstance(assessment, dict) or not str(assessment.get("statement") or "").strip():
                continue
            if assessment.get("usage") == CONTEXT_USAGE:
                # New interpretations have exact multi-card links in context_links.
                continue
            source_domain = _canonical_domain(str(assessment.get("source_domain") or ""))
            if included_source_domains is not None and source_domain not in included_source_domains:
                continue
            primary_ids = set(_dedupe_strings(assessment.get("primary_evidence_ids") or []))
            matching = [
                card_key
                for card_key in domain_cards
                if primary_ids.intersection(provenance.get(card_key, {}).get("source_evidence_ids") or [])
            ]
            card_key = matching[0] if matching else ""
            if not card_key:
                continue
            context = {
                "source_domain": str(assessment.get("source_domain") or ""),
                "effect": str(assessment.get("effect") or "neutral"),
                "usage": SECONDARY_CONTEXT_USAGE,
                "statement": str(assessment.get("statement") or "").strip(),
            }
            limitation = str(assessment.get("limitation") or "").strip()
            if limitation:
                context["limitation"] = limitation
            contexts = cards[card_key].setdefault("secondary_context", [])
            if context not in contexts:
                contexts.append(context)


def _peer_card_source_domain(card: dict[str, Any]) -> str:
    card_key = str(card.get("card_key") or "")
    if card_key in {"peer.market_performance", "peer.valuation"}:
        return "yfinance"
    return "financial"


def _reader_limitations(
    cards: dict[str, dict[str, Any]],
    decision_constraints: Iterable[Any],
    peer_limits: Iterable[Any],
) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    for text in [*decision_constraints, *peer_limits]:
        value = str(text or "").strip()
        if value:
            rows.append({"basis_card_key": "", "text": value})
    seen: set[str] = set()
    result = []
    for row in rows:
        normalized = re.sub(r"\s+", " ", row["text"]).strip().lower()
        if not normalized or normalized in seen:
            continue
        seen.add(normalized)
        result.append(row)
    return result


def _limitation_requirements(
    cards: dict[str, dict[str, Any]],
    peer_comparison: dict[str, Any],
) -> list[dict[str, Any]]:
    """Build typed coverage requirements without generating report prose."""

    requirements: list[dict[str, Any]] = []
    filing = _dict(cards.get("financial.filing_basis"))
    filing_observation = _dict(filing.get("primary_observation"))
    if filing_observation.get("fallback_applied"):
        requirements.append(
            {
                "category": "filing_lag",
                "basis_card_keys": ["financial.filing_basis"],
                "facts": {
                    "selected_date": filing_observation.get("selected_date"),
                    "latest_available_filing": copy.deepcopy(
                        _dict(filing_observation.get("latest_available_filing"))
                    ),
                },
            }
        )

    peer_cards = [card for card in cards.values() if card.get("comparison_scope") == "selected_peer"]
    peer_entities = _dict(peer_cards[0].get("comparison_entities")) if peer_cards else {}
    peer_names = _dedupe_strings(peer_entities.get("peer_companies") or [])
    if not peer_names:
        peer_names = _dedupe_strings(
            row.get("company_name")
            for row in _list(peer_comparison.get("metrics"))
            if isinstance(row, dict) and row.get("peer_group") != "target"
        )
    peer_category = (
        "no_peer_scope"
        if not peer_names
        else "single_peer_scope"
        if len(peer_names) == 1
        else "selected_peer_scope"
    )
    requirements.append(
        {
            "category": peer_category,
            "basis_card_keys": [str(peer_cards[0].get("card_key"))] if peer_cards else [],
            "facts": {"peer_count": len(peer_names), "peer_companies": peer_names},
        }
    )

    valuation = _dict(cards.get("valuation.selected_date"))
    valuation_observation = _dict(valuation.get("primary_observation"))
    valuation_date = str(valuation_observation.get("as_of_date") or "")
    input_dates = sorted(
        {
            str(row.get("as_of_date"))
            for row in _dict(valuation_observation.get("inputs")).values()
            if isinstance(row, dict) and str(row.get("as_of_date") or "")
        }
    )
    if valuation and input_dates and any(value != valuation_date for value in input_dates):
        requirements.append(
            {
                "category": "valuation_input_date_mix",
                "basis_card_keys": ["valuation.selected_date"],
                "facts": {
                    "valuation_date": valuation_date,
                    "fundamental_input_dates": input_dates,
                },
            }
        )

    product = _dict(cards.get("financial.product_breakdown"))
    if product and _requires_product_scope_label(product):
        requirements.append(
            {
                "category": "product_breakdown_scope",
                "basis_card_keys": ["financial.product_breakdown"],
                "facts": {
                    "scope_label": PRODUCT_DISCLOSURE_SCOPE_LABEL,
                    "reconciliation": copy.deepcopy(
                        _dict(_dict(product.get("primary_observation")).get("reconciliation"))
                    ),
                },
            }
        )

    return requirements


def _build_section_inputs(cards: dict[str, dict[str, Any]]) -> dict[str, list[str]]:
    """Assign each card one primary home while allowed_sections retains reuse rules."""

    result = {section: [] for section in STRATEGY_SECTIONS}
    for card_key, card in cards.items():
        domain = str(card.get("domain") or "")
        if card_key in {"financial.filing_basis", "valuation.provider_reference"}:
            section = "limitations"
        elif card_key == "financial.product_breakdown":
            section = "business_mix_view"
        elif domain == "financial":
            section = "financial_view"
        elif domain == "news":
            roles = set(_list(_dict(card.get("primary_observation")).get("source_roles")))
            section = "risk_view" if roles.intersection({"negative_signals", "key_risks"}) else "catalyst_view"
        elif domain == "market":
            section = "market_price_view"
        elif domain == "valuation":
            section = "valuation_view"
        elif domain == "peer":
            section = "peer_competitor_positioning"
        else:
            section = "limitations"
        if section not in (card.get("allowed_sections") or []):
            allowed = [value for value in card.get("allowed_sections") or [] if value in STRATEGY_SECTIONS]
            section = allowed[0] if allowed else "limitations"
        result[section].append(card_key)
    return result


def _compact_valuation_inputs(inputs: dict[str, Any]) -> dict[str, Any]:
    return {
        key: _pick(_dict(value), "value", "as_of_date", "status")
        for key, value in inputs.items()
        if isinstance(value, dict)
    }


def _card(
    card_key: str,
    *,
    domain: str,
    card_type: str,
    label: str,
    allowed_sections: Iterable[str],
    evidence_family: str,
    observation_basis: str,
    observation: dict[str, Any],
    comparison_scope: str = "none",
    comparison_label: str = "",
    comparison_entities: Mapping[str, Any] | None = None,
    decision_use: str = "factor_eligible",
    role: str = "primary",
    eligibility: str = "eligible",
    reader_limitations: Iterable[str] = (),
    machine_blockers: Iterable[dict[str, str]] = (),
) -> dict[str, Any]:
    card = {
        "card_key": card_key,
        "domain": domain,
        "card_type": card_type,
        "label": label,
        "allowed_sections": list(dict.fromkeys(allowed_sections)),
        "evidence_family": evidence_family,
        "observation_basis": observation_basis,
        "comparison_scope": comparison_scope,
        "decision_use": "context_only" if role == "reference" else decision_use,
        "evidence_role": role,
        "eligibility": eligibility,
        "primary_observation": copy.deepcopy(observation),
    }
    if comparison_label:
        card["comparison_label"] = comparison_label
    if comparison_entities:
        card["comparison_entities"] = copy.deepcopy(dict(comparison_entities))
    normalized_limitations = _dedupe_strings(reader_limitations)
    normalized_blockers = [copy.deepcopy(item) for item in machine_blockers]
    if normalized_limitations:
        card["reader_limitations"] = normalized_limitations
    if normalized_blockers:
        card["machine_blockers"] = normalized_blockers
    return card


def _validate_card_semantics(card: dict[str, Any]) -> None:
    card_key = str(card.get("card_key") or "")
    if not str(card.get("evidence_family") or "").strip():
        raise ValueError(f"evidence_family is required for {card_key}")
    if card.get("observation_basis") not in OBSERVATION_BASES:
        raise ValueError(f"Invalid observation_basis for {card_key}")
    scope = str(card.get("comparison_scope") or "")
    if scope not in COMPARISON_SCOPES:
        raise ValueError(f"Invalid comparison_scope for {card_key}: {scope}")
    if card.get("decision_use") not in DECISION_USES:
        raise ValueError(f"Invalid decision_use for {card_key}")
    entities = _dict(card.get("comparison_entities"))
    if scope == "market_benchmark":
        benchmark_name = str(entities.get("benchmark_name") or "").strip()
        if not benchmark_name or benchmark_name not in str(card.get("comparison_label") or ""):
            raise ValueError(f"Market benchmark metadata is incomplete for {card_key}")
    if scope == "selected_peer":
        peers = _dedupe_strings(entities.get("peer_companies") or [])
        if not peers or int(entities.get("peer_count") or 0) != len(peers):
            raise ValueError(f"Selected-peer metadata is incomplete for {card_key}")


def _period_pair_blockers(current: dict[str, Any], previous: dict[str, Any]) -> list[dict[str, str]]:
    blockers = []
    for key in ("period_type", "basis"):
        if current.get(key) and previous.get(key) and current.get(key) != previous.get(key):
            blockers.append({"code": f"financial_{key}_mismatch", "reason": f"{current.get(key)} != {previous.get(key)}"})
    return blockers


def _change_rate(current: Any, previous: Any) -> float | None:
    if not _finite(current) or not _finite(previous) or float(previous) == 0:
        return None
    return (float(current) - float(previous)) / abs(float(previous))


def _period_change_rates(
    current: dict[str, Any],
    previous: dict[str, Any],
    metric_keys: Iterable[str],
) -> dict[str, float]:
    return {
        metric_key: rate
        for metric_key in metric_keys
        if (rate := _change_rate(current.get(metric_key), previous.get(metric_key))) is not None
    }


def _margin_changes(
    current: dict[str, Any],
    previous: dict[str, Any],
) -> dict[str, float]:
    return {
        metric_key: float(current[metric_key]) - float(previous[metric_key])
        for metric_key in current.keys() & previous.keys()
        if _finite(current.get(metric_key)) and _finite(previous.get(metric_key))
    }


def _normalized_metric(
    report: dict[str, Any],
    metric: str,
    *,
    period: str = "current_values",
) -> Any:
    return _path(report, f"financial_trends.normalized_metrics.{period}.{metric}")


def _market_benchmark_name(report: dict[str, Any]) -> str:
    for value in (
        report.get("benchmark_name"),
        report.get("market_benchmark_name"),
        _dict(report.get("benchmark")).get("name"),
        _dict(report.get("market_benchmark")).get("name"),
    ):
        text = str(value or "").strip()
        if text:
            return text
    # The current YFinance pipeline computes every stock_excess_* metric from ^KS11.
    return "KOSPI"


def _peer_basis(
    target: dict[str, Any],
    peer: dict[str, Any],
    metric_path: str,
    basis_field: str,
) -> tuple[str, str]:
    section = metric_path.split(".", 1)[0]
    if basis_field == "as_of_date":
        return str(target.get("as_of_date") or ""), str(peer.get("as_of_date") or "")
    return (
        str(_path(target, f"{section}.{basis_field}") or ""),
        str(_path(peer, f"{section}.{basis_field}") or ""),
    )


def _packet_telemetry(packet: dict[str, Any], *, model: str) -> dict[str, Any]:
    serialized = compact_json(packet)
    counts = _card_counts(_dict(packet.get("cards")))
    return {
        "packet_version": PACKET_VERSION,
        "serialized_bytes": len(serialized.encode("utf-8")),
        "estimated_input_tokens": estimate_text_tokens(serialized, model=model),
        "top_level_fields": measure_top_level_fields(packet, model=model),
        "card_counts": counts,
        "advisories": [
            f"{domain} card count exceeds historical sizing target: {count} > {CARD_BUDGETS[domain]}; all preserved"
            for domain, count in counts.items()
            if domain in CARD_BUDGETS and count > CARD_BUDGETS[domain]
        ],
        "overflow": False,
    }


def _source_files_for_domain(metadata: dict[str, Any], domain: str) -> list[str]:
    keys = {
        "financial": "target_financial_path",
        "news": "target_news_path",
        "market": "target_yfinance_path",
        "valuation": "target_yfinance_path",
        "peer": ("peer_comparison_path", "peer_analysis_path"),
    }.get(domain)
    if not keys:
        return []
    if isinstance(keys, str):
        keys = (keys,)
    return [str(metadata[key]) for key in keys if metadata.get(key)]


def _card_counts(cards: dict[str, Any]) -> dict[str, int]:
    counts: dict[str, int] = defaultdict(int)
    for card in cards.values():
        if isinstance(card, dict):
            counts[str(card.get("domain") or "unknown")] += 1
    return dict(counts)


def _canonical_domain(value: str) -> str:
    return {"yfinance": "market"}.get(value, value)


def _news_event_materiality(
    statuses: dict[str, str],
    coverage: dict[str, Any],
) -> str:
    if statuses.get("financial_link_status") == "observed":
        return "confirmed_financial"
    if (
        statuses.get("event_status") in {"occurred", "announced"}
        and statuses.get("company_specificity") in {"direct", "product_direct"}
        and statuses.get("materiality_status") == "observed"
        and bool(coverage.get("primary_source_present"))
    ):
        return "probable_financial"
    if (
        statuses.get("event_status") in {"occurred", "announced"}
        and statuses.get("company_specificity") in {"direct", "product_direct"}
    ):
        return "occurrence_only"
    return "operational_context"


def _news_card_priority(card: dict[str, Any]) -> tuple[int, int, int, str]:
    observation = _dict(card.get("primary_observation"))
    critical = int(_is_critical_news_card(card))
    materiality = {"observed": 3, "plausible_unquantified": 2, "mixed": 1, "not_established": 0}.get(observation.get("materiality_status"), 0)
    directness = {"product_direct": 4, "direct": 3, "mixed": 1, "industry_context": 0, "insufficient": 0}.get(observation.get("company_specificity"), 0)
    return critical, materiality, directness, str(observation.get("event_date") or "")


def _is_critical_news_card(card: dict[str, Any]) -> bool:
    observation = _dict(card.get("primary_observation"))
    return (
        observation.get("evidence_origin") != "model_summarized"
        and observation.get("company_specificity") in {"direct", "product_direct"}
        and observation.get("event_status") in {"occurred", "announced"}
        and observation.get("materiality_status") == "observed"
    )


def _metadata(item: dict[str, Any], validation: dict[str, Any], key: str, fallback: str) -> str:
    return str(validation.get(key) or item.get(key) or fallback)


def _semantic_slug(value: str, limit: int = 20) -> str:
    normalized = re.sub(r"[^0-9A-Za-z가-힣]+", "_", value).strip("_").lower()
    return (normalized[:limit].rstrip("_") or "event")


def _period(value: dict[str, Any]) -> dict[str, Any]:
    return _pick(value, "fiscal_year", "period_type", "period_end", "basis")


def _pick(value: dict[str, Any], *keys: str) -> dict[str, Any]:
    return {
        key: copy.deepcopy(value.get(key))
        for key in keys
        if value.get(key) not in (None, "", [], {})
    }


def _path(value: Any, path: str) -> Any:
    current = value
    for key in path.split("."):
        if not isinstance(current, Mapping):
            return None
        current = current.get(key)
    return current


def _dedupe_strings(values: Iterable[Any]) -> list[str]:
    return list(dict.fromkeys(str(value).strip() for value in values if str(value).strip()))


__all__ = [
    "CANONICAL_SECTIONS",
    "PACKET_VERSION",
    "PROVENANCE_VERSION",
    "PacketOverflowError",
    "STRATEGY_SECTIONS",
    "build_compact_strategy_packet",
    "build_peer_pair_cards",
    "validate_compact_strategy_packet",
]
