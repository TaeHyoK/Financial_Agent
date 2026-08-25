"""One-command company-to-report orchestration."""

from __future__ import annotations

import argparse
import hashlib
import json
import logging
import os
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from Agent_Team.Competitor_Agent.peer_resolver import resolve_naver_peer
from Agent_Team.Strategy_Agent.agent import (
    DECISION_HORIZON_PROFILES,
    DEFAULT_DECISION_HORIZON_PROFILE,
    resolve_decision_horizon_profile,
)

from .ablation import AblationConfig, config_from_args
from .company_resolver import (
    CompanyIdentity,
    CompanyResolutionError,
    build_resolved_company_config,
    fetch_dart_company_directory,
    resolve_company_identity,
    resolve_company_identity_by_stock_code,
)
from .config import (
    DEFAULT_ENV_FILE,
    DEFAULT_NEWS_CONFIG_PATH,
    OUTPUT_ROOT,
    PROJECT_ROOT,
    build_run_key,
    load_project_env,
    normalize_date,
)
from .usage_summary import summarize_execution_usage


logger = logging.getLogger(__name__)

FINAL_STAGE_NAMES = frozenset({"peer_comparison_analysis", "strategy", "writer"})
DEFAULT_FINAL_STAGE_TIMEOUT_SECONDS = 900
STAGE_LOG_TAIL_CHARACTERS = 4000


class FullPipelineError(RuntimeError):
    """Raised when a required full-pipeline stage fails."""


def _positive_int(value: str) -> int:
    parsed = int(value)
    if parsed < 1:
        raise argparse.ArgumentTypeError("value must be at least 1")
    return parsed


@dataclass(frozen=True)
class FullPipelinePaths:
    """Target-scoped paths owned by the full orchestration layer."""

    output_root: Path
    run_key: str
    execution_id: str

    @property
    def run_dir(self) -> Path:
        return self.output_root / "runs" / self.run_key

    @property
    def resolved_inputs_dir(self) -> Path:
        return self.run_dir / "resolved_inputs"

    @property
    def target_config(self) -> Path:
        return self.resolved_inputs_dir / "target_company.json"

    @property
    def peer_config(self) -> Path:
        return self.resolved_inputs_dir / "peer_company.json"

    @property
    def news_config(self) -> Path:
        return self.resolved_inputs_dir / "news_config.yaml"

    @property
    def ablation_config(self) -> Path:
        return self.resolved_inputs_dir / "ablation_config.json"

    @property
    def identity_resolution(self) -> Path:
        return self.resolved_inputs_dir / "identity_resolution.json"

    @property
    def competitor_dir(self) -> Path:
        return self.output_root / "Competitor" / self.run_key

    @property
    def peer_resolution(self) -> Path:
        return self.competitor_dir / "peer_resolution.json"

    @property
    def peer_comparison(self) -> Path:
        return self.competitor_dir / "peer_comparison_dataset.json"

    @property
    def peer_analysis(self) -> Path:
        return self.competitor_dir / "peer_comparison_report.json"

    @property
    def strategy_dir(self) -> Path:
        return self.output_root / "Strategy" / self.run_key

    @property
    def writer_dir(self) -> Path:
        return self.output_root / "Writer" / self.run_key

    @property
    def execution_dir(self) -> Path:
        return self.run_dir / "executions" / self.execution_id

    @property
    def usage_manifest(self) -> Path:
        return self.execution_dir / "llm_usage_manifest.jsonl"

    @property
    def usage_summary(self) -> Path:
        return self.execution_dir / "llm_usage_summary.json"

    @property
    def latest_usage_summary(self) -> Path:
        return self.run_dir / "llm_usage_summary.json"

    @property
    def full_manifest(self) -> Path:
        return self.execution_dir / "full_pipeline_manifest.json"

    @property
    def latest_full_manifest(self) -> Path:
        return self.run_dir / "full_pipeline_manifest.json"

    def ensure_directories(self) -> None:
        for path in (
            self.resolved_inputs_dir,
            self.competitor_dir,
            self.strategy_dir,
            self.writer_dir,
            self.execution_dir,
        ):
            path.mkdir(parents=True, exist_ok=True)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Resolve a listed company and run target, peer, Strategy, and Writer stages end to end."
    )
    parser.add_argument("--company-name", required=True, help="Exact listed company name, e.g. SK바이오팜.")
    parser.add_argument("--selected-date", required=True, help="YYYYMMDD report date, interpreted before market open.")
    parser.add_argument("--news-window", default="1m", choices=["2w", "1m", "3m"])
    parser.add_argument(
        "--target-news-query",
        default="",
        help=(
            "Optional Google News query override for the target company only. "
            "Peer collection continues to use the resolved peer company name."
        ),
    )
    parser.add_argument(
        "--news-event-top-k",
        "--news-total-max-results",
        dest="news_total_max_results",
        type=_positive_int,
        default=None,
        help=(
            "Maximum same-day-deduplicated News events retained after article "
            "reranking. --news-total-max-results is a compatibility alias."
        ),
    )
    parser.add_argument(
        "--decision-horizon-profile",
        default=DEFAULT_DECISION_HORIZON_PROFILE,
        choices=list(DECISION_HORIZON_PROFILES),
        help=(
            "Strategy decision horizon: default, unspecified, short_term (1 month), "
            "medium_term (3 months), or long_term (6 months). Independent of --news-window."
        ),
    )
    parser.add_argument("--peer-stock-code", default="", help="Optional six-digit peer override when Naver is unavailable.")
    parser.add_argument(
        "--peer-resolution-from",
        type=Path,
        default=None,
        help=(
            "Reuse a prior automatic peer_resolution.json for paired replicates. "
            "The original FG000 selection method and provenance are preserved."
        ),
    )
    parser.add_argument("--peer-timeout", type=int, default=20)
    parser.add_argument("--output-root", type=Path, default=OUTPUT_ROOT)
    parser.add_argument(
        "--reuse-domain-data-from",
        type=Path,
        default=None,
        metavar="OUTPUT_ROOT",
        help=(
            "Reuse DART, market, and News collection/summary artifacts from a completed output root, "
            "while rerunning downstream domain analysis, Strategy, and Writer."
        ),
    )
    parser.add_argument("--env-file", type=Path, default=DEFAULT_ENV_FILE)
    parser.add_argument("--llm-model", default="gpt-5.4-mini")
    parser.add_argument("--llm-timeout", type=int, default=300)
    parser.add_argument("--max-retries", type=int, default=1)
    parser.add_argument(
        "--final-stage-timeout",
        type=_positive_int,
        default=DEFAULT_FINAL_STAGE_TIMEOUT_SECONDS,
        metavar="SECONDS",
        help="Wall-clock timeout applied independently to Strategy and Writer only.",
    )
    parser.add_argument("--execution-id", default="", help="Optional execution ID; generated automatically by default.")
    parser.add_argument(
        "--no-sy",
        action="store_true",
        help="Bypass all domain SY agents and expose the unverified domain-agent outputs through a schema adapter.",
    )
    parser.add_argument(
        "--exclude-domain",
        action="append",
        default=[],
        metavar="DOMAIN",
        help="Omit one source domain from Strategy evidence: financial/dart, news, or yfinance. Repeatable.",
    )
    parser.add_argument(
        "--only-domain",
        default="",
        metavar="DOMAIN",
        help="Expose only one source domain to Strategy: financial/dart, news, or yfinance.",
    )
    parser.add_argument(
        "--primary-data-only",
        action="store_true",
        help="Run each domain agent with its own primary data and remove cross-domain secondary/subdata.",
    )
    parser.add_argument(
        "--no-competitor",
        action="store_true",
        help="Disable peer resolution, the peer company pipeline, and all Strategy peer cards.",
    )
    parser.add_argument(
        "--full-context",
        action="store_true",
        help=(
            "Ablate the compact-only Strategy input by exposing the sanitized full domain reports "
            "alongside the required semantic cards."
        ),
    )
    parser.add_argument(
        "--free-form-writer",
        action="store_true",
        help=(
            "Ablate deterministic thesis/evidence/risk assembly and let Writer author those visible "
            "components under the same grounding contract."
        ),
    )
    parser.add_argument(
        "--experiment-name",
        default="",
        help="Stable output label. Active ablations default to Output_total/ablations/<label>.",
    )
    parser.add_argument("--dry-run", action="store_true", help="Resolve inputs and write commands without running agents.")
    parser.add_argument("--no-progress", action="store_true", help="Disable lower-level progress bars.")
    parser.add_argument("--log-level", default="INFO")
    return parser


