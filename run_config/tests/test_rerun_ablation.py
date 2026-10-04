"""Offline checks for the ablation re-run tool: replay layer, keying and resume. No model calls."""
import json
from pathlib import Path

import pytest

import rerun_ablation as tool


REQUEST = {"model": "gpt-5.4", "messages": [{"role": "user", "content": "hello"}],
           "response_format": {"type": "json_object"}}
STEP = "competitor:comparison_analysis"
CONTEXT = tool.CallContext("r01", "full", "현대건설", "final", "현대건설")


def store_with(entry_context=CONTEXT, step=STEP, request=REQUEST, content='{"ok": true}'):
    store = tool.ReplayStore()
    key = (entry_context.replicate, entry_context.condition, entry_context.target_company, entry_context.role, step)
    store.add(key, tool.ReplayEntry(step, tool.request_sha256(request), "inline", "inline-test",
                                    {"prompt_tokens": 3}, {"content": content}))
    return store


def never_called(*args, **kwargs):
    raise AssertionError("transport must not be reached")


@pytest.fixture
def installed():
    yield
    tool.uninstall_replay()


def test_replay_hit_returns_original_response_without_transport(tmp_path, installed):
    layer = tool.ReplayLayer(store_with(), mode="real", call_log=tmp_path / "calls.jsonl")
    with layer.scope(CONTEXT):
        response = layer.execute(never_called, never_called, request_payload=REQUEST, model="gpt-5.4", step=STEP)
    assert json.loads(response.choices[0].message.content) == {"ok": True}
    assert response.usage.model_dump() == {"prompt_tokens": 3}
    row = json.loads((tmp_path / "calls.jsonl").read_text())
    assert row["replayed"] is True and row["model"] == "gpt-5.4"
    assert row["request_sha256"] == tool.request_sha256(REQUEST)
    assert (row["replicate"], row["condition"], row["role"]) == ("r01", "full", "final")


def test_replay_miss_goes_to_transport(tmp_path, installed):
    calls = []

    def original(call, *, request_payload, model, step, **kwargs):
        calls.append((step, model, kwargs))
        return call()

    other = dict(REQUEST, messages=[{"role": "user", "content": "changed"}])
    layer = tool.ReplayLayer(store_with(), mode="real", call_log=tmp_path / "calls.jsonl")
    with layer.scope(CONTEXT):
        result = layer.execute(original, lambda: "sent", request_payload=other, model="gpt-5.4", step=STEP,
                               max_attempts=1)
    assert result == "sent"
    assert calls == [(STEP, "gpt-5.4", {"max_attempts": 1})]
    row = json.loads((tmp_path / "calls.jsonl").read_text())
    assert row["replayed"] is False and row["outcome"] == "sent"


@pytest.mark.parametrize("context", [
    tool.CallContext("r02", "full", "현대건설", "final", "현대건설"),   # other replicate
    tool.CallContext("r01", "random_news", "현대건설", "final", "현대건설"),  # other condition
    tool.CallContext("r01", "full", "두산", "final", "두산"),   # other company
    tool.CallContext("r01", "full", "현대건설", "peer", "GS건설"),   # other entity
])
def test_same_hash_under_another_key_is_not_replayed(context, installed):
    layer = tool.ReplayLayer(store_with(), mode="plan")
    with layer.scope(context), pytest.raises(tool.PlannedNewCall):
        layer.execute(never_called, never_called, request_payload=REQUEST, model="gpt-5.4", step=STEP)
    with layer.scope(CONTEXT), pytest.raises(tool.PlannedNewCall):
        layer.execute(never_called, never_called, request_payload=REQUEST, model="gpt-5.4", step="writer:html_report")


def test_module_hook_routes_existing_and_reimported_call_sites(installed):
    import shared.llm_clients as llm_clients
    original = llm_clients.execute_with_telemetry
    layer = tool.ReplayLayer(store_with(), mode="plan")
    tool.install_replay(layer)
    assert llm_clients.execute_with_telemetry is not original
    with layer.scope(CONTEXT):
        response = llm_clients.execute_with_telemetry(never_called, request_payload=REQUEST, model="gpt-5.4", step=STEP)
        assert json.loads(response.choices[0].message.content) == {"ok": True}
    tool.uninstall_replay()
    assert llm_clients.execute_with_telemetry is original


def test_rebuilt_context_issues_follow_schema_refs():
    schema = {"$defs": {"issue": {"type": "object", "properties": {"context_id": {}, "source_domain": {}, "statement": {}}}},
              "properties": {"by_domain": {"type": "object", "properties": {
                  "market": {"type": "array", "items": {"$ref": "#/$defs/issue"}},
                  "news": {"type": "array", "items": {"$ref": "#/$defs/issue"}}}}}}
    saved = [{"statement": "s", "source_domain": "news", "context_id": "C1"}]
    grouped = tool.group_context_issues(saved, schema["properties"]["by_domain"], schema)
    assert grouped == {"market": [], "news": [{"context_id": "C1", "source_domain": "news", "statement": "s"}]}
    assert list(grouped["news"][0]) == ["context_id", "source_domain", "statement"]


