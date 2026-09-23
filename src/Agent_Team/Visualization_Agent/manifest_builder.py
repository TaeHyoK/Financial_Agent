"""Manifest writer for Writer-selected report charts."""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any


OUTPUT_VERSION = "2.0"


def build_chart_manifest(
    company_name: str,
    run_key: str,
    source_files: dict[str, Any],
    chart_metadata: list[dict[str, Any]],
    output_path: str | Path,
) -> dict[str, Any]:
    """Build and write the generated-chart manifest."""

    manifest = {
        "agent_name": "Visualization Agent",
        "output_version": OUTPUT_VERSION,
        "target_company_name": company_name,
        "target_run_key": run_key,
        "created_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "source_files": source_files,
        "charts": chart_metadata,
    }
    save_json(output_path, manifest)
    return manifest


def save_json(path: str | Path, payload: dict[str, Any]) -> None:
    output_path = Path(path).expanduser().resolve()
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
