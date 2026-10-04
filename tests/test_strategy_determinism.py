"""Strategy input order and repeated card references are deterministic."""

import unittest

from Agent_Team.Strategy_Agent.decision import _dedupe_card_references
from Agent_Team.Strategy_Agent.packet import _margin_changes


class MarginChangeOrderTests(unittest.TestCase):
    def test_follows_current_mapping_order(self):
        current = {"operating_margin": 0.10, "net_margin": 0.05, "gross_margin": 0.30}
        previous = {"gross_margin": 0.28, "net_margin": 0.06, "operating_margin": 0.08}
        self.assertEqual(list(_margin_changes(current, previous)), ["operating_margin", "net_margin", "gross_margin"])

    def test_skips_missing_and_non_finite_values(self):
        current = {"a": 0.2, "b": float("nan"), "c": 0.1}
        previous = {"a": 0.1, "b": 0.1}
        self.assertEqual(list(_margin_changes(current, previous)), ["a"])


class DuplicateReferenceTests(unittest.TestCase):
    def test_drops_repeats_everywhere_keeping_first(self):
        output = {
            "strategy_brief": {"outlook": {"text": "t", "card_keys": ["x", "y", "x"]}},
            "coverage_assessment": {"events": {"status": "used", "card_keys": ["n1", "n1", "n2"]}},
            "evidence_plan": {
                "decision_basis_cards": [{"card_key": "x", "note": 1}, {"card_key": "x", "note": 2}],
                "report_context_cards": [{"card_key": "y"}],
            },
        }
        _dedupe_card_references(output)
        self.assertEqual(output["strategy_brief"]["outlook"]["card_keys"], ["x", "y"])
        self.assertEqual(output["coverage_assessment"]["events"]["card_keys"], ["n1", "n2"])
        self.assertEqual(output["evidence_plan"]["decision_basis_cards"], [{"card_key": "x", "note": 1}])
        self.assertEqual(output["strategy_brief"]["outlook"]["text"], "t")


if __name__ == "__main__":
    unittest.main()
