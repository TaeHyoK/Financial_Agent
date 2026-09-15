"""Apply the production excerpt collector to frozen, already-selected news."""
from datetime import datetime, timezone

from .google_news_collector import GoogleNewsCollector, SNIPPET_POLICY
from .candidate_preparation import require_common_candidate_pool
from ..dart.schemas import RawNewsRecord


EVENT_VIEWS = ("news_events_all", "news_events_weekly", "news_events_final", "news_events_topk")


def enrich_report_snippets(reports, *, collector=None):
    """Mutate excerpt fields only; shared representative articles are fetched once.

    A report may be Full, random, or a batch of both. Previously processed
    excerpts (including missing ones) are frozen, not retried per condition.
    """
    if len({(r["collect_date"], r["company"]["company_name"]) for r in reports}) > 1:
        raise ValueError("Snippet batches must share a company and collection date")
    # Pre-selection snapshots, including older versions, must not fall through
    # to legacy post-selection fetching when the company-filter policy changes.
    if any("candidate_preparation" in (r.get("news_selection") or {}) for r in reports):
        for report in reports:
            require_common_candidate_pool(report)
        # Full and Random intentionally have different filtered pools; both
        # must retain the identical frozen prefilter pool when it is available.
        pools = [{str(e['event_id']): e['representative'] for e in
                  r.get('news_events_prefilter', r['news_events_all'])} for r in reports]
        if any(pool != pools[0] for pool in pools[1:]):
            raise ValueError("Conflicting frozen common candidate pools")
        return {"policy": SNIPPET_POLICY, "source": "frozen_common_candidate_pool", "network_requests": 0,
                "unique_articles": len(pools[0]), "populated": len(pools[0]), "notes": []}
    collector = collector or GoogleNewsCollector()
    representatives, selected = {}, {}
    fetched_at = datetime.now(timezone.utc).isoformat()
    for report in reports:
        for view in EVENT_VIEWS:
            for event in report.get(view, []):
                rep = event["representative"]
                # Member IDs refer to the common collected article pool.
                key = str((event.get("member_article_ids") or [event["event_id"]])[0])
                identity = (report["collect_date"], key)
                representatives.setdefault(identity, []).append(rep)
                if view in {"news_events_weekly", "news_events_final"}:
                    selected[identity] = rep
    records = []
    for identity, rep in selected.items():
        existing = [r for r in representatives[identity]
                    if r.get("snippet_metadata", {}).get("snippet_policy") == SNIPPET_POLICY]
        if existing:
            rep = existing[0]
            if any((r.get("title"), r.get("snippet"), r.get("url")) !=
                   (rep.get("title"), rep.get("snippet"), rep.get("url")) for r in existing):
                raise ValueError(f"Conflicting frozen snippets for article {identity}")
        records.append(RawNewsRecord(collect_date=identity[0], article_id=identity[1],
            article_date=rep.get("time"), source=rep.get("source"), url=rep.get("url") or "",
            title=rep["title"], snippet=rep.get("snippet"), doc_text="", query_used="", lang="ko",
            fetched_at=fetched_at, metadata=rep.get("snippet_metadata") or {}))
    notes = []
    enriched = collector._enrich_records(records, notes)
    for record in enriched:
        identity = (record.collect_date, record.article_id)
        for rep in representatives[identity]:
            rep.update(snippet=record.snippet or "", url=record.url,
                       snippet_metadata={key: value for key, value in (record.metadata or {}).items()
                                         if key in {"snippet_policy", "snippet_source"}})
    return {"policy": SNIPPET_POLICY, "unique_articles": len(records),
            "populated": sum(bool(r.snippet) for r in enriched), "notes": notes}


def require_prepared_summary_snippets(report, *, summary_input=None):
    """A missing excerpt is valid; an unattempted Full input is not comparable."""
    for event in [*report.get("news_events_weekly", []), *report.get("news_events_final", [])]:
        if event["representative"].get("snippet_metadata", {}).get("snippet_policy") != SNIPPET_POLICY:
            raise ValueError("Regenerate Full summary inputs with the shared snippet policy before ablation")
    if summary_input is not None:
        expected = {str(e["event_id"]): e["representative"].get("snippet") or ""
                    for e in report["news_events_weekly"]}
        actual = {str(e["event_id"]): e.get("snippet") or ""
                  for period in summary_input["periods"] for e in period["events"]}
        if actual != expected:
            raise ValueError("Regenerate Full summaries: their article snippets differ from the prepared source")
