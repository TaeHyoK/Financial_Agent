#!/usr/bin/env python3
import sys
from pathlib import Path
from typing import Any, Dict, List


sys.path.insert(0, str(Path(__file__).resolve().parent))

from run_validation import (  # noqa: E402
    build_rule_checks,
    evaluate_claim,
    evidence_by_claim,
    get_claims,
    get_key_evidence,
    has_investment_decision,
)


def base_payload() -> Dict[str, Any]:
    return {
        "agent_name": "Financial Analyst Agent",
        "target_entity": {
            "company_name": "테스트",
            "ticker": "000000.KS",
            "corp_code": "00000000",
            "as_of_date": "2025-10-31",
        },
        "final_financial_opinion": {
            "core_opinion_ko": "검증 테스트용 의견이다.",
            "fundamental_direction": "neutral",
            "main_cautions_ko": [],
            "not_investment_decision": True,
        },
        "financial_claims": [],
        "key_evidence": [],
        "validation": {
            "overall_status": "partially_consistent",
            "summary_ko": "테스트용 검증 요약이다.",
            "key_validated_claims": [],
            "key_conflicts": [],
            "unsupported_or_overextended_claims": [],
        },
        "sy_handoff": {
            "reconciliation_flags": [],
        },
        "confidence": {
            "grade": "medium",
            "reason_ko": "테스트용이다.",
        },
    }


def claim(claim_id: str, **overrides: Any) -> Dict[str, Any]:
    item = {
        "claim_id": claim_id,
        "claim_ko": "DART 기준 매출은 개선 방향이다.",
        "financial_dimension": "growth",
        "status": "active",
        "dart_anchor_summary_ko": "DART 기준 매출 증가가 확인된다.",
        "context_summary_ko": "",
        "caution_ko": "",
        "action_for_sy": "use_normally",
    }
    item.update(overrides)
    return item


def evidence(evidence_id: str, claim_id: str, source: str = "DART", **overrides: Any) -> Dict[str, Any]:
    item = {
        "evidence_id": evidence_id,
        "claim_id": claim_id,
        "source": source,
        "metric_or_event": "revenue",
        "period": "2025 Q3 YTD",
        "value": 100,
        "period_basis": "YTD",
        "interpretation_ko": "매출 개선의 직접 근거다.",
    }
    item.update(overrides)
    return item


def evaluate_case(payload: Dict[str, Any]) -> List[Dict[str, Any]]:
    mapping = evidence_by_claim(get_key_evidence(payload))
    investment = has_investment_decision(payload)
    return [
        evaluate_claim(item, mapping.get(item["claim_id"], []), investment)
        for item in get_claims(payload)
    ]


def one_claim_payload(case_claim: Dict[str, Any], case_evidence: List[Dict[str, Any]]) -> Dict[str, Any]:
    payload = base_payload()
    payload["financial_claims"] = [case_claim]
    payload["key_evidence"] = case_evidence
    return payload


def assert_decision(name: str, payload: Dict[str, Any], expected: str) -> None:
    result = evaluate_case(payload)[0]
    actual = result["decision"]
    if actual != expected:
        raise AssertionError(f"{name}: expected {expected}, got {actual}. reason={result['reason_ko']}")
    print(f"PASS {name}: {actual}")


