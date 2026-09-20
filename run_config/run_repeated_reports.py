"""Repeat the frozen five-condition experiment without collecting or summarizing data.

Only orchestration and output locations differ from replicate 01. The original
agent modules, model settings and prompts are imported unchanged.
"""
from __future__ import annotations

import argparse
import copy
from contextlib import redirect_stderr, redirect_stdout
import fcntl
import html
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
from types import SimpleNamespace

import run_prepared_reports as original
import run_one_team_reports as unified

WORKSPACE, REPO = original.WORKSPACE, original.REPO
BATCH = WORKSPACE / "reports/repeated_standard_5companies"
MANIFEST = BATCH / "preparation_manifest.json"
STATUS = BATCH / "status.json"
USAGE = BATCH / "llm_usage.jsonl"
COMPANIES = ("현대건설", "두산", "BGF리테일", "아모레퍼시픽", "SK바이오팜")
CONDITIONS = ("full", "random_news", "no_subdata", "no_peer", "one_team")
REPLICATES = (2, 3)
read, save, sha, now = original.read, original.save, original.sha, original.now
flow = original.flow
EXCLUDED_INPUT_NAMES = {"final_report.json", "final_report.md", "actual_llm_request.json", "news_agent_handoff.json"}


def source_files(source, day):
    for domain in ("Financial", "Y_Finance", "News"):
        for path in sorted((source / domain / day).rglob("*")):
            if path.is_file() and path.name not in EXCLUDED_INPUT_NAMES:
                yield path


def make_paths(spec):
    name, day = spec["target_company"], spec["selected_date"]
    return flow.FullPipelinePaths(Path(spec["report_output_root"]), original.build_run_key(name, day), name, day,
        f"repeat_standard_{spec['condition']}_{original.identity(spec['entities'][0]).stock_code}_r{spec['replicate']:02d}")


def prepare():
    if MANIFEST.exists():
        return check()
    if BATCH.exists() and any(BATCH.iterdir()):
        raise RuntimeError("Batch directory is nonempty without a manifest; inspect before preparing")
    old, collection = original.check()
    one = unified.check(SimpleNamespace(model="gpt-5.4", downstream_model="gpt-5.4", replicate=1,
        run_id="one_team_gpt54_r01", companies="all"))
    if read(original.STATUS)["state"] != "success":
        raise RuntimeError("The original run must be complete")
    originals = {(s["condition"], s["target_company"]): s for s in old["conditions"] + one["conditions"]}
    jobs, sources = [], {}
    for replicate in REPLICATES:
        for condition in CONDITIONS:
            for name in COMPANIES:
                spec = copy.deepcopy(originals[condition, name])
                spec.update(replicate=replicate, report_output_root=str(BATCH / f"replicate_{replicate:02d}" / condition))
                for entity in spec["entities"]:
                    source_condition = "full" if condition in {"one_team", "no_peer"} else condition
                    source = WORKSPACE / "reports" / source_condition / "replicate_01" / name
                    if entity["role"] == "peer":
                        source = source / "비교기업" / entity["company_name"]
                    entity["source_root"] = str(source)
                    for path in source_files(source, spec["selected_date"]):
                        sources[str(path)] = sha(path)
                    sources[entity["company_config"]] = sha(entity["company_config"])
                    if condition == "one_team":
                        for key in ("prepared_packet", "request_path"):
                            sources[entity[key]] = sha(entity[key])
                jobs.append(spec)
    code = [*sorted((REPO / "src").rglob("*.py")), Path(__file__),
            Path(original.__file__), Path(unified.__file__)]
    result = {"status": "prepared_offline", "prepared_at": now(), "companies": COMPANIES,
        "conditions": CONDITIONS, "replicates": REPLICATES, "reports_planned": 50,
        "model": "gpt-5.4", "service_tier": "unchanged_standard", "new_summary_calls": 0,
        "new_collection_calls": 0, "planned_analysis_calls": 200, "planned_final_calls": 140,
        "estimated_cost_usd_no_cache": 57.02236,
        "policy": {"original_agent_code_unchanged": True, "fixed_random_selection": True,
                   "same_replicate_full_target_reused_by_no_peer": True,
                   "prior_analysis_reused_across_replicates": False,
                   "reuse_monthly_summaries": True, "automatic_evaluation": False},
        "environment_file": collection["environment_file"], "source_hashes": sources,
        "code_hashes": {str(p): sha(p) for p in code}, "jobs": jobs}
    save(MANIFEST, result)
    write_index([])
    return result


