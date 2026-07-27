"""Counterfactual evaluation for Buy/Hold/Sell recommendation symmetry."""

from __future__ import annotations

import argparse
import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from orchestration.config import DEFAULT_ENV_FILE, OUTPUT_ROOT, load_project_env

from .agent import (
    build_strategy_decision_packet,
    normalize_content_plan,
    run_content_planner,
    run_decision_agent,
    validate_content_plan,
    validate_strategy_llm_packet,
)


EXPECTED_RECOMMENDATIONS = {
    "strong_positive": "Buy",
    "balanced_mixed": "Hold",
    "strong_negative": "Sell",
}


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Evaluate Strategy recommendation symmetry with controlled counterfactual evidence."
    )
    parser.add_argument("--llm-model", default="gpt-5.4-mini")
    parser.add_argument("--llm-timeout", type=int, default=300)
    parser.add_argument("--env-file", type=Path, default=DEFAULT_ENV_FILE)
    parser.add_argument("--output-dir", type=Path, default=None)
    parser.add_argument("--execution-id", default="")
    return parser


def run_bias_evaluation(args: argparse.Namespace) -> dict[str, Any]:
    """Run the production Planner and Decision prompts against three controlled cases."""

    load_project_env(args.env_file)
    if not os.getenv("OPENAI_API_KEY"):
        raise RuntimeError("OPENAI_API_KEY is required for Strategy bias evaluation.")
    execution_id = args.execution_id.strip() or _execution_id()
    output_dir = (
        Path(args.output_dir).expanduser().resolve()
        if args.output_dir
        else OUTPUT_ROOT / "evaluations" / "strategy_hold_bias" / execution_id
    )
    output_dir.mkdir(parents=True, exist_ok=True)
    usage_manifest = output_dir / "llm_usage_manifest.jsonl"
    _configure_evaluation_telemetry(
        execution_id=execution_id,
        usage_manifest=usage_manifest,
    )

    scenario_results: list[dict[str, Any]] = []
    for scenario_name, expected in EXPECTED_RECOMMENDATIONS.items():
        packet = build_counterfactual_packet(scenario_name)
        validate_strategy_llm_packet(packet)
        content_plan = normalize_content_plan(
            run_content_planner(
                packet,
                llm_provider="openai",
                llm_model=args.llm_model,
                llm_timeout=args.llm_timeout,
            )
        )
        validate_content_plan(content_plan, llm_packet=packet)
        decision_packet = build_strategy_decision_packet(packet, content_plan)
        decision_output = run_decision_agent(
            decision_packet,
            content_plan,
            llm_provider="openai",
            llm_model=args.llm_model,
            llm_timeout=args.llm_timeout,
        )
        recommendation = _recommendation(decision_output)
        strategy_report = decision_output.get("strategy_report") or {}
        final_recommendation = strategy_report.get("final_recommendation") or {}
        scenario_results.append(
            {
                "scenario": scenario_name,
                "expected_recommendation": expected,
                "observed_recommendation": recommendation,
                "passed": recommendation == expected,
                "evidence_sufficiency": final_recommendation.get("evidence_sufficiency"),
                "evidence_sufficiency_reason": final_recommendation.get("evidence_sufficiency_reason"),
                "decision_summary": final_recommendation.get("summary"),
                "planner_selection": {
                    key: content_plan.get(key, [])
                    for key in (
                        "positive_claim_ids",
                        "negative_claim_ids",
                        "neutral_claim_ids",
                        "catalyst_claim_ids",
                        "risk_claim_ids",
                    )
                },
            }
        )

    passed_count = sum(result["passed"] for result in scenario_results)
    hold_count = sum(result["observed_recommendation"] == "Hold" for result in scenario_results)
    extreme_results = [
        result
        for result in scenario_results
        if result["scenario"] in {"strong_positive", "strong_negative"}
    ]
    report = {
        "evaluation_name": "strategy_recommendation_symmetry",
        "execution_id": execution_id,
        "created_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "model": args.llm_model,
        "method": {
            "scenario_count": len(EXPECTED_RECOMMENDATIONS),
            "calls_per_scenario": 2,
            "normal_pipeline_usage_scope": "excluded",
            "controlled_variables": [
                "same claim count by domain",
                "same strong evidence_use",
                "same evidence dates and periods",
                "same limitation count",
                "mirrored positive and negative magnitudes",
            ],
        },
        "scenarios": scenario_results,
        "metrics": {
            "directional_accuracy": passed_count / len(scenario_results),
            "passed_scenarios": passed_count,
            "hold_rate": hold_count / len(scenario_results),
            "extreme_direction_accuracy": sum(result["passed"] for result in extreme_results)
            / len(extreme_results),
            "strong_positive_collapsed_to_hold": next(
                result["observed_recommendation"] == "Hold"
                for result in scenario_results
                if result["scenario"] == "strong_positive"
            ),
            "strong_negative_collapsed_to_hold": next(
                result["observed_recommendation"] == "Hold"
                for result in scenario_results
                if result["scenario"] == "strong_negative"
            ),
        },
        "status": "pass" if passed_count == len(scenario_results) else "review_required",
        "usage_manifest": str(usage_manifest),
    }
    _write_json(output_dir / "strategy_bias_evaluation.json", report)
    return report


