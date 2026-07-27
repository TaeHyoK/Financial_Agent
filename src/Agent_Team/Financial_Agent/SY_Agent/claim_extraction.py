"""Read Financial Analyst claims and evidence without generating fallback prose."""

from __future__ import annotations

from typing import Any


def evidence_by_claim(evidence_items: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    mapping: dict[str, list[dict[str, Any]]] = {}
    for item in evidence_items:
        claim_id = str(item.get("claim_id") or "")
        if claim_id:
            mapping.setdefault(claim_id, []).append(item)
    return mapping


def get_claims(data: dict[str, Any]) -> list[dict[str, Any]]:
    handoff = data.get("sy_handoff") if isinstance(data.get("sy_handoff"), dict) else {}
    values = data.get("financial_claims") or handoff.get("financial_claims") or []
    return [
        {**item, "claim_origin": item.get("claim_origin") or "financial_analyst_handoff"}
        for item in values
        if isinstance(item, dict) and item.get("claim_id") and item.get("claim_ko")
    ]


def get_key_evidence(data: dict[str, Any]) -> list[dict[str, Any]]:
    handoff = data.get("sy_handoff") if isinstance(data.get("sy_handoff"), dict) else {}
    values = data.get("key_evidence") or handoff.get("key_evidence") or []
    return [
        {**item, "evidence_origin": item.get("evidence_origin") or "financial_analyst_handoff"}
        for item in values
        if isinstance(item, dict) and item.get("evidence_id") and item.get("claim_id")
    ]


def get_target_entity(data: dict[str, Any]) -> dict[str, Any]:
    if isinstance(data.get("target_entity"), dict):
        return data["target_entity"]
    return {
        "company_name": data.get("target_company", ""),
        "ticker": data.get("ticker", ""),
        "corp_code": data.get("corp_code", ""),
        "as_of_date": data.get("as_of_date", ""),
    }
