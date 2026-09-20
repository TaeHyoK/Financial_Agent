"""Top-level condition/replicate/company experiment scheduler."""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
import os
import shutil
import sys
import traceback
from dataclasses import asdict
from pathlib import Path
from typing import Any

from .config import (
    ABLATION_ROOT,
    COMPANY_BY_KEY,
    COMPANY_BY_NAME,
    COMPANY_SPECS,
    CONDITIONS,
    DEFAULT_ENV_FILE,
    DEFAULT_MODEL,
    DEFAULT_REPLICATES,
    DEFAULT_SEED,
    DEFAULT_SOURCE_ROOTS,
    FINAL_ROOT,
    FINAL_SRC,
    RANDOM_NEWS_COUNT,
    RUNTIME_COMPAT_ROOT,
    CompanySpec,
)
from .snapshots import (
    FrozenSource,
    locate_frozen_source,
    prepare_random_snapshot,
    random_snapshot_plan,
)
from .protocol import PROTOCOLS, protocol_date_mode, resolve_selected_date
from .source_lock import lock_or_verify_source
from .annual_random import ANNUAL_RANDOM_PROTOCOL, annual_random_plan, prepare_annual_random_snapshot, require_monthly_news_source
from .unified import UNIFIED_PROTOCOL, run_unified_domain_team, unified_preflight_plan
from .utils import (
    SuiteLock,
    load_json,
    python_env,
    run_logged,
    safe_label,
    tail_text,
    utc_now,
    write_json,
)


if str(FINAL_SRC) not in sys.path:
    sys.path.insert(0, str(FINAL_SRC))

from orchestration.config import load_run_config, peer_output_root  # noqa: E402
from orchestration.end_to_end_loop import materialize_reused_domain_snapshot  # noqa: E402
from orchestration.paths import resolve_run_paths  # noqa: E402
from orchestration.usage_summary import summarize_execution_usage  # noqa: E402


DOWNSTREAM_STAGE_NAMES = (
    "peer_comparison_dataset",
    "peer_comparison_analysis",
    "strategy",
    "visualization_catalog",
    "writer_generation",
    "visualization",
    "writer_render",
)


