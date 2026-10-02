"""Render the Writer Agent's LLM report payload as one complete HTML document."""

from __future__ import annotations

import base64
import copy
from html import escape, unescape
import logging
from pathlib import Path
import re
from typing import Any

from shared.coerce import as_dict as _dict
from .html_report_spec import (
    REPORT_DISCLAIMER,
    REPORT_SECTIONS,
    TABLE_ITEM_KEYS,
    resolve_report_item_title,
    has_data_limit_content,
    reader_label_leaks,
    replace_english_grade_labels,
)
from .writer_io import write_text


logger = logging.getLogger(__name__)


MISSING_VALUE = "데이터 추가 필요"
MAIN_COLUMN_SECTION_KEYS = (
    "investment_call_thesis",
    "business_market_context",
    "key_evidence_table",
    "catalysts_execution",
    "risk_monitoring_matrix",
    "data_limits",
)
# Sections placed in the left column beside the sidebar on page one; every later
# section spans both columns so pages after the sidebar have no empty column.
SIDEBAR_COLUMN_SECTION_COUNT = 1


def render_formatted_html_report(
    report_payload: dict[str, Any],
    output_dir: str | Path,
) -> dict[str, str]:
    """Render and save report.html."""

    output_dir = Path(output_dir).expanduser().resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    html = build_complete_html(
        _embed_market_chart_assets(report_payload, output_dir=output_dir)
    )
    _warn_reader_label_leaks(html, output_dir=output_dir)
    report_path = output_dir / "report.html"
    write_text(report_path, html)
    legacy_final_path = output_dir / "final_report.html"
    if legacy_final_path.exists():
        legacy_final_path.unlink()
    return {
        "html_report": str(report_path),
        "report_html": str(report_path),
        "html_content": html,
    }


def _warn_reader_label_leaks(html: str, *, output_dir: Path) -> None:
    """Log, without failing, English grades or internal field names left visible."""

    leaks = reader_label_leaks(_visible_text(html))
    if leaks:
        logger.warning(
            "Reader-visible English grade labels or internal field names remain in %s: %s",
            output_dir / "report.html",
            leaks,
        )


def _visible_text(html: str) -> str:
    body = re.sub(r"<(style|script)\b.*?</\1>", " ", html, flags=re.IGNORECASE | re.DOTALL)
    return unescape(re.sub(r"<[^>]+>", " ", body))


def _embed_market_chart_assets(
    report_payload: dict[str, Any],
    *,
    output_dir: Path,
) -> dict[str, Any]:
    """Return a render-only payload whose local PNG charts are embedded in HTML."""

    embedded = copy.deepcopy(report_payload)
    output_dir = output_dir.resolve()
    charts = embedded.get("report_charts")
    if not isinstance(charts, list):
        charts = embedded.get("market_charts")
    if not isinstance(charts, list):
        return embedded
    for chart in charts:
        if not isinstance(chart, dict):
            continue
        source_ref = str(chart.get("src") or "").strip()
        if not source_ref or source_ref.startswith("data:image/"):
            continue
        source = (output_dir / source_ref).resolve()
        try:
            source.relative_to(output_dir)
        except ValueError:
            continue
        if not source.is_file() or source.suffix.lower() != ".png":
            continue
        encoded = base64.b64encode(source.read_bytes()).decode("ascii")
        chart["src"] = f"data:image/png;base64,{encoded}"
    return embedded


