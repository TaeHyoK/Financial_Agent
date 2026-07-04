"""Build automated analyst interpretation tasks for Writer Agent v3."""

from __future__ import annotations

from typing import Any


def build_interpretation_tasks(contract: dict[str, Any], strategy_report: dict[str, Any]) -> dict[str, Any]:
    """Create deterministic task prompts that guide LLM analyst commentary.

    The tasks are company-agnostic. They do not contain few-shot examples or
    company-specific templates; they convert chart metadata and Strategy context
    into required interpretation angles.
    """

    metadata = contract.get("report_metadata", {})
    recommendation = str(metadata.get("recommendation") or "").strip() or "투자의견"
    company = str(metadata.get("company_name") or "대상 기업").strip()
    visual_blocks = contract.get("visual_report_blocks", [])
    peer_blocks = contract.get("peer_comparison", {}).get("peer_chart_blocks", [])
    chart_tasks = [
        _chart_task(block, recommendation=recommendation, company=company)
        for block in [*visual_blocks, *peer_blocks]
        if isinstance(block, dict) and block.get("figure_id")
    ]
    section_tasks = _section_tasks(contract, strategy_report, recommendation=recommendation, company=company)
    return {
        "version": "v3",
        "objective": "차트와 섹션별 수치를 투자 논리와 투자의견에 연결하는 자동 해석 과제",
        "writing_principles": [
            "차트 설명을 기계적으로 반복하지 말고 투자 질문에 답한다.",
            "긍정 신호와 반대 신호를 모두 제시한다.",
            "현재 투자의견과 직접 연결한다.",
            "향후 확인 조건은 현재 수치 반복이 아니라 관찰해야 할 변화로 쓴다.",
            "해석 제한을 넘어 목표주가, 가치평가, 글로벌 peer 비교를 새로 만들지 않는다.",
        ],
        "chart_tasks": chart_tasks,
        "section_tasks": section_tasks,
    }


def _chart_task(block: dict[str, Any], *, recommendation: str, company: str) -> dict[str, Any]:
    figure_id = str(block.get("figure_id") or "")
    title = str(block.get("display_title") or block.get("figure_title") or figure_id)
    role = _infer_role(block)
    insights = block.get("chart_insights", {}) if isinstance(block.get("chart_insights"), dict) else {}
    snapshot = block.get("data_snapshot", {}) if isinstance(block.get("data_snapshot"), dict) else {}
    watch_points = insights.get("watch_points", []) if isinstance(insights.get("watch_points"), list) else []
    visible = insights.get("what_is_visible", []) if isinstance(insights.get("what_is_visible"), list) else []
    return {
        "task_id": f"chart::{figure_id}",
        "scope": "chart_interpretation",
        "figure_id": figure_id,
        "display_title": title,
        "role": role,
        "investment_question": _investment_question(role, title, company),
        "positive_signal": _positive_signal(role, visible, snapshot),
        "counter_signal": _counter_signal(role, insights, block),
        "recommendation_link_required": (
            f"{recommendation} 판단과 직접 연결한다. 긍정 신호가 있더라도 왜 현재 의견을 유지하거나 바꾸기 어려운지 설명한다."
        ),
        "monitoring_trigger": _monitoring_trigger(role, watch_points),
        "available_data_snapshot": snapshot,
        "interpretation_limit": block.get("interpretation_limit", ""),
        "required_output": {
            "what_chart_shows": "수치와 순위를 포함하되 차트 묘사에 그치지 않는다.",
            "analyst_takeaway": "투자 질문에 대한 답, 긍정 신호, 반대 신호, 투자의견 연결을 포함한다.",
            "support_reason": "이 차트를 리포트에 넣는 이유를 투자 논리 관점으로 작성한다.",
        },
    }


