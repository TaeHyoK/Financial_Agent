"""LLM-based Strategy Agent for evidence-grounded investment synthesis."""

from __future__ import annotations

import hashlib
import json
import os
import re
from copy import deepcopy
from datetime import datetime
from pathlib import Path
from typing import Any
from urllib import error, request

from shared.jsonio import atomic_write_text as _atomic_write_text
from shared.llm_clients import (
    compact_json,
    estimate_text_tokens,
    execute_with_telemetry,
    is_transient_transport_error,
)
from orchestration.ablation import config_from_mapping
from orchestration.config import agent_output_dir, company_from_run_key, normalize_date, safe_label

from . import AGENT_DIR, DEFAULT_TARGET_CONFIG, OUTPUT_ROOT
from .packet import build_compact_strategy_packet
from .artifacts import (
    COMPACT_PACKET_FILENAME,
    CONTEXT_PACKAGE_FILENAME,
    CONTEXT_TELEMETRY_FILENAME,
    DECISION_CACHE_FILENAME,
    DECISION_OUTPUT_FILENAME,
    DECISION_PROFILE_FILENAME,
    FAILURE_REPORT_FILENAME,
    GENERATION_CONTEXT_FILENAME,
    PACKET_PROVENANCE_FILENAME,
)

from .decision import (
    CONTEXT_VERSION,
    DECISION_VERSION,
    STRATEGY_CACHE_VERSION,
    align_strategy_decision_evidence_plan,
    build_strategy_context_package,
    strategy_decision_response_format,
    validate_strategy_decision,
)


OUTPUT_VERSION = "4.0"
BASIS_CARD_VERSION = "1.2"
DECISION_BASIS_VERSION = "1.0"
PROMPTS_DIR = AGENT_DIR / "prompts"
DEFAULT_ENV_FILE = AGENT_DIR.parents[2] / "configs" / ".env"
DEFAULT_OPENAI_MODEL = "gpt-5.4"
DEFAULT_OPENAI_MAX_TOKENS = 20000
FINAL_RECOMMENDATIONS = {"Buy", "Hold", "Sell"}
DEFAULT_DECISION_HORIZON_PROFILE = "annual"
DECISION_HORIZON_PROFILES = {
    "annual": {
        "horizon": "12개월",
        "policy": (
            "- 기준일부터 향후 12개월의 기업 투자 매력을 판단한다. 실적의 지속성, 사업 변화, 가격 수준과 위험을 종합한다.\n"
            "- 의견 등급의 공통 기준점은 향후 12개월 기대수익률 Buy +15% 이상, Hold -15% 초과~+15% 미만, Sell -15% 이하이다. 증권사 전체의 통일된 기준이 아니라 본 분석의 등급 정의다.\n"
            "- 이 구간은 의견을 해석하는 기준점이지 수익률 계산이나 임계값 검증 요구가 아니다. 과거 수익률에 적용하지 않는다. 구간에 맞추기 위해 목표주가나 기대수익률을 만들어내지 않는다.\n"
            "- 시장 대비 성과는 판단 근거이며, 의견은 지수 대비 상대수익률 이진 분류가 아니다.\n"
            "- 단기 과열이나 진입 시점만으로 장기 투자 의견을 결정하지 않는다. 목표주가·미래 EPS의 부재만으로 중립을 선택하지 않는다.\n"
            "- horizon은 정확히 12개월로 반환한다."
        ),
    },
    "default": {
        "horizon": "6~12개월",
        "policy": (
            "- 향후 6~12개월 관점에서 판단한다. 중기 실적 지속성, 현금흐름, 현재 가격과 밸류에이션, "
            "확인 가능한 촉매·위험을 균형 있게 본다.\n"
            "- `decision.horizon`은 정확히 `6~12개월`로 반환한다."
        ),
    },
    "unspecified": {
        "horizon": "기간 미지정",
        "policy": (
            "- 특정 보유기간을 미리 가정하지 않는다. 현재 입력 근거가 보여주는 전반적인 투자 매력과 "
            "위험의 균형만 판단하며, 임의의 기간 때문에 특정 근거의 중요도를 높이거나 낮추지 않는다.\n"
            "- `decision.horizon`은 정확히 `기간 미지정`으로 반환한다."
        ),
    },
    "short_term": {
        "horizon": "1개월",
        "policy": (
            "- 향후 1개월 단기 관점에서 판단한다. 기준일까지 확인된 최근 가격·거래량·시장 기준지표 "
            "상대성과, 현재 가치평가, 보도된 사업 사건의 단기 의미를 비교한다. 재무 기여가 수치로 "
            "확인되지 않은 뉴스도 심리·수급·운영 위험에 직접 관련되면 판단에 사용할 수 있지만, "
            "확인된 실적 기여로 바꾸어 쓰지 않는다.\n"
            "- `decision.horizon`은 정확히 `1개월`로 반환한다."
        ),
    },
    "medium_term": {
        "horizon": "3개월",
        "policy": (
            "- 향후 3개월 중기 관점에서 판단한다. 기준일까지 확인된 시장 흐름과 가치평가, 촉매·위험, "
            "실적 및 현금흐름의 지속 가능성을 함께 평가한다. 재무 기여가 "
            "확인되지 않은 기대는 결정적 근거로 승격하지 않는다.\n"
            "- `decision.horizon`은 정확히 `3개월`로 반환한다."
        ),
    },
    "long_term": {
        "horizon": "6개월",
        "policy": (
            "- 향후 6개월 장기 관점에서 판단한다. 단기 가격 변동보다 실적 개선의 지속성, 현금창출력, "
            "재무구조, 제품 집중도, 경쟁 위치와 사업 실행 가능성을 우선한다. 뉴스는 기간 안에 사업·재무 "
            "성과로 연결될 근거가 있을 때만 방향 판단에 사용한다.\n"
            "- `decision.horizon`은 정확히 `6개월`로 반환한다."
        ),
    },
}
CONTENT_PLAN_SECTIONS = (
    "investment_thesis",
    "financial_view",
    "business_mix_view",
    "catalyst_view",
    "risk_view",
    "market_price_view",
    "valuation_view",
    "cross_agent_consistency_check",
    "peer_competitor_positioning",
    "decision_balance",
    "limitations",
)
DECISION_REFERENCE_SECTIONS = (
    "final_recommendation",
    "investment_thesis",
    "financial_view",
    "business_mix_view",
    "catalyst_view",
    "risk_view",
    "market_price_view",
    "valuation_view",
    "cross_agent_consistency_check",
    "peer_competitor_positioning",
    "decision_balance",
    "final_rationale",
    "limitations",
)
BASIS_SOURCE_ROOTS = (
    "claim_ledger",
    "evidence_catalog",
    "secondary_context_assessments",
    "structured_facts",
    "peer_metric_catalog",
    "peer_context",
    "limitations",
    "decision_constraints",
)


