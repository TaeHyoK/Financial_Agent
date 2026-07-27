"""End-to-end market data collection, indicator, and visualization pipeline."""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

import matplotlib

matplotlib.use("Agg")

import matplotlib.dates as mdates
import matplotlib.pyplot as plt
import pandas as pd

from indicators import add_technical_indicators
from valuation import collect_historical_valuation, unavailable_direct_valuation


DEFAULT_FX_TICKER = "KRW=X"
DEFAULT_KOSPI_TICKER = "^KS11"
AGENT_DIR = Path(__file__).resolve().parent
SOURCE_LOOKBACK_YEARS = 2
OUTPUT_COLUMNS = [
    "date",
    "stock_close",
    "stock_adjusted_close",
    "stock_dividends",
    "stock_splits",
    "stock_return_5d",
    "stock_return_20d",
    "stock_return_60d",
    "stock_close_to_ma20",
    "stock_close_to_ma60",
    "stock_ma5_to_ma20",
    "stock_rsi_14",
    "stock_macd_hist",
    "stock_macd_hist_change_1d",
    "stock_bb_width_20",
    "stock_volatility_20",
    "stock_volume_ratio_20",
    "stock_obv_trend",
    "kospi_close",
    "kospi_return_5d",
    "kospi_return_20d",
    "kospi_close_to_ma20",
    "kospi_rsi_14",
    "kospi_volatility_20",
    "fx_close",
    "fx_return_5d",
    "fx_return_20d",
    "fx_close_to_ma20",
    "fx_rsi_14",
    "fx_volatility_20",
    "stock_excess_return_5d",
    "stock_excess_return_20d",
    "stock_relative_strength_60",
]


@dataclass(frozen=True)
class PipelineInput:
    """Normalized input values for the YFinance pipeline."""

    ticker: str
    company_name: str | None
    start_date: date
    end_date: date
    selected_date: date
    source_path: Path
    selected_date_policy: str = "before_market_open"


@dataclass(frozen=True)
class OutputPaths:
    """Paths written by the pipeline."""

    full_csv: Path
    full_json: Path
    summary_csv: Path
    summary_json: Path
    valuation_json: Path
    manifest_json: Path
    full_period_technical_chart: Path
    full_period_macro_chart: Path
    summary_chart: Path


@dataclass(frozen=True)
class SummaryResult:
    """Summary output row and selected-date resolution metadata."""

    frame: pd.DataFrame
    requested_date: str
    actual_date: str
    match_type: str


def load_pipeline_input(path: Path) -> PipelineInput:
    """Load input JSON and normalize ticker/date fields."""

    with path.open("r", encoding="utf-8") as file:
        payload = json.load(file)

    ticker = str(payload.get("ticker", "")).strip()
    if not ticker:
        raise ValueError(f"Input file must include a non-empty ticker: {path}")

    start_date, requested_end_date = _resolve_date_range(payload)
    selected_raw = str(
        payload.get("selected_date")
        or (requested_end_date + timedelta(days=1)).strftime("%Y%m%d")
    )
    selected_date = _parse_yyyymmdd(selected_raw)
    end_date = min(requested_end_date, selected_date - timedelta(days=1))
    if selected_date <= start_date or end_date < start_date:
        raise ValueError(
            f"date_range must contain data before selected_date {selected_date.isoformat()}"
        )

    return PipelineInput(
        ticker=ticker,
        company_name=payload.get("company_name"),
        start_date=start_date,
        end_date=end_date,
        selected_date=selected_date,
        source_path=path,
        selected_date_policy="before_market_open",
    )


