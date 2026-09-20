"""Frozen-source discovery and final-input-only random-news snapshots."""

from __future__ import annotations

import copy
import os
import random
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

from .config import FINAL_SRC, RANDOM_NEWS_COUNT
from .utils import load_json, sha256_file, stable_seed, utc_now, write_json


if str(FINAL_SRC) not in sys.path:
    sys.path.insert(0, str(FINAL_SRC))

from Agent_Team.News_Agent.context_export import build_context_exports  # noqa: E402
from orchestration.config import load_run_config, peer_output_root  # noqa: E402
from orchestration.end_to_end_loop import materialize_reused_domain_snapshot  # noqa: E402
from orchestration.paths import RunPaths, resolve_run_paths  # noqa: E402


RANDOM_NEWS_PROTOCOL = "random_news_v2_final_input_only"


@dataclass(frozen=True)
class FrozenSource:
    root: Path
    company_name: str
    selected_date: str
    company_dir: Path
    full_manifest: Path
    target_config: Path
    peer_config: Path

    @property
    def peer_name(self) -> str:
        payload = load_json(self.peer_config)
        return str(payload.get("company_name") or "")


def locate_frozen_source(
    company_name: str,
    selected_date: str,
    source_roots: Iterable[Path],
) -> FrozenSource:
    failures: list[str] = []
    for raw_root in source_roots:
        root = Path(raw_root).expanduser().resolve()
        company_dir = root / company_name
        run_dir = company_dir / "runs" / selected_date
        full_manifest = run_dir / "full_pipeline_manifest.json"
        target_config = run_dir / "resolved_inputs" / "target_company.json"
        peer_config = run_dir / "resolved_inputs" / "peer_company.json"
        try:
            full_payload = load_json(full_manifest)
            run_payload = load_json(run_dir / "run_status.json")
        except (OSError, ValueError) as exc:
            failures.append(f"{root}: {exc}")
            continue
        if full_payload.get("status") != "success":
            failures.append(f"{root}: full manifest is not successful")
            continue
        if run_payload.get("status") != "success" or run_payload.get("pipeline_completed") is not True:
            failures.append(f"{root}: target domain snapshot is incomplete")
            continue
        if not target_config.is_file() or not peer_config.is_file():
            failures.append(f"{root}: resolved target/peer configs are missing")
            continue
        peer_name = str(load_json(peer_config).get("company_name") or "")
        peer_status = (
            peer_output_root(root, company_name)
            / peer_name
            / "runs"
            / selected_date
            / "run_status.json"
        )
        try:
            peer_payload = load_json(peer_status)
        except (OSError, ValueError) as exc:
            failures.append(f"{root}: peer snapshot unavailable: {exc}")
            continue
        if peer_payload.get("status") != "success" or peer_payload.get("pipeline_completed") is not True:
            failures.append(f"{root}: peer domain snapshot is incomplete")
            continue
        return FrozenSource(
            root=root,
            company_name=company_name,
            selected_date=selected_date,
            company_dir=company_dir,
            full_manifest=full_manifest,
            target_config=target_config,
            peer_config=peer_config,
        )
    detail = "\n  - ".join(failures) if failures else "no source roots supplied"
    raise FileNotFoundError(
        f"No successful frozen full snapshot for {company_name}/{selected_date}:\n  - {detail}"
    )