class AblationSuiteRunner:
    def __init__(self, args: argparse.Namespace) -> None:
        self.args = args
        self.suite_dir = (ABLATION_ROOT / "experiments" / args.suite_id).resolve()
        self.output_base = self.suite_dir / "outputs"
        self.log_base = self.suite_dir / "logs"
        self.state_base = self.suite_dir / "state"
        self.snapshot_base = self.suite_dir / "snapshots"
        self.plan_base = self.suite_dir / "plans"
        self.source_roots = tuple(Path(path).expanduser().resolve() for path in args.source_root)
        self.conditions = parse_conditions(args.conditions)
        self.companies = parse_companies(args.companies)
        self.failures: list[dict[str, Any]] = []
        self.source_content_hashes: dict[str, str] = {}
        digest = hashlib.sha256()
        for root in (FINAL_SRC, ABLATION_ROOT / "ablation_suite", RUNTIME_COMPAT_ROOT):
            for path in sorted(root.rglob("*")):
                if path.is_file() and path.suffix in {".py", ".md", ".json", ".yaml"}:
                    digest.update(str(path.relative_to(root)).encode("utf-8"))
                    digest.update(path.read_bytes())
        digest.update((FINAL_ROOT / "configs" / "news_default.yaml").read_bytes())
        self.generation_code_hash = digest.hexdigest()

    def _selected_date(self, company: CompanySpec) -> str:
        return resolve_selected_date(company.key, self.args.selected_date)

    def _task_protocol(self, condition: str, company: CompanySpec) -> dict[str, Any]:
        return {
            "protocol": PROTOCOLS[self.args.protocol]["version"],
            "condition": condition,
            "selected_date": self._selected_date(company),
            "news_window": self.args.news_window,
            "decision_horizon_profile": self.args.decision_horizon_profile,
            "model": self.args.model,
            "llm_timeout": self.args.llm_timeout,
            "random_seed": self.args.seed,
            "news_count": self.args.random_news_count,
            "source_roots": [str(path) for path in self.source_roots],
            "generation_code_hash": self.generation_code_hash,
            "source_content_sha256": self.source_content_hashes.get(company.key),
        }

    def _task_fingerprint(self, condition: str, company: CompanySpec) -> str:
        payload = json.dumps(self._task_protocol(condition, company), sort_keys=True)
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()

    def _locked_source(self, company: CompanySpec) -> FrozenSource:
        source = locate_frozen_source(company.name, self._selected_date(company), self.source_roots)
        lock_path = self.snapshot_base / "common_sources" / f"{company.key}.json"
        if not lock_path.exists() and any(self.state_base.glob(f"*/replicate_*/{company.key}.json")):
            raise ValueError(f"Common source lock is missing for an existing task; use a new suite: {lock_path}")
        self.source_content_hashes[company.key] = lock_or_verify_source(source, lock_path)
        return source

    def build_plan(self) -> dict[str, Any]:
        """Inspect local prerequisites without collecting data or calling an LLM."""
        companies = []
        for company in self.companies:
            selected_date = self._selected_date(company)
            try:
                source = locate_frozen_source(company.name, selected_date, self.source_roots)
                snapshot = {"status": "available", "root": str(source.root)}
            except FileNotFoundError as exc:
                snapshot = {"status": "missing", "reason": str(exc)}
            companies.append({
                "company": company.name,
                "company_key": company.key,
                "selected_date": selected_date,
                "frozen_source": snapshot,
                "conditions": [self._task_protocol(condition, company) for condition in self.conditions],
            })
        return {
            "suite_id": self.args.suite_id,
            "mode": "plan_only",
            "protocol": PROTOCOLS[self.args.protocol],
            "information_cutoff_policy": "strictly_before_selected_date; current FINAL excludes report-day data",
            "scheduled_reports": len(self.companies) * len(self.conditions) * (
                self.args.replicates - self.args.replicate_start + 1
            ),
            "companies": companies,
            "remaining_contract_work": [
                "collect and freeze matching annual target/peer source data before running experiments",
                "random summary regeneration and no_subdata valuation isolation pass offline checks; live report quality still needs validation",
                "unified annual inputs pass offline parity checks; live handoff and output completeness still need validation",
            ],
        }

    def run(self) -> int:
        if self.args.plan_only:
            print(json.dumps(self.build_plan(), ensure_ascii=False, indent=2))
            return 0
        self.suite_dir.mkdir(parents=True, exist_ok=True)
        with SuiteLock(self.suite_dir / ".suite.lock"):
            manifest_path = self.suite_dir / "suite_manifest.json"
            if manifest_path.is_file():
                previous = load_json(manifest_path).get("protocol") or {}
                expected = PROTOCOLS[self.args.protocol]["version"]
                if previous.get("protocol_version") != expected:
                    raise ValueError(
                        "This suite belongs to a different or unversioned protocol. "
                        "Use a new --suite-id to preserve existing results."
                    )
            # Resolve every selected company's common inputs before any paid task.
            for company in self.companies:
                self._locked_source(company)
            self._write_suite_manifest(status="running")
            for condition in self.conditions:
                for replicate in range(self.args.replicate_start, self.args.replicates + 1):
                    for company in self.companies:
                        try:
                            self._run_task(condition, replicate, company)
                        except Exception as exc:
                            failure = {
                                "condition": condition,
                                "replicate": replicate,
                                "company": company.name,
                                "error_type": type(exc).__name__,
                                "error": str(exc),
                            }
                            self.failures.append(failure)
                            print(
                                f"[FAILED] {condition}/r{replicate:02d}/{company.name}: {exc}",
                                flush=True,
                            )
                            traceback.print_exc()
                            if self.args.fail_fast:
                                self._write_suite_manifest(status="failed")
                                return 1
            status = "dry_run" if self.args.dry_run else ("failed" if self.failures else "success")
            self._write_suite_manifest(status=status)
            return 1 if self.failures else 0

    def _run_task(self, condition: str, replicate: int, company: CompanySpec) -> None:
        # Verify before the resume shortcut as well as before a new execution.
        source = self._locked_source(company)
        state_path = self._task_state_path(condition, replicate, company)
        output_root = self._output_root(condition, replicate)
        report_path = output_root / company.name / f"report_{safe_label(company.name)}.html"
        fingerprint = self._task_fingerprint(condition, company)
        if state_path.is_file():
            previous = load_json(state_path)
            if previous.get("protocol_fingerprint") != fingerprint:
                raise ValueError("Task settings changed; use a new --suite-id to preserve existing results.")
        if self.args.resume and _successful_task(
            state_path, report_path, dry_run=self.args.dry_run,
            expected_fingerprint=fingerprint,
        ):
            print(f"[SKIP] {condition}/r{replicate:02d}/{company.name}", flush=True)
            return
        started = utc_now()
        state = {
            "status": "running",
            "condition": condition,
            "replicate": replicate,
            "company": asdict(company),
            "selected_date": source.selected_date,
            "protocol_fingerprint": fingerprint,
            "task_protocol": self._task_protocol(condition, company),
            "source_root": str(source.root),
            "source_full_manifest": str(source.full_manifest),
            "output_root": str(output_root),
            "started_at": started,
        }
        write_json(state_path, state)
        print(f"[START] {condition}/r{replicate:02d}/{company.name}", flush=True)
        try:
            if condition == "unified_domain_team":
                result = self._run_unified(condition, replicate, company, source)
            else:
                result = self._run_final_condition(condition, replicate, company, source)
            self._locked_source(company)  # Reject source edits made while this task ran.
            state.update(result)
            state["status"] = "dry_run" if self.args.dry_run else "success"
            state["completed_at"] = utc_now()
            write_json(state_path, state)
            print(f"[DONE] {condition}/r{replicate:02d}/{company.name}", flush=True)
        except Exception as exc:
            state.update(
                {
                    "status": "failed",
                    "completed_at": utc_now(),
                    "error_type": type(exc).__name__,
                    "error": str(exc),
                }
            )
            write_json(state_path, state)
            raise

    def _run_final_condition(
        self,
        condition: str,
        replicate: int,
        company: CompanySpec,
        source: FrozenSource,
    ) -> dict[str, Any]:
        reuse_root = source.root
        if self.args.protocol == "annual":
            require_monthly_news_source(source)
        random_plan_path = ""
        if condition == "random_news":
            snapshot_root = self.snapshot_base / "random_news" / company.key / "source_root"
            if self.args.protocol == "annual":
                snapshot_root = self.snapshot_base / "random_news" / f"replicate_{replicate:02d}" / company.key / "source_root"
                options = {"base_seed": self.args.seed, "replicate": replicate,
                           "sample_size": self.args.random_news_count, "code_hash": self.generation_code_hash}
                if self.args.dry_run:
                    plan = annual_random_plan(source, **options)
                    random_plan = self.plan_base / "random_news" / f"replicate_{replicate:02d}" / f"{company.key}.json"
                    write_json(random_plan, plan)
                    random_plan_path = str(random_plan)
                else:
                    prepare_annual_random_snapshot(source, destination_root=snapshot_root, model=self.args.model,
                                                   env_file=self.args.env_file, **options)
            elif self.args.dry_run:
                plan = random_snapshot_plan(
                    source,
                    base_seed=self.args.seed,
                    sample_size=self.args.random_news_count,
                )
                random_plan = self.plan_base / "random_news" / f"{company.key}.json"
                write_json(random_plan, plan)
                random_plan_path = str(random_plan)
            else:
                prepare_random_snapshot(
                    source,
                    destination_root=snapshot_root,
                    base_seed=self.args.seed,
                    model=self.args.model,
                    sample_size=self.args.random_news_count,
                )
            reuse_root = snapshot_root

        command = self._full_command(
            condition=condition,
            replicate=replicate,
            company=company,
            source=source,
            reuse_root=reuse_root,
            dry_run=self.args.dry_run,
        )
        log_path = self._task_log_path(condition, replicate, company)
        code = run_logged(
            command,
            log_path=log_path,
            cwd=FINAL_ROOT,
            env=_ablation_python_env(),
        )
        if code != 0:
            raise RuntimeError(f"FINAL pipeline returned {code}; log tail:\n{tail_text(log_path)}")
        output_root = self._output_root(condition, replicate)
        full_manifest = (
            output_root
            / company.name
            / "runs"
            / source.selected_date
            / "full_pipeline_manifest.json"
        )
        payload = load_json(full_manifest)
        expected = "dry_run" if self.args.dry_run else "success"
        if payload.get("status") != expected:
            raise RuntimeError(f"Unexpected FINAL manifest status: {payload.get('status')} != {expected}")
        preprocessing_usage = ""
        if condition == "random_news" and self.args.protocol == "annual" and not self.args.dry_run:
            preprocessing_usage = str(reuse_root / "random_news_preprocessing_usage.json")
            payload.setdefault("outputs", {})["random_news_preprocessing_usage_summary"] = preprocessing_usage
            write_json(full_manifest, payload)
        return {
            "command": command,
            "log_path": str(log_path),
            "full_pipeline_manifest": str(full_manifest),
            "writer_report": str((payload.get("outputs") or {}).get("writer_report") or ""),
            "random_news_plan": random_plan_path,
            "random_news_snapshot": str(reuse_root) if condition == "random_news" else "",
            "random_news_preprocessing_usage_summary": preprocessing_usage,
        }

    def _run_unified(
        self,
        condition: str,
        replicate: int,
        company: CompanySpec,
        source: FrozenSource,
    ) -> dict[str, Any]:
        if self.args.protocol == "annual":
            require_monthly_news_source(source)
        output_root = self._output_root(condition, replicate)
        scaffold_command = self._full_command(
            condition=condition,
            replicate=replicate,
            company=company,
            source=source,
            reuse_root=source.root,
            dry_run=True,
        )
        task_log = self._task_log_path(condition, replicate, company)
        code = run_logged(
            scaffold_command,
            log_path=task_log,
            cwd=FINAL_ROOT,
            env=_ablation_python_env(),
        )
        if code != 0:
            raise RuntimeError(f"Unified scaffold failed ({code}); log tail:\n{tail_text(task_log)}")
        full_manifest_path = (
            output_root
            / company.name
            / "runs"
            / source.selected_date
            / "full_pipeline_manifest.json"
        )
        scaffold = load_json(full_manifest_path)
        if self.args.dry_run:
            entity_plans = []
            for role, config_path, source_entity_root in (
                ("target", source.target_config, source.root),
                (
                    "peer",
                    source.peer_config,
                    peer_output_root(source.root, company.name),
                ),
            ):
                config = load_run_config(config_path)
                entity_plans.append(
                    unified_preflight_plan(
                        run_config=config,
                        paths=resolve_run_paths(config, source_entity_root),
                        model=self.args.model,
                        role=role,
                    )
                )
            plan_path = self.plan_base / "unified_domain_team" / f"r{replicate:02d}_{company.key}.json"
            write_json(
                plan_path,
                {
                    "protocol": UNIFIED_PROTOCOL,
                    "semantic_calls": ["target: one integrated call", "peer: one integrated call"],
                    "skipped_final_stages": ["target_domain_pipeline", "peer_domain_pipeline"],
                    "unchanged_downstream_stages": list(DOWNSTREAM_STAGE_NAMES),
                    "scaffold_manifest": str(full_manifest_path),
                    "prior_domain_agent_reports_used": False,
                    "entity_preflight": entity_plans,
                },
            )
            return {
                "command": scaffold_command,
                "log_path": str(task_log),
                "full_pipeline_manifest": str(full_manifest_path),
                "unified_plan": str(plan_path),
            }

        completed = False
        try:
            result = self._execute_unified_pipeline(
                condition=condition, replicate=replicate, company=company, source=source,
                scaffold=scaffold, output_root=output_root, full_manifest_path=full_manifest_path,
                task_log=task_log,
            )
            result["command"] = scaffold_command
            completed = True
            return result
        finally:
            original_error = sys.exc_info()[1]
            try:
                self._close_unified_usage(scaffold, full_manifest_path, completed=completed,
                                          error=original_error)
            except Exception as accounting_error:
                if original_error is None:
                    raise
                # A disk/telemetry failure must not hide the original pipeline error.
                print(f"[USAGE_ERROR] {accounting_error}", file=sys.stderr, flush=True)

    def _close_unified_usage(self, scaffold, full_manifest_path, *, completed, error=None):
        outputs = scaffold.get("outputs") or {}
        usage_path = str(outputs.get("llm_usage_manifest") or "").strip()
        execution_id = str(scaffold.get("execution_id") or "").strip()
        if not usage_path or not execution_id:
            raise RuntimeError("Unified scaffold is missing execution/usage telemetry paths")
        usage = summarize_execution_usage(
            usage_path, execution_id=execution_id, pipeline_completed=completed,
            expected_logical_calls_by_role={"target": 1, "peer": 1, "final": 3},
        )
        self._write_unified_usage_summaries(scaffold, full_manifest_path, usage)
        if not completed:
            manifest = load_json(full_manifest_path)
            manifest.update(status="failed", pipeline_completed=False, llm_usage=usage)
            manifest["error"] = str(error) if error else "Unified pipeline did not complete"
            write_json(full_manifest_path, manifest)

    def _execute_unified_pipeline(
        self, *, condition, replicate, company, source, scaffold, output_root,
        full_manifest_path, task_log,
    ):
        target_config = Path((scaffold.get("outputs") or {}).get("target_config") or "")
        peer_config = Path((scaffold.get("outputs") or {}).get("peer_config") or "")
        usage_manifest_value = str(
            (scaffold.get("outputs") or {}).get("llm_usage_manifest") or ""
        ).strip()
        usage_manifest = Path(usage_manifest_value)
        execution_id = str(scaffold.get("execution_id") or "")
        if not execution_id or not usage_manifest_value:
            raise RuntimeError("Unified scaffold is missing execution/usage telemetry paths")
        entity_manifests = []
        entity_specs = [
            ("target", target_config, source.root, output_root),
            (
                "peer",
                peer_config,
                peer_output_root(source.root, company.name),
                peer_output_root(output_root, company.name),
            ),
        ]
        for role, config_path, source_entity_root, destination_entity_root in entity_specs:
            config = load_run_config(config_path)
            paths = resolve_run_paths(config, destination_entity_root)
            materialize_reused_domain_snapshot(
                run_config=config,
                source_root=source_entity_root,
                destination_paths=paths,
                expected_news_model=self.args.model,
            )
            entity_manifest = run_unified_domain_team(
                run_config=config,
                destination_paths=paths,
                model=self.args.model,
                env_file=self.args.env_file,
                timeout_seconds=self.args.llm_timeout,
                role=role,
                usage_manifest=usage_manifest,
                execution_id=execution_id,
            )
            write_json(
                paths.run_status,
                {
                    "status": "success",
                    "pipeline_completed": True,
                    "protocol": UNIFIED_PROTOCOL,
                    "company_name": config.company_name,
                    "selected_date": config.selected_date,
                },
            )
            entity_manifests.append(entity_manifest)

        downstream_results = self._execute_downstream_stages(
            scaffold=scaffold,
            task_log=task_log,
            company=company,
            replicate=replicate,
        )
        internal_report = output_root / company.name / "Writer" / source.selected_date / "report.html"
        published_report = output_root / company.name / f"report_{safe_label(company.name)}.html"
        if not internal_report.is_file() or internal_report.stat().st_size == 0:
            raise RuntimeError(f"Unified Writer report is missing: {internal_report}")
        published_report.parent.mkdir(parents=True, exist_ok=True)
        temporary_report = published_report.with_name(f".{published_report.name}.tmp.{os.getpid()}")
        shutil.copy2(internal_report, temporary_report)
        os.replace(temporary_report, published_report)
        unified_manifest = (
            output_root
            / company.name
            / "runs"
            / source.selected_date
            / "unified_domain_team_manifest.json"
        )
        usage = summarize_execution_usage(
            usage_manifest,
            execution_id=execution_id,
            pipeline_completed=True,
            expected_logical_calls_by_role={"target": 1, "peer": 1, "final": 3},
        )
        self._write_unified_usage_summaries(scaffold, full_manifest_path, usage)
        unified_payload = {
            "status": "success",
            "protocol": UNIFIED_PROTOCOL,
            "condition": condition,
            "replicate": replicate,
            "company_name": company.name,
            "selected_date": source.selected_date,
            "source_root": str(source.root),
            "model": self.args.model,
            "entity_integrated_calls": entity_manifests,
            "downstream_stages": downstream_results,
            "llm_usage": usage,
            "published_report": str(published_report),
            "completed_at": utc_now(),
        }
        write_json(unified_manifest, unified_payload)
        self._finalize_unified_standard_manifest(
            scaffold=scaffold,
            full_manifest_path=full_manifest_path,
            unified_manifest=unified_manifest,
            entity_manifests=entity_manifests,
            downstream_results=downstream_results,
            published_report=published_report,
            usage=usage,
        )
        return {
            "log_path": str(task_log),
            "full_pipeline_manifest": str(full_manifest_path),
            "unified_manifest": str(unified_manifest),
            "writer_report": str(published_report),
        }

    def _write_unified_usage_summaries(
        self,
        scaffold: dict[str, Any],
        full_manifest_path: Path,
        usage: dict[str, Any],
    ) -> None:
        execution_summary_value = str(
            (scaffold.get("outputs") or {}).get("llm_usage_summary") or ""
        ).strip()
        if not execution_summary_value:
            raise RuntimeError("Unified scaffold is missing llm_usage_summary path")
        execution_summary = Path(execution_summary_value)
        write_json(execution_summary, usage)
        write_json(full_manifest_path.parent / "llm_usage_summary.json", usage)

    def _finalize_unified_standard_manifest(
        self,
        *,
        scaffold: dict[str, Any],
        full_manifest_path: Path,
        unified_manifest: Path,
        entity_manifests: list[dict[str, Any]],
        downstream_results: list[dict[str, Any]],
        published_report: Path,
        usage: dict[str, Any],
    ) -> None:
        """Replace the dry-run scaffold with the actual successful unified execution."""

        manifest = copy.deepcopy(scaffold)
        request = dict(manifest.get("request") or {})
        request["dry_run"] = False
        manifest["request"] = request
        ablation = dict(manifest.get("ablation") or {})
        ablation.update(
            {
                "active": True,
                "domain_ablation_stage": "domain_team_architecture",
                "domain_team_mode": "one_integrated_company_report_after_equal_preprocessing",
                "analysis_report_count_per_company": 1,
                "prior_domain_agent_reports_used": False,
                "domain_boundary_packets_preserved": True,
                "domain_evidence_catalogs_separated": True,
            }
        )
        manifest["ablation"] = ablation
        entity_steps = [
            {
                "name": f"{item.get('role')}_unified_domain_agent",
                "status": "success",
                "protocol": UNIFIED_PROTOCOL,
                "semantic_call_count": item.get("semantic_call_count"),
                "manifest_path": item.get("manifest_path"),
                "evidence_counts": item.get("evidence_counts") or {},
            }
            for item in entity_manifests
        ]
        downstream_steps = [
            {
                **copy.deepcopy(item),
                "status": "success" if item.get("returncode") == 0 else "failed",
            }
            for item in downstream_results
        ]
        manifest["steps"] = [*entity_steps, *downstream_steps]
        outputs = dict(manifest.get("outputs") or {})
        outputs.update(
            {
                "unified_domain_team_manifest": str(unified_manifest),
                "writer_report": str(published_report),
            }
        )
        manifest["outputs"] = outputs
        manifest["validation"] = {
            "status": "pass",
            "entity_integrated_calls": len(entity_manifests),
            "analysis_report_count_per_company": 1,
            "downstream_stage_count": len(downstream_results),
            "published_report": {
                "path": str(published_report),
                "exists": published_report.is_file(),
                "bytes": published_report.stat().st_size if published_report.is_file() else 0,
            },
        }
        manifest["llm_usage"] = usage
        manifest["status"] = "success"
        manifest["completed_at"] = utc_now()
        write_json(full_manifest_path, manifest)
        execution_id = str(manifest.get("execution_id") or "")
        if execution_id:
            execution_manifest = (
                full_manifest_path.parent
                / "executions"
                / execution_id
                / "full_pipeline_manifest.json"
            )
            write_json(execution_manifest, manifest)

    def _execute_downstream_stages(
        self,
        *,
        scaffold: dict[str, Any],
        task_log: Path,
        company: CompanySpec,
        replicate: int,
    ) -> list[dict[str, Any]]:
        steps = {
            str(step.get("name")): step
            for step in scaffold.get("steps") or []
            if isinstance(step, dict)
        }
        usage_manifest = str((scaffold.get("outputs") or {}).get("llm_usage_manifest") or "")
        env = _ablation_python_env(
            {
                "OPENAI_MODEL": self.args.model,
                "NEWS_AGENT_LLM_MODEL": self.args.model,
                "ABLATION_SINGLE_INTEGRATED_REPORT": "1",
                "LLM_USAGE_MANIFEST": usage_manifest,
                "LLM_RUN_ID": str(scaffold.get("run_key") or ""),
                "LLM_RUN_ROLE": "final",
                "LLM_COMPANY_NAME": company.name,
                "LLM_EXECUTION_ID": str(scaffold.get("execution_id") or ""),
            },
        )
        results = []
        for stage_name in DOWNSTREAM_STAGE_NAMES:
            step = steps.get(stage_name)
            if not step:
                raise RuntimeError(f"Unified scaffold does not contain downstream stage {stage_name}")
            command = [str(item) for item in step.get("command") or []]
            stage_log = task_log.with_name(task_log.stem + f".{stage_name}.log")
            code = run_logged(
                command,
                log_path=stage_log,
                cwd=FINAL_ROOT,
                env=env,
                timeout=self.args.final_stage_timeout if stage_name in {
                    "peer_comparison_analysis", "strategy", "writer_generation"
                } else None,
            )
            result = {
                "name": stage_name,
                "command": command,
                "returncode": code,
                "log_path": str(stage_log),
            }
            results.append(result)
            if code != 0:
                raise RuntimeError(
                    f"Unified downstream stage {stage_name} failed ({code}); "
                    f"log tail:\n{tail_text(stage_log)}"
                )
        return results

    def _full_command(
        self,
        *,
        condition: str,
        replicate: int,
        company: CompanySpec,
        source: FrozenSource,
        reuse_root: Path,
        dry_run: bool,
    ) -> list[str]:
        output_root = self._output_root(condition, replicate)
        execution_id = (
            f"{safe_label(self.args.suite_id)}_{condition}_{company.key}_r{replicate:02d}"
        )
        command = [
            sys.executable,
            "-m",
            "orchestration.full_report_pipeline",
            "--company-name",
            company.name,
            "--selected-date",
            self._selected_date(company),
            "--news-window",
            self.args.news_window,
            "--decision-horizon-profile",
            self.args.decision_horizon_profile,
            "--llm-model",
            self.args.model,
            "--llm-timeout",
            str(self.args.llm_timeout),
            "--max-retries",
            "1",
            "--final-stage-timeout",
            str(self.args.final_stage_timeout),
            "--output-root",
            str(output_root),
            "--env-file",
            str(self.args.env_file),
            "--execution-id",
            execution_id,
            "--experiment-name",
            condition,
            "--identity-resolution-from",
            str(source.full_manifest),
            "--reuse-domain-data-from",
            str(Path(reuse_root).resolve()),
            "--no-progress",
        ]
        if company.target_news_query:
            command.extend(["--target-news-query", company.target_news_query])
        if self.args.protocol != "annual":
            command.extend(["--news-event-top-k", "20"])
        if condition == "no_peer":
            command.append("--no-competitor")
        elif condition == "no_subdata":
            command.append("--primary-data-only")
        if dry_run:
            command.append("--dry-run")
        return command

    def _output_root(self, condition: str, replicate: int) -> Path:
        return self.output_base / condition / f"replicate_{replicate:02d}"

    def _task_log_path(self, condition: str, replicate: int, company: CompanySpec) -> Path:
        return self.log_base / condition / f"replicate_{replicate:02d}" / f"{company.key}.log"

    def _task_state_path(self, condition: str, replicate: int, company: CompanySpec) -> Path:
        return self.state_base / condition / f"replicate_{replicate:02d}" / f"{company.key}.json"

    def _write_suite_manifest(self, *, status: str) -> None:
        tasks = []
        for path in sorted(self.state_base.glob("**/*.json")) if self.state_base.exists() else []:
            try:
                tasks.append(load_json(path))
            except (OSError, ValueError):
                continue
        counts: dict[str, int] = {}
        for task in tasks:
            key = str(task.get("status") or "unknown")
            counts[key] = counts.get(key, 0) + 1
        write_json(
            self.suite_dir / "suite_manifest.json",
            {
                "suite_id": self.args.suite_id,
                "status": status,
                "updated_at": utc_now(),
                "protocol": {
                    "conditions": list(self.conditions),
                    "companies": [asdict(company) for company in self.companies],
                    "selected_date": self.args.selected_date,
                    "selected_dates_by_company": {
                        company.key: self._selected_date(company) for company in self.companies
                    },
                    "protocol_version": PROTOCOLS[self.args.protocol]["version"],
                    "news_window": self.args.news_window,
                    "news_count": 20,
                    "decision_horizon": PROTOCOLS[self.args.protocol]["decision_horizon"],
                    "decision_horizon_profile": self.args.decision_horizon_profile,
                    "model": self.args.model,
                    "replicate_start": self.args.replicate_start,
                    "replicate_end": self.args.replicates,
                    "scheduled_replicates": (
                        self.args.replicates - self.args.replicate_start + 1
                    ),
                    "replicates": self.args.replicates,
                    "random_seed": self.args.seed,
                    "random_unit": "semantic_event_cluster",
                    "random_selection_scope": "weekly_events_and_final_raw" if self.args.protocol == "annual" else "final_raw_news_20_only",
                    "random_summaries": "regenerated_monthly" if self.args.protocol == "annual" else "unchanged_full_snapshot",
                    "random_sample_reused_across_replicates": self.args.protocol != "annual",
                    "random_news_protocol": ANNUAL_RANDOM_PROTOCOL if self.args.protocol == "annual" else "random_news_v2_final_input_only",
                    "unified_domain_team_protocol": UNIFIED_PROTOCOL,
                    "unified_preprocessing_parity": True,
                    "unified_domain_boundary_packets_preserved": True,
                    "unified_prior_domain_agent_reports_used": False,
                },
                "source_roots": [str(path) for path in self.source_roots],
                "source_content_manifests": {
                    key: {"content_sha256": digest,
                          "manifest_path": str(self.snapshot_base / "common_sources" / f"{key}.json")}
                    for key, digest in self.source_content_hashes.items()
                },
                "task_counts": counts,
                "tasks": tasks,
                "failures": self.failures,
            },
        )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Run the agreed six-company FINAL ablation suite with frozen upstream data."
    )
    parser.add_argument(
        "--suite-id",
        default=None,
        help="Directory name under ABLATION/experiments.",
    )
    parser.add_argument(
        "--conditions",
        default=",".join(CONDITIONS),
        help=f"Comma-separated subset of: {', '.join(CONDITIONS)}",
    )
    parser.add_argument(
        "--companies",
        default="all",
        help="all, or comma-separated company keys/names.",
    )
    parser.add_argument("--protocol", choices=list(PROTOCOLS), default="annual")
    parser.add_argument(
        "--selected-date", default=None,
        help="YYYYMMDD override, or 'report' for company-specific analyst report dates.",
    )
    parser.add_argument("--plan-only", action="store_true", help="Print local input requirements without writing files or calling APIs.")
    parser.add_argument("--model", default=DEFAULT_MODEL)
    parser.add_argument(
        "--replicate-start",
        type=int,
        default=1,
        help=(
            "First replicate number to execute (inclusive). "
            "--replicates remains the final replicate number."
        ),
    )
    parser.add_argument("--replicates", type=int, default=DEFAULT_REPLICATES)
    parser.add_argument("--seed", type=int, default=DEFAULT_SEED)
    parser.add_argument("--random-news-count", type=int, default=None)
    parser.add_argument("--env-file", type=Path, default=DEFAULT_ENV_FILE)
    parser.add_argument(
        "--source-root",
        action="append",
        default=None,
        help="Frozen full Output_total root. Repeat to add fallbacks.",
    )
    parser.add_argument("--llm-timeout", type=int, default=300)
    parser.add_argument("--final-stage-timeout", type=int, default=900)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--no-resume", dest="resume", action="store_false")
    parser.add_argument("--fail-fast", action="store_true")
    parser.set_defaults(resume=True)
    return parser


