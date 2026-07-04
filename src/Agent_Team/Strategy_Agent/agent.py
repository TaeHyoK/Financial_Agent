"""LLM-based Strategy Agent for final Buy/Hold/Sell synthesis."""

from __future__ import annotations

import json
import os
import re
from datetime import datetime
from pathlib import Path
from typing import Any
from urllib import error, request

from . import AGENT_DIR, DEFAULT_TARGET_CONFIG, OUTPUT_ROOT


OUTPUT_VERSION = "2.0"
BASIS_CARD_VERSION = "1.1"
DECISION_BASIS_VERSION = "1.0"
PROMPTS_DIR = AGENT_DIR / "prompts"
DEFAULT_ENV_FILE = AGENT_DIR.parents[2] / "configs" / ".env"
DEFAULT_OPENAI_MODEL = "gpt-5.4-mini"
DEFAULT_OPENAI_MAX_TOKENS = 12000
FINAL_RECOMMENDATIONS = {"Buy", "Hold", "Sell"}
VALIDATION_ANSWER_CHAR_LIMIT = 900
VALIDATION_REASON_CHAR_LIMIT = 500


def run_strategy_agent(
    *,
    target_company_name: str,
    target_run_key: str,
    target_financial_path: Path,
    target_news_path: Path,
    target_yfinance_path: Path,
    competitor_report_paths: list[Path],
    output_dir: Path,
    llm_provider: str = "openai",
    llm_model: str = "auto",
    llm_timeout: int = 120,
    env_file: Path | None = DEFAULT_ENV_FILE,
) -> dict[str, Any]:
    """Run Strategy Agent and write strategy outputs."""

    if env_file:
        load_env_file(env_file)

    output_dir = output_dir.expanduser().resolve()
    output_dir.mkdir(parents=True, exist_ok=True)

    input_bundle = build_strategy_input_bundle(
        target_company_name=target_company_name,
        target_run_key=target_run_key,
        target_financial_path=target_financial_path,
        target_news_path=target_news_path,
        target_yfinance_path=target_yfinance_path,
        competitor_report_paths=competitor_report_paths,
    )
    validate_input_bundle(input_bundle)
    save_json(output_dir / "strategy_input_bundle.json", input_bundle)

    content_plan = run_content_planner(
        input_bundle,
        llm_provider=llm_provider,
        llm_model=llm_model,
        llm_timeout=llm_timeout,
    )
    validate_content_plan(content_plan)
    save_json(output_dir / "strategy_content_plan.json", content_plan)

    strategy_output = run_decision_agent(
        input_bundle,
        content_plan,
        llm_provider=llm_provider,
        llm_model=llm_model,
        llm_timeout=llm_timeout,
    )
    strategy_report, decision_basis_by_section = normalize_strategy_decision_output(strategy_output, input_bundle)
    validate_strategy_report(strategy_report)
    validate_decision_basis_by_section(decision_basis_by_section, strategy_report)
    decision_basis_card = build_decision_basis_card(strategy_report, decision_basis_by_section)
    validate_decision_basis_card(decision_basis_card)
    save_json(output_dir / "strategy_report.json", strategy_report)
    save_text(output_dir / "strategy_report.md", render_strategy_markdown(strategy_report))
    save_json(output_dir / "decision_basis_by_section.json", decision_basis_by_section)
    save_json(output_dir / "decision_basis_card.json", decision_basis_card)
    return strategy_report


def generate_strategy_report(
    *,
    run_key: str | None = None,
    target_config: Path | None = DEFAULT_TARGET_CONFIG,
    financial_report: Path | None = None,
    news_report: Path | None = None,
    yfinance_report: Path | None = None,
    output_root: Path = OUTPUT_ROOT,
    output_json: Path | None = None,
    output_md: Path | None = None,
    competitor_reports: list[Path] | None = None,
    auto_discover_competitors: bool = False,
    llm_provider: str = "openai",
    llm_model: str = "auto",
    llm_timeout: int = 120,
    env_file: Path | None = DEFAULT_ENV_FILE,
) -> dict[str, Any]:
    """Compatibility wrapper around run_strategy_agent using repo defaults."""

    output_root = output_root.expanduser().resolve()
    identity = load_identity_from_config(target_config) if target_config and target_config.exists() else {}
    target_run_key = run_key or infer_run_key_from_paths([financial_report, news_report, yfinance_report]) or identity.get("run_key")
    if not target_run_key:
        raise ValueError("target_run_key is required.")
    target_company_name = identity.get("company_name") or company_from_run_key(target_run_key)
    paths = {
        "financial": financial_report or output_root / "Financial" / target_run_key / "final_report.json",
        "news": news_report or output_root / "News" / target_run_key / "final_report.json",
        "yfinance": yfinance_report or output_root / "Y_Finance" / target_run_key / "final_report.json",
    }
    competitor_paths = list(competitor_reports or [])
    if auto_discover_competitors:
        competitor_paths.extend(discover_competitor_reports(output_root=output_root, target_run_key=target_run_key))
    output_dir = (output_json.parent if output_json else output_md.parent if output_md else output_root / "Strategy" / target_run_key)
    report = run_strategy_agent(
        target_company_name=target_company_name,
        target_run_key=target_run_key,
        target_financial_path=paths["financial"],
        target_news_path=paths["news"],
        target_yfinance_path=paths["yfinance"],
        competitor_report_paths=dedupe_paths(competitor_paths),
        output_dir=output_dir,
        llm_provider=llm_provider,
        llm_model=llm_model,
        llm_timeout=llm_timeout,
        env_file=env_file,
    )
    if output_json and output_json != output_dir / "strategy_report.json":
        save_json(output_json, report)
    if output_md and output_md != output_dir / "strategy_report.md":
        save_text(output_md, render_strategy_markdown(report))
    return report


def build_strategy_input_bundle(
    *,
    target_company_name: str,
    target_run_key: str,
    target_financial_path: Path,
    target_news_path: Path,
    target_yfinance_path: Path,
    competitor_report_paths: list[Path],
) -> dict[str, Any]:
    """Create the exact input bundle read by the two Strategy Agent LLM steps."""

    target_financial_path = target_financial_path.expanduser().resolve()
    target_news_path = target_news_path.expanduser().resolve()
    target_yfinance_path = target_yfinance_path.expanduser().resolve()
    target_financial_path = resolve_preferred_report_path(target_financial_path, "financial")
    target_news_path = resolve_preferred_report_path(target_news_path, "news")
    target_yfinance_path = resolve_preferred_report_path(target_yfinance_path, "yfinance")

    financial = sanitize_strategy_input_report(load_required_json(target_financial_path, "Target Financial"), "financial")
    news = sanitize_strategy_input_report(load_required_json(target_news_path, "Target News"), "news")
    yfinance = sanitize_strategy_input_report(load_required_json(target_yfinance_path, "Target YFinance"), "yfinance")
    financial_validation = load_optional_validation_evidence(target_financial_path, "financial")
    news_validation = load_optional_validation_evidence(target_news_path, "news")
    yfinance_validation = load_optional_validation_evidence(target_yfinance_path, "yfinance")
    target_company = infer_target_company(
        target_company_name=target_company_name,
        target_run_key=target_run_key,
        financial=financial,
        news=news,
        yfinance=yfinance,
    )
    competitors = load_competitor_reports(competitor_report_paths)
    return {
        "agent_name": "Strategy Agent",
        "output_version": OUTPUT_VERSION,
        "target_company": target_company,
        "target_reports": {
            "financial": financial,
            "news": news,
            "yfinance": yfinance,
        },
        "target_validation_evidence": {
            "financial": financial_validation,
            "news": news_validation,
            "yfinance": yfinance_validation,
        },
        "competitor_reports": competitors,
        "decision_constraints": extract_decision_constraints(financial, news, yfinance),
        "input_metadata": {
            "target_financial_path": str(target_financial_path),
            "target_news_path": str(target_news_path),
            "target_yfinance_path": str(target_yfinance_path),
            "target_validation_paths": {
                "financial": financial_validation.get("source_path", ""),
                "news": news_validation.get("source_path", ""),
                "yfinance": yfinance_validation.get("source_path", ""),
            },
            "competitor_report_paths": [str(path.expanduser().resolve()) for path in competitor_report_paths],
            "competitor_count": len(competitors),
            "created_at": datetime.now().isoformat(timespec="seconds"),
        },
    }


def run_content_planner(
    input_bundle: dict[str, Any],
    *,
    llm_provider: str,
    llm_model: str,
    llm_timeout: int,
) -> dict[str, Any]:
    """Call the Content Planner LLM and return its JSON plan."""

    prompt = read_prompt("content_planner.md")
    payload = {
        "strategy_input_bundle": input_bundle,
        "required_output_schema": content_plan_schema(),
    }
    return call_llm_json(
        prompt=prompt,
        payload=payload,
        llm_provider=llm_provider,
        llm_model=llm_model,
        llm_timeout=llm_timeout,
        system_message="You are a financial Strategy Agent Content Planner. Return only valid JSON.",
    )


def run_decision_agent(
    input_bundle: dict[str, Any],
    content_plan: dict[str, Any],
    *,
    llm_provider: str,
    llm_model: str,
    llm_timeout: int,
) -> dict[str, Any]:
    """Call the Decision Agent LLM and return its JSON report."""

    prompt = read_prompt("decision_agent.md")
    payload = {
        "strategy_input_bundle": input_bundle,
        "strategy_content_plan": content_plan,
        "required_output_schema": strategy_decision_output_schema(),
    }
    return call_llm_json(
        prompt=prompt,
        payload=payload,
        llm_provider=llm_provider,
        llm_model=llm_model,
        llm_timeout=llm_timeout,
        system_message="You are a financial Strategy Decision Agent. Return only valid JSON.",
    )


def normalize_strategy_report(report: dict[str, Any], input_bundle: dict[str, Any]) -> dict[str, Any]:
    """Normalize required fields that must mirror the actual input bundle."""

    if not isinstance(report, dict):
        raise ValueError("strategy_report must be an object.")
    target = input_bundle["target_company"]
    metadata = input_bundle["input_metadata"]
    source_files = {
        "target_financial": metadata["target_financial_path"],
        "target_news": metadata["target_news_path"],
        "target_yfinance": metadata["target_yfinance_path"],
        "target_validations": metadata.get("target_validation_paths", {}),
        "competitor_reports": metadata["competitor_report_paths"],
    }

    if "final_recommendation" in report and isinstance(report.get("final_recommendation"), dict):
        normalized = normalize_structured_strategy_report(report)
    else:
        normalized = convert_legacy_strategy_report(report)

    normalized["agent_name"] = "Strategy Agent"
    normalized["target_company_name"] = target["company_name"]
    normalized["target_run_key"] = target["run_key"]
    normalized["source_files"] = source_files
    normalized["limitations"] = normalize_limitations(normalized.get("limitations"), input_bundle.get("decision_constraints"))
    normalized = rewrite_conservative_language(normalized)
    normalized["limitations"] = normalize_limitations(normalized.get("limitations"), None)
    normalized = enforce_specific_evidence_language(normalized, input_bundle)
    normalized["limitations"] = consolidate_limitations(normalized.get("limitations"), normalized)
    normalized["opinion_index"] = build_report_opinion_index(normalized)
    normalized.setdefault("created_at", datetime.now().isoformat(timespec="seconds"))
    normalized.setdefault("output_version", OUTPUT_VERSION)
    return normalized


