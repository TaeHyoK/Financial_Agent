"""Data loading and deterministic transformations for the Visualization Agent."""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd


logger = logging.getLogger(__name__)

REQUIRED_MARKET_COLUMNS = [
    "date",
    "stock_close",
    "stock_close_to_ma20",
    "stock_close_to_ma60",
    "stock_volume_ratio_20",
    "stock_excess_return_20d",
    "stock_relative_strength_60",
]

OPTIONAL_MARKET_COLUMNS = [
    "stock_return_5d",
    "stock_return_20d",
    "stock_return_60d",
    "stock_rsi_14",
    "stock_macd_hist",
    "stock_volatility_20",
    "kospi_close",
    "kospi_return_20d",
    "fx_close",
]

REQUIRED_DART_METRICS = ["contribution_margin", "sga_margin"]
OPTIONAL_DART_METRICS = ["revenue", "contribution_profit", "eps"]
INCOME_TREND_METRICS = ["revenue", "contribution_profit", "sga"]


def ensure_file_exists(path: str | Path, label: str) -> Path:
    """Return an absolute path if it exists, otherwise raise a clear error."""

    resolved = Path(path).expanduser().resolve()
    if not resolved.exists():
        raise FileNotFoundError(f"{label} file not found: {resolved}")
    if not resolved.is_file():
        raise FileNotFoundError(f"{label} path is not a file: {resolved}")
    return resolved


def load_market_dataset(path: str | Path) -> pd.DataFrame:
    """Load market_full_dataset.csv, validate schema, parse dates, and derive chart fields."""

    csv_path = ensure_file_exists(path, "Market dataset")
    df = pd.read_csv(csv_path)
    source_row_count = int(len(df))

    missing_columns = [column for column in REQUIRED_MARKET_COLUMNS if column not in df.columns]
    if missing_columns:
        raise ValueError(f"Market dataset missing required columns: {missing_columns}")

    df = df.copy()
    df["date"] = pd.to_datetime(df["date"], errors="coerce")
    invalid_date_count = int(df["date"].isna().sum())
    if invalid_date_count:
        raise ValueError(f"Market dataset has {invalid_date_count} invalid date value(s).")

    numeric_columns = [column for column in df.columns if column != "date"]
    for column in numeric_columns:
        df[column] = pd.to_numeric(df[column], errors="coerce")

    duplicate_date_count = int(df.duplicated(subset=["date"]).sum())
    if duplicate_date_count:
        logger.warning("Market dataset has %s duplicate date row(s); keeping the last row per date.", duplicate_date_count)
        df = df.drop_duplicates(subset=["date"], keep="last")

    null_stock_close_count = int(df["stock_close"].isna().sum())
    if null_stock_close_count:
        logger.warning("Market dataset has %s row(s) without stock_close; excluding them from chart data.", null_stock_close_count)
        df = df[df["stock_close"].notna()]

    df = df.sort_values("date").reset_index(drop=True)
    df["derived_ma20"] = _derive_moving_average(df["stock_close"], df["stock_close_to_ma20"])
    df["derived_ma60"] = _derive_moving_average(df["stock_close"], df["stock_close_to_ma60"])
    df["stock_excess_return_20d_pct"] = df["stock_excess_return_20d"] * 100.0
    df["stock_relative_strength_60_pct"] = df["stock_relative_strength_60"] * 100.0
    df.attrs["source_path"] = str(csv_path)
    df.attrs["source_row_count"] = source_row_count
    df.attrs["duplicate_date_count"] = duplicate_date_count
    df.attrs["dropped_stock_close_null_count"] = null_stock_close_count
    return df


def _derive_moving_average(stock_close: pd.Series, close_to_ma_ratio: pd.Series) -> pd.Series:
    """Reverse close-to-MA ratio into the moving average value."""

    denominator = 1.0 + close_to_ma_ratio
    valid_denominator = denominator.notna() & (denominator != 0)
    return pd.Series(
        np.where(valid_denominator, stock_close / denominator, np.nan),
        index=stock_close.index,
        dtype="float64",
    )


