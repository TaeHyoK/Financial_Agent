"""Resolve theoretical fiscal report targets from selected_date."""

from __future__ import annotations

import json
import re
from dataclasses import replace
from datetime import date, datetime
from pathlib import Path
from typing import Any

try:
    from .dart_client import DartClient
    from .models import Filing, PipelineInput, TargetReport
    from .report_selector import select_latest_valid_filing
except ImportError:  # pragma: no cover - supports direct script execution
    from dart_client import DartClient
    from models import Filing, PipelineInput, TargetReport
    from report_selector import select_latest_valid_filing


def load_pipeline_input(path: Path) -> PipelineInput:
    """Load and validate the supported fields from test_input.json."""

    with path.open("r", encoding="utf-8") as file:
        payload = json.load(file)
    if not isinstance(payload, dict):
        raise ValueError("Input JSON must be an object.")

    selected_date = _parse_selected_date(str(payload.get("selected_date") or ""))
    max_retries = _parse_max_retries(payload.get("max_retries", 2))

    return PipelineInput(
        company_code=str(payload.get("company_code") or "").strip(),
        company_name=str(payload.get("company_name") or "").strip(),
        ticker=str(payload.get("ticker") or "").strip(),
        report_type=str(payload.get("report_type") or "").strip(),
        date_range=str(payload.get("date_range") or "").strip(),
        selected_date=selected_date,
        max_retries=max_retries,
    )


def build_targets(selected_date: date) -> tuple[TargetReport, TargetReport]:
    """Map selected_date to primary and secondary theoretical fiscal periods."""

    year = selected_date.year
    month = selected_date.month

    if 1 <= month <= 3:
        primary = _annual_target("primary", year - 1)
        secondary = _annual_target("secondary", year - 2)
    elif 4 <= month <= 6:
        primary = TargetReport(
            role="primary",
            fiscal_year=year,
            period_type="q1",
            period_end=date(year, 3, 31),
            dart_detail_type="A003",
            report_keyword="분기보고서",
        )
        secondary = _annual_target("secondary", year - 1)
    elif 7 <= month <= 9:
        primary = TargetReport(
            role="primary",
            fiscal_year=year,
            period_type="half",
            period_end=date(year, 6, 30),
            dart_detail_type="A002",
            report_keyword="반기보고서",
        )
        secondary = _annual_target("secondary", year - 1)
    else:
        primary = TargetReport(
            role="primary",
            fiscal_year=year,
            period_type="q3",
            period_end=date(year, 9, 30),
            dart_detail_type="A003",
            report_keyword="분기보고서",
        )
        secondary = _annual_target("secondary", year - 1)

    return primary, secondary


def resolve_reports(
    *,
    client: DartClient,
    company_code: str,
    primary_target: TargetReport,
    secondary_target: TargetReport,
    today: date | None = None,
) -> dict[str, tuple[TargetReport, Filing]]:
    """Resolve both target reports before document collection begins."""

    return {
        "primary": (primary_target, resolve_single_report(client, company_code, primary_target, today=today)),
        "secondary": (secondary_target, resolve_single_report(client, company_code, secondary_target, today=today)),
    }


def resolve_single_report(
    client: DartClient,
    company_code: str,
    target: TargetReport,
    *,
    today: date | None = None,
) -> Filing:
    """Resolve one target report to a DART receipt number."""

    if not company_code:
        raise ValueError("company_code is required.")
    today = today or date.today()
    bgn_de, end_de = _search_window(target, today)
    filings = client.list_filings(
        corp_code=company_code,
        bgn_de=bgn_de,
        end_de=end_de,
        pblntf_detail_ty=target.dart_detail_type,
    )
    selected = select_latest_valid_filing(filings, target)
    if not selected.corp_code:
        selected = replace(selected, corp_code=company_code)
    return selected


def _annual_target(role: str, fiscal_year: int) -> TargetReport:
    return TargetReport(
        role=role,  # type: ignore[arg-type]
        fiscal_year=fiscal_year,
        period_type="annual",
        period_end=date(fiscal_year, 12, 31),
        dart_detail_type="A001",
        report_keyword="사업보고서",
    )


def _search_window(target: TargetReport, today: date) -> tuple[str, str]:
    if target.period_type == "annual":
        start_year = target.fiscal_year + 1
    else:
        start_year = target.fiscal_year

    start = date(start_year, 1, 1)
    if start > today:
        start = date(target.fiscal_year, 1, 1)
    return start.strftime("%Y%m%d"), today.strftime("%Y%m%d")


def _parse_selected_date(value: str) -> date:
    digits = re.sub(r"\D", "", value or "")
    if len(digits) != 8:
        raise ValueError("selected_date must be YYYYMMDD or YYYY-MM-DD.")
    return datetime.strptime(digits, "%Y%m%d").date()


def _parse_max_retries(value: Any) -> int:
    try:
        return max(0, int(value))
    except (TypeError, ValueError):
        return 2
