"""LLM comparison of one target company and one selected domestic peer.

The deterministic pairwise dataset remains the source of like-for-like numeric
comparisons.  This module adds one analytical pass over both companies' domain
handoffs so that the peer is not reduced to a small metric table before
Strategy synthesis.
"""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
from copy import deepcopy
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from openai import OpenAI

from shared.llm_clients import (
    compact_json,
    execute_with_telemetry,
    is_transient_transport_error,
)

from . import AGENT_DIR


CONTEXT_VERSION = "peer_comparison_context_v1"
OUTPUT_VERSION = "peer_comparison_analysis_v1"
CACHE_VERSION = "2"
DEFAULT_MODEL = "gpt-5.4"
PROMPT_PATH = AGENT_DIR / "prompts" / "comparison_agent.md"
DEFAULT_ENV_FILE = AGENT_DIR.parents[2] / "configs" / ".env"

_INTERNAL_FIELDS = {
    "evidence_ids",
    "primary_evidence_ids",
    "secondary_evidence_ids",
    "source_evidence_ids",
    "source_paths",
    "source_path",
    "source_files",
    "evidence_map_path",
    "verification_report_path",
    "raw_content",
    "usage",
}


@dataclass(frozen=True)
class ComparisonAgentPaths:
    """Artifacts written by the peer comparison analysis stage."""

    context_json: Path
    output_json: Path
    report_json: Path
    cache_json: Path


def run_comparison_agent(
    *,
    target_company_name: str,
    peer_company_name: str,
    target_financial_path: Path,
    target_news_path: Path,
    target_yfinance_path: Path,
    peer_financial_path: Path,
    peer_news_path: Path,
    peer_yfinance_path: Path,
    pairwise_dataset_path: Path,
    output_dir: Path,
    llm_model: str = "auto",
    llm_timeout: int = 120,
    env_file: Path | None = DEFAULT_ENV_FILE,
    included_domains: tuple[str, ...] = ("financial", "news", "yfinance"),
) -> ComparisonAgentPaths:
    """Run one comparison LLM call and persist its evidence-linked result."""

    if env_file:
        _load_env_file(env_file)
    model = _resolve_model(llm_model)
    source_paths = {
        "target_financial": _resolved_file(target_financial_path, "target Financial report"),
        "target_news": _resolved_file(target_news_path, "target News report"),
        "target_market": _resolved_file(target_yfinance_path, "target YFinance report"),
        "peer_financial": _resolved_file(peer_financial_path, "peer Financial report"),
        "peer_news": _resolved_file(peer_news_path, "peer News report"),
        "peer_market": _resolved_file(peer_yfinance_path, "peer YFinance report"),
        "pairwise_dataset": _resolved_file(pairwise_dataset_path, "pairwise comparison dataset"),
    }
    sources = {key: _load_json(path) for key, path in source_paths.items()}
    context = build_comparison_context(
        target_company_name=target_company_name,
        peer_company_name=peer_company_name,
        target_financial=sources["target_financial"],
        target_news=sources["target_news"],
        target_yfinance=sources["target_market"],
        peer_financial=sources["peer_financial"],
        peer_news=sources["peer_news"],
        peer_yfinance=sources["peer_market"],
        pairwise_dataset=sources["pairwise_dataset"],
        included_domains=included_domains,
    )
    destination = Path(output_dir).expanduser().resolve()
    destination.mkdir(parents=True, exist_ok=True)
    paths = ComparisonAgentPaths(
        context_json=destination / "peer_comparison_context.json",
        output_json=destination / "peer_comparison_output.json",
        report_json=destination / "peer_comparison_report.json",
        cache_json=destination / "peer_comparison_cache.json",
    )
    _write_json(paths.context_json, context)

    prompt = PROMPT_PATH.read_text(encoding="utf-8")
    fingerprint = _fingerprint(context=context, prompt=prompt, model=model)
    output = _load_cached_output(paths.output_json, paths.cache_json, fingerprint)
    if output is None:
        output = call_comparison_llm(
            context=context,
            prompt=prompt,
            model=model,
            timeout_seconds=llm_timeout,
        )
    _require_output_contract(output)
    report = build_comparison_report(
        output,
        context=context,
        source_paths=source_paths,
    )
    _write_json(paths.output_json, output)
    _write_json(
        paths.cache_json,
        {
            "fingerprint": fingerprint,
            "comparison_version": OUTPUT_VERSION,
            "model": model,
        },
    )
    _write_json(paths.report_json, report)
    return paths