def build_complete_html(report_payload: dict[str, Any]) -> str:
    metadata = _dict(report_payload.get("metadata"))
    company_name = metadata.get("company_name") or MISSING_VALUE
    title = metadata.get("report_title") or f"{company_name} Investment Report"
    indexed_sections = {
        section["key"]: (index, section)
        for index, section in enumerate(REPORT_SECTIONS, start=1)
    }
    section_charts, unplaced_charts = _place_report_charts(report_payload)
    rendered_sections = [
        _render_section(
            report_payload,
            index,
            section,
            location="main",
            metadata=metadata,
            charts=section_charts.get(key, []),
        )
        for key in MAIN_COLUMN_SECTION_KEYS
        for index, section in [indexed_sections[key]]
    ]
    lead_sections = "\n".join(rendered_sections[:SIDEBAR_COLUMN_SECTION_COUNT])
    flow_sections = "\n".join(rendered_sections[SIDEBAR_COLUMN_SECTION_COUNT:])
    return f"""<!DOCTYPE html>
<html lang="ko">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <meta name="investment-recommendation" content="{_text(metadata.get('recommendation') or '')}">
  <meta name="investment-horizon" content="{_text(metadata.get('investment_horizon') or '')}">
  <title>{_text(replace_english_grade_labels(title))}</title>
  <style>
{_css()}
  </style>
</head>
<body>
  <main class="a4-sheet">
{_document_header(metadata)}
    <div class="paper-grid">
      <div class="main-column">
{lead_sections}
      </div>
      <div class="full-width-flow">
{flow_sections}
{_render_report_charts(unplaced_charts)}
      </div>
      <div class="visual-sidebar">
{_render_sidebar_key_data(metadata)}
      </div>
    </div>
    <footer class="report-disclaimer">{_text(REPORT_DISCLAIMER)}</footer>
  </main>
</body>
</html>
"""


OPINION_LABELS = {"Buy": "매수", "Hold": "중립", "Sell": "매도"}
OPINION_CLASSES = {"Buy": "opinion-buy", "Hold": "opinion-hold", "Sell": "opinion-sell"}


def _document_header(metadata: dict[str, Any]) -> str:
    """Render the page-one band: company, code, base date, opinion badge and headline."""

    company = metadata.get("company_name") or MISSING_VALUE
    base_date = metadata.get("base_date") or MISSING_VALUE
    headline = metadata.get("report_title") or ""
    stock_code = str(metadata.get("stock_code") or "").strip()
    code_html = f'<span class="stock-code">{_text(stock_code)}</span>' if stock_code else ""
    recommendation = metadata.get("recommendation")
    opinion = OPINION_LABELS.get(recommendation, "")
    horizon = metadata.get("investment_horizon") or MISSING_VALUE
    badge_html = ""
    if opinion:
        badge_html = f"""
        <div class="opinion-badge {OPINION_CLASSES[recommendation]}">
          <span class="badge-caption">투자의견</span>
          <strong class="investment-opinion">{opinion}</strong>
          <span class="badge-horizon">{_inline(horizon)}</span>
        </div>"""
    return f"""    <header class="document-header">
      <div class="header-top">
        <p class="report-kicker">기업분석 리포트</p>
        <p class="header-date"><span>기준일</span> {_inline(base_date)}</p>
      </div>
      <div class="header-main">
        <div class="header-title">
          <p class="report-name">{_inline(company)}{code_html}</p>
          <h1>{_inline(replace_english_grade_labels(headline))}</h1>
        </div>{badge_html}
      </div>
    </header>"""


def _render_sidebar_key_data(metadata: dict[str, Any]) -> str:
    """Render the sidebar figures prepared during payload normalization."""

    groups: dict[str, list[str]] = {}
    for metric in metadata.get("key_metrics") or []:
        if not isinstance(metric, dict) or not str(metric.get("label") or "").strip():
            continue
        groups.setdefault(str(metric.get("group") or ""), []).append(
            _metric_row(metric.get("label"), metric.get("value") or MISSING_VALUE)
        )
    groups["판단 정보"] = [
        _metric_row("자료 충실도", _level_label(metadata.get("data_coverage"))),
        _metric_row("판단 확신도", _level_label(metadata.get("decision_confidence"))),
    ]
    group_html = "\n".join(
        f"""          <div class="metric-group">
            {f'<h3>{_text(name)}</h3>' if name else ''}
            <dl>
{chr(10).join(rows)}
            </dl>
          </div>"""
        for name, rows in groups.items()
    )
    market_date = str(metadata.get("market_data_date") or "").strip()
    note_html = (
        f'\n          <p class="panel-note">시세·가치평가 기준 {_inline(market_date)}</p>'
        if market_date and metadata.get("key_metrics")
        else ""
    )
    return f"""        <section class="sidebar-panel key-data-panel">
          <h2>핵심 지표</h2>{note_html}
{group_html}
        </section>"""


def _metric_row(label: Any, value: Any) -> str:
    text = str(value)
    tone = ""
    if re.match(r"^\+\d", text) and re.search(r"[1-9]", text):
        tone = ' class="value-up"'
    elif re.match(r"^-\d", text) and re.search(r"[1-9]", text):
        tone = ' class="value-down"'
    return f"              <div><dt>{_inline(label)}</dt><dd{tone}>{_inline(text)}</dd></div>"


