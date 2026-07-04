"""Build canonical two-year financial statement JSON from matrix extraction."""

from __future__ import annotations

import copy
import hashlib
import re
from dataclasses import dataclass
from datetime import date
from typing import Any, Literal

try:
    from .models import SectionJson, TargetReport
    from .normalizer import STATEMENT_NAMES, isolate_previous_fiscal_year
except ImportError:  # pragma: no cover - supports direct script execution
    from models import SectionJson, TargetReport
    from normalizer import STATEMENT_NAMES, isolate_previous_fiscal_year


PeriodBasis = Literal["POINT_IN_TIME", "YTD", "FULL_YEAR"]

_DATE_RE = re.compile(
    r"(20\d{2})\s*(?:[./-]|년\s*)\s*(\d{1,2})\s*(?:[./-]|월\s*)\s*(\d{1,2})\s*(?:일)?"
)
_NOTE_REF_RE = re.compile(r"\((?:\s*주\s*)[^)]*\)")
_PAREN_LOSS_RE = re.compile(r"\((손실)\)")
_HEADER_LABELS = {"", "과목", "계정과목", "항목", "구분", "주석", "비고"}
_PERIOD_HINT_RE = re.compile(r"제\s*\d+\s*기|20\d{2}|당기|전기|분기|반기|기말|누적|3개월")
_AMOUNT_RE = re.compile(r"^\(?-?\d+(?:,\d{3})*(?:\.\d+)?\)?$")

_TABLE_KEY_PREFIX = {
    "4-1": "balance_sheet",
    "4-2": "income_statement",
    "4-3": "changes_in_equity",
    "4-4": "cash_flow",
}

_SAFE_ALIAS_KEYS = {
    "유동자산": "current_assets",
    "비유동자산": "non_current_assets",
    "자산총계": "total_assets",
    "유동부채": "current_liabilities",
    "비유동부채": "non_current_liabilities",
    "부채총계": "total_liabilities",
    "자본총계": "total_equity",
    "자본합계": "total_equity",
    "현금및현금성자산": "cash_and_cash_equivalents",
    "기초현금및현금성자산": "beginning_cash_and_cash_equivalents",
    "기말현금및현금성자산": "ending_cash_and_cash_equivalents",
    "매출액": "revenue",
    "제품매출": "product_revenue",
    "용역매출": "service_revenue",
    "영업이익": "operating_profit",
    "영업이익손실": "operating_profit",
    "당기순이익": "net_income",
    "당기순이익손실": "net_income",
    "분기순이익": "net_income",
    "분기순이익손실": "net_income",
    "반기순이익": "net_income",
    "반기순이익손실": "net_income",
    "영업활동현금흐름": "cash_flows_from_operating_activities",
    "영업활동으로인한현금흐름": "cash_flows_from_operating_activities",
    "투자활동현금흐름": "cash_flows_from_investing_activities",
    "재무활동현금흐름": "cash_flows_from_financing_activities",
    "자본금": "share_capital",
    "기타불입자본": "additional_paid_in_capital",
    "자본잉여금": "capital_surplus",
    "기타자본": "other_equity",
    "기타자본항목": "other_equity",
    "기타포괄손익누계액": "accumulated_other_comprehensive_income",
    "이익잉여금": "retained_earnings",
    "결손금": "deficit",
    "지분법자본변동": "equity_method_capital_changes",
}

_SAFE_ROW_KEYS = {
    "기초자본": "beginning_balance",
    "기말자본": "ending_balance",
    "분기말자본": "ending_balance",
    "반기말자본": "ending_balance",
    "총포괄손익": "total_comprehensive_income",
    "총포괄이익손실": "total_comprehensive_income",
    "당기순이익": "net_income",
    "분기순이익": "net_income",
    "반기순이익": "net_income",
    "지분법자본변동": "equity_method_capital_changes",
}


@dataclass(frozen=True)
class PeriodItems:
    """Single-period values parsed from a non-equity statement table."""

    label: str
    fiscal_year: int | None
    period_type: str
    period_end: str
    items: list[dict[str, str | None]]


