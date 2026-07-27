"""Standalone same-period financial-performance chart generator."""

from __future__ import annotations

import argparse
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import matplotlib

matplotlib.use("Agg")

import matplotlib.pyplot as plt
from matplotlib import font_manager
from matplotlib.ticker import FuncFormatter
import numpy as np
import pandas as pd


CURRENT_COLOR = "#2563EB"
PREVIOUS_COLOR = "#D1D5DB"
TEXT_COLOR = "#111827"
GRID_COLOR = "#E5E7EB"
DEFAULT_OUTPUT_NAME = "same_period_financial_performance.png"
METRICS = (
    ("revenue", "매출"),
    ("operating_profit", "영업이익"),
    ("net_income", "순이익"),
    ("operating_cash_flow", "영업현금흐름"),
)


@dataclass(frozen=True)
class ChartResult:
    chart_path: Path
    data_path: Path
    metadata_path: Path
    current_label: str
    previous_label: str


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Create a two-panel same-period financial performance chart from Financial final_report.json."
    )
    parser.add_argument("--financial-report", required=True, type=Path)
    parser.add_argument("--company-name", default="", help="Overrides the company name in the report.")
    parser.add_argument("--output-dir", required=True, type=Path)
    parser.add_argument("--output-name", default=DEFAULT_OUTPUT_NAME)
    return parser


def build_same_period_financial_chart(
    *,
    financial_report: Path,
    output_dir: Path,
    company_name: str = "",
    output_name: str = DEFAULT_OUTPUT_NAME,
) -> ChartResult:
    """Read the verified Financial report and persist a same-basis comparison chart."""

    source_path = financial_report.expanduser().resolve()
    report = _load_json(source_path)
    company = str(company_name or report.get("target_company") or "대상기업").strip()
    comparison = extract_same_period_comparison(report)

    output_dir = output_dir.expanduser().resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    chart_path = output_dir / _safe_png_name(output_name)
    data_path = output_dir / "same_period_financial_performance_data.csv"
    metadata_path = output_dir / "same_period_financial_performance_metadata.json"

    render_same_period_chart(
        comparison,
        company_name=company,
        output_path=chart_path,
    )
    comparison["data"].to_csv(data_path, index=False, encoding="utf-8")

    metadata = {
        "chart_type": "same_period_financial_performance",
        "company_name": company,
        "source_report": str(source_path),
        "selected_date": comparison["selected_date"],
        "selected_date_policy": (report.get("collection_context") or {}).get("selected_date_policy"),
        "statement_scope": comparison["statement_scope"],
        "comparison_basis": comparison["basis"],
        "period_type": comparison["period_type"],
        "current_period": comparison["current_period"],
        "previous_period": comparison["previous_period"],
        "metrics": [key for key, _label in METRICS],
        "unit": "100m_KRW",
        "colors": {"current": CURRENT_COLOR, "previous": PREVIOUS_COLOR},
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
        current_label=comparison["current_label"],
        previous_label=comparison["previous_label"],
    )


