"""LLM-based Strategy Agent for final Buy/Hold/Sell synthesis."""

from __future__ import annotations

import hashlib
import json
import os
import re
import tempfile
from copy import deepcopy
from datetime import datetime
from pathlib import Path
from typing import Any
from urllib import error, request

from shared.llm_clients import compact_json, execute_with_telemetry, is_transient_transport_error
from orchestration.ablation import config_from_mapping

from . import AGENT_DIR, DEFAULT_TARGET_CONFIG, OUTPUT_ROOT
from .contracts_v2 import (
    DECISION_VERSION,
    STRATEGY_CACHE_VERSION,
    build_compact_strategy_packet_v2,
    finalize_strategy_decision_v2,
    strategy_decision_response_format_v2,
    validate_strategy_decision_v2,
)


OUTPUT_VERSION = "4.0"
BASIS_CARD_VERSION = "1.2"
DECISION_BASIS_VERSION = "1.0"
PROMPTS_DIR = AGENT_DIR / "prompts"
DEFAULT_ENV_FILE = AGENT_DIR.parents[2] / "configs" / ".env"
DEFAULT_OPENAI_MODEL = "gpt-5.4"
DEFAULT_OPENAI_MAX_TOKENS = 20000
FINAL_RECOMMENDATIONS = {"Buy", "Hold", "Sell"}
DEFAULT_DECISION_HORIZON_PROFILE = "default"
DECISION_HORIZON_PROFILES = {
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
            "- 향후 1개월 단기 관점에서 판단한다. 최근 가격·거래량·KOSPI 상대성과, 현재 밸류에이션과 "
            "기간 안에 확인 가능한 촉매·위험을 우선한다. 장기 사업 가능성과 재무 기여가 확인되지 않은 "
            "뉴스는 보조 문맥으로만 사용한다.\n"
            "- `decision.horizon`은 정확히 `1개월`로 반환한다."
        ),
    },
    "medium_term": {
        "horizon": "3개월",
        "policy": (
            "- 향후 3개월 중기 관점에서 판단한다. 최근 시장 흐름과 밸류에이션을 보되 다음 실적 확인과 "
            "구체화 가능한 촉매·위험, 실적 및 현금흐름의 지속 가능성을 함께 평가한다. 재무 기여가 "
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
    llm_provider: str = "openai",
    llm_model: str = "auto",
    llm_timeout: int = 120,
    env_file: Path | None = DEFAULT_ENV_FILE,
    packet_version: str | None = None,
    emit_v2_shadow_artifacts: bool | None = None,
    ablation_config: dict[str, Any] | None = None,
    decision_horizon_profile: str = DEFAULT_DECISION_HORIZON_PROFILE,
) -> dict[str, Any]:
    """Run the selected Strategy contract without mixing v1 and v2 downstream inputs."""

    version = (packet_version or os.getenv("STRATEGY_PACKET_VERSION") or "v2").strip().lower()
    if version not in {"v1", "v2"}:
        raise ValueError("packet_version must be v1 or v2.")
    if version == "v2":
        return _run_strategy_agent_v2(
            target_company_name=target_company_name,
            target_run_key=target_run_key,
            target_financial_path=target_financial_path,
            target_news_path=target_news_path,
            target_yfinance_path=target_yfinance_path,
            output_dir=output_dir,
            peer_comparison_path=peer_comparison_path,
            llm_provider=llm_provider,
            llm_model=llm_model,
            llm_timeout=llm_timeout,
            env_file=env_file,
            ablation_config=ablation_config,
            decision_horizon_profile=decision_horizon_profile,
        )
    if decision_horizon_profile != DEFAULT_DECISION_HORIZON_PROFILE:
        raise ValueError("decision_horizon_profile is supported only by packet_version=v2.")
    report = _run_strategy_agent_v1(
        target_company_name=target_company_name,
        target_run_key=target_run_key,
        target_financial_path=target_financial_path,
        target_news_path=target_news_path,
        target_yfinance_path=target_yfinance_path,
        output_dir=output_dir,
        peer_comparison_path=peer_comparison_path,
        llm_provider=llm_provider,
        llm_model=llm_model,
        llm_timeout=llm_timeout,
        env_file=env_file,
        ablation_config=ablation_config,
    )
    shadow = emit_v2_shadow_artifacts
    if shadow is None:
        shadow = os.getenv("EMIT_STRATEGY_V2_SHADOW_ARTIFACTS", "").strip().lower() in {"1", "true", "yes"}
    if shadow:
        _emit_v2_shadow_artifacts(
            target_company_name=target_company_name,
            target_run_key=target_run_key,
            target_financial_path=target_financial_path,
            target_news_path=target_news_path,
            target_yfinance_path=target_yfinance_path,
            output_dir=output_dir,
            peer_comparison_path=peer_comparison_path,
            llm_model=llm_model,
            ablation_config=ablation_config,
        )
    return report


def _run_strategy_agent_v1(
    *,
    target_company_name: str,
    target_run_key: str,
    target_financial_path: Path,
    target_news_path: Path,
    target_yfinance_path: Path,
    output_dir: Path,
    peer_comparison_path: Path | None = None,
    llm_provider: str = "openai",
    llm_model: str = "auto",
    llm_timeout: int = 120,
    env_file: Path | None = DEFAULT_ENV_FILE,
    ablation_config: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Run the deprecated two-call Strategy contract for rollback only."""

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
        peer_comparison_path=peer_comparison_path,
        ablation_config=ablation_config,
    )
    validate_input_bundle(input_bundle)
    save_json(output_dir / "strategy_input_bundle.json", input_bundle)
    llm_packet = build_strategy_llm_packet(input_bundle)
    save_json(output_dir / "strategy_llm_packet.json", llm_packet)

    content_plan_path = output_dir / "strategy_content_plan.json"
    content_plan_cache_path = output_dir / "strategy_content_plan_cache.json"
    planner_fingerprint = content_plan_fingerprint(
        llm_packet,
        llm_provider=llm_provider,
        llm_model=llm_model,
    )
    content_plan = load_cached_content_plan(
        content_plan_path,
        content_plan_cache_path,
        planner_fingerprint,
    )
    if content_plan is None:
        content_plan = run_content_planner(
            llm_packet,
            llm_provider=llm_provider,
            llm_model=llm_model,
            llm_timeout=llm_timeout,
        )
        content_plan = normalize_content_plan(content_plan)
        validate_content_plan(content_plan, llm_packet=llm_packet)
        save_json(content_plan_path, content_plan)
        save_json(content_plan_cache_path, {"fingerprint": planner_fingerprint})
    else:
        validate_content_plan(content_plan, llm_packet=llm_packet)

    decision_packet = build_strategy_decision_packet(llm_packet, content_plan)
    save_json(output_dir / "strategy_decision_packet.json", decision_packet)
    decision_output_path = output_dir / "strategy_decision_output.json"
    decision_cache_path = output_dir / "strategy_decision_cache.json"
    decision_fp = decision_fingerprint(
        decision_packet,
        content_plan,
        llm_provider=llm_provider,
        llm_model=llm_model,
    )
    strategy_output = load_cached_llm_output(
        decision_output_path,
        decision_cache_path,
        decision_fp,
    )
    if strategy_output is None:
        strategy_output = run_decision_agent(
            decision_packet,
            content_plan,
            llm_provider=llm_provider,
            llm_model=llm_model,
            llm_timeout=llm_timeout,
        )
        save_json(decision_output_path, strategy_output)
        save_json(decision_cache_path, {"fingerprint": decision_fp})
    raw_report, raw_evidence_refs = split_strategy_decision_output(strategy_output)
    strategy_report = normalize_strategy_report(raw_report, input_bundle)
    validate_strategy_report(strategy_report, input_bundle=decision_packet)
    decision_basis_by_section = normalize_decision_basis_by_section(
        raw_evidence_refs,
        strategy_report,
        decision_packet,
    )
    validate_decision_basis_by_section(decision_basis_by_section, strategy_report)
    decision_basis_card = build_decision_basis_card(strategy_report, decision_basis_by_section)
    validate_decision_basis_card(decision_basis_card)
    save_json(output_dir / "strategy_report.json", strategy_report)
    save_text(output_dir / "strategy_report.md", render_strategy_markdown(strategy_report))
    save_json(output_dir / "decision_basis_by_section.json", decision_basis_by_section)
    save_json(output_dir / "decision_basis_card.json", decision_basis_card)
    return strategy_report


def _run_strategy_agent_v2(
    *,
    target_company_name: str,
    target_run_key: str,
    target_financial_path: Path,
    target_news_path: Path,
    target_yfinance_path: Path,
    output_dir: Path,
    peer_comparison_path: Path | None,
    llm_provider: str,
    llm_model: str,
    llm_timeout: int,
    env_file: Path | None,
    ablation_config: dict[str, Any] | None,
    decision_horizon_profile: str,
) -> dict[str, Any]:
    """Run the one-call self-contained Strategy contract."""

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
        ablation_config=ablation_config,
    )
    validate_input_bundle(input_bundle)
    save_json(output_dir / "strategy_input_bundle.json", input_bundle)
    resolved_model = resolve_llm_model(resolve_llm_provider(llm_provider), llm_model)
    packet, provenance, telemetry, gate_a = build_compact_strategy_packet_v2(
        input_bundle,
        model=resolved_model,
    )
    strategy_context_mode = str(
        (input_bundle.get("ablation") or {}).get("strategy_context_mode")
        or "compact_cards"
    )
    generation_payload = build_strategy_generation_payload_v2(
        input_bundle=input_bundle,
        compact_packet=packet,
        context_mode=strategy_context_mode,
    )
    generation_prompt = decision_generation_prompt_v2(
        decision_horizon_profile,
        context_mode=strategy_context_mode,
    )
    save_json(output_dir / "strategy_compact_packet_v2.json", packet)
    save_json(output_dir / "strategy_packet_provenance_v2.json", provenance)
    save_json(output_dir / "strategy_generation_context_v2.json", generation_payload)
    telemetry = {
        **telemetry,
        "strategy_context_mode": strategy_context_mode,
        "generation_payload_bytes": len(compact_json(generation_payload).encode("utf-8")),
    }
    save_json(output_dir / "strategy_packet_telemetry_v2.json", telemetry)

    decision_path = output_dir / "strategy_decision_output_v2.json"
    cache_path = output_dir / "strategy_decision_cache_v2.json"
    fingerprint = strategy_v2_fingerprint(
        packet,
        llm_provider=llm_provider,
        llm_model=llm_model,
        decision_horizon_profile=decision_horizon_profile,
        generation_payload=generation_payload,
        generation_prompt=generation_prompt,
    )
    decision_output = load_cached_llm_output(decision_path, cache_path, fingerprint)
    failure_report_path = output_dir / "strategy_failure_report_v2.json"
    if decision_output is None:
        try:
            decision_output = run_decision_agent_v2(
                packet,
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
                    "stage": "decision_generation_or_finalize",
                    "error_type": type(exc).__name__,
                    "message": str(exc),
                    "fingerprint": fingerprint,
                    "decision_horizon_profile": decision_horizon_profile,
                    "required_horizon": profile["horizon"],
                },
            )
            raise
    failed_decision_path = output_dir / "strategy_decision_output_v2.failed.json"
    try:
        gate_b = validate_strategy_decision_v2(
            decision_output,
            packet=packet,
            provenance=provenance,
            required_horizon=str(profile["horizon"]),
        )
    except ValueError as exc:
        save_json(failed_decision_path, decision_output)
        if cache_path.exists():
            cache_path.unlink()
        save_json(
            failure_report_path,
            {
                "status": "fail",
                "stage": "gate_b",
                "error_type": type(exc).__name__,
                "message": str(exc),
                "fingerprint": fingerprint,
                "decision_horizon_profile": decision_horizon_profile,
                "required_horizon": profile["horizon"],
                "failed_decision_path": str(failed_decision_path),
            },
        )
        raise
    if failed_decision_path.exists():
        failed_decision_path.unlink()
    if failure_report_path.exists():
        failure_report_path.unlink()
    decision_output = deepcopy(decision_output)
    decision_output.pop("strategy_report", None)
    strategy_report = build_strategy_report_projection_v2(
        decision_output,
        input_bundle=input_bundle,
        packet=packet,
    )
    save_json(decision_path, decision_output)
    save_json(cache_path, {"fingerprint": fingerprint, "contract_version": DECISION_VERSION})
    save_json(
        output_dir / "strategy_decision_profile_v2.json",
        {
            "profile": decision_horizon_profile,
            "required_horizon": profile["horizon"],
            "prompt_sha256": hashlib.sha256(
                decision_prompt_v2(decision_horizon_profile).encode("utf-8")
            ).hexdigest(),
        },
    )
    save_json(
        output_dir / "strategy_semantic_validation_v2.json",
        {"status": "pass", "gate_a": gate_a, "gate_b": gate_b},
    )
    save_json(output_dir / "strategy_report.json", strategy_report)
    save_text(
        output_dir / "strategy_report.md",
        render_strategy_projection_markdown_v2(strategy_report),
    )
    _remove_deprecated_v1_strategy_artifacts(output_dir)
    return strategy_report


def _remove_deprecated_v1_strategy_artifacts(output_dir: Path) -> None:
    for filename in (
        "strategy_content_plan.json",
        "strategy_content_plan_cache.json",
        "strategy_decision_output.json",
        "strategy_decision_cache.json",
        "strategy_decision_packet.json",
        "strategy_llm_packet.json",
        "decision_basis_by_section.json",
        "decision_basis_card.json",
    ):
        path = output_dir / filename
        if path.exists():
            path.unlink()


def _emit_v2_shadow_artifacts(
    *,
    target_company_name: str,
    target_run_key: str,
    target_financial_path: Path,
    target_news_path: Path,
    target_yfinance_path: Path,
    output_dir: Path,
    peer_comparison_path: Path | None,
    llm_model: str,
    ablation_config: dict[str, Any] | None,
) -> None:
    """Build v2 packet artifacts without making an additional LLM call."""

    bundle = build_strategy_input_bundle(
        target_company_name=target_company_name,
        target_run_key=target_run_key,
        target_financial_path=target_financial_path,
        target_news_path=target_news_path,
        target_yfinance_path=target_yfinance_path,
        peer_comparison_path=peer_comparison_path,
        ablation_config=ablation_config,
    )
    packet, provenance, telemetry, gate_a = build_compact_strategy_packet_v2(bundle, model=llm_model)
    output_dir = output_dir.expanduser().resolve()
    save_json(output_dir / "strategy_compact_packet_v2.json", packet)
    save_json(output_dir / "strategy_packet_provenance_v2.json", provenance)
    save_json(output_dir / "strategy_packet_telemetry_v2.json", telemetry)
    save_json(output_dir / "strategy_gate_a_shadow_v2.json", gate_a)


def build_strategy_report_projection_v2(
    decision_output: dict[str, Any],
    *,
    input_bundle: dict[str, Any],
    packet: dict[str, Any],
) -> dict[str, Any]:
    """Project the typed decision into a non-duplicative Strategy artifact."""

    target = require_dict(input_bundle.get("target_company"), "target_company")
    decision = require_dict(decision_output.get("decision"), "decision")
    bridge = require_dict(
        decision_output.get("recommendation_bridge"),
        "recommendation_bridge",
    )
    cards = require_dict(packet.get("cards"), "cards")
    assessments = []
    for item in decision_output.get("evidence_assessments") or []:
        if not isinstance(item, dict):
            continue
        card_key = str(item.get("card_key") or "")
        card = cards.get(card_key) if isinstance(cards.get(card_key), dict) else {}
        assessments.append(
            {
                "card_key": card_key,
                "label": card.get("label"),
                "domain": card.get("domain"),
                "evidence_family": card.get("evidence_family"),
                "comparison_scope": card.get("comparison_scope"),
                "materiality": item.get("materiality"),
                "investment_effect": item.get("investment_effect"),
                "interpretation": item.get("interpretation"),
            }
        )
    return {
        "agent_name": "Strategy Agent",
        "output_version": "6.0",
        "contract_version": DECISION_VERSION,
        "created_at": datetime.now().isoformat(timespec="seconds"),
        "target_company_name": target.get("company_name"),
        "target_run_key": target.get("run_key"),
        "final_recommendation": {
            "opinion": decision.get("opinion"),
            "investment_horizon": decision.get("horizon"),
            "data_coverage": decision.get("evidence_sufficiency"),
            "decision_confidence": bridge.get("decision_confidence"),
        },
        "recommendation_bridge": deepcopy(bridge),
        "evidence_assessments": assessments,
        "peer_findings": deepcopy(decision_output.get("peer_findings") or []),
        "decision_risk_factors": deepcopy(
            decision_output.get("decision_risk_factors") or []
        ),
        "limitation_requirements": deepcopy(packet.get("limitation_requirements") or []),
        "coverage_summary": deepcopy(packet.get("coverage_summary") or {}),
    }


def render_strategy_projection_markdown_v2(report: dict[str, Any]) -> str:
    """Render the typed Strategy projection without regenerating analytical prose."""

    recommendation = require_dict(
        report.get("final_recommendation"),
        "final_recommendation",
    )
    bridge = require_dict(report.get("recommendation_bridge"), "recommendation_bridge")
    lines = [
        f"# {report.get('target_company_name')} Strategy Report",
        "",
        "## Final Recommendation",
        f"- Opinion: {recommendation.get('opinion')}",
        f"- Horizon: {recommendation.get('investment_horizon')}",
        f"- Data coverage: {recommendation.get('data_coverage')}",
        f"- Decision confidence: {recommendation.get('decision_confidence')}",
        "",
        "## Recommendation Bridge",
        f"- Current price: {bridge.get('current_price_rationale')}",
        f"- Forward support: {bridge.get('forward_support')}",
        f"- Valuation counterweight: {bridge.get('valuation_counterweight')}",
        f"- Residual uncertainty: {bridge.get('residual_uncertainty')}",
        "",
        "## Evidence Assessments",
    ]
    for item in report.get("evidence_assessments") or []:
        if not isinstance(item, dict):
            continue
        lines.append(
            f"- [{item.get('materiality')}/{item.get('investment_effect')}] "
            f"{item.get('label')}: {item.get('interpretation')}"
        )
    lines.extend(["", "## Peer Findings"])
    for item in report.get("peer_findings") or []:
        if isinstance(item, dict):
            lines.append(f"- {item.get('finding')}")
    lines.extend(["", "## Risks"])
    for item in report.get("decision_risk_factors") or []:
        if isinstance(item, dict):
            lines.append(
                f"- {item.get('reader_summary') or item.get('risk_summary')} | "
                f"Monitoring: {item.get('monitoring_point')}"
            )
    lines.extend(["", "## Data Limits"])
    for item in report.get("limitation_requirements") or []:
        if isinstance(item, dict):
            lines.append(
                f"- {item.get('category')}: "
                f"{json.dumps(item.get('facts') or {}, ensure_ascii=False)}"
            )
    return "\n".join(lines).rstrip() + "\n"


def strategy_v2_fingerprint(
    packet: dict[str, Any],
    *,
    llm_provider: str,
    llm_model: str,
    decision_horizon_profile: str = DEFAULT_DECISION_HORIZON_PROFILE,
    generation_payload: dict[str, Any] | None = None,
    generation_prompt: str | None = None,
) -> str:
    profile = resolve_decision_horizon_profile(decision_horizon_profile)
    payload = {
        "cache_version": STRATEGY_CACHE_VERSION,
        "contract_version": DECISION_VERSION,
        "packet": packet,
        "generation_payload": generation_payload or {"strategy_compact_packet_v2": packet},
        "decision_horizon_profile": decision_horizon_profile,
        "prompt": generation_prompt or decision_prompt_v2(decision_horizon_profile),
        "response_format": strategy_decision_response_format_v2(
            packet,
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
    llm_provider: str = "openai",
    llm_model: str = "auto",
    llm_timeout: int = 120,
    env_file: Path | None = DEFAULT_ENV_FILE,
    packet_version: str | None = None,
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
        "financial": financial_report or output_root / "Financial" / target_run_key / "final_report.json",
        "news": news_report or output_root / "News" / target_run_key / "final_report.json",
        "yfinance": yfinance_report or output_root / "Y_Finance" / target_run_key / "final_report.json",
    }
    output_dir = (output_json.parent if output_json else output_md.parent if output_md else output_root / "Strategy" / target_run_key)
    report = run_strategy_agent(
        target_company_name=target_company_name,
        target_run_key=target_run_key,
        target_financial_path=paths["financial"],
        target_news_path=paths["news"],
        target_yfinance_path=paths["yfinance"],
        output_dir=output_dir,
        peer_comparison_path=peer_comparison,
        llm_provider=llm_provider,
        llm_model=llm_model,
        llm_timeout=llm_timeout,
        env_file=env_file,
        packet_version=packet_version,
        ablation_config=ablation_config,
        decision_horizon_profile=decision_horizon_profile,
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
    peer_comparison_path: Path | None = None,
    ablation_config: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Create the exact input bundle read by the two Strategy Agent LLM steps."""

    target_financial_path = target_financial_path.expanduser().resolve()
    target_news_path = target_news_path.expanduser().resolve()
    target_yfinance_path = target_yfinance_path.expanduser().resolve()
    target_financial_path = resolve_preferred_report_path(target_financial_path, "financial")
    target_news_path = resolve_preferred_report_path(target_news_path, "news")
    target_yfinance_path = resolve_preferred_report_path(target_yfinance_path, "yfinance")

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
    financial_validation = (
        load_optional_validation_evidence(target_financial_path, "financial")
        if "financial" in ablation.included_domains
        else {"source_path": "", "summary": {}, "claims": []}
    )
    news_validation = (
        load_optional_validation_evidence(target_news_path, "news")
        if "news" in ablation.included_domains
        else {"source_path": "", "summary": {}, "claims": []}
    )
    yfinance_validation = (
        load_optional_validation_evidence(target_yfinance_path, "yfinance")
        if "yfinance" in ablation.included_domains
        else {"source_path": "", "summary": {}, "claims": []}
    )
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
            "created_at": datetime.now().isoformat(timespec="seconds"),
        },
    }


