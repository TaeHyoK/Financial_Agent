"""Annual ablation: matched articles and condition-specific monthly subdata summaries."""

from collections import Counter, defaultdict
from contextlib import contextmanager
import copy
from datetime import date, timedelta
import hashlib
import json
import os
from pathlib import Path
import random
import tempfile
from uuid import uuid4

from .snapshots import (
    FrozenSource, _existing_report_context, _source_entities, _snapshot_statuses_exist,
    load_run_config, resolve_run_paths, peer_output_root, materialize_reused_domain_snapshot,
)
from .utils import load_json, sha256_file, stable_seed, utc_now, write_json
from Agent_Team.News_Agent import context_export
from Agent_Team.News_Agent.collectors.report_snippets import require_prepared_summary_snippets
from Agent_Team.News_Agent.collectors.candidate_preparation import require_common_candidate_pool
from orchestration.usage_summary import summarize_execution_usage
from shared.time_windows import monthly_windows
from shared.news_selection import MONTHLY_NEWS_POLICY, event_period, validate_monthly_news


from shared.news_articles import ARTICLE_NEWS_POLICY, build_article_packet

ANNUAL_RANDOM_PROTOCOL = "random_news_v8_raw_primary_monthly_summary_subdata"


def _bounded_unique(rows, start, end):
    by_id = {}
    for row in rows:
        if not isinstance(row, dict) or not row.get("event_id"):
            continue
        stamp = str((row.get("representative") or {}).get("time") or "")[:10]
        try:
            parsed = date.fromisoformat(stamp)
        except ValueError:
            continue
        if start <= parsed <= end and int(row.get("mention_count") or 0) >= 1:
            key = str(row["event_id"])
            if key in by_id and by_id[key] != row:
                raise ValueError(f"Conflicting duplicate news event ID: {key}")
            by_id[key] = row
    return [by_id[key] for key in sorted(by_id)]


def _week(row):
    parsed = date.fromisoformat(str(row["representative"]["time"])[:10])
    year, week, _ = parsed.isocalendar()
    return f"{year:04d}-W{week:02d}"


def select_annual_random_events(report, *, seed: int, sample_size: int = 20):
    """Remove DART-based news selection, keeping Full's week/month input counts."""
    from Agent_Team.News_Agent.pipelines.run_news_pipeline import NEUTRAL_EVENT_POLICY
    if ((report.get("news_selection") or {}).get("prefilter_pool_policy") != NEUTRAL_EVENT_POLICY
            or not isinstance(report.get("news_events_prefilter"), list)):
        raise ValueError("Regenerate Full with a DART-free prefilter event pool before Random selection")
    modified, audit = select_filtered_random_events(report, seed=seed, sample_size=sample_size,
                                                    pool_key="news_events_prefilter")
    modified["news_events_all"] = copy.deepcopy(report["news_events_prefilter"])
    selection = modified["news_selection"]
    selection["selection_method"] = "random_prefilter_week_month_matched"
    selection["dart_news_selection"] = False
    # Ranking metadata describes Full, not the Random candidate pool.
    for key in ("section_weights", "section_score_policy", "weekly_embedding_candidates", "weekly_rerank_top_k", "weekly_top_k", "scored_event_count", "metric"):
        selection.pop(key, None)
    selection.update(candidate_article_count=selection.get("prefilter_article_count"),
                     seven_day_event_count=len(modified["news_events_all"]))
    audit.update(protocol=ANNUAL_RANDOM_PROTOCOL, source_pool="news_events_prefilter",
                 dart_company_filter_used=False, dart_similarity_used=False,
                 representative_policy="news_cluster_centroid",
                 treatment="DART company filtering and relevance selection jointly removed",
                 summary_company_profile="same DART profile as Full; selection-only ablation")
    return modified, audit