def extract_same_period_comparison(report: dict[str, Any]) -> dict[str, Any]:
    """Validate equal period bases and return chart-ready values in KRW 100m."""

    trends = _dict(report.get("financial_trends"))
    same_period = _dict(trends.get("current_vs_same_period"))
    current_period = _dict(same_period.get("current_period"))
    previous_period = _dict(same_period.get("previous_period"))
    current_values = _dict(same_period.get("current_values"))
    previous_values = _dict(same_period.get("previous_values"))
    if not current_period or not previous_period:
        raise ValueError("Financial report has no current_vs_same_period period metadata.")

    current_type = str(current_period.get("period_type") or "")
    previous_type = str(previous_period.get("period_type") or "")
    current_basis = str(current_period.get("basis") or "")
    previous_basis = str(previous_period.get("basis") or "")
    if not current_type or current_type != previous_type:
        raise ValueError(
            f"Same-period chart requires matching period_type: {current_type!r} != {previous_type!r}."
        )
    if not current_basis or current_basis != previous_basis:
        raise ValueError(
            f"Same-period chart requires matching basis: {current_basis!r} != {previous_basis!r}."
        )

    current_year = _required_int(current_period.get("fiscal_year"), "current fiscal_year")
    previous_year = _required_int(previous_period.get("fiscal_year"), "previous fiscal_year")
    current_label = _period_label(current_year, current_type, current_basis)
    previous_label = _period_label(previous_year, previous_type, previous_basis)
    rows: list[dict[str, Any]] = []
    for metric, label in METRICS:
        current_krw = _required_number(current_values.get(metric), f"current_values.{metric}")
        previous_krw = _required_number(previous_values.get(metric), f"previous_values.{metric}")
        growth_pct = None
        if previous_krw != 0:
            growth_pct = (current_krw / previous_krw - 1.0) * 100.0
        rows.append(
            {
                "metric": metric,
                "label": label,
                "previous_period": previous_label,
                "previous_value_100m_krw": previous_krw / 100_000_000.0,
                "current_period": current_label,
                "current_value_100m_krw": current_krw / 100_000_000.0,
                "yoy_change_pct": growth_pct,
            }
        )
    collection = _dict(report.get("collection_context"))
    return {
        "data": pd.DataFrame(rows),
        "selected_date": str(collection.get("selected_date") or report.get("as_of_date") or ""),
        "statement_scope": str(collection.get("statement_scope") or "unknown"),
        "period_type": current_type,
        "basis": current_basis,
        "current_label": current_label,
        "previous_label": previous_label,
        "current_period": current_period,
        "previous_period": previous_period,
    }


def render_same_period_chart(
    comparison: dict[str, Any],
    *,
    company_name: str,
    output_path: Path,
) -> None:
    """Render revenue separately from profit/cash flow so scale remains readable."""

    _configure_korean_font()
    data = comparison["data"].set_index("metric")
    current_label = comparison["current_label"]
    previous_label = comparison["previous_label"]

    fig, axes = plt.subplots(
        nrows=2,
        ncols=1,
        figsize=(13, 8.4),
        gridspec_kw={"height_ratios": [1.0, 1.45]},
    )
    fig.patch.set_facecolor("white")
    fig.suptitle(
        f"{company_name} 동일기간 재무성과 비교",
        x=0.075,
        y=0.98,
        ha="left",
        fontsize=17,
        fontweight="bold",
        color=TEXT_COLOR,
    )

    _draw_grouped_bars(
        axes[0],
        data.loc[["revenue"]],
        current_label=current_label,
        previous_label=previous_label,
    )
    axes[0].set_title("매출", loc="left", fontsize=12, fontweight="bold", color=TEXT_COLOR)
    axes[0].set_xticklabels([""])

    _draw_grouped_bars(
        axes[1],
        data.loc[["operating_profit", "net_income", "operating_cash_flow"]],
        current_label=current_label,
        previous_label=previous_label,
    )
    axes[1].set_title("이익 및 현금흐름", loc="left", fontsize=12, fontweight="bold", color=TEXT_COLOR)
    axes[1].legend(
        loc="upper left",
        bbox_to_anchor=(0.0, -0.14),
        ncol=2,
        frameon=False,
        fontsize=10.5,
    )

    fig.tight_layout(rect=(0.04, 0.035, 1, 0.95), h_pad=2.5)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(output_path, dpi=220, bbox_inches="tight", facecolor="white")
    plt.close(fig)


