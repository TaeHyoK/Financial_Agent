from __future__ import annotations

import inspect
import json
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace


AGENT_DIR = Path(__file__).resolve().parents[1]
REPO_ROOT = Path(__file__).resolve().parents[4]
DEFAULT_OUTPUT_ROOT = REPO_ROOT / "Output_total"
sys.path.insert(0, str(AGENT_DIR))

import writer_agent as writer_agent_module
from formatted_html_renderer import build_complete_html, render_formatted_html_report
from html_report_spec import REPORT_SECTIONS
from html_report_validator import validate_html_report
from html_report_writer import (
    _build_context,
    _call_openai_writer,
    _raw_payload_shape_errors,
    _system_prompt,
    build_writer_llm_input,
    build_required_key_evidence,
    normalize_report_payload,
    writer_request_fingerprint,
)
from writer_agent import (
    REQUIRED_STRATEGY_INPUT_FILES,
    WRITER_VALIDATOR_VERSION,
    WriterAgentConfig,
    _coerce_config,
    discover_default_run_key,
    load_cached_writer_response,
)
from writer_handoff import build_writer_handoff, handoff_json_size, validate_writer_handoff


RUN_KEY = "SK바이오팜_20251031"


def test_default_run_paths_resolve_to_current_repo() -> None:
    cfg = _coerce_config(WriterAgentConfig(run_key=RUN_KEY))

    assert cfg.strategy_packet == DEFAULT_OUTPUT_ROOT / "Strategy" / RUN_KEY / "strategy_compact_packet_v2.json"
    assert cfg.strategy_provenance == DEFAULT_OUTPUT_ROOT / "Strategy" / RUN_KEY / "strategy_packet_provenance_v2.json"
    assert cfg.strategy_decision == DEFAULT_OUTPUT_ROOT / "Strategy" / RUN_KEY / "strategy_decision_output_v2.json"


def test_default_run_discovery_requires_all_strategy_inputs(tmp_path: Path) -> None:
    strategy_root = tmp_path / "Strategy"
    incomplete = strategy_root / "INCOMPLETE_20251031"
    complete = strategy_root / "COMPLETE_20251031"
    incomplete.mkdir(parents=True)
    complete.mkdir(parents=True)
    (incomplete / "strategy_report.json").write_text("{}", encoding="utf-8")
    for filename in REQUIRED_STRATEGY_INPUT_FILES:
        (complete / filename).write_text("{}", encoding="utf-8")

    assert discover_default_run_key(tmp_path) == "COMPLETE_20251031"


def test_writer_handoff_is_layered_bounded_and_has_no_path_or_truncation_metadata() -> None:
    handoff = _writer_handoff_from_sources()
    validate_writer_handoff(handoff)
    serialized = json.dumps(handoff, ensure_ascii=False)

    assert list(handoff) == [
        "handoff_version",
        "target",
        "decision",
        "decisive_positive_evidence",
        "decisive_negative_evidence",
        "contrary_evidence",
        "business_context",
        "financial_trend",
        "revenue_breakdown",
        "valuation",
        "market_context",
        "peer_comparison",
        "catalysts",
        "risks",
        "data_limits",
        "evidence_refs",
    ]
    assert handoff["revenue_breakdown"]["current_items"][0]["name"] == "세노바메이트"
    assert [item["company_name"] for item in handoff["peer_comparison"]["metrics"]] == ["SK바이오팜", "일성아이에스"]
    assert handoff_json_size(handoff) < 70_000
    assert "source_path" not in serialized
    assert "opinion_index" not in serialized
    assert "truncated" not in serialized
    assert "/home/" not in serialized


def test_context_contains_compact_writer_input_and_one_output_contract() -> None:
    handoff = _writer_handoff_from_sources()
    context = _build_context(writer_handoff=handoff)

    assert context["writer_input"] == build_writer_llm_input(handoff)
    assert "writer_handoff" not in context
    assert "strategy_report" not in context
    assert "decision_basis_by_section" not in context
    assert "truncated" not in json.dumps(context, ensure_ascii=False)
    assert list(context["output_contract"]["sections"]) == [section["key"] for section in REPORT_SECTIONS]
    assert "valid_json_skeleton" not in context
    assert "json_shape_requirements" not in context
    assert "required_report_structure" not in context