def load_dart_index(path: str | Path) -> dict[str, Any]:
    """Load dart_main.json and validate the margin metrics required for charting."""

    json_path = ensure_file_exists(path, "DART main")
    with json_path.open("r", encoding="utf-8") as file:
        dart_index = json.load(file)

    if not isinstance(dart_index, dict):
        raise ValueError("DART main must be a JSON object.")
    if not isinstance(dart_index.get("periods"), dict):
        raise ValueError("DART main missing required object: periods")
    metrics_by_key = dart_index.get("metrics_by_key")
    if not isinstance(metrics_by_key, dict):
        raise ValueError("DART main missing required object: metrics_by_key")

    missing_metrics = [metric for metric in REQUIRED_DART_METRICS if metric not in metrics_by_key]
    if missing_metrics:
        raise ValueError(f"DART main missing required metrics: {missing_metrics}")

    for metric in REQUIRED_DART_METRICS:
        values = metrics_by_key[metric].get("values_by_period")
        if not isinstance(values, dict) or not values:
            raise ValueError(f"DART metric '{metric}' missing values_by_period.")

    return dart_index


def load_json_file(path: str | Path, label: str) -> dict[str, Any]:
    """Load a required JSON object from disk."""

    json_path = ensure_file_exists(path, label)
    with json_path.open("r", encoding="utf-8") as file:
        payload = json.load(file)
    if not isinstance(payload, dict):
        raise ValueError(f"{label} must be a JSON object: {json_path}")
    return payload


def extract_peer_profitability_snapshot(peer_comparison_dataset: dict[str, Any]) -> pd.DataFrame:
    """Build peer profitability snapshot from Peer Comparison Agent v1 output."""

    metrics = peer_comparison_dataset.get("metrics", [])
    if not isinstance(metrics, list) or not metrics:
        raise ValueError("Peer comparison dataset has no metrics rows.")

    rows: list[dict[str, Any]] = []
    for item in metrics:
        if not isinstance(item, dict):
            continue
        financial = item.get("financial_metrics", {})
        if not isinstance(financial, dict):
            financial = {}
        rows.append(
            {
                "run_key": item.get("run_key"),
                "company_name": item.get("company_name") or str(item.get("run_key") or "").rsplit("_", 1)[0],
                "peer_group": item.get("peer_group", "domestic_peer"),
                "revenue_100m": _optional_numeric_value(financial.get("revenue_100m")),
                "contribution_margin_pct": _optional_numeric_value(financial.get("contribution_margin_pct")),
                "sga_margin_pct": _optional_numeric_value(financial.get("sga_margin_pct")),
                "eps": _optional_numeric_value(financial.get("eps")),
                "operating_cash_flow_100m": _optional_numeric_value(financial.get("operating_cash_flow_100m")),
                "financial_period": financial.get("financial_period"),
            }
        )

    snapshot_df = pd.DataFrame(rows)
    required_metrics = ["revenue_100m", "contribution_margin_pct", "sga_margin_pct", "eps"]
    if snapshot_df.empty or snapshot_df[required_metrics].isna().all(axis=None):
        raise ValueError("Peer profitability snapshot has no usable financial comparison data.")
    return snapshot_df


def extract_margin_trend(dart_index: dict[str, Any]) -> pd.DataFrame:
    """Extract contribution_margin and sga_margin by period in period_end order."""

    metrics_by_key = dart_index["metrics_by_key"]
    contribution_values = metrics_by_key["contribution_margin"]["values_by_period"]
    sga_values = metrics_by_key["sga_margin"]["values_by_period"]

    common_period_keys = sorted(set(contribution_values) & set(sga_values))
    if not common_period_keys:
        raise ValueError("No common DART periods found for contribution_margin and sga_margin.")

    rows: list[dict[str, Any]] = []
    for period_key in common_period_keys:
        contribution_entry = contribution_values[period_key]
        sga_entry = sga_values[period_key]
        period = contribution_entry.get("period") or sga_entry.get("period") or {}
        period_end = pd.to_datetime(period.get("period_end"), errors="coerce")
        if pd.isna(period_end):
            raise ValueError(f"DART period '{period_key}' has an invalid period_end.")

        contribution_value = _numeric_metric_value(contribution_entry, "contribution_margin", period_key)
        sga_value = _numeric_metric_value(sga_entry, "sga_margin", period_key)
        rows.append(
            {
                "period_key": period_key,
                "period_label": format_period_label(period),
                "period_end": period_end,
                "fiscal_year": period.get("fiscal_year"),
                "period_type": period.get("period_type"),
                "basis": period.get("basis"),
                "contribution_margin": contribution_value,
                "sga_margin": sga_value,
                "contribution_margin_pct": contribution_value * 100.0,
                "sga_margin_pct": sga_value * 100.0,
            }
        )

    margin_df = pd.DataFrame(rows).sort_values("period_end").reset_index(drop=True)
    if margin_df.empty:
        raise ValueError("DART margin trend is empty after extraction.")
    return margin_df