def run_pipeline(
    pipeline_input: PipelineInput,
    *,
    output_dir: Path,
    fx_ticker: str = DEFAULT_FX_TICKER,
    kospi_ticker: str = DEFAULT_KOSPI_TICKER,
    logger: logging.Logger | None = None,
) -> OutputPaths:
    """Collect market data, calculate indicators, and write output files."""

    logger = logger or logging.getLogger(__name__)
    output_dir.mkdir(parents=True, exist_ok=True)
    charts_dir = output_dir / "charts"
    charts_dir.mkdir(parents=True, exist_ok=True)

    source_start_date = resolve_source_start_date(pipeline_input.start_date, pipeline_input.end_date)
    logger.info(
        "Downloading %s, %s, %s for source %s..%s; output %s..%s",
        pipeline_input.ticker,
        kospi_ticker,
        fx_ticker,
        source_start_date.isoformat(),
        pipeline_input.end_date.isoformat(),
        pipeline_input.start_date.isoformat(),
        pipeline_input.end_date.isoformat(),
    )
    raw_frames = download_market_data(
        {
            "stock": pipeline_input.ticker,
            "kospi": kospi_ticker,
            "fx_usdkrw": fx_ticker,
        },
        start_date=source_start_date,
        end_date=pipeline_input.end_date,
    )

    full = build_full_dataset(raw_frames, pipeline_input=pipeline_input)
    summary = build_summary_dataset(full, selected_date=pipeline_input.selected_date)

    full_csv = output_dir / "market_full_dataset.csv"
    full_json = output_dir / "market_full_dataset.json"
    summary_csv = output_dir / f"market_summary_{pipeline_input.selected_date.strftime('%Y%m%d')}.csv"
    summary_json = output_dir / f"market_summary_{pipeline_input.selected_date.strftime('%Y%m%d')}.json"
    valuation_json = output_dir / "valuation_snapshot.json"
    manifest_json = output_dir / "manifest.json"

    write_dataframe_outputs(full, csv_path=full_csv, json_path=full_json)
    write_dataframe_outputs(summary.frame, csv_path=summary_csv, json_path=summary_json)
    try:
        valuation = collect_historical_valuation(
            pipeline_input.ticker,
            selected_date=pipeline_input.selected_date,
        )
    except Exception as exc:  # provider failures must not discard valid OHLCV output
        logger.warning("Historical valuation collection unavailable: %s", type(exc).__name__)
        valuation = unavailable_direct_valuation(
            ticker=pipeline_input.ticker,
            selected_date=pipeline_input.selected_date,
            reason="provider_request_failed",
        )
    _write_json_payload(valuation_json, valuation)

    technical_chart = charts_dir / "full_period_technical.png"
    macro_chart = charts_dir / "full_period_kospi_fx.png"
    summary_chart = charts_dir / f"summary_{pipeline_input.selected_date.strftime('%Y%m%d')}.png"
    plot_full_period_technical(full, technical_chart, pipeline_input=pipeline_input)
    plot_full_period_macro(full, macro_chart, pipeline_input=pipeline_input)
    plot_summary(summary, summary_chart, pipeline_input=pipeline_input)

    paths = OutputPaths(
        full_csv=full_csv,
        full_json=full_json,
        summary_csv=summary_csv,
        summary_json=summary_json,
        valuation_json=valuation_json,
        manifest_json=manifest_json,
        full_period_technical_chart=technical_chart,
        full_period_macro_chart=macro_chart,
        summary_chart=summary_chart,
    )
    write_manifest(
        manifest_json,
        pipeline_input=pipeline_input,
        fx_ticker=fx_ticker,
        kospi_ticker=kospi_ticker,
        full=full,
        summary=summary,
        paths=paths,
    )
    return paths


