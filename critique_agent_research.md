# Critique Agent 설계 제안: Decision Basis Card 중심 감사 구조

## 1. 목적

현재 `Financial_Agent_Final` 프로젝트는 다음 흐름까지 구현되어 있다.

```text
Financial Agent final report
News Agent final report
YFinance Agent final report
Competitor Agent summary reports
        ↓
Strategy Agent
        ↓
strategy_report.json / strategy_report.md
```

다음 단계에서는 Strategy Agent가 작성한 투자 전략 초안을 비평하고, 그 비평을 기반으로 Planner Agent와 Writer Agent가 최종 투자 의견 보고서를 작성하는 구조를 구축하려고 한다.

이 문서의 핵심 제안은 다음과 같다.

```text
Critique Agent가 전체 보고서를 full audit하지 않고,
Strategy Agent가 함께 제출한 Decision Basis Card를 중심으로 감사한다.
```

즉, Strategy Agent는 보고서 본문만 쓰는 것이 아니라 자신이 Buy/Hold/Sell 판단을 내린 압축 근거 카드도 함께 제출해야 한다. Critique Agent는 이 카드를 전수 감사하고, 보고서 본문은 강한 주장만 위험 기반으로 샘플링해 검사한다.

## 2. 권장 전체 흐름

```text
1단계. Strategy Agent
  - strategy_report.json 작성
  - strategy_report.md 작성
  - decision_basis_card.json 작성

2단계. Critique Agent
  - decision_basis_card.json 전수 감사
  - Strategy 본문에서 강한 주장만 샘플링 감사
  - critique_report.json 작성
  - planner_revision_brief.json 작성

3단계. Planner Agent
  - Critique 결과를 반영해 최종 보고서 구조 재설계
  - final_report_plan.json 작성

4단계. Writer Agent
  - Planner의 구조와 Critique의 제약을 반영해 최종 보고서 작성
  - final_investment_report.json / .md 작성

5단계. Final Gate
  - Critique 반영 여부만 짧게 재검사
  - 금지 표현, recommendation 불일치, must_fix 누락만 확인
```

이 구조가 기존 full audit 방식보다 적합한 이유는 다음과 같다.

- 토큰 비용과 지연 시간이 줄어든다.
- Critique Agent의 역할이 명확해진다.
- 최종 Buy/Hold/Sell 판단의 traceability가 좋아진다.
- Planner와 Writer가 반영해야 할 수정 지시가 더 선명해진다.
- 전체 본문을 반복 감사하면서 생기는 LLM judge bias와 불필요한 문장 수정 요구를 줄일 수 있다.

## 3. 참고문헌 및 적용 방향

| 참고문헌 | 핵심 아이디어 | 우리 프로젝트 적용 |
| --- | --- | --- |
| OpenAI CriticGPT / LLM Critics Help Catch LLM Bugs | Critic은 오류 탐지에 유용하지만 Critic 자체도 hallucination 가능성이 있다. | Critique Agent는 최종 판단자가 아니라 Decision Basis Card의 오류, 누락, 과장을 찾는 감사자로 제한한다. |
| Chain-of-Verification | 초안 작성 후 검증 질문을 만들고 독립적으로 답해 hallucination을 줄인다. | Decision Basis Card의 각 basis item에 검증 질문을 붙이고 원천 산출물로 답하게 한다. |
| RAGAS | 답변이 context에 충실한지, 필요한 context가 반영됐는지 평가한다. | Card의 `faithfulness`, `coverage`, `context relevance`를 평가한다. |
| LLM-as-a-Judge / MT-Bench | LLM judge는 편향이 있으므로 명시적 rubric과 구조화된 출력이 필요하다. | 자유 비평 대신 JSON schema, severity, action, planner instruction으로 제한한다. |
| G-Eval | form-filling 기반 평가가 자동 평가 안정성을 높인다. | Critique prompt를 고정 평가 양식으로 만든다. |
| Self-Refine | 초안, 피드백, 수정의 반복 구조가 성능을 올릴 수 있다. | Strategy draft -> Critique -> Planner -> Writer의 1회 개선 루프를 기본으로 한다. |
| Reflexion | 피드백을 메모리로 남겨 다음 실행에 반영한다. | 반복 오류는 `critic_memory.json`에 축적할 수 있다. |
| Constitutional AI | 사람이 정한 원칙으로 self-critique와 revision을 수행한다. | 투자판단 constitution을 별도 규칙 파일로 둔다. |
| SelfCheckGPT | 여러 샘플 간 불일치로 hallucination 가능성을 탐지한다. | 고위험 basis item은 필요 시 다중 샘플 검증으로 확장한다. |
| Multi-agent Debate | 역할 분리 토론이 reasoning 개선에 도움을 줄 수 있다. | MVP 이후 Bull/Bear critic 또는 Financial/Market/Risk critic으로 확장한다. |

