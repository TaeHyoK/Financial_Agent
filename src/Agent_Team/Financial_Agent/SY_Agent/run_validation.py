#!/usr/bin/env python3
import argparse
import json
import re
from pathlib import Path
from typing import Any, Dict, List


REQUIRED_BLOCKS = [
    "agent_name",
    "sy_handoff",
]
try:
    from .. import DEFAULT_ENV_FILE
except ImportError:  # pragma: no cover - supports direct script execution
    DEFAULT_ENV_FILE = Path(__file__).resolve().parents[4] / ".env"

DEFAULT_RULES = {
    "claim_count_limit": 24,
    "primary_financial_sources": ["DART"],
    "context_only_sources": ["News", "Y-Finance", "YFinance"],
    "investment_decision_terms": [
        "매수",
        "매도",
        "보유",
        "목표주가",
        "목표가",
        "투자의견",
        "비중확대",
        "비중축소",
        "상승여력",
        "하락여력",
        "buy",
        "sell",
        "hold",
        "target price",
        "price target",
        "upside",
        "downside",
        "outperform",
        "underperform",
        "overweight",
        "underweight",
        "accumulate",
        "reduce",
    ],
}

SECTION_CLAIM_SPECS = [
    ("revenue_growth", "revenue", "FSV_REVENUE_GROWTH", "growth", "매출 성장성"),
    ("profitability", "margin", "FSV_PROFITABILITY", "profitability", "수익성"),
    ("cost_efficiency", "expense_efficiency", "FSV_COST_EFFICIENCY", "cost_efficiency", "비용 효율성"),
    ("eps", "eps", "FSV_EPS", "eps", "EPS"),
    ("risk_context", "risk_and_context", "FSV_RISK_CONTEXT", "risk", "리스크 context"),
    ("cash_flow", "cash_flow", "FSV_CASH_FLOW", "cash_flow", "현금흐름"),
    ("balance_sheet", "balance_sheet", "FSV_BALANCE_SHEET", "balance_sheet", "재무상태표"),
    ("capital_structure", "capital_structure", "FSV_CAPITAL_STRUCTURE", "capital_structure", "자본구조"),
    ("debt", "debt", "FSV_DEBT", "debt", "부채"),
    ("liquidity", "liquidity", "FSV_LIQUIDITY", "liquidity", "유동성"),
]

CROSS_RECONCILIATION_SPECS = [
    ("main_analysis", "CDR_MAIN_ANALYSIS", "DART 메인 분석"),
    ("news_plus_dart", "CDR_NEWS_PLUS_DART", "News + DART 교차분석"),
    ("market_plus_dart", "CDR_MARKET_PLUS_DART", "Market + DART 교차분석"),
    ("market_plus_news_plus_dart", "CDR_MARKET_NEWS_DART", "Market + News + DART 교차분석"),
]

REQUIRED_METRICS_BY_DIMENSION = {
    "growth": {"revenue", "revenue_growth"},
    "profitability": {"contribution_margin"},
    "cost_efficiency": {"sga_margin"},
    "eps": {"eps"},
    "cash_flow": {"operating_cash_flow", "investing_cash_flow", "financing_cash_flow", "net_cash_change"},
    "balance_sheet": {"total_assets", "current_assets", "non_current_assets", "cash_and_cash_equivalents"},
    "capital_structure": {"total_equity", "total_liabilities", "equity_ratio", "debt_to_equity"},
    "debt": {"total_liabilities", "current_liabilities", "non_current_liabilities", "liabilities_to_assets"},
    "liquidity": {"current_ratio", "cash_ratio", "current_assets", "current_liabilities"},
}
MIN_REQUIRED_METRIC_MATCHES = {
    "cash_flow": 2,
    "balance_sheet": 2,
    "capital_structure": 2,
    "debt": 2,
    "liquidity": 2,
}

YTD_CAUTION_PHRASES = [
    "clean full-year YoY가 아니다",
    "같은 조건의 YoY로 표현하지 않는다",
    "연간 개선을 단정하기는 어렵다",
    "연간 확정 비교는 아니다",
    "연간 확정",
    "기간 기준이 다르다",
    "Q3 YTD",
    "3분기 누적",
    "누적 기준",
]

SOURCE_AUDIT_MAIN_METRICS = {
    "revenue",
    "contribution_margin",
    "sga_margin",
    "eps",
}
SOURCE_AUDIT_PREVIOUS_METRICS = {
    "previous_revenue": "revenue",
    "previous_contribution_margin": "contribution_margin",
    "previous_sga_margin": "sga_margin",
    "previous_eps": "eps",
}
SOURCE_AUDIT_MASTER_ITEMS = {
    "current_assets": ("4-1", ("current_assets",), ("유동자산",)),
    "cash_and_cash_equivalents": ("4-1", ("cash_and_cash_equivalents",), ("현금및현금성자산",)),
    "non_current_assets": ("4-1", ("non_current_assets",), ("비유동자산",)),
    "total_assets": ("4-1", ("total_assets",), ("자산총계",)),
    "current_liabilities": ("4-1", ("current_liabilities",), ("유동부채",)),
    "non_current_liabilities": ("4-1", ("non_current_liabilities",), ("비유동부채",)),
    "total_liabilities": ("4-1", ("total_liabilities",), ("부채총계",)),
    "total_equity": ("4-1", ("total_equity",), ("자본총계",)),
    "operating_cash_flow": ("4-4", ("cash_flows_from_operating_activities",), ("영업활동으로 인한 현금흐름",)),
    "investing_cash_flow": ("4-4", (), ("투자활동으로 인한 현금흐름",)),
    "financing_cash_flow": ("4-4", (), ("재무활동으로 인한 현금흐름",)),
    "net_cash_change": ("4-4", (), ("현금및현금성자산의 순증감", "현금및현금성자산의 증가", "현금및현금성자산의 증가(감소)")),
}


