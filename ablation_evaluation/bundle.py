"""Candidate-blind report extraction and candidate-neutral evidence bundles."""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Any, Iterable

from bs4 import BeautifulSoup, Tag

from ablation_suite.recommendations import read_explicit_rating


VISIBLE_REPORT_VERSION = "visible_report_v2_explicit_rating"
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
_FORBIDDEN_CONDITION_TOKENS = {
    "ablation",
    "no_peer",
    "no_subdata",
    "unified_domain_team",
    "random_news",
}


def compact_json(value: Any, *, sort_keys: bool = False) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        allow_nan=False,
        separators=(",", ":"),
        sort_keys=sort_keys,
    )


def file_sha256(path: str | Path) -> str:
    digest = hashlib.sha256()
    with Path(path).expanduser().resolve().open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def payload_sha256(payload: Any) -> str:
    return hashlib.sha256(compact_json(payload, sort_keys=True).encode("utf-8")).hexdigest()


def extract_visible_report(path: str | Path) -> dict[str, Any]:
    """Extract only content a reader can see in the final HTML report."""

    source = Path(path).expanduser().resolve()
    soup = BeautifulSoup(source.read_text(encoding="utf-8"), "lxml")
    for element in soup.find_all(["script", "style", "noscript", "template", "svg"]):
        element.decompose()
    for element in list(soup.find_all(True)):
        if _hidden(element):
            element.decompose()

    root = soup.select_one(".a4-sheet") or soup.body or soup
    title = _text(root.select_one(".report-name")) or _text(soup.title)
    sections: list[dict[str, Any]] = []
    for index, section in enumerate(root.select("section"), start=1):
        blocks = _extract_blocks(section)
        if blocks:
            sections.append(
                {
                    "section_id": str(section.get("id") or f"section_{index}"),
                    "heading": _text(section.find(["h1", "h2", "h3"])),
                    "blocks": blocks,
                }
            )
    if not sections:
        blocks = _extract_blocks(root)
        if blocks:
            sections.append(
                {"section_id": "document", "heading": title or "보고서", "blocks": blocks}
            )
    result = {
        "version": VISIBLE_REPORT_VERSION,
        "title": title,
        "metadata": _extract_metadata(root),
        "sections": sections,
    }
    rating = read_explicit_rating(source)
    if rating["present"]:
        result["declared_recommendation"] = rating["label"]
    return result


def build_evidence_bundle(packet_path: str | Path) -> dict[str, Any]:
    """Convert a Strategy compact packet into a candidate-neutral fact bundle."""

    source = Path(packet_path).expanduser().resolve()
    packet = json.loads(source.read_text(encoding="utf-8"))
    if not isinstance(packet, dict):
        raise ValueError(f"Strategy packet must be an object: {source}")
    cards_payload = packet.get("cards")
    if not isinstance(cards_payload, dict) or not cards_payload:
        raise ValueError(f"Strategy packet has no evidence cards: {source}")
    cards: list[dict[str, Any]] = []
    for card_key, raw in sorted(cards_payload.items()):
        if not isinstance(raw, dict):
            continue
        cards.append(
            _sanitize(
                {
                    "card_key": str(card_key),
                    "domain": raw.get("domain"),
                    "label": raw.get("label"),
                    "evidence_family": raw.get("evidence_family"),
                    "observation_basis": raw.get("observation_basis"),
                    "comparison_scope": raw.get("comparison_scope"),
                    "decision_use": raw.get("decision_use"),
                    "primary_observation": raw.get("primary_observation"),
                    "reader_observation": raw.get("reader_observation"),
                    "reader_limitations": raw.get("reader_limitations") or [],
                }
            )
        )
    target = packet.get("target_company") if isinstance(packet.get("target_company"), dict) else {}
    bundle = {
        "version": EVIDENCE_BUNDLE_VERSION,
        "target_company": {
            key: target.get(key)
            for key in ("company_name", "run_key", "as_of_date", "ticker", "corp_code")
            if target.get(key) not in (None, "")
        },
        "selected_date_policy": packet.get("selected_date_policy"),
        "coverage_summary": {
            "card_count": len(cards),
            "card_counts_by_domain": _card_counts(cards),
        },
        "cards": cards,
        "reader_limitations": _neutral_items(packet.get("reader_limitations") or []),
        "limitation_requirements": _neutral_items(
            packet.get("limitation_requirements") or []
        ),
        "source_packet_sha256": file_sha256(source),
    }
    bundle["bundle_sha256"] = payload_sha256(bundle)
    assert_candidate_neutral(bundle)
    return bundle


