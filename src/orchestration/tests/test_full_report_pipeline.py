from __future__ import annotations

import json
import os
import sys

from orchestration.company_resolver import CompanyIdentity
from orchestration.company_resolver import CompanyResolutionError
from orchestration.full_report_pipeline import (
    _execute_stage,
    build_parser,
    run_full_pipeline,
)


def _identity(name: str, corp_code: str, stock_code: str, market: str) -> CompanyIdentity:
    suffix = ".KS" if market == "KOSPI" else ".KQ"
    return CompanyIdentity(
        company_name=name,
        corp_code=corp_code,
        stock_code=stock_code,
        market=market,
        ticker=f"{stock_code}{suffix}",
        source={"provider": "test"},
    )


def test_full_pipeline_resolves_configs_and_builds_all_stages(tmp_path, monkeypatch) -> None:
    target = _identity("SK바이오팜", "01364795", "326030", "KOSPI")
    peer = _identity("일성아이에스", "00146289", "003120", "KOSPI")
    args = build_parser().parse_args(
        [
            "--company-name",
            "SK바이오팜",
            "--selected-date",
            "20251031",
            "--news-window",
            "1m",
            "--decision-horizon-profile",
            "short_term",
            "--semantic-attempts",
            "3",
            "--final-stage-timeout",
            "123",
            "--output-root",
            str(tmp_path),
            "--env-file",
            str(tmp_path / ".env"),
            "--execution-id",
            "exec-test",
            "--no-progress",
        ]
    )
    monkeypatch.setenv("DART_API_KEY", "test-key")
    monkeypatch.setattr(
        "orchestration.full_report_pipeline.fetch_dart_company_directory",
        lambda _key: [{"stock_code": target.stock_code}, {"stock_code": peer.stock_code}],
    )
    monkeypatch.setattr(
        "orchestration.full_report_pipeline.resolve_company_identity",
        lambda *_args, **_kwargs: target,
    )
    monkeypatch.setattr(
        "orchestration.full_report_pipeline.resolve_company_identity_by_stock_code",
        lambda *_args, **_kwargs: peer,
    )
    monkeypatch.setattr(
        "orchestration.full_report_pipeline._resolve_peer_selection",
        lambda *_args, **_kwargs: {
            "status": "selected",
            "selected_peer": {
                "stock_code": peer.stock_code,
                "market_cap_100m_krw": 1234,
            },
            "selection_basis": {"method": "test_selection"},
            "source": {"provider": "test_naver"},
        },
    )
    executed: list[tuple[str, list[str]]] = []
    stage_timeouts: dict[str, int | float | None] = {}
    stage_envs: dict[str, dict[str, str]] = {}
    pre_execution_statuses: list[str] = []

    def fake_execute_stage(*, name, command, env, dry_run, log_path, timeout_seconds, record):
        del dry_run, log_path
        running_manifest = json.loads(
            (
                tmp_path
                / "runs"
                / "SK바이오팜_20251031"
                / "executions"
                / "exec-test"
                / "full_pipeline_manifest.json"
            ).read_text(encoding="utf-8")
        )
        pre_execution_statuses.append(running_manifest["steps"][-1]["status"])
        executed.append((name, command))
        stage_timeouts[name] = timeout_seconds
        stage_envs[name] = env
        record.update({"status": "success", "returncode": 0})
        return record

    monkeypatch.setattr("orchestration.full_report_pipeline._execute_stage", fake_execute_stage)
    monkeypatch.setattr(
        "orchestration.full_report_pipeline.validate_full_pipeline_outputs",
        lambda **_kwargs: {"status": "pass", "final_recommendation": "Hold"},
    )

    manifest = run_full_pipeline(args)

    assert manifest["status"] == "success"
    assert [name for name, _command in executed] == [
        "target_domain_pipeline",
        "peer_domain_pipeline",
        "peer_comparison_dataset",
        "strategy",
        "writer",
    ]
    target_config = json.loads(
        (tmp_path / "runs" / "SK바이오팜_20251031" / "resolved_inputs" / "target_company.json").read_text(
            encoding="utf-8"
        )
    )
    assert target_config["date_range"] == "20251001-20251030"
    assert target_config["selected_date_policy"] == "before_market_open"
    assert target_config["peer_selection"]["company_name"] == "일성아이에스"
    assert "market_cap_100m_krw" not in json.dumps(target_config)
    target_command = executed[0][1]
    assert target_command[target_command.index("--llm-run-role") + 1] == "target"
    assert target_command[target_command.index("--llm-execution-id") + 1] == "exec-test"
    strategy_command = executed[3][1]
    writer_command = executed[4][1]
    assert strategy_command[strategy_command.index("--packet-version") + 1] == "v2"
    assert strategy_command[strategy_command.index("--decision-horizon-profile") + 1] == "short_term"
    assert strategy_command[strategy_command.index("--semantic-attempts") + 1] == "3"
    assert "--strategy-packet" in writer_command
    assert "--strategy-decision" in writer_command
    assert writer_command[writer_command.index("--semantic-attempts") + 1] == "3"
    assert "--decision-basis-by-section" not in writer_command
    assert stage_timeouts == {
        "target_domain_pipeline": None,
        "peer_domain_pipeline": None,
        "peer_comparison_dataset": None,
        "strategy": 123,
        "writer": 123,
    }
    assert pre_execution_statuses == ["running"] * 5
    assert all(env["LLM_TIMEOUT_SECONDS"] == "300" for env in stage_envs.values())
    assert all(env["LLM_TRANSPORT_RETRIES"] == "1" for env in stage_envs.values())
    assert manifest["request"]["news_window"] == "1m"
    assert manifest["request"]["decision_horizon_profile"] == "short_term"
    assert manifest["request"]["decision_horizon"] == "1개월"
    assert manifest["request"]["semantic_attempts"] == 3
    assert manifest["request"]["final_stage_timeout_seconds"] == 123
    assert manifest["llm_usage"]["expected_cold_cache_logical_calls"] == 14


