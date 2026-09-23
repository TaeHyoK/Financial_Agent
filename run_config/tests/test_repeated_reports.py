"""Offline checks: never call an LLM while testing repeat orchestration."""
import copy
from pathlib import Path

import pytest

import run_repeated_reports as runner


pytestmark = pytest.mark.skipif(
    not (runner.original.PREPARED / "preparation_manifest.json").is_file(),
    reason="historical frozen generation inputs are not part of the Git handoff",
)


@pytest.fixture
def batch(tmp_path, monkeypatch):
    monkeypatch.setattr(runner, "BATCH", tmp_path / "repeats")
    monkeypatch.setattr(runner, "MANIFEST", runner.BATCH / "preparation_manifest.json")
    monkeypatch.setattr(runner, "STATUS", runner.BATCH / "status.json")
    monkeypatch.setattr(runner, "USAGE", runner.BATCH / "llm_usage.jsonl")
    return runner.BATCH


def get_spec(condition, company="현대건설", replicate=2):
    original = runner.read(runner.original.PREPARED / "preparation_manifest.json")
    spec = copy.deepcopy(next(s for s in original["conditions"]
                             if s["condition"] == condition and s["target_company"] == company))
    spec.update(replicate=replicate,
                report_output_root=str(runner.BATCH / f"replicate_{replicate:02d}" / condition))
    for entity in spec["entities"]:
        source = runner.WORKSPACE / "reports" / condition / "replicate_01" / company
        if entity["role"] == "peer":
            source = source / "비교기업" / entity["company_name"]
        entity["source_root"] = str(source)
    return spec


def test_prepare_preserves_models_sources_and_separates_fifty_reports(batch):
    manifest = runner.prepare()
    assert len(manifest["jobs"]) == 50
    assert manifest["new_summary_calls"] == 0
    assert manifest["planned_analysis_calls"] + manifest["planned_final_calls"] == 340
    paths = [runner.make_paths(s).published_report for s in manifest["jobs"]]
    assert len(set(paths)) == 50
    assert all(p.is_relative_to(batch) for p in paths)
    assert {s["target_company"] for s in manifest["jobs"]} == set(runner.COMPANIES)
    assert runner.check() == manifest or runner.check()["reports_planned"] == 50
    assert not runner.USAGE.exists()


@pytest.mark.parametrize("condition", ["full", "random_news", "no_subdata"])
@pytest.mark.parametrize("role", ["target", "peer"])
def test_relocated_inputs_do_not_copy_old_analysis(batch, condition, role):
    spec = get_spec(condition)
    entity = next(e for e in spec["entities"] if e["role"] == role)
    paths = runner.make_paths(spec)
    root = runner.materialize(spec, entity, paths)
    day = entity["selected_date"]
    source = Path(entity["source_root"])
    for domain in ("Financial", "News", "Y_Finance"):
        assert not (root / domain / day / "final_report.json").exists()
    assert not (root / "News" / day / "output/news_agent_handoff.json").exists()
    for relative in (f"Financial/{day}/dart_main.json", f"Y_Finance/{day}/market_full_dataset.json",
                     f"News/{day}/output/news_agent_llm_request.json",
                     f"News/{day}/context_exports/month/selected_articles.json"):
        assert runner.sha(root / relative) == runner.sha(source / relative)
    monthly = Path("News") / day / "context_exports/month/llm_period_summaries.json"
    if (source / monthly).exists():
        assert runner.sha(root / monthly) == runner.sha(source / monthly)


@pytest.mark.parametrize("condition", ["full", "random_news", "no_subdata"])
def test_financial_request_unchanged(batch, condition, monkeypatch):
    spec = get_spec(condition)
    entity = spec["entities"][0]
    root = runner.materialize(spec, entity, runner.make_paths(spec))
    captured = {}
    class Captured(Exception):
        pass
    def stop(factual, **kwargs):
        captured.update(runner.original.build_financial_request(factual, model="gpt-5.4"))
        raise Captured()
    monkeypatch.setattr(runner.original, "generate_financial_analysis_with_llm", stop)
    with pytest.raises(Captured):
        runner.original.generate_domain(root, entity, condition, "financial")
    prior = runner.read(Path(entity["source_root"]) / "Financial" / entity["selected_date"] / "actual_llm_request.json")
    assert captured == prior


@pytest.mark.parametrize("condition", ["full", "random_news", "no_subdata"])
def test_market_request_unchanged(batch, condition, monkeypatch):
    spec = get_spec(condition)
    entity = spec["entities"][0]
    root = runner.materialize(spec, entity, runner.make_paths(spec))
    requests = []
    class Captured(Exception):
        pass
    def stop(request, **kwargs):
        requests.append(request)
        raise Captured()
    monkeypatch.setattr(runner.original.reporting, "call_domain_response", stop)
    for directory in (Path(entity["source_root"]), root):
        with pytest.raises(Captured):
            runner.original.generate_domain(directory, entity, condition, "market")
    assert requests[0] == requests[1]


def test_no_peer_reuses_only_same_new_replicate(batch):
    spec = get_spec("no_peer")
    paths = runner.make_paths(spec)
    entity = spec["entities"][0]
    with pytest.raises(RuntimeError, match="same-replicate"):
        runner.materialize(spec, entity, paths)
    full = get_spec("full")
    root = runner.materialize(full, full["entities"][0], runner.make_paths(full))
    day = entity["selected_date"]
    for domain in ("Financial", "Y_Finance", "News"):
        runner.save(root / domain / day / "final_report.json", {"new_replicate": 2, "domain": domain})
    reused = runner.materialize(spec, entity, paths)
    for domain in ("Financial", "Y_Finance", "News"):
        assert runner.read(reused / domain / day / "final_report.json")["new_replicate"] == 2
    other = get_spec("no_peer", replicate=3)
    with pytest.raises(RuntimeError, match="same-replicate"):
        runner.materialize(other, other["entities"][0], runner.make_paths(other))


def test_completed_run_resume_makes_no_calls(batch, monkeypatch):
    runner.prepare()
    runner.save(runner.STATUS, {"state": "success"})
    monkeypatch.setattr(runner.original, "generate_domain", lambda *a, **k: pytest.fail("Paid regeneration"))
    runner.execute_run(resume=True)
    assert not runner.USAGE.exists()


def test_unified_reuses_exact_gpt54_request(batch):
    manifest = runner.prepare()
    for s in manifest["jobs"]:
        if s["condition"] != "one_team":
            continue
        for entity in s["entities"]:
            request = runner.read(entity["request_path"])
            packet = runner.read(entity["prepared_packet"])
            assert request == runner.unified.build_request(packet["semantic_input"], model="gpt-5.4")
            assert request["reasoning"]["effort"] == "none"
            assert request.get("service_tier") != "flex"
