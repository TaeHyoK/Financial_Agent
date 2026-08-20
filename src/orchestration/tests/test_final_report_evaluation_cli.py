from __future__ import annotations

import json

from orchestration.final_report_evaluation_cli import (
    PairSpec,
    _load_candidate_snapshot,
    build_parser,
    evaluate_pair,
    run_evaluation,
)
from orchestration.final_report_evaluation_metrics import AXES


def _write_json(path, payload) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")


def test_load_candidate_snapshot_returns_exact_archived_pair(tmp_path) -> None:
    pair_id = "suite__case__no_peer__r01"
    pair_dir = tmp_path / "prior" / "comparisons" / pair_id
    full = {"title": "Full", "sections": [{"text": "원본 스냅샷"}]}
    ablation = {"title": "Ablation", "sections": [{"text": "비교 스냅샷"}]}
    _write_json(pair_dir / "candidate_full_visible.json", full)
    _write_json(pair_dir / "candidate_ablation_visible.json", ablation)

    snapshot = _load_candidate_snapshot(pair_id, [tmp_path / "prior"])

    assert snapshot is not None
    assert snapshot["full"] == full
    assert snapshot["ablation"] == ablation
    assert snapshot["provenance"]["mode"] == "frozen_judge_visible_snapshot"


def test_dry_run_discovers_three_default_ablation_pairs(tmp_path, monkeypatch) -> None:
    suite_root = tmp_path / "suite"
    run_key = "회사A_20250101"
    report_html = """
    <html><body><main class="a4-sheet">
      <p class="report-name">회사A 투자 리서치</p>
      <section id="thesis"><h1>투자의견</h1><p>Hold 의견입니다.</p></section>
    </main></body></html>
    """
    packet = suite_root / "conditions" / "full" / "Strategy" / run_key / "strategy_compact_packet_v2.json"
    _write_json(
        packet,
        {
            "target_company": {
                "company_name": "회사A",
                "run_key": run_key,
                "as_of_date": "2025-01-01",
            },
            "selected_date_policy": "before_selected_date",
            "coverage_summary": {},
            "reader_limitations": [],
            "limitation_requirements": [],
            "cards": {
                "financial.revenue": {
                    "domain": "financial",
                    "label": "매출",
                    "primary_observation": {"value": 100},
                }
            },
        },
    )
    runs = []
    for condition in ("full", "no_sy", "no_competitor", "primary_only"):
        condition_dir = suite_root / "conditions" / condition
        report = condition_dir / "Writer" / run_key / "report.html"
        report.parent.mkdir(parents=True, exist_ok=True)
        report.write_text(report_html, encoding="utf-8")
        manifest = condition_dir / "manifest.json"
        outputs = {"strategy_compact_packet_v2": str(packet)}
        _write_json(manifest, {"outputs": outputs})
        runs.append(
            {
                "condition": condition,
                "replicate": 1,
                "status": "success",
                "recommendation": "Hold",
                "gate_a": "pass",
                "gate_b": "pass",
                "writer_gate": "pass",
                "report_html": str(report),
                "pipeline_manifest": str(manifest),
            }
        )
    _write_json(
        suite_root / "ablation_summary.json",
        {"suite_id": "suite", "status": "success", "runs": runs},
    )
    output_root = tmp_path / "evaluation"
    args = build_parser().parse_args(
        [
            "--suite-root",
            str(suite_root),
            "--output-root",
            str(output_root),
            "--evaluation-id",
            "dry",
            "--dry-run",
        ]
    )
    monkeypatch.setattr(
        "orchestration.final_report_evaluation_cli.code_identity",
        lambda: {"git_commit": "test"},
    )

    summary = run_evaluation(args)

    assert summary["status"] == "dry_run"
    assert summary["counts"] == {
        "planned_pairs": 3,
        "completed_pairs": 3,
        "successful_pairs": 0,
        "failed_pairs": 0,
        "dry_run_pairs": 3,
    }
    assert {item["ablation_condition"] for item in summary["pairs"]} == {
        "no_sy",
        "no_competitor",
        "primary_only",
    }
    assert len(list((output_root / "dry" / "comparisons").glob("*/judgments/order_ab_request.json"))) == 3


def test_pair_evaluation_crosses_order_and_reconciles_full_win(tmp_path) -> None:
    def report(path, text):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            f"<html><body><main class='a4-sheet'><section><h1>분석</h1><p>{text}</p>"
            "</section></main></body></html>",
            encoding="utf-8",
        )

    full_report = tmp_path / "full.html"
    ablation_report = tmp_path / "ablation.html"
    report(full_report, "정확한 공통 근거 보고서")
    report(ablation_report, "근거가 부족한 보고서")
    packet = tmp_path / "packet.json"
    _write_json(
        packet,
        {
            "target_company": {"company_name": "회사A", "as_of_date": "2025-01-01"},
            "cards": {
                "financial.revenue": {
                    "domain": "financial",
                    "label": "매출",
                    "primary_observation": {"value": 100},
                }
            },
        },
    )
    prompt = tmp_path / "prompt.md"
    prompt.write_text("블라인드 평가", encoding="utf-8")

    def fake_judge(request_payload, **_kwargs):
        user_payload = json.loads(request_payload["messages"][1]["content"])
        assert "candidate_accessible_evidence" not in user_payload
        assert "candidate_evidence_access" not in user_payload["evaluation_contract"]
        candidate_a = json.dumps(user_payload["candidate_A"], ensure_ascii=False)
        winner = "A" if "정확한 공통 근거" in candidate_a else "B"
        return {
            "axes": {
                axis: {
                    "winner": winner,
                    "reason": "공통 근거와 더 일치함",
                    "supporting_card_keys": ["financial.revenue"],
                    "candidate_a_error_tags": [],
                    "candidate_b_error_tags": [],
                }
                for axis in AXES
            }
        }

    result = evaluate_pair(
        PairSpec(
            suite_id="suite",
            case_id="case",
            company_name="회사A",
            replicate=1,
            ablation_condition="no_sy",
            baseline_report=full_report,
            ablation_report=ablation_report,
            common_packet=packet,
            ablation_packet=packet,
            baseline_recommendation="Hold",
            ablation_recommendation="Hold",
        ),
        output_dir=tmp_path / "output",
        model="judge-model",
        prompt_path=prompt,
        timeout_seconds=10,
        transport_retries=0,
        dry_run=False,
        force=False,
        judge_call=fake_judge,
        evidence_mode="union_blind",
    )

    assert result["status"] == "success"
    assert result["evidence_mode"] == "union_blind"
    assert result["evidence_scope"]["candidate_access_metadata_sent"] is False
    assert result["order_consistency_rate"] == 1.0
    assert all(item["outcome"] == "full_win" for item in result["axes"].values())
