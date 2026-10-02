"""Matplotlib chart builders for Visualization Agent outputs."""

from __future__ import annotations

import logging
from pathlib import Path

import matplotlib

matplotlib.use("Agg")

import matplotlib.dates as mdates
import matplotlib.pyplot as plt
from matplotlib import font_manager
import numpy as np
import pandas as pd
from matplotlib.ticker import FuncFormatter, MaxNLocator


logger = logging.getLogger(__name__)


def _configure_matplotlib_fonts() -> None:
    font_candidates = [
        Path.home() / ".local/share/fonts/NotoSansCJKkr-Regular.otf",
        Path("/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc"),
        Path("/usr/share/fonts/truetype/nanum/NanumGothic.ttf"),
    ]
    for font_path in font_candidates:
        if font_path.exists():
            font_manager.fontManager.addfont(str(font_path))
            plt.rcParams["font.family"] = "Noto Sans CJK KR"
            plt.rcParams["axes.unicode_minus"] = False
            return
    plt.rcParams["axes.unicode_minus"] = False


_configure_matplotlib_fonts()


# Report styling. Figures are drawn at their final printed size (about 150mm wide,
# 16:7) so the point sizes below are the sizes readers see on the page.
FIGURE_WIDTH_IN = 150 / 25.4
FIGURE_SIZE = (FIGURE_WIDTH_IN, FIGURE_WIDTH_IN * 7 / 16)
PNG_DPI = 300
TITLE_SIZE = 11
PANEL_TITLE_SIZE = 9.5
LABEL_SIZE = 9
TICK_SIZE = 8
LEGEND_SIZE = 8
VALUE_LABEL_SIZE = 7
NOTE_SIZE = 7

INK = "#111827"
MUTED = "#6b7280"
GRID = "#eceef1"
SPINE = "#d1d5db"
ACCENT = "#1f3a5f"  # target series
BENCHMARK = "#9ca3af"  # market or benchmark series
SECOND = "#5b9e96"  # muted teal, lighter than the accent in grayscale
THIRD_LINE = "#b5833f"  # muted ochre, drawn dashed
THIRD_BAR = "#c3ccd6"  # light slate
OUTPERFORM_FILL = "#fde2e2"
UNDERPERFORM_FILL = "#e3ecfa"
NEGATIVE_BAR = "#7aa3d4"
MISSING_BAR = "#d1d5db"


def _new_figure(nrows: int = 1, ncols: int = 1, **kwargs):
    """Create a report-sized figure whose layout engine keeps labels inside the canvas."""

    fig, axes = plt.subplots(nrows, ncols, figsize=FIGURE_SIZE, layout="constrained", **kwargs)
    fig.patch.set_facecolor("white")
    fig.get_layout_engine().set(w_pad=0.04, h_pad=0.04, wspace=0.06, hspace=0.06)
    return fig, axes


def _set_title(ax, text: str, *, size: float = TITLE_SIZE) -> None:
    ax.set_title(text, fontsize=size, loc="left", color=INK, pad=6)


def _legend_above(ax, ncol: int, *, reverse: bool = False) -> None:
    """Place the legend above the plot, right-aligned on the title row."""

    handles, labels = ax.get_legend_handles_labels()
    if reverse:
        handles, labels = handles[::-1], labels[::-1]
    ax.legend(
        handles,
        labels,
        loc="lower right",
        bbox_to_anchor=(1.0, 1.0),
        ncol=ncol,
        frameon=False,
        fontsize=LEGEND_SIZE,
        borderaxespad=0.3,
        handlelength=1.6,
        columnspacing=1.2,
    )


def _legend_below(ax, ncol: int) -> None:
    """Place the legend under the tick labels for narrow side-by-side panels."""

    ax.legend(
        loc="upper center",
        bbox_to_anchor=(0.5, -0.12),
        ncol=ncol,
        frameon=False,
        fontsize=LEGEND_SIZE,
        borderaxespad=0.0,
        handlelength=1.2,
        columnspacing=1.0,
    )


def _end_label(ax, x, y, text: str, color: str, dy: float) -> None:
    """Label the last point of a series just to its right."""

    ax.annotate(
        text,
        xy=(x, y),
        xytext=(3, dy),
        textcoords="offset points",
        ha="left",
        va="center",
        fontsize=VALUE_LABEL_SIZE,
        color=color,
    )


def _reserve_end_label_space(ax, dates: pd.Series, share: float) -> None:
    """Extend the date axis so end labels stay inside the plot area."""

    start, end = pd.to_datetime(dates).min(), pd.to_datetime(dates).max()
    pad = (end - start) * 0.01
    ax.set_xlim(start - pad, end + (end - start) * share)


def _date_axis(ax, dates: pd.Series) -> None:
    span_days = (pd.to_datetime(dates).max() - pd.to_datetime(dates).min()).days
    ax.xaxis.set_major_locator(mdates.AutoDateLocator(minticks=4, maxticks=8))
    ax.xaxis.set_major_formatter(mdates.DateFormatter("%y.%m" if span_days > 120 else "%m.%d"))