def build_strategy_llm_packet(input_bundle: dict[str, Any]) -> dict[str, Any]:
    """Build the referenced-only packet sent to Strategy LLM calls."""

    global_catalog = _merge_strategy_evidence_catalogs(input_bundle)
    claim_ledger: dict[str, list[dict[str, Any]]] = {}
    referenced_ids: set[str] = set()
    excluded_counts: dict[str, int] = {}
    seen_statements: dict[str, dict[str, Any]] = {}
    for domain in ("financial", "news", "yfinance"):
        validation = (input_bundle.get("target_validation_evidence") or {}).get(domain) or {}
        rows: list[dict[str, Any]] = []
        excluded_counts[domain] = 0
        for claim in ensure_list(validation.get("claims")):
            if not isinstance(claim, dict):
                continue
            evidence_use = clean_text(claim.get("evidence_use")).lower()
            if evidence_use == "exclude":
                excluded_counts[domain] += 1
                continue
            statement = clean_text(claim.get("claim"))
            claim_id = clean_text(claim.get("claim_id"))
            if not statement or not claim_id:
                continue
            evidence_ids = [
                evidence_id
                for evidence_id in dedupe(text_items(claim.get("evidence_ids")), 32)
                if evidence_id in global_catalog
            ]
            referenced_ids.update(evidence_ids)
            normalized_statement = re.sub(r"\s+", " ", statement).strip().lower()
            duplicate = seen_statements.get(normalized_statement)
            if duplicate is not None:
                duplicate["source_claim_ids"] = dedupe(
                    text_items(duplicate.get("source_claim_ids")) + [claim_id],
                    32,
                )
                duplicate["primary_evidence_ids"] = dedupe(
                    text_items(duplicate.get("primary_evidence_ids")) + evidence_ids,
                    32,
                )
                duplicate["limitations"] = dedupe(
                    text_items(duplicate.get("limitations")) + text_items(claim.get("limitations")),
                    32,
                )
                if evidence_use == "strong":
                    duplicate["evidence_use"] = "strong"
                continue
            row = {
                "claim_id": claim_id,
                "source_claim_ids": [claim_id],
                "domain": domain,
                "statement": statement,
                "claim_kind": clean_text(claim.get("claim_kind")) or "interpretation",
                "evidence_use": evidence_use or "context_only",
                "primary_evidence_ids": evidence_ids,
                "limitations": text_items(claim.get("limitations")),
            }
            rows.append(row)
            seen_statements[normalized_statement] = row
        claim_ledger[domain] = rows

    context_assessments = _collect_strategy_context_assessments(input_bundle, global_catalog)
    for item in context_assessments:
        referenced_ids.update(item["primary_evidence_ids"])
        referenced_ids.update(item["secondary_evidence_ids"])

    evidence_catalog = {
        evidence_id: global_catalog[evidence_id]
        for evidence_id in sorted(referenced_ids)
        if evidence_id in global_catalog
    }
    limitations = build_strategy_limitation_catalog(
        claim_ledger,
        input_bundle.get("decision_constraints") or [],
        input_bundle.get("peer_comparison") or {},
    )
    llm_claim_ledger = {
        domain: [
            {
                key: deepcopy(value)
                for key, value in claim.items()
                if key not in {"domain", "limitations"}
            }
            for claim in claims
        ]
        for domain, claims in claim_ledger.items()
    }
    peer_metric_catalog = build_peer_metric_catalog(input_bundle.get("peer_comparison") or {})
    reports = input_bundle.get("target_reports") or {}
    packet = {
        "agent_name": "Strategy Agent",
        "packet_version": "2.0",
        "target_company": deepcopy(input_bundle.get("target_company") or {}),
        "claim_ledger": llm_claim_ledger,
        "evidence_catalog": evidence_catalog,
        "secondary_context_assessments": context_assessments,
        "structured_facts": {
            "financial": compact_strategy_financial_facts(reports.get("financial") or {}),
            "valuation": compact_strategy_valuation(reports.get("yfinance") or {}),
        },
        "peer_metric_catalog": peer_metric_catalog,
        "peer_context": {
            "comparison_limits": text_items((input_bundle.get("peer_comparison") or {}).get("comparison_limits")),
            "data_quality": [
                {
                    "company_name": clean_text(company.get("company_name")),
                    "missing_fields": text_items(
                        get_path(company, ["data_quality", "missing_fields"])
                    ),
                }
                for company in ensure_list((input_bundle.get("peer_comparison") or {}).get("metrics"))
                if isinstance(company, dict)
                and text_items(get_path(company, ["data_quality", "missing_fields"]))
            ],
        },
        "limitations": limitations,
        "decision_constraints": deepcopy(input_bundle.get("decision_constraints") or []),
        "coverage_summary": {
            "admissible_claim_counts": {domain: len(rows) for domain, rows in llm_claim_ledger.items()},
            "excluded_claim_counts": excluded_counts,
            "secondary_context_count": len(context_assessments),
            "referenced_evidence_count": len(evidence_catalog),
        },
    }
    validate_strategy_llm_packet(packet)
    return packet


