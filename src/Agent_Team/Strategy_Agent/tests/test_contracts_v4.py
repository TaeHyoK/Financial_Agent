from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

from Agent_Team.Strategy_Agent.agent import run_strategy_agent
from Agent_Team.Strategy_Agent.contracts_v2 import build_compact_strategy_packet_v2
from Agent_Team.Strategy_Agent.contracts_v4 import (
    build_strategy_context_package_v4,
    strategy_decision_response_format_v4,
    validate_strategy_decision_v4,
)
from Agent_Team.Strategy_Agent.tests.test_contracts_v2 import _bundle


def test_v4_context_preserves_handoffs_and_removes_decision_policy_fields() -> None:
    bundle = _bundle()
    bundle["target_reports"]["financial"]["main_view"] = {
        "summary": "재무 개선과 외형 둔화가 함께 나타난다.",
        "primary_basis": ["영업이익 증가", "매출 감소"],
    }
    bundle["target_reports"]["financial"]["secondary_context_assessment"] = [
        {
            "context_id": "news_context_1",
            "source_domain": "news",
            "statement": "최근 사업 뉴스는 공시기간 이후의 사건이다.",
            "primary_evidence_ids": ["E001"],
            "secondary_evidence_ids": ["NEWS_RAW_1"],
            "usage": "framing_and_limitation_only",
        }
    ]
    bundle["target_reports"]["news"]["output"][
        "secondary_context_assessment"
    ] = [
        {
            "context_id": "financial_context_1",
            "source_domain": "financial",
            "statement": "재무자료는 사건 발생 전의 기초 체력을 보여준다.",
            "primary_evidence_ids": ["NEWS_RAW_1"],
            "secondary_evidence_ids": ["E001"],
            "usage": "framing_and_limitation_only",
        }
    ]
    packet, _provenance, _telemetry, _gate_a = build_compact_strategy_packet_v2(
        bundle
    )

    context = build_strategy_context_package_v4(packet, input_bundle=bundle)
    card = context["evidence_cards"]["financial.same_period_trend"]

    assert context["context_version"] == "strategy_context_package_v4"
    assert context["domain_handoffs"]["financial"]["main_view"]["summary"]
    financial_cross = context["domain_handoffs"]["financial"][
        "cross_domain_assessments"
    ][0]
    news_cross = context["domain_handoffs"]["news"][
        "cross_domain_assessments"
    ][0]
    assert financial_cross["statement"] == "최근 사업 뉴스는 공시기간 이후의 사건이다."
    assert news_cross["statement"] == "재무자료는 사건 발생 전의 기초 체력을 보여준다."
    assert "primary_evidence_ids" not in financial_cross
    assert "secondary_evidence_ids" not in news_cross
    assert set(context["evidence_cards"]) == set(packet["cards"])
    assert {"allowed_sections", "decision_use", "eligibility"}.isdisjoint(card)
    peer_pair = context["evidence_cards"]["peer.valuation"]["primary_observation"][
        "pairs"
    ][0]
    assert "allowed_interpretation" not in peer_pair
    assert "preferred_direction" not in peer_pair


def test_v4_schema_asks_llm_to_select_basis_instead_of_assessing_every_card() -> None:
    context = _context()
    schema = strategy_decision_response_format_v4(
        context,
        required_horizon="1개월",
    )["json_schema"]["schema"]

    assert "evidence_assessments" not in schema["properties"]
    assert "reassessment_conditions" not in schema["properties"]
    assert "basis_cards" in schema["properties"]
    assert "strategy_brief" in schema["properties"]
    assert "current_implication" in schema["properties"]["key_risks"]["items"][
        "properties"
    ]
    assert "monitoring_point" not in schema["properties"]["key_risks"]["items"][
        "properties"
    ]
    serialized = str(schema)
    assert "materiality" not in serialized
    assert "investment_effect" not in serialized
    assert "allowed_sections" not in serialized


def test_v4_integrity_gate_accepts_agent_judgment_without_lexical_rules() -> None:
    context = _context()
    output = _v4_output(context)
    output["strategy_brief"]["thesis"] = "Hold라는 표현이 있어도 운영 무결성 검사는 문체를 평가하지 않는다."
    output["strategy_brief"]["existing_position_response"] = "상대성과가 낮다."

    gate_b = validate_strategy_decision_v4(
        output,
        context=context,
        required_horizon="1개월",
    )

    assert gate_b["status"] == "pass"
    assert gate_b["selected_basis_card_count"] == 3
    assert gate_b["available_card_count"] > gate_b["selected_basis_card_count"]