def build_stock_price_ma_volume_chart(
    market_df: pd.DataFrame,
    output_pdf: str | Path,
    output_png: str | Path,
    company_name: str,
) -> dict:
    """Create the price, moving-average and volume chart."""

    output_pdf = Path(output_pdf).expanduser().resolve()
    output_png = Path(output_png).expanduser().resolve()
    output_pdf.parent.mkdir(parents=True, exist_ok=True)
    output_png.parent.mkdir(parents=True, exist_ok=True)

    required_columns = [
        "date",
        "stock_close",
        "derived_ma20",
        "derived_ma60",
        "stock_volume_ratio_20",
    ]
    _require_columns(market_df, required_columns, "market chart")
    chart_df = market_df.dropna(subset=["date", "stock_close"]).sort_values("date")
    if chart_df.empty:
        raise ValueError("Market chart data is empty after excluding rows without stock_close.")

    fig, axes = _new_figure(
        nrows=2,
        ncols=1,
        sharex=True,
        gridspec_kw={"height_ratios": [2.6, 1.0]},
    )

    title_company = _safe_title_company(company_name)
    price_ax, volume_ax = axes
    price_ax.plot(chart_df["date"], chart_df["stock_close"], color=ACCENT, linewidth=1.4, label="종가")
    price_ax.plot(chart_df["date"], chart_df["derived_ma20"], color=SECOND, linewidth=1.0, label="20일 이동평균")
    price_ax.plot(
        chart_df["date"],
        chart_df["derived_ma60"],
        color=THIRD_LINE,
        linewidth=1.0,
        linestyle=(0, (4, 2)),
        label="60일 이동평균",
    )
    latest = chart_df.iloc[-1]
    _end_label(price_ax, latest["date"], latest["stock_close"], f"{latest['stock_close']:,.0f}원", ACCENT, 0)
    _reserve_end_label_space(price_ax, chart_df["date"], 0.11)
    _set_title(price_ax, f"{title_company} 주가와 이동평균")
    price_ax.set_ylabel("주가(원)", fontsize=LABEL_SIZE)
    price_ax.yaxis.set_major_formatter(FuncFormatter(lambda value, _: f"{value:,.0f}"))
    _legend_above(price_ax, ncol=3)
    _style_axis(price_ax)

    volume_ax.plot(chart_df["date"], chart_df["stock_volume_ratio_20"], color=BENCHMARK, linewidth=0.9, label="20일 평균 대비 거래량")
    volume_ax.axhline(1.0, color=MUTED, linewidth=0.6, linestyle="--")
    volume_ax.text(
        0.005,
        0.97,
        "20일 평균 대비 거래량",
        transform=volume_ax.transAxes,
        ha="left",
        va="top",
        fontsize=TICK_SIZE,
        color=MUTED,
        bbox={"facecolor": "white", "edgecolor": "none", "alpha": 0.8, "pad": 0.5},
    )
    volume_ax.set_ylabel("배수", fontsize=LABEL_SIZE)
    _style_axis(volume_ax)
    _date_axis(volume_ax, chart_df["date"])

    _save_figure(fig, output_pdf, output_png)
    plt.close(fig)
    logger.info("Wrote market chart: %s, %s", output_pdf, output_png)

    return {
        "figure_id": "fig_stock_price_ma_volume",
        "title": "주가·이동평균·거래량",
        "chart_type": "multi_panel_time_series",
        "section_recommendation": "시장·주가 분석",
        "asset_path_pdf": "figures/stock_price_ma_volume_relative_strength.pdf",
        "asset_path_png": "figures/stock_price_ma_volume_relative_strength.png",
        "asset_abs_path_pdf": str(output_pdf),
        "asset_abs_path_png": str(output_png),
        "data_source": "market_full_dataset.csv",
        "used_columns": [
            "date",
            "stock_close",
            "stock_close_to_ma20",
            "stock_close_to_ma60",
            "stock_volume_ratio_20",
        ],
        "derived_columns": [
            "derived_ma20",
            "derived_ma60",
        ],
        "caption": (
            "주가는 20일 및 60일 이동평균선 대비 위치와 20일 거래량 비율을 함께 보여준다."
        ),
        "writer_allowed_interpretation": (
            "주가의 절대 추세, 이동평균선 대비 위치와 거래량 활성도를 설명할 수 있다."
        ),
        "writer_forbidden_interpretation": [
            "이동평균선 상회만으로 매수 신호라고 단정하지 않는다.",
            "거래량 증가만으로 실적 개선을 단정하지 않는다.",
            "목표주가, upside/downside를 이 차트에서 산출하지 않는다.",
        ],
        "data_limitations": [
            "시장 데이터는 가격 및 거래 지표이며 펀더멘털 개선의 직접 증거가 아니다.",
        ],
    }


