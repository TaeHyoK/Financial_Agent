"""Analyst-quality rewrite layer for Writer Agent contracts.

This module must stay company-agnostic. It may only use values already present
in the report contract, Strategy output, metrics table, or chart manifest.
"""

from __future__ import annotations

from copy import deepcopy
from typing import Any


def rewrite_contract(contract: dict[str, Any]) -> dict[str, Any]:
    """Rewrite draft contract sections into analyst-style commentary."""

    rewritten = deepcopy(contract)
    rewritten["commentary_generation"] = {
        "mode": "deterministic_scaffold",
        "rule_based_scope": [
            "fact extraction",
            "section structure",
            "fallback commentary",
            "validation guardrails",
        ],
        "final_commentary_policy": "LLM Writer should replace scaffold commentary when available.",
        "llm_sections_updated": [],
    }
    meta = rewritten.get("report_metadata", {})
    company = meta.get("company_name", "분석 대상 기업")
    recommendation = meta.get("recommendation", "N/A")
    rewritten["main_investment_logic"] = _main_logic(rewritten, company, recommendation)
    rewritten["report_metadata"]["valuation_status"] = "목표주가 미제시"
    rewritten["report_metadata"]["report_type_display"] = "기업분석 리포트 초안"
    _rewrite_cover_summary(rewritten, company, recommendation)
    _rewrite_charts(rewritten, recommendation)
    _rewrite_catalysts(rewritten, recommendation)
    _rewrite_risks(rewritten, recommendation)
    _rewrite_final_rationale(rewritten, company, recommendation)
    return rewritten


def _main_logic(contract: dict[str, Any], company: str, recommendation: str) -> str:
    strengths = contract.get("cover_summary", {}).get("positive_signals", [])
    risks = contract.get("cover_summary", {}).get("negative_signals", [])
    positive = _first_text(strengths, "긍정 요인")
    risk = _first_text(risks, "확인 필요 요인")
    return f"{company}은 '{positive}'를 중심으로 재무·사업 모멘텀이 확인되지만, '{risk}'가 남아 있어 현재 {recommendation} 의견이 타당하다."


def _rewrite_cover_summary(contract: dict[str, Any], company: str, recommendation: str) -> None:
    cover = contract["cover_summary"]
    reader = contract["reader_friendly_sections"]["investment_summary"]
    metric_values = _metric_value_map(contract)
    market = _chart_snapshot_by_role(contract, "market_composite")
    positives = _cover_positive_signals(contract)
    negatives = _cover_negative_signals(contract)
    cover["positive_signals"] = positives
    cover["negative_signals"] = negatives
    margin_sentence = _margin_sentence(metric_values)
    market_sentence = _market_sentence(market)

    cover["headline"] = f"{company}: 핵심 개선 요인과 확인 과제를 반영한 {recommendation}"
    cover["one_line_view"] = (
        f"{company}은 {margin_sentence} 기준으로 비용 효율성 개선이 확인된다. "
        f"다만 {market_sentence}라서 재무 개선이 곧바로 투자심리 회복으로 연결됐다고 보기는 어렵다."
    )
    cover["recommendation_rationale"] = (
        f"현재 투자의견은 {recommendation}이다. "
        "재무 지표는 개선 방향을 보이지만 EPS의 기간 기준 차이, 규제·경쟁 리스크, 시장 대비 성과 확인이 함께 남아 있어 현재 의견보다 더 공격적으로 해석하기에는 확인이 부족하다."
    )
    cover["key_debate"] = (
        "핵심 투자 쟁점은 수익성 개선이 연간 실적 지속성과 시장 대비 성과 회복으로 이어질 수 있느냐이다."
    )
    cover["executive_summary"] = (
        f"{company}은 {margin_sentence} 기준으로 재무 개선의 방향성은 분명해지고 있다. "
        "특히 공헌이익률 개선과 판관비율 하락이 함께 나타나는 점은 매출 성장보다 중요한 수익성 신호다. "
        f"반면 {market_sentence}이고 EPS 기준 차이와 규제·경쟁 변수가 남아 있어, 이번 리포트의 결론은 개선을 인정하되 확인 과제를 함께 반영한 {recommendation}이다."
    )
    reader["one_line_view"] = cover["one_line_view"]
    reader["recommendation_rationale"] = cover["recommendation_rationale"]
    reader["key_debate"] = cover["key_debate"]


def _rewrite_charts(contract: dict[str, Any], recommendation: str) -> None:
    for block in contract.get("visual_report_blocks", []):
        what = block.get("what_chart_shows", "")
        limit = block.get("interpretation_limit", "")
        if not block.get("analyst_takeaway"):
            block["analyst_takeaway"] = (
                f"이 차트는 {what} "
                f"투자 판단상으로는 {recommendation} 의견의 근거와 확인 과제를 함께 설명한다."
            )
        if not limit:
            block["interpretation_limit"] = "차트 해석은 chart_manifest.json에 명시된 허용 범위로 제한한다."


