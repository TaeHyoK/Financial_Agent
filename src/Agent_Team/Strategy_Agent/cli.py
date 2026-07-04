"""CLI entrypoint for the Strategy Agent."""

from __future__ import annotations

import argparse
import logging
from pathlib import Path

from . import DEFAULT_TARGET_CONFIG, OUTPUT_ROOT
from .agent import DEFAULT_ENV_FILE, generate_strategy_report, run_strategy_agent


def build_parser() -> argparse.ArgumentParser:
    """Build CLI parser."""

    parser = argparse.ArgumentParser(
        description="Create a Strategy Agent Buy/Hold/Sell report from target reports and N competitor summaries."
    )
    parser.add_argument("--target-company-name", default=None, help="Target company display name.")
    parser.add_argument("--target-run-key", "--run-key", default=None, help="Target run_key, e.g. SK바이오팜_20251031.")
    parser.add_argument("--target-financial", "--financial-report", default=None, help="Target Financial final_report.json.")
    parser.add_argument("--target-news", "--news-report", default=None, help="Target News final_report.json.")
    parser.add_argument("--target-yfinance", "--yfinance-report", default=None, help="Target YFinance final_report.json.")
    parser.add_argument(
        "--competitor-report",
        action="append",
        default=[],
        help="Competitor_Agent competitor_summary_report.json. Can be repeated N times.",
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
    parser.add_argument(
        "--auto-discover-competitors",
        action="store_true",
        help="Read all Output_total/Competitor/*/competitor_summary_report.json files except target.",
    )
    parser.add_argument("--llm-provider", default="openai", choices=["auto", "openai"])
    parser.add_argument("--llm-model", default="auto")
    parser.add_argument("--llm-timeout", type=int, default=120)
    parser.add_argument("--env-file", default=None, help="Optional .env path. Defaults to configs/.env in agent.py.")
    parser.add_argument("--log-level", default="INFO")
    return parser


def main(argv: list[str] | None = None) -> int:
    """Run Strategy Agent CLI."""

    parser = build_parser()
    args = parser.parse_args(argv)
    logging.basicConfig(level=getattr(logging, args.log_level.upper(), logging.INFO), format="%(levelname)s %(message)s")
    logger = logging.getLogger("strategy_agent")

    explicit_target_paths = args.target_financial and args.target_news and args.target_yfinance
    if explicit_target_paths:
        target_run_key = args.target_run_key or Path(args.target_financial).expanduser().parent.name
        target_company_name = args.target_company_name or target_run_key.rsplit("_", 1)[0]
        output_dir = Path(args.output_dir).expanduser().resolve() if args.output_dir else Path(args.output_root).expanduser().resolve() / "Strategy" / target_run_key
        report = run_strategy_agent(
            target_company_name=target_company_name,
            target_run_key=target_run_key,
            target_financial_path=Path(args.target_financial).expanduser().resolve(),
            target_news_path=Path(args.target_news).expanduser().resolve(),
            target_yfinance_path=Path(args.target_yfinance).expanduser().resolve(),
            competitor_report_paths=[Path(path).expanduser().resolve() for path in args.competitor_report],
            output_dir=output_dir,
            llm_provider=args.llm_provider,
            llm_model=args.llm_model,
            llm_timeout=args.llm_timeout,
            env_file=Path(args.env_file).expanduser().resolve() if args.env_file else DEFAULT_ENV_FILE,
        )
    else:
        report = generate_strategy_report(
            run_key=args.target_run_key,
            target_config=Path(args.target_config).expanduser().resolve() if args.target_config else None,
            output_root=Path(args.output_root).expanduser().resolve(),
            competitor_reports=[Path(path).expanduser().resolve() for path in args.competitor_report],
            auto_discover_competitors=args.auto_discover_competitors,
            llm_provider=args.llm_provider,
            llm_model=args.llm_model,
            llm_timeout=args.llm_timeout,
            env_file=Path(args.env_file).expanduser().resolve() if args.env_file else DEFAULT_ENV_FILE,
        )

    logger.info(
        "Wrote Strategy report for %s (%s): %s",
        report.get("target_company_name") or report.get("target_company"),
        report.get("target_run_key"),
        report.get("final_recommendation"),
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
