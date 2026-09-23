"""Offline regression checks for dates, financial/market math and report contracts."""

from __future__ import annotations

import copy
import json
from pathlib import Path
import sys
import tempfile
import unittest
from datetime import date, timedelta

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "src"), str(ROOT / "src/Agent_Team/YFinance_Agent")]

from shared.time_windows import monthly_windows
from shared.subdata import financial_subdata, market_subdata, news_subdata
from shared.evidence_cards import card_content_sha256
from orchestration.company_resolver import resolve_news_date_range
from Agent_Team.YFinance_Agent.pipeline import PipelineInput, build_full_dataset, build_summary_dataset
from Agent_Team.YFinance_Agent.reporting import (
    build_daily_market_evidence_catalog, build_monthly_market_evidence_catalog,
    build_dart_secondary_context, build_news_secondary_context,
)
from Agent_Team.News_Agent import context_export, analysis_agent
from Agent_Team.Financial_Agent.financial_index_calculator import calculate_financial_index
from Agent_Team.Strategy_Agent.decision import (
    align_strategy_decision_evidence_plan, validate_strategy_decision,
    strategy_decision_response_format,
)
from Agent_Team.Writer_Agent.writer_handoff import build_writer_editorial_packet
from Agent_Team.Writer_Agent.html_report_writer import normalize_report_payload
from Agent_Team.Writer_Agent.formatted_html_renderer import build_complete_html
from Agent_Team.Writer_Agent.html_report_spec import REPORT_SECTIONS
from Agent_Team.Writer_Agent.html_report_validator import validate_html_report


def financial_fixture():
    periods = {}
    for key, year, basis, end, kind in [
        ("previous_fiscal_year_3", 2022, "FULL_YEAR", "12-31", "ANNUAL"),
        ("previous_fiscal_year_2", 2023, "FULL_YEAR", "12-31", "ANNUAL"),
        ("previous_fiscal_year", 2024, "FULL_YEAR", "12-31", "ANNUAL"),
        ("same_period_previous_year", 2024, "YTD", "06-30", "HALF"),
        ("current_fiscal_year", 2025, "YTD", "06-30", "HALF"),
    ]:
        periods[key] = dict(fiscal_year=year, basis=basis, period_end=f"{year}-{end}",
                            period_type=kind, receipt_date="2025-08-14", label=f"{year} {kind}")
    sections = {}
    for section, rows in {
        "4-2": {"revenue": [100, 110, 121, 50, 60], "operating_profit": [10, 11, 12.1, 5, 9], "net_income": [7, 8, 9, 4, 6]},
        "4-1": {"total_equity": [80, 90, 100, 95, 110], "total_liabilities": [40, 45, 50, 47.5, 55]},
        "4-4": {"cash_flows_from_operating_activities": [9, 10, 11, 4, 7]},
    }.items():
        items = {key: {"item_key": key, "display_name": key,
                       "numeric_values_by_period_key": dict(zip(periods, [x * 1e8 for x in values]))}
                 for key, values in rows.items()}
        sections[section] = {"tables": [{"periods": copy.deepcopy(periods), "items_by_key": items, "item_order": list(items)}]}
    return sections


def market_fixture():
    index = pd.bdate_range("2023-10-01", "2025-11-05")
    price = pd.Series(np.linspace(80, 150, len(index)), index=index)
    def frame(close):
        return pd.DataFrame({"close": close, "adj_close": close * .9, "open": close,
                             "high": close, "low": close, "volume": 1000.0}, index=index)
    frames = {"stock": frame(price), "kospi": frame(price * 20), "fx_usdkrw": frame(price * 10)}
    config = PipelineInput("000000.KS", "검증기업", date(2024, 10, 31), date(2025, 10, 30),
                           date(2025, 10, 31), Path("fixture.json"))
    return frames, config


