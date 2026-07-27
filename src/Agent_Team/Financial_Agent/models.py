"""Shared data models for the deterministic DART collection pipeline."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Any, Literal, TypedDict


ReportRole = Literal["primary", "secondary", "same_period_previous", "annual_history"]
PeriodType = Literal["q1", "half", "q3", "annual"]


class TableJson(TypedDict):
    """Public JSON schema for one parsed financial statement table."""

    table_title: str
    matrix: list[list[str]]


class SectionJson(TypedDict):
    """Public JSON schema for one financial statement subsection."""

    section_title: str
    tables: list[TableJson]


SectionMap = dict[str, SectionJson]


@dataclass(frozen=True)
class PipelineInput:
    """Normalized fields accepted from test_input.json."""

    company_code: str
    company_name: str
    ticker: str
    report_type: str
    date_range: str
    selected_date: date
    max_retries: int


@dataclass(frozen=True)
class TargetReport:
    """Theoretical fiscal report target derived from selected_date."""

    role: ReportRole
    fiscal_year: int
    period_type: PeriodType
    period_end: date
    dart_detail_type: str
    report_keyword: str

    @property
    def is_periodic(self) -> bool:
        """Return True when the report is Q1, half-year, or Q3."""

        return self.period_type != "annual"


@dataclass(frozen=True)
class Filing:
    """DART filing selected for a target fiscal period."""

    rcept_no: str
    report_nm: str
    rcept_dt: str
    corp_code: str = ""
    corp_name: str = ""

    @classmethod
    def from_api_item(cls, item: dict[str, Any]) -> "Filing":
        """Create a Filing from one DART list.json item."""

        return cls(
            rcept_no=str(item.get("rcept_no") or "").strip(),
            report_nm=str(item.get("report_nm") or "").strip(),
            rcept_dt=str(item.get("rcept_dt") or "").strip(),
            corp_code=str(item.get("corp_code") or "").strip(),
            corp_name=str(item.get("corp_name") or "").strip(),
        )
