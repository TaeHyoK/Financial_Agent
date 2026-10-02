"""Offline valuation reference points: operating-profit multiple, denominator warnings, prior year end."""
import copy
import unittest
from pathlib import Path

import pandas as pd
from test_annual_context import financial_fixture
from Agent_Team.Competitor_Agent.peer_comparison import _missing_fields, _valuation_metrics
from Agent_Team.Financial_Agent.financial_index_calculator import calculate_financial_index
from Agent_Team.Strategy_Agent.decision import build_strategy_context_package
from Agent_Team.Strategy_Agent.packet import build_compact_strategy_packet, build_peer_pair_cards
from Agent_Team.YFinance_Agent.valuation import (
    EARNINGS_BASE_WARNING_LIMIT,
    PRIOR_YEAR_END_CURRENT_SHARES_LIMIT,
    build_valuation_snapshot,
)

METRICS = ["Revenue", "Operating Profit", "Net Income", "Total Equity"]


def dart_payload(scope="separate", net_income=None, metrics=METRICS):
    source = financial_fixture()
    source["collection_context"] = {"statement_scope": scope}
    if net_income is not None:
        item = source["4-2"]["tables"][0]["items_by_key"]["net_income"]
        item["numeric_values_by_period_key"].update({k: v * 1e8 for k, v in net_income.items()})
    if scope == "consolidated":
        for section_key, total, parent in (("4-2", "net_income", "parent_net_income"),
                                           ("4-1", "total_equity", "parent_equity")):
            table = source[section_key]["tables"][0]
            item = copy.deepcopy(table["items_by_key"][total])
            item["item_key"] = parent; item["display_name"] = parent
            item["numeric_values_by_period_key"] = {k: v / 2 for k, v in item["numeric_values_by_period_key"].items()}
            table["items_by_key"][parent] = item; table["item_order"].append(parent)
    dart = calculate_financial_index(source, metrics)
    dart["share_information"] = {"common_issued_shares": 1000000, "share_class": "common_only",
                                 "as_of_date": "2025-06-30", "source": {"receipt_date": "2025-08-14"}}
    return dart


def market_frame(start="2024-12-02"):
    dates = pd.bdate_range(start, "2025-10-30")
    close = [8000.0 if day <= pd.Timestamp("2024-12-31") else 10000.0 for day in dates]
    return pd.DataFrame({"date": dates, "stock_close": close, "stock_splits": 0.0})


def calculated(dart, frame=None):
    snapshot = build_valuation_snapshot(
        market_summary={"latest_snapshot": {"date": "2025-10-30", "stock_close": 10000}},
        dart_payload=dart, direct_valuation={"selected_date": "2025-10-31"}, market_frame=frame,
    )
    return snapshot


class OperatingProfitMultipleTests(unittest.TestCase):
    def test_ttm_operating_profit_uses_net_income_period_arithmetic(self):
        result = calculated(dart_payload())["calculated_from_close_and_dart"]
        self.assertAlmostEqual(result["inputs"]["ttm_operating_profit"]["value"], 16.1e8, delta=1)
        metric = result["metrics"]["price_to_operating_profit"]
        self.assertEqual(metric["status"], "ok")
        self.assertAlmostEqual(metric["value"], 1e10 / (16.1e8))
        self.assertAlmostEqual(result["metrics"]["trailing_pe"]["value"], 1e10 / (11e8))

    def test_missing_operating_profit_leaves_existing_status_unchanged(self):
        result = calculated(dart_payload(metrics=["Revenue", "Net Income", "Total Equity"]))
        block = result["calculated_from_close_and_dart"]
        self.assertEqual(block["status"], "available")
        self.assertEqual(block["metrics"]["price_to_operating_profit"]["status"], "insufficient_data")

    def test_non_positive_operating_profit_is_not_a_multiple(self):
        dart = dart_payload()
        dart["metrics_by_key"]["operating_profit"]["values_by_period"]["ttm"]["value"] = -1e8
        metric = calculated(dart)["calculated_from_close_and_dart"]["metrics"]["price_to_operating_profit"]
        self.assertIsNone(metric["value"])
        self.assertEqual(metric["reason"], "non_positive_ttm_operating_profit")


