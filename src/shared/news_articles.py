"""Selected articles for News primary input and monthly subdata summarization."""
from copy import deepcopy
from datetime import date, timedelta

from .time_windows import monthly_windows
from .news_selection import event_period

ARTICLE_NEWS_POLICY = "monthly_selected_articles_v1"
ARTICLE_NEWS_LABEL = "월별 개별 뉴스"


def build_article_packet(report, *, period_count=12):
    """Arrange the weekly selection without summarizing or selecting it again."""
    selection = report.get("news_selection") or {}
    if selection.get("raw_news_policy") != ARTICLE_NEWS_POLICY:
        raise ValueError("Rebuild news selection with the article-only input policy")
    rows = report.get("news_events_weekly")
    if not isinstance(rows, list):
        raise ValueError("Missing weekly news selection; no candidate-pool fallback")
    end = date.fromisoformat(report["collect_date"]) + timedelta(days=1)
    windows = monthly_windows(end, period_count)
    events, seen = [], set()
    for row in rows:
        source = row["representative"]
        key, stamp = str(row["event_id"]), str(source.get("time") or "")[:10]
        period = event_period(stamp, windows)
        if not key or key in seen or period is None:
            raise ValueError("News article ID/date is missing, duplicated or outside the requested period")
        if not str(source.get("title") or "").strip() or not str(source.get("snippet") or "").strip():
            raise ValueError("Selected article lacks a prepared title or snippet")
        seen.add(key)
        events.append({"event_id": key, "period": period, "time": stamp,
                       "title": source["title"], "snippet": source["snippet"],
                       "source": source.get("source") or "",
                       "event_timeline": deepcopy(row.get("event_timeline") or [])})
    events.sort(key=lambda row: (row["time"], row["event_id"]))
    return {"metadata": {"raw_news_policy": ARTICLE_NEWS_POLICY, "granularity": "month",
                         "company": deepcopy(report.get("company") or {}),
                         "collect_date": report["collect_date"], "period_count": period_count},
            "events": events,
            "periods": [{**window, "events": [e for e in events if e["period"] == window["period"]]}
                        for window in windows]}


def article_catalog(packet):
    """The same IDs and source text serve as primary or secondary evidence."""
    if (packet.get("metadata") or {}).get("raw_news_policy") != ARTICLE_NEWS_POLICY:
        raise ValueError("Expected article-only news packet, not a saved model summary")
    catalog = {}
    for event in packet["events"]:
        key = f"NEWS_RAW_{event['period']}_{event['event_id']}"
        if key in catalog:
            raise ValueError(f"Duplicate news evidence ID: {key}")
        catalog[key] = {"evidence_id": key, "domain": "news", "source_domain": "news",
                        "origin_type": "raw_source", "source_type": "recent_raw_event",
                        "source_ref": f"news_events.{event['period']}.{event['event_id']}",
                        "source_date": event["time"], **deepcopy(event)}
    return catalog


def articles_for_llm(catalog):
    """Omit storage metadata, not article content; preserve publication dates."""
    return {key: {field: deepcopy(row[field]) for field in
                  ("source_date", "title", "snippet", "source", "event_timeline")
                  if row.get(field) not in (None, "", [], {})}
            for key, row in catalog.items()}
