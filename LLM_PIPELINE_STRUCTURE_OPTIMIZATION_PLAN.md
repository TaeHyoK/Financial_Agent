# LLM 파이프라인 구조 효율화 계획

## 1. 목적

`max_output_tokens`를 낮춰 응답을 잘라내는 방식이 아니라, 같은 보고서와 검증 문맥을 반복 전송하는 현재 구조를 제거한다.

목표는 다음과 같다.

1. Financial, News, YFinance 검증을 다회차 문답·평가·재작성 구조에서 근거 적합성 판정 구조로 바꾼다.
2. Strategy가 전체 원문 묶음을 반복해서 받지 않고, 정규화된 핵심 근거 패킷만 받게 한다.
3. Strategy 본문과 근거 참조를 같은 LLM 호출에서 생성하여 별도의 Basis 호출을 없앤다.
4. 입력 크기를 API 호출 전에 측정하고, 근거 참조 단위로 안전하게 분할한다.
5. 동일 입력은 fingerprint로 자동 재사용하여 실수로 전체 파이프라인을 재실행하지 않게 한다.
6. Review Agent와 Repair Agent를 다시 도입하지 않는다.

## 2. 현재 상태에 대한 판단

### 2.1 1개월 실행 기준 호출량

SK바이오팜과 비교 기업 일성아이에스를 함께 실행한 현재 파이프라인의 최소 호출 수는 다음과 같다. SDK 내부 재시도와 Strategy의 JSON 분할 재시도는 포함하지 않았다.

| 단계 | 기업당 호출 | 전체 호출 |
| --- | ---: | ---: |
| Financial 본문 + SY | 79 | 158 |
| News 요약·분석 + SY | 6 | 12 |
| YFinance 본문 + SY | 5 | 10 |
| Competitor 요약 | - | 1 |
| Strategy planner·decision·basis | - | 11 |
| Writer | - | 1 |
| **합계** |  | **193** |

핵심 병목은 원천 수집이 아니라 검증과 Strategy 근거 생성이다.

### 2.2 측정된 토큰 기준선

사용량이 기록된 호출과 저장된 요청·응답을 `o200k_base`로 재계산한 호출을 구분했다.

| 구분 | 토큰 | 측정 방식 |
| --- | ---: | --- |
| Financial, 두 기업 | 1,517,656 | API usage 기록 |
| News, 두 기업 | 315,383 | API usage 기록 |
| Competitor | 18,687 | API usage 기록 |
| Strategy 입력 | 664,675 | 오프라인 추정 |
| YFinance SY 입력·출력 | 401,621 | 오프라인 추정 |
| Writer 입력·출력 | 24,380 | 오프라인 추정 |
| **확인 가능한 보수적 합계** | **2,942,402 이상** | 일부 호출·출력 제외 |

약 294만 토큰은 하한이다. 초기 YFinance 보고서 호출과 일부 Strategy 출력 토큰은 사용량 기록이 없어 포함되지 않았다. 기록된 호출의 cached token은 모두 0이었다.

### 2.3 단일 입력 크기와 컨텍스트 한도

현재 큰 입력은 다음 수준이다.

| 호출 | 입력 토큰 |
| --- | ---: |
| YFinance SY 최종 재작성 | 약 83,343 |
| News SY 최종 재작성 | 최대 약 66,011 |
| Strategy basis 1회 | 약 62,800~63,700 |
| Financial SY 최종 재작성 | 약 51,559 |
| Strategy decision | 약 51,041 |
| Writer | 약 21,887 |

현재 기본 모델 기준 공식 한도는 다음과 같다.

