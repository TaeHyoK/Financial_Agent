"""CLI and orchestration for the fixed-format HTML Writer Agent."""

from __future__ import annotations

import argparse
import logging
import os
import shutil
import sys
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[3]
SRC_ROOT = REPO_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from formatted_html_renderer import render_formatted_html_report
from html_report_validator import validate_html_report
from html_report_writer import (
    DEFAULT_LLM_MODEL,
    normalize_report_payload,
    request_html_report_payload,
    validate_raw_writer_payload,
    writer_request_fingerprint,
)
from orchestration.config import DEFAULT_ENV_FILE, load_project_env
from writer_handoff import (
    build_writer_editorial_packet,
    validate_writer_editorial_packet,
)
from writer_io import load_json, save_json


logger = logging.getLogger(__name__)

OUTPUT_ROOT = REPO_ROOT / "Output_total"
DEFAULT_RUN_KEY = ""
REQUIRED_STRATEGY_INPUT_FILES = (
    "strategy_compact_packet_v2.json",
    "strategy_packet_provenance_v2.json",
    "strategy_decision_output_v2.json",
)
WRITER_NORMALIZATION_VERSION = "6"
WRITER_VALIDATOR_VERSION = "2"


def discover_default_run_key(output_root: Path = OUTPUT_ROOT) -> str:
    """Return the newest Strategy run key."""

    strategy_root = output_root / "Strategy"
    if not strategy_root.exists():
        return ""
    candidates = [
        path
        for path in strategy_root.iterdir()
        if path.is_dir()
        and all((path / filename).exists() for filename in REQUIRED_STRATEGY_INPUT_FILES)
    ]
    if not candidates:
        return ""
    candidates.sort(key=lambda path: (path / "strategy_decision_output_v2.json").stat().st_mtime, reverse=True)
    return candidates[0].name


@dataclass(frozen=True)
class WriterAgentConfig:
    run_key: str = DEFAULT_RUN_KEY
    strategy_packet: Path = OUTPUT_ROOT / "Strategy" / DEFAULT_RUN_KEY / "strategy_compact_packet_v2.json"
    strategy_provenance: Path = OUTPUT_ROOT / "Strategy" / DEFAULT_RUN_KEY / "strategy_packet_provenance_v2.json"
    strategy_decision: Path = OUTPUT_ROOT / "Strategy" / DEFAULT_RUN_KEY / "strategy_decision_output_v2.json"
    output_dir: Path = OUTPUT_ROOT / "Writer" / DEFAULT_RUN_KEY
    env_file: Path = DEFAULT_ENV_FILE
    llm_model: str = DEFAULT_LLM_MODEL
    writer_mode: str = "deterministic"
    revalidate_raw: bool = False


