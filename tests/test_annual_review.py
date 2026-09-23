"""Checks for paths missed by the initial annual-context regression suite."""

import copy
from datetime import date
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from types import SimpleNamespace

import pandas as pd

from test_annual_context import ROOT, financial_fixture, market_fixture
from Agent_Team.Financial_Agent.financial_index_calculator import calculate_financial_index, load_metric_order
from Agent_Team.Financial_Agent.langgraph_flow import build_financial_trends, build_financial_secondary_context
from Agent_Team.Strategy_Agent.packet import _news_claim_card, _validate_card_semantics
from Agent_Team.YFinance_Agent.reporting import build_monthly_market_evidence_catalog, build_news_secondary_context
from Agent_Team.YFinance_Agent import reporting
from Agent_Team.YFinance_Agent.pipeline import build_full_dataset
from Agent_Team.News_Agent.analysis_agent import _compact_market_context
from orchestration.config import load_run_config
from orchestration.paths import resolve_run_paths
from orchestration.end_to_end_loop import AgentTeamOrchestrator, materialize_reused_dart_snapshot, materialize_reused_domain_snapshot, build_parser
from shared.subdata import financial_subdata
from shared.time_windows import monthly_windows
from Agent_Team.Writer_Agent.writer_handoff import _reader_observation


def write_json(path, payload):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False))


def annual_filing_fixture():
    source = financial_fixture()
    for section in source.values():
        for table in section["tables"]:
            table["periods"].pop("same_period_previous_year")
            table["periods"]["current_fiscal_year"].update(
                fiscal_year=2025, basis="FULL_YEAR", period_type="ANNUAL", period_end="2025-12-31"
            )
    return source


def source_snapshot(root):
    config_path = root / "config.json"
    write_json(config_path, {"company_code": "00000000", "corp_code": "00000000", "company_name": "검증기업",
                             "ticker": "000000.KS", "selected_date": "20251031", "date_range": "20241031-20251030"})
    config = load_run_config(config_path)
    source = resolve_run_paths(config, root / "source")
    destination = resolve_run_paths(config, root / "destination")
    source.ensure_directories()
    destination.ensure_directories()
    master = financial_fixture()
    master["collection_context"] = {"selected_date": "2025-10-31"}
    main = calculate_financial_index(master, load_metric_order(ROOT / "src/Agent_Team/Financial_Agent/financial_index.json"))
    old_lightweight = copy.deepcopy(main)
    for key in ("previous_fiscal_year_2", "previous_fiscal_year_3"):
        old_lightweight["periods"].pop(key)
    for key in ("operating_margin", "debt_ratio"):
        old_lightweight["metrics_by_key"].pop(key)
    for path, payload in [(source.dart_master, master), (source.dart_main, main), (source.dart_lightweight, old_lightweight)]:
        write_json(path, payload)
    return config, source, destination


