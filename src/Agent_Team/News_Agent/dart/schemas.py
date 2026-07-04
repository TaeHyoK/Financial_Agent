"""Data schemas for DART parsing and context chunks."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal, TypedDict


SectionType = Literal[
    "overview",
    "products",
    "materials",
    "facilities",
    "contracts",
    "rnd",
    "unknown",
]


class Provenance(TypedDict, total=False):
    source: str
    parser: str
    offset_hint: str
    section_name: str
    line_start: int | None
    line_end: int | None


@dataclass(frozen=True)
class DartSection:
    section_name: str
    raw_text: str
    provenance: Provenance


@dataclass(frozen=True)
class ContextChunk:
    company_id: str
    company_name: str
    report_key: str
    report_date: str
    section_type: SectionType
    chunk_id: str
    text: str
    char_len: int
    score_info: float
    score_section: float
    score_total: float
    provenance: Provenance


@dataclass(frozen=True)
class CorporateContextChunk(ContextChunk):
    embedding: list[float] = field(default_factory=list)


@dataclass(frozen=True)
class RawNewsRecord:
    collect_date: str
    article_id: str
    article_date: str | None
    source: str | None
    url: str
    title: str
    snippet: str | None
    doc_text: str
    query_used: str
    lang: str
    fetched_at: str
    author: str | None = None
    publisher: str | None = None
    metadata: dict[str, Any] | None = None


@dataclass(frozen=True)
class NewsEventRecord:
    collect_date: str
    company_id: str
    company_name: str
    event_id: str
    mention_count: int
    representative_url: str
    representative_title: str
    representative_snippet: str | None
    representative_source: str | None
    representative_article_date: str | None
    rel_dense: float
    rel_rerank: float
    section_score: float
    global_section_score: float
    impact_score: float
    mention_score: float
    time_score: float
    final_score: float
    matched_chunk_ids: str
    matched_chunk_texts: str
    matched_chunk_section_types: str
    matched_chunk_similarities: str
    member_article_ids: list[str] = field(default_factory=list)
    member_urls: list[str] = field(default_factory=list)
    query_used: str | None = None
