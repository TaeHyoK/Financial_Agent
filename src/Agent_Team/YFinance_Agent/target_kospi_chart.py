"""Build a point-in-time two-year indexed target-stock vs KOSPI chart."""

from __future__ import annotations

import argparse
import json
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path

import matplotlib

matplotlib.use("Agg")

import matplotlib.dates as mdates
import matplotlib.pyplot as plt
from matplotlib import font_manager
import pandas as pd


DEFAULT_KOSPI_TICKER = "^KS11"
TARGET_COLOR = "#2563EB"
KOSPI_COLOR = "#000000"
DEFAULT_OUTPUT_NAME = "target_vs_kospi_2y.png"


@dataclass(frozen=True)
class ChartResult:
    chart_path: Path
    data_path: Path
    metadata_path: Path
    requested_start: date
    requested_end_exclusive: date
    actual_start: date
    actual_end: date
    row_count: int


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Download point-in-time YFinance prices and chart target performance against KOSPI."
    )
    parser.add_argument("--ticker", required=True, help="Target Yahoo ticker, e.g. 326030.KS.")
    parser.add_argument("--company-name", required=True, help="Target company label shown in the chart.")
    parser.add_argument(
        "--selected-date",
        required=True,
        help="Report date in YYYYMMDD; market observations must be strictly before this date.",
    )
    parser.add_argument("--years", type=int, default=2, help="Calendar-year lookback. Defaults to 2.")
    parser.add_argument("--kospi-ticker", default=DEFAULT_KOSPI_TICKER)
    parser.add_argument("--output-dir", required=True, type=Path)
    parser.add_argument("--output-name", default=DEFAULT_OUTPUT_NAME)
    return parser


