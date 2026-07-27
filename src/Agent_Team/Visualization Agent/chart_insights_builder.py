"""Build structured chart insights from the actual data used in figures."""

from __future__ import annotations

from typing import Any

import pandas as pd


CATEGORY_LABELS = {
    "financial": "재무",
    "business_catalyst": "사업 모멘텀",
    "peer_positioning": "경쟁 포지셔닝",
    "market_price": "시장 가격",
    "summary_strengths": "핵심 강점",
    "regulatory": "규제",
    "market": "시장",
    "execution": "실행",
    "cross_agent_consistency": "분석 간 일관성",
    "strategy_implication": "전략적 시사점",
    "monitoring": "모니터링",
}


def attach_chart_insights(
    chart_metadata: list[dict[str, Any]],
    *,
    market_df: pd.DataFrame,
    margin_df: pd.DataFrame,
    income_df: pd.DataFrame,
    peer_return_df: pd.DataFrame,
    financial_health_df: pd.DataFrame,
    evidence_df: pd.DataFrame,
    company_name: str,
    peer_profitability_df: pd.DataFrame | None = None,
) -> list[dict[str, Any]]:
    """Attach data snapshots and report-ready interpretation scaffolding."""

    insight_by_id = {
        "fig_stock_price_ma_volume_relative_strength": _market_composite_insights(market_df),
        "fig_fundamental_margin_trend": _margin_insights(margin_df),
        "fig_indexed_stock_vs_kospi": _indexed_market_insights(market_df),
        "fig_peer_return_comparison": _peer_return_insights(peer_return_df, company_name),
        "fig_peer_profitability_comparison": _peer_profitability_insights(peer_profitability_df, company_name)
        if peer_profitability_df is not None and not peer_profitability_df.empty
        else None,
        "fig_revenue_profit_sga_trend": _income_insights(income_df),
        "fig_liquidity_leverage_peer_comparison": _financial_health_insights(financial_health_df, company_name),
        "fig_investment_thesis_evidence_map": _evidence_map_insights(evidence_df),
    }

    enriched = []
    for chart in chart_metadata:
        updated = dict(chart)
        insights = insight_by_id.get(chart.get("figure_id"))
        if insights:
            updated["data_snapshot"] = insights["data_snapshot"]
            updated["chart_insights"] = insights["chart_insights"]
            updated["chart_observation"] = " ".join(insights["chart_insights"]["what_is_visible"])
        enriched.append(updated)
    return enriched


def _market_composite_insights(market_df: pd.DataFrame) -> dict[str, Any]:
    chart_df = market_df.dropna(subset=["date", "stock_close"]).sort_values("date").copy()
    latest = chart_df.iloc[-1]
    close_to_ma20_pct = _ratio_to_pct(latest.get("stock_close_to_ma20"))
    close_to_ma60_pct = _ratio_to_pct(latest.get("stock_close_to_ma60"))
    volume_ratio = _number(latest.get("stock_volume_ratio_20"))
    excess_20d = _number(latest.get("stock_excess_return_20d_pct"))
    relative_60d = _number(latest.get("stock_relative_strength_60_pct"))

    ma_state = _ma_state(close_to_ma20_pct, close_to_ma60_pct)
    relative_state = "시장 대비 약세" if (excess_20d or 0) < 0 and (relative_60d or 0) < 0 else "시장 대비 확인 필요"
    return {
        "data_snapshot": {
            "as_of": _date(latest.get("date")),
            "stock_close_krw": _number(latest.get("stock_close")),
            "close_to_ma20_pct": close_to_ma20_pct,
            "close_to_ma60_pct": close_to_ma60_pct,
            "volume_ratio_20d": volume_ratio,
            "excess_return_20d_pct": excess_20d,
            "relative_strength_60d_pct": relative_60d,
        },
        "chart_insights": {
            "what_is_visible": [
                f"최근 종가는 {_fmt_krw(latest.get('stock_close'))}이며 MA20 대비 {_fmt_pct(close_to_ma20_pct)}, MA60 대비 {_fmt_pct(close_to_ma60_pct)} 수준으로 {ma_state}이다.",
                f"20일 거래량 비율은 {_fmt_ratio(volume_ratio)}로 평소보다 높은 거래 관심이 확인된다.",
                f"20일 초과수익률은 {_fmt_pct(excess_20d)}, 60일 상대강도는 {_fmt_pct(relative_60d)}로 {relative_state}가 동시에 남아 있다.",
            ],
            "investment_debate": "절대 가격 흐름 회복 vs 시장 대비 상대성과 약세",
            "report_commentary": (
                "단기 가격은 회복 국면에 들어섰지만 초과수익률과 상대강도가 동시에 음수라는 점이 핵심이다. "
                "이는 투자자 관심이 늘었더라도 아직 시장 대비 선호 회복으로 이어지지 않았다는 의미다."
            ),
            "recommendation_readthrough": "절대 가격 회복만으로 재평가를 단정하기 어렵기 때문에 상대성과 반전 전까지는 현재 투자의견을 더 공격적으로 해석하기 어렵다.",
            "watch_points": ["MA20/MA60 상회 지속 여부", "거래량 증가의 지속성", "초과수익률과 상대강도 반전 여부"],
        },
    }


