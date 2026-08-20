#!/usr/bin/env python3
"""Evidence-admissibility validation for YFinance Agent output."""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
import math
import os
import time
from datetime import date
from pathlib import Path
from typing import Any, TypedDict

from dotenv import load_dotenv
from langgraph.graph import END, StateGraph
from openai import OpenAI
from shared.evidence_contracts import (
    canonical_evidence_id,
    validate_evidence_catalog as validate_evidence_catalog_contract,
    validate_secondary_context_assessments,
)
from shared.llm_clients import compact_json, execute_with_telemetry, partition_by_prompt_budget


PROJECT_ROOT = Path(__file__).resolve().parents[4]
OUTPUT_DIR = PROJECT_ROOT / "Output_total" / "Y_Finance"
DEFAULT_ENV_PATH = PROJECT_ROOT / "configs" / ".env"
if not DEFAULT_ENV_PATH.exists():
    DEFAULT_ENV_PATH = PROJECT_ROOT / ".env"
DEFAULT_OPENAI_MODEL = "gpt-5.4-mini"
INPUT_PATH = str(OUTPUT_DIR / "yfinance_analyst_report.json")
OUTPUT_PATH = str(OUTPUT_DIR / "sy_verified_yfinance_report.json")
STRATEGY_REPORT_FILENAME = "yfinance_verified_report.json"
SEMANTIC_BATCH_TARGET_TOKENS = 100_000
EVIDENCE_USE_VALUES = {"strong", "context_only", "exclude"}
GRAPH_FLOW = [
    "Input Specialist Output",
    "Claim and Evidence Extraction",
    "Deterministic Market Checks",
    "Semantic Batch Evaluation",
    "Admissibility Ledger Output",
]


class SYState(TypedDict, total=False):
    input_path: str
    output_path: str
    base_dir: str
    env_file: str
    llm_model: str
    strategy_report_path: str
    report: dict[str, Any]
    market_records: list[dict[str, Any]]
    claims: list[dict[str, Any]]
    evidence_catalog: dict[str, dict[str, Any]]
    secondary_context_assessments: list[dict[str, Any]]
    secondary_context_catalog: dict[str, dict[str, Any]]
    deterministic_checks: dict[str, dict[str, Any]]
    evaluations: list[dict[str, Any]]
    llm_calls: list[dict[str, Any]]
    verified_report: dict[str, Any]
    strategy_report: dict[str, Any]
    started_at: float


def load_json(path: str) -> dict[str, Any]:
    file_path = Path(path)
    if not file_path.exists():
        raise FileNotFoundError(f"File not found: {path}")
    payload = json.loads(file_path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"JSON root must be an object: {path}")
    return payload


def save_json(data: Any, path: str) -> None:
    file_path = Path(path)
    file_path.parent.mkdir(parents=True, exist_ok=True)
    file_path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def extract_json(text: str) -> dict[str, Any]:
    cleaned = str(text or "").strip().replace("```json", "").replace("```", "").strip()
    try:
        payload = json.loads(cleaned)
        return payload if isinstance(payload, dict) else {}
    except json.JSONDecodeError:
        start = cleaned.find("{")
        end = cleaned.rfind("}")
        if start < 0 or end <= start:
            return {}
        try:
            payload = json.loads(cleaned[start : end + 1])
        except json.JSONDecodeError:
            return {}
        return payload if isinstance(payload, dict) else {}


def resolve_model(env_file: str | None, model: str | None) -> str:
    load_dotenv(Path(env_file).expanduser() if env_file else DEFAULT_ENV_PATH)
    if not os.getenv("OPENAI_API_KEY"):
        raise ValueError("OPENAI_API_KEY is not configured.")
    return model or os.getenv("OPENAI_MODEL", DEFAULT_OPENAI_MODEL)


