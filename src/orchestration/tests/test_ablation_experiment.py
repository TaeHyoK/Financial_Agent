from __future__ import annotations

import json

from orchestration.ablation_experiment import (
    CONDITIONS,
    _interrupted_run_context,
    build_condition_command,
    build_parser,
    build_summary,
    run_frozen_final_stage,
    select_conditions,
)


def _args(tmp_path):
    return build_parser().parse_args(
        [
            "--company-name",
            "SK바이오팜",
            "--selected-date",
            "20251031",
            "--peer-stock-code",
            "003120",
            "--news-total-max-results",
            "40",
            "--target-news-query",
            '("S-OIL" OR "에쓰오일")',
            "--output-root",
            str(tmp_path),
            "--no-progress",
        ]
    )


def test_default_matrix_contains_full_and_all_single_ablations() -> None:
    selected = select_conditions([])

    assert selected == CONDITIONS
    assert [item.name for item in selected] == [
        "full",
        "no_sy",
        "no_financial",
        "no_news",
        "no_yfinance",
        "primary_only",
        "no_sy_primary_only",
        "no_competitor",
        "only_financial",
        "only_news",
        "only_yfinance",
        "full_context",
        "free_form_writer",
    ]


def test_condition_command_isolates_output_and_propagates_flags(tmp_path) -> None:
    args = _args(tmp_path)
    condition = next(item for item in CONDITIONS if item.name == "full_context")

    command = build_condition_command(
        args=args,
        condition=condition,
        condition_root=tmp_path / "condition",
        execution_id="suite__full_context__r01",
    )

    assert command[1:3] == ["-m", "orchestration.full_report_pipeline"]
    assert command[command.index("--output-root") + 1] == str(tmp_path / "condition")
    assert command[command.index("--peer-stock-code") + 1] == "003120"
    assert command[command.index("--news-event-top-k") + 1] == "40"
    assert command[command.index("--target-news-query") + 1] == '("S-OIL" OR "에쓰오일")'
    assert "--full-context" in command


def test_condition_command_can_reuse_collected_domain_snapshot(tmp_path) -> None:
    args = _args(tmp_path)
    args.peer_stock_code = ""
    condition = next(item for item in CONDITIONS if item.name == "primary_only")
    snapshot_root = tmp_path / "snapshot"

    command = build_condition_command(
        args=args,
        condition=condition,
        condition_root=tmp_path / "condition",
        execution_id="suite__primary_only__r01",
        reuse_domain_data_from=snapshot_root,
        peer_resolution_from=tmp_path / "peer_resolution.json",
    )

    assert command[command.index("--reuse-domain-data-from") + 1] == str(snapshot_root.resolve())
    assert command[command.index("--peer-resolution-from") + 1] == str(
        (tmp_path / "peer_resolution.json").resolve()
    )
    assert "--primary-data-only" in command


def test_combined_no_sy_primary_only_condition_has_both_flags(tmp_path) -> None:
    args = _args(tmp_path)
    condition = next(item for item in CONDITIONS if item.name == "no_sy_primary_only")

    command = build_condition_command(
        args=args,
        condition=condition,
        condition_root=tmp_path / "condition",
        execution_id="suite__no_sy_primary_only__r01",
        reuse_domain_data_from=tmp_path / "snapshot",
    )

    assert "--no-sy" in command
    assert "--primary-data-only" in command
    assert "--reuse-domain-data-from" in command


def test_no_competitor_command_does_not_pass_peer_override(tmp_path) -> None:
    args = _args(tmp_path)
    condition = next(item for item in CONDITIONS if item.name == "no_competitor")

    command = build_condition_command(
        args=args,
        condition=condition,
        condition_root=tmp_path / "condition",
        execution_id="suite__no_competitor__r01",
    )

    assert "--no-competitor" in command
    assert "--peer-stock-code" not in command


def test_force_condition_is_repeatable(tmp_path) -> None:
    args = build_parser().parse_args(
        [
            "--company-name",
            "SK바이오팜",
            "--selected-date",
            "20251031",
            "--output-root",
            str(tmp_path),
            "--force-condition",
            "free_form_writer",
            "--force-condition",
            "full_context",
        ]
    )

    assert args.force_condition == ["free_form_writer", "full_context"]


def test_resume_running_record_reuses_attempt_root_and_execution_id(tmp_path) -> None:
    condition_root = tmp_path / "conditions" / "no_sy" / "replicate_01" / "attempt_03"
    existing = {
        "status": "running",
        "attempt": 3,
        "condition_root": str(condition_root),
        "execution_id": "suite__no_sy__r01__attempt03",
    }

    context = _interrupted_run_context(
        existing,
        resume=True,
        force_condition=False,
    )

    assert context == (
        3,
        condition_root.resolve(),
        "suite__no_sy__r01__attempt03",
    )