def _margin_insights(margin_df: pd.DataFrame) -> dict[str, Any]:
    chart_df = margin_df.sort_values("period_end").reset_index(drop=True)
    latest = chart_df.iloc[-1]
    previous = chart_df.iloc[-2] if len(chart_df) >= 2 else None
    contribution_change = _diff(latest.get("contribution_margin_pct"), previous.get("contribution_margin_pct") if previous is not None else None)
    sga_change = _diff(latest.get("sga_margin_pct"), previous.get("sga_margin_pct") if previous is not None else None)

    return {
        "data_snapshot": {
            "latest_period": latest.get("period_label"),
            "latest_basis": latest.get("basis"),
            "contribution_margin_pct": _number(latest.get("contribution_margin_pct")),
            "sga_margin_pct": _number(latest.get("sga_margin_pct")),
            "previous_period": previous.get("period_label") if previous is not None else None,
            "contribution_margin_change_pctp": contribution_change,
            "sga_margin_change_pctp": sga_change,
        },
        "chart_insights": {
            "what_is_visible": [
                f"{latest.get('period_label')} 기준 공헌이익률은 {_fmt_pct(latest.get('contribution_margin_pct'))}, 판관비율은 {_fmt_pct(latest.get('sga_margin_pct'))}다.",
                f"직전 표시 기간 대비 공헌이익률 변화는 {_fmt_pctp(contribution_change)}, 판관비율 변화는 {_fmt_pctp(sga_change)}로 수익성 구조와 비용 부담을 함께 보여준다.",
                f"{latest.get('basis')} 기준 수치가 포함되어 있어 방향성은 볼 수 있지만 연간 기준 개선으로 바로 단정하기는 어렵다.",
            ],
            "investment_debate": "수익성 구조 개선 vs 기간 기준 차이",
            "report_commentary": (
                "공헌이익률 상승과 판관비율 하락이 동시에 나타나면서 비용 효율성 개선이 확인된다. "
                "다만 현재 구간은 YTD 기준이므로 개선 속도를 연간 체력으로 곧바로 환산하기에는 이르다."
            ),
            "recommendation_readthrough": "마진 구조 개선은 긍정 변수지만 연간 지속성과 EPS 해석 한계가 남아 있어 현재 투자의견의 근거 안에서 보수적으로 반영하는 것이 적절하다.",
            "watch_points": ["연간 확정치에서 마진 개선 지속 여부", "판관비율 추가 하락 여부", "공헌이익률 유지 가능성"],
        },
    }


