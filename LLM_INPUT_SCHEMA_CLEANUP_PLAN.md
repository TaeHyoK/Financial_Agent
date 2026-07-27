# LLM 입력 스키마 및 근거 계약 정리 계획

## 1. 목적

현재 파이프라인은 호출 수를 크게 줄였지만, API 입력 안에는 아직 다음 문제가 남아 있다.

1. 검증 대상 문장이 evidence로 다시 들어가는 순환 검증
2. 원 보고서, validation claim, evidence catalog 사이의 동일 문장 중복
3. 다른 도메인 요약의 primary/secondary 역할이 유지되지 않아 동일 원천 해석이 여러 Agent에서 증폭되는 문제
4. 저장·감사용 metadata와 LLM이 읽어야 하는 데이터의 혼합
5. 같은 출력 스키마와 작성 규칙을 한 요청 안에서 여러 형태로 반복
6. 최종 보고서 생성에 사용되지 않는 상태 메시지를 위한 LLM 호출

이번 정리의 목적은 `max token`을 낮추는 것이 아니라, **LLM이 판단에 필요한 원천 근거와 계약만 한 번씩 받도록 입력 구조를 다시 정의하는 것**이다.

## 2. 현재 기준선

### 2.1 최종 정상 경로 실측

SK바이오팜과 일성아이에스를 대상으로 기존 원천·Analyst 산출물을 재사용하고 SY부터 Writer까지 실행한 결과다.

| 항목 | 현재 |
| --- | ---: |
| LLM 호출 | 9회 |
| input tokens | 151,921 |
| output tokens | 28,740 |
| total tokens | 180,661 |
| 최대 단일 입력 추정 | 34,911 |

원천 수집과 Analyst까지 포함한 정상 전체 구조는 19회 호출로 예상된다.

### 2.2 확인된 입력 중복

| 구간 | 확인 결과 |
| --- | --- |
| Strategy packet | input bundle 25,943 tokens 대비 evidence packet 25,654 tokens로 1.1%만 감소 |
| Strategy validation | News claim 31개, YFinance claim 27개가 원 report 문장과 전부 동일 |
| Strategy News catalog | 34개 evidence 중 admissible claim이 쓰지 않는 항목 17개 |
| YFinance SY | report의 summary, stance, interpretation 자체를 evidence로 사용 가능 |
| YFinance SY | claim이 없는 valuation snapshot이 기업당 약 8.4k~9.1k tokens 포함 |
| News SY | claim과 original_item 중복, 전역 deterministic check를 claim마다 반복 |
| Writer | evidence_refs 약 5.1k tokens 중 LLM에 필요한 id-to-path 정보는 약 0.7k tokens |
| Writer | 6개 섹션 출력 계약이 skeleton, structure, role, contract, system prompt로 반복 |
| YFinance Analyst | 동일 월별 News 결합 데이터를 최대 네 번 전달 |
| Financial Analyst | 보고서에 사용되지 않는 상태 메시지 4회에 총 8,072 tokens 사용 |

## 3. 정리 원칙

### 3.1 Audit artifact와 LLM input을 분리한다

- `audit_bundle`: 원문, 절대 경로, 상세 provenance, 전체 validation 결과를 보존한다.
- `llm_packet`: 해당 호출의 판단과 생성에 실제 필요한 필드만 포함한다.
- 저장용 JSON이 크다는 이유만으로 삭제하지 않는다.
- 저장용 JSON 전체를 그대로 LLM에 보내지 않는다.

### 3.2 Evidence는 원천 또는 결정론적 파생값만 허용한다

허용되는 evidence origin은 다음 두 종류다.

- `raw_source`: DART, News 기사, YFinance 원천 값
- `deterministic_derived`: 코드로 계산한 수익률, 비율, valuation, 기간 비교

LLM이 생성한 다음 값은 evidence가 될 수 없다.

- summary
- direction 또는 stance
- interpretation 또는 reasoning
- cross_analysis
- reaction_interpretation
- Strategy 또는 Analyst가 만든 결론 문장

이 값들은 `claim` 또는 `analysis`로만 존재하고, 별도의 원천 evidence로 검증받아야 한다.

### 3.3 Primary domain과 secondary context를 분리한다

다른 도메인의 요약 입력은 제거하지 않는다. 각 하위 Agent가 자기 데이터만 보고 생길 수 있는 편향을 점검하도록 compact secondary context를 계속 제공한다.

| Agent | Primary evidence | 유지할 secondary context | 허용되는 판단 |
| --- | --- | --- | --- |
| Financial | DART 재무·공시 | 주요 News event, 시장·상대성과 요약 | 재무 해석과 외부 문맥의 일치·불일치·확인 불가 |
| News | 기사·공시 이벤트 | 핵심 재무 snapshot, 시장 반응 요약 | 이벤트 서사와 실적·시장 문맥의 일치·불일치·확인 불가 |
| YFinance | 가격·시장·valuation | 핵심 재무 snapshot, 주요 News event | 가격 해석과 사업·재무 문맥의 일치·불일치·확인 불가 |
| Strategy | 세 도메인의 primary claim, secondary context, peer | 모든 검증된 근거 | 교차 도메인 해석과 Buy/Hold/Sell 판단 |
| Writer | Strategy handoff | 없음 | 투자자용 문서 표현 |

