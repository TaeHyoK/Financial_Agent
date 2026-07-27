"""Extract product, service, or segment revenue breakdowns from DART XML."""

from __future__ import annotations

import re
import warnings
from calendar import monthrange
from datetime import date
from typing import Any

from bs4 import BeautifulSoup, Tag, XMLParsedAsHTMLWarning

try:
    from .models import Filing, TargetReport
    from .table_parser import parse_table_matrix
except ImportError:  # pragma: no cover - supports direct script execution
    from models import Filing, TargetReport
    from table_parser import parse_table_matrix


_SECTION_RE = re.compile(r"주요\s*(?:제품|서비스).*(?:제품|서비스)|주요\s*(?:제품|서비스)")
_AMOUNT_HEADER_RE = re.compile(r"매출액|매출금액|매출|수익")
_SHARE_HEADER_RE = re.compile(r"비율|비중|구성비")
_UNIT_RE = re.compile(r"단위\s*[:：]\s*([^\s)]+)")
_DATE_RE = re.compile(r"(20\d{2})\s*년?\s*[./-]?\s*(\d{1,2})\s*월?")
_TOTAL_LABELS = {"계", "합계", "총계", "소계", "total"}
_UNIT_MULTIPLIERS = {
    "원": 1,
    "천원": 1_000,
    "백만원": 1_000_000,
    "억원": 100_000_000,
}


def extract_revenue_breakdown(
    xml_text: str,
    *,
    target: TargetReport,
    filing: Filing,
) -> dict[str, Any]:
    """Return a normalized revenue composition from the latest regular filing."""

    # DART documents mix XML declarations with HTML-style, occasionally malformed
    # tables. The HTML parser is intentional because it recovers those tables.
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", XMLParsedAsHTMLWarning)
        soup = BeautifulSoup(xml_text or "", "html.parser")
    section = _find_revenue_section(soup)
    if section is None:
        return _not_disclosed(target, filing, "section_not_found")

    section_title = _section_title(section)
    unit = _extract_unit(section)
    for table in section.find_all("table"):
        matrix = parse_table_matrix(table)
        parsed = _parse_breakdown_matrix(matrix, unit=unit, target=target)
        if parsed is None:
            continue
        parsed.update(
            {
                "status": "available",
                "section_title": section_title,
                "dimension_type": _dimension_type(matrix, section_title),
                "source": _source_metadata(target, filing),
            }
        )
        return parsed
    return _not_disclosed(target, filing, "breakdown_table_not_found", section_title=section_title)


def _find_revenue_section(soup: BeautifulSoup) -> Tag | None:
    for title in soup.find_all("title"):
        text = _clean_text(title.get_text(" ", strip=True))
        if not _SECTION_RE.search(text):
            continue
        parent = title.parent
        if isinstance(parent, Tag):
            return parent
    return None


def _section_title(section: Tag) -> str:
    title = section.find("title")
    return _clean_text(title.get_text(" ", strip=True)) if isinstance(title, Tag) else "주요 제품 및 서비스"


def _extract_unit(section: Tag) -> str:
    for text in section.stripped_strings:
        match = _UNIT_RE.search(_clean_text(text))
        if match:
            return match.group(1).strip()
    return "원"


