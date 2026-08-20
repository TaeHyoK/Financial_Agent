"""Prepare and run the six-company Single-LLM baseline on frozen v3 sources."""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from orchestration.paths import PROJECT_ROOT
from single_llm.config import DEFAULT_CONFIG_PATH


DEFAULT_EXPERIMENT_ID = "paper_six_company_single_llm_gpt5_4_mini_v3"
DEFAULT_MODEL = "gpt-5.4-mini"


@dataclass(frozen=True)
class CompanySpec:
    slug: str
    company_name: str


@dataclass(frozen=True)
class ResolvedCompany:
    slug: str
    company_name: str
    source_suite_root: str
    source_root: str
    source_pipeline_manifest: str
    target_run_key: str
    peer_run_key: str
    revised_suite_root: str


COMPANIES = (
    CompanySpec("skbiopharm", "SK바이오팜"),
    CompanySpec("amorepacific", "아모레퍼시픽"),
    CompanySpec("coway", "코웨이"),
    CompanySpec("hyundai_mobis", "현대모비스"),
    CompanySpec("bgf_retail", "BGF리테일"),
    CompanySpec("s_oil", "S-OIL"),
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("plan", "build", "generate", "status"))
    parser.add_argument("--project-root", type=Path, default=PROJECT_ROOT)
    parser.add_argument("--output-root", type=Path, default=PROJECT_ROOT / "Output_total")
    parser.add_argument(
        "--preflight-output-root",
        type=Path,
        default=PROJECT_ROOT / "Output_total" / "experiments" / "preflight" / "single_llm_v3",
    )
    parser.add_argument("--experiment-id", default=DEFAULT_EXPERIMENT_ID)
    parser.add_argument("--model", default=DEFAULT_MODEL)
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG_PATH)
    parser.add_argument("--replicates", type=int, default=3)
    parser.add_argument(
        "--only-company",
        action="append",
        default=[],
        choices=[item.slug for item in COMPANIES],
    )
    parser.add_argument(
        "--force-invalid",
        action="store_true",
        help="Replace a prior API-generated invalid/failed replicate.",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.replicates <= 0:
        raise ValueError("replicates must be positive")
    project_root = args.project_root.expanduser().resolve()
    output_root = args.output_root.expanduser().resolve()
    selected = [
        spec for spec in COMPANIES if not args.only_company or spec.slug in args.only_company
    ]
    resolved = [resolve_company(project_root, spec) for spec in selected]
    if args.action == "plan":
        _print_plan(resolved, args.replicates)
        return 0
    if args.action == "status":
        return _show_status(output_root, args.experiment_id, resolved, args.replicates)
    if args.action == "build":
        return _run_matrix(
            resolved,
            command="build",
            project_root=project_root,
            output_root=args.preflight_output_root.expanduser().resolve(),
            experiment_id=f"{args.experiment_id}_preflight",
            model=args.model,
            config=args.config.expanduser().resolve(),
            replicates=args.replicates,
            force_invalid=True,
        )
    return _run_matrix(
        resolved,
        command="generate",
        project_root=project_root,
        output_root=output_root,
        experiment_id=args.experiment_id,
        model=args.model,
        config=args.config.expanduser().resolve(),
        replicates=args.replicates,
        force_invalid=bool(args.force_invalid),
    )


def resolve_company(project_root: Path, spec: CompanySpec) -> ResolvedCompany:
    suite_id = f"paper_{spec.slug}_20251031_source_ablation_v3"
    source_suite_root = (
        project_root / "Output_total" / "experiments" / "ablations" / suite_id
    )
    suite_manifest_path = source_suite_root / "ablation_suite_manifest.json"
    suite_manifest = _load_json(suite_manifest_path)
    records = [
        item
        for item in suite_manifest.get("runs") or []
        if isinstance(item, dict)
        and item.get("condition") == "no_sy"
        and int(item.get("replicate") or 0) == 1
        and item.get("status") == "success"
    ]
    if not records:
        raise ValueError(f"No successful no_sy replicate 1 for {spec.company_name}")
    record = max(records, key=lambda item: int(item.get("attempt") or 1))
    source_root = _rebase_output_path(
        record.get("condition_root"), project_root=project_root
    )
    pipeline_manifest_path = _rebase_output_path(
        record.get("pipeline_manifest"), project_root=project_root
    )
    pipeline = _load_json(pipeline_manifest_path)
    if pipeline.get("status") != "success":
        raise ValueError(f"Source pipeline is not successful: {pipeline_manifest_path}")
    target_run_key = str(pipeline.get("run_key") or "")
    peer = pipeline.get("peer") if isinstance(pipeline.get("peer"), dict) else {}
    peer_run_key = str(peer.get("run_key") or "")
    if not target_run_key or not peer_run_key:
        raise ValueError(f"Target/peer run keys are missing: {pipeline_manifest_path}")
    return ResolvedCompany(
        slug=spec.slug,
        company_name=spec.company_name,
        source_suite_root=str(source_suite_root),
        source_root=str(source_root),
        source_pipeline_manifest=str(pipeline_manifest_path),
        target_run_key=target_run_key,
        peer_run_key=peer_run_key,
        revised_suite_root=str(
            project_root
            / "Output_total"
            / "experiments"
            / "ablations"
            / f"paper_{spec.slug}_20251031_revised_nosy_ablation_v3"
        ),
    )


def _run_matrix(
    companies: list[ResolvedCompany],
    *,
    command: str,
    project_root: Path,
    output_root: Path,
    experiment_id: str,
    model: str,
    config: Path,
    replicates: int,
    force_invalid: bool,
) -> int:
    experiment_root = output_root / "Single_LLM" / experiment_id
    experiment_root.mkdir(parents=True, exist_ok=True)
    records: list[dict[str, Any]] = []
    failures = 0
    for company in companies:
        for replicate in range(1, replicates + 1):
            output_dir = experiment_root / company.target_run_key / f"r{replicate:02d}"
            existing = _load_json_optional(output_dir / "run_manifest.json")
            if command == "generate" and _is_complete_output(output_dir, existing):
                print(
                    f"[single-llm] SKIP {company.company_name} r{replicate:02d} "
                    f"status={existing.get('status')}",
                    flush=True,
                )
                records.append(_record(company, replicate, output_dir, existing))
                continue
            if (
                command == "generate"
                and existing.get("llm_called")
                and not force_invalid
            ):
                print(
                    f"[single-llm] BLOCK {company.company_name} r{replicate:02d} "
                    "prior API result is not valid; use --force-invalid to replace it",
                    flush=True,
                )
                records.append(_record(company, replicate, output_dir, existing))
                failures += 1
                continue
            cli = [
                sys.executable,
                "-m",
                "single_llm.cli",
                command,
                "--target-run-key",
                company.target_run_key,
                "--peer-run-key",
                company.peer_run_key,
                "--source-root",
                company.source_root,
                "--output-root",
                str(output_root),
                "--experiment-id",
                experiment_id,
                "--replicate",
                str(replicate),
                "--model",
                model,
                "--config",
                str(config),
                "--overwrite",
            ]
            print(
                f"[single-llm] START {company.company_name} r{replicate:02d} action={command}",
                flush=True,
            )
            completed = subprocess.run(cli, cwd=str(project_root), check=False)
            current = _load_json_optional(output_dir / "run_manifest.json")
            records.append(_record(company, replicate, output_dir, current))
            if completed.returncode != 0 or (
                command == "generate" and not _is_complete_output(output_dir, current)
            ):
                failures += 1
                print(
                    f"[single-llm] END {company.company_name} r{replicate:02d} status=failed",
                    flush=True,
                )
            else:
                print(
                    f"[single-llm] END {company.company_name} r{replicate:02d} "
                    f"status={current.get('status', 'prepared')}",
                    flush=True,
                )
            _write_experiment_summary(
                experiment_root,
                experiment_id=experiment_id,
                model=model,
                command=command,
                companies=companies,
                replicates=replicates,
                records=records,
            )
    _write_experiment_summary(
        experiment_root,
        experiment_id=experiment_id,
        model=model,
        command=command,
        companies=companies,
        replicates=replicates,
        records=records,
    )
    return 1 if failures else 0


def _rebase_output_path(value: Any, *, project_root: Path) -> Path:
    """Resolve a manifest path under the active checkout's Output_total tree."""

    raw = str(value or "").strip()
    if not raw:
        raise ValueError("Manifest output path is empty")
    path = Path(raw).expanduser()
    if path.is_absolute() and "Output_total" in path.parts:
        output_index = path.parts.index("Output_total")
        path = project_root.joinpath(*path.parts[output_index:])
    elif not path.is_absolute():
        path = project_root / path
    return path.resolve()


def _write_experiment_summary(
    experiment_root: Path,
    *,
    experiment_id: str,
    model: str,
    command: str,
    companies: list[ResolvedCompany],
    replicates: int,
    records: list[dict[str, Any]],
) -> None:
    expected = len(companies) * replicates
    valid = sum(item.get("validation_status") == "valid" for item in records)
    invalid = sum(item.get("validation_status") == "invalid" for item in records)
    completed = valid + invalid
    failed = sum(
        item.get("status") == "generation_failed"
        for item in records
    )
    prepared = sum(item.get("status") == "prepared" for item in records)
    payload = {
        "schema_version": "single_llm_six_company_experiment_v1",
        "experiment_id": experiment_id,
        "updated_at": _now(),
        "action": command,
        "model": model,
        "design": {
            "company_count": len(companies),
            "replicates": replicates,
            "expected_reports": expected,
            "semantic_calls_per_report": 1,
        },
        "counts": {
            "recorded": len(records),
            "completed": completed,
            "prepared": prepared,
            "valid": valid,
            "validation_failed": invalid,
            "failed": failed,
        },
        "companies": [asdict(item) for item in companies],
        "runs": records,
    }
    _write_json(experiment_root / "experiment_summary.json", payload)


def _record(
    company: ResolvedCompany,
    replicate: int,
    output_dir: Path,
    manifest: dict[str, Any],
) -> dict[str, Any]:
    validation = _load_json_optional(output_dir / "validation.json")
    return {
        "slug": company.slug,
        "company_name": company.company_name,
        "target_run_key": company.target_run_key,
        "peer_run_key": company.peer_run_key,
        "replicate": replicate,
        "status": str(manifest.get("status") or "missing"),
        "llm_called": bool(manifest.get("llm_called")),
        "output_dir": str(output_dir),
        "report_html": str(output_dir / "report.html") if (output_dir / "report.html").is_file() else "",
        "report_json": str(output_dir / "report.json") if (output_dir / "report.json").is_file() else "",
        "input_bundle": str(output_dir / "input_bundle.json") if (output_dir / "input_bundle.json").is_file() else "",
        "run_manifest": str(output_dir / "run_manifest.json") if (output_dir / "run_manifest.json").is_file() else "",
        "validation_status": str(validation.get("status") or ""),
        "numeric_grounding_precision": (
            (validation.get("numeric_grounding") or {}).get("precision")
            if isinstance(validation.get("numeric_grounding"), dict)
            else None
        ),
        "request_tokens": manifest.get("request_tokens"),
        "usage": manifest.get("usage") or {},
        "estimated_cost_usd": manifest.get("estimated_cost_usd") or {},
        "source_root": company.source_root,
        "source_pipeline_manifest": company.source_pipeline_manifest,
        "revised_suite_root": company.revised_suite_root,
    }


def _show_status(
    output_root: Path,
    experiment_id: str,
    companies: list[ResolvedCompany],
    replicates: int,
) -> int:
    experiment_root = output_root / "Single_LLM" / experiment_id
    print("company\treplicate\tstatus\tvalidation\tllm_called")
    missing = 0
    for company in companies:
        for replicate in range(1, replicates + 1):
            output_dir = experiment_root / company.target_run_key / f"r{replicate:02d}"
            manifest = _load_json_optional(output_dir / "run_manifest.json")
            validation = _load_json_optional(output_dir / "validation.json")
            status = str(manifest.get("status") or "missing")
            missing += status == "missing"
            print(
                f"{company.company_name}\tr{replicate:02d}\t{status}\t"
                f"{validation.get('status', '')}\t{bool(manifest.get('llm_called'))}"
            )
    return 1 if missing else 0


def _print_plan(companies: list[ResolvedCompany], replicates: int) -> None:
    print("company\ttarget_run_key\tpeer_run_key\treplicates\tsource_root")
    for item in companies:
        print(
            f"{item.company_name}\t{item.target_run_key}\t{item.peer_run_key}\t"
            f"{replicates}\t{item.source_root}"
        )


def _is_complete_output(output_dir: Path, manifest: dict[str, Any]) -> bool:
    return (
        manifest.get("status") in {"valid", "validation_failed"}
        and (output_dir / "report.json").is_file()
        and (output_dir / "report.html").is_file()
        and (output_dir / "validation.json").is_file()
    )


def _load_json(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"Expected a JSON object: {path}")
    return payload


def _load_json_optional(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {}
    return _load_json(path)


def _write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, allow_nan=False) + "\n",
        encoding="utf-8",
    )


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


if __name__ == "__main__":
    raise SystemExit(main())
