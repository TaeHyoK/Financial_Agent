#!/usr/bin/env python3
"""Repair deterministic Writer financial displays while reusing saved LLM prose."""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


REPO_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = REPO_ROOT / "src"
WRITER_ROOT = SRC_ROOT / "Agent_Team" / "Writer Agent"
for import_root in (SRC_ROOT, WRITER_ROOT):
    if str(import_root) not in sys.path:
        sys.path.insert(0, str(import_root))

from formatted_html_renderer import render_formatted_html_report
from html_report_validator import validate_html_report
from html_report_writer import normalize_report_payload
from shared.evidence_cards import card_content_sha256
from writer_handoff import (
    reformat_financial_reader_observations,
    validate_writer_editorial_packet,
)
from writer_io import load_json, save_json


REPAIR_VERSION = "writer_financial_unit_repair_v1"
REQUIRED_FILES = (
    "writer_editorial_packet_v2.json",
    "writer_packet_provenance_v2.json",
    "llm_writer_output.json",
    "writer_report_payload.json",
    "report.html",
)


def repair_writer_directory(
    writer_dir: Path,
    *,
    source_unit: str,
    text_replacements: dict[str, str] | None = None,
) -> dict[str, Any]:
    writer_dir = writer_dir.expanduser().resolve()
    missing = [name for name in REQUIRED_FILES if not (writer_dir / name).is_file()]
    if missing:
        raise FileNotFoundError(f"Missing Writer artifacts in {writer_dir}: {missing}")

    editorial_path = writer_dir / "writer_editorial_packet_v2.json"
    provenance_path = writer_dir / "writer_packet_provenance_v2.json"
    llm_output_path = writer_dir / "llm_writer_output.json"
    payload_path = writer_dir / "writer_report_payload.json"
    report_path = writer_dir / "report.html"
    validation_path = writer_dir / "writer_validation_report.json"

    before_hashes = {
        "writer_editorial_packet_v2.json": _sha256(editorial_path),
        "writer_report_payload.json": _sha256(payload_path),
        "report.html": _sha256(report_path),
        "llm_writer_output.json": _sha256(llm_output_path),
    }
    before_payload = load_json(payload_path, "Writer report payload")
    before_metadata = dict(before_payload.get("metadata") or {})

    packet = load_json(editorial_path, "Writer editorial packet")
    repaired_packet = reformat_financial_reader_observations(
        packet,
        source_unit=source_unit,
    )
    repaired_packet, replacement_counts = _replace_text_recursive(
        repaired_packet,
        text_replacements or {},
    )
    validate_writer_editorial_packet(repaired_packet)

    provenance = load_json(provenance_path, "Writer packet provenance")
    provenance_cards = provenance.get("cards") or {}
    for card_key, card in (repaired_packet.get("cards") or {}).items():
        if card_key in provenance_cards and isinstance(provenance_cards[card_key], dict):
            provenance_cards[card_key]["writer_editorial_card_sha256"] = card_content_sha256(card)

    llm_output = load_json(llm_output_path, "Writer LLM output")
    raw_payload = llm_output.get("raw_payload")
    if not isinstance(raw_payload, dict):
        raise ValueError(f"Saved LLM output has no reusable raw_payload: {llm_output_path}")
    if "억원" in json.dumps(raw_payload, ensure_ascii=False):
        raise ValueError(
            f"Saved LLM prose contains currency amounts and cannot be repaired deterministically: {llm_output_path}"
        )

    writer_mode = str(llm_output.get("writer_mode") or "deterministic")
    repaired_payload = normalize_report_payload(
        raw_payload,
        writer_handoff=repaired_packet,
        writer_mode=writer_mode,
    )
    after_metadata = dict(repaired_payload.get("metadata") or {})
    for key in ("company_name", "base_date", "recommendation", "investment_horizon"):
        if before_metadata.get(key) != after_metadata.get(key):
            raise ValueError(
                f"Deterministic unit repair changed metadata.{key}: "
                f"{before_metadata.get(key)!r} -> {after_metadata.get(key)!r}"
            )

    save_json(editorial_path, repaired_packet)
    save_json(provenance_path, provenance)
    save_json(payload_path, repaired_payload)
    render_result = render_formatted_html_report(repaired_payload, writer_dir)
    validation = validate_html_report(
        report_payload=repaired_payload,
        html_content=render_result["html_content"],
        writer_handoff=repaired_packet,
    )
    if validation.get("status") != "pass":
        raise ValueError(
            f"Repaired report failed Writer validation: {writer_dir}: "
            f"{validation.get('blocking_failures') or []}"
        )
    save_json(validation_path, validation)

    after_hashes = {
        "writer_editorial_packet_v2.json": _sha256(editorial_path),
        "writer_report_payload.json": _sha256(payload_path),
        "report.html": _sha256(report_path),
        "llm_writer_output.json": _sha256(llm_output_path),
    }
    manifest = {
        "repair_version": REPAIR_VERSION,
        "repaired_at_utc": datetime.now(timezone.utc).isoformat(),
        "writer_dir": str(writer_dir),
        "source_unit": source_unit,
        "target_display_unit": "억원",
        "text_replacement_counts": replacement_counts,
        "llm_called": False,
        "saved_llm_raw_payload_reused": True,
        "llm_output_unchanged": before_hashes["llm_writer_output.json"]
        == after_hashes["llm_writer_output.json"],
        "recommendation_unchanged": before_metadata.get("recommendation")
        == after_metadata.get("recommendation"),
        "before_sha256": before_hashes,
        "after_sha256": after_hashes,
        "validation_status": validation.get("status"),
    }
    save_json(writer_dir / "writer_unit_repair_v1.json", manifest)
    return manifest