Secondary context에는 다음 규칙을 적용한다.

- source-normalized fact 또는 검증된 compact summary만 넣는다.
- primary claim의 직접 증거로 사용하지 않는다.
- 인과관계를 생성하지 않고 `corroborates`, `contradicts`, `neutral`, `insufficient` 중 하나로만 분류한다.
- primary fact의 `strong/context_only/exclude`는 primary evidence로 판정한다.
- secondary context는 표현 강도, limitation, 추가 점검 항목에만 영향을 준다.
- 같은 evidence ID를 여러 Agent가 참조해도 Strategy에서는 하나의 독립 근거로만 계산한다.

```json
{
  "primary_domain_claim": {
    "claim_id": "FIN_CLAIM_001",
    "primary_evidence_ids": ["FIN_EVIDENCE_001"]
  },
  "secondary_context": [
    {
      "source_domain": "market",
      "evidence_ids": ["YF_EVIDENCE_003"],
      "effect": "contradicts",
      "usage": "framing_and_limitation_only"
    }
  ]
}
```

### 3.4 동일 사실은 한 요청에서 한 번만 보낸다

- claim statement가 report path에 이미 있으면 두 사본을 동시에 보내지 않는다.
- 같은 period metadata를 metric마다 반복하지 않는다.
- catalog는 현재 batch의 admissible claim이 참조하는 evidence만 포함한다.
- 절대 경로와 실행 metadata는 LLM packet에서 제거한다.

### 3.5 출력 스키마는 한 가지 표현만 사용한다

- 가능하면 API의 strict structured output을 사용한다.
- strict schema를 사용할 수 없는 호출은 compact output contract 하나만 보낸다.
- skeleton, shape requirements, structure, output contract를 동시에 보내지 않는다.

### 3.6 보고서 문장을 규칙으로 생성하지 않는다

- 결정론적 코드는 수치, 날짜, 기간, 단위, source ref, schema만 검사한다.
- Buy/Hold/Sell 근거와 독자용 문장은 Strategy와 Writer LLM이 작성한다.
- Review Agent와 Repair Agent를 다시 추가하지 않는다.

## 4. 목표 구조

```text
DART / News / YFinance raw artifacts
  -> domain-normalized evidence catalog
       origin_type = raw_source | deterministic_derived
  -> 하위 Agent별 LLM input
       primary_evidence + compact secondary_context
  -> domain claim ledger
       claim_id + statement + primary_evidence_ids
       + secondary_context_assessment + limitations
  -> audit artifact 저장
  -> 호출별 referenced-only LLM packet
  -> Strategy Planner
       primary claim과 context assessment를 구분해 ID 선택
  -> Strategy Decision
       선택된 claim/evidence + peer만 입력
       report + evidence refs 동시 출력
  -> full Writer audit handoff
  -> compact Writer LLM input
       id-to-path grounding map + 독자용 구조화 근거
  -> deterministic validation
```

## 5. 목표 데이터 계약

### 5.1 Evidence catalog

```json
{
  "evidence_id": "YF_MARKET_001",
  "domain": "market",
  "origin_type": "raw_source | deterministic_derived",
  "source_ref": "market_summary.latest_snapshot.stock_return_20d",
  "source_date": "2025-10-31",
  "period": "20D",
  "metric": "stock_return_20d",
  "value": 0.1379,
  "unit": "ratio",
  "text": ""
}
```

- 정량 evidence는 `value`, `unit`, `period`를 사용한다.
- 정성 evidence만 `text`를 사용한다.
- `summary`, `interpretation`, `reasoning` 경로는 evidence catalog 생성 대상에서 제외한다.
- 동일 source ref와 값은 하나의 evidence ID만 갖는다.

### 5.2 Claim ledger

```json
{
  "claim_id": "YF_CLAIM_001",
  "domain": "market",
  "statement": "20일 절대수익률은 양수이나 시장 대비 초과수익률은 음수다.",
  "claim_kind": "fact | interpretation | data_limitation | hypothetical",
  "evidence_use": "strong | context_only | exclude",
  "primary_evidence_ids": ["YF_MARKET_001", "YF_MARKET_002"],
  "secondary_context": [
    {
      "context_id": "CTX_001",
      "source_domain": "news",
      "evidence_ids": ["NEWS_EVENT_001"],
      "effect": "corroborates | contradicts | neutral | insufficient",
      "usage": "framing_and_limitation_only"
    }
  ],
  "limitations": []
}
```

- claim 문장은 ledger에 한 번만 둔다.
- primary evidence와 secondary context evidence를 별도 필드로 유지한다.
- secondary context만으로 primary claim을 strong으로 올리거나 exclude로 내리지 않는다.
- 직접 충돌이 있으면 primary claim 상태를 바꾸는 대신 conflict flag와 limitation을 남긴다.
- Strategy packet에 원 report를 함께 넣지 않는다.
- `exclude` 상세는 audit ledger에 보존하되 Strategy LLM에는 기본적으로 전달하지 않는다.
- exclude 개수와 주요 coverage gap은 summary로만 전달한다.