def build_2y_handoff(master: dict[str, Any], secondary_target: TargetReport) -> dict[str, Any]:
    """Build the canonical two-year schema from the flat matrix master."""

    canonical: dict[str, Any] = {}
    for statement_key, statement_name in STATEMENT_NAMES.items():
        current_section = _section(master, "primary", statement_key)
        secondary_section = _section(master, "secondary", statement_key)
        if statement_key == "4-3":
            canonical[statement_key] = _build_equity_statement(current_section, secondary_section, secondary_target)
        else:
            canonical[statement_key] = _build_pair_statement(
                statement_key,
                statement_name,
                current_section,
                secondary_section,
                secondary_target,
            )
    return canonical


def build_master_canonical(master: dict[str, Any], secondary_target: TargetReport) -> dict[str, Any]:
    """Build the canonical master with current period plus prior annual history."""

    canonical: dict[str, Any] = {}
    for statement_key, statement_name in STATEMENT_NAMES.items():
        current_section = _section(master, "primary", statement_key)
        secondary_section = _section(master, "secondary", statement_key)
        if statement_key == "4-3":
            canonical[statement_key] = _build_equity_master_statement(current_section, secondary_section, secondary_target)
        else:
            canonical[statement_key] = _build_multi_period_statement(
                statement_key,
                statement_name,
                current_section,
                secondary_section,
                secondary_target,
            )
    return canonical


def _build_pair_statement(
    statement_key: str,
    statement_name: str,
    current_section: SectionJson,
    secondary_section: SectionJson,
    secondary_target: TargetReport,
) -> dict[str, Any]:
    current_basis, previous_basis = _basis_for_statement(statement_key)
    current_period_meta = _current_period_metadata(secondary_target, current_basis)
    previous_period_meta = _period_metadata(
        label="",
        fiscal_year=secondary_target.fiscal_year,
        period_type="ANNUAL",
        period_end=secondary_target.period_end.isoformat(),
        basis=previous_basis,
    )
    tables: list[dict[str, Any]] = []
    for table_index, (current_table, previous_table) in enumerate(
        _pair_tables(current_section.get("tables", []), secondary_section.get("tables", [])),
        start=1,
    ):
        current_period = _single_period_table(current_table, target_year=None)
        previous_period = _single_period_table(previous_table, target_year=secondary_target.fiscal_year)
        items_by_key, item_order = _canonical_pair_items(current_period.items, previous_period.items)
        tables.append(
            {
                "table_key": f"{_TABLE_KEY_PREFIX[statement_key]}_{table_index}",
                "table_title": _table_title(current_table, previous_table),
                "unit": "원",
                "periods": {
                    "current_fiscal_year": _with_label(current_period_meta, current_period.label),
                    "previous_fiscal_year": _with_label(previous_period_meta, previous_period.label),
                },
                "items_by_key": items_by_key,
                "item_order": item_order,
            }
        )
    return {"statement_name": statement_name, "tables": tables}


def _build_multi_period_statement(
    statement_key: str,
    statement_name: str,
    current_section: SectionJson,
    secondary_section: SectionJson,
    secondary_target: TargetReport,
) -> dict[str, Any]:
    current_basis, previous_basis = _basis_for_statement(statement_key)
    current_period_meta = _current_period_metadata(secondary_target, current_basis)
    tables: list[dict[str, Any]] = []
    for table_index, (current_table, previous_table) in enumerate(
        _pair_tables(current_section.get("tables", []), secondary_section.get("tables", [])),
        start=1,
    ):
        period_sources = _multi_period_sources(
            current_table,
            previous_table,
            secondary_target,
            current_basis=current_basis,
            previous_basis=previous_basis,
            current_period_meta=current_period_meta,
        )
        periods = {source["period_key"]: source["metadata"] for source in period_sources}
        items_by_key, item_order = _canonical_multi_period_items(period_sources)
        tables.append(
            {
                "table_key": f"{_TABLE_KEY_PREFIX[statement_key]}_{table_index}",
                "table_title": _table_title(current_table, previous_table),
                "unit": "원",
                "periods": periods,
                "items_by_key": items_by_key,
                "item_order": item_order,
            }
        )
    return {"statement_name": statement_name, "tables": tables}