def extract_income_trend(dart_index: dict[str, Any]) -> pd.DataFrame:
    """Extract revenue, contribution profit, and SG&A trend in KRW from dart_main."""

    metrics_by_key = dart_index.get("metrics_by_key", {})
    missing_metrics = [metric for metric in INCOME_TREND_METRICS if metric not in metrics_by_key]
    if missing_metrics:
        raise ValueError(f"DART main missing income trend metrics: {missing_metrics}")

    period_keys = sorted(
        set().union(
            *[
                set(metrics_by_key[metric].get("values_by_period", {}).keys())
                for metric in INCOME_TREND_METRICS
            ]
        )
    )
    if not period_keys:
        raise ValueError("No DART periods found for income trend metrics.")

    rows: list[dict[str, Any]] = []
    for period_key in period_keys:
        period = _period_for_metric(metrics_by_key, period_key)
        period_end = pd.to_datetime(period.get("period_end"), errors="coerce")
        if pd.isna(period_end):
            raise ValueError(f"DART period '{period_key}' has an invalid period_end.")

        row: dict[str, Any] = {
            "period_key": period_key,
            "period_label": format_period_label(period),
            "period_end": period_end,
            "fiscal_year": period.get("fiscal_year"),
            "period_type": period.get("period_type"),
            "basis": period.get("basis"),
        }
        for metric in INCOME_TREND_METRICS:
            entry = metrics_by_key[metric].get("values_by_period", {}).get(period_key, {})
            row[metric] = _optional_numeric_value(entry.get("value"))
            row[f"{metric}_krw_bn"] = row[metric] / 1_000_000_000 if row[metric] is not None else np.nan
            row[f"{metric}_status"] = entry.get("status")
        rows.append(row)

    income_df = pd.DataFrame(rows).sort_values("period_end").reset_index(drop=True)
    if income_df[INCOME_TREND_METRICS].isna().all(axis=None):
        raise ValueError("Income trend metrics are all missing.")
    return income_df


def extract_peer_return_snapshot(
    market_datasets: dict[str, pd.DataFrame],
    company_names: dict[str, str],
) -> pd.DataFrame:
    """Build latest return and relative-strength snapshot for peer market comparison."""

    required_columns = [
        "date",
        "stock_return_5d",
        "stock_return_20d",
        "stock_return_60d",
        "stock_excess_return_20d",
        "stock_relative_strength_60",
        "stock_rsi_14",
        "stock_macd_hist",
        "stock_volatility_20",
        "stock_volume_ratio_20",
    ]
    rows: list[dict[str, Any]] = []
    for run_key, df in market_datasets.items():
        missing_columns = [column for column in required_columns if column not in df.columns]
        if missing_columns:
            raise ValueError(f"Peer market dataset '{run_key}' missing required columns: {missing_columns}")
        latest = df.sort_values("date").iloc[-1]
        rows.append(
            {
                "run_key": run_key,
                "company_name": company_names.get(run_key, run_key.rsplit("_", 1)[0]),
                "date": latest["date"],
                "stock_return_5d_pct": latest["stock_return_5d"] * 100.0,
                "stock_return_20d_pct": latest["stock_return_20d"] * 100.0,
                "stock_return_60d_pct": latest["stock_return_60d"] * 100.0,
                "stock_excess_return_20d_pct": latest["stock_excess_return_20d"] * 100.0,
                "stock_relative_strength_60_pct": latest["stock_relative_strength_60"] * 100.0,
                "stock_rsi_14": latest["stock_rsi_14"],
                "stock_macd_hist": latest["stock_macd_hist"],
                "stock_volatility_20_pct": latest["stock_volatility_20"] * 100.0,
                "stock_volume_ratio_20": latest["stock_volume_ratio_20"],
            }
        )
    return pd.DataFrame(rows)


