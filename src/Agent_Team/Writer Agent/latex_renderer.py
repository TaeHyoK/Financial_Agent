"""Render LaTeX artifacts and fallback PDF for Writer Agent outputs."""

from __future__ import annotations

import math
import shutil
import subprocess
from pathlib import Path
from typing import Any

import fitz
from PIL import Image

from data_loader import write_text


REPORT_NAVY = (11 / 255, 31 / 255, 58 / 255)
REPORT_BLUE = (31 / 255, 90 / 255, 166 / 255)
REPORT_LIGHT_BLUE = (234 / 255, 242 / 255, 255 / 255)
REPORT_GRAY = (244 / 255, 246 / 255, 248 / 255)
REPORT_DARK_GRAY = (74 / 255, 85 / 255, 104 / 255)
REPORT_TEXT = (26 / 255, 32 / 255, 44 / 255)
REPORT_RISK_RED = (180 / 255, 35 / 255, 24 / 255)
REPORT_LIGHT_RED = (255 / 255, 241 / 255, 240 / 255)
REPORT_GREEN = (6 / 255, 118 / 255, 71 / 255)
WHITE = (1, 1, 1)


def render_writer_outputs(
    contract: dict[str, Any],
    output_dir: str | Path,
    *,
    include_source_trace: bool = False,
) -> dict[str, Any]:
    """Render section tex files, main.tex, and final_report.pdf."""

    output_dir = Path(output_dir).expanduser().resolve()
    sections_dir = output_dir / "sections"
    sections_dir.mkdir(parents=True, exist_ok=True)
    main_tex = build_main_tex(contract)
    write_text(output_dir / "main.tex", main_tex)
    for filename, content in build_section_tex_files(contract).items():
        write_text(sections_dir / filename, content)

    latex_status, latex_notes = _try_latex_compile(output_dir)
    final_pdf = output_dir / "final_report.pdf"
    if latex_status != "pass" or not final_pdf.exists():
        build_fallback_pdf(contract, final_pdf, include_source_trace=include_source_trace)
    render_pdf_previews(final_pdf, output_dir / "render_preview")
    return {
        "main_tex": main_tex,
        "latex_compile_status": latex_status,
        "latex_notes": latex_notes,
        "final_pdf": str(final_pdf),
    }


def build_main_tex(contract: dict[str, Any]) -> str:
    """Build a XeLaTeX-friendly main.tex representation."""

    metadata = contract["report_metadata"]
    blocks = contract.get("visual_report_blocks", [])
    figure_tex = "\n".join(_figure_tex(block) for block in blocks)
    return rf"""\documentclass[10pt,a4paper]{{article}}
\usepackage[a4paper,margin=16mm]{{geometry}}
\usepackage{{kotex}}
\usepackage{{fontspec}}
\usepackage{{graphicx}}
\usepackage{{xcolor}}
\usepackage{{booktabs}}
\usepackage{{tabularx}}
\usepackage{{tcolorbox}}
\usepackage{{fancyhdr}}
\usepackage{{titlesec}}
\definecolor{{ReportNavy}}{{HTML}}{{0B1F3A}}
\definecolor{{ReportBlue}}{{HTML}}{{1F5AA6}}
\definecolor{{ReportLightBlue}}{{HTML}}{{EAF2FF}}
\definecolor{{ReportGray}}{{HTML}}{{F4F6F8}}
\definecolor{{ReportDarkGray}}{{HTML}}{{4A5568}}
\definecolor{{ReportRiskRed}}{{HTML}}{{B42318}}
\setmainfont{{Noto Sans CJK KR}}
\pagestyle{{fancy}}
\fancyhf{{}}
\fancyhead[L]{{{_tex_escape(metadata['company_name'])} Equity Research Draft}}
\fancyhead[R]{{{_tex_escape(metadata['base_date'])}}}
\fancyfoot[L]{{AI-generated research draft}}
\fancyfoot[C]{{\thepage}}
\fancyfoot[R]{{Internal draft}}
\titleformat{{\section}}{{\large\bfseries\color{{ReportNavy}}}}{{\thesection}}{{0.5em}}{{}}
\begin{{document}}
\section*{{{_tex_escape(metadata['company_name'])} Equity Research Draft}}
\begin{{tcolorbox}}[colback=ReportLightBlue,colframe=ReportBlue,title=Recommendation Card]
\textbf{{Recommendation:}} {_tex_escape(metadata['recommendation'])} \quad
\textbf{{Target Price:}} {_tex_escape(metadata['target_price'])} \quad
\textbf{{Valuation Status:}} {_tex_escape(metadata['valuation_status'])}
\end{{tcolorbox}}
\input{{sections/cover_summary.tex}}
\input{{sections/key_charts.tex}}
{figure_tex}
\input{{sections/financial_view.tex}}
\input{{sections/market_price_view.tex}}
\input{{sections/catalyst_risk.tex}}
\input{{sections/peer_positioning.tex}}
\input{{sections/appendix.tex}}
\end{{document}}
"""


