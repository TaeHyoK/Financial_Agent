from __future__ import annotations

import json

from orchestration.end_to_end_loop import (
    REUSED_DOMAIN_SNAPSHOT_STEPS,
    build_parser,
    file_sha256,
    hash_source_trees,
    load_fingerprint_state,
    materialize_reused_domain_snapshot,
)
from orchestration.config import RunConfig
from orchestration.manifest import infer_overall_status, is_pipeline_completed, write_run_files
from orchestration.paths import RunPaths
from orchestration.run_state import FAILED, RUNNING, SUCCESS, StepRecord
from orchestration.validators import collect_token_usage


def test_orchestration_parser_keeps_single_company_defaults() -> None:
    args = build_parser().parse_args(["--dry-run"])

    assert args.dry_run is True
    assert args.skip_step == []
    assert args.reuse_existing is False
    assert args.reuse_domain_data_from is None
    assert args.force_step == []
    assert args.news_split_by_period is False
    assert args.news_total_max_results is None
    assert args.llm_usage_manifest is None
    assert args.llm_run_role == "target"
    assert args.llm_execution_id == ""


def test_news_period_split_is_explicit_opt_in() -> None:
    args = build_parser().parse_args(["--dry-run", "--news-split-by-period"])

    assert args.news_split_by_period is True


def test_news_total_window_cap_is_explicit() -> None:
    args = build_parser().parse_args(
        ["--dry-run", "--news-total-max-results", "40"]
    )

    assert args.news_total_max_results == 40


def test_force_step_can_be_repeated() -> None:
    args = build_parser().parse_args(
        ["--dry-run", "--force-step", "financial_sy", "--force-step", "news_sy"]
    )

    assert args.force_step == ["financial_sy", "news_sy"]


def test_ablation_flags_are_explicit_opt_ins() -> None:
    args = build_parser().parse_args(["--dry-run", "--no-sy", "--primary-data-only"])

    assert args.no_sy is True
    assert args.primary_data_only is True


def test_materialize_reused_domain_snapshot_copies_only_fixed_inputs(tmp_path) -> None:
    config_path = tmp_path / "config.json"
    config_path.write_text("{}", encoding="utf-8")
    config = RunConfig(
        config_path=config_path,
        company_code="sample",
        company_name="Sample",
        ticker="000000.KS",
        corp_code="00000000",
        date_range="20251001-20251030",
        selected_date="20251031",
        raw={},
    )
    source = RunPaths(
        project_root=tmp_path,
        output_root=tmp_path / "source",
        run_key=config.run_key,
        selected_date=config.selected_date,
    )
    destination = RunPaths(
        project_root=tmp_path,
        output_root=tmp_path / "destination",
        run_key=config.run_key,
        selected_date=config.selected_date,
    )
    source.ensure_directories()
    destination.ensure_directories()
    source.run_status.write_text(
        json.dumps({"status": "success", "pipeline_completed": True}),
        encoding="utf-8",
    )
    required = [
        source.yfinance_dir / "market_full_dataset.json",
        source.yfinance_dir / "market_full_dataset.csv",
        source.market_summary_dated,
        source.market_summary,
        source.valuation_snapshot,
        source.dart_main,
        source.dart_master,
        source.dart_lightweight,
        source.output_root
        / "News"
        / "artifacts"
        / "reports"
        / "packs"
        / f"{config.company_name}_{config.information_cutoff_date}"
        / "report_context.json",
        source.news_context_export_day_dir / "llm_summary_request.json",
        source.news_context_export_day_dir / "llm_period_summaries.json",
        source.news_context_export_day_dir / "summary_prompt_input.json",
        source.news_context_export_day_dir / "recent_raw_input.json",
        source.news_context_export_day_dir / "context_export_manifest.json",
    ]
    for index, path in enumerate(required):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({"index": index}), encoding="utf-8")

    snapshot = materialize_reused_domain_snapshot(
        run_config=config,
        source_root=source.output_root,
        destination_paths=destination,
    )

    assert snapshot["status"] == "materialized"
    assert set(snapshot["reused_steps"]) == set(REUSED_DOMAIN_SNAPSHOT_STEPS)
    assert destination.dart_main.read_bytes() == source.dart_main.read_bytes()
    assert destination.news_llm_period_summaries.read_bytes() == source.news_llm_period_summaries.read_bytes()
    assert not destination.news_handoff.exists()


def test_source_hash_changes_when_source_changes(tmp_path) -> None:
    source = tmp_path / "agent.py"
    source.write_text("value = 1\n", encoding="utf-8")
    initial = hash_source_trees([tmp_path])

    source.write_text("value = 2\n", encoding="utf-8")

    assert file_sha256(source)
    assert hash_source_trees([tmp_path]) != initial


