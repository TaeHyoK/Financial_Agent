"""Run and aggregate a paper-oriented full ablation matrix."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

from Agent_Team.Strategy_Agent.agent import (
    DECISION_HORIZON_PROFILES,
    DEFAULT_DECISION_HORIZON_PROFILE,
)
from orchestration.config import DEFAULT_ENV_FILE
from orchestration.paths import PROJECT_ROOT
from orchestration.usage_summary import summarize_execution_usage


DEFAULT_OUTPUT_ROOT = PROJECT_ROOT / "Output_total" / "experiments" / "ablations"


@dataclass(frozen=True)
class AblationCondition:
    name: str
    flags: tuple[str, ...]
    hypothesis: str


CONDITIONS: tuple[AblationCondition, ...] = (
    AblationCondition("full", (), "Complete proposed system."),
    AblationCondition("no_sy", ("--no-sy",), "Contribution of domain claim verification."),
    AblationCondition(
        "no_financial",
        ("--exclude-domain", "financial"),
        "Contribution of financial evidence to the final decision.",
    ),
    AblationCondition(
        "no_news",
        ("--exclude-domain", "news"),
        "Contribution of event, catalyst, and news-risk evidence.",
    ),
    AblationCondition(
        "no_yfinance",
        ("--exclude-domain", "yfinance"),
        "Contribution of market, relative-performance, and valuation evidence.",
    ),
    AblationCondition(
        "primary_only",
        ("--primary-data-only",),
        "Contribution of cross-domain secondary context.",
    ),
    AblationCondition(
        "no_competitor",
        ("--no-competitor",),
        "Contribution of the selected-peer path.",
    ),
    AblationCondition(
        "only_financial",
        ("--only-domain", "financial"),
        "Financial-only diagnostic condition.",
    ),
    AblationCondition(
        "only_news",
        ("--only-domain", "news"),
        "News-only diagnostic condition.",
    ),
    AblationCondition(
        "only_yfinance",
        ("--only-domain", "yfinance"),
        "Market-only diagnostic condition.",
    ),
    AblationCondition(
        "full_context",
        ("--full-context",),
        "Effect of replacing compact-only Strategy input with sanitized full reports.",
    ),
    AblationCondition(
        "free_form_writer",
        ("--free-form-writer",),
        "Effect of removing deterministic thesis and table assembly.",
    ),
)
CONDITION_BY_NAME = {condition.name: condition for condition in CONDITIONS}
FULL_PIPELINE_CONDITIONS = {"full", "no_sy", "primary_only"}


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Run the full paper ablation matrix with isolated outputs and one suite manifest."
    )
    parser.add_argument("--company-name", required=True)
    parser.add_argument("--selected-date", required=True, help="YYYYMMDD, before market open.")
    parser.add_argument("--news-window", default="1m", choices=["2w", "1m", "3m"])
    parser.add_argument(
        "--news-total-max-results",
        type=_positive_int,
        default=None,
        help="Maximum deduplicated News articles per company across the selected window.",
    )
    parser.add_argument(
        "--decision-horizon-profile",
        default=DEFAULT_DECISION_HORIZON_PROFILE,
        choices=list(DECISION_HORIZON_PROFILES),
    )
    parser.add_argument("--llm-model", default="gpt-5.4-mini")
    parser.add_argument("--env-file", type=Path, default=DEFAULT_ENV_FILE)
    parser.add_argument("--output-root", type=Path, default=DEFAULT_OUTPUT_ROOT)
    parser.add_argument("--suite-id", default="")
    parser.add_argument(
        "--reuse-collected-from",
        type=Path,
        default=None,
        metavar="FULL_PIPELINE_MANIFEST",
        help=(
            "Reuse the successful Full run's fixed DART, market, and News collection/summary snapshot "
            "for full, no_sy, and primary_only instead of collecting providers again."
        ),
    )
    parser.add_argument(
        "--condition",
        action="append",
        default=[],
        choices=list(CONDITION_BY_NAME),
        help="Run only selected condition(s); repeatable. Defaults to the complete matrix.",
    )
    parser.add_argument("--replicates", type=_positive_int, default=1)
    parser.add_argument("--peer-stock-code", default="")
    parser.add_argument("--llm-timeout", type=_positive_int, default=300)
    parser.add_argument("--max-retries", type=_non_negative_int, default=1)
    parser.add_argument("--semantic-attempts", type=_positive_int, default=2)
    parser.add_argument("--final-stage-timeout", type=_positive_int, default=900)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument(
        "--freeze-upstream",
        action=argparse.BooleanOptionalAction,
        default=True,
        help=(
            "Reuse Full's exact upstream artifacts for Strategy/Writer-only ablations. "
            "Enabled by default for paired scientific comparison."
        ),
    )
    parser.add_argument(
        "--resume",
        action="store_true",
        help="Resume the same suite, skipping successful condition/replicate records.",
    )
    parser.add_argument(
        "--force-condition",
        action="append",
        default=[],
        choices=list(CONDITION_BY_NAME),
        help=(
            "Rerun a successful condition while resuming; repeatable. "
            "The rerun receives a fresh execution ID so usage accounting stays isolated."
        ),
    )
    parser.add_argument("--fail-fast", action="store_true")
    parser.add_argument("--no-progress", action="store_true")
    return parser


def run_ablation_suite(args: argparse.Namespace) -> dict[str, Any]:
    suite_id = _safe_label(args.suite_id or _new_suite_id())
    suite_root = Path(args.output_root).expanduser().resolve() / suite_id
    suite_root.mkdir(parents=True, exist_ok=True)
    selected_conditions = select_conditions(args.condition)
    manifest_path = suite_root / "ablation_suite_manifest.json"
    summary_path = suite_root / "ablation_summary.json"
    summary_md_path = suite_root / "ablation_summary.md"
    if args.resume:
        if not manifest_path.exists():
            raise ValueError(f"Cannot resume missing suite manifest: {manifest_path}")
        manifest = _load_json(manifest_path)
        manifest["status"] = "running"
        manifest["resumed_at"] = _utc_now()
        manifest["resume_code"] = code_identity()
        request = manifest.setdefault("request", {})
        request["freeze_upstream"] = bool(args.freeze_upstream)
        request["reuse_collected_from"] = (
            str(Path(args.reuse_collected_from).expanduser().resolve())
            if args.reuse_collected_from
            else ""
        )
        request["replicates"] = args.replicates
        request["news_total_max_results"] = args.news_total_max_results
        for old_record in manifest.get("runs") or []:
            if not isinstance(old_record, dict) or old_record.get("execution_mode"):
                continue
            old_record["execution_mode"] = (
                "full_pipeline"
                if old_record.get("condition") in FULL_PIPELINE_CONDITIONS
                else "frozen_upstream_final_stage"
            )
    else:
        manifest = {
            "suite_id": suite_id,
            "status": "running",
            "created_at": _utc_now(),
            "project_root": str(PROJECT_ROOT),
            "suite_root": str(suite_root),
            "request": {
                "company_name": args.company_name,
                "selected_date": args.selected_date,
                "news_window": args.news_window,
                "news_total_max_results": args.news_total_max_results,
                "decision_horizon_profile": args.decision_horizon_profile,
                "llm_model": args.llm_model,
                "replicates": args.replicates,
                "peer_stock_code": args.peer_stock_code,
                "dry_run": bool(args.dry_run),
                "freeze_upstream": bool(args.freeze_upstream),
                "reuse_collected_from": (
                    str(Path(args.reuse_collected_from).expanduser().resolve())
                    if args.reuse_collected_from
                    else ""
                ),
            },
            "code": code_identity(),
            "conditions": [
                {
                    "name": condition.name,
                    "flags": list(condition.flags),
                    "hypothesis": condition.hypothesis,
                }
                for condition in selected_conditions
            ],
            "runs": [],
        }
    _write_json(manifest_path, manifest)
    reuse_snapshot_root = (
        _snapshot_root_from_full_manifest(
            Path(args.reuse_collected_from).expanduser().resolve(),
            company_name=args.company_name,
            selected_date=args.selected_date,
            news_window=args.news_window,
        )
        if args.reuse_collected_from
        else None
    )

    for condition in selected_conditions:
        for replicate in range(1, args.replicates + 1):
            run_name = f"{condition.name}__r{replicate:02d}"
            replicate_root = (
                suite_root / "conditions" / condition.name / f"replicate_{replicate:02d}"
            )
            existing = _find_run_record(manifest, condition.name, replicate)
            force_condition = condition.name in set(args.force_condition or [])
            if (
                args.resume
                and existing
                and existing.get("status") in {"success", "dry_run"}
                and not force_condition
            ):
                existing.setdefault("attempt", 1)
                existing_manifest = existing.get("pipeline_manifest")
                if _path_exists(existing_manifest):
                    existing["result"] = load_condition_result(
                        Path(str(existing_manifest)).expanduser().resolve()
                    )
                print(f"[ablation] SKIP {run_name} status={existing['status']}", flush=True)
                continue
            previous_attempt = int(existing.get("attempt", 1)) if existing else 0
            rerun_incomplete = bool(
                args.resume
                and existing
                and existing.get("status") not in {"success", "dry_run"}
            )
            attempt = (
                previous_attempt + 1
                if existing and (force_condition or rerun_incomplete)
                else max(previous_attempt, 1)
            )
            condition_root = (
                replicate_root if attempt == 1 else replicate_root / f"attempt_{attempt:02d}"
            )
            execution_id = f"{suite_id}__{run_name}"
            if attempt > 1:
                execution_id += f"__attempt{attempt:02d}"
            log_dir = suite_root / "logs"
            log_dir.mkdir(parents=True, exist_ok=True)
            log_stem = run_name if attempt == 1 else f"{run_name}__attempt{attempt:02d}"
            stdout_path = log_dir / f"{log_stem}.stdout.log"
            stderr_path = log_dir / f"{log_stem}.stderr.log"
            use_frozen_upstream = bool(
                args.freeze_upstream
                and not args.dry_run
                and condition.name not in FULL_PIPELINE_CONDITIONS
            )
            record = existing if existing is not None else {}
            record.clear()
            record.update({
                "condition": condition.name,
                "replicate": replicate,
                "attempt": attempt,
                "execution_id": execution_id,
                "execution_mode": (
                    "frozen_upstream_final_stage"
                    if use_frozen_upstream
                    else (
                        "reused_domain_snapshot_pipeline"
                        if reuse_snapshot_root and condition.name in FULL_PIPELINE_CONDITIONS
                        else "full_pipeline"
                    )
                ),
                "status": "running",
                "started_at": _utc_now(),
                "condition_root": str(condition_root),
                "stdout_log": str(stdout_path),
                "stderr_log": str(stderr_path),
            })
            if existing is None:
                manifest["runs"].append(record)
            _write_json(manifest_path, manifest)
            print(
                f"[ablation] START {run_name} mode={record['execution_mode']}",
                flush=True,
            )
            started = time.monotonic()
            if use_frozen_upstream:
                source_manifest = _successful_full_manifest(manifest, replicate)
                if source_manifest is None:
                    raise ValueError(
                        f"Full replicate {replicate} must succeed before frozen-upstream ablations."
                    )
                frozen = run_frozen_final_stage(
                    args=args,
                    condition=condition,
                    condition_root=condition_root,
                    execution_id=execution_id,
                    source_manifest=source_manifest,
                    log_dir=log_dir,
                )
                returncode = int(frozen["returncode"])
                record["commands"] = frozen["commands"]
                record["source_pipeline_manifest"] = str(source_manifest)
                pipeline_manifest = Path(frozen["manifest_path"])
                result = frozen["result"]
            else:
                command = build_condition_command(
                    args=args,
                    condition=condition,
                    condition_root=condition_root,
                    execution_id=execution_id,
                    reuse_domain_data_from=(
                        reuse_snapshot_root
                        if condition.name in FULL_PIPELINE_CONDITIONS
                        else None
                    ),
                )
                record["command"] = command
                if reuse_snapshot_root and condition.name in FULL_PIPELINE_CONDITIONS:
                    record["source_snapshot_manifest"] = str(
                        Path(args.reuse_collected_from).expanduser().resolve()
                    )
                    record["source_snapshot_root"] = str(reuse_snapshot_root)
                with stdout_path.open("w", encoding="utf-8") as stdout_handle, stderr_path.open(
                    "w", encoding="utf-8"
                ) as stderr_handle:
                    completed = subprocess.run(
                        command,
                        cwd=str(PROJECT_ROOT),
                        text=True,
                        stdout=stdout_handle,
                        stderr=stderr_handle,
                        check=False,
                    )
                returncode = completed.returncode
                pipeline_manifest = locate_pipeline_manifest(condition_root, execution_id)
                result = load_condition_result(pipeline_manifest) if pipeline_manifest else {}
            record["returncode"] = returncode
            record["elapsed_seconds"] = round(time.monotonic() - started, 3)
            record["completed_at"] = _utc_now()
            record["pipeline_manifest"] = str(pipeline_manifest) if pipeline_manifest else ""
            record["result"] = result
            pipeline_status = str(result.get("pipeline_status") or "")
            if args.dry_run:
                record["status"] = "dry_run" if returncode == 0 else "failed"
            else:
                record["status"] = (
                    "success"
                    if returncode == 0 and pipeline_status == "success"
                    else "failed"
                )
            _write_json(manifest_path, manifest)
            print(
                f"[ablation] END {run_name} status={record['status']} "
                f"elapsed={record['elapsed_seconds']}s",
                flush=True,
            )
            if record["status"] == "failed" and args.fail_fast:
                manifest["status"] = "failed"
                manifest["completed_at"] = _utc_now()
                _write_json(manifest_path, manifest)
                summary = build_summary(manifest)
                _write_json(summary_path, summary)
                _write_text(summary_md_path, render_summary_markdown(summary))
                return summary

    summary = build_summary(manifest)
    manifest["status"] = summary["status"]
    manifest["completed_at"] = _utc_now()
    _write_json(manifest_path, manifest)
    summary = build_summary(manifest)
    _write_json(summary_path, summary)
    _write_text(summary_md_path, render_summary_markdown(summary))
    return summary


def select_conditions(names: Iterable[str]) -> tuple[AblationCondition, ...]:
    selected = list(names)
    if not selected:
        return CONDITIONS
    wanted = set(selected)
    return tuple(condition for condition in CONDITIONS if condition.name in wanted)


def build_condition_command(
    *,
    args: argparse.Namespace,
    condition: AblationCondition,
    condition_root: Path,
    execution_id: str,
    reuse_domain_data_from: Path | None = None,
) -> list[str]:
    command = [
        sys.executable,
        "-m",
        "orchestration.full_report_pipeline",
        "--company-name",
        args.company_name,
        "--selected-date",
        args.selected_date,
        "--news-window",
        args.news_window,
        "--decision-horizon-profile",
        args.decision_horizon_profile,
        "--llm-model",
        args.llm_model,
        "--env-file",
        str(Path(args.env_file).expanduser().resolve()),
        "--output-root",
        str(condition_root),
        "--execution-id",
        execution_id,
        "--experiment-name",
        condition.name,
        "--llm-timeout",
        str(args.llm_timeout),
        "--max-retries",
        str(args.max_retries),
        "--semantic-attempts",
        str(args.semantic_attempts),
        "--final-stage-timeout",
        str(args.final_stage_timeout),
    ]
    if args.peer_stock_code and condition.name != "no_competitor":
        command.extend(["--peer-stock-code", args.peer_stock_code])
    if args.news_total_max_results is not None:
        command.extend(
            ["--news-total-max-results", str(args.news_total_max_results)]
        )
    if reuse_domain_data_from is not None:
        command.extend(
            [
                "--reuse-domain-data-from",
                str(Path(reuse_domain_data_from).expanduser().resolve()),
            ]
        )
    if args.no_progress:
        command.append("--no-progress")
    if args.dry_run:
        command.append("--dry-run")
    command.extend(condition.flags)
    return command


def _snapshot_root_from_full_manifest(
    manifest_path: Path,
    *,
    company_name: str,
    selected_date: str,
    news_window: str,
) -> Path:
    source = _load_json(manifest_path)
    if source.get("status") != "success":
        raise ValueError(f"Collected snapshot Full manifest is not successful: {manifest_path}")
    request = source.get("request") if isinstance(source.get("request"), dict) else {}
    target = source.get("target") if isinstance(source.get("target"), dict) else {}
    if str(target.get("company_name") or "") != company_name:
        raise ValueError("Collected snapshot company does not match the requested company.")
    if str(request.get("selected_date") or "") != selected_date:
        raise ValueError("Collected snapshot selected date does not match the requested date.")
    if str(request.get("news_window") or "") != news_window:
        raise ValueError("Collected snapshot news window does not match the requested window.")
    outputs = source.get("outputs") if isinstance(source.get("outputs"), dict) else {}
    strategy_report = Path(str(outputs.get("strategy_report") or "")).expanduser()
    if not strategy_report.is_file():
        raise ValueError(f"Collected snapshot has no Strategy report: {strategy_report}")
    root = strategy_report.resolve().parents[2]
    if not root.is_dir():
        raise ValueError(f"Collected snapshot output root does not exist: {root}")
    return root


def run_frozen_final_stage(
    *,
    args: argparse.Namespace,
    condition: AblationCondition,
    condition_root: Path,
    execution_id: str,
    source_manifest: Path,
    log_dir: Path,
) -> dict[str, Any]:
    """Run paired Strategy/Writer ablations against Full's exact upstream files."""

    source = _load_json(source_manifest)
    source_outputs = source.get("outputs") if isinstance(source.get("outputs"), dict) else {}
    target = source.get("target") if isinstance(source.get("target"), dict) else {}
    run_key = str(source.get("run_key") or "")
    company_name = str(target.get("company_name") or args.company_name)
    if not run_key:
        raise ValueError(f"Source Full manifest has no run_key: {source_manifest}")
    required_inputs = {
        "target_financial": source_outputs.get("strategy_report") and str(
            Path(str(source_outputs["strategy_report"])).parents[2]
            / "Financial"
            / run_key
            / "final_report.json"
        ),
        "target_news": source_outputs.get("strategy_report") and str(
            Path(str(source_outputs["strategy_report"])).parents[2]
            / "News"
            / run_key
            / "final_report.json"
        ),
        "target_yfinance": source_outputs.get("strategy_report") and str(
            Path(str(source_outputs["strategy_report"])).parents[2]
            / "Y_Finance"
            / run_key
            / "final_report.json"
        ),
        "peer_comparison": source_outputs.get("peer_comparison"),
    }
    missing = [name for name, path in required_inputs.items() if name != "peer_comparison" and not _path_exists(path)]
    if missing:
        raise ValueError(f"Frozen Full upstream is missing required files: {missing}")

    strategy_dir = condition_root / "Strategy" / run_key
    writer_dir = condition_root / "Writer" / run_key
    execution_dir = condition_root / "runs" / run_key / "executions" / execution_id
    strategy_dir.mkdir(parents=True, exist_ok=True)
    writer_dir.mkdir(parents=True, exist_ok=True)
    execution_dir.mkdir(parents=True, exist_ok=True)
    usage_manifest = execution_dir / "llm_usage_manifest.jsonl"
    usage_summary_path = execution_dir / "llm_usage_summary.json"
    final_manifest_path = execution_dir / "final_stage_manifest.json"
    included_domains = _condition_domains(condition.name)
    condition_flags = set(condition.flags)
    use_sy = "--no-sy" not in condition_flags
    primary_data_only = "--primary-data-only" in condition_flags
    include_competitor = (
        condition.name != "no_competitor"
        and "--no-competitor" not in condition_flags
    )
    reuse_full_strategy = condition.name == "free_form_writer"
    reused_strategy_artifacts: dict[str, str] = {}
    if reuse_full_strategy:
        source_decision = Path(str(source_outputs.get("strategy_decision_output_v2") or ""))
        if not source_decision.exists():
            raise ValueError(
                "Writer-only ablation requires Full's strategy_decision_output_v2.json."
            )
        source_strategy_dir = source_decision.resolve().parent
        strategy_filenames = (
            "strategy_report.json",
            "strategy_compact_packet_v2.json",
            "strategy_packet_provenance_v2.json",
            "strategy_decision_output_v2.json",
            "strategy_semantic_validation_v2.json",
            "strategy_packet_telemetry_v2.json",
            "strategy_decision_profile_v2.json",
            "strategy_generation_context_v2.json",
        )
        missing_strategy = [
            filename for filename in strategy_filenames if not (source_strategy_dir / filename).exists()
        ]
        if missing_strategy:
            raise ValueError(
                "Writer-only ablation is missing Full Strategy artifacts: "
                f"{missing_strategy}"
            )
        for filename in strategy_filenames:
            source_path = source_strategy_dir / filename
            destination = strategy_dir / filename
            shutil.copy2(source_path, destination)
            reused_strategy_artifacts[f"full_strategy_{filename.removesuffix('.json')}"] = str(
                source_path
            )
    strategy_command = [
        sys.executable,
        "-m",
        "Agent_Team.Strategy_Agent.cli",
        "--target-company-name",
        company_name,
        "--target-run-key",
        run_key,
        "--target-financial",
        str(required_inputs["target_financial"]),
        "--target-news",
        str(required_inputs["target_news"]),
        "--target-yfinance",
        str(required_inputs["target_yfinance"]),
        "--output-dir",
        str(strategy_dir),
        "--packet-version",
        "v2",
        "--llm-model",
        args.llm_model,
        "--llm-timeout",
        str(args.llm_timeout),
        "--decision-horizon-profile",
        args.decision_horizon_profile,
        "--semantic-attempts",
        str(args.semantic_attempts),
        "--env-file",
        str(Path(args.env_file).expanduser().resolve()),
        "--experiment-name",
        condition.name,
    ]
    for domain in included_domains:
        strategy_command.extend(["--include-domain", domain])
    if include_competitor:
        if not _path_exists(required_inputs.get("peer_comparison")):
            raise ValueError("Frozen Full upstream has no peer comparison dataset.")
        strategy_command.extend(["--peer-comparison", str(required_inputs["peer_comparison"])])
    else:
        strategy_command.append("--no-competitor")
    if not use_sy:
        strategy_command.append("--no-sy")
    if primary_data_only:
        strategy_command.append("--primary-data-only")
    if condition.name == "full_context":
        strategy_command.append("--full-context")

    writer_command = [
        sys.executable,
        str(PROJECT_ROOT / "src" / "Agent_Team" / "Writer Agent" / "writer_agent.py"),
        "--run-key",
        run_key,
        "--strategy-packet",
        str(strategy_dir / "strategy_compact_packet_v2.json"),
        "--strategy-provenance",
        str(strategy_dir / "strategy_packet_provenance_v2.json"),
        "--strategy-decision",
        str(strategy_dir / "strategy_decision_output_v2.json"),
        "--output-dir",
        str(writer_dir),
        "--env-file",
        str(Path(args.env_file).expanduser().resolve()),
        "--llm-model",
        args.llm_model,
        "--semantic-attempts",
        str(args.semantic_attempts),
    ]
    if condition.name == "free_form_writer":
        writer_command.append("--free-form")

    environment = os.environ.copy()
    source_pythonpath = str(PROJECT_ROOT / "src")
    current_pythonpath = environment.get("PYTHONPATH", "")
    environment["PYTHONPATH"] = (
        source_pythonpath if not current_pythonpath else source_pythonpath + os.pathsep + current_pythonpath
    )
    environment.update(
        {
            "OPENAI_MODEL": args.llm_model,
            "LLM_TIMEOUT_SECONDS": str(args.llm_timeout),
            "LLM_TRANSPORT_RETRIES": str(args.max_retries),
            "LLM_USAGE_MANIFEST": str(usage_manifest),
            "LLM_RUN_ID": run_key,
            "LLM_RUN_ROLE": "final",
            "LLM_COMPANY_NAME": company_name,
            "LLM_EXECUTION_ID": execution_id,
        }
    )
    strategy_stdout = log_dir / f"{condition.name}__strategy.stdout.log"
    strategy_stderr = log_dir / f"{condition.name}__strategy.stderr.log"
    writer_stdout = log_dir / f"{condition.name}__writer.stdout.log"
    writer_stderr = log_dir / f"{condition.name}__writer.stderr.log"
    if reuse_full_strategy:
        strategy_rc = 0
        _write_text(
            strategy_stdout,
            "Reused Full's byte-identical Strategy artifacts for Writer-only ablation.\n",
        )
        _write_text(strategy_stderr, "")
    else:
        strategy_rc = _run_logged_command(
            strategy_command,
            env=environment,
            stdout_path=strategy_stdout,
            stderr_path=strategy_stderr,
            timeout=args.final_stage_timeout,
        )
    writer_rc = 125
    if strategy_rc == 0:
        writer_rc = _run_logged_command(
            writer_command,
            env=environment,
            stdout_path=writer_stdout,
            stderr_path=writer_stderr,
            timeout=args.final_stage_timeout,
        )

    strategy_validation = _load_json_optional(strategy_dir / "strategy_semantic_validation_v2.json")
    writer_validation = _load_json_optional(writer_dir / "writer_validation_report.json")
    report_path = writer_dir / "report.html"
    completed = (
        strategy_rc == 0
        and writer_rc == 0
        and strategy_validation.get("status") == "pass"
        and writer_validation.get("status") == "pass"
        and report_path.exists()
    )
    usage = summarize_execution_usage(
        usage_manifest,
        execution_id=execution_id,
        pipeline_completed=completed,
        expected_logical_calls_by_role={
            "target": 0,
            "peer": 0,
            "final": 1 if reuse_full_strategy else 2,
        },
    )
    _write_json(usage_summary_path, usage)
    error: dict[str, Any] = {}
    if not completed:
        error = {
            "type": "FrozenFinalStageFailure",
            "message": f"strategy_rc={strategy_rc}, writer_rc={writer_rc}",
        }
    final_manifest = {
        "execution_id": execution_id,
        "run_key": run_key,
        "status": "success" if completed else "failed",
        "execution_mode": "frozen_upstream_final_stage",
        "condition": condition.name,
        "source_pipeline_manifest": str(source_manifest),
        "source_artifacts": {
            name: {"path": str(path or ""), "sha256": _sha256_optional(path)}
            for name, path in {**required_inputs, **reused_strategy_artifacts}.items()
        },
        "outputs": {
            "strategy_report": str(strategy_dir / "strategy_report.json"),
            "strategy_compact_packet_v2": str(strategy_dir / "strategy_compact_packet_v2.json"),
            "strategy_decision_output_v2": str(strategy_dir / "strategy_decision_output_v2.json"),
            "writer_report": str(report_path),
            "llm_usage_manifest": str(usage_manifest),
            "llm_usage_summary": str(usage_summary_path),
        },
        "validation": {
            "status": "pass" if completed else "fail",
            "final_recommendation": _nested_value(
                _load_json_optional(strategy_dir / "strategy_decision_output_v2.json"),
                "decision",
                "opinion",
            ),
        },
        "llm_usage": usage,
        "error": error,
        "commands": {
            "strategy": [] if reuse_full_strategy else strategy_command,
            "writer": writer_command,
        },
        "logs": {
            "strategy_stdout": str(strategy_stdout),
            "strategy_stderr": str(strategy_stderr),
            "writer_stdout": str(writer_stdout),
            "writer_stderr": str(writer_stderr),
        },
    }
    _write_json(final_manifest_path, final_manifest)
    return {
        "returncode": 0 if completed else 1,
        "manifest_path": str(final_manifest_path),
        "result": load_condition_result(final_manifest_path),
        "commands": [writer_command] if reuse_full_strategy else [strategy_command, writer_command],
    }


