"""One integrated Agent over the same three domain preprocessing outputs as FINAL."""

from __future__ import annotations

import copy
import json
import os
import sys
from contextlib import contextmanager
from datetime import date
from pathlib import Path
from typing import Any, Iterable, Iterator

import pandas as pd

from .config import FINAL_SRC
from .utils import load_json, sha256_file, utc_now, write_json


if str(FINAL_SRC) not in sys.path:
    sys.path.insert(0, str(FINAL_SRC))

# YFinance reporting keeps a legacy local import (``from valuation``).
YFINANCE_AGENT_DIR = FINAL_SRC / "Agent_Team" / "YFinance_Agent"
if str(YFINANCE_AGENT_DIR) not in sys.path:
    sys.path.insert(0, str(YFINANCE_AGENT_DIR))

from Agent_Team.Financial_Agent.financial_analysis_agent import (  # noqa: E402
    apply_financial_analysis,
    build_financial_llm_packet,
    build_financial_request,
    financial_analysis_json_schema,
    validate_financial_analysis,
)
from Agent_Team.Financial_Agent.langgraph_flow import (  # noqa: E402
    build_financial_analyst_output,
)
from Agent_Team.News_Agent.analysis_agent import (  # noqa: E402
    AnalysisPaths,
    build_analysis_input_payload,
    build_llm_request as build_news_llm_request,
    _merge_analysis_anchor_evidence_ids,
    _validate_news_analysis_output,
)
from Agent_Team.YFinance_Agent.reporting import (  # noqa: E402
    build_dart_secondary_context,
    build_daily_market_evidence_catalog,
    build_monthly_market_evidence_catalog,
    build_llm_evidence_packet,
    build_market_request,
    validate_market_analysis,
    build_market_primary_evidence_catalog,
    build_market_summary,
    build_monthly_market_table,
    build_news_secondary_context,
    build_valuation_snapshot,
    load_market_dataset,
    yfinance_agent_json_schema,
)
from orchestration.config import RunConfig  # noqa: E402
from shared.domain_llm import domain_request, call_domain_response
from orchestration.manifest import write_financial_runtime_manifest  # noqa: E402
from orchestration.paths import RunPaths  # noqa: E402
from shared.llm_clients import (  # noqa: E402
    measure_request,
    normalize_usage,
)


from . import integrated_report

UNIFIED_PROTOCOL = integrated_report.PROTOCOL


def run_unified_domain_team(
    *,
    run_config: RunConfig,
    destination_paths: RunPaths,
    model: str,
    env_file: Path,
    timeout_seconds: int,
    role: str,
    usage_manifest: Path,
    execution_id: str,
) -> dict[str, Any]:
    """Run one semantic Agent over frozen, pre-Agent domain preprocessing outputs."""

    _load_env(env_file)
    prepared = prepare_unified_inputs(run_config=run_config, paths=destination_paths, model=model)
    semantic_input = _dict(prepared.get("semantic_input"))
    request = build_unified_request(semantic_input, model=model)
    audit_dir = destination_paths.run_dir / "unified_domain_team"
    write_json(audit_dir / "preprocessed_input_bundle.json", prepared)
    write_json(audit_dir / "unified_input.json", semantic_input)
    write_json(audit_dir / "unified_request.json", request)

    telemetry = {
        "LLM_USAGE_MANIFEST": str(usage_manifest),
        "LLM_EXECUTION_ID": execution_id,
        "LLM_RUN_ID": run_config.run_key,
        "LLM_RUN_ROLE": role,
        "LLM_COMPANY_NAME": run_config.company_name,
    }
    with _temporary_environment(telemetry):
        output, usage = _call_openai(request, timeout_seconds=timeout_seconds)
    validate_unified_output(output, semantic_input)
    write_json(audit_dir / "unified_output.json", output)

    adapter_paths = integrated_report.write_report(
        output=output,
        prepared=prepared,
        destination_paths=destination_paths,
        run_config=run_config,
        model=model,
        role=role,
    )
    manifest = {
        "status": "success",
        "protocol": UNIFIED_PROTOCOL,
        "semantic_call_count": 1,
        "analysis_report_count": 1,
        "company_name": run_config.company_name,
        "role": role,
        "selected_date": destination_paths.selected_date,
        "model": model,
        "created_at": utc_now(),
        "usage": usage,
        "preprocessing_parity": prepared["preprocessing_parity"],
        "evidence_counts": prepared["evidence_counts"],
        "input_artifacts": prepared["input_artifacts"],
        "input_policy": {
            "prior_domain_agent_reports_used": False,
            "domain_primary_evidence_separated": True,
            "domain_boundary_packets_preserved": True,
            "secondary_context_preserved_inside_boundary_packets": True,
            "news_articles_reused_from_full": True,
        },
        "downstream_contract": (
            "one integrated company report; comparison and Strategy consume its interpretation once"
        ),
        "outputs": adapter_paths,
    }
    manifest_path = audit_dir / "manifest.json"
    write_json(manifest_path, manifest)
    manifest["manifest_path"] = str(manifest_path)
    return manifest


