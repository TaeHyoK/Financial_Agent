#!/usr/bin/env python3
"""Collect fixed DART, market, and News inputs without making any LLM call."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
import traceback
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import yaml


SCRIPT_DIR = Path(__file__).resolve().parent
MANIFEST_PATH = SCRIPT_DIR / "collection_manifest.json"


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temporary.replace(path)


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def one_year_earlier(value: date) -> date:
    try:
        return value.replace(year=value.year - 1)
    except ValueError:
        return value.replace(year=value.year - 1, day=28)


def safe_name(value: str) -> str:
    result = value.strip()
    for character in '\\/:*?"<>|':
        result = result.replace(character, "_")
    return "_".join(result.split())


def load_required_environment(env_file: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    for raw_line in env_file.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        values[key.strip()] = value.strip().strip('"').strip("'")
    dart_key = values.get("DART_API_KEY", "").strip()
    if not dart_key:
        raise RuntimeError(f"DART_API_KEY is missing from {env_file}")
    environment = os.environ.copy()
    environment["DART_API_KEY"] = dart_key
    # Fail closed: even an accidental LLM phase cannot reach a paid endpoint.
    environment["OPENAI_API_KEY"] = ""
    environment["OPENAI_BASE_URL"] = "http://127.0.0.1:9"
    environment["OPENAI_MODEL"] = ""
    environment["NEWS_AGENT_LLM_MODEL"] = ""
    environment["TOKENIZERS_PARALLELISM"] = "false"
    return environment


def entity_paths(workspace: Path, target_name: str, entity: dict[str, Any], role: str, report_date: str) -> dict[str, Path]:
    snapshot_root = workspace / "collected_data" / safe_name(target_name) / "snapshot"
    output_root = snapshot_root if role == "target" else snapshot_root / safe_name(target_name) / "비교기업"
    company_root = output_root / safe_name(entity["company_name"])
    financial_dir = company_root / "Financial" / report_date
    market_dir = company_root / "Y_Finance" / report_date
    news_dir = company_root / "News" / report_date
    cutoff = (datetime.strptime(report_date, "%Y%m%d").date() - timedelta(days=1)).strftime("%Y%m%d")
    return {
        "snapshot_root": snapshot_root,
        "output_root": output_root,
        "company_root": company_root,
        "financial_dir": financial_dir,
        "market_dir": market_dir,
        "news_dir": news_dir,
        "news_context": news_dir / "artifacts" / "reports" / "packs" / f"{safe_name(entity['company_name'])}_{cutoff}" / "report_context.json",
        "news_export_dir": news_dir / "context_exports",
        "news_month_dir": news_dir / "context_exports" / "month",
    }


def prepare_configs(
    workspace: Path,
    repo: Path,
    target_name: str,
    entity: dict[str, Any],
    role: str,
    report_date: str,
    paths: dict[str, Path],
) -> tuple[Path, Path, dict[str, Any]]:
    selected = datetime.strptime(report_date, "%Y%m%d").date()
    start = one_year_earlier(selected)
    end = selected - timedelta(days=1)
    config_dir = workspace / "run_config" / "companies" / safe_name(target_name)
    config_dir.mkdir(parents=True, exist_ok=True)
    stem = f"{role}_{safe_name(entity['company_name'])}"
    company_config = config_dir / f"{stem}.json"
    company_payload = {
        "company_code": entity["corp_code"],
        "corp_code": entity["corp_code"],
        "stock_code": entity["stock_code"],
        "company_name": entity["company_name"],
        "ticker": entity["ticker"],
        "report_type": "latest filing available before report date",
        "date_range": f"{start:%Y%m%d}-{end:%Y%m%d}",
        "selected_date": report_date,
        "llm_model": "gpt-5.4",
        "max_retries": 2,
    }
    write_json(company_config, company_payload)

    base_news_config = yaml.safe_load((repo / "configs" / "news_default.yaml").read_text(encoding="utf-8"))
    base_news_config["data_root"] = str(paths["news_dir"] / "artifacts")
    base_news_config["inputs_root"] = str(paths["news_dir"] / "inputs")
    base_news_config.setdefault("news", {})["collection_days"] = (end - start).days + 1
    news_config = config_dir / f"{stem}_news.yaml"
    news_config.write_text(yaml.safe_dump(base_news_config, allow_unicode=True, sort_keys=False), encoding="utf-8")
    return company_config, news_config, company_payload


def common_news_arguments(
    entity: dict[str, Any],
    report_date: str,
    company_config: dict[str, Any],
    news_config: Path,
    paths: dict[str, Path],
    env_file: Path,
) -> list[str]:
    selected = datetime.strptime(report_date, "%Y%m%d").date()
    cutoff = selected - timedelta(days=1)
    start_text, end_text = company_config["date_range"].split("-", 1)
    collection_days = (datetime.strptime(end_text, "%Y%m%d").date() - datetime.strptime(start_text, "%Y%m%d").date()).days + 1
    return [
        "--collect-date", cutoff.isoformat(),
        "--company-id", entity["corp_code"],
        "--company-name", entity["company_name"],
        "--ticker", entity["ticker"],
        "--corp-code", entity["corp_code"],
        "--as-of-date", selected.isoformat(),
        "--collection-days", str(collection_days),
        "--granularity", "month",
        "--period-count", "12",
        "--raw-period-count", "12",
        "--min-mention-count", "1",
        "--context-export-dir", str(paths["news_export_dir"]),
        "--analysis-output-dir", str(paths["news_dir"] / "output"),
        "--dart-lightweight", str(paths["financial_dir"] / "dart_lightweight.json"),
        "--market-summary", str(paths["market_dir"] / "market_summary.json"),
        "--env-path", str(env_file),
        "--config", str(news_config),
        "--split-by-period",
        "--llm-model", "gpt-5.6-luna",
        "--analysis-model", "gpt-5.4",
    ]


def build_steps(
    repo: Path,
    entity: dict[str, Any],
    report_date: str,
    company_config_path: Path,
    company_config: dict[str, Any],
    news_config: Path,
    paths: dict[str, Path],
    env_file: Path,
) -> list[dict[str, Any]]:
    python = sys.executable
    start_text, end_text = company_config["date_range"].split("-", 1)
    news_args = common_news_arguments(entity, report_date, company_config, news_config, paths, env_file)
    steps = [
        {
            "name": "yfinance_layer_1",
            "command": [
                python,
                str(repo / "src" / "Agent_Team" / "YFinance_Agent" / "main.py"),
                "--input", str(company_config_path),
                "--output-dir", str(paths["market_dir"]),
                "--start-date", start_text,
                "--end-date", end_text,
                "--selected-date", report_date,
                "--kospi-ticker", "^KS11",
                "--fx-ticker", "KRW=X",
            ],
            "outputs": [
                paths["market_dir"] / "market_full_dataset.json",
                paths["market_dir"] / "market_full_dataset.csv",
                paths["market_dir"] / "manifest.json",
                paths["market_dir"] / f"market_summary_{report_date}.json",
                paths["market_dir"] / "valuation_snapshot.json",
            ],
        },
        {
            "name": "financial_layer_1",
            "command": [
                python,
                "-m", "Agent_Team.Financial_Agent.main",
                "--input", str(company_config_path),
                "--output-dir", str(paths["financial_dir"]),
                "--env-file", str(env_file),
            ],
            "outputs": [
                paths["financial_dir"] / "dart_master.json",
                paths["financial_dir"] / "dart_main.json",
                paths["financial_dir"] / "dart_lightweight.json",
                paths["financial_dir"] / "financial_subdata.json",
            ],
        },
    ]
    if entity.get("reuse_news_report_context"):
        steps.append({
            "name": "news_collect_reuse",
            "command": [],
            "outputs": [paths["news_context"]],
            "reuse_source": Path(entity["reuse_news_report_context"]),
        })
    else:
        steps.append({
            "name": "news_collect",
            "command": [python, "-m", "Agent_Team.News_Agent.cli", "--phase", "collect", *news_args],
            "outputs": [paths["news_context"]],
        })
    steps.append({
        "name": "news_export",
        "command": [python, "-m", "Agent_Team.News_Agent.cli", "--phase", "export", *news_args],
        "outputs": [
            paths["news_month_dir"] / "selected_articles.json",
            paths["news_month_dir"] / "llm_summary_request.json",
            paths["news_month_dir"] / "context_export_manifest.json",
        ],
    })
    for step in steps:
        if "--use-llm" in step.get("command", []):
            raise RuntimeError("LLM execution flag detected in a data-only command")
        if step["name"] in {"news_llm", "news_analysis", "financial_analyst", "yfinance_report"}:
            raise RuntimeError(f"LLM-dependent step detected: {step['name']}")
    return steps


def outputs_valid(paths: list[Path]) -> bool:
    return bool(paths) and all(path.is_file() and path.stat().st_size > 0 for path in paths)


def run_subprocess(command: list[str], *, repo: Path, environment: dict[str, str], log_path: Path) -> int:
    log_path.parent.mkdir(parents=True, exist_ok=True)
    with log_path.open("a", encoding="utf-8") as log:
        log.write(f"\n[{utc_now()}] command: {json.dumps(command, ensure_ascii=False)}\n")
        log.flush()
        completed = subprocess.run(
            command,
            cwd=repo,
            env=environment,
            stdout=log,
            stderr=subprocess.STDOUT,
            text=True,
            check=False,
        )
        log.write(f"[{utc_now()}] return_code={completed.returncode}\n")
        return completed.returncode


def materialize_reused_news(source: Path, destination: Path) -> dict[str, str]:
    if not source.is_file() or source.stat().st_size == 0:
        raise FileNotFoundError(f"Reusable News context is unavailable: {source}")
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, destination)
    source_hash = sha256(source)
    if sha256(destination) != source_hash:
        raise RuntimeError(f"News context copy hash mismatch: {source} -> {destination}")
    return {"source": str(source), "destination": str(destination), "sha256": source_hash}


def verify_references(workspace: Path, manifest: dict[str, Any]) -> None:
    for company in manifest["companies"]:
        reference = company["reference_report"]
        path = workspace / reference["file"]
        if not path.is_file():
            raise FileNotFoundError(path)
        if sha256(path) != reference["sha256"]:
            raise RuntimeError(f"Reference report hash mismatch: {path}")


def planned_entities(manifest: dict[str, Any]):
    for company in manifest["companies"]:
        target_name = company["target"]["company_name"]
        yield target_name, "target", company["target"], company["report_date"]
        yield target_name, "peer", company["peer"], company["report_date"]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, default=MANIFEST_PATH)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--force", action="store_true", help="Run steps again even when validated outputs exist")
    args = parser.parse_args()

    manifest = read_json(args.manifest.resolve())
    workspace = Path(manifest["workspace"]).resolve()
    repo = Path(manifest["repository"]["path"]).resolve()
    env_file = Path(manifest["environment_file"]).resolve()
    status_path = workspace / "status" / "data_collection_status.json"
    worker_pid_path = workspace / "status" / "data_collection_worker.pid"
    worker_pid_path.write_text(f"{os.getpid()}\n", encoding="utf-8")

    if not os.access(workspace, os.W_OK):
        raise PermissionError(f"Workspace is not writable: {workspace}")
    actual_commit = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=repo, check=True, capture_output=True, text=True
    ).stdout.strip()
    if actual_commit != manifest["repository"]["commit"]:
        raise RuntimeError(f"Repository commit changed: expected {manifest['repository']['commit']}, got {actual_commit}")
    verify_references(workspace, manifest)
    environment = load_required_environment(env_file)
    environment["PYTHONPATH"] = str(repo / "src") + os.pathsep + environment.get("PYTHONPATH", "")

    status: dict[str, Any] = {
        "state": "dry_run" if args.dry_run else "running",
        "started_at": utc_now(),
        "worker_pid": os.getpid(),
        "repository_commit": actual_commit,
        "llm_calls_allowed": False,
        "entities_total": len(manifest["companies"]) * 2,
        "entities": {},
    }
    write_json(status_path, status)
    failures: list[str] = []

    for target_name, role, entity, report_date in planned_entities(manifest):
        key = f"{target_name}:{role}:{entity['company_name']}"
        paths = entity_paths(workspace, target_name, entity, role, report_date)
        company_config_path, news_config, company_config = prepare_configs(
            workspace, repo, target_name, entity, role, report_date, paths
        )
        steps = build_steps(repo, entity, report_date, company_config_path, company_config, news_config, paths, env_file)
        record = {
            "target_company": target_name,
            "role": role,
            "company_name": entity["company_name"],
            "report_date": report_date,
            "date_range": company_config["date_range"],
            "output_root": str(paths["output_root"]),
            "state": "planned" if args.dry_run else "running",
            "steps": {},
        }
        status["entities"][key] = record
        write_json(status_path, status)

        if args.dry_run:
            for step in steps:
                record["steps"][step["name"]] = {
                    "state": "planned",
                    "command": step.get("command", []),
                    "outputs": [str(path) for path in step["outputs"]],
                    "reuse_source": str(step.get("reuse_source", "")),
                }
            continue

        entity_failed = False
        for step in steps:
            step_name = step["name"]
            step_record = {
                "state": "running",
                "started_at": utc_now(),
                "outputs": [str(path) for path in step["outputs"]],
            }
            record["steps"][step_name] = step_record
            write_json(status_path, status)
            try:
                if outputs_valid(step["outputs"]) and not args.force:
                    step_record["state"] = "reused_valid_output"
                    step_record["completed_at"] = utc_now()
                    write_json(status_path, status)
                    continue
                if step_name == "news_collect_reuse":
                    step_record["reuse"] = materialize_reused_news(step["reuse_source"], step["outputs"][0])
                    return_code = 0
                else:
                    log_path = workspace / "logs" / "data_collection" / f"{safe_name(target_name)}_{role}_{safe_name(entity['company_name'])}_{step_name}.log"
                    step_record["log"] = str(log_path)
                    return_code = run_subprocess(step["command"], repo=repo, environment=environment, log_path=log_path)
                    if step_name == "yfinance_layer_1" and return_code == 0:
                        dated = paths["market_dir"] / f"market_summary_{report_date}.json"
                        alias = paths["market_dir"] / "market_summary.json"
                        if dated.is_file():
                            shutil.copy2(dated, alias)
                valid = outputs_valid(step["outputs"])
                step_record.update({
                    "state": "success" if return_code == 0 and valid else "failed",
                    "return_code": return_code,
                    "outputs_valid": valid,
                    "completed_at": utc_now(),
                })
                if step_record["state"] == "failed":
                    entity_failed = True
                    failures.append(f"{key}:{step_name}")
                    break
            except Exception as exc:
                step_record.update({
                    "state": "failed",
                    "error": f"{type(exc).__name__}: {exc}",
                    "traceback": traceback.format_exc(),
                    "completed_at": utc_now(),
                })
                entity_failed = True
                failures.append(f"{key}:{step_name}")
                break
            finally:
                write_json(status_path, status)

        record["state"] = "failed" if entity_failed else "success"
        record["completed_at"] = utc_now()
        write_json(status_path, status)

    status["completed_at"] = utc_now()
    if args.dry_run:
        status["state"] = "dry_run_complete"
    elif failures:
        status["state"] = "completed_with_failures"
        status["failures"] = failures
    else:
        status["state"] = "success"
    write_json(status_path, status)
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