def _build_equity_statement(
    current_section: SectionJson,
    secondary_section: SectionJson,
    secondary_target: TargetReport,
) -> dict[str, Any]:
    previous_section = isolate_previous_fiscal_year(secondary_section, "4-3", fiscal_year=secondary_target.fiscal_year)
    current_basis: PeriodBasis = "YTD"
    previous_basis: PeriodBasis = "FULL_YEAR"
    current_period_meta = _current_period_metadata(secondary_target, current_basis)
    previous_period_meta = _period_metadata(
        label="",
        fiscal_year=secondary_target.fiscal_year,
        period_type="ANNUAL",
        period_end=secondary_target.period_end.isoformat(),
        basis=previous_basis,
    )
    tables: list[dict[str, Any]] = []
    for table_index, (current_table, previous_table) in enumerate(
        _pair_tables(current_section.get("tables", []), previous_section.get("tables", [])),
        start=1,
    ):
        current_block = _equity_block(current_table, basis=current_basis, period_meta=current_period_meta)
        previous_block = _equity_block(previous_table, basis=previous_basis, period_meta=previous_period_meta)
        _merge_column_aliases(current_block, previous_block)
        tables.append(
            {
                "table_key": f"{_TABLE_KEY_PREFIX['4-3']}_{table_index}",
                "table_title": _table_title(current_table, previous_table),
                "unit": "원",
                "period_blocks": {
                    "current_fiscal_year": current_block,
                    "previous_fiscal_year": previous_block,
                },
            }
        )
    return {"statement_name": STATEMENT_NAMES["4-3"], "tables": tables}


def _build_equity_master_statement(
    current_section: SectionJson,
    secondary_section: SectionJson,
    secondary_target: TargetReport,
) -> dict[str, Any]:
    current_basis: PeriodBasis = "YTD"
    current_period_meta = _current_period_metadata(secondary_target, current_basis)
    tables: list[dict[str, Any]] = []
    for table_index, (current_table, previous_table) in enumerate(
        _pair_tables(current_section.get("tables", []), secondary_section.get("tables", [])),
        start=1,
    ):
        period_blocks: dict[str, Any] = {
            "current_fiscal_year": _equity_block(current_table, basis=current_basis, period_meta=current_period_meta)
        }
        for offset in range(3):
            fiscal_year = secondary_target.fiscal_year - offset
            period_key = _historical_period_key(offset)
            historical_section = isolate_previous_fiscal_year(
                secondary_section,
                "4-3",
                fiscal_year=fiscal_year,
            )
            historical_table = _table_at_or_none(historical_section.get("tables", []), table_index - 1)
            block = _equity_block(
                historical_table,
                basis="FULL_YEAR",
                period_meta=_annual_period_metadata(fiscal_year, basis="FULL_YEAR"),
            )
            if block["row_order"]:
                period_blocks[period_key] = block

        _merge_all_period_column_aliases(period_blocks)
        tables.append(
            {
                "table_key": f"{_TABLE_KEY_PREFIX['4-3']}_{table_index}",
                "table_title": _table_title(current_table, previous_table),
                "unit": "원",
                "period_blocks": period_blocks,
            }
        )
    return {"statement_name": STATEMENT_NAMES["4-3"], "tables": tables}


def _single_period_table(table: dict[str, Any] | None, target_year: int | None) -> PeriodItems:
    if table is None:
        return PeriodItems(label="", fiscal_year=None, period_type="", period_end="", items=[])

    matrix = _matrix(table)
    if not matrix:
        return PeriodItems(label="", fiscal_year=None, period_type="", period_end="", items=[])

    header_count = _header_row_count(matrix)
    descriptors = [_column_descriptor(matrix, header_count, col) for col in range(_width(matrix))]
    data_col = _select_period_column(descriptors, target_year)
    if data_col is None:
        return PeriodItems(label="", fiscal_year=None, period_type="", period_end="", items=[])

    items: list[dict[str, str | None]] = []
    used_keys: set[str] = set()
    for row in matrix[header_count:]:
        display_name = _display_label(row[0] if row else "")
        if not display_name:
            continue
        value = _cell(row, data_col)
        if value == "":
            continue
        base_key = _stable_key(display_name, namespace="item")
        item_key = _dedupe_key(base_key, used_keys)
        items.append({"key": item_key, "display_name": display_name, "value": value})

    descriptor = descriptors[data_col]
    period_dates = _parse_dates(descriptor)
    period_end = max(period_dates).isoformat() if period_dates else ""
    fiscal_year = period_dates[-1].year if period_dates else _parse_year_from_descriptor(descriptor)
    return PeriodItems(
        label=_period_label(descriptor),
        fiscal_year=fiscal_year,
        period_type=_infer_period_type(descriptor, period_end),
        period_end=period_end,
        items=items,
    )