def run_writer_agent(config: WriterAgentConfig | dict[str, Any]) -> dict[str, Any]:
    """Run the Writer Agent and produce one complete HTML report."""

    cfg = _coerce_config(config)
    env_status = load_project_env(cfg.env_file)
    output_dir = Path(cfg.output_dir).expanduser().resolve()
    output_dir.mkdir(parents=True, exist_ok=True)

    strategy_packet = load_json(cfg.strategy_packet, "Strategy compact packet v2")
    strategy_provenance = load_json(cfg.strategy_provenance, "Strategy packet provenance v2")
    strategy_decision = load_json(cfg.strategy_decision, "Strategy decision output v2")
    writer_handoff, writer_provenance = build_writer_editorial_packet(
        strategy_packet=strategy_packet,
        strategy_decision=strategy_decision,
        strategy_provenance=strategy_provenance,
    )
    validate_writer_editorial_packet(
        writer_handoff,
        provenance=writer_provenance,
        strategy_packet=strategy_packet,
    )
    source_files = {
        "strategy_compact_packet_v2": str(Path(cfg.strategy_packet).expanduser().resolve()),
        "strategy_packet_provenance_v2": str(Path(cfg.strategy_provenance).expanduser().resolve()),
        "strategy_decision_output_v2": str(Path(cfg.strategy_decision).expanduser().resolve()),
        "env_file": env_status["env_file"] if env_status.get("env_file_exists") else "",
    }

    editorial_packet_path = output_dir / "writer_editorial_packet_v2.json"
    provenance_path = output_dir / "writer_packet_provenance_v2.json"
    source_files_path = output_dir / "source_files.json"
    failure_path = output_dir / "writer_failure_report.json"
    save_json(editorial_packet_path, writer_handoff)
    save_json(provenance_path, writer_provenance)
    save_json(source_files_path, source_files)

    writer_fingerprint = writer_request_fingerprint(
        writer_handoff=writer_handoff,
        model=cfg.llm_model,
        writer_mode=cfg.writer_mode,
    )
    cache_path = output_dir / "writer_execution_cache_v2.json"
    payload_path = output_dir / "writer_report_payload.json"
    llm_output_path = output_dir / "llm_writer_output.json"
    cached = load_cached_writer_outputs(
        cache_path=cache_path,
        payload_path=payload_path,
        llm_output_path=llm_output_path,
        expected_fingerprint=writer_fingerprint,
    )
    if cached is None:
        cached_response = load_cached_writer_response(
            llm_output_path=llm_output_path,
            expected_fingerprint=writer_fingerprint,
            validation_report_path=output_dir / "writer_validation_report.json",
            allow_failed_validation=cfg.revalidate_raw,
        )
        try:
            if cached_response is not None:
                raw_payload = cached_response["raw_payload"]
                llm_writer_output = {
                    **cached_response,
                    "cache_status": "raw_response_reused",
                    "call_count": 0,
                }
            else:
                raw_payload, llm_writer_output = request_html_report_payload(
                    writer_handoff=writer_handoff,
                    model=cfg.llm_model,
                    writer_mode=cfg.writer_mode,
                )
                llm_writer_output["fingerprint"] = writer_fingerprint
                llm_writer_output["cache_status"] = "miss"
                save_json(llm_output_path, llm_writer_output)
            validate_raw_writer_payload(raw_payload)
            report_payload = normalize_report_payload(
                raw_payload,
                writer_handoff=writer_handoff,
                writer_mode=cfg.writer_mode,
            )
        except Exception as exc:
            _mark_writer_response_failed(
                llm_output_path=llm_output_path,
                fingerprint=writer_fingerprint,
                stage="writer_response_validation_or_normalization",
                error_type=type(exc).__name__,
                message=str(exc),
            )
            save_json(
                failure_path,
                {
                    "status": "fail",
                    "stage": "writer_response_validation_or_normalization",
                    "error_type": type(exc).__name__,
                    "message": str(exc),
                    "fingerprint": writer_fingerprint,
                    "validator_version": WRITER_VALIDATOR_VERSION,
                    "normalization_version": WRITER_NORMALIZATION_VERSION,
                    "raw_response_saved": llm_output_path.exists(),
                },
            )
            raise
    else:
        report_payload, llm_writer_output = cached
    save_json(payload_path, report_payload)
    save_json(llm_output_path, llm_writer_output)
    render_result = render_formatted_html_report(report_payload, output_dir)
    validation = validate_html_report(
        report_payload=report_payload,
        html_content=render_result["html_content"],
        writer_handoff=writer_handoff,
    )

    llm_writer_output = {
        **llm_writer_output,
        "validation_status": validation["status"],
        "validator_version": WRITER_VALIDATOR_VERSION,
        "normalization_version": WRITER_NORMALIZATION_VERSION,
        "validation_blocking_failures": list(validation.get("blocking_failures") or []),
    }
    save_json(llm_output_path, llm_writer_output)

    if validation["status"] == "pass":
        save_json(
            cache_path,
            {
                "fingerprint": writer_fingerprint,
                "normalization_version": WRITER_NORMALIZATION_VERSION,
                "validator_version": WRITER_VALIDATOR_VERSION,
            },
        )
    else:
        if cache_path.exists():
            cache_path.unlink()
        save_json(
            failure_path,
            {
                "status": "fail",
                "stage": "gate_c",
                "error_type": "WriterSemanticValidationError",
                "message": "Writer output failed Gate C semantic validation.",
                "fingerprint": writer_fingerprint,
                "validator_version": WRITER_VALIDATOR_VERSION,
                "normalization_version": WRITER_NORMALIZATION_VERSION,
                "blocking_failures": list(validation.get("blocking_failures") or []),
                "advisories": list(validation.get("advisories") or []),
                "raw_response_saved": llm_output_path.exists(),
            },
        )
    save_json(output_dir / "writer_validation_report.json", validation)
    if validation["status"] == "pass":
        if failure_path.exists():
            failure_path.unlink()
        _remove_deprecated_v1_writer_artifacts(output_dir)
    logger.info("Wrote Writer Agent HTML report to %s", render_result["html_report"])
    return {
        "output_dir": str(output_dir),
        "html_report": render_result["html_report"],
        "report_html": render_result["report_html"],
        "report_payload": str(output_dir / "writer_report_payload.json"),
        "writer_editorial_packet": str(editorial_packet_path),
        "writer_packet_provenance": str(provenance_path),
        "validation_report": str(output_dir / "writer_validation_report.json"),
        "validation_status": validation["status"],
    }