def prepare_unified_inputs(
    *,
    run_config: RunConfig,
    paths: RunPaths,
    model: str,
) -> dict[str, Any]:
    """Rebuild deterministic packets immediately preceding each domain Agent."""

    financial_factual, financial_packet, financial_evidence = _prepare_financial_input(
        run_config, paths
    )
    news_payload, news_packet, news_evidence, news_evidence_map = _prepare_news_input(
        run_config, paths, model
    )
    market_facts, market_packet, market_evidence = _prepare_market_input(run_config, paths)

    catalogs = {
        "financial": financial_evidence,
        "news": news_evidence,
        "market": market_evidence,
    }
    _validate_separated_catalogs(catalogs)
    semantic_input = {
        "protocol": UNIFIED_PROTOCOL,
        "target": {
            "company_name": run_config.company_name,
            "ticker": run_config.ticker,
            "corp_code": run_config.corp_code,
            "selected_date": run_config.selected_date_iso,
            "information_cutoff_date": run_config.information_cutoff_date_iso,
        },
        "financial": {
            "preprocessed_input": financial_packet,
            "evidence": financial_evidence,
            "evidence_policy": "Only this financial catalog may support financial findings.",
        },
        "news": {
            "preprocessed_input": news_packet,
            "evidence": news_evidence,
            "evidence_policy": "Use the supplied NEWS_RAW article IDs; preserve publication dates and earnings periods.",
            "article_usage": "publication dates and snippets; month grouping is not the event or earnings period",
        },
        "market": {
            "preprocessed_input": market_packet,
            "evidence": market_evidence,
            "evidence_policy": "Only this YFinance catalog may support market findings.",
        },
    }

    semantic_input["boundary_requests"] = {
        "financial": build_financial_request(financial_factual, model=model),
        "news": build_news_llm_request(input_payload=news_payload, model=model),
        "market": build_market_request({
            "company_name": run_config.company_name,
            "market_summary": market_facts["market_summary"],
            "primary_evidence_catalog": market_evidence,
            "secondary_context": market_packet["secondary_context"],
        }, ticker=run_config.ticker, model=model),
    }
    artifacts = _input_artifact_manifest(paths)
    if any(Path(row["path"]).name == "final_report.json" for row in artifacts.values()):
        raise ValueError("Unified preprocessing input must never include prior Agent final_report.json")
    return {
        "protocol": UNIFIED_PROTOCOL,
        "semantic_input": semantic_input,
        "adapter_facts": {
            "financial_factual_report": financial_factual,
            "news_input_policy": news_payload.get("input_policy") or {},
            "news_target_entity": news_payload.get("target_entity") or {},
            "news_evidence_map": news_evidence_map,
            "market_facts": market_facts,
        },
        "preprocessing_parity": {
            "financial": {
                "builder": (
                    "Financial_Agent.build_financial_analyst_output + "
                    "build_financial_llm_packet"
                ),
                "same_as_full_agent_boundary": True,
                "boundary_packet_preserved_without_field_removal": True,
            },
            "news": {
                "builder": "News_Agent.build_analysis_input_payload + build_llm_request",
                "same_as_full_agent_boundary": True,
                "boundary_packet_preserved_without_field_removal": True,
                "raw_news_count": sum(key.startswith("NEWS_RAW_") for key in news_evidence),
                "period_news_count": sum(key.startswith("NEWS_PERIOD_") for key in news_evidence),
            },
            "market": {
                "builder": (
                    "YFinance_Agent.build_market_summary + primary/daily/monthly evidence catalogs + "
                    "build_llm_evidence_packet"
                ),
                "same_as_full_agent_boundary": True,
                "boundary_packet_preserved_without_field_removal": True,
                "market_row_count": market_facts["market_row_count"],
            },
            "evidence_separation": (
                "Each domain reuses Full analytical guidelines and user input; "
                "primary and provided secondary evidence references follow the same contracts."
            ),
        },
        "evidence_counts": {
            domain: len(catalog) for domain, catalog in catalogs.items()
        },
        "input_artifacts": artifacts,
    }


