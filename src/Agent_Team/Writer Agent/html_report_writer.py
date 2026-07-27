"""Single-call LLM writer for the fixed-format HTML investment report."""

from __future__ import annotations

import hashlib
import json
import os
import re
from typing import Any

from html_report_spec import (
    KEY_EVIDENCE_DISPLAY_COLUMNS,
    REPORT_SECTIONS,
    RISK_DISPLAY_COLUMNS,
)
from shared.evidence_cards import PRODUCT_DISCLOSURE_SCOPE_LABEL
from shared.llm_clients import compact_json, execute_with_telemetry, is_transient_transport_error
from writer_handoff import (
    EDITORIAL_PACKET_VERSION,
    validate_writer_editorial_packet,
    validate_writer_handoff,
)


DEFAULT_LLM_MODEL = "gpt-5.4"
MISSING_VALUE = "데이터 추가 필요"
WRITER_CACHE_VERSION = "7"
DETERMINISTIC_WRITER_MODE = "deterministic"
FREE_FORM_WRITER_MODE = "free_form"
WRITER_MODES = {DETERMINISTIC_WRITER_MODE, FREE_FORM_WRITER_MODE}


class HTMLReportWriterUnavailable(RuntimeError):
    """Raised when the LLM HTML report writer cannot run."""


def writer_request_fingerprint(
    *,
    writer_handoff: dict[str, Any],
    model: str,
    writer_mode: str = DETERMINISTIC_WRITER_MODE,
) -> str:
    """Fingerprint every value that can change the single Writer LLM response."""

    _validate_writer_input(writer_handoff)
    writer_mode = _normalize_writer_mode(writer_mode)
    payload = {
        "cache_version": WRITER_CACHE_VERSION,
        "contract_version": _writer_contract_version(writer_handoff),
        "model": model,
        "writer_mode": writer_mode,
        "system_prompt": _system_prompt(
            _writer_contract_version(writer_handoff), writer_mode=writer_mode
        ),
        "context": _build_context(writer_handoff=writer_handoff, writer_mode=writer_mode),
        "response_format": writer_report_response_format(
            writer_handoff, writer_mode=writer_mode
        ),
    }
    return hashlib.sha256(compact_json(payload, sort_keys=True).encode("utf-8")).hexdigest()