def _condition_domains(name: str) -> tuple[str, ...]:
    domains = ("financial", "news", "yfinance")
    if name.startswith("no_") and name.removeprefix("no_") in domains:
        removed = name.removeprefix("no_")
        return tuple(domain for domain in domains if domain != removed)
    if name.startswith("only_") and name.removeprefix("only_") in domains:
        return (name.removeprefix("only_"),)
    return domains


def _run_logged_command(
    command: list[str],
    *,
    env: dict[str, str],
    stdout_path: Path,
    stderr_path: Path,
    timeout: int,
) -> int:
    stdout_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        with stdout_path.open("w", encoding="utf-8") as stdout_handle, stderr_path.open(
            "w", encoding="utf-8"
        ) as stderr_handle:
            completed = subprocess.run(
                command,
                cwd=str(PROJECT_ROOT),
                env=env,
                text=True,
                stdout=stdout_handle,
                stderr=stderr_handle,
                timeout=timeout,
                check=False,
            )
        return completed.returncode
    except subprocess.TimeoutExpired:
        with stderr_path.open("a", encoding="utf-8") as handle:
            handle.write(f"\nTimed out after {timeout} seconds.\n")
        return 124


def _successful_full_manifest(manifest: dict[str, Any], replicate: int) -> Path | None:
    record = _find_run_record(manifest, "full", replicate)
    if not record or record.get("status") != "success":
        return None
    path = Path(str(record.get("pipeline_manifest") or "")).expanduser().resolve()
    return path if path.exists() else None


