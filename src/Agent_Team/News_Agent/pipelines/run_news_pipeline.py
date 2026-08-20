"""News pipeline: collect -> rerank -> same-day cluster -> select -> outputs."""

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


def _clustering_date_bucket(record: RawNewsRecord, collect_date: date) -> str:
    """Date key used to prevent cross-day clustering leakage."""
    if record.article_date:
        return record.article_date
    metadata = record.metadata or {}
    query_day = metadata.get("query_day")
    if isinstance(query_day, str) and query_day:
        return query_day
    return collect_date.isoformat()


def _event_date_ordinal(value: str | None) -> int:
    try:
        return date.fromisoformat(str(value or "")).toordinal()
    except ValueError:
        return 0


def _rank_events_after_rerank(
    events: list[NewsEventRecord],
    top_k: int | None,
) -> tuple[list[NewsEventRecord], list[NewsEventRecord]]:
    """Rank same-day events by their representative article reranker score.

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


def _cluster_articles_within_day(
    records: list[RawNewsRecord],
    embeddings: np.ndarray,
    *,
    collect_date: date,
    time_window_hours: float,
    min_cluster_size: int,
    min_samples: int,
) -> dict[int, list[int]]:
    """Cluster semantically similar articles without ever crossing a date bucket."""

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
        bucket = _clustering_date_bucket(record, collect_date)
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


def run_daily_news(
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
    # ``total_max_results`` is retained as a compatibility alias for existing
    # orchestration scripts.  Its corrected meaning is the number of same-day
    # deduplicated events retained *after* article reranking.
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
    dedup_on_url = (
        bool(dedup_on_url_override)
        if dedup_on_url_override is not None
        else bool(news_cfg.get("dedup_on_url", True))
    )

    # Query one day at a time to avoid per-query cap bottlenecks.
    raw_records: list[RawNewsRecord] = []
    collection_notes: list[str] = []
    for offset in range(collection_days):
        day = collect_date - timedelta(days=offset)
        day_records, day_meta = collector.collect(
            query=query,
            collect_date=day,
            lookback_days=0,
            max_results=max_results,
            dedup_on_url=dedup_on_url,
        )
        day_notes = [f"{day.isoformat()}::{note}" for note in day_meta.get("collection_notes", [])]
        collection_notes.extend(day_notes)
        for record in day_records:
            payload = asdict(record)
            payload["metadata"] = {
                **(record.metadata or {}),
                "query_day": day.isoformat(),
                "window_collect_date": collect_date.isoformat(),
                "window_start_date": (collect_date - timedelta(days=collection_days - 1)).isoformat(),
                "collection_days": collection_days,
            }
            raw_records.append(RawNewsRecord(**payload))

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
                "event_selection_stage": "post_rerank_same_day_cluster",
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
            "selection_stage": "post_rerank_same_day_cluster",
            "selection_method": "top_k_events_by_representative_article_rerank",
            "cross_date_clustering": False,
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

    reranker = Reranker(
        model_cfg.get("reranker_model_name", "BAAI/bge-reranker-v2-m3"),
        device=model_cfg.get("device", "cpu"),
        batch_size=int(model_cfg.get("batch_size", 32)),
    )

    scoring_cfg = config.get("scoring", {})
    alpha = float(scoring_cfg.get("alpha", 0.75))
    beta = float(scoring_cfg.get("beta", 0.15))
    gamma = float(scoring_cfg.get("gamma", 0.10))
    delta = float(scoring_cfg.get("delta", 0.0))
    tau_hours = float(scoring_cfg.get("tau_hours", 48))
    mention_transform = scoring_cfg.get("mention_transform", "log1p")
    impact_topk = max(int(scoring_cfg.get("impact_topk", 3)), 1)
    rerank_all_chunks = bool(scoring_cfg.get("rerank_all_chunks", False))

    # Score at article level first.
    article_scores: list[dict[str, Any]] = []
    for idx, record in enumerate(raw_records):
        article_vec = news_embeddings[idx]
        rel_dense_scores = np.dot(context_embeddings, article_vec) / (
            np.linalg.norm(context_embeddings, axis=1) * np.linalg.norm(article_vec) + 1e-8
        )
        top_indices = np.argsort(rel_dense_scores)[-impact_topk:][::-1].tolist()

        rel_dense = float(rel_dense_scores[top_indices[0]]) if top_indices else 0.0
        impact_raw = float(sum(float(rel_dense_scores[i]) * float(context_importance[i]) for i in top_indices))

        rerank_indices = range(len(context_texts)) if rerank_all_chunks else top_indices
        pairs = [(record.doc_text, context_texts[i]) for i in rerank_indices]
        rerank_scores = reranker.score(pairs)
        rel_rerank_raw = float(max(rerank_scores)) if rerank_scores else 0.0

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
                "rel_rerank_raw": rel_rerank_raw,
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

    # Semantic duplicate merging intentionally stays inside an exact publication
    # date.  Similar coverage on another date may contain a real status update and
    # must remain a separate event.
    clustering_cfg = config.get("clustering", {})
    time_window_hours = float(clustering_cfg.get("time_window_hours", 48))
    min_cluster_size = int(clustering_cfg.get("min_cluster_size", 3))
    min_samples = int(clustering_cfg.get("min_samples", 1))
    clusters = _cluster_articles_within_day(
        raw_records,
        news_embeddings,
        collect_date=collect_date,
        time_window_hours=time_window_hours,
        min_cluster_size=min_cluster_size,
        min_samples=min_samples,
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
                s["rel_rerank_raw"],
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
        rel_rerank_raw = max(float(s["rel_rerank_raw"]) for s in member_scores)
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
                rel_rerank=rel_rerank_raw,
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

    # Select the event budget only after every candidate article has been
    # cross-encoder reranked and same-day semantic duplicates have been merged.
    ranked_raw_events, selected_raw_events = _rank_events_after_rerank(
        event_records,
        int(event_top_k) if event_top_k is not None else None,
    )
    ranked_event_ids = [event.event_id for event in ranked_raw_events]
    selected_event_ids = {event.event_id for event in selected_raw_events}
    event_selection_rank = {
        event_id: rank for rank, event_id in enumerate(ranked_event_ids, start=1)
    }
    event_raw_rerank = {
        event.event_id: float(event.rel_rerank) for event in event_records
    }

    # Normalize scores before weighted sum.
    normalized_rerank = minmax_normalize([event.rel_rerank for event in event_records])
    normalized_mention = minmax_normalize(mention_raw_values)
    normalized_impact = minmax_normalize(impact_raw_values)
    normalized_section = minmax_normalize(section_raw_values)

    for idx, event in enumerate(event_records):
        rel_rerank = normalized_rerank[idx] if idx < len(normalized_rerank) else event.rel_rerank
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
    ranked_all_events = [
        normalized_events_by_id[event_id]
        for event_id in ranked_event_ids
        if event_id in normalized_events_by_id
    ]
    selected_events = [
        event
        for event in ranked_all_events
        if event.event_id in selected_event_ids
    ]

    # Save the full candidate-event audit trail and the selected event set
    # separately.  Downstream report construction consumes only news_events.parquet.
    events_output_dir = data_root / "news" / "events" / artifact_dir
    all_news_events_path = events_output_dir / "news_events_all.parquet"
    news_events_path = events_output_dir / "news_events.parquet"
    event_ranking_path = events_output_dir / "event_ranking.parquet"
    save_parquet([asdict(record) for record in ranked_all_events], all_news_events_path)
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
                "selection_rank": event_selection_rank.get(event.event_id),
                "selected": event.event_id in selected_event_ids,
                "selection_method": "top_k_events_by_representative_article_rerank",
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
                "rel_rerank_raw": float(article_scores[idx]["rel_rerank_raw"]),
                "impact_raw": float(article_scores[idx]["impact_raw"]),
                "section_raw": float(article_scores[idx]["section_raw"]),
                "same_day_event_id": article_event_ids.get(idx),
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

    top_events = sorted(selected_events, key=lambda e: e.final_score, reverse=True)
    news_events_topk: list[dict] = []
    for event in top_events:
        evidence = []
        chunk_ids = event.matched_chunk_ids.split(",") if event.matched_chunk_ids else []
        chunk_texts = event.matched_chunk_texts.split(" ||| ") if event.matched_chunk_texts else []
        chunk_sections = event.matched_chunk_section_types.split(",") if event.matched_chunk_section_types else []
        chunk_similarities = (
            [float(val) for val in event.matched_chunk_similarities.split(",")]
            if event.matched_chunk_similarities
            else []
        )
        for cid, ctext, csec, csim in zip(chunk_ids, chunk_texts, chunk_sections, chunk_similarities):
            evidence.append(
                {
                    "chunk_id": cid,
                    "section_type": csec,
                    "text": ctext,
                    "similarity": csim,
                }
            )
        member_article_ids = list(event.member_article_ids or [])
        member_records = [raw_records_by_id[article_id] for article_id in member_article_ids if article_id in raw_records_by_id]
        article_previews = _build_article_previews(member_records)

        news_events_topk.append(
            {
                "event_id": event.event_id,
                "mention_count": event.mention_count,
                "representative": {
                    "title": event.representative_title,
                    "snippet": event.representative_snippet,
                    "source": event.representative_source,
                    "time": event.representative_article_date,
                    "url": event.representative_url,
                },
                "articles": article_previews,
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
        )

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
            "same_day_event_count_before_top_k": len(ranked_all_events),
            "selected_event_count": len(selected_events),
            "selected_source_article_count": len(selected_raw_records),
            "stage": "post_rerank_same_day_cluster",
            "unit": "event",
            "metric": "representative_article_rel_rerank_raw",
            "top_k": int(event_top_k) if event_top_k is not None else None,
            "cross_date_clustering": False,
        },
        "news_events_topk": news_events_topk,
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
        "selection_stage": "post_rerank_same_day_cluster",
        "selection_method": "top_k_events_by_representative_article_rerank",
        "cross_date_clustering": False,
    }
