"""Manifest writers for Agent_Team orchestration."""

from __future__ import annotations

import json
import shutil
from pathlib import Path
from typing import Any

from .config import RunConfig
from .paths import RunPaths
from .run_state import (
    FAILED,
    PARTIAL_SUCCESS,
    PENDING,
    RUNNING,
    SKIPPED,
    SUCCESS,
    StepRecord,
    utc_now,
)
from .validators import collect_token_usage, validate_outputs


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def write_run_config_copy(paths: RunPaths, config: RunConfig) -> None:
    write_json(paths.run_config_copy, config.raw)


def write_financial_runtime_manifest(
    paths: RunPaths,
    config: RunConfig,
    *,
    primary_data_only: bool = False,
) -> None:
    manifest = {
        "agent_name": "Financial Analyst Agent",
        "agent_version": "3.4",
        "target_entity": {
            "company_name": config.company_name,
            "ticker": config.ticker,
            "corp_code": config.corp_code,
            "as_of_date": config.selected_date_iso,
        },
        "input_paths": {
            "dart_main": str(paths.dart_main),
            "dart_master": str(paths.dart_master),
            "yfinance_market_summary": "" if primary_data_only else str(paths.market_summary),
            "news_company_top10": "" if primary_data_only else str(paths.news_company_top10),
        },
        "input_roles": {
            "dart_main": "primary_financial_anchor",
            "dart_master": "primary_detailed_statement_evidence",
            "yfinance_market_summary": "market_context",
            "news_company_top10": "company_related_news_secondary_data",
        },
        "source_notes": {
            "news_granularity": "day",
            "news_window": "The ten highest-ranked company-related News events are used as Financial subdata.",
            "market_window": "Derived from config.date_range by default; market_summary is selected_date only.",
            "dart_window": (
                "Latest regular filing available by selected_date, prior-year same-period filing, "
                "and up to three annual periods; future receipt dates are excluded."
            ),
            "selected_date": config.selected_date_iso,
        },
        "output_contract": {
            "format": "json",
            "mode": "financial_report_with_sy_handoff",
            "language": "ko",
            "investment_decision_allowed": False,
        },
        "ablation": {
            "primary_data_only": primary_data_only,
            "secondary_context_enabled": not primary_data_only,
        },
    }
    write_json(paths.financial_runtime_manifest, manifest)


def build_outputs_manifest(paths: RunPaths) -> dict[str, Any]:
    return {
        "financial": {
            "dart_main": str(paths.dart_main),
            "dart_master": str(paths.dart_master),
            "dart_lightweight": str(paths.dart_lightweight),
            "verified_report": str(paths.financial_verified_report),
            "final_report": str(paths.financial_final_report),
            "final_validation": str(paths.financial_final_validation),
            "agent_pipeline_dir": str(paths.financial_agent_pipeline_dir),
        },
        "news": {
            "context_exports": str(paths.news_context_export_dir),
            "llm_period_summaries": str(paths.news_llm_period_summaries),
            "company_related_news_top10": str(paths.news_company_top10),
            "handoff": str(paths.news_handoff),
            "sy_validations": str(paths.news_sy_validations),
            "verified_report": str(paths.news_verified_report),
            "final_report": str(paths.news_final_report),
            "final_validation": str(paths.news_final_validation),
        },
        "yfinance": {
            "market_summary": str(paths.market_summary),
            "market_summary_dated": str(paths.market_summary_dated),
            "valuation_snapshot": str(paths.valuation_snapshot),
            "analyst_report": str(paths.yfinance_analyst_report),
            "verified_report": str(paths.yfinance_verified_report),
            "strategy_verified_report": str(paths.yfinance_strategy_verified_report),
            "final_report": str(paths.yfinance_final_report),
            "final_validation": str(paths.yfinance_final_validation),
        },
    }


def sync_final_aliases(paths: RunPaths) -> dict[str, Any]:
    sources = paths.final_alias_sources()
    targets = paths.final_alias_targets()
    result: dict[str, Any] = {}
    for team, team_sources in sources.items():
        result[team] = {}
        for alias_name, source in team_sources.items():
            target = targets[team][alias_name]
            copied = False
            if source.exists():
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(source, target)
                copied = True
            result[team][alias_name] = {
                "source": str(source),
                "target": str(target),
                "copied": copied,
            }
    return result


def infer_overall_status(
    steps: list[StepRecord],
    *,
    expected_step_count: int | None = None,
) -> str:
    statuses = [step.status for step in steps]
    if any(status == FAILED for status in statuses):
        if any(status == SUCCESS for status in statuses):
            return PARTIAL_SUCCESS
        return FAILED
    if any(status in {PENDING, RUNNING} for status in statuses) or (
        expected_step_count is not None and len(steps) < expected_step_count
    ):
        return RUNNING
    if any(status == SKIPPED for status in statuses):
        if any(status == SUCCESS for status in statuses):
            return PARTIAL_SUCCESS
        return SKIPPED
    return SUCCESS


def is_pipeline_completed(
    steps: list[StepRecord],
    *,
    expected_step_count: int | None = None,
) -> bool:
    if any(step.status in {PENDING, RUNNING} for step in steps):
        return False
    if expected_step_count is not None and len(steps) < expected_step_count:
        return False
    return bool(steps)


def write_run_files(
    paths: RunPaths,
    config: RunConfig,
    steps: list[StepRecord],
    *,
    dry_run: bool,
    llm_usage_manifest: str | Path | None = None,
    llm_execution_id: str = "",
    llm_run_id: str = "",
    expected_step_count: int | None = None,
) -> dict[str, Any]:
    status = infer_overall_status(steps, expected_step_count=expected_step_count)
    pipeline_completed = is_pipeline_completed(
        steps,
        expected_step_count=expected_step_count,
    )
    step_dicts = [step.as_dict() for step in steps]
    final_aliases = sync_final_aliases(paths)
    validations = validate_outputs(paths)
    token_usage = collect_token_usage(
        paths,
        manifest_path=llm_usage_manifest,
        execution_id=llm_execution_id,
        run_id=llm_run_id,
    )
    manifest = {
        "run_key": paths.run_key,
        "company_name": config.company_name,
        "ticker": config.ticker,
        "corp_code": config.corp_code,
        "selected_date": config.selected_date,
        "selected_date_policy": "before_market_open",
        "information_cutoff_date": config.information_cutoff_date,
        "requested_date_range": config.date_range,
        "effective_date_range": config.effective_date_range,
        "status": status,
        "pipeline_completed": pipeline_completed,
        "dry_run": dry_run,
        "created_at": utc_now(),
        "outputs": build_outputs_manifest(paths),
        "final_aliases": final_aliases,
        "validations": validations,
        "token_usage": token_usage,
        "steps": step_dicts,
    }
    status_payload = {
        "run_key": paths.run_key,
        "status": status,
        "pipeline_completed": pipeline_completed,
        "dry_run": dry_run,
        "updated_at": utc_now(),
        "steps": [{"name": step.name, "status": step.status, "returncode": step.returncode} for step in steps],
    }
    errors = [
        {
            "name": step.name,
            "returncode": step.returncode,
            "stderr_tail": step.stderr_tail,
            "reason": step.reason,
        }
        for step in steps
        if step.status == FAILED
    ]

    write_json(paths.run_manifest, manifest)
    write_json(paths.run_status, status_payload)
    write_json(paths.run_trace, {"run_key": paths.run_key, "steps": step_dicts})
    write_json(paths.errors, errors)
    return manifest