def _multi_period_sources(
    current_table: dict[str, Any] | None,
    previous_table: dict[str, Any] | None,
    secondary_target: TargetReport,
    *,
    current_basis: PeriodBasis,
    previous_basis: PeriodBasis,
    current_period_meta: dict[str, Any],
) -> list[dict[str, Any]]:
    sources: list[dict[str, Any]] = []
    current_period = _single_period_table(current_table, target_year=None)
    sources.append(
        {
            "period_key": "current_fiscal_year",
            "metadata": _with_label(current_period_meta, current_period.label),
            "items": current_period.items,
        }
    )

    for source in _annual_period_sources(previous_table, secondary_target.fiscal_year, basis=previous_basis):
        sources.append(source)

    return sources


def _annual_period_sources(
    table: dict[str, Any] | None,
    starting_fiscal_year: int,
    *,
    basis: PeriodBasis,
) -> list[dict[str, Any]]:
    if table is None:
        return []
    matrix = _matrix(table)
    if not matrix:
        return []

    header_count = _header_row_count(matrix)
    descriptors = [_column_descriptor(matrix, header_count, col) for col in range(_width(matrix))]
    data_cols = [index for index in range(1, len(descriptors)) if not _is_note_label(descriptors[index])]
    sources: list[dict[str, Any]] = []

    for offset, col in enumerate(data_cols[:3]):
        fiscal_year = _parse_year_from_descriptor(descriptors[col]) or starting_fiscal_year - offset
        period_key = _historical_period_key(offset)
        items: list[dict[str, str | None]] = []
        used_keys: set[str] = set()
        for row in matrix[header_count:]:
            display_name = _display_label(row[0] if row else "")
            if not display_name:
                continue
            value = _cell(row, col)
            if value == "":
                continue
            base_key = _stable_key(display_name, namespace="item")
            item_key = _dedupe_key(base_key, used_keys)
            items.append({"key": item_key, "display_name": display_name, "value": value})
        sources.append(
            {
                "period_key": period_key,
                "metadata": _with_label(_annual_period_metadata(fiscal_year, basis=basis), _period_label(descriptors[col])),
                "items": items,
            }
        )

    return sources


def _canonical_pair_items(
    current_items: list[dict[str, str | None]],
    previous_items: list[dict[str, str | None]],
) -> tuple[dict[str, dict[str, Any]], list[str]]:
    items_by_key: dict[str, dict[str, Any]] = {}
    item_order: list[str] = []

    def ensure_item(key: str, display_name: str) -> dict[str, Any]:
        if key not in items_by_key:
            items_by_key[key] = {
                "display_name": display_name,
                "aliases": [display_name],
                "current_value": None,
                "current_numeric": None,
                "previous_value": None,
                "previous_numeric": None,
            }
            item_order.append(key)
        else:
            _append_alias(items_by_key[key]["aliases"], display_name)
        return items_by_key[key]

    for item in current_items:
        key = str(item.get("key") or "")
        display_name = str(item.get("display_name") or "")
        if not key or not display_name:
            continue
        target = ensure_item(key, display_name)
        target["current_value"] = item.get("value")
        target["current_numeric"] = _parse_numeric(item.get("value"))

    for item in previous_items:
        key = str(item.get("key") or "")
        display_name = str(item.get("display_name") or "")
        if not key or not display_name:
            continue
        target = ensure_item(key, display_name)
        target["previous_value"] = item.get("value")
        target["previous_numeric"] = _parse_numeric(item.get("value"))

    return items_by_key, item_order


