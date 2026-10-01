"""Opt-in decision style must not change the default Strategy prompt."""

import unittest

from Agent_Team.Strategy_Agent.agent import decision_generation_prompt, decision_prompt
from Agent_Team.Strategy_Agent.cli import build_parser


class StrategyDecisionStyleTest(unittest.TestCase):
    def test_standard_prompt_is_unchanged(self):
        self.assertEqual(
            decision_generation_prompt("annual", context_mode="compact_cards"),
            decision_prompt("annual"),
        )

    def test_candidate_is_opt_in(self):
        candidate = decision_generation_prompt(
            "annual", context_mode="compact_cards", decision_style="evidence_weighted_buy"
        )
        self.assertIn("실험 후보: 확인된 개선과 위험 노출의 무게 비교", candidate)
        self.assertIn("실험 후보: 가격 주장 범위", candidate)
        self.assertNotIn("실험 후보:", decision_generation_prompt("annual", context_mode="compact_cards"))

    def test_unknown_style_fails(self):
        with self.assertRaises(ValueError):
            decision_generation_prompt("annual", context_mode="compact_cards", decision_style="unknown")

    def test_cli_defaults_to_standard(self):
        parser = build_parser()
        self.assertEqual(parser.parse_args([]).decision_style, "standard")
        self.assertEqual(
            parser.parse_args(["--decision-style", "evidence_weighted_buy"]).decision_style,
            "evidence_weighted_buy",
        )


if __name__ == "__main__":
    unittest.main()
