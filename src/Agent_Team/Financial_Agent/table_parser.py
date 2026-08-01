"""HTML table parsing with rowspan/colspan expansion."""

from __future__ import annotations

import re
from html import unescape
from typing import Any

from bs4 import BeautifulSoup, Tag

try:
    from .models import TableJson
except ImportError:  # pragma: no cover - supports direct script execution
    from models import TableJson


_STATEMENT_NAMES = ("재무상태표", "포괄손익계산서", "손익계산서", "자본변동표", "현금흐름표")
_NUMERIC_RE = re.compile(r"\(?\d{1,3}(?:,\d{3})+(?:\.\d+)?\)?|\(?\d{4,}(?:\.\d+)?\)?")
_UNIT_RE = re.compile(r"단위[:：]?(원|천원|백만원|억원)")
_UNIT_MULTIPLIERS_TO_KRW = {
    "원": 1,
    "천원": 1_000,
    "백만원": 1_000_000,
    "억원": 100_000_000,
}


def parse_statement_tables(fragment_html: str, fallback_title: str) -> list[TableJson]:
    """Parse relevant data tables from one financial statement subsection."""

    soup = BeautifulSoup(fragment_html or "", "html.parser")
    tables: list[TableJson] = []
    current_title = fallback_title
    current_unit = "원"

    for table in soup.find_all("table"):
        matrix = parse_table_matrix(table)
        if not matrix:
            continue

        is_data_table = _looks_like_data_table(matrix)
        detected_unit = _unit_from_matrix(matrix, header_only=is_data_table)
        if detected_unit:
            current_unit = detected_unit

        metadata_title = _title_from_metadata_matrix(matrix)
        if metadata_title:
            current_title = metadata_title

        if not is_data_table:
            continue

        tables.append(
            {
                "table_title": current_title or fallback_title,
                "matrix": matrix,
                "source_unit": current_unit,
                "unit_multiplier_to_krw": _UNIT_MULTIPLIERS_TO_KRW[current_unit],
            }
        )

    return tables


def parse_table_matrix(table: Tag) -> list[list[str]]:
    """Expand one HTML table into a rectangular string matrix."""

    rows = table.find_all("tr")
    grid: list[list[str]] = []

    for row_index, row in enumerate(rows):
        while len(grid) <= row_index:
            grid.append([])

        col_index = 0
        cells = _direct_cells(row)
        for cell in cells:
            text = _cell_text(cell)
            rowspan = _span(cell, "rowspan")
            colspan = _span(cell, "colspan")

            while col_index < len(grid[row_index]) and grid[row_index][col_index] != "":
                col_index += 1

            for row_offset in range(rowspan):
                target_row = row_index + row_offset
                while len(grid) <= target_row:
                    grid.append([])
                required_width = col_index + colspan
                if len(grid[target_row]) < required_width:
                    grid[target_row].extend([""] * (required_width - len(grid[target_row])))
                for col_offset in range(colspan):
                    target_col = col_index + col_offset
                    if grid[target_row][target_col] == "":
                        grid[target_row][target_col] = text

            col_index += colspan

    width = max((len(row) for row in grid), default=0)
    normalized = [row + [""] * (width - len(row)) for row in grid if any(_logic_text(cell) for cell in row)]
    return normalized


def _direct_cells(row: Tag) -> list[Tag]:
    cells = [child for child in row.children if isinstance(child, Tag) and child.name in {"td", "th", "te"}]
    if cells:
        return cells
    return list(row.find_all(["td", "th", "te"]))


def _span(cell: Tag, attr_name: str) -> int:
    raw = cell.get(attr_name, 1)
    try:
        return max(1, int(str(raw)))
    except (TypeError, ValueError):
        return 1


def _cell_text(cell: Tag) -> str:
    text = cell.get_text(" ", strip=False)
    text = unescape(text)
    text = text.replace("\xa0", " ")
    text = re.sub(r"[ \t\r\n\f\v]+", " ", text)
    return text.strip(" \t\r\n\f\v")


def _title_from_metadata_matrix(matrix: list[list[str]]) -> str:
    if not matrix:
        return ""
    width = max(len(row) for row in matrix)
    if width > 1:
        return ""
    for row in matrix:
        text = _logic_text(row[0] if row else "")
        for name in _STATEMENT_NAMES:
            if name in text:
                if name == "손익계산서" and "포괄손익계산서" in text:
                    return "포괄손익계산서"
                return name
    return ""


def _looks_like_data_table(matrix: list[list[str]]) -> bool:
    width = max((len(row) for row in matrix), default=0)
    if width <= 1:
        return False
    if len(matrix) < 2:
        return False
    numeric_count = sum(1 for row in matrix for cell in row[1:] if _NUMERIC_RE.search(_logic_text(cell)))
    if numeric_count > 0:
        return True
    header_text = " ".join(_logic_text(cell) for row in matrix[:3] for cell in row)
    return any(token in header_text for token in ("제", "당기", "전기", "누적", "3개월"))


def _unit_from_matrix(matrix: list[list[str]], *, header_only: bool) -> str:
    rows = matrix[:3] if header_only else matrix
    for row in rows:
        for cell in row:
            compact = re.sub(r"\s+", "", str(cell or ""))
            match = _UNIT_RE.search(compact)
            if match:
                return match.group(1)
        if header_only and any(_NUMERIC_RE.search(_logic_text(cell)) for cell in row[1:]):
            break
    return ""


def _logic_text(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "").replace("\u3000", " ")).strip()