def test_resume_failed_record_starts_a_new_attempt_instead(tmp_path) -> None:
    context = _interrupted_run_context(
        {
            "status": "failed",
            "attempt": 3,
            "condition_root": str(tmp_path / "attempt_03"),
            "execution_id": "suite__no_sy__r01__attempt03",
        },
        resume=True,
        force_condition=False,
    )

    assert context is None


def test_free_form_writer_reuses_full_strategy_and_runs_writer_only(
    tmp_path,
    monkeypatch,
) -> None:
    run_key = "SK바이오팜_20251031"
    source_root = tmp_path / "source"
    strategy_dir = source_root / "Strategy" / run_key
    strategy_dir.mkdir(parents=True)
    strategy_payloads = {
        "strategy_report.json": {},
        "strategy_compact_packet_v2.json": {"cards": {}},
        "strategy_packet_provenance_v2.json": {},
        "strategy_decision_output_v2.json": {
            "decision": {
                "opinion": "Hold",
                "horizon": "6-12m",
                "evidence_sufficiency": "high",
            }
        },
        "strategy_semantic_validation_v2.json": {
            "status": "pass",
            "gate_a": {"status": "pass"},
            "gate_b": {"status": "pass"},
        },
        "strategy_packet_telemetry_v2.json": {"strategy_context_mode": "compact_cards"},
        "strategy_decision_profile_v2.json": {},
        "strategy_generation_context_v2.json": {},
    }
    for filename, payload in strategy_payloads.items():
        (strategy_dir / filename).write_text(json.dumps(payload), encoding="utf-8")
    for domain_dir in ("Financial", "News", "Y_Finance"):
        path = source_root / domain_dir / run_key / "final_report.json"
        path.parent.mkdir(parents=True)
        path.write_text("{}", encoding="utf-8")
    peer_path = source_root / "Competitor" / run_key / "peer_comparison_dataset.json"
    peer_path.parent.mkdir(parents=True)
    peer_path.write_text("{}", encoding="utf-8")
    source_manifest = tmp_path / "full_pipeline_manifest.json"
    source_manifest.write_text(
        json.dumps(
            {
                "run_key": run_key,
                "target": {"company_name": "SK바이오팜"},
                "outputs": {
                    "strategy_report": str(strategy_dir / "strategy_report.json"),
                    "strategy_decision_output_v2": str(
                        strategy_dir / "strategy_decision_output_v2.json"
                    ),
                    "peer_comparison": str(peer_path),
                },
            }
        ),
        encoding="utf-8",
    )
    commands: list[list[str]] = []

    def fake_run(command, **_kwargs):
        commands.append(command)
        output_dir = command[command.index("--output-dir") + 1]
        from pathlib import Path

        writer_dir = Path(output_dir)
        writer_dir.mkdir(parents=True, exist_ok=True)
        (writer_dir / "writer_validation_report.json").write_text(
            json.dumps({"status": "pass"}), encoding="utf-8"
        )
        (writer_dir / "report.html").write_text("<html></html>", encoding="utf-8")
        return 0

    monkeypatch.setattr("orchestration.ablation_experiment._run_logged_command", fake_run)
    condition = next(item for item in CONDITIONS if item.name == "free_form_writer")
    result = run_frozen_final_stage(
        args=_args(tmp_path),
        condition=condition,
        condition_root=tmp_path / "condition",
        execution_id="suite__free_form_writer__r01__attempt02",
        source_manifest=source_manifest,
        log_dir=tmp_path / "logs",
    )

    assert result["returncode"] == 0
    assert len(commands) == 1
    assert "--free-form" in commands[0]
    copied_decision = (
        tmp_path
        / "condition"
        / "Strategy"
        / run_key
        / "strategy_decision_output_v2.json"
    )
    assert copied_decision.read_bytes() == (
        strategy_dir / "strategy_decision_output_v2.json"
    ).read_bytes()
    final_manifest = json.loads(open(result["manifest_path"], encoding="utf-8").read())
    assert final_manifest["commands"]["strategy"] == []
    assert final_manifest["llm_usage"]["by_role"]["final"]["expected_logical_calls"] == 1


def test_summary_keeps_failed_conditions_in_denominator() -> None:
    summary = build_summary(
        {
            "suite_id": "suite",
            "request": {},
            "runs": [
                {"condition": "full", "replicate": 1, "status": "success", "result": {}},
                {"condition": "no_sy", "replicate": 1, "status": "failed", "result": {}},
            ],
        }
    )

    assert summary["status"] == "complete_with_failures"
    assert summary["counts"] == {
        "planned": 2,
        "terminal": 2,
        "success": 1,
        "failed": 1,
        "dry_run": 0,
    }
