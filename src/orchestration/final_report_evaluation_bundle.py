"""Candidate-blind inputs for final-report pairwise evaluation."""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Any

from bs4 import BeautifulSoup, Tag

from shared.llm_clients import compact_json


VISIBLE_REPORT_VERSION = "visible_report_v1"
EVIDENCE_BUNDLE_VERSION = "candidate_neutral_evidence_v1"
EVIDENCE_UNION_VERSION = "candidate_neutral_evidence_union_v1"

_BLOCK_TAGS = {"p", "ul", "ol", "table", "dl"}
_OMITTED_EVIDENCE_KEYS = {
    "strategy_interpretation",
    "investment_effect",
    "materiality",
    "recommendation_bridge",
    "risk_summary",
    "strategy_risk_summary",
    "representative_excerpts",
    "article_text",
    "raw_articles",
    "source_path",
    "source_paths",
    "provenance",
    "execution_id",
    "cache_metadata",
    "model",
    "model_name",
}


def extract_visible_report(path: str | Path) -> dict[str, Any]:
    """Extract only user-visible headings, prose, lists, metadata, and tables."""

    source = Path(path).expanduser().resolve()
    html = source.read_text(encoding="utf-8")
    soup = BeautifulSoup(html, "lxml")
    for element in soup.find_all(["script", "style", "noscript", "template", "svg"]):
        element.decompose()
    for element in list(soup.find_all(True)):
        if _hidden(element):
            element.decompose()

    root = soup.select_one(".a4-sheet") or soup.body or soup
    report_title = _text(root.select_one(".report-name")) or _text(soup.title)
    metadata = _extract_metadata(root)
    sections: list[dict[str, Any]] = []
    for index, section in enumerate(root.select("section"), start=1):
        section_payload = _extract_section(section, index=index)
        if section_payload["blocks"]:
            sections.append(section_payload)

    if not sections:
        fallback = _extract_blocks(root)
        if fallback:
            sections.append(
                {
                    "section_id": "document",
                    "heading": report_title or "보고서",
                    "blocks": fallback,
                }
            )

    visible = {
        "version": VISIBLE_REPORT_VERSION,
        "title": report_title,
        "metadata": metadata,
        "sections": sections,
    }
    return visible


def build_common_evidence_bundle(packet_path: str | Path) -> dict[str, Any]:
    """Build a candidate-neutral Judge bundle from Full's compact packet."""

    source = Path(packet_path).expanduser().resolve()
    packet = json.loads(source.read_text(encoding="utf-8"))
    if not isinstance(packet, dict):
        raise ValueError(f"Strategy packet must be a JSON object: {source}")
    cards_payload = packet.get("cards")
    if not isinstance(cards_payload, dict) or not cards_payload:
        raise ValueError(f"Strategy packet has no evidence cards: {source}")

    cards: list[dict[str, Any]] = []
    for card_key, raw_card in sorted(cards_payload.items()):
        if not isinstance(raw_card, dict):
            continue
        card = {
            "card_key": str(card_key),
            "domain": raw_card.get("domain"),
            "label": raw_card.get("label"),
            "evidence_family": raw_card.get("evidence_family"),
            "observation_basis": raw_card.get("observation_basis"),
            "comparison_scope": raw_card.get("comparison_scope"),
            "decision_use": raw_card.get("decision_use"),
            "primary_observation": raw_card.get("primary_observation"),
            "reader_observation": raw_card.get("reader_observation"),
            "reader_limitations": raw_card.get("reader_limitations") or [],
        }
        cards.append(_sanitize_evidence(card))

    target = packet.get("target_company") if isinstance(packet.get("target_company"), dict) else {}
    bundle = {
        "version": EVIDENCE_BUNDLE_VERSION,
        "target_company": {
            key: target.get(key)
            for key in ("company_name", "run_key", "as_of_date", "ticker", "corp_code")
            if target.get(key) not in (None, "")
        },
        "selected_date_policy": packet.get("selected_date_policy"),
        "coverage_summary": _sanitize_evidence(packet.get("coverage_summary") or {}),
        "cards": cards,
        "reader_limitations": _sanitize_evidence(packet.get("reader_limitations") or []),
        "limitation_requirements": _sanitize_evidence(
            packet.get("limitation_requirements") or []
        ),
    }
    bundle["source_packet_sha256"] = file_sha256(source)
    neutral_content = {key: value for key, value in bundle.items() if key != "bundle_sha256"}
    bundle["bundle_sha256"] = hashlib.sha256(
        compact_json(neutral_content, sort_keys=True).encode("utf-8")
    ).hexdigest()
    assert_candidate_neutral(bundle)
    return bundle