def download_market_data(
    tickers: dict[str, str],
    *,
    start_date: date,
    end_date: date,
) -> dict[str, pd.DataFrame]:
    """Download OHLCV frames from yfinance for an inclusive date range."""

    try:
        import yfinance as yf
    except ImportError as exc:
        raise RuntimeError(
            "Missing dependency: yfinance. Install it with "
            f"`python -m pip install -r {AGENT_DIR / 'requirements.txt'}`."
        ) from exc

    end_exclusive = end_date + timedelta(days=1)
    frames: dict[str, pd.DataFrame] = {}
    for label, ticker in tickers.items():
        frame = yf.download(
            ticker,
            start=start_date.isoformat(),
            end=end_exclusive.isoformat(),
            auto_adjust=False,
            progress=False,
            actions=True,
            threads=True,
        )
        normalized = normalize_ohlcv_frame(frame)
        if normalized.empty:
            raise RuntimeError(
                f"yfinance returned no rows for {label} ticker {ticker} "
                f"between {start_date.isoformat()} and {end_date.isoformat()}"
            )
        frames[label] = normalized
    return frames


def resolve_source_start_date(start_date: date, end_date: date) -> date:
    """Return the widened raw-data start date used for indicator warm-up."""

    two_year_start = _shift_years(end_date, -SOURCE_LOOKBACK_YEARS) + timedelta(days=1)
    return min(start_date, two_year_start)


def normalize_ohlcv_frame(frame: pd.DataFrame) -> pd.DataFrame:
    """Normalize yfinance output into lowercase OHLCV columns indexed by date."""

    if frame.empty:
        return pd.DataFrame(
            columns=["open", "high", "low", "close", "adj_close", "volume", "dividends", "stock_splits"]
        )

    normalized = frame.copy()
    if isinstance(normalized.columns, pd.MultiIndex):
        normalized.columns = normalized.columns.get_level_values(0)

    rename_map = {str(column).strip().lower().replace(" ", "_"): column for column in normalized.columns}
    output = pd.DataFrame(index=pd.to_datetime(normalized.index).tz_localize(None).normalize())
    for expected in [
        "open",
        "high",
        "low",
        "close",
        "adj_close",
        "volume",
        "dividends",
        "stock_splits",
    ]:
        source = rename_map.get(expected)
        if source is None:
            output[expected] = pd.NA
        else:
            output[expected] = pd.to_numeric(normalized[source], errors="coerce")

    adjusted_close_available = not output["adj_close"].isna().all()
    if not adjusted_close_available:
        output["adj_close"] = output["close"]
    if output["volume"].isna().all():
        output["volume"] = 0
    output["dividends"] = output["dividends"].fillna(0.0)
    output["stock_splits"] = output["stock_splits"].fillna(0.0)
    output.attrs["adjusted_close_source"] = "provider" if adjusted_close_available else "raw_close_fallback"

    return output.sort_index()