def _find_run_record(
    manifest: dict[str, Any],
    condition: str,
    replicate: int,
) -> dict[str, Any] | None:
    for item in manifest.get("runs") or []:
        if (
            isinstance(item, dict)
            and item.get("condition") == condition
            and item.get("replicate") == replicate
        ):
            return item
    return None


def locate_pipeline_manifest(condition_root: Path, execution_id: str) -> Path | None:
    candidates = sorted(
        condition_root.glob(f"runs/*/executions/{execution_id}/full_pipeline_manifest.json")
    )
    return candidates[0] if len(candidates) == 1 else None


def load_condition_result(path: Path) -> dict[str, Any]:
    manifest = _load_json(path)
    outputs = manifest.get("outputs") if isinstance(manifest.get("outputs"), dict) else {}
    validation = manifest.get("validation") if isinstance(manifest.get("validation"), dict) else {}
    usage = manifest.get("llm_usage") if isinstance(manifest.get("llm_usage"), dict) else {}
    strategy_validation = _load_json_optional(
        _sibling_output(outputs.get("strategy_decision_output_v2"), "strategy_semantic_validation_v2.json")
    )
    strategy_packet = _load_json_optional(outputs.get("strategy_compact_packet_v2"))
    strategy_decision = _load_json_optional(outputs.get("strategy_decision_output_v2"))
    writer_validation = _load_json_optional(
        _sibling_output(outputs.get("writer_report"), "writer_validation_report.json")
    )
    telemetry = _load_json_optional(
        _sibling_output(outputs.get("strategy_compact_packet_v2"), "strategy_packet_telemetry_v2.json")
    )
    decision = strategy_decision.get("decision") if isinstance(strategy_decision.get("decision"), dict) else {}
    cards = strategy_packet.get("cards") if isinstance(strategy_packet.get("cards"), dict) else {}
    usage_values = usage.get("usage") if isinstance(usage.get("usage"), dict) else {}
    expected_calls = usage.get("expected_cold_cache_logical_calls")
    observed_calls = usage.get("observed_logical_calls")
    if isinstance(expected_calls, int) and isinstance(observed_calls, int):
        usage_accounting_status = (
            "clean" if expected_calls == observed_calls else "aggregate_or_cache_affected"
        )
    else:
        usage_accounting_status = "unavailable"
    usage_caveat = ""
    if usage_accounting_status == "aggregate_or_cache_affected":
        usage_caveat = (
            f"Expected {expected_calls} cold-cache logical calls but observed {observed_calls}; "
            "token totals may include a restarted attempt or cache-suppressed calls."
        )
    return {
        "pipeline_status": manifest.get("status"),
        "error": manifest.get("error") or {},
        "validation_status": validation.get("status"),
        "recommendation": decision.get("opinion") or validation.get("final_recommendation"),
        "investment_horizon": decision.get("horizon"),
        "data_coverage": decision.get("evidence_sufficiency"),
        "gate_a_status": _nested_status(strategy_validation, "gate_a"),
        "gate_b_status": _nested_status(strategy_validation, "gate_b"),
        "writer_validation_status": writer_validation.get("status"),
        "writer_blocking_failures": writer_validation.get("blocking_failures") or [],
        "strategy_card_count": len(cards),
        "strategy_context_mode": telemetry.get("strategy_context_mode"),
        "generation_payload_bytes": telemetry.get("generation_payload_bytes"),
        "expected_logical_calls": expected_calls,
        "observed_logical_calls": observed_calls,
        "usage_accounting_status": usage_accounting_status,
        "usage_caveat": usage_caveat,
        "transport_attempts": usage.get("transport_attempts"),
        "input_tokens": usage_values.get("input_tokens"),
        "output_tokens": usage_values.get("output_tokens"),
        "total_tokens": usage_values.get("total_tokens"),
        "report_html": outputs.get("writer_report"),
    }