def select_random_events(
    report: dict[str, Any],
    *,
    seed: int,
    sample_size: int = RANDOM_NEWS_COUNT,
) -> tuple[dict[str, Any], dict[str, Any]]:
    pool = report.get("news_events_all")
    if not isinstance(pool, list):
        raise ValueError("report_context.news_events_all must be a list")
    unique: list[dict[str, Any]] = []
    seen: set[str] = set()
    for row in pool:
        if not isinstance(row, dict):
            continue
        event_id = str(row.get("event_id") or "").strip()
        if not event_id or event_id in seen:
            continue
        seen.add(event_id)
        unique.append(row)
    # news_events_all is stored in reranker order in FINAL. Canonicalizing by the
    # event identifier before drawing prevents that order from affecting the
    # realized fixed-seed sample.
    unique.sort(key=lambda row: str(row.get("event_id") or ""))
    if len(unique) < sample_size:
        raise ValueError(
            f"Random News requires exactly {sample_size} event clusters; pool has {len(unique)}"
        )
    chosen_indices = random.Random(seed).sample(range(len(unique)), sample_size)
    selected: list[dict[str, Any]] = []
    for canonical_pool_index in chosen_indices:
        row = copy.deepcopy(unique[canonical_pool_index])
        row["relevance_rank"] = 0
        scores = row.get("scores")
        if isinstance(scores, dict):
            row["scores"] = {key: 0.0 for key in scores}
        row["ablation_selection"] = {
            "method": "uniform_random_event_cluster",
            "canonical_pool_index": canonical_pool_index,
            "ranking_fields_used": False,
        }
        selected.append(row)

    modified = copy.deepcopy(report)
    # Preserve the full condition's weekly dense-15/rerank-5 pool. It is the
    # source of the frozen weekly summaries (subdata). Only the final raw News
    # Agent evidence is replaced, isolating ranked-20 versus random-20.
    modified["news_events_final"] = copy.deepcopy(selected)
    modified["news_events_topk"] = copy.deepcopy(selected)
    modified["ablation_selection"] = {
        "stage": "final_raw_uniform_random_event_cluster",
        "sample_size": sample_size,
        "seed": seed,
        "source_pool": "news_events_all",
        "source_pool_count": len(unique),
        "source_pool_order": "canonical_event_id",
        "sampling_unit": "semantic_event_cluster",
        "embedding_used_for_selection": False,
        "reranking_used_for_selection": False,
        "ranking_fields_ignored": True,
        "selection_scope": ["news_events_final", "news_events_topk", "recent_raw_input"],
        "summary_source": "unchanged_full_weekly_summaries",
        "weekly_event_pool_changed": False,
        "weekly_summary_changed": False,
        "upstream_full_event_clustering_reused": True,
    }
    audit = {
        **modified["ablation_selection"],
        "selected_event_ids_in_draw_order": [str(row.get("event_id")) for row in selected],
        "selected_representative_urls": [
            str((row.get("representative") or {}).get("url") or "") for row in selected
        ],
    }
    return modified, audit


def random_snapshot_plan(
    source: FrozenSource,
    *,
    base_seed: int,
    sample_size: int = RANDOM_NEWS_COUNT,
) -> dict[str, Any]:
    entities = []
    for role, config_path, source_root in _source_entities(source):
        config = load_run_config(config_path)
        paths = resolve_run_paths(config, source_root)
        report_path = _existing_report_context(paths, config.company_name)
        summary_path = paths.news_llm_period_summaries
        report = load_json(report_path)
        seed = stable_seed(base_seed, source.selected_date, config.company_name)
        _modified, audit = select_random_events(report, seed=seed, sample_size=sample_size)
        entities.append(
            {
                "role": role,
                "company_name": config.company_name,
                "source_report_context": str(report_path),
                "source_report_context_sha256": sha256_file(report_path),
                "source_weekly_summaries": str(summary_path),
                "source_weekly_summaries_sha256": sha256_file(summary_path),
                **audit,
            }
        )
    return {
        "protocol": RANDOM_NEWS_PROTOCOL,
        "target_company": source.company_name,
        "selected_date": source.selected_date,
        "base_seed": base_seed,
        "replicate_policy": "one sample per company reused across all LLM replicates",
        "entities": entities,
    }