def make_context(tmp_path):
    entity = tool.Entity("target", "현대건설", "현대건설", "000720.KS", "1", "000720", "20251020",
                         tmp_path / "config.json", tmp_path / "collected", tmp_path / "news.json")
    peer = tool.Entity("peer", "GS건설", "현대건설", "006360.KS", "2", "006360", "20251020",
                       tmp_path / "peer.json", tmp_path / "collected_peer", tmp_path / "peer_news.json")
    return tool.RunContext(source=None, layout=tool.Layout(tmp_path / "out"), share_info="recompute", mode="real",
                           env_file=tmp_path / "empty.env", companies={"현대건설": tool.Company("현대건설", "20251020", [entity, peer])})


def test_resume_skips_completed_report(tmp_path, monkeypatch):
    ctx = make_context(tmp_path)
    job = tool.Job("r01", "full", "현대건설")
    report = tmp_path / "report.html"
    report.write_text("<html></html>", encoding="utf-8")
    tool.save(ctx.layout.status_file(job), {"key": job.key, "state": "success", "stages": {}, "report": str(report)})
    monkeypatch.setattr(tool, "prepare_company", never_called)
    monkeypatch.setattr(tool, "set_module_mode", never_called)
    run = tool.ReportRun(ctx, job)
    run.execute()
    assert tool.read(ctx.layout.status_file(job))["state"] == "success"


def test_resume_skips_completed_stage_and_reruns_unfinished(tmp_path):
    ctx = make_context(tmp_path)
    job = tool.Job("r02", "full", "현대건설")
    tool.save(ctx.layout.status_file(job), {"key": job.key, "state": "failed", "stages": {
        "target_news": {"outcome": "done"}, "target_financial": {"outcome": "planned_new"}}})
    run = tool.ReportRun(ctx, job)
    run.paths = type("Paths", (), {"execution_dir": tmp_path / "execution"})()
    ran = []
    run.stage("target_news", lambda: ran.append("target_news"))
    run.stage("target_financial", lambda: ran.append("target_financial"))
    assert ran == ["target_financial"]
    assert tool.read(ctx.layout.status_file(job))["stages"]["target_financial"]["outcome"] == "done"


def test_dependencies_follow_original_order():
    assert tool.Job("r01", "full", "두산").level < tool.Job("r01", "no_peer", "두산").level
    assert tool.Job("r02", "full", "두산").level < tool.Job("r02", "no_peer", "두산").level
    (dependency, stages), = tool.Job("r03", "one_team", "두산").dependencies()
    assert dependency == tool.Job("r01", "full", "두산") and "peer_monthly_summaries" in stages
    (dependency, _), = tool.Job("r02", "no_peer", "두산").dependencies()
    assert dependency == tool.Job("r02", "full", "두산")
    jobs = tool.with_dependencies([tool.Job("r02", "no_peer", "두산")])
    assert [job.key for job in jobs] == ["r01/full/두산", "r02/full/두산", "r02/no_peer/두산"]
    assert Path(tool.report_relpath("r01", "one_team")) == Path("reports/one_team/one_team_gpt54_r01")


def test_first_source_keeps_a_key_and_sources_are_counted():
    store = tool.ReplayStore()
    key = ("r01", "full", "현대건설", "final", STEP)
    sha = tool.request_sha256(REQUEST)
    assert store.add(key, tool.ReplayEntry(STEP, sha, "inline", "a", extra={"content": "{}"}))
    assert not store.add(key, tool.ReplayEntry(STEP, sha, "inline", "b", extra={"content": "{}"}, label="/earlier"))
    assert store.add(key, tool.ReplayEntry(STEP, "other", "inline", "c", extra={"content": "{}"}, label="/earlier"))
    assert store.lookup(CONTEXT, STEP, sha).source == "a"
    assert store.count_by_source() == {"original": {"competitor": 1}, "/earlier": {"competitor": 1}}


def test_replayed_call_records_its_source_label(tmp_path, installed):
    store = tool.ReplayStore()
    store.add(("r01", "full", "현대건설", "final", STEP),
              tool.ReplayEntry(STEP, tool.request_sha256(REQUEST), "inline", "x", extra={"content": "{}"}, label="/earlier"))
    layer = tool.ReplayLayer(store, mode="plan", call_log=tmp_path / "calls.jsonl")
    with layer.scope(CONTEXT):
        layer.execute(never_called, never_called, request_payload=REQUEST, model="gpt-5.4", step=STEP)
    row = json.loads((tmp_path / "calls.jsonl").read_text())
    assert row["source_label"] == "/earlier" and layer.by_source == {"/earlier": 1}
    summary = tool.summarize_calls([row, {**row, "replayed": False, "source_label": None}], [tool.Job("r01", "full", "현대건설")])
    assert summary["by_source"] == {"/earlier": {"competitor": 1}, "new": {"competitor": 1}}


