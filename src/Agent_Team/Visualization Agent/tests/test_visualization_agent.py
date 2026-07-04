from __future__ import annotations

import json
import sys
from pathlib import Path

import pandas as pd
import pytest


AGENT_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(AGENT_DIR))

from data_loader import (
    extract_financial_health_snapshot,
    extract_income_trend,
    extract_margin_trend,
    extract_peer_return_snapshot,
    extract_strategy_evidence_map,
    format_period_label,
    load_dart_index,
    load_market_dataset,
)
from chart_insights_builder import attach_chart_insights
from chart_builders import _safe_company_label, _safe_title_company
from manifest_builder import build_chart_manifest
from strategy_chart_planner import enrich_chart_metadata_with_strategy


def test_load_market_dataset_derives_moving_averages(tmp_path: Path) -> None:
    csv_path = tmp_path / "market.csv"
    pd.DataFrame(
        [
            {
                "date": "2025-01-02",
                "stock_close": 120.0,
                "stock_close_to_ma20": 0.2,
                "stock_close_to_ma60": -0.25,
                "stock_volume_ratio_20": 1.5,
                "stock_excess_return_20d": -0.03,
                "stock_relative_strength_60": 0.04,
            }
        ]
    ).to_csv(csv_path, index=False)

    df = load_market_dataset(csv_path)

    assert df.loc[0, "derived_ma20"] == pytest.approx(100.0)
    assert df.loc[0, "derived_ma60"] == pytest.approx(160.0)
    assert df.loc[0, "stock_excess_return_20d_pct"] == pytest.approx(-3.0)
    assert df.loc[0, "stock_relative_strength_60_pct"] == pytest.approx(4.0)


def test_load_market_dataset_missing_required_column_raises(tmp_path: Path) -> None:
    csv_path = tmp_path / "market.csv"
    pd.DataFrame(
        [
            {
                "date": "2025-01-02",
                "stock_close": 120.0,
                "stock_close_to_ma20": 0.2,
            }
        ]
    ).to_csv(csv_path, index=False)

    with pytest.raises(ValueError, match="Market dataset missing required columns"):
        load_market_dataset(csv_path)


def test_extract_margin_trend_and_q3_ytd_label(tmp_path: Path) -> None:
    dart_path = tmp_path / "dart_main.json"
    dart_payload = _sample_dart_payload()
    dart_path.write_text(json.dumps(dart_payload, ensure_ascii=False), encoding="utf-8")

    dart_index = load_dart_index(dart_path)
    margin_df = extract_margin_trend(dart_index)

    assert margin_df["period_label"].tolist() == ["2024 FY", "2025 Q3 YTD"]
    assert margin_df.loc[0, "contribution_margin_pct"] == pytest.approx(80.0)
    assert margin_df.loc[1, "sga_margin_pct"] == pytest.approx(50.0)
    assert format_period_label({"fiscal_year": 2025, "period_type": "Q3", "basis": "YTD"}) == "2025 Q3 YTD"


def test_extract_income_trend_converts_krw_to_billions(tmp_path: Path) -> None:
    dart_path = tmp_path / "dart_main.json"
    dart_path.write_text(json.dumps(_sample_dart_payload(), ensure_ascii=False), encoding="utf-8")

    income_df = extract_income_trend(load_dart_index(dart_path))

    assert income_df["period_label"].tolist() == ["2024 FY", "2025 Q3 YTD"]
    assert income_df.loc[0, "revenue_krw_bn"] == pytest.approx(100.0)
    assert income_df.loc[1, "contribution_profit_krw_bn"] == pytest.approx(180.0)
    assert income_df.loc[1, "sga_krw_bn"] == pytest.approx(90.0)


def test_extract_peer_return_snapshot_converts_ratios_to_percentages() -> None:
    market_df = pd.DataFrame(
        [
            {
                "date": pd.Timestamp("2025-10-31"),
                "stock_return_5d": 0.01,
                "stock_return_20d": 0.02,
                "stock_return_60d": -0.03,
                "stock_excess_return_20d": -0.04,
                "stock_relative_strength_60": 0.05,
                "stock_rsi_14": 55.0,
                "stock_macd_hist": 10.0,
                "stock_volatility_20": 0.015,
                "stock_volume_ratio_20": 1.2,
            }
        ]
    )

    snapshot = extract_peer_return_snapshot({"회사_YYYYMMDD": market_df}, {"회사_YYYYMMDD": "회사"})

    assert snapshot.loc[0, "stock_return_20d_pct"] == pytest.approx(2.0)
    assert snapshot.loc[0, "stock_excess_return_20d_pct"] == pytest.approx(-4.0)
    assert snapshot.loc[0, "stock_volatility_20_pct"] == pytest.approx(1.5)