def build_unified_request(context: dict[str, Any], *, model: str) -> dict[str, Any]:
    return integrated_report.build_request(context, model=model)


def unified_response_format(context: dict[str, Any]) -> dict[str, Any]:
    format_spec = integrated_report.response_format(context)
    return {"type": "json_schema", "json_schema": {key: value for key, value in format_spec.items() if key != "type"}}


def validate_unified_output(output: dict[str, Any], context: dict[str, Any]) -> None:
    integrated_report.validate_output(output, context)


def unified_preflight_plan(
    *,
    run_config: RunConfig,
    paths: RunPaths,
    model: str,
    role: str,
) -> dict[str, Any]:
    """Validate one frozen entity without making an API call."""

    prepared = prepare_unified_inputs(run_config=run_config, paths=paths, model=model)
    request = build_unified_request(prepared["semantic_input"], model=model)
    measurement = measure_request(request, model=model)
    return {
        "role": role,
        "company_name": run_config.company_name,
        "run_key": run_config.run_key,
        "protocol": UNIFIED_PROTOCOL,
        "evidence_counts": prepared["evidence_counts"],
        "preprocessing_parity": prepared["preprocessing_parity"],
        "input_artifacts": prepared["input_artifacts"],
        "request_measurement": measurement.as_dict(),
    }


def _prepare_financial_input(
    run_config: RunConfig,
    paths: RunPaths,
) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    if not paths.financial_runtime_manifest.is_file():
        write_financial_runtime_manifest(paths, run_config, primary_data_only=False)
    manifest = load_json(paths.financial_runtime_manifest)
    inputs = {
        "dart_main": load_json(paths.dart_main),
        "dart_master": load_json(paths.dart_master),
        "yfinance_market_summary": load_json(paths.market_summary),
        "news_weekly_summaries": load_json(paths.news_llm_period_summaries),
    }
    factual = build_financial_analyst_output(manifest, inputs)
    if str(factual.get("target_company") or "") != run_config.company_name:
        raise ValueError("Financial preprocessing target does not match run config")
    exact_packet = build_financial_llm_packet(factual)
    evidence: dict[str, Any] = {}
    for evidence_id, raw in _dict(exact_packet.get("primary_financial_evidence")).items():
        row = copy.deepcopy(_dict(raw))
        row.update(
            {
                "evidence_id": str(evidence_id),
                "domain": "financial",
                "origin_type": "deterministic_preprocessed",
                "source_ref": f"dart_preprocessing.primary_financial_evidence.{evidence_id}",
            }
        )
        evidence[str(evidence_id)] = row
    packet = copy.deepcopy(exact_packet)
    return factual, packet, evidence


