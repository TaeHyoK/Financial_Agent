"""Shared LLM request sizing and usage telemetry.

This module does not own an OpenAI transport. Existing agents can keep their
SDK or urllib clients while using one prompt-budget and usage contract.
"""

from __future__ import annotations

import hashlib
import json
import math
import os
import random
import time
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Iterable, TypeVar


DEFAULT_MINI_TARGET_INPUT_TOKENS = 100_000
DEFAULT_FULL_TARGET_INPUT_TOKENS = 120_000
DEFAULT_HARD_INPUT_TOKENS = 200_000


class PromptBudgetExceeded(RuntimeError):
    """Raised before an API call whose serialized request exceeds the hard budget."""


@dataclass(frozen=True)
class PromptMeasurement:
    model: str
    estimated_input_tokens: int
    target_input_tokens: int
    hard_input_tokens: int
    over_target: bool
    request_sha256: str
    serialized_bytes: int

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


def compact_json(value: Any, *, sort_keys: bool = False) -> str:
    """Serialize JSON without API-irrelevant indentation or ASCII escaping."""

    return json.dumps(
        value,
        ensure_ascii=False,
        allow_nan=False,
        separators=(",", ":"),
        sort_keys=sort_keys,
    )


def estimate_text_tokens(text: str, *, model: str = "") -> int:
    """Estimate tokens using tiktoken, with a conservative local fallback."""

    try:
        import tiktoken

        try:
            encoding = tiktoken.encoding_for_model(model)
        except KeyError:
            encoding = tiktoken.get_encoding("o200k_base")
        return len(encoding.encode(text))
    except (ImportError, ModuleNotFoundError):
        ascii_chars = sum(1 for char in text if ord(char) < 128)
        non_ascii_chars = len(text) - ascii_chars
        return max(1, math.ceil(ascii_chars / 4) + non_ascii_chars)


def prompt_budget_for_model(model: str) -> tuple[int, int]:
    """Return the internal target and hard input budgets for a model."""

    normalized = (model or "").lower()
    default_target = (
        DEFAULT_MINI_TARGET_INPUT_TOKENS
        if "mini" in normalized or not normalized
        else DEFAULT_FULL_TARGET_INPUT_TOKENS
    )
    target = int(os.getenv("LLM_PROMPT_TARGET_TOKENS", str(default_target)))
    hard = int(os.getenv("LLM_PROMPT_HARD_TOKENS", str(DEFAULT_HARD_INPUT_TOKENS)))
    if target <= 0 or hard <= 0 or target > hard:
        raise ValueError("LLM prompt budgets must satisfy 0 < target <= hard.")
    return target, hard


def measure_request(request_payload: Any, *, model: str = "") -> PromptMeasurement:
    """Measure the exact compact JSON representation used for request tracking."""

    serialized = compact_json(request_payload)
    resolved_model = model or (
        str(request_payload.get("model") or "") if isinstance(request_payload, dict) else ""
    )
    target, hard = prompt_budget_for_model(resolved_model)
    estimated = estimate_text_tokens(serialized, model=resolved_model)
    return PromptMeasurement(
        model=resolved_model,
        estimated_input_tokens=estimated,
        target_input_tokens=target,
        hard_input_tokens=hard,
        over_target=estimated > target,
        request_sha256=hashlib.sha256(serialized.encode("utf-8")).hexdigest(),
        serialized_bytes=len(serialized.encode("utf-8")),
    )


def measure_top_level_fields(payload: dict[str, Any], *, model: str = "") -> dict[str, dict[str, int]]:
    """Measure each top-level JSON field for request composition diagnostics."""

    if not isinstance(payload, dict):
        raise TypeError("payload must be an object")
    return {
        str(key): {
            "estimated_tokens": estimate_text_tokens(compact_json({key: value}), model=model),
            "serialized_bytes": len(compact_json({key: value}).encode("utf-8")),
        }
        for key, value in payload.items()
    }


def preflight_request(
    request_payload: Any,
    *,
    model: str = "",
    step: str = "",
) -> PromptMeasurement:
    """Measure a request and reject it before transport when it exceeds the hard budget."""

    measurement = measure_request(request_payload, model=model)
    if measurement.estimated_input_tokens > measurement.hard_input_tokens:
        label = f" for {step}" if step else ""
        raise PromptBudgetExceeded(
            f"LLM input{label} is approximately {measurement.estimated_input_tokens:,} tokens; "
            f"hard budget is {measurement.hard_input_tokens:,}. Partition by evidence references."
        )
    return measurement


def normalize_usage(usage: Any) -> dict[str, int]:
    """Normalize Chat Completions and Responses API usage variants."""

    if hasattr(usage, "model_dump"):
        usage = usage.model_dump()
    if not isinstance(usage, dict):
        usage = {}

    prompt_details = usage.get("prompt_tokens_details") or usage.get("input_tokens_details") or {}
    completion_details = usage.get("completion_tokens_details") or usage.get("output_tokens_details") or {}
    input_tokens = _first_int(usage, "prompt_tokens", "input_tokens", "promptTokenCount")
    output_tokens = _first_int(usage, "completion_tokens", "output_tokens", "candidatesTokenCount")
    cached_tokens = _first_int(
        prompt_details,
        "cached_tokens",
        "cachedContentTokenCount",
    ) or _first_int(usage, "cachedContentTokenCount")
    reasoning_tokens = _first_int(
        completion_details,
        "reasoning_tokens",
        "thoughtsTokenCount",
    ) or _first_int(usage, "thoughtsTokenCount")
    total_tokens = _first_int(usage, "total_tokens", "totalTokenCount") or input_tokens + output_tokens
    return {
        "input_tokens": input_tokens,
        "cached_input_tokens": cached_tokens,
        "output_tokens": output_tokens,
        "reasoning_tokens": reasoning_tokens,
        "total_tokens": total_tokens,
    }


