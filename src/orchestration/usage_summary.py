"""Execution-scoped LLM usage summaries for the full report pipeline."""

from __future__ import annotations

import json
from collections import defaultdict
from pathlib import Path
from typing import Any


NORMAL_RUN_ROLES = ("target", "peer", "final")
MILLION_TOKENS = 1_000_000
OPENAI_PRICING_SOURCE = "https://developers.openai.com/api/docs/models/gpt-5.4"
MODEL_PRICING_USD_PER_MILLION = {
    "gpt-5.4": {
        "input": 2.50,
        "cached_input": 0.25,
        "output": 15.00,
        "long_context_threshold": 272_000,
        "long_context_input_multiplier": 2.0,
        "long_context_output_multiplier": 1.5,
    },
    "gpt-5.4-mini": {
        "input": 0.75,
        "cached_input": 0.075,
        "output": 4.50,
        "long_context_threshold": 0,
        "long_context_input_multiplier": 1.0,
        "long_context_output_multiplier": 1.0,
    },
}
EXPECTED_LOGICAL_CALLS_BY_ROLE = {
    "target": 6,
    "peer": 6,
    "final": 2,
}
USAGE_KEYS = (
    "input_tokens",
    "cached_input_tokens",
    "output_tokens",
    "reasoning_tokens",
    "total_tokens",
)


