"""Render Writer Agent broker report contract to an HTML preview."""

from __future__ import annotations

import base64
import mimetypes
import shutil
import zipfile
from html import escape
from pathlib import Path
from typing import Any

from data_loader import write_text


AGENT_DIR = Path(__file__).resolve().parent
TEMPLATE_PATH = AGENT_DIR / "templates" / "broker_report_preview.html.j2"
CSS_PATH = AGENT_DIR / "templates" / "assets" / "report_style.css"


def render_html_preview(
    contract: dict[str, Any],
    output_dir: str | Path,
    *,
    include_source_trace: bool = False,
    embed_images: bool = False,
) -> dict[str, str]:
    """Render final_report_preview.html from broker_report_contract_v1.json."""

    output_dir = Path(output_dir).expanduser().resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    template = TEMPLATE_PATH.read_text(encoding="utf-8")
    css = CSS_PATH.read_text(encoding="utf-8")
    title = _title(contract)
    body = _body(contract, output_dir=output_dir, include_source_trace=include_source_trace, embed_images=embed_images)
    html = template.replace("{{ title }}", escape(title)).replace("{{ css }}", css).replace("{{ body }}", body)
    output_path = output_dir / "final_report_preview.html"
    write_text(output_path, html)
    report_path = output_dir / "report.html"
    write_text(report_path, html)
    zip_path = _write_with_figure_zip(output_dir, report_path)
    return {
        "html_preview": str(output_path),
        "report_html": str(report_path),
        "with_figure_zip": str(zip_path),
        "html_content": html,
        "include_source_trace": str(include_source_trace).lower(),
        "embed_images": str(embed_images).lower(),
    }


def _write_with_figure_zip(output_dir: Path, report_path: Path) -> Path:
    zip_path = output_dir / "with_figure.zip"
    figures_dir = output_dir / "figures"
    with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.write(report_path, arcname="report.html")
        if figures_dir.exists():
            for path in sorted(figures_dir.rglob("*")):
                if path.is_file():
                    archive.write(path, arcname=str(path.relative_to(output_dir)))
    return zip_path


def _title(contract: dict[str, Any]) -> str:
    meta = contract["report_metadata"]
    return f"{meta['company_name']} 기업분석 리포트 초안"


def _body(contract: dict[str, Any], *, output_dir: Path, include_source_trace: bool, embed_images: bool) -> str:
    pages = [
        _investment_summary_page(contract),
        _key_charts_page(contract, output_dir=output_dir, embed_images=embed_images),
        _financial_market_page(contract),
        _peer_comparison_page(contract, output_dir=output_dir, embed_images=embed_images),
        _catalyst_risk_page(contract),
        _limitations_page(contract),
    ]
    if include_source_trace:
        pages.append(_source_trace_page(contract))
    return "\n".join(pages)


def _investment_summary_page(contract: dict[str, Any]) -> str:
    meta = contract["report_metadata"]
    cover = contract["cover_summary"]
    return f"""
<section class="report-page">
  {_header(meta, "투자 요약")}
  {_investment_dashboard(meta, cover, contract["key_metrics_table"]["metrics"])}
  {_executive_summary_block(cover.get("executive_summary", ""))}
  <div class="three-grid">
    {_summary_card("한 줄 요약", cover["one_line_view"])}
    {_summary_card("투자의견 근거", cover["recommendation_rationale"])}
    {_summary_card("핵심 투자 쟁점", cover["key_debate"])}
  </div>
  <h2>핵심 지표</h2>
  {_metrics_table(contract["key_metrics_table"]["metrics"])}
  <div class="section-grid">
    {_signal_card("긍정 요인", cover.get("positive_signals", []), "summary-card")}
    {_signal_card("경계 요인", cover.get("negative_signals", []), "risk-card")}
  </div>
  <div class="note-box">{_p(contract["key_metrics_table"]["note"])}</div>
</section>
"""


def _key_charts_page(contract: dict[str, Any], *, output_dir: Path, embed_images: bool) -> str:
    meta = contract["report_metadata"]
    blocks = "\n".join(
        _chart_block(block, output_dir=output_dir, embed_images=embed_images) for block in contract.get("visual_report_blocks", [])
    )
    return f"""
<section class="report-page">
  {_header(meta, "핵심 차트")}
  {blocks}
</section>
"""


