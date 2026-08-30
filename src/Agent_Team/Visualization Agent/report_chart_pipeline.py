"""Writer-selected report chart catalog and deterministic generation."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

import pandas as pd

from orchestration.config import agent_output_dir

from chart_builders import (
    build_fundamental_margin_trend_chart,
    build_indexed_stock_vs_kospi_chart,
    build_liquidity_leverage_peer_comparison_chart,
    build_peer_profitability_comparison_chart,
    build_peer_return_comparison_chart,
    build_revenue_profit_sga_trend_chart,
    build_stock_price_ma_volume_chart,
)
from data_loader import (
    extract_financial_health_snapshot,
    extract_income_trend,
    extract_margin_trend,
    extract_peer_profitability_snapshot,
    extract_peer_return_snapshot,
    load_dart_index,
    load_json_file,
    load_market_dataset,
)
from manifest_builder import build_chart_manifest


CATALOG_VERSION = "writer_chart_catalog_v2"
SELECTION_VERSION = "writer_chart_selection_v2"
MAX_REPORT_CHARTS = 2


@dataclass(frozen=True)
class ReportChartConfig:
    output_root: Path
    run_key: str
    company_name: str
    output_dir: Path
    peer_run_keys: tuple[str, ...] = ()
    peer_output_root: Path | None = None

    @property
    def market_csv(self) -> Path:
        return agent_output_dir(self.output_root, self.run_key, "Y_Finance") / "market_full_dataset.csv"

    @property
    def dart_main(self) -> Path:
        return agent_output_dir(self.output_root, self.run_key, "Financial") / "dart_main.json"

    @property
    def peer_comparison_dataset(self) -> Path:
        return agent_output_dir(self.output_root, self.run_key, "Competitor") / "peer_comparison_dataset.json"

    @property
    def comparison_run_keys(self) -> tuple[str, ...]:
        return tuple(dict.fromkeys((self.run_key, *self.peer_run_keys)))

    def output_root_for_run(self, run_key: str) -> Path:
        if run_key == self.run_key or self.peer_output_root is None:
            return self.output_root
        return self.peer_output_root


CHART_SPECS: tuple[dict[str, Any], ...] = (
    {
        "chart_key": "stock_technical",
        "title": "주가·이동평균·거래량",
        "description": "분석기간의 주가 추세와 거래량을 제시한다.",
        "compatible_card_keys": ["market.absolute_trend", "market.momentum_volume"],
        "suitable_sections": ["사업·시장 현황", "투자의견 요약"],
        "interpretation_limit": "재무성과나 목표주가의 직접 근거로 사용할 수 없다.",
        "data_key": "market_technical",
    },
    {
        "chart_key": "stock_vs_kospi",
        "title": "대상기업과 코스피 지수화 성과",
        "description": "동일 시작점을 기준으로 대상기업 주가와 코스피 성과를 비교한다.",
        "compatible_card_keys": ["market.relative_performance"],
        "suitable_sections": ["사업·시장 현황", "투자의견 요약"],
        "interpretation_limit": "시작일 선택에 따라 상대성과의 크기가 달라질 수 있다.",
        "data_key": "market_vs_kospi",
    },
    {
        "chart_key": "profitability_margin",
        "title": "수익성 지표 추이",
        "description": "공헌이익률과 판관비율의 기간별 변화를 제시한다.",
        "compatible_card_keys": [
            "financial.same_period_trend",
            "financial.annual_trend",
        ],
        "suitable_sections": ["핵심 판단 근거"],
        "interpretation_limit": "누적기간 수치를 연간 확정치와 같은 기준으로 해석할 수 없다.",
        "data_key": "margin",
    },
    {
        "chart_key": "revenue_profit_sga",
        "title": "매출·공헌이익·판관비 추이",
        "description": "매출과 공헌이익, 판관비의 공시기간별 금액 흐름을 제시한다.",
        "compatible_card_keys": [
            "financial.same_period_trend",
            "financial.annual_trend",
        ],
        "suitable_sections": ["핵심 판단 근거"],
        "interpretation_limit": "서로 다른 누적기간을 단순 성장률로 비교할 수 없다.",
        "data_key": "income",
    },
    {
        "chart_key": "peer_return",
        "title": "기간 수익률 비교",
        "description": "대상기업의 시장 성과를 동일 기준일의 비교기업 지표와 대조한다.",
        "compatible_card_keys": ["peer.market_performance"],
        "suitable_sections": ["핵심 판단 근거"],
        "interpretation_limit": "비교기업 한 곳을 업종 평균으로 일반화할 수 없다.",
        "data_key": "peer_return",
    },
    {
        "chart_key": "peer_profitability",
        "title": "수익성 지표 비교",
        "description": "대상기업의 수익성 위치를 동일 절차로 산출한 비교기업 지표와 대조한다.",
        "compatible_card_keys": ["peer.profitability"],
        "suitable_sections": ["핵심 판단 근거"],
        "interpretation_limit": "비교기간과 회계기준이 일치하는 항목만 해석할 수 있다.",
        "data_key": "peer_profitability",
    },
    {
        "chart_key": "liquidity_leverage",
        "title": "유동성·자본구조 지표 비교",
        "description": "대상기업의 재무 여력을 동일 기준의 비교기업 지표와 대조한다.",
        "compatible_card_keys": ["peer.financial_position"],
        "suitable_sections": ["핵심 판단 근거"],
        "interpretation_limit": "재무 안정성만으로 성장성이나 주가 상승을 설명할 수 없다.",
        "data_key": "financial_health",
    },
)


def build_report_chart_catalog(config: ReportChartConfig) -> dict[str, Any]:
    """Return only charts whose required source data can be prepared."""

    data, failures, source_files = _prepare_chart_data(config)
    available: list[dict[str, Any]] = []
    unavailable: list[dict[str, str]] = []
    for spec in CHART_SPECS:
        public_spec = {key: value for key, value in spec.items() if key != "data_key"}
        data_key = str(spec["data_key"])
        if data_key in data:
            public_spec["chart_facts"] = _build_chart_facts(data_key, data[data_key])
            available.append(public_spec)
        else:
            unavailable.append(
                {
                    "chart_key": str(spec["chart_key"]),
                    "reason": failures.get(data_key, "required data is unavailable"),
                }
            )
    catalog = {
        "catalog_version": CATALOG_VERSION,
        "target_run_key": config.run_key,
        "company_name": config.company_name,
        "max_selected_charts": MAX_REPORT_CHARTS,
        "available_charts": available,
        "unavailable_charts": unavailable,
        "source_files": source_files,
    }
    config.output_dir.mkdir(parents=True, exist_ok=True)
    _write_json(config.output_dir / "chart_catalog.json", catalog)
    return catalog


def generate_requested_report_charts(
    config: ReportChartConfig,
    requested_chart_keys: list[str],
    selection_details: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Generate exactly the Writer-selected charts in the requested order."""

    requested = list(dict.fromkeys(str(value) for value in requested_chart_keys if str(value)))
    if len(requested) > MAX_REPORT_CHARTS:
        raise ValueError(f"Writer may select at most {MAX_REPORT_CHARTS} charts.")
    catalog = build_report_chart_catalog(config)
    available = {
        str(item["chart_key"]): item
        for item in catalog["available_charts"]
        if isinstance(item, dict) and item.get("chart_key")
    }
    unknown = [key for key in requested if key not in available]
    if unknown:
        raise ValueError(f"Writer selected unavailable chart key(s): {unknown}")
    normalized_details = _normalize_selection_details(requested, selection_details)

    data, _failures, source_files = _prepare_chart_data(config)
    figures_dir = config.output_dir / "figures"
    figures_dir.mkdir(parents=True, exist_ok=True)
    builders = _chart_builders(config, data, figures_dir)
    chart_metadata: list[dict[str, Any]] = []
    for order, chart_key in enumerate(requested, start=1):
        metadata = builders[chart_key]()
        metadata["chart_key"] = chart_key
        metadata["selected_order"] = order
        metadata["chart_facts"] = available[chart_key].get("chart_facts") or {}
        metadata["writer_selection"] = {
            **available[chart_key],
            **normalized_details.get(chart_key, {}),
        }
        chart_metadata.append(metadata)

    manifest = build_chart_manifest(
        company_name=config.company_name,
        run_key=config.run_key,
        source_files=source_files,
        chart_metadata=chart_metadata,
        output_path=config.output_dir / "chart_manifest.json",
    )
    selection = {
        "selection_version": SELECTION_VERSION,
        "target_run_key": config.run_key,
        "requested_chart_keys": requested,
        "generated_chart_keys": [str(item["chart_key"]) for item in chart_metadata],
        "selection_details": [normalized_details[key] for key in requested],
        "chart_manifest": str((config.output_dir / "chart_manifest.json").resolve()),
    }
    _write_json(config.output_dir / "chart_selection.json", selection)
    return {
        "catalog": catalog,
        "selection": selection,
        "manifest": manifest,
        "chart_manifest": str((config.output_dir / "chart_manifest.json").resolve()),
    }