def select_filtered_random_events(report, *, seed: int, sample_size: int = 20, pool_key="news_events_all"):
    """Sampling primitive; default retains the old filtered-Random comparison."""
    for key in ("news_events_all", "news_events_weekly", "news_events_final"):
        if not isinstance(report.get(key), list):
            raise ValueError(f"Annual random selection requires {key}")
    end = date.fromisoformat(str(report["collect_date"])[:10])
    start = date.fromisoformat(monthly_windows(end + timedelta(days=1), 12)[0]["period_start"])
    pool = _bounded_unique(report[pool_key], start, end)
    full_weekly = _bounded_unique(report["news_events_weekly"], start, end)
    full_final = _bounded_unique(report["news_events_final"], start, end)
    pool_ids = {str(row["event_id"]) for row in report["news_events_all"]}
    weekly_ids = {str(row["event_id"]) for row in full_weekly}
    if not weekly_ids <= pool_ids or not {str(row["event_id"]) for row in full_final} <= weekly_ids:
        raise ValueError("Full weekly/final selection is not a subset of the common event pool")
    if (report.get("news_selection") or {}).get("raw_news_policy") in {MONTHLY_NEWS_POLICY, ARTICLE_NEWS_POLICY}:
        return _select_monthly_random(report, seed=seed, pool=pool, full_weekly=full_weekly,
                                      full_final=full_final, start=start, end=end)
    if len(full_final) > sample_size:
        raise ValueError("Full final news count exceeds the configured maximum")
    quotas = Counter(_week(row) for row in full_weekly)
    buckets = defaultdict(list)
    for row in pool:
        buckets[_week(row)].append(row)
    rng = random.Random(seed)
    chosen = []
    for week, count in sorted(quotas.items()):
        if len(buckets[week]) < count:
            raise ValueError(f"Invalid Full weekly count for {week}: {count}")
        chosen.extend(rng.sample(buckets[week], count))
    final = rng.sample(chosen, len(full_final))
    def clean(rows):
        return [{key: copy.deepcopy(value) for key, value in row.items()
                 if key not in {"relevance_rank", "scores", "ablation_selection"}} for row in rows]
    modified = copy.deepcopy(report)
    modified["news_events_weekly"] = clean(chosen)
    modified["news_events_final"] = clean(final)
    modified["news_events_topk"] = clean(final)
    audit = {
        "seed": seed, "source_pool": "news_events_all", "source_pool_count": len(pool),
        "source_pool_order": "canonical_event_id", "period_start": start.isoformat(), "period_end": end.isoformat(),
        "sampling_unit": "semantic_event_cluster", "weekly_counts": dict(sorted(quotas.items())),
        "weekly_selected_event_ids": [str(row["event_id"]) for row in chosen],
        "selected_event_ids_in_draw_order": [str(row["event_id"]) for row in final],
        "final_count": len(final), "ranking_fields_used": False,
        "summary_source": "new_monthly_summaries_from_random_weekly_events",
    }
    return modified, audit


def _select_monthly_random(report, *, seed, pool, full_weekly, full_final, start, end):
    windows = monthly_windows(end + timedelta(days=1), 12)
    stamp = lambda row: str(row["representative"]["time"])[:10]
    period = lambda row: event_period(stamp(row), windows)
    article_only = report["news_selection"].get("raw_news_policy") == ARTICLE_NEWS_POLICY
    if article_only:
        if {str(r["event_id"]) for r in full_final} != {str(r["event_id"]) for r in full_weekly}:
            raise ValueError("Article-only final input must equal the complete weekly selection")
        counts = Counter(period(row) for row in full_weekly)
        quotas = {window["period"]: counts[window["period"]] for window in windows}
    else:
        quotas = validate_monthly_news(full_final, end_exclusive=end + timedelta(days=1),
                                      time_of=stamp, id_of=lambda row: row["event_id"])
    # Month boundaries can bisect an ISO week. Match both dimensions before
    # summarization so a different random draw cannot shift monthly coverage.
    strata = Counter((_week(row), period(row)) for row in full_weekly)
    buckets = defaultdict(list)
    for row in pool:
        buckets[(_week(row), period(row))].append(row)
    rng, chosen = random.Random(seed), []
    for key, count in sorted(strata.items()):
        if len(buckets[key]) < count:
            raise ValueError(f"Insufficient random candidates for week-month stratum {key}")
        chosen.extend(rng.sample(buckets[key], count))
    final = []
    for key, count in quotas.items():
        candidates = [row for row in chosen if period(row) == key]
        if len(candidates) < count:
            raise ValueError(f"Insufficient monthly random candidates for {key}")
        final.extend(candidates if article_only else rng.sample(candidates, count))
    chosen.sort(key=lambda row: (stamp(row), str(row["event_id"])))
    final.sort(key=lambda row: (stamp(row), str(row["event_id"])))
    def clean(rows):
        return [{key: copy.deepcopy(value) for key, value in row.items()
                 if key not in {"relevance_rank", "scores", "ablation_selection"}} for row in rows]
    modified = copy.deepcopy(report)
    modified["news_events_weekly"] = clean(chosen)
    modified["news_events_final"] = clean(final)
    modified["news_events_topk"] = clean(final)
    modified["news_selection"]["selection_method"] = "random_week_month_matched"
    audit = {"seed": seed, "source_pool": "news_events_all", "source_pool_count": len(pool),
        "source_pool_order": "canonical_event_id", "period_start": start.isoformat(), "period_end": end.isoformat(),
        "sampling_unit": "semantic_event_cluster", "raw_news_policy": report["news_selection"]["raw_news_policy"],
        "weekly_counts": dict(sorted(Counter(_week(row) for row in chosen).items())),
        "week_month_counts": [{"week": week, "period": month, "count": n} for (week, month), n in sorted(strata.items())],
        "monthly_final_counts": quotas,
        "weekly_selected_event_ids": [str(row["event_id"]) for row in chosen],
        "selected_event_ids_in_draw_order": [str(row["event_id"]) for row in final],
        "final_order": "chronological", "final_count": len(final), "ranking_fields_used": False,
        "summary_source": "new_monthly_summaries_from_random_week_month_matched_events"}
    return modified, audit