def _financial_market_page(contract: dict[str, Any]) -> str:
    meta = contract["report_metadata"]
    reader = contract["reader_friendly_sections"]
    financial_cards = "\n".join(_analysis_card(card) for card in reader["financial_view_cards"])
    market_cards = "\n".join(_analysis_card(card) for card in reader["market_view_cards"])
    return f"""
<section class="report-page">
  {_header(meta, "재무 분석 및 주가/시장 해석")}
  <div class="section-grid">
    <div>
      <h2>재무 분석</h2>
      {financial_cards}
    </div>
    <div>
      <h2>주가 및 시장 해석</h2>
      {market_cards}
    </div>
  </div>
</section>
"""


def _peer_comparison_page(contract: dict[str, Any], *, output_dir: Path, embed_images: bool) -> str:
    peer = contract.get("peer_comparison", {})
    if not isinstance(peer, dict) or not peer.get("enabled"):
        return ""
    meta = contract["report_metadata"]
    chart_blocks = "\n".join(
        _chart_block(block, output_dir=output_dir, embed_images=embed_images)
        for block in peer.get("peer_chart_blocks", [])
    )
    analysis_cards = "\n".join(_peer_analysis_card(card) for card in peer.get("analysis_cards", []))
    return f"""
<section class="report-page">
  {_header(meta, "동종기업 비교")}
  {_peer_commentary_block(peer)}
  <h2>국내 Peer 정량 비교</h2>
  {_peer_metrics_table(peer.get("table_rows", []))}
  <h2>Peer 비교 차트</h2>
  {chart_blocks}
  <h2>상대 매력도 해석</h2>
  <div class="section-grid">{analysis_cards}</div>
  {_peer_positioning_block(peer.get("relative_positioning", {}))}
  {_peer_limitations_block(peer)}
</section>
"""


def _catalyst_risk_page(contract: dict[str, Any]) -> str:
    meta = contract["report_metadata"]
    reader = contract["reader_friendly_sections"]
    catalyst_cards = _catalyst_groups(reader.get("catalyst_analysis_cards", []))
    risk_cards = "\n".join(_risk_card(card) for card in reader["risk_cards"])
    final = reader["final_rationale"]
    return f"""
<section class="report-page">
  {_header(meta, "성장 촉매·리스크 및 최종 판단")}
  <h2>성장 촉매 분석</h2>
  {catalyst_cards}
  <h2>주요 리스크</h2>
  <div class="section-grid">{risk_cards}</div>
  <h2>최종 투자의견 근거</h2>
  {_final_rationale_card(final)}
  {_view_change_conditions(final.get("view_change_conditions", {}))}
</section>
"""


def _limitations_page(contract: dict[str, Any]) -> str:
    meta = contract["report_metadata"]
    limitations = contract["limitations"]
    return f"""
<section class="report-page">
  {_header(meta, "해석상 한계")}
  <div class="section-grid">
    {_signal_card("데이터 한계", limitations.get("data_limitations", [])[:4], "card")}
    {_signal_card("해석 한계", limitations.get("interpretation_limitations", [])[:4], "card")}
  </div>
  {_signal_card("모니터링 포인트", limitations.get("monitoring_points", [])[:3], "card")}
</section>
"""


def _peer_metrics_table(rows: list[dict[str, Any]]) -> str:
    body = "\n".join(_peer_metrics_row(row) for row in rows)
    return f"""
<table class="metrics-table">
  <thead>
    <tr>
      <th>구분</th><th>기업</th><th>매출</th><th>공헌이익률</th><th>판관비율</th><th>EPS</th>
      <th>영업현금흐름</th><th>유동비율</th><th>부채비율</th><th>20일 초과수익률</th><th>60일 상대강도</th>
    </tr>
  </thead>
  <tbody>{body}</tbody>
</table>
"""


def _peer_commentary_block(peer: dict[str, Any]) -> str:
    commentary = peer.get("peer_investment_commentary", "")
    summary = peer.get("relative_positioning_summary", "")
    if not commentary and not summary:
        return ""
    summary_block = _card_section("상대 위치 요약", summary)
    return f"""
<div class="card executive-summary">
  <h3>Peer 비교 코멘트</h3>
  <p>{_p(commentary)}</p>
  {summary_block}
</div>
"""


