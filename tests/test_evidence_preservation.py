"""Editorial targets must not truncate valid agent prose or citations."""
import copy
import json
from pathlib import Path
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from test_annual_context import strategy_fixture
from shared.evidence_cards import card_content_sha256
from Agent_Team.Strategy_Agent.decision import (
    align_strategy_decision_evidence_plan, validate_strategy_decision,
    strategy_decision_response_format,
)
from Agent_Team.Strategy_Agent.agent import preserve_and_validate_strategy
from Agent_Team.Strategy_Agent.packet import _news_cards, _reader_limitations
from writer_handoff import build_writer_editorial_packet
from html_report_writer import (
    _normalize_text, _ensure_claim_units_visible, writer_report_response_format,
    _normalize_requested_chart_keys,
)
from html_report_validator import _validate_chart_selection_grounding, _validate_compact_text_sections
from formatted_html_renderer import build_complete_html
from jsonschema import Draft202012Validator
from orchestration.full_report_pipeline import validate_full_pipeline_outputs, FullPipelineError


class EvidencePreservationTests(unittest.TestCase):
    def test_news_agent_citations_are_not_selected_again_by_count(self):
        signals = []
        catalog = {}
        for index in range(15):
            evidence_id = f"NEWS_RAW_{index}"
            signals.append({"claim": f"독립 사건 {index}이다.", "evidence_ids": [evidence_id]})
            catalog[evidence_id] = {"source_date": f"2025-10-{index + 1:02d}", "snippet": f"사건 {index}의 원자료다."}
        report = {"analysis_blocks": {"news_only": {"positive_signals": signals}}}
        cards, omitted = _news_cards(report, {}, catalog)
        self.assertEqual(len(cards), 15)
        self.assertEqual(omitted, [])
        self.assertEqual({key for _, ids, _ in cards for key in ids}, set(catalog))
        limits = [f"판단 한계 {index}" for index in range(12)]
        self.assertEqual(len(_reader_limitations({}, limits, [])), 12)

    def many_cards(self, count=30):
        packet, context, decision, provenance = strategy_fixture()
        template = next(iter(packet["cards"].values()))
        keys = []
        for index in range(count):
            key = f"financial.observation_{index}"
            card = {**copy.deepcopy(template), "card_key": key}
            packet["cards"][key] = card
            context["evidence_cards"][key] = card
            provenance["cards"][key] = {"strategy_card_sha256": card_content_sha256(card),
                                        "source_evidence_ids": [], "source_paths": [], "source_files": []}
            keys.append(key)
        decision["strategy_brief"]["earnings_review"]["card_keys"] = keys
        return packet, context, decision, provenance, keys

    def test_all_citations_survive_alignment_and_writer_handoff(self):
        for count in (15, 30, 60):
            packet, context, decision, provenance, keys = self.many_cards(count)
            original = copy.deepcopy(decision)
            schema = strategy_decision_response_format(context)["json_schema"]["schema"]
            Draft202012Validator(schema).validate(decision)
            aligned = align_strategy_decision_evidence_plan(decision, context=context)
            validate_strategy_decision(aligned, context=context, required_horizon="12개월")
            self.assertEqual(decision, original)
            self.assertEqual(aligned["strategy_brief"], original["strategy_brief"])
            self.assertEqual(set(keys), {x["card_key"] for x in aligned["evidence_plan"]["report_context_cards"]})
            self.assertEqual(aligned, align_strategy_decision_evidence_plan(aligned, context=context))
            handoff, _ = build_writer_editorial_packet(strategy_packet=packet, strategy_decision=aligned, strategy_provenance=provenance)
            self.assertTrue(set(keys).issubset(handoff["cards"]))
            self.assertTrue(set(keys).issubset(handoff["available_card_keys_by_component"]["business_market_context"]))
            self.assertEqual(handoff["required_card_keys_by_component"]["business_market_context"], [])

    def test_decisive_evidence_and_risks_have_no_editorial_ceiling(self):
        _, context, decision, _, keys = self.many_cards(5)
        for key in keys:
            row = copy.deepcopy(decision["evidence_plan"]["decision_basis_cards"][0])
            row["card_key"] = key
            decision["evidence_plan"]["decision_basis_cards"].append(row)
        decision["key_risks"] *= 4
        schema = strategy_decision_response_format(context)["json_schema"]["schema"]
        Draft202012Validator(schema).validate(decision)
        validate_strategy_decision(align_strategy_decision_evidence_plan(decision, context=context), context=context)

    def test_invalid_and_duplicate_refs_still_fail(self):
        _, context, decision, _, keys = self.many_cards()
        decision["strategy_brief"]["outlook"]["card_keys"] = ["news.unknown"]
        with self.assertRaisesRegex(ValueError, "unknown card"):
            align_strategy_decision_evidence_plan(decision, context=context)
        decision["strategy_brief"]["outlook"]["card_keys"] = [keys[0], keys[0]]
        with self.assertRaises(ValueError):
            validate_strategy_decision(align_strategy_decision_evidence_plan(decision, context=context), context=context)

    def test_postprocessing_failure_retains_raw_and_history(self):
        _, context, decision, _ = strategy_fixture()
        decision["strategy_brief"]["outlook"]["card_keys"] = ["unknown"]
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for _ in range(2):
                with self.assertRaises(ValueError):
                    preserve_and_validate_strategy(decision, context=context, output_dir=root,
                        fingerprint="test_fingerprint", decision_horizon_profile="annual", required_horizon="12개월")
            failure = json.loads((root / "strategy_failure_report_v5.json").read_text())
            raw = json.loads(Path(failure["raw_response_path"]).read_text())
            self.assertEqual(raw["decision_output"], decision)
            self.assertEqual(failure["stage"], "decision_alignment_or_validation")
            self.assertEqual(len(list((root / "strategy_response_attempts").glob("*.json"))), 4)

    def test_writer_preserves_all_paragraphs_when_claim_is_missing(self):
        paragraphs = [f"{i}번째 설명 문단이다." for i in range(8)]
        claims = [{"claim": "추가 근거의 해석이다.", "card_keys": []}]
        normalized = _normalize_text({"paragraphs": paragraphs, "_claim_units": claims}, preserve_claim_units=True)
        self.assertEqual(normalized["paragraphs"], paragraphs + [claims[0]["claim"]])
        self.assertEqual(_ensure_claim_units_visible(normalized["paragraphs"], claims), normalized["paragraphs"])

    def test_long_chart_commentary_is_advisory_not_grounding_failure(self):
        packet, _, decision, provenance = strategy_fixture()
        handoff, _ = build_writer_editorial_packet(strategy_packet=packet, strategy_decision=decision, strategy_provenance=provenance)
        payload = {"requested_chart_keys": ["price"], "chart_selection_details": [{
            "chart_key": "price", "basis_card_keys": list(handoff["cards"]),
            "selection_reason": "가격 해석", "chart_observation": "관찰이다." * 60,
            "investment_interpretation": "가격 변동을 실적과 구분한다.",
        }]}
        self.assertEqual(_validate_chart_selection_grounding(payload, handoff, []), "pass")
        notes = []
        _validate_compact_text_sections(payload, handoff, notes)
        self.assertTrue(any("chart commentary" in x for x in notes))

    def test_writer_schema_has_no_paragraph_or_claim_ceiling(self):
        packet, _, decision, provenance = strategy_fixture()
        handoff, _ = build_writer_editorial_packet(strategy_packet=packet, strategy_decision=decision, strategy_provenance=provenance)
        schema = writer_report_response_format(writer_handoff=handoff)["json_schema"]["schema"]
        sections = schema["properties"]["sections"]["properties"]
        for section in sections.values():
            for item in section["properties"].values():
                for name in ("paragraphs", "_claim_units"):
                    field = item["properties"].get(name)
                    if field and field.get("minItems") == 1:
                        self.assertNotIn("maxItems", field)

    def test_renderer_does_not_discard_third_chart(self):
        catalog = {"max_selected_charts": 2, "available_charts": [{"chart_key": f"c{i}"} for i in range(3)]}
        keys = ["c0", "c1", "c2"]
        self.assertEqual(_normalize_requested_chart_keys(keys, chart_catalog=catalog), keys)
        html = build_complete_html({"report_charts": [{"src": f"{key}.png", "title": key} for key in keys]})
        for key in keys:
            self.assertIn(f"{key}.png", html)

    def test_pipeline_accepts_three_charts_but_rejects_unknown_selection(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            paths = SimpleNamespace(output_root=root, peer_output_root=root,
                strategy_dir=root, visualization_dir=root, writer_dir=root,
                published_report=root / "published.html")
            for name in ("strategy_decision_output_v5.json", "chart_catalog.json", "report.html", "published.html", "chart.png"):
                (root / name).write_text("test")
            keys = ["a", "b", "c"]
            data = {
                "run_manifest.json": {"status": "success"},
                "strategy_decision_output_v5.json": {"decision_version": "strategy_decision_output_v5"},
                "chart_catalog.json": {"available_charts": [{"chart_key": key} for key in keys]},
                "chart_manifest.json": {"charts": [{"chart_key": key, "asset_abs_path_png": str(root / "chart.png")} for key in keys]},
                "writer_report_payload.json": {"requested_chart_keys": keys, "chart_selection_details": [{
                    "chart_key": key, "basis_card_keys": ["financial.test"], "selection_reason": "근거",
                    "chart_observation": "관찰", "investment_interpretation": "해석"} for key in keys]},
                "writer_run_status.json": {"status": "success"},
            }
            with patch('orchestration.full_report_pipeline._load_json', side_effect=lambda path: data[path.name]):
                result = validate_full_pipeline_outputs(paths=paths, target_run_key="검증_20251031", peer_run_key="", include_competitor=False)
                self.assertEqual(result["status"], "pass")
                data['chart_catalog.json']['available_charts'].pop()
                with self.assertRaisesRegex(FullPipelineError, "writer_chart_selection"):
                    validate_full_pipeline_outputs(paths=paths, target_run_key="검증_20251031", peer_run_key="", include_competitor=False)


if __name__ == "__main__":
    unittest.main()