참고 링크:

- CriticGPT: https://openai.com/index/finding-gpt4s-mistakes-with-gpt-4/
- LLM Critics Help Catch LLM Bugs: https://arxiv.org/abs/2407.00215
- Chain-of-Verification: https://arxiv.org/abs/2309.11495
- RAGAS: https://arxiv.org/abs/2309.15217
- Judging LLM-as-a-Judge with MT-Bench and Chatbot Arena: https://arxiv.org/abs/2306.05685
- G-Eval: https://arxiv.org/abs/2303.16634
- Self-Refine: https://arxiv.org/abs/2303.17651
- Reflexion: https://arxiv.org/abs/2303.11366
- Constitutional AI: https://arxiv.org/abs/2212.08073
- SelfCheckGPT: https://arxiv.org/abs/2303.08896
- Multi-agent Debate: https://arxiv.org/abs/2305.14325

## 4. 현재 Strategy Report 기준 관찰

현재 예시 산출물:

```text
Output_total/Strategy/SK바이오팜_20251031/
  strategy_input_bundle.json
  strategy_content_plan.json
  strategy_report.json
  strategy_report.md
```

현재 `strategy_report.json`의 주요 필드는 다음과 같다.

```text
final_recommendation: Hold
decision_rationale: 7개
target_strengths: 8개
target_risks: 6개
competitor_comparison: 2개
limitations: 14개
```

현재 파일에는 `decision_basis_card.json`이 별도로 존재하지 않는다. 따라서 1차 구현에서는 두 가지 선택지가 있다.

1. Strategy Agent를 수정해 `decision_basis_card.json`을 새로 생성하게 한다.
2. Critique Agent가 기존 `strategy_report.json`과 `strategy_content_plan.json`에서 임시 Decision Basis Card를 생성해 감사한다.

권장안은 1번이다. 다만 빠른 MVP를 위해서는 2번도 가능하다.

현재 Strategy 산출물을 보면 Critique Agent가 특히 확인해야 할 지점은 다음이다.

- `Hold` 결론은 정성적으로 납득 가능하지만, Buy/Hold/Sell을 가르는 명시적 decision rule이 부족하다.
- `limitations`에 동일하거나 유사한 제약 문장이 반복된다.
- source file은 있지만 claim 단위 evidence id 또는 source field가 약하다.
- News SY에서 약화 또는 hallucination candidate였던 claim이 Strategy에서 어떻게 반영됐는지 trace가 약하다.
- 경쟁사 비교가 target과 동일 기간, 동일 지표, 동일 데이터 가용성을 기준으로 이루어졌는지 확인해야 한다.
- 뉴스 촉매와 주가 반응이 재무 개선의 직접 증거처럼 과장됐는지 확인해야 한다.
- 2025 Q3 YTD와 2024 full-year 비교 caution이 모든 관련 claim에 일관되게 적용됐는지 확인해야 한다.

## 5. Decision Basis Card의 역할

Decision Basis Card는 단순 요약문이 아니다. Strategy Agent가 자신의 투자 판단을 감사 가능하게 제출하는 근거 카드다.

Decision Basis Card는 다음 질문에 답해야 한다.

```text
왜 Buy인가?
왜 Hold인가?
왜 Sell인가?
왜 다른 선택지가 아닌가?
어떤 근거가 결론을 밀었는가?
어떤 리스크가 결론을 낮췄는가?
어떤 제약 조건을 적용했는가?
어떤 강한 본문 주장이 결론에 연결되는가?
```