def run_strategy_agent(
    *,
    target_company_name: str,
    target_run_key: str,
    target_financial_path: Path,
    target_news_path: Path,
    target_yfinance_path: Path,
    output_dir: Path,
    peer_comparison_path: Path | None = None,
    peer_analysis_path: Path | None = None,
    llm_provider: str = "openai",
    llm_model: str = "auto",
    llm_timeout: int = 120,
    env_file: Path | None = DEFAULT_ENV_FILE,
    ablation_config: dict[str, Any] | None = None,
    decision_horizon_profile: str = DEFAULT_DECISION_HORIZON_PROFILE,
) -> dict[str, Any]:
    """Run one Strategy contract with separate decision and report-context evidence."""

    if env_file:
        load_env_file(env_file)
    profile = resolve_decision_horizon_profile(decision_horizon_profile)
    output_dir = output_dir.expanduser().resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    input_bundle = build_strategy_input_bundle(
        target_company_name=target_company_name,
        target_run_key=target_run_key,
        target_financial_path=target_financial_path,
        target_news_path=target_news_path,
        target_yfinance_path=target_yfinance_path,
        peer_comparison_path=peer_comparison_path,
        peer_analysis_path=peer_analysis_path,
        ablation_config=ablation_config,
    )
    validate_input_bundle(input_bundle)
    save_json(output_dir / "strategy_input_bundle.json", input_bundle)
    resolved_model = resolve_llm_model(resolve_llm_provider(llm_provider), llm_model)
    packet, provenance, packet_telemetry, _input_contract = build_compact_strategy_packet(
        input_bundle,
        model=resolved_model,
    )
    context = build_strategy_context_package(packet, input_bundle=input_bundle)
    strategy_context_mode = str(
        (input_bundle.get("ablation") or {}).get("strategy_context_mode")
        or "compact_cards"
    )
    generation_payload = build_strategy_generation_payload(
        input_bundle=input_bundle,
        context=context,
        context_mode=strategy_context_mode,
    )
    generation_prompt = decision_generation_prompt(
        decision_horizon_profile,
        context_mode=strategy_context_mode,
    )
    save_json(output_dir / COMPACT_PACKET_FILENAME, packet)
    save_json(output_dir / PACKET_PROVENANCE_FILENAME, provenance)
    save_json(output_dir / CONTEXT_PACKAGE_FILENAME, context)
    save_json(output_dir / GENERATION_CONTEXT_FILENAME, generation_payload)
    context_telemetry = {
        "context_version": CONTEXT_VERSION,
        "decision_contract": DECISION_VERSION,
        "strategy_context_mode": strategy_context_mode,
        "available_card_count": len(context.get("evidence_cards") or {}),
        "serialized_bytes": len(compact_json(context).encode("utf-8")),
        "estimated_input_tokens": estimate_text_tokens(
            compact_json(context),
            model=resolved_model,
        ),
        "source_packet_telemetry": packet_telemetry,
    }
    save_json(output_dir / CONTEXT_TELEMETRY_FILENAME, context_telemetry)

    decision_path = output_dir / DECISION_OUTPUT_FILENAME
    cache_path = output_dir / DECISION_CACHE_FILENAME
    fingerprint = strategy_fingerprint(
        context,
        llm_provider=llm_provider,
        llm_model=llm_model,
        decision_horizon_profile=decision_horizon_profile,
        generation_payload=generation_payload,
        generation_prompt=generation_prompt,
    )
    decision_output = load_cached_llm_output(decision_path, cache_path, fingerprint)
    failure_report_path = output_dir / FAILURE_REPORT_FILENAME
    if decision_output is None:
        try:
            decision_output = run_decision_agent(
                context,
                llm_provider=llm_provider,
                llm_model=llm_model,
                llm_timeout=llm_timeout,
                decision_horizon_profile=decision_horizon_profile,
                generation_payload=generation_payload,
                generation_prompt=generation_prompt,
            )
        except Exception as exc:
            save_json(
                failure_report_path,
                {
                    "status": "fail",
                    "stage": "decision_generation",
                    "error_type": type(exc).__name__,
                    "message": str(exc),
                    "fingerprint": fingerprint,
                    "decision_horizon_profile": decision_horizon_profile,
                    "required_horizon": profile["horizon"],
                },
            )
            raise
    decision_output, validation = preserve_and_validate_strategy(
        decision_output, context=context, output_dir=output_dir,
        fingerprint=fingerprint, decision_horizon_profile=decision_horizon_profile,
        required_horizon=str(profile["horizon"]),
    )
    if failure_report_path.exists():
        failure_report_path.unlink()
    strategy_report = build_strategy_report_projection(
        decision_output,
        input_bundle=input_bundle,
        context=context,
    )
    save_json(decision_path, decision_output)
    save_json(
        cache_path,
        {"fingerprint": fingerprint, "contract_version": DECISION_VERSION},
    )
    save_json(
        output_dir / DECISION_PROFILE_FILENAME,
        {
            "profile": decision_horizon_profile,
            "required_horizon": profile["horizon"],
            "prompt_sha256": hashlib.sha256(
                decision_prompt(decision_horizon_profile).encode("utf-8")
            ).hexdigest(),
            "integrity_validation": validation,
        },
    )
    _remove_runtime_validation_artifacts(output_dir)
    save_json(output_dir / "strategy_report.json", strategy_report)
    save_text(
        output_dir / "strategy_report.md",
        render_strategy_projection_markdown(strategy_report),
    )
    _remove_legacy_strategy_artifacts(output_dir)
    return strategy_report