def build_union_evidence_bundle(packet_paths: Iterable[str | Path]) -> dict[str, Any]:
    """Build an order-independent union used only to fact-check both candidates."""

    sources = [Path(path).expanduser().resolve() for path in packet_paths]
    if not sources:
        raise ValueError("At least one Strategy packet is required.")
    bundles = [build_evidence_bundle(path) for path in sources]
    identities = {
        (
            str((bundle.get("target_company") or {}).get("company_name") or ""),
            str((bundle.get("target_company") or {}).get("as_of_date") or ""),
            str((bundle.get("target_company") or {}).get("ticker") or ""),
        )
        for bundle in bundles
    }
    if len(identities) != 1:
        raise ValueError(f"Evidence packets refer to different targets/dates: {identities}")

    variants_by_key: dict[str, dict[str, dict[str, Any]]] = {}
    for bundle in bundles:
        for card in bundle.get("cards") or []:
            if not isinstance(card, dict) or not card.get("card_key"):
                continue
            key = str(card["card_key"])
            variants_by_key.setdefault(key, {})[payload_sha256(card)] = card

    cards: list[dict[str, Any]] = []
    for key in sorted(variants_by_key):
        variants = [variants_by_key[key][signature] for signature in sorted(variants_by_key[key])]
        if len(variants) == 1:
            cards.append(variants[0])
        else:
            cards.append(
                {
                    "card_key": key,
                    "domain": _common_or_values(variants, "domain"),
                    "label": _common_or_values(variants, "label"),
                    "candidate_neutral_observation_variants": [
                        {name: value for name, value in variant.items() if name != "card_key"}
                        for variant in variants
                    ],
                }
            )
    card_counts = _card_counts(cards)
    policies = sorted(
        {
            str(bundle.get("selected_date_policy") or "")
            for bundle in bundles
            if bundle.get("selected_date_policy")
        }
    )
    union = {
        "version": EVIDENCE_UNION_VERSION,
        "target_company": bundles[0].get("target_company") or {},
        "selected_date_policy": policies[0] if len(policies) == 1 else policies,
        "coverage_summary": {
            "union_card_count": len(cards),
            "union_card_counts_by_domain": dict(sorted(card_counts.items())),
        },
        "cards": cards,
        "reader_limitations": _intersection_json(
            [bundle.get("reader_limitations") or [] for bundle in bundles]
        ),
        "limitation_requirements": _intersection_json(
            [bundle.get("limitation_requirements") or [] for bundle in bundles]
        ),
        "source_packet_sha256s": sorted(file_sha256(path) for path in sources),
    }
    union["bundle_sha256"] = payload_sha256(union)
    assert_candidate_neutral(union)
    return union


def assert_candidate_neutral(payload: Any) -> None:
    violations: list[str] = []

    def visit(value: Any, path: str) -> None:
        if isinstance(value, dict):
            for key, item in value.items():
                if str(key).lower().lstrip("_") in _OMITTED_EVIDENCE_KEYS:
                    violations.append(f"{path}.{key}")
                visit(item, f"{path}.{key}")
        elif isinstance(value, list):
            for index, item in enumerate(value):
                visit(item, f"{path}[{index}]")
        elif isinstance(value, str):
            lowered = value.lower()
            for token in _FORBIDDEN_CONDITION_TOKENS:
                if token in lowered:
                    violations.append(f"{path}:condition-token:{token}")

    visit(payload, "$bundle")
    if violations:
        raise ValueError(f"Candidate-specific fields leaked into evidence bundle: {violations}")