def test_v4_integrity_gate_rejects_unknown_or_unselected_references() -> None:
    context = _context()
    output = _v4_output(context)
    output["rationale"][0]["basis_card_keys"] = ["valuation.selected_date"]

    with pytest.raises(ValueError, match="unselected card"):
        validate_strategy_decision_v4(
            output,
            context=context,
            required_horizon="1개월",
        )


def test_run_strategy_v4_is_default_and_renders_only_the_agent_brief(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("STRATEGY_PACKET_VERSION", raising=False)
    bundle = _bundle()
    bundle["peer_comparison"]["source_path"] = "/tmp/peer.json"
    bundle["evidence_hierarchy"] = [{"rank": 1, "source": "financial"}]
    packet, _provenance, _telemetry, _gate_a = build_compact_strategy_packet_v2(
        bundle
    )
    context = build_strategy_context_package_v4(packet, input_bundle=bundle)
    output_dir = tmp_path / "Strategy"
    output_dir.mkdir()
    (output_dir / "strategy_decision_output_v3.json").write_text(
        "{}",
        encoding="utf-8",
    )

    with patch(
        "Agent_Team.Strategy_Agent.agent.build_strategy_input_bundle",
        return_value=bundle,
    ), patch(
        "Agent_Team.Strategy_Agent.agent.call_llm_json",
        return_value=_v4_output(context),
    ) as mocked_llm:
        report = run_strategy_agent(
            target_company_name="대상기업",
            target_run_key="대상기업_20251031",
            target_financial_path=tmp_path / "financial.json",
            target_news_path=tmp_path / "news.json",
            target_yfinance_path=tmp_path / "yfinance.json",
            peer_comparison_path=tmp_path / "peer.json",
            output_dir=output_dir,
            llm_provider="openai",
            llm_model="test-model",
            env_file=None,
            decision_horizon_profile="short_term",
        )

    markdown = (output_dir / "strategy_report.md").read_text(encoding="utf-8")
    assert mocked_llm.call_count == 1
    assert report["contract_version"] == "strategy_decision_output_v4"
    assert (output_dir / "strategy_decision_output_v4.json").exists()
    assert not (output_dir / "strategy_decision_output_v3.json").exists()
    assert "## 결론" in markdown
    assert "## 현재 대응" in markdown
    assert "## 주요 근거 평가" not in markdown
    assert "financial.same_period_trend" not in markdown


def _context() -> dict:
    bundle = _bundle()
    packet, _provenance, _telemetry, _gate_a = build_compact_strategy_packet_v2(
        bundle
    )
    return build_strategy_context_package_v4(packet, input_bundle=bundle)


def _v4_output(context: dict) -> dict:
    assert "financial.same_period_trend" in context["evidence_cards"]
    return {
        "decision_version": "strategy_decision_output_v4",
        "strategy_brief": {
            "horizon": "1개월",
            "thesis": "재무 개선보다 상대성과 부진을 우선해 신중한 대응이 필요한 구간이다.",
            "existing_position_response": "현재 비중을 유지하며 상대성과 회복을 관찰한다.",
            "new_entry_response": "시장 대비 흐름이 반전될 때까지 진입 시점을 늦춘다.",
            "price_context": "현재 배수 부담은 크지 않지만 상대성과가 약하다.",
            "counterview": "같은 기간 재무 개선은 판단의 반대 근거다.",
            "limitation_summary": "상대성과 부진이 일시적인 지수 주도 흐름인지 회사 고유의 약세인지 구분하기 어렵다.",
            "evidence_sufficiency": "medium",
            "decision_confidence": "medium",
        },
        "rationale": [
            {
                "point": "시장 대비 상대성과가 단기 판단을 제약한다.",
                "basis_card_keys": ["market.relative_performance"],
            },
            {
                "point": "재무 개선은 하방을 제한한다.",
                "basis_card_keys": ["financial.same_period_trend"],
            },
        ],
        "basis_cards": [
            {
                "card_key": "market.relative_performance",
                "role": "primary",
                "usage_reason": "단기 시장 선호를 판단하기 위해 선택했다.",
            },
            {
                "card_key": "financial.same_period_trend",
                "role": "counter",
                "usage_reason": "상대 약세의 반대 근거로 선택했다.",
            },
            {
                "card_key": "market.momentum_volume",
                "role": "monitoring",
                "usage_reason": "상대성과 반전 여부를 관찰하기 위해 선택했다.",
            },
        ],
        "key_risks": [
            {
                "risk": "상대 약세가 장기화될 수 있다.",
                "current_implication": "1개월 관점의 적극적인 대응 근거를 약화시킨다.",
                "basis_card_keys": ["market.relative_performance"],
            }
        ],
    }