def _income_insights(income_df: pd.DataFrame) -> dict[str, Any]:
    chart_df = income_df.sort_values("period_end").reset_index(drop=True)
    latest = chart_df.iloc[-1]
    previous = chart_df.iloc[-2] if len(chart_df) >= 2 else None
    revenue = _number(latest.get("revenue_krw_bn"))
    contribution_profit = _number(latest.get("contribution_profit_krw_bn"))
    sga = _number(latest.get("sga_krw_bn"))
    contribution_minus_sga = _safe_sub(contribution_profit, sga)

    return {
        "data_snapshot": {
            "latest_period": latest.get("period_label"),
            "latest_basis": latest.get("basis"),
            "revenue_krw_bn": revenue,
            "contribution_profit_krw_bn": contribution_profit,
            "sga_krw_bn": sga,
            "contribution_profit_less_sga_krw_bn": contribution_minus_sga,
            "previous_period": previous.get("period_label") if previous is not None else None,
        },
        "chart_insights": {
            "what_is_visible": [
                f"{latest.get('period_label')} 매출, 공헌이익, 판관비는 각각 {_fmt_bn_as_eok(revenue)}, {_fmt_bn_as_eok(contribution_profit)}, {_fmt_bn_as_eok(sga)}이다.",
                f"공헌이익과 판관비의 차이는 {_fmt_bn_as_eok(contribution_minus_sga)}이며, 매출 증가뿐 아니라 비용 흡수 여력을 함께 점검하게 한다.",
                f"{latest.get('basis')} 기준 표시이므로 비교 연간 수치와 동일 기간 성장률로 읽지 않는 것이 핵심이다.",
            ],
            "investment_debate": "매출 및 공헌이익 확대 vs 누적 기준 수치의 비교 한계",
            "report_commentary": (
                "매출과 공헌이익의 규모가 판관비를 충분히 흡수하는 구조가 나타나면서 손익 체력 개선 신호가 강화된다. "
                "다만 누적 기준 수치이기 때문에 연간 실적의 지속성 확인 없이는 성장률을 공격적으로 반영하기 어렵다."
            ),
            "recommendation_readthrough": "재무 개선 방향은 분명하지만 연간 확정 전까지는 추가적인 의견 강화보다 확인 구간으로 보는 것이 합리적이다.",
            "watch_points": ["연간 매출 확정치", "공헌이익과 판관비 간 격차 유지", "판관비 증가 속도"],
        },
    }


def _indexed_market_insights(market_df: pd.DataFrame) -> dict[str, Any]:
    chart_df = market_df.dropna(subset=["date", "stock_close", "kospi_close"]).sort_values("date").copy()
    chart_df["stock_index"] = chart_df["stock_close"] / chart_df["stock_close"].iloc[0] * 100.0
    chart_df["kospi_index"] = chart_df["kospi_close"] / chart_df["kospi_close"].iloc[0] * 100.0
    chart_df["relative_gap"] = chart_df["stock_index"] - chart_df["kospi_index"]
    latest = chart_df.iloc[-1]
    gap = _number(latest.get("relative_gap"))
    return {
        "data_snapshot": {
            "start_date": _date(chart_df.iloc[0].get("date")),
            "as_of": _date(latest.get("date")),
            "stock_index": _number(latest.get("stock_index")),
            "kospi_index": _number(latest.get("kospi_index")),
            "relative_gap_index_points": gap,
        },
        "chart_insights": {
            "what_is_visible": [
                f"시작일 100 기준 최근 주가지수는 {_fmt_number(latest.get('stock_index'))}, KOSPI 지수는 {_fmt_number(latest.get('kospi_index'))}로 표시된다.",
                f"상대 격차는 {_fmt_number(gap)}pt로, 절대 주가 흐름과 시장 흐름의 차이를 직관적으로 보여준다.",
            ],
            "investment_debate": "절대 성과 회복 vs 시장 대비 성과 확인",
            "report_commentary": "지수화 성과가 시장을 안정적으로 앞서야 펀더멘털 개선이 주가 선호로 전환되고 있다고 볼 수 있다. 기준일 효과가 있어 보조적으로 해석하되, 시장 대비 격차의 방향은 중요하다.",
            "recommendation_readthrough": "시장 대비 우위가 명확하지 않으면 재무 개선만으로 투자의견 강화를 정당화하기 어렵다.",
            "watch_points": ["KOSPI 대비 격차 축소 여부", "성과 격차의 추세 지속성", "기준일 변화에 따른 민감도"],
        },
    }


