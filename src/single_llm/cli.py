"""CLI for preparing, generating, and validating Single-LLM baseline reports."""

from __future__ import annotations

import argparse
import json
import os
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator

from orchestration.config import load_project_env, safe_label

from .artifacts import ensure_output_directory, write_json, write_text
from .client import (
    DEFAULT_PROMPT_PATH,
    build_single_llm_request,
    call_single_llm,
    estimate_cost_usd,
    estimate_request_tokens,
    request_fingerprint,
)
from .config import (
    DEFAULT_CONFIG_PATH,
    PROJECT_ROOT,
    SingleLLMConfig,
    load_single_llm_config,
)
from .input_bundle import (
    build_input_bundle,
    fit_bundle_to_token_budget,
)
from .renderer import render_report_html
from .validator import validate_report


DEFAULT_EXPERIMENT_ID = "single_llm_gpt4_1_v1"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Single-model, single-generation-call financial report baseline."
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    build = subparsers.add_parser(
        "build",
        help="Freeze and size the raw-data request without making an LLM call.",
    )
    _add_generation_arguments(build)

    generate = subparsers.add_parser(
        "generate",
        help="Build the request and make exactly one semantic report-generation call.",
    )
    _add_generation_arguments(generate)
    generate.add_argument(
        "--env-file",
        default=None,
        help="Optional dotenv file. Defaults to configs/.env.",
    )

    validate = subparsers.add_parser(
        "validate",
        help="Validate an existing Single-LLM report without calling an LLM.",
    )
    validate.add_argument("--report", required=True)
    validate.add_argument("--bundle", required=True)
    validate.add_argument("--config", default=str(DEFAULT_CONFIG_PATH))
    validate.add_argument("--output", default=None)
    validate.add_argument(
        "--no-strict-numeric-grounding",
        action="store_true",
        help="Report unsupported numbers as warnings instead of validation errors.",
    )

    finalize = subparsers.add_parser(
        "finalize",
        help=(
            "Revalidate and render an existing API result without making another "
            "LLM call."
        ),
    )
    finalize.add_argument("--run-dir", required=True)
    finalize.add_argument("--config", default=str(DEFAULT_CONFIG_PATH))
    return parser


def _add_generation_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--target-run-key", required=True)
    parser.add_argument("--peer-run-key", required=True)
    parser.add_argument("--experiment-id", default=DEFAULT_EXPERIMENT_ID)
    parser.add_argument("--replicate", type=int, default=1)
    parser.add_argument("--config", default=None)
    parser.add_argument("--model", default=None)
    parser.add_argument("--decision-horizon", default=None)
    parser.add_argument("--target-input-tokens", type=int, default=None)
    parser.add_argument("--hard-input-tokens", type=int, default=None)
    parser.add_argument("--max-output-tokens", type=int, default=None)
    parser.add_argument("--max-news-items-per-company", type=int, default=None)
    parser.add_argument("--min-news-items-per-company", type=int, default=None)
    parser.add_argument("--output-root", default=str(PROJECT_ROOT / "Output_total"))
    parser.add_argument(
        "--source-root",
        default=None,
        help=(
            "Root containing runs/, Financial/, News/, and Y_Finance/ source "
            "artifacts. Defaults to --output-root."
        ),
    )
    parser.add_argument("--prompt", default=str(DEFAULT_PROMPT_PATH))
    parser.add_argument(
        "--overwrite",
        action="store_true",
        help="Replace this exact experiment/target/replicate artifact set.",
    )


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.command == "validate":
        return _validate_existing(args)
    if args.command == "finalize":
        return _finalize_existing(args)
    config = _config_from_args(args)
    prepared = _prepare(args, config)
    if args.command == "build":
        print(f"prepared_output_dir={prepared['output_dir']}")
        print(f"estimated_input_tokens={prepared['request_tokens']}")
        print("llm_called=false")
        return 0
    return _generate(args, config, prepared)


