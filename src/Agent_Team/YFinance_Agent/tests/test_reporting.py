"""Tests for YFinance-centered analyst report generation."""

from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from reporting import generate_analyst_report


class ReportingTests(unittest.TestCase):
    def test_generate_analyst_report_writes_markdown_and_json(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            market_json = root / "market.json"
            dart_json = root / "dart.json"
            news_json = root / "SK바이오팜_20251031" / "news.json"
            report_md = root / "report.md"
            report_json = root / "report.json"

            news_json.parent.mkdir(parents=True)
            _write_json(market_json, _market_rows())
            _write_json(dart_json, _dart_payload())
            _write_json(news_json, _news_payload())

            with patch("reporting.generate_agent_json_report_with_llm", return_value=_agent_report()):
                paths = generate_analyst_report(
                    market_json=market_json,
                    dart_json=dart_json,
                    news_json=news_json,
                    report_md=report_md,
                    report_json=report_json,
                )

            self.assertEqual(paths.markdown, report_md)
            self.assertTrue(report_md.exists())
            self.assertTrue(report_json.exists())

            markdown = report_md.read_text(encoding="utf-8")
            self.assertIn("SK바이오팜 Y-Finance Agent Report", markdown)
            self.assertIn("Main View", markdown)
            self.assertIn("Cross Data Reconciliation", markdown)
            self.assertIn("실적 성장", markdown)

            payload = json.loads(report_json.read_text(encoding="utf-8"))
            self.assertEqual(payload["agent_name"], "Y-Finance Agent")
            self.assertEqual(payload["target_company"], "SK바이오팜")
            self.assertEqual(payload["as_of_date"], "2025-10-31")
            self.assertIn("main_view", payload)
            self.assertIn("time_horizon_view", payload)
            self.assertIn("detailed_analysis", payload)
            self.assertIn("cross_data_reconciliation", payload)
            self.assertIn("news_plus_market", payload["cross_data_reconciliation"])
            self.assertIn("dart_plus_market", payload["cross_data_reconciliation"])
            self.assertIn("news_plus_dart_plus_market", payload["cross_data_reconciliation"])
            self.assertNotIn("score", json.dumps(payload, ensure_ascii=False))


def _write_json(path: Path, payload) -> None:
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")


def _market_rows() -> list[dict[str, float | str]]:
    rows = []
    closes = [100.0, 105.0, 102.0, 110.0, 120.0]
    kospi = [2000.0, 2010.0, 2020.0, 2030.0, 2040.0]
    fx = [1300.0, 1305.0, 1310.0, 1308.0, 1315.0]
    dates = ["2025-09-29", "2025-09-30", "2025-10-01", "2025-10-30", "2025-10-31"]
    for index, date in enumerate(dates):
        rows.append(
            {
                "date": date,
                "stock_close": closes[index],
                "stock_return_5d": 0.05,
                "stock_return_20d": 0.10,
                "stock_return_60d": 0.20,
                "stock_close_to_ma20": 0.03,
                "stock_close_to_ma60": 0.04,
                "stock_ma5_to_ma20": 0.01,
                "stock_rsi_14": 65.0,
                "stock_macd_hist": 10.0,
                "stock_macd_hist_change_1d": 1.0,
                "stock_bb_width_20": 0.15,
                "stock_volatility_20": 0.02,
                "stock_volume_ratio_20": 1.6,
                "stock_obv_trend": 0.1,
                "kospi_close": kospi[index],
                "kospi_return_5d": 0.01,
                "kospi_return_20d": 0.02,
                "kospi_close_to_ma20": 0.01,
                "kospi_rsi_14": 55.0,
                "kospi_volatility_20": 0.01,
                "fx_close": fx[index],
                "fx_return_5d": 0.01,
                "fx_return_20d": 0.01,
                "fx_close_to_ma20": 0.01,
                "fx_rsi_14": 50.0,
                "fx_volatility_20": 0.01,
                "stock_excess_return_5d": 0.04,
                "stock_excess_return_20d": 0.08,
                "stock_relative_strength_60": 0.10,
            }
        )
    return rows


def _dart_payload() -> dict:
    return {
        "schema_name": "dart_financial_index",
        "unit": "원",
        "periods": {"current_fiscal_year": {"period_end": "2025-09-30"}},
        "metric_order": ["revenue", "revenue_growth"],
        "metrics_by_key": {
            "revenue": {
                "metric_key": "revenue",
                "display_name": "Revenue",
                "metric_type": "period_value",
                "unit": "원",
                "values_by_period": {
                    "current_fiscal_year": {
                        "period": {"label": "제 15 기 3분기", "basis": "YTD", "period_end": "2025-09-30"},
                        "value": 1000,
                        "display_value": "1,000",
                        "status": "ok",
                    }
                },
            },
            "revenue_growth": {
                "metric_key": "revenue_growth",
                "display_name": "Revenue Growth",
                "metric_type": "comparison",
                "unit": "ratio",
                "comparisons": {"2025_vs_2024": {"display_value": "10.00%", "value": 0.1}},
            },
        },
    }


def _news_payload() -> dict:
    return {
        "output": {
            "periods": [
                {
                    "period": "2025-10",
                    "period_summary": "10월에는 핵심 제품 실적 성장 뉴스가 집중됐다.",
                    "issues": [
                        {
                            "issue": "실적 성장",
                            "mention_count": 4,
                            "importance": "high",
                            "rationale": "핵심 제품 성장.",
                        }
                    ],
                }
            ]
        }
    }


def _agent_report() -> dict:
    return {
        "agent_name": "Y-Finance Agent",
        "role": "Stock Price / Market Data Analyst",
        "target_company": "SK바이오팜",
        "ticker": "326030.KS",
        "as_of_date": "2025-10-31",
        "main_view": {
            "summary": "실적 성장 뉴스와 가격 지표를 함께 점검한다.",
            "direction": "neutral_positive",
            "primary_basis": ["실적 성장"],
        },
        "time_horizon_view": {
            "short_term": {
                "stance": "neutral_positive",
                "reasoning": "단기 흐름은 양호하나 과열 여부를 점검한다.",
                "key_features": ["stock_return_5d"],
            },
            "mid_term": {
                "stance": "positive",
                "reasoning": "중기 흐름은 시장 대비 우위다.",
                "key_features": ["stock_return_20d"],
            },
            "long_term": {
                "stance": "conditional_positive",
                "reasoning": "장기 판단은 제한적이다.",
                "key_features": ["stock_return_60d"],
                "data_limitation": "MA120/MA240 부재.",
            },
        },
        "detailed_analysis": {
            "price_trend": {
                "interpretation": "가격 추세 확인.",
                "supporting_features": {"stock_close_to_ma20": 3.0},
            },
            "momentum": {
                "interpretation": "모멘텀 확인.",
                "supporting_features": {"stock_rsi_14": 65.0},
            },
            "volatility_and_volume": {
                "interpretation": "수급 확인.",
                "supporting_features": {"stock_volume_ratio_20": 1.6},
            },
            "market_relative": {
                "interpretation": "상대성과 확인.",
                "supporting_features": {"stock_excess_return_20d": 8.0},
            },
            "fx_context": {
                "interpretation": "환율 확인.",
                "supporting_features": {"fx_return_20d": 1.0},
                "caution": "환율 영향은 별도 확인.",
            },
        },
        "cross_data_reconciliation": {
            "news_plus_market": _cross_block("뉴스와 시장 데이터 비교", "실적 성장"),
            "dart_plus_market": _cross_block("DART와 시장 데이터 비교", "매출 성장"),
            "news_plus_dart_plus_market": _cross_block("뉴스, DART, 시장 데이터 통합 비교", "통합 성장 근거"),
        },
    }


def _cross_block(summary: str, point: str) -> dict:
    return {
        "summary": summary,
        "reaction_points": [
            {
                "point": point,
                "cross_analysis": f"{point}과 가격 반응을 연결했다.",
                "reaction_interpretation": "가격 반응의 설명 근거로 사용한다.",
            }
        ],
        "divergences": [
            {
                "point": "상대성과 괴리",
                "cross_analysis": "절대 상승과 상대성과는 다를 수 있다.",
                "reaction_interpretation": "투자 의견 없이 괴리만 기록한다.",
            }
        ],
    }


if __name__ == "__main__":
    unittest.main()