def _draw_grouped_bars(
    ax: plt.Axes,
    rows: pd.DataFrame,
    *,
    current_label: str,
    previous_label: str,
) -> None:
    x = np.arange(len(rows), dtype=float)
    width = 0.30
    previous = rows["previous_value_100m_krw"].astype(float).to_numpy()
    current = rows["current_value_100m_krw"].astype(float).to_numpy()
    previous_bars = ax.bar(
        x - width / 2,
        previous,
        width,
        color=PREVIOUS_COLOR,
        label=previous_label,
        zorder=2,
    )
    current_bars = ax.bar(
        x + width / 2,
        current,
        width,
        color=CURRENT_COLOR,
        label=current_label,
        zorder=3,
    )

    values = np.concatenate([previous, current])
    min_value = min(0.0, float(np.nanmin(values)))
    max_value = max(0.0, float(np.nanmax(values)))
    span = max(max_value - min_value, 1.0)
    ax.set_ylim(min_value - span * 0.08, max_value + span * 0.24)
    ax.axhline(0.0, color="#9CA3AF", linewidth=0.8)
    ax.set_xticks(x)
    ax.set_xticklabels(rows["label"].tolist(), fontsize=11)
    ax.set_ylabel("억원", fontsize=10.5)
    ax.yaxis.set_major_formatter(FuncFormatter(lambda value, _position: f"{value:,.0f}"))
    ax.grid(axis="y", color=GRID_COLOR, linewidth=0.8, zorder=0)
    ax.grid(axis="x", visible=False)
    ax.spines["top"].set_visible(False)
    ax.spines["right"].set_visible(False)
    ax.spines["left"].set_color("#D1D5DB")
    ax.spines["bottom"].set_color("#D1D5DB")
    ax.tick_params(colors="#4B5563")

    label_offset = span * 0.022
    _label_values(ax, previous_bars, previous, label_offset=label_offset, color="#4B5563")
    _label_values(ax, current_bars, current, label_offset=label_offset, color=CURRENT_COLOR)
    for bar, growth in zip(current_bars, rows["yoy_change_pct"].tolist()):
        if growth is None or pd.isna(growth):
            continue
        value = float(bar.get_height())
        offset = span * 0.09 if value >= 0 else -span * 0.09
        ax.text(
            bar.get_x() + bar.get_width() / 2,
            value + offset,
            f"YoY {float(growth):+.1f}%",
            ha="center",
            va="bottom" if value >= 0 else "top",
            fontsize=9.2,
            fontweight="bold",
            color=CURRENT_COLOR,
        )


def _label_values(
    ax: plt.Axes,
    bars: Any,
    values: np.ndarray,
    *,
    label_offset: float,
    color: str,
) -> None:
    for bar, value in zip(bars, values):
        offset = label_offset if value >= 0 else -label_offset
        ax.text(
            bar.get_x() + bar.get_width() / 2,
            value + offset,
            f"{value:,.0f}",
            ha="center",
            va="bottom" if value >= 0 else "top",
            fontsize=9.5,
            color=color,
        )


def _period_label(fiscal_year: int, period_type: str, basis: str) -> str:
    period_names = {
        "Q1": "1분기",
        "HALF": "반기",
        "Q3": "3분기",
        "ANNUAL": "연간",
    }
    name = period_names.get(period_type, period_type)
    suffix = " 누적" if basis == "YTD" and period_type != "ANNUAL" else ""
    return f"{fiscal_year}년 {name}{suffix}"


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


def _load_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        raise FileNotFoundError(f"Financial report not found: {path}")
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"Financial report must be a JSON object: {path}")
    return payload


def _dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _required_number(value: Any, label: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{label} must be numeric.")
    return float(value)


def _required_int(value: Any, label: str) -> int:
    number = _required_number(value, label)
    if not number.is_integer():
        raise ValueError(f"{label} must be an integer.")
    return int(number)


def _safe_png_name(value: str) -> str:
    name = Path(value).name
    if not name.lower().endswith(".png"):
        name += ".png"
    return name


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    result = build_same_period_financial_chart(
        financial_report=args.financial_report,
        output_dir=args.output_dir,
        company_name=args.company_name,
        output_name=args.output_name,
    )
    print(
        json.dumps(
            {
                "chart_path": str(result.chart_path),
                "data_path": str(result.data_path),
                "metadata_path": str(result.metadata_path),
                "comparison": [result.previous_label, result.current_label],
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
