"""Strategy-aware chart enrichment for broker-report chart selection."""

from __future__ import annotations

import re
from typing import Any


STRATEGY_FIELD_LABELS = {
    "final_recommendation.summary": "투자의견 요약",
    "final_rationale.why_buy_hold_sell": "최종 판단 근거",
    "investment_thesis.thesis_1": "재무 투자포인트",
    "investment_thesis.thesis_2": "사업 모멘텀",
    "investment_thesis.thesis_3": "보수적 판단 근거",
    "financial_view.revenue": "매출",
    "financial_view.profitability": "수익성",
    "financial_view.cash_flow": "현금흐름",
    "financial_view.balance_sheet": "재무 안정성",
    "market_price_view.price_trend": "가격 흐름",
    "market_price_view.volume": "거래 활성도",
    "market_price_view.relative_strength": "상대성과",
    "market_price_view.market_interpretation": "시장 해석",
    "peer_competitor_positioning.target_relative_strength": "경쟁사 대비 상대강도",
    "peer_competitor_positioning.peer_based_investment_implication": "peer 기반 투자 시사점",
}

ROLE_FIELDS = {
    "decision_evidence": [
        "final_recommendation.summary",
        "final_rationale.why_buy_hold_sell",
        "investment_thesis.thesis_1",
        "investment_thesis.thesis_3",
    ],
    "financial_income": [
        "financial_view.revenue",
        "financial_view.profitability",
        "financial_view.cash_flow",
        "investment_thesis.thesis_1",
    ],
    "financial_margin": [
        "financial_view.profitability",
        "financial_view.revenue",
        "investment_thesis.thesis_1",
        "risk_view.financial_risks",
    ],
    "financial_stability": [
        "financial_view.cash_flow",
        "financial_view.balance_sheet",
        "peer_competitor_positioning.peer_based_investment_implication",
    ],
    "market_composite": [
        "market_price_view.price_trend",
        "market_price_view.volume",
        "market_price_view.relative_strength",
        "market_price_view.market_interpretation",
    ],
    "market_indexed": [
        "market_price_view.price_trend",
        "market_price_view.relative_strength",
        "market_price_view.market_interpretation",
    ],
    "peer_market": [
        "market_price_view.relative_strength",
        "peer_competitor_positioning.target_relative_strength",
        "peer_competitor_positioning.peer_based_investment_implication",
    ],
    "peer_profitability": [
        "financial_view.revenue",
        "financial_view.profitability",
        "peer_competitor_positioning.target_relative_strength",
        "peer_competitor_positioning.peer_based_investment_implication",
    ],
}

ROLE_BASE_SCORE = {
    "decision_evidence": 72,
    "financial_income": 70,
    "financial_margin": 68,
    "market_composite": 66,
    "peer_market": 58,
    "peer_profitability": 60,
    "financial_stability": 54,
    "market_indexed": 48,
}

UNSUPPORTED_VISIBLE_TERMS = ("OPM", "ROE", "ROA", "P/E", "P/B", "PER", "PBR")


def enrich_chart_metadata_with_strategy(
    chart_metadata: list[dict[str, Any]],
    strategy_report: dict[str, Any],
    *,
    company_name: str = "",
) -> list[dict[str, Any]]:
    """Add report-selection and interpretation metadata using Strategy output."""

    enriched: list[dict[str, Any]] = []
    for chart in chart_metadata:
        role = _infer_report_role(chart)
        fields = _available_fields(strategy_report, ROLE_FIELDS.get(role, ["final_recommendation.summary"]))
        support_summary = _support_summary(fields)
        enriched_chart = dict(chart)
        enriched_chart.update(
            {
                "report_chart_role": role,
                "broker_report_use_case": _use_case_for_role(role),
                "strategy_support_fields": fields,
                "strategy_support_summary": support_summary,
                "selection_reason": _selection_reason(role, support_summary),
                "analyst_takeaway": _analyst_takeaway(role, strategy_report, company_name=company_name),
                "interpretation_limit": _interpretation_limit(role, chart),
                "writer_priority_score": _priority_score(role, fields, strategy_report, chart),
                "recommended_for_report": bool(fields),
            }
        )
        enriched.append(enriched_chart)

    ranked = sorted(
        enumerate(enriched),
        key=lambda item: (-int(item[1].get("writer_priority_score", 0)), item[0]),
    )
    for rank, (_, chart) in enumerate(ranked, start=1):
        chart["recommended_report_rank"] = rank
    return enriched