def preserve_and_validate_strategy(
    output: dict[str, Any], *, context: dict[str, Any], output_dir: Path,
    fingerprint: str, decision_horizon_profile: str, required_horizon: str,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Archive the response before any alignment; never lose a paid response."""
    attempt = datetime.now().strftime("%Y%m%dT%H%M%S%f")
    raw_path = output_dir / "strategy_response_attempts" / f"{attempt}_{fingerprint[:12]}.json"
    save_json(raw_path, {"fingerprint": fingerprint, "decision_output": output})
    try:
        aligned = align_strategy_decision_evidence_plan(output, context=context)
        _require_runtime_decision_contract(
            aligned, expected_version=DECISION_VERSION,
            required_horizon=required_horizon, brief_key="strategy_brief",
        )
        validation = validate_strategy_decision(
            aligned, context=context, required_horizon=required_horizon,
        )
        return aligned, validation
    except Exception as exc:
        failure = {
            "status": "fail", "stage": "decision_alignment_or_validation",
            "error_type": type(exc).__name__, "message": str(exc),
            "fingerprint": fingerprint,
            "decision_horizon_profile": decision_horizon_profile,
            "required_horizon": required_horizon, "raw_response_path": str(raw_path),
        }
        save_json(raw_path.with_suffix(".failure.json"), failure)
        save_json(output_dir / FAILURE_REPORT_FILENAME, failure)
        raise


def _require_runtime_decision_contract(
    output: dict[str, Any],
    *,
    expected_version: str,
    required_horizon: str,
    brief_key: str,
) -> None:
    """Require only the fields needed to persist and render a Strategy response."""

    if not isinstance(output, dict) or output.get("decision_version") != expected_version:
        raise ValueError(f"Strategy decision_version must be {expected_version}.")
    brief = output.get(brief_key)
    if not isinstance(brief, dict):
        raise ValueError(f"Strategy output requires {brief_key}.")
    actual_horizon = str(brief.get("horizon") or "")
    if actual_horizon != required_horizon:
        raise ValueError(
            "Strategy decision horizon mismatch: "
            f"expected={required_horizon}, actual={actual_horizon}"
        )


def _remove_legacy_strategy_artifacts(output_dir: Path) -> None:
    """Drop artifacts left behind by superseded Strategy contracts."""

    for filename in (
        "strategy_content_plan.json",
        "strategy_content_plan_cache.json",
        "strategy_decision_packet.json",
        "strategy_llm_packet.json",
        "decision_basis_by_section.json",
        "decision_basis_card.json",
        "strategy_generation_context_v2.json",
        "strategy_decision_output_v2.json",
        "strategy_decision_cache_v2.json",
        "strategy_decision_profile_v2.json",
        "strategy_semantic_validation_v2.json",
        "strategy_failure_report_v2.json",
        "strategy_decision_output_v2.failed.json",
        "strategy_generation_context_v3.json",
        "strategy_decision_output_v3.json",
        "strategy_decision_cache_v3.json",
        "strategy_decision_profile_v3.json",
        "strategy_semantic_validation_v3.json",
        "strategy_failure_report_v3.json",
        "strategy_decision_output_v3.failed.json",
        "strategy_context_package_v4.json",
        "strategy_generation_context_v4.json",
        "strategy_context_telemetry_v4.json",
        "strategy_decision_output_v4.json",
        "strategy_decision_cache_v4.json",
        "strategy_decision_profile_v4.json",
        "strategy_semantic_validation_v4.json",
        "strategy_failure_report_v4.json",
        "strategy_decision_output_v4.failed.json",
        "strategy_compact_packet_v2.json",
        "strategy_packet_provenance_v2.json",
        "strategy_context_package_v5.json",
        "strategy_generation_context_v5.json",
        "strategy_context_telemetry_v5.json",
        "strategy_decision_output_v5.json",
        "strategy_decision_cache_v5.json",
        "strategy_decision_profile_v5.json",
        "strategy_failure_report_v5.json",
    ):
        path = output_dir / filename
        if path.exists():
            path.unlink()


def _remove_runtime_validation_artifacts(output_dir: Path) -> None:
    """Remove artifacts from the retired runtime-gate execution path."""

    for filename in (
        "strategy_semantic_validation_v2.json",
        "strategy_semantic_validation_v3.json",
        "strategy_semantic_validation_v4.json",
        "strategy_decision_output_v2.failed.json",
        "strategy_decision_output_v3.failed.json",
        "strategy_decision_output_v4.failed.json",
    ):
        path = output_dir / filename
        if path.exists():
            path.unlink()


def build_strategy_report_projection(
    decision_output: dict[str, Any],
    *,
    input_bundle: dict[str, Any],
    context: dict[str, Any],
) -> dict[str, Any]:
    """Project the decision and both evidence tiers for downstream Writer use."""

    target = require_dict(input_bundle.get("target_company"), "target_company")
    cards = require_dict(context.get("evidence_cards"), "evidence_cards")
    plan = require_dict(decision_output.get("evidence_plan"), "evidence_plan")

    def enrich(items: Any) -> list[dict[str, Any]]:
        result = []
        for item in items or []:
            if not isinstance(item, dict):
                continue
            card_key = str(item.get("card_key") or "")
            card = cards.get(card_key) if isinstance(cards.get(card_key), dict) else {}
            result.append(
                {
                    **deepcopy(item),
                    "label": card.get("label"),
                    "domain": card.get("domain"),
                }
            )
        return result

    return {
        "agent_name": "Strategy Agent",
        "output_version": "9.0",
        "contract_version": DECISION_VERSION,
        "schema_revision": decision_output.get("schema_revision"),
        "created_at": datetime.now().isoformat(timespec="seconds"),
        "target_company_name": target.get("company_name"),
        "target_run_key": target.get("run_key"),
        "strategy_brief": deepcopy(decision_output.get("strategy_brief") or {}),
        "evidence_plan": {
            "decision_basis_cards": enrich(plan.get("decision_basis_cards")),
            "report_context_cards": enrich(plan.get("report_context_cards")),
            "coverage_assessment": deepcopy(plan.get("coverage_assessment") or {}),
        },
        "report_insights": deepcopy(decision_output.get("report_insights") or []),
        "key_risks": deepcopy(decision_output.get("key_risks") or []),
        "limitation_requirements": deepcopy(
            context.get("limitation_requirements") or []
        ),
        "data_limitations": deepcopy(context.get("data_limitations") or []),
        "applicability_notes": deepcopy(context.get("applicability_notes") or {}),
        "coverage_summary": deepcopy(context.get("coverage_summary") or {}),
    }


def render_strategy_projection_markdown(report: dict[str, Any]) -> str:
    """Render the Strategy output without internal evidence identifiers."""

    brief = require_dict(report.get("strategy_brief"), "strategy_brief")
    level_names = {"high": "높음", "medium": "보통", "low": "낮음"}

    def linked_text(key: str) -> str:
        return str(require_dict(brief.get(key), f"strategy_brief.{key}").get("text") or "")

    lines = [
        f"# {report.get('target_company_name')}: {brief.get('headline')}",
        "",
        f"판단 기간: {brief.get('horizon')}",
        "",
        "## 결론",
        linked_text("thesis"),
        "",
        "## 투자 의견",
        {"Buy": "매수", "Hold": "중립", "Sell": "매도"}[brief["recommendation"]],
        "", linked_text("decision_rationale") if brief.get("decision_rationale") else "",
        "", "## 최근 실적", linked_text("earnings_review"),
        "", f"## 향후 {brief.get('horizon')} 전망", linked_text("outlook"),
        f"- 가격 판단: {linked_text('price_assessment')}",
        "",
        "## 핵심 분석",
    ]
    insights = [item for item in report.get("report_insights") or [] if isinstance(item, dict)]
    if insights:
        for item in insights:
            lines.append(f"- {item.get('text')}")
    else:
        lines.append("- 별도의 설명용 분석을 추가하지 않았다.")
    lines.extend(["", "## 반대 논리", linked_text("counterview"), "", "## 주요 위험"])
    risks = [item for item in report.get("key_risks") or [] if isinstance(item, dict)]
    if risks:
        for item in risks:
            lines.append(
                f"- {item.get('risk_title')}: {item.get('risk')} — {item.get('current_implication')}"
            )
    else:
        lines.append("- 이번 판단에서 별도의 주요 위험을 제시하지 않았다.")
    lines.extend(
        [
            "",
            "## 판단 한계",
            linked_text("decision_limitation"),
            "",
            (
                "자료 충실도: "
                f"{level_names.get(str(brief.get('evidence_sufficiency')), brief.get('evidence_sufficiency'))} · "
                "판단 확신도: "
                f"{level_names.get(str(brief.get('decision_confidence')), brief.get('decision_confidence'))}"
            ),
        ]
    )
    return "\n".join(lines).rstrip() + "\n"


def strategy_fingerprint(
    context: dict[str, Any],
    *,
    llm_provider: str,
    llm_model: str,
    decision_horizon_profile: str = DEFAULT_DECISION_HORIZON_PROFILE,
    generation_payload: dict[str, Any] | None = None,
    generation_prompt: str | None = None,
) -> str:
    """Fingerprint the decision context, prompt, schema, provider, and model."""

    profile = resolve_decision_horizon_profile(decision_horizon_profile)
    payload = {
        "cache_version": STRATEGY_CACHE_VERSION,
        "contract_version": DECISION_VERSION,
        "context": context,
        "generation_payload": generation_payload
        or {CONTEXT_VERSION: context},
        "decision_horizon_profile": decision_horizon_profile,
        "prompt": generation_prompt or decision_prompt(decision_horizon_profile),
        "response_format": strategy_decision_response_format(
            context,
            required_horizon=str(profile["horizon"]),
        ),
        "provider": llm_provider,
        "model": llm_model,
    }
    return hashlib.sha256(compact_json(payload, sort_keys=True).encode("utf-8")).hexdigest()


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
    peer_comparison: Path | None = None,
    peer_analysis: Path | None = None,
    llm_provider: str = "openai",
    llm_model: str = "auto",
    llm_timeout: int = 120,
    env_file: Path | None = DEFAULT_ENV_FILE,
    ablation_config: dict[str, Any] | None = None,
    decision_horizon_profile: str = DEFAULT_DECISION_HORIZON_PROFILE,
) -> dict[str, Any]:
    """Compatibility wrapper around run_strategy_agent using repo defaults."""

    output_root = output_root.expanduser().resolve()
    identity = load_identity_from_config(target_config) if target_config and target_config.exists() else {}
    target_run_key = run_key or infer_run_key_from_paths([financial_report, news_report, yfinance_report]) or identity.get("run_key")
    if not target_run_key:
        raise ValueError("target_run_key is required.")
    target_company_name = identity.get("company_name") or company_from_run_key(target_run_key)
    paths = {
        "financial": financial_report or agent_output_dir(output_root, target_run_key, "Financial") / "final_report.json",
        "news": news_report or agent_output_dir(output_root, target_run_key, "News") / "final_report.json",
        "yfinance": yfinance_report or agent_output_dir(output_root, target_run_key, "Y_Finance") / "final_report.json",
    }
    output_dir = (
        output_json.parent
        if output_json
        else output_md.parent
        if output_md
        else agent_output_dir(output_root, target_run_key, "Strategy")
    )
    report = run_strategy_agent(
        target_company_name=target_company_name,
        target_run_key=target_run_key,
        target_financial_path=paths["financial"],
        target_news_path=paths["news"],
        target_yfinance_path=paths["yfinance"],
        output_dir=output_dir,
        peer_comparison_path=peer_comparison,
        peer_analysis_path=peer_analysis,
        llm_provider=llm_provider,
        llm_model=llm_model,
        llm_timeout=llm_timeout,
        env_file=env_file,
        ablation_config=ablation_config,
        decision_horizon_profile=decision_horizon_profile,
    )
    if output_json and output_json != output_dir / "strategy_report.json":
        save_json(output_json, report)
    if output_md and output_md != output_dir / "strategy_report.md":
        if report.get("contract_version") != DECISION_VERSION:
            raise ValueError(
                f"Unsupported Strategy contract version: {report.get('contract_version')}"
            )
        rendered = render_strategy_projection_markdown(report)
        save_text(output_md, rendered)
    return report


def build_strategy_input_bundle(
    *,
    target_company_name: str,
    target_run_key: str,
    target_financial_path: Path,
    target_news_path: Path,
    target_yfinance_path: Path,
    peer_comparison_path: Path | None = None,
    peer_analysis_path: Path | None = None,
    ablation_config: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Create the exact input bundle read by the two Strategy Agent LLM steps."""

    target_financial_path = target_financial_path.expanduser().resolve()
    target_news_path = target_news_path.expanduser().resolve()
    target_yfinance_path = target_yfinance_path.expanduser().resolve()
    ablation = config_from_mapping(ablation_config)
    raw_financial = load_required_json(target_financial_path, "Target Financial")
    raw_news = load_required_json(target_news_path, "Target News")
    raw_yfinance = load_required_json(target_yfinance_path, "Target YFinance")
    all_reports = {
        "financial": sanitize_strategy_input_report(raw_financial, "financial"),
        "news": sanitize_strategy_input_report(raw_news, "news"),
        "yfinance": sanitize_strategy_input_report(raw_yfinance, "yfinance"),
    }
    financial = all_reports["financial"] if "financial" in ablation.included_domains else {}
    news = all_reports["news"] if "news" in ablation.included_domains else {}
    yfinance = all_reports["yfinance"] if "yfinance" in ablation.included_domains else {}
    financial_validation = {"source_path": "", "summary": {}, "claims": []}
    news_validation = {"source_path": "", "summary": {}, "claims": []}
    yfinance_validation = {"source_path": "", "summary": {}, "claims": []}
    target_company = infer_target_company(
        target_company_name=target_company_name,
        target_run_key=target_run_key,
        financial=financial,
        news=news,
        yfinance=yfinance,
    )
    peer_comparison = (
        load_peer_comparison(peer_comparison_path)
        if ablation.include_competitor
        else {}
    )
    peer_analysis = (
        load_peer_analysis(peer_analysis_path)
        if ablation.include_competitor
        else {}
    )
    decision_constraints = extract_decision_constraints(financial, news, yfinance)
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
        "peer_comparison": peer_comparison,
        "peer_comparison_analysis": peer_analysis,
        "evidence_catalogs": {
            "financial": load_financial_evidence_catalog(financial) if financial else {},
            "news": load_news_evidence_catalog(raw_news, target_news_path) if news else {},
            "yfinance": load_yfinance_evidence_catalog(yfinance) if yfinance else {},
        },
        "evidence_hierarchy": build_evidence_hierarchy(peer_comparison_available=bool(peer_comparison)),
        "decision_constraints": decision_constraints,
        "ablation": ablation.as_dict(),
        "input_metadata": {
            "target_financial_path": str(target_financial_path),
            "target_news_path": str(target_news_path),
            "target_yfinance_path": str(target_yfinance_path),
            "target_validation_paths": {
                "financial": financial_validation.get("source_path", ""),
                "news": news_validation.get("source_path", ""),
                "yfinance": yfinance_validation.get("source_path", ""),
            },
            "peer_comparison_path": str(peer_comparison_path.expanduser().resolve()) if peer_comparison_path else "",
            "peer_analysis_path": str(peer_analysis_path.expanduser().resolve()) if peer_analysis_path else "",
            "created_at": datetime.now().isoformat(timespec="seconds"),
        },
    }


