"""Six-section DART similarity used by production news selection.

Weights are a fixed design choice, not a validated optimum. Extraction and score
normalization match the 2026-09-09 section-weighting pilot.
"""

import hashlib
import math
import re

import numpy as np
from bs4 import BeautifulSoup

WEIGHTS = {"sales": .30, "earnings": .20, "products": .15,
           "contracts": .15, "materials_facilities": .10, "overview": .10}
SECTION_TITLES = {"sales": "매출 및 수주상황", "products": "주요 제품 및 서비스",
                  "contracts": "주요계약 및 연구개발활동",
                  "materials_facilities": "원재료 및 생산설비", "overview": "사업의 개요"}


def clean_title(value):
    return re.sub(r"^\s*[\dⅠⅡⅢⅣⅤⅥⅦⅧⅨⅩ]+[.\-\s]*", "", value).strip()


def extract_section_chunks(xml_text, *, company, report_date, max_chars=400):
    """Keep XML TE/TU financial cells as well as TD/TH, including row context.

    No income-statement notes, forecasts, or other documents are substituted for
    the statement. All resulting chunks are used; there is no keyword selection.
    """
    if max_chars <= 0:
        raise ValueError("max_chars must be positive")
    # DART XML-like exports can contain unclosed inline tags. Parsing the whole
    # document with XML recovery may nest all later sections inside an earlier
    # one. Bound fragments by literal section closing tags before DOM recovery.
    def fragments(tag):
        pattern = rf"<{tag}\b[^>]*>.*?</{tag}>"
        return [BeautifulSoup(m.group(), "xml").find(tag)
                for m in re.finditer(pattern, xml_text, re.S | re.I)]
    sections = fragments('SECTION-2')
    nodes = {}
    for section, title in SECTION_TITLES.items():
        matches = [n for n in sections if n.find('TITLE') is not None
                   and clean_title(n.find('TITLE').get_text(" ", strip=True)) == title]
        if len(matches) != 1:
            raise ValueError(f"{company}: expected one {title}, found {len(matches)}")
        nodes[section] = matches[0]
    statements = [n.find('TITLE') for n in fragments('TABLE-GROUP')
                  if n.find('TITLE') is not None and "손익계산서" in n.find('TITLE').get_text()]
    consolidated = [t for t in statements if "연결" in t.get_text()]
    candidates = consolidated or [t for t in statements if "연결" not in t.get_text()]
    # Some reports publish income and comprehensive income in separate tables.
    # Use the actual income statement when both are present in the same scope.
    ordinary_income = [t for t in candidates if "포괄" not in t.get_text()]
    candidates = ordinary_income or candidates
    if len(candidates) != 1:
        raise ValueError(f"{company}: ambiguous/missing income statement ({len(candidates)})")
    nodes["earnings"] = candidates[0].parent
    result = []
    for section in WEIGHTS:
        node = nodes[section]
        title = node.find("TITLE").get_text(" ", strip=True)
        # Serialize each row with its column headers; do not discard XBRL TE cells.
        fragment = BeautifulSoup(str(node), "xml")
        for table in list(fragment.find_all("TABLE")):
            if table.parent is None:
                continue
            headers = []
            rows = []
            for tr in table.find_all("TR"):
                cells = tr.find_all(["TD", "TH", "TE", "TU"], recursive=False)
                values = [c.get_text(" ", strip=True) for c in cells]
                if not any(values):
                    continue
                text = " | ".join(values)
                if any(c.name == "TH" for c in cells):
                    headers.append(text)
                else:
                    rows.append((" / ".join(headers) + " : " if headers else "") + text)
            table.replace_with("\n" + "\n".join(rows or headers) + "\n")
        text = fragment.get_text("\n", strip=True)
        lines = [re.sub(r"\s+", " ", line).strip() for line in text.splitlines() if line.strip()]
        prefix = f"{company} | {title} | 공시일 {report_date}\n"
        # Preserve all text. Long rows are split but the section identity remains.
        pieces = []
        current = ""
        for line in lines:
            for offset in range(0, len(line), max_chars):
                part = line[offset:offset + max_chars]
                if current and len(current) + len(part) + 1 > max_chars:
                    pieces.append(current)
                    current = ""
                current += ("\n" if current else "") + part
        if current:
            pieces.append(current)
        for index, piece in enumerate(pieces):
            text = prefix + piece
            result.append({"section": section, "section_title": title, "index": index,
                           "text": text, "chunk_id": hashlib.sha256(text.encode()).hexdigest()[:20],
                           "source_report_date": report_date})
    return result


def aggregate(scores, weights):
    if (set(scores) != set(WEIGHTS) or set(weights) != set(WEIGHTS)
            or any(not math.isfinite(v) or v < 0 for v in weights.values())
            or not math.isclose(sum(weights.values()), 1)):
        raise ValueError("All six sections and normalized weights are required")
    arrays = [np.asarray(scores[key], dtype=float) for key in weights]
    if any(a.shape != arrays[0].shape for a in arrays):
        raise ValueError("Section score shapes must match")
    if any(not np.all(np.isfinite(a)) or np.any(a < 0) or np.any(a > 1) for a in arrays):
        raise ValueError("Section scores must be finite and in [0,1]")
    return sum(weights[key] * np.asarray(scores[key]) for key in weights)


POLICY_VERSION = "section_weighted_v1"


def configured_weights(config):
    """Legacy configurations remain readable; the default YAML enables weights."""
    settings = config.get("scoring", {}).get("section_weighting", {})
    if not settings.get("enabled", False):
        return None
    weights = {key: float(value) for key, value in settings.get("weights", WEIGHTS).items()}
    aggregate({key: 0.0 for key in WEIGHTS}, weights)
    return weights


def section_indices(context_records):
    groups = {key: [] for key in WEIGHTS}
    for index, record in enumerate(context_records):
        section = record["section_type"]
        if section not in groups or record.get("ranking_policy") != POLICY_VERSION:
            raise ValueError("Stale/incompatible corporate context: rebuild the six-section context DB")
        groups[section].append(index)
    if any(not indices for indices in groups.values()):
        raise ValueError("All six report sections are required; rebuild corporate context")
    return groups


def dense_scores(similarities, groups):
    clipped = np.clip(np.asarray(similarities), 0.0, 1.0)
    return {key: clipped[..., indices].max(axis=-1) for key, indices in groups.items()}
