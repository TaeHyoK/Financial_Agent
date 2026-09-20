"""Isolated section-weighting experiment; production ranking is not changed.

All profiles use frozen event clusters and the same article text. Equal and
weighted profiles share six DART sections, normalization and candidate budgets.
No analyst reference, future return or generated report is used in ranking.
"""
from __future__ import annotations

import copy
import hashlib
import math
import re
from collections import defaultdict
from datetime import date

import numpy as np
from bs4 import BeautifulSoup

WEIGHTS = {"sales": .30, "earnings": .20, "products": .15,
           "contracts": .15, "materials_facilities": .10, "overview": .10}
EQUAL_WEIGHTS = {key: 1 / len(WEIGHTS) for key in WEIGHTS}
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
    if set(scores) != set(weights) or not math.isclose(sum(weights.values()), 1):
        raise ValueError("All six sections and normalized weights are required")
    arrays = [np.asarray(scores[key], dtype=float) for key in weights]
    if any(not np.all(np.isfinite(a)) or np.any(a < 0) or np.any(a > 1) for a in arrays):
        raise ValueError("Section scores must be finite and in [0,1]")
    return sum(weights[key] * np.asarray(scores[key]) for key in weights)


def week_key(event):
    d = date.fromisoformat(event["representative"]["time"][:10])
    y, w, _ = d.isocalendar()
    return f"{y}-W{w:02d}"


def select_by_week(events, scores, k):
    if len(events) != len(scores) or k <= 0:
        raise ValueError("Invalid selection arguments")
    buckets = defaultdict(list)
    for i, event in enumerate(events):
        if not np.isfinite(scores[i]):
            raise ValueError("Non-finite ranking score")
        buckets[week_key(event)].append(i)
    result = []
    for week in sorted(buckets):
        result.extend(sorted(buckets[week], key=lambda i: (-float(scores[i]),
                      -date.fromisoformat(events[i]["representative"]["time"][:10]).toordinal(),
                      str(events[i]["event_id"])))[:k])
    return result


def make_report_context(original, events, weekly, final, section_scores, weights, profile):
    out = copy.deepcopy(original)
    out["news_events_all"] = copy.deepcopy(events)
    ranked = sorted(weekly, key=lambda i: (-float(aggregate(section_scores, weights)[i]),
                    str(events[i]["event_id"])))
    rank = {i: n + 1 for n, i in enumerate(ranked)}
    total = aggregate(section_scores, weights)
    for i, event in enumerate(out["news_events_all"]):
        event.pop("evidence", None)  # Old matched DART chunks do not explain new ranks.
        event["relevance_rank"] = rank.get(i, 0)
        event["scores"] = {"final_score": float(total[i]),
                           "section_scores": {k: float(v[i]) for k, v in section_scores.items()}}
    out["news_events_weekly"] = [copy.deepcopy(out["news_events_all"][i]) for i in weekly]
    out["news_events_final"] = [copy.deepcopy(out["news_events_all"][i]) for i in final]
    out["news_selection"].update({"stage": profile, "metric": "section_weighted_similarity",
        "section_weights": weights, "weekly_selected_event_count": len(weekly),
        "selected_event_count": len(final), "article_text_policy": "frozen_title_only_all_conditions"})
    return out