def test_earlier_rerun_outputs_replay_as_saved_model_json(tmp_path):
    day = "20251020"
    report = tmp_path / "report"
    comparison = {"comparison_version": "v1", "comparison_brief": "b"}
    tool.save(report / "Competitor" / day / "peer_comparison_output.json", comparison)
    strategy = report / "Strategy" / day
    tool.save(strategy / "strategy_decision_cache.json", {"fingerprint": "f2"})
    tool.save(strategy / "strategy_response_attempts/20261004T090000_f1.json", {"fingerprint": "f1", "decision_output": {"n": 1}})
    tool.save(strategy / "strategy_response_attempts/20261004T091000_f2.json", {"fingerprint": "f2", "decision_output": {"n": 2}})
    tool.save(strategy / "strategy_response_attempts/20261004T092000_f2.json", {"fingerprint": "f2", "decision_output": {"n": 3}})
    tool.save(strategy / "strategy_response_attempts/20261004T092000_f2.failure.json", {"status": "fail"})
    writer = report / "Writer" / day
    tool.save(writer / "llm_writer_output.json", {"fingerprint": "w", "raw_payload": {"sections": {}}, "usage": {"prompt_tokens": 5}})
    tool.save(writer / "writer_execution_cache.json", {"fingerprint": "w"})
    store = tool.ReplayStore()
    expected = {"competitor:comparison_analysis": comparison, "strategy:x": {"n": 2}, "writer:html_report": {"sections": {}}}
    for step, output in expected.items():
        kind, path, request_path = tool.saved_response(report, None, step, day)
        assert request_path is None
        entry = tool.ReplayEntry(step, "sha", kind, str(path), {"input_tokens": 7, "output_tokens": 2, "total_tokens": 9})
        response = store.response(entry, step, {"model": "gpt-5.4"})
        if step.startswith("strategy:"):
            assert json.loads(response["choices"][0]["message"]["content"]) == output
            assert response["usage"]["prompt_tokens"] == 7
        else:
            assert json.loads(response.choices[0].message.content) == output
    tool.save(writer / "writer_execution_cache.json", {"fingerprint": "changed"})
    assert tool.saved_response(report, None, "writer:html_report", day)[1] is None


@pytest.mark.parametrize("settings, message", [(None, "run_settings.json is missing"),
                                               ({"transport": "fake-canned"}, "only model responses")])
def test_replay_from_accepts_only_real_rerun_workspaces(tmp_path, settings, message):
    if settings is not None:
        tool.save(tmp_path / "run_settings.json", settings)
    with pytest.raises(SystemExit, match=message):
        tool.build_previous_store(None, tool.ReplayLayer(tool.ReplayStore(), mode="plan"), tmp_path)


def test_unselected_dependencies_run_only_the_needed_stages():
    jobs, partial = tool.plan_jobs([tool.Job("r02", "no_peer", "두산")])
    assert [job.key for job in jobs] == ["r01/full/두산", "r02/full/두산", "r02/no_peer/두산"]
    assert partial["r02/full/두산"] == {"target_inputs", "target_news", "target_financial", "target_market"}
    assert partial["r01/full/두산"] == {f"{role}_{kind}" for role in ("target", "peer")
                                       for kind in ("inputs", "monthly_summaries")}
    _, partial = tool.plan_jobs([tool.Job("r01", "one_team", "두산")])
    assert "target_monthly_summaries" in partial["r01/full/두산"] and "peer_financial" in partial["r01/full/두산"]
    jobs, partial = tool.plan_jobs([tool.Job("r01", "full", "두산"), tool.Job("r01", "no_peer", "두산")])
    assert partial == {} and len(jobs) == 2
    jobs, partial = tool.plan_jobs([tool.Job("r01", "no_peer", "두산")], add_dependencies=False)
    assert [job.key for job in jobs] == ["r01/no_peer/두산"] and partial == {}


def test_partial_run_skips_stages_outside_its_set(tmp_path):
    ctx = make_context(tmp_path)
    job = tool.Job("r01", "full", "현대건설")
    run = tool.ReportRun(ctx, job, frozenset({"target_news"}))
    run.paths = type("Paths", (), {"execution_dir": tmp_path / "execution"})()
    ran = []
    run.stage("target_news", lambda: ran.append("target_news"))
    run.stage("strategy", lambda: ran.append("strategy"))
    assert ran == ["target_news"]
