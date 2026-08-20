"""Deterministic, evidence-ID-free HTML renderer for Single-LLM reports."""

from __future__ import annotations

from html import escape
import re
from typing import Any


_RECOMMENDATION_LABELS = {"BUY": "매수", "HOLD": "보유", "SELL": "매도"}
_CONVICTION_LABELS = {"LOW": "낮음", "MEDIUM": "중간", "HIGH": "높음"}
_EFFECT_LABELS = {
    "POSITIVE": "긍정",
    "NEGATIVE": "부정",
    "MIXED": "혼재",
    "NEUTRAL": "중립",
}
_ANALYSIS_TITLES = {
    "business_and_financial": "사업 및 재무 분석",
    "market_and_valuation": "시장 및 밸류에이션",
    "news_and_catalysts": "뉴스 및 촉매",
    "peer_comparison": "선정 Peer 비교",
}
_INTERNAL_CITATION_PATTERN = re.compile(
    r"\s*\[(?:[A-Z][A-Z0-9_.:-]*_[A-Z0-9_.:-]+)"
    r"(?:\s*,\s*[A-Z][A-Z0-9_.:-]*_[A-Z0-9_.:-]+)*\]"
)
_TRAILING_INTERNAL_CITATION_PATTERN = re.compile(
    r"\s*evidence_ids?\s*:\s*"
    r"(?:[A-Z][A-Z0-9_.:-]*_[A-Z0-9_.:-]+)"
    r"(?:\s*,\s*[A-Z][A-Z0-9_.:-]*_[A-Z0-9_.:-]+)*\s*$",
    flags=re.IGNORECASE,
)


def render_report_html(report: dict[str, Any]) -> str:
    """Render only user-facing report content; never expose evidence IDs."""

    metadata = _object(report.get("metadata"))
    call = _object(report.get("investment_call"))
    analysis = _object(report.get("analysis"))
    title = str(metadata.get("report_title") or "Single-LLM 투자 리서치 보고서")
    recommendation = str(call.get("recommendation") or "HOLD")
    sections: list[str] = [
        _investment_call_section(call),
        _key_evidence_section(_array(report.get("key_evidence"))),
    ]
    for key, section_title in _ANALYSIS_TITLES.items():
        sections.append(_analysis_section(key, section_title, _array(analysis.get(key))))
    sections.append(_risk_section(_array(report.get("risks"))))
    sections.append(_limits_section(_array(report.get("data_limits"))))

    return f"""<!doctype html>
<html lang="ko">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{_e(title)}</title>
  <style>
    :root {{ --ink:#152033; --muted:#5f6b7a; --line:#dce2ea; --panel:#f7f9fc; --accent:#173f73; }}
    * {{ box-sizing:border-box; }}
    body {{ margin:0; background:#eef2f7; color:var(--ink); font:15px/1.65 Arial,"Noto Sans KR",sans-serif; }}
    .a4-sheet {{ width:min(100%, 980px); margin:32px auto; padding:48px 56px; background:white; box-shadow:0 8px 30px rgba(30,45,70,.09); }}
    .report-name {{ margin:0 0 18px; color:var(--accent); font-size:30px; line-height:1.3; }}
    .meta-grid {{ display:grid; grid-template-columns:repeat(4,minmax(0,1fr)); gap:10px; margin-bottom:28px; }}
    .meta-grid div {{ padding:10px 12px; background:var(--panel); border:1px solid var(--line); }}
    .meta-grid span {{ display:block; color:var(--muted); font-size:12px; }}
    section {{ margin:30px 0; }}
    h2 {{ margin:0 0 12px; padding-bottom:7px; border-bottom:2px solid var(--accent); font-size:21px; }}
    h3 {{ margin:16px 0 6px; font-size:16px; }}
    p {{ margin:6px 0 12px; }}
    ul {{ margin:6px 0 12px; padding-left:22px; }}
    .callout {{ padding:16px 18px; background:#f2f6fb; border-left:5px solid var(--accent); }}
    .tag {{ display:inline-block; margin-right:8px; padding:3px 9px; color:white; background:var(--accent); border-radius:999px; font-size:13px; }}
    table {{ width:100%; border-collapse:collapse; margin:10px 0 16px; }}
    th,td {{ padding:9px 10px; border:1px solid var(--line); text-align:left; vertical-align:top; }}
    th {{ background:var(--panel); }}
    .disclaimer {{ margin-top:34px; color:var(--muted); font-size:12px; }}
    @media(max-width:720px) {{ .a4-sheet {{ margin:0; padding:28px 20px; }} .meta-grid {{ grid-template-columns:1fr 1fr; }} }}
  </style>
</head>
<body>
<article class="a4-sheet">
  <header>
    <h1 class="report-name">{_e(title)}</h1>
    <div class="meta-grid">
      <div><span>기업</span>{_e(metadata.get("company_name"))}</div>
      <div><span>기준일</span>{_e(metadata.get("selected_date"))}</div>
      <div><span>판단 기간</span>{_e(metadata.get("decision_horizon"))}</div>
      <div><span>투자의견</span>{_e(_RECOMMENDATION_LABELS.get(recommendation, recommendation))}</div>
    </div>
  </header>
  {''.join(sections)}
  <p class="disclaimer">본 보고서는 연구·성능 비교 목적의 Single-LLM 산출물이며 투자 자문이나 매매 권유가 아닙니다.</p>
</article>
</body>
</html>
"""


