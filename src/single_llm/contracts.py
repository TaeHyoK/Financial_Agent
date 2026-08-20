"""Strict generation and validation contracts for Single-LLM Direct."""

from __future__ import annotations

from typing import Any


REPORT_VERSION = "single_llm_direct_v1"
BUNDLE_VERSION = "single_llm_input_bundle_v1"
SOURCE_MANIFEST_VERSION = "single_llm_source_manifest_v1"
VALIDATION_VERSION = "single_llm_validation_v1"
RECOMMENDATIONS = ("BUY", "HOLD", "SELL")
CONVICTIONS = ("LOW", "MEDIUM", "HIGH")
INVESTMENT_EFFECTS = ("POSITIVE", "NEGATIVE", "MIXED", "NEUTRAL")
ANALYSIS_SECTIONS = (
    "business_and_financial",
    "market_and_valuation",
    "news_and_catalysts",
    "peer_comparison",
)


def single_llm_response_format(
    *,
    evidence_ids: list[str],
    company_name: str,
    selected_date: str,
    decision_horizon: str,
) -> dict[str, Any]:
    """Return the dynamic Structured Outputs schema for one report."""

    allowed = sorted({str(item) for item in evidence_ids if str(item).strip()})
    if not allowed:
        raise ValueError("At least one evidence ID is required")

    evidence_refs = _bounded_array(
        {"type": "string", "enum": allowed},
        min_items=1,
        max_items=min(8, len(allowed)),
    )
    claim_unit = _strict_object(
        {
            "claim": {"type": "string"},
            "observation": {"type": "string"},
            "interpretation": {"type": "string"},
            "investment_effect": {
                "type": "string",
                "enum": list(INVESTMENT_EFFECTS),
            },
            "evidence_ids": evidence_refs,
        }
    )
    key_evidence_row = _strict_object(
        {
            "label": {"type": "string"},
            "observed_fact": {"type": "string"},
            "interpretation": {"type": "string"},
            "investment_effect": {
                "type": "string",
                "enum": list(INVESTMENT_EFFECTS),
            },
            "evidence_ids": evidence_refs,
        }
    )
    condition = _strict_object(
        {
            "condition": {"type": "string"},
            "why_it_matters": {"type": "string"},
            "evidence_ids": evidence_refs,
        }
    )
    risk = _strict_object(
        {
            "risk": {"type": "string"},
            "current_evidence": {"type": "string"},
            "monitoring_trigger": {"type": "string"},
            "potential_impact": {"type": "string"},
            "evidence_ids": evidence_refs,
        }
    )
    limitation = _strict_object(
        {
            "limitation": {"type": "string"},
            "report_impact": {"type": "string"},
            "evidence_ids": _bounded_array(
                {"type": "string", "enum": allowed},
                min_items=0,
                max_items=min(4, len(allowed)),
            ),
        }
    )
    schema = _strict_object(
        {
            "report_version": {"type": "string", "enum": [REPORT_VERSION]},
            "metadata": _strict_object(
                {
                    "report_title": {"type": "string"},
                    "company_name": {"type": "string", "enum": [company_name]},
                    "selected_date": {"type": "string", "enum": [selected_date]},
                    "decision_horizon": {
                        "type": "string",
                        "enum": [decision_horizon],
                    },
                }
            ),
            "investment_call": _strict_object(
                {
                    "recommendation": {
                        "type": "string",
                        "enum": list(RECOMMENDATIONS),
                    },
                    "conviction": {
                        "type": "string",
                        "enum": list(CONVICTIONS),
                    },
                    "thesis": {"type": "string"},
                    "current_price_rationale": {"type": "string"},
                    "forward_outlook": {"type": "string"},
                    "valuation_view": {"type": "string"},
                    "residual_uncertainty": {"type": "string"},
                    "upgrade_conditions": _bounded_array(
                        condition, min_items=1, max_items=4
                    ),
                    "downgrade_conditions": _bounded_array(
                        condition, min_items=1, max_items=4
                    ),
                    "evidence_ids": _bounded_array(
                        {"type": "string", "enum": allowed},
                        min_items=2,
                        max_items=min(10, len(allowed)),
                    ),
                }
            ),
            "key_evidence": _bounded_array(
                key_evidence_row, min_items=5, max_items=12
            ),
            "analysis": _strict_object(
                {
                    section: _bounded_array(claim_unit, min_items=2, max_items=8)
                    for section in ANALYSIS_SECTIONS
                }
            ),
            "risks": _bounded_array(risk, min_items=3, max_items=8),
            "data_limits": _bounded_array(limitation, min_items=1, max_items=6),
        }
    )
    return {
        "type": "json_schema",
        "json_schema": {
            "name": REPORT_VERSION,
            "strict": True,
            "schema": schema,
        },
    }


def _strict_object(properties: dict[str, Any]) -> dict[str, Any]:
    return {
        "type": "object",
        "properties": properties,
        "required": list(properties),
        "additionalProperties": False,
    }


def _bounded_array(
    item_schema: dict[str, Any],
    *,
    min_items: int,
    max_items: int,
) -> dict[str, Any]:
    if max_items < min_items:
        raise ValueError("array maximum cannot be smaller than minimum")
    return {
        "type": "array",
        "items": item_schema,
        "minItems": min_items,
        "maxItems": max_items,
    }


__all__ = [
    "ANALYSIS_SECTIONS",
    "BUNDLE_VERSION",
    "CONVICTIONS",
    "INVESTMENT_EFFECTS",
    "RECOMMENDATIONS",
    "REPORT_VERSION",
    "SOURCE_MANIFEST_VERSION",
    "VALIDATION_VERSION",
    "single_llm_response_format",
]