def _extract_metadata(root: Tag) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    for item in root.select(".meta-grid > div"):
        label = _text(item.find(["span", "dt"]))
        value = _text(item)
        if label and value.startswith(label):
            value = value[len(label) :].lstrip(":： ")
        if label or value:
            rows.append({"label": label, "value": value})
    return rows


def _extract_blocks(container: Tag) -> list[dict[str, Any]]:
    blocks: list[dict[str, Any]] = []
    for element in container.find_all(list(_BLOCK_TAGS)):
        if container.name == "section" and element.find_parent("section") is not container:
            continue
        if any(parent.name in _BLOCK_TAGS for parent in element.parents if parent is not container):
            continue
        if element.name == "p":
            text = _text(element)
            if text:
                blocks.append({"type": "paragraph", "text": text})
        elif element.name in {"ul", "ol"}:
            items = [_text(item) for item in element.find_all("li", recursive=False)]
            if any(items):
                blocks.append({"type": "list", "items": [item for item in items if item]})
        elif element.name == "table":
            headers = [_text(cell) for cell in element.select("thead th")]
            rows = []
            for row in element.select("tbody tr") or element.find_all("tr"):
                values = [_text(cell) for cell in row.find_all(["th", "td"], recursive=False)]
                if values and values != headers:
                    rows.append(values)
            if headers or rows:
                blocks.append({"type": "table", "headers": headers, "rows": rows})
        elif element.name == "dl":
            rows = []
            for term in element.find_all("dt", recursive=False):
                rows.append([_text(term), _text(term.find_next_sibling("dd"))])
            if rows:
                blocks.append({"type": "definition_list", "rows": rows})
    return blocks


def _hidden(element: Tag) -> bool:
    if element.has_attr("hidden") or str(element.get("aria-hidden") or "").lower() == "true":
        return True
    style = re.sub(r"\s+", "", str(element.get("style") or "").lower())
    return "display:none" in style or "visibility:hidden" in style


def _text(element: Any) -> str:
    if element is None or not hasattr(element, "get_text"):
        return ""
    return " ".join(str(element.get_text(" ", strip=True)).split())


def _sanitize(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            str(key): _sanitize(item)
            for key, item in value.items()
            if str(key).lower().lstrip("_") not in _OMITTED_EVIDENCE_KEYS
        }
    if isinstance(value, list):
        return [_sanitize(item) for item in value]
    return value


def _unique_json(items: Iterable[Any]) -> list[Any]:
    unique = {compact_json(item, sort_keys=True): item for item in items}
    return [unique[key] for key in sorted(unique)]


def _intersection_json(groups: list[list[Any]]) -> list[Any]:
    if not groups:
        return []
    signatures = [
        {compact_json(item, sort_keys=True): item for item in group}
        for group in groups
    ]
    common = set(signatures[0])
    for group in signatures[1:]:
        common &= set(group)
    return [signatures[0][signature] for signature in sorted(common)]


def _neutral_items(items: Any) -> list[Any]:
    if not isinstance(items, list):
        return []
    output = []
    for item in items:
        sanitized = _sanitize(item)
        serialized = compact_json(sanitized, sort_keys=True).lower()
        if any(token in serialized for token in _FORBIDDEN_CONDITION_TOKENS):
            continue
        output.append(sanitized)
    return output


def _card_counts(cards: list[dict[str, Any]]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for card in cards:
        domain = card.get("domain")
        label = str(domain if not isinstance(domain, list) else "mixed")
        counts[label] = counts.get(label, 0) + 1
    return dict(sorted(counts.items()))


def _common_or_values(items: list[dict[str, Any]], key: str) -> Any:
    values = _unique_json(item.get(key) for item in items)
    return values[0] if len(values) == 1 else values


__all__ = [
    "assert_candidate_neutral",
    "build_evidence_bundle",
    "build_union_evidence_bundle",
    "compact_json",
    "extract_visible_report",
    "file_sha256",
    "payload_sha256",
]