def _peer_metrics_row(row: dict[str, Any]) -> str:
    company = f"<strong>{_p(row.get('company_name', ''))}</strong>" if row.get("is_target") else _p(row.get("company_name", ""))
    return (
        "<tr>"
        f"<td>{_p(row.get('peer_group', ''))}</td>"
        f"<td>{company}</td>"
        f"<td>{_p(row.get('revenue', ''))}</td>"
        f"<td>{_p(row.get('contribution_margin', ''))}</td>"
        f"<td>{_p(row.get('sga_margin', ''))}</td>"
        f"<td>{_p(row.get('eps', ''))}</td>"
        f"<td>{_p(row.get('operating_cash_flow', ''))}</td>"
        f"<td>{_p(row.get('current_ratio', ''))}</td>"
        f"<td>{_p(row.get('debt_ratio', ''))}</td>"
        f"<td>{_p(row.get('excess_return_20d', ''))}</td>"
        f"<td>{_p(row.get('relative_strength_60d', ''))}</td>"
        "</tr>"
    )


def _peer_analysis_card(card: dict[str, Any]) -> str:
    return f"""
<div class="card summary-card">
  <h3>{_p(card.get("title", ""))}</h3>
  <p>{_p(card.get("body", ""))}</p>
</div>
"""


def _peer_positioning_block(positioning: dict[str, Any]) -> str:
    if not isinstance(positioning, dict):
        return ""
    items = [
        positioning.get("revenue_scale", ""),
        positioning.get("profitability", ""),
        positioning.get("financial_stability", ""),
        positioning.get("market_performance", ""),
        positioning.get("valuation", ""),
    ]
    items = [item for item in items if str(item).strip()]
    if not items:
        return ""
    return _signal_card("상대 위치 요약", items, "card")


def _peer_limitations_block(peer: dict[str, Any]) -> str:
    commentary = peer.get("peer_limitations_commentary", "")
    limitations = peer.get("limitations", [])[:4]
    intro = f"<div class=\"note-box\">{_p(commentary)}</div>" if commentary else ""
    return intro + _signal_card("해석상 한계", limitations, "card")


def _source_trace_page(contract: dict[str, Any]) -> str:
    meta = contract["report_metadata"]
    items = "\n".join(
        f"<li>{_p(entry.get('used_in_section', ''))}: {_p(entry.get('source_file', ''))} / {_p(entry.get('source_field', ''))}</li>"
        for entry in contract.get("source_trace", [])
    )
    return f"""
<section class="report-page debug-source-trace">
  {_header(meta, "Source Trace Summary")}
  <p>Writer Agent가 사용한 문장과 차트의 근거 파일 및 필드다.</p>
  <ul>{items}</ul>
</section>
"""


def _header(meta: dict[str, Any], page_title: str) -> str:
    report_type = meta.get("report_type_display") or "기업분석 리포트 초안"
    return f"""
<header class="report-header">
  <div class="report-title">
    <h1>{_p(meta["company_name"])}</h1>
    <p>{_p(page_title)}</p>
  </div>
  <div class="meta-block">
    <div>Base Date: {_p(meta["base_date"])}</div>
    <div>{_p(report_type)}</div>
  </div>
</header>
"""


def _metric_card(label: str, value: str) -> str:
    return f"""
<div class="metric-card">
  <div class="label">{_p(label)}</div>
  <div class="value">{_p(value)}</div>
</div>
"""


def _investment_dashboard(meta: dict[str, Any], cover: dict[str, Any], metrics: list[dict[str, Any]]) -> str:
    positives = cover.get("positive_signals", [])
    negatives = cover.get("negative_signals", [])
    metric_items = _dashboard_metric_items(metrics)
    return f"""
<section class="investment-dashboard" aria-label="투자 요약 대시보드">
  <div class="recommendation-tile">
    <div class="label">투자의견</div>
    <div class="recommendation-value">{_p(_recommendation_display(meta.get("recommendation", "")))}</div>
    <p>{_p(cover.get("headline", ""))}</p>
  </div>
  <div class="dashboard-main">
    <div class="dashboard-message">
      <div class="label">리포트 핵심 메시지</div>
      <p>{_p(cover.get("one_line_view", ""))}</p>
    </div>
    <div class="dashboard-grid">
      {_dashboard_panel("긍정 요인", positives[:2], "summary-card")}
      {_dashboard_panel("확인 필요 요인", negatives[:2], "risk-card")}
      {_dashboard_panel("핵심 지표", metric_items, "metric-snapshot")}
    </div>
  </div>
</section>
"""