현재 SK바이오팜 예시처럼 최종 판단이 `Hold`라면 Card에는 반드시 다음 항목이 있어야 한다.

- 왜 Buy까지는 아닌가?
- 왜 Sell은 아닌가?
- Hold를 지지하는 핵심 긍정 근거는 무엇인가?
- Hold를 지지하는 핵심 부정/제약 근거는 무엇인가?
- 가장 중요한 반대 근거는 무엇인가?
- 기간 비교 제한은 어디에 반영됐는가?

## 6. Decision Basis Card 권장 Schema

권장 파일:

```text
Output_total/Strategy/<run_key>/decision_basis_card.json
```

권장 schema:

```json
{
  "agent_name": "Strategy Agent",
  "card_version": "1.0",
  "target_company": "SK바이오팜",
  "target_run_key": "SK바이오팜_20251031",
  "final_recommendation": "Hold",
  "recommendation_confidence": "medium",
  "decision_summary": "재무 개선 신호는 있으나 기간 비교 제약, 시장 상대 약세, 규제/정책 리스크를 반영해 Hold로 판단한다.",
  "basis_items": [
    {
      "basis_id": "B001",
      "claim": "2025 Q3 YTD 기준 매출, 공헌이익률, 판관비율에서 재무 개선 신호가 있다.",
      "domain": "financial",
      "direction": "positive",
      "importance": "high",
      "evidence_sources": [
        "Output_total/Financial/SK바이오팜_20251031/final_report.json"
      ],
      "source_fields": [
        "decision_rationale[0]",
        "investment_view.financial_view"
      ],
      "upstream_validation_status": "pass",
      "limitations": [
        "2025 Q3 YTD와 2024 full-year는 동일 기간 YoY가 아니다."
      ],
      "decision_effect": "supports_hold_or_buy"
    }
  ],
  "risk_items": [
    {
      "risk_id": "R001",
      "claim": "시장 대비 20일 초과수익률과 60일 상대강도가 부진하다.",
      "domain": "market",
      "severity": "medium",
      "evidence_sources": [
        "Output_total/Y_Finance/SK바이오팜_20251031/final_report.json"
      ],
      "decision_effect": "prevents_buy"
    }
  ],
  "competitor_basis": [
    {
      "competitor": "더블유에스아이",
      "claim": "더블유에스아이는 EPS 적자 전환으로 단기 수익성이 부진하다.",
      "comparability_limitations": [
        "경쟁사별 데이터 가용성과 기간 기준 차이를 함께 명시해야 한다."
      ]
    }
  ],
  "decision_constraints_applied": [
    "2025 Q3 YTD와 2024 full-year를 동일 기간 YoY로 단정하지 않는다.",
    "뉴스 촉매는 재무 수치의 직접 증거가 아니다.",
    "주가 상승만으로 펀더멘털 개선을 주장하지 않는다."
  ],
  "why_not_buy": [
    "시장 상대강도가 부진하다.",
    "EPS와 성장률 해석에 기간 기준 제약이 있다.",
    "FDA 안전성 조사 및 관세 정책 리스크가 남아 있다."
  ],
  "why_not_sell": [
    "DART 기준 수익성 및 재무 안정성 신호가 양호하다.",
    "영업현금흐름과 자본 구조가 안정적이다.",
    "뉴스상 성장 촉매가 존재한다."
  ],
  "data_gaps": [
    "동일 기간 기준 YoY 비교가 제한적이다.",
    "뉴스 촉매와 재무 성과의 직접 연결은 검증되지 않았다."
  ],
  "strong_claims_in_report": [
    {
      "claim_id": "SC001",
      "section": "Key Strengths",
      "claim": "글로벌 시장에서 세노바메이트 처방 확대 및 시장점유율 1위 달성",
      "linked_basis_id": "B004",
      "risk_reason": "market leadership claim"
    }
  ]
}
```

## 7. Critique Agent의 감사 범위

Critique Agent는 전체 보고서를 전수 감사하지 않는다. 감사 범위는 다음처럼 제한한다.

