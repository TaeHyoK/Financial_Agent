"""Clustering utilities for news events."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

import numpy as np


@dataclass(frozen=True)
class ClusterResult:
    labels: list[int]
    n_clusters: int


def _build_features(embeddings: np.ndarray, timestamps: list[datetime], time_window_hours: float) -> np.ndarray:
    if embeddings.size == 0:
        return embeddings
    min_time = min(timestamps).timestamp()
    time_scale = max(time_window_hours, 1.0) * 3600.0
    time_feature = np.array([(ts.timestamp() - min_time) / time_scale for ts in timestamps], dtype=np.float32)
    time_feature = time_feature.reshape(-1, 1)
    return np.hstack([embeddings, time_feature])


def cluster_embeddings(
    embeddings: np.ndarray,
    timestamps: list[datetime],
    *,
    time_window_hours: float,
    min_cluster_size: int,
    min_samples: int,
) -> ClusterResult:
    if embeddings.size == 0:
        return ClusterResult(labels=[], n_clusters=0)

    sample_count = int(embeddings.shape[0])
    if sample_count < max(2, min_cluster_size):
        return ClusterResult(labels=[-1] * sample_count, n_clusters=0)

    effective_min_cluster_size = min(max(min_cluster_size, 2), sample_count)
    effective_min_samples = min(max(min_samples, 1), sample_count - 1)
    features = _build_features(embeddings, timestamps, time_window_hours)

    try:
        import hdbscan

        clusterer = hdbscan.HDBSCAN(
            min_cluster_size=effective_min_cluster_size,
            min_samples=effective_min_samples,
            metric="euclidean",
        )
        labels = clusterer.fit_predict(features)
        return ClusterResult(labels=labels.tolist(), n_clusters=len(set(labels)) - (1 if -1 in labels else 0))
    except Exception:
        return ClusterResult(labels=[-1] * sample_count, n_clusters=0)
