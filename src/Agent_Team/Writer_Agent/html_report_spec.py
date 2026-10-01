"""Fixed HTML report structure for the Writer Agent."""

from __future__ import annotations

import re
from typing import Any


SUPPORTED_INVESTMENT_HORIZONS = (
    "12개월",
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
        "display_title": "투자 판단 요약",
        "items": [
            ("section_analysis", "투자 의견과 핵심 논거", "text"),
        ],
    },
    {
        "key": "business_market_context",
        "id": "business-market-context",
        "title": "Business & Market Context",
        "display_title": "최근 실적과 가격 평가",
        "items": [
            ("section_analysis", "손익·현금흐름과 시장 가격", "text"),
        ],
    },
    {
        "key": "key_evidence_table",
        "id": "key-evidence-table",
        "title": "Key Evidence Table",
        "display_title": "핵심 판단 근거",
        "items": [
            ("evidence_table", "판단을 구성한 주요 근거", "table"),
        ],
    },
    {
        "key": "catalysts_execution",
        "id": "catalysts-execution",
        "title": "Catalysts & Execution",
        "display_title": "향후 12개월 전망",
        "items": [
            ("section_analysis", "성장 동인과 전망의 전제", "text"),
        ],
    },
    {
        "key": "risk_monitoring_matrix",
        "id": "risk-monitoring-matrix",
        "title": "Risk & Monitoring Matrix",
        "display_title": "리스크 점검",
        "items": [
            ("risk_monitoring_table", "현재 위험과 투자 판단에 미치는 영향", "table"),
        ],
    },
    {
        "key": "data_limits",
        "id": "data-limits",
        "title": "Data Limits",
        "display_title": "데이터 기준과 한계",
        "items": [
            ("section_analysis", "자료 시점과 해석 범위", "text"),
        ],
    },
]


RISK_DISPLAY_COLUMNS = (
    "리스크 요인",
    "현재 확인된 내용",
    "투자 판단에 미치는 영향",
)


TABLE_ITEM_KEYS = {
    "evidence_table",
    "risk_monitoring_table",
}


ENGLISH_GRADE_LABELS = {
    "Buy": "매수",
    "Hold": "중립",
    "Sell": "매도",
    "BUY": "매수",
    "HOLD": "중립",
    "SELL": "매도",
}
# Whole-word grade labels only: letters on either side (Holdings, Buyback)
# mean the token is part of another English word and must stay unchanged.
# A directly attached Korean particle is captured so it can agree with the
# Korean label's final consonant (e.g. a label ending in a vowel takes 를).
_ENGLISH_GRADE_PATTERN = re.compile(
    r"(?<![A-Za-z])(Buy|Hold|Sell|BUY|HOLD|SELL)(?![A-Za-z])"
    r"(으로|로|(?:을|를|은|는|이|가|과|와)(?![가-힣]))?"
)
_PARTICLE_PAIRS = {
    # particle: (after a final consonant, after a vowel)
    "을": ("을", "를"),
    "를": ("을", "를"),
    "은": ("은", "는"),
    "는": ("은", "는"),
    "이": ("이", "가"),
    "가": ("이", "가"),
    "과": ("과", "와"),
    "와": ("과", "와"),
    "으로": ("으로", "로"),
    "로": ("으로", "로"),
}
_VISIBLE_GRADE_LEAK_PATTERN = re.compile(r"(?<![A-Za-z])(Buy|Hold|Sell)(?![A-Za-z])", re.IGNORECASE)
# Internal handoff field names that must never reach reader-visible text.
INTERNAL_FIELD_NAMES = (
    "origin_type",
    "source_date",
    "source_type",
    "source_domain",
    "source_domains",
    "recent_raw_event",
    "deterministic_derived",
    "interpretation_ko",
    "investment_implication",
    "cited_sources",
    "valuation_date",
    "date_policy",
    "metric_or_event",
    "period_basis",
    "reader_observation",
    "primary_observation",
    "strategy_interpretation",
)
_INTERNAL_FIELD_PATTERN = re.compile(
    r"(?<![A-Za-z0-9_])(" + "|".join(INTERNAL_FIELD_NAMES) + r")(?![A-Za-z0-9_])"
)
# Generic English words count as leaks only in a stringified "key: value" form.
_INTERNAL_GENERIC_KEY_PATTERN = re.compile(
    r"(?<![A-Za-z0-9_])(metrics|status|unit|value)(?=\s*:)"
)


def replace_english_grade_labels(text: str) -> str:
    """Show whole-word English grade labels with their Korean reader labels."""

    return _ENGLISH_GRADE_PATTERN.sub(_korean_grade_label, str(text))


def _korean_grade_label(match: re.Match[str]) -> str:
    label = ENGLISH_GRADE_LABELS[match.group(1)]
    particle = match.group(2)
    if not particle:
        return label
    final_code = (ord(label[-1]) - 0xAC00) % 28
    if particle in {"으로", "로"} and final_code == 8:
        # A final ㄹ takes 로, like a vowel.
        return f"{label}로"
    return label + _PARTICLE_PAIRS[particle][0 if final_code else 1]


def reader_label_leaks(visible_text: str) -> list[str]:
    """Return English grade labels or internal field names left in reader text."""

    found = [match.group(0) for match in _VISIBLE_GRADE_LEAK_PATTERN.finditer(visible_text)]
    found.extend(match.group(0) for match in _INTERNAL_FIELD_PATTERN.finditer(visible_text))
    found.extend(
        match.group(0) for match in _INTERNAL_GENERIC_KEY_PATTERN.finditer(visible_text)
    )
    return sorted(set(found))


def has_data_limit_content(report_payload: dict[str, Any]) -> bool:
    """Whether an optional data-limits section has any authored reader text."""
    sections = report_payload.get("sections") or {}
    item = (sections.get("data_limits") or {}).get("section_analysis") or {}
    if isinstance(item, str):
        return bool(item.strip())
    return any(str(value or "").strip()
               for key in ("paragraphs", "bullets") for value in item.get(key) or [])


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
