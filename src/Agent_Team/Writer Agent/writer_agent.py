"""CLI and orchestration for the Writer Agent."""

from __future__ import annotations

import argparse
import logging
import os
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[3]
SRC_ROOT = REPO_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from analyst_rewriter import rewrite_contract
from data_loader import load_json, load_optional_market_csv, load_optional_text, save_json
from editorial_polisher import polish_contract
from html_renderer import render_html_preview
from latex_renderer import render_writer_outputs
from llm_writer import DEFAULT_LLM_MODEL, LLMWriterUnavailable, apply_llm_writer, sanitize_contract_against_strategy
from orchestration.config import DEFAULT_ENV_FILE, load_project_env
from quality_validator import validate_report_quality
from report_contract_builder import build_report_contract
from writer_validator import validate_writer_outputs


logger = logging.getLogger(__name__)

OUTPUT_ROOT = REPO_ROOT / "Output_total"
DEFAULT_RUN_KEY = ""


def discover_default_run_key(output_root: Path = OUTPUT_ROOT) -> str:
    """Return the newest Strategy run key without binding the agent to a company."""

    strategy_root = output_root / "Strategy"
    if not strategy_root.exists():
        return ""
    candidates = [
        path
        for path in strategy_root.iterdir()
        if path.is_dir() and (path / "strategy_report.json").exists()
    ]
    if not candidates:
        return ""
    candidates.sort(key=lambda path: (path / "strategy_report.json").stat().st_mtime, reverse=True)
    return candidates[0].name


@dataclass(frozen=True)
class WriterAgentConfig:
    run_key: str = DEFAULT_RUN_KEY
    strategy_json: Path = OUTPUT_ROOT / "Strategy" / DEFAULT_RUN_KEY / "strategy_report.json"
    strategy_md: Path = OUTPUT_ROOT / "Strategy" / DEFAULT_RUN_KEY / "strategy_report.md"
    strategy_input_bundle: Path = OUTPUT_ROOT / "Strategy" / DEFAULT_RUN_KEY / "strategy_input_bundle.json"
    strategy_content_plan: Path = OUTPUT_ROOT / "Strategy" / DEFAULT_RUN_KEY / "strategy_content_plan.json"
    decision_basis_by_section: Path = OUTPUT_ROOT / "Strategy" / DEFAULT_RUN_KEY / "decision_basis_by_section.json"
    dart_main: Path = OUTPUT_ROOT / "Financial" / DEFAULT_RUN_KEY / "dart_main.json"
    dart_lightweight: Path = OUTPUT_ROOT / "Financial" / DEFAULT_RUN_KEY / "dart_lightweight.json"
    market_csv: Path = OUTPUT_ROOT / "Y_Finance" / "market_full_dataset.csv"
    chart_manifest: Path = OUTPUT_ROOT / "Visualization" / DEFAULT_RUN_KEY / "chart_manifest.json"
    visualization_dir: Path = OUTPUT_ROOT / "Visualization" / DEFAULT_RUN_KEY
    peer_comparison_dataset: Path = OUTPUT_ROOT / "Competitor" / DEFAULT_RUN_KEY / "peer_comparison_dataset.json"
    peer_positioning_summary: Path = OUTPUT_ROOT / "Competitor" / DEFAULT_RUN_KEY / "peer_positioning_summary.json"
    output_dir: Path = OUTPUT_ROOT / "Writer" / DEFAULT_RUN_KEY
    env_file: Path = DEFAULT_ENV_FILE
    render_format: str = "html"
    include_source_trace: bool = False
    embed_images: bool = False
    writer_mode: str = "hybrid"
    llm_model: str = DEFAULT_LLM_MODEL


