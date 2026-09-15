"""Replay three domain requests on frozen inputs; no news collection or reranking."""
from __future__ import annotations
import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
import copy
import hashlib
import json
import os
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "src"), str(ROOT / "src/Agent_Team/YFinance_Agent")]
from shared.domain_llm import call_domain_response
from shared.llm_clients import measure_request
from shared.subdata_guidance import CONTEXT_POLICY_VERSION
from orchestration.usage_summary import summarize_execution_usage
from Agent_Team.Financial_Agent.financial_analysis_agent import (
    build_financial_request, validate_financial_analysis, apply_financial_analysis,
)
from Agent_Team.News_Agent.analysis_agent import (
    build_llm_request, _merge_analysis_anchor_evidence_ids, _validate_news_analysis_output,
)
from Agent_Team.YFinance_Agent.reporting import build_market_request, validate_market_analysis
from Agent_Team.Strategy_Agent.contracts_v2 import build_compact_strategy_packet_v2
from Agent_Team.Strategy_Agent.contracts_v5 import build_strategy_context_package_v5
from Agent_Team.Strategy_Agent.agent import sanitize_strategy_input_report


def load(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def save(path, data):
    Path(path).write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def run(args):
    bundle = load(args.strategy_input)
    news_input = load(args.news_input)
    reports = bundle["target_reports"]
    financial = reports["financial"]
    market = reports["yfinance"]
    # Reconstruct the same source-level context, not previous agent conclusions.
    market_context = {
        "financial": copy.deepcopy(news_input["secondary_context"]["financial"]),
        "news": copy.deepcopy(financial["secondary_context"]["news"]),
    }
    market_payload = {
        "company_name": bundle["target_company"]["company_name"],
        "market_summary": {"latest_snapshot": {"date": market["as_of_date"]}},
        "primary_evidence_catalog": market["primary_evidence_catalog"],
        "secondary_context": market_context,
    }
    requests = {
        "financial": build_financial_request(financial, model=args.model),
        "news": build_llm_request(input_payload=news_input, model=args.model),
        "market": build_market_request(market_payload, ticker=market.get("ticker") or bundle["target_company"].get("ticker"), model=args.model),
    }
    output = Path(args.output_dir).resolve()
    output.mkdir(parents=True, exist_ok=False)
    hashes = {str(Path(path).resolve()): hashlib.sha256(Path(path).read_bytes()).hexdigest()
              for path in (args.strategy_input, args.news_input)}
    plan = {"context_policy": CONTEXT_POLICY_VERSION, "model": args.model, "source_hashes": hashes,
            "mode": "live" if args.live else "preflight", "status": "prepared",
            "scope": "three target-domain agents and offline Strategy handoff; no peer regeneration or report evaluation",
            "estimated_request_tokens": {}}
    for domain, request in requests.items():
        save(output / f"{domain}_request.json", request)
        plan["estimated_request_tokens"][domain] = measure_request(request, model=args.model).estimated_input_tokens
    save(output / "status.json", plan)
    print(json.dumps(plan, ensure_ascii=False), flush=True)
    if not args.live:
        return
    from dotenv import load_dotenv
    load_dotenv(args.env_file or ROOT / "configs/.env", override=False)
    if not os.getenv("OPENAI_API_KEY"):
        raise RuntimeError("OPENAI_API_KEY unavailable")
    execution = output.name
    os.environ.update(LLM_EXECUTION_ID=execution, LLM_RUN_ROLE="target",
                      LLM_RUN_ID=execution, LLM_COMPANY_NAME=market_payload["company_name"],
                      LLM_USAGE_MANIFEST=str(output / "usage.jsonl"))
    results, errors = {}, {}
    plan["status"] = "running"
    save(output / "status.json", plan)
    try:
        with ThreadPoolExecutor(max_workers=3) as executor:
            futures = {executor.submit(call_domain_response, request, step=f"{domain}:analysis"): domain
                       for domain, request in requests.items()}
            for future in as_completed(futures):
                domain = futures[future]
                try:
                    response = future.result()
                    save(output / f"{domain}_response.json", response.model_dump(mode="json"))
                    results[domain] = json.loads(response.output_text)
                    save(output / f"{domain}_output.json", results[domain])
                    print(f"{domain}: response saved", flush=True)
                except Exception as exc:
                    errors[domain] = str(exc)
        if errors:
            raise RuntimeError(str(errors))
        new_bundle = copy.deepcopy(bundle)
        new_bundle["target_validation_evidence"] = {"financial": {}, "news": {}, "yfinance": {}}
        financial_analysis = validate_financial_analysis(
            results["financial"],
            primary_evidence_ids=list(json.loads(requests["financial"]["input"][1]["content"])["primary_financial_evidence"]),
            secondary_context=financial["secondary_context"],
        )
        new_bundle["target_reports"]["financial"] = apply_financial_analysis(financial, financial_analysis)
        news = results["news"]
        _merge_analysis_anchor_evidence_ids(news)
        _validate_news_analysis_output(news, news_input)
        save(output / "news_evidence_map.json", news_input["evidence_map"])
        news["evidence_map_path"] = str(output / "news_evidence_map.json")
        new_bundle["target_reports"]["news"] = {"output": news}
        new_bundle["evidence_catalogs"]["news"] = copy.deepcopy(news_input["evidence_map"])
        analyzed_market = validate_market_analysis(results["market"], market_payload)
        for key in ("primary_evidence_catalog", "valuation_snapshot", "selected_date", "selected_date_policy"):
            if key in market:
                analyzed_market[key] = copy.deepcopy(market[key])
        analyzed_market["secondary_context"] = market_context
        new_bundle["target_reports"]["yfinance"] = analyzed_market
        # Exercise the same source-field retention as normal Strategy loading.
        new_bundle["target_reports"] = {
            domain: sanitize_strategy_input_report(report, domain)
            for domain, report in new_bundle["target_reports"].items()
        }
        packet, provenance, _, _ = build_compact_strategy_packet_v2(new_bundle)
        context = build_strategy_context_package_v5(packet, input_bundle=new_bundle)
        save(output / "normalized_domain_bundle.json", new_bundle)
        save(output / "strategy_packet.json", packet)
        save(output / "strategy_provenance.json", provenance)
        save(output / "strategy_context.json", context)
        plan["status"] = "completed"
        plan["context_issues"] = {domain: len(view["cross_domain_assessments"])
                                  for domain, view in context["domain_handoffs"].items()}
        plan["strategy_card_count"] = len(packet["cards"])
        print(json.dumps(plan, ensure_ascii=False), flush=True)
    except Exception as exc:
        plan.update(status="failed", error=str(exc))
        raise
    finally:
        plan["sources_unchanged"] = all(hashlib.sha256(Path(path).read_bytes()).hexdigest() == digest
                                         for path, digest in hashes.items())
        save(output / "status.json", plan)
        save(output / "usage_summary.json", summarize_execution_usage(
            output / "usage.jsonl", execution_id=execution, pipeline_completed=plan["status"] == "completed",
            expected_logical_calls_by_role={"target": 3, "peer": 0, "final": 0},
        ))


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--strategy-input", required=True)
    parser.add_argument("--news-input", required=True)
    parser.add_argument("--output-dir", required=True)
    parser.add_argument("--model", default="gpt-5.4-mini")
    parser.add_argument("--env-file")
    parser.add_argument("--live", action="store_true")
    run(parser.parse_args())
