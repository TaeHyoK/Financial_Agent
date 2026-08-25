from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest


AGENT_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(AGENT_DIR))

from shared.evidence_cards import card_content_sha256
from formatted_html_renderer import build_complete_html
from html_report_validator import validate_html_report
from html_report_writer import (
    _qualify_partial_product_scope_v2,
    normalize_report_payload,
    writer_request_fingerprint,
    writer_report_response_format,
)
from writer_handoff import (
    _reader_observation,
    build_writer_editorial_packet,
    reformat_financial_reader_observations,
    validate_writer_editorial_packet,
)


def test_news_reader_observation_excludes_article_excerpt_and_internal_statuses() -> None:
    excerpt = "기사 원문이 최종 표에 그대로 노출되면 안 된다."
    observation = _reader_observation(
        {
            "card_key": "news.2025_10_21.event",
            "domain": "news",
            "label": "주요 사건",
            "primary_observation": {
                "event_date": "2025-10-21",
                "event_summary": "기업이 신규 서비스를 공개했다고 보도됐다.",
                "representative_excerpts": [excerpt],
                "event_status": "occurred",
                "company_specificity": "direct",
                "materiality_status": "observed",
                "financial_link_status": "not_observed",
                "coverage": {
                    "article_count": 3,
                    "deduplicated_article_count": 2,
                    "unique_publisher_count": 2,
                },
            },
        }
    )

    serialized = json.dumps(observation, ensure_ascii=False)
    assert observation == {
        "발생일": "2025-10-21",
        "사건 요약": "기업이 신규 서비스를 공개했다고 보도됐다.",
        "보도 범위": "중복 제거 후 2건 · 2개 매체",
        "재무적 영향": "기사에서 재무적 영향의 규모와 시점이 확인되지 않음",
    }
    assert excerpt not in serialized
    assert "event_status" not in serialized
    assert "financial_link_status" not in serialized


def test_market_reader_observations_use_korean_labels_and_display_units() -> None:
    trend = _reader_observation(
        {
            "card_key": "market.absolute_trend",
            "domain": "market",
            "primary_observation": {
                "as_of_date": "2025-10-30",
                "metrics": {
                    "stock_close": 307000,
                    "stock_return_5d": 0.0032679,
                    "stock_return_20d": 0.0199334,
                    "stock_return_60d": 0.0372066,
                    "stock_close_to_ma20": 0.0138705,
                    "stock_close_to_ma60": 0.009224,
                },
            },
        }
    )
    momentum = _reader_observation(
        {
            "card_key": "market.momentum_volume",
            "domain": "market",
            "primary_observation": {
                "as_of_date": "2025-10-30",
                "metrics": {
                    "stock_rsi_14": 51.8137,
                    "stock_macd_hist": 382.6005,
                    "stock_macd_hist_change_1d": -21.0563,
                    "stock_volatility_20": 0.0158613,
                    "stock_volume_ratio_20": 1.6372,
                },
            },
        }
    )

    assert trend == {
        "기준일": "2025-10-30",
        "종가": "307,000원",
        "5거래일 수익률": "0.33%",
        "20거래일 수익률": "1.99%",
        "60거래일 수익률": "3.72%",
        "20일 이동평균 대비": "1.39%",
        "60일 이동평균 대비": "0.92%",
    }
    assert momentum == {
        "기준일": "2025-10-30",
        "14일 RSI": "51.81",
        "MACD 히스토그램": "382.60",
        "MACD 히스토그램 전일 대비 변화": "-21.06",
        "20일 변동성": "1.59%",
        "20일 평균 대비 거래량": "1.64배",
    }
    serialized = json.dumps({"trend": trend, "momentum": momentum}, ensure_ascii=False)
    assert "stock_" not in serialized
    assert "metrics" not in serialized


def test_annual_financial_reader_observation_uses_yearly_korean_rows() -> None:
    observation = _reader_observation(
        {
            "card_key": "financial.annual_trend",
            "domain": "financial",
            "primary_observation": {
                "annual_history": [
                    {
                        "period": {"fiscal_year": 2024, "period_type": "ANNUAL"},
                        "values": {
                            "revenue": 36_604_026_000_000,
                            "operating_profit": 1_802_853_000_000,
                            "net_income": 2_594_221_000_000,
                            "operating_cash_flow": 2_831_367_000_000,
                        },
                    }
                ]
            },
        }
    )

    assert observation == {
        "2024년 연간": {
            "매출": "366,040.3억원",
            "영업이익": "18,028.5억원",
            "순이익": "25,942.2억원",
            "영업현금흐름": "28,313.7억원",
        }
    }


