from __future__ import annotations

from single_llm.renderer import render_report_html


def test_renderer_removes_bracketed_internal_evidence_ids() -> None:
    report = {
        "metadata": {
            "report_title": "테스트 보고서",
            "company_name": "테스트기업",
            "selected_date": "2025-10-31",
            "decision_horizon": "1개월",
        },
        "investment_call": {
            "recommendation": "HOLD",
            "conviction": "MEDIUM",
            "thesis": (
                "근거가 확인됐다. [FINANCIAL_TARGET_DART_MAIN, "
                "NEWS_TARGET_NEWS_RAW_2025-10-22_49]"
            ),
        },
        "key_evidence": [],
        "analysis": {},
        "risks": [],
        "data_limits": [],
    }

    html = render_report_html(report)

    assert "근거가 확인됐다." in html
    assert "FINANCIAL_TARGET_DART_MAIN" not in html
    assert "NEWS_TARGET_NEWS_RAW_2025-10-22_49" not in html


def test_renderer_removes_trailing_evidence_id_label() -> None:
    report = {
        "metadata": {
            "report_title": "테스트 보고서",
            "company_name": "테스트기업",
            "selected_date": "2025-10-31",
            "decision_horizon": "1개월",
        },
        "investment_call": {
            "recommendation": "HOLD",
            "conviction": "MEDIUM",
            "thesis": (
                "근거가 확인됐다. evidence_id: FINANCIAL_TARGET_DART_MAIN, "
                "NEWS_TARGET_NEWS_RAW_2025-10-22_49"
            ),
        },
        "key_evidence": [],
        "analysis": {},
        "risks": [],
        "data_limits": [],
    }

    html = render_report_html(report)

    assert "근거가 확인됐다." in html
    assert "evidence_id:" not in html
    assert "FINANCIAL_TARGET_DART_MAIN" not in html
