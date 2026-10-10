"""Discover and validate the exact Full/ablation report matrix."""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from statistics import mean
from typing import Any, Iterable

from ablation_suite.config import COMPANY_SPECS
from ablation_suite.protocol import resolve_selected_date
from ablation_suite.utils import safe_label


DEFAULT_ABLATIONS: tuple[str, ...] = (
    "no_peer",
    "no_subdata",
    "unified_domain_team",
    "random_news",
)


@dataclass(frozen=True)
class RunArtifact:
    condition: str
    replicate: int
    company_key: str
    company_name: str
    report: Path
    manifest: Path
    strategy_packet: Path
    usage_summary: Path | None
    duration_seconds: float | None
    source: str
    preprocessing_usage_summary: Path | None = None

    @property
    def key(self) -> tuple[str, int, str]:
        return (self.condition, self.replicate, self.company_key)


@dataclass(frozen=True)
class PairSpec:
    company_key: str
    company_name: str
    replicate: int
    ablation_condition: str
    full: RunArtifact
    ablation: RunArtifact

    @property
    def pair_id(self) -> str:
        return safe_label(
            f"{self.company_key}__{self.ablation_condition}__r{self.replicate:02d}"
        )


def discover_evaluation_inputs(
    *,
    ablation_suite: str | Path,
    full_suite: str | Path,
    full_r1_roots: Iterable[str | Path],
    selected_date: str,
    ablations: tuple[str, ...] = DEFAULT_ABLATIONS,
    replicates: tuple[int, ...] = (1, 2, 3),
) -> tuple[list[PairSpec], list[RunArtifact]]:
    """Return a complete 72-pair matrix or fail before any Judge call."""

    unknown = sorted(set(ablations) - set(DEFAULT_ABLATIONS))
    if unknown:
        raise ValueError(f"Unsupported ablation conditions: {unknown}")
    artifacts: list[RunArtifact] = []
    if selected_date != "report":
        artifacts.extend(
            discover_frozen_full_r1(full_r1_roots, selected_date=selected_date)
        )
    artifacts.extend(
        discover_suite_artifacts(
            full_suite,
            conditions=("full",),
            selected_date=selected_date,
        )
    )
    artifacts.extend(
        discover_suite_artifacts(
            ablation_suite,
            conditions=ablations,
            selected_date=selected_date,
        )
    )

    by_key: dict[tuple[str, int, str], RunArtifact] = {}
    duplicates: list[tuple[str, int, str]] = []
    for artifact in artifacts:
        if artifact.key in by_key:
            duplicates.append(artifact.key)
        by_key[artifact.key] = artifact
    if duplicates:
        raise ValueError(f"Duplicate run artifacts: {sorted(set(duplicates))}")

    expected_company_keys = tuple(company.key for company in COMPANY_SPECS)
    missing: list[str] = []
    pairs: list[PairSpec] = []
    for replicate in replicates:
        for company in COMPANY_SPECS:
            full = by_key.get(("full", replicate, company.key))
            if full is None:
                missing.append(f"full/r{replicate:02d}/{company.name}")
                continue
            for condition in ablations:
                ablation = by_key.get((condition, replicate, company.key))
                if ablation is None:
                    missing.append(f"{condition}/r{replicate:02d}/{company.name}")
                    continue
                pairs.append(
                    PairSpec(
                        company_key=company.key,
                        company_name=company.name,
                        replicate=replicate,
                        ablation_condition=condition,
                        full=full,
                        ablation=ablation,
                    )
                )
    if missing:
        raise ValueError(
            "Evaluation input matrix is incomplete; no Judge calls were made. Missing: "
            + ", ".join(missing)
        )
    expected_pairs = len(expected_company_keys) * len(replicates) * len(ablations)
    if len(pairs) != expected_pairs:
        raise AssertionError(f"Expected {expected_pairs} pairs, discovered {len(pairs)}")

    selected_keys = {
        (condition, replicate, company_key)
        for condition in ("full", *ablations)
        for replicate in replicates
        for company_key in expected_company_keys
    }
    selected_artifacts = [by_key[key] for key in sorted(selected_keys)]
    return pairs, selected_artifacts


def discover_frozen_full_r1(
    roots: Iterable[str | Path],
    *,
    selected_date: str,
) -> list[RunArtifact]:
    resolved_roots = [Path(root).expanduser().resolve() for root in roots]
    artifacts: list[RunArtifact] = []
    for company in COMPANY_SPECS:
        company_date = resolve_selected_date(company.key, selected_date)
        matches = [
            root / company.name / "runs" / company_date / "full_pipeline_manifest.json"
            for root in resolved_roots
        ]
        manifest = next((path for path in matches if path.is_file()), None)
        if manifest is None:
            raise ValueError(
                f"Missing frozen Full r01 for {company.name}; checked: "
                + ", ".join(map(str, matches))
            )
        artifacts.append(
            _artifact_from_manifest(
                condition="full",
                replicate=1,
                company_key=company.key,
                company_name=company.name,
                manifest_path=manifest,
                state=None,
                source="frozen_full_r1",
                selected_date=company_date,
            )
        )
    return artifacts