def load_news_evidence_catalog(report: dict[str, Any], report_path: Path) -> dict[str, Any]:
    """Load the News evidence catalog referenced by its verified handoff."""

    output = report.get("output") if isinstance(report.get("output"), dict) else report
    path_value = output.get("evidence_map_path") if isinstance(output, dict) else None
    if not path_value:
        return {}
    declared_path = Path(str(path_value)).expanduser()
    candidates = [
        declared_path if declared_path.is_absolute() else report_path.parent / declared_path,
        report_path.parent / "output" / declared_path.name,
        report_path.parent / declared_path.name,
    ]
    path = next((candidate for candidate in candidates if candidate.exists()), None)
    if path is None:
        return {}
    payload = load_json(path)
    return payload if isinstance(payload, dict) else {}


def load_financial_evidence_catalog(report: dict[str, Any]) -> dict[str, Any]:
    """Normalize Financial key evidence without its generated interpretation text."""

    catalog: dict[str, Any] = {}
    source_date = str(report.get("as_of_date") or "")
    handoff = report.get("strategy_handoff") or {}
    for item in handoff.get("key_evidence") or []:
        if not isinstance(item, dict) or str(item.get("source") or "").upper() != "DART":
            continue
        evidence_id = clean_text(item.get("evidence_id"))
        if not evidence_id:
            continue
        catalog[evidence_id] = {
            "evidence_id": evidence_id,
            "domain": "financial",
            "origin_type": "raw_source",
            "source_ref": f"dart_financial_evidence.{clean_text(item.get('metric_or_event')) or evidence_id}",
            "source_date": source_date,
            "period": clean_text(item.get("period")),
            "metric": clean_text(item.get("metric_or_event")),
            "value": deepcopy(item.get("value")),
            "unit": clean_text(item.get("period_basis")),
        }
    return catalog


