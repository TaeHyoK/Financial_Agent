"""Report layout, chart placement and post-Writer evidence display formatting."""

from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import matplotlib

matplotlib.use("Agg")
import pandas as pd

from Agent_Team.Visualization_Agent import chart_builders
from Agent_Team.Writer_Agent.formatted_html_renderer import (
    SIDEBAR_COLUMN_SECTION_COUNT,
    _place_report_charts,
    build_complete_html,
)
from Agent_Team.Writer_Agent.html_report_writer import _evidence_observation_text


def _payload(section_cards: dict[str, list[str]], charts: dict[str, list[str]]) -> dict:
    return {
        "sections": {
            key: {"item": {"card_keys": keys}} for key, keys in section_cards.items()
        },
        "report_charts": [{"chart_key": key, "src": f"{key}.png"} for key in charts],
        "chart_selection_details": [
            {"chart_key": key, "basis_card_keys": basis} for key, basis in charts.items()
        ],
    }


class ChartPlacementTests(unittest.TestCase):
    def test_lower_overlap_chart_moves_to_its_next_best_section(self):
        payload = _payload(
            {
                "investment_call_thesis": ["a", "b", "c"],
                "business_market_context": ["c"],
            },
            {"weak": ["c"], "strong": ["a", "b"]},
        )
        placed, unplaced = _place_report_charts(payload)
        self.assertEqual([c["chart_key"] for c in placed["investment_call_thesis"]], ["strong"])
        self.assertEqual([c["chart_key"] for c in placed["business_market_context"]], ["weak"])
        self.assertEqual(unplaced, [])

    def test_chart_without_a_free_overlapping_section_goes_to_the_end_block(self):
        payload = _payload(
            {"investment_call_thesis": ["a"], "data_limits": ["a"]},
            {"first": ["a"], "second": ["a"], "none": ["z"]},
        )
        placed, unplaced = _place_report_charts(payload)
        self.assertEqual([c["chart_key"] for c in placed["investment_call_thesis"]], ["first"])
        self.assertNotIn("data_limits", placed)
        self.assertEqual([c["chart_key"] for c in unplaced], ["second", "none"])


class LayoutTests(unittest.TestCase):
    def test_sections_after_the_lead_span_both_columns(self):
        html = build_complete_html({"metadata": {"company_name": "기업"}, "sections": {}})
        main = html.index('<div class="main-column">')
        flow = html.index('<div class="full-width-flow">')
        sidebar = html.index('<div class="visual-sidebar">')
        lead_ids = ["investment-call-thesis"][:SIDEBAR_COLUMN_SECTION_COUNT]
        for section_id in lead_ids:
            self.assertTrue(main < html.index(f'id="{section_id}"') < flow)
        self.assertTrue(flow < html.index('id="business-market-context"') < sidebar)
        self.assertIn("grid-column: 1 / -1", html)


class EvidenceDisplayTests(unittest.TestCase):
    def test_relative_performance_shows_the_fixed_short_set(self):
        cards = {
            "market.absolute_trend": {
                "domain": "market",
                "axis": "absolute_trend",
                "primary_observation": {"metrics": {"stock_return_12m": 0.0134}},
            }
        }
        card = {
            "domain": "market",
            "axis": "relative_performance",
            "primary_observation": {
                "as_of_date": "2025-01-31",
                "benchmark_name": "KOSPI",
                "metrics": {
                    "kospi_return_12m": 0.554,
                    "kospi_return_1m": 0.1,
                    "stock_excess_return_12m": -0.5405,
                    "stock_excess_return_3m": -0.1696,
                    "stock_relative_strength_60": -0.01,
                },
            },
        }
        text = _evidence_observation_text("market.relative_performance", card, cards)
        self.assertEqual(
            text.splitlines(),
            [
                "기준일: 2025-01-31",
                "12개월 수익률: 1.34%",
                "KOSPI 12개월 수익률: 55.40%",
                "KOSPI 대비 12개월 초과수익률: -54.05%p",
                "KOSPI 대비 3개월 초과수익률: -16.96%p",
            ],
        )

    def test_prior_year_end_pairs_are_formatted_with_dates_and_close_change(self):
        card = {
            "primary_observation": {
                "current_as_of_date": "2025-01-31",
                "reference_date": "2024-12-30",
                "close": {"current": 120.0, "reference": 100.0, "change_rate": 0.2},
                "pairs": [
                    {
                        "metric_key": "price_to_operating_profit",
                        "current_value": 20.71,
                        "reference_value": 27.8,
                        "comparability": "comparable",
                    },
                    {"metric_key": "unknown_metric", "current_value": 1.0, "reference_value": 2.0},
                ],
                "current_earnings_base_warnings": ["ttm_includes_loss_half_year", "new_code"],
            }
        }
        lines = _evidence_observation_text("valuation.prior_year_end", card).splitlines()
        self.assertEqual(lines[0], "P/영업이익: 27.80배(2024-12-30) → 20.71배(2025-01-31)")
        self.assertEqual(lines[1], "종가: 100원(2024-12-30) → 120원(2025-01-31) (+20.0%)")
        self.assertTrue(lines[2].startswith("주: 최근 4개 분기 이익에 손실을 낸 반기가 포함됨"))
        self.assertNotIn("new_code", lines[2])
        self.assertEqual(len(lines), 3)


class ChartStyleTests(unittest.TestCase):
    def test_charts_are_drawn_at_report_size(self):
        frame = pd.DataFrame(
            {
                "period_key": ["same_period_previous_year", "current_fiscal_year"],
                "period_label": ["2024년 반기 누적", "2025년 반기 누적"],
                "period_type": ["HALF", "HALF"],
                "period_end": ["2024-06-30", "2025-06-30"],
                "basis": ["YTD", "YTD"],
                "contribution_margin_pct": [10.0, 12.0],
                "sga_margin_pct": [5.0, 6.0],
            }
        )
        saved = []
        with tempfile.TemporaryDirectory() as tmp, patch.object(
            chart_builders, "_save_figure", side_effect=lambda fig, *_: saved.append(fig)
        ):
            out = Path(tmp)
            chart_builders.build_fundamental_margin_trend_chart(frame, out / "c.pdf", out / "c.png", "기업")
        width, height = saved[0].get_size_inches()
        self.assertAlmostEqual(width * 25.4, 150.0, places=3)
        self.assertAlmostEqual(width / height, 16 / 7, places=3)
        # Single-basis periods are named by the tick labels, so no footnote is drawn.
        self.assertEqual(saved[0].texts, [])


if __name__ == "__main__":
    unittest.main()