def _canonical_multi_period_items(period_sources: list[dict[str, Any]]) -> tuple[dict[str, dict[str, Any]], list[str]]:
    items_by_key: dict[str, dict[str, Any]] = {}
    item_order: list[str] = []

    def ensure_item(key: str, display_name: str) -> dict[str, Any]:
        if key not in items_by_key:
            items_by_key[key] = {
                "display_name": display_name,
                "aliases": [display_name],
                "current_value": None,
                "current_numeric": None,
                "previous_value": None,
                "previous_numeric": None,
                "values_by_period_key": {},
                "numeric_values_by_period_key": {},
            }
            item_order.append(key)
        else:
            _append_alias(items_by_key[key]["aliases"], display_name)
        return items_by_key[key]

    for source in period_sources:
        period_key = str(source.get("period_key") or "")
        for item in source.get("items", []):
            key = str(item.get("key") or "")
            display_name = str(item.get("display_name") or "")
            if not period_key or not key or not display_name:
                continue
            value = item.get("value")
            target = ensure_item(key, display_name)
            target["values_by_period_key"][period_key] = value
            target["numeric_values_by_period_key"][period_key] = _parse_numeric(value)
            if period_key == "current_fiscal_year":
                target["current_value"] = value
                target["current_numeric"] = _parse_numeric(value)
            elif period_key == "previous_fiscal_year":
                target["previous_value"] = value
                target["previous_numeric"] = _parse_numeric(value)

    return items_by_key, item_order


def _equity_block(table: dict[str, Any] | None, *, basis: PeriodBasis, period_meta: dict[str, Any]) -> dict[str, Any]:
    if table is None:
        return _empty_equity_block(basis, period_meta)

    matrix = _matrix(table)
    if not matrix:
        return _empty_equity_block(basis, period_meta)

    header_count = _header_row_count(matrix)
    columns = _equity_value_columns(matrix, header_count)
    columns_by_key = {column["key"]: column["meta"] for column in columns}
    column_order = [column["key"] for column in columns]

    rows_by_key: dict[str, dict[str, Any]] = {}
    row_order: list[str] = []
    used_row_keys: set[str] = set()
    block_dates: list[date] = []

    for row in matrix[header_count:]:
        row_name = _display_label(row[0] if row else "")
        if not row_name:
            continue
        block_dates.extend(_parse_dates(row_name))
        row_key = _dedupe_key(_stable_key(row_name, namespace="row"), used_row_keys)
        values_by_column_key = {
            column["key"]: _cell(row, int(column["index"]))
            for column in columns
            if _cell(row, int(column["index"])) != ""
        }
        numeric_values_by_column_key = {
            column_key: _parse_numeric(value)
            for column_key, value in values_by_column_key.items()
        }
        rows_by_key[row_key] = {
            "display_name": row_name,
            "aliases": [row_name],
            "values_by_column_key": values_by_column_key,
            "numeric_values_by_column_key": numeric_values_by_column_key,
        }
        row_order.append(row_key)

    return {
        **_with_label(period_meta, _period_block_label(block_dates)),
        "basis": basis,
        "columns_by_key": columns_by_key,
        "column_order": column_order,
        "rows_by_key": rows_by_key,
        "row_order": row_order,
    }


def _empty_equity_block(basis: PeriodBasis, period_meta: dict[str, Any]) -> dict[str, Any]:
    return {
        **_with_label(period_meta, ""),
        "basis": basis,
        "columns_by_key": {},
        "column_order": [],
        "rows_by_key": {},
        "row_order": [],
    }


def _equity_value_columns(matrix: list[list[str]], header_count: int) -> list[dict[str, Any]]:
    columns: list[dict[str, Any]] = []
    used_keys: set[str] = set()
    for col in range(1, _width(matrix)):
        display_name = _equity_column_label(matrix, header_count, col)
        if not display_name or _is_note_label(display_name):
            continue
        column_key = _dedupe_key(_stable_key(display_name, namespace="column"), used_keys)
        columns.append(
            {
                "index": col,
                "key": column_key,
                "meta": {"display_name": display_name, "aliases": [display_name]},
            }
        )
    return columns