def run_full_pipeline(args: argparse.Namespace) -> dict[str, Any]:
    """Resolve runtime identities and execute the complete reporting pipeline."""

    ablation = config_from_args(args)
    selected_date = normalize_date(args.selected_date)
    output_root = Path(args.output_root).expanduser().resolve()
    if ablation.active and output_root == OUTPUT_ROOT.resolve():
        output_root = OUTPUT_ROOT / "ablations" / ablation.experiment_name
    env_file = Path(args.env_file).expanduser().resolve()
    execution_id = args.execution_id.strip() or _new_execution_id()
    provisional_run_key = build_run_key(args.company_name, selected_date)
    paths = FullPipelinePaths(output_root, provisional_run_key, execution_id)
    paths.ensure_directories()
    steps: list[dict[str, Any]] = []
    manifest = _base_manifest(
        args=args,
        ablation=ablation,
        paths=paths,
        selected_date=selected_date,
        status="resolving_identities",
        steps=steps,
    )
    _write_full_manifest(paths, manifest)

    try:
        env_status = load_project_env(env_file)
        dart_api_key = os.getenv("DART_API_KEY", "").strip()
        if not dart_api_key:
            raise CompanyResolutionError(
                f"DART_API_KEY is required; checked environment and {env_status['env_file']}."
            )
        directory = fetch_dart_company_directory(dart_api_key)
        target = resolve_company_identity(
            args.company_name,
            selected_date=selected_date,
            directory=directory,
        )
        canonical_run_key = build_run_key(target.company_name, selected_date)
        if canonical_run_key != paths.run_key:
            paths = FullPipelinePaths(output_root, canonical_run_key, execution_id)
            paths.ensure_directories()
        peer: CompanyIdentity | None = None
        if ablation.include_competitor:
            if args.peer_resolution_from is not None:
                peer_resolution = _load_peer_resolution_snapshot(
                    args.peer_resolution_from,
                    target=target,
                )
            else:
                peer_resolution = _resolve_peer_selection(
                    target,
                    peer_stock_code=args.peer_stock_code,
                    timeout=args.peer_timeout,
                )
            peer_stock_code = str((peer_resolution.get("selected_peer") or {}).get("stock_code") or "")
            if not peer_stock_code:
                _write_json(paths.peer_resolution, peer_resolution)
                manifest = _base_manifest(
                    args=args,
                    ablation=ablation,
                    paths=paths,
                    selected_date=selected_date,
                    status="resolving_identities",
                    steps=steps,
                    target=target,
                )
                manifest["peer_resolution_failure"] = {
                    "status": peer_resolution.get("status"),
                    "reason": peer_resolution.get("reason"),
                    "artifact": str(paths.peer_resolution),
                }
                _write_full_manifest(paths, manifest)
                reason = peer_resolution.get("reason") or "selected peer stock code is missing"
                raise CompanyResolutionError(f"Naver peer resolution failed: {reason}.")
            peer = resolve_company_identity_by_stock_code(
                peer_stock_code,
                selected_date=selected_date,
                directory=directory,
            )
            if peer.stock_code == target.stock_code:
                raise CompanyResolutionError("Resolved peer must differ from the target company.")
        else:
            peer_resolution = {
                "status": "disabled",
                "reason": "competitor_ablation",
                "target": {"stock_code": target.stock_code, "company_name": target.company_name},
                "usage_policy": {"purpose": "ablation", "point_in_time_financial_evidence": False},
            }

        peer_run_key = build_run_key(peer.company_name, selected_date) if peer else ""
        _write_resolved_inputs(
            args=args,
            ablation=ablation,
            paths=paths,
            selected_date=selected_date,
            target=target,
            peer=peer,
            peer_resolution=peer_resolution,
        )

        manifest = _base_manifest(
            args=args,
            ablation=ablation,
            paths=paths,
            selected_date=selected_date,
            status="dry_run" if args.dry_run else "running",
            steps=steps,
            target=target,
            peer=peer,
            peer_run_key=peer_run_key,
        )
        _write_full_manifest(paths, manifest)

        common_env = _subprocess_env(
            paths=paths,
            run_id=paths.run_key,
            run_role="final",
            company_name=target.company_name,
            llm_model=args.llm_model,
            llm_timeout=args.llm_timeout,
            transport_retries=args.max_retries,
        )
        stage_commands = [
            (
                "target_domain_pipeline",
                build_domain_pipeline_command(
                    config_path=paths.target_config,
                    paths=paths,
                    run_id=paths.run_key,
                    run_role="target",
                    args=args,
                    ablation=ablation,
                    env_file=env_file,
                ),
                _subprocess_env(
                    paths=paths,
                    run_id=paths.run_key,
                    run_role="target",
                    company_name=target.company_name,
                    llm_model=args.llm_model,
                    llm_timeout=args.llm_timeout,
                    transport_retries=args.max_retries,
                ),
            ),
        ]
        if peer is not None:
            stage_commands.extend(
                [
                    (
                        "peer_domain_pipeline",
                        build_domain_pipeline_command(
                            config_path=paths.peer_config,
                            paths=paths,
                            run_id=peer_run_key,
                            run_role="peer",
                            args=args,
                            ablation=ablation,
                            env_file=env_file,
                        ),
                        _subprocess_env(
                            paths=paths,
                            run_id=peer_run_key,
                            run_role="peer",
                            company_name=peer.company_name,
                            llm_model=args.llm_model,
                            llm_timeout=args.llm_timeout,
                            transport_retries=args.max_retries,
                        ),
                    ),
                    (
                        "peer_comparison_dataset",
                        build_peer_comparison_command(
                            paths=paths,
                            peer_run_key=peer_run_key,
                            selected_date=selected_date,
                            target=target,
                        ),
                        common_env,
                    ),
                    (
                        "peer_comparison_analysis",
                        build_peer_analysis_command(
                            paths=paths,
                            peer_run_key=peer_run_key,
                            target=target,
                            peer=peer,
                            args=args,
                            ablation=ablation,
                            env_file=env_file,
                        ),
                        common_env,
                    ),
                ]
            )
        stage_commands.extend(
            [
                (
                    "strategy",
                    build_strategy_command(
                        paths=paths,
                        selected_date=selected_date,
                        target=target,
                        args=args,
                        ablation=ablation,
                        env_file=env_file,
                    ),
                    common_env,
                ),
                (
                    "writer",
                    build_writer_command(
                        paths=paths,
                        args=args,
                        ablation=ablation,
                        env_file=env_file,
                    ),
                    common_env,
                ),
            ]
        )

        for name, command, command_env in stage_commands:
            timeout_seconds = args.final_stage_timeout if name in FINAL_STAGE_NAMES else None
            log_path = paths.execution_dir / "stage_logs" / f"{name}.log"
            record = _new_stage_record(
                name=name,
                command=command,
                dry_run=args.dry_run,
                log_path=log_path,
                timeout_seconds=timeout_seconds,
            )
            steps.append(record)
            manifest["steps"] = steps
            _write_full_manifest(paths, manifest)
            record = _execute_stage(
                name=name,
                command=command,
                env=command_env,
                dry_run=args.dry_run,
                log_path=log_path,
                timeout_seconds=timeout_seconds,
                record=record,
            )
            steps[-1] = record
            manifest["steps"] = steps
            _write_full_manifest(paths, manifest)
            if record["status"] == "failed":
                raise FullPipelineError(f"{name} failed with return code {record['returncode']}.")

        if args.dry_run:
            usage = summarize_execution_usage(
                paths.usage_manifest,
                execution_id=paths.execution_id,
                pipeline_completed=False,
                expected_logical_calls_by_role=_expected_calls(
                    ablation,
                    reused_domain_snapshot=bool(args.reuse_domain_data_from),
                ),
            )
            _write_usage_summaries(paths, usage)
            manifest["llm_usage"] = usage
            manifest["status"] = "dry_run"
            manifest["completed_at"] = _utc_now()
            _write_full_manifest(paths, manifest)
            return manifest

        validation = validate_full_pipeline_outputs(
            paths=paths,
            target_run_key=paths.run_key,
            peer_run_key=peer_run_key,
            include_competitor=ablation.include_competitor,
        )
        usage = summarize_execution_usage(
            paths.usage_manifest,
            execution_id=paths.execution_id,
            pipeline_completed=True,
            expected_logical_calls_by_role=_expected_calls(
                ablation,
                reused_domain_snapshot=bool(args.reuse_domain_data_from),
            ),
        )
        _write_usage_summaries(paths, usage)
        manifest["validation"] = validation
        manifest["llm_usage"] = usage
        manifest["status"] = "success"
        manifest["completed_at"] = _utc_now()
        _write_full_manifest(paths, manifest)
        return manifest
    except Exception as exc:
        usage = summarize_execution_usage(
            paths.usage_manifest,
            execution_id=paths.execution_id,
            pipeline_completed=False,
            expected_logical_calls_by_role=_expected_calls(
                ablation,
                reused_domain_snapshot=bool(args.reuse_domain_data_from),
            ),
        )
        _write_usage_summaries(paths, usage)
        manifest["status"] = "failed"
        manifest["completed_at"] = _utc_now()
        manifest["error"] = {"type": type(exc).__name__, "message": str(exc)}
        manifest["steps"] = steps
        manifest["llm_usage"] = usage
        _write_full_manifest(paths, manifest)
        raise


