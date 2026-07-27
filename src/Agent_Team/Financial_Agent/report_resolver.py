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


def build_primary_target(selected_date: date) -> TargetReport:
    """Map selected_date to the closest available fiscal report target."""

    primary, _ = build_targets(selected_date)
    return primary


def build_available_report_candidates(selected_date: date) -> list[TargetReport]:
    """Return regular-report targets in descending fiscal-period order."""

    candidates: list[TargetReport] = []
    for fiscal_year in range(selected_date.year, selected_date.year - 3, -1):
        for period_type, month, day, detail_type, keyword in (
            ("annual", 12, 31, "A001", "사업보고서"),
            ("q3", 9, 30, "A003", "분기보고서"),
            ("half", 6, 30, "A002", "반기보고서"),
            ("q1", 3, 31, "A003", "분기보고서"),
        ):
            period_end = date(fiscal_year, month, day)
            if period_end > selected_date:
                continue
            candidates.append(
                TargetReport(
                    role="primary",
                    fiscal_year=fiscal_year,
                    period_type=period_type,  # type: ignore[arg-type]
                    period_end=period_end,
                    dart_detail_type=detail_type,
                    report_keyword=keyword,
                )
            )
    return sorted(candidates, key=lambda target: target.period_end, reverse=True)


def build_same_period_previous_target(primary_target: TargetReport) -> TargetReport | None:
    """Build the prior-year target with the same fiscal-period basis."""

    if not primary_target.is_periodic:
        return None
    return replace(
        primary_target,
        role="same_period_previous",
        fiscal_year=primary_target.fiscal_year - 1,
        period_end=primary_target.period_end.replace(year=primary_target.period_end.year - 1),
    )


def resolve_report_set(
    *,
    client: DartClient,
    company_code: str,
    selected_date: date,
) -> dict[str, tuple[TargetReport, Filing]]:
    """Resolve the latest filing, its prior-year peer, and annual history."""

    primary_target, primary_filing = resolve_latest_available_report(
        client=client,
        company_code=company_code,
        selected_date=selected_date,
    )
    resolved: dict[str, tuple[TargetReport, Filing]] = {
        "primary": (primary_target, primary_filing),
    }

    same_period_target = build_same_period_previous_target(primary_target)
    if same_period_target is not None:
        try:
            resolved["same_period_previous"] = (
                same_period_target,
                resolve_single_report(
                    client,
                    company_code,
                    same_period_target,
                    as_of_date=selected_date,
                ),
            )
        except LookupError:
            pass

    annual_target, annual_filing = resolve_latest_available_annual_report(
        client=client,
        company_code=company_code,
        selected_date=selected_date,
    )
    if annual_filing.rcept_no != primary_filing.rcept_no:
        resolved["annual_history"] = (annual_target, annual_filing)

    return resolved


def resolve_latest_available_report(
    *,
    client: DartClient,
    company_code: str,
    selected_date: date,
) -> tuple[TargetReport, Filing]:
    """Find the latest regular report actually filed by the selected date."""

    for target in build_available_report_candidates(selected_date):
        try:
            filing = resolve_single_report(
                client,
                company_code,
                target,
                as_of_date=selected_date,
            )
        except LookupError:
            continue
        return target, filing
    raise LookupError(f"No regular DART filing was available by {selected_date.isoformat()}.")


def resolve_latest_available_annual_report(
    *,
    client: DartClient,
    company_code: str,
    selected_date: date,
) -> tuple[TargetReport, Filing]:
    """Find the latest annual report actually filed by the selected date."""

    for fiscal_year in range(selected_date.year - 1, selected_date.year - 5, -1):
        target = _annual_target("annual_history", fiscal_year)
        try:
            filing = resolve_single_report(
                client,
                company_code,
                target,
                as_of_date=selected_date,
            )
        except LookupError:
            continue
        return target, filing
    raise LookupError(f"No annual DART filing was available by {selected_date.isoformat()}.")


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


def resolve_primary_report(
    *,
    client: DartClient,
    company_code: str,
    primary_target: TargetReport,
    today: date | None = None,
) -> dict[str, tuple[TargetReport, Filing]]:
    """Resolve only the closest primary report."""

    return {
        "primary": (primary_target, resolve_single_report(client, company_code, primary_target, today=today)),
    }


def resolve_single_report(
    client: DartClient,
    company_code: str,
    target: TargetReport,
    *,
    today: date | None = None,
    as_of_date: date | None = None,
) -> Filing:
    """Resolve one target report to a DART receipt number."""

    if not company_code:
        raise ValueError("company_code is required.")
    cutoff = as_of_date or today or date.today()
    bgn_de, end_de = _search_window(target, cutoff)
    filings = client.list_filings(
        corp_code=company_code,
        bgn_de=bgn_de,
        end_de=end_de,
        pblntf_detail_ty=target.dart_detail_type,
    )
    selected = select_latest_valid_filing(filings, target, as_of_date=cutoff)
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