def build_fundamental_margin_trend_chart(
    margin_df: pd.DataFrame,
    output_pdf: str | Path,
    output_png: str | Path,
    company_name: str,
) -> dict:
    """Create the contribution margin and SG&A margin trend chart."""

    output_pdf = Path(output_pdf).expanduser().resolve()
    output_png = Path(output_png).expanduser().resolve()
    output_pdf.parent.mkdir(parents=True, exist_ok=True)
    output_png.parent.mkdir(parents=True, exist_ok=True)

    required_columns = [
        "period_key",
        "period_label",
        "period_type",
        "contribution_margin_pct",
        "sga_margin_pct",
        "basis",
    ]
    _require_columns(margin_df, required_columns, "fundamental margin chart")
    chart_df = _select_comparable_period_rows(margin_df)
    if chart_df.empty:
        raise ValueError("Fundamental margin chart data is empty.")

    fig, ax = _new_figure()
    x_positions = np.arange(len(chart_df))
    width = 0.3
    contribution_bars = ax.bar(
        x_positions - width / 2,
        chart_df["contribution_margin_pct"],
        width,
        color=ACCENT,
        label="공헌이익률",
    )
    sga_bars = ax.bar(
        x_positions + width / 2,
        chart_df["sga_margin_pct"],
        width,
        color=SECOND,
        label="판매관리비율",
    )
    _label_bars(ax, contribution_bars, suffix="%")
    _label_bars(ax, sga_bars, suffix="%")

    title_company = _safe_title_company(company_name)
    _set_title(ax, f"{title_company} 동일 기간 수익성 비교")
    ax.set_xticks(x_positions)
    ax.set_xticklabels(chart_df["period_label"].tolist())
    ax.set_ylabel("비율(%)", fontsize=LABEL_SIZE)
    ax.yaxis.set_major_formatter(FuncFormatter(lambda value, _: f"{value:.0f}%"))
    _legend_above(ax, ncol=2)
    _style_axis(ax)
    _add_period_note(fig, chart_df)
    _save_figure(fig, output_pdf, output_png)
    plt.close(fig)
    logger.info("Wrote fundamental margin chart: %s, %s", output_pdf, output_png)

    return {
        "figure_id": "fig_fundamental_margin_trend",
        "title": "동일 기간 공헌이익률과 판매관리비율 비교",
        "chart_type": "grouped_bar_comparison",
        "section_recommendation": "재무 분석",
        "asset_path_pdf": "figures/fundamental_margin_trend.pdf",
        "asset_path_png": "figures/fundamental_margin_trend.png",
        "asset_abs_path_pdf": str(output_pdf),
        "asset_abs_path_png": str(output_png),
        "data_source": "dart_main.json",
        "used_metrics": ["contribution_margin", "sga_margin"],
        "caption": (
            "DART 기준 당기와 전년 동기의 공헌이익률 및 판관비율을 동일 기간 기준으로 비교한다."
        ),
        "writer_allowed_interpretation": (
            "동일 누적기간의 공헌이익률과 판관비율 차이를 바탕으로 수익성 구조 변화를 설명할 수 있다."
        ),
        "writer_forbidden_interpretation": [
            "contribution_margin을 별도 근거 없는 이익률 지표로 표현하지 않는다.",
            "sga_margin 개선만으로 별도 산출되지 않은 수익성 지표 개선을 단정하지 않는다.",
            "서로 다른 누적기간이나 연간 수치를 동일 기준으로 비교하지 않는다.",
            "추가 근거 없이 수익성, 자본효율성, 밸류에이션 지표를 임의 확장하지 않는다.",
        ],
        "data_limitations": [
            "누적 수치는 연간 확정치가 아니다.",
            "추가 수익성 및 자본효율성 지표는 별도 근거가 있을 때만 해석한다.",
        ],
    }