def test_extract_financial_health_snapshot_and_strategy_evidence_map() -> None:
    financial_report = {
        "target_company": "회사",
        "detailed_analysis": {
            "capital_structure": {
                "supporting_features": {
                    "equity_ratio": 0.7,
                    "debt_to_equity": 0.4,
                    "period_basis": "POINT_IN_TIME",
                }
            },
            "liquidity": {
                "supporting_features": {
                    "current_ratio": 2.0,
                    "cash_ratio": 0.5,
                    "period_basis": "POINT_IN_TIME",
                }
            },
        },
    }
    health = extract_financial_health_snapshot({"회사_YYYYMMDD": financial_report}, {"회사_YYYYMMDD": "회사"})
    assert health.loc[0, "current_ratio_pct"] == pytest.approx(200.0)
    assert health.loc[0, "debt_to_equity_pct"] == pytest.approx(40.0)

    evidence = extract_strategy_evidence_map(
        {
            "decision_basis_card": {
                "basis_items": [{"category": "financial", "direction": "positive", "evidence": ["a", "b"]}],
                "risk_items": [{"category": "market", "direction": "negative", "evidence": ["c"]}],
                "mixed_or_conflicting_signals": [],
                "monitoring_points": [{"category": "monitoring", "direction": "watch", "evidence": []}],
            }
        }
    )
    assert evidence["signal_type"].tolist() == ["Positive Basis", "Risk", "Monitoring"]
    assert evidence.loc[0, "evidence_count"] == 2


def test_build_chart_manifest_writes_required_fields(tmp_path: Path) -> None:
    output_path = tmp_path / "chart_manifest.json"
    manifest = build_chart_manifest(
        company_name="테스트제약",
        run_key="테스트제약_YYYYMMDD",
        source_files={"market_full_dataset": "/tmp/market.csv", "dart_main": "/tmp/dart.json"},
        chart_metadata=[
            {
                "figure_id": "fig_stock_price_ma_volume_relative_strength",
                "title": "Stock Price with MA20/MA60, Volume Ratio, and Relative Strength",
                "caption": "caption",
                "writer_allowed_interpretation": "allowed",
                "writer_forbidden_interpretation": ["forbidden"],
                "data_limitations": ["limited"],
            }
        ],
        output_path=output_path,
    )

    on_disk = json.loads(output_path.read_text(encoding="utf-8"))
    assert manifest["agent_name"] == "Visualization Agent"
    assert on_disk["target_run_key"] == "테스트제약_YYYYMMDD"
    assert on_disk["charts"][0]["writer_forbidden_interpretation"] == ["forbidden"]


def test_chart_labels_preserve_dynamic_company_names() -> None:
    assert _safe_title_company("테스트제약") == "테스트제약"
    assert _safe_company_label("테스트제약") == "테스트제약"
    assert _safe_title_company("Global Pharma") == "Global Pharma"
    assert _safe_company_label("") == "Peer"


