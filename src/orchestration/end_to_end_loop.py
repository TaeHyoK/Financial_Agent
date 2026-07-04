"""Subprocess-based Layer 1 to Layer 2 Agent_Team orchestration."""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

from tqdm.auto import tqdm

from .config import DEFAULT_CONFIG_PATH, DEFAULT_ENV_FILE, DEFAULT_NEWS_CONFIG_PATH, load_run_config
from .dependency_graph import STEP_SPECS
from .manifest import write_financial_runtime_manifest, write_run_config_copy, write_run_files
from .paths import RunPaths, resolve_run_paths
from .run_state import FAILED, SUCCESS, StepRecord


DEFAULT_KOSPI_TICKER = "^KS11"
DEFAULT_FX_TICKER = "KRW=X"


class AgentTeamOrchestrator:
    """Run existing team CLIs and persist a global run manifest."""

    def __init__(self, args: argparse.Namespace) -> None:
        self.args = args
        self.run_config = load_run_config(args.config)
        self.paths = resolve_run_paths(self.run_config, args.output_root)
        self.paths.ensure_directories()
        write_run_config_copy(self.paths, self.run_config)
        write_financial_runtime_manifest(self.paths, self.run_config)
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

                if spec.name in self.args.skip_step:
                    record.skip("Skipped by --skip-step.")
                    self._write_state()
                    self._finish_progress_step(record)
                    continue

                if self.args.reuse_existing and outputs_exist(record.outputs):
                    record.reuse("Reused existing outputs because --reuse-existing was set.")
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
        if step_name == "financial_sy":
            return self._financial_sy_command()
        if step_name == "yfinance_report":
            return self._yfinance_report_command()
        if step_name == "yfinance_sy":
            return self._yfinance_sy_command()
        raise KeyError(f"Unknown step: {step_name}")

    def common_llm_model(self) -> str:
        if self.args.llm_model and self.args.llm_model != "auto":
            return self.args.llm_model
        if self.run_config.llm_model:
            return self.run_config.llm_model
        return "gpt-5.4-mini"

    def outputs_for_step(self, step_name: str) -> dict[str, str]:
        if step_name == "yfinance_layer_1":
            return {
                "market_summary": str(self.paths.market_summary),
                "market_summary_dated": str(self.paths.market_summary_dated),
            }
        if step_name == "financial_layer_1":
            return {
                "dart_main": str(self.paths.dart_main),
                "dart_lightweight": str(self.paths.dart_lightweight),
            }
        if step_name == "news_collect":
            return {"report_context": str(self.paths.news_report_context)}
        if step_name == "news_export":
            return {"llm_summary_request": str(self.paths.news_context_export_month_dir / "llm_summary_request.json")}
        if step_name == "news_llm":
            return {"llm_period_summaries": str(self.paths.news_llm_period_summaries)}
        if step_name == "news_analysis":
            return {"handoff": str(self.paths.news_handoff)}
        if step_name == "news_sy":
            return {
                "sy_validations": str(self.paths.news_sy_validations),
                "verified_report": str(self.paths.news_verified_report),
            }
        if step_name == "financial_analyst":
            return {"analyst_report": str(self.paths.financial_analyst_report)}
        if step_name == "financial_sy":
            return {
                "sy_validation": str(self.paths.financial_validation),
                "verified_report": str(self.paths.financial_verified_report),
            }
        if step_name == "yfinance_report":
            return {"analyst_report": str(self.paths.yfinance_analyst_report)}
        if step_name == "yfinance_sy":
            return {
                "verified_report": str(self.paths.yfinance_verified_report),
                "strategy_verified_report": str(self.paths.yfinance_strategy_verified_report),
                "question_answer_log": str(self.paths.yfinance_question_answer_log),
            }
        return {}

    def _yfinance_layer_1_command(self) -> list[str]:
        return [
            sys.executable,
            str(self.paths.project_root / "src" / "Agent_Team" / "YFinance_Agent" / "main.py"),
            "--input",
            str(self.run_config.config_path),
            "--output-dir",
            str(self.paths.yfinance_dir),
            "--start-date",
            self.run_config.start_date,
            "--end-date",
            self.run_config.end_date,
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

    def _news_phase_command(self, phase: str) -> list[str]:
        command = [
            sys.executable,
            "-m",
            "Agent_Team.News_Agent.cli",
            "--phase",
            phase,
            "--collect-date",
            self.run_config.selected_date_iso,
            "--company-id",
            self.run_config.company_code,
            "--company-name",
            self.run_config.company_name,
            "--ticker",
            self.run_config.ticker,
            "--corp-code",
            self.run_config.corp_code,
            "--granularity",
            "month",
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
        if self.args.news_collection_days is not None:
            command.extend(["--collection-days", str(self.args.news_collection_days)])
        if self.args.news_max_results is not None:
            command.extend(["--max-results", str(self.args.news_max_results)])
        command.extend(["--llm-model", self.args.news_llm_model or self.common_llm_model()])
        command.extend(["--analysis-model", self.args.news_analysis_model or self.common_llm_model()])
        command.extend(["--sy-model", self.args.news_sy_model or self.common_llm_model()])
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
            "--env-file",
            str(self.args.env_file),
            "--llm-provider",
            self.args.llm_provider,
            "--llm-model",
            self.common_llm_model(),
        ]
        command.append("--use-llm")
        return command

    def _financial_sy_command(self) -> list[str]:
        command = [
            sys.executable,
            str(self.paths.project_root / "src" / "Agent_Team" / "Financial_Agent" / "SY_Agent" / "langgraph_flow.py"),
            "--input",
            str(self.paths.financial_analyst_report),
            "--output",
            str(self.paths.financial_validation),
            "--dart-main",
            str(self.paths.dart_main),
            "--dart-master",
            str(self.paths.dart_master),
            "--trace-output",
            str(self.paths.financial_validation_trace),
            "--verified-report-output",
            str(self.paths.financial_verified_report),
            "--env-file",
            str(self.args.env_file),
            "--llm-provider",
            self.args.llm_provider,
            "--llm-model",
            self.common_llm_model(),
        ]
        command.append("--use-llm")
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
        return command

    def _yfinance_sy_command(self) -> list[str]:
        return [
            sys.executable,
            str(self.paths.project_root / "src" / "Agent_Team" / "YFinance_Agent" / "SY_Agent" / "sy_agent.py"),
            "--input",
            str(self.paths.yfinance_analyst_report),
            "--output",
            str(self.paths.yfinance_verified_report),
            "--strategy-output",
            str(self.paths.yfinance_strategy_verified_report),
            "--question-log",
            str(self.paths.yfinance_question_answer_log),
            "--env-file",
            str(self.args.env_file),
            "--model",
            self.common_llm_model(),
        ]

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
        env["NEWS_SY_AGENT_LLM_MODEL"] = self.common_llm_model()
        return env

    def _post_step(self, step_name: str, record: StepRecord) -> None:
        if record.status != SUCCESS:
            return
        if step_name == "yfinance_layer_1":
            ensure_market_summary_alias(self.paths)
        if step_name == "financial_layer_1":
            write_financial_runtime_manifest(self.paths, self.run_config)
        if step_name == "financial_sy":
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
        return write_run_files(self.paths, self.run_config, self.steps, dry_run=self.args.dry_run)

    def _write_financial_pipeline_manifest(self) -> None:
        self.paths.financial_pipeline_manifest.write_text(
            json.dumps(
                {
                    "financial_analyst_output": str(self.paths.financial_analyst_report),
                    "financial_analyst_trace": str(self.paths.financial_analyst_trace),
                    "sy_validation_output": str(self.paths.financial_validation),
                    "sy_validation_trace": str(self.paths.financial_validation_trace),
                    "verified_financial_report_output": str(self.paths.financial_verified_report),
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


def outputs_exist(outputs: dict[str, str]) -> bool:
    return bool(outputs) and all(Path(path).exists() for path in outputs.values())


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run Agent_Team Layer 1 to Layer 2 orchestration.")
    parser.add_argument("--config", default=str(DEFAULT_CONFIG_PATH), help="Common run config JSON.")
    parser.add_argument("--env-file", default=str(DEFAULT_ENV_FILE), type=Path, help="Env file for API keys.")
    parser.add_argument("--output-root", default=None, help="Override Output_total root.")
    parser.add_argument("--use-llm", action="store_true", help="Run LLM-dependent Layer 2 phases.")
    parser.add_argument("--dry-run", action="store_true", help="Write commands/manifest without executing team CLIs.")
    parser.add_argument("--no-progress", action="store_true", help="Disable top-level tqdm progress output.")
    parser.add_argument("--reuse-existing", action="store_true", help="Mark a step successful when its expected outputs already exist.")
    parser.add_argument("--continue-on-error", action="store_true", help="Continue independent steps after a failure.")
    parser.add_argument("--skip-step", action="append", default=[], help="Skip a step by name. Can be repeated.")
    parser.add_argument("--log-level", default="INFO")
    parser.add_argument("--kospi-ticker", default=DEFAULT_KOSPI_TICKER)
    parser.add_argument("--fx-ticker", default=DEFAULT_FX_TICKER)
    parser.add_argument("--news-config", default=str(DEFAULT_NEWS_CONFIG_PATH), type=Path)
    parser.add_argument("--news-collection-days", type=int, default=None)
    parser.add_argument("--news-max-results", type=int, default=None)
    parser.add_argument("--news-min-mention-count", type=int, default=1)
    parser.add_argument("--news-llm-model", default=None)
    parser.add_argument("--news-analysis-model", default=None)
    parser.add_argument("--news-sy-model", default=None)
    parser.add_argument("--no-news-split-by-period", dest="news_split_by_period", action="store_false")
    parser.set_defaults(news_split_by_period=True)
    parser.add_argument("--timeout-seconds", type=float, default=300.0)
    parser.add_argument("--llm-provider", default="openai", choices=["auto", "openai"])
    parser.add_argument("--llm-model", default="auto")
    parser.add_argument("--yfinance-model", default=None)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    args.env_file = Path(args.env_file).expanduser().resolve()
    args.news_config = Path(args.news_config).expanduser().resolve()
    orchestrator = AgentTeamOrchestrator(args)
    return orchestrator.run()


if __name__ == "__main__":
    raise SystemExit(main())