### 5.3 Content plan

Planner는 근거 문장을 다시 쓰지 않고 ID와 섹션 역할을 선택한다.

```json
{
  "positive_claim_ids": ["FIN_CLAIM_001"],
  "negative_claim_ids": ["YF_CLAIM_003"],
  "neutral_claim_ids": ["FIN_CLAIM_006"],
  "catalyst_claim_ids": ["NEWS_CLAIM_002"],
  "risk_claim_ids": ["FIN_CLAIM_008"],
  "context_assessment_ids": ["CTX_001"],
  "peer_metric_ids": ["PEER_METRIC_004"],
  "limitation_ids": ["LIMIT_001"],
  "section_plan": {
    "investment_thesis": ["FIN_CLAIM_001", "YF_CLAIM_003"]
  }
}
```

### 5.4 Writer LLM input

전체 provenance는 Writer audit handoff에 유지한다. LLM에는 다음처럼 줄여 전달한다.

```json
{
  "target": {},
  "decision": {},
  "structured_evidence": {},
  "risks": [],
  "catalysts": [],
  "data_limits": [],
  "grounding_ref_map": {
    "OP001": "final_recommendation.summary"
  }
}
```

Writer가 사용하지 않는 `agent`, `claim_id`, `source_section`, 상세 `evidence_ids`는 audit handoff에만 남긴다.

## 6. 단계별 구현 계획

## Phase 0. 계약 테스트와 정확한 입력 계측

### 작업

- 현재 1개월 산출물을 회귀 fixture로 고정한다.
- telemetry가 실제 SDK/HTTP transport에 전달되는 payload만 측정하게 한다.
- 요청별 top-level field token breakdown을 테스트 또는 진단 명령으로 제공한다.
- LLM packet 금지 필드를 정의한다.
  - absolute path
  - `original_report`
  - `source_paths`
  - 실행 시간과 base directory
  - 사용되지 않는 빈 collection
- evidence origin 검증기를 추가한다.
- primary evidence와 secondary context를 혼합하면 실패하는 계약 테스트를 추가한다.
- 동일 evidence ID가 여러 Agent의 context에 나타나도 Strategy weight가 중복되지 않는 테스트를 추가한다.

### 완료 조건

- 저장 artifact와 실제 API request token을 별도로 측정한다.
- 모든 호출의 token estimate가 실제 transport payload와 일치한다.
- 순환 evidence를 재현하는 테스트가 수정 전 실패한다.

## Phase 1. YFinance Analyst와 SY 근거 계약 수정

### Analyst 작업

- YFinance LLM의 primary input은 시장·기술 지표와 valuation으로 고정한다.
- 주요 News event와 핵심 DART snapshot은 compact secondary context로 계속 제공한다.
- 기존의 장문 News+DART+Market 교차분석은 `secondary_context_assessment` 구조로 교체한다.
- context assessment는 일치·불일치·중립·확인 불가만 반환하고 인과관계를 단정하지 않는다.
- DART는 selected-date valuation 계산의 결정론적 입력이면서 재무 상태를 확인하는 secondary context로 사용한다.
- 동일한 `monthly_market_news_joined`를 best, worst, recent로 반복 전달하지 않는다.
- source path와 중복 hard constraints를 LLM evidence packet에서 제거한다.
- 각 secondary context item에 원 News/DART evidence ID를 유지한다.

### SY 작업

- evidence catalog를 report 전체 scalar flatten 방식으로 만들지 않는다.
- 원 market summary와 결정론적 supporting feature만 catalog에 넣는다.
- claim의 `reasoning`, `related_data`를 요청에서 제거한다.
- `summary`, `stance`, `interpretation`, `cross_analysis`를 evidence로 등록하지 않는다.
- News/DART secondary context는 해당 도메인의 원천 또는 결정론적 evidence catalog에서 가져온다.
- `secondary_context_assessment`는 별도 claim으로 검증하되 primary market evidence와 context evidence를 구분한다.
- valuation은 semantic claim 없이 catalog에 넣지 않는다.
- 선택일 valuation은 deterministic validation 결과를 Strategy catalog로 직접 전달한다.
- batch마다 실제 claim이 참조 가능한 evidence subset만 보낸다.

### 완료 조건

- claim 자신의 source path 또는 문장이 evidence로 선택되는 경우가 0건이다.
- 모든 evidence origin이 `raw_source` 또는 `deterministic_derived`다.
- YFinance LLM input에 News와 DART secondary context가 존재하고 각 항목의 source ID가 유효하다.
- secondary context만으로 primary market claim 상태를 변경한 경우가 0건이다.
- YFinance SY 두 기업 합산 input을 57,630에서 30,000 이하로 낮춘다.
- 시장·valuation 정보 손실 없이 Strategy가 기존 핵심 수치를 역참조할 수 있다.

## Phase 2. Financial과 News의 도메인 책임 정리

### Financial 작업