def check():
    manifest = read(MANIFEST)
    if (manifest["companies"] != list(COMPANIES) or manifest["replicates"] != list(REPLICATES)
            or manifest["model"] != "gpt-5.4" or manifest["reports_planned"] != 50):
        raise ValueError("Batch parameters differ from the prepared run")
    for group in ("source_hashes", "code_hashes"):
        for path, expected in manifest[group].items():
            if sha(path) != expected:
                raise ValueError(f"Frozen {group} changed: {path}")
    return manifest


def materialize(spec, entity, paths):
    day, name = spec["selected_date"], entity["company_name"]
    destination = (paths.output_root if entity["role"] == "target" else paths.peer_output_root) / name
    if spec["condition"] == "no_peer":
        source = BATCH / f"replicate_{spec['replicate']:02d}" / "full" / name
        for domain in ("Financial", "Y_Finance", "News"):
            if not (source / domain / day / "final_report.json").is_file():
                raise RuntimeError(f"No-peer needs completed same-replicate Full: {source}")
        for domain in ("Financial", "Y_Finance", "News"):
            shutil.copytree(source / domain / day, destination / domain / day, dirs_exist_ok=True)
    else:
        source = Path(entity["source_root"])
        for path in source_files(source, day):
            target = destination / path.relative_to(source)
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(path, target)
    # This is an on-disk evidence lookup, not a model-input change.
    payload_path = destination / "News" / day / "output/news_agent_input_payload.json"
    payload = read(payload_path)
    payload["evidence_map_path"] = str(payload_path.parent / "news_agent_evidence_map.json")
    save(payload_path, payload)
    rebuilt = original.analysis_agent.build_llm_request(input_payload=payload, model="gpt-5.4")
    if rebuilt != read(payload_path.parent / "news_agent_llm_request.json"):
        raise ValueError("News request changed while relocating inputs")
    return destination


def generate_unified(root, entity):
    """Use the same saved request and postprocessing as the existing one-team runner."""
    day = entity["selected_date"]
    directory = root / "runs" / day / "unified_domain_team"
    request = read(entity["request_path"])
    packet = read(entity["prepared_packet"])
    save(directory / "unified_request.json", request)
    raw_path = directory / "unified_response.json"
    if raw_path.exists():
        raw = read(raw_path)
        if raw["request_sha256"] != sha(entity["request_path"]):
            raise ValueError("Saved integrated response belongs to another input")
    else:
        response = unified.call_domain_response(request, step="unified:domain_agent", timeout_seconds=300)
        raw = {"request_sha256": sha(entity["request_path"]), "output": json.loads(response.output_text),
               "usage": unified.normalize_usage(response.usage), "response_id": getattr(response, "id", None)}
        save(raw_path, raw)
    output, changes = unified.normalize_source_domains(raw["output"], packet["semantic_input"])
    unified.validate_output(output, packet["semantic_input"])
    config = read(entity["company_config"])
    outputs = unified.write_report(output=output, prepared=packet,
        destination_paths=SimpleNamespace(run_dir=root / "runs" / day),
        run_config=SimpleNamespace(company_name=entity["company_name"], ticker=config["ticker"],
                                   selected_date_iso=f"{day[:4]}-{day[4:6]}-{day[6:]}"),
        model="gpt-5.4", role=entity["role"])
    save(directory / "manifest.json", {"status": "success", "protocol": unified.PROTOCOL,
        "outputs": outputs, "usage": raw["usage"], "domain_metadata_normalizations": changes,
        "source_artifacts": packet["source_artifacts"]})


def downstream(spec, paths, env_file):
    if spec["condition"] == "one_team":
        return unified.stage_commands(spec, paths, "gpt-5.4", env_file)
    name, day = spec["target_company"], spec["selected_date"]
    args = flow.build_parser().parse_args(["--company-name", name, "--selected-date", day, *spec["final_flags"]])
    ablation = original.config_from_args(args)
    target = original.identity(spec["entities"][0])
    peer = original.identity(spec["entities"][1]) if len(spec["entities"]) == 2 else None
    peer_key = original.build_run_key(peer.company_name, day) if peer else ""
    commands = []
    if peer:
        commands += [("peer_dataset", flow.build_peer_comparison_command(paths=paths, peer_run_key=peer_key, selected_date=day, target=target)),
            ("peer_analysis", flow.build_peer_analysis_command(paths=paths, peer_run_key=peer_key, target=target, peer=peer, args=args, ablation=ablation, env_file=env_file))]
    commands += [("strategy", flow.build_strategy_command(paths=paths, selected_date=day, target=target, args=args, ablation=ablation, env_file=env_file)),
        ("chart_catalog", flow.build_visualization_catalog_command(paths=paths, target=target, peer_run_key=peer_key)),
        ("writer", flow.build_writer_generation_command(paths=paths, args=args, ablation=ablation, env_file=env_file)),
        ("charts", flow.build_visualization_command(paths=paths, target=target, peer_run_key=peer_key)),
        ("render", flow.build_writer_render_command(paths=paths, args=args, ablation=ablation, env_file=env_file))]
    return args, ablation, target, peer, commands