def test_reformats_million_krw_financial_observations_without_touching_eps() -> None:
    packet = {
        "cards": {
            "financial.same_period_trend": {
                "card_key": "financial.same_period_trend",
                "primary_observation": {
                    "current_period": {"fiscal_year": 2025, "period_type": "HALF"},
                    "previous_period": {"fiscal_year": 2024, "period_type": "HALF"},
                    "current_values": {"revenue": 18_343_470, "eps": 12_332},
                    "previous_values": {"revenue": 18_401_166, "eps": 13_649},
                },
            },
            "financial.balance_sheet": {
                "card_key": "financial.balance_sheet",
                "primary_observation": {
                    "as_of_date": "2025-06-30",
                    "values": {
                        "total_assets": 37_982_102,
                        "total_liabilities": 8_193_009,
                        "total_equity": 29_789_093,
                    },
                },
            },
        }
    }

    repaired = reformat_financial_reader_observations(packet, source_unit="백만원")

    trend = repaired["cards"]["financial.same_period_trend"]["reader_observation"]
    balance = repaired["cards"]["financial.balance_sheet"]["reader_observation"]
    assert trend["당기"]["매출"] == "183,434.7억원"
    assert trend["당기"]["EPS"] == "12,332원"
    assert balance["총자산"] == "379,821.0억원"
    assert balance["총부채"] == "81,930.1억원"
    assert balance["총자본"] == "297,890.9억원"
    assert "reader_observation" not in packet["cards"]["financial.same_period_trend"]


def test_rejects_unknown_financial_source_unit() -> None:
    with pytest.raises(ValueError, match="Unsupported KRW source unit"):
        reformat_financial_reader_observations({"cards": {}}, source_unit="달러")


def test_writer_editorial_packet_keeps_strategy_meaning_and_external_provenance() -> None:
    strategy_packet, strategy_decision, provenance = _strategy_artifacts()

    packet, writer_provenance = build_writer_editorial_packet(
        strategy_packet=strategy_packet,
        strategy_decision=strategy_decision,
        strategy_provenance=provenance,
    )

    validate_writer_editorial_packet(
        packet,
        provenance=writer_provenance,
        strategy_packet=strategy_packet,
    )
    assert packet["packet_version"] == "writer_editorial_packet_v2"
    assert packet["cards"]["financial.same_period_trend"]["strategy_interpretation"] == "동일기간 매출과 이익이 개선됐다."
    assert packet["cards"]["valuation.selected_date"]["reader_observation"]["지표"]["P/E"] == "20.00배"
    assert packet["required_card_keys_by_component"]["investment_call_thesis"] == [
        "valuation.selected_date",
        "financial.same_period_trend",
        "peer.valuation",
    ]
    assert "E001" not in json.dumps(packet, ensure_ascii=False)
    assert writer_provenance["cards"]["financial.same_period_trend"]["source_evidence_ids"] == ["E001"]


def test_writer_editorial_packet_accepts_label_free_strategy_v4() -> None:
    strategy_packet, _legacy_decision, provenance = _strategy_artifacts()
    decision = {
        "decision_version": "strategy_decision_output_v4",
        "strategy_brief": {
            "horizon": "1개월",
            "thesis": "실적 개선은 유효하지만 현재 가격 부담을 함께 고려해야 한다.",
            "existing_position_response": "실적 흐름을 확인하며 비중 확대는 자제한다.",
            "new_entry_response": "가격 부담이 완화될 때까지 진입 시점을 나누어 본다.",
            "price_context": "선택일 계산 배수는 비교기업보다 높다.",
            "counterview": "현금흐름 개선은 가격 부담을 일부 상쇄한다.",
            "limitation_summary": "공개 자료만으로 단기 변동의 원인을 확정하기 어렵다.",
            "evidence_sufficiency": "medium",
            "decision_confidence": "medium",
        },
        "rationale": [
            {
                "point": "재무 개선과 가격 부담을 함께 본다.",
                "basis_card_keys": [
                    "financial.same_period_trend",
                    "valuation.selected_date",
                ],
            }
        ],
        "basis_cards": [
            {
                "card_key": "financial.same_period_trend",
                "role": "primary",
                "usage_reason": "동일기간 실적 개선을 판단의 중심 근거로 사용한다.",
            },
            {
                "card_key": "valuation.selected_date",
                "role": "counter",
                "usage_reason": "현재 가격 부담을 반대 근거로 사용한다.",
            },
            {
                "card_key": "peer.valuation",
                "role": "context",
                "usage_reason": "비교기업 대비 가격 수준을 보완한다.",
            },
        ],
        "key_risks": [
            {
                "risk": "높은 계산 배수가 단기 수익률을 제약할 수 있다.",
                "current_implication": "후속 가격 흐름과 실적 공시를 함께 확인한다.",
                "basis_card_keys": ["valuation.selected_date", "peer.valuation"],
            }
        ],
    }

    packet, writer_provenance = build_writer_editorial_packet(
        strategy_packet=strategy_packet,
        strategy_decision=decision,
        strategy_provenance=provenance,
    )

    validate_writer_editorial_packet(
        packet,
        provenance=writer_provenance,
        strategy_packet=strategy_packet,
    )
    assert packet["strategy_contract_version"] == "strategy_decision_output_v4"
    assert packet["decision"]["judgment"] == decision["strategy_brief"]["thesis"]
    assert "opinion" not in packet["decision"]
    assert packet["decision"]["existing_position_response"]
    assert packet["decision"]["new_entry_response"]
    normalized = normalize_report_payload(_writer_payload(packet), writer_handoff=packet)
    assert normalized["metadata"]["recommendation"] == decision["strategy_brief"]["thesis"]


