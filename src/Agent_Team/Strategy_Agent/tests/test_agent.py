"""Tests for Strategy Agent workflow."""

from __future__ import annotations

import json
import re
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from Agent_Team.Strategy_Agent.agent import (
    PROMPTS_DIR,
    build_decision_basis_card,
    build_strategy_decision_packet,
    build_strategy_input_bundle,
    build_strategy_llm_packet,
    content_plan_response_format,
    iter_editable_report_opinions,
    normalize_decision_basis_by_section,
    normalize_basis_source_evidence,
    normalize_strategy_decision_output,
    normalize_strategy_report,
    run_strategy_agent,
    sanitize_strategy_input_report,
    strategy_decision_output_schema,
    validate_decision_basis_by_section,
    validate_decision_basis_card,
    validate_content_plan,
    validate_strategy_report,
)


class StrategyAgentTests(unittest.TestCase):
    def test_evidence_catalog_corrects_mislabeled_basis_agent(self) -> None:
        packet = build_strategy_llm_packet(_packet_fixture())

        rows = normalize_basis_source_evidence(
            [
                {
                    "agent": "Competitor",
                    "claim_id": "",
                    "source_section": "",
                    "evidence_ids": ["NEWS_RAW_1"],
                }
            ],
            input_bundle=packet,
        )

        self.assertEqual(rows[0]["agent"], "News")
        self.assertEqual(rows[0]["source_section"], "evidence_catalog.NEWS_RAW_1")

    def test_limitation_id_resolves_to_shared_limitation_catalog(self) -> None:
        packet = build_strategy_llm_packet(_packet_fixture())

        rows = normalize_basis_source_evidence(
            [
                {
                    "agent": "Financial",
                    "claim_id": "LIMIT_001",
                    "source_section": "limitations",
                    "evidence_ids": [],
                }
            ],
            input_bundle=packet,
        )

        self.assertEqual(rows[0]["source_section"], "limitations.LIMIT_001")
        self.assertEqual(rows[0]["claim_id"], "")

    def test_decision_schema_requires_refs_for_every_reader_facing_section(self) -> None:
        refs = strategy_decision_output_schema()["evidence_refs_by_section"]

        self.assertIn("peer_competitor_positioning", refs)
        self.assertIn("final_recommendation", refs)
        self.assertIn("limitations", refs)
        self.assertNotIn("<top-level strategy_report section or exact reader-facing path>", refs)

    def test_strategy_llm_packet_is_referenced_only_and_has_no_audit_paths(self) -> None:
        bundle = _packet_fixture()

        packet = build_strategy_llm_packet(bundle)

        self.assertNotIn("target_reports", packet)
        self.assertNotIn("target_validation_evidence", packet)
        self.assertNotIn("input_metadata", packet)
        self.assertEqual(set(packet["evidence_catalog"]), {"E001", "NEWS_RAW_1"})
        self.assertEqual(len(packet["secondary_context_assessments"]), 1)
        self.assertNotIn("/home/", json.dumps(packet, ensure_ascii=False))

    def test_content_plan_ids_filter_the_decision_packet(self) -> None:
        packet = build_strategy_llm_packet(_packet_fixture())
        plan = {
            "target_company": "대상기업",
            "positive_claim_ids": ["F001"],
            "negative_claim_ids": [],
            "neutral_claim_ids": [],
            "catalyst_claim_ids": ["N001"],
            "risk_claim_ids": [],
            "context_assessment_ids": ["CTX_FINANCIAL_001"],
            "peer_metric_ids": [],
            "limitation_ids": ["LIMIT_001"],
            "section_plan": {
                "investment_thesis": ["F001", "N001"],
                "financial_view": ["F001"],
                "business_mix_view": ["F001"],
                "catalyst_view": ["N001"],
                "risk_view": [],
                "market_price_view": ["CTX_FINANCIAL_001"],
                "valuation_view": [],
                "cross_agent_consistency_check": ["CTX_FINANCIAL_001"],
                "peer_competitor_positioning": [],
                "decision_balance": ["F001", "N001"],
                "limitations": ["LIMIT_001"],
            },
        }

        validate_content_plan(plan, llm_packet=packet)
        decision = build_strategy_decision_packet(packet, plan)

        self.assertEqual(
            {claim["claim_id"] for claims in decision["claim_ledger"].values() for claim in claims},
            {"F001", "N001"},
        )
        self.assertEqual(set(decision["evidence_catalog"]), {"E001", "NEWS_RAW_1"})

    def test_content_plan_rejects_unknown_claim_id(self) -> None:
        packet = build_strategy_llm_packet(_packet_fixture())
        plan = _content_plan()
        plan["positive_claim_ids"] = ["UNKNOWN"]

        with self.assertRaisesRegex(ValueError, "Unknown claim IDs"):
            validate_content_plan(plan, llm_packet=packet)

    def test_content_plan_response_format_enumerates_only_supplied_ids(self) -> None:
        packet = build_strategy_llm_packet(_packet_fixture())

        response_format = content_plan_response_format(packet)
        schema = response_format["json_schema"]["schema"]
        properties = schema["properties"]
        definitions = schema["$defs"]

        self.assertEqual(response_format["type"], "json_schema")
        self.assertTrue(response_format["json_schema"]["strict"])
        self.assertEqual(properties["target_company"]["enum"], ["대상기업"])
        self.assertEqual(
            set(definitions["claim_id"]["enum"]),
            {"F001", "N001"},
        )
        self.assertEqual(
            definitions["context_id"]["enum"],
            ["CTX_FINANCIAL_001"],
        )
        section_ids = definitions["supplied_id"]["enum"]
        self.assertEqual(set(section_ids), {"F001", "N001", "CTX_FINANCIAL_001", "LIMIT_001"})
        self.assertNotIn("NEWS_RAW_1", section_ids)
        self.assertEqual(
            properties["section_plan"]["properties"]["investment_thesis"]["items"]["$ref"],
            "#/$defs/supplied_id",
        )

    def test_run_strategy_agent_writes_outputs_with_two_integrated_llm_steps(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            run_key = "SK바이오팜_20251031"
            _write_source_reports(root, run_key)
            output_dir = root / "Strategy" / run_key

            with patch(
                "Agent_Team.Strategy_Agent.agent.call_llm_json",
                side_effect=[
                    _content_plan(),
                    _strategy_decision_output(),
                ],
            ) as mocked_llm:
                report = run_strategy_agent(
                    target_company_name="SK바이오팜",
                    target_run_key=run_key,
                    target_financial_path=root / "Financial" / run_key / "final_report.json",
                    target_news_path=root / "News" / run_key / "final_report.json",
                    target_yfinance_path=root / "Y_Finance" / run_key / "final_report.json",
                    output_dir=output_dir,
                    llm_provider="openai",
                    llm_model="test-model",
                    env_file=None,
                    packet_version="v1",
                )

            self.assertEqual(mocked_llm.call_count, 2)
            self.assertEqual(report["final_recommendation"]["opinion"], "Hold")
            self.assertNotIn("confidence", report["final_recommendation"])
            self.assertIn("thesis_3", report["investment_thesis"])
            self.assertEqual(report["final_recommendation"]["investment_horizon"], "12개월")
            self.assertEqual(report["final_recommendation"]["evidence_sufficiency"], "medium")
            self.assertNotIn("upcoming_catalysts", report["catalyst_view"])
            self.assertTrue((output_dir / "strategy_input_bundle.json").exists())
            self.assertTrue((output_dir / "strategy_llm_packet.json").exists())
            self.assertTrue((output_dir / "strategy_decision_packet.json").exists())
            self.assertTrue((output_dir / "strategy_content_plan.json").exists())
            self.assertTrue((output_dir / "strategy_report.json").exists())
            self.assertTrue((output_dir / "strategy_report.md").exists())
            self.assertTrue((output_dir / "decision_basis_by_section.json").exists())
            self.assertTrue((output_dir / "decision_basis_card.json").exists())

            bundle = json.loads((output_dir / "strategy_input_bundle.json").read_text(encoding="utf-8"))
            self.assertNotIn("competitor_reports", bundle)
            self.assertNotIn("competitor_report_paths", bundle["input_metadata"])
            self.assertIn("YTD와 연간 비교 기준 차이", json.dumps(bundle["decision_constraints"], ensure_ascii=False))
            self.assertIn("낮춰야", json.dumps(bundle["decision_constraints"], ensure_ascii=False))

            markdown = (output_dir / "strategy_report.md").read_text(encoding="utf-8")
            self.assertIn("## 0. Target Company Name: SK바이오팜", markdown)
            self.assertIn("- Opinion: Hold", markdown)
            self.assertIn("- Thesis 3:", markdown)
            self.assertNotIn("- Confidence:", markdown)
            self.assertNotIn("- Upcoming Catalysts:", markdown)
            self.assertIn("## 8. Valuation View", markdown)
            self.assertIn("## 10. Peer / Competitor Positioning", markdown)
            self.assertIn("## 11. Decision Balance", markdown)
            self.assertIn("## 12. Final Rationale", markdown)
            limitations_markdown = markdown.split("## 13. Limitations", 1)[1]
            self.assertIn("직접 증거", limitations_markdown)
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
            self.assertEqual(revenue_basis["source_evidence"][0]["claim_id"], "")
            self.assertNotIn("basis_summary", revenue_basis)

    def test_run_strategy_agent_rejects_invalid_report_without_repair(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            run_key = "SK바이오팜_20251031"
            _write_source_reports(root, run_key)
            invalid_report = _strategy_report()
            invalid_report["limitations"] = ["구조가 잘못된 제한사항"]
            output_dir = root / "Strategy" / run_key

            with patch(
                "Agent_Team.Strategy_Agent.agent.call_llm_json",
                side_effect=[
                    _content_plan(),
                    {"strategy_report": invalid_report},
                ],
            ) as mocked_llm:
                with self.assertRaisesRegex(ValueError, "limitations must be an object"):
                    run_strategy_agent(
                        target_company_name="SK바이오팜",
                        target_run_key=run_key,
                        target_financial_path=root / "Financial" / run_key / "final_report.json",
                        target_news_path=root / "News" / run_key / "final_report.json",
                        target_yfinance_path=root / "Y_Finance" / run_key / "final_report.json",
                        output_dir=output_dir,
                        llm_provider="openai",
                        llm_model="test-model",
                        env_file=None,
                        packet_version="v1",
                    )

            self.assertEqual(mocked_llm.call_count, 2)
            self.assertFalse((output_dir / "strategy_report.json").exists())

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
            )

            decision_output = _strategy_decision_output()
            report, basis_payload = normalize_strategy_decision_output(decision_output, bundle)

            validate_strategy_report(report)
            validate_decision_basis_by_section(basis_payload, report)
            basis_map = basis_payload["decision_basis_by_section"]
            self.assertEqual(basis_map["financial_view.revenue"]["opinion_text"], report["financial_view"]["revenue"])
            self.assertIn("investment_thesis.thesis_1", basis_map)
            self.assertNotIn("key_numbers", basis_map["financial_view.revenue"])
            self.assertNotIn("basis_summary", basis_map["investment_thesis.thesis_1"])

    def test_normalize_strategy_decision_output_requires_llm_basis(self) -> None:
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
            )

            with self.assertRaisesRegex(ValueError, "evidence_refs_by_section is required"):
                normalize_strategy_decision_output({"strategy_report": _strategy_report()}, bundle)

    def test_section_level_refs_expand_to_leaf_paths(self) -> None:
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
            )
            report = normalize_strategy_report(_strategy_report(), bundle)
            packet = build_strategy_llm_packet(bundle)
            section_refs = {
                f"strategy_report.{root_name}": [
                    {
                        "agent": "Financial",
                        "source_section": "strategy_decision_packet.structured_facts.financial",
                        "claim_id": "",
                        "evidence_ids": [],
                    }
                ]
                for root_name in {
                    path.split(".", 1)[0]
                    for path, _ in iter_editable_report_opinions(report)
                }
            }

            payload = normalize_decision_basis_by_section(section_refs, report, packet)

            self.assertIn("financial_view.revenue", payload["decision_basis_by_section"])
            self.assertEqual(
                payload["decision_basis_by_section"]["financial_view.revenue"]["source_evidence"][0]["source_section"],
                "structured_facts.financial",
            )

    def test_basis_source_metadata_drops_opinion_ids_and_invalid_bundle_paths(self) -> None:
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
            )
            report = normalize_strategy_report(_strategy_report(), bundle)
            packet = build_strategy_llm_packet(bundle)
            raw_basis = _decision_basis_by_section(report)
            revenue_source = raw_basis["financial_view.revenue"]["source_evidence"][0]
            revenue_source["claim_id"] = "OP012"
            revenue_source["evidence_ids"] = ["OP012", "E001"]
            revenue_source["source_section"] = "financial_view.revenue"
            news_source = raw_basis["risk_view.observed_risks[0].statement"]["source_evidence"][0]
            news_source["agent"] = "News"
            news_source["claim_id"] = "NCLAIM_007"
            news_source["source_section"] = "claim_ledger.news[0]"

            payload = normalize_decision_basis_by_section(raw_basis, report, packet)
            validate_decision_basis_by_section(payload, report)

            normalized = payload["decision_basis_by_section"]["financial_view.revenue"]["source_evidence"][0]
            self.assertEqual(normalized["claim_id"], "")
            self.assertEqual(normalized["evidence_ids"], [])
            self.assertEqual(normalized["source_section"], "")
            news_source = payload["decision_basis_by_section"]["risk_view.observed_risks[0].statement"]["source_evidence"][0]
            self.assertEqual(news_source["source_section"], "claim_ledger.news[0]")

    def test_thesis_3_is_required_for_every_recommendation_without_fallback(self) -> None:
        report = _strategy_report()
        report["investment_thesis"].pop("thesis_3", None)

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
            )

            normalized = normalize_strategy_report(report, bundle)
            self.assertEqual(normalized["investment_thesis"].get("thesis_3"), "")
            with self.assertRaisesRegex(ValueError, "investment_thesis.thesis_3"):
                validate_strategy_report(normalized)

    def test_build_strategy_input_bundle_contains_no_competitor_prose(self) -> None:
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
            )

            self.assertNotIn("competitor_reports", bundle)
            self.assertNotIn("competitor_report_paths", bundle["input_metadata"])
            self.assertTrue(bundle["evidence_hierarchy"])

    def test_input_bundle_preserves_explicit_peer_metrics(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            run_key = "대상기업_20251031"
            _write_source_reports(root, run_key)
            peer_path = root / "Competitor" / run_key / "peer_comparison_dataset.json"
            _write_json(
                peer_path,
                {
                    "target_company": "대상기업",
                    "peer_groups": {
                        "domestic_peers": [{"run_key": "비교기업_20251031", "company_name": "비교기업"}]
                    },
                    "metrics": [
                        {"company_name": "대상기업", "peer_group": "target", "valuation_metrics": {"trailing_pe": 20.0}},
                        {"company_name": "비교기업", "peer_group": "domestic_peer", "valuation_metrics": {"trailing_pe": 10.0}},
                    ],
                    "comparison_limits": ["동일 기준일 배수만 비교"],
                },
            )

            bundle = build_strategy_input_bundle(
                target_company_name="대상기업",
                target_run_key=run_key,
                target_financial_path=root / "Financial" / run_key / "final_report.json",
                target_news_path=root / "News" / run_key / "final_report.json",
                target_yfinance_path=root / "Y_Finance" / run_key / "final_report.json",
                peer_comparison_path=peer_path,
            )

            self.assertEqual(len(bundle["peer_comparison"]["metrics"]), 2)
            self.assertEqual(bundle["peer_comparison"]["metrics"][1]["company_name"], "비교기업")
            self.assertIn(
                "explicit_pairwise_peer_comparison",
                [item["topic"] for item in bundle["evidence_hierarchy"]],
            )

    def test_strategy_input_removes_validation_workflow_narration(self) -> None:
        report = {
            "target_company": "대상기업",
            "secondary_context_assessment": [
                {
                    "context_id": "CTX_001",
                    "source_domain": "financial",
                    "effect": "contradicts",
                    "statement": "시장 대비 상대성과가 약하다",
                    "primary_evidence_ids": ["YF_001"],
                    "secondary_evidence_ids": ["DART_001"],
                    "usage": "framing_and_limitation_only",
                    "limitation": "인과관계는 확인할 수 없다.",
                }
            ],
            "report_status": "sy_revision_reflected_no_reverification",
            "sy_validation": {"summary": {"revised_count": 1}},
        }

        cleaned = sanitize_strategy_input_report(report, "yfinance")
        serialized = json.dumps(cleaned, ensure_ascii=False)

        self.assertNotIn("SY revise", serialized)
        self.assertNotIn("report_status", serialized)
        self.assertNotIn("sy_validation", serialized)
        self.assertIn("시장 대비 상대성과가 약하다", serialized)

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
            )

            self.assertEqual(len(bundle["target_validation_evidence"]["financial"]["claims"]), 1)
            self.assertEqual(len(bundle["target_validation_evidence"]["news"]["claims"]), 1)
            self.assertNotIn("evidence_summary", bundle["target_validation_evidence"]["financial"]["claims"][0])
            self.assertNotIn("question_1", bundle["target_validation_evidence"]["financial"]["claims"][0])
            self.assertNotIn("answer_2", bundle["target_validation_evidence"]["financial"]["claims"][0])
            self.assertNotIn("report_status", bundle["target_reports"]["news"])
            rendered_news_report = json.dumps(bundle["target_reports"]["news"], ensure_ascii=False)
            self.assertNotIn("SY revise:", rendered_news_report)
            self.assertNotIn("SY 검증 결과 News Agent 핵심 주장", rendered_news_report)
            self.assertNotIn("strategy_handoff_notes", rendered_news_report)
            self.assertEqual(bundle["target_validation_evidence"]["news"]["summary"]["claim_count"], 1)
            self.assertEqual(
                bundle["target_validation_evidence"]["news"]["summary"]["evidence_use_counts"]["strong"],
                1,
            )
            self.assertTrue(
                bundle["target_validation_evidence"]["news"]["source_path"].endswith(
                    "output/sy_agent/sy_claim_validations.json"
                )
            )
            self.assertEqual(bundle["target_validation_evidence"]["news"]["claims"][0]["evidence_use"], "strong")
            validation_json = json.dumps(bundle["target_validation_evidence"], ensure_ascii=False)
            self.assertNotIn('"decision"', validation_json)
            self.assertNotIn("raw_decision", validation_json)
            self.assertNotIn("revision_suggestion", validation_json)
            self.assertNotIn("hallucination_candidate", validation_json)

            report = _strategy_report()
            report["target_run_key"] = run_key
            vague_risk = "뉴스 주요 리스크 이슈가 재무 개선 지속성에 대한 주의 요인으로 작용"
            report["risk_view"]["observed_risks"].append(
                {"category": "execution", "statement": vague_risk}
            )
            normalized = normalize_strategy_report(report, bundle)
            rendered = json.dumps(normalized, ensure_ascii=False)
            self.assertIn("뉴스 주요 리스크 이슈", rendered)
            validate_strategy_report(normalized)
            self.assertGreater(len(normalized["opinion_index"]), 0)
            self.assertEqual(normalized["opinion_index"][0]["id"], "OP001")

    def test_low_evidence_sufficiency_is_valid_for_buy_hold_and_sell(self) -> None:
        for opinion in ("Buy", "Hold", "Sell"):
            with self.subTest(opinion=opinion):
                report = _strategy_report()
                report["final_recommendation"].update(
                    {
                        "opinion": opinion,
                        "summary": f"입력 근거를 종합한 {opinion} 판단이다.",
                        "evidence_sufficiency": "low",
                        "evidence_sufficiency_reason": "핵심 자료 일부가 누락돼 근거 범위가 제한적이다.",
                    }
                )
                validate_strategy_report(report)

    def test_fixed_prompts_are_domain_neutral_and_have_no_hold_preference(self) -> None:
        prompt_text = "\n".join(
            path.read_text(encoding="utf-8")
            for path in sorted(PROMPTS_DIR.glob("*.md"))
        )
        lowered = prompt_text.lower()

        for forbidden in ("sk바이오팜", "세노바", "fda", "임상", "제네릭", "generic-entry"):
            self.assertNotIn(forbidden, lowered)
        self.assertIsNone(re.search(r"20\d{2}", prompt_text))
        self.assertNotIn("prefer hold", lowered)
        self.assertNotIn("unless the inputs clearly support sell", lowered)

    def test_schema_validation_preserves_reader_facing_language_without_rewriting(self) -> None:
        report = _strategy_report()
        report["limitations"]["data_limitations"] = ["성장률 해석에 주의가 필요하다."]

        validate_strategy_report(report)
        self.assertEqual(report["limitations"]["data_limitations"], ["성장률 해석에 주의가 필요하다."])

    def test_strategy_report_rejects_ungrounded_large_integer(self) -> None:
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
            )
            report = normalize_strategy_report(_strategy_report(), bundle)
            report["financial_view"]["revenue"] = "입력에 없는 매출 3,540,423,161,121원이다."

            with self.assertRaisesRegex(ValueError, "ungrounded large integer"):
                validate_strategy_report(report, input_bundle=bundle)

    def test_validate_strategy_report_rejects_duplicate_limitation_buckets(self) -> None:
        report = _strategy_report()
        duplicate = "뉴스 촉매와 재무 수치 사이의 직접 연결성은 제한적이다."
        report["limitations"]["data_limitations"] = [duplicate]
        report["limitations"]["interpretation_limitations"] = [duplicate]

        with self.assertRaisesRegex(ValueError, "duplicate across buckets"):
            validate_strategy_report(report)

    def test_build_decision_basis_card_has_object_arrays_without_confidence(self) -> None:
        report = _strategy_report()
        report["target_run_key"] = "SK바이오팜_20251031"
        report["source_files"] = {
            "target_financial": "/tmp/financial.json",
            "target_news": "/tmp/news.json",
            "target_yfinance": "/tmp/yfinance.json",
        }
        report["investment_thesis"]["thesis_3"] = "Buy로 상향하기에는 기간 기준 차이와 상대성과 약세가 남아 있다."
        basis_payload = {
            "target_company_name": report["target_company_name"],
            "target_run_key": report["target_run_key"],
            "final_recommendation": report["final_recommendation"]["opinion"],
            "decision_basis_by_section": _decision_basis_by_section(report),
            "basis_card_version": "1.0",
        }
        payload = build_decision_basis_card(report, basis_payload)
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
        self.assertNotEqual(card["risk_items"][0]["claim"], card["risk_items"][0]["evidence"][0])
        self.assertIn("FDA", card["risk_items"][0]["evidence"][0])
        self.assertEqual(card["risk_items"][0]["direction"], "source_text")
        self.assertIsInstance(card["basis_items"][0]["evidence"], list)
        self.assertIsInstance(card["basis_items"][0]["source_sections"], list)
        self.assertIsInstance(card["basis_items"][0]["evidence_limitations"], list)
        self.assertIsInstance(card["strong_claims_in_report"][0]["source_sections"], list)
        self.assertNotIn("source_section", card["strong_claims_in_report"][0])
        self.assertNotIn("claim_type", card["strong_claims_in_report"][0])


