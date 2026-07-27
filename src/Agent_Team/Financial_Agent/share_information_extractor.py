"""Extract point-in-time share counts from an exact DART filing document."""

from __future__ import annotations

import re
import warnings
from typing import Any

from bs4 import BeautifulSoup, Tag, XMLParsedAsHTMLWarning

try:
    from .models import Filing, TargetReport
    from .table_parser import parse_table_matrix
except ImportError:  # pragma: no cover - supports direct script execution
    from models import Filing, TargetReport
    from table_parser import parse_table_matrix


_SECTION_RE = re.compile(r"주식의\s*총수")
def extract_share_information(
    xml_text: str,
    *,
    target: TargetReport,
    filing: Filing,
) -> dict[str, Any]:
    """Return issued, treasury, and outstanding shares from one filing XML."""

    with warnings.catch_warnings():
        warnings.simplefilter("ignore", XMLParsedAsHTMLWarning)
        soup = BeautifulSoup(xml_text or "", "html.parser")

    section = _find_section(soup)
    if section is None:
        return _unavailable(target, filing, "section_not_found")

    for table in section.find_all("table"):
        matrix = parse_table_matrix(table)
        parsed = _parse_share_table(matrix)
        if parsed is None:
            continue
        return {
            "status": "available",
            "as_of_date": target.period_end.isoformat(),
            **parsed,
            "source": _source_metadata(target, filing),
        }
    return _unavailable(target, filing, "share_table_not_found")


def _find_section(soup: BeautifulSoup) -> Tag | None:
    for title in soup.find_all("title"):
        if not _SECTION_RE.search(_clean_text(title.get_text(" ", strip=True))):
            continue
        if isinstance(title.parent, Tag):
            return title.parent
    return None


def _parse_share_table(matrix: list[list[str]]) -> dict[str, Any] | None:
    if len(matrix) < 3:
        return None
    total_column = _total_column(matrix[:2])
    if total_column is None:
        return None

    values: dict[str, int | None] = {}
    disclosed: dict[str, str | None] = {}
    priorities: dict[str, int] = {}
    for row in matrix[2:]:
        label = _normalized_label(" ".join(row[:2]))
        match = _field_for_label(label)
        if match is None:
            continue
        field, priority = match
        if priority <= priorities.get(field, -1):
            continue
        source_value = _cell(row, total_column)
        values[field] = _parse_integer(source_value)
        disclosed[field] = source_value or None
        priorities[field] = priority

    if values.get("issued_shares") is None and values.get("shares_outstanding") is None:
        return None
    issued = values.get("issued_shares")
    treasury = values.get("treasury_shares")
    outstanding = values.get("shares_outstanding")
    if outstanding is None and issued is not None and treasury is not None:
        outstanding = issued - treasury

    return {
        "unit": "shares",
        "issued_shares": issued,
        "treasury_shares": treasury,
        "shares_outstanding": outstanding,
        "disclosed_values": disclosed,
        "validation": {
            "issued_minus_treasury_equals_outstanding": (
                issued - treasury == outstanding
                if issued is not None and treasury is not None and outstanding is not None
                else None
            )
        },
    }


def _total_column(header_rows: list[list[str]]) -> int | None:
    width = max((len(row) for row in header_rows), default=0)
    for col in range(width):
        labels = [_normalized_label(_cell(row, col)) for row in header_rows]
        if "합계" in labels:
            return col
    return None


def _field_for_label(label: str) -> tuple[str, int] | None:
    if "유통주식수" in label:
        return "shares_outstanding", 2
    if "자기주식수" in label:
        return "treasury_shares", 2
    if "현재까지발행한주식의총수" in label:
        return "issued_shares", 1
    if "발행주식의총수" in label:
        return "issued_shares", 2
    return None


def _unavailable(target: TargetReport, filing: Filing, reason: str) -> dict[str, Any]:
    return {
        "status": "not_disclosed",
        "reason": reason,
        "as_of_date": target.period_end.isoformat(),
        "unit": "shares",
        "issued_shares": None,
        "treasury_shares": None,
        "shares_outstanding": None,
        "disclosed_values": {},
        "validation": {},
        "source": _source_metadata(target, filing),
    }


def _source_metadata(target: TargetReport, filing: Filing) -> dict[str, Any]:
    return {
        "provider": "DART",
        "method": "exact_filing_document_xml",
        "period_end": target.period_end.isoformat(),
        "period_type": target.period_type,
        "receipt_no": filing.rcept_no,
        "receipt_date": _iso_date(filing.rcept_dt),
        "report_name": filing.report_nm,
    }


def _parse_integer(value: str) -> int | None:
    text = _clean_text(value)
    if not text or text in {"-", "--", "N/A", "n/a"}:
        return None
    text = text.replace(",", "")
    return int(text) if re.fullmatch(r"\d+", text) else None


def _cell(row: list[str], col: int) -> str:
    if col < 0 or col >= len(row):
        return ""
    return str(row[col] or "").strip()


def _normalized_label(value: str) -> str:
    return re.sub(r"[^0-9A-Za-z가-힣]", "", value or "").lower()


def _clean_text(value: str) -> str:
    return re.sub(r"\s+", " ", str(value or "").replace("\u3000", " ")).strip()


def _iso_date(value: str) -> str:
    digits = re.sub(r"\D", "", value or "")
    if len(digits) == 8:
        return f"{digits[:4]}-{digits[4:6]}-{digits[6:]}"
    return str(value or "")