def build_comparison_context(
    *,
    target_company_name: str,
    peer_company_name: str,
    target_financial: dict[str, Any],
    target_news: dict[str, Any],
    target_yfinance: dict[str, Any],
    peer_financial: dict[str, Any],
    peer_news: dict[str, Any],
    peer_yfinance: dict[str, Any],
    pairwise_dataset: dict[str, Any],
    included_domains: tuple[str, ...] = ("financial", "news", "yfinance"),
) -> dict[str, Any]:
    """Build neutral evidence units from both complete lower-agent handoffs."""

    allowed_domains = set(included_domains)
    unknown_domains = allowed_domains - {"financial", "news", "yfinance"}
    if unknown_domains:
        raise ValueError(f"Unknown comparison domains: {sorted(unknown_domains)}")
    cards: dict[str, dict[str, Any]] = {}
    for role, company_name, financial, news, market in (
        (
            "target",
            target_company_name,
            target_financial,
            target_news,
            target_yfinance,
        ),
        (
            "peer",
            peer_company_name,
            peer_financial,
            peer_news,
            peer_yfinance,
        ),
    ):
        if "financial" in allowed_domains:
            cards[f"{role}.financial.analysis"] = _basis_card(
                card_key=f"{role}.financial.analysis",
                label=f"{company_name} 재무 분석",
                company_scope=role,
                domain="financial",
                observation={
                    "main_view": financial.get("main_view"),
                    "financial_statement_view": financial.get("financial_statement_view"),
                    "detailed_analysis": financial.get("detailed_analysis"),
                    "cross_domain_assessments": financial.get("secondary_context_assessment"),
                },
            )
        news_output = _dict(news.get("output")) or news
        if "news" in allowed_domains:
            cards[f"{role}.news.analysis"] = _basis_card(
                card_key=f"{role}.news.analysis",
                label=f"{company_name} 뉴스 분석",
                company_scope=role,
                domain="news",
                observation={
                    "analysis_blocks": news_output.get("analysis_blocks"),
                    "cross_domain_assessments": news_output.get("secondary_context_assessment"),
                },
            )
        if "yfinance" in allowed_domains:
            cards[f"{role}.market.analysis"] = _basis_card(
                card_key=f"{role}.market.analysis",
                label=f"{company_name} 시장 분석",
                company_scope=role,
                domain="market",
                observation={
                    "main_view": market.get("main_view"),
                    "time_horizon_view": market.get("time_horizon_view"),
                    "cross_domain_assessments": market.get("secondary_context_assessment"),
                },
            )

    rows = [row for row in pairwise_dataset.get("metrics") or [] if isinstance(row, dict)]
    for domain, field, label in (
        ("financial", "financial_metrics", "대상기업·비교기업 재무지표"),
        ("market", "market_metrics", "대상기업·비교기업 시장지표"),
        ("valuation", "valuation_metrics", "대상기업·비교기업 가치평가지표"),
    ):
        source_domain = "yfinance" if domain in {"market", "valuation"} else domain
        if source_domain not in allowed_domains:
            continue
        cards[f"pair.{domain}.metrics"] = _basis_card(
            card_key=f"pair.{domain}.metrics",
            label=label,
            company_scope="pair",
            domain=domain,
            observation={
                "companies": [
                    {
                        "company_name": row.get("company_name"),
                        "peer_group": row.get("peer_group"),
                        "as_of_date": row.get("as_of_date"),
                        field: row.get(field) or {},
                        "data_quality": row.get("data_quality") or {},
                    }
                    for row in rows
                ]
            },
        )

    context = {
        "context_version": CONTEXT_VERSION,
        "target_company": target_company_name,
        "peer_company": peer_company_name,
        "comparison_scope": "selected_domestic_peer",
        "included_domains": list(included_domains),
        "basis_cards": cards,
        "comparison_limits": _clean_value(pairwise_dataset.get("comparison_limits") or []),
    }
    _require_context_contract(context)
    return context