def load_yfinance_evidence_catalog(report: dict[str, Any]) -> dict[str, Any]:
    """Merge primary market and referenced secondary catalogs from YFinance."""

    catalog: dict[str, Any] = {}
    for key in ("primary_evidence_catalog", "secondary_context_catalog"):
        value = report.get(key)
        if not isinstance(value, dict):
            continue
        for evidence_id, evidence in value.items():
            if isinstance(evidence, dict):
                catalog[str(evidence_id)] = deepcopy(evidence)
    return catalog


def run_decision_agent(
    context: dict[str, Any],
    *,
    llm_provider: str,
    llm_model: str,
    llm_timeout: int,
    decision_horizon_profile: str = DEFAULT_DECISION_HORIZON_PROFILE,
    generation_payload: dict[str, Any] | None = None,
    generation_prompt: str | None = None,
) -> dict[str, Any]:
    """Call the Strategy decision LLM; semantic judgment remains model-authored."""

    profile = resolve_decision_horizon_profile(decision_horizon_profile)
    output = call_llm_json(
        prompt=generation_prompt or decision_prompt(decision_horizon_profile),
        payload=generation_payload or {CONTEXT_VERSION: context},
        llm_provider=llm_provider,
        llm_model=llm_model,
        llm_timeout=llm_timeout,
        system_message=(
            "당신은 기업 리서치 Strategy Agent다. 제공된 자료를 분석하고 대안 해석을 비교한 뒤 "
            "투자 의견과 선택 이유를 작성한다. 실제 사용한 근거를 연결한 한국어 JSON 객체 하나를 반환한다."
        ),
        response_format=strategy_decision_response_format(
            context,
            required_horizon=str(profile["horizon"]),
        ),
    )
    brief = output.get("strategy_brief") if isinstance(output, dict) else None
    actual_horizon = str(brief.get("horizon") if isinstance(brief, dict) else "")
    if actual_horizon != profile["horizon"]:
        raise ValueError(
            f"Strategy decision horizon mismatch: expected={profile['horizon']}, "
            f"actual={actual_horizon}"
        )
    return output


