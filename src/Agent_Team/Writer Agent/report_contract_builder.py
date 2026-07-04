"""Build broker_report_contract_v1.json from upstream agent outputs."""

from __future__ import annotations

import re
from datetime import datetime
from pathlib import Path
from typing import Any

from data_loader import get_nested
from figure_selector import select_figures, select_figures_by_ids
from interpretation_task_builder import build_interpretation_tasks
from source_trace_builder import build_source_trace


WRITER_AGENT_VERSION = "1.5-v4-llm-first"
BASIS_WARNING = "누적 기간 수치와 연간 수치는 집계 기준이 다를 수 있으므로 동일 기간 YoY로 단정하지 않는다."
PRICE_CAUSALITY_WARNING = "주가와 거래량 신호는 시장 관심과 가격 흐름을 보여주는 지표이며, 펀더멘털 개선의 직접 증거로 단정할 수 없다."


def build_report_contract(
    *,
    strategy_report: dict[str, Any],
    strategy_input_bundle: dict[str, Any] | None = None,
    dart_main: dict[str, Any] | None,
    chart_manifest: dict[str, Any] | None,
    visualization_dir: str | Path,
    source_files: dict[str, str],
    peer_comparison_dataset: dict[str, Any] | None = None,
    peer_positioning_summary: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Build the full Writer Agent broker report contract."""

    company_name = strategy_report.get("target_company_name") or "분석 대상 기업"
    run_key = strategy_report.get("target_run_key") or "unknown_run"
    base_date = _base_date_from_run_key(run_key)
    recommendation = get_nested(strategy_report, "final_recommendation.opinion", "N/A")
    final_summary = get_nested(strategy_report, "final_recommendation.summary", "")
    visual_blocks = select_figures(
        chart_manifest=chart_manifest or {"charts": []},
        strategy_report=strategy_report,
        visualization_dir=visualization_dir,
    )
    key_metrics = _build_key_metrics(dart_main, strategy_report, strategy_input_bundle)
    reader_friendly_sections = _build_reader_friendly_sections(strategy_report, key_metrics)
    peer_comparison = _build_peer_comparison_section(
        peer_comparison_dataset=peer_comparison_dataset,
        peer_positioning_summary=peer_positioning_summary,
        chart_manifest=chart_manifest or {"charts": []},
        strategy_report=strategy_report,
        visualization_dir=visualization_dir,
    )

    contract = {
        "render_targets": {
            "html_preview": True,
            "pdf_export": False,
            "default_render_format": "html",
        },
        "report_metadata": {
            "report_type": "Equity Research Draft",
            "company_name": company_name,
            "run_key": run_key,
            "base_date": base_date,
            "language": "ko",
            "recommendation": recommendation,
            "target_price": "N/A",
            "valuation_status": "Valuation Agent not applied",
            "writer_agent_version": WRITER_AGENT_VERSION,
            "created_at": datetime.now().astimezone().isoformat(timespec="seconds"),
            "source_files": source_files,
        },
        "cover_summary": _build_cover_summary(strategy_report, final_summary, key_metrics),
        "investment_view": _build_investment_view(strategy_report, recommendation),
        "key_metrics_table": {
            "metrics": key_metrics,
            "note": BASIS_WARNING,
        },
        "visual_report_blocks": visual_blocks,
        "peer_comparison": peer_comparison,
        "reader_friendly_sections": reader_friendly_sections,
        "sections": _build_sections(strategy_report, visual_blocks),
        "limitations": _build_limitations(strategy_report),
        "source_trace": build_source_trace(strategy_report, visual_blocks, key_metrics=key_metrics),
        "layout_plan": _build_layout_plan(),
        "validation_rules": _build_validation_rules(),
        "design_spec": _build_design_spec(),
    }
    contract["interpretation_tasks"] = build_interpretation_tasks(contract, strategy_report)
    return contract


def _build_cover_summary(
    strategy_report: dict[str, Any],
    final_summary: str,
    key_metrics: list[dict[str, str]],
) -> dict[str, Any]:
    company = strategy_report.get("target_company_name") or "분석 대상 기업"
    recommendation = get_nested(strategy_report, "final_recommendation.opinion", "N/A")
    thesis_items = _dict_values(strategy_report.get("investment_thesis", {}))
    positive_signals = _metric_signal_items(key_metrics)
    if len(positive_signals) < 4 and thesis_items:
        positive_signals.append("사업 모멘텀이 확인된다.")
    positive_signals = positive_signals[:4] or ["재무 및 사업 측면의 긍정 요인이 확인된다."]
    negative_signals = _risk_signal_items(strategy_report) or ["투자 판단상 확인 필요 요인이 남아 있다."]
    first_positive = positive_signals[0]
    first_risk = negative_signals[0]
    return {
        "headline": f"{company}: 긍정 요인과 확인 필요 요인을 함께 반영한 {recommendation} 의견",
        "one_line_view": f"{company}은 {first_positive}가 확인되지만, {first_risk}도 함께 고려해야 한다.",
        "recommendation_rationale": (
            f"현재 투자의견은 {recommendation}이다. "
            "재무 개선 신호는 긍정적이나 리스크와 시장 확인 부족을 함께 반영해야 한다."
        ),
        "key_debate": (
            "핵심 쟁점은 긍정 요인이 실적과 시장 확인으로 이어지는 속도와, 남아 있는 리스크가 투자 판단에 주는 할인 폭이다."
        ),
        "recommendation_rationale_short": _friendly_final_summary(final_summary),
        "positive_signals": positive_signals,
        "negative_signals": negative_signals,
        "monitoring_points": [
            "동일 기준 실적과 수익성 지표의 지속성을 확인한다.",
            "주요 리스크 이벤트의 완화 또는 확대 여부를 점검한다.",
            "가격 흐름과 시장 대비 성과가 함께 개선되는지 관찰한다.",
        ],
    }


def _build_investment_view(strategy_report: dict[str, Any], recommendation: str) -> dict[str, Any]:
    thesis = strategy_report.get("investment_thesis", {})
    thesis_items = []
    if isinstance(thesis, dict):
        for index, key in enumerate(["thesis_1", "thesis_2", "thesis_3"], start=1):
            if thesis.get(key):
                thesis_items.append(
                    {
                        "title": f"Thesis {index}",
                        "body": thesis[key],
                        "source_fields": [f"investment_thesis.{key}"],
                    }
                )
    return {
        "final_recommendation": recommendation,
        "investment_thesis": thesis_items,
        "recommendation_rationale": get_nested(strategy_report, "final_rationale.why_buy_hold_sell", ""),
        "not_buy_reason": _join_items(_risk_items(strategy_report)[:2], "확인 필요 요인이 남아 적극적 매수 판단에는 추가 근거가 필요하다."),
        "not_sell_reason": _join_items(
            _clean_items(strategy_report.get("key_strengths", []))[:2],
            "재무 및 사업 모멘텀이 확인되어 부정적 판단으로 단정하기 어렵다.",
        ),
    }


def _build_key_metrics(
    dart_main: dict[str, Any] | None,
    strategy_report: dict[str, Any],
    strategy_input_bundle: dict[str, Any] | None = None,
) -> list[dict[str, str]]:
    metric_specs = [
        ("Revenue", "revenue", "현재 기간", "매출 기반 확인", "financial_view.revenue"),
        ("Contribution Margin", "contribution_margin", "현재 기간", "수익성 구조 확인", "financial_view.profitability"),
        ("SG&A Margin", "sga_margin", "현재 기간", "비용 효율성 확인", "financial_view.profitability"),
        ("EPS", "eps", "현재 기간", "기간 기준 차이 유의", "risk_view.financial_risks"),
    ]
    metrics = []
    for display_name, metric_key, default_period, interpretation, source_field in metric_specs:
        entry = _current_metric_entry(dart_main, metric_key)
        metrics.append(
            {
                "metric_name": display_name,
                "value": _metric_display_value(metric_key, entry),
                "period": _period_label(entry.get("period")) or default_period,
                "interpretation": interpretation,
                "source_field": source_field,
                "source_file": "dart_main.json",
            }
        )
    metrics.extend(_build_stability_metrics(strategy_input_bundle))
    return metrics


def _build_stability_metrics(strategy_input_bundle: dict[str, Any] | None) -> list[dict[str, str]]:
    if not strategy_input_bundle:
        return []
    financial = get_nested(strategy_input_bundle, "target_reports.financial.detailed_analysis", {})
    if not isinstance(financial, dict):
        return []

    capital_features = get_nested(financial, "capital_structure.supporting_features", {})
    liquidity_features = get_nested(financial, "liquidity.supporting_features", {})
    cash_flow_features = get_nested(financial, "cash_flow.supporting_features", {})
    balance_features = get_nested(financial, "balance_sheet.supporting_features", {})
    metric_specs = [
        (
            "Debt Ratio",
            get_nested(capital_features, "debt_to_equity"),
            _point_in_time_period(capital_features),
            "자본 구조 안정성 확인",
            "target_reports.financial.detailed_analysis.capital_structure.supporting_features.debt_to_equity",
            "ratio_pct",
        ),
        (
            "Current Ratio",
            get_nested(liquidity_features, "current_ratio"),
            _point_in_time_period(liquidity_features),
            "단기 유동성 여력 양호",
            "target_reports.financial.detailed_analysis.liquidity.supporting_features.current_ratio",
            "ratio_pct",
        ),
        (
            "Operating Cash Flow",
            get_nested(cash_flow_features, "operating_cash_flow"),
            get_nested(cash_flow_features, "period", "2025 Q3 YTD"),
            "영업현금흐름 양호",
            "target_reports.financial.detailed_analysis.cash_flow.supporting_features.operating_cash_flow",
            "krw_100m",
        ),
        (
            "Cash & Cash Equivalents",
            get_nested(balance_features, "cash_and_cash_equivalents")
            or get_nested(liquidity_features, "cash_and_cash_equivalents"),
            _point_in_time_period(balance_features) or _point_in_time_period(liquidity_features),
            "현금성자산 기반 단기 대응 여력 확인",
            "target_reports.financial.detailed_analysis.balance_sheet.supporting_features.cash_and_cash_equivalents",
            "krw_100m",
        ),
    ]
    metrics: list[dict[str, str]] = []
    for name, value, period, interpretation, source_field, value_type in metric_specs:
        display_value = _display_stability_value(value, value_type)
        if display_value == "N/A":
            continue
        metrics.append(
            {
                "metric_name": name,
                "value": display_value,
                "period": str(period),
                "interpretation": interpretation,
                "source_field": source_field,
                "source_file": "strategy_input_bundle.json",
            }
        )
    return metrics


def _build_reader_friendly_sections(strategy_report: dict[str, Any], key_metrics: list[dict[str, str]]) -> dict[str, Any]:
    metric_map = {metric["metric_name"]: metric["value"] for metric in key_metrics}
    company = strategy_report.get("target_company_name") or "분석 대상 기업"
    recommendation = get_nested(strategy_report, "final_recommendation.opinion", "N/A")
    revenue = metric_map.get("Revenue", "N/A")
    contribution_margin = metric_map.get("Contribution Margin", "N/A")
    sga_margin = metric_map.get("SG&A Margin", "N/A")
    eps = metric_map.get("EPS", "N/A")
    debt_ratio = metric_map.get("Debt Ratio", "N/A")
    current_ratio = metric_map.get("Current Ratio", "N/A")
    operating_cash_flow = metric_map.get("Operating Cash Flow", "N/A")
    cash_equivalents = metric_map.get("Cash & Cash Equivalents", "N/A")
    strengths = [_short_item(item) for item in _clean_items(strategy_report.get("key_strengths", []))]
    risks = [_short_item(item) for item in _risk_items(strategy_report)]
    first_strength = strengths[0] if strengths else "재무 및 사업 측면의 긍정 요인"
    first_risk = risks[0] if risks else "확인 필요 요인"
    volume_values = _extract_number_phrases(get_nested(strategy_report, "market_price_view.volume", ""))
    relative_values = _extract_number_phrases(get_nested(strategy_report, "market_price_view.relative_strength", ""))
    return {
        "investment_summary": {
            "one_line_view": f"{company}은 {first_strength}가 확인되지만, {first_risk}도 함께 고려해야 한다.",
            "recommendation_rationale": (
                f"현재 투자의견은 {recommendation}이다. "
                "긍정 요인이 확인되더라도 리스크와 해석 한계가 남아 있어 보수적인 접근이 필요하다."
            ),
            "key_debate": "핵심 쟁점은 긍정 요인이 실적과 시장 확인으로 이어지는 속도와 리스크 할인 요인의 크기다.",
            "source_fields": ["final_recommendation.summary", "investment_thesis.thesis_1", "investment_thesis.thesis_3"],
        },
        "financial_view_cards": [
            {
                "title": "매출 성장",
                "what_we_see": f"{company}의 매출 지표는 {revenue} 수준으로 확인된다. 현재 기간 기준 매출 규모를 보여주는 핵심 수치다.",
                "why_it_matters": "매출은 사업 규모와 성장성 판단의 출발점이다. 다만 누적 기간과 연간 기준이 섞여 있으면 성장률을 단정하기보다 추세 확인에 초점을 둬야 한다.",
                "what_to_watch": "다음 공시에서 같은 기준의 매출 흐름이 유지되는지 확인해야 한다. 기간 기준 차이가 해소된 뒤에도 성장성이 이어지는지가 중요하다.",
                "investment_implication": "매출 신호는 현 결론 안에서 긍정 요인으로 반영하되, 단독으로 투자의견을 바꾸는 근거로 쓰지 않는다.",
                "source_fields": ["financial_view.revenue", "metrics_by_key.revenue"],
            },
            {
                "title": "수익성 구조",
                "what_we_see": f"공헌이익률은 {contribution_margin}, 판관비율은 {sga_margin} 수준으로 확인된다. 두 지표는 매출의 질과 비용 통제력을 함께 보여준다.",
                "why_it_matters": "수익성 구조가 개선되면 사업 모멘텀이 이익 체력으로 전환되는지 판단할 수 있다. 비용 효율성 지표는 투자 판단의 긍정 근거가 될 수 있다.",
                "what_to_watch": "다만 이 지표만으로 현재 데이터에 없는 추가 수익성 지표 개선을 추정해서는 안 된다. 연간 실적에서 공헌이익률과 판관비율의 방향성이 유지되는지 확인해야 한다.",
                "investment_implication": "수익성 지표는 현재 투자의견을 지탱하는 긍정 근거지만, 연간 지속성이 확인되기 전까지는 보수적으로 해석한다.",
                "source_fields": ["financial_view.profitability", "metrics_by_key.contribution_margin", "metrics_by_key.sga_margin"],
            },
            {
                "title": "현금흐름과 재무구조",
                "what_we_see": _cash_structure_sentence(
                    operating_cash_flow=operating_cash_flow,
                    debt_ratio=debt_ratio,
                    current_ratio=current_ratio,
                    cash_equivalents=cash_equivalents,
                ),
                "why_it_matters": "현금흐름과 자본 구조는 리스크 구간에서 재무적 완충력을 판단하는 근거다. 안정성 지표가 양호하면 부정적 이벤트가 발생해도 방어 논리가 강화된다.",
                "what_to_watch": "영업현금흐름이 반복적으로 유지되는지와 현금성자산 변화가 투자 집행 때문인지 구조적 부담 때문인지 구분해야 한다.",
                "investment_implication": "재무 안정성은 방어 논리를 보강하지만, 현금흐름 지속성 확인은 계속 필요하다.",
                "source_fields": ["financial_view.cash_flow", "financial_view.balance_sheet"],
            },
            {
                "title": "EPS 해석",
                "what_we_see": f"EPS는 {eps}으로 제시됐다. EPS는 기간 기준 차이가 있을 경우 연간 수치와 직접 비교하기 어렵다.",
                "why_it_matters": "EPS는 수익성 개선이 주주가치 지표로 이어지는지 확인하는 핵심 변수다. 기간 기준이 다르면 이익 개선을 과도하게 단정할 위험이 있다.",
                "what_to_watch": "동일 기준의 EPS가 확인될 때 매출과 수익성 개선이 주당이익으로 연결되는지 봐야 한다.",
                "investment_implication": "EPS 해석 제한은 보수적 결론을 유지하게 만드는 확인 필요 요인이다.",
                "source_fields": ["risk_view.financial_risks", "metrics_by_key.eps"],
            },
        ],
        "market_view_cards": [
            {
                "title": "가격 흐름",
                "what_we_see": "가격 흐름과 이동평균선 대비 위치는 단기 추세 개선 여부를 보여준다.",
                "why_it_matters": "가격 흐름 개선은 재무 개선 신호가 시장에서 완전히 무시되고 있지는 않다는 확인 신호다. 그러나 이동평균선 상회만으로 펀더멘털 개선이나 투자 의견 상향을 판단할 수는 없다.",
                "what_to_watch": "가격이 이동평균선 위에서 얼마나 오래 유지되는지와 변동성이 낮아지는지를 봐야 한다. 절대 가격 회복이 상대성과 개선으로 연결되는지도 함께 확인해야 한다.",
                "investment_implication": "가격 흐름은 긍정 신호로 반영되지만, 상대성과 확인 전까지 의견 상향 근거로는 부족하다.",
                "source_fields": ["market_price_view.price_trend"],
            },
            {
                "title": "거래 활성도",
                "what_we_see": _market_value_sentence("거래 활성도 지표", volume_values),
                "why_it_matters": "거래량 증가는 투자자 관심 회복 여부를 볼 때 유용하다. 다만 뉴스 이벤트나 단기 수급만으로도 발생할 수 있어 투자의견을 바꾸는 독립 근거로 쓰기 어렵다.",
                "what_to_watch": "거래량 증가가 실적 발표, 규제 뉴스, 사업 모멘텀 중 무엇에 반응한 것인지 구분해야 한다. 거래 활성화가 가격 안정과 상대성과 개선으로 이어지는지도 확인해야 한다.",
                "investment_implication": "거래 활성도는 관심 확대 신호지만, 펀더멘털 확인 전까지 보조 지표로 제한한다.",
                "source_fields": ["market_price_view.volume"],
            },
            {
                "title": "상대성과",
                "what_we_see": _market_value_sentence("상대성과 지표", relative_values),
                "why_it_matters": "상대성과는 투자의견 변화의 중요한 시장 확인 지표다. 재무 개선이 투자자 선호 회복으로 이어지고 있다면 상대성과가 개선되어야 하지만, 현재 데이터는 그 확인이 부족하다.",
                "what_to_watch": "상대강도 약세가 완화되는지, 주가 회복이 시장 대비 초과성과로 이어지는지 확인해야 한다. 경쟁과 규제 뉴스가 상대성과를 다시 훼손하는지도 점검 대상이다.",
                "investment_implication": "상대성과는 시장 확인 강도를 판단하는 보조 지표다.",
                "source_fields": ["market_price_view.relative_strength"],
            },
        ],
        "catalyst_analysis_cards": _build_catalyst_analysis_cards(strategy_report),
        "risk_cards": _build_reader_risk_cards(strategy_report),
        "final_rationale": _build_final_rationale(strategy_report, key_metrics),
    }


def _build_peer_comparison_section(
    *,
    peer_comparison_dataset: dict[str, Any] | None,
    peer_positioning_summary: dict[str, Any] | None,
    chart_manifest: dict[str, Any],
    strategy_report: dict[str, Any],
    visualization_dir: str | Path,
) -> dict[str, Any]:
    if not peer_comparison_dataset or not isinstance(peer_comparison_dataset.get("metrics"), list):
        return {
            "enabled": False,
            "reason": "peer_comparison_dataset.json 없음",
            "table_rows": [],
            "peer_chart_blocks": [],
        }

    rows = [row for row in peer_comparison_dataset.get("metrics", []) if isinstance(row, dict)]
    if not rows:
        return {
            "enabled": False,
            "reason": "peer_comparison_dataset.json에 비교 가능한 행 없음",
            "table_rows": [],
            "peer_chart_blocks": [],
        }

    target_company = peer_comparison_dataset.get("target_company") or strategy_report.get("target_company_name") or "대상 기업"
    positioning = peer_positioning_summary or {}
    attractiveness = positioning.get("relative_attractiveness", {}) if isinstance(positioning, dict) else {}
    relative_positioning = positioning.get("relative_positioning", {}) if isinstance(positioning, dict) else {}
    table_rows = [_peer_table_row(row) for row in rows]
    peer_chart_blocks = _select_peer_chart_blocks(chart_manifest, strategy_report, visualization_dir)
    limitations = peer_comparison_dataset.get("comparison_limits", []) if isinstance(peer_comparison_dataset, dict) else []
    return {
        "enabled": True,
        "title": "동종기업 비교",
        "subtitle": "현재 데이터로 가능한 국내 peer 기준 상대 위치 점검",
        "target_company": target_company,
        "peer_scope": peer_comparison_dataset.get("peer_scope", "domestic_only"),
        "excluded_scope": peer_comparison_dataset.get("excluded_scope", []),
        "commentary_mode": "llm_writer_preferred",
        "peer_investment_commentary": (
            f"{target_company}의 국내 peer 비교는 재무 체력의 상대 우위와 시장 성과 확인 부족을 함께 보여준다. "
            "이 섹션은 동일 기준일에 확보된 국내 비교군만 사용하며, 표와 차트의 수치를 바탕으로 투자 판단상 의미를 해석한다."
        ),
        "relative_positioning_summary": (
            "재무 지표상 우위는 긍정적이나, 시장 대비 성과와 비교 데이터의 범위 제한을 함께 고려해야 한다."
        ),
        "peer_limitations_commentary": (
            "국내 비교군과 확인 가능한 재무·시장 지표에 한정한 분석이며, 글로벌 peer와 가치평가 비교는 포함하지 않는다."
        ),
        "table_columns": [
            "구분",
            "기업",
            "매출",
            "공헌이익률",
            "판관비율",
            "EPS",
            "영업현금흐름",
            "유동비율",
            "부채비율",
            "20일 초과수익률",
            "60일 상대강도",
        ],
        "table_rows": table_rows,
        "peer_chart_blocks": peer_chart_blocks,
        "relative_positioning": {
            "revenue_scale": relative_positioning.get("revenue_scale", ""),
            "profitability": relative_positioning.get("profitability", ""),
            "financial_stability": relative_positioning.get("financial_stability", ""),
            "market_performance": relative_positioning.get("market_performance", ""),
            "valuation": relative_positioning.get("valuation", ""),
        },
        "analysis_cards": [
            {
                "title": "상대 우위",
                "body": _join_items(
                    _clean_items(attractiveness.get("attractive_points", []))[:3],
                    "국내 peer 대비 명확한 상대 우위는 확인 가능한 지표 범위에서 제한적으로 해석한다.",
                ),
            },
            {
                "title": "할인 요인",
                "body": _join_items(
                    _clean_items(attractiveness.get("discount_factors", []))[:3],
                    "시장 성과, 결측 데이터, valuation 부재는 peer 해석의 할인 요인이다.",
                ),
            },
            {
                "title": "투자 판단 시사점",
                "body": attractiveness.get("investment_implication")
                or get_nested(strategy_report, "peer_competitor_positioning.peer_based_investment_implication", "")
                or "Peer 비교는 단독 투자의견 산출이 아니라 Strategy 판단을 보조하는 상대 위치 점검으로 활용한다.",
            },
        ],
        "limitations": _clean_items(limitations)[:5],
        "source_files": peer_comparison_dataset.get("source_files", {}),
    }


def _peer_table_row(row: dict[str, Any]) -> dict[str, Any]:
    financial = row.get("financial_metrics", {}) if isinstance(row.get("financial_metrics"), dict) else {}
    market = row.get("market_metrics", {}) if isinstance(row.get("market_metrics"), dict) else {}
    return {
        "peer_group": "대상기업" if row.get("peer_group") == "target" else "국내 Peer",
        "company_name": row.get("company_name") or row.get("run_key", ""),
        "is_target": row.get("peer_group") == "target",
        "revenue": _fmt_100m(financial.get("revenue_100m")),
        "contribution_margin": _fmt_pct(financial.get("contribution_margin_pct")),
        "sga_margin": _fmt_pct(financial.get("sga_margin_pct")),
        "eps": _fmt_won(financial.get("eps")),
        "operating_cash_flow": _fmt_100m(financial.get("operating_cash_flow_100m")),
        "current_ratio": _fmt_pct(financial.get("current_ratio_pct")),
        "debt_ratio": _fmt_pct(financial.get("debt_ratio_pct")),
        "excess_return_20d": _fmt_signed_pct(market.get("stock_excess_return_20d_pct")),
        "relative_strength_60d": _fmt_signed_pct(market.get("stock_relative_strength_60_pct")),
        "financial_period": financial.get("financial_period") or "",
        "missing_fields": row.get("data_quality", {}).get("missing_fields", []),
    }


def _select_peer_chart_blocks(
    chart_manifest: dict[str, Any],
    strategy_report: dict[str, Any],
    visualization_dir: str | Path,
) -> list[dict[str, Any]]:
    peer_ids = []
    for chart in chart_manifest.get("charts", []):
        if not isinstance(chart, dict) or not chart.get("figure_id"):
            continue
        text = " ".join(
            str(chart.get(key, ""))
            for key in ["figure_id", "title", "section_recommendation", "caption", "chart_type"]
        ).lower()
        if "peer" in text or "comparison" in text:
            peer_ids.append(str(chart["figure_id"]))
    if not peer_ids:
        return []
    return select_figures_by_ids(
        chart_manifest=chart_manifest,
        strategy_report=strategy_report,
        visualization_dir=visualization_dir,
        figure_ids=peer_ids,
        max_figures=3,
    )


def _fmt_100m(value: Any) -> str:
    number = _safe_number(value)
    if number is None:
        return "N/A"
    return f"{number:,.0f}억원"


def _fmt_pct(value: Any) -> str:
    number = _safe_number(value)
    if number is None:
        return "N/A"
    return f"{number:,.1f}%"


def _fmt_signed_pct(value: Any) -> str:
    number = _safe_number(value)
    if number is None:
        return "N/A"
    return f"{number:+.2f}%"


def _fmt_won(value: Any) -> str:
    number = _safe_number(value)
    if number is None:
        return "N/A"
    return f"{number:,.0f}원"


def _safe_number(value: Any) -> float | None:
    if value is None:
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if number != number:
        return None
    return number


def _build_reader_risk_cards(strategy_report: dict[str, Any]) -> list[dict[str, Any]]:
    risk_specs = [
        (
            "Financial Risk",
            get_nested(strategy_report, "risk_view.financial_risks", []),
            "재무 리스크는 개선 신호가 이익과 현금 창출로 이어지는지 확인하기 전까지 실적 전망을 보수적으로 보게 만든다.",
            "동일 기준의 이익 지표와 현금흐름 방향을 함께 확인한다.",
            "risk_view.financial_risks",
        ),
        (
            "Regulatory Risk",
            get_nested(strategy_report, "risk_view.regulatory_risks", []),
            "규제와 정책 변수는 사업 모멘텀의 실행 속도와 투자심리에 동시에 영향을 줄 수 있어 리스크 할인 요인이 된다.",
            "주요 규제 이벤트와 정책 변화가 사업 계획에 미치는 영향을 추적한다.",
            "risk_view.regulatory_risks",
        ),
        (
            "Market Risk",
            get_nested(strategy_report, "risk_view.market_risks", []),
            "시장 리스크는 가격 흐름과 상대성과가 투자 판단을 충분히 확인해 주는지에 대한 불확실성이다. 이 신호가 약하면 의견 상향의 설득력이 낮아진다.",
            "상대성과, 거래 활성도, 경쟁 환경 변화가 함께 개선되는지 점검한다.",
            "risk_view.market_risks",
        ),
        (
            "Execution Risk",
            get_nested(strategy_report, "risk_view.execution_risks", []),
            "실행 리스크는 사업 계획이 실제 매출과 이익 기여로 전환되는지 확인되기 전까지 기대 요인을 할인하게 만든다.",
            "주요 사업 과제의 일정, 상업화, 실적 반영 여부를 확인한다.",
            "risk_view.execution_risks",
        ),
    ]
    cards: list[dict[str, Any]] = []
    for risk_type, items, impact, monitoring, source_field in risk_specs:
        descriptions = items[:2] if isinstance(items, list) else []
        cards.append(
            {
                "risk_type": risk_type,
                "description": _friendly_risk_description(risk_type, descriptions),
                "impact": impact,
                "monitoring_point": monitoring,
                "source_fields": [source_field],
            }
        )
    return cards


def _build_catalyst_analysis_cards(strategy_report: dict[str, Any]) -> list[dict[str, Any]]:
    catalysts = get_nested(strategy_report, "catalyst_view.positive_catalysts", [])
    business = get_nested(strategy_report, "catalyst_view.business_expansion", [])
    source_items = catalysts if isinstance(catalysts, list) else []
    business_items = business if isinstance(business, list) else []
    all_items = [(str(item), f"catalyst_view.positive_catalysts[{index}]") for index, item in enumerate(source_items)]
    all_items.extend((str(item), f"catalyst_view.business_expansion[{index}]") for index, item in enumerate(business_items))
    cards: list[dict[str, Any]] = []
    for category in ["commercial_product", "digital_platform", "pipeline", "global"]:
        matches = [(text, source_field) for text, source_field in all_items if _catalyst_category(text) == category]
        if matches:
            cards.append(_catalyst_card_from_items(category, matches))
    if cards:
        return cards[:4]
    used_sources = {source for card in cards for source in card.get("source_fields", [])}
    for text, source_field in all_items:
        if source_field in used_sources:
            continue
        cards.append(_catalyst_card_from_items("generic", [(text, source_field)]))
        if len(cards) >= 5:
            break
    if not cards:
        cards.append(
            {
                "catalyst_title": "확인된 촉매 없음",
                "catalyst_group": "기타 촉매",
                "investment_relevance": "현재 확인 가능한 별도 성장 촉매가 제한적이다.",
                "evidence_from_strategy": "catalyst_view 별도 값 없음",
                "what_to_watch": "추가 뉴스와 사업 업데이트에서 투자 판단에 영향을 줄 촉매가 확인되는지 점검한다.",
                "source_fields": ["catalyst_view"],
            }
        )
    return cards


def _build_final_rationale(strategy_report: dict[str, Any], key_metrics: list[dict[str, str]]) -> dict[str, Any]:
    recommendation = get_nested(strategy_report, "final_recommendation.opinion", "N/A")
    strengths = _clean_items(strategy_report.get("key_strengths", []))[:3]
    risks = _risk_items(strategy_report)[:3]
    metrics = [f"{metric['metric_name']} {metric['value']}" for metric in key_metrics if metric.get("value") != "N/A"][:3]
    return {
        "title": "최종 투자의견 근거",
        "positive_case": _join_items(strengths, _join_items(metrics, "확인 가능한 긍정 요인을 반영한다.")),
        "caution_case": _join_items(risks, "투자 판단상 확인 필요 요인이 남아 있다."),
        "investment_conclusion": (
            f"종합하면 현재 투자의견은 {recommendation}이다. "
            "재무 개선 신호는 긍정적이나 리스크와 시장 확인 부족이 남아 있어 보수적 접근이 필요하다."
        ),
        "what_we_see": "재무, 시장, 촉매, 리스크 신호가 함께 제시되어 투자 판단은 균형 있게 해석해야 한다.",
        "why_it_matters": "긍정 신호와 확인 필요 요인이 공존하는 구간에서는 리스크를 할인한 균형 판단이 필요하다.",
        "what_to_watch": "동일 기준 실적, 주요 리스크 변화, 시장 확인 신호를 함께 점검한다.",
        "investment_implication": "최종 결론은 긍정 요인을 인정하되 확인 필요 요인을 할인하며, 향후 판단 변화 조건을 함께 제시한다.",
        "source_fields": ["final_rationale.why_buy_hold_sell", "limitations.monitoring_points"],
    }


def _friendly_final_summary(final_summary: str) -> str:
    if not final_summary:
        return "확인된 투자 근거를 기준으로 긍정 요인과 확인 필요 요인을 균형 있게 반영한다."
    return "긍정 요인과 리스크가 함께 존재하므로 보수적 투자 판단이 필요하다."


def _friendly_risk_description(risk_type: str, descriptions: list[Any]) -> str:
    labels = {
        "Financial Risk": "재무 지표의 지속성과 기간 기준 차이를 확인해야 한다.",
        "Regulatory Risk": "규제와 정책 변화가 사업 전망에 미치는 영향을 확인해야 한다.",
        "Market Risk": "가격과 상대성과 신호의 지속성을 확인해야 한다.",
        "Execution Risk": "사업 계획이 실적으로 전환되는지 확인해야 한다.",
    }
    count = len(descriptions)
    suffix = f" 관련 리스크 항목 {count}개가 확인된다." if count else ""
    return labels.get(risk_type, "관련 리스크는 지속적인 확인이 필요하다.") + suffix


def _metric_display_value(metric_key: str, entry: dict[str, Any]) -> str:
    if not entry:
        return "N/A"
    value = entry.get("value")
    if metric_key == "revenue" and isinstance(value, (int, float)):
        return f"{value / 100_000_000:,.0f}억원"
    if metric_key == "eps":
        display_value = entry.get("display_value", "N/A")
        return f"{display_value}원" if display_value != "N/A" else display_value
    return entry.get("display_value", "N/A")


def _display_stability_value(value: Any, value_type: str) -> str:
    if not isinstance(value, (int, float)):
        return "N/A"
    if value_type == "ratio_pct":
        return f"{value * 100:.1f}%"
    if value_type == "krw_100m":
        return f"{value / 100_000_000:,.0f}억원"
    return str(value)


def _current_metric_entry(dart_main: dict[str, Any] | None, metric_key: str) -> dict[str, Any]:
    if not dart_main:
        return {}
    return (
        dart_main.get("metrics_by_key", {})
        .get(metric_key, {})
        .get("values_by_period", {})
        .get("current_fiscal_year", {})
    )


def _period_label(period: dict[str, Any] | None) -> str:
    if not isinstance(period, dict):
        return ""
    fiscal_year = period.get("fiscal_year")
    period_type = period.get("period_type")
    basis = period.get("basis")
    if basis == "YTD":
        return f"{fiscal_year} {period_type} YTD"
    if basis == "FULL_YEAR":
        return f"{fiscal_year} FY"
    return " ".join(str(part) for part in [fiscal_year, period_type, basis] if part)


def _build_sections(strategy_report: dict[str, Any], visual_blocks: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "investment_summary": {
            "title": "Investment Summary",
            "body": get_nested(strategy_report, "final_recommendation.summary", ""),
            "key_points": _dict_values(strategy_report.get("investment_thesis", {})),
        },
        "financial_view": {
            "title": "Financial View",
            "body": get_nested(strategy_report, "financial_view.financial_interpretation", ""),
            "subsections": {
                "revenue": get_nested(strategy_report, "financial_view.revenue", ""),
                "profitability": get_nested(strategy_report, "financial_view.profitability", ""),
                "cash_flow": get_nested(strategy_report, "financial_view.cash_flow", ""),
                "balance_sheet": get_nested(strategy_report, "financial_view.balance_sheet", ""),
                "basis_note": BASIS_WARNING,
            },
            "linked_figures": [block["figure_id"] for block in visual_blocks if block["section"] == "Financial Analysis"],
        },
        "market_price_view": {
            "title": "Market / Price View",
            "body": get_nested(strategy_report, "market_price_view.market_interpretation", ""),
            "subsections": {
                "price_trend": get_nested(strategy_report, "market_price_view.price_trend", ""),
                "volume": get_nested(strategy_report, "market_price_view.volume", ""),
                "relative_strength": get_nested(strategy_report, "market_price_view.relative_strength", ""),
                "market_interpretation": get_nested(strategy_report, "market_price_view.market_interpretation", ""),
                "causality_note": PRICE_CAUSALITY_WARNING,
            },
            "linked_figures": [block["figure_id"] for block in visual_blocks if "Market" in block["section"]],
        },
        "catalyst_and_risk": {
            "title": "Catalyst & Risk",
            "positive_catalysts": get_nested(strategy_report, "catalyst_view.positive_catalysts", []),
            "business_expansion": get_nested(strategy_report, "catalyst_view.business_expansion", []),
            "risk_blocks": {
                "financial_risks": get_nested(strategy_report, "risk_view.financial_risks", []),
                "regulatory_risks": get_nested(strategy_report, "risk_view.regulatory_risks", []),
                "market_risks": get_nested(strategy_report, "risk_view.market_risks", []),
                "execution_risks": get_nested(strategy_report, "risk_view.execution_risks", []),
            },
        },
        "peer_positioning": {
            "title": "Peer / Competitor Positioning",
            "body": get_nested(strategy_report, "peer_competitor_positioning.peer_based_investment_implication", ""),
            "target_relative_strength": get_nested(strategy_report, "peer_competitor_positioning.target_relative_strength", []),
            "target_relative_weakness": get_nested(strategy_report, "peer_competitor_positioning.target_relative_weakness", []),
        },
        "final_rationale": {
            "title": "Final Rationale",
            "body": get_nested(strategy_report, "final_rationale.why_buy_hold_sell", ""),
        },
    }


def _build_limitations(strategy_report: dict[str, Any]) -> dict[str, list[str]]:
    return {
        "data_limitations": [
            "누적 기간 수치와 연간 수치는 집계 기준이 다를 수 있으므로 동일 기간 YoY로 단정하지 않는다.",
            "이익 및 현금흐름 지표는 기간 기준과 일회성 요인의 영향을 받을 수 있다.",
            "확인 가능한 근거가 없는 지표는 새로 생성하지 않는다.",
        ],
        "interpretation_limitations": [
            "가격과 거래량 신호는 시장 관심과 수급 흐름을 보여주지만 펀더멘털 개선의 직접 증거는 아니다.",
            "뉴스 촉매와 재무 지표 사이에는 시차가 있을 수 있어 사업 모멘텀을 실적 기여로 단정하지 않는다.",
            "경쟁 환경과 실행 리스크는 후속 데이터 확인 전까지 보수적으로 해석한다.",
        ],
        "monitoring_points": [
            "동일 기준 실적과 수익성 지표의 지속성을 확인한다.",
            "주요 리스크 이벤트의 완화 또는 확대 여부를 점검한다.",
            "가격 흐름과 시장 대비 성과가 함께 개선되는지 관찰한다.",
        ],
    }


def _build_layout_plan() -> dict[str, list[str]]:
    return {
        "page_1": ["investment_summary", "recommendation_cards", "key_metrics_table"],
        "page_2": ["key_charts"],
        "page_3": ["financial_view_cards", "market_view_cards"],
        "page_4": ["peer_comparison"],
        "page_5": ["catalyst_cards", "risk_cards", "final_rationale"],
        "appendix": ["limitations"],
        "debug_appendix": ["source_trace"],
    }


def _build_validation_rules() -> dict[str, Any]:
    return {
        "recommendation_must_match_strategy": True,
        "target_price_allowed": False,
        "new_number_generation_allowed": False,
        "forbidden_terms_without_source": [
            "적정주가",
            "상승여력",
            "하락여력",
            "P/E Band",
            "P/B Band",
            "PER 밴드",
            "PBR 밴드",
            "OPM",
            "ROE",
            "DCF",
            "fair value",
            "upside",
            "downside",
            "영업이익률 개선",
            "자기자본이익률 개선",
            "저평가",
            "고평가",
            "강력 매수",
            "매수 전환",
            "목표주가 상향",
            "실적 개선 확정",
            "펀더멘털 개선이 주가 상승을 견인",
        ],
        "basis_mismatch_warning_required": True,
        "price_signal_causality_warning_required": True,
        "html_preview_required": True,
        "source_trace_default_hidden": True,
        "reader_friendly_rewrite_required": True,
        "chart_structure_required": ["what_chart_shows", "analyst_takeaway", "interpretation_limit"],
    }


def _build_design_spec() -> dict[str, Any]:
    return {
        "theme": {
            "style": "professional_equity_research",
            "primary_color": "ReportNavy",
            "accent_color": "ReportBlue",
            "risk_color": "ReportRiskRed",
            "background_color": "white",
            "box_style": "subtle_colored_box",
        },
        "typography": {
            "main_font": "Noto Sans CJK KR",
            "title_font": "Noto Sans CJK KR",
            "body_font_size": "10pt",
            "caption_font_size": "8.5pt",
        },
        "layout": {
            "preview_width": "1120px",
            "paper_size": "A4",
            "orientation": "portrait",
            "margin": "40px 48px",
            "page_count_target": "4 report pages plus appendix",
            "summary_page_columns": 2,
            "chart_width": "100%",
        },
        "components": {
            "recommendation_card": True,
            "summary_box": True,
            "positive_signal_box": True,
            "risk_signal_box": True,
            "key_metrics_table": True,
            "chart_takeaway_box": True,
            "limitation_note_box": True,
            "risk_matrix": True,
        },
    }


def _clean_items(value: Any) -> list[str]:
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    if isinstance(value, str) and value.strip():
        return [value.strip()]
    return []


def _risk_items(strategy_report: dict[str, Any]) -> list[str]:
    key_risks = _clean_items(strategy_report.get("key_risks", []))
    if key_risks:
        return key_risks
    risks: list[str] = []
    risk_view = strategy_report.get("risk_view", {})
    if isinstance(risk_view, dict):
        for key in ["financial_risks", "regulatory_risks", "market_risks", "execution_risks"]:
            risks.extend(_clean_items(risk_view.get(key, [])))
    return risks


def _join_items(items: list[Any], fallback: str) -> str:
    cleaned = [_short_item(str(item)) for item in items if str(item).strip()]
    if not cleaned:
        return fallback
    if len(cleaned) == 1:
        return cleaned[0]
    return " / ".join(cleaned)


def _short_item(text: str, limit: int = 72) -> str:
    normalized = " ".join(text.split())
    if len(normalized) <= limit:
        return normalized
    return normalized[:limit].rstrip(" ,.;") + "..."


def _extract_number_phrases(text: Any) -> list[str]:
    if not isinstance(text, str):
        return []
    pattern = r"[-+]?\d+(?:,\d{3})*(?:\.\d+)?\s*(?:%|배|원|억원|조원|일|개월|년|p|bp)?"
    return [match.group(0).strip() for match in re.finditer(pattern, text) if match.group(0).strip()]


def _cash_structure_sentence(
    *,
    operating_cash_flow: str,
    debt_ratio: str,
    current_ratio: str,
    cash_equivalents: str,
) -> str:
    parts = []
    if operating_cash_flow != "N/A":
        parts.append(f"영업활동현금흐름 {operating_cash_flow}")
    if debt_ratio != "N/A":
        parts.append(f"부채비율 {debt_ratio}")
    if current_ratio != "N/A":
        parts.append(f"유동비율 {current_ratio}")
    if cash_equivalents != "N/A":
        parts.append(f"현금및현금성자산 {cash_equivalents}")
    if not parts:
        return "현금흐름과 재무구조 지표는 재무 분석 문맥 안에서 보수적으로 확인해야 한다."
    return " / ".join(parts) + "가 확인된다."


def _market_value_sentence(label: str, values: list[str]) -> str:
    if not values:
        return f"{label}는 가격 흐름을 보완하는 참고 지표로 해석한다."
    return f"{label}는 {', '.join(values[:3])} 등으로 제시된다."


def _metric_signal_items(metrics: list[dict[str, str]]) -> list[str]:
    signals = []
    for metric in metrics:
        name = metric.get("metric_name", "")
        value = metric.get("value", "")
        interpretation = metric.get("interpretation", "")
        if not name or not value or value == "N/A":
            continue
        if name == "EPS":
            continue
        signals.append(f"{_metric_display_name(name)} {value}: {interpretation}")
        if len(signals) >= 4:
            break
    return signals


def _metric_display_name(value: Any) -> str:
    labels = {
        "Revenue": "매출",
        "Contribution Margin": "공헌이익률",
        "SG&A Margin": "판관비율",
        "EPS": "EPS",
        "Debt Ratio": "부채비율",
        "Current Ratio": "유동비율",
        "Operating Cash Flow": "영업활동현금흐름",
        "Cash & Cash Equivalents": "현금및현금성자산",
    }
    return labels.get(str(value), str(value))


def _risk_signal_items(strategy_report: dict[str, Any]) -> list[str]:
    risk_view = strategy_report.get("risk_view", {})
    if not isinstance(risk_view, dict):
        return []
    specs = [
        ("financial_risks", "재무 리스크: 이익·현금흐름 확인 필요"),
        ("regulatory_risks", "규제·정책 리스크: 사업 불확실성 확인 필요"),
        ("market_risks", "시장 리스크: 가격·상대성과 확인 필요"),
        ("execution_risks", "실행 리스크: 사업 계획의 실적 반영 확인 필요"),
    ]
    signals = []
    for key, label in specs:
        if _clean_items(risk_view.get(key, [])):
            signals.append(label)
    return signals


def _point_in_time_period(features: Any) -> str:
    if not isinstance(features, dict):
        return "현재 기준"
    period = features.get("period") or features.get("period_basis")
    if not period:
        return "현재 기준"
    if period == "POINT_IN_TIME":
        return "현재 기준"
    return str(period)


def _catalyst_card_from_items(category: str, matches: list[tuple[str, str]]) -> dict[str, Any]:
    source_fields = [source_field for _, source_field in matches[:3]]
    profile = _catalyst_profile(category, matches[0][0])
    return {
        "catalyst_title": profile["title"],
        "catalyst_group": profile["group"],
        "investment_relevance": profile["investment_relevance"],
        "evidence_from_strategy": _catalyst_evidence_summary(category, matches),
        "what_to_watch": profile["what_to_watch"],
        "source_fields": source_fields,
    }


def _catalyst_category(text: Any) -> str:
    value = str(text).lower()
    if any(token in value for token in ["처방", "점유율", "매출", "판매", "상업화", "제품", "치료제", "품목"]):
        return "commercial_product"
    if any(token in value for token in ["ai", "디지털", "플랫폼", "조인트벤처", "헬스케어", "솔루션"]):
        return "digital_platform"
    if any(token in value for token in ["신약", "파이프라인", "원료", "공동연구", "후보물질", "임상"]):
        return "pipeline"
    if any(token in value for token in ["글로벌", "해외", "수출", "현지", "국제", "지역 확장", "사업 확장", "인허가", "출시"]):
        return "global"
    return "generic"


def _catalyst_profile(category: str, source_text: str) -> dict[str, str]:
    if category == "commercial_product":
        product_name = _leading_catalyst_term(source_text)
        return {
            "title": f"{product_name} 매출 확대" if product_name else "핵심 제품/서비스 매출 확대",
            "group": "핵심 촉매",
            "investment_relevance": (
                "핵심 제품 또는 서비스의 판매, 이용량, 점유율 확대는 매출 가시성과 수익성 개선을 직접 확인할 수 있는 촉매다. "
                "상업화 지표가 유지될수록 재무 개선의 지속성에 대한 신뢰가 높아진다."
            ),
            "what_to_watch": "판매 또는 이용 추이, 점유율 유지, 분기 매출 기여도, 관련 규제 또는 경쟁 이슈의 영향을 확인한다.",
        }
    if category == "digital_platform":
        return {
            "title": "디지털/플랫폼 신사업",
            "group": "중장기 성장 옵션",
            "investment_relevance": (
                "디지털, AI, 플랫폼 사업은 기존 제품 매출 외 확장 옵션을 제공한다. "
                "다만 단기 실적 기여보다 사업모델 검증과 사용자 확보가 먼저 확인되어야 한다."
            ),
            "what_to_watch": "서비스 출시 일정, 사용자 확보, 파트너십 진행, 기존 사업과의 시너지 여부를 점검한다.",
        }
    if category == "pipeline":
        return {
            "title": "연구개발/파이프라인 확장",
            "group": "중장기 성장 옵션",
            "investment_relevance": (
                "연구개발과 파이프라인 확장은 중장기 성장 옵션과 사업 다각화 측면에서 의미가 있다. "
                "다만 초기 단계의 성과는 상업화 가능성과 실적 기여로 이어지는지 별도 확인이 필요하다."
            ),
            "what_to_watch": "프로젝트 구체화, 단계별 마일스톤, 공동연구 또는 파트너십 성과, 공급 안정성을 확인한다.",
        }
    if category == "global":
        return {
            "title": "해외 시장 및 적용 범위 확대",
            "group": "글로벌 확장 변수",
            "investment_relevance": (
                "글로벌 확장은 단일 시장 의존도를 낮추고 중장기 매출 기회를 넓히는 변수다. "
                "다만 각 지역의 허가, 출시, 판매 확대가 실제 매출로 연결되는 속도가 투자 판단의 핵심이다."
            ),
            "what_to_watch": "주요 해외 지역별 인허가 또는 출시 일정, 적용 범위 확대, 현지 매출 전환 여부를 확인한다.",
        }
    return {
        "title": _short_item(str(source_text), limit=36),
        "group": "기타 촉매",
        "investment_relevance": (
            "해당 촉매는 성장 기대와 실적 반영 가능성을 확인하는 변수다. "
            "현재 리포트에서는 확인 가능한 자료의 범위 안에서만 투자 판단에 연결한다."
        ),
        "what_to_watch": "해당 촉매가 매출, 이익, 시장 확인으로 실제 연결되는지 후속 데이터에서 확인한다.",
    }


def _leading_catalyst_term(source_text: Any) -> str:
    text = str(source_text).strip()
    if not text:
        return ""
    stop_tokens = {"글로벌", "미국", "국내", "해외", "시장", "처방", "판매", "점유율", "매출", "확대", "확보"}
    for token in re.split(r"[\s,/·]+", text):
        cleaned = token.strip("()[]{}'\"")
        if not cleaned or cleaned.lower() in {"ai", "nda", "esg"} or cleaned in stop_tokens:
            continue
        if len(cleaned) <= 1:
            continue
        return _short_item(cleaned, limit=18)
    return ""


def _catalyst_evidence_summary(category: str, matches: list[tuple[str, str]]) -> str:
    count = len(matches)
    suffix = f" 관련 항목 {count}개 확인" if count > 1 else " 관련 항목 확인"
    if category == "commercial_product":
        product_name = _leading_catalyst_term(matches[0][0]) if matches else ""
        prefix = f"{product_name} 중심의 " if product_name else "핵심 제품의 "
        return f"{prefix}처방, 판매, 점유율 확대{suffix}"
    if category == "digital_platform":
        return f"디지털, AI, 플랫폼 사업 추진{suffix}"
    if category == "pipeline":
        return f"신약, 공동연구, 임상 또는 파이프라인 확장{suffix}"
    if category == "global":
        return f"해외 허가, 지역 확장, 적응증 확대{suffix}"
    return f"성장 촉매{suffix}"


def _base_date_from_run_key(run_key: str) -> str:
    suffix = run_key.rsplit("_", 1)[-1]
    if len(suffix) == 8 and suffix.isdigit():
        return f"{suffix[:4]}-{suffix[4:6]}-{suffix[6:]}"
    return ""


def _dict_values(payload: dict[str, Any]) -> list[str]:
    if not isinstance(payload, dict):
        return []
    return [str(value) for _, value in sorted(payload.items()) if value]