def test_enrich_chart_metadata_adds_strategy_aware_interpretation() -> None:
    charts = [
        {
            "figure_id": "fig_revenue_profit_sga_trend",
            "title": "Revenue, Contribution Profit, and SG&A Trend",
            "section_recommendation": "Financial Analysis",
            "caption": "caption",
            "writer_allowed_interpretation": "allowed",
            "data_limitations": ["YTD 수치가 포함된 경우 연간 확정치가 아니다."],
        },
        {
            "figure_id": "fig_stock_price_ma_volume_relative_strength",
            "title": "Stock Price with MA20/MA60, Volume Ratio, and Relative Strength",
            "section_recommendation": "Market / Price View",
            "caption": "caption",
            "writer_allowed_interpretation": "allowed",
            "data_limitations": ["시장 데이터는 펀더멘털 개선의 직접 증거가 아니다."],
        },
    ]
    strategy = {
        "final_recommendation": {"opinion": "Hold", "summary": "재무 개선은 긍정적이나 리스크로 Hold."},
        "final_rationale": {"why_buy_hold_sell": "Buy 전환보다 관망이 적절하다."},
        "financial_view": {
            "revenue": "매출 개선",
            "profitability": "수익성 개선",
            "cash_flow": "현금흐름 양호",
        },
        "market_price_view": {
            "price_trend": "이동평균 상회",
            "volume": "거래량 증가",
            "relative_strength": "상대성과 약세",
            "market_interpretation": "상대성과 확인 필요",
        },
        "investment_thesis": {"thesis_1": "재무 개선", "thesis_3": "리스크"},
    }

    enriched = enrich_chart_metadata_with_strategy(charts, strategy, company_name="테스트")

    assert enriched[0]["report_chart_role"] == "financial_income"
    assert enriched[0]["strategy_support_fields"]
    assert enriched[0]["writer_priority_score"] > 0
    assert "Hold" in enriched[0]["analyst_takeaway"]
    assert enriched[1]["report_chart_role"] == "market_composite"
    assert "상대성과" in enriched[1]["analyst_takeaway"]
    assert enriched[0]["recommended_report_rank"] >= 1
    assert enriched[1]["recommended_report_rank"] >= 1


def test_attach_chart_insights_adds_data_snapshot_and_commentary() -> None:
    market_df = pd.DataFrame(
        [
            {
                "date": pd.Timestamp("2025-10-31"),
                "stock_close": 100_000,
                "stock_close_to_ma20": 0.05,
                "stock_close_to_ma60": 0.10,
                "derived_ma20": 95_238.1,
                "derived_ma60": 90_909.1,
                "stock_volume_ratio_20": 1.6,
                "stock_excess_return_20d_pct": -7.5,
                "stock_relative_strength_60_pct": -8.3,
                "kospi_close": 4_000,
            }
        ]
    )
    margin_df = pd.DataFrame(
        [
            {"period_label": "2024 FY", "period_end": pd.Timestamp("2024-12-31"), "basis": "FULL_YEAR", "contribution_margin_pct": 80.0, "sga_margin_pct": 60.0},
            {"period_label": "2025 Q3 YTD", "period_end": pd.Timestamp("2025-09-30"), "basis": "YTD", "contribution_margin_pct": 93.6, "sga_margin_pct": 50.1},
        ]
    )
    income_df = pd.DataFrame(
        [
            {"period_label": "2024 FY", "period_end": pd.Timestamp("2024-12-31"), "basis": "FULL_YEAR", "revenue_krw_bn": 400.0, "contribution_profit_krw_bn": 300.0, "sga_krw_bn": 220.0},
            {"period_label": "2025 Q3 YTD", "period_end": pd.Timestamp("2025-09-30"), "basis": "YTD", "revenue_krw_bn": 501.1, "contribution_profit_krw_bn": 469.2, "sga_krw_bn": 251.0},
        ]
    )
    peer_return_df = pd.DataFrame(
        [
            {"company_name": "테스트", "date": pd.Timestamp("2025-10-31"), "stock_return_20d_pct": -5.0, "stock_return_60d_pct": 3.0, "stock_excess_return_20d_pct": -7.5, "stock_relative_strength_60_pct": -8.3},
            {"company_name": "Peer", "date": pd.Timestamp("2025-10-31"), "stock_return_20d_pct": 1.0, "stock_return_60d_pct": 4.0, "stock_excess_return_20d_pct": 2.0, "stock_relative_strength_60_pct": 1.0},
        ]
    )
    financial_health_df = pd.DataFrame(
        [
            {"company_name": "테스트", "current_ratio_pct": 500.0, "cash_ratio_pct": 100.0, "equity_ratio_pct": 80.0, "debt_to_equity_pct": 20.0},
            {"company_name": "Peer", "current_ratio_pct": 200.0, "cash_ratio_pct": 50.0, "equity_ratio_pct": 60.0, "debt_to_equity_pct": 50.0},
        ]
    )
    evidence_df = pd.DataFrame(
        [
            {"signal_type": "Positive Basis", "category": "financial"},
            {"signal_type": "Risk", "category": "market"},
            {"signal_type": "Monitoring", "category": "market"},
        ]
    )
    charts = [
        {"figure_id": "fig_stock_price_ma_volume_relative_strength"},
        {"figure_id": "fig_revenue_profit_sga_trend"},
        {"figure_id": "fig_investment_thesis_evidence_map"},
    ]

    enriched = attach_chart_insights(
        charts,
        market_df=market_df,
        margin_df=margin_df,
        income_df=income_df,
        peer_return_df=peer_return_df,
        financial_health_df=financial_health_df,
        evidence_df=evidence_df,
        company_name="테스트",
    )

    assert enriched[0]["data_snapshot"]["volume_ratio_20d"] == pytest.approx(1.6)
    assert "20일 거래량 비율" in enriched[0]["chart_observation"]
    assert enriched[1]["data_snapshot"]["revenue_krw_bn"] == pytest.approx(501.1)
    assert "공헌이익" in enriched[1]["chart_insights"]["what_is_visible"][0]
    assert enriched[2]["data_snapshot"]["risk_count"] == 1
    assert enriched[2]["chart_insights"]["watch_points"]


