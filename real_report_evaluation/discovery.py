"""Discover the fixed six-company reference and Full-report matrix."""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from ablation_evaluation.inputs import (
    discover_frozen_full_r1,
    discover_suite_artifacts,
)
from ablation_suite.config import COMPANY_SPECS, COMPANY_BY_KEY, DEFAULT_SOURCE_ROOTS
from ablation_suite.protocol import resolve_selected_date


REFERENCE_ALIASES = {
    "skbiopharm": ("sk바이오팜",),
    "amorepacific": ("아모레퍼시픽",),
    "coway": ("코웨이",),
    "hyundai_mobis": ("현대모비스",),
    "bgf_retail": ("bgf리테일",),
    "s_oil": ("soil", "s-oil", "s_oil", "에쓰오일", "에스오일"),
}


@dataclass(frozen=True)
class ComparisonInput:
    condition: str
    company_key: str
    company_name: str
    replicate: int
    reference_pdf: Path
    reference_date: str
    candidate_html: Path
    selected_date: str

    @property
    def date_gap_days(self) -> int:
        reference = datetime.strptime(self.reference_date, "%Y%m%d").date()
        selected = datetime.strptime(self.selected_date, "%Y%m%d").date()
        return (reference - selected).days


def discover_inputs(
    *,
    real_report_dir: str | Path,
    full_suite: str | Path,
    ablation_suite: str | Path,
    selected_date: str,
    replicates: tuple[int, ...],
    conditions: tuple[str, ...],
    company_keys: tuple[str, ...] | None = None,
) -> list[ComparisonInput]:
    reference_root = Path(real_report_dir).expanduser().resolve()
    suite_root = Path(full_suite).expanduser().resolve()
    companies = select_companies(company_keys)
    references = _discover_references(reference_root, companies=companies)

    ablation_root = Path(ablation_suite).expanduser().resolve()
    supported = {"full", "no_peer", "no_subdata", "unified_domain_team", "random_news"}
    unknown = sorted(set(conditions) - supported)
    if unknown:
        raise ValueError(f"Unsupported conditions: {unknown}")

    artifacts = []
    if "full" in conditions:
        if selected_date != "report":
            artifacts.extend(
                discover_frozen_full_r1(DEFAULT_SOURCE_ROOTS, selected_date=selected_date)
            )
        artifacts.extend(
            discover_suite_artifacts(
                suite_root,
                conditions=("full",),
                selected_date=selected_date,
            )
        )
    ablations = tuple(condition for condition in conditions if condition != "full")
    if ablations:
        artifacts.extend(
            discover_suite_artifacts(
                ablation_root,
                conditions=ablations,
                selected_date=selected_date,
            )
        )
    by_key = {
        (artifact.condition, artifact.company_key, artifact.replicate): artifact
        for artifact in artifacts
    }

    comparisons: list[ComparisonInput] = []
    missing: list[str] = []
    for condition in conditions:
        for company in companies:
            reference_pdf, reference_date = references[company.key]
            company_date = resolve_selected_date(company.key, selected_date)
            if selected_date == "report" and reference_date != company_date:
                raise ValueError(f"Reference date differs from protocol for {company.name}: {reference_date} != {company_date}")
            for replicate in replicates:
                artifact = by_key.get((condition, company.key, replicate))
                if artifact is None:
                    missing.append(
                        f"{condition}/{company.name}/replicate_{replicate:02d}"
                    )
                    continue
                comparisons.append(
                    ComparisonInput(
                        condition=condition,
                        company_key=company.key,
                        company_name=company.name,
                        replicate=replicate,
                        reference_pdf=reference_pdf,
                        reference_date=reference_date,
                        candidate_html=artifact.report,
                        selected_date=company_date,
                    )
                )
    if missing:
        raise ValueError("Missing Full report artifacts: " + ", ".join(missing))
    expected = len(conditions) * len(companies) * len(replicates)
    if len(comparisons) != expected:
        raise AssertionError(f"Expected {expected} comparisons, found {len(comparisons)}")
    return comparisons


def select_companies(company_keys: tuple[str, ...] | None):
    if company_keys is None:
        return COMPANY_SPECS
    if not company_keys or len(set(company_keys)) != len(company_keys):
        raise ValueError('Company selection must be nonempty and unique')
    unknown = set(company_keys) - set(COMPANY_BY_KEY)
    if unknown:
        raise ValueError(f'Unknown company keys: {sorted(unknown)}')
    return tuple(COMPANY_BY_KEY[key] for key in company_keys)


def _discover_references(reference_root: Path, *, companies=COMPANY_SPECS) -> dict[str, tuple[Path, str]]:
    if not reference_root.is_dir():
        raise ValueError(f"Real-report directory does not exist: {reference_root}")
    pdfs = sorted(reference_root.glob("*.pdf"))
    result: dict[str, tuple[Path, str]] = {}
    for company in companies:
        aliases = REFERENCE_ALIASES[company.key]
        matches = [
            path
            for path in pdfs
            if any(alias.casefold() in path.stem.casefold() for alias in aliases)
        ]
        if len(matches) != 1:
            raise ValueError(
                f"Expected exactly one reference PDF for {company.name}; found: "
                + ", ".join(str(path) for path in matches)
            )
        date_match = re.match(r"(\d{8})", matches[0].name)
        if date_match is None:
            raise ValueError(
                f"Reference filename must start with YYYYMMDD: {matches[0].name}"
            )
        result[company.key] = (matches[0].resolve(), date_match.group(1))
    return result