def build_full_dataset(
    frames: dict[str, pd.DataFrame],
    *,
    pipeline_input: PipelineInput,
) -> pd.DataFrame:
    """Join stock, KOSPI, and FX features into the normalized output schema."""

    calculation_index = frames["stock"].index
    stock_input = frames["stock"].copy()
    if "adj_close" not in stock_input:
        stock_input["adj_close"] = stock_input["close"]
    for action_column in ("dividends", "stock_splits"):
        if action_column not in stock_input:
            stock_input[action_column] = 0.0
    stock_input["analysis_close"] = stock_input["adj_close"]
    stock_features = add_technical_indicators(stock_input, close_col="analysis_close")
    kospi_input = frames["kospi"].reindex(calculation_index).ffill()
    if "adj_close" not in kospi_input:
        kospi_input["adj_close"] = kospi_input["close"]
    kospi_input["analysis_close"] = kospi_input["adj_close"]
    kospi_features = add_technical_indicators(kospi_input, close_col="analysis_close")
    fx_features = add_technical_indicators(frames["fx_usdkrw"].reindex(calculation_index).ffill())

    output_mask = (calculation_index.date >= pipeline_input.start_date) & (
        calculation_index.date <= pipeline_input.end_date
    )
    stock_output = stock_features.loc[output_mask].copy()
    if stock_output.empty:
        raise RuntimeError("No stock rows remain after date-range filtering.")

    kospi_output = kospi_features.loc[stock_output.index]
    fx_output = fx_features.loc[stock_output.index]

    full = pd.DataFrame(index=stock_output.index)
    full["date"] = full.index.strftime("%Y-%m-%d")

    full["stock_close"] = stock_output["close"]
    full["stock_adjusted_close"] = stock_output["adj_close"]
    full["stock_dividends"] = stock_output["dividends"]
    full["stock_splits"] = stock_output["stock_splits"]
    full["stock_return_5d"] = stock_output["return_5d"]
    full["stock_return_20d"] = stock_output["return_20d"]
    full["stock_return_60d"] = stock_output["return_60d"]
    full["stock_close_to_ma20"] = stock_output["close_to_ma20"]
    full["stock_close_to_ma60"] = stock_output["close_to_ma60"]
    full["stock_ma5_to_ma20"] = stock_output["ma5_to_ma20"]
    full["stock_rsi_14"] = stock_output["rsi_14"]
    full["stock_macd_hist"] = stock_output["macd_hist_12_26_9"]
    full["stock_macd_hist_change_1d"] = stock_output["macd_hist_change_1d"]
    full["stock_bb_width_20"] = stock_output["bb_width_20"]
    full["stock_volatility_20"] = stock_output["volatility_20"]
    full["stock_volume_ratio_20"] = stock_output["volume_ratio_20"]
    full["stock_obv_trend"] = stock_output["obv_trend"]

    full["kospi_close"] = kospi_output["close"]
    full["kospi_return_5d"] = kospi_output["return_5d"]
    full["kospi_return_20d"] = kospi_output["return_20d"]
    full["kospi_close_to_ma20"] = kospi_output["close_to_ma20"]
    full["kospi_rsi_14"] = kospi_output["rsi_14"]
    full["kospi_volatility_20"] = kospi_output["volatility_20"]

    full["fx_close"] = fx_output["close"]
    full["fx_return_5d"] = fx_output["return_5d"]
    full["fx_return_20d"] = fx_output["return_20d"]
    full["fx_close_to_ma20"] = fx_output["close_to_ma20"]
    full["fx_rsi_14"] = fx_output["rsi_14"]
    full["fx_volatility_20"] = fx_output["volatility_20"]

    full["stock_excess_return_5d"] = full["stock_return_5d"] - full["kospi_return_5d"]
    full["stock_excess_return_20d"] = full["stock_return_20d"] - full["kospi_return_20d"]
    stock_strength = 1.0 + stock_output["return_60d"]
    kospi_strength = 1.0 + kospi_output["return_60d"]
    full["stock_relative_strength_60"] = stock_strength / kospi_strength.replace(0, pd.NA) - 1.0

    result = full.reset_index(drop=True)[OUTPUT_COLUMNS]
    result.attrs["stock_adjusted_close_source"] = frames["stock"].attrs.get(
        "adjusted_close_source",
        "unknown",
    )
    return result


def build_summary_dataset(full: pd.DataFrame, *, selected_date: date) -> SummaryResult:
    """Return the latest trading row strictly before a pre-open selected date."""

    target = selected_date.isoformat()
    prior = full.loc[full["date"] < target].tail(1).copy()
    if prior.empty:
        raise RuntimeError(f"No market data exists on or before selected date {target}.")
    frame = prior.reset_index(drop=True)
    return SummaryResult(
        frame=frame,
        requested_date=target,
        actual_date=str(frame.iloc[0]["date"]),
        match_type="latest_trading_day_before_selected_date",
    )


def write_dataframe_outputs(frame: pd.DataFrame, *, csv_path: Path, json_path: Path) -> None:
    """Write a dataframe to CSV and records-oriented JSON."""

    frame.to_csv(csv_path, index=False, encoding="utf-8")
    with json_path.open("w", encoding="utf-8") as file:
        json.dump(_json_records(frame), file, ensure_ascii=False, indent=2, allow_nan=False)
        file.write("\n")