def load_cached_writer_outputs(
    *,
    cache_path: Path,
    payload_path: Path,
    llm_output_path: Path,
    expected_fingerprint: str,
) -> tuple[dict[str, Any], dict[str, Any]] | None:
    if not cache_path.exists() or not payload_path.exists() or not llm_output_path.exists():
        return None
    try:
        cache = load_json(cache_path, "Writer execution cache")
        if cache.get("fingerprint") != expected_fingerprint:
            return None
        if cache.get("normalization_version") != WRITER_NORMALIZATION_VERSION:
            return None
        if cache.get("validator_version") != WRITER_VALIDATOR_VERSION:
            return None
        payload = load_json(payload_path, "Writer report payload")
        llm_output = load_json(llm_output_path, "Writer LLM output")
    except (OSError, ValueError):
        return None
    return payload, llm_output


def load_cached_writer_response(
    *,
    llm_output_path: Path,
    expected_fingerprint: str,
    validation_report_path: Path | None = None,
    allow_failed_validation: bool = False,
) -> dict[str, Any] | None:
    if not llm_output_path.exists():
        return None
    try:
        llm_output = load_json(llm_output_path, "Writer LLM output")
    except (OSError, ValueError):
        return None
    if llm_output.get("fingerprint") != expected_fingerprint:
        return None
    if not isinstance(llm_output.get("raw_payload"), dict):
        return None
    validation_status = str(llm_output.get("validation_status") or "").strip().lower()
    if not validation_status:
        report_path = validation_report_path or llm_output_path.with_name("writer_validation_report.json")
        if report_path.exists():
            try:
                validation_status = str(
                    load_json(report_path, "Writer validation report").get("status") or ""
                ).strip().lower()
            except (OSError, ValueError):
                validation_status = ""
    if validation_status == "fail" and not allow_failed_validation:
        return None
    return llm_output


def _mark_writer_response_failed(
    *,
    llm_output_path: Path,
    fingerprint: str,
    stage: str,
    error_type: str,
    message: str,
) -> None:
    if not llm_output_path.exists():
        return
    try:
        llm_output = load_json(llm_output_path, "Writer LLM output")
    except (OSError, ValueError):
        return
    if llm_output.get("fingerprint") != fingerprint:
        return
    save_json(
        llm_output_path,
        {
            **llm_output,
            "validation_status": "fail",
            "validator_version": WRITER_VALIDATOR_VERSION,
            "normalization_version": WRITER_NORMALIZATION_VERSION,
            "failure_stage": stage,
            "failure_error_type": error_type,
            "failure_message": message,
        },
    )