def _dashboard_panel(title: str, items: list[Any], class_name: str) -> str:
    rows = "\n".join(f"<li>{_p(item)}</li>" for item in items if str(item).strip())
    if not rows:
        rows = "<li>확인 가능한 지표 기준 점검 중</li>"
    return f"""
<div class="dashboard-panel {class_name}">
  <div class="label">{_p(title)}</div>
  <ul>{rows}</ul>
</div>
"""


def _dashboard_metric_items(metrics: list[dict[str, Any]]) -> list[str]:
    items = []
    for metric in metrics:
        value = metric.get("value")
        if not value or value == "N/A":
            continue
        items.append(f"{_metric_display_name(metric.get('metric_name', ''))}: {value}")
        if len(items) >= 3:
            break
    return items


def _recommendation_display(value: Any) -> str:
    return "Hold / 중립" if str(value).strip().lower() == "hold" else str(value)


def _executive_summary_block(body: str) -> str:
    if not body:
        return ""
    return f"""
<div class="card executive-summary">
  <h3>투자 요약 코멘트</h3>
  <p>{_p(body)}</p>
</div>
"""


def _summary_card(title: str, body: str) -> str:
    return f"""
<div class="card summary-card">
  <h3>{_p(title)}</h3>
  <p>{_p(body)}</p>
</div>
"""


def _signal_card(title: str, items: list[Any], class_name: str) -> str:
    rows = "\n".join(f"<li>{_p(item)}</li>" for item in items)
    return f"""
<div class="card {class_name}">
  <h3>{_p(title)}</h3>
  <ul class="pill-list">{rows}</ul>
</div>
"""


def _catalyst_groups(cards: list[dict[str, Any]]) -> str:
    if not cards:
        return ""
    group_order = ["핵심 촉매", "중장기 성장 옵션", "글로벌 확장 변수", "기타 촉매"]
    chunks = []
    for group in group_order:
        grouped = [card for card in cards if card.get("catalyst_group") == group]
        if not grouped:
            continue
        rows = "\n".join(_catalyst_card(card) for card in grouped)
        chunks.append(
            f"""
  <h3 class="group-heading">{_p(group)}</h3>
  <div class="section-grid">{rows}</div>
"""
        )
    return "\n".join(chunks)


def _analysis_card(card: dict[str, Any]) -> str:
    return f"""
<div class="card">
  <h3>{_p(card.get("title", ""))}</h3>
  {_card_section("확인된 지표", card.get("what_we_see", ""))}
  {_card_section("투자 판단상 의미", card.get("why_it_matters", ""))}
  {_card_section("확인 필요 요인", card.get("what_to_watch", ""))}
  {_card_section("투자의견 시사점", card.get("investment_implication", ""))}
</div>
"""


def _catalyst_card(card: dict[str, Any]) -> str:
    return f"""
<div class="card summary-card">
  <h3>촉매 요인: {_p(card.get("catalyst_title", ""))}</h3>
  {_card_section("투자 판단상 의미", card.get("investment_relevance", ""))}
  {_card_section("주요 근거", card.get("evidence_from_strategy", ""))}
  {_card_section("확인 필요 요인", card.get("what_to_watch", ""))}
  {_card_section("투자의견 영향", card.get("investment_impact", ""))}
</div>
"""


def _risk_card(card: dict[str, Any]) -> str:
    return f"""
<div class="card risk-card">
  <h3>{_p(card.get("risk_type", ""))}</h3>
  {_card_section("리스크 요인", card.get("description", ""))}
  {_card_section("투자 판단상 영향", card.get("impact", ""))}
  {_card_section("확인 필요 요인", card.get("monitoring_point", ""))}
  {_card_section("투자의견과의 연결", card.get("hold_connection", ""))}
</div>
"""


def _final_rationale_card(card: dict[str, Any]) -> str:
    return f"""
<div class="card">
  <h3>{_p(card.get("title", "최종 투자의견 근거"))}</h3>
  {_card_section("긍정 요인", card.get("positive_case", ""))}
  {_card_section("경계 요인", card.get("caution_case", ""))}
  {_card_section("종합 판단", card.get("balance_of_evidence", ""))}
  {_card_section("투자의견 결론", card.get("investment_conclusion", ""))}
  {_card_section("투자의견 시사점", card.get("investment_implication", ""))}
</div>
"""