# Sections that may host an inline chart; the optional limits section never does.
CHART_HOST_SECTION_KEYS = tuple(key for key in MAIN_COLUMN_SECTION_KEYS if key != "data_limits")


def _place_report_charts(
    report_payload: dict[str, Any],
) -> tuple[dict[str, list[dict[str, Any]]], list[dict[str, Any]]]:
    """Give each chart its own section, chosen by card-key overlap with its basis cards.

    A section hosts at most one chart. Charts with the strongest overlap choose first
    (ties keep the selection order); a chart whose best section is taken moves to its
    next-best overlapping section, and charts left without one go to the closing block.
    """

    charts = [
        chart
        for chart in (
            report_payload.get("report_charts")
            or report_payload.get("market_charts")
            or []
        )
        if isinstance(chart, dict) and str(chart.get("src") or "").strip()
    ]
    basis_by_chart = {
        str(detail.get("chart_key") or ""): {
            str(key) for key in detail.get("basis_card_keys") or [] if str(key).strip()
        }
        for detail in report_payload.get("chart_selection_details") or []
        if isinstance(detail, dict)
    }
    sections = _dict(report_payload.get("sections"))
    section_keys: dict[str, set[str]] = {}
    for key in CHART_HOST_SECTION_KEYS:
        keys: set[str] = set()
        for item in _dict(sections.get(key)).values():
            keys.update(str(card_key) for card_key in _dict(item).get("card_keys") or [])
        section_keys[key] = keys
    # Candidate sections per chart, best overlap first, then report order.
    candidates: list[list[tuple[int, str]]] = []
    for chart in charts:
        basis = basis_by_chart.get(str(chart.get("chart_key") or ""), set())
        ranked = sorted(
            (-len(basis & section_keys[key]), order, key)
            for order, key in enumerate(CHART_HOST_SECTION_KEYS)
            if basis & section_keys[key]
        )
        candidates.append([(-overlap, key) for overlap, _order, key in ranked])
    choice_order = sorted(
        range(len(charts)),
        key=lambda index: (-(candidates[index][0][0] if candidates[index] else 0), index),
    )
    assigned: dict[int, str] = {}
    taken: set[str] = set()
    for index in choice_order:
        for _overlap, key in candidates[index]:
            if key not in taken:
                assigned[index] = key
                taken.add(key)
                break
    placed: dict[str, list[dict[str, Any]]] = {key: [] for key in CHART_HOST_SECTION_KEYS}
    unplaced: list[dict[str, Any]] = []
    for index, chart in enumerate(charts):
        if index in assigned:
            placed[assigned[index]].append(chart)
        else:
            unplaced.append(chart)
    return placed, unplaced


def _render_chart_figure(chart: dict[str, Any]) -> str:
    return f"""
          <figure class="report-chart">
            <img src="{escape(str(chart['src']), quote=True)}" alt="{escape(replace_english_grade_labels(chart.get('alt') or chart.get('title') or '주요 차트'), quote=True)}">
            <figcaption>
              <strong class="chart-caption-title">{_text(replace_english_grade_labels(chart.get('title') or '주요 차트'))}</strong>
              <span class="chart-observation">{_text(replace_english_grade_labels(chart.get('chart_observation') or ''))}</span>
              <span class="chart-interpretation">{_text(replace_english_grade_labels(chart.get('investment_interpretation') or ''))}</span>
            </figcaption>
          </figure>"""


def _render_report_charts(charts: list[dict[str, Any]]) -> str:
    """Render charts that no section claimed as a closing block in the main column."""

    if not charts:
        return ""
    figures = "\n".join(_render_chart_figure(chart) for chart in charts)
    return f"""
        <section class="report-chart-section">
          <h1>주요 차트</h1>
          <div class="report-chart-grid">
{figures}
          </div>
        </section>
"""


def _level_label(value: Any) -> str:
    return {
        "high": "높음",
        "medium": "보통",
        "low": "낮음",
    }.get(str(value or "").strip().lower(), str(value or MISSING_VALUE))