def build_domain_pipeline_command(
    *,
    config_path: Path,
    paths: FullPipelinePaths,
    run_id: str,
    run_role: str,
    args: argparse.Namespace,
    ablation: AblationConfig,
    env_file: Path,
) -> list[str]:
    command = [
        sys.executable,
        "-m",
        "orchestration.end_to_end_loop",
        "--config",
        str(config_path),
        "--env-file",
        str(env_file),
        "--output-root",
        str(paths.output_root),
        "--use-llm",
        "--llm-model",
        args.llm_model,
        "--timeout-seconds",
        str(args.llm_timeout),
        "--llm-usage-manifest",
        str(paths.usage_manifest),
        "--llm-run-id",
        run_id,
        "--llm-run-role",
        run_role,
        "--llm-execution-id",
        paths.execution_id,
        "--news-config",
        str(paths.news_config),
    ]
    if not ablation.use_sy:
        command.append("--no-sy")
    if ablation.primary_data_only:
        command.append("--primary-data-only")
    if args.reuse_domain_data_from:
        command.extend(
            [
                "--reuse-domain-data-from",
                str(Path(args.reuse_domain_data_from).expanduser().resolve()),
            ]
        )
    if args.news_total_max_results is not None:
        command.extend(
            ["--news-event-top-k", str(args.news_total_max_results)]
        )
    if run_role == "target" and args.target_news_query:
        command.extend(["--news-query", args.target_news_query])
    if args.no_progress:
        command.append("--no-progress")
    if args.dry_run:
        command.append("--dry-run")
    return command