def discover_suite_artifacts(
    suite_root: str | Path,
    *,
    conditions: Iterable[str],
    selected_date: str,
) -> list[RunArtifact]:
    root = Path(suite_root).expanduser().resolve()
    if not root.is_dir():
        raise ValueError(f"Suite root does not exist: {root}")
    artifacts: list[RunArtifact] = []
    for condition in conditions:
        state_root = root / "state" / condition
        if not state_root.is_dir():
            continue
        for path in sorted(state_root.glob("replicate_*/*.json")):
            state = _load_object(path)
            if state.get("status") != "success":
                continue
            company = state.get("company") if isinstance(state.get("company"), dict) else {}
            replicate = int(state.get("replicate") or 0)
            company_key = str(company.get("key") or path.stem)
            company_date = resolve_selected_date(company_key, selected_date)
            if state.get("selected_date") and state["selected_date"] != company_date:
                raise ValueError(f"Selected-date mismatch in task state: {path}")
            artifacts.append(
                _artifact_from_manifest(
                    condition=condition,
                    replicate=replicate,
                    company_key=company_key,
                    company_name=str(company.get("name") or path.stem),
                    manifest_path=Path(str(state.get("full_pipeline_manifest") or "")),
                    state=state,
                    source=str(root),
                    selected_date=company_date,
                )
            )
    return artifacts


def aggregate_generation_efficiency(artifacts: list[RunArtifact]) -> dict[str, Any]:
    """Summarize comparable analysis/report calls plus raw observed usage.

    The original Full r01 generated the frozen weekly News summaries, while all
    later runs reused them.  Excluding ``news:period_summary`` from every row
    prevents that one-time preprocessing call from biasing the condition cost.
    """

    by_condition: dict[str, Any] = {}
    for condition in sorted({artifact.condition for artifact in artifacts}):
        rows = [artifact for artifact in artifacts if artifact.condition == condition]
        usage_rows = [
            _load_object(artifact.usage_summary)
            for artifact in rows
            if artifact.usage_summary is not None and artifact.usage_summary.is_file()
        ]
        comparable = [_comparable_usage(row) for row in usage_rows]
        preprocessing = [
            _load_object(artifact.preprocessing_usage_summary) for artifact in rows
            if artifact.preprocessing_usage_summary is not None and artifact.preprocessing_usage_summary.is_file()
        ]
        durations = [row.duration_seconds for row in rows if row.duration_seconds is not None]
        by_condition[condition] = {
            "run_count": len(rows),
            "usage_summary_count": len(usage_rows),
            "mean_logical_calls": _mean_or_none(
                [float(row["logical_calls"]) for row in comparable]
            ),
            "mean_total_tokens": _mean_or_none(
                [float(row["usage"]["total_tokens"]) for row in comparable]
            ),
            "mean_input_tokens": _mean_or_none(
                [float(row["usage"]["input_tokens"]) for row in comparable]
            ),
            "mean_output_tokens": _mean_or_none(
                [float(row["usage"]["output_tokens"]) for row in comparable]
            ),
            "mean_cost_usd": _mean_or_none(
                [float(row["cost_usd"]) for row in comparable if row["cost_usd"] is not None]
            ),
            "mean_duration_seconds": _mean_or_none(durations),
            "transport_error_attempts": sum(int(row["error_attempts"]) for row in comparable),
            "excluded_from_comparable_metrics": ["*:news:period_summary"],
            "preprocessing_usage_summary_count": len(preprocessing),
            "additional_preprocessing_total_tokens": sum(int((row.get("usage") or {}).get("total_tokens") or 0) for row in preprocessing),
            "additional_preprocessing_cost_usd": (
                sum(float(row["estimated_api_cost"]["total_cost_usd"]) for row in preprocessing)
                if preprocessing and all((row.get("estimated_api_cost") or {}).get("status") == "available" for row in preprocessing)
                else None
            ),
            "cost_scope_note": "Analysis-only metrics exclude shared preprocessing; per-replicate random summary regeneration is reported separately and must be added for end-to-end cost.",
            "raw_observed_mean_logical_calls": _mean_or_none(
                [float(row.get("observed_logical_calls") or 0) for row in usage_rows]
            ),
            "raw_observed_mean_total_tokens": _mean_or_none(
                [float((row.get("usage") or {}).get("total_tokens") or 0) for row in usage_rows]
            ),
            "raw_observed_mean_cost_usd": _mean_or_none(
                [
                    float((row.get("estimated_api_cost") or {}).get("total_cost_usd") or 0)
                    for row in usage_rows
                    if (row.get("estimated_api_cost") or {}).get("status") == "available"
                ]
            ),
            "pipeline_incomplete_count": sum(
                row.get("pipeline_completed") is not True for row in usage_rows
            ),
        }
    return {"by_condition": by_condition}