def _prepare_news_input(
    run_config: RunConfig,
    paths: RunPaths,
    model: str,
) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any], dict[str, Any]]:
    analysis_paths = AnalysisPaths(
        context_export_dir=paths.news_context_export_dir,
        context_manifest_path=paths.news_context_export_week_dir / "context_export_manifest.json",
        period_summaries_path=paths.news_llm_period_summaries,
        summary_prompt_input_path=paths.news_context_export_week_dir / "summary_prompt_input.json",
        recent_raw_path=paths.news_articles,
        dart_lightweight_path=paths.dart_lightweight,
        market_summary_path=paths.market_summary,
        output_dir=paths.news_analysis_output_dir,
        input_payload_path=paths.news_analysis_output_dir / "news_agent_input_payload.json",
        llm_request_path=paths.news_analysis_output_dir / "news_agent_llm_request.json",
        handoff_path=paths.news_handoff,
        evidence_map_path=paths.news_evidence_map,
    )
    payload = build_analysis_input_payload(
        company_name=run_config.company_name,
        ticker=run_config.ticker,
        corp_code=run_config.corp_code,
        as_of_date=date.fromisoformat(run_config.selected_date_iso),
        paths=analysis_paths,
        max_raw_events_per_period=24,
        include_secondary_context=True,
    )
    exact_request = build_news_llm_request(input_payload=payload, model=model)
    request_body = json.loads(str(exact_request["input"][1]["content"]))
    exact_packet = _dict(request_body.get("input_payload"))
    compact_news = {key: row for period in exact_packet.get("월별 개별 뉴스", [])
                    for key, row in period["articles"].items()}
    raw_map = {
        str(evidence_id): copy.deepcopy(row)
        for evidence_id, row in _dict(payload.get("evidence_map")).items()
        if isinstance(row, dict)
        and row.get("domain") == "news"
        and (
            row.get("source_type") == "recent_raw_event"
            or str(evidence_id).startswith("NEWS_RAW_")
        )
    }
    if set(compact_news) != set(raw_map):
        raise ValueError("News Agent compact input and raw News evidence map IDs differ")
    evidence = {
        evidence_id: {
            "evidence_id": evidence_id,
            "domain": "news",
            "origin_type": "raw_source",
            "source_type": "recent_raw_event",
            **copy.deepcopy(_dict(compact_news[evidence_id])),
        }
        for evidence_id in sorted(compact_news)
    }
    packet = copy.deepcopy(exact_packet)
    # Local adapter validation needs full source metadata, just like Full.
    # Only boundary_requests are sent to the LLM; this packet is not transmitted.
    packet["secondary_context"] = copy.deepcopy(payload.get("secondary_context") or {})
    return payload, packet, evidence, raw_map


def _prepare_market_input(
    run_config: RunConfig,
    paths: RunPaths,
) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    market_path = paths.yfinance_dir / "market_full_dataset.json"
    frame = load_market_dataset(market_path)
    direct_valuation = load_json(paths.valuation_snapshot)
    manifest_path = market_path.parent / "manifest.json"
    manifest = load_json(manifest_path) if manifest_path.exists() else {}
    boundary = pd.Timestamp(run_config.selected_date_iso).date()
    for value in (manifest.get("selected_date"), direct_valuation.get("selected_date")):
        if value and pd.Timestamp(value).date() != boundary:
            raise ValueError("Unified market inputs use a different selected date")
    frame = frame[frame["date"].dt.date < boundary].copy()
    date_range = manifest.get("date_range") or {}
    if date_range.get("start"):
        frame = frame[frame["date"] >= pd.Timestamp(date_range["start"])]
    if date_range.get("end"):
        frame = frame[frame["date"] <= pd.Timestamp(date_range["end"])]
    if frame.empty:
        raise ValueError("No market observations remain before the selected-date cutoff")
    frame.attrs["selected_date"] = boundary.isoformat()
    market_summary = build_market_summary(frame)
    evidence = build_market_primary_evidence_catalog(market_summary)
    evidence.update(build_daily_market_evidence_catalog(frame, max_rows=20))
    evidence.update(build_monthly_market_evidence_catalog(frame))
    valuation_snapshot = build_valuation_snapshot(
        market_summary=market_summary,
        dart_payload=load_json(paths.dart_lightweight),
        direct_valuation=direct_valuation,
        market_frame=frame,
    )
    market_evidence = {
        str(evidence_id): copy.deepcopy(row)
        for evidence_id, row in evidence.items()
        if isinstance(row, dict) and row.get("domain") == "market"
    }
    secondary_context = {
        "financial": build_dart_secondary_context(load_json(paths.dart_lightweight)),
        "news": build_news_secondary_context(
            load_json(paths.news_llm_period_summaries),
            source_path=paths.news_llm_period_summaries,
        ),
    }
    boundary_payload = {
        "company_name": run_config.company_name,
        "market_summary": market_summary,
        "primary_evidence_catalog": evidence,
        "secondary_context": secondary_context,
    }
    packet = build_llm_evidence_packet(boundary_payload, ticker=run_config.ticker)
    latest = _dict(market_summary.get("latest_snapshot"))
    facts = {
        "company_name": run_config.company_name,
        "ticker": run_config.ticker,
        "as_of_date": latest.get("date"),
        "selected_date": run_config.selected_date_iso,
        "market_row_count": int(len(frame.index)),
        "market_summary": market_summary,
        "monthly_market_table": build_monthly_market_table(frame),
        "valuation_snapshot": valuation_snapshot,
        "primary_evidence_catalog": market_evidence,
    }
    return facts, packet, market_evidence


