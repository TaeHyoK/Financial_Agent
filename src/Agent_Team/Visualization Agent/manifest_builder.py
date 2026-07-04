"""Manifest and summary writers for Visualization Agent outputs."""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any

import pandas as pd

from data_loader import OPTIONAL_DART_METRICS, OPTIONAL_MARKET_COLUMNS, REQUIRED_DART_METRICS, REQUIRED_MARKET_COLUMNS


OUTPUT_VERSION = "1.1"
BASIS_WARNING = "YTD 기준 수치가 포함된 경우 연간 확정치와 동일 기간 YoY로 단정하지 않는다."


def build_chart_manifest(
    company_name: str,
    run_key: str,
    source_files: dict[str, Any],
    chart_metadata: list[dict[str, Any]],
    output_path: str | Path,
) -> dict[str, Any]:
    """Build and write chart_manifest.json."""

    manifest = {
        "agent_name": "Visualization Agent",
        "output_version": OUTPUT_VERSION,
        "target_company_name": company_name,
        "target_run_key": run_key,
        "created_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "source_files": source_files,
        "charts": chart_metadata,
    }
    save_json(output_path, manifest)
    return manifest


def build_data_quality_report(
    market_df: pd.DataFrame,
    margin_df: pd.DataFrame,
    dart_index: dict[str, Any],
    source_files: dict[str, Any],
    output_path: str | Path,
    peer_return_df: pd.DataFrame | None = None,
    financial_health_df: pd.DataFrame | None = None,
    evidence_df: pd.DataFrame | None = None,
) -> dict[str, Any]:
    """Build and write data_quality_report.json."""

    missing_required_columns = [column for column in REQUIRED_MARKET_COLUMNS if column not in market_df.columns]
    latest_snapshot = _market_latest_snapshot(market_df)
    report = {
        "market_dataset": {
            "path": source_files.get("market_full_dataset"),
            "row_count": int(len(market_df)),
            "source_row_count": int(market_df.attrs.get("source_row_count", len(market_df))),
            "duplicate_date_count": int(market_df.attrs.get("duplicate_date_count", 0)),
            "dropped_stock_close_null_count": int(market_df.attrs.get("dropped_stock_close_null_count", 0)),
            "date_min": _date_to_string(market_df["date"].min()) if "date" in market_df else None,
            "date_max": _date_to_string(market_df["date"].max()) if "date" in market_df else None,
            "required_columns_present": not missing_required_columns,
            "missing_required_columns": missing_required_columns,
            "null_counts_for_required_columns": {
                column: int(market_df[column].isna().sum()) for column in REQUIRED_MARKET_COLUMNS if column in market_df.columns
            },
            "latest_snapshot": latest_snapshot,
            "optional_columns_summary": _optional_market_summary(market_df),
        },
        "dart_dataset": {
            "path": source_files.get("dart_main"),
            "required_metrics_present": _required_dart_metrics_present(dart_index),
            "missing_required_metrics": [metric for metric in REQUIRED_DART_METRICS if metric not in dart_index.get("metrics_by_key", {})],
            "periods": margin_df["period_label"].tolist() if "period_label" in margin_df else [],
            "basis_warning": BASIS_WARNING,
            "optional_metrics_summary": _optional_dart_summary(dart_index),
        },
    }
    if peer_return_df is not None:
        report["peer_market_dataset"] = {
            "source_files": source_files.get("peer_market_datasets", {}),
            "peer_count": int(len(peer_return_df)),
            "companies": peer_return_df["company_name"].tolist() if "company_name" in peer_return_df else [],
            "latest_return_snapshot": _records_for_json(peer_return_df),
        }
    if financial_health_df is not None:
        report["peer_financial_health_dataset"] = {
            "source_files": source_files.get("peer_financial_reports", {}),
            "peer_count": int(len(financial_health_df)),
            "companies": financial_health_df["company_name"].tolist() if "company_name" in financial_health_df else [],
            "latest_ratio_snapshot": _records_for_json(financial_health_df),
        }
    if evidence_df is not None:
        report["strategy_evidence_dataset"] = {
            "path": source_files.get("decision_basis_card"),
            "item_count": int(len(evidence_df)),
            "counts_by_signal_type": evidence_df.groupby("signal_type").size().to_dict()
            if "signal_type" in evidence_df
            else {},
            "counts_by_category": evidence_df.groupby("category").size().to_dict()
            if "category" in evidence_df
            else {},
        }
    save_json(output_path, report)
    return report