def require_monthly_news_source(source):
    """Reject old global-20 sources before any condition starts paid generation."""
    for role, config_path, source_root in _source_entities(source):
        config = load_run_config(config_path)
        paths = resolve_run_paths(config, source_root)
        report = load_json(_existing_report_context(paths, config.company_name))
        require_common_candidate_pool(report)
        require_prepared_summary_snippets(report)
        if (report.get("news_selection") or {}).get("raw_news_policy") == ARTICLE_NEWS_POLICY:
            if load_json(paths.news_articles) != build_article_packet(report):
                raise ValueError(f"{role}: exported articles differ from the weekly selection")
            select_annual_random_events(report, seed=0)
            continue
        if (report.get("news_selection") or {}).get("raw_news_policy") != MONTHLY_NEWS_POLICY:
            raise ValueError(f"{role}: regenerate the common Full source with monthly raw news before annual ablation")
        select_annual_random_events(report, seed=0)  # Validate the new pool/quota contract before paid work.
        export = load_json(paths.news_context_export_week_dir / "context_export_manifest.json")
        if (export.get("metadata") or {}).get("raw_news_policy") != MONTHLY_NEWS_POLICY:
            raise ValueError(f"{role}: monthly news exports do not match the current selection")
        end = date.fromisoformat(report['collect_date']) + timedelta(days=1)
        validate_monthly_news(report['news_events_final'],end_exclusive=end,
            time_of=lambda row: row['representative']['time'],id_of=lambda row: row['event_id'])
        raw = load_json(paths.news_company_top20)
        if {str(row['event_id']) for row in raw.get('events',[])} != {str(row['event_id']) for row in report['news_events_final']}:
            raise ValueError(f"{role}: exported monthly news differs from the selected events")
        expected_snippets = {str(row['event_id']): row['representative'].get('snippet') or '' for row in report['news_events_final']}
        if any((row.get('snippet') or '') != expected_snippets[str(row['event_id'])] for row in raw.get('events', [])):
            raise ValueError(f"{role}: exported raw snippets differ from the prepared source")