| 감사 대상 | 감사 방식 | 목적 |
| --- | --- | --- |
| `decision_basis_card.json` | 전수 감사 | 최종 판단 근거의 충실성, 균형, traceability 확인 |
| `final_recommendation` | 전수 감사 | Buy/Hold/Sell 결론이 근거와 리스크에 비례하는지 확인 |
| `basis_items[]` | 전수 감사 | 핵심 긍정 근거가 실제 원천 산출물에 의해 지지되는지 확인 |
| `risk_items[]` | 전수 감사 | 핵심 리스크가 누락되거나 약화되지 않았는지 확인 |
| `why_not_buy`, `why_not_sell` | 전수 감사 | 선택지 배제 논리가 충분한지 확인 |
| `decision_constraints_applied` | 전수 감사 | 기간 비교, 뉴스/시장 과장 방지 규칙이 적용됐는지 확인 |
| 본문 강한 주장 | 위험 기반 샘플링 | 보고서 본문에 Card와 어긋나는 강한 표현이 있는지 확인 |
| 전체 본문 | 전수 감사하지 않음 | 토큰 낭비와 과도한 문장 비평 방지 |

이 방식의 핵심 위험은 Strategy Agent가 Decision Basis Card에 불리한 근거를 누락하는 경우다. 따라서 Critique Agent는 Card만 보지 말고 `strategy_input_bundle.json`과 Card의 coverage를 간단히 비교해야 한다.

첫 번째 감사 질문은 다음이어야 한다.

```text
Strategy Agent가 결론에 중요한 긍정 근거, 부정 근거, 반대 근거, 데이터 한계를 Decision Basis Card에 충분히 올렸는가?
```

## 8. 본문 강한 주장 샘플링 기준

본문은 full audit하지 않고 강한 주장만 샘플링한다. 샘플링 대상은 `strategy_report.md` 또는 `strategy_report.json`의 본문성 필드에서 추출한다.

우선 검사할 표현:

```text
1위
급증
확대
개선
우수
확정
직접 영향
펀더멘털 개선
시장점유율
경쟁 우위
상대적 우위
Buy/Sell/Hold 판단에 직접 연결되는 문장
숫자 또는 비율이 포함된 문장
```

현재 SK바이오팜 Strategy report 기준으로 샘플링 후보가 될 수 있는 문장은 다음과 같다.

```text
- 공헌이익률 93.64%로 수익성 개선 신호 확인
- 판관비율 50.10%로 비용 효율성 개선 방향 나타남
- 글로벌 시장에서 세노바메이트 처방 확대 및 시장점유율 1위 달성
- 주가 및 거래량 상승과 단기 모멘텀 강화 관찰
- SK바이오팜이 상대적으로 수익성과 재무 안정성에서 강하게 보인다
```

Critique Agent는 이 문장들이 Card의 basis item과 연결되는지, 원천 산출물에 근거가 있는지, 과장 표현인지 검사한다.

## 9. 현재 Strategy Report에서 임시 Decision Basis Card 만들기

Strategy Agent를 아직 수정하지 않았다면 Critique Agent가 임시 Card를 만들 수 있다.

현재 `strategy_report.json`의 필드를 다음처럼 매핑한다.

| Decision Basis Card 필드 | 현재 Strategy report에서 가져올 위치 |
| --- | --- |
| `final_recommendation` | `final_recommendation` |
| `decision_summary` | `recommendation_summary` |
| `basis_items` | `decision_rationale[]`, `target_strengths[]`, `investment_view.financial_view`, `investment_view.news_view`, `investment_view.market_view` |
| `risk_items` | `target_risks[]`, `limitations[]`, `investment_view.market_view`, `investment_view.news_view` |
| `competitor_basis` | `competitor_comparison[]`, `investment_view.competitor_view` |
| `decision_constraints_applied` | `limitations[]` 중 정책/해석 제약 문장 |
| `why_not_buy` | `target_risks[]`, `market_view`, `limitations[]`에서 추론 |
| `why_not_sell` | `target_strengths[]`, `financial_view`, 긍정 뉴스/시장 근거에서 추론 |
| `strong_claims_in_report` | 숫자, 1위, 개선, 우수, 상승, 확대 등 강한 표현이 있는 본문 문장 |

이 임시 Card는 `Output_total/Critique/<run_key>/derived_decision_basis_card.json`으로 저장하는 것이 좋다. Strategy Agent가 공식 Card를 생성하기 시작하면 이 파생 파일은 fallback으로만 쓴다.

