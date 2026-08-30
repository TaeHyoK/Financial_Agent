"""Validation for the fixed-format Writer Agent HTML report."""

from __future__ import annotations

from html import unescape
import re
from typing import Any

from html_report_spec import (
    INVESTMENT_THESIS_ITEM_KEY,
    INVESTMENT_THESIS_SECTION_KEY,
    KEY_EVIDENCE_DISPLAY_COLUMNS,
    LABEL_FREE_KEY_EVIDENCE_DISPLAY_COLUMNS,
    REPORT_DISCLAIMER,
    REPORT_SECTIONS,
    RISK_DISPLAY_COLUMNS,
    TEXT_PARAGRAPH_LIMITS,
    SUPPORTED_INVESTMENT_HORIZONS,
    investment_horizon_heading,
)
from html_report_writer import (
    _plain_korean_text,
    _qualify_partial_product_scope_v2,
    _strategy_role_label,
)
from writer_handoff import EDITORIAL_PACKET_VERSION, EDITORIAL_PACKET_VERSION_V3


REQUIRED_TABLE_SECTION_IDS = {
    "key-evidence-table-evidence-table",
    "risk-monitoring-matrix-risk-monitoring-table",
}


def validate_html_report(
    *,
    report_payload: dict[str, Any],
    html_content: str,
    writer_handoff: dict[str, Any],
) -> dict[str, Any]:
    """Reject report-integrity violations and report presentation advisories."""

    notes: list[str] = []
    hard_checks = {
        "complete_html_document": _pass_fail(
            bool(re.search(r"<html(?:\s|>)", html_content, flags=re.IGNORECASE))
            and bool(re.search(r"</html>\s*$", html_content, flags=re.IGNORECASE))
        ),
        "required_section_ids": _validate_required_section_ids(html_content, notes),
        "forbidden_content_removed": _validate_forbidden_content(report_payload, notes),
        "required_tables": _validate_required_tables(html_content, notes),
        "recommendation_consistency": _validate_recommendation(report_payload, writer_handoff, notes),
        "reader_recommendation_labels_hidden": _validate_reader_recommendation_labels(
            html_content, notes
        ),
        "fixed_disclaimer": _validate_fixed_disclaimer(html_content, notes),
        "investment_horizon_heading": _validate_investment_horizon_heading(
            report_payload,
            html_content,
            writer_handoff,
            notes,
        ),
        "grounding_refs": _validate_grounding_refs(report_payload, writer_handoff, notes),
        "card_key_coverage": _validate_card_key_coverage(report_payload, writer_handoff, notes),
        "strategy_meaning_preservation": _validate_strategy_meaning_preservation(
            report_payload, writer_handoff, notes
        ),
        "claim_card_grounding": _validate_claim_card_grounding(
            report_payload, writer_handoff, notes
        ),
        "required_limitation_coverage": _validate_required_limitation_coverage(
            report_payload, writer_handoff, notes
        ),
        "chart_selection_grounding": _validate_chart_selection_grounding(
            report_payload, writer_handoff, notes
        ),
        "internal_metadata_hidden": _validate_internal_metadata_hidden(
            report_payload, html_content, writer_handoff, notes
        ),
        "required_evidence_coverage": _validate_required_evidence_coverage(report_payload, writer_handoff, notes),
        "large_number_grounding": _validate_large_number_grounding(report_payload, writer_handoff, notes),
        "absolute_paths_removed": _validate_no_absolute_paths(report_payload, html_content, notes),
    }
    advisory_notes: list[str] = []
    advisory_checks = {
        "section_h1_count": _pass_fail(
            len(re.findall(r"<h1[>\s]", html_content))
            == len(REPORT_SECTIONS)
            + (1 if report_payload.get("report_charts") else 0)
        ),
        "table_of_contents_removed": _validate_no_table_of_contents(
            html_content, advisory_notes
        ),
        "a4_print_layout": _validate_a4_print_layout(html_content, advisory_notes),
        "style_block": _pass_fail("<style>" in html_content and "</style>" in html_content),
        "strong_tags": _pass_fail("<strong>" in html_content and "</strong>" in html_content),
        "strategy_presentation_preservation": _validate_strategy_presentation_preservation(
            report_payload, writer_handoff, advisory_notes
        ),
        "claim_visibility": _validate_claim_visibility(
            report_payload, writer_handoff, advisory_notes
        ),
        "compact_text_sections": _validate_compact_text_sections(
            report_payload, writer_handoff, advisory_notes
        ),
    }
    advisory_statuses = {
        key: "pass" if value == "pass" else "warning"
        for key, value in advisory_checks.items()
    }
    for key, status in advisory_statuses.items():
        if status == "warning":
            advisory_notes.append(f"{key}: advisory check did not pass")
    hard_failures = {key: value for key, value in hard_checks.items() if value != "pass"}
    return {
        "status": "pass" if not hard_failures else "fail",
        **hard_checks,
        **advisory_statuses,
        "blocking_failures": sorted(hard_failures),
        "advisories": advisory_notes,
        "notes": notes,
    }


