"""Run configuration helpers for Agent_Team orchestration."""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[2]
SRC_ROOT = PROJECT_ROOT / "src"
OUTPUT_ROOT = PROJECT_ROOT / "Output_total"
CONFIG_ROOT = PROJECT_ROOT / "configs"
DEFAULT_CONFIG_PATH = CONFIG_ROOT / "company_input.json"
DEFAULT_ENV_FILE = CONFIG_ROOT / ".env"
DEFAULT_NEWS_CONFIG_PATH = CONFIG_ROOT / "news_default.yaml"


@dataclass(frozen=True)
class RunConfig:
    """Common company/date contract shared by the three agent teams."""

    config_path: Path
    company_code: str
    company_name: str
    ticker: str
    corp_code: str
    date_range: str
    selected_date: str
    raw: dict[str, Any]

    @property
    def selected_date_iso(self) -> str:
        return date_to_iso(self.selected_date)

    @property
    def start_date(self) -> str:
        return date_range_bounds(self.date_range)[0]

    @property
    def end_date(self) -> str:
        return date_range_bounds(self.date_range)[1]

    @property
    def run_key(self) -> str:
        return build_run_key(self.company_name, self.selected_date, self.company_code)

    @property
    def llm_model(self) -> str:
        return str(self.raw.get("llm_model") or "").strip()


def load_run_config(path: str | Path) -> RunConfig:
    """Load the common JSON config used by all team runners."""

    config_path = Path(path).expanduser().resolve()
    payload = json.loads(config_path.read_text(encoding="utf-8"))
    company_code = str(payload.get("company_code") or payload.get("corp_code") or "").strip()
    corp_code = str(payload.get("corp_code") or company_code).strip()
    selected_date = normalize_date(payload.get("selected_date"))
    date_range = str(payload.get("date_range") or "").strip()
    if not date_range:
        raise ValueError("date_range is required in run config.")
    if not company_code:
        raise ValueError("company_code or corp_code is required in run config.")

    return RunConfig(
        config_path=config_path,
        company_code=company_code,
        company_name=str(payload.get("company_name") or company_code).strip(),
        ticker=str(payload.get("ticker") or "").strip(),
        corp_code=corp_code,
        date_range=date_range,
        selected_date=selected_date,
        raw=payload,
    )


def normalize_date(value: Any) -> str:
    """Return YYYYMMDD for supported date inputs."""

    if isinstance(value, datetime):
        return value.strftime("%Y%m%d")
    digits = "".join(character for character in str(value or "") if character.isdigit())
    if len(digits) != 8:
        raise ValueError("date must be YYYYMMDD or YYYY-MM-DD.")
    return digits


def date_to_iso(value: Any) -> str:
    normalized = normalize_date(value)
    return f"{normalized[:4]}-{normalized[4:6]}-{normalized[6:]}"


def date_range_bounds(value: str) -> tuple[str, str]:
    parts = str(value).split("-", 1)
    if len(parts) != 2:
        raise ValueError("date_range must be YYYYMMDD-YYYYMMDD.")
    return normalize_date(parts[0]), normalize_date(parts[1])


def safe_label(value: str | None, fallback: str = "company") -> str:
    label = str(value or fallback).strip() or fallback
    for character in ('\\', '/', ':', '*', '?', '"', '<', '>', '|'):
        label = label.replace(character, "_")
    return "_".join(label.split())


def build_run_key(company_name: str | None, selected_date: Any, fallback: str | None = None) -> str:
    return f"{safe_label(company_name, fallback or 'company')}_{normalize_date(selected_date)}"


def load_project_env(env_file: str | Path | None = None, *, override: bool = False) -> dict[str, Any]:
    """Load the shared project env file without exposing secret values.

    The project-wide default is ``configs/.env``. If a caller passes an explicit
    env file, that path is used instead. Values are loaded into ``os.environ`` so
    downstream OpenAI clients can use the standard ``OPENAI_API_KEY`` variable.
    """

    resolved = Path(env_file).expanduser().resolve() if env_file else DEFAULT_ENV_FILE
    status = {
        "env_file": str(resolved),
        "env_file_exists": resolved.exists(),
        "openai_api_key_loaded": bool(os.getenv("OPENAI_API_KEY")),
        "loader": "not_loaded",
    }
    if not resolved.exists():
        return status

    loaded = False
    try:
        from dotenv import load_dotenv

        loaded = bool(load_dotenv(resolved, override=override))
        status["loader"] = "python-dotenv"
    except Exception:
        loaded = _load_env_file_manually(resolved, override=override)
        status["loader"] = "manual"

    status["loaded"] = loaded
    status["openai_api_key_loaded"] = bool(os.getenv("OPENAI_API_KEY"))
    return status


def _load_env_file_manually(path: Path, *, override: bool) -> bool:
    loaded = False
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if not key:
            continue
        if override or key not in os.environ:
            os.environ[key] = value
            loaded = True
    return loaded