def record_llm_call(
    *,
    step: str,
    model: str,
    measurement: PromptMeasurement,
    usage: Any = None,
    elapsed_seconds: float | None = None,
    status: str = "ok",
    attempt: int = 1,
    error: str = "",
    manifest_path: str | Path | None = None,
) -> dict[str, Any]:
    """Append one transport attempt to the run-level JSONL manifest when configured."""

    row = {
        "recorded_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "execution_id": os.getenv("LLM_EXECUTION_ID", ""),
        "run_id": os.getenv("LLM_RUN_ID", ""),
        "run_role": os.getenv("LLM_RUN_ROLE", ""),
        "company_name": os.getenv("LLM_COMPANY_NAME", ""),
        "step": step,
        "model": model,
        "status": status,
        "attempt": attempt,
        "elapsed_seconds": round(elapsed_seconds, 3) if elapsed_seconds is not None else None,
        "request": measurement.as_dict(),
        "usage": normalize_usage(usage),
        "error": error,
    }
    resolved_path = Path(manifest_path or os.getenv("LLM_USAGE_MANIFEST", "")).expanduser()
    if str(resolved_path) not in {"", "."}:
        _append_jsonl(resolved_path, row)
    return row


ItemT = TypeVar("ItemT")
ResponseT = TypeVar("ResponseT")


def execute_with_telemetry(
    call: Callable[[], ResponseT],
    *,
    request_payload: Any,
    model: str,
    step: str,
    usage_getter: Callable[[ResponseT], Any] | None = None,
    max_attempts: int = 1,
    retry_predicate: Callable[[Exception], bool] | None = None,
    backoff_seconds: float = 1.0,
    sleep: Callable[[float], None] = time.sleep,
    jitter: Callable[[], float] = random.random,
) -> ResponseT:
    """Execute a transport call with preflight, bounded retries, and per-attempt telemetry."""

    measurement = preflight_request(request_payload, model=model, step=step)
    attempts = max(1, int(max_attempts))
    for attempt in range(1, attempts + 1):
        started = time.monotonic()
        try:
            response = call()
        except Exception as exc:
            record_llm_call(
                step=step,
                model=model,
                measurement=measurement,
                elapsed_seconds=time.monotonic() - started,
                status="error",
                attempt=attempt,
                error=str(exc),
            )
            should_retry = (
                attempt < attempts
                and retry_predicate is not None
                and retry_predicate(exc)
            )
            if not should_retry:
                raise
            delay = max(0.0, float(backoff_seconds)) * (2 ** (attempt - 1))
            if delay:
                sleep(delay + (delay * 0.25 * max(0.0, min(1.0, float(jitter())))))
            continue
        usage = usage_getter(response) if usage_getter else None
        record_llm_call(
            step=step,
            model=model,
            measurement=measurement,
            usage=usage,
            elapsed_seconds=time.monotonic() - started,
            attempt=attempt,
        )
        return response
    raise RuntimeError("LLM transport retry loop exhausted without a result.")


def is_transient_transport_error(exc: Exception) -> bool:
    """Return whether an exception is safe to retry as a transient transport failure."""

    current: BaseException | None = exc
    while current is not None:
        status_code = getattr(current, "status_code", None)
        if status_code is None:
            status_code = getattr(current, "code", None)
        try:
            status = int(status_code) if status_code is not None else 0
        except (TypeError, ValueError):
            status = 0
        if status in {408, 409, 429} or 500 <= status <= 599:
            return True
        if status:
            return False
        if isinstance(current, (TimeoutError, ConnectionError, OSError)):
            return True
        name = type(current).__name__.lower()
        if any(token in name for token in ("timeout", "connection", "ratelimit")):
            return True
        reason = getattr(current, "reason", None)
        current = current.__cause__ or current.__context__ or (
            reason if isinstance(reason, BaseException) else None
        )
    return False


def partition_by_prompt_budget(
    items: Iterable[ItemT],
    *,
    build_request: Callable[[list[ItemT]], Any],
    model: str,
    target_input_tokens: int | None = None,
) -> list[list[ItemT]]:
    """Greedily partition items while measuring each exact candidate request."""

    target, _ = prompt_budget_for_model(model)
    limit = target_input_tokens or target
    chunks: list[list[ItemT]] = []
    current: list[ItemT] = []
    for item in items:
        candidate = [*current, item]
        measurement = measure_request(build_request(candidate), model=model)
        if current and measurement.estimated_input_tokens > limit:
            chunks.append(current)
            current = [item]
            preflight_request(build_request(current), model=model, step="single batch item")
        else:
            current = candidate
            preflight_request(build_request(current), model=model, step="batch")
    if current:
        chunks.append(current)
    return chunks


def _first_int(payload: Any, *keys: str) -> int:
    if not isinstance(payload, dict):
        return 0
    for key in keys:
        value = payload.get(key)
        if value is None:
            continue
        try:
            return int(value)
        except (TypeError, ValueError):
            continue
    return 0


def _append_jsonl(path: Path, row: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    line = compact_json(row) + "\n"
    with path.open("a", encoding="utf-8") as handle:
        try:
            import fcntl

            fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
            handle.write(line)
            handle.flush()
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        except ImportError:  # pragma: no cover - non-POSIX fallback
            handle.write(line)
            handle.flush()