## 10. Critique Agent의 핵심 질문

Decision Basis Card 전수 감사 질문:

1. Card가 최종 recommendation을 설명하기에 충분한가?
2. 긍정 근거, 부정 근거, 반대 근거가 균형 있게 들어 있는가?
3. 각 basis item은 원천 산출물의 어느 section 또는 field에 연결되는가?
4. 각 basis item의 direction과 decision effect가 타당한가?
5. `why_not_buy`와 `why_not_sell`이 결론과 일관적인가?
6. 기간 기준, 뉴스/시장 보조 역할, 경쟁사 비교 한계가 명시됐는가?
7. upstream SY validation에서 약화된 claim이 강한 근거로 재사용되지 않았는가?
8. Card에 없는 강한 본문 주장이 최종 보고서에 남아 있는가?

본문 샘플링 감사 질문:

1. 이 강한 주장은 Decision Basis Card의 basis item에 연결되는가?
2. 연결된 basis item이 원천 산출물에 의해 지지되는가?
3. 표현 강도가 근거 강도보다 세지 않은가?
4. 숫자, 기간, 단위가 원천과 일치하는가?
5. 뉴스나 주가를 재무 개선의 직접 증거처럼 표현하지 않았는가?

## 11. 평가 Rubric

Critique Agent는 단일 점수 대신 여러 판정 축을 사용한다.

| 평가 축 | 설명 | 예시 판정 |
| --- | --- | --- |
| `card_completeness` | Card가 결론의 주요 근거와 리스크를 충분히 담았는가 | `complete`, `partial`, `missing_key_basis` |
| `basis_faithfulness` | basis item이 원천 산출물에 충실한가 | `supported`, `weakly_supported`, `unsupported`, `contradicted` |
| `decision_traceability` | final recommendation이 basis/risk/constraint로 추적 가능한가 | `clear`, `partial`, `unclear` |
| `alternative_handling` | why_not_buy / why_not_sell이 충분한가 | `sufficient`, `thin`, `missing` |
| `period_consistency` | 기간 비교가 올바른가 | `pass`, `warning`, `fail` |
| `causal_overclaim` | 뉴스/주가를 재무 개선 원인처럼 과장했는가 | `none`, `minor`, `major` |
| `competitor_comparability` | 경쟁사 비교 기준이 공정한가 | `comparable`, `limited`, `not_comparable` |
| `strong_claim_alignment` | 본문 강한 주장이 Card와 일치하는가 | `aligned`, `partially_aligned`, `not_aligned` |
| `critique_readiness` | Planner가 바로 수정 계획을 세울 수 있는가 | `ready`, `needs_more_detail` |

Severity는 다음 3단계로 충분하다.

| severity | 의미 | 처리 |
| --- | --- | --- |
| `critical` | 결론을 바꿀 수 있는 오류 또는 핵심 근거 누락 | Planner가 반드시 수정하거나 결론 재검토 |
| `major` | 결론은 유지 가능하지만 논리/근거 보강 필요 | Planner/Writer가 구조와 문장 보강 |
| `minor` | 중복, 표현, 구성 문제 | Writer가 편집 |

## 12. 권장 파일 구조

```text
src/Agent_Team/Critique_Agent/
  __init__.py
  agent.py
  cli.py
  prompts/
    decision_basis_critic.md
    strong_claim_sampler.md
  rules/
    critique_constitution.json
  tests/
    test_agent.py
```

## 13. 입력 파일 계약

Critique Agent의 기본 입력:

```text
Output_total/Strategy/<run_key>/strategy_report.json
Output_total/Strategy/<run_key>/strategy_report.md
Output_total/Strategy/<run_key>/strategy_input_bundle.json
Output_total/Strategy/<run_key>/strategy_content_plan.json
Output_total/Strategy/<run_key>/decision_basis_card.json
```

`decision_basis_card.json`이 없을 때 fallback:

```text
Output_total/Critique/<run_key>/derived_decision_basis_card.json
```

검증 정확도를 높이기 위한 참조 파일:

