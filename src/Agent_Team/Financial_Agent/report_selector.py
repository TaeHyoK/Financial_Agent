"""Deterministic filing matching and selection rules."""

from __future__ import annotations

import re
from datetime import date

try:
    from .models import Filing, TargetReport
except ImportError:  # pragma: no cover - supports direct script execution
    from models import Filing, TargetReport


_PERIOD_RE = re.compile(r"\((20\d{2})\s*[./-]\s*(0[1-9]|1[0-2])\)")
_ANY_PERIOD_RE = re.compile(r"(20\d{2})\s*[./-]\s*(0[1-9]|1[0-2])")


def parse_period_end_from_report_name(report_nm: str) -> date | None:
    """Parse the fiscal period end encoded in a DART report title."""

    match = _PERIOD_RE.search(report_nm or "") or _ANY_PERIOD_RE.search(report_nm or "")
    if not match:
        return None
    year = int(match.group(1))
    month = int(match.group(2))
    day = 31
    if month == 6:
        day = 30
    if month == 9:
        day = 30
    return date(year, month, day)


def is_valid_periodic_filing(filing: Filing, target: TargetReport) -> bool:
    """Return True when a filing title matches the target fiscal period."""

    report_nm = filing.report_nm
    if not filing.rcept_no or not report_nm:
        return False
    if "철회" in report_nm:
        return False
    if target.report_keyword and target.report_keyword not in report_nm:
        return False
    return parse_period_end_from_report_name(report_nm) == target.period_end


def select_latest_valid_filing(filings: list[Filing], target: TargetReport) -> Filing:
    """Select the latest valid filing for the same target fiscal period."""

    matches = [filing for filing in filings if is_valid_periodic_filing(filing, target)]
    if not matches:
        expected = f"{target.report_keyword} ({target.period_end:%Y.%m})"
        raise LookupError(f"No DART filing matched target period: {expected}")
    return max(matches, key=lambda item: (_date_int(item.rcept_dt), item.rcept_no))


def _date_int(value: str) -> int:
    digits = re.sub(r"\D", "", value or "")
    return int(digits) if digits.isdigit() else 0