def build_target_vs_kospi_chart(
    *,
    ticker: str,
    company_name: str,
    selected_date: date,
    output_dir: Path,
    years: int = 2,
    kospi_ticker: str = DEFAULT_KOSPI_TICKER,
    output_name: str = DEFAULT_OUTPUT_NAME,
    target_prices: pd.Series | None = None,
    kospi_prices: pd.Series | None = None,
) -> ChartResult:
    """Download, index, render, and persist one target-vs-KOSPI chart."""

    if years < 1:
        raise ValueError("years must be at least 1.")
    requested_start = _shift_years(selected_date, -years)
    if target_prices is None:
        target_prices = download_adjusted_close(
            ticker,
            start_date=requested_start,
            end_exclusive=selected_date,
        )
    if kospi_prices is None:
        kospi_prices = download_adjusted_close(
            kospi_ticker,
            start_date=requested_start,
            end_exclusive=selected_date,
        )
    indexed = build_indexed_frame(
        target_prices=target_prices,
        kospi_prices=kospi_prices,
        start_date=requested_start,
        end_exclusive=selected_date,
    )

    output_dir = output_dir.expanduser().resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    chart_path = output_dir / _safe_output_name(output_name)
    data_path = output_dir / "target_vs_kospi_2y_data.csv"
    metadata_path = output_dir / "target_vs_kospi_2y_metadata.json"

    render_indexed_chart(
        indexed,
        output_path=chart_path,
        company_name=company_name,
    )
    indexed.to_csv(data_path, index=False, encoding="utf-8")

    actual_start = indexed["date"].iloc[0].date()
    actual_end = indexed["date"].iloc[-1].date()
    metadata = {
        "chart_type": "indexed_target_vs_kospi_time_series",
        "company_name": company_name,
        "target_ticker": ticker,
        "kospi_ticker": kospi_ticker,
        "selected_date": selected_date.isoformat(),
        "selected_date_policy": "before_market_open",
        "information_cutoff_date": actual_end.isoformat(),
        "requested_period": {
            "start": requested_start.isoformat(),
            "end_exclusive": selected_date.isoformat(),
        },
        "actual_period": {
            "start": actual_start.isoformat(),
            "end": actual_end.isoformat(),
        },
        "row_count": int(len(indexed)),
        "price_basis": "Yahoo Finance adjusted close; raw close fallback when adjusted close is unavailable",
        "normalization": "first common trading date = 100",
        "colors": {"target": TARGET_COLOR, "kospi": KOSPI_COLOR},
        "chart_path": str(chart_path),
        "data_path": str(data_path),
        "integrated_into_final_report": False,
    }
    metadata_path.write_text(
        json.dumps(metadata, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return ChartResult(
        chart_path=chart_path,
        data_path=data_path,
        metadata_path=metadata_path,
        requested_start=requested_start,
        requested_end_exclusive=selected_date,
        actual_start=actual_start,
        actual_end=actual_end,
        row_count=len(indexed),
    )


def download_adjusted_close(
    ticker: str,
    *,
    start_date: date,
    end_exclusive: date,
) -> pd.Series:
    """Return one timezone-naive adjusted-close series from Yahoo Finance."""

    try:
        import yfinance as yf
    except ImportError as exc:
        raise RuntimeError("yfinance is required to download chart data.") from exc

    frame = yf.download(
        ticker,
        start=start_date.isoformat(),
        end=end_exclusive.isoformat(),
        auto_adjust=False,
        progress=False,
        actions=False,
        threads=False,
    )
    if frame.empty:
        raise RuntimeError(
            f"Yahoo Finance returned no data for {ticker} between "
            f"{start_date.isoformat()} and {end_exclusive.isoformat()}."
        )
    if isinstance(frame.columns, pd.MultiIndex):
        frame.columns = frame.columns.get_level_values(0)
    price_column = "Adj Close" if "Adj Close" in frame.columns else "Close"
    if price_column not in frame.columns:
        raise RuntimeError(f"Yahoo Finance data for {ticker} has no adjusted/close column.")
    series = pd.to_numeric(frame[price_column], errors="coerce").dropna()
    series.index = pd.to_datetime(series.index).tz_localize(None).normalize()
    series = series[~series.index.duplicated(keep="last")].sort_index()
    series.name = ticker
    if series.empty:
        raise RuntimeError(f"Yahoo Finance prices for {ticker} are empty after normalization.")
    return series


def build_indexed_frame(
    *,
    target_prices: pd.Series,
    kospi_prices: pd.Series,
    start_date: date,
    end_exclusive: date,
) -> pd.DataFrame:
    """Align common trading dates and rebase both adjusted-close series to 100."""

    target = _normalized_series(target_prices, "target_adjusted_close")
    kospi = _normalized_series(kospi_prices, "kospi_adjusted_close")
    frame = pd.concat([target, kospi], axis=1, join="inner").dropna().sort_index()
    mask = (frame.index.date >= start_date) & (frame.index.date < end_exclusive)
    frame = frame.loc[mask].copy()
    if frame.empty:
        raise ValueError("No common target/KOSPI trading dates remain in the requested period.")
    for column in ("target_adjusted_close", "kospi_adjusted_close"):
        first = float(frame[column].iloc[0])
        if first <= 0:
            raise ValueError(f"{column} first value must be positive.")
    frame["target_index"] = frame["target_adjusted_close"] / frame["target_adjusted_close"].iloc[0] * 100.0
    frame["kospi_index"] = frame["kospi_adjusted_close"] / frame["kospi_adjusted_close"].iloc[0] * 100.0
    frame.index.name = "date"
    return frame.reset_index()


def render_indexed_chart(
    indexed: pd.DataFrame,
    *,
    output_path: Path,
    company_name: str,
) -> None:
    """Render the normalized two-line performance chart."""

    _configure_korean_font()
    dates = pd.to_datetime(indexed["date"])
    target_index = pd.to_numeric(indexed["target_index"], errors="raise")
    kospi_index = pd.to_numeric(indexed["kospi_index"], errors="raise")
    target_return = float(target_index.iloc[-1] - 100.0)
    kospi_return = float(kospi_index.iloc[-1] - 100.0)

    fig, ax = plt.subplots(figsize=(13, 7.2))
    fig.patch.set_facecolor("white")
    ax.set_facecolor("white")
    ax.plot(
        dates,
        target_index,
        color=TARGET_COLOR,
        linewidth=2.4,
        label=f"{company_name} ({target_return:+.1f}%)",
        zorder=3,
    )
    ax.plot(
        dates,
        kospi_index,
        color=KOSPI_COLOR,
        linewidth=2.0,
        label=f"KOSPI ({kospi_return:+.1f}%)",
        zorder=2,
    )
    ax.axhline(100.0, color="#9CA3AF", linewidth=1.0, linestyle="--", zorder=1)
    ax.scatter(dates.iloc[-1], target_index.iloc[-1], color=TARGET_COLOR, s=28, zorder=4)
    ax.scatter(dates.iloc[-1], kospi_index.iloc[-1], color=KOSPI_COLOR, s=24, zorder=4)

    ax.set_title(f"{company_name} vs KOSPI 주가 성과", fontsize=17, fontweight="bold", loc="left", pad=18)
    ax.set_ylabel("누적 성과 지수 (시작일=100)", fontsize=11)
    ax.set_xlabel("")
    ax.legend(loc="upper left", frameon=False, ncol=2, fontsize=10.5)
    ax.grid(axis="y", color="#E5E7EB", linewidth=0.8)
    ax.grid(axis="x", visible=False)
    ax.spines["top"].set_visible(False)
    ax.spines["right"].set_visible(False)
    ax.spines["left"].set_color("#D1D5DB")
    ax.spines["bottom"].set_color("#D1D5DB")
    ax.tick_params(colors="#4B5563")
    ax.yaxis.set_major_formatter(lambda value, _position: f"{value:.0f}")

    locator = mdates.MonthLocator(interval=3)
    ax.xaxis.set_major_locator(locator)
    ax.xaxis.set_major_formatter(mdates.DateFormatter("%Y-%m"))
    plt.setp(ax.get_xticklabels(), rotation=0, ha="center")
    fig.tight_layout()
    output_path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(output_path, dpi=220, bbox_inches="tight", facecolor="white")
    plt.close(fig)


def _normalized_series(value: pd.Series, name: str) -> pd.Series:
    series = pd.Series(value).copy()
    series.index = pd.to_datetime(series.index).tz_localize(None).normalize()
    series = pd.to_numeric(series, errors="coerce").dropna()
    series = series[~series.index.duplicated(keep="last")].sort_index()
    series.name = name
    return series


def _configure_korean_font() -> None:
    candidates = (
        Path.home() / ".local/share/fonts/NotoSansCJKkr-Regular.otf",
        Path("/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc"),
        Path("/usr/share/fonts/truetype/nanum/NanumGothic.ttf"),
    )
    for path in candidates:
        if not path.exists():
            continue
        font_manager.fontManager.addfont(str(path))
        plt.rcParams["font.family"] = font_manager.FontProperties(fname=str(path)).get_name()
        break
    plt.rcParams["axes.unicode_minus"] = False


def _shift_years(value: date, years: int) -> date:
    try:
        return value.replace(year=value.year + years)
    except ValueError:
        return value.replace(year=value.year + years, month=2, day=28)


def _parse_date(value: str) -> date:
    digits = "".join(character for character in str(value) if character.isdigit())
    if len(digits) != 8:
        raise ValueError("selected-date must be YYYYMMDD or YYYY-MM-DD.")
    return datetime.strptime(digits, "%Y%m%d").date()


def _safe_output_name(value: str) -> str:
    name = Path(value).name
    if not name.lower().endswith(".png"):
        name += ".png"
    return name


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    selected_date = _parse_date(args.selected_date)
    result = build_target_vs_kospi_chart(
        ticker=args.ticker,
        company_name=args.company_name,
        selected_date=selected_date,
        output_dir=args.output_dir,
        years=args.years,
        kospi_ticker=args.kospi_ticker,
        output_name=args.output_name,
    )
    print(json.dumps({
        "chart_path": str(result.chart_path),
        "data_path": str(result.data_path),
        "metadata_path": str(result.metadata_path),
        "actual_period": [result.actual_start.isoformat(), result.actual_end.isoformat()],
        "row_count": result.row_count,
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
