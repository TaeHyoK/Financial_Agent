"""Atomic artifact writers for a reproducible Single-LLM run."""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from typing import Any


_OWNED_ARTIFACT_NAMES = {
    "config_resolved.json",
    "input_bundle.json",
    "llm_usage_manifest.jsonl",
    "report.html",
    "report.json",
    "request.json",
    "request_budget.json",
    "run_manifest.json",
    "source_manifest.json",
    "temporal_validation.json",
    "validation.json",
}


def write_json(path: str | Path, payload: Any) -> Path:
    target = Path(path).expanduser().resolve()
    text = json.dumps(payload, ensure_ascii=False, indent=2, allow_nan=False) + "\n"
    return write_text(target, text)


def write_text(path: str | Path, text: str) -> Path:
    target = Path(path).expanduser().resolve()
    target.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{target.name}.",
        suffix=".tmp",
        dir=target.parent,
        text=True,
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        temporary.replace(target)
    except Exception:
        temporary.unlink(missing_ok=True)
        raise
    return target


def ensure_output_directory(path: str | Path, *, overwrite: bool) -> Path:
    target = Path(path).expanduser().resolve()
    if target.exists() and any(target.iterdir()):
        if not overwrite:
            raise FileExistsError(
                f"Output directory is not empty: {target}. Use --overwrite to replace exact files."
            )
        unknown = [item for item in target.iterdir() if item.name not in _OWNED_ARTIFACT_NAMES]
        if unknown:
            raise FileExistsError(
                f"Refusing to overwrite a directory with unowned files: {unknown}"
            )
        for item in target.iterdir():
            if item.is_dir():
                raise IsADirectoryError(f"Refusing to remove unexpected directory: {item}")
            item.unlink()
    target.mkdir(parents=True, exist_ok=True)
    return target


__all__ = ["ensure_output_directory", "write_json", "write_text"]
