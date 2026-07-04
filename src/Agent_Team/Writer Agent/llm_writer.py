"""LLM-backed chart selection and analyst writing layer for Writer Agent."""

from __future__ import annotations

import json
import os
from copy import deepcopy
from pathlib import Path
from typing import Any

from figure_selector import select_figures_by_ids


DEFAULT_LLM_MODEL = "gpt-5.4-mini"
MAX_SELECTED_FIGURES = 2


class LLMWriterUnavailable(RuntimeError):
    """Raised when the LLM writer is requested but cannot run."""


def apply_llm_writer(
    *,
    contract: dict[str, Any],
    strategy_report: dict[str, Any],
    strategy_content_plan: dict[str, Any] | None,
    decision_basis_by_section: dict[str, Any] | None,
    chart_manifest: dict[str, Any],
    visualization_dir: str | Path,
    model: str,
    api_key: str | None = None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Apply LLM-selected charts and LLM-written commentary to a contract."""

    resolved_api_key = api_key or os.getenv("OPENAI_API_KEY")
    if not resolved_api_key:
        raise LLMWriterUnavailable("OPENAI_API_KEY is not set.")

    context = _build_llm_context(
        contract=contract,
        strategy_report=strategy_report,
        strategy_content_plan=strategy_content_plan,
        decision_basis_by_section=decision_basis_by_section,
        chart_manifest=chart_manifest,
    )
    payload = _call_openai_writer(context=context, model=model, api_key=resolved_api_key)
    if _needs_peer_writer_payload(contract, payload):
        peer_payload = _call_openai_peer_writer(
            context=_build_peer_llm_context(contract=contract, strategy_report=strategy_report),
            model=model,
            api_key=resolved_api_key,
        )
        if isinstance(peer_payload.get("peer_comparison"), dict):
            payload["peer_comparison"] = peer_payload["peer_comparison"]
    updated = _merge_llm_payload(
        contract=contract,
        payload=payload,
        strategy_report=strategy_report,
        chart_manifest=chart_manifest,
        visualization_dir=visualization_dir,
        model=model,
    )
    editor_payload: dict[str, Any] | None = None
    editor_status = "not_requested"
    try:
        editor_payload = _call_openai_editor(
            context=_build_editor_context(contract=updated, strategy_report=strategy_report),
            model=model,
            api_key=resolved_api_key,
        )
        updated = _merge_llm_payload(
            contract=updated,
            payload=editor_payload,
            strategy_report=strategy_report,
            chart_manifest=chart_manifest,
            visualization_dir=visualization_dir,
            model=model,
        )
        editor_status = "applied"
    except LLMWriterUnavailable as exc:
        editor_status = f"skipped: {exc}"
    metadata = {
        "status": "applied",
        "model": model,
        "editor_status": editor_status,
        "selected_chart_ids": [block.get("figure_id") for block in updated.get("visual_report_blocks", [])],
        "raw_payload": payload,
        "raw_editor_payload": editor_payload or {},
    }
    updated["llm_writer"] = {
        key: value
        for key, value in metadata.items()
        if key not in {"raw_payload", "raw_editor_payload"}
    }
    return updated, metadata


def _needs_peer_writer_payload(contract: dict[str, Any], payload: dict[str, Any]) -> bool:
    peer = contract.get("peer_comparison")
    if not isinstance(peer, dict) or not peer.get("enabled"):
        return False
    return not isinstance(payload.get("peer_comparison"), dict)


def sanitize_contract_against_strategy(contract: dict[str, Any], strategy_report: dict[str, Any]) -> dict[str, Any]:
    """Remove overly similar Strategy wording from a report contract in-place."""

    _reduce_raw_copy_sequences(contract, strategy_report)
    _strengthen_risk_implications(contract)
    return contract


def _call_openai_writer(*, context: dict[str, Any], model: str, api_key: str) -> dict[str, Any]:
    try:
        from openai import OpenAI
    except Exception as exc:  # pragma: no cover - depends on environment
        raise LLMWriterUnavailable(f"openai package is unavailable: {exc}") from exc

    client = OpenAI(api_key=api_key)
    messages = [
        {
            "role": "system",
            "content": _system_prompt(),
        },
        {
            "role": "user",
            "content": json.dumps(context, ensure_ascii=False),
        },
    ]
    try:
        response = client.chat.completions.create(
            model=model,
            messages=messages,
            temperature=0.2,
            response_format={"type": "json_object"},
        )
    except Exception as exc:  # pragma: no cover - depends on network/API
        raise LLMWriterUnavailable(f"LLM writer request failed: {exc}") from exc
    content = response.choices[0].message.content
    if not content:
        raise LLMWriterUnavailable("LLM writer returned empty content.")
    try:
        payload = json.loads(content)
    except json.JSONDecodeError as exc:
        raise LLMWriterUnavailable(f"LLM writer returned invalid JSON: {exc}") from exc
    if not isinstance(payload, dict):
        raise LLMWriterUnavailable("LLM writer JSON must be an object.")
    return payload


def _call_openai_peer_writer(*, context: dict[str, Any], model: str, api_key: str) -> dict[str, Any]:
    try:
        from openai import OpenAI
    except Exception as exc:  # pragma: no cover - depends on environment
        raise LLMWriterUnavailable(f"openai package is unavailable: {exc}") from exc

    client = OpenAI(api_key=api_key)
    messages = [
        {
            "role": "system",
            "content": _peer_system_prompt(),
        },
        {
            "role": "user",
            "content": json.dumps(context, ensure_ascii=False),
        },
    ]
    try:
        response = client.chat.completions.create(
            model=model,
            messages=messages,
            temperature=0.2,
            response_format={"type": "json_object"},
        )
    except Exception as exc:  # pragma: no cover - depends on network/API
        raise LLMWriterUnavailable(f"Peer LLM writer request failed: {exc}") from exc
    content = response.choices[0].message.content
    if not content:
        raise LLMWriterUnavailable("Peer LLM writer returned empty content.")
    try:
        payload = json.loads(content)
    except json.JSONDecodeError as exc:
        raise LLMWriterUnavailable(f"Peer LLM writer returned invalid JSON: {exc}") from exc
    if not isinstance(payload, dict):
        raise LLMWriterUnavailable("Peer LLM writer JSON must be an object.")
    return payload


def _call_openai_editor(*, context: dict[str, Any], model: str, api_key: str) -> dict[str, Any]:
    try:
        from openai import OpenAI
    except Exception as exc:  # pragma: no cover - depends on environment
        raise LLMWriterUnavailable(f"openai package is unavailable: {exc}") from exc

    client = OpenAI(api_key=api_key)
    messages = [
        {
            "role": "system",
            "content": _editor_system_prompt(),
        },
        {
            "role": "user",
            "content": json.dumps(context, ensure_ascii=False),
        },
    ]
    try:
        response = client.chat.completions.create(
            model=model,
            messages=messages,
            temperature=0.15,
            response_format={"type": "json_object"},
        )
    except Exception as exc:  # pragma: no cover - depends on network/API
        raise LLMWriterUnavailable(f"LLM editor request failed: {exc}") from exc
    content = response.choices[0].message.content
    if not content:
        raise LLMWriterUnavailable("LLM editor returned empty content.")
    try:
        payload = json.loads(content)
    except json.JSONDecodeError as exc:
        raise LLMWriterUnavailable(f"LLM editor returned invalid JSON: {exc}") from exc
    if not isinstance(payload, dict):
        raise LLMWriterUnavailable("LLM editor JSON must be an object.")
    return payload


def _editor_system_prompt() -> str:
    return """
You are the automated sell-side editor for a Korean equity research Writer Agent.
Return JSON only. Do not include Markdown.

Your role is not to create new analysis. Improve the draft using interpretation_tasks:
- Remove generic commentary.
- Treat any deterministic scaffold wording as replaceable draft text, not as final prose.
- Make each chart takeaway answer the investment_question.
- Make each chart mention both positive_signal and counter_signal when available.
- Link every key takeaway to the unchanged recommendation.
- Improve monitoring triggers without inventing new numbers.
- Keep all constraints and interpretation limits.
- Prefer specific investment logic over repeated phrases such as "확인 필요", "보조 근거", "공격적 재평가".

Hard constraints:
- Do not change recommendation.
- Do not invent numbers, target price, valuation, PER/PBR/PSR/EV/Sales, DCF, upside, downside, global peer, or industry-average claims.
- Do not add peer companies not present in the draft.
- Keep Korean analyst tone concise and report-ready.
- Do not use the phrases "보조 근거", "공격적 재평가", or "긍정적이나 제한적". Replace them with specific risk/reward language.
- If the draft contains generic fallback wording, rewrite that field instead of returning it unchanged.

Return only fields that need improvement, using this JSON shape:
{
  "visual_report_blocks": [
    {"figure_id": "...", "what_chart_shows": "...", "analyst_takeaway": "...", "support_reason": "..."}
  ],
  "peer_comparison": {
    "peer_investment_commentary": "...",
    "relative_positioning_summary": "...",
    "analysis_cards": [
      {"title": "상대 우위", "body": "..."},
      {"title": "할인 요인", "body": "..."},
      {"title": "투자 판단 시사점", "body": "..."}
    ],
    "peer_chart_blocks": [
      {"figure_id": "...", "what_chart_shows": "...", "analyst_takeaway": "...", "support_reason": "..."}
    ],
    "peer_limitations_commentary": "..."
  },
  "reader_friendly_sections": {
    "final_rationale": {
      "positive_case": "...",
      "caution_case": "...",
      "balance_of_evidence": "...",
      "investment_conclusion": "...",
      "investment_implication": "...",
      "view_change_conditions": {
        "upside_conditions": ["...", "..."],
        "downside_conditions": ["...", "..."]
      }
    }
  }
}
""".strip()


def _peer_system_prompt() -> str:
    return """
You are the peer-comparison writing layer of a Korean equity research Writer Agent.
Return JSON only. Do not include Markdown.

Write analyst-quality Korean commentary for the peer_comparison section only.

Hard constraints:
- Do not change the recommendation.
- Do not invent numbers or peer companies.
- Use only domestic peer evidence visible in the input.
- Do not add target price, PER, PBR, PSR, EV/Sales, DCF, upside, or downside.
- Do not claim global peer comparison or industry-average comparison.
- Do not copy input wording verbatim. Rewrite in analyst commentary style.

Return exactly this JSON shape:
{
  "peer_comparison": {
    "peer_investment_commentary": "...",
    "relative_positioning_summary": "...",
    "analysis_cards": [
      {"title": "상대 우위", "body": "..."},
      {"title": "할인 요인", "body": "..."},
      {"title": "투자 판단 시사점", "body": "..."}
    ],
    "peer_chart_blocks": [
      {
        "figure_id": "...",
        "what_chart_shows": "...",
        "analyst_takeaway": "...",
        "support_reason": "..."
      }
    ],
    "peer_limitations_commentary": "..."
  }
}
""".strip()


def _system_prompt() -> str:
    return """
You are the Writer Agent inside a Korean equity research report pipeline.
Return JSON only. Do not include Markdown.

Your job:
1. Select up to 2 investor-facing charts from available_charts.
2. Write analyst-quality Korean commentary for the report.
3. Connect Strategy Agent logic, chart insights, and current recommendation.
4. When peer_comparison is available, use it only as domestic peer evidence and do not add valuation/global-peer claims.
5. If peer_comparison is available, rewrite its interpretation in analyst style. Keep the table numbers intact.
6. Use interpretation_tasks as mandatory guidance for chart and section interpretation.
7. Treat rule-based draft text as a fact scaffold only. Do not preserve scaffold wording unless it is a number, company name, section title, or required limitation.
8. Final interpretation sentences should come from your analyst reasoning over the provided facts, not from template-like fallback prose.

Hard constraints:
- Do not change report_metadata.recommendation.
- Do not invent target price, valuation, P/E, P/B, PER, PBR, ROE, ROA, OPM, upside, or downside.
- Do not invent numbers. Use only numbers visible in the input JSON.
- For peer_comparison, do not add peer companies that are not in the input table.
- Do not reinterpret charts beyond each chart's interpretation_limit and data_limitations.
- Do not select internal validation/evidence-map charts unless no investor-facing chart exists.
- Do not copy Strategy text verbatim. Rewrite in analyst commentary style.
- Never reuse 15 or more consecutive words/tokens from Strategy input. Paraphrase evidence instead.
- Avoid repeated generic phrases such as "확인 필요", "보조 근거", "공격적 재평가", "긍정적이나 제한적".
- Do not use "보조 근거" or "공격적 재평가" in final prose.
- Keep each field concise enough for an HTML report card.
- For each selected chart, answer its interpretation_tasks investment_question and include the recommendation link.

Return this JSON shape:
{
  "selected_chart_ids": ["figure_id_1", "figure_id_2"],
  "chart_selection_rationale": {"figure_id_1": "why this chart supports the investment view"},
  "main_investment_logic": "...",
  "cover_summary": {
    "headline": "...",
    "one_line_view": "...",
    "recommendation_rationale": "...",
    "key_debate": "...",
    "executive_summary": "...",
    "positive_signals": ["...", "..."],
    "negative_signals": ["...", "..."],
    "monitoring_points": ["...", "...", "..."]
  },
  "visual_report_blocks": [
    {
      "figure_id": "...",
      "what_chart_shows": "...",
      "analyst_takeaway": "...",
      "support_reason": "..."
    }
  ],
  "reader_friendly_sections": {
    "investment_summary": {
      "one_line_view": "...",
      "recommendation_rationale": "...",
      "key_debate": "..."
    },
    "financial_view_cards": [
      {
        "title": "...",
        "what_we_see": "...",
        "why_it_matters": "...",
        "what_to_watch": "...",
        "investment_implication": "..."
      }
    ],
    "market_view_cards": [
      {
        "title": "...",
        "what_we_see": "...",
        "why_it_matters": "...",
        "what_to_watch": "...",
        "investment_implication": "..."
      }
    ],
    "catalyst_analysis_cards": [
      {
        "catalyst_title": "...",
        "investment_relevance": "...",
        "evidence_from_strategy": "...",
        "what_to_watch": "...",
        "investment_impact": "..."
      }
    ],
    "risk_cards": [
      {
        "risk_type": "...",
        "description": "...",
        "impact": "...",
        "hold_connection": "..."
      }
    ],
    "final_rationale": {
      "positive_case": "...",
      "caution_case": "...",
      "balance_of_evidence": "...",
      "investment_conclusion": "...",
      "what_we_see": "...",
      "why_it_matters": "...",
      "what_to_watch": "...",
      "investment_implication": "...",
      "view_change_conditions": {
        "upside_conditions": ["...", "..."],
        "downside_conditions": ["...", "..."]
      }
    }
  },
  "peer_comparison": {
    "peer_investment_commentary": "...",
    "relative_positioning_summary": "...",
    "analysis_cards": [
      {"title": "상대 우위", "body": "..."},
      {"title": "할인 요인", "body": "..."},
      {"title": "투자 판단 시사점", "body": "..."}
    ],
    "peer_chart_blocks": [
      {
        "figure_id": "...",
        "what_chart_shows": "...",
        "analyst_takeaway": "...",
        "support_reason": "..."
      }
    ],
    "peer_limitations_commentary": "..."
  }
}
""".strip()


def _build_llm_context(
    *,
    contract: dict[str, Any],
    strategy_report: dict[str, Any],
    strategy_content_plan: dict[str, Any] | None,
    decision_basis_by_section: dict[str, Any] | None,
    chart_manifest: dict[str, Any],
) -> dict[str, Any]:
    metadata = contract.get("report_metadata", {})
    return {
        "task": "select charts and write Korean analyst report commentary",
        "commentary_policy": {
            "rule_based_scope": "facts, structure, candidate figures, numeric guardrails, validation only",
            "llm_scope": "final analyst interpretation, chart takeaways, peer commentary, catalyst/risk implication, final rationale",
            "draft_scaffold_policy": "Do not copy deterministic scaffold prose. Use it only to know section shape and factual constraints.",
        },
        "report_metadata": {
            "company_name": metadata.get("company_name"),
            "run_key": metadata.get("run_key"),
            "base_date": metadata.get("base_date"),
            "recommendation": metadata.get("recommendation"),
            "target_price_policy": "N/A; do not create target price",
        },
        "strategy_report": _compact_strategy(strategy_report),
        "strategy_content_plan": _compact_value(strategy_content_plan or {}, max_chars=10000),
        "decision_basis_by_section": _compact_value(decision_basis_by_section or {}, max_chars=10000),
        "available_charts": [_compact_chart(chart) for chart in chart_manifest.get("charts", [])],
        "interpretation_tasks": _compact_value(contract.get("interpretation_tasks", {}), max_chars=16000),
        "fact_scaffold": _build_fact_scaffold(contract),
    }


def _build_editor_context(*, contract: dict[str, Any], strategy_report: dict[str, Any]) -> dict[str, Any]:
    metadata = contract.get("report_metadata", {})
    return {
        "task": "edit Korean analyst report commentary using interpretation tasks",
        "commentary_policy": {
            "rule_based_scope": "validation and factual guardrails only",
            "editor_scope": "replace generic or deterministic-sounding prose with analyst commentary",
        },
        "report_metadata": {
            "company_name": metadata.get("company_name"),
            "run_key": metadata.get("run_key"),
            "base_date": metadata.get("base_date"),
            "recommendation": metadata.get("recommendation"),
        },
        "interpretation_tasks": _compact_value(contract.get("interpretation_tasks", {}), max_chars=18000),
        "draft_for_editing": _compact_value(
            {
                "visual_report_blocks": contract.get("visual_report_blocks", []),
                "peer_comparison": contract.get("peer_comparison", {}),
                "final_rationale": contract.get("reader_friendly_sections", {}).get("final_rationale", {}),
            },
            max_chars=18000,
        ),
        "strategy_guardrails": _compact_value(
            {
                "final_recommendation": strategy_report.get("final_recommendation"),
                "limitations": strategy_report.get("limitations"),
            },
            max_chars=5000,
        ),
    }


def _build_peer_llm_context(*, contract: dict[str, Any], strategy_report: dict[str, Any]) -> dict[str, Any]:
    metadata = contract.get("report_metadata", {})
    peer = contract.get("peer_comparison", {})
    return {
        "task": "write Korean analyst commentary for peer comparison section",
        "commentary_policy": {
            "rule_based_scope": "peer metrics, missing fields, chart facts, comparison limits",
            "llm_scope": "relative attractiveness interpretation and recommendation linkage",
            "draft_scaffold_policy": "Do not copy deterministic peer commentary. Use rows, ranks, and limits as facts only.",
        },
        "report_metadata": {
            "company_name": metadata.get("company_name"),
            "run_key": metadata.get("run_key"),
            "base_date": metadata.get("base_date"),
            "recommendation": metadata.get("recommendation"),
        },
        "strategy_peer_context": _compact_value(
            {
                "peer_competitor_positioning": strategy_report.get("peer_competitor_positioning"),
                "final_recommendation": strategy_report.get("final_recommendation"),
                "final_rationale": strategy_report.get("final_rationale"),
                "market_price_view": strategy_report.get("market_price_view"),
                "financial_view": strategy_report.get("financial_view"),
            },
            max_chars=10000,
        ),
        "peer_fact_scaffold": _compact_value(_peer_fact_scaffold(peer), max_chars=14000),
    }


def _build_fact_scaffold(contract: dict[str, Any]) -> dict[str, Any]:
    """Expose structure and facts to the LLM without deterministic prose anchors."""

    return {
        "selected_chart_ids_from_deterministic_fallback": [
            block.get("figure_id") for block in contract.get("visual_report_blocks", [])
        ],
        "key_metrics_table": contract.get("key_metrics_table", {}),
        "investment_view": contract.get("investment_view", {}),
        "section_structure": _section_structure_for_llm(contract.get("reader_friendly_sections", {})),
        "visual_report_blocks": [_chart_fact_scaffold(block) for block in contract.get("visual_report_blocks", [])],
        "peer_comparison": _peer_fact_scaffold(contract.get("peer_comparison", {})),
        "limitations": contract.get("limitations", {}),
    }


def _section_structure_for_llm(reader_sections: Any) -> dict[str, Any]:
    if not isinstance(reader_sections, dict):
        return {}
    return {
        "investment_summary_fields": ["one_line_view", "recommendation_rationale", "key_debate"],
        "financial_view_cards": [
            {
                "title": card.get("title"),
                "source_fields": card.get("source_fields", []),
            }
            for card in reader_sections.get("financial_view_cards", [])
            if isinstance(card, dict)
        ],
        "market_view_cards": [
            {
                "title": card.get("title"),
                "source_fields": card.get("source_fields", []),
            }
            for card in reader_sections.get("market_view_cards", [])
            if isinstance(card, dict)
        ],
        "catalyst_analysis_cards": [
            {
                "catalyst_title": card.get("catalyst_title"),
                "catalyst_group": card.get("catalyst_group"),
                "source_fields": card.get("source_fields", []),
            }
            for card in reader_sections.get("catalyst_analysis_cards", [])
            if isinstance(card, dict)
        ],
        "risk_cards": [
            {
                "risk_type": card.get("risk_type"),
                "monitoring_point": card.get("monitoring_point"),
                "source_fields": card.get("source_fields", []),
            }
            for card in reader_sections.get("risk_cards", [])
            if isinstance(card, dict)
        ],
        "final_rationale_fields": [
            "positive_case",
            "caution_case",
            "balance_of_evidence",
            "investment_conclusion",
            "investment_implication",
            "view_change_conditions",
        ],
    }


def _chart_fact_scaffold(block: Any) -> dict[str, Any]:
    if not isinstance(block, dict):
        return {}
    return {
        "figure_id": block.get("figure_id"),
        "display_title": block.get("display_title"),
        "section": block.get("section"),
        "strategy_support_fields": block.get("linked_strategy_fields", []),
        "chart_insights": block.get("chart_insights", {}),
        "data_snapshot": block.get("data_snapshot", {}),
        "interpretation_limit": block.get("interpretation_limit"),
        "recommended_report_rank": block.get("recommended_report_rank"),
        "support_score": block.get("support_score"),
    }


def _peer_fact_scaffold(peer: Any) -> dict[str, Any]:
    if not isinstance(peer, dict):
        return {}
    return {
        "enabled": peer.get("enabled"),
        "title": peer.get("title"),
        "subtitle": peer.get("subtitle"),
        "target_company": peer.get("target_company"),
        "peer_scope": peer.get("peer_scope"),
        "excluded_scope": peer.get("excluded_scope", []),
        "table_columns": peer.get("table_columns", []),
        "table_rows": peer.get("table_rows", []),
        "relative_positioning": peer.get("relative_positioning", {}),
        "peer_chart_blocks": [_chart_fact_scaffold(block) for block in peer.get("peer_chart_blocks", [])],
        "limitations": peer.get("limitations", []),
    }


def _compact_strategy(strategy_report: dict[str, Any]) -> dict[str, Any]:
    keys = [
        "target_company_name",
        "target_run_key",
        "final_recommendation",
        "investment_thesis",
        "financial_view",
        "market_price_view",
        "catalyst_view",
        "risk_view",
        "peer_competitor_positioning",
        "key_strengths",
        "key_risks",
        "final_rationale",
        "limitations",
        "opinion_index",
    ]
    return {key: _compact_value(strategy_report.get(key), max_chars=12000 if key == "opinion_index" else 5000) for key in keys if key in strategy_report}


def _compact_chart(chart: dict[str, Any]) -> dict[str, Any]:
    keys = [
        "figure_id",
        "title",
        "chart_type",
        "section_recommendation",
        "report_chart_role",
        "recommended_report_rank",
        "writer_priority_score",
        "strategy_support_fields",
        "strategy_support_summary",
        "selection_reason",
        "caption",
        "data_snapshot",
        "chart_insights",
        "analyst_takeaway",
        "interpretation_limit",
        "data_limitations",
        "writer_allowed_interpretation",
        "writer_forbidden_interpretation",
    ]
    return {key: _compact_value(chart.get(key), max_chars=3500) for key in keys if key in chart}


def _merge_llm_payload(
    *,
    contract: dict[str, Any],
    payload: dict[str, Any],
    strategy_report: dict[str, Any],
    chart_manifest: dict[str, Any],
    visualization_dir: str | Path,
    model: str,
) -> dict[str, Any]:
    updated = deepcopy(contract)
    selected_ids = _selected_chart_ids(payload, chart_manifest)
    if selected_ids:
        updated["visual_report_blocks"] = select_figures_by_ids(
            chart_manifest=chart_manifest,
            strategy_report=strategy_report,
            visualization_dir=visualization_dir,
            figure_ids=selected_ids,
            max_figures=MAX_SELECTED_FIGURES,
        )
    if payload.get("main_investment_logic"):
        updated["main_investment_logic"] = _clean_text(payload["main_investment_logic"])
    _merge_cover(updated, payload.get("cover_summary"))
    _merge_reader_sections(updated, payload.get("reader_friendly_sections"))
    _merge_visual_blocks(updated, payload)
    _merge_peer_comparison(updated, payload.get("peer_comparison"))
    _reduce_raw_copy_sequences(updated, strategy_report)
    _strengthen_risk_implications(updated)
    _mark_llm_commentary_application(updated, payload)
    updated.setdefault("llm_writer", {})
    updated["llm_writer"].update(
        {
            "status": "applied",
            "model": model,
            "selected_chart_ids": [block.get("figure_id") for block in updated.get("visual_report_blocks", [])],
        }
    )
    return updated


def _mark_llm_commentary_application(contract: dict[str, Any], payload: dict[str, Any]) -> None:
    generation = contract.setdefault("commentary_generation", {})
    generation["mode"] = "llm_first_with_deterministic_guardrails"
    generation["rule_based_scope"] = [
        "fact extraction",
        "chart asset validation",
        "numeric guardrails",
        "fallback only when LLM omits a field",
        "quality validation",
    ]
    generation["final_commentary_policy"] = (
        "Final interpretation should come from LLM commentary; deterministic text remains only as fallback."
    )
    existing = generation.get("llm_sections_updated")
    if not isinstance(existing, list):
        existing = []
    for section in _payload_sections(payload):
        if section not in existing:
            existing.append(section)
    generation["llm_sections_updated"] = existing


def _payload_sections(payload: dict[str, Any]) -> list[str]:
    sections: list[str] = []
    if _clean_text(payload.get("main_investment_logic")):
        sections.append("main_investment_logic")
    if isinstance(payload.get("cover_summary"), dict):
        sections.append("cover_summary")
    if isinstance(payload.get("visual_report_blocks"), list):
        sections.append("visual_report_blocks")
    reader = payload.get("reader_friendly_sections")
    if isinstance(reader, dict):
        for key in [
            "investment_summary",
            "financial_view_cards",
            "market_view_cards",
            "catalyst_analysis_cards",
            "risk_cards",
            "final_rationale",
        ]:
            if key in reader:
                sections.append(f"reader_friendly_sections.{key}")
    if isinstance(payload.get("peer_comparison"), dict):
        sections.append("peer_comparison")
    return sections


def _selected_chart_ids(payload: dict[str, Any], chart_manifest: dict[str, Any]) -> list[str]:
    allowed = {
        str(chart.get("figure_id"))
        for chart in chart_manifest.get("charts", [])
        if chart.get("figure_id") and str(chart.get("report_chart_role", "")) != "decision_evidence"
    }
    raw_ids = payload.get("selected_chart_ids")
    if not isinstance(raw_ids, list):
        return []
    selected = []
    for figure_id in raw_ids:
        figure_id = str(figure_id)
        if figure_id in allowed and figure_id not in selected:
            selected.append(figure_id)
        if len(selected) >= MAX_SELECTED_FIGURES:
            break
    return selected


def _merge_cover(contract: dict[str, Any], cover_payload: Any) -> None:
    if not isinstance(cover_payload, dict):
        return
    cover = contract.setdefault("cover_summary", {})
    for key in ["headline", "one_line_view", "recommendation_rationale", "key_debate", "executive_summary"]:
        value = _clean_text(cover_payload.get(key))
        if value:
            cover[key] = value
    for key in ["positive_signals", "negative_signals", "monitoring_points"]:
        values = _clean_list(cover_payload.get(key), limit=4)
        if values:
            cover[key] = values


def _merge_reader_sections(contract: dict[str, Any], reader_payload: Any) -> None:
    if not isinstance(reader_payload, dict):
        return
    reader = contract.setdefault("reader_friendly_sections", {})
    _merge_dict_fields(reader.setdefault("investment_summary", {}), reader_payload.get("investment_summary"), ["one_line_view", "recommendation_rationale", "key_debate"])
    _merge_card_list(reader.get("financial_view_cards", []), reader_payload.get("financial_view_cards"), ["what_we_see", "why_it_matters", "what_to_watch", "investment_implication"])
    _merge_card_list(reader.get("market_view_cards", []), reader_payload.get("market_view_cards"), ["what_we_see", "why_it_matters", "what_to_watch", "investment_implication"])
    _merge_card_list(
        reader.get("catalyst_analysis_cards", []),
        reader_payload.get("catalyst_analysis_cards"),
        ["investment_relevance", "evidence_from_strategy", "what_to_watch", "investment_impact"],
    )
    _merge_card_list(reader.get("risk_cards", []), reader_payload.get("risk_cards"), ["description", "impact", "hold_connection"])
    _merge_final_rationale(reader.setdefault("final_rationale", {}), reader_payload.get("final_rationale"))


def _merge_final_rationale(final: dict[str, Any], payload: Any) -> None:
    if not isinstance(payload, dict):
        return
    _merge_dict_fields(
        final,
        payload,
        [
            "positive_case",
            "caution_case",
            "balance_of_evidence",
            "investment_conclusion",
            "what_we_see",
            "why_it_matters",
            "what_to_watch",
            "investment_implication",
        ],
    )
    conditions = payload.get("view_change_conditions")
    if isinstance(conditions, dict):
        clean_conditions = {}
        for key in ["upside_conditions", "downside_conditions"]:
            values = _clean_list(conditions.get(key), limit=4)
            if values:
                clean_conditions[key] = values
        if clean_conditions:
            final["view_change_conditions"] = clean_conditions


def _merge_visual_blocks(contract: dict[str, Any], payload: dict[str, Any]) -> None:
    block_payloads = payload.get("visual_report_blocks")
    if not isinstance(block_payloads, list):
        block_payloads = []
    by_id = {
        str(item.get("figure_id")): item
        for item in block_payloads
        if isinstance(item, dict) and item.get("figure_id")
    }
    rationale = payload.get("chart_selection_rationale")
    for block in contract.get("visual_report_blocks", []):
        item = by_id.get(str(block.get("figure_id")), {})
        for key in ["what_chart_shows", "analyst_takeaway", "support_reason"]:
            value = _clean_text(item.get(key))
            if value:
                block[key] = value
        if isinstance(rationale, dict):
            reason = _clean_text(rationale.get(block.get("figure_id")))
            if reason:
                block["support_reason"] = reason


def _merge_peer_comparison(contract: dict[str, Any], peer_payload: Any) -> None:
    if not isinstance(peer_payload, dict):
        return
    peer = contract.get("peer_comparison")
    if not isinstance(peer, dict) or not peer.get("enabled"):
        return

    for key in ["peer_investment_commentary", "relative_positioning_summary", "peer_limitations_commentary"]:
        value = _clean_text(peer_payload.get(key))
        if value:
            peer[key] = value

    analysis_payload = peer_payload.get("analysis_cards")
    if isinstance(analysis_payload, list):
        _merge_peer_analysis_cards(peer.get("analysis_cards", []), analysis_payload)

    chart_payloads = peer_payload.get("peer_chart_blocks")
    if isinstance(chart_payloads, list):
        _merge_peer_chart_blocks(peer.get("peer_chart_blocks", []), chart_payloads)


def _merge_peer_analysis_cards(base_cards: Any, payload_cards: list[Any]) -> None:
    if not isinstance(base_cards, list):
        return
    payload_by_title = {
        _clean_text(item.get("title")): item
        for item in payload_cards
        if isinstance(item, dict) and _clean_text(item.get("title"))
    }
    for index, card in enumerate(base_cards):
        if not isinstance(card, dict):
            continue
        item = payload_by_title.get(_clean_text(card.get("title")))
        if item is None and index < len(payload_cards) and isinstance(payload_cards[index], dict):
            item = payload_cards[index]
        if not isinstance(item, dict):
            continue
        title = _clean_text(item.get("title"))
        body = _clean_text(item.get("body"))
        if title:
            card["title"] = title
        if body:
            card["body"] = body


def _merge_peer_chart_blocks(base_blocks: Any, payload_blocks: list[Any]) -> None:
    if not isinstance(base_blocks, list):
        return
    by_id = {
        str(item.get("figure_id")): item
        for item in payload_blocks
        if isinstance(item, dict) and item.get("figure_id")
    }
    for block in base_blocks:
        if not isinstance(block, dict):
            continue
        item = by_id.get(str(block.get("figure_id")))
        if not isinstance(item, dict):
            continue
        for key in ["what_chart_shows", "analyst_takeaway", "support_reason"]:
            value = _clean_text(item.get(key))
            if value:
                block[key] = value


def _reduce_raw_copy_sequences(contract: dict[str, Any], strategy_report: dict[str, Any]) -> None:
    strategy_tokens = _tokens(str(strategy_report))
    if len(strategy_tokens) < 15:
        return
    strategy_ngrams = {" ".join(strategy_tokens[index : index + 15]) for index in range(len(strategy_tokens) - 14)}
    for section_key in ["cover_summary", "visual_report_blocks", "peer_comparison", "reader_friendly_sections", "limitations"]:
        if section_key in contract:
            contract[section_key] = _clean_raw_copy_value(contract[section_key], strategy_ngrams, field_name=section_key)


def _clean_raw_copy_value(value: Any, strategy_ngrams: set[str], *, field_name: str) -> Any:
    if isinstance(value, dict):
        return {key: _clean_raw_copy_value(item, strategy_ngrams, field_name=str(key)) for key, item in value.items()}
    if isinstance(value, list):
        return [_clean_raw_copy_value(item, strategy_ngrams, field_name=field_name) for item in value]
    if isinstance(value, str) and _has_raw_copy_sequence(value, strategy_ngrams):
        if field_name == "evidence_from_strategy":
            return "해당 촉매는 해외 확장, 처방 기반 확대, 매출 확인 사례와 연결된 근거로 제시되어 있다."
        if field_name == "recommendation_rationale":
            return "수익성, 현금창출력, 재무 안정성은 긍정적이나 기간 기준 차이와 규제·정책 변수, 시장 대비 약세가 남아 있어 보수적 투자의견을 유지하는 것이 적절하다."
        if field_name == "caution_case":
            return "기간 기준 차이와 정책·규제 변수, 시장 대비 성과 부진은 긍정 요인을 즉시 공격적으로 반영하기 어렵게 만드는 확인 과제다."
        if field_name == "negative_signals":
            return "규제·정책 변수와 시장 대비 성과 부진이 투자 판단의 할인 요인으로 남아 있음"
        return "해당 근거는 투자 판단의 보조 변수로 활용하되, 최종 의견에는 잔존 리스크와 추가 확인 과제를 함께 반영한다."
    return value


def _has_raw_copy_sequence(text: str, strategy_ngrams: set[str]) -> bool:
    tokens = _tokens(text)
    if len(tokens) < 15:
        return False
    return any(" ".join(tokens[index : index + 15]) in strategy_ngrams for index in range(len(tokens) - 14))


def _strengthen_risk_implications(contract: dict[str, Any]) -> None:
    reader = contract.get("reader_friendly_sections", {})
    recommendation = str(contract.get("report_metadata", {}).get("recommendation", "")).strip()
    marker = recommendation or "투자의견"
    for card in reader.get("risk_cards", []):
        if not isinstance(card, dict):
            continue
        combined = f"{card.get('impact', '')} {card.get('hold_connection', '')}"
        if any(token in combined for token in [marker, "보수적", "리스크 할인", "투자의견", "판단"]):
            continue
        impact = _clean_text(card.get("impact"))
        suffix = f" 따라서 이 리스크는 {marker} 관점의 보수적 판단을 유지하게 만드는 요인이다."
        card["impact"] = (impact + suffix).strip() if impact else suffix.strip()


def _merge_dict_fields(target: dict[str, Any], payload: Any, fields: list[str]) -> None:
    if not isinstance(payload, dict):
        return
    for key in fields:
        value = _clean_text(payload.get(key))
        if value:
            target[key] = value


def _merge_card_list(base_cards: list[dict[str, Any]], payload_cards: Any, fields: list[str]) -> None:
    if not isinstance(base_cards, list) or not isinstance(payload_cards, list):
        return
    for index, card in enumerate(base_cards):
        if not isinstance(card, dict) or index >= len(payload_cards) or not isinstance(payload_cards[index], dict):
            continue
        _merge_dict_fields(card, payload_cards[index], fields)


def _clean_list(value: Any, *, limit: int) -> list[str]:
    if not isinstance(value, list):
        return []
    cleaned = []
    for item in value:
        text = _clean_text(item)
        if text:
            cleaned.append(text)
        if len(cleaned) >= limit:
            break
    return cleaned


def _clean_text(value: Any) -> str:
    if value is None or isinstance(value, (dict, list)):
        return ""
    return " ".join(str(value).split())


def _tokens(text: str) -> list[str]:
    return [token for token in "".join(character if character.isalnum() or character in ".-" else " " for character in text).split() if token]


def _compact_value(value: Any, *, max_chars: int) -> Any:
    if isinstance(value, dict):
        return {str(key): _compact_value(item, max_chars=max_chars) for key, item in value.items()}
    if isinstance(value, list):
        compacted = [_compact_value(item, max_chars=max_chars) for item in value[:24]]
        if len(value) > 24:
            compacted.append(f"... {len(value) - 24} more item(s)")
        return compacted
    if isinstance(value, str):
        text = " ".join(value.split())
        return text[:max_chars].rstrip() + ("..." if len(text) > max_chars else "")
    return value