def load_shared_rules() -> Dict[str, Any]:
    path = Path(__file__).resolve().parent.parent / "shared" / "validation_rules.json"
    if not path.exists():
        return DEFAULT_RULES
    rules = json.loads(path.read_text())
    merged = dict(DEFAULT_RULES)
    merged.update(rules)
    return merged


SHARED_RULES = load_shared_rules()
CLAIM_COUNT_LIMIT = int(SHARED_RULES["claim_count_limit"])
INVESTMENT_TERMS = SHARED_RULES["investment_decision_terms"]
PRIMARY_FINANCIAL_SOURCES = {source.lower() for source in SHARED_RULES["primary_financial_sources"]}
CONTEXT_ONLY_SOURCES = {source.lower() for source in SHARED_RULES["context_only_sources"]}


def flatten_text(value: Any) -> str:
    if isinstance(value, str):
        return value
    if isinstance(value, dict):
        return " ".join(flatten_text(v) for v in value.values())
    if isinstance(value, list):
        return " ".join(flatten_text(v) for v in value)
    return ""


def contains_term(text: str, term: str) -> bool:
    lowered = text.lower()
    lowered_term = term.lower()
    if re.fullmatch(r"[a-z][a-z ]+[a-z]", lowered_term):
        return bool(re.search(rf"(?<![a-z]){re.escape(lowered_term)}(?![a-z])", lowered))
    if lowered_term == "보유":
        return bool(
            re.search(r"(투자의견|의견|recommendation|rating|매수|매도|중립).{0,12}보유", lowered)
            or re.search(r"보유.{0,12}(의견|recommendation|rating)", lowered)
        )
    return lowered_term in lowered


def has_investment_decision(data: Dict[str, Any]) -> bool:
    text = flatten_text(data)
    text = text.replace("과매수", "").replace("과매도", "")
    return any(contains_term(text, term) for term in INVESTMENT_TERMS)


def evidence_by_claim(evidence_items: List[Dict[str, Any]]) -> Dict[str, List[Dict[str, Any]]]:
    mapping: Dict[str, List[Dict[str, Any]]] = {}
    for item in evidence_items:
        mapping.setdefault(item.get("claim_id", ""), []).append(item)
    return mapping


def get_claims(data: Dict[str, Any]) -> List[Dict[str, Any]]:
    base_claims = data.get("financial_claims") or data.get("sy_handoff", {}).get("financial_claims", [])
    claims = [dict(item, claim_origin=item.get("claim_origin", "sy_handoff")) for item in base_claims]
    seen = {str(item.get("claim_id")) for item in claims}
    for item in build_section_claims(data):
        claim_id = str(item.get("claim_id"))
        if claim_id and claim_id not in seen:
            claims.append(item)
            seen.add(claim_id)
    return claims


def get_key_evidence(data: Dict[str, Any]) -> List[Dict[str, Any]]:
    base_evidence = data.get("key_evidence") or data.get("sy_handoff", {}).get("key_evidence", [])
    evidence_items = [dict(item, evidence_origin=item.get("evidence_origin", "sy_handoff")) for item in base_evidence]
    seen = {str(item.get("evidence_id")) for item in evidence_items}
    for item in build_section_evidence(data):
        evidence_id = str(item.get("evidence_id"))
        if evidence_id and evidence_id not in seen:
            evidence_items.append(item)
            seen.add(evidence_id)
    return evidence_items


def build_section_claims(data: Dict[str, Any]) -> List[Dict[str, Any]]:
    claims: List[Dict[str, Any]] = []
    statement_view = data.get("financial_statement_view") or {}
    detailed = data.get("detailed_analysis") or {}

    for statement_key, detail_key, claim_id, dimension, label in SECTION_CLAIM_SPECS:
        block = statement_view.get(statement_key) or {}
        if not isinstance(block, dict) or not block.get("stance"):
            continue
        detailed_block = detailed.get(detail_key) or {}
        supporting_features = detailed_block.get("supporting_features", {}) if isinstance(detailed_block, dict) else {}
        caution = str(block.get("caution") or detailed_block.get("caution") or "").strip()
        period_basis = infer_period_basis(supporting_features, block)
        if not caution and period_basis in {"YTD", "QUARTER"}:
            caution = "YTD 또는 분기 기준이므로 연간 확정 개선으로 단정하지 않는다."
        claims.append(
            {
                "claim_id": claim_id,
                "claim_ko": f"{label}: {block.get('stance')}",
                "financial_dimension": dimension,
                "status": "active",
                "claim_origin": "financial_statement_view",
                "section_path": f"financial_statement_view.{statement_key}",
                "dart_anchor_summary_ko": build_dart_anchor_summary(label, block, supporting_features),
                "context_summary_ko": str(block.get("reasoning") or detailed_block.get("interpretation") or "").strip(),
                "caution_ko": caution,
                "action_for_sy": "use_normally" if dimension not in {"risk", "eps"} else "use_with_caution",
                "supporting_features": supporting_features,
                "key_features": block.get("key_features", []),
            }
        )

    cross = data.get("cross_data_reconciliation") or {}
    for section_key, claim_id, label in CROSS_RECONCILIATION_SPECS:
        block = cross.get(section_key) or {}
        if not isinstance(block, dict) or not block.get("summary"):
            continue
        claims.append(
            {
                "claim_id": claim_id,
                "claim_ko": f"{label}: {block.get('summary')}",
                "financial_dimension": "reconciliation",
                "status": "active",
                "claim_origin": "cross_data_reconciliation",
                "section_path": f"cross_data_reconciliation.{section_key}",
                "dart_anchor_summary_ko": "DART 기준 재무 claim을 중심으로 교차분석을 검증한다.",
                "context_summary_ko": str(block.get("summary") or "").strip(),
                "caution_ko": summarize_divergences(block),
                "action_for_sy": "use_with_caution",
            }
        )
    return claims