def test_writer_llm_input_replaces_detailed_refs_with_grounding_map() -> None:
    compact = build_writer_llm_input(_writer_handoff_from_sources())

    assert "evidence_refs" not in compact
    assert "contrary_evidence" not in compact
    assert compact["grounding_ref_map"]["OP001"] == "final_recommendation.summary"
    assert "source_section" not in json.dumps(compact, ensure_ascii=False)


def test_required_key_evidence_exposes_exact_display_tokens() -> None:
    required = build_required_key_evidence(_writer_handoff_from_sources())

    assert required["revenue_items"][0] == {
        "name": "세노바메이트",
        "revenue_display": "304,962 백만원",
        "share_display": "95.1%",
    }
    assert required["selected_date_valuation"]["display_tokens"] == [
        "P/E 27.47",
        "P/S 14.74",
        "P/B 13.39",
    ]
    assert required["peer_company_names"] == ["SK바이오팜", "일성아이에스"]


def test_context_does_not_promote_data_limits_to_risks() -> None:
    context = _build_context(writer_handoff=_writer_handoff_from_sources())
    risk_policy = context["writing_rules"]["risk_policy"]

    assert "행 수는 risks 수를 넘지 않는다" in risk_policy
    assert "새 리스크 행으로 승격하지 않는다" in risk_policy


def test_writer_prompt_forbids_numeric_unit_reconstruction() -> None:
    prompt = _system_prompt()

    assert "단위 환산으로 새 숫자를 만들지 않는다" in prompt
    assert "이미 억원 단위" in prompt


def test_writer_request_fingerprint_changes_with_model_or_handoff() -> None:
    handoff = _writer_handoff_from_sources()
    original = writer_request_fingerprint(writer_handoff=handoff, model="gpt-5.4")
    changed_model = writer_request_fingerprint(writer_handoff=handoff, model="gpt-5.4-mini")
    changed_handoff = json.loads(json.dumps(handoff, ensure_ascii=False))
    changed_handoff["decision"]["summary"] = "변경된 요약"

    assert original != changed_model
    assert original != writer_request_fingerprint(writer_handoff=changed_handoff, model="gpt-5.4")


def test_failed_semantic_validation_does_not_reuse_fingerprinted_raw_response(tmp_path: Path) -> None:
    path = tmp_path / "llm_writer_output.json"
    path.write_text(
        json.dumps(
            {
                "fingerprint": "same",
                "raw_payload": _sample_payload(),
                "validation_status": "fail",
                "validator_version": WRITER_VALIDATOR_VERSION,
            }
        ),
        encoding="utf-8",
    )

    assert load_cached_writer_response(
        llm_output_path=path,
        expected_fingerprint="same",
    ) is None
    assert load_cached_writer_response(
        llm_output_path=path,
        expected_fingerprint="same",
        allow_failed_validation=True,
    ) is not None
    assert load_cached_writer_response(
        llm_output_path=path,
        expected_fingerprint="different",
    ) is None


def test_legacy_failed_validation_report_blocks_raw_response_reuse(tmp_path: Path) -> None:
    path = tmp_path / "llm_writer_output.json"
    path.write_text(
        json.dumps({"fingerprint": "same", "raw_payload": _sample_payload()}),
        encoding="utf-8",
    )
    validation_path = tmp_path / "writer_validation_report.json"
    validation_path.write_text(json.dumps({"status": "fail"}), encoding="utf-8")

    assert load_cached_writer_response(
        llm_output_path=path,
        expected_fingerprint="same",
        validation_report_path=validation_path,
    ) is None


