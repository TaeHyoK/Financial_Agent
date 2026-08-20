"""Blind, cross-order LLM Judge for evidence-grounded financial reports."""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
from typing import Any

from shared.llm_clients import (
    compact_json,
    execute_with_telemetry,
    is_transient_transport_error,
)

from .final_report_evaluation_metrics import AXES, validate_judgment


DEFAULT_JUDGE_MODEL = "gpt-5.4"
DEFAULT_PROMPT_PATH = Path(__file__).resolve().parent / "prompts" / "final_report_pairwise_judge.md"
EVIDENCE_SCOPES: tuple[str, ...] = ("candidate_specific", "union_blind")
ERROR_TAGS: tuple[str, ...] = (
    "unsupported_numeric",
    "incorrect_unit_or_period",
    "temporal_leakage",
    "comparison_scope_error",
    "unsupported_causal_claim",
    "evidence_omission",
    "recommendation_inconsistency",
    "risk_omission",
    "limitation_omission",
    "verbosity_or_repetition",
    "unclear_writing",
)


def judge_response_format(allowed_card_keys: list[str]) -> dict[str, Any]:
    """Return the strict Structured Outputs contract for all six axes."""

    card_keys = sorted({str(key) for key in allowed_card_keys if str(key).strip()})
    if not card_keys:
        raise ValueError("At least one evidence card key is required for Judge grounding.")
    axis_schema = _strict_object(
        {
            "winner": {"type": "string", "enum": ["A", "B", "tie"]},
            "reason": {"type": "string"},
            "supporting_card_keys": {
                "type": "array",
                "items": {"type": "string", "enum": card_keys},
            },
            "candidate_a_error_tags": {
                "type": "array",
                "items": {"type": "string", "enum": list(ERROR_TAGS)},
            },
            "candidate_b_error_tags": {
                "type": "array",
                "items": {"type": "string", "enum": list(ERROR_TAGS)},
            },
        }
    )
    return {
        "type": "json_schema",
        "json_schema": {
            "name": "final_report_pairwise_judgment_v1",
            "strict": True,
            "schema": _strict_object(
                {
                    "axes": _strict_object({axis: axis_schema for axis in AXES}),
                }
            ),
        },
    }


def build_judge_request(
    *,
    candidate_a: dict[str, Any],
    candidate_b: dict[str, Any],
    evidence_bundle: dict[str, Any],
    model: str,
    prompt_path: str | Path = DEFAULT_PROMPT_PATH,
    candidate_a_available_card_keys: list[str] | None = None,
    candidate_b_available_card_keys: list[str] | None = None,
    candidate_a_evidence_bundle: dict[str, Any] | None = None,
    candidate_b_evidence_bundle: dict[str, Any] | None = None,
    evidence_scope: str = "candidate_specific",
) -> dict[str, Any]:
    """Build the exact candidate-blind request sent to the Judge."""

    prompt = Path(prompt_path).expanduser().resolve().read_text(encoding="utf-8").strip()
    card_keys = [
        str(card.get("card_key"))
        for card in evidence_bundle.get("cards") or []
        if isinstance(card, dict) and card.get("card_key")
    ]
    available_a = (
        card_keys
        if candidate_a_available_card_keys is None
        else candidate_a_available_card_keys
    )
    available_b = (
        card_keys
        if candidate_b_available_card_keys is None
        else candidate_b_available_card_keys
    )
    if evidence_scope not in EVIDENCE_SCOPES:
        raise ValueError(f"Unsupported evidence scope: {evidence_scope}")
    evaluation_contract: dict[str, Any] = {
        "candidate_identity_is_blind": True,
        "use_only_supplied_evidence": True,
        "do_not_use_post_cutoff_information": True,
        "judge_report_length_only_when_it_affects_clarity": True,
        "required_axes": list(AXES),
        "evidence_scope": evidence_scope,
    }
    user_payload: dict[str, Any] = {
        "evaluation_contract": evaluation_contract,
        "common_evidence_bundle": evidence_bundle,
        "candidate_A": candidate_a,
        "candidate_B": candidate_b,
    }
    if evidence_scope == "candidate_specific":
        evaluation_contract["candidate_evidence_access"] = {
            "A": sorted(set(available_a)),
            "B": sorted(set(available_b)),
        }
        user_payload["candidate_accessible_evidence"] = {
            "A": candidate_a_evidence_bundle or {"cards": []},
            "B": candidate_b_evidence_bundle or {"cards": []},
        }
    else:
        evaluation_contract.update(
            {
                "union_bundle_is_fact_check_reference_only": True,
                "do_not_infer_candidate_access": True,
                "do_not_penalize_unused_cards": True,
                "do_not_reward_raw_information_quantity": True,
            }
        )
    request_payload: dict[str, Any] = {
        "model": model,
        "messages": [
            {"role": "system", "content": prompt},
            {"role": "user", "content": compact_json(user_payload)},
        ],
        "response_format": judge_response_format(card_keys),
    }
    max_tokens = max(1000, int(os.getenv("EVALUATION_MAX_COMPLETION_TOKENS", "8000")))
    if _uses_max_completion_tokens(model):
        request_payload["max_completion_tokens"] = max_tokens
    else:
        request_payload["temperature"] = 0
        request_payload["max_tokens"] = max_tokens
    return request_payload