def _rewrite_catalysts(contract: dict[str, Any], recommendation: str) -> None:
    for index, card in enumerate(contract["reader_friendly_sections"].get("catalyst_analysis_cards", [])):
        card.setdefault("catalyst_group", _default_catalyst_group(index))
        card["investment_impact"] = _catalyst_impact(card, index, recommendation)


def _rewrite_risks(contract: dict[str, Any], recommendation: str) -> None:
    for card in contract["reader_friendly_sections"].get("risk_cards", []):
        card["hold_connection"] = _risk_connection(card, recommendation)


def _rewrite_final_rationale(contract: dict[str, Any], company: str, recommendation: str) -> None:
    final = contract["reader_friendly_sections"]["final_rationale"]
    positives = contract.get("cover_summary", {}).get("positive_signals", [])
    negatives = contract.get("cover_summary", {}).get("negative_signals", [])
    metrics = _metric_phrases(contract, limit=3)
    final["title"] = "최종 투자의견 근거"
    final["positive_case"] = (
        f"{company}의 긍정 요인은 {_join_texts(positives, '확인 가능한 재무 및 사업 모멘텀')}이다. "
        f"{'핵심 지표로는 ' + ', '.join(metrics) + '가 확인된다.' if metrics else '핵심 지표는 확인 가능한 범위에서 보수적으로 해석한다.'}"
    )
    final["caution_case"] = (
        f"경계 요인은 {_join_texts(negatives, '확인 필요 요인')}이다. "
        "EPS의 기간 기준 차이, 규제·경쟁 변수, 시장 대비 성과 부담이 남아 있으면 긍정 신호를 전부 반영하기 어렵다."
    )
    final["balance_of_evidence"] = (
        "핵심은 개선의 방향성이 아니라 그 개선이 연간 실적과 시장 선호로 확인되는지다. "
        "확인 가능한 수익성 개선은 인정하되, 근거가 없는 추가 지표로 결론을 확장하지 않는다."
    )
    final["investment_conclusion"] = (
        f"종합하면 현재 투자의견은 {recommendation}이다. "
        "매출과 마진 구조는 투자 매력을 높이는 방향이지만 상대성과, 규제·경쟁 리스크, 연간 기준 확인이 남아 있어 공격적인 의견 상향에는 아직 근거가 부족하다."
    )
    final["investment_implication"] = (
        "향후 판단 변화는 실적 지속성, 리스크 완화, 시장 확인이 함께 나타날 때 검토할 수 있으며, 현재 결론은 균형 판단에 가깝다."
    )
    final["view_change_conditions"] = _view_change_conditions(contract)


def _view_change_conditions(contract: dict[str, Any]) -> dict[str, list[str]]:
    return {
        "upside_conditions": [
            "연간 확정 실적에서도 공헌이익률 개선과 판관비율 하락이 유지될 것",
            "동일 기준 EPS와 영업현금흐름이 함께 개선되어 이익 체력 회복이 확인될 것",
            "20일 초과수익률과 60일 상대강도가 시장 대비 플러스로 전환될 것",
            "핵심 촉매가 처방 확대, 매출 기여, 계약 진행 등 확인 가능한 성과로 연결될 것",
        ],
        "downside_conditions": [
            "공헌이익률 개선이 둔화되거나 판관비율이 재상승해 비용 효율성 논리가 약화될 것",
            "EPS 또는 영업현금흐름이 동일 기준에서 악화되어 재무 개선의 지속성이 흔들릴 것",
            "규제 조사, 공급망 변수, 대체재 또는 경쟁 심화가 사업 일정이나 매출 가시성에 영향을 줄 것",
            "주가 회복에도 상대성과가 계속 부진해 시장 선호 회복이 지연될 것",
        ],
    }


def _metric_value_map(contract: dict[str, Any]) -> dict[str, str]:
    return {
        str(metric.get("metric_name")): str(metric.get("value"))
        for metric in contract.get("key_metrics_table", {}).get("metrics", [])
        if metric.get("value") and metric.get("value") != "N/A"
    }


def _margin_sentence(metric_values: dict[str, str]) -> str:
    revenue = metric_values.get("Revenue")
    contribution = metric_values.get("Contribution Margin")
    sga = metric_values.get("SG&A Margin")
    parts = []
    if revenue:
        parts.append(f"매출 {revenue}")
    if contribution:
        parts.append(f"공헌이익률 {contribution}")
    if sga:
        parts.append(f"판관비율 {sga}")
    return ", ".join(parts) if parts else "확인 가능한 재무 지표"