def test_writer_cli_retries_one_gate_c_failure_with_fresh_attempt(
    tmp_path: Path, monkeypatch
) -> None:
    output_dir = tmp_path / "Writer"
    calls = {"count": 0}

    def fake_run(_config):
        calls["count"] += 1
        if calls["count"] == 1:
            output_dir.mkdir(parents=True, exist_ok=True)
            (output_dir / "writer_failure_report.json").write_text(
                json.dumps({"status": "fail", "stage": "gate_c"}),
                encoding="utf-8",
            )
            return {"validation_status": "fail"}
        return {"validation_status": "pass"}

    monkeypatch.setattr(writer_agent_module, "run_writer_agent", fake_run)

    result = writer_agent_module.main(
        [
            "--run-key",
            RUN_KEY,
            "--output-dir",
            str(output_dir),
            "--semantic-attempts",
            "2",
        ]
    )

    assert result == 0
    assert calls["count"] == 2
    assert (output_dir / "attempts/attempt_01/writer_failure_report.json").exists()


def test_writer_uses_one_llm_call_without_repair(monkeypatch) -> None:
    calls = {"count": 0}
    payload = _sample_payload()

    class FakeCompletions:
        def create(self, **_kwargs):
            calls["count"] += 1
            return SimpleNamespace(
                choices=[SimpleNamespace(message=SimpleNamespace(content=json.dumps(payload, ensure_ascii=False)))]
            )

    class FakeOpenAI:
        def __init__(self, **_kwargs):
            self.chat = SimpleNamespace(completions=FakeCompletions())

    monkeypatch.setitem(sys.modules, "openai", SimpleNamespace(OpenAI=FakeOpenAI))
    result = _call_openai_writer(context={"writer_handoff": _writer_handoff_from_sources()}, model="test", api_key="test")

    assert result == payload
    assert calls["count"] == 1
    assert "repair" not in inspect.getsource(_call_openai_writer).lower()


def test_renderer_outputs_six_sections_with_data_limits_and_no_view_change_or_target_price(tmp_path: Path) -> None:
    handoff = _writer_handoff_from_sources()
    payload = normalize_report_payload(_sample_payload(), writer_handoff=handoff)
    result = render_formatted_html_report(payload, tmp_path)
    html = Path(result["html_report"]).read_text(encoding="utf-8")

    assert html.startswith("<!DOCTYPE html>")
    assert html.rstrip().endswith("</html>")
    assert html.count("<h1>") == 6
    assert '<colgroup class="key-evidence-columns">' in html
    assert '<col class="evidence-observation-column">' in html
    assert ".key-evidence-columns .evidence-observation-column" in html
    assert "width: 42%;" in html
    assert "투자의견 요약" in html
    assert "핵심 판단 근거" in html
    assert "OUR NAME FOR Project" not in html
    assert "Share Performance" not in html
    assert "<svg" not in html
    assert '<section id="data-limits"' in html
    assert "view-change" not in html.lower()
    assert "target price" not in html.lower()
    assert "목표주가" not in html
    assert "@page" in html and "size: A4" in html
    assert html.count("<table>") >= 2
    assert "<strong>Hold</strong>" in html


def test_validator_passes_grounded_fixed_format_html() -> None:
    handoff = _writer_handoff_from_sources()
    payload = normalize_report_payload(_sample_payload(), writer_handoff=handoff)
    html = build_complete_html(payload)
    validation = validate_html_report(report_payload=payload, html_content=html, writer_handoff=handoff)

    assert validation["status"] == "pass"
    assert validation["grounding_refs"] == "pass"
    assert validation["required_evidence_coverage"] == "pass"
    assert validation["large_number_grounding"] == "pass"
    assert validation["absolute_paths_removed"] == "pass"
    assert validation["forbidden_content_removed"] == "pass"