def build_strategy_generation_payload(
    *,
    input_bundle: dict[str, Any],
    context: dict[str, Any],
    context_mode: str,
) -> dict[str, Any]:
    """Build the decision generation payload for production and ablation runs."""

    payload = {CONTEXT_VERSION: context}
    if context_mode == "compact_cards":
        return payload
    if context_mode != "full_reports":
        raise ValueError(f"Unknown Strategy context mode: {context_mode}")
    payload["full_context_ablation"] = {
        "target_company": deepcopy(input_bundle.get("target_company") or {}),
        "target_reports": deepcopy(input_bundle.get("target_reports") or {}),
        "target_validation_evidence": deepcopy(
            input_bundle.get("target_validation_evidence") or {}
        ),
        "peer_comparison": deepcopy(input_bundle.get("peer_comparison") or {}),
        "peer_comparison_analysis": deepcopy(
            input_bundle.get("peer_comparison_analysis") or {}
        ),
        "decision_constraints": deepcopy(input_bundle.get("decision_constraints") or []),
    }
    return payload


def decision_generation_prompt(
    decision_horizon_profile: str,
    *,
    context_mode: str,
) -> str:
    """Render the decision prompt and optional full-context ablation instruction."""

    prompt = decision_prompt(decision_horizon_profile)
    if context_mode == "compact_cards":
        return prompt
    if context_mode != "full_reports":
        raise ValueError(f"Unknown Strategy context mode: {context_mode}")
    return (
        prompt
        + "\n\n## 전체 문맥 제외 실험\n"
        + "이번 실험에서는 하위 에이전트 보고서와 검증 자료 전체도 추가로 제공된다. 근거 연결은 "
        + f"{CONTEXT_VERSION}.evidence_cards 안의 카드로 한정하고, 추가 문맥에만 존재하는 "
        + "사실이나 수치를 보고서에 새로 쓰지 않는다."
    )


def load_cached_llm_output(
    output_path: Path,
    cache_path: Path,
    expected_fingerprint: str,
) -> dict[str, Any] | None:
    if not output_path.exists() or not cache_path.exists():
        return None
    try:
        cache = load_json(cache_path)
        output = load_json(output_path)
    except (OSError, ValueError, json.JSONDecodeError):
        return None
    return output if cache.get("fingerprint") == expected_fingerprint else None


def sanitize_strategy_input_report(report: dict[str, Any], domain: str) -> dict[str, Any]:
    """Keep only the bounded domain-agent evidence used by Strategy."""

    cleaned = strip_operational_validation_content(deepcopy(report))
    return compact_strategy_input_report(cleaned, domain)