def _remove_deprecated_v1_writer_artifacts(output_dir: Path) -> None:
    for filename in ("writer_handoff.json", "writer_execution_cache.json"):
        path = output_dir / filename
        if path.exists():
            path.unlink()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Generate one complete HTML investment report from Strategy Agent outputs.")
    parser.add_argument("--run-key", default=DEFAULT_RUN_KEY, help="Target run key, e.g. COMPANY_YYYYMMDD. Defaults to newest Strategy run.")
    parser.add_argument(
        "--strategy-packet",
        default=str(WriterAgentConfig.strategy_packet),
        help="Override strategy_compact_packet_v2.json path.",
    )
    parser.add_argument(
        "--strategy-provenance",
        default=str(WriterAgentConfig.strategy_provenance),
        help="Override strategy_packet_provenance_v2.json path.",
    )
    parser.add_argument(
        "--strategy-decision",
        default=str(WriterAgentConfig.strategy_decision),
        help="Override strategy_decision_output_v2.json path.",
    )
    parser.add_argument("--output-dir", default=str(WriterAgentConfig.output_dir), help="Override Writer output directory.")
    parser.add_argument("--env-file", default=str(WriterAgentConfig.env_file), help="Shared project env file containing OPENAI_API_KEY.")
    parser.add_argument(
        "--llm-model",
        default=os.getenv("WRITER_LLM_MODEL") or os.getenv("OPENAI_MODEL") or WriterAgentConfig.llm_model,
        help="OpenAI model used by the LLM Writer.",
    )
    parser.add_argument(
        "--revalidate-raw",
        action="store_true",
        help="Explicitly revalidate a fingerprinted raw response that previously failed Writer validation.",
    )
    parser.add_argument(
        "--free-form",
        action="store_true",
        help="Let Writer author thesis and table cells instead of deterministic assembly.",
    )
    parser.add_argument(
        "--semantic-attempts",
        type=int,
        default=2,
        help="Maximum fresh Writer generations for response-normalization or Gate-C failures.",
    )
    parser.add_argument("--log-level", default="INFO")
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    logging.basicConfig(level=getattr(logging, args.log_level.upper(), logging.INFO), format="%(levelname)s %(message)s")
    config = WriterAgentConfig(
        run_key=args.run_key,
        strategy_packet=Path(args.strategy_packet),
        strategy_provenance=Path(args.strategy_provenance),
        strategy_decision=Path(args.strategy_decision),
        output_dir=Path(args.output_dir),
        env_file=Path(args.env_file),
        llm_model=args.llm_model,
        writer_mode="free_form" if args.free_form else "deterministic",
        revalidate_raw=args.revalidate_raw,
    )
    config = _coerce_config(config)
    output_dir = Path(config.output_dir).expanduser().resolve()
    failure_path = output_dir / "writer_failure_report.json"
    semantic_attempts = max(1, int(args.semantic_attempts))
    for semantic_attempt in range(1, semantic_attempts + 1):
        before = failure_path.stat().st_mtime_ns if failure_path.exists() else None
        try:
            attempt_config = (
                config
                if semantic_attempt == 1
                else replace(config, revalidate_raw=False)
            )
            result = run_writer_agent(attempt_config)
        except Exception:
            changed_failure = (
                failure_path.exists()
                and failure_path.stat().st_mtime_ns != before
            )
            if not changed_failure or semantic_attempt >= semantic_attempts:
                raise
            _archive_writer_failure_attempt(output_dir, semantic_attempt)
            logger.warning(
                "Writer semantic attempt %s/%s failed during response processing; generating a fresh response.",
                semantic_attempt,
                semantic_attempts,
            )
            continue
        if result["validation_status"] == "pass":
            return 0
        _archive_writer_failure_attempt(output_dir, semantic_attempt)
        if semantic_attempt < semantic_attempts:
            logger.warning(
                "Writer semantic attempt %s/%s failed Gate C; generating a fresh response.",
                semantic_attempt,
                semantic_attempts,
            )
    return 1


