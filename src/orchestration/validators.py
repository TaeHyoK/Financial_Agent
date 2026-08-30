"""Output validators for the integrated Agent_Team run."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .paths import RunPaths


def validate_outputs(paths: RunPaths) -> dict[str, Any]:
    financial = {
        "dart_main": file_status(paths.dart_main),
        "dart_lightweight": file_status(paths.dart_lightweight),
        "analyst_report": file_status(paths.financial_analyst_report),
        "final_report": file_status(paths.financial_final_report),
    }
    news = {
        "handoff": file_status(paths.news_handoff),
        "final_report": file_status(paths.news_final_report),
    }
    yfinance = {
        "market_summary": file_status(paths.market_summary),
        "market_summary_dated": file_status(paths.market_summary_dated),
        "valuation_snapshot": file_status(paths.valuation_snapshot),
        "analyst_report": file_status(paths.yfinance_analyst_report),
        "final_report": file_status(paths.yfinance_final_report),
    }
    global_outputs = {
        "run_manifest": file_status(paths.run_manifest),
        "run_status": file_status(paths.run_status),
        "run_trace": file_status(paths.run_trace),
    }
    return {
        "financial": financial,
        "news": news,
        "yfinance": yfinance,
        "global": global_outputs,
    }


def collect_token_usage(
    paths: RunPaths,
    *,
    manifest_path: str | Path | None = None,
    execution_id: str = "",
    run_id: str = "",
) -> dict[str, Any]:
    usage_path = Path(manifest_path).expanduser().resolve() if manifest_path else paths.llm_usage_manifest
    manifest_usage = _usage_from_jsonl(
        usage_path,
        execution_id=execution_id,
        run_id=run_id,
    )
    if manifest_usage is not None:
        return manifest_usage

    items = {
        "financial_analyst": _usage_by_field(paths.financial_agent_pipeline_dir / "pipeline_financial_analyst_report_trace.json"),
        "news_period_summary": _usage_at_path(paths.news_llm_period_summaries, ("usage", "total")),
        "news_analysis": _usage_at_path(paths.news_handoff, ("usage",)),
    }
    total = {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}
    for usage in items.values():
        for key in total:
            total[key] += int(usage.get(key) or 0)
    return {
        "source": "legacy_agent_artifacts",
        "recorded_total": total,
        "by_step": items,
        "untracked_steps": ["yfinance_analyst_report"],
        "note": "YFinance report usage was not persisted by the current implementation, so recorded_total is a lower bound.",
    }


def _usage_from_jsonl(
    path: Path,
    *,
    execution_id: str = "",
    run_id: str = "",
) -> dict[str, Any] | None:
    if not path.exists():
        return None
    rows: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(row, dict):
            continue
        if execution_id and row.get("execution_id") != execution_id:
            continue
        if run_id and row.get("run_id") != run_id:
            continue
        rows.append(row)
    if not rows:
        return None

    total = {
        "input_tokens": 0,
        "cached_input_tokens": 0,
        "output_tokens": 0,
        "reasoning_tokens": 0,
        "total_tokens": 0,
    }
    by_step: dict[str, dict[str, int]] = {}
    for row in rows:
        usage = row.get("usage") if isinstance(row.get("usage"), dict) else {}
        step = str(row.get("step") or "unknown")
        step_usage = by_step.setdefault(step, {**{key: 0 for key in total}, "call_count": 0, "error_count": 0})
        step_usage["call_count"] += 1
        if row.get("status") != "ok":
            step_usage["error_count"] += 1
        for key in total:
            amount = int(usage.get(key) or 0)
            total[key] += amount
            step_usage[key] += amount
    return {
        "source": str(path),
        "recorded_total": total,
        "by_step": by_step,
        "call_count": len(rows),
        "error_count": sum(1 for row in rows if row.get("status") != "ok"),
        "untracked_steps": [],
    }


def file_status(path: Path) -> dict[str, Any]:
    return {
        "path": str(path),
        "exists": path.exists(),
        "size_bytes": path.stat().st_size if path.exists() else 0,
    }


def read_nested_value(path: Path, keys: tuple[str, ...]) -> Any:
    if not path.exists():
        return None
    try:
        value: Any = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return None
    for key in keys:
        if not isinstance(value, dict):
            return None
        value = value.get(key)
    return value


def _usage_by_field(path: Path) -> dict[str, int]:
    value = read_nested_value(path, ("llm_usage_summary", "by_field")) or {}
    return _normalize_usage(value)


def _usage_at_path(path: Path, keys: tuple[str, ...]) -> dict[str, int]:
    return _normalize_usage(read_nested_value(path, keys) or {})


def _normalize_usage(value: dict[str, Any]) -> dict[str, int]:
    return {
        "prompt_tokens": int(value.get("prompt_tokens") or 0),
        "completion_tokens": int(value.get("completion_tokens") or 0),
        "total_tokens": int(value.get("total_tokens") or 0),
    }