def load_report_node(state: SYState) -> SYState:
    state["report"] = load_json(state.get("input_path") or INPUT_PATH)
    base_dir = Path(state.get("base_dir") or Path(state.get("input_path") or INPUT_PATH).parent)
    state["market_records"] = load_market_records(base_dir)
    return state


def load_market_records(base_dir: Path) -> list[dict[str, Any]]:
    """Load the deterministic market dataset used to validate YFinance prose."""

    for filename in ("market_full_dataset.json", "market_summary.json"):
        path = base_dir / filename
        if not path.exists():
            continue
        payload = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(payload, list) and all(isinstance(item, dict) for item in payload):
            return payload
    raise FileNotFoundError(f"Market source dataset not found under {base_dir}")


def extract_claims_node(state: SYState) -> SYState:
    report = state["report"]
    claims: list[dict[str, Any]] = []
    main_view = report.get("main_view") or {}
    if main_view.get("direction"):
        claims.append(_claim("main_direction", "main_view.direction", main_view["direction"]))
    time_view = report.get("time_horizon_view") or {}
    for horizon in ("short_term", "mid_term", "long_term"):
        block = time_view.get(horizon) or {}
        if block.get("stance"):
            claims.append(
                _claim(
                    f"{horizon}_stance",
                    f"time_horizon_view.{horizon}",
                    block.get("stance"),
                )
            )
    for section_name, block in (report.get("detailed_analysis") or {}).items():
        if isinstance(block, dict) and block.get("interpretation"):
            claims.append(
                _claim(
                    f"detailed_{section_name}",
                    f"detailed_analysis.{section_name}",
                    block.get("interpretation"),
                )
            )
    state["claims"] = claims
    state["evidence_catalog"] = build_evidence_catalog(
        report,
        market_records=state["market_records"],
    )
    secondary_catalog = report.get("secondary_context_catalog") or {}
    if not isinstance(secondary_catalog, dict):
        raise ValueError("secondary_context_catalog must be an object.")
    validate_evidence_catalog_contract(
        secondary_catalog,
        allowed_domains={"financial", "news"},
    )
    state["secondary_context_catalog"] = copy.deepcopy(secondary_catalog)
    state["secondary_context_assessments"] = validate_secondary_context_assessments(
        report.get("secondary_context_assessment") or [],
        primary_evidence_ids=state["evidence_catalog"].keys(),
        secondary_catalog=secondary_catalog,
        allowed_source_domains={"financial", "news"},
    )
    for claim in claims:
        claim["candidate_evidence_ids"] = evidence_ids_for_claim(claim, state["evidence_catalog"])
    return state


def _claim(claim_id: str, section: str, claim: Any) -> dict[str, Any]:
    return {
        "claim_id": claim_id,
        "section": section,
        "claim": str(claim or ""),
        "required_evidence_domains": ["market"],
    }