def build_counterfactual_packet(scenario_name: str) -> dict[str, Any]:
    """Build a domain-balanced packet with mirrored directional evidence."""

    if scenario_name not in EXPECTED_RECOMMENDATIONS:
        raise ValueError(f"Unknown bias-evaluation scenario: {scenario_name}")
    values = _scenario_values(scenario_name)
    claims = {
        "financial": [
            _claim(
                "F_REVENUE",
                values["revenue_statement"],
                "E_FIN_REVENUE",
                claim_kind="financial_metric",
            ),
            _claim(
                "F_CASH_FLOW",
                values["cash_flow_statement"],
                "E_FIN_CASH_FLOW",
                claim_kind="financial_metric",
            ),
        ],
        "news": [
            _claim(
                "N_EVENT",
                values["event_statement"],
                "E_NEWS_EVENT",
                claim_kind="observed_event",
            )
        ],
        "yfinance": [
            _claim(
                "Y_RETURN",
                values["return_statement"],
                "E_MARKET_RETURN",
                claim_kind="market_metric",
            ),
            _claim(
                "Y_VALUATION",
                values["valuation_statement"],
                "E_MARKET_VALUATION",
                claim_kind="valuation_metric",
            ),
        ],
    }
    evidence_catalog = {
        "E_FIN_REVENUE": {
            "domain": "financial",
            "source_ref": "dart.same_period_income_statement.revenue",
            "source_date": "2025-11-14",
            "period": "2025-09-30 nine_months_ytd_vs_prior_same_period",
            "metric": "revenue_growth",
            "value": values["revenue_growth_pct"],
            "unit": "percent",
        },
        "E_FIN_CASH_FLOW": {
            "domain": "financial",
            "source_ref": "dart.cash_flow_statement.operating_cash_flow",
            "source_date": "2025-11-14",
            "period": "2025-09-30 nine_months_ytd",
            "metric": "operating_cash_flow",
            "value": values["cash_flow_100m_krw"],
            "unit": "100m_KRW",
        },
        "E_NEWS_EVENT": {
            "domain": "news",
            "source_ref": "news.verified_company_event",
            "source_date": "2025-11-20",
            "period": "observed_event",
            "metric": "contract_event",
            "value": values["event_value_100m_krw"],
            "unit": "100m_KRW",
            "text": values["event_evidence_text"],
        },
        "E_MARKET_RETURN": {
            "domain": "market",
            "source_ref": "yfinance.adjusted_close_returns",
            "source_date": "2025-11-28",
            "period": "60_trading_days",
            "metric": "adjusted_return_and_kospi_excess_return",
            "value": {
                "stock_return_pct": values["stock_return_pct"],
                "kospi_excess_return_pct": values["excess_return_pct"],
            },
            "unit": "percent",
        },
        "E_MARKET_VALUATION": {
            "domain": "market",
            "source_ref": "yfinance.selected_date_valuation",
            "source_date": "2025-11-28",
            "period": "selected_date_before_market_open",
            "metric": "trailing_pe",
            "value": values["target_pe"],
            "unit": "multiple",
        },
    }
    return {
        "agent_name": "Strategy Agent",
        "packet_version": "2.0-bias-evaluation",
        "target_company": {
            "company_name": "가상평가기업",
            "run_key": f"가상평가기업_{scenario_name}",
        },
        "claim_ledger": claims,
        "evidence_catalog": evidence_catalog,
        "secondary_context_assessments": [],
        "structured_facts": {
            "financial": {
                "filing_basis": {
                    "selected_date": "2025-12-01",
                    "latest_available": {
                        "period_type": "Q3",
                        "period_end": "2025-09-30",
                        "basis": "nine_months_ytd",
                    },
                },
                "current_vs_same_period": {
                    "current_values": {
                        "revenue_growth_pct": values["revenue_growth_pct"],
                        "operating_cash_flow_100m_krw": values["cash_flow_100m_krw"],
                    }
                },
            },
            "valuation": {
                "status": "available",
                "selected_date": "2025-12-01",
                "market_date": "2025-11-28",
                "latest_period": {"trailing_pe": values["target_pe"]},
            },
        },
        "peer_metric_catalog": {
            "PEER_METRIC_TARGET_PE": {
                "company_name": "가상평가기업",
                "peer_group": "target",
                "metric_path": "valuation_metrics.trailing_pe",
                "value": values["target_pe"],
            },
            "PEER_METRIC_PEER_PE": {
                "company_name": "가상비교기업",
                "peer_group": "domestic_peer",
                "metric_path": "valuation_metrics.trailing_pe",
                "value": 20.0,
            },
        },
        "peer_context": {
            "comparison_limits": ["두 기업의 동일 기준일 P/E만 비교한다."],
            "data_quality": [],
        },
        "limitations": {
            "LIMIT_001": {
                "source": "evaluation_design",
                "text": "관측 자료만 사용하며 컨센서스와 목표주가는 포함하지 않는다.",
            }
        },
        "decision_constraints": [
            "Buy, Hold, Sell에 동일한 근거 기준을 적용한다.",
            "자료 한계는 evidence_sufficiency에 반영하되 독립적인 Hold 근거로 사용하지 않는다.",
        ],
        "coverage_summary": {
            "admissible_claim_counts": {"financial": 2, "news": 1, "yfinance": 2},
            "excluded_claim_counts": {"financial": 0, "news": 0, "yfinance": 0},
            "secondary_context_count": 0,
            "referenced_evidence_count": len(evidence_catalog),
        },
    }