def build_peer_comparison_command(
    *,
    paths: FullPipelinePaths,
    peer_run_key: str,
    selected_date: str,
    target: CompanyIdentity,
) -> list[str]:
    return [
        sys.executable,
        "-m",
        "Agent_Team.Competitor_Agent.peer_comparison_cli",
        "--target-config",
        str(paths.target_config),
        "--run-key",
        paths.run_key,
        "--company-name",
        target.company_name,
        "--selected-date",
        selected_date,
        "--peer-run-key",
        peer_run_key,
        "--output-root",
        str(paths.output_root),
        "--output-dir",
        str(paths.competitor_dir),
    ]


def build_peer_analysis_command(
    *,
    paths: FullPipelinePaths,
    peer_run_key: str,
    target: CompanyIdentity,
    peer: CompanyIdentity,
    args: argparse.Namespace,
    ablation: AblationConfig,
    env_file: Path,
) -> list[str]:
    command = [
        sys.executable,
        "-m",
        "Agent_Team.Competitor_Agent.comparison_agent_cli",
        "--target-company-name",
        target.company_name,
        "--peer-company-name",
        peer.company_name,
        "--target-financial",
        str(paths.output_root / "Financial" / paths.run_key / "final_report.json"),
        "--target-news",
        str(paths.output_root / "News" / paths.run_key / "final_report.json"),
        "--target-yfinance",
        str(paths.output_root / "Y_Finance" / paths.run_key / "final_report.json"),
        "--peer-financial",
        str(paths.output_root / "Financial" / peer_run_key / "final_report.json"),
        "--peer-news",
        str(paths.output_root / "News" / peer_run_key / "final_report.json"),
        "--peer-yfinance",
        str(paths.output_root / "Y_Finance" / peer_run_key / "final_report.json"),
        "--pairwise-dataset",
        str(paths.peer_comparison),
        "--output-dir",
        str(paths.competitor_dir),
        "--llm-model",
        args.llm_model,
        "--llm-timeout",
        str(args.llm_timeout),
        "--env-file",
        str(env_file),
    ]
    for domain in ablation.included_domains:
        command.extend(["--include-domain", domain])
    return command