def usage_summary():
    rows = [json.loads(line) for line in USAGE.read_text().splitlines() if line.strip()] if USAGE.exists() else []
    return {"attempts": len(rows), "successful_calls": sum(r["status"] == "ok" for r in rows),
        "total_tokens": sum(r.get("usage", {}).get("total_tokens", 0) for r in rows),
        "estimated_api_cost": original.estimate_api_cost(rows)}


def write_index(completed):
    done = {row["key"]: row for row in completed}
    lines = ["<!doctype html><html lang='ko'><meta charset='utf-8'><title>추가 반복 실험</title>",
             "<h1>GPT-5.4 Standard 추가 반복 실험</h1><p>기존 입력·월별 요약 고정, 분석부터 새로 생성</p><ul>"]
    for r in REPLICATES:
        for c in CONDITIONS:
            for name in COMPANIES:
                key = f"r{r:02d}/{c}/{name}"
                report = done.get(key, {}).get("report")
                label = html.escape(key)
                if report:
                    label = f'<a href="{html.escape(str(Path(report).relative_to(BATCH)), quote=True)}">{label}</a>'
                lines.append(f"<li>{label} — {'완료' if report else '대기'}</li>")
    lines.append("</ul></html>")
    (BATCH / "index.html").write_text("\n".join(lines), encoding="utf-8")