def _render_section(
    report_payload: dict[str, Any],
    index: int,
    section: dict[str, Any],
    *,
    location: str,
    metadata: dict[str, Any],
    charts: list[dict[str, Any]] | None = None,
) -> str:
    section_payload = _dict(_dict(report_payload.get("sections")).get(section["key"]))
    if section["key"] == "data_limits" and not has_data_limit_content(report_payload):
        return ""
    items = "\n".join(
        _render_item(
            section["id"],
            section["key"],
            section_payload,
            item,
            metadata=metadata,
        )
        for item in section["items"]
    )
    section_class = f"report-section {location}-section"
    display_title = section.get("display_title") or section["title"]
    if section["key"] == "catalysts_execution":
        display_title = f"향후 {metadata.get('investment_horizon') or ''} 전망"
    chart_html = ""
    if charts:
        figures = "\n".join(_render_chart_figure(chart) for chart in charts)
        chart_html = f"""
      <div class="section-charts">
{figures}
      </div>"""
    return f"""
    <section id="{section["id"]}" class="{section_class}">
      <h1><span class="section-number">{index}.</span> {_text(display_title)}</h1>
{items}{chart_html}
    </section>
"""


def _render_item(
    section_id: str,
    section_key: str,
    section_payload: dict[str, Any],
    item: tuple[str, str, str],
    *,
    metadata: dict[str, Any],
) -> str:
    item_key, item_title, item_type = item
    item_title = resolve_report_item_title(
        section_key=section_key,
        item_key=item_key,
        default_title=item_title,
        metadata=metadata,
    )
    item_id = f"{section_id}-{item_key.replace('_', '-')}"
    raw_value = section_payload.get(item_key)
    if item_type == "table" or item_key in TABLE_ITEM_KEYS:
        body = _render_table(raw_value, item_key=item_key)
    else:
        body = _render_text_block(raw_value, prefer_list=item_type == "list")
    return f"""
      <h2 id="{item_id}">{_text(item_title)}</h2>
{body}
"""


def _render_text_block(value: Any, *, prefer_list: bool = False) -> str:
    payload = _dict(value)
    paragraphs = _clean_list(payload.get("paragraphs"))
    bullets = _clean_list(payload.get("bullets"))
    if not paragraphs and not bullets:
        if isinstance(value, str) and value.strip():
            paragraphs = [value.strip()]
        else:
            paragraphs = [MISSING_VALUE]
    # Render-time safety net for model-authored prose; the payload is unchanged.
    paragraphs = [replace_english_grade_labels(paragraph) for paragraph in paragraphs]
    bullets = [replace_english_grade_labels(bullet) for bullet in bullets]
    paragraph_html = "\n".join(f"      <p>{_inline(paragraph)}</p>" for paragraph in paragraphs)
    if prefer_list or bullets:
        if not bullets:
            bullets = paragraphs
            paragraph_html = ""
        bullet_html = "\n".join(f"        <li>{_inline(item)}</li>" for item in bullets)
        return f"""{paragraph_html}
      <ul>
{bullet_html}
      </ul>"""
    return paragraph_html


def _render_table(value: Any, *, item_key: str = "") -> str:
    payload = _dict(value)
    columns = _clean_list(payload.get("columns"))
    rows = payload.get("rows")
    if not isinstance(rows, list):
        rows = []
    if not columns:
        columns = ["항목", "내용"]
    if not rows:
        rows = [[MISSING_VALUE for _ in columns]]
    head = "".join(
        _render_table_header(column, index, item_key=item_key)
        for index, column in enumerate(columns)
    )
    body_rows = "\n".join(
        _render_table_row(row, columns, item_key=item_key)
        for row in rows
    )
    column_group = ""
    column_classes = {
        "evidence_table": (
            "key-evidence-columns",
            (
                "evidence-axis-column",
                "evidence-observation-column",
                "evidence-interpretation-column",
                "evidence-impact-column",
            ),
        ),
        "risk_monitoring_table": (
            "risk-monitoring-columns",
            ("risk-title-column", "risk-current-column", "risk-monitoring-column"),
        ),
    }.get(item_key)
    if column_classes:
        group_class, col_classes = column_classes
        # One <col> per rendered column so fixed widths match the real table.
        cols = "".join(
            f"\n          <col class=\"{name}\">" for name in col_classes[: len(columns)]
        )
        column_group = f"""
        <colgroup class="{group_class}">{cols}
        </colgroup>"""
    return f"""
      <table>{column_group}
        <thead>
          <tr>{head}</tr>
        </thead>
        <tbody>
{body_rows}
        </tbody>
      </table>
"""


