"""CLI and orchestration for the fixed-format HTML Writer Agent."""

from __future__ import annotations

import argparse
import logging
import os
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .formatted_html_renderer import render_formatted_html_report
from .html_report_writer import (
    DEFAULT_LLM_MODEL,
    normalize_report_payload,
    request_html_report_payload,
    validate_raw_writer_payload,
    writer_request_fingerprint,
)
from Agent_Team.Strategy_Agent.artifacts import (
    COMPACT_PACKET_FILENAME,
    DECISION_OUTPUT_FILENAME,
    PACKET_PROVENANCE_FILENAME,
)
from orchestration.config import DEFAULT_ENV_FILE, agent_output_dir, load_project_env
from .artifacts import (
    EDITORIAL_PACKET_FILENAME,
    EXECUTION_CACHE_FILENAME,
    PACKET_PROVENANCE_FILENAME as WRITER_PACKET_PROVENANCE_FILENAME,
)
from .writer_handoff import (
    build_writer_editorial_packet,
    validate_writer_editorial_packet,
)
from .writer_io import load_json, save_json


logger = logging.getLogger(__name__)

REPO_ROOT = Path(__file__).resolve().parents[3]
OUTPUT_ROOT = REPO_ROOT / "Output_total"
DEFAULT_RUN_KEY = ""
REQUIRED_STRATEGY_INPUT_FILES = (
    COMPACT_PACKET_FILENAME,
    PACKET_PROVENANCE_FILENAME,
    DECISION_OUTPUT_FILENAME,
)
WRITER_NORMALIZATION_VERSION = "12"
WRITER_RUNTIME_VERSION = "4"


def discover_default_run_key(output_root: Path = OUTPUT_ROOT) -> str:
    """Return the newest Strategy run key."""

    if not output_root.exists():
        return ""
    candidates = [
        path
        for company_dir in output_root.iterdir()
        if company_dir.is_dir()
        for path in (company_dir / "Strategy").glob("*")
        if path.is_dir()
        and all((path / filename).exists() for filename in REQUIRED_STRATEGY_INPUT_FILES)
    ]
    if not candidates:
        return ""
    candidates.sort(
        key=lambda path: (path / DECISION_OUTPUT_FILENAME).stat().st_mtime,
        reverse=True,
    )
    return f"{candidates[0].parents[1].name}_{candidates[0].name}"


@dataclass(frozen=True)
class WriterAgentConfig:
    run_key: str = DEFAULT_RUN_KEY
    strategy_packet: Path = OUTPUT_ROOT / "Strategy" / DEFAULT_RUN_KEY / COMPACT_PACKET_FILENAME
    strategy_provenance: Path = OUTPUT_ROOT / "Strategy" / DEFAULT_RUN_KEY / PACKET_PROVENANCE_FILENAME
    strategy_decision: Path = OUTPUT_ROOT / "Strategy" / DEFAULT_RUN_KEY / DECISION_OUTPUT_FILENAME
    output_dir: Path = OUTPUT_ROOT / "Writer" / DEFAULT_RUN_KEY
    market_charts: tuple[Path, ...] = ()
    chart_catalog: Path | None = None
    chart_manifest: Path | None = None
    env_file: Path = DEFAULT_ENV_FILE
    llm_model: str = DEFAULT_LLM_MODEL
    writer_mode: str = "deterministic"


def run_writer_agent(config: WriterAgentConfig | dict[str, Any]) -> dict[str, Any]:
    """Run the Writer Agent and produce one complete HTML report."""

    generation = run_writer_generation(config)
    rendering = run_writer_render(config)
    return {**generation, **rendering}


