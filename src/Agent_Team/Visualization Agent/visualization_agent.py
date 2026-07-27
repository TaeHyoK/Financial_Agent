"""End-to-end deterministic Visualization Agent orchestration."""

from __future__ import annotations

import logging
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[3]
SRC_ROOT = REPO_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from chart_builders import (
    build_fundamental_margin_trend_chart,
    build_indexed_stock_vs_kospi_chart,
    build_investment_thesis_evidence_map_chart,
    build_liquidity_leverage_peer_comparison_chart,
    build_peer_profitability_comparison_chart,
    build_peer_return_comparison_chart,
    build_revenue_profit_sga_trend_chart,
    build_stock_price_ma_volume_relative_strength_chart,
)
from chart_insights_builder import attach_chart_insights
from data_loader import (
    ensure_file_exists,
    extract_financial_health_snapshot,
    extract_income_trend,
    extract_margin_trend,
    extract_peer_profitability_snapshot,
    extract_peer_return_snapshot,
    extract_strategy_evidence_map,
    load_dart_index,
    load_json_file,
    load_market_dataset,
)
from manifest_builder import build_chart_manifest, build_data_quality_report, build_visualization_summary
from orchestration.config import DEFAULT_ENV_FILE, load_project_env
from strategy_chart_planner import enrich_chart_metadata_with_strategy


logger = logging.getLogger(__name__)

OUTPUT_ROOT = REPO_ROOT / "Output_total"
DEFAULT_COMPANY_NAME = ""
DEFAULT_RUN_KEY = ""


@dataclass(frozen=True)
class VisualizationAgentConfig:
    """Runtime configuration for the Visualization Agent."""

    market_csv: Path = OUTPUT_ROOT / "Y_Finance" / "market_full_dataset.csv"
    dart_main: Path = OUTPUT_ROOT / "Financial" / DEFAULT_RUN_KEY / "dart_main.json"
    dart_lightweight: Path = OUTPUT_ROOT / "Financial" / DEFAULT_RUN_KEY / "dart_lightweight.json"
    strategy_json: Path = OUTPUT_ROOT / "Strategy" / DEFAULT_RUN_KEY / "strategy_report.json"
    strategy_md: Path = OUTPUT_ROOT / "Strategy" / DEFAULT_RUN_KEY / "strategy_report.md"
    decision_basis_card: Path = OUTPUT_ROOT / "Strategy" / DEFAULT_RUN_KEY / "decision_basis_card.json"
    peer_comparison_dataset: Path = OUTPUT_ROOT / "Competitor" / DEFAULT_RUN_KEY / "peer_comparison_dataset.json"
    output_dir: Path = OUTPUT_ROOT / "Visualization" / DEFAULT_RUN_KEY
    output_root: Path = OUTPUT_ROOT
    env_file: Path = DEFAULT_ENV_FILE
    peer_run_keys: tuple[str, ...] = ()
    company_name: str = DEFAULT_COMPANY_NAME
    run_key: str = DEFAULT_RUN_KEY