def build_section_evidence(data: Dict[str, Any]) -> List[Dict[str, Any]]:
    evidence_items: List[Dict[str, Any]] = []
    statement_view = data.get("financial_statement_view") or {}
    detailed = data.get("detailed_analysis") or {}

    for statement_key, detail_key, claim_id, dimension, label in SECTION_CLAIM_SPECS:
        block = statement_view.get(statement_key) or {}
        if not isinstance(block, dict) or not block.get("stance"):
            continue
        detailed_block = detailed.get(detail_key) or {}
        supporting_features = detailed_block.get("supporting_features", {}) if isinstance(detailed_block, dict) else {}
        evidence_items.append(
            {
                "evidence_id": f"E_{claim_id}",
                "claim_id": claim_id,
                "source": "DART" if dimension != "risk" else "DART/News/Y-Finance",
                "metric_or_event": label,
                "period": infer_period_label(supporting_features),
                "value": supporting_features or block.get("key_features", []),
                "period_basis": infer_period_basis(supporting_features, block),
                "interpretation_ko": str(block.get("reasoning") or detailed_block.get("interpretation") or "").strip(),
                "evidence_origin": "section_derived",
            }
        )

    cross = data.get("cross_data_reconciliation") or {}
    for section_key, claim_id, label in CROSS_RECONCILIATION_SPECS:
        block = cross.get(section_key) or {}
        if not isinstance(block, dict) or not block.get("summary"):
            continue
        evidence_items.append(
            {
                "evidence_id": f"E_{claim_id}",
                "claim_id": claim_id,
                "source": "DART",
                "metric_or_event": label,
                "period": str(data.get("as_of_date") or get_target_entity(data).get("as_of_date") or ""),
                "value": {
                    "summary": block.get("summary"),
                    "reaction_points": block.get("reaction_points", []),
                    "divergences": block.get("divergences", []),
                },
                "period_basis": "MIXED_CONTEXT",
                "interpretation_ko": "DART primary anchor와 News/Y-Finance context의 정합성을 확인하는 교차분석 근거다.",
                "evidence_origin": "section_derived",
            }
        )
    return evidence_items


def build_dart_anchor_summary(label: str, block: Dict[str, Any], supporting_features: Any) -> str:
    features = json.dumps(supporting_features, ensure_ascii=False, sort_keys=True) if supporting_features else ""
    return f"DART 기준 {label} 검증 항목이다. {block.get('reasoning', '')} {features}".strip()


def infer_period_label(supporting_features: Any) -> str:
    if isinstance(supporting_features, dict):
        for key in ("period", "period_basis"):
            if supporting_features.get(key):
                return str(supporting_features[key])
    return ""


def infer_period_basis(supporting_features: Any, block: Dict[str, Any] | None = None) -> str:
    values = []
    if isinstance(supporting_features, dict):
        values.extend(str(value) for value in supporting_features.values())
        if supporting_features.get("period_basis"):
            return str(supporting_features["period_basis"]).upper()
    if isinstance(block, dict):
        values.append(flatten_text(block))
    text = " ".join(values).upper()
    if "POINT_IN_TIME" in text:
        return "POINT_IN_TIME"
    if "YTD" in text or "누적" in text:
        return "YTD"
    if "Q1" in text or "Q2" in text or "Q3" in text or "Q4" in text or "분기" in text:
        return "QUARTER"
    if "CONTEXT" in text:
        return "context_only"
    return ""


def summarize_divergences(block: Dict[str, Any]) -> str:
    divergences = block.get("divergences") or []
    if not divergences:
        return ""
    points = [str(item.get("point") or item.get("cross_analysis") or "").strip() for item in divergences if isinstance(item, dict)]
    points = [point for point in points if point]
    return " / ".join(points[:3])


def get_target_entity(data: Dict[str, Any]) -> Dict[str, Any]:
    if data.get("target_entity"):
        return data["target_entity"]
    return {
        "company_name": data.get("target_company", ""),
        "ticker": data.get("ticker", ""),
        "corp_code": data.get("corp_code", ""),
        "as_of_date": data.get("as_of_date", ""),
    }


def normalize_source(source: str) -> str:
    return source.strip().lower()


def answer_for_claim(claim: Dict[str, Any], evidence_items: List[Dict[str, Any]]) -> str:
    parts = []
    if claim.get("section_path"):
        parts.append(f"검증 섹션: {claim['section_path']}")
    if claim.get("claim_ko"):
        parts.append(f"claim: {claim['claim_ko']}")
    if claim.get("dart_anchor_summary_ko"):
        parts.append(claim["dart_anchor_summary_ko"])
    for item in evidence_items:
        source = item.get("source", "")
        metric_or_event = item.get("metric_or_event", "")
        period = item.get("period", "")
        interpretation = item.get("interpretation_ko", "")
        parts.append(f"{source} {metric_or_event}({period}): {interpretation}".strip())
    if claim.get("context_summary_ko"):
        parts.append(claim["context_summary_ko"])
    if claim.get("caution_ko"):
        parts.append(f"주의: {claim['caution_ko']}")
    return " ".join(part for part in parts if part).strip()