def _view_change_conditions(conditions: dict[str, Any]) -> str:
    if not conditions:
        return ""
    upside = "".join(f"<li>{_p(item)}</li>" for item in conditions.get("upside_conditions", [])[:4])
    downside = "".join(f"<li>{_p(item)}</li>" for item in conditions.get("downside_conditions", [])[:4])
    return f"""
<h2>투자의견 변경 조건</h2>
<div class="note-box">아래 조건은 향후 투자의견이 달라질 수 있는 확인 조건이며, 새로운 수치 가정 없이 관찰 가능한 변화만 제시한다.</div>
<div class="section-grid">
  <div class="card summary-card">
    <h3>상향 조건</h3>
    <ul class="pill-list">{upside}</ul>
  </div>
  <div class="card risk-card">
    <h3>하향 조건</h3>
    <ul class="pill-list">{downside}</ul>
  </div>
</div>
"""


def _chart_block(block: dict[str, Any], *, output_dir: Path, embed_images: bool) -> str:
    img_path = block.get("html_img_path") or block.get("figure_path_png") or block.get("figure_path")
    img_src = _image_src(img_path, output_dir=output_dir, embed_images=embed_images)
    return f"""
<article class="chart-block">
  <h2>{_p(block.get("display_title") or block.get("figure_title", ""))}</h2>
  <img class="chart-img" src="{_attr(img_src)}" alt="{_attr(block.get("display_title", "chart"))}">
  <div class="card">
    {_card_section("차트에서 확인되는 점", block.get("what_chart_shows", ""))}
    {_card_section("애널리스트 해석", block.get("analyst_takeaway", ""))}
    {_chart_watch_points(block.get("chart_insights", {}))}
    <div class="takeaway-box">
      <div class="label">해석상 유의점</div>
      <p>{_p(block.get("interpretation_limit", ""))}</p>
    </div>
  </div>
</article>
"""


def _chart_watch_points(insights: Any) -> str:
    if not isinstance(insights, dict):
        return ""
    watch_points = insights.get("watch_points", [])
    if not isinstance(watch_points, list):
        return ""
    items = [_p(item) for item in watch_points if str(item).strip()]
    if not items:
        return ""
    return f"""
<div class="card-section">
  <div class="label">확인 포인트</div>
  <ul class="pill-list">{''.join(f'<li>{item}</li>' for item in items[:3])}</ul>
</div>
"""


def _metrics_table(metrics: list[dict[str, Any]]) -> str:
    rows = "\n".join(
        "<tr>"
        f"<td>{_p(_metric_display_name(metric.get('metric_name', '')))}</td>"
        f"<td>{_p(metric.get('period', ''))}</td>"
        f"<td>{_p(metric.get('value', ''))}</td>"
        f"<td>{_p(metric.get('interpretation', ''))}</td>"
        "</tr>"
        for metric in metrics
    )
    return f"""
<table class="metrics-table">
  <thead>
    <tr><th>지표</th><th>기준 기간</th><th>수치</th><th>해석</th></tr>
  </thead>
  <tbody>{rows}</tbody>
</table>
"""


def _card_section(label: str, body: str) -> str:
    if not body:
        return ""
    return f"""
<div class="card-section">
  <div class="label">{_p(label)}</div>
  <p>{_p(body)}</p>
</div>
"""


def _p(value: Any) -> str:
    return escape(str(value), quote=False)


def _attr(value: Any) -> str:
    return escape(str(value), quote=True)


def _metric_display_name(value: Any) -> str:
    labels = {
        "Revenue": "매출",
        "Contribution Margin": "공헌이익률",
        "SG&A Margin": "판관비율",
        "EPS": "EPS",
        "Debt Ratio": "부채비율",
        "Current Ratio": "유동비율",
        "Operating Cash Flow": "영업활동현금흐름",
        "Cash & Cash Equivalents": "현금및현금성자산",
    }
    return labels.get(str(value), str(value))


def _image_src(path_value: Any, *, output_dir: Path, embed_images: bool) -> str:
    path = Path(str(path_value)).expanduser()
    if not embed_images:
        if path.exists() and path.is_file():
            figure_dir = output_dir / "figures"
            figure_dir.mkdir(parents=True, exist_ok=True)
            target = figure_dir / path.name
            if path.resolve() != target.resolve():
                shutil.copy2(path, target)
            return f"figures/{target.name}"
        return str(path_value)
    if not path.exists() or not path.is_file():
        return str(path_value)
    mime_type = mimetypes.guess_type(path.name)[0] or "image/png"
    encoded = base64.b64encode(path.read_bytes()).decode("ascii")
    return f"data:{mime_type};base64,{encoded}"