def _validate_required_section_ids(html_content: str, notes: list[str]) -> str:
    missing = [
        section["id"]
        for section in REPORT_SECTIONS
        if not _html_has_id(html_content, section["id"])
    ]
    if missing:
        notes.append(f"Missing required section id(s): {missing}")
    return _pass_fail(not missing)


def _validate_chart_selection_grounding(
    report_payload: dict[str, Any],
    writer_handoff: dict[str, Any],
    notes: list[str],
) -> str:
    """Check chart-to-evidence links without judging which chart should be chosen."""

    if writer_handoff.get("packet_version") not in {
        EDITORIAL_PACKET_VERSION,
        EDITORIAL_PACKET_VERSION_V3,
    }:
        return "pass"
    requested = [
        str(value).strip()
        for value in report_payload.get("requested_chart_keys") or []
        if str(value).strip()
    ]
    details = report_payload.get("chart_selection_details")
    if not requested and details in (None, []):
        return "pass"
    if not isinstance(details, list):
        notes.append("Chart selection details are missing.")
        return "fail"
    detail_keys = [
        str(item.get("chart_key") or "").strip()
        for item in details
        if isinstance(item, dict)
    ]
    allowed_cards = set((writer_handoff.get("cards") or {}).keys())
    links_valid = len(details) == len(detail_keys) and all(
        bool(item.get("basis_card_keys"))
        and set(str(key) for key in item.get("basis_card_keys") or []).issubset(
            allowed_cards
        )
        and bool(str(item.get("selection_reason") or "").strip())
        and bool(str(item.get("chart_observation") or "").strip())
        and bool(str(item.get("investment_interpretation") or "").strip())
        and (
            len(str(item.get("chart_observation") or "").strip())
            + len(str(item.get("investment_interpretation") or "").strip())
            <= 220
        )
        for item in details
        if isinstance(item, dict)
    )
    passed = detail_keys == requested and links_valid
    if not passed:
        notes.append(
            "Chart selection details must match requested chart keys and reference Writer cards."
        )
    return _pass_fail(passed)


def _validate_no_table_of_contents(html_content: str, notes: list[str]) -> str:
    blocked = ["<aside", "</aside>"]
    found = [term for term in blocked if term in html_content]
    if found:
        notes.append(f"Table of Contents markup remains: {found}")
    return _pass_fail(not found)


def _validate_forbidden_content(report_payload: dict[str, Any], notes: list[str]) -> str:
    blocked = {"target_price", "view_change_conditions", "view_change"}
    found = sorted(blocked.intersection(_collect_keys(report_payload)))
    if found:
        notes.append(f"Forbidden output key(s) remain: {found}")
    return _pass_fail(not found)


def _validate_required_tables(html_content: str, notes: list[str]) -> str:
    missing = [
        section_id
        for section_id in REQUIRED_TABLE_SECTION_IDS
        if not _html_has_id(html_content, section_id)
    ]
    table_count = len(re.findall(r"<table(?:\s|>)", html_content, flags=re.IGNORECASE))
    if missing:
        notes.append(f"Missing table subsection heading id(s): {missing}")
    if table_count < len(REQUIRED_TABLE_SECTION_IDS):
        notes.append(f"Expected at least {len(REQUIRED_TABLE_SECTION_IDS)} tables, found {table_count}.")
    return _pass_fail(not missing and table_count >= len(REQUIRED_TABLE_SECTION_IDS))


def _validate_a4_print_layout(html_content: str, notes: list[str]) -> str:
    required_terms = [
        "@page",
        "size: A4",
        "width: 210mm",
        'class="a4-sheet"',
        'class="paper-grid"',
        'class="visual-sidebar"',
        "grid-template-columns: minmax(0, 145mm) 44mm",
        "column-gap: 7mm",
        "@media screen and (max-width: 820px)",
        "break-inside: auto",
        "page-break-inside: auto",
    ]
    missing = [term for term in required_terms if term not in html_content]
    single_page_layout = all(
        term in html_content
        for term in ("height: 297mm", "max-height: 297mm")
    )
    complete_flow_layout = all(
        term in html_content
        for term in ("min-height: 297mm", "max-height: none")
    )
    if missing:
        notes.append(f"Missing A4 print layout term(s): {missing}")
    if not single_page_layout and not complete_flow_layout:
        notes.append("Missing a supported complete A4 pagination layout.")
    return _pass_fail(not missing and (single_page_layout or complete_flow_layout))


def _validate_recommendation(report_payload: dict[str, Any], writer_handoff: dict[str, Any], notes: list[str]) -> str:
    decision = _dict(writer_handoff.get("decision"))
    expected = str(decision.get("opinion") or decision.get("judgment") or "").strip()
    actual = str(report_payload.get("metadata", {}).get("recommendation") or "").strip()
    ok = bool(expected) and actual == expected
    if not ok:
        notes.append(f"Recommendation mismatch: expected={expected!r}, actual={actual!r}")
    return _pass_fail(ok)