- DART 재무 사실, 추세, 재무상태, 제품·서비스 매출을 primary evidence로 유지한다.
- 주요 News event와 시장·상대성과 요약은 compact secondary context로 계속 제공한다.
- 기존 장문 교차 인과 문장은 `secondary_context_assessment`로 교체한다.
- 시장 약세나 News 불확실성은 DART 재무 사실을 기각하지 않고 framing·limitation에만 반영한다.
- report에 사용되지 않는 상태 메시지용 LLM 호출 2회를 제거한다.
- transcript 상태는 고정된 실행 metadata 또는 구조화 상태값으로 기록한다.

### News summary 작업

- DART 원문 company profile 전체 대신 기업명, 업종, 주요 제품·서비스명 중심 compact profile을 만든다.
- metadata의 절대 경로와 수집 내부 설정을 LLM 요청에서 제거한다.
- 기간별 입력은 이미 ranking된 상위 관련 event만 전달하고, 원 event artifact는 별도 보존한다.

### News Analyst 작업

- News 사건·불확실성을 primary claim으로 생성한다.
- 핵심 재무 snapshot과 시장 반응 요약은 compact secondary context로 계속 제공한다.
- 기존 `news_plus_financial`, `news_plus_market`, 통합 장문 교차분석은 공통 `secondary_context_assessment`로 교체한다.
- 재무 또는 가격 데이터는 News 사건의 발생 사실을 증명하지 않고 중요도·정합성·불확실성 점검에만 사용한다.
- evidence map 전체를 user payload에 복사하지 않고 현재 News claim이 참조할 evidence만 전달한다.
- Financial context의 period metadata, display value, source value 중복을 제거한다.

### News SY 작업

- 동일한 `claim`과 `original_item`을 함께 보내지 않는다.
- object claim에 필요한 추가 문맥만 `supporting_context`로 분리한다.
- 전역 date/numeric check는 요청 상단에 한 번만 둔다.
- claim별 deterministic 정보는 blockers와 declared evidence IDs만 전달한다.
- News primary evidence와 Financial/Market secondary context evidence를 별도 필드로 검증한다.

### 완료 조건

- Financial과 News LLM input에 필요한 secondary context가 유지된다.
- 다른 도메인 데이터를 직접 증거나 확정적 원인으로 사용한 문장이 없다.
- 모든 context assessment가 유효한 primary 및 secondary evidence ID를 가진다.
- Financial 상태 메시지 LLM 호출이 두 기업 합산 4회에서 0회가 된다.
- News SY 두 기업 합산 input을 14,745에서 8,000 이하로 낮춘다.
- News 사건과 source evidence coverage는 유지된다.

## Phase 3. Strategy 전용 LLM packet 재구성

### 작업

- `strategy_input_bundle.json`은 audit artifact로 유지한다.
- LLM에는 별도 `strategy_llm_packet.json`만 전달한다.
- full Financial/News/YFinance report를 packet에 넣지 않는다.
- domain primary claim ledger, secondary context assessment, referenced evidence catalog를 조립한다.
- `exclude` claim과 미참조 evidence는 LLM packet에서 제거한다.
- data limitation claim은 별도 evidence가 없어도 명시적으로 유지한다.
- `input_metadata`, 절대 경로, agent/version, 빈 competitor collection을 제거한다.
- peer comparison에서 source path를 제거하고 실제 pairwise metric과 limitation만 전달한다.
- Planner 출력은 prose가 아니라 primary claim, context assessment, evidence ID 선택으로 바꾼다.
- Planner와 Decision이 secondary context를 primary evidence로 승격하지 못하게 schema를 분리한다.
- 동일 underlying evidence ID가 여러 Agent에서 참조되면 한 번만 material evidence로 집계한다.
- Decision에는 Planner가 선택한 claim/evidence와 필수 coverage 항목만 전달한다.
- Planner가 재무, 시장, valuation, News, peer, limitation과 주요 conflict context를 빠뜨리면 코드에서 실패시킨다.
- Strategy output schema는 strict structured output 또는 하나의 compact schema로 통일한다.

### 완료 조건

- strategy audit bundle과 LLM packet의 역할이 코드 수준에서 분리된다.
- LLM packet 내 모든 evidence는 최소 한 admissible claim에서 참조된다.
- 동일 claim statement가 packet 안에 두 번 존재하지 않는다.
- 동일 원천 evidence가 Agent 수만큼 중복 가중되는 경우가 0건이다.
- 각 하위 Agent의 secondary context가 Strategy에 전달되지만 primary/context 역할이 유지된다.
- Strategy Planner+Decision 합산 input을 58,472에서 35,000 이하로 낮춘다.
- 모든 reader-facing material claim에 유효한 source ref가 존재한다.

## Phase 4. Writer 입력 계약 축소

### 작업

- 기존 full `writer_handoff.json`은 audit와 deterministic validator용으로 유지한다.
- 별도 compact Writer LLM input을 만든다.
- `evidence_refs`의 상세 source 110개를 `id -> strategy_path` map으로 바꾼다.
- positive/negative와 동일 문장을 반복하는 contrary evidence는 ID 참조로 바꾼다.
- 다음 schema 표현 중 하나만 유지한다.
  - strict structured output schema
  - compact output contract
