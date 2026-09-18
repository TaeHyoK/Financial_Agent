"""Offline checks for separate summary and analysis models."""
import ast
import json
from pathlib import Path
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
ANALYSIS_MODEL = "gpt-5.4"
SUMMARY_MODEL = "gpt-5.6-luna"


class ModelDefaultsTests(unittest.TestCase):
    def test_analysis_component_defaults(self):
        for path, variable in (
            ("src/Agent_Team/Financial_Agent/financial_analysis_agent.py", "DEFAULT_OPENAI_MODEL"),
            ("src/Agent_Team/YFinance_Agent/reporting.py", "DEFAULT_OPENAI_MODEL"),
            ("src/Agent_Team/News_Agent/analysis_agent.py", "DEFAULT_MODEL"),
            ("src/Agent_Team/Competitor_Agent/comparison_agent.py", "DEFAULT_MODEL"),
            ("src/Agent_Team/Strategy_Agent/agent.py", "DEFAULT_OPENAI_MODEL"),
            ("src/Agent_Team/Writer Agent/html_report_writer.py", "DEFAULT_LLM_MODEL"),
        ):
            tree = ast.parse((ROOT / path).read_text())
            values = [ast.literal_eval(node.value) for node in tree.body if isinstance(node, ast.Assign)
                      and any(isinstance(t, ast.Name) and t.id == variable for t in node.targets)]
            with self.subTest(path=path):
                self.assertEqual(values, [ANALYSIS_MODEL])

    def test_cli_defaults_and_independent_overrides(self):
        from orchestration.full_report_pipeline import build_parser
        from orchestration.end_to_end_loop import build_parser as domain_parser
        from Agent_Team.News_Agent.cli import build_parser as news_parser
        from Agent_Team.News_Agent.context_export import build_parser as export_parser
        parser = build_parser()
        self.assertEqual(parser.get_default("llm_model"), ANALYSIS_MODEL)
        self.assertEqual(parser.get_default("news_summary_model"), SUMMARY_MODEL)
        self.assertEqual(domain_parser().get_default("news_llm_model"), SUMMARY_MODEL)
        self.assertEqual(news_parser().get_default("llm_model"), SUMMARY_MODEL)
        self.assertEqual(export_parser().get_default("llm_model"), SUMMARY_MODEL)
        args = parser.parse_args(["--company-name", "검증기업", "--selected-date", "20251031",
                                 "--llm-model", "gpt-5.4-mini", "--news-summary-model", ANALYSIS_MODEL])
        self.assertEqual(args.llm_model, "gpt-5.4-mini")
        self.assertEqual(args.news_summary_model, ANALYSIS_MODEL)

    def test_company_config_default(self):
        self.assertEqual(json.loads((ROOT / "configs/company_input.json").read_text())["llm_model"], ANALYSIS_MODEL)

    def test_target_peer_and_ablations_share_model_routing(self):
        from orchestration.ablation import config_from_args
        from orchestration.full_report_pipeline import FullPipelinePaths, build_domain_pipeline_command, build_parser, _base_manifest
        from orchestration.end_to_end_loop import build_parser as domain_parser
        with tempfile.TemporaryDirectory() as temporary:
            for flags in ([], ["--primary-data-only"], ["--no-competitor"]):
                args = build_parser().parse_args(["--company-name", "검증기업", "--selected-date", "20251031", *flags])
                ablation = config_from_args(args)
                paths = FullPipelinePaths(Path(temporary), "검증기업_20251031", "검증기업", "20251031", "offline")
                for role in ("target", "peer"):
                    command = build_domain_pipeline_command(config_path=paths.target_config, paths=paths,
                        run_id="offline", run_role=role, args=args, ablation=ablation, env_file=Path(temporary) / ".env")
                    routed = domain_parser().parse_args(command[3:])
                    self.assertEqual(routed.llm_model, ANALYSIS_MODEL)
                    self.assertEqual(routed.news_analysis_model, ANALYSIS_MODEL)
                    self.assertEqual(routed.news_llm_model, SUMMARY_MODEL)
                    self.assertTrue(routed.news_split_by_period)
                    self.assertEqual(routed.primary_data_only, ablation.primary_data_only)
                manifest = _base_manifest(args=args, ablation=ablation, paths=paths,
                    selected_date="20251031", status="dry_run", steps=[])
                self.assertEqual(manifest["request"]["llm_model"], ANALYSIS_MODEL)
                self.assertEqual(manifest["request"]["news_summary_model"], SUMMARY_MODEL)

    def test_normalization_preserves_cache_write_tokens(self):
        from shared.llm_clients import normalize_usage
        for usage in (
            {"prompt_tokens": 1000, "completion_tokens": 100,
             "prompt_tokens_details": {"cached_tokens": 200, "cache_write_tokens": 300}},
            {"input_tokens": 1000, "output_tokens": 100,
             "input_tokens_details": {"cached_tokens": 200, "cache_write_tokens": 300}},
        ):
            normalized = normalize_usage(usage)
            self.assertEqual(normalized["cache_write_input_tokens"], 300)
            self.assertEqual(normalized["cached_input_tokens"], 200)
            self.assertEqual(normalized["total_tokens"], 1100)

    def test_twelve_monthly_requests_keep_luna_and_each_months_articles(self):
        from Agent_Team.News_Agent.context_export import _build_llm_summary_request, _build_period_llm_requests, _load_llm_user_payload
        periods = [{"period": f"2025-{month:02}", "events": [{"event_id": f"E{month}",
                    "title": f"검증기업 사건 {month}", "snippet": "제공된 기사 발췌문이다."}]}
                   for month in range(1, 13)]
        request = _build_llm_summary_request({"metadata": {}, "periods": periods}, SUMMARY_MODEL)
        split = _build_period_llm_requests(request)
        self.assertEqual(len(split), 12)
        for (period, part), original in zip(split, periods):
            self.assertEqual(part["model"], SUMMARY_MODEL)
            self.assertNotIn("temperature", part)
            self.assertEqual(period, original["period"])
            self.assertEqual(_load_llm_user_payload(part)["periods"], [original])

    def test_mixed_usage_pricing_and_legacy_mini(self):
        from orchestration.usage_summary import estimate_api_cost
        result = estimate_api_cost([
            {"model": SUMMARY_MODEL, "usage": {"input_tokens": 1_000_000,
             "cached_input_tokens": 100_000, "cache_write_input_tokens": 200_000, "output_tokens": 100_000}},
            {"model": ANALYSIS_MODEL, "usage": {"input_tokens": 100_000, "output_tokens": 10_000}},
        ])
        self.assertEqual(result["status"], "available")
        # Luna exceeds 272K: input/cache 2x and output 1.5x; GPT-5.4 is below the threshold.
        self.assertAlmostEqual(result["by_model"][SUMMARY_MODEL]["total_cost_usd"], 0.564)
        self.assertAlmostEqual(result["total_cost_usd"], 0.964)
        self.assertEqual(result["by_model"][SUMMARY_MODEL]["cache_write_input_tokens"], 200_000)
        mini = estimate_api_cost([{"model": "gpt-5.4-mini", "usage": {"input_tokens": 1_000_000,
            "cached_input_tokens": 100_000, "output_tokens": 100_000}}])
        self.assertAlmostEqual(mini["total_cost_usd"], 1.1325)


if __name__ == "__main__":
    unittest.main()