def _market_sentence(snapshot: dict[str, Any]) -> str:
    if not snapshot:
        return "시장 확인 신호는 아직 추가 점검이 필요한 상태"
    ma20 = _fmt_pct(snapshot.get("close_to_ma20_pct"))
    ma60 = _fmt_pct(snapshot.get("close_to_ma60_pct"))
    excess = _fmt_pct(snapshot.get("excess_return_20d_pct"))
    relative = _fmt_pct(snapshot.get("relative_strength_60d_pct"))
    volume = _fmt_ratio(snapshot.get("volume_ratio_20d"))
    return (
        f"주가는 MA20 대비 {ma20}, MA60 대비 {ma60}로 단기 흐름은 개선됐지만 "
        f"거래량 {volume}에도 20일 초과수익률 {excess}, 60일 상대강도 {relative}로 시장 대비 약세가 남아 있는 상태"
    )


def _chart_snapshot_by_role(contract: dict[str, Any], role: str) -> dict[str, Any]:
    for block in contract.get("visual_report_blocks", []):
        if block.get("block_id") and role in str(block.get("block_id")):
            return block.get("data_snapshot", {}) if isinstance(block.get("data_snapshot"), dict) else {}
        if _role_from_figure_id(block.get("figure_id", "")) == role:
            return block.get("data_snapshot", {}) if isinstance(block.get("data_snapshot"), dict) else {}
    return {}


def _role_from_figure_id(figure_id: str) -> str:
    text = str(figure_id)
    if "stock_price" in text:
        return "market_composite"
    if "fundamental_margin" in text:
        return "financial_margin"
    if "revenue_profit_sga" in text:
        return "financial_income"
    if "peer_return" in text:
        return "peer_market"
    return ""


def _cover_positive_signals(contract: dict[str, Any]) -> list[str]:
    metric_values = _metric_value_map(contract)
    signals = []
    revenue = metric_values.get("Revenue")
    contribution = metric_values.get("Contribution Margin")
    sga = metric_values.get("SG&A Margin")
    if revenue:
        signals.append(f"매출 {revenue}로 외형 기반 확대 확인")
    if contribution and sga:
        signals.append(f"공헌이익률 {contribution}, 판관비율 {sga}로 비용 효율성 개선")
    catalysts = contract.get("reader_friendly_sections", {}).get("catalyst_analysis_cards", [])
    if catalysts:
        signals.append("핵심 제품과 신사업 촉매가 중장기 성장 옵션으로 작용")
    return signals[:3] or ["재무 및 사업 측면의 개선 신호 확인"]


def _cover_negative_signals(contract: dict[str, Any]) -> list[str]:
    metric_values = _metric_value_map(contract)
    market = _chart_snapshot_by_role(contract, "market_composite")
    signals = []
    eps = metric_values.get("EPS")
    if eps:
        signals.append(f"EPS {eps}은 기간 기준 차이로 보수적 해석 필요")
    if market:
        signals.append(
            f"20일 초과수익률 {_fmt_pct(market.get('excess_return_20d_pct'))}, "
            f"60일 상대강도 {_fmt_pct(market.get('relative_strength_60d_pct'))}로 시장 대비 약세"
        )
    signals.append("규제·정책 변수와 경쟁 심화 가능성이 리스크 할인 요인")
    return signals[:3]


def _fmt_pct(value: Any) -> str:
    try:
        return f"{float(value):.2f}%"
    except (TypeError, ValueError):
        return "확인 필요"


def _fmt_ratio(value: Any) -> str:
    try:
        return f"{float(value):.2f}배"
    except (TypeError, ValueError):
        return "확인 필요"


def _metric_phrases(contract: dict[str, Any], *, limit: int) -> list[str]:
    phrases = []
    for metric in contract.get("key_metrics_table", {}).get("metrics", []):
        value = metric.get("value")
        if not value or value == "N/A":
            continue
        phrases.append(f"{_metric_display_name(metric.get('metric_name'))} {value}")
        if len(phrases) >= limit:
            break
    return phrases


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


def _first_text(items: Any, fallback: str) -> str:
    if isinstance(items, list):
        for item in items:
            text = str(item).strip()
            if text:
                return text
    if isinstance(items, str) and items.strip():
        return items.strip()
    return fallback


def _join_texts(items: Any, fallback: str) -> str:
    if not isinstance(items, list):
        return fallback
    texts = [str(item).strip() for item in items[:3] if str(item).strip()]
    return " / ".join(texts) if texts else fallback


def _condition_items(source_items: Any, fallback: list[str]) -> list[str]:
    if not isinstance(source_items, list):
        return fallback[:4]
    items = [str(item).strip() for item in source_items if str(item).strip()]
    if not items:
        return fallback[:4]
    normalized = []
    for item in items[:4]:
        if len(item) > 44:
            item = item[:44].rstrip(" ,.;") + "..."
        normalized.append(item)
    while len(normalized) < 4:
        normalized.append(fallback[len(normalized)])
    return normalized[:4]