def build_evidence_catalog(
    report: dict[str, Any],
    *,
    market_records: list[dict[str, Any]],
) -> dict[str, dict[str, Any]]:
    """Build evidence only from raw market rows and deterministic calculations."""

    selected_raw = str(report.get("selected_date") or report.get("as_of_date") or "")
    selected = date.fromisoformat(selected_raw)
    dated: list[tuple[date, dict[str, Any]]] = []
    for row in market_records:
        try:
            row_date = date.fromisoformat(str(row.get("date") or ""))
        except ValueError:
            continue
        if row_date < selected:
            dated.append((row_date, row))
    if not dated:
        return {}
    dated.sort(key=lambda item: item[0])
    first_date, first = dated[0]
    latest_date, latest = dated[-1]
    stock_price_column = (
        "stock_adjusted_close"
        if any(row.get("stock_adjusted_close") is not None for _, row in dated)
        else "stock_close"
    )
    catalog: dict[str, dict[str, Any]] = {}

    for metric, value in latest.items():
        if metric == "date" or isinstance(value, bool) or not isinstance(value, (int, float)):
            continue
        add_market_evidence(
            catalog,
            metric=metric,
            value=value,
            source_ref=f"market_full_dataset.latest.{metric}",
            source_date=latest_date.isoformat(),
            origin_type="raw_source",
        )

    for metric, column in (
        ("stock_period_return", stock_price_column),
        ("kospi_period_return", "kospi_close"),
        ("fx_period_return", "fx_close"),
    ):
        value = safe_return(first.get(column), latest.get(column))
        if value is not None:
            add_market_evidence(
                catalog,
                metric=metric,
                value=value,
                source_ref=f"market_full_dataset.derived.{metric}",
                source_date=latest_date.isoformat(),
                origin_type="deterministic_derived",
                period=f"{first_date.isoformat()}..{latest_date.isoformat()}",
            )
    stock_return = evidence_value_by_metric(catalog, "stock_period_return")
    kospi_return = evidence_value_by_metric(catalog, "kospi_period_return")
    if stock_return is not None and kospi_return is not None:
        add_market_evidence(
            catalog,
            metric="stock_period_excess_return",
            value=stock_return - kospi_return,
            source_ref="market_full_dataset.derived.stock_period_excess_return",
            source_date=latest_date.isoformat(),
            origin_type="deterministic_derived",
            period=f"{first_date.isoformat()}..{latest_date.isoformat()}",
        )
    drawdown = max_drawdown([row.get(stock_price_column) for _, row in dated])
    if drawdown is not None:
        add_market_evidence(
            catalog,
            metric="stock_max_drawdown",
            value=drawdown,
            source_ref="market_full_dataset.derived.stock_max_drawdown",
            source_date=latest_date.isoformat(),
            origin_type="deterministic_derived",
            period=f"{first_date.isoformat()}..{latest_date.isoformat()}",
        )
    validate_evidence_catalog(catalog)
    return catalog


def add_market_evidence(
    catalog: dict[str, dict[str, Any]],
    *,
    metric: str,
    value: float,
    source_ref: str,
    source_date: str,
    origin_type: str,
    period: str = "",
) -> None:
    if not math.isfinite(float(value)):
        return
    evidence_id = canonical_evidence_id("market", metric)
    catalog[evidence_id] = {
        "evidence_id": evidence_id,
        "domain": "market",
        "origin_type": origin_type,
        "source_ref": source_ref,
        "source_date": source_date,
        "period": period,
        "metric": metric,
        "value": value,
        "unit": market_metric_unit(metric),
        "source_domains": ["market"],
    }


def market_metric_unit(metric: str) -> str:
    if metric in {"stock_close", "kospi_close", "fx_close"}:
        return "price"
    if "rsi" in metric or "ratio" in metric:
        return "index"
    if any(token in metric for token in ("return", "strength", "volatility", "drawdown", "to_ma", "obv", "bb_width")):
        return "ratio"
    return "number"


def safe_return(first: Any, latest: Any) -> float | None:
    if not isinstance(first, (int, float)) or not isinstance(latest, (int, float)) or first == 0:
        return None
    value = float(latest) / float(first) - 1.0
    return value if math.isfinite(value) else None


def max_drawdown(values: list[Any]) -> float | None:
    peak: float | None = None
    worst = 0.0
    found = False
    for raw in values:
        if not isinstance(raw, (int, float)) or not math.isfinite(float(raw)):
            continue
        value = float(raw)
        peak = value if peak is None else max(peak, value)
        if peak:
            worst = min(worst, value / peak - 1.0)
        found = True
    return worst if found else None


def evidence_value_by_metric(catalog: dict[str, dict[str, Any]], metric: str) -> float | None:
    for evidence in catalog.values():
        if evidence.get("metric") == metric and isinstance(evidence.get("value"), (int, float)):
            return float(evidence["value"])
    return None


def validate_evidence_catalog(catalog: dict[str, dict[str, Any]]) -> None:
    validate_evidence_catalog_contract(catalog, allowed_domains={"market"})


