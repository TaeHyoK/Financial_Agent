"""Translate cited context evidence to exact Strategy card links, not guessed links."""
from __future__ import annotations

import copy
import hashlib
import re

from shared.subdata_guidance import CONTEXT_USAGE


def build_context_links(*, input_bundle, cards, provenance, add_card, card_factory, included_domains):
    reports = input_bundle.get("target_reports") or {}
    catalogs = {}
    metadata = {}
    canonical = lambda domain: "market" if domain == "yfinance" else domain
    included = {canonical(domain) for domain in included_domains}

    def register(catalog, domain, meta=None):
        for key, value in (catalog or {}).items():
            if not isinstance(value, dict):
                continue
            actual_domain = canonical(value.get("domain") or domain)
            handle = (actual_domain, str(key))
            # Domain catalogs can describe the same observation with different
            # wrappers. Keep a recipient's original subdata wrapper for new cards.
            catalogs[handle] = value
            if meta:
                metadata[handle] = {k: copy.deepcopy(v) for k, v in meta.items() if k != "evidence_catalog"}

    for domain, catalog in (input_bundle.get("evidence_catalogs") or {}).items():
        register(catalog, domain)
    for domain, wrapper in reports.items():
        report = wrapper.get("output") or wrapper
        register(report.get("primary_evidence_catalog"), domain)
        register(report.get("secondary_context_catalog"), domain)
        for other, block in (report.get("secondary_context") or {}).items():
            register(block.get("evidence_catalog"), other, block)
        if domain == "financial":
            register({row["evidence_id"]: row for row in (report.get("strategy_handoff") or {}).get("key_evidence", [])
                      if isinstance(row, dict) and row.get("evidence_id")}, domain)

    def resolve(domain, evidence_id, *, strict):
        matching = [
            key for key, card in cards.items()
            if card.get("domain") == domain and key != "financial.filing_basis"
            and evidence_id in (provenance.get(key) or {}).get("source_evidence_ids", [])
        ]
        if matching:
            return matching
        evidence = catalogs.get((domain, evidence_id))
        if not evidence:
            if strict:
                raise ValueError(f"Missing cited context source: {domain}/{evidence_id}")
            return []
        # This is an original observation/summary, NOT the agent's interpretation.
        # Avoid opaque IDs in LLM-facing content; preserve IDs in provenance.
        slug = re.sub(r"[^a-z0-9가-힣_]+", "_", str(evidence.get("metric") or "source").lower()).strip("_")
        digest = hashlib.sha256(f"{domain}:{evidence_id}".encode()).hexdigest()[:12]
        key = f"{domain}.context_source.{slug or 'source'}_{digest}"
        observation = {k: copy.deepcopy(v) for k, v in evidence.items()
                       if k not in {"evidence_id", "source_ref"} and not k.endswith("_ids")}
        origin = evidence.get("origin_type") or evidence.get("evidence_origin") or "raw_source"
        observation["evidence_origin"] = origin
        observation["source_scope"] = metadata.get((domain, evidence_id), {})
        if domain == "news":
            observation["source_periods"] = [evidence["period"]] if evidence.get("period") else []
            observation["date_precision"] = "period" if origin == "model_summarized" else "publication_date"
            observation["event_summary"] = evidence.get("text") or evidence.get("snippet") or evidence.get("title") or ""
        label = str(evidence.get("label") or evidence.get("metric_or_event") or evidence.get("metric") or "참고자료")
        card = card_factory(
            key, domain=domain, card_type="context_source", label=label,
            allowed_sections=("investment_thesis", "financial_view", "catalyst_view", "market_price_view", "risk_view"),
            evidence_family=f"{domain}_source", observation_basis="reference",
            observation=observation, role="reference",
        )
        add_card(card, raw_ids=[evidence_id], source_paths=[
            str(evidence.get("source_ref") or f"target_reports.{domain}.source_catalog")
        ])
        return [key]

    result = {}
    for domain, wrapper in reports.items():
        origin_domain = canonical(domain)
        if origin_domain not in included:
            continue
        report = wrapper.get("output") or wrapper
        main = report.get("overall_assessment") if origin_domain == "news" else report.get("main_view")
        main = main or {}
        used_contexts = set(main.get("context_ids") or [])
        links = []
        for item in report.get("secondary_context_assessment") or []:
            secondary_domain = canonical(item.get("source_domain"))
            if secondary_domain not in included:
                continue
            strict = item.get("usage") == CONTEXT_USAGE
            primary = [key for ref in item.get("primary_evidence_ids") or []
                       for key in resolve(origin_domain, str(ref), strict=strict)]
            secondary = [key for ref in item.get("secondary_evidence_ids") or []
                         for key in resolve(secondary_domain, str(ref), strict=strict)]
            links.append({
                "source_domain": secondary_domain,
                "effect": item.get("effect"),
                "usage": item.get("usage"),
                "statement": item.get("statement"),
                "judgment_impact": item.get("judgment_impact", ""),
                "limitation": item.get("limitation", ""),
                "used_in_domain_conclusion": item.get("context_id") in used_contexts,
                "primary_card_keys": list(dict.fromkeys(primary)),
                "secondary_card_keys": list(dict.fromkeys(secondary)),
            })
        # Also preserve primary sources cited only by the new combined summary.
        main_primary = main.get("primary_evidence_ids") or main.get("analysis_evidence_ids") or []
        conclusion_keys = [key for ref in main_primary
                           for key in resolve(origin_domain, str(ref), strict=False)]
        result[origin_domain] = {"assessments": links, "conclusion_card_keys": list(dict.fromkeys(conclusion_keys))}
    return result
