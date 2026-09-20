"""Text and recommendation extraction for PDF references and HTML candidates."""

from __future__ import annotations

import re
import hashlib
import json
import unicodedata
from dataclasses import dataclass
from pathlib import Path

import pdfplumber
from bs4 import BeautifulSoup

from ablation_suite.recommendations import read_explicit_rating

REFERENCE_REGIONS = Path(__file__).with_name("reference_regions.json")
BODY_POLICY_VERSION = "narrative_body_v1"


def body_policy_hash() -> str:
    return hashlib.sha256(REFERENCE_REGIONS.read_bytes()).hexdigest()


def extract_pdf_body(path: str | Path) -> tuple[str, int]:
    """Read pre-reviewed narrative regions; unknown or modified PDFs need review."""
    source = Path(path).expanduser().resolve()
    manifest = json.loads(REFERENCE_REGIONS.read_text(encoding="utf-8"))
    entry = manifest["documents"].get(source.name)
    if not entry or hashlib.sha256(source.read_bytes()).hexdigest() != entry["sha256"]:
        raise ValueError(f"Reference PDF needs narrative-region review: {source}")
    parts = []
    with pdfplumber.open(source) as pdf:
        page_count = len(pdf.pages)
        for region in entry["regions"]:
            text = pdf.pages[region["page"] - 1].crop(tuple(region["bbox"])).extract_text(
                x_tolerance=1, y_tolerance=3, layout=False,
            ) or ""
            if not text.strip():
                raise ValueError(f"Empty reviewed PDF region: {source}, {region}")
            parts.append(text)
    body = normalize_text("\n".join(parts))
    if len(body) < 100:
        raise ValueError(f"Too little narrative text: {source}")
    return body, page_count


def extract_html_body(path: str | Path) -> str:
    """Use narrative sections only, excluding tabular/graphical/report boilerplate."""
    soup = BeautifulSoup(Path(path).read_text(encoding="utf-8"), "lxml")
    sections = soup.select("section.report-section")
    if not sections:
        raise ValueError(f"Generated report has no recognized narrative sections: {path}")
    parts = []
    for section in sections:
        for tag in section.select("table, figure, .report-chart-section, h1, h2, h3, script, style, svg"):
            tag.decompose()
        parts.extend(p.get_text(" ", strip=True) for p in section.select("p, li") if not p.find_parent("li"))
    text = normalize_text("\n".join(parts))
    if not text:
        raise ValueError(f"Generated report has no narrative body: {path}")
    return text


@dataclass(frozen=True)
class Recommendation:
    label: str
    matched_text: str


def normalize_text(text: str) -> str:
    """Normalize Unicode and whitespace without changing document order."""

    text = unicodedata.normalize("NFKC", text).replace("\x00", " ")
    lines = [re.sub(r"\s+", " ", line).strip() for line in text.splitlines()]
    return "\n".join(line for line in lines if line)


def extract_pdf_text(path: str | Path, *, max_pages: int = 0) -> tuple[str, int]:
    """Extract selectable text from a PDF in page order.

    ``max_pages=0`` means all pages. Scanned PDFs without selectable text are
    rejected rather than silently producing misleading near-zero scores.
    """

    source = Path(path).expanduser().resolve()
    page_text: list[str] = []
    with pdfplumber.open(source) as pdf:
        page_count = len(pdf.pages)
        pages = pdf.pages if max_pages <= 0 else pdf.pages[:max_pages]
        for page in pages:
            page_text.append(
                page.extract_text(x_tolerance=1, y_tolerance=3, layout=False) or ""
            )
    text = normalize_text("\n".join(page_text))
    if len(text) < 100:
        raise ValueError(
            f"PDF text extraction produced too little text ({len(text)} chars): {source}. "
            "OCR is required for scanned PDFs."
        )
    return text, page_count


def extract_html_text(path: str | Path) -> str:
    """Extract only user-visible text from the generated HTML report."""

    source = Path(path).expanduser().resolve()
    soup = BeautifulSoup(source.read_text(encoding="utf-8"), "lxml")
    for tag in soup(["script", "style", "noscript", "template", "svg"]):
        tag.decompose()
    root = soup.find("main") or soup.find("article") or soup.body or soup
    return normalize_text(root.get_text("\n", strip=True))


