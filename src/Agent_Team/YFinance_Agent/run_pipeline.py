#!/usr/bin/env python3
"""Run YFinance data collection, analyst report, and SY validation in one command."""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any


YFINANCE_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = YFINANCE_DIR.parents[2]
DEFAULT_CONFIG_PATH = PROJECT_ROOT / "configs" / "company_input.json"
DEFAULT_ENV_FILE = PROJECT_ROOT / "configs" / ".env"
DEFAULT_OUTPUT_ROOT = PROJECT_ROOT / "Output_total" / "Y_Finance"
DEFAULT_KOSPI_TICKER = "^KS11"
DEFAULT_FX_TICKER = "KRW=X"


def load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def normalize_date(value: str) -> str:
    digits = "".join(character for character in str(value) if character.isdigit())
    if len(digits) != 8:
        raise ValueError("Date values must be YYYYMMDD or YYYY-MM-DD.")
    return digits


def build_run_key(company_name: str | None, selected_date: str, fallback: str | None = None) -> str:
    company = str(company_name or fallback or "company").strip() or "company"
    company = company.replace("/", "_").replace("\\", "_")
    return f"{company}_{normalize_date(selected_date)}"


def date_range_bounds(date_range: str) -> tuple[str, str]:
    parts = str(date_range).split("-", 1)
    if len(parts) != 2:
        raise ValueError("date_range must be YYYYMMDD-YYYYMMDD.")
    return normalize_date(parts[0]), normalize_date(parts[1])


def run_command(cmd: list[str], *, cwd: Path) -> None:
    completed = subprocess.run(cmd, cwd=str(cwd), check=False, text=True, capture_output=True)
    if completed.stdout.strip():
        print(completed.stdout.strip())
    if completed.stderr.strip():
        print(completed.stderr.strip(), file=sys.stderr)
    if completed.returncode != 0:
        raise subprocess.CalledProcessError(completed.returncode, cmd, completed.stdout, completed.stderr)


def ensure_summary_alias(output_dir: Path, selected_date: str) -> Path:
    dated_summary = output_dir / f"market_summary_{normalize_date(selected_date)}.json"
    alias = output_dir / "market_summary.json"
    if dated_summary.exists():
        shutil.copyfile(dated_summary, alias)
    return alias


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the full YFinance Agent pipeline")
    parser.add_argument("--input", default=str(DEFAULT_CONFIG_PATH), help="Run config JSON path.")
    parser.add_argument(
        "--output-dir",
        default=None,
        help="Output directory. Defaults to Output_total/Y_Finance/<company>_<YYYYMMDD>.",
    )
    parser.add_argument("--env-file", default=str(DEFAULT_ENV_FILE), help="Env file containing OPENAI_API_KEY.")
    parser.add_argument("--dart-json", default=None, help="DART lightweight JSON path for report generation.")
    parser.add_argument("--news-json", default=None, help="News period summary JSON path for report generation.")
    parser.add_argument("--model", default=None, help="OpenAI model for YFinance report generation.")
    parser.add_argument("--fx-ticker", default=DEFAULT_FX_TICKER)
    parser.add_argument("--kospi-ticker", default=DEFAULT_KOSPI_TICKER)
    parser.add_argument("--skip-collect", action="store_true", help="Skip yfinance download and reuse existing market files.")
    parser.add_argument("--skip-report", action="store_true", help="Skip analyst report generation.")
    parser.add_argument("--skip-sy", action="store_true", help="Skip SY validation.")
    args = parser.parse_args()

    input_path = Path(args.input).expanduser().resolve()
    payload = load_json(input_path)
    company_name = payload.get("company_name")
    ticker = payload.get("ticker")
    selected_date = normalize_date(str(payload.get("selected_date") or ""))
    start_date, end_date = date_range_bounds(str(payload.get("date_range") or ""))
    run_key = build_run_key(company_name, selected_date, payload.get("company_code"))

    output_dir = Path(args.output_dir).expanduser().resolve() if args.output_dir else DEFAULT_OUTPUT_ROOT / run_key
    output_dir.mkdir(parents=True, exist_ok=True)

    market_json = output_dir / "market_full_dataset.json"
    market_summary = output_dir / "market_summary.json"
    report_md = output_dir / "yfinance_analyst_report.md"
    report_json = output_dir / "yfinance_analyst_report.json"
    sy_output = output_dir / "sy_verified_yfinance_report.json"
    strategy_verified_report = output_dir / "yfinance_verified_report.json"
    sy_question_log = output_dir / "sy_question_answer_log.json"
    manifest_path = output_dir / "pipeline_manifest.json"

    dart_json = (
        Path(args.dart_json).expanduser().resolve()
        if args.dart_json
        else PROJECT_ROOT / "Output_total" / "Financial" / run_key / "dart_lightweight.json"
    )
    news_json = (
        Path(args.news_json).expanduser().resolve()
        if args.news_json
        else PROJECT_ROOT / "Output_total" / "News" / run_key / "context_exports" / "month" / "llm_period_summaries.json"
    )

    if not args.skip_collect:
        run_command(
            [
                sys.executable,
                "main.py",
                "--input",
                str(input_path),
                "--output-dir",
                str(output_dir),
                "--start-date",
                start_date,
                "--end-date",
                end_date,
                "--selected-date",
                selected_date,
                "--kospi-ticker",
                args.kospi_ticker,
                "--fx-ticker",
                args.fx_ticker,
            ],
            cwd=YFINANCE_DIR,
        )
    market_summary = ensure_summary_alias(output_dir, selected_date)

    if not args.skip_report:
        report_cmd = [
            sys.executable,
            "report.py",
            "--market-json",
            str(market_json),
            "--dart-json",
            str(dart_json),
            "--news-json",
            str(news_json),
            "--report-md",
            str(report_md),
            "--report-json",
            str(report_json),
            "--company-name",
            str(company_name or ""),
            "--ticker",
            str(ticker or ""),
            "--env-file",
            str(Path(args.env_file).expanduser().resolve()),
        ]
        if args.model:
            report_cmd.extend(["--model", args.model])
        run_command(report_cmd, cwd=YFINANCE_DIR)

    if not args.skip_sy:
        run_command(
            [
                sys.executable,
                str(YFINANCE_DIR / "SY_Agent" / "sy_agent.py"),
                "--input",
                str(report_json),
                "--output",
                str(sy_output),
                "--strategy-output",
                str(strategy_verified_report),
                "--question-log",
                str(sy_question_log),
                "--env-file",
                str(Path(args.env_file).expanduser().resolve()),
            ],
            cwd=YFINANCE_DIR,
        )

    manifest = {
        "run_key": run_key,
        "company_name": company_name,
        "ticker": ticker,
        "selected_date": selected_date,
        "output_dir": str(output_dir),
        "market_full_dataset": str(market_json),
        "market_summary": str(market_summary),
        "dart_json": str(dart_json),
        "news_json": str(news_json),
        "yfinance_analyst_report_md": str(report_md),
        "yfinance_analyst_report_json": str(report_json),
        "sy_verified_yfinance_report": str(sy_output),
        "yfinance_verified_report": str(strategy_verified_report),
        "sy_question_answer_log": str(sy_question_log),
    }
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(manifest_path)


if __name__ == "__main__":
    main()