def _render_table_header(column: str, index: int, *, item_key: str) -> str:
    cell_class = ' class="evidence-impact-cell"' if item_key == "evidence_table" and index == 3 else ""
    return f"<th{cell_class}>{_inline(column)}</th>"


def _render_table_row(row: Any, columns: list[str], *, item_key: str = "") -> str:
    if isinstance(row, dict):
        cells = [_table_cell(row, column) for column in columns]
    elif isinstance(row, list):
        cells = [row[index] if index < len(row) else MISSING_VALUE for index in range(len(columns))]
    else:
        cells = [MISSING_VALUE for _ in columns]
    rendered_cells = []
    for index, cell in enumerate(cells):
        display_cell = replace_english_grade_labels(cell)
        if item_key == "evidence_table" and index == 1:
            rendered_cells.append(f'<td class="evidence-facts-cell">{_render_fact_list(display_cell)}</td>')
        elif item_key == "evidence_table" and index == 3:
            effect_class = {
                "긍정 요인": "impact-positive",
                "부담 요인": "impact-negative",
                "혼합": "impact-mixed",
                "중립": "impact-neutral",
                "핵심 근거": "impact-positive",
                "반대 근거": "impact-negative",
                "위험 신호": "impact-mixed",
                "판단 문맥": "impact-reference",
            }.get(str(cell), "impact-reference")
            rendered_cells.append(
                f'<td class="evidence-impact-cell"><span class="impact-badge {effect_class}">'
                f"{_inline(display_cell)}</span></td>"
            )
        else:
            rendered_cells.append(f"<td>{_inline(display_cell)}</td>")
    cell_html = "".join(rendered_cells)
    return f"          <tr>{cell_html}</tr>"


def _render_fact_list(value: Any) -> str:
    """Show one confirmed fact per line with a muted label and its value."""

    lines = [line.strip() for line in str(value).splitlines() if line.strip()]
    if not lines:
        return _inline(value)
    items = []
    for line in lines:
        label, value_text = _split_fact_label(line)
        label_html = f'<span class="fact-label">{_inline(label)}</span> ' if label else ""
        items.append(f"<li>{label_html}<span class=\"fact-value\">{_render_fact_value(value_text)}</span></li>")
    return f'<ul class="fact-list">{"".join(items)}</ul>'


def _split_fact_label(line: str) -> tuple[str, str]:
    label, separator, rest = line.partition(": ")
    if separator and rest.strip() and 0 < len(label) <= 40 and not re.search(r"[.。]$", label):
        return label.strip(), rest.strip()
    return "", line


def _render_fact_value(value: str) -> str:
    """Keep compound values on one line, muting the label of each "label: value" part."""

    parts = value.split(" · ")
    if len(parts) == 1:
        return _inline(value)
    rendered = []
    for part in parts:
        label, text = _split_fact_label(part)
        # Short "label value" pairs stay unbroken; longer ones may wrap inside the cell.
        part_class = "fact-part fact-part-short" if len(label) + len(text) <= 18 else "fact-part"
        rendered.append(
            f'<span class="{part_class}"><span class="fact-sublabel">{_inline(label)}</span> {_inline(text)}</span>'
            if label
            else f'<span class="{part_class}">{_inline(text)}</span>'
        )
    return '<span class="fact-sep"> · </span>'.join(rendered)


def _table_cell(row: dict[str, Any], column: str) -> Any:
    if column in row:
        return row[column]
    normalized_column = _normalize_key(column)
    if normalized_column in row:
        return row[normalized_column]
    for key, value in row.items():
        key_text = str(key).strip()
        if key_text.startswith(str(column).strip()):
            return value
        if _normalize_key(key_text).startswith(normalized_column):
            return value
    return MISSING_VALUE


def _normalize_key(value: Any) -> str:
    return str(value).strip().lower().replace(" ", "_").replace("/", "_")


def _clean_list(value: Any) -> list[str]:
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    if isinstance(value, str) and value.strip():
        return [value.strip()]
    return []


def _inline(value: Any) -> str:
    escaped = escape(str(value), quote=False)
    allowed = {
        "&lt;strong&gt;": "<strong>",
        "&lt;/strong&gt;": "</strong>",
    }
    for source, target in allowed.items():
        escaped = escaped.replace(source, target)
    return escaped