- `json_shape_requirements`, `valid_json_skeleton`, `required_report_structure`, `output_contract`의 중복을 제거한다.
- 섹션 역할과 writing policy는 서로 겹치지 않는 한 위치에만 둔다.
- LLM 결과 검증은 계속 full audit handoff로 수행한다.

### 완료 조건

- Writer input에서 상세 source provenance가 제거되지만 validator의 역추적 능력은 유지된다.
- Writer 단일 입력을 16,849에서 12,000 이하로 낮춘다.
- 6개 섹션, grounding refs, 필수 수치, risk 1:1 계약이 모두 통과한다.

## Phase 5. 저장 artifact와 dead schema 정리

### 작업

- YFinance `final_validation.json`의 `original_report` 사본을 제거하고 source path, hash, schema version만 남긴다.
- 같은 validation 결과의 중복 alias 파일이 실제 소비되는지 확인한다.
- audit 목적으로 필요한 alias만 유지하고 나머지는 제거한다.
- source path는 audit artifact에만 남기고 LLM packet에서는 차단한다.
- schema version을 domain별로 올리고 구 schema 소비자를 같은 Phase에서 함께 수정한다.
- 장기간 유지되는 구·신 packet 이중 경로는 만들지 않는다.

### 완료 조건

- 모든 output artifact에 명확한 소비자가 있거나 audit 목적이 문서화된다.
- LLM input과 무관한 상세 원문 사본이 validation 파일에 중복 저장되지 않는다.
- orchestration fingerprint와 downstream path resolver가 새 계약을 사용한다.

## Phase 6. 1개월 실제 API 회귀 검증

### 실행 순서

1. SK바이오팜, 기준일 `2025-10-31`, 뉴스 1개월
2. 일성아이에스 동일 기준 실행
3. domain validation 결과 직접 검토
4. peer comparison 재생성
5. Strategy Planner와 Decision 실행
6. Writer 실행 및 HTML 검증
7. 동일 명령 재실행으로 API 호출 0회 확인

### 정성 평가

- Buy/Hold/Sell 중 특정 의견으로 유도하는 보수성 또는 낙관성이 생기지 않았는지 확인한다.
- 기존 Hold를 강제로 유지하지 않는다. 같은 근거로 결론이 달라지면 이유를 비교한다.
- 재무 개선, 제품 집중, 상대성과, valuation, catalyst, limitation이 모두 남는지 확인한다.
- 하위 Agent의 secondary context를 구조화한 뒤 Strategy가 이를 직접 증거나 인과관계로 과장하지 않는지 읽어 본다.
- Financial, News, YFinance가 다른 도메인 문맥을 보고도 자기 도메인 단독 해석에 갇히지 않는지 확인한다.
- 동일 원천을 여러 Agent가 언급한 것을 독립된 다수 근거로 해석하지 않는지 확인한다.
- 제외된 claim 또는 LLM 생성 문장이 근거로 다시 사용되지 않는지 역추적한다.
- Writer risk 행이 실제 Strategy risk와 1:1로 일치하는지 확인한다.

## 7. 테스트 계획

### 단위 테스트

- evidence origin allowlist
- narrative field evidence 등록 금지
- claim-to-evidence ID 무결성
- primary evidence와 secondary context evidence 역할 분리
- secondary context effect enum 검증
- secondary context 단독으로 primary claim 상태 변경 금지
- Agent 간 동일 evidence ID 중복 가중 금지
- 미참조 catalog entry 0건
- absolute path가 LLM packet에 없음
- `original_item`과 claim 중복 없음
- domain별 허용 source만 존재
- structured output schema가 한 번만 요청에 포함됨
- token budget field regression
- fingerprint가 schema, prompt, model 변경을 감지

### 통합 테스트

- Financial -> Strategy claim/evidence 역참조
- News -> Strategy event/evidence 역참조
- YFinance -> Strategy market/valuation 역참조
- peer metric의 양사·동일 기준일 검증
- Strategy report의 모든 material path에 source ref 존재
- Writer grounding ref가 Strategy path로 해석 가능
- cache hit에서 usage manifest 증가 없음

### 스트레스 테스트

- News 90 claim
- YFinance market claim 60개
- 제품·서비스 항목 다수
- valuation 일부 결측
- 적자 기업의 P/E unavailable
- peer 일부 metric 결측
- 3개월 News 입력
- API 429와 timeout retry

## 8. 최종 수용 기준

| 항목 | 현재 | 목표 |
| --- | ---: | ---: |
| 정상 전체 LLM 호출 | 19회 예상 | 15회 이하 |
| SY -> Writer 호출 | 9회 | 9회 이하 |
| SY -> Writer input tokens | 151,921 | 100,000 이하 |
| SY -> Writer total tokens | 180,661 | 130,000 이하 |
| YFinance SY input, 두 기업 | 57,630 | 30,000 이하 |
| News SY input, 두 기업 | 14,745 | 8,000 이하 |
| Strategy input, 2회 | 58,472 | 35,000 이하 |
| Writer input | 16,849 | 12,000 이하 |
| 최대 단일 입력 | 34,911 | 25,000 이하 |
| 순환 evidence | 존재 | 0건 |
| LLM packet 내 미참조 evidence | 존재 | 0건 |
| 하위 Agent secondary context coverage | 혼합·비정형 | 100% 구조화 |
| 동일 source의 Agent 간 중복 가중 | 가능 | 0건 |
| material claim source ref | 57/57 | 100% 유지 |
| 동일 입력 재실행 | 0회 확인 | 0회 유지 |