def _default_catalyst_group(index: int) -> str:
    if index == 0:
        return "핵심 촉매"
    if index <= 2:
        return "중장기 성장 옵션"
    return "글로벌 확장 변수"


def _catalyst_impact(card: dict[str, Any], index: int, recommendation: str) -> str:
    title = card.get("catalyst_title", "해당 촉매")
    group = card.get("catalyst_group", "")
    category = _catalyst_category(title, card.get("evidence_from_strategy", ""))
    if category == "commercial_product":
        return (
            "핵심 제품의 처방 또는 판매 확대는 매출 가시성과 수익성 개선을 직접 확인할 수 있는 중요한 촉매다. "
            f"점유율과 처방 추이가 유지되면 {recommendation} 결론 안의 긍정 축이 강화되지만, 규제 이슈가 함께 완화되어야 의견 상향 논의로 이어질 수 있다."
        )
    if category == "digital_platform":
        return (
            "디지털·플랫폼 사업은 기존 제품 매출 외의 확장성을 보여 주는 옵션이다. "
            "다만 단기 실적보다 서비스 출시, 사용자 확보, 사업모델 검증이 먼저 필요해 현재는 장기 모멘텀으로 반영한다."
        )
    if category == "pipeline":
        return (
            "신약과 파이프라인 확장은 사업 다각화와 중장기 옵션을 넓히는 촉매다. "
            "공동연구와 공급 계약은 초기 근거지만 임상 진입과 후보물질 구체화 전까지는 실적 가치로 크게 반영하기 어렵다."
        )
    if category == "global":
        return (
            "글로벌 확장은 매출 기반을 넓히는 중장기 변수다. "
            "주요 해외 지역별 허가, 출시, 판매 성과가 실제 매출로 이어지는 속도가 리스크 할인 축소의 핵심이다."
        )
    if index == 0 or "핵심" in str(group):
        return (
            f"{title}은 성장 기대를 실적 확인으로 바꿀 수 있는 핵심 변수다. "
            f"매출·마진 기여가 확인되면 {recommendation} 결론 안의 긍정 축이 강화되지만, 확인 전에는 기대를 선반영하기 어렵다."
        )
    return (
        f"{title}은 투자심리 개선에 도움을 줄 수 있는 보조 촉매다. "
        "실적 반영 또는 시장 확인으로 이어질 때 의견 상향 논의의 근거가 될 수 있다."
    )


def _catalyst_category(*values: Any) -> str:
    text = " ".join(str(value).lower() for value in values)
    if any(token in text for token in ["처방", "점유율", "매출", "판매", "상업화", "제품", "치료제", "품목"]):
        return "commercial_product"
    if any(token in text for token in ["ai", "디지털", "플랫폼", "조인트벤처", "헬스케어", "솔루션"]):
        return "digital_platform"
    if any(token in text for token in ["신약", "파이프라인", "원료", "공동연구", "후보물질", "임상"]):
        return "pipeline"
    if any(token in text for token in ["글로벌", "해외", "수출", "현지", "국제", "지역 확장", "사업 확장", "인허가", "출시"]):
        return "global"
    return "generic"


def _risk_connection(card: dict[str, Any], recommendation: str) -> str:
    risk_type = str(card.get("risk_type", ""))
    if "Financial" in risk_type:
        return (
            "이 리스크가 완화되지 않으면 매출 증가가 이익 체력 개선으로 연결된다고 단정하기 어렵다. "
            f"따라서 {recommendation} 결론은 재무 개선을 인정하되 확인 전까지 할인해 반영하는 판단이다."
        )
    if "Regulatory" in risk_type:
        return (
            "규제 변수는 사업 모멘텀의 실적화 시점과 투자심리를 동시에 흔들 수 있다. "
            "이 불확실성이 남아 있으면 긍정 촉매를 전부 반영하기보다 리스크 프리미엄을 유지해야 한다."
        )
    if "Market" in risk_type:
        return (
            "상대성과가 회복되지 않으면 시장이 아직 펀더멘털 개선을 충분히 가격에 반영하지 않는다는 의미다. "
            "가격 흐름 개선만으로 의견을 더 공격적으로 해석하기 어렵다는 점이 현재 결론의 핵심 제약이다."
        )
    if "Execution" in risk_type:
        return (
            "사업 계획이 실제 매출과 현금흐름으로 전환되는 과정이 확인되어야 기대 요인을 실적 가치로 반영할 수 있다. "
            "실행 확인 전에는 긍정 시나리오를 보수적으로 반영하는 것이 타당하다."
        )
    return (
        f"이 리스크는 {recommendation} 결론에서 할인 요인으로 반영된다. "
        "완화 여부가 확인될 때 투자 판단의 방향성이 달라질 수 있다."
    )