def _infer_report_role(chart: dict[str, Any]) -> str:
    figure_id = str(chart.get("figure_id", "")).lower()
    title = str(chart.get("title", "")).lower()
    section = str(chart.get("section_recommendation", "")).lower()
    text = f"{figure_id} {title} {section}"
    if "investment_thesis" in text or "evidence_map" in text or "decision" in text:
        return "decision_evidence"
    if "peer_profitability" in text or ("peer" in text and ("profitability" in text or "revenue" in text or "eps" in text)):
        return "peer_profitability"
    if "revenue_profit_sga" in text or ("revenue" in text and "sga" in text):
        return "financial_income"
    if "fundamental_margin" in text or "margin" in text:
        return "financial_margin"
    if "liquidity" in text or "leverage" in text or "stability" in text:
        return "financial_stability"
    if "peer_return" in text or ("peer" in text and "relative" in text):
        return "peer_market"
    if "indexed" in text or "kospi" in text:
        return "market_indexed"
    if "stock_price" in text or "volume" in text or "price" in text:
        return "market_composite"
    return "decision_evidence"


def _use_case_for_role(role: str) -> dict[str, str]:
    cases = {
        "decision_evidence": {
            "section": "Investment Summary / Final Rationale",
            "role": "투자 판단의 긍정 근거와 리스크 균형을 한 장에서 설명",
        },
        "financial_income": {
            "section": "Financial View",
            "role": "매출, 공헌이익, 판관비의 금액 흐름으로 재무 개선 논리를 설명",
        },
        "financial_margin": {
            "section": "Financial View",
            "role": "공헌이익률과 판관비율로 수익성 구조와 비용 효율성을 설명",
        },
        "financial_stability": {
            "section": "Financial View / Peer View",
            "role": "유동성과 레버리지 비교로 재무 안정성의 방어력을 설명",
        },
        "market_composite": {
            "section": "Market View",
            "role": "가격 추세, 거래량, 상대성과를 함께 보여 주가 판단의 균형을 설명",
        },
        "market_indexed": {
            "section": "Market View",
            "role": "지수화 주가와 시장 성과 비교로 상대성과 방향을 설명",
        },
        "peer_market": {
            "section": "Peer / Market View",
            "role": "동일 기준 peer 수익률과 상대강도 비교로 시장 내 위치를 설명",
        },
        "peer_profitability": {
            "section": "Peer / Profitability View",
            "role": "국내 peer 대비 매출 규모와 수익성 구조의 상대 위치를 설명",
        },
    }
    return cases.get(role, cases["decision_evidence"])


def _available_fields(strategy_report: dict[str, Any], fields: list[str]) -> list[str]:
    available = []
    for field in fields:
        value = _get_nested(strategy_report, field)
        if _has_value(value):
            available.append(field)
    return available


def _support_summary(fields: list[str]) -> str:
    labels = [STRATEGY_FIELD_LABELS.get(field, field.rsplit(".", 1)[-1].replace("_", " ")) for field in fields]
    if not labels:
        return "주요 투자 판단을 설명하는 일반 차트"
    return ", ".join(labels[:4])


def _selection_reason(role: str, support_summary: str) -> str:
    reasons = {
        "decision_evidence": "최종 투자의견을 긍정 근거와 리스크의 균형으로 설명할 수 있어 리포트 핵심 차트로 우선 사용한다.",
        "financial_income": "재무 개선 논리를 금액 기준으로 보여 주며, 매출과 비용 구조가 투자의견에 미치는 영향을 설명하기 적합하다.",
        "financial_margin": "수익성 구조와 비용 효율성 개선 여부를 직접 보여 주어 재무 투자포인트 해석에 적합하다.",
        "financial_stability": "재무 안정성은 하방 리스크를 낮추는 방어 논리이므로 peer 비교가 필요한 경우 사용한다.",
        "market_composite": "주가 추세 개선과 상대성과 약세를 동시에 보여 주어 의견 상향 여부를 판단하는 시장 신호 해석에 적합하다.",
        "market_indexed": "시장 대비 성과를 직관적으로 보여 주지만 기준일 영향이 있어 보조 차트로 사용한다.",
        "peer_market": "peer 대비 수익률과 상대강도 비교를 통해 시장 내 선호도와 상대 약세 리스크를 설명하기 적합하다.",
        "peer_profitability": "국내 peer 대비 매출 규모와 수익성 구조를 보여 주어 상대 매력도와 남은 확인 과제를 설명하기 적합하다.",
    }
    return f"{reasons.get(role, reasons['decision_evidence'])} 연결 필드: {support_summary}."