def strategy_fixture(opinion="Hold"):
    key = "financial.same_period_trend"
    card = {"card_key": key, "domain": "financial", "card_type": "financial", "label": "실적 추세",
            "primary_observation": {"revenue_growth": .2}, "evidence_family": "financial",
            "observation_basis": "same_period", "comparison_scope": "same_company",
            "decision_use": "primary", "reader_limitations": []}
    packet = {"target_company": {"company_name": "검증기업", "run_key": "검증기업_20251031",
                                "as_of_date": "2025-10-31", "ticker": "000000.KS"},
              "cards": {key: card}, "limitation_requirements": [], "reader_limitations": []}
    dimensions = {name: ([key] if name == "performance" else []) for name in
                  ("performance", "cash_flow", "financial_position", "market", "valuation", "events", "peer")}
    context = {"evidence_cards": {key: card}, "coverage_dimensions": dimensions}
    linked = lambda text: {"text": text, "card_keys": [key]}
    decision = {
        "decision_version": "strategy_decision_output", "schema_revision": "12m_v3",
        "evidence_plan": {
            "decision_basis_cards": [{"card_key": key, "importance": "high",
                                      "investment_implication": "실적 개선의 지속성을 확인할 필요가 있다.", "target_peer_context": None}],
            "report_context_cards": [],
            "coverage_assessment": {name: {"status": "used" if keys else "unavailable", "card_keys": keys,
                                          "reason": "제공된 자료의 범위다."} for name, keys in dimensions.items()},
        },
        "strategy_brief": {"headline": "실적 개선의 지속성이 관건", "recommendation": opinion, "horizon": "12개월",
                           "thesis": linked("실적 개선과 지속성의 불확실성을 함께 고려한다."),
                           "earnings_review": linked("매출은 전년 동기보다 증가했다."),
                           "outlook": linked("매출 성장세가 이어진다면 향후 실적을 뒷받침할 수 있다."),
                           "price_assessment": linked("가격 자료가 없어 실적과 가격을 직접 비교하기 어렵다."),
                           "counterview": linked("성장세가 둔화하면 실적 개선의 의미가 약해질 수 있다."),
                           "decision_rationale": linked("성장은 긍정적이나 지속성을 뒷받침하는 근거의 범위가 제한되어 판단의 강도를 조절한다."),
                           "decision_limitation": linked("이 검증 자료는 실제 투자 분석 결과가 아니다."),
                           "evidence_sufficiency": "low", "decision_confidence": "low"},
        "report_insights": [], "key_risks": [{"risk_title": "성장 둔화", "risk": "매출 성장의 지속성이 불확실하다.",
                                              "current_implication": "성장 지속을 전제로 한 해석에 주의가 필요하다.", "card_keys": [key]}],
    }
    provenance = {"cards": {key: {"strategy_card_sha256": card_content_sha256(card), "source_evidence_ids": [],
                                 "source_paths": [], "source_files": []}}}
    return packet, context, decision, provenance