def build_strategy_command(
    *,
    paths: FullPipelinePaths,
    selected_date: str,
    target: CompanyIdentity,
    args: argparse.Namespace,
    ablation: AblationConfig,
    env_file: Path,
) -> list[str]:
    del selected_date
    command = [
        sys.executable,
        "-m",
        "Agent_Team.Strategy_Agent.cli",
        "--target-company-name",
        target.company_name,
        "--target-run-key",
        paths.run_key,
        "--target-financial",
        str(paths.output_root / "Financial" / paths.run_key / "final_report.json"),
        "--target-news",
        str(paths.output_root / "News" / paths.run_key / "final_report.json"),
        "--target-yfinance",
        str(paths.output_root / "Y_Finance" / paths.run_key / "final_report.json"),
        "--output-dir",
        str(paths.strategy_dir),
        "--packet-version",
        "v4",
        "--llm-model",
        args.llm_model,
        "--llm-timeout",
        str(args.llm_timeout),
        "--decision-horizon-profile",
        args.decision_horizon_profile,
        "--env-file",
        str(env_file),
    ]
    if ablation.include_competitor:
        command.extend(["--peer-comparison", str(paths.peer_comparison)])
        command.extend(["--peer-analysis", str(paths.peer_analysis)])
    for domain in ablation.included_domains:
        command.extend(["--include-domain", domain])
    if not ablation.use_sy:
        command.append("--no-sy")
    if ablation.primary_data_only:
        command.append("--primary-data-only")
    if not ablation.include_competitor:
        command.append("--no-competitor")
    if ablation.strategy_context_mode == "full_reports":
        command.append("--full-context")
    command.extend(["--experiment-name", ablation.experiment_name])
    return command


def build_writer_command(
    *,
    paths: FullPipelinePaths,
    args: argparse.Namespace,
    ablation: AblationConfig,
    env_file: Path,
) -> list[str]:
    command = [
        sys.executable,
        str(PROJECT_ROOT / "src" / "Agent_Team" / "Writer Agent" / "writer_agent.py"),
        "--run-key",
        paths.run_key,
        "--strategy-packet",
        str(paths.strategy_dir / "strategy_compact_packet_v2.json"),
        "--strategy-provenance",
        str(paths.strategy_dir / "strategy_packet_provenance_v2.json"),
        "--strategy-decision",
        str(paths.strategy_dir / "strategy_decision_output_v4.json"),
        "--output-dir",
        str(paths.writer_dir),
        "--env-file",
        str(env_file),
        "--llm-model",
        args.llm_model,
    ]
    chart_dir = paths.output_root / "Y_Finance" / paths.run_key / "charts"
    for chart_path in (
        chart_dir / "full_period_technical.png",
        chart_dir / "full_period_kospi_fx.png",
        chart_dir / f"summary_{paths.run_key.rsplit('_', 1)[-1]}.png",
    ):
        command.extend(["--market-chart", str(chart_path)])
    if ablation.writer_mode == "free_form":
        command.append("--free-form")
    return command


def validate_full_pipeline_outputs(
    *,
    paths: FullPipelinePaths,
    target_run_key: str,
    peer_run_key: str,
    include_competitor: bool = True,
) -> dict[str, Any]:
    target_manifest = _load_json(paths.output_root / "runs" / target_run_key / "run_manifest.json")
    peer_manifest = (
        _load_json(paths.output_root / "runs" / peer_run_key / "run_manifest.json")
        if include_competitor
        else {}
    )
    strategy_path = paths.strategy_dir / "strategy_decision_output_v4.json"
    strategy = _load_json(strategy_path)
    writer_status = _load_json(paths.writer_dir / "writer_run_status.json")
    report_path = paths.writer_dir / "report.html"
    checks = {
        "target_domain_pipeline": target_manifest.get("status") == "success",
        "strategy_output": strategy_path.is_file() and bool(strategy),
        "label_free_strategy_contract": strategy.get("decision_version") == "strategy_decision_output_v4",
        "writer_run": writer_status.get("status") == "success",
        "writer_html": report_path.is_file() and report_path.stat().st_size > 0,
    }
    if include_competitor:
        checks["peer_domain_pipeline"] = peer_manifest.get("status") == "success"
        checks["peer_comparison_dataset"] = paths.peer_comparison.exists()
        checks["peer_comparison_analysis"] = paths.peer_analysis.exists()
    failed = [name for name, passed in checks.items() if not passed]
    if failed:
        raise FullPipelineError(f"Full-pipeline output validation failed: {', '.join(failed)}")
    return {
        "status": "pass",
        "checks": checks,
        "strategy_output": str(strategy_path),
        "writer_report": str(report_path),
    }