def test_news_window_does_not_select_strategy_horizon() -> None:
    args = build_parser().parse_args(
        [
            "--company-name",
            "Target",
            "--selected-date",
            "20250102",
            "--news-window",
            "3m",
        ]
    )

    assert args.news_window == "3m"
    assert args.decision_horizon_profile == "default"


def test_semantic_attempts_must_be_positive() -> None:
    parser = build_parser()

    try:
        parser.parse_args(
            [
                "--company-name",
                "Target",
                "--selected-date",
                "20250102",
                "--semantic-attempts",
                "0",
            ]
        )
    except SystemExit as exc:
        assert exc.code == 2
    else:
        raise AssertionError("zero semantic attempts must be rejected")


def test_dry_run_does_not_execute_subprocesses(tmp_path, monkeypatch) -> None:
    target = _identity("Target", "00000001", "111111", "KOSPI")
    peer = _identity("Peer", "00000002", "222222", "KOSDAQ")
    args = build_parser().parse_args(
        [
            "--company-name",
            "Target",
            "--selected-date",
            "20250102",
            "--output-root",
            str(tmp_path),
            "--execution-id",
            "dry-test",
            "--peer-stock-code",
            peer.stock_code,
            "--dry-run",
        ]
    )
    monkeypatch.setenv("DART_API_KEY", "test-key")
    monkeypatch.setattr("orchestration.full_report_pipeline.fetch_dart_company_directory", lambda _key: [])
    monkeypatch.setattr("orchestration.full_report_pipeline.resolve_company_identity", lambda *_a, **_k: target)
    monkeypatch.setattr(
        "orchestration.full_report_pipeline.resolve_company_identity_by_stock_code",
        lambda *_a, **_k: peer,
    )

    manifest = run_full_pipeline(args)

    assert manifest["status"] == "dry_run"
    assert all(step["status"] == "planned" for step in manifest["steps"])
    assert manifest["llm_usage"]["cache_suppressed_calls"] is None


def test_peer_resolution_failure_persists_target_and_diagnostic_artifact(
    tmp_path,
    monkeypatch,
) -> None:
    target = _identity("Canonical Target", "00000001", "111111", "KOSPI")
    args = build_parser().parse_args(
        [
            "--company-name",
            "Alias Target",
            "--selected-date",
            "20250102",
            "--output-root",
            str(tmp_path),
            "--execution-id",
            "peer-failure-test",
        ]
    )
    monkeypatch.setenv("DART_API_KEY", "test-key")
    monkeypatch.setattr("orchestration.full_report_pipeline.fetch_dart_company_directory", lambda _key: [])
    monkeypatch.setattr("orchestration.full_report_pipeline.resolve_company_identity", lambda *_a, **_k: target)
    monkeypatch.setattr(
        "orchestration.full_report_pipeline._resolve_peer_selection",
        lambda *_a, **_k: {
            "status": "peer_unavailable",
            "reason": "comparable_peer_market_caps_missing",
            "target": {"stock_code": target.stock_code},
            "candidates": [],
            "selected_peer": {},
        },
    )

    try:
        run_full_pipeline(args)
    except CompanyResolutionError as exc:
        assert "comparable_peer_market_caps_missing" in str(exc)
    else:
        raise AssertionError("peer resolution failure must stop the full pipeline")

    run_dir = tmp_path / "runs" / "Canonical_Target_20250102"
    diagnostic = json.loads(
        (tmp_path / "Competitor" / "Canonical_Target_20250102" / "peer_resolution.json").read_text(
            encoding="utf-8"
        )
    )
    manifest = json.loads(
        (
            run_dir
            / "executions"
            / "peer-failure-test"
            / "full_pipeline_manifest.json"
        ).read_text(encoding="utf-8")
    )

    assert diagnostic["reason"] == "comparable_peer_market_caps_missing"
    assert manifest["status"] == "failed"
    assert manifest["target"]["stock_code"] == target.stock_code
    assert manifest["peer_resolution_failure"]["reason"] == diagnostic["reason"]