def has_dart_anchor(claim: Dict[str, Any], evidence_items: List[Dict[str, Any]]) -> bool:
    if re.search(r"DART|다트", claim.get("dart_anchor_summary_ko", ""), re.IGNORECASE):
        return True
    return any(normalize_source(item.get("source", "")) in PRIMARY_FINANCIAL_SOURCES for item in evidence_items)


def uses_only_context_sources(evidence_items: List[Dict[str, Any]]) -> bool:
    sources = [normalize_source(item.get("source", "")) for item in evidence_items if item.get("source")]
    return bool(sources) and all(source in CONTEXT_ONLY_SOURCES for source in sources)


def overstates_ytd_as_full_year(claim: Dict[str, Any], evidence_items: List[Dict[str, Any]]) -> bool:
    has_ytd_basis = any(str(item.get("period_basis", "")).upper() in {"YTD", "QUARTER"} for item in evidence_items)
    if not has_ytd_basis:
        return False
    text = " ".join(
        [
            claim.get("claim_ko", ""),
            claim.get("dart_anchor_summary_ko", ""),
            claim.get("context_summary_ko", ""),
            claim.get("caution_ko", ""),
        ]
    )
    explicit_caution = any(
        phrase in text
        for phrase in [
            "clean full-year YoY가 아니다",
            "같은 조건의 YoY로 표현하지 않는다",
            "연간 개선을 단정하기는 어렵다",
            "기간 기준이 다르다",
            "연간 확정 비교는 아니다",
        ]
    )
    overstatement = any(
        phrase in text
        for phrase in [
            "연간 개선이 확정",
            "연간 실적 개선이 확정",
            "full-year YoY 개선 확정",
            "연간 기준으로 개선이 확인",
        ]
    )
    return overstatement and not explicit_caution


def missing_required_metrics(claim: Dict[str, Any], evidence_items: List[Dict[str, Any]]) -> List[str]:
    required = REQUIRED_METRICS_BY_DIMENSION.get(claim.get("financial_dimension", ""))
    if not required:
        return []
    observed = set()
    for item in evidence_items:
        observed.update(extract_metric_keys(item.get("value")))
        observed.update(metric_tokens(item.get("metric_or_event", "")))
    matched = required & observed
    minimum = MIN_REQUIRED_METRIC_MATCHES.get(claim.get("financial_dimension", ""), 1)
    if len(matched) >= minimum:
        return []
    return sorted(required - observed)


def metric_tokens(value: Any) -> set[str]:
    text = str(value or "").lower()
    tokens = {text}
    for separator in (" ", "-", "/", "(", ")", ","):
        text = text.replace(separator, "_")
    tokens.add(text.strip("_"))
    if "revenue" in text and "growth" in text:
        tokens.add("revenue_growth")
    if "contribution" in text and "margin" in text:
        tokens.add("contribution_margin")
    if ("sga" in text or "sg&a" in text) and "margin" in text:
        tokens.add("sga_margin")
    return {token for token in tokens if token}


def extract_metric_keys(value: Any) -> set[str]:
    if isinstance(value, dict):
        keys = set(str(key) for key in value.keys())
        for child in value.values():
            keys.update(extract_metric_keys(child))
        return keys
    if isinstance(value, list):
        keys: set[str] = set()
        for item in value:
            keys.update(extract_metric_keys(item))
        return keys
    return set()


def needs_ytd_caution(claim: Dict[str, Any], evidence_items: List[Dict[str, Any]]) -> bool:
    has_ytd_basis = any(str(item.get("period_basis", "")).upper() in {"YTD", "QUARTER"} for item in evidence_items)
    if not has_ytd_basis:
        return False
    if claim.get("financial_dimension") not in {"growth", "profitability", "cost_efficiency", "eps", "cash_flow"}:
        return False
    text = " ".join(
        [
            claim.get("claim_ko", ""),
            claim.get("dart_anchor_summary_ko", ""),
            claim.get("context_summary_ko", ""),
            claim.get("caution_ko", ""),
        ]
    )
    return not any(phrase in text for phrase in YTD_CAUTION_PHRASES)


def has_unit_or_ratio_mismatch_risk(claim: Dict[str, Any], evidence_items: List[Dict[str, Any]]) -> bool:
    text = " ".join(
        [
            claim.get("claim_ko", ""),
            claim.get("context_summary_ko", ""),
            claim.get("dart_anchor_summary_ko", ""),
            flatten_text(claim.get("key_features", [])),
            flatten_text(claim.get("supporting_features", {})),
        ]
    )
    if "%" in text:
        return False
    if "비율" not in text and "ratio" not in text.lower():
        return False
    numeric_values = []
    for item in evidence_items:
        numeric_values.extend(extract_numeric_values(item.get("value")))
    if not numeric_values:
        return False
    return any(abs(value) > 1.0 for value in numeric_values if isinstance(value, float)) and "억원" not in text


def extract_numeric_values(value: Any) -> List[float]:
    if isinstance(value, bool):
        return []
    if isinstance(value, (int, float)):
        return [float(value)]
    if isinstance(value, dict):
        values: List[float] = []
        for child in value.values():
            values.extend(extract_numeric_values(child))
        return values
    if isinstance(value, list):
        values = []
        for child in value:
            values.extend(extract_numeric_values(child))
        return values
    return []


