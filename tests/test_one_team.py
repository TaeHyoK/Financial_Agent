"""One-team preserves inputs, emits one report and supports native consumers."""
import copy
import json
import importlib.util
import os
from pathlib import Path
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import unittest

from Agent_Team.Unified_Agent import report as integrated_report


def output():
    view = {"statement": "실적과 뉴스 및 주가 자료를 함께 판단한다.", "evidence_ids": ["E001", "NEWS_RAW_1", "YF_CLOSE"]}
    return {"title": "검증기업 종합 분석", "executive_summary": copy.deepcopy(view),
        "integrated_analysis": copy.deepcopy(view), "findings": [{"title": "실적과 사건 및 주가의 관계",
        "domains": ["financial", "news", "market"], "observation": "자료별 기간을 구분하여 관측한다.",
        "interpretation": "동반 관측의 인과관계는 확인되지 않는다.", "investment_implication": "자료를 종합하여 판단한다.",
        "evidence_ids": view["evidence_ids"]}], "risks": [],
        "outlook": {key: copy.deepcopy(view) for key in ("short_term", "medium_term", "long_term")},
        "limitations": ["자료의 시점과 범위를 확인한다."]}


class IntegratedReportTest(unittest.TestCase):
    def setUp(self):
        self.context = {"boundary_requests": {}}
        for domain, key in (("financial", "E001"), ("news", "NEWS_RAW_1"), ("market", "YF_CLOSE")):
            self.context[domain] = {"evidence": {key: {"label": domain, "value": 1}}}
            self.context["boundary_requests"][domain] = {"model": "gpt-5.4", "input": [
                {"role": "system", "content": f"{domain} 원본 분석 지침"},
                {"role": "user", "content": json.dumps({"original_input": domain})}]}
        self.context["news"]["evidence"]["NEWS_PERIOD_1"] = {"label": "summary"}
        self.prepared = {"semantic_input": self.context, "adapter_facts": {
            "financial_factual_report": {"target_company": "검증기업", "detailed_analysis": {}},
            "market_facts": {"valuation_snapshot": {}, "primary_evidence_catalog": self.context["market"]["evidence"]}}}

    def test_single_report_preserves_all_native_inputs_and_instructions(self):
        request = integrated_report.build_request(self.context, model="gpt-5.4")
        schema = request["text"]["format"]["schema"]
        self.assertFalse({"financial", "news", "market"} & set(schema["properties"]))
        inputs = json.loads(request["input"][1]["content"])
        for domain, native in self.context["boundary_requests"].items():
            self.assertEqual(inputs[domain], json.loads(native["input"][1]["content"]))
            self.assertIn(native["input"][0]["content"], request["input"][0]["content"])
        self.assertNotIn("NEWS_PERIOD_1", integrated_report.evidence_catalog(self.context))
        integrated_report.validate_output(output(), self.context)

    def test_luna_uses_no_reasoning(self):
        for native in self.context["boundary_requests"].values(): native["model"] = "gpt-5.6-luna"
        request = integrated_report.build_request(self.context, model="gpt-5.6-luna")
        self.assertEqual(request["reasoning"], {"effort": "none"})

    def test_rejects_unknown_sources_and_old_split_schema(self):
        wrong = output(); wrong["findings"][0]["evidence_ids"] = ["MISSING"]
        with self.assertRaises(Exception): integrated_report.validate_output(wrong, self.context)
        with self.assertRaises(Exception):
            integrated_report.validate_output({"financial": {}, "news": {}, "market": {}}, self.context)

    def test_declared_domains_require_matching_sources(self):
        wrong = output(); wrong["findings"][0]["evidence_ids"] = ["E001"]
        with self.assertRaisesRegex(ValueError, "domains"):
            integrated_report.validate_output(wrong, self.context)

    def test_evidence_ids_do_not_leak_into_prose(self):
        wrong = output(); wrong["integrated_analysis"]["statement"] = "E001을 근거로 판단한다."
        with self.assertRaises(ValueError): integrated_report.validate_output(wrong, self.context)

    def test_domain_normalization_keeps_original_prose_and_citations(self):
        original = output()
        original["findings"][0]["domains"] = ["financial"]
        before = copy.deepcopy(original)
        normalized, changes = integrated_report.normalize_source_domains(original, self.context)
        self.assertEqual(original, before)
        self.assertEqual(normalized["findings"][0]["domains"], ["financial", "news", "market"])
        normalized["findings"][0]["domains"] = before["findings"][0]["domains"]
        self.assertEqual(normalized, before)
        self.assertEqual(len(changes), 1)

    def test_domain_normalization_does_not_accept_unknown_sources(self):
        wrong = output(); wrong["findings"][0]["evidence_ids"] = ["MISSING"]
        with self.assertRaises(Exception): integrated_report.normalize_source_domains(wrong, self.context)

    def test_only_one_report_is_written(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            outputs = integrated_report.write_report(output=output(), prepared=self.prepared,
                destination_paths=SimpleNamespace(run_dir=root / "runs/20251106"),
                run_config=SimpleNamespace(company_name="검증기업", ticker="000000.KS", selected_date_iso="2025-11-06"),
                model="gpt-5.4", role="target")
            self.assertEqual(set(outputs), {"unified_report", "unified_report_md"})
            self.assertEqual(len(list(root.rglob("unified_report.json"))), 1)
            self.assertEqual(list(root.rglob("final_report.json")), [])
            self.assertEqual(json.loads(Path(outputs["unified_report"]).read_text())["report"], output())

    def test_downstream_subprocess_reads_one_interpretation_and_citations(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            company = root / "검증기업"
            paths = SimpleNamespace(run_dir=company / "runs/20251106",
                financial_final_report=company / "Financial/20251106/final_report.json",
                news_final_report=company / "News/20251106/final_report.json",
                yfinance_final_report=company / "Y_Finance/20251106/final_report.json")
            config = SimpleNamespace(company_name="검증기업", ticker="000000.KS", selected_date_iso="2025-11-06")
            integrated_report.write_report(output=output(), prepared=self.prepared,
                destination_paths=paths, run_config=config, model="gpt-5.4", role="target")
            script = '''
import json, sys
from pathlib import Path
from Agent_Team.Strategy_Agent.agent import build_strategy_input_bundle, validate_input_bundle
from Agent_Team.Strategy_Agent.packet import build_compact_strategy_packet
from Agent_Team.Strategy_Agent.decision import build_strategy_context_package
from Agent_Team.Competitor_Agent.comparison_agent import _resolved_file, _load_json, build_comparison_context
from Agent_Team.Competitor_Agent.peer_comparison import _load_json as load_metrics
financial, news, market = map(Path, sys.argv[1:])
bundle = build_strategy_input_bundle(target_company_name='검증기업', target_run_key='검증기업_20251106',
    target_financial_path=financial, target_news_path=news, target_yfinance_path=market)
validate_input_bundle(bundle)
packet, provenance, _, _ = build_compact_strategy_packet(bundle, model='gpt-5.4')
context = build_strategy_context_package(packet, input_bundle=bundle)
assert set(context['domain_handoffs']) == {'integrated'}, context['domain_handoffs']
assert context['domain_handoffs']['integrated']['integrated_analysis']['statement']
assert 'integrated.finding_1' in context['evidence_cards']
assert context['evidence_cards']['integrated.finding_1']['evidence_origin'] == 'model_interpreted'
assert provenance['cards']['integrated.finding_1']['source_evidence_ids'] == ['E001', 'NEWS_RAW_1', 'YF_CLOSE']
assert len(context['evidence_cards']['integrated.finding_1']['primary_observation']['cited_sources']) == 3
reports = [_load_json(_resolved_file(path, 'report')) for path in (financial, news, market)]
assert load_metrics(financial)['target_company'] == '검증기업'
comparison = build_comparison_context(target_company_name='검증기업', peer_company_name='비교기업',
    target_financial=reports[0], target_news=reports[1], target_yfinance=reports[2],
    peer_financial=reports[0], peer_news=reports[1], peer_yfinance=reports[2], pairwise_dataset={'metrics': []})
assert set(comparison['basis_cards']) == {'target.integrated.analysis', 'peer.integrated.analysis',
    'pair.financial.metrics', 'pair.market.metrics', 'pair.valuation.metrics'}, comparison['basis_cards']
print('single report reached comparison and Strategy')
'''
            runtime = Path(integrated_report.__file__).parent / "runtime"
            src = runtime.parents[2]
            env = {**os.environ, "ONE_TEAM_RUNTIME": "1", "ONE_TEAM_SINGLE_REPORT": "1",
                "PYTHONPATH": os.pathsep.join([str(runtime), str(src)])}
            result = subprocess.run([sys.executable, "-c", script, str(paths.financial_final_report),
                str(paths.news_final_report), str(paths.yfinance_final_report)], env=env, text=True,
                capture_output=True, timeout=45)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)


@unittest.skipUnless(
    (Path(__file__).resolve().parents[1] / "run_config/run_one_team_reports.py").is_file(),
    "Server experiment runner is outside this repository; native one-team tests still run.",
)
class OneTeamRunnerTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        path = Path(__file__).resolve().parents[1] / "run_config/run_one_team_reports.py"
        spec = importlib.util.spec_from_file_location("one_team_runner_test", path)
        cls.runner = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = cls.runner
        spec.loader.exec_module(cls.runner)

    def test_model_selection_applies_to_downstream_by_default(self):
        from unittest.mock import patch
        with patch.object(self.runner, "prepare") as prepare:
            self.runner.main(["prepare"])
        args = prepare.call_args.args[0]
        self.assertEqual(args.model, "gpt-5.6-luna")
        self.assertEqual(args.downstream_model, args.model)
        with patch.object(self.runner, "prepare") as prepare:
            self.runner.main(["prepare", "--model", "gpt-5.4"])
        self.assertEqual(prepare.call_args.args[0].downstream_model, "gpt-5.4")

    def test_execution_reaches_first_call_with_entity_config_without_ticker_field(self):
        from unittest.mock import patch
        class StopBeforeAPI(Exception): pass
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary)
            config=root / "company.json"
            config.write_text(json.dumps({"company_name":"검증기업","corp_code":"00000000",
                "stock_code":"000000","ticker":"000000.KS"}))
            request=root / "request.json"; request.write_text("{}")
            packet=root / "packet.json"; packet.write_text("{}")
            entity={"role":"target","company_name":"검증기업","company_config":str(config),
                "request_path":str(request),"prepared_packet":str(packet)}
            spec={"target_company":"검증기업","selected_date":"20251106", "entities":[entity],
                "report_output_root":str(root / "reports")}
            manifest={"run_id":"test_run","models":{"integrated_analysis":"gpt-5.6-luna"},
                "reports_planned":1,"environment_file":str(root / "absent.env"),"conditions":[spec]}
            args=SimpleNamespace(run_id="test_run",model="gpt-5.6-luna",downstream_model="gpt-5.6-luna",replicate=1)
            with patch.object(self.runner,"WORKSPACE",root), patch.object(self.runner,"check",return_value=manifest), \
                 patch.object(self.runner,"load_project_env"), patch.dict(os.environ,{"OPENAI_API_KEY":"offline-test"}), \
                 patch.object(self.runner,"stage_commands",return_value=(None,None,None,None,[])), \
                 patch.object(self.runner.flow,"_write_resolved_inputs"), \
                 patch.object(self.runner,"materialize",return_value=root / "reports/검증기업"), \
                 patch.object(self.runner,"call_domain_response",side_effect=StopBeforeAPI) as call:
                with self.assertRaises(StopBeforeAPI): self.runner.run(args)
            call.assert_called_once()
            status=json.loads((root / "status/one_team/test_run.json").read_text())
            self.assertEqual(status["current_stage"],"target_unified")
            self.assertEqual(status["state"],"failed")

    def test_materialize_preserves_sources_without_prior_analyses_or_peer_leaks(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "full"
            for name in ("Financial/20251106/dart_main.json", "Financial/20251106/final_report.json",
                "Financial/20251106/actual_llm_request.json", "News/20251106/output/news_agent_handoff.json",
                "News/20251106/context_exports/month/selected_articles.json",
                "Y_Finance/20251106/market_full_dataset.csv", "Y_Finance/20251106/final_report.md",
                "비교기업/peer/Financial/20251106/dart_main.json", "Financial/20251105/dart_main.json"):
                p = source / name; p.parent.mkdir(parents=True, exist_ok=True); p.write_text("source")
            entity = {"source_root": str(source), "company_name": "검증기업", "selected_date": "20251106", "role": "target"}
            destination = self.runner.materialize(entity,
                SimpleNamespace(output_root=root / "one_team", peer_output_root=root / "one_team/비교기업"))
            actual = {str(p.relative_to(destination)) for p in destination.rglob("*") if p.is_file()}
            self.assertEqual(actual, {"Financial/20251106/dart_main.json",
                "News/20251106/context_exports/month/selected_articles.json", "Y_Finance/20251106/market_full_dataset.csv"})


if __name__ == '__main__': unittest.main()