def _merge_column_aliases(current_block: dict[str, Any], previous_block: dict[str, Any]) -> None:
    all_aliases: dict[str, list[str]] = {}
    for block in [current_block, previous_block]:
        for key, meta in block.get("columns_by_key", {}).items():
            all_aliases.setdefault(key, [])
            for alias in meta.get("aliases", []):
                _append_alias(all_aliases[key], str(alias))

    for block in [current_block, previous_block]:
        for key, aliases in all_aliases.items():
            if key in block.get("columns_by_key", {}):
                block["columns_by_key"][key]["aliases"] = aliases


def _merge_all_period_column_aliases(period_blocks: dict[str, Any]) -> None:
    all_aliases: dict[str, list[str]] = {}
    for block in period_blocks.values():
        for key, meta in block.get("columns_by_key", {}).items():
            all_aliases.setdefault(key, [])
            for alias in meta.get("aliases", []):
                _append_alias(all_aliases[key], str(alias))

    for block in period_blocks.values():
        for key, aliases in all_aliases.items():
            if key in block.get("columns_by_key", {}):
                block["columns_by_key"][key]["aliases"] = aliases


def _equity_column_label(matrix: list[list[str]], header_count: int, col: int) -> str:
    for row in reversed(matrix[:header_count]):
        label = _display_label(_cell(row, col))
        if label and label not in _HEADER_LABELS and label != "자본":
            return label
    return _display_label(_cell(matrix[0], col) if matrix else "")


def _pair_tables(
    current_tables: list[dict[str, Any]],
    previous_tables: list[dict[str, Any]],
) -> list[tuple[dict[str, Any] | None, dict[str, Any] | None]]:
    pairs: list[tuple[dict[str, Any] | None, dict[str, Any] | None]] = []
    used_previous: set[int] = set()

    for current_index, current_table in enumerate(current_tables):
        previous_index = _find_matching_table(current_table, previous_tables, used_previous)
        if previous_index is None and current_index < len(previous_tables) and current_index not in used_previous:
            previous_index = current_index
        previous_table = previous_tables[previous_index] if previous_index is not None else None
        if previous_index is not None:
            used_previous.add(previous_index)
        pairs.append((current_table, previous_table))

    for previous_index, previous_table in enumerate(previous_tables):
        if previous_index not in used_previous:
            pairs.append((None, previous_table))

    return pairs


def _find_matching_table(
    current_table: dict[str, Any],
    previous_tables: list[dict[str, Any]],
    used_previous: set[int],
) -> int | None:
    current_key = _match_key(str(current_table.get("table_title") or ""))
    if not current_key:
        return None
    for index, previous_table in enumerate(previous_tables):
        if index in used_previous:
            continue
        if _match_key(str(previous_table.get("table_title") or "")) == current_key:
            return index
    return None


def _section(master: dict[str, Any], role: str, key: str) -> SectionJson:
    candidate = (master.get(role) or {}).get(key) or {}
    return {
        "section_title": str(candidate.get("section_title") or STATEMENT_NAMES.get(key, "")),
        "tables": copy.deepcopy(candidate.get("tables") or []),
    }


def _basis_for_statement(statement_key: str) -> tuple[PeriodBasis, PeriodBasis]:
    if statement_key == "4-1":
        return "POINT_IN_TIME", "POINT_IN_TIME"
    return "YTD", "FULL_YEAR"


def _historical_period_key(offset: int) -> str:
    if offset == 0:
        return "previous_fiscal_year"
    return f"previous_fiscal_year_{offset + 1}"


def _annual_period_metadata(fiscal_year: int, *, basis: PeriodBasis) -> dict[str, Any]:
    return _period_metadata(
        label="",
        fiscal_year=fiscal_year,
        period_type="ANNUAL",
        period_end=date(fiscal_year, 12, 31).isoformat(),
        basis=basis,
    )


def _table_at_or_none(tables: list[dict[str, Any]], index: int) -> dict[str, Any] | None:
    if 0 <= index < len(tables):
        return tables[index]
    return None


def _current_period_metadata(secondary_target: TargetReport, basis: PeriodBasis) -> dict[str, Any]:
    fiscal_year = secondary_target.fiscal_year + 1
    if basis == "POINT_IN_TIME":
        period_type, period_end = _period_from_point_in_time_basis(fiscal_year)
    else:
        period_type, period_end = _period_from_ytd_basis(fiscal_year, secondary_target)
    return _period_metadata(
        label="",
        fiscal_year=fiscal_year,
        period_type=period_type,
        period_end=period_end,
        basis=basis,
    )