def build_section_tex_files(contract: dict[str, Any]) -> dict[str, str]:
    sections = contract.get("sections", {})
    cover = contract.get("cover_summary", {})
    metrics = contract.get("key_metrics_table", {}).get("metrics", [])
    limitations = contract.get("limitations", {})
    return {
        "cover_summary.tex": _cover_tex(cover, metrics),
        "key_charts.tex": "\\section*{Key Charts}\n핵심 차트는 Strategy Agent 필드와 직접 연결된 승인 차트만 포함한다.\n",
        "financial_view.tex": _section_tex(sections.get("financial_view", {})),
        "market_price_view.tex": _section_tex(sections.get("market_price_view", {})),
        "catalyst_risk.tex": _catalyst_risk_tex(sections.get("catalyst_and_risk", {})),
        "peer_positioning.tex": _peer_tex(sections.get("peer_positioning", {})),
        "appendix.tex": _appendix_tex(sections.get("final_rationale", {}), limitations),
    }


def build_fallback_pdf(
    contract: dict[str, Any],
    output_pdf: str | Path,
    *,
    include_source_trace: bool = False,
) -> None:
    """Generate final_report.pdf using PyMuPDF when LaTeX is unavailable."""

    output_pdf = Path(output_pdf).expanduser().resolve()
    output_pdf.parent.mkdir(parents=True, exist_ok=True)
    doc = fitz.open()
    _draw_cover_page(doc, contract)
    _draw_chart_pages(doc, contract)
    _draw_financial_market_page(doc, contract)
    _draw_catalyst_risk_page(doc, contract)
    _draw_final_appendix_page(doc, contract)
    if include_source_trace:
        _draw_source_trace_page(doc, contract)
    doc.save(output_pdf)
    doc.close()


def render_pdf_previews(pdf_path: str | Path, preview_dir: str | Path) -> list[str]:
    pdf_path = Path(pdf_path).expanduser().resolve()
    preview_dir = Path(preview_dir).expanduser().resolve()
    preview_dir.mkdir(parents=True, exist_ok=True)
    doc = fitz.open(pdf_path)
    paths: list[str] = []
    for index, page in enumerate(doc):
        pix = page.get_pixmap(matrix=fitz.Matrix(1.5, 1.5), alpha=False)
        out = preview_dir / f"page_{index + 1}.png"
        pix.save(out)
        paths.append(str(out))
    doc.close()
    return paths


def _try_latex_compile(output_dir: Path) -> tuple[str, list[str]]:
    notes: list[str] = []
    latexmk = shutil.which("latexmk")
    xelatex = shutil.which("xelatex")
    if not latexmk or not xelatex:
        note = "latexmk/xelatex not available in this environment; skipped LaTeX compile."
        write_text(output_dir / "compile_log.txt", note + "\nGenerated final_report.pdf with PyMuPDF fallback renderer.\n")
        return "skipped", [note]
    command = [latexmk, "-xelatex", "-interaction=nonstopmode", "-halt-on-error", "main.tex"]
    result = subprocess.run(command, cwd=output_dir, capture_output=True, text=True, timeout=120)
    log = result.stdout + "\n" + result.stderr
    write_text(output_dir / "compile_log.txt", log)
    if result.returncode == 0:
        return "pass", notes
    notes.append("LaTeX compile failed; generated final_report.pdf with PyMuPDF fallback renderer.")
    return "fail", notes