def extract_reference_recommendation(text: str) -> Recommendation:
    """Map a Korean analyst rating to FinRpt's buy/non-buy label."""

    head = text[:6000]
    patterns = (
        ("buy", r"(?i)(?:투자의견[^\n]{0,40})?(?:매수|\bBUY\b)"),
        ("sell", r"(?i)(?:투자의견[^\n]{0,40})?(?:매도|\bSELL\b|UNDERPERFORM)"),
        ("hold", r"(?i)(?:투자의견[^\n]{0,40})?(?:중립|보유|\bHOLD\b|NEUTRAL)"),
    )
    matches: list[tuple[int, str, str]] = []
    for label, pattern in patterns:
        for match in re.finditer(pattern, head):
            matches.append((match.start(), label, match.group(0)))
    if not matches:
        return Recommendation("unclear", "")
    _, label, matched = min(matches, key=lambda item: item[0])
    return Recommendation(label, normalize_text(matched))


def extract_generated_recommendations(path: str | Path) -> dict[str, Recommendation]:
    """Read the annual explicit rating and retain legacy textual diagnostics."""

    source = Path(path).expanduser().resolve()
    soup = BeautifulSoup(source.read_text(encoding="utf-8"), "lxml")
    heading = soup.find(id="investment-call-thesis-section-analysis")
    section = heading.parent if heading is not None else soup.find("main") or soup.body
    thesis = normalize_text(section.get_text(" ", strip=True) if section else "")

    existing_text = _bounded_clause(thesis, "기존 편입분", "신규 자금")
    new_text = _bounded_clause(thesis, "신규 자금", "")
    explicit = read_explicit_rating(source)
    return {
        "rating": Recommendation(str(explicit["label"]), str(explicit["matched_text"])),
        "existing_position": Recommendation(
            _classify_existing_position(existing_text), existing_text
        ),
        "new_entry": Recommendation(_classify_new_entry(new_text), new_text),
    }


def _bounded_clause(text: str, start: str, end: str) -> str:
    start_index = text.find(start)
    if start_index < 0:
        return ""
    fragment = text[start_index:]
    if end:
        end_index = fragment.find(end, len(start))
        if end_index >= 0:
            fragment = fragment[:end_index]
    sentence_end = re.search(r"[.!?](?:\s+|$)", fragment)
    if sentence_end and sentence_end.start() > 20:
        fragment = fragment[: sentence_end.end()]
    return normalize_text(fragment[:1000])


def _classify_existing_position(text: str) -> str:
    if not text:
        return "unclear"
    if re.search(
        r"비중\s*(?:축소|감축)(?:가|를|이)?\s*(?:적절|우선|타당|필요)|매도가?\s*(?:적절|우선|타당)",
        text,
    ):
        return "reduce"
    if re.search(r"비중\s*유지|유지(?:가|를|하되|하는| 쪽|가 더|를 우선)", text):
        return "hold"
    if re.search(
        r"(?:비중\s*축소|서둘러\s*줄일|즉시\s*줄일)[^.]{0,100}(?:필요.*크지\s*않|상황.*아니|정도.*아니|서두를.*아니)",
        text,
    ):
        return "hold"
    if re.search(r"비중\s*확대|추가\s*매수", text) and not re.search(
        r"확대(?:까지|는|를)?\s*(?:어렵|제한|신중|정당화되지|아니)", text
    ):
        return "increase"
    return "unclear"


def _classify_new_entry(text: str) -> str:
    if not text:
        return "unclear"
    if re.search(r"진입\s*(?:회피|금지)|매수\s*회피|접근을\s*피", text):
        return "avoid"
    if re.search(
        r"유보|진입을\s*(?:미루|기다)|대기|속도\s*조절|"
        r"서둘러\s*진입할\s*(?:근거|구간)[^.]{0,80}(?:없|않|아니)|"
        r"진입을\s*정당화[^.]{0,80}(?:어렵|않)|보수적\s*접근|신중하게\s*접근",
        text,
    ):
        return "wait"
    if re.search(
        r"분할\s*(?:진입|접근|매수)|소규모(?:\s*또는\s*분할)?\s*접근|"
        r"신규\s*(?:진입|매수)(?:이|가|을)?\s*(?:적절|가능|타당)",
        text,
    ):
        return "enter"
    return "unclear"