def test_writer_v2_uses_strict_dynamic_json_schema() -> None:
    strategy_packet, strategy_decision, provenance = _strategy_artifacts()
    packet, _writer_provenance = build_writer_editorial_packet(
        strategy_packet=strategy_packet,
        strategy_decision=strategy_decision,
        strategy_provenance=provenance,
    )
    packet["required_limitations"] = [
        {
            "category": "single_peer_scope",
            "basis_card_keys": ["peer.valuation"],
            "facts": {"peer_count": 1, "peer_companies": ["비교기업"]},
        }
    ]

    response_format = writer_report_response_format(packet)
    json_schema = response_format["json_schema"]
    schema = json_schema["schema"]

    assert response_format["type"] == "json_schema"
    assert json_schema["strict"] is True
    assert schema["additionalProperties"] is False
    assert schema["required"] == ["metadata", "sections"]
    sections = schema["properties"]["sections"]
    assert sections["additionalProperties"] is False
    assert sections["required"] == [
        "investment_call_thesis",
        "business_market_context",
        "key_evidence_table",
        "catalysts_execution",
        "risk_monitoring_matrix",
        "data_limits",
    ]
    business_item = sections["properties"]["business_market_context"]["properties"]["section_analysis"]
    assert business_item["required"] == [
        "paragraphs",
        "bullets",
        "card_keys",
        "_claim_units",
    ]
    assert business_item["additionalProperties"] is False
    card_key_items = business_item["properties"]["card_keys"]["items"]
    assert set(card_key_items.get("enum", [])) == set(
        packet["required_card_keys_by_component"]["business_market_context"]
    )
    data_limits_item = sections["properties"]["data_limits"]["properties"]["section_analysis"]
    assert data_limits_item["required"] == ["_limitation_claims"]
    limitation_claims = data_limits_item["properties"]["_limitation_claims"]
    assert limitation_claims["required"] == ["single_peer_scope"]
    assert limitation_claims["additionalProperties"] is False
    category_claim = limitation_claims["properties"]["single_peer_scope"]
    assert category_claim["required"] == ["claim"]
    assert category_claim["additionalProperties"] is False


def test_free_form_writer_schema_authors_thesis_and_tables() -> None:
    strategy_packet, strategy_decision, provenance = _strategy_artifacts()
    packet, _writer_provenance = build_writer_editorial_packet(
        strategy_packet=strategy_packet,
        strategy_decision=strategy_decision,
        strategy_provenance=provenance,
    )

    response_format = writer_report_response_format(packet, writer_mode="free_form")
    sections = response_format["json_schema"]["schema"]["properties"]["sections"]["properties"]
    thesis = sections["investment_call_thesis"]["properties"]["section_analysis"]
    evidence = sections["key_evidence_table"]["properties"]["evidence_table"]
    risks = sections["risk_monitoring_matrix"]["properties"]["risk_monitoring_table"]

    assert thesis["properties"]["paragraphs"]["minItems"] == 1
    assert evidence["properties"]["rows"]["minItems"] == len(
        packet["required_card_keys_by_component"]["key_evidence_table"]
    )
    assert risks["properties"]["rows"]["minItems"] == len(packet["risk_factors"])
    assert writer_request_fingerprint(
        writer_handoff=packet,
        model="test-model",
    ) != writer_request_fingerprint(
        writer_handoff=packet,
        model="test-model",
        writer_mode="free_form",
    )


def test_writer_v2_materializes_category_keyed_limitation_claims() -> None:
    strategy_packet, strategy_decision, provenance = _strategy_artifacts()
    packet, _writer_provenance = build_writer_editorial_packet(
        strategy_packet=strategy_packet,
        strategy_decision=strategy_decision,
        strategy_provenance=provenance,
    )
    packet["required_limitations"] = [
        {
            "category": "single_peer_scope",
            "basis_card_keys": ["peer.valuation"],
            "facts": {"peer_count": 1, "peer_companies": ["비교기업"]},
        }
    ]
    packet["required_card_keys_by_component"]["data_limits"] = ["peer.valuation"]
    raw = _writer_payload(packet)
    raw["sections"]["data_limits"]["section_analysis"] = {
        "_limitation_claims": {
            "single_peer_scope": {
                "claim": "비교 대상은 비교기업 한 곳뿐이어서 업계 전체로 일반화할 수 없다."
            }
        }
    }

    payload = normalize_report_payload(raw, writer_handoff=packet)
    data_limits = payload["sections"]["data_limits"]["section_analysis"]

    assert data_limits["_limitation_categories"] == ["single_peer_scope"]
    assert data_limits["card_keys"] == ["peer.valuation"]
    assert data_limits["_claim_units"] == [
        {
            "claim": "비교 대상은 비교기업 한 곳뿐이어서 업계 전체로 일반화할 수 없다.",
            "card_keys": ["peer.valuation"],
            "limitation_categories": ["single_peer_scope"],
        }
    ]


