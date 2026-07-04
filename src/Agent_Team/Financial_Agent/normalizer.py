"""Deterministic financial statement normalization rules."""

from __future__ import annotations

import copy
import re
from dataclasses import dataclass
from datetime import date
from typing import Any

try:
    from .models import SectionJson, SectionMap, TargetReport
except ImportError:  # pragma: no cover - supports direct script execution
    from models import SectionJson, SectionMap, TargetReport


STATEMENT_NAMES = {
    "4-1": "재무상태표",
    "4-2": "포괄손익계산서",
    "4-3": "자본변동표",
    "4-4": "현금흐름표",
}

_HEADER_LABELS = {"", "과목", "계정과목", "항목", "구분", "주석", "비고"}
_PERIOD_RE = re.compile(r"제\s*\d+\s*기|20\d{2}[./-]\d{1,2}[./-]\d{1,2}|당기|전기|분기|반기|기말|누적|3개월")
_AMOUNT_RE = re.compile(r"^\(?-?\d+(?:,\d{3})*(?:\.\d+)?\)?$")
_DATE_RE = re.compile(
    r"(20\d{2})\s*(?:[./-]|년\s*)\s*(\d{1,2})\s*(?:[./-]|월\s*)\s*(\d{1,2})\s*(?:일)?"
)


@dataclass(frozen=True)
class ColumnProfile:
    """Parsed facts about one matrix column."""

    index: int
    descriptor: str
    top_label: str
    years: frozenset[int]
    dates: tuple[date, ...]


def normalize_primary_report(raw: SectionMap, target: TargetReport) -> SectionMap:
    """Apply normalization only to primary Q1/half/Q3 reports."""

    if not target.is_periodic:
        return copy.deepcopy(raw)

    normalized = copy.deepcopy(raw)
    normalized["4-1"] = _normalize_column_section(raw.get("4-1"), mode="balance", target=target)
    normalized["4-2"] = _normalize_column_section(raw.get("4-2"), mode="income", target=target)
    normalized["4-3"] = _normalize_equity_section(raw.get("4-3"), fiscal_year=target.fiscal_year)
    normalized["4-4"] = _normalize_column_section(raw.get("4-4"), mode="cashflow", target=target)
    return normalized


def isolate_previous_fiscal_year(section: SectionJson, statement_key: str, fiscal_year: int) -> SectionJson:
    """Isolate one fiscal year from the secondary annual report for handoff."""

    if statement_key == "4-3":
        return _normalize_equity_section(section, fiscal_year=fiscal_year)
    return _normalize_column_section(section, mode="annual_first_period")


def _normalize_column_section(
    section: SectionJson | None,
    *,
    mode: str,
    target: TargetReport | None = None,
) -> SectionJson:
    section_out = _empty_section(section, mode)
    for table in (section or {}).get("tables", []):
        matrix = table.get("matrix") or []
        if not matrix:
            section_out["tables"].append(copy.deepcopy(table))
            continue
        keep_cols = _select_columns(matrix, mode=mode, target=target)
        section_out["tables"].append(
            {
                "table_title": str(table.get("table_title") or ""),
                "matrix": _filter_columns(matrix, keep_cols),
            }
        )
    return section_out


def _normalize_equity_section(section: SectionJson | None, *, fiscal_year: int) -> SectionJson:
    section_out = _empty_section(section, "equity")
    for table in (section or {}).get("tables", []):
        matrix = table.get("matrix") or []
        section_out["tables"].append(
            {
                "table_title": str(table.get("table_title") or ""),
                "matrix": _filter_equity_rows(matrix, fiscal_year=fiscal_year),
            }
        )
    return section_out


def _select_columns(matrix: list[list[str]], *, mode: str, target: TargetReport | None = None) -> list[int]:
    width = max((len(row) for row in matrix), default=0)
    if width == 0:
        return []

    header_count = _header_row_count(matrix)
    descriptors = [_column_descriptor(matrix, header_count, col) for col in range(width)]
    profiles = [
        ColumnProfile(
            index=col,
            descriptor=descriptors[col],
            top_label=_top_group_label(matrix, header_count, col),
            years=frozenset(_parse_years(descriptors[col])),
            dates=tuple(_parse_dates(descriptors[col])),
        )
        for col in range(width)
    ]
    label_cols, data_cols = _split_label_and_data_columns(matrix, descriptors, header_count)

    if not data_cols:
        return list(range(width))

    if mode == "annual_first_period":
        return label_cols + [data_cols[0]]

    if mode == "balance":
        return label_cols + [_select_current_period_column(data_cols, profiles, target)]

    if mode == "income":
        return label_cols + _select_current_ytd_columns(data_cols, profiles, target, require_ytd=True)

    if mode == "cashflow":
        return label_cols + _select_current_ytd_columns(data_cols, profiles, target, require_ytd=False)

    return list(range(width))