class EarningsBaseWarningTests(unittest.TestCase):
    def test_no_warning_for_steady_earnings(self):
        result = calculated(dart_payload())["calculated_from_close_and_dart"]
        self.assertEqual(result["earnings_base_warnings"], [])
        self.assertNotIn(EARNINGS_BASE_WARNING_LIMIT, result["data_limits"])

    def test_loss_in_prior_year_remainder_is_flagged(self):
        dart = dart_payload(net_income={"previous_fiscal_year": 3, "same_period_previous_year": 4})
        result = calculated(dart)["calculated_from_close_and_dart"]
        self.assertIn("ttm_includes_loss_half_year", result["earnings_base_warnings"])
        self.assertIn(EARNINGS_BASE_WARNING_LIMIT, result["data_limits"])

    def test_loss_in_current_window_is_flagged(self):
        dart = dart_payload(net_income={"current_fiscal_year": -1, "previous_fiscal_year": 20})
        result = calculated(dart)["calculated_from_close_and_dart"]
        self.assertIn("ttm_includes_loss_half_year", result["earnings_base_warnings"])
        self.assertNotIn("ttm_net_income_far_below_operating_profit", result["earnings_base_warnings"])

    def test_net_income_far_below_operating_profit_is_flagged(self):
        result = calculated(dart_payload(scope="consolidated"))["calculated_from_close_and_dart"]
        self.assertEqual(result["earnings_base_warnings"], ["ttm_net_income_far_below_operating_profit"])


class PriorYearEndReferenceTests(unittest.TestCase):
    def test_reference_uses_fiscal_year_end_close_and_annual_denominators(self):
        reference = calculated(dart_payload(), market_frame())["calculated_from_close_and_dart"]["prior_year_end_reference"]
        self.assertEqual(reference["status"], "available")
        self.assertEqual(reference["reference_date"], "2024-12-31")
        self.assertEqual(reference["share_count_basis"], "current_calculation_common_shares")
        self.assertIn(PRIOR_YEAR_END_CURRENT_SHARES_LIMIT, reference["data_limits"])
        metrics = reference["metrics"]
        self.assertAlmostEqual(metrics["trailing_pe"]["value"], 8e9 / 9e8)
        self.assertAlmostEqual(metrics["price_to_book"]["value"], 8e9 / 100e8)
        self.assertAlmostEqual(metrics["price_to_sales"]["value"], 8e9 / 121e8)
        self.assertAlmostEqual(metrics["price_to_operating_profit"]["value"], 8e9 / 12.1e8)

    def test_reference_uses_last_trading_day_before_period_end(self):
        frame = market_frame()
        frame = frame[frame["date"] != pd.Timestamp("2024-12-31")]
        reference = calculated(dart_payload(), frame)["calculated_from_close_and_dart"]["prior_year_end_reference"]
        self.assertEqual(reference["reference_date"], "2024-12-30")

    def test_price_history_not_reaching_period_end_is_unavailable(self):
        reference = calculated(dart_payload(), market_frame(start="2025-01-02"))[
            "calculated_from_close_and_dart"]["prior_year_end_reference"]
        self.assertEqual(reference["status"], "unavailable")
        self.assertEqual(reference["reason"], "no_close_on_or_before_fiscal_year_end")
        self.assertEqual(reference["metrics"], {})

    def test_annual_report_received_after_selected_date_is_unavailable(self):
        dart = dart_payload()
        dart["periods"]["previous_fiscal_year"]["receipt_date"] = "2025-11-01"
        reference = calculated(dart, market_frame())["calculated_from_close_and_dart"]["prior_year_end_reference"]
        self.assertEqual(reference["status"], "unavailable")
        self.assertEqual(reference["reason"], "fiscal_year_report_not_received_before_selected_date")

    def test_missing_price_history_is_unavailable(self):
        reference = calculated(dart_payload())["calculated_from_close_and_dart"]["prior_year_end_reference"]
        self.assertEqual(reference["reason"], "price_history_not_provided")


