from __future__ import annotations

from copy import deepcopy

from single_llm.contracts import ANALYSIS_SECTIONS, REPORT_VERSION
from single_llm.validator import validate_report


def _bundle() -> dict:
    return {
        "selected_date": "2025-10-31",
        "information_cutoff_date": "2025-10-30",
        "decision_horizon": "1개월",
        "target": {"company_name": "테스트기업"},
        "evidence_catalog": [
            {
                "evidence_id": f"EVIDENCE_{domain.upper()}",
                "domain": domain,
                "payload": {"value": 100, "date": "2025-10-30"},
            }
            for domain in ("financial", "news", "market", "valuation")
        ],
    }


def _claim(evidence_id: str = "EVIDENCE_FINANCIAL") -> dict:
    return {
        "claim": "확인된 항목",
        "observation": "값은 100이다.",
        "interpretation": "참고 가능한 근거다.",
        "investment_effect": "NEUTRAL",
        "evidence_ids": [evidence_id],
    }


def _report() -> dict:
    domain_ids = [
        "EVIDENCE_FINANCIAL",
        "EVIDENCE_NEWS",
        "EVIDENCE_MARKET",
        "EVIDENCE_VALUATION",
    ]
    return {
        "report_version": REPORT_VERSION,
        "metadata": {
            "report_title": "테스트 보고서",
            "company_name": "테스트기업",
            "selected_date": "2025-10-31",
            "decision_horizon": "1개월",
        },
        "investment_call": {
            "recommendation": "HOLD",
            "conviction": "MEDIUM",
            "thesis": "값 100을 기준으로 균형을 확인했다.",
            "current_price_rationale": "값 100을 참고했다.",
            "forward_outlook": "추가 확인이 필요하다.",
            "valuation_view": "제공된 값 100만 사용했다.",
            "residual_uncertainty": "자료 범위가 제한된다.",
            "upgrade_conditions": [
                {
                    "condition": "값 100 개선",
                    "why_it_matters": "긍정 근거 강화",
                    "evidence_ids": ["EVIDENCE_FINANCIAL"],
                }
            ],
            "downgrade_conditions": [
                {
                    "condition": "값 100 훼손",
                    "why_it_matters": "부정 근거 강화",
                    "evidence_ids": ["EVIDENCE_FINANCIAL"],
                }
            ],
            "evidence_ids": domain_ids,
        },
        "key_evidence": [
            {
                "label": "핵심 근거",
                "observed_fact": "값 100",
                "interpretation": "중립",
                "investment_effect": "NEUTRAL",
                "evidence_ids": [domain_ids[index % len(domain_ids)]],
            }
            for index in range(5)
        ],
        "analysis": {
            section: [_claim(domain_ids[index % len(domain_ids)]), _claim(domain_ids[(index + 1) % len(domain_ids)])]
            for index, section in enumerate(ANALYSIS_SECTIONS)
        },
        "risks": [
            {
                "risk": "값 100 변동",
                "current_evidence": "현재 값 100",
                "monitoring_trigger": "값 100 변화",
                "potential_impact": "불확실성 확대",
                "evidence_ids": ["EVIDENCE_MARKET"],
            }
            for _ in range(3)
        ],
        "data_limits": [
            {
                "limitation": "표본 제한",
                "report_impact": "일반화할 수 없다.",
                "evidence_ids": [],
            }
        ],
    }


def test_valid_report_passes_grounding() -> None:
    result = validate_report(
        _report(),
        bundle=_bundle(),
        strict_numeric_grounding=True,
    )
    assert result["status"] == "valid"
    assert result["numeric_grounding"]["unsupported_count"] == 0


def test_unknown_evidence_id_fails() -> None:
    report = deepcopy(_report())
    report["key_evidence"][0]["evidence_ids"] = ["UNKNOWN"]
    result = validate_report(
        report,
        bundle=_bundle(),
        strict_numeric_grounding=False,
    )
    assert result["status"] == "invalid"
    assert any("unknown evidence ID" in error for error in result["errors"])


def test_korean_currency_display_units_are_grounded() -> None:
    bundle = _bundle()
    bundle["evidence_catalog"][0]["payload"] = {
        "revenue_krw": 354_042_316_121,
        "market_cap_krw": 7_940_000_000_000,
        "close_krw": 113_400,
        "operating_loss_krw": -3_300_000_000,
    }
    report = _report()
    report["analysis"]["business_and_financial"][0]["observation"] = (
        "매출은 3,540억 원이고 영업손실은 33억 원이다."
    )
    report["analysis"]["market_and_valuation"][0]["observation"] = (
        "시가총액은 7조 9,400억 원이다."
    )
    report["analysis"]["market_and_valuation"][0]["evidence_ids"] = [
        "EVIDENCE_FINANCIAL"
    ]
    report["analysis"]["market_and_valuation"][1]["observation"] = (
        "종가는 11만 3,400원이다."
    )
    report["analysis"]["market_and_valuation"][1]["evidence_ids"] = [
        "EVIDENCE_FINANCIAL"
    ]

    result = validate_report(
        report,
        bundle=bundle,
        strict_numeric_grounding=True,
    )

    assert result["status"] == "valid"
    assert result["numeric_grounding"]["unsupported_count"] == 0


def test_inline_catalog_evidence_id_extends_string_level_grounding() -> None:
    bundle = _bundle()
    bundle["evidence_catalog"].append(
        {
            "evidence_id": "EVIDENCE_NEWS_DETAIL",
            "domain": "news",
            "payload": {"snippet": "연매출 목표는 6400억원이다."},
        }
    )
    report = _report()
    report["investment_call"]["forward_outlook"] = (
        "연매출 목표는 6400억원이다. [EVIDENCE_NEWS_DETAIL]"
    )

    result = validate_report(
        report,
        bundle=bundle,
        strict_numeric_grounding=True,
    )

    assert result["status"] == "valid"
    assert result["numeric_grounding"]["unsupported_count"] == 0


def test_numeric_horizon_in_evidence_key_is_grounded() -> None:
    bundle = _bundle()
    bundle["evidence_catalog"][2]["payload"] = {
        "stock_relative_strength_60": -0.1116,
        "date": "2025-10-30",
    }
    report = _report()
    report["analysis"]["market_and_valuation"][0]["observation"] = (
        "60일 상대강도는 -0.1116이다."
    )
    report["analysis"]["market_and_valuation"][0]["evidence_ids"] = [
        "EVIDENCE_MARKET"
    ]

    result = validate_report(
        report,
        bundle=bundle,
        strict_numeric_grounding=True,
    )

    assert result["status"] == "valid"
    assert result["numeric_grounding"]["unsupported_count"] == 0
