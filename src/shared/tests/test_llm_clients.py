import json

import pytest

from shared.llm_clients import (
    PromptBudgetExceeded,
    compact_json,
    execute_with_telemetry,
    is_transient_transport_error,
    measure_request,
    measure_top_level_fields,
    normalize_usage,
    partition_by_prompt_budget,
    preflight_request,
    record_llm_call,
)


def test_compact_json_removes_formatting_without_escaping_korean() -> None:
    payload = {"회사": "테스트", "items": [1, 2]}

    assert compact_json(payload) == '{"회사":"테스트","items":[1,2]}'


def test_preflight_rejects_request_over_hard_budget(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LLM_PROMPT_TARGET_TOKENS", "5")
    monkeypatch.setenv("LLM_PROMPT_HARD_TOKENS", "10")

    with pytest.raises(PromptBudgetExceeded):
        preflight_request({"model": "test", "input": "가" * 100}, step="test")


def test_measure_top_level_fields_reports_each_field() -> None:
    result = measure_top_level_fields(
        {"metadata": {"run_id": "test"}, "claims": [{"claim_id": "C1"}]},
        model="gpt-5.4-mini",
    )

    assert set(result) == {"metadata", "claims"}
    assert all(item["estimated_tokens"] > 0 for item in result.values())
    assert all(item["serialized_bytes"] > 0 for item in result.values())


def test_usage_normalization_handles_cached_and_reasoning_tokens() -> None:
    usage = {
        "prompt_tokens": 100,
        "completion_tokens": 20,
        "total_tokens": 120,
        "prompt_tokens_details": {"cached_tokens": 40},
        "completion_tokens_details": {"reasoning_tokens": 5},
    }

    assert normalize_usage(usage) == {
        "input_tokens": 100,
        "cached_input_tokens": 40,
        "output_tokens": 20,
        "reasoning_tokens": 5,
        "total_tokens": 120,
    }


def test_record_llm_call_appends_jsonl(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    payload = {"model": "gpt-5.4-mini", "messages": [{"role": "user", "content": "hello"}]}
    measurement = measure_request(payload, model="gpt-5.4-mini")
    output = tmp_path / "usage.jsonl"
    monkeypatch.setenv("LLM_EXECUTION_ID", "execution-1")
    monkeypatch.setenv("LLM_RUN_ID", "sample_20251031")
    monkeypatch.setenv("LLM_RUN_ROLE", "peer")
    monkeypatch.setenv("LLM_COMPANY_NAME", "sample")

    record_llm_call(
        step="unit-test",
        model="gpt-5.4-mini",
        measurement=measurement,
        usage={"prompt_tokens": 4, "completion_tokens": 2, "total_tokens": 6},
        manifest_path=output,
    )

    row = json.loads(output.read_text(encoding="utf-8"))
    assert row["step"] == "unit-test"
    assert row["execution_id"] == "execution-1"
    assert row["run_id"] == "sample_20251031"
    assert row["run_role"] == "peer"
    assert row["company_name"] == "sample"
    assert row["request"]["request_sha256"] == measurement.request_sha256
    assert row["usage"]["total_tokens"] == 6


def test_partition_by_prompt_budget_preserves_item_order(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LLM_PROMPT_TARGET_TOKENS", "100")
    monkeypatch.setenv("LLM_PROMPT_HARD_TOKENS", "1000")
    items = ["가" * 40, "나" * 40, "다" * 40]

    chunks = partition_by_prompt_budget(
        items,
        build_request=lambda chunk: {"model": "gpt-5.4-mini", "items": chunk},
        model="gpt-5.4-mini",
        target_input_tokens=70,
    )

    assert [item for chunk in chunks for item in chunk] == items
    assert len(chunks) >= 2


def test_execute_with_telemetry_records_transport_error(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    output = tmp_path / "usage.jsonl"
    monkeypatch.setenv("LLM_USAGE_MANIFEST", str(output))

    def fail() -> None:
        raise RuntimeError("transport failed")

    with pytest.raises(RuntimeError, match="transport failed"):
        execute_with_telemetry(
            fail,
            request_payload={"model": "gpt-5.4-mini", "input": "test"},
            model="gpt-5.4-mini",
            step="failure-test",
        )

    row = json.loads(output.read_text(encoding="utf-8"))
    assert row["status"] == "error"
    assert row["error"] == "transport failed"


def test_execute_with_telemetry_retries_transient_error_and_records_attempts(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    output = tmp_path / "usage.jsonl"
    monkeypatch.setenv("LLM_USAGE_MANIFEST", str(output))
    calls = {"count": 0}
    delays: list[float] = []

    class RateLimitError(RuntimeError):
        status_code = 429

    def flaky() -> dict:
        calls["count"] += 1
        if calls["count"] == 1:
            raise RateLimitError("retry me")
        return {"usage": {"total_tokens": 3}}

    result = execute_with_telemetry(
        flaky,
        request_payload={"model": "gpt-5.4-mini", "input": "test"},
        model="gpt-5.4-mini",
        step="retry-test",
        usage_getter=lambda response: response["usage"],
        max_attempts=2,
        retry_predicate=is_transient_transport_error,
        backoff_seconds=1.0,
        sleep=delays.append,
        jitter=lambda: 0.0,
    )

    rows = [json.loads(line) for line in output.read_text(encoding="utf-8").splitlines()]
    assert result["usage"]["total_tokens"] == 3
    assert calls["count"] == 2
    assert delays == [1.0]
    assert [(row["attempt"], row["status"]) for row in rows] == [(1, "error"), (2, "ok")]


def test_execute_with_telemetry_does_not_retry_non_transient_error() -> None:
    calls = {"count": 0}

    def fail() -> None:
        calls["count"] += 1
        raise ValueError("bad request")

    with pytest.raises(ValueError, match="bad request"):
        execute_with_telemetry(
            fail,
            request_payload={"model": "gpt-5.4-mini", "input": "test"},
            model="gpt-5.4-mini",
            step="no-retry-test",
            max_attempts=3,
            retry_predicate=is_transient_transport_error,
            sleep=lambda _seconds: None,
        )

    assert calls["count"] == 1


def test_transient_error_classifier_retries_network_oserror_but_not_http_400() -> None:
    class BadRequest(RuntimeError):
        status_code = 400

    assert is_transient_transport_error(OSError("connection reset")) is True
    assert is_transient_transport_error(BadRequest("bad request")) is False
