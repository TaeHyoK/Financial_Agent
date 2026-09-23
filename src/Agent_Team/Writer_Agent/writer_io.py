"""Uniquely named JSON and text I/O helpers for the Writer Agent."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from shared.jsonio import atomic_write_text as _atomic_write_text


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


def save_json(path: str | Path, payload: Any) -> None:
    resolved = Path(path).expanduser().resolve()
    _atomic_write_text(resolved, json.dumps(payload, ensure_ascii=False, indent=2))


def write_text(path: str | Path, content: str) -> None:
    resolved = Path(path).expanduser().resolve()
    _atomic_write_text(resolved, content)
