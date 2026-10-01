"""The frozen-input runner must not overwrite completed or mismatched runs."""

import argparse
import json
from pathlib import Path
import tempfile
import unittest

from scripts.evaluate_strategy_decision_style import run


class StrategyDecisionStyleRunnerTests(unittest.TestCase):
    def test_retry_refuses_completed_or_different_source(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "bundle.json"
            source.write_text(json.dumps({"target_company": {"company_name": "검증기업"}}), encoding="utf-8")
            output = root / "output"
            output.mkdir()
            status = {"company": "검증기업", "decision_style": "standard",
                      "news_date_mode": "split", "source_sha256": "wrong", "status": "failed"}
            (output / "status.json").write_text(json.dumps(status), encoding="utf-8")
            args = argparse.Namespace(input_bundle=str(source), output_dir=str(output),
                                      decision_style="standard", news_date_mode="split",
                                      retry_incomplete=True)
            with self.assertRaisesRegex(ValueError, "source hash"):
                run(args)
            status["status"] = "completed"
            (output / "status.json").write_text(json.dumps(status), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "failed or interrupted"):
                run(args)


if __name__ == "__main__":
    unittest.main()