def _archive_writer_failure_attempt(output_dir: Path, attempt: int) -> None:
    attempt_dir = output_dir / "attempts" / f"attempt_{attempt:02d}"
    attempt_dir.mkdir(parents=True, exist_ok=True)
    for filename in (
        "llm_writer_output.json",
        "writer_report_payload.json",
        "writer_validation_report.json",
        "writer_failure_report.json",
        "report.html",
    ):
        source = output_dir / filename
        if source.exists():
            shutil.copy2(source, attempt_dir / filename)


def _coerce_config(config: WriterAgentConfig | dict[str, Any]) -> WriterAgentConfig:
    if isinstance(config, WriterAgentConfig):
        return _resolve_config_paths(config)
    if not isinstance(config, dict):
        raise TypeError("config must be WriterAgentConfig or dict.")
    run_key = str(config.get("run_key", WriterAgentConfig.run_key) or "")
    cfg = WriterAgentConfig(
        run_key=run_key,
        strategy_packet=Path(config.get("strategy_packet", WriterAgentConfig.strategy_packet)),
        strategy_provenance=Path(config.get("strategy_provenance", WriterAgentConfig.strategy_provenance)),
        strategy_decision=Path(config.get("strategy_decision", WriterAgentConfig.strategy_decision)),
        output_dir=Path(config.get("output_dir", WriterAgentConfig.output_dir)),
        env_file=Path(config.get("env_file", WriterAgentConfig.env_file)),
        llm_model=str(config.get("llm_model", WriterAgentConfig.llm_model)),
        writer_mode=str(config.get("writer_mode", WriterAgentConfig.writer_mode)),
        revalidate_raw=bool(config.get("revalidate_raw", WriterAgentConfig.revalidate_raw)),
    )
    return _resolve_config_paths(cfg)


def _resolve_config_paths(config: WriterAgentConfig) -> WriterAgentConfig:
    run_key = config.run_key or discover_default_run_key()
    if not run_key:
        return config
    default_for_run = WriterAgentConfig(
        run_key=run_key,
        strategy_packet=OUTPUT_ROOT / "Strategy" / run_key / "strategy_compact_packet_v2.json",
        strategy_provenance=OUTPUT_ROOT / "Strategy" / run_key / "strategy_packet_provenance_v2.json",
        strategy_decision=OUTPUT_ROOT / "Strategy" / run_key / "strategy_decision_output_v2.json",
        output_dir=OUTPUT_ROOT / "Writer" / run_key,
        env_file=config.env_file,
        llm_model=config.llm_model,
        revalidate_raw=config.revalidate_raw,
    )
    empty_defaults = WriterAgentConfig()
    return WriterAgentConfig(
        run_key=run_key,
        strategy_packet=default_for_run.strategy_packet
        if config.strategy_packet == empty_defaults.strategy_packet
        else config.strategy_packet,
        strategy_provenance=default_for_run.strategy_provenance
        if config.strategy_provenance == empty_defaults.strategy_provenance
        else config.strategy_provenance,
        strategy_decision=default_for_run.strategy_decision
        if config.strategy_decision == empty_defaults.strategy_decision
        else config.strategy_decision,
        output_dir=default_for_run.output_dir if config.output_dir == empty_defaults.output_dir else config.output_dir,
        env_file=default_for_run.env_file if config.env_file == empty_defaults.env_file else config.env_file,
        llm_model=config.llm_model,
        revalidate_raw=config.revalidate_raw,
    )


if __name__ == "__main__":
    raise SystemExit(main())
