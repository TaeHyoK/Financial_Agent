"""Identity helpers shared by deterministic peer-resolution components."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from . import OUTPUT_ROOT


@dataclass(frozen=True)
class RunIdentity:
    """Company/date identity used to find existing output reports."""

    run_key: str
    company_name: str
    selected_date: str | None = None
    ticker: str | None = None
    corp_code: str | None = None
    stock_code: str | None = None


def discover_competitor_identities(
    *,
    output_root: Path = OUTPUT_ROOT,
    target: RunIdentity,
    selected_date: str | None = None,
    include_partial: bool = False,
) -> list[RunIdentity]:
    """Find same-date peer run keys from existing deterministic outputs."""

    output_root = output_root.expanduser().resolve()
    suffix = normalize_date(selected_date or target.selected_date) if (selected_date or target.selected_date) else None
    financial_root = output_root / "Financial"
    if not financial_root.exists():
        return []
    identities: list[RunIdentity] = []
    for child in sorted(financial_root.iterdir()):
        if not child.is_dir() or child.name == target.run_key:
            continue
        if suffix and not child.name.endswith(f"_{suffix}"):
            continue
        company_name = company_from_run_key(child.name)
        if safe_label(company_name) == safe_label(target.company_name):
            continue
        has_required = (
            (child / "final_report.json").exists()
            and (output_root / "Y_Finance" / child.name / "market_full_dataset.csv").exists()
        )
        if include_partial or has_required:
            identities.append(
                RunIdentity(
                    run_key=child.name,
                    company_name=company_name,
                    selected_date=suffix,
                )
            )
    return identities


def load_identity_from_config(path: Path) -> RunIdentity:
    """Read a company config JSON and convert it to a RunIdentity."""

    payload = json.loads(path.expanduser().resolve().read_text(encoding="utf-8"))
    selected_date = normalize_date(payload.get("selected_date"))
    company_name = str(payload.get("company_name") or payload.get("company_code") or "company").strip()
    ticker = str(payload.get("ticker") or "").strip() or None
    corp_code = str(payload.get("corp_code") or payload.get("company_code") or "").strip() or None
    stock_code = str(payload.get("stock_code") or "").strip() or (ticker.split(".", 1)[0] if ticker else None)
    return RunIdentity(
        run_key=build_run_key(company_name, selected_date, corp_code),
        company_name=company_name,
        selected_date=selected_date,
        ticker=ticker,
        corp_code=corp_code,
        stock_code=stock_code,
    )


def normalize_date(value: Any) -> str:
    """Return YYYYMMDD for supported date inputs."""

    digits = "".join(character for character in str(value or "") if character.isdigit())
    if len(digits) != 8:
        raise ValueError("date must be YYYYMMDD or YYYY-MM-DD.")
    return digits


def safe_label(value: str | None, fallback: str = "company") -> str:
    """Sanitize labels for run-key path fragments."""

    label = str(value or fallback).strip() or fallback
    for character in ('\\', '/', ':', '*', '?', '"', '<', '>', '|'):
        label = label.replace(character, "_")
    return "_".join(label.split())


def build_run_key(company_name: str | None, selected_date: Any, fallback: str | None = None) -> str:
    """Build the run-key shape used by orchestration."""

    return f"{safe_label(company_name, fallback or 'company')}_{normalize_date(selected_date)}"


def company_from_run_key(run_key: str) -> str:
    """Infer company name from a run key."""

    match = re.match(r"^(?P<name>.+)_(?P<date>\d{8})$", run_key)
    return match.group("name") if match else run_key