def comparison_response_format(context: dict[str, Any]) -> dict[str, Any]:
    """Return the strict transport schema for the comparison LLM."""

    card_keys = sorted(_dict(context.get("basis_cards")))
    basis_use = _strict_object(
        {
            "card_key": {"type": "string", "enum": card_keys},
            "usage_reason": _nonempty_string_schema(),
        }
    )
    point = _strict_object(
        {
            "topic": _nonempty_string_schema(),
            "assessment": {
                "type": "string",
                "enum": [
                    "target_relative_strength",
                    "target_relative_weakness",
                    "mixed",
                    "common_factor",
                ],
            },
            "finding": _nonempty_string_schema(),
            "target_implication": _nonempty_string_schema(),
            "basis": {
                "type": "array",
                "items": basis_use,
                "minItems": 1,
                "maxItems": len(card_keys),
            },
        }
    )
    schema = _strict_object(
        {
            "comparison_version": {"type": "string", "enum": [OUTPUT_VERSION]},
            "comparison_brief": _nonempty_string_schema(),
            "comparison_brief_basis": {
                "type": "array",
                "items": basis_use,
                "minItems": 1,
                "maxItems": len(card_keys),
            },
            "comparison_points": {
                "type": "array",
                "items": point,
                "minItems": 1,
                "maxItems": 8,
            },
            "comparison_limitations": {
                "type": "array",
                "items": _nonempty_string_schema(),
                "maxItems": 6,
            },
        }
    )
    return {
        "type": "json_schema",
        "json_schema": {
            "name": "peer_comparison_analysis_v1",
            "strict": True,
            "schema": schema,
        },
    }


def call_comparison_llm(
    *,
    context: dict[str, Any],
    prompt: str,
    model: str,
    timeout_seconds: int,
) -> dict[str, Any]:
    """Make one evidence-linked comparison call."""

    api_key = os.getenv("OPENAI_API_KEY", "").strip()
    if not api_key:
        raise RuntimeError("OPENAI_API_KEY is required for the peer comparison analysis.")
    request_payload: dict[str, Any] = {
        "model": model,
        "messages": [
            {
                "role": "system",
                "content": (
                    "당신은 한국 상장사의 대상기업과 선정 비교기업을 함께 분석하는 비교 에이전트입니다. "
                    "입력된 하위 에이전트 결과와 수치 자료 안에서만 판단하고 한국어 분석을 JSON으로 반환하세요."
                ),
            },
            {
                "role": "user",
                "content": f"{prompt}\n\n입력 JSON:\n{compact_json(context)}",
            },
        ],
        "response_format": comparison_response_format(context),
    }
    if not _uses_max_completion_tokens(model):
        request_payload["temperature"] = 0.2
    client = OpenAI(api_key=api_key, timeout=timeout_seconds)

    def invoke() -> Any:
        kwargs = deepcopy(request_payload)
        return client.chat.completions.create(**kwargs)

    response = execute_with_telemetry(
        invoke,
        request_payload=request_payload,
        model=model,
        step="competitor:comparison_analysis",
        usage_getter=lambda result: getattr(result, "usage", None),
        max_attempts=max(0, int(os.getenv("LLM_TRANSPORT_RETRIES", "0"))) + 1,
        retry_predicate=is_transient_transport_error,
    )
    content = response.choices[0].message.content or ""
    try:
        payload = json.loads(content)
    except json.JSONDecodeError as exc:
        raise ValueError("Peer comparison LLM response is not valid JSON.") from exc
    if not isinstance(payload, dict):
        raise ValueError("Peer comparison LLM response must be a JSON object.")
    return payload


def build_comparison_report(
    output: dict[str, Any],
    *,
    context: dict[str, Any],
    source_paths: dict[str, Path],
) -> dict[str, Any]:
    """Add the selected evidence records without changing the LLM judgment."""

    cards = _dict(context.get("basis_cards"))
    usage_reasons: dict[str, list[str]] = {}
    for basis in _dict_list(output.get("comparison_brief_basis")):
        key = str(basis.get("card_key") or "")
        reason = str(basis.get("usage_reason") or "").strip()
        if key in cards and reason:
            usage_reasons.setdefault(key, [])
            if reason not in usage_reasons[key]:
                usage_reasons[key].append(reason)
    for point in output.get("comparison_points") or []:
        for basis in _dict_list(_dict(point).get("basis")):
            key = str(basis.get("card_key") or "")
            reason = str(basis.get("usage_reason") or "").strip()
            if key in cards and reason:
                usage_reasons.setdefault(key, [])
                if reason not in usage_reasons[key]:
                    usage_reasons[key].append(reason)
    selected_cards = [
        {
            **deepcopy(cards[key]),
            "usage_reasons": reasons,
        }
        for key, reasons in usage_reasons.items()
    ]
    return {
        **deepcopy(output),
        "target_company": context.get("target_company"),
        "peer_company": context.get("peer_company"),
        "comparison_scope": context.get("comparison_scope"),
        "selected_basis_cards": selected_cards,
        "source_files": {key: str(path) for key, path in source_paths.items()},
    }


