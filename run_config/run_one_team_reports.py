"""Prepare or run the one-team condition alongside the existing frozen experiment."""
from __future__ import annotations

import argparse
from contextlib import contextmanager
from datetime import datetime, timezone
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
from types import SimpleNamespace

WORKSPACE = Path(__file__).resolve().parents[1]
REPO = WORKSPACE
PACKAGE = REPO / "src/Agent_Team/Unified_Agent"
sys.path.insert(0, str(REPO / "src"))
from Agent_Team.Unified_Agent.io import read_json as read, write_json as save
from Agent_Team.Unified_Agent.inputs import prepare_entity
from Agent_Team.Unified_Agent.report import PROTOCOL, build_request, normalize_source_domains, validate_output, write_report
from orchestration import full_report_pipeline as flow
from orchestration.ablation import config_from_args
from orchestration.config import build_run_key, load_project_env
from orchestration.company_resolver import CompanyIdentity
from orchestration.usage_summary import summarize_execution_usage
from shared.domain_llm import call_domain_response
from shared.llm_clients import measure_request, normalize_usage


def now():
    return datetime.now(timezone.utc).isoformat()


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def locations(args):
    identifier = args.run_id or f"one_team_{args.model.replace('.', '_')}_r{args.replicate:02d}"
    if not re.fullmatch(r"[A-Za-z0-9_-]+", identifier):
        raise ValueError("run-id must contain only letters, digits, underscores or hyphens")
    return SimpleNamespace(identifier=identifier,
        prepared=WORKSPACE / "prepared_inputs/one_team" / identifier,
        output=WORKSPACE / "reports/one_team" / identifier,
        status=WORKSPACE / "status/one_team" / f"{identifier}.json",
        usage=WORKSPACE / "status/one_team" / f"{identifier}_usage.jsonl",
        lock=WORKSPACE / "status/one_team" / f"{identifier}.lock")


def manifest_path(args):
    return locations(args).prepared / "preparation_manifest.json"


def prepare(args):
    paths = locations(args)
    if manifest_path(args).exists():
        manifest = check(args)
        print(f"Existing one-team preparation verified: {manifest_path(args)}; paid calls: 0")
        return manifest
    collection = read(WORKSPACE / "run_config/collection_manifest.json")
    preparation = read(WORKSPACE / "prepared_inputs/replicate_01/preparation_manifest.json")
    previous = read(WORKSPACE / "status/report_generation_status.json")
    completed = {row["company"]: row for row in previous["completed"] if row["condition"] == "full"}
    full_specs = {row["target_company"]: row for row in preparation["conditions"] if row["condition"] == "full"}
    selected = args.companies.split(",") if args.companies != "all" else list(full_specs)
    if len(selected) != len(set(selected)) or set(selected) - set(full_specs):
        raise ValueError(f"Choose companies from: {', '.join(full_specs)}")
    input_hashes = {str(WORKSPACE / "run_config/collection_manifest.json"): sha(WORKSPACE / "run_config/collection_manifest.json")}
    prepared_hashes, specs = {}, []
    for name in selected:
        if name not in completed or not Path(completed[name]["report"]).is_file():
            raise ValueError(f"Completed Full source is missing: {name}")
        full = full_specs[name]
        entities = []
        for entity in full["entities"]:
            source = WORKSPACE / "reports/full/replicate_01" / name
            if entity["role"] == "peer":
                source = source / "비교기업" / entity["company_name"]
            prepared = prepare_entity(source_root=source, entity=entity, model=args.model)
            directory = paths.prepared / name / entity["role"]
            packet_path = save(directory / "preprocessed_input_bundle.json", prepared)
            request = build_request(prepared["semantic_input"], model=args.model)
            request_path = save(directory / "unified_request.json", request)
            for path in (packet_path, request_path):
                prepared_hashes[str(path)] = sha(path)
            for artifact in prepared["source_artifacts"].values():
                input_hashes[artifact["path"]] = artifact["sha256"]
            entities.append({**entity, "source_root": str(source), "prepared_packet": str(packet_path),
                "request_path": str(request_path),
                "evidence_counts": {domain: len(prepared["semantic_input"][domain]["evidence"])
                                    for domain in ("financial", "news", "market")},
                "request_measurement": measure_request(request, model=args.model).as_dict()})
        specs.append({"target_company": name, "condition": "one_team", "selected_date": full["selected_date"],
            "replicate": args.replicate, "entities": entities, "report_output_root": str(paths.output)})
    code_files = [*sorted((REPO / "src").rglob("*.py")), Path(__file__).resolve()]
    manifest = {"status": "prepared_offline", "protocol": PROTOCOL, "run_id": paths.identifier,
        "replicate": args.replicate, "models": {"integrated_analysis": args.model, "downstream": args.downstream_model,
                                                "reused_news_summary": "gpt-5.6-luna"},
        "reports_planned": len(specs), "new_integrated_calls": len(specs) * 2,
        "new_downstream_calls": len(specs) * 3, "new_summary_calls": 0, "paid_calls": 0,
        "existing_full_analysis_model": preparation["models"]["analysis"],
        "condition_difference": "integrated architecture and requested analysis model; not an architecture-only comparison",
        "source_preparation": str(WORKSPACE / "prepared_inputs/replicate_01/preparation_manifest.json"),
        "environment_file": collection["environment_file"], "source_hashes": input_hashes,
        "prepared_hashes": prepared_hashes, "code_hashes": {str(path): sha(path) for path in code_files},
        "conditions": specs, "prepared_at": now()}
    save(manifest_path(args), manifest)
    print(f"Prepared {len(specs)} one-team reports; {len(specs)*2} integrated + {len(specs)*3} downstream calls planned; paid calls: 0")
    print(manifest_path(args))
    return manifest


