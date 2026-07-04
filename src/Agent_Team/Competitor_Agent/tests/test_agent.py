"""Tests for competitor report aggregation."""

from __future__ import annotations

import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from Agent_Team.Competitor_Agent.agent import (
    RunIdentity,
    discover_competitor_identities,
    generate_competitor_report,
)


class CompetitorAgentTests(unittest.TestCase):
    def test_generate_competitor_report_writes_one_company_report(self) -> None:
        with TemporaryDirectory() as tmp:
            output_root = Path(tmp)
            run_key = "경쟁기업A_20251031"
            _write_source_reports(output_root, run_key, company_name="경쟁기업A")

            target = RunIdentity(run_key="타깃기업_20251031", company_name="타깃기업", selected_date="20251031")
            competitor = RunIdentity(run_key=run_key, company_name="경쟁기업A", selected_date="20251031")
            with patch("Agent_Team.Competitor_Agent.agent.call_openai", return_value=_llm_response()):
                paths = generate_competitor_report(
                    target=target,
                    competitors=[competitor],
                    output_root=output_root,
                    llm_provider="openai",
                    llm_model="test-model",
                )

            self.assertEqual(len(paths), 1)
            self.assertEqual(paths[0].run_key, run_key)
            self.assertTrue(paths[0].json.exists())
            self.assertTrue(paths[0].markdown.exists())
            self.assertEqual(paths[0].json.parent.name, run_key)
            payload = json.loads(paths[0].json.read_text(encoding="utf-8"))
            self.assertEqual(payload["agent_name"], "Competitor Agent")
            self.assertTrue(payload["llm_synthesis"]["requested"])
            self.assertTrue(payload["llm_synthesis"]["used"])
            self.assertEqual(payload["company"]["company_name"], "경쟁기업A")
            self.assertTrue(payload["source_reports"]["news"]["available"])
            self.assertTrue(payload["source_reports"]["dart"]["available"])
            self.assertTrue(payload["source_reports"]["yfinance"]["available"])
            self.assertIn("LLM 종합 요약", payload["summary"])
            self.assertIn("PICC", json.dumps(payload["strengths"], ensure_ascii=False))
            self.assertIn("EPS", json.dumps(payload["risks"], ensure_ascii=False))
            self.assertNotIn("_llm_source_reports", payload)
            self.assertNotIn("competitors", payload)

            markdown = paths[0].markdown.read_text(encoding="utf-8")
            self.assertIn("Company: 경쟁기업A", markdown)
            self.assertIn("## Strengths", markdown)
            self.assertIn("## Risks", markdown)

    def test_discovery_excludes_target_and_requires_complete_sources_by_default(self) -> None:
        with TemporaryDirectory() as tmp:
            output_root = Path(tmp)
            _write_source_reports(output_root, "타깃기업_20251031", company_name="타깃기업")
            _write_source_reports(output_root, "경쟁기업A_20251031", company_name="경쟁기업A")
            _write_json(
                output_root / "News" / "부분데이터기업_20251031" / "final_report.json",
                _news_report("부분데이터기업"),
            )

            target = RunIdentity(run_key="타깃기업_20251031", company_name="타깃기업", selected_date="20251031")
            identities = discover_competitor_identities(output_root=output_root, target=target)

            self.assertEqual([identity.run_key for identity in identities], ["경쟁기업A_20251031"])


def _write_source_reports(output_root: Path, run_key: str, *, company_name: str) -> None:
    _write_json(output_root / "News" / run_key / "final_report.json", _news_report(company_name))
    _write_json(output_root / "Financial" / run_key / "final_report.json", _dart_report(company_name))
    _write_json(output_root / "Y_Finance" / run_key / "final_report.json", _yfinance_report(company_name))


def _write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")


def _llm_response() -> dict:
    return {
        "text": json.dumps(
            {
                "run_key": "경쟁기업A_20251031",
                "company_name": "경쟁기업A",
                "summary": "LLM 종합 요약: 뉴스, DART, YFinance를 모두 반영했다.",
                "strengths": ["PICC 계약 기반 사업 확장"],
                "risks": ["EPS -62원에 따른 수익성 부담"],
            },
            ensure_ascii=False,
        ),
        "usage": {"total_tokens": 123},
    }


def _news_report(company_name: str) -> dict:
    return {
        "output": {
            "target_entity": {
                "company_name": company_name,
                "ticker": "000000.KQ",
                "corp_code": "00000000",
                "as_of_date": "2025-10-31",
            },
            "analysis_blocks": {
                "news_only": {
                    "summary": f"{company_name}는 PICC 계약과 로봇 워크숍 뉴스가 확인된다.",
                    "positive_signals": ["키말과 PICC 합작개발 및 생산 계약"],
                    "negative_signals": ["동종 섹터 내 주가 변동성"],
                    "key_risks": ["경쟁 심화에 따른 시장 지위 변동"],
                    "uncertainties": ["자회사 사업 확장성 미확정"],
                },
                "news_plus_financial_plus_market": {
                    "summary": "뉴스와 주가 반응은 긍정적이나 재무 수익성은 확인이 필요하다.",
                    "integrated_risks": ["뉴스와 EPS 사이 괴리"],
                },
            },
        }
    }


def _dart_report(company_name: str) -> dict:
    return {
        "target_company": company_name,
        "ticker": "000000.KQ",
        "corp_code": "00000000",
        "as_of_date": "2025-10-31",
        "main_view": {
            "summary": f"{company_name}는 DART 기준 EPS -62원이 확인된다.",
            "direction": "mixed",
            "primary_basis": ["영업활동현금흐름 26억원", "부채비율 51.4%"],
            "main_cautions": ["EPS는 마이너스이며 기간 기준 차이에 주의"],
        },
        "financial_statement_view": {
            "capital_structure": {
                "stance": "자본 비중이 높고 부채 부담은 제한적인 구조",
                "reasoning": "자본비율과 부채비율을 함께 확인한다.",
                "key_features": ["부채비율 51.4%"],
            },
            "eps": {
                "stance": "적자 또는 EPS 부담",
                "reasoning": "2025 Q3 YTD EPS는 -62원이다.",
                "key_features": ["EPS -62원"],
            },
        },
    }


def _yfinance_report(company_name: str) -> dict:
    return {
        "target_company": company_name,
        "ticker": "000000.KQ",
        "as_of_date": "2025-10-31",
        "main_view": {
            "summary": f"{company_name}는 최근 20일 주가 상승세가 확인된다.",
            "direction": "상승 우위",
            "primary_basis": ["20일 주가 수익률 22.6%", "거래량 증가"],
        },
        "time_horizon_view": {
            "short_term": {
                "stance": "긍정적",
                "reasoning": "최근 수익률과 거래량이 개선됐다.",
                "key_features": ["20일 주가 수익률 22.6%"],
            },
            "long_term": {
                "stance": "조건부 긍정",
                "reasoning": "성장 전략은 있으나 재무 확인이 필요하다.",
                "key_features": ["의료기기 사업 확대"],
                "data_limitation": "장기 재무 트렌드 분석 한계",
            },
        },
        "cross_data_reconciliation": {
            "news_plus_market": {
                "summary": "뉴스와 시장 반응은 대체로 같은 방향이다.",
                "divergences": [{"point": "단기 급등", "cross_analysis": "지속성은 확인 필요"}],
            }
        },
    }


if __name__ == "__main__":
    unittest.main()