def annual_random_plan(source: FrozenSource, *, base_seed: int, replicate: int, sample_size: int = 20, code_hash: str = ""):
    entities = []
    for role, config_path, source_root in _source_entities(source):
        config = load_run_config(config_path)
        paths = resolve_run_paths(config, source_root)
        report_path = _existing_report_context(paths, config.company_name)
        report = load_json(report_path)
        require_common_candidate_pool(report)
        require_prepared_summary_snippets(report)
        if config.selected_date != source.selected_date or report.get("collect_date") != config.information_cutoff_date_iso:
            raise ValueError("Random source news/config dates do not match the experiment cutoff")
        seed = stable_seed(base_seed, source.selected_date, f"{config.company_name}:replicate:{replicate}")
        _, audit = select_annual_random_events(report, seed=seed, sample_size=sample_size)
        export_manifest = load_json(paths.news_context_export_week_dir / "context_export_manifest.json")
        metadata = export_manifest.get("metadata") or {}
        if metadata.get("granularity") != "month" or metadata.get("period_count") != 12:
            raise ValueError("Annual random news requires Full monthly-12 context exports")
        article_only = metadata.get("raw_news_policy") == ARTICLE_NEWS_POLICY
        split = bool((export_manifest.get("llm") or {}).get("split_by_period"))
        if article_only:
            if load_json(paths.news_articles) != build_article_packet(report):
                raise ValueError("Article packet differs from the selected source")
        recorded_request = load_json(paths.news_context_export_week_dir / "llm_summary_request.json")
        current_request = context_export._build_llm_summary_request(
            load_json(paths.news_context_export_week_dir / "summary_prompt_input.json"),
            str(recorded_request.get("model") or ""),
        )
        if recorded_request.get("messages") != current_request["messages"]:
            raise ValueError("Full summaries use an older input/prompt contract; regenerate the common Full summaries first")
        files = [config_path, report_path, paths.dart_master, paths.valuation_snapshot,
                 paths.yfinance_dir / "market_full_dataset.json", paths.yfinance_dir / "market_full_dataset.csv",
                 paths.yfinance_dir / "manifest.json", paths.market_summary_dated]
        files.extend(sorted(paths.news_context_export_dir.rglob("*.json")))
        entities.append({"role": role, "company_name": config.company_name,
                         "input_hashes": {str(path): sha256_file(path) for path in files}, "split_by_period": split, "article_only": article_only, **audit})
    recipe = hashlib.sha256(Path(__file__).read_bytes() + Path(context_export.__file__).read_bytes()).hexdigest()
    return {"protocol": ANNUAL_RANDOM_PROTOCOL, "recipe_sha256": recipe, "generation_code_hash": code_hash, "base_seed": base_seed,
            "replicate": replicate, "selected_date": source.selected_date,
            "replicate_policy": "different fixed seed per company and replicate; retry reuses same sample",
            "entities": entities}


@contextmanager
def _telemetry(path, execution_id, config, role):
    values = {"LLM_USAGE_MANIFEST": str(path), "LLM_EXECUTION_ID": execution_id,
              "LLM_RUN_ID": config.run_key, "LLM_RUN_ROLE": role, "LLM_COMPANY_NAME": config.company_name}
    old = {key: os.environ.get(key) for key in values}
    os.environ.update(values)
    try:
        yield
    finally:
        for key, value in old.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value


def _rebase_paths(root, destination):
    def rewrite(value):
        if isinstance(value, dict):
            return {key: rewrite(item) for key, item in value.items()}
        if isinstance(value, list):
            return [rewrite(item) for item in value]
        if isinstance(value, str) and value.startswith(str(root) + "/"):
            return str(destination) + value[len(str(root)):]
        return value
    for path in root.rglob("*.json"):
        original = load_json(path)
        changed = rewrite(original)
        if changed != original:
            write_json(path, changed)