def normalize_args(args: argparse.Namespace) -> argparse.Namespace:
    protocol = PROTOCOLS[args.protocol]
    args.selected_date = protocol_date_mode(args.protocol, args.selected_date)
    args.news_window = protocol["news_window"]
    args.decision_horizon_profile = protocol["decision_horizon_profile"]
    args.suite_id = args.suite_id or protocol["suite_id"]
    if args.replicates < 1:
        raise ValueError("--replicates must be at least 1")
    if args.replicate_start < 1:
        raise ValueError("--replicate-start must be at least 1")
    if args.replicate_start > args.replicates:
        raise ValueError("--replicate-start cannot exceed --replicates")
    expected_news_count = 24 if args.protocol == "annual" else RANDOM_NEWS_COUNT
    if args.random_news_count is None:
        args.random_news_count = expected_news_count
    if args.random_news_count != expected_news_count:
        raise ValueError(
            f"Protocol requires a {expected_news_count}-event capacity (annual: match Full counts, at most two per month); "
            f"received {args.random_news_count}"
        )
    args.env_file = args.env_file.expanduser().resolve()
    args.source_root = args.source_root or list(DEFAULT_SOURCE_ROOTS)
    args.suite_id = safe_label(args.suite_id)
    return args


def parse_conditions(value: str) -> tuple[str, ...]:
    requested = [item.strip() for item in str(value).split(",") if item.strip()]
    unknown = sorted(set(requested) - set(CONDITIONS))
    if unknown:
        raise ValueError(f"Unknown condition(s): {unknown}; allowed={list(CONDITIONS)}")
    return tuple(dict.fromkeys(requested))