def test_writer_provenance_detects_editorial_card_change() -> None:
    strategy_packet, strategy_decision, provenance = _strategy_artifacts()
    packet, writer_provenance = build_writer_editorial_packet(
        strategy_packet=strategy_packet,
        strategy_decision=strategy_decision,
        strategy_provenance=provenance,
    )
    packet["cards"]["peer.valuation"]["investment_effect"] = "positive"

    with pytest.raises(ValueError, match="hash mismatch"):
        validate_writer_editorial_packet(
            packet,
            provenance=writer_provenance,
            strategy_packet=strategy_packet,
        )


def test_writer_input_rejects_strategy_bridge_internal_metadata() -> None:
    strategy_packet, strategy_decision, provenance = _strategy_artifacts()
    packet, _writer_provenance = build_writer_editorial_packet(
        strategy_packet=strategy_packet,
        strategy_decision=strategy_decision,
        strategy_provenance=provenance,
    )
    packet["recommendation_bridge"]["forward_support"] += (
        " forward_support_card_keys는 financial.same_period_trend를 사용한다."
    )

    with pytest.raises(ValueError, match="Internal metadata leaked"):
        validate_writer_editorial_packet(packet)


def test_claim_unit_reader_text_replaces_semantic_card_keys() -> None:
    strategy_packet, strategy_decision, provenance = _strategy_artifacts()
    packet, _writer_provenance = build_writer_editorial_packet(
        strategy_packet=strategy_packet,
        strategy_decision=strategy_decision,
        strategy_provenance=provenance,
    )
    raw = _writer_payload(packet)
    item = raw["sections"]["business_market_context"]["section_analysis"]
    item["paragraphs"] = ["valuation.selected_date 근거를 확인했다."]
    item["_claim_units"] = [
        {
            "claim": "valuation.selected_date 근거를 확인했다.",
            "card_keys": [],
            "limitation_categories": [],
        }
    ]

    payload = normalize_report_payload(raw, writer_handoff=packet)
    normalized = payload["sections"]["business_market_context"]["section_analysis"]
    serialized = json.dumps(normalized, ensure_ascii=False)

    assert "valuation.selected_date" not in serialized
    assert "밸류에이션 근거를 확인했다" in serialized
    assert normalized["paragraphs"] == [normalized["_claim_units"][0]["claim"]]


def test_gate_c_accepts_exact_strategy_meaning_and_hides_card_metadata() -> None:
    strategy_packet, strategy_decision, provenance = _strategy_artifacts()
    packet, _writer_provenance = build_writer_editorial_packet(
        strategy_packet=strategy_packet,
        strategy_decision=strategy_decision,
        strategy_provenance=provenance,
    )
    payload = normalize_report_payload(_writer_payload(packet), writer_handoff=packet)
    html = build_complete_html(payload)

    validation = validate_html_report(
        report_payload=payload,
        html_content=html,
        writer_handoff=packet,
    )

    assert validation["status"] == "pass"
    assert validation["card_key_coverage"] == "pass"
    assert validation["strategy_meaning_preservation"] == "pass"
    assert validation["internal_metadata_hidden"] == "pass"
    assert "financial.same_period_trend" not in html
    assert "핵심 근거" in html
    assert "확인된 수치·사실" in html
    assert "투자 해석" in html
    assert "리스크 요인" in html


@pytest.mark.parametrize(
    ("horizon", "expected_heading"),
    [
        pytest.param("6~12개월", "6~12개월 판단 근거", id="default"),
        pytest.param("1개월", "1개월 판단 근거", id="one-month"),
        pytest.param("3개월", "3개월 판단 근거", id="three-month"),
        pytest.param("6개월", "6개월 판단 근거", id="six-month"),
        pytest.param("기간 미지정", "기간 미지정 판단 근거", id="unspecified"),
    ],
)
def test_renderer_and_gate_c_use_exact_strategy_horizon_heading(
    horizon: str,
    expected_heading: str,
) -> None:
    strategy_packet, strategy_decision, provenance = _strategy_artifacts()
    packet, _writer_provenance = build_writer_editorial_packet(
        strategy_packet=strategy_packet,
        strategy_decision=strategy_decision,
        strategy_provenance=provenance,
    )
    packet["decision"]["investment_horizon"] = horizon
    payload = normalize_report_payload(_writer_payload(packet), writer_handoff=packet)
    html = build_complete_html(payload)

    validation = validate_html_report(
        report_payload=payload,
        html_content=html,
        writer_handoff=packet,
    )

    assert f'id="investment-call-thesis-section-analysis">{expected_heading}</h2>' in html
    assert validation["status"] == "pass"
    assert validation["investment_horizon_heading"] == "pass"


