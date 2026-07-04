"""Pipeline helpers."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml


def load_config(config_path: str | Path) -> dict[str, Any]:
    path = Path(config_path)
    if not path.exists():
        raise FileNotFoundError(f"Config not found: {config_path}")
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def resolve_data_root(config: dict[str, Any]) -> Path:
    return Path(config.get("data_root", "./artifacts"))