def _analyst_takeaway(role: str, strategy_report: dict[str, Any], *, company_name: str = "") -> str:
    recommendation = _recommendation(strategy_report)
    subject = company_name.strip() or "대상 회사"
    if role == "decision_evidence":
        return (
            f"이 차트는 {subject}의 {recommendation} 판단이 단일 긍정 요인보다 재무 개선, 사업 모멘텀, 리스크 요인의 균형에서 나온 결론임을 보여준다. "
            "긍정 근거가 존재하더라도 규제, 경쟁, 시장 상대성과 같은 확인 과제가 함께 남아 있으면 리포트의 결론은 단일 긍정 논리보다 균형 판단에 가까워진다. "
            "따라서 본문에서는 이 차트를 최종 판단의 근거 구조를 설명하는 요약 자료로 사용하는 것이 적절하다."
        )
    if role == "financial_income":
        return (
            "매출과 공헌이익, 판관비 흐름은 재무 개선 신호가 실제 손익 구조에서 어떻게 나타나는지 보여준다. "
            f"재무 개선이 {recommendation} 유지의 긍정 축이더라도, 누적 기간과 연간 기준이 섞여 있으면 성장률을 단정하기 어렵다. "
            "따라서 이 차트는 재무 방향성은 긍정적이나 연간 확정치 확인 전까지 보수적 판단이 필요하다는 논리를 뒷받침한다."
        )
    if role == "financial_margin":
        return (
            "공헌이익률과 판관비율은 매출 성장보다 한 단계 더 안쪽의 수익성 구조를 보여주는 차트다. "
            "공헌이익률 개선과 판관비 부담 완화가 함께 나타나면 비용 효율성 개선 신호로 해석할 수 있지만, 누적 기준 수치를 연간 수익성 개선으로 바로 확장해서는 안 된다. "
            f"따라서 이 차트는 재무 개선의 질을 설명하되 {recommendation} 의견을 유지하는 해석 제한도 함께 제시하는 데 적합하다."
        )
    if role == "financial_stability":
        return (
            "유동성과 레버리지 비교는 실적 모멘텀과 별개로 재무 리스크 흡수 능력을 점검하는 자료다. "
            "단기 유동성이나 낮은 레버리지는 하방 리스크를 낮추는 근거가 될 수 있지만, 성장성이나 주가 재평가를 단독으로 설명하지는 못한다. "
            f"따라서 이 차트는 {recommendation} 판단에서 긍정 근거를 보강하되 투자의견 상향의 직접 근거보다는 방어적 근거로 사용하는 편이 적절하다."
        )
    if role == "market_composite":
        return (
            "가격, 이동평균, 거래량, 상대성과를 함께 보면 절대 가격 흐름과 시장 대비 성과가 같은 방향인지 확인할 수 있다. "
            "주가가 주요 이동평균 위에 있고 거래량이 늘어나는 흐름은 관심 회복의 신호지만, 초과수익률과 상대강도가 약하면 시장 대비 설득력은 제한된다. "
            f"따라서 이 차트는 가격 추세 회복과 상대성과 약세라는 투자 논쟁을 보여 주며, {recommendation} 결론에서 의견 상향을 논의하기에는 추가 확인이 필요하다는 결론과 연결된다."
        )
    if role == "market_indexed":
        return (
            "지수화 성과 비교는 대상 주식의 절대 흐름이 시장 흐름을 실제로 앞섰는지 보여 주는 보조 자료다. "
            "시장 대비 격차가 확대되거나 축소되는 방향은 투자심리 판단에 유용하지만, 기준일 선택에 따라 시각적 차이가 달라질 수 있다. "
            f"따라서 이 차트는 {recommendation} 의견의 시장 성과 부분을 보조하되 펀더멘털 결론으로 직접 연결하지 않는다."
        )
    if role == "peer_market":
        return (
            "Peer 수익률과 상대강도 비교는 대상 회사의 시장 내 선호도가 동종 그룹 안에서 어느 위치에 있는지 보여준다. "
            "절대 가격이 개선되더라도 peer나 시장 대비 성과가 약하면 투자자 수급의 확신이 아직 부족하다는 해석이 가능하다. "
            f"따라서 이 차트는 {recommendation} 판단에서 상대성과 리스크를 설명하는 핵심 근거로 사용한다."
        )
    if role == "peer_profitability":
        return (
            "국내 peer 대비 매출 규모와 공헌이익률, 판관비율, EPS를 함께 보면 대상 회사의 재무 체력과 비용 효율성의 상대 위치를 확인할 수 있다. "
            "재무 지표상 우위가 확인되더라도 이 차트는 가치평가 지표와 글로벌 peer 비교를 포함하지 않으므로 할인 또는 프리미엄을 단정하는 근거는 아니다. "
            f"따라서 이 차트는 {recommendation} 판단에서 긍정 요인을 보강하되, 시장 확인과 가치평가 데이터 부재를 함께 고려하는 균형 근거로 사용한다."
        )
    return (
        f"이 차트는 {recommendation} 판단의 근거와 확인 과제를 함께 보여준다. 차트 해석은 본문 투자 논리의 범위 안에서 사용한다."
    )


