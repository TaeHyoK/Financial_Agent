"""Strict, blind, cross-order LLM Judge request and transport."""

from __future__ import annotations

import hashlib
import json
import os
import sys
from pathlib import Path
from typing import Any

from .bundle import compact_json
from .metrics import AXES, validate_judgment


FINAL_SRC = Path(os.getenv("FINANCIAL_AGENT_ROOT", Path(__file__).resolve().parents[1])).resolve() / "src"
if str(FINAL_SRC) not in sys.path:
    sys.path.insert(0, str(FINAL_SRC))

from shared.llm_clients import execute_with_telemetry, is_transient_transport_error  # noqa: E402


DEFAULT_JUDGE_MODEL = "gpt-5.4-mini"
DEFAULT_PROMPT_PATH = (
    Path(__file__).resolve().parent / "prompts" / "pairwise_union_blind_ko.md"
)
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
EXISTING_POSITION_LABELS: tuple[str, ...] = ("increase", "hold", "reduce", "unclear")
NEW_ENTRY_LABELS: tuple[str, ...] = ("enter", "wait", "avoid", "unclear")


def response_format(allowed_card_keys: list[str]) -> dict[str, Any]:
    card_keys = sorted({str(key) for key in allowed_card_keys if str(key).strip()})
    if not card_keys:
        raise ValueError("At least one evidence card key is required.")
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
    recommendation_schema = _strict_object(
        {
            "existing_position": {
                "type": "string",
                "enum": list(EXISTING_POSITION_LABELS),
            },
            "new_entry": {"type": "string", "enum": list(NEW_ENTRY_LABELS)},
        }
    )
    return {
        "type": "json_schema",
        "json_schema": {
            "name": "ablation_pairwise_union_blind_v1",
            "strict": True,
            "schema": _strict_object(
                {
                    "axes": _strict_object({axis: axis_schema for axis in AXES}),
                    "recommendations": _strict_object(
                        {
                            "candidate_A": recommendation_schema,
                            "candidate_B": recommendation_schema,
                        }
                    ),
                }
            ),
        },
    }


def build_request(
    *,
    candidate_a: dict[str, Any],
    candidate_b: dict[str, Any],
    evidence_bundle: dict[str, Any],
    model: str,
    prompt_path: str | Path = DEFAULT_PROMPT_PATH,
) -> dict[str, Any]:
    """Build the exact label-free request; no condition metadata enters this payload."""

    prompt = Path(prompt_path).expanduser().resolve().read_text(encoding="utf-8").strip()
    card_keys = [
        str(card.get("card_key"))
        for card in evidence_bundle.get("cards") or []
        if isinstance(card, dict) and card.get("card_key")
    ]
    payload = {
        "evaluation_contract": {
            "candidate_identity_is_blind": True,
            "evidence_mode": "union_blind",
            "union_bundle_is_fact_check_reference_only": True,
            "do_not_infer_candidate_access": True,
            "do_not_penalize_unused_cards": True,
            "do_not_reward_raw_information_quantity": True,
            "use_only_supplied_evidence": True,
            "do_not_use_post_cutoff_information": True,
            "required_axes": list(AXES),
        },
        "common_evidence_bundle": evidence_bundle,
        "candidate_A": candidate_a,
        "candidate_B": candidate_b,
    }
    request: dict[str, Any] = {
        "model": model,
        "messages": [
            {"role": "system", "content": prompt},
            {"role": "user", "content": compact_json(payload)},
        ],
        "response_format": response_format(card_keys),
    }
    max_tokens = max(1000, int(os.getenv("EVALUATION_MAX_COMPLETION_TOKENS", "8000")))
    if _uses_max_completion_tokens(model):
        request["max_completion_tokens"] = max_tokens
    else:
        request["temperature"] = 0
        request["max_tokens"] = max_tokens
    return request


def request_fingerprint(request: dict[str, Any]) -> str:
    return hashlib.sha256(compact_json(request, sort_keys=True).encode("utf-8")).hexdigest()


def call_judge(
    request: dict[str, Any],
    *,
    timeout_seconds: float = 300.0,
    transport_retries: int = 1,
) -> dict[str, Any]:
    api_key = os.getenv("OPENAI_API_KEY", "").strip()
    if not api_key:
        raise RuntimeError("OPENAI_API_KEY is required for LLM Judge evaluation.")
    try:
        from openai import OpenAI
    except Exception as exc:  # pragma: no cover - environment dependent
        raise RuntimeError(f"openai package is unavailable: {exc}") from exc

    model = str(request.get("model") or DEFAULT_JUDGE_MODEL)
    client = OpenAI(api_key=api_key, timeout=timeout_seconds, max_retries=0)
    response = execute_with_telemetry(
        lambda: client.chat.completions.create(**request),
        request_payload=request,
        model=model,
        step=f"evaluation:ablation_pairwise:{os.getenv('LLM_RUN_ID', 'pair')}",
        usage_getter=lambda result: getattr(result, "usage", None),
        max_attempts=max(0, int(transport_retries)) + 1,
        retry_predicate=is_transient_transport_error,
    )
    content = response.choices[0].message.content
    if not content:
        raise RuntimeError("LLM Judge returned empty content.")
    try:
        judgment = json.loads(content)
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"LLM Judge returned invalid JSON: {exc}") from exc
    return validate_judgment(judgment, allowed_card_keys=_card_key_enum(request))


def _card_key_enum(request: dict[str, Any]) -> list[str]:
    try:
        return list(
            request["response_format"]["json_schema"]["schema"]["properties"]["axes"]
            ["properties"][AXES[0]]["properties"]["supporting_card_keys"]["items"]["enum"]
        )
    except (KeyError, TypeError) as exc:
        raise ValueError("Judge request has no valid evidence-card enum.") from exc


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
    "DEFAULT_PROMPT_PATH",
    "ERROR_TAGS",
    "EXISTING_POSITION_LABELS",
    "NEW_ENTRY_LABELS",
    "build_request",
    "call_judge",
    "request_fingerprint",
    "response_format",
]