def run_visualization_agent(config: VisualizationAgentConfig | dict[str, Any]) -> dict[str, Any]:
    """Run the Visualization Agent and write all chart, manifest, and report outputs."""

    resolved_config = _coerce_config(config)
    load_project_env(resolved_config.env_file)
    output_dir = Path(resolved_config.output_dir).expanduser().resolve()
    figures_dir = output_dir / "figures"
    figures_dir.mkdir(parents=True, exist_ok=True)

    source_files = _resolve_source_files(resolved_config)
    logger.info("Loading market dataset: %s", source_files["market_full_dataset"])
    market_df = load_market_dataset(source_files["market_full_dataset"])
    logger.info("Loading DART main dataset: %s", source_files["dart_main"])
    dart_index = load_dart_index(source_files["dart_main"])
    strategy_report = load_json_file(source_files["strategy_report_json"], "Strategy report JSON")
    margin_df = extract_margin_trend(dart_index)
    income_df = extract_income_trend(dart_index)

    peer_run_keys = resolved_config.peer_run_keys or tuple(_discover_peer_run_keys(resolved_config.output_root, resolved_config.run_key))
    company_names = _load_company_names(resolved_config.output_root, peer_run_keys)
    peer_market_datasets = _load_peer_market_datasets(resolved_config.output_root, peer_run_keys)
    peer_return_df = extract_peer_return_snapshot(peer_market_datasets, company_names)
    peer_financial_reports = _load_peer_financial_reports(resolved_config.output_root, peer_run_keys)
    financial_health_df = extract_financial_health_snapshot(peer_financial_reports, company_names)
    peer_comparison_dataset = _load_optional_json(source_files.get("peer_comparison_dataset", ""))
    peer_profitability_df = (
        extract_peer_profitability_snapshot(peer_comparison_dataset)
        if peer_comparison_dataset
        else None
    )
    decision_basis_card = load_json_file(source_files["decision_basis_card"], "Decision basis card")
    evidence_df = extract_strategy_evidence_map(decision_basis_card)
    recommendation = _extract_strategy_recommendation(strategy_report)

    chart_metadata: list[dict[str, Any]] = []
    try:
        chart_metadata.append(
            build_stock_price_ma_volume_relative_strength_chart(
                market_df=market_df,
                output_pdf=figures_dir / "stock_price_ma_volume_relative_strength.pdf",
                output_png=figures_dir / "stock_price_ma_volume_relative_strength.png",
                company_name=resolved_config.company_name,
            )
        )
    except Exception:
        logger.exception("Failed to build chart: stock_price_ma_volume_relative_strength")
        raise

    try:
        chart_metadata.append(
            build_fundamental_margin_trend_chart(
                margin_df=margin_df,
                output_pdf=figures_dir / "fundamental_margin_trend.pdf",
                output_png=figures_dir / "fundamental_margin_trend.png",
                company_name=resolved_config.company_name,
            )
        )
    except Exception:
        logger.exception("Failed to build chart: fundamental_margin_trend")
        raise

    try:
        chart_metadata.append(
            build_indexed_stock_vs_kospi_chart(
                market_df=market_df,
                output_pdf=figures_dir / "indexed_stock_vs_kospi.pdf",
                output_png=figures_dir / "indexed_stock_vs_kospi.png",
                company_name=resolved_config.company_name,
            )
        )
    except Exception:
        logger.exception("Failed to build chart: indexed_stock_vs_kospi")
        raise

    try:
        chart_metadata.append(
            build_peer_return_comparison_chart(
                peer_return_df=peer_return_df,
                output_pdf=figures_dir / "peer_return_comparison.pdf",
                output_png=figures_dir / "peer_return_comparison.png",
            )
        )
    except Exception:
        logger.exception("Failed to build chart: peer_return_comparison")
        raise

    try:
        chart_metadata.append(
            build_revenue_profit_sga_trend_chart(
                income_df=income_df,
                output_pdf=figures_dir / "revenue_profit_sga_trend.pdf",
                output_png=figures_dir / "revenue_profit_sga_trend.png",
                company_name=resolved_config.company_name,
            )
        )
    except Exception:
        logger.exception("Failed to build chart: revenue_profit_sga_trend")
        raise

    try:
        chart_metadata.append(
            build_liquidity_leverage_peer_comparison_chart(
                financial_health_df=financial_health_df,
                output_pdf=figures_dir / "liquidity_leverage_peer_comparison.pdf",
                output_png=figures_dir / "liquidity_leverage_peer_comparison.png",
            )
        )
    except Exception:
        logger.exception("Failed to build chart: liquidity_leverage_peer_comparison")
        raise

    if peer_profitability_df is not None:
        try:
            chart_metadata.append(
                build_peer_profitability_comparison_chart(
                    peer_profitability_df=peer_profitability_df,
                    output_pdf=figures_dir / "peer_profitability_comparison.pdf",
                    output_png=figures_dir / "peer_profitability_comparison.png",
                )
            )
        except Exception:
            logger.exception("Failed to build chart: peer_profitability_comparison")
            raise

    try:
        chart_metadata.append(
            build_investment_thesis_evidence_map_chart(
                evidence_df=evidence_df,
                output_pdf=figures_dir / "investment_thesis_evidence_map.pdf",
                output_png=figures_dir / "investment_thesis_evidence_map.png",
                recommendation=recommendation,
            )
        )
    except Exception:
        logger.exception("Failed to build chart: investment_thesis_evidence_map")
        raise

    chart_metadata = attach_chart_insights(
        chart_metadata,
        market_df=market_df,
        margin_df=margin_df,
        income_df=income_df,
        peer_return_df=peer_return_df,
        financial_health_df=financial_health_df,
        evidence_df=evidence_df,
        company_name=resolved_config.company_name,
        peer_profitability_df=peer_profitability_df,
    )
    chart_metadata = enrich_chart_metadata_with_strategy(
        chart_metadata,
        strategy_report,
        company_name=resolved_config.company_name,
    )
    manifest = build_chart_manifest(
        company_name=resolved_config.company_name,
        run_key=resolved_config.run_key,
        source_files=source_files,
        chart_metadata=chart_metadata,
        output_path=output_dir / "chart_manifest.json",
    )
    data_quality_report = build_data_quality_report(
        market_df=market_df,
        margin_df=margin_df,
        dart_index=dart_index,
        source_files=source_files,
        output_path=output_dir / "data_quality_report.json",
        peer_return_df=peer_return_df,
        financial_health_df=financial_health_df,
        evidence_df=evidence_df,
    )
    build_visualization_summary(
        company_name=resolved_config.company_name,
        run_key=resolved_config.run_key,
        manifest=manifest,
        data_quality_report=data_quality_report,
        output_path=output_dir / "visualization_summary.md",
    )

    return {
        "output_dir": str(output_dir),
        "chart_manifest": str(output_dir / "chart_manifest.json"),
        "data_quality_report": str(output_dir / "data_quality_report.json"),
        "visualization_summary": str(output_dir / "visualization_summary.md"),
        "figures": [chart["asset_abs_path_png"] for chart in chart_metadata]
        + [chart["asset_abs_path_pdf"] for chart in chart_metadata],
        "charts": chart_metadata,
    }


