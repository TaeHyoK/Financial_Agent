"""Candidate-neutral raw-data bundle builder for Single-LLM Direct."""

from __future__ import annotations

import copy
import hashlib
import json
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable

from shared.llm_clients import compact_json

from .contracts import BUNDLE_VERSION, SOURCE_MANIFEST_VERSION


_NON_SEMANTIC_KEYS = {
    "cache_metadata",
    "created_at",
    "execution_id",
    "generated_at",
    "index_file",
    "llm_model",
    "model",
    "model_name",
    "request_sha256",
    "retrieved_at",
    "source_file",
    "source_path",
    "source_paths",
}
_NEWS_FIELDS = (
    "source_date",
    "period",
    "time",
    "relation_type",
    "mention_count",
    "final_score",
    "title",
    "snippet",
    "source",
    "url",
)


@dataclass(frozen=True)
class BundleBuildResult:
    bundle: dict[str, Any]
    source_manifest: dict[str, Any]
    temporal_validation: dict[str, Any]


@dataclass(frozen=True)
class _RunArtifacts:
    role: str
    run_key: str
    run_config_path: Path
    financial_path: Path
    news_evidence_path: Path
    market_path: Path
    valuation_path: Path

    def paths(self) -> dict[str, Path]:
        return {
            "run_config": self.run_config_path,
            "financial": self.financial_path,
            "news": self.news_evidence_path,
            "market": self.market_path,
            "valuation": self.valuation_path,
        }


