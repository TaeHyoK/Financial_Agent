"""Subprocess-based Layer 1 to Layer 2 Agent_Team orchestration."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
from datetime import datetime, timedelta
from pathlib import Path

from tqdm.auto import tqdm

from .config import DEFAULT_CONFIG_PATH, DEFAULT_ENV_FILE, DEFAULT_NEWS_CONFIG_PATH, load_run_config
from .dependency_graph import STEP_SPECS
from .manifest import write_financial_runtime_manifest, write_run_config_copy, write_run_files
from .paths import RunPaths, resolve_run_paths
from .run_state import FAILED, SUCCESS, StepRecord


DEFAULT_KOSPI_TICKER = "^KS11"
DEFAULT_FX_TICKER = "KRW=X"
DEFAULT_NEWS_GRANULARITY = "week"
DEFAULT_NEWS_RAW_PERIOD_COUNT = 14
STEP_FINGERPRINT_VERSION = "1"
FINGERPRINT_SOURCE_SUFFIXES = {".py", ".md", ".json", ".yaml", ".yml", ".toml"}
REUSED_DOMAIN_SNAPSHOT_STEPS = frozenset(
    {
        "yfinance_layer_1",
        "financial_layer_1",
        "news_collect",
        "news_export",
        "news_llm",
    }
)


class AgentTeamOrchestrator:
    """Run existing team CLIs and persist a global run manifest."""

    def __init__(self, args: argparse.Namespace) -> None:
        self.args = args
        self.run_config = load_run_config(args.config)
        self.paths = resolve_run_paths(self.run_config, args.output_root)
        self.paths.ensure_directories()
        write_run_config_copy(self.paths, self.run_config)
        self.reused_domain_snapshot: dict[str, object] = {}
        self.reused_snapshot_steps: set[str] = set()
        if self.args.reuse_domain_data_from and self.args.reuse_dart_data_from:
            raise ValueError(
                "--reuse-domain-data-from and --reuse-dart-data-from cannot be used together."
            )
        if self.args.reuse_domain_data_from:
            self.reused_domain_snapshot = materialize_reused_domain_snapshot(
                run_config=self.run_config,
                source_root=self.args.reuse_domain_data_from,
                destination_paths=self.paths,
                expected_news_model=(
                    self.args.news_llm_model or self.common_llm_model()
                ),
            )
            self.reused_snapshot_steps = set(REUSED_DOMAIN_SNAPSHOT_STEPS)
        elif self.args.reuse_dart_data_from:
            self.reused_domain_snapshot = materialize_reused_dart_snapshot(
                run_config=self.run_config,
                source_root=self.args.reuse_dart_data_from,
                destination_paths=self.paths,
            )
            self.reused_snapshot_steps = {"financial_layer_1"}
        if self.reused_domain_snapshot:
            snapshot_manifest = self.paths.run_dir / "reused_domain_snapshot.json"
            snapshot_manifest.write_text(
                json.dumps(self.reused_domain_snapshot, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )
        write_financial_runtime_manifest(
            self.paths,
            self.run_config,
            primary_data_only=self.args.primary_data_only,
        )
        self.fingerprint_state = load_fingerprint_state(self.paths.step_fingerprints)
        self.steps: list[StepRecord] = []
        self.step_by_name: dict[str, StepRecord] = {}
        self.progress: tqdm | None = None

    def run(self) -> int:
        with tqdm(
            total=len(STEP_SPECS),
            desc=f"Agent Team E2E: {self.paths.run_key}",
            unit="step",
            disable=self.args.no_progress,
        ) as progress:
            self.progress = progress
            for spec in STEP_SPECS:
                progress.set_postfix_str(f"{spec.name}: pending")
                record = StepRecord(name=spec.name)
                self.steps.append(record)
                self.step_by_name[spec.name] = record
                command = self.command_for_step(spec.name)
                record.command = command
                record.outputs = self.outputs_for_step(spec.name)
                record.input_fingerprint = self._step_fingerprint(
                    step_name=spec.name,
                    command=command,
                    dependencies=spec.dependencies,
                    outputs=record.outputs,
                )

                if spec.name in self.args.skip_step:
                    record.skip("Skipped by --skip-step.")
                    self._write_state()
                    self._finish_progress_step(record)
                    continue

                if spec.name in self.reused_snapshot_steps:
                    record.reuse(
                        "Reused the explicitly selected provider snapshot for this step."
                    )
                    self._post_step(spec.name, record)
                    self._save_step_fingerprint(record)
                    self._write_state()
                    self._finish_progress_step(record)
                    continue

                if spec.name not in self.args.force_step and self._can_reuse(record):
                    record.reuse("Reused outputs with matching input, code, dependency, and output fingerprints.")
                    self._write_state()
                    self._finish_progress_step(record)
                    continue

                if spec.requires_llm and not self.args.use_llm:
                    record.skip("LLM-dependent step skipped because --use-llm was not set.")
                    self._write_state()
                    self._finish_progress_step(record)
                    continue

                if self.args.dry_run:
                    record.skip("Dry run; command was not executed.")
                    self._write_state()
                    self._finish_progress_step(record)
                    continue

                failed_dependency = self._failed_dependency(spec.dependencies)
                if failed_dependency:
                    record.skip(f"Dependency did not succeed: {failed_dependency}.")
                    self._write_state()
                    self._finish_progress_step(record)
                    continue

                progress.set_postfix_str(f"{spec.name}: running")
                self._run_record(record)
                self._post_step(spec.name, record)
                if record.status == SUCCESS:
                    self._save_step_fingerprint(record)
                self._write_state()
                self._finish_progress_step(record)

                if record.status == FAILED and not self.args.continue_on_error:
                    self._skip_remaining_after_failure(spec.name)
                    break
            self.progress = None

        manifest = self._write_state()
        if self.args.dry_run:
            return 0
        return 0 if manifest["status"] == SUCCESS else 1

    def command_for_step(self, step_name: str) -> list[str]:
        if step_name == "yfinance_layer_1":
            return self._yfinance_layer_1_command()
        if step_name == "financial_layer_1":
            return self._financial_layer_1_command()
        if step_name.startswith("news_"):
            return self._news_phase_command(step_name.removeprefix("news_"))
        if step_name == "financial_analyst":
            return self._financial_analyst_command()
        if step_name == "yfinance_report":
            return self._yfinance_report_command()
        raise KeyError(f"Unknown step: {step_name}")

    def common_llm_model(self) -> str:
        if self.args.llm_model and self.args.llm_model != "auto":
            return self.args.llm_model
        if self.run_config.llm_model:
            return self.run_config.llm_model
        return "gpt-5.4"

    def outputs_for_step(self, step_name: str) -> dict[str, str]:
        if step_name == "yfinance_layer_1":
            return {
                "market_summary": str(self.paths.market_summary),
                "market_summary_dated": str(self.paths.market_summary_dated),
                "valuation_snapshot": str(self.paths.valuation_snapshot),
            }
        if step_name == "financial_layer_1":
            return {
                "dart_main": str(self.paths.dart_main),
                "dart_lightweight": str(self.paths.dart_lightweight),
            }
        if step_name == "news_collect":
            return {"report_context": str(self.paths.news_report_context)}
        if step_name == "news_export":
            return {
                "llm_summary_request": str(
                    self.paths.news_context_export_dir / self.args.news_granularity / "llm_summary_request.json"
                ),
                "company_related_news_top20": str(self.paths.news_company_top20),
            }
        if step_name == "news_llm":
            return {"llm_period_summaries": str(self.paths.news_llm_period_summaries)}
        if step_name == "news_analysis":
            return {"handoff": str(self.paths.news_handoff)}
        if step_name == "financial_analyst":
            return {
                "analyst_report": str(self.paths.financial_analyst_report),
                "analyst_trace": str(self.paths.financial_analyst_trace),
            }
        if step_name == "yfinance_report":
            return {
                "analyst_report": str(self.paths.yfinance_analyst_report),
                "analyst_report_md": str(self.paths.yfinance_analyst_report_md),
            }
        return {}

    def _yfinance_layer_1_command(self) -> list[str]:
        start_date, end_date = self._market_date_range()
        return [
            sys.executable,
            str(self.paths.project_root / "src" / "Agent_Team" / "YFinance_Agent" / "main.py"),
            "--input",
            str(self.run_config.config_path),
            "--output-dir",
            str(self.paths.yfinance_dir),
            "--start-date",
            start_date,
            "--end-date",
            end_date,
            "--selected-date",
            self.run_config.selected_date,
            "--kospi-ticker",
            self.args.kospi_ticker,
            "--fx-ticker",
            self.args.fx_ticker,
            "--log-level",
            self.args.log_level,
        ]

    def _financial_layer_1_command(self) -> list[str]:
        return [
            sys.executable,
            "-m",
            "Agent_Team.Financial_Agent.main",
            "--input",
            str(self.run_config.config_path),
            "--output-dir",
            str(self.paths.financial_dir),
            "--env-file",
            str(self.args.env_file),
            "--log-level",
            self.args.log_level,
        ]

    def _market_date_range(self) -> tuple[str, str]:
        if self.args.market_window_days is None:
            return self.run_config.start_date, self.run_config.end_date
        selected = datetime.strptime(self.run_config.selected_date, "%Y%m%d").date()
        window_days = max(1, int(self.args.market_window_days))
        end = selected - timedelta(days=1)
        start = end - timedelta(days=window_days - 1)
        return start.strftime("%Y%m%d"), end.strftime("%Y%m%d")

    def _date_range_days(self) -> int:
        start = datetime.strptime(self.run_config.start_date, "%Y%m%d").date()
        end = datetime.strptime(self.run_config.end_date, "%Y%m%d").date()
        return max(1, (end - start).days + 1)

    def _news_collection_days(self) -> int:
        if self.args.news_collection_days is not None:
            return max(1, int(self.args.news_collection_days))
        return self._date_range_days()

    def _news_period_count(self) -> int:
        if self.args.news_period_count is not None:
            return max(1, int(self.args.news_period_count))
        if self.args.news_granularity == "day":
            return self._news_collection_days()
        if self.args.news_granularity == "week":
            end = datetime.strptime(
                self.run_config.information_cutoff_date, "%Y%m%d"
            ).date()
            start = end - timedelta(days=self._news_collection_days() - 1)
            return len(
                {
                    (start + timedelta(days=offset)).isocalendar()[:2]
                    for offset in range((end - start).days + 1)
                }
            )
        return max(1, int(self.args.news_period_count or 1))

    def _news_phase_command(self, phase: str) -> list[str]:
        news_collection_days = self._news_collection_days()
        news_period_count = self._news_period_count()
        command = [
            sys.executable,
            "-m",
            "Agent_Team.News_Agent.cli",
            "--phase",
            phase,
            "--collect-date",
            self.run_config.information_cutoff_date_iso,
            "--company-id",
            self.run_config.company_code,
            "--company-name",
            self.run_config.company_name,
            "--ticker",
            self.run_config.ticker,
            "--corp-code",
            self.run_config.corp_code,
            "--as-of-date",
            self.run_config.selected_date_iso,
            "--granularity",
            self.args.news_granularity,
            "--period-count",
            str(news_period_count),
            "--raw-period-count",
            str(self.args.news_raw_period_count),
            "--min-mention-count",
            str(self.args.news_min_mention_count),
            "--context-export-dir",
            str(self.paths.news_context_export_dir),
            "--analysis-output-dir",
            str(self.paths.news_analysis_output_dir),
            "--dart-lightweight",
            str(self.paths.dart_lightweight),
            "--market-summary",
            str(self.paths.market_summary),
            "--env-path",
            str(self.args.env_file),
            "--config",
            str(self.args.news_config),
            "--timeout-seconds",
            str(self.args.timeout_seconds),
        ]
        if self.args.news_split_by_period:
            command.append("--split-by-period")
        if self.args.primary_data_only:
            command.append("--primary-data-only")
        if self.args.news_query:
            command.extend(["--query", self.args.news_query])
        command.extend(["--collection-days", str(news_collection_days)])
        if self.args.news_max_results is not None:
            command.extend(["--max-results", str(self.args.news_max_results)])
        if self.args.news_total_max_results is not None:
            command.extend(
                ["--event-top-k", str(self.args.news_total_max_results)]
            )
        command.extend(["--llm-model", self.args.news_llm_model or self.common_llm_model()])
        command.extend(["--analysis-model", self.args.news_analysis_model or self.common_llm_model()])
        return command

    def _financial_analyst_command(self) -> list[str]:
        command = [
            sys.executable,
            str(self.paths.project_root / "src" / "Agent_Team" / "Financial_Agent" / "langgraph_flow.py"),
            "--manifest",
            str(self.paths.financial_runtime_manifest),
            "--output",
            str(self.paths.financial_analyst_report),
            "--trace-output",
            str(self.paths.financial_analyst_trace),
            "--model",
            self.common_llm_model(),
        ]
        return command

    def _yfinance_report_command(self) -> list[str]:
        command = [
            sys.executable,
            str(self.paths.project_root / "src" / "Agent_Team" / "YFinance_Agent" / "report.py"),
            "--market-json",
            str(self.paths.yfinance_dir / "market_full_dataset.json"),
            "--dart-json",
            str(self.paths.dart_lightweight),
            "--news-json",
            str(self.paths.news_llm_period_summaries),
            "--valuation-json",
            str(self.paths.valuation_snapshot),
            "--report-md",
            str(self.paths.yfinance_analyst_report_md),
            "--report-json",
            str(self.paths.yfinance_analyst_report),
            "--company-name",
            self.run_config.company_name,
            "--ticker",
            self.run_config.ticker,
            "--env-file",
            str(self.args.env_file),
        ]
        command.extend(["--model", self.args.yfinance_model or self.common_llm_model()])
        if self.args.primary_data_only:
            command.append("--primary-data-only")
        return command

    def _run_record(self, record: StepRecord) -> None:
        started = record.start(record.command)
        completed = subprocess.run(
            record.command,
            cwd=str(self.paths.project_root),
            env=self._subprocess_env(),
            text=True,
            capture_output=True,
            check=False,
        )
        status = SUCCESS if completed.returncode == 0 else FAILED
        record.finish(
            status=status,
            started=started,
            returncode=completed.returncode,
            stdout=completed.stdout,
            stderr=completed.stderr,
        )
        if completed.stdout.strip():
            self._write_progress_message(completed.stdout.strip())
        if completed.stderr.strip():
            self._write_progress_message(completed.stderr.strip(), stderr=True)

    def _subprocess_env(self) -> dict[str, str]:
        env = os.environ.copy()
        src = str(self.paths.project_root / "src")
        current = env.get("PYTHONPATH", "")
        env["PYTHONPATH"] = src if not current else src + os.pathsep + current
        env["OPENAI_MODEL"] = self.common_llm_model()
        env["NEWS_AGENT_LLM_MODEL"] = self.common_llm_model()
        env["LLM_USAGE_MANIFEST"] = str(self.args.llm_usage_manifest or self.paths.llm_usage_manifest)
        env["LLM_RUN_ID"] = self.args.llm_run_id or self.paths.run_key
        env["LLM_RUN_ROLE"] = self.args.llm_run_role
        env["LLM_COMPANY_NAME"] = self.run_config.company_name
        env["LLM_EXECUTION_ID"] = self.args.llm_execution_id
        return env

    def _step_fingerprint(
        self,
        *,
        step_name: str,
        command: list[str],
        dependencies: tuple[str, ...],
        outputs: dict[str, str],
    ) -> str:
        output_paths = {str(Path(path).expanduser().resolve()) for path in outputs.values()}
        command_inputs: dict[str, str] = {}
        for raw_value in command[1:]:
            candidate = Path(raw_value).expanduser()
            if not candidate.exists() or not candidate.is_file():
                continue
            resolved = candidate.resolve()
            if str(resolved) in output_paths or resolved == self.args.env_file:
                continue
            command_inputs[str(resolved)] = file_sha256(resolved)

        dependency_outputs: dict[str, dict[str, str]] = {}
        for dependency in dependencies:
            dependency_outputs[dependency] = {
                name: file_sha256(Path(path))
                for name, path in self.outputs_for_step(dependency).items()
                if Path(path).exists()
            }
        payload = {
            "version": STEP_FINGERPRINT_VERSION,
            "step": step_name,
            "command": command,
            "command_inputs": command_inputs,
            "dependency_outputs": dependency_outputs,
            "source_hash": self._step_source_hash(step_name),
        }
        return hashlib.sha256(
            json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
        ).hexdigest()

    def _step_source_hash(self, step_name: str) -> str:
        if step_name.startswith("news_"):
            agent_dir = self.paths.project_root / "src" / "Agent_Team" / "News_Agent"
        elif step_name.startswith("financial_"):
            agent_dir = self.paths.project_root / "src" / "Agent_Team" / "Financial_Agent"
        elif step_name.startswith("yfinance_"):
            agent_dir = self.paths.project_root / "src" / "Agent_Team" / "YFinance_Agent"
        else:
            raise KeyError(f"Unknown step source root: {step_name}")
        roots = [agent_dir, self.paths.project_root / "src" / "shared"]
        return hash_source_trees(roots)

    def _can_reuse(self, record: StepRecord) -> bool:
        if not outputs_exist(record.outputs):
            return False
        previous = (self.fingerprint_state.get("steps") or {}).get(record.name)
        if not isinstance(previous, dict) or previous.get("input_fingerprint") != record.input_fingerprint:
            return False
        current_outputs = {
            name: file_sha256(Path(path))
            for name, path in record.outputs.items()
        }
        return previous.get("output_hashes") == current_outputs

    def _save_step_fingerprint(self, record: StepRecord) -> None:
        steps = self.fingerprint_state.setdefault("steps", {})
        steps[record.name] = {
            "input_fingerprint": record.input_fingerprint,
            "output_hashes": {
                name: file_sha256(Path(path))
                for name, path in record.outputs.items()
                if Path(path).exists()
            },
        }
        self.fingerprint_state["version"] = STEP_FINGERPRINT_VERSION
        self.paths.step_fingerprints.write_text(
            json.dumps(self.fingerprint_state, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )

    def _post_step(self, step_name: str, record: StepRecord) -> None:
        if record.status != SUCCESS:
            return
        if step_name == "yfinance_layer_1":
            ensure_market_summary_alias(self.paths)
        if step_name == "financial_layer_1":
            write_financial_runtime_manifest(
                self.paths,
                self.run_config,
                primary_data_only=self.args.primary_data_only,
            )
        if step_name == "financial_analyst":
            self._write_financial_pipeline_manifest()

    def _failed_dependency(self, dependencies: tuple[str, ...]) -> str | None:
        for dependency in dependencies:
            record = self.step_by_name.get(dependency)
            if record and record.status != SUCCESS:
                return dependency
        return None

    def _skip_remaining_after_failure(self, failed_step: str) -> None:
        known = {step.name for step in self.steps}
        for spec in STEP_SPECS:
            if spec.name in known:
                continue
            record = StepRecord(name=spec.name, command=self.command_for_step(spec.name))
            record.outputs = self.outputs_for_step(spec.name)
            record.skip(f"Skipped after failed step: {failed_step}.")
            self.steps.append(record)
            self.step_by_name[spec.name] = record
            self._finish_progress_step(record)

    def _write_state(self) -> dict:
        return write_run_files(
            self.paths,
            self.run_config,
            self.steps,
            dry_run=self.args.dry_run,
            llm_usage_manifest=self.args.llm_usage_manifest,
            llm_execution_id=self.args.llm_execution_id,
            llm_run_id=self.args.llm_run_id or self.paths.run_key,
            expected_step_count=len(STEP_SPECS),
        )

    def _write_financial_pipeline_manifest(self) -> None:
        self.paths.financial_pipeline_manifest.write_text(
            json.dumps(
                {
                    "financial_analyst_output": str(self.paths.financial_analyst_report),
                    "financial_analyst_trace": str(self.paths.financial_analyst_trace),
                    "final_report_output": str(self.paths.financial_final_report),
                    "processing_policy": "direct_domain_agent_output",
                },
                ensure_ascii=False,
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )

    def _finish_progress_step(self, record: StepRecord) -> None:
        if self.progress is None:
            return
        self.progress.set_postfix_str(f"{record.name}: {record.status}")
        self.progress.update(1)

    def _write_progress_message(self, message: str, *, stderr: bool = False) -> None:
        if self.progress is not None:
            self.progress.write(message)
        elif stderr:
            print(message, file=sys.stderr)
        else:
            print(message)


def ensure_market_summary_alias(paths: RunPaths) -> None:
    """Keep the dated YFinance summary and the common alias in sync."""

    if paths.market_summary_dated.exists():
        paths.market_summary.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(paths.market_summary_dated, paths.market_summary)


def materialize_reused_domain_snapshot(
    *,
    run_config,
    source_root: str | Path,
    destination_paths: RunPaths,
    expected_news_model: str | None = None,
) -> dict[str, object]:
    """Copy only the fixed provider/News-summary inputs needed by downstream agents."""

    source_paths = resolve_run_paths(run_config, source_root)
    if source_paths.output_root == destination_paths.output_root:
        raise ValueError("Reused domain snapshot source and destination roots must differ.")
    source_status = _load_json_object(source_paths.run_status)
    if source_status.get("status") != SUCCESS or source_status.get("pipeline_completed") is not True:
        raise ValueError(
            f"Reused domain snapshot must come from a completed successful run: {source_paths.run_status}"
        )

    summary_source = source_paths.news_llm_period_summaries
    summary_payload = _load_json_object(summary_source)
    summary_model = str(summary_payload.get("model") or "").strip()
    if expected_news_model and summary_model and summary_model != expected_news_model:
        raise ValueError(
            "Reused News summary model does not match the requested model: "
            f"snapshot={summary_model}, requested={expected_news_model}, "
            f"file={summary_source}"
        )

    file_pairs = [
        (source_paths.yfinance_dir / "market_full_dataset.json", destination_paths.yfinance_dir / "market_full_dataset.json"),
        (source_paths.yfinance_dir / "market_full_dataset.csv", destination_paths.yfinance_dir / "market_full_dataset.csv"),
        (source_paths.market_summary_dated, destination_paths.market_summary_dated),
        (source_paths.market_summary, destination_paths.market_summary),
        (source_paths.valuation_snapshot, destination_paths.valuation_snapshot),
        (source_paths.dart_main, destination_paths.dart_main),
        (source_paths.dart_master, destination_paths.dart_master),
        (source_paths.dart_lightweight, destination_paths.dart_lightweight),
        (
            _reused_news_report_context_source(source_paths, run_config),
            destination_paths.news_report_context,
        ),
    ]
    copied: list[dict[str, str]] = []
    for source, destination in file_pairs:
        copied.append(_copy_snapshot_file(source, destination))

    context_source = source_paths.news_context_export_dir
    if not context_source.is_dir():
        raise ValueError(f"Missing News context export snapshot: {context_source}")
    for source in sorted(path for path in context_source.rglob("*") if path.is_file()):
        relative = source.relative_to(context_source)
        destination = destination_paths.news_context_export_dir / relative
        copied.append(_copy_snapshot_file(source, destination))

    required_outputs = {
        step: destination_paths_for_step(destination_paths, step)
        for step in REUSED_DOMAIN_SNAPSHOT_STEPS
    }
    missing = [
        str(path)
        for outputs in required_outputs.values()
        for path in outputs.values()
        if not Path(path).exists()
    ]
    if missing:
        raise ValueError(f"Reused domain snapshot is incomplete after materialization: {missing}")
    return {
        "status": "materialized",
        "snapshot_scope": "all_collected_domain_inputs",
        "source_root": str(source_paths.output_root),
        "destination_root": str(destination_paths.output_root),
        "run_key": destination_paths.run_key,
        "selected_date": destination_paths.selected_date,
        "news_summary_model": summary_model or "unrecorded",
        "expected_news_model": expected_news_model or "",
        "reused_steps": sorted(REUSED_DOMAIN_SNAPSHOT_STEPS),
        "files": copied,
    }


def materialize_reused_dart_snapshot(
    *,
    run_config,
    source_root: str | Path,
    destination_paths: RunPaths,
) -> dict[str, object]:
    """Copy only point-in-time DART preprocessing outputs from a prior run."""

    source_paths = resolve_run_paths(run_config, source_root)
    if source_paths.output_root == destination_paths.output_root:
        raise ValueError("Reused DART snapshot source and destination roots must differ.")
    pairs = [
        (source_paths.dart_main, destination_paths.dart_main),
        (source_paths.dart_master, destination_paths.dart_master),
        (source_paths.dart_lightweight, destination_paths.dart_lightweight),
    ]
    for source, _destination in pairs:
        payload = _load_json_object(source)
        context = payload.get("collection_context")
        if not isinstance(context, dict):
            raise ValueError(f"Reused DART snapshot has no collection_context: {source}")
        snapshot_date = str(context.get("selected_date") or "").replace("-", "")
        if snapshot_date != run_config.selected_date:
            raise ValueError(
                f"Reused DART snapshot selected date does not match {run_config.selected_date}: {source}"
            )
    copied = [_copy_snapshot_file(source, destination) for source, destination in pairs]
    return {
        "status": "materialized",
        "snapshot_scope": "dart_preprocessing_only",
        "source_root": str(source_paths.output_root),
        "destination_root": str(destination_paths.output_root),
        "run_key": destination_paths.run_key,
        "selected_date": destination_paths.selected_date,
        "reused_steps": ["financial_layer_1"],
        "regenerated_steps": [
            "yfinance_layer_1",
            "news_collect",
            "news_export",
            "news_llm",
            "news_analysis",
            "financial_analyst",
            "yfinance_report",
        ],
        "files": copied,
    }


def _reused_news_report_context_source(
    source_paths: RunPaths,
    run_config,
) -> Path:
    """Resolve current and legacy News pack directory conventions."""

    current = source_paths.news_report_context
    legacy = (
        source_paths.output_root
        / "News"
        / "artifacts"
        / "reports"
        / "packs"
        / f"{run_config.company_name}_{run_config.information_cutoff_date}"
        / "report_context.json"
    )
    return current if current.exists() else legacy


def destination_paths_for_step(paths: RunPaths, step_name: str) -> dict[str, str]:
    """Return snapshot-stage outputs without requiring an orchestrator instance."""

    if step_name == "yfinance_layer_1":
        return {
            "market_summary": str(paths.market_summary),
            "market_summary_dated": str(paths.market_summary_dated),
            "valuation_snapshot": str(paths.valuation_snapshot),
        }
    if step_name == "financial_layer_1":
        return {
            "dart_main": str(paths.dart_main),
            "dart_lightweight": str(paths.dart_lightweight),
        }
    if step_name == "news_collect":
        return {"report_context": str(paths.news_report_context)}
    if step_name == "news_export":
        return {
            "llm_summary_request": str(paths.news_context_export_week_dir / "llm_summary_request.json")
        }
    if step_name == "news_llm":
        return {"llm_period_summaries": str(paths.news_llm_period_summaries)}
    raise KeyError(f"Step cannot be provided by a reused domain snapshot: {step_name}")


def _copy_snapshot_file(source: Path, destination: Path) -> dict[str, str]:
    if not source.is_file():
        raise ValueError(f"Missing reused domain snapshot file: {source}")
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, destination)
    source_hash = file_sha256(source)
    destination_hash = file_sha256(destination)
    if source_hash != destination_hash:
        raise ValueError(f"Snapshot copy hash mismatch: {source} -> {destination}")
    return {
        "source": str(source.resolve()),
        "destination": str(destination.resolve()),
        "sha256": source_hash,
    }


def _load_json_object(path: Path) -> dict:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as exc:
        raise ValueError(f"Unable to read reused domain snapshot status: {path}") from exc
    if not isinstance(payload, dict):
        raise ValueError(f"Reused domain snapshot status must be a JSON object: {path}")
    return payload


def outputs_exist(outputs: dict[str, str]) -> bool:
    return bool(outputs) and all(Path(path).exists() for path in outputs.values())


def load_fingerprint_state(path: Path) -> dict:
    if not path.exists():
        return {"version": STEP_FINGERPRINT_VERSION, "steps": {}}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {"version": STEP_FINGERPRINT_VERSION, "steps": {}}
    if not isinstance(payload, dict) or payload.get("version") != STEP_FINGERPRINT_VERSION:
        return {"version": STEP_FINGERPRINT_VERSION, "steps": {}}
    if not isinstance(payload.get("steps"), dict):
        payload["steps"] = {}
    return payload


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def hash_source_trees(roots: list[Path]) -> str:
    digest = hashlib.sha256()
    files: list[tuple[Path, Path]] = []
    for root in roots:
        if not root.exists():
            continue
        for path in root.rglob("*"):
            if not path.is_file() or path.suffix.lower() not in FINGERPRINT_SOURCE_SUFFIXES:
                continue
            if "__pycache__" in path.parts or "tests" in path.parts:
                continue
            if path.suffix.lower() == ".md" and "prompts" not in path.parts:
                continue
            files.append((root, path))
    for root, path in sorted(files, key=lambda item: (str(item[0]), str(item[1]))):
        digest.update(str(path.relative_to(root)).encode("utf-8"))
        digest.update(file_sha256(path).encode("ascii"))
    return digest.hexdigest()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run Agent_Team Layer 1 to Layer 2 orchestration.")
    parser.add_argument("--config", default=str(DEFAULT_CONFIG_PATH), help="Common run config JSON.")
    parser.add_argument("--env-file", default=str(DEFAULT_ENV_FILE), type=Path, help="Env file for API keys.")
    parser.add_argument("--output-root", default=None, help="Override Output_total root.")
    parser.add_argument("--use-llm", action="store_true", help="Run LLM-dependent Layer 2 phases.")
    parser.add_argument(
        "--primary-data-only",
        action="store_true",
        help="Remove cross-domain secondary/subdata from each domain-agent request.",
    )
    parser.add_argument("--dry-run", action="store_true", help="Write commands/manifest without executing team CLIs.")
    parser.add_argument("--no-progress", action="store_true", help="Disable top-level tqdm progress output.")
    parser.add_argument(
        "--reuse-existing",
        action="store_true",
        help="Deprecated compatibility flag. Exact fingerprint reuse is automatic.",
    )
    parser.add_argument(
        "--reuse-domain-data-from",
        type=Path,
        default=None,
        metavar="OUTPUT_ROOT",
        help=(
            "Reuse a completed run's DART, market, and News collection/summary snapshot, "
            "then rerun downstream domain analysis and report generation."
        ),
    )
    parser.add_argument(
        "--reuse-dart-data-from",
        type=Path,
        default=None,
        metavar="OUTPUT_ROOT",
        help=(
            "Reuse only DART preprocessing outputs for the same company and selected date; "
            "market data, News data, summaries, and every LLM analysis are regenerated."
        ),
    )
    parser.add_argument(
        "--force-step",
        action="append",
        default=[],
        choices=[spec.name for spec in STEP_SPECS],
        help="Force one step to run even when its fingerprint matches. Can be repeated.",
    )
    parser.add_argument("--continue-on-error", action="store_true", help="Continue independent steps after a failure.")
    parser.add_argument("--skip-step", action="append", default=[], help="Skip a step by name. Can be repeated.")
    parser.add_argument("--log-level", default="INFO")
    parser.add_argument("--kospi-ticker", default=DEFAULT_KOSPI_TICKER)
    parser.add_argument("--fx-ticker", default=DEFAULT_FX_TICKER)
    parser.add_argument("--market-window-days", type=int, default=None)
    parser.add_argument("--news-config", default=str(DEFAULT_NEWS_CONFIG_PATH), type=Path)
    parser.add_argument(
        "--news-query",
        default="",
        help=(
            "Optional target-specific Google News query override. The full-report "
            "orchestrator only forwards this to the target company, never its peer."
        ),
    )
    parser.add_argument("--news-collection-days", type=int, default=None)
    parser.add_argument("--news-max-results", type=int, default=None)
    parser.add_argument(
        "--news-event-top-k",
        "--news-total-max-results",
        dest="news_total_max_results",
        type=int,
        default=None,
        help=(
            "Final globally ranked News events retained after weekly selection. "
            "--news-total-max-results is a compatibility alias."
        ),
    )
    parser.add_argument("--news-min-mention-count", type=int, default=1)
    parser.add_argument("--news-granularity", default=DEFAULT_NEWS_GRANULARITY, choices=["day", "week", "month"])
    parser.add_argument("--news-period-count", type=int, default=None)
    parser.add_argument("--news-raw-period-count", type=int, default=DEFAULT_NEWS_RAW_PERIOD_COUNT)
    parser.add_argument("--news-llm-model", default=None)
    parser.add_argument("--news-analysis-model", default=None)
    parser.add_argument(
        "--news-split-by-period",
        dest="news_split_by_period",
        action="store_true",
        help="Call the News summary LLM once per period instead of using the default batched request.",
    )
    parser.add_argument("--no-news-split-by-period", dest="news_split_by_period", action="store_false")
    parser.set_defaults(news_split_by_period=False)
    parser.add_argument("--timeout-seconds", type=float, default=300.0)
    parser.add_argument("--llm-provider", default="openai", choices=["auto", "openai"])
    parser.add_argument("--llm-model", default="auto")
    parser.add_argument("--yfinance-model", default=None)
    parser.add_argument(
        "--llm-usage-manifest",
        default=None,
        type=Path,
        help="Optional central JSONL path for this execution's transport-attempt telemetry.",
    )
    parser.add_argument("--llm-run-id", default="", help="Telemetry run ID. Defaults to this company run key.")
    parser.add_argument(
        "--llm-run-role",
        default="target",
        choices=["target", "peer", "final", "evaluation"],
        help="Logical role used in full-pipeline usage summaries.",
    )
    parser.add_argument("--llm-execution-id", default="", help="Full-pipeline execution ID for telemetry grouping.")
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    args.env_file = Path(args.env_file).expanduser().resolve()
    args.news_config = Path(args.news_config).expanduser().resolve()
    if args.llm_usage_manifest is not None:
        args.llm_usage_manifest = args.llm_usage_manifest.expanduser().resolve()
    orchestrator = AgentTeamOrchestrator(args)
    return orchestrator.run()


if __name__ == "__main__":
    raise SystemExit(main())