def discover_writer_directories(suite_root: Path) -> list[Path]:
    return sorted(
        path.parent
        for path in suite_root.expanduser().resolve().glob(
            "conditions/*/replicate_*/**/Writer/*/writer_editorial_packet_v2.json"
        )
    )


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as file:
        for chunk in iter(lambda: file.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _replace_text_recursive(
    value: Any,
    replacements: dict[str, str],
) -> tuple[Any, dict[str, int]]:
    counts = {source: 0 for source in replacements}

    def visit(item: Any) -> Any:
        if isinstance(item, dict):
            return {key: visit(child) for key, child in item.items()}
        if isinstance(item, list):
            return [visit(child) for child in item]
        if not isinstance(item, str):
            return item
        text = item
        for source, target in replacements.items():
            occurrences = text.count(source)
            if occurrences:
                counts[source] += occurrences
                text = text.replace(source, target)
        return text

    return visit(value), counts


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--suite-root", type=Path, required=True)
    parser.add_argument(
        "--source-unit",
        required=True,
        choices=("원", "천원", "백만원", "억원", "KRW", "100m_KRW"),
    )
    parser.add_argument(
        "--replace-text",
        action="append",
        default=[],
        metavar="OLD=NEW",
        help="Deterministically replace a verified amount phrase in the Writer packet.",
    )
    return parser.parse_args()


def main() -> int:
    args = _parse_args()
    replacements: dict[str, str] = {}
    for raw in args.replace_text:
        if "=" not in raw:
            raise ValueError(f"--replace-text must use OLD=NEW: {raw!r}")
        source, target = raw.split("=", 1)
        if not source or not target:
            raise ValueError(f"--replace-text must have non-empty OLD and NEW: {raw!r}")
        replacements[source] = target
    writer_dirs = discover_writer_directories(args.suite_root)
    if not writer_dirs:
        raise FileNotFoundError(f"No Writer artifacts found below {args.suite_root}")

    manifests = [
        repair_writer_directory(
            writer_dir,
            source_unit=args.source_unit,
            text_replacements=replacements,
        )
        for writer_dir in writer_dirs
    ]
    print(
        json.dumps(
            {
                "suite_root": str(args.suite_root.expanduser().resolve()),
                "source_unit": args.source_unit,
                "repaired_reports": len(manifests),
                "validation_passed": sum(
                    item.get("validation_status") == "pass" for item in manifests
                ),
                "text_replacements": {
                    source: sum(
                        int((item.get("text_replacement_counts") or {}).get(source) or 0)
                        for item in manifests
                    )
                    for source in replacements
                },
                "llm_calls": 0,
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