def test_validator_rejects_invalid_grounding_ref() -> None:
    handoff = _writer_handoff_from_sources()
    payload = normalize_report_payload(_sample_payload(), writer_handoff=handoff)
    payload["sections"]["catalysts_execution"]["section_analysis"]["grounding_refs"] = ["OP999"]
    validation = validate_html_report(
        report_payload=payload,
        html_content=build_complete_html(payload),
        writer_handoff=handoff,
    )

    assert validation["status"] == "fail"
    assert validation["grounding_refs"] == "fail"


def test_validator_rejects_ungrounded_large_integer() -> None:
    handoff = _writer_handoff_from_sources()
    payload = normalize_report_payload(_sample_payload(), writer_handoff=handoff)
    payload["sections"]["investment_call_thesis"]["section_analysis"]["paragraphs"].append(
        "입력에 없는 매출은 999,999,999,999원이다."
    )
    validation = validate_html_report(
        report_payload=payload,
        html_content=build_complete_html(payload),
        writer_handoff=handoff,
    )

    assert validation["large_number_grounding"] == "fail"


def test_validator_requires_product_revenue_share_valuation_date_and_peer() -> None:
    handoff = _writer_handoff_from_sources()
    payload = normalize_report_payload(_sample_payload(), writer_handoff=handoff)
    for section in REPORT_SECTIONS:
        for item_key, _title, item_type in section["items"]:
            if item_type != "table":
                continue
            rows = payload["sections"][section["key"]][item_key]["rows"]
            rows[:] = [row for row in rows if "솔리암페톨" not in str(row)]
    validation = validate_html_report(
        report_payload=payload,
        html_content=build_complete_html(payload),
        writer_handoff=handoff,
    )

    assert validation["required_evidence_coverage"] == "fail"


def test_writer_cli_help_exposes_three_v2_strategy_inputs_only() -> None:
    result = subprocess.run(
        [sys.executable, str(AGENT_DIR / "writer_agent.py"), "--help"],
        capture_output=True,
        text=True,
        check=True,
    )
    help_text = result.stdout

    assert "--strategy-packet" in help_text
    assert "--strategy-provenance" in help_text
    assert "--strategy-decision" in help_text
    assert "--strategy-json" not in help_text
    assert "--decision-basis-by-section" not in help_text
    assert "--chart-manifest" not in help_text
    assert "--visualization-dir" not in help_text


def test_prompt_and_schema_have_no_view_change_section_or_fixed_domain_example() -> None:
    prompt = _system_prompt()
    lowered = prompt.lower()

    assert "view_change_conditions" not in lowered
    assert "view change conditions" not in lowered
    assert "repair" not in lowered
    assert "strategy_report" not in lowered
    assert "decision_basis_by_section" not in lowered
    assert "특정 기업" not in prompt
    assert list(_build_context(writer_handoff=_writer_handoff_from_sources())["output_contract"]["sections"])[-1] == "data_limits"


def test_normalize_report_payload_converts_raw_technical_terms() -> None:
    handoff = _writer_handoff_from_sources()
    payload = _sample_payload()
    payload["sections"]["investment_call_thesis"]["section_analysis"]["paragraphs"] = [
        "2025 Q3 YTD와 2024 FULL_YEAR를 비교하고 peer catalyst를 monitoring한다."
    ]
    normalized = normalize_report_payload(payload, writer_handoff=handoff)
    paragraph = normalized["sections"]["investment_call_thesis"]["section_analysis"]["paragraphs"][0]

    assert "YTD" not in paragraph
    assert "FULL_YEAR" not in paragraph
    assert "peer" not in paragraph
    assert "catalyst" not in paragraph
    assert "monitoring" not in paragraph
    assert "2025년 3분기 누적" in paragraph
    assert "2024년 연간 실적" in paragraph


def test_raw_payload_shape_errors_reject_missing_extra_and_nested_sections() -> None:
    payload = _sample_payload()
    nested = {
        "metadata": payload["metadata"],
        "sections": {
            **{key: value for key, value in payload["sections"].items() if key != "data_limits"},
            "key_evidence_table": {
                **payload["sections"]["key_evidence_table"],
                "data_limits": payload["sections"]["data_limits"],
            },
            "unexpected_section": {},
        },
    }
    errors = _raw_payload_shape_errors(nested)

    assert any("Missing top-level section key" in error for error in errors)
    assert any("Unexpected top-level section key" in error for error in errors)
    assert any("contains nested section key" in error for error in errors)