def request_fingerprint(request_payload: dict[str, Any]) -> str:
    return hashlib.sha256(
        compact_json(request_payload, sort_keys=True).encode("utf-8")
    ).hexdigest()


def call_pairwise_judge(
    request_payload: dict[str, Any],
    *,
    timeout_seconds: float = 300.0,
    transport_retries: int = 1,
) -> dict[str, Any]:
    """Call OpenAI and semantically validate the strict Judge response."""

    api_key = os.getenv("OPENAI_API_KEY", "").strip()
    if not api_key:
        raise RuntimeError("OPENAI_API_KEY is required for LLM Judge evaluation.")
    try:
        from openai import OpenAI
    except Exception as exc:  # pragma: no cover - environment dependent
        raise RuntimeError(f"openai package is unavailable: {exc}") from exc

    model = str(request_payload.get("model") or DEFAULT_JUDGE_MODEL)
    client = OpenAI(api_key=api_key, timeout=timeout_seconds, max_retries=0)
    response = execute_with_telemetry(
        lambda: client.chat.completions.create(**request_payload),
        request_payload=request_payload,
        model=model,
        step=f"evaluation:final_report_pairwise:{os.getenv('LLM_RUN_ID', 'pair')}",
        usage_getter=lambda result: getattr(result, "usage", None),
        max_attempts=max(0, int(transport_retries)) + 1,
        retry_predicate=is_transient_transport_error,
    )
    content = response.choices[0].message.content
    if not content:
        raise RuntimeError("LLM Judge returned empty content.")
    try:
        payload = json.loads(content)
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"LLM Judge returned invalid JSON: {exc}") from exc
    allowed_card_keys = _card_key_enum(request_payload)
    return validate_judgment(payload, allowed_card_keys=allowed_card_keys)


def _card_key_enum(request_payload: dict[str, Any]) -> list[str]:
    try:
        return list(
            request_payload["response_format"]["json_schema"]["schema"]["properties"]
            ["axes"]["properties"][AXES[0]]["properties"]["supporting_card_keys"]
            ["items"]["enum"]
        )
    except (KeyError, TypeError) as exc:
        raise ValueError("Judge request has no valid supporting-card enum.") from exc


def _strict_object(properties: dict[str, Any]) -> dict[str, Any]:
    return {
        "type": "object",
        "properties": properties,
        "required": list(properties),
        "additionalProperties": False,
    }


def _uses_max_completion_tokens(model: str) -> bool:
    return str(model).startswith(("gpt-5", "o1", "o3", "o4"))


__all__ = [
    "DEFAULT_JUDGE_MODEL",
    "EVIDENCE_SCOPES",
    "ERROR_TAGS",
    "build_judge_request",
    "call_pairwise_judge",
    "judge_response_format",
    "request_fingerprint",
]
