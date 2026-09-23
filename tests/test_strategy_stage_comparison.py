"""Experimental separation preserves source evidence and production contracts."""
import copy
import importlib.util
from pathlib import Path
import unittest
from jsonschema import Draft202012Validator, ValidationError

spec = importlib.util.spec_from_file_location("stage_comparison", Path(__file__).resolve().parents[1] / "scripts/compare_strategy_stages.py")
experiment = importlib.util.module_from_spec(spec)
spec.loader.exec_module(experiment)


class StrategyStageComparisonTests(unittest.TestCase):
    def fixture(self):
        context = {"evidence_cards": {f"financial.item_{i}": {"primary_observation": {"value": i}} for i in range(16)},
                   "domain_handoffs": {"financial": {"main_view": "기존 하위 해석"}},
                   "applicability_notes": {"shared": ["기간 주의"], "by_card": {}}}
        linked = {"text": "확인된 변화와 전망의 가정을 구분한다.", "card_keys": list(context["evidence_cards"])}
        memo = {"analysis_version": "predecision_analysis_v1", **{key: copy.deepcopy(linked) for key in (
            "earnings_review", "outlook", "price_assessment", "alternative_interpretation")}}
        return context, memo

    def test_analysis_contract_has_no_opinion_or_grade(self):
        context, memo = self.fixture()
        schema = experiment.analysis_response_format(context)["json_schema"]["schema"]
        Draft202012Validator(schema).validate(memo)
        memo["recommendation"] = "Hold"
        with self.assertRaises(ValidationError):
            Draft202012Validator(schema).validate(memo)

    def test_all_original_input_reaches_decision_without_cap(self):
        context, memo = self.fixture()
        before = copy.deepcopy(context)
        single = experiment.decision_payload(context)
        split = experiment.decision_payload(context, memo)
        self.assertEqual(single[experiment.CONTEXT_VERSION], split[experiment.CONTEXT_VERSION])
        self.assertEqual(split["preliminary_analysis"], memo)
        self.assertEqual(context, before)
        self.assertEqual(len(split["preliminary_analysis"]["earnings_review"]["card_keys"]), 16)

    def test_unknown_reference_rejected_but_missing_data_refs_can_be_empty(self):
        context, memo = self.fixture()
        validator = Draft202012Validator(experiment.analysis_response_format(context)["json_schema"]["schema"])
        memo["price_assessment"] = {"text": "가격 근거가 제공되지 않았다.", "card_keys": []}
        validator.validate(memo)
        memo["earnings_review"]["card_keys"].append("invented.source")
        with self.assertRaises(ValidationError):
            validator.validate(memo)

    def test_no_lexical_gate_rewrites_analysis(self):
        context, memo = self.fixture()
        # Whether the model obeyed the no-opinion instruction is reviewed,
        # not repaired with keyword deletion or repeated generation.
        memo["outlook"]["text"] = "중립 의견이 필요하다는 문장은 이 단계의 역할 이탈이다."
        original = copy.deepcopy(memo)
        Draft202012Validator(experiment.analysis_response_format(context)["json_schema"]["schema"]).validate(memo)
        self.assertEqual(memo, original)


if __name__ == "__main__":
    unittest.main()