```text
Output_total/Financial/<run_key>/final_report.json
Output_total/Financial/<run_key>/final_validation.json
Output_total/News/<run_key>/final_report.json
Output_total/News/<run_key>/final_validation.json
Output_total/Y_Finance/<run_key>/final_report.json
Output_total/Y_Finance/<run_key>/final_validation.json
Output_total/Competitor/<competitor_run_key>/competitor_summary_report.json
```

## 14. 출력 파일 계약

권장 출력 위치:

```text
Output_total/Critique/<run_key>/
  derived_decision_basis_card.json
  critique_report.json
  critique_report.md
  planner_revision_brief.json
```

`critique_report.json` 예시:

```json
{
  "agent_name": "Critique Agent",
  "target_run_key": "SK바이오팜_20251031",
  "target_company": "SK바이오팜",
  "audit_mode": "decision_basis_card_plus_strong_claim_sampling",
  "source_strategy_report": "Output_total/Strategy/SK바이오팜_20251031/strategy_report.json",
  "source_decision_basis_card": "Output_total/Strategy/SK바이오팜_20251031/decision_basis_card.json",
  "verdict": "revise_required",
  "summary": {
    "critical_count": 0,
    "major_count": 4,
    "minor_count": 3,
    "card_completeness": "partial",
    "decision_traceability": "partial",
    "decision_support": "moderate",
    "recommended_next_step": "planner_revision"
  },
  "card_audits": [
    {
      "basis_id": "B001",
      "claim": "2025 Q3 YTD 기준 재무 개선 신호가 있다.",
      "domain": "financial",
      "support_status": "supported",
      "severity": "minor",
      "issue_types": ["period_caution_required"],
      "action": "revise",
      "planner_instruction": "동일 기간 YoY가 아니라 latest YTD directional signal이라는 점을 명시할 것."
    }
  ],
  "decision_audit": {
    "recommendation": "Hold",
    "is_recommendation_supported": true,
    "support_level": "moderate",
    "why_not_buy_status": "sufficient",
    "why_not_sell_status": "sufficient",
    "missing_decision_factors": [
      "Buy/Hold/Sell을 가르는 명시적 threshold 또는 decision rule 부족"
    ],
    "alternative_view_required": true
  },
  "strong_claim_sample_audits": [
    {
      "claim_id": "SC001",
      "section": "Key Strengths",
      "claim": "글로벌 시장에서 세노바메이트 처방 확대 및 시장점유율 1위 달성",
      "linked_basis_id": "B004",
      "support_status": "weakly_supported",
      "severity": "major",
      "action": "revise",
      "planner_instruction": "시장점유율 1위 표현은 원천 근거가 명확할 때만 유지하고, 아니면 처방 확대 중심으로 약화할 것."
    }
  ],
  "coverage_audit": {
    "card_missing_from_input_bundle": [],
    "important_risks_not_in_card": [],
    "important_constraints_not_in_card": []
  },
  "planner_revision_brief": {
    "must_fix": [],
    "should_fix": [],
    "writer_constraints": []
  },
  "created_at": "2026-06-04T00:00:00"
}
```

`planner_revision_brief.json` 예시:

```json
{
  "target_run_key": "SK바이오팜_20251031",
  "recommended_recommendation": "Hold",
  "recommendation_confidence": "medium",
  "audit_mode": "decision_basis_card_plus_strong_claim_sampling",
  "must_fix": [
    "Decision Basis Card에 why_not_buy와 why_not_sell을 명시할 것."
  ],
  "should_fix": [
    "Strategy report의 중복 limitation을 정리할 것.",
    "Buy/Hold/Sell 판단 기준을 별도 문단으로 설명할 것.",
    "경쟁사 비교의 기간 기준 한계를 별도 문단으로 분리할 것."
  ],
  "writer_constraints": [
    "뉴스 촉매를 재무 수치의 직접 증거로 쓰지 말 것.",
    "주가 상승을 펀더멘털 개선의 직접 증거로 쓰지 말 것.",
    "2025 Q3 YTD와 2024 full-year를 동일 기간 YoY로 단정하지 말 것.",
    "Decision Basis Card에 없는 강한 주장은 최종 보고서에서 약화하거나 삭제할 것."
  ],
  "required_report_sections": [
    "최종 투자 의견",
    "Decision Basis 요약",
    "왜 Hold인가",
    "왜 Buy가 아닌가",
    "왜 Sell이 아닌가",
    "재무 근거",
    "뉴스 및 촉매",
    "시장 반응",
    "경쟁사 비교",
    "리스크 및 반대 근거",
    "판단 한계"
  ]
}
```