def _text(value: Any) -> str:
    return escape(str(value), quote=False)


def _css() -> str:
    return """    :root {
      --ink: #111827;
      --muted: #6b7280;
      --line: #e5e7eb;
      --accent: #1f3a5f;
      --accent-soft: #eef2f7;
      --buy: #c62828;
      --hold: #6b7280;
      --sell: #1565c0;
      --paper: #ffffff;
      --desk: #e5e7eb;
    }
    @page {
      size: A4;
      margin: 0;
    }
    * { box-sizing: border-box; }
    html {
      width: 210mm;
      min-height: 297mm;
      margin: 0 auto;
      background: var(--desk);
    }
    body {
      margin: 0;
      font-family: "Pretendard", "Noto Sans KR", "Noto Sans CJK KR", "Malgun Gothic", "Apple SD Gothic Neo", sans-serif;
      color: var(--ink);
      font-size: 9.5pt;
      line-height: 1.6;
      background: var(--desk);
      word-break: keep-all;
      overflow-wrap: break-word;
      -webkit-print-color-adjust: exact;
      print-color-adjust: exact;
    }
    .a4-sheet {
      width: 210mm;
      min-height: 297mm;
      margin: 0 auto;
      padding: 8mm 7mm 6mm;
      position: relative;
      background: var(--paper);
      overflow: visible;
    }
    .document-header {
      margin: 0 0 5mm;
      padding: 3mm 4mm 3.5mm;
      border-top: 3px solid var(--accent);
      background: var(--accent-soft);
    }
    .header-top {
      display: flex;
      justify-content: space-between;
      align-items: baseline;
      gap: 4mm;
      margin: 0 0 1.5mm;
      color: var(--muted);
      font-size: 8pt;
      line-height: 1.3;
    }
    .header-top p {
      margin: 0;
    }
    .report-kicker {
      color: var(--accent);
      font-weight: 700;
      letter-spacing: 0.02em;
    }
    .header-date span {
      margin-right: 1mm;
    }
    .header-main {
      display: flex;
      justify-content: space-between;
      align-items: flex-start;
      gap: 6mm;
    }
    .header-title {
      min-width: 0;
      flex: 1 1 auto;
    }
    .report-name {
      margin: 0 0 1.5mm;
      color: var(--ink);
      font-size: 20pt;
      line-height: 1.15;
      font-weight: 700;
    }
    .stock-code {
      margin-left: 2.5mm;
      color: var(--muted);
      font-size: 10pt;
      font-weight: 400;
      font-variant-numeric: tabular-nums;
    }
    .document-header h1 {
      margin: 0;
      padding: 0;
      border: 0;
      color: var(--accent);
      font-size: 12pt;
      line-height: 1.45;
      font-weight: 600;
    }
    .opinion-badge {
      flex: 0 0 auto;
      display: flex;
      flex-direction: column;
      align-items: center;
      min-width: 24mm;
      padding: 2mm 3mm;
      border-radius: 2mm;
      color: #ffffff;
      text-align: center;
      line-height: 1.2;
    }
    .opinion-buy { background: var(--buy); }
    .opinion-hold { background: var(--hold); }
    .opinion-sell { background: var(--sell); }
    .badge-caption,
    .badge-horizon {
      font-size: 7.5pt;
      opacity: 0.9;
    }
    .opinion-badge .investment-opinion {
      margin: 0.6mm 0;
      color: #ffffff;
      font-size: 15pt;
      font-weight: 700;
      line-height: 1.1;
    }
    .paper-grid {
      display: grid;
      grid-template-columns: minmax(0, 145mm) 44mm;
      column-gap: 7mm;
      align-items: start;
    }
    .main-column,
    .visual-sidebar,
    .full-width-flow {
      min-width: 0;
    }
    .main-column {
      grid-column: 1;
      grid-row: 1;
    }
    .visual-sidebar {
      grid-column: 2;
      grid-row: 1;
    }
    .full-width-flow {
      grid-column: 1 / -1;
      grid-row: 2;
    }
    .report-section {
      margin: 0 0 5mm;
      break-inside: avoid;
      page-break-inside: avoid;
    }
    h1 {
      margin: 0 0 2mm;
      color: var(--ink);
      font-size: 12pt;
      line-height: 1.3;
      font-weight: 700;
    }
    .main-section > h1,
    .report-chart-section > h1 {
      padding: 0.3mm 0 0.3mm 2.2mm;
      border-left: 3px solid var(--accent);
      break-after: avoid;
      page-break-after: avoid;
    }
    .section-number {
      color: var(--accent);
    }
    h2 {
      margin: 0 0 1.2mm;
      color: var(--muted);
      font-size: 8pt;
      line-height: 1.3;
      font-weight: 600;
      break-after: avoid;
      page-break-after: avoid;
    }
    p {
      margin: 0 0 0.7em;
      text-align: left;
    }
    ul {
      margin: 0 0 0.7em;
      padding-left: 4mm;
    }
    li {
      margin: 0 0 0.3em;
    }
    strong {
      color: var(--ink);
      font-weight: 700;
    }
    table {
      width: 100%;
      border-collapse: collapse;
      margin: 1mm 0 2mm;
      background: var(--paper);
      font-size: 7.6pt;
      line-height: 1.45;
      table-layout: fixed;
      word-break: keep-all;
      overflow-wrap: break-word;
      font-variant-numeric: tabular-nums;
      border-top: 1.5px solid var(--accent);
      border-bottom: 1px solid var(--line);
    }
    th,
    td {
      border: 0;
      border-bottom: 1px solid var(--line);
      padding: 1.5mm 2mm;
      vertical-align: top;
      text-align: left;
    }
    th {
      background: var(--accent-soft);
      color: var(--accent);
      font-weight: 700;
    }
    tr {
      break-inside: avoid;
      page-break-inside: avoid;
    }
    .key-evidence-columns .evidence-axis-column {
      width: 17%;
    }
    .key-evidence-columns .evidence-observation-column {
      width: 52%;
    }
    .key-evidence-columns .evidence-interpretation-column {
      width: 31%;
    }
    .key-evidence-columns .evidence-impact-column {
      width: 12%;
    }
    .evidence-impact-cell {
      text-align: center;
    }
    .fact-list {
      margin: 0;
      padding: 0;
      list-style: none;
    }
    .fact-list li {
      margin: 0 0 0.6mm;
    }
    .fact-list li:last-child {
      margin-bottom: 0;
    }
    .fact-label,
    .fact-sublabel {
      color: var(--muted);
    }
    .fact-label {
      margin-right: 0.6mm;
    }
    .fact-part-short {
      white-space: nowrap;
    }
    .fact-sep {
      color: #c4c9d1;
    }
    .fact-value {
      font-variant-numeric: tabular-nums;
    }
    .impact-badge {
      display: inline-block;
      min-width: 12mm;
      padding: 0.45mm 0.8mm;
      border-radius: 2px;
      font-weight: 700;
      line-height: 1.2;
      text-align: center;
    }
    .impact-positive {
      color: var(--buy);
      background: #fdecea;
    }
    .impact-negative {
      color: var(--sell);
      background: #e8f0fb;
    }
    .impact-mixed {
      color: #854d0e;
      background: #fef3c7;
    }
    .impact-neutral,
    .impact-reference {
      color: var(--muted);
      background: #f3f4f6;
    }
    .risk-monitoring-columns .risk-title-column {
      width: 20%;
    }
    .risk-monitoring-columns .risk-current-column {
      width: 45%;
    }
    .risk-monitoring-columns .risk-monitoring-column {
      width: 35%;
    }
    .visual-sidebar {
      min-height: 0;
      overflow: visible;
    }
    .report-disclaimer {
      position: static;
      margin: 2mm 0 0;
      color: var(--muted);
      font-size: 6pt;
      font-weight: 400;
      line-height: 1.35;
      text-align: center;
      white-space: normal;
      word-break: keep-all;
    }
    .sidebar-panel {
      margin: 0 0 3mm;
      padding: 2.5mm 2.8mm 2mm;
      border-top: 3px solid var(--accent);
      background: var(--accent-soft);
      break-inside: avoid;
    }
    .sidebar-panel h2 {
      display: block;
      margin: 0 0 0.6mm;
      color: var(--accent);
      font-size: 10pt;
      font-weight: 700;
      line-height: 1.2;
      text-align: left;
    }
    .panel-note {
      margin: 0 0 1.8mm;
      color: var(--muted);
      font-size: 6.8pt;
      line-height: 1.3;
    }
    .metric-group {
      margin: 0 0 2mm;
    }
    .metric-group:last-child {
      margin-bottom: 0;
    }
    .metric-group h3 {
      margin: 0 0 0.8mm;
      padding-bottom: 0.5mm;
      border-bottom: 1px solid #d5dde8;
      color: var(--muted);
      font-size: 7pt;
      font-weight: 600;
      line-height: 1.2;
    }
    .key-data-panel dl {
      margin: 0;
    }
    .key-data-panel dl div {
      display: grid;
      grid-template-columns: minmax(0, 1fr) auto;
      align-items: baseline;
      gap: 1.5mm;
      padding: 0.55mm 0;
      font-size: 7.6pt;
      line-height: 1.3;
    }
    .key-data-panel dt {
      color: var(--ink);
      font-weight: 400;
    }
    .key-data-panel dd {
      margin: 0;
      color: var(--ink);
      font-weight: 700;
      text-align: right;
      white-space: nowrap;
      font-variant-numeric: tabular-nums;
    }
    .key-data-panel dd.value-up {
      color: var(--buy);
    }
    .key-data-panel dd.value-down {
      color: var(--sell);
    }
    .section-charts {
      margin: 2.5mm 0 0;
    }
    .report-chart-section {
      margin: 0 0 5mm;
    }
    .report-chart {
      max-width: 150mm;
      margin: 0 auto 3mm;
      min-width: 0;
      break-inside: avoid;
      page-break-inside: avoid;
    }
    .report-chart img {
      display: block;
      width: 100%;
      max-width: 100%;
      height: auto;
      max-height: 70mm;
      margin: 0 auto;
      object-fit: contain;
      border: 1px solid var(--line);
      background: var(--paper);
    }
    .report-chart figcaption {
      margin-top: 1mm;
      color: var(--ink);
      font-size: 7pt;
      line-height: 1.45;
      text-align: left;
    }
    .chart-caption-title,
    .chart-observation,
    .chart-interpretation {
      display: block;
    }
    .chart-caption-title {
      margin-bottom: 0.4mm;
      color: var(--accent);
      font-size: 7.5pt;
      font-weight: 700;
    }
    .chart-interpretation {
      margin-top: 0.3mm;
      color: var(--muted);
    }
    @media print {
      @page {
        size: A4;
        margin: 0;
      }
      html,
      body {
        width: 210mm;
        height: auto;
        min-height: 0;
        margin: 0 !important;
        padding: 0 !important;
        background: var(--paper);
        overflow: visible;
      }
      body {
        font-size: 9pt;
        line-height: 1.55;
      }
      .a4-sheet {
        width: 210mm;
        height: auto;
        min-height: 594mm;
        max-height: none;
        margin: 0 !important;
        padding: 8mm 7mm 6mm;
        box-shadow: none !important;
        overflow: visible;
      }
      .paper-grid {
        display: grid;
        grid-template-columns: minmax(0, 145mm) 44mm;
        column-gap: 7mm;
        height: auto;
        min-height: 0;
        overflow: visible;
      }
      .visual-sidebar {
        height: auto;
        overflow: visible;
      }
      .report-section {
        margin-bottom: 4mm;
      }
      #data-limits p {
        font-size: 8.4pt;
        line-height: 1.5;
      }
      .report-section,
      .sidebar-panel {
        break-inside: auto;
        page-break-inside: auto;
      }
      .report-disclaimer {
        position: static;
        margin: 2mm 0 0;
        white-space: normal;
      }
    }
    @media screen {
      .a4-sheet {
        box-shadow: 0 12px 32px rgba(15, 23, 42, 0.18);
      }
    }
    @media screen and (max-width: 820px) {
      html,
      body {
        width: auto;
        min-height: 0;
      }
      .a4-sheet {
        width: auto;
        height: auto;
        min-height: 0;
        padding: 16px;
        overflow: visible;
      }
      .header-main {
        flex-direction: column;
        gap: 3mm;
      }
      .paper-grid {
        display: block;
        height: auto;
      }
      .visual-sidebar {
        height: auto;
        margin-top: 18px;
        padding-left: 0;
        border-left: 0;
      }
      .report-disclaimer {
        position: static;
        margin-top: 18px;
        white-space: normal;
      }
    }"""