def _draw_cover_page(doc: fitz.Document, contract: dict[str, Any]) -> None:
    page = _new_page(doc, contract, "Investment Summary")
    meta = contract["report_metadata"]
    cover = contract["cover_summary"]
    y = 74
    _text(page, 40, y, meta["company_name"], 22, REPORT_NAVY)
    _text(page, 40, y + 26, "Equity Research Draft", 11, REPORT_NAVY)
    _text(page, 40, y + 44, f"Base Date: {meta['base_date']} | Report Type: {meta['report_type']}", 8.5, REPORT_DARK_GRAY)
    _box(page, 40, y + 55, 515, 78, REPORT_NAVY, REPORT_NAVY)
    _text(page, 56, y + 72, "Recommendation", 10, WHITE)
    _text(page, 56, y + 94, meta["recommendation"], 28, WHITE)
    _text(page, 235, y + 75, "Target Price", 10, WHITE)
    _text(page, 235, y + 96, meta["target_price"], 18, WHITE)
    _text(page, 350, y + 75, "Valuation Status", 10, WHITE)
    _text_block(page, fitz.Rect(350, y + 88, 540, y + 118), meta["valuation_status"], 8.2, WHITE)

    _section_box(page, 40, y + 150, 515, 95, "Summary", cover["one_line_view"], REPORT_LIGHT_BLUE, REPORT_BLUE)
    _text_block(
        page,
        fitz.Rect(54, y + 191, 540, y + 232),
        _truncate_text(cover["recommendation_rationale_short"], 170),
        7.4,
        REPORT_TEXT,
    )
    _signals_box(page, 40, y + 265, 245, 145, "Positive Signals", cover.get("positive_signals", []), REPORT_LIGHT_BLUE, REPORT_BLUE)
    _signals_box(page, 310, y + 265, 245, 145, "Risk Signals", cover.get("negative_signals", []), REPORT_LIGHT_RED, REPORT_RISK_RED)
    _metrics_table(page, 40, y + 435, 515, contract["key_metrics_table"]["metrics"])
    _note_box(page, 40, y + 650, 515, 72, contract["key_metrics_table"]["note"])


def _draw_chart_pages(doc: fitz.Document, contract: dict[str, Any]) -> None:
    for index, block in enumerate(contract.get("visual_report_blocks", [])[:2], start=1):
        _draw_single_chart_page(doc, contract, block, index)


def _draw_single_chart_page(doc: fitz.Document, contract: dict[str, Any], block: dict[str, Any], index: int) -> None:
    page = _new_page(doc, contract, f"Key Chart {index}")
    y = 70
    _text(page, 40, y, f"Key Chart {index}", 15, REPORT_NAVY)
    y += 24
    _text_block(page, fitz.Rect(40, y, 555, y + 28), _chart_title(block), 12.5, REPORT_BLUE)
    y += 36
    y = _image(page, block["figure_path"], 40, y, 515, 342)
    y += 10
    _text_block(page, fitz.Rect(40, y, 555, y + 38), block["caption"], 7.5, REPORT_DARK_GRAY)
    y += 46
    _section_box(page, 40, y, 515, 108, "Analyst Takeaway", block["analyst_takeaway"], REPORT_LIGHT_BLUE, REPORT_BLUE, title_size=8.8, body_size=7.2)
    y += 122
    _note_box(page, 40, y, 515, 52, block["interpretation_limit"])