def _writer_handoff_from_sources() -> dict:
    return build_writer_handoff(
        strategy_report=_strategy_report(),
        strategy_input_bundle=_strategy_input_bundle(),
        decision_basis_by_section=_decision_basis(),
    )


def _strategy_report() -> dict:
    return {
        "target_company_name": "SK바이오팜",
        "target_run_key": RUN_KEY,
        "final_recommendation": {
            "opinion": "Hold",
            "summary": "재무 개선과 가격 부담이 균형을 이룬다.",
            "investment_horizon": "6~12개월",
            "evidence_sufficiency": "medium",
            "evidence_sufficiency_reason": "핵심 자료는 있으나 시차가 있다.",
        },
        "investment_thesis": {"thesis_1": "재무 개선", "thesis_2": "가격 부담", "thesis_3": "상대성과 약세"},
        "financial_view": {"revenue": "매출 증가"},
        "business_mix_view": {"revenue_composition": "제품 구성이 공개됐다.", "concentration": "집중도가 높다."},
        "market_price_view": {"price_trend": "절대 상승", "relative_strength": "상대 약세"},
        "valuation_view": {"selected_date_valuation": "선택일 P/E 27.47배"},
        "peer_competitor_positioning": {"pairwise_findings": ["두 회사 수익성을 비교했다."], "comparison_limits": []},
        "catalyst_view": {"observed_catalysts": ["AI 프로젝트 참여"]},
        "risk_view": {"observed_risks": [{"category": "business", "statement": "제품 집중"}]},
        "decision_balance": {
            "positive_evidence": ["매출과 이익 개선"],
            "negative_evidence": ["높은 밸류에이션"],
        },
        "final_rationale": {"why_buy_hold_sell": "양쪽 근거가 균형이다."},
        "limitations": {
            "data_limitations": ["반기 재무와 시장 기준일에 시차가 있다."],
            "interpretation_limitations": ["뉴스와 실적 인과는 확인되지 않았다."],
            "monitoring_points": ["후속 사업화 공시"],
        },
    }


def _strategy_input_bundle() -> dict:
    return {
        "target_company": {
            "company_name": "SK바이오팜",
            "run_key": RUN_KEY,
            "ticker": "326030.KS",
        },
        "target_reports": {
            "financial": {
                "ticker": "326030.KS",
                "collection_context": {
                    "selected_date": "2025-10-31",
                    "latest_available_filing": {"period_end": "2025-06-30", "receipt_date": "2025-08-14"},
                    "future_filing_excluded": True,
                },
                "financial_trends": {
                    "current_vs_same_period": {"revenue": {"current": 354_042_316_121, "previous": 214_987_543_506}},
                    "annual_history": [],
                    "ttm": {"revenue": 613_484_732_499},
                },
                "revenue_breakdown": {
                    "status": "available",
                    "dimension_type": "product_service",
                    "unit": "백만원",
                    "current_period": {"period_end": "2025-06-30"},
                    "current_items": [
                        {"name": "세노바메이트", "revenue_disclosed": "304,962", "revenue_share_disclosed": "95.1%"},
                        {"name": "솔리암페톨", "revenue_disclosed": "4,798", "revenue_share_disclosed": "1.5%"},
                        {"name": "기타", "revenue_disclosed": "10,894", "revenue_share_disclosed": "3.4%"},
                    ],
                    "source": {"receipt_no": "20250814001203"},
                    "validation": {"status": "pass"},
                },
            },
            "yfinance": {
                "as_of_date": "2025-10-31",
                "main_view": {"summary": "절대 상승, 상대 약세"},
                "time_horizon_view": {},
                "detailed_analysis": {"market_relative": {"stock_excess_return_20d_pct": -7.51}},
                "valuation_snapshot": {
                    "status": "available",
                    "selected_date": "2025-10-31",
                    "calculated_from_close_and_dart": {
                        "status": "available",
                        "as_of_date": "2025-10-31",
                        "metrics": {
                            "trailing_pe": {"value": 27.47225758},
                            "price_to_sales": {"value": 14.74393721},
                            "price_to_book": {"value": 13.3857359},
                        },
                    },
                    "direct_yfinance": {"date_policy": "on_or_before", "latest_period": {"valuation_date": "2025-09-30"}},
                    "validation": {},
                    "data_limits": [],
                },
            },
        },
        "peer_comparison": {
            "peer_groups": {"target": {"company_name": "SK바이오팜"}, "domestic_peers": [{"company_name": "일성아이에스"}]},
            "metrics": [
                {"company_name": "SK바이오팜", "valuation_metrics": {"trailing_pe": 27.47}},
                {"company_name": "일성아이에스", "valuation_metrics": {"trailing_pe": 13.51}},
            ],
            "comparison_limits": ["단일 비교 기업"],
            "source_path": "/private/peer.json",
        },
    }


