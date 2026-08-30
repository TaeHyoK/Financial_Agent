"""Domestic peer comparison v1 built from existing agent outputs.

This module compares one explicitly selected domestic peer from existing agent
outputs. Global-peer and complete industry-average claims remain out of scope.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pandas as pd
from orchestration.config import agent_output_dir

from . import OUTPUT_ROOT
from .identity import (
    RunIdentity,
    build_run_key,
    company_from_run_key,
    discover_competitor_identities,
    load_identity_from_config,
    normalize_date,
)


OUTPUT_VERSION = "1.0"
EXCLUDED_SCOPE = [
    "global_peer_comparison",
    "complete_industry_average_comparison",
]


@dataclass(frozen=True)
class PeerComparisonPaths:
    """Paths written by Peer Comparison Agent v1."""

    dataset_json: Path


def generate_peer_comparison(
    *,
    target: RunIdentity,
    peer_run_keys: list[str] | None = None,
    output_root: Path = OUTPUT_ROOT,
    peer_output_root: Path | None = None,
    output_dir: Path | None = None,
) -> PeerComparisonPaths:
    """Generate domestic-only peer comparison artifacts for one target run."""

    output_root = Path(output_root).expanduser().resolve()
    resolved_peer_root = (
        Path(peer_output_root).expanduser().resolve()
        if peer_output_root is not None
        else output_root
    )
    resolved_target = _resolve_target_identity(target, output_root)
    peers = _resolve_peer_identities(
        target=resolved_target,
        output_root=resolved_peer_root,
        peer_run_keys=peer_run_keys or [],
    )
    run_identities = [resolved_target, *peers]
    rows = [
        _build_company_metric_row(
            identity=identity,
            output_root=(
                output_root
                if identity.run_key == resolved_target.run_key
                else resolved_peer_root
            ),
            peer_group="target" if identity.run_key == resolved_target.run_key else "domestic_peer",
        )
        for identity in run_identities
    ]
    dataset = _build_dataset_payload(
        target=resolved_target,
        peers=peers,
        rows=rows,
        output_root=output_root,
        peer_output_root=resolved_peer_root,
    )
    destination = output_dir or agent_output_dir(
        output_root, resolved_target.run_key, "Competitor"
    )
    destination = Path(destination).expanduser().resolve()
    destination.mkdir(parents=True, exist_ok=True)
    dataset_json = destination / "peer_comparison_dataset.json"
    _write_json(dataset_json, dataset)

    return PeerComparisonPaths(dataset_json=dataset_json)


def _resolve_target_identity(target: RunIdentity, output_root: Path) -> RunIdentity:
    run_key = target.run_key
    if not run_key:
        raise ValueError("target.run_key is required for peer comparison.")
    financial_path = agent_output_dir(output_root, run_key, "Financial") / "final_report.json"
    yfinance_manifest = agent_output_dir(output_root, run_key, "Y_Finance") / "manifest.json"
    company_name = target.company_name or company_from_run_key(run_key)
    ticker = target.ticker
    if yfinance_manifest.exists():
        manifest = _load_json(yfinance_manifest)
        company_name = str(manifest.get("company_name") or company_name)
        ticker = str(manifest.get("ticker") or ticker or "").strip() or ticker
    elif financial_path.exists():
        report = _load_json(financial_path)
        company_name = str(report.get("target_company") or report.get("company_name") or company_name)
    selected_date = target.selected_date or _date_suffix(run_key)
    return RunIdentity(
        run_key=run_key,
        company_name=company_name,
        selected_date=selected_date,
        ticker=ticker,
        corp_code=target.corp_code,
        stock_code=target.stock_code,
    )


def _resolve_peer_identities(
    *,
    target: RunIdentity,
    output_root: Path,
    peer_run_keys: list[str],
) -> list[RunIdentity]:
    if peer_run_keys:
        candidates = [
            RunIdentity(run_key=run_key, company_name=company_from_run_key(run_key), selected_date=target.selected_date)
            for run_key in peer_run_keys
            if run_key != target.run_key
        ]
    else:
        candidates = discover_competitor_identities(
            output_root=output_root,
            target=target,
            selected_date=target.selected_date,
            include_partial=True,
        )
    peers: list[RunIdentity] = []
    for identity in candidates:
        if not _has_required_peer_files(output_root, identity.run_key):
            continue
        peers.append(_resolve_target_identity(identity, output_root))
    peers.sort(key=lambda item: item.run_key)
    return peers


def _has_required_peer_files(output_root: Path, run_key: str) -> bool:
    return (
        (agent_output_dir(output_root, run_key, "Financial") / "final_report.json").exists()
        and (agent_output_dir(output_root, run_key, "Y_Finance") / "market_full_dataset.csv").exists()
    )


def _build_company_metric_row(*, identity: RunIdentity, output_root: Path, peer_group: str) -> dict[str, Any]:
    financial_path = agent_output_dir(output_root, identity.run_key, "Financial") / "final_report.json"
    market_path = agent_output_dir(output_root, identity.run_key, "Y_Finance") / "market_full_dataset.csv"
    yfinance_path = agent_output_dir(output_root, identity.run_key, "Y_Finance") / "final_report.json"
    financial = _load_json(financial_path)
    market = _latest_market_row(market_path)
    yfinance = _load_json(yfinance_path)
    detailed = financial.get("detailed_analysis", {}) if isinstance(financial, dict) else {}

    revenue = _supporting_features(detailed, "revenue")
    margin = _supporting_features(detailed, "margin")
    expense = _supporting_features(detailed, "expense_efficiency")
    eps = _supporting_features(detailed, "eps")
    cash_flow = _supporting_features(detailed, "cash_flow")
    balance = _supporting_features(detailed, "balance_sheet")
    capital = _supporting_features(detailed, "capital_structure")
    liquidity = _supporting_features(detailed, "liquidity")
    normalized = (
        ((financial.get("financial_trends") or {}).get("normalized_metrics") or {}).get("current_values")
        or {}
    )

    row = {
        "company_name": identity.company_name or company_from_run_key(identity.run_key),
        "run_key": identity.run_key,
        "peer_group": peer_group,
        "ticker": identity.ticker,
        "as_of_date": _date_suffix(identity.run_key),
        "financial_metrics": {
            "revenue_100m": _krw_to_100m(revenue.get("revenue")),
            "revenue_growth_pct": _ratio_to_pct(revenue.get("revenue_growth")),
            "revenue_growth_basis": revenue.get("period"),
            "contribution_margin_pct": _ratio_to_pct(margin.get("contribution_margin")),
            "sga_margin_pct": _ratio_to_pct(expense.get("sga_margin")),
            "operating_margin_pct": _ratio_to_pct(normalized.get("operating_margin")),
            "net_margin_pct": _ratio_to_pct(normalized.get("net_margin")),
            "operating_cash_flow_margin_pct": _ratio_to_pct(
                normalized.get("operating_cash_flow_margin")
            ),
            "eps": _number(eps.get("eps")),
            "operating_cash_flow_100m": _krw_to_100m(cash_flow.get("operating_cash_flow")),
            "cash_and_equivalents_100m": _krw_to_100m(
                balance.get("cash_and_cash_equivalents") or liquidity.get("cash_and_cash_equivalents")
            ),
            "debt_ratio_pct": _ratio_to_pct(capital.get("debt_to_equity")),
            "current_ratio_pct": _ratio_to_pct(liquidity.get("current_ratio")),
            "cash_ratio_pct": _ratio_to_pct(liquidity.get("cash_ratio")),
            "equity_ratio_pct": _ratio_to_pct(capital.get("equity_ratio")),
            "financial_period": revenue.get("period") or margin.get("period") or eps.get("period"),
            "balance_sheet_basis": balance.get("period_basis") or capital.get("period_basis") or liquidity.get("period_basis"),
        },
        "market_metrics": {
            "market_date": market.get("date"),
            "stock_return_5d_pct": _ratio_to_pct(market.get("stock_return_5d")),
            "stock_return_20d_pct": _ratio_to_pct(market.get("stock_return_20d")),
            "stock_return_60d_pct": _ratio_to_pct(market.get("stock_return_60d")),
            "stock_excess_return_20d_pct": _ratio_to_pct(market.get("stock_excess_return_20d")),
            "stock_relative_strength_60_pct": _ratio_to_pct(market.get("stock_relative_strength_60")),
            "stock_volume_ratio_20": _number(market.get("stock_volume_ratio_20")),
        },
        "valuation_metrics": _valuation_metrics(yfinance),
    }
    row["data_quality"] = {"missing_fields": _missing_fields(row)}
    return row


def _build_dataset_payload(
    *,
    target: RunIdentity,
    peers: list[RunIdentity],
    rows: list[dict[str, Any]],
    output_root: Path,
    peer_output_root: Path,
) -> dict[str, Any]:
    source_files = {
        row["run_key"]: {
            "financial_final_report": str(agent_output_dir(
                output_root if row["run_key"] == target.run_key else peer_output_root,
                row["run_key"],
                "Financial",
            ) / "final_report.json"),
            "market_full_dataset": str(agent_output_dir(
                output_root if row["run_key"] == target.run_key else peer_output_root,
                row["run_key"],
                "Y_Finance",
            ) / "market_full_dataset.csv"),
            "yfinance_final_report": str(agent_output_dir(
                output_root if row["run_key"] == target.run_key else peer_output_root,
                row["run_key"],
                "Y_Finance",
            ) / "final_report.json"),
        }
        for row in rows
    }
    return {
        "agent_name": "Peer Comparison Agent",
        "role": "domestic-only peer comparison dataset builder",
        "output_version": OUTPUT_VERSION,
        "target_run_key": target.run_key,
        "target_company": target.company_name,
        "peer_scope": "domestic_only",
        "excluded_scope": EXCLUDED_SCOPE,
        "peer_groups": {
            "target": {"run_key": target.run_key, "company_name": target.company_name},
            "domestic_peers": [
                {"run_key": peer.run_key, "company_name": peer.company_name}
                for peer in peers
            ],
            "global_peers": [],
        },
        "metrics": rows,
        "comparison_limits": [
            "국내 비교군은 현재 확보된 동일 기준일 국내 비교 대상만 포함한다.",
            "글로벌 peer 비교는 현재 데이터 범위에 없어 생성하지 않는다.",
            "기준일 계산 P/E, P/B, P/S는 동일 기준일끼리 비교하며 직접 YFinance EV 배수는 제공자 표시일을 별도로 유지한다.",
            "완전한 업종 평균 비교는 업종 전체 표본이 없어 생성하지 않는다.",
            "누적 기간과 연간 기준이 섞일 수 있어 기간 기준을 확인한 뒤 해석해야 한다.",
        ],
        "source_files": source_files,
    }


def _supporting_features(detailed: dict[str, Any], section: str) -> dict[str, Any]:
    value = detailed.get(section, {})
    if not isinstance(value, dict):
        return {}
    features = value.get("supporting_features", {})
    return features if isinstance(features, dict) else {}


def _latest_market_row(path: Path) -> dict[str, Any]:
    df = pd.read_csv(path)
    if df.empty:
        return {}
    if "date" in df.columns:
        df["date"] = pd.to_datetime(df["date"], errors="coerce")
        df = df.sort_values("date")
    latest = df.iloc[-1].to_dict()
    if isinstance(latest.get("date"), pd.Timestamp):
        latest["date"] = latest["date"].strftime("%Y-%m-%d")
    return latest


def _valuation_metrics(report: dict[str, Any]) -> dict[str, Any]:
    snapshot = report.get("valuation_snapshot") or {}
    calculated = snapshot.get("calculated_from_close_and_dart") or {}
    calculated_metrics = calculated.get("metrics") or {}
    direct = snapshot.get("direct_yfinance") or {}
    direct_latest = direct.get("latest_period") or {}
    direct_metrics = direct_latest.get("metrics") or {}

    def calculated_value(key: str) -> float | None:
        return _number((calculated_metrics.get(key) or {}).get("value"))

    def direct_value(key: str) -> float | None:
        return _number((direct_metrics.get(key) or {}).get("value"))

    market_cap = calculated_value("market_cap")
    return {
        "calculated_as_of_date": calculated.get("as_of_date"),
        "market_cap_100m_krw": market_cap / 100_000_000 if market_cap is not None else None,
        "trailing_pe": calculated_value("trailing_pe"),
        "price_to_book": calculated_value("price_to_book"),
        "price_to_sales": calculated_value("price_to_sales"),
        "direct_valuation_date": direct_latest.get("valuation_date"),
        "enterprise_value_100m_krw": (
            direct_value("enterprise_value") / 100_000_000
            if direct_value("enterprise_value") is not None
            else None
        ),
        "enterprise_value_to_revenue": direct_value("enterprise_value_to_revenue"),
        "enterprise_value_to_ebitda": direct_value("enterprise_value_to_ebitda"),
    }


def _missing_fields(row: dict[str, Any]) -> list[str]:
    missing: list[str] = []
    for section_key in ["financial_metrics", "market_metrics", "valuation_metrics"]:
        section = row.get(section_key, {})
        for key, value in section.items():
            if key.endswith("_basis") or key.endswith("_period") or key.endswith("_date") or key == "market_date":
                continue
            if value is None:
                missing.append(f"{section_key}.{key}")
    return missing


def _krw_to_100m(value: Any) -> float | None:
    numeric = _number(value)
    if numeric is None:
        return None
    return numeric / 100_000_000


def _ratio_to_pct(value: Any) -> float | None:
    numeric = _number(value)
    if numeric is None:
        return None
    return numeric * 100.0


def _number(value: Any) -> float | None:
    if value is None:
        return None
    try:
        if pd.isna(value):
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def _date_suffix(run_key: str) -> str | None:
    suffix = run_key.rsplit("_", 1)[-1]
    if len(suffix) == 8 and suffix.isdigit():
        return suffix
    return None


def _load_json(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as file:
        payload = json.load(file)
    if not isinstance(payload, dict):
        raise ValueError(f"Expected JSON object: {path}")
    return payload


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


__all__ = [
    "EXCLUDED_SCOPE",
    "OUTPUT_VERSION",
    "PeerComparisonPaths",
    "generate_peer_comparison",
    "load_identity_from_config",
    "RunIdentity",
    "build_run_key",
    "normalize_date",
]