def _draw_financial_market_page(doc: fitz.Document, contract: dict[str, Any]) -> None:
    page = _new_page(doc, contract, "Financial / Market View")
    sections = contract["sections"]
    y = 72
    _text(page, 40, y, "Financial View", 16, REPORT_NAVY)
    y += 20
    fin = sections["financial_view"]
    for key in ["revenue", "profitability", "cash_flow", "balance_sheet"]:
        y = _paragraph_item(page, 40, y, key.replace("_", " ").title(), fin["subsections"].get(key, ""), 515)
    _note_box(page, 40, y + 8, 515, 48, fin["subsections"].get("basis_note", ""))
    y += 78
    _text(page, 40, y, "Market / Price View", 16, REPORT_NAVY)
    y += 20
    market = sections["market_price_view"]
    for key in ["price_trend", "volume", "relative_strength", "market_interpretation"]:
        y = _paragraph_item(page, 40, y, key.replace("_", " ").title(), market["subsections"].get(key, ""), 515)
    _note_box(page, 40, y + 8, 515, 48, market["subsections"].get("causality_note", ""))


def _draw_catalyst_risk_page(doc: fitz.Document, contract: dict[str, Any]) -> None:
    page = _new_page(doc, contract, "Catalyst & Risk")
    section = contract["sections"]["catalyst_and_risk"]
    y = 72
    _text(page, 40, y, "Catalyst & Risk", 16, REPORT_NAVY)
    _signals_box(page, 40, y + 20, 245, 180, "Catalysts", section.get("positive_catalysts", [])[:4], REPORT_LIGHT_BLUE, REPORT_BLUE)
    _signals_box(page, 310, y + 20, 245, 180, "Business Expansion", section.get("business_expansion", [])[:4], REPORT_GRAY, REPORT_BLUE)
    y += 230
    _text(page, 40, y, "Risk Matrix", 15, REPORT_NAVY)
    risk_blocks = section.get("risk_blocks", {})
    labels = [
        ("Financial Risk", risk_blocks.get("financial_risks", [])),
        ("Regulatory Risk", risk_blocks.get("regulatory_risks", [])),
        ("Market Risk", risk_blocks.get("market_risks", [])),
        ("Execution Risk", risk_blocks.get("execution_risks", [])),
    ]
    positions = [(40, y + 22), (310, y + 22), (40, y + 178), (310, y + 178)]
    for (label, items), (x, box_y) in zip(labels, positions):
        _signals_box(page, x, box_y, 245, 132, label, items[:3], REPORT_LIGHT_RED, REPORT_RISK_RED, body_size=7.2)
    y += 340
    peer = contract["sections"]["peer_positioning"]
    _section_box(page, 40, y, 515, 108, "Peer / Competitor Positioning", peer.get("body", ""), REPORT_GRAY, REPORT_BLUE, title_size=10, body_size=7.8)


def _draw_final_appendix_page(doc: fitz.Document, contract: dict[str, Any]) -> None:
    page = _new_page(doc, contract, "Final Rationale / Appendix")
    y = 72
    final = contract["sections"]["final_rationale"]
    recommendation = contract.get("report_metadata", {}).get("recommendation", "Investment Decision")
    _section_box(page, 40, y, 515, 150, f"Final Rationale - Why {recommendation}?", final.get("body", ""), REPORT_LIGHT_BLUE, REPORT_BLUE, body_size=8)
    y += 178
    limitations = contract["limitations"]
    _text(page, 40, y, "Limitations & Monitoring", 16, REPORT_NAVY)
    y += 22
    for title, items in [
        ("Data Limitations", limitations.get("data_limitations", [])[:3]),
        ("Interpretation Limitations", limitations.get("interpretation_limitations", [])[:4]),
        ("Monitoring Points", limitations.get("monitoring_points", [])[:4]),
    ]:
        _signals_box(page, 40, y, 515, 130, title, items, REPORT_GRAY, REPORT_DARK_GRAY, body_size=7.0)
        y += 144