def _resolve_peer_selection(
    target: CompanyIdentity,
    *,
    peer_stock_code: str,
    timeout: int,
) -> dict[str, Any]:
    override = "".join(character for character in str(peer_stock_code or "") if character.isdigit())
    if override:
        return {
            "status": "selected",
            "target": {"stock_code": target.stock_code, "company_name": target.company_name},
            "candidates": [],
            "selected_peer": {"stock_code": override.zfill(6)},
            "selection_basis": {"method": "explicit_peer_stock_code_override"},
            "source": {"provider": "command_line_override"},
            "usage_policy": {
                "purpose": "peer_identity_selection_only",
                "point_in_time_financial_evidence": False,
            },
        }
    return resolve_naver_peer(target.stock_code, timeout=max(1, int(timeout)))


def _load_peer_resolution_snapshot(
    path: Path,
    *,
    target: CompanyIdentity,
) -> dict[str, Any]:
    snapshot_path = Path(path).expanduser().resolve()
    payload = _load_json(snapshot_path)
    if payload.get("status") != "selected":
        raise CompanyResolutionError(
            f"Frozen peer resolution is not successful: {snapshot_path}"
        )
    target_code = str((payload.get("target") or {}).get("stock_code") or "")
    selected_code = str((payload.get("selected_peer") or {}).get("stock_code") or "")
    if target_code != target.stock_code:
        raise CompanyResolutionError(
            "Frozen peer resolution target does not match the requested company."
        )
    if not selected_code or selected_code == target.stock_code:
        raise CompanyResolutionError("Frozen peer resolution has no valid distinct peer.")

    frozen = json.loads(json.dumps(payload, ensure_ascii=False))
    frozen["paired_experiment_freeze"] = {
        "status": "reused",
        "source_artifact": str(snapshot_path),
        "source_sha256": hashlib.sha256(snapshot_path.read_bytes()).hexdigest(),
        "policy": "automatic FG000 selection executed once, then frozen across paired replicates",
    }
    return frozen


def _write_resolved_inputs(
    *,
    args: argparse.Namespace,
    ablation: AblationConfig,
    paths: FullPipelinePaths,
    selected_date: str,
    target: CompanyIdentity,
    peer: CompanyIdentity | None,
    peer_resolution: dict[str, Any],
) -> tuple[dict[str, Any], dict[str, Any] | None]:
    target_config = build_resolved_company_config(
        target,
        selected_date=selected_date,
        news_window=args.news_window,
        llm_model=args.llm_model,
        max_retries=args.max_retries,
    )
    peer_config = None
    if peer is not None:
        peer_config = build_resolved_company_config(
            peer,
            selected_date=selected_date,
            news_window=args.news_window,
            llm_model=args.llm_model,
            max_retries=args.max_retries,
        )
        target_config["peer_selection"] = {
            "company_name": peer.company_name,
            "stock_code": peer.stock_code,
            "ticker": peer.ticker,
            "provider": (peer_resolution.get("source") or {}).get("provider"),
            "selection_method": (peer_resolution.get("selection_basis") or {}).get("method"),
            "paired_experiment_frozen": bool(peer_resolution.get("paired_experiment_freeze")),
            "usage": "identity_only",
        }
        peer_config["comparison_target"] = {
            "company_name": target.company_name,
            "stock_code": target.stock_code,
            "ticker": target.ticker,
            "usage": "identity_only",
        }
    else:
        target_config["peer_selection"] = {
            "status": "disabled",
            "reason": "competitor_ablation",
            "usage": "disabled",
        }
    identity_payload = {
        "selected_date": selected_date,
        "selected_date_policy": "before_market_open",
        "news_window": args.news_window,
        "target": target.as_dict(),
        "peer": peer.as_dict() if peer else {},
        "peer_selection": {
            "status": peer_resolution.get("status"),
            "selected_peer_stock_code": peer.stock_code if peer else "",
            "provider": (peer_resolution.get("source") or {}).get("provider"),
            "selection_method": (peer_resolution.get("selection_basis") or {}).get("method"),
            "financial_values_forwarded_to_llm": False,
        },
    }
    _write_json(paths.target_config, target_config)
    if peer_config is not None:
        _write_json(paths.peer_config, peer_config)
    _write_json(paths.identity_resolution, identity_payload)
    _write_json(paths.peer_resolution, peer_resolution)
    _write_json(paths.ablation_config, ablation.as_dict())
    _write_ablation_news_config(paths)
    return target_config, peer_config


