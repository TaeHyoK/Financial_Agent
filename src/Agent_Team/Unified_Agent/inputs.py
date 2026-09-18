"""Rebuild pre-analysis packets from the completed Full run's frozen sources."""
from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path
from unittest.mock import patch

from Agent_Team.Financial_Agent.financial_analysis_agent import build_financial_request
from Agent_Team.Financial_Agent.langgraph_flow import build_financial_analyst_output
from Agent_Team.News_Agent.analysis_agent import build_llm_request
from Agent_Team.YFinance_Agent import reporting
from .io import read_json
from .report import PROTOCOL


class _CapturedMarket(Exception):
    pass


def prepare_entity(*, source_root: Path, entity: dict, model: str):
    day = entity["selected_date"]
    financial = source_root / "Financial" / day
    market = source_root / "Y_Finance" / day
    news = source_root / "News" / day
    files = {
        "financial_main": financial / "dart_main.json",
        "financial_master": financial / "dart_master.json",
        "financial_lightweight": financial / "dart_lightweight.json",
        "market_summary": market / "market_summary.json",
        "market_json": market / "market_full_dataset.json",
        "market_csv": market / "market_full_dataset.csv",
        "market_manifest": market / "manifest.json",
        "valuation": market / "valuation_snapshot.json",
        "news_input": news / "output/news_agent_input_payload.json",
        "full_financial_request": financial / "actual_llm_request.json",
        "full_news_request": news / "output/news_agent_llm_request.json",
        "monthly_summaries": news / "context_exports/month/llm_period_summaries.json",
        "selected_articles": news / "context_exports/month/selected_articles.json",
        "company_config": Path(entity["company_config"]),
    }
    source_artifacts = {key: {"path": str(path.resolve()), "sha256": hashlib.sha256(path.read_bytes()).hexdigest()}
                        for key, path in files.items()}
    payload = read_json(files["news_input"])
    target = payload["target_entity"]
    if target["company_name"] != entity["company_name"]:
        raise ValueError("News target differs from the collection manifest")
    config = read_json(files["company_config"])
    if config["selected_date"].replace("-", "") != day:
        raise ValueError("Company config uses a different report date")
    if read_json(files["market_manifest"])["selected_date"].replace("-", "") != day:
        raise ValueError("Market source uses a different report date")
    factual = build_financial_analyst_output({"target_entity": target}, {
        "dart_main": read_json(files["financial_main"]),
        "dart_master": read_json(files["financial_master"]),
        "yfinance_market_summary": read_json(files["market_summary"]),
        "news_weekly_summaries": read_json(files["monthly_summaries"]),
    })
    financial_request = build_financial_request(factual, model=model)
    news_request = build_llm_request(input_payload=payload, model=model)
    # Assert that the input and analytical guidelines still match the executed Full.
    for request, path in ((financial_request, files["full_financial_request"]),
                          (news_request, files["full_news_request"])):
        if request["input"] != read_json(path)["input"]:
            raise ValueError(f"Unified domain input differs from completed Full: {path}")
    captured = {}
    def capture(value, **kwargs):
        captured.update(copy.deepcopy(value))
        raise _CapturedMarket()
    # Reuse the exact production preprocessing path and stop BEFORE any API call.
    with patch.object(reporting, "generate_agent_json_report_with_llm", side_effect=capture):
        try:
            reporting.generate_analyst_report(market_json=files["market_json"], dart_json=files["financial_lightweight"],
                news_json=files["monthly_summaries"], valuation_json=files["valuation"],
                company_name=entity["company_name"], ticker=config["ticker"], model=model,
                report_json=source_root / "never_written.json", report_md=source_root / "never_written.md")
        except _CapturedMarket:
            pass
    if not captured:
        raise RuntimeError("Market pre-analysis packet was not captured")
    market_request = reporting.build_market_request(captured, ticker=config["ticker"], model=model)
    financial_packet = json.loads(financial_request["input"][1]["content"])
    news_packet = json.loads(news_request["input"][1]["content"])
    market_packet = json.loads(market_request["input"][1]["content"])
    context = {"protocol": PROTOCOL,
        "boundary_requests": {"financial": financial_request, "news": news_request, "market": market_request},
        "financial": {"preprocessed_input": financial_packet, "evidence": financial_packet["primary_financial_evidence"]},
        "news": {"preprocessed_input": news_packet, "evidence": {
            key: value for key, value in payload["evidence_map"].items()
            if key.startswith("NEWS_RAW_") and value.get("domain") == "news"}},
        "market": {"preprocessed_input": market_packet, "evidence": captured["primary_evidence_catalog"]},
    }
    return {"semantic_input": context, "adapter_facts": {"financial_factual_report": factual,
        "market_facts": {"valuation_snapshot": captured["valuation_snapshot"],
                         "primary_evidence_catalog": captured["primary_evidence_catalog"]}},
        "source_artifacts": source_artifacts,
        "input_policy": {"prior_domain_agent_reports_used": False,
                         "monthly_summaries_reused_from_full": True,
                         "all_selected_news_articles_preserved": True}}
