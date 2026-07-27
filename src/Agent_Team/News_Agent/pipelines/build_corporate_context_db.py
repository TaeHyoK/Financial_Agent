"""Pipeline to build Corporate Context DB from DART report."""

from __future__ import annotations

from dataclasses import asdict
from typing import Any

from ..dart.parser_adapter import extract_business_section
from ..dart.section_extractor import extract_sections
from ..dart.chunker import build_context_chunks
from ..io.storage import save_json, save_jsonl
from ..ranking.embedding import EmbeddingModel
from .utils import resolve_data_root


def build_context_db(
    *,
    config: dict[str, Any],
    company_id: str,
    company_name: str,
    report_key: str,
    report_date: str,
    report_path: str,
) -> None:
    data_root = resolve_data_root(config)
    dart_cfg = config.get("dart", {})

    business_text, provenance = extract_business_section(
        report_path=report_path,
        parser_py_path=dart_cfg.get("parser_py_path"),
    )

    sections = extract_sections(
        business_text=business_text,
        sections_to_extract=dart_cfg.get("sections_to_extract", []),
    )

    dart_sections_payload = {
        "company_id": company_id,
        "company_name": company_name,
        "report_key": report_key,
        "report_date": report_date,
        "sections": [
            {
                "section_name": section.section_name,
                "raw_text": section.raw_text,
                "provenance": {
                    **section.provenance,
                    "source": provenance.get("source"),
                    "parser": provenance.get("parser"),
                    "offset_hint": provenance.get("offset_hint"),
                },
            }
            for section in sections
        ],
    }

    sections_path = data_root / "dart" / "sections" / company_id / report_key / "dart_sections.json"
    save_json(dart_sections_payload, sections_path)

    chunk_config = dart_cfg.get("chunking", {})
    chunks = build_context_chunks(
        sections=sections,
        company_id=company_id,
        company_name=company_name,
        report_key=report_key,
        report_date=report_date,
        chunk_config=chunk_config,
    )

    chunks_path = data_root / "dart" / "chunks" / company_id / report_key / "context_chunks.jsonl"
    save_jsonl([asdict(chunk) for chunk in chunks], chunks_path)

    model_cfg = config.get("models", {})
    embedder = EmbeddingModel(
        model_cfg.get("embedding_model_name", "BAAI/bge-m3"),
        device=model_cfg.get("device", "cpu"),
        batch_size=int(model_cfg.get("batch_size", 32)),
    )
    embeddings = embedder.encode([chunk.text for chunk in chunks])

    context_records: list[dict] = []
    for chunk, embedding in zip(chunks, embeddings):
        payload = asdict(chunk)
        payload["embedding"] = embedding.tolist()
        context_records.append(payload)

    context_db_path = data_root / "db" / "corporate_context" / company_id / report_key / "corporate_context_db.jsonl"
    save_jsonl(context_records, context_db_path)