def build_summary(manifest: dict[str, Any]) -> dict[str, Any]:
    runs = [item for item in manifest.get("runs") or [] if isinstance(item, dict)]
    terminal = [item for item in runs if item.get("status") in {"success", "failed", "dry_run"}]
    successes = [item for item in terminal if item.get("status") == "success"]
    failures = [item for item in terminal if item.get("status") == "failed"]
    dry_runs = [item for item in terminal if item.get("status") == "dry_run"]
    if len(terminal) < len(runs):
        status = "running"
    elif failures:
        status = "complete_with_failures"
    elif dry_runs and not successes:
        status = "dry_run"
    else:
        status = "success"
    rows = []
    for item in runs:
        result = item.get("result") if isinstance(item.get("result"), dict) else {}
        rows.append(
            {
                "condition": item.get("condition"),
                "replicate": item.get("replicate"),
                "attempt": item.get("attempt", 1),
                "execution_mode": item.get("execution_mode"),
                "status": item.get("status"),
                "recommendation": result.get("recommendation"),
                "data_coverage": result.get("data_coverage"),
                "gate_a": result.get("gate_a_status"),
                "gate_b": result.get("gate_b_status"),
                "writer_gate": result.get("writer_validation_status"),
                "cards": result.get("strategy_card_count"),
                "strategy_context_mode": result.get("strategy_context_mode"),
                "generation_payload_bytes": result.get("generation_payload_bytes"),
                "observed_logical_calls": result.get("observed_logical_calls"),
                "expected_logical_calls": result.get("expected_logical_calls"),
                "usage_accounting_status": result.get("usage_accounting_status"),
                "usage_caveat": result.get("usage_caveat") or "",
                "input_tokens": result.get("input_tokens"),
                "output_tokens": result.get("output_tokens"),
                "total_tokens": result.get("total_tokens"),
                "elapsed_seconds": item.get("elapsed_seconds"),
                "pipeline_manifest": item.get("pipeline_manifest"),
                "report_html": result.get("report_html"),
                "error": result.get("error") or {},
                "writer_blocking_failures": result.get("writer_blocking_failures") or [],
            }
        )
    return {
        "suite_id": manifest.get("suite_id"),
        "status": status,
        "request": manifest.get("request") or {},
        "counts": {
            "planned": len(runs),
            "terminal": len(terminal),
            "success": len(successes),
            "failed": len(failures),
            "dry_run": len(dry_runs),
        },
        "runs": rows,
    }