def test_no_competitor_ablation_skips_peer_stages_and_records_scope(tmp_path, monkeypatch) -> None:
    target = _identity("Target", "00000001", "111111", "KOSPI")
    args = build_parser().parse_args(
        [
            "--company-name",
            "Target",
            "--selected-date",
            "20250102",
            "--output-root",
            str(tmp_path),
            "--execution-id",
            "no-peer-test",
            "--no-competitor",
            "--no-sy",
            "--only-domain",
            "dart",
        ]
    )
    monkeypatch.setenv("DART_API_KEY", "test-key")
    monkeypatch.setattr("orchestration.full_report_pipeline.fetch_dart_company_directory", lambda _key: [])
    monkeypatch.setattr("orchestration.full_report_pipeline.resolve_company_identity", lambda *_a, **_k: target)
    executed: list[tuple[str, list[str]]] = []

    def fake_execute_stage(*, name, command, env, dry_run, log_path, timeout_seconds, record):
        del env, dry_run, log_path, timeout_seconds
        executed.append((name, command))
        record.update({"status": "success", "returncode": 0})
        return record

    monkeypatch.setattr("orchestration.full_report_pipeline._execute_stage", fake_execute_stage)
    monkeypatch.setattr(
        "orchestration.full_report_pipeline.validate_full_pipeline_outputs",
        lambda **_kwargs: {"status": "pass", "final_recommendation": "Hold"},
    )

    manifest = run_full_pipeline(args)

    assert [name for name, _command in executed] == [
        "target_domain_pipeline",
        "strategy",
        "writer",
    ]
    target_command = executed[0][1]
    strategy_command = executed[1][1]
    assert "--no-sy" in target_command
    assert "--no-sy" in strategy_command
    assert strategy_command[strategy_command.index("--include-domain") + 1] == "financial"
    assert "--peer-comparison" not in strategy_command
    assert manifest["ablation"]["included_domains"] == ["financial"]
    assert manifest["ablation"]["include_competitor"] is False
    assert manifest["llm_usage"]["expected_logical_calls_by_role"] == {
        "target": 3,
        "peer": 0,
        "final": 2,
    }


def test_execute_stage_captures_logs_and_classifies_nonzero_exit(tmp_path) -> None:
    log_path = tmp_path / "stage_logs" / "failure.log"

    record = _execute_stage(
        name="failure",
        command=[
            sys.executable,
            "-c",
            "import sys; print('captured stdout'); print('captured stderr', file=sys.stderr); raise SystemExit(7)",
        ],
        env=os.environ.copy(),
        dry_run=False,
        log_path=log_path,
    )

    assert record["status"] == "failed"
    assert record["returncode"] == 7
    assert record["failure_class"] == "nonzero_exit"
    assert record["log_path"] == str(log_path)
    assert "captured stdout" in record["stdout_tail"]
    assert "captured stderr" in record["stderr_tail"]
    assert "captured stdout" in record["log_tail"]
    assert "captured stderr" in log_path.read_text(encoding="utf-8")


def test_execute_stage_timeout_is_return_code_124(tmp_path) -> None:
    log_path = tmp_path / "stage_logs" / "timeout.log"

    record = _execute_stage(
        name="strategy",
        command=[sys.executable, "-c", "import time; print('started', flush=True); time.sleep(2)"],
        env=os.environ.copy(),
        dry_run=False,
        log_path=log_path,
        timeout_seconds=0.05,
    )

    assert record["status"] == "failed"
    assert record["returncode"] == 124
    assert record["failure_class"] == "timeout"
    assert record["timeout_seconds"] == 0.05
    assert "Stage exceeded timeout" in record["stderr_tail"]
    assert "started" in log_path.read_text(encoding="utf-8")
