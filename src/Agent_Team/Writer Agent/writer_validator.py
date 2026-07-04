"""Validation rules for Writer Agent outputs."""

from __future__ import annotations

import re
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any


def validate_writer_outputs(
    *,
    contract: dict[str, Any],
    strategy_report: dict[str, Any],
    chart_manifest: dict[str, Any] | None,
    source_trace: list[dict[str, str]],
    main_tex: str,
    final_pdf_path: str | Path,
    html_preview_path: str | Path = "",
    html_content: str = "",
    render_format: str = "html",
    include_source_trace: bool = False,
    embed_images: bool = False,
    latex_compile_status: str = "not_requested",
    latex_notes: list[str] | None = None,
) -> dict[str, Any]:
    """Validate Writer Agent contract, figure usage, and generated artifacts."""

    notes: list[str] = list(latex_notes or [])
    warnings: list[str] = []
    checks: dict[str, str] = {}
    strategy_opinion = strategy_report.get("final_recommendation", {}).get("opinion")
    checks["recommendation_consistency"] = _pass_fail(contract["report_metadata"].get("recommendation") == strategy_opinion)
    checks["target_price_policy"] = _pass_fail(contract["report_metadata"].get("target_price") == "N/A")
    checks["forbidden_terms"] = _validate_forbidden_terms(contract, notes)
    checks["figure_assets"] = _validate_figure_assets(contract, notes)
    checks["chart_manifest_consistency"] = _validate_chart_manifest_consistency(contract, chart_manifest, notes)
    checks["basis_mismatch_warning"] = _pass_fail(
        _contract_contains(contract, "동일 기간 YoY로 단정") or _contract_contains(contract, "집계 기준")
    )
    checks["price_signal_causality_warning"] = _pass_fail(_contract_contains(contract, "펀더멘털 개선의 직접 증거로 단정할 수 없다"))
    checks["source_trace"] = _pass_fail(bool(source_trace))
    checks["design_spec"] = _pass_fail(bool(contract.get("design_spec")))
    checks["recommendation_card"] = _pass_fail(contract.get("design_spec", {}).get("components", {}).get("recommendation_card") is True)
    checks["chart_takeaway_box"] = _pass_fail(all(block.get("analyst_takeaway") for block in contract.get("visual_report_blocks", [])))
    checks["chart_interpretation_limit"] = _pass_fail(all(block.get("interpretation_limit") for block in contract.get("visual_report_blocks", [])))
    checks["chart_block_structure"] = _validate_chart_block_structure(contract, notes)
    checks["reader_friendly_sections"] = _validate_reader_friendly_sections(contract, notes)
    checks["strategy_copy_ratio"] = _validate_strategy_copy_ratio(contract, strategy_report, warnings)
    checks["raw_copy_sequence"] = _validate_raw_copy_sequence(contract, strategy_report, warnings)
    checks["investment_implication"] = _validate_investment_implications(contract, warnings)
    checks["section_text_length"] = _validate_text_lengths(contract, notes)
    checks["price_signal_causality"] = _validate_price_causality(contract, notes)
    if render_format in {"html", "both"}:
        checks["html_preview_render"] = _validate_html_preview(html_preview_path, notes)
        checks["html_source_trace_hidden"] = _validate_source_trace_hidden(html_content, include_source_trace, notes)
        checks["html_letter_spacing"] = _validate_html_letter_spacing(html_content, notes)
        checks["html_internal_terms"] = _validate_html_internal_terms(html_content, notes)
        checks["html_image_mode"] = _validate_html_image_mode(html_content, embed_images, notes)
        checks["html_sentence_polish"] = _validate_html_sentence_polish(html_content, notes)
    else:
        checks["html_preview_render"] = "not_requested"
        checks["html_source_trace_hidden"] = "not_requested"
        checks["html_letter_spacing"] = "not_requested"
        checks["html_internal_terms"] = "not_requested"
        checks["html_image_mode"] = "not_requested"
        checks["html_sentence_polish"] = "not_requested"
    if render_format in {"pdf", "both"}:
        checks["latex_structure"] = _pass_fail("\\begin{figure}" in main_tex and "Analyst Takeaway" in main_tex)
        checks["pdf_render"] = _pass_fail(Path(final_pdf_path).exists() and Path(final_pdf_path).stat().st_size > 0)
        checks["latex_compile"] = latex_compile_status
    else:
        checks["latex_structure"] = "not_requested"
        checks["pdf_render"] = "not_requested"
        checks["latex_compile"] = "not_requested"
    if render_format in {"pdf", "both"} and latex_compile_status != "pass":
        notes.append("LaTeX toolchain unavailable or compile failed; final_report.pdf was generated with PyMuPDF fallback renderer.")

    hard_fail_checks = {
        key: value
        for key, value in checks.items()
        if value not in {"not_requested", "warning"} and key != "latex_compile"
    }
    status = "pass" if all(value == "pass" for value in hard_fail_checks.values()) else "fail"
    if status == "pass" and render_format in {"pdf", "both"} and latex_compile_status != "pass":
        status = "pass_with_warnings"
    if status == "pass" and warnings:
        status = "pass_with_warnings"
    return {
        "status": status,
        **checks,
        "warnings": warnings,
        "notes": notes,
    }