def parse_companies(value: str) -> tuple[CompanySpec, ...]:
    if str(value).strip().lower() == "all":
        return COMPANY_SPECS
    selected = []
    for raw in str(value).split(","):
        key = raw.strip()
        if not key:
            continue
        company = COMPANY_BY_KEY.get(key) or COMPANY_BY_NAME.get(key)
        if company is None:
            raise ValueError(
                f"Unknown company {key!r}; keys={list(COMPANY_BY_KEY)}, names={list(COMPANY_BY_NAME)}"
            )
        if company not in selected:
            selected.append(company)
    if not selected:
        raise ValueError("At least one company is required")
    return tuple(selected)


def _successful_task(
    state_path: Path, report_path: Path, *, dry_run: bool,
    expected_fingerprint: str | None = None,
) -> bool:
    if not state_path.is_file():
        return False
    try:
        state = load_json(state_path)
    except (OSError, ValueError):
        return False
    if expected_fingerprint is not None and state.get("protocol_fingerprint") != expected_fingerprint:
        return False
    if dry_run:
        return state.get("status") == "dry_run"
    return state.get("status") == "success" and report_path.is_file() and report_path.stat().st_size > 0


def _ablation_python_env(extra: dict[str, str] | None = None) -> dict[str, str]:
    """Run FINAL unchanged while enabling narrow ABLATION-only runtime compatibility fixes."""

    env = python_env(FINAL_SRC, extra)
    env["PYTHONPATH"] = str(RUNTIME_COMPAT_ROOT) + os.pathsep + env.get("PYTHONPATH", "")
    env["ABLATION_FINAL_RUNTIME_COMPAT"] = "1"
    return env


def main(argv: list[str] | None = None) -> int:
    args = normalize_args(build_parser().parse_args(argv))
    return AblationSuiteRunner(args).run()
