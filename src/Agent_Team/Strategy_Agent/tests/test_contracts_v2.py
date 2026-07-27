from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import pytest

from Agent_Team.Strategy_Agent import cli as strategy_cli
from Agent_Team.Strategy_Agent.agent import (
    build_strategy_generation_payload_v2,
    decision_generation_prompt_v2,
    decision_prompt_v2,
    run_strategy_agent,
    strategy_v2_fingerprint,
)
from Agent_Team.Strategy_Agent.contracts_v2 import (
    build_compact_strategy_packet_v2,
    build_peer_pair_cards,
    finalize_strategy_decision_v2,
    strategy_decision_response_format_v2,
    validate_strategy_decision_v2,
)


def test_strategy_cli_retries_one_post_gate_a_failure(tmp_path: Path, monkeypatch) -> None:
    output_dir = tmp_path / "Strategy"
    calls = {"count": 0}

    def fake_run_strategy_agent(**_kwargs):
        calls["count"] += 1
        if calls["count"] == 1:
            output_dir.mkdir(parents=True, exist_ok=True)
            (output_dir / "strategy_failure_report_v2.json").write_text(
                json.dumps({"status": "fail", "stage": "gate_b"}),
                encoding="utf-8",
            )
            raise ValueError("Gate B failed")
        return {
            "target_company_name": "대상기업",
            "target_run_key": "대상기업_20251031",
            "final_recommendation": {"opinion": "Hold", "investment_horizon": "1개월"},
        }

    monkeypatch.setattr(strategy_cli, "run_strategy_agent", fake_run_strategy_agent)

    result = strategy_cli.main(
        [
            "--target-company-name",
            "대상기업",
            "--target-run-key",
            "대상기업_20251031",
            "--target-financial",
            str(tmp_path / "financial.json"),
            "--target-news",
            str(tmp_path / "news.json"),
            "--target-yfinance",
            str(tmp_path / "yfinance.json"),
            "--output-dir",
            str(output_dir),
            "--packet-version",
            "v2",
            "--decision-horizon-profile",
            "short_term",
            "--semantic-attempts",
            "2",
        ]
    )

    assert result == 0
    assert calls["count"] == 2
    assert (output_dir / "attempts/attempt_01/strategy_failure_report_v2.json").exists()


def test_compact_packet_is_self_contained_and_keeps_raw_ids_only_in_provenance() -> None:
    packet, provenance, telemetry, gate_a = build_compact_strategy_packet_v2(_bundle())

    serialized = json.dumps(packet, ensure_ascii=False)
    assert packet["packet_version"] == "strategy_compact_packet_v2"
    assert "E001" not in serialized
    assert "NEWS_RAW_2025-10-21_1" not in serialized
    assert provenance["cards"]["financial.same_period_trend"]["source_evidence_ids"] == [
        "E001"
    ]
    assert packet["cards"]["valuation.selected_date"]["primary_observation"]["as_of_date"] == "2025-10-30"
    assert packet["cards"]["peer.valuation"]["primary_observation"]["pairs"][0]["comparability"] == "comparable"
    assert packet["cards"]["market.relative_performance"]["comparison_label"] == "KOSPI 대비"
    assert packet["cards"]["peer.valuation"]["comparison_entities"]["peer_companies"] == ["비교기업"]
    assert telemetry["serialized_bytes"] > 0
    assert gate_a["status"] == "pass"


def test_full_context_ablation_keeps_compact_contract_and_adds_reports() -> None:
    bundle = _bundle()
    packet, _provenance, _telemetry, _gate_a = build_compact_strategy_packet_v2(bundle)

    compact = build_strategy_generation_payload_v2(
        input_bundle=bundle,
        compact_packet=packet,
        context_mode="compact_cards",
    )
    full = build_strategy_generation_payload_v2(
        input_bundle=bundle,
        compact_packet=packet,
        context_mode="full_reports",
    )
    prompt = decision_generation_prompt_v2("default", context_mode="full_reports")

    assert compact == {"strategy_compact_packet_v2": packet}
    assert full["strategy_compact_packet_v2"] == packet
    assert full["full_context_ablation"]["target_reports"] == bundle["target_reports"]
    assert "input_metadata" not in full["full_context_ablation"]
    assert "Full-context ablation" in prompt


def test_secondary_context_is_framing_only_and_not_an_independent_card() -> None:
    packet, _provenance, _telemetry, _gate_a = build_compact_strategy_packet_v2(_bundle())

    card = packet["cards"]["financial.same_period_trend"]
    assert card["secondary_context"][0]["usage"] == "framing_only"
    assert all(not key.startswith("context.") for key in packet["cards"])


