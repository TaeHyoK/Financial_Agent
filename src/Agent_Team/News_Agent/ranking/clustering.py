"""Deterministic semantic clustering utilities for news events."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

import numpy as np


@dataclass(frozen=True)
class ClusterResult:
    labels: list[int]
    n_clusters: int


def cluster_embeddings(
    embeddings: np.ndarray,
    timestamps: list[datetime],
    *,
    time_window_hours: float,
    min_cluster_size: int,
    min_samples: int,
    similarity_threshold: float = 0.88,
) -> ClusterResult:
    """Group semantically near-identical articles inside a bounded time span.

    Connected components make the duplicate rule stable across environments;
    singleton/noise articles retain ``-1`` labels and become individual events
    in the caller.
    """

    if embeddings.size == 0:
        return ClusterResult(labels=[], n_clusters=0)

    sample_count = int(embeddings.shape[0])
    if sample_count < max(2, min_cluster_size):
        return ClusterResult(labels=[-1] * sample_count, n_clusters=0)
    if len(timestamps) != sample_count:
        raise ValueError("timestamps and embeddings must have the same length")
    if not 0.0 < float(similarity_threshold) <= 1.0:
        raise ValueError("similarity_threshold must be in (0, 1]")

    del min_samples  # Retained in the public signature for configuration compatibility.
    norms = np.linalg.norm(embeddings, axis=1, keepdims=True)
    normalized = embeddings / np.maximum(norms, 1e-12)
    similarities = normalized @ normalized.T
    max_seconds = max(float(time_window_hours), 0.0) * 3600.0

    parent = list(range(sample_count))

    def find(index: int) -> int:
        while parent[index] != index:
            parent[index] = parent[parent[index]]
            index = parent[index]
        return index

    def union(left: int, right: int) -> None:
        left_root = find(left)
        right_root = find(right)
        if left_root != right_root:
            parent[right_root] = left_root

    for left in range(sample_count):
        for right in range(left + 1, sample_count):
            time_distance = abs(
                (timestamps[left] - timestamps[right]).total_seconds()
            )
            if time_distance > max_seconds:
                continue
            if float(similarities[left, right]) >= float(similarity_threshold):
                union(left, right)

    components: dict[int, list[int]] = {}
    for index in range(sample_count):
        components.setdefault(find(index), []).append(index)

    labels = [-1] * sample_count
    cluster_id = 0
    effective_min_cluster_size = max(2, int(min_cluster_size))
    for members in sorted(components.values(), key=lambda values: values[0]):
        if len(members) < effective_min_cluster_size:
            continue
        for index in members:
            labels[index] = cluster_id
        cluster_id += 1
    return ClusterResult(labels=labels, n_clusters=cluster_id)