## 15. Critique Constitution 초안

`rules/critique_constitution.json`에 둘 수 있는 원칙은 다음과 같다.

```json
{
  "rules": [
    {
      "id": "C001",
      "name": "Decision Basis Card first",
      "rule": "Critique Agent는 전체 본문 full audit보다 Decision Basis Card 전수 감사를 우선한다.",
      "severity_if_violated": "major"
    },
    {
      "id": "C002",
      "name": "DART period comparability",
      "rule": "2025 Q3 YTD와 2024 full-year를 동일 기간 YoY로 단정하지 않는다.",
      "severity_if_violated": "critical"
    },
    {
      "id": "C003",
      "name": "News is not financial proof",
      "rule": "뉴스 촉매는 재무 수치의 직접 증거가 아니라 보조 context로만 사용한다.",
      "severity_if_violated": "major"
    },
    {
      "id": "C004",
      "name": "Price is not fundamentals",
      "rule": "주가 상승만으로 펀더멘털 개선을 주장하지 않는다.",
      "severity_if_violated": "major"
    },
    {
      "id": "C005",
      "name": "Alternative handling",
      "rule": "최종 recommendation은 why_not_buy와 why_not_sell 또는 why_not_hold를 통해 대안 배제 논리를 포함해야 한다.",
      "severity_if_violated": "major"
    },
    {
      "id": "C006",
      "name": "Competitor comparability",
      "rule": "경쟁사 비교는 기간, 지표, 데이터 가용성 차이를 함께 명시한다.",
      "severity_if_violated": "major"
    },
    {
      "id": "C007",
      "name": "No unsupported numeric claims",
      "rule": "입력 산출물에 없는 수치 또는 비율을 새로 만들지 않는다.",
      "severity_if_violated": "critical"
    },
    {
      "id": "C008",
      "name": "Strong claims must link to card",
      "rule": "최종 보고서 본문의 강한 주장은 Decision Basis Card의 basis item 또는 risk item에 연결되어야 한다.",
      "severity_if_violated": "major"
    }
  ]
}
```

## 16. 구현 방식 제안

MVP는 rule-based audit과 LLM critique를 섞는 방식이 가장 현실적이다.

### Step 1. 입력 로드

- `strategy_report.json`
- `strategy_report.md`
- `strategy_input_bundle.json`
- `strategy_content_plan.json`
- `decision_basis_card.json`
- target final reports
- competitor summary reports
- 가능하면 각 domain final validation

### Step 2. Decision Basis Card 확보

우선순위:

1. Strategy Agent가 생성한 `decision_basis_card.json` 사용
2. 없으면 Critique Agent가 `strategy_report.json`에서 `derived_decision_basis_card.json` 생성

### Step 3. Card 전수 감사

검사 항목:

- Card completeness
- basis/risk item support
- source file 및 source field 존재 여부
- upstream validation status 반영 여부
- decision constraints 적용 여부
- why_not_buy / why_not_sell 충분성
- competitor comparability

### Step 4. 본문 강한 주장 샘플링

초기에는 rule-based extractor로 충분하다.

샘플링 기준:

- 숫자 또는 비율 포함
- `1위`, `개선`, `우수`, `확대`, `급증`, `경쟁 우위` 등 강한 표현 포함
- Buy/Hold/Sell 결론과 직접 연결
- Card에 연결되지 않은 본문 주장

### Step 5. LLM Critique

LLM에는 전체 원천 데이터를 무작정 넣지 않는다. 다음처럼 압축된 packet을 전달한다.

```json
{
  "decision_basis_card": {},
  "sampled_strong_claims": [],
  "compact_source_context": {
    "financial": {},
    "news": {},
    "market": {},
    "competitors": []
  },
  "upstream_validation_summary": {},
  "critique_constitution": {}
}
```

LLM 출력은 반드시 JSON schema로 제한한다.

### Step 6. Planner brief 생성

