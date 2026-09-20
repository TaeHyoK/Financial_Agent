"""Date and horizon settings shared by generation and evaluation."""

from __future__ import annotations

import re
from datetime import datetime


REPORT_DATES = {
    "hyundai_mobis": "20251103",
    "s_oil": "20251104",
    "skbiopharm": "20251106",
    "bgf_retail": "20251107",
    "amorepacific": "20251107",
    "coway": "20251110",
}

PROTOCOLS = {
    "legacy": {
        "version": "legacy_20251031",
        "news_window": "3m",
        "decision_horizon_profile": "short_term",
        "decision_horizon": "1개월",
        "suite_id": "ablation_20251031",
    },
    "annual": {
        "version": "annual_report_dates_v2_common_contracts",
        "news_window": "1y",
        "decision_horizon_profile": "annual",
        "decision_horizon": "12개월",
        "suite_id": "ablation_annual_report_dates",
    },
}


def validate_selected_date(value: str) -> str:
    if not re.fullmatch(r"\d{8}", value):
        raise ValueError("selected date must be YYYYMMDD")
    datetime.strptime(value, "%Y%m%d")
    return value


def resolve_selected_date(company_key: str, value: str | None) -> str:
    """Resolve the explicit 'report' mode without changing historical defaults."""
    if value == "report":
        return REPORT_DATES[company_key]
    return validate_selected_date(value or "20251031")


def protocol_date_mode(protocol: str, override: str | None) -> str:
    if override:
        if override != "report":
            validate_selected_date(override)
        return override
    return "report" if protocol == "annual" else "20251031"