def _interpretation_limit(role: str, chart: dict[str, Any]) -> str:
    limitations = chart.get("data_limitations", [])
    if isinstance(limitations, str):
        limitations = [limitations]
    base_items = [
        _compact_text(item)
        for item in limitations
        if _compact_text(item) and not _has_unsupported_visible_term(_compact_text(item))
    ]
    base = " ".join(base_items)
    role_limits = {
        "decision_evidence": "항목 수는 투자 판단 근거를 구조화한 결과이며 독립적인 투자 등급 산식이 아니다.",
        "financial_income": "누적 기간 수치와 연간 수치를 동일 기간 YoY로 단정하지 않는다.",
        "financial_margin": "공헌이익률과 판관비율을 별도 근거 없는 수익성 또는 밸류에이션 지표로 대체 해석하지 않는다.",
        "financial_stability": "유동성 및 레버리지 지표만으로 성장성, 수익성, 목표주가를 단정하지 않는다.",
        "market_composite": "시장 데이터는 가격과 거래 흐름을 보여 주며 펀더멘털 개선의 직접 증거가 아니다.",
        "market_indexed": "지수화 기준일 선택에 따라 성과 격차의 시각적 크기는 달라질 수 있다.",
        "peer_market": "Peer 비교는 현재 수집된 비교군과 동일 기준일 데이터로 제한된다.",
        "peer_profitability": "국내 peer 비교로 제한하며, 가치평가 지표와 글로벌 peer 비교 또는 업종 평균 비교로 확장하지 않는다.",
    }
    role_limit = role_limits.get(role, "차트 해석은 chart_manifest.json에 명시된 범위로 제한한다.")
    if base and role_limit not in base and not _has_overlapping_limit(base_items, role_limit):
        return f"{base} {role_limit}"
    return base or role_limit


def _priority_score(
    role: str,
    fields: list[str],
    strategy_report: dict[str, Any],
    chart: dict[str, Any],
) -> int:
    score = ROLE_BASE_SCORE.get(role, 40) + len(fields) * 6
    if _recommendation(strategy_report):
        score += 6
    if chart.get("writer_allowed_interpretation"):
        score += 4
    if chart.get("caption"):
        score += 3
    if role in {"financial_income", "financial_margin"} and _has_value(_get_nested(strategy_report, "financial_view.profitability")):
        score += 5
    if role in {"market_composite", "peer_market"} and _has_value(_get_nested(strategy_report, "market_price_view.relative_strength")):
        score += 5
    if role == "peer_profitability" and _has_value(_get_nested(strategy_report, "peer_competitor_positioning.peer_based_investment_implication")):
        score += 8
    if role == "decision_evidence" and _has_value(_get_nested(strategy_report, "final_rationale.why_buy_hold_sell")):
        score += 8
    return int(score)


def _recommendation(strategy_report: dict[str, Any]) -> str:
    value = _get_nested(strategy_report, "final_recommendation.opinion")
    if value:
        return str(value)
    summary = str(_get_nested(strategy_report, "final_recommendation.summary") or "")
    match = re.search(r"\b(Buy|Hold|Sell|Neutral)\b", summary, re.IGNORECASE)
    return match.group(1).title() if match else "투자의견"


def _get_nested(payload: dict[str, Any], path: str) -> Any:
    current: Any = payload
    for part in path.split("."):
        if not isinstance(current, dict):
            return None
        current = current.get(part)
    return current


def _has_value(value: Any) -> bool:
    if value is None:
        return False
    if isinstance(value, str):
        return bool(value.strip())
    if isinstance(value, (list, tuple, set, dict)):
        return bool(value)
    return True


def _compact_text(value: Any) -> str:
    return " ".join(str(value).split())


def _has_overlapping_limit(base_items: list[str], role_limit: str) -> bool:
    role_tokens = _limit_tokens(role_limit)
    if not role_tokens:
        return False
    for item in base_items:
        item_tokens = _limit_tokens(item)
        if not item_tokens:
            continue
        overlap = len(role_tokens & item_tokens) / max(len(role_tokens), 1)
        if overlap >= 0.45:
            return True
    return False


def _limit_tokens(text: str) -> set[str]:
    normalized = re.sub(r"[^0-9A-Za-z가-힣]+", " ", text)
    stopwords = {"이다", "한다", "있는", "없는", "또는", "그리고", "차트", "해석", "직접"}
    return {token for token in normalized.split() if len(token) >= 2 and token not in stopwords}


def _has_unsupported_visible_term(text: str) -> bool:
    return any(term in text for term in UNSUPPORTED_VISIBLE_TERMS)