def complete_domain_snapshot(root):
    config, source, destination = source_snapshot(root)
    frames, market_config = market_fixture()
    full = build_full_dataset(frames, pipeline_input=market_config)
    rows = json.loads(full.to_json(orient="records", date_format="iso"))
    for row in rows:
        row["date"] = row["date"][:10]
    write_json(source.yfinance_dir / "market_full_dataset.json", rows)
    full.to_csv(source.yfinance_dir / "market_full_dataset.csv", index=False)
    write_json(source.market_summary_dated, rows[-1:])
    write_json(source.market_summary, [{"date": "2025-08-01", "stock_close": -1}])
    write_json(source.valuation_snapshot, {"selected_date": "2025-10-31"})
    write_json(source.yfinance_dir / "manifest.json", {
        "selected_date": "2025-10-31", "date_range": {"start": "20241031", "end": "20251030"},
        "price_basis": {"returns_and_technical_indicators": "provider_split_adjusted_close_excluding_cash_dividends"},
    })
    periods = [{**window, "period_summary": "검증용 월별 요약이다.", "source_event_ids": [f"event_{index}"]}
               for index, window in enumerate(monthly_windows(date(2025, 10, 31)))]
    write_json(source.news_llm_period_summaries, {"model": "offline", "output": {"periods": periods}})
    write_json(source.news_context_export_week_dir / "llm_summary_request.json", {"input": []})
    from Agent_Team.News_Agent.collectors.candidate_preparation import CANDIDATE_POLICY
    from Agent_Team.News_Agent.collectors.google_news_collector import SNIPPET_POLICY
    event = {'event_id': '1', 'representative': {'title': '검증기업', 'snippet': '확보한 기사 발췌문',
             'time': '2025-10-15', 'snippet_metadata': {'snippet_policy': SNIPPET_POLICY}}}
    write_json(source.news_report_context, {"collect_date": "2025-10-30",
        'news_selection': {'raw_news_policy': 'monthly_selected_articles_v1', 'candidate_preparation': {'policy': CANDIDATE_POLICY}},
        'news_events_all': [event], 'news_events_weekly': [event], 'news_events_final': [event]})
    from shared.news_articles import build_article_packet
    write_json(source.news_articles, build_article_packet(json.loads(source.news_report_context.read_text())))
    from Agent_Team.News_Agent import context_export
    exports = context_export.build_context_exports(report_context_path=source.news_report_context,
        output_dir=source.news_context_export_week_dir, llm_model='offline')
    request = json.loads(Path(exports['llm_summary_request_path']).read_text())
    output = {'periods': [{'period': p['period'], 'issues': [
        {'summary': e['snippet'], 'source_event_ids': [e['event_id']]} for e in p['events']]}
        for p in context_export._load_llm_user_payload(request)['periods']]}
    context_export._attach_source_event_ids(output, request)
    write_json(source.news_llm_period_summaries, {'model': 'offline', 'output': output,
        'source_request_sha256': context_export.summary_request_hash(request)})
    write_json(source.run_config_copy, config.raw)
    write_json(source.run_status, {"status": "success", "pipeline_completed": True})
    return config, source, destination