def render_summary_markdown(summary: dict[str, Any]) -> str:
    lines = [
        "# Ablation experiment summary",
        "",
        f"- Suite: `{summary.get('suite_id')}`",
        f"- Status: `{summary.get('status')}`",
        f"- Counts: `{json.dumps(summary.get('counts') or {}, ensure_ascii=False)}`",
        "",
        "| Condition | Rep | Status | Opinion | Coverage | Gate A | Gate B | Writer | Cards | Calls | Tokens | Seconds |",
        "| --- | ---: | --- | --- | --- | --- | --- | --- | ---: | ---: | ---: | ---: |",
    ]
    for row in summary.get("runs") or []:
        if not isinstance(row, dict):
            continue
        lines.append(
            "| {condition} | {replicate} | {status} | {recommendation} | {data_coverage} | "
            "{gate_a} | {gate_b} | {writer_gate} | {cards} | {calls} | {total_tokens} | "
            "{elapsed_seconds} |".format(
                calls=(
                    f"{row.get('observed_logical_calls', '')}/"
                    f"{row.get('expected_logical_calls', '')}"
                ),
                **{key: row.get(key, "") for key in row},
            )
        )
    caveats = [
        row for row in summary.get("runs") or []
        if isinstance(row, dict) and row.get("usage_caveat")
    ]
    if caveats:
        lines.extend(["", "## Usage accounting caveats", ""])
        for row in caveats:
            lines.append(f"- `{row.get('condition')}`: {row.get('usage_caveat')}")
    return "\n".join(lines).rstrip() + "\n"