def main() -> None:
    print("Financial SY rule-based regression tests are obsolete; LLM-only evaluation is now the supported path.")
    return

    assert_decision(
        "valid_dart_claim",
        one_claim_payload(
            claim("T001", caution_ko="Q3 YTD 기준이므로 연간 확정 개선으로 단정하지 않는다."),
            [evidence("E001", "T001")],
        ),
        "keep",
    )
    assert_decision(
        "no_evidence",
        one_claim_payload(claim("T002", dart_anchor_summary_ko="", context_summary_ko=""), []),
        "delete",
    )
    assert_decision(
        "no_dart_financial_claim",
        one_claim_payload(
            claim("T003", dart_anchor_summary_ko="뉴스상 매출 기대가 있다.", context_summary_ko="뉴스만 존재한다."),
            [evidence("E003", "T003", "News", period_basis="context_only")],
        ),
        "delete",
    )
    assert_decision(
        "news_primary_financial_claim",
        one_claim_payload(
            claim("T004", dart_anchor_summary_ko="", context_summary_ko="뉴스만 근거다."),
            [evidence("E004", "T004", "News", period_basis="context_only")],
        ),
        "delete",
    )
    assert_decision(
        "yfinance_primary_financial_claim",
        one_claim_payload(
            claim("T005", dart_anchor_summary_ko="", context_summary_ko="주가 상승만 근거다."),
            [evidence("E005", "T005", "Y-Finance", metric_or_event="stock_return", period_basis="context_only")],
        ),
        "delete",
    )
    investment_payload = one_claim_payload(claim("T006"), [evidence("E006", "T006")])
    investment_payload["final_financial_opinion"]["core_opinion_ko"] = "매수 의견을 제시한다."
    assert_decision("investment_decision_language", investment_payload, "delete")
    assert_decision(
        "ytd_overstated_as_full_year",
        one_claim_payload(
            claim(
                "T007",
                claim_ko="2025년 연간 기준으로 개선이 확인된다.",
                dart_anchor_summary_ko="DART 기준 2025년 Q3 YTD 수치다.",
            ),
            [evidence("E007", "T007")],
        ),
        "delete",
    )
    assert_decision(
        "risk_claim_with_context_evidence",
        one_claim_payload(
            claim("T008", financial_dimension="risk", action_for_sy="use_with_caution"),
            [evidence("E008", "T008", "News", period_basis="context_only")],
        ),
        "keep",
    )
    assert_decision(
        "missing_required_metrics_requires_revision",
        one_claim_payload(
            claim("T011", financial_dimension="liquidity", dart_anchor_summary_ko="DART 기준 유동성은 안정적이다."),
            [evidence("E011", "T011", metric_or_event="liquidity snapshot", value={"current_ratio": 5.5})],
        ),
        "revise",
    )
    assert_decision(
        "ytd_claim_without_period_caution_requires_revision",
        one_claim_payload(
            claim("T012", claim_ko="매출은 개선 방향이다.", dart_anchor_summary_ko="DART 기준 매출 증가가 확인된다."),
            [evidence("E012", "T012", period_basis="YTD")],
        ),
        "revise",
    )
    overbought_payload = one_claim_payload(
        claim(
            "T009",
            context_summary_ko="RSI는 과매수 직전 구간이다.",
            caution_ko="Q3 YTD 기준이므로 연간 확정 개선으로 단정하지 않는다.",
        ),
        [evidence("E009", "T009")]
    )
    assert_decision("overbought_not_investment_decision", overbought_payload, "keep")
    cash_holding_payload = one_claim_payload(
        claim("T010", financial_dimension="liquidity", dart_anchor_summary_ko="현금 보유가 유동부채를 상회한다."),
        [
            evidence(
                "E010",
                "T010",
                metric_or_event="balance sheet and liquidity snapshot",
                period_basis="POINT_IN_TIME",
                value={"current_ratio": 5.5, "cash_ratio": 2.1, "current_assets": 500, "current_liabilities": 90},
            )
        ],
    )
    cash_holding_payload["detailed_analysis"] = {
        "liquidity": {
            "interpretation": "현금 보유가 유동부채를 상회한다.",
            "supporting_features": {},
        }
    }
    assert_decision("cash_holding_not_investment_decision", cash_holding_payload, "keep")

    claim_count_payload = base_payload()
    claim_count_payload["financial_claims"] = [claim(f"T{i:03d}") for i in range(25)]
    claim_count_payload["key_evidence"] = [evidence(f"E{i:03d}", f"T{i:03d}") for i in range(25)]
    evaluations = evaluate_case(claim_count_payload)
    rules = build_rule_checks(claim_count_payload, get_claims(claim_count_payload), evaluations)
    r002 = next(rule for rule in rules if rule["rule_id"] == "R002")
    if r002["status"] != "fail":
        raise AssertionError(f"claim_count_over_limit: expected R002 fail, got {r002['status']}")
    print("PASS claim_count_over_limit: R002 fail")


if __name__ == "__main__":
    main()
