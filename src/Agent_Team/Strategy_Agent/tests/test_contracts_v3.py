from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import pytest

from Agent_Team.Strategy_Agent.agent import (
    decision_prompt_v3,
    load_news_evidence_catalog,
    run_strategy_agent,
    strategy_v3_fingerprint,
)
from Agent_Team.Strategy_Agent.contracts_v2 import build_compact_strategy_packet_v2
from Agent_Team.Strategy_Agent.contracts_v3 import (
    finalize_strategy_decision_v3,
    strategy_decision_response_format_v3,
    validate_strategy_decision_v3,
)
from Agent_Team.Strategy_Agent.tests.test_contracts_v2 import _bundle, _decision_output


def test_v3_schema_has_action_fields_and_no_opinion_field() -> None:
    packet, _provenance, _telemetry, _gate_a = build_compact_strategy_packet_v2(
        _bundle()
    )
    schema = strategy_decision_response_format_v3(
        packet,
        required_horizon="1개월",
    )["json_schema"]["schema"]
    decision = schema["properties"]["decision"]

    assert "opinion" not in decision["properties"]
    assert decision["properties"]["horizon"]["enum"] == ["1개월"]
    assert {
        "judgment",
        "current_response",
        "decisive_reason",
        "evidence_sufficiency",
        "decision_confidence",
    } == set(decision["properties"]) - {"horizon"}
    assert schema["properties"]["reassessment_conditions"]["minItems"] == 1


def test_news_catalog_falls_back_to_current_run_directory(tmp_path: Path) -> None:
    report_path = tmp_path / "News" / "현대모비스_20251031" / "final_report.json"
    catalog_path = report_path.parent / "output" / "news_agent_evidence_map.json"
    catalog_path.parent.mkdir(parents=True)
    catalog_path.write_text(
        json.dumps({"NEWS_001": {"title": "현대모비스 공급 계약"}}, ensure_ascii=False),
        encoding="utf-8",
    )
    report = {
        "output": {
            "evidence_map_path": (
                "/workspace/renamed-repository/Output_total/News/"
                "현대모비스_20251031/output/news_agent_evidence_map.json"
            )
        }
    }

    assert load_news_evidence_catalog(report, report_path) == {
        "NEWS_001": {"title": "현대모비스 공급 계약"}
    }


def test_v3_gate_accepts_grounded_action_and_reassessment_conditions() -> None:
    packet, provenance, _telemetry, _gate_a = build_compact_strategy_packet_v2(
        _bundle()
    )
    output = _v3_decision_output(packet)
    output["decision_basis"]["judgment_card_keys"].append(
        "financial.filing_basis"
    )

    gate_b = validate_strategy_decision_v3(
        output,
        packet=packet,
        provenance=provenance,
        required_horizon="1개월",
    )

    assert gate_b["status"] == "pass"
    assert gate_b["judgment_factor_count"] == 2
    assert gate_b["response_factor_count"] == 1
    assert gate_b["reassessment_condition_count"] == 1
    assert "opinion" not in output["decision"]


def test_v3_gate_rejects_fact_only_current_response() -> None:
    packet, provenance, _telemetry, _gate_a = build_compact_strategy_packet_v2(
        _bundle()
    )
    output = _v3_decision_output(packet)
    output["decision"]["current_response"] = "상대성과가 낮고 계산 배수가 높다."

    with pytest.raises(ValueError, match="concrete present response"):
        validate_strategy_decision_v3(
            output,
            packet=packet,
            provenance=provenance,
            required_horizon="1개월",
            experimental_prose_gate=True,
        )


def test_v3_gate_rejects_rating_label_in_reader_prose() -> None:
    packet, provenance, _telemetry, _gate_a = build_compact_strategy_packet_v2(
        _bundle()
    )
    output = _v3_decision_output(packet)
    output["decision"]["judgment"] = "현재는 Hold 의견이 적절하다."

    with pytest.raises(ValueError, match="Recommendation label leaked"):
        validate_strategy_decision_v3(
            output,
            packet=packet,
            provenance=provenance,
            required_horizon="1개월",
            experimental_prose_gate=True,
        )


def test_v3_gate_rejects_inflected_korean_rating_word() -> None:
    packet, provenance, _telemetry, _gate_a = build_compact_strategy_packet_v2(
        _bundle()
    )
    output = _v3_decision_output(packet)
    output["decision"]["current_response"] = "기존 보유자는 현재 비중을 유지한다."

    with pytest.raises(ValueError, match="Recommendation label leaked"):
        validate_strategy_decision_v3(
            output,
            packet=packet,
            provenance=provenance,
            required_horizon="1개월",
            experimental_prose_gate=True,
        )