def code_identity() -> dict[str, Any]:
    commit = _run_git(["rev-parse", "HEAD"])
    branch = _run_git(["branch", "--show-current"])
    status = _run_git(["status", "--short"])
    diff = _run_git(["diff", "--binary", "HEAD"])
    return {
        "git_commit": commit,
        "git_branch": branch,
        "git_dirty": bool(status),
        "git_status": status.splitlines(),
        "git_diff_sha256": hashlib.sha256(diff.encode("utf-8")).hexdigest(),
        "python": sys.version,
    }


def _run_git(args: list[str]) -> str:
    completed = subprocess.run(
        ["git", *args],
        cwd=str(PROJECT_ROOT),
        text=True,
        capture_output=True,
        check=False,
    )
    return completed.stdout.strip() if completed.returncode == 0 else ""


def _nested_status(payload: dict[str, Any], key: str) -> Any:
    value = payload.get(key)
    if isinstance(value, dict):
        return value.get("status") or "pass"
    return value


def _nested_value(payload: dict[str, Any], *keys: str) -> Any:
    value: Any = payload
    for key in keys:
        if not isinstance(value, dict):
            return None
        value = value.get(key)
    return value


def _path_exists(path: Any) -> bool:
    return bool(str(path or "").strip()) and Path(str(path)).expanduser().resolve().exists()