def run_writer_agent(config: WriterAgentConfig | dict[str, Any]) -> dict[str, Any]:
    """Run the Writer Agent end-to-end."""

    cfg = _coerce_config(config)
    env_status = load_project_env(cfg.env_file)
    output_dir = Path(cfg.output_dir).expanduser().resolve()
    output_dir.mkdir(parents=True, exist_ok=True)

    strategy_report = load_json(cfg.strategy_json, "Strategy report")
    strategy_md = load_optional_text(cfg.strategy_md)
    strategy_input_bundle = (
        load_json(cfg.strategy_input_bundle, "Strategy input bundle") if Path(cfg.strategy_input_bundle).exists() else None
    )
    strategy_content_plan = (
        load_json(cfg.strategy_content_plan, "Strategy content plan") if Path(cfg.strategy_content_plan).exists() else None
    )
    decision_basis_by_section = (
        load_json(cfg.decision_basis_by_section, "Decision basis by section")
        if Path(cfg.decision_basis_by_section).exists()
        else None
    )
    dart_main = load_json(cfg.dart_main, "DART main") if Path(cfg.dart_main).exists() else None
    chart_manifest = load_json(cfg.chart_manifest, "Chart manifest") if Path(cfg.chart_manifest).exists() else {"charts": []}
    peer_comparison_dataset = (
        load_json(cfg.peer_comparison_dataset, "Peer comparison dataset")
        if Path(cfg.peer_comparison_dataset).exists()
        else None
    )
    peer_positioning_summary = (
        load_json(cfg.peer_positioning_summary, "Peer positioning summary")
        if Path(cfg.peer_positioning_summary).exists()
        else None
    )
    load_optional_market_csv(cfg.market_csv)

    source_files = {
        "strategy_json": str(Path(cfg.strategy_json).expanduser().resolve()),
        "strategy_md": str(Path(cfg.strategy_md).expanduser().resolve()) if Path(cfg.strategy_md).exists() else "",
        "strategy_input_bundle": str(Path(cfg.strategy_input_bundle).expanduser().resolve())
        if Path(cfg.strategy_input_bundle).exists()
        else "",
        "strategy_content_plan": str(Path(cfg.strategy_content_plan).expanduser().resolve())
        if Path(cfg.strategy_content_plan).exists()
        else "",
        "decision_basis_by_section": str(Path(cfg.decision_basis_by_section).expanduser().resolve())
        if Path(cfg.decision_basis_by_section).exists()
        else "",
        "dart_main": str(Path(cfg.dart_main).expanduser().resolve()) if Path(cfg.dart_main).exists() else "",
        "dart_lightweight": str(Path(cfg.dart_lightweight).expanduser().resolve()) if Path(cfg.dart_lightweight).exists() else "",
        "market_csv": str(Path(cfg.market_csv).expanduser().resolve()) if Path(cfg.market_csv).exists() else "",
        "chart_manifest": str(Path(cfg.chart_manifest).expanduser().resolve()) if Path(cfg.chart_manifest).exists() else "",
        "visualization_dir": str(Path(cfg.visualization_dir).expanduser().resolve()),
        "peer_comparison_dataset": str(Path(cfg.peer_comparison_dataset).expanduser().resolve())
        if Path(cfg.peer_comparison_dataset).exists()
        else "",
        "peer_positioning_summary": str(Path(cfg.peer_positioning_summary).expanduser().resolve())
        if Path(cfg.peer_positioning_summary).exists()
        else "",
        "env_file": env_status["env_file"] if env_status.get("env_file_exists") else "",
    }
    contract = build_report_contract(
        strategy_report=strategy_report,
        strategy_input_bundle=strategy_input_bundle,
        dart_main=dart_main,
        chart_manifest=chart_manifest,
        visualization_dir=cfg.visualization_dir,
        peer_comparison_dataset=peer_comparison_dataset,
        peer_positioning_summary=peer_positioning_summary,
        source_files=source_files,
    )
    contract = rewrite_contract(contract)
    contract["environment"] = {
        "env_file": env_status["env_file"] if env_status.get("env_file_exists") else "",
        "openai_api_key_loaded": bool(env_status.get("openai_api_key_loaded")),
        "env_loader": env_status.get("loader", ""),
    }
    llm_writer_output: dict[str, Any] = {
        "status": "not_requested",
        "model": "",
        "selected_chart_ids": [block.get("figure_id") for block in contract.get("visual_report_blocks", [])],
    }
    if cfg.writer_mode in {"hybrid", "llm"}:
        try:
            contract, llm_writer_output = apply_llm_writer(
                contract=contract,
                strategy_report=strategy_report,
                strategy_content_plan=strategy_content_plan,
                decision_basis_by_section=decision_basis_by_section,
                chart_manifest=chart_manifest,
                visualization_dir=cfg.visualization_dir,
                model=cfg.llm_model,
            )
        except LLMWriterUnavailable as exc:
            if cfg.writer_mode == "llm":
                raise
            logger.warning("LLM Writer unavailable; falling back to deterministic Writer: %s", exc)
            llm_writer_output = {
                "status": "fallback_deterministic",
                "model": cfg.llm_model,
                "reason": str(exc),
                "selected_chart_ids": [block.get("figure_id") for block in contract.get("visual_report_blocks", [])],
            }
            contract["llm_writer"] = {key: value for key, value in llm_writer_output.items() if key != "raw_payload"}
    else:
        contract["llm_writer"] = {
            "status": "deterministic_only",
            "model": "",
            "selected_chart_ids": [block.get("figure_id") for block in contract.get("visual_report_blocks", [])],
        }
        llm_writer_output = contract["llm_writer"]
    contract = polish_contract(contract)
    contract = sanitize_contract_against_strategy(contract, strategy_report)
    contract["render_targets"]["html_preview"] = cfg.render_format in {"html", "both"}
    contract["render_targets"]["pdf_export"] = cfg.render_format in {"pdf", "both"}
    contract["render_targets"]["requested_render_format"] = cfg.render_format
    contract["render_targets"]["embed_images"] = cfg.embed_images
    contract["render_targets"]["writer_mode"] = cfg.writer_mode
    contract["render_targets"]["llm_model"] = cfg.llm_model if cfg.writer_mode in {"hybrid", "llm"} else ""
    save_json(output_dir / "broker_report_contract_v1.json", contract)
    save_json(output_dir / "source_trace.json", contract["source_trace"])
    save_json(output_dir / "llm_writer_output.json", llm_writer_output)

    render_result: dict[str, Any] = {
        "render_format": cfg.render_format,
        "html_preview": "",
        "html_content": "",
        "main_tex": "",
        "latex_compile_status": "not_requested",
        "latex_notes": [],
        "final_pdf": "",
        "embed_images": cfg.embed_images,
    }
    if cfg.render_format in {"html", "both"}:
        render_result.update(
            render_html_preview(
                contract,
                output_dir,
                include_source_trace=cfg.include_source_trace,
                embed_images=cfg.embed_images,
            )
        )
    if cfg.render_format in {"pdf", "both"}:
        render_result.update(render_writer_outputs(contract, output_dir, include_source_trace=cfg.include_source_trace))

    validation = validate_writer_outputs(
        contract=contract,
        strategy_report=strategy_report,
        chart_manifest=chart_manifest,
        source_trace=contract["source_trace"],
        main_tex=render_result["main_tex"],
        final_pdf_path=render_result["final_pdf"],
        html_preview_path=render_result["html_preview"],
        html_content=render_result["html_content"],
        render_format=cfg.render_format,
        include_source_trace=cfg.include_source_trace,
        embed_images=cfg.embed_images,
        latex_compile_status=render_result["latex_compile_status"],
        latex_notes=render_result["latex_notes"],
    )
    quality = validate_report_quality(
        contract=contract,
        strategy_report=strategy_report,
        html_content=render_result["html_content"],
        embed_images=cfg.embed_images,
    )
    validation["quality"] = quality
    validation["quality_score"] = quality["overall_quality_score"]
    save_json(output_dir / "quality_validation_report.json", quality)
    save_json(output_dir / "writer_validation_report.json", validation)
    logger.info("Wrote Writer Agent outputs to %s", output_dir)
    return {
        "output_dir": str(output_dir),
        "contract": str(output_dir / "broker_report_contract_v1.json"),
        "source_trace": str(output_dir / "source_trace.json"),
        "validation_report": str(output_dir / "writer_validation_report.json"),
        "quality_report": str(output_dir / "quality_validation_report.json"),
        "html_preview": render_result["html_preview"],
        "main_tex": str(output_dir / "main.tex") if render_result["main_tex"] else "",
        "final_report_pdf": render_result["final_pdf"],
        "validation_status": validation["status"],
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Generate final equity research draft outputs from Strategy and Visualization Agent artifacts.")
    parser.add_argument("--run-key", default=DEFAULT_RUN_KEY, help="Target run key, e.g. COMPANY_YYYYMMDD. Defaults to newest Strategy run.")
    parser.add_argument("--strategy-json", default=str(WriterAgentConfig.strategy_json), help="Override strategy_report.json path.")
    parser.add_argument("--strategy-md", default=str(WriterAgentConfig.strategy_md), help="Override strategy_report.md path.")
    parser.add_argument("--strategy-input-bundle", default=str(WriterAgentConfig.strategy_input_bundle), help="Override strategy_input_bundle.json path.")
    parser.add_argument("--strategy-content-plan", default=str(WriterAgentConfig.strategy_content_plan), help="Override strategy_content_plan.json path.")
    parser.add_argument(
        "--decision-basis-by-section",
        default=str(WriterAgentConfig.decision_basis_by_section),
        help="Override decision_basis_by_section.json path.",
    )
    parser.add_argument("--dart-main", default=str(WriterAgentConfig.dart_main), help="Override dart_main.json path.")
    parser.add_argument("--dart-lightweight", default=str(WriterAgentConfig.dart_lightweight), help="Override dart_lightweight.json path.")
    parser.add_argument("--market-csv", default=str(WriterAgentConfig.market_csv), help="Override market_full_dataset.csv path.")
    parser.add_argument("--chart-manifest", default=str(WriterAgentConfig.chart_manifest), help="Override chart_manifest.json path.")
    parser.add_argument("--visualization-dir", default=str(WriterAgentConfig.visualization_dir), help="Override Visualization output directory.")
    parser.add_argument("--peer-comparison-dataset", default=str(WriterAgentConfig.peer_comparison_dataset), help="Override peer_comparison_dataset.json path.")
    parser.add_argument("--peer-positioning-summary", default=str(WriterAgentConfig.peer_positioning_summary), help="Override peer_positioning_summary.json path.")
    parser.add_argument("--output-dir", default=str(WriterAgentConfig.output_dir), help="Override Writer output directory.")
    parser.add_argument("--env-file", default=str(WriterAgentConfig.env_file), help="Shared project env file containing OPENAI_API_KEY.")
    parser.add_argument("--render-format", choices=["html", "pdf", "both"], default=WriterAgentConfig.render_format)
    parser.add_argument("--include-source-trace", default="false")
    parser.add_argument("--embed-images", default="false")
    parser.add_argument(
        "--writer-mode",
        choices=["deterministic", "hybrid", "llm"],
        default=WriterAgentConfig.writer_mode,
        help="deterministic: rule-based only, hybrid: LLM when available with deterministic fallback, llm: require LLM.",
    )
    parser.add_argument(
        "--llm-model",
        default=os.getenv("WRITER_LLM_MODEL") or os.getenv("OPENAI_MODEL") or WriterAgentConfig.llm_model,
        help="OpenAI model used by the LLM Writer.",
    )
    parser.add_argument("--log-level", default="INFO")
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    logging.basicConfig(level=getattr(logging, args.log_level.upper(), logging.INFO), format="%(levelname)s %(message)s")
    run_writer_agent(
        WriterAgentConfig(
            run_key=args.run_key,
            strategy_json=Path(args.strategy_json),
            strategy_md=Path(args.strategy_md),
            strategy_input_bundle=Path(args.strategy_input_bundle),
            strategy_content_plan=Path(args.strategy_content_plan),
            decision_basis_by_section=Path(args.decision_basis_by_section),
            dart_main=Path(args.dart_main),
            dart_lightweight=Path(args.dart_lightweight),
            market_csv=Path(args.market_csv),
            chart_manifest=Path(args.chart_manifest),
            visualization_dir=Path(args.visualization_dir),
            peer_comparison_dataset=Path(args.peer_comparison_dataset),
            peer_positioning_summary=Path(args.peer_positioning_summary),
            output_dir=Path(args.output_dir),
            env_file=Path(args.env_file),
            render_format=args.render_format,
            include_source_trace=_parse_bool(args.include_source_trace),
            embed_images=_parse_bool(args.embed_images),
            writer_mode=args.writer_mode,
            llm_model=args.llm_model,
        )
    )
    return 0


def _coerce_config(config: WriterAgentConfig | dict[str, Any]) -> WriterAgentConfig:
    if isinstance(config, WriterAgentConfig):
        return _resolve_config_paths(config)
    if not isinstance(config, dict):
        raise TypeError("config must be WriterAgentConfig or dict.")
    run_key = str(config.get("run_key", WriterAgentConfig.run_key) or "")
    cfg = WriterAgentConfig(
        run_key=run_key,
        strategy_json=Path(config.get("strategy_json", WriterAgentConfig.strategy_json)),
        strategy_md=Path(config.get("strategy_md", WriterAgentConfig.strategy_md)),
        strategy_input_bundle=Path(config.get("strategy_input_bundle", WriterAgentConfig.strategy_input_bundle)),
        strategy_content_plan=Path(config.get("strategy_content_plan", WriterAgentConfig.strategy_content_plan)),
        decision_basis_by_section=Path(config.get("decision_basis_by_section", WriterAgentConfig.decision_basis_by_section)),
        dart_main=Path(config.get("dart_main", WriterAgentConfig.dart_main)),
        dart_lightweight=Path(config.get("dart_lightweight", WriterAgentConfig.dart_lightweight)),
        market_csv=Path(config.get("market_csv", WriterAgentConfig.market_csv)),
        chart_manifest=Path(config.get("chart_manifest", WriterAgentConfig.chart_manifest)),
        visualization_dir=Path(config.get("visualization_dir", WriterAgentConfig.visualization_dir)),
        peer_comparison_dataset=Path(config.get("peer_comparison_dataset", WriterAgentConfig.peer_comparison_dataset)),
        peer_positioning_summary=Path(config.get("peer_positioning_summary", WriterAgentConfig.peer_positioning_summary)),
        output_dir=Path(config.get("output_dir", WriterAgentConfig.output_dir)),
        env_file=Path(config.get("env_file", WriterAgentConfig.env_file)),
        render_format=str(config.get("render_format", WriterAgentConfig.render_format)),
        include_source_trace=_parse_bool(config.get("include_source_trace", WriterAgentConfig.include_source_trace)),
        embed_images=_parse_bool(config.get("embed_images", WriterAgentConfig.embed_images)),
        writer_mode=str(config.get("writer_mode", WriterAgentConfig.writer_mode)),
        llm_model=str(config.get("llm_model", WriterAgentConfig.llm_model)),
    )
    return _resolve_config_paths(cfg)


def _resolve_config_paths(config: WriterAgentConfig) -> WriterAgentConfig:
    run_key = config.run_key or discover_default_run_key()
    if not run_key:
        return config
    run_market_csv = OUTPUT_ROOT / "Y_Finance" / run_key / "market_full_dataset.csv"
    fallback_market_csv = OUTPUT_ROOT / "Y_Finance" / "market_full_dataset.csv"
    default_market_csv = run_market_csv if run_market_csv.exists() else fallback_market_csv
    default_for_run = WriterAgentConfig(run_key=run_key)
    default_for_run = WriterAgentConfig(
        run_key=run_key,
        strategy_json=OUTPUT_ROOT / "Strategy" / run_key / "strategy_report.json",
        strategy_md=OUTPUT_ROOT / "Strategy" / run_key / "strategy_report.md",
        strategy_input_bundle=OUTPUT_ROOT / "Strategy" / run_key / "strategy_input_bundle.json",
        strategy_content_plan=OUTPUT_ROOT / "Strategy" / run_key / "strategy_content_plan.json",
        decision_basis_by_section=OUTPUT_ROOT / "Strategy" / run_key / "decision_basis_by_section.json",
        dart_main=OUTPUT_ROOT / "Financial" / run_key / "dart_main.json",
        dart_lightweight=OUTPUT_ROOT / "Financial" / run_key / "dart_lightweight.json",
        market_csv=default_market_csv,
        chart_manifest=OUTPUT_ROOT / "Visualization" / run_key / "chart_manifest.json",
        visualization_dir=OUTPUT_ROOT / "Visualization" / run_key,
        peer_comparison_dataset=OUTPUT_ROOT / "Competitor" / run_key / "peer_comparison_dataset.json",
        peer_positioning_summary=OUTPUT_ROOT / "Competitor" / run_key / "peer_positioning_summary.json",
        output_dir=OUTPUT_ROOT / "Writer" / run_key,
        env_file=config.env_file,
        render_format=config.render_format,
        include_source_trace=config.include_source_trace,
        embed_images=config.embed_images,
        writer_mode=config.writer_mode,
        llm_model=config.llm_model,
    )
    empty_defaults = WriterAgentConfig()
    return WriterAgentConfig(
        run_key=run_key,
        strategy_json=default_for_run.strategy_json if config.strategy_json == empty_defaults.strategy_json else config.strategy_json,
        strategy_md=default_for_run.strategy_md if config.strategy_md == empty_defaults.strategy_md else config.strategy_md,
        strategy_input_bundle=default_for_run.strategy_input_bundle
        if config.strategy_input_bundle == empty_defaults.strategy_input_bundle
        else config.strategy_input_bundle,
        strategy_content_plan=default_for_run.strategy_content_plan
        if config.strategy_content_plan == empty_defaults.strategy_content_plan
        else config.strategy_content_plan,
        decision_basis_by_section=default_for_run.decision_basis_by_section
        if config.decision_basis_by_section == empty_defaults.decision_basis_by_section
        else config.decision_basis_by_section,
        dart_main=default_for_run.dart_main if config.dart_main == empty_defaults.dart_main else config.dart_main,
        dart_lightweight=default_for_run.dart_lightweight if config.dart_lightweight == empty_defaults.dart_lightweight else config.dart_lightweight,
        market_csv=default_for_run.market_csv if config.market_csv == empty_defaults.market_csv else config.market_csv,
        chart_manifest=default_for_run.chart_manifest if config.chart_manifest == empty_defaults.chart_manifest else config.chart_manifest,
        visualization_dir=default_for_run.visualization_dir
        if config.visualization_dir == empty_defaults.visualization_dir
        else config.visualization_dir,
        peer_comparison_dataset=default_for_run.peer_comparison_dataset
        if config.peer_comparison_dataset == empty_defaults.peer_comparison_dataset
        else config.peer_comparison_dataset,
        peer_positioning_summary=default_for_run.peer_positioning_summary
        if config.peer_positioning_summary == empty_defaults.peer_positioning_summary
        else config.peer_positioning_summary,
        output_dir=default_for_run.output_dir if config.output_dir == empty_defaults.output_dir else config.output_dir,
        env_file=default_for_run.env_file if config.env_file == empty_defaults.env_file else config.env_file,
        render_format=config.render_format,
        include_source_trace=config.include_source_trace,
        embed_images=config.embed_images,
        writer_mode=config.writer_mode,
        llm_model=config.llm_model,
    )


def _parse_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in {"1", "true", "yes", "y"}


if __name__ == "__main__":
    raise SystemExit(main())