def load_requested_chart_keys(path: str | Path) -> list[str]:
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    values = payload.get("requested_chart_keys") or []
    if not isinstance(values, list):
        raise ValueError("Writer requested_chart_keys must be an array.")
    return [str(value) for value in values]


def load_chart_selection_request(
    path: str | Path,
) -> tuple[list[str], list[dict[str, Any]]]:
    """Load Writer chart keys and their grounded reader-facing commentary."""

    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    values = payload.get("requested_chart_keys") or []
    details = payload.get("chart_selection_details") or []
    if not isinstance(values, list):
        raise ValueError("Writer requested_chart_keys must be an array.")
    if not isinstance(details, list):
        raise ValueError("Writer chart_selection_details must be an array.")
    return [str(value) for value in values], details


def _normalize_selection_details(
    requested: list[str],
    details: list[dict[str, Any]] | None,
) -> dict[str, dict[str, Any]]:
    if details is None:
        return {}
    if len(details) != len(requested):
        raise ValueError("Writer chart_selection_details must match requested_chart_keys.")
    normalized: dict[str, dict[str, Any]] = {}
    for index, item in enumerate(details):
        if not isinstance(item, dict):
            raise ValueError(f"Writer chart_selection_details[{index}] must be an object.")
        chart_key = str(item.get("chart_key") or "").strip()
        if chart_key != requested[index] or chart_key in normalized:
            raise ValueError("Writer chart selection detail order or uniqueness is invalid.")
        observation = str(item.get("chart_observation") or "").strip()
        interpretation = str(item.get("investment_interpretation") or "").strip()
        if not observation or not interpretation:
            raise ValueError(
                f"Writer chart commentary is incomplete for {chart_key}."
            )
        normalized[chart_key] = {
            "basis_card_keys": [
                str(value) for value in item.get("basis_card_keys") or []
            ],
            "selection_reason": str(item.get("selection_reason") or "").strip(),
            "chart_observation": observation,
            "investment_interpretation": interpretation,
        }
    return normalized


