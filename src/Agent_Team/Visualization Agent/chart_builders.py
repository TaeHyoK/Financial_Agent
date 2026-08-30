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

    fig, axes = plt.subplots(
        nrows=2,
        ncols=1,
        figsize=(12, 6),
        sharex=True,
        gridspec_kw={"height_ratios": [2.5, 1.0]},
    )
    fig.patch.set_facecolor("white")

    title_company = _safe_title_company(company_name)
    price_ax, volume_ax = axes
    price_ax.plot(chart_df["date"], chart_df["stock_close"], color="#1f5a99", linewidth=2.0, label="종가")
    price_ax.plot(chart_df["date"], chart_df["derived_ma20"], color="#2b8a3e", linewidth=1.5, label="20일 이동평균")
    price_ax.plot(chart_df["date"], chart_df["derived_ma60"], color="#b7791f", linewidth=1.5, label="60일 이동평균")
    latest = chart_df.iloc[-1]
    price_ax.annotate(
        f"{latest['stock_close']:,.0f}원",
        xy=(latest["date"], latest["stock_close"]),
        xytext=(-85, 18),
        textcoords="offset points",
        arrowprops={"arrowstyle": "->", "color": "#4a5568", "lw": 0.8},
        fontsize=9,
        color="#1a202c",
    )
    price_ax.set_title(f"{title_company} 주가와 이동평균", fontsize=12, loc="left")
    price_ax.set_ylabel("주가(원)")
    price_ax.yaxis.set_major_formatter(FuncFormatter(lambda value, _: f"{value:,.0f}"))
    price_ax.legend(loc="upper left", ncol=3, frameon=False)
    _style_axis(price_ax)

    volume_ax.plot(chart_df["date"], chart_df["stock_volume_ratio_20"], color="#5f6368", linewidth=1.5, label="20일 평균 대비 거래량")
    volume_ax.axhline(1.0, color="#a0aec0", linewidth=1.0, linestyle="--")
    volume_ax.set_title("20일 평균 대비 거래량", fontsize=11, loc="left")
    volume_ax.set_ylabel("거래량 배수")
    _style_axis(volume_ax)

    locator = mdates.AutoDateLocator(minticks=6, maxticks=10)
    volume_ax.xaxis.set_major_locator(locator)
    volume_ax.xaxis.set_major_formatter(mdates.DateFormatter("%m.%d"))
    volume_ax.set_xlabel("거래일")

    fig.tight_layout()
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

    fig, ax = plt.subplots(figsize=(10, 6))
    fig.patch.set_facecolor("white")
    x_positions = np.arange(len(chart_df))
    width = 0.34
    contribution_bars = ax.bar(
        x_positions - width / 2,
        chart_df["contribution_margin_pct"],
        width,
        color="#1f5a99",
        label="공헌이익률",
    )
    sga_bars = ax.bar(
        x_positions + width / 2,
        chart_df["sga_margin_pct"],
        width,
        color="#b7791f",
        label="판매관리비율",
    )
    _label_bars(ax, contribution_bars, suffix="%")
    _label_bars(ax, sga_bars, suffix="%")

    title_company = _safe_title_company(company_name)
    ax.set_title(f"{title_company} 동일 기간 수익성 비교", fontsize=12, loc="left")
    ax.set_xticks(x_positions)
    ax.set_xticklabels(chart_df["period_label"].tolist())
    ax.set_ylabel("비율(%)")
    ax.yaxis.set_major_formatter(FuncFormatter(lambda value, _: f"{value:.0f}%"))
    ax.legend(loc="best", frameon=False)
    _style_axis(ax)
    fig.text(0.01, 0.01, _period_comparison_note(chart_df), fontsize=9, color="#4a5568")
    fig.tight_layout(rect=(0, 0.04, 1, 1))
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
    ax.set_title(f"{title_company}와 KOSPI 지수화 성과", fontsize=12, loc="left")
    ax.set_ylabel("지수(시작일=100)")
    ax.legend(loc="upper left", frameon=False)
    locator = mdates.AutoDateLocator(minticks=6, maxticks=10)
    ax.xaxis.set_major_locator(locator)
    ax.xaxis.set_major_formatter(mdates.DateFormatter("%m.%d"))
    _style_axis(ax)
    fig.tight_layout()
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

    fig, axes = plt.subplots(2, 1, figsize=(11, 7), gridspec_kw={"height_ratios": [1.3, 1.0]})
    fig.patch.set_facecolor("white")
    returns_ax, relative_ax = axes

    return_specs = [
        ("stock_return_5d_pct", "5일", "#1f5a99", -width),
        ("stock_return_20d_pct", "20일", "#2b8a3e", 0.0),
        ("stock_return_60d_pct", "60일", "#b7791f", width),
    ]
    for column, label, color, offset in return_specs:
        bars = returns_ax.bar(x + offset, chart_df[column], width=width, label=label, color=color)
        _label_bars(returns_ax, bars)
    returns_ax.axhline(0.0, color="#4a5568", linewidth=0.9)
    returns_ax.set_title("대상기업과 비교기업 주가수익률", fontsize=12, loc="left")
    returns_ax.set_ylabel("수익률(%)")
    returns_ax.set_xticks(x)
    returns_ax.set_xticklabels(chart_df["company_label"])
    returns_ax.legend(loc="upper left", ncol=3, frameon=False)
    _style_axis(returns_ax)

    relative_specs = [
        ("stock_excess_return_20d_pct", "20일 KOSPI 초과수익률", "#0f766e", -width / 2),
        ("stock_relative_strength_60_pct", "60일 상대강도", "#c2410c", width / 2),
    ]
    for column, label, color, offset in relative_specs:
        bars = relative_ax.bar(x + offset, chart_df[column], width=width, label=label, color=color)
        _label_bars(relative_ax, bars)
    relative_ax.axhline(0.0, color="#4a5568", linewidth=0.9)
    relative_ax.set_title("시장 대비 상대성과", fontsize=11, loc="left")
    relative_ax.set_ylabel("상대성과(%)")
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
    if chart_df[["revenue_100m", "contribution_margin_pct", "sga_margin_pct", "eps"]].isna().all(axis=None):
        raise ValueError("Peer profitability comparison chart has no usable numeric data.")

    chart_df["company_label"] = chart_df["company_name"].map(_safe_company_label)
    x = np.arange(len(chart_df))
    width = 0.34

    fig, axes = plt.subplots(3, 1, figsize=(11.5, 8.2), gridspec_kw={"height_ratios": [1.1, 1.2, 1.0]})
    fig.patch.set_facecolor("white")
    revenue_ax, margin_ax, eps_ax = axes

    revenue_bars = revenue_ax.bar(x, chart_df["revenue_100m"], width=0.46, color="#1f5a99", label="매출")
    _label_bars(revenue_ax, revenue_bars)
    revenue_ax.set_title("대상기업과 비교기업 매출 규모", fontsize=12, loc="left")
    revenue_ax.set_ylabel("억원")
    revenue_ax.set_xticks(x)
    revenue_ax.set_xticklabels(chart_df["company_label"])
    revenue_ax.yaxis.set_major_formatter(FuncFormatter(lambda value, _: f"{value:,.0f}"))
    _style_axis(revenue_ax)

    bars = margin_ax.bar(
        x - width / 2,
        chart_df["contribution_margin_pct"],
        width=width,
        label="공헌이익률",
        color="#2b8a3e",
    )
    _label_bars(margin_ax, bars)
    bars = margin_ax.bar(
        x + width / 2,
        chart_df["sga_margin_pct"],
        width=width,
        label="판매관리비율",
        color="#b7791f",
    )
    _label_bars(margin_ax, bars)
    margin_ax.set_title("수익성 구조", fontsize=11, loc="left")
    margin_ax.set_ylabel("이익률(%)")
    margin_ax.set_xticks(x)
    margin_ax.set_xticklabels(chart_df["company_label"])
    margin_ax.legend(loc="upper right", frameon=False)
    _style_axis(margin_ax)

    eps_colors = ["#0f766e" if value >= 0 else "#c2410c" for value in chart_df["eps"].fillna(0)]
    eps_bars = eps_ax.bar(x, chart_df["eps"], width=0.46, color=eps_colors, label="주당순이익")
    _label_bars(eps_ax, eps_bars)
    eps_ax.axhline(0, color="#a0aec0", linewidth=1.0)
    eps_ax.set_title("주당순이익", fontsize=11, loc="left")
    eps_ax.set_ylabel("원")
    eps_ax.set_xticks(x)
    eps_ax.set_xticklabels(chart_df["company_label"])
    eps_ax.yaxis.set_major_formatter(FuncFormatter(lambda value, _: f"{value:,.0f}"))
    _style_axis(eps_ax)

    fig.text(
        0.01,
        0.01,
        "주: 국내 비교기업 기준이며, 결측치는 보간하지 않았다. 해외 비교기업, 가치평가 지표, 업종 평균은 포함하지 않았다.",
        fontsize=9,
        color="#4a5568",
    )
    fig.tight_layout(rect=(0, 0.04, 1, 1))
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
    fig, ax = plt.subplots(figsize=(10.5, 6))
    fig.patch.set_facecolor("white")
    specs = [
        ("revenue_krw_bn", "매출", "#1f5a99"),
        ("contribution_profit_krw_bn", "공헌이익", "#2b8a3e"),
        ("sga_krw_bn", "판매관리비", "#b7791f"),
    ]
    for column, label, color in specs:
        ax.plot(x, chart_df[column], linewidth=2.0, marker="o", label=label, color=color)

    title_company = _safe_title_company(company_name)
    ax.set_title(f"{title_company} 매출·공헌이익·판매관리비 추이", fontsize=12, loc="left")
    ax.set_xticks(x)
    ax.set_xticklabels(chart_df["period_label"].tolist())
    ax.set_ylabel("십억원")
    ax.yaxis.set_major_formatter(FuncFormatter(lambda value, _: f"{value:,.0f}"))
    ax.legend(loc="upper left", ncol=3, frameon=False)
    _style_axis(ax)
    fig.text(0.01, 0.01, _period_comparison_note(chart_df), fontsize=9, color="#4a5568")
    fig.tight_layout(rect=(0, 0.04, 1, 1))
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

    fig, axes = plt.subplots(2, 1, figsize=(11, 7), gridspec_kw={"height_ratios": [1.0, 1.0]})
    fig.patch.set_facecolor("white")
    liquidity_ax, leverage_ax = axes

    bars = liquidity_ax.bar(x - width / 2, chart_df["current_ratio_pct"], width=width, label="유동비율", color="#1f5a99")
    _label_bars(liquidity_ax, bars)
    bars = liquidity_ax.bar(x + width / 2, chart_df["cash_ratio_pct"], width=width, label="현금비율", color="#2b8a3e")
    _label_bars(liquidity_ax, bars)
    liquidity_ax.set_title("대상기업과 비교기업 유동성", fontsize=12, loc="left")
    liquidity_ax.set_ylabel("비율(%)")
    liquidity_ax.set_xticks(x)
    liquidity_ax.set_xticklabels(chart_df["company_label"])
    liquidity_ax.legend(loc="upper center", ncol=2, frameon=False)
    _style_axis(liquidity_ax)

    bars = leverage_ax.bar(x - width / 2, chart_df["equity_ratio_pct"], width=width, label="자기자본비율", color="#0f766e")
    _label_bars(leverage_ax, bars)
    bars = leverage_ax.bar(x + width / 2, chart_df["debt_to_equity_pct"], width=width, label="부채비율", color="#c2410c")
    _label_bars(leverage_ax, bars)
    leverage_ax.set_title("자본구조와 레버리지", fontsize=11, loc="left")
    leverage_ax.set_ylabel("비율(%)")
    leverage_ax.set_xticks(x)
    leverage_ax.set_xticklabels(chart_df["company_label"])
    leverage_ax.legend(loc="upper center", ncol=2, frameon=False)
    _style_axis(leverage_ax)

    fig.tight_layout()
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
        offset = 3 if height >= 0 else -3
        ax.annotate(
            f"{height:.1f}{suffix}",
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
