"""Extract only section 4 financial statement subsections from DART XML."""

from __future__ import annotations

import re
from dataclasses import dataclass
from html import unescape

try:
    from .models import SectionMap
    from .table_parser import parse_statement_tables
except ImportError:  # pragma: no cover - supports direct script execution
    from models import SectionMap
    from table_parser import parse_statement_tables


SECTION_TITLES = {
    "4-1": "재무상태표",
    "4-2": "포괄손익계산서",
    "4-3": "자본변동표",
    "4-4": "현금흐름표",
}

_TITLE_RE = re.compile(r"<TITLE\b[^>]*>(.*?)</TITLE>", flags=re.IGNORECASE | re.DOTALL)


@dataclass(frozen=True)
class TitleMatch:
    key: str
    title: str
    start: int


def extract_section_four(xml_text: str) -> SectionMap:
    """Extract 4-1 through 4-4 statement tables from a DART document."""

    title_matches = list(_iter_titles(xml_text))
    starts = _find_subsection_starts(title_matches)

    output: SectionMap = {
        key: {"section_title": section_title, "tables": []}
        for key, section_title in SECTION_TITLES.items()
    }

    for index, match in enumerate(starts):
        end = _subsection_end(xml_text, title_matches, starts, index)
        fragment = xml_text[match.start:end]
        output[match.key] = {
            "section_title": SECTION_TITLES[match.key],
            "tables": parse_statement_tables(fragment, SECTION_TITLES[match.key]),
        }

    return output


def _iter_titles(xml_text: str) -> list[TitleMatch]:
    matches: list[TitleMatch] = []
    for match in _TITLE_RE.finditer(xml_text or ""):
        title = _clean_text(match.group(1))
        key = _section_key_from_title(title)
        matches.append(TitleMatch(key=key, title=title, start=match.start()))
    return matches


def _find_subsection_starts(title_matches: list[TitleMatch]) -> list[TitleMatch]:
    exact: dict[str, TitleMatch] = {}
    for match in title_matches:
        if match.key in SECTION_TITLES and match.key not in exact:
            exact[match.key] = match

    if len(exact) == len(SECTION_TITLES):
        return sorted(exact.values(), key=lambda item: item.start)

    # Fallback: inside "4. 재무제표", accept unnumbered statement titles
    # until the next top-level financial-note section.
    section_four_start = None
    section_four_end = None
    for index, match in enumerate(title_matches):
        if _is_section_four_title(match.title):
            section_four_start = match.start
            for later in title_matches[index + 1 :]:
                if _is_after_section_four_boundary(later.title):
                    section_four_end = later.start
                    break
            break

    if section_four_start is None:
        return sorted(exact.values(), key=lambda item: item.start)

    fallback = dict(exact)
    for match in title_matches:
        if match.start <= section_four_start:
            continue
        if section_four_end is not None and match.start >= section_four_end:
            break
        for key, statement_title in SECTION_TITLES.items():
            if key not in fallback and statement_title in match.title:
                fallback[key] = TitleMatch(key=key, title=match.title, start=match.start)

    return sorted(fallback.values(), key=lambda item: item.start)


def _subsection_end(
    xml_text: str,
    title_matches: list[TitleMatch],
    starts: list[TitleMatch],
    index: int,
) -> int:
    current = starts[index]
    if index + 1 < len(starts):
        return starts[index + 1].start

    for match in title_matches:
        if match.start <= current.start:
            continue
        if _is_after_section_four_boundary(match.title):
            return match.start
    return len(xml_text)


def _section_key_from_title(title: str) -> str:
    compact = re.sub(r"\s+", "", title or "")
    for key in SECTION_TITLES:
        number = key.replace("-", r"[-.]?")
        if re.match(rf"^{number}", compact):
            return key
    return ""


def _is_section_four_title(title: str) -> bool:
    compact = re.sub(r"\s+", "", title or "")
    return bool(re.match(r"^4[.)]?(재무제표|재무상태표)?$", compact))


def _is_after_section_four_boundary(title: str) -> bool:
    compact = re.sub(r"\s+", "", title or "")
    if re.match(r"^5[.)-]?", compact):
        return True
    if "재무제표주석" in compact:
        return True
    return bool(re.match(r"^(IV|Ⅳ|V|Ⅴ)[.)]", title.strip(), flags=re.IGNORECASE))


def _clean_text(value: str) -> str:
    text = re.sub(r"<[^>]+>", " ", value or "")
    text = unescape(text)
    text = text.replace("\xa0", " ").replace("\u3000", " ")
    return re.sub(r"\s+", " ", text).strip()