def _write_json_payload(path: Path, payload: dict[str, Any]) -> None:
    with path.open("w", encoding="utf-8") as file:
        json.dump(payload, file, ensure_ascii=False, indent=2, allow_nan=False)
        file.write("\n")


def write_manifest(
    path: Path,
    *,
    pipeline_input: PipelineInput,
    fx_ticker: str,
    kospi_ticker: str,
    full: pd.DataFrame,
    summary: SummaryResult,
    paths: OutputPaths,
) -> None:
    """Write run metadata and output locations."""

    manifest = {
        "created_at": datetime.now().isoformat(timespec="seconds"),
        "input_path": str(pipeline_input.source_path),
        "ticker": pipeline_input.ticker,
        "company_name": pipeline_input.company_name,
        "date_range": {
            "start": pipeline_input.start_date.isoformat(),
            "end": pipeline_input.end_date.isoformat(),
        },
        "source_date_range": {
            "start": resolve_source_start_date(pipeline_input.start_date, pipeline_input.end_date).isoformat(),
            "end": pipeline_input.end_date.isoformat(),
        },
        "selected_date": pipeline_input.selected_date.isoformat(),
        "selected_date_policy": pipeline_input.selected_date_policy,
        "information_cutoff_date": (pipeline_input.selected_date - timedelta(days=1)).isoformat(),
        "summary_requested_date": summary.requested_date,
        "summary_actual_date": summary.actual_date,
        "summary_date_match": summary.match_type,
        "kospi_ticker": kospi_ticker,
        "fx_ticker": fx_ticker,
        "row_count": int(len(full)),
        "price_basis": {
            "valuation_and_display": "raw_close",
            "returns_and_technical_indicators": "adjusted_close",
            "adjusted_close_source": full.attrs.get("stock_adjusted_close_source", "unknown"),
        },
        "corporate_actions": {
            "dividend_event_count": int((pd.to_numeric(full["stock_dividends"], errors="coerce").fillna(0) != 0).sum()),
            "split_event_count": int((pd.to_numeric(full["stock_splits"], errors="coerce").fillna(0) != 0).sum()),
        },
        "columns": OUTPUT_COLUMNS,
        "outputs": {key: str(value) for key, value in paths.__dict__.items()},
    }
    with path.open("w", encoding="utf-8") as file:
        json.dump(manifest, file, ensure_ascii=False, indent=2)
        file.write("\n")


def plot_full_period_technical(
    full: pd.DataFrame,
    path: Path,
    *,
    pipeline_input: PipelineInput,
) -> None:
    """Plot stock close, RSI, MACD histogram, and derived risk/volume features."""

    data = _with_datetime_index(full)
    fig, axes = plt.subplots(4, 1, figsize=(14, 12), sharex=True, gridspec_kw={"height_ratios": [3, 1, 1, 1]})
    fig.suptitle(f"{pipeline_input.ticker} Technical Indicators", fontsize=14)

    axes[0].plot(
        data.index,
        data["stock_adjusted_close"],
        label="Adjusted Close",
        color="#1f77b4",
        linewidth=1.5,
    )
    axes[0].set_ylabel("Price")
    axes[0].legend(loc="upper left", fontsize=9)
    axes[0].grid(True, alpha=0.25)

    axes[1].plot(data.index, data["stock_rsi_14"], label="RSI 14", color="#9467bd", linewidth=1.2)
    axes[1].axhline(70, color="#d62728", linestyle="--", linewidth=0.8)
    axes[1].axhline(30, color="#2ca02c", linestyle="--", linewidth=0.8)
    axes[1].set_ylim(0, 100)
    axes[1].set_ylabel("RSI")
    axes[1].grid(True, alpha=0.25)

    axes[2].bar(data.index, data["stock_macd_hist"], label="MACD Hist", color="#7f7f7f", alpha=0.45)
    axes[2].plot(data.index, data["stock_macd_hist_change_1d"], label="Hist Change 1D", color="#ff7f0e", linewidth=1.1)
    axes[2].set_ylabel("MACD")
    axes[2].legend(loc="upper left", ncols=2, fontsize=9)
    axes[2].grid(True, alpha=0.25)

    axes[3].plot(data.index, data["stock_volume_ratio_20"], color="#777777", linewidth=1.1, label="Volume Ratio 20")
    axes[3].plot(data.index, data["stock_volatility_20"], color="#d62728", linewidth=1.1, label="Volatility 20")
    axes[3].plot(data.index, data["stock_bb_width_20"], color="#2ca02c", linewidth=1.1, label="BB Width 20")
    axes[3].set_ylabel("Derived")
    axes[3].legend(loc="upper left", ncols=3, fontsize=9)
    axes[3].grid(True, alpha=0.25)

    _format_date_axis(axes[-1])
    fig.tight_layout(rect=(0, 0, 1, 0.97))
    fig.savefig(path, dpi=160)
    plt.close(fig)