class AnnualContextTests(unittest.TestCase):
    def test_calendar_windows_include_leap_day_and_have_no_gaps(self):
        for end in (date(2025, 10, 31), date(2024, 2, 29), date(2025, 3, 1), date(2025, 1, 1)):
            start, last = resolve_news_date_range(end, "1y")
            windows = monthly_windows(end)
            self.assertEqual(len(windows), 12)
            self.assertEqual(windows[0]["period_start"], start.isoformat())
            self.assertEqual(windows[-1]["period_end"], last.isoformat())
            for prev, curr in zip(windows, windows[1:]):
                self.assertEqual(date.fromisoformat(prev["period_end"]) + timedelta(days=1), date.fromisoformat(curr["period_start"]))

    def test_annual_financial_table_and_ytd_comparisons(self):
        result = calculate_financial_index(financial_fixture(), ["Revenue", "Revenue Growth", "Operating Profit", "Operating Margin",
                                                               "Net Income", "Operating Cash Flow", "Total Equity", "Debt Ratio"])
        table = financial_subdata(result)
        self.assertEqual(len(table["periods"]), 5)
        self.assertEqual(len(table["evidence_catalog"]), 8)
        self.assertEqual(table["evidence_catalog"]["DART_REVENUE"]["value"], 60)
        self.assertEqual(table["evidence_catalog"]["DART_REVENUE_GROWTH"]["value"], 20)
        self.assertEqual(table["evidence_catalog"]["DART_OPERATING_MARGIN"]["value"], 15)
        self.assertEqual(table["evidence_catalog"]["DART_DEBT_RATIO"]["value"], 50)
        self.assertEqual(analysis_agent._compact_financial_context(result), build_dart_secondary_context(result))

    def test_nonpositive_equity_is_missing_not_a_cheap_debt_ratio(self):
        fixture = financial_fixture()
        fixture["4-1"]["tables"][0]["items_by_key"]["total_equity"]["numeric_values_by_period_key"]["current_fiscal_year"] = -1
        table = financial_subdata(calculate_financial_index(fixture, ["Debt Ratio"]))
        self.assertIsNone(table["evidence_catalog"]["DART_DEBT_RATIO"]["value"])
        self.assertEqual(table["evidence_catalog"]["DART_DEBT_RATIO"]["missing_reasons"]["current_fiscal_year"], "nonpositive_equity")
        self.assertEqual(table["evidence_catalog"]["DART_REVENUE"]["unit"], "억원")

    def test_market_windows_use_warmup_and_exclude_future(self):
        frames, config = market_fixture()
        full = build_full_dataset(frames, pipeline_input=config)
        summary = build_summary_dataset(full, selected_date=config.selected_date).frame.iloc[0]
        baseline = frames["stock"].loc[:pd.Timestamp(summary["date"]) - pd.DateOffset(months=12)].iloc[-1]["close"]
        self.assertAlmostEqual(summary["stock_return_12m"], summary["stock_close"] / baseline - 1)
        self.assertAlmostEqual(summary["stock_excess_return_12m"], 0)
        self.assertEqual(summary["stock_max_drawdown_1y"], 0)
        self.assertEqual(summary["stock_volume_ratio_5_60"], 1)
        changed = copy.deepcopy(frames)
        changed["stock"].loc["2025-10-31":, "close"] = 1e9
        pd.testing.assert_frame_equal(full, build_full_dataset(changed, pipeline_input=config))
        table = market_subdata([summary.to_dict()])
        self.assertEqual(table, analysis_agent._compact_market_context([summary.to_dict()]))
        self.assertEqual(len(table["evidence_catalog"]), 16)
        full["date"] = pd.to_datetime(full["date"])
        full.attrs["selected_date"] = config.selected_date.isoformat()
        self.assertEqual(len(build_daily_market_evidence_catalog(full)), 20)
        months = build_monthly_market_evidence_catalog(full)
        self.assertEqual(len(months), 12)
        self.assertEqual([r["period"] for r in months.values()], [w["period"] for w in monthly_windows(config.selected_date)])

    def test_short_history_does_not_fabricate_annual_return(self):
        from Agent_Team.YFinance_Agent.annual_features import annual_features
        frames, _ = market_fixture()
        features = annual_features(frames["stock"].tail(60))
        self.assertTrue(features["return_12m"].isna().all())
        self.assertTrue(features["volatility_1y"].isna().all())

    def test_drawdown_volatility_and_volume_calculation(self):
        from Agent_Team.YFinance_Agent.annual_features import annual_features
        index = pd.date_range("2024-01-01", "2025-01-01")
        prices = pd.Series(100.0, index=index)
        prices.iloc[100:200] = 200.0
        volume = pd.Series(100.0, index=index)
        volume.iloc[-5:] = 200.0
        frame = pd.DataFrame({"close": prices, "volume": volume})
        last = annual_features(frame).iloc[-1]
        self.assertEqual(last["max_drawdown_1y"], -.5)
        self.assertEqual(last["current_drawdown_1y"], -.5)
        self.assertAlmostEqual(last["volatility_1y"], prices.pct_change().dropna().std(ddof=1) * np.sqrt(252))
        self.assertAlmostEqual(last["volume_ratio_5_60"], 200 / ((55 * 100 + 5 * 200) / 60))

    def test_market_annual_values_reach_strategy_cards_and_reader_labels(self):
        from Agent_Team.Strategy_Agent.packet import _market_cards
        from Agent_Team.YFinance_Agent.reporting import build_market_summary, build_market_primary_evidence_catalog
        from Agent_Team.Writer_Agent.writer_handoff import _reader_observation
        frames, config = market_fixture()
        full = build_full_dataset(frames, pipeline_input=config)
        full["date"] = pd.to_datetime(full["date"])
        catalog = build_market_primary_evidence_catalog(build_market_summary(full))
        cards = {card["card_key"]: card for card, _, _ in _market_cards({"primary_evidence_catalog": catalog})}
        self.assertIn("stock_return_12m", cards["market.absolute_trend"]["primary_observation"]["metrics"])
        self.assertIn("stock_excess_return_12m", cards["market.relative_performance"]["primary_observation"]["metrics"])
        self.assertIn("stock_max_drawdown_1y", cards["market.momentum_volume"]["primary_observation"]["metrics"])
        self.assertIn("12개월 수익률", _reader_observation(cards["market.absolute_trend"]))

    def test_new_cli_defaults(self):
        from orchestration.full_report_pipeline import build_parser
        from Agent_Team.Strategy_Agent.agent import resolve_decision_horizon_profile
        args = build_parser().parse_args(["--company-name", "검증기업", "--selected-date", "20251031"])
        self.assertEqual(args.news_window, "1y")
        self.assertEqual(resolve_decision_horizon_profile(args.decision_horizon_profile)["horizon"], "12개월")

    def test_news_export_keeps_twelve_bins_and_source_lineage(self):
        event = {"event_id": "e1", "mention_count": 1, "representative": {"time": "2024-11-01", "title": "검증 기사"}}
        future = copy.deepcopy(event)
        future.update(event_id="future", representative={"time": "2025-10-31", "title": "미래 기사"})
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "report_context.json"
            source.write_text(json.dumps({"collect_date": "2025-10-30", "company": {"company_name": "검증기업"},
                                          "news_events_weekly": [event, future], "news_events_final": [event, future]}))
            context_export.build_context_exports(report_context_path=source, output_dir=root / "month")
            request = json.loads((root / "month/llm_summary_request.json").read_text())
            periods = context_export._load_llm_user_payload(request)["periods"]
            self.assertEqual(len(periods), 12)
            self.assertEqual(sum(len(p["events"]) for p in periods), 1)
            output = {"periods": [{"period": p["period"], "issues": [
                {"summary": "요약 검증 문장이다.", "source_event_ids": [e['event_id']]}
                for e in p['events']]} for p in periods]}
            context_export._attach_source_event_ids(output, request)
            self.assertEqual(len(output["periods"][0]["source_event_ids"]), 1)
            self.assertEqual(output["periods"][-1]["period_end"], "2025-10-30")
            packet = {"output": output}
            self.assertEqual(news_subdata(packet), build_news_secondary_context(packet, source_path=source))
            self.assertEqual(next(iter(news_subdata(packet)['evidence_catalog'].values()))['source_event_ids'], ['e1'])
            (root / "month/llm_period_summaries.json").write_text(json.dumps(packet))
            for name in ("dart.json", "market.json"):
                (root / name).write_text("{}")
            paths = analysis_agent.AnalysisPaths(
                context_export_dir=root / "month", context_manifest_path=root / "month/context_export_manifest.json",
                period_summaries_path=root / "month/llm_period_summaries.json", summary_prompt_input_path=root / "month/summary_prompt_input.json",
                recent_raw_path=root / "month/recent_raw_input.json", dart_lightweight_path=root / "dart.json",
                market_summary_path=root / "market.json", output_dir=root, input_payload_path=root / "input.json",
                llm_request_path=root / "request.json", handoff_path=root / "handoff.json", evidence_map_path=root / "evidence.json",
            )
            analysis_input = analysis_agent.build_analysis_input_payload(
                company_name="검증기업", ticker=None, corp_code=None, as_of_date=date(2025, 10, 31),
                paths=paths, max_raw_events_per_period=20,
            )
            llm_input = json.loads(analysis_agent.build_llm_request(input_payload=analysis_input, model="offline")["input"][1]["content"])["input_payload"]
            self.assertEqual(len(llm_input["최근 1년 월별 요약 12개"]), 12)
            self.assertEqual(len(llm_input["기업 관련 뉴스 상위 20건"]), 1)
            self.assertEqual(json.dumps(llm_input, ensure_ascii=False).count("요약 검증 문장이다."), 1)
            self.assertEqual(len([k for k in analysis_input["evidence_map"] if k.startswith("NEWS_PERIOD_")]), 12)
            with self.assertRaises(ValueError):
                context_export._attach_source_event_ids({"periods": output["periods"][:-1]}, request)

    def test_strategy_and_writer_preserve_each_explicit_opinion(self):
        for opinion, label in [("Buy", "매수"), ("Hold", "중립"), ("Sell", "매도")]:
            packet, context, decision, provenance = strategy_fixture(opinion)
            normalized = align_strategy_decision_evidence_plan(decision, context=context)
            validate_strategy_decision(normalized, context=context, required_horizon="12개월")
            handoff, _ = build_writer_editorial_packet(strategy_packet=packet, strategy_decision=normalized, strategy_provenance=provenance)
            sections = {}
            for section in REPORT_SECTIONS:
                keys = handoff["required_card_keys_by_component"][section["key"]]
                text = "제공된 자료 범위에서 실적 개선의 지속성을 검토한다."
                sections[section["key"]] = {key: ({"paragraphs": [text], "bullets": [], "card_keys": keys,
                                                      "_claim_units": [{"claim": text, "card_keys": keys, "limitation_categories": []}]}
                                                     if kind == "text" else {"columns": [], "rows": [], "card_keys": keys})
                                                 for key, _, kind in section["items"]}
            sections["key_evidence_table"]["evidence_table"]["_display_labels"] = [
                {"card_key": key, "display_label": "실적 성장의 지속성"} for key in handoff["required_card_keys_by_component"]["key_evidence_table"]
            ]
            report = normalize_report_payload({"sections": sections}, writer_handoff=handoff)
            html = build_complete_html(report)
            self.assertIn(f'class="investment-opinion">{label}</strong>', html)
            self.assertIn(f'name="investment-recommendation" content="{opinion}"', html)
            self.assertIn("실적 개선의 지속성이 관건", html)
            validation = validate_html_report(report_payload=report, html_content=html, writer_handoff=handoff)
            self.assertFalse(validation.get("blocking_failures"), validation)
            self.assertEqual(validation["status"], "pass", validation)

    def test_old_strategy_cache_and_wrong_horizon_are_rejected(self):
        _, context, decision, _ = strategy_fixture()
        bad = copy.deepcopy(decision)
        bad.pop("schema_revision")
        with self.assertRaises(ValueError):
            validate_strategy_decision(bad, context=context)
        with self.assertRaises(ValueError):
            validate_strategy_decision(decision, context=context, required_horizon="1개월")
        schema = strategy_decision_response_format(context, required_horizon="12개월")["json_schema"]["schema"]
        self.assertNotIn("maxItems", schema["properties"]["key_risks"])
        self.assertIn("recommendation", schema["properties"]["strategy_brief"]["required"])


if __name__ == "__main__":
    unittest.main()
