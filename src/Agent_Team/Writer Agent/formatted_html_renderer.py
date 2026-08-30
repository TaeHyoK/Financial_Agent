"""Render the Writer Agent's LLM report payload as one complete HTML document."""

from __future__ import annotations

import base64
import copy
from html import escape
from pathlib import Path
from typing import Any

from html_report_spec import (
    REPORT_DISCLAIMER,
    REPORT_SECTIONS,
    TABLE_ITEM_KEYS,
    resolve_report_item_title,
)
from writer_io import write_text


MISSING_VALUE = "데이터 추가 필요"
MAIN_COLUMN_SECTION_KEYS = (
    "investment_call_thesis",
    "business_market_context",
    "key_evidence_table",
    "catalysts_execution",
    "risk_monitoring_matrix",
    "data_limits",
)


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
    main_sections = "\n".join(
        _render_section(
            report_payload,
            index,
            section,
            location="main",
            metadata=metadata,
        )
        for key in MAIN_COLUMN_SECTION_KEYS
        for index, section in [indexed_sections[key]]
    )
    return f"""<!DOCTYPE html>
<html lang="ko">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{_text(title)}</title>
  <style>
{_css()}
  </style>
</head>
<body>
  <main class="a4-sheet">
    <div class="paper-grid">
      <div class="main-column">
        {_document_header(metadata)}
{main_sections}
      </div>
      <div class="visual-sidebar">
        {_sidebar_header(metadata)}
{_render_sidebar_key_data(metadata)}
{_render_sidebar_signal_summary(report_payload)}
      </div>
    </div>
{_render_report_charts(report_payload)}
    <footer class="report-disclaimer">{_text(REPORT_DISCLAIMER)}</footer>
  </main>
</body>
</html>
"""


def _document_header(metadata: dict[str, Any]) -> str:
    company = metadata.get("company_name") or MISSING_VALUE
    base_date = metadata.get("base_date") or MISSING_VALUE
    return f"""
    <header class="document-header">
      <p class="report-name">{_inline(company)} 투자 리서치</p>
      <div class="meta-grid">
        <div><span>기준일</span><strong>{_inline(base_date)}</strong></div>
      </div>
    </header>
"""


def _sidebar_header(metadata: dict[str, Any]) -> str:
    base_date = metadata.get("base_date") or MISSING_VALUE
    horizon = metadata.get("investment_horizon") or MISSING_VALUE
    return f"""
        <div class="sidebar-summary">
          <p class="sidebar-brand">기업분석 리포트</p>
          <dl>
            <div><dt>기준일</dt><dd>{_inline(base_date)}</dd></div>
            <div><dt>투자기간</dt><dd>{_inline(horizon)}</dd></div>
          </dl>
        </div>
"""


def _render_sidebar_key_data(metadata: dict[str, Any]) -> str:
    coverage = _level_label(metadata.get("data_coverage"))
    confidence = _level_label(metadata.get("decision_confidence"))
    return f"""
        <section class="sidebar-panel key-data-panel">
          <h2>핵심 정보</h2>
          <dl>
            <div><dt>자료 충실도</dt><dd>{_inline(coverage)}</dd></div>
            <div><dt>판단 확신도</dt><dd>{_inline(confidence)}</dd></div>
          </dl>
        </section>
"""


def _render_sidebar_signal_summary(report_payload: dict[str, Any]) -> str:
    sections = _dict(report_payload.get("sections"))
    evidence = _dict(_dict(sections.get("key_evidence_table")).get("evidence_table"))
    rows = [row for row in evidence.get("rows") or [] if isinstance(row, dict)]
    role_based = any(str(row.get("_strategy_role") or "") for row in rows)
    groups = ({
        "판단 지지": [
            str(row.get("핵심 근거") or "").strip()
            for row in rows
            if row.get("_strategy_role") in {"primary", "supports_decision"}
        ][:3],
        "반대 논리": [
            str(row.get("핵심 근거") or "").strip()
            for row in rows
            if row.get("_strategy_role") in {"counter", "opposes_decision"}
        ][:3],
        "불확실성": [
            str(row.get("핵심 근거") or "").strip()
            for row in rows
            if row.get("_strategy_role") in {"monitoring", "limits_confidence"}
        ][:3],
    } if role_based else {
        "긍정 요인": [
            str(row.get("핵심 근거") or "").strip()
            for row in rows
            if row.get("_investment_effect") == "positive"
        ][:3],
        "부담 요인": [
            str(row.get("핵심 근거") or "").strip()
            for row in rows
            if row.get("_investment_effect") == "negative"
        ][:3],
    })
    visible_groups = {label: values for label, values in groups.items() if values}
    if not visible_groups:
        return ""
    content = "\n".join(
        f"""
          <div class="signal-group">
            <h3>{_text(label)}</h3>
            <ul>{''.join(f'<li>{_inline(value)}</li>' for value in values)}</ul>
          </div>"""
        for label, values in visible_groups.items()
    )
    return f"""
        <section class="sidebar-panel signal-panel">
          <h2>판단 요인</h2>
{content}
        </section>
"""