def _write_ablation_news_config(paths: FullPipelinePaths) -> None:
    """Point News collection at the execution's isolated output root."""

    try:
        import yaml

        payload = yaml.safe_load(DEFAULT_NEWS_CONFIG_PATH.read_text(encoding="utf-8")) or {}
    except Exception as exc:
        raise FullPipelineError(f"Unable to load News config: {DEFAULT_NEWS_CONFIG_PATH}: {exc}") from exc
    payload["data_root"] = str(paths.output_root / "News" / "artifacts")
    payload["inputs_root"] = str(paths.output_root / "News" / "inputs")
    # JSON is valid YAML and keeps this generated runtime file deterministic.
    _write_json(paths.news_config, payload)


def _new_stage_record(
    *,
    name: str,
    command: list[str],
    dry_run: bool,
    log_path: Path,
    timeout_seconds: int | float | None,
) -> dict[str, Any]:
    record: dict[str, Any] = {
        "name": name,
        "command": command,
        "started_at": _utc_now(),
        "status": "planned" if dry_run else "running",
        "returncode": None,
        "log_path": str(log_path),
        "log_tail": "",
        "stdout_tail": "",
        "stderr_tail": "",
        "failure_class": None,
    }
    if timeout_seconds is not None:
        record["timeout_seconds"] = timeout_seconds
    return record


def _execute_stage(
    *,
    name: str,
    command: list[str],
    env: dict[str, str],
    dry_run: bool,
    log_path: Path,
    timeout_seconds: int | float | None = None,
    record: dict[str, Any] | None = None,
) -> dict[str, Any]:
    record = record or _new_stage_record(
        name=name,
        command=command,
        dry_run=dry_run,
        log_path=log_path,
        timeout_seconds=timeout_seconds,
    )
    if dry_run:
        record["completed_at"] = _utc_now()
        record["elapsed_seconds"] = 0.0
        return record
    logger.info("Running %s", name)
    started = time.monotonic()
    stdout = ""
    stderr = ""
    _write_stage_log(log_path, stdout=stdout, stderr=stderr)
    try:
        completed = subprocess.run(
            command,
            cwd=str(PROJECT_ROOT),
            env=env,
            check=False,
            capture_output=True,
            text=True,
            timeout=timeout_seconds,
        )
        stdout = completed.stdout or ""
        stderr = completed.stderr or ""
        record["returncode"] = completed.returncode
        record["status"] = "success" if completed.returncode == 0 else "failed"
        if completed.returncode != 0:
            record["failure_class"] = "nonzero_exit"
    except subprocess.TimeoutExpired as exc:
        stdout = _stream_text(exc.stdout)
        stderr = _stream_text(exc.stderr)
        timeout_message = f"Stage exceeded timeout of {timeout_seconds} seconds."
        stderr = f"{stderr.rstrip()}\n{timeout_message}\n" if stderr else timeout_message + "\n"
        record["returncode"] = 124
        record["status"] = "failed"
        record["failure_class"] = "timeout"
    except OSError as exc:
        stderr = f"{type(exc).__name__}: {exc}\n"
        record["returncode"] = 127
        record["status"] = "failed"
        record["failure_class"] = "launch_error"
    log_content = _write_stage_log(log_path, stdout=stdout, stderr=stderr)
    record["log_tail"] = _tail(log_content)
    record["stdout_tail"] = _tail(stdout)
    record["stderr_tail"] = _tail(stderr)
    record["completed_at"] = _utc_now()
    record["elapsed_seconds"] = round(time.monotonic() - started, 3)
    return record


def _write_stage_log(path: Path, *, stdout: str, stderr: str) -> str:
    path.parent.mkdir(parents=True, exist_ok=True)
    content = f"[stdout]\n{stdout.rstrip()}\n\n[stderr]\n{stderr.rstrip()}\n"
    path.write_text(content, encoding="utf-8")
    return content


def _stream_text(value: str | bytes | None) -> str:
    if value is None:
        return ""
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    return value


def _tail(value: str) -> str:
    return value[-STAGE_LOG_TAIL_CHARACTERS:]


def _subprocess_env(
    *,
    paths: FullPipelinePaths,
    run_id: str,
    run_role: str,
    company_name: str,
    llm_model: str,
    llm_timeout: int,
    transport_retries: int,
) -> dict[str, str]:
    env = os.environ.copy()
    src = str(PROJECT_ROOT / "src")
    current = env.get("PYTHONPATH", "")
    env["PYTHONPATH"] = src if not current else src + os.pathsep + current
    env["OPENAI_MODEL"] = llm_model
    env["LLM_TIMEOUT_SECONDS"] = str(llm_timeout)
    env["LLM_TRANSPORT_RETRIES"] = str(transport_retries)
    env["LLM_USAGE_MANIFEST"] = str(paths.usage_manifest)
    env["LLM_EXECUTION_ID"] = paths.execution_id
    env["LLM_RUN_ID"] = run_id
    env["LLM_RUN_ROLE"] = run_role
    env["LLM_COMPANY_NAME"] = company_name
    return env