def test_gate_c_rejects_stale_investment_horizon_heading() -> None:
    strategy_packet, strategy_decision, provenance = _strategy_artifacts()
    packet, _writer_provenance = build_writer_editorial_packet(
        strategy_packet=strategy_packet,
        strategy_decision=strategy_decision,
        strategy_provenance=provenance,
    )
    packet["decision"]["investment_horizon"] = "1개월"
    payload = normalize_report_payload(_writer_payload(packet), writer_handoff=packet)
    html = build_complete_html(payload).replace(
        ">1개월 판단 근거</h2>",
        ">6~12개월 판단 근거</h2>",
        1,
    )

    validation = validate_html_report(
        report_payload=payload,
        html_content=html,
        writer_handoff=packet,
    )

    assert validation["status"] == "fail"
    assert validation["investment_horizon_heading"] == "fail"
    assert "investment_horizon_heading" in validation["blocking_failures"]
    assert any("Stale investment horizon heading" in note for note in validation["notes"])


def test_normalizer_replaces_writer_interpretation_drift_deterministically() -> None:
    strategy_packet, strategy_decision, provenance = _strategy_artifacts()
    packet, _writer_provenance = build_writer_editorial_packet(
        strategy_packet=strategy_packet,
        strategy_decision=strategy_decision,
        strategy_provenance=provenance,
    )
    raw = _writer_payload(packet)
    raw["sections"]["key_evidence_table"]["evidence_table"]["rows"][0]["투자 해석"] = "다른 의미로 변경했다."
    payload = normalize_report_payload(raw, writer_handoff=packet)

    validation = validate_html_report(
        report_payload=payload,
        html_content=build_complete_html(payload),
        writer_handoff=packet,
    )

    assert validation["status"] == "pass"
    assert validation["strategy_meaning_preservation"] == "pass"
    row = payload["sections"]["key_evidence_table"]["evidence_table"]["rows"][0]
    assert row["투자 해석"] == packet["cards"][row["_card_key"]]["strategy_interpretation"]


def test_normalizer_links_missing_hidden_metadata_from_exact_locked_meaning() -> None:
    strategy_packet, strategy_decision, provenance = _strategy_artifacts()
    packet, _writer_provenance = build_writer_editorial_packet(
        strategy_packet=strategy_packet,
        strategy_decision=strategy_decision,
        strategy_provenance=provenance,
    )
    raw = _writer_payload(packet)
    for row in raw["sections"]["key_evidence_table"]["evidence_table"]["rows"]:
        for key in ("_card_key", "_strategy_interpretation", "_investment_effect"):
            row.pop(key)
    for row in raw["sections"]["risk_monitoring_matrix"]["risk_monitoring_table"]["rows"]:
        row.pop("_basis_card_keys")
        row.pop("_strategy_risk_summary")
    raw["sections"]["risk_monitoring_matrix"]["risk_monitoring_table"]["rows"][0]["현재 확인된 내용"] = "peer.valuation"
    raw["sections"]["key_evidence_table"]["evidence_table"].pop("card_keys")

    payload = normalize_report_payload(raw, writer_handoff=packet)
    validation = validate_html_report(
        report_payload=payload,
        html_content=build_complete_html(payload),
        writer_handoff=packet,
    )

    assert validation["status"] == "pass"
    assert payload["sections"]["key_evidence_table"]["evidence_table"]["rows"][0]["_card_key"]
    assert "peer.valuation" not in build_complete_html(payload)


def test_locked_thesis_adds_partial_product_disclosure_scope() -> None:
    packet = {
        "cards": {
            "financial.product_breakdown": {
                "primary_observation": {
                    "items": [{"name": "세노바메이트"}],
                    "reconciliation": {"reconciliation_status": "partial"},
                }
            }
        }
    }

    qualified = _qualify_partial_product_scope_v2(
        "실적이 개선됐다. 세노바메이트가 매출의 대부분을 차지한다.",
        packet,
        ["financial.product_breakdown"],
    )

    assert qualified == (
        "주요 제품·서비스 공시표 기준으로 보면, "
        "실적이 개선됐다. 세노바메이트가 매출의 대부분을 차지한다."
    )


def test_gate_c_does_not_block_on_comparison_keywords() -> None:
    strategy_packet, strategy_decision, provenance = _strategy_artifacts()
    packet, _writer_provenance = build_writer_editorial_packet(
        strategy_packet=strategy_packet,
        strategy_decision=strategy_decision,
        strategy_provenance=provenance,
    )
    raw = _writer_payload(packet)
    packet["required_card_keys_by_component"]["business_market_context"] = [
        "peer.valuation"
    ]
    business = raw["sections"]["business_market_context"]["section_analysis"]
    business["paragraphs"] = ["동종 대비 배수 부담이 높다."]
    business["card_keys"] = ["peer.valuation"]
    business["_claim_units"] = [
        {
            "claim": "동종 대비 배수 부담이 높다.",
            "card_keys": ["peer.valuation"],
            "limitation_categories": [],
        }
    ]
    payload = normalize_report_payload(raw, writer_handoff=packet)
    validation = validate_html_report(
        report_payload=payload,
        html_content=build_complete_html(payload),
        writer_handoff=packet,
    )

    assert validation["status"] == "pass"
    assert validation["blocking_failures"] == []