def compact_strategy_input_report(report: dict[str, Any], domain: str) -> dict[str, Any]:
    """Return the bounded source sections used by Strategy decision prompts."""

    identity_keys = ("agent_name", "target_company", "ticker", "corp_code", "as_of_date")
    compact = {key: report.get(key) for key in identity_keys if key in report}
    if domain == "financial":
        for key in (
            "collection_context",
            "financial_trends",
            "revenue_breakdown",
            "share_information",
            "main_view",
            "financial_statement_view",
            "detailed_analysis",
            "strategy_handoff",
            "secondary_context",
            "secondary_context_assessment",
            "analysis_metadata",
        ):
            if key in report:
                compact[key] = report.get(key)
        return compact
    if domain == "news":
        output = report.get("output") if isinstance(report.get("output"), dict) else report
        compact["output"] = {
            key: output.get(key)
            for key in (
                "target_entity", "analysis_blocks", "overall_assessment",
                "secondary_context", "secondary_context_assessment", "context_policy_version",
            )
            if key in output
        }
        return compact
    if domain == "yfinance":
        for key in (
            "main_view",
            "time_horizon_view",
            "detailed_analysis",
            "valuation_snapshot",
            "primary_evidence_catalog",
            "secondary_context_catalog",
            "secondary_context",
            "secondary_context_assessment",
            "context_policy_version",
        ):
            if key in report:
                compact[key] = report.get(key)
        return compact
    return report


def strip_operational_validation_content(value: Any) -> Any:
    """Recursively remove obsolete workflow metadata from report evidence."""

    operational_keys = {
        "report_status",
        "verification_summary",
        "revision_brief",
        "rewrite_history",
        "question_answer_log_path",
        "source_context_summary",
    }
    if isinstance(value, dict):
        cleaned: dict[str, Any] = {}
        for key, child in value.items():
            if key in operational_keys:
                continue
            cleaned[key] = strip_operational_validation_content(child)
        return cleaned
    if isinstance(value, list):
        return [strip_operational_validation_content(item) for item in value]
    return value


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
    peer_comparison = bundle.get("peer_comparison")
    if peer_comparison:
        require_dict(peer_comparison, "peer_comparison")
        if not isinstance(peer_comparison.get("metrics"), list):
            raise ValueError("peer_comparison.metrics must be a list.")
        require_non_empty(peer_comparison.get("source_path"), "peer_comparison.source_path")
    peer_analysis = bundle.get("peer_comparison_analysis")
    if peer_analysis:
        require_dict(peer_analysis, "peer_comparison_analysis")
        require_non_empty(
            peer_analysis.get("comparison_brief"),
            "peer_comparison_analysis.comparison_brief",
        )
        if not isinstance(peer_analysis.get("comparison_points"), list):
            raise ValueError("peer_comparison_analysis.comparison_points must be a list.")
        require_non_empty(peer_analysis.get("source_path"), "peer_comparison_analysis.source_path")
    hierarchy = bundle.get("evidence_hierarchy")
    if not isinstance(hierarchy, list) or not hierarchy:
        raise ValueError("evidence_hierarchy must be a non-empty list.")


def load_peer_comparison(path: Path | None) -> dict[str, Any]:
    """Load the explicit pairwise comparison used by the decision agents."""

    if path is None:
        return {}
    resolved = path.expanduser().resolve()
    payload = load_required_json(resolved, "Peer comparison")
    return {
        "target_company": payload.get("target_company"),
        "peer_groups": payload.get("peer_groups") or {},
        "metrics": payload.get("metrics") or [],
        "comparison_limits": payload.get("comparison_limits") or [],
        "source_path": str(resolved),
    }


def load_peer_analysis(path: Path | None) -> dict[str, Any]:
    """Load the selected-peer analytical handoff used by Strategy."""

    if path is None:
        return {}
    resolved = path.expanduser().resolve()
    payload = load_required_json(resolved, "Peer comparison analysis")
    return {
        "comparison_version": payload.get("comparison_version"),
        "target_company": payload.get("target_company"),
        "peer_company": payload.get("peer_company"),
        "comparison_scope": payload.get("comparison_scope"),
        "comparison_brief": payload.get("comparison_brief"),
        "comparison_points": payload.get("comparison_points") or [],
        "comparison_limitations": payload.get("comparison_limitations") or [],
        "selected_basis_cards": payload.get("selected_basis_cards") or [],
        "source_path": str(resolved),
    }


def build_evidence_hierarchy(*, peer_comparison_available: bool) -> list[dict[str, Any]]:
    """Declare the order in which decision agents should evaluate evidence."""

    hierarchy = [
        {
            "priority": 1,
            "topic": "financial_trends_and_cash_flow",
            "source_paths": [
                "target_reports.financial.financial_trends",
                "target_reports.financial.financial_statement_view.cash_flow",
                "target_reports.financial.financial_statement_view.balance_sheet",
            ],
        },
        {
            "priority": 2,
            "topic": "revenue_composition_and_concentration",
            "source_paths": ["target_reports.financial.revenue_breakdown"],
        },
        {
            "priority": 3,
            "topic": "market_reaction",
            "source_paths": [
                "target_reports.yfinance.main_view",
                "target_reports.yfinance.time_horizon_view",
                "target_reports.yfinance.detailed_analysis.market_relative",
            ],
        },
        {
            "priority": 4,
            "topic": "valuation",
            "source_paths": ["target_reports.yfinance.valuation_snapshot"],
        },
        {
            "priority": 5,
            "topic": "recent_catalysts_and_risks",
            "source_paths": ["target_reports.news.output.analysis_blocks"],
        },
    ]
    if peer_comparison_available:
        hierarchy.append(
            {
                "priority": 6,
                "topic": "explicit_pairwise_peer_comparison",
                "source_paths": ["peer_comparison.metrics"],
            }
        )
    hierarchy.append(
        {
            "priority": 7,
            "topic": "counter_evidence_and_data_limits",
            "source_paths": ["target_validation_evidence", "decision_constraints"],
        }
    )
    return hierarchy


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
    """Extract analytical cautions without carrying workflow instructions."""

    constraints: list[str] = []
    constraints.extend(text_items(get_path(financial, ["main_view", "main_cautions"])))
    flags = get_path(financial, ["strategy_handoff", "reconciliation_flags"]) or []
    for flag in ensure_list(flags):
        if isinstance(flag, dict):
            constraints.append(clean_text(flag.get("flag_ko")))
    for report in (financial, yfinance, get_path(news, ["output"]) or news):
        for assessment in ensure_list(report.get("secondary_context_assessment")):
            if not isinstance(assessment, dict):
                continue
            if assessment.get("effect") == "contradicts":
                constraints.append(clean_text(assessment.get("statement")))
            constraints.append(clean_text(assessment.get("limitation")))
    return dedupe([clean_text(constraint) for constraint in constraints if clean_text(constraint)], 20)