def _validate_reader_recommendation_labels(html_content: str, notes: list[str]) -> str:
    visible_text = _visible_html_text(html_content)
    matches = sorted(
        {
            match.group(0)
            for match in re.finditer(
                r"(?<![A-Za-z가-힣])(?:buy|hold|sell|매수|매도|보유)(?![A-Za-z가-힣])",
                visible_text,
                flags=re.IGNORECASE,
            )
        }
    )
    if matches:
        notes.append(f"Reader-visible recommendation label(s) remain: {matches}")
    return _pass_fail(not matches)


def _validate_fixed_disclaimer(html_content: str, notes: list[str]) -> str:
    required_terms = [
        f'class="report-disclaimer">{REPORT_DISCLAIMER}</footer>',
        ".report-disclaimer {",
        "font-size: 4.4pt",
        "text-align: center",
    ]
    missing = [term for term in required_terms if term not in html_content]
    if missing:
        notes.append(f"Fixed report disclaimer is missing or misformatted: {missing}")
    return _pass_fail(not missing)


def _validate_investment_horizon_heading(
    report_payload: dict[str, Any],
    html_content: str,
    writer_handoff: dict[str, Any],
    notes: list[str],
) -> str:
    expected_horizon = str(
        _dict(writer_handoff.get("decision")).get("investment_horizon") or ""
    ).strip()
    payload_horizon = str(
        _dict(report_payload.get("metadata")).get("investment_horizon") or ""
    ).strip()
    expected_heading = investment_horizon_heading(expected_horizon)
    heading_id = (
        f"{_section_id(INVESTMENT_THESIS_SECTION_KEY)}-"
        f"{INVESTMENT_THESIS_ITEM_KEY.replace('_', '-')}"
    )
    actual_heading = _html_heading_text(html_content, heading_id)
    errors: list[str] = []

    if not expected_horizon:
        errors.append("Writer handoff investment horizon is missing")
    if not payload_horizon or payload_horizon != expected_horizon:
        errors.append(
            "Investment horizon metadata mismatch: "
            f"expected={expected_horizon!r}, actual={payload_horizon!r}"
        )
    if actual_heading != expected_heading:
        errors.append(
            "Investment horizon heading mismatch: "
            f"expected={expected_heading!r}, actual={actual_heading!r}"
        )

    rendered_h2_texts = set(_html_heading_texts(html_content, level=2))
    stale_headings = sorted(
        investment_horizon_heading(horizon)
        for horizon in SUPPORTED_INVESTMENT_HORIZONS
        if horizon != expected_horizon
        and investment_horizon_heading(horizon) in rendered_h2_texts
    )
    if stale_headings:
        errors.append(f"Stale investment horizon heading(s) rendered: {stale_headings}")

    if errors:
        notes.extend(errors)
    return _pass_fail(not errors)


def _validate_grounding_refs(report_payload: dict[str, Any], writer_handoff: dict[str, Any], notes: list[str]) -> str:
    if _is_v2_writer_packet(writer_handoff):
        return "pass"
    refs = {
        str(item.get("id") or ""): str(item.get("strategy_path") or "")
        for item in _list(writer_handoff.get("evidence_refs"))
        if isinstance(item, dict) and item.get("id")
    }
    required_prefixes = {
        "investment_call_thesis": ("final_recommendation", "investment_thesis", "decision_balance", "final_rationale"),
        "business_market_context": ("business_mix_view", "market_price_view"),
        "key_evidence_table": ("financial_view", "business_mix_view", "market_price_view", "valuation_view", "peer_competitor_positioning"),
        "catalysts_execution": ("catalyst_view",),
        "risk_monitoring_matrix": ("risk_view", "limitations.monitoring_points"),
        "data_limits": ("limitations.data_limitations", "limitations.interpretation_limitations"),
    }
    errors: list[str] = []
    sections = _dict(report_payload.get("sections"))
    for section in REPORT_SECTIONS:
        section_key = section["key"]
        payload = _dict(sections.get(section_key))
        section_refs: list[str] = []
        for item_key, _title, _item_type in section["items"]:
            item = _dict(payload.get(item_key))
            grounding_refs = [str(value).strip() for value in _list(item.get("grounding_refs")) if str(value).strip()]
            if not grounding_refs:
                errors.append(f"{section_key}.{item_key} has no grounding_refs")
            invalid = sorted(set(grounding_refs) - set(refs))
            if invalid:
                errors.append(f"{section_key}.{item_key} has invalid grounding_refs: {invalid}")
            section_refs.extend(ref for ref in grounding_refs if ref in refs)
        prefixes = required_prefixes[section_key]
        if section_refs and not any(refs[ref].startswith(prefixes) for ref in section_refs):
            errors.append(f"{section_key} has no grounding ref for its required evidence domain")
    if errors:
        notes.extend(errors)
    return _pass_fail(not errors)