Card audit과 strong claim audit 결과를 Planner가 사용할 수 있게 다음 세 묶음으로 변환한다.

- `must_fix`
- `should_fix`
- `writer_constraints`

## 17. Planner / Writer와의 연결

Critique Agent output은 Planner Agent의 입력이 된다.

```text
strategy_report.json
decision_basis_card.json
critique_report.json
planner_revision_brief.json
        ↓
Planner Agent
```

Planner Agent는 최종 보고서 구조를 만든다.

```text
final_report_plan.json
```

Writer Agent는 다음 입력을 받아 최종 Markdown/JSON 보고서를 만든다.

```text
strategy_report.json
decision_basis_card.json
planner_revision_brief.json
final_report_plan.json
source final reports
        ↓
final_investment_report.json
final_investment_report.md
```

최종 보고서에는 최소한 다음 섹션이 필요하다.

```text
1. 최종 투자 의견: Buy / Hold / Sell
2. 한 줄 결론
3. Decision Basis 요약
4. 왜 이 recommendation인가
5. 왜 다른 recommendation은 아닌가
6. 재무 근거
7. 뉴스 및 촉매
8. 시장 반응
9. 경쟁사 비교
10. 주요 리스크 및 반대 근거
11. 판단 한계
12. 모니터링 포인트
```

## 18. Final Gate 역할

Final Gate는 full audit을 다시 하면 안 된다. Final Gate는 짧게 다음만 검사한다.

```text
1. Critique의 must_fix가 Writer 보고서에 반영됐는가?
2. 금지 표현이 남아 있는가?
3. 최종 recommendation이 Planner plan과 일치하는가?
4. Decision Basis Card와 최종 보고서의 핵심 근거가 어긋나지 않는가?
5. Card에 없는 강한 주장이 최종 보고서에 새로 생기지 않았는가?
```

권장 출력:

```text
Output_total/Final_Gate/<run_key>/
  final_gate_check.json
```

## 19. 추천 MVP 범위

1차 구현:

1. Strategy Agent에 `decision_basis_card.json` 생성 추가
2. `Critique_Agent` 패키지 생성
3. Card 전수 감사 구현
4. strong claim sampler 구현
5. `critique_report.json`, `critique_report.md`, `planner_revision_brief.json` 생성
6. 현재 예시인 `SK바이오팜_20251031`에 대해 smoke test 작성

1차 구현에서 피할 것:

- 전체 보고서 full audit
- multi-agent debate
- 고위험 claim 다중 샘플링
- Critique Agent가 최종 보고서 직접 작성

2차 확장:

1. Financial critic, News critic, Market critic, Risk critic 역할 분리
2. SelfCheckGPT 방식의 고위험 basis item 다중 샘플 검증
3. `critic_memory.json` 도입
4. Planner/Writer/Final Gate까지 E2E orchestration 연결

## 20. 최종 제안

우리 프로젝트에서는 Critique Agent를 다음 한 문장으로 정의하는 것이 좋다.

```text
Critique Agent는 Strategy Agent의 전체 보고서를 전수 감사하는 Agent가 아니라,
Strategy Agent가 제출한 Decision Basis Card를 전수 감사하고,
본문의 강한 주장만 위험 기반으로 샘플링해 검사한 뒤,
Planner Agent가 사용할 수정 지시를 생성하는 Agent다.
```

역할 분리는 다음과 같이 잡는다.

- Strategy Agent: 초안 보고서와 Decision Basis Card 생성
- Critique Agent: Card 전수 감사, 본문 강한 주장 샘플링, Planner 수정 지시 생성
- Planner Agent: Critique 결과를 반영해 최종 보고서 구조와 논리 재설계
- Writer Agent: 최종 투자 의견 보고서 작성
- Final Gate: Critique 반영 여부와 금지 표현만 짧게 재검사

이 구조가 현재 프로젝트의 SY Agent 철학과도 잘 맞는다. 각 도메인 SY Agent가 domain-level claim을 검증했다면, Critique Agent는 Strategy-level decision basis를 검증하는 상위 감사자 역할을 맡는다. Full audit이 아니라 Decision Basis Card 중심으로 가야 운영 비용, 정확도, 역할 경계가 모두 안정적이다.
