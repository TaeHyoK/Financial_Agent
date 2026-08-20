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
from matplotlib.patches import FancyBboxPatch
import pandas as pd


CURRENT_COLOR = "#2563EB"
PREVIOUS_COLOR = "#CBD5E1"
TEXT_COLOR = "#0F172A"
MUTED_TEXT_COLOR = "#64748B"
BACKGROUND_COLOR = "#F5F7FB"
CARD_BORDER_COLOR = "#E2E8F0"
TRACK_COLOR = "#EEF2F7"
POSITIVE_COLOR = "#0F766E"
POSITIVE_BACKGROUND = "#CCFBF1"
NEGATIVE_COLOR = "#B42318"
NEGATIVE_BACKGROUND = "#FEE4E2"
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
    """Render a compact editorial dashboard with one independent card per metric."""

    _configure_korean_font()
    data = comparison["data"].set_index("metric")
    current_label = comparison["current_label"]
    previous_label = comparison["previous_label"]

    fig = plt.figure(figsize=(13, 7.4), facecolor=BACKGROUND_COLOR)
    fig.text(
        0.055,
        0.925,
        f"{company_name} 동일기간 재무성과 비교",
        ha="left",
        va="center",
        fontsize=21,
        fontweight="bold",
        color=TEXT_COLOR,
    )
    fig.text(
        0.055,
        0.875,
        f"{previous_label} 대비 {current_label}  ·  단위: 억원",
        ha="left",
        va="center",
        fontsize=10.5,
        color=MUTED_TEXT_COLOR,
    )
    fig.text(0.745, 0.875, "●", color=PREVIOUS_COLOR, fontsize=10.5, va="center")
    fig.text(0.762, 0.875, previous_label, color=MUTED_TEXT_COLOR, fontsize=9.5, va="center")
    fig.text(0.865, 0.875, "●", color=CURRENT_COLOR, fontsize=10.5, va="center")
    fig.text(0.882, 0.875, current_label, color=MUTED_TEXT_COLOR, fontsize=9.5, va="center")

    grid = fig.add_gridspec(
        nrows=2,
        ncols=2,
        left=0.055,
        right=0.965,
        bottom=0.09,
        top=0.81,
        wspace=0.13,
        hspace=0.18,
    )
    for index, (metric, _label) in enumerate(METRICS):
        axis = fig.add_subplot(grid[index // 2, index % 2])
        _draw_metric_card(
            axis,
            data.loc[metric],
            current_label=current_label,
            previous_label=previous_label,
        )

    scope_label = {
        "separate": "별도재무제표",
        "consolidated": "연결재무제표",
    }.get(str(comparison.get("statement_scope") or "").lower(), "재무제표")
    selected_date = str(comparison.get("selected_date") or "").strip()
    footer = f"기준일 {selected_date}  ·  {scope_label}  ·  각 지표는 독립 척도로 표시"
    fig.text(0.055, 0.035, footer, ha="left", va="center", fontsize=8.5, color="#94A3B8")

    output_path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(output_path, dpi=220, facecolor=BACKGROUND_COLOR)
    plt.close(fig)


def _draw_metric_card(
    ax: plt.Axes,
    row: pd.Series,
    *,
    current_label: str,
    previous_label: str,
) -> None:
    """Draw one rounded metric card using an independent comparison scale."""

    ax.set_xlim(0.0, 1.0)
    ax.set_ylim(0.0, 1.0)
    ax.axis("off")
    ax.add_patch(
        FancyBboxPatch(
            (0.0, 0.0),
            1.0,
            1.0,
            transform=ax.transAxes,
            boxstyle="round,pad=0.012,rounding_size=0.035",
            facecolor="white",
            edgecolor=CARD_BORDER_COLOR,
            linewidth=1.0,
            clip_on=False,
            zorder=-10,
        )
    )

    label = str(row["label"])
    previous = float(row["previous_value_100m_krw"])
    current = float(row["current_value_100m_krw"])
    growth_value = row["yoy_change_pct"]
    growth = None if growth_value is None or pd.isna(growth_value) else float(growth_value)

    if growth is None:
        badge_text = "YoY N/A"
        badge_color = MUTED_TEXT_COLOR
        badge_background = TRACK_COLOR
    elif growth >= 0:
        badge_text = f"YoY  +{growth:.1f}%"
        badge_color = POSITIVE_COLOR
        badge_background = POSITIVE_BACKGROUND
    else:
        badge_text = f"YoY  {growth:.1f}%"
        badge_color = NEGATIVE_COLOR
        badge_background = NEGATIVE_BACKGROUND

    ax.text(
        0.06,
        0.84,
        label,
        transform=ax.transAxes,
        ha="left",
        va="center",
        fontsize=13,
        fontweight="bold",
        color=TEXT_COLOR,
    )
    ax.text(
        0.94,
        0.84,
        badge_text,
        transform=ax.transAxes,
        ha="right",
        va="center",
        fontsize=9.2,
        fontweight="bold",
        color=badge_color,
        bbox={
            "boxstyle": "round,pad=0.42,rounding_size=1.0",
            "facecolor": badge_background,
            "edgecolor": "none",
        },
    )

    ax.text(0.06, 0.65, current_label, transform=ax.transAxes, fontsize=8.8, color=MUTED_TEXT_COLOR)
    ax.text(
        0.06,
        0.50,
        f"{current:,.0f}",
        transform=ax.transAxes,
        fontsize=23,
        fontweight="bold",
        color=TEXT_COLOR,
    )
    ax.text(0.06, 0.415, "억원", transform=ax.transAxes, fontsize=8.5, color=MUTED_TEXT_COLOR)

    ax.text(
        0.94,
        0.65,
        previous_label,
        transform=ax.transAxes,
        ha="right",
        fontsize=8.8,
        color=MUTED_TEXT_COLOR,
    )
    ax.text(
        0.94,
        0.51,
        f"{previous:,.0f}",
        transform=ax.transAxes,
        ha="right",
        fontsize=13,
        fontweight="bold",
        color="#475569",
    )

    ax.plot([0.06, 0.94], [0.35, 0.35], transform=ax.transAxes, color=TRACK_COLOR, linewidth=1.0)
    scale = max(abs(previous), abs(current), 1.0)
    _draw_comparison_track(
        ax,
        y=0.225,
        label="전년",
        value=previous,
        scale=scale,
        color=PREVIOUS_COLOR,
    )
    _draw_comparison_track(
        ax,
        y=0.105,
        label="당기",
        value=current,
        scale=scale,
        color=CURRENT_COLOR,
    )


def _draw_comparison_track(
    ax: plt.Axes,
    *,
    y: float,
    label: str,
    value: float,
    scale: float,
    color: str,
) -> None:
    """Draw one normalized rounded comparison track inside a metric card."""

    x_start = 0.18
    track_width = 0.76
    height = 0.055
    ax.text(
        0.06,
        y + height / 2,
        label,
        transform=ax.transAxes,
        ha="left",
        va="center",
        fontsize=8.5,
        fontweight="bold" if label == "당기" else "normal",
        color=TEXT_COLOR if label == "당기" else MUTED_TEXT_COLOR,
    )
    ax.add_patch(
        FancyBboxPatch(
            (x_start, y),
            track_width,
            height,
            transform=ax.transAxes,
            boxstyle="round,pad=0,rounding_size=0.026",
            facecolor=TRACK_COLOR,
            edgecolor="none",
        )
    )
    fill_width = max(track_width * min(abs(value) / scale, 1.0), 0.012)
    fill_color = color if value >= 0 else NEGATIVE_COLOR
    ax.add_patch(
        FancyBboxPatch(
            (x_start, y),
            fill_width,
            height,
            transform=ax.transAxes,
            boxstyle="round,pad=0,rounding_size=0.026",
            facecolor=fill_color,
            edgecolor="none",
        )
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