def _draw_source_trace_page(doc: fitz.Document, contract: dict[str, Any]) -> None:
    page = _new_page(doc, contract, "Source Trace")
    y = 72
    _text(page, 40, y, "Source Trace Summary", 13, REPORT_NAVY)
    y += 16
    _text_block(
        page,
        fitz.Rect(40, y, 555, y + 36),
        "Writer Agent가 사용한 문장과 차트의 근거 파일 및 필드다. 신규 분석이나 임의 수치는 포함하지 않는다.",
        7.5,
        REPORT_DARK_GRAY,
    )
    y += 48
    for entry in contract.get("source_trace", [])[:18]:
        trace_line = f"- {entry['used_in_section']}: {entry['source_file']} / {entry['source_field']}"
        _text_block(page, fitz.Rect(42, y, 552, y + 22), trace_line, 6.5, REPORT_DARK_GRAY)
        y += 22


def _new_page(doc: fitz.Document, contract: dict[str, Any], title: str) -> fitz.Page:
    page = doc.new_page(width=595, height=842)
    meta = contract["report_metadata"]
    _text(page, 40, 28, f"{meta['company_name']} Equity Research Draft", 8, REPORT_DARK_GRAY)
    _text(page, 455, 28, meta["base_date"], 8, REPORT_DARK_GRAY)
    page.draw_line((40, 42), (555, 42), color=REPORT_NAVY, width=0.8)
    _text(page, 40, 815, "AI-generated research draft", 7, REPORT_DARK_GRAY)
    _text(page, 270, 815, str(len(doc)), 7, REPORT_DARK_GRAY)
    _text(page, 470, 815, "Internal draft", 7, REPORT_DARK_GRAY)
    return page


def _text(page: fitz.Page, x: float, y: float, text: str, size: float, color=REPORT_TEXT) -> None:
    page.insert_text((x, y), text, fontsize=size, fontname="korea", color=color)


def _text_block(page: fitz.Page, rect: fitz.Rect, text: str, size: float, color=REPORT_TEXT) -> None:
    cleaned = _clean(text)
    current_size = size
    while current_size >= 5.6:
        spare_height = page.insert_textbox(
            rect,
            cleaned,
            fontsize=current_size,
            fontname="korea",
            color=color,
            align=fitz.TEXT_ALIGN_LEFT,
        )
        if spare_height >= 0:
            return
        current_size -= 0.4
    page.insert_textbox(
        rect,
        _truncate_text(cleaned, 140),
        fontsize=5.6,
        fontname="korea",
        color=color,
        align=fitz.TEXT_ALIGN_LEFT,
    )


def _box(page: fitz.Page, x: float, y: float, w: float, h: float, fill, stroke=REPORT_GRAY) -> None:
    page.draw_rect(fitz.Rect(x, y, x + w, y + h), color=stroke, fill=fill, width=0.8)


def _section_box(page: fitz.Page, x: float, y: float, w: float, h: float, title: str, body: str, fill, stroke, title_size=10, body_size=8) -> None:
    _box(page, x, y, w, h, fill, stroke)
    _text(page, x + 10, y + 18, title, title_size, stroke)
    _text_block(page, fitz.Rect(x + 10, y + 26, x + w - 10, y + h - 8), body, body_size, REPORT_TEXT)


def _signals_box(page: fitz.Page, x: float, y: float, w: float, h: float, title: str, items: list[str], fill, stroke, body_size=7.6) -> None:
    _box(page, x, y, w, h, fill, stroke)
    _text(page, x + 10, y + 18, title, 10, stroke)
    y_cursor = y + 32
    for item in items[:4]:
        _text_block(page, fitz.Rect(x + 12, y_cursor, x + w - 10, y_cursor + 25), f"- {item}", body_size, REPORT_TEXT)
        y_cursor += 26


def _note_box(page: fitz.Page, x: float, y: float, w: float, h: float, text: str) -> None:
    _section_box(page, x, y, w, h, "Note", text, REPORT_GRAY, REPORT_DARK_GRAY, title_size=8.5, body_size=7.5)


