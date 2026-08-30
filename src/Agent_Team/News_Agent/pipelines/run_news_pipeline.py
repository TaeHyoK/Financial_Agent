"""News pipeline: collect -> seven-day event merge -> rerank -> select -> outputs."""

from __future__ import annotations

from dataclasses import asdict
from datetime import date, datetime, timedelta, timezone
from typing import Any

import numpy as np

from ..collectors.google_news_collector import GoogleNewsCollector
from ..dart.schemas import NewsEventRecord, RawNewsRecord
from ..io.storage import save_json, save_parquet, read_jsonl
from ..ranking.clustering import cluster_embeddings
from ..ranking.embedding import EmbeddingModel
from ..ranking.rerank import Reranker, minmax_normalize
from ..ranking.scoring import mention_score, time_score, final_score
from .utils import resolve_data_root


ARTICLE_PREVIEW_LIMIT = None


def _artifact_dirname(company_name: str, collect_date: date) -> str:
    safe_name = company_name.strip()
    safe_name = "".join("_" if ch in '\\/:*?"<>|' else ch for ch in safe_name)
    safe_name = "_".join(part for part in safe_name.split() if part).strip("._")
    return f"{safe_name or 'company'}_{collect_date.strftime('%Y%m%d')}"


def _build_article_preview(record: RawNewsRecord) -> dict[str, str]:
    snippet = (record.snippet or "").strip()
    return {
        "article_id": record.article_id,
        "title": record.title,
        "snippet": snippet,
        "time": record.article_date or "",
        "source": record.source or "",
        "url": record.url,
    }


def _build_article_previews(
    records: list[RawNewsRecord],
    limit: int | None = ARTICLE_PREVIEW_LIMIT,
) -> list[dict[str, str]]:
    previews: list[dict[str, str]] = []
    seen: set[tuple[str, str, str]] = set()
    for record in records:
        key = (
            (record.title or "").strip().lower(),
            (record.source or "").strip().lower(),
            record.article_date or "",
        )
        if not key[0] or key in seen:
            continue
        seen.add(key)
        previews.append(_build_article_preview(record))
        if limit is not None and len(previews) >= limit:
            break
    return previews


def _build_event_timeline(
    records: list[RawNewsRecord],
    *,
    article_dense_by_id: dict[str, float],
) -> list[dict[str, str]]:
    """Keep one report-relevant headline per publication date for an event."""

    records_by_date: dict[str, list[RawNewsRecord]] = {}
    for record in records:
        article_date = str(record.article_date or "").strip()
        if not article_date:
            continue
        records_by_date.setdefault(article_date, []).append(record)

    if len(records_by_date) <= 1:
        return []

    timeline: list[dict[str, str]] = []
    for article_date in sorted(records_by_date):
        daily_record = max(
            records_by_date[article_date],
            key=lambda record: (
                float(article_dense_by_id.get(record.article_id, float("-inf"))),
                bool((record.snippet or "").strip()),
                len((record.title or "").strip()),
                record.article_id,
            ),
        )
        timeline.append(
            {
                "date": article_date,
                "title": (daily_record.title or "").strip(),
            }
        )
    return timeline


def _build_report_event(
    event: NewsEventRecord,
    *,
    raw_records_by_id: dict[str, RawNewsRecord],
    article_dense_by_id: dict[str, float],
    relevance_rank: int,
) -> dict[str, Any]:
    evidence = []
    chunk_ids = event.matched_chunk_ids.split(",") if event.matched_chunk_ids else []
    chunk_texts = event.matched_chunk_texts.split(" ||| ") if event.matched_chunk_texts else []
    chunk_sections = (
        event.matched_chunk_section_types.split(",")
        if event.matched_chunk_section_types
        else []
    )
    chunk_similarities = (
        [float(value) for value in event.matched_chunk_similarities.split(",")]
        if event.matched_chunk_similarities
        else []
    )
    for chunk_id, chunk_text, section, similarity in zip(
        chunk_ids,
        chunk_texts,
        chunk_sections,
        chunk_similarities,
    ):
        evidence.append(
            {
                "chunk_id": chunk_id,
                "section_type": section,
                "text": chunk_text,
                "similarity": similarity,
            }
        )
    member_article_ids = list(event.member_article_ids or [])
    member_records = [
        raw_records_by_id[article_id]
        for article_id in member_article_ids
        if article_id in raw_records_by_id
    ]
    return {
        "event_id": event.event_id,
        "relevance_rank": relevance_rank,
        "mention_count": event.mention_count,
        "representative": {
            "title": event.representative_title,
            "snippet": event.representative_snippet,
            "source": event.representative_source,
            "time": event.representative_article_date,
            "url": event.representative_url,
        },
        "event_timeline": _build_event_timeline(
            member_records,
            article_dense_by_id=article_dense_by_id,
        ),
        "articles": _build_article_previews(member_records),
        "scores": {
            "rel_dense": event.rel_dense,
            "rel_rerank": event.rel_rerank,
            "section_score": event.section_score,
            "global_section_score": event.global_section_score,
            "time_score": event.time_score,
            "impact_score": event.impact_score,
            "mention_score": event.mention_score,
            "final_score": event.final_score,
        },
        "evidence": evidence,
        "members": list(event.member_urls or []),
        "member_article_ids": member_article_ids,
    }