def evidence_ids_for_claim(
    claim: dict[str, Any],
    catalog: dict[str, dict[str, Any]],
) -> list[str]:
    section = str(claim.get("section") or "")
    if section.startswith("time_horizon_view.short_term"):
        metrics = {"stock_return_5d", "stock_close_to_ma20", "stock_rsi_14", "stock_macd_hist", "stock_macd_hist_change_1d", "stock_volume_ratio_20", "stock_excess_return_5d"}
    elif section.startswith("time_horizon_view.mid_term"):
        metrics = {"stock_return_20d", "stock_return_60d", "stock_close_to_ma20", "stock_close_to_ma60", "stock_excess_return_20d", "stock_relative_strength_60"}
    elif section.startswith("time_horizon_view.long_term"):
        metrics = {"stock_return_60d", "stock_relative_strength_60", "stock_period_return", "stock_period_excess_return", "stock_max_drawdown"}
    elif "price_trend" in section:
        metrics = {"stock_close", "stock_period_return", "stock_close_to_ma20", "stock_close_to_ma60", "stock_ma5_to_ma20"}
    elif "momentum" in section:
        metrics = {"stock_rsi_14", "stock_macd_hist", "stock_macd_hist_change_1d"}
    elif "volatility_and_volume" in section:
        metrics = {"stock_bb_width_20", "stock_volatility_20", "stock_volume_ratio_20", "stock_obv_trend"}
    elif "market_relative" in section:
        metrics = {"stock_excess_return_5d", "stock_excess_return_20d", "stock_relative_strength_60", "kospi_return_5d", "kospi_return_20d", "stock_period_excess_return"}
    elif "fx_context" in section:
        metrics = {"fx_return_5d", "fx_return_20d", "fx_close_to_ma20", "fx_rsi_14", "fx_volatility_20", "fx_period_return"}
    else:
        metrics = {
            "stock_period_return", "stock_return_20d", "stock_return_60d", "stock_close_to_ma20",
            "stock_close_to_ma60", "stock_rsi_14", "stock_volume_ratio_20",
            "stock_excess_return_20d", "stock_relative_strength_60",
        }
    return [
        evidence_id
        for evidence_id, evidence in catalog.items()
        if evidence.get("metric") in metrics
    ]


def deterministic_checks_node(state: SYState) -> SYState:
    report = state["report"]
    selected_date = str(report.get("selected_date") or report.get("as_of_date") or "")
    date_valid = report_dates_valid(report, selected_date)
    numeric_values_valid = finite_nested_numbers(state.get("market_records", []))
    catalog_available = bool(state["evidence_catalog"])
    state["deterministic_checks"] = {
        claim["claim_id"]: {
            "source_path_exists": path_exists(report, claim["section"]),
            "catalog_available": catalog_available,
            "date_valid": date_valid,
            "numeric_values_valid": numeric_values_valid,
            "candidate_evidence_ids": list(claim.get("candidate_evidence_ids", [])),
            "blockers": [
                reason
                for reason, failed in (
                    ("missing_source_section", not path_exists(report, claim["section"])),
                    ("missing_evidence_catalog", not catalog_available),
                    ("missing_candidate_evidence", not claim.get("candidate_evidence_ids")),
                    ("future_or_invalid_market_date", not date_valid),
                    ("invalid_numeric_value", not numeric_values_valid),
                )
                if failed
            ],
        }
        for claim in state["claims"]
    }
    return state


def report_dates_valid(report: dict[str, Any], as_of_date: str) -> bool:
    if not as_of_date:
        return False
    try:
        selected = date.fromisoformat(as_of_date)
    except ValueError:
        return False
    checked_keys = {
        "market_date",
        "valuation_date",
        "price_date",
        "price_as_of",
        "valuation_as_of",
        "period_end",
    }

    def dates_are_valid(value: Any) -> bool:
        if isinstance(value, dict):
            for key, child in value.items():
                if key in checked_keys and child:
                    try:
                        if date.fromisoformat(str(child)) >= selected:
                            return False
                    except ValueError:
                        return False
                if not dates_are_valid(child):
                    return False
        elif isinstance(value, list):
            return all(dates_are_valid(child) for child in value)
        return True

    return dates_are_valid(report)