def build_source_audit(
    data: Dict[str, Any],
    *,
    dart_main_path: str | Path | None = None,
    dart_master_path: str | Path | None = None,
) -> Dict[str, Any]:
    raise RuntimeError("Rule-based DART source audit is disabled. Use langgraph_flow LLM source context instead.")
    """Compare report DART evidence values with source DART JSON values."""

    resolved_paths = {
        "dart_main": str(Path(dart_main_path).expanduser().resolve()) if dart_main_path else "",
        "dart_master": str(Path(dart_master_path).expanduser().resolve()) if dart_master_path else "",
    }
    if not resolved_paths["dart_main"] and not resolved_paths["dart_master"]:
        return {
            "enabled": False,
            "status": "skipped",
            "source_paths": resolved_paths,
            "summary_ko": "DART 원천 파일 경로가 제공되지 않아 source audit을 수행하지 않았다.",
            "checks": [],
            "mismatches": [],
            "load_errors": [],
        }

    load_errors: List[str] = []
    dart_main = _load_optional_json(resolved_paths["dart_main"], "dart_main", load_errors)
    dart_master = _load_optional_json(resolved_paths["dart_master"], "dart_master", load_errors)
    source_values = _build_source_value_map(dart_main, dart_master)
    observations = _collect_report_source_observations(data)
    checks: List[Dict[str, Any]] = []
    for observation in observations:
        source_key = observation["source_key"]
        source_item = source_values.get(source_key)
        if not source_item or source_item.get("value") is None:
            continue
        reported_value = observation["reported_value"]
        source_value = source_item["value"]
        if not _is_number(reported_value) or not _is_number(source_value):
            continue
        tolerance = _numeric_tolerance(source_value)
        matched = abs(float(reported_value) - float(source_value)) <= tolerance
        check_id = f"SA{len(checks) + 1:03d}"
        checks.append(
            {
                "check_id": check_id,
                "status": "pass" if matched else "fail",
                "claim_id": observation.get("claim_id", ""),
                "source_key": source_key,
                "location": observation["location"],
                "source_file": source_item["source_file"],
                "reported_value": reported_value,
                "source_value": source_value,
                "tolerance": tolerance,
                "summary_ko": (
                    f"{source_key} 값이 DART 원천과 일치한다."
                    if matched
                    else f"{source_key} 값이 DART 원천과 불일치한다."
                ),
            }
        )

    mismatches = [check for check in checks if check["status"] == "fail"]
    if load_errors:
        status = "fail"
        summary = "DART 원천 파일을 일부 로드하지 못해 source audit에 실패했다."
    elif mismatches:
        status = "fail"
        summary = f"DART 원천과 불일치하는 수치가 {len(mismatches)}개 발견되었다."
    elif checks:
        status = "pass"
        summary = f"Financial report의 DART 수치 {len(checks)}개가 원천 DART 파일과 일치한다."
    else:
        status = "warn"
        summary = "DART 원천 파일은 로드했지만 비교 가능한 report 수치를 찾지 못했다."

    return {
        "enabled": True,
        "status": status,
        "source_paths": resolved_paths,
        "summary_ko": summary,
        "checks": checks,
        "mismatches": mismatches,
        "load_errors": load_errors,
    }


def _load_optional_json(path_value: str, label: str, errors: List[str]) -> Dict[str, Any]:
    if not path_value:
        return {}
    path = Path(path_value)
    if not path.exists():
        errors.append(f"{label} 파일이 존재하지 않는다: {path}")
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:  # pragma: no cover - defensive error path
        errors.append(f"{label} 파일 로드 실패: {path}: {exc}")
        return {}
    return payload if isinstance(payload, dict) else {}


def _build_source_value_map(dart_main: Dict[str, Any], dart_master: Dict[str, Any]) -> Dict[str, Dict[str, Any]]:
    source_values: Dict[str, Dict[str, Any]] = {}
    for metric_key in SOURCE_AUDIT_MAIN_METRICS:
        source_values[metric_key] = {
            "value": _dart_main_period_value(dart_main, metric_key, "current_fiscal_year"),
            "source_file": "dart_main",
        }
    for source_key, metric_key in SOURCE_AUDIT_PREVIOUS_METRICS.items():
        source_values[source_key] = {
            "value": _dart_main_period_value(dart_main, metric_key, "previous_fiscal_year"),
            "source_file": "dart_main",
        }
    source_values["revenue_growth"] = {
        "value": _dart_main_current_comparison_value(dart_main, "revenue_growth"),
        "source_file": "dart_main",
    }

    master_current: Dict[str, Any] = {}
    for source_key, (section_key, item_keys, labels) in SOURCE_AUDIT_MASTER_ITEMS.items():
        master_current[source_key] = _dart_master_current_value(dart_master, section_key, item_keys, labels)
        source_values[source_key] = {
            "value": master_current[source_key],
            "source_file": "dart_master",
        }
    source_values["current_ratio"] = {
        "value": _safe_div(master_current.get("current_assets"), master_current.get("current_liabilities")),
        "source_file": "dart_master",
    }
    source_values["cash_ratio"] = {
        "value": _safe_div(master_current.get("cash_and_cash_equivalents"), master_current.get("current_liabilities")),
        "source_file": "dart_master",
    }
    source_values["debt_to_equity"] = {
        "value": _safe_div(master_current.get("total_liabilities"), master_current.get("total_equity")),
        "source_file": "dart_master",
    }
    source_values["liabilities_to_assets"] = {
        "value": _safe_div(master_current.get("total_liabilities"), master_current.get("total_assets")),
        "source_file": "dart_master",
    }
    source_values["equity_ratio"] = {
        "value": _safe_div(master_current.get("total_equity"), master_current.get("total_assets")),
        "source_file": "dart_master",
    }
    return source_values