def _build_chart_facts(data_key: str, frame: pd.DataFrame) -> dict[str, Any]:
    """Expose compact display facts from the exact dataframe used for a chart."""

    if not isinstance(frame, pd.DataFrame) or frame.empty:
        return {}
    if data_key == "market_technical":
        chart_df = frame.dropna(subset=["date", "stock_close"]).sort_values("date")
        if chart_df.empty:
            return {}
        latest = chart_df.iloc[-1]
        return {
            "analysis_period": _date_span(chart_df),
            "latest_observation": {
                "date": _display_value("date", latest.get("date")),
                "closing_price": _display_value("stock_close", latest.get("stock_close")),
                "five_day_return": _display_value("stock_return_5d", latest.get("stock_return_5d")),
                "twenty_day_return": _display_value("stock_return_20d", latest.get("stock_return_20d")),
                "sixty_day_return": _display_value("stock_return_60d", latest.get("stock_return_60d")),
                "close_vs_ma20": _display_value("stock_close_to_ma20", latest.get("stock_close_to_ma20")),
                "close_vs_ma60": _display_value("stock_close_to_ma60", latest.get("stock_close_to_ma60")),
                "volume_vs_20day_average": _display_value(
                    "stock_volume_ratio_20", latest.get("stock_volume_ratio_20")
                ),
            },
        }
    if data_key == "market_vs_kospi":
        chart_df = frame.dropna(subset=["date", "stock_close", "kospi_close"]).sort_values("date")
        if chart_df.empty:
            return {}
        first = chart_df.iloc[0]
        latest = chart_df.iloc[-1]
        stock_return = (float(latest["stock_close"]) / float(first["stock_close"]) - 1.0) * 100.0
        kospi_return = (float(latest["kospi_close"]) / float(first["kospi_close"]) - 1.0) * 100.0
        return {
            "analysis_period": _date_span(chart_df),
            "period_performance": {
                "target_return": _format_number(stock_return, "%"),
                "kospi_return": _format_number(kospi_return, "%"),
                "target_minus_kospi": _format_number(stock_return - kospi_return, "%p"),
            },
            "latest_market_observation": {
                "five_day_excess_return": _display_value(
                    "stock_excess_return_5d", latest.get("stock_excess_return_5d")
                ),
                "twenty_day_excess_return": _display_value(
                    "stock_excess_return_20d", latest.get("stock_excess_return_20d")
                ),
                "sixty_day_relative_strength": _display_value(
                    "stock_relative_strength_60", latest.get("stock_relative_strength_60")
                ),
            },
        }

    fact_columns = {
        "margin": [
            "period_label",
            "contribution_margin_pct",
            "sga_margin_pct",
        ],
        "income": [
            "period_label",
            "revenue_krw_bn",
            "contribution_profit_krw_bn",
            "sga_krw_bn",
        ],
        "peer_return": [
            "company_name",
            "date",
            "stock_return_5d_pct",
            "stock_return_20d_pct",
            "stock_return_60d_pct",
            "stock_excess_return_20d_pct",
            "stock_relative_strength_60_pct",
        ],
        "peer_profitability": [
            "company_name",
            "financial_period",
            "revenue_100m",
            "contribution_margin_pct",
            "sga_margin_pct",
            "eps",
        ],
        "financial_health": [
            "company_name",
            "period_basis",
            "current_ratio_pct",
            "cash_ratio_pct",
            "equity_ratio_pct",
            "debt_to_equity_pct",
        ],
    }
    columns = [column for column in fact_columns.get(data_key, []) if column in frame.columns]
    if not columns:
        return {}
    rows = frame.sort_values("period_end").tail(4) if "period_end" in frame.columns else frame
    return {
        "observations": [
            {
                column: _display_value(column, value)
                for column, value in row.items()
                if not _is_missing(value)
            }
            for row in rows[columns].to_dict(orient="records")
        ]
    }


