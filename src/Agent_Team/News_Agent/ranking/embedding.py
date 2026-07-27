"""Embedding model wrapper."""

from __future__ import annotations

import hashlib
import re
from typing import TYPE_CHECKING, Iterable

import numpy as np

if TYPE_CHECKING:
    from sentence_transformers import SentenceTransformer

_MODEL_CACHE: dict[tuple[str, str], "SentenceTransformer"] = {}
FALLBACK_DIM = 384


class EmbeddingModel:
    def __init__(
        self,
        model_name: str,
        device: str = "cpu",
        batch_size: int = 32,
    ) -> None:
        self.model_name = model_name
        self.device = device
        self.batch_size = batch_size
        self._model = None
        self._fallback_reason = ""

        try:
            from sentence_transformers import SentenceTransformer

            cache_key = (model_name, device)
            if cache_key in _MODEL_CACHE:
                self._model = _MODEL_CACHE[cache_key]
            else:
                self._model = SentenceTransformer(model_name, device=device)
                _MODEL_CACHE[cache_key] = self._model
        except Exception as exc:
            self._fallback_reason = f"Embedding model load failed for {model_name}: {exc}"

    def encode(self, texts: list[str]) -> np.ndarray:
        if not texts:
            return np.zeros((0, 0), dtype=np.float32)
        if self._model is None:
            return _hashing_embeddings(texts)
        embeddings = self._model.encode(
            texts,
            batch_size=self.batch_size,
            normalize_embeddings=True,
            show_progress_bar=False,
        )
        return np.asarray(embeddings, dtype=np.float32)


def cosine_similarity_matrix(a: np.ndarray, b: np.ndarray) -> np.ndarray:
    if a.size == 0 or b.size == 0:
        return np.zeros((a.shape[0], b.shape[0]), dtype=np.float32)
    a_norm = a / (np.linalg.norm(a, axis=1, keepdims=True) + 1e-8)
    b_norm = b / (np.linalg.norm(b, axis=1, keepdims=True) + 1e-8)
    return np.dot(a_norm, b_norm.T)


def cosine_similarity(vec_a: Iterable[float], vec_b: Iterable[float]) -> float:
    a = np.asarray(list(vec_a), dtype=np.float32)
    b = np.asarray(list(vec_b), dtype=np.float32)
    if a.size == 0 or b.size == 0:
        return 0.0
    denom = (np.linalg.norm(a) * np.linalg.norm(b))
    if denom == 0.0:
        return 0.0
    return float(np.dot(a, b) / denom)


def _hashing_embeddings(texts: list[str]) -> np.ndarray:
    matrix = np.zeros((len(texts), FALLBACK_DIM), dtype=np.float32)
    for row, text in enumerate(texts):
        for token in _tokens(text):
            digest = hashlib.blake2b(token.encode("utf-8"), digest_size=8).digest()
            index = int.from_bytes(digest, "big") % FALLBACK_DIM
            matrix[row, index] += 1.0
        norm = float(np.linalg.norm(matrix[row]))
        if norm:
            matrix[row] /= norm
    return matrix


def _tokens(text: str) -> list[str]:
    return re.findall(r"[0-9A-Za-z가-힣]{2,}", str(text).lower())