def _base_manifest(
    *,
    args: argparse.Namespace,
    ablation: AblationConfig,
    paths: FullPipelinePaths,
    selected_date: str,
    status: str,
    steps: list[dict[str, Any]],
    target: CompanyIdentity | None = None,
    peer: CompanyIdentity | None = None,
    peer_run_key: str = "",
) -> dict[str, Any]:
    resolved_horizon = resolve_decision_horizon_profile(args.decision_horizon_profile)["horizon"]
    return {
        "execution_id": paths.execution_id,
        "run_key": paths.run_key,
        "status": status,
        "created_at": _utc_now(),
        "request": {
            "company_name": args.company_name,
            "selected_date": selected_date,
            "selected_date_policy": "before_market_open",
            "information_cutoff_date": _previous_calendar_date(selected_date),
            "news_window": args.news_window,
            "target_news_query": args.target_news_query,
            "news_event_top_k": args.news_total_max_results,
            # Deprecated alias retained for older experiment readers.
            "news_total_max_results": args.news_total_max_results,
            "decision_horizon_profile": args.decision_horizon_profile,
            "decision_horizon": resolved_horizon,
            "final_stage_timeout_seconds": args.final_stage_timeout,
            "llm_model": args.llm_model,
            "dry_run": bool(args.dry_run),
            "reuse_domain_data_from": (
                str(Path(args.reuse_domain_data_from).expanduser().resolve())
                if args.reuse_domain_data_from
                else ""
            ),
            "peer_resolution_from": (
                str(Path(args.peer_resolution_from).expanduser().resolve())
                if args.peer_resolution_from
                else ""
            ),
        },
        "ablation": ablation.as_dict(),
        "target": target.as_dict() if target else {},
        "peer": {**(peer.as_dict() if peer else {}), "run_key": peer_run_key} if peer else {},
        "outputs": {
            "target_config": str(paths.target_config),
            "peer_config": str(paths.peer_config),
            "ablation_config": str(paths.ablation_config),
            "news_config": str(paths.news_config),
            "peer_resolution": str(paths.peer_resolution),
            "peer_comparison": str(paths.peer_comparison),
            "peer_analysis": str(paths.peer_analysis),
            "strategy_report": str(paths.strategy_dir / "strategy_report.json"),
            "strategy_compact_packet_v2": str(paths.strategy_dir / "strategy_compact_packet_v2.json"),
            "strategy_packet_provenance_v2": str(paths.strategy_dir / "strategy_packet_provenance_v2.json"),
            "strategy_decision_output_v4": str(paths.strategy_dir / "strategy_decision_output_v4.json"),
            "writer_editorial_packet_v2": str(paths.writer_dir / "writer_editorial_packet_v2.json"),
            "writer_packet_provenance_v2": str(paths.writer_dir / "writer_packet_provenance_v2.json"),
            "writer_run_status": str(paths.writer_dir / "writer_run_status.json"),
            "writer_report": str(paths.writer_dir / "report.html"),
            "llm_usage_manifest": str(paths.usage_manifest),
            "llm_usage_summary": str(paths.usage_summary),
        },
        "steps": steps,
    }


def _expected_calls(ablation: AblationConfig, *, reused_domain_snapshot: bool = False) -> dict[str, int]:
    domain_calls = 3 if not ablation.use_sy else 6
    if reused_domain_snapshot:
        domain_calls -= 1  # News collection summary LLM output is part of the fixed snapshot.
    return {
        "target": domain_calls,
        "peer": domain_calls if ablation.include_competitor else 0,
        "final": 3 if ablation.include_competitor else 2,
    }


def _write_full_manifest(paths: FullPipelinePaths, manifest: dict[str, Any]) -> None:
    _write_json(paths.full_manifest, manifest)
    _write_json(paths.latest_full_manifest, manifest)


def _write_usage_summaries(paths: FullPipelinePaths, usage: dict[str, Any]) -> None:
    _write_json(paths.usage_summary, usage)
    _write_json(paths.latest_usage_summary, usage)


def _write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(
        prefix=f".{path.name}.",
        suffix=".tmp",
        dir=path.parent,
    )
    temporary_path = Path(temporary)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as file:
            file.write(json.dumps(payload, ensure_ascii=False, indent=2) + "\n")
            file.flush()
            os.fsync(file.fileno())
        os.replace(temporary_path, path)
    finally:
        if temporary_path.exists():
            temporary_path.unlink()


def _load_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        raise FullPipelineError(f"Required output does not exist: {path}")
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise FullPipelineError(f"Required output must be a JSON object: {path}")
    return payload


def _new_execution_id() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _previous_calendar_date(value: str) -> str:
    selected = datetime.strptime(value, "%Y%m%d")
    return (selected.date() - timedelta(days=1)).strftime("%Y%m%d")


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    logging.basicConfig(
        level=getattr(logging, args.log_level.upper(), logging.INFO),
        format="%(levelname)s %(message)s",
    )
    try:
        manifest = run_full_pipeline(args)
    except (CompanyResolutionError, FullPipelineError, OSError, ValueError, json.JSONDecodeError) as exc:
        logger.error("%s", exc)
        return 1
    logger.info("Full pipeline %s: %s", manifest["status"], manifest["outputs"]["writer_report"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