def _write_source_reports(output_root: Path, run_key: str) -> None:
    _write_json(output_root / "Financial" / run_key / "final_report.json", _financial_report())
    _write_json(output_root / "News" / run_key / "final_report.json", _news_report())
    _write_json(output_root / "Y_Finance" / run_key / "final_report.json", _yfinance_report())


def _packet_fixture() -> dict:
    evidence_catalogs = {
        "financial": {
            "E001": {
                "evidence_id": "E001",
                "domain": "financial",
                "origin_type": "raw_source",
                "source_ref": "dart.revenue",
                "value": 100,
            },
            "UNUSED": {
                "evidence_id": "UNUSED",
                "domain": "financial",
                "origin_type": "raw_source",
                "source_ref": "dart.unused",
                "value": 1,
            },
        },
        "news": {
            "NEWS_RAW_1": {
                "evidence_id": "NEWS_RAW_1",
                "domain": "news",
                "origin_type": "raw_source",
                "source_ref": "news.1",
                "text": "신규 계약 체결",
            }
        },
        "yfinance": {},
    }
    return {
        "target_company": {"company_name": "대상기업", "run_key": "대상기업_20251031"},
        "target_reports": {
            "financial": {
                "financial_trends": {"revenue": [100]},
                "secondary_context_assessment": [
                    {
                        "context_id": "LOCAL_1",
                        "source_domain": "news",
                        "effect": "corroborates",
                        "statement": "재무 흐름과 같은 기간에 계약 사건이 확인됐다.",
                        "primary_evidence_ids": ["E001"],
                        "secondary_evidence_ids": ["NEWS_RAW_1"],
                        "usage": "framing_and_limitation_only",
                        "limitation": "인과관계는 확인할 수 없다.",
                    }
                ],
            },
            "news": {},
            "yfinance": {},
        },
        "target_validation_evidence": {
            "financial": {
                "claims": [
                    {
                        "claim_id": "F001",
                        "claim": "매출이 증가했다.",
                        "evidence_ids": ["E001"],
                        "evidence_use": "strong",
                        "limitations": [],
                    }
                ]
            },
            "news": {
                "claims": [
                    {
                        "claim_id": "N001",
                        "claim": "신규 계약이 체결됐다.",
                        "evidence_ids": ["NEWS_RAW_1"],
                        "evidence_use": "strong",
                        "limitations": [],
                    }
                ]
            },
            "yfinance": {"claims": []},
        },
        "evidence_catalogs": evidence_catalogs,
        "peer_comparison": {},
        "decision_constraints": ["기간 비교 한계가 있다."],
    }


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