def _prepare(args: argparse.Namespace, config: SingleLLMConfig) -> dict[str, Any]:
    if args.replicate <= 0:
        raise ValueError("replicate must be positive")
    output_root = Path(args.output_root).expanduser().resolve()
    source_root = Path(args.source_root or args.output_root).expanduser().resolve()
    output_dir = (
        output_root
        / "Single_LLM"
        / safe_label(args.experiment_id, "experiment")
        / safe_label(args.target_run_key, "target")
        / f"r{args.replicate:02d}"
    )
    ensure_output_directory(output_dir, overwrite=bool(args.overwrite))

    build_result = build_input_bundle(
        project_root=PROJECT_ROOT,
        output_root=source_root,
        target_run_key=args.target_run_key,
        peer_run_key=args.peer_run_key,
        decision_horizon=config.decision_horizon,
        max_news_items_per_company=config.max_news_items_per_company,
    )
    prompt_path = Path(args.prompt).expanduser().resolve()

    def measure(bundle: dict[str, Any]) -> int:
        request = build_single_llm_request(
            bundle=bundle,
            config=config,
            prompt_path=prompt_path,
        )
        return estimate_request_tokens(request, model=config.model)

    fitted_bundle, budget = fit_bundle_to_token_budget(
        build_result.bundle,
        measure_input_tokens=measure,
        target_input_tokens=config.target_input_tokens,
        hard_input_tokens=config.hard_input_tokens,
        min_news_items_per_company=config.min_news_items_per_company,
    )
    request = build_single_llm_request(
        bundle=fitted_bundle,
        config=config,
        prompt_path=prompt_path,
    )
    request_tokens = estimate_request_tokens(request, model=config.model)
    if request_tokens > config.hard_input_tokens:
        raise ValueError(
            f"Final request is {request_tokens:,} tokens; hard limit is "
            f"{config.hard_input_tokens:,}."
        )

    manifest = {
        "version": "single_llm_run_manifest_v1",
        "status": "prepared",
        "prepared_at": _now(),
        "experiment_id": args.experiment_id,
        "replicate": args.replicate,
        "target_run_key": args.target_run_key,
        "peer_run_key": args.peer_run_key,
        "output_dir": str(output_dir),
        "source_root": str(source_root),
        "prompt_path": str(prompt_path),
        "prompt_sha256": _file_sha256(prompt_path),
        "config": config.as_dict(),
        "request_fingerprint": request_fingerprint(request),
        "request_tokens": request_tokens,
        "budget": budget,
        "semantic_generation_attempts": 0,
        "llm_called": False,
    }
    write_json(output_dir / "config_resolved.json", config.as_dict())
    write_json(output_dir / "source_manifest.json", build_result.source_manifest)
    write_json(output_dir / "temporal_validation.json", build_result.temporal_validation)
    write_json(output_dir / "input_bundle.json", fitted_bundle)
    write_json(output_dir / "request.json", request)
    write_json(output_dir / "request_budget.json", budget)
    write_json(output_dir / "run_manifest.json", manifest)
    return {
        "output_dir": output_dir,
        "bundle": fitted_bundle,
        "request": request,
        "request_tokens": request_tokens,
        "manifest": manifest,
    }


def _generate(
    args: argparse.Namespace,
    config: SingleLLMConfig,
    prepared: dict[str, Any],
) -> int:
    output_dir = Path(prepared["output_dir"])
    env_status = load_project_env(args.env_file)
    started_at = _now()
    with _telemetry_environment(
        output_dir=output_dir,
        target_run_key=args.target_run_key,
        company_name=str((prepared["bundle"].get("target") or {}).get("company_name") or ""),
        replicate=args.replicate,
        target_input_tokens=config.target_input_tokens,
        hard_input_tokens=config.hard_input_tokens,
    ):
        try:
            result = call_single_llm(prepared["request"], config=config)
        except Exception as exc:
            failure = {
                **prepared["manifest"],
                "status": "generation_failed",
                "started_at": started_at,
                "finished_at": _now(),
                "semantic_generation_attempts": 1,
                "llm_called": True,
                "error": str(exc),
                "environment": env_status,
            }
            write_json(output_dir / "run_manifest.json", failure)
            raise

    write_json(output_dir / "report.json", result.report)
    validation = validate_report(
        result.report,
        bundle=prepared["bundle"],
        strict_numeric_grounding=config.strict_numeric_grounding,
    )
    write_json(output_dir / "validation.json", validation)
    generation_manifest = {
        **prepared["manifest"],
        "status": "valid" if validation["status"] == "valid" else "validation_failed",
        "started_at": started_at,
        "finished_at": _now(),
        "semantic_generation_attempts": 1,
        "llm_called": True,
        "response_id": result.response_id,
        "response_model": result.response_model,
        "usage": result.usage,
        "estimated_cost_usd": estimate_cost_usd(result.usage, config),
        "environment": env_status,
        "validation_status": validation["status"],
    }
    # Persist the API outcome before presentation rendering. If rendering fails,
    # a retry-safe finalize command can reuse the one generated response instead
    # of making a second semantic call.
    write_json(output_dir / "run_manifest.json", generation_manifest)
    html = render_report_html(result.report)
    leaked_ids = [
        str(item.get("evidence_id"))
        for item in prepared["bundle"].get("evidence_catalog") or []
        if isinstance(item, dict)
        and item.get("evidence_id")
        and str(item.get("evidence_id")) in html
    ]
    if leaked_ids:
        raise RuntimeError(f"Renderer exposed internal evidence IDs: {leaked_ids[:5]}")
    write_text(output_dir / "report.html", html)
    write_json(output_dir / "run_manifest.json", generation_manifest)
    print(f"report={output_dir / 'report.html'}")
    print(f"validation={validation['status']}")
    print(f"estimated_cost_usd={generation_manifest['estimated_cost_usd']['total_usd']}")
    # Validation is an observed baseline-quality outcome, not a reason to sample
    # another response. Returning success preserves exactly one semantic call per
    # planned report and lets the evaluator report the validation pass rate.
    return 0