def _validate_card_key_coverage(
    report_payload: dict[str, Any],
    writer_handoff: dict[str, Any],
    notes: list[str],
) -> str:
    if not _is_v2_writer_packet(writer_handoff):
        return "pass"
    required = _dict(writer_handoff.get("required_card_keys_by_component"))
    sections = _dict(report_payload.get("sections"))
    errors: list[str] = []
    for section in REPORT_SECTIONS:
        component = section["key"]
        expected = set(_text_list(required.get(component)))
        actual: set[str] = set()
        for item_key, _title, _item_type in section["items"]:
            item = _dict(_dict(sections.get(component)).get(item_key))
            raw_keys = _text_list(item.get("card_keys"))
            if len(raw_keys) != len(set(raw_keys)):
                errors.append(f"{component}.{item_key} contains duplicate card_keys")
            actual.update(raw_keys)
        if actual != expected:
            errors.append(
                f"{component} card coverage mismatch: expected={sorted(expected)}, actual={sorted(actual)}"
            )
    if errors:
        notes.extend(errors)
    return _pass_fail(not errors)


def _validate_strategy_meaning_preservation(
    report_payload: dict[str, Any],
    writer_handoff: dict[str, Any],
    notes: list[str],
) -> str:
    if not _is_v2_writer_packet(writer_handoff):
        return "pass"
    errors: list[str] = []
    cards = _dict(writer_handoff.get("cards"))
    label_free = _is_label_free_writer_packet(writer_handoff)
    required = _dict(writer_handoff.get("required_card_keys_by_component"))
    sections = _dict(report_payload.get("sections"))
    evidence_item = _dict(_dict(sections.get("key_evidence_table")).get("evidence_table"))
    evidence_rows = [row for row in _list(evidence_item.get("rows")) if isinstance(row, dict)]
    expected_evidence_keys = _text_list(required.get("key_evidence_table"))
    actual_evidence_keys = [str(row.get("_card_key") or "") for row in evidence_rows]
    if len(actual_evidence_keys) != len(set(actual_evidence_keys)):
        errors.append("key_evidence_table contains duplicate card rows")
    if set(actual_evidence_keys) != set(expected_evidence_keys):
        errors.append(
            "key_evidence_table row card coverage mismatch: "
            f"expected={sorted(expected_evidence_keys)}, actual={sorted(actual_evidence_keys)}"
        )
    for row in evidence_rows:
        card_key = str(row.get("_card_key") or "")
        card = _dict(cards.get(card_key))
        if not card:
            errors.append(f"key_evidence_table references unknown card: {card_key!r}")
            continue
        interpretation = str(card.get("strategy_interpretation") or "")
        if row.get("_strategy_interpretation") != interpretation:
            errors.append(f"Strategy interpretation metadata changed: {card_key}")
        if label_free:
            if row.get("_strategy_role") != card.get("strategy_role"):
                errors.append(f"Strategy role metadata changed: {card_key}")
        elif row.get("_investment_effect") != card.get("investment_effect"):
            errors.append(f"Investment effect metadata changed: {card_key}")
        if not str(row.get("확인된 수치·사실") or "").strip():
            errors.append(f"Key evidence observation is empty: {card_key}")

    risk_item = _dict(_dict(sections.get("risk_monitoring_matrix")).get("risk_monitoring_table"))
    risk_rows = [row for row in _list(risk_item.get("rows")) if isinstance(row, dict)]
    risk_factors = [risk for risk in _list(writer_handoff.get("risk_factors")) if isinstance(risk, dict)]
    if len(risk_rows) != len(risk_factors):
        errors.append(f"risk row count mismatch: expected={len(risk_factors)}, actual={len(risk_rows)}")
    for index, (row, risk) in enumerate(zip(risk_rows, risk_factors)):
        expected_basis = _text_list(risk.get("basis_card_keys"))
        actual_basis = _text_list(row.get("_basis_card_keys"))
        expected_summary = str(risk.get("risk_summary") or "").strip()
        if actual_basis != expected_basis:
            errors.append(f"risk row {index} basis card keys changed")
        if str(row.get("_strategy_risk_summary") or "").strip() != expected_summary:
            errors.append(f"risk row {index} Strategy summary metadata changed")

    decision = _dict(writer_handoff.get("decision"))
    negative_keys = set(_text_list(decision.get("negative_factor_card_keys")))
    thesis_keys = set(_text_list(required.get("investment_call_thesis")))
    risk_keys = {
        key
        for risk in risk_factors
        for key in _text_list(risk.get("basis_card_keys"))
    }
    unaligned = sorted(negative_keys - thesis_keys - risk_keys)
    if unaligned:
        errors.append(f"Negative decision factor is absent from thesis and risk matrix: {unaligned}")

    horizon = str(decision.get("investment_horizon") or "").strip()
    payload_horizon = str(_dict(report_payload.get("metadata")).get("investment_horizon") or "").strip()
    if not horizon or horizon != payload_horizon:
        errors.append("Investment horizon is absent or changed in Writer metadata")
    if errors:
        notes.extend(errors)
    return _pass_fail(not errors)