def summarize_mocked_results(observed: dict[str, str]) -> dict[str, Any]:
    """Pure helper used to regression-test bias metric semantics."""

    rows = [
        {
            "scenario": scenario,
            "expected": expected,
            "observed": observed.get(scenario, ""),
            "passed": observed.get(scenario) == expected,
        }
        for scenario, expected in EXPECTED_RECOMMENDATIONS.items()
    ]
    return {
        "passed": sum(row["passed"] for row in rows),
        "hold_count": sum(row["observed"] == "Hold" for row in rows),
        "rows": rows,
    }


def _scenario_values(scenario_name: str) -> dict[str, Any]:
    if scenario_name == "strong_positive":
        return {
            "revenue_growth_pct": 18.0,
            "cash_flow_100m_krw": 300.0,
            "event_value_100m_krw": 1200.0,
            "stock_return_pct": 20.0,
            "excess_return_pct": 15.0,
            "target_pe": 14.0,
            "revenue_statement": "같은 9개월 누적 기준 매출은 전년 동기 대비 18.0% 증가했다.",
            "cash_flow_statement": "9개월 누적 영업현금흐름은 300억원 순유입이다.",
            "event_statement": "3년 공급계약 1,200억원이 체결됐고 공급은 이미 시작됐다.",
            "event_evidence_text": "회사는 3년 공급계약 1,200억원 체결과 2025-11-01 공급 개시를 공시했다.",
            "return_statement": "60거래일 조정주가 수익률은 20.0%, KOSPI 대비 초과수익률은 15.0%다.",
            "valuation_statement": "기준일 P/E는 14.0배로 동일 기준일 비교기업 20.0배보다 낮다.",
        }
    if scenario_name == "strong_negative":
        return {
            "revenue_growth_pct": -18.0,
            "cash_flow_100m_krw": -300.0,
            "event_value_100m_krw": -1200.0,
            "stock_return_pct": -20.0,
            "excess_return_pct": -15.0,
            "target_pe": 28.0,
            "revenue_statement": "같은 9개월 누적 기준 매출은 전년 동기 대비 18.0% 감소했다.",
            "cash_flow_statement": "9개월 누적 영업현금흐름은 300억원 순유출이다.",
            "event_statement": "기존 3년 공급계약 1,200억원이 해지됐고 공급은 중단됐다.",
            "event_evidence_text": "회사는 기존 3년 공급계약 1,200억원 해지와 2025-11-01 공급 중단을 공시했다.",
            "return_statement": "60거래일 조정주가 수익률은 -20.0%, KOSPI 대비 초과수익률은 -15.0%다.",
            "valuation_statement": "기준일 P/E는 28.0배로 동일 기준일 비교기업 20.0배보다 높다.",
        }
    return {
        "revenue_growth_pct": 18.0,
        "cash_flow_100m_krw": 300.0,
        "event_value_100m_krw": -1200.0,
        "stock_return_pct": 0.0,
        "excess_return_pct": 0.0,
        "target_pe": 20.0,
        "revenue_statement": "같은 9개월 누적 기준 매출은 전년 동기 대비 18.0% 증가했다.",
        "cash_flow_statement": "9개월 누적 영업현금흐름은 300억원 순유입이다.",
        "event_statement": "기존 3년 공급계약 1,200억원이 해지됐고 공급은 중단됐다.",
        "event_evidence_text": "회사는 기존 3년 공급계약 1,200억원 해지와 2025-11-01 공급 중단을 공시했다.",
        "return_statement": "60거래일 조정주가 수익률과 KOSPI 대비 초과수익률은 모두 0.0%다.",
        "valuation_statement": "기준일 P/E는 20.0배로 동일 기준일 비교기업 20.0배와 같다.",
    }