def extract_financial_health_snapshot(
    financial_reports: dict[str, dict[str, Any]],
    company_names: dict[str, str],
) -> pd.DataFrame:
    """Extract peer liquidity and leverage ratios from Financial Agent final reports."""

    rows: list[dict[str, Any]] = []
    for run_key, report in financial_reports.items():
        detailed = report.get("detailed_analysis", {})
        capital = detailed.get("capital_structure", {}).get("supporting_features", {})
        liquidity = detailed.get("liquidity", {}).get("supporting_features", {})
        row = {
            "run_key": run_key,
            "company_name": company_names.get(run_key, report.get("target_company") or run_key.rsplit("_", 1)[0]),
            "current_ratio_pct": _optional_numeric_value(liquidity.get("current_ratio")) * 100.0
            if liquidity.get("current_ratio") is not None
            else np.nan,
            "cash_ratio_pct": _optional_numeric_value(liquidity.get("cash_ratio")) * 100.0
            if liquidity.get("cash_ratio") is not None
            else np.nan,
            "equity_ratio_pct": _optional_numeric_value(capital.get("equity_ratio")) * 100.0
            if capital.get("equity_ratio") is not None
            else np.nan,
            "debt_to_equity_pct": _optional_numeric_value(capital.get("debt_to_equity")) * 100.0
            if capital.get("debt_to_equity") is not None
            else np.nan,
            "period_basis": capital.get("period_basis") or liquidity.get("period_basis"),
        }
        rows.append(row)

    snapshot_df = pd.DataFrame(rows)
    required_metrics = ["current_ratio_pct", "cash_ratio_pct", "equity_ratio_pct", "debt_to_equity_pct"]
    if snapshot_df[required_metrics].isna().all(axis=None):
        raise ValueError("Peer financial health snapshot has no usable ratio data.")
    return snapshot_df


def format_period_label(period: dict[str, Any]) -> str:
    """Format DART period metadata into Writer-safe labels."""

    fiscal_year = period.get("fiscal_year")
    period_type = str(period.get("period_type") or "").upper()
    basis = str(period.get("basis") or "").upper()
    year_label = f"{fiscal_year}년" if fiscal_year else ""
    if basis == "TTM" or period_type == "TTM":
        return " ".join(part for part in [year_label, "최근 12개월"] if part)
    if basis == "FULL_YEAR" or period_type == "ANNUAL":
        return f"{fiscal_year}년 연간"
    period_labels = {
        "Q1": "1분기",
        "Q2": "2분기",
        "HALF": "반기",
        "Q3": "3분기",
        "Q4": "4분기",
    }
    period_label = period_labels.get(period_type, period_type)
    if basis == "YTD":
        period_label = f"{period_label} 누적" if period_label else "누적"
    return " ".join(
        str(part)
        for part in [year_label, period_label, basis if basis not in {"", "YTD"} else ""]
        if part
    )


def _period_for_metric(metrics_by_key: dict[str, Any], period_key: str) -> dict[str, Any]:
    for metric in INCOME_TREND_METRICS + REQUIRED_DART_METRICS:
        values = metrics_by_key.get(metric, {}).get("values_by_period", {})
        entry = values.get(period_key)
        if isinstance(entry, dict) and isinstance(entry.get("period"), dict):
            return entry["period"]
    raise ValueError(f"DART period metadata not found for period_key: {period_key}")


def _optional_numeric_value(value: Any) -> float | None:
    if value is None:
        return None
    try:
        if pd.isna(value):
            return None
        return float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"Non-numeric value found where numeric data was expected: {value}") from exc


def _numeric_metric_value(entry: dict[str, Any], metric_key: str, period_key: str) -> float:
    value = entry.get("value")
    if value is None:
        raise ValueError(f"DART metric '{metric_key}' period '{period_key}' has no value.")
    try:
        return float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"DART metric '{metric_key}' period '{period_key}' has a non-numeric value: {value}") from exc