def build_indexed_stock_vs_kospi_chart(
    market_df: pd.DataFrame,
    output_pdf: str | Path,
    output_png: str | Path,
    company_name: str,
) -> dict:
    """Create indexed target stock price vs KOSPI performance chart."""

    output_pdf, output_png = _prepare_output_paths(output_pdf, output_png)
    required_columns = ["date", "stock_close", "kospi_close"]
    _require_columns(market_df, required_columns, "indexed stock vs KOSPI chart")
    chart_df = market_df.dropna(subset=required_columns).sort_values("date").copy()
    if chart_df.empty:
        raise ValueError("Indexed stock vs KOSPI chart data is empty.")

    chart_df["stock_index"] = chart_df["stock_close"] / chart_df["stock_close"].iloc[0] * 100.0
    chart_df["kospi_index"] = chart_df["kospi_close"] / chart_df["kospi_close"].iloc[0] * 100.0
    chart_df["relative_gap"] = chart_df["stock_index"] - chart_df["kospi_index"]
    latest = chart_df.iloc[-1]

    fig, ax = _new_figure()
    title_company = _safe_title_company(company_name)
    ax.fill_between(
        chart_df["date"],
        chart_df["stock_index"],
        chart_df["kospi_index"],
        where=chart_df["relative_gap"] >= 0,
        color=OUTPERFORM_FILL,
        linewidth=0,
        interpolate=True,
    )
    ax.fill_between(
        chart_df["date"],
        chart_df["stock_index"],
        chart_df["kospi_index"],
        where=chart_df["relative_gap"] < 0,
        color=UNDERPERFORM_FILL,
        linewidth=0,
        interpolate=True,
    )
    ax.axhline(100.0, color=MUTED, linewidth=0.6, linestyle="--")
    ax.plot(chart_df["date"], chart_df["kospi_index"], color=BENCHMARK, linewidth=1.1, label="KOSPI")
    ax.plot(chart_df["date"], chart_df["stock_index"], color=ACCENT, linewidth=1.4, label=title_company)
    # End labels sit right of the last point; the higher series is nudged up, the lower down.
    stock_on_top = latest["stock_index"] >= latest["kospi_index"]
    _end_label(ax, latest["date"], latest["stock_index"], f"{latest['stock_index']:.1f}", ACCENT, 3 if stock_on_top else -3)
    _end_label(ax, latest["date"], latest["kospi_index"], f"KOSPI {latest['kospi_index']:.1f}", MUTED, -3 if stock_on_top else 3)
    _reserve_end_label_space(ax, chart_df["date"], 0.11)
    _set_title(ax, f"{title_company}와 KOSPI 지수화 성과")
    ax.set_ylabel("지수(시작일=100)", fontsize=LABEL_SIZE)
    _legend_above(ax, ncol=2, reverse=True)
    _date_axis(ax, chart_df["date"])
    _style_axis(ax)
    _save_figure(fig, output_pdf, output_png)
    plt.close(fig)
    logger.info("Wrote indexed stock vs KOSPI chart: %s, %s", output_pdf, output_png)

    return {
        "figure_id": "fig_indexed_stock_vs_kospi",
        "title": "대상기업과 KOSPI 지수화 성과",
        "chart_type": "indexed_time_series",
        "section_recommendation": "시장·상대성과 분석",
        "asset_path_pdf": "figures/indexed_stock_vs_kospi.pdf",
        "asset_path_png": "figures/indexed_stock_vs_kospi.png",
        "asset_abs_path_pdf": str(output_pdf),
        "asset_abs_path_png": str(output_png),
        "data_source": "market_full_dataset.csv",
        "used_columns": ["date", "stock_close", "kospi_close"],
        "derived_columns": ["stock_index", "kospi_index", "relative_gap"],
        "caption": f"{title_company} 주가와 KOSPI를 시작일 100으로 지수화해 절대 성과와 시장 대비 성과를 함께 보여준다.",
        "writer_allowed_interpretation": "절대 주가 흐름과 KOSPI 대비 상대 성과의 방향성을 설명할 수 있다.",
        "writer_forbidden_interpretation": [
            "지수화 성과만으로 목표주가나 투자수익률 전망을 산출하지 않는다.",
            "시장 대비 부진을 기업 펀더멘털 악화로 단정하지 않는다.",
        ],
        "data_limitations": [
            "지수화 기준일 선택에 따라 성과 격차의 시각적 크기는 달라질 수 있다.",
        ],
    }


def build_peer_return_comparison_chart(
    peer_return_df: pd.DataFrame,
    output_pdf: str | Path,
    output_png: str | Path,
) -> dict:
    """Create peer return and relative-performance comparison chart."""

    output_pdf, output_png = _prepare_output_paths(output_pdf, output_png)
    required_columns = [
        "company_name",
        "stock_return_5d_pct",
        "stock_return_20d_pct",
        "stock_return_60d_pct",
        "stock_excess_return_20d_pct",
        "stock_relative_strength_60_pct",
    ]
    _require_columns(peer_return_df, required_columns, "peer return comparison chart")
    chart_df = peer_return_df.copy()
    chart_df["company_label"] = chart_df["company_name"].map(_safe_company_label)
    x = np.arange(len(chart_df))
    width = 0.24

    fig, axes = _new_figure(1, 2, gridspec_kw={"width_ratios": [1.25, 1.0]})
    returns_ax, relative_ax = axes

    return_specs = [
        ("stock_return_5d_pct", "5일", ACCENT, -width),
        ("stock_return_20d_pct", "20일", SECOND, 0.0),
        ("stock_return_60d_pct", "60일", THIRD_BAR, width),
    ]
    for column, label, color, offset in return_specs:
        bars = returns_ax.bar(x + offset, chart_df[column], width=width, label=label, color=color)
        _label_bars(returns_ax, bars)
    returns_ax.axhline(0.0, color=MUTED, linewidth=0.6)
    _set_title(returns_ax, "대상기업과 비교기업 주가수익률", size=PANEL_TITLE_SIZE)
    returns_ax.set_ylabel("수익률(%)", fontsize=LABEL_SIZE)
    returns_ax.set_xticks(x)
    returns_ax.set_xticklabels(chart_df["company_label"])
    _legend_below(returns_ax, ncol=3)
    _style_axis(returns_ax)

    relative_specs = [
        ("stock_excess_return_20d_pct", "20일 KOSPI 초과수익률", ACCENT, -width / 2),
        ("stock_relative_strength_60_pct", "60일 상대강도", SECOND, width / 2),
    ]
    for column, label, color, offset in relative_specs:
        bars = relative_ax.bar(x + offset, chart_df[column], width=width, label=label, color=color)
        _label_bars(relative_ax, bars)
    relative_ax.axhline(0.0, color=MUTED, linewidth=0.6)
    _set_title(relative_ax, "시장 대비 상대성과", size=PANEL_TITLE_SIZE)
    relative_ax.set_ylabel("상대성과(%)", fontsize=LABEL_SIZE)
    relative_ax.set_xticks(x)
    relative_ax.set_xticklabels(chart_df["company_label"])
    _legend_below(relative_ax, ncol=2)
    _style_axis(relative_ax)

    _save_figure(fig, output_pdf, output_png)
    plt.close(fig)
    logger.info("Wrote peer return comparison chart: %s, %s", output_pdf, output_png)

    return {
        "figure_id": "fig_peer_return_comparison",
        "title": "대상기업과 비교기업 주가·상대성과",
        "chart_type": "peer_group_bar_chart",
        "section_recommendation": "비교기업·시장 분석",
        "asset_path_pdf": "figures/peer_return_comparison.pdf",
        "asset_path_png": "figures/peer_return_comparison.png",
        "asset_abs_path_pdf": str(output_pdf),
        "asset_abs_path_png": str(output_png),
        "data_source": "Y_Finance peer market_summary / market_full_dataset files",
        "used_columns": [
            "stock_return_5d",
            "stock_return_20d",
            "stock_return_60d",
            "stock_excess_return_20d",
            "stock_relative_strength_60",
        ],
        "derived_columns": [
            "stock_return_5d_pct",
            "stock_return_20d_pct",
            "stock_return_60d_pct",
            "stock_excess_return_20d_pct",
            "stock_relative_strength_60_pct",
        ],
        "caption": "비교 기업들의 단기·중기 주가 수익률과 시장 대비 상대성과를 비교한다.",
        "writer_allowed_interpretation": "동일 기준일의 시장 성과 비교와 상대강도 차이를 설명할 수 있다.",
        "writer_forbidden_interpretation": [
            "비교기업 주가수익률만으로 펀더멘털 우열을 단정하지 않는다.",
            "상대성과를 목표주가나 추천 의견으로 변환하지 않는다.",
        ],
        "data_limitations": [
            "비교기업 분석은 현재 확보된 동일 기준일 국내 비교 대상만 포함한다.",
        ],
    }