def test_gate_c_does_not_block_on_trend_keywords() -> None:
    strategy_packet, strategy_decision, provenance = _strategy_artifacts()
    packet, _writer_provenance = build_writer_editorial_packet(
        strategy_packet=strategy_packet,
        strategy_decision=strategy_decision,
        strategy_provenance=provenance,
    )
    raw = _writer_payload(packet)
    packet["required_card_keys_by_component"]["business_market_context"] = [
        "valuation.selected_date"
    ]
    business = raw["sections"]["business_market_context"]["section_analysis"]
    business["paragraphs"] = ["선택일 밸류에이션이 개선됐다."]
    business["card_keys"] = ["valuation.selected_date"]
    business["_claim_units"] = [
        {
            "claim": "선택일 밸류에이션이 개선됐다.",
            "card_keys": ["valuation.selected_date"],
            "limitation_categories": [],
        }
    ]
    payload = normalize_report_payload(raw, writer_handoff=packet)
    validation = validate_html_report(
        report_payload=payload,
        html_content=build_complete_html(payload),
        writer_handoff=packet,
    )

    assert validation["status"] == "pass"
    assert validation["blocking_failures"] == []


def test_gate_c_reports_text_length_as_advisory() -> None:
    strategy_packet, strategy_decision, provenance = _strategy_artifacts()
    packet, _writer_provenance = build_writer_editorial_packet(
        strategy_packet=strategy_packet,
        strategy_decision=strategy_decision,
        strategy_provenance=provenance,
    )
    raw = _writer_payload(packet)
    business = raw["sections"]["business_market_context"]["section_analysis"]
    long_claim = "가" * 3_300
    business["paragraphs"] = [long_claim]
    business["_claim_units"] = [
        {
            "claim": long_claim,
            "card_keys": business["card_keys"],
            "limitation_categories": [],
        }
    ]
    payload = normalize_report_payload(raw, writer_handoff=packet)
    validation = validate_html_report(
        report_payload=payload,
        html_content=build_complete_html(payload),
        writer_handoff=packet,
    )

    assert validation["status"] == "pass"
    assert validation["compact_text_sections"] == "warning"
    assert validation["advisories"]


def test_gate_c_reports_layout_and_markup_preferences_as_advisories() -> None:
    strategy_packet, strategy_decision, provenance = _strategy_artifacts()
    packet, _writer_provenance = build_writer_editorial_packet(
        strategy_packet=strategy_packet,
        strategy_decision=strategy_decision,
        strategy_provenance=provenance,
    )
    payload = normalize_report_payload(_writer_payload(packet), writer_handoff=packet)
    html = build_complete_html(payload)
    html = html.replace("<style>", '<style type="text/css">')
    html = html.replace("size: A4", "size: auto")
    html = html.replace("<strong>", "<b>").replace("</strong>", "</b>")
    html = html.replace("<body>", "<body><aside>요약 내비게이션</aside>", 1)

    validation = validate_html_report(
        report_payload=payload,
        html_content=html,
        writer_handoff=packet,
    )

    assert validation["status"] == "pass"
    assert validation["blocking_failures"] == []
    assert validation["table_of_contents_removed"] == "warning"
    assert validation["a4_print_layout"] == "warning"
    assert validation["style_block"] == "warning"
    assert validation["strong_tags"] == "warning"


def test_gate_c_reports_claim_paraphrase_as_advisory() -> None:
    strategy_packet, strategy_decision, provenance = _strategy_artifacts()
    packet, _writer_provenance = build_writer_editorial_packet(
        strategy_packet=strategy_packet,
        strategy_decision=strategy_decision,
        strategy_provenance=provenance,
    )
    payload = normalize_report_payload(_writer_payload(packet), writer_handoff=packet)
    business = payload["sections"]["business_market_context"]["section_analysis"]
    business["paragraphs"] = ["같은 근거를 자연스러운 문장으로 다시 표현했다."]

    validation = validate_html_report(
        report_payload=payload,
        html_content=build_complete_html(payload),
        writer_handoff=packet,
    )

    assert validation["status"] == "pass"
    assert validation["claim_card_grounding"] == "pass"
    assert validation["claim_visibility"] == "warning"
    assert validation["blocking_failures"] == []


def test_gate_c_rejects_unauthorized_claim_card() -> None:
    strategy_packet, strategy_decision, provenance = _strategy_artifacts()
    packet, _writer_provenance = build_writer_editorial_packet(
        strategy_packet=strategy_packet,
        strategy_decision=strategy_decision,
        strategy_provenance=provenance,
    )
    raw = _writer_payload(packet)
    business = raw["sections"]["business_market_context"]["section_analysis"]
    business["_claim_units"][0]["card_keys"] = ["financial.same_period_trend"]
    payload = normalize_report_payload(raw, writer_handoff=packet)
    validation = validate_html_report(
        report_payload=payload,
        html_content=build_complete_html(payload),
        writer_handoff=packet,
    )

    assert validation["status"] == "fail"
    assert validation["claim_card_grounding"] == "fail"


