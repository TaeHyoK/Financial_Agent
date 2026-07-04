"""Text/date normalization utilities."""

from __future__ import annotations

import math
import re
from datetime import datetime, date, timedelta, timezone
from typing import Iterable

_KIWI_AVAILABLE = False
_KSS_AVAILABLE = False

try:
    from kiwipiepy import Kiwi  # type: ignore

    _KIWI = Kiwi()
    _KIWI_AVAILABLE = True
except Exception:
    _KIWI = None

if not _KIWI_AVAILABLE:
    try:
        from kss import split_sentences as kss_split_sentences  # type: ignore

        _KSS_AVAILABLE = True
    except Exception:
        _KSS_AVAILABLE = False

WHITESPACE_PATTERN = re.compile(r"[\t\f\v ]+")
LINEBREAK_PATTERN = re.compile(r"\r\n?|\n+")

DATE_PATTERNS = [
    re.compile(r"(?P<y>\d{4})[\./-](?P<m>\d{1,2})[\./-](?P<d>\d{1,2})"),
    re.compile(r"(?P<y>\d{4})년\s*(?P<m>\d{1,2})월\s*(?P<d>\d{1,2})일"),
]

RELATIVE_PATTERN = re.compile(r"(\d+)\s*(분|시간|일)\s*전")


def normalize_text(text: str, *, keep_newlines: bool = True) -> str:
    if not text:
        return ""
    text = text.replace("\u00a0", " ")
    if keep_newlines:
        lines = [WHITESPACE_PATTERN.sub(" ", line).strip() for line in LINEBREAK_PATTERN.split(text)]
        return "\n".join(line for line in lines if line)
    collapsed = WHITESPACE_PATTERN.sub(" ", LINEBREAK_PATTERN.sub(" ", text))
    return collapsed.strip()


def split_sentences(text: str) -> list[str]:
    text = normalize_text(text, keep_newlines=True)
    if not text:
        return []

    if _KIWI_AVAILABLE and _KIWI is not None:
        return [sent.text.strip() for sent in _KIWI.split_into_sents(text) if sent.text.strip()]

    if _KSS_AVAILABLE:
        return [sentence.strip() for sentence in kss_split_sentences(text) if sentence.strip()]

    sentences: list[str] = []
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        parts = re.split(r"(?<=[\.\?!])\s+", line)
        for part in parts:
            part = part.strip()
            if part:
                sentences.append(part)
    return sentences


def parse_date_from_text(text: str, *, fallback: date | None = None) -> date | None:
    text = text.strip()
    if not text:
        return fallback

    now = datetime.now(timezone.utc)
    rel = RELATIVE_PATTERN.search(text)
    if rel:
        value = int(rel.group(1))
        unit = rel.group(2)
        delta = timedelta(minutes=value) if unit == "분" else timedelta(hours=value) if unit == "시간" else timedelta(days=value)
        return (now - delta).date()

    for pattern in DATE_PATTERNS:
        match = pattern.search(text)
        if not match:
            continue
        try:
            y = int(match.group("y"))
            m = int(match.group("m"))
            d = int(match.group("d"))
            return date(y, m, d)
        except ValueError:
            continue
    return fallback


def cosine_similarity(vec_a: Iterable[float], vec_b: Iterable[float]) -> float:
    a = list(vec_a)
    b = list(vec_b)
    if len(a) != len(b) or not a:
        return 0.0
    dot = sum(x * y for x, y in zip(a, b))
    norm_a = math.sqrt(sum(x * x for x in a))
    norm_b = math.sqrt(sum(y * y for y in b))
    if norm_a == 0.0 or norm_b == 0.0:
        return 0.0
    return dot / (norm_a * norm_b)
