from __future__ import annotations

import json
import sys
from pathlib import Path

import jsonschema


AGENT_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(AGENT_DIR))

from data_loader import load_json
from analyst_rewriter import rewrite_contract
from editorial_polisher import polish_contract
from html_renderer import render_html_preview
from llm_writer import _merge_llm_payload
from quality_validator import validate_report_quality
from report_contract_builder import build_report_contract
from writer_validator import validate_writer_outputs


REPO_ROOT = Path("/home/agent2/Financial_Agent_Final")
RUN_KEY = ""


def _fixture_run_key() -> str:
    strategy_root = REPO_ROOT / "Output_total" / "Strategy"
    candidates = [
        path
        for path in strategy_root.iterdir()
        if path.is_dir() and (path / "strategy_report.json").exists()
    ]
    if not candidates:
        raise RuntimeError(f"No Strategy fixture found under {strategy_root}")
    candidates.sort(key=lambda path: (path / "strategy_report.json").stat().st_mtime, reverse=True)
    return candidates[0].name


def test_contract_schema_valid() -> None:
    contract, strategy, manifest = _build_contract()
    schema = json.loads((AGENT_DIR / "schemas" / "broker_report_contract_v1.schema.json").read_text(encoding="utf-8"))

    jsonschema.validate(contract, schema)
    assert contract["report_metadata"]["recommendation"] == "Hold"
    assert contract["report_metadata"]["target_price"] == "N/A"
    assert contract["render_targets"]["default_render_format"] == "html"
    assert contract["reader_friendly_sections"]["financial_view_cards"]
    assert contract["reader_friendly_sections"]["catalyst_analysis_cards"]
    assert contract["reader_friendly_sections"]["final_rationale"]["investment_conclusion"]
    assert contract["reader_friendly_sections"]["final_rationale"]["balance_of_evidence"]
    assert contract["reader_friendly_sections"]["final_rationale"]["view_change_conditions"]
    metric_names = {metric["metric_name"] for metric in contract["key_metrics_table"]["metrics"]}
    assert {"Debt Ratio", "Current Ratio", "Operating Cash Flow"} <= metric_names
    assert all(card.get("investment_implication") for card in contract["reader_friendly_sections"]["financial_view_cards"])
    assert all(card.get("investment_implication") for card in contract["reader_friendly_sections"]["market_view_cards"])


def test_figure_manifest_mapping_and_approved_only() -> None:
    contract, _, manifest = _build_contract()
    manifest_ids = {chart["figure_id"] for chart in manifest["charts"]}
    figure_ids = [block["figure_id"] for block in contract["visual_report_blocks"]]

    assert len(figure_ids) == 2
    assert figure_ids == ["fig_stock_price_ma_volume_relative_strength", "fig_fundamental_margin_trend"]
    assert "fig_investment_thesis_evidence_map" not in figure_ids
    assert all(figure_id in manifest_ids for figure_id in figure_ids)
    assert all(Path(block["figure_path"]).exists() for block in contract["visual_report_blocks"])
    assert all(block.get("support_score", 0) > 0 for block in contract["visual_report_blocks"])
    assert all(block.get("support_reason") for block in contract["visual_report_blocks"])


def test_basis_and_price_warnings_present() -> None:
    contract, _, _ = _build_contract()
    serialized = json.dumps(contract, ensure_ascii=False)

    assert "동일 기간 YoY로 단정" in serialized or "집계 기준" in serialized
    assert "펀더멘털 개선의 직접 증거로 단정할 수 없다" in serialized


def test_html_preview_hides_source_trace_by_default(tmp_path: Path) -> None:
    contract, strategy, manifest = _build_contract()
    html_result = render_html_preview(contract, tmp_path, include_source_trace=False, embed_images=True)

    html = Path(html_result["html_preview"]).read_text(encoding="utf-8")
    assert "Source Trace Summary" not in html
    assert "투자 요약" in html
    assert "투자 요약 코멘트" in html
    assert "핵심 차트" in html
    assert "재무 분석 및 주가/시장 해석" in html
    assert "성장 촉매 분석" in html
    assert "최종 투자의견 근거" in html
    assert "확인된 지표" in html
    assert "애널리스트 해석" in html
    assert "투자의견 시사점" in html
    assert "핵심 촉매" in html
    assert "종합 판단" in html
    assert "투자의견 변경 조건" in html
    assert "letter-spacing: normal" in html
    assert "Investment Summary" not in html
    assert "Financial View" not in html
    assert "Catalyst Analysis" not in html
    assert "Positive case" not in html
    assert "Balance of evidence" not in html
    assert "Evidence from Strategy" not in html
    assert "Investment relevance" not in html
    assert "Valuation Agent 미적용" not in html
    assert "Report Type: Equity Research Draft" not in html
    assert html.count("src=\"data:image/png;base64,") == len(contract["visual_report_blocks"])
    assert "/home/agent2/Financial_Agent_Final/Output_total/Visualization" not in html