토큰 목표를 맞추기 위해 원천 근거를 삭제하지 않는다. 목표를 넘는 경우 먼저 metadata, 중복 사본, cross-domain prose, 미참조 evidence를 제거하고, 필요한 경우 evidence 경계를 보존한 batch 분할을 사용한다.

## 9. 예상 수정 파일

- `src/shared/llm_clients.py`
- `src/Agent_Team/Financial_Agent/langgraph_flow.py`
- `src/Agent_Team/Financial_Agent/SY_Agent/claim_extraction.py`
- `src/Agent_Team/Financial_Agent/SY_Agent/langgraph_flow.py`
- `src/Agent_Team/News_Agent/context_export.py`
- `src/Agent_Team/News_Agent/analysis_agent.py`
- `src/Agent_Team/News_Agent/SY_Agent/sy_agent.py`
- `src/Agent_Team/YFinance_Agent/reporting.py`
- `src/Agent_Team/YFinance_Agent/SY_Agent/sy_agent.py`
- `src/Agent_Team/Strategy_Agent/agent.py`
- `src/Agent_Team/Strategy_Agent/prompts/content_planner.md`
- `src/Agent_Team/Strategy_Agent/prompts/decision_agent.md`
- `src/Agent_Team/Writer Agent/writer_handoff.py`
- `src/Agent_Team/Writer Agent/html_report_writer.py`
- `src/orchestration/end_to_end_loop.py`
- 관련 단위·통합 테스트와 문서

## 10. 구현 순서와 보고 방식

```text
Phase 0 계측·계약 테스트
  -> Phase 1 YFinance 순환 근거 제거
  -> Phase 2 Financial/News primary-context 역할 분리
  -> Phase 3 Strategy packet 실질 축소
  -> Phase 4 Writer compact input
  -> Phase 5 artifact/dead schema 정리
  -> Phase 6 1개월 실제 API 회귀
```

각 Phase가 끝날 때 다음을 먼저 보고한다.

1. 변경 파일
2. 제거한 필드와 유지한 근거
3. 변경 전후 request token
4. 단위·통합 테스트 결과
5. 정성적 품질 판단
6. 다음 Phase 진입 여부

## 11. 명시적 제외 범위

- `max_output_tokens` 축소를 핵심 절감 수단으로 사용
- 원천 기사, DART filing, 시장 데이터의 수집 범위 축소
- 하위 Agent에 제공되는 다른 도메인의 compact 요약 제거
- Repair Agent 또는 Review Agent 추가
- 규칙 기반 Buy/Hold/Sell 또는 보고서 문장 생성
- 컨센서스, 목표주가, view-change 조건 추가
- 단일 비교 기업 계약 변경
- 문자 수 기준 임의 절단
- 품질 검증 없이 모든 Phase를 한 번에 적용

## 12. 구현 및 1개월 실제 회귀 결과

### 12.1 완료 상태

기준일 `2026-07-11` 현재 Phase 0부터 Phase 6까지 구현과 검증을 완료했다.

| Phase | 상태 | 구현 결과 |
| --- | --- | --- |
| Phase 0 | 완료 | 공통 evidence origin/context 계약, 요청 계측, 금지 필드와 참조 무결성 테스트를 추가했다. |
| Phase 1 | 완료 | YFinance의 market primary evidence와 News/DART secondary context를 분리하고, Analyst와 SY에 referenced-only strict schema를 적용했다. |
| Phase 2 | 완료 | Financial은 DART, News는 기사·공시 event만 primary evidence로 사용하도록 고정하고 다른 도메인은 compact context로 구조화했다. 상태 메시지용 LLM 호출은 제거했다. |
| Phase 3 | 완료 | Strategy audit bundle, Planner packet, Decision packet을 분리했다. Planner는 현재 packet의 ID만 고를 수 있는 동적 strict schema를 사용한다. |
| Phase 4 | 완료 | Writer full handoff는 검증용으로 유지하고 LLM에는 compact writer input만 전달한다. 결과 검증은 full handoff로 수행한다. |
| Phase 5 | 완료 | `original_report`, 구 Strategy packet, 구 cross-analysis alias, repair/review 경로와 소비자 없는 Writer 산출물·코드를 정리했다. |
| Phase 6 | 완료 | SK바이오팜과 일성아이에스의 2025년 10월 1개월 데이터를 실제 API로 회귀하고 Strategy와 Writer까지 검증했다. |

### 12.2 핵심 구현 내용