def run_writer_generation(config: WriterAgentConfig | dict[str, Any]) -> dict[str, Any]:
    """Create the report payload and select chart keys in the same LLM call."""

    cfg = _coerce_config(config)
    env_status = load_project_env(cfg.env_file)
    output_dir = Path(cfg.output_dir).expanduser().resolve()
    output_dir.mkdir(parents=True, exist_ok=True)

    strategy_packet = load_json(cfg.strategy_packet, "Strategy compact packet")
    strategy_provenance = load_json(cfg.strategy_provenance, "Strategy packet provenance")
    strategy_decision = load_json(cfg.strategy_decision, "Strategy decision output")
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
    chart_catalog = (
        load_json(cfg.chart_catalog, "Visualization chart catalog")
        if cfg.chart_catalog is not None
        else None
    )
    source_files = {
        "strategy_compact_packet": str(Path(cfg.strategy_packet).expanduser().resolve()),
        "strategy_packet_provenance": str(Path(cfg.strategy_provenance).expanduser().resolve()),
        "strategy_decision_output": str(Path(cfg.strategy_decision).expanduser().resolve()),
        "market_charts": [str(Path(path).expanduser().resolve()) for path in cfg.market_charts],
        "chart_catalog": (
            str(Path(cfg.chart_catalog).expanduser().resolve())
            if cfg.chart_catalog is not None
            else ""
        ),
        "env_file": env_status["env_file"] if env_status.get("env_file_exists") else "",
    }

    editorial_packet_path = output_dir / EDITORIAL_PACKET_FILENAME
    provenance_path = output_dir / WRITER_PACKET_PROVENANCE_FILENAME
    source_files_path = output_dir / "source_files.json"
    failure_path = output_dir / "writer_failure_report.json"
    save_json(editorial_packet_path, writer_handoff)
    save_json(provenance_path, writer_provenance)
    save_json(source_files_path, source_files)
    retired_packets = (
        "writer_editorial_packet_v3.json",
        "writer_packet_provenance_v3.json",
        "writer_editorial_packet_v2.json",
        "writer_packet_provenance_v2.json",
        "writer_execution_cache_v2.json",
    )
    for filename in retired_packets:
        retired = output_dir / filename
        if retired.exists():
            retired.unlink()

    writer_fingerprint = writer_request_fingerprint(
        writer_handoff=writer_handoff,
        model=cfg.llm_model,
        writer_mode=cfg.writer_mode,
        chart_catalog=chart_catalog,
    )
    cache_path = output_dir / EXECUTION_CACHE_FILENAME
    payload_path = output_dir / "writer_report_payload.json"
    llm_output_path = output_dir / "llm_writer_output.json"
    cached = load_cached_writer_outputs(
        cache_path=cache_path,
        payload_path=payload_path,
        llm_output_path=llm_output_path,
        expected_fingerprint=writer_fingerprint,
    )
    if cached is None:
        try:
            raw_payload, llm_writer_output = request_html_report_payload(
                writer_handoff=writer_handoff,
                model=cfg.llm_model,
                writer_mode=cfg.writer_mode,
                chart_catalog=chart_catalog,
            )
            llm_writer_output["fingerprint"] = writer_fingerprint
            llm_writer_output["cache_status"] = "miss"
            save_json(llm_output_path, llm_writer_output)
            validate_raw_writer_payload(raw_payload)
            report_payload = normalize_report_payload(
                raw_payload,
                writer_handoff=writer_handoff,
                writer_mode=cfg.writer_mode,
                chart_catalog=chart_catalog,
            )
        except Exception as exc:
            save_json(
                failure_path,
                {
                    "status": "fail",
                    "stage": "writer_response_validation_or_normalization",
                    "error_type": type(exc).__name__,
                    "message": str(exc),
                    "fingerprint": writer_fingerprint,
                    "runtime_version": WRITER_RUNTIME_VERSION,
                    "normalization_version": WRITER_NORMALIZATION_VERSION,
                    "raw_response_saved": llm_output_path.exists(),
                },
            )
            raise
    else:
        report_payload, llm_writer_output = cached
    report_payload.pop("report_charts", None)
    report_payload.pop("market_charts", None)
    save_json(payload_path, report_payload)
    llm_writer_output = {
        **llm_writer_output,
        "generation_status": "success",
        "runtime_version": WRITER_RUNTIME_VERSION,
        "normalization_version": WRITER_NORMALIZATION_VERSION,
    }
    save_json(llm_output_path, llm_writer_output)
    save_json(
        cache_path,
        {
            "fingerprint": writer_fingerprint,
            "normalization_version": WRITER_NORMALIZATION_VERSION,
            "runtime_version": WRITER_RUNTIME_VERSION,
        },
    )
    save_json(
        output_dir / "writer_generation_status.json",
        {
            "status": "success",
            "runtime_version": WRITER_RUNTIME_VERSION,
            "report_payload": str(payload_path),
            "requested_chart_keys": report_payload.get("requested_chart_keys", []),
            "chart_selection_details": report_payload.get(
                "chart_selection_details", []
            ),
        },
    )
    for retired_path in (failure_path,):
        if retired_path.exists():
            retired_path.unlink()
    _remove_legacy_writer_artifacts(output_dir)
    logger.info("Wrote Writer Agent payload to %s", payload_path)
    return {
        "status": "success",
        "output_dir": str(output_dir),
        "report_payload": str(payload_path),
        "writer_editorial_packet": str(editorial_packet_path),
        "writer_packet_provenance": str(provenance_path),
        "generation_status": str(output_dir / "writer_generation_status.json"),
    }