def finite_nested_numbers(value: Any) -> bool:
    if isinstance(value, bool) or value is None or isinstance(value, str):
        return True
    if isinstance(value, (int, float)):
        return math.isfinite(float(value))
    if isinstance(value, dict):
        return all(finite_nested_numbers(child) for child in value.values())
    if isinstance(value, list):
        return all(finite_nested_numbers(child) for child in value)
    return True


def path_exists(value: Any, path: str) -> bool:
    normalized = path.replace("[", ".").replace("]", "")
    current = value
    for token in (part for part in normalized.split(".") if part):
        if isinstance(current, dict) and token in current:
            current = current[token]
        elif isinstance(current, list) and token.isdigit() and int(token) < len(current):
            current = current[int(token)]
        else:
            return False
    return True


def semantic_evaluation_node(state: SYState) -> SYState:
    eligible = [
        claim
        for claim in state["claims"]
        if not state["deterministic_checks"][claim["claim_id"]].get("blockers")
    ]
    chunks = partition_by_prompt_budget(
        eligible,
        build_request=lambda chunk: build_semantic_request(state, chunk),
        model=state["llm_model"],
        target_input_tokens=SEMANTIC_BATCH_TARGET_TOKENS,
    ) if eligible else []
    evaluations_by_id: dict[str, dict[str, Any]] = {}
    for index, chunk in enumerate(chunks, start=1):
        request_payload = build_semantic_request(state, chunk)
        parsed, usage, elapsed = call_openai_json(
            request_payload=request_payload,
            model=state["llm_model"],
            step=f"yfinance_sy:semantic_batch:{index}",
        )
        evaluations = parsed.get("evaluations_by_claim_id")
        if not isinstance(evaluations, dict):
            raise RuntimeError("YFinance SY response must contain evaluations_by_claim_id.")
        for claim_id, evaluation in evaluations.items():
            if isinstance(evaluation, dict):
                evaluations_by_id[str(claim_id)] = evaluation
        state.setdefault("llm_calls", []).append(
            {"node": f"Semantic Batch Evaluation:{index}", "model": state["llm_model"], "usage": usage, "elapsed_seconds": round(elapsed, 3)}
        )
    normalized = []
    for claim in state["claims"]:
        claim_id = claim["claim_id"]
        checks = state["deterministic_checks"][claim_id]
        if checks.get("blockers"):
            evaluation = {
                "claim_id": claim_id,
                "evidence_use": "exclude",
                "reason_ko": "결정론적 시장 데이터 검사에서 필수 조건을 충족하지 못했다.",
                "evidence_ids": [],
                "limitations": checks["blockers"],
            }
        else:
            evaluation = evaluations_by_id.get(claim_id)
            if not isinstance(evaluation, dict):
                raise RuntimeError(f"YFinance SY response missing claim id: {claim_id}")
        normalized.append(normalize_evaluation(claim, checks, evaluation, state["evidence_catalog"]))
    state["evaluations"] = normalized
    return state