def _date_span(frame: pd.DataFrame) -> str:
    dates = pd.to_datetime(frame["date"], errors="coerce").dropna()
    if dates.empty:
        return ""
    return f"{dates.iloc[0].date().isoformat()}~{dates.iloc[-1].date().isoformat()}"


def _is_missing(value: Any) -> bool:
    try:
        return bool(pd.isna(value))
    except (TypeError, ValueError):
        return False


def _display_value(key: str, value: Any) -> str:
    if _is_missing(value):
        return ""
    if key == "date" or key.endswith("_date"):
        parsed = pd.to_datetime(value, errors="coerce")
        return parsed.date().isoformat() if not pd.isna(parsed) else str(value)
    if key == "stock_close":
        return f"{float(value):,.0f}원"
    if key in {
        "stock_return_5d",
        "stock_return_20d",
        "stock_return_60d",
        "stock_close_to_ma20",
        "stock_close_to_ma60",
        "stock_excess_return_5d",
        "stock_excess_return_20d",
        "stock_relative_strength_60",
    }:
        return _format_number(float(value) * 100.0, "%")
    if key.endswith("_pct"):
        return _format_number(float(value), "%")
    if key.endswith("_krw_bn"):
        return f"{float(value):,.1f}십억원"
    if key.endswith("_100m"):
        return f"{float(value):,.0f}억원"
    if key == "eps":
        return f"{float(value):,.0f}원"
    if key == "stock_volume_ratio_20":
        return f"{float(value):.2f}배"
    if isinstance(value, (int, float)):
        return _format_number(float(value), "")
    return str(value)


def _format_number(value: float, suffix: str) -> str:
    return f"{value:+.2f}{suffix}" if suffix in {"%", "%p"} else f"{value:,.2f}{suffix}"


