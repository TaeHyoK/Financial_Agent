from __future__ import annotations

import json

from Agent_Team.Competitor_Agent.comparison_agent import (
    OUTPUT_VERSION,
    build_comparison_context,
    build_comparison_report,
    comparison_response_format,
    run_comparison_agent,
)


def _reports():
    financial = {
        "main_view": {"summary": "매출이 증가했다."},
        "financial_statement_view": {"cash_flow": "현금흐름은 양호하다."},
        "detailed_analysis": {"revenue": {"interpretation": "증가"}},
        "secondary_context_assessment": [
            {"statement": "시장 흐름은 혼재됐다.", "primary_evidence_ids": ["E001"]}
        ],
    }
    news = {
        "output": {
            "analysis_blocks": {"news_only": {"summary": {"claim": "수주가 발표됐다."}}},
            "secondary_context_assessment": [],
        }
    }
    market = {
        "main_view": {"summary": "상대수익률이 양호했다."},
        "time_horizon_view": {"short_term": {"stance": "상승"}},
        "secondary_context_assessment": [],
    }
    dataset = {
        "metrics": [
            {
                "company_name": "대상기업",
                "peer_group": "target",
                "as_of_date": "2025-10-30",
                "financial_metrics": {"operating_margin_pct": 10.0},
                "market_metrics": {"stock_return_20d_pct": 5.0},
                "valuation_metrics": {"trailing_pe": 12.0},
                "data_quality": {"missing_fields": []},
            },
            {
                "company_name": "비교기업",
                "peer_group": "domestic_peer",
                "as_of_date": "2025-10-30",
                "financial_metrics": {"operating_margin_pct": 7.0},
                "market_metrics": {"stock_return_20d_pct": 2.0},
                "valuation_metrics": {"trailing_pe": 15.0},
                "data_quality": {"missing_fields": []},
            },
        ],
        "comparison_limits": ["선정 비교기업 한 곳과의 비교다."],
    }
    return financial, news, market, dataset


def _context(*, included_domains=("financial", "news", "yfinance")):
    financial, news, market, dataset = _reports()
    return build_comparison_context(
        target_company_name="대상기업",
        peer_company_name="비교기업",
        target_financial=financial,
        target_news=news,
        target_yfinance=market,
        peer_financial=financial,
        peer_news=news,
        peer_yfinance=market,
        pairwise_dataset=dataset,
        included_domains=included_domains,
    )


def _output():
    return {
        "comparison_version": OUTPUT_VERSION,
        "comparison_brief": "대상기업은 수익성에서 상대적으로 앞선다.",
        "comparison_brief_basis": [
            {
                "card_key": "pair.financial.metrics",
                "usage_reason": "요약의 수익성 차이를 뒷받침한다.",
            }
        ],
        "comparison_points": [
            {
                "topic": "수익성",
                "assessment": "target_relative_strength",
                "finding": "대상기업의 영업이익률이 비교기업보다 높다.",
                "target_implication": "수익성 측면의 상대적 우위가 확인된다.",
                "basis": [
                    {
                        "card_key": "pair.financial.metrics",
                        "usage_reason": "동일 기준 영업이익률을 비교하기 위해 사용했다.",
                    }
                ],
            }
        ],
        "comparison_limitations": ["선정 비교기업 한 곳과의 비교다."],
    }


def test_context_preserves_both_company_handoffs_and_pair_metrics():
    context = _context()

    assert len(context["basis_cards"]) == 9
    assert "target.news.analysis" in context["basis_cards"]
    assert "peer.news.analysis" in context["basis_cards"]
    assert "pair.valuation.metrics" in context["basis_cards"]
    assert "primary_evidence_ids" not in json.dumps(context, ensure_ascii=False)
    schema = comparison_response_format(context)["json_schema"]["schema"]
    enum = schema["properties"]["comparison_points"]["items"]["properties"]["basis"][
        "items"
    ]["properties"]["card_key"]["enum"]
    assert set(enum) == set(context["basis_cards"])


def test_context_respects_domain_ablation():
    context = _context(included_domains=("news",))

    assert set(context["basis_cards"]) == {
        "target.news.analysis",
        "peer.news.analysis",
    }


def test_report_records_selected_basis_observation():
    context = _context()
    report = build_comparison_report(
        _output(),
        context=context,
        source_paths={"pairwise_dataset": __file__},
    )

    selected = report["selected_basis_cards"]
    assert [item["card_key"] for item in selected] == ["pair.financial.metrics"]
    assert selected[0]["usage_reasons"] == [
        "요약의 수익성 차이를 뒷받침한다.",
        "동일 기준 영업이익률을 비교하기 위해 사용했다.",
    ]
    assert selected[0]["observation"]["companies"][0]["company_name"] == "대상기업"


def test_run_comparison_agent_uses_one_cached_llm_output(tmp_path, monkeypatch):
    financial, news, market, dataset = _reports()
    inputs = {}
    for name, payload in {
        "target_financial": financial,
        "target_news": news,
        "target_market": market,
        "peer_financial": financial,
        "peer_news": news,
        "peer_market": market,
        "pair": dataset,
    }.items():
        path = tmp_path / f"{name}.json"
        path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        inputs[name] = path
    calls = []

    def fake_call(**_kwargs):
        calls.append(1)
        return _output()

    monkeypatch.setattr(
        "Agent_Team.Competitor_Agent.comparison_agent.call_comparison_llm",
        fake_call,
    )
    kwargs = {
        "target_company_name": "대상기업",
        "peer_company_name": "비교기업",
        "target_financial_path": inputs["target_financial"],
        "target_news_path": inputs["target_news"],
        "target_yfinance_path": inputs["target_market"],
        "peer_financial_path": inputs["peer_financial"],
        "peer_news_path": inputs["peer_news"],
        "peer_yfinance_path": inputs["peer_market"],
        "pairwise_dataset_path": inputs["pair"],
        "output_dir": tmp_path / "out",
        "env_file": None,
    }

    first = run_comparison_agent(**kwargs)
    second = run_comparison_agent(**kwargs)

    assert len(calls) == 1
    assert first.report_json == second.report_json
    assert json.loads(first.report_json.read_text(encoding="utf-8"))["comparison_version"] == OUTPUT_VERSION