def _validate_strategy_presentation_preservation(
    report_payload: dict[str, Any],
    writer_handoff: dict[str, Any],
    notes: list[str],
) -> str:
    """Advise on exact labels/order without rejecting a semantically linked report."""

    if not _is_v2_writer_packet(writer_handoff):
        return "pass"
    errors: list[str] = []
    cards = _dict(writer_handoff.get("cards"))
    label_free = _is_label_free_writer_packet(writer_handoff)
    required = _dict(writer_handoff.get("required_card_keys_by_component"))
    sections = _dict(report_payload.get("sections"))
    evidence_item = _dict(_dict(sections.get("key_evidence_table")).get("evidence_table"))
    evidence_rows = [row for row in _list(evidence_item.get("rows")) if isinstance(row, dict)]
    expected_columns = (
        LABEL_FREE_KEY_EVIDENCE_DISPLAY_COLUMNS
        if label_free
        else KEY_EVIDENCE_DISPLAY_COLUMNS
    )
    if _text_list(evidence_item.get("columns")) != list(expected_columns):
        errors.append("key_evidence_table display columns changed")
    expected_evidence_keys = _text_list(required.get("key_evidence_table"))
    actual_evidence_keys = [str(row.get("_card_key") or "") for row in evidence_rows]
    if actual_evidence_keys != expected_evidence_keys:
        errors.append(
            "key_evidence_table row order changed: "
            f"expected={expected_evidence_keys}, actual={actual_evidence_keys}"
        )
    for row in evidence_rows:
        card_key = str(row.get("_card_key") or "")
        card = _dict(cards.get(card_key))
        if not card:
            continue
        interpretation = str(card.get("strategy_interpretation") or "")
        expected_visible_interpretation = _plain_korean_text(
            _qualify_partial_product_scope_v2(
                interpretation,
                writer_handoff,
                [card_key],
            )
        ).strip()
        if str(row.get("투자 해석") or "").strip() != expected_visible_interpretation:
            errors.append(f"Visible Strategy interpretation was paraphrased: {card_key}")
        if label_free:
            expected_role_label = _strategy_role_label(card.get("strategy_role"))
            if str(row.get("판단상 역할") or "").strip() != expected_role_label:
                errors.append(f"Visible Strategy role label changed: {card_key}")
        else:
            expected_effect_label = _effect_label(str(card.get("investment_effect") or ""))
            if str(row.get("영향") or "").strip() != expected_effect_label:
                errors.append(f"Visible investment effect label changed: {card_key}")

    risk_item = _dict(_dict(sections.get("risk_monitoring_matrix")).get("risk_monitoring_table"))
    risk_rows = [row for row in _list(risk_item.get("rows")) if isinstance(row, dict)]
    if _text_list(risk_item.get("columns")) != list(RISK_DISPLAY_COLUMNS):
        errors.append("risk_monitoring_matrix display columns changed")
    risk_factors = [risk for risk in _list(writer_handoff.get("risk_factors")) if isinstance(risk, dict)]
    for index, (row, risk) in enumerate(zip(risk_rows, risk_factors)):
        if label_free:
            expected_title = str(risk.get("display_title") or "").strip()
            if str(row.get("리스크 요인") or "").strip() != expected_title:
                errors.append(f"risk row {index} Strategy risk title changed")
        expected_summary = str(risk.get("risk_summary") or "").strip()
        reader_summary = str(risk.get("reader_summary") or expected_summary).strip()
        qualifier = str(risk.get("scope_qualifier") or "").strip()
        expected_visible_summary = (
            f"{qualifier}: {reader_summary}"
            if qualifier
            and qualifier != "not_applicable"
            and qualifier not in reader_summary
            else reader_summary
        )
        expected_visible_summary = _plain_korean_text(expected_visible_summary)
        if str(row.get("현재 확인된 내용") or "").strip() != expected_visible_summary:
            errors.append(f"risk row {index} visible Strategy summary was paraphrased")
        expected_impact = _plain_korean_text(
            str(risk.get("current_implication") or risk.get("monitoring_point") or "").strip()
        )
        if str(row.get("투자 판단에 미치는 영향") or "").strip() != expected_impact:
            errors.append(f"risk row {index} investment impact was paraphrased")

    horizon = str(_dict(writer_handoff.get("decision")).get("investment_horizon") or "").strip()
    thesis_text = str(_dict(_dict(sections.get("investment_call_thesis")).get("section_analysis")))
    if horizon and horizon not in thesis_text:
        errors.append("Investment horizon is not repeated in the thesis text")
    if errors:
        notes.extend(errors)
    return _pass_fail(not errors)