def _merge_strategy_evidence_catalogs(input_bundle: dict[str, Any]) -> dict[str, dict[str, Any]]:
    catalog: dict[str, dict[str, Any]] = {}
    sources: list[dict[str, Any]] = [
        value
        for value in (input_bundle.get("evidence_catalogs") or {}).values()
        if isinstance(value, dict)
    ]
    for report in (input_bundle.get("target_reports") or {}).values():
        if not isinstance(report, dict):
            continue
        for key in ("primary_evidence_catalog", "secondary_context_catalog"):
            value = report.get(key)
            if isinstance(value, dict):
                sources.append(value)
        for context in (report.get("secondary_context") or {}).values():
            if isinstance(context, dict) and isinstance(context.get("evidence_catalog"), dict):
                sources.append(context["evidence_catalog"])
        output = report.get("output") if isinstance(report.get("output"), dict) else {}
        for key in ("primary_evidence_catalog", "secondary_context_catalog"):
            value = output.get(key)
            if isinstance(value, dict):
                sources.append(value)
    for source in sources:
        for raw_id, raw_evidence in source.items():
            if not isinstance(raw_evidence, dict):
                continue
            evidence_id = clean_text(raw_evidence.get("evidence_id")) or clean_text(raw_id)
            if not evidence_id:
                continue
            evidence = normalize_strategy_evidence(evidence_id, raw_evidence)
            existing = catalog.get(evidence_id)
            if existing is None or len(compact_json(evidence)) > len(compact_json(existing)):
                catalog[evidence_id] = evidence
    return catalog


def normalize_strategy_evidence(evidence_id: str, evidence: dict[str, Any]) -> dict[str, Any]:
    domain = clean_text(evidence.get("domain") or evidence.get("source_domain"))
    if not domain:
        domain = "news" if evidence_id.startswith("NEWS_") else "financial" if evidence_id.startswith(("DART_", "E")) else "market"
    normalized = {
        "domain": domain,
        "source_ref": clean_text(evidence.get("source_ref")) or f"normalized_catalog.{evidence_id}",
        "source_date": clean_text(evidence.get("source_date") or evidence.get("time") or evidence.get("date")),
        "period": clean_text(evidence.get("period")),
        "metric": clean_text(evidence.get("metric") or evidence.get("metric_or_event")),
        "unit": clean_text(evidence.get("unit")),
    }
    if "value" in evidence:
        normalized["value"] = deepcopy(evidence.get("value"))
    text = clean_text(evidence.get("text") or evidence.get("title"))
    if text:
        normalized["text"] = text
    return {key: value for key, value in normalized.items() if value not in (None, "", [], {})}


def _collect_strategy_context_assessments(
    input_bundle: dict[str, Any],
    evidence_catalog: dict[str, dict[str, Any]],
) -> list[dict[str, Any]]:
    assessments: list[dict[str, Any]] = []
    reports = input_bundle.get("target_reports") or {}
    for origin_agent, report in reports.items():
        if not isinstance(report, dict):
            continue
        output = report.get("output") if isinstance(report.get("output"), dict) else report
        values = output.get("secondary_context_assessment") or report.get("secondary_context_assessment") or []
        for raw in ensure_list(values):
            if not isinstance(raw, dict):
                continue
            primary_ids = [value for value in text_items(raw.get("primary_evidence_ids")) if value in evidence_catalog]
            secondary_ids = [value for value in text_items(raw.get("secondary_evidence_ids")) if value in evidence_catalog]
            if not primary_ids or not clean_text(raw.get("statement")):
                continue
            assessments.append(
                {
                    "context_id": f"CTX_{origin_agent.upper()}_{len(assessments) + 1:03d}",
                    "origin_agent": origin_agent,
                    "source_domain": clean_text(raw.get("source_domain")),
                    "effect": clean_text(raw.get("effect")),
                    "statement": clean_text(raw.get("statement")),
                    "primary_evidence_ids": primary_ids,
                    "secondary_evidence_ids": secondary_ids,
                    "usage": "framing_and_limitation_only",
                    "limitation": clean_text(raw.get("limitation")),
                }
            )
    return assessments


def compact_strategy_financial_facts(report: dict[str, Any]) -> dict[str, Any]:
    collection = report.get("collection_context") or {}
    latest = collection.get("latest_available_filing") or {}
    theoretical = collection.get("theoretical_target") or {}
    trends = report.get("financial_trends") or {}
    current_comparison = trends.get("current_vs_same_period") or {}
    annual_history = []
    for item in ensure_list(trends.get("annual_history")):
        if not isinstance(item, dict):
            continue
        annual_history.append(
            {
                "period": _compact_financial_period(item.get("period") or {}),
                "values": deepcopy(item.get("values") or {}),
            }
        )
    ttm = trends.get("ttm") or {}
    facts = {
        "filing_basis": {
            "selected_date": collection.get("selected_date"),
            "latest_available": _compact_financial_period(latest),
            "theoretical_target": _compact_financial_period(theoretical),
            "fallback_applied": collection.get("fallback_applied"),
        },
        "current_vs_same_period": {
            "current_period": _compact_financial_period(current_comparison.get("current_period") or {}),
            "previous_period": _compact_financial_period(current_comparison.get("previous_period") or {}),
            "current_values": deepcopy(current_comparison.get("current_values") or {}),
            "previous_values": deepcopy(current_comparison.get("previous_values") or {}),
        },
        "annual_history": annual_history,
        "ttm": {
            "period": _compact_financial_period(ttm.get("period") or {}),
            "values": deepcopy(ttm.get("values") or {}),
        },
        "revenue_breakdown": _compact_revenue_breakdown(report.get("revenue_breakdown") or {}),
        "share_information": {
            key: deepcopy((report.get("share_information") or {}).get(key))
            for key in (
                "status",
                "as_of_date",
                "issued_shares",
                "treasury_shares",
                "shares_outstanding",
            )
            if (report.get("share_information") or {}).get(key) is not None
        },
    }
    return {key: value for key, value in facts.items() if value not in (None, "", [], {})}


def _compact_financial_period(period: dict[str, Any]) -> dict[str, Any]:
    return {
        key: deepcopy(period.get(key))
        for key in ("fiscal_year", "period_type", "period_end", "basis")
        if period.get(key) not in (None, "", [], {})
    }