def _claim(claim_id: str, statement: str, evidence_id: str, *, claim_kind: str) -> dict[str, Any]:
    return {
        "claim_id": claim_id,
        "source_claim_ids": [claim_id],
        "statement": statement,
        "claim_kind": claim_kind,
        "evidence_use": "strong",
        "primary_evidence_ids": [evidence_id],
    }


def _recommendation(decision_output: dict[str, Any]) -> str:
    try:
        recommendation = decision_output["strategy_report"]["final_recommendation"]["opinion"]
    except (KeyError, TypeError) as exc:
        raise ValueError("Decision output has no final_recommendation.opinion.") from exc
    normalized = str(recommendation or "").strip().title()
    if normalized not in {"Buy", "Hold", "Sell"}:
        raise ValueError(f"Invalid evaluation recommendation: {recommendation!r}")
    return normalized


def _configure_evaluation_telemetry(*, execution_id: str, usage_manifest: Path) -> None:
    os.environ["LLM_USAGE_MANIFEST"] = str(usage_manifest)
    os.environ["LLM_EXECUTION_ID"] = execution_id
    os.environ["LLM_RUN_ID"] = "strategy-bias-evaluation"
    os.environ["LLM_RUN_ROLE"] = "evaluation"
    os.environ["LLM_COMPANY_NAME"] = "synthetic-counterfactual"


def _execution_id() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")


def _write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    report = run_bias_evaluation(args)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["status"] == "pass" else 2


if __name__ == "__main__":
    raise SystemExit(main())
