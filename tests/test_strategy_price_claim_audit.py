"""Lexical price flags are diagnostics, not recommendation changes."""

import unittest

from Agent_Team.Strategy_Agent.price_claim_audit import audit_strategy_price_claims


class StrategyPriceClaimAuditTests(unittest.TestCase):
    def test_flags_market_belief_inference_with_market_card(self):
        decision = {"strategy_brief": {"price_assessment": {
            "text": "주가가 올랐다. 회복 기대가 이미 상당 부분 반영된 구간이다.",
            "card_keys": ["market.absolute_trend", "valuation.provider_reference"],
        }}}
        findings = audit_strategy_price_claims(decision)
        self.assertTrue(any(row["rule"] == "priced_in" for row in findings))
        self.assertTrue(all(row["status"] == "manual_review_required" for row in findings))

    def test_negated_claim_and_non_market_field_do_not_flag(self):
        decision = {"strategy_brief": {"price_assessment": {
            "text": "과거 수익률만으로 성장 기대가 이미 반영됐다고 단정할 수 없다.",
            "card_keys": ["market.absolute_trend"],
        }, "outlook": {
            "text": "사업 기대가 높아졌다.", "card_keys": ["financial.annual_trend"],
        }}}
        self.assertEqual(audit_strategy_price_claims(decision), [])

    def test_flags_directional_non_reflection_but_not_earnings_reflection(self):
        decision = {"strategy_brief": {
            "price_assessment": {"text": "주가가 강한 낙관을 선반영했다고 보기도 어렵다.",
                                 "card_keys": ["market.absolute_trend"]},
            "decision_rationale": {"text": "사업 구조 변화가 실적에 반영되기 시작했다.",
                                   "card_keys": ["market.absolute_trend", "financial.annual_trend"]},
        }}
        findings = audit_strategy_price_claims(decision)
        self.assertTrue(any("선반영" in row["matched_text"] for row in findings))
        self.assertFalse(any(row["field"] == "decision_rationale" for row in findings))

    def test_one_sentence_has_one_review_flag(self):
        decision = {"strategy_brief": {"price_assessment": {
            "text": "현재 주가에 기대가 이미 반영돼 시장 신뢰가 높아졌다.",
            "card_keys": ["market.absolute_trend"],
        }}}
        self.assertEqual(len(audit_strategy_price_claims(decision)), 1)

    def test_price_discount_comparison_is_not_a_priced_in_claim(self):
        decision = {"strategy_brief": {"price_assessment": {
            "text": "매수 의견의 핵심은 가격 할인보다 사업 개선의 지속성이다.",
            "card_keys": ["market.absolute_trend"],
        }}}
        self.assertEqual(audit_strategy_price_claims(decision), [])


if __name__ == "__main__":
    unittest.main()
