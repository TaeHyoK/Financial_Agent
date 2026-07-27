"""CLI entrypoint for the Strategy Agent."""

from __future__ import annotations

import argparse
import logging
import shutil
from pathlib import Path

from orchestration.ablation import AblationConfig, DOMAIN_ORDER, normalize_domain

from . import DEFAULT_TARGET_CONFIG, OUTPUT_ROOT
from .agent import (
    DECISION_HORIZON_PROFILES,
    DEFAULT_DECISION_HORIZON_PROFILE,
    DEFAULT_ENV_FILE,
    generate_strategy_report,
    run_strategy_agent,
)


def build_parser() -> argparse.ArgumentParser:
    """Build CLI parser."""

    parser = argparse.ArgumentParser(
        description="Create a Strategy Agent Buy/Hold/Sell report from target reports and a structured peer dataset."
    )
    parser.add_argument("--target-company-name", default=None, help="Target company display name.")
    parser.add_argument("--target-run-key", "--run-key", default=None, help="Target run_key, e.g. SK바이오팜_20251031.")
    parser.add_argument("--target-financial", "--financial-report", default=None, help="Target Financial final_report.json.")
    parser.add_argument("--target-news", "--news-report", default=None, help="Target News final_report.json.")
    parser.add_argument("--target-yfinance", "--yfinance-report", default=None, help="Target YFinance final_report.json.")
    parser.add_argument(
        "--peer-comparison",
        default=None,
        help="Explicit pairwise peer_comparison_dataset.json.",
    )
    parser.add_argument("--output-dir", default=None, help="Output directory for Strategy files.")
    parser.add_argument(
        "--output-root",
        default=str(OUTPUT_ROOT),
        help="Output_total root used by the compatibility/default path resolver.",
    )
    parser.add_argument(
        "--target-config",
        default=str(DEFAULT_TARGET_CONFIG),
        help="Optional target config used only when --target-run-key/--target-company-name are omitted.",
    )
    parser.add_argument("--llm-provider", default="openai", choices=["auto", "openai"])
    parser.add_argument("--llm-model", default="auto")
    parser.add_argument("--llm-timeout", type=int, default=120)
    parser.add_argument(
        "--packet-version",
        default=None,
        choices=["v1", "v2"],
        help="Strategy packet/decision contract. Defaults to STRATEGY_PACKET_VERSION or v2.",
    )
    parser.add_argument("--env-file", default=None, help="Optional .env path. Defaults to configs/.env in agent.py.")
    parser.add_argument(
        "--include-domain",
        action="append",
        default=[],
        help="Source domain exposed to Strategy: financial, news, or yfinance. Repeatable.",
    )
    parser.add_argument("--no-sy", action="store_true", help="Mark inputs as no-SY passthrough artifacts.")
    parser.add_argument("--primary-data-only", action="store_true", help="Mark inputs as primary-data-only artifacts.")
    parser.add_argument("--no-competitor", action="store_true", help="Disable peer cards even if a peer path is supplied.")
    parser.add_argument(
        "--full-context",
        action="store_true",
        help="Expose sanitized full domain reports alongside compact semantic cards.",
    )
    parser.add_argument("--experiment-name", default="baseline")
    parser.add_argument(
        "--decision-horizon-profile",
        default=DEFAULT_DECISION_HORIZON_PROFILE,
        choices=list(DECISION_HORIZON_PROFILES),
        help=(
            "Decision horizon policy: default, unspecified, short_term (1 month), "
            "medium_term (3 months), or long_term (6 months)."
        ),
    )
    parser.add_argument(
        "--semantic-attempts",
        type=int,
        default=2,
        help="Maximum fresh Strategy generations for post-Gate-A generation/finalize/Gate-B failures.",
    )
    parser.add_argument("--log-level", default="INFO")
    return parser