def _dart_main_period_value(dart_main: Dict[str, Any], metric_key: str, period_key: str) -> Any:
    return (
        (dart_main.get("metrics_by_key") or {})
        .get(metric_key, {})
        .get("values_by_period", {})
        .get(period_key, {})
        .get("value")
    )


def _dart_main_current_comparison_value(dart_main: Dict[str, Any], metric_key: str) -> Any:
    comparisons = (dart_main.get("metrics_by_key") or {}).get(metric_key, {}).get("comparisons", {})
    if not isinstance(comparisons, dict):
        return None
    for item in comparisons.values():
        if isinstance(item, dict) and item.get("current_period_key") == "current_fiscal_year":
            return item.get("value")
    for item in comparisons.values():
        if isinstance(item, dict):
            return item.get("value")
    return None


def _dart_master_current_value(
    dart_master: Dict[str, Any],
    section_key: str,
    item_keys: tuple[str, ...],
    labels: tuple[str, ...],
) -> Any:
    tables = (dart_master.get(section_key) or {}).get("tables") or []
    table = tables[0] if tables else {}
    items = table.get("items_by_key") or {}
    for item_key in item_keys:
        value = (items.get(item_key) or {}).get("current_numeric")
        if _is_number(value):
            return value
    normalized_labels = tuple(_normalize_label(label) for label in labels)
    for item in items.values():
        display_name = _normalize_label(item.get("display_name", ""))
        aliases = tuple(_normalize_label(alias) for alias in item.get("aliases", []))
        if any(label and (label in display_name or label in aliases) for label in normalized_labels):
            value = item.get("current_numeric")
            if _is_number(value):
                return value
    return None


def _collect_report_source_observations(data: Dict[str, Any]) -> List[Dict[str, Any]]:
    observations: List[Dict[str, Any]] = []
    for evidence in get_key_evidence(data):
        if "dart" not in normalize_source(evidence.get("source", "")):
            continue
        value = evidence.get("value")
        if isinstance(value, dict):
            observations.extend(
                _observations_from_mapping(
                    value,
                    location=f"sy_handoff.key_evidence.{evidence.get('evidence_id', '')}.value",
                    claim_id=evidence.get("claim_id", ""),
                )
            )
        else:
            source_key = _source_key_from_metric_text(evidence.get("metric_or_event", ""))
            if source_key and _is_number(value):
                observations.append(
                    {
                        "source_key": source_key,
                        "reported_value": value,
                        "location": f"sy_handoff.key_evidence.{evidence.get('evidence_id', '')}.value",
                        "claim_id": evidence.get("claim_id", ""),
                    }
                )
    detailed = data.get("detailed_analysis") or {}
    if isinstance(detailed, dict):
        for section_name, section in detailed.items():
            if not isinstance(section, dict):
                continue
            observations.extend(
                _observations_from_mapping(
                    section.get("supporting_features", {}),
                    location=f"detailed_analysis.{section_name}.supporting_features",
                    claim_id="",
                )
            )
    return observations


def _observations_from_mapping(value: Any, *, location: str, claim_id: str) -> List[Dict[str, Any]]:
    observations: List[Dict[str, Any]] = []
    if not isinstance(value, dict):
        return observations
    for key, child in value.items():
        source_key = _source_key_from_report_key(key)
        child_location = f"{location}.{key}"
        if source_key and _is_number(child):
            observations.append(
                {
                    "source_key": source_key,
                    "reported_value": child,
                    "location": child_location,
                    "claim_id": claim_id,
                }
            )
        elif isinstance(child, dict):
            observations.extend(_observations_from_mapping(child, location=child_location, claim_id=claim_id))
    return observations


def _source_key_from_report_key(key: Any) -> str:
    normalized = str(key or "").strip()
    if normalized in SOURCE_AUDIT_MAIN_METRICS or normalized in SOURCE_AUDIT_PREVIOUS_METRICS:
        return normalized
    if normalized in SOURCE_AUDIT_MASTER_ITEMS:
        return normalized
    if normalized in {"current_ratio", "cash_ratio", "debt_to_equity", "liabilities_to_assets", "equity_ratio", "revenue_growth"}:
        return normalized
    return ""


def _source_key_from_metric_text(value: Any) -> str:
    text = str(value or "").lower()
    if "revenue" in text and "growth" in text:
        return "revenue_growth"
    if "매출" in text and "증감" in text:
        return "revenue_growth"
    if "contribution" in text and "margin" in text:
        return "contribution_margin"
    if "공헌" in text and "이익률" in text:
        return "contribution_margin"
    if "sg&a" in text or "sga" in text or "판관비율" in text:
        return "sga_margin"
    if "eps" in text or "주당" in text:
        return "eps"
    if "revenue" in text or "매출" in text:
        return "revenue"
    return ""


def _numeric_tolerance(source_value: Any) -> float:
    value = abs(float(source_value))
    if value <= 1:
        return 1e-6
    return max(1.0, value * 1e-9)


def _is_number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _safe_div(numerator: Any, denominator: Any) -> float | None:
    if not _is_number(numerator) or not _is_number(denominator) or denominator == 0:
        return None
    return float(numerator) / float(denominator)


def _normalize_label(value: Any) -> str:
    return re.sub(r"[\s(),:：주0-9]+", "", str(value or "")).lower()