def _period_from_point_in_time_basis(fiscal_year: int) -> tuple[str, str]:
    # Primary annual reports reach this code only when selected_date maps to annual.
    # Periodic reports use the currently normalized primary period. The exact end
    # date is refined from labels when available; Q3 is the conservative default
    # for Oct-Dec selected_date flows.
    return "Q3", date(fiscal_year, 9, 30).isoformat()


def _period_from_ytd_basis(fiscal_year: int, secondary_target: TargetReport) -> tuple[str, str]:
    # The current fiscal year is always the year after the secondary annual report.
    # In the active selected-date mappings this can be Q1, HALF, Q3, or ANNUAL;
    # when only the secondary target is available, infer Q3 as the common periodic
    # handoff case and let descriptor-derived labels provide human context.
    return "Q3", date(fiscal_year, 9, 30).isoformat()


def _period_metadata(
    *,
    label: str,
    fiscal_year: int | None,
    period_type: str,
    period_end: str,
    basis: PeriodBasis,
) -> dict[str, Any]:
    return {
        "label": label,
        "fiscal_year": fiscal_year,
        "period_type": period_type,
        "period_end": period_end,
        "basis": basis,
    }


def _with_label(metadata: dict[str, Any], label: str) -> dict[str, Any]:
    output = dict(metadata)
    output["label"] = label or str(metadata.get("label") or "")
    inferred_year = _parse_year_from_descriptor(output["label"])
    if inferred_year is not None:
        output["fiscal_year"] = inferred_year
    inferred_dates = _parse_dates(output["label"])
    if inferred_dates:
        output["period_end"] = max(inferred_dates).isoformat()
        output["period_type"] = _infer_period_type(output["label"], output["period_end"])
    else:
        output["period_type"] = _infer_period_type(output["label"], str(output.get("period_end") or ""))
        period_end = _period_end_from_label(output["label"], output.get("fiscal_year"), output["period_type"])
        if period_end:
            output["period_end"] = period_end
    return output


def _select_period_column(descriptors: list[str], target_year: int | None) -> int | None:
    data_cols = [index for index in range(1, len(descriptors)) if not _is_note_label(descriptors[index])]
    if not data_cols:
        return None

    if target_year is not None:
        for col in data_cols:
            if str(target_year) in descriptors[col]:
                return col
    return data_cols[0]


def _parse_year_from_descriptor(descriptor: str) -> int | None:
    match = re.search(r"20\d{2}", descriptor or "")
    if match:
        return int(match.group(0))
    return None


def _infer_period_type(descriptor: str, period_end: str) -> str:
    text = descriptor or ""
    if "반기" in text or period_end.endswith("-06-30"):
        return "HALF"
    if "3분기" in text or period_end.endswith("-09-30"):
        return "Q3"
    if "1분기" in text or period_end.endswith("-03-31"):
        return "Q1"
    if "사업" in text or period_end.endswith("-12-31"):
        return "ANNUAL"
    return ""


def _period_end_from_label(label: str, fiscal_year: Any, period_type: str) -> str:
    try:
        year = int(fiscal_year)
    except (TypeError, ValueError):
        return ""
    if period_type == "Q1":
        return date(year, 3, 31).isoformat()
    if period_type == "HALF":
        return date(year, 6, 30).isoformat()
    if period_type == "Q3":
        return date(year, 9, 30).isoformat()
    if period_type == "ANNUAL":
        return date(year, 12, 31).isoformat()
    return ""


def _period_label(descriptor: str) -> str:
    parts = [
        part
        for part in (_display_label(part) for part in descriptor.split(" / "))
        if part and part not in {"누적", "3개월"}
    ]
    return " / ".join(dict.fromkeys(parts))


def _column_descriptor(matrix: list[list[str]], header_count: int, col: int) -> str:
    parts: list[str] = []
    for row in matrix[:header_count]:
        cell = _display_label(_cell(row, col))
        if cell and cell not in _HEADER_LABELS and cell not in parts:
            parts.append(cell)
    return " / ".join(parts)