def build_input_bundle(
    *,
    project_root: str | Path,
    output_root: str | Path,
    target_run_key: str,
    peer_run_key: str,
    decision_horizon: str,
    max_news_items_per_company: int = 0,
) -> BundleBuildResult:
    """Build one frozen input bundle without reading any LLM-generated report."""

    project = Path(project_root).expanduser().resolve()
    output = Path(output_root).expanduser().resolve()
    if not target_run_key.strip() or not peer_run_key.strip():
        raise ValueError("target_run_key and peer_run_key are required")
    if target_run_key == peer_run_key:
        raise ValueError("target and peer run keys must be different")
    if max_news_items_per_company < 0:
        raise ValueError("max_news_items_per_company must be zero or positive")

    target_artifacts = _resolve_artifacts(output, target_run_key, role="target")
    peer_artifacts = _resolve_artifacts(output, peer_run_key, role="peer")
    artifacts = (target_artifacts, peer_artifacts)
    loaded = {
        artifact.role: {
            name: _read_json(path)
            for name, path in artifact.paths().items()
        }
        for artifact in artifacts
    }

    target_config = _object(loaded["target"]["run_config"], "target run config")
    peer_config = _object(loaded["peer"]["run_config"], "peer run config")
    target_date = _iso_date(target_config.get("selected_date"))
    peer_date = _iso_date(peer_config.get("selected_date"))
    if target_date != peer_date:
        raise ValueError(
            f"Target and peer selected_date differ: {target_date} != {peer_date}"
        )
    selected_date = target_date
    cutoff_date = (date.fromisoformat(selected_date) - timedelta(days=1)).isoformat()

    evidence: list[dict[str, Any]] = []
    news_selection: dict[str, dict[str, Any]] = {}
    for artifact in artifacts:
        role = artifact.role
        role_payload = loaded[role]
        financial = _strip_nonsemantic(role_payload["financial"])
        evidence.append(
            {
                "evidence_id": f"FINANCIAL_{role.upper()}_DART_MAIN",
                "role": role,
                "domain": "financial",
                "evidence_type": "raw_and_deterministic_financial",
                "as_of_date": _financial_latest_receipt_date(financial),
                "payload": financial,
            }
        )

        news_records = _prepare_news_records(
            _object(role_payload["news"], f"{role} news evidence"),
            role=role,
            max_items=max_news_items_per_company,
        )
        evidence.extend(news_records)
        news_selection[role] = {
            "available_count": _raw_news_count(role_payload["news"]),
            "included_count": len(news_records),
            "initial_cap": max_news_items_per_company,
            "budget_pruned_count": 0,
            "budget_pruned_source_ids": [],
            "selection_rule": (
                "all_raw_news"
                if max_news_items_per_company == 0
                else "top_final_score_then_recency_then_source_id"
            ),
        }

        market_rows = _array(role_payload["market"], f"{role} market dataset")
        for index, row in enumerate(market_rows, start=1):
            market_row = _object(row, f"{role} market row {index}")
            row_date = _optional_iso_date(market_row.get("date"))
            date_label = (row_date or f"ROW_{index:03d}").replace("-", "")
            evidence.append(
                {
                    "evidence_id": f"MARKET_{role.upper()}_{date_label}_{index:03d}",
                    "role": role,
                    "domain": "market",
                    "evidence_type": "deterministic_market_row",
                    "as_of_date": row_date,
                    "payload": _strip_nonsemantic(market_row),
                }
            )

        evidence.append(
            {
                "evidence_id": f"VALUATION_{role.upper()}_SNAPSHOT",
                "role": role,
                "domain": "valuation",
                "evidence_type": "historical_provider_snapshot",
                "as_of_date": _valuation_latest_date(role_payload["valuation"]),
                "payload": _strip_nonsemantic(role_payload["valuation"]),
            }
        )

    evidence.sort(key=_evidence_sort_key)
    artifact_hashes = {
        artifact.role: {
            name: file_sha256(path)
            for name, path in artifact.paths().items()
        }
        for artifact in artifacts
    }
    bundle: dict[str, Any] = {
        "bundle_version": BUNDLE_VERSION,
        "experiment_contract": {
            "baseline": "single_model_single_generation_call",
            "candidate_isolation": True,
            "use_only_supplied_data": True,
            "semantic_generation_attempts": 1,
            "agent_generated_reports_excluded": True,
            "strategy_outputs_excluded": True,
            "writer_outputs_excluded": True,
            "point_in_time_rule": "all substantive evidence dates must be before selected_date",
        },
        "selected_date": selected_date,
        "information_cutoff_date": cutoff_date,
        "selected_date_policy": str(
            target_config.get("selected_date_policy") or "before_market_open"
        ),
        "decision_horizon": str(decision_horizon),
        "target": _identity(target_config, run_key=target_run_key),
        "peer": _identity(peer_config, run_key=peer_run_key),
        "comparison_scope": {
            "allowed_peer_company": str(peer_config.get("company_name") or ""),
            "allowed_benchmark": "KOSPI",
            "selected_peer_is_not_an_industry_average": True,
        },
        "report_requirements": {
            "language": "Korean",
            "recommendations": ["BUY", "HOLD", "SELL"],
            "cite_evidence_ids_for_every_material_claim": True,
            "do_not_create_numbers_absent_from_referenced_evidence": True,
            "separate_observation_from_interpretation": True,
            "state_data_limitations": True,
        },
        "news_selection": news_selection,
        "source_artifact_sha256": artifact_hashes,
        "evidence_catalog": evidence,
    }
    validation = validate_bundle_temporality(bundle)
    if validation["status"] != "valid":
        raise ValueError(
            "Point-in-time validation failed: " + "; ".join(validation["violations"])
        )
    bundle["bundle_sha256"] = bundle_sha256(bundle)
    source_manifest = _source_manifest(project, artifacts)
    return BundleBuildResult(
        bundle=bundle,
        source_manifest=source_manifest,
        temporal_validation=validation,
    )


