"""No semantic gates: immutable analysis, explicit edits and separate ownership."""
import copy
import importlib.util
from pathlib import Path
import unittest
from jsonschema import Draft202012Validator, ValidationError

spec = importlib.util.spec_from_file_location("preserved_analysis", Path(__file__).resolve().parents[1] / "scripts/compare_preserved_analysis.py")
experiment = importlib.util.module_from_spec(spec)
spec.loader.exec_module(experiment)


class PreservedAnalysisTests(unittest.TestCase):
    def fixture(self):
        return {"analysis_version": "predecision_analysis_v1", **{
            field: {"text": "첫 문장. 잘못된 표현. 중요한 비교는 유지한다.",
                    "card_keys": [f"financial.item_{n}" for n in range(25)]}
            for field in experiment.FIELDS}}

    def correction(self, **kwargs):
        return {"section": "earnings_review", "original_quote": "잘못된 표현.",
                "replacement_text": "수정된 표현.", "reason": "관측 범위로 수정", "card_keys": [], **kwargs}

    def test_empty_corrections_preserve_every_word_and_reference(self):
        analysis = self.fixture()
        effective = experiment.apply_explicit_corrections(analysis, [])
        self.assertEqual(effective, analysis)
        self.assertIsNot(effective, analysis)
        self.assertEqual(len(effective["outlook"]["card_keys"]), 25)

    def test_only_exact_model_edit_is_applied_original_unchanged(self):
        analysis = self.fixture()
        before = copy.deepcopy(analysis)
        effective = experiment.apply_explicit_corrections(analysis, [self.correction(card_keys=["news.item_1"])])
        self.assertEqual(analysis, before)
        self.assertEqual(effective["earnings_review"]["text"], "첫 문장. 수정된 표현. 중요한 비교는 유지한다.")
        self.assertEqual(effective["outlook"], analysis["outlook"])
        self.assertEqual(len(effective["earnings_review"]["card_keys"]), 26)

    def test_ambiguous_or_unknown_span_stops_without_fuzzy_rewrite(self):
        for quote in ("없는 문장", "문장", ""):
            analysis = self.fixture()
            if quote == "문장":
                analysis["earnings_review"]["text"] = "문장 문장"
            with self.assertRaises(ValueError):
                experiment.apply_explicit_corrections(analysis, [self.correction(original_quote=quote)])

    def test_overlapping_edits_rejected(self):
        with self.assertRaises(ValueError):
            experiment.apply_explicit_corrections(self.fixture(), [self.correction(), self.correction(original_quote="잘못된 표현")])

    def test_deletion_does_not_drop_other_analysis(self):
        effective = experiment.apply_explicit_corrections(self.fixture(), [self.correction(replacement_text="")])
        self.assertIn("중요한 비교는 유지한다.", effective["earnings_review"]["text"])

    def test_no_opinion_keyword_gate(self):
        analysis = self.fixture()
        analysis["outlook"]["text"] = "매수 의견이라는 표현이 있어도 규칙으로 지우지 않는다."
        self.assertEqual(experiment.apply_explicit_corrections(analysis, []), analysis)

    def test_decision_contract_does_not_regenerate_analysis(self):
        context = {"evidence_cards": {"financial.a": {"domain": "financial"}}, "coverage_dimensions": {}}
        fmt = experiment.decision_only_format(context)
        schema = fmt["json_schema"]["schema"]
        self.assertFalse(set(experiment.FIELDS[:3]) & set(schema["properties"]["strategy_brief"]["properties"]))
        self.assertIn("analysis_corrections", schema["required"])
        self.assertNotIn("maxItems", schema["properties"]["analysis_corrections"])
        correction = self.correction(card_keys=["invented"])
        with self.assertRaises(ValidationError):
            Draft202012Validator(schema["properties"]["analysis_corrections"]["items"]).validate(correction)

    def test_prompt_does_not_request_full_analysis_rewrite(self):
        prompt = experiment.decision_only_prompt()
        self.assertIn("분석 문단 전체를 다시 작성하지 않는다", prompt)
        self.assertNotIn("- earnings_review:", prompt)
        self.assertIn("특정 의견을 기본값으로 삼지 않는다", prompt)


if __name__ == "__main__":
    unittest.main()
