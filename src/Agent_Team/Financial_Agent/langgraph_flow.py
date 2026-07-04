#!/usr/bin/env python3
import argparse
import json
import os
from pathlib import Path
from typing import Any, Dict, List, TypedDict
from urllib import error, request

from langgraph.graph import END, START, StateGraph

try:
    from . import AGENT_DIR, DEFAULT_ENV_FILE, PROJECT_ROOT
except ImportError:  # pragma: no cover - supports direct script execution
    AGENT_DIR = Path(__file__).resolve().parent
    PROJECT_ROOT = AGENT_DIR.parents[2]
    DEFAULT_ENV_FILE = PROJECT_ROOT / "configs" / ".env"


DEFAULT_OPENAI_MODEL = "gpt-5.4-mini"


class FinancialAnalystGraphState(TypedDict, total=False):
    manifest_path: str
    env_file: str
    llm_provider: str
    llm_model: str
    llm_timeout: int
    use_llm: bool
    manifest: Dict[str, Any]
    inputs: Dict[str, Any]
    transcript: List[Dict[str, str]]
    llm_calls: List[Dict[str, Any]]
    financial_analysis_output: Dict[str, Any]
    pending_cross_data_reconciliation: Dict[str, Any]
    cross_data_reconciliation: Dict[str, Any]
    cross_analysis_questions: Dict[str, str]
    report_output: Dict[str, Any]
    schema_validation: Dict[str, Any]


CROSS_ANALYSIS_QUESTIONS = {
    "news_plus_dart": (
        "왜 News context와 DART 실적 요약이 정합적이라고 판단했는가? "
        "뉴스 내용이 DART 기반 재무 claim을 보조하거나 약화하는 지점을 설명하라."
    ),
    "market_plus_dart": (
        "왜 DART 실적 요약과 시장 데이터 해석이 정합적이라고 판단했는가? "
        "매출 성장, 이익률, EPS 정보가 가격 반등 해석과 어떻게 연결되는지 설명하라."
    ),
    "market_plus_news_plus_dart": (
        "왜 DART 메인지표, 뉴스 요약, 시장 지표를 함께 비교했을 때 정합 또는 괴리 판단이 가능한가? "
        "메인지표 1개와 보조지표 2개를 한 번에 연결해서 설명하라."
    ),
}


def load_json(path: str) -> Dict[str, Any]:
    return json.loads(Path(path).read_text())


def load_input_file(path: str) -> Any:
    return json.loads(Path(path).read_text())


def load_env_file(path: str) -> None:
    env_path = Path(path)
    if not env_path.exists():
        return
    for raw_line in env_path.read_text().splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = value


def resolve_llm_provider(provider: str) -> str:
    if provider == "none":
        return "none"
    if provider not in {"auto", "openai"}:
        raise RuntimeError(f"Unsupported LLM provider: {provider}. Only openai is supported.")
    if provider == "openai":
        return "openai"
    if os.getenv("OPENAI_API_KEY"):
        return "openai"
    return "none"


def resolve_llm_model(provider: str, model: str) -> str:
    if model and model != "auto":
        return model
    if provider == "openai":
        return os.getenv("OPENAI_MODEL", DEFAULT_OPENAI_MODEL)
    return model or DEFAULT_OPENAI_MODEL