def _validate_forbidden_terms(contract: dict[str, Any], notes: list[str]) -> str:
    forbidden = contract.get("validation_rules", {}).get("forbidden_terms_without_source", [])
    scanned_parts = [
        contract.get("cover_summary", {}),
        contract.get("investment_view", {}),
        contract.get("key_metrics_table", {}),
        contract.get("visual_report_blocks", {}),
        contract.get("reader_friendly_sections", {}),
        contract.get("sections", {}),
        contract.get("limitations", {}),
    ]
    scanned_text = " ".join(_string_values(part) for part in scanned_parts)
    allowed_context = {"Target Price", "N/A"}
    violations = [term for term in forbidden if term and term in scanned_text]
    if violations:
        notes.append(f"Forbidden term(s) found in report body: {violations}")
    return _pass_fail(not violations)


def _validate_figure_assets(contract: dict[str, Any], notes: list[str]) -> str:
    missing = [
        block.get("figure_path")
        for block in contract.get("visual_report_blocks", [])
        if not block.get("figure_path") or not Path(block["figure_path"]).exists()
    ]
    if missing:
        notes.append(f"Missing figure asset(s): {missing}")
    return _pass_fail(not missing)


def _validate_chart_manifest_consistency(contract: dict[str, Any], chart_manifest: dict[str, Any] | None, notes: list[str]) -> str:
    manifest_ids = {chart.get("figure_id") for chart in (chart_manifest or {}).get("charts", [])}
    unknown = [
        block.get("figure_id")
        for block in contract.get("visual_report_blocks", [])
        if block.get("figure_id") not in manifest_ids
    ]
    if unknown:
        notes.append(f"Figure id(s) not found in chart manifest: {unknown}")
    return _pass_fail(not unknown)


def _validate_chart_block_structure(contract: dict[str, Any], notes: list[str]) -> str:
    missing = []
    for block in contract.get("visual_report_blocks", []):
        for field in ["what_chart_shows", "analyst_takeaway", "interpretation_limit"]:
            if not block.get(field):
                missing.append(f"{block.get('figure_id')}:{field}")
    if missing:
        notes.append(f"Chart block field(s) missing: {missing}")
    return _pass_fail(not missing)


def _validate_reader_friendly_sections(contract: dict[str, Any], notes: list[str]) -> str:
    reader = contract.get("reader_friendly_sections", {})
    cards = []
    cards.extend(reader.get("financial_view_cards", []))
    cards.extend(reader.get("market_view_cards", []))
    final = reader.get("final_rationale")
    if isinstance(final, dict):
        cards.append(final)
    invalid = []
    for card in cards:
        count = sum(bool(card.get(field)) for field in ["what_we_see", "why_it_matters", "what_to_watch"])
        if count < 2:
            invalid.append(card.get("title") or "final_rationale")
    if invalid:
        notes.append(f"Reader-friendly card(s) lack required commentary fields: {invalid}")
    return _pass_fail(not invalid and bool(cards))