def build_peer_profitability_comparison_chart(
    peer_profitability_df: pd.DataFrame,
    output_pdf: str | Path,
    output_png: str | Path,
) -> dict:
    """Create domestic peer revenue and profitability comparison chart."""

    output_pdf, output_png = _prepare_output_paths(output_pdf, output_png)
    required_columns = ["company_name", "revenue_100m", "contribution_margin_pct", "sga_margin_pct", "eps"]
    _require_columns(peer_profitability_df, required_columns, "peer profitability comparison chart")
    chart_df = peer_profitability_df.copy().reset_index(drop=True)
    # Matplotlib needs numeric NaN, not object-dtype None. Keep missing values
    # missing rather than plotting them as zero or inventing an estimate.
    for column in required_columns[1:]:
        chart_df[column] = pd.to_numeric(chart_df[column], errors="raise").astype(float)
    if chart_df[["revenue_100m", "contribution_margin_pct", "sga_margin_pct", "eps"]].isna().all(axis=None):
        raise ValueError("Peer profitability comparison chart has no usable numeric data.")

    chart_df["company_label"] = chart_df["company_name"].map(_safe_company_label)
    x = np.arange(len(chart_df))
    width = 0.34

    fig, axes = _new_figure(1, 3, gridspec_kw={"width_ratios": [1.0, 1.25, 1.0]})
    revenue_ax, margin_ax, eps_ax = axes

    revenue_bars = revenue_ax.bar(x, chart_df["revenue_100m"], width=0.46, color=ACCENT, label="매출")
    _label_bars(revenue_ax, revenue_bars)
    _set_title(revenue_ax, "매출 규모", size=PANEL_TITLE_SIZE)
    revenue_ax.set_ylabel("억원", fontsize=LABEL_SIZE)
    revenue_ax.set_xticks(x)
    revenue_ax.set_xticklabels(chart_df["company_label"])
    revenue_ax.yaxis.set_major_formatter(FuncFormatter(lambda value, _: f"{value:,.0f}"))
    _style_axis(revenue_ax)

    bars = margin_ax.bar(
        x - width / 2,
        chart_df["contribution_margin_pct"],
        width=width,
        label="공헌이익률",
        color=ACCENT,
    )
    _label_bars(margin_ax, bars)
    bars = margin_ax.bar(
        x + width / 2,
        chart_df["sga_margin_pct"],
        width=width,
        label="판매관리비율",
        color=SECOND,
    )
    _label_bars(margin_ax, bars)
    _set_title(margin_ax, "수익성 구조", size=PANEL_TITLE_SIZE)
    margin_ax.set_ylabel("이익률(%)", fontsize=LABEL_SIZE)
    margin_ax.set_xticks(x)
    margin_ax.set_xticklabels(chart_df["company_label"])
    _legend_below(margin_ax, ncol=2)
    _style_axis(margin_ax)

    eps_colors = [MISSING_BAR if pd.isna(value) else ACCENT if value >= 0 else NEGATIVE_BAR for value in chart_df["eps"]]
    eps_bars = eps_ax.bar(x, chart_df["eps"], width=0.46, color=eps_colors, label="주당순이익")
    _label_bars(eps_ax, eps_bars)
    for index, value in enumerate(chart_df["eps"]):
        if pd.isna(value):
            eps_ax.text(index, 0.05, "자료 없음", transform=eps_ax.get_xaxis_transform(), ha="center", fontsize=VALUE_LABEL_SIZE, color=MUTED)
    eps_ax.axhline(0, color=MUTED, linewidth=0.6)
    _set_title(eps_ax, "주당순이익", size=PANEL_TITLE_SIZE)
    eps_ax.set_ylabel("원", fontsize=LABEL_SIZE)
    eps_ax.set_xticks(x)
    eps_ax.set_xticklabels(chart_df["company_label"])
    eps_ax.yaxis.set_major_formatter(FuncFormatter(lambda value, _: f"{value:,.0f}"))
    _style_axis(eps_ax)

    # The report caption does not carry this scope note, so it stays in the figure.
    fig.supxlabel(
        "주: 국내 비교기업 기준이며, 결측치는 보간하지 않았다. 해외 비교기업, 가치평가 지표, 업종 평균은 포함하지 않았다.",
        x=0.0,
        ha="left",
        fontsize=NOTE_SIZE,
        color=MUTED,
    )
    _save_figure(fig, output_pdf, output_png)
    plt.close(fig)
    logger.info("Wrote peer profitability comparison chart: %s, %s", output_pdf, output_png)

    return {
        "figure_id": "fig_peer_profitability_comparison",
        "title": "대상기업과 비교기업 매출·수익성",
        "chart_type": "peer_group_bar_chart",
        "section_recommendation": "비교기업·수익성 분석",
        "asset_path_pdf": "figures/peer_profitability_comparison.pdf",
        "asset_path_png": "figures/peer_profitability_comparison.png",
        "asset_abs_path_pdf": str(output_pdf),
        "asset_abs_path_png": str(output_png),
        "data_source": "Competitor peer_comparison_dataset.json",
        "used_metrics": ["revenue_100m", "contribution_margin_pct", "sga_margin_pct", "eps"],
        "caption": "국내 비교기업을 기준으로 매출 규모, 공헌이익률, 판매관리비율, 주당순이익을 비교한다.",
        "writer_allowed_interpretation": "국내 비교군 안에서 대상 기업의 매출 규모, 수익성 구조, 비용 효율성, EPS 위치를 설명할 수 있다.",
        "writer_forbidden_interpretation": [
            "global peer와의 우열을 언급하지 않는다.",
            "PER/PBR/PSR/EV/Sales 등 valuation 비교로 확장하지 않는다.",
            "업종 평균 대비 할인 또는 프리미엄을 단정하지 않는다.",
            "결측치를 임의로 추정하지 않는다.",
        ],
        "data_limitations": [
            "국내 비교기업 분석은 현재 확보된 동일 기준일 비교 대상만 포함한다.",
            "일부 peer의 재무 항목은 N/A이며 보간하지 않는다.",
            "누적 수치가 포함된 경우 연간 확정치와 직접 비교하지 않는다.",
        ],
    }