def run_writer_render(config: WriterAgentConfig | dict[str, Any]) -> dict[str, Any]:
    """Attach generated chart assets and render the final self-contained HTML."""

    cfg = _coerce_config(config)
    output_dir = Path(cfg.output_dir).expanduser().resolve()
    payload_path = output_dir / "writer_report_payload.json"
    report_payload = load_json(payload_path, "Writer report payload")
    if cfg.chart_manifest is not None:
        chart_manifest = load_json(cfg.chart_manifest, "Visualization chart manifest")
        report_charts = _prepare_chart_manifest_assets(
            chart_manifest,
            output_dir=output_dir,
        )
        requested = [str(value) for value in report_payload.get("requested_chart_keys") or []]
        generated = [str(item.get("chart_key") or "") for item in report_charts]
        if requested != generated:
            raise RuntimeError(
                "Visualization chart manifest does not match Writer requested_chart_keys: "
                f"requested={requested}, generated={generated}"
            )
    else:
        report_charts = _prepare_market_chart_assets(
            cfg.market_charts,
            output_dir=output_dir,
        )
    report_payload.pop("market_charts", None)
    report_payload["report_charts"] = report_charts
    save_json(payload_path, report_payload)
    source_files_path = output_dir / "source_files.json"
    source_files = (
        load_json(source_files_path, "Writer source files")
        if source_files_path.exists()
        else {}
    )
    source_files["chart_manifest"] = (
        str(Path(cfg.chart_manifest).expanduser().resolve())
        if cfg.chart_manifest is not None
        else ""
    )
    source_files["rendered_chart_assets"] = [
        str(item.get("src") or "") for item in report_charts
    ]
    save_json(source_files_path, source_files)
    render_result = render_formatted_html_report(report_payload, output_dir)
    html_report_path = Path(render_result["html_report"])
    if not html_report_path.is_file() or html_report_path.stat().st_size == 0:
        raise RuntimeError("Writer did not create a non-empty report.html file.")
    llm_output_path = output_dir / "llm_writer_output.json"
    if llm_output_path.exists():
        llm_writer_output = load_json(llm_output_path, "Writer LLM output")
        llm_writer_output["run_status"] = "success"
        save_json(llm_output_path, llm_writer_output)
    save_json(
        output_dir / "writer_run_status.json",
        {
            "status": "success",
            "runtime_version": WRITER_RUNTIME_VERSION,
            "html_report": str(html_report_path),
            "requested_chart_keys": report_payload.get("requested_chart_keys", []),
            "chart_selection_details": report_payload.get(
                "chart_selection_details", []
            ),
            "rendered_chart_keys": [
                str(item.get("chart_key") or "") for item in report_charts
            ],
        },
    )
    validation_path = output_dir / "writer_validation_report.json"
    if validation_path.exists():
        validation_path.unlink()
    logger.info("Wrote Writer Agent HTML report to %s", render_result["html_report"])
    return {
        "status": "success",
        "output_dir": str(output_dir),
        "html_report": render_result["html_report"],
        "report_html": render_result["report_html"],
        "report_payload": str(payload_path),
        "run_status": str(output_dir / "writer_run_status.json"),
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
        if cache.get("runtime_version") != WRITER_RUNTIME_VERSION:
            return None
        payload = load_json(payload_path, "Writer report payload")
        llm_output = load_json(llm_output_path, "Writer LLM output")
    except (OSError, ValueError):
        return None
    return payload, llm_output


def _remove_legacy_writer_artifacts(output_dir: Path) -> None:
    for filename in ("writer_handoff.json",):
        path = output_dir / filename
        if path.exists():
            path.unlink()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Generate one complete HTML investment report from Strategy Agent outputs.")
    parser.add_argument(
        "--phase",
        choices=("all", "generate", "render"),
        default="all",
        help="Run the complete Writer, payload generation only, or final rendering only.",
    )
    parser.add_argument("--run-key", default=DEFAULT_RUN_KEY, help="Target run key, e.g. COMPANY_YYYYMMDD. Defaults to newest Strategy run.")
    parser.add_argument(
        "--strategy-packet",
        default=str(WriterAgentConfig.strategy_packet),
        help=f"Override {COMPACT_PACKET_FILENAME} path.",
    )
    parser.add_argument(
        "--strategy-provenance",
        default=str(WriterAgentConfig.strategy_provenance),
        help=f"Override {PACKET_PROVENANCE_FILENAME} path.",
    )
    parser.add_argument(
        "--strategy-decision",
        default=str(WriterAgentConfig.strategy_decision),
        help="Override Strategy decision output path.",
    )
    parser.add_argument(
        "--market-chart",
        action="append",
        default=[],
        help="PNG market chart to copy into and embed in the final report. Repeatable.",
    )
    parser.add_argument(
        "--chart-catalog",
        default="",
        help="Visualization chart_catalog.json exposed to the Writer generation call.",
    )
    parser.add_argument(
        "--chart-manifest",
        default="",
        help="Visualization chart_manifest.json attached during final rendering.",
    )
    parser.add_argument("--output-dir", default=str(WriterAgentConfig.output_dir), help="Override Writer output directory.")
    parser.add_argument("--env-file", default=str(WriterAgentConfig.env_file), help="Shared project env file containing OPENAI_API_KEY.")
    parser.add_argument(
        "--llm-model",
        default=os.getenv("WRITER_LLM_MODEL") or os.getenv("OPENAI_MODEL") or WriterAgentConfig.llm_model,
        help="OpenAI model used by the LLM Writer.",
    )
    parser.add_argument(
        "--free-form",
        action="store_true",
        help="Let Writer author thesis and table cells instead of deterministic assembly.",
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
        market_charts=tuple(Path(path) for path in args.market_chart),
        chart_catalog=Path(args.chart_catalog) if args.chart_catalog else None,
        chart_manifest=Path(args.chart_manifest) if args.chart_manifest else None,
        env_file=Path(args.env_file),
        llm_model=args.llm_model,
        writer_mode="free_form" if args.free_form else "deterministic",
    )
    config = _coerce_config(config)
    if args.phase == "generate":
        run_writer_generation(config)
    elif args.phase == "render":
        run_writer_render(config)
    else:
        run_writer_agent(config)
    return 0


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
        market_charts=tuple(Path(path) for path in config.get("market_charts", ())),
        chart_catalog=(
            Path(config["chart_catalog"])
            if config.get("chart_catalog")
            else None
        ),
        chart_manifest=(
            Path(config["chart_manifest"])
            if config.get("chart_manifest")
            else None
        ),
        env_file=Path(config.get("env_file", WriterAgentConfig.env_file)),
        llm_model=str(config.get("llm_model", WriterAgentConfig.llm_model)),
        writer_mode=str(config.get("writer_mode", WriterAgentConfig.writer_mode)),
    )
    return _resolve_config_paths(cfg)