def main(argv: list[str] | None = None) -> int:
    """Run Strategy Agent CLI."""

    parser = build_parser()
    args = parser.parse_args(argv)
    logging.basicConfig(level=getattr(logging, args.log_level.upper(), logging.INFO), format="%(levelname)s %(message)s")
    logger = logging.getLogger("strategy_agent")
    included_set = {normalize_domain(value) for value in args.include_domain} if args.include_domain else set(DOMAIN_ORDER)
    included_domains = tuple(domain for domain in DOMAIN_ORDER if domain in included_set)
    if not included_domains:
        parser.error("At least one --include-domain value is required.")
    ablation = AblationConfig(
        included_domains=included_domains,
        use_sy=not args.no_sy,
        primary_data_only=args.primary_data_only,
        include_competitor=not args.no_competitor,
        strategy_context_mode="full_reports" if args.full_context else "compact_cards",
        experiment_name=args.experiment_name,
    )

    explicit_target_paths = args.target_financial and args.target_news and args.target_yfinance
    if explicit_target_paths:
        target_run_key = args.target_run_key or Path(args.target_financial).expanduser().parent.name
        target_company_name = args.target_company_name or target_run_key.rsplit("_", 1)[0]
        output_dir = Path(args.output_dir).expanduser().resolve() if args.output_dir else Path(args.output_root).expanduser().resolve() / "Strategy" / target_run_key
        failure_markers = (
            output_dir / "strategy_failure_report_v2.json",
            output_dir / "strategy_decision_output_v2.failed.json",
        )
        semantic_attempts = max(1, int(args.semantic_attempts))
        for semantic_attempt in range(1, semantic_attempts + 1):
            before = {
                path: path.stat().st_mtime_ns if path.exists() else None
                for path in failure_markers
            }
            try:
                report = run_strategy_agent(
                    target_company_name=target_company_name,
                    target_run_key=target_run_key,
                    target_financial_path=Path(args.target_financial).expanduser().resolve(),
                    target_news_path=Path(args.target_news).expanduser().resolve(),
                    target_yfinance_path=Path(args.target_yfinance).expanduser().resolve(),
                    output_dir=output_dir,
                    peer_comparison_path=Path(args.peer_comparison).expanduser().resolve() if args.peer_comparison else None,
                    llm_provider=args.llm_provider,
                    llm_model=args.llm_model,
                    llm_timeout=args.llm_timeout,
                    env_file=Path(args.env_file).expanduser().resolve() if args.env_file else DEFAULT_ENV_FILE,
                    packet_version=args.packet_version,
                    ablation_config=ablation.as_dict(),
                    decision_horizon_profile=args.decision_horizon_profile,
                )
                break
            except Exception:
                changed_failure = any(
                    path.exists()
                    and path.stat().st_mtime_ns != before[path]
                    for path in failure_markers
                )
                if not changed_failure or semantic_attempt >= semantic_attempts:
                    raise
                _archive_strategy_failure_attempt(output_dir, semantic_attempt)
                logger.warning(
                    "Strategy semantic attempt %s/%s failed after Gate A; generating a fresh response.",
                    semantic_attempt,
                    semantic_attempts,
                )
    else:
        report = generate_strategy_report(
            run_key=args.target_run_key,
            target_config=Path(args.target_config).expanduser().resolve() if args.target_config else None,
            output_root=Path(args.output_root).expanduser().resolve(),
            peer_comparison=Path(args.peer_comparison).expanduser().resolve() if args.peer_comparison else None,
            llm_provider=args.llm_provider,
            llm_model=args.llm_model,
            llm_timeout=args.llm_timeout,
            env_file=Path(args.env_file).expanduser().resolve() if args.env_file else DEFAULT_ENV_FILE,
            packet_version=args.packet_version,
            ablation_config=ablation.as_dict(),
            decision_horizon_profile=args.decision_horizon_profile,
        )

    logger.info(
        "Wrote Strategy report for %s (%s): %s",
        report.get("target_company_name") or report.get("target_company"),
        report.get("target_run_key"),
        report.get("final_recommendation"),
    )
    return 0


def _archive_strategy_failure_attempt(output_dir: Path, attempt: int) -> None:
    attempt_dir = output_dir / "attempts" / f"attempt_{attempt:02d}"
    attempt_dir.mkdir(parents=True, exist_ok=True)
    for filename in (
        "strategy_failure_report_v2.json",
        "strategy_decision_output_v2.failed.json",
    ):
        source = output_dir / filename
        if source.exists():
            shutil.copy2(source, attempt_dir / filename)


if __name__ == "__main__":
    raise SystemExit(main())
