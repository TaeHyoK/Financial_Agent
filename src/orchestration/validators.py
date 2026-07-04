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
        "verified_report": file_status(paths.financial_verified_report),
        "final_report": file_status(paths.financial_final_report),
        "final_validation": file_status(paths.financial_final_validation),
        "sy_overall_status": (
            read_nested_value(paths.financial_agent_pipeline_dir / "pipeline_sy_validation_output.json", ("validation_summary", "overall_status"))
            or read_nested_value(paths.financial_agent_pipeline_dir / "pipeline_sy_validation_output.json", ("overall_status",))
        ),
    }
    news = {
        "handoff": file_status(paths.news_handoff),
        "sy_validations": file_status(paths.news_sy_validations),
        "verified_report": file_status(paths.news_verified_report),
        "final_report": file_status(paths.news_final_report),
        "final_validation": file_status(paths.news_final_validation),
        "sy_summary": read_nested_value(paths.news_sy_validations, ("summary",)),
    }
    yfinance = {
        "market_summary": file_status(paths.market_summary),
        "market_summary_dated": file_status(paths.market_summary_dated),
        "analyst_report": file_status(paths.yfinance_analyst_report),
        "verified_report": file_status(paths.yfinance_verified_report),
        "strategy_verified_report": file_status(paths.yfinance_strategy_verified_report),
        "final_report": file_status(paths.yfinance_final_report),
        "final_validation": file_status(paths.yfinance_final_validation),
        "verification_summary": read_nested_value(paths.yfinance_verified_report, ("summary",)),
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


def collect_token_usage(paths: RunPaths) -> dict[str, Any]:
    items = {
        "financial_analyst": _usage_by_field(paths.financial_agent_pipeline_dir / "pipeline_financial_analyst_report_trace.json"),
        "financial_sy": _usage_by_field(paths.financial_agent_pipeline_dir / "pipeline_sy_validation_trace.json"),
        "news_period_summary": _usage_at_path(paths.news_llm_period_summaries, ("usage", "total")),
        "news_analysis": _usage_at_path(paths.news_handoff, ("usage",)),
        "news_sy": _usage_at_path(paths.news_sy_validations, ("llm_usage",)),
    }
    total = {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}
    for usage in items.values():
        for key in total:
            total[key] += int(usage.get(key) or 0)
    return {
        "recorded_total": total,
        "by_step": items,
        "untracked_steps": [
            "yfinance_analyst_report",
            "yfinance_sy_validation",
        ],
        "note": "YFinance report/SY usage was not persisted by the current YFinance implementation, so recorded_total is a lower bound.",
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