def _validate_existing(args: argparse.Namespace) -> int:
    report_path = Path(args.report).expanduser().resolve()
    bundle_path = Path(args.bundle).expanduser().resolve()
    report = _read_json_object(report_path)
    bundle = _read_json_object(bundle_path)
    config = load_single_llm_config(args.config)
    validation = validate_report(
        report,
        bundle=bundle,
        strict_numeric_grounding=(
            False if args.no_strict_numeric_grounding else config.strict_numeric_grounding
        ),
    )
    output = (
        Path(args.output).expanduser().resolve()
        if args.output
        else report_path.with_name("validation.json")
    )
    write_json(output, validation)
    print(f"validation={validation['status']}")
    print(f"output={output}")
    return 0 if validation["status"] == "valid" else 2


def _finalize_existing(args: argparse.Namespace) -> int:
    """Promote an already generated response after deterministic revalidation."""

    run_dir = Path(args.run_dir).expanduser().resolve()
    report = _read_json_object(run_dir / "report.json")
    bundle = _read_json_object(run_dir / "input_bundle.json")
    manifest = _read_json_object(run_dir / "run_manifest.json")
    if not manifest.get("llm_called"):
        usage_record = _successful_usage_record(run_dir / "llm_usage_manifest.jsonl")
        if usage_record is None:
            raise ValueError(f"No completed API generation is recorded: {run_dir}")
        recorded_model = str(usage_record.get("model") or "")
        recovered_usage = usage_record.get("usage") or {}
        manifest = {
            **manifest,
            "llm_called": True,
            "semantic_generation_attempts": 1,
            "response_id": None,
            "response_model": recorded_model,
            "usage": recovered_usage,
            "generation_recovery": {
                "reason": "API call completed before a renderer failure",
                "usage_manifest": str(run_dir / "llm_usage_manifest.jsonl"),
                "recovered_at": _now(),
            },
        }

    config = load_single_llm_config(args.config)
    recorded_model = str(
        manifest.get("response_model")
        or (manifest.get("config") or {}).get("model")
        or config.model
    )
    pricing_config = config.with_overrides(model=recorded_model)
    if manifest.get("usage") and not manifest.get("estimated_cost_usd"):
        manifest["estimated_cost_usd"] = estimate_cost_usd(
            manifest["usage"], pricing_config
        )
    validation = validate_report(
        report,
        bundle=bundle,
        strict_numeric_grounding=config.strict_numeric_grounding,
    )
    write_json(run_dir / "validation.json", validation)
    updated_manifest = {
        **manifest,
        "status": (
            "valid" if validation["status"] == "valid" else "validation_failed"
        ),
        "validation_status": validation["status"],
        "revalidated_at": _now(),
        "validation_version": validation.get("version"),
    }
    html = render_report_html(report)
    leaked_ids = [
        str(item.get("evidence_id"))
        for item in bundle.get("evidence_catalog") or []
        if isinstance(item, dict)
        and item.get("evidence_id")
        and str(item.get("evidence_id")) in html
    ]
    if leaked_ids:
        raise RuntimeError(f"Renderer exposed internal evidence IDs: {leaked_ids[:5]}")
    write_text(run_dir / "report.html", html)
    write_json(run_dir / "run_manifest.json", updated_manifest)
    print(f"validation={validation['status']}")
    print(f"report={run_dir / 'report.html'}")
    print("llm_called=false")
    return 0


def _config_from_args(args: argparse.Namespace) -> SingleLLMConfig:
    config = load_single_llm_config(args.config)
    return config.with_overrides(
        model=args.model,
        decision_horizon=args.decision_horizon,
        target_input_tokens=args.target_input_tokens,
        hard_input_tokens=args.hard_input_tokens,
        max_output_tokens=args.max_output_tokens,
        max_news_items_per_company=args.max_news_items_per_company,
        min_news_items_per_company=args.min_news_items_per_company,
    )


@contextmanager
def _telemetry_environment(
    *,
    output_dir: Path,
    target_run_key: str,
    company_name: str,
    replicate: int,
    target_input_tokens: int,
    hard_input_tokens: int,
) -> Iterator[None]:
    updates = {
        "LLM_USAGE_MANIFEST": str(output_dir / "llm_usage_manifest.jsonl"),
        "LLM_EXECUTION_ID": f"single_llm_{target_run_key}_r{replicate:02d}",
        "LLM_RUN_ID": f"{target_run_key}:single_llm:r{replicate:02d}",
        "LLM_RUN_ROLE": "single_llm_baseline",
        "LLM_COMPANY_NAME": company_name,
        "LLM_PROMPT_TARGET_TOKENS": str(target_input_tokens),
        "LLM_PROMPT_HARD_TOKENS": str(hard_input_tokens),
    }
    previous = {key: os.environ.get(key) for key in updates}
    os.environ.update(updates)
    try:
        yield
    finally:
        for key, value in previous.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value


def _read_json_object(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"Expected a JSON object: {path}")
    return payload


def _successful_usage_record(path: Path) -> dict[str, Any] | None:
    if not path.is_file():
        return None
    records: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        payload = json.loads(line)
        if isinstance(payload, dict) and payload.get("status") == "ok":
            records.append(payload)
    return records[-1] if records else None


def _file_sha256(path: Path) -> str:
    import hashlib

    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