def _dedupe_records(records: list[RawNewsRecord], dedup_on_url: bool) -> list[RawNewsRecord]:
    deduped: list[RawNewsRecord] = []
    seen_url: set[str] = set()
    seen_key: set[tuple[str, str, str]] = set()
    for record in records:
        if dedup_on_url:
            if record.url in seen_url:
                continue
            seen_url.add(record.url)
        else:
            key = (
                (record.title or "").strip().lower(),
                (record.source or "").strip().lower(),
                record.article_date or "",
            )
            if key in seen_key:
                continue
            seen_key.add(key)
        deduped.append(record)
    return deduped


def _reindex_records(records: list[RawNewsRecord]) -> list[RawNewsRecord]:
    reindexed: list[RawNewsRecord] = []
    for idx, record in enumerate(records, start=1):
        payload = asdict(record)
        payload["article_id"] = str(idx)
        reindexed.append(RawNewsRecord(**payload))
    return reindexed


def _clustering_week_bucket(record: RawNewsRecord, collect_date: date) -> str:
    """ISO calendar-week key for seven-day semantic duplicate merging."""

    if record.article_date:
        return _iso_week_key(record.article_date, collect_date)
    metadata = record.metadata or {}
    query_window_end = metadata.get("query_window_end")
    if isinstance(query_window_end, str) and query_window_end:
        return _iso_week_key(query_window_end, collect_date)
    return _iso_week_key(None, collect_date)


def _event_date_ordinal(value: str | None) -> int:
    try:
        return date.fromisoformat(str(value or "")).toordinal()
    except ValueError:
        return 0


def _rank_events_after_rerank(
    events: list[NewsEventRecord],
    top_k: int | None,
) -> tuple[list[NewsEventRecord], list[NewsEventRecord]]:
    """Rank deduplicated events by their representative article reranker score.

    ``rel_rerank`` still contains the raw cross-encoder score when this helper is
    called.  Dense relevance and recency are deterministic tie breakers only; URL
    and event id make the final ordering stable across repeated runs.
    """

    if top_k is not None and int(top_k) <= 0:
        raise ValueError("event_top_k must be >= 1")

    ranked = sorted(
        events,
        key=lambda event: (
            -float(event.rel_rerank),
            -float(event.rel_dense),
            -_event_date_ordinal(event.representative_article_date),
            str(event.representative_url or ""),
            str(event.event_id),
        ),
    )
    if top_k is None:
        return ranked, list(ranked)
    return ranked, ranked[: int(top_k)]


def _iso_week_key(value: str | None, fallback: date) -> str:
    """Return an ISO year-week key for stable calendar-week selection."""

    try:
        parsed = date.fromisoformat(str(value or "")[:10])
    except ValueError:
        parsed = fallback
    iso_year, iso_week, _ = parsed.isocalendar()
    return f"{iso_year:04d}-W{iso_week:02d}"


def _weekly_embedding_candidates(
    events: list[NewsEventRecord],
    *,
    collect_date: date,
    candidates_per_week: int,
) -> tuple[dict[str, list[NewsEventRecord]], dict[str, int]]:
    """Select dense-similarity candidates independently inside each ISO week."""

    if candidates_per_week <= 0:
        raise ValueError("weekly_embedding_candidates must be >= 1")
    grouped: dict[str, list[NewsEventRecord]] = {}
    for event in events:
        week = _iso_week_key(event.representative_article_date, collect_date)
        grouped.setdefault(week, []).append(event)

    selected: dict[str, list[NewsEventRecord]] = {}
    ranks: dict[str, int] = {}
    for week, week_events in sorted(grouped.items()):
        ranked = sorted(
            week_events,
            key=lambda event: (
                -float(event.rel_dense),
                -_event_date_ordinal(event.representative_article_date),
                str(event.representative_url or ""),
                str(event.event_id),
            ),
        )
        selected[week] = ranked[:candidates_per_week]
        for rank, event in enumerate(ranked, start=1):
            ranks[event.event_id] = rank
    return selected, ranks


def _weekly_rerank_selection(
    candidates_by_week: dict[str, list[NewsEventRecord]],
    *,
    events_by_id: dict[str, NewsEventRecord],
    events_per_week: int,
) -> tuple[list[NewsEventRecord], dict[str, int]]:
    """Retain at most ``events_per_week`` cross-encoder results per week."""

    if events_per_week <= 0:
        raise ValueError("weekly_rerank_top_k must be >= 1")
    selected: list[NewsEventRecord] = []
    ranks: dict[str, int] = {}
    for week, candidates in sorted(candidates_by_week.items()):
        scored = [events_by_id[event.event_id] for event in candidates]
        ranked, week_selected = _rank_events_after_rerank(scored, events_per_week)
        for rank, event in enumerate(ranked, start=1):
            ranks[event.event_id] = rank
        selected.extend(week_selected)
    return selected, ranks