def _render_report_charts(report_payload: dict[str, Any]) -> str:
    charts = [
        chart
        for chart in (
            report_payload.get("report_charts")
            or report_payload.get("market_charts")
            or []
        )
        if isinstance(chart, dict) and str(chart.get("src") or "").strip()
    ]
    if not charts:
        return ""
    figures = "\n".join(
        f"""
          <figure class="report-chart">
            <img src="{escape(str(chart['src']), quote=True)}" alt="{escape(str(chart.get('alt') or chart.get('title') or '주요 차트'), quote=True)}">
            <figcaption>
              <strong class="chart-caption-title">{_text(chart.get('title') or '주요 차트')}</strong>
              <span class="chart-observation">{_text(chart.get('chart_observation'))}</span>
              <span class="chart-interpretation">{_text(chart.get('investment_interpretation'))}</span>
            </figcaption>
          </figure>"""
        for chart in charts[:2]
    )
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
) -> str:
    section_payload = _dict(_dict(report_payload.get("sections")).get(section["key"]))
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
    return f"""
    <section id="{section["id"]}" class="{section_class}">
      <h1>{index}. {_text(section.get("display_title") or section["title"])}</h1>
{items}
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
    if item_key == "evidence_table":
        column_group = """
        <colgroup class="key-evidence-columns">
          <col class="evidence-axis-column">
          <col class="evidence-observation-column">
          <col class="evidence-interpretation-column">
          <col class="evidence-impact-column">
        </colgroup>"""
    elif item_key == "risk_monitoring_table":
        column_group = """
        <colgroup class="risk-monitoring-columns">
          <col class="risk-title-column">
          <col class="risk-current-column">
          <col class="risk-monitoring-column">
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
        if item_key == "evidence_table" and index == 1:
            rendered_cells.append(f'<td class="evidence-facts-cell">{_inline(cell)}</td>')
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
                f"{_inline(cell)}</span></td>"
            )
        else:
            rendered_cells.append(f"<td>{_inline(cell)}</td>")
    cell_html = "".join(rendered_cells)
    return f"          <tr>{cell_html}</tr>"


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