def _sha256_optional(path: Any) -> str:
    if not _path_exists(path):
        return ""
    resolved = Path(str(path)).expanduser().resolve()
    digest = hashlib.sha256()
    with resolved.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _sibling_output(path: Any, filename: str) -> Path | None:
    if not str(path or "").strip():
        return None
    return Path(str(path)).expanduser().resolve().parent / filename


def _load_json_optional(path: str | Path | None) -> dict[str, Any]:
    if path is None:
        return {}
    resolved = Path(path).expanduser().resolve()
    if not resolved.exists():
        return {}
    try:
        return _load_json(resolved)
    except (OSError, ValueError, json.JSONDecodeError):
        return {}


def _load_json(path: str | Path) -> dict[str, Any]:
    resolved = Path(path).expanduser().resolve()
    payload = json.loads(resolved.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"JSON artifact must be an object: {resolved}")
    return payload


def _write_json(path: Path, payload: Any) -> None:
    _atomic_write(path, json.dumps(payload, ensure_ascii=False, indent=2) + "\n")


def _write_text(path: Path, text: str) -> None:
    _atomic_write(path, text)


def _atomic_write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=str(path.parent))
    try:
        with open(descriptor, "w", encoding="utf-8", closefd=True) as handle:
            handle.write(text)
        Path(temporary).replace(path)
    finally:
        temporary_path = Path(temporary)
        if temporary_path.exists():
            temporary_path.unlink()


def _positive_int(value: str) -> int:
    parsed = int(value)
    if parsed < 1:
        raise argparse.ArgumentTypeError("value must be >= 1")
    return parsed


def _non_negative_int(value: str) -> int:
    parsed = int(value)
    if parsed < 0:
        raise argparse.ArgumentTypeError("value must be >= 0")
    return parsed


def _new_suite_id() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")


def _safe_label(value: str) -> str:
    label = str(value or "").strip() or _new_suite_id()
    for character in ('\\', '/', ':', '*', '?', '"', '<', '>', '|', ' '):
        label = label.replace(character, "_")
    return label.strip("._") or _new_suite_id()


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    summary = run_ablation_suite(args)
    print(json.dumps(summary["counts"], ensure_ascii=False), flush=True)
    return 1 if summary["status"] == "complete_with_failures" else 0


if __name__ == "__main__":
    raise SystemExit(main())