def normalize_strategy_decision_output(output: dict[str, Any], input_bundle: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    """Normalize the Decision Agent output into report plus path-level basis card."""

    raw_report, raw_basis = split_strategy_decision_output(output)
    strategy_report = normalize_strategy_report(raw_report, input_bundle)
    decision_basis_by_section = normalize_decision_basis_by_section(raw_basis, strategy_report, input_bundle)
    return strategy_report, decision_basis_by_section


def split_strategy_decision_output(output: dict[str, Any]) -> tuple[dict[str, Any], Any]:
    """Accept the new wrapped output while preserving old report-only compatibility."""

    if not isinstance(output, dict):
        raise ValueError("strategy decision output must be an object.")
    if isinstance(output.get("strategy_report"), dict):
        return output["strategy_report"], first_non_empty_object(
            output.get("decision_basis_by_section"),
            output.get("basis_by_section"),
            output.get("decision_basis_card_by_section"),
        )
    legacy_report = {
        key: value
        for key, value in output.items()
        if key not in {"decision_basis_by_section", "basis_by_section", "decision_basis_card_by_section"}
    }
    return legacy_report, first_non_empty_object(
        output.get("decision_basis_by_section"),
        output.get("basis_by_section"),
        output.get("decision_basis_card_by_section"),
    )


def first_non_empty_object(*values: Any) -> Any:
    """Return the first non-empty dict/list/string-like value."""

    for value in values:
        if value:
            return value
    return None


def normalize_decision_basis_by_section(
    raw_basis: Any,
    strategy_report: dict[str, Any],
    input_bundle: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Normalize LLM-written basis entries keyed to Strategy Report section paths."""

    raw_map = unwrap_decision_basis_map(raw_basis)
    opinion_ids = {
        clean_text(item.get("section_path")): clean_text(item.get("id"))
        for item in ensure_list(strategy_report.get("opinion_index"))
        if isinstance(item, dict)
    }
    basis_map: dict[str, Any] = {}
    for section_path, opinion_text in iter_editable_report_opinions(strategy_report):
        if not opinion_text:
            continue
        raw_entry = raw_map.get(section_path) or raw_map.get(opinion_ids.get(section_path, ""))
        basis_map[section_path] = normalize_decision_basis_entry(
            raw_entry,
            section_path=section_path,
            opinion_id=opinion_ids.get(section_path, ""),
            opinion_text=opinion_text,
            input_bundle=input_bundle,
        )
    fill_missing_basis_summaries(basis_map, input_bundle)
    return {
        "target_company_name": clean_text(strategy_report.get("target_company_name")),
        "target_run_key": clean_text(strategy_report.get("target_run_key")),
        "final_recommendation": clean_text(get_path(strategy_report, ["final_recommendation", "opinion"])),
        "decision_basis_by_section": basis_map,
        "basis_card_version": DECISION_BASIS_VERSION,
        "created_at": datetime.now().isoformat(timespec="seconds"),
    }


def unwrap_decision_basis_map(raw_basis: Any) -> dict[str, Any]:
    """Return a path-keyed basis map from supported LLM output variants."""

    if isinstance(raw_basis, dict):
        nested = first_dict(
            raw_basis.get("decision_basis_by_section"),
            raw_basis.get("basis_by_section"),
            raw_basis.get("sections"),
        )
        if nested:
            return unwrap_decision_basis_map(nested)
        return raw_basis
    if isinstance(raw_basis, list):
        mapped: dict[str, Any] = {}
        for item in raw_basis:
            if not isinstance(item, dict):
                continue
            key = first_non_empty(item.get("section_path"), item.get("path"), item.get("opinion_id"), item.get("id"))
            if key:
                mapped[clean_text(key)] = item
        return mapped
    return {}


def normalize_decision_basis_entry(
    raw_entry: Any,
    *,
    section_path: str,
    opinion_id: str,
    opinion_text: str,
    input_bundle: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Normalize one LLM-written basis entry without inventing analytical claims."""

    if isinstance(raw_entry, str):
        payload: dict[str, Any] = {"basis_summary": raw_entry}
    elif isinstance(raw_entry, dict):
        payload = raw_entry
    else:
        payload = {}
    basis_summary = first_non_empty(
        payload.get("basis_summary"),
        payload.get("why_written"),
        payload.get("reasoning"),
        payload.get("evidence_summary"),
        payload.get("basis"),
    )
    if is_stale_path_fallback_basis(basis_summary, section_path):
        basis_summary = ""
    if not basis_summary and not raw_entry:
        basis_summary = infer_system_added_basis_summary(section_path, opinion_text, input_bundle)
    key_numbers = normalize_key_numbers(payload.get("key_numbers"), opinion_text)
    return {
        "opinion_id": clean_text(payload.get("opinion_id")) or opinion_id,
        "section_path": section_path,
        "opinion_text": opinion_text,
        "basis_summary": clean_text(basis_summary),
        "key_numbers": dedupe(key_numbers, 5),
        "source_evidence": normalize_basis_source_evidence(
            first_non_empty_object(payload.get("source_evidence"), payload.get("evidence"), payload.get("evidence_refs"))
        )[:2],
        "limitations": dedupe(text_items(payload.get("limitations")), 2),
    }


def fill_missing_basis_summaries(basis_map: dict[str, Any], input_bundle: dict[str, Any] | None = None) -> None:
    """Fill omitted basis summaries from existing input evidence, not from opinion text."""

    aggregate_fragments = aggregate_input_basis_fragments(basis_map)
    input_candidates = input_text_candidates(input_bundle)
    for section_path, entry in basis_map.items():
        if not isinstance(entry, dict) or clean_text(entry.get("basis_summary")):
            continue
        fragments = input_basis_fragments(entry)
        if section_path == "final_recommendation.summary":
            fragments = aggregate_fragments or fragments
        if not fragments:
            related_input = best_related_input_text(clean_text(entry.get("opinion_text")), input_candidates)
            if related_input:
                fragments = [related_input]
        if fragments:
            entry["basis_summary"] = render_input_to_opinion_basis(fragments, section_path)


def aggregate_input_basis_fragments(basis_map: dict[str, Any]) -> list[str]:
    """Collect representative input evidence fragments from all basis entries."""

    preferred_prefixes = (
        "financial_view.",
        "risk_view.",
        "market_price_view.",
        "catalyst_view.",
        "cross_agent_consistency_check.",
    )
    fragments: list[str] = []
    for prefix in preferred_prefixes:
        for section_path, entry in basis_map.items():
            if clean_text(section_path).startswith(prefix):
                fragments.extend(input_basis_fragments(entry))
            if len(fragments) >= 4:
                return dedupe(fragments, 4)
    return dedupe(fragments, 4)


def input_basis_fragments(entry: dict[str, Any]) -> list[str]:
    """Extract concise input evidence fragments from a normalized basis entry."""

    fragments: list[str] = []
    for source in normalize_basis_source_evidence(entry.get("source_evidence")):
        agent = clean_text(source.get("agent"))
        evidence = clean_text(source.get("evidence_text"))
        if evidence:
            fragments.append(f"{agent} 입력의 {evidence}" if agent else evidence)
    fragments.extend(text_items(entry.get("key_numbers")))
    fragments.extend(text_items(entry.get("limitations")))
    return dedupe([truncate_text(item, 90) for item in fragments if item], 4)


def render_input_to_opinion_basis(fragments: list[str], section_path: str) -> str:
    """Render an input-to-opinion basis sentence without repeating the opinion text."""

    evidence = "; ".join(dedupe(fragments, 3))
    label = section_path_basis_label(section_path)
    return f"{evidence} 근거가 입력에서 확인되어 {label}{ro_particle(label)} 판단했다."


def section_path_basis_label(section_path: str) -> str:
    """Return a reader-facing label for a Strategy Report path."""

    path = clean_text(section_path)
    if path.startswith("final_recommendation"):
        return "최종 투자의견"
    if path.startswith("investment_thesis"):
        return "투자 thesis"
    if path.startswith("financial_view.revenue"):
        return "매출 의견"
    if path.startswith("financial_view.profitability"):
        return "수익성 의견"
    if path.startswith("financial_view.cash_flow"):
        return "현금흐름 의견"
    if path.startswith("financial_view.balance_sheet"):
        return "재무구조 의견"
    if path.startswith("financial_view"):
        return "재무 해석 의견"
    if path.startswith("catalyst_view"):
        return "사업 catalyst 의견"
    if path.startswith("risk_view.regulatory_risks"):
        return "규제 리스크 의견"
    if path.startswith("risk_view.market_risks"):
        return "시장 리스크 의견"
    if path.startswith("risk_view.execution_risks"):
        return "실행 리스크 의견"
    if path.startswith("risk_view.financial_risks"):
        return "재무 리스크 의견"
    if path.startswith("market_price_view"):
        return "시장 가격 의견"
    if path.startswith("peer_competitor_positioning"):
        return "경쟁사 비교 의견"
    if path.startswith("cross_agent_consistency_check"):
        return "교차 검증 의견"
    if path.startswith("key_strengths"):
        return "핵심 강점 의견"
    if path.startswith("key_risks"):
        return "핵심 리스크 의견"
    if path.startswith("limitations"):
        return "한계 및 모니터링 의견"
    if path.startswith("final_rationale"):
        return "최종 판단 근거"
    return "해당 의견"


def is_stale_path_fallback_basis(basis_summary: str, section_path: str) -> bool:
    """Detect old fallback wording that exposed raw report paths to readers."""

    text = clean_text(basis_summary)
    if not text or "근거가 입력에서 확인되어" not in text:
        return False
    return clean_text(section_path) in text or "의견로 판단했다" in text or "투자의견로 판단했다" in text


def ro_particle(text: str) -> str:
    """Return Korean instrumental particle for labels ending in Hangul."""

    value = clean_text(text)
    for character in reversed(value):
        code = ord(character)
        if 0xAC00 <= code <= 0xD7A3:
            final = (code - 0xAC00) % 28
            return "로" if final in {0, 8} else "으로"
    return "로"


def input_text_candidates(input_bundle: dict[str, Any] | None) -> list[str]:
    """Collect concise source strings from the Strategy input bundle."""

    if not isinstance(input_bundle, dict):
        return []
    candidates: list[str] = []
    collect_input_text_candidates(input_bundle, candidates)
    return dedupe([truncate_text(item, 220) for item in candidates if item], 1500)


def collect_input_text_candidates(value: Any, candidates: list[str]) -> None:
    """Recursively collect meaningful strings from nested input data."""

    if isinstance(value, dict):
        for child in value.values():
            collect_input_text_candidates(child, candidates)
    elif isinstance(value, list):
        for child in value:
            collect_input_text_candidates(child, candidates)
    else:
        text = clean_text(value)
        if len(text) >= 12 and not looks_like_path_or_identifier(text):
            candidates.append(text)


def looks_like_path_or_identifier(text: str) -> bool:
    """Skip file paths and bare identifiers when searching input basis text."""

    value = clean_text(text)
    if "/" in value or "\\" in value:
        return True
    if re.fullmatch(r"[A-Z_]+_\d+(?:[-_]\d+)*", value):
        return True
    return False


def infer_system_added_basis_summary(
    section_path: str,
    opinion_text: str,
    input_bundle: dict[str, Any] | None,
) -> str:
    """Explain deterministic report additions that come directly from input constraints."""

    if not section_path.startswith("limitations.") or not isinstance(input_bundle, dict):
        return ""
    constraints = text_items(input_bundle.get("decision_constraints"))
    match = best_related_input_text(opinion_text, constraints)
    if not match:
        return ""
    return f"strategy_input_bundle.decision_constraints에 '{match}' 입력이 포함되어 있어 해당 제약을 limitations에 반영했다."


def best_related_input_text(target_text: str, candidates: list[str]) -> str:
    """Return the candidate sharing the most meaningful words with target_text."""

    target_tokens = meaningful_korean_tokens(target_text)
    best = ""
    best_score = 0
    for candidate in candidates:
        candidate_tokens = meaningful_korean_tokens(candidate)
        score = len(target_tokens & candidate_tokens)
        if score > best_score:
            best = candidate
            best_score = score
    return best if best_score >= 1 else ""


def meaningful_korean_tokens(text: str) -> set[str]:
    """Extract coarse Korean/English tokens for fuzzy source matching."""

    stopwords = {
        "있다",
        "없다",
        "해석",
        "제한",
        "기준",
        "직접",
        "증거",
        "필요",
        "있음",
        "대상",
        "확인",
    }
    return {token for token in re.findall(r"[가-힣A-Za-z0-9]+", clean_text(text)) if len(token) >= 2 and token not in stopwords}


def normalize_basis_source_evidence(value: Any) -> list[dict[str, Any]]:
    """Normalize source-evidence rows used by a path-level decision basis entry."""

    if value is None:
        return []
    if isinstance(value, dict):
        values = [value]
    else:
        values = ensure_list(value)
    rows: list[dict[str, Any]] = []
    for item in values:
        if isinstance(item, dict):
            evidence_text = first_non_empty(
                item.get("evidence_text"),
                item.get("answer_1"),
                item.get("answer_2"),
                item.get("claim"),
                item.get("text"),
                item.get("summary"),
            )
            evidence_ids = dedupe(
                text_items(item.get("evidence_ids"))
                + text_items(item.get("evidence_refs"))
                + text_items(item.get("evidence_ids_used")),
                16,
            )
            rows.append(
                {
                    "agent": clean_text(item.get("agent")),
                    "claim_id": clean_text(item.get("claim_id")),
                    "evidence_text": truncate_text(evidence_text, 160),
                    "source_path": first_non_empty(item.get("source_path"), item.get("path")),
                    "source_section": first_non_empty(item.get("source_section"), item.get("section"), item.get("section_path")),
                    "evidence_ids": evidence_ids,
                }
            )
        else:
            text = clean_text(item)
            if text:
                rows.append(
                    {
                        "agent": "",
                        "claim_id": "",
                        "evidence_text": text,
                        "source_path": "",
                        "source_section": "",
                        "evidence_ids": [],
                    }
                )
    return [row for row in rows if row.get("evidence_text") or row.get("claim_id") or row.get("evidence_ids")]


def extract_numbers(text: str) -> list[str]:
    """Extract numeric phrases already present in a report opinion."""

    pattern = r"[-+]?\d+(?:,\d{3})*(?:\.\d+)?\s*(?:조원|억원|원|%|배|일|년|개월|분기|Q[1-4])?"
    return dedupe([match.strip() for match in re.findall(pattern, clean_text(text)) if match.strip()], 16)


def normalize_key_numbers(value: Any, fallback_text: str) -> list[str]:
    """Keep only useful numeric, period, and market-metric basis values."""

    raw_items = text_items(value) or extract_numbers(fallback_text)
    return dedupe([item for item in raw_items if is_useful_key_number(item)], 16)


def is_useful_key_number(text: str) -> bool:
    """Reject evidence-id fragments and bare integers from key_numbers."""

    value = clean_text(text)
    if not value or not re.search(r"\d", value):
        return False
    if "NEWS_RAW" in value or "EVIDENCE" in value.upper():
        return False
    if re.fullmatch(r"[-+]?\d+(?:,\d{3})*(?:\.\d+)?", value):
        return False
    useful_markers = (
        "조원",
        "억원",
        "원",
        "%",
        "배",
        "일",
        "년",
        "개월",
        "분기",
        "Q1",
        "Q2",
        "Q3",
        "Q4",
        "YTD",
        "FULL_YEAR",
        "초과수익률",
        "상대강도",
        "EPS",
        "매출",
        "공헌이익률",
        "판관비율",
    )
    return any(marker in value for marker in useful_markers)


def normalize_structured_strategy_report(report: dict[str, Any]) -> dict[str, Any]:
    """Normalize the current section-based Strategy Report schema."""

    recommendation = require_dict(report.get("final_recommendation"), "final_recommendation")
    opinion = normalize_recommendation(recommendation.get("opinion"))
    summary = clean_text(recommendation.get("summary"))
    final_rationale_text = clean_text(get_path(report, ["final_rationale", "why_buy_hold_sell"]))
    normalized = {
        "target_company_name": clean_text(report.get("target_company_name")),
        "final_recommendation": {
            "opinion": opinion,
            "summary": summary,
        },
        "investment_thesis": normalize_investment_thesis(
            report.get("investment_thesis"),
            opinion=opinion,
            summary=summary,
            final_rationale=final_rationale_text,
            risks=report.get("key_risks"),
            limitations=report.get("limitations"),
        ),
        "financial_view": string_fields(
            report.get("financial_view"),
            ("revenue", "profitability", "cash_flow", "balance_sheet", "financial_interpretation"),
        ),
        "catalyst_view": list_fields(
            report.get("catalyst_view"),
            ("positive_catalysts", "business_expansion"),
        ),
        "risk_view": list_fields(
            report.get("risk_view"),
            ("financial_risks", "regulatory_risks", "market_risks", "execution_risks"),
        ),
        "market_price_view": string_fields(
            report.get("market_price_view"),
            ("price_trend", "volume", "relative_strength", "market_interpretation"),
        ),
        "cross_agent_consistency_check": {
            **list_fields(report.get("cross_agent_consistency_check"), ("confirmed_signals", "mixed_conflicting_signals")),
            "strategy_implication": clean_text(get_path(report, ["cross_agent_consistency_check", "strategy_implication"])),
        },
        "peer_competitor_positioning": {
            **list_fields(
                report.get("peer_competitor_positioning"),
                ("competitor_summary", "target_relative_strength", "target_relative_weakness"),
            ),
            "peer_based_investment_implication": clean_text(
                get_path(report, ["peer_competitor_positioning", "peer_based_investment_implication"])
            ),
        },
        "key_strengths": text_items(report.get("key_strengths")),
        "key_risks": text_items(report.get("key_risks")),
        "final_rationale": {
            "why_buy_hold_sell": final_rationale_text,
        },
        "limitations": normalize_limitations(report.get("limitations"), None),
    }
    return normalized


def convert_legacy_strategy_report(report: dict[str, Any]) -> dict[str, Any]:
    """Convert the previous Strategy Report schema to the section-based schema."""

    opinion = normalize_recommendation(report.get("final_recommendation"))
    investment_view = report.get("investment_view") if isinstance(report.get("investment_view"), dict) else {}
    competitors = [item for item in ensure_list(report.get("competitor_comparison")) if isinstance(item, dict)]
    competitor_summaries = [
        f"{clean_text(item.get('competitor'))}: {clean_text(item.get('comparison_summary'))}".strip(": ")
        for item in competitors
        if clean_text(item.get("competitor")) or clean_text(item.get("comparison_summary"))
    ]
    competitor_strengths: list[str] = []
    competitor_risks: list[str] = []
    for item in competitors:
        competitor_strengths.extend(text_items(item.get("competitor_strengths_considered")))
        competitor_risks.extend(text_items(item.get("competitor_risks_considered")))

    rationale = text_items(report.get("decision_rationale"))
    strengths = text_items(report.get("target_strengths"))
    risks = text_items(report.get("target_risks"))
    limitations = text_items(report.get("limitations"))
    summary = clean_text(report.get("recommendation_summary"))

    investment_thesis = {
        "thesis_1": strengths[0] if strengths else summary,
        "thesis_2": strengths[1] if len(strengths) > 1 else (rationale[0] if rationale else summary),
    }
    if opinion == "Hold":
        investment_thesis["thesis_3"] = build_hold_buy_blocker_thesis(summary, rationale, risks, limitations)

    return {
        "target_company_name": clean_text(report.get("target_company")),
        "final_recommendation": {
            "opinion": opinion,
            "summary": summary,
        },
        "investment_thesis": investment_thesis,
        "financial_view": {
            "revenue": clean_text(investment_view.get("financial_view")),
            "profitability": clean_text(investment_view.get("financial_view")),
            "cash_flow": clean_text(investment_view.get("financial_view")),
            "balance_sheet": clean_text(investment_view.get("financial_view")),
            "financial_interpretation": clean_text(investment_view.get("financial_view")),
        },
        "catalyst_view": {
            "positive_catalysts": text_items(investment_view.get("news_view")),
            "business_expansion": text_items(investment_view.get("news_view")),
        },
        "risk_view": {
            "financial_risks": risks,
            "regulatory_risks": [item for item in risks if "FDA" in item or "규제" in item or "관세" in item],
            "market_risks": [item for item in risks if "주가" in item or "시장" in item or "상대" in item],
            "execution_risks": [item for item in risks if "신사업" in item or "지속성" in item or "상업화" in item],
        },
        "market_price_view": {
            "price_trend": clean_text(investment_view.get("market_view")),
            "volume": clean_text(investment_view.get("market_view")),
            "relative_strength": clean_text(investment_view.get("market_view")),
            "market_interpretation": clean_text(investment_view.get("market_view")),
        },
        "cross_agent_consistency_check": {
            "confirmed_signals": strengths,
            "mixed_conflicting_signals": limitations + risks,
            "strategy_implication": "; ".join(rationale) if rationale else summary,
        },
        "peer_competitor_positioning": {
            "competitor_summary": competitor_summaries,
            "target_relative_strength": dedupe(competitor_risks, 8),
            "target_relative_weakness": dedupe(competitor_strengths, 8),
            "peer_based_investment_implication": clean_text(investment_view.get("competitor_view")),
        },
        "key_strengths": strengths,
        "key_risks": risks,
        "final_rationale": {
            "why_buy_hold_sell": "; ".join(rationale) if rationale else summary,
        },
        "limitations": normalize_limitations(limitations, None),
    }


def string_fields(value: Any, keys: tuple[str, ...]) -> dict[str, str]:
    """Return a dict with required string keys."""

    payload = value if isinstance(value, dict) else {}
    return {key: clean_text(payload.get(key)) for key in keys}


def normalize_investment_thesis(
    value: Any,
    *,
    opinion: str,
    summary: str,
    final_rationale: str,
    risks: Any,
    limitations: Any,
) -> dict[str, str]:
    """Normalize thesis fields with a third balancing thesis for Hold reports."""

    payload = value if isinstance(value, dict) else {}
    thesis = {
        "thesis_1": clean_text(payload.get("thesis_1")),
        "thesis_2": clean_text(payload.get("thesis_2")),
    }
    thesis_3 = first_non_empty(payload.get("thesis_3"), payload.get("why_not_buy"))
    if opinion == "Hold":
        thesis["thesis_3"] = thesis_3 or build_hold_buy_blocker_thesis(
            summary,
            [final_rationale],
            text_items(risks),
            limitations_text_items(normalize_limitations(limitations, None)),
        )
    elif thesis_3:
        thesis["thesis_3"] = thesis_3
    return thesis


def build_hold_buy_blocker_thesis(summary: str, rationale: Any, risks: Any, limitations: Any) -> str:
    """Build a data-derived reason why Hold is not Buy when the LLM omits one."""

    blocker = first_non_empty(*text_items(risks), *text_items(limitations))
    if blocker:
        return f"Buy로 상향하기에는 {blocker} 요인이 남아 있어 Hold가 적절하다."
    return first_non_empty(*text_items(rationale), summary)


def list_fields(value: Any, keys: tuple[str, ...]) -> dict[str, list[str]]:
    """Return a dict with required list-of-string keys."""

    payload = value if isinstance(value, dict) else {}
    return {key: text_items(payload.get(key)) for key in keys}


def normalize_limitations(value: Any, constraints: Any) -> dict[str, list[str]]:
    """Normalize limitations into data, interpretation, and monitoring buckets."""

    if isinstance(value, dict):
        data_limitations = sanitize_limitation_items(value.get("data_limitations"))
        interpretation_limitations = sanitize_limitation_items(value.get("interpretation_limitations"))
        monitoring_points = sanitize_limitation_items(value.get("monitoring_points"))
    else:
        items = sanitize_limitation_items(value)
        data_limitations = [item for item in items if any(token in item for token in ("데이터", "기간", "기준", "집계", "부재"))]
        interpretation_limitations = [
            item
            for item in items
            if any(token in item for token in ("해석", "직접 증거", "인과관계", "단정", "제한"))
        ]
        monitoring_points = [item for item in items if item not in data_limitations and item not in interpretation_limitations]
    constraint_items = sanitize_limitation_items(constraints)
    if constraint_items:
        interpretation_limitations = dedupe(interpretation_limitations + constraint_items, 14)
    return {
        "data_limitations": dedupe(data_limitations, 10),
        "interpretation_limitations": dedupe(interpretation_limitations, 14),
        "monitoring_points": dedupe(monitoring_points, 10),
    }


def consolidate_limitations(value: Any, strategy_report: dict[str, Any] | None = None) -> dict[str, list[str]]:
    """Deduplicate limitation buckets without inventing new reader-facing claims."""

    del strategy_report
    limitations = normalize_limitations(value, None)
    data_items = consolidate_limitation_bucket(
        limitations.get("data_limitations"),
        limit=6,
    )
    interpretation_items = consolidate_limitation_bucket(
        limitations.get("interpretation_limitations"),
        limit=8,
    )
    monitoring_items = consolidate_limitation_bucket(
        limitations.get("monitoring_points"),
        limit=6,
    )
    return {
        "data_limitations": data_items,
        "interpretation_limitations": interpretation_items,
        "monitoring_points": monitoring_items,
    }


def consolidate_limitation_bucket(value: Any, *, limit: int) -> list[str]:
    """Deduplicate limitation text while preserving the LLM/source wording."""

    items = [clean_limitation_sentence(item) for item in text_items(value)]
    items = [item for item in items if item and not is_low_value_limitation(item)]
    return dedupe(items, limit)


def clean_limitation_sentence(text: Any) -> str:
    """Normalize surface wording in limitation sentences."""

    value = clean_text(text)
    value = value.replace("대상임", "대상이다")
    value = value.replace("제한이 있음", "제한이 있다")
    value = value.replace("부족함", "부족하다")
    value = value.replace("어려움", "어렵다")
    value = value.replace("리스크은", "리스크는")
    value = value.replace("는은", "은").replace("은은", "은")
    return value


def is_low_value_limitation(text: str) -> bool:
    """Drop generic limitation filler that adds little audit value."""

    generic = {
        "추가 검토 전까지 해석에 제한이 있다.",
        "추가 검증 전까지 해석에 제한이 있다.",
        "추가 확인 전까지 해석에 제한이 있다.",
    }
    if text in generic:
        return True
    if text.startswith("News SY 검증에서 일부 "):
        return True
    if re.fullmatch(r".+지속 관찰 대상이다(?:\.는 지속 관찰 대상이다)*\.?", text):
        return True
    return False


def sanitize_limitation_items(value: Any) -> list[str]:
    """Convert internal caution wording into reader-facing limitations."""

    items: list[str] = []
    for item in text_items(value):
        text = user_facing_limitation_text(item)
        if text:
            items.append(text)
    return dedupe(items, 20)


def resolve_preferred_report_path(report_path: Path, domain: str) -> Path:
    """Prefer latest verified handoff/report artifacts over stale top-level aliases."""

    path = report_path.expanduser().resolve()
    candidates = preferred_report_candidates(path, domain)
    return newest_existing_artifact(path, candidates)


def preferred_report_candidates(report_path: Path, domain: str) -> list[Path]:
    """Return source-of-truth report candidates for a domain."""

    if domain == "news":
        if report_path.name == "news_agent_verified_handoff.json":
            return [report_path]
        return [
            report_path.parent / "output" / "sy_agent" / "news_agent_verified_handoff.json",
            report_path,
        ]
    if domain == "financial":
        if report_path.name == "pipeline_verified_financial_report_output.json":
            return [report_path]
        return [
            report_path.parent / "agent_pipeline" / "pipeline_verified_financial_report_output.json",
            report_path,
        ]
    if domain == "yfinance":
        if report_path.name == "yfinance_verified_report.json":
            return [report_path]
        return [
            report_path.parent / "yfinance_verified_report.json",
            report_path,
        ]
    return [report_path]


def newest_existing_artifact(default_path: Path, candidates: list[Path]) -> Path:
    """Choose the newest existing candidate, falling back to the requested path."""

    existing = [path.expanduser().resolve() for path in candidates if path.expanduser().resolve().exists()]
    if not existing:
        return default_path.expanduser().resolve()
    return max(existing, key=lambda path: path.stat().st_mtime)


def load_optional_validation_evidence(report_path: Path, domain: str) -> dict[str, Any]:
    """Load adjacent final_validation.json as compact claim evidence if present."""

    validation_path = resolve_preferred_validation_path(report_path, domain)
    if not validation_path.exists():
        return {"source_path": "", "summary": {}, "claims": []}
    payload = load_required_json(validation_path, f"Target {domain} validation")
    return compact_validation_evidence(payload, domain=domain, source_path=validation_path)


def resolve_preferred_validation_path(report_path: Path, domain: str) -> Path:
    """Prefer latest SY validation artifacts over stale top-level aliases."""

    path = report_path.expanduser().resolve()
    candidates = preferred_validation_candidates(path, domain)
    return newest_existing_artifact(path.parent / "final_validation.json", candidates)


def preferred_validation_candidates(report_path: Path, domain: str) -> list[Path]:
    """Return source-of-truth validation candidates for a domain."""

    if domain == "news":
        if report_path.parent.name == "sy_agent":
            return [
                report_path.parent / "sy_claim_validations.json",
                report_path.parent.parent.parent / "final_validation.json",
            ]
        return [
            report_path.parent / "output" / "sy_agent" / "sy_claim_validations.json",
            report_path.parent / "final_validation.json",
        ]
    if domain == "financial":
        if report_path.parent.name == "agent_pipeline":
            return [
                report_path.parent / "pipeline_sy_validation_output.json",
                report_path.parent.parent / "final_validation.json",
            ]
        return [
            report_path.parent / "agent_pipeline" / "pipeline_sy_validation_output.json",
            report_path.parent / "final_validation.json",
        ]
    if domain == "yfinance":
        return [
            report_path.parent / "final_validation.json",
            report_path.parent / "sy_verified_yfinance_report.json",
        ]
    return [report_path.parent / "final_validation.json"]


def sanitize_strategy_input_report(report: dict[str, Any], domain: str) -> dict[str, Any]:
    """Remove upstream agent-internal notes that should not steer Strategy prose."""

    if domain != "news":
        return report
    sanitize_news_strategy_handoff_notes(report)
    return report


def sanitize_news_strategy_handoff_notes(report: dict[str, Any]) -> None:
    """Keep News handoff notes concise and reader-facing for Strategy inputs."""

    summary_note = news_validation_summary_note(report)
    note_paths = (
        ["output", "analysis_blocks", "news_plus_financial_plus_market", "strategy_handoff_notes"],
        ["output", "news_plus_financial_plus_market", "strategy_handoff_notes"],
        ["analysis_blocks", "news_plus_financial_plus_market", "strategy_handoff_notes"],
    )
    updated = False
    for path in note_paths:
        container = get_path(report, path[:-1])
        if not isinstance(container, dict) or path[-1] not in container:
            continue
        notes = text_items(container.get(path[-1]))
        cleaned = [note for note in notes if not is_sy_validation_handoff_note(note)]
        if summary_note:
            cleaned.append(summary_note)
        container[path[-1]] = dedupe(cleaned, 12)
        updated = True

    if not updated and summary_note:
        container = get_path(report, ["output", "analysis_blocks", "news_plus_financial_plus_market"])
        if isinstance(container, dict):
            container["strategy_handoff_notes"] = [summary_note]


def is_sy_validation_handoff_note(value: Any) -> bool:
    """Detect verbose SY claim-level notes that belong in validation evidence, not Strategy context."""

    text = clean_text(value)
    if not text:
        return False
    lowered = text.lower()
    if re.match(r"^sy\s+(?:keep|revise|revised|weaken|hallucination_candidate|remove|removed|unsupported):", lowered):
        return True
    return text.startswith("SY 검증 결과")


def news_validation_summary_note(report: dict[str, Any]) -> str:
    """Render one compact News SY validation note from verified handoff metadata."""

    summary = first_dict(
        report.get("verification_summary"),
        report.get("validation_summary"),
        report.get("summary"),
        get_path(report, ["output", "verification_summary"]),
        get_path(report, ["output", "sy_validation", "summary"]),
    )
    if not summary:
        return ""
    counts = summary.get("decision_counts") if isinstance(summary.get("decision_counts"), dict) else {}
    total = first_non_empty(summary.get("total_claims"), summary.get("total"), summary.get("total_count"))
    keep = first_non_empty(summary.get("kept_claims"), summary.get("verified_count"), counts.get("keep"))
    revise = first_non_empty(
        summary.get("revised_claims"),
        summary.get("revised_count"),
        summary.get("weakened_count"),
        counts.get("revise"),
        counts.get("weaken"),
    )
    hallucination = first_non_empty(
        summary.get("hallucination_candidate_count"),
        summary.get("unsupported_count"),
        counts.get("hallucination_candidate"),
    )
    remove = first_non_empty(summary.get("removed_count"), summary.get("deleted_claims"), counts.get("remove"))

    count_parts = []
    for label, value in (("keep", keep), ("revise", revise), ("hallucination_candidate", hallucination), ("remove", remove)):
        if value:
            count_parts.append(f"{label} {value}건")
    if not total and not count_parts:
        return ""
    total_text = f"{total}건 중 " if total else ""
    counts_text = ", ".join(count_parts) if count_parts else "일부 claim"
    return (
        f"News SY 검증 결과 {total_text}{counts_text}으로 분류되어 "
        "일부 뉴스-재무-시장 연결 주장은 보조 근거로 제한된다."
    )



def compact_validation_evidence(payload: dict[str, Any], *, domain: str, source_path: Path) -> dict[str, Any]:
    """Keep only Strategy-useful validation evidence from large SY outputs."""

    claims = validation_claims(payload, domain)
    compact_claims = [
        compact_validation_claim(claim, domain=domain)
        for claim in claims
        if isinstance(claim, dict) and first_non_empty(claim.get("claim_id"), claim.get("section"), claim.get("claim"))
    ]
    return {
        "source_path": str(source_path.expanduser().resolve()),
        "summary": validation_summary(payload),
        "claims": compact_claims,
    }


def validation_claims(payload: dict[str, Any], domain: str) -> list[dict[str, Any]]:
    """Return validation claims across the different agent validation schemas."""

    if isinstance(payload.get("claim_validations"), list):
        return payload["claim_validations"]
    if isinstance(payload.get("claim_validation"), list):
        return payload["claim_validation"]
    if domain == "yfinance":
        claims: list[dict[str, Any]] = []
        for key in ("verified_claims", "weakened_claims", "hallucination_candidates", "removed_claims"):
            claims.extend(item for item in ensure_list(payload.get(key)) if isinstance(item, dict))
        return claims
    return []


def validation_summary(payload: dict[str, Any]) -> dict[str, Any]:
    """Extract compact validation summary fields without carrying raw reports."""

    summary = first_dict(payload.get("validation_summary"), payload.get("summary"), payload.get("verification_summary"))
    if not summary:
        return {}
    keep_keys = (
        "overall_status",
        "summary_ko",
        "total_claims",
        "kept_claims",
        "revised_claims",
        "deleted_claims",
        "verified_count",
        "weakened_count",
        "hallucination_candidate_count",
        "removed_count",
        "decision_counts",
    )
    return {key: summary[key] for key in keep_keys if key in summary}


def compact_validation_claim(claim: dict[str, Any], *, domain: str) -> dict[str, Any]:
    """Normalize one validation claim into a small evidence ledger row."""

    answer_1 = first_non_empty(
        claim.get("answer_1_ko"),
        claim.get("answer_round_1_summary"),
        claim.get("yfinance_answer"),
    )
    answer_2 = first_non_empty(claim.get("answer_2_ko"), claim.get("answer_round_2_summary"))
    raw_decision = clean_text(claim.get("decision"))
    decision = normalize_validation_decision(raw_decision)
    support_level = normalize_validation_support(clean_text(claim.get("support_level")), decision)
    return {
        "claim_id": first_non_empty(claim.get("claim_id"), claim.get("section")),
        "section": first_non_empty(claim.get("section"), claim.get("section_path")),
        "claim": first_non_empty(claim.get("claim_ko"), claim.get("claim")),
        "question_1": first_non_empty(claim.get("question_1_ko"), claim.get("question_round_1"), claim.get("question")),
        "answer_1": truncate_text(answer_1, VALIDATION_ANSWER_CHAR_LIMIT),
        "question_2": first_non_empty(claim.get("question_2_ko"), claim.get("question_round_2")),
        "answer_2": truncate_text(answer_2, VALIDATION_ANSWER_CHAR_LIMIT),
        "evidence_ids": validation_evidence_ids(claim, domain),
        "support_level": support_level,
        "decision": decision,
        "raw_decision": raw_decision,
        "sy_reason": truncate_text(first_non_empty(claim.get("reason_ko"), claim.get("sy_reason")), VALIDATION_REASON_CHAR_LIMIT),
        "revision_suggestion": truncate_text(
            normalize_revision_suggestion(clean_text(claim.get("revision_suggestion")), decision),
            VALIDATION_REASON_CHAR_LIMIT,
        ),
    }


def normalize_validation_decision(value: str) -> str:
    """Normalize validation decisions across agent-specific vocabularies."""

    mapping = {
        "keep": "keep",
        "supported": "keep",
        "revise": "weaken",
        "revised": "weaken",
        "weaken": "weaken",
        "weakly_supported": "weaken",
        "hallucination_candidate": "hallucination_candidate",
        "unsupported": "hallucination_candidate",
        "remove": "remove",
        "removed": "remove",
        "contradicted": "remove",
    }
    return mapping.get(clean_text(value), clean_text(value))


def normalize_validation_support(value: str, decision: str) -> str:
    """Fill support level consistently when source validation omits it."""

    if value:
        return value
    mapping = {
        "keep": "supported",
        "weaken": "weakly_supported",
        "hallucination_candidate": "unsupported",
        "remove": "contradicted",
    }
    return mapping.get(decision, "")


def normalize_revision_suggestion(value: str, decision: str) -> str:
    """Preserve upstream revision suggestions without fabricating Strategy wording."""

    text = clean_text(value)
    if normalize_validation_decision(decision) == "keep" and text in {"표현 수정 또는 삭제 필요", "수정 불필요"}:
        return ""
    if text == "표현 수정 또는 삭제 필요":
        return ""
    return text


def validation_evidence_ids(claim: dict[str, Any], domain: str) -> list[str]:
    """Extract evidence identifiers from validation claim variants."""

    del domain
    evidence: list[str] = []
    for key in ("evidence_refs", "evidence_ids_used", "declared_evidence_ids", "evidence_used"):
        evidence.extend(text_items(claim.get(key)))
    return dedupe(evidence, 16)


def truncate_text(text: str, limit: int) -> str:
    """Limit validation excerpts to keep Strategy prompts bounded."""

    value = clean_text(text)
    if len(value) <= limit:
        return value
    return value[: limit - 3].rstrip() + "..."


def first_dict(*values: Any) -> dict[str, Any]:
    """Return the first dict-like value."""

    for value in values:
        if isinstance(value, dict):
            return value
    return {}


def enforce_specific_evidence_language(strategy_report: dict[str, Any], input_bundle: dict[str, Any]) -> dict[str, Any]:
    """Replace vague News risk references with specific source-backed issue names."""

    specific_news_risks = extract_specific_news_risks(input_bundle)
    if not specific_news_risks:
        return strategy_report
    return rewrite_vague_news_risk_text(strategy_report, specific_news_risks)


def extract_specific_news_risks(input_bundle: dict[str, Any]) -> list[str]:
    """Collect concrete News risk issue names from reports and validation answers."""

    news_report = get_path(input_bundle, ["target_reports", "news"]) or {}
    analysis = get_path(news_report, ["output", "analysis_blocks"]) or {}
    candidates: list[str] = []
    for path in (
        ["news_only", "negative_signals"],
        ["news_only", "key_risks"],
        ["news_only", "uncertainties"],
        ["news_plus_financial_plus_market", "integrated_risks"],
    ):
        candidates.extend(text_items(get_path(analysis, path)))

    validation_claims_payload = get_path(input_bundle, ["target_validation_evidence", "news", "claims"]) or []
    for item in ensure_list(validation_claims_payload):
        if not isinstance(item, dict):
            continue
        section = clean_text(item.get("section"))
        claim = clean_text(item.get("claim"))
        if is_news_risk_section(section) or is_specific_risk_phrase(claim):
            candidates.append(claim)
        answer = first_non_empty(item.get("answer_1"), item.get("answer_2"))
        if is_specific_risk_phrase(answer):
            candidates.append(answer)
    return dedupe([compact_risk_phrase(item) for item in candidates], 8)


def is_news_risk_section(section: str) -> bool:
    """Detect News sections that intentionally describe risks."""

    return any(token in section for token in ("negative_signals", "key_risks", "integrated_risks", "uncertainties"))


def is_specific_risk_phrase(text: str) -> bool:
    """Return True when text names concrete risk categories or issues."""

    if not text:
        return False
    risk_tokens = ("FDA", "안전성", "관세", "무역", "공급망", "제네릭", "경쟁", "규제", "상업화", "시장 점유율")
    return any(token in text for token in risk_tokens)


def compact_risk_phrase(text: str) -> str:
    """Shorten verbose validation answers into reusable risk phrases."""

    value = clean_text(text)
    if not value:
        return ""
    if len(value) <= 90:
        return value.rstrip(".")
    first_sentence = re.split(r"(?<=[.!?。])\s+", value, maxsplit=1)[0]
    if len(first_sentence) <= 110:
        return first_sentence.rstrip(".")
    return first_sentence[:107].rstrip() + "..."


def rewrite_vague_news_risk_text(value: Any, specific_news_risks: list[str]) -> Any:
    """Recursively rewrite generic News risk wording."""

    if isinstance(value, dict):
        return {key: rewrite_vague_news_risk_text(item, specific_news_risks) for key, item in value.items()}
    if isinstance(value, list):
        return [rewrite_vague_news_risk_text(item, specific_news_risks) for item in value]
    if not isinstance(value, str):
        return value
    return enrich_vague_news_risk_text(value, specific_news_risks)


def enrich_vague_news_risk_text(text: str, specific_news_risks: list[str]) -> str:
    """Use concrete News risk names where the model left a placeholder phrase."""

    if not is_vague_news_risk_text(text):
        return text
    risk_phrase = render_korean_series(specific_news_risks[:4])
    replacements = {
        "뉴스 주요 리스크 이슈": f"{risk_phrase} 등 뉴스상 구체 리스크",
        "뉴스 주요 리스크": f"{risk_phrase} 등 뉴스상 구체 리스크",
        "뉴스 리스크": f"{risk_phrase} 등 뉴스상 구체 리스크",
        "주요 리스크 이슈": f"{risk_phrase} 등 구체 리스크",
    }
    rewritten = text
    for source, target in replacements.items():
        rewritten = rewritten.replace(source, target)
    if rewritten == text:
        rewritten = f"{risk_phrase} 등 뉴스상 구체 리스크가 {text}"
    return rewritten


def is_vague_news_risk_text(text: str) -> bool:
    """Detect vague News risk wording that needs concrete issue names."""

    if not text:
        return False
    vague_markers = ("뉴스 주요 리스크", "뉴스 리스크", "주요 리스크 이슈")
    return any(marker in text for marker in vague_markers)


def render_korean_series(items: list[str]) -> str:
    """Render a short Korean comma series."""

    cleaned = dedupe(items, 4)
    return ", ".join(cleaned)


def build_report_opinion_index(strategy_report: dict[str, Any]) -> list[dict[str, Any]]:
    """Create stable edit IDs for critique-agent targeted revisions."""

    items: list[dict[str, Any]] = []
    for section_path, text in iter_editable_report_opinions(strategy_report):
        if not text:
            continue
        items.append(
            {
                "id": f"OP{len(items) + 1:03d}",
                "section_path": section_path,
                "text": text,
                "edit_scope": "replace_this_text_only",
            }
        )
    return items


def iter_editable_report_opinions(strategy_report: dict[str, Any]) -> list[tuple[str, str]]:
    """Flatten reader-facing Strategy opinions into critique-editable text units."""

    roots = (
        "final_recommendation",
        "investment_thesis",
        "financial_view",
        "catalyst_view",
        "risk_view",
        "market_price_view",
        "cross_agent_consistency_check",
        "peer_competitor_positioning",
        "key_strengths",
        "key_risks",
        "final_rationale",
        "limitations",
    )
    opinions: list[tuple[str, str]] = []
    for root in roots:
        value = strategy_report.get(root)
        for section_path, text in iter_report_claims(value, root):
            if section_path.endswith(".opinion"):
                continue
            opinions.append((section_path, text))
    return opinions


def user_facing_limitation_text(value: Any) -> str:
    """Remove instruction-like wording from limitation output."""

    text = clean_text(value)
    if not text:
        return ""
    if re.match(r"^SY\s+(?:keep|revise|revised|weaken|hallucination_candidate|remove|removed|unsupported):", text, re.I):
        return ""
    if text.startswith("SY 검증 결과"):
        return ""
    text = text.replace("는은", "은").replace("은은", "은")
    text = text.replace("이은", "은").replace("가은", "은")
    text = text.replace("리스크은", "리스크는")
    if "가격 확인 강도" in text and "낮추" in text:
        return "시장 가격 신호의 확인 강도는 제한적이다."
    if "보수적으로 검증" in text:
        return "추가 검증 전까지 해석에 제한이 있다."
    if "추가 검증" in text and "필요" in text:
        return "추가 검증 전까지 해석에 제한이 있다."
    if "추가 검토" in text and "필요" in text:
        return "추가 검토 전까지 해석에 제한이 있다."
    if "추가 확인" in text and "필요" in text:
        return "추가 확인 전까지 해석에 제한이 있다."
    if "인과관계" in text and "펀더멘털" in text and any(token in text for token in ("주가", "거래량", "시장", "가격")):
        return "시장 가격 신호와 펀더멘털 사이의 직접적 인과관계는 제한적이다."
    monitoring_noun_need = re.match(r"(.+?)\s*모니터링\s*필요(?:가\s*)?(?:있다|있음)?\.?$", text)
    if monitoring_noun_need:
        return monitoring_target_text(monitoring_noun_need.group(1))
    malformed_monitoring = re.match(r"(.+?)\s*(?:지속\s*)?(?:주의\s*깊은\s*)?모니터링이\s*해석에\s*제한이\s*(?:있다|있음).*", text)
    if malformed_monitoring:
        return monitoring_target_text(malformed_monitoring.group(1))
    monitoring_need = re.match(
        r"(.+?)(?:은|는)?\s*(?:지속\s*)?(?:주의\s*깊은\s*)?모니터링이\s*(?:필요|요구)(?:하다|함|됨|된다)?(?:\s*\(.+\))?\.?$",
        text,
    )
    if monitoring_need:
        return monitoring_target_text(monitoring_need.group(1))
    if "신중한 해석" in text and "필요" in text:
        return text.replace("신중한 해석이 필요하다", "해석의 불확실성이 남아 있다")
    watch_match = re.match(
        r"(.+?)(?:을|를)?\s*(?:면밀히\s*)?(?:지속\s*)?(?:모니터링|관찰)할\s*(?:필요가\s*)?(?:있다|있음|것)\.?$",
        text,
    )
    if watch_match:
        return monitoring_target_text(watch_match.group(1))
    if "모니터링" in text and "필요" in text:
        rewritten = re.sub(r"(.+?)에\s*모니터링이\s*필요(?:하다|함)\.?$", r"\1은 지속 관찰 대상이다.", text)
        if rewritten != text:
            return rewritten
    if "대비할 필요" in text:
        rewritten = re.sub(r"(.+?)에\s*대비할\s*필요가\s*(?:있다|있음)\.?$", r"\1은 지속 관찰 대상이다.", text)
        if rewritten != text:
            return rewritten
    if re.search(r"확인\s*필요", text):
        confirm_match = re.match(r"(.+?)\s*확인\s*필요\.?$", text)
        if confirm_match:
            return topic_sentence(confirm_match.group(1), "확인 전까지 해석에 제한이 있다.")
    if "해석에 주의" in text:
        text = text.replace("해석에 주의가 필요하다", "해석에 제한이 있다")
        text = text.replace("해석에 주의가 필요함", "해석에 제한이 있음")
    news_risk_match = re.match(r"(?:News|뉴스)\s*주요\s*리스크/주의\s*이슈\((.+?)\).*", text)
    if news_risk_match:
        return f"{news_risk_match.group(1)} 관련 이슈는 재무 주장 지속성 해석의 보조 검토 대상이다."
    if "직접 증거" in text:
        if any(token in text for token in ("뉴스", "News", "촉매")):
            return "뉴스 촉매와 재무 수치 사이의 직접 연결성은 제한적이다."
        if any(token in text for token in ("주가", "시장", "가격", "거래량")):
            return "시장 가격 신호와 펀더멘털 사이의 직접 연결성은 제한적이다."
        return "자료 간 직접 연결성은 제한적이다."
    replacements = {
        "주장하지 않는다": "확인하기 어렵다",
        "단정하지 않는다": "단정하기 어렵다",
        "단정하지 않음": "단정하기 어려움",
        "검증할 것": "추가 확인 전까지 해석에 제한이 있다",
        "확인할 것": "추가 확인 전까지 해석에 제한이 있다",
        "검토할 필요가 있음": "추가 검토 전까지 해석에 제한이 있다",
        "대비할 필요가 있음": "지속 관찰 대상이다",
        "요인으로만 사용한다": "요인으로 제한적으로 활용된다",
        "사용한다": "활용에 제한이 있다",
        "재무 claim": "재무 주장",
        "사용할 것": "활용에 제한이 있다",
        "활용할 것": "활용에 제한이 있다",
        "낮출 것": "제한적이다",
        "주의가 필요하다": "해석에 제한이 있다",
        "주의가 필요함": "해석에 제한이 있음",
        "필요가 있다": "해석에 제한이 있다",
        "필요가 있음": "해석에 제한이 있음",
        "필요가 있어": "해석에 제한이 있어",
        "필요하다": "해석에 제한이 있다",
        "필요함": "해석에 제한이 있음",
    }
    for source, target in replacements.items():
        text = text.replace(source, target)
    if is_internal_instruction_text(text):
        return ""
    return text


def monitoring_target_text(subject: str) -> str:
    """Render monitoring subjects without duplicated Korean particles."""

    cleaned = clean_topic_subject(subject)
    return f"{cleaned}{topic_particle(cleaned)} 모니터링 항목이다." if cleaned else "모니터링 항목이다."


def topic_sentence(subject: str, predicate: str) -> str:
    """Render a Korean topic sentence with a clean subject particle."""

    cleaned = clean_topic_subject(subject)
    return f"{cleaned}{topic_particle(cleaned)} {predicate}" if cleaned else predicate


def clean_topic_subject(subject: str) -> str:
    """Clean a phrase before adding a topic particle."""

    cleaned = clean_text(subject)
    cleaned = re.sub(r"\s*\([^)]*\)\s*$", "", cleaned)
    cleaned = re.sub(r"\s*가능성이\s*있어$", " 가능성", cleaned)
    return re.sub(r"\s*(?:은|는|이|가|을|를)$", "", cleaned)


def topic_particle(text: str) -> str:
    """Return Korean topic particle for the last Hangul syllable."""

    for character in reversed(clean_text(text)):
        code = ord(character)
        if 0xAC00 <= code <= 0xD7A3:
            return "은" if (code - 0xAC00) % 28 else "는"
    return "은"


def is_internal_instruction_text(text: str) -> bool:
    """Detect prompt-style instructions that should not be rendered as limitations."""

    lowered = text.lower()
    english_markers = ("do not ", "must ", "should ", "prefer ", "use only ", "treat ")
    if any(marker in lowered for marker in english_markers):
        return True
    return any(
        marker in text
        for marker in (
            "하지 말",
            "하지 않는다",
            "해야 함",
            "해야 한다",
            "할 것",
            "말 것",
            "낮춰야",
            "낮출 것",
            "낮춘다",
            "검증할 것",
            "사용한다",
        )
    )


def validate_input_bundle(bundle: dict[str, Any]) -> None:
    """Validate strategy_input_bundle.json shape."""

    require_dict(bundle, "input_bundle")
    target = require_dict(bundle.get("target_company"), "target_company")
    require_non_empty(target.get("company_name"), "target_company.company_name")
    require_non_empty(target.get("run_key"), "target_company.run_key")
    reports = require_dict(bundle.get("target_reports"), "target_reports")
    for key in ("financial", "news", "yfinance"):
        require_dict(reports.get(key), f"target_reports.{key}")
    validations = bundle.get("target_validation_evidence")
    if validations is not None:
        require_dict(validations, "target_validation_evidence")
        for key in ("financial", "news", "yfinance"):
            payload = require_dict(validations.get(key), f"target_validation_evidence.{key}")
            if not isinstance(payload.get("claims"), list):
                raise ValueError(f"target_validation_evidence.{key}.claims must be a list.")
    competitors = bundle.get("competitor_reports")
    if not isinstance(competitors, list):
        raise ValueError("competitor_reports must be a list.")
    for index, competitor in enumerate(competitors):
        require_non_empty(competitor.get("summary"), f"competitor_reports[{index}].summary")
        if not isinstance(competitor.get("strengths"), list):
            raise ValueError(f"competitor_reports[{index}].strengths must be a list.")
        if not isinstance(competitor.get("risks"), list):
            raise ValueError(f"competitor_reports[{index}].risks must be a list.")


def validate_content_plan(content_plan: dict[str, Any]) -> None:
    """Validate strategy_content_plan.json shape."""

    require_dict(content_plan, "content_plan")
    require_non_empty(content_plan.get("target_core_summary"), "target_core_summary")
    if not isinstance(content_plan.get("competitor_context"), list):
        raise ValueError("competitor_context must be a list.")
    require_dict(content_plan.get("comparison_points"), "comparison_points")
    if not isinstance(content_plan.get("decision_constraints"), list):
        raise ValueError("decision_constraints must be a list.")
    if "final_recommendation" in content_plan:
        raise ValueError("content_plan must not include final_recommendation.")


def validate_strategy_report(strategy_report: dict[str, Any]) -> None:
    """Validate final strategy_report.json shape."""

    require_dict(strategy_report, "strategy_report")
    require_non_empty(strategy_report.get("target_company_name"), "target_company_name")
    recommendation = require_dict(strategy_report.get("final_recommendation"), "final_recommendation")
    if recommendation.get("opinion") not in FINAL_RECOMMENDATIONS:
        raise ValueError("final_recommendation.opinion must be one of Buy/Hold/Sell.")
    require_non_empty(recommendation.get("summary"), "final_recommendation.summary")

    for section, keys in {
        "investment_thesis": ("thesis_1", "thesis_2"),
        "financial_view": ("revenue", "profitability", "cash_flow", "balance_sheet", "financial_interpretation"),
        "market_price_view": ("price_trend", "volume", "relative_strength", "market_interpretation"),
        "final_rationale": ("why_buy_hold_sell",),
    }.items():
        payload = require_dict(strategy_report.get(section), section)
        for key in keys:
            require_non_empty(payload.get(key), f"{section}.{key}")
    if recommendation.get("opinion") == "Hold":
        require_non_empty(get_path(strategy_report, ["investment_thesis", "thesis_3"]), "investment_thesis.thesis_3")

    for section, keys in {
        "catalyst_view": ("positive_catalysts", "business_expansion"),
        "risk_view": ("financial_risks", "regulatory_risks", "market_risks", "execution_risks"),
    }.items():
        payload = require_dict(strategy_report.get(section), section)
        for key in keys:
            if not isinstance(payload.get(key), list):
                raise ValueError(f"{section}.{key} must be a list.")

    consistency = require_dict(strategy_report.get("cross_agent_consistency_check"), "cross_agent_consistency_check")
    for key in ("confirmed_signals", "mixed_conflicting_signals"):
        if not isinstance(consistency.get(key), list):
            raise ValueError(f"cross_agent_consistency_check.{key} must be a list.")
    require_non_empty(consistency.get("strategy_implication"), "cross_agent_consistency_check.strategy_implication")

    peer = require_dict(strategy_report.get("peer_competitor_positioning"), "peer_competitor_positioning")
    for key in ("competitor_summary", "target_relative_strength", "target_relative_weakness"):
        if not isinstance(peer.get(key), list):
            raise ValueError(f"peer_competitor_positioning.{key} must be a list.")
    require_non_empty(peer.get("peer_based_investment_implication"), "peer_competitor_positioning.peer_based_investment_implication")

    for key in ("key_strengths", "key_risks"):
        if not isinstance(strategy_report.get(key), list):
            raise ValueError(f"{key} must be a list.")

    limitations = require_dict(strategy_report.get("limitations"), "limitations")
    for key in ("data_limitations", "interpretation_limitations", "monitoring_points"):
        if not isinstance(limitations.get(key), list):
            raise ValueError(f"limitations.{key} must be a list.")
    validate_reader_facing_limitations(limitations)
    require_dict(strategy_report.get("source_files"), "source_files")
    validate_conservative_language(strategy_report)
    validate_specific_evidence_language(strategy_report)
    validate_opinion_index(strategy_report.get("opinion_index"))


def validate_reader_facing_limitations(limitations: dict[str, Any]) -> None:
    """Reject internal instruction-style wording in Limitations."""

    text = " ".join(limitations_text_items(limitations))
    bad_markers = (
        "해야 한다",
        "해야 함",
        "하지 않는다",
        "하지 말",
        "할 것",
        "낮추어야",
        "낮춰야",
        "낮출 것",
        "검증할 것",
        "확인할 것",
        "사용한다",
        "주장하지 않는다",
        "필요하다",
        "필요함",
        "필요가 있다",
        "필요가 있음",
        "검토가 필요",
        "모니터링이 필요",
        "주의가 필요",
        "확인 필요",
    )
    found = [marker for marker in bad_markers if marker in text]
    if found:
        raise ValueError(f"limitations contains instruction-style wording: {', '.join(found)}")


def validate_conservative_language(strategy_report: dict[str, Any]) -> None:
    """Reject reports that overstate known source limitations."""

    text = json.dumps(strategy_report, ensure_ascii=False)
    period_terms = ("전년 대비", "YoY", "연간 개선")
    period_cautions = ("동일 기간 YoY", "집계 기준", "단순 비교", "기간 기준")
    if any(term in text for term in period_terms) and not any(caution in text for caution in period_cautions):
        raise ValueError("Period comparison is overstated without YTD/FULL_YEAR caution.")

    if ("주가 상승" in text or "거래량 증가" in text) and "펀더멘털" in text:
        market_cautions = ("직접적 인과관계", "직접 증거", "직접 연결성", "보조 검증", "신중", "확인하기 어렵", "제한적")
        if not any(caution in text for caution in market_cautions):
            raise ValueError("Market movement is being used too strongly as fundamental evidence.")

    recommendation = get_path(strategy_report, ["final_recommendation", "opinion"])
    if recommendation == "Buy":
        limitations = " ".join(limitations_text_items(strategy_report.get("limitations")))
        rationale = clean_text(get_path(strategy_report, ["final_rationale", "why_buy_hold_sell"]))
        if limitations and not any(token in rationale for token in ("관리 가능", "감안", "제약", "리스크")):
            raise ValueError("Buy recommendation must explain why constraints do not block Buy.")


def validate_specific_evidence_language(strategy_report: dict[str, Any]) -> None:
    """Reject unresolved vague risk placeholders in the final report."""

    text = json.dumps(strategy_report, ensure_ascii=False)
    vague_markers = ("뉴스 주요 리스크 이슈", "뉴스 주요 리스크", "뉴스 리스크")
    found = [marker for marker in vague_markers if marker in text]
    if found:
        raise ValueError(f"strategy_report contains vague news risk wording: {', '.join(found)}")


def validate_opinion_index(value: Any) -> None:
    """Validate critique-edit IDs when opinion_index is present."""

    if value is None:
        return
    if not isinstance(value, list):
        raise ValueError("opinion_index must be a list.")
    seen: set[str] = set()
    for index, item in enumerate(value):
        if not isinstance(item, dict):
            raise ValueError(f"opinion_index[{index}] must be an object.")
        item_id = clean_text(item.get("id"))
        if not item_id:
            raise ValueError(f"opinion_index[{index}].id is required.")
        if item_id in seen:
            raise ValueError(f"duplicate opinion_index id: {item_id}")
        seen.add(item_id)
        require_non_empty(item.get("section_path"), f"opinion_index[{index}].section_path")
        require_non_empty(item.get("text"), f"opinion_index[{index}].text")


def render_strategy_markdown(strategy_report: dict[str, Any]) -> str:
    """Render strategy_report.md."""

    recommendation = require_dict(strategy_report.get("final_recommendation"), "final_recommendation")
    thesis = require_dict(strategy_report.get("investment_thesis"), "investment_thesis")
    financial = require_dict(strategy_report.get("financial_view"), "financial_view")
    catalyst = require_dict(strategy_report.get("catalyst_view"), "catalyst_view")
    risks = require_dict(strategy_report.get("risk_view"), "risk_view")
    market = require_dict(strategy_report.get("market_price_view"), "market_price_view")
    consistency = require_dict(strategy_report.get("cross_agent_consistency_check"), "cross_agent_consistency_check")
    peer = require_dict(strategy_report.get("peer_competitor_positioning"), "peer_competitor_positioning")
    final_rationale = require_dict(strategy_report.get("final_rationale"), "final_rationale")
    limitations = require_dict(strategy_report.get("limitations"), "limitations")

    lines = [
        f"## 0. Target Company Name: {clean_text(strategy_report.get('target_company_name')) or 'N/A'}",
        "",
        "## 1. Final Recommendation",
        f"- Opinion: {clean_text(recommendation.get('opinion')) or 'N/A'}",
        f"- Summary: {clean_text(recommendation.get('summary')) or 'N/A'}",
        "",
        "## 2. Investment Thesis",
        f"- Thesis 1: {clean_text(thesis.get('thesis_1')) or 'N/A'}",
        f"- Thesis 2: {clean_text(thesis.get('thesis_2')) or 'N/A'}",
    ]
    thesis_3 = clean_text(thesis.get("thesis_3"))
    if thesis_3:
        lines.append(f"- Thesis 3: {thesis_3}")
    lines.extend(
        [
            "",
            "## 3. Financial View",
        f"- Revenue: {clean_text(financial.get('revenue')) or 'N/A'}",
        f"- Profitability: {clean_text(financial.get('profitability')) or 'N/A'}",
        f"- Cash Flow: {clean_text(financial.get('cash_flow')) or 'N/A'}",
        f"- Balance Sheet: {clean_text(financial.get('balance_sheet')) or 'N/A'}",
        f"- Financial Interpretation: {clean_text(financial.get('financial_interpretation')) or 'N/A'}",
        "",
        "## 4. Catalyst View",
        f"- Positive Catalysts: {render_inline_items(catalyst.get('positive_catalysts'))}",
        f"- Business Expansion: {render_inline_items(catalyst.get('business_expansion'))}",
        "",
        "## 5. Risk View (분류형 상세 리스크)",
        f"- Financial Risks: {render_inline_items(risks.get('financial_risks'))}",
        f"- Regulatory Risks: {render_inline_items(risks.get('regulatory_risks'))}",
        f"- Market Risks: {render_inline_items(risks.get('market_risks'))}",
        f"- Execution Risks: {render_inline_items(risks.get('execution_risks'))}",
        "",
        "## 6. Market / Price View",
        f"- Price Trend: {clean_text(market.get('price_trend')) or 'N/A'}",
        f"- Volume: {clean_text(market.get('volume')) or 'N/A'}",
        f"- Relative Strength: {clean_text(market.get('relative_strength')) or 'N/A'}",
        f"- Market Interpretation: {clean_text(market.get('market_interpretation')) or 'N/A'}",
        "",
        "## 7. Cross-Agent Consistency Check",
        f"- Confirmed Signals: {render_inline_items(consistency.get('confirmed_signals'))}",
        f"- Mixed / Conflicting Signals: {render_inline_items(consistency.get('mixed_conflicting_signals'))}",
        f"- Strategy Implication: {clean_text(consistency.get('strategy_implication')) or 'N/A'}",
        "",
        "## 8. Peer / Competitor Positioning",
        f"- Competitor Summary: {render_inline_items(peer.get('competitor_summary'))}",
        f"- Target Relative Strength: {render_inline_items(peer.get('target_relative_strength'))}",
        f"- Target Relative Weakness: {render_inline_items(peer.get('target_relative_weakness'))}",
        f"- Peer-based Investment Implication: {clean_text(peer.get('peer_based_investment_implication')) or 'N/A'}",
        "",
        "## 9. Key Strengths",
        ]
    )
    key_strengths = text_items(strategy_report.get("key_strengths"))
    lines.extend(f"- {item}" for item in key_strengths) if key_strengths else lines.append("- N/A")
    lines.extend(["", "## 10. Key Risks"])
    key_risks = text_items(strategy_report.get("key_risks"))
    lines.extend(f"- {item}" for item in key_risks) if key_risks else lines.append("- N/A")
    lines.extend(
        [
            "",
            "## 11. Final Rationale",
            f"- Why Buy/Hold/Sell: {clean_text(final_rationale.get('why_buy_hold_sell')) or 'N/A'}",
            "",
            "## 12. Limitations",
            f"- Data limitations: {render_inline_items(limitations.get('data_limitations'))}",
            f"- Interpretation limitations: {render_inline_items(limitations.get('interpretation_limitations'))}",
            f"- Monitoring points: {render_inline_items(limitations.get('monitoring_points'))}",
            "",
        ]
    )
    return "\n".join(lines).rstrip() + "\n"


def build_decision_basis_card(
    strategy_report: dict[str, Any],
    decision_basis_by_section: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Build the legacy flat Decision Basis Card from path-level LLM basis entries."""

    recommendation = require_dict(strategy_report.get("final_recommendation"), "final_recommendation")
    basis_payload = decision_basis_by_section or normalize_decision_basis_by_section(None, strategy_report)
    basis_map = extract_decision_basis_map(basis_payload)
    buckets = {
        "basis_items": [],
        "risk_items": [],
        "decision_constraints_applied": [],
        "mixed_or_conflicting_signals": [],
        "monitoring_points": [],
        "limitations": [],
    }
    counters = {key: 0 for key in buckets}

    for section_path, entry in basis_map.items():
        for bucket, category in basis_card_destinations_for_path(section_path):
            counters[bucket] += 1
            buckets[bucket].append(
                basis_card_item_from_entry(
                    entry,
                    item_id=f"{bucket[:-1] if bucket.endswith('s') else bucket}_{counters[bucket]}",
                    category=category,
                )
            )

    card = {
        "target_company_name": clean_text(strategy_report.get("target_company_name")),
        "target_run_key": clean_text(strategy_report.get("target_run_key")),
        "final_recommendation": clean_text(recommendation.get("opinion")),
        "decision_summary": clean_text(recommendation.get("summary")),
        "basis_items": buckets["basis_items"],
        "risk_items": buckets["risk_items"],
        "decision_constraints_applied": buckets["decision_constraints_applied"],
        "mixed_or_conflicting_signals": buckets["mixed_or_conflicting_signals"],
        "strong_claims_in_report": build_strong_claim_items(strategy_report),
        "monitoring_points": buckets["monitoring_points"],
        "limitations": buckets["limitations"],
        "source_files": strategy_report.get("source_files") if isinstance(strategy_report.get("source_files"), dict) else {},
        "basis_card_version": BASIS_CARD_VERSION,
        "created_at": datetime.now().isoformat(timespec="seconds"),
    }
    return {"decision_basis_card": card}


def extract_decision_basis_map(payload: Any) -> dict[str, dict[str, Any]]:
    """Extract normalized path-level basis entries from a payload or direct map."""

    if isinstance(payload, dict) and isinstance(payload.get("decision_basis_by_section"), dict):
        raw_map = payload["decision_basis_by_section"]
    else:
        raw_map = unwrap_decision_basis_map(payload)
    return {clean_text(key): value for key, value in raw_map.items() if clean_text(key) and isinstance(value, dict)}


def basis_card_destinations_for_path(section_path: str) -> list[tuple[str, str]]:
    """Map a report path to flat-card buckets without adding new opinions."""

    path = clean_text(section_path)
    if path.startswith("risk_view."):
        return [("risk_items", risk_category_from_path(path))]
    if path.startswith("key_risks"):
        return [("risk_items", "summary_risk")]
    if path.startswith("limitations.monitoring_points"):
        return [("monitoring_points", "monitoring")]
    if path.startswith("limitations.data_limitations"):
        return [("decision_constraints_applied", "data_constraint"), ("limitations", "data")]
    if path.startswith("limitations.interpretation_limitations"):
        return [("decision_constraints_applied", "interpretation_constraint"), ("limitations", "interpretation")]
    if path.startswith("cross_agent_consistency_check.mixed_conflicting_signals") or path.startswith(
        "cross_agent_consistency_check.strategy_implication"
    ):
        return [("mixed_or_conflicting_signals", "cross_agent_consistency")]
    return [("basis_items", basis_category_from_path(path))]


def basis_category_from_path(section_path: str) -> str:
    """Return a compact category label derived from the report section path."""

    if section_path.startswith("financial_view"):
        return "financial"
    if section_path.startswith("catalyst_view"):
        return "business_catalyst"
    if section_path.startswith("market_price_view"):
        return "market_price"
    if section_path.startswith("peer_competitor_positioning"):
        return "peer_positioning"
    if section_path.startswith("key_strengths"):
        return "summary_strengths"
    if section_path.startswith("cross_agent_consistency_check"):
        return "cross_agent_consistency"
    return "recommendation"


def risk_category_from_path(section_path: str) -> str:
    """Return a risk category label derived from the report section path."""

    if ".financial_risks" in section_path:
        return "financial"
    if ".regulatory_risks" in section_path:
        return "regulatory"
    if ".market_risks" in section_path:
        return "market"
    if ".execution_risks" in section_path:
        return "execution"
    return "risk"


def basis_card_item_from_entry(entry: dict[str, Any], *, item_id: str, category: str) -> dict[str, Any]:
    """Convert one path-level basis entry into the legacy flat-card item shape."""

    section_path = clean_text(entry.get("section_path"))
    item = card_item(
        item_id=item_id,
        category=category,
        direction="source_text",
        claim=clean_text(entry.get("opinion_text")),
        reasoning=clean_text(entry.get("basis_summary")) or clean_text(entry.get("opinion_text")),
        evidence=basis_entry_evidence(entry),
        source_sections=[section_path],
        critique_focus=basis_entry_critique_focus(entry),
        opinion_id=clean_text(entry.get("opinion_id")),
        basis_path=section_path,
    )
    return item


def basis_entry_evidence(entry: dict[str, Any]) -> list[str]:
    """Return evidence text supplied by the LLM for one basis entry."""

    evidence = text_items(entry.get("key_numbers"))
    for item in ensure_list(entry.get("source_evidence")):
        if isinstance(item, dict):
            evidence.append(clean_text(item.get("evidence_text")))
            evidence.extend(text_items(item.get("evidence_ids")))
        else:
            evidence.append(clean_text(item))
    if not any(evidence):
        evidence.append(clean_text(entry.get("basis_summary")) or clean_text(entry.get("opinion_text")))
    return dedupe(evidence, 12)


def basis_entry_critique_focus(entry: dict[str, Any]) -> list[str]:
    """Use LLM-written limitations as critique focus without adding canned prose."""

    return text_items(entry.get("limitations"))


def build_strong_claim_items(strategy_report: dict[str, Any]) -> list[dict[str, Any]]:
    """Extract numeric, comparative, and causal claims that critique should verify first."""

    items: list[dict[str, Any]] = []
    for source_section, claim in iter_report_claims(strategy_report):
        claim_type = classify_strong_claim(claim)
        if not claim_type:
            continue
        items.append(
            {
                "id": f"strong_claim_{len(items) + 1}",
                "claim": claim,
                "source_sections": [source_section],
                "verification_focus": verification_focus_for_claim_type(claim_type),
            }
        )
        if len(items) >= 24:
            break
    return items


def iter_report_claims(value: Any, prefix: str = "") -> list[tuple[str, str]]:
    """Flatten report text into source-section/claim pairs."""

    claims: list[tuple[str, str]] = []
    if isinstance(value, dict):
        for key, child in value.items():
            if key in {"agent_name", "target_company_name", "target_run_key", "source_files", "opinion_index", "created_at", "output_version"}:
                continue
            child_prefix = f"{prefix}.{key}" if prefix else key
            claims.extend(iter_report_claims(child, child_prefix))
    elif isinstance(value, list):
        for index, child in enumerate(value):
            child_prefix = f"{prefix}[{index}]"
            claims.extend(iter_report_claims(child, child_prefix))
    else:
        text = clean_text(value)
        if text:
            claims.append((prefix, text))
    return claims


def classify_strong_claim(claim: str) -> str:
    """Classify a claim worth prioritizing for critique."""

    if re.search(r"\d", claim) or any(token in claim for token in ("억원", "%", "배", "원")):
        return "numeric_or_metric"
    if any(token in claim for token in ("대비", "상대", "경쟁사", "우위", "약세", "강세", "증가", "감소", "개선")):
        return "comparison_or_direction"
    if any(token in claim for token in ("인해", "작용", "뒷받침", "정당화", "연결", "기여", "제한")):
        return "causal_or_interpretive"
    return ""


def verification_focus_for_claim_type(claim_type: str) -> list[str]:
    """Return critique focus by strong claim type."""

    mapping = {
        "numeric_or_metric": ["수치가 원천 input data와 일치하는지 확인", "기간 기준과 단위가 올바른지 확인"],
        "comparison_or_direction": ["비교 기준이 동일한지 확인", "상대/방향성 표현이 과장되지 않았는지 확인"],
        "causal_or_interpretive": ["인과 또는 해석 연결이 input data로 충분히 뒷받침되는지 확인"],
    }
    return mapping.get(claim_type, ["input data 근거 확인"])


def card_item(
    *,
    item_id: str,
    category: str,
    direction: str,
    claim: str,
    reasoning: str,
    evidence: list[str],
    source_sections: list[str],
    critique_focus: list[str],
    opinion_id: str = "",
    basis_path: str = "",
) -> dict[str, Any]:
    """Create a normalized object-array item for the Decision Basis Card."""

    item = {
        "id": item_id,
        "category": category,
        "direction": direction,
        "claim": clean_text(claim),
        "reasoning": clean_text(reasoning),
        "evidence": dedupe(text_items(evidence), 12),
        "source_sections": dedupe(text_items(source_sections), 8),
        "critique_focus": dedupe(text_items(critique_focus), 8),
    }
    if opinion_id:
        item["opinion_id"] = clean_text(opinion_id)
    if basis_path:
        item["basis_path"] = clean_text(basis_path)
    return item


def validate_decision_basis_by_section(payload: dict[str, Any], strategy_report: dict[str, Any]) -> None:
    """Validate decision_basis_by_section.json shape against strategy_report.json."""

    require_dict(payload, "decision_basis_by_section_payload")
    basis_map = require_dict(payload.get("decision_basis_by_section"), "decision_basis_by_section")
    if payload.get("final_recommendation") not in FINAL_RECOMMENDATIONS:
        raise ValueError("decision_basis_by_section.final_recommendation must be one of Buy/Hold/Sell.")
    require_non_empty(payload.get("target_company_name"), "decision_basis_by_section.target_company_name")
    require_non_empty(payload.get("target_run_key"), "decision_basis_by_section.target_run_key")
    require_non_empty(payload.get("basis_card_version"), "decision_basis_by_section.basis_card_version")

    expected_paths = {
        clean_text(item.get("section_path")): clean_text(item.get("id"))
        for item in ensure_list(strategy_report.get("opinion_index"))
        if isinstance(item, dict)
    }
    for section_path, opinion_id in expected_paths.items():
        if section_path not in basis_map:
            raise ValueError(f"decision_basis_by_section missing path: {section_path}")
        entry = require_dict(basis_map.get(section_path), f"decision_basis_by_section.{section_path}")
        require_non_empty(entry.get("opinion_id"), f"decision_basis_by_section.{section_path}.opinion_id")
        if opinion_id and clean_text(entry.get("opinion_id")) != opinion_id:
            raise ValueError(f"decision_basis_by_section.{section_path}.opinion_id does not match opinion_index.")
        require_non_empty(entry.get("section_path"), f"decision_basis_by_section.{section_path}.section_path")
        require_non_empty(entry.get("opinion_text"), f"decision_basis_by_section.{section_path}.opinion_text")
        require_non_empty(entry.get("basis_summary"), f"decision_basis_by_section.{section_path}.basis_summary")
        validate_basis_summary_quality(section_path, entry)
        if not isinstance(entry.get("key_numbers"), list):
            raise ValueError(f"decision_basis_by_section.{section_path}.key_numbers must be a list.")
        if not isinstance(entry.get("source_evidence"), list):
            raise ValueError(f"decision_basis_by_section.{section_path}.source_evidence must be a list.")
        if not isinstance(entry.get("limitations"), list):
            raise ValueError(f"decision_basis_by_section.{section_path}.limitations must be a list.")


def validate_basis_summary_quality(section_path: str, entry: dict[str, Any]) -> None:
    """Reject basis text that only repeats the Strategy opinion."""

    opinion_text = clean_text(entry.get("opinion_text"))
    basis_summary = clean_text(entry.get("basis_summary"))
    if normalize_for_basis_comparison(opinion_text) == normalize_for_basis_comparison(basis_summary):
        raise ValueError(f"decision_basis_by_section.{section_path}.basis_summary repeats opinion_text.")
    if not has_input_based_basis_signal(entry):
        raise ValueError(f"decision_basis_by_section.{section_path}.basis_summary lacks input-based evidence.")


def normalize_for_basis_comparison(text: str) -> str:
    """Normalize Korean/English text for exact opinion-vs-basis repetition checks."""

    return re.sub(r"[\W_]+", "", clean_text(text).lower())


def has_input_based_basis_signal(entry: dict[str, Any]) -> bool:
    """Return True when a basis entry points to concrete input evidence or limitations."""

    if text_items(entry.get("key_numbers")):
        return True
    if normalize_basis_source_evidence(entry.get("source_evidence")):
        return True
    basis_summary = clean_text(entry.get("basis_summary"))
    input_markers = (
        "input",
        "Financial",
        "News",
        "YFinance",
        "Y-Finance",
        "Competitor",
        "DART",
        "SY",
        "검증",
        "확인",
        "근거",
        "입력",
        "보고서",
        "수치",
        "매출",
        "공헌이익률",
        "판관비율",
        "EPS",
        "초과수익률",
        "상대강도",
        "FDA",
        "관세",
        "제네릭",
    )
    if any(marker in basis_summary for marker in input_markers):
        return True
    return bool(text_items(entry.get("limitations")))


def validate_decision_basis_card(payload: dict[str, Any]) -> None:
    """Validate decision_basis_card.json shape."""

    require_dict(payload, "decision_basis_card_payload")
    card = require_dict(payload.get("decision_basis_card"), "decision_basis_card")
    if "recommendation_confidence" in card:
        raise ValueError("decision_basis_card must not include recommendation_confidence.")
    require_non_empty(card.get("target_company_name"), "decision_basis_card.target_company_name")
    require_non_empty(card.get("target_run_key"), "decision_basis_card.target_run_key")
    if card.get("final_recommendation") not in FINAL_RECOMMENDATIONS:
        raise ValueError("decision_basis_card.final_recommendation must be one of Buy/Hold/Sell.")
    require_non_empty(card.get("decision_summary"), "decision_basis_card.decision_summary")
    for key in (
        "basis_items",
        "risk_items",
        "decision_constraints_applied",
        "mixed_or_conflicting_signals",
        "strong_claims_in_report",
        "monitoring_points",
        "limitations",
    ):
        if not isinstance(card.get(key), list):
            raise ValueError(f"decision_basis_card.{key} must be a list.")
        for index, item in enumerate(card.get(key) or []):
            if not isinstance(item, dict):
                raise ValueError(f"decision_basis_card.{key}[{index}] must be an object.")
            require_non_empty(item.get("claim"), f"decision_basis_card.{key}[{index}].claim")
            if key != "strong_claims_in_report":
                require_non_empty(item.get("reasoning"), f"decision_basis_card.{key}[{index}].reasoning")
    require_dict(card.get("source_files"), "decision_basis_card.source_files")
    require_non_empty(card.get("basis_card_version"), "decision_basis_card.basis_card_version")


def load_competitor_reports(paths: list[Path]) -> list[dict[str, Any]]:
    """Load N competitor summary reports without hardcoding competitor count."""

    competitors: list[dict[str, Any]] = []
    for path in paths:
        resolved = path.expanduser().resolve()
        report = load_required_json(resolved, "Competitor summary")
        company = report.get("company") if isinstance(report.get("company"), dict) else {}
        competitor = {
            "company_name": company.get("company_name") or company_from_run_key(resolved.parent.name),
            "run_key": company.get("run_key") or resolved.parent.name,
            "ticker": company.get("ticker"),
            "as_of_date": company.get("as_of_date"),
            "summary": clean_text(report.get("summary")),
            "strengths": text_items(report.get("strengths")),
            "risks": text_items(report.get("risks")),
            "data_gaps": text_items(report.get("data_gaps")),
            "source_path": str(resolved),
        }
        if not competitor["summary"]:
            raise ValueError(f"Competitor report missing summary: {resolved}")
        competitors.append(competitor)
    return competitors


def infer_target_company(
    *,
    target_company_name: str,
    target_run_key: str,
    financial: dict[str, Any],
    news: dict[str, Any],
    yfinance: dict[str, Any],
) -> dict[str, Any]:
    """Infer normalized target identity."""

    news_entity = get_path(news, ["output", "target_entity"]) or {}
    company_name = first_non_empty(
        target_company_name,
        financial.get("target_company"),
        yfinance.get("target_company"),
        news_entity.get("company_name") if isinstance(news_entity, dict) else None,
        company_from_run_key(target_run_key),
    )
    ticker = first_non_empty(
        financial.get("ticker"),
        yfinance.get("ticker"),
        news_entity.get("ticker") if isinstance(news_entity, dict) else None,
    )
    corp_code = first_non_empty(
        financial.get("corp_code"),
        news_entity.get("corp_code") if isinstance(news_entity, dict) else None,
    )
    as_of_date = first_non_empty(
        financial.get("as_of_date"),
        yfinance.get("as_of_date"),
        news_entity.get("as_of_date") if isinstance(news_entity, dict) else None,
    )
    return {
        "company_name": company_name,
        "run_key": target_run_key,
        "as_of_date": normalize_iso_date(as_of_date) or as_of_date,
        "ticker": ticker,
        "corp_code": corp_code,
    }


def extract_decision_constraints(financial: dict[str, Any], news: dict[str, Any], yfinance: dict[str, Any]) -> list[str]:
    """Extract cautions that must constrain final recommendation."""

    constraints: list[str] = []
    constraints.extend(text_items(get_path(financial, ["main_view", "main_cautions"])))
    flags = get_path(financial, ["sy_handoff", "reconciliation_flags"]) or []
    for flag in ensure_list(flags):
        if isinstance(flag, dict):
            constraints.append(clean_text(flag.get("flag_ko")))
    constraints.extend(text_items(get_path(news, ["output", "analysis_blocks", "news_plus_financial_plus_market", "strategy_handoff_notes"])))
    yfinance_recon = get_path(yfinance, ["cross_data_reconciliation", "news_plus_dart_plus_market", "divergences"]) or []
    constraints.extend(text_items(yfinance_recon))
    return dedupe(constraints, 20)


def call_llm_json(
    *,
    prompt: str,
    payload: dict[str, Any],
    llm_provider: str,
    llm_model: str,
    llm_timeout: int,
    system_message: str,
) -> dict[str, Any]:
    """Call selected LLM and parse a JSON object response."""

    provider = resolve_llm_provider(llm_provider)
    model = resolve_llm_model(provider, llm_model)
    if provider == "none":
        raise RuntimeError("Strategy Agent requires OPENAI_API_KEY.")
    user_prompt = f"{prompt}\n\nInput JSON:\n{json.dumps(payload, ensure_ascii=False, indent=2)}"
    if provider != "openai":
        raise RuntimeError(f"Unsupported LLM provider: {provider}")
    result = call_openai(user_prompt, model, llm_timeout, system_message=system_message)
    return parse_llm_json(result["text"])


def call_openai(prompt: str, model: str, timeout: int, *, system_message: str) -> dict[str, Any]:
    """Call OpenAI chat completions with urllib."""

    api_key = os.getenv("OPENAI_API_KEY")
    if not api_key:
        raise RuntimeError("OPENAI_API_KEY is not set")
    base_url = os.getenv("OPENAI_BASE_URL", "https://api.openai.com/v1").rstrip("/")
    max_tokens = int(os.getenv("OPENAI_MAX_TOKENS", str(DEFAULT_OPENAI_MAX_TOKENS)))
    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": system_message},
            {"role": "user", "content": prompt},
        ],
        "response_format": {"type": "json_object"},
    }
    if uses_max_completion_tokens(model):
        payload["max_completion_tokens"] = max_tokens
    else:
        payload["temperature"] = 0.2
        payload["max_tokens"] = max_tokens
    req = request.Request(
        f"{base_url}/chat/completions",
        data=json.dumps(payload).encode("utf-8"),
        headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
        method="POST",
    )
    try:
        with request.urlopen(req, timeout=timeout) as response:
            raw = response.read().decode("utf-8")
    except error.HTTPError as exc:
        body = exc.read().decode("utf-8", "ignore")
        raise RuntimeError(f"OpenAI HTTP {exc.code}: {body[:500]}") from exc
    result = json.loads(raw)
    choices = result.get("choices") or []
    if not choices:
        raise RuntimeError(f"OpenAI returned no choices: {result}")
    text = choices[0].get("message", {}).get("content", "").strip()
    if not text:
        raise RuntimeError("OpenAI returned empty text")
    return {"text": text, "usage": result.get("usage", {})}


def uses_max_completion_tokens(model: str) -> bool:
    """Return True for models that reject the legacy max_tokens parameter."""

    return model.startswith(("gpt-5", "o1", "o3", "o4"))


def parse_llm_json(text: str) -> dict[str, Any]:
    """Parse JSON object from a model response."""

    cleaned = text.strip()
    cleaned = re.sub(r"^```(?:json)?", "", cleaned).strip()
    cleaned = re.sub(r"```$", "", cleaned).strip()
    start = cleaned.find("{")
    end = cleaned.rfind("}")
    if start == -1 or end == -1 or end <= start:
        raise ValueError("LLM response did not contain a JSON object.")
    payload = json.loads(cleaned[start : end + 1])
    if not isinstance(payload, dict):
        raise ValueError("LLM response JSON is not an object.")
    return payload


def resolve_llm_provider(provider: str) -> str:
    """Resolve LLM provider. OpenAI is the only runtime provider."""

    if provider not in {"", "auto", "openai"}:
        raise RuntimeError(f"Unsupported LLM provider: {provider}. Only openai is supported.")
    if provider == "openai":
        return "openai"
    if os.getenv("OPENAI_API_KEY"):
        return "openai"
    return "none"


def resolve_llm_model(provider: str, model: str) -> str:
    """Resolve default model for provider."""

    if model and model != "auto":
        return model
    if provider == "openai":
        return os.getenv("OPENAI_MODEL", DEFAULT_OPENAI_MODEL)
    return model or DEFAULT_OPENAI_MODEL


def content_plan_schema() -> dict[str, Any]:
    """Return required Content Planner schema for prompting."""

    return {
        "target_company": "string",
        "target_core_summary": "string",
        "target_strength_candidates": ["string"],
        "target_risk_candidates": ["string - one opinion plus concrete source-backed basis"],
        "competitor_context": [
            {
                "company_name": "string",
                "summary": "string",
                "strengths": ["string"],
                "risks": ["string"],
            }
        ],
        "comparison_points": {
            "target_possible_advantages": ["string - one opinion plus concrete source-backed basis"],
            "target_possible_disadvantages": ["string - one opinion plus concrete source-backed basis"],
            "mixed_or_uncertain_points": ["string - one opinion plus concrete source-backed basis"],
        },
        "decision_constraints": ["string - reader-facing constraint with concrete basis"],
        "report_outline": ["string"],
    }


def strategy_report_schema() -> dict[str, Any]:
    """Return required Strategy Report schema for prompting."""

    return {
        "agent_name": "Strategy Agent",
        "target_company_name": "string",
        "final_recommendation": {
            "opinion": "Buy | Hold | Sell",
            "summary": "string",
        },
        "investment_thesis": {
            "thesis_1": "string - opinion plus concrete basis",
            "thesis_2": "string - opinion plus concrete basis",
            "thesis_3": "Required when final_recommendation.opinion is Hold: explain why Buy is not yet justified using risks, market uncertainty, or limitations from the inputs.",
        },
        "financial_view": {
            "revenue": "string - opinion plus concrete financial basis",
            "profitability": "string - opinion plus concrete financial basis",
            "cash_flow": "string - opinion plus concrete financial basis",
            "balance_sheet": "string - opinion plus concrete financial basis",
            "financial_interpretation": "string - opinion plus concrete financial basis and limitation",
        },
        "catalyst_view": {
            "positive_catalysts": ["string - one catalyst plus concrete evidence"],
            "business_expansion": ["string - one expansion opinion plus concrete evidence"],
        },
        "risk_view": {
            "financial_risks": ["string - one risk plus concrete evidence"],
            "regulatory_risks": ["string - one risk plus concrete News/validation evidence"],
            "market_risks": ["string - one risk plus concrete market or News evidence"],
            "execution_risks": ["string - one risk plus concrete News/validation evidence"],
        },
        "market_price_view": {
            "price_trend": "string - opinion plus concrete market basis",
            "volume": "string - opinion plus concrete volume basis",
            "relative_strength": "string - opinion plus concrete relative performance basis",
            "market_interpretation": "string - opinion plus limitation on fundamental inference",
        },
        "cross_agent_consistency_check": {
            "confirmed_signals": ["string - one confirmed signal plus concrete basis"],
            "mixed_conflicting_signals": ["string - one conflicting signal plus concrete basis"],
            "strategy_implication": "string - conclusion plus concrete basis",
        },
        "peer_competitor_positioning": {
            "competitor_summary": ["string - competitor name plus concrete summary basis"],
            "target_relative_strength": ["string - one relative strength plus concrete basis"],
            "target_relative_weakness": ["string - one relative weakness plus concrete basis"],
            "peer_based_investment_implication": "string - conclusion plus concrete basis",
        },
        "key_strengths": ["string - one strength plus concrete basis"],
        "key_risks": ["string - one risk plus concrete basis"],
        "final_rationale": {
            "why_buy_hold_sell": "string - final opinion plus concrete basis and risk balancing",
        },
        "limitations": {
            "data_limitations": ["string - concrete data limitation"],
            "interpretation_limitations": ["string - concrete interpretation limitation"],
            "monitoring_points": ["string - concrete issue to monitor"],
        },
        "source_files": {
            "target_financial": "string",
            "target_news": "string",
            "target_yfinance": "string",
            "competitor_reports": ["string"],
        },
    }


def strategy_decision_output_schema() -> dict[str, Any]:
    """Return required Decision Agent schema for prompting."""

    return {
        "strategy_report": strategy_report_schema(),
        "decision_basis_by_section": {
            "<every non-empty editable strategy_report path>": decision_basis_entry_schema(),
        },
    }


def decision_basis_entry_schema() -> dict[str, Any]:
    """Return one path-level decision basis entry schema for prompting."""

    return {
        "opinion_id": "string - leave blank; system will assign OP ids after normalization if omitted",
        "section_path": "string - exact path in strategy_report, e.g. financial_view.revenue or risk_view.market_risks[0]",
        "opinion_text": "string - exact text written at the same strategy_report path",
        "basis_summary": "string - one concise Korean sentence, under 180 chars preferred; explain which input facts caused this opinion, without restating opinion_text",
        "key_numbers": ["string - max 5 numeric values, units, periods, or market indicators used"],
        "source_evidence": [
            {
                "agent": "Financial | News | YFinance | Competitor | Strategy",
                "claim_id": "string - validation claim id when available",
                "evidence_text": "string - concise evidence summary under 160 chars, not a long copied passage",
                "source_path": "string - source artifact path when available",
                "source_section": "string - source section or evidence id when available",
                "evidence_ids": ["string"],
            }
        ],
        "limitations": ["string - max 2 evidence limitations or uncertainties that affect this exact opinion"],
    }


def read_prompt(filename: str) -> str:
    """Read prompt file from prompts directory."""

    path = PROMPTS_DIR / filename
    if not path.exists():
        raise FileNotFoundError(f"Prompt file not found: {path}")
    return path.read_text(encoding="utf-8")


def load_required_json(path: Path, label: str) -> dict[str, Any]:
    """Load a required JSON object."""

    if not path.exists():
        raise FileNotFoundError(f"{label} path does not exist: {path}")
    payload = load_json(path)
    if not isinstance(payload, dict):
        raise ValueError(f"{label} JSON must be an object: {path}")
    return payload


def load_json(path: Path) -> Any:
    """Read JSON from path."""

    return json.loads(path.read_text(encoding="utf-8"))


def save_json(path: Path, payload: Any) -> None:
    """Write formatted JSON."""

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def save_text(path: Path, content: str) -> None:
    """Write UTF-8 text."""

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def load_env_file(path: Path) -> None:
    """Load KEY=VALUE pairs without overriding exported environment variables."""

    if not path.exists():
        return
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        if key and key not in os.environ:
            os.environ[key] = strip_env_value(value.strip())


def strip_env_value(value: str) -> str:
    """Remove matching shell-style quotes from env values."""

    if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
        return value[1:-1]
    return value


def load_identity_from_config(path: Path | None) -> dict[str, str]:
    """Read company config into a small identity dict."""

    if not path:
        return {}
    payload = load_json(path.expanduser().resolve())
    selected_date = normalize_date(payload.get("selected_date"))
    company_name = first_non_empty(payload.get("company_name"), payload.get("company_code"), "company")
    return {
        "company_name": company_name,
        "run_key": f"{safe_label(company_name)}_{selected_date}",
    }


def discover_competitor_reports(*, output_root: Path, target_run_key: str) -> list[Path]:
    """Find competitor_summary_report.json files under Output_total/Competitor."""

    root = output_root / "Competitor"
    if not root.exists():
        return []
    reports: list[Path] = []
    for child in sorted(root.iterdir()):
        if not child.is_dir() or child.name == target_run_key:
            continue
        path = child / "competitor_summary_report.json"
        if path.exists():
            reports.append(path)
    return reports


def infer_run_key_from_paths(paths: list[Path | None]) -> str | None:
    """Infer run_key from a final_report.json source path."""

    for path in paths:
        if path and path.name == "final_report.json":
            return path.expanduser().parent.name
    return None


def normalize_recommendation(value: Any) -> str:
    """Normalize LLM recommendation to Buy/Hold/Sell."""

    text = clean_text(value).lower()
    mapping = {
        "buy": "Buy",
        "hold": "Hold",
        "sell": "Sell",
    }
    if text in mapping:
        return mapping[text]
    for key, normalized in mapping.items():
        if key in text:
            return normalized
    raise ValueError(f"Invalid final_recommendation: {value}")


def require_dict(value: Any, label: str) -> dict[str, Any]:
    """Require a dict value."""

    if not isinstance(value, dict):
        raise ValueError(f"{label} must be an object.")
    return value


def require_non_empty(value: Any, label: str) -> None:
    """Require a non-empty string-like value."""

    if not clean_text(value):
        raise ValueError(f"{label} is required.")


def get_path(payload: Any, path: list[str]) -> Any:
    """Get nested dict value by path."""

    current = payload
    for key in path:
        if not isinstance(current, dict):
            return None
        current = current.get(key)
    return current


def first_non_empty(*values: Any) -> str:
    """Return first non-empty value as string."""

    for value in values:
        text = clean_text(value)
        if text:
            return text
    return ""


def clean_text(value: Any) -> str:
    """Normalize arbitrary values into compact one-line text."""

    if value is None:
        return ""
    if isinstance(value, str):
        text = value
    elif isinstance(value, (int, float)):
        text = str(value)
    else:
        return ""
    return " ".join(text.split()).strip()


def ensure_list(value: Any) -> list[Any]:
    """Normalize scalar/list values to a list."""

    if value is None:
        return []
    if isinstance(value, list):
        return value
    if isinstance(value, tuple):
        return list(value)
    return [value]


def text_items(value: Any) -> list[str]:
    """Extract readable text items from scalars, dicts, and lists."""

    items: list[str] = []
    for item in ensure_list(value):
        if isinstance(item, str):
            text = clean_text(item)
        elif isinstance(item, dict):
            text = first_non_empty(
                item.get("summary"),
                item.get("point"),
                item.get("text"),
                item.get("cross_analysis"),
                item.get("reasoning"),
                item.get("interpretation"),
                item.get("flag_ko"),
            )
        else:
            text = clean_text(item)
        if text:
            items.append(text)
    return items


def limitations_text_items(value: Any) -> list[str]:
    """Flatten structured limitation buckets into a string list."""

    if not isinstance(value, dict):
        return text_items(value)
    items: list[str] = []
    for key in ("data_limitations", "interpretation_limitations", "monitoring_points"):
        items.extend(text_items(value.get(key)))
    return items


def render_inline_items(value: Any) -> str:
    """Render a list-like field as one Markdown line."""

    items = text_items(value)
    return "; ".join(items) if items else "N/A"


def merge_limitations(base: Any, constraints: Any) -> list[str]:
    """Preserve model limitations and append source-derived decision constraints."""

    return dedupe(text_items(base) + text_items(constraints), 14)


def rewrite_conservative_language(value: Any) -> Any:
    """Rewrite common overstatements into period-aware conservative wording."""

    if isinstance(value, dict):
        return {key: rewrite_conservative_language(item) for key, item in value.items()}
    if isinstance(value, list):
        return [rewrite_conservative_language(item) for item in value]
    if not isinstance(value, str):
        return value

    text = value
    replacements = {
        "2024년 연간 대비 증가 추세(단순 비교 제한)": (
            "2024 ANNUAL FULL_YEAR와 단순 비교 시 높은 수준이나 동일 기간 YoY로 단정하지 않음"
        ),
        "2024년 연간 대비 증가 추세": (
            "2024 ANNUAL FULL_YEAR와 단순 비교 시 높은 수준이나 동일 기간 YoY로 단정하지 않음"
        ),
        "2024 연간 대비 증가 추세 확인(단, 기간 기준 차이 유의)": (
            "2024 ANNUAL FULL_YEAR와 단순 비교 시 높은 수준이나 동일 기간 YoY로 단정하지 않음"
        ),
        "2024 연간 대비 증가 추세": (
            "2024 ANNUAL FULL_YEAR와 단순 비교 시 높은 수준이나 동일 기간 YoY로 단정하지 않음"
        ),
        "2024 연간 대비": "2024 ANNUAL FULL_YEAR와 단순 비교 시",
        "2024년 대비 개선되어": "2024 ANNUAL FULL_YEAR 대비 개선 방향이나 동일 기간 YoY로 단정하지 않으며",
        "전년 대비 개선되어": "비교 기준 대비 개선 방향이나 동일 기간 YoY로 단정하지 않으며",
        "전년 대비 소폭 개선": "비교 기준 대비 소폭 개선 방향이나 동일 기간 YoY로 단정하지 않음",
        "전년 대비": "비교 기준 대비(동일 기간 YoY 아님)",
        "연간 개선 단정": "연간 개선으로 단정",
        "명확히 우위": "상대적으로 강하게 보임",
        "상대적으로 우위에 있다": "상대적으로 강하게 보인다",
        "동일 기간 YoY로 단정하지 않음이나": "동일 기간 YoY로 단정하지 않으며",
    }
    for source, target in replacements.items():
        text = text.replace(source, target)
    return text


def dedupe(items: list[str], limit: int | None = None) -> list[str]:
    """Dedupe strings while preserving order."""

    seen: set[str] = set()
    output: list[str] = []
    for item in items:
        text = clean_text(item)
        if not text:
            continue
        key = text.casefold()
        if key in seen:
            continue
        seen.add(key)
        output.append(text)
        if limit is not None and len(output) >= limit:
            break
    return output


def dedupe_paths(paths: list[Path]) -> list[Path]:
    """Dedupe paths while preserving order."""

    seen: set[str] = set()
    output: list[Path] = []
    for path in paths:
        key = str(path.expanduser().resolve())
        if key in seen:
            continue
        seen.add(key)
        output.append(path)
    return output


def normalize_date(value: Any) -> str:
    """Return YYYYMMDD for supported date inputs."""

    digits = "".join(character for character in str(value or "") if character.isdigit())
    if len(digits) != 8:
        raise ValueError("date must be YYYYMMDD or YYYY-MM-DD.")
    return digits


def normalize_iso_date(value: Any) -> str | None:
    """Return YYYY-MM-DD for date-like values."""

    digits = "".join(character for character in str(value or "") if character.isdigit())
    if len(digits) != 8:
        return None
    return f"{digits[:4]}-{digits[4:6]}-{digits[6:]}"


def safe_label(value: str | None, fallback: str = "company") -> str:
    """Sanitize labels for run_key path fragments."""

    label = str(value or fallback).strip() or fallback
    for character in ('\\', '/', ':', '*', '?', '"', '<', '>', '|'):
        label = label.replace(character, "_")
    return "_".join(label.split())


def company_from_run_key(run_key: str) -> str:
    """Infer company name from run_key."""

    match = re.match(r"^(?P<name>.+)_(?P<date>\d{8})$", run_key)
    if match:
        return match.group("name")
    return run_key