def _sample_dart_payload() -> dict:
    return {
        "periods": {
            "current_fiscal_year": {
                "label": "제 15 기 3분기",
                "fiscal_year": 2025,
                "period_type": "Q3",
                "period_end": "2025-09-30",
                "basis": "YTD",
            },
            "previous_fiscal_year": {
                "label": "제 14 기",
                "fiscal_year": 2024,
                "period_type": "ANNUAL",
                "period_end": "2024-12-31",
                "basis": "FULL_YEAR",
            },
        },
        "metrics_by_key": {
            "contribution_margin": {
                "values_by_period": {
                    "current_fiscal_year": {
                        "period": {
                            "fiscal_year": 2025,
                            "period_type": "Q3",
                            "period_end": "2025-09-30",
                            "basis": "YTD",
                        },
                        "value": 0.9,
                    },
                    "previous_fiscal_year": {
                        "period": {
                            "fiscal_year": 2024,
                            "period_type": "ANNUAL",
                            "period_end": "2024-12-31",
                            "basis": "FULL_YEAR",
                        },
                        "value": 0.8,
                    },
                }
            },
            "sga_margin": {
                "values_by_period": {
                    "current_fiscal_year": {
                        "period": {
                            "fiscal_year": 2025,
                            "period_type": "Q3",
                            "period_end": "2025-09-30",
                            "basis": "YTD",
                        },
                        "value": 0.5,
                    },
                    "previous_fiscal_year": {
                        "period": {
                            "fiscal_year": 2024,
                            "period_type": "ANNUAL",
                            "period_end": "2024-12-31",
                            "basis": "FULL_YEAR",
                        },
                        "value": 0.7,
                    },
                }
            },
            "revenue": {
                "values_by_period": {
                    "current_fiscal_year": {
                        "period": {
                            "fiscal_year": 2025,
                            "period_type": "Q3",
                            "period_end": "2025-09-30",
                            "basis": "YTD",
                        },
                        "value": 200_000_000_000,
                        "status": "ok",
                    },
                    "previous_fiscal_year": {
                        "period": {
                            "fiscal_year": 2024,
                            "period_type": "ANNUAL",
                            "period_end": "2024-12-31",
                            "basis": "FULL_YEAR",
                        },
                        "value": 100_000_000_000,
                        "status": "ok",
                    },
                }
            },
            "contribution_profit": {
                "values_by_period": {
                    "current_fiscal_year": {
                        "period": {
                            "fiscal_year": 2025,
                            "period_type": "Q3",
                            "period_end": "2025-09-30",
                            "basis": "YTD",
                        },
                        "value": 180_000_000_000,
                        "status": "ok",
                    },
                    "previous_fiscal_year": {
                        "period": {
                            "fiscal_year": 2024,
                            "period_type": "ANNUAL",
                            "period_end": "2024-12-31",
                            "basis": "FULL_YEAR",
                        },
                        "value": 80_000_000_000,
                        "status": "ok",
                    },
                }
            },
            "sga": {
                "values_by_period": {
                    "current_fiscal_year": {
                        "period": {
                            "fiscal_year": 2025,
                            "period_type": "Q3",
                            "period_end": "2025-09-30",
                            "basis": "YTD",
                        },
                        "value": 90_000_000_000,
                        "status": "ok",
                    },
                    "previous_fiscal_year": {
                        "period": {
                            "fiscal_year": 2024,
                            "period_type": "ANNUAL",
                            "period_end": "2024-12-31",
                            "basis": "FULL_YEAR",
                        },
                        "value": 70_000_000_000,
                        "status": "ok",
                    },
                }
            },
        },
    }
