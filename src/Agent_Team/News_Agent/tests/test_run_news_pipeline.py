from __future__ import annotations

from datetime import date
import json
from pathlib import Path

import numpy as np
import pytest

from Agent_Team.News_Agent.dart.schemas import NewsEventRecord, RawNewsRecord
from Agent_Team.News_Agent.pipelines import run_news_pipeline as pipeline
from Agent_Team.News_Agent.ranking.clustering import ClusterResult


def _record(article_id: str, article_date: str) -> RawNewsRecord:
    return RawNewsRecord(
        collect_date="2025-10-30",
        article_id=article_id,
        article_date=article_date,
        source="source",
        url=f"https://example.com/{article_id}",
        title=article_id,
        snippet="snippet",
        doc_text=article_id,
        query_used="Target",
        lang="ko",
        fetched_at="2025-10-30T00:00:00Z",
        metadata={"query_day": article_date},
    )


def _event(
    event_id: str,
    *,
    rel_rerank: float,
    rel_dense: float,
    article_date: str,
    url: str | None = None,
) -> NewsEventRecord:
    return NewsEventRecord(
        collect_date="2025-10-30",
        company_id="001",
        company_name="Target",
        event_id=event_id,
        mention_count=1,
        representative_url=url or f"https://example.com/{event_id}",
        representative_title=event_id,
        representative_snippet="snippet",
        representative_source="source",
        representative_article_date=article_date,
        rel_dense=rel_dense,
        rel_rerank=rel_rerank,
        section_score=0.0,
        global_section_score=0.0,
        impact_score=0.0,
        mention_score=0.0,
        time_score=0.0,
        final_score=0.0,
        matched_chunk_ids="",
        matched_chunk_texts="",
        matched_chunk_section_types="",
        matched_chunk_similarities="",
        member_article_ids=[event_id],
        member_urls=[url or f"https://example.com/{event_id}"],
        query_used="Target",
    )


def test_event_top_k_uses_reranker_then_deterministic_tie_breakers() -> None:
    events = [
        _event("low", rel_rerank=0.7, rel_dense=0.99, article_date="2025-10-30"),
        _event("dense", rel_rerank=0.9, rel_dense=0.8, article_date="2025-10-29"),
        _event("recent", rel_rerank=0.9, rel_dense=0.8, article_date="2025-10-30"),
    ]

    ranked, selected = pipeline._rank_events_after_rerank(events, 2)

    assert [event.event_id for event in ranked] == ["recent", "dense", "low"]
    assert [event.event_id for event in selected] == ["recent", "dense"]


def test_event_top_k_none_keeps_all_ranked_events() -> None:
    events = [
        _event("one", rel_rerank=0.2, rel_dense=0.1, article_date="2025-10-29"),
        _event("two", rel_rerank=0.8, rel_dense=0.1, article_date="2025-10-30"),
    ]

    ranked, selected = pipeline._rank_events_after_rerank(events, None)

    assert [event.event_id for event in ranked] == ["two", "one"]
    assert selected == ranked


def test_event_top_k_must_be_positive() -> None:
    with pytest.raises(ValueError, match="event_top_k must be >= 1"):
        pipeline._rank_events_after_rerank([], 0)


def test_embedding_clusters_never_merge_across_publication_dates(monkeypatch) -> None:
    records = [
        _record("day-one-a", "2025-10-29"),
        _record("day-one-b", "2025-10-29"),
        _record("day-two-a", "2025-10-30"),
        _record("day-two-b", "2025-10-30"),
    ]
    embeddings = np.ones((4, 3), dtype=np.float32)
    observed_bucket_sizes: list[int] = []

    def fake_cluster_embeddings(
        bucket_embeddings: np.ndarray,
        _timestamps: list,
        **_kwargs: object,
    ) -> ClusterResult:
        observed_bucket_sizes.append(len(bucket_embeddings))
        return ClusterResult(labels=[0] * len(bucket_embeddings), n_clusters=1)

    monkeypatch.setattr(pipeline, "cluster_embeddings", fake_cluster_embeddings)

    clusters = pipeline._cluster_articles_within_day(
        records,
        embeddings,
        collect_date=date(2025, 10, 30),
        time_window_hours=48,
        min_cluster_size=2,
        min_samples=1,
    )

    assert observed_bucket_sizes == [2, 2]
    assert sorted(sorted(indices) for indices in clusters.values()) == [[0, 1], [2, 3]]


