"""Reject incomplete or ambiguous condition-to-report joins before scoring."""
import copy
import importlib.util
from pathlib import Path
import unittest

path = Path(__file__).resolve().parents[1] / 'evaluate_report_bodies.py'
spec = importlib.util.spec_from_file_location('report_body_evaluation_test', path)
evaluation = importlib.util.module_from_spec(spec)
spec.loader.exec_module(evaluation)


class ReportIndexTests(unittest.TestCase):
    def setUp(self):
        self.original = {'state': 'success', 'completed': [
            {'company': name, 'condition': condition, 'report': f'/reports/{condition}/{name}.html'}
            for name in evaluation.COMPANIES for condition in evaluation.CONDITIONS if condition != 'one_team']}
        self.one_team = {'state': 'success', 'completed': [
            {'company': name, 'condition': 'one_team', 'report': f'/reports/one_team/{name}.html'}
            for name in evaluation.COMPANIES]}

    def test_all_35_reports_join_to_correct_companies_and_conditions(self):
        reports = evaluation.build_report_index(self.original, self.one_team)
        self.assertEqual(len(reports), 35)
        for name in evaluation.COMPANIES:
            self.assertEqual(reports[(name, 'one_team')], Path(f'/reports/one_team/{name}.html'))

    def test_missing_one_team_report_cannot_be_silently_skipped(self):
        self.one_team['completed'].pop()
        with self.assertRaisesRegex(ValueError, 'Missing'):
            evaluation.build_report_index(self.original, self.one_team)

    def test_duplicate_one_team_report_cannot_replace_another_report(self):
        self.one_team['completed'].append(copy.deepcopy(self.one_team['completed'][0]))
        with self.assertRaisesRegex(ValueError, 'duplicate'):
            evaluation.build_report_index(self.original, self.one_team)

    def test_failed_generation_cannot_be_scored_as_complete(self):
        self.one_team['state'] = 'failed'
        with self.assertRaisesRegex(ValueError, 'complete'):
            evaluation.build_report_index(self.original, self.one_team)


class GenerationModelNoteTests(unittest.TestCase):
    def test_shared_luna_summaries_do_not_make_54_analysis_a_model_mismatch(self):
        note = evaluation.generation_model_note({"integrated_analysis": "gpt-5.4",
            "downstream": "gpt-5.4", "reused_news_summary": "gpt-5.6-luna"})
        self.assertIn("모두 gpt-5.4", note)
        self.assertNotIn("모델 변경", note)

    def test_luna_analysis_does_not_get_labeled_as_matched_54(self):
        note = evaluation.generation_model_note({"integrated_analysis": "gpt-5.6-luna",
            "downstream": "gpt-5.6-luna", "reused_news_summary": "gpt-5.6-luna"})
        self.assertIn("모델 변경", note)
        self.assertNotIn("모두 gpt-5.4", note)


if __name__ == '__main__':
    unittest.main()
