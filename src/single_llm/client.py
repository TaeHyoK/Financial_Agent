"""The only semantic generation call used by the Single-LLM baseline."""

from __future__ import annotations

import hashlib
import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from shared.llm_clients import (
    compact_json,
    estimate_text_tokens,
    execute_with_telemetry,
    is_transient_transport_error,
    normalize_usage,
)

from .config import SingleLLMConfig
from .contracts import single_llm_response_format


DEFAULT_PROMPT_PATH = Path(__file__).resolve().parent / "prompts" / "single_llm_direct.md"


@dataclass(frozen=True)
class SingleLLMResult:
    report: dict[str, Any]
    response_id: str
    response_model: str
    usage: dict[str, int]


def build_single_llm_request(
    *,
    bundle: dict[str, Any],
    config: SingleLLMConfig,
    prompt_path: str | Path = DEFAULT_PROMPT_PATH,
) -> dict[str, Any]:
    """Build the exact one-call request from the frozen raw-data bundle."""

    prompt = Path(prompt_path).expanduser().resolve().read_text(encoding="utf-8").strip()
    evidence_ids = [
        str(item.get("evidence_id"))
        for item in bundle.get("evidence_catalog") or []
        if isinstance(item, dict) and item.get("evidence_id")
    ]
    target = bundle.get("target") or {}
    request: dict[str, Any] = {
        "model": config.model,
        "messages": [
            {"role": "system", "content": prompt},
            {"role": "user", "content": compact_json(bundle)},
        ],
        "response_format": single_llm_response_format(
            evidence_ids=evidence_ids,
            company_name=str(target.get("company_name") or ""),
            selected_date=str(bundle.get("selected_date") or ""),
            decision_horizon=str(bundle.get("decision_horizon") or ""),
        ),
    }
    if _uses_max_completion_tokens(config.model):
        request["max_completion_tokens"] = config.max_output_tokens
    else:
        request["temperature"] = config.temperature
        request["max_tokens"] = config.max_output_tokens
    return request


def estimate_request_tokens(request: dict[str, Any], *, model: str) -> int:
    """Estimate the complete request, including prompt and JSON schema."""

    return estimate_text_tokens(compact_json(request), model=model)


def request_fingerprint(request: dict[str, Any]) -> str:
    return hashlib.sha256(
        compact_json(request, sort_keys=True).encode("utf-8")
    ).hexdigest()


def call_single_llm(
    request: dict[str, Any],
    *,
    config: SingleLLMConfig,
) -> SingleLLMResult:
    """Make one semantic call; only explicitly configured transport retries are allowed."""

    api_key = os.getenv("OPENAI_API_KEY", "").strip()
    if not api_key:
        raise RuntimeError("OPENAI_API_KEY is required for Single-LLM generation")
    try:
        from openai import OpenAI
    except Exception as exc:  # pragma: no cover - environment dependent
        raise RuntimeError(f"openai package is unavailable: {exc}") from exc

    client = OpenAI(
        api_key=api_key,
        timeout=config.timeout_seconds,
        max_retries=0,
    )
    response = execute_with_telemetry(
        lambda: client.chat.completions.create(**request),
        request_payload=request,
        model=config.model,
        step="single_llm:direct_report",
        usage_getter=lambda result: getattr(result, "usage", None),
        max_attempts=config.transport_retries + 1,
        retry_predicate=is_transient_transport_error,
    )
    message = response.choices[0].message
    refusal = getattr(message, "refusal", None)
    if refusal:
        raise RuntimeError(f"Single-LLM request was refused: {refusal}")
    content = message.content
    if not content:
        raise RuntimeError("Single-LLM returned empty content")
    try:
        report = json.loads(content)
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"Single-LLM returned invalid JSON: {exc}") from exc
    if not isinstance(report, dict):
        raise RuntimeError("Single-LLM response must be a JSON object")
    return SingleLLMResult(
        report=report,
        response_id=str(getattr(response, "id", "") or ""),
        response_model=str(getattr(response, "model", "") or config.model),
        usage=normalize_usage(getattr(response, "usage", None)),
    )


def estimate_cost_usd(usage: dict[str, int], config: SingleLLMConfig) -> dict[str, Any]:
    """Estimate token cost from the explicitly versioned rates in the YAML config."""

    if config.model != config.pricing_model:
        return {
            "status": "unavailable_model_mismatch",
            "generation_model": config.model,
            "pricing_model": config.pricing_model,
            "total_usd": None,
        }

    input_tokens = int(usage.get("input_tokens") or 0)
    cached_tokens = min(input_tokens, int(usage.get("cached_input_tokens") or 0))
    uncached_tokens = input_tokens - cached_tokens
    output_tokens = int(usage.get("output_tokens") or 0)
    input_cost = uncached_tokens * config.input_price_per_million / 1_000_000
    cached_cost = cached_tokens * config.cached_input_price_per_million / 1_000_000
    output_cost = output_tokens * config.output_price_per_million / 1_000_000
    return {
        "status": "estimated",
        "generation_model": config.model,
        "pricing_model": config.pricing_model,
        "uncached_input_usd": round(input_cost, 6),
        "cached_input_usd": round(cached_cost, 6),
        "output_usd": round(output_cost, 6),
        "total_usd": round(input_cost + cached_cost + output_cost, 6),
    }


def _uses_max_completion_tokens(model: str) -> bool:
    return str(model).lower().startswith(("gpt-5", "o1", "o3", "o4"))


__all__ = [
    "DEFAULT_PROMPT_PATH",
    "SingleLLMResult",
    "build_single_llm_request",
    "call_single_llm",
    "estimate_cost_usd",
    "estimate_request_tokens",
    "request_fingerprint",
]