def execute_run(*, resume=False):
    BATCH.mkdir(parents=True, exist_ok=True)
    with (BATCH / "run.lock").open("a+") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        manifest = check()
        if STATUS.exists() and not resume:
            raise RuntimeError("Status exists; refuse duplicate paid execution")
        if resume and not STATUS.exists():
            raise RuntimeError("No existing run to resume")
        state = read(STATUS) if resume else {"state": "running", "started_at": now(), "reports_total": 50,
            "completed": [], "stages": [], "failures": []}
        if state["state"] == "success":
            return
        original.load_project_env(Path(manifest["environment_file"]))
        if not os.getenv("OPENAI_API_KEY", "").strip():
            raise RuntimeError("Configured API key is missing")
        os.environ.update(OPENAI_MODEL="gpt-5.4", NEWS_AGENT_LLM_MODEL="gpt-5.4", LLM_TIMEOUT_SECONDS="300",
            LLM_TRANSPORT_RETRIES="0", LLM_USAGE_MANIFEST=str(USAGE), PYTHONUNBUFFERED="1")
        for key in ("ONE_TEAM_RUNTIME", "ONE_TEAM_SINGLE_REPORT"):
            os.environ.pop(key, None)
        state.update(state="running", worker_pid=os.getpid())
        done_stages = {(r["key"], r["stage"]) for r in state["stages"]}
        def update(**values):
            state.update(values, updated_at=now(), reports_completed=len(state["completed"]), usage=usage_summary())
            save(STATUS, state)
        def stage(key, name, action):
            if (key, name) in done_stages:
                return
            update(current_job=key, current_stage=name)
            print(now(), key, name, "started", flush=True)
            action()
            state["stages"].append({"key": key, "stage": name, "completed_at": now()})
            done_stages.add((key, name))
            update()
        update()
        for spec in manifest["jobs"]:
            name, condition, day = spec["target_company"], spec["condition"], spec["selected_date"]
            key = f"r{spec['replicate']:02d}/{condition}/{name}"
            previous = next((r for r in state["completed"] if r["key"] == key), None)
            if previous:
                if not Path(previous["report"]).is_file() or sha(previous["report"]) != previous["sha256"]:
                    raise RuntimeError(f"Completed report changed: {key}; refusing regeneration")
                continue
            paths = make_paths(spec)
            paths.ensure_directories()
            args, ablation, target, peer, commands = downstream(spec, paths, Path(manifest["environment_file"]))
            flow._write_resolved_inputs(args=args, ablation=ablation, paths=paths, selected_date=day,
                target=target, peer=peer, peer_resolution={"status": "frozen_manual_pair" if peer else "disabled",
                    "source": {"provider": "collection_manifest"}, "selection_basis": {"method": "frozen_experiment_pair"}})
            logs = paths.execution_dir / "logs"
            logs.mkdir(exist_ok=True)
            try:
                for entity in spec["entities"]:
                    stage(key, f"{entity['role']}_inputs", lambda: materialize(spec, entity, paths))
                    root = (paths.output_root if entity["role"] == "target" else paths.peer_output_root) / entity["company_name"]
                    os.environ.update(LLM_EXECUTION_ID=paths.execution_id, LLM_RUN_ROLE=entity["role"],
                        LLM_RUN_ID=original.build_run_key(entity["company_name"], day), LLM_COMPANY_NAME=entity["company_name"])
                    if condition == "no_peer":
                        continue
                    for kind in (("unified",) if condition == "one_team" else ("news", "financial", "market")):
                        with (logs / f"{entity['role']}_{kind}.log").open("a") as stream, redirect_stdout(stream), redirect_stderr(stream):
                            stage(key, f"{entity['role']}_{kind}",
                                  lambda: generate_unified(root, entity) if kind == "unified"
                                  else original.generate_domain(root, entity, condition, kind))
                env = {**os.environ, "LLM_RUN_ROLE": "final", "LLM_RUN_ID": paths.run_key,
                       "LLM_COMPANY_NAME": name, "PYTHONPATH": str(REPO / "src")}
                if condition == "one_team":
                    env.update(ONE_TEAM_RUNTIME="1", ONE_TEAM_SINGLE_REPORT="1",
                        PYTHONPATH=os.pathsep.join([str(unified.PACKAGE / "runtime"), str(REPO / "src")]))
                save(paths.execution_dir / "commands.json", [{"stage": n, "command": c} for n, c in commands])
                for step, command in commands:
                    def execute(step=step, command=command):
                        with (logs / f"{step}.log").open("a") as stream:
                            result = subprocess.run(command, cwd=REPO, env=env, stdout=stream,
                                stderr=subprocess.STDOUT, timeout=900)
                        if result.returncode:
                            raise RuntimeError(f"{key}/{step} failed: {logs / (step + '.log')}")
                    stage(key, step, execute)
                if read(paths.writer_dir / "writer_run_status.json")["status"] != "success":
                    raise RuntimeError("Writer did not complete")
                flow._publish_final_report(paths)
                state["completed"].append({"key": key, "report": str(paths.published_report),
                    "sha256": sha(paths.published_report), "completed_at": now()})
                save(paths.execution_dir / "repeat_manifest.json", {"status": "success", "specification": spec,
                    "source_preparation": str(MANIFEST), "report": str(paths.published_report)})
                update()
                write_index(state["completed"])
            except Exception as exc:
                # Do not re-run completed API stages or rewrite prompts after a failure.
                state["failures"].append({"key": key, "stage": state.get("current_stage"),
                    "type": type(exc).__name__, "message": str(exc), "at": now()})
                print(now(), key, "FAILED", type(exc).__name__, str(exc), flush=True)
                update()
                if type(exc).__name__ in {"AuthenticationError", "PermissionDeniedError"}:
                    update(state="failed", stopped_at=now())
                    raise
        check()
        update(state="success" if len(state["completed"]) == 50 else "completed_with_failures", completed_at=now())
        print(json.dumps({"reports_completed": len(state["completed"]), "usage": state["usage"]}, ensure_ascii=False), flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("prepare", "check", "run", "resume", "launch", "launch-resume"))
    action = parser.parse_args().action
    if action == "prepare":
        print(json.dumps({k: v for k, v in prepare().items() if k in {"companies", "reports_planned", "new_summary_calls", "estimated_cost_usd_no_cache"}}, ensure_ascii=False))
    elif action == "check":
        print(f"Verified {check()['reports_planned']} reports; paid calls: 0")
    elif action in {"run", "resume"}:
        execute_run(resume=action == "resume")
    else:
        check()
        resume = action == "launch-resume"
        if STATUS.exists() and not resume:
            raise RuntimeError("Existing status; refusing duplicate launch")
        # Serialize launches; the worker separately owns run.lock for its whole lifetime.
        with (BATCH / "launch.lock").open("a+") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            pid_path = BATCH / "worker.pid"
            if pid_path.exists():
                try:
                    os.kill(int(pid_path.read_text().strip()), 0)
                except ProcessLookupError:
                    pass
                else:
                    raise RuntimeError("A worker PID is still alive")
            with (BATCH / "background.log").open("a") as stream:
                child = subprocess.Popen([sys.executable, "-u", str(Path(__file__).resolve()), "resume" if resume else "run"],
                    cwd=WORKSPACE, stdin=subprocess.DEVNULL, stdout=stream, stderr=subprocess.STDOUT, start_new_session=True)
            pid_path.write_text(str(child.pid) + "\n")
            print(f"Detached PID {child.pid}; outputs/status/logs: {BATCH}")


if __name__ == "__main__":
    main()