def build_revenue_profit_sga_trend_chart(
    income_df: pd.DataFrame,
    output_pdf: str | Path,
    output_png: str | Path,
    company_name: str,
) -> dict:
    """Create revenue, contribution profit, and SG&A trend chart."""

    output_pdf, output_png = _prepare_output_paths(output_pdf, output_png)
    required_columns = [
        "period_key",
        "period_label",
        "period_type",
        "revenue_krw_bn",
        "contribution_profit_krw_bn",
        "sga_krw_bn",
        "basis",
    ]
    _require_columns(income_df, required_columns, "revenue/profit/SG&A trend chart")
    chart_df = _select_comparable_period_rows(income_df)
    if chart_df[["revenue_krw_bn", "contribution_profit_krw_bn", "sga_krw_bn"]].isna().all(axis=None):
        raise ValueError("Revenue/profit/SG&A trend chart has no usable numeric data.")

    x = np.arange(len(chart_df))
    fig, ax = _new_figure()
    specs = [
        ("revenue_krw_bn", "매출", ACCENT, "-"),
        ("contribution_profit_krw_bn", "공헌이익", SECOND, "-"),
        ("sga_krw_bn", "판매관리비", THIRD_LINE, (0, (4, 2))),
    ]
    for column, label, color, linestyle in specs:
        ax.plot(
            x,
            chart_df[column],
            linewidth=1.4,
            linestyle=linestyle,
            marker="o",
            markersize=3.5,
            label=label,
            color=color,
        )

    title_company = _safe_title_company(company_name)
    _set_title(ax, f"{title_company} 매출·공헌이익·판매관리비 추이")
    ax.set_xticks(x)
    ax.set_xticklabels(chart_df["period_label"].tolist())
    ax.set_xlim(-0.4, len(chart_df) - 0.6)
    ax.set_ylabel("십억원", fontsize=LABEL_SIZE)
    ax.yaxis.set_major_formatter(FuncFormatter(lambda value, _: f"{value:,.0f}"))
    _legend_above(ax, ncol=3)
    _style_axis(ax)
    _add_period_note(fig, chart_df)
    _save_figure(fig, output_pdf, output_png)
    plt.close(fig)
    logger.info("Wrote revenue/profit/SG&A trend chart: %s, %s", output_pdf, output_png)

    return {
        "figure_id": "fig_revenue_profit_sga_trend",
        "title": "매출·공헌이익·판매관리비 추이",
        "chart_type": "line_time_series",
        "section_recommendation": "재무 분석",
        "asset_path_pdf": "figures/revenue_profit_sga_trend.pdf",
        "asset_path_png": "figures/revenue_profit_sga_trend.png",
        "asset_abs_path_pdf": str(output_pdf),
        "asset_abs_path_png": str(output_png),
        "data_source": "dart_main.json",
        "used_metrics": ["revenue", "contribution_profit", "sga"],
        "derived_columns": ["revenue_krw_bn", "contribution_profit_krw_bn", "sga_krw_bn"],
        "caption": "DART 기준 매출, 공헌이익, 판관비의 금액 추이를 함께 보여준다. 누적 수치가 포함된 경우 연간 확정치가 아니다.",
        "writer_allowed_interpretation": "매출 규모, 공헌이익, 판관비 부담의 방향성을 설명할 수 있다.",
        "writer_forbidden_interpretation": [
            "누적 수치와 연간 수치를 동일 기준의 전년 대비 성장으로 단정하지 않는다.",
            "공헌이익을 영업이익 또는 순이익으로 표현하지 않는다.",
        ],
        "data_limitations": [
            "누적 수치가 포함된 경우 연간 확정치가 아니다.",
        ],
    }