def test_peer_pair_rejects_mismatched_valuation_dates() -> None:
    peer = _peer_comparison()
    peer["metrics"][1]["valuation_metrics"]["calculated_as_of_date"] = "2025-10-29"

    cards = {card["card_key"]: card for card, _ids, _paths in build_peer_pair_cards(peer)}
    valuation_pairs = cards["peer.valuation"]["primary_observation"]["pairs"]

    assert all(pair["comparability"] == "incomparable" for pair in valuation_pairs)
    assert cards["peer.valuation"]["eligibility"] == "incomparable"


def test_strategy_gate_b_preserves_card_refs_and_peer_basis() -> None:
    packet, provenance, _telemetry, _gate_a = build_compact_strategy_packet_v2(_bundle())
    output = _decision_output(packet)

    result = validate_strategy_decision_v2(output, packet=packet, provenance=provenance)
    schema = strategy_decision_response_format_v2(packet)["json_schema"]["schema"]

    assert result["status"] == "pass"
    assert result["assessment_count"] == len(packet["cards"])
    assert "strategy_report" not in schema["properties"]
    assessment_map = schema["properties"]["evidence_assessments"]
    assert assessment_map["type"] == "object"
    assert assessment_map["required"] == sorted(packet["cards"])
    assert assessment_map["additionalProperties"] is False
    assert set(assessment_map["properties"]) == set(packet["cards"])
    reference_assessment = assessment_map["properties"]["valuation.provider_reference"]
    assert "card_key" not in reference_assessment["properties"]
    assert reference_assessment["properties"]["materiality"]["enum"] == ["context"]


def test_finalize_materializes_card_keyed_assessments_without_duplicates() -> None:
    packet, provenance, _telemetry, _gate_a = build_compact_strategy_packet_v2(_bundle())
    output = _decision_output(packet)
    output["evidence_assessments"] = {
        assessment["card_key"]: {
            key: value
            for key, value in assessment.items()
            if key not in {"card_key", "direction"}
        }
        for assessment in output["evidence_assessments"]
    }

    finalized = finalize_strategy_decision_v2(output, packet)
    card_keys = [item["card_key"] for item in finalized["evidence_assessments"]]

    assert card_keys == sorted(packet["cards"])
    assert len(card_keys) == len(set(card_keys))
    assert validate_strategy_decision_v2(
        finalized,
        packet=packet,
        provenance=provenance,
    )["status"] == "pass"


def test_horizon_profiles_change_prompt_schema_and_cache_fingerprint() -> None:
    packet, _provenance, _telemetry, _gate_a = build_compact_strategy_packet_v2(_bundle())
    profiles = {
        "unspecified": "기간 미지정",
        "short_term": "1개월",
        "medium_term": "3개월",
        "long_term": "6개월",
    }
    fingerprints = set()

    for profile, horizon in profiles.items():
        prompt = decision_prompt_v2(profile)
        response_format = strategy_decision_response_format_v2(
            packet,
            required_horizon=horizon,
        )
        horizon_schema = response_format["json_schema"]["schema"]["properties"][
            "decision"
        ]["properties"]["horizon"]

        assert "{{DECISION_HORIZON_POLICY}}" not in prompt
        assert horizon in prompt
        assert horizon_schema["enum"] == [horizon]
        fingerprints.add(
            strategy_v2_fingerprint(
                packet,
                llm_provider="openai",
                llm_model="test-model",
                decision_horizon_profile=profile,
            )
        )

    assert len(fingerprints) == len(profiles)


def test_strategy_schema_matches_gate_b_card_and_text_constraints() -> None:
    packet, _provenance, _telemetry, _gate_a = build_compact_strategy_packet_v2(_bundle())
    schema = strategy_decision_response_format_v2(packet)["json_schema"]["schema"]
    bridge = schema["properties"]["recommendation_bridge"]["properties"]
    assessments = schema["properties"]["evidence_assessments"]["properties"]
    risk = schema["properties"]["decision_risk_factors"]["items"]["properties"]
    expected_price_keys = sorted(
        card_key
        for card_key, card in packet["cards"].items()
        if card["domain"] in {"market", "valuation"}
        or (
            card["domain"] == "peer"
            and card["card_type"] in {"market_relative", "valuation"}
        )
    )

    assert bridge["current_price_card_keys"]["items"]["enum"] == expected_price_keys
    assert bridge["current_price_card_keys"]["maxItems"] == len(expected_price_keys)
    assert bridge["current_price_rationale"]["pattern"] == r"\S"
    assert assessments["financial.same_period_trend"]["properties"]["interpretation"][
        "pattern"
    ] == r"\S"
    assert risk["basis_card_keys"]["minItems"] == 1
    assert risk["risk_summary"]["pattern"] == r"\S"
    assert risk["monitoring_point"]["pattern"] == r"\S"

    def schema_keywords(value: object) -> set[str]:
        if isinstance(value, dict):
            return set(value).union(
                *(schema_keywords(item) for item in value.values())
            )
        if isinstance(value, list):
            return set().union(*(schema_keywords(item) for item in value))
        return set()

    # OpenAI strict Structured Outputs does not support these general JSON Schema keywords.
    assert {"minLength", "uniqueItems"}.isdisjoint(schema_keywords(schema))