def _filter_columns(matrix: list[list[str]], keep_cols: list[int]) -> list[list[str]]:
    filtered: list[list[str]] = []
    for row in matrix:
        new_row = [row[col] if col < len(row) else "" for col in keep_cols]
        if any(_logic_text(cell) for cell in new_row):
            filtered.append(new_row)
    return filtered


def _filter_equity_rows(matrix: list[list[str]], *, fiscal_year: int) -> list[list[str]]:
    if not matrix:
        return []

    header_count = _header_row_count(matrix)
    header_rows = matrix[:header_count]
    body_rows = matrix[header_count:]
    selected_body: list[list[str]] = []
    active = False
    saw_target_marker = False

    for row in body_rows:
        label = _logic_text(row[0] if row else "")
        marker_year = _row_marker_year(label)
        if marker_year is not None:
            active = marker_year == fiscal_year
            if active:
                saw_target_marker = True
        if active:
            selected_body.append(row)

    if not saw_target_marker:
        selected_body = [
            row
            for row in body_rows
            if _row_marker_year(_logic_text(row[0] if row else "")) in {None, fiscal_year}
            and str(fiscal_year - 1) not in _logic_text(row[0] if row else "")
        ]

    return header_rows + selected_body


def _header_row_count(matrix: list[list[str]]) -> int:
    count = 0
    for index, row in enumerate(matrix[:6]):
        if _is_header_row(row, index):
            count += 1
            continue
        break
    return max(1, count) if matrix else 0


def _is_header_row(row: list[str], row_index: int) -> bool:
    texts = [_logic_text(cell) for cell in row]
    row_text = " ".join(texts)
    first = texts[0] if texts else ""
    non_first = texts[1:]
    has_amount = any(_is_amount_text(text) for text in non_first)

    if any(label in _HEADER_LABELS for label in texts):
        if row_index == 0 or _PERIOD_RE.search(row_text):
            return True
    if _PERIOD_RE.search(row_text) and not has_amount:
        return True
    if row_index <= 2 and not has_amount and _is_blank(first):
        return True
    return False


def _split_label_and_data_columns(
    matrix: list[list[str]],
    descriptors: list[str],
    header_count: int,
) -> tuple[list[int], list[int]]:
    body = matrix[header_count:]
    first_data_col = None

    for col, descriptor in enumerate(descriptors):
        logic_descriptor = _logic_text(descriptor)
        if _is_note_descriptor(logic_descriptor):
            continue
        if _PERIOD_RE.search(logic_descriptor):
            first_data_col = col
            break
        if col > 0 and _amount_ratio(body, col) >= 0.35:
            first_data_col = col
            break

    if first_data_col is None:
        return [0], list(range(1, len(descriptors)))

    label_cols = list(range(first_data_col))
    data_cols = [col for col in range(first_data_col, len(descriptors)) if not _is_note_descriptor(descriptors[col])]
    return label_cols, data_cols


def _column_descriptor(matrix: list[list[str]], header_count: int, col: int) -> str:
    parts: list[str] = []
    for row in matrix[:header_count]:
        cell = _logic_text(row[col] if col < len(row) else "")
        if cell and cell not in parts and cell not in _HEADER_LABELS:
            parts.append(cell)
    return " ".join(parts)


def _top_group_label(matrix: list[list[str]], header_count: int, col: int) -> str:
    if header_count <= 1:
        return _column_descriptor(matrix, header_count, col)
    return _logic_text(matrix[0][col] if col < len(matrix[0]) else "") or _column_descriptor(matrix, header_count, col)


def _select_current_period_column(
    data_cols: list[int],
    profiles: list[ColumnProfile],
    target: TargetReport | None,
) -> int:
    exact = [col for col in data_cols if target and _has_target_period_end(profiles[col], target)]
    if exact:
        return exact[0]

    current = _current_data_columns(data_cols, profiles, target)
    return current[0] if current else data_cols[0]


def _select_current_ytd_columns(
    data_cols: list[int],
    profiles: list[ColumnProfile],
    target: TargetReport | None,
    *,
    require_ytd: bool,
) -> list[int]:
    current_cols = _current_data_columns(data_cols, profiles, target)
    ytd_cols = [col for col in current_cols if _is_ytd_profile(profiles[col], target)]
    if ytd_cols:
        return ytd_cols

    non_three_month = [col for col in current_cols if not _is_three_month_profile(profiles[col])]
    if non_three_month:
        if require_ytd and len(current_cols) > 1:
            return [non_three_month[-1]]
        return [non_three_month[0]]

    if require_ytd and len(current_cols) > 1:
        return [current_cols[-1]]
    return [current_cols[0]] if current_cols else [data_cols[0]]