- [GPT-5.4 mini](https://developers.openai.com/api/docs/models/gpt-5.4-mini): 400,000 context window, 128,000 max output tokens
- [GPT-5.4](https://developers.openai.com/api/docs/models/gpt-5.4): 1,050,000 context window, 128,000 max output tokens
- GPT-5.4는 272,000 input tokens를 넘는 장문 요청에 별도 장문 요율이 적용된다.

따라서 **현재 1개월·단일 비교 기업 실행은 단일 요청의 컨텍스트 한도에 걸릴 가능성이 낮다.** 지금의 직접적인 문제는 한 번의 입력이 너무 큰 것이 아니라, 5만~8만 토큰 문맥과 3만 자 원문을 여러 호출에서 반복 전송하는 것이다.

다만 구조를 잘못 합치면 문제가 생긴다. 77회 호출을 단순히 하나로 합치면서 77개의 기존 문답과 전체 원문까지 한 요청에 넣으면 입력이 급증한다. 배치화는 반드시 `claim -> evidence_id` 참조 구조와 함께 수행해야 한다.

### 2.4 컨텍스트 외 제한 위험

- 전체 실행 토큰과 분당 토큰(TPM)은 다르다. 현재 약 294만 토큰이 순차 처리되므로 곧바로 TPM 초과를 뜻하지는 않지만, 대상 기업과 비교 기업을 병렬화하면 계정 tier의 TPM 제한이 먼저 나타날 수 있다.
- `[:30000]` 같은 문자 수 기준 절단은 토큰 수를 보장하지 않고 JSON 또는 문장 의미를 중간에서 끊을 수 있다.
- 출력 상한만 줄이면 입력 비용과 중복 호출은 그대로이며, JSON이 잘려 재시도가 늘어날 수 있다.
- 뉴스 기간 확대, 제품·사업부문이 많은 기업, claim 수 증가가 동시에 발생하면 현재보다 빠르게 입력이 커진다.

## 3. 현재 구조의 낭비 원인

### 3.1 Financial SY

현재 claim마다 2회 답변, 1회 평가, 이후 전체 재작성을 수행한다. 19개 claim 기준으로 76회 검증 호출과 1회 재작성 호출이 발생한다.

세 호출에 동일한 `source_context`가 반복되고, 마지막 재작성에는 원 보고서, 수정 지시, 모든 검증 결과, 전체 문맥이 다시 포함된다. 재작성 결과를 다시 검증하지도 않으므로 비용에 비해 보장되는 품질이 약하다.

### 3.2 News SY

두 차례 답변, 평가, 재작성으로 기업당 4회 호출한다. 같은 기사 또는 같은 사건에서 파생된 여러 claim이 각각 독립 문답으로 확장되어 근거가 반복된다.

### 3.3 YFinance SY

수치·날짜·밸류에이션 검증까지 LLM 문답으로 처리하고, 마지막 재작성에 모든 Q&A와 평가를 넣는다. 수치 일치, 날짜 범위, 분모 유효성은 결정론적으로 판정할 수 있다.

### 3.4 Strategy

Planner, Decision, 9개 Basis chunk가 거의 같은 전체 입력 bundle을 다시 받는다. 현재 Strategy 입력 약 66만 토큰 중 대부분이 이 반복에서 발생한다.

또한 Writer는 Basis 결과 중 `opinion_id`, `source_section`, `claim_id`, `evidence_ids`만 주로 사용한다. 별도 Basis 호출에서 만든 설명 문장은 Writer 핵심 입력으로 소비되지 않는다.

### 3.5 Orchestration

기존 산출물 재사용이 명시적 `--reuse-existing`에 의존한다. 동일 조건을 다시 실행하면 사용자가 의도하지 않아도 전체 LLM 단계를 다시 수행할 수 있다.

## 4. 목표 파이프라인

```text
DART / News / YFinance 원천
  -> 도메인별 정규화
  -> evidence catalog 생성
  -> claim 생성
  -> 결정론적 검증
       숫자 / 날짜 / 기간 / 단위 / source ref
  -> 의미 판정이 필요한 claim만 1회 batch 평가
  -> admissibility ledger
       strong / context_only / exclude
  -> compact strategy evidence packet
  -> Content Planner 1회
  -> Decision Agent 1회
       본문 + claim_id + evidence_ids 동시 생성
  -> 결정론적 Writer handoff 조립
  -> Writer 1회
```

검증 단계는 보고서를 다시 쓰지 않는다. 검증 결과는 어떤 근거를 Strategy에 허용할지만 결정한다. 투자 문장 생성은 Strategy와 Writer에만 남긴다.

## 5. 목표 데이터 계약

### 5.1 Evidence catalog

```json
{
  "evidence_id": "financial:revenue:2025H1",
  "domain": "financial",
  "source_date": "2025-08-14",
  "period": "2025H1",
  "metric": "revenue",
  "value": 123456,
  "unit": "KRW_million",
  "text": "원문에서 필요한 최소 문맥",
  "source_ref": "dart:receipt_no:section_or_table"
}
```

- 원문 전체를 각 claim에 복사하지 않는다.
- 정량 데이터는 `value`, `unit`, `period`를 별도 필드로 둔다.
- `text`는 정성 claim에 필요한 문장 또는 단락만 포함한다.

### 5.2 Claim validation

```json
{
  "claim_id": "financial:claim:001",
  "statement": "검증 대상 주장",
  "evidence_ids": ["financial:revenue:2025H1"],
  "deterministic_checks": {
    "source_exists": true,
    "date_valid": true,
    "period_comparable": true,
    "numeric_match": true
  },
  "semantic_status": "strong",
  "limitations": []
}
```

`semantic_status`는 `strong`, `context_only`, `exclude`만 사용한다. LLM은 이 상태와 짧은 사유만 반환하며 보고서 문장을 재작성하지 않는다.

### 5.3 Strategy decision

```json
{
  "opinion": "Buy|Hold|Sell",
  "confidence": "low|medium|high",
  "sections": [
    {
      "section_path": "investment_thesis/positive",
      "text": "Strategy가 생성한 문장",
      "claim_ids": ["financial:claim:001"],
      "evidence_ids": ["financial:revenue:2025H1"]
    }
  ],
  "limitations": []
}
```

이 결과에서 Strategy report와 Writer용 evidence refs를 결정론적으로 분리한다. 별도 LLM Basis Agent는 필요하지 않다.

## 6. 입력 크기 관리 원칙

### 6.1 API 호출 전 계측

`src/shared/llm_clients.py`를 공통 호출 경로로 사용해 다음을 기록한다.

- model
- serialized input token estimate
- prompt, cached, completion, reasoning, total usage
- step, company, run id, attempt
- request fingerprint
- latency와 retry 사유

토큰 추정은 실제 API에 보내는 compact JSON을 기준으로 수행한다. 파일 산출물은 읽기 쉽게 pretty JSON을 유지하되, API 요청에는 indentation을 넣지 않는다.

### 6.2 설계상 입력 예산

다음 수치는 API의 `max token` 설정이 아니라 구조 이상을 조기에 발견하기 위한 내부 설계 기준이다.

| 모델군 | 정상 목표 | 분할 시작 | 강제 중단 또는 명시적 override |
| --- | ---: | ---: | ---: |
| GPT-5.4 mini | 100k 이하 | 100k 초과 | 200k 초과 |
| GPT-5.4 | 120k 이하 | 120k 초과 | 200k 초과 |
| 미등록 모델 | 100k 이하 | 100k 초과 | 200k 초과 |

예상 출력과 reasoning 여유도 함께 고려한다. GPT-5.4는 장문 요율 구간인 272k보다 충분히 낮게 유지한다.

### 6.3 분할 단위

입력이 기준을 넘을 때 전체 원문을 동일하게 붙인 채 claim만 나누지 않는다.

1. claim을 도메인과 source별로 묶는다.
2. 각 묶음이 참조하는 evidence만 포함한다.
3. 공통 메타데이터는 최소 필드만 공유한다.
4. 결과는 `claim_id`로 병합한다.
5. 한 evidence가 여러 묶음에 필요할 때만 제한적으로 중복한다.

문자 수 기반 절단은 제거하고, JSON 객체와 evidence 경계를 보존한다.

### 6.4 규모별 예상

| 시나리오 | 판단 |
| --- | --- |
| 현재 1개월, 대상 1개 + 비교 기업 1개 | 단일 컨텍스트 초과 위험 낮음 |
| 뉴스 3개월 또는 이슈 다수 | 기사 원문 전체 전달 시 위험 증가, event/claim별 evidence 선택 필요 |
| 제품·사업부문이 많은 기업 | 정규화 표는 유지하되 Strategy에는 중요한 변화와 상위 항목만 전달 |
| claim 60개 이상 | 단일 전체 보고서 반복 금지, evidence 참조 기반 2~3 batch 허용 |
| 비교 기업 확대 | 현재 단일 비교 기업 계약을 유지하며, 향후 확대 시 peer별 요약 패킷을 별도 구성 |

## 7. 단계별 구현 계획

## Phase 0. 기준선과 계측 통합

### 작업

- `src/shared/llm_clients.py`에 공통 OpenAI 호출 및 usage 기록 기능을 구현한다.
- 모든 LLM 단계가 `llm_usage_manifest.jsonl`에 동일한 스키마로 기록되게 한다.
- API 전송 직전 compact serialization과 token preflight를 적용한다.
- 현재 1개월 SK바이오팜 + 일성아이에스 결과를 품질·호출 수·토큰 기준선 fixture로 고정한다.

### 완료 조건

- 모든 실제 LLM 호출과 retry가 manifest에서 식별된다.
- 단계별 입력·출력·cached token 합계가 자동 집계된다.
- 동작 결과는 바꾸지 않은 상태에서 기준선을 재현한다.

## Phase 1. 실행 fingerprint와 자동 재사용

### 작업

- 입력 기업, 기준일, 기간, 원천 artifact hash, prompt version, model, 주요 파라미터, schema/code version으로 단계 fingerprint를 만든다.
- fingerprint가 동일하고 산출물 검증을 통과하면 자동 재사용한다.
- 특정 단계만 다시 실행하는 `--force-step`을 제공한다.
- 단순 파일 존재 여부만으로 재사용하지 않는다.

### 완료 조건

- 같은 명령을 두 번 실행했을 때 두 번째 실행의 LLM 호출은 0회다.
- 원천 파일, prompt, model 중 하나가 바뀌면 해당 단계와 downstream만 재실행된다.

## Phase 2. Financial SY 구조 축소

### 작업

- 수치 일치, 단위, 기간 비교 가능성, 기준일, source ref를 결정론적으로 검사한다.
- 의미 판정이 필요한 claim만 claim별 evidence와 함께 한 번의 batch 호출로 보낸다.
- 입력이 100k를 넘는 경우에만 evidence domain 기준으로 분할한다.
- 2회 문답, LLM 평가, 보고서 재작성 노드를 제거한다.
- 결과는 admissibility ledger로만 출력한다.

### 목표

- SY 호출: 기업당 77회에서 통상 1회 이하
- 검증 프롬프트: 기업당 30k 안팎을 우선 목표로 설정
- 재작성된 미검증 보고서가 downstream으로 전달되는 경로 제거

## Phase 3. News SY와 YFinance SY 구조 축소

### News 작업

- 중복 기사와 동일 event에서 파생된 claim을 먼저 묶는다.
- 날짜, 기업 식별, URL/source ref 존재 여부는 결정론적으로 검사한다.
- 정성적 인과·중요도 claim만 1회 batch 평가한다.
- 문답 2회와 재작성 호출을 제거한다.

### YFinance 작업

- 가격 날짜, 수익률 계산, 배수의 분모, 결측값, 기간 정합성을 코드로 검사한다.
- 정성 해석 claim만 1회 batch 평가한다.
- 전체 보고서와 Q&A를 다시 넣는 재작성 호출을 제거한다.

### 목표

- News SY: 기업당 4회에서 통상 1회 이하
- YFinance SY: 기업당 4회에서 통상 1회 이하
- 각 validator는 보고서를 생성하거나 고치지 않고 근거 사용 가능 여부만 반환

## Phase 4. Strategy 입력 계층화와 Basis 호출 제거

### 작업

- Financial, News, YFinance, peer의 `strong` 및 필요한 `context_only` claim으로 `strategy_evidence_packet.json`을 만든다.
- 전체 report와 validation narrative를 동시에 넣지 않는다.
- Content Planner는 섹션 구조와 필요한 evidence group만 결정한다.
- Decision Agent가 투자 판단, 본문, `claim_ids`, `evidence_ids`를 한 번에 출력한다.
- 현재 9회 Basis LLM 호출을 제거한다.
- 기존 basis 산출물이 필요한 소비자는 Decision 결과의 refs를 결정론적으로 변환해 사용한다. 규칙 기반 투자 문장은 생성하지 않는다.
- Strategy payload의 pretty JSON 직렬화를 compact JSON으로 바꾼다.

### 목표

- Strategy 호출: 11회에서 2회
- Strategy 전체 입력: 현재 약 665k에서 120k 이하를 우선 목표로 설정
- 모든 핵심 투자 문장에 유효한 claim/evidence ref 존재

## Phase 5. Competitor와 Writer 경로 정리

### 작업

- Competitor의 별도 LLM prose 요약은 기본 파이프라인에서 제거한다.
- 정규화된 peer 수치와 evidence를 Strategy가 직접 사용한다.
- Writer는 현재 1회 호출 구조를 유지한다.
- Writer handoff에서 같은 Strategy 본문, basis prose, 원천 report가 중복되지 않도록 한다.
- Writer 입력과 출력 usage도 공통 manifest에 기록한다.

### 목표

- Competitor LLM 호출: 1회에서 0회
- Writer 입력: 현재 약 22k 수준 유지 또는 감소
- 최종 보고서의 인용과 수치가 evidence catalog에서 모두 역참조 가능

## Phase 6. 전체 회귀 및 규모 스트레스 테스트

### 테스트 시나리오

1. SK바이오팜, 기준일 `2025-10-31`, 뉴스 1개월, 일성아이에스 1개
2. 동일 입력 재실행
3. 뉴스 3개월과 기사 수가 많은 fixture
4. 제품·서비스 또는 사업부문 행이 많은 fixture
5. claim 60개 이상 fixture
6. 결측 valuation과 적자 기업 fixture
7. API 429와 timeout retry fixture

### 품질 평가

- Buy/Hold/Sell 결론이 데이터 손실이나 prompt 보수성 때문에 기계적으로 Hold로 이동하지 않는지 확인한다.
- 핵심 긍정·부정 근거, 반대 근거, limitation이 Writer handoff에 모두 남는지 직접 읽어 평가한다.
- 모든 수치와 날짜를 원천 artifact에 역추적한다.
- 구조 변경 전후 보고서를 blind 비교하고, 정보 손실·중복·근거 없는 단정을 기록한다.

## 8. 최종 수용 기준

| 항목 | 현재 | 목표 |
| --- | ---: | ---: |
| 전체 LLM 호출 | 최소 193회 | 통상 19회, 최대 25회 |
| 전체 토큰 | 2.94M 이상 | 0.4M~0.6M 목표 |
| Financial SY 호출 | 154회 | 통상 2회 |
| News SY 호출 | 8회 | 통상 2회 |
| YFinance SY 호출 | 8회 | 통상 2회 |
| Strategy 호출 | 11회 | 2회 |
| 별도 Competitor prose 호출 | 1회 | 0회 |
| 단일 입력 | 최대 약 83k | mini 100k, GPT-5.4 120k 이하 유지 |
| usage 기록 범위 | 일부 | 100% |

토큰 목표는 구현 후 실제 API usage로 재측정한다. 품질 기준을 충족하지 못하면 호출 수 목표를 맞추기 위해 근거를 버리지 않고, 문제가 발생한 도메인만 evidence 기반 batch를 추가한다.

## 9. 예상 수정 파일

- `src/shared/llm_clients.py`
- `src/orchestration/manifest.py`
- `src/orchestration/end_to_end_loop.py`
- `src/Agent_Team/Financial_Agent/SY_Agent/langgraph_flow.py`
- `src/Agent_Team/News_Agent/SY_Agent/sy_agent.py`
- `src/Agent_Team/YFinance_Agent/SY_Agent/sy_agent.py`
- `src/Agent_Team/Strategy_Agent/agent.py`
- `src/Agent_Team/Strategy_Agent/prompts/content_planner.md`
- `src/Agent_Team/Strategy_Agent/prompts/decision_agent.md`
- `src/Agent_Team/Writer Agent/writer_handoff.py`
- 관련 단위·통합 테스트

새 공통 모듈은 실제 중복이 확인되는 범위에서만 추가하고, 장기간 유지되는 구·신 파이프라인 이중 구현은 만들지 않는다.

## 10. 명시적 제외 범위

- `max_output_tokens` 축소를 핵심 절감 수단으로 사용
- Review Agent 또는 Repair Agent 재도입
- 검증 단계의 보고서 재작성
- 규칙 기반 Buy/Hold/Sell 문장 생성
- 문자 수 기준 원문 절단
- 전체 원문을 유지한 채 호출만 무조건 하나로 합치는 방식
- 이번 구조 개선과 무관한 데이터 수집 범위 확대

## 11. 구현 순서 요약

```text
Phase 0 계측
  -> Phase 1 fingerprint 재사용
  -> Phase 2 Financial SY
  -> Phase 3 News/YFinance SY
  -> Phase 4 Strategy/Basis
  -> Phase 5 Competitor/Writer
  -> Phase 6 1개월 E2E 및 스트레스 테스트
```

각 Phase 종료 시 호출 수, 실제 토큰, 최대 단일 입력, 테스트 결과, 정성 평가를 먼저 보고한 뒤 다음 Phase로 진행한다.

## 12. 구현 및 검증 결과

구현일: `2026-07-11`

### 12.1 완료 범위

- 공통 입력 token preflight 및 JSONL usage manifest
- orchestration 단계별 입력·코드·dependency·출력 fingerprint 자동 재사용
- Financial SY: claim별 다회차 문답·재작성 제거, semantic batch 1회
- News SY: 문장별 적용 evidence domain + semantic batch 1회
- YFinance SY: source path 기반 evidence catalog + semantic batch 1회
- Strategy: compact evidence packet, Planner 1회, report+source refs Decision 1회
- 별도 Decision Basis LLM 및 prompt 제거
- 구조화 peer comparison의 Competitor prose 의존 제거
- Writer 단일 호출 유지 및 fingerprint cache 추가
- 질문·답변 log와 critic queue 생성 경로 및 기존 1개월 산출물 제거

### 12.2 1개월 실측

기존 원천 수집과 Analyst 산출물은 재사용하고, 변경된 SY부터 Writer까지 SK바이오팜과 일성아이에스를 실제 API로 실행했다.

| 단계 | 최종 정상 호출 | 실제 total tokens |
| --- | ---: | ---: |
| Financial SY, 두 기업 | 2 | 5,588 |
| News SY, 두 기업 | 2 | 22,136 |
| YFinance SY, 두 기업 | 2 | 65,685 |
| Strategy Planner + Decision | 2 | 68,294 |
| Writer | 1 | 18,958 |
| **합계** | **9** | **180,661** |

- 실제 input tokens: `151,921`
- 실제 output tokens: `28,740`
- 최대 단일 입력 추정: `34,911` tokens
- 모든 단일 입력이 mini 100k, GPT-5.4 120k 내부 설계 기준 아래였다.
- 개발 중 계약 보완 재시도는 별도 usage manifest에 남겼으며, 위 표는 run/company/step별 최종 정상 호출만 집계했다.
- 원천 수집과 최초 Analyst 생성까지 포함한 정상 전체 구조의 예상 호출은 19회다.

### 12.3 품질 결과

- 최종 Strategy 의견: `Hold`, 투자기간 `6~12개월`
- Hold 근거: 재무 개선과 안정성 대비 높은 선택일 밸류에이션, 시장 대비 약세, 특정 제품 집중
- 미확정 뉴스 촉매의 재무 기여는 negative evidence에서 제거하고 limitation으로 분리
- News admissibility
  - SK바이오팜: strong 2, context_only 27, exclude 2
  - 일성아이에스: strong 7, context_only 18, exclude 1
- 제외된 News claim은 입력 근거가 없는 비용 증가·현금흐름 개선 가정이었다.
- Financial SY는 규칙 기반 fallback claim을 제거해 기업당 원본 claim 5개만 검증한다.
- Strategy opinion 57개와 source-ref 57개가 일치하며 source 없는 ref는 0개다.
- Writer HTML validation 전 항목 `pass`
- 동일 Strategy와 Writer 명령 재실행 시 usage manifest 행 수가 증가하지 않아 API 호출 0회를 확인했다.

### 12.4 입력 규모 스트레스 검사

- News 90 claim: 약 `21,453` input tokens
- YFinance 60 claim: 약 `37,350` input tokens
- 기준 초과 시 문자 절단이 아니라 evidence 경계를 보존한 자동 batch 분할을 사용한다.

### 12.5 테스트

- 전체 pytest: `134 passed`
- `python -m compileall -q src`: 통과
- orchestration dry-run: 통과

## 13. 최신 통합 결과

이 절은 앞선 중간 실측을 최신 전체 파이프라인 기준으로 갱신한다.

- `financial-report` 단일 명령으로 company resolution부터 Writer까지 실행한다.
- 정상 cold-cache logical call은 `target 6 + peer 6 + final 3 = 15`다.
- 중앙 JSONL은 retry attempt와 logical call을 분리한다.
- 하위 trace와 Markdown 출력 누락 때문에 반복 호출되던 fingerprint 문제를 수정했다.
- deterministic peer dataset에서 실행시각을 제거해 Strategy cache를 안정화했다.
- README 수정은 cache를 무효화하지 않고 Strategy runtime prompt 수정은 무효화한다.
- 최종 warm 실행은 4.1초, LLM 0회, token 0이었다.
- 최대 단일 입력 추정은 16,640 tokens로 100k target과 200k hard limit 아래였다.
- 별도 Hold 편향 평가는 정상 호출 범위에서 제외한다.
- 최종 전체 pytest는 `179 passed`다.