def build_semantic_request(state: SYState, claims: list[dict[str, Any]]) -> dict[str, Any]:
    selected_evidence_ids = {
        evidence_id
        for claim in claims
        for evidence_id in claim.get("candidate_evidence_ids", [])
    }
    evidence_catalog = {
        evidence_id: evidence
        for evidence_id, evidence in state["evidence_catalog"].items()
        if evidence_id in selected_evidence_ids
    }
    return {
        "model": state["llm_model"],
        "temperature": 0.0,
        "response_format": _yfinance_sy_response_format(claims),
        "messages": [
            {
                "role": "system",
                "content": (
                    "당신은 YFinance evidence admissibility evaluator입니다. 보고서를 수정하거나 새 투자 문장을 쓰지 않습니다. "
                    "각 claim이 제공된 원 시장 수치와 결정론적 파생 evidence로 뒷받침되는지만 평가하세요. strong은 필수 도메인이 직접 연결될 때, "
                    "context_only는 일부 근거나 방향성 참고만 가능할 때, exclude는 유효한 근거가 없거나 충돌할 때 사용합니다. "
                    "가격 움직임만으로 펀더멘털을 단정한 주장은 strong으로 두지 마세요. claim별 candidate_evidence_ids 밖의 ID를 사용하지 말고 JSON만 출력하세요."
                ),
            },
            {
                "role": "user",
                "content": compact_json(
                    {
                        "target": {
                            "company": state["report"].get("target_company"),
                            "ticker": state["report"].get("ticker"),
                            "as_of_date": state["report"].get("as_of_date"),
                            "selected_date": state["report"].get("selected_date"),
                        },
                        "claims": [
                            {
                                "claim_id": claim["claim_id"],
                                "section": claim["section"],
                                "claim": claim["claim"],
                                "required_evidence_domains": claim["required_evidence_domains"],
                                "candidate_evidence_ids": claim.get("candidate_evidence_ids", []),
                                "blockers": state["deterministic_checks"][claim["claim_id"]].get("blockers", []),
                            }
                            for claim in claims
                        ],
                        "evidence_catalog": evidence_catalog,
                    }
                ),
            },
        ],
    }


def _yfinance_sy_response_format(claims: list[dict[str, Any]]) -> dict[str, Any]:
    evaluations: dict[str, Any] = {}
    for claim in claims:
        claim_id = str(claim.get("claim_id") or "")
        candidate_ids = list(
            dict.fromkeys(str(value) for value in claim.get("candidate_evidence_ids", []))
        )
        evidence_item: dict[str, Any] = {"type": "string"}
        if candidate_ids:
            evidence_item["enum"] = candidate_ids
        evaluations[claim_id] = {
            "type": "object",
            "properties": {
                "evidence_use": {
                    "type": "string",
                    "enum": sorted(EVIDENCE_USE_VALUES),
                },
                "reason_ko": {"type": "string"},
                "evidence_ids": {"type": "array", "items": evidence_item},
                "limitations": {"type": "array", "items": {"type": "string"}},
            },
            "required": ["evidence_use", "reason_ko", "evidence_ids", "limitations"],
            "additionalProperties": False,
        }
    return {
        "type": "json_schema",
        "json_schema": {
            "name": "yfinance_sy_admissibility",
            "strict": True,
            "schema": {
                "type": "object",
                "properties": {
                    "evaluations_by_claim_id": {
                        "type": "object",
                        "properties": evaluations,
                        "required": list(evaluations),
                        "additionalProperties": False,
                    }
                },
                "required": ["evaluations_by_claim_id"],
                "additionalProperties": False,
            },
        },
    }


def call_openai_json(*, request_payload: dict[str, Any], model: str, step: str) -> tuple[dict[str, Any], Any, float]:
    client = OpenAI()
    started = time.monotonic()
    response = execute_with_telemetry(
        lambda: client.chat.completions.create(**request_payload),
        request_payload=request_payload,
        model=model,
        step=step,
        usage_getter=lambda result: getattr(result, "usage", None),
    )
    elapsed = time.monotonic() - started
    content = response.choices[0].message.content or ""
    parsed = extract_json(content)
    usage_obj = response.usage
    usage = usage_obj.model_dump() if hasattr(usage_obj, "model_dump") else usage_obj
    return parsed, usage, elapsed