def _decision_basis() -> dict:
    paths = {
        "OP001": "final_recommendation.summary",
        "OP002": "business_mix_view.revenue_composition",
        "OP003": "financial_view.revenue",
        "OP004": "valuation_view.selected_date_valuation",
        "OP005": "peer_competitor_positioning.pairwise_findings[0]",
        "OP006": "catalyst_view.observed_catalysts[0]",
        "OP007": "risk_view.observed_risks[0].statement",
        "OP008": "limitations.monitoring_points[0]",
        "OP009": "limitations.data_limitations[0]",
    }
    return {
        "decision_basis_by_section": {
            path: {
                "opinion_id": opinion_id,
                "source_evidence": [
                    {
                        "agent": "Financial",
                        "claim_id": f"C{index:03d}",
                        "source_section": "target_reports.financial",
                        "evidence_ids": [f"E{index:03d}"],
                        "source_path": "/private/source.json",
                    }
                ],
            }
            for index, (opinion_id, path) in enumerate(paths.items(), start=1)
        }
    }


def _sample_payload() -> dict:
    text_refs = {
        "investment_call_thesis": ["OP001"],
        "business_market_context": ["OP002"],
        "catalysts_execution": ["OP006"],
        "data_limits": ["OP009"],
    }
    sections: dict[str, dict] = {}
    for section in REPORT_SECTIONS:
        item_key, item_title, item_type = section["items"][0]
        if item_type == "table":
            refs = ["OP003", "OP004", "OP005"] if section["key"] == "key_evidence_table" else ["OP007", "OP008"]
            rows = [
                {"항목": "재무", "관찰": "매출 354,042,316,121원", "판단": "개선"},
                {
                    "항목": "제품 매출",
                    "관찰": "세노바메이트 304,962백만원 95.1%, 솔리암페톨 4,798백만원 1.5%, 기타 10,894백만원 3.4%",
                    "판단": "집중도 확인",
                },
                {"항목": "밸류에이션", "관찰": "2025-10-31 P/E 27.47배, P/S 14.74배, P/B 13.39배", "판단": "가격 부담"},
                {"항목": "1:1 비교", "관찰": "SK바이오팜과 일성아이에스", "판단": "단일 비교"},
            ]
            sections[section["key"]] = {
                item_key: {"columns": ["항목", "관찰", "판단"], "rows": rows, "grounding_refs": refs}
            }
        else:
            sections[section["key"]] = {
                item_key: {
                    "paragraphs": [f"{item_title}: <strong>Hold</strong> 근거를 설명한다."],
                    "bullets": [],
                    "grounding_refs": text_refs.get(section["key"], ["OP001"]),
                }
            }
    return {"metadata": {"report_title": "SK바이오팜 Investment Report"}, "sections": sections}