def _coerce_config(config: VisualizationAgentConfig | dict[str, Any]) -> VisualizationAgentConfig:
    if isinstance(config, VisualizationAgentConfig):
        return _resolve_config_paths(config)
    if not isinstance(config, dict):
        raise TypeError("config must be VisualizationAgentConfig or dict.")
    cfg = VisualizationAgentConfig(
        market_csv=Path(config.get("market_csv", VisualizationAgentConfig.market_csv)),
        dart_main=Path(config.get("dart_main", VisualizationAgentConfig.dart_main)),
        dart_lightweight=Path(config.get("dart_lightweight", VisualizationAgentConfig.dart_lightweight)),
        strategy_json=Path(config.get("strategy_json", VisualizationAgentConfig.strategy_json)),
        strategy_md=Path(config.get("strategy_md", VisualizationAgentConfig.strategy_md)),
        decision_basis_card=Path(config.get("decision_basis_card", VisualizationAgentConfig.decision_basis_card)),
        peer_comparison_dataset=Path(config.get("peer_comparison_dataset", VisualizationAgentConfig.peer_comparison_dataset)),
        output_dir=Path(config.get("output_dir", VisualizationAgentConfig.output_dir)),
        output_root=Path(config.get("output_root", VisualizationAgentConfig.output_root)),
        env_file=Path(config.get("env_file", VisualizationAgentConfig.env_file)),
        peer_run_keys=tuple(config.get("peer_run_keys", VisualizationAgentConfig.peer_run_keys) or ()),
        company_name=str(config.get("company_name", DEFAULT_COMPANY_NAME)),
        run_key=str(config.get("run_key", DEFAULT_RUN_KEY)),
    )
    return _resolve_config_paths(cfg)


def discover_default_run_key(output_root: Path = OUTPUT_ROOT) -> str:
    """Return the newest Strategy run key without binding the agent to a company."""

    strategy_root = Path(output_root).expanduser().resolve() / "Strategy"
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


def _resolve_config_paths(config: VisualizationAgentConfig) -> VisualizationAgentConfig:
    run_key = config.run_key or discover_default_run_key(config.output_root)
    if not run_key:
        return config

    empty_defaults = VisualizationAgentConfig()
    run_market_csv = config.output_root / "Y_Finance" / run_key / "market_full_dataset.csv"
    fallback_market_csv = config.output_root / "Y_Finance" / "market_full_dataset.csv"
    default_market_csv = run_market_csv if run_market_csv.exists() else fallback_market_csv
    company_name = config.company_name or _infer_company_name(config.output_root, run_key)

    return VisualizationAgentConfig(
        market_csv=default_market_csv if config.market_csv == empty_defaults.market_csv else config.market_csv,
        dart_main=config.output_root / "Financial" / run_key / "dart_main.json"
        if config.dart_main == empty_defaults.dart_main
        else config.dart_main,
        dart_lightweight=config.output_root / "Financial" / run_key / "dart_lightweight.json"
        if config.dart_lightweight == empty_defaults.dart_lightweight
        else config.dart_lightweight,
        strategy_json=config.output_root / "Strategy" / run_key / "strategy_report.json"
        if config.strategy_json == empty_defaults.strategy_json
        else config.strategy_json,
        strategy_md=config.output_root / "Strategy" / run_key / "strategy_report.md"
        if config.strategy_md == empty_defaults.strategy_md
        else config.strategy_md,
        decision_basis_card=config.output_root / "Strategy" / run_key / "decision_basis_card.json"
        if config.decision_basis_card == empty_defaults.decision_basis_card
        else config.decision_basis_card,
        peer_comparison_dataset=config.output_root / "Competitor" / run_key / "peer_comparison_dataset.json"
        if config.peer_comparison_dataset == empty_defaults.peer_comparison_dataset
        else config.peer_comparison_dataset,
        output_dir=config.output_root / "Visualization" / run_key
        if config.output_dir == empty_defaults.output_dir
        else config.output_dir,
        output_root=config.output_root,
        env_file=config.env_file,
        peer_run_keys=config.peer_run_keys,
        company_name=company_name,
        run_key=run_key,
    )


