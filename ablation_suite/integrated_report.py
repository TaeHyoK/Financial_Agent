"""One company report spanning financial, news and market evidence."""
from __future__ import annotations

import copy
import json
from typing import Any

from shared.domain_llm import domain_request
from .utils import write_json
from runtime_compat.model_policy import apply_request_policy

PROTOCOL = "unified_domain_team_v8_single_report"


def evidence_catalog(context: dict[str, Any]) -> dict[str, Any]:
    catalog = {}
    for domain in ("financial", "news", "market"):
        for key, row in context[domain]["evidence"].items():
            if key in catalog:
                raise ValueError(f"Ambiguous integrated evidence ID: {key}")
            # Period summaries provide context; claims cite original articles.
            if domain == "news" and not key.startswith("NEWS_RAW_"):
                continue
            catalog[key] = {**copy.deepcopy(row), "domain": domain}
    return catalog


def response_format(context: dict[str, Any]) -> dict[str, Any]:
    text = {"type": "string", "minLength": 1}
    refs = {"type": "array", "items": {"type": "string", "enum": sorted(evidence_catalog(context))}, "minItems": 1}

    def obj(properties):
        return {"type": "object", "properties": properties, "required": list(properties), "additionalProperties": False}

    finding = obj({
        "title": text,
        "domains": {"type": "array", "items": {"type": "string", "enum": ["financial", "news", "market"]}, "minItems": 1},
        "observation": text, "interpretation": text, "investment_implication": text,
        "evidence_ids": refs,
    })
    view = obj({"statement": text, "evidence_ids": refs})
    schema = obj({
        "title": text,
        "executive_summary": view,
        "integrated_analysis": view,
        "findings": {"type": "array", "items": finding, "minItems": 1},
        "risks": {"type": "array", "items": finding},
        "outlook": obj({key: view for key in ("short_term", "medium_term", "long_term")}),
        "limitations": {"type": "array", "items": text},
    })
    return {"type": "json_schema", "name": PROTOCOL, "strict": True, "schema": schema}


def build_request(context: dict[str, Any], *, model: str) -> dict[str, Any]:
    inputs, guidelines = {}, []
    for domain, request in context["boundary_requests"].items():
        if request["model"] != model:
            raise ValueError("Full and unified model settings differ")
        guidelines.append(f"<{domain}>\n{request['input'][0]['content']}\n</{domain}>")
        inputs[domain] = json.loads(request["input"][1]["content"])
    instructions = "\n\n".join(guidelines) + "\n\n" + (
        "당신은 한 기업을 분석하는 통합 분석가입니다. 위 재무·뉴스·시장 지침의 분석 요구와 시점·근거 정책을 적용하여 "
        "서로 다른 자료가 기업의 실적, 사건, 주가와 전망에 어떻게 연결되는지 종합한 하나의 기업 분석 보고서를 작성합니다. "
        "출력 형식은 이번 요청의 단일 보고서 JSON 스키마를 따릅니다. 위 개별 지침의 도메인별 출력 형식 지시는 이 스키마로 대체합니다. "
        "financial/news/market별 독립 보고서나 독립 결론을 작성하지 않습니다. integrated_analysis에는 전체 논지를 연결한 본문을, "
        "findings에는 그 논지를 뒷받침하는 관측·해석·투자 함의를 작성합니다. risks와 기간별 outlook도 동일한 기업 관점에서 작성합니다. "
        "근거가 연결되는 경우 domains에 관련 영역을 함께 표시하되 인과관계를 꾸며내지 않습니다. "
        "각 주장은 제공된 원천 근거 ID를 인용합니다. 뉴스 주장은 NEWS_RAW 기사 ID를 사용하고 월별 요약을 원문 근거로 인용하지 않습니다. "
        "본문에는 내부 근거 ID를 직접 적지 말고 evidence_ids 배열에 기록합니다. "
        "출처 날짜·실적 기간·연결/별도 기준·뉴스의 대상기업과 산업 일반 정보 구분을 보존합니다. "
        "근거가 부족하거나 서로 충돌하는 경우 해당 해석과 limitations에 한계를 명시합니다."
    )
    return apply_request_policy(domain_request({"model": model,
        "input": [{"role": "system", "content": instructions},
                  {"role": "user", "content": json.dumps(inputs, ensure_ascii=False, separators=(",", ":"))}],
        "text": {"format": response_format(context)}}, domain="unified"))


def validate_output(output: dict[str, Any], context: dict[str, Any]) -> None:
    from jsonschema import Draft202012Validator
    from shared.evidence_cards import assert_no_internal_references_in_reader_text
    Draft202012Validator(response_format(context)["schema"]).validate(output)
    catalog = evidence_catalog(context)
    for finding in [*output["findings"], *output["risks"]]:
        cited = {catalog[key]["domain"] for key in finding["evidence_ids"]}
        if set(finding["domains"]) != cited:
            raise ValueError("Finding domains must match its cited evidence domains")
    def check_text(value):
        if isinstance(value, dict):
            for key, child in value.items():
                if key not in {"evidence_ids", "domains"}:
                    check_text(child)
        elif isinstance(value, list):
            for child in value:
                check_text(child)
        elif isinstance(value, str):
            assert_no_internal_references_in_reader_text(value, card_keys=catalog, location="integrated_report")
    check_text(output)


def write_report(*, output, prepared, destination_paths, run_config, model, role):
    validate_output(output, prepared["semantic_input"])
    facts = prepared["adapter_facts"]
    report = {
        "protocol": PROTOCOL, "company_name": run_config.company_name,
        "ticker": run_config.ticker, "selected_date": run_config.selected_date_iso,
        "model": model, "role": role, "report": copy.deepcopy(output),
        "evidence_catalog": evidence_catalog(prepared["semantic_input"]),
        # Deterministic facts support peer metrics and charts, not extra analyses.
        "supporting_facts": {
            "financial": copy.deepcopy(facts["financial_factual_report"]),
            "market": {"valuation_snapshot": copy.deepcopy(facts["market_facts"]["valuation_snapshot"]),
                       "primary_evidence_catalog": copy.deepcopy(facts["market_facts"]["primary_evidence_catalog"])},
        },
    }
    directory = destination_paths.run_dir / "unified_domain_team"
    path = write_json(directory / "unified_report.json", report)
    markdown = directory / "unified_report.md"
    markdown.write_text(render_markdown(output), encoding="utf-8")
    return {"unified_report": str(path), "unified_report_md": str(markdown)}


def render_markdown(report):
    paragraphs = [f"# {report['title']}", report["executive_summary"]["statement"], report["integrated_analysis"]["statement"]]
    for finding in report["findings"]:
        paragraphs.extend([f"## {finding['title']}", finding["observation"], finding["interpretation"], finding["investment_implication"]])
    if report["risks"]:
        paragraphs.append("## 위험 요인")
        for risk in report["risks"]:
            paragraphs.extend([f"### {risk['title']}", risk["observation"], risk["interpretation"], risk["investment_implication"]])
    paragraphs.append("## 기간별 전망")
    for key, label in (("short_term", "단기"), ("medium_term", "중기"), ("long_term", "장기")):
        paragraphs.extend([f"### {label}", report["outlook"][key]["statement"]])
    if report["limitations"]:
        paragraphs.extend(["## 자료와 해석의 한계", *report["limitations"]])
    return "\n\n".join(paragraphs) + "\n"