def _cluster_articles_within_week(
    records: list[RawNewsRecord],
    embeddings: np.ndarray,
    *,
    collect_date: date,
    time_window_hours: float,
    min_cluster_size: int,
    min_samples: int,
    similarity_threshold: float = 0.88,
) -> dict[int, list[int]]:
    """Merge semantic duplicates across dates, bounded to one ISO week."""

    timestamps: list[datetime] = []
    bucket_to_indices: dict[str, list[int]] = {}
    for idx, record in enumerate(records):
        if record.article_date:
            article_dt = datetime.combine(
                date.fromisoformat(record.article_date),
                datetime.min.time(),
                tzinfo=timezone.utc,
            )
        else:
            article_dt = datetime.combine(
                collect_date,
                datetime.min.time(),
                tzinfo=timezone.utc,
            )
        timestamps.append(article_dt)
        bucket = _clustering_week_bucket(record, collect_date)
        bucket_to_indices.setdefault(bucket, []).append(idx)

    clusters: dict[int, list[int]] = {}
    cluster_key_to_global_id: dict[tuple[str, int], int] = {}
    next_cluster_id = 1

    for bucket, bucket_indices in sorted(bucket_to_indices.items()):
        bucket_embeddings = embeddings[bucket_indices]
        bucket_timestamps = [timestamps[i] for i in bucket_indices]
        bucket_result = cluster_embeddings(
            bucket_embeddings,
            bucket_timestamps,
            time_window_hours=time_window_hours,
            min_cluster_size=min_cluster_size,
            min_samples=min_samples,
            similarity_threshold=similarity_threshold,
        )
        bucket_labels = bucket_result.labels
        max_label = max(bucket_labels) if bucket_labels else -1
        for local_idx, label in enumerate(bucket_labels):
            global_article_idx = bucket_indices[local_idx]
            local_cluster_id = label if label != -1 else max_label + local_idx + 1
            cluster_key = (bucket, int(local_cluster_id))
            global_cluster_id = cluster_key_to_global_id.get(cluster_key)
            if global_cluster_id is None:
                global_cluster_id = next_cluster_id
                cluster_key_to_global_id[cluster_key] = global_cluster_id
                next_cluster_id += 1
            clusters.setdefault(global_cluster_id, []).append(global_article_idx)

    return clusters