- 모든 도메인의 LLM evidence는 `raw_source` 또는 `deterministic_derived`로 제한했다.
- Financial, News, YFinance가 다른 도메인의 요약을 계속 읽되 `secondary_context_assessment`로만 사용하도록 했다.
- secondary context는 `framing_and_limitation_only`이며 primary claim의 직접 증거나 상태 판정 근거가 될 수 없다.
- Analyst와 SY 출력은 실행 시점의 유효 evidence ID를 enum으로 갖는 strict JSON schema로 제한했다.
- Strategy Planner 입력에서 전체 보고서, 절대 경로, 중복 evidence metadata와 prompt용 schema 사본을 제거했다.
- Strategy Planner 출력은 현재 claim/context/peer/limitation ID만 선택할 수 있다. `$defs`와 `$ref`를 사용해 OpenAI structured-output enum 1,000개 제한도 충족했다.
- Strategy Decision은 Planner가 선택한 claim과 참조된 evidence만 받는다.
- Strategy의 독자용 모든 상위 섹션에 근거 참조를 의무화했고, limitation과 peer provenance도 같은 계약으로 검증한다.
- Writer는 상세 provenance 대신 compact grounding map을 읽고, 결정론적 validator는 full audit handoff를 사용한다.
- Financial claim 제목은 `흑자 기조`, `비용 효율성 개선` 같은 규칙 기반 해석문이 아니라 기간·지표를 나타내는 중립적 사실 라벨로 바꿨다.
- Buy/Hold/Sell 근거와 최종 독자용 문장은 Strategy와 Writer LLM만 작성한다. 결정론적 코드는 수치·기간·ID·schema·provenance를 검사한다.
- News 기간 요약은 기본적으로 관측 기간 전체를 한 요청으로 처리하고, 명시적 옵션에서만 기간별 분할한다.

### 12.3 1개월 실제 실행 범위

| 항목 | 실행 결과 |
| --- | --- |
| 대상 기업 | SK바이오팜 |
| 비교 기업 | 일성아이에스 1개사 |
| 기준일 | 2025-10-31 |
| News 범위 | 2025-10-01 ~ 2025-10-31 |
| SK바이오팜 News 기간 | 관측 25일 전체를 1회 요약 |
| 일성아이에스 News 기간 | 관측 4일 전체를 1회 요약 |
| 최신 DART 재무 | 기준일 당시 확보 가능한 2025-06-30 반기 누적 |
| 과거 재무 | 2022~2024 사업연도와 전년 동기 비교 자료 |
| peer source | 네이버 금융 업종분석에서 확정한 일성아이에스, 비교 계산은 로컬 원천 데이터로 수행 |

Financial SY 판정은 SK바이오팜 `strong 3 / context_only 1 / exclude 0`, 일성아이에스 `strong 2 / context_only 1 / exclude 1`이었다. 일성아이에스의 제외 1건은 EPS를 원천 자료에서 확인할 수 없어서 제외한 것으로, 결측을 임의 보완하지 않았다.

### 12.4 토큰 실측

디버깅 중 재시도는 제외하고, 두 기업의 usage manifest에서 논리 step별 마지막 성공 기록만 정상 경로로 집계했다.

| 항목 | 변경 전 | 최종 실측 | 목표 | 결과 |
| --- | ---: | ---: | ---: | --- |
| 정상 전체 LLM 호출 | 19회 예상 | 15회 | 15회 이하 | 충족 |
| 정상 전체 input tokens | - | 116,938 | - | 기록 |
| 정상 전체 output tokens | - | 29,903 | - | 기록 |
| 정상 전체 total tokens | - | 146,841 | - | 기록 |
| SY -> Writer 호출 | 9회 | 9회 | 9회 이하 | 충족 |
| SY -> Writer input tokens | 151,921 | 66,468 | 100,000 이하 | 충족 |
| SY -> Writer total tokens | 180,661 | 84,460 | 130,000 이하 | 충족 |
| YFinance SY input, 두 기업 | 57,630 | 9,803 | 30,000 이하 | 충족 |
| News SY input, 두 기업 | 14,745 | 7,255 | 8,000 이하 | 충족 |
| Strategy Planner + Decision input | 58,472 | 29,530 | 35,000 이하 | 충족 |
| Writer input | 16,849 | 9,879 | 12,000 이하 | 충족 |
| 최대 단일 입력 | 34,911 | 16,389 | 25,000 이하 | 충족 |

가장 큰 단일 요청도 16,389 input tokens이므로 현재 1개월 입력은 내부 목표 100,000과 hard limit 200,000보다 충분히 작다. 원천 범위를 줄이거나 임의 문자 절단을 적용하지 않고 중복 schema, 미참조 evidence, 장문 cross-domain prose를 제거해 달성했다.

### 12.5 Strategy 정성 평가