def test_v3_production_gate_does_not_apply_lexical_prose_rules() -> None:
    packet, provenance, _telemetry, _gate_a = build_compact_strategy_packet_v2(
        _bundle()
    )
    output = _v3_decision_output(packet)
    output["decision"]["judgment"] = "현재는 Hold 의견으로 요약할 수 있다."
    output["decision"]["current_response"] = "상대성과가 낮고 계산 배수가 높다."

    gate_b = validate_strategy_decision_v3(
        output,
        packet=packet,
        provenance=provenance,
        required_horizon="1개월",
    )

    assert gate_b["status"] == "pass"
    assert gate_b["experimental_prose_gate"] is False


def test_v3_prompt_and_cache_fingerprint_change_by_horizon() -> None:
    packet, _provenance, _telemetry, _gate_a = build_compact_strategy_packet_v2(
        _bundle()
    )
    short_prompt = decision_prompt_v3("short_term")
    medium_prompt = decision_prompt_v3("medium_term")

    assert "1개월" in short_prompt
    assert "3개월" in medium_prompt
    assert "{{DECISION_HORIZON_POLICY}}" not in short_prompt
    assert strategy_v3_fingerprint(
        packet,
        llm_provider="openai",
        llm_model="test-model",
        decision_horizon_profile="short_term",
    ) != strategy_v3_fingerprint(
        packet,
        llm_provider="openai",
        llm_model="test-model",
        decision_horizon_profile="medium_term",
    )


def test_run_strategy_v3_writes_only_v3_decision(
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
    output_dir = tmp_path / "Strategy"
    output_dir.mkdir()
    (output_dir / "strategy_decision_output_v2.json").write_text(
        json.dumps({"stale": True}),
        encoding="utf-8",
    )

    with patch(
        "Agent_Team.Strategy_Agent.agent.build_strategy_input_bundle",
        return_value=bundle,
    ), patch(
        "Agent_Team.Strategy_Agent.agent.call_llm_json",
        return_value=_v3_raw_decision_output(packet),
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
            packet_version="v3",
            decision_horizon_profile="short_term",
        )

    assert mocked_llm.call_count == 1
    assert report["contract_version"] == "strategy_decision_output_v3"
    assert "final_recommendation" not in report
    assert report["decision"]["current_response"].startswith("현 가격에서는")
    assert (output_dir / "strategy_decision_output_v3.json").exists()
    assert not (output_dir / "strategy_decision_output_v2.json").exists()


def _v3_raw_decision_output(packet: dict) -> dict:
    old = _decision_output(packet)
    assessments = {
        item["card_key"]: {
            key: value
            for key, value in item.items()
            if key not in {"card_key", "direction"}
        }
        for item in old["evidence_assessments"]
    }
    return {
        "decision_version": "strategy_decision_output_v3",
        "decision": {
            "horizon": "1개월",
            "judgment": "재무 개선보다 현재의 상대성과와 가치평가 부담을 우선해서 볼 필요가 있다.",
            "current_response": "현 가격에서는 비중 확대를 서두르지 않고 상대성과 회복을 확인한다.",
            "decisive_reason": "계산 배수 부담이 재무 개선 효과의 가격 반영 여지를 제한한다.",
            "evidence_sufficiency": "medium",
            "decision_confidence": "medium",
        },
        "decision_basis": {
            "judgment_card_keys": [
                "financial.same_period_trend",
                "peer.valuation",
            ],
            "current_response_card_keys": ["peer.valuation"],
            "decisive_reason_card_keys": ["peer.valuation"],
            "counter_evidence": "재무 지표의 같은 기간 개선은 반대 근거다.",
            "counter_evidence_card_keys": ["financial.same_period_trend"],
            "current_price_context": "선택일 계산 배수와 시장 가격을 함께 고려했다.",
            "current_price_card_keys": ["valuation.selected_date"],
        },
        "reassessment_conditions": [
            {
                "signal": "KOSPI 대비 상대성과의 회복 여부",
                "response_if_confirmed": "회복이 확인되면 비중 확대 여부를 재검토한다.",
                "response_if_not_confirmed": "회복되지 않으면 현재의 신중한 대응을 유지한다.",
                "basis_card_keys": ["market.relative_performance"],
            }
        ],
        "evidence_assessments": assessments,
        "peer_findings": [
            {
                key: value
                for key, value in old["peer_findings"][0].items()
                if key not in {"comparison_basis", "direction"}
            }
        ],
        "decision_risk_factors": [
            {
                key: value
                for key, value in old["decision_risk_factors"][0].items()
                if key not in {"reader_summary", "scope_qualifier"}
            }
        ],
    }


def _v3_decision_output(packet: dict) -> dict:
    return finalize_strategy_decision_v3(_v3_raw_decision_output(packet), packet)