def test_daily_pipeline_reranks_all_candidates_before_event_top_k(
    monkeypatch,
    tmp_path,
) -> None:
    records = [
        _record("best", "2025-10-30"),
        _record("middle", "2025-10-30"),
        _record("low", "2025-10-30"),
    ]

    class FakeCollector:
        def __init__(self, **_kwargs: object) -> None:
            pass

        def collect(self, **_kwargs: object) -> tuple[list[RawNewsRecord], dict]:
            return records, {"collection_notes": []}

    class FakeEmbedder:
        def __init__(self, *_args: object, **_kwargs: object) -> None:
            pass

        def encode(self, texts: list[str]) -> np.ndarray:
            vectors = {
                "best": [1.0, 0.0, 0.0],
                "middle": [0.9, 0.1, 0.0],
                "low": [0.0, 1.0, 0.0],
            }
            return np.asarray([vectors[text] for text in texts], dtype=np.float32)

    class FakeReranker:
        def __init__(self, *_args: object, **_kwargs: object) -> None:
            pass

        def score(self, pairs: list[tuple[str, str]]) -> list[float]:
            scores = {"best": 0.9, "middle": 0.7, "low": 0.1}
            return [scores[left] for left, _right in pairs]

    monkeypatch.setattr(pipeline, "GoogleNewsCollector", FakeCollector)
    monkeypatch.setattr(pipeline, "EmbeddingModel", FakeEmbedder)
    monkeypatch.setattr(pipeline, "Reranker", FakeReranker)
    monkeypatch.setattr(
        pipeline,
        "_cluster_articles_within_day",
        lambda *_args, **_kwargs: {1: [0], 2: [1], 3: [2]},
    )

    data_root = tmp_path / "artifacts"
    context_path = (
        data_root / "db/corporate_context/001/report/corporate_context_db.jsonl"
    )
    context_path.parent.mkdir(parents=True)
    context_path.write_text(
        json.dumps(
            {
                "report_key": "report",
                "report_date": "2025-08-14",
                "section_type": "overview",
                "chunk_id": "chunk-1",
                "text": "company context",
                "embedding": [1.0, 0.0, 0.0],
                "score_total": 1.0,
                "provenance": {},
            }
        )
        + "\n",
        encoding="utf-8",
    )

    result = pipeline.run_daily_news(
        config={
            "data_root": str(data_root),
            "news": {"collection_days": 1, "event_top_k": 2, "dedup_on_url": True},
            "models": {},
            "clustering": {"min_cluster_size": 3, "min_samples": 1},
            "scoring": {"impact_topk": 1, "rerank_all_chunks": True},
        },
        collect_date=date(2025, 10, 30),
        company_id="001",
        company_name="Target",
        report_key="report",
    )

    import pandas as pd

    candidates = pd.read_parquet(result["raw_news_candidates_path"])
    selected_articles = pd.read_parquet(result["raw_news_path"])
    all_events = pd.read_parquet(result["all_news_events_path"])
    selected_events = pd.read_parquet(result["news_events_path"])
    event_ranking = pd.read_parquet(result["event_ranking_path"])

    assert len(candidates) == 3
    assert selected_articles["article_id"].tolist() == ["1", "2"]
    assert len(all_events) == 3
    assert selected_events["event_id"].astype(str).tolist() == ["1", "2"]
    assert event_ranking["selected"].tolist() == [True, True, False]
    assert result["collected_unique_count"] == 3
    assert result["news_event_count_before_top_k"] == 3
    assert result["news_event_count"] == 2
    assert result["selection_stage"] == "post_rerank_same_day_cluster"

    report_context = json.loads(
        Path(result["report_context_path"]).read_text(encoding="utf-8")
    )
    assert [event["event_id"] for event in report_context["news_events_all"]] == [
        "1",
        "2",
        "3",
    ]
    assert [event["event_id"] for event in report_context["news_events_topk"]] == [
        "1",
        "2",
    ]
    assert [event["relevance_rank"] for event in report_context["news_events_topk"]] == [1, 2]