def _validate_strategy_copy_ratio(contract: dict[str, Any], strategy_report: dict[str, Any], warnings: list[str]) -> str:
    strategy_sentences = _sentences(str(strategy_report))
    candidate_text = _writer_commentary_text(contract)
    violations = []
    for paragraph in _sentences(candidate_text):
        if len(paragraph) < 35:
            continue
        ratio = max((SequenceMatcher(None, paragraph, source).ratio() for source in strategy_sentences), default=0)
        if ratio >= 0.8:
            violations.append(paragraph[:80])
    if violations:
        warnings.append(f"Strategy sentence copy ratio over threshold: {violations[:3]}")
    return "warning" if violations else "pass"


def _validate_raw_copy_sequence(contract: dict[str, Any], strategy_report: dict[str, Any], warnings: list[str]) -> str:
    strategy_tokens = _tokens(str(strategy_report))
    writer_tokens = _tokens(_writer_commentary_text(contract))
    if len(strategy_tokens) < 15 or len(writer_tokens) < 15:
        return "pass"
    strategy_ngrams = {" ".join(strategy_tokens[index : index + 15]) for index in range(len(strategy_tokens) - 14)}
    matches = []
    for index in range(len(writer_tokens) - 14):
        phrase = " ".join(writer_tokens[index : index + 15])
        if phrase in strategy_ngrams:
            matches.append(phrase)
            if len(matches) >= 3:
                break
    if matches:
        warnings.append(f"15-token raw-copy sequence(s) detected: {matches}")
    return "warning" if matches else "pass"


def _validate_investment_implications(contract: dict[str, Any], warnings: list[str]) -> str:
    reader = contract.get("reader_friendly_sections", {})
    recommendation = str(contract.get("report_metadata", {}).get("recommendation", "")).strip()
    missing = []
    for card in reader.get("financial_view_cards", []) + reader.get("market_view_cards", []):
        if not card.get("investment_implication"):
            missing.append(card.get("title", "unnamed_card"))
    final = reader.get("final_rationale", {})
    if isinstance(final, dict) and not final.get("investment_implication"):
        missing.append("final_rationale")
    for card in reader.get("risk_cards", []):
        impact = str(card.get("impact", ""))
        connection = str(card.get("hold_connection", ""))
        combined = impact + " " + connection
        markers = [marker for marker in [recommendation, "보수적", "리스크 할인", "투자의견", "판단"] if marker]
        if not any(marker in combined for marker in markers):
            missing.append(card.get("risk_type", "risk_card"))
    if missing:
        warnings.append(f"Investment implication missing or weak in section(s): {missing}")
    return "warning" if missing else "pass"


def _validate_text_lengths(contract: dict[str, Any], notes: list[str]) -> str:
    reader = contract.get("reader_friendly_sections", {})
    too_long = []
    for card in reader.get("financial_view_cards", []) + reader.get("market_view_cards", []):
        for field in ["what_we_see", "why_it_matters", "what_to_watch", "investment_implication"]:
            if len(str(card.get(field, ""))) > 520:
                too_long.append(f"{card.get('title')}:{field}")
    for block in contract.get("visual_report_blocks", []):
        if len(str(block.get("analyst_takeaway", ""))) > 520:
            too_long.append(f"{block.get('figure_id')}:analyst_takeaway")
    if too_long:
        notes.append(f"Text field(s) too long for preview cards: {too_long}")
    return _pass_fail(not too_long)


def _validate_price_causality(contract: dict[str, Any], notes: list[str]) -> str:
    text = str(
        {
            "reader_friendly_sections": contract.get("reader_friendly_sections", {}),
            "visual_report_blocks": contract.get("visual_report_blocks", []),
        }
    )
    bad_patterns = [
        "주가 상승이 펀더멘털 개선을 입증",
        "가격 신호가 펀더멘털 개선을 입증",
        "거래량 증가가 재무 개선을 입증",
        "펀더멘털 개선이 주가 상승을 견인",
    ]
    violations = [pattern for pattern in bad_patterns if pattern in text]
    if violations:
        notes.append(f"Unsupported price causality statement(s): {violations}")
    return _pass_fail(not violations)


def _validate_html_preview(html_preview_path: str | Path, notes: list[str]) -> str:
    path = Path(html_preview_path) if html_preview_path else Path()
    ok = bool(html_preview_path) and path.exists() and path.stat().st_size > 0
    if not ok:
        notes.append(f"HTML preview not found or empty: {html_preview_path}")
    return _pass_fail(ok)