def _prepare_chart_data(
    config: ReportChartConfig,
) -> tuple[dict[str, Any], dict[str, str], dict[str, Any]]:
    data: dict[str, Any] = {}
    failures: dict[str, str] = {}
    source_files: dict[str, Any] = {
        "market_full_dataset": str(config.market_csv.resolve()),
        "dart_main": str(config.dart_main.resolve()),
        "peer_comparison_dataset": str(config.peer_comparison_dataset.resolve()),
        "peer_market_datasets": {},
        "peer_financial_reports": {},
    }

    try:
        market = load_market_dataset(config.market_csv)
        if market.empty:
            raise ValueError("market dataset has no usable rows")
        data["market_technical"] = market
        if "kospi_close" not in market.columns or market[
            ["stock_close", "kospi_close"]
        ].dropna().empty:
            failures["market_vs_kospi"] = "market dataset has no usable KOSPI observations"
        else:
            data["market_vs_kospi"] = market
    except Exception as exc:
        failure = _failure_text(exc)
        failures["market_technical"] = failure
        failures["market_vs_kospi"] = failure

    try:
        dart_index = load_dart_index(config.dart_main)
        data["margin"] = extract_margin_trend(dart_index)
    except Exception as exc:
        failures["margin"] = _failure_text(exc)
    try:
        dart_index = load_dart_index(config.dart_main)
        data["income"] = extract_income_trend(dart_index)
    except Exception as exc:
        failures["income"] = _failure_text(exc)

    try:
        market_datasets: dict[str, Any] = {}
        company_names: dict[str, str] = {}
        for run_key in config.comparison_run_keys:
            run_root = config.output_root_for_run(run_key)
            path = agent_output_dir(run_root, run_key, "Y_Finance") / "market_full_dataset.csv"
            source_files["peer_market_datasets"][run_key] = str(path.resolve())
            market_datasets[run_key] = load_market_dataset(path)
            company_names[run_key] = _company_name(run_root, run_key)
        if len(market_datasets) < 2:
            raise ValueError("target and one peer market dataset are required")
        data["peer_return"] = extract_peer_return_snapshot(market_datasets, company_names)
    except Exception as exc:
        failures["peer_return"] = _failure_text(exc)

    try:
        reports: dict[str, dict[str, Any]] = {}
        company_names = {}
        for run_key in config.comparison_run_keys:
            run_root = config.output_root_for_run(run_key)
            path = agent_output_dir(run_root, run_key, "Financial") / "final_report.json"
            source_files["peer_financial_reports"][run_key] = str(path.resolve())
            reports[run_key] = load_json_file(path, f"Financial report {run_key}")
            company_names[run_key] = _company_name(run_root, run_key)
        if len(reports) < 2:
            raise ValueError("target and one peer financial report are required")
        data["financial_health"] = extract_financial_health_snapshot(reports, company_names)
    except Exception as exc:
        failures["financial_health"] = _failure_text(exc)

    try:
        comparison = load_json_file(
            config.peer_comparison_dataset,
            "Peer comparison dataset",
        )
        data["peer_profitability"] = extract_peer_profitability_snapshot(comparison)
    except Exception as exc:
        failures["peer_profitability"] = _failure_text(exc)
    return data, failures, source_files


def _chart_builders(
    config: ReportChartConfig,
    data: dict[str, Any],
    figures_dir: Path,
) -> dict[str, Callable[[], dict[str, Any]]]:
    def paths(chart_key: str) -> tuple[Path, Path]:
        return figures_dir / f"{chart_key}.pdf", figures_dir / f"{chart_key}.png"

    return {
        "stock_technical": lambda: build_stock_price_ma_volume_chart(
            market_df=data["market_technical"],
            output_pdf=paths("stock_technical")[0],
            output_png=paths("stock_technical")[1],
            company_name=config.company_name,
        ),
        "stock_vs_kospi": lambda: build_indexed_stock_vs_kospi_chart(
            market_df=data["market_vs_kospi"],
            output_pdf=paths("stock_vs_kospi")[0],
            output_png=paths("stock_vs_kospi")[1],
            company_name=config.company_name,
        ),
        "profitability_margin": lambda: build_fundamental_margin_trend_chart(
            margin_df=data["margin"],
            output_pdf=paths("profitability_margin")[0],
            output_png=paths("profitability_margin")[1],
            company_name=config.company_name,
        ),
        "revenue_profit_sga": lambda: build_revenue_profit_sga_trend_chart(
            income_df=data["income"],
            output_pdf=paths("revenue_profit_sga")[0],
            output_png=paths("revenue_profit_sga")[1],
            company_name=config.company_name,
        ),
        "peer_return": lambda: build_peer_return_comparison_chart(
            peer_return_df=data["peer_return"],
            output_pdf=paths("peer_return")[0],
            output_png=paths("peer_return")[1],
        ),
        "peer_profitability": lambda: build_peer_profitability_comparison_chart(
            peer_profitability_df=data["peer_profitability"],
            output_pdf=paths("peer_profitability")[0],
            output_png=paths("peer_profitability")[1],
        ),
        "liquidity_leverage": lambda: build_liquidity_leverage_peer_comparison_chart(
            financial_health_df=data["financial_health"],
            output_pdf=paths("liquidity_leverage")[0],
            output_png=paths("liquidity_leverage")[1],
        ),
    }


def _company_name(output_root: Path, run_key: str) -> str:
    manifest_path = agent_output_dir(output_root, run_key, "Y_Finance") / "manifest.json"
    if manifest_path.is_file():
        try:
            payload = load_json_file(manifest_path, f"YFinance manifest {run_key}")
            value = str(payload.get("company_name") or "").strip()
            if value:
                return value
        except Exception:
            pass
    return run_key.rsplit("_", 1)[0]


def _failure_text(exc: Exception) -> str:
    return f"{type(exc).__name__}: {exc}"


def _write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


__all__ = [
    "CATALOG_VERSION",
    "MAX_REPORT_CHARTS",
    "ReportChartConfig",
    "build_report_chart_catalog",
    "generate_requested_report_charts",
    "load_chart_selection_request",
    "load_requested_chart_keys",
]