def apply_source_audit_to_evaluation(
    evaluation: Dict[str, Any],
    claim: Dict[str, Any],
    source_audit: Dict[str, Any] | None,
) -> Dict[str, Any]:
    raise RuntimeError("Rule-based source-audit evaluation is disabled. Use langgraph_flow LLM evaluation instead.")
    if not source_audit or source_audit.get("status") != "fail":
        evaluation["source_audit_refs"] = []
        return evaluation
    claim_id = str(claim.get("claim_id") or evaluation.get("claim_id") or "")
    mismatches = [
        item
        for item in source_audit.get("mismatches", [])
        if item.get("claim_id") and item.get("claim_id") == claim_id
    ]
    if not mismatches:
        evaluation["source_audit_refs"] = []
        return evaluation
    refs = [item["check_id"] for item in mismatches if item.get("check_id")]
    evaluation["source_audit_refs"] = refs
    if evaluation.get("decision") == "keep":
        evaluation["decision"] = "revise"
        evaluation["reason_ko"] = (
            f"{evaluation.get('reason_ko', '')} "
            f"DART 원천 파일과 불일치하는 수치가 있어 표현 보강 또는 수치 수정이 필요하다: {', '.join(refs)}."
        ).strip()
    return evaluation


def evaluate_claim(
    claim: Dict[str, Any],
    evidence_items: List[Dict[str, Any]],
    investment_decision_present: bool,
) -> Dict[str, Any]:
    raise RuntimeError("Rule-based claim evaluation is disabled. Use langgraph_flow LLM evaluation instead.")
    claim_id = claim.get("claim_id", "")
    dimension = claim.get("financial_dimension", "")
    answer = answer_for_claim(claim, evidence_items)
    evidence_refs = [item.get("evidence_id") for item in evidence_items if item.get("evidence_id")]

    decision = "keep"
    reason = "왜 이런 의견을 냈는지 입력 데이터로 답변 가능하다."

    if investment_decision_present:
        decision = "delete"
        reason = "buy/sell/hold 또는 목표주가 성격의 투자 판단 표현이 포함되어 삭제한다."
    elif not answer:
        decision = "delete"
        reason = "왜 이런 의견을 냈는지 답할 근거가 없어 삭제한다."
    elif dimension != "risk" and uses_only_context_sources(evidence_items) and not has_dart_anchor(claim, evidence_items):
        decision = "delete"
        reason = "News/Y-Finance만으로 재무 claim을 만들었으므로 삭제한다."
    elif dimension != "risk" and not has_dart_anchor(claim, evidence_items):
        decision = "delete"
        reason = "재무 claim인데 DART anchor가 없어 삭제한다."
    elif overstates_ytd_as_full_year(claim, evidence_items):
        decision = "delete"
        reason = "YTD 또는 분기 데이터를 연간 확정 실적처럼 과장해 삭제한다."
    else:
        missing_metrics = missing_required_metrics(claim, evidence_items)
        if missing_metrics:
            decision = "revise"
            reason = f"claim을 뒷받침할 필수 재무 지표가 부족해 표현 보강이 필요하다: {', '.join(missing_metrics)}."
        elif needs_ytd_caution(claim, evidence_items):
            decision = "revise"
            reason = "YTD 또는 분기 기준 claim이므로 연간 확정 개선으로 오해되지 않도록 기간 제한 표현을 보강해야 한다."
        elif has_unit_or_ratio_mismatch_risk(claim, evidence_items):
            decision = "revise"
            reason = "비율 또는 금액 단위 표현이 혼재될 수 있어 단위/계산식 명시가 필요하다."

    return {
        "claim_id": claim_id,
        "claim_origin": claim.get("claim_origin", ""),
        "section_path": claim.get("section_path", ""),
        "financial_dimension": dimension,
        "decision": decision,
        "reason_ko": reason,
        "answer_ko": answer,
        "evidence_refs": evidence_refs,
    }


def decide_claim(
    claim: Dict[str, Any],
    evidence_items: List[Dict[str, Any]],
    investment_decision_present: bool,
):
    raise RuntimeError("Rule-based claim decisions are disabled. Use langgraph_flow LLM evaluation instead.")
    evaluation = evaluate_claim(claim, evidence_items, investment_decision_present)
    return (
        evaluation["decision"],
        evaluation["reason_ko"],
        evaluation["answer_ko"],
        evaluation["evidence_refs"],
    )