def prepare_random_snapshot(
    source: FrozenSource,
    *,
    destination_root: Path,
    base_seed: int,
    model: str,
    sample_size: int = RANDOM_NEWS_COUNT,
) -> Path:
    destination_root = destination_root.expanduser().resolve()
    manifest_path = destination_root / "random_news_snapshot_manifest.json"
    plan = random_snapshot_plan(source, base_seed=base_seed, sample_size=sample_size)
    if manifest_path.is_file():
        existing = load_json(manifest_path)
        if _snapshot_matches(existing, plan, model) and _snapshot_statuses_exist(
            source, destination_root
        ):
            return destination_root

    destination_root.parent.mkdir(parents=True, exist_ok=True)
    temporary = Path(
        tempfile.mkdtemp(prefix=f".{destination_root.name}.building.", dir=destination_root.parent)
    )
    try:
        realized_entities: list[dict[str, Any]] = []
        for role, config_path, source_root in _source_entities(source):
            config = load_run_config(config_path)
            destination_entity_root = (
                temporary
                if role == "target"
                else peer_output_root(temporary, source.company_name)
            )
            destination_paths = resolve_run_paths(config, destination_entity_root)
            materialize_reused_domain_snapshot(
                run_config=config,
                source_root=source_root,
                destination_paths=destination_paths,
                expected_news_model=model,
            )
            source_paths = resolve_run_paths(config, source_root)
            source_report = _existing_report_context(source_paths, config.company_name)
            source_summary = source_paths.news_llm_period_summaries
            destination_report = destination_paths.news_report_context
            destination_summary = destination_paths.news_llm_period_summaries
            source_summary_sha256 = sha256_file(source_summary)
            if sha256_file(destination_summary) != source_summary_sha256:
                raise RuntimeError(
                    "Materialized weekly News summaries differ from the full source: "
                    f"{config.company_name}"
                )
            report = load_json(destination_report)
            seed = stable_seed(base_seed, source.selected_date, config.company_name)
            modified, audit = select_random_events(
                report, seed=seed, sample_size=sample_size
            )
            write_json(destination_report, modified)
            exports = _refresh_random_raw_export(
                destination_report=destination_report,
                destination_paths=destination_paths,
                model=model,
                audit=audit,
            )
            destination_summary_sha256 = sha256_file(destination_summary)
            if destination_summary_sha256 != source_summary_sha256:
                raise RuntimeError(
                    "random_news must not alter the frozen weekly News summaries: "
                    f"{config.company_name}"
                )
            write_json(
                destination_paths.run_status,
                {
                    "status": "success",
                    "pipeline_completed": True,
                    "snapshot_only": True,
                    "snapshot_protocol": RANDOM_NEWS_PROTOCOL,
                    "company_name": config.company_name,
                    "selected_date": config.selected_date,
                },
            )
            realized_entities.append(
                {
                    "role": role,
                    "company_name": config.company_name,
                    "source_root": str(Path(source_root).resolve()),
                    "source_report_context": str(source_report),
                    "source_report_context_sha256": sha256_file(source_report),
                    "source_weekly_summaries": str(source_summary),
                    "source_weekly_summaries_sha256": source_summary_sha256,
                    "destination_report_context": str(destination_report),
                    "destination_weekly_summaries": str(destination_summary),
                    "destination_weekly_summaries_sha256": destination_summary_sha256,
                    "recent_raw_input": exports["recent_raw_input_path"],
                    **audit,
                }
            )
        realized = {
            **plan,
            "status": "success",
            "created_at": utc_now(),
            "model": model,
            "entities": realized_entities,
        }
        write_json(temporary / "random_news_snapshot_manifest.json", realized)
        if destination_root.exists():
            stale = destination_root.with_name(
                destination_root.name + ".stale." + utc_now().replace(":", "").replace("+", "_")
            )
            os.replace(destination_root, stale)
        os.replace(temporary, destination_root)
        return destination_root
    except Exception:
        failed = temporary.with_name(temporary.name + ".failed")
        if temporary.exists():
            os.replace(temporary, failed)
        raise