def summarize_execution_usage(
    manifest_path: str | Path,
    *,
    execution_id: str,
    pipeline_completed: bool,
    expected_logical_calls_by_role: dict[str, int] | None = None,
) -> dict[str, Any]:
    """Summarize one execution without conflating retries and logical calls."""

    path = Path(manifest_path).expanduser().resolve()
    rows = [
        row
        for row in _read_jsonl(path)
        if row.get("execution_id") == execution_id and row.get("run_role") in NORMAL_RUN_ROLES
    ]
    expected_by_role = {
        role: int((expected_logical_calls_by_role or EXPECTED_LOGICAL_CALLS_BY_ROLE).get(role, 0))
        for role in NORMAL_RUN_ROLES
    }
    expected_total = sum(expected_by_role.values())
    successful_rows = [row for row in rows if row.get("status") == "ok"]
    logical_keys = {_logical_call_key(row) for row in successful_rows}
    logical_keys.discard(None)

    totals = _empty_usage()
    request_estimate_total = 0
    max_estimated_input_tokens = 0
    over_target_count = 0
    by_role: dict[str, dict[str, Any]] = {}
    by_step: dict[str, dict[str, Any]] = {}

    for role in NORMAL_RUN_ROLES:
        role_rows = [row for row in rows if row.get("run_role") == role]
        role_successes = [row for row in role_rows if row.get("status") == "ok"]
        role_keys = {_logical_call_key(row) for row in role_successes}
        role_keys.discard(None)
        by_role[role] = {
            "expected_logical_calls": expected_by_role[role],
            "observed_logical_calls": len(role_keys),
            "transport_attempts": len(role_rows),
            "error_attempts": sum(row.get("status") != "ok" for row in role_rows),
            "usage": _sum_usage(role_rows),
        }

    grouped: dict[tuple[str, str, str], list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        grouped[(
            str(row.get("run_role") or ""),
            str(row.get("run_id") or ""),
            str(row.get("step") or "unknown"),
        )].append(row)
        request = row.get("request") if isinstance(row.get("request"), dict) else {}
        estimated = _integer(request.get("estimated_input_tokens"))
        request_estimate_total += estimated
        max_estimated_input_tokens = max(max_estimated_input_tokens, estimated)
        over_target_count += bool(request.get("over_target"))
        _add_usage(totals, row.get("usage"))

    for (role, run_id, step), step_rows in sorted(grouped.items()):
        step_successes = [row for row in step_rows if row.get("status") == "ok"]
        step_keys = {_logical_call_key(row) for row in step_successes}
        step_keys.discard(None)
        by_step[f"{role}:{run_id}:{step}"] = {
            "run_role": role,
            "run_id": run_id,
            "step": step,
            "logical_calls": len(step_keys),
            "transport_attempts": len(step_rows),
            "error_attempts": sum(row.get("status") != "ok" for row in step_rows),
            "usage": _sum_usage(step_rows),
            "max_estimated_input_tokens": max(
                (
                    _integer((row.get("request") or {}).get("estimated_input_tokens"))
                    for row in step_rows
                    if isinstance(row.get("request"), dict)
                ),
                default=0,
            ),
        }

    observed = len(logical_keys)
    unobserved_expected = max(0, expected_total - observed)
    summary = {
        "execution_id": execution_id,
        "source": str(path),
        "scope": {
            "included": "normal target, peer, Strategy, and Writer OpenAI transports",
            "excluded": [
                "deterministic collection and transformation",
                "Naver peer identity resolution",
                "peer comparison dataset construction",
                "evaluation-role LLM calls",
            ],
            "retry_policy": "Retries are transport attempts, not additional logical calls.",
        },
        "expected_cold_cache_logical_calls": expected_total,
        "expected_logical_calls_by_role": expected_by_role,
        "observed_logical_calls": observed,
        "transport_attempts": len(rows),
        "successful_transport_attempts": len(successful_rows),
        "error_transport_attempts": len(rows) - len(successful_rows),
        "cache_suppressed_or_unobserved_calls": unobserved_expected,
        "cache_suppressed_calls": unobserved_expected if pipeline_completed else None,
        "pipeline_completed": pipeline_completed,
        "cold_cache_call_count_matches": observed == expected_total,
        "usage": totals,
        "uncached_input_tokens": max(0, totals["input_tokens"] - totals["cached_input_tokens"]),
        "request_budget": {
            "estimated_input_tokens_across_transport_attempts": request_estimate_total,
            "max_estimated_input_tokens": max_estimated_input_tokens,
            "over_target_attempt_count": over_target_count,
        },
        "by_role": by_role,
        "by_step": by_step,
    }
    summary["estimated_api_cost"] = estimate_api_cost(rows)
    return summary


def estimate_api_cost(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """Estimate standard OpenAI text-token charges for recorded transports.

    ``input_tokens`` includes cached input, so cached tokens are subtracted
    before the regular input rate is applied. Reasoning tokens are already a
    subset of output tokens and are not charged a second time.
    """

    by_model: dict[str, dict[str, Any]] = {}
    unknown_models: set[str] = set()
    priced_usage_present = False

    for row in rows:
        usage = row.get("usage") if isinstance(row.get("usage"), dict) else {}
        input_tokens = max(0, _integer(usage.get("input_tokens")))
        cached_tokens = min(input_tokens, max(0, _integer(usage.get("cached_input_tokens"))))
        uncached_tokens = input_tokens - cached_tokens
        output_tokens = max(0, _integer(usage.get("output_tokens")))
        if input_tokens == 0 and output_tokens == 0:
            continue

        raw_model = str(row.get("model") or "").strip()
        billing_model = _billing_model(raw_model)
        pricing = MODEL_PRICING_USD_PER_MILLION.get(billing_model)
        if pricing is None:
            unknown_models.add(raw_model or "unknown")
            continue

        priced_usage_present = True
        long_context = (
            int(pricing["long_context_threshold"]) > 0
            and input_tokens > int(pricing["long_context_threshold"])
        )
        input_multiplier = (
            float(pricing["long_context_input_multiplier"]) if long_context else 1.0
        )
        output_multiplier = (
            float(pricing["long_context_output_multiplier"]) if long_context else 1.0
        )
        uncached_cost = (
            uncached_tokens
            * float(pricing["input"])
            * input_multiplier
            / MILLION_TOKENS
        )
        cached_cost = (
            cached_tokens
            * float(pricing["cached_input"])
            * input_multiplier
            / MILLION_TOKENS
        )
        output_cost = (
            output_tokens
            * float(pricing["output"])
            * output_multiplier
            / MILLION_TOKENS
        )

        model_summary = by_model.setdefault(
            billing_model,
            {
                "observed_model_names": [],
                "uncached_input_tokens": 0,
                "cached_input_tokens": 0,
                "output_tokens": 0,
                "long_context_requests": 0,
                "pricing_usd_per_million_tokens": {
                    "input": float(pricing["input"]),
                    "cached_input": float(pricing["cached_input"]),
                    "output": float(pricing["output"]),
                },
                "uncached_input_cost_usd": 0.0,
                "cached_input_cost_usd": 0.0,
                "output_cost_usd": 0.0,
                "total_cost_usd": 0.0,
            },
        )
        if raw_model and raw_model not in model_summary["observed_model_names"]:
            model_summary["observed_model_names"].append(raw_model)
        model_summary["uncached_input_tokens"] += uncached_tokens
        model_summary["cached_input_tokens"] += cached_tokens
        model_summary["output_tokens"] += output_tokens
        model_summary["long_context_requests"] += int(long_context)
        model_summary["uncached_input_cost_usd"] += uncached_cost
        model_summary["cached_input_cost_usd"] += cached_cost
        model_summary["output_cost_usd"] += output_cost
        model_summary["total_cost_usd"] += uncached_cost + cached_cost + output_cost

    for model_summary in by_model.values():
        for key in (
            "uncached_input_cost_usd",
            "cached_input_cost_usd",
            "output_cost_usd",
            "total_cost_usd",
        ):
            model_summary[key] = round(float(model_summary[key]), 8)

    total_cost = round(
        sum(float(item["total_cost_usd"]) for item in by_model.values()),
        8,
    )
    if unknown_models:
        status = "partial" if priced_usage_present else "unavailable"
    else:
        status = "available"
    return {
        "status": status,
        "currency": "USD",
        "total_cost_usd": total_cost if priced_usage_present or not unknown_models else None,
        "by_model": by_model,
        "unknown_models": sorted(unknown_models),
        "pricing_basis": "OpenAI standard API text-token rates per 1M tokens",
        "pricing_source": OPENAI_PRICING_SOURCE,
        "notes": [
            "Cached input is priced separately from uncached input.",
            "Reasoning tokens are included in output tokens and are not added twice.",
            "The estimate excludes tool-call fees and regional-processing uplifts.",
        ],
    }


def _billing_model(model: str) -> str:
    normalized = str(model or "").strip().lower()
    if normalized == "gpt-5.4-mini" or normalized.startswith("gpt-5.4-mini-"):
        return "gpt-5.4-mini"
    if normalized == "gpt-5.4" or normalized.startswith("gpt-5.4-202"):
        return "gpt-5.4"
    return normalized


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    rows: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(row, dict):
            rows.append(row)
    return rows


def _logical_call_key(row: dict[str, Any]) -> tuple[str, str, str, str] | None:
    request = row.get("request") if isinstance(row.get("request"), dict) else {}
    request_sha = str(request.get("request_sha256") or "")
    if not request_sha:
        return None
    return (
        str(row.get("run_role") or ""),
        str(row.get("run_id") or ""),
        str(row.get("step") or "unknown"),
        request_sha,
    )


def _empty_usage() -> dict[str, int]:
    return {key: 0 for key in USAGE_KEYS}


def _sum_usage(rows: list[dict[str, Any]]) -> dict[str, int]:
    total = _empty_usage()
    for row in rows:
        _add_usage(total, row.get("usage"))
    return total


def _add_usage(total: dict[str, int], usage: Any) -> None:
    normalized = usage if isinstance(usage, dict) else {}
    for key in USAGE_KEYS:
        total[key] += _integer(normalized.get(key))


def _integer(value: Any) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0
