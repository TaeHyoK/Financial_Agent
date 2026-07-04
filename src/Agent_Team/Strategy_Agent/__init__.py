"""Strategy guideline agent package."""

from __future__ import annotations

from pathlib import Path


AGENT_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = AGENT_DIR.parents[2]
OUTPUT_ROOT = PROJECT_ROOT / "Output_total"
DEFAULT_TARGET_CONFIG = PROJECT_ROOT / "configs" / "company_input.json"

__all__ = ["AGENT_DIR", "PROJECT_ROOT", "OUTPUT_ROOT", "DEFAULT_TARGET_CONFIG"]