def build_visualization_summary(
    company_name: str,
    run_key: str,
    manifest: dict[str, Any],
    data_quality_report: dict[str, Any],
    output_path: str | Path,
) -> str:
    """Build and write a human-readable visualization_summary.md."""

    market_quality = data_quality_report.get("market_dataset", {})
    dart_quality = data_quality_report.get("dart_dataset", {})
    chart_lines = [
        f"{index}. {chart['title']}" for index, chart in enumerate(manifest.get("charts", []), start=1)
    ]
    summary = "\n".join(
        [
            "# Visualization Agent Summary",
            "",
            "## Target",
            f"- Company: {company_name}",
            f"- Run key: {run_key}",
            "",
            "## Generated Charts",
            *chart_lines,
            "",
            "## Key Data Notes",
            f"- Market data range: {market_quality.get('date_min')} ~ {market_quality.get('date_max')}",
            f"- DART periods: {', '.join(dart_quality.get('periods', []))}",
            f"- Generated chart count: {len(manifest.get('charts', []))}",
            "- YTD DART values, if present, are not full-year values.",
            "",
            "## Writer Agent Usage",
            "- Use chart_manifest.json as the source of truth for chart captions and allowed interpretation.",
            "- Do not infer unsupported profitability, capital-efficiency, or valuation metrics from these charts.",
            "",
        ]
    )
    output_path = Path(output_path).expanduser().resolve()
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(summary, encoding="utf-8")
    return summary


def save_json(path: str | Path, payload: dict[str, Any]) -> None:
    output_path = Path(path).expanduser().resolve()
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def _market_latest_snapshot(market_df: pd.DataFrame) -> dict[str, Any]:
    if market_df.empty:
        return {}
    latest = market_df.sort_values("date").iloc[-1]
    snapshot_columns = [
        "date",
        "stock_close",
        "stock_volume_ratio_20",
        "stock_excess_return_20d_pct",
        "stock_relative_strength_60_pct",
    ]
    snapshot: dict[str, Any] = {}
    for column in snapshot_columns:
        value = latest.get(column)
        snapshot[column] = _json_value(value)
    return snapshot


def _optional_market_summary(market_df: pd.DataFrame) -> dict[str, Any]:
    summary: dict[str, Any] = {}
    for column in OPTIONAL_MARKET_COLUMNS:
        if column not in market_df.columns:
            continue
        series = pd.to_numeric(market_df[column], errors="coerce")
        summary[column] = {
            "null_count": int(series.isna().sum()),
            "latest": _json_value(series.iloc[-1]) if len(series) else None,
            "min": _json_value(series.min()),
            "max": _json_value(series.max()),
            "mean": _json_value(series.mean()),
        }
    return summary


def _required_dart_metrics_present(dart_index: dict[str, Any]) -> bool:
    metrics_by_key = dart_index.get("metrics_by_key", {})
    return all(metric in metrics_by_key for metric in REQUIRED_DART_METRICS)


def _optional_dart_summary(dart_index: dict[str, Any]) -> dict[str, Any]:
    metrics_by_key = dart_index.get("metrics_by_key", {})
    summary: dict[str, Any] = {}
    for metric in OPTIONAL_DART_METRICS:
        metric_payload = metrics_by_key.get(metric)
        if not isinstance(metric_payload, dict):
            continue
        period_values: dict[str, Any] = {}
        for period_key, entry in metric_payload.get("values_by_period", {}).items():
            period = entry.get("period", {})
            label = _period_label_from_entry(period_key, period)
            period_values[label] = {
                "value": _json_value(entry.get("value")),
                "display_value": entry.get("display_value"),
                "status": entry.get("status"),
            }
        summary[metric] = {
            "display_name": metric_payload.get("display_name"),
            "unit": metric_payload.get("unit"),
            "values_by_period": period_values,
        }
    return summary


def _period_label_from_entry(period_key: str, period: dict[str, Any]) -> str:
    fiscal_year = period.get("fiscal_year")
    period_type = period.get("period_type")
    basis = period.get("basis")
    if basis == "FULL_YEAR":
        return f"{fiscal_year} FY"
    if period_type == "Q3" and basis == "YTD":
        return f"{fiscal_year} Q3 YTD"
    if fiscal_year:
        return " ".join(str(part) for part in [fiscal_year, period_type, basis] if part is not None)
    return period_key


def _date_to_string(value: Any) -> str | None:
    if pd.isna(value):
        return None
    return pd.Timestamp(value).date().isoformat()


def _json_value(value: Any) -> Any:
    if isinstance(value, pd.Timestamp):
        return value.date().isoformat()
    if pd.isna(value):
        return None
    if hasattr(value, "item"):
        return value.item()
    return value


def _records_for_json(df: pd.DataFrame) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    for raw_record in df.to_dict("records"):
        records.append({key: _json_value(value) for key, value in raw_record.items()})
    return records