def _strategy_decision_path(run_key: str) -> Path:
    return agent_output_dir(OUTPUT_ROOT, run_key, "Strategy") / DECISION_OUTPUT_FILENAME


def _resolve_config_paths(config: WriterAgentConfig) -> WriterAgentConfig:
    run_key = config.run_key or discover_default_run_key()
    if not run_key:
        return config
    default_for_run = WriterAgentConfig(
        run_key=run_key,
        strategy_packet=agent_output_dir(OUTPUT_ROOT, run_key, "Strategy") / COMPACT_PACKET_FILENAME,
        strategy_provenance=agent_output_dir(OUTPUT_ROOT, run_key, "Strategy") / PACKET_PROVENANCE_FILENAME,
        strategy_decision=_strategy_decision_path(run_key),
        output_dir=agent_output_dir(OUTPUT_ROOT, run_key, "Writer"),
        market_charts=_default_market_charts(run_key),
        chart_catalog=config.chart_catalog,
        chart_manifest=config.chart_manifest,
        env_file=config.env_file,
        llm_model=config.llm_model,
        writer_mode=config.writer_mode,
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
        market_charts=default_for_run.market_charts if not config.market_charts else config.market_charts,
        chart_catalog=config.chart_catalog,
        chart_manifest=config.chart_manifest,
        env_file=default_for_run.env_file if config.env_file == empty_defaults.env_file else config.env_file,
        llm_model=config.llm_model,
        writer_mode=config.writer_mode,
    )