def build_html_report_payload(
    *,
    writer_handoff: dict[str, Any],
    model: str = DEFAULT_LLM_MODEL,
    api_key: str | None = None,
    writer_mode: str = DETERMINISTIC_WRITER_MODE,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Generate one grounded report payload from the compact Writer handoff."""

    raw_payload, llm_output = request_html_report_payload(
        writer_handoff=writer_handoff,
        model=model,
        api_key=api_key,
        writer_mode=writer_mode,
    )
    validate_raw_writer_payload(raw_payload)
    normalized = normalize_report_payload(
        raw_payload,
        writer_handoff=writer_handoff,
        writer_mode=writer_mode,
    )
    return normalized, llm_output


def request_html_report_payload(
    *,
    writer_handoff: dict[str, Any],
    model: str = DEFAULT_LLM_MODEL,
    api_key: str | None = None,
    writer_mode: str = DETERMINISTIC_WRITER_MODE,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Request and return the raw Writer response before local validation."""

    _validate_writer_input(writer_handoff)
    resolved_api_key = api_key or os.getenv("OPENAI_API_KEY")
    if not resolved_api_key:
        raise HTMLReportWriterUnavailable("OPENAI_API_KEY is not set. Writer Agent requires LLM mode.")
    writer_mode = _normalize_writer_mode(writer_mode)
    context = _build_context(writer_handoff=writer_handoff, writer_mode=writer_mode)
    raw_payload = _call_openai_writer(
        context=context,
        model=model,
        api_key=resolved_api_key,
        response_format=writer_report_response_format(
            writer_handoff, writer_mode=writer_mode
        ),
    )
    return raw_payload, {
        "status": "applied",
        "model": model,
        "writer_mode": writer_mode,
        "call_count": 1,
        "raw_payload": raw_payload,
    }


def validate_raw_writer_payload(payload: dict[str, Any]) -> None:
    """Reject a Writer response that does not match the fixed report shell."""

    shape_errors = _raw_payload_shape_errors(payload)
    if shape_errors:
        raise HTMLReportWriterUnavailable(
            f"LLM writer returned invalid section structure: {shape_errors}"
        )


def normalize_report_payload(
    payload: dict[str, Any],
    *,
    writer_handoff: dict[str, Any],
    writer_mode: str = DETERMINISTIC_WRITER_MODE,
) -> dict[str, Any]:
    """Normalize item containers without writing analytical fallback sentences."""

    _validate_writer_input(writer_handoff)
    writer_mode = _normalize_writer_mode(writer_mode)
    is_v2 = _is_v2_writer_packet(writer_handoff)
    if is_v2:
        payload = _materialize_data_limit_claims_v2(payload, writer_handoff)
        payload = _enrich_writer_metadata_v2(payload, writer_handoff)
        if writer_mode == DETERMINISTIC_WRITER_MODE:
            payload = _apply_locked_thesis_v2(payload, writer_handoff)
            payload = _apply_deterministic_evidence_table_v2(payload, writer_handoff)
            payload = _apply_deterministic_risk_table_v2(payload, writer_handoff)
        payload = _replace_visible_card_keys(payload, writer_handoff)
    target = _dict(writer_handoff.get("target"))
    decision = _dict(writer_handoff.get("decision"))
    metadata = _dict(payload.get("metadata"))
    company_name = target.get("company_name") or MISSING_VALUE
    normalized: dict[str, Any] = {
        "metadata": {
            "company_name": company_name,
            "base_date": target.get("selected_date") or MISSING_VALUE,
            "recommendation": decision.get("opinion") or MISSING_VALUE,
            "investment_horizon": decision.get("investment_horizon") or MISSING_VALUE,
            "data_coverage": decision.get("data_coverage") or MISSING_VALUE,
            "decision_confidence": decision.get("decision_confidence") or MISSING_VALUE,
            "report_title": (
                f"{company_name} 투자 리서치"
                if is_v2
                else metadata.get("report_title") or f"{company_name} Investment Report"
            ),
        }
    }
    sections = _dict(payload.get("sections"))
    normalized_sections: dict[str, Any] = {}
    for section in REPORT_SECTIONS:
        section_payload = _dict(sections.get(section["key"]))
        normalized_items: dict[str, Any] = {}
        for item_key, _title, item_type in section["items"]:
            raw_item = section_payload.get(item_key)
            normalized_items[item_key] = (
                _normalize_table(raw_item, preserve_strategy_values=is_v2)
                if item_type == "table"
                else _normalize_text(
                    raw_item,
                    preserve_claim_units=is_v2,
                )
            )
        normalized_sections[section["key"]] = normalized_items
    normalized["sections"] = normalized_sections
    contract_version = _writer_contract_version(writer_handoff)
    normalized["generation"] = {
        "mode": (
            (
                "single_call_llm_free_form_writer_ablation_v2"
                if writer_mode == FREE_FORM_WRITER_MODE
                else "single_call_llm_with_editorial_cards_v2"
            )
            if contract_version == EDITORIAL_PACKET_VERSION
            else "single_call_llm_with_compact_handoff"
        ),
        "contract_version": contract_version,
        "missing_value_policy": MISSING_VALUE,
    }
    if not _is_v2_writer_packet(writer_handoff):
        normalized["generation"]["writer_handoff_version"] = writer_handoff.get("handoff_version")
    return normalized


def _call_openai_writer(
    *,
    context: dict[str, Any],
    model: str,
    api_key: str,
    response_format: dict[str, Any] | None = None,
) -> dict[str, Any]:
    try:
        from openai import OpenAI
    except Exception as exc:  # pragma: no cover - environment dependent
        raise HTMLReportWriterUnavailable(f"openai package is unavailable: {exc}") from exc

    timeout_seconds = max(1.0, float(os.getenv("LLM_TIMEOUT_SECONDS", "300")))
    transport_retries = max(0, int(os.getenv("LLM_TRANSPORT_RETRIES", "0")))
    client = OpenAI(api_key=api_key, timeout=timeout_seconds, max_retries=0)
    payload = _request_openai_json(
        client=client,
        model=model,
        messages=[
            {
                "role": "system",
                "content": _system_prompt(
                    str(context.get("contract_version") or ""),
                    writer_mode=str(
                        context.get("writer_mode") or DETERMINISTIC_WRITER_MODE
                    ),
                ),
            },
            {"role": "user", "content": compact_json(context)},
        ],
        response_format=response_format or {"type": "json_object"},
        transport_retries=transport_retries,
    )
    return payload


def _request_openai_json(
    *,
    client: Any,
    model: str,
    messages: list[dict[str, str]],
    response_format: dict[str, Any],
    transport_retries: int = 0,
) -> dict[str, Any]:
    request_payload = {
        "model": model,
        "messages": messages,
        "temperature": 0.2,
        "response_format": response_format,
    }
    try:
        response = execute_with_telemetry(
            lambda: client.chat.completions.create(**request_payload),
            request_payload=request_payload,
            model=model,
            step="writer:html_report",
            usage_getter=lambda result: getattr(result, "usage", None),
            max_attempts=transport_retries + 1,
            retry_predicate=is_transient_transport_error,
        )
    except Exception as exc:  # pragma: no cover - network/API dependent
        raise HTMLReportWriterUnavailable(f"LLM writer request failed: {exc}") from exc
    content = response.choices[0].message.content
    if not content:
        raise HTMLReportWriterUnavailable("LLM writer returned empty content.")
    try:
        payload = json.loads(content)
    except json.JSONDecodeError as exc:
        raise HTMLReportWriterUnavailable(f"LLM writer returned invalid JSON: {exc}") from exc
    if not isinstance(payload, dict):
        raise HTMLReportWriterUnavailable("LLM writer JSON must be an object.")
    return payload


def _build_context(
    *,
    writer_handoff: dict[str, Any],
    writer_mode: str = DETERMINISTIC_WRITER_MODE,
) -> dict[str, Any]:
    """Return the compact generation context for the one Writer call."""

    _validate_writer_input(writer_handoff)
    writer_mode = _normalize_writer_mode(writer_mode)
    if _is_v2_writer_packet(writer_handoff):
        return _build_context_v2(writer_handoff, writer_mode=writer_mode)
    return {
        "writer_mode": writer_mode,
        "task": "계층화된 근거를 사용해 한국어 one-paper 기업 리서치 리포트 payload를 작성한다.",
        "output_contract": _output_contract(),
        "section_role_guidance": _section_role_guidance(),
        "writing_rules": {
            "recommendation_lock": _dict(writer_handoff.get("decision")).get("opinion"),
            "use_only_writer_input": True,
            "grounding_refs_required": True,
            "missing_data_phrase": MISSING_VALUE,
            "hierarchy": [
                "decisive_positive_evidence",
                "decisive_negative_evidence",
                "structured domain evidence",
                "data_limits",
            ],
            "financial_policy": "동일 기간 재무 추세와 현금흐름을 우선하고 기간·단위·공시 기준을 보존한다.",
            "revenue_policy": "제품·서비스별 매출액과 비중은 revenue_breakdown의 현재 공시값만 사용하며 시장점유율을 만들지 않는다.",
            "valuation_policy": "선택일 계산 밸류에이션을 우선하고 날짜가 다른 provider-direct 값은 별도 참고값으로 구분한다.",
            "required_evidence_policy": (
                "required_key_evidence의 모든 display token을 key_evidence_table에 정확히 한 번씩 그대로 복사한다. "
                "반올림, 단위 환산, provider-direct 값 대체를 하지 않는다."
            ),
            "peer_policy": "peer_comparison의 명시된 1:1 비교만 사용하고 업종 평균이나 다른 경쟁사를 만들지 않는다.",
            "catalyst_policy": "catalysts의 서로 다른 이벤트만 사용하고 시장 반응이나 현재 실적을 촉매로 다시 만들지 않는다.",
            "risk_policy": (
                "risk_monitoring_table의 리스크 행은 writer_handoff.risks의 실제 observed risk와 1:1로 대응해야 하며 "
                "행 수는 risks 수를 넘지 않는다. data_limits, monitoring_points, 미공개 정보, 아직 확인되지 않은 촉매 기여를 "
                "새 리스크 행으로 승격하지 않는다. monitoring_points는 기존 risk의 확인 항목 또는 data_limits 섹션에만 사용한다."
            ),
            "no_new_information": "수치, 제품·서비스명, 회사명, 이벤트, 인과관계, 전망치를 새로 만들지 않는다.",
            "no_internal_narration": "Agent, prompt, validation workflow, 파일 경로, OP/claim/evidence ID를 독자 문장에 노출하지 않는다.",
            "no_forbidden_content": "목표주가, 컨센서스, 별도 투자의견 변경 시나리오를 작성하지 않는다.",
            "plain_korean": "누적·연간·비교 기업·촉매·확인 항목은 일반 투자자가 이해할 수 있는 한국어로 쓴다.",
            "deduplication": "같은 수치나 이벤트를 여러 섹션에 반복하지 않고 각 섹션의 질문에 필요한 역할로만 배치한다.",
            "text_density": "텍스트 섹션은 1~2개 압축 문단만 사용하고 bullets는 빈 배열로 둔다. 상세 원 단위 수치와 전체 비교값은 key_evidence_table에 배치한다.",
            "inline_html": ["<strong>"],
        },
        "writer_input": build_writer_llm_input(writer_handoff),
    }


def _build_context_v2(
    writer_packet: dict[str, Any],
    *,
    writer_mode: str = DETERMINISTIC_WRITER_MODE,
) -> dict[str, Any]:
    writer_mode = _normalize_writer_mode(writer_mode)
    required = _dict(writer_packet.get("required_card_keys_by_component"))
    free_form = writer_mode == FREE_FORM_WRITER_MODE
    return {
        "contract_version": EDITORIAL_PACKET_VERSION,
        "writer_mode": writer_mode,
        "task": "Strategy가 확정한 카드 해석을 보존해 한국어 one-paper 기업 리서치 payload를 편집한다.",
        "output_contract": _output_contract_v2(writer_packet, writer_mode=writer_mode),
        "section_role_guidance": _section_role_guidance(),
        "writing_rules": {
            "recommendation_lock": _dict(writer_packet.get("decision")).get("opinion"),
            "use_only_writer_input": True,
            "component_card_keys_exact": required,
            "recommendation_bridge_lock": _dict(writer_packet.get("recommendation_bridge")),
            "strategy_meaning_lock": (
                "각 카드의 strategy_interpretation과 investment_effect를 바꾸거나 새 인과관계로 확대하지 않는다. "
                "output_contract에 제시된 숨은 검증 필드에는 해당 값을 정확히 복사한다."
            ),
            "key_evidence_row_policy": (
                (
                    "key_evidence_table의 각 required card를 입력 순서대로 정확히 한 행씩 직접 작성한다. "
                    "숨은 card/Strategy 필드는 output_contract 값을 그대로 복사하고, 보이는 관찰·해석·영향은 "
                    "입력 card의 의미와 표시 단위를 보존한다."
                )
                if free_form
                else (
                    "key_evidence_table의 rows는 빈 배열로 반환한다. 시스템이 reader_observation과 "
                    "Strategy 해석으로 최종 행을 결정론적으로 만든다."
                )
            ),
            "claim_grounding_policy": (
                "각 텍스트 문단의 실제 문장을 _claim_units에 그대로 복사하고, 문장마다 실제 사용한 "
                "component 허용 card_keys를 연결한다. 각 item의 모든 claim unit에 연결한 card_keys의 "
                "합집합은 그 item의 card_keys와 정확히 같아야 한다."
            ),
            "comparison_scope_policy": (
                "market_benchmark는 benchmark_name 대비로, selected_peer는 명시된 peer 회사명 대비로만 쓴다. "
                "industry_aggregate 카드가 없으면 업종·동종·산업 평균 비교로 바꾸지 않는다."
            ),
            "limitation_coverage_policy": (
                "data_limits._limitation_claims의 각 필수 category 아래 claim 하나를 작성한다. 해당 "
                "required limitation의 facts와 basis card 내용을 독자가 이해할 수 있는 문장으로 실제 설명한다. "
                "category 이름과 card key는 문장에 노출하지 않으며, 검증 메타데이터는 시스템이 연결한다."
            ),
            "risk_row_policy": (
                (
                    "writer_input.risk_factors의 각 risk를 입력 순서대로 정확히 한 행씩 직접 작성한다. "
                    "숨은 basis/Strategy 필드는 output_contract 값을 그대로 복사한다."
                )
                if free_form
                else (
                    "risk_monitoring_matrix의 rows는 빈 배열로 반환한다. 시스템이 Strategy risk와 "
                    "monitoring point로 최종 행을 결정론적으로 만든다."
                )
            ),
            "thesis_policy": (
                "recommendation_bridge와 thesis component card를 사용해 투자기간, 긍정·부정 균형과 결론을 직접 작성한다."
                if free_form
                else "investment_call_thesis는 빈 배열로 반환하고 시스템이 recommendation_bridge로 조립한다."
            ),
            "product_scope_policy": (
                f"reconciliation이 matched가 아닌 제품 card를 사용할 때 모든 관련 문장과 표 행에 "
                f"'{PRODUCT_DISCLOSURE_SCOPE_LABEL}'이라고 명시한다."
            ),
            "no_opaque_ids": "원천 evidence/claim/opinion ID를 생성하거나 노출하지 않는다.",
            "no_new_information": "수치, 회사, 제품·서비스, 이벤트, 인과관계, 전망치를 새로 만들지 않는다.",
            "no_numeric_derivation": "입력 수치의 단위 환산, 비율 계산, 반올림 재계산을 하지 않는다.",
            "no_internal_narration": "Agent, prompt, validation, 파일 경로, card key를 독자 문장이나 보이는 표 셀에 쓰지 않는다.",
            "no_forbidden_content": "목표주가, 컨센서스, 별도 투자의견 변경 시나리오를 작성하지 않는다.",
            "text_density": "텍스트 item은 1~2개 압축 문단만 사용하고 bullets는 빈 배열로 둔다.",
            "inline_html": ["<strong>"],
        },
        "writer_input": build_writer_llm_input(writer_packet),
    }


def build_writer_llm_input(writer_handoff: dict[str, Any]) -> dict[str, Any]:
    """Remove audit-only provenance and duplicated counter-evidence from LLM input."""

    _validate_writer_input(writer_handoff)
    if _is_v2_writer_packet(writer_handoff):
        compact = json.loads(json.dumps(writer_handoff, ensure_ascii=False))
        compact["target"] = {
            key: value
            for key, value in _dict(compact.get("target")).items()
            if key != "run_key"
        }
        return compact
    compact = {
        key: json.loads(json.dumps(value, ensure_ascii=False))
        for key, value in writer_handoff.items()
        if key not in {"handoff_version", "contrary_evidence", "evidence_refs"}
    }
    compact["target"] = {
        key: value
        for key, value in _dict(compact.get("target")).items()
        if key != "run_key"
    }
    compact["grounding_ref_map"] = {
        str(item.get("id")): str(item.get("strategy_path"))
        for item in writer_handoff.get("evidence_refs") or []
        if isinstance(item, dict) and item.get("id") and item.get("strategy_path")
    }
    compact["required_key_evidence"] = build_required_key_evidence(writer_handoff)
    return compact


def build_required_key_evidence(writer_handoff: dict[str, Any]) -> dict[str, Any]:
    """Expose exact display tokens that the fixed key-evidence table must preserve."""

    revenue = _dict(writer_handoff.get("revenue_breakdown"))
    revenue_unit = str(revenue.get("unit") or "").strip()
    revenue_items = [
        {
            "name": str(item.get("name") or "").strip(),
            "revenue_display": " ".join(
                value
                for value in (str(item.get("revenue_disclosed") or "").strip(), revenue_unit)
                if value
            ),
            "share_display": str(item.get("revenue_share_disclosed") or "").strip(),
        }
        for item in revenue.get("current_items") or []
        if isinstance(item, dict)
    ]
    calculated = _dict(_dict(writer_handoff.get("valuation")).get("calculated_from_close_and_dart"))
    metrics = _dict(calculated.get("metrics"))
    valuation_labels = {
        "trailing_pe": "P/E",
        "price_to_sales": "P/S",
        "price_to_book": "P/B",
    }
    valuation_tokens = [
        f"{label} {float(metric['value']):.2f}"
        for key, label in valuation_labels.items()
        for metric in [_dict(metrics.get(key))]
        if metric.get("value") is not None
    ]
    peer_names = [
        str(item.get("company_name") or "").strip()
        for item in _dict(writer_handoff.get("peer_comparison")).get("metrics") or []
        if isinstance(item, dict) and str(item.get("company_name") or "").strip()
    ]
    return {
        "instruction": "Copy every display token verbatim into key_evidence_table.",
        "revenue_period": _dict(revenue.get("current_period")).get("label"),
        "revenue_items": revenue_items,
        "selected_date_valuation": {
            "as_of_date": calculated.get("as_of_date"),
            "display_tokens": valuation_tokens,
        },
        "peer_company_names": list(dict.fromkeys(peer_names)),
    }


def _section_keys() -> list[str]:
    return [section["key"] for section in REPORT_SECTIONS]


def _raw_payload_shape_errors(payload: dict[str, Any]) -> list[str]:
    sections = payload.get("sections")
    if not isinstance(sections, dict):
        return ["sections must be an object."]
    expected = set(_section_keys())
    actual = set(sections)
    errors: list[str] = []
    missing = sorted(expected - actual)
    unexpected = sorted(actual - expected)
    if missing:
        errors.append(f"Missing top-level section key(s): {missing}")
    if unexpected:
        errors.append(f"Unexpected top-level section key(s): {unexpected}")
    nested_locations: list[str] = []
    for parent_key, parent_value in sections.items():
        if not isinstance(parent_value, dict):
            continue
        nested = sorted(expected.intersection(parent_value))
        if nested:
            nested_locations.append(f"{parent_key} contains nested section key(s): {nested}")
    if nested_locations:
        errors.append("; ".join(nested_locations))
    section_specs = {section["key"]: {item[0] for item in section["items"]} for section in REPORT_SECTIONS}
    for section_key, expected_items in section_specs.items():
        section_payload = sections.get(section_key)
        if not isinstance(section_payload, dict):
            errors.append(f"{section_key} must be an object")
            continue
        actual_items = set(section_payload)
        if actual_items != expected_items:
            errors.append(
                f"{section_key} item keys must be {sorted(expected_items)}, got {sorted(actual_items)}"
            )
    forbidden_keys = sorted(_find_keys(payload).intersection({"target_price", "view_change_conditions", "view_change"}))
    if forbidden_keys:
        errors.append(f"Forbidden output key(s): {forbidden_keys}")
    return errors


def _find_keys(value: Any) -> set[str]:
    keys: set[str] = set()
    if isinstance(value, dict):
        for key, child in value.items():
            keys.add(str(key))
            keys.update(_find_keys(child))
    elif isinstance(value, list):
        for child in value:
            keys.update(_find_keys(child))
    return keys


def _section_role_guidance() -> list[dict[str, Any]]:
    roles = {
        "investment_call_thesis": {
            "reader_question": "현재 투자의견과 투자기간은 무엇이며, 어떤 긍정·부정 근거가 결론을 결정했는가?",
            "content_focus": "decision과 decisive evidence를 사용해 결론부터 제시하고 contrary evidence를 함께 설명한다.",
        },
        "business_market_context": {
            "reader_question": "회사는 무엇으로 매출을 만들고 사업 집중도와 시장 환경은 판단에 어떤 의미인가?",
            "content_focus": "revenue_breakdown, business_context, market_context를 사용하되 집중도를 안정성으로 확대 해석하지 않는다.",
        },
        "key_evidence_table": {
            "reader_question": "재무 추세, 제품 매출 구성, 선택일 밸류에이션, 시장, 1:1 peer의 핵심 수치는 무엇인가?",
            "content_focus": "서로 다른 증거 축을 행으로 분리하고 관찰값·해석·투자의견 영향을 함께 쓴다.",
        },
        "catalysts_execution": {
            "reader_question": "확인된 촉매는 무엇이며 실제 사업·재무 성과로 이어졌는가?",
            "content_focus": "catalysts만 사용하고 확인된 이벤트와 미확인 실행·재무 기여를 분리한다.",
        },
        "risk_monitoring_matrix": {
            "reader_question": "확인된 위험은 무엇이고 어떤 공개 지표나 후속 사실을 관찰해야 하는가?",
            "content_focus": (
                "risks의 각 항목만 리스크 행으로 만들고 대응하는 limitations.monitoring_points를 확인 항목에 연결한다. "
                "risks에 없는 데이터 부재나 촉매 불확실성은 새 리스크 행으로 만들지 않는다."
            ),
        },
        "data_limits": {
            "reader_question": "현재 자료의 시점·기간·비교·인과 한계는 무엇인가?",
            "content_focus": "data_limits와 comparison_limits만 설명하고 판단 변경 시나리오를 작성하지 않는다.",
        },
    }
    return [{"section_key": section["key"], "title": section["title"], **roles[section["key"]]} for section in REPORT_SECTIONS]


def _output_contract() -> dict[str, Any]:
    return {
        "metadata": {"report_title": "문자열"},
        "sections": {
            section["key"]: {
                item_key: _table_contract() if item_type == "table" else _text_contract()
                for item_key, _title, item_type in section["items"]
            }
            for section in REPORT_SECTIONS
        },
    }


def _output_contract_v2(
    writer_packet: dict[str, Any],
    *,
    writer_mode: str = DETERMINISTIC_WRITER_MODE,
) -> dict[str, Any]:
    writer_mode = _normalize_writer_mode(writer_mode)
    free_form = writer_mode == FREE_FORM_WRITER_MODE
    required = _dict(writer_packet.get("required_card_keys_by_component"))
    cards = _dict(writer_packet.get("cards"))
    risks = [
        item for item in writer_packet.get("risk_factors") or [] if isinstance(item, dict)
    ]
    sections: dict[str, Any] = {}
    for section in REPORT_SECTIONS:
        component = section["key"]
        card_keys = _clean_identifiers(required.get(component))
        section_items: dict[str, Any] = {}
        for item_key, _title, item_type in section["items"]:
            if component == "key_evidence_table":
                section_items[item_key] = {
                    "columns": (
                        list(KEY_EVIDENCE_DISPLAY_COLUMNS)
                        if free_form
                        else ["근거 축", "관찰", "해석", "투자의견 영향"]
                    ),
                    "rows": (
                        [
                            {
                                "핵심 근거": "card label을 독자용 한국어로 작성",
                                "확인된 수치·사실": "reader_observation을 단위 변경 없이 작성",
                                "투자 해석": "strategy_interpretation 의미를 보존해 작성",
                                "영향": "investment_effect를 독자용 한국어로 작성",
                                "_card_key": card_key,
                                "_strategy_interpretation": _dict(cards.get(card_key)).get(
                                    "strategy_interpretation"
                                ),
                                "_investment_effect": _dict(cards.get(card_key)).get(
                                    "investment_effect"
                                ),
                            }
                            for card_key in card_keys
                        ]
                        if free_form
                        else []
                    ),
                    "card_keys": card_keys,
                }
            elif component == "risk_monitoring_matrix":
                section_items[item_key] = {
                    "columns": (
                        list(RISK_DISPLAY_COLUMNS)
                        if free_form
                        else ["리스크", "근거", "확인 항목"]
                    ),
                    "rows": (
                        [
                            {
                                "리스크 요인": "risk를 독자용 한국어로 요약",
                                "현재 확인된 내용": "Strategy risk 의미를 보존해 작성",
                                "향후 점검사항": "monitoring_point를 보존해 작성",
                                "_basis_card_keys": _clean_identifiers(
                                    risk.get("basis_card_keys")
                                ),
                                "_strategy_risk_summary": risk.get("risk_summary"),
                            }
                            for risk in risks
                        ]
                        if free_form
                        else []
                    ),
                    "card_keys": card_keys,
                }
            elif item_type == "table":
                section_items[item_key] = {
                    "columns": ["항목", "내용"],
                    "rows": [],
                    "card_keys": card_keys,
                }
            else:
                if component == "investment_call_thesis":
                    section_items[item_key] = {
                        "paragraphs": (
                            ["투자기간과 recommendation bridge의 긍정·부정 균형을 포함한 압축 문단"]
                            if free_form
                            else []
                        ),
                        "bullets": [],
                        "card_keys": card_keys,
                        "_claim_units": (
                            [
                                {
                                    "claim": "paragraphs에 실제로 작성한 완결 문장",
                                    "card_keys": card_keys,
                                    "limitation_categories": [],
                                }
                            ]
                            if free_form
                            else []
                        ),
                    }
                    continue
                limitation_claim_units = [
                    {
                        "claim": (
                            f"{item.get('category')} 한계를 독자가 이해할 수 있게 설명한 완결 문장"
                        ),
                        "card_keys": _clean_identifiers(item.get("basis_card_keys")),
                        "limitation_categories": [str(item.get("category"))],
                    }
                    for item in writer_packet.get("required_limitations") or []
                    if isinstance(item, dict) and item.get("category")
                ]
                if component == "data_limits" and limitation_claim_units:
                    section_items[item_key] = {
                        "_limitation_claims": {
                            str(item.get("category")): {
                                "claim": (
                                    "이 category의 facts와 basis card를 사용해 한계만 설명하는 "
                                    "독자용 한국어 완결 문장"
                                )
                            }
                            for item in writer_packet.get("required_limitations") or []
                            if isinstance(item, dict) and item.get("category")
                        }
                    }
                    continue
                section_items[item_key] = {
                    "paragraphs": ["1~2개의 압축된 한국어 분석 문단"],
                    "bullets": [],
                    "card_keys": card_keys,
                    "_claim_units": (
                        limitation_claim_units
                        if component == "data_limits" and limitation_claim_units
                        else [
                            {
                                "claim": "paragraphs에 실제로 작성한 완결 문장",
                                "card_keys": card_keys,
                                "limitation_categories": [],
                            }
                        ]
                    ),
                }
                if component == "data_limits":
                    section_items[item_key]["_limitation_categories"] = [
                        str(item.get("category"))
                        for item in writer_packet.get("required_limitations") or []
                        if isinstance(item, dict) and item.get("category")
                    ]
        sections[component] = section_items
    return {"metadata": {"report_title": "문자열"}, "sections": sections}


def writer_report_response_format(
    writer_handoff: dict[str, Any],
    *,
    writer_mode: str = DETERMINISTIC_WRITER_MODE,
) -> dict[str, Any]:
    """Return the API response format for the active Writer contract."""

    _validate_writer_input(writer_handoff)
    writer_mode = _normalize_writer_mode(writer_mode)
    if not _is_v2_writer_packet(writer_handoff):
        return {"type": "json_object"}
    return {
        "type": "json_schema",
        "json_schema": {
            "name": "writer_report_payload_v2",
            "strict": True,
            "schema": _writer_report_schema_v2(
                writer_handoff, writer_mode=writer_mode
            ),
        },
    }


def _writer_report_schema_v2(
    writer_packet: dict[str, Any],
    *,
    writer_mode: str = DETERMINISTIC_WRITER_MODE,
) -> dict[str, Any]:
    writer_mode = _normalize_writer_mode(writer_mode)
    free_form = writer_mode == FREE_FORM_WRITER_MODE
    required_by_component = _dict(writer_packet.get("required_card_keys_by_component"))
    risks = [
        item for item in writer_packet.get("risk_factors") or [] if isinstance(item, dict)
    ]
    all_card_keys = _clean_identifiers(list(_dict(writer_packet.get("cards")).keys()))
    limitation_categories = _clean_identifiers(
        [
            item.get("category")
            for item in writer_packet.get("required_limitations") or []
            if isinstance(item, dict) and item.get("category")
        ]
    )
    section_properties: dict[str, Any] = {}
    for section in REPORT_SECTIONS:
        component = section["key"]
        allowed_card_keys = _clean_identifiers(required_by_component.get(component))
        item_properties: dict[str, Any] = {}
        for item_key, _title, item_type in section["items"]:
            if item_type == "table":
                expected_columns = (
                    (
                        list(KEY_EVIDENCE_DISPLAY_COLUMNS)
                        if free_form
                        else ["근거 축", "관찰", "해석", "투자의견 영향"]
                    )
                    if component == "key_evidence_table"
                    else (
                        list(RISK_DISPLAY_COLUMNS)
                        if free_form
                        else ["리스크", "근거", "확인 항목"]
                    )
                    if component == "risk_monitoring_matrix"
                    else ["항목", "내용"]
                )
                row_schema = _strict_schema_object({})
                row_count = 0
                if free_form and component == "key_evidence_table":
                    row_schema = _strict_schema_object(
                        {
                            "핵심 근거": {"type": "string"},
                            "확인된 수치·사실": {"type": "string"},
                            "투자 해석": {"type": "string"},
                            "영향": {"type": "string"},
                            "_card_key": {
                                "type": "string",
                                "enum": allowed_card_keys,
                            },
                            "_strategy_interpretation": {"type": "string"},
                            "_investment_effect": {"type": "string"},
                        }
                    )
                    row_count = len(allowed_card_keys)
                elif free_form and component == "risk_monitoring_matrix":
                    row_schema = _strict_schema_object(
                        {
                            "리스크 요인": {"type": "string"},
                            "현재 확인된 내용": {"type": "string"},
                            "향후 점검사항": {"type": "string"},
                            "_basis_card_keys": _bounded_string_array_schema(
                                all_card_keys,
                                min_items=1,
                                max_items=len(all_card_keys),
                            ),
                            "_strategy_risk_summary": {"type": "string"},
                        }
                    )
                    row_count = len(risks)
                item_properties[item_key] = _strict_schema_object(
                    {
                        "columns": _bounded_string_array_schema(
                            expected_columns,
                            exact_count=len(expected_columns),
                        ),
                        "rows": {
                            "type": "array",
                            "items": row_schema,
                            "minItems": row_count,
                            "maxItems": row_count,
                        },
                        "card_keys": _bounded_string_array_schema(
                            allowed_card_keys,
                            exact_count=len(allowed_card_keys),
                        ),
                    }
                )
                continue

            if component == "data_limits" and limitation_categories:
                item_properties[item_key] = _strict_schema_object(
                    {
                        "_limitation_claims": _strict_schema_object(
                            {
                                category: _strict_schema_object(
                                    {"claim": {"type": "string"}}
                                )
                                for category in limitation_categories
                            }
                        )
                    }
                )
                continue

            locked_thesis = (
                component == "investment_call_thesis" and not free_form
            )
            claim_unit = _strict_schema_object(
                {
                    "claim": {"type": "string"},
                    "card_keys": _bounded_string_array_schema(
                        allowed_card_keys,
                        min_items=0,
                        max_items=len(allowed_card_keys),
                    ),
                    "limitation_categories": _bounded_string_array_schema(
                        limitation_categories if component == "data_limits" else [],
                        min_items=0,
                        max_items=(
                            len(limitation_categories)
                            if component == "data_limits"
                            else 0
                        ),
                    ),
                }
            )
            text_properties: dict[str, Any] = {
                "paragraphs": {
                    "type": "array",
                    "items": {"type": "string"},
                    "minItems": 0 if locked_thesis else 1,
                    "maxItems": 0 if locked_thesis else 2,
                },
                "bullets": {
                    "type": "array",
                    "items": {"type": "string"},
                    "minItems": 0,
                    "maxItems": 0,
                },
                "card_keys": _bounded_string_array_schema(
                    allowed_card_keys,
                    exact_count=len(allowed_card_keys),
                ),
                "_claim_units": {
                    "type": "array",
                    "items": claim_unit,
                    "minItems": 0 if locked_thesis else 1,
                    "maxItems": 0 if locked_thesis else 8,
                },
            }
            if component == "data_limits":
                text_properties["_limitation_categories"] = (
                    _bounded_string_array_schema(
                        limitation_categories,
                        exact_count=len(limitation_categories),
                    )
                )
            item_properties[item_key] = _strict_schema_object(text_properties)
        section_properties[component] = _strict_schema_object(item_properties)
    return _strict_schema_object(
        {
            "metadata": _strict_schema_object(
                {"report_title": {"type": "string"}}
            ),
            "sections": _strict_schema_object(section_properties),
        }
    )


def _strict_schema_object(properties: dict[str, Any]) -> dict[str, Any]:
    return {
        "type": "object",
        "properties": properties,
        "required": list(properties),
        "additionalProperties": False,
    }


def _bounded_string_array_schema(
    allowed_values: list[str],
    *,
    exact_count: int | None = None,
    min_items: int | None = None,
    max_items: int | None = None,
) -> dict[str, Any]:
    item_schema: dict[str, Any] = {"type": "string"}
    if allowed_values:
        item_schema["enum"] = allowed_values
    schema: dict[str, Any] = {"type": "array", "items": item_schema}
    if exact_count is not None:
        schema["minItems"] = exact_count
        schema["maxItems"] = exact_count
    else:
        if min_items is not None:
            schema["minItems"] = min_items
        if max_items is not None:
            schema["maxItems"] = max_items
    return schema


def _text_contract() -> dict[str, Any]:
    return {
        "paragraphs": ["1~2개의 압축된 한국어 분석 문단"],
        "bullets": [],
        "grounding_refs": ["writer_input.grounding_ref_map의 유효한 id"],
    }


def _table_contract() -> dict[str, Any]:
    return {
        "columns": ["표 컬럼명"],
        "rows": [{"표 컬럼명": "근거가 있는 셀 값"}],
        "grounding_refs": ["writer_input.grounding_ref_map의 유효한 id"],
    }


def _system_prompt(
    contract_version: str = "",
    *,
    writer_mode: str = DETERMINISTIC_WRITER_MODE,
) -> str:
    writer_mode = _normalize_writer_mode(writer_mode)
    if contract_version == EDITORIAL_PACKET_VERSION:
        return _system_prompt_v2(writer_mode=writer_mode)
    return """
너는 범용 상장기업 리서치 Writer Agent다. 반드시 유효한 JSON object 하나만 반환한다.

강제 조건:
- user message의 writer_input만 사용한다. 새로운 수치, 회사, 제품·서비스, 이벤트, 인과관계, 전망을 만들지 않는다.
- 숫자는 writer_input에 표시된 값과 단위를 그대로 사용한다. 곱셈·나눗셈·단위 환산으로 새 숫자를 만들지 않는다.
- 이름이 _100m 또는 _100m_krw로 끝나는 값은 이미 억원 단위다. 원 단위 정수로 재계산하지 말고 억원으로 표시한다.
- Strategy의 투자의견과 투자기간을 바꾸지 않는다.
- sections 바로 아래에 정확히 6개 section key를 sibling으로 둔다. 중첩하거나 다른 section을 추가하지 않는다.
- 각 section item에 사용한 writer_input.grounding_ref_map의 유효한 id를 grounding_refs로 넣는다.
- OP/claim/evidence ID, Agent, prompt, validation workflow, 절대 파일 경로는 본문이나 표 셀에 쓰지 않는다.
- 목표주가, 컨센서스, 별도 투자의견 변경 시나리오를 생성하지 않는다.
- 제품·서비스별 매출액과 비중, 선택일 밸류에이션, 명시된 1:1 비교 기업을 key evidence table에 반영한다.
- writer_input.required_key_evidence의 revenue_items, selected_date_valuation.display_tokens, peer_company_names를 모두 key evidence table에 문자열 그대로 한 번씩 포함한다.
- 선택일 계산 밸류에이션과 날짜가 다른 provider-direct 값은 구분한다.
- 같은 근거를 여러 섹션에 반복하지 않는다.
- 텍스트 섹션은 1~2개 문단으로 작성하고 bullets는 빈 배열로 둔다. 상세 원 단위 수치와 전체 비교값은 key_evidence_table에만 배치한다.
- inline HTML은 <strong>만 허용한다. Markdown과 raw HTML 문서는 반환하지 않는다.

섹션 목적:
- investment_call_thesis: 결론, 결정적 긍정·부정 근거, 반대 근거를 압축한다.
- business_market_context: 매출 구조와 시장 맥락을 설명한다.
- key_evidence_table: 재무 추세, 제품 매출, 시장, 밸류에이션, 1:1 peer를 표로 비교한다.
- catalysts_execution: 확인된 이벤트와 실행·재무 기여의 확인 범위를 구분한다.
- risk_monitoring_matrix: 근거가 있는 위험과 관찰 가능한 확인 항목을 표로 정리한다.
- data_limits: 자료 시점, 기간, 비교, 인과 한계만 설명한다. 판단 변경 시나리오는 쓰지 않는다.
""".strip()


def _system_prompt_v2(
    *,
    writer_mode: str = DETERMINISTIC_WRITER_MODE,
) -> str:
    writer_mode = _normalize_writer_mode(writer_mode)
    assembly_policy = (
        """
- investment_call_thesis의 paragraphs와 _claim_units를 직접 작성한다.
- key_evidence_table은 output_contract의 각 card에 대응하는 행을 순서대로 직접 작성한다.
- risk_monitoring_matrix는 output_contract의 각 risk에 대응하는 행을 순서대로 직접 작성한다.
- 밑줄로 시작하는 Strategy/card/risk 필드는 output_contract 값을 정확히 복사하며 보이는 셀에는 노출하지 않는다.
""".strip()
        if writer_mode == FREE_FORM_WRITER_MODE
        else """
- investment_call_thesis의 paragraphs와 _claim_units는 빈 배열로 반환한다. 시스템이 검증된 recommendation_bridge를 그대로 배치한다.
- key_evidence_table의 rows는 빈 배열로 반환한다. 최종 표는 시스템이 구조화된 관찰값으로 만든다.
- risk_monitoring_matrix의 rows는 빈 배열로 반환한다. 최종 리스크 행도 시스템이 Strategy 의미와 확인 항목으로 만든다.
""".strip()
    )
    return f"""
너는 범용 상장기업 리서치 Writer Agent다. 반드시 유효한 JSON object 하나만 반환한다.

역할 경계:
- Strategy가 이미 판단한 해석과 투자의견 영향을 독자가 읽기 좋은 한국어로 편집한다.
- Strategy 판단을 재평가하거나 새로운 해석, 인과관계, 전망, 수치, 회사, 제품·서비스, 이벤트를 만들지 않는다.
- 투자의견과 투자기간을 변경하지 않는다.

출력 계약:
- output_contract의 sections와 item key를 정확히 유지하고 다른 key를 추가하지 않는다.
- 각 item의 card_keys는 output_contract에 지정된 배열과 순서까지 그대로 복사한다.
{assembly_policy}
- 밑줄로 시작하는 필드는 검증 전용이다. 그 값을 보이는 문장이나 표 셀에 노출하지 않는다.
- 각 텍스트 item의 실제 완결 문장을 _claim_units.claim에 그대로 복사하고 문장별 사용 card_keys를 연결한다. 각 item의 _claim_units.card_keys 합집합은 item.card_keys와 정확히 같아야 한다.
- data_limits의 _limitation_claims에는 스키마가 요구하는 category key를 정확히 유지하고, 각 claim은 해당 required limitation의 facts와 basis card를 사용해 독자가 이해할 수 있는 문장으로 실제 설명한다.
- _limitation_claims의 category 이름이나 card key를 claim 문장에 노출하지 않는다. 검증용 category와 card key는 시스템이 원본 packet에서 연결한다.

작성 규칙:
- 보이는 key evidence 열은 각각 근거 축, 관찰, 해석, 투자의견 영향의 역할을 지킨다.
- 카드에 reader_observation이 있으면 관찰 열은 그 표시값을 우선 사용하고 raw 숫자를 다시 환산하지 않는다.
- 제품 card의 reconciliation이 matched가 아니면 관련 thesis, business, key evidence와 risk 문장에 `주요 제품·서비스 공시표 기준`이라고 명시하고 회사 전체 매출 구성으로 확대하지 않는다.
- 관찰과 해석을 섞지 않고 strategy_interpretation의 의미와 investment_effect의 방향을 유지한다.
- market_benchmark card는 명시된 benchmark_name 대비로 쓰고 selected_peer card는 명시된 회사명 대비로 쓴다.
- industry_aggregate card가 없으면 업종·동종·산업 평균 비교 표현을 사용하지 않는다.
- observation_basis=point_in_time인 card만으로 개선·악화·증가·감소 같은 시계열 변화를 주장하지 않는다.
- 데이터 한계나 미공개 정보를 새 리스크로 승격하지 않는다.
- 입력 숫자는 표시된 값과 단위를 그대로 사용하고 계산, 단위 환산, 임의 반올림을 하지 않는다.
- 원천 evidence/claim/opinion ID, card key, Agent, prompt, validation, 절대 파일 경로를 보이는 문장에 쓰지 않는다.
- 목표주가, 컨센서스, 별도 투자의견 변경 시나리오를 작성하지 않는다.
- 텍스트 item은 1~2개 문단으로 작성하고 bullets는 빈 배열로 둔다.
- inline HTML은 <strong>만 허용하며 Markdown이나 raw HTML 문서는 반환하지 않는다.
""".strip()


def _is_v2_writer_packet(value: Any) -> bool:
    return isinstance(value, dict) and value.get("packet_version") == EDITORIAL_PACKET_VERSION


def _writer_contract_version(value: dict[str, Any]) -> str:
    if _is_v2_writer_packet(value):
        return EDITORIAL_PACKET_VERSION
    return str(value.get("handoff_version") or "")


def _normalize_writer_mode(value: str) -> str:
    mode = str(value or DETERMINISTIC_WRITER_MODE).strip().lower()
    if mode not in WRITER_MODES:
        raise ValueError(
            f"writer_mode must be one of: {', '.join(sorted(WRITER_MODES))}"
        )
    return mode


def _validate_writer_input(value: dict[str, Any]) -> None:
    if _is_v2_writer_packet(value):
        validate_writer_editorial_packet(value)
        return
    validate_writer_handoff(value)


def _enrich_writer_metadata_v2(payload: dict[str, Any], writer_packet: dict[str, Any]) -> dict[str, Any]:
    """Attach hidden card links only when visible locked Strategy meaning is unambiguous."""

    enriched = json.loads(json.dumps(payload, ensure_ascii=False))
    cards = _dict(writer_packet.get("cards"))
    sections = _dict(enriched.get("sections"))
    evidence_item = _dict(_dict(sections.get("key_evidence_table")).get("evidence_table"))
    interpretation_map: dict[str, list[str]] = {}
    for card_key in _clean_identifiers(
        _dict(writer_packet.get("required_card_keys_by_component")).get("key_evidence_table")
    ):
        interpretation = str(_dict(cards.get(card_key)).get("strategy_interpretation") or "").strip()
        interpretation_map.setdefault(interpretation, []).append(card_key)
    for row in evidence_item.get("rows") or []:
        if not isinstance(row, dict) or row.get("_card_key"):
            continue
        candidates = interpretation_map.get(str(row.get("해석") or "").strip(), [])
        if len(candidates) != 1:
            continue
        card_key = candidates[0]
        card = _dict(cards.get(card_key))
        row["_card_key"] = card_key
        row["_strategy_interpretation"] = card.get("strategy_interpretation")
        row["_investment_effect"] = card.get("investment_effect")
    if not evidence_item.get("card_keys"):
        inferred_keys = [
            str(row.get("_card_key") or "")
            for row in evidence_item.get("rows") or []
            if isinstance(row, dict) and row.get("_card_key")
        ]
        if len(inferred_keys) == len(evidence_item.get("rows") or []):
            evidence_item["card_keys"] = inferred_keys

    risk_item = _dict(_dict(sections.get("risk_monitoring_matrix")).get("risk_monitoring_table"))
    risk_map: dict[str, list[dict[str, Any]]] = {}
    for risk in writer_packet.get("risk_factors") or []:
        if isinstance(risk, dict):
            risk_map.setdefault(_visible_risk_summary(risk).strip(), []).append(risk)
    for row in risk_item.get("rows") or []:
        if not isinstance(row, dict) or row.get("_strategy_risk_summary"):
            continue
        candidates = risk_map.get(str(row.get("리스크") or "").strip(), [])
        if len(candidates) != 1:
            continue
        risk = candidates[0]
        row["_basis_card_keys"] = _clean_identifiers(risk.get("basis_card_keys"))
        row["_strategy_risk_summary"] = risk.get("risk_summary")
    if not risk_item.get("card_keys"):
        risk_item["card_keys"] = list(
            dict.fromkeys(
                card_key
                for row in risk_item.get("rows") or []
                if isinstance(row, dict)
                for card_key in _clean_identifiers(row.get("_basis_card_keys"))
            )
        )
    return enriched


def _apply_locked_thesis_v2(
    payload: dict[str, Any],
    writer_packet: dict[str, Any],
) -> dict[str, Any]:
    """Use the validated Strategy bridge as the report's decision thesis."""

    normalized = json.loads(json.dumps(payload, ensure_ascii=False))
    sections = normalized.setdefault("sections", {})
    thesis_section = sections.setdefault("investment_call_thesis", {})
    thesis = thesis_section.setdefault("section_analysis", {})
    decision = _dict(writer_packet.get("decision"))
    bridge = _dict(writer_packet.get("recommendation_bridge"))
    intro = (
        f"<strong>{decision.get('opinion')}</strong>, "
        f"{decision.get('investment_horizon')} 관점입니다."
    )
    forward = str(bridge.get("forward_support") or "").strip()
    current = str(bridge.get("current_price_rationale") or "").strip()
    valuation = str(bridge.get("valuation_counterweight") or "").strip()
    forward = _qualify_partial_product_scope_v2(
        forward,
        writer_packet,
        bridge.get("forward_support_card_keys"),
    )
    current = _qualify_partial_product_scope_v2(
        current,
        writer_packet,
        bridge.get("current_price_card_keys"),
    )
    valuation = _qualify_partial_product_scope_v2(
        valuation,
        writer_packet,
        bridge.get("valuation_card_keys"),
    )
    bridge_keys = _clean_identifiers(
        [
            *(_clean_identifiers(bridge.get("forward_support_card_keys"))),
            *(_clean_identifiers(bridge.get("current_price_card_keys"))),
            *(_clean_identifiers(bridge.get("valuation_card_keys"))),
        ]
    )
    required_keys = _clean_identifiers(
        _dict(writer_packet.get("required_card_keys_by_component")).get(
            "investment_call_thesis"
        )
    )
    cards = _dict(writer_packet.get("cards"))
    orphan_keys = [card_key for card_key in required_keys if card_key not in bridge_keys]
    orphan_claims = [
        _qualify_partial_product_scope_v2(
            str(_dict(cards.get(card_key)).get("strategy_interpretation") or "").strip(),
            writer_packet,
            [card_key],
        )
        for card_key in orphan_keys
        if str(_dict(cards.get(card_key)).get("strategy_interpretation") or "").strip()
    ]
    thesis["paragraphs"] = [
        " ".join(value for value in (intro, forward) if value),
        " ".join(value for value in (current, valuation, *orphan_claims) if value),
    ]
    thesis["bullets"] = []
    thesis["card_keys"] = required_keys
    thesis["_claim_units"] = [
        {"claim": intro, "card_keys": [], "limitation_categories": []},
        {
            "claim": forward,
            "card_keys": _clean_identifiers(bridge.get("forward_support_card_keys")),
            "limitation_categories": [],
        },
        {
            "claim": current,
            "card_keys": _clean_identifiers(bridge.get("current_price_card_keys")),
            "limitation_categories": [],
        },
        {
            "claim": valuation,
            "card_keys": _clean_identifiers(bridge.get("valuation_card_keys")),
            "limitation_categories": [],
        },
        *[
            {
                "claim": str(_dict(cards.get(card_key)).get("strategy_interpretation") or "").strip(),
                "card_keys": [card_key],
                "limitation_categories": [],
            }
            for card_key in orphan_keys
            if str(_dict(cards.get(card_key)).get("strategy_interpretation") or "").strip()
        ],
    ]
    return normalized


def _qualify_partial_product_scope_v2(
    text: str,
    writer_packet: dict[str, Any],
    card_keys: Any,
) -> str:
    """Scope partial product disclosure from structured claim-card linkage."""

    if not text or PRODUCT_DISCLOSURE_SCOPE_LABEL in text:
        return text
    if "financial.product_breakdown" not in _clean_identifiers(card_keys):
        return text
    product_card = _dict(_dict(writer_packet.get("cards")).get("financial.product_breakdown"))
    reconciliation = _dict(
        _dict(product_card.get("primary_observation")).get("reconciliation")
    )
    if not product_card or reconciliation.get("reconciliation_status") == "matched":
        return text
    return f"{PRODUCT_DISCLOSURE_SCOPE_LABEL}으로 보면, {text}"


def _apply_deterministic_evidence_table_v2(
    payload: dict[str, Any],
    writer_packet: dict[str, Any],
) -> dict[str, Any]:
    """Replace model-authored evidence cells with structured card displays."""

    normalized = json.loads(json.dumps(payload, ensure_ascii=False))
    sections = normalized.setdefault("sections", {})
    evidence_section = sections.setdefault("key_evidence_table", {})
    evidence_item = evidence_section.setdefault("evidence_table", {})
    required = _clean_identifiers(
        _dict(writer_packet.get("required_card_keys_by_component")).get("key_evidence_table")
    )
    cards = _dict(writer_packet.get("cards"))
    evidence_item["columns"] = list(KEY_EVIDENCE_DISPLAY_COLUMNS)
    evidence_item["rows"] = [
        {
            "핵심 근거": _evidence_display_label(card_key, card),
            "확인된 수치·사실": _evidence_observation_text(card_key, card),
            "투자 해석": _plain_korean_text(
                _qualify_partial_product_scope_v2(
                    str(card.get("strategy_interpretation") or ""),
                    writer_packet,
                    [card_key],
                )
            ),
            "영향": _effect_label(card.get("investment_effect")),
            "_card_key": card_key,
            "_strategy_interpretation": card.get("strategy_interpretation"),
            "_investment_effect": card.get("investment_effect"),
        }
        for card_key in required
        for card in [_dict(cards.get(card_key))]
    ]
    evidence_item["card_keys"] = required
    return normalized


def _apply_deterministic_risk_table_v2(
    payload: dict[str, Any],
    writer_packet: dict[str, Any],
) -> dict[str, Any]:
    """Render Strategy-owned risk meaning and monitoring points without model drift."""

    normalized = json.loads(json.dumps(payload, ensure_ascii=False))
    sections = normalized.setdefault("sections", {})
    risk_section = sections.setdefault("risk_monitoring_matrix", {})
    risk_item = risk_section.setdefault("risk_monitoring_table", {})
    risks = [item for item in writer_packet.get("risk_factors") or [] if isinstance(item, dict)]
    risk_item["columns"] = list(RISK_DISPLAY_COLUMNS)
    risk_item["rows"] = [
        {
            "리스크 요인": _risk_display_title(risk),
            "현재 확인된 내용": _plain_korean_text(_visible_risk_summary(risk)),
            "향후 점검사항": risk.get("monitoring_point"),
            "_basis_card_keys": _clean_identifiers(risk.get("basis_card_keys")),
            "_strategy_risk_summary": risk.get("risk_summary"),
        }
        for risk in risks
    ]
    risk_item["card_keys"] = list(
        dict.fromkeys(
            card_key
            for risk in risks
            for card_key in _clean_identifiers(risk.get("basis_card_keys"))
        )
    )
    return normalized


def _materialize_data_limit_claims_v2(
    payload: dict[str, Any],
    writer_packet: dict[str, Any],
) -> dict[str, Any]:
    """Attach trusted limitation metadata to category-keyed Writer prose."""

    normalized = json.loads(json.dumps(payload, ensure_ascii=False))
    limitations = [
        item
        for item in writer_packet.get("required_limitations") or []
        if isinstance(item, dict) and str(item.get("category") or "").strip()
    ]
    if not limitations:
        return normalized
    sections = _dict(normalized.get("sections"))
    data_limits = _dict(sections.get("data_limits"))
    item = _dict(data_limits.get("section_analysis"))
    raw_claims = _dict(item.get("_limitation_claims"))
    if not raw_claims:
        return normalized

    assignments = _limitation_card_assignments_v2(writer_packet, limitations)
    claim_units: list[dict[str, Any]] = []
    for limitation in limitations:
        category = str(limitation.get("category") or "").strip()
        claim = str(_dict(raw_claims.get(category)).get("claim") or "").strip()
        if not claim:
            continue
        claim_units.append(
            {
                "claim": claim,
                "card_keys": assignments.get(category, []),
                "limitation_categories": [category],
            }
        )
    claims = [str(unit["claim"]) for unit in claim_units]
    item.clear()
    item.update(
        {
            "paragraphs": (
                [claims[0], " ".join(claims[1:])]
                if len(claims) > 1
                else claims
            ),
            "bullets": [],
            "card_keys": _clean_identifiers(
                _dict(writer_packet.get("required_card_keys_by_component")).get(
                    "data_limits"
                )
            ),
            "_claim_units": claim_units,
            "_limitation_categories": [
                str(item.get("category")) for item in limitations
            ],
        }
    )
    return normalized


def _limitation_card_assignments_v2(
    writer_packet: dict[str, Any],
    limitations: list[dict[str, Any]],
) -> dict[str, list[str]]:
    """Assign every routed data-limit card to the closest typed limitation."""

    cards = _dict(writer_packet.get("cards"))
    routed = _clean_identifiers(
        _dict(writer_packet.get("required_card_keys_by_component")).get("data_limits")
    )
    routed_set = set(routed)
    assignments = {
        str(item.get("category")): [
            card_key
            for card_key in _clean_identifiers(item.get("basis_card_keys"))
            if card_key in routed_set
        ]
        for item in limitations
    }
    assigned = {
        card_key for card_keys in assignments.values() for card_key in card_keys
    }
    for card_key in routed:
        if card_key in assigned:
            continue
        domain = str(_dict(cards.get(card_key)).get("domain") or "")
        candidates = []
        for limitation in limitations:
            category = str(limitation.get("category"))
            basis_domains = {
                str(_dict(cards.get(basis_key)).get("domain") or "")
                for basis_key in _clean_identifiers(limitation.get("basis_card_keys"))
            }
            if domain and domain in basis_domains:
                candidates.append(category)
        target_category = candidates[-1] if candidates else str(limitations[-1].get("category"))
        assignments[target_category].append(card_key)
    return assignments


def _evidence_display_label(card_key: str, card: dict[str, Any]) -> str:
    aliases = {
        "financial.same_period_trend": "실적 성장",
        "financial.annual_trend": "중장기 실적 추세",
        "financial.cash_flow": "현금창출력",
        "financial.balance_sheet": "재무안정성",
        "financial.product_breakdown": "매출 구성",
        "financial.filing_basis": "공시 기준",
        "market.absolute_trend": "주가 흐름",
        "market.momentum_volume": "수급·모멘텀",
        "valuation.selected_date": "기준일 밸류에이션",
        "valuation.provider_reference": "밸류에이션 참고값",
        "peer.revenue_growth": "비교기업 대비 성장성",
        "peer.profitability": "비교기업 대비 수익성",
        "peer.financial_position": "비교기업 대비 재무구조",
        "peer.market_performance": "비교기업 대비 주가 성과",
        "peer.valuation": "비교기업 대비 밸류에이션",
    }
    if card_key == "market.relative_performance":
        benchmark = str(
            _dict(card.get("comparison_entities")).get("benchmark_name")
            or _dict(card.get("primary_observation")).get("benchmark_name")
            or "시장"
        ).strip()
        return f"{benchmark} 상대성과"
    if str(card.get("domain") or "") == "news":
        return "주요 이벤트"
    return aliases.get(card_key, str(card.get("label") or "핵심 근거"))


def _evidence_observation_text(card_key: str, card: dict[str, Any]) -> str:
    observation = card.get("reader_observation") or card.get("primary_observation")
    if card_key == "market.relative_performance":
        return _market_relative_observation_text(card, observation)
    if card_key.startswith("peer."):
        return _peer_observation_text(observation)
    if card_key == "financial.balance_sheet":
        return _balance_sheet_observation_text(observation)
    if card_key == "financial.product_breakdown":
        return _product_observation_text(observation)
    if card_key == "valuation.selected_date":
        return _valuation_observation_text(observation)
    return _structured_observation_text(observation)


def _market_relative_observation_text(card: dict[str, Any], value: Any) -> str:
    observation = _dict(value)
    metrics = _dict(observation.get("지표"))
    benchmark = str(
        _dict(card.get("comparison_entities")).get("benchmark_name")
        or _dict(card.get("primary_observation")).get("benchmark_name")
        or "시장"
    ).strip()
    period = str(
        _dict(_dict(card.get("primary_observation")).get("periods")).get(
            "stock_period_excess_return"
        )
        or ""
    ).replace("..", "~")
    lines = [f"기준일: {observation.get('기준일')}" if observation.get("기준일") else ""]
    for label, metric_value in metrics.items():
        display_label = str(label).replace("5일", "5거래일").replace("20일", "20거래일").replace("60일", "60거래일")
        if display_label.startswith("조회기간"):
            display_label = f"요청기간({period}) {benchmark} 대비 초과수익률" if period else display_label.replace("조회기간", "요청기간")
        display_value = str(metric_value)
        if "초과수익률" in display_label and display_value.endswith("%"):
            display_value = f"{display_value[:-1]}%p"
        lines.append(f"{display_label}: {display_value}")
    if any("60거래일" in line for line in lines):
        lines.append("주: 60거래일 지표는 요청기간 이전 warm-up 데이터 포함")
    return "\n".join(line for line in lines if line) or MISSING_VALUE


def _peer_observation_text(value: Any) -> str:
    observation = _dict(value)
    target = str(observation.get("대상 기업") or "대상 기업").strip()
    metrics = [item for item in observation.get("지표") or [] if isinstance(item, dict)]
    bases = list(
        dict.fromkeys(
            _plain_korean_text(str(item.get("비교 기준") or "").strip())
            for item in metrics
            if str(item.get("비교 기준") or "").strip()
        )
    )
    lines = [f"비교 기준: {', '.join(bases)}"] if bases else []
    for item in metrics:
        metric_name = str(item.get("지표명") or "지표").strip()
        peer_name = str(item.get("비교 기업명") or "비교기업").strip()
        lines.append(
            f"{metric_name}: {target} {item.get('대상')} · {peer_name} {item.get('비교 기업')}"
        )
    return "\n".join(lines) or _structured_observation_text(value)


def _balance_sheet_observation_text(value: Any) -> str:
    observation = _dict(value)
    lines = []
    if observation.get("기준일"):
        lines.append(f"기준일: {observation['기준일']}")
    scale = [
        f"{key}: {observation[key]}"
        for key in ("총자산", "총부채", "총자본")
        if observation.get(key)
    ]
    ratios = [
        f"{key}: {observation[key]}"
        for key in ("유동비율", "현금비율", "부채비율")
        if observation.get(key)
    ]
    if scale:
        lines.append(" · ".join(scale))
    if ratios:
        lines.append(" · ".join(ratios))
    return "\n".join(lines) or _structured_observation_text(value)


def _product_observation_text(value: Any) -> str:
    observation = _dict(value)
    period = str(observation.get("공시 기준") or "").strip()
    unit = str(observation.get("공시 단위") or "").strip()
    header = " · ".join(
        text
        for text in (
            f"공시 기준: {period}" if period else "",
            f"단위: {unit}" if unit else "",
        )
        if text
    )
    lines = [header] if header else []
    for item in observation.get("제품") or []:
        if not isinstance(item, dict):
            continue
        name = str(item.get("제품명") or "제품").strip()
        revenue = str(item.get("매출액") or MISSING_VALUE).strip()
        share = str(item.get("비중") or "").strip()
        lines.append(f"{name}: {revenue}{f' ({share})' if share else ''}")
    scope = str(observation.get("범위") or "").strip()
    if scope:
        lines.append(f"범위: {scope}")
    return "\n".join(lines) or _structured_observation_text(value)


def _valuation_observation_text(value: Any) -> str:
    observation = _dict(value)
    lines = []
    if observation.get("기준일"):
        lines.append(f"기준일: {observation['기준일']}")
    metrics = _dict(observation.get("지표"))
    if metrics:
        lines.append(" · ".join(f"{key}: {metric}" for key, metric in metrics.items()))
    return "\n".join(lines) or _structured_observation_text(value)


def _structured_observation_text(value: Any, *, depth: int = 0) -> str:
    if isinstance(value, dict):
        parts = [
            f"{key}: {_structured_observation_text(child, depth=depth + 1)}"
            for key, child in value.items()
            if child not in (None, "", [], {})
        ]
        separator = "\n" if depth == 0 else " · "
        return separator.join(parts) if parts else MISSING_VALUE
    if isinstance(value, list):
        parts = [
            _structured_observation_text(child, depth=depth + 1)
            for child in value
            if child not in (None, "", [], {})
        ]
        separator = "\n" if depth == 0 else "; "
        return separator.join(parts) if parts else MISSING_VALUE
    if isinstance(value, bool):
        return "예" if value else "아니오"
    text = str(value or "").strip()
    return text or MISSING_VALUE


def _replace_visible_card_keys(payload: dict[str, Any], writer_packet: dict[str, Any]) -> dict[str, Any]:
    labels = {
        card_key: str(card.get("label") or card_key)
        for card_key, card in _dict(writer_packet.get("cards")).items()
        if isinstance(card, dict)
    }

    def replace(value: Any, *, hidden: bool = False) -> Any:
        if isinstance(value, dict):
            return {
                key: replace(
                    child,
                    hidden=(
                        hidden
                        or (
                            str(key).startswith("_")
                            and key != "_claim_units"
                        )
                        or key
                        in {
                            "card_keys",
                            "grounding_refs",
                            "limitation_categories",
                        }
                    ),
                )
                for key, child in value.items()
            }
        if isinstance(value, list):
            return [replace(child, hidden=hidden) for child in value]
        if not hidden and isinstance(value, str):
            for card_key in sorted(labels, key=len, reverse=True):
                value = value.replace(card_key, labels[card_key])
        return value

    return replace(payload)


def _effect_label(value: Any) -> str:
    return {
        "positive": "긍정 요인",
        "negative": "부담 요인",
        "mixed": "혼합",
        "neutral": "중립",
        "reference": "참고",
    }.get(str(value or ""), "참고")


def _risk_display_title(risk: dict[str, Any]) -> str:
    basis = set(_clean_identifiers(risk.get("basis_card_keys")))
    if "financial.product_breakdown" in basis:
        return "제품 집중도"
    if any(key.startswith("market.") for key in basis):
        return "시장 상대성과"
    if any(key.startswith("valuation.") or key == "peer.valuation" for key in basis):
        return "밸류에이션 부담"
    category = str(risk.get("category") or "").strip()
    return {
        "business": "사업 구조",
        "financial": "재무 부담",
        "regulatory": "규제·경쟁",
        "market": "시장 변동성",
        "valuation": "밸류에이션 부담",
        "execution": "실행·수익화",
    }.get(category, "주요 리스크")


def _visible_risk_summary(risk: dict[str, Any]) -> str:
    summary = str(risk.get("reader_summary") or risk.get("risk_summary") or "").strip()
    qualifier = str(risk.get("scope_qualifier") or "").strip()
    if qualifier and qualifier != "not_applicable" and qualifier not in summary:
        return f"{qualifier}: {summary}"
    return summary


def _ensure_claim_units_visible(
    paragraphs: list[str],
    claim_units: list[dict[str, Any]],
) -> list[str]:
    claims = [
        str(unit.get("claim") or "").strip()
        for unit in claim_units
        if str(unit.get("claim") or "").strip()
    ]
    if not claims:
        return paragraphs

    updated = list(paragraphs)
    if not updated or updated == [MISSING_VALUE]:
        updated = [claims[0]]

    visible_text = " ".join(updated)
    missing_claims = [claim for claim in claims if claim not in visible_text]
    if missing_claims:
        if len(claims) == 1:
            updated = claims
        else:
            split_at = min(2, len(claims) - 1)
            updated = [" ".join(claims[:split_at]), " ".join(claims[split_at:])]

    for index, paragraph in enumerate(updated):
        if not any(claim in paragraph for claim in claims):
            updated[index] = f"{paragraph} {claims[0]}"
    return updated


def _normalize_text(value: Any, *, preserve_claim_units: bool = False) -> dict[str, Any]:
    payload = _dict(value)
    paragraphs = _clean_list(payload.get("paragraphs"))
    if len(paragraphs) > 2:
        paragraphs = [paragraphs[0], " ".join(paragraphs[1:])]
    bullets = _clean_list(payload.get("bullets"))
    if not paragraphs and not bullets:
        paragraphs = [MISSING_VALUE]
    result = {
        "paragraphs": paragraphs,
        "bullets": bullets,
        "grounding_refs": _clean_refs(payload.get("grounding_refs")),
    }
    if "card_keys" in payload:
        result["card_keys"] = _clean_identifiers(payload.get("card_keys"))
    if "_claim_units" in payload:
        result["_claim_units"] = [
            {
                "claim": _plain_korean_text(str(unit.get("claim") or "").strip()),
                "card_keys": _clean_identifiers(unit.get("card_keys")),
                "limitation_categories": _clean_identifiers(
                    unit.get("limitation_categories")
                ),
            }
            for unit in payload.get("_claim_units") or []
            if isinstance(unit, dict) and str(unit.get("claim") or "").strip()
        ]
    declared_limitations = _clean_identifiers(payload.get("_limitation_categories"))
    if not declared_limitations and result.get("_claim_units"):
        declared_limitations = list(
            dict.fromkeys(
                category
                for unit in result["_claim_units"]
                for category in _clean_identifiers(unit.get("limitation_categories"))
            )
        )
    if declared_limitations:
        result["_limitation_categories"] = declared_limitations
    if preserve_claim_units and result.get("_claim_units"):
        result["paragraphs"] = _ensure_claim_units_visible(
            result["paragraphs"],
            result["_claim_units"],
        )
    return result


def _normalize_table(value: Any, *, preserve_strategy_values: bool = False) -> dict[str, Any]:
    payload = _dict(value)
    columns = _clean_list(payload.get("columns")) or ["항목", "내용"]
    rows = payload.get("rows") if isinstance(payload.get("rows"), list) else []
    if not rows:
        rows = [[MISSING_VALUE for _ in columns]]
    result = {
        "columns": columns,
        "rows": _plain_korean_rows(rows, preserve_strategy_values=preserve_strategy_values),
        "grounding_refs": _clean_refs(payload.get("grounding_refs")),
    }
    if "card_keys" in payload:
        result["card_keys"] = _clean_identifiers(payload.get("card_keys"))
    return result


def _dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _clean_list(value: Any) -> list[str]:
    if isinstance(value, list):
        return [_plain_korean_text(str(item).strip()) for item in value if str(item).strip()]
    if isinstance(value, str) and value.strip():
        return [_plain_korean_text(value.strip())]
    return []


def _clean_refs(value: Any) -> list[str]:
    refs = _clean_list(value)
    return list(dict.fromkeys(refs))


def _clean_identifiers(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return list(dict.fromkeys(str(item).strip() for item in value if str(item).strip()))


def _plain_korean_rows(rows: list[Any], *, preserve_strategy_values: bool = False) -> list[Any]:
    normalized = []
    for row in rows:
        if not isinstance(row, dict):
            normalized.append(_plain_korean_value(row))
            continue
        normalized.append(
            {
                _plain_korean_text(str(key)): (
                    value
                    if str(key).startswith("_")
                    or preserve_strategy_values
                    and str(key)
                    in {
                        "해석",
                        "투자의견 영향",
                        "리스크",
                        "투자 해석",
                        "영향",
                        "현재 확인된 내용",
                    }
                    else _plain_korean_value(value)
                )
                for key, value in row.items()
            }
        )
    return normalized


def _plain_korean_value(value: Any) -> Any:
    if isinstance(value, str):
        return _plain_korean_text(value)
    if isinstance(value, list):
        return [_plain_korean_value(item) for item in value]
    if isinstance(value, dict):
        return {_plain_korean_text(str(key)): _plain_korean_value(item) for key, item in value.items()}
    return value


def _plain_korean_text(text: str) -> str:
    text = re.sub(
        r"(?<![A-Za-z0-9_])(\d{4})\s+HALF\s+YTD(?![A-Za-z0-9_])",
        lambda match: f"{match.group(1)}년 반기 누적",
        text,
        flags=re.IGNORECASE,
    )
    text = re.sub(
        r"(?<![A-Za-z0-9_])(\d{4})\s+HALF\s+누적\s+기준(?![A-Za-z0-9_])",
        lambda match: f"{match.group(1)}년 반기 누적",
        text,
        flags=re.IGNORECASE,
    )
    text = re.sub(
        r"(?<![A-Za-z0-9_])(\d{4})\s+Q([1-4])\s+YTD(?![A-Za-z0-9_])",
        lambda match: f"{match.group(1)}년 {match.group(2)}분기 누적",
        text,
        flags=re.IGNORECASE,
    )
    text = re.sub(
        r"(?<![A-Za-z0-9_])(\d{4})\s+(?:ANNUAL[\s_-]+)?FULL[\s_-]+YEAR(?![A-Za-z0-9_])",
        lambda match: f"{match.group(1)}년 연간 실적",
        text,
        flags=re.IGNORECASE,
    )
    text = re.sub(r"(?<!\d)(\d{4})\s*반기", lambda match: f"{match.group(1)}년 반기", text)
    text = re.sub(
        r"(?<![\d.])-?\d+\.\d{3,}(?![\d.])",
        lambda match: f"{float(match.group(0)):.2f}",
        text,
    )
    replacements = [
        (r"(?<![A-Za-z0-9_])YTD(?![A-Za-z0-9_])", "누적 기준"),
        (r"(?<![A-Za-z0-9_])FULL[\s_-]+YEAR(?![A-Za-z0-9_])", "연간 실적"),
        (r"(?<![A-Za-z0-9_])peers?(?![A-Za-z0-9_])", "비교기업"),
        (r"피어", "비교기업"),
        (r"(?<![A-Za-z0-9_])catalysts?(?![A-Za-z0-9_])", "촉매"),
        (r"(?<![A-Za-z0-9_])monitoring(?![A-Za-z0-9_])", "확인"),
        (r"선택일 계산 밸류에이션", "기준일 밸류에이션"),
        (r"선택일 계산 배수", "기준일 산출 배수"),
        (r"멀티플 프리미엄", "밸류에이션 프리미엄"),
        (r"비교기업 비교", "비교기업 분석"),
        (
            r"주요 제품·서비스 공시표는 주요 제품·서비스 공시표 기준이며",
            "제품별 매출 구성은 주요 제품·서비스 공시표 기준이며",
        ),
    ]
    for pattern, replacement in replacements:
        text = re.sub(pattern, replacement, text, flags=re.IGNORECASE)
    text = (
        text.replace("누적와", "누적과")
        .replace("누적는", "누적은")
        .replace("비교기업 분석는", "비교기업 분석은")
    )
    return text
