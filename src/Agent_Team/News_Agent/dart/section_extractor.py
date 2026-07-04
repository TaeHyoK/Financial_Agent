"""Section extraction for '사업의 내용' sub-sections."""

from __future__ import annotations

import re
from typing import Iterable

from .schemas import DartSection
from ..io.normalization import normalize_text

SECTION_MAP = {
    "1. 사업의 개요": "overview",
    "2. 주요 제품 및 서비스": "products",
    "3. 원재료 및 생산설비": "materials",
    "6. 주요계약 및 연구개발활동": "contracts"
}

HEADER_PREFIX = r"(?:[\dⅠⅡⅢⅣⅤⅥⅦⅧⅨⅩ]+[\.|\)|\-\s]*)?"


def _is_header_line(line: str, section_name: str) -> bool:
    if section_name not in line:
        return False
    if len(line.strip()) > 40:
        return False
    pattern = rf"^\s*{HEADER_PREFIX}{re.escape(section_name)}\s*$"
    return re.match(pattern, line.strip()) is not None


def extract_sections(
    *,
    business_text: str,
    sections_to_extract: Iterable[str],
) -> list[DartSection]:
    cleaned = normalize_text(business_text, keep_newlines=True)
    lines = cleaned.splitlines()
    headers: list[tuple[int, str]] = []

    for idx, line in enumerate(lines):
        for name in sections_to_extract:
            if _is_header_line(line, name):
                headers.append((idx, name))
                break

    if not headers:
        return [
            DartSection(
                section_name="unknown",
                raw_text=cleaned,
                provenance={
                    "source": "DART",
                    "offset_hint": "section_header_not_found",
                },
            )
        ]

    headers.sort(key=lambda item: item[0])
    sections: list[DartSection] = []
    for i, (start_idx, name) in enumerate(headers):
        end_idx = headers[i + 1][0] if i + 1 < len(headers) else len(lines)
        body_lines = lines[start_idx:end_idx]
        raw_text = "\n".join(body_lines).strip()
        provenance = {
            "source": "DART",
            "section_name": name,
            "line_start": start_idx + 1,
            "line_end": end_idx if end_idx > start_idx else start_idx + 1,
        }
        sections.append(
            DartSection(
                section_name=name,
                raw_text=raw_text,
                provenance=provenance,
            )
        )

    return sections
