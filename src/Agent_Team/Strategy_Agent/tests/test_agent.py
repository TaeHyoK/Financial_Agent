"""Tests for Strategy Agent workflow."""

from __future__ import annotations

import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from Agent_Team.Strategy_Agent.agent import (
    build_decision_basis_card,
    build_strategy_input_bundle,
    consolidate_limitations,
    generate_strategy_report,
    iter_editable_report_opinions,
    normalize_strategy_decision_output,
    normalize_strategy_report,
    normalize_limitations,
    run_strategy_agent,
    validate_decision_basis_by_section,
    validate_decision_basis_card,
    validate_strategy_report,
)


class StrategyAgentTests(unittest.TestCase):
    def test_run_strategy_agent_writes_outputs_and_uses_two_llm_steps(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            run_key = "SK바이오팜_20251031"
            _write_source_reports(root, run_key)
            competitor_paths = [
                _write_competitor(root, "더블유에스아이_20251031", "더블유에스아이"),
                _write_competitor(root, "위더스제약_20251031", "위더스제약"),
            ]
            output_dir = root / "Strategy" / run_key

            with patch(
                "Agent_Team.Strategy_Agent.agent.call_llm_json",
                side_effect=[_content_plan(), _strategy_decision_output()],
            ) as mocked_llm:
                report = run_strategy_agent(
                    target_company_name="SK바이오팜",
                    target_run_key=run_key,
                    target_financial_path=root / "Financial" / run_key / "final_report.json",
                    target_news_path=root / "News" / run_key / "final_report.json",
                    target_yfinance_path=root / "Y_Finance" / run_key / "final_report.json",
                    competitor_report_paths=competitor_paths,
                    output_dir=output_dir,
                    llm_provider="openai",
                    llm_model="test-model",
                    env_file=None,
                )

            self.assertEqual(mocked_llm.call_count, 2)
            self.assertEqual(report["final_recommendation"]["opinion"], "Hold")
            self.assertNotIn("confidence", report["final_recommendation"])
            self.assertIn("thesis_3", report["investment_thesis"])
            self.assertIn("Buy", report["investment_thesis"]["thesis_3"])
            self.assertNotIn("upcoming_catalysts", report["catalyst_view"])
            self.assertTrue((output_dir / "strategy_input_bundle.json").exists())
            self.assertTrue((output_dir / "strategy_content_plan.json").exists())
            self.assertTrue((output_dir / "strategy_report.json").exists())
            self.assertTrue((output_dir / "strategy_report.md").exists())
            self.assertTrue((output_dir / "decision_basis_by_section.json").exists())
            self.assertTrue((output_dir / "decision_basis_card.json").exists())

            bundle = json.loads((output_dir / "strategy_input_bundle.json").read_text(encoding="utf-8"))
            self.assertEqual(bundle["input_metadata"]["competitor_count"], 2)
            self.assertEqual([item["company_name"] for item in bundle["competitor_reports"]], ["더블유에스아이", "위더스제약"])
            self.assertIn("YTD와 연간 비교 기준 차이", json.dumps(bundle["decision_constraints"], ensure_ascii=False))

            markdown = (output_dir / "strategy_report.md").read_text(encoding="utf-8")
            self.assertIn("## 0. Target Company Name: SK바이오팜", markdown)
            self.assertIn("- Opinion: Hold", markdown)
            self.assertIn("- Thesis 3:", markdown)
            self.assertNotIn("- Confidence:", markdown)
            self.assertNotIn("- Upcoming Catalysts:", markdown)
            self.assertIn("## 8. Peer / Competitor Positioning", markdown)
            self.assertIn("## 9. Key Strengths", markdown)
            self.assertNotIn("## 9. Key Strengths (", markdown)
            self.assertIn("## 11. Final Rationale", markdown)
            self.assertNotIn("Valuation View", markdown)
            limitations_markdown = markdown.split("## 12. Limitations", 1)[1]
            self.assertNotIn("직접 증거", limitations_markdown)
            self.assertNotIn("낮춰야", limitations_markdown)
            self.assertNotIn("낮추어야", limitations_markdown)
            self.assertNotIn("주장하지 않는다", limitations_markdown)
            self.assertNotIn("필요하다", limitations_markdown)
            self.assertNotIn("필요가 있다", limitations_markdown)
            self.assertNotIn("확인 필요", limitations_markdown)
            self.assertNotIn("사용한다", limitations_markdown)

            basis_card_payload = json.loads((output_dir / "decision_basis_card.json").read_text(encoding="utf-8"))
            basis_card = basis_card_payload["decision_basis_card"]
            self.assertEqual(basis_card["target_company_name"], "SK바이오팜")
            self.assertEqual(basis_card["target_run_key"], run_key)
            self.assertEqual(basis_card["final_recommendation"], "Hold")
            self.assertNotIn("recommendation_confidence", basis_card)
            for key in (
                "basis_items",
                "risk_items",
                "decision_constraints_applied",
                "mixed_or_conflicting_signals",
                "strong_claims_in_report",
                "monitoring_points",
                "limitations",
            ):
                self.assertIsInstance(basis_card[key], list)
                self.assertGreater(len(basis_card[key]), 0)
                self.assertIsInstance(basis_card[key][0], dict)
                self.assertIn("claim", basis_card[key][0])
                if key != "strong_claims_in_report":
                    self.assertIn("reasoning", basis_card[key][0])
                else:
                    self.assertIsInstance(basis_card[key][0]["source_sections"], list)
                    self.assertNotIn("source_section", basis_card[key][0])
                    self.assertNotIn("claim_type", basis_card[key][0])
            self.assertIn("opinion_id", basis_card["basis_items"][0])
            self.assertIn("basis_path", basis_card["basis_items"][0])
            self.assertNotIn("why_not_buy", basis_card)
            self.assertNotIn("why_not_sell", basis_card)

            basis_by_section = json.loads((output_dir / "decision_basis_by_section.json").read_text(encoding="utf-8"))
            validate_decision_basis_by_section(basis_by_section, report)
            revenue_basis = basis_by_section["decision_basis_by_section"]["financial_view.revenue"]
            self.assertEqual(revenue_basis["opinion_text"], report["financial_view"]["revenue"])
            self.assertIn("DART", revenue_basis["basis_summary"])
            self.assertEqual(revenue_basis["source_evidence"][0]["claim_id"], "F001")

    def test_generate_strategy_report_auto_discovers_n_competitors(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            run_key = "SK바이오팜_20251031"
            _write_source_reports(root, run_key)
            for idx in range(3):
                _write_competitor(root, f"경쟁사{idx}_20251031", f"경쟁사{idx}")

            with patch(
                "Agent_Team.Strategy_Agent.agent.call_llm_json",
                side_effect=[_content_plan(), _strategy_decision_output()],
            ):
                report = generate_strategy_report(
                    run_key=run_key,
                    target_config=None,
                    output_root=root,
                    auto_discover_competitors=True,
                    llm_provider="openai",
                    llm_model="test-model",
                    env_file=None,
                )

            self.assertEqual(report["final_recommendation"]["opinion"], "Hold")
            bundle = json.loads((root / "Strategy" / run_key / "strategy_input_bundle.json").read_text(encoding="utf-8"))
            self.assertEqual(bundle["input_metadata"]["competitor_count"], 3)

    def test_normalize_strategy_decision_output_preserves_section_basis(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            run_key = "SK바이오팜_20251031"
            _write_source_reports(root, run_key)
            bundle = build_strategy_input_bundle(
                target_company_name="SK바이오팜",
                target_run_key=run_key,
                target_financial_path=root / "Financial" / run_key / "final_report.json",
                target_news_path=root / "News" / run_key / "final_report.json",
                target_yfinance_path=root / "Y_Finance" / run_key / "final_report.json",
                competitor_report_paths=[],
            )

            report, basis_payload = normalize_strategy_decision_output(_strategy_decision_output(), bundle)

            validate_strategy_report(report)
            validate_decision_basis_by_section(basis_payload, report)
            basis_map = basis_payload["decision_basis_by_section"]
            self.assertEqual(basis_map["financial_view.revenue"]["opinion_text"], report["financial_view"]["revenue"])
            self.assertIn("5,011억원", basis_map["financial_view.revenue"]["key_numbers"])
            self.assertIn("investment_thesis.thesis_1", basis_map)
            self.assertNotEqual(
                basis_map["investment_thesis.thesis_1"]["basis_summary"],
                report["investment_thesis"]["thesis_1"],
            )
            self.assertIn("입력", basis_map["investment_thesis.thesis_1"]["basis_summary"])

    def test_build_strategy_input_bundle_allows_empty_competitor_list(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            run_key = "SK바이오팜_20251031"
            _write_source_reports(root, run_key)

            bundle = build_strategy_input_bundle(
                target_company_name="SK바이오팜",
                target_run_key=run_key,
                target_financial_path=root / "Financial" / run_key / "final_report.json",
                target_news_path=root / "News" / run_key / "final_report.json",
                target_yfinance_path=root / "Y_Finance" / run_key / "final_report.json",
                competitor_report_paths=[],
            )

            self.assertEqual(bundle["input_metadata"]["competitor_count"], 0)
            self.assertEqual(bundle["competitor_reports"], [])

    def test_validation_evidence_drives_specific_news_risk_language_and_opinion_ids(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            run_key = "SK바이오팜_20251031"
            _write_source_reports(root, run_key)
            _write_final_validations(root, run_key)

            bundle = build_strategy_input_bundle(
                target_company_name="SK바이오팜",
                target_run_key=run_key,
                target_financial_path=root / "Financial" / run_key / "final_report.json",
                target_news_path=root / "News" / run_key / "final_report.json",
                target_yfinance_path=root / "Y_Finance" / run_key / "final_report.json",
                competitor_report_paths=[],
            )

            self.assertEqual(len(bundle["target_validation_evidence"]["financial"]["claims"]), 1)
            self.assertEqual(len(bundle["target_validation_evidence"]["news"]["claims"]), 1)
            self.assertIn("answer_2", bundle["target_validation_evidence"]["financial"]["claims"][0])
            self.assertEqual(bundle["target_reports"]["news"]["report_status"], "sy_validation_reflected")
            rendered_news_report = json.dumps(bundle["target_reports"]["news"], ensure_ascii=False)
            self.assertNotIn("SY revise:", rendered_news_report)
            self.assertNotIn("SY 검증 결과 News Agent 핵심 주장", rendered_news_report)
            self.assertIn("News SY 검증 결과 1건 중 keep 1건, revise 0건", rendered_news_report)
            self.assertTrue(
                bundle["target_validation_evidence"]["news"]["source_path"].endswith(
                    "output/sy_agent/sy_claim_validations.json"
                )
            )
            self.assertEqual(bundle["target_validation_evidence"]["news"]["claims"][0]["decision"], "keep")

            report = _strategy_report()
            report["target_run_key"] = run_key
            vague_risk = "뉴스 주요 리스크 이슈가 재무 개선 지속성에 대한 주의 요인으로 작용"
            report["risk_view"]["execution_risks"].append(vague_risk)
            report["key_risks"].append(vague_risk)
            normalized = normalize_strategy_report(report, bundle)
            validate_strategy_report(normalized)
            rendered = json.dumps(normalized, ensure_ascii=False)
            self.assertNotIn("뉴스 주요 리스크 이슈", rendered)
            self.assertIn("미국 FDA 안전성 조사", rendered)
            self.assertIn("인도 제네릭 출현", rendered)
            self.assertGreater(len(normalized["opinion_index"]), 0)
            self.assertEqual(normalized["opinion_index"][0]["id"], "OP001")

    def test_normalize_limitations_rewrites_instruction_style_wording(self) -> None:
        limitations = normalize_limitations(
            {
                "data_limitations": ["성장률 해석에 주의가 필요하다."],
                "interpretation_limitations": [
                    "주가 절대 상승은 긍정적 신호이나 상대성과 혼재되어 가격 확인 강도는 낮추어야 한다.",
                    "주가 상승과 거래량 증가는 긍정적 신호이나 펀더멘털과의 직접적 인과관계는 신중히 추가 검토가 필요하다.",
                    "추가 검증이 필요하다.",
                    "신사업 성과 모니터링 필요가 있음",
                    "미국 FDA 안전성 조사 결과 및 글로벌 관세 정책 변화 등 규제 및 정책 리스크를 지속 모니터링할 필요가 있다.",
                    "신사업 분야인 AI 및 방사성의약품의 상업화 성공 가능성과 경쟁 환경을 면밀히 관찰할 필요가 있다.",
                    "미국 FDA 안전성 조사 및 규제 리스크는 지속 모니터링이 필요함",
                    "시장 주가의 상대적 약세와 변동성은 투자 심리 변화 및 외부 요인에 따른 영향 가능성이 있어 주의 깊은 모니터링이 요구됨",
                    "주요 리스크는 지속 모니터링이 해석에 제한이 있음",
                ],
                "monitoring_points": ["경쟁사 동향과 제네릭 출현 가능성에 대비할 필요가 있다."],
            },
            ["규제 리스크 확인 필요"],
        )
        rendered = json.dumps(limitations, ensure_ascii=False)
        for marker in ("필요하다", "필요가 있다", "낮추어야", "검토가 필요", "확인 필요"):
            self.assertNotIn(marker, rendered)
        self.assertNotIn("해석이 해석에 제한", rendered)
        self.assertNotIn("변동성에 지속 관찰 대상", rendered)
        self.assertNotIn("모니터링할 해석에 제한", rendered)
        self.assertNotIn("관찰할 해석에 제한", rendered)
        self.assertNotIn("모니터링이 해석에 제한", rendered)
        self.assertNotIn("는은", rendered)
        self.assertNotIn("은은", rendered)
        self.assertNotIn("리스크은", rendered)

    def test_consolidate_limitations_groups_noisy_sy_revise_items(self) -> None:
        limitations = consolidate_limitations(
            {
                "data_limitations": [
                    "2025 Q3 YTD와 2024 ANNUAL FULL_YEAR는 집계 기준과 기간이 달라 동일 기간 YoY 비교로 단정하기 어려움",
                    "부채 부담 판단 시 업종 평균, 만기 구조 등 추가 비교 지표 및 상세 근거 부족",
                ],
                "interpretation_limitations": [
                    "뉴스 촉매와 재무 수치 사이의 직접 연결성은 제한적이다.",
                    "SY revise: 세노바메이트 미국 및 글로벌 매출 성장과 2025년 3분기 누적 매출액 증가 (뉴스 근거는 충분하나 재무 근거가 부족하거나 기간 차이로 인해 해석에 제한이 있다.)",
                    "SY revise: 연구개발 및 판매관리비 투자 확대와 2025년 3분기 SG&A 비용 감소 (뉴스에서 투자 확대가 언급되나 재무 지표와의 구체적 연결이 부족하다.)",
                    "시장 주가 반응은 다양한 요인에 의해 결정되므로 재무 지표와 1:1 대응하지 않음",
                    "추가 검토 전까지 해석에 제한이 있다.",
                ],
                "monitoring_points": [
                    "시장 가격 신호와 펀더멘털 사이의 직접 연결성은 제한적이다.",
                ],
            },
            {
                "risk_view": {
                    "regulatory_risks": ["미국 FDA 안전성 조사 대상 추가"],
                    "market_risks": ["20일 초과수익률 부진과 상대강도 약세"],
                    "execution_risks": ["인도 제네릭 출현 가능성과 글로벌 관세 정책 변화"],
                }
            },
        )
        rendered = json.dumps(limitations, ensure_ascii=False)
        self.assertNotIn("SY revise:", rendered)
        self.assertNotIn("추가 검토 전까지 해석에 제한", rendered)
        self.assertNotIn("News SY 검증에서 일부 뉴스-재무-시장 연결 주장이 revise로 분류", rendered)
        self.assertIn("2025 Q3 YTD와 2024 ANNUAL FULL_YEAR", rendered)
        self.assertIn("부채 부담 판단 시 업종 평균", rendered)
        self.assertIn("뉴스 촉매와 재무 수치 사이의 직접 연결성은 제한적", rendered)
        self.assertIn("시장 가격 신호와 펀더멘털 사이의 직접 연결성은 제한적", rendered)
        self.assertNotIn("미국 FDA 안전성 조사 및 규제 리스크는 지속 관찰 대상", rendered)
        self.assertNotIn("인도 제네릭 출현 가능성, 글로벌 관세 정책 변화", rendered)

    def test_build_decision_basis_card_has_object_arrays_without_confidence(self) -> None:
        report = _strategy_report()
        report["target_run_key"] = "SK바이오팜_20251031"
        report["source_files"] = {
            "target_financial": "/tmp/financial.json",
            "target_news": "/tmp/news.json",
            "target_yfinance": "/tmp/yfinance.json",
            "competitor_reports": [],
        }
        report["investment_thesis"]["thesis_3"] = "Buy로 상향하기에는 기간 기준 차이와 상대성과 약세가 남아 있다."
        payload = build_decision_basis_card(report)
        validate_decision_basis_card(payload)
        card = payload["decision_basis_card"]
        self.assertEqual(card["final_recommendation"], "Hold")
        self.assertNotIn("recommendation_confidence", card)
        self.assertNotIn("why_not_buy", card)
        self.assertNotIn("why_not_sell", card)
        self.assertGreater(len(card["basis_items"]), 0)
        self.assertGreater(len(card["risk_items"]), 0)
        self.assertGreater(len(card["strong_claims_in_report"]), 0)
        self.assertIn("reasoning", card["risk_items"][0])
        self.assertEqual(card["risk_items"][0]["claim"], card["risk_items"][0]["evidence"][0])
        self.assertEqual(card["risk_items"][0]["direction"], "source_text")
        self.assertIsInstance(card["basis_items"][0]["evidence"], list)
        self.assertIsInstance(card["basis_items"][0]["source_sections"], list)
        self.assertIsInstance(card["basis_items"][0]["critique_focus"], list)
        self.assertIsInstance(card["strong_claims_in_report"][0]["source_sections"], list)
        self.assertNotIn("source_section", card["strong_claims_in_report"][0])
        self.assertNotIn("claim_type", card["strong_claims_in_report"][0])


def _write_source_reports(output_root: Path, run_key: str) -> None:
    _write_json(output_root / "Financial" / run_key / "final_report.json", _financial_report())
    _write_json(output_root / "News" / run_key / "final_report.json", _news_report())
    _write_json(output_root / "Y_Finance" / run_key / "final_report.json", _yfinance_report())


def _write_final_validations(output_root: Path, run_key: str) -> None:
    _write_json(
        output_root / "Financial" / run_key / "final_validation.json",
        {
            "validation_summary": {"overall_status": "pass", "total_claims": 1},
            "claim_validations": [
                {
                    "claim_id": "F001",
                    "claim_ko": "2025 Q3 YTD 기준 매출 흐름은 증가로 해석된다.",
                    "answer_1_ko": "2025 Q3 YTD 매출 5,011억원이 근거다.",
                    "answer_2_ko": "2025 Q3 YTD는 1~9월 누적이고 2024년 연간은 1~12월 기준이라 동일 기간 YoY 비교는 제한적이다.",
                    "evidence_refs": ["E001"],
                    "decision": "keep",
                }
            ],
        },
    )
    _write_json(
        output_root / "News" / run_key / "final_validation.json",
        {
            "summary": {"total_claims": 1, "verified_count": 1},
            "claim_validations": [
                {
                    "claim_id": "NCLAIM_007",
                    "section": "analysis_blocks.news_only.negative_signals[0]",
                    "claim": "미국 FDA 안전성 조사, 글로벌 관세 정책 변화, 인도 제네릭 출현 가능성",
                    "answer_round_1_summary": "미국 FDA 안전성 조사와 글로벌 관세 정책 변화, 인도 제네릭 출현 가능성이 구체 리스크다.",
                    "answer_round_2_summary": "세 리스크는 뉴스에서 확인되나 영향 범위는 불확실하다.",
                    "evidence_ids_used": ["NEWS001"],
                    "decision": "keep",
                    "support_level": "supported",
                }
            ],
        },
    )
    verified_handoff = _news_report()
    verified_handoff["report_status"] = "sy_validation_reflected"
    verified_handoff["verification_summary"] = {"total_claims": 1, "verified_count": 1, "revised_count": 0}
    verified_handoff["output"]["analysis_blocks"]["news_plus_financial_plus_market"]["strategy_handoff_notes"] = [
        "뉴스 촉매는 재무 수치의 직접 증거가 아님",
        "SY 검증 결과 News Agent 핵심 주장 1건 중 revise 0건으로 분류됨",
        "SY revise: 재무 도메인 근거가 부족하므로 표현 약화 필요",
    ]
    _write_json(
        output_root / "News" / run_key / "output" / "sy_agent" / "news_agent_verified_handoff.json",
        verified_handoff,
    )
    _write_json(
        output_root / "News" / run_key / "output" / "sy_agent" / "sy_claim_validations.json",
        {
            "summary": {"total_claims": 1, "verified_count": 1, "revised_count": 0},
            "claim_validations": [
                {
                    "claim_id": "NCLAIM_007",
                    "section": "analysis_blocks.news_only.negative_signals[0]",
                    "claim": "미국 FDA 안전성 조사, 글로벌 관세 정책 변화, 인도 제네릭 출현 가능성",
                    "answer_round_1_summary": "미국 FDA 안전성 조사와 글로벌 관세 정책 변화, 인도 제네릭 출현 가능성이 구체 리스크다.",
                    "answer_round_2_summary": "세 리스크는 뉴스에서 확인되나 영향 범위는 불확실하다.",
                    "evidence_ids_used": ["NEWS001"],
                    "decision": "keep",
                    "support_level": "supported",
                }
            ],
        },
    )
    _write_json(
        output_root / "Y_Finance" / run_key / "final_validation.json",
        {
            "summary": {"total_claims": 1, "verified_count": 1},
            "verified_claims": [
                {
                    "claim_id": "market_relative",
                    "section": "market_relative",
                    "claim": "시장 대비 상대성과 약세",
                    "yfinance_answer": "20일 초과수익률과 60일 상대강도가 마이너스다.",
                    "evidence_used": ["stock_excess_return_20d"],
                    "decision": "keep",
                    "support_level": "supported",
                }
            ],
        },
    )


def _write_competitor(output_root: Path, run_key: str, company_name: str) -> Path:
    path = output_root / "Competitor" / run_key / "competitor_summary_report.json"
    _write_json(
        path,
        {
            "agent_name": "Competitor Agent",
            "company": {"company_name": company_name, "run_key": run_key, "ticker": "000000.KQ", "as_of_date": "2025-10-31"},
            "summary": f"{company_name}는 성장 이슈와 수익성 리스크가 함께 있다.",
            "strengths": ["사업 확장", "현금 보유"],
            "risks": ["EPS 부담", "주가 변동성"],
            "data_gaps": [],
        },
    )
    return path


def _write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")


def _content_plan() -> dict:
    return {
        "target_company": "SK바이오팜",
        "target_core_summary": "재무 개선은 확인되나 뉴스와 시장 리스크가 혼재한다.",
        "target_strength_candidates": ["매출 증가", "마진 개선"],
        "target_risk_candidates": ["상대성과 약세", "기간 기준 차이"],
        "competitor_context": [
            {"company_name": "더블유에스아이", "summary": "사업 확장 중", "strengths": ["사업 확장"], "risks": ["EPS 부담"]}
        ],
        "comparison_points": {
            "target_possible_advantages": ["재무 개선"],
            "target_possible_disadvantages": ["시장 상대성과 약세"],
            "mixed_or_uncertain_points": ["기간 기준 차이"],
        },
        "decision_constraints": ["YTD와 연간 비교 기준 차이", "주가 상승은 펀더멘털 직접 증거가 아님"],
        "report_outline": [
            "1. Investment Summary",
            "2. Target Company Analysis",
            "3. Competitor Context",
            "4. Key Strengths",
            "5. Key Risks",
            "6. Final Recommendation",
        ],
    }


def _strategy_decision_output() -> dict:
    report = _strategy_report()
    return {
        "strategy_report": report,
        "decision_basis_by_section": _decision_basis_by_section(report),
    }


def _decision_basis_by_section(report: dict) -> dict:
    basis = {
        "final_recommendation.summary": {
            "section_path": "final_recommendation.summary",
            "opinion_text": "재무 개선은 긍정적이나 시장 상대성과와 리스크가 혼재해 Hold로 판단한다.",
            "basis_summary": "Financial 개선 신호와 YFinance 상대성과 약세가 동시에 있어 Hold 의견으로 작성했다.",
            "key_numbers": [],
            "source_evidence": [
                {
                    "agent": "YFinance",
                    "claim_id": "market_relative",
                    "evidence_text": "20일 초과수익률과 60일 상대강도가 마이너스다.",
                    "source_path": "/tmp/yfinance_validation.json",
                    "source_section": "market_relative",
                    "evidence_ids": ["stock_excess_return_20d"],
                }
            ],
            "limitations": ["시장 가격 신호는 펀더멘털 직접 증거가 아니다."],
        },
        "financial_view.revenue": {
            "section_path": "financial_view.revenue",
            "opinion_text": "매출 증가 신호가 있다.",
            "basis_summary": "DART 검증에서 2025 Q3 YTD 매출 5,011억원이 확인되어 매출 증가 신호로 작성했다.",
            "key_numbers": ["2025 Q3 YTD", "5,011억원"],
            "source_evidence": [
                {
                    "agent": "Financial",
                    "claim_id": "F001",
                    "evidence_text": "2025 Q3 YTD 매출 5,011억원이 근거다.",
                    "source_path": "/tmp/financial_validation.json",
                    "source_section": "claim_validation[0]",
                    "evidence_ids": ["E001"],
                }
            ],
            "limitations": ["2025 Q3 YTD와 2024년 연간은 기간 기준이 다르다."],
        },
        "risk_view.regulatory_risks[0]": {
            "section_path": "risk_view.regulatory_risks[0]",
            "opinion_text": "규제 리스크",
            "basis_summary": "News 검증에서 미국 FDA 안전성 조사와 글로벌 관세 정책 변화가 구체 리스크로 확인되어 규제 리스크로 작성했다.",
            "key_numbers": [],
            "source_evidence": [
                {
                    "agent": "News",
                    "claim_id": "NCLAIM_007",
                    "evidence_text": "미국 FDA 안전성 조사와 글로벌 관세 정책 변화, 인도 제네릭 출현 가능성이 구체 리스크다.",
                    "source_path": "/tmp/news_validation.json",
                    "source_section": "analysis_blocks.news_only.negative_signals[0]",
                    "evidence_ids": ["NEWS001"],
                }
            ],
            "limitations": ["뉴스에서 확인되나 영향 범위는 불확실하다."],
        },
    }
    for section_path, opinion_text in iter_editable_report_opinions(report):
        basis.setdefault(
            section_path,
            {
                "section_path": section_path,
                "opinion_text": opinion_text,
                "basis_summary": (
                    f"Strategy 입력의 Financial, News, YFinance 근거를 종합해 {section_path} 위치의 의견을 작성했다."
                ),
                "key_numbers": [],
                "source_evidence": [
                    {
                        "agent": "Strategy",
                        "claim_id": "",
                        "evidence_text": "테스트 입력 bundle에서 해당 의견의 근거가 확인된다.",
                        "source_path": "/tmp/strategy_input_bundle.json",
                        "source_section": section_path,
                        "evidence_ids": [],
                    }
                ],
                "limitations": [],
            },
        )
    return basis


def _strategy_report() -> dict:
    return {
        "agent_name": "Strategy Agent",
        "target_company_name": "SK바이오팜",
        "final_recommendation": {
            "opinion": "Hold",
            "summary": "재무 개선은 긍정적이나 시장 상대성과와 리스크가 혼재해 Hold로 판단한다.",
        },
        "investment_thesis": {
            "thesis_1": "DART 기준 매출 증가와 마진 개선이 확인된다.",
            "thesis_2": "뉴스/시장 리스크가 혼재해 공격적 Buy보다는 Hold가 적절하다.",
            "thesis_3": "Buy로 상향하기에는 기간 기준 차이와 상대성과 약세가 남아 있다.",
        },
        "financial_view": {
            "revenue": "매출 증가 신호가 있다.",
            "profitability": "마진 개선 신호가 있다.",
            "cash_flow": "현금흐름은 안정적으로 해석된다.",
            "balance_sheet": "재무구조는 안정적이다.",
            "financial_interpretation": "DART 기준 개선 신호가 있다.",
        },
        "catalyst_view": {
            "positive_catalysts": ["성장 catalyst"],
            "business_expansion": ["신사업 확장"],
        },
        "risk_view": {
            "financial_risks": ["기간 기준 차이"],
            "regulatory_risks": ["규제 리스크"],
            "market_risks": ["상대성과 약세"],
            "execution_risks": ["신사업 실행 리스크"],
        },
        "market_price_view": {
            "price_trend": "절대 흐름은 양호하다.",
            "volume": "거래량은 보조 신호다.",
            "relative_strength": "상대성과가 약하다.",
            "market_interpretation": "주가 흐름은 펀더멘털 직접 증거가 아니므로 신중히 본다.",
        },
        "cross_agent_consistency_check": {
            "confirmed_signals": ["Financial 개선 신호"],
            "mixed_conflicting_signals": ["News/Market 리스크 혼재"],
            "strategy_implication": "제약 조건 존재로 Hold가 적절하다.",
        },
        "peer_competitor_positioning": {
            "competitor_summary": ["더블유에스아이: 사업 확장 이슈는 있으나 수익성 리스크가 있다."],
            "target_relative_strength": ["매출 증가"],
            "target_relative_weakness": ["상대성과 약세"],
            "peer_based_investment_implication": "경쟁사 context는 보조 근거다.",
        },
        "key_strengths": ["매출 증가", "마진 개선"],
        "key_risks": ["기간 기준 차이", "상대성과 약세"],
        "final_rationale": {
            "why_buy_hold_sell": "Financial 개선 신호와 News/Market 리스크 혼재를 감안해 Hold로 판단한다.",
        },
        "limitations": {
            "data_limitations": ["2025 Q3 YTD와 2024 연간 기준 차이"],
            "interpretation_limitations": ["뉴스는 재무 직접 증거가 아님"],
            "monitoring_points": ["규제 리스크 확인 필요"],
        },
        "source_files": {},
    }


def _financial_report() -> dict:
    return {
        "target_company": "SK바이오팜",
        "ticker": "326030.KS",
        "corp_code": "00878696",
        "as_of_date": "2025-10-31",
        "main_view": {
            "summary": "DART 기준 매출과 마진 개선이 확인된다.",
            "direction": "positive",
            "primary_basis": ["매출 증가", "공헌이익률 개선", "판관비율 하락", "EPS 흑자"],
            "main_cautions": ["YTD와 연간 비교 기준 차이", "주가 상승은 펀더멘털 직접 증거가 아님"],
        },
        "sy_handoff": {
            "reconciliation_flags": [
                {"flag_ko": "시장 상대성과 혼재 시 가격 확인 강도를 낮춰야 함", "severity": "medium"}
            ]
        },
    }


def _news_report() -> dict:
    return {
        "output": {
            "target_entity": {
                "company_name": "SK바이오팜",
                "ticker": "326030.KS",
                "corp_code": "00878696",
                "as_of_date": "2025-10-31",
            },
            "analysis_blocks": {
                "news_plus_financial_plus_market": {
                    "strategy_handoff_notes": ["뉴스 촉매는 재무 수치의 직접 증거가 아님"],
                }
            },
        }
    }


def _yfinance_report() -> dict:
    return {
        "target_company": "SK바이오팜",
        "ticker": "326030.KS",
        "as_of_date": "2025-10-31",
        "main_view": {"summary": "시장 데이터는 상승과 상대 약세가 혼재한다.", "direction": "상승세 우위, 상대적 약세"},
        "cross_data_reconciliation": {
            "news_plus_dart_plus_market": {
                "divergences": [{"point": "20일 초과수익률 약세", "cross_analysis": "시장 대비 성과가 약하다."}]
            }
        },
    }


if __name__ == "__main__":
    unittest.main()
