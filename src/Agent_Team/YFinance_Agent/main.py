"""CLI entrypoint for the YFinance market data pipeline."""

from __future__ import annotations

import argparse
import logging
from datetime import datetime
from pathlib import Path

from pipeline import DEFAULT_FX_TICKER, DEFAULT_KOSPI_TICKER, PipelineInput, load_pipeline_input, run_pipeline
from reporting import (
    DEFAULT_OUTPUT_DIR,
    DEFAULT_REPORT_JSON,
    DEFAULT_REPORT_MD,
    generate_analyst_report,
)


YFINANCE_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = YFINANCE_DIR.parents[2]
DEFAULT_INPUT_PATH = PROJECT_ROOT / "configs" / "company_input.json"
if not DEFAULT_INPUT_PATH.exists():
    DEFAULT_INPUT_PATH = PROJECT_ROOT / "test_input.json"
if not DEFAULT_INPUT_PATH.exists():
    DEFAULT_INPUT_PATH = Path("/home/agent2/SY/test_input.json")


def main() -> None:
    parser = argparse.ArgumentParser(description="Collect yfinance OHLCV, KOSPI, FX, indicators, and charts.")
    parser.add_argument(
        "--input",
        default=str(DEFAULT_INPUT_PATH),
        help="Input JSON path. Defaults to configs/company_input.json.",
    )
    parser.add_argument(
        "--output-dir",
        default=str(DEFAULT_OUTPUT_DIR),
        help="Directory where output data and charts are written. Defaults to Output_total/Y_Finance.",
    )
    parser.add_argument("--start-date", help="Override start date as YYYYMMDD.")
    parser.add_argument("--end-date", help="Override end date as YYYYMMDD.")
    parser.add_argument("--selected-date", help="Override summary date as YYYYMMDD.")
    parser.add_argument("--fx-ticker", default=DEFAULT_FX_TICKER, help="FX ticker. Defaults to USD/KRW KRW=X.")
    parser.add_argument("--kospi-ticker", default=DEFAULT_KOSPI_TICKER, help="KOSPI ticker. Defaults to ^KS11.")
    parser.add_argument(
        "--report-only",
        action="store_true",
        help="Skip yfinance download and generate the analyst report from the three existing JSON files.",
    )
    parser.add_argument(
        "--market-json",
        default=None,
        help="Primary YFinance JSON used for the analyst report. Required with --report-only.",
    )
    parser.add_argument(
        "--dart-json",
        default=None,
        help="Supporting DART lightweight JSON used for cross analysis. Required with --report-only.",
    )
    parser.add_argument(
        "--news-json",
        default=None,
        help="Supporting news period summary JSON used for cross analysis. Required with --report-only.",
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
    parser.add_argument("--company-name", help="Company name displayed in the analyst report.")
    parser.add_argument("--report-ticker", help="Ticker displayed in the analyst report when --report-only is used.")
    parser.add_argument("--log-level", default="INFO", help="Python logging level.")
    args = parser.parse_args()

    logging.basicConfig(level=getattr(logging, args.log_level.upper(), logging.INFO), format="%(levelname)s %(message)s")
    logger = logging.getLogger("yfinance_pipeline")

    if args.report_only:
        missing = [
            name
            for name, value in {
                "--market-json": args.market_json,
                "--dart-json": args.dart_json,
                "--news-json": args.news_json,
                "--company-name": args.company_name,
                "--report-ticker": args.report_ticker,
            }.items()
            if not value
        ]
        if missing:
            parser.error(f"--report-only requires {', '.join(missing)}")
        report_paths = generate_analyst_report(
            market_json=Path(args.market_json).expanduser().resolve(),
            dart_json=Path(args.dart_json).expanduser().resolve(),
            news_json=Path(args.news_json).expanduser().resolve(),
            report_md=Path(args.report_md).expanduser().resolve(),
            report_json=Path(args.report_json).expanduser().resolve(),
            company_name=args.company_name,
            ticker=args.report_ticker,
        )
        for name, path in report_paths.__dict__.items():
            logger.info("Wrote %s: %s", name, path)
        return

    output_dir = Path(args.output_dir).expanduser().resolve()
    input_path = Path(args.input).expanduser().resolve()
    pipeline_input = load_pipeline_input(input_path)
    pipeline_input = _apply_date_overrides(
        pipeline_input,
        start_date=args.start_date,
        end_date=args.end_date,
        selected_date=args.selected_date,
    )

    paths = run_pipeline(
        pipeline_input,
        output_dir=output_dir,
        fx_ticker=args.fx_ticker,
        kospi_ticker=args.kospi_ticker,
        logger=logger,
    )
    for name, path in paths.__dict__.items():
        logger.info("Wrote %s: %s", name, path)


def _apply_date_overrides(
    pipeline_input: PipelineInput,
    *,
    start_date: str | None,
    end_date: str | None,
    selected_date: str | None,
) -> PipelineInput:
    if not any([start_date, end_date, selected_date]):
        return pipeline_input

    new_start = _parse_override(start_date) if start_date else pipeline_input.start_date
    new_end = _parse_override(end_date) if end_date else pipeline_input.end_date
    new_selected = _parse_override(selected_date) if selected_date else pipeline_input.selected_date
    if new_start > new_end:
        raise ValueError("start_date must be before or equal to end_date.")
    if new_selected < new_start or new_selected > new_end:
        raise ValueError("selected_date must be within start_date..end_date.")

    return PipelineInput(
        ticker=pipeline_input.ticker,
        company_name=pipeline_input.company_name,
        start_date=new_start,
        end_date=new_end,
        selected_date=new_selected,
        source_path=pipeline_input.source_path,
    )


def _parse_override(value: str):
    return datetime.strptime(value, "%Y%m%d").date()


if __name__ == "__main__":
    main()