def _header_row_count(matrix: list[list[str]]) -> int:
    count = 0
    for index, row in enumerate(matrix[:6]):
        if _is_header_row(row, index):
            count += 1
            continue
        break
    return max(1, count) if matrix else 0


def _is_header_row(row: list[str], row_index: int) -> bool:
    texts = [_display_label(cell) for cell in row]
    row_text = " ".join(texts)
    first = texts[0] if texts else ""
    non_first = texts[1:]
    has_amount = any(_is_amount_text(text) for text in non_first)

    if row_index == 0 and any(text in _HEADER_LABELS for text in texts):
        return True
    if _PERIOD_HINT_RE.search(row_text) and not has_amount:
        return True
    if row_index <= 2 and not first and not has_amount:
        return True
    return False


def _period_block_label(dates: list[date]) -> str:
    if not dates:
        return ""
    start = min(dates)
    end = max(dates)
    if start == end:
        return _format_date(start)
    return f"{_format_date(start)}~{_format_date(end)}"


def _stable_key(label: str, *, namespace: Literal["item", "row", "column"]) -> str:
    match_label = _canonical_match_label(label)
    if namespace == "row":
        for token, key in _SAFE_ROW_KEYS.items():
            if token in match_label:
                return key
    registry_key = _SAFE_ALIAS_KEYS.get(match_label)
    if registry_key:
        return registry_key
    digest = hashlib.sha1(match_label.encode("utf-8")).hexdigest()[:12]
    return f"{namespace}_{digest}"


def _canonical_match_label(label: str) -> str:
    text = _display_label(label)
    text = _NOTE_REF_RE.sub("", text)
    text = _PAREN_LOSS_RE.sub(r"\1", text)
    text = re.sub(r"[()\[\]{}]", "", text)
    text = re.sub(r"\s*([·ㆍ/,+\-])\s*", r"\1", text)
    text = re.sub(r"\s+", "", text)
    return text


def _dedupe_key(base_key: str, used_keys: set[str]) -> str:
    if base_key not in used_keys:
        used_keys.add(base_key)
        return base_key
    suffix = 2
    while f"{base_key}_{suffix}" in used_keys:
        suffix += 1
    key = f"{base_key}_{suffix}"
    used_keys.add(key)
    return key


def _parse_dates(text: str) -> list[date]:
    dates: list[date] = []
    for match in _DATE_RE.finditer(text or ""):
        try:
            dates.append(date(int(match.group(1)), int(match.group(2)), int(match.group(3))))
        except ValueError:
            continue
    return dates


def _parse_numeric(value: Any) -> int | float | None:
    text = _display_label(value)
    if not text or text in {"-", "―", "—", "N/A"}:
        return None
    negative = (text.startswith("(") and text.endswith(")")) or text.startswith("-")
    cleaned = re.sub(r"[^0-9.]", "", text)
    if not cleaned or cleaned == ".":
        return None
    try:
        number: int | float
        if "." in cleaned:
            number = float(cleaned)
        else:
            number = int(cleaned)
    except ValueError:
        return None
    return -number if negative else number


def _format_date(value: date) -> str:
    return value.strftime("%Y.%m.%d")


def _table_title(current_table: dict[str, Any] | None, previous_table: dict[str, Any] | None) -> str:
    source = current_table if current_table is not None else previous_table
    return str((source or {}).get("table_title") or "")


def _matrix(table: dict[str, Any]) -> list[list[str]]:
    matrix = table.get("matrix") or []
    return [[str(cell) if cell is not None else "" for cell in row] for row in matrix if isinstance(row, list)]


def _width(matrix: list[list[str]]) -> int:
    return max((len(row) for row in matrix), default=0)


def _cell(row: list[str], col: int) -> str:
    return _display_label(row[col]) if col < len(row) else ""


def _display_label(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "").strip().strip("\u3000")).strip()


def _match_key(value: str) -> str:
    return re.sub(r"\s+", " ", _display_label(value).replace("\u3000", " ")).strip()


def _append_alias(aliases: list[str], alias: str) -> None:
    if alias and alias not in aliases:
        aliases.append(alias)


def _is_note_label(value: str) -> bool:
    return _display_label(value) in {"주석", "비고"}


def _is_amount_text(value: str) -> bool:
    return bool(_AMOUNT_RE.match(_display_label(value)))