def test_validator_flags_core_rules_pass(tmp_path: Path) -> None:
    contract, strategy, manifest = _build_contract()
    html_result = render_html_preview(contract, tmp_path, include_source_trace=False, embed_images=False)
    validation = validate_writer_outputs(
        contract=contract,
        strategy_report=strategy,
        chart_manifest=manifest,
        source_trace=contract["source_trace"],
        main_tex="",
        final_pdf_path="",
        html_preview_path=html_result["html_preview"],
        html_content=html_result["html_content"],
        render_format="html",
        include_source_trace=False,
        embed_images=False,
        latex_compile_status="not_requested",
        latex_notes=[],
    )

    assert validation["recommendation_consistency"] == "pass"
    assert validation["target_price_policy"] == "pass"
    assert validation["figure_assets"] == "pass"
    assert validation["chart_manifest_consistency"] == "pass"
    assert validation["html_preview_render"] == "pass"
    assert validation["html_source_trace_hidden"] == "pass"
    assert validation["html_image_mode"] == "pass"
    assert validation["html_sentence_polish"] == "pass"
    assert validation["html_internal_terms"] == "pass"
    assert validation["investment_implication"] == "pass"
    assert validation["raw_copy_sequence"] == "pass"
    assert validation["status"] == "pass"
    html = Path(html_result["html_preview"]).read_text(encoding="utf-8")
    for block in contract["visual_report_blocks"]:
        figure_name = Path(block["html_img_path"]).name
        assert f'src="figures/{figure_name}"' in html
        assert (tmp_path / "figures" / figure_name).exists()


def test_quality_score_meets_target(tmp_path: Path) -> None:
    contract, strategy, _ = _build_contract()
    html_result = render_html_preview(contract, tmp_path, include_source_trace=False, embed_images=True)
    quality = validate_report_quality(
        contract=contract,
        strategy_report=strategy,
        html_content=html_result["html_content"],
        embed_images=True,
    )

    assert quality["overall_quality_score"] >= 85
    assert quality["checks"]["has_main_investment_logic"] is True
    assert quality["checks"]["has_view_change_conditions"] is True
    assert quality["checks"]["avoids_internal_agent_terms"] is True
    assert quality["checks"]["avoids_unsupported_metrics"] is True
    assert quality["checks"]["has_stability_metrics_when_claimed"] is True


def test_llm_payload_selects_allowed_figures_and_merges_commentary() -> None:
    contract, strategy, manifest = _build_contract()
    run_key = RUN_KEY or _fixture_run_key()
    payload = {
        "selected_chart_ids": [
            "fig_investment_thesis_evidence_map",
            "fig_revenue_profit_sga_trend",
            "fig_indexed_stock_vs_kospi",
        ],
        "main_investment_logic": "실적 개선 신호와 시장 상대성과의 괴리가 현재 투자의견을 설명하는 핵심 축이다.",
        "cover_summary": {
            "headline": "실적 개선은 확인되지만 상대성과 확인이 필요한 구간",
            "monitoring_points": ["연간 실적 확인", "상대성과 회복", "핵심 촉매의 실행 속도"],
        },
        "visual_report_blocks": [
            {
                "figure_id": "fig_revenue_profit_sga_trend",
                "what_chart_shows": "매출과 이익, 판관비 흐름을 함께 보여준다.",
                "analyst_takeaway": "수익성 개선 방향은 긍정적이나 기간 기준 차이를 감안해야 한다.",
                "support_reason": "재무 개선 논리를 직접 뒷받침한다.",
            },
            {
                "figure_id": "fig_indexed_stock_vs_kospi",
                "what_chart_shows": "시장 대비 주가 흐름을 지수화해 보여준다.",
                "analyst_takeaway": "절대 가격 흐름만으로 투자 판단을 강화하기 어렵다는 점을 보여준다.",
                "support_reason": "상대성과 확인 필요성을 뒷받침한다.",
            },
        ],
        "reader_friendly_sections": {
            "investment_summary": {
                "one_line_view": "개선 신호와 검증 필요성이 공존한다.",
                "recommendation_rationale": "긍정 요인은 있으나 확인해야 할 조건이 남아 있다.",
                "key_debate": "수익성 개선이 시장 상대성과 개선으로 이어질 수 있는지가 핵심이다.",
            }
        },
    }

    updated = _merge_llm_payload(
        contract=contract,
        payload=payload,
        strategy_report=strategy,
        chart_manifest=manifest,
        visualization_dir=REPO_ROOT / "Output_total" / "Visualization" / run_key,
        model="test-model",
    )

    figure_ids = [block["figure_id"] for block in updated["visual_report_blocks"]]
    assert "fig_investment_thesis_evidence_map" not in figure_ids
    assert figure_ids == ["fig_revenue_profit_sga_trend", "fig_indexed_stock_vs_kospi"]
    assert updated["main_investment_logic"] == payload["main_investment_logic"]
    assert updated["cover_summary"]["headline"] == payload["cover_summary"]["headline"]
    assert updated["reader_friendly_sections"]["investment_summary"]["key_debate"] == payload["reader_friendly_sections"]["investment_summary"]["key_debate"]
    assert updated["visual_report_blocks"][0]["analyst_takeaway"] == payload["visual_report_blocks"][0]["analyst_takeaway"]
    assert updated["llm_writer"]["status"] == "applied"
    assert updated["llm_writer"]["model"] == "test-model"


def _build_contract():
    run_key = RUN_KEY or _fixture_run_key()
    strategy = load_json(REPO_ROOT / "Output_total" / "Strategy" / run_key / "strategy_report.json", "strategy")
    strategy_input = load_json(
        REPO_ROOT / "Output_total" / "Strategy" / run_key / "strategy_input_bundle.json",
        "strategy input bundle",
    )
    dart = load_json(REPO_ROOT / "Output_total" / "Financial" / run_key / "dart_main.json", "dart")
    manifest = load_json(REPO_ROOT / "Output_total" / "Visualization" / run_key / "chart_manifest.json", "manifest")
    draft = build_report_contract(
        strategy_report=strategy,
        strategy_input_bundle=strategy_input,
        dart_main=dart,
        chart_manifest=manifest,
        visualization_dir=REPO_ROOT / "Output_total" / "Visualization" / run_key,
        source_files={},
    )
    contract = polish_contract(rewrite_contract(draft))
    return contract, strategy, manifest
