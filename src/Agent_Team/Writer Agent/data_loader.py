"""Input loading helpers for the Writer Agent."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pandas as pd


def ensure_file_exists(path: str | Path, label: str) -> Path:
    resolved = Path(path).expanduser().resolve()
    if not resolved.exists():
        raise FileNotFoundError(f"{label} file not found: {resolved}")
    if not resolved.is_file():
        raise FileNotFoundError(f"{label} path is not a file: {resolved}")
    return resolved


def load_json(path: str | Path, label: str) -> dict[str, Any]:
    resolved = ensure_file_exists(path, label)
    with resolved.open("r", encoding="utf-8") as file:
        payload = json.load(file)
    if not isinstance(payload, dict):
        raise ValueError(f"{label} must be a JSON object: {resolved}")
    return payload


def load_optional_text(path: str | Path) -> str:
    resolved = Path(path).expanduser().resolve()
    if not resolved.exists():
        return ""
    return resolved.read_text(encoding="utf-8")


def load_optional_market_csv(path: str | Path) -> pd.DataFrame | None:
    resolved = Path(path).expanduser().resolve()
    if not resolved.exists():
        return None
    df = pd.read_csv(resolved)
    if "date" in df.columns:
        df["date"] = pd.to_datetime(df["date"], errors="coerce")
    return df


def get_nested(payload: dict[str, Any], dotted_path: str, default: Any = None) -> Any:
    current: Any = payload
    for part in dotted_path.split("."):
        if not isinstance(current, dict) or part not in current:
            return default
        current = current[part]
    return current


def save_json(path: str | Path, payload: Any) -> None:
    resolved = Path(path).expanduser().resolve()
    resolved.parent.mkdir(parents=True, exist_ok=True)
    resolved.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def write_text(path: str | Path, content: str) -> None:
    resolved = Path(path).expanduser().resolve()
    resolved.parent.mkdir(parents=True, exist_ok=True)
    resolved.write_text(content, encoding="utf-8")