def test_finalize_deduplicates_model_owned_references_before_gate_b() -> None:
    packet, provenance, _telemetry, _gate_a = build_compact_strategy_packet_v2(_bundle())
    output = _decision_output(packet)
    output["recommendation_bridge"]["current_price_card_keys"] *= 2
    output["decision_risk_factors"][0]["basis_card_keys"] *= 2
    output["peer_findings"].append(
        json.loads(json.dumps(output["peer_findings"][0], ensure_ascii=False))
    )

    finalized = finalize_strategy_decision_v2(output, packet)

    assert finalized["recommendation_bridge"]["current_price_card_keys"] == [
        "valuation.selected_date"
    ]
    assert finalized["decision_risk_factors"][0]["basis_card_keys"] == [
        "peer.valuation"
    ]
    assert len(finalized["peer_findings"]) == 1
    assert validate_strategy_decision_v2(
        finalized,
        packet=packet,
        provenance=provenance,
    )["status"] == "pass"


def test_gate_b_rejects_required_horizon_mismatch() -> None:
    packet, provenance, _telemetry, _gate_a = build_compact_strategy_packet_v2(_bundle())
    output = _decision_output(packet)

    with pytest.raises(ValueError, match="horizon mismatch"):
        validate_strategy_decision_v2(
            output,
            packet=packet,
            provenance=provenance,
            required_horizon="1개월",
        )


def test_strategy_gate_b_rejects_incomparable_peer_finding() -> None:
    bundle = _bundle()
    bundle["peer_comparison"]["metrics"][1]["valuation_metrics"]["calculated_as_of_date"] = "2025-10-29"
    packet, provenance, _telemetry, _gate_a = build_compact_strategy_packet_v2(bundle)
    output = _decision_output(packet)
    output["peer_findings"][0] = {
        "basis_card_key": "peer.valuation",
        "metric_key": "trailing_pe",
        "peer_company": "비교기업",
        "comparison_basis": "2025-10-30",
        "direction": "target_advantage",
        "investment_effect": "positive",
        "finding": "날짜가 다른 배수를 비교했다.",
    }

    import pytest

    with pytest.raises(ValueError, match="[Ii]ncomparable"):
        validate_strategy_decision_v2(output, packet=packet, provenance=provenance)


def test_strategy_gate_b_does_not_match_product_scope_phrases() -> None:
    packet, provenance, _telemetry, _gate_a = build_compact_strategy_packet_v2(_bundle())
    output = _decision_output(packet)
    assessment = next(
        item
        for item in output["evidence_assessments"]
        if item["card_key"] == "financial.product_breakdown"
    )
    assessment["interpretation"] = "제품A가 전체 매출의 대부분을 차지한다."

    gate_b = validate_strategy_decision_v2(output, packet=packet, provenance=provenance)

    assert gate_b["status"] == "pass"


def test_run_strategy_v2_uses_one_llm_call_and_writes_only_v2_contract(tmp_path: Path) -> None:
    bundle = _bundle()
    bundle["peer_comparison"]["source_path"] = "/tmp/peer.json"
    bundle["evidence_hierarchy"] = [{"rank": 1, "source": "financial"}]
    packet, _provenance, _telemetry, _gate_a = build_compact_strategy_packet_v2(bundle)

    with patch(
        "Agent_Team.Strategy_Agent.agent.build_strategy_input_bundle",
        return_value=bundle,
    ), patch(
        "Agent_Team.Strategy_Agent.agent.call_llm_json",
        return_value=_decision_output(packet),
    ) as mocked_llm:
        report = run_strategy_agent(
            target_company_name="대상기업",
            target_run_key="대상기업_20251031",
            target_financial_path=tmp_path / "financial.json",
            target_news_path=tmp_path / "news.json",
            target_yfinance_path=tmp_path / "yfinance.json",
            peer_comparison_path=tmp_path / "peer.json",
            output_dir=tmp_path / "Strategy",
            llm_provider="openai",
            llm_model="test-model",
            env_file=None,
            packet_version="v2",
        )

    assert mocked_llm.call_count == 1
    assert report["contract_version"] == "strategy_decision_output_v2"
    assert (tmp_path / "Strategy" / "strategy_compact_packet_v2.json").exists()
    assert (tmp_path / "Strategy" / "strategy_packet_provenance_v2.json").exists()
    assert (tmp_path / "Strategy" / "strategy_decision_output_v2.json").exists()
    assert not (tmp_path / "Strategy" / "strategy_content_plan.json").exists()