def _source_entities(source: FrozenSource) -> list[tuple[str, Path, Path]]:
    return [
        ("target", source.target_config, source.root),
        (
            "peer",
            source.peer_config,
            peer_output_root(source.root, source.company_name),
        ),
    ]


def _refresh_random_raw_export(
    *,
    destination_report: Path,
    destination_paths: RunPaths,
    model: str,
    audit: dict[str, Any],
) -> dict[str, str]:
    """Regenerate only raw News input while preserving every summary artifact."""

    export_dir = destination_paths.news_context_export_week_dir
    with tempfile.TemporaryDirectory(
        prefix=".random_raw_export.",
        dir=export_dir.parent,
    ) as staging_dir:
        staged = build_context_exports(
            report_context_path=destination_report,
            output_dir=Path(staging_dir),
            granularity="week",
            period_count=14,
            raw_period_count=14,
            min_mention_count=1,
            llm_model=model,
            run_llm=False,
            split_by_period=False,
        )
        recent_raw_input = load_json(Path(staged["recent_raw_input_path"]))
        staged_manifest = load_json(Path(staged["manifest_path"]))

    raw_path = destination_paths.news_company_top20
    manifest_path = export_dir / "context_export_manifest.json"
    write_json(raw_path, recent_raw_input)

    # Keep the copied full manifest's summary/LLM provenance, but make its raw
    # input accounting describe the randomized final 20.
    manifest = load_json(manifest_path)
    for key in (
        "raw_periods_for_news_agent",
        "company_related_news_top_k",
        "company_related_news_count",
    ):
        manifest[key] = staged_manifest.get(key)
    manifest["ablation"] = {
        "protocol": RANDOM_NEWS_PROTOCOL,
        "selection": audit,
        "weekly_summary_artifacts_reused_unchanged": True,
        "recent_raw_input_path": str(raw_path),
    }
    write_json(manifest_path, manifest)
    return {
        "recent_raw_input_path": str(raw_path),
        "manifest_path": str(manifest_path),
        "llm_period_summaries_path": str(destination_paths.news_llm_period_summaries),
    }


def _existing_report_context(paths: RunPaths, company_name: str) -> Path:
    if paths.news_report_context.is_file():
        return paths.news_report_context
    legacy = (
        paths.output_root
        / "News"
        / "artifacts"
        / "reports"
        / "packs"
        / f"{company_name}_{paths._information_cutoff_date}"
        / "report_context.json"
    )
    if legacy.is_file():
        return legacy
    raise FileNotFoundError(f"News report_context is missing for {company_name}: {paths.output_root}")


def _snapshot_matches(existing: Any, plan: dict[str, Any], model: str) -> bool:
    if not isinstance(existing, dict) or existing.get("status") != "success":
        return False
    if existing.get("protocol") != plan.get("protocol"):
        return False
    if existing.get("model") != model or existing.get("base_seed") != plan.get("base_seed"):
        return False
    wanted = {
        (
            row["role"],
            row["source_report_context_sha256"],
            row["source_weekly_summaries_sha256"],
            tuple(row["selected_event_ids_in_draw_order"]),
        )
        for row in plan["entities"]
    }
    actual = {
        (
            row.get("role"),
            row.get("source_report_context_sha256"),
            row.get("source_weekly_summaries_sha256"),
            tuple(row.get("selected_event_ids_in_draw_order") or []),
        )
        for row in existing.get("entities") or []
        if isinstance(row, dict)
    }
    return wanted == actual


def _snapshot_statuses_exist(source: FrozenSource, destination_root: Path) -> bool:
    for role, config_path, _source_root in _source_entities(source):
        config = load_run_config(config_path)
        entity_root = (
            destination_root
            if role == "target"
            else peer_output_root(destination_root, source.company_name)
        )
        status_path = resolve_run_paths(config, entity_root).run_status
        if not status_path.is_file():
            return False
        status = load_json(status_path)
        if status.get("status") != "success" or status.get("pipeline_completed") is not True:
            return False
    return True