def call_llm_json(
    *,
    prompt: str,
    payload: dict[str, Any],
    llm_provider: str,
    llm_model: str,
    llm_timeout: int,
    system_message: str,
    response_format: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Call selected LLM and parse a JSON object response."""

    provider = resolve_llm_provider(llm_provider)
    model = resolve_llm_model(provider, llm_model)
    if provider == "none":
        raise RuntimeError("Strategy Agent requires OPENAI_API_KEY.")
    user_prompt = f"{prompt}\n\nInput JSON:\n{compact_json(payload)}"
    if provider != "openai":
        raise RuntimeError(f"Unsupported LLM provider: {provider}")
    result = call_openai(
        user_prompt,
        model,
        llm_timeout,
        system_message=system_message,
        response_format=response_format,
    )
    return parse_llm_json(result["text"])


def call_openai(
    prompt: str,
    model: str,
    timeout: int,
    *,
    system_message: str,
    response_format: dict[str, Any] | None = None,
) -> dict[str, Any]:
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
        "response_format": response_format or {"type": "json_object"},
    }
    if uses_max_completion_tokens(model):
        payload["max_completion_tokens"] = max_tokens
    else:
        payload["temperature"] = 0.2
        payload["max_tokens"] = max_tokens
    req = request.Request(
        f"{base_url}/chat/completions",
        data=compact_json(payload).encode("utf-8"),
        headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
        method="POST",
    )
    def send_request() -> dict[str, Any]:
        try:
            with request.urlopen(req, timeout=timeout) as response:
                return json.loads(response.read().decode("utf-8"))
        except error.HTTPError as exc:
            body = exc.read().decode("utf-8", "ignore")
            raise RuntimeError(f"OpenAI HTTP {exc.code}: {body[:500]}") from exc

    result = execute_with_telemetry(
        send_request,
        request_payload=payload,
        model=model,
        step=f"strategy:{system_message.split('.')[0].strip().lower().replace(' ', '_')}",
        usage_getter=lambda response: response.get("usage", {}),
        max_attempts=max(0, int(os.getenv("LLM_TRANSPORT_RETRIES", "0"))) + 1,
        retry_predicate=is_transient_transport_error,
    )
    choices = result.get("choices") or []
    if not choices:
        raise RuntimeError(f"OpenAI returned no choices: {result}")
    choice = choices[0]
    finish_reason = clean_text(choice.get("finish_reason"))
    if finish_reason == "length":
        raise RuntimeError(
            "OpenAI response was truncated before valid JSON completed. "
            "Reduce the evidence packet or split the Strategy request."
        )
    text = choice.get("message", {}).get("content", "").strip()
    if not text:
        raise RuntimeError(f"OpenAI returned empty text (finish_reason={finish_reason or 'unknown'})")
    return {"text": text, "usage": result.get("usage", {}), "finish_reason": finish_reason}


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


def read_prompt(filename: str) -> str:
    """Read prompt file from prompts directory."""

    path = PROMPTS_DIR / filename
    if not path.exists():
        raise FileNotFoundError(f"Prompt file not found: {path}")
    return path.read_text(encoding="utf-8")


def resolve_decision_horizon_profile(profile: str) -> dict[str, str]:
    """Return one validated Strategy decision-horizon profile."""

    normalized = str(profile or DEFAULT_DECISION_HORIZON_PROFILE).strip().lower()
    if normalized not in DECISION_HORIZON_PROFILES:
        raise ValueError(
            "decision_horizon_profile must be one of: "
            + ", ".join(DECISION_HORIZON_PROFILES)
        )
    return DECISION_HORIZON_PROFILES[normalized]


def decision_prompt(
    profile: str = DEFAULT_DECISION_HORIZON_PROFILE,
) -> str:
    """Render the Strategy decision prompt with one horizon policy."""

    resolved = resolve_decision_horizon_profile(profile)
    template = read_prompt("decision_agent.md")
    placeholder = "{{DECISION_HORIZON_POLICY}}"
    if template.count(placeholder) != 1:
        raise ValueError(
            "decision_agent.md must contain exactly one horizon-policy placeholder."
        )
    return template.replace("12개월", str(resolved["horizon"])).replace(placeholder, str(resolved["policy"]))


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

    _atomic_write_text(path, json.dumps(payload, ensure_ascii=False, indent=2) + "\n")


def save_text(path: Path, content: str) -> None:
    """Write UTF-8 text."""

    _atomic_write_text(path, content)


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


def infer_run_key_from_paths(paths: list[Path | None]) -> str | None:
    """Infer run_key from a final_report.json source path."""

    for path in paths:
        if path and path.name == "final_report.json":
            return path.expanduser().parent.name
    return None


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


def normalize_iso_date(value: Any) -> str | None:
    """Return YYYY-MM-DD for date-like values."""

    digits = "".join(character for character in str(value or "") if character.isdigit())
    if len(digits) != 8:
        return None
    return f"{digits[:4]}-{digits[4:6]}-{digits[6:]}"