def _validate_claim_card_grounding(
    report_payload: dict[str, Any],
    writer_handoff: dict[str, Any],
    notes: list[str],
) -> str:
    if not _is_v2_writer_packet(writer_handoff):
        return "pass"
    sections = _dict(report_payload.get("sections"))
    errors: list[str] = []
    for section in REPORT_SECTIONS:
        component = section["key"]
        for item_key, _title, item_type in section["items"]:
            if item_type == "table":
                continue
            item = _dict(_dict(sections.get(component)).get(item_key))
            paragraphs = [str(value).strip() for value in _list(item.get("paragraphs")) if str(value).strip()]
            units = [unit for unit in _list(item.get("_claim_units")) if isinstance(unit, dict)]
            if paragraphs and not units:
                errors.append(f"{component}.{item_key} has no _claim_units")
                continue
            used_keys: set[str] = set()
            declared_keys = set(_text_list(item.get("card_keys")))
            for index, unit in enumerate(units):
                claim = re.sub(r"<[^>]+>", "", str(unit.get("claim") or "")).strip()
                keys = set(_text_list(unit.get("card_keys")))
                used_keys.update(keys)
                unknown = sorted(keys - declared_keys)
                if not claim:
                    errors.append(f"{component}.{item_key} claim unit {index} is empty")
                if unknown:
                    errors.append(
                        f"{component}.{item_key} claim unit {index} uses unauthorized cards: {unknown}"
                    )
            if used_keys != declared_keys:
                errors.append(
                    f"{component}.{item_key} claim card coverage mismatch: "
                    f"expected={sorted(declared_keys)}, actual={sorted(used_keys)}"
                )
    if errors:
        notes.extend(errors)
    return _pass_fail(not errors)


def _validate_claim_visibility(
    report_payload: dict[str, Any],
    writer_handoff: dict[str, Any],
    notes: list[str],
) -> str:
    """Advise when hidden claim units do not exactly match rendered prose."""

    if not _is_v2_writer_packet(writer_handoff):
        return "pass"
    sections = _dict(report_payload.get("sections"))
    errors: list[str] = []
    for section in REPORT_SECTIONS:
        component = section["key"]
        for item_key, _title, item_type in section["items"]:
            if item_type == "table":
                continue
            item = _dict(_dict(sections.get(component)).get(item_key))
            paragraphs = [
                re.sub(r"<[^>]+>", "", str(value)).strip()
                for value in _list(item.get("paragraphs"))
                if str(value).strip()
            ]
            unit_claims = [
                re.sub(r"<[^>]+>", "", str(unit.get("claim") or "")).strip()
                for unit in _list(item.get("_claim_units"))
                if isinstance(unit, dict)
            ]
            visible_text = " ".join(paragraphs)
            for index, claim in enumerate(unit_claims):
                if claim and claim not in visible_text:
                    errors.append(
                        f"{component}.{item_key} claim unit {index} is not visible verbatim"
                    )
            for index, paragraph in enumerate(paragraphs):
                if not any(claim and claim in paragraph for claim in unit_claims):
                    errors.append(
                        f"{component}.{item_key} paragraph {index} has no verbatim claim-unit match"
                    )
    if errors:
        notes.extend(errors)
    return _pass_fail(not errors)


def _validate_required_limitation_coverage(
    report_payload: dict[str, Any],
    writer_handoff: dict[str, Any],
    notes: list[str],
) -> str:
    if not _is_v2_writer_packet(writer_handoff):
        return "pass"
    required = [
        str(item.get("category"))
        for item in _list(writer_handoff.get("required_limitations"))
        if isinstance(item, dict) and item.get("category")
    ]
    item = _dict(
        _dict(_dict(report_payload.get("sections")).get("data_limits")).get("section_analysis")
    )
    declared = _text_list(item.get("_limitation_categories"))
    units = [unit for unit in _list(item.get("_claim_units")) if isinstance(unit, dict)]
    covered = list(
        dict.fromkeys(
            category
            for unit in units
            for category in _text_list(unit.get("limitation_categories"))
        )
    )
    errors = []
    if declared != required:
        errors.append(f"Data-limit category order/coverage mismatch: expected={required}, actual={declared}")
    if set(covered) != set(required):
        errors.append(
            f"Data-limit claim coverage mismatch: expected={sorted(required)}, actual={sorted(covered)}"
        )
    if errors:
        notes.extend(errors)
    return _pass_fail(not errors)