def build_liquidity_leverage_peer_comparison_chart(
    financial_health_df: pd.DataFrame,
    output_pdf: str | Path,
    output_png: str | Path,
) -> dict:
    """Create peer liquidity and leverage comparison chart."""

    output_pdf, output_png = _prepare_output_paths(output_pdf, output_png)
    required_columns = ["company_name", "current_ratio_pct", "cash_ratio_pct", "equity_ratio_pct", "debt_to_equity_pct"]
    _require_columns(financial_health_df, required_columns, "liquidity/leverage peer comparison chart")
    chart_df = financial_health_df.copy()
    chart_df["company_label"] = chart_df["company_name"].map(_safe_company_label)
    x = np.arange(len(chart_df))
    width = 0.34

    fig, axes = _new_figure(1, 2)
    liquidity_ax, leverage_ax = axes

    bars = liquidity_ax.bar(x - width / 2, chart_df["current_ratio_pct"], width=width, label="유동비율", color=ACCENT)
    _label_bars(liquidity_ax, bars)
    bars = liquidity_ax.bar(x + width / 2, chart_df["cash_ratio_pct"], width=width, label="현금비율", color=SECOND)
    _label_bars(liquidity_ax, bars)
    _set_title(liquidity_ax, "대상기업과 비교기업 유동성", size=PANEL_TITLE_SIZE)
    liquidity_ax.set_ylabel("비율(%)", fontsize=LABEL_SIZE)
    liquidity_ax.set_xticks(x)
    liquidity_ax.set_xticklabels(chart_df["company_label"])
    _legend_below(liquidity_ax, ncol=2)
    _style_axis(liquidity_ax)

    bars = leverage_ax.bar(x - width / 2, chart_df["equity_ratio_pct"], width=width, label="자기자본비율", color=ACCENT)
    _label_bars(leverage_ax, bars)
    bars = leverage_ax.bar(x + width / 2, chart_df["debt_to_equity_pct"], width=width, label="부채비율", color=SECOND)
    _label_bars(leverage_ax, bars)
    _set_title(leverage_ax, "자본구조와 레버리지", size=PANEL_TITLE_SIZE)
    leverage_ax.set_ylabel("비율(%)", fontsize=LABEL_SIZE)
    leverage_ax.set_xticks(x)
    leverage_ax.set_xticklabels(chart_df["company_label"])
    _legend_below(leverage_ax, ncol=2)
    _style_axis(leverage_ax)

    _save_figure(fig, output_pdf, output_png)
    plt.close(fig)
    logger.info("Wrote liquidity/leverage peer comparison chart: %s, %s", output_pdf, output_png)

    return {
        "figure_id": "fig_liquidity_leverage_peer_comparison",
        "title": "대상기업과 비교기업 유동성·레버리지",
        "chart_type": "peer_group_bar_chart",
        "section_recommendation": "비교기업·재무안정성 분석",
        "asset_path_pdf": "figures/liquidity_leverage_peer_comparison.pdf",
        "asset_path_png": "figures/liquidity_leverage_peer_comparison.png",
        "asset_abs_path_pdf": str(output_pdf),
        "asset_abs_path_png": str(output_png),
        "data_source": "Financial final_report.json files",
        "used_metrics": ["current_ratio", "cash_ratio", "equity_ratio", "debt_to_equity"],
        "derived_columns": ["current_ratio_pct", "cash_ratio_pct", "equity_ratio_pct", "debt_to_equity_pct"],
        "caption": "비교 기업의 유동비율, 현금비율, 자본비율, 부채비율을 비교해 재무 안정성 차이를 보여준다.",
        "writer_allowed_interpretation": "동일 기준 산출물 내에서 비교기업 간 유동성 및 부채 부담의 상대적 차이를 설명할 수 있다.",
        "writer_forbidden_interpretation": [
            "유동성 지표만으로 투자 의견을 산출하지 않는다.",
            "부채비율이 낮다는 이유만으로 성장성 또는 수익성을 단정하지 않는다.",
        ],
        "data_limitations": [
            "재무 안정성 비교는 구조화된 재무 지표 기준으로 제한한다.",
        ],
    }


