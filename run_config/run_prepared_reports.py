"""Run the approved 28-report experiment from frozen inputs, without collection."""
import argparse
from contextlib import redirect_stdout, redirect_stderr
from datetime import datetime, timezone
import fcntl
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import traceback

WORKSPACE = Path(__file__).resolve().parents[1]
REPO = WORKSPACE
PREPARED = WORKSPACE / "prepared_inputs/replicate_01"
STATUS = WORKSPACE / "status/report_generation_status.json"
USAGE = WORKSPACE / "status/report_generation_usage.jsonl"
sys.path[:0] = [str(REPO / "src"), str(REPO / "src/Agent_Team/YFinance_Agent")]
from orchestration import full_report_pipeline as flow
from orchestration.ablation import config_from_args
from orchestration.company_resolver import CompanyIdentity
from orchestration.config import load_project_env, build_run_key
from orchestration.usage_summary import estimate_api_cost
from Agent_Team.News_Agent import context_export, analysis_agent
from Agent_Team.Financial_Agent.langgraph_flow import build_financial_analyst_output
from Agent_Team.Financial_Agent.financial_analysis_agent import generate_financial_analysis_with_llm, apply_financial_analysis, build_financial_request
from Agent_Team.YFinance_Agent import reporting


def read(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def save(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + "\n", encoding="utf-8")
    temporary.replace(path)


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def now():
    return datetime.now(timezone.utc).isoformat()


def usage_summary():
    rows = [json.loads(line) for line in USAGE.read_text().splitlines() if line.strip()] if USAGE.exists() else []
    return {"transport_attempts": len(rows), "successful_attempts": sum(r.get("status") == "ok" for r in rows),
        "input_tokens": sum(r.get("usage", {}).get("input_tokens", 0) for r in rows),
        "output_tokens": sum(r.get("usage", {}).get("output_tokens", 0) for r in rows),
        "total_tokens": sum(r.get("usage", {}).get("total_tokens", 0) for r in rows),
        "estimated_api_cost": estimate_api_cost(rows)}


def check():
    manifest = read(PREPARED / "preparation_manifest.json")
    if manifest["status"] != "prepared_offline" or manifest["reports_planned"] != 28:
        raise ValueError("Expected the approved 28-report preparation")
    for path, expected in manifest["source_hashes"].items():
        if sha(path) != expected:
            raise ValueError(f"Frozen input changed: {path}")
    for relative, expected in manifest["generation_code_hashes"].items():
        repairs_path = WORKSPACE / "status/report_generation_repairs.json"
        repairs = read(repairs_path).get("code_repairs", {}) if repairs_path.exists() else {}
        if relative in repairs:
            repair = repairs[relative]
            if repair["original_sha256"] != expected:
                raise ValueError(f"Repair does not match frozen code: {relative}")
            expected = repair["repaired_sha256"]
        if sha(REPO / relative) != expected:
            raise ValueError(f"Generation code changed after preparation: {relative}")
    collection = read(WORKSPACE / "run_config/collection_manifest.json")
    load_project_env(Path(collection["environment_file"]))
    if not os.getenv("OPENAI_API_KEY", "").strip():
        raise RuntimeError("Configured OPENAI_API_KEY is missing")
    for spec in manifest["conditions"]:
        for entity in spec["entities"]:
            p = Path(entity["input_dir"])
            if spec["condition"] == "no_subdata":
                requests = [p / f"{kind}_request.json" for kind in ("news", "financial", "market")]
            else:
                requests = [Path(entity["news_request"]), Path(entity["summary_request"])]
            for path in requests:
                request = read(path)
                expected = "gpt-5.6-luna" if path.name == "llm_summary_request.json" else "gpt-5.4"
                if request["model"] != expected:
                    raise ValueError(f"Model differs from approved plan: {path}")
    return manifest, collection


def identity(entity):
    config = read(entity["company_config"])
    return CompanyIdentity(config["company_name"], config["corp_code"], config["stock_code"],
        config["ticker"].rsplit(".", 1)[-1], config["ticker"], {"provider": "frozen_collection_manifest"})


def materialize(entity, paths, condition):
    day, name = entity["selected_date"], entity["company_name"]
    root = paths.output_root if entity["role"] == "target" else paths.peer_output_root
    destination = root / name
    source = Path(entity["frozen_company_root"])
    if condition == "no_peer":
        source = WORKSPACE / "reports/full/replicate_01" / name
        for domain in ("Financial", "Y_Finance", "News"):
            shutil.copytree(source / domain / day, destination / domain / day, dirs_exist_ok=True)
        return destination
    for domain in ("Financial", "Y_Finance"):
        shutil.copytree(source / domain / day, destination / domain / day, dirs_exist_ok=True)
    prepared = Path(entity["primary_news_reused_from"] if condition == "no_subdata" else entity["input_dir"])
    shutil.copytree(prepared / "context_exports", destination / "News" / day / "context_exports", dirs_exist_ok=True)
    original = prepared / "news/news_agent_input_payload.json"
    if condition == "no_subdata":
        original = Path(entity["input_dir"]) / "news_agent_input_payload.json"
    payload = read(original)
    news_dir = destination / "News" / day / "output"
    payload["evidence_map_path"] = str(news_dir / "news_agent_evidence_map.json")
    save(news_dir / "news_agent_input_payload.json", payload)
    save(news_dir / "news_agent_evidence_map.json", payload["evidence_map"])
    save(news_dir / "news_agent_llm_request.json", analysis_agent.build_llm_request(input_payload=payload, model="gpt-5.4"))
    return destination


def summarize_months(root, day, update):
    from openai import OpenAI
    directory = root / "News" / day / "context_exports/month"
    request = read(directory / "llm_summary_request.json")
    period_results = []
    client = OpenAI(timeout=300, max_retries=0)
    for index, (period, monthly) in enumerate(context_export._build_period_llm_requests(request), 1):
        update(month=index, period=period)
        result = context_export._call_llm_summary(client, monthly)
        if result["output"].get("parse_error"):
            raise ValueError("Monthly summary JSON parsing failed; no automatic regeneration")
        period_results.append({"period": period, "status": "success", "usage": result["usage"],
            "output": context_export._extract_period_output(period, result["output"])})
        save(directory / "llm_period_summaries.json", context_export._split_summary_payload(request, period_results))
        update(month_completed=index)


def generate_domain(root, entity, condition, kind):
    day = entity["selected_date"]
    f, m, n = root / "Financial" / day, root / "Y_Finance" / day, root / "News" / day
    no_sub = condition == "no_subdata"
    if kind == "news":
        payload, request = read(n / "output/news_agent_input_payload.json"), read(n / "output/news_agent_llm_request.json")
        result = analysis_agent.execute_analysis_request(llm_request=request, input_payload=payload, model="gpt-5.4", timeout_seconds=300)
        if result["output"].get("parse_error"):
            raise ValueError("News JSON parsing failed; no automatic regeneration")
        save(n / "output/news_agent_handoff.json", result)
        save(n / "final_report.json", result)
    elif kind == "financial":
        factual = build_financial_analyst_output({"target_entity": read(n / "output/news_agent_input_payload.json")["target_entity"]},
            {"dart_main": read(f / "dart_main.json"), "dart_master": read(f / "dart_master.json"),
             "yfinance_market_summary": {} if no_sub else read(m / "market_summary.json"),
             "news_weekly_summaries": {} if no_sub else read(n / "context_exports/month/llm_period_summaries.json")})
        save(f / "actual_llm_request.json", build_financial_request(factual, model="gpt-5.4"))
        result = apply_financial_analysis(factual, generate_financial_analysis_with_llm(factual, model="gpt-5.4"))
        save(f / "final_report.json", result)
    else:
        reporting.generate_analyst_report(market_json=m / "market_full_dataset.json", dart_json=f / "dart_lightweight.json",
            news_json=n / "context_exports/month/llm_period_summaries.json", valuation_json=m / "valuation_snapshot.json",
            report_md=m / "final_report.md", report_json=m / "final_report.json", company_name=entity["company_name"],
            ticker=read(entity["company_config"])["ticker"], model="gpt-5.4", primary_data_only=no_sub)


def run(resume=False):
    lock = (WORKSPACE / "status/report_generation.lock").open("a+")
    fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    manifest, collection = check()
    if STATUS.exists() and not resume:
        raise RuntimeError("Generation already has a status file; inspect before restarting paid work")
    previous = read(STATUS) if resume else None
    if resume and previous["state"] != "failed":
        raise RuntimeError("Only an explicitly failed run can be resumed")
    os.environ.update(OPENAI_MODEL="gpt-5.4", NEWS_AGENT_LLM_MODEL="gpt-5.4", LLM_TIMEOUT_SECONDS="300",
        LLM_TRANSPORT_RETRIES="0", LLM_USAGE_MANIFEST=str(USAGE), PYTHONUNBUFFERED="1")
    state = {"state": "running", "worker_pid": os.getpid(), "started_at": now(), "reports_total": 28,
        "reports_completed": 0, "models": manifest["models"], "completed": [], "stages": [],
        "approved_cost_scenarios_usd": read(PREPARED / "cost_estimate.json")["totals"]}
    if previous:
        save(WORKSPACE / f"status/report_generation_before_resume_{os.getpid()}.json", previous)
        state = previous
        state.setdefault("resumptions", []).append({"resumed_at": now(), "previous_error": state.pop("error", None), "previous_stop": state.pop("stopped_at", None)})
        state.update(state="running", worker_pid=os.getpid())
    completed_stages = {(s["company"], s["condition"], s["stage"]) for s in state["stages"]}
    def update(**values):
        state.update(values)
        state["updated_at"] = now()
        state["usage"] = usage_summary()
        save(STATUS, state)
    def stage(name, action):
        key = (state["current_company"], state["current_condition"], name)
        if key in completed_stages:
            print(now(), *key, "reused; no execution", flush=True)
            return
        update(current_stage=name, month=None, period=None)
        print(now(), state["current_company"], state["current_condition"], name, "started", flush=True)
        action()
        state["stages"].append({"company": state["current_company"], "condition": state["current_condition"], "stage": name, "completed_at": now()})
        completed_stages.add(key)
        update()
    try:
        specs = sorted(manifest["conditions"], key=lambda x: (dict(full=0, random_news=1, no_subdata=2, no_peer=3)[x["condition"]],
            [r["target"]["company_name"] for r in collection["companies"]].index(x["target_company"])))
        for spec in specs:
            name, condition, day = spec["target_company"], spec["condition"], spec["selected_date"]
            completed = next((r for r in state["completed"] if r["company"] == name and r["condition"] == condition), None)
            if completed:
                report = Path(completed["report"])
                if not report.is_file() or report.stat().st_size == 0:
                    raise RuntimeError(f"Completed report is missing: {report}; refusing paid regeneration")
                print(now(), name, condition, "completed report reused", flush=True)
                continue
            update(current_company=name, current_condition=condition)
            paths = flow.FullPipelinePaths(Path(spec["report_output_root"]), build_run_key(name, day), name, day,
                f"h2_2025_{condition}_{read(spec['entities'][0]['company_config'])['stock_code']}_r01")
            paths.ensure_directories()
            args = flow.build_parser().parse_args(["--company-name", name, "--selected-date", day, *spec["final_flags"]])
            ablation = config_from_args(args)
            target = identity(spec["entities"][0])
            peer = identity(spec["entities"][1]) if len(spec["entities"]) == 2 else None
            env_file = Path(collection["environment_file"])
            flow._write_resolved_inputs(args=args, ablation=ablation, paths=paths, selected_date=day, target=target, peer=peer,
                peer_resolution={"status": "frozen_manual_pair" if peer else "disabled", "source": {"provider": "collection_manifest"},
                    "selection_basis": {"method": "frozen_experiment_pair"}})
            log_dir = paths.execution_dir / "logs"
            log_dir.mkdir(parents=True, exist_ok=True)
            for entity in spec["entities"]:
                root = materialize(entity, paths, condition)
                role = entity["role"]
                os.environ.update(LLM_EXECUTION_ID=paths.execution_id, LLM_RUN_ROLE=role,
                    LLM_RUN_ID=build_run_key(entity["company_name"], day), LLM_COMPANY_NAME=entity["company_name"])
                if condition == "no_peer":
                    continue
                if condition != "no_subdata":
                    with (log_dir / f"{role}_monthly_summaries.log").open("a") as stream, redirect_stdout(stream), redirect_stderr(stream):
                        stage(f"{role}_monthly_summaries", lambda: summarize_months(root, day, update))
                for kind in ("news", "financial", "market"):
                    with (log_dir / f"{role}_{kind}.log").open("a") as stream, redirect_stdout(stream), redirect_stderr(stream):
                        stage(f"{role}_{kind}", lambda kind=kind: generate_domain(root, entity, condition, kind))
            os.environ.update(LLM_EXECUTION_ID=paths.execution_id, LLM_RUN_ROLE="final", LLM_RUN_ID=paths.run_key, LLM_COMPANY_NAME=name)
            peer_key = build_run_key(peer.company_name, day) if peer else ""
            commands = []
            if peer:
                commands += [("peer_dataset", flow.build_peer_comparison_command(paths=paths, peer_run_key=peer_key, selected_date=day, target=target)),
                    ("peer_analysis", flow.build_peer_analysis_command(paths=paths, peer_run_key=peer_key, target=target, peer=peer, args=args, ablation=ablation, env_file=env_file))]
            commands += [("strategy", flow.build_strategy_command(paths=paths, selected_date=day, target=target, args=args, ablation=ablation, env_file=env_file)),
                ("chart_catalog", flow.build_visualization_catalog_command(paths=paths, target=target, peer_run_key=peer_key)),
                ("writer", flow.build_writer_generation_command(paths=paths, args=args, ablation=ablation, env_file=env_file)),
                ("charts", flow.build_visualization_command(paths=paths, target=target, peer_run_key=peer_key)),
                ("render", flow.build_writer_render_command(paths=paths, args=args, ablation=ablation, env_file=env_file))]
            save(paths.execution_dir / "commands.json", [{"stage": n, "command": c} for n, c in commands])
            for step, command in commands:
                def execute(command=command, step=step):
                    env = os.environ.copy()
                    env["PYTHONPATH"] = str(REPO / "src")
                    with (log_dir / f"{step}.log").open("a") as stream:
                        result = subprocess.run(command, cwd=REPO, env=env, stdout=stream, stderr=subprocess.STDOUT, timeout=900)
                    if result.returncode:
                        raise RuntimeError(f"{name}/{condition}/{step} failed; inspect {log_dir / (step+'.log')} before retrying")
                stage(step, execute)
            if read(paths.writer_dir / "writer_run_status.json")["status"] != "success":
                raise RuntimeError("Writer did not complete successfully")
            flow._publish_final_report(paths)
            state["completed"].append({"company": name, "condition": condition, "report": str(paths.published_report)})
            update(reports_completed=len(state["completed"]))
            save(paths.execution_dir / "experiment_manifest.json", {"status": "success", "specification": spec,
                "report": str(paths.published_report), "source_preparation": str(PREPARED / "preparation_manifest.json")})
        update(state="success", completed_at=now(), current_stage="complete")
    except Exception as exc:
        update(state="failed", error={"type": type(exc).__name__, "message": str(exc)}, stopped_at=now())
        traceback.print_exc()
        raise


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("check", "run", "launch", "resume", "launch-resume"))
    action = parser.parse_args().action
    if action == "check":
        check()
        print("Frozen data/code, approved models and configured API key: checked; paid calls: 0")
    elif action in ("run", "resume"):
        run(resume=action == "resume")
    else:
        check()
        resuming = action == "launch-resume"
        if resuming and (not STATUS.exists() or read(STATUS)["state"] != "failed"):
            raise RuntimeError("No failed generation to resume")
        if STATUS.exists() and not resuming:
            raise RuntimeError("A generation status already exists; refusing duplicate paid launch")
        log = WORKSPACE / "logs/report_generation_background.log"
        log.parent.mkdir(parents=True, exist_ok=True)
        with log.open("a") as stream:
            child = subprocess.Popen([sys.executable, "-u", str(Path(__file__).resolve()), "resume" if resuming else "run"], cwd=REPO,
                stdin=subprocess.DEVNULL, stdout=stream, stderr=subprocess.STDOUT, start_new_session=True)
        (WORKSPACE / "status/report_generation_worker.pid").write_text(str(child.pid) + "\n")
        print(f"Detached worker PID: {child.pid}; log: {log}")
