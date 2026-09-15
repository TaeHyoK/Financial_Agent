"""Validate reused inputs before they can bypass the collection stages."""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

from Agent_Team.Financial_Agent.financial_index_calculator import calculate_financial_index_files
from Agent_Team.News_Agent.collectors.candidate_preparation import require_common_candidate_pool
from shared.subdata import MARKET_METRICS
from shared.time_windows import monthly_windows

from .config import load_run_config


def read_json(path: Path):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise ValueError(f"Unable to read reused input: {path}") from exc


def validate_financial_source(paths, run_config) -> None:
    master = read_json(paths.dart_master)
    context = master.get("collection_context") or {}
    if str(context.get("selected_date") or "").replace("-", "") != run_config.selected_date:
        raise ValueError(f"Reused DART selected date does not match {run_config.selected_date}: {paths.dart_master}")
    tables = [table for section in ("4-1", "4-2", "4-4")
              for table in (master.get(section) or {}).get("tables", [])]
    if not tables:
        raise ValueError(f"Reused DART requires canonical source statements: {paths.dart_master}")
    receipts = [period.get("receipt_date") for table in tables
                for period in (table.get("periods") or {}).values()]
    receipts.extend(filing.get("receipt_date") for filing in context.get("reports_used") or [])
    if any(str(receipt).replace("-", "") >= run_config.selected_date for receipt in receipts if receipt):
        raise ValueError(f"Reused DART contains a filing at or after the information cutoff: {paths.dart_master}")


def rebuild_financial_indices(paths) -> list[str]:
    """Use the archived master, which retains more history than old handoffs."""
    calculate_financial_index_files(
        master_path=paths.dart_master,
        handoff_path=paths.dart_master,
        index_path=paths.project_root / "src/Agent_Team/Financial_Agent/financial_index.json",
        output_dir=paths.financial_dir,
    )
    return [str(paths.dart_main), str(paths.dart_lightweight), str(paths.financial_dir / "financial_subdata.json")]


def validate_domain_source(paths, run_config, *, market_dates=None, news_period_count=12) -> None:
    source_config = load_run_config(paths.run_config_copy)
    if source_config.effective_date_range != run_config.effective_date_range:
        raise ValueError(
            "Reused domain analysis date range differs from requested date_range: "
            f"snapshot={source_config.effective_date_range}, requested={run_config.effective_date_range}. "
            "Collect a matching snapshot, or reuse only DART with --reuse-dart-data-from."
        )
    for field in ("selected_date", "company_code", "ticker"):
        if getattr(source_config, field) != getattr(run_config, field):
            raise ValueError(f"Reused domain identity/date mismatch: {field}")
    manifest = read_json(paths.yfinance_dir / "manifest.json")
    start, end = market_dates or (run_config.start_date, run_config.end_date)
    requested_range = {"start": start, "end": end}
    actual_range = {key: str((manifest.get("date_range") or {}).get(key) or "").replace("-", "")
                    for key in ("start", "end")}
    if actual_range != requested_range or str(manifest.get("selected_date") or "").replace("-", "") != run_config.selected_date:
        raise ValueError("Reused market date range or selected date does not match the requested analysis period.")
    if (manifest.get("price_basis") or {}).get("returns_and_technical_indicators") != "provider_split_adjusted_close_excluding_cash_dividends":
        raise ValueError("Reused market snapshot uses an incompatible price basis; recollect market data.")
    full = read_json(paths.yfinance_dir / "market_full_dataset.json")
    if not isinstance(full, list) or not full:
        raise ValueError("Reused market snapshot has no daily observations.")
    if any(not isinstance(row, dict) or not start <= str(row.get("date") or "").replace("-", "") <= end for row in full):
        raise ValueError("Reused market observations fall outside the requested analysis period.")
    required = set(MARKET_METRICS) | {"stock_close_to_ma120", "stock_close_to_ma200", "stock_ma120_change_20d", "stock_ma200_change_20d", "stock_position_52w"}
    if any(not required.issubset(row) for row in full):
        raise ValueError("Reused market snapshot lacks annual indicators; recollect market data.")
    latest = max(full, key=lambda row: row["date"])
    summary = read_json(paths.market_summary_dated)
    if not isinstance(summary, list) or len(summary) != 1 or any(summary[0].get(key) != latest.get(key) for key in required | {"date", "stock_close"}):
        raise ValueError("Reused market summary does not match the latest daily observation.")
    require_common_candidate_pool(read_json(paths.news_report_context))
    if paths.news_granularity == "month":
        expected = {window["period"] for window in monthly_windows(
            datetime.strptime(run_config.selected_date, "%Y%m%d").date(), news_period_count
        )}
        from shared.news_articles import build_article_packet
        packet = read_json(paths.news_articles)
        rebuilt = build_article_packet(read_json(paths.news_report_context), period_count=news_period_count)
        if packet != rebuilt:
            raise ValueError("Reused news article packet differs from the selected source articles")
        summaries = packet["periods"]
        periods = [row.get("period") for row in summaries if isinstance(row, dict)]
        if len(periods) != len(expected) or set(periods) != expected:
            raise ValueError("Reused News articles do not cover the requested monthly analysis period exactly once.")
        from Agent_Team.News_Agent.context_export import (
            article_summary_input, _build_llm_summary_request, summary_request_hash, _attach_source_event_ids,
        )
        summary = read_json(paths.news_llm_period_summaries)
        request = _build_llm_summary_request(article_summary_input(
            packet, read_json(paths.news_report_context), paths.news_report_context), str(summary.get("model") or ""))
        if summary.get("source_request_sha256") != summary_request_hash(request):
            raise ValueError("Reused news summaries do not match the selected articles/current summary request")
        _attach_source_event_ids(summary.get("output"), request)
    validate_financial_source(paths, run_config)
