"""Archived v7 separate-domain output contract for historical regression checks.

The active one-team pipeline uses integrated_report and emits one company report.
"""
from __future__ import annotations
import copy
import json
from typing import Any
from .unified import (
    RunConfig, RunPaths, write_json, _dict,
    validate_financial_analysis, _merge_analysis_anchor_evidence_ids,
    _validate_news_analysis_output, validate_market_analysis, apply_financial_analysis,
)
from shared.domain_llm import domain_request
UNIFIED_PROTOCOL = "unified_domain_team_v7_common_contracts"

def build_unified_request(context: dict[str, Any], *, model: str) -> dict[str, Any]:
    requests = context["boundary_requests"]
    if any(request["model"] != model for request in requests.values()):
        raise ValueError("Full and unified model settings differ")
    instructions = ["세 영역을 한 호출에서 분석하고 financial, news, market에 각 영역의 결과를 작성합니다. 각 영역의 지침과 입력은 아래와 같습니다."]
    inputs = {}
    for domain in ("financial", "news", "market"):
        messages = requests[domain]["input"]
        instructions.append(f"<{domain}>\n" + messages[0]["content"] + f"\n</{domain}>")
        inputs[domain] = json.loads(messages[1]["content"])
    return domain_request({
        "model": model,
        "input": [{"role": "system", "content": "\n\n".join(instructions)},
                  {"role": "user", "content": json.dumps(inputs, ensure_ascii=False, separators=(",", ":"))}],
        "text": {"format": {"type": "json_schema", "name": UNIFIED_PROTOCOL, "strict": True,
                            "schema": unified_response_format(context)["json_schema"]["schema"]}},
    }, domain="unified")


def unified_response_format(context: dict[str, Any]) -> dict[str, Any]:
    """Nest the exact Full schemas, hoisting definitions without changing meaning."""
    properties = {}
    definitions = {}
    def relocate(value, domain):
        if isinstance(value, list):
            return [relocate(item, domain) for item in value]
        if isinstance(value, dict):
            return {key: (f"#/$defs/{domain}_" + item[len("#/$defs/"):] if key == "$ref" and item.startswith("#/$defs/")
                          else relocate(item, domain)) for key, item in value.items()}
        return value
    for domain in ("financial", "news", "market"):
        schema = copy.deepcopy(context["boundary_requests"][domain]["text"]["format"]["schema"])
        local_definitions = schema.pop("$defs", {})
        properties[domain] = relocate(schema, domain)
        definitions.update({f"{domain}_{key}": relocate(value, domain) for key, value in local_definitions.items()})
    schema = {"type": "object", "properties": properties, "required": list(properties), "additionalProperties": False}
    if definitions:
        schema["$defs"] = definitions
    return {"type": "json_schema", "json_schema": {"name": UNIFIED_PROTOCOL, "strict": True, "schema": schema}}


def validate_unified_output(output: dict[str, Any], context: dict[str, Any]) -> None:
    from jsonschema import Draft202012Validator, ValidationError
    try:
        Draft202012Validator(unified_response_format(context)["json_schema"]["schema"]).validate(output)
    except ValidationError as exc:
        raise ValueError(f"Invalid unified output: {exc.message}") from exc
    _normalized_domain_outputs(output, context)


def _normalized_domain_outputs(output, context):
    financial_packet = context["financial"]["preprocessed_input"]
    financial = validate_financial_analysis(
        output["financial"], primary_evidence_ids=sorted(financial_packet["primary_financial_evidence"]),
        secondary_context=financial_packet.get("secondary_context") or {},
    )
    news_packet = context["news"]["preprocessed_input"]
    news_context = news_packet.get("secondary_context") or {}
    evidence_map = copy.deepcopy(context["news"]["evidence"])
    for block in news_context.values():
        evidence_map.update(_dict(block).get("evidence_catalog") or {})
    news = copy.deepcopy(output["news"])
    _merge_analysis_anchor_evidence_ids(news)
    _validate_news_analysis_output(news, {"evidence_map": evidence_map, "secondary_context": news_context})
    market_packet = context["market"]["preprocessed_input"]
    market = validate_market_analysis(output["market"], {
        "primary_evidence_catalog": market_packet["primary_market_evidence"],
        "secondary_context": market_packet.get("secondary_context") or {},
    })
    return financial, news, market






def write_unified_adapters(
    *, output: dict[str, Any], prepared: dict[str, Any], destination_paths: RunPaths,
    run_config: RunConfig, model: str, role: str,
) -> dict[str, str]:
    facts = prepared["adapter_facts"]
    context = prepared["semantic_input"]
    financial_analysis, news, market = _normalized_domain_outputs(output, context)
    financial = apply_financial_analysis(facts["financial_factual_report"], financial_analysis)
    news["evidence_map_path"] = str(destination_paths.news_evidence_map)
    news_context = context["news"]["preprocessed_input"].get("secondary_context") or {}
    news_map = copy.deepcopy(facts["news_evidence_map"])
    for block in news_context.values():
        news_map.update(_dict(block).get("evidence_catalog") or {})
    write_json(destination_paths.news_evidence_map, news_map)
    news_handoff = {"model": model, "output": news, "usage": {}, "raw_content": None}
    market["selected_date"] = run_config.selected_date_iso
    market["selected_date_policy"] = "before_market_open"
    market["valuation_snapshot"] = facts["market_facts"]["valuation_snapshot"]
    market["primary_evidence_catalog"] = facts["market_facts"]["primary_evidence_catalog"]
    market["secondary_context_catalog"] = {}
    for block in context["market"]["preprocessed_input"].get("secondary_context", {}).values():
        market["secondary_context_catalog"].update(_dict(block).get("evidence_catalog") or {})
    write_json(destination_paths.financial_final_report, financial)
    write_json(destination_paths.news_handoff, news_handoff)
    write_json(destination_paths.news_final_report, news_handoff)
    write_json(destination_paths.yfinance_analyst_report, market)
    write_json(destination_paths.yfinance_final_report, market)
    from Agent_Team.YFinance_Agent.reporting import render_agent_markdown_report
    destination_paths.yfinance_analyst_report_md.parent.mkdir(parents=True, exist_ok=True)
    destination_paths.yfinance_analyst_report_md.write_text(render_agent_markdown_report(market), encoding="utf-8")
    return {"financial": str(destination_paths.financial_final_report), "news": str(destination_paths.news_final_report),
            "news_evidence_map": str(destination_paths.news_evidence_map), "market": str(destination_paths.yfinance_final_report)}