def _metrics_table(page: fitz.Page, x: float, y: float, w: float, metrics: list[dict[str, str]]) -> None:
    _text(page, x, y, "Key Metrics", 14, REPORT_NAVY)
    y += 14
    col_widths = [110, 95, 100, 210]
    headers = ["Metric", "Period", "Value", "Interpretation"]
    _box(page, x, y, w, 22, REPORT_NAVY, REPORT_NAVY)
    x_cursor = x + 8
    for header, width in zip(headers, col_widths):
        _text(page, x_cursor, y + 15, header, 8, WHITE)
        x_cursor += width
    y += 22
    for metric in metrics:
        _box(page, x, y, w, 32, WHITE, REPORT_GRAY)
        x_cursor = x + 8
        for key, width in zip(["metric_name", "period", "value", "interpretation"], col_widths):
            _text_block(page, fitz.Rect(x_cursor, y + 6, x_cursor + width - 8, y + 28), metric.get(key, ""), 7.4, REPORT_TEXT)
            x_cursor += width
        y += 32


def _paragraph_item(page: fitz.Page, x: float, y: float, label: str, body: str, w: float) -> float:
    label_map = {
        "Price Trend": "Price",
        "Relative Strength": "Relative Strength",
        "Market Interpretation": "Market View",
        "Balance Sheet": "Balance Sheet",
    }
    _text_block(page, fitz.Rect(x, y - 10, x + 118, y + 12), label_map.get(label, label), 8.4, REPORT_BLUE)
    _text_block(page, fitz.Rect(x + 120, y - 10, x + w, y + 40), body, 7.5, REPORT_TEXT)
    return y + 50


def _image(page: fitz.Page, path: str, x: float, y: float, max_w: float, max_h: float) -> float:
    image_path = Path(path)
    if image_path.suffix.lower() == ".pdf":
        chart_doc = fitz.open(image_path)
        chart_page = chart_doc[0]
        width = chart_page.rect.width
        height = chart_page.rect.height
        scale = min(max_w / width, max_h / height)
        draw_w = width * scale
        draw_h = height * scale
        pix = chart_page.get_pixmap(matrix=fitz.Matrix(2, 2), alpha=False)
        page.insert_image(fitz.Rect(x, y, x + draw_w, y + draw_h), stream=pix.tobytes("png"))
        chart_doc.close()
        return y + draw_h
    with Image.open(image_path) as img:
        width, height = img.size
    scale = min(max_w / width, max_h / height)
    draw_w = width * scale
    draw_h = height * scale
    page.insert_image(fitz.Rect(x, y, x + draw_w, y + draw_h), filename=str(image_path))
    return y + draw_h


def _chart_title(block: dict[str, Any]) -> str:
    return block.get("display_title") or block.get("figure_title") or block.get("figure_id") or "Key Chart"


def _cover_tex(cover: dict[str, Any], metrics: list[dict[str, str]]) -> str:
    metric_rows = "\n".join(
        f"{_tex_escape(m['metric_name'])} & {_tex_escape(m['period'])} & {_tex_escape(m['value'])} & {_tex_escape(m['interpretation'])} \\\\"
        for m in metrics
    )
    positives = "\n".join(f"\\item {_tex_escape(item)}" for item in cover.get("positive_signals", [])[:4])
    risks = "\n".join(f"\\item {_tex_escape(item)}" for item in cover.get("negative_signals", [])[:4])
    return rf"""\section*{{Investment Summary}}
\begin{{tcolorbox}}[colback=ReportLightBlue,colframe=ReportBlue,title=Summary Box]
{_tex_escape(cover.get('one_line_view', ''))}
\end{{tcolorbox}}
\begin{{minipage}}{{0.48\textwidth}}
\begin{{tcolorbox}}[colback=ReportLightBlue,colframe=ReportBlue,title=Positive Signals]
\begin{{itemize}}{positives}\end{{itemize}}
\end{{tcolorbox}}
\end{{minipage}}\hfill
\begin{{minipage}}{{0.48\textwidth}}
\begin{{tcolorbox}}[colback=ReportLightRed,colframe=ReportRiskRed,title=Risk Signals]
\begin{{itemize}}{risks}\end{{itemize}}
\end{{tcolorbox}}
\end{{minipage}}
\begin{{tabularx}}{{\textwidth}}{{llll}}
\toprule
Metric & Period & Value & Interpretation \\
\midrule
{metric_rows}
\bottomrule
\end{{tabularx}}
"""


