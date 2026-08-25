"""CLI for the target-versus-selected-peer comparison analysis."""

from __future__ import annotations

import argparse
import logging
from pathlib import Path

from .comparison_agent import DEFAULT_ENV_FILE, run_comparison_agent


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Compare target and selected-peer lower-agent reports in one LLM call."
    )
    parser.add_argument("--target-company-name", required=True)
    parser.add_argument("--peer-company-name", required=True)
    parser.add_argument("--target-financial", required=True)
    parser.add_argument("--target-news", required=True)
    parser.add_argument("--target-yfinance", required=True)
    parser.add_argument("--peer-financial", required=True)
    parser.add_argument("--peer-news", required=True)
    parser.add_argument("--peer-yfinance", required=True)
    parser.add_argument("--pairwise-dataset", required=True)
    parser.add_argument("--output-dir", required=True)
    parser.add_argument("--llm-model", default="auto")
    parser.add_argument("--llm-timeout", type=int, default=120)
    parser.add_argument("--env-file", default=str(DEFAULT_ENV_FILE))
    parser.add_argument(
        "--include-domain",
        action="append",
        choices=["financial", "news", "yfinance"],
        default=[],
    )
    parser.add_argument("--log-level", default="INFO")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    logging.basicConfig(
        level=getattr(logging, args.log_level.upper(), logging.INFO),
        format="%(levelname)s %(message)s",
    )
    paths = run_comparison_agent(
        target_company_name=args.target_company_name,
        peer_company_name=args.peer_company_name,
        target_financial_path=Path(args.target_financial),
        target_news_path=Path(args.target_news),
        target_yfinance_path=Path(args.target_yfinance),
        peer_financial_path=Path(args.peer_financial),
        peer_news_path=Path(args.peer_news),
        peer_yfinance_path=Path(args.peer_yfinance),
        pairwise_dataset_path=Path(args.pairwise_dataset),
        output_dir=Path(args.output_dir),
        llm_model=args.llm_model,
        llm_timeout=args.llm_timeout,
        env_file=Path(args.env_file) if args.env_file else None,
        included_domains=tuple(args.include_domain or ("financial", "news", "yfinance")),
    )
    logging.getLogger("peer_comparison_analysis").info(
        "Wrote peer comparison analysis: %s", paths.report_json
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
