"""Common subdata instructions and issue-level output contracts."""
from __future__ import annotations

CONTEXT_POLICY_VERSION = "context_interpretation_v6_monthly_summary_subdata"
CONTEXT_USAGE = "context_informed_interpretation"

COMMON_GUIDANCE = """
보조자료 활용 원칙:
- secondary_context는 타 도메인의 원자료를 정리한 참고자료이지 다른 분석 에이전트의 결론이 아니다. 재무·시장 자료는 수치표이고 뉴스는 선정 기사의 월별 요약이다. 뉴스 요약은 원문 자체나 공시 확정치가 아니며, 요약에 명시된 기업·사건·실적 대상 기간을 구분한다.
- 대상기업·자료 기간·단위·작성 방식을 확인한 뒤 주 자료의 관측과 연결한다. 보조자료는 관측의 중요도·지속성·위험·적용 범위에 대한 해석과 종합 방향을 보강하거나 수정할 수 있다. 주 자료의 수치·발생 사실·출처·확정 수준 자체는 변경하지 않는다.
- 사실과 해석을 구분한다. 관련 자료로 뒷받침되는 추론은 추론으로 설명하되, 확인되지 않은 인과관계나 입력에 없는 금액·일정·전망치를 사실로 만들지 않는다. 기준일 이후 확인·추가 수집·자동 모니터링을 수행할 것이라고 전제하지 않는다.
- 쟁점별 보조자료 평가의 statement에는 연결한 관측과 그 의미를, judgment_impact에는 자기 도메인의 해석을 무엇 때문에 어떻게 보강·수정했는지 또는 바꾸지 않았는지를 적는다. primary_evidence_ids와 secondary_evidence_ids로 실제 근거를 연결한다.
- 같은 근거를 여러 쟁점이 사용해도 보존한다. 항목 수를 채우거나 모든 보조자료를 인용하지 않는다. 관련 쟁점이 없으면 해당 평가 배열은 비워 둔다. 보조자료가 없거나 관련성이 낮다는 이유만으로 부정적·중립 판단을 기본값으로 삼지 않는다.
- 종합 요약에는 분석상 의미가 있는 쟁점의 해석을 반영하고 context_ids로 해당 쟁점을 참조한다. context_ids는 실제 생성한 context_id만 사용한다. 의미가 없으면 빈 배열로 둔다. 동일 사건이 여러 도메인에 나타났다는 이유로 독립된 확인 근거가 늘었다고 세지 않는다.
- context_ids에 영향이 있다고 연결한 쟁점은 실제 종합 요약 문장에서도 그 경제적 의미가 드러나게 한다. 별도 평가에서만 '반영했다'고 쓰고 종합 요약은 주 자료만 반복하지 않는다. 어떤 도메인에서 변화가 일어났는지 설명하되 분석 절차를 나열하지 않는다.
- 기사에 실제 실적 수치가 명시되어 있으면 '보도된 실적'으로 활용하되 공시 수치와 기간·확정 수준을 구분한다. 실적 발표와 계약·승인·계획의 향후 기대효과를 혼동하지 않는다. 과거 재무제표에 후행 사건이 미반영되었다는 것 자체는 악재가 아니다.
- 현재 위험은 기준일에 남아 있는 쟁점을 중심으로 설명한다. 입력의 후속 발표로 해소된 과거 허가 대기·불확실성은 과거 경과로 구분하고 현재 위험으로 재사용하지 않는다.
- 자료의 제약이 해당 해석의 방향·강도·적용 범위를 실질적으로 바꾸는 경우에만 limitation 또는 data_limitation을 작성한다. 어떤 판단이 무엇 때문에 제한되는지 설명하고, 해당 제약이 없으면 빈 문자열로 둔다. 숫자로 된 재무 기여액이 없거나 뉴스가 짧은 발췌문이라는 사실만으로 한계 문장을 채우지 않는다. 사건의 사업상 의미와 확인되지 않은 실적 효과는 구분하며, 실제로 중요한 불확실성을 숨기지 않는다.
""".strip()