def _basis_card(
    *,
    card_key: str,
    label: str,
    company_scope: str,
    domain: str,
    observation: dict[str, Any],
) -> dict[str, Any]:
    return {
        "card_key": card_key,
        "label": label,
        "company_scope": company_scope,
        "domain": domain,
        "observation": _clean_value(observation),
    }


def _clean_value(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            str(key): _clean_value(child)
            for key, child in value.items()
            if str(key) not in _INTERNAL_FIELDS
            and not str(key).endswith("_path")
            and not str(key).endswith("_paths")
            and not str(key).endswith("_ids")
        }
    if isinstance(value, list):
        return [_clean_value(child) for child in value]
    return value


def _require_context_contract(context: dict[str, Any]) -> None:
    if context.get("context_version") != CONTEXT_VERSION:
        raise ValueError(f"context_version must be {CONTEXT_VERSION}.")
    if not str(context.get("target_company") or "").strip():
        raise ValueError("target_company is required.")
    if not str(context.get("peer_company") or "").strip():
        raise ValueError("peer_company is required.")
    cards = _dict(context.get("basis_cards"))
    if not cards:
        raise ValueError("Peer comparison context requires at least one basis card.")
    if any(key != card.get("card_key") for key, card in cards.items()):
        raise ValueError("Peer comparison context contains a mismatched basis card key.")


def _require_output_contract(output: dict[str, Any]) -> None:
    if output.get("comparison_version") != OUTPUT_VERSION:
        raise ValueError(f"comparison_version must be {OUTPUT_VERSION}.")
    if not str(output.get("comparison_brief") or "").strip():
        raise ValueError("comparison_brief is required.")
    points = output.get("comparison_points")
    if not isinstance(points, list) or not points:
        raise ValueError("comparison_points requires at least one item.")


def _load_cached_output(output_path: Path, cache_path: Path, fingerprint: str) -> dict[str, Any] | None:
    if not output_path.is_file() or not cache_path.is_file():
        return None
    cache = _load_json(cache_path)
    if cache.get("fingerprint") != fingerprint:
        return None
    output = _load_json(output_path)
    return output if output.get("comparison_version") == OUTPUT_VERSION else None


def _fingerprint(*, context: dict[str, Any], prompt: str, model: str) -> str:
    payload = {
        "context": context,
        "prompt": prompt,
        "model": model,
        "version": OUTPUT_VERSION,
        "cache_version": CACHE_VERSION,
    }
    return hashlib.sha256(compact_json(payload, sort_keys=True).encode("utf-8")).hexdigest()


def _resolve_model(model: str) -> str:
    return model if model and model != "auto" else os.getenv("OPENAI_MODEL", DEFAULT_MODEL)


def _uses_max_completion_tokens(model: str) -> bool:
    return model.startswith(("gpt-5", "o1", "o3", "o4"))


def _resolved_file(path: Path, label: str) -> Path:
    resolved = Path(path).expanduser().resolve()
    if not resolved.is_file():
        raise FileNotFoundError(f"Missing {label}: {resolved}")
    return resolved


def _load_json(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"Expected a JSON object: {path}")
    return payload


def _write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    temporary_path = Path(temporary)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            handle.write(json.dumps(payload, ensure_ascii=False, indent=2) + "\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary_path, path)
    finally:
        if temporary_path.exists():
            temporary_path.unlink()


def _load_env_file(path: Path) -> None:
    resolved = Path(path).expanduser().resolve()
    if not resolved.is_file():
        return
    for line in resolved.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        key, value = stripped.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key:
            os.environ.setdefault(key, value)


def _strict_object(properties: dict[str, Any]) -> dict[str, Any]:
    return {
        "type": "object",
        "properties": properties,
        "required": list(properties),
        "additionalProperties": False,
    }


def _nonempty_string_schema() -> dict[str, Any]:
    return {"type": "string", "minLength": 1}


def _dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _dict_list(value: Any) -> list[dict[str, Any]]:
    return [item for item in value or [] if isinstance(item, dict)]


__all__ = [
    "CONTEXT_VERSION",
    "OUTPUT_VERSION",
    "ComparisonAgentPaths",
    "build_comparison_context",
    "build_comparison_report",
    "call_comparison_llm",
    "comparison_response_format",
    "run_comparison_agent",
]