def _investment_call_section(call: dict[str, Any]) -> str:
    recommendation = str(call.get("recommendation") or "")
    conviction = str(call.get("conviction") or "")
    rationale_table = _definition_table(
        [
            ("현재 가격 판단", call.get("current_price_rationale")),
            ("전망", call.get("forward_outlook")),
            ("밸류에이션", call.get("valuation_view")),
            ("잔여 불확실성", call.get("residual_uncertainty")),
        ]
    )
    upgrade_conditions = _condition_list(_array(call.get("upgrade_conditions")))
    downgrade_conditions = _condition_list(_array(call.get("downgrade_conditions")))
    return f"""
  <section id="investment_call_thesis">
    <h2>투자의견 및 핵심 논거</h2>
    <div class="callout">
      <span class="tag">{_e(_RECOMMENDATION_LABELS.get(recommendation, recommendation))}</span>
      <span class="tag">확신도 {_e(_CONVICTION_LABELS.get(conviction, conviction))}</span>
      <p>{_e(call.get("thesis"))}</p>
    </div>
    {rationale_table}
    <h3>상향 조건</h3>{upgrade_conditions}
    <h3>하향 조건</h3>{downgrade_conditions}
  </section>
"""


def _key_evidence_section(rows: list[Any]) -> str:
    body = "".join(
        "<tr>"
        f"<td>{_e(_object(row).get('label'))}</td>"
        f"<td>{_e(_object(row).get('observed_fact'))}</td>"
        f"<td>{_e(_object(row).get('interpretation'))}</td>"
        f"<td>{_e(_effect_label(_object(row).get('investment_effect')))}</td>"
        "</tr>"
        for row in rows
    )
    return f"""
  <section id="key_evidence_table">
    <h2>핵심 근거</h2>
    <table><thead><tr><th>근거 축</th><th>확인된 사실</th><th>해석</th><th>영향</th></tr></thead><tbody>{body}</tbody></table>
  </section>
"""


def _analysis_section(key: str, title: str, items: list[Any]) -> str:
    blocks = "".join(
        f"<h3>{_e(_object(item).get('claim'))}</h3>"
        f"<p><strong>관찰:</strong> {_e(_object(item).get('observation'))}</p>"
        f"<p><strong>해석:</strong> {_e(_object(item).get('interpretation'))} "
        f"<em>({_e(_effect_label(_object(item).get('investment_effect')))})</em></p>"
        for item in items
    )
    return f'<section id="{_e(key)}"><h2>{_e(title)}</h2>{blocks}</section>'


def _risk_section(risks: list[Any]) -> str:
    body = "".join(
        "<tr>"
        f"<td>{_e(_object(item).get('risk'))}</td>"
        f"<td>{_e(_object(item).get('current_evidence'))}</td>"
        f"<td>{_e(_object(item).get('monitoring_trigger'))}</td>"
        f"<td>{_e(_object(item).get('potential_impact'))}</td>"
        "</tr>"
        for item in risks
    )
    return f"""
  <section id="risk_monitoring_matrix">
    <h2>리스크 및 모니터링</h2>
    <table><thead><tr><th>리스크</th><th>현재 근거</th><th>점검 조건</th><th>잠재 영향</th></tr></thead><tbody>{body}</tbody></table>
  </section>
"""


def _limits_section(limits: list[Any]) -> str:
    items = "".join(
        f"<li><strong>{_e(_object(item).get('limitation'))}</strong> — {_e(_object(item).get('report_impact'))}</li>"
        for item in limits
    )
    return f'<section id="data_limits"><h2>데이터 한계</h2><ul>{items}</ul></section>'


def _condition_list(items: list[Any]) -> str:
    return "<ul>" + "".join(
        f"<li><strong>{_e(_object(item).get('condition'))}</strong> — {_e(_object(item).get('why_it_matters'))}</li>"
        for item in items
    ) + "</ul>"


def _definition_table(rows: list[tuple[str, Any]]) -> str:
    body = "".join(
        f"<tr><th>{_e(label)}</th><td>{_e(value)}</td></tr>" for label, value in rows
    )
    return f"<table><tbody>{body}</tbody></table>"


def _e(value: Any) -> str:
    # The structured JSON retains evidence IDs for audit and validation. A model
    # may also repeat those IDs as bracketed inline citations; remove only that
    # machine-readable citation form from the user-facing HTML.
    display_text = _INTERNAL_CITATION_PATTERN.sub("", str(value or ""))
    display_text = _TRAILING_INTERNAL_CITATION_PATTERN.sub("", display_text)
    return escape(display_text, quote=True)


def _effect_label(value: Any) -> str:
    normalized = str(value or "")
    return _EFFECT_LABELS.get(normalized, normalized)


def _object(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _array(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


__all__ = ["render_report_html"]