class AnnualReviewTests(unittest.TestCase):
    def test_annual_primary_compares_to_previous_annual_filing(self):
        report = calculate_financial_index(annual_filing_fixture(), ["Revenue", "Revenue Growth", "Operating Profit"])
        trends = build_financial_trends(report)
        comparison = trends["current_vs_same_period"]
        self.assertEqual(comparison["previous_period"]["period_end"], "2024-12-31")
        self.assertEqual(comparison["previous_values"]["revenue"], 121e8)
        self.assertAlmostEqual(comparison["current_values"]["revenue_growth"], 60 / 121 - 1, places=5)

    def test_missing_annual_year_is_not_reported_as_yoy(self):
        source = annual_filing_fixture()
        for section in source.values():
            for table in section["tables"]:
                table["periods"].pop("previous_fiscal_year_2")
        report = calculate_financial_index(source, ["Revenue Growth"])
        table = financial_subdata(report)
        self.assertIsNone(table["evidence_catalog"]["DART_REVENUE_GROWTH"]["values_by_period"]["previous_fiscal_year"])

    def test_dart_reuse_rebuilds_three_year_lightweight_and_new_metrics(self):
        with tempfile.TemporaryDirectory() as temporary:
            config, source, destination = source_snapshot(Path(temporary))
            original = source.dart_lightweight.read_bytes()
            materialize_reused_dart_snapshot(run_config=config, source_root=source.output_root, destination_paths=destination)
            rebuilt = json.loads(destination.dart_lightweight.read_text())
            table = financial_subdata(rebuilt)
            self.assertEqual(len(table["periods"]), 5)
            self.assertEqual(table["evidence_catalog"]["DART_DEBT_RATIO"]["value"], 50)
            self.assertTrue((destination.financial_dir / "financial_subdata.json").exists())
            self.assertEqual(source.dart_lightweight.read_bytes(), original)

    def test_domain_reuse_rejects_wrong_analysis_period_before_copying(self):
        with tempfile.TemporaryDirectory() as temporary:
            config, source, destination = source_snapshot(Path(temporary))
            write_json(source.run_status, {"status": "success", "pipeline_completed": True})
            old_config = copy.deepcopy(config.raw)
            old_config["date_range"] = "20250801-20251030"
            write_json(source.run_config_copy, old_config)
            with self.assertRaisesRegex(ValueError, "date range|date_range|analysis period"):
                materialize_reused_domain_snapshot(run_config=config, source_root=source.output_root, destination_paths=destination)
            self.assertFalse(destination.dart_main.exists())

    def test_monthly_summary_date_is_not_promoted_to_event_date(self):
        summary = {"origin_type": "model_summarized", "source_type": "monthly_news_context", "source_date": "2025-10-30",
                   "period": "2025-09-30/2025-10-30", "period_start": "2025-09-30", "period_end": "2025-10-30",
                   "snippet": "기간 중 신제품과 공급계약에 관한 보도가 이어졌다.", "source_ref": "news_periods.example"}
        candidate = {"claim": "신제품 관련 보도가 이어졌다.", "source_key": "positive_signals", "evidence_ids": ["NEWS_PERIOD_X"],
                     "evidence_use": "strong", "event_status": "announced", "company_specificity": "direct",
                     "materiality_status": "observed", "financial_link_status": "not_observed", "limitations": []}
        card, _, _ = _news_claim_card(candidate, {"NEWS_PERIOD_X": summary})
        _validate_card_semantics(card)
        self.assertIsNone(card["primary_observation"]["event_date"])
        self.assertEqual(card["primary_observation"]["source_periods"], ["2025-09-30/2025-10-30"])
        display = _reader_observation(card)
        self.assertIn("요약 기간", display)
        self.assertNotIn("발생일", display)
        raw = {"origin_type": "raw_source", "source_date": "2025-10-04", "source_ref": "news_events.e1", "snippet": "신제품이 발표됐다."}
        candidate["evidence_ids"].append("NEWS_RAW_X")
        card, _, _ = _news_claim_card(candidate, {"NEWS_PERIOD_X": summary, "NEWS_RAW_X": raw})
        self.assertEqual(card["primary_observation"]["event_date"], "2025-10-04")

    def test_monthly_market_catalog_explicitly_preserves_empty_periods(self):
        frame = pd.DataFrame({"date": pd.to_datetime(["2025-10-28", "2025-10-30"]), "stock_close": [100, 110], "kospi_close": [2000, 2100]})
        frame.attrs["selected_date"] = "2025-10-31"
        catalog = build_monthly_market_evidence_catalog(frame)
        self.assertEqual(len(catalog), 12)
        missing = [row for row in catalog.values() if row["status"] == "unavailable"]
        self.assertEqual(len(missing), 11)
        self.assertTrue(all(row["value"]["stock_return"] is None for row in missing))

    def test_matching_domain_reuse_preserves_cutoff_and_rebuilds_common_subdata(self):
        with tempfile.TemporaryDirectory() as temporary:
            config, source, destination = complete_domain_snapshot(Path(temporary))
            result = materialize_reused_domain_snapshot(
                run_config=config, source_root=source.output_root, destination_paths=destination,
                expected_news_model="offline",
            )
            self.assertEqual(result["status"], "materialized")
            self.assertEqual((destination.yfinance_dir / "manifest.json").read_bytes(),
                             (source.yfinance_dir / "manifest.json").read_bytes())
            self.assertEqual(destination.market_summary.read_bytes(), source.market_summary_dated.read_bytes())
            financial = json.loads(destination.dart_lightweight.read_text())
            self.assertEqual(len(financial_subdata(financial)["periods"]), 5)
            market = json.loads(destination.market_summary.read_text())
            news = json.loads(destination.news_llm_period_summaries.read_text())
            received = build_financial_secondary_context({"news_weekly_summaries": news, "yfinance_market_summary": market})
            self.assertEqual(received["news"], build_news_secondary_context(news, source_path=destination.news_llm_period_summaries))
            self.assertEqual(received["news"]["period_count"], 12)
            self.assertEqual(received["market"], _compact_market_context(market))

    def test_domain_reuse_rejects_pre_selection_snippet_legacy_source(self):
        with tempfile.TemporaryDirectory() as temporary:
            config, source, destination = complete_domain_snapshot(Path(temporary))
            write_json(source.news_report_context, {'collect_date': '2025-10-30'})
            with self.assertRaisesRegex(ValueError, 'all candidate snippets'):
                materialize_reused_domain_snapshot(run_config=config, source_root=source.output_root,
                                                  destination_paths=destination, expected_news_model='offline')
            self.assertFalse(destination.dart_main.exists())

    def test_report_generation_excludes_future_rows_before_llm_input(self):
        with tempfile.TemporaryDirectory() as temporary:
            _, source, _ = complete_domain_snapshot(Path(temporary))
            market_json = source.yfinance_dir / "market_full_dataset.json"
            rows = json.loads(market_json.read_text())
            rows.append({**rows[-1], "date": "2025-11-01", "stock_close": 99999})
            write_json(market_json, rows)
            with patch.object(reporting, "generate_agent_json_report_with_llm", return_value={}) as llm, \
                    patch.object(reporting, "render_agent_markdown_report", return_value="offline fixture"):
                result = reporting.generate_analyst_report(
                    market_json=market_json, dart_json=source.dart_lightweight, news_json=source.news_llm_period_summaries,
                    valuation_json=source.valuation_snapshot, report_json=Path(temporary) / "report.json",
                    report_md=Path(temporary) / "report.md", primary_data_only=True,
                )
            payload = llm.call_args.args[0]
            self.assertEqual(payload["market_summary"]["latest_snapshot"]["date"], "2025-10-30")
            self.assertTrue(all(str(row.get("source_date") or "") < "2025-10-31"
                                for row in payload["primary_evidence_catalog"].values()))
            self.assertEqual(json.loads(result.json.read_text())["selected_date"], "2025-10-31")

    def test_reuse_rejects_filing_on_information_cutoff(self):
        with tempfile.TemporaryDirectory() as temporary:
            config, source, destination = source_snapshot(Path(temporary))
            master = json.loads(source.dart_master.read_text())
            master["4-2"]["tables"][0]["periods"]["current_fiscal_year"]["receipt_date"] = "2025-10-31"
            write_json(source.dart_master, master)
            with self.assertRaisesRegex(ValueError, "information cutoff"):
                materialize_reused_dart_snapshot(run_config=config, source_root=source.output_root, destination_paths=destination)
            self.assertFalse(destination.dart_master.exists())

    def test_history_change_invalidates_analysis_even_when_latest_summary_is_identical(self):
        with tempfile.TemporaryDirectory() as temporary:
            _, source, _ = complete_domain_snapshot(Path(temporary))
            loop = AgentTeamOrchestrator.__new__(AgentTeamOrchestrator)
            loop.paths = source
            loop.args = SimpleNamespace(env_file=Path(temporary) / "unused.env")
            fingerprint = lambda: loop._step_fingerprint(
                step_name="yfinance_report", command=["python"], dependencies=("yfinance_layer_1",), outputs={},
            )
            before = fingerprint()
            summary_before = source.market_summary_dated.read_bytes()
            path = source.yfinance_dir / "market_full_dataset.json"
            rows = json.loads(path.read_text())
            rows[0]["stock_close"] += 1
            write_json(path, rows)
            self.assertNotEqual(before, fingerprint())
            self.assertEqual(summary_before, source.market_summary_dated.read_bytes())

    def test_orchestrator_passes_current_news_capacity_to_actual_cli(self):
        from Agent_Team.News_Agent.cli import build_parser as news_parser
        with tempfile.TemporaryDirectory() as temporary:
            config, source, _ = source_snapshot(Path(temporary))
            loop = AgentTeamOrchestrator.__new__(AgentTeamOrchestrator)
            loop.paths, loop.run_config = source, config
            loop.args = build_parser().parse_args([])
            for override, expected in ((None, 20), (20, 20)):
                loop.args.news_total_max_results = override
                command = loop._news_phase_command("analysis")
                parsed = news_parser().parse_args(command[3:])
                self.assertEqual(parsed.max_raw_events_per_period, expected)
                for phase in ("collect", "export", "llm"):
                    self.assertNotIn("--max-raw-events-per-period", loop._news_phase_command(phase))


if __name__ == "__main__":
    unittest.main()