def _require_columns(df: pd.DataFrame, columns: list[str], context: str) -> None:
    missing = [column for column in columns if column not in df.columns]
    if missing:
        raise ValueError(f"{context} missing required columns: {missing}")


def _select_comparable_period_rows(chart_df: pd.DataFrame) -> pd.DataFrame:
    """Prefer current and prior-year same-period rows; otherwise use annual rows."""

    comparable_keys = {"same_period_previous_year", "current_fiscal_year"}
    same_period = chart_df[chart_df["period_key"].isin(comparable_keys)].copy()
    if len(same_period) == 2:
        basis_values = same_period["basis"].dropna().astype(str).unique().tolist()
        period_types = same_period["period_type"].dropna().astype(str).unique().tolist()
        if len(basis_values) == 1 and len(period_types) == 1:
            return same_period.sort_values("period_end").reset_index(drop=True)

    annual = chart_df[chart_df["basis"] == "FULL_YEAR"].copy()
    if len(annual) >= 2:
        return annual.sort_values("period_end").tail(4).reset_index(drop=True)

    fallback = chart_df[chart_df["basis"] != "TTM"].copy()
    return fallback.sort_values("period_end").tail(4).reset_index(drop=True)


def _period_comparison_note(chart_df: pd.DataFrame) -> str:
    if chart_df.empty:
        return "주: DART가 제공한 기간 구분을 적용했다."
    bases = chart_df["basis"].dropna().astype(str).unique().tolist()
    period_types = chart_df["period_type"].dropna().astype(str).unique().tolist()
    if bases == ["YTD"] and len(period_types) == 1:
        label = {
            "Q1": "1분기 누적",
            "Q2": "2분기 누적",
            "HALF": "반기 누적",
            "Q3": "3분기 누적",
            "Q4": "4분기 누적",
        }.get(period_types[0], "누적")
        return f"주: {label} 기준으로 당기와 전년 동기를 비교했다."
    if bases == ["FULL_YEAR"]:
        return "주: 연간 확정치 기준으로 비교했다."
    return "주: 동일한 기간 구분의 수치만 비교해야 한다."


def _prepare_output_paths(output_pdf: str | Path, output_png: str | Path) -> tuple[Path, Path]:
    output_pdf = Path(output_pdf).expanduser().resolve()
    output_png = Path(output_png).expanduser().resolve()
    output_pdf.parent.mkdir(parents=True, exist_ok=True)
    output_png.parent.mkdir(parents=True, exist_ok=True)
    return output_pdf, output_png


def _label_bars(ax, bars, *, suffix: str = "") -> None:
    for bar in bars:
        height = bar.get_height()
        if pd.isna(height):
            continue
        va = "bottom" if height >= 0 else "top"
        offset = 2 if height >= 0 else -2
        ax.annotate(
            f"{height:.1f}{suffix}",
            xy=(bar.get_x() + bar.get_width() / 2, height),
            xytext=(0, offset),
            textcoords="offset points",
            ha="center",
            va=va,
            fontsize=VALUE_LABEL_SIZE,
            color=INK,
        )
    # Leave headroom so value labels stay inside the plot area.
    ax.margins(y=0.12)


def _style_axis(ax) -> None:
    ax.set_axisbelow(True)
    # Round steps keep integer-formatted tick labels (such as "12%") truthful.
    ax.yaxis.set_major_locator(MaxNLocator(nbins=5, steps=[1, 2, 5, 10]))
    ax.grid(True, axis="y", color=GRID, linewidth=0.6)
    ax.grid(False, axis="x")
    ax.spines["top"].set_visible(False)
    ax.spines["right"].set_visible(False)
    for side in ("left", "bottom"):
        ax.spines[side].set_color(SPINE)
        ax.spines[side].set_linewidth(0.6)
    ax.tick_params(axis="both", colors=SPINE, labelcolor=MUTED, labelsize=TICK_SIZE, length=2.5, width=0.6)
    ax.yaxis.label.set_color(MUTED)


def _add_period_note(fig, chart_df: pd.DataFrame) -> None:
    """Add the period note only when it says more than the period tick labels.

    Tick labels already name the period and its cumulative or annual basis, and the
    report caption names the comparison, so the note is kept only for mixed bases.
    """

    if _uses_single_period_basis(chart_df):
        return
    fig.supxlabel(_period_comparison_note(chart_df), x=0.0, ha="left", fontsize=NOTE_SIZE, color=MUTED)


def _uses_single_period_basis(chart_df: pd.DataFrame) -> bool:
    bases = chart_df["basis"].dropna().astype(str).unique().tolist()
    period_types = chart_df["period_type"].dropna().astype(str).unique().tolist()
    return bases == ["FULL_YEAR"] or (bases == ["YTD"] and len(period_types) == 1)


def _save_figure(fig, output_pdf: Path, output_png: Path) -> None:
    # Saved at the exact figure size so every chart keeps the same printed proportions.
    fig.savefig(output_pdf)
    fig.savefig(output_png, dpi=PNG_DPI)


def _safe_title_company(company_name: str) -> str:
    label = str(company_name or "").strip()
    return label or "Target Company"


def _safe_company_label(company_name: str) -> str:
    label = str(company_name or "").strip()
    return label or "Peer"
