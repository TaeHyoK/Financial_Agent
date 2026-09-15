"""Common, pre-selection news eligibility; no LLM or relevance scoring."""
from collections import Counter
from dataclasses import replace
from pathlib import Path
import re

from .google_news_collector import SNIPPET_POLICY
from ..dart.company_relations import name_key as _name_key, prefix_aliases, read_company_relations


CANDIDATE_POLICY = "all_snippets_then_entity_filter_v3"


class CompanyNewsFilter:
    """Exclude other-company-only mentions; rank relevance using DART embeddings.

    Consolidated subsidiaries belong to the analysis scope. Unrecognized names
    are not evidence of irrelevance, and recognized names do not establish who
    owns a financial figure in the article.
    """

    def __init__(self, company_name, report_path):
        path = Path(report_path)
        relations = read_company_relations(path.read_text(encoding="utf-8"))
        self.targets = {_name_key(company_name)}
        if relations['legal_name']:
            self.targets.add(_name_key(relations['legal_name']))
        target_aliases = relations['registrations'].get(relations['target_registration'], set())
        self.targets.update(_name_key(name) for name in target_aliases)
        affiliate_names = relations['names']['affiliates']
        subsidiary_names = set(relations['names']['subsidiaries'])
        for aliases in relations['registrations'].values():
            if {_name_key(name) for name in aliases} & {_name_key(name) for name in subsidiary_names}:
                subsidiary_names.update(aliases)
        names = affiliate_names | subsidiary_names
        normalized = {_name_key(name) for name in names} - self.targets - {""}
        self.subsidiary_names = {_name_key(name) for name in subsidiary_names} - self.targets - {""}
        other_names = normalized - self.subsidiary_names
        spelling_aliases = prefix_aliases(self.targets, normalized | self.targets)
        self.subsidiary_names.update(alias for alias, name in spelling_aliases.items() if name in self.subsidiary_names)
        other_names.update(alias for alias, name in spelling_aliases.items() if name in other_names)
        # A disclosed company name that is also another company's prefix cannot
        # safely exclude articles on a bare substring match (any corporate group).
        all_names = normalized | self.targets | set(spelling_aliases)
        ambiguous = {name for name in other_names
                     if any(longer != name and longer.startswith(name) for longer in all_names)}
        self.other_names = other_names - ambiguous
        self.pattern = re.compile("|".join(re.escape(name) for name in
                                          sorted(all_names, key=lambda s: (-len(s), s))))
        self.source = {"report_path": str(path), "disclosed_company_names": sorted(names),
                       "target_names": sorted(self.targets),
                       "consolidated_company_names": sorted(subsidiary_names),
                       "consolidated_names": sorted(self.subsidiary_names),
                       "other_related_names": sorted(self.other_names),
                       "ignored_ambiguous_names": sorted(ambiguous),
                       "target_registration": relations['target_registration'],
                       "target_aliases_from_registration": sorted(target_aliases),
                       "derived_prefix_aliases": dict(sorted(spelling_aliases.items())),
                       "table_status": relations['tables'],
                       "scope": "disclosed_affiliates_and_consolidated_subsidiaries"}

    def classify(self, record):
        fields = [record.title or "", record.snippet or ""]
        matches = {match.group() for field in fields for match in self.pattern.finditer(_name_key(field))}
        other = sorted(matches & self.other_names)
        target = sorted(matches & self.targets)
        subsidiaries = sorted(matches & self.subsidiary_names)
        scope = ("target" if target else "consolidated_subsidiary" if subsidiaries
                 else "other_related_only" if other else "undetermined")
        if not (record.snippet or "").strip():
            status = "missing_snippet"
        elif scope == "other_related_only":
            status = "other_related_only"
        else:
            status = "eligible"
        return {"status": status, "company_scope": scope, "target_names": target,
                "consolidated_names": subsidiaries, "related_names": other}


def prepare_candidates(records, *, collector, company_filter, notes):
    """Fetch every unique-URL candidate before any semantic merge or ranking.

    Preserve all attempted records with exclusion reasons; only eligible records
    proceed. The shared snapshot, not a condition-specific fetch, fixes input text.
    """
    enriched = collector._enrich_records(records, notes)
    audited, eligible = [], []
    for record in enriched:
        verdict = company_filter.classify(record)
        metadata = {**(record.metadata or {}), "candidate_policy": CANDIDATE_POLICY,
                    "candidate_eligibility": verdict}
        row = replace(record, metadata=metadata,
                      doc_text=f"{record.title} [SEP] {record.snippet or ''}")
        audited.append(row)
        if verdict["status"] == "eligible":
            eligible.append(row)
    audit = {"policy": CANDIDATE_POLICY, "snippet_policy": SNIPPET_POLICY,
             "attempted_count": len(audited), "eligible_count": len(eligible),
             "status_counts": dict(Counter(r.metadata["candidate_eligibility"]["status"] for r in audited)),
             "missing_reasons": dict(Counter((r.metadata or {}).get("snippet_failure_reason", "not_recorded")
                                             for r in audited if not (r.snippet or "").strip())),
             "entity_source": company_filter.source}
    return audited, eligible, audit


def require_common_candidate_pool(report):
    """Check data lineage, not generated opinions; never repair an old selection."""
    preparation = (report.get("news_selection") or {}).get("candidate_preparation") or {}
    if preparation.get("policy") != CANDIDATE_POLICY:
        raise ValueError("Regenerate the common Full source: all candidate snippets must precede filtering and clustering")
    pool = {}
    for event in report.get("news_events_all", []):
        rep = event["representative"]
        if not (rep.get("snippet") or "").strip() or rep.get("snippet_metadata", {}).get("snippet_policy") != SNIPPET_POLICY:
            raise ValueError("Common news pool contains missing or unprepared snippets; regenerate the common Full source")
        pool[str(event["event_id"])] = rep
    if not pool:
        raise ValueError("Common news pool is empty; regenerate the common Full source")
    for view in ("news_events_weekly", "news_events_final"):
        for event in report.get(view, []):
            if pool.get(str(event["event_id"])) != event["representative"]:
                raise ValueError("Selected news text differs from the frozen common candidate pool")