def _input_artifact_manifest(paths: RunPaths) -> dict[str, dict[str, Any]]:
    files = {
        "financial_runtime_manifest": paths.financial_runtime_manifest,
        "dart_main": paths.dart_main,
        "dart_master": paths.dart_master,
        "dart_lightweight": paths.dart_lightweight,
        "news_context_manifest": paths.news_context_export_week_dir / "context_export_manifest.json",
        "news_articles": paths.news_articles,
        "news_subdata_summaries": paths.news_llm_period_summaries,
        "market_full_dataset": paths.yfinance_dir / "market_full_dataset.json",
        "market_summary": paths.market_summary,
        "valuation_snapshot": paths.valuation_snapshot,
    }
    result: dict[str, dict[str, Any]] = {}
    for name, path in files.items():
        resolved = Path(path).expanduser().resolve()
        if not resolved.is_file():
            raise FileNotFoundError(f"Unified preprocessing artifact is missing: {resolved}")
        result[name] = {
            "path": str(resolved),
            "sha256": sha256_file(resolved),
            "bytes": resolved.stat().st_size,
        }
    return result


def _validate_separated_catalogs(catalogs: dict[str, dict[str, Any]]) -> None:
    owners: dict[str, str] = {}
    for domain, catalog in catalogs.items():
        if not catalog:
            raise ValueError(f"Unified {domain} evidence catalog is empty")
        for evidence_id, row in catalog.items():
            if evidence_id in owners:
                raise ValueError(
                    f"Evidence ID {evidence_id} appears in both {owners[evidence_id]} and {domain}"
                )
            owners[evidence_id] = domain
            if _dict(row).get("domain") != domain:
                raise ValueError(f"Evidence {evidence_id} has incorrect domain metadata")
            if domain == "news" and not str(evidence_id).startswith(("NEWS_RAW_", "NEWS_PERIOD_")):
                raise ValueError(f"Unsupported News evidence ID: {evidence_id}")


def _call_openai(request: dict[str, Any], *, timeout_seconds: int) -> tuple[dict[str, Any], dict[str, Any]]:
    # Timeout and transport retry policy are shared with all Full domains.
    response = call_domain_response(request, step="unified:domain_agent", timeout_seconds=timeout_seconds)
    return json.loads(response.output_text), normalize_usage(response.usage)










def _evidence_ids(context: dict[str, Any], domain: str) -> Iterable[str]:
    evidence = _dict(_dict(context.get(domain)).get("evidence"))
    if evidence:
        return evidence.keys()
    rows = _dict(context.get(domain)).get("evidence") or []
    return [
        str(row.get("evidence_id"))
        for row in rows
        if isinstance(row, dict) and row.get("evidence_id")
    ]


@contextmanager
def _temporary_environment(values: dict[str, str]) -> Iterator[None]:
    previous = {key: os.environ.get(key) for key in values}
    try:
        os.environ.update(values)
        yield
    finally:
        for key, value in previous.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value


def _load_env(path: Path) -> None:
    try:
        from dotenv import load_dotenv

        load_dotenv(path, override=False)
    except Exception:
        for raw in path.read_text(encoding="utf-8").splitlines():
            line = raw.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


def _dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}