def _infer_company_name(output_root: Path, run_key: str) -> str:
    manifest_path = Path(output_root) / "Y_Finance" / run_key / "manifest.json"
    if manifest_path.exists():
        try:
            manifest = load_json_file(manifest_path, f"Y-Finance manifest {run_key}")
            company_name = str(manifest.get("company_name") or "").strip()
            if company_name:
                return company_name
        except Exception:
            logger.warning("Failed to infer company name from %s", manifest_path)
    return run_key.rsplit("_", 1)[0]


def _extract_strategy_recommendation(strategy_report: dict[str, Any]) -> str:
    final_recommendation = strategy_report.get("final_recommendation")
    if isinstance(final_recommendation, dict):
        opinion = str(final_recommendation.get("opinion") or "").strip()
        if opinion:
            return opinion
    return "Investment Decision"


def _resolve_source_files(config: VisualizationAgentConfig) -> dict[str, str]:
    source_files = {
        "market_full_dataset": str(ensure_file_exists(config.market_csv, "Market dataset")),
        "dart_main": str(ensure_file_exists(config.dart_main, "DART main")),
        "dart_lightweight": str(ensure_file_exists(config.dart_lightweight, "DART lightweight")),
        "strategy_report_json": str(ensure_file_exists(config.strategy_json, "Strategy report JSON")),
        "strategy_report_md": str(ensure_file_exists(config.strategy_md, "Strategy report Markdown")),
        "decision_basis_card": str(ensure_file_exists(config.decision_basis_card, "Decision basis card")),
        "env_file": str(config.env_file.expanduser().resolve()) if config.env_file.exists() else "",
    }
    peer_comparison_dataset = Path(config.peer_comparison_dataset).expanduser().resolve()
    if peer_comparison_dataset.exists():
        source_files["peer_comparison_dataset"] = str(peer_comparison_dataset)
    else:
        source_files["peer_comparison_dataset"] = ""
    peer_run_keys = config.peer_run_keys or tuple(_discover_peer_run_keys(config.output_root, config.run_key))
    source_files["peer_market_datasets"] = {
        run_key: str(ensure_file_exists(config.output_root / "Y_Finance" / run_key / "market_full_dataset.csv", f"Peer market dataset {run_key}"))
        for run_key in peer_run_keys
    }
    source_files["peer_financial_reports"] = {
        run_key: str(ensure_file_exists(config.output_root / "Financial" / run_key / "final_report.json", f"Peer financial report {run_key}"))
        for run_key in peer_run_keys
    }
    return source_files


def _load_optional_json(path_value: str) -> dict[str, Any] | None:
    if not path_value:
        return None
    path = Path(path_value).expanduser().resolve()
    if not path.exists():
        return None
    return load_json_file(path, f"Optional JSON {path.name}")


def _discover_peer_run_keys(output_root: Path, target_run_key: str) -> list[str]:
    output_root = Path(output_root).expanduser().resolve()
    yfinance_root = output_root / "Y_Finance"
    selected_date = target_run_key.rsplit("_", 1)[-1]
    discovered = [
        path.name
        for path in yfinance_root.glob(f"*_{selected_date}")
        if path.is_dir() and (path / "market_full_dataset.csv").exists()
    ]
    ordered = [target_run_key] if target_run_key in discovered else []
    ordered.extend(sorted(run_key for run_key in discovered if run_key != target_run_key))
    if not ordered:
        raise FileNotFoundError(f"No peer market datasets found under {yfinance_root} for date suffix {selected_date}.")
    return ordered


def _load_company_names(output_root: Path, run_keys: tuple[str, ...]) -> dict[str, str]:
    company_names: dict[str, str] = {}
    for run_key in run_keys:
        manifest_path = Path(output_root) / "Y_Finance" / run_key / "manifest.json"
        if manifest_path.exists():
            manifest = load_json_file(manifest_path, f"Y-Finance manifest {run_key}")
            company_names[run_key] = str(manifest.get("company_name") or run_key.rsplit("_", 1)[0])
        else:
            company_names[run_key] = run_key.rsplit("_", 1)[0]
    return company_names


def _load_peer_market_datasets(output_root: Path, run_keys: tuple[str, ...]) -> dict[str, Any]:
    datasets = {}
    for run_key in run_keys:
        path = Path(output_root) / "Y_Finance" / run_key / "market_full_dataset.csv"
        datasets[run_key] = load_market_dataset(path)
    return datasets


def _load_peer_financial_reports(output_root: Path, run_keys: tuple[str, ...]) -> dict[str, dict[str, Any]]:
    reports = {}
    for run_key in run_keys:
        path = Path(output_root) / "Financial" / run_key / "final_report.json"
        reports[run_key] = load_json_file(path, f"Financial final report {run_key}")
    return reports