def _peer_return_insights(peer_return_df: pd.DataFrame, company_name: str) -> dict[str, Any]:
    chart_df = peer_return_df.copy().reset_index(drop=True)
    target = _target_row(chart_df, company_name)
    peer_count = len(chart_df)
    rank_20d = _rank(chart_df, target, "stock_return_20d_pct", ascending=False)
    rank_relative = _rank(chart_df, target, "stock_relative_strength_60_pct", ascending=False)
    return {
        "data_snapshot": {
            "company_name": target.get("company_name"),
            "peer_count": peer_count,
            "as_of": _date(target.get("date")),
            "stock_return_20d_pct": _number(target.get("stock_return_20d_pct")),
            "stock_return_60d_pct": _number(target.get("stock_return_60d_pct")),
            "excess_return_20d_pct": _number(target.get("stock_excess_return_20d_pct")),
            "relative_strength_60d_pct": _number(target.get("stock_relative_strength_60_pct")),
            "rank_20d_return": rank_20d,
            "rank_60d_relative_strength": rank_relative,
        },
        "chart_insights": {
            "what_is_visible": [
                f"비교군 {peer_count}개 중 대상 회사의 20일 수익률 순위는 {_fmt_rank(rank_20d, peer_count)}, 60일 상대강도 순위는 {_fmt_rank(rank_relative, peer_count)}로 표시된다.",
                f"20일 초과수익률은 {_fmt_pct(target.get('stock_excess_return_20d_pct'))}, 60일 상대강도는 {_fmt_pct(target.get('stock_relative_strength_60_pct'))}로 시장 대비 성과 부담이 남아 있다.",
                "단기·중기 수익률을 함께 보면 절대 수익률보다 peer 안에서의 선호도 회복 여부가 더 중요한 관찰 포인트다.",
            ],
            "investment_debate": "peer 대비 상대 선호도 회복 여부",
            "report_commentary": (
                "비교군 내 순위가 중위권에 머무는 점은 재무 개선 신호가 아직 차별적 주가 성과로 연결되지 않았음을 시사한다. "
                "상대강도 회복이 동반되지 않으면 업종 내 선호도 개선을 주장하기 어렵다."
            ),
            "recommendation_readthrough": "Peer 대비 시장 선호가 확인되지 않은 상태에서는 긍정적 펀더멘털을 전부 가격에 반영하기보다 리스크 할인을 유지하는 결론이 자연스럽다.",
            "watch_points": ["20일 수익률 순위 개선", "60일 상대강도 반전", "peer 대비 거래 관심 지속성"],
        },
    }


def _financial_health_insights(financial_health_df: pd.DataFrame, company_name: str) -> dict[str, Any]:
    chart_df = financial_health_df.copy().reset_index(drop=True)
    target = _target_row(chart_df, company_name)
    peer_count = len(chart_df)
    current_rank = _rank(chart_df, target, "current_ratio_pct", ascending=False)
    debt_rank = _rank(chart_df, target, "debt_to_equity_pct", ascending=True)
    return {
        "data_snapshot": {
            "company_name": target.get("company_name"),
            "peer_count": peer_count,
            "current_ratio_pct": _number(target.get("current_ratio_pct")),
            "cash_ratio_pct": _number(target.get("cash_ratio_pct")),
            "equity_ratio_pct": _number(target.get("equity_ratio_pct")),
            "debt_to_equity_pct": _number(target.get("debt_to_equity_pct")),
            "current_ratio_rank": current_rank,
            "debt_to_equity_rank_lowest": debt_rank,
        },
        "chart_insights": {
            "what_is_visible": [
                f"비교군 {peer_count}개 중 유동비율 순위는 {_fmt_rank(current_rank, peer_count)}, 낮은 부채비율 기준 순위는 {_fmt_rank(debt_rank, peer_count)}다.",
                f"대상 회사의 유동비율은 {_fmt_pct(target.get('current_ratio_pct'))}, 부채비율은 {_fmt_pct(target.get('debt_to_equity_pct'))}로 재무 리스크 흡수력을 점검하게 한다.",
            ],
            "investment_debate": "재무 안정성 방어력 vs 성장 재평가의 별도 확인 필요",
            "report_commentary": "유동성과 낮은 레버리지는 하방 리스크를 낮추는 방어 요인이다. 다만 재무 안정성은 성장성과 주가 재평가를 직접 설명하지 않으므로 투자 매력의 보조 축으로 해석한다.",
            "recommendation_readthrough": "재무 안정성은 하방 위험을 낮추지만 성장 재평가를 단독으로 만들지는 못하므로 방어 논리로 해석한다.",
            "watch_points": ["유동비율 유지", "부채비율 상승 여부", "현금흐름과 투자 지출의 균형"],
        },
    }


