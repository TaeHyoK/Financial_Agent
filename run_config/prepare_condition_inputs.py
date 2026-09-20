"""Prepare frozen ablation inputs and estimate cost. No network or LLM calls."""
import argparse
from collections import Counter
from copy import deepcopy
from datetime import date
import hashlib
import json
import os
from pathlib import Path
import socket
import statistics
import sys
from unittest.mock import patch


def read(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def save(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + "\n", encoding="utf-8")


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def profile(values):
    return {"low": min(values), "central": statistics.median(values), "high": max(values)}


def run(args):
    workspace = args.workspace.resolve()
    repo = workspace
    src = repo / "src"
    sys.path[:0] = [str(src), str(src / "Agent_Team/YFinance_Agent"), str(workspace)]
    # Reuse the existing matched sampler, but bind its source imports to this repo.
    import ablation_suite.config as sampling_config
    sampling_config.FINAL_SRC = src
    from ablation_suite import annual_random
    from ablation_suite.utils import stable_seed
    from Agent_Team.News_Agent import context_export, analysis_agent
    from Agent_Team.News_Agent.collectors.candidate_preparation import require_common_candidate_pool
    from Agent_Team.News_Agent.collectors.report_snippets import require_prepared_summary_snippets
    from Agent_Team.Financial_Agent.langgraph_flow import build_financial_analyst_output
    from Agent_Team.Financial_Agent.financial_analysis_agent import build_financial_request, build_financial_llm_packet
    from Agent_Team.YFinance_Agent import reporting
    from shared.llm_clients import measure_request, estimate_text_tokens, compact_json
    from shared.subdata import financial_subdata, news_subdata, secondary_context_for_llm
    from shared.news_articles import build_article_packet

    assert Path(context_export.__file__).resolve().is_relative_to(repo)
    assert Path(reporting.__file__).resolve().is_relative_to(repo)
    collection = read(workspace / "run_config/collection_manifest.json")
    status = read(workspace / "status/data_collection_status.json")
    if status["state"] != "success" or len(status["entities"]) != 14:
        raise ValueError("Expected fourteen successful frozen company snapshots")
    output = workspace / "prepared_inputs" / f"replicate_{args.replicate:02}"
    output.mkdir(parents=True, exist_ok=False)
    source_hashes, measurements, fairness, conditions = {}, [], [], []
    models = {"analysis": "gpt-5.4", "news_summary": "gpt-5.6-luna"}

    def frozen(path):
        source_hashes[str(path)] = digest(path)
        return read(path)

    def tokens(request):
        return measure_request(request, model=request["model"]).estimated_input_tokens

    pilot = frozen(workspace / "experiments/luna_summary_pilot/pilot_manifest.json")
    summary_outputs, summary_context_sizes, pilot_contexts = [], [], []
    for case in pilot["cases"]:
        result = frozen(Path(case["result"]))
        count = case["input_event_count"]
        summary_outputs.append(case["usage"]["completion_tokens"] / count)
        actual_context = news_subdata(result)
        pilot_contexts.append((count, actual_context))
        context = secondary_context_for_llm({"news": actual_context})["news"]
        summary_context_sizes.append(estimate_text_tokens(compact_json(context), model=models["analysis"]) / count)
    summary_output_profile = profile(summary_outputs)
    context_size_profile = profile(summary_context_sizes)
    baseline = Path(os.getenv(
        "ABLATION_HISTORY_ROOT",
        workspace / "historical_inputs/amore_monthly_summary_full_random_20260914",
    ))
    history = []
    for condition in ("full", "random"):
        path = baseline / condition / "outputs/아모레퍼시픽/runs/20251107/executions" / f"amore_monthly_summary_full_random_20260914_{condition}" / "llm_usage_manifest.jsonl"
        source_hashes[str(path)] = digest(path)
        history.extend(json.loads(line) for line in path.read_text().splitlines() if line.strip())
    domain_profiles = {}
    final_profiles = {}
    for kind, step in (("news", "news:analysis"), ("financial", "financial:analyst_report"), ("market", "yfinance:analyst_report")):
        domain_profiles[kind] = profile([r["usage"]["output_tokens"] for r in history if r.get("status") == "ok" and r["step"] == step])
    for kind, prefix in (("comparison", "competitor:"), ("strategy", "strategy:"), ("writer", "writer:")):
        rows = [r for r in history if r.get("status") == "ok" and r["step"].startswith(prefix)]
        final_profiles[kind] = {key: profile([r["usage"][key] for r in rows]) for key in ("input_tokens", "output_tokens")}

    class Captured(Exception):
        pass

    with patch.object(socket.socket, "connect", side_effect=RuntimeError("Offline preparation forbids network")), \
         patch.object(context_export, "_build_openai_client", side_effect=RuntimeError("No paid calls allowed")):
        for company in collection["companies"]:
            target = company["target"]["company_name"]
            day = company["report_date"]
            boundary = date.fromisoformat(f"{day[:4]}-{day[4:6]}-{day[6:]}")
            reference = workspace / company["reference_report"]["file"]
            source_hashes[str(reference)] = digest(reference)
            if source_hashes[str(reference)] != company["reference_report"]["sha256"]:
                raise ValueError("Reference report changed")
            entity_specs = {name: [] for name in ("full", "random_news", "no_peer", "no_subdata")}
            for role in ("target", "peer"):
                entity = company[role]
                name = entity["company_name"]
                state = status["entities"][f"{target}:{role}:{name}"]
                if state["state"] != "success":
                    raise ValueError(f"Incomplete source: {name}")
                report_path = Path(state["steps"]["news_collect"]["outputs"][0])
                report = frozen(report_path)
                config_path = workspace / "run_config/companies" / target / f"{role}_{name}.json"
                config = frozen(config_path)
                root = Path(state["output_root"]) / name
                financial_dir, market_dir = root / "Financial" / day, root / "Y_Finance" / day
                dart, master = frozen(financial_dir / "dart_main.json"), frozen(financial_dir / "dart_master.json")
                lightweight = frozen(financial_dir / "dart_lightweight.json")
                market_summary = frozen(market_dir / "market_summary.json")
                for path in (market_dir / "market_full_dataset.json", market_dir / "manifest.json", market_dir / "valuation_snapshot.json"):
                    frozen(path)
                require_common_candidate_pool(report)
                require_prepared_summary_snippets(report)
                packet = build_article_packet(report)
                if packet != frozen(root / "News" / day / "context_exports/month/selected_articles.json"):
                    raise ValueError(f"Selected articles changed: {name}")
                if report["collect_date"] != (boundary.fromordinal(boundary.toordinal()-1)).isoformat():
                    raise ValueError("News cutoff differs from report date")
                seed = stable_seed(args.seed, day, f"{name}:replicate:{args.replicate}")
                randomized, selection_audit = annual_random.select_annual_random_events(report, seed=seed)
                captured = {}
                def capture(payload, **kwargs):
                    captured.update(deepcopy(payload))
                    raise Captured()
                with patch.object(reporting, "generate_agent_json_report_with_llm", side_effect=capture):
                    try:
                        reporting.generate_analyst_report(market_json=market_dir / "market_full_dataset.json",
                            dart_json=financial_dir / "dart_lightweight.json", news_json=Path("not_used"),
                            valuation_json=market_dir / "valuation_snapshot.json", company_name=name, ticker=entity["ticker"],
                            report_md=output / "not_written.md", report_json=output / "not_written.json", primary_data_only=True)
                    except Captured:
                        pass
                if not captured:
                    raise ValueError("Market input capture failed")
                primary_financial = build_financial_analyst_output({"target_entity": {**entity, "as_of_date": boundary.isoformat()}},
                    {"dart_main": dart, "dart_master": master, "yfinance_market_summary": {}, "news_weekly_summaries": {}})
                full_financial = build_financial_analyst_output({"target_entity": {**entity, "as_of_date": boundary.isoformat()}},
                    {"dart_main": dart, "dart_master": master, "yfinance_market_summary": market_summary, "news_weekly_summaries": {}})
                primary_packet, full_packet = build_financial_llm_packet(primary_financial), build_financial_llm_packet(full_financial)
                primary_packet.pop("secondary_context")
                full_packet.pop("secondary_context")
                if primary_packet != full_packet:
                    raise ValueError("No-subdata changed primary financial input")
                base_market = deepcopy(captured)
                base_market["secondary_context"]["financial"] = financial_subdata(lightweight)
                base_market["data_policy"]["primary_data_only"] = False
                financial_base_tokens = tokens(build_financial_request(full_financial, model=models["analysis"]))
                market_base_tokens = tokens(reporting.build_market_request(base_market, ticker=entity["ticker"], model=models["analysis"]))
                financial_deltas, market_deltas = [], []
                # Size proxies only: real pilot summaries are never saved as this company's inputs.
                # Rebuild schemas as well as prompts to include citation-enum overhead.
                for article_count, actual_context in pilot_contexts:
                    proxy_financial, proxy_market = deepcopy(full_financial), deepcopy(base_market)
                    proxy_financial["secondary_context"]["news"] = actual_context
                    proxy_market["secondary_context"]["news"] = actual_context
                    financial_deltas.append((tokens(build_financial_request(proxy_financial, model=models["analysis"])) - financial_base_tokens) / article_count)
                    market_deltas.append((tokens(reporting.build_market_request(proxy_market, ticker=entity["ticker"], model=models["analysis"])) - market_base_tokens) / article_count)
                common = {"role": role, "company_name": name, "company_config": str(config_path),
                          "frozen_company_root": str(root), "selected_date": day, "date_range": config["date_range"]}
                built, counts = {}, {}
                for condition, selected in (("full", report), ("random_news", randomized)):
                    dest = output / target / condition / role
                    # Only selected input is copied; the complete candidate pool stays frozen at its source.
                    selected_context = {key: deepcopy(value) for key, value in selected.items()
                                        if not key.startswith("news_events_")}
                    selected_context.update(news_events_weekly=deepcopy(selected["news_events_weekly"]),
                                            news_events_final=deepcopy(selected["news_events_final"]))
                    selected_context["frozen_candidate_source"] = str(report_path)
                    save(dest / "selected_context.json", selected_context)
                    exports = context_export.build_context_exports(report_context_path=dest / "selected_context.json",
                        output_dir=dest / "context_exports/month", llm_model=models["news_summary"], split_by_period=True, run_llm=False)
                    selected_packet = read(exports["news_articles_path"])
                    counts[condition] = [len(p["events"]) for p in selected_packet["periods"]]
                    paths = analysis_agent._resolve_paths(project_root=repo, context_export_dir=dest / "context_exports",
                        granularity="month", as_of_date=boundary, dart_lightweight_path=str(financial_dir / "dart_lightweight.json"),
                        market_summary_path=str(market_dir / "market_summary.json"), output_dir=str(dest / "news"))
                    news = analysis_agent.build_analysis_input_payload(company_name=name, ticker=entity["ticker"], corp_code=entity["corp_code"],
                        as_of_date=boundary, paths=paths, max_raw_events_per_period=1)
                    request = analysis_agent.build_llm_request(input_payload=news, model=models["analysis"])
                    save(dest / "news/news_agent_input_payload.json", news)
                    save(dest / "news/news_agent_llm_request.json", request)
                    # These packets are not final requests: genuine monthly summaries are still pending.
                    save(dest / "financial_before_news_summary.json", full_financial)
                    save(dest / "market_before_news_summary.json", base_market)
                    monthly = context_export._build_period_llm_requests(read(exports["llm_summary_request_path"]))
                    if len(monthly) != 12 or any(req["model"] != models["news_summary"] or "temperature" in req for _, req in monthly):
                        raise ValueError("Monthly summary model/call contract differs")
                    built[condition] = {**common, "input_dir": str(dest), "articles": len(selected_packet["events"]),
                        "news_request": str(dest / "news/news_agent_llm_request.json"),
                        "summary_request": exports["llm_summary_request_path"], "summary_calls": 12,
                        "pending": ["monthly_summaries", "financial_and_market_requests_with_actual_summaries"]}
                    entity_specs[condition].append(built[condition])
                    measurements.append({"target_company": target, "role": role, "company_name": name, "condition": condition,
                        "articles": len(selected_packet["events"]), "monthly_counts": counts[condition],
                        "summary_input_tokens": sum(tokens(req) for _, req in monthly), "news_input_tokens": tokens(request),
                        "financial_base_input_tokens": financial_base_tokens,
                        "market_base_input_tokens": market_base_tokens,
                        "financial_summary_added_tokens_per_article": profile(financial_deltas),
                        "market_summary_added_tokens_per_article": profile(market_deltas),
                        "summary_pending": True})
                if counts["full"] != counts["random_news"]:
                    raise ValueError("Full and Random differ in monthly input counts")
                dest = output / target / "no_subdata" / role
                full_paths = analysis_agent._resolve_paths(project_root=repo, context_export_dir=Path(built["full"]["input_dir"]) / "context_exports",
                    granularity="month", as_of_date=boundary, dart_lightweight_path=str(financial_dir / "dart_lightweight.json"),
                    market_summary_path=str(market_dir / "market_summary.json"), output_dir=str(dest / "news"))
                no_sub_news = analysis_agent.build_analysis_input_payload(company_name=name, ticker=entity["ticker"], corp_code=entity["corp_code"],
                    as_of_date=boundary, paths=full_paths, max_raw_events_per_period=1, include_secondary_context=False)
                full_news = read(Path(built["full"]["input_dir"]) / "news/news_agent_input_payload.json")
                if no_sub_news["news_context"] != full_news["news_context"]:
                    raise ValueError("No-subdata changed primary news")
                requests = {"news": analysis_agent.build_llm_request(input_payload=no_sub_news, model=models["analysis"]),
                    "financial": build_financial_request(primary_financial, model=models["analysis"]),
                    "market": reporting.build_market_request(captured, ticker=entity["ticker"], model=models["analysis"])}
                save(dest / "news_agent_input_payload.json", no_sub_news)
                for kind, request in requests.items():
                    save(dest / f"{kind}_request.json", request)
                entity_specs["no_subdata"].append({**common, "input_dir": str(dest), "primary_news_reused_from": built["full"]["input_dir"],
                    "summary_calls": 0, "articles": len(packet["events"]), "pending": []})
                measurements.append({"target_company": target, "role": role, "company_name": name, "condition": "no_subdata",
                    "articles": len(packet["events"]), "monthly_counts": counts["full"], "summary_input_tokens": 0,
                    "news_input_tokens": tokens(requests["news"]), "financial_base_input_tokens": tokens(requests["financial"]),
                    "market_base_input_tokens": tokens(requests["market"]), "summary_pending": False})
                if role == "target":
                    entity_specs["no_peer"].append({**built["full"], "reuse_domain_outputs_from": "full", "new_domain_calls": 0, "summary_calls": 0})
                # Full and prefilter clusters have different ID namespaces; compare article URLs, not event IDs.
                full_ids = {r["representative"]["url"] for r in report["news_events_weekly"]}
                random_ids = {r["representative"]["url"] for r in randomized["news_events_weekly"]}
                if not all(full_ids | random_ids):
                    raise ValueError("Selected article URL is missing")
                full_articles = {r["representative"]["url"]: r["representative"] for r in report["news_events_weekly"]}
                for row in randomized["news_events_weekly"]:
                    representative = row["representative"]
                    same = full_articles.get(representative["url"])
                    if same and any(same.get(key) != representative.get(key) for key in ("time", "title", "snippet", "source")):
                        raise ValueError("The same article has different frozen text between conditions")
                pool = report["news_events_prefilter"]
                from shared.news_selection import event_period
                from shared.time_windows import monthly_windows
                windows = monthly_windows(boundary, 12)
                bucket = lambda r: (annual_random._week(r), event_period(r["representative"]["time"][:10], windows))
                capacities, quotas = Counter(bucket(r) for r in pool), Counter(bucket(r) for r in report["news_events_weekly"])
                forced = sum(n for key, n in quotas.items() if capacities[key] == n)
                fairness.append({"target_company": target, **common, "full_count": len(full_ids), "random_count": len(random_ids),
                    "monthly_counts_equal": True, "no_subdata_primary_equal": True, "shared_snippets_frozen": True,
                    "random_source_pool": "news_events_prefilter", "random_seed": seed,
                    "overlap_identity": "representative_article_url; event IDs differ between cluster namespaces",
                    "overlap_count": len(full_ids & random_ids), "overlap_fraction": len(full_ids & random_ids) / len(full_ids),
                    "forced_random_articles": forced, "forced_random_fraction": forced / len(full_ids),
                    "prefilter_count": len(pool), "filtered_count": len(report["news_events_all"])})
                save(output / target / "random_news" / role / "selection_audit.json", selection_audit)
            for condition, entities in entity_specs.items():
                spec = {"target_company": target, "condition": condition, "selected_date": day, "decision_horizon": "12개월",
                    "models": models, "entities": entities, "reference_report": str(reference),
                    "final_flags": ["--no-competitor"] if condition == "no_peer" else ["--primary-data-only"] if condition == "no_subdata" else [],
                    "report_output_root": str(workspace / "reports" / condition / f"replicate_{args.replicate:02}"),
                    "new_analysis_calls": 0 if condition == "no_peer" else 6, "new_final_calls": 2 if condition == "no_peer" else 3}
                save(output / target / condition / "condition_manifest.json", spec)
                conditions.append(spec)

    if any(digest(Path(path)) != value for path, value in source_hashes.items()):
        raise ValueError("Frozen source changed during preparation")
    costs = []
    for spec in conditions:
        rows = [r for r in measurements if r["target_company"] == spec["target_company"] and r["condition"] == spec["condition"]]
        scenarios = {}
        for scenario, margin in (("low", .75), ("central", 1.0), ("high", 1.5)):
            summary_cost = domain_cost = final_cost = 0.0
            for row in rows:
                if row["summary_pending"]:
                    summary_cost += (row["summary_input_tokens"] * .25 + row["articles"] * summary_output_profile[scenario] * 1.2) / 1e6
                extra = row["articles"] * (
                    row["financial_summary_added_tokens_per_article"][scenario]
                    + row["market_summary_added_tokens_per_article"][scenario]
                ) if row["summary_pending"] else 0
                domain_input = row["news_input_tokens"] + row["financial_base_input_tokens"] + row["market_base_input_tokens"] + extra
                domain_output = sum(p[scenario] for p in domain_profiles.values()) * margin
                domain_cost += (domain_input * 2.5 + domain_output * 15) / 1e6
            for kind in (("comparison", "strategy", "writer") if spec["condition"] != "no_peer" else ("strategy", "writer")):
                p = final_profiles[kind]
                final_cost += (p["input_tokens"][scenario] * 2.5 + p["output_tokens"][scenario] * 15) * margin / 1e6
            scenarios[scenario] = {"monthly_summary_usd": round(summary_cost, 4), "domain_analysis_usd": round(domain_cost, 4),
                "final_stages_usd": round(final_cost, 4), "total_usd": round(summary_cost + domain_cost + final_cost, 4)}
        costs.append({"company": spec["target_company"], "condition": spec["condition"], "scenarios": scenarios})
    totals = {s: round(sum(r["scenarios"][s]["total_usd"] for r in costs), 2) for s in ("low", "central", "high")}
    manifest = {"status": "prepared_offline", "paid_calls": 0, "replicate": args.replicate, "base_seed": args.seed,
        "reports_planned": len(conditions), "models": models, "source_hashes": source_hashes, "sources_unchanged": True,
        "sampler_source": str(Path(annual_random.__file__).resolve()), "sampler_sha256": digest(Path(annual_random.__file__)),
        "generation_code_hashes": {str(p.relative_to(repo)): digest(p) for p in src.rglob("*.py")},
        "new_monthly_summary_calls": 336, "new_domain_analysis_calls": 126, "new_final_calls": 77,
        "total_new_calls": 539, "fairness": fairness, "measurements": measurements, "conditions": conditions}
    manifest["input_readiness"] = {
        "complete_news_requests": 42,
        "complete_no_subdata_financial_requests": 14,
        "complete_no_subdata_market_requests": 14,
        "pending_financial_and_market_requests": 56,
        "reason": "Full/Random Financial and Market requests must be rebuilt after actual monthly summaries exist.",
        "no_peer_target_analysis": "reuse Full target outputs; no duplicate generation",
    }
    save(output / "preparation_manifest.json", manifest)
    estimate = {"currency": "USD", "totals": totals, "costs": costs,
        "summary_output_tokens_per_article": summary_output_profile, "summary_subdata_tokens_per_article": context_size_profile,
        "domain_output_tokens": domain_profiles, "final_profiles": final_profiles,
        "assumptions": ["One replicate only; 28 final reports.", "No cache-read discounts; Luna input conservatively billed as cache writes ($0.25/M).",
            "Summary and News input sizes measured from prepared requests; Financial/Market base sizes measured before genuine monthly summaries exist.",
            "Missing subdata size and citation-schema overhead estimated by inserting five real Luna pilot summaries into sizing-only requests; downstream outputs and final inputs estimated from two recent GPT-5.4 runs.",
            "Low/high are scenarios with 0.75/1.5 margins, not confidence bounds or a spending cap.",
            "No-peer reuses Full target analysis; Full summaries and identical data are not regenerated.",
            "No retries, evaluation LLM calls, BERTScore GPU time or Batch discount included."],
        "pricing_sources": ["https://developers.openai.com/api/docs/models/gpt-5.4", "https://developers.openai.com/api/docs/models/gpt-5.6-luna"]}
    save(output / "cost_estimate.json", estimate)
    lines = ["# 7개 기업 제거 실험 입력 준비", "", "유료 호출 없이 28개 보고서(기업 7개 × 조건 4개), 각 조건 1회 실행분의 입력을 준비했다.", "",
        "월별 요약은 GPT-5.6 Luna, 하위 분석·비교 분석·Strategy·Writer는 GPT-5.4를 사용한다.", "",
        "Full은 기업 필터와 공시 섹션 유사도 선정, Random은 기업 필터 이전 중복 사건에서 주별·월별 동일 개수 무작위 추출이다. 동일한 스니펫과 중복처리 결과를 사용한다.",
        "No-subdata는 Full의 주 자료를 그대로 두고 타 도메인 보조자료만 제거한다. No-peer는 Full 대상기업의 하위 분석을 재사용하고 비교 분석 없이 Strategy·Writer만 재생성한다.", "",
        "| 대상기업 | 비교기업 | Full/Random 대상 사건 수 | Full/Random 비교 사건 수 |", "|---|---|---:|---:|"]
    for company in collection["companies"]:
        name = company["target"]["company_name"]
        target, peer = [r for r in fairness if r["target_company"] == name]
        lines.append(f"| {name} | {company['peer']['company_name']} | {target['full_count']} | {peer['full_count']} |")
    lines += ["", "## 예상 생성 비용", "", f"합계: 낮은 시나리오 ${totals['low']:.2f}, 중심 추정 ${totals['central']:.2f}, 높은 시나리오 ${totals['high']:.2f}.", "",
        "| 기업 | Full | Random | No-peer | No-subdata | 합계(중심 추정) |", "|---|---:|---:|---:|---:|---:|"]
    for company in collection["companies"]:
        name = company["target"]["company_name"]
        values = [r["scenarios"]["central"]["total_usd"] for r in costs if r["company"] == name]
        lines.append(f"| {name} | " + " | ".join(f"${v:.2f}" for v in values) + f" | ${sum(values):.2f} |")
    lines += ["", "요약 336회 + 하위 분석 126회 + 비교·Strategy·Writer 77회 = 신규 호출 539회. 실패·재시도·LLM 평가는 별도다.", "",
        "정확히 측정한 입력과 아직 생성되지 않아 추정한 입력은 cost_estimate.json에 구분했다. 이 범위는 상한 보장이 아니며, 유료 실행 전 비용 승인을 받아야 한다.", "",
        "쿠쿠홀딩스는 55개 후보에서 43개를 선정하므로 비교기업 뉴스의 무작위 조건 차이가 제한적이다. 조건별 중첩률과 무작위 선택이 강제되는 비중은 preparation_manifest.json에 기록했다.", "",
        "Full·Random의 월별 요약은 아직 생성하지 않았다. financial_before_news_summary.json과 market_before_news_summary.json은 중간 자료이며 실제 요약을 연결해 요청을 다시 구성해야 한다. No-subdata 요청은 완성되어 있다.", "",
        "원래 수집 자료와 기사 내용은 수정하지 않았다. 코드·데이터 해시를 보관했으며 준비 중 원본 변경이 없음을 확인했다.", ""]
    (output / "입력_준비_및_예상비용.md").write_text("\n".join(lines), encoding="utf-8")
    print(json.dumps({"output": str(output), "reports": len(conditions), "paid_calls": 0, "cost_estimate_usd": totals}, ensure_ascii=False))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workspace", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--seed", type=int, default=20251031)
    parser.add_argument("--replicate", type=int, default=1)
    run(parser.parse_args())