def call_openai(prompt: str, model: str, timeout: int) -> Dict[str, Any]:
    api_key = os.getenv("OPENAI_API_KEY")
    if not api_key:
        raise RuntimeError("OPENAI_API_KEY is not set")
    base_url = os.getenv("OPENAI_BASE_URL", "https://api.openai.com/v1").rstrip("/")
    payload = {
        "model": model,
        "messages": [{"role": "user", "content": prompt}],
        "temperature": 0.2,
        "max_tokens": 2048,
    }
    req = request.Request(
        f"{base_url}/chat/completions",
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    try:
        with request.urlopen(req, timeout=timeout) as response:
            raw = response.read().decode("utf-8")
    except error.HTTPError as exc:
        body = exc.read().decode("utf-8", "ignore")
        raise RuntimeError(f"OpenAI HTTP {exc.code}: {body[:500]}") from exc
    result = json.loads(raw)
    choices = result.get("choices") or []
    if not choices:
        raise RuntimeError(f"OpenAI returned no choices: {result}")
    text = choices[0].get("message", {}).get("content", "").strip()
    if not text:
        raise RuntimeError("OpenAI returned empty text")
    return {"text": text, "usage": result.get("usage", {})}


def summarize_llm_usage(calls: List[Dict[str, Any]]) -> Dict[str, Any]:
    usage_keys = [
        "promptTokenCount",
        "candidatesTokenCount",
        "thoughtsTokenCount",
        "cachedContentTokenCount",
        "totalTokenCount",
        "prompt_tokens",
        "completion_tokens",
        "total_tokens",
    ]
    summary: Dict[str, Any] = {
        "api_call_count": 0,
        "adopted_llm_call_count": 0,
        "fallback_call_count": 0,
        "by_field": {key: 0 for key in usage_keys},
    }
    for call in calls:
        usage = call.get("usage") or {}
        if usage:
            summary["api_call_count"] += 1
            for key in usage_keys:
                summary["by_field"][key] += int(usage.get(key) or 0)
        if call.get("used_llm"):
            summary["adopted_llm_call_count"] += 1
        elif call.get("status", "").startswith("fallback"):
            summary["fallback_call_count"] += 1
    return summary


def is_complete_korean_text(text: str) -> bool:
    stripped = text.strip()
    if len(stripped) < 12:
        return False
    return stripped[-1] in {".", "?", "!", "다", "요", "함", "음", "라", "됨", "임"}


def clean_llm_text(text: str) -> str:
    lines = []
    for raw_line in text.replace("```", "").splitlines():
        line = raw_line.strip()
        if not line:
            continue
        if line.startswith("**") and line.endswith("**"):
            continue
        lowered = line.lower()
        if lowered.startswith("validation.summary_ko"):
            continue
        lines.append(line)
    return "\n".join(lines).strip()


def llm_generate(
    state: FinancialAnalystGraphState,
    node: str,
    prompt: str,
    fallback: str,
) -> str:
    state.setdefault("llm_calls", [])
    provider = state.get("llm_provider", "none")
    if not state.get("use_llm") or provider == "none":
        state["llm_calls"].append(
            {"node": node, "provider": provider, "used_llm": False, "status": "fallback"}
        )
        return fallback
    try:
        if provider != "openai":
            raise RuntimeError(f"Unsupported LLM provider: {provider}")
        response = call_openai(prompt, state.get("llm_model", DEFAULT_OPENAI_MODEL), state.get("llm_timeout", 60))
        text = response["text"]
        usage = response.get("usage", {})
    except Exception as exc:
        state["llm_calls"].append(
            {
                "node": node,
                "provider": provider,
                "used_llm": False,
                "status": "fallback_after_error",
                "error": str(exc),
            }
        )
        return fallback
    text = clean_llm_text(text)
    if not is_complete_korean_text(text):
        state["llm_calls"].append(
            {
                "node": node,
                "provider": provider,
                "model": state.get("llm_model"),
                "used_llm": False,
                "status": "fallback_after_incomplete_text",
                "raw_length": len(text),
                "usage": usage,
            }
        )
        return fallback
    state["llm_calls"].append(
        {
            "node": node,
            "provider": provider,
            "model": state.get("llm_model"),
            "used_llm": True,
            "status": "ok",
            "usage": usage,
        }
    )
    return text


def metric_period(dart: Dict[str, Any], key: str, period_key: str = "current_fiscal_year") -> Dict[str, Any]:
    return dart["metrics_by_key"][key]["values_by_period"][period_key]


def metric_comp(dart: Dict[str, Any], key: str, comp: str = "2025_vs_2024") -> Dict[str, Any]:
    return dart["metrics_by_key"][key]["comparisons"][comp]


def pct(value: float | None) -> str:
    if value is None:
        return "N/A"
    return f"{value:.2%}"


def pct1(value: float | None) -> str:
    if value is None:
        return "N/A"
    return f"{value:.1%}"


def ratio1(value: float | None) -> str:
    if value is None:
        return "N/A"
    return f"{value:.1f}배"


def ratio2(value: float | None) -> str:
    if value is None:
        return "N/A"
    return f"{value:.2f}배"


def krw_eok(value: int | float | None) -> str:
    if value is None:
        return "N/A"
    return f"{value / 100_000_000:,.0f}억원"


def won(value: int | float | None) -> str:
    if value is None:
        return "N/A"
    return f"{value:,.0f}원"


def safe_div(numerator: int | float | None, denominator: int | float | None) -> float | None:
    if numerator is None or denominator in (None, 0):
        return None
    return numerator / denominator


def first_table(master: Dict[str, Any], section_key: str) -> Dict[str, Any]:
    tables = master.get(section_key, {}).get("tables", [])
    return tables[0] if tables else {}


def item_by_key(master: Dict[str, Any], section_key: str, item_key: str) -> Dict[str, Any]:
    return first_table(master, section_key).get("items_by_key", {}).get(item_key, {})


def current_numeric(master: Dict[str, Any], section_key: str, item_key: str) -> int | float | None:
    value = item_by_key(master, section_key, item_key).get("current_numeric")
    return value if isinstance(value, (int, float)) else None


def numeric_by_display_name(master: Dict[str, Any], section_key: str, display_name: str) -> int | float | None:
    items = first_table(master, section_key).get("items_by_key", {})
    for item in items.values():
        if item.get("display_name") == display_name:
            value = item.get("current_numeric")
            return value if isinstance(value, (int, float)) else None
    return None


def build_financial_position_summary(master: Dict[str, Any]) -> Dict[str, Any]:
    current_assets = current_numeric(master, "4-1", "current_assets")
    cash = current_numeric(master, "4-1", "cash_and_cash_equivalents")
    non_current_assets = current_numeric(master, "4-1", "non_current_assets")
    total_assets = current_numeric(master, "4-1", "total_assets")
    current_liabilities = current_numeric(master, "4-1", "current_liabilities")
    non_current_liabilities = current_numeric(master, "4-1", "non_current_liabilities")
    total_liabilities = current_numeric(master, "4-1", "total_liabilities")
    total_equity = current_numeric(master, "4-1", "total_equity")

    operating_cash_flow = current_numeric(master, "4-4", "cash_flows_from_operating_activities")
    investing_cash_flow = numeric_by_display_name(master, "4-4", "투자활동으로 인한 현금흐름")
    financing_cash_flow = numeric_by_display_name(master, "4-4", "재무활동으로 인한 현금흐름")
    net_cash_change = numeric_by_display_name(master, "4-4", "현금및현금성자산의 순증감")

    return {
        "current_assets": current_assets,
        "cash_and_cash_equivalents": cash,
        "non_current_assets": non_current_assets,
        "total_assets": total_assets,
        "current_liabilities": current_liabilities,
        "non_current_liabilities": non_current_liabilities,
        "total_liabilities": total_liabilities,
        "total_equity": total_equity,
        "debt_to_equity": safe_div(total_liabilities, total_equity),
        "liabilities_to_assets": safe_div(total_liabilities, total_assets),
        "equity_ratio": safe_div(total_equity, total_assets),
        "current_ratio": safe_div(current_assets, current_liabilities),
        "cash_ratio": safe_div(cash, current_liabilities),
        "operating_cash_flow": operating_cash_flow,
        "investing_cash_flow": investing_cash_flow,
        "financing_cash_flow": financing_cash_flow,
        "net_cash_change": net_cash_change,
    }


def resolve_dart_master_path(paths: Dict[str, str]) -> Path | None:
    explicit_path = paths.get("dart_master")
    if explicit_path:
        return Path(explicit_path)
    dart_main_path = paths.get("dart_main")
    if not dart_main_path:
        return None
    candidate = Path(dart_main_path).with_name("dart_master.json")
    return candidate if candidate.exists() else None


def latest_news_period(news: Dict[str, Any]) -> Dict[str, Any]:
    periods = news.get("output", {}).get("periods", [])
    return periods[0] if periods else {"period": "", "period_summary": "", "issues": []}


def issue_names(news_period: Dict[str, Any], limit: int = 3, keywords: tuple[str, ...] | None = None) -> List[str]:
    names: List[str] = []
    for issue in news_period.get("issues", []):
        name = str(issue.get("issue") or "").strip()
        if not name:
            continue
        if keywords:
            searchable = f"{name} {issue.get('rationale', '')}".lower()
            if not any(keyword.lower() in searchable for keyword in keywords):
                continue
        names.append(name)
        if len(names) >= limit:
            break
    return names


def join_or_default(items: List[str], default: str) -> str:
    return ", ".join(item for item in items if item) or default


def short_text(text: str, limit: int = 220) -> str:
    text = " ".join(str(text or "").split())
    if len(text) <= limit:
        return text
    return text[: limit - 1].rstrip() + "..."


def format_period(period: Dict[str, Any] | None) -> str:
    if not period:
        return "확인 기간"
    fiscal_year = period.get("fiscal_year")
    period_type = period.get("period_type")
    basis = period.get("basis")
    parts = [str(part) for part in (fiscal_year, period_type, basis) if part not in (None, "")]
    if parts:
        return " ".join(parts)
    return str(period.get("label") or period.get("period_end") or "확인 기간")


def current_comparison_pair(dart: Dict[str, Any]) -> Dict[str, Any]:
    pairs = dart.get("comparison_pairs", [])
    for pair in pairs:
        if pair.get("current_period_key") == "current_fiscal_year":
            return pair
    return pairs[0] if pairs else {}


def metric_period_value(dart: Dict[str, Any], key: str, period_key: str = "current_fiscal_year") -> int | float | None:
    try:
        value = metric_period(dart, key, period_key).get("value")
    except KeyError:
        return None
    return value if isinstance(value, (int, float)) else None


def metric_comparison_value(dart: Dict[str, Any], key: str, comparison_key: str | None) -> int | float | None:
    if not comparison_key:
        return None
    try:
        value = metric_comp(dart, key, comparison_key).get("value")
    except KeyError:
        return None
    return value if isinstance(value, (int, float)) else None


def signal_from_delta(current: int | float | None, previous: int | float | None, higher_is_better: bool = True) -> int:
    if current is None or previous is None:
        return 0
    delta = current - previous
    if abs(delta) < 1e-12:
        return 0
    improved = delta > 0 if higher_is_better else delta < 0
    return 1 if improved else -1


def signal_from_value(value: int | float | None, threshold: int | float = 0) -> int:
    if value is None:
        return 0
    if value > threshold:
        return 1
    if value < threshold:
        return -1
    return 0


def movement_ko(current: int | float | None, previous: int | float | None, lower_is_better: bool = False) -> str:
    if current is None or previous is None:
        return "비교 제한"
    delta = current - previous
    if abs(delta) < 1e-12:
        return "유지"
    improved = delta < 0 if lower_is_better else delta > 0
    direction = "하락" if delta < 0 else "상승"
    return f"{direction}해 {'개선' if improved else '부담 확대'} 방향"


def basis_caution(current_period: Dict[str, Any], previous_period: Dict[str, Any]) -> str:
    current_label = format_period(current_period)
    previous_label = format_period(previous_period)
    if current_period.get("basis") and previous_period.get("basis") and current_period.get("basis") != previous_period.get("basis"):
        return f"{current_label}와 {previous_label}는 집계 기준이 달라 동일 기간 YoY로 단정하지 않는다."
    return f"{current_label}와 {previous_label}는 동일 집계 기준일 때만 직접 비교한다."


def market_metric(yf: Dict[str, Any], key: str) -> float | None:
    value = yf.get(key)
    return value if isinstance(value, (int, float)) else None


def market_context_sentence(yf: Dict[str, Any], market_date: str) -> str:
    return (
        f"{market_date} 기준 20일 주가수익률 {pct(market_metric(yf, 'stock_return_20d'))}, "
        f"60일 주가수익률 {pct(market_metric(yf, 'stock_return_60d'))}, "
        f"20일 초과수익률 {pct(market_metric(yf, 'stock_excess_return_20d'))}, "
        f"60일 상대강도 {pct(market_metric(yf, 'stock_relative_strength_60'))}가 확인된다."
    )


def cash_flow_stance(position: Dict[str, Any]) -> str:
    operating = position.get("operating_cash_flow")
    net_change = position.get("net_cash_change")
    if operating is None:
        return "현금흐름 확인 제한"
    if operating > 0 and (net_change is None or net_change >= 0):
        return "영업현금흐름은 양수이며 현금 잔액도 방어되는 구조"
    if operating > 0:
        return "영업현금흐름은 양수이나 전체 현금 잔액은 감소"
    return "영업현금흐름 부담 확인"


def capital_structure_stance(position: Dict[str, Any]) -> str:
    debt_to_equity = position.get("debt_to_equity")
    equity_ratio = position.get("equity_ratio")
    if debt_to_equity is None and equity_ratio is None:
        return "자본 구조 확인 제한"
    if (equity_ratio is not None and equity_ratio >= 0.5) and (debt_to_equity is None or debt_to_equity <= 1.0):
        return "자본 비중이 높고 부채 부담은 제한적인 구조"
    if debt_to_equity is not None and debt_to_equity > 1.0:
        return "부채비율 부담을 함께 점검해야 하는 구조"
    return "자본과 부채 균형을 추가 점검해야 하는 구조"


def liquidity_stance(position: Dict[str, Any]) -> str:
    current_ratio = position.get("current_ratio")
    cash_ratio = position.get("cash_ratio")
    if current_ratio is None:
        return "단기 유동성 확인 제한"
    if current_ratio >= 1.5:
        return "단기 유동성은 비교적 안정적인 편"
    if current_ratio >= 1.0:
        return "단기 유동성은 최소 지급능력을 충족하는 수준"
    cash_text = f", 현금비율 {pct1(cash_ratio)}" if cash_ratio is not None else ""
    return f"단기 유동성 부담 점검 필요{cash_text}"


def build_financial_analyst_output(manifest: Dict[str, Any], inputs: Dict[str, Any]) -> Dict[str, Any]:
    dart = inputs["dart_main"]
    dart_master = inputs.get("dart_master", {})
    position = build_financial_position_summary(dart_master)
    yf_source = inputs.get("yfinance_market_summary") or []
    if isinstance(yf_source, list):
        yf = yf_source[0] if yf_source else {}
    elif isinstance(yf_source, dict):
        yf = yf_source
    else:
        yf = {}
    news_period = latest_news_period(inputs.get("news_llm_period_summaries", {}))
    news_summary = news_period.get("period_summary", "")
    current_period = dart.get("periods", {}).get("current_fiscal_year", {})
    comparison_pair = current_comparison_pair(dart)
    previous_period_key = comparison_pair.get("previous_period_key", "previous_fiscal_year")
    previous_period = dart.get("periods", {}).get(previous_period_key, {})
    comparison_key = comparison_pair.get("comparison_key")
    period_label = format_period(current_period)
    previous_period_label = format_period(previous_period)
    period_caution = basis_caution(current_period, previous_period)
    period_basis = str(current_period.get("basis") or "period")
    target = manifest.get("target_entity", {})
    company_name = str(target.get("company_name") or "분석 대상 기업")
    ticker = str(target.get("ticker") or "")
    corp_code = str(target.get("corp_code") or target.get("company_code") or "")
    as_of_date = str(target.get("as_of_date") or yf.get("date") or current_period.get("period_end") or "")
    market_date = str(yf.get("date") or as_of_date or "시장 기준일")

    revenue = metric_period_value(dart, "revenue")
    previous_revenue = metric_period_value(dart, "revenue", previous_period_key)
    revenue_growth = metric_comparison_value(dart, "revenue_growth", comparison_key)
    if revenue_growth is None and revenue is not None and previous_revenue not in (None, 0):
        revenue_growth = (revenue - previous_revenue) / previous_revenue
    contribution_margin = metric_period_value(dart, "contribution_margin")
    previous_contribution_margin = metric_period_value(dart, "contribution_margin", previous_period_key)
    sga_margin = metric_period_value(dart, "sga_margin")
    previous_sga_margin = metric_period_value(dart, "sga_margin", previous_period_key)
    eps = metric_period_value(dart, "eps")
    previous_eps = metric_period_value(dart, "eps", previous_period_key)

    news_issue_list = issue_names(news_period, limit=3)
    news_issue_summary = join_or_default(news_issue_list, "뉴스 주요 이슈가 제한적으로 확인됨")
    news_growth_summary = join_or_default(
        issue_names(
            news_period,
            limit=3,
            keywords=("성장", "확대", "승인", "수주", "호조", "개선", "협력", "진출", "매출", "기대", "개발", "투자"),
        )
        or news_issue_list,
        "뉴스 기반 성장/사업 이슈가 제한적으로 확인됨",
    )
    news_risk_summary = join_or_default(
        issue_names(
            news_period,
            limit=3,
            keywords=("리스크", "위험", "우려", "규제", "소송", "특허", "관세", "경고", "부진", "하락", "불확실", "경쟁", "감소", "손실", "적자", "약가"),
        ),
        "뉴스 기반 리스크 이슈가 제한적으로 확인됨",
    )
    news_summary_short = short_text(news_summary, 260) or news_issue_summary
    market_context = market_context_sentence(yf, market_date)

    revenue_stance = (
        "증가" if signal_from_value(revenue_growth) > 0
        else "감소" if signal_from_value(revenue_growth) < 0
        else "확인 제한 또는 유지"
    )
    profitability_stance = (
        "공헌이익률 개선" if signal_from_delta(contribution_margin, previous_contribution_margin) > 0
        else "공헌이익률 약화" if signal_from_delta(contribution_margin, previous_contribution_margin) < 0
        else "공헌이익률 비교 제한 또는 유지"
    )
    cost_stance = (
        "판관비율 하락으로 비용 효율성 개선" if signal_from_delta(sga_margin, previous_sga_margin, higher_is_better=False) > 0
        else "판관비율 상승으로 비용 부담 확대" if signal_from_delta(sga_margin, previous_sga_margin, higher_is_better=False) < 0
        else "판관비율 비교 제한 또는 유지"
    )
    eps_stance = "흑자 기조" if signal_from_value(eps) > 0 else "적자 또는 EPS 부담" if signal_from_value(eps) < 0 else "확인 제한 또는 손익분기 수준"
    overall_score = (
        signal_from_value(revenue_growth)
        + signal_from_delta(contribution_margin, previous_contribution_margin)
        + signal_from_delta(sga_margin, previous_sga_margin, higher_is_better=False)
        + signal_from_value(eps)
        + signal_from_value(position.get("operating_cash_flow"))
        + signal_from_value(position.get("current_ratio"), threshold=1.0)
    )
    direction = "positive" if overall_score >= 2 else "negative" if overall_score <= -2 else "mixed"
    direction_ko = {"positive": "개선 신호 우세", "negative": "부담 신호 우세", "mixed": "혼재된 신호"}[direction]

    revenue_anchor = (
        f"DART 기준 {period_label} 매출은 {krw_eok(revenue)}이고, "
        f"비교 기준인 {previous_period_label} 매출은 {krw_eok(previous_revenue)}이다. "
        f"증감률은 {pct(revenue_growth)}이며, {period_caution}"
    )
    profitability_anchor = (
        f"DART 기준 공헌이익률은 {pct(contribution_margin)}이고 {previous_period_label} 공헌이익률은 "
        f"{pct(previous_contribution_margin)}이다. 판관비율은 {pct(sga_margin)}이고 "
        f"{previous_period_label} 판관비율은 {pct(previous_sga_margin)}이다."
    )
    eps_anchor = (
        f"DART 기준 {period_label} EPS는 {won(eps)}이고, "
        f"{previous_period_label} EPS는 {won(previous_eps)}이다. {period_caution}"
    )
    financial_position_anchor = (
        f"DART 기준 영업활동현금흐름 {krw_eok(position['operating_cash_flow'])}, "
        f"현금및현금성자산 {krw_eok(position['cash_and_cash_equivalents'])}, "
        f"자산총계 {krw_eok(position['total_assets'])}, 부채총계 {krw_eok(position['total_liabilities'])}, "
        f"자본총계 {krw_eok(position['total_equity'])}, 유동비율 {pct1(position['current_ratio'])}, "
        f"부채비율 {pct1(position['debt_to_equity'])}가 확인된다."
    )
    claims = [
        {
            "claim_id": "F001",
            "claim_ko": f"{period_label} 기준 {company_name}의 매출 흐름은 {revenue_stance}로 해석된다.",
            "financial_dimension": "growth",
            "status": "active",
            "dart_anchor_summary_ko": revenue_anchor,
            "context_summary_ko": f"News 주요 사업/성장 이슈는 {news_growth_summary}이다. {market_context}",
            "caution_ko": period_caution,
            "action_for_sy": "use_with_caution",
        },
        {
            "claim_id": "F002",
            "claim_ko": f"{period_label} 기준 {company_name}의 수익성과 비용 효율성은 공헌이익률 측면({profitability_stance})과 비용 측면({cost_stance})을 나눠 판단하되, 연간 확정 개선으로 단정하지 않는다.",
            "financial_dimension": "profitability",
            "status": "active",
            "dart_anchor_summary_ko": profitability_anchor,
            "context_summary_ko": f"News context는 {news_issue_summary}를 제공하며, 수익성 claim의 직접 근거는 DART 마진과 비용 지표로 제한한다.",
            "caution_ko": f"마진과 비용 지표도 {period_basis} 기준이므로 비교 기간 차이를 함께 표시한다.",
            "action_for_sy": "use_normally",
        },
        {
            "claim_id": "F003",
            "claim_ko": f"{company_name}의 EPS는 {eps_stance}로 해석하되 비교 기간 차이를 함께 반영해야 한다.",
            "financial_dimension": "eps",
            "status": "caution",
            "dart_anchor_summary_ko": eps_anchor,
            "context_summary_ko": f"{market_context} 가격 데이터는 EPS claim의 보조 context이며 직접 회계 근거는 아니다.",
            "caution_ko": period_caution,
            "action_for_sy": "use_with_caution",
        },
        {
            "claim_id": "F004",
            "claim_ko": f"{company_name}의 현금흐름, 재무상태표, 자본 구조, 부채, 유동성은 DART 재무상태표와 현금흐름표 기준으로 함께 검토해야 한다.",
            "financial_dimension": "financial_position",
            "status": "conditional",
            "dart_anchor_summary_ko": financial_position_anchor,
            "context_summary_ko": f"{cash_flow_stance(position)}, {capital_structure_stance(position)}, {liquidity_stance(position)}으로 요약된다.",
            "caution_ko": "재무상태표는 특정 시점 기준이고 현금흐름표는 누적 기간 기준이므로 서로 같은 의미의 기간 지표로 혼용하지 않는다.",
            "action_for_sy": "use_normally",
        },
        {
            "claim_id": "F005",
            "claim_ko": f"{company_name}의 재무 claim은 DART를 기준으로 하되 News와 시장 데이터의 지속성 리스크를 보조적으로 반영해야 한다.",
            "financial_dimension": "risk",
            "status": "conditional",
            "dart_anchor_summary_ko": "DART 수치는 재무 claim의 primary anchor이며, News와 Y-Finance는 재무 수치를 대체하지 않는다.",
            "context_summary_ko": f"News 주요 리스크/주의 이슈는 {news_risk_summary}이다. {market_context}",
            "caution_ko": "News 리스크나 주가 반응만으로 DART 기반 재무 claim을 단독 기각하거나 확정하지 않는다.",
            "action_for_sy": "use_with_caution",
        },
    ]
    evidence = [
        {
            "evidence_id": "E001",
            "claim_id": "F001",
            "source": "DART",
            "metric_or_event": f"{period_label} revenue",
            "period": period_label,
            "value": revenue,
            "period_basis": period_basis,
            "interpretation_ko": "최신연도 매출 방향성 개선의 핵심 anchor다.",
        },
        {
            "evidence_id": "E002",
            "claim_id": "F001",
            "source": "DART",
            "metric_or_event": "revenue growth",
            "period": comparison_key or f"{period_label}_vs_{previous_period_label}",
            "value": revenue_growth,
            "period_basis": period_basis,
            "interpretation_ko": f"{period_caution} 따라서 성장률은 기간 기준을 함께 표시해야 한다.",
        },
        {
            "evidence_id": "E003",
            "claim_id": "F002",
            "source": "DART",
            "metric_or_event": "contribution margin",
            "period": period_label,
            "value": contribution_margin,
            "period_basis": period_basis,
            "interpretation_ko": "수익성 개선 방향의 직접 근거다.",
        },
        {
            "evidence_id": "E004",
            "claim_id": "F002",
            "source": "DART",
            "metric_or_event": "SG&A margin",
            "period": period_label,
            "value": sga_margin,
            "period_basis": period_basis,
            "interpretation_ko": "비용 효율성 개선 방향의 직접 근거다.",
        },
        {
            "evidence_id": "E005",
            "claim_id": "F003",
            "source": "DART",
            "metric_or_event": "EPS",
            "period": period_label,
            "value": eps,
            "period_basis": period_basis,
            "interpretation_ko": "흑자 기조는 확인되나 연간 확정 비교는 아니다.",
        },
        {
            "evidence_id": "E006",
            "claim_id": "F001",
            "source": "News",
            "metric_or_event": news_growth_summary,
            "period": str(news_period.get("period") or "latest_news_period"),
            "value": None,
            "period_basis": "context_only",
            "interpretation_ko": "매출 개선의 배경 context다.",
        },
        {
            "evidence_id": "E007",
            "claim_id": "F005",
            "source": "News",
            "metric_or_event": news_risk_summary,
            "period": str(news_period.get("period") or "latest_news_period"),
            "value": None,
            "period_basis": "context_only",
            "interpretation_ko": "재무 개선 지속성에 대한 주의 context다.",
        },
        {
            "evidence_id": "E008",
            "claim_id": "F005",
            "source": "Y-Finance",
            "metric_or_event": "relative performance",
            "period": market_date,
            "value": {
                "20d_excess": market_metric(yf, "stock_excess_return_20d"),
                "60d_relative_strength": market_metric(yf, "stock_relative_strength_60"),
            },
            "period_basis": "context_only",
            "interpretation_ko": "상대성과 혼재로 가격 확인 강도를 낮춘다.",
        },
        {
            "evidence_id": "E009",
            "claim_id": "F004",
            "source": "DART",
            "metric_or_event": "cash flow snapshot",
            "period": period_label,
            "value": {
                "operating_cash_flow": position["operating_cash_flow"],
                "investing_cash_flow": position["investing_cash_flow"],
                "financing_cash_flow": position["financing_cash_flow"],
                "net_cash_change": position["net_cash_change"],
            },
            "period_basis": period_basis,
            "interpretation_ko": "영업현금흐름과 현금 증감 방향을 확인하는 보조 DART anchor다.",
        },
        {
            "evidence_id": "E010",
            "claim_id": "F004",
            "source": "DART",
            "metric_or_event": "balance sheet and liquidity snapshot",
            "period": current_period.get("period_end") or period_label,
            "value": {
                "total_assets": position["total_assets"],
                "total_liabilities": position["total_liabilities"],
                "total_equity": position["total_equity"],
                "current_ratio": position["current_ratio"],
                "cash_ratio": position["cash_ratio"],
                "debt_to_equity": position["debt_to_equity"],
            },
            "period_basis": "POINT_IN_TIME",
            "interpretation_ko": "재무상태표 안정성, 부채 부담, 단기 유동성을 확인하는 DART anchor다.",
        },
    ]

    return {
        "agent_name": "Financial Analyst Agent",
        "role": "DART-based Financial Statement Analyst",
        "target_company": company_name,
        "ticker": ticker,
        "corp_code": corp_code,
        "as_of_date": as_of_date,
        "main_view": {
            "summary": (
                f"{company_name}은 {period_label} 기준 DART에서 매출 {krw_eok(revenue)}, "
                f"공헌이익률 {pct(contribution_margin)}, 판관비율 {pct(sga_margin)}가 확인된다. EPS는 {won(eps)}이다. "
                f"종합 신호는 {direction_ko}이며, News와 Y-Finance는 재무 수치의 직접 근거가 아닌 보조 검증 context로만 사용한다."
            ),
            "direction": direction,
            "primary_basis": [
                f"DART 기준 {period_label} 매출 {krw_eok(revenue)}, {previous_period_label} 매출 {krw_eok(previous_revenue)}, 증감률 {pct(revenue_growth)}",
                f"공헌이익률 {pct(contribution_margin)}, {previous_period_label} 대비 {movement_ko(contribution_margin, previous_contribution_margin)}",
                f"판관비율 {pct(sga_margin)}, {previous_period_label} 대비 {movement_ko(sga_margin, previous_sga_margin, lower_is_better=True)}",
                f"EPS는 {won(eps)}이며 {previous_period_label} EPS {won(previous_eps)}와 기간 기준을 함께 확인",
                f"영업활동현금흐름은 {krw_eok(position['operating_cash_flow'])}, 현금및현금성자산은 {krw_eok(position['cash_and_cash_equivalents'])}",
                f"자산총계 {krw_eok(position['total_assets'])}, 부채총계 {krw_eok(position['total_liabilities'])}, 자본총계 {krw_eok(position['total_equity'])}",
                f"부채비율 {pct1(position['debt_to_equity'])}, 유동비율 {pct1(position['current_ratio'])}로 재무상태표 안정성도 함께 확인"
            ],
            "main_cautions": [
                period_caution,
                "News 촉매는 재무 수치의 직접 증거가 아니다.",
                "주가 상승만으로 펀더멘털 개선을 주장하지 않는다.",
                f"News 주요 리스크/주의 이슈({news_risk_summary})는 재무 claim의 지속성 검토 요인으로만 사용한다."
            ],
            "not_investment_decision": True,
        },
        "financial_statement_view": {
            "revenue_growth": {
                "stance": revenue_stance,
                "reasoning": f"{period_label} 매출은 {krw_eok(revenue)}이고 {previous_period_label} 매출은 {krw_eok(previous_revenue)}이다. {period_caution}",
                "key_features": [f"{period_label} 매출 {krw_eok(revenue)}", f"{comparison_key or 'current_vs_previous'} 매출 증감률 {pct(revenue_growth)}", f"{period_label}와 {previous_period_label} 비교"]
            },
            "profitability": {
                "stance": profitability_stance,
                "reasoning": f"공헌이익률은 {pct(contribution_margin)}로 {previous_period_label} {pct(previous_contribution_margin)} 대비 {movement_ko(contribution_margin, previous_contribution_margin)}이다.",
                "key_features": [f"공헌이익률 {pct(contribution_margin)}", f"{previous_period_label} 공헌이익률 {pct(previous_contribution_margin)}", "DART 직접 근거"]
            },
            "cost_efficiency": {
                "stance": cost_stance,
                "reasoning": f"판관비율은 {pct(sga_margin)}로 {previous_period_label} {pct(previous_sga_margin)} 대비 {movement_ko(sga_margin, previous_sga_margin, lower_is_better=True)}이다.",
                "key_features": [f"판관비율 {pct(sga_margin)}", f"{previous_period_label} 판관비율 {pct(previous_sga_margin)}", f"{period_label} 기준"]
            },
            "eps": {
                "stance": eps_stance,
                "reasoning": f"{period_label} EPS는 {won(eps)}이고 {previous_period_label} EPS는 {won(previous_eps)}이다. {period_caution}",
                "key_features": [f"{period_label} EPS {won(eps)}", f"{previous_period_label} EPS {won(previous_eps)}", "기간 기준 확인 필요"]
            },
            "risk_context": {
                "stance": "News·시장 context는 보수적 가중치 요인",
                "reasoning": f"News 주요 이슈는 {news_issue_summary}이며, 시장 데이터는 {market_context} DART 기반 재무 개선을 대체하지 않고 지속성 및 가격 확인 강도를 조정하는 보조 context로만 사용한다.",
                "key_features": [news_risk_summary, f"20일 초과수익률 {pct(market_metric(yf, 'stock_excess_return_20d'))}", f"60일 상대강도 {pct(market_metric(yf, 'stock_relative_strength_60'))}"]
            },
            "cash_flow": {
                "stance": cash_flow_stance(position),
                "reasoning": f"{period_label} 영업활동현금흐름, 투자활동현금흐름, 재무활동현금흐름과 현금 순증감을 함께 확인한다.",
                "key_features": [
                    f"영업활동현금흐름 {krw_eok(position['operating_cash_flow'])}",
                    f"투자활동현금흐름 {krw_eok(position['investing_cash_flow'])}",
                    f"재무활동현금흐름 {krw_eok(position['financing_cash_flow'])}",
                    f"현금및현금성자산 순증감 {krw_eok(position['net_cash_change'])}"
                ]
            },
            "balance_sheet": {
                "stance": "자산 구성과 현금 보유 규모 확인",
                "reasoning": "재무상태표 기준 자산총계, 유동자산, 비유동자산, 현금및현금성자산을 함께 확인해 자산 구성과 단기 대응 여력을 판단한다.",
                "key_features": [
                    f"자산총계 {krw_eok(position['total_assets'])}",
                    f"유동자산 {krw_eok(position['current_assets'])}",
                    f"비유동자산 {krw_eok(position['non_current_assets'])}",
                    f"현금및현금성자산 {krw_eok(position['cash_and_cash_equivalents'])}"
                ]
            },
            "capital_structure": {
                "stance": capital_structure_stance(position),
                "reasoning": "자본총계, 부채총계, 자본비율, 부채비율을 함께 보며 자본 구조의 안정성과 레버리지 부담을 판단한다.",
                "key_features": [
                    f"자본총계 {krw_eok(position['total_equity'])}",
                    f"부채총계 {krw_eok(position['total_liabilities'])}",
                    f"자본비율 {pct1(position['equity_ratio'])}",
                    f"부채비율 {pct1(position['debt_to_equity'])}"
                ]
            },
            "debt": {
                "stance": "총부채와 유동부채 부담 확인",
                "reasoning": "총부채/총자산, 부채비율, 유동부채, 비유동부채를 함께 보며 단기·중장기 부채 부담을 분리해 판단한다.",
                "key_features": [
                    f"총부채/총자산 {pct1(position['liabilities_to_assets'])}",
                    f"부채비율 {pct1(position['debt_to_equity'])}",
                    f"유동부채 {krw_eok(position['current_liabilities'])}",
                    f"비유동부채 {krw_eok(position['non_current_liabilities'])}"
                ]
            },
            "liquidity": {
                "stance": liquidity_stance(position),
                "reasoning": "유동비율과 현금비율을 중심으로 유동자산 및 현금성자산이 유동부채를 어느 정도 커버하는지 확인한다.",
                "key_features": [
                    f"유동비율 {pct1(position['current_ratio'])}",
                    f"현금비율 {pct1(position['cash_ratio'])}",
                    f"유동자산 {krw_eok(position['current_assets'])}",
                    f"유동부채 {krw_eok(position['current_liabilities'])}"
                ]
            }
        },
        "detailed_analysis": {
            "revenue": {
                "interpretation": f"매출은 {revenue_stance}로 해석되지만, 비교 기간의 집계 기준을 함께 표시해야 한다.",
                "supporting_features": {
                    "revenue": revenue,
                    "revenue_growth": revenue_growth,
                    "period": period_label
                },
                "caution": period_caution
            },
            "margin": {
                "interpretation": f"공헌이익률은 {profitability_stance}으로 해석된다.",
                "supporting_features": {
                    "contribution_margin": contribution_margin,
                    "previous_contribution_margin": previous_contribution_margin,
                    "period": period_label
                }
            },
            "expense_efficiency": {
                "interpretation": f"판관비율은 {cost_stance}으로 해석된다.",
                "supporting_features": {
                    "sga_margin": sga_margin,
                    "previous_sga_margin": previous_sga_margin,
                    "period": period_label
                }
            },
            "eps": {
                "interpretation": f"EPS는 {eps_stance}로 해석하되 기간 기준 차이를 함께 반영한다.",
                "supporting_features": {
                    "eps": eps,
                    "previous_eps": previous_eps,
                    "period": period_label
                },
                "caution": period_caution
            },
            "risk_and_context": {
                "interpretation": "뉴스 리스크와 상대성과 혼재는 DART 기반 개선 claim의 지속성 검증 요인이다.",
                "supporting_features": {
                    "news_issues": news_issue_list,
                    "news_summary": news_summary_short,
                    "stock_excess_return_20d": market_metric(yf, "stock_excess_return_20d"),
                    "stock_relative_strength_60": market_metric(yf, "stock_relative_strength_60")
                },
                "caution": "시장 데이터와 뉴스는 primary financial evidence가 아니다."
            },
            "cash_flow": {
                "interpretation": cash_flow_stance(position),
                "supporting_features": {
                    "operating_cash_flow": position["operating_cash_flow"],
                    "investing_cash_flow": position["investing_cash_flow"],
                    "financing_cash_flow": position["financing_cash_flow"],
                    "net_cash_change": position["net_cash_change"],
                    "period": period_label
                },
                "caution": f"현금흐름표도 {period_basis} 기준이므로 연간 확정 현금창출력으로 단정하지 않는다."
            },
            "balance_sheet": {
                "interpretation": "재무상태표는 자산 구성, 현금 보유, 부채와 자본의 균형을 시점 기준으로 보여준다.",
                "supporting_features": {
                    "total_assets": position["total_assets"],
                    "current_assets": position["current_assets"],
                    "non_current_assets": position["non_current_assets"],
                    "cash_and_cash_equivalents": position["cash_and_cash_equivalents"],
                    "period_basis": "POINT_IN_TIME"
                }
            },
            "capital_structure": {
                "interpretation": capital_structure_stance(position),
                "supporting_features": {
                    "total_equity": position["total_equity"],
                    "total_liabilities": position["total_liabilities"],
                    "equity_ratio": position["equity_ratio"],
                    "debt_to_equity": position["debt_to_equity"],
                    "period_basis": "POINT_IN_TIME"
                }
            },
            "debt": {
                "interpretation": "총부채와 유동부채는 자산, 자본, 유동성 대비 부담 수준을 함께 보며 판단한다.",
                "supporting_features": {
                    "total_liabilities": position["total_liabilities"],
                    "current_liabilities": position["current_liabilities"],
                    "non_current_liabilities": position["non_current_liabilities"],
                    "liabilities_to_assets": position["liabilities_to_assets"],
                    "period_basis": "POINT_IN_TIME"
                }
            },
            "liquidity": {
                "interpretation": liquidity_stance(position),
                "supporting_features": {
                    "current_ratio": position["current_ratio"],
                    "cash_ratio": position["cash_ratio"],
                    "current_assets": position["current_assets"],
                    "current_liabilities": position["current_liabilities"],
                    "cash_and_cash_equivalents": position["cash_and_cash_equivalents"],
                    "period_basis": "POINT_IN_TIME"
                }
            }
        },
        "cross_data_reconciliation": {
            "main_analysis": {
                "summary": (
                    f"DART 메인 분석 기준으로 {period_label} 매출은 {krw_eok(revenue)}이고 공헌이익률은 {pct(contribution_margin)}, "
                    f"판관비율은 {pct(sga_margin)}, EPS는 {won(eps)}이다. 종합 신호는 {direction_ko}이나, "
                    f"{period_caution}"
                ),
                "reaction_points": [
                    {
                        "point": "매출 규모와 성장 방향",
                        "cross_analysis": (
                            f"DART 기준 {period_label} 매출은 {krw_eok(revenue)}이며, {previous_period_label} 매출은 {krw_eok(previous_revenue)}이다. "
                            f"증감률은 {pct(revenue_growth)}이고, {period_caution}"
                        ),
                        "reaction_interpretation": "Financial Analyst의 성장성 판단은 DART가 primary anchor이며, 연간 확정 성장으로 과장하지 않는 조건에서 유지 가능하다."
                    },
                    {
                        "point": "수익성과 비용 효율성",
                        "cross_analysis": (
                            f"공헌이익률은 {pct(contribution_margin)}, 판관비율은 {pct(sga_margin)}로 확인된다. "
                            f"{previous_period_label} 대비 공헌이익률은 {movement_ko(contribution_margin, previous_contribution_margin)}, "
                            f"판관비율은 {movement_ko(sga_margin, previous_sga_margin, lower_is_better=True)}으로 확인된다."
                        ),
                        "reaction_interpretation": f"마진 개선 claim은 DART 기반으로 설명 가능하지만, {period_label} 기준이라는 기간 주석이 필요하다."
                    }
                ],
                "divergences": [
                    {
                        "point": "비교 기간 기준 차이",
                        "cross_analysis": period_caution,
                        "reaction_interpretation": "메인 분석은 개선 방향을 말할 수 있으나 연간 확정 개선, 연간 성장률 확정 같은 표현은 제한해야 한다."
                    },
                    {
                        "point": "EPS 개선 단정 제한",
                        "cross_analysis": f"{period_label} EPS는 {won(eps)}이고 {previous_period_label} EPS는 {won(previous_eps)}이다. {period_caution}",
                        "reaction_interpretation": "EPS는 흑자 기조 확인에는 사용할 수 있지만 연간 EPS 개선 claim으로 확장하지 않는다."
                    }
                ]
            },
            "news_plus_dart": {
                "summary": (
                    f"DART에서는 {period_label} 기준 매출 {krw_eok(revenue)}, 공헌이익률 {pct(contribution_margin)}, "
                    f"판관비율 {pct(sga_margin)}, EPS {won(eps)}가 확인된다. News context에서는 {news_issue_summary} 등이 "
                    "핵심 이슈로 확인되며, DART 기반 재무 개선 claim의 배경 설명과 지속성 리스크를 함께 제공한다."
                ),
                "reaction_points": [
                    {
                        "point": "News 주요 사업 이슈와 DART 매출 흐름",
                        "cross_analysis": (
                            f"DART 매출은 {period_label} 기준 {krw_eok(revenue)}로 확인된다. "
                            f"뉴스에서는 {news_growth_summary} 등이 주요 사업/성장 context로 확인된다."
                        ),
                        "reaction_interpretation": "뉴스는 매출 개선 방향의 사업 배경을 보조하지만, 재무 claim의 primary evidence는 DART 수치로 제한한다."
                    },
                    {
                        "point": "News 이슈와 DART 수익성 claim 범위",
                        "cross_analysis": (
                            f"뉴스 요약은 '{news_summary_short}'로 정리된다. "
                            f"DART에서는 공헌이익률 {pct(contribution_margin)}와 판관비율 {pct(sga_margin)}가 확인된다."
                        ),
                        "reaction_interpretation": "신사업 뉴스는 향후 성장 context로만 사용하고, 현재 수익성 claim은 DART의 마진·비용 지표로만 설명한다."
                    }
                ],
                "divergences": [
                    {
                        "point": "긍정 뉴스와 DART 기간 기준 차이",
                        "cross_analysis": (
                            f"뉴스는 {news_growth_summary}를 강조하지만, DART 최신 수치는 {period_label} 기준이다."
                        ),
                        "reaction_interpretation": "뉴스 기대를 연간 확정 실적으로 확장하지 않고, YTD 기준 개선 방향으로만 제한한다."
                    },
                    {
                        "point": "News 리스크와 DART claim 지속성",
                        "cross_analysis": f"뉴스에는 {news_risk_summary}가 확인되며, DART claim은 회계 수치 기준으로만 유지한다.",
                        "reaction_interpretation": "DART 기반 개선 claim은 유지하되, 뉴스 리스크는 지속성 caution으로 반영한다."
                    }
                ]
            },
            "market_plus_dart": {
                "summary": (
                    f"DART에서는 {period_label} 매출 {krw_eok(revenue)}, 공헌이익률 {pct(contribution_margin)}, "
                    f"판관비율 {pct(sga_margin)}, EPS {won(eps)}가 확인된다. 시장 데이터에서는 주가가 20일 {pct(market_metric(yf, 'stock_return_20d'))}, "
                    f"60일 {pct(market_metric(yf, 'stock_return_60d'))} 움직였고 20일 초과수익률 {pct(market_metric(yf, 'stock_excess_return_20d'))}, "
                    f"60일 상대강도 {pct(market_metric(yf, 'stock_relative_strength_60'))}가 확인된다."
                ),
                "reaction_points": [
                    {
                        "point": "매출 및 마진 개선과 가격 반등",
                        "cross_analysis": (
                            f"DART의 매출 규모와 공헌이익률 {pct(contribution_margin)}는 재무 개선 방향을 보여준다. "
                            f"동시에 주가는 20일 {pct(market_metric(yf, 'stock_return_20d'))}, 60일 {pct(market_metric(yf, 'stock_return_60d'))} 움직였다."
                        ),
                        "reaction_interpretation": "절대 가격 반등은 재무 개선 기대와 방향상 부합하지만, 가격 데이터만으로 펀더멘털 개선을 확정하지 않는다."
                    },
                    {
                        "point": "비용 효율성 개선과 거래량 확대",
                        "cross_analysis": (
                            f"DART 판관비율은 {pct(sga_margin)}이고, 시장에서는 거래량 비율이 {ratio2(market_metric(yf, 'stock_volume_ratio_20'))}다."
                        ),
                        "reaction_interpretation": "비용 효율성 개선과 거래 활성화가 함께 나타나지만, 거래량은 관심도 지표이며 재무 수치의 직접 증거는 아니다."
                    }
                ],
                "divergences": [
                    {
                        "point": "재무 개선 방향과 상대성과 약세",
                        "cross_analysis": (
                            f"DART 재무 지표는 {direction_ko}이나 20일 초과수익률은 {pct(market_metric(yf, 'stock_excess_return_20d'))}, "
                            f"60일 상대강도는 {pct(market_metric(yf, 'stock_relative_strength_60'))}로 확인된다."
                        ),
                        "reaction_interpretation": "재무 개선 기대가 절대 가격에는 반영되었지만, 시장 대비 강한 확신으로 이어졌다고 보기는 어렵다."
                    },
                    {
                        "point": "YTD 재무 데이터와 가격 반응의 시점 차이",
                        "cross_analysis": f"DART는 {period_label} 기준 누적 또는 기간 실적이고 시장 가격은 {market_date} 기준 반응이다.",
                        "reaction_interpretation": "가격 반응은 실적뿐 아니라 뉴스, 수급, 시장지수 움직임을 함께 반영하므로 DART 수치와 1:1로 대응시키지 않는다."
                    }
                ]
            },
            "market_plus_news_plus_dart": {
                "summary": (
                    "DART 기준 3축 교차분석은 DART 메인지표, News context, Market context를 동시에 연결한다. "
                    f"DART는 {direction_ko}를 제공하고, 뉴스는 {news_issue_summary}를 제공하며, 시장 데이터는 {market_context}"
                ),
                "reaction_points": [
                    {
                        "point": "DART 매출 흐름 + News 주요 이슈 + 주가 반응",
                        "cross_analysis": (
                            f"DART 매출은 {period_label} 기준 {krw_eok(revenue)}이고, 뉴스는 {news_growth_summary}를 언급한다. "
                            f"시장에서는 20일 주가수익률 {pct(market_metric(yf, 'stock_return_20d'))}가 확인된다."
                        ),
                        "reaction_interpretation": "세 축은 모두 성장 기대라는 방향에서는 정합적이나, DART가 primary anchor이고 뉴스·시장 데이터는 보조 검증으로 제한한다."
                    },
                    {
                        "point": "마진 개선 + 실적 기대 뉴스 + 거래량 확대",
                        "cross_analysis": (
                            f"DART 공헌이익률은 {pct(contribution_margin)}이고 판관비율은 {pct(sga_margin)}다. "
                            f"뉴스 주요 이슈는 {news_issue_summary}이며, 시장 거래량 비율은 {ratio2(market_metric(yf, 'stock_volume_ratio_20'))}다."
                        ),
                        "reaction_interpretation": "수익성 개선 기대가 시장 관심을 높였을 가능성은 있으나, 거래량 확대 자체를 재무 개선 증거로 사용하지 않는다."
                    }
                ],
                "divergences": [
                    {
                        "point": "성장·실적 뉴스와 상대성과 부진의 동시 존재",
                        "cross_analysis": (
                            f"DART와 뉴스는 {direction_ko} 및 {news_issue_summary}를 제시하지만, 시장 대비 20일 초과수익률은 {pct(market_metric(yf, 'stock_excess_return_20d'))}로 확인된다."
                        ),
                        "reaction_interpretation": "DART·News·Market 교차분석에서는 긍정 모멘텀과 상대성과 약세를 모두 반영해 claim 가중치를 보수적으로 둔다."
                    },
                    {
                        "point": "News 리스크와 재무 claim 지속성",
                        "cross_analysis": f"뉴스에는 {news_risk_summary}가 확인되며, DART claim은 회계 수치 기준으로만 유지한다.",
                        "reaction_interpretation": "DART·뉴스·시장 축을 동시에 보더라도 개선 claim은 유지하되 지속성 리스크를 별도 caution으로 남긴다."
                    }
                ]
            }
        },
        "sy_handoff": {
            "financial_claims": claims,
            "key_evidence": evidence,
            "reconciliation_flags": [
                {
                    "flag_ko": period_caution,
                    "severity": "high",
                    "action_for_sy": "use_with_caution"
                },
                {
                    "flag_ko": "주가 절대 상승은 긍정적이나 상대성과 혼재로 가격 확인 강도는 낮출 것",
                    "severity": "medium",
                    "action_for_sy": "use_with_caution"
                },
                {
                    "flag_ko": f"News 주요 리스크/주의 이슈({news_risk_summary})는 보수적으로 검증할 것",
                    "severity": "medium",
                    "action_for_sy": "use_with_caution"
                }
            ]
        }
    }


def append_message(state: FinancialAnalystGraphState, node: str, role: str, content: str) -> None:
    state.setdefault("transcript", []).append({"node": node, "role": role, "content": content})


def report_claims(report: Dict[str, Any]) -> List[Dict[str, Any]]:
    return report.get("sy_handoff", {}).get("financial_claims", report.get("financial_claims", []))


def report_evidence(report: Dict[str, Any]) -> List[Dict[str, Any]]:
    return report.get("sy_handoff", {}).get("key_evidence", report.get("key_evidence", []))


def input_state_node(state: FinancialAnalystGraphState) -> FinancialAnalystGraphState:
    manifest = load_json(state["manifest_path"])
    paths = manifest["input_paths"]
    dart_master_path = resolve_dart_master_path(paths)
    inputs = {
        "dart_main": load_input_file(paths["dart_main"]),
        "dart_master": load_input_file(str(dart_master_path)) if dart_master_path else {},
        "yfinance_market_summary": load_input_file(paths["yfinance_market_summary"]),
        "news_llm_period_summaries": load_input_file(paths["news_llm_period_summaries"]),
    }
    state["manifest"] = manifest
    state["inputs"] = inputs
    append_message(state, "Input State", "system", "입력 manifest와 DART/Y-Finance/News 데이터를 로드했다.")
    return state


def financial_agent_execution_node(state: FinancialAnalystGraphState) -> FinancialAnalystGraphState:
    report = build_financial_analyst_output(state["manifest"], state["inputs"])
    cross_data_reconciliation = report.pop("cross_data_reconciliation")
    state["financial_analysis_output"] = report
    state["pending_cross_data_reconciliation"] = cross_data_reconciliation
    fallback = "DART anchor를 기준으로 financial statement view, detailed analysis, SY handoff claim/evidence를 생성했다."
    prompt = (
        "너는 Financial Analyst Agent다. DART 기반 재무 분석을 수행한 직후의 짧은 상태 메시지를 한국어 한 문장으로 작성하라. "
        "매수/매도/보유 판단은 쓰지 말고, 아직 SY 검증은 수행하지 않았다고 명확히 하라.\n\n"
        f"financial_claims={json.dumps(report_claims(report), ensure_ascii=False)}"
    )
    message = llm_generate(state, "Financial Agent Execution Node", prompt, fallback)
    append_message(
        state,
        "Financial Agent Execution Node",
        "analyst",
        message,
    )
    return state


def cross_data_reconciliation_node(state: FinancialAnalystGraphState) -> FinancialAnalystGraphState:
    reconciliation = state["pending_cross_data_reconciliation"]
    state["cross_data_reconciliation"] = reconciliation
    state["cross_analysis_questions"] = {
        section: question
        for section, question in CROSS_ANALYSIS_QUESTIONS.items()
        if section in reconciliation
    }
    fallback = "News와 Y-Finance를 DART 재무 분석의 보조 context로만 교차 검증했다."
    prompt = (
        "너는 Financial Analyst Agent다. cross_data_reconciliation 확정 상태 메시지를 한국어 한 문장으로 작성하라. "
        "통일된 교차분석 질문으로 News/Y-Finance가 primary financial evidence가 아니라 교차 검증 context임을 포함하라.\n\n"
        f"cross_analysis_questions={json.dumps(state['cross_analysis_questions'], ensure_ascii=False)}\n"
        f"cross_data_reconciliation={json.dumps(reconciliation, ensure_ascii=False)}"
    )
    message = llm_generate(state, "Cross Data Reconciliation Node", prompt, fallback)
    append_message(state, "Cross Data Reconciliation Node", "analyst", message)
    return state


def financial_report_output_node(state: FinancialAnalystGraphState) -> FinancialAnalystGraphState:
    output = dict(state["financial_analysis_output"])
    output["cross_data_reconciliation"] = state["cross_data_reconciliation"]
    state["report_output"] = output
    required = [
        "agent_name",
        "role",
        "target_company",
        "ticker",
        "corp_code",
        "as_of_date",
        "main_view",
        "financial_statement_view",
        "detailed_analysis",
        "cross_data_reconciliation",
        "sy_handoff",
    ]
    missing = [key for key in required if key not in output]
    state["schema_validation"] = {
        "status": "pass" if not missing else "fail",
        "missing_keys": missing,
        "claim_count": len(report_claims(output)),
        "claim_count_limit": 10,
    }
    append_message(state, "Financial Report Output Node", "system", f"리포트 출력 생성 완료: {state['schema_validation']['status']}")
    return state


def build_graph():
    graph = StateGraph(FinancialAnalystGraphState)
    graph.add_node("input_state", input_state_node)
    graph.add_node("financial_agent_execution", financial_agent_execution_node)
    graph.add_node("cross_data_reconciliation", cross_data_reconciliation_node)
    graph.add_node("financial_report_output", financial_report_output_node)

    graph.add_edge(START, "input_state")
    graph.add_edge("input_state", "financial_agent_execution")
    graph.add_edge("financial_agent_execution", "cross_data_reconciliation")
    graph.add_edge("cross_data_reconciliation", "financial_report_output")
    graph.add_edge("financial_report_output", END)
    return graph.compile()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--manifest", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--trace-output")
    parser.add_argument("--env-file", default=str(DEFAULT_ENV_FILE))
    parser.add_argument("--use-llm", action="store_true")
    parser.add_argument("--llm-provider", default="openai", choices=["auto", "none", "openai"])
    parser.add_argument("--llm-model", default="auto")
    parser.add_argument("--llm-timeout", type=int, default=60)
    args = parser.parse_args()

    load_env_file(args.env_file)
    provider = resolve_llm_provider(args.llm_provider)
    llm_model = resolve_llm_model(provider, args.llm_model)
    app = build_graph()
    final_state = app.invoke(
        {
            "manifest_path": args.manifest,
            "env_file": args.env_file,
            "use_llm": args.use_llm,
            "llm_provider": provider,
            "llm_model": llm_model,
            "llm_timeout": args.llm_timeout,
            "transcript": [],
            "llm_calls": [],
        }
    )

    output_path = Path(args.output)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(final_state["report_output"], ensure_ascii=False, indent=2) + "\n")

    if args.trace_output:
        trace_path = Path(args.trace_output)
        trace_path.parent.mkdir(parents=True, exist_ok=True)
        trace = {
            "fixed_node_flow": [
                "Input State",
                "Financial Agent Execution Node",
                "Cross Data Reconciliation Node",
                "Financial Report Output Node",
            ],
            "schema_validation": final_state["schema_validation"],
            "cross_analysis_questions": final_state.get("cross_analysis_questions", {}),
            "llm": {
                "enabled": args.use_llm,
                "provider": provider,
                "model": llm_model if args.use_llm else None,
                "env_file": args.env_file,
                "api_key_loaded": bool(os.getenv("OPENAI_API_KEY")) if provider == "openai" else False,
            },
            "llm_usage_summary": summarize_llm_usage(final_state.get("llm_calls", [])),
            "llm_calls": final_state.get("llm_calls", []),
            "transcript": final_state["transcript"],
        }
        trace_path.write_text(json.dumps(trace, ensure_ascii=False, indent=2) + "\n")

    print(output_path)


if __name__ == "__main__":
    main()