def plot_full_period_macro(
    full: pd.DataFrame,
    path: Path,
    *,
    pipeline_input: PipelineInput,
) -> None:
    """Plot normalized stock, KOSPI, and USD/KRW exchange-rate movement."""

    data = _with_datetime_index(full)
    fig, axes = plt.subplots(2, 1, figsize=(14, 8), sharex=True)
    fig.suptitle(f"{pipeline_input.ticker} vs KOSPI and USD/KRW", fontsize=14)

    normalized_stock = _normalize_to_100(data["stock_adjusted_close"])
    normalized_kospi = _normalize_to_100(data["kospi_close"])
    normalized_fx = _normalize_to_100(data["fx_close"])

    axes[0].plot(data.index, normalized_stock, label="Stock Close", color="#1f77b4", linewidth=1.5)
    axes[0].plot(data.index, normalized_kospi, label="KOSPI Close", color="#2ca02c", linewidth=1.3)
    axes[0].plot(data.index, normalized_fx, label="USD/KRW Close", color="#d62728", linewidth=1.3)
    axes[0].set_ylabel("Indexed to 100")
    axes[0].legend(loc="upper left", ncols=3, fontsize=9)
    axes[0].grid(True, alpha=0.25)

    axes[1].plot(data.index, data["kospi_close"], label="KOSPI", color="#2ca02c", linewidth=1.3)
    ax2 = axes[1].twinx()
    ax2.plot(data.index, data["fx_close"], label="USD/KRW", color="#d62728", linewidth=1.3)
    axes[1].set_ylabel("KOSPI")
    ax2.set_ylabel("USD/KRW")
    axes[1].grid(True, alpha=0.25)
    lines_1, labels_1 = axes[1].get_legend_handles_labels()
    lines_2, labels_2 = ax2.get_legend_handles_labels()
    axes[1].legend(lines_1 + lines_2, labels_1 + labels_2, loc="upper left", ncols=2, fontsize=9)

    _format_date_axis(axes[-1])
    fig.tight_layout(rect=(0, 0, 1, 0.96))
    fig.savefig(path, dpi=160)
    plt.close(fig)