def _validate_internal_metadata_hidden(
    report_payload: dict[str, Any],
    html_content: str,
    writer_handoff: dict[str, Any],
    notes: list[str],
) -> str:
    reader_html = re.sub(
        r"(?is)(?P<prefix>\bsrc\s*=\s*(?P<quote>[\"']))data:image/.*?(?P=quote)",
        r"\g<prefix>[embedded image]\g<quote>",
        html_content,
    )
    visible = f"{_reader_payload_text(report_payload)}\n{reader_html}"
    blocked_terms = [
        "required_key_evidence",
        "grounding_ref_map",
        "claim_ledger",
        "_card_key",
        "_strategy_interpretation",
        "_investment_effect",
        "_strategy_role",
        "_basis_card_keys",
        "_strategy_risk_summary",
        "_claim_units",
        "_limitation_categories",
    ]
    found = [term for term in blocked_terms if term in reader_html]
    raw_ids = sorted(
        set(
            re.findall(
                r"(?<![A-Za-z0-9_])(?:OP|NCLAIM|PEER_METRIC|NEWS_RAW|CLAIM|E|F)\d{3,}(?![A-Za-z0-9_])",
                visible,
                flags=re.IGNORECASE,
            )
        )
    )
    leaked_card_keys = []
    if _is_v2_writer_packet(writer_handoff):
        leaked_card_keys = sorted(
            card_key
            for card_key in _dict(writer_handoff.get("cards"))
            if card_key and card_key in reader_html
        )
    if found:
        notes.append(f"Internal metadata field(s) rendered: {found}")
    if raw_ids:
        notes.append(f"Opaque internal ID(s) remain in reader-visible output: {raw_ids}")
    if leaked_card_keys:
        notes.append(f"Semantic card key(s) rendered: {leaked_card_keys}")
    return _pass_fail(not found and not raw_ids and not leaked_card_keys)


def _validate_required_evidence_coverage(
    report_payload: dict[str, Any],
    writer_handoff: dict[str, Any],
    notes: list[str],
) -> str:
    if _is_v2_writer_packet(writer_handoff):
        return "pass"
    serialized = _reader_payload_text(report_payload)
    missing: list[str] = []
    revenue = _dict(writer_handoff.get("revenue_breakdown"))
    if revenue.get("status") == "available":
        for item in _list(revenue.get("current_items")):
            if not isinstance(item, dict):
                continue
            for value in (item.get("name"), item.get("revenue_disclosed"), item.get("revenue_share_disclosed")):
                text = str(value or "").strip()
                if text and text not in serialized:
                    missing.append(f"revenue_breakdown:{text}")
    valuation = _dict(_dict(writer_handoff.get("valuation")).get("calculated_from_close_and_dart"))
    if valuation.get("status") == "available":
        valuation_date = str(valuation.get("as_of_date") or "").strip()
        if valuation_date and valuation_date not in serialized:
            missing.append(f"valuation_date:{valuation_date}")
        metric_names = {
            "trailing_pe": "P/E",
            "price_to_sales": "P/S",
            "price_to_book": "P/B",
        }
        metrics = _dict(valuation.get("metrics"))
        for key, label in metric_names.items():
            metric = _dict(metrics.get(key))
            value = metric.get("value")
            if value is None:
                continue
            rounded = f"{float(value):.2f}"
            if rounded not in serialized:
                missing.append(f"valuation:{label} {rounded}")
    peer_metrics = _list(_dict(writer_handoff.get("peer_comparison")).get("metrics"))
    for metric in peer_metrics:
        if not isinstance(metric, dict):
            continue
        company_name = str(metric.get("company_name") or "").strip()
        if company_name and company_name not in serialized:
            missing.append(f"peer_company:{company_name}")
    if missing:
        notes.append(f"Required handoff evidence is absent from the report payload: {missing}")
    return _pass_fail(not missing)


def _validate_compact_text_sections(
    report_payload: dict[str, Any],
    writer_handoff: dict[str, Any],
    notes: list[str],
) -> str:
    errors: list[str] = []
    total_chars = 0
    sections = _dict(report_payload.get("sections"))
    for section in REPORT_SECTIONS:
        section_payload = _dict(sections.get(section["key"]))
        for item_key, _title, item_type in section["items"]:
            if item_type == "table":
                continue
            item = _dict(section_payload.get(item_key))
            paragraphs = [str(value).strip() for value in _list(item.get("paragraphs")) if str(value).strip()]
            bullets = [str(value).strip() for value in _list(item.get("bullets")) if str(value).strip()]
            total_chars += sum(len(re.sub(r"<[^>]+>", "", paragraph)) for paragraph in paragraphs)
            max_paragraphs = TEXT_PARAGRAPH_LIMITS.get(section["key"], 2)
            if not 1 <= len(paragraphs) <= max_paragraphs:
                errors.append(
                    f"{section['key']}.{item_key} must contain 1-{max_paragraphs} paragraphs"
                )
            if bullets:
                errors.append(f"{section['key']}.{item_key} bullets must be empty")
    character_budget = (
        4_200
        if _dict(writer_handoff).get("packet_version") == EDITORIAL_PACKET_VERSION_V3
        else 3_200
    )
    if total_chars > character_budget:
        errors.append(
            f"text section character budget exceeded: {total_chars} > {character_budget}"
        )
    if errors:
        notes.extend(errors)
    return _pass_fail(not errors)


def _validate_large_number_grounding(
    report_payload: dict[str, Any],
    writer_handoff: dict[str, Any],
    notes: list[str],
) -> str:
    known = _collect_large_integers(writer_handoff)
    unknown = [token for token, value in _large_integer_tokens(_reader_payload_text(report_payload)) if value not in known]
    if unknown:
        notes.append(f"Ungrounded large integer value(s): {sorted(set(unknown))}")
    return _pass_fail(not unknown)