def build_union_evidence_bundle(packet_paths: list[str | Path]) -> dict[str, Any]:
    """Build an order-independent union of candidate-neutral packet observations."""

    resolved_paths = [Path(path).expanduser().resolve() for path in packet_paths]
    if not resolved_paths:
        raise ValueError("At least one Strategy packet is required for an evidence union.")
    bundles = [build_common_evidence_bundle(path) for path in resolved_paths]
    targets = [bundle.get("target_company") or {} for bundle in bundles]
    identities = {
        (
            str(target.get("company_name") or ""),
            str(target.get("as_of_date") or ""),
            str(target.get("ticker") or ""),
        )
        for target in targets
    }
    if len(identities) != 1:
        raise ValueError(f"Evidence packets refer to different targets or dates: {identities}")

    variants_by_key: dict[str, dict[str, dict[str, Any]]] = {}
    for bundle in bundles:
        for card in bundle.get("cards") or []:
            if not isinstance(card, dict) or not card.get("card_key"):
                continue
            card_key = str(card["card_key"])
            signature = hashlib.sha256(
                compact_json(card, sort_keys=True).encode("utf-8")
            ).hexdigest()
            variants_by_key.setdefault(card_key, {})[signature] = card

    cards: list[dict[str, Any]] = []
    for card_key in sorted(variants_by_key):
        variants = [variants_by_key[card_key][key] for key in sorted(variants_by_key[card_key])]
        if len(variants) == 1:
            cards.append(variants[0])
            continue
        variant_payloads = [
            {key: value for key, value in variant.items() if key != "card_key"}
            for variant in variants
        ]
        cards.append(
            {
                "card_key": card_key,
                "domain": _common_or_values(variants, "domain"),
                "label": _common_or_values(variants, "label"),
                "candidate_neutral_observation_variants": variant_payloads,
            }
        )

    policies = sorted(
        {
            str(bundle.get("selected_date_policy") or "")
            for bundle in bundles
            if bundle.get("selected_date_policy")
        }
    )
    reader_limitations = _unique_json_items(
        item for bundle in bundles for item in bundle.get("reader_limitations") or []
    )
    limitation_requirements = _unique_json_items(
        item for bundle in bundles for item in bundle.get("limitation_requirements") or []
    )
    card_counts: dict[str, int] = {}
    for card in cards:
        domain = card.get("domain")
        domain_label = str(domain if not isinstance(domain, list) else "mixed")
        card_counts[domain_label] = card_counts.get(domain_label, 0) + 1
    bundle = {
        "version": EVIDENCE_UNION_VERSION,
        "target_company": targets[0],
        "selected_date_policy": policies[0] if len(policies) == 1 else policies,
        "coverage_summary": {
            "union_card_count": len(cards),
            "union_card_counts_by_domain": dict(sorted(card_counts.items())),
        },
        "cards": cards,
        "reader_limitations": reader_limitations,
        "limitation_requirements": limitation_requirements,
        "source_packet_sha256s": sorted(file_sha256(path) for path in resolved_paths),
    }
    neutral_content = {key: value for key, value in bundle.items() if key != "bundle_sha256"}
    bundle["bundle_sha256"] = hashlib.sha256(
        compact_json(neutral_content, sort_keys=True).encode("utf-8")
    ).hexdigest()
    assert_candidate_neutral(bundle)
    return bundle


def assert_candidate_neutral(payload: Any) -> None:
    """Reject fields that can reveal candidate generation choices to the Judge."""

    violations: list[str] = []

    def visit(value: Any, path: str) -> None:
        if isinstance(value, dict):
            for key, item in value.items():
                normalized = str(key).lower().lstrip("_")
                if normalized in _OMITTED_EVIDENCE_KEYS:
                    violations.append(f"{path}.{key}")
                visit(item, f"{path}.{key}")
        elif isinstance(value, list):
            for index, item in enumerate(value):
                visit(item, f"{path}[{index}]")

    visit(payload, "$bundle")
    if violations:
        raise ValueError(f"Candidate-specific fields leaked into evidence bundle: {violations}")