def _current_data_columns(
    data_cols: list[int],
    profiles: list[ColumnProfile],
    target: TargetReport | None,
) -> list[int]:
    if not data_cols:
        return []

    if target:
        exact = [col for col in data_cols if _has_target_period_end(profiles[col], target)]
        if exact:
            return exact

        by_year = [
            col
            for col in data_cols
            if target.fiscal_year in profiles[col].years and not _is_prior_profile(profiles[col], target)
        ]
        if by_year:
            return by_year

    by_current_token = [col for col in data_cols if _has_current_token(profiles[col]) and not _is_prior_profile(profiles[col], target)]
    if by_current_token:
        return by_current_token

    first_group = profiles[data_cols[0]].top_label
    if first_group:
        grouped = [col for col in data_cols if profiles[col].top_label == first_group]
        if len(grouped) > 1:
            return grouped

    if _looks_like_repeated_three_month_ytd_pair(data_cols, profiles):
        return data_cols[:2]

    return [data_cols[0]]


def _has_target_period_end(profile: ColumnProfile, target: TargetReport) -> bool:
    if target.period_end in profile.dates:
        return True
    ranges = _date_ranges(profile)
    return any(end == target.period_end for _, end in ranges)


def _is_ytd_profile(profile: ColumnProfile, target: TargetReport | None) -> bool:
    text = _logic_text(profile.descriptor)
    if "누적" in text:
        return True
    for start, end in _date_ranges(profile):
        if start.month == 1 and start.day == 1:
            if target is None or (start.year == target.fiscal_year and end == target.period_end):
                return True
    return False


def _is_three_month_profile(profile: ColumnProfile) -> bool:
    text = _logic_text(profile.descriptor)
    if "3개월" in text or "석달" in text:
        return True
    for start, end in _date_ranges(profile):
        if start.year == end.year and (end.month - start.month) in {2, 3} and start.day == 1:
            return True
    return False


def _is_prior_profile(profile: ColumnProfile, target: TargetReport | None) -> bool:
    text = _logic_text(profile.descriptor)
    if any(token in text for token in ("전기", "전분기", "전반기", "전년", "전년도")):
        return True
    if target and (target.fiscal_year - 1) in profile.years:
        return True
    return False


def _has_current_token(profile: ColumnProfile) -> bool:
    text = _logic_text(profile.descriptor)
    return any(token in text for token in ("당기", "당분기", "당반기", "당년도", "당해"))


def _looks_like_repeated_three_month_ytd_pair(data_cols: list[int], profiles: list[ColumnProfile]) -> bool:
    if len(data_cols) < 4:
        return False
    first = profiles[data_cols[0]]
    second = profiles[data_cols[1]]
    third = profiles[data_cols[2]]
    fourth = profiles[data_cols[3]]
    return (
        _is_three_month_profile(first)
        and _is_ytd_profile(second, None)
        and _is_three_month_profile(third)
        and _is_ytd_profile(fourth, None)
    )


def _date_ranges(profile: ColumnProfile) -> list[tuple[date, date]]:
    dates = list(profile.dates)
    if len(dates) < 2:
        return []
    ranges: list[tuple[date, date]] = []
    for index in range(0, len(dates) - 1, 2):
        start = dates[index]
        end = dates[index + 1]
        if start <= end:
            ranges.append((start, end))
    return ranges


def _parse_dates(text: str) -> list[date]:
    dates: list[date] = []
    for match in _DATE_RE.finditer(text or ""):
        try:
            dates.append(date(int(match.group(1)), int(match.group(2)), int(match.group(3))))
        except ValueError:
            continue
    return dates


def _parse_years(text: str) -> set[int]:
    years = {int(match) for match in re.findall(r"20\d{2}", text or "")}
    years.update(parsed_date.year for parsed_date in _parse_dates(text))
    return years


def _amount_ratio(rows: list[list[str]], col: int) -> float:
    if not rows:
        return 0.0
    checked = 0
    amount_like = 0
    for row in rows:
        if col >= len(row):
            continue
        text = _logic_text(row[col])
        if not text:
            continue
        checked += 1
        if _is_amount_text(text):
            amount_like += 1
    return amount_like / checked if checked else 0.0


def _row_marker_year(label: str) -> int | None:
    match = re.search(r"(20\d{2})[./-]\s*(?:0?1[./-]\s*0?1|12[./-]\s*31)", label)
    if match:
        return int(match.group(1))
    match = re.search(r"(20\d{2})", label)
    if match and any(token in label for token in ("기초", "기말", "당기", "분기", "반기")):
        return int(match.group(1))
    return None


def _is_amount_text(value: str) -> bool:
    text = _logic_text(value)
    return bool(_AMOUNT_RE.match(text))


def _is_note_descriptor(value: str) -> bool:
    text = _logic_text(value)
    return text in {"주석", "비고"}


def _is_blank(value: str) -> bool:
    return _logic_text(value) == ""


def _logic_text(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "").replace("\u3000", " ")).strip()


def _empty_section(section: SectionJson | None, mode: str) -> SectionJson:
    title = ""
    if section:
        title = str(section.get("section_title") or "")
    if not title:
        title = {
            "balance": STATEMENT_NAMES["4-1"],
            "income": STATEMENT_NAMES["4-2"],
            "equity": STATEMENT_NAMES["4-3"],
            "cashflow": STATEMENT_NAMES["4-4"],
            "annual_first_period": "",
        }.get(mode, "")
    return {"section_title": title, "tables": []}