def plot_summary(summary: SummaryResult, path: Path, *, pipeline_input: PipelineInput) -> None:
    """Plot a compact one-day summary snapshot."""

    row = summary.frame.iloc[0]
    fig, axes = plt.subplots(1, 2, figsize=(12, 5), gridspec_kw={"width_ratios": [1, 1.25]})
    fig.suptitle(f"{pipeline_input.ticker} Summary {row['date']}", fontsize=14)

    chart_labels = ["5D Ret", "20D Ret", "RSI14", "MACD Hist", "Excess 20D"]
    chart_values = [
        row["stock_return_5d"],
        row["stock_return_20d"],
        row["stock_rsi_14"],
        row["stock_macd_hist"],
        row["stock_excess_return_20d"],
    ]
    axes[0].bar(chart_labels, chart_values, color=["#1f77b4", "#2ca02c", "#9467bd", "#ff7f0e", "#d62728"])
    axes[0].set_ylabel("Metric Value")
    axes[0].grid(True, axis="y", alpha=0.25)
    axes[0].tick_params(axis="x", rotation=30)

    metrics = {
        "Close": row.get("stock_close"),
        "Return 60D": row.get("stock_return_60d"),
        "Close/MA20": row.get("stock_close_to_ma20"),
        "Close/MA60": row.get("stock_close_to_ma60"),
        "RSI 14": row.get("stock_rsi_14"),
        "MACD Hist": row.get("stock_macd_hist"),
        "Volatility 20": row.get("stock_volatility_20"),
        "KOSPI Close": row.get("kospi_close"),
        "USD/KRW Close": row.get("fx_close"),
        "Relative Strength 60": row.get("stock_relative_strength_60"),
    }
    lines = [
        f"Requested date: {summary.requested_date}",
        f"Data date: {summary.actual_date} ({summary.match_type})",
        "",
    ]
    lines.extend(f"{name}: {_format_number(value)}" for name, value in metrics.items())

    axes[1].axis("off")
    axes[1].text(0.02, 0.96, "\n".join(lines), va="top", ha="left", fontsize=11, family="monospace")

    fig.tight_layout(rect=(0, 0, 1, 0.93))
    fig.savefig(path, dpi=160)
    plt.close(fig)


def _resolve_date_range(payload: dict[str, Any]) -> tuple[date, date]:
    raw = str(payload.get("date_range", "")).strip()
    if raw:
        try:
            start_raw, end_raw = raw.split("-", 1)
        except ValueError as exc:
            raise ValueError(f"date_range must look like YYYYMMDD-YYYYMMDD: {raw}") from exc
        return _parse_yyyymmdd(start_raw), _parse_yyyymmdd(end_raw)

    start_raw = payload.get("start_date")
    end_raw = payload.get("end_date")
    if not start_raw or not end_raw:
        raise ValueError("Input must include either date_range or start_date/end_date.")
    return _parse_yyyymmdd(str(start_raw)), _parse_yyyymmdd(str(end_raw))


def _parse_yyyymmdd(value: str) -> date:
    return datetime.strptime(value.strip(), "%Y%m%d").date()


def _shift_years(value: date, years: int) -> date:
    try:
        return value.replace(year=value.year + years)
    except ValueError:
        return value.replace(year=value.year + years, month=2, day=28)


def _json_records(frame: pd.DataFrame) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    for record in frame.to_dict(orient="records"):
        clean: dict[str, Any] = {}
        for key, value in record.items():
            if hasattr(value, "item"):
                value = value.item()
            if pd.isna(value):
                value = None
            clean[key] = value
        records.append(clean)
    return records


def _with_datetime_index(full: pd.DataFrame) -> pd.DataFrame:
    data = full.copy()
    data.index = pd.to_datetime(data["date"])
    return data


def _format_date_axis(axis: plt.Axes) -> None:
    axis.xaxis.set_major_locator(mdates.MonthLocator(interval=1))
    axis.xaxis.set_major_formatter(mdates.DateFormatter("%Y-%m"))
    axis.tick_params(axis="x", rotation=45)


def _normalize_to_100(series: pd.Series) -> pd.Series:
    clean = pd.to_numeric(series, errors="coerce").dropna()
    if clean.empty:
        return pd.Series(index=series.index, dtype=float)
    first = clean.iloc[0]
    if first == 0:
        return pd.Series(index=series.index, dtype=float)
    return pd.to_numeric(series, errors="coerce") / first * 100


def _format_number(value: Any) -> str:
    try:
        numeric = float(value)
    except (TypeError, ValueError):
        return "N/A"
    if pd.isna(numeric):
        return "N/A"
    if abs(numeric) >= 1000:
        return f"{numeric:,.2f}"
    return f"{numeric:.4f}"
