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
from matplotlib.ticker import FuncFormatter


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


def build_stock_price_ma_volume_relative_strength_chart(
    market_df: pd.DataFrame,
    output_pdf: str | Path,
    output_png: str | Path,
    company_name: str,
) -> dict:
    """Create the market composite chart and return manifest metadata."""

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
        "stock_excess_return_20d_pct",
        "stock_relative_strength_60_pct",
    ]
    _require_columns(market_df, required_columns, "market chart")
    chart_df = market_df.dropna(subset=["date", "stock_close"]).sort_values("date")
    if chart_df.empty:
        raise ValueError("Market chart data is empty after excluding rows without stock_close.")

    fig, axes = plt.subplots(
        nrows=3,
        ncols=1,
        figsize=(12, 8),
        sharex=True,
        gridspec_kw={"height_ratios": [2.3, 1.0, 1.2]},
    )
    fig.patch.set_facecolor("white")

    title_company = _safe_title_company(company_name)
    price_ax, volume_ax, relative_ax = axes
    price_ax.plot(chart_df["date"], chart_df["stock_close"], color="#1f5a99", linewidth=2.0, label="Close")
    price_ax.plot(chart_df["date"], chart_df["derived_ma20"], color="#2b8a3e", linewidth=1.5, label="Derived MA20")
    price_ax.plot(chart_df["date"], chart_df["derived_ma60"], color="#b7791f", linewidth=1.5, label="Derived MA60")
    latest = chart_df.iloc[-1]
    price_ax.annotate(
        f"{latest['stock_close']:,.0f} KRW",
        xy=(latest["date"], latest["stock_close"]),
        xytext=(-85, 18),
        textcoords="offset points",
        arrowprops={"arrowstyle": "->", "color": "#4a5568", "lw": 0.8},
        fontsize=9,
        color="#1a202c",
    )
    price_ax.set_title(f"{title_company} Stock Price with MA20/MA60", fontsize=12, loc="left")
    price_ax.set_ylabel("Price (KRW)")
    price_ax.yaxis.set_major_formatter(FuncFormatter(lambda value, _: f"{value:,.0f}"))
    price_ax.legend(loc="upper left", ncol=3, frameon=False)
    _style_axis(price_ax)

    volume_ax.plot(chart_df["date"], chart_df["stock_volume_ratio_20"], color="#5f6368", linewidth=1.5, label="20D Volume Ratio")
    volume_ax.axhline(1.0, color="#a0aec0", linewidth=1.0, linestyle="--")
    volume_ax.set_title("20D Volume Ratio", fontsize=11, loc="left")
    volume_ax.set_ylabel("Volume Ratio (20D)")
    _style_axis(volume_ax)

    relative_ax.plot(
        chart_df["date"],
        chart_df["stock_excess_return_20d_pct"],
        color="#0f766e",
        linewidth=1.6,
        label="20D Excess Return",
    )
    relative_ax.plot(
        chart_df["date"],
        chart_df["stock_relative_strength_60_pct"],
        color="#c2410c",
        linewidth=1.6,
        label="60D Relative Strength",
    )
    relative_ax.axhline(0.0, color="#a0aec0", linewidth=1.0, linestyle="--")
    relative_ax.set_title("20D Excess Return and 60D Relative Strength", fontsize=11, loc="left")
    relative_ax.set_ylabel("Relative Performance (%)")
    relative_ax.legend(loc="upper left", ncol=2, frameon=False)
    _style_axis(relative_ax)

    locator = mdates.AutoDateLocator(minticks=6, maxticks=10)
    relative_ax.xaxis.set_major_locator(locator)
    relative_ax.xaxis.set_major_formatter(mdates.ConciseDateFormatter(locator))
    relative_ax.set_xlabel("Date")

    fig.tight_layout()
    _save_figure(fig, output_pdf, output_png)
    plt.close(fig)
    logger.info("Wrote market chart: %s, %s", output_pdf, output_png)

    return {
        "figure_id": "fig_stock_price_ma_volume_relative_strength",
        "title": "Stock Price with MA20/MA60, Volume Ratio, and Relative Strength",
        "chart_type": "multi_panel_time_series",
        "section_recommendation": "Market / Price View",
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
            "stock_excess_return_20d",
            "stock_relative_strength_60",
        ],
        "derived_columns": [
            "derived_ma20",
            "derived_ma60",
            "stock_excess_return_20d_pct",
            "stock_relative_strength_60_pct",
        ],
        "caption": (
            "주가는 20일 및 60일 이동평균선 대비 위치, 20일 거래량 비율, "
            "20일 초과수익률 및 60일 상대강도를 함께 보여준다."
        ),
        "writer_allowed_interpretation": (
            "주가의 절대 추세, 이동평균선 대비 위치, 거래량 활성도, "
            "시장 대비 상대성과를 설명할 수 있다."
        ),
        "writer_forbidden_interpretation": [
            "이동평균선 상회만으로 매수 신호라고 단정하지 않는다.",
            "거래량 증가만으로 실적 개선을 단정하지 않는다.",
            "상대강도 약세를 기업 펀더멘털 악화로 단정하지 않는다.",
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

    required_columns = ["period_label", "contribution_margin_pct", "sga_margin_pct", "basis"]
    _require_columns(margin_df, required_columns, "fundamental margin chart")
    chart_df = margin_df.reset_index(drop=True)
    if chart_df.empty:
        raise ValueError("Fundamental margin chart data is empty.")

    fig, ax = plt.subplots(figsize=(10, 6))
    fig.patch.set_facecolor("white")
    x_positions = list(range(len(chart_df)))
    ax.plot(
        x_positions,
        chart_df["contribution_margin_pct"],
        color="#1f5a99",
        linewidth=2.0,
        marker="o",
        label="Contribution Margin",
    )
    ax.plot(
        x_positions,
        chart_df["sga_margin_pct"],
        color="#b7791f",
        linewidth=2.0,
        marker="o",
        label="SG&A Margin",
    )
    for index, row in chart_df.iterrows():
        if row.get("basis") == "YTD":
            ax.annotate(
                "Q3 YTD\nnot FY",
                xy=(index, row["contribution_margin_pct"]),
                xytext=(8, 18),
                textcoords="offset points",
                arrowprops={"arrowstyle": "->", "color": "#4a5568", "lw": 0.8},
                fontsize=9,
                color="#4a5568",
            )

    title_company = _safe_title_company(company_name)
    ax.set_title(f"{title_company} Contribution Margin and SG&A Margin Trend", fontsize=12, loc="left")
    ax.set_xticks(x_positions)
    ax.set_xticklabels(chart_df["period_label"].tolist())
    ax.set_ylabel("Percentage (%)")
    ax.yaxis.set_major_formatter(FuncFormatter(lambda value, _: f"{value:.0f}%"))
    ax.legend(loc="best", frameon=False)
    _style_axis(ax)
    fig.text(0.01, 0.01, _ytd_note(chart_df), fontsize=9, color="#4a5568")
    fig.tight_layout(rect=(0, 0.04, 1, 1))
    _save_figure(fig, output_pdf, output_png)
    plt.close(fig)
    logger.info("Wrote fundamental margin chart: %s, %s", output_pdf, output_png)

    return {
        "figure_id": "fig_fundamental_margin_trend",
        "title": "Contribution Margin and SG&A Margin Time-Series Fundamental Trend",
        "chart_type": "line_time_series",
        "section_recommendation": "Financial Analysis",
        "asset_path_pdf": "figures/fundamental_margin_trend.pdf",
        "asset_path_png": "figures/fundamental_margin_trend.png",
        "asset_abs_path_pdf": str(output_pdf),
        "asset_abs_path_png": str(output_png),
        "data_source": "dart_main.json",
        "used_metrics": ["contribution_margin", "sga_margin"],
        "caption": (
            "DART 기준 공헌이익률과 판관비율의 추이를 보여준다. YTD 수치가 포함된 경우 "
            "연간 수치와 직접 비교에는 제한이 있다."
        ),
        "writer_allowed_interpretation": (
            "공헌이익률과 판관비율의 방향성을 바탕으로 수익성 구조 변화를 설명할 수 있다. "
            "단, YTD 수치를 연간 수치와 직접 YoY 개선으로 단정하지 않는다."
        ),
        "writer_forbidden_interpretation": [
            "contribution_margin을 별도 근거 없는 이익률 지표로 표현하지 않는다.",
            "sga_margin 개선만으로 별도 산출되지 않은 수익성 지표 개선을 단정하지 않는다.",
            "YTD 수치와 FY 수치를 동일 기준 YoY로 단정하지 않는다.",
            "추가 근거 없이 수익성, 자본효율성, 밸류에이션 지표를 임의 확장하지 않는다.",
        ],
        "data_limitations": [
            "YTD 수치가 포함된 경우 연간 확정치가 아니다.",
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

    fig, ax = plt.subplots(figsize=(11, 6))
    fig.patch.set_facecolor("white")
    title_company = _safe_title_company(company_name)
    ax.plot(chart_df["date"], chart_df["stock_index"], color="#1f5a99", linewidth=2.2, label=title_company)
    ax.plot(chart_df["date"], chart_df["kospi_index"], color="#5f6368", linewidth=1.8, label="KOSPI")
    ax.fill_between(
        chart_df["date"],
        chart_df["stock_index"],
        chart_df["kospi_index"],
        where=chart_df["relative_gap"] >= 0,
        color="#2b8a3e",
        alpha=0.10,
        interpolate=True,
    )
    ax.fill_between(
        chart_df["date"],
        chart_df["stock_index"],
        chart_df["kospi_index"],
        where=chart_df["relative_gap"] < 0,
        color="#c2410c",
        alpha=0.10,
        interpolate=True,
    )
    ax.axhline(100.0, color="#a0aec0", linewidth=1.0, linestyle="--")
    ax.annotate(
        f"{latest['stock_index']:.1f}",
        xy=(latest["date"], latest["stock_index"]),
        xytext=(-48, 18),
        textcoords="offset points",
        arrowprops={"arrowstyle": "->", "color": "#1f5a99", "lw": 0.8},
        fontsize=9,
        color="#1f5a99",
    )
    ax.annotate(
        f"KOSPI {latest['kospi_index']:.1f}",
        xy=(latest["date"], latest["kospi_index"]),
        xytext=(-78, -24),
        textcoords="offset points",
        arrowprops={"arrowstyle": "->", "color": "#5f6368", "lw": 0.8},
        fontsize=9,
        color="#5f6368",
    )
    ax.set_title(f"{title_company} Indexed Performance vs KOSPI", fontsize=12, loc="left")
    ax.set_ylabel("Index (first date = 100)")
    ax.legend(loc="upper left", frameon=False)
    locator = mdates.AutoDateLocator(minticks=6, maxticks=10)
    ax.xaxis.set_major_locator(locator)
    ax.xaxis.set_major_formatter(mdates.ConciseDateFormatter(locator))
    _style_axis(ax)
    fig.tight_layout()
    _save_figure(fig, output_pdf, output_png)
    plt.close(fig)
    logger.info("Wrote indexed stock vs KOSPI chart: %s, %s", output_pdf, output_png)

    return {
        "figure_id": "fig_indexed_stock_vs_kospi",
        "title": "Indexed Stock Performance vs KOSPI",
        "chart_type": "indexed_time_series",
        "section_recommendation": "Market / Relative Performance",
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

    fig, axes = plt.subplots(2, 1, figsize=(11, 7), gridspec_kw={"height_ratios": [1.3, 1.0]})
    fig.patch.set_facecolor("white")
    returns_ax, relative_ax = axes

    return_specs = [
        ("stock_return_5d_pct", "5D", "#1f5a99", -width),
        ("stock_return_20d_pct", "20D", "#2b8a3e", 0.0),
        ("stock_return_60d_pct", "60D", "#b7791f", width),
    ]
    for column, label, color, offset in return_specs:
        bars = returns_ax.bar(x + offset, chart_df[column], width=width, label=label, color=color)
        _label_bars(returns_ax, bars)
    returns_ax.axhline(0.0, color="#4a5568", linewidth=0.9)
    returns_ax.set_title("Peer Stock Return Comparison", fontsize=12, loc="left")
    returns_ax.set_ylabel("Return (%)")
    returns_ax.set_xticks(x)
    returns_ax.set_xticklabels(chart_df["company_label"])
    returns_ax.legend(loc="upper left", ncol=3, frameon=False)
    _style_axis(returns_ax)

    relative_specs = [
        ("stock_excess_return_20d_pct", "20D Excess vs KOSPI", "#0f766e", -width / 2),
        ("stock_relative_strength_60_pct", "60D Relative Strength", "#c2410c", width / 2),
    ]
    for column, label, color, offset in relative_specs:
        bars = relative_ax.bar(x + offset, chart_df[column], width=width, label=label, color=color)
        _label_bars(relative_ax, bars)
    relative_ax.axhline(0.0, color="#4a5568", linewidth=0.9)
    relative_ax.set_title("Market Relative Performance", fontsize=11, loc="left")
    relative_ax.set_ylabel("Relative Performance (%)")
    relative_ax.set_xticks(x)
    relative_ax.set_xticklabels(chart_df["company_label"])
    relative_ax.legend(loc="upper left", ncol=2, frameon=False)
    _style_axis(relative_ax)

    fig.tight_layout()
    _save_figure(fig, output_pdf, output_png)
    plt.close(fig)
    logger.info("Wrote peer return comparison chart: %s, %s", output_pdf, output_png)

    return {
        "figure_id": "fig_peer_return_comparison",
        "title": "Peer Return and Relative Strength Comparison",
        "chart_type": "peer_group_bar_chart",
        "section_recommendation": "Peer / Market Comparison",
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
            "peer 주가 수익률만으로 펀더멘털 우열을 단정하지 않는다.",
            "상대성과를 목표주가나 추천 의견으로 변환하지 않는다.",
        ],
        "data_limitations": [
            "peer 비교는 현재 확보된 동일 기준일 국내 비교 대상만 포함한다.",
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
    if chart_df[["revenue_100m", "contribution_margin_pct", "sga_margin_pct", "eps"]].isna().all(axis=None):
        raise ValueError("Peer profitability comparison chart has no usable numeric data.")

    chart_df["company_label"] = chart_df["company_name"].map(_safe_company_label)
    x = np.arange(len(chart_df))
    width = 0.34

    fig, axes = plt.subplots(3, 1, figsize=(11.5, 8.2), gridspec_kw={"height_ratios": [1.1, 1.2, 1.0]})
    fig.patch.set_facecolor("white")
    revenue_ax, margin_ax, eps_ax = axes

    revenue_bars = revenue_ax.bar(x, chart_df["revenue_100m"], width=0.46, color="#1f5a99", label="Revenue")
    _label_bars(revenue_ax, revenue_bars)
    revenue_ax.set_title("Domestic Peer Revenue Scale", fontsize=12, loc="left")
    revenue_ax.set_ylabel("KRW 100mn")
    revenue_ax.set_xticks(x)
    revenue_ax.set_xticklabels(chart_df["company_label"])
    revenue_ax.yaxis.set_major_formatter(FuncFormatter(lambda value, _: f"{value:,.0f}"))
    _style_axis(revenue_ax)

    bars = margin_ax.bar(
        x - width / 2,
        chart_df["contribution_margin_pct"],
        width=width,
        label="Contribution Margin",
        color="#2b8a3e",
    )
    _label_bars(margin_ax, bars)
    bars = margin_ax.bar(
        x + width / 2,
        chart_df["sga_margin_pct"],
        width=width,
        label="SG&A Margin",
        color="#b7791f",
    )
    _label_bars(margin_ax, bars)
    margin_ax.set_title("Profitability Structure", fontsize=11, loc="left")
    margin_ax.set_ylabel("Margin (%)")
    margin_ax.set_xticks(x)
    margin_ax.set_xticklabels(chart_df["company_label"])
    margin_ax.legend(loc="upper right", frameon=False)
    _style_axis(margin_ax)

    eps_colors = ["#0f766e" if value >= 0 else "#c2410c" for value in chart_df["eps"].fillna(0)]
    eps_bars = eps_ax.bar(x, chart_df["eps"], width=0.46, color=eps_colors, label="EPS")
    _label_bars(eps_ax, eps_bars)
    eps_ax.axhline(0, color="#a0aec0", linewidth=1.0)
    eps_ax.set_title("EPS Snapshot", fontsize=11, loc="left")
    eps_ax.set_ylabel("KRW")
    eps_ax.set_xticks(x)
    eps_ax.set_xticklabels(chart_df["company_label"])
    eps_ax.yaxis.set_major_formatter(FuncFormatter(lambda value, _: f"{value:,.0f}"))
    _style_axis(eps_ax)

    fig.text(
        0.01,
        0.01,
        "Note: 국내 peer 기준. 결측치는 보간하지 않으며 글로벌 peer, 가치평가 지표, 업종 평균 비교는 포함하지 않음.",
        fontsize=9,
        color="#4a5568",
    )
    fig.tight_layout(rect=(0, 0.04, 1, 1))
    _save_figure(fig, output_pdf, output_png)
    plt.close(fig)
    logger.info("Wrote peer profitability comparison chart: %s, %s", output_pdf, output_png)

    return {
        "figure_id": "fig_peer_profitability_comparison",
        "title": "Domestic Peer Revenue and Profitability Comparison",
        "chart_type": "peer_group_bar_chart",
        "section_recommendation": "Peer / Profitability Comparison",
        "asset_path_pdf": "figures/peer_profitability_comparison.pdf",
        "asset_path_png": "figures/peer_profitability_comparison.png",
        "asset_abs_path_pdf": str(output_pdf),
        "asset_abs_path_png": str(output_png),
        "data_source": "Competitor peer_comparison_dataset.json",
        "used_metrics": ["revenue_100m", "contribution_margin_pct", "sga_margin_pct", "eps"],
        "caption": "국내 peer 기준 매출 규모, 공헌이익률, 판관비율, EPS를 비교한다.",
        "writer_allowed_interpretation": "국내 비교군 안에서 대상 기업의 매출 규모, 수익성 구조, 비용 효율성, EPS 위치를 설명할 수 있다.",
        "writer_forbidden_interpretation": [
            "global peer와의 우열을 언급하지 않는다.",
            "PER/PBR/PSR/EV/Sales 등 valuation 비교로 확장하지 않는다.",
            "업종 평균 대비 할인 또는 프리미엄을 단정하지 않는다.",
            "결측치를 임의로 추정하지 않는다.",
        ],
        "data_limitations": [
            "국내 peer 비교는 현재 확보된 동일 기준일 국내 비교 대상만 포함한다.",
            "일부 peer의 재무 항목은 N/A이며 보간하지 않는다.",
            "YTD 수치가 포함된 경우 연간 확정치와 직접 비교하지 않는다.",
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
    required_columns = ["period_label", "revenue_krw_bn", "contribution_profit_krw_bn", "sga_krw_bn", "basis"]
    _require_columns(income_df, required_columns, "revenue/profit/SG&A trend chart")
    chart_df = income_df.reset_index(drop=True)
    if chart_df[["revenue_krw_bn", "contribution_profit_krw_bn", "sga_krw_bn"]].isna().all(axis=None):
        raise ValueError("Revenue/profit/SG&A trend chart has no usable numeric data.")

    x = np.arange(len(chart_df))
    fig, ax = plt.subplots(figsize=(10.5, 6))
    fig.patch.set_facecolor("white")
    specs = [
        ("revenue_krw_bn", "Revenue", "#1f5a99"),
        ("contribution_profit_krw_bn", "Contribution Profit", "#2b8a3e"),
        ("sga_krw_bn", "SG&A", "#b7791f"),
    ]
    for column, label, color in specs:
        ax.plot(x, chart_df[column], linewidth=2.0, marker="o", label=label, color=color)

    for index, row in chart_df.iterrows():
        if row.get("basis") == "YTD":
            ax.annotate(
                "Q3 YTD\nnot FY",
                xy=(index, row["revenue_krw_bn"]),
                xytext=(8, 18),
                textcoords="offset points",
                arrowprops={"arrowstyle": "->", "color": "#4a5568", "lw": 0.8},
                fontsize=9,
                color="#4a5568",
            )

    title_company = _safe_title_company(company_name)
    ax.set_title(f"{title_company} Revenue, Contribution Profit, and SG&A Trend", fontsize=12, loc="left")
    ax.set_xticks(x)
    ax.set_xticklabels(chart_df["period_label"].tolist())
    ax.set_ylabel("KRW bn")
    ax.yaxis.set_major_formatter(FuncFormatter(lambda value, _: f"{value:,.0f}"))
    ax.legend(loc="upper left", ncol=3, frameon=False)
    _style_axis(ax)
    fig.text(0.01, 0.01, _ytd_note(chart_df), fontsize=9, color="#4a5568")
    fig.tight_layout(rect=(0, 0.04, 1, 1))
    _save_figure(fig, output_pdf, output_png)
    plt.close(fig)
    logger.info("Wrote revenue/profit/SG&A trend chart: %s, %s", output_pdf, output_png)

    return {
        "figure_id": "fig_revenue_profit_sga_trend",
        "title": "Revenue, Contribution Profit, and SG&A Trend",
        "chart_type": "line_time_series",
        "section_recommendation": "Financial Analysis",
        "asset_path_pdf": "figures/revenue_profit_sga_trend.pdf",
        "asset_path_png": "figures/revenue_profit_sga_trend.png",
        "asset_abs_path_pdf": str(output_pdf),
        "asset_abs_path_png": str(output_png),
        "data_source": "dart_main.json",
        "used_metrics": ["revenue", "contribution_profit", "sga"],
        "derived_columns": ["revenue_krw_bn", "contribution_profit_krw_bn", "sga_krw_bn"],
        "caption": "DART 기준 매출, 공헌이익, 판관비의 금액 추이를 함께 보여준다. YTD 수치가 포함된 경우 연간 확정치가 아니다.",
        "writer_allowed_interpretation": "매출 규모, 공헌이익, 판관비 부담의 방향성을 설명할 수 있다.",
        "writer_forbidden_interpretation": [
            "YTD 수치와 FY 수치를 동일 기준 YoY 성장으로 단정하지 않는다.",
            "공헌이익을 영업이익 또는 순이익으로 표현하지 않는다.",
        ],
        "data_limitations": [
            "YTD 수치가 포함된 경우 연간 확정치가 아니다.",
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

    fig, axes = plt.subplots(2, 1, figsize=(11, 7), gridspec_kw={"height_ratios": [1.0, 1.0]})
    fig.patch.set_facecolor("white")
    liquidity_ax, leverage_ax = axes

    bars = liquidity_ax.bar(x - width / 2, chart_df["current_ratio_pct"], width=width, label="Current Ratio", color="#1f5a99")
    _label_bars(liquidity_ax, bars)
    bars = liquidity_ax.bar(x + width / 2, chart_df["cash_ratio_pct"], width=width, label="Cash Ratio", color="#2b8a3e")
    _label_bars(liquidity_ax, bars)
    liquidity_ax.set_title("Peer Liquidity Comparison", fontsize=12, loc="left")
    liquidity_ax.set_ylabel("Ratio (%)")
    liquidity_ax.set_xticks(x)
    liquidity_ax.set_xticklabels(chart_df["company_label"])
    liquidity_ax.legend(loc="upper right", frameon=False)
    _style_axis(liquidity_ax)

    bars = leverage_ax.bar(x - width / 2, chart_df["equity_ratio_pct"], width=width, label="Equity Ratio", color="#0f766e")
    _label_bars(leverage_ax, bars)
    bars = leverage_ax.bar(x + width / 2, chart_df["debt_to_equity_pct"], width=width, label="Debt/Equity", color="#c2410c")
    _label_bars(leverage_ax, bars)
    leverage_ax.set_title("Capital Structure / Leverage", fontsize=11, loc="left")
    leverage_ax.set_ylabel("Ratio (%)")
    leverage_ax.set_xticks(x)
    leverage_ax.set_xticklabels(chart_df["company_label"])
    leverage_ax.legend(loc="upper right", frameon=False)
    _style_axis(leverage_ax)

    fig.tight_layout()
    _save_figure(fig, output_pdf, output_png)
    plt.close(fig)
    logger.info("Wrote liquidity/leverage peer comparison chart: %s, %s", output_pdf, output_png)

    return {
        "figure_id": "fig_liquidity_leverage_peer_comparison",
        "title": "Liquidity and Leverage Peer Comparison",
        "chart_type": "peer_group_bar_chart",
        "section_recommendation": "Peer / Financial Stability",
        "asset_path_pdf": "figures/liquidity_leverage_peer_comparison.pdf",
        "asset_path_png": "figures/liquidity_leverage_peer_comparison.png",
        "asset_abs_path_pdf": str(output_pdf),
        "asset_abs_path_png": str(output_png),
        "data_source": "Financial final_report.json files",
        "used_metrics": ["current_ratio", "cash_ratio", "equity_ratio", "debt_to_equity"],
        "derived_columns": ["current_ratio_pct", "cash_ratio_pct", "equity_ratio_pct", "debt_to_equity_pct"],
        "caption": "비교 기업의 유동비율, 현금비율, 자본비율, 부채비율을 비교해 재무 안정성 차이를 보여준다.",
        "writer_allowed_interpretation": "동일 기준 산출물 내에서 peer 간 유동성 및 레버리지 부담의 상대적 차이를 설명할 수 있다.",
        "writer_forbidden_interpretation": [
            "유동성 지표만으로 투자 의견을 산출하지 않는다.",
            "부채비율이 낮다는 이유만으로 성장성 또는 수익성을 단정하지 않는다.",
        ],
        "data_limitations": [
            "재무 안정성 비교는 구조화된 재무 지표 기준으로 제한한다.",
        ],
    }


def build_investment_thesis_evidence_map_chart(
    evidence_df: pd.DataFrame,
    output_pdf: str | Path,
    output_png: str | Path,
    recommendation: str = "Investment Decision",
) -> dict:
    """Create an evidence map from decision basis items."""

    output_pdf, output_png = _prepare_output_paths(output_pdf, output_png)
    recommendation_label = str(recommendation or "Investment Decision").strip()
    required_columns = ["signal_type", "category"]
    _require_columns(evidence_df, required_columns, "investment thesis evidence map")
    counts = evidence_df.groupby(["signal_type", "category"]).size().reset_index(name="count")
    if counts.empty:
        raise ValueError("Investment thesis evidence map has no countable evidence items.")

    signal_order = ["Positive Basis", "Risk", "Mixed Signal", "Monitoring"]
    categories = sorted(counts["category"].unique().tolist())
    x_lookup = {category: index for index, category in enumerate(categories)}
    y_lookup = {signal: index for index, signal in enumerate(signal_order)}
    counts["x"] = counts["category"].map(x_lookup)
    counts["y"] = counts["signal_type"].map(y_lookup)
    colors = {
        "Positive Basis": "#2b8a3e",
        "Risk": "#c2410c",
        "Mixed Signal": "#b7791f",
        "Monitoring": "#1f5a99",
    }

    fig, ax = plt.subplots(figsize=(11.5, 6.2))
    fig.patch.set_facecolor("white")
    for signal_type in signal_order:
        subset = counts[counts["signal_type"] == signal_type]
        if subset.empty:
            continue
        ax.scatter(
            subset["x"],
            subset["y"],
            s=260 + subset["count"] * 180,
            color=colors[signal_type],
            alpha=0.82,
            label=signal_type,
            edgecolor="white",
            linewidth=1.0,
        )
        for _, row in subset.iterrows():
            ax.text(row["x"], row["y"], str(int(row["count"])), ha="center", va="center", color="white", fontsize=10, weight="bold")

    ax.set_title(f"{recommendation_label} Decision Evidence Map", fontsize=12, loc="left")
    ax.set_xticks(range(len(categories)))
    ax.set_xticklabels([_safe_category_label(category) for category in categories], rotation=25, ha="right")
    ax.set_yticks(range(len(signal_order)))
    ax.set_yticklabels(signal_order)
    ax.set_xlim(-0.6, len(categories) - 0.4)
    ax.set_ylim(-0.6, len(signal_order) - 0.4)
    ax.grid(True, color="#e2e8f0", linewidth=0.8)
    ax.legend(loc="upper right", frameon=False)
    for spine in ax.spines.values():
        spine.set_color("#cbd5e0")
    ax.tick_params(axis="both", colors="#2d3748", labelsize=9)
    fig.text(
        0.01,
        0.01,
        "Bubble size and number indicate count of decision-basis items by category.",
        fontsize=9,
        color="#4a5568",
    )
    fig.tight_layout(rect=(0, 0.04, 1, 1))
    _save_figure(fig, output_pdf, output_png)
    plt.close(fig)
    logger.info("Wrote investment thesis evidence map chart: %s, %s", output_pdf, output_png)

    return {
        "figure_id": "fig_investment_thesis_evidence_map",
        "title": "Investment Thesis Evidence Map",
        "chart_type": "strategy_evidence_bubble_map",
        "section_recommendation": "Investment Thesis / Decision Rationale",
        "asset_path_pdf": "figures/investment_thesis_evidence_map.pdf",
        "asset_path_png": "figures/investment_thesis_evidence_map.png",
        "asset_abs_path_pdf": str(output_pdf),
        "asset_abs_path_png": str(output_png),
        "data_source": "decision_basis_card.json",
        "used_fields": ["basis_items", "risk_items", "mixed_or_conflicting_signals", "monitoring_points"],
        "derived_columns": ["signal_type", "category", "count"],
        "caption": f"{recommendation_label} 판단 근거를 긍정 근거, 리스크, 혼재 신호, 모니터링 항목으로 요약한다.",
        "writer_allowed_interpretation": f"최종 {recommendation_label} 판단이 어떤 근거와 리스크의 균형에서 나온 것인지 구조적으로 설명할 수 있다.",
        "writer_forbidden_interpretation": [
            "항목 개수를 정량 점수나 투자 등급 산식으로 해석하지 않는다.",
            "차트 항목 수만으로 새로운 투자 의견을 생성하지 않는다.",
        ],
        "data_limitations": [
            "이 차트는 투자 판단 근거 항목 수를 요약한 것이며 독립적인 정량 모델이 아니다.",
        ],
    }


def _require_columns(df: pd.DataFrame, columns: list[str], context: str) -> None:
    missing = [column for column in columns if column not in df.columns]
    if missing:
        raise ValueError(f"{context} missing required columns: {missing}")


def _ytd_note(chart_df: pd.DataFrame) -> str:
    if "basis" not in chart_df.columns:
        return "Note: YTD values, if present, are not directly comparable with full-year values."
    ytd_df = chart_df[chart_df["basis"] == "YTD"]
    if ytd_df.empty:
        return "Note: Values use the period basis provided by DART."
    labels = []
    if "period_label" in ytd_df.columns:
        labels = [str(label) for label in ytd_df["period_label"].dropna().unique().tolist()]
    period_text = ", ".join(labels) if labels else "YTD"
    return f"Note: {period_text} values are YTD and are not directly comparable with full-year values."


def _prepare_output_paths(output_pdf: str | Path, output_png: str | Path) -> tuple[Path, Path]:
    output_pdf = Path(output_pdf).expanduser().resolve()
    output_png = Path(output_png).expanduser().resolve()
    output_pdf.parent.mkdir(parents=True, exist_ok=True)
    output_png.parent.mkdir(parents=True, exist_ok=True)
    return output_pdf, output_png


def _label_bars(ax, bars) -> None:
    for bar in bars:
        height = bar.get_height()
        if pd.isna(height):
            continue
        va = "bottom" if height >= 0 else "top"
        offset = 3 if height >= 0 else -3
        ax.annotate(
            f"{height:.1f}",
            xy=(bar.get_x() + bar.get_width() / 2, height),
            xytext=(0, offset),
            textcoords="offset points",
            ha="center",
            va=va,
            fontsize=8,
            color="#2d3748",
        )


def _style_axis(ax) -> None:
    ax.grid(True, axis="y", color="#e2e8f0", linewidth=0.8)
    ax.spines["top"].set_visible(False)
    ax.spines["right"].set_visible(False)
    ax.spines["left"].set_color("#cbd5e0")
    ax.spines["bottom"].set_color("#cbd5e0")
    ax.tick_params(axis="both", colors="#2d3748", labelsize=9)


def _save_figure(fig, output_pdf: Path, output_png: Path) -> None:
    fig.savefig(output_pdf, bbox_inches="tight")
    fig.savefig(output_png, dpi=220, bbox_inches="tight")


def _safe_title_company(company_name: str) -> str:
    label = str(company_name or "").strip()
    return label or "Target Company"


def _safe_company_label(company_name: str) -> str:
    label = str(company_name or "").strip()
    return label or "Peer"


def _safe_category_label(category: str) -> str:
    labels = {
        "financial": "Financial",
        "business_catalyst": "Business Catalyst",
        "peer_positioning": "Peer Positioning",
        "market_price": "Market Price",
        "summary_strengths": "Summary Strengths",
        "regulatory": "Regulatory",
        "market": "Market Risk",
        "execution": "Execution",
        "cross_agent_consistency": "Cross Check",
        "strategy_implication": "Thesis",
        "monitoring": "Monitoring",
    }
    return labels.get(category, category.replace("_", " ").title())
