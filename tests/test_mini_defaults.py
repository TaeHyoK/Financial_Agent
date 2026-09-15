"""Offline regression checks for cost-saving defaults; historical fixtures stay intact."""
import ast
import json
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
MINI = "gpt-5.4-mini"


class MiniDefaultsTests(unittest.TestCase):
    def test_component_and_experiment_defaults(self):
        for path, variable in (
            ("src/Agent_Team/Financial_Agent/financial_analysis_agent.py", "DEFAULT_OPENAI_MODEL"),
            ("src/Agent_Team/YFinance_Agent/reporting.py", "DEFAULT_OPENAI_MODEL"),
            ("src/Agent_Team/News_Agent/analysis_agent.py", "DEFAULT_MODEL"),
            ("src/Agent_Team/Competitor_Agent/comparison_agent.py", "DEFAULT_MODEL"),
            ("src/Agent_Team/Strategy_Agent/agent.py", "DEFAULT_OPENAI_MODEL"),
            ("src/Agent_Team/Writer Agent/html_report_writer.py", "DEFAULT_LLM_MODEL"),
            ("scripts/compare_strategy_stages.py", "MODEL"),
        ):
            tree = ast.parse((ROOT / path).read_text())
            values = [ast.literal_eval(node.value) for node in tree.body if isinstance(node, ast.Assign)
                      and any(isinstance(t, ast.Name) and t.id == variable for t in node.targets)]
            with self.subTest(path=path):
                self.assertEqual(values, [MINI])

    def test_full_cli_default_and_explicit_override(self):
        from orchestration.full_report_pipeline import build_parser
        parser = build_parser()
        self.assertEqual(parser.get_default("llm_model"), MINI)
        # The parser keeps explicit model selection available for reproducibility.
        action = next(a for a in parser._actions if a.dest == "llm_model")
        self.assertIsNone(action.choices)

    def test_company_config_default(self):
        self.assertEqual(json.loads((ROOT / "configs/company_input.json").read_text())["llm_model"], MINI)

    def test_active_generation_paths_have_no_full_model_literal(self):
        for path in ("scripts/check_optional_limits.py", "scripts/check_subdata_guidance.py",
                     "src/orchestration/end_to_end_loop.py", "src/orchestration/company_resolver.py",
                     "src/Agent_Team/News_Agent/context_export.py", "src/Agent_Team/News_Agent/cli.py"):
            tree = ast.parse((ROOT / path).read_text())
            literals = [n.value for n in ast.walk(tree) if isinstance(n, ast.Constant) and isinstance(n.value, str)]
            with self.subTest(path=path):
                self.assertNotIn("gpt-5.4", literals)
                self.assertIn(MINI, literals)

    def test_mini_usage_pricing_keeps_cached_input_separate(self):
        from orchestration.usage_summary import estimate_api_cost
        result = estimate_api_cost([{"model": MINI, "usage": {"input_tokens": 1_000_000,
            "cached_input_tokens": 100_000, "output_tokens": 100_000}}])
        self.assertEqual(result["status"], "available")
        self.assertAlmostEqual(result["total_cost_usd"], 1.1325)


if __name__ == "__main__":
    unittest.main()