def _validate_source_trace_hidden(html_content: str, include_source_trace: bool, notes: list[str]) -> str:
    if include_source_trace:
        return "pass"
    leaked = "Source Trace Summary" in html_content or "source_trace" in html_content or "strategy_report.json /" in html_content
    if leaked:
        notes.append("Source Trace appears in HTML preview while include_source_trace is false.")
    return _pass_fail(not leaked)


def _validate_html_letter_spacing(html_content: str, notes: list[str]) -> str:
    bad_values = []
    for match in re.finditer(r"letter-spacing\s*:\s*([^;]+)", html_content):
        value = match.group(1).strip().lower()
        if value != "normal":
            bad_values.append(value)
    if "text-transform: uppercase" in html_content.lower():
        bad_values.append("text-transform: uppercase")
    if bad_values:
        notes.append(f"Unsupported HTML text spacing/style value(s): {bad_values}")
    return _pass_fail(not bad_values)


def _validate_html_internal_terms(html_content: str, notes: list[str]) -> str:
    internal_terms = [
        "Investment Summary",
        "Key Charts",
        "Financial View",
        "Market View",
        "Catalyst Analysis",
        "Risk Matrix",
        "Final Rationale",
        "Appendix: Limitations",
        "Positive case",
        "Caution case",
        "Balance of evidence",
        "Investment conclusion",
        "Evidence from Strategy",
        "Investment relevance",
        "Investment implication",
        "What we see",
        "Why it matters",
        "What to watch",
        "Valuation Agent 미적용",
        "Report Type: Equity Research Draft",
    ]
    found = [term for term in internal_terms if term in html_content]
    if found:
        notes.append(f"Internal label(s) found in HTML: {found}")
    return _pass_fail(not found)


def _validate_html_sentence_polish(html_content: str, notes: list[str]) -> str:
    bad_patterns = [
        "투자 의견을 적극적 비중 확대에는",
        "Buy로 높이기에는",
        "전환 근거로는 부족",
    ]
    found = [pattern for pattern in bad_patterns if pattern in html_content]
    if found:
        notes.append(f"Awkward sentence pattern(s) found in HTML: {found}")
    return _pass_fail(not found)


def _validate_html_image_mode(html_content: str, embed_images: bool, notes: list[str]) -> str:
    image_sources = re.findall(r'<img[^>]+src="([^"]+)"', html_content)
    if not image_sources:
        notes.append("No HTML image source found.")
        return "fail"
    if embed_images:
        missing = [src[:80] for src in image_sources if not src.startswith("data:image/")]
        if missing:
            notes.append(f"HTML image source(s) are not embedded data URIs: {missing}")
        return _pass_fail(not missing)
    embedded = [src[:40] for src in image_sources if src.startswith("data:image/")]
    if embedded:
        notes.append(f"HTML image source(s) unexpectedly embedded while embed_images is false: {embedded}")
    return _pass_fail(not embedded)


def _sentences(text: str) -> list[str]:
    normalized = " ".join(text.replace("\\n", " ").split())
    parts = re.split(r"(?<=[.!?。다])\s+", normalized)
    return [part.strip() for part in parts if part.strip()]


def _writer_commentary_text(contract: dict[str, Any]) -> str:
    return str(
        {
            "cover_summary": contract.get("cover_summary", {}),
            "reader_friendly_sections": contract.get("reader_friendly_sections", {}),
            "visual_report_blocks": [
                {
                    "what_chart_shows": block.get("what_chart_shows"),
                    "analyst_takeaway": block.get("analyst_takeaway"),
                }
                for block in contract.get("visual_report_blocks", [])
            ],
        }
    )


def _tokens(text: str) -> list[str]:
    normalized = re.sub(r"[^\w가-힣.%+-]+", " ", text)
    return [token for token in normalized.split() if token]


def _string_values(value: Any) -> str:
    if isinstance(value, dict):
        return " ".join(_string_values(item) for item in value.values())
    if isinstance(value, list):
        return " ".join(_string_values(item) for item in value)
    if isinstance(value, str):
        return value
    return ""


def _contract_contains(contract: dict[str, Any], text: str) -> bool:
    return text in str(contract)


def _pass_fail(condition: bool) -> str:
    return "pass" if condition else "fail"
