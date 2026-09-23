"""One-team-only readers for a single integrated company report.

Legacy domain file arguments are resolved in memory. No domain analysis reports
are written; comparison and Strategy consume the integrated interpretation once.
"""
from __future__ import annotations

import copy
import json
from pathlib import Path

PROTOCOL = "unified_domain_team_v8_single_report"


def report_path(path):
    path = Path(path)
    if path.name != "final_report.json" or path.parent.parent.name not in {"Financial", "News", "Y_Finance"}:
        return None
    company = path.parent.parent.parent
    candidate = company / "runs" / path.parent.name / "unified_domain_team" / "unified_report.json"
    return candidate if candidate.is_file() else None


def read_report(path):
    candidate = report_path(path)
    if candidate is None:
        return None
    report = json.loads(candidate.read_text(encoding="utf-8"))
    if report.get("protocol") != PROTOCOL:
        raise ValueError(f"Unexpected integrated report protocol: {candidate}")
    return report


def fact_view(report, domain):
    if domain == "Financial":
        return copy.deepcopy(report["supporting_facts"]["financial"])
    if domain == "Y_Finance":
        return copy.deepcopy(report["supporting_facts"]["market"])
    return {}


def patch_fact_loader(module, name):
    original = getattr(module, name)
    def load(path, *args, **kwargs):
        report = read_report(path)
        if report is not None:
            return fact_view(report, Path(path).parent.parent.name)
        return original(path, *args, **kwargs)
    setattr(module, name, load)


def patch_peer_dataset(module):
    patch_fact_loader(module, "_load_json")
    original = module._has_required_peer_files
    def has_files(output_root, run_key):
        financial = module.agent_output_dir(output_root, run_key, "Financial") / "final_report.json"
        if report_path(financial):
            market = module.agent_output_dir(output_root, run_key, "Y_Finance") / "market_full_dataset.csv"
            return market.is_file()
        return original(output_root, run_key)
    module._has_required_peer_files = has_files
    original_dataset = module._build_dataset_payload
    def dataset(**kwargs):
        payload = original_dataset(**kwargs)
        for files in payload["source_files"].values():
            for key in ("financial_final_report", "yfinance_final_report"):
                resolved = report_path(files[key])
                if resolved:
                    files[key] = str(resolved)
        return payload
    module._build_dataset_payload = dataset


def clean(value):
    """Keep source content while removing machine reference fields from prose."""
    if isinstance(value, dict):
        return {key: clean(child) for key, child in value.items()
                if not key.endswith(("_ids", "_id", "_path", "_paths"))
                and key not in {"source_ref", "source_paths", "evidence_catalog"}}
    if isinstance(value, list):
        return [clean(child) for child in value]
    return value


def patch_comparison(module):
    original_resolve = module._resolved_file
    def resolve(path, label):
        resolved = report_path(path)
        return resolved if resolved else original_resolve(path, label)
    module._resolved_file = resolve
    original_context = module.build_comparison_context
    def context(**kwargs):
        target, peer = kwargs["target_financial"], kwargs["peer_financial"]
        if target.get("protocol") != PROTOCOL:
            return original_context(**kwargs)
        if peer.get("protocol") != PROTOCOL:
            raise ValueError("Both comparison companies require integrated reports")
        empty = dict(kwargs)
        for role in ("target", "peer"):
            for domain in ("financial", "news", "yfinance"):
                empty[f"{role}_{domain}"] = {}
        result = original_context(**empty)
        result["basis_cards"] = {key: card for key, card in result["basis_cards"].items() if key.startswith("pair.")}
        for role, report in (("target", target), ("peer", peer)):
            key = f"{role}.integrated.analysis"
            result["basis_cards"][key] = module._basis_card(
                card_key=key, label=f"{report['company_name']} 통합 기업 분석",
                company_scope=role, domain="integrated", observation=clean(report["report"]))
        module._require_context_contract(result)
        return result
    module.build_comparison_context = context


def patch_strategy(module):
    patch_fact_loader(module, "load_required_json")
    original = module.build_strategy_input_bundle
    def bundle(**kwargs):
        report = read_report(kwargs["target_financial_path"])
        result = original(**kwargs)
        if report is None:
            return result
        result["integrated_report"] = copy.deepcopy(report)
        result["target_reports"]["financial"]["_integrated_report"] = copy.deepcopy(report)
        result["decision_constraints"] = copy.deepcopy(report["report"]["limitations"])
        path = str(report_path(kwargs["target_financial_path"]))
        for domain in ("financial", "news", "yfinance"):
            result["input_metadata"][f"target_{domain}_path"] = path
        return result
    module.build_strategy_input_bundle = bundle


def patch_cards(module):
    original = module._financial_cards
    def cards(report):
        integrated = report.get("_integrated_report")
        if not integrated:
            return original(report)
        # Keep the same deterministic financial observations as Full.
        result = original(report)
        analysis = integrated["report"]
        findings = [(group, index, finding) for group in ("findings", "risks")
                    for index, finding in enumerate(analysis[group])]
        for index, (group, group_index, finding) in enumerate(findings, 1):
            domains = finding["domains"]
            domain = next(value for value in ("financial", "news", "market") if value in domains)
            card = module._card(f"integrated.finding_{index}", domain=domain,
                card_type="integrated_finding", label=finding["title"],
                allowed_sections=module.STRATEGY_SECTIONS,
                evidence_family="integrated_company_analysis", observation_basis="reference",
                observation={"observation": finding["observation"], "interpretation": finding["interpretation"],
                    "investment_implication": finding["investment_implication"], "source_domains": domains,
                    "cited_sources": [clean(integrated["evidence_catalog"][key]) for key in finding["evidence_ids"]]},
                reader_limitations=analysis["limitations"])
            card["evidence_origin"] = "model_interpreted"
            result.append((card, finding["evidence_ids"], [f"integrated_report.report.{group}[{group_index}]"]))
        return result
    module._financial_cards = cards


def patch_strategy_context(module):
    original = module.build_base_strategy_context
    def context(packet, *, input_bundle):
        result = original(packet, input_bundle=input_bundle)
        integrated = input_bundle.get("integrated_report")
        if integrated:
            result["domain_handoffs"] = {"integrated": clean(integrated["report"])}
        return result
    module.build_base_strategy_context = context


def patch_module(module):
    name = module.__name__
    if name.endswith("Competitor_Agent.peer_comparison"):
        patch_peer_dataset(module)
    elif name.endswith("Competitor_Agent.comparison_agent"):
        patch_comparison(module)
    elif name.endswith("Strategy_Agent.agent"):
        patch_strategy(module)
    elif name.endswith("Strategy_Agent.packet"):
        patch_cards(module)
    elif name.endswith("Strategy_Agent.context"):
        patch_strategy_context(module)
    elif name.endswith("Visualization_Agent.data_loader"):
        patch_fact_loader(module, "load_json_file")
