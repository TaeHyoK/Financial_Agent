"""The stated price position must not contradict the sign of observed returns."""

import unittest

from test_annual_context import strategy_fixture
from Agent_Team.Strategy_Agent.decision import parse_rationale_categories, validate_strategy_decision

TREND_KEY = "market.absolute_trend"


def fixture_with_returns(rationale: str, returns: dict[str, object] | None):
    """Strategy fixture whose rationale opening and price-trend card are set."""

    _, context, decision, _ = strategy_fixture()
    if returns is not None:
        context["evidence_cards"][TREND_KEY] = {
            "card_key": TREND_KEY, "domain": "market", "card_type": "absolute_trend",
            "label": "절대 가격 추세", "primary_observation": {"metrics": returns},
        }
    decision["strategy_brief"]["decision_rationale"]["text"] = rationale
    return context, decision


class ParseRationaleTests(unittest.TestCase):
    def test_parses_each_category(self):
        cases = {
            "사업 궤적은 개선, 가격 위치는 반대다. 이어지는 설명.": ("개선", "반대"),
            "사업 궤적은 악화, 가격 위치는 같은 방향이다.": ("악화", "같은 방향"),
            "사업 궤적은 유지, 가격 위치는 뚜렷하지 않음이다.": ("유지", "뚜렷하지 않음"),
            "사업 궤적은 개선, 가격 위치는 판정 불가다.": ("개선", "판정 불가"),
            "  사업궤적은 '개선', 가격 위치는 '같은  방향'이다.": ("개선", "같은 방향"),
        }
        for text, expected in cases.items():
            self.assertEqual(parse_rationale_categories(text), expected, text)

    def test_unparsable_opening_returns_none(self):
        for text in ("성장은 긍정적이다.", "가격 위치는 반대, 사업 궤적은 개선이다.", "", None):
            self.assertIsNone(parse_rationale_categories(text))


class PricePositionSignTests(unittest.TestCase):
    def assert_passes(self, rationale, returns):
        context, decision = fixture_with_returns(rationale, returns)
        validate_strategy_decision(decision, context=context)

    def assert_fails(self, rationale, returns):
        context, decision = fixture_with_returns(rationale, returns)
        with self.assertRaisesRegex(ValueError, "cannot be 같은 방향"):
            validate_strategy_decision(decision, context=context)

    def test_improving_with_both_returns_negative_cannot_be_same_direction(self):
        self.assert_fails("사업 궤적은 개선, 가격 위치는 같은 방향이다.",
                          {"stock_return_12m": -0.084, "stock_return_3m": -0.127})

    def test_worsening_with_both_returns_positive_cannot_be_same_direction(self):
        self.assert_fails("사업 궤적은 악화, 가격 위치는 같은 방향이다.",
                          {"stock_return_12m": 0.21, "stock_return_3m": 0.05})

    def test_consistent_positions_pass(self):
        self.assert_passes("사업 궤적은 개선, 가격 위치는 반대다.",
                           {"stock_return_12m": -0.084, "stock_return_3m": -0.127})
        self.assert_passes("사업 궤적은 개선, 가격 위치는 같은 방향이다.",
                           {"stock_return_12m": 0.30, "stock_return_3m": 0.10})
        self.assert_passes("사업 궤적은 악화, 가격 위치는 같은 방향이다.",
                           {"stock_return_12m": -0.30, "stock_return_3m": -0.10})
        self.assert_passes("사업 궤적은 유지, 가격 위치는 같은 방향이다.",
                           {"stock_return_12m": -0.30, "stock_return_3m": -0.10})

    def test_mixed_signs_pass(self):
        self.assert_passes("사업 궤적은 개선, 가격 위치는 같은 방향이다.",
                           {"stock_return_12m": 0.15, "stock_return_3m": -0.05})
        self.assert_passes("사업 궤적은 악화, 가격 위치는 같은 방향이다.",
                           {"stock_return_12m": -0.15, "stock_return_3m": 0.05})

    def test_missing_returns_skip_the_check(self):
        rationale = "사업 궤적은 개선, 가격 위치는 같은 방향이다."
        self.assert_passes(rationale, None)
        self.assert_passes(rationale, {"stock_return_12m": -0.084})
        self.assert_passes(rationale, {"stock_return_12m": -0.084, "stock_return_3m": None})
        self.assert_passes(rationale, {"stock_return_12m": float("nan"), "stock_return_3m": -0.1})

    def test_unparsable_rationale_skips_the_check(self):
        self.assert_passes("가격이 이미 같은 방향으로 움직였다고 본다.",
                           {"stock_return_12m": -0.084, "stock_return_3m": -0.127})


if __name__ == "__main__":
    unittest.main()
