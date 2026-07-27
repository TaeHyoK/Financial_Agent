"""Shared contracts for primary evidence and cross-domain context."""

from __future__ import annotations

import copy
import re
from typing import Any, Iterable, Mapping


EVIDENCE_ORIGIN_TYPES = frozenset({"raw_source", "deterministic_derived"})
SECONDARY_CONTEXT_EFFECTS = frozenset(
    {"corroborates", "contradicts", "neutral", "insufficient"}
)
SECONDARY_CONTEXT_USAGE = "framing_and_limitation_only"

_DOMAIN_PREFIXES = {
    "financial": "DART",
    "market": "YF",
    "news": "NEWS",
}
_FORBIDDEN_SOURCE_TOKENS = frozenset(
    {"summary", "direction", "stance", "interpretation", "reasoning", "cross_analysis"}
)


def canonical_evidence_id(domain: str, metric_or_key: str) -> str:
    """Return a readable evidence ID shared by all domain agents."""

    prefix = _DOMAIN_PREFIXES.get(domain)
    if prefix is None:
        raise ValueError(f"Unsupported evidence domain: {domain}")
    slug = re.sub(r"[^A-Za-z0-9]+", "_", str(metric_or_key)).strip("_").upper()
    if not slug:
        raise ValueError("Evidence metric/key cannot be empty.")
    return f"{prefix}_{slug}"


def validate_evidence_catalog(
    catalog: Mapping[str, Mapping[str, Any]],
    *,
    allowed_domains: Iterable[str] | None = None,
) -> None:
    """Reject narrative, generated, or malformed evidence entries."""

    allowed = set(allowed_domains or _DOMAIN_PREFIXES)
    for evidence_id, evidence in catalog.items():
        if evidence.get("evidence_id") != evidence_id:
            raise ValueError(f"Evidence ID mismatch: {evidence_id}")
        domain = str(evidence.get("domain") or "")
        if domain not in allowed:
            raise ValueError(f"Invalid evidence domain for {evidence_id}: {domain}")
        if evidence.get("origin_type") not in EVIDENCE_ORIGIN_TYPES:
            raise ValueError(f"Invalid evidence origin for {evidence_id}")
        source_ref = str(evidence.get("source_ref") or "")
        if not source_ref:
            raise ValueError(f"Missing source_ref for {evidence_id}")
        source_tokens = {token.lower() for token in source_ref.split(".")}
        if source_tokens & _FORBIDDEN_SOURCE_TOKENS:
            raise ValueError(f"Narrative field cannot be evidence: {source_ref}")


def validate_secondary_context_assessments(
    assessments: Any,
    *,
    primary_evidence_ids: Iterable[str],
    secondary_catalog: Mapping[str, Mapping[str, Any]],
    allowed_source_domains: Iterable[str],
    required_source_domains: Iterable[str] = (),
) -> list[dict[str, Any]]:
    """Validate that secondary context cannot become primary claim evidence."""

    if not isinstance(assessments, list):
        raise ValueError("secondary_context_assessment must be an array.")
    primary_ids = set(primary_evidence_ids)
    allowed_domains = set(allowed_source_domains)
    required_domains = set(required_source_domains)
    normalized: list[dict[str, Any]] = []
    seen_context_ids: set[str] = set()

    for raw in assessments:
        if not isinstance(raw, dict):
            raise ValueError("Each secondary context assessment must be an object.")
        context_id = str(raw.get("context_id") or "").strip()
        if not context_id or context_id in seen_context_ids:
            raise ValueError(f"Invalid or duplicate context_id: {context_id!r}")
        seen_context_ids.add(context_id)

        source_domain = str(raw.get("source_domain") or "")
        if source_domain not in allowed_domains:
            raise ValueError(f"Invalid context source domain for {context_id}: {source_domain}")
        effect = str(raw.get("effect") or "")
        if effect not in SECONDARY_CONTEXT_EFFECTS:
            raise ValueError(f"Invalid context effect for {context_id}: {effect}")
        if raw.get("usage") != SECONDARY_CONTEXT_USAGE:
            raise ValueError(f"Invalid context usage for {context_id}")

        primary_refs = _unique_strings(raw.get("primary_evidence_ids"))
        if not primary_refs or any(item not in primary_ids for item in primary_refs):
            raise ValueError(f"Invalid primary evidence reference for {context_id}")
        secondary_refs = _unique_strings(raw.get("secondary_evidence_ids"))
        if set(secondary_refs) & primary_ids:
            raise ValueError(f"Primary evidence reused as secondary context for {context_id}")
        for evidence_id in secondary_refs:
            evidence = secondary_catalog.get(evidence_id)
            if not isinstance(evidence, Mapping):
                raise ValueError(f"Unknown secondary evidence ID for {context_id}: {evidence_id}")
            if evidence.get("domain") != source_domain:
                raise ValueError(f"Secondary evidence domain mismatch for {context_id}: {evidence_id}")
        if effect != "insufficient" and not secondary_refs:
            raise ValueError(f"Context effect {effect} requires secondary evidence: {context_id}")

        statement = str(raw.get("statement") or "").strip()
        if not statement:
            raise ValueError(f"Missing context statement for {context_id}")
        normalized.append(
            {
                "context_id": context_id,
                "source_domain": source_domain,
                "effect": effect,
                "statement": statement,
                "primary_evidence_ids": primary_refs,
                "secondary_evidence_ids": secondary_refs,
                "usage": SECONDARY_CONTEXT_USAGE,
                "limitation": str(raw.get("limitation") or "").strip(),
            }
        )

    covered_domains = {item["source_domain"] for item in normalized}
    missing_domains = sorted(required_domains - covered_domains)
    if missing_domains:
        raise ValueError(
            "Missing secondary context assessment for available domains: "
            + ", ".join(missing_domains)
        )
    return copy.deepcopy(normalized)


def _unique_strings(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return list(dict.fromkeys(str(item) for item in value if str(item).strip()))
