from __future__ import annotations

import json

from orchestration.usage_summary import summarize_execution_usage


def _row(*, role: str, step: str, sha: str, status: str = "ok", attempt: int = 1) -> dict:
    return {
        "execution_id": "exec-1",
        "run_id": f"{role}-run",
        "run_role": role,
        "step": step,
        "status": status,
        "attempt": attempt,
        "request": {
            "request_sha256": sha,
            "estimated_input_tokens": 100,
            "over_target": False,
        },
        "usage": {
            "input_tokens": 80 if status == "ok" else 0,
            "cached_input_tokens": 20 if status == "ok" else 0,
            "output_tokens": 10 if status == "ok" else 0,
            "reasoning_tokens": 2 if status == "ok" else 0,
            "total_tokens": 90 if status == "ok" else 0,
        },
    }


def test_summary_separates_retry_attempts_from_logical_calls(tmp_path) -> None:
    path = tmp_path / "usage.jsonl"
    rows = [
        _row(role="target", step="news", sha="same", status="error"),
        _row(role="target", step="news", sha="same", attempt=2),
        _row(role="final", step="writer", sha="writer"),
        {**_row(role="evaluation", step="bias", sha="eval"), "run_role": "evaluation"},
    ]
    path.write_text("\n".join(json.dumps(row) for row in rows) + "\n", encoding="utf-8")

    summary = summarize_execution_usage(path, execution_id="exec-1", pipeline_completed=True)

    assert summary["observed_logical_calls"] == 2
    assert summary["transport_attempts"] == 3
    assert summary["error_transport_attempts"] == 1
    assert summary["cache_suppressed_calls"] == 12
    assert summary["usage"]["total_tokens"] == 180
    assert summary["uncached_input_tokens"] == 120
    assert summary["request_budget"]["max_estimated_input_tokens"] == 100


def test_empty_completed_execution_is_reported_as_fully_cache_suppressed(tmp_path) -> None:
    summary = summarize_execution_usage(
        tmp_path / "missing.jsonl",
        execution_id="exec-2",
        pipeline_completed=True,
    )

    assert summary["expected_cold_cache_logical_calls"] == 14
    assert summary["observed_logical_calls"] == 0
    assert summary["cache_suppressed_calls"] == 14
    assert summary["usage"]["total_tokens"] == 0