def check(args):
    manifest = read(manifest_path(args))
    if manifest["status"] != "prepared_offline" or manifest["protocol"] != PROTOCOL:
        raise ValueError("Unexpected one-team preparation contract")
    expected = {"integrated_analysis": args.model, "downstream": args.downstream_model, "reused_news_summary": "gpt-5.6-luna"}
    if manifest["models"] != expected or manifest["replicate"] != args.replicate:
        raise ValueError("Run parameters differ from preparation; use a new run-id")
    selected = {row["target_company"] for row in manifest["conditions"]}
    if args.companies != "all" and set(args.companies.split(",")) != selected:
        raise ValueError("Company selection differs from preparation; use a new run-id")
    for group in ("source_hashes", "prepared_hashes", "code_hashes"):
        for path, expected_hash in manifest[group].items():
            if sha(path) != expected_hash:
                raise ValueError(f"Frozen one-team {group} changed: {path}; use a new run-id")
    for spec in manifest["conditions"]:
        for entity in spec["entities"]:
            request = read(entity["request_path"])
            packet = read(entity["prepared_packet"])
            if request != build_request(packet["semantic_input"], model=args.model):
                raise ValueError("Prepared request differs from its input bundle")
    return manifest


def identity(entity):
    config = read(entity["company_config"])
    return CompanyIdentity(config["company_name"], config["corp_code"], config["stock_code"],
        config["ticker"].rsplit(".", 1)[-1], config["ticker"], {"provider": "frozen_collection_manifest"})


@contextmanager
def telemetry(values):
    previous = {key: os.environ.get(key) for key in values}
    os.environ.update(values)
    try:
        yield
    finally:
        for key, value in previous.items():
            if value is None: os.environ.pop(key, None)
            else: os.environ[key] = value


def stage_commands(spec, paths, model, env_file):
    name, day = spec["target_company"], spec["selected_date"]
    target, peer = map(identity, spec["entities"])
    args = flow.build_parser().parse_args(["--company-name", name, "--selected-date", day,
        "--llm-model", model, "--news-summary-model", "gpt-5.6-luna",
        "--decision-horizon-profile", "annual"])
    ablation = config_from_args(args)
    peer_key = build_run_key(peer.company_name, day)
    return args, ablation, target, peer, [
        ("peer_dataset", flow.build_peer_comparison_command(paths=paths, peer_run_key=peer_key, selected_date=day, target=target)),
        ("peer_analysis", flow.build_peer_analysis_command(paths=paths, peer_run_key=peer_key, target=target, peer=peer, args=args, ablation=ablation, env_file=env_file)),
        ("strategy", flow.build_strategy_command(paths=paths, selected_date=day, target=target, args=args, ablation=ablation, env_file=env_file)),
        ("chart_catalog", flow.build_visualization_catalog_command(paths=paths, target=target, peer_run_key=peer_key)),
        ("writer", flow.build_writer_generation_command(paths=paths, args=args, ablation=ablation, env_file=env_file)),
        ("charts", flow.build_visualization_command(paths=paths, target=target, peer_run_key=peer_key)),
        ("render", flow.build_writer_render_command(paths=paths, args=args, ablation=ablation, env_file=env_file)),
    ]


