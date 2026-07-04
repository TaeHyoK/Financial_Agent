"""Quality scoring for Writer Agent report contracts and HTML previews."""

from __future__ import annotations

import re
from difflib import SequenceMatcher
from typing import Any


INTERNAL_TERMS = [
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

UNSUPPORTED_TERMS = ["적정주가", "P/E", "P/B", "OPM", "ROE"]


def validate_report_quality(
    *,
    contract: dict[str, Any],
    strategy_report: dict[str, Any],
    html_content: str = "",
    embed_images: bool = False,
) -> dict[str, Any]:
    """Return deterministic quality score for the generated report."""

    warnings: list[str] = []
    visible_html = _visible_html_text(html_content)
    checks = {
        "has_main_investment_logic": _has_main_logic(contract),
        "has_section_investment_implications": _has_section_implications(contract),
        "has_view_change_conditions": _has_view_change_conditions(contract),
        "uses_report_friendly_labels": _uses_report_friendly_labels(visible_html),
        "avoids_internal_agent_terms": _avoids_internal_terms(visible_html),
        "reduces_repetitive_recommendation_language": _reduces_repetitive_recommendation_language(contract, visible_html),
        "avoids_unsupported_metrics": _avoids_unsupported_metrics(contract, visible_html),
        "html_image_mode_matches_option": _html_image_mode_matches_option(html_content, embed_images, warnings),
        "has_stability_metrics_when_claimed": _has_stability_metrics_when_claimed(contract, visible_html, warnings),
        "has_v3_interpretation_tasks": _has_v3_interpretation_tasks(contract, warnings),
        "chart_takeaways_answer_investment_tasks": _chart_takeaways_answer_investment_tasks(contract, warnings),
        "llm_commentary_priority_when_available": _llm_commentary_priority_when_available(contract, warnings),
        "avoids_awkward_sentence_patterns": _avoids_awkward_sentence_patterns(visible_html, warnings),
        "keeps_basis_mismatch_warning": "동일 기간 YoY로 단정" in str(contract) or "집계 기준" in str(contract),
        "keeps_price_signal_limitation": "펀더멘털 개선의 직접 증거" in str(contract),
        "avoids_raw_strategy_copy": not _has_raw_copy(contract, strategy_report, warnings),
    }
    section_scores = {
        "investment_summary": _score_investment_summary(contract),
        "financial_view": _score_card_section(contract, "financial_view_cards"),
        "market_view": _score_card_section(contract, "market_view_cards"),
        "catalyst_analysis": _score_catalysts(contract),
        "risk_matrix": _score_risks(contract),
        "peer_comparison": _score_peer_comparison(contract),
        "final_rationale": _score_final_rationale(contract),
    }
    overall = round((sum(section_scores.values()) / len(section_scores)) * 0.7 + (sum(checks.values()) / len(checks)) * 30)
    if not checks["avoids_unsupported_metrics"]:
        overall = min(overall, 69)
    if not checks["avoids_internal_agent_terms"]:
        overall = min(overall, 84)
    if overall < 85:
        warnings.append(f"Overall quality score below target: {overall}")
    return {
        "overall_quality_score": overall,
        "section_scores": section_scores,
        "checks": checks,
        "warnings": warnings,
    }


def _has_main_logic(contract: dict[str, Any]) -> bool:
    logic = contract.get("main_investment_logic", "")
    company = contract.get("report_metadata", {}).get("company_name", "")
    recommendation = contract.get("report_metadata", {}).get("recommendation", "")
    return bool(company and recommendation and company in logic and str(recommendation) in logic)


def _has_section_implications(contract: dict[str, Any]) -> bool:
    reader = contract.get("reader_friendly_sections", {})
    cards = reader.get("financial_view_cards", []) + reader.get("market_view_cards", [])
    if not all(card.get("investment_implication") for card in cards):
        return False
    final = reader.get("final_rationale", {})
    return bool(final.get("investment_implication"))


def _has_view_change_conditions(contract: dict[str, Any]) -> bool:
    conditions = contract.get("reader_friendly_sections", {}).get("final_rationale", {}).get("view_change_conditions", {})
    return bool(conditions.get("upside_conditions")) and bool(conditions.get("downside_conditions"))


def _uses_report_friendly_labels(html_content: str) -> bool:
    if not html_content:
        return True
    required = [
        "투자 요약",
        "핵심 차트",
        "재무 분석",
        "주가 및 시장 해석",
        "성장 촉매 분석",
        "주요 리스크",
        "최종 투자의견 근거",
        "투자의견 변경 조건",
        "확인된 지표",
        "투자 판단상 의미",
        "확인 필요 요인",
        "투자의견 시사점",
    ]
    return all(label in html_content for label in required)


def _avoids_internal_terms(html_content: str) -> bool:
    return not any(term in html_content for term in INTERNAL_TERMS)


def _html_image_mode_matches_option(html_content: str, embed_images: bool, warnings: list[str]) -> bool:
    if not html_content:
        return True
    has_embedded = "src=\"data:image/" in html_content
    if has_embedded and not embed_images:
        warnings.append("Base64 image data found while embed_images is false.")
        return False
    return True


def _llm_commentary_priority_when_available(contract: dict[str, Any], warnings: list[str]) -> bool:
    llm_status = str(contract.get("llm_writer", {}).get("status", "")).strip().lower()
    if llm_status != "applied":
        return True
    generation = contract.get("commentary_generation", {})
    if not isinstance(generation, dict):
        warnings.append("LLM Writer applied but commentary_generation metadata is missing.")
        return False
    mode = str(generation.get("mode", ""))
    sections = generation.get("llm_sections_updated", [])
    required = {
        "cover_summary",
        "visual_report_blocks",
        "reader_friendly_sections.final_rationale",
    }
    if "llm_first" not in mode:
        warnings.append("LLM Writer applied but commentary_generation is not marked as llm_first.")
        return False
    if not isinstance(sections, list) or not required.issubset(set(sections)):
        warnings.append("LLM Writer did not update the core commentary sections expected for final interpretation.")
        return False
    serialized = str(
        {
            "visual_report_blocks": contract.get("visual_report_blocks", []),
            "peer_comparison": contract.get("peer_comparison", {}),
            "reader_friendly_sections": contract.get("reader_friendly_sections", {}),
        }
    )
    banned_generic_phrases = ["보조 근거", "공격적 재평가", "긍정적이나 제한적"]
    found = [phrase for phrase in banned_generic_phrases if phrase in serialized]
    if found:
        warnings.append(f"LLM-first commentary still contains generic fallback phrases: {', '.join(found)}")
        return False
    return True


def _has_stability_metrics_when_claimed(contract: dict[str, Any], html_content: str, warnings: list[str]) -> bool:
    scan_text = _report_body_text(contract) + " " + html_content
    stability_claimed = any(term in scan_text for term in ["재무 안정성", "자본 구조", "유동성", "영업현금흐름"])
    if not stability_claimed:
        return True
    metric_names = {metric.get("metric_name") for metric in contract.get("key_metrics_table", {}).get("metrics", [])}
    stability_metrics = {"Debt Ratio", "Current Ratio", "Operating Cash Flow", "Cash & Cash Equivalents"}
    ok = bool(metric_names & stability_metrics)
    if not ok:
        warnings.append("Financial stability is discussed but no stability metric is present in Key Metrics.")
    return ok


def _has_v3_interpretation_tasks(contract: dict[str, Any], warnings: list[str]) -> bool:
    tasks = contract.get("interpretation_tasks", {})
    chart_tasks = tasks.get("chart_tasks", []) if isinstance(tasks, dict) else []
    expected = len(contract.get("visual_report_blocks", []))
    peer = contract.get("peer_comparison", {})
    if isinstance(peer, dict) and peer.get("enabled"):
        expected += len(peer.get("peer_chart_blocks", []))
    ok = isinstance(chart_tasks, list) and len(chart_tasks) >= expected and expected > 0
    if not ok:
        warnings.append("v3 interpretation task layer is missing or incomplete.")
    return ok


def _chart_takeaways_answer_investment_tasks(contract: dict[str, Any], warnings: list[str]) -> bool:
    recommendation = str(contract.get("report_metadata", {}).get("recommendation", "")).strip()
    task_tokens = ["투자", "판단", "의견", "근거", "확인", "상향", "유지"]
    blocks = list(contract.get("visual_report_blocks", []))
    peer = contract.get("peer_comparison", {})
    if isinstance(peer, dict) and peer.get("enabled"):
        blocks.extend(peer.get("peer_chart_blocks", []))
    weak = []
    for block in blocks:
        if not isinstance(block, dict):
            continue
        takeaway = str(block.get("analyst_takeaway") or "")
        if len(takeaway) < 60:
            weak.append(block.get("figure_id", "unknown"))
            continue
        if recommendation and recommendation not in takeaway and not any(token in takeaway for token in task_tokens):
            weak.append(block.get("figure_id", "unknown"))
    if weak:
        warnings.append(f"Chart takeaway(s) weakly connected to investment tasks: {weak[:4]}")
    return not weak


def _avoids_awkward_sentence_patterns(html_content: str, warnings: list[str]) -> bool:
    patterns = [
        "투자 의견을 적극적 비중 확대에는",
        "Buy로 높이기에는",
        "전환 근거로는 부족",
    ]
    found = [pattern for pattern in patterns if pattern in html_content]
    if found:
        warnings.append(f"Awkward sentence pattern(s) found: {found}")
    return not found


def _reduces_repetitive_recommendation_language(contract: dict[str, Any], html_content: str) -> bool:
    if not html_content:
        return True
    text = re.sub(r"<[^>]+>", " ", html_content)
    recommendation = str(contract.get("report_metadata", {}).get("recommendation", "")).strip()
    if not recommendation or recommendation == "N/A":
        return True
    return text.count(f"{recommendation} 유지") <= 2 and text.count(f"{recommendation} 의견") <= 5


def _avoids_unsupported_metrics(contract: dict[str, Any], html_content: str) -> bool:
    scan_text = _report_body_text(contract) + " " + html_content
    return not any(term in scan_text for term in UNSUPPORTED_TERMS)


def _has_raw_copy(contract: dict[str, Any], strategy_report: dict[str, Any], warnings: list[str]) -> bool:
    strategy_tokens = _tokens(str(strategy_report))
    writer_tokens = _tokens(_report_body_text(contract))
    if len(strategy_tokens) < 15 or len(writer_tokens) < 15:
        return False
    strategy_ngrams = {" ".join(strategy_tokens[index : index + 15]) for index in range(len(strategy_tokens) - 14)}
    for index in range(len(writer_tokens) - 14):
        phrase = " ".join(writer_tokens[index : index + 15])
        if phrase in strategy_ngrams:
            warnings.append(f"Raw-copy sequence detected: {phrase}")
            return True
    strategy_sentences = _sentences(str(strategy_report))
    for sentence in _sentences(_report_body_text(contract)):
        if len(sentence) < 40:
            continue
        ratio = max((SequenceMatcher(None, sentence, source).ratio() for source in strategy_sentences), default=0)
        if ratio >= 0.8:
            warnings.append(f"High copy-ratio sentence detected: {sentence[:80]}")
            return True
    return False


def _score_investment_summary(contract: dict[str, Any]) -> int:
    cover = str(contract.get("cover_summary", {}))
    recommendation = str(contract.get("report_metadata", {}).get("recommendation", "")).strip()
    score = 70
    for token in [item for item in [recommendation, "EPS", "규제", "상대성과", "리스크"] if item]:
        if token in cover:
            score += 5
    return min(score, 100)


def _report_body_text(contract: dict[str, Any]) -> str:
    return str(
        {
            "cover_summary": contract.get("cover_summary", {}),
            "key_metrics_table": contract.get("key_metrics_table", {}),
            "visual_report_blocks": contract.get("visual_report_blocks", {}),
            "peer_comparison": contract.get("peer_comparison", {}),
            "reader_friendly_sections": contract.get("reader_friendly_sections", {}),
            "limitations": contract.get("limitations", {}),
        }
    )


def _score_card_section(contract: dict[str, Any], key: str) -> int:
    cards = contract.get("reader_friendly_sections", {}).get(key, [])
    if not cards:
        return 0
    score = 75
    if all(card.get("investment_implication") for card in cards):
        score += 10
    if all(card.get("what_to_watch") for card in cards):
        score += 10
    if all(len(card.get("why_it_matters", "")) > 70 for card in cards):
        score += 5
    return min(score, 100)


def _score_catalysts(contract: dict[str, Any]) -> int:
    cards = contract.get("reader_friendly_sections", {}).get("catalyst_analysis_cards", [])
    if not cards:
        return 0
    fields = ["investment_relevance", "evidence_from_strategy", "what_to_watch", "investment_impact"]
    return 95 if all(all(card.get(field) for field in fields) for card in cards) else 75


def _score_risks(contract: dict[str, Any]) -> int:
    cards = contract.get("reader_friendly_sections", {}).get("risk_cards", [])
    if not cards:
        return 0
    fields = ["description", "impact", "monitoring_point", "hold_connection"]
    return 95 if all(all(card.get(field) for field in fields) for card in cards) else 75


def _score_peer_comparison(contract: dict[str, Any]) -> int:
    peer = contract.get("peer_comparison", {})
    if not isinstance(peer, dict) or not peer.get("enabled"):
        return 95
    score = 70
    if peer.get("peer_investment_commentary"):
        score += 8
    if peer.get("relative_positioning_summary"):
        score += 6
    cards = peer.get("analysis_cards", [])
    if isinstance(cards, list) and len(cards) >= 3 and all(card.get("body") for card in cards if isinstance(card, dict)):
        score += 8
    chart_blocks = peer.get("peer_chart_blocks", [])
    if isinstance(chart_blocks, list) and chart_blocks and all(block.get("analyst_takeaway") for block in chart_blocks if isinstance(block, dict)):
        score += 8
    return min(score, 100)


def _score_final_rationale(contract: dict[str, Any]) -> int:
    final = contract.get("reader_friendly_sections", {}).get("final_rationale", {})
    fields = ["positive_case", "caution_case", "balance_of_evidence", "investment_conclusion", "view_change_conditions"]
    return 98 if all(final.get(field) for field in fields) else 70


def _tokens(text: str) -> list[str]:
    normalized = re.sub(r"[^\w가-힣.%+-]+", " ", text)
    return [token for token in normalized.split() if token]


def _visible_html_text(html_content: str) -> str:
    without_data = re.sub(r'data:image/[^"]+', "", html_content)
    without_tags = re.sub(r"<[^>]+>", " ", without_data)
    return " ".join(without_tags.split())


def _sentences(text: str) -> list[str]:
    normalized = " ".join(text.replace("\\n", " ").split())
    return [part.strip() for part in re.split(r"(?<=[.!?。다])\s+", normalized) if part.strip()]