def file_sha256(path: str | Path) -> str:
    resolved = Path(path).expanduser().resolve()
    digest = hashlib.sha256()
    with resolved.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _extract_metadata(root: Tag) -> list[dict[str, str]]:
    metadata: list[dict[str, str]] = []
    for item in root.select(".meta-grid > div"):
        label_element = item.find(["span", "dt"])
        label = _text(label_element)
        value = _text(item)
        if label and value.startswith(label):
            value = value[len(label) :].lstrip(":： ")
        if label or value:
            metadata.append({"label": label, "value": value})
    return metadata


def _extract_section(section: Tag, *, index: int) -> dict[str, Any]:
    heading_element = section.find(["h1", "h2", "h3"])
    heading = _text(heading_element)
    section_id = str(section.get("id") or f"section_{index}")
    return {
        "section_id": section_id,
        "heading": heading,
        "blocks": _extract_blocks(section),
    }


def _extract_blocks(container: Tag) -> list[dict[str, Any]]:
    blocks: list[dict[str, Any]] = []
    heading = container.find(["h1", "h2", "h3"])
    for element in container.find_all(list(_BLOCK_TAGS)):
        if element.find_parent("section") is not container and container.name == "section":
            continue
        if any(parent.name in _BLOCK_TAGS for parent in element.parents if parent is not container):
            continue
        if element.name == "p":
            text = _text(element)
            if text and element is not heading:
                blocks.append({"type": "paragraph", "text": text})
        elif element.name in {"ul", "ol"}:
            items = [_text(item) for item in element.find_all("li", recursive=False)]
            items = [item for item in items if item]
            if items:
                blocks.append({"type": "list", "items": items})
        elif element.name == "table":
            table = _extract_table(element)
            if table["headers"] or table["rows"]:
                blocks.append(table)
        elif element.name == "dl":
            rows: list[list[str]] = []
            for term in element.find_all("dt", recursive=False):
                description = term.find_next_sibling("dd")
                rows.append([_text(term), _text(description)])
            if rows:
                blocks.append({"type": "definition_list", "rows": rows})
    return blocks


def _extract_table(table: Tag) -> dict[str, Any]:
    headers = [_text(cell) for cell in table.select("thead th")]
    rows: list[list[str]] = []
    body_rows = table.select("tbody tr") or table.find_all("tr")
    for row in body_rows:
        cells = row.find_all(["th", "td"], recursive=False)
        values = [_text(cell) for cell in cells]
        if values and (not headers or values != headers):
            rows.append(values)
    return {"type": "table", "headers": headers, "rows": rows}


def _hidden(element: Tag) -> bool:
    if element.has_attr("hidden") or str(element.get("aria-hidden") or "").lower() == "true":
        return True
    style = re.sub(r"\s+", "", str(element.get("style") or "").lower())
    return "display:none" in style or "visibility:hidden" in style


def _text(element: Any) -> str:
    if element is None or not hasattr(element, "get_text"):
        return ""
    return " ".join(str(element.get_text(" ", strip=True)).split())


def _sanitize_evidence(value: Any) -> Any:
    if isinstance(value, dict):
        sanitized: dict[str, Any] = {}
        for key, item in value.items():
            normalized = str(key).lower().lstrip("_")
            if normalized in _OMITTED_EVIDENCE_KEYS:
                continue
            sanitized[str(key)] = _sanitize_evidence(item)
        return sanitized
    if isinstance(value, list):
        return [_sanitize_evidence(item) for item in value]
    return value


def _common_or_values(items: list[dict[str, Any]], key: str) -> Any:
    values = _unique_json_items(item.get(key) for item in items)
    return values[0] if len(values) == 1 else values


def _unique_json_items(items: Any) -> list[Any]:
    unique: dict[str, Any] = {}
    for item in items:
        signature = compact_json(item, sort_keys=True)
        unique[signature] = item
    return [unique[key] for key in sorted(unique)]


__all__ = [
    "EVIDENCE_BUNDLE_VERSION",
    "VISIBLE_REPORT_VERSION",
    "assert_candidate_neutral",
    "build_common_evidence_bundle",
    "build_union_evidence_bundle",
    "extract_visible_report",
    "file_sha256",
]