def normalize_evaluation(claim: dict[str, Any], checks: dict[str, Any], evaluation: dict[str, Any], catalog: dict[str, dict[str, Any]]) -> dict[str, Any]:
    evidence_use = str(evaluation.get("evidence_use") or "")
    if evidence_use not in EVIDENCE_USE_VALUES:
        raise RuntimeError(f"Invalid YFinance SY evidence_use for {claim['claim_id']}: {evidence_use}")
    requested = evaluation.get("evidence_ids") if isinstance(evaluation.get("evidence_ids"), list) else []
    candidates = set(claim.get("candidate_evidence_ids", []))
    evidence_ids = list(
        dict.fromkeys(
            str(value)
            for value in requested
            if str(value) in catalog and str(value) in candidates
        )
    )
    coverage = sorted(
        {
            domain
            for evidence_id in evidence_ids
            for domain in catalog[evidence_id].get("source_domains", [])
        }
    )
    missing = [domain for domain in claim["required_evidence_domains"] if domain not in coverage]
    if not evidence_ids:
        evidence_use = "exclude"
    elif missing and evidence_use == "strong":
        evidence_use = "context_only"
    support = {"strong": "supported", "context_only": "weakly_supported", "exclude": "unsupported"}[evidence_use]
    decision = {"strong": "keep", "context_only": "revise", "exclude": "remove"}[evidence_use]
    limitations = evaluation.get("limitations") if isinstance(evaluation.get("limitations"), list) else []
    return {
        "claim_id": claim["claim_id"],
        "section": claim["section"],
        "claim": claim["claim"],
        "required_evidence_domains": claim["required_evidence_domains"],
        "evidence_ids": evidence_ids,
        "evidence_used": evidence_ids,
        "evidence_domain_coverage": coverage,
        "missing_evidence_domains": missing,
        "deterministic_checks": copy.deepcopy(checks),
        "evidence_use": evidence_use,
        "support_level": support,
        "decision": decision,
        "sy_reason": str(evaluation.get("reason_ko") or ""),
        "limitations": [str(item) for item in limitations if str(item).strip()],
    }


def build_verified_report_node(state: SYState) -> SYState:
    evaluations = state["evaluations"]
    buckets = {
        status: [item for item in evaluations if item.get("evidence_use") == status]
        for status in ("strong", "context_only", "exclude")
    }
    usage = aggregate_llm_usage(state.get("llm_calls", []))
    source_path = Path(state.get("input_path") or INPUT_PATH).expanduser().resolve()
    state["verified_report"] = {
        "validation_version": "4.0",
        "source_agent": state["report"].get("agent_name"),
        "verifier_agent": "SY Agent",
        "target_company": state["report"].get("target_company"),
        "ticker": state["report"].get("ticker"),
        "as_of_date": state["report"].get("as_of_date"),
        "selected_date": state["report"].get("selected_date"),
        "selected_date_policy": state["report"].get("selected_date_policy"),
        "source_report": {
            "path": str(source_path),
            "sha256": hashlib.sha256(source_path.read_bytes()).hexdigest(),
        },
        "verification_mode": "deterministic_checks_plus_semantic_batch",
        "summary": {
            "total_claims": len(evaluations),
            "evidence_use_counts": {key: len(value) for key, value in buckets.items()},
            "llm_call_count": len(state.get("llm_calls", [])),
            "report_rewritten": False,
        },
        "llm_usage": usage,
        "verified_claims": buckets["strong"],
        "context_only_claims": buckets["context_only"],
        "excluded_claims": buckets["exclude"],
        "evidence_catalog": state["evidence_catalog"],
        "secondary_context_assessments": state.get("secondary_context_assessments", []),
        "secondary_context_catalog": state.get("secondary_context_catalog", {}),
        "elapsed_seconds": round(time.monotonic() - state["started_at"], 3),
    }
    return state


def aggregate_llm_usage(calls: list[dict[str, Any]]) -> dict[str, int]:
    total = {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}
    for call in calls:
        usage = call.get("usage") if isinstance(call.get("usage"), dict) else {}
        for key in total:
            total[key] += int(usage.get(key) or 0)
    return {**total, "call_count": len(calls)}