def _comparable_usage(summary: dict[str, Any]) -> dict[str, Any]:
    steps = summary.get("by_step") if isinstance(summary.get("by_step"), dict) else {}
    included = [
        item
        for key, item in steps.items()
        if isinstance(item, dict) and "news:period_summary" not in str(key)
    ]
    usage_keys = (
        "input_tokens",
        "cached_input_tokens",
        "output_tokens",
        "reasoning_tokens",
        "total_tokens",
    )
    usage = {
        key: sum(int((item.get("usage") or {}).get(key) or 0) for item in included)
        for key in usage_keys
    }
    cost: float | None = None
    models = (summary.get("estimated_api_cost") or {}).get("by_model") or {}
    if isinstance(models, dict) and len(models) == 1:
        model = next(iter(models.values()))
        pricing = model.get("pricing_usd_per_million_tokens") if isinstance(model, dict) else None
        if isinstance(pricing, dict):
            cached = usage["cached_input_tokens"]
            uncached = max(0, usage["input_tokens"] - cached)
            cost = (
                uncached * float(pricing.get("input") or 0)
                + cached * float(pricing.get("cached_input") or 0)
                + usage["output_tokens"] * float(pricing.get("output") or 0)
            ) / 1_000_000
    return {
        "logical_calls": sum(int(item.get("logical_calls") or 0) for item in included),
        "error_attempts": sum(int(item.get("error_attempts") or 0) for item in included),
        "usage": usage,
        "cost_usd": cost,
    }


def _artifact_from_manifest(
    *,
    condition: str,
    replicate: int,
    company_key: str,
    company_name: str,
    manifest_path: Path,
    state: dict[str, Any] | None,
    source: str,
    selected_date: str,
) -> RunArtifact:
    manifest_path = manifest_path.expanduser().resolve()
    if not manifest_path.is_file():
        raise ValueError(f"Pipeline manifest is missing: {manifest_path}")
    manifest = _load_object(manifest_path)
    if manifest.get("status") != "success":
        raise ValueError(f"Pipeline did not succeed: {manifest_path}")
    request = manifest.get("request") if isinstance(manifest.get("request"), dict) else {}
    if str(request.get("selected_date") or "") != selected_date:
        raise ValueError(f"Selected-date mismatch: {manifest_path}")
    outputs = manifest.get("outputs") if isinstance(manifest.get("outputs"), dict) else {}
    report = Path(str(outputs.get("writer_report") or (state or {}).get("writer_report") or ""))
    strategy_packet = Path(str(outputs.get("strategy_compact_packet") or ""))
    usage_value = str(outputs.get("llm_usage_summary") or "").strip()
    usage_summary = Path(usage_value).expanduser().resolve() if usage_value else None
    preprocessing_value = str(outputs.get("random_news_preprocessing_usage_summary") or (state or {}).get("random_news_preprocessing_usage_summary") or "").strip()
    preprocessing_usage = Path(preprocessing_value).expanduser().resolve() if preprocessing_value else None
    if preprocessing_usage is not None and not preprocessing_usage.is_file():
        raise ValueError(f"Missing random preprocessing usage summary: {preprocessing_usage}")
    for label, path in (("writer report", report), ("Strategy packet", strategy_packet)):
        if not path.expanduser().resolve().is_file():
            raise ValueError(f"Missing {label} for {condition}/r{replicate:02d}/{company_name}: {path}")
    return RunArtifact(
        condition=condition,
        replicate=replicate,
        company_key=company_key,
        company_name=company_name,
        report=report.expanduser().resolve(),
        manifest=manifest_path,
        strategy_packet=strategy_packet.expanduser().resolve(),
        usage_summary=usage_summary,
        duration_seconds=_duration_seconds(state, manifest),
        source=source,
        preprocessing_usage_summary=preprocessing_usage,
    )


def _duration_seconds(state: dict[str, Any] | None, manifest: dict[str, Any]) -> float | None:
    candidates = []
    if state:
        candidates.append((state.get("started_at"), state.get("completed_at")))
    candidates.append((manifest.get("created_at"), manifest.get("completed_at")))
    steps = manifest.get("steps") if isinstance(manifest.get("steps"), list) else []
    starts = [step.get("started_at") for step in steps if isinstance(step, dict) and step.get("started_at")]
    ends = [
        step.get("completed_at") or step.get("ended_at")
        for step in steps
        if isinstance(step, dict) and (step.get("completed_at") or step.get("ended_at"))
    ]
    if starts and ends:
        candidates.append((min(starts), max(ends)))
    for start, end in candidates:
        try:
            return max(0.0, (_parse_time(str(end)) - _parse_time(str(start))).total_seconds())
        except (TypeError, ValueError):
            continue
    return None


def _parse_time(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def _load_object(path: str | Path) -> dict[str, Any]:
    payload = json.loads(Path(path).expanduser().resolve().read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"JSON artifact must be an object: {path}")
    return payload


def _mean_or_none(values: list[float]) -> float | None:
    return mean(values) if values else None


__all__ = [
    "DEFAULT_ABLATIONS",
    "PairSpec",
    "RunArtifact",
    "aggregate_generation_efficiency",
    "discover_evaluation_inputs",
]