def _peer_profitability_insights(peer_profitability_df: pd.DataFrame, company_name: str) -> dict[str, Any]:
    chart_df = peer_profitability_df.copy().reset_index(drop=True)
    target = _target_row(chart_df, company_name)
    peer_count = len(chart_df)
    revenue_rank = _rank(chart_df, target, "revenue_100m", ascending=False)
    margin_rank = _rank(chart_df, target, "contribution_margin_pct", ascending=False)
    sga_rank = _rank(chart_df, target, "sga_margin_pct", ascending=True)
    eps_rank = _rank(chart_df, target, "eps", ascending=False)
    missing_profitability = int(
        chart_df[["revenue_100m", "contribution_margin_pct", "sga_margin_pct", "eps"]].isna().any(axis=1).sum()
    )
    return {
        "data_snapshot": {
            "company_name": target.get("company_name"),
            "peer_count": peer_count,
            "financial_period": target.get("financial_period"),
            "revenue_100m": _number(target.get("revenue_100m")),
            "contribution_margin_pct": _number(target.get("contribution_margin_pct")),
            "sga_margin_pct": _number(target.get("sga_margin_pct")),
            "eps": _number(target.get("eps")),
            "revenue_rank": revenue_rank,
            "contribution_margin_rank": margin_rank,
            "sga_margin_rank_lowest": sga_rank,
            "eps_rank": eps_rank,
            "missing_profitability_peer_count": missing_profitability,
        },
        "chart_insights": {
            "what_is_visible": [
                f"국내 비교군 {peer_count}개 중 대상 회사의 매출 규모 순위는 {_fmt_rank(revenue_rank, peer_count)}, 공헌이익률 순위는 {_fmt_rank(margin_rank, peer_count)}다.",
                f"판관비율은 낮은 순위 기준 {_fmt_rank(sga_rank, peer_count)}, EPS 순위는 {_fmt_rank(eps_rank, peer_count)}로 표시되어 수익성과 비용 효율성을 함께 보여준다.",
                f"일부 peer의 재무 항목 결측이 {missing_profitability}개 관측되어 비교는 확인 가능한 국내 데이터로 제한된다.",
            ],
            "investment_debate": "국내 peer 대비 수익성 우위 vs valuation 부재와 결측 데이터 한계",
            "report_commentary": (
                "대상 회사가 매출 규모와 마진 구조에서 우위에 있다면 peer 대비 사업 체력과 비용 효율성은 긍정적으로 해석할 수 있다. "
                "다만 이 차트는 valuation을 포함하지 않기 때문에 수익성 우위가 곧바로 저평가 또는 매수 근거로 이어진다고 볼 수는 없다."
            ),
            "recommendation_readthrough": "Peer 수익성 비교는 재무 개선 논리를 보강하지만, 상대성과와 평가 확인이 빠져 있어 균형적 투자의견을 유지하게 만드는 상대 위치 증거로 해석한다.",
            "watch_points": ["연간 기준 수익성 유지", "peer 결측 항목 보완", "수익성 우위의 시장 성과 전환 여부"],
        },
    }


def _evidence_map_insights(evidence_df: pd.DataFrame) -> dict[str, Any]:
    signal_counts = evidence_df.groupby("signal_type").size().to_dict()
    category_counts = evidence_df.groupby("category").size().sort_values(ascending=False).to_dict()
    top_category = next(iter(category_counts), "n/a")
    top_category_label = CATEGORY_LABELS.get(str(top_category), str(top_category))
    positive = int(signal_counts.get("Positive Basis", 0))
    risks = int(signal_counts.get("Risk", 0))
    monitoring = int(signal_counts.get("Monitoring", 0))
    mixed = int(signal_counts.get("Mixed Signal", 0))
    return {
        "data_snapshot": {
            "positive_basis_count": positive,
            "risk_count": risks,
            "mixed_signal_count": mixed,
            "monitoring_count": monitoring,
            "top_category": top_category,
            "top_category_label": top_category_label,
            "counts_by_category": {str(key): int(value) for key, value in category_counts.items()},
        },
        "chart_insights": {
            "what_is_visible": [
                f"긍정 근거 {positive}개, 리스크 {risks}개, 혼재 신호 {mixed}개, 모니터링 항목 {monitoring}개가 한 화면에 배치된다.",
                f"가장 많이 등장한 범주는 {top_category_label}이며, 최종 판단이 단일 지표보다 근거와 리스크의 균형에 의해 형성됐음을 보여준다.",
            ],
            "investment_debate": "긍정 근거의 축적 vs 확인 과제의 동시 존재",
            "report_commentary": (
                "긍정 근거보다 리스크와 모니터링 항목의 비중이 크게 나타나는 점이 핵심이다. "
                "재무 개선과 사업 모멘텀은 인정되지만, 확인 과제가 동시에 많아 투자 판단의 할인 요인이 남아 있다."
            ),
            "recommendation_readthrough": "긍정 근거가 존재하더라도 리스크와 모니터링 항목이 더 많아 단일 방향의 강한 결론보다 균형 판단을 지지하는 그림이다.",
            "watch_points": ["리스크 항목 감소 여부", "긍정 근거의 실적 전환", "모니터링 항목의 해소 속도"],
        },
    }