def _parse_breakdown_matrix(
    matrix: list[list[str]],
    *,
    unit: str,
    target: TargetReport,
) -> dict[str, Any] | None:
    if len(matrix) < 3:
        return None
    header_rows = _header_row_count(matrix)
    if header_rows < 2:
        return None
    descriptors = [_column_descriptor(matrix, header_rows, col) for col in range(_width(matrix))]
    amount_columns = [
        col
        for col in range(1, len(descriptors))
        if _AMOUNT_HEADER_RE.search(_clean_text(matrix[header_rows - 1][col] if col < len(matrix[header_rows - 1]) else ""))
    ]
    if not amount_columns:
        return None

    periods: list[dict[str, Any]] = []
    column_map: dict[int, dict[str, Any]] = {}
    for amount_col in amount_columns:
        descriptor = descriptors[amount_col]
        period = _period_metadata(descriptor, target=target, index=len(periods))
        share_col = _matching_share_column(matrix, header_rows, amount_col, descriptor)
        periods.append(period)
        column_map[amount_col] = {"period_key": period["period_key"], "share_col": share_col}

    items: list[dict[str, Any]] = []
    totals_by_period: dict[str, dict[str, Any]] = {}
    multiplier = _UNIT_MULTIPLIERS.get(unit)
    for row in matrix[header_rows:]:
        name = _clean_text(row[0] if row else "")
        if not name:
            continue
        values_by_period: dict[str, dict[str, Any]] = {}
        for amount_col, mapping in column_map.items():
            disclosed_amount = _cell(row, amount_col)
            amount = _parse_number(disclosed_amount)
            share_text = _cell(row, mapping["share_col"]) if mapping["share_col"] is not None else ""
            share = _parse_share(share_text)
            if amount is None and share is None:
                continue
            value = {
                "revenue": amount,
                "revenue_disclosed": disclosed_amount or None,
                "revenue_krw": amount * multiplier if amount is not None and multiplier is not None else None,
                "revenue_share": share,
                "revenue_share_disclosed": share_text or None,
            }
            values_by_period[mapping["period_key"]] = value
        if not values_by_period:
            continue
        if _normalized_label(name) in _TOTAL_LABELS:
            totals_by_period.update(values_by_period)
            continue
        items.append({"name": name, "values_by_period": values_by_period})

    if not items:
        return None
    current_period_key = _current_period_key(periods, target)
    current_items = []
    for item in items:
        value = item["values_by_period"].get(current_period_key)
        if value is not None:
            current_items.append({"name": item["name"], **value})
    return {
        "unit": unit,
        "unit_multiplier_to_krw": multiplier,
        "periods": periods,
        "current_period_key": current_period_key,
        "current_period": next((period for period in periods if period["period_key"] == current_period_key), {}),
        "items": items,
        "current_items": current_items,
        "totals_by_period": totals_by_period,
        "validation": _validate_shares(items, periods, totals_by_period),
    }


def _header_row_count(matrix: list[list[str]]) -> int:
    for index, row in enumerate(matrix):
        if index >= 2 and any(_parse_number(cell) is not None for cell in row[1:]):
            return index
    return min(2, len(matrix))


def _column_descriptor(matrix: list[list[str]], header_rows: int, col: int) -> str:
    parts: list[str] = []
    for row in matrix[:header_rows]:
        value = _clean_text(_cell(row, col))
        if value and (not parts or value != parts[-1]):
            parts.append(value)
    return " | ".join(parts)


def _matching_share_column(
    matrix: list[list[str]],
    header_rows: int,
    amount_col: int,
    descriptor: str,
) -> int | None:
    width = _width(matrix)
    top_label = _clean_text(_cell(matrix[0], amount_col))
    for col in range(amount_col + 1, min(width, amount_col + 3)):
        subheader = _clean_text(_cell(matrix[header_rows - 1], col))
        candidate_top = _clean_text(_cell(matrix[0], col))
        if _SHARE_HEADER_RE.search(subheader) and (not top_label or candidate_top == top_label):
            return col
    return None


def _period_metadata(descriptor: str, *, target: TargetReport, index: int) -> dict[str, Any]:
    dates = _DATE_RE.findall(descriptor)
    fiscal_year = int(dates[-1][0]) if dates else target.fiscal_year - index
    month = int(dates[-1][1]) if dates else target.period_end.month if index == 0 else 12
    period_end = date(fiscal_year, month, monthrange(fiscal_year, month)[1])
    compact = re.sub(r"\s+", "", descriptor)
    if "반기" in compact or month == 6:
        period_type, basis = "HALF", "YTD"
    elif "3분기" in compact or month == 9:
        period_type, basis = "Q3", "YTD"
    elif "1분기" in compact or month == 3:
        period_type, basis = "Q1", "YTD"
    elif "기말" in compact or month == 12:
        period_type, basis = "ANNUAL", "FULL_YEAR"
    else:
        period_type, basis = "OTHER", "DISCLOSED_PERIOD"
    return {
        "period_key": f"disclosed_period_{index + 1}",
        "label": descriptor,
        "fiscal_year": fiscal_year,
        "period_type": period_type,
        "period_end": period_end.isoformat(),
        "basis": basis,
    }


