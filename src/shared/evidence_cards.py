"""Shared integrity helpers for self-contained Strategy and Writer cards."""

from __future__ import annotations

import hashlib
import re
from collections.abc import Iterable, Mapping
from typing import Any

from .llm_clients import compact_json


CARD_KEY_PATTERN = re.compile(r"^[^\s.]+(?:\.[^\s.]+)+$")
RAW_EVIDENCE_ID_PATTERN = re.compile(
    r"^(?:E\d{3,}|F\d{3,}|NCLAIM_\d+|NEWS_RAW_|OP\d+|PEER_METRIC_\d+|LIMIT_\d+)",
    flags=re.IGNORECASE,
)
INTERNAL_READER_FIELD_PATTERN = re.compile(
    r"(?<![A-Za-z0-9_])(?:"
    r"recommendation_bridge|evidence_assessments|"
    r"card_keys?|_card_keys?|[A-Za-z][A-Za-z0-9_]*_card_keys?"
    r")(?![A-Za-z0-9_])",
    flags=re.IGNORECASE,
)
EVIDENCE_ROLES = frozenset({"primary", "reference"})
ELIGIBILITY_VALUES = frozenset({"eligible", "reference_only", "incomparable"})
SECONDARY_CONTEXT_USAGE = "framing_only"
PRODUCT_DISCLOSURE_SCOPE_LABEL = "주요 제품·서비스 공시표 기준"


def card_content_sha256(card: Mapping[str, Any]) -> str:
    """Return the canonical hash used by provenance validators."""

    return hashlib.sha256(compact_json(card, sort_keys=True).encode("utf-8")).hexdigest()


def validate_card_key(card_key: Any) -> str:
    """Validate and return one semantic, LLM-local card key."""

    value = str(card_key or "").strip()
    if not value or not CARD_KEY_PATTERN.fullmatch(value):
        raise ValueError(f"Invalid semantic card_key: {value!r}")
    if RAW_EVIDENCE_ID_PATTERN.match(value):
        raise ValueError(f"Opaque evidence ID cannot be a card_key: {value}")
    return value


def validate_self_contained_card(
    card: Mapping[str, Any],
    *,
    allowed_section_names: Iterable[str],
) -> None:
    """Validate the common card envelope without interpreting its prose."""

    if not isinstance(card, Mapping):
        raise ValueError("Evidence card must be an object.")
    card_key = validate_card_key(card.get("card_key"))
    role = str(card.get("evidence_role") or "")
    if role not in EVIDENCE_ROLES:
        raise ValueError(f"Invalid evidence_role for {card_key}: {role}")
    eligibility = str(card.get("eligibility") or "")
    if eligibility not in ELIGIBILITY_VALUES:
        raise ValueError(f"Invalid eligibility for {card_key}: {eligibility}")
    observation = card.get("primary_observation")
    if not isinstance(observation, Mapping) or not observation:
        raise ValueError(f"primary_observation is required for {card_key}")

    allowed = list(dict.fromkeys(str(value) for value in card.get("allowed_sections") or []))
    known_sections = set(allowed_section_names)
    if not allowed or any(value not in known_sections for value in allowed):
        raise ValueError(f"Invalid allowed_sections for {card_key}: {allowed}")

    for context in card.get("secondary_context") or []:
        if not isinstance(context, Mapping):
            raise ValueError(f"secondary_context must contain objects for {card_key}")
        if context.get("usage") != SECONDARY_CONTEXT_USAGE:
            raise ValueError(f"Invalid secondary context usage for {card_key}")
        if not str(context.get("statement") or "").strip():
            raise ValueError(f"Secondary context statement is required for {card_key}")

    for key in ("reader_limitations", "machine_blockers"):
        if not isinstance(card.get(key, []), list):
            raise ValueError(f"{key} must be a list for {card_key}")
    assert_no_opaque_ids(card, location=f"cards.{card_key}")


def validate_provenance_map(
    cards: Mapping[str, Mapping[str, Any]],
    provenance: Mapping[str, Any],
) -> None:
    """Require exact card-key coverage and hashes in an external provenance map."""

    entries = provenance.get("cards") if isinstance(provenance, Mapping) else None
    if not isinstance(entries, Mapping):
        raise ValueError("provenance.cards must be an object.")
    if set(entries) != set(cards):
        missing = sorted(set(cards) - set(entries))
        extra = sorted(set(entries) - set(cards))
        raise ValueError(f"Provenance card coverage mismatch: missing={missing}, extra={extra}")
    for card_key, card in cards.items():
        entry = entries.get(card_key)
        if not isinstance(entry, Mapping):
            raise ValueError(f"Missing provenance object for {card_key}")
        expected = card_content_sha256(card)
        if entry.get("strategy_card_sha256") != expected:
            raise ValueError(f"Strategy card hash mismatch for {card_key}")
        if not isinstance(entry.get("source_evidence_ids", []), list):
            raise ValueError(f"source_evidence_ids must be a list for {card_key}")
        if not isinstance(entry.get("source_paths", []), list):
            raise ValueError(f"source_paths must be a list for {card_key}")


def assert_no_opaque_ids(value: Any, *, location: str = "payload") -> None:
    """Reject raw evidence handles from an LLM-facing payload."""

    for path, item in _walk_strings(value, location):
        if RAW_EVIDENCE_ID_PATTERN.match(item.strip()):
            raise ValueError(f"Opaque evidence ID leaked into {path}: {item}")


def assert_no_internal_references_in_reader_text(
    value: Any,
    *,
    card_keys: Iterable[str],
    location: str = "reader_text",
) -> None:
    """Reject schema field names and semantic card keys from reader-facing prose."""

    known_card_keys = tuple(
        sorted(
            {str(card_key).strip() for card_key in card_keys if str(card_key).strip()},
            key=len,
            reverse=True,
        )
    )
    for path, item in _walk_strings(value, location):
        leaked_fields = sorted(set(INTERNAL_READER_FIELD_PATTERN.findall(item)))
        leaked_card_keys = [card_key for card_key in known_card_keys if card_key in item]
        if leaked_fields or leaked_card_keys:
            details = []
            if leaked_fields:
                details.append(f"schema_fields={leaked_fields}")
            if leaked_card_keys:
                details.append(f"card_keys={leaked_card_keys}")
            raise ValueError(
                f"Internal metadata leaked into reader-facing text at {path}: "
                + ", ".join(details)
            )


def _walk_strings(value: Any, path: str) -> Iterable[tuple[str, str]]:
    if isinstance(value, str):
        yield path, value
    elif isinstance(value, Mapping):
        for key, child in value.items():
            yield from _walk_strings(child, f"{path}.{key}")
    elif isinstance(value, list):
        for index, child in enumerate(value):
            yield from _walk_strings(child, f"{path}[{index}]")


__all__ = [
    "ELIGIBILITY_VALUES",
    "EVIDENCE_ROLES",
    "SECONDARY_CONTEXT_USAGE",
    "assert_no_internal_references_in_reader_text",
    "assert_no_opaque_ids",
    "card_content_sha256",
    "validate_card_key",
    "validate_provenance_map",
    "validate_self_contained_card",
]