def _write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")


def _content_plan() -> dict:
    sections = {
        key: []
        for key in (
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
    }
    return {
        "target_company": "SK바이오팜",
        "positive_claim_ids": [],
        "negative_claim_ids": [],
        "neutral_claim_ids": [],
        "catalyst_claim_ids": [],
        "risk_claim_ids": [],
        "context_assessment_ids": [],
        "peer_metric_ids": [],
        "limitation_ids": ["LIMIT_001"],
        "section_plan": {**sections, "limitations": ["LIMIT_001"]},
    }


def _strategy_decision_output() -> dict:
    report = _strategy_report()
    return {
        "strategy_report": report,
        "evidence_refs_by_section": _decision_basis_by_section(report),
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
                    "agent": "Financial",
                    "claim_id": "",
                    "evidence_text": "20일 초과수익률과 60일 상대강도가 마이너스다.",
                    "source_path": "",
                    "source_section": "structured_facts.financial",
                    "evidence_ids": [],
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
                    "claim_id": "",
                    "evidence_text": "2025 Q3 YTD 매출 5,011억원이 근거다.",
                    "source_path": "",
                    "source_section": "structured_facts.financial",
                    "evidence_ids": [],
                }
            ],
            "limitations": ["2025 Q3 YTD와 2024년 연간은 기간 기준이 다르다."],
        },
        "risk_view.observed_risks[0].statement": {
            "section_path": "risk_view.observed_risks[0].statement",
            "opinion_text": "규제 리스크",
            "basis_summary": "News 검증에서 미국 FDA 안전성 조사와 글로벌 관세 정책 변화가 구체 리스크로 확인되어 규제 리스크로 작성했다.",
            "key_numbers": [],
            "source_evidence": [
                {
                    "agent": "Financial",
                    "claim_id": "",
                    "evidence_text": "미국 FDA 안전성 조사와 글로벌 관세 정책 변화, 인도 제네릭 출현 가능성이 구체 리스크다.",
                    "source_path": "",
                    "source_section": "structured_facts.financial",
                    "evidence_ids": [],
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
                        "agent": "Financial",
                        "claim_id": "",
                        "evidence_text": "테스트 입력 bundle에서 해당 의견의 근거가 확인된다.",
                        "source_path": "",
                        "source_section": "structured_facts.financial",
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
            "investment_horizon": "12개월",
            "evidence_sufficiency": "medium",
            "evidence_sufficiency_reason": "재무·시장 자료는 확보됐으나 일부 사건 영향의 불확실성이 남아 있다.",
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
        "business_mix_view": {
            "revenue_composition": "주요 매출 구성 자료가 제공됐다.",
            "concentration": "상위 매출원 집중도가 높다.",
            "business_mix_interpretation": "집중도는 성장 기여와 단일 매출원 의존을 함께 보여준다.",
        },
        "catalyst_view": {
            "observed_catalysts": ["신사업 확장 이벤트"],
        },
        "risk_view": {
            "observed_risks": [
                {"category": "regulatory", "statement": "규제 리스크"},
                {"category": "business", "statement": "매출원 집중 리스크"},
                {"category": "market", "statement": "상대성과 약세"},
                {"category": "execution", "statement": "신사업 실행 리스크"},
            ],
        },
        "market_price_view": {
            "price_trend": "절대 흐름은 양호하다.",
            "volume": "거래량은 보조 신호다.",
            "relative_strength": "상대성과가 약하다.",
            "market_interpretation": "주가 흐름은 펀더멘털 직접 증거가 아니므로 신중히 본다.",
        },
        "valuation_view": {
            "selected_date_valuation": "기준일 P/E, P/B, P/S가 확인된다.",
            "peer_valuation_comparison": "명시적 peer보다 대상 배수가 높다.",
            "valuation_interpretation": "높은 배수는 성장 기대와 가격 부담을 함께 반영한다.",
        },
        "cross_agent_consistency_check": {
            "confirmed_signals": ["Financial 개선 신호"],
            "mixed_conflicting_signals": ["News/Market 리스크 혼재"],
            "strategy_implication": "제약 조건 존재로 Hold가 적절하다.",
        },
        "peer_competitor_positioning": {
            "pairwise_findings": ["SK바이오팜은 흑자이고 더블유에스아이는 적자다."],
            "comparison_limits": ["제품 구성은 한쪽만 공개됐다."],
            "peer_based_investment_implication": "경쟁사 context는 보조 근거다.",
        },
        "decision_balance": {
            "positive_evidence": ["동일 기간 매출과 수익성 개선"],
            "negative_evidence": ["시장 상대성과 약세와 높은 peer 대비 배수"],
            "balance_conclusion": "긍정·부정 근거의 균형으로 Hold 판단이 형성됐다.",
        },
        "final_rationale": {
            "why_buy_hold_sell": "Financial 개선 신호와 News/Market 리스크 혼재를 감안해 Hold로 판단한다.",
        },
        "limitations": {
            "data_limitations": ["2025 Q3 YTD와 2024 연간 기준 차이"],
            "interpretation_limitations": ["뉴스는 재무 직접 증거가 아님"],
            "monitoring_points": ["규제 리스크는 확인 전까지 해석에 제한이 있다."],
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
                "news_only": {
                    "summary": {
                        "claim": "확인된 뉴스 사건",
                        "evidence_ids": ["NEWS_RAW_1"],
                    },
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
        "secondary_context_assessment": [],
    }


if __name__ == "__main__":
    unittest.main()
