"""Select Writer-approved figures from Visualization Agent chart manifests."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from data_loader import get_nested


MAX_SELECTED_FIGURES = 2
PRIMARY_REPORT_ROLES = ("market_composite", "financial_margin")
FALLBACK_REPORT_ROLES = ("peer_profitability", "financial_income", "peer_market", "market_indexed", "financial_stability")
INTERNAL_ONLY_ROLES = {"decision_evidence"}
UNSUPPORTED_CHART_TERMS = ["OPM", "ROE", "ROA", "P/E", "P/B", "PER", "PBR"]
STRATEGY_FIELDS_FOR_SUPPORT = [
    "final_recommendation.summary",
    "final_rationale.why_buy_hold_sell",
    "investment_thesis.thesis_1",
    "investment_thesis.thesis_2",
    "investment_thesis.thesis_3",
    "financial_view.revenue",
    "financial_view.profitability",
    "financial_view.cash_flow",
    "financial_view.balance_sheet",
    "market_price_view.price_trend",
    "market_price_view.volume",
    "market_price_view.relative_strength",
    "peer_competitor_positioning.peer_based_investment_implication",
]


def select_figures(
    *,
    chart_manifest: dict[str, Any],
    strategy_report: dict[str, Any],
    visualization_dir: str | Path,
) -> list[dict[str, Any]]:
    """Select directly usable figures without binding to a specific company."""

    visualization_dir = Path(visualization_dir).expanduser().resolve()
    candidates = []
    strategy_profile = _strategy_profile(strategy_report)
    for order, chart in enumerate(chart_manifest.get("charts", [])):
        figure_id = chart.get("figure_id")
        if not figure_id:
            continue
        figure_path = _resolve_asset_path(chart, visualization_dir)
        figure_path_png = _resolve_png_asset_path(chart, visualization_dir)
        if not figure_path.exists():
            continue
        candidates.append((order, _chart_score(chart, strategy_profile), chart, figure_path, figure_path_png))

    selected: list[dict[str, Any]] = []
    selected_ids: set[str] = set()
    for role in PRIMARY_REPORT_ROLES:
        match = _best_candidate_for_role(candidates, role, selected_ids)
        if match:
            _, score, chart, figure_path, figure_path_png = match
            selected.append(_chart_block(chart, figure_path, figure_path_png, strategy_report, support_score=score))
            selected_ids.add(chart.get("figure_id", ""))
        if len(selected) >= MAX_SELECTED_FIGURES:
            return selected

    for role in FALLBACK_REPORT_ROLES:
        match = _best_candidate_for_role(candidates, role, selected_ids)
        if match:
            _, score, chart, figure_path, figure_path_png = match
            selected.append(_chart_block(chart, figure_path, figure_path_png, strategy_report, support_score=score))
            selected_ids.add(chart.get("figure_id", ""))
        if len(selected) >= MAX_SELECTED_FIGURES:
            return selected

    for _, score, chart, figure_path, figure_path_png in sorted(candidates, key=lambda item: (-item[1], item[0])):
        role = _section_key(chart)
        if chart.get("figure_id") in selected_ids or role in INTERNAL_ONLY_ROLES or score <= 0:
            continue
        selected.append(_chart_block(chart, figure_path, figure_path_png, strategy_report, support_score=score))
        selected_ids.add(chart.get("figure_id", ""))
        if len(selected) >= MAX_SELECTED_FIGURES:
            return selected

    if not selected:
        for _, score, chart, figure_path, figure_path_png in sorted(candidates, key=lambda item: (-item[1], item[0])):
            if score <= 0:
                continue
            selected.append(_chart_block(chart, figure_path, figure_path_png, strategy_report, support_score=score))
            if len(selected) >= MAX_SELECTED_FIGURES:
                break
    return selected


def select_figures_by_ids(
    *,
    chart_manifest: dict[str, Any],
    strategy_report: dict[str, Any],
    visualization_dir: str | Path,
    figure_ids: list[str],
    max_figures: int = MAX_SELECTED_FIGURES,
) -> list[dict[str, Any]]:
    """Build report figure blocks from LLM-selected figure ids with deterministic fallback."""

    visualization_dir = Path(visualization_dir).expanduser().resolve()
    strategy_profile = _strategy_profile(strategy_report)
    candidates = []
    for order, chart in enumerate(chart_manifest.get("charts", [])):
        figure_id = chart.get("figure_id")
        if not figure_id:
            continue
        role = _section_key(chart)
        if role in INTERNAL_ONLY_ROLES:
            continue
        figure_path = _resolve_asset_path(chart, visualization_dir)
        figure_path_png = _resolve_png_asset_path(chart, visualization_dir)
        if not figure_path.exists():
            continue
        candidates.append((order, _chart_score(chart, strategy_profile), chart, figure_path, figure_path_png))

    by_id = {str(chart.get("figure_id")): (order, score, chart, figure_path, figure_path_png) for order, score, chart, figure_path, figure_path_png in candidates}
    selected: list[dict[str, Any]] = []
    selected_ids: set[str] = set()
    for figure_id in figure_ids:
        match = by_id.get(str(figure_id))
        if not match or str(figure_id) in selected_ids:
            continue
        _, score, chart, figure_path, figure_path_png = match
        selected.append(_chart_block(chart, figure_path, figure_path_png, strategy_report, support_score=score))
        selected_ids.add(str(figure_id))
        if len(selected) >= max_figures:
            return selected

    for _, score, chart, figure_path, figure_path_png in sorted(candidates, key=lambda item: (-item[1], item[0])):
        figure_id = str(chart.get("figure_id"))
        if figure_id in selected_ids or score <= 0:
            continue
        selected.append(_chart_block(chart, figure_path, figure_path_png, strategy_report, support_score=score))
        selected_ids.add(figure_id)
        if len(selected) >= max_figures:
            break
    return selected


def _best_candidate_for_role(
    candidates: list[tuple[int, int, dict[str, Any], Path, Path]],
    role: str,
    selected_ids: set[str],
) -> tuple[int, int, dict[str, Any], Path, Path] | None:
    matches = [
        candidate
        for candidate in candidates
        if _section_key(candidate[2]) == role and candidate[2].get("figure_id") not in selected_ids and candidate[1] > 0
    ]
    if not matches:
        return None
    return sorted(matches, key=lambda item: (-item[1], item[0]))[0]


def _chart_block(
    chart: dict[str, Any],
    figure_path: Path,
    figure_path_png: Path,
    strategy_report: dict[str, Any],
    *,
    support_score: int,
) -> dict[str, Any]:
    figure_id = chart.get("figure_id", "")
    linked_fields = _linked_fields_for_chart(chart)
    return {
        "block_id": _safe_id(figure_id),
        "section": chart.get("section_recommendation", ""),
        "figure_id": figure_id,
        "figure_path": str(figure_path),
        "figure_path_png": str(figure_path_png) if figure_path_png.exists() else str(figure_path),
        "html_img_path": str(figure_path_png) if figure_path_png.exists() else str(figure_path),
        "figure_title": chart.get("title", ""),
        "display_title": _display_title_for_chart(chart),
        "caption": chart.get("caption", ""),
        "chart_insights": chart.get("chart_insights", {}),
        "data_snapshot": chart.get("data_snapshot", {}),
        "what_chart_shows": _what_chart_shows(chart, linked_fields, strategy_report),
        "analyst_takeaway": _build_takeaway(chart, linked_fields, strategy_report),
        "linked_strategy_fields": linked_fields,
        "interpretation_limit": _interpretation_limit_for_chart(chart),
        "support_score": support_score,
        "support_reason": _support_reason(chart, linked_fields),
        "recommended_report_rank": chart.get("recommended_report_rank"),
    }


def _resolve_asset_path(chart: dict[str, Any], visualization_dir: Path) -> Path:
    if chart.get("asset_abs_path_pdf"):
        return Path(chart["asset_abs_path_pdf"]).expanduser().resolve()
    if chart.get("asset_path_pdf"):
        return (visualization_dir / chart["asset_path_pdf"]).resolve()
    if chart.get("asset_abs_path_png"):
        return Path(chart["asset_abs_path_png"]).expanduser().resolve()
    return (visualization_dir / chart.get("asset_path_png", "")).resolve()


def _resolve_png_asset_path(chart: dict[str, Any], visualization_dir: Path) -> Path:
    if chart.get("asset_abs_path_png"):
        return Path(chart["asset_abs_path_png"]).expanduser().resolve()
    if chart.get("asset_path_png"):
        return (visualization_dir / chart["asset_path_png"]).resolve()
    return _resolve_asset_path(chart, visualization_dir)


def _chart_score(chart: dict[str, Any], strategy_profile: dict[str, Any]) -> int:
    chart_tokens = _tokens(_chart_text(chart))
    overlap = len(chart_tokens & strategy_profile["tokens"])
    section = " ".join(str(chart.get(key, "")) for key in ["section_recommendation", "title", "chart_type"]).lower()
    score = overlap * 3
    if chart.get("writer_priority_score") is not None:
        try:
            score += int(round(float(chart["writer_priority_score"])))
        except (TypeError, ValueError):
            pass
    rank = _safe_int(chart.get("recommended_report_rank"), 0)
    if rank > 0:
        score += max(0, 40 - (rank - 1) * 5)
    if chart.get("writer_allowed_interpretation"):
        score += 8
    if chart.get("caption"):
        score += 4
    if "investment" in section or "thesis" in section or "decision" in section or "rationale" in section:
        score += 18 if strategy_profile["has_decision"] else 4
    if "financial" in section or "fundamental" in section or "revenue" in section or "margin" in section:
        score += 15 if strategy_profile["has_financial"] else 3
    if "revenue" in section and ("profit" in section or "sga" in section):
        score += 10 if strategy_profile["has_financial"] else 0
    if "liquidity" in section or "leverage" in section or "stability" in section:
        score += 15 if strategy_profile["has_stability"] else 3
    if "market" in section or "price" in section or "relative" in section or "kospi" in section:
        score += 15 if strategy_profile["has_market"] else 3
    if "peer" in section or "comparison" in section:
        score += 12 if strategy_profile["has_peer"] else 3
    if _has_unsupported_term(_chart_text(chart)):
        score -= 3
    return score


def _section_key(chart: dict[str, Any]) -> str:
    if chart.get("report_chart_role"):
        return _safe_id(str(chart["report_chart_role"]))
    broker_use_case = chart.get("broker_report_use_case")
    if isinstance(broker_use_case, dict) and broker_use_case.get("role"):
        return _safe_id(str(broker_use_case["role"]))
    text = str(chart.get("section_recommendation", "")).lower()
    title = str(chart.get("title", "")).lower()
    combined = f"{text} {title}"
    if "investment" in combined or "thesis" in combined or "decision" in combined or "rationale" in combined:
        return "decision_rationale"
    if "liquidity" in combined or "leverage" in combined or "stability" in combined:
        return "financial_stability"
    if "peer" in combined and ("profitability" in combined or "revenue" in combined or "margin" in combined or "eps" in combined):
        return "peer_profitability"
    if "peer" in combined and ("market" in combined or "return" in combined or "relative" in combined):
        return "peer_market"
    if "market" in text or "price" in text:
        return "market_price"
    if "financial" in text or "fundamental" in text or "revenue" in combined or "margin" in combined:
        return "financial_performance"
    return _safe_id(text or str(chart.get("chart_type", "other")))


def _linked_fields_for_chart(chart: dict[str, Any]) -> list[str]:
    manifest_fields = chart.get("strategy_support_fields")
    if isinstance(manifest_fields, list):
        safe_fields = [str(field) for field in manifest_fields if str(field).strip()]
        if safe_fields:
            return safe_fields
    text = " ".join(str(chart.get(key, "")) for key in ["section_recommendation", "title", "caption"]).lower()
    if "investment" in text or "thesis" in text or "decision" in text or "rationale" in text:
        return ["final_recommendation.summary", "final_rationale.why_buy_hold_sell", "investment_thesis.thesis_1", "investment_thesis.thesis_3"]
    if "liquidity" in text or "leverage" in text or "stability" in text:
        return [
            "financial_view.cash_flow",
            "financial_view.balance_sheet",
            "peer_competitor_positioning.target_relative_strength",
            "peer_competitor_positioning.peer_based_investment_implication",
        ]
    if "peer" in text and ("market" in text or "return" in text or "relative" in text):
        return ["market_price_view.relative_strength", "peer_competitor_positioning.peer_based_investment_implication"]
    if "peer" in text and ("profitability" in text or "revenue" in text or "margin" in text or "eps" in text):
        return [
            "financial_view.revenue",
            "financial_view.profitability",
            "peer_competitor_positioning.target_relative_strength",
            "peer_competitor_positioning.peer_based_investment_implication",
        ]
    if "market" in text or "price" in text or "relative" in text or "kospi" in text:
        return ["market_price_view.price_trend", "market_price_view.volume", "market_price_view.relative_strength"]
    if "financial" in text or "fundamental" in text or "margin" in text or "revenue" in text:
        return ["financial_view.revenue", "financial_view.profitability", "financial_view.cash_flow"]
    return ["final_recommendation.summary"]


def _display_title_for_chart(chart: dict[str, Any]) -> str:
    role_titles = {
        "decision_evidence": "투자판단 근거: 긍정 요인과 리스크 균형",
        "financial_income": "매출·공헌이익·판관비 흐름",
        "financial_margin": "공헌이익률·판관비율 추이",
        "financial_stability": "Peer 대비 유동성·레버리지 비교",
        "market_composite": "주가 추세·거래량·상대강도",
        "market_indexed": "시장 대비 지수화 주가 성과",
        "peer_market": "Peer 대비 수익률·상대강도",
        "peer_profitability": "국내 Peer 대비 매출·수익성 비교",
    }
    role = _section_key(chart)
    if role in role_titles:
        return role_titles[role]
    title = str(chart.get("title") or chart.get("figure_id") or "Chart").strip()
    return title.replace("_", " ")


def _what_chart_shows(chart: dict[str, Any], linked_fields: list[str], strategy_report: dict[str, Any]) -> str:
    insight_observation = _insight_observation(chart)
    if insight_observation:
        return insight_observation
    manifest_observation = _safe_text(chart.get("chart_observation", ""))
    if manifest_observation:
        return manifest_observation

    role = str(chart.get("report_chart_role") or _section_key(chart))
    if role == "decision_evidence":
        return (
            "차트는 투자 판단 근거를 긍정 요인, 리스크, 혼재 신호, 모니터링 항목으로 나누어 보여준다. "
            "긍정 근거만 독립적으로 강조되는 구조가 아니라, 최종 의견을 유지하게 만드는 확인 과제가 함께 배치되어 있는지가 핵심 관찰 포인트다."
        )
    if role == "financial_income":
        revenue_markers = _numeric_markers(
            str(get_nested(strategy_report, "financial_view.revenue") or ""),
            units=("억원", "조원", "원"),
        )
        profitability_markers = _numeric_markers(
            str(get_nested(strategy_report, "financial_view.profitability") or ""),
            units=("%",),
        )
        marker_text = _join_markers([revenue_markers, profitability_markers])
        return (
            "차트는 매출, 공헌이익, 판관비를 같은 흐름 안에서 보여 주어 성장 규모와 비용 부담을 동시에 확인하게 한다. "
            f"투자 판단과 연결되는 핵심 수치는 {marker_text or '매출, 공헌이익, 판관비 흐름'}이며, "
            "YTD 구간 표시는 현재 수치가 연간 확정치와 직접 비교될 수 없다는 점을 함께 보여준다."
        )
    if role == "financial_margin":
        profitability_markers = _numeric_markers(str(get_nested(strategy_report, "financial_view.profitability") or ""))
        return (
            "차트는 공헌이익률과 판관비율을 함께 배치해 수익성의 질과 비용 효율성 방향을 확인하게 한다. "
            f"투자 판단과 연결되는 핵심 수치는 {profitability_markers or '공헌이익률과 판관비율 변화'}이며, "
            "두 비율의 방향성이 엇갈릴수록 재무 개선 신호를 더 선명하게 읽을 수 있다."
        )
    if role == "market_composite":
        volume_markers = _numeric_markers(str(get_nested(strategy_report, "market_price_view.volume") or ""), units=("배",))
        relative_markers = _numeric_markers(str(get_nested(strategy_report, "market_price_view.relative_strength") or ""), units=("%",))
        marker_text = _join_markers([volume_markers, relative_markers])
        return (
            "차트는 주가의 이동평균선 대비 위치, 거래량 활성화, 시장 대비 성과를 한 번에 확인하게 한다. "
            f"투자 판단과 연결되는 핵심 수치는 {marker_text or '거래량 비율, 초과수익률, 상대강도'}이며, "
            "이동평균선 상회와 상대성과 약세가 동시에 나타나는지가 핵심 관찰 포인트다."
        )
    if role == "peer_market":
        relative_markers = _numeric_markers(str(get_nested(strategy_report, "market_price_view.relative_strength") or ""), units=("%",))
        return (
            "차트는 비교 기업들의 단기 및 중기 수익률과 시장 대비 상대성과를 동일 기준에서 비교한다. "
            f"대상 회사의 상대성과 문맥은 {relative_markers or '시장 대비 성과'}로 연결되고, peer 해석은 동종 그룹 내 위치를 통해 보완된다. "
            "따라서 절대 주가 흐름보다 시장 안에서의 선호도와 상대 약세 여부를 확인하는 데 초점이 있다."
        )
    if role == "peer_profitability":
        revenue_markers = _numeric_markers(str(get_nested(strategy_report, "financial_view.revenue") or ""), units=("억원", "조원", "원"))
        profitability_markers = _numeric_markers(str(get_nested(strategy_report, "financial_view.profitability") or ""), units=("%",))
        marker_text = _join_markers([revenue_markers, profitability_markers])
        return (
            "차트는 국내 비교군 안에서 매출 규모, 공헌이익률, 판관비율, EPS 위치를 함께 보여준다. "
            f"투자 판단과 연결되는 핵심 수치는 {marker_text or '매출 규모와 수익성 지표'}이며, "
            "valuation 없이도 재무 체력의 상대적 위치를 확인할 수 있지만 할인 또는 프리미엄 판단으로 확장하지 않는다."
        )
    if role == "financial_stability":
        balance_markers = _numeric_markers(str(get_nested(strategy_report, "financial_view.balance_sheet") or ""))
        cash_flow_markers = _numeric_markers(str(get_nested(strategy_report, "financial_view.cash_flow") or ""), units=("억원", "조원", "원"))
        marker_text = _join_markers([balance_markers, cash_flow_markers])
        return (
            "차트는 peer 간 유동성, 현금성 지표, 자본 구조, 레버리지 부담을 나란히 보여준다. "
            f"투자 판단과 연결되는 핵심 수치는 {marker_text or '유동성, 현금흐름, 레버리지 지표'}이며, "
            "실적 모멘텀보다 재무 리스크 흡수력 확인에 적합하다."
        )
    if role == "market_indexed":
        relative_markers = _numeric_markers(str(get_nested(strategy_report, "market_price_view.relative_strength") or ""), units=("%",))
        return (
            "차트는 대상 주가와 시장 지수를 같은 출발점으로 지수화해 성과 격차의 방향을 보여준다. "
            f"시장 대비 해석은 {relative_markers or '상대성과'}로 연결된다. "
            "성과 격차가 축소되는지 확대되는지가 시장 신뢰 회복 여부를 보는 관찰 포인트다."
        )

    caption = _safe_text(chart.get("caption", ""))
    allowed = _safe_text(chart.get("writer_allowed_interpretation", ""))
    if caption and allowed:
        return f"{caption} 이 차트는 {allowed}"
    if caption:
        return caption
    if allowed:
        return f"이 차트는 {allowed}"
    return "이 차트는 해당 섹션의 핵심 지표 흐름과 투자 판단에 필요한 확인 포인트를 요약한다."


def _build_takeaway(chart: dict[str, Any], linked_fields: list[str], strategy_report: dict[str, Any]) -> str:
    insight_takeaway = _insight_takeaway(chart)
    if insight_takeaway:
        return insight_takeaway
    manifest_takeaway = _safe_text(chart.get("analyst_takeaway", ""))
    if manifest_takeaway:
        return manifest_takeaway
    labels = [_field_label(field) for field in linked_fields if get_nested(strategy_report, field)]
    if labels:
        return (
            f"관련 투자 문맥은 {', '.join(labels)} 항목과 연결된다. "
            "따라서 이 차트는 투자 논리의 근거와 확인 과제를 함께 설명한다."
        )
    return "이 차트는 관련 섹션의 투자 논리와 확인 과제를 설명하는 시각 자료다."


def _support_reason(chart: dict[str, Any], linked_fields: list[str]) -> str:
    insights = chart.get("chart_insights")
    if isinstance(insights, dict) and _safe_text(insights.get("investment_debate", "")):
        return f"차트의 핵심 투자 논쟁은 {_safe_text(insights.get('investment_debate'))}이다."
    selection_reason = _safe_text(chart.get("selection_reason", ""))
    if selection_reason:
        return selection_reason
    support_summary = _safe_text(chart.get("strategy_support_summary", ""))
    if support_summary:
        return f"{support_summary} 항목과 연결된다."
    labels = [_field_label(field) for field in linked_fields]
    if labels:
        return f"{', '.join(labels[:4])} 항목과 연결된다."
    section = chart.get("section_recommendation") or chart.get("chart_type") or "관련 섹션"
    return f"{section} 판단을 보조한다."


def _interpretation_limit_for_chart(chart: dict[str, Any]) -> str:
    manifest_limit = _safe_text(chart.get("interpretation_limit", ""))
    if manifest_limit:
        return manifest_limit
    limitations = chart.get("data_limitations", [])
    if isinstance(limitations, str):
        limitations = [limitations]
    safe_limitations = [_safe_text(item) for item in limitations if _safe_text(item)]
    safe_limitations = [item for item in safe_limitations if not _has_unsupported_term(item)]
    if safe_limitations:
        return " ".join(safe_limitations[:2])
    section = _section_key(chart)
    if section == "market":
        return "시장 데이터는 가격과 거래 흐름을 보여주며 펀더멘털 개선의 직접 증거로 단정할 수 없다."
    if section == "financial":
        return "재무 차트는 지표의 방향성을 보여주며, 서로 다른 기간 기준의 수치를 동일 기간 비교로 단정하지 않는다."
    return "차트 해석은 chart_manifest.json에 명시된 허용 범위로 제한한다."


def _safe_text(value: Any) -> str:
    text = " ".join(str(value).split())
    return text


def _strategy_profile(strategy_report: dict[str, Any]) -> dict[str, Any]:
    values = []
    for field in STRATEGY_FIELDS_FOR_SUPPORT:
        value = get_nested(strategy_report, field)
        if isinstance(value, list):
            values.extend(str(item) for item in value)
        elif value:
            values.append(str(value))
    risk_view = strategy_report.get("risk_view", {})
    if isinstance(risk_view, dict):
        for value in risk_view.values():
            if isinstance(value, list):
                values.extend(str(item) for item in value)
    text = " ".join(values)
    return {
        "text": text,
        "tokens": _tokens(text),
        "has_decision": bool(get_nested(strategy_report, "final_recommendation.summary") or get_nested(strategy_report, "final_rationale.why_buy_hold_sell")),
        "has_financial": bool(strategy_report.get("financial_view")),
        "has_stability": any(word in text for word in ["유동", "부채", "자본", "현금", "안정", "cash", "debt", "liquidity", "leverage"]),
        "has_market": bool(strategy_report.get("market_price_view")),
        "has_peer": bool(strategy_report.get("peer_competitor_positioning")),
    }


def _chart_text(chart: dict[str, Any]) -> str:
    parts = []
    for key in [
        "figure_id",
        "title",
        "chart_type",
        "section_recommendation",
        "caption",
        "writer_allowed_interpretation",
        "analyst_takeaway",
        "selection_reason",
        "strategy_support_summary",
        "report_chart_role",
        "chart_observation",
    ]:
        value = chart.get(key)
        if value:
            parts.append(str(value))
    insights = chart.get("chart_insights")
    if isinstance(insights, dict):
        parts.append(str(insights))
    for key in ["used_columns", "derived_columns", "used_metrics", "data_limitations"]:
        value = chart.get(key)
        if isinstance(value, list):
            parts.extend(str(item) for item in value)
        elif value:
            parts.append(str(value))
    return " ".join(parts)


def _tokens(text: str) -> set[str]:
    normalized = re.sub(r"[^0-9A-Za-z가-힣_]+", " ", text.lower())
    tokens = {token for token in normalized.split() if len(token) >= 2}
    expanded = set(tokens)
    for token in tokens:
        expanded.update(part for part in token.split("_") if len(part) >= 2)
    return expanded


def _short_text(text: str, *, limit: int) -> str:
    text = _safe_text(text)
    if len(text) <= limit:
        return text
    return text[:limit].rstrip(" ,.;") + "..."


def _insight_observation(chart: dict[str, Any]) -> str:
    insights = chart.get("chart_insights")
    if not isinstance(insights, dict):
        return ""
    visible = insights.get("what_is_visible")
    if not isinstance(visible, list):
        return ""
    sentences = [_safe_text(item) for item in visible if _safe_text(item)]
    return " ".join(sentences[:4])


def _insight_takeaway(chart: dict[str, Any]) -> str:
    insights = chart.get("chart_insights")
    if not isinstance(insights, dict):
        return ""
    debate = _safe_text(insights.get("investment_debate", ""))
    commentary = _safe_text(insights.get("report_commentary", ""))
    recommendation_readthrough = _safe_text(insights.get("recommendation_readthrough", ""))
    parts = []
    if debate:
        parts.append(f"이 차트의 투자 쟁점은 {debate}이다.")
    if commentary:
        parts.append(commentary)
    if recommendation_readthrough:
        parts.append(recommendation_readthrough)
    return " ".join(parts)


def _numeric_markers(
    text: str,
    *,
    units: tuple[str, ...] = ("억원", "조원", "원", "%", "배"),
    limit: int = 4,
) -> str:
    unit_pattern = "|".join(re.escape(unit) for unit in sorted(units, key=len, reverse=True))
    markers = re.findall(rf"[-+]?\d[\d,]*(?:\.\d+)?\s*(?:{unit_pattern})", text)
    cleaned = []
    seen = set()
    for marker in markers:
        normalized = marker.strip()
        if not normalized or normalized in seen:
            continue
        cleaned.append(normalized)
        seen.add(normalized)
        if len(cleaned) >= limit:
            break
    return " / ".join(cleaned)


def _join_markers(marker_groups: list[str]) -> str:
    markers = []
    seen = set()
    for group in marker_groups:
        for marker in [part.strip() for part in group.split(" / ") if part.strip()]:
            if marker in seen:
                continue
            markers.append(marker)
            seen.add(marker)
    return ", ".join(markers[:6])


def _safe_id(value: str) -> str:
    normalized = re.sub(r"[^A-Za-z0-9가-힣_]+", "_", str(value)).strip("_")
    return normalized or "chart"


def _safe_int(value: Any, default: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _field_label(field: str) -> str:
    labels = {
        "market_price_view.price_trend": "가격 흐름",
        "market_price_view.volume": "거래 활성도",
        "market_price_view.relative_strength": "상대성과",
        "financial_view.revenue": "매출",
        "financial_view.profitability": "수익성",
        "financial_view.cash_flow": "현금흐름",
        "final_recommendation.summary": "투자 요약",
    }
    return labels.get(field, field.rsplit(".", 1)[-1].replace("_", " "))


def _has_unsupported_term(text: str) -> bool:
    return any(term in text for term in UNSUPPORTED_CHART_TERMS)