def test_gate_c_requires_every_typed_limitation_category() -> None:
    strategy_packet, strategy_decision, provenance = _strategy_artifacts()
    packet, _writer_provenance = build_writer_editorial_packet(
        strategy_packet=strategy_packet,
        strategy_decision=strategy_decision,
        strategy_provenance=provenance,
    )
    packet["required_limitations"] = [
        {
            "category": "single_peer_scope",
            "basis_card_keys": ["peer.valuation"],
            "facts": {"peer_count": 1, "peer_companies": ["비교기업"]},
        }
    ]
    raw = _writer_payload(packet)
    payload = normalize_report_payload(raw, writer_handoff=packet)
    validation = validate_html_report(
        report_payload=payload,
        html_content=build_complete_html(payload),
        writer_handoff=packet,
    )

    assert validation["status"] == "fail"
    assert validation["required_limitation_coverage"] == "fail"


def _strategy_artifacts() -> tuple[dict, dict, dict]:
    cards = {
        "financial.same_period_trend": _card(
            "financial.same_period_trend",
            "financial",
            "same_period_trend",
            "동일기간 재무 추세",
            {"current_revenue": 100, "previous_revenue": 80},
        ),
        "valuation.selected_date": _card(
            "valuation.selected_date",
            "valuation",
            "selected_date_calculated",
            "선택일 계산 밸류에이션",
            {"as_of_date": "2025-10-30", "metrics": {"trailing_pe": {"value": 20}}},
        ),
        "peer.valuation": _card(
            "peer.valuation",
            "peer",
            "valuation",
            "동일 날짜 계산 밸류에이션 비교",
            {"target_company": "대상기업", "peer_company": "비교기업", "pairs": []},
        ),
    }
    packet = {
        "packet_version": "strategy_compact_packet_v2",
        "target_company": {
            "company_name": "대상기업",
            "run_key": "대상기업_20251031",
            "ticker": "000000.KS",
            "as_of_date": "2025-10-31",
        },
        "cards": cards,
        "reader_limitations": [],
        "limitation_requirements": [],
    }
    assessments = [
        {
            "card_key": "financial.same_period_trend",
            "section": "financial_view",
            "direction": "positive",
            "materiality": "decisive",
            "interpretation": "동일기간 매출과 이익이 개선됐다.",
            "investment_effect": "positive",
        },
        {
            "card_key": "valuation.selected_date",
            "section": "valuation_view",
            "direction": "neutral",
            "materiality": "supporting",
            "interpretation": "선택일 계산 배수를 기준으로 가격 수준을 본다.",
            "investment_effect": "neutral",
        },
        {
            "card_key": "peer.valuation",
            "section": "peer_competitor_positioning",
            "direction": "negative",
            "materiality": "decisive",
            "interpretation": "동일 날짜 기준 비교 기업보다 높은 배수다.",
            "investment_effect": "negative",
        },
    ]
    decision = {
        "decision": {
            "opinion": "Hold",
            "horizon": "6~12개월",
            "evidence_sufficiency": "medium",
            "positive_factor_card_keys": ["financial.same_period_trend"],
            "negative_factor_card_keys": ["peer.valuation"],
        },
        "recommendation_bridge": {
            "current_price_rationale": "선택일 계산 배수를 기준으로 현재 가격을 판단했다.",
            "current_price_card_keys": ["valuation.selected_date"],
            "forward_support": "재무 개선과 비교기업 대비 배수 부담이 혼재한다.",
            "forward_support_card_keys": ["financial.same_period_trend", "peer.valuation"],
            "valuation_counterweight": "비교기업 대비 배수 부담이 있다.",
            "valuation_card_keys": ["peer.valuation"],
            "residual_uncertainty": "공개 자료 범위의 불확실성이 남아 있다.",
            "uncertainty_card_keys": ["valuation.selected_date"],
            "decision_confidence": "medium",
            "independent_positive_families": ["financial_performance"],
            "independent_negative_families": ["valuation"],
        },
        "evidence_assessments": assessments,
        "peer_findings": [],
        "decision_risk_factors": [
            {
                "category": "valuation",
                "basis_card_keys": ["peer.valuation"],
                "risk_summary": "비교 기업 대비 높은 계산 배수",
                "monitoring_point": "후속 공시 이익",
            }
        ],
        "section_card_keys": {"catalyst_view": []},
    }
    provenance = {
        "cards": {
            key: {
                "source_evidence_ids": ["E001"] if key.startswith("financial") else [],
                "source_paths": [f"source.{key}"],
                "source_files": ["/tmp/source.json"],
                "strategy_card_sha256": card_content_sha256(card),
            }
            for key, card in cards.items()
        }
    }
    return packet, decision, provenance


def _card(card_key: str, domain: str, card_type: str, label: str, observation: dict) -> dict:
    card = {
        "card_key": card_key,
        "domain": domain,
        "card_type": card_type,
        "label": label,
        "allowed_sections": ["investment_thesis", "financial_view", "valuation_view", "peer_competitor_positioning", "risk_view", "decision_balance"],
        "evidence_role": "primary",
        "eligibility": "eligible",
        "primary_observation": observation,
        "evidence_family": (
            "financial_performance" if domain == "financial" else "valuation"
        ),
        "observation_basis": (
            "period_comparison"
            if card_key == "financial.same_period_trend"
            else "pairwise_comparison"
            if domain == "peer"
            else "point_in_time"
        ),
        "comparison_scope": "selected_peer" if domain == "peer" else "none",
        "decision_use": "factor_eligible",
    }
    if domain == "peer":
        card["comparison_label"] = "비교기업 대비"
        card["comparison_entities"] = {
            "target_company": "대상기업",
            "peer_companies": ["비교기업"],
            "peer_count": 1,
        }
    return card


