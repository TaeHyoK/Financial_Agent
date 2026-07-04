"""Financial Agent package paths and public metadata."""

from __future__ import annotations

from datetime import date, datetime
from pathlib import Path
from typing import Any


AGENT_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = AGENT_DIR.parents[2]
DEFAULT_ENV_FILE = PROJECT_ROOT / "configs" / ".env"
DEFAULT_CONFIG_PATH = PROJECT_ROOT / "configs" / "company_input.json"
DEFAULT_OUTPUT_ROOT = PROJECT_ROOT / "Output_total" / "Financial"
DEFAULT_RUN_KEY: str | None = None
DEFAULT_RUN_OUTPUT_DIR: Path | None = None
DEFAULT_AGENT_PIPELINE_OUTPUT_DIR: Path | None = None
DEFAULT_FINANCIAL_INDEX_PATH = AGENT_DIR / "financial_index.json"


def normalize_run_date(selected_date: Any) -> str:
    """Return YYYYMMDD for supported run date inputs."""

    if isinstance(selected_date, datetime):
        return selected_date.strftime("%Y%m%d")
    if isinstance(selected_date, date):
        return selected_date.strftime("%Y%m%d")

    value = str(selected_date or "").strip()
    if not value:
        raise ValueError("selected_date is required to build a financial run key.")

    if len(value) == 10 and value[4] == "-" and value[7] == "-":
        return datetime.strptime(value, "%Y-%m-%d").strftime("%Y%m%d")

    compact = "".join(character for character in value if character.isdigit())
    if len(compact) == 8:
        return compact

    raise ValueError("selected_date must be YYYYMMDD or YYYY-MM-DD.")


def normalize_run_company(company_name: str | None, company_code: str | None = None) -> str:
    """Return a filesystem-safe company label for output folders."""

    company = str(company_name or company_code or "financial").strip() or "financial"
    return company.replace("/", "_").replace("\\", "_")


def build_run_key(company_name: str | None, selected_date: Any, company_code: str | None = None) -> str:
    """Build the shared per-company output key: <company>_<YYYYMMDD>."""

    return f"{normalize_run_company(company_name, company_code)}_{normalize_run_date(selected_date)}"


def resolve_run_output_dir(
    company_name: str | None,
    selected_date: Any,
    company_code: str | None = None,
    output_root: str | Path = DEFAULT_OUTPUT_ROOT,
) -> Path:
    """Resolve Output_total/Financial/<company>_<YYYYMMDD>."""

    return (Path(output_root).expanduser().resolve() / build_run_key(company_name, selected_date, company_code)).resolve()


def resolve_agent_pipeline_output_dir(
    company_name: str | None,
    selected_date: Any,
    company_code: str | None = None,
    output_root: str | Path = DEFAULT_OUTPUT_ROOT,
) -> Path:
    """Resolve Output_total/Financial/<company>_<YYYYMMDD>/agent_pipeline."""

    return resolve_run_output_dir(company_name, selected_date, company_code, output_root) / "agent_pipeline"

__all__ = [
    "AGENT_DIR",
    "PROJECT_ROOT",
    "DEFAULT_RUN_KEY",
    "DEFAULT_ENV_FILE",
    "DEFAULT_CONFIG_PATH",
    "DEFAULT_OUTPUT_ROOT",
    "DEFAULT_RUN_OUTPUT_DIR",
    "DEFAULT_AGENT_PIPELINE_OUTPUT_DIR",
    "DEFAULT_FINANCIAL_INDEX_PATH",
    "normalize_run_date",
    "normalize_run_company",
    "build_run_key",
    "resolve_run_output_dir",
    "resolve_agent_pipeline_output_dir",
]