def fit_bundle_to_token_budget(
    bundle: dict[str, Any],
    *,
    measure_input_tokens: Callable[[dict[str, Any]], int],
    target_input_tokens: int,
    hard_input_tokens: int,
    min_news_items_per_company: int,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Deterministically prune the weakest news records until the request fits."""

    if not 0 < target_input_tokens <= hard_input_tokens:
        raise ValueError("token budgets must satisfy 0 < target <= hard")
    fitted = copy.deepcopy(bundle)
    initial_tokens = int(measure_input_tokens(fitted))
    current_tokens = initial_tokens
    removed: list[dict[str, str]] = []

    while current_tokens > target_input_tokens:
        candidate = _lowest_priority_removable_news(
            fitted,
            min_news_items_per_company=min_news_items_per_company,
        )
        if candidate is None:
            break
        catalog = _array(fitted.get("evidence_catalog"), "evidence_catalog")
        catalog.remove(candidate)
        role = str(candidate.get("role") or "")
        source_id = str(
            _object(candidate.get("payload"), "news payload").get("source_evidence_id")
            or candidate.get("evidence_id")
        )
        removed.append({"role": role, "source_evidence_id": source_id})
        selection = _object(fitted.get("news_selection"), "news_selection")
        role_selection = _object(selection.get(role), f"news_selection.{role}")
        role_selection["included_count"] = int(role_selection["included_count"]) - 1
        role_selection["budget_pruned_count"] = int(
            role_selection.get("budget_pruned_count") or 0
        ) + 1
        role_selection.setdefault("budget_pruned_source_ids", []).append(source_id)
        current_tokens = int(measure_input_tokens(fitted))

    if current_tokens > hard_input_tokens:
        raise ValueError(
            f"Single-LLM request is {current_tokens:,} tokens after deterministic news "
            f"pruning; hard input budget is {hard_input_tokens:,}."
        )
    fitted["bundle_sha256"] = bundle_sha256(fitted)
    return fitted, {
        "initial_input_tokens": initial_tokens,
        "final_input_tokens": current_tokens,
        "target_input_tokens": target_input_tokens,
        "hard_input_tokens": hard_input_tokens,
        "over_target": current_tokens > target_input_tokens,
        "removed_news_count": len(removed),
        "removed_news": removed,
    }


def validate_bundle_temporality(bundle: dict[str, Any]) -> dict[str, Any]:
    """Validate known evidence dates against the pre-market selected date."""

    selected = date.fromisoformat(_iso_date(bundle.get("selected_date")))
    violations: list[str] = []
    checked = 0
    for item in _array(bundle.get("evidence_catalog"), "evidence_catalog"):
        evidence = _object(item, "evidence item")
        evidence_id = str(evidence.get("evidence_id") or "unknown")
        domain = str(evidence.get("domain") or "")
        payload = evidence.get("payload")
        dates: list[tuple[str, str]] = []
        if domain == "news":
            news = _object(payload, f"{evidence_id}.payload")
            value = news.get("source_date") or news.get("period") or news.get("time")
            if value:
                dates.append(("source_date", _iso_date(value)))
            else:
                violations.append(f"{evidence_id}.source_date is missing")
        elif domain == "market":
            row = _object(payload, f"{evidence_id}.payload")
            if row.get("date"):
                dates.append(("date", _iso_date(row["date"])))
            else:
                violations.append(f"{evidence_id}.date is missing")
        elif domain == "valuation":
            for index, period in enumerate(
                _array(_object(payload, f"{evidence_id}.payload").get("periods") or [], "periods")
            ):
                period_obj = _object(period, f"{evidence_id}.periods[{index}]")
                if period_obj.get("valuation_date"):
                    dates.append(
                        (f"periods[{index}].valuation_date", _iso_date(period_obj["valuation_date"]))
                    )
        elif domain == "financial":
            financial = _object(payload, f"{evidence_id}.payload")
            context = financial.get("collection_context") or {}
            if isinstance(context, dict):
                for index, report in enumerate(context.get("reports_used") or []):
                    if isinstance(report, dict) and report.get("receipt_date"):
                        dates.append(
                            (f"reports_used[{index}].receipt_date", _iso_date(report["receipt_date"]))
                        )
        for field, value in dates:
            checked += 1
            if date.fromisoformat(value) >= selected:
                violations.append(f"{evidence_id}.{field}={value} is not before {selected}")
    return {
        "status": "valid" if not violations else "invalid",
        "selected_date": selected.isoformat(),
        "checked_dates": checked,
        "violations": violations,
    }


def bundle_sha256(bundle: dict[str, Any]) -> str:
    content = {key: value for key, value in bundle.items() if key != "bundle_sha256"}
    return hashlib.sha256(
        compact_json(content, sort_keys=True).encode("utf-8")
    ).hexdigest()


def file_sha256(path: str | Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _resolve_artifacts(output_root: Path, run_key: str, *, role: str) -> _RunArtifacts:
    artifact = _RunArtifacts(
        role=role,
        run_key=run_key,
        run_config_path=output_root / "runs" / run_key / "run_config.json",
        financial_path=output_root / "Financial" / run_key / "dart_main.json",
        news_evidence_path=(
            output_root / "News" / run_key / "output" / "news_agent_evidence_map.json"
        ),
        market_path=output_root / "Y_Finance" / run_key / "market_full_dataset.json",
        valuation_path=output_root / "Y_Finance" / run_key / "valuation_snapshot.json",
    )
    missing = [str(path) for path in artifact.paths().values() if not path.is_file()]
    if missing:
        raise FileNotFoundError(f"Missing {role} source artifacts: {missing}")
    return artifact


def _prepare_news_records(
    evidence_map: dict[str, Any],
    *,
    role: str,
    max_items: int,
) -> list[dict[str, Any]]:
    candidates: list[tuple[str, dict[str, Any]]] = []
    for source_id, raw_value in evidence_map.items():
        if not str(source_id).startswith("NEWS_RAW_"):
            continue
        if not isinstance(raw_value, dict):
            continue
        candidates.append((str(source_id), raw_value))
    candidates.sort(key=lambda pair: _news_rank_key(pair[0], pair[1]))
    if max_items:
        candidates = candidates[:max_items]

    records: list[dict[str, Any]] = []
    for source_id, raw in candidates:
        payload = {"source_evidence_id": source_id}
        for key in _NEWS_FIELDS:
            value = raw.get(key)
            if value not in (None, "", [], {}):
                payload[key] = value
        coverage = raw.get("coverage") or {}
        if isinstance(coverage, dict):
            payload["coverage"] = {
                key: coverage.get(key)
                for key in (
                    "article_count",
                    "unique_publisher_count",
                    "primary_source_present",
                    "coverage_quality",
                )
                if coverage.get(key) is not None
            }
        source_date = _optional_iso_date(
            raw.get("source_date") or raw.get("period") or raw.get("time")
        )
        records.append(
            {
                "evidence_id": f"NEWS_{role.upper()}_{source_id}",
                "role": role,
                "domain": "news",
                "evidence_type": "raw_news_event",
                "as_of_date": source_date,
                "payload": payload,
            }
        )
    return records


def _news_rank_key(source_id: str, payload: dict[str, Any]) -> tuple[float, int, str]:
    try:
        score = float(payload.get("final_score") or 0.0)
    except (TypeError, ValueError):
        score = 0.0
    date_digits = "".join(
        character
        for character in str(
            payload.get("source_date") or payload.get("period") or payload.get("time") or ""
        )
        if character.isdigit()
    )
    recency = int(date_digits[:8]) if len(date_digits) >= 8 else 0
    return (-score, -recency, source_id)


def _lowest_priority_removable_news(
    bundle: dict[str, Any],
    *,
    min_news_items_per_company: int,
) -> dict[str, Any] | None:
    catalog = _array(bundle.get("evidence_catalog"), "evidence_catalog")
    news = [item for item in catalog if isinstance(item, dict) and item.get("domain") == "news"]
    counts: dict[str, int] = {}
    for item in news:
        role = str(item.get("role") or "")
        counts[role] = counts.get(role, 0) + 1
    removable = [
        item
        for item in news
        if counts.get(str(item.get("role") or ""), 0) > min_news_items_per_company
    ]
    if not removable:
        return None

    def weakest(item: dict[str, Any]) -> tuple[float, int, str]:
        payload = item.get("payload") or {}
        try:
            score = float(payload.get("final_score") or 0.0)
        except (TypeError, ValueError):
            score = 0.0
        date_digits = "".join(
            character for character in str(item.get("as_of_date") or "") if character.isdigit()
        )
        recency = int(date_digits[:8]) if len(date_digits) >= 8 else 0
        return (score, recency, str(item.get("evidence_id") or ""))

    return min(removable, key=weakest)


def _identity(config: dict[str, Any], *, run_key: str) -> dict[str, Any]:
    resolution = config.get("identity_resolution") or {}
    return {
        "run_key": run_key,
        "company_name": str(config.get("company_name") or ""),
        "corp_code": str(config.get("corp_code") or config.get("company_code") or ""),
        "stock_code": str(config.get("stock_code") or ""),
        "ticker": str(config.get("ticker") or ""),
        "market": str(resolution.get("market") or "") if isinstance(resolution, dict) else "",
    }


def _source_manifest(project_root: Path, artifacts: tuple[_RunArtifacts, ...]) -> dict[str, Any]:
    sources: list[dict[str, Any]] = []
    for artifact in artifacts:
        for domain, path in artifact.paths().items():
            try:
                display_path = str(path.relative_to(project_root))
            except ValueError:
                display_path = str(path)
            sources.append(
                {
                    "role": artifact.role,
                    "run_key": artifact.run_key,
                    "domain": domain,
                    "path": display_path,
                    "bytes": path.stat().st_size,
                    "sha256": file_sha256(path),
                }
            )
    return {
        "version": SOURCE_MANIFEST_VERSION,
        "created_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "forbidden_source_classes": [
            "agent natural-language reports",
            "SY validation narratives",
            "Strategy packets and decisions",
            "Writer outputs",
            "existing peer comparison derived from final reports",
        ],
        "sources": sources,
    }


def _valuation_latest_date(value: Any) -> str | None:
    payload = _object(value, "valuation snapshot")
    latest = payload.get("latest_period") or {}
    if isinstance(latest, dict) and latest.get("valuation_date"):
        return _iso_date(latest["valuation_date"])
    return None


def _financial_latest_receipt_date(value: Any) -> str | None:
    payload = _object(value, "financial payload")
    context = payload.get("collection_context") or {}
    if not isinstance(context, dict):
        return None
    dates = [
        _iso_date(report.get("receipt_date"))
        for report in context.get("reports_used") or []
        if isinstance(report, dict) and report.get("receipt_date")
    ]
    return max(dates) if dates else None


def _evidence_sort_key(item: dict[str, Any]) -> tuple[str, str, str, str]:
    return (
        str(item.get("role") or ""),
        str(item.get("domain") or ""),
        str(item.get("as_of_date") or ""),
        str(item.get("evidence_id") or ""),
    )


def _raw_news_count(value: Any) -> int:
    payload = _object(value, "news evidence map")
    return sum(1 for key in payload if str(key).startswith("NEWS_RAW_"))


def _strip_nonsemantic(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            str(key): _strip_nonsemantic(item)
            for key, item in value.items()
            if str(key).lower().lstrip("_") not in _NON_SEMANTIC_KEYS
        }
    if isinstance(value, list):
        return [_strip_nonsemantic(item) for item in value]
    return value


def _read_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ValueError(f"Invalid JSON source artifact {path}: {exc}") from exc


def _object(value: Any, label: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ValueError(f"{label} must be a JSON object")
    return value


def _array(value: Any, label: str) -> list[Any]:
    if not isinstance(value, list):
        raise ValueError(f"{label} must be a JSON array")
    return value


def _iso_date(value: Any) -> str:
    digits = "".join(character for character in str(value or "") if character.isdigit())
    if len(digits) != 8:
        raise ValueError(f"Expected YYYYMMDD or YYYY-MM-DD date, got {value!r}")
    normalized = f"{digits[:4]}-{digits[4:6]}-{digits[6:]}"
    date.fromisoformat(normalized)
    return normalized


def _optional_iso_date(value: Any) -> str | None:
    if value in (None, ""):
        return None
    return _iso_date(value)


__all__ = [
    "BundleBuildResult",
    "build_input_bundle",
    "bundle_sha256",
    "file_sha256",
    "fit_bundle_to_token_budget",
    "validate_bundle_temporality",
]