def build_strategy_compatible_verified_report(
    verified_report: dict[str, Any],
    *,
    source_report: dict[str, Any] | None = None,
    validation_report_path: str | None = None,
) -> dict[str, Any]:
    report = copy.deepcopy(source_report or {})
    summary = copy.deepcopy(verified_report.get("summary") or {})
    report["report_status"] = "sy_evidence_admissibility_applied"
    report["verification_summary"] = summary
    report["sy_validation"] = {
        "verifier_agent": "SY Agent",
        "verification_mode": verified_report.get("verification_mode"),
        "validation_report_path": validation_report_path,
        "summary": summary,
        "claim_admissibility": [
            {
                "claim_id": item.get("claim_id"),
                "evidence_use": item.get("evidence_use"),
                "evidence_ids": item.get("evidence_ids", []),
            }
            for bucket in ("verified_claims", "context_only_claims", "excluded_claims")
            for item in verified_report.get(bucket, [])
        ],
        "secondary_context_assessment_count": len(
            verified_report.get("secondary_context_assessments", [])
        ),
    }
    return report


def default_strategy_report_path(output_path: str | Path) -> Path:
    return Path(output_path).expanduser().resolve().with_name(STRATEGY_REPORT_FILENAME)


def save_verified_report_node(state: SYState) -> SYState:
    output_path = Path(state.get("output_path") or OUTPUT_PATH).expanduser().resolve()
    strategy_path = Path(state.get("strategy_report_path") or default_strategy_report_path(output_path)).expanduser().resolve()
    save_json(state["verified_report"], str(output_path))
    strategy_report = build_strategy_compatible_verified_report(
        state["verified_report"],
        source_report=state["report"],
        validation_report_path=str(output_path),
    )
    save_json(strategy_report, str(strategy_path))
    state["strategy_report"] = strategy_report
    state["strategy_report_path"] = str(strategy_path)
    return state


def build_graph():
    graph = StateGraph(SYState)
    graph.add_node("load_report", load_report_node)
    graph.add_node("extract_claims", extract_claims_node)
    graph.add_node("deterministic_checks", deterministic_checks_node)
    graph.add_node("semantic_evaluation", semantic_evaluation_node)
    graph.add_node("build_verified_report", build_verified_report_node)
    graph.add_node("save_verified_report", save_verified_report_node)
    graph.set_entry_point("load_report")
    graph.add_edge("load_report", "extract_claims")
    graph.add_edge("extract_claims", "deterministic_checks")
    graph.add_edge("deterministic_checks", "semantic_evaluation")
    graph.add_edge("semantic_evaluation", "build_verified_report")
    graph.add_edge("build_verified_report", "save_verified_report")
    graph.add_edge("save_verified_report", END)
    return graph.compile()


def main() -> None:
    parser = argparse.ArgumentParser(description="Run YFinance evidence-admissibility validation")
    parser.add_argument("--input", default=INPUT_PATH)
    parser.add_argument("--output", default=OUTPUT_PATH)
    parser.add_argument("--strategy-output", default=None)
    parser.add_argument("--env-file", default=str(DEFAULT_ENV_PATH))
    parser.add_argument("--model", default=None)
    args = parser.parse_args()
    input_path = str(Path(args.input).expanduser().resolve())
    output_path = str(Path(args.output).expanduser().resolve())
    strategy_path = str(Path(args.strategy_output).expanduser().resolve()) if args.strategy_output else str(default_strategy_report_path(output_path))
    env_file = str(Path(args.env_file).expanduser().resolve())
    model = resolve_model(env_file, args.model)
    initial_state: SYState = {
        "input_path": input_path,
        "output_path": output_path,
        "base_dir": str(Path(output_path).parent),
        "env_file": env_file,
        "llm_model": model,
        "strategy_report_path": strategy_path,
        "llm_calls": [],
        "started_at": time.monotonic(),
    }
    result = build_graph().invoke(initial_state)
    print("YFinance SY evidence validation complete")
    print(output_path)
    print(json.dumps(result["verified_report"]["summary"], ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