def _default_market_charts(run_key: str) -> tuple[Path, ...]:
    charts_dir = agent_output_dir(OUTPUT_ROOT, run_key, "Y_Finance") / "charts"
    selected = (
        charts_dir / "full_period_technical.png",
        charts_dir / "full_period_kospi_fx.png",
        charts_dir / f"summary_{run_key.rsplit('_', 1)[-1]}.png",
    )
    return tuple(path for path in selected if path.is_file())


def _prepare_market_chart_assets(
    chart_paths: tuple[Path, ...],
    *,
    output_dir: Path,
) -> list[dict[str, str]]:
    captions = {
        "full_period_technical.png": "분석기간 주가 및 기술지표",
        "full_period_kospi_fx.png": "코스피 및 원·달러 환율 흐름",
    }
    assets_dir = output_dir / "assets"
    assets: list[dict[str, str]] = []
    for raw_path in chart_paths:
        source = Path(raw_path).expanduser().resolve()
        if not source.is_file() or source.suffix.lower() != ".png":
            logger.warning("Skipping unavailable market chart: %s", source)
            continue
        assets_dir.mkdir(parents=True, exist_ok=True)
        destination = assets_dir / source.name
        if source != destination:
            shutil.copy2(source, destination)
        caption = captions.get(source.name)
        if caption is None and source.name.startswith("summary_"):
            caption = "분석기간 시장 요약"
        assets.append(
            {
                "src": f"assets/{destination.name}",
                "caption": caption or source.stem.replace("_", " "),
            }
        )
    return assets


def _prepare_chart_manifest_assets(
    chart_manifest: dict[str, Any],
    *,
    output_dir: Path,
) -> list[dict[str, str]]:
    """Copy Writer-selected Visualization PNGs while preserving chart order and captions."""

    assets_dir = output_dir / "assets"
    assets: list[dict[str, str]] = []
    for index, chart in enumerate(chart_manifest.get("charts") or [], start=1):
        if not isinstance(chart, dict):
            raise RuntimeError("Visualization chart manifest contains a non-object chart entry.")
        chart_key = str(chart.get("chart_key") or "").strip()
        source_ref = str(chart.get("asset_abs_path_png") or "").strip()
        if not chart_key or not source_ref:
            raise RuntimeError("Visualization chart manifest requires chart_key and asset_abs_path_png.")
        source = Path(source_ref).expanduser().resolve()
        if not source.is_file() or source.suffix.lower() != ".png":
            raise RuntimeError(f"Visualization chart PNG is unavailable: {source}")
        assets_dir.mkdir(parents=True, exist_ok=True)
        destination = assets_dir / f"{index:02d}_{chart_key}.png"
        if source != destination:
            shutil.copy2(source, destination)
        writer_selection = (
            chart.get("writer_selection")
            if isinstance(chart.get("writer_selection"), dict)
            else {}
        )
        chart_observation = str(
            writer_selection.get("chart_observation") or ""
        ).strip()
        investment_interpretation = str(
            writer_selection.get("investment_interpretation") or ""
        ).strip()
        if not chart_observation or not investment_interpretation:
            raise RuntimeError(
                f"Visualization chart commentary is unavailable: {chart_key}"
            )
        assets.append(
            {
                "chart_key": chart_key,
                "src": f"assets/{destination.name}",
                "title": str(chart.get("title") or chart_key),
                "alt": str(chart.get("caption") or chart.get("title") or chart_key),
                "chart_observation": chart_observation,
                "investment_interpretation": investment_interpretation,
                "caption": f"{chart_observation} {investment_interpretation}",
            }
        )
    return assets


if __name__ == "__main__":
    raise SystemExit(main())