def _section_tex(section: dict[str, Any]) -> str:
    subsections = section.get("subsections", {})
    lines = [f"\\section*{{{_tex_escape(section.get('title', ''))}}}", _tex_escape(section.get("body", ""))]
    for key, value in subsections.items():
        lines.append(f"\\paragraph{{{_tex_escape(key.replace('_', ' ').title())}}} {_tex_escape(value)}")
    return "\n".join(lines)


def _catalyst_risk_tex(section: dict[str, Any]) -> str:
    risks = section.get("risk_blocks", {})
    return "\n".join(
        [
            "\\section*{Catalyst \\& Risk}",
            _itemize("Positive Catalysts", section.get("positive_catalysts", [])),
            _itemize("Business Expansion", section.get("business_expansion", [])),
            _itemize("Financial Risk", risks.get("financial_risks", [])),
            _itemize("Regulatory Risk", risks.get("regulatory_risks", [])),
            _itemize("Market Risk", risks.get("market_risks", [])),
            _itemize("Execution Risk", risks.get("execution_risks", [])),
        ]
    )


def _peer_tex(section: dict[str, Any]) -> str:
    return "\n".join(
        [
            "\\section*{Peer / Competitor Positioning}",
            _tex_escape(section.get("body", "")),
            _itemize("Target Relative Strength", section.get("target_relative_strength", [])),
            _itemize("Target Relative Weakness", section.get("target_relative_weakness", [])),
        ]
    )


def _appendix_tex(final: dict[str, Any], limitations: dict[str, list[str]]) -> str:
    return "\n".join(
        [
            "\\section*{Final Rationale}",
            _tex_escape(final.get("body", "")),
            "\\section*{Appendix}",
            _itemize("Data Limitations", limitations.get("data_limitations", [])),
            _itemize("Interpretation Limitations", limitations.get("interpretation_limitations", [])),
            _itemize("Monitoring Points", limitations.get("monitoring_points", [])),
        ]
    )


def _figure_tex(block: dict[str, Any]) -> str:
    return rf"""\begin{{figure}}[htbp]
\centering
\includegraphics[width=0.95\linewidth]{{{_tex_escape(block['figure_path'])}}}
\caption{{{_tex_escape(block['caption'])}}}
\end{{figure}}
\begin{{tcolorbox}}[colback=ReportLightBlue,colframe=ReportBlue,title=Analyst Takeaway]
{_tex_escape(block['analyst_takeaway'])}
\end{{tcolorbox}}
{{\footnotesize \textcolor{{ReportDarkGray}}{{Note: {_tex_escape(block['interpretation_limit'])}}}}}
"""


def _itemize(title: str, items: list[str]) -> str:
    body = "\n".join(f"\\item {_tex_escape(item)}" for item in items[:6])
    return f"\\paragraph{{{_tex_escape(title)}}}\n\\begin{{itemize}}\n{body}\n\\end{{itemize}}"


def _tex_escape(text: Any) -> str:
    text = str(text)
    replacements = {
        "\\": "\\textbackslash{}",
        "&": "\\&",
        "%": "\\%",
        "$": "\\$",
        "#": "\\#",
        "_": "\\_",
        "{": "\\{",
        "}": "\\}",
        "~": "\\textasciitilde{}",
        "^": "\\textasciicircum{}",
    }
    for old, new in replacements.items():
        text = text.replace(old, new)
    return text


def _clean(text: Any) -> str:
    return " ".join(str(text).split())


def _truncate_text(text: str, max_chars: int) -> str:
    if len(text) <= max_chars:
        return text
    return text[: max_chars - 1].rstrip() + "..."
