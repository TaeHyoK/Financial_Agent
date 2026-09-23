"""Identity helpers shared by deterministic peer-resolution components."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from . import OUTPUT_ROOT
from orchestration.config import (
    agent_output_dir,
    build_run_key,
    company_from_run_key,
    normalize_date,
    safe_label,
)


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
    identities: list[RunIdentity] = []
    for company_dir in sorted(output_root.iterdir()) if output_root.exists() else []:
        if not company_dir.is_dir():
            continue
        run_key = f"{company_dir.name}_{suffix}" if suffix else ""
        if not run_key or run_key == target.run_key:
            continue
        financial_dir = agent_output_dir(output_root, run_key, "Financial")
        if not financial_dir.is_dir():
            continue
        company_name = company_from_run_key(run_key)
        if safe_label(company_name) == safe_label(target.company_name):
            continue
        has_required = (
            (financial_dir / "final_report.json").exists()
            and (agent_output_dir(output_root, run_key, "Y_Finance") / "market_full_dataset.csv").exists()
        )
        if include_partial or has_required:
            identities.append(
                RunIdentity(
                    run_key=run_key,
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