- 최종 의견은 `Hold`, 투자기간은 `6개월`, evidence sufficiency는 `medium`이었다.
- Hold는 보수적으로 고정된 결과가 아니다. 반기 매출 성장·수익성·현금흐름과 최근 절대 주가 추세는 긍정적으로, 제품 매출 집중도·제네릭/경쟁 불확실성·높은 멀티플과 제한된 peer 표본은 부정 또는 제한 요인으로 반영됐다.
- Strategy는 Buy가 아닌 이유와 Sell이 아닌 이유를 함께 설명해 특정 의견으로 유도된 흔적이 없었다.
- evidence sufficiency를 추천 의견과 분리했다. 재무가 반기 자료에 머물고 peer가 1개사이며 뉴스의 금액·상업화 기여도를 확인할 수 없어서 `medium`으로 판단했다.
- 56개 reader-facing basis path 모두 하나 이상의 source evidence를 보유했고 빈 근거 항목은 0건이었다.
- Financial, News, YFinance가 같은 원천을 언급해도 evidence ID 기준으로 하나만 material evidence로 취급했다.
- selected-date 계산 valuation과 provider-direct valuation의 기준일이 다름을 별도 limitation으로 유지했다.

### 12.6 Writer 정성 평가

- Writer는 Strategy의 `Hold`와 6개월 투자기간을 일관되게 유지했다.
- 선택일 `2025-10-31` 계산 valuation과 provider label `2025-09-30` 직접 조회 valuation을 서로 다른 행으로 분리해 혼용하지 않았다.
- 재무 개선, 제품별 매출 집중, catalyst, risk, 시장 성과, valuation, peer, 데이터 한계가 독자용 정보 계층으로 모두 남았다.
- risk table은 Strategy의 실제 observed risk와 grounding ref로 연결됐다.
- `writer_validation_report.json`의 18개 검증 항목이 모두 `pass`였고 notes는 비어 있었다.

### 12.7 남아 있는 데이터 한계

- 2025-10-31 기준 DART에 3분기 보고서가 아직 없어서 최신 재무는 2025-06-30 반기 누적이다.
- 국내 peer가 일성아이에스 1개사이므로 업종 평균이나 순위를 일반화할 수 없다.
- 뉴스에서 계약 금액, 실제 매출 기여, 상업화 속도를 확인할 수 없는 event는 정량 catalyst로 승격하지 않는다.
- provider-direct valuation은 제공일 라벨이 선택일과 다를 수 있어 선택일 계산값과 계속 분리해야 한다.

### 12.8 최종 검증

| 검증 | 결과 |
| --- | --- |
| 전체 테스트 | `156 passed` |
| Strategy 테스트 | `25 passed` |
| Python compileall | 통과 |
| `git diff --check` | 통과 |
| 운영 Python의 구 `original_report`, Strategy 구 packet, repair/review, `news_plus_*` 경로 | 0건 |
| Strategy basis source 누락 | 0건 |
| Writer validation | 전체 pass |
| 동일 입력 Strategy·Writer 재실행 | API 호출 0회, cache hit |

이번 계획의 수용 기준은 모두 충족했다. 이후 기능 보완은 입력을 다시 키우기보다 현재 evidence 계약을 유지한 채 데이터 source coverage를 늘리는 방향으로 진행한다.

## 13. 최신 실행 구조 및 계측 정정

기준일: `2026-07-11`

이 절은 12절 이후 추가된 전체 오케스트레이션과 최신 실제 회귀를 기록한다. 앞 절의 `156 passed`, 토큰 수, 날짜 범위는 중간 측정값이며 아래 결과가 우선한다.

### 구조 변경

- 회사명 자동 identity 해석과 target/peer config 생성을 추가했다.
- 회사별 파이프라인 두 개, deterministic peer dataset, Strategy, Writer를 한 명령으로 연결했다.
- execution별 중앙 usage manifest에 `execution_id`, `run_role`, `run_id`, `company_name`을 기록한다.
- 15회는 정상 cold-cache logical call이며 retry transport와 evaluation call을 분리한다.
- 실행 보조 출력 trace/Markdown을 outputs로 등록해 자기 출력을 다음 fingerprint 입력으로 오인하던 문제를 수정했다.
- peer dataset의 동적 `created_at`을 제거하고 비실행 README를 source hash에서 제외했다. runtime prompt Markdown은 계속 추적한다.
- Writer validation 실패 cache를 금지했다.

### 입력 예산과 실측

최종 Writer 계약 수정 직전 cold route 15회와 수정 후 Writer 1회를 결합한 validated cold-equivalent:

| 항목 | 값 |
| --- | ---: |
| logical calls | 15 |
| input tokens | 113,522 |
| output tokens | 29,748 |
| total tokens | 143,270 |
| 최대 단일 입력 추정 | 16,640 |
| 100k target 초과 | 0 |
| 200k hard limit 초과 | 0 |

최종 warm 실행:

| 항목 | 값 |
| --- | ---: |
| 실행 시간 | 4.1초 |
| logical calls | 0 |
| total tokens | 0 |
| cache suppressed calls | 15 |

Hold 편향 평가 6회는 `run_role=evaluation`으로 별도 집계했으며 27,238 total tokens를 사용했다. 정상 15회 수치에는 포함하지 않는다.

### 최종 품질

- target YFinance SY: strong 9, context_only 0, exclude 0
- peer YFinance SY: strong 9, context_only 0, exclude 0
- Strategy: Hold, 6~12개월, evidence sufficiency high
- Writer validator: 18/18 pass, notes 0
- 동일 입력 전체 파이프라인: 0 API calls
- 최종 전체 pytest: 179 passed