def test_source_hash_ignores_readme_but_tracks_runtime_prompt(tmp_path) -> None:
    source = tmp_path / "agent.py"
    readme = tmp_path / "README.md"
    prompt = tmp_path / "prompts" / "decision.md"
    prompt.parent.mkdir()
    source.write_text("value = 1\n", encoding="utf-8")
    readme.write_text("docs one\n", encoding="utf-8")
    prompt.write_text("prompt one\n", encoding="utf-8")
    initial = hash_source_trees([tmp_path])

    readme.write_text("docs two\n", encoding="utf-8")
    assert hash_source_trees([tmp_path]) == initial

    prompt.write_text("prompt two\n", encoding="utf-8")
    assert hash_source_trees([tmp_path]) != initial


def test_invalid_fingerprint_state_is_ignored(tmp_path) -> None:
    path = tmp_path / "step_fingerprints.json"
    path.write_text('{"version":"old","steps":{"x":{}}}', encoding="utf-8")

    assert load_fingerprint_state(path)["steps"] == {}


def test_incomplete_successful_steps_remain_running() -> None:
    steps = [StepRecord(name="first", status=SUCCESS)]

    assert infer_overall_status(steps, expected_step_count=2) == RUNNING
    assert is_pipeline_completed(steps, expected_step_count=2) is False


def test_all_expected_successful_steps_complete_the_pipeline() -> None:
    steps = [
        StepRecord(name="first", status=SUCCESS),
        StepRecord(name="second", status=SUCCESS),
    ]

    assert infer_overall_status(steps, expected_step_count=2) == SUCCESS
    assert is_pipeline_completed(steps, expected_step_count=2) is True


def test_failure_status_is_visible_before_remaining_steps_are_recorded() -> None:
    steps = [StepRecord(name="first", status=FAILED)]

    assert infer_overall_status(steps, expected_step_count=2) == FAILED
    assert is_pipeline_completed(steps, expected_step_count=2) is False


def test_run_status_file_marks_incomplete_successful_steps_as_running(tmp_path) -> None:
    config_path = tmp_path / "config.json"
    config_path.write_text("{}", encoding="utf-8")
    config = RunConfig(
        config_path=config_path,
        company_code="sample",
        company_name="sample",
        ticker="000000.KS",
        corp_code="00000000",
        date_range="20251001-20251030",
        selected_date="20251031",
        raw={},
    )
    paths = RunPaths(
        project_root=tmp_path,
        output_root=tmp_path,
        run_key="sample_20251031",
        selected_date="20251031",
    )
    paths.ensure_directories()

    manifest = write_run_files(
        paths,
        config,
        [StepRecord(name="first", status=SUCCESS)],
        dry_run=False,
        expected_step_count=2,
    )
    persisted_status = json.loads(paths.run_status.read_text(encoding="utf-8"))

    assert manifest["status"] == RUNNING
    assert manifest["pipeline_completed"] is False
    assert persisted_status["status"] == RUNNING
    assert persisted_status["pipeline_completed"] is False




def test_collect_token_usage_prefers_run_jsonl(tmp_path) -> None:
    paths = RunPaths(
        project_root=tmp_path,
        output_root=tmp_path,
        run_key="sample",
        selected_date="20251031",
    )
    paths.run_dir.mkdir(parents=True)
    paths.llm_usage_manifest.write_text(
        '{"step":"writer","status":"ok","usage":{"input_tokens":10,"cached_input_tokens":2,"output_tokens":3,"reasoning_tokens":1,"total_tokens":13}}\n',
        encoding="utf-8",
    )

    usage = collect_token_usage(paths)

    assert usage["call_count"] == 1
    assert usage["recorded_total"]["input_tokens"] == 10
    assert usage["by_step"]["writer"]["cached_input_tokens"] == 2


def test_collect_token_usage_filters_central_manifest(tmp_path) -> None:
    paths = RunPaths(
        project_root=tmp_path,
        output_root=tmp_path,
        run_key="target-run",
        selected_date="20251031",
    )
    central = tmp_path / "central.jsonl"
    central.write_text(
        "\n".join(
            [
                '{"execution_id":"e1","run_id":"target-run","step":"a","status":"ok","usage":{"total_tokens":5}}',
                '{"execution_id":"e1","run_id":"peer-run","step":"b","status":"ok","usage":{"total_tokens":7}}',
                '{"execution_id":"e2","run_id":"target-run","step":"c","status":"ok","usage":{"total_tokens":11}}',
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    usage = collect_token_usage(
        paths,
        manifest_path=central,
        execution_id="e1",
        run_id="target-run",
    )

    assert usage["call_count"] == 1
    assert usage["recorded_total"]["total_tokens"] == 5
