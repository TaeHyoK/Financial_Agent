"""Fixed HTML report structure for the Writer Agent."""

from __future__ import annotations

from typing import Any


SUPPORTED_INVESTMENT_HORIZONS = (
    "6~12개월",
    "1개월",
    "3개월",
    "6개월",
    "기간 미지정",
)
INVESTMENT_THESIS_SECTION_KEY = "investment_call_thesis"
INVESTMENT_THESIS_ITEM_KEY = "section_analysis"
REPORT_DISCLAIMER = (
    "본 리포트는 투자 판단을 위한 참고자료이며, 최종 투자 결정과 그에 따른 책임은 "
    "투자자 본인에게 있습니다. 불확실성과 변동 가능성을 충분히 고려하여 보수적인 "
    "관점에서 접근하시기 바랍니다."
)


REPORT_SECTIONS: list[dict[str, Any]] = [
    {
        "key": "investment_call_thesis",
        "id": "investment-call-thesis",
        "title": "Investment Call & Thesis",
        "display_title": "투자의견 요약",
        "items": [
            ("section_analysis", "투자기간 판단 근거", "text"),
        ],
    },
    {
        "key": "business_market_context",
        "id": "business-market-context",
        "title": "Business & Market Context",
        "display_title": "사업·시장 현황",
        "items": [
            ("section_analysis", "매출 구성과 주가 흐름", "text"),
        ],
    },
    {
        "key": "key_evidence_table",
        "id": "key-evidence-table",
        "title": "Key Evidence Table",
        "display_title": "핵심 판단 근거",
        "items": [
            ("evidence_table", "투자의견을 구성한 주요 증거", "table"),
        ],
    },
    {
        "key": "catalysts_execution",
        "id": "catalysts-execution",
        "title": "Catalysts & Execution",
        "display_title": "주요 이벤트",
        "items": [
            ("section_analysis", "성장 촉매와 실적 연결 여부", "text"),
        ],
    },
    {
        "key": "risk_monitoring_matrix",
        "id": "risk-monitoring-matrix",
        "title": "Risk & Monitoring Matrix",
        "display_title": "리스크 점검",
        "items": [
            ("risk_monitoring_table", "현재 위험과 향후 확인사항", "table"),
        ],
    },
    {
        "key": "data_limits",
        "id": "data-limits",
        "title": "Data Limits",
        "display_title": "데이터 한계",
        "items": [
            ("section_analysis", "해석 시 유의사항", "text"),
        ],
    },
]


KEY_EVIDENCE_DISPLAY_COLUMNS = (
    "핵심 근거",
    "확인된 수치·사실",
    "투자 해석",
    "영향",
)

LABEL_FREE_KEY_EVIDENCE_DISPLAY_COLUMNS = (
    "핵심 근거",
    "확인된 수치·사실",
    "투자 해석",
    "판단상 역할",
)

RISK_DISPLAY_COLUMNS = (
    "리스크 요인",
    "현재 확인된 내용",
    "향후 점검사항",
)


TABLE_ITEM_KEYS = {
    "evidence_table",
    "risk_monitoring_table",
}


def investment_horizon_heading(horizon: Any) -> str:
    """Return the reader-visible thesis heading for one Strategy horizon."""

    normalized = str(horizon or "").strip()
    return f"{normalized} 판단 근거" if normalized else "투자기간 판단 근거"


def resolve_report_item_title(
    *,
    section_key: str,
    item_key: str,
    default_title: str,
    metadata: dict[str, Any],
) -> str:
    """Resolve metadata-dependent item titles without mutating the report spec."""

    if (
        section_key == INVESTMENT_THESIS_SECTION_KEY
        and item_key == INVESTMENT_THESIS_ITEM_KEY
    ):
        return investment_horizon_heading(metadata.get("investment_horizon"))
    return default_title