def _dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


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
      --text: #111827;
      --muted: #4b5563;
      --line: #9ca3af;
      --ink: #020617;
      --panel: #f8fafc;
      --blue: #356dff;
      --red: #e15b64;
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
      background: #e5e7eb;
    }
    body {
      margin: 0;
      font-family: Arial, "Noto Sans KR", "Noto Sans CJK KR", "Apple SD Gothic Neo", sans-serif;
      color: var(--text);
      font-size: 8.2pt;
      line-height: 1.23;
      background: #e5e7eb;
      word-break: keep-all;
    }
    .a4-sheet {
      width: 210mm;
      min-height: 297mm;
      margin: 0 auto;
      padding: 6mm 7mm;
      position: relative;
      background: #ffffff;
      overflow: visible;
    }
    .paper-grid {
      display: grid;
      grid-template-columns: minmax(0, 145mm) 44mm;
      column-gap: 7mm;
      align-items: start;
      min-height: 281.5mm;
    }
    .main-column,
    .visual-sidebar {
      min-width: 0;
    }
    .report-section {
      margin: 0 0 2mm;
      break-inside: avoid;
      page-break-inside: avoid;
    }
    .document-header {
      margin: 0 0 2.6mm;
      padding: 0;
    }
    .report-name {
      margin: 0 0 1.2mm;
      color: var(--ink);
      font-size: 12.6pt;
      line-height: 1.08;
      letter-spacing: 0;
      font-weight: 800;
    }
    h1 {
      margin: 0 0 1.2mm;
      color: var(--ink);
      font-size: 10pt;
      line-height: 1.1;
      letter-spacing: 0;
      font-weight: 800;
    }
    .main-section h1 {
      padding-bottom: 0.8mm;
      border-bottom: 1pt solid var(--ink);
    }
    h2 {
      margin: 0 0 0.7mm;
      color: var(--muted);
      font-size: 6.6pt;
      line-height: 1.1;
      letter-spacing: 0;
      font-weight: 700;
    }
    .report-section > h2 {
      display: block;
    }
    p {
      margin: 0 0 1.4mm;
      text-align: justify;
    }
    ul {
      margin: 0 0 1.8mm;
      padding-left: 3.6mm;
    }
    li {
      margin: 0 0 0.9mm;
    }
    strong {
      color: var(--ink);
      font-weight: 800;
    }
    .meta-grid {
      display: grid;
      grid-template-columns: repeat(2, minmax(0, 1fr));
      gap: 1.2mm;
    }
    .meta-grid div {
      padding: 0;
      border: 0;
      background: transparent;
    }
    .meta-grid span {
      display: inline;
      color: var(--muted);
      font-size: 6.1pt;
      line-height: 1.05;
      text-transform: uppercase;
    }
    .meta-grid span::after {
      content: ": ";
    }
    table {
      width: 100%;
      border-collapse: collapse;
      margin: 0.8mm 0 2mm;
      background: #ffffff;
      font-size: 5.8pt;
      line-height: 1.18;
      table-layout: fixed;
      word-break: break-word;
      border-top: 1.2pt solid var(--ink);
      border-bottom: 1.2pt solid var(--ink);
    }
    th,
    td {
      border: 0;
      border-bottom: 0.35pt solid #d1d5db;
      padding: 0.62mm 0.7mm;
      vertical-align: top;
      text-align: left;
    }
    th {
      background: #ffffff;
      color: var(--ink);
      font-weight: 800;
    }
    .key-evidence-columns .evidence-axis-column {
      width: 16%;
    }
    .key-evidence-columns .evidence-observation-column {
      width: 42%;
    }
    .key-evidence-columns .evidence-interpretation-column {
      width: 32%;
    }
    .key-evidence-columns .evidence-impact-column {
      width: 10%;
    }
    .evidence-impact-cell {
      text-align: center;
    }
    .evidence-facts-cell {
      white-space: pre-line;
    }
    .impact-badge {
      display: inline-block;
      min-width: 12mm;
      padding: 0.45mm 0.6mm;
      border-radius: 2px;
      font-weight: 800;
      line-height: 1.05;
      text-align: center;
    }
    .impact-positive {
      color: #166534;
      background: #dcfce7;
    }
    .impact-negative {
      color: #991b1b;
      background: #fee2e2;
    }
    .impact-mixed {
      color: #854d0e;
      background: #fef3c7;
    }
    .impact-neutral,
    .impact-reference {
      color: #374151;
      background: #f3f4f6;
    }
    .risk-monitoring-columns .risk-title-column {
      width: 18%;
    }
    .risk-monitoring-columns .risk-current-column {
      width: 47%;
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
      color: #6b7280;
      font-size: 4.4pt;
      font-weight: 400;
      line-height: 1.2;
      text-align: center;
      white-space: nowrap;
    }
    .sidebar-summary {
      margin: 0 0 3mm;
      padding-bottom: 1.2mm;
      border-bottom: 1.5pt solid var(--ink);
    }
    .sidebar-brand {
      margin: 0 0 1mm;
      color: var(--ink);
      font-size: 9.2pt;
      line-height: 1.1;
      font-weight: 800;
    }
    .sidebar-summary dl {
      margin: 0;
    }
    .sidebar-summary div {
      display: grid;
      grid-template-columns: 19mm minmax(0, 1fr);
      gap: 1mm;
      margin-bottom: 0.65mm;
      font-size: 6.2pt;
      line-height: 1.15;
    }
    .sidebar-summary dt {
      color: var(--muted);
      font-weight: 700;
    }
    .sidebar-summary dd {
      margin: 0;
      color: var(--ink);
      font-weight: 800;
      text-align: right;
    }
    .sidebar-panel {
      margin: 0 0 3mm;
      break-inside: avoid;
    }
    .sidebar-panel h2 {
      display: block;
      margin: 0 0 1.2mm;
      padding-bottom: 0.6mm;
      border-bottom: 1.2pt solid var(--ink);
      color: var(--ink);
      font-size: 9pt;
      font-weight: 800;
      line-height: 1.1;
      text-align: left;
    }
    .key-data-panel dl {
      margin: 0;
    }
    .key-data-panel div {
      display: grid;
      grid-template-columns: 24mm minmax(0, 1fr);
      gap: 1mm;
      margin-bottom: 0.85mm;
      font-size: 6.4pt;
      line-height: 1.15;
    }
    .key-data-panel dt {
      color: var(--muted);
      font-weight: 700;
    }
    .key-data-panel dd {
      margin: 0;
      color: var(--ink);
      font-weight: 800;
      text-align: right;
    }
    .signal-group {
      margin: 0 0 2mm;
    }
    .signal-group h3 {
      margin: 0 0 0.8mm;
      color: var(--muted);
      font-size: 6.4pt;
      line-height: 1.1;
    }
    .signal-group ul {
      margin: 0;
      padding: 0;
      list-style: none;
    }
    .signal-group li {
      margin: 0 0 0.65mm;
      padding-left: 2.2mm;
      position: relative;
      font-size: 6.4pt;
      line-height: 1.15;
    }
    .signal-group li::before {
      content: "";
      position: absolute;
      left: 0;
      top: 0.45em;
      width: 1.1mm;
      height: 1.1mm;
      background: var(--ink);
    }
    .report-chart-section {
      margin: 2.5mm 0 0;
      break-inside: avoid;
      page-break-inside: avoid;
    }
    .report-chart-section > h1 {
      margin-bottom: 1.5mm;
      padding-bottom: 0.8mm;
      border-bottom: 1pt solid var(--ink);
    }
    .report-chart-grid {
      display: grid;
      grid-template-columns: repeat(2, minmax(0, 1fr));
      gap: 3mm;
      align-items: start;
    }
    .report-chart {
      margin: 0;
      min-width: 0;
    }
    .report-chart img {
      display: block;
      width: 100%;
      height: auto;
      border: 0.35pt solid #d1d5db;
    }
    .report-chart figcaption {
      margin-top: 0.7mm;
      color: var(--ink);
      font-size: 6pt;
      line-height: 1.22;
      text-align: left;
    }
    .chart-caption-title,
    .chart-observation,
    .chart-interpretation {
      display: block;
    }
    .chart-caption-title {
      margin-bottom: 0.45mm;
      font-size: 6.2pt;
    }
    .chart-observation {
      color: var(--ink);
    }
    .chart-interpretation {
      margin-top: 0.35mm;
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
        background: #ffffff;
        overflow: visible;
      }
      .a4-sheet {
        width: 210mm;
        height: 594mm;
        min-height: 594mm;
        max-height: none;
        margin: 0 !important;
        padding: 6mm 7mm;
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
      p {
        margin-bottom: 1.2mm;
      }
      .report-section {
        margin-bottom: 1.8mm;
      }
      #data-limits p {
        font-size: 7.8pt;
        line-height: 1.16;
      }
      .report-chart-grid {
        grid-template-columns: 1fr;
        gap: 4mm;
      }
      .report-chart img {
        width: 100%;
        max-height: 112mm;
        object-fit: contain;
      }
      .report-chart figcaption {
        font-size: 6pt;
      }
      .report-section,
      .sidebar-panel {
        break-inside: auto;
        page-break-inside: auto;
      }
      .report-disclaimer {
        position: absolute;
        right: 7mm;
        bottom: 2.2mm;
        left: 7mm;
        margin: 0;
        white-space: nowrap;
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
      .meta-grid {
        grid-template-columns: 1fr;
      }
      .report-chart-grid {
        grid-template-columns: 1fr;
      }
    }"""