DOMAIN_GUIDANCE = {
    "news": """
뉴스 분석에서 재무 보조자료는 매출·수익성·영업현금흐름·자본·부채의 기존 추세와 사건의 의미를 연결하는 데 사용한다. 제공되지 않은 제품별 매출 비중이나 계약의 이익 기여액은 추정하지 않는다.
시장 보조자료는 제공된 기간별 성과·변동성·낙폭과 뉴스 흐름의 부합 또는 괴리를 살핀다. 월별 시장 관측이나 사건 전후 가격이 없으면 개별 사건의 주가 반응을 복원하지 않는다.
news_only에는 뉴스로 확인되는 사실·전망·불확실성을 보존한다. 타 도메인 자료와 결합한 해석은 쟁점별 평가와 overall_assessment에 작성한다. overall_assessment.summary는 뉴스의 경제적 의미를 종합하되 투자 등급·목표가격은 제시하지 않는다.
""".strip(),
    "financial": """
재무 분석에서는 월별 뉴스의 실적 발표·사업 변화·계약·비용 요인이 공시에서 관찰한 성장·수익성·현금흐름 추세의 지속성 해석을 보강하거나 약화하는지 살핀다. 최신 뉴스 보도 실적은 공시 확인 실적과 별도로 설명하며 공시 표를 갱신하거나 다른 기간의 값과 직접 합산하지 않는다.
시장 보조자료는 재무 개선·악화와 가격 흐름의 부합 또는 괴리를 해석하는 데 사용한다. 가격만으로 회계 실적의 진위나 시장 참여자의 의도를 추정하지 않는다. 시장 대비 주가 부진만으로 영업 실적의 지속성이 낮다고 결론내리지 않는다. 사업 지속성 판단에는 실제 사업·재무 근거가 필요하다.
재무 차원별 reasoning과 main_view에는 관련 쟁점을 반영할 수 있다. main_view.context_ids에 종합 판단에 사용한 쟁점을 연결한다. 재무 수치의 관찰 방향과 그 지속성·위험에 대한 해석을 구분한다.
""".strip(),
    "market": """
시장 분석에서는 월별 뉴스 요약과 같은 기간의 월별 가격·거래량·시장 대비 성과를 대조하고 일치와 괴리를 설명한다. 요약의 월 구간과 사건·실적의 대상 기간을 구분한다. 요약에 정확한 사건일이 없으면 일별 주가와 임의로 연결하지 않으며, 해당 월 전체의 수익률을 특정 기사 이후 수익률로 쓰지 않는다.
재무 보조자료는 이익·현금흐름 추세와 가격 흐름을 함께 해석하는 데 사용한다. 주가 변화가 특정 사건 때문이라거나 이미 선반영됐다고 단정하지 않는다. 비교 기준이 없는 평가 배수만으로 고평가·저평가를 판단하지 않는다.
main_view와 기간별 reasoning에는 의미 있는 쟁점을 반영하고 main_view.context_ids에 종합 판단과 연결된 쟁점을 적는다. 시장자료의 관측값은 그대로 유지한다.
""".strip(),
}


def context_guidance(domain: str) -> str:
    return "\n\n" + COMMON_GUIDANCE + "\n\n" + DOMAIN_GUIDANCE[domain]


def context_ref_schema():
    return {"type": "array", "items": {"type": "string"}}


def context_issue_schema(*, domain, primary_ids, secondary_ids, effects):
    properties = {
        "context_id": {"type": "string", "description": "쟁점별 고유 식별자"},
        "source_domain": {"type": "string", "enum": [domain]},
        "effect": {"type": "string", "enum": sorted(effects)},
        "statement": {"type": "string"},
        "judgment_impact": {"type": "string"},
        "primary_evidence_ids": {"type": "array", "items": {"type": "string", "enum": primary_ids}, "minItems": 1},
        "secondary_evidence_ids": {"type": "array", "items": {"type": "string", "enum": secondary_ids}, "minItems": 1},
        "usage": {"type": "string", "enum": [CONTEXT_USAGE]},
        "limitation": {"type": "string", "description": "해당 해석의 실질적 한계가 없으면 빈 문자열"},
    }
    return {"type": "array", "items": {"type": "object", "properties": properties,
            "required": list(properties), "additionalProperties": False}}


def flatten_context_issues(by_domain, domains):
    """Flatten issue arrays; old singleton artifacts remain readable, not relabelled."""
    if not isinstance(by_domain, dict) or set(by_domain) != set(domains):
        raise ValueError("Secondary context domain containers do not match available inputs")
    result = []
    for domain in sorted(domains):
        value = by_domain[domain]
        items = [value] if isinstance(value, dict) else value
        if not isinstance(items, list):
            raise ValueError("Secondary context must contain issue arrays")
        for item in items:
            if not isinstance(item, dict) or item.get("source_domain") != domain:
                raise ValueError("Secondary context issue is in the wrong domain")
            result.append(item)
    return result


def validate_context_refs(view, assessments):
    """Check references only, not whether prose changes the model's conclusion."""
    refs = view.get("context_ids", [])
    known = {item["context_id"] for item in assessments}
    if not isinstance(refs, list) or any(not isinstance(ref, str) or ref not in known for ref in refs):
        raise ValueError("Unknown context_id in domain conclusion")