def _validate_no_absolute_paths(report_payload: dict[str, Any], html_content: str, notes: list[str]) -> str:
    serialized = f"{_reader_payload_text(report_payload)}\n{html_content}"
    paths = sorted(set(re.findall(r"/(?:home|Users|tmp)/[^\s<>'\"]+", serialized)))
    if paths:
        notes.append(f"Absolute path(s) remain: {paths}")
    return _pass_fail(not paths)


def _html_has_id(html_content: str, element_id: str) -> bool:
    return bool(
        re.search(
            rf"\bid\s*=\s*(['\"]){re.escape(element_id)}\1",
            html_content,
            flags=re.IGNORECASE,
        )
    )


def _section_id(section_key: str) -> str:
    return next(
        str(section["id"])
        for section in REPORT_SECTIONS
        if section["key"] == section_key
    )


def _html_heading_text(html_content: str, element_id: str) -> str:
    match = re.search(
        rf"<h2\b(?=[^>]*\bid\s*=\s*(['\"]){re.escape(element_id)}\1)[^>]*>(.*?)</h2\s*>",
        html_content,
        flags=re.IGNORECASE | re.DOTALL,
    )
    return _visible_heading_text(match.group(2)) if match else ""


def _html_heading_texts(html_content: str, *, level: int) -> list[str]:
    return [
        _visible_heading_text(value)
        for value in re.findall(
            rf"<h{level}\b[^>]*>(.*?)</h{level}\s*>",
            html_content,
            flags=re.IGNORECASE | re.DOTALL,
        )
    ]


def _visible_heading_text(value: str) -> str:
    return " ".join(unescape(re.sub(r"<[^>]+>", "", value)).split())


def _visible_html_text(html_content: str) -> str:
    without_noncontent = re.sub(
        r"<(?:style|script)\b[^>]*>.*?</(?:style|script)\s*>",
        " ",
        html_content,
        flags=re.IGNORECASE | re.DOTALL,
    )
    return " ".join(unescape(re.sub(r"<[^>]+>", " ", without_noncontent)).split())


def _reader_payload_text(report_payload: dict[str, Any]) -> str:
    return str(_visible_value({"metadata": report_payload.get("metadata"), "sections": report_payload.get("sections")}))


def _visible_value(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            key: _visible_value(child)
            for key, child in value.items()
            if not str(key).startswith("_") and key not in {"card_keys", "grounding_refs"}
        }
    if isinstance(value, list):
        return [_visible_value(child) for child in value]
    return value


def _collect_keys(value: Any) -> set[str]:
    keys: set[str] = set()
    if isinstance(value, dict):
        keys.update(str(key) for key in value)
        children = value.values()
    elif isinstance(value, list):
        children = value
    else:
        return keys
    for child in children:
        keys.update(_collect_keys(child))
    return keys


def _collect_large_integers(value: Any) -> set[int]:
    values: set[int] = set()
    if isinstance(value, bool) or value is None:
        return values
    if isinstance(value, int):
        if abs(value) >= 100_000_000:
            values.add(value)
        return values
    if isinstance(value, float):
        if value.is_integer() and abs(value) >= 100_000_000:
            values.add(int(value))
        return values
    if isinstance(value, str):
        values.update(number for _token, number in _large_integer_tokens(value))
        return values
    if isinstance(value, dict):
        for child in value.values():
            values.update(_collect_large_integers(child))
        return values
    if isinstance(value, list):
        for child in value:
            values.update(_collect_large_integers(child))
    return values


def _large_integer_tokens(value: Any) -> list[tuple[str, int]]:
    tokens: list[tuple[str, int]] = []
    for match in re.finditer(r"(?<![\d.,])[-+]?\d[\d,]*(?![\d.,])", str(value)):
        token = match.group(0)
        digits = token.lstrip("+-")
        if "," in digits and not re.fullmatch(r"\d{1,3}(?:,\d{3})+", digits):
            continue
        number = int(token.replace(",", ""))
        if abs(number) >= 100_000_000:
            tokens.append((token, number))
    return tokens


def _dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _text_list(value: Any) -> list[str]:
    return [str(item).strip() for item in _list(value) if str(item).strip()]


def _is_v2_writer_packet(value: Any) -> bool:
    return isinstance(value, dict) and value.get("packet_version") in {
        EDITORIAL_PACKET_VERSION,
        EDITORIAL_PACKET_VERSION_V3,
    }


def _is_label_free_writer_packet(value: Any) -> bool:
    return (
        _is_v2_writer_packet(value)
        and value.get("strategy_contract_version") in {
            "strategy_decision_output_v4",
            "strategy_decision_output_v5",
        }
    )


def _effect_label(value: Any) -> str:
    return {
        "positive": "긍정 요인",
        "negative": "부담 요인",
        "mixed": "혼합",
        "neutral": "중립",
        "reference": "참고",
    }.get(str(value or ""), "참고")


def _pass_fail(condition: bool) -> str:
    return "pass" if condition else "fail"
