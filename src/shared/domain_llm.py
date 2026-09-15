"""Common request/transport policy for separated and integrated domain analysis."""
from __future__ import annotations

import copy
import json
import os
from typing import Any

from .llm_clients import execute_with_telemetry, is_transient_transport_error

DOMAIN_OUTPUT_BUDGETS = {"financial": 12000, "news": 12000, "market": 12000}
DOMAIN_TIMEOUT_SECONDS = 300
DOMAIN_POLICY_VERSION = "domain_parity_v4_analysis_before_conclusion"


def domain_request(request: dict[str, Any], *, domain: str) -> dict[str, Any]:
    """Normalize legacy builders to the same explicit Responses policy."""
    result = copy.deepcopy(request)
    if "messages" in result:
        result["input"] = result.pop("messages")
        format_spec = result.pop("response_format")["json_schema"]
        result["text"] = {"format": {"type": "json_schema", **format_spec}}
    result["text"]["verbosity"] = "medium"
    result["store"] = False
    result["temperature"] = 0.2
    if str(result["model"]).startswith("gpt-5.4"):
        result["reasoning"] = {"effort": "none"}
    result["max_output_tokens"] = (sum(DOMAIN_OUTPUT_BUDGETS.values()) if domain == "unified"
                                   else DOMAIN_OUTPUT_BUDGETS[domain])
    return result


def call_domain_response(request: dict[str, Any], *, step: str, timeout_seconds: float | None = None) -> Any:
    from openai import OpenAI
    timeout = timeout_seconds if timeout_seconds is not None else float(os.getenv("LLM_TIMEOUT_SECONDS", DOMAIN_TIMEOUT_SECONDS))
    if timeout <= 0:
        raise ValueError("Domain LLM timeout must be positive")
    client = OpenAI(timeout=timeout, max_retries=0)
    response = execute_with_telemetry(
        lambda: client.responses.create(**request), request_payload=request,
        model=request["model"], step=step,
        usage_getter=lambda result: getattr(result, "usage", None),
        max_attempts=3, retry_predicate=is_transient_transport_error, backoff_seconds=1.0,
    )
    # Usage has been recorded already; never retry a semantic/schema failure.
    if getattr(response, "status", "completed") != "completed":
        raise RuntimeError(f"{step}: incomplete response (possibly output token limit)")
    if not getattr(response, "output_text", ""):
        raise RuntimeError(f"{step}: empty or refused response")
    from jsonschema import Draft202012Validator
    Draft202012Validator(request["text"]["format"]["schema"]).validate(json.loads(response.output_text))
    return response