class PeerOperatingProfitMultipleTests(unittest.TestCase):
    def test_peer_row_exposes_operating_profit_multiple(self):
        snapshot = calculated(dart_payload())
        metrics = _valuation_metrics({"valuation_snapshot": snapshot})
        self.assertAlmostEqual(metrics["price_to_operating_profit"], 1e10 / 16.1e8)
        missing = _missing_fields({"valuation_metrics": _valuation_metrics({})})
        self.assertIn("valuation_metrics.price_to_operating_profit", missing)

    def test_peer_valuation_card_pairs_operating_profit_multiple(self):
        rows = [
            {"company_name": name, "peer_group": group,
             "valuation_metrics": {"calculated_as_of_date": "2025-10-30", "trailing_pe": 9.0,
                                   "price_to_book": 1.0, "price_to_sales": 0.8, "price_to_operating_profit": value}}
            for name, group, value in (("대상기업", "target", 6.0), ("비교기업", "domestic_peer", 7.5))
        ]
        cards = {card["card_key"]: card for card, _, _ in build_peer_pair_cards({"metrics": rows})}
        pairs = cards["peer.valuation"]["primary_observation"]["pairs"]
        pair = next(item for item in pairs if item["metric_key"] == "price_to_operating_profit")
        self.assertEqual((pair["target_value"], pair["peer_value"], pair["comparability"]), (6.0, 7.5, "comparable"))


class StrategyValuationCardTests(unittest.TestCase):
    def build(self, dart):
        bundle = {"target_company": {"company_name": "검증기업"},
                  "target_reports": {"yfinance": {"valuation_snapshot": calculated(dart, market_frame())}}}
        packet, _, _, _ = build_compact_strategy_packet(bundle)
        return packet, build_strategy_context_package(packet, input_bundle=bundle)

    def test_prior_year_end_card_is_company_history_comparison(self):
        packet, context = self.build(dart_payload())
        card = packet["cards"]["valuation.prior_year_end"]
        self.assertEqual(card["comparison_scope"], "company_history")
        self.assertEqual(card["eligibility"], "eligible")
        self.assertEqual(card["decision_use"], "factor_eligible")
        observation = card["primary_observation"]
        self.assertEqual((observation["current_as_of_date"], observation["reference_date"]), ("2025-10-30", "2024-12-31"))
        pair = next(item for item in observation["pairs"] if item["metric_key"] == "price_to_operating_profit")
        self.assertAlmostEqual(pair["current_value"], 1e10 / 16.1e8)
        self.assertAlmostEqual(pair["reference_value"], 8e9 / 12.1e8)
        self.assertTrue(any("한 시점의 참고값" in text for text in card["reader_limitations"]))
        self.assertIn("valuation.prior_year_end", packet["section_inputs"]["valuation_view"])
        self.assertIn("valuation.prior_year_end", context["coverage_dimensions"]["valuation"])
        self.assertIn("price_to_operating_profit", packet["cards"]["valuation.selected_date"]["primary_observation"]["metrics"])

    def test_selected_date_card_carries_earnings_base_warnings(self):
        packet, _ = self.build(dart_payload(scope="consolidated"))
        card = packet["cards"]["valuation.selected_date"]
        self.assertEqual(card["primary_observation"]["earnings_base_warnings"],
                         ["ttm_net_income_far_below_operating_profit"])
        self.assertTrue(any("영업이익 기준 배수" in text for text in card["reader_limitations"]))
        steady, _ = self.build(dart_payload())
        self.assertNotIn("earnings_base_warnings", steady["cards"]["valuation.selected_date"]["primary_observation"])
        self.assertNotIn("reader_limitations", steady["cards"]["valuation.selected_date"])


class DecisionPromptValuationRuleTests(unittest.TestCase):
    def test_prompt_allows_company_history_comparison_and_denominator_warning(self):
        prompt = (Path(__file__).resolve().parents[1]
                  / "src/Agent_Team/Strategy_Agent/prompts/decision_agent.md").read_text(encoding="utf-8")
        self.assertIn("comparison_scope가 company_history인 가치평가 카드", prompt)
        self.assertIn("분모 경고가 붙어 있으면 그 배수를 결정 근거로 쓰지 않고 영업이익 기준 배수를 먼저 본다", prompt)
        self.assertIn("같은 기간의 이익 변화와 주가 변화를 대비하는 것은 관측으로 쓸 수 있으나", prompt)
        self.assertNotIn("이익 증가만으로 가격 매력이 있다고 결론내리지 않는다", prompt)
        self.assertIn("입력에 없는 목표주가·미래 실적 숫자·기대수익률을 만들지 않는다", prompt)


if __name__ == "__main__":
    unittest.main()