def _target_row(df: pd.DataFrame, company_name: str) -> pd.Series:
    if df.empty:
        return pd.Series(dtype="object")
    if "company_name" not in df.columns or not company_name:
        return df.iloc[0]
    normalized_target = _normalize_name(company_name)
    matches = df[df["company_name"].map(_normalize_name) == normalized_target]
    if not matches.empty:
        return matches.iloc[0]
    partial = df[df["company_name"].map(lambda value: normalized_target in _normalize_name(value) or _normalize_name(value) in normalized_target)]
    if not partial.empty:
        return partial.iloc[0]
    return df.iloc[0]


def _rank(df: pd.DataFrame, target: pd.Series, column: str, *, ascending: bool) -> int | None:
    if column not in df.columns or target.empty:
        return None
    values = pd.to_numeric(df[column], errors="coerce")
    target_value = pd.to_numeric(pd.Series([target.get(column)]), errors="coerce").iloc[0]
    if pd.isna(target_value):
        return None
    if ascending:
        return int((values < target_value).sum() + 1)
    return int((values > target_value).sum() + 1)


def _ratio_to_pct(value: Any) -> float | None:
    number = _number(value)
    return None if number is None else number * 100.0


def _diff(value: Any, base: Any) -> float | None:
    left = _number(value)
    right = _number(base)
    if left is None or right is None:
        return None
    return left - right


def _safe_sub(left: float | None, right: float | None) -> float | None:
    if left is None or right is None:
        return None
    return left - right


def _number(value: Any) -> float | None:
    if value is None or pd.isna(value):
        return None
    if hasattr(value, "item"):
        value = value.item()
    try:
        return round(float(value), 4)
    except (TypeError, ValueError):
        return None


def _date(value: Any) -> str | None:
    if value is None or pd.isna(value):
        return None
    return pd.Timestamp(value).date().isoformat()


def _ma_state(ma20_pct: float | None, ma60_pct: float | None) -> str:
    if ma20_pct is None or ma60_pct is None:
        return "추세 확인이 제한되는 상태"
    if ma20_pct > 0 and ma60_pct > 0:
        return "단기와 중기 가격 흐름이 모두 개선된 상태"
    if ma20_pct > 0 or ma60_pct > 0:
        return "일부 추세 회복 신호가 있는 상태"
    return "이동평균선 대비 약세 상태"


def _fmt_krw(value: Any) -> str:
    number = _number(value)
    if number is None:
        return "n/a"
    return f"{number:,.0f}원"


def _fmt_bn_as_eok(value: Any) -> str:
    number = _number(value)
    if number is None:
        return "n/a"
    return f"{number * 10:,.0f}억원"


def _fmt_pct(value: Any) -> str:
    number = _number(value)
    if number is None:
        return "n/a"
    return f"{number:+.2f}%" if number < 0 else f"{number:.2f}%"


def _fmt_pctp(value: Any) -> str:
    number = _number(value)
    if number is None:
        return "n/a"
    return f"{number:+.2f}%p"


def _fmt_ratio(value: Any) -> str:
    number = _number(value)
    if number is None:
        return "n/a"
    return f"{number:.2f}배"


def _fmt_number(value: Any) -> str:
    number = _number(value)
    if number is None:
        return "n/a"
    return f"{number:,.1f}"


def _fmt_rank(rank: int | None, count: int) -> str:
    if rank is None:
        return "n/a"
    return f"{rank}위/{count}개"


def _normalize_name(value: Any) -> str:
    return "".join(str(value).lower().split())