def _compact_revenue_breakdown(revenue: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(revenue, dict) or revenue.get("status") != "available":
        return {"status": clean_text(revenue.get("status")) or "unavailable"}
    periods = [
        {
            "period_key": clean_text(period.get("period_key")),
            **_compact_financial_period(period),
        }
        for period in ensure_list(revenue.get("periods"))
        if isinstance(period, dict)
    ]
    items = []
    for item in ensure_list(revenue.get("items")):
        if not isinstance(item, dict):
            continue
        values = {
            str(period_key): {
                key: deepcopy(period_value.get(key))
                for key in ("revenue_krw", "revenue_share")
                if period_value.get(key) is not None
            }
            for period_key, period_value in (item.get("values_by_period") or {}).items()
            if isinstance(period_value, dict)
        }
        items.append({"name": clean_text(item.get("name")), "values_by_period": values})
    return {
        "status": "available",
        "periods": periods,
        "current_period_key": clean_text(revenue.get("current_period_key")),
        "items": items,
    }


def compact_strategy_valuation(report: dict[str, Any]) -> dict[str, Any]:
    valuation = report.get("valuation_snapshot") or {}
    if not isinstance(valuation, dict):
        return {}
    direct = valuation.get("direct_yfinance") or valuation
    latest = direct.get("latest_period") if isinstance(direct, dict) else {}
    return {
        "status": valuation.get("status") or direct.get("status"),
        "selected_date": valuation.get("selected_date") or direct.get("selected_date"),
        "market_date": valuation.get("market_date"),
        "latest_period": deepcopy(latest or {}),
        "data_limits": text_items(direct.get("data_limits")) if isinstance(direct, dict) else [],
    }


def build_peer_metric_catalog(peer_comparison: dict[str, Any]) -> dict[str, Any]:
    catalog: dict[str, Any] = {}
    for company in ensure_list(peer_comparison.get("metrics")):
        if not isinstance(company, dict):
            continue
        identity = {
            "company_name": clean_text(company.get("company_name")),
            "peer_group": clean_text(company.get("peer_group")),
        }
        for key, value in flatten_scalar_values(company):
            if key in {"company_name", "peer_group", "run_key", "ticker"}:
                continue
            if _is_peer_metadata_path(key):
                continue
            metric_id = f"PEER_METRIC_{len(catalog) + 1:03d}"
            catalog[metric_id] = {**identity, "metric_path": key, "value": value}
    return catalog


def _is_peer_metadata_path(path: str) -> bool:
    return (
        path == "as_of_date"
        or path.startswith("data_quality.")
        or path.endswith(("_basis", "_period", "_date"))
        or path.endswith(".market_date")
    )


def flatten_scalar_values(value: Any, prefix: str = "") -> list[tuple[str, Any]]:
    rows: list[tuple[str, Any]] = []
    if isinstance(value, dict):
        for key, child in value.items():
            path = f"{prefix}.{key}" if prefix else key
            rows.extend(flatten_scalar_values(child, path))
    elif value not in (None, "", [], {}):
        rows.append((prefix, value))
    return rows


def build_strategy_limitation_catalog(
    claim_ledger: dict[str, list[dict[str, Any]]],
    decision_constraints: list[Any],
    peer_comparison: dict[str, Any],
) -> dict[str, Any]:
    values: list[tuple[str, str]] = []
    for domain, claims in claim_ledger.items():
        for claim in claims:
            values.extend((f"claim:{domain}:{claim['claim_id']}", text) for text in text_items(claim.get("limitations")))
    values.extend(("decision_constraint", clean_text(value)) for value in decision_constraints if clean_text(value))
    values.extend(("peer_comparison", text) for text in text_items(peer_comparison.get("comparison_limits")))
    catalog: dict[str, Any] = {}
    seen: set[str] = set()
    for source, text in values:
        normalized = re.sub(r"\s+", " ", text).strip().lower()
        if not normalized or normalized in seen:
            continue
        seen.add(normalized)
        limit_id = f"LIMIT_{len(catalog) + 1:03d}"
        catalog[limit_id] = {"source": source, "text": text}
    return catalog


def validate_strategy_llm_packet(packet: dict[str, Any]) -> None:
    require_dict(packet, "strategy_llm_packet")
    if "input_metadata" in packet or "target_reports" in packet or "target_validation_evidence" in packet:
        raise ValueError("Strategy LLM packet contains audit-only fields.")
    catalog = require_dict(packet.get("evidence_catalog"), "evidence_catalog")
    referenced: set[str] = set()
    statements: list[str] = []
    for domain, claims in require_dict(packet.get("claim_ledger"), "claim_ledger").items():
        if not isinstance(claims, list):
            raise ValueError(f"claim_ledger.{domain} must be a list.")
        for claim in claims:
            statement = clean_text(claim.get("statement"))
            if statement:
                statements.append(statement)
            for evidence_id in text_items(claim.get("primary_evidence_ids")):
                if evidence_id not in catalog:
                    raise ValueError(f"Unknown claim evidence ID: {evidence_id}")
                referenced.add(evidence_id)
    for context in ensure_list(packet.get("secondary_context_assessments")):
        if context.get("usage") != "framing_and_limitation_only":
            raise ValueError("Invalid Strategy secondary context usage.")
        for evidence_id in text_items(context.get("primary_evidence_ids")) + text_items(context.get("secondary_evidence_ids")):
            if evidence_id not in catalog:
                raise ValueError(f"Unknown context evidence ID: {evidence_id}")
            referenced.add(evidence_id)
    if set(catalog) != referenced:
        raise ValueError("Strategy evidence_catalog must contain referenced evidence only.")
    if len(statements) != len(set(statements)):
        raise ValueError("Strategy packet contains duplicate claim statements.")
    serialized = compact_json(packet)
    if re.search(r'"/(?:home|tmp|var|Users)/', serialized):
        raise ValueError("Strategy LLM packet contains an absolute path.")


def build_strategy_decision_packet(
    llm_packet: dict[str, Any],
    content_plan: dict[str, Any],
) -> dict[str, Any]:
    """Materialize only Planner-selected ledgers for the Decision call."""

    selected_claim_ids = {
        value
        for key in (
            "positive_claim_ids",
            "negative_claim_ids",
            "neutral_claim_ids",
            "catalyst_claim_ids",
            "risk_claim_ids",
        )
        for value in text_items(content_plan.get(key))
    }
    selected_context_ids = set(text_items(content_plan.get("context_assessment_ids")))
    selected_peer_ids = set(text_items(content_plan.get("peer_metric_ids")))
    selected_limit_ids = set(text_items(content_plan.get("limitation_ids")))
    for refs in (content_plan.get("section_plan") or {}).values():
        for value in text_items(refs):
            if value.startswith("CTX_"):
                selected_context_ids.add(value)
            elif value.startswith("PEER_METRIC_"):
                selected_peer_ids.add(value)
            elif value.startswith("LIMIT_"):
                selected_limit_ids.add(value)
            else:
                selected_claim_ids.add(value)

    claim_ledger = {
        domain: [
            deepcopy(claim)
            for claim in claims
            if claim.get("claim_id") in selected_claim_ids
        ]
        for domain, claims in (llm_packet.get("claim_ledger") or {}).items()
    }
    contexts = [
        deepcopy(item)
        for item in ensure_list(llm_packet.get("secondary_context_assessments"))
        if item.get("context_id") in selected_context_ids
    ]
    referenced_ids = {
        evidence_id
        for claims in claim_ledger.values()
        for claim in claims
        for evidence_id in text_items(claim.get("primary_evidence_ids"))
    }
    for item in contexts:
        referenced_ids.update(text_items(item.get("primary_evidence_ids")))
        referenced_ids.update(text_items(item.get("secondary_evidence_ids")))
    source_catalog = llm_packet.get("evidence_catalog") or {}
    packet = {
        "agent_name": "Strategy Agent",
        "packet_version": "2.1-decision",
        "target_company": deepcopy(llm_packet.get("target_company") or {}),
        "claim_ledger": claim_ledger,
        "evidence_catalog": {
            evidence_id: deepcopy(source_catalog[evidence_id])
            for evidence_id in sorted(referenced_ids)
            if evidence_id in source_catalog
        },
        "secondary_context_assessments": contexts,
        "structured_facts": deepcopy(llm_packet.get("structured_facts") or {}),
        "peer_metric_catalog": {
            metric_id: deepcopy(metric)
            for metric_id, metric in (llm_packet.get("peer_metric_catalog") or {}).items()
            if metric_id in selected_peer_ids
        },
        "peer_context": deepcopy(llm_packet.get("peer_context") or {}),
        "limitations": {
            limit_id: deepcopy(item)
            for limit_id, item in (llm_packet.get("limitations") or {}).items()
            if limit_id in selected_limit_ids
        },
        "decision_constraints": deepcopy(llm_packet.get("decision_constraints") or []),
        "section_plan": deepcopy(content_plan.get("section_plan") or {}),
        "coverage_summary": deepcopy(llm_packet.get("coverage_summary") or {}),
    }
    validate_strategy_llm_packet(packet)
    return packet


def load_news_evidence_catalog(report: dict[str, Any], report_path: Path) -> dict[str, Any]:
    """Load the News evidence catalog referenced by its verified handoff."""

    output = report.get("output") if isinstance(report.get("output"), dict) else report
    path_value = output.get("evidence_map_path") if isinstance(output, dict) else None
    if not path_value:
        return {}
    path = Path(str(path_value)).expanduser()
    if not path.is_absolute():
        path = report_path.parent / path
    if not path.exists():
        return {}
    payload = load_json(path)
    return payload if isinstance(payload, dict) else {}


def load_financial_evidence_catalog(report: dict[str, Any]) -> dict[str, Any]:
    """Normalize Financial key evidence without its generated interpretation text."""

    catalog: dict[str, Any] = {}
    source_date = str(report.get("as_of_date") or "")
    handoff = report.get("sy_handoff") or {}
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


def run_content_planner(
    llm_packet: dict[str, Any],
    *,
    llm_provider: str,
    llm_model: str,
    llm_timeout: int,
) -> dict[str, Any]:
    """Call the Content Planner LLM and return its JSON plan."""

    prompt = read_prompt("content_planner.md")
    return call_llm_json(
        prompt=prompt,
        payload={"strategy_llm_packet": llm_packet},
        llm_provider=llm_provider,
        llm_model=llm_model,
        llm_timeout=llm_timeout,
        system_message="You are a financial Strategy Agent Content Planner. Return only valid JSON.",
        response_format=content_plan_response_format(llm_packet),
    )


def run_decision_agent(
    decision_packet: dict[str, Any],
    content_plan: dict[str, Any],
    *,
    llm_provider: str,
    llm_model: str,
    llm_timeout: int,
) -> dict[str, Any]:
    """Call the Decision Agent LLM and return its JSON report."""

    prompt = (
        read_prompt("decision_agent.md")
        + "\n\nRuntime output-size constraint:\n"
        + "- Return exactly one top-level JSON object with keys \"strategy_report\" and \"evidence_refs_by_section\".\n"
        + "- evidence_refs_by_section must include every required top-level report section listed in the output schema.\n"
        + "- Keep evidence refs compact; do not repeat report prose in the refs map.\n"
        + "- Keep the report compact enough to remain valid JSON.\n"
    )
    payload = {
        "strategy_decision_packet": decision_packet,
        "strategy_content_plan": {
            key: deepcopy(content_plan.get(key) or [])
            for key in (
                "positive_claim_ids",
                "negative_claim_ids",
                "neutral_claim_ids",
                "catalyst_claim_ids",
                "risk_claim_ids",
            )
        },
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


def run_decision_agent_v2(
    compact_packet: dict[str, Any],
    *,
    llm_provider: str,
    llm_model: str,
    llm_timeout: int,
    decision_horizon_profile: str = DEFAULT_DECISION_HORIZON_PROFILE,
    generation_payload: dict[str, Any] | None = None,
    generation_prompt: str | None = None,
) -> dict[str, Any]:
    """Call the single v2 Decision LLM with self-contained cards only."""

    profile = resolve_decision_horizon_profile(decision_horizon_profile)
    output = call_llm_json(
        prompt=generation_prompt or decision_prompt_v2(decision_horizon_profile),
        payload=generation_payload or {"strategy_compact_packet_v2": compact_packet},
        llm_provider=llm_provider,
        llm_model=llm_model,
        llm_timeout=llm_timeout,
        system_message="You are a financial Strategy Decision Agent v2. Return only valid JSON.",
        response_format=strategy_decision_response_format_v2(
            compact_packet,
            required_horizon=str(profile["horizon"]),
        ),
    )
    finalized = finalize_strategy_decision_v2(output, compact_packet)
    decision = finalized.get("decision")
    actual_horizon = str(
        decision.get("horizon") if isinstance(decision, dict) else ""
    )
    if actual_horizon != profile["horizon"]:
        raise ValueError(
            f"Strategy decision horizon mismatch: expected={profile['horizon']}, "
            f"actual={actual_horizon}"
        )
    return finalized


def build_strategy_generation_payload_v2(
    *,
    input_bundle: dict[str, Any],
    compact_packet: dict[str, Any],
    context_mode: str,
) -> dict[str, Any]:
    """Build the exact Decision input for compact and full-context ablations."""

    if context_mode == "compact_cards":
        return {"strategy_compact_packet_v2": compact_packet}
    if context_mode != "full_reports":
        raise ValueError(f"Unknown Strategy context mode: {context_mode}")
    return {
        "strategy_compact_packet_v2": compact_packet,
        "full_context_ablation": {
            "target_company": deepcopy(input_bundle.get("target_company") or {}),
            "target_reports": deepcopy(input_bundle.get("target_reports") or {}),
            "target_validation_evidence": deepcopy(
                input_bundle.get("target_validation_evidence") or {}
            ),
            "peer_comparison": deepcopy(input_bundle.get("peer_comparison") or {}),
            "decision_constraints": deepcopy(input_bundle.get("decision_constraints") or []),
        },
    }


def decision_generation_prompt_v2(
    decision_horizon_profile: str,
    *,
    context_mode: str,
) -> str:
    """Render the production prompt plus one explicit structural-ablation policy."""

    prompt = decision_prompt_v2(decision_horizon_profile)
    if context_mode == "compact_cards":
        return prompt
    if context_mode != "full_reports":
        raise ValueError(f"Unknown Strategy context mode: {context_mode}")
    return (
        prompt
        + "\n\n## Full-context ablation\n"
        + "이번 실험에서는 compact card뿐 아니라 sanitized upstream domain report와 validation ledger도 "
        + "추가 문맥으로 제공된다. 모든 출력 key와 근거 연결은 여전히 strategy_compact_packet_v2.cards에 "
        + "한정한다. 추가 문맥에서만 보이는 사실·수치·인과관계를 출력에 새로 만들지 말고, 같은 사실을 "
        + "여러 domain에서 보았더라도 독립 근거로 중복 계산하지 않는다."
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
        "peer_comparison": metadata.get("peer_comparison_path", ""),
    }

    if not isinstance(report.get("final_recommendation"), dict):
        raise ValueError("strategy_report must use the current structured final_recommendation schema.")
    normalized = normalize_structured_strategy_report(report)

    normalized["agent_name"] = "Strategy Agent"
    normalized["target_company_name"] = target["company_name"]
    normalized["target_run_key"] = target["run_key"]
    normalized["source_files"] = source_files
    normalized["opinion_index"] = build_report_opinion_index(normalized)
    normalized.setdefault("created_at", datetime.now().isoformat(timespec="seconds"))
    normalized.setdefault("output_version", OUTPUT_VERSION)
    return normalized


def normalize_strategy_decision_output(output: dict[str, Any], input_bundle: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    """Normalize the Decision Agent output into report plus path-level basis card."""

    raw_report, raw_basis = split_strategy_decision_output(output)
    strategy_report = normalize_strategy_report(raw_report, input_bundle)
    basis_packet = (
        build_strategy_llm_packet(input_bundle)
        if "target_reports" in input_bundle
        else input_bundle
    )
    decision_basis_by_section = normalize_decision_basis_by_section(
        raw_basis,
        strategy_report,
        basis_packet,
    )
    return strategy_report, decision_basis_by_section


def split_strategy_decision_output(output: dict[str, Any]) -> tuple[dict[str, Any], Any]:
    """Split the integrated report and source-reference map."""

    if not isinstance(output, dict):
        raise ValueError("strategy decision output must be an object.")
    if isinstance(output.get("strategy_report"), dict):
        return output["strategy_report"], first_non_empty_object(
            output.get("evidence_refs_by_section"),
            output.get("decision_basis_by_section"),
            output.get("basis_by_section"),
            output.get("decision_basis_card_by_section"),
        )
    legacy_report = {
        key: value
        for key, value in output.items()
        if key not in {
            "evidence_refs_by_section",
            "decision_basis_by_section",
            "basis_by_section",
            "decision_basis_card_by_section",
        }
    }
    return legacy_report, first_non_empty_object(
        output.get("evidence_refs_by_section"),
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


def normalize_content_plan(content_plan: dict[str, Any]) -> dict[str, Any]:
    """Coerce common LLM shape drift in the intermediate content plan."""

    if not isinstance(content_plan, dict):
        return content_plan
    plan = dict(content_plan)
    for key in (
        "positive_claim_ids",
        "negative_claim_ids",
        "neutral_claim_ids",
        "catalyst_claim_ids",
        "risk_claim_ids",
        "context_assessment_ids",
        "peer_metric_ids",
        "limitation_ids",
    ):
        if not isinstance(plan.get(key), list):
            plan[key] = text_items(plan.get(key))
    if not isinstance(plan.get("section_plan"), dict):
        plan["section_plan"] = {}
    else:
        plan["section_plan"] = {
            str(key): text_items(value)
            for key, value in plan["section_plan"].items()
        }
    return plan


def normalize_decision_basis_by_section(
    raw_basis: Any,
    strategy_report: dict[str, Any],
    input_bundle: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Normalize source refs produced in the same call as the Strategy report."""

    raw_map = flatten_decision_reference_map(unwrap_decision_basis_map(raw_basis))
    if not raw_map:
        raise ValueError("evidence_refs_by_section is required from the Decision Agent output.")
    opinion_ids = {
        clean_text(item.get("section_path")): clean_text(item.get("id"))
        for item in ensure_list(strategy_report.get("opinion_index"))
        if isinstance(item, dict)
    }
    basis_map: dict[str, Any] = {}
    missing_paths: list[str] = []
    for section_path, opinion_text in iter_editable_report_opinions(strategy_report):
        if not opinion_text:
            continue
        raw_entry = (
            raw_map.get(section_path)
            or raw_map.get(opinion_ids.get(section_path, ""))
            or nearest_section_reference(raw_map, section_path)
            or final_rationale_reference(raw_map, section_path)
        )
        if not raw_entry:
            missing_paths.append(section_path)
            continue
        basis_map[section_path] = normalize_decision_basis_entry(
            raw_entry,
            section_path=section_path,
            opinion_id=opinion_ids.get(section_path, ""),
            opinion_text=opinion_text,
            input_bundle=input_bundle,
        )
    if missing_paths:
        raise ValueError(
            "decision_basis_by_section missing path(s): "
            f"{missing_paths}; available reference keys: {sorted(raw_map)[:30]}"
        )
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
            raw_basis.get("evidence_refs_by_section"),
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


def flatten_decision_reference_map(raw_map: dict[str, Any], prefix: str = "") -> dict[str, Any]:
    """Flatten a nested report-shaped refs object into section-path entries."""

    flattened: dict[str, Any] = {}
    for key, value in raw_map.items():
        if key in {"strategy_report", "evidence_refs_by_section"} and isinstance(value, dict):
            flattened.update(flatten_decision_reference_map(value, prefix))
            continue
        path = f"{prefix}.{key}" if prefix else clean_text(key)
        if path.startswith("strategy_report."):
            path = path.removeprefix("strategy_report.")
        if is_decision_reference_entry(value):
            flattened[path] = value
        elif isinstance(value, dict):
            flattened.update(flatten_decision_reference_map(value, path))
    return flattened


def content_plan_fingerprint(
    evidence_packet: dict[str, Any],
    *,
    llm_provider: str,
    llm_model: str,
) -> str:
    provider = resolve_llm_provider(llm_provider)
    model = resolve_llm_model(provider, llm_model)
    payload = {
        "packet": evidence_packet,
        "prompt": read_prompt("content_planner.md"),
        "response_format": content_plan_response_format(evidence_packet),
        "provider": provider,
        "model": model,
    }
    return hashlib.sha256(compact_json(payload, sort_keys=True).encode("utf-8")).hexdigest()


def load_cached_content_plan(
    content_plan_path: Path,
    cache_path: Path,
    expected_fingerprint: str,
) -> dict[str, Any] | None:
    if not content_plan_path.exists() or not cache_path.exists():
        return None
    try:
        cache = load_json(cache_path)
        if cache.get("fingerprint") != expected_fingerprint:
            return None
        plan = normalize_content_plan(load_json(content_plan_path))
        validate_content_plan(plan)
    except (OSError, ValueError, json.JSONDecodeError):
        return None
    return plan


def decision_fingerprint(
    evidence_packet: dict[str, Any],
    content_plan: dict[str, Any],
    *,
    llm_provider: str,
    llm_model: str,
) -> str:
    provider = resolve_llm_provider(llm_provider)
    model = resolve_llm_model(provider, llm_model)
    payload = {
        "packet": evidence_packet,
        "content_plan": content_plan,
        "prompt": read_prompt("decision_agent.md"),
        "schema": strategy_decision_output_schema(),
        "provider": provider,
        "model": model,
    }
    return hashlib.sha256(compact_json(payload, sort_keys=True).encode("utf-8")).hexdigest()


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


def is_decision_reference_entry(value: Any) -> bool:
    if isinstance(value, list):
        return True
    if not isinstance(value, dict):
        return False
    return any(
        key in value
        for key in (
            "source_evidence",
            "evidence_refs",
            "sources",
            "source_section",
            "claim_id",
            "evidence_ids",
            "basis_summary",
        )
    )


def nearest_section_reference(raw_map: dict[str, Any], section_path: str) -> Any:
    """Return the nearest ancestor refs when the model cited a whole report section."""

    candidates = [
        (path, value)
        for path, value in raw_map.items()
        if section_path == path or section_path.startswith(path + ".") or section_path.startswith(path + "[")
    ]
    if not candidates:
        return None
    return max(candidates, key=lambda item: len(item[0]))[1]


def final_rationale_reference(raw_map: dict[str, Any], section_path: str) -> Any:
    """Reuse final decision refs when a duplicate final-rationale ref is omitted."""

    if not section_path.startswith("final_rationale."):
        return None
    return raw_map.get("decision_balance") or raw_map.get("final_recommendation")


def normalize_decision_basis_entry(
    raw_entry: Any,
    *,
    section_path: str,
    opinion_id: str,
    opinion_text: str,
    input_bundle: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Normalize one source-reference entry without generating analytical prose."""

    if isinstance(raw_entry, list):
        payload: dict[str, Any] = {"source_evidence": raw_entry}
    elif isinstance(raw_entry, dict):
        payload = raw_entry
    else:
        payload = {}
    source_evidence = first_non_empty_object(
        payload.get("source_evidence"),
        payload.get("evidence_refs"),
        payload.get("sources"),
    )
    if not source_evidence and any(
        payload.get(key) for key in ("source_section", "claim_id", "evidence_ids")
    ):
        source_evidence = [payload]
    return {
        "opinion_id": clean_text(payload.get("opinion_id")) or opinion_id,
        "section_path": section_path,
        "opinion_text": opinion_text,
        "source_evidence": normalize_basis_source_evidence(
            source_evidence,
            input_bundle=input_bundle,
        )[:2],
    }


def normalize_basis_source_evidence(
    value: Any,
    *,
    input_bundle: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
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
            agent = clean_text(item.get("agent"))
            claim_id = clean_text(item.get("claim_id"))
            limitation_source_section = ""
            if (
                input_bundle is not None
                and claim_id
                and claim_id in (input_bundle.get("limitations") or {})
            ):
                limitation_source_section = f"limitations.{claim_id}"
            if is_strategy_opinion_id(claim_id) or is_empty_source_identifier(claim_id):
                claim_id = ""
            evidence_ids = [evidence_id for evidence_id in evidence_ids if not is_strategy_opinion_id(evidence_id)]
            if input_bundle is not None and isinstance(input_bundle.get("evidence_catalog"), dict):
                evidence_ids = [
                    evidence_id
                    for evidence_id in evidence_ids
                    if evidence_id in input_bundle["evidence_catalog"]
                ]
                known_claim_ids = {
                    clean_text(claim.get("claim_id"))
                    for claims in (input_bundle.get("claim_ledger") or {}).values()
                    for claim in ensure_list(claims)
                    if isinstance(claim, dict)
                }
                if claim_id and claim_id not in known_claim_ids:
                    claim_id = ""
            source_path = first_non_empty(item.get("source_path"), item.get("path"))
            source_section = normalize_basis_source_section(
                first_non_empty(item.get("source_section"), item.get("section"), item.get("section_path")),
                agent=agent,
                input_bundle=input_bundle,
            )
            if not source_section:
                source_section = normalize_basis_source_section(
                    source_path,
                    agent=agent,
                    input_bundle=input_bundle,
                )
            if limitation_source_section and source_section in {"", "limitations", "decision_constraints"}:
                source_section = normalize_basis_source_section(
                    limitation_source_section,
                    agent=agent,
                    input_bundle=input_bundle,
                )
            if not source_section:
                source_section = source_section_for_validation_claim(
                    claim_id,
                    agent=agent,
                    input_bundle=input_bundle,
                )
            if not source_section:
                source_section = source_section_for_input_file(
                    source_path,
                    agent="",
                    input_bundle=input_bundle,
                )
            if not source_section and evidence_ids and input_bundle is not None:
                source_section = normalize_basis_source_section(
                    f"evidence_catalog.{evidence_ids[0]}",
                    agent="",
                    input_bundle=input_bundle,
                )
            evidence_agent = basis_agent_for_evidence_ids(evidence_ids, input_bundle)
            if evidence_agent:
                agent = evidence_agent
            agent = canonical_basis_agent(agent, source_section)
            rows.append(
                {
                    "agent": agent,
                    "claim_id": claim_id,
                    "evidence_text": truncate_text(evidence_text, 160),
                    "source_path": source_path,
                    "source_section": source_section,
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
    return [
        row
        for row in rows
        if row.get("evidence_text") or row.get("claim_id") or row.get("evidence_ids") or row.get("source_section")
    ]


def basis_agent_for_evidence_ids(
    evidence_ids: list[str],
    input_bundle: dict[str, Any] | None,
) -> str:
    if input_bundle is None:
        return ""
    catalog = input_bundle.get("evidence_catalog") or {}
    domains = {
        clean_text((catalog.get(evidence_id) or {}).get("domain"))
        for evidence_id in evidence_ids
        if isinstance(catalog.get(evidence_id), dict)
    }
    domains.discard("")
    if len(domains) != 1:
        return ""
    return {
        "financial": "Financial",
        "news": "News",
        "market": "YFinance",
    }.get(next(iter(domains)), "")


def is_strategy_opinion_id(value: Any) -> bool:
    """Return True for Strategy opinion-index IDs, which are not source IDs."""

    return bool(re.fullmatch(r"OP\d+", clean_text(value), flags=re.IGNORECASE))


def is_empty_source_identifier(value: Any) -> bool:
    """Return True for placeholder text that is not a provenance identifier."""

    return clean_text(value).lower() in {"n/a", "na", "none", "null", "not applicable", "not_applicable"}


def normalize_basis_source_section(
    value: Any,
    *,
    agent: str,
    input_bundle: dict[str, Any] | None,
) -> str:
    """Keep only canonical source paths that exist in the supplied input bundle."""

    source_section = clean_text(value)
    if not source_section:
        return ""
    source_section = re.sub(r"^\$\.?", "", source_section)
    if source_section.startswith("strategy_input_bundle."):
        source_section = source_section.removeprefix("strategy_input_bundle.")
    if source_section.startswith("strategy_llm_packet."):
        source_section = source_section.removeprefix("strategy_llm_packet.")
    if source_section.startswith("strategy_decision_packet."):
        source_section = source_section.removeprefix("strategy_decision_packet.")
    if not source_section.startswith(BASIS_SOURCE_ROOTS):
        return ""
    allowed_roots = basis_source_roots_for_agent(agent)
    if allowed_roots and not source_section.startswith(allowed_roots):
        return ""
    if input_bundle is not None and not input_bundle_path_exists(input_bundle, source_section):
        return ""
    return source_section


def basis_source_roots_for_agent(agent: str) -> tuple[str, ...]:
    """Return input-bundle roots allowed for a recognized evidence domain."""

    normalized = re.sub(r"[^a-z]", "", clean_text(agent).lower())
    common = ("limitations", "decision_constraints")
    if normalized in {"financial", "financialagent", "dart", "dartagent"}:
        return ("claim_ledger.financial", "evidence_catalog", "structured_facts.financial", *common)
    if normalized in {"news", "newsagent"}:
        return ("claim_ledger.news", "evidence_catalog", "secondary_context_assessments", *common)
    if normalized in {"yfinance", "yfinanceagent", "market", "marketagent"}:
        return (
            "claim_ledger.yfinance",
            "evidence_catalog",
            "secondary_context_assessments",
            "structured_facts.valuation",
            *common,
        )
    if normalized in {"competitor", "competitoragent", "peer", "peercomparison"}:
        return ("peer_metric_catalog", "peer_context", *common)
    return ()


def canonical_basis_agent(agent: str, source_section: str) -> str:
    """Align the evidence-domain label with an exact input-bundle section."""

    if source_section.startswith(("claim_ledger.financial", "structured_facts.financial")):
        return "Financial"
    if source_section.startswith("claim_ledger.news"):
        return "News"
    if source_section.startswith(("claim_ledger.yfinance", "structured_facts.valuation")):
        return "YFinance"
    if source_section.startswith(("peer_metric_catalog", "peer_context")):
        return "Competitor"
    if source_section.startswith("evidence_catalog"):
        return agent
    return agent


def input_bundle_path_exists(input_bundle: dict[str, Any], source_section: str) -> bool:
    """Resolve a dotted path with optional list indexes against strategy_input_bundle."""

    dotted_path = re.sub(r"\[(\d+)\]", r".\1", source_section)
    tokens = [token for token in dotted_path.split(".") if token]
    current: Any = input_bundle
    for token in tokens:
        if isinstance(current, dict) and token in current:
            current = current[token]
            continue
        if isinstance(current, list) and token.isdigit() and int(token) < len(current):
            current = current[int(token)]
            continue
        return False
    return bool(tokens)


def source_section_for_validation_claim(
    claim_id: str,
    *,
    agent: str,
    input_bundle: dict[str, Any] | None,
) -> str:
    """Resolve a validation claim ID to its exact compact-ledger path."""

    if not claim_id or input_bundle is None:
        return ""
    allowed_roots = basis_source_roots_for_agent(agent)
    validation_root = input_bundle.get("claim_ledger")
    if not isinstance(validation_root, dict):
        return ""
    for domain, payload in validation_root.items():
        domain_prefix = f"claim_ledger.{domain}"
        if allowed_roots and not domain_prefix.startswith(allowed_roots):
            continue
        for index, claim in enumerate(ensure_list(payload)):
            if isinstance(claim, dict) and clean_text(claim.get("claim_id")) == claim_id:
                return f"{domain_prefix}[{index}]"
    return ""


def source_section_for_input_file(
    source_path: str,
    *,
    agent: str,
    input_bundle: dict[str, Any] | None,
) -> str:
    """Map an exact source-file path back to its input-bundle section."""

    if not source_path or input_bundle is None:
        return ""
    metadata = input_bundle.get("input_metadata")
    if not isinstance(metadata, dict):
        return ""
    candidates: list[tuple[str, str]] = [
        (clean_text(metadata.get("target_financial_path")), "target_reports.financial"),
        (clean_text(metadata.get("target_news_path")), "target_reports.news"),
        (clean_text(metadata.get("target_yfinance_path")), "target_reports.yfinance"),
        (clean_text(metadata.get("peer_comparison_path")), "peer_comparison"),
    ]
    validation_paths = metadata.get("target_validation_paths")
    if isinstance(validation_paths, dict):
        candidates.extend(
            (clean_text(path), f"target_validation_evidence.{domain}")
            for domain, path in validation_paths.items()
        )
    normalized_source = str(Path(source_path).expanduser().resolve()) if source_path else ""
    for candidate_path, section in candidates:
        if not candidate_path:
            continue
        if normalized_source != str(Path(candidate_path).expanduser().resolve()):
            continue
        return normalize_basis_source_section(section, agent=agent, input_bundle=input_bundle)
    return ""


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
            "investment_horizon": clean_text(recommendation.get("investment_horizon")),
            "evidence_sufficiency": clean_text(recommendation.get("evidence_sufficiency")).lower(),
            "evidence_sufficiency_reason": clean_text(recommendation.get("evidence_sufficiency_reason")),
        },
        "investment_thesis": string_fields(
            report.get("investment_thesis"),
            ("thesis_1", "thesis_2", "thesis_3"),
        ),
        "financial_view": string_fields(
            report.get("financial_view"),
            ("revenue", "profitability", "cash_flow", "balance_sheet", "financial_interpretation"),
        ),
        "business_mix_view": string_fields(
            report.get("business_mix_view"),
            ("revenue_composition", "concentration", "business_mix_interpretation"),
        ),
        "catalyst_view": list_fields(
            report.get("catalyst_view"),
            ("observed_catalysts",),
        ),
        "risk_view": normalize_observed_risks(report.get("risk_view")),
        "market_price_view": string_fields(
            report.get("market_price_view"),
            ("price_trend", "volume", "relative_strength", "market_interpretation"),
        ),
        "valuation_view": string_fields(
            report.get("valuation_view"),
            ("selected_date_valuation", "peer_valuation_comparison", "valuation_interpretation"),
        ),
        "cross_agent_consistency_check": {
            **list_fields(report.get("cross_agent_consistency_check"), ("confirmed_signals", "mixed_conflicting_signals")),
            "strategy_implication": clean_text(get_path(report, ["cross_agent_consistency_check", "strategy_implication"])),
        },
        "peer_competitor_positioning": {
            **list_fields(
                report.get("peer_competitor_positioning"),
                ("pairwise_findings", "comparison_limits"),
            ),
            "peer_based_investment_implication": clean_text(
                get_path(report, ["peer_competitor_positioning", "peer_based_investment_implication"])
            ),
        },
        "decision_balance": {
            **list_fields(report.get("decision_balance"), ("positive_evidence", "negative_evidence")),
            "balance_conclusion": clean_text(get_path(report, ["decision_balance", "balance_conclusion"])),
        },
        "final_rationale": {
            "why_buy_hold_sell": final_rationale_text,
        },
        # Limitation meaning and bucket ownership belong to the Decision Agent.
        "limitations": report.get("limitations"),
    }
    return normalized


def string_fields(value: Any, keys: tuple[str, ...]) -> dict[str, str]:
    """Return a dict with required string keys."""

    payload = value if isinstance(value, dict) else {}
    return {key: clean_text(payload.get(key)) for key in keys}


def list_fields(value: Any, keys: tuple[str, ...]) -> dict[str, list[str]]:
    """Return a dict with required list-of-string keys."""

    payload = value if isinstance(value, dict) else {}
    return {key: text_items(payload.get(key)) for key in keys}


def normalize_observed_risks(value: Any) -> dict[str, list[dict[str, str]]]:
    """Normalize the evidence-backed risk list without inferring categories."""

    payload = value if isinstance(value, dict) else {}
    raw_items = payload.get("observed_risks")
    if not isinstance(raw_items, list):
        raise ValueError("risk_view.observed_risks must be a list.")
    items: list[dict[str, str]] = []
    for index, item in enumerate(raw_items):
        if not isinstance(item, dict):
            raise ValueError(f"risk_view.observed_risks[{index}] must be an object.")
        items.append(
            {
                "category": clean_text(item.get("category")).lower(),
                "statement": clean_text(item.get("statement")),
            }
        )
    return {"observed_risks": items}


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
    """Keep decision evidence while removing upstream validation operations."""

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
            "sy_handoff",
            "secondary_context",
            "secondary_context_assessment",
        ):
            if key in report:
                compact[key] = report.get(key)
        return compact
    if domain == "news":
        output = report.get("output") if isinstance(report.get("output"), dict) else report
        compact["output"] = {
            key: output.get(key)
            for key in ("target_entity", "analysis_blocks", "secondary_context_assessment")
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
            "secondary_context_assessment",
        ):
            if key in report:
                compact[key] = report.get(key)
        return compact
    return report


def strip_operational_validation_content(value: Any) -> Any:
    """Recursively remove SY/rewrite mechanics from reader-facing evidence."""

    operational_keys = {
        "report_status",
        "sy_validation",
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


def compact_validation_evidence(payload: dict[str, Any], *, domain: str, source_path: Path) -> dict[str, Any]:
    """Keep only Strategy-useful validation evidence from large SY outputs."""

    claims = validation_claims(payload, domain)
    compact_claims = [
        compact_validation_claim(claim, domain=domain)
        for claim in claims
        if isinstance(claim, dict) and first_non_empty(claim.get("claim_id"), claim.get("section"), claim.get("claim"))
    ]
    evidence_use_counts = {
        status: sum(1 for claim in compact_claims if claim.get("evidence_use") == status)
        for status in ("strong", "context_only", "exclude")
    }
    return {
        "source_path": str(source_path.expanduser().resolve()),
        "summary": {
            "claim_count": len(compact_claims),
            "evidence_use_counts": evidence_use_counts,
        },
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
        for key in (
            "verified_claims",
            "context_only_claims",
            "revised_claims",
            "weakened_claims",
            "excluded_claims",
            "hallucination_candidates",
            "removed_claims",
        ):
            claims.extend(item for item in ensure_list(payload.get(key)) if isinstance(item, dict))
        return claims
    return []


def compact_validation_claim(claim: dict[str, Any], *, domain: str) -> dict[str, Any]:
    """Normalize one validation claim into a small evidence ledger row."""

    raw_decision = clean_text(claim.get("decision"))
    support_level = clean_text(claim.get("support_level"))
    result = {
        "claim_id": first_non_empty(claim.get("claim_id"), claim.get("section")),
        "section": first_non_empty(claim.get("section"), claim.get("section_path")),
        "claim": first_non_empty(claim.get("claim_ko"), claim.get("claim")),
        "evidence_ids": validation_evidence_ids(claim, domain),
        "claim_kind": clean_text(claim.get("claim_kind")),
        "limitations": text_items(claim.get("limitations")),
        "evidence_use": validation_evidence_use(
            raw_decision,
            support_level,
            explicit=clean_text(claim.get("evidence_use")),
        ),
    }
    if domain == "news":
        for key in (
            "event_status",
            "company_specificity",
            "materiality_status",
            "financial_link_status",
        ):
            value = clean_text(claim.get(key))
            if value:
                result[key] = value
    return result


def validation_evidence_use(decision: str, support_level: str, *, explicit: str = "") -> str:
    """Map upstream workflow states to a reader-neutral evidence-use contract."""

    explicit_value = clean_text(explicit).lower()
    if explicit_value in {"strong", "context_only", "exclude"}:
        return explicit_value
    decision_value = clean_text(decision).lower()
    support_value = clean_text(support_level).lower()
    if decision_value in {"remove", "delete", "hallucination_candidate"} or support_value == "unsupported":
        return "exclude"
    if decision_value in {"keep", "verified"} and support_value in {"", "supported", "verified"}:
        return "strong"
    if not decision_value and support_value in {"supported", "verified"}:
        return "strong"
    return "context_only"


def validation_evidence_ids(claim: dict[str, Any], domain: str) -> list[str]:
    """Extract evidence identifiers from validation claim variants."""

    del domain
    evidence: list[str] = []
    for key in (
        "evidence_ids",
        "evidence_refs",
        "evidence_ids_used",
        "declared_evidence_ids",
        "evidence_used",
    ):
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


def build_report_opinion_index(strategy_report: dict[str, Any]) -> list[dict[str, Any]]:
    """Create stable audit IDs for reader-facing report opinions."""

    items: list[dict[str, Any]] = []
    for section_path, text in iter_editable_report_opinions(strategy_report):
        if not text:
            continue
        items.append(
            {
                "id": f"OP{len(items) + 1:03d}",
                "section_path": section_path,
                "text": text,
                "audit_scope": "trace_this_text_only",
            }
        )
    return items


def iter_editable_report_opinions(strategy_report: dict[str, Any]) -> list[tuple[str, str]]:
    """Flatten reader-facing Strategy opinions into auditable text units."""

    roots = (
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
    opinions: list[tuple[str, str]] = []
    for root in roots:
        value = strategy_report.get(root)
        for section_path, text in iter_report_claims(value, root):
            if section_path.endswith((".opinion", ".investment_horizon", ".evidence_sufficiency", ".category")):
                continue
            opinions.append((section_path, text))
    return opinions


def iter_report_claims(value: Any, prefix: str = "") -> list[tuple[str, str]]:
    """Flatten nested report values into stable path/text pairs."""

    claims: list[tuple[str, str]] = []
    if isinstance(value, dict):
        for key, child in value.items():
            child_prefix = f"{prefix}.{key}" if prefix else key
            claims.extend(iter_report_claims(child, child_prefix))
    elif isinstance(value, list):
        for index, child in enumerate(value):
            claims.extend(iter_report_claims(child, f"{prefix}[{index}]"))
    else:
        text = clean_text(value)
        if text:
            claims.append((prefix, text))
    return claims


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
    hierarchy = bundle.get("evidence_hierarchy")
    if not isinstance(hierarchy, list) or not hierarchy:
        raise ValueError("evidence_hierarchy must be a non-empty list.")


def validate_content_plan(
    content_plan: dict[str, Any],
    *,
    llm_packet: dict[str, Any] | None = None,
) -> None:
    """Validate strategy_content_plan.json shape."""

    require_dict(content_plan, "content_plan")
    for key in (
        "positive_claim_ids",
        "negative_claim_ids",
        "neutral_claim_ids",
        "catalyst_claim_ids",
        "risk_claim_ids",
        "context_assessment_ids",
        "peer_metric_ids",
        "limitation_ids",
    ):
        if not isinstance(content_plan.get(key), list):
            raise ValueError(f"{key} must be a list.")
    section_plan = require_dict(content_plan.get("section_plan"), "section_plan")
    required_sections = set(CONTENT_PLAN_SECTIONS)
    missing_sections = sorted(required_sections - set(section_plan))
    if missing_sections:
        raise ValueError(f"section_plan missing sections: {missing_sections}")
    if any(not isinstance(value, list) for value in section_plan.values()):
        raise ValueError("section_plan values must be arrays of supplied IDs.")
    if "final_recommendation" in content_plan:
        raise ValueError("content_plan must not include final_recommendation.")
    if llm_packet is None:
        return
    claim_ids = {
        clean_text(claim.get("claim_id"))
        for claims in (llm_packet.get("claim_ledger") or {}).values()
        for claim in claims
        if clean_text(claim.get("claim_id"))
    }
    context_ids = {
        clean_text(item.get("context_id"))
        for item in ensure_list(llm_packet.get("secondary_context_assessments"))
    }
    peer_ids = set((llm_packet.get("peer_metric_catalog") or {}).keys())
    limit_ids = set((llm_packet.get("limitations") or {}).keys())
    selected_claim_ids = {
        value
        for key in (
            "positive_claim_ids",
            "negative_claim_ids",
            "neutral_claim_ids",
            "catalyst_claim_ids",
            "risk_claim_ids",
        )
        for value in text_items(content_plan.get(key))
    }
    _assert_known_ids(selected_claim_ids, claim_ids, "claim")
    _assert_known_ids(set(text_items(content_plan.get("context_assessment_ids"))), context_ids, "context assessment")
    _assert_known_ids(set(text_items(content_plan.get("peer_metric_ids"))), peer_ids, "peer metric")
    _assert_known_ids(set(text_items(content_plan.get("limitation_ids"))), limit_ids, "limitation")
    all_ids = claim_ids | context_ids | peer_ids | limit_ids
    for section, values in section_plan.items():
        _assert_known_ids(set(text_items(values)), all_ids, f"section_plan.{section}")
    if claim_ids and not selected_claim_ids:
        raise ValueError("Content plan selected no admissible claim IDs.")
    domain_claim_ids = {
        domain: {
            clean_text(claim.get("claim_id"))
            for claim in claims
            if clean_text(claim.get("claim_id"))
        }
        for domain, claims in (llm_packet.get("claim_ledger") or {}).items()
    }
    for domain, available in domain_claim_ids.items():
        if available and not (available & selected_claim_ids):
            raise ValueError(f"Content plan omitted the {domain} claim domain.")
    selected_context_ids = set(text_items(content_plan.get("context_assessment_ids")))
    conflicting_context_ids = {
        clean_text(item.get("context_id"))
        for item in ensure_list(llm_packet.get("secondary_context_assessments"))
        if item.get("effect") == "contradicts"
    }
    if conflicting_context_ids - selected_context_ids:
        raise ValueError("Content plan omitted conflicting secondary context.")
    if peer_ids and not set(text_items(content_plan.get("peer_metric_ids"))):
        raise ValueError("Content plan omitted available peer metrics.")
    if limit_ids and not set(text_items(content_plan.get("limitation_ids"))):
        raise ValueError("Content plan omitted available limitations.")


def _assert_known_ids(selected: set[str], available: set[str], label: str) -> None:
    unknown = sorted(selected - available)
    if unknown:
        raise ValueError(f"Unknown {label} IDs: {unknown}")


def validate_strategy_report(
    strategy_report: dict[str, Any],
    *,
    input_bundle: dict[str, Any] | None = None,
) -> None:
    """Validate final strategy_report.json shape."""

    require_dict(strategy_report, "strategy_report")
    require_non_empty(strategy_report.get("target_company_name"), "target_company_name")
    recommendation = require_dict(strategy_report.get("final_recommendation"), "final_recommendation")
    if recommendation.get("opinion") not in FINAL_RECOMMENDATIONS:
        raise ValueError("final_recommendation.opinion must be one of Buy/Hold/Sell.")
    require_non_empty(recommendation.get("summary"), "final_recommendation.summary")
    require_non_empty(recommendation.get("investment_horizon"), "final_recommendation.investment_horizon")
    if recommendation.get("evidence_sufficiency") not in {"high", "medium", "low"}:
        raise ValueError("final_recommendation.evidence_sufficiency must be high/medium/low.")
    require_non_empty(
        recommendation.get("evidence_sufficiency_reason"),
        "final_recommendation.evidence_sufficiency_reason",
    )

    for section, keys in {
        "investment_thesis": ("thesis_1", "thesis_2", "thesis_3"),
        "financial_view": ("revenue", "profitability", "cash_flow", "balance_sheet", "financial_interpretation"),
        "business_mix_view": ("revenue_composition", "concentration", "business_mix_interpretation"),
        "market_price_view": ("price_trend", "volume", "relative_strength", "market_interpretation"),
        "valuation_view": ("selected_date_valuation", "peer_valuation_comparison", "valuation_interpretation"),
        "final_rationale": ("why_buy_hold_sell",),
    }.items():
        payload = require_dict(strategy_report.get(section), section)
        for key in keys:
            require_non_empty(payload.get(key), f"{section}.{key}")
    for section, keys in {
        "catalyst_view": ("observed_catalysts",),
    }.items():
        payload = require_dict(strategy_report.get(section), section)
        for key in keys:
            if not isinstance(payload.get(key), list):
                raise ValueError(f"{section}.{key} must be a list.")

    risk_view = require_dict(strategy_report.get("risk_view"), "risk_view")
    observed_risks = risk_view.get("observed_risks")
    if not isinstance(observed_risks, list):
        raise ValueError("risk_view.observed_risks must be a list.")
    allowed_risk_categories = {"business", "financial", "regulatory", "market", "execution"}
    for index, item in enumerate(observed_risks):
        risk = require_dict(item, f"risk_view.observed_risks[{index}]")
        if risk.get("category") not in allowed_risk_categories:
            raise ValueError(
                f"risk_view.observed_risks[{index}].category must be business/financial/regulatory/market/execution."
            )
        require_non_empty(risk.get("statement"), f"risk_view.observed_risks[{index}].statement")

    consistency = require_dict(strategy_report.get("cross_agent_consistency_check"), "cross_agent_consistency_check")
    for key in ("confirmed_signals", "mixed_conflicting_signals"):
        if not isinstance(consistency.get(key), list):
            raise ValueError(f"cross_agent_consistency_check.{key} must be a list.")
    require_non_empty(consistency.get("strategy_implication"), "cross_agent_consistency_check.strategy_implication")

    peer = require_dict(strategy_report.get("peer_competitor_positioning"), "peer_competitor_positioning")
    for key in ("pairwise_findings", "comparison_limits"):
        if not isinstance(peer.get(key), list):
            raise ValueError(f"peer_competitor_positioning.{key} must be a list.")
    require_non_empty(peer.get("peer_based_investment_implication"), "peer_competitor_positioning.peer_based_investment_implication")

    balance = require_dict(strategy_report.get("decision_balance"), "decision_balance")
    for key in ("positive_evidence", "negative_evidence"):
        items = balance.get(key)
        if not isinstance(items, list):
            raise ValueError(f"decision_balance.{key} must be a list.")
        for index, item in enumerate(items):
            require_non_empty(item, f"decision_balance.{key}[{index}]")
    require_non_empty(balance.get("balance_conclusion"), "decision_balance.balance_conclusion")

    limitations = require_dict(strategy_report.get("limitations"), "limitations")
    seen_limitations: dict[str, str] = {}
    for key in ("data_limitations", "interpretation_limitations", "monitoring_points"):
        items = limitations.get(key)
        if not isinstance(items, list):
            raise ValueError(f"limitations.{key} must be a list.")
        for index, item in enumerate(items):
            if not isinstance(item, str) or not item.strip():
                raise ValueError(f"limitations.{key}[{index}] must be a non-empty string.")
            normalized_item = clean_text(item)
            if normalized_item in seen_limitations:
                raise ValueError(
                    f"limitations contains a duplicate across buckets: {seen_limitations[normalized_item]} and "
                    f"limitations.{key}[{index}]"
                )
            seen_limitations[normalized_item] = f"limitations.{key}[{index}]"
    require_dict(strategy_report.get("source_files"), "source_files")
    validate_opinion_index(strategy_report.get("opinion_index"))
    if input_bundle is not None:
        validate_large_number_grounding(strategy_report, input_bundle)


def validate_large_number_grounding(strategy_report: dict[str, Any], input_bundle: dict[str, Any]) -> None:
    """Reject full-size integer figures that do not occur in the Strategy input."""

    known_values = collect_large_integer_values(input_bundle)
    ungrounded: list[str] = []
    for section_path, opinion_text in iter_editable_report_opinions(strategy_report):
        for token, number in large_integer_tokens(opinion_text):
            if number not in known_values:
                ungrounded.append(f"{section_path}: {token}")
    if ungrounded:
        raise ValueError(f"strategy_report contains ungrounded large integer value(s): {ungrounded[:5]}")


def collect_large_integer_values(value: Any) -> set[int]:
    """Collect exact large integer values from nested structured or textual input."""

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
        values.update(number for _, number in large_integer_tokens(value))
        return values
    if isinstance(value, dict):
        for child in value.values():
            values.update(collect_large_integer_values(child))
        return values
    if isinstance(value, list):
        for child in value:
            values.update(collect_large_integer_values(child))
    return values


def large_integer_tokens(value: Any) -> list[tuple[str, int]]:
    """Extract integer tokens large enough to represent exact won-scale figures."""

    tokens: list[tuple[str, int]] = []
    for match in re.finditer(r"(?<![\d.,])[-+]?\d[\d,]*(?![\d.,])", clean_text(value)):
        token = match.group(0)
        digits = token.lstrip("+-")
        if "," in digits and not re.fullmatch(r"\d{1,3}(?:,\d{3})+", digits):
            continue
        number = int(token.replace(",", ""))
        if abs(number) >= 100_000_000:
            tokens.append((token, number))
    return tokens


def validate_opinion_index(value: Any) -> None:
    """Validate audit IDs when opinion_index is present."""

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
    business_mix = require_dict(strategy_report.get("business_mix_view"), "business_mix_view")
    catalyst = require_dict(strategy_report.get("catalyst_view"), "catalyst_view")
    risks = require_dict(strategy_report.get("risk_view"), "risk_view")
    market = require_dict(strategy_report.get("market_price_view"), "market_price_view")
    valuation = require_dict(strategy_report.get("valuation_view"), "valuation_view")
    consistency = require_dict(strategy_report.get("cross_agent_consistency_check"), "cross_agent_consistency_check")
    peer = require_dict(strategy_report.get("peer_competitor_positioning"), "peer_competitor_positioning")
    final_rationale = require_dict(strategy_report.get("final_rationale"), "final_rationale")
    balance = require_dict(strategy_report.get("decision_balance"), "decision_balance")
    limitations = require_dict(strategy_report.get("limitations"), "limitations")

    lines = [
        f"## 0. Target Company Name: {clean_text(strategy_report.get('target_company_name')) or 'N/A'}",
        "",
        "## 1. Final Recommendation",
        f"- Opinion: {clean_text(recommendation.get('opinion')) or 'N/A'}",
        f"- Summary: {clean_text(recommendation.get('summary')) or 'N/A'}",
        f"- Investment Horizon: {clean_text(recommendation.get('investment_horizon')) or 'N/A'}",
        f"- Evidence Sufficiency: {clean_text(recommendation.get('evidence_sufficiency')) or 'N/A'}",
        f"- Sufficiency Basis: {clean_text(recommendation.get('evidence_sufficiency_reason')) or 'N/A'}",
        "",
        "## 2. Investment Thesis",
        f"- Thesis 1: {clean_text(thesis.get('thesis_1')) or 'N/A'}",
        f"- Thesis 2: {clean_text(thesis.get('thesis_2')) or 'N/A'}",
        f"- Thesis 3: {clean_text(thesis.get('thesis_3')) or 'N/A'}",
    ]
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
        "## 4. Business Mix View",
        f"- Revenue Composition: {clean_text(business_mix.get('revenue_composition')) or 'N/A'}",
        f"- Concentration: {clean_text(business_mix.get('concentration')) or 'N/A'}",
        f"- Interpretation: {clean_text(business_mix.get('business_mix_interpretation')) or 'N/A'}",
        "",
        "## 5. Catalyst View",
        f"- Observed Catalysts: {render_inline_items(catalyst.get('observed_catalysts'))}",
        "",
        "## 6. Risk View",
        f"- Observed Risks: {render_observed_risks(risks.get('observed_risks'))}",
        "",
        "## 7. Market / Price View",
        f"- Price Trend: {clean_text(market.get('price_trend')) or 'N/A'}",
        f"- Volume: {clean_text(market.get('volume')) or 'N/A'}",
        f"- Relative Strength: {clean_text(market.get('relative_strength')) or 'N/A'}",
        f"- Market Interpretation: {clean_text(market.get('market_interpretation')) or 'N/A'}",
        "",
        "## 8. Valuation View",
        f"- Selected-date Valuation: {clean_text(valuation.get('selected_date_valuation')) or 'N/A'}",
        f"- Peer Valuation: {clean_text(valuation.get('peer_valuation_comparison')) or 'N/A'}",
        f"- Interpretation: {clean_text(valuation.get('valuation_interpretation')) or 'N/A'}",
        "",
        "## 9. Cross-Agent Consistency Check",
        f"- Confirmed Signals: {render_inline_items(consistency.get('confirmed_signals'))}",
        f"- Mixed / Conflicting Signals: {render_inline_items(consistency.get('mixed_conflicting_signals'))}",
        f"- Strategy Implication: {clean_text(consistency.get('strategy_implication')) or 'N/A'}",
        "",
        "## 10. Peer / Competitor Positioning",
        f"- Pairwise Findings: {render_inline_items(peer.get('pairwise_findings'))}",
        f"- Comparison Limits: {render_inline_items(peer.get('comparison_limits'))}",
        f"- Peer-based Investment Implication: {clean_text(peer.get('peer_based_investment_implication')) or 'N/A'}",
        "",
        "## 11. Decision Balance",
            f"- Positive Evidence: {render_inline_items(balance.get('positive_evidence'))}",
            f"- Negative Evidence: {render_inline_items(balance.get('negative_evidence'))}",
            f"- Balance Conclusion: {clean_text(balance.get('balance_conclusion')) or 'N/A'}",
            "",
            "## 12. Final Rationale",
            f"- Why Buy/Hold/Sell: {clean_text(final_rationale.get('why_buy_hold_sell')) or 'N/A'}",
            "",
            "## 13. Limitations",
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
        "strong_claims_in_report": [
            {
                "id": f"strong_claim_{index}",
                "claim": clean_text(entry.get("opinion_text")),
                "source_sections": [section_path],
                "verification_focus": text_items(entry.get("limitations")),
            }
            for index, (section_path, entry) in enumerate(basis_map.items(), start=1)
            if text_items(entry.get("key_numbers")) or normalize_basis_source_evidence(entry.get("source_evidence"))
        ],
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
    if section_path.startswith("business_mix_view"):
        return "business_mix"
    if section_path.startswith("catalyst_view"):
        return "business_catalyst"
    if section_path.startswith("market_price_view"):
        return "market_price"
    if section_path.startswith("valuation_view"):
        return "valuation"
    if section_path.startswith("peer_competitor_positioning"):
        return "peer_positioning"
    if section_path.startswith("cross_agent_consistency_check"):
        return "cross_agent_consistency"
    if section_path.startswith("decision_balance"):
        return "decision_balance"
    return "recommendation"


def risk_category_from_path(section_path: str) -> str:
    """Return a risk category label derived from the report section path."""

    return "observed_risk" if ".observed_risks" in section_path else "risk"


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
        evidence_limitations=basis_entry_evidence_limitations(entry),
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


def basis_entry_evidence_limitations(entry: dict[str, Any]) -> list[str]:
    """Use LLM-written limitations as audit metadata without adding prose."""

    return text_items(entry.get("limitations"))


def card_item(
    *,
    item_id: str,
    category: str,
    direction: str,
    claim: str,
    reasoning: str,
    evidence: list[str],
    source_sections: list[str],
    evidence_limitations: list[str],
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
        "evidence_limitations": dedupe(text_items(evidence_limitations), 8),
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
        if not isinstance(entry.get("source_evidence"), list):
            raise ValueError(f"decision_basis_by_section.{section_path}.source_evidence must be a list.")
        if not entry.get("source_evidence"):
            raise ValueError(f"decision_basis_by_section.{section_path}.source_evidence must not be empty.")
        validate_basis_source_evidence_integrity(section_path, entry.get("source_evidence"))


def validate_basis_source_evidence_integrity(section_path: str, value: Any) -> None:
    """Reject Strategy opinion IDs and non-bundle section labels as source metadata."""

    for index, row in enumerate(ensure_list(value)):
        if not isinstance(row, dict):
            raise ValueError(f"decision_basis_by_section.{section_path}.source_evidence[{index}] must be an object.")
        if is_strategy_opinion_id(row.get("claim_id")):
            raise ValueError(
                f"decision_basis_by_section.{section_path}.source_evidence[{index}].claim_id uses an opinion ID."
            )
        if any(is_strategy_opinion_id(evidence_id) for evidence_id in text_items(row.get("evidence_ids"))):
            raise ValueError(
                f"decision_basis_by_section.{section_path}.source_evidence[{index}].evidence_ids uses an opinion ID."
            )
        source_section = clean_text(row.get("source_section"))
        if source_section and not source_section.startswith(BASIS_SOURCE_ROOTS):
            raise ValueError(
                f"decision_basis_by_section.{section_path}.source_evidence[{index}].source_section is not an input-bundle path."
            )


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
    flags = get_path(financial, ["sy_handoff", "reconciliation_flags"]) or []
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


def content_plan_response_format(llm_packet: dict[str, Any]) -> dict[str, Any]:
    """Constrain Content Planner output to IDs present in the current packet."""

    claim_ids = sorted(
        {
            clean_text(claim.get("claim_id"))
            for claims in (llm_packet.get("claim_ledger") or {}).values()
            for claim in ensure_list(claims)
            if isinstance(claim, dict) and clean_text(claim.get("claim_id"))
        }
    )
    context_ids = sorted(
        {
            clean_text(item.get("context_id"))
            for item in ensure_list(llm_packet.get("secondary_context_assessments"))
            if isinstance(item, dict) and clean_text(item.get("context_id"))
        }
    )
    peer_ids = sorted(str(value) for value in (llm_packet.get("peer_metric_catalog") or {}))
    limitation_ids = sorted(str(value) for value in (llm_packet.get("limitations") or {}))
    all_ids = sorted(set(claim_ids) | set(context_ids) | set(peer_ids) | set(limitation_ids))
    target_company = clean_text((llm_packet.get("target_company") or {}).get("company_name"))

    def id_schema(allowed_ids: list[str]) -> dict[str, Any]:
        item_schema: dict[str, Any] = {"type": "string"}
        if allowed_ids:
            item_schema["enum"] = allowed_ids
        return item_schema

    def array_schema(definition: str) -> dict[str, Any]:
        return {"type": "array", "items": {"$ref": f"#/$defs/{definition}"}}

    target_schema: dict[str, Any] = {"type": "string"}
    if target_company:
        target_schema["enum"] = [target_company]

    section_properties = {
        section: array_schema("supplied_id")
        for section in CONTENT_PLAN_SECTIONS
    }
    schema = {
        "type": "object",
        "$defs": {
            "claim_id": id_schema(claim_ids),
            "context_id": id_schema(context_ids),
            "peer_metric_id": id_schema(peer_ids),
            "limitation_id": id_schema(limitation_ids),
            "supplied_id": id_schema(all_ids),
        },
        "properties": {
            "target_company": target_schema,
            "positive_claim_ids": array_schema("claim_id"),
            "negative_claim_ids": array_schema("claim_id"),
            "neutral_claim_ids": array_schema("claim_id"),
            "catalyst_claim_ids": array_schema("claim_id"),
            "risk_claim_ids": array_schema("claim_id"),
            "context_assessment_ids": array_schema("context_id"),
            "peer_metric_ids": array_schema("peer_metric_id"),
            "limitation_ids": array_schema("limitation_id"),
            "section_plan": {
                "type": "object",
                "properties": section_properties,
                "required": list(CONTENT_PLAN_SECTIONS),
                "additionalProperties": False,
            },
        },
        "required": [
            "target_company",
            "positive_claim_ids",
            "negative_claim_ids",
            "neutral_claim_ids",
            "catalyst_claim_ids",
            "risk_claim_ids",
            "context_assessment_ids",
            "peer_metric_ids",
            "limitation_ids",
            "section_plan",
        ],
        "additionalProperties": False,
    }
    return {
        "type": "json_schema",
        "json_schema": {
            "name": "strategy_content_plan",
            "strict": True,
            "schema": schema,
        },
    }


def strategy_report_schema() -> dict[str, Any]:
    """Return required Strategy Report schema for prompting."""

    return {
        "agent_name": "Strategy Agent",
        "target_company_name": "string",
        "final_recommendation": {
            "opinion": "Buy | Hold | Sell",
            "summary": "string",
            "investment_horizon": "string - explicit horizon used for the recommendation",
            "evidence_sufficiency": "high | medium | low - independent from Buy/Hold/Sell",
            "evidence_sufficiency_reason": "string - explain source coverage and material gaps",
        },
        "investment_thesis": {
            "thesis_1": "string - opinion plus concrete basis",
            "thesis_2": "string - opinion plus concrete basis",
            "thesis_3": "string - strongest counterpoint and how it affects the chosen opinion",
        },
        "financial_view": {
            "revenue": "string - opinion plus concrete financial basis",
            "profitability": "string - opinion plus concrete financial basis",
            "cash_flow": "string - opinion plus concrete financial basis",
            "balance_sheet": "string - opinion plus concrete financial basis",
            "financial_interpretation": "string - opinion plus concrete financial basis and limitation",
        },
        "business_mix_view": {
            "revenue_composition": "string - disclosed revenue composition or explicit not_disclosed status",
            "concentration": "string - concentration interpretation with concrete shares when available",
            "business_mix_interpretation": "string - implication and evidence limitation",
        },
        "catalyst_view": {
            "observed_catalysts": ["string - one supplied event or development that may change future expectations, plus concrete evidence"],
        },
        "risk_view": {
            "observed_risks": [
                {
                    "category": "business | financial | regulatory | market | execution",
                    "statement": "string - one observed adverse exposure, condition, obligation, or event plus concrete evidence",
                }
            ],
        },
        "market_price_view": {
            "price_trend": "string - opinion plus concrete market basis",
            "volume": "string - opinion plus concrete volume basis",
            "relative_strength": "string - opinion plus concrete relative performance basis",
            "market_interpretation": "string - opinion plus limitation on fundamental inference",
        },
        "valuation_view": {
            "selected_date_valuation": "string - selected-date P/E, P/B, P/S with provenance",
            "peer_valuation_comparison": "string - explicit pairwise comparison when provided",
            "valuation_interpretation": "string - price-level implication and date/input limitation",
        },
        "cross_agent_consistency_check": {
            "confirmed_signals": ["string - one confirmed signal plus concrete basis"],
            "mixed_conflicting_signals": ["string - one conflicting signal plus concrete basis"],
            "strategy_implication": "string - conclusion plus concrete basis",
        },
        "peer_competitor_positioning": {
            "pairwise_findings": ["string - one finding that names both target and peer and compares a fact or metric supplied for both"],
            "comparison_limits": ["string - one material dimension unavailable for one side or otherwise not comparable"],
            "peer_based_investment_implication": "string - conclusion plus concrete basis",
        },
        "decision_balance": {
            "positive_evidence": ["string - supplied evidence supporting upside or resilience; empty only when none exists"],
            "negative_evidence": ["string - supplied evidence supporting downside or limiting upside; empty only when none exists"],
            "balance_conclusion": "string - why the chosen side outweighs, is outweighed, or remains balanced",
        },
        "final_rationale": {
            "why_buy_hold_sell": "string - final opinion plus concrete basis and risk balancing",
        },
        "limitations": {
            "data_limitations": [
                "string - unavailable, stale, mismatched-period, or insufficient source-data limitation"
            ],
            "interpretation_limitations": [
                "string - causal, generalization, comparability, or evidence-strength limitation"
            ],
            "monitoring_points": [
                "string - unresolved concrete event or variable whose future observation may change the view"
            ],
        },
        "source_files": {
            "target_financial": "string",
            "target_news": "string",
            "target_yfinance": "string",
            "peer_comparison": "string",
        },
    }


def strategy_decision_output_schema() -> dict[str, Any]:
    """Return required Decision Agent schema for prompting."""

    return {
        "strategy_report": strategy_report_schema(),
        "evidence_refs_by_section": {
            section: [decision_source_ref_schema()]
            for section in DECISION_REFERENCE_SECTIONS
        },
    }


def decision_source_ref_schema() -> dict[str, Any]:
    """Return one compact source reference generated with the report."""

    return {
        "agent": "Financial | News | YFinance | Competitor",
        "claim_id": "supplied claim id or blank",
        "source_section": "exact strategy_decision_packet path",
        "evidence_ids": ["supplied evidence id"],
    }


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


def decision_prompt_v2(
    profile: str = DEFAULT_DECISION_HORIZON_PROFILE,
) -> str:
    """Render the shared v2 prompt with one isolated horizon policy."""

    resolved = resolve_decision_horizon_profile(profile)
    template = read_prompt("decision_agent_v2.md")
    placeholder = "{{DECISION_HORIZON_POLICY}}"
    if template.count(placeholder) != 1:
        raise ValueError(
            "decision_agent_v2.md must contain exactly one horizon-policy placeholder."
        )
    return template.replace(placeholder, str(resolved["policy"]))


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


def _atomic_write_text(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(
        prefix=f".{path.name}.",
        suffix=".tmp",
        dir=path.parent,
    )
    temporary_path = Path(temporary)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as file:
            file.write(content)
            file.flush()
            os.fsync(file.fileno())
        os.replace(temporary_path, path)
    finally:
        if temporary_path.exists():
            temporary_path.unlink()


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


def render_inline_items(value: Any) -> str:
    """Render a list-like field as one Markdown line."""

    items = text_items(value)
    return "; ".join(items) if items else "N/A"


def render_observed_risks(value: Any) -> str:
    """Render categorized risk objects for the Markdown report."""

    rendered: list[str] = []
    for item in ensure_list(value):
        if not isinstance(item, dict):
            continue
        category = clean_text(item.get("category")) or "risk"
        statement = clean_text(item.get("statement"))
        if statement:
            rendered.append(f"[{category}] {statement}")
    return "; ".join(rendered) if rendered else "N/A"


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
