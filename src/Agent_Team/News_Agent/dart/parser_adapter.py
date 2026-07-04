"""Adapter for DART parser to extract sections."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from . import parser as internal_parser

def load_parser(parser_py_path: str | None):
    if parser_py_path:
        path = Path(parser_py_path)
        if path.exists():
            import importlib.util

            spec = importlib.util.spec_from_file_location("legacy_dart_parser", str(path))
            if spec is None or spec.loader is None:
                raise ImportError(f"Cannot load parser module from {parser_py_path}")
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
            return module
    return internal_parser


def extract_business_section(
    *,
    report_path: str,
    parser_py_path: str | None = None,
    title_keywords: list[str] | None = None,
) -> tuple[str, dict[str, Any]]:
    module = load_parser(parser_py_path)
    content = Path(report_path).read_text(encoding="utf-8")

    title_keywords = title_keywords or [
        "사업의 내용",
        "1. 사업의 내용",
        "사업내용",
    ]

    section1_fragment = None
    found_keyword = None
    if hasattr(module, "extract_section_1_html"):
        for keyword in title_keywords:
            section1_fragment = module.extract_section_1_html(content, keyword)
            if section1_fragment:
                found_keyword = keyword
                break

    section2_fragments = None
    if section1_fragment and hasattr(module, "extract_section_2_fragments"):
        section2_fragments = module.extract_section_2_fragments(section1_fragment)

    if section2_fragments:
        section_texts = [module.html_to_text(fragment) for _, fragment in section2_fragments]
        raw_text = "\n\n".join(text for text in section_texts if text)
        offset_hint = f"section1_keyword:{found_keyword}|section2_count:{len(section2_fragments)}"
    else:
        fragment = None
        for keyword in title_keywords:
            fragment = module.extract_section_html(content, keyword)
            if fragment:
                found_keyword = keyword
                break

        if fragment:
            raw_text = module.html_to_text(fragment)
            offset_hint = f"matched_keyword:{found_keyword}"
        else:
            raw_text = module.html_to_text(content)
            offset_hint = "full_document"

    provenance = {
        "source": "DART",
        "parser": "parser.py",
        "offset_hint": offset_hint,
    }

    return raw_text, provenance
