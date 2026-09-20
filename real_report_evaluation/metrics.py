"""Metric implementations adapted from the official FinRpt benchmark."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Sequence


FINRPT_NUMBER_PATTERN = re.compile(r"\d+\.?\d*")
TOKEN_PATTERN = re.compile(
    r"[가-힣]+|[A-Za-z]+(?:[-_.][A-Za-z0-9]+)*|[+-]?\d[\d,]*(?:\.\d+)?%?"
)


@dataclass(frozen=True)
class RougeL:
    precision: float
    recall: float
    f1: float


def tokenize_for_rouge(text: str) -> list[str]:
    """Whitespace-friendly Korean tokenization for ROUGE-L."""

    return [token.casefold() for token in TOKEN_PATTERN.findall(text)]


def rouge_l(reference: str, candidate: str) -> RougeL:
    """Compute ROUGE-L precision, recall, and harmonic F1."""

    reference_tokens = tokenize_for_rouge(reference)
    candidate_tokens = tokenize_for_rouge(candidate)
    if not reference_tokens or not candidate_tokens:
        return RougeL(0.0, 0.0, 0.0)
    lcs = _lcs_length(reference_tokens, candidate_tokens)
    recall = lcs / len(reference_tokens)
    precision = lcs / len(candidate_tokens)
    denominator = precision + recall
    f1 = 0.0 if denominator == 0.0 else 2.0 * precision * recall / denominator
    return RougeL(precision, recall, f1)


def _lcs_length(left: Sequence[str], right: Sequence[str]) -> int:
    """Return LCS length using O(min(n, m)) memory."""

    if len(left) < len(right):
        shorter, longer = left, right
    else:
        shorter, longer = right, left
    previous = [0] * (len(shorter) + 1)
    for long_token in longer:
        current = [0]
        for index, short_token in enumerate(shorter, start=1):
            if long_token == short_token:
                current.append(previous[index - 1] + 1)
            else:
                current.append(max(previous[index], current[-1]))
        previous = current
    return previous[-1]


def finrpt_number_count(text: str) -> int:
    """Count numeric strings with FinRpt's published regex."""

    return len(FINRPT_NUMBER_PATTERN.findall(text))


def number_rate(reference: str, candidate: str) -> tuple[float, int, int]:
    """Return min(N_candidate / N_reference, 1), plus both counts."""

    reference_count = finrpt_number_count(reference)
    candidate_count = finrpt_number_count(candidate)
    if reference_count == 0:
        score = 1.0
    else:
        score = min(candidate_count / reference_count, 1.0)
    return score, reference_count, candidate_count


def finrpt_accuracy(reference_rating: str, candidate_new_entry: str) -> float | None:
    """Binary Buy vs non-Buy agreement, matching FinRpt's trend accuracy.

    For this project, an analyst Buy maps to ``buy`` and a generated ``enter``
    recommendation maps to Buy. Wait/Avoid map to non-Buy. An unclear label is
    not silently counted as incorrect and is returned as missing.
    """

    if reference_rating not in {"buy", "hold", "sell"}:
        return None
    if candidate_new_entry not in {"enter", "wait", "avoid"}:
        return None
    return float((reference_rating == "buy") == (candidate_new_entry == "enter"))


def existing_position_agreement(
    reference_rating: str, candidate_existing_position: str
) -> float | None:
    """Optional diagnostic; not the primary FinRpt Accuracy value."""

    if reference_rating not in {"buy", "hold", "sell"}:
        return None
    if candidate_existing_position not in {"increase", "hold", "reduce"}:
        return None
    reference_direction = {"buy": "increase", "hold": "hold", "sell": "reduce"}[
        reference_rating
    ]
    return float(reference_direction == candidate_existing_position)


def explicit_rating_agreement(reference_rating: str, candidate_rating: str, *, binary: bool = True) -> float | None:
    """Compare declared opinions, not realized returns or inferred entry actions."""
    if reference_rating not in {"buy", "hold", "sell"} or candidate_rating not in {"buy", "hold", "sell"}:
        return None
    if binary:
        return float((reference_rating == "buy") == (candidate_rating == "buy"))
    return float(reference_rating == candidate_rating)
