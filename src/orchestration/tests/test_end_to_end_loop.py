from __future__ import annotations

from orchestration.end_to_end_loop import build_parser, file_sha256, hash_source_trees, load_fingerprint_state
from orchestration.paths import RunPaths
from orchestration.validators import collect_token_usage


def test_orchestration_parser_keeps_single_company_defaults() -> None:
    args = build_parser().parse_args(["--dry-run"])

    assert args.dry_run is True
    assert args.skip_step == []
    assert args.reuse_existing is False
    assert args.force_step == []
    assert args.news_split_by_period is False
    assert args.llm_usage_manifest is None
    assert args.llm_run_role == "target"
    assert args.llm_execution_id == ""


def test_news_period_split_is_explicit_opt_in() -> None:
    args = build_parser().parse_args(["--dry-run", "--news-split-by-period"])

    assert args.news_split_by_period is True


def test_force_step_can_be_repeated() -> None:
    args = build_parser().parse_args(
        ["--dry-run", "--force-step", "financial_sy", "--force-step", "news_sy"]
    )

    assert args.force_step == ["financial_sy", "news_sy"]


def test_ablation_flags_are_explicit_opt_ins() -> None:
    args = build_parser().parse_args(["--dry-run", "--no-sy", "--primary-data-only"])

    assert args.no_sy is True
    assert args.primary_data_only is True


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
