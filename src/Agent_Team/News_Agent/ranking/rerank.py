"""Cross-encoder reranker."""

from __future__ import annotations

import re

class Reranker:
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
            from sentence_transformers import CrossEncoder

            self._model = CrossEncoder(model_name, device=device)
        except Exception as exc:
            self._fallback_reason = f"CrossEncoder load failed for {model_name}: {exc}"

    def score(self, pairs: list[tuple[str, str]]) -> list[float]:
        if not pairs:
            return []
        if self._model is None:
            return [_token_overlap_score(left, right) for left, right in pairs]
        scores = self._model.predict(pairs, batch_size=self.batch_size)
        return [float(s) for s in scores]


def minmax_normalize(values: list[float]) -> list[float]:
    if not values:
        return []
    min_v = min(values)
    max_v = max(values)
    if max_v == min_v:
        return [0.5 for _ in values]
    return [(v - min_v) / (max_v - min_v) for v in values]


def _token_overlap_score(left: str, right: str) -> float:
    left_tokens = set(re.findall(r"[0-9A-Za-z가-힣]{2,}", str(left).lower()))
    right_tokens = set(re.findall(r"[0-9A-Za-z가-힣]{2,}", str(right).lower()))
    if not left_tokens or not right_tokens:
        return 0.0
    overlap = len(left_tokens & right_tokens)
    return overlap / max(len(left_tokens | right_tokens), 1)
