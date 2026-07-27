"""CLI entrypoint for the existing-data YFinance analyst report."""

from __future__ import annotations

import argparse
import logging
import os
from pathlib import Path

from reporting import (
    PROJECT_ROOT,
    DEFAULT_REPORT_JSON,
    DEFAULT_REPORT_MD,
    generate_analyst_report,
)

DEFAULT_ENV_PATH = PROJECT_ROOT / "configs" / ".env"
if not DEFAULT_ENV_PATH.exists():
    DEFAULT_ENV_PATH = PROJECT_ROOT / ".env"
if not DEFAULT_ENV_PATH.exists():
    DEFAULT_ENV_PATH = Path("/home/agent2/SY/.env")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Generate an analyst report from existing YFinance, News, and DART JSON files."
    )
    parser.add_argument(
        "--market-json",
        required=True,
        help="Primary YFinance market_full_dataset JSON.",
    )
    parser.add_argument(
        "--dart-json",
        required=True,
        help="Supporting DART lightweight JSON.",
    )
    parser.add_argument(
        "--news-json",
        required=True,
        help="Supporting News period summaries JSON.",
    )
    parser.add_argument(
        "--valuation-json",
        default=None,
        help="Point-in-time YFinance valuation snapshot JSON.",
    )
    parser.add_argument(
        "--report-md",
        default=str(DEFAULT_REPORT_MD),
        help="Markdown report output path.",
    )
    parser.add_argument(
        "--report-json",
        default=str(DEFAULT_REPORT_JSON),
        help="Structured JSON report output path.",
    )
    parser.add_argument("--company-name", required=True, help="Company name displayed in the report.")
    parser.add_argument("--ticker", required=True, help="Ticker displayed in the report.")
    parser.add_argument(
        "--model",
        default=None,
        help="OpenAI model for LLM report generation. Defaults to OPENAI_MODEL or gpt-5.4-mini.",
    )
    parser.add_argument(
        "--env-file",
        default=str(DEFAULT_ENV_PATH),
        help="Env file to load before calling OpenAI.",
    )
    parser.add_argument(
        "--primary-data-only",
        action="store_true",
        help="Use YFinance market/provider data only and omit DART/News subdata.",
    )
    parser.add_argument("--log-level", default="INFO", help="Python logging level.")
    args = parser.parse_args()

    load_env_file(Path(args.env_file).expanduser().resolve())
    logging.basicConfig(level=getattr(logging, args.log_level.upper(), logging.INFO), format="%(levelname)s %(message)s")
    logger = logging.getLogger("yfinance_report")

    paths = generate_analyst_report(
        market_json=Path(args.market_json).expanduser().resolve(),
        dart_json=Path(args.dart_json).expanduser().resolve(),
        news_json=Path(args.news_json).expanduser().resolve(),
        valuation_json=Path(args.valuation_json).expanduser().resolve() if args.valuation_json else None,
        report_md=Path(args.report_md).expanduser().resolve(),
        report_json=Path(args.report_json).expanduser().resolve(),
        company_name=args.company_name,
        ticker=args.ticker,
        model=args.model,
        primary_data_only=args.primary_data_only,
    )
    for name, path in paths.__dict__.items():
        logger.info("Wrote %s: %s", name, path)


def load_env_file(path: Path) -> None:
    """Load KEY=VALUE pairs without overriding already-exported variables."""

    if not path.exists():
        return
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        if not key or key in os.environ:
            continue
        os.environ[key] = _strip_env_value(value.strip())


def _strip_env_value(value: str) -> str:
    if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
        return value[1:-1]
    return value


if __name__ == "__main__":
    main()