def prepare_annual_random_snapshot(source: FrozenSource, *, destination_root: Path, base_seed: int,
                                   replicate: int, model: str, env_file: Path, sample_size: int = 20, code_hash: str = ""):
    plan = annual_random_plan(source, base_seed=base_seed, replicate=replicate, sample_size=sample_size, code_hash=code_hash)
    cache_key = hashlib.sha256(json.dumps({"plan": plan, "model": model}, sort_keys=True).encode()).hexdigest()
    destination_root = destination_root.resolve()
    manifest_path = destination_root / "random_news_snapshot_manifest.json"
    if manifest_path.exists():
        existing = load_json(manifest_path)
        if (existing.get("cache_key") == cache_key and existing.get("status") == "success"
                and _snapshot_statuses_exist(source, destination_root)
                and all(Path(path).is_file() and sha256_file(Path(path)) == digest
                        for path, digest in (existing.get("output_hashes") or {}).items())
                and existing.get("output_hashes")):
            return destination_root
    destination_root.parent.mkdir(parents=True, exist_ok=True)
    temporary = Path(tempfile.mkdtemp(prefix=f".{destination_root.name}.building.", dir=destination_root.parent))
    usage_path = temporary / "random_news_usage.jsonl"
    # The sample/cache identity is stable; each paid preparation attempt is not.
    execution_id = f"random-preprocessing:{cache_key}:{uuid4().hex}"
    entities = []
    expected_calls = {row["role"]: (12 if row["split_by_period"] else 1) for row in plan["entities"]}
    try:
        for role, config_path, source_root in _source_entities(source):
            config = load_run_config(config_path)
            target_root = temporary if role == "target" else peer_output_root(temporary, source.company_name)
            paths = resolve_run_paths(config, target_root)
            materialize_reused_domain_snapshot(run_config=config, source_root=source_root,
                                              destination_paths=paths, expected_news_model=model)
            # This snapshot becomes a source on the next pipeline invocation.
            # The low-level copier does not create an orchestration run config.
            write_json(paths.run_config_copy, config.raw)
            audit = next(row for row in plan["entities"] if row["role"] == role)
            modified, selected = select_annual_random_events(load_json(paths.news_report_context),
                                                            seed=audit["seed"], sample_size=sample_size)
            require_common_candidate_pool(modified)
            selected["snippet_processing"] = {"source": "frozen_common_candidate_pool", "network_requests": 0}
            write_json(paths.news_report_context, modified)
            with _telemetry(usage_path, execution_id, config, role):
                exports = context_export.build_context_exports(
                    report_context_path=paths.news_report_context, output_dir=paths.news_context_export_week_dir,
                    granularity="month", period_count=12, raw_period_count=12, min_mention_count=1,
                    llm_model=model, run_llm=True, split_by_period=audit["split_by_period"], env_path=env_file,
                )
            # Validate provenance against Random's request, never Full's copied summaries.
            summary = load_json(paths.news_llm_period_summaries)
            request = load_json(Path(exports["llm_summary_request_path"]))
            if (not isinstance(summary.get("output"), dict) or summary.get("model") != model
                    or summary.get("source_request_sha256") != context_export.summary_request_hash(request)):
                raise ValueError("Random monthly summary did not produce a matching model output")
            context_export._attach_source_event_ids(summary.get("output"), request)
            write_json(paths.news_llm_period_summaries, summary)
            write_json(paths.run_status, {"status": "success", "pipeline_completed": True, "snapshot_only": True,
                                         "snapshot_protocol": ANNUAL_RANDOM_PROTOCOL, "company_name": config.company_name,
                                         "selected_date": config.selected_date})
            entities.append({"role": role, "company_name": config.company_name, "exports": exports, **selected})
        usage = summarize_execution_usage(usage_path, execution_id=execution_id, pipeline_completed=True,
                                          expected_logical_calls_by_role=expected_calls)
        write_json(temporary / "random_news_preprocessing_usage.json", usage)
        write_json(temporary / "random_news_snapshot_manifest.json", {
            "status": "success", "protocol": ANNUAL_RANDOM_PROTOCOL, "plan": plan, "cache_key": cache_key,
            "model": model, "created_at": utc_now(), "entities": entities,
            "usage_summary": str(temporary / "random_news_preprocessing_usage.json"),
        })
        _rebase_paths(temporary, destination_root)
        output_hashes = {str(destination_root / path.relative_to(temporary)): sha256_file(path)
                         for path in sorted(temporary.rglob("*")) if path.is_file()
                         and path.suffix in {".json", ".csv", ".jsonl"} and path.name != "random_news_snapshot_manifest.json"}
        manifest = load_json(temporary / "random_news_snapshot_manifest.json")
        manifest["output_hashes"] = output_hashes
        write_json(temporary / "random_news_snapshot_manifest.json", manifest)
        if destination_root.exists():
            stale = destination_root.with_name(destination_root.name + ".stale." + temporary.name.rsplit(".", 1)[-1])
            os.replace(destination_root, stale)
        os.replace(temporary, destination_root)
        return destination_root
    except Exception:
        write_json(temporary / "random_news_preprocessing_usage.json", summarize_execution_usage(
            usage_path, execution_id=execution_id, pipeline_completed=False,
            expected_logical_calls_by_role=expected_calls))
        raise