def build_rule_checks(
    data: Dict[str, Any],
    claims: List[Dict[str, Any]],
    evaluations: List[Dict[str, Any]],
    source_audit: Dict[str, Any] | None = None,
) -> List[Dict[str, str]]:
    raise RuntimeError("Rule-based checks are disabled. Use llm_evaluation_checks from langgraph_flow instead.")
    missing_blocks = [block for block in REQUIRED_BLOCKS if block not in data]
    claim_count_ok = len(claims) <= CLAIM_COUNT_LIMIT
    investment_decision_present = has_investment_decision(data)
    deleted = [item for item in evaluations if item["decision"] == "delete"]
    revised = [item for item in evaluations if item["decision"] == "revise"]
    coverage_missing = missing_financial_statement_coverage(data, claims)
    checks = [
        {
            "rule_id": "R001",
            "rule_name": "Required Input Blocks",
            "status": "pass" if not missing_blocks else "fail",
            "summary_ko": "필수 입력 블록이 모두 존재한다." if not missing_blocks else f"필수 입력 블록 누락: {', '.join(missing_blocks)}",
        },
        {
            "rule_id": "R002",
            "rule_name": "Claim Count Limit",
            "status": "pass" if claim_count_ok else "fail",
            "summary_ko": f"claim 수는 {len(claims)}개이며 최대 {CLAIM_COUNT_LIMIT}개 제한을 충족한다." if claim_count_ok else f"claim 수가 {len(claims)}개로 최대 {CLAIM_COUNT_LIMIT}개 제한을 초과한다.",
        },
        {
            "rule_id": "R003",
            "rule_name": "No Buy Sell Hold",
            "status": "pass" if not investment_decision_present else "fail",
            "summary_ko": "buy/sell/hold/목표주가 성격의 투자 판단 표현이 없다." if not investment_decision_present else "투자 판단 표현이 감지되었다.",
        },
        {
            "rule_id": "R004",
            "rule_name": "Why Question Answerability",
            "status": "pass" if not deleted else "fail",
            "summary_ko": "모든 claim이 '왜 이런 의견을 냈어?'에 답할 수 있다." if not deleted else "답변 불가 또는 근거 부족 claim이 있어 삭제했다.",
        },
        {
            "rule_id": "R005",
            "rule_name": "DART Primary Anchor",
            "status": "pass" if not deleted else "fail",
            "summary_ko": "재무 claim은 DART anchor를 기준으로 검증했다." if not deleted else "DART anchor가 부족한 재무 claim이 삭제 대상에 포함되었다.",
        },
        {
            "rule_id": "R006",
            "rule_name": "Detailed Financial Statement Coverage",
            "status": "pass" if not coverage_missing else "fail",
            "summary_ko": "현금흐름, 재무상태표, 자본구조, 부채, 유동성 claim을 세부 검증에 포함했다."
            if not coverage_missing
            else f"세부 재무 검증 claim 누락: {', '.join(coverage_missing)}",
        },
        {
            "rule_id": "R007",
            "rule_name": "Revision Required Claims",
            "status": "pass" if not revised else "warn",
            "summary_ko": "표현 보강이 필요한 claim이 없다." if not revised else f"표현 보강이 필요한 claim {len(revised)}개가 있다.",
        },
    ]
    if source_audit:
        audit_status = str(source_audit.get("status") or "skipped")
        checks.append(
            {
                "rule_id": "R008",
                "rule_name": "DART Source Audit",
                "status": (
                    "pass"
                    if audit_status == "pass"
                    else "fail"
                    if audit_status == "fail"
                    else "warn"
                    if audit_status == "warn"
                    else "skip"
                ),
                "summary_ko": str(source_audit.get("summary_ko") or "DART source audit 결과가 없다."),
            }
        )
    return checks


def missing_financial_statement_coverage(data: Dict[str, Any], claims: List[Dict[str, Any]]) -> List[str]:
    if not data.get("financial_statement_view"):
        return []
    required = {"cash_flow", "balance_sheet", "capital_structure", "debt", "liquidity"}
    present = {str(item.get("financial_dimension")) for item in claims}
    return sorted(required - present)


def build_output(
    data: Dict[str, Any],
    input_path: Path,
    *,
    dart_main_path: str | Path | None = None,
    dart_master_path: str | Path | None = None,
) -> Dict[str, Any]:
    raise RuntimeError("Rule-based Financial SY output is disabled. Use langgraph_flow LLM evaluation instead.")
    claims = get_claims(data)
    evidence_map = evidence_by_claim(get_key_evidence(data))
    investment = has_investment_decision(data)
    source_audit = build_source_audit(data, dart_main_path=dart_main_path, dart_master_path=dart_master_path)
    evaluations = [
        apply_source_audit_to_evaluation(
            evaluate_claim(claim, evidence_map.get(claim.get("claim_id", ""), []), investment),
            claim,
            source_audit,
        )
        for claim in claims
    ]
    return {
        "agent_name": "SY Agent",
        "output_mode": "simple_rule_validation",
        "source_output_path": str(input_path),
        "source_audit": source_audit,
        "rule_based_checks": build_rule_checks(data, claims, evaluations, source_audit),
        "claim_validations": evaluations,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--dart-main", default=None)
    parser.add_argument("--dart-master", default=None)
    parser.add_argument("--skip-source-audit", action="store_true", help="Deprecated. DART files are LLM context only.")
    parser.add_argument("--legacy", action="store_true", help="Disabled. Rule-based validation is no longer supported.")
    parser.add_argument("--env-file", default=str(DEFAULT_ENV_FILE))
    parser.add_argument("--llm-provider", default="openai", choices=["auto", "openai"])
    parser.add_argument("--llm-model", default="auto")
    parser.add_argument("--llm-timeout", type=int, default=300)
    args = parser.parse_args()

    input_path = Path(args.input)
    output_path = Path(args.output)
    if args.legacy:
        raise RuntimeError("Legacy rule-based Financial SY validation has been disabled.")

    try:
        from .langgraph_flow import build_graph, load_env_file, resolve_llm_model, resolve_llm_provider
    except ImportError:  # pragma: no cover - supports direct script execution
        from langgraph_flow import build_graph, load_env_file, resolve_llm_model, resolve_llm_provider

    load_env_file(args.env_file)
    provider = resolve_llm_provider(args.llm_provider)
    llm_model = resolve_llm_model(provider, args.llm_model)
    app = build_graph()
    final_state = app.invoke(
        {
            "input_path": str(input_path),
            "env_file": str(args.env_file),
            "use_llm": True,
            "llm_provider": provider,
            "llm_model": llm_model,
            "llm_timeout": args.llm_timeout,
            "dart_main_path": "" if args.skip_source_audit else str(args.dart_main or ""),
            "dart_master_path": "" if args.skip_source_audit else str(args.dart_master or ""),
            "report_rewritten": False,
            "revision_brief": [],
            "rewrite_history": [],
            "dialogue_trace": [],
            "llm_calls": [],
        }
    )
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(final_state["final_output"], ensure_ascii=False, indent=2) + "\n")
    print(output_path)


if __name__ == "__main__":
    main()