def _section_tasks(
    contract: dict[str, Any],
    strategy_report: dict[str, Any],
    *,
    recommendation: str,
    company: str,
) -> list[dict[str, Any]]:
    peer = contract.get("peer_comparison", {})
    tasks: list[dict[str, Any]] = []
    if isinstance(peer, dict) and peer.get("enabled"):
        tasks.append(
            {
                "task_id": "section::peer_comparison",
                "scope": "peer_section_commentary",
                "investment_question": "국내 peer 대비 상대 우위가 투자 매력으로 충분히 전환됐는가?",
                "positive_signal": "국내 peer 대비 재무 체력, 수익성, 재무 안정성 우위가 있는지 확인한다.",
                "counter_signal": "시장 대비 성과 약세, 결측 데이터, 가치평가 부재를 함께 반영한다.",
                "recommendation_link_required": f"{recommendation} 판단을 설득하는 상대 매력도 해석으로 작성한다.",
                "monitoring_trigger": "상대강도 반전, peer 대비 수익률 개선, 결측 재무 항목 보완 여부를 관찰 조건으로 제시한다.",
                "available_peer_table": peer.get("table_rows", []),
                "interpretation_limit": peer.get("peer_limitations_commentary", ""),
            }
        )
    tasks.append(
        {
            "task_id": "section::final_rationale",
            "scope": "final_rationale_editorial_check",
            "investment_question": f"{company}에 대해 왜 현재 결론이 {recommendation}인지 설득되는가?",
            "positive_signal": "재무 개선, 사업 모멘텀, 현금흐름 또는 재무 안정성의 긍정 근거를 확인한다.",
            "counter_signal": "기간 기준 차이, 규제·정책, 경쟁, 시장 상대성과의 확인 과제를 반영한다.",
            "recommendation_link_required": f"Buy/Sell이 아니라 {recommendation}인 이유를 균형 논리로 작성한다.",
            "monitoring_trigger": "의견 변경 조건을 향후 확인 가능한 변화로 쓴다.",
            "strategy_context": {
                "final_recommendation": strategy_report.get("final_recommendation"),
                "final_rationale": strategy_report.get("final_rationale"),
            },
        }
    )
    return tasks


def _infer_role(block: dict[str, Any]) -> str:
    text = " ".join(
        str(block.get(key, ""))
        for key in ["figure_id", "section", "display_title", "figure_title"]
    ).lower()
    if "peer" in text and ("profitability" in text or "수익성" in text or "매출" in text):
        return "peer_profitability"
    if "peer" in text and ("return" in text or "상대강도" in text or "수익률" in text):
        return "peer_market"
    if "liquidity" in text or "leverage" in text or "유동성" in text or "레버리지" in text:
        return "financial_stability"
    if "market" in text or "price" in text or "주가" in text or "상대강도" in text:
        return "market_composite"
    if "margin" in text or "공헌" in text or "판관비" in text:
        return "financial_margin"
    if "revenue" in text or "매출" in text:
        return "financial_income"
    return "general_chart"


def _investment_question(role: str, title: str, company: str) -> str:
    questions = {
        "financial_margin": "수익성 구조 개선이 연간 지속성과 투자의견 개선 근거로 이어질 수 있는가?",
        "financial_income": "매출과 비용 구조의 변화가 이익 체력 개선으로 이어지고 있는가?",
        "market_composite": "절대 주가 회복이 시장 대비 선호 회복으로 이어졌는가?",
        "peer_market": "국내 peer 안에서 시장 선호 회복이 확인되는가?",
        "peer_profitability": "국내 peer 대비 재무 우위가 투자 매력으로 충분히 전환됐는가?",
        "financial_stability": "재무 안정성이 하방 방어를 넘어 재평가 근거가 될 수 있는가?",
    }
    return questions.get(role, f"{company}의 {title} 차트가 현재 투자 판단에 어떤 의미를 갖는가?")


def _positive_signal(role: str, visible: list[Any], snapshot: dict[str, Any]) -> str:
    if visible:
        return str(visible[0])
    if role.startswith("peer"):
        return "동일 기준일 국내 peer 대비 상대 위치를 확인할 수 있다."
    if snapshot:
        return f"확인 가능한 핵심 수치: {_compact_snapshot(snapshot)}"
    return "차트가 투자 판단에 필요한 핵심 지표를 제공한다."


def _counter_signal(role: str, insights: dict[str, Any], block: dict[str, Any]) -> str:
    readthrough = str(insights.get("recommendation_readthrough") or "").strip()
    if readthrough:
        return readthrough
    limit = str(block.get("interpretation_limit") or "").strip()
    if limit:
        return limit
    if role.startswith("peer"):
        return "비교 범위와 결측 데이터, 가치평가 부재를 함께 고려해야 한다."
    return "현재 차트만으로 투자의견을 단독 변경하는 근거를 만들 수 없다."


def _monitoring_trigger(role: str, watch_points: list[Any]) -> str:
    if watch_points:
        return ", ".join(str(item) for item in watch_points[:3])
    defaults = {
        "market_composite": "초과수익률과 상대강도의 동시 반전",
        "peer_market": "peer 대비 수익률 순위와 상대강도 개선",
        "peer_profitability": "연간 기준 수익성 유지와 상대성과 개선",
        "financial_margin": "연간 기준 공헌이익률 유지와 판관비율 안정",
        "financial_stability": "유동성 유지와 레버리지 상승 여부",
    }
    return defaults.get(role, "후속 데이터에서 긍정 신호의 지속성과 반대 신호 완화 여부")


def _compact_snapshot(snapshot: dict[str, Any]) -> str:
    items = []
    for key, value in snapshot.items():
        if value is None or isinstance(value, (dict, list)):
            continue
        items.append(f"{key}={value}")
        if len(items) >= 4:
            break
    return ", ".join(items)