def _writer_payload(packet: dict) -> dict:
    required = packet["required_card_keys_by_component"]
    cards = packet["cards"]
    observation_text = {
        "financial.same_period_trend": "동일 누적 기준 매출은 100, 비교 기간 매출은 80이다.",
        "valuation.selected_date": "2025-10-30 선택일 계산 P/E는 20이다.",
        "peer.valuation": "대상기업과 비교기업의 같은 기준 값을 비교했다.",
    }
    effect_labels = {
        "positive": "긍정 요인",
        "negative": "부담 요인",
        "mixed": "혼합",
        "neutral": "중립",
        "reference": "참고",
    }
    evidence_rows = []
    for card_key in required["key_evidence_table"]:
        card = cards[card_key]
        row = {
                "핵심 근거": card["label"],
                "확인된 수치·사실": observation_text[card_key],
                "투자 해석": card["strategy_interpretation"],
                "_card_key": card_key,
                "_strategy_interpretation": card["strategy_interpretation"],
        }
        if card.get("strategy_role"):
            row.update(
                {
                    "판단상 역할": {
                        "primary": "핵심 근거",
                        "counter": "반대 근거",
                        "monitoring": "위험 신호",
                        "context": "판단 문맥",
                    }[card["strategy_role"]],
                    "_strategy_role": card["strategy_role"],
                }
            )
        else:
            row.update(
                {
                    "영향": effect_labels[card["investment_effect"]],
                    "_investment_effect": card["investment_effect"],
                }
            )
        evidence_rows.append(row)
    risk_rows = [
        {
            "리스크 요인": "밸류에이션 부담",
            "현재 확인된 내용": risk["risk_summary"],
            "향후 점검사항": risk["monitoring_point"],
            "_basis_card_keys": risk["basis_card_keys"],
            "_strategy_risk_summary": risk["risk_summary"],
        }
        for risk in packet["risk_factors"]
    ]
    return {
        "metadata": {"report_title": "대상기업 Investment Report"},
        "sections": {
            "investment_call_thesis": {
                "section_analysis": {
                    "paragraphs": ["<strong>Hold</strong>, 6~12개월 관점에서 재무 개선과 비교기업 대비 배수 부담이 혼재한다."],
                    "bullets": [],
                    "card_keys": required["investment_call_thesis"],
                    "_claim_units": [
                        {
                            "claim": "Hold, 6~12개월 관점에서 재무 개선과 비교기업 대비 배수 부담이 혼재한다.",
                            "card_keys": required["investment_call_thesis"],
                            "limitation_categories": [],
                        }
                    ],
                }
            },
            "business_market_context": {
                "section_analysis": {
                    "paragraphs": ["현재 제공된 사업 및 시장 근거 범위에서 판단한다."],
                    "bullets": [],
                    "card_keys": required["business_market_context"],
                    "_claim_units": [{"claim": "현재 제공된 사업 및 시장 근거 범위에서 판단한다.", "card_keys": required["business_market_context"], "limitation_categories": []}],
                }
            },
            "key_evidence_table": {
                "evidence_table": {
                    "columns": (
                        ["핵심 근거", "확인된 수치·사실", "투자 해석", "판단상 역할"]
                        if packet.get("strategy_contract_version") == "strategy_decision_output_v4"
                        else ["핵심 근거", "확인된 수치·사실", "투자 해석", "영향"]
                    ),
                    "rows": evidence_rows,
                    "card_keys": required["key_evidence_table"],
                }
            },
            "catalysts_execution": {
                "section_analysis": {
                    "paragraphs": ["확인된 촉매 근거는 제한적이다."],
                    "bullets": [],
                    "card_keys": required["catalysts_execution"],
                    "_claim_units": [{"claim": "확인된 촉매 근거는 제한적이다.", "card_keys": required["catalysts_execution"], "limitation_categories": []}],
                }
            },
            "risk_monitoring_matrix": {
                "risk_monitoring_table": {
                    "columns": ["리스크 요인", "현재 확인된 내용", "향후 점검사항"],
                    "rows": risk_rows,
                    "card_keys": required["risk_monitoring_matrix"],
                }
            },
            "data_limits": {
                "section_analysis": {
                    "paragraphs": ["공개 자료 범위 안에서 해석했다."],
                    "bullets": [],
                    "card_keys": required["data_limits"],
                    "_claim_units": [{"claim": "공개 자료 범위 안에서 해석했다.", "card_keys": required["data_limits"], "limitation_categories": []}],
                    "_limitation_categories": [],
                }
            },
        },
    }
