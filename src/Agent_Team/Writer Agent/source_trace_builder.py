"""Source trace construction for generated Writer Agent claims."""

from __future__ import annotations

from typing import Any

from data_loader import get_nested


def build_source_trace(
    strategy_report: dict[str, Any],
    visual_blocks: list[dict[str, Any]],
    *,
    key_metrics: list[dict[str, Any]] | None = None,
) -> list[dict[str, str]]:
    """Build source trace entries for core report claims."""

    entries: list[dict[str, str]] = []
    _add(entries, strategy_report, "final_recommendation.summary", "Investment Summary")
    _add(entries, strategy_report, "investment_thesis.thesis_1", "Investment Summary")
    _add(entries, strategy_report, "investment_thesis.thesis_2", "Catalyst & Risk")
    _add(entries, strategy_report, "investment_thesis.thesis_3", "Final Rationale")
    _add(entries, strategy_report, "financial_view.revenue", "Financial View")
    _add(entries, strategy_report, "financial_view.profitability", "Financial View")
    _add(entries, strategy_report, "financial_view.cash_flow", "Financial View")
    _add(entries, strategy_report, "financial_view.balance_sheet", "Financial View")
    _add(entries, strategy_report, "market_price_view.price_trend", "Market / Price View")
    _add(entries, strategy_report, "market_price_view.volume", "Market / Price View")
    _add(entries, strategy_report, "market_price_view.relative_strength", "Market / Price View")
    _add(entries, strategy_report, "peer_competitor_positioning.peer_based_investment_implication", "Peer / Competitor Positioning")
    _add(entries, strategy_report, "final_rationale.why_buy_hold_sell", "Final Rationale")

    for block in visual_blocks:
        entries.append(
            {
                "claim": f"Figure {block['figure_id']} is used in {block['section']} with manifest-provided caption and interpretation limit.",
                "source_file": "chart_manifest.json",
                "source_field": block["figure_id"],
                "used_in_section": block["section"],
            }
        )
    for metric in key_metrics or []:
        source_field = metric.get("source_field", "")
        if not source_field:
            continue
        entries.append(
            {
                "claim": f"{metric.get('metric_name', '')}: {metric.get('value', '')}",
                "source_file": metric.get("source_file", "strategy_report.json"),
                "source_field": source_field,
                "used_in_section": "key_metrics_table",
            }
        )
    return entries


def _add(entries: list[dict[str, str]], payload: dict[str, Any], field: str, section: str) -> None:
    value = get_nested(payload, field)
    if value is None:
        return
    entries.append(
        {
            "claim": str(value),
            "source_file": "strategy_report.json",
            "source_field": field,
            "used_in_section": section,
        }
    )