def _current_period_key(periods: list[dict[str, Any]], target: TargetReport) -> str:
    for period in periods:
        if period.get("period_end") == target.period_end.isoformat():
            return str(period["period_key"])
    return str(periods[0]["period_key"]) if periods else ""


def _validate_shares(
    items: list[dict[str, Any]],
    periods: list[dict[str, Any]],
    totals_by_period: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    share_sums: dict[str, float | None] = {}
    for period in periods:
        period_key = str(period["period_key"])
        shares = [
            item["values_by_period"][period_key].get("revenue_share")
            for item in items
            if period_key in item["values_by_period"]
        ]
        numeric_shares = [float(value) for value in shares if isinstance(value, int | float)]
        share_sums[period_key] = round(sum(numeric_shares), 6) if numeric_shares else None
    return {
        "share_sums": share_sums,
        "share_sum_within_tolerance": {
            key: value is None or abs(value - 1.0) <= 0.02
            for key, value in share_sums.items()
        },
        "reported_totals_present": bool(totals_by_period),
    }


def _dimension_type(matrix: list[list[str]], section_title: str) -> str:
    header = " ".join(_clean_text(cell) for row in matrix[:2] for cell in row)
    if re.search(r"지역|국가|내수|수출", header):
        return "region"
    if re.search(r"사업부문|부문", header):
        return "segment"
    if "제품" in section_title and "서비스" in section_title:
        return "product_service"
    if "서비스" in section_title:
        return "service"
    if "제품" in section_title:
        return "product"
    return "other"


def _not_disclosed(
    target: TargetReport,
    filing: Filing,
    reason: str,
    *,
    section_title: str = "",
) -> dict[str, Any]:
    return {
        "status": "not_disclosed",
        "reason": reason,
        "section_title": section_title,
        "dimension_type": "other",
        "unit": None,
        "periods": [],
        "current_period_key": "",
        "current_period": {},
        "items": [],
        "current_items": [],
        "totals_by_period": {},
        "validation": {},
        "source": _source_metadata(target, filing),
    }


def _source_metadata(target: TargetReport, filing: Filing) -> dict[str, Any]:
    return {
        "period_end": target.period_end.isoformat(),
        "period_type": target.period_type,
        "receipt_no": filing.rcept_no,
        "receipt_date": _iso_date(filing.rcept_dt),
        "report_name": filing.report_nm,
    }


def _parse_number(value: str) -> int | float | None:
    text = _clean_text(value)
    if not text or text in {"-", "N/A", "n/a"}:
        return None
    negative = text.startswith("(") and text.endswith(")")
    text = text.strip("()").replace(",", "")
    if not re.fullmatch(r"-?\d+(?:\.\d+)?", text):
        return None
    number: int | float = float(text) if "." in text else int(text)
    return -number if negative else number


def _parse_share(value: str) -> float | None:
    text = _clean_text(value).replace("%", "")
    number = _parse_number(text)
    if number is None:
        return None
    return round(float(number) / 100.0, 8)


def _cell(row: list[str], col: int | None) -> str:
    if col is None or col < 0 or col >= len(row):
        return ""
    return str(row[col] or "").strip()


def _width(matrix: list[list[str]]) -> int:
    return max((len(row) for row in matrix), default=0)


def _normalized_label(value: str) -> str:
    return re.sub(r"\s+", "", value or "").lower()


def _clean_text(value: str) -> str:
    return re.sub(r"\s+", " ", str(value or "").replace("\u3000", " ")).strip()


def _iso_date(value: str) -> str:
    digits = re.sub(r"\D", "", value or "")
    if len(digits) == 8:
        return f"{digits[:4]}-{digits[4:6]}-{digits[6:]}"
    return str(value or "")