def run_news_window(
    *,
    config: dict[str, Any],
    collect_date: date,
    company_id: str,
    company_name: str,
    report_key: str,
    query_override: str | None = None,
    collection_days_override: int | None = None,
    max_results_override: int | None = None,
    total_max_results_override: int | None = None,
    dedup_on_url_override: bool | None = None,
) -> dict[str, Any]:
    data_root = resolve_data_root(config)

    news_cfg = config.get("news", {})
    collector = GoogleNewsCollector(
        language=news_cfg.get("query_language", "ko"),
    )
    query = query_override or company_name
    collection_days = (
        int(collection_days_override)
        if collection_days_override is not None
        else int(news_cfg.get("collection_days", 365))
    )
    if collection_days <= 0:
        raise ValueError("collection_days must be >= 1")

    max_results = max_results_override if max_results_override is not None else news_cfg.get("max_results")
    weekly_embedding_candidate_k = int(
        news_cfg.get("weekly_embedding_candidates", 15)
    )
    weekly_rerank_top_k = int(news_cfg.get("weekly_rerank_top_k", 5))
    if weekly_embedding_candidate_k <= 0:
        raise ValueError("weekly_embedding_candidates must be >= 1")
    if weekly_rerank_top_k <= 0:
        raise ValueError("weekly_rerank_top_k must be >= 1")
    if weekly_rerank_top_k > weekly_embedding_candidate_k:
        raise ValueError(
            "weekly_rerank_top_k cannot exceed weekly_embedding_candidates"
        )

    # ``total_max_results`` is retained as a compatibility alias. It now means
    # the final global News-Agent article/event budget after weekly selection.
    configured_event_top_k = news_cfg.get("event_top_k")
    if configured_event_top_k is None:
        configured_event_top_k = news_cfg.get("total_max_results")
    event_top_k = (
        total_max_results_override
        if total_max_results_override is not None
        else configured_event_top_k
    )
    if event_top_k is not None and int(event_top_k) <= 0:
        raise ValueError("event_top_k must be >= 1")
    selection_method = (
        f"weekly_dense_top{weekly_embedding_candidate_k}_"
        f"rerank_top{weekly_rerank_top_k}_"
        f"global_top{int(event_top_k) if event_top_k is not None else 'all'}"
    )
    dedup_on_url = (
        bool(dedup_on_url_override)
        if dedup_on_url_override is not None
        else bool(news_cfg.get("dedup_on_url", True))
    )
    collection_chunk_days = max(1, int(news_cfg.get("collection_chunk_days", 7)))

    # Query bounded seven-day windows. This retains coverage across the full
    # 90-day interval without paying URL-decoding and publisher-fetch costs for
    # every unranked article. Exact-date semantic merging still happens below.
    raw_records: list[RawNewsRecord] = []
    collection_notes: list[str] = []
    remaining_days = collection_days
    chunk_end = collect_date
    while remaining_days > 0:
        chunk_days = min(collection_chunk_days, remaining_days)
        chunk_start = chunk_end - timedelta(days=chunk_days - 1)
        chunk_records, chunk_meta = collector.collect(
            query=query,
            collect_date=chunk_end,
            lookback_days=chunk_days - 1,
            max_results=max_results,
            dedup_on_url=dedup_on_url,
            enrich=False,
        )
        chunk_label = f"{chunk_start.isoformat()}..{chunk_end.isoformat()}"
        chunk_notes = [
            f"{chunk_label}::{note}"
            for note in chunk_meta.get("collection_notes", [])
        ]
        collection_notes.extend(chunk_notes)
        for record in chunk_records:
            payload = asdict(record)
            payload["metadata"] = {
                **(record.metadata or {}),
                "query_window_start": chunk_start.isoformat(),
                "query_window_end": chunk_end.isoformat(),
                "window_collect_date": collect_date.isoformat(),
                "window_start_date": (collect_date - timedelta(days=collection_days - 1)).isoformat(),
                "collection_days": collection_days,
            }
            raw_records.append(RawNewsRecord(**payload))
        remaining_days -= chunk_days
        chunk_end = chunk_start - timedelta(days=1)

    window_start_date = collect_date - timedelta(days=collection_days - 1)
    window_end_date = collect_date
    filtered_records: list[RawNewsRecord] = []
    for record in raw_records:
        if not record.article_date:
            filtered_records.append(record)
            continue
        article_date = date.fromisoformat(record.article_date)
        if window_start_date <= article_date <= window_end_date:
            filtered_records.append(record)
    raw_records = filtered_records

    raw_records = _reindex_records(
        _dedupe_records(raw_records, dedup_on_url=dedup_on_url)
    )
    collected_unique_count = len(raw_records)

    if raw_records:
        enriched_records: list[RawNewsRecord] = []
        for record in raw_records:
            record_dict = asdict(record)
            record_dict["metadata"] = {
                **(record.metadata or {}),
                "collection_notes": collection_notes,
                "collection_total": collected_unique_count,
                "collection_unique_total": collected_unique_count,
                "event_selection_stage": "weekly_dense_then_rerank",
                "event_selection_top_k": (
                    int(event_top_k) if event_top_k is not None else None
                ),
            }
            enriched_records.append(RawNewsRecord(**record_dict))
        raw_records = enriched_records

    artifact_dir = _artifact_dirname(company_name, collect_date)
    raw_output_dir = data_root / "news" / "raw" / artifact_dir
    raw_news_candidates_path = raw_output_dir / "raw_news_candidates.parquet"
    raw_news_path = raw_output_dir / "raw_news.parquet"
    article_ranking_path = raw_output_dir / "article_ranking.parquet"
    save_parquet([asdict(record) for record in raw_records], raw_news_candidates_path)

    if not raw_records:
        save_parquet([], raw_news_path)
        save_parquet([], article_ranking_path)
        return {
            "raw_news_candidates_path": str(raw_news_candidates_path),
            "raw_news_path": str(raw_news_path),
            "article_ranking_path": str(article_ranking_path),
            "news_events_path": "",
            "all_news_events_path": "",
            "weekly_news_events_path": "",
            "event_ranking_path": "",
            "report_context_path": "",
            "collected_unique_count": 0,
            "raw_news_count": 0,
            "raw_news_count_before_total_cap": 0,
            "news_event_count_before_top_k": 0,
            "news_event_count": 0,
            "query_used": query,
            "event_top_k": (
                int(event_top_k) if event_top_k is not None else None
            ),
            "total_max_results": (
                int(event_top_k) if event_top_k is not None else None
            ),
            "weekly_embedding_candidates": weekly_embedding_candidate_k,
            "weekly_rerank_top_k": weekly_rerank_top_k,
            "selection_stage": "weekly_dense_then_rerank",
            "selection_method": selection_method,
            "semantic_dedup_window_days": 7,
            "cross_date_clustering": True,
        }

    model_cfg = config.get("models", {})
    embedder = EmbeddingModel(
        model_cfg.get("embedding_model_name", "BAAI/bge-m3"),
        device=model_cfg.get("device", "cpu"),
        batch_size=int(model_cfg.get("batch_size", 32)),
    )
    news_embeddings = embedder.encode([record.doc_text for record in raw_records])
    raw_records_by_id = {record.article_id: record for record in raw_records}

    # Use the exact context DB from this run's report_key.
    context_db_path = data_root / "db" / "corporate_context" / company_id / report_key / "corporate_context_db.jsonl"
    if not context_db_path.exists():
        raise FileNotFoundError(f"Corporate context DB not found for report_key={report_key}: {context_db_path}")

    context_records = read_jsonl(context_db_path)
    if not context_records:
        raise RuntimeError(f"Corporate context DB is empty: {context_db_path}")

    context_texts = [rec["text"] for rec in context_records]
    context_embeddings = np.array([rec["embedding"] for rec in context_records], dtype=np.float32)
    context_section_types = [rec["section_type"] for rec in context_records]
    context_chunk_ids = [rec["chunk_id"] for rec in context_records]
    context_importance = [float(rec.get("score_total", 1.0)) for rec in context_records]
    section_to_context_indices: dict[str, list[int]] = {}
    for idx, section in enumerate(context_section_types):
        section_to_context_indices.setdefault(section, []).append(idx)
    sorted_sections = sorted(section_to_context_indices.keys())

    scoring_cfg = config.get("scoring", {})
    alpha = float(scoring_cfg.get("alpha", 0.75))
    beta = float(scoring_cfg.get("beta", 0.15))
    gamma = float(scoring_cfg.get("gamma", 0.10))
    delta = float(scoring_cfg.get("delta", 0.0))
    tau_hours = float(scoring_cfg.get("tau_hours", 48))
    mention_transform = scoring_cfg.get("mention_transform", "log1p")
    impact_topk = max(int(scoring_cfg.get("impact_topk", 3)), 1)

    # Compute inexpensive embedding similarity for every article first. The
    # cross-encoder is intentionally deferred until weekly candidates exist.
    article_scores: list[dict[str, Any]] = []
    for idx, record in enumerate(raw_records):
        article_vec = news_embeddings[idx]
        rel_dense_scores = np.dot(context_embeddings, article_vec) / (
            np.linalg.norm(context_embeddings, axis=1) * np.linalg.norm(article_vec) + 1e-8
        )
        top_indices = np.argsort(rel_dense_scores)[-impact_topk:][::-1].tolist()

        rel_dense = float(rel_dense_scores[top_indices[0]]) if top_indices else 0.0
        impact_raw = float(sum(float(rel_dense_scores[i]) * float(context_importance[i]) for i in top_indices))

        section_best: dict[str, float] = {}
        for section in sorted_sections:
            section_indices = section_to_context_indices.get(section, [])
            if not section_indices:
                continue
            section_best[section] = float(np.max(rel_dense_scores[section_indices]))

        article_scores.append(
            {
                "article_idx": idx,
                "top_indices": top_indices,
                "rel_dense": rel_dense,
                "impact_raw": impact_raw,
                "section_best": section_best,
                "section_raw": 0.0,
            }
        )

    # Normalize section similarities per-section, then aggregate equally.
    for section in sorted_sections:
        section_values = [float(score["section_best"].get(section, 0.0)) for score in article_scores]
        normalized_values = minmax_normalize(section_values)
        for article_idx, score in enumerate(article_scores):
            section_best = score["section_best"]
            if section not in section_best:
                continue
            section_best[section] = (
                normalized_values[article_idx]
                if article_idx < len(normalized_values)
                else section_values[article_idx]
            )

    for score in article_scores:
        normalized_section_values = list(score["section_best"].values())
        score["section_raw"] = (
            float(sum(normalized_section_values) / len(normalized_section_values))
            if normalized_section_values
            else 0.0
        )
    article_dense_by_id = {
        raw_records[int(score["article_idx"])].article_id: float(score["rel_dense"])
        for score in article_scores
    }

    # Merge repeated coverage across publication dates inside one seven-day ISO
    # week. Articles never cross a weekly boundary, which keeps later updates in
    # a separate event while removing short-lived repetition.
    clustering_cfg = config.get("clustering", {})
    time_window_hours = float(clustering_cfg.get("time_window_hours", 168))
    min_cluster_size = int(clustering_cfg.get("min_cluster_size", 3))
    min_samples = int(clustering_cfg.get("min_samples", 1))
    similarity_threshold = float(clustering_cfg.get("similarity_threshold", 0.88))
    clusters = _cluster_articles_within_week(
        raw_records,
        news_embeddings,
        collect_date=collect_date,
        time_window_hours=time_window_hours,
        min_cluster_size=min_cluster_size,
        min_samples=min_samples,
        similarity_threshold=similarity_threshold,
    )
    article_event_ids: dict[int, str] = {}
    for cluster_id, member_indices in clusters.items():
        for member_index in member_indices:
            article_event_ids[member_index] = str(cluster_id)

    event_records: list[NewsEventRecord] = []
    mention_raw_values: list[float] = []
    impact_raw_values: list[float] = []
    section_raw_values: list[float] = []

    for cluster_id, member_indices in clusters.items():
        member_scores = [article_scores[i] for i in member_indices]

        rep_score = max(
            member_scores,
            key=lambda s: (
                s["rel_dense"],
                raw_records[s["article_idx"]].article_date or "",
            ),
        )
        rep_idx = int(rep_score["article_idx"])
        rep_record = raw_records[rep_idx]

        member_records = [raw_records[i] for i in member_indices]
        ordered_member_records = [rep_record] + [record for record in member_records if record.article_id != rep_record.article_id]
        representative_snippet = (rep_record.snippet or "").strip()

        rel_dense = max(float(s["rel_dense"]) for s in member_scores)
        impact_raw = max(float(s["impact_raw"]) for s in member_scores)

        mention_cnt = len(member_indices)
        mention_raw = mention_score(mention_cnt, mention_transform)
        mention_raw_values.append(float(mention_raw))
        impact_raw_values.append(float(impact_raw))
        section_raw = max(float(s.get("section_raw", 0.0)) for s in member_scores)
        section_raw_values.append(float(section_raw))

        if rep_record.article_date:
            article_time = datetime.combine(
                date.fromisoformat(rep_record.article_date),
                datetime.min.time(),
                tzinfo=timezone.utc,
            )
        else:
            article_time = datetime.combine(collect_date, datetime.min.time(), tzinfo=timezone.utc)
        time_sc = time_score(article_time, datetime.combine(collect_date, datetime.min.time(), tzinfo=timezone.utc), tau_hours)

        top_indices = rep_score["top_indices"]
        matched_chunk_ids = ",".join(context_chunk_ids[i] for i in top_indices)
        matched_chunk_texts = " ||| ".join(context_texts[i] for i in top_indices)
        matched_chunk_sections = ",".join(context_section_types[i] for i in top_indices)
        matched_chunk_similarities = ",".join(
            str(float(np.dot(context_embeddings[i], news_embeddings[rep_idx]) /
                (np.linalg.norm(context_embeddings[i]) * np.linalg.norm(news_embeddings[rep_idx]) + 1e-8)))
            for i in top_indices
        )

        event_records.append(
            NewsEventRecord(
                collect_date=collect_date.isoformat(),
                company_id=company_id,
                company_name=company_name,
                event_id=str(cluster_id),
                mention_count=mention_cnt,
                representative_url=rep_record.url,
                representative_title=rep_record.title,
                representative_snippet=representative_snippet,
                representative_source=rep_record.source,
                representative_article_date=rep_record.article_date,
                rel_dense=rel_dense,
                rel_rerank=0.0,
                section_score=section_raw,
                global_section_score=0.0,
                impact_score=impact_raw,
                mention_score=float(mention_raw),
                time_score=time_sc,
                final_score=0.0,
                matched_chunk_ids=matched_chunk_ids,
                matched_chunk_texts=matched_chunk_texts,
                matched_chunk_section_types=matched_chunk_sections,
                matched_chunk_similarities=matched_chunk_similarities,
                member_article_ids=[record.article_id for record in ordered_member_records],
                member_urls=[record.url for record in ordered_member_records if record.url],
                query_used=rep_record.query_used,
            )
        )

    # Keep at most 15 dense-similarity candidates per ISO week. Only those
    # candidates reach the cross-encoder, which bounds 90-day inference cost.
    weekly_candidates, weekly_embedding_ranks = _weekly_embedding_candidates(
        event_records,
        collect_date=collect_date,
        candidates_per_week=weekly_embedding_candidate_k,
    )
    candidate_event_ids = {
        event.event_id
        for candidates in weekly_candidates.values()
        for event in candidates
    }
    article_index_by_id = {
        record.article_id: index for index, record in enumerate(raw_records)
    }
    reranker = Reranker(
        model_cfg.get("reranker_model_name", "BAAI/bge-reranker-v2-m3"),
        device=model_cfg.get("device", "cpu"),
        batch_size=int(model_cfg.get("batch_size", 32)),
    )
    rerank_all_chunks = bool(scoring_cfg.get("rerank_all_chunks", False))
    reranked_events: list[NewsEventRecord] = []
    event_raw_rerank: dict[str, float] = {}
    for event in event_records:
        rerank_score = 0.0
        if event.event_id in candidate_event_ids and event.member_article_ids:
            representative_id = event.member_article_ids[0]
            article_idx = article_index_by_id.get(representative_id)
            if article_idx is not None:
                top_indices = article_scores[article_idx]["top_indices"]
                rerank_indices = (
                    range(len(context_texts)) if rerank_all_chunks else top_indices
                )
                pairs = [
                    (raw_records[article_idx].doc_text, context_texts[index])
                    for index in rerank_indices
                ]
                scores = reranker.score(pairs)
                rerank_score = float(max(scores)) if scores else 0.0
        event_raw_rerank[event.event_id] = rerank_score
        reranked_events.append(
            NewsEventRecord(**{**asdict(event), "rel_rerank": rerank_score})
        )
    event_records = reranked_events
    raw_events_by_id = {event.event_id: event for event in event_records}

    # Rerank inside each week, keep five, then choose the final global raw-event
    # list for the News Agent from the union of weekly selections.
    weekly_selected_raw, weekly_rerank_ranks = _weekly_rerank_selection(
        weekly_candidates,
        events_by_id=raw_events_by_id,
        events_per_week=weekly_rerank_top_k,
    )
    ranked_raw_events, selected_raw_events = _rank_events_after_rerank(
        weekly_selected_raw,
        int(event_top_k) if event_top_k is not None else None,
    )
    selected_weekly_event_ids = {
        event.event_id for event in weekly_selected_raw
    }
    ranked_event_ids = [event.event_id for event in ranked_raw_events]
    selected_event_ids = {event.event_id for event in selected_raw_events}
    event_selection_rank = {
        event_id: rank for rank, event_id in enumerate(ranked_event_ids, start=1)
    }

    # Normalize scores before weighted sum.
    ordered_candidate_ids = sorted(candidate_event_ids)
    candidate_rerank_values = [
        event_raw_rerank[event_id] for event_id in ordered_candidate_ids
    ]
    normalized_candidate_rerank = dict(
        zip(ordered_candidate_ids, minmax_normalize(candidate_rerank_values))
    )
    normalized_mention = minmax_normalize(mention_raw_values)
    normalized_impact = minmax_normalize(impact_raw_values)
    normalized_section = minmax_normalize(section_raw_values)

    for idx, event in enumerate(event_records):
        rel_rerank = normalized_candidate_rerank.get(event.event_id, 0.0)
        mention_norm = normalized_mention[idx] if idx < len(normalized_mention) else event.mention_score
        impact_norm = normalized_impact[idx] if idx < len(normalized_impact) else event.impact_score
        section_norm = normalized_section[idx] if idx < len(normalized_section) else event.section_score
        global_section = (rel_rerank + section_norm) / 2.0
        time_sc = event.time_score
        final = final_score(global_section, mention_norm, time_sc, impact_norm, alpha, beta, gamma, delta)
        event_records[idx] = NewsEventRecord(
            **{
                **asdict(event),
                "rel_rerank": rel_rerank,
                "section_score": section_norm,
                "global_section_score": global_section,
                "mention_score": mention_norm,
                "impact_score": impact_norm,
                "final_score": final,
            }
        )

    normalized_events_by_id = {event.event_id: event for event in event_records}
    ranked_all_events = sorted(
        event_records,
        key=lambda event: (
            event.event_id not in candidate_event_ids,
            -float(event_raw_rerank.get(event.event_id, 0.0)),
            -float(event.rel_dense),
            -_event_date_ordinal(event.representative_article_date),
            str(event.event_id),
        ),
    )
    weekly_selected_events = [
        normalized_events_by_id[event.event_id]
        for event in weekly_selected_raw
        if event.event_id in normalized_events_by_id
    ]
    selected_events = [
        normalized_events_by_id[event_id]
        for event_id in ranked_event_ids
        if event_id in selected_event_ids and event_id in normalized_events_by_id
    ]

    # Publisher URL resolution and snippet retrieval are network-expensive.
    # Apply them only to the final global evidence set after ranking, then
    # propagate the enriched representative fields to every event view.
    selected_representative_ids = {
        event.member_article_ids[0]
        for event in selected_events
        if event.member_article_ids
    }
    representative_indices = [
        index
        for index, record in enumerate(raw_records)
        if record.article_id in selected_representative_ids
    ]
    enrich_records = getattr(collector, "_enrich_records", None)
    if representative_indices and callable(enrich_records):
        enriched = enrich_records(
            [raw_records[index] for index in representative_indices],
            collection_notes,
        )
        for index, record in zip(representative_indices, enriched):
            raw_records[index] = record
    raw_records_by_id = {record.article_id: record for record in raw_records}

    def with_enriched_representative(event: NewsEventRecord) -> NewsEventRecord:
        if not event.member_article_ids:
            return event
        representative = raw_records_by_id.get(event.member_article_ids[0])
        if representative is None:
            return event
        return NewsEventRecord(
            **{
                **asdict(event),
                "representative_url": representative.url,
                "representative_title": representative.title,
                "representative_snippet": representative.snippet or "",
                "representative_source": representative.source,
                "member_urls": [
                    raw_records_by_id[article_id].url
                    for article_id in event.member_article_ids
                    if article_id in raw_records_by_id and raw_records_by_id[article_id].url
                ],
            }
        )

    ranked_all_events = [with_enriched_representative(event) for event in ranked_all_events]
    weekly_selected_events = [
        with_enriched_representative(event) for event in weekly_selected_events
    ]
    selected_events = [with_enriched_representative(event) for event in selected_events]

    # Save the full candidate-event audit trail and the selected event set
    # separately.  Downstream report construction consumes only news_events.parquet.
    events_output_dir = data_root / "news" / "events" / artifact_dir
    all_news_events_path = events_output_dir / "news_events_all.parquet"
    weekly_news_events_path = events_output_dir / "news_events_weekly.parquet"
    news_events_path = events_output_dir / "news_events.parquet"
    event_ranking_path = events_output_dir / "event_ranking.parquet"
    save_parquet([asdict(record) for record in ranked_all_events], all_news_events_path)
    save_parquet([asdict(record) for record in weekly_selected_events], weekly_news_events_path)
    save_parquet([asdict(record) for record in selected_events], news_events_path)
    save_parquet(
        [
            {
                "event_id": event.event_id,
                "representative_article_date": event.representative_article_date,
                "representative_title": event.representative_title,
                "representative_url": event.representative_url,
                "member_article_count": len(event.member_article_ids or []),
                "rel_rerank_raw": event_raw_rerank.get(event.event_id),
                "rel_rerank_normalized": event.rel_rerank,
                "rel_dense": event.rel_dense,
                "final_score": event.final_score,
                "iso_week": _iso_week_key(event.representative_article_date, collect_date),
                "weekly_embedding_rank": weekly_embedding_ranks.get(event.event_id),
                "weekly_candidate": event.event_id in candidate_event_ids,
                "weekly_rerank_rank": weekly_rerank_ranks.get(event.event_id),
                "weekly_selected": event.event_id in selected_weekly_event_ids,
                "selection_rank": event_selection_rank.get(event.event_id),
                "selected": event.event_id in selected_event_ids,
                "selection_method": selection_method,
            }
            for event in ranked_all_events
        ],
        event_ranking_path,
    )

    selected_article_ids = {
        article_id
        for event in selected_events
        for article_id in (event.member_article_ids or [])
    }
    selected_raw_records = [
        record for record in raw_records if record.article_id in selected_article_ids
    ]
    save_parquet([asdict(record) for record in selected_raw_records], raw_news_path)
    save_parquet(
        [
            {
                "article_id": record.article_id,
                "article_date": record.article_date,
                "source": record.source,
                "title": record.title,
                "url": record.url,
                "rel_dense": float(article_scores[idx]["rel_dense"]),
                "rel_rerank_raw": event_raw_rerank.get(
                    article_event_ids.get(idx, "")
                ),
                "impact_raw": float(article_scores[idx]["impact_raw"]),
                "section_raw": float(article_scores[idx]["section_raw"]),
                "seven_day_event_id": article_event_ids.get(idx),
                "event_selection_rank": event_selection_rank.get(
                    article_event_ids.get(idx, "")
                ),
                "selected": record.article_id in selected_article_ids,
            }
            for idx, record in enumerate(raw_records)
        ],
        article_ranking_path,
    )

    # Build report pack
    chunks_by_section: dict[str, list[dict]] = {}
    for rec in context_records:
        chunks_by_section.setdefault(rec["section_type"], []).append(
            {
                "chunk_id": rec["chunk_id"],
                "text": rec["text"],
                "provenance": rec.get("provenance", {}),
            }
        )

    all_report_events = [
        _build_report_event(
            event,
            raw_records_by_id=raw_records_by_id,
            article_dense_by_id=article_dense_by_id,
            relevance_rank=index,
        )
        for index, event in enumerate(ranked_all_events, start=1)
    ]
    news_events_topk = [
        _build_report_event(
            event,
            raw_records_by_id=raw_records_by_id,
            article_dense_by_id=article_dense_by_id,
            relevance_rank=index,
        )
        for index, event in enumerate(selected_events, start=1)
    ]
    news_events_weekly = [
        _build_report_event(
            event,
            raw_records_by_id=raw_records_by_id,
            article_dense_by_id=article_dense_by_id,
            relevance_rank=index,
        )
        for index, event in enumerate(weekly_selected_events, start=1)
    ]

    report_pack = {
        "collect_date": collect_date.isoformat(),
        "company": {
            "company_id": company_id,
            "company_name": company_name,
            "ksic_code": [],
        },
        "corporate_context": {
            "report_key": context_records[0].get("report_key") if context_records else "",
            "report_date": context_records[0].get("report_date") if context_records else "",
            "chunks_by_section": chunks_by_section,
        },
        "news_selection": {
            "candidate_article_count": collected_unique_count,
            "seven_day_event_count": len(ranked_all_events),
            "weekly_candidate_count": len(candidate_event_ids),
            "weekly_selected_event_count": len(weekly_selected_events),
            "selected_event_count": len(selected_events),
            "selected_source_article_count": len(selected_raw_records),
            "stage": "weekly_dense_then_rerank",
            "unit": "event",
            "metric": (
                f"dense_top{weekly_embedding_candidate_k}_per_week_then_"
                f"rerank_top{weekly_rerank_top_k}_per_week"
            ),
            "weekly_embedding_candidates": weekly_embedding_candidate_k,
            "weekly_rerank_top_k": weekly_rerank_top_k,
            "top_k": int(event_top_k) if event_top_k is not None else None,
            "semantic_dedup_window_days": 7,
            "cross_date_clustering": True,
            "collection_chunk_days": collection_chunk_days,
        },
        "news_events_all": all_report_events,
        "news_events_weekly": news_events_weekly,
        "news_events_final": news_events_topk,
    }

    report_dir = data_root / "reports" / "packs" / artifact_dir
    report_context_path = report_dir / "report_context.json"
    save_json(report_pack, report_context_path)
    return {
        "raw_news_candidates_path": str(raw_news_candidates_path),
        "raw_news_path": str(raw_news_path),
        "article_ranking_path": str(article_ranking_path),
        "news_events_path": str(news_events_path),
        "all_news_events_path": str(all_news_events_path),
        "weekly_news_events_path": str(weekly_news_events_path),
        "event_ranking_path": str(event_ranking_path),
        "report_context_path": str(report_context_path),
        "collected_unique_count": collected_unique_count,
        "raw_news_count": len(selected_raw_records),
        # Deprecated compatibility field.  No pre-rerank cap is applied now.
        "raw_news_count_before_total_cap": collected_unique_count,
        "news_event_count_before_top_k": len(ranked_all_events),
        "news_event_count": len(selected_events),
        "query_used": query,
        "event_top_k": int(event_top_k) if event_top_k is not None else None,
        "total_max_results": (
            int(event_top_k) if event_top_k is not None else None
        ),
        "weekly_embedding_candidates": weekly_embedding_candidate_k,
        "weekly_rerank_top_k": weekly_rerank_top_k,
        "weekly_selected_event_count": len(weekly_selected_events),
        "selection_stage": "weekly_dense_then_rerank",
        "selection_method": selection_method,
        "semantic_dedup_window_days": 7,
        "cross_date_clustering": True,
        "collection_chunk_days": collection_chunk_days,
    }