def test_run_strategy_v2_revalidates_cached_horizon_against_profile(tmp_path: Path) -> None:
    bundle = _bundle()
    bundle["peer_comparison"]["source_path"] = "/tmp/peer.json"
    bundle["evidence_hierarchy"] = [{"rank": 1, "source": "financial"}]
    packet, _provenance, _telemetry, _gate_a = build_compact_strategy_packet_v2(bundle)
    short_term_output = _decision_output(packet)
    short_term_output["decision"]["horizon"] = "1개월"
    output_dir = tmp_path / "Strategy"

    with patch(
        "Agent_Team.Strategy_Agent.agent.build_strategy_input_bundle",
        return_value=bundle,
    ), patch(
        "Agent_Team.Strategy_Agent.agent.call_llm_json",
        return_value=short_term_output,
    ) as mocked_llm:
        run_strategy_agent(
            target_company_name="대상기업",
            target_run_key="대상기업_20251031",
            target_financial_path=tmp_path / "financial.json",
            target_news_path=tmp_path / "news.json",
            target_yfinance_path=tmp_path / "yfinance.json",
            output_dir=output_dir,
            llm_provider="openai",
            llm_model="test-model",
            env_file=None,
            packet_version="v2",
            decision_horizon_profile="short_term",
        )

        cached_path = output_dir / "strategy_decision_output_v2.json"
        cached_output = json.loads(cached_path.read_text(encoding="utf-8"))
        cached_output["decision"]["horizon"] = "6~12개월"
        cached_path.write_text(
            json.dumps(cached_output, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )

        with pytest.raises(ValueError, match="horizon mismatch"):
            run_strategy_agent(
                target_company_name="대상기업",
                target_run_key="대상기업_20251031",
                target_financial_path=tmp_path / "financial.json",
                target_news_path=tmp_path / "news.json",
                target_yfinance_path=tmp_path / "yfinance.json",
                output_dir=output_dir,
                llm_provider="openai",
                llm_model="test-model",
                env_file=None,
                packet_version="v2",
                decision_horizon_profile="short_term",
            )

    assert mocked_llm.call_count == 1
    assert (output_dir / "strategy_decision_output_v2.failed.json").exists()


def test_gate_b_uses_comparison_metadata_instead_of_prose_keywords() -> None:
    packet, provenance, _telemetry, _gate_a = build_compact_strategy_packet_v2(_bundle())
    output = _decision_output(packet)
    assessment = next(
        item
        for item in output["evidence_assessments"]
        if item["card_key"] == "market.relative_performance"
    )
    assessment["interpretation"] = "동종 대비 상대성과가 약하다."

    gate_b = validate_strategy_decision_v2(output, packet=packet, provenance=provenance)

    assert gate_b["status"] == "pass"


def test_factor_selection_deduplicates_same_evidence_family() -> None:
    packet, _provenance, _telemetry, _gate_a = build_compact_strategy_packet_v2(_bundle())
    output = _decision_output(packet)
    peer_growth = next(
        item
        for item in output["evidence_assessments"]
        if item["card_key"] == "peer.revenue_growth"
    )
    peer_growth["investment_effect"] = "positive"
    peer_growth["direction"] = "positive"
    peer_growth["materiality"] = "decisive"

    finalized = finalize_strategy_decision_v2(output, packet)

    selected = finalized["decision"]["positive_factor_card_keys"]
    assert "financial.same_period_trend" in selected
    assert "peer.revenue_growth" not in selected


def test_context_only_news_cannot_be_decisive() -> None:
    packet, provenance, _telemetry, _gate_a = build_compact_strategy_packet_v2(_bundle())
    output = _decision_output(packet)
    news_key = next(key for key, card in packet["cards"].items() if card["domain"] == "news")
    assert packet["cards"][news_key]["decision_use"] == "context_only"
    assessment = next(
        item for item in output["evidence_assessments"] if item["card_key"] == news_key
    )
    assessment["materiality"] = "decisive"
    assessment["investment_effect"] = "positive"
    assessment["direction"] = "positive"

    with pytest.raises(ValueError, match="Context-only"):
        validate_strategy_decision_v2(output, packet=packet, provenance=provenance)


def test_finalize_removes_ineligible_forward_support_keys() -> None:
    packet, provenance, _telemetry, _gate_a = build_compact_strategy_packet_v2(_bundle())
    output = _decision_output(packet)
    news_key = next(key for key, card in packet["cards"].items() if card["domain"] == "news")
    output["recommendation_bridge"]["forward_support_card_keys"].extend(
        [news_key, "financial.product_breakdown"]
    )

    finalized = finalize_strategy_decision_v2(output, packet)

    assert finalized["recommendation_bridge"]["forward_support_card_keys"] == [
        "financial.same_period_trend",
        "peer.valuation",
    ]
    assert validate_strategy_decision_v2(
        finalized,
        packet=packet,
        provenance=provenance,
    )["status"] == "pass"


def test_finalize_removes_nonvaluation_key_from_valuation_bridge() -> None:
    packet, provenance, _telemetry, _gate_a = build_compact_strategy_packet_v2(_bundle())
    output = _decision_output(packet)
    output["recommendation_bridge"]["valuation_card_keys"].append(
        "market.absolute_trend"
    )

    finalized = finalize_strategy_decision_v2(output, packet)

    assert "market.absolute_trend" not in finalized["recommendation_bridge"][
        "valuation_card_keys"
    ]
    assert validate_strategy_decision_v2(
        finalized,
        packet=packet,
        provenance=provenance,
    )["status"] == "pass"


def test_peer_cards_support_multiple_selected_companies_without_industry_aggregation() -> None:
    comparison = _peer_comparison()
    second_peer = json.loads(json.dumps(comparison["metrics"][1], ensure_ascii=False))
    second_peer["company_name"] = "비교기업2"
    comparison["metrics"].append(second_peer)

    cards = {card["card_key"]: card for card, _ids, _paths in build_peer_pair_cards(comparison)}
    valuation = cards["peer.valuation"]

    assert valuation["comparison_scope"] == "selected_peer"
    assert valuation["comparison_entities"]["peer_companies"] == ["비교기업", "비교기업2"]
    assert {pair["peer_company"] for pair in valuation["primary_observation"]["pairs"]} == {
        "비교기업",
        "비교기업2",
    }


def test_directional_recommendation_with_one_forward_family_is_advisory() -> None:
    packet, provenance, _telemetry, _gate_a = build_compact_strategy_packet_v2(_bundle())
    output = _decision_output(packet)
    output["decision"]["opinion"] = "Buy"
    output["recommendation_bridge"]["forward_support_card_keys"] = [
        "financial.same_period_trend"
    ]

    result = validate_strategy_decision_v2(output, packet=packet, provenance=provenance)

    assert result["status"] == "pass"
    assert result["blocking_failures"] == []
    assert any("fewer than two independent" in note for note in result["advisories"])


def test_gate_b_does_not_require_available_price_valuation_or_forward_cards() -> None:
    packet, provenance, _telemetry, _gate_a = build_compact_strategy_packet_v2(_bundle())
    output = _decision_output(packet)
    bridge = output["recommendation_bridge"]
    bridge["current_price_card_keys"] = []
    bridge["valuation_card_keys"] = []
    bridge["forward_support_card_keys"] = []

    result = validate_strategy_decision_v2(output, packet=packet, provenance=provenance)

    assert result["status"] == "pass"
    assert result["advisory_count"] == 3
    assert any("price evidence" in note for note in result["advisories"])
    assert any("valuation evidence" in note for note in result["advisories"])
    assert any("factor evidence" in note for note in result["advisories"])


def test_news_only_ablation_allows_explicitly_missing_price_and_valuation() -> None:
    bundle = _bundle()
    bundle["target_reports"]["financial"] = {}
    bundle["target_reports"]["yfinance"] = {}
    bundle["target_validation_evidence"]["financial"] = {"claims": []}
    bundle["target_validation_evidence"]["yfinance"] = {"claims": []}
    bundle["peer_comparison"] = {}
    bundle["ablation"] = {
        "experiment_name": "only_news",
        "included_domains": ["news"],
        "use_sy": True,
        "primary_data_only": False,
        "include_competitor": True,
    }
    packet, provenance, _telemetry, _gate_a = build_compact_strategy_packet_v2(bundle)
    section_card_keys = {section: [] for section in packet["section_inputs"]}
    assessments = []
    for card_key, card in packet["cards"].items():
        section = next(
            section
            for section in card["allowed_sections"]
            if section in section_card_keys
        )
        effect = "reference" if card["evidence_role"] == "reference" else "neutral"
        assessments.append(
            {
                "card_key": card_key,
                "section": section,
                "materiality": "context",
                "interpretation": f"{card['label']}은 뉴스 문맥으로만 사용한다.",
                "investment_effect": effect,
            }
        )
        section_card_keys[section].append(card_key)
    output = finalize_strategy_decision_v2(
        {
            "decision_version": "strategy_decision_output_v2",
            "decision": {
                "opinion": "Hold",
                "horizon": "6~12개월",
                "evidence_sufficiency": "low",
            },
            "recommendation_bridge": {
                "current_price_rationale": "시장 가격 데이터는 이 ablation에서 제외됐다.",
                "current_price_card_keys": [],
                "forward_support": "투자 factor로 사용할 수 있는 forward 근거가 남지 않았다.",
                "forward_support_card_keys": [],
                "valuation_counterweight": "밸류에이션 데이터는 이 ablation에서 제외됐다.",
                "valuation_card_keys": [],
                "residual_uncertainty": "뉴스 사건의 재무 연결이 확인되지 않았다.",
                "uncertainty_card_keys": list(packet["cards"])[:1],
                "decision_confidence": "low",
            },
            "evidence_assessments": assessments,
            "peer_findings": [],
            "decision_risk_factors": [],
        },
        packet,
    )

    result = validate_strategy_decision_v2(output, packet=packet, provenance=provenance)

    assert result["status"] == "pass"
    assert {card["domain"] for card in packet["cards"].values()} == {"news"}
    assert output["recommendation_bridge"]["current_price_card_keys"] == []
    assert output["recommendation_bridge"]["valuation_card_keys"] == []


def _bundle() -> dict:
    return {
        "target_company": {
            "company_name": "대상기업",
            "run_key": "대상기업_20251031",
            "as_of_date": "2025-10-31",
        },
        "target_reports": {
            "financial": _financial_report(),
            "news": _news_report(),
            "yfinance": _yfinance_report(),
        },
        "target_validation_evidence": {
            "financial": {"claims": []},
            "news": {
                "claims": [
                    {
                        "section": "analysis_blocks.news_only.positive_signals[0]",
                        "claim": "신규 사업 발표",
                        "evidence_use": "strong",
                        "evidence_ids": ["NEWS_RAW_2025-10-21_1"],
                        "event_status": "announced",
                        "company_specificity": "direct",
                        "materiality_status": "plausible_unquantified",
                        "financial_link_status": "not_observed",
                        "limitations": [],
                    }
                ]
            },
            "yfinance": {"claims": []},
        },
        "evidence_catalogs": {
            "financial": {},
            "news": {
                "NEWS_RAW_2025-10-21_1": {
                    "evidence_id": "NEWS_RAW_2025-10-21_1",
                    "domain": "news",
                    "source_ref": "news_events.2025-10-21.1",
                    "source_date": "2025-10-21",
                    "title": "대상기업 신규 사업 발표",
                    "snippet": "대상기업이 신규 사업을 추진한다고 발표했다.",
                    "source": "테스트뉴스",
                    "mention_count": 2,
                    "coverage": {
                        "article_count": 2,
                        "publisher_names": ["테스트뉴스", "다른뉴스"],
                        "unique_publisher_count": 2,
                        "deduplicated_article_count": 1,
                        "primary_source_present": False,
                        "coverage_quality": "verified",
                    },
                }
            },
            "yfinance": {},
        },
        "peer_comparison": _peer_comparison(),
        "decision_constraints": ["뉴스의 재무 기여 규모는 확인되지 않았다."],
        "input_metadata": {
            "target_financial_path": "/tmp/financial.json",
            "target_news_path": "/tmp/news.json",
            "target_yfinance_path": "/tmp/yfinance.json",
            "peer_comparison_path": "/tmp/peer.json",
        },
    }


def _decision_output(packet: dict) -> dict:
    assessments = []
    section_card_keys = {section: [] for section in packet["section_inputs"]}
    for card_key, card in packet["cards"].items():
        allowed = [section for section in card["allowed_sections"] if section in section_card_keys]
        section = allowed[0]
        effect = "reference" if card["evidence_role"] == "reference" or card["eligibility"] != "eligible" else "neutral"
        materiality = "context" if effect == "reference" else "supporting"
        if card_key == "financial.same_period_trend":
            effect = "positive"
            materiality = "decisive"
        elif card_key == "peer.valuation":
            effect = "negative"
            materiality = "decisive"
        comparison_label = str(card.get("comparison_label") or "")
        interpretation = (
            f"주요 제품·서비스 공시표 기준 {card['label']}에 대한 Strategy 해석"
            if card_key == "financial.product_breakdown"
            else f"{comparison_label} {card['label']}에 대한 Strategy 해석".strip()
        )
        assessments.append(
            {
                "card_key": card_key,
                "section": section,
                "direction": effect,
                "materiality": materiality,
                "interpretation": interpretation,
                "investment_effect": effect,
            }
        )
        section_card_keys[section].append(card_key)
    output = {
        "decision_version": "strategy_decision_output_v2",
        "decision": {
            "opinion": "Hold",
            "horizon": "6~12개월",
            "evidence_sufficiency": "medium",
            "positive_factor_card_keys": ["financial.same_period_trend"],
            "negative_factor_card_keys": ["peer.valuation"],
        },
        "recommendation_bridge": {
            "current_price_rationale": "선택일 가격과 계산 배수를 함께 고려했다.",
            "current_price_card_keys": ["valuation.selected_date"],
            "forward_support": "재무 개선과 비교기업 대비 배수 부담이 균형을 이룬다.",
            "forward_support_card_keys": ["financial.same_period_trend", "peer.valuation"],
            "valuation_counterweight": "비교기업 대비 높은 계산 배수는 반대 근거다.",
            "valuation_card_keys": ["peer.valuation"],
            "residual_uncertainty": "KOSPI 상대성과와 사건의 재무 연결은 제한적이다.",
            "uncertainty_card_keys": ["market.relative_performance"],
            "decision_confidence": "medium",
        },
        "evidence_assessments": assessments,
        "peer_findings": [
            {
                "basis_card_key": "peer.revenue_growth",
                "metric_key": "revenue_growth_pct",
                "peer_company": "비교기업",
                "comparison_basis": "2025 HALF YTD",
                "direction": "target_advantage",
                "investment_effect": "positive",
                "finding": "비교기업보다 동일 반기 누적 기준 매출 성장률이 높다.",
            }
        ],
        "decision_risk_factors": [
            {
                "category": "valuation",
                "basis_card_keys": ["peer.valuation"],
                "risk_summary": "동일 날짜 계산 배수가 비교 기업보다 높다.",
                "monitoring_point": "후속 공시의 이익과 선택일 계산 배수",
                "scope_qualifier": "not_applicable",
            }
        ],
        "section_card_keys": section_card_keys,
    }
    if packet["cards"]["peer.valuation"]["eligibility"] != "eligible":
        output["recommendation_bridge"]["forward_support_card_keys"] = [
            "financial.same_period_trend"
        ]
        output["recommendation_bridge"]["valuation_card_keys"] = [
            "valuation.selected_date"
        ]
    return finalize_strategy_decision_v2(output, packet)


def _financial_report() -> dict:
    return {
        "collection_context": {
            "selected_date": "2025-10-31",
            "latest_available_filing": {
                "fiscal_year": 2025,
                "period_type": "HALF",
                "period_end": "2025-06-30",
                "receipt_date": "2025-08-14",
                "report_name": "반기보고서",
            },
            "fallback_applied": True,
            "statement_scope": "separate",
        },
        "financial_trends": {
            "current_vs_same_period": {
                "current_period": {"period_type": "HALF", "basis": "YTD", "period_end": "2025-06-30"},
                "previous_period": {"period_type": "HALF", "basis": "YTD", "period_end": "2024-06-30"},
                "current_values": {
                    "revenue": 100,
                    "operating_profit": 20,
                    "net_income": 10,
                    "operating_cash_flow": 15,
                    "eps": 10,
                },
                "previous_values": {
                    "revenue": 80,
                    "operating_profit": 8,
                    "net_income": 4,
                    "operating_cash_flow": 12,
                    "eps": 4,
                },
            },
            "annual_history": [],
            "normalized_metrics": {"current_values": {"operating_cash_flow_margin": 0.15}},
        },
        "revenue_breakdown": {
            "status": "available",
            "unit": "백만원",
            "current_period": {"period_type": "HALF", "basis": "YTD", "period_end": "2025-06-30"},
            "current_items": [{"name": "제품A", "revenue_krw": 90, "revenue_share": 1.0}],
            "statement_scope": "separate",
            "breakdown_scope": "unknown",
            "validation": {
                "financial_statement_reconciliation": {
                    "coverage_ratio": 0.9,
                    "reconciliation_status": "partial",
                }
            },
        },
        "sy_handoff": {
            "key_evidence": [
                {
                    "evidence_id": "E001",
                    "metric_or_event": "revenue",
                    "period": "2025 HALF YTD",
                    "value": 100,
                },
                {
                    "evidence_id": "E009",
                    "metric_or_event": "cash flow snapshot",
                    "period": "2025 HALF YTD",
                    "value": {"operating_cash_flow": 15},
                },
                {
                    "evidence_id": "E010",
                    "metric_or_event": "balance sheet and liquidity snapshot",
                    "period": "2025-06-30",
                    "period_basis": "POINT_IN_TIME",
                    "value": {"total_assets": 200, "total_equity": 150},
                },
            ]
        },
        "secondary_context_assessment": [
            {
                "source_domain": "news",
                "effect": "neutral",
                "statement": "뉴스 기여 규모는 아직 확인되지 않았다.",
                "primary_evidence_ids": ["E001"],
                "usage": "framing_and_limitation_only",
                "limitation": "인과관계는 확인되지 않았다.",
            }
        ],
    }


def _news_report() -> dict:
    return {
        "output": {
            "analysis_blocks": {
                "news_only": {
                    "positive_signals": [
                        {
                            "claim": "대상기업이 신규 사업을 발표했다.",
                            "evidence_ids": ["NEWS_RAW_2025-10-21_1"],
                            "event_status": "announced",
                            "company_specificity": "direct",
                            "materiality_status": "plausible_unquantified",
                            "financial_link_status": "not_observed",
                        }
                    ],
                    "negative_signals": [],
                    "key_risks": [],
                    "uncertainties": [],
                }
            },
            "secondary_context_assessment": [],
        }
    }


def _yfinance_report() -> dict:
    metrics = {
        "stock_return_20d": {
            "evidence_id": "YF_STOCK_RETURN_20D",
            "metric": "stock_return_20d",
            "value": 0.1,
            "unit": "ratio",
            "source_date": "2025-10-30",
        },
        "stock_excess_return_20d": {
            "evidence_id": "YF_STOCK_EXCESS_RETURN_20D",
            "metric": "stock_excess_return_20d",
            "value": -0.02,
            "unit": "ratio",
            "source_date": "2025-10-30",
        },
        "stock_rsi_14": {
            "evidence_id": "YF_STOCK_RSI_14",
            "metric": "stock_rsi_14",
            "value": 55,
            "unit": "index",
            "source_date": "2025-10-30",
        },
    }
    return {
        "primary_evidence_catalog": {
            value["evidence_id"]: value for value in metrics.values()
        },
        "valuation_snapshot": {
            "calculated_from_close_and_dart": {
                "status": "available",
                "as_of_date": "2025-10-30",
                "inputs": {},
                "metrics": {
                    "trailing_pe": {"value": 20, "status": "ok", "unit": "times"},
                    "price_to_book": {"value": 2, "status": "ok", "unit": "times"},
                    "price_to_sales": {"value": 3, "status": "ok", "unit": "times"},
                },
            },
            "direct_yfinance": {
                "status": "available",
                "latest_period": {"valuation_date": "2025-09-30", "metrics": {"trailing_pe": {"value": 19}}},
            },
        },
        "secondary_context_assessment": [],
    }


def _peer_comparison() -> dict:
    def row(name: str, group: str, growth: float, pe: float) -> dict:
        return {
            "company_name": name,
            "peer_group": group,
            "as_of_date": "20251031",
            "financial_metrics": {
                "revenue_100m": 100,
                "revenue_growth_pct": growth,
                "financial_period": "2025 HALF YTD",
                "operating_margin_pct": 20,
                "net_margin_pct": 10,
                "operating_cash_flow_margin_pct": 15,
                "contribution_margin_pct": 90,
                "sga_margin_pct": 40,
                "debt_ratio_pct": 20,
                "current_ratio_pct": 300,
                "cash_ratio_pct": 100,
                "equity_ratio_pct": 80,
                "balance_sheet_basis": "POINT_IN_TIME",
            },
            "market_metrics": {
                "market_date": "2025-10-30",
                "stock_return_20d_pct": 10,
                "stock_return_60d_pct": 20,
                "stock_excess_return_20d_pct": -2,
                "stock_relative_strength_60_pct": -3,
            },
            "valuation_metrics": {
                "calculated_as_of_date": "2025-10-30",
                "market_cap_100m_krw": 1000,
                "trailing_pe": pe,
                "price_to_book": 2,
                "price_to_sales": 3,
            },
        }

    return {
        "metrics": [
            row("대상기업", "target", 20, 20),
            row("비교기업", "domestic_peer", 5, 10),
        ],
        "comparison_limits": [],
    }