def materialize(entity, paths):
    source = Path(entity["source_root"])
    root = paths.output_root if entity["role"] == "target" else paths.peer_output_root
    destination = root / entity["company_name"]
    day = entity["selected_date"]
    for source_file in sorted(source.rglob("*")):
        relative = source_file.relative_to(source)
        if not source_file.is_file() or len(relative.parts) < 3:
            continue
        if relative.parts[0] not in {"Financial", "Y_Finance", "News"} or relative.parts[1] != day:
            continue
        # Copy source/preprocessing artifacts, never the existing LLM analyses.
        if source_file.name in {"final_report.json", "final_report.md", "actual_llm_request.json", "news_agent_handoff.json"}:
            continue
        target_file = destination / relative
        target_file.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source_file, target_file)
    return destination


def run(args, *, resume=False):
    manifest = check(args)
    local = locations(args)
    local.lock.parent.mkdir(parents=True, exist_ok=True)
    with local.lock.open("a+") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        if local.status.exists() and not resume:
            raise RuntimeError("One-team status exists; use resume instead of generating duplicate reports")
        if resume and not local.status.exists():
            raise RuntimeError("No existing one-team run to resume")
        state = read(local.status) if resume else {"state": "running", "run_id": local.identifier,
            "protocol": PROTOCOL, "started_at": now(), "models": manifest["models"],
            "reports_total": manifest["reports_planned"], "completed": [], "stages": [], "entities": []}
        if state.get("run_id") != manifest["run_id"] or state.get("models") != manifest["models"]:
            raise ValueError("One-team status differs from preparation")
        if resume and state["state"] not in {"running", "failed", "success"}:
            raise RuntimeError("Unexpected prior run state")
        env_file = Path(manifest["environment_file"])
        load_project_env(env_file)
        if not os.getenv("OPENAI_API_KEY", "").strip():
            raise RuntimeError("Configured OPENAI_API_KEY is missing")
        state.update(state="running", worker_pid=os.getpid())
        state.pop("error", None)
        completed_stages = {(row["company"], row["stage"]) for row in state["stages"]}
        def update(**values):
            state.update(values, updated_at=now())
            save(local.status, state)
        def stage(company, name, action):
            if (company, name) in completed_stages:
                return
            update(current_company=company, current_stage=name)
            action()
            state["stages"].append({"company": company, "stage": name, "completed_at": now()})
            completed_stages.add((company, name))
            update()
        try:
            for spec in manifest["conditions"]:
                name, day = spec["target_company"], spec["selected_date"]
                previous = next((row for row in state["completed"] if row["company"] == name), None)
                if previous:
                    if not Path(previous["report"]).is_file() or Path(previous["report"]).stat().st_size == 0:
                        raise RuntimeError("Completed one-team HTML is missing; refusing regeneration")
                    continue
                paths = flow.FullPipelinePaths(Path(spec["report_output_root"]), build_run_key(name, day), name, day,
                    f"{local.identifier}_{identity(spec['entities'][0]).stock_code}")
                paths.ensure_directories()
                native_args, ablation, target, peer, commands = stage_commands(spec, paths, args.downstream_model, env_file)
                flow._write_resolved_inputs(args=native_args, ablation=ablation, paths=paths, selected_date=day,
                    target=target, peer=peer, peer_resolution={"status": "frozen_manual_pair",
                        "source": {"provider": "collection_manifest"}, "selection_basis": {"method": "frozen_experiment_pair"}})
                for entity in spec["entities"]:
                    root = materialize(entity, paths)
                    report_dir = root / "runs" / day / "unified_domain_team"
                    report_path = report_dir / "unified_report.json"
                    def generate(entity=entity, root=root, report_dir=report_dir):
                        request = read(entity["request_path"])
                        prepared = read(entity["prepared_packet"])
                        save(report_dir / "unified_request.json", request)
                        values = {"LLM_USAGE_MANIFEST": str(local.usage), "LLM_EXECUTION_ID": paths.execution_id,
                            "LLM_RUN_ROLE": entity["role"], "LLM_RUN_ID": build_run_key(entity["company_name"], day),
                            "LLM_COMPANY_NAME": entity["company_name"]}
                        raw_path = report_dir / "unified_response.json"
                        request_hash = sha(entity["request_path"])
                        if raw_path.exists():
                            raw = read(raw_path)
                            if raw["request_sha256"] != request_hash:
                                raise ValueError("Saved response belongs to a different request")
                        else:
                            with telemetry(values):
                                response = call_domain_response(request, step="unified:domain_agent", timeout_seconds=300)
                            raw = {"request_sha256": request_hash, "output": json.loads(response.output_text),
                                "usage": normalize_usage(response.usage), "response_id": getattr(response, "id", None)}
                            save(raw_path, raw)
                        output, domain_changes = normalize_source_domains(raw["output"], prepared["semantic_input"])
                        validate_output(output, prepared["semantic_input"])
                        config = read(entity["company_config"])
                        outputs = write_report(output=output, prepared=prepared,
                            destination_paths=SimpleNamespace(run_dir=root / "runs" / day),
                            run_config=SimpleNamespace(company_name=entity["company_name"], ticker=config["ticker"],
                                selected_date_iso=f"{day[:4]}-{day[4:6]}-{day[6:]}"), model=args.model, role=entity["role"])
                        save(report_dir / "manifest.json", {"status": "success", "protocol": PROTOCOL,
                            "semantic_call_count": 1, "analysis_report_count": 1, "outputs": outputs,
                            "usage": raw["usage"], "domain_metadata_normalizations": domain_changes,
                            "raw_response": str(raw_path), "source_artifacts": prepared["source_artifacts"]})
                    stage(name, f"{entity['role']}_unified", generate)
                    if not report_path.is_file():
                        raise RuntimeError("Completed integrated report is missing; refusing paid regeneration")
                env = {**os.environ, "OPENAI_MODEL": args.downstream_model, "NEWS_AGENT_LLM_MODEL": args.downstream_model,
                    "ONE_TEAM_RUNTIME": "1", "ONE_TEAM_SINGLE_REPORT": "1",
                    "LLM_USAGE_MANIFEST": str(local.usage), "LLM_EXECUTION_ID": paths.execution_id,
                    "LLM_RUN_ROLE": "final", "LLM_RUN_ID": paths.run_key, "LLM_COMPANY_NAME": name,
                    "PYTHONPATH": os.pathsep.join([str(PACKAGE / "runtime"), str(REPO / "src")])}
                save(paths.execution_dir / "one_team_commands.json", [{"stage": step, "command": command} for step, command in commands])
                for step, command in commands:
                    def execute(step=step, command=command):
                        log = paths.execution_dir / "logs" / f"{step}.log"
                        log.parent.mkdir(parents=True, exist_ok=True)
                        with log.open("a") as stream:
                            result = subprocess.run(command, cwd=REPO, env=env, stdout=stream, stderr=subprocess.STDOUT, timeout=900)
                        if result.returncode:
                            raise RuntimeError(f"One-team {name}/{step} failed: {log}")
                    stage(name, step, execute)
                if read(paths.writer_dir / "writer_run_status.json")["status"] != "success":
                    raise RuntimeError("One-team Writer did not complete")
                flow._publish_final_report(paths)
                usage = summarize_execution_usage(local.usage, execution_id=paths.execution_id, pipeline_completed=True,
                    expected_logical_calls_by_role={"target": 1, "peer": 1, "final": 3})
                save(paths.execution_dir / "one_team_manifest.json", {"status": "success", "protocol": PROTOCOL,
                    "specification": spec, "models": manifest["models"], "published_report": str(paths.published_report),
                    "llm_usage": usage, "source_preparation": str(manifest_path(args))})
                state["completed"].append({"company": name, "condition": "one_team", "report": str(paths.published_report)})
                update(reports_completed=len(state["completed"]))
            update(state="success", completed_at=now(), current_stage="complete")
        except Exception as exc:
            update(state="failed", error={"type": type(exc).__name__, "message": str(exc)}, stopped_at=now())
            raise


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("prepare", "check", "run", "resume"))
    parser.add_argument("--model", default="gpt-5.6-luna")
    parser.add_argument("--downstream-model", default=None, help="Comparison, Strategy and Writer model; defaults to --model")
    parser.add_argument("--companies", default="all", help="all or comma-separated target company names")
    parser.add_argument("--replicate", type=int, default=1)
    parser.add_argument("--run-id", default=None)
    args = parser.parse_args(argv)
    args.downstream_model = args.downstream_model or args.model
    if args.replicate < 1: parser.error("replicate must be positive")
    if args.action == "prepare": prepare(args)
    elif args.action == "check":
        manifest = check(args)
        print(f"Verified {manifest['reports_planned']} one-team reports; paid calls: 0")
    else: run(args, resume=args.action == "resume")


if __name__ == "__main__": main()
