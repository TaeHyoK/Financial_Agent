# 보고서 품질 파이프라인 진단 및 개선 계획

## 1. 목적과 범위

이 문서는 다음 실행을 기준으로 하위 Agent의 정보 충분성, Strategy의 비교 논리, Writer 입력 계층화, 최종 의미 정합성 검증을 점검한 결과와 개선 순서를 정리한다.

- 대상 실행: `SK바이오팜_20251031`
- 비교 대상 보고서
  - 이전: `Output_total/Writer/SK바이오팜_20251031/report_v1.html`
  - 현재: `Output_total/Writer/SK바이오팜_20251031/report.html`
- 주요 조사 산출물
  - Financial: `Output_total/Financial/SK바이오팜_20251031/final_report.json`
  - News: `Output_total/News/SK바이오팜_20251031/final_report.json`
  - YFinance: `Output_total/Y_Finance/SK바이오팜_20251031/final_report.json`
  - Peer: `Output_total/Competitor/SK바이오팜_20251031/peer_comparison_dataset.json`
  - Strategy: `Output_total/Strategy/SK바이오팜_20251031/strategy_llm_packet.json`, `strategy_content_plan.json`, `strategy_decision_output.json`
  - Writer: `Output_total/Writer/SK바이오팜_20251031/writer_handoff.json`, `writer_report_payload.json`, `writer_validation_report.json`

이 계획은 다음 기존 원칙을 유지한다.

- 기업명과 기준일만으로 동작하는 산업 중립 파이프라인을 유지한다.
- 기준일은 장 시작 전으로 해석하고, 시장 가격은 직전 거래일까지 사용한다.
- 비교 기업은 이번 실행에서 선택된 일성아이에스 1개만 사용한다.
- 시장점유율, 컨센서스, 목표주가, View change 조건은 추가하지 않는다.
- Review Agent와 Repair Agent를 다시 도입하지 않는다.
- 투자 문장을 규칙으로 생성하지 않는다.
- 결정론적 코드는 날짜, 기간, 단위, 비교 가능성, 참조 관계와 출력 계약을 검증한다.

## 2. 결론 요약

현재 보고서의 부실함은 하위 Agent가 아무 정보도 수집하지 못해서 생긴 문제가 아니다. 원천 산출물에는 다음 정보가 이미 존재한다.

- 동일 누적기간 재무 비교
- 2022~2024년 연간 재무 이력
- 2025년 6월 말 TTM
- 3개 공시기간의 제품·서비스 매출
- 선택일 직전 거래일 계산 밸류에이션
- KOSPI 대비 초과수익률과 상대강도
- SK바이오팜과 일성아이에스의 성장률, 마진, 시장 성과, 밸류에이션

핵심 문제는 다음 다섯 단계에서 발생한다.

1. 일부 하위 Agent의 검증 claim이 원천 데이터보다 지나치게 얇거나, 데이터의 범위와 중요도를 충분히 표시하지 않는다.
2. Strategy가 비교 가능한 값의 날짜와 기준을 보존하지 않고, 절대 규모를 성장성이나 체력으로 확대 해석한다.
3. Writer가 읽는 입력은 2만 자가 넘는 중복 구조인데도 우선순위가 약하며, exact token 강제 규칙이 근거 표를 토큰별 행으로 분해한다.
4. 현재 validator는 구조와 값의 존재를 확인하지만, 관찰값과 해석의 논리 관계는 확인하지 않는다.
5. evidence ID는 원문 retrieval handle로 사용되지 않으며, 특히 Writer에서는 Strategy path를 가리키는 형식적 grounding 표식에 머문다.

따라서 해결 방향은 데이터 수집량을 무작정 늘리는 것이 아니라 다음 순서여야 한다.

```text
하위 Agent의 범위·날짜·중요도 계약 보강
  -> 동일 지표·동일 날짜·동일 기간의 비교 pair를 결정론적으로 생성
  -> self-contained compact Strategy packet과 별도 provenance map 생성
  -> 각 card에 의미 있는 LLM-local card_key와 section role 부여
  -> Strategy가 raw evidence ID 없이 card_key 기반 typed comparison/risk/evidence object를 출력
  -> Writer에는 선별된 editorial evidence card만 전달
  -> typed metadata 기반 의미 정합성 validator 실행
  -> HTML 렌더링
```

## 3. 현재 실행 기준선

### 3.1 데이터 및 선택량

| 구간 | 현재 상태 | 판단 |
| --- | ---: | --- |
| Financial 검증 claim | 4개 | 원천 데이터 범위에 비해 부족 |
| Financial strong claim | 3개 | 현금흐름·재무상태 복합 claim 1개는 `context_only` |
| News 검증 claim | 10개 전부 strong | 기사 존재와 투자 중요도를 구분하지 못함 |
| YFinance 검증 claim | 9개 전부 strong | 같은 가격 데이터를 여러 horizon stance로 반복 |
| Content Planner claim 선택 | 23/23개 | 실질적인 선별 없음 |
| Content Planner peer metric 선택 | 45/45개 | 실질적인 선별 없음 |
| Content Planner limitation 선택 | 33/33개 | 실질적인 선별 없음 |
| Content Planner context 선택 | 6/6개 | 실질적인 선별 없음 |
| Writer grounding map | 55개 | one-paper 작성 입력으로 과다 |

`strategy_content_plan.json`은 모든 claim, 모든 peer metric, 모든 limitation을 선택한다. 현재 Content Planner는 허용 ID인지 여부만 검증받고, 역할별 중복·선택 상한·비교 pair 완전성은 검증받지 않는다.

### 3.2 실제 LLM 입력량

최근 비캐시 실행 기록 기준 수치는 다음과 같다.

| 호출 | input tokens | output tokens |
| --- | ---: | ---: |
| Strategy Content Planner | 13,703 | 1,297 |
| Strategy Decision | 15,827 | 7,974 |
| Writer | 9,879 | 1,916 |

Writer 입력은 현재 모델의 context limit에 근접하지 않는다. 따라서 현재 문제는 truncation이 아니라 **선택되지 않은 정보가 한꺼번에 들어가면서 핵심 근거의 우선순위가 약해지는 것**이다.

Writer LLM input을 compact JSON으로 측정하면 약 28.7KB이고, 큰 항목은 다음과 같다.

| Writer 입력 항목 | compact 크기 |
| --- | ---: |
| `market_context` | 약 5.0KB |
| `peer_comparison` | 약 4.8KB |
| `valuation` | 약 4.7KB |
| `financial_trend` | 약 4.4KB |
| `grounding_ref_map` | 약 2.7KB |
| `decision` | 약 2.1KB |

결론보다 시장·비교·밸류에이션 원본 묶음이 더 크게 전달되고 있으며, Strategy가 이미 만든 해석과 하위 Agent 원본 해석이 함께 중복된다.

## 4. 하위 Agent별 정보 충분성 진단

### 4.1 Financial Agent

#### 확보된 정보

Financial 산출물에는 다음 정보가 정상적으로 존재한다.

- 기준일 당시 최신 사용 가능 공시: 2025-06-30 반기보고서
- 2025년 반기 누적 대 2024년 반기 누적 비교
- 2022~2024년 연간 손익·현금흐름 이력
- 2025-06-30까지의 TTM
- 2023년, 2024년, 2025년 반기 제품·서비스 매출 표
- 발행주식수와 재무상태·유동성 지표

2025-10-31 당시 3분기보고서가 아직 사용 가능하지 않아 반기보고서로 후퇴한 것은 결손이 아니라 올바른 point-in-time 처리다.

#### 문제 1: 제품 매출 표와 재무제표 매출의 범위가 일치하지 않는다

현재 반기 재무제표 매출은 `354,042,316,121원`이지만 제품·서비스 표 합계는 `320,654백만원`이다.

- 제품 표의 재무제표 매출 커버 비율: 약 90.57%
- 차이: 약 333.9억원

2024년에도 재무제표 연간 매출은 약 4,744.3억원인데 제품 표 합계는 약 5,476.0억원으로 오히려 더 크다. 이는 기간, 연결·별도, 공시 표 범위 또는 추출 열 중 하나가 다를 가능성을 뜻한다.

현재 `revenue_breakdown.validation`은 제품 표 내부 비중 합계가 100%인지 확인할 뿐, 재무제표 매출과의 범위 일치는 확인하지 않는다. 따라서 현재 보고서의 “매출은 세노바메이트 95.1%, 솔리암페톨 1.5%, 기타 3.4%로 구성”이라는 표현은 회사 전체 매출 구성으로 확대 해석될 수 있다.

#### 해결 방안

- 모든 재무 수치에 `statement_scope`를 추가한다: `separate`, `consolidated`, `unknown`.
- 제품 표에도 `breakdown_scope`와 `scope_source_text`를 추가한다.
- 다음 reconciliation 필드를 결정론적으로 계산한다.
  - `breakdown_total_krw`
  - `financial_statement_revenue_krw`
  - `coverage_ratio`
  - `reconciliation_status`: `matched`, `partial`, `scope_mismatch`, `incomparable`
- `matched`가 아니면 Writer에는 “주요 제품·서비스 공시표 기준”이라고 명시하고 회사 전체 매출 구성으로 표현하지 못하게 한다.
- 기존 별도 재무제표 정책과 일치하는 제품 표를 선택할 수 없으면 이를 limitation으로 유지한다.

#### 문제 2: 검증 claim이 원천 데이터 범위보다 부족하다

Financial validation에는 claim이 4개뿐이다.

- 매출 및 성장률
- 공헌이익률 및 판관비율
- EPS
- 영업현금흐름과 재무상태·유동성의 복합 claim

반면 연간 이력, TTM, 영업이익, 순이익, 제품 집중도, 자본구조는 별도의 검증 claim으로 승격되지 않는다. 특히 마지막 claim은 누적기간 현금흐름과 시점 재무상태를 한 문장에 묶어 `context_only`로 하향됐다.

#### 해결 방안

- 기간 성격이 다른 claim을 분리한다.
  - 누적 손익 claim
  - 누적 현금흐름 claim
  - 기준일 재무상태 claim
  - 기준일 유동성·레버리지 claim
- 다음 항목을 별도의 검증 claim 또는 typed fact card로 제공한다.
  - 동일기간 매출·영업이익·순이익·현금흐름 변화
  - 3개년 연간 추세와 흑자전환 여부
  - TTM 값과 derivation
  - 제품 표 범위와 집중도
- `operating_margin`, `net_margin`, `operating_cash_flow_margin`처럼 기업 규모가 다른 peer에도 사용할 수 있는 정규화 지표를 결정론적으로 계산한다.
- 원 단위 절대값과 비율을 함께 유지하되, peer 비교에서는 비율을 우선 사용한다.

### 4.2 News Agent

#### 확보된 정보

1개월 뉴스에는 다음과 같은 직접 기업 이벤트가 존재한다.

- 유로파마와 AI 뇌전증 관리 JV 출범
- 미국 영업 전략 공유
- 한국·중국·일본 상업화 추진
- 인도 제네릭 등장 가능성 보도
- 엑스코프리 집중도 문제 보도

원천 parquet에는 기사 제목뿐 아니라 snippet, 언론사, 기사일, URL과 관련도 점수도 존재한다.

#### 문제 1: Strategy로 전달되는 evidence에서 중요도 판단 정보가 손실된다

`news_agent_evidence_map.json`은 주로 제목과 relation type 중심으로 축약된다. 그 결과 “기사에서 언급됐다”는 사실은 strong으로 검증되지만, 다음 항목은 구분되지 않는다.

- 기업에 직접 적용되는 사건인지
- 산업 전반의 일반 기사인지
- 실제 발생한 사건인지 기사 전망인지
- 재무 영향 경로가 확인됐는지
- 같은 사건을 여러 기사가 반복한 것인지

예를 들어 미국 관세 기사는 제목과 snippet에 SK바이오팜이 직접 등장하지 않는 산업 일반 기사인데 `partner_or_product`로 분류되고 Strategy의 회사 리스크로 승격됐다.

#### 문제 2: 기사 존재와 투자 근거 강도가 같은 `strong`으로 표현된다

“매출 신기록 기대가 언급됐다”는 claim은 해당 기사가 존재한다는 점에서는 사실이지만, 실적 개선이 확인됐다는 strong positive evidence는 아니다. 현재 Content Planner는 이를 positive claim과 catalyst 후보로 선택한다.

#### 문제 3: 동일 사건 중복이 제거되지 않는다

인도 제네릭 기사 하나가 다음 두 claim으로 중복된다.

- 인도 시장 진입 전 제네릭 등장 조짐
- 해외 확장 과정에서 제네릭 경쟁 가능성

Strategy는 이를 business와 regulatory risk로 각각 다시 출력한다.

#### 해결 방안

각 News event/claim을 다음 self-contained card로 정규화한다.

```json
{
  "card_key": "news.mentis_care_launch",
  "event_date": "YYYY-MM-DD",
  "event_summary": "회사에 발생하거나 발표된 핵심 사건",
  "representative_excerpt": "대표 기사 또는 공식 발표의 핵심 1~2문장",
  "event_status": "occurred | announced | reported_expectation | allegation",
  "company_specificity": "direct | product_direct | industry_context",
  "materiality_status": "observed | plausible_unquantified | not_established",
  "financial_link_status": "observed | not_observed | not_applicable",
  "coverage": {
    "article_count": 1,
    "unique_publisher_count": 1,
    "deduplicated_article_count": 1,
    "primary_source_present": false
  }
}
```

- `industry_context`는 회사별 노출 근거가 별도로 없으면 risk로 승격하지 않는다.
- `reported_expectation`은 catalyst 또는 positive evidence가 아니라 context로 전달한다.
- canonical event key와 evidence ID를 이용한 중복 제거는 packet 생성 전에 코드가 수행하고 해당 ID는 LLM packet에 넣지 않는다.
- snippet과 source를 compact evidence에 유지해 제목만으로 중요도를 판단하지 않게 한다.
- 새 뉴스 수집을 늘리기보다 현재 수집된 원문의 company specificity와 materiality를 보존한다.

### 4.3 YFinance Agent

#### 확보된 정보

YFinance 산출물에는 다음 정보가 충분히 존재한다.

- 5일·20일·60일 수익률
- 이동평균, RSI, MACD, 변동성, 거래량
- KOSPI 대비 5일·20일 초과수익률과 60일 상대강도
- 2025-10-30 종가를 사용한 선택일 계산 밸류에이션
- 2025-09-30 provider-direct 밸류에이션
- direct 값과 calculated 값의 날짜 차이 검증

#### 문제 1: Strategy compaction이 선택일 계산 밸류에이션을 제거한다

`compact_strategy_valuation()`은 `direct_yfinance.latest_period`만 전달하고 `calculated_from_close_and_dart`를 제외한다. 이 때문에 Strategy의 `selected_date_valuation`이 실제 선택일 계산값이 아니라 2025-09-30 provider-direct 값이 됐다.

반면 Writer는 원본 YFinance report에서 2025-10-30 계산값을 다시 읽는다. 결과적으로 Strategy의 핵심 논리와 Writer의 Key Evidence Table이 서로 다른 밸류에이션을 우선한다.

#### 문제 2: 같은 시장 신호가 여러 strong claim으로 반복된다

`main_direction`, 단기·중기·장기 stance, 가격 추세, 모멘텀, 거래량이 상당 부분 같은 가격 데이터를 재사용한다. Content Planner는 이들을 대부분 positive로 선택하고, `detailed_market_relative`는 positive와 negative에 동시에 넣는다.

이는 같은 원천 가격 움직임이 여러 독립 긍정 근거처럼 보일 수 있는 구조다.

#### 해결 방안

- Strategy valuation packet에 두 층을 명확히 유지한다.
  - `primary_selected_date_calculated`: 2025-10-30 계산값
  - `provider_direct_reference`: 2025-09-30 참고값
- 각 밸류에이션 값에 `as_of_date`, `calculation_basis`, `input_periods`, `comparison_eligibility`를 포함한다.
- direct와 calculated 값은 서로 검증 대체값이 아니라 날짜가 다른 별도 observation으로 유지한다.
- 시장 claim을 독립 evidence family로 축약한다.
  - absolute trend
  - market-relative performance
  - momentum/volume quality
  - FX context
- 동일 evidence family에서 여러 horizon stance를 독립 득표처럼 세지 않는다.
- 6~12개월 의견에 비해 가격 데이터가 최근 60일 중심이라는 점은 evidence sufficiency에 반영한다.

### 4.4 Peer Comparison

#### 확보된 정보

`peer_comparison_dataset.json`에는 양사의 다음 값이 모두 존재한다.

- 동일 반기 누적 매출 성장률: SK바이오팜 64.68%, 일성아이에스 -0.77%
- 공헌이익률과 판관비율
- 현금흐름과 재무상태
- 5일·20일·60일 수익률과 시장 대비 상대성과
- 2025-10-30 계산 P/E, P/B, P/S

비교 기업이 하나뿐인 것은 사용자 요구사항이므로 결손이 아니다.

#### 문제 1: Strategy packet에서 날짜와 기간 metadata가 제거된다

`build_peer_metric_catalog()`는 모든 값을 개별 scalar ID로 flatten하며, `_is_peer_metadata_path()`는 `_period`, `_date`, `market_date`로 끝나는 metadata를 제거한다.

따라서 Decision Agent는 값은 받지만 각 값이 어느 날짜와 어느 기간 기준인지 pair 단위로 확인하기 어렵다.

#### 문제 2: 비교 pair가 아니라 45개의 독립 scalar가 전달된다

Content Planner는 45개를 전부 선택한다. Decision Agent가 metric path를 보고 대상과 peer 값을 직접 짝지어야 하므로 다음 오류가 발생했다.

- 매출 규모 비교를 성장률 우위로 확대
- 절대 영업현금흐름을 기업 체력 우위로 확대
- 시장 대비 상대성과와 peer 대비 상대성과의 benchmark 혼동

#### 문제 3: 실제 날짜가 다른 밸류에이션을 비교했다

현재 Strategy는 다음 값을 비교했다.

- SK바이오팜: 2025-09-30 provider-direct P/E 24.77배
- 일성아이에스: 2025-10-30 calculated P/E 13.48배

그리고 이를 “동일 기준”이라고 표현했다. 실제로는 양사 모두 2025-10-30 calculated P/E가 있으므로 SK바이오팜 26.97배 대 일성아이에스 13.48배를 비교해야 한다.

#### 해결 방안

scalar catalog 대신 결정론적으로 생성한 comparable pair catalog를 Strategy에 전달한다.

```json
{
  "card_key": "peer.revenue_growth",
  "metric_key": "revenue_growth_pct",
  "comparison_type": "rate",
  "target": {
    "company": "SK바이오팜",
    "value": 64.68,
    "unit": "%",
    "period": "2025 HALF YTD"
  },
  "peer": {
    "company": "일성아이에스",
    "value": -0.77,
    "unit": "%",
    "period": "2025 HALF YTD"
  },
  "comparability": "comparable",
  "allowed_interpretations": ["growth_direction", "growth_gap"]
}
```

- pair 생성 전 metric key, 단위, 기간 basis, 날짜를 확인한다.
- 매출 절대값은 `scale` 비교로만 사용하고 `growth` 해석을 허용하지 않는다.
- OCF 절대값은 `cash_generation_scale`로만 표현한다. 체력 비교가 필요하면 OCF margin을 사용한다.
- 주가 비교에는 benchmark를 명시한다: `peer_relative`와 `market_relative`를 구분한다.
- 밸류에이션은 양쪽 모두 calculated 또는 양쪽 모두 provider-direct이고 날짜가 같을 때만 비교한다.
- 비교 불가 pair는 LLM에서 숨기지 말고 `incomparable` 사유만 전달한다.

## 5. Strategy 논리 진단

### 5.1 현재 확인된 의미 오류

| 심각도 | 현재 결과 | 문제 |
| --- | --- | --- |
| 높음 | “가치평가 동일 날짜 기준 배수” | 실제로는 9월 30일 target과 10월 30일 peer를 혼합 |
| 높음 | 매출 3,540억원 대 328.8억원 -> 성장률 우위 | 절대 규모로 성장률을 판단 |
| 높음 | peer 밸류에이션 비교 데이터가 없다고 작성 | 동일 날짜 calculated P/E/P/B/P/S가 실제로 존재 |
| 높음 | 해외 상업화와 JV가 제품 집중도를 완화할 수 있음 | 해외 상업화가 동일 제품 중심이면 집중도 완화 근거가 아니며 JV 실적 기여도 미확인 |
| 중간 | 같은 인도 제네릭 이슈를 business/regulatory risk로 중복 | 하나의 기사와 하나의 위험을 두 번 계산 |
| 중간 | 산업 일반 관세 기사를 회사 market risk로 승격 | 회사별 노출 경로 미확인 |
| 중간 | evidence sufficiency를 `high`로 판정 | 제품 표 scope와 valuation date 불일치가 해결되지 않음 |

### 5.2 구조적 원인

1. Strategy report 핵심 필드가 대부분 자유 문장이라 날짜·기간·metric key가 검증 가능한 구조로 남지 않는다.
2. `compact_strategy_valuation()`이 primary로 써야 할 calculated valuation을 누락한다.
3. peer data가 pair가 아닌 scalar list로 전달된다.
4. Content Planner에는 선택 상한, 중복 evidence family 제한, role exclusivity가 없고 실제 실행에서는 모든 ID를 선택해 별도 LLM 호출의 선별 효과가 없다.
5. `risk_view`가 주로 News adverse event만 받아 밸류에이션·상대성과 같은 실제 Hold blocker가 Writer risk matrix에서 빠진다.
6. `validate_strategy_report()`는 필드 존재와 큰 숫자 grounding을 확인하지만 문장 간 논리와 비교 가능성은 확인하지 않는다.

### 5.3 목표 Strategy 계약

Strategy가 독자용 문장을 생성하는 기능은 유지하되, 판단 근거를 함께 typed object로 출력한다.

```json
{
  "decision": {
    "opinion": "Hold",
    "horizon": "6~12개월",
    "evidence_sufficiency": "medium",
    "balance_summary": "...",
    "positive_factor_card_keys": ["financial.same_period_trend"],
    "negative_factor_card_keys": ["peer.valuation"]
  },
  "evidence_assessments": {
    "financial.same_period_trend": {
      "direction": "positive",
      "materiality": "decisive",
      "interpretation": "...",
      "investment_effect": "positive"
    }
  },
  "peer_findings": [
    {
      "basis_card_key": "peer.revenue_growth",
      "metric_key": "revenue_growth_pct",
      "comparison_basis": "2025 HALF YTD",
      "direction": "target_advantage",
      "investment_effect": "positive"
    }
  ],
  "decision_risk_factors": [
    {
      "category": "valuation",
      "basis_card_keys": ["valuation.selected_date", "peer.valuation"],
      "risk_summary": "동일 날짜 비교 기업 대비 높은 계산 배수",
      "monitoring_point": "후속 공시의 TTM 이익과 선택일 계산 배수"
    }
  ]
}
```

`card_key`는 `E001`, `NEWS_RAW_*`, `OP*` 같은 opaque 원천 ID가 아니다. card 내용과 함께 전달되는 `financial.same_period_trend`, `news.mentis_care_launch` 같은 요청 내부의 의미 있는 key다. Strategy는 입력에 존재하는 `card_key`만 출력할 수 있으며, 외부 provenance map은 같은 key를 원천 evidence ID와 연결한다.

독자용 prose는 이 typed object와 함께 생성한다. validator는 prose 자체를 규칙으로 다시 쓰지 않고, typed object가 self-contained packet의 수치·날짜·범위와 일치하는지 확인한다. Strategy 출력과 원천의 연결은 `card_key -> provenance map -> raw evidence ID` 순서로 추적한다.

### 5.4 evidence sufficiency 기준 보완

추천 방향과 근거 충분도를 계속 분리하되, 다음 최소 기준을 명시한다.

- `high`
  - 최신 사용 가능 재무와 동일기간 비교가 strong
  - 현금흐름과 재무상태가 기간별로 분리 검증됨
  - 선택일 계산 valuation의 날짜와 입력이 유효함
  - 핵심 peer pair가 동일 기간·동일 날짜로 비교 가능함
  - 결론에 영향을 주는 scope/date conflict가 없음
- `medium`
  - 핵심 재무·시장 근거는 있으나 일부 scope, 날짜, peer, event materiality가 제한됨
- `low`
  - 핵심 재무 또는 가격·valuation 축이 없거나 결론을 지지할 비교 가능 근거가 부족함

현재 SK바이오팜 실행은 재무와 시장 데이터는 충분하지만 제품 표 scope와 valuation 비교 날짜가 충돌하므로 개선 전 기준에서는 `medium`이 더 적절하다.

## 6. Writer 입력 계층화 진단

### 6.1 현재 구조의 문제

Writer는 다음 정보를 동시에 받는다.

- Strategy의 최종 결론과 해석
- Financial 원본 추세·연간 이력·TTM
- YFinance의 main view, horizon view, detailed analysis
- 양사의 전체 peer metrics
- Strategy의 peer findings
- 55개 grounding path
- 별도의 `required_key_evidence` exact token 묶음

이 구조에서는 Writer가 편집자가 아니라 비교 분석을 다시 수행해야 한다. Strategy의 잘못된 pairwise finding과 원본 45개 metrics가 함께 들어가더라도 어느 쪽을 우선해야 하는지 계약상 명확하지 않다.

### 6.2 exact token 정책이 표를 분해한다

현재 prompt는 다음 값을 “정확히 한 번씩 그대로” Key Evidence Table에 넣도록 요구한다.

- 제품별 매출 token
- P/E, P/S, P/B token
- target과 peer 회사명 token

이 규칙 때문에 현재 보고서는 다음과 같은 저가치 행을 만들었다.

- 제품 기간 label만 있는 행
- P/E, P/S, P/B 각각의 개별 행
- 회사명만 있는 target/peer 개별 행
- “선택일 계산 밸류에이션의 핵심 배수다” 같은 동어반복 해석
- `required_key_evidence`라는 내부 field name 노출

### 6.3 보고서 섹션과 Strategy 위험의 역할이 맞지 않는다

Writer risk policy는 `writer_handoff.risks`만 표 행으로 만들도록 강제한다. 하지만 Strategy `risk_view`에는 News event risk만 있고, 실제 Hold의 핵심 blocker인 밸류에이션 부담과 시장 대비 상대성과 약세는 없다.

그 결과 thesis와 risk matrix가 서로 다른 위험을 말한다.

### 6.4 목표 Writer 입력 계약

Writer에는 원본 전체를 다시 주지 않고 Strategy가 승인한 editorial card만 전달한다.

```json
{
  "decision_card": {
    "opinion": "Hold",
    "horizon": "6~12개월",
    "summary": "...",
    "positive_factor_card_keys": ["financial.same_period_trend"],
    "negative_factor_card_keys": ["peer.valuation"]
  },
  "required_card_keys_by_component": {
    "investment_thesis": ["financial.same_period_trend", "peer.valuation"],
    "key_evidence_table": ["financial.same_period_trend", "peer.valuation"],
    "risk_matrix": ["peer.valuation"]
  },
  "evidence_cards": [
    {
      "card_key": "financial.same_period_trend",
      "axis": "financial_trend",
      "label": "반기 재무 추세",
      "observations": ["..."],
      "interpretation_scope": "growth_and_profitability",
      "strategy_interpretation": "동일 누적 기준에서 외형과 이익이 함께 개선됐다.",
      "investment_effect": "positive"
    },
    {
      "card_key": "peer.valuation",
      "axis": "peer_comparison",
      "label": "일성아이에스 비교",
      "comparable_metrics": ["revenue_growth_pct", "trailing_pe", "price_to_book"],
      "strategy_interpretation": "성장성과 수익성은 우위지만 동일 날짜 계산 배수도 높다.",
      "investment_effect": "mixed"
    }
  ],
  "catalyst_cards": [
    {
      "card_key": "news.mentis_care_launch",
      "strategy_interpretation": "사업 확장 경로는 확인되지만 재무 기여는 아직 계량되지 않았다.",
      "investment_effect": "positive"
    }
  ],
  "risk_cards": [
    {
      "card_key": "peer.valuation",
      "risk_summary": "동일 날짜 계산 기준으로 비교 기업보다 높은 배수다.",
      "investment_effect": "negative"
    }
  ],
  "limitation_cards": [
    {
      "card_key": "financial.product_breakdown",
      "limitation": "주요 제품·서비스 공시표와 재무제표 매출의 범위가 일치하지 않는다."
    }
  ]
}
```

Writer LLM에는 이 카드 내용만 전달한다. Key Evidence Table의 `strategy_interpretation`과 `investment_effect`는 Strategy가 의미를 확정한 필드이므로 Writer가 새로 판단하거나 방향을 바꾸지 않는다. Writer는 관찰값을 읽기 쉬운 문장으로 배열하고 섹션 사이의 연결 문장을 작성한다.

Writer payload의 각 표 행과 risk 행은 대응하는 `card_key`를 metadata로 반환한다. Renderer는 `card_key`를 HTML에 표시하지 않고 validator와 provenance 연결에만 사용한다. 카드와 원천 evidence ID의 연결은 `writer_packet_provenance_v2.json` 같은 별도 audit artifact에 저장하고 prompt에는 raw evidence ID를 포함하지 않는다.

### 6.5 Key Evidence Table 목표

- 한 행은 한 token이 아니라 한 evidence axis를 나타낸다.
- 기본 행은 5~8개로 제한한다.
  - 동일기간 재무 추세
  - 재무상태·유동성
  - 제품·서비스 매출 구조
  - 선택일 계산 밸류에이션
  - 시장 절대·상대성과
  - 1:1 peer 비교
  - 필요한 경우 provider-direct 참고값
- 제품 3개는 하나의 매출 구조 행에 합친다.
- P/E, P/S, P/B는 선택일과 계산 기준을 포함한 하나의 valuation 행에 합친다.
- 회사명만 있는 행은 금지한다.
- `투자의견 영향`은 `긍정`, `부정`, `혼재`, `참고` 중 하나로 일관되게 표시한다.
- `해석`과 `투자의견 영향`은 Strategy card의 값을 유지하고 Writer가 독립적으로 다시 판단하지 않는다.
- 관찰값과 해석을 연결할 수 없는 metadata-only 행은 금지한다.
- exact value coverage는 행 분리를 강제하지 않고 통합 행 안의 값 존재만 검증한다.

### 6.6 텍스트 밀도

현재 1~2문단 제한 자체가 문제의 전부는 아니다. 낮은 가치의 표 행을 줄인 뒤 다음 기준을 적용한다.

- Investment thesis에는 의견, 투자기간, 결정적 긍정·부정 근거를 반드시 포함한다.
- Business section은 제품 표 scope와 사업 집중도를 구분한다.
- Catalyst section은 event date, event status, 미확인 재무 기여를 포함한다.
- Data Limits에는 최신 사용 공시, 선택일 시장일, valuation date, peer 범위를 명시한다.
- 전역 3,200자 상한만 두지 말고 섹션별 필수 질문 충족 여부를 먼저 검사한다.

## 7. 의미 정합성 검증 설계

### 7.1 원칙

- 별도의 LLM Review/Repair 호출을 추가하지 않는다.
- 문장을 규칙으로 다시 생성하지 않는다.
- 자유 문장을 regex로 과도하게 해석하지 않는다.
- 비교·날짜·단위·역할을 typed metadata로 먼저 보존하고 이를 결정론적으로 검증한다.
- 자유 문장에는 최종적인 내부 field leakage와 금지어 검사만 적용한다.

### 7.2 Gate A: 하위 Agent contract 검증

#### Financial

- 손익·현금흐름·재무상태의 period basis 분리
- current와 previous의 동일 basis 확인
- 제품 표와 재무제표 매출 reconciliation
- TTM derivation component 존재 확인
- statement scope 존재 확인

#### News

- event date와 selected date 비교
- canonical event 중복 제거
- industry context의 company risk 승격 금지
- reported expectation의 realized evidence 사용 금지
- source snippet 또는 본문 근거 존재 확인

#### YFinance

- 장 시작 전 기준 `market_date < selected_date`
- calculated valuation input date와 formula 확인
- provider-direct와 calculated 값의 날짜 구분
- 동일 evidence family의 중복 claim 식별

### 7.3 Gate B: Strategy 의미 검증

다음 검사는 Strategy prose가 아니라 typed decision object를 대상으로 한다.

1. Strategy가 반환한 모든 `card_key`가 compact packet에 존재해야 한다.
2. `card_key`의 `allowed_sections`와 실제 사용 section이 일치해야 한다.
3. `growth` 해석은 양쪽의 growth metric을 포함한 card를 참조해야 한다.
4. `profitability` 해석은 profit 또는 margin metric을 포함한 card를 참조해야 한다.
5. `financial_strength` 해석은 절대 규모 하나가 아니라 비율 또는 명시된 scale 해석을 사용해야 한다.
6. peer pair의 metric key, unit, date, period basis가 일치해야 한다.
7. target 9월 provider 값과 peer 10월 calculated 값 같은 혼합 비교를 거부한다.
8. market-relative와 peer-relative benchmark를 구분한다.
9. 같은 canonical event/evidence family가 positive 또는 risk에 중복 계상되지 않게 한다.
10. 한 card를 positive와 negative에 동시에 넣으려면 `mixed` 역할을 명시한다.
11. catalyst가 집중도를 완화한다고 판단하려면 별도의 제품·매출 다변화 card를 참조해야 한다.
12. evidence sufficiency 등급이 미리 정의한 coverage 조건과 일치해야 한다.
13. decision의 상위 negative factor가 risk card 또는 thesis counterpoint 중 하나에 반드시 연결돼야 한다.

### 7.4 Gate C: Writer payload 검증

#### 구조 및 coverage

- 투자기간이 thesis 또는 metadata에 존재하는지 확인한다.
- Key Evidence Table의 필수 axis가 존재하는지 확인한다.
- 표 행 수, `card_key`, axis 중복을 확인한다.
- 제품, valuation, peer 이름 값은 통합 행 안에서 coverage를 확인한다.
- `required_card_keys_by_component`와 Writer payload의 component별 `card_key` 집합이 순서와 무관하게 정확히 대응하는지 확인한다.

#### 의미 및 일관성

- 각 표 행은 동일 `card_key` 입력의 `axis`, 관찰값, `strategy_interpretation`, `investment_effect`를 가져야 한다.
- peer 행은 packet에 포함된 comparable pair의 metric key, 날짜, 기간만 사용해야 한다.
- 동일 지표가 섹션별로 다른 날짜나 값으로 나타나지 않는지 확인한다.
- Strategy의 primary selected-date valuation과 Writer 표의 primary valuation이 같은지 확인한다.
- thesis의 상위 blocker와 risk matrix의 핵심 risk가 정렬되는지 확인한다.
- provider-direct 값은 `참고` 외의 primary effect로 쓰지 못하게 한다.
- Key Evidence Table의 Strategy 해석과 effect가 Writer 단계에서 변경되지 않았는지 확인한다.

#### 최종 문장 leakage

다음 형태를 최종 payload와 HTML에서 차단한다.

- `required_key_evidence`, `grounding_ref_map`, `claim_ledger` 등 내부 field name
- `OP...`, `F...`, `NCLAIM...`, `PEER_METRIC...` 같은 내부 ID
- raw evidence ID는 HTML에 표시하지 않으며 semantic `card_key`도 renderer가 독자 화면에서 숨긴다.
- 절대 파일 경로와 validation workflow 문구
- 회사명만 있는 metadata-only 표 행

이 검사는 현재의 제한된 `plain_korean_terms` 목록보다 일반적인 내부 identifier allowlist/denylist로 구현한다.

### 7.5 결정론적 검증의 경계

결정론적 validator는 다음 항목을 강하게 보장할 수 있다.

- card 존재, provenance 연결, 날짜, 기간, 단위, scope
- comparison type과 metric key의 적합성
- Strategy가 선언한 direction·materiality·investment effect
- Writer가 Strategy 해석과 effect를 보존했는지 여부
- 수치·날짜·회사·제품·event의 무단 추가 여부

반면 자유로운 한국어 문장의 모든 의미를 결정론적으로 판정할 수는 없다. 별도 Review LLM과 광범위한 한국어 규칙을 사용하지 않는다는 원칙을 유지하므로 다음 경계를 명시한다.

- Key Evidence Table과 risk matrix의 핵심 해석은 Strategy가 작성하고 Writer가 의미를 변경하지 않는다.
- Writer가 새 투자 해석을 만들 수 있는 범위를 최소화하고 연결 문장과 문서 편집에 집중시킨다.
- Narrative prose의 미세한 의미 drift는 typed factor·수치·방향 보존 검사와 회귀 fixture로 줄이되 완전 자동 판정을 보장하지 않는다.
- acceptance 기준은 검증 가능한 semantic contract 위반을 차단하는 것으로 한정하고 자유 문장 품질 전체를 보장한다고 표현하지 않는다.

## 8. Self-contained compact packet 및 적응형 card budget

### 8.1 설계 결정

이 프로젝트에서는 evidence ID마다 DART 원문, 기사 본문, YFinance 원본을 다시 불러오는 full retrieval을 기본 경로로 사용하지 않는다. 전체 원문 retrieval은 다음 문제가 있다.

- DART XML과 기사 본문이 반복 입력돼 token 사용량이 급증한다.
- 같은 사실을 하위 Agent와 Strategy가 다시 해석해 책임 경계가 흐려진다.
- Writer까지 원문을 읽으면 Strategy 판단을 다시 수행하게 된다.
- 원문 길이에 따라 기업별 입력 편차가 커진다.

대신 LLM이 판단에 필요한 수치, 날짜, 기간, 범위, 대표 발췌와 한계를 한 객체 안에 넣은 self-contained card를 만든다. `E001`, `NEWS_RAW_*`, `OP*` 같은 opaque 원천 ID는 Strategy와 Writer LLM packet에서 제거하고, 원천 추적에 필요한 ID와 파일 위치는 별도 provenance artifact에 보존한다.

다만 LLM 출력과 입력 card를 연결하려면 의미 있는 요청 내부 key는 필요하다. 각 card에는 내용과 함께 `financial.same_period_trend`, `news.mentis_care_launch`, `peer.revenue_growth` 같은 semantic `card_key`를 둔다. 이 key는 원문을 조회하는 handle이 아니라 현재 packet 안에서 typed 판단과 provenance를 연결하는 local join key다.

```text
DART / News / YFinance 원천
  -> domain evidence store
       원천 evidence ID와 상세 provenance 보존
  -> candidate card 생성
       semantic card_key와 필요한 수치·날짜·범위·대표 발췌 포함
  -> 결정론적 validity·dedup·comparability·budget 처리
  -> self-contained Strategy packet
       raw evidence ID 없음, card 내용과 semantic card_key 포함
  -> Strategy Decision LLM
  -> Writer editorial packet
       Strategy가 승인한 card 내용만 포함
```

Financial과 YFinance의 수치형 evidence는 정규화된 원천 record가 사실상 필요한 근거이므로 전체 XML이나 CSV를 다시 넣지 않는다. News는 제목만 보내지 않고 현재 parquet에 이미 존재하는 대표 snippet 1~2문장을 포함한다. snippet으로도 회사 직접성이나 사건 내용이 확인되지 않으면 추정하지 않고 `insufficient`로 표시한다.

### 8.2 현재 수량과 목표 수량

현재 `strategy_llm_packet.json`의 주요 항목은 다음과 같다.

| 영역 | 현재 입력 |
| --- | ---: |
| Financial claim | 4개 |
| News claim | 10개 |
| YFinance claim | 9개 |
| Financial/News/Market evidence catalog | 총 57개 |
| News claim이 참조하는 고유 evidence | 15개 |
| Market claim이 참조하는 고유 evidence | 27개 |
| Peer scalar metric | 45개 |
| Secondary context assessment | 6개 |
| Limitation | 33개 |

현재 Content Planner는 claim 23/23개, peer metric 45/45개, context 6/6개, limitation 33/33개를 선택한다. 따라서 선택 호출이 Decision 입력을 줄이지 못하고 오히려 별도 호출과 출력만 추가한다.

LLM-facing compact packet은 다음 범위를 기본값으로 사용한다.

| 영역 | 적응형 범위 | SK바이오팜 기본 구성 | 구성 원칙 |
| --- | ---: | ---: | --- |
| Financial | 4~6 card, 다사업 예외 최대 7 | 6 | 공시 기준, 동일기간 추세, 수익성, 현금흐름, 재무상태, 사업 구성 |
| News | 0~8 event card | 6 | canonical event 기준으로 직접·구체 사건만 포함 |
| Market | 0~3 card | 3 | 절대 추세, 시장 상대성과, 거래·모멘텀 품질 |
| Valuation | 0~2 card | 2 | 선택일 계산값과 provider-direct 참고값 분리 |
| Peer | 0~6 comparable pair | 5 | 성장, 수익성, 재무상태, 시장 성과, 동일 날짜 valuation |
| Reader-facing limitation | 0~8개 | 7 | 결론에 영향을 주는 날짜·범위·인과·비교 제약만 통합 |
| Machine limitation/blocker | 상한 없음 | 전체 보존 | validity·comparability를 막는 제약은 LLM 표시 상한과 분리 |
| Secondary context | 별도 quota 없음 | 관련 card 내부 배열 | `framing_only` 역할을 유지하고 primary observation과 분리 |

이 범위는 quota가 아니라 상한과 기본 구성이다. 데이터가 없으면 억지로 채우지 않으며, 누락 자체를 Hold나 부정 근거로 사용하지 않는다. card budget은 LLM-facing 표현량에만 적용하고, machine validation에 필요한 blocker와 원천 provenance는 삭제하지 않는다. 전체 machine limitation은 validation sidecar에 보존하고, LLM-facing card에는 해당 card의 eligibility와 판단에 직접 영향을 주는 구조화된 blocker만 넣는다.

### 8.3 token 실측 기준

현재 packet과 동일한 SK바이오팜 원천값으로 `Financial 6 + News 6 + Market 3 + Valuation 2 + Peer 5 + Limitation 7` 시안을 메모리에서 구성해 측정한 결과다.

| 항목 | 현재 Strategy packet | compact 시안 |
| --- | ---: | ---: |
| 데이터 JSON 크기 | 44,487 bytes | 8,449 bytes |
| 데이터 부분 추정 token | 12,945 | 2,543 |
| 실제 Strategy Decision 전체 입력 | 15,827 tokens | 약 4,700~6,000 tokens 예상 |

데이터 부분은 약 80% 감소한다. Prompt와 strict output schema를 포함한 전체 Decision 입력은 구현 형태에 따라 달라지지만 약 60~70% 감소할 가능성이 있다. 최종 v2에는 `card_key`, section routing, 분리된 secondary context가 추가되므로 2,543 tokens를 고정 목표로 사용하지 않는다. 이 수치는 구현 전 시안 측정값이며 실제 전환 후 telemetry와 필수 axis coverage를 함께 확인한다.

compact packet builder는 호출 전에 card 수, 영역별 JSON bytes, 전체 직렬화 크기와 추정 input tokens를 기록한다. model context에 맞추기 위해 원문을 뒤에서 자르는 방식은 사용하지 않는다. 적응형 card 계약과 critical overflow를 적용한 뒤에도 필수 axis를 보존한 packet을 만들 수 없으면 `packet_overflow`로 LLM 호출 전에 실패하며, 실패 artifact에 영역별 크기와 초과 원인을 남긴다.

### 8.4 card별 내용 제한

card 개수만 줄이고 한 card에 전체 원문과 모든 metric을 넣으면 같은 문제가 반복된다. 각 card는 다음 범위를 기본으로 한다.

- 핵심 metric 3~6개
- 비교 기준 1개
- 원천 날짜와 기간 basis
- 대표 발췌 1~2문장
- 독자용 핵심 limitation 최대 1~2개. machine limitation/blocker는 상한 없이 별도 보존
- 하위 Agent의 반복 해석문 제외

공통 card envelope은 다음 필드를 갖는다.

```json
{
  "card_key": "market.relative_performance",
  "allowed_sections": ["investment_thesis", "market_price_view", "decision_balance"],
  "evidence_role": "primary",
  "primary_observation": {},
  "secondary_context": [
    {
      "source_domain": "news",
      "effect": "neutral",
      "usage": "framing_only",
      "statement": "..."
    }
  ],
  "reader_limitations": ["..."],
  "machine_limitations": []
}
```

- `card_key`는 packet 안에서 유일하고 Strategy 출력이 그대로 참조한다.
- `allowed_sections`는 Content Planner가 담당하던 section routing을 대체한다.
- `primary_observation`과 `secondary_context`를 분리해 다른 도메인 요약이 primary evidence로 승격되지 않게 한다.
- `framing_only` secondary context는 evidence sufficiency, factor 수 또는 투자 방향의 독립 근거로 계상하지 않는다.
- `machine_limitations` 전체는 validation sidecar에 상한 없이 보존한다. LLM-facing projection에는 해당 card의 eligibility와 결정에 직접 필요한 blocker code·reason만 중복 없이 포함한다.

Financial 연간 이력은 연도별 card 세 개로 나누지 않고 `annual_trend` card 하나에 3개년 값을 넣는다. P/E, P/S, P/B도 각각의 card로 나누지 않고 같은 날짜의 valuation card 하나에 묶는다. Peer는 target과 peer scalar를 따로 보내지 않고 같은 metric의 pair 하나로 묶는다.

### 8.5 코드와 LLM의 책임 경계

#### 코드가 결정하는 항목

- selected date 이전 자료만 허용
- 기간·단위·statement scope 확인
- target/peer 비교 가능성 판정
- 적자 또는 유효하지 않은 분모의 valuation 제외
- canonical news event 중복 병합
- 기사 수, 고유 publisher 수와 본문 중복 제거 후 기사 수 계산
- 영역별 card 상한과 packet 크기 관리
- 같은 evidence family의 중복 계상 차단
- semantic `card_key` 생성과 충돌 검사
- card별 `allowed_sections`와 machine limitation 부여
- packet path와 원천 provenance 연결

이 과정은 문장을 만들거나 Buy/Hold/Sell 방향에 점수를 주지 않는다.

#### 하위 Agent가 결정하는 항목

- News event의 `event_status`, `company_specificity`, `materiality_status`
- 원문에서 대표할 event summary와 representative excerpt
- Financial·YFinance claim의 정성적 맥락과 명시적 limitation

News Agent가 생성한 정성 metadata는 그대로 신뢰하지 않고 다음 SY 검증에서 claim 근거와 snippet 일치, 회사 직접성의 과장 여부, `reported_expectation`과 `occurred` 구분을 확인한다.

회사명 포함 여부와 날짜 같은 값은 코드가 보조할 수 있지만, 뉴스의 최종 회사 직접성과 중요도를 키워드 규칙만으로 확정하지 않는다.

#### Strategy LLM이 결정하는 항목

- 각 유효 card의 투자 중요도
- 긍정·부정·혼재 해석
- 상충하는 재무·뉴스·시장 근거의 균형
- evidence sufficiency와 Buy/Hold/Sell
- 독자에게 전달할 최종 논리

### 8.6 News event card와 기사 count

비슷한 기사 수는 사건 보도 범위를 보여주는 보조 지표로만 사용한다. 기사 수가 많다는 이유로 긍정·부정 강도를 높이지 않는다. 같은 보도자료를 여러 매체가 재전송한 경우도 독립 근거 여러 개로 세지 않는다.

```json
{
  "event_date": "2025-10-21",
  "card_key": "news.mentis_care_launch",
  "allowed_sections": ["catalyst_view", "decision_balance"],
  "evidence_role": "primary",
  "event_summary": "유로파마와 AI 뇌전증 관리 JV 출범",
  "representative_excerpt": "대표 기사 또는 공식 발표의 핵심 1~2문장",
  "event_status": "occurred",
  "company_specificity": "direct",
  "materiality_status": "plausible_unquantified",
  "coverage": {
    "article_count": 4,
    "unique_publisher_count": 3,
    "deduplicated_article_count": 2,
    "primary_source_present": true,
    "coverage_quality": "verified"
  },
  "financial_link_status": "not_observed"
}
```

- `article_count`: event cluster에 포함된 전체 기사 수
- `unique_publisher_count`: 정규화된 publisher 기준 서로 다른 매체 수
- `deduplicated_article_count`: 정규화 제목과 `doc_text` 유사도 기준으로 근접 복제 기사를 병합한 수
- `primary_source_present`: 회사 IR·보도자료·DART 등 사전에 정의한 1차 source 유형이 event에 연결됐는지 여부
- `coverage_quality`: publisher, 본문, source type이 충분해 위 통계를 계산할 수 있는지 나타내는 상태

현재 데이터만으로 서로 다른 기사가 실제 독립 취재인지 확정할 수 없으므로 `independent_source_count`라는 명칭은 사용하지 않는다. 기사 count는 사건 확인도와 시장 관심의 보조 정보이며 투자 materiality를 대체하지 않는다. 한 건의 공식 공시가 여러 개의 복제 기사보다 강한 근거일 수 있다.

### 8.7 상한 초과와 중요 사건 보존

News 8개 같은 상한은 단순 앞순서 절단으로 구현하지 않는다.

1. `critical` 또는 실제 발생한 고중요도 직접 사건은 우선 포함한다.
2. 동일 사건은 하나의 canonical event로 병합한다.
3. 긍정·부정·혼재 근거 중 실제 존재하는 반대 근거를 모두 제거하지 않는다.
4. 남은 후보는 직접성, 사건 상태, materiality, 1차 source 존재, 중복 제거 후 coverage 품질, 최신성 순으로 정렬한다.
5. 제외된 저중요도 사건은 원문 없이 coverage summary에 개수와 유형만 기록한다.

```json
{
  "news_coverage": {
    "total_event_clusters": 21,
    "selected_event_clusters": 6,
    "omitted_low_materiality_clusters": 15
  }
}
```

모든 candidate event와 provenance는 audit store에 유지한다. 고중요도 직접 사건이 일반 상한 8개를 초과하면 다음 순서로 처리한다.

1. 같은 canonical theme의 사건을 하나의 theme card로 합치되 각 event date와 status를 보존한다.
2. theme 병합 후에도 중요 card가 8개를 넘으면 `critical_overflow`로 최대 2개까지 추가해 최대 10개를 허용한다.
3. 10개를 넘어 서로 다른 중요 사건을 표현할 수 없으면 조용히 삭제하지 않고 `packet_overflow`로 LLM 호출 전에 실패한다.

이 예외는 정상적인 저중요도 뉴스가 상한을 우회하게 하는 용도로 사용하지 않는다. 방향별 고정 quota는 두지 않지만 실제 반대 근거가 모두 제거되는 선택은 허용하지 않는다.

### 8.8 LLM 외부 provenance map

원천 evidence ID는 삭제하지 않고 audit artifact에 유지한다. 다만 LLM에는 보내지 않는다.

```json
{
  "financial.same_period_trend": {
    "source_evidence_ids": ["E001", "E002"],
    "source_paths": ["financial.sy_handoff.key_evidence"],
    "strategy_card_sha256": "..."
  },
  "news.mentis_care_launch": {
    "source_evidence_ids": [
      "NEWS_RAW_2025-10-21_46",
      "NEWS_RAW_2025-10-21_47"
    ],
    "source_paths": ["news_events.parquet"],
    "strategy_card_sha256": "..."
  }
}
```

권장 산출물은 다음과 같다.

- `strategy_compact_packet_v2.json`: Strategy LLM이 실제로 읽는 self-contained 데이터
- `strategy_packet_provenance_v2.json`: packet path와 원천 ID·파일을 연결하는 audit map
- `strategy_decision_output_v2.json`: Strategy의 typed 판단과 독자용 prose
- `writer_editorial_packet_v2.json`: Writer가 읽는 최종 card 묶음
- `writer_packet_provenance_v2.json`: Writer card와 Strategy/원천 경로 연결

Strategy의 typed assessment와 Writer payload는 같은 semantic `card_key`를 반환한다. Strategy provenance에는 `strategy_card_sha256`를 기록하고, Writer provenance에는 `source_strategy_card_sha256`와 `writer_editorial_card_sha256`를 각각 기록한다. validator는 단계별 입력 card hash와 `card_key` 연결을 확인하고 provenance map을 통해 raw evidence까지 역추적한다. 최종 보고서에서 출처 표시가 필요해지면 renderer가 provenance map을 조회한다. Strategy나 Writer가 opaque 원천 ID를 다시 출력하도록 요구하지 않는다.

### 8.9 Content Planner 처리

현재 Content Planner는 13,703 input tokens를 사용하고 모든 ID를 선택한다. v2 전체 전환 시 정상 경로에서 이를 제거하고 결정론적 compact packet builder로 대체하는 것을 기본안으로 한다.

```text
기존
  전체 ID packet -> Content Planner LLM -> 전체 ID 선택 -> Decision LLM

목표
  candidate card -> deterministic compact packet builder -> Decision LLM
```

하위 Agent가 candidate의 정성적 상태와 중요도 정보를 제공하고, packet builder는 validity·dedup·comparability·coverage만 적용한다. 이 변경은 LLM 호출을 추가하지 않으며 정상 경로에서 Strategy 호출을 한 번 줄일 수 있다.

향후 기업 복잡도가 높아 별도 Planner가 다시 필요해지더라도 전체 원문이나 전체 evidence catalog를 주지 않는다. compact card index만 주고 엄격한 선택 상한과 반대 근거 보존 규칙을 적용한다.

Content Planner의 `section_plan` 역할은 card의 `allowed_sections`와 결정론적 section router가 대체한다. Strategy packet은 선택된 card를 한 배열에 섞지 않고 reader-facing section별 입력을 명시한다.

```json
{
  "section_inputs": {
    "investment_thesis": ["financial.same_period_trend", "valuation.selected_date"],
    "financial_view": ["financial.same_period_trend", "financial.cash_flow"],
    "risk_view": ["news.india_generic_entry_risk", "peer.valuation"],
    "peer_competitor_positioning": ["peer.revenue_growth", "peer.valuation"]
  },
  "cards": {
    "financial.same_period_trend": {},
    "valuation.selected_date": {}
  }
}
```

router는 `allowed_sections`를 벗어난 배치를 거부하고, Strategy 출력도 section별로 허용된 `card_key`만 참조할 수 있다.

`allowed_sections`, `section_inputs`와 Writer의 `required_card_keys_by_component`는 하나의 canonical section enum을 공유한다. renderer 명칭이 다르면 결정론적 mapping table로 변환하며 LLM이 임의의 section 이름을 만들게 하지 않는다.

### 8.10 v2 원자적 전환과 운영 마이그레이션

compact packet만 먼저 활성화하면 현재 `content_plan`, `evidence_refs_by_section`, `decision_basis_by_section`을 요구하는 Strategy·Writer 경로가 깨진다. 따라서 다음 계약은 하나의 v2 migration 단위로 설계한다.

- `strategy_compact_packet_v2.json`
- `strategy_packet_provenance_v2.json`
- `strategy_decision_output_v2.json`
- `writer_editorial_packet_v2.json`
- `writer_packet_provenance_v2.json`

구현은 Phase별로 진행하되 v2 전체가 준비되기 전에는 정상 경로를 전환하지 않는다.

1. Phase 2~5 동안 정상 실행 설정은 `strategy_packet_version=v1`로 유지한다.
2. `emit_strategy_v2_shadow_artifacts=true`일 때 이름이 분리된 v2 artifact를 추가 생성하고 v2 unit/integration test에만 사용한다.
3. v2 Strategy와 Writer가 모두 통과하면 shadow 생성을 끄고 정상 실행 설정을 `strategy_packet_version=v2`로 전환한다.
4. cache fingerprint에 packet/contract version을 포함해 v1 cache가 v2에서 재사용되지 않게 한다.
5. 전환 후 한 차례 회귀 실행을 거친 뒤 `strategy_content_plan*.json`과 기존 decision basis artifact를 deprecated 처리한다.

Content Planner 제거 시 정상 cold-cache 예상 호출은 현재 15회에서 14회로 바뀐다. 다음 운영 항목을 같은 migration에서 수정한다.

- `src/orchestration/usage_summary.py`의 final role 예상 호출 수 `3 -> 2`
- full-pipeline 및 usage-summary 테스트의 `15 -> 14` 기대값
- orchestration manifest, dependency graph와 source-file 목록
- Strategy/Writer cache fingerprint와 output path 계약
- README와 Agent별 workflow 문서
- 더 이상 생성하지 않는 planner cache와 stale artifact 정리 정책

### 8.11 일반기업 적용 조건

적응형 budget은 비금융 일반기업의 one-paper 보고서를 기준으로 한다.

- 적자 기업은 P/E를 제외하고 유효한 P/S·P/B와 손실 추세만 전달한다.
- 신규 상장사는 짧은 연간 이력을 limitation으로 표시한다.
- 다사업 기업은 단일 사업 구성 card로 핵심 segment를 표현할 수 없을 때만 두 번째 사업 구성 card를 허용하며 Financial 총 상한은 7개다.
- 뉴스가 적으면 산업 일반 기사로 최소 개수를 채우지 않는다.
- 제품·서비스 매출이 미공시되면 추정하지 않는다.
- peer metric이 부족하면 가능한 pair만 전달한다.
- 데이터 누락은 evidence sufficiency에 반영하되 자동으로 Hold나 부정 근거가 되지 않는다.

은행·금융업 특수 지표와 연결재무제표용 별도 처리는 현재 제외 범위를 유지한다.

## 9. 구현 순서

### Phase 0. 회귀 fixture와 실패 사례 고정

#### 작업

- 현재 `SK바이오팜_20251031` 산출물을 의미 오류 fixture로 보존한다.
- `report_v1.html`은 참고 문체로만 사용하고 golden truth로 사용하지 않는다.
- 다음 실패 사례를 테스트로 먼저 추가한다.
  - 절대 매출 규모 -> 성장 우위
  - 날짜가 다른 P/E 비교
  - 내부 schema field 노출
  - risk 중복
  - investment horizon 누락
  - 제품 표와 재무제표 매출 scope 불일치
  - opaque raw evidence ID만 있고 self-contained card 내용이나 semantic `card_key`가 없는 Strategy/Writer 입력
  - News 상한 처리로 저빈도 중요 반대 사건이 누락되는 경우

#### 완료 기준

- 각 fixture에 기대 violation code와 문제 `card_key` 또는 metric axis가 명시된다.
- Phase 0에서는 validator 구현을 요구하지 않는다. fixture가 재현 가능하고 이후 Gate A/B/C 테스트가 같은 기대값을 사용할 수 있으면 완료한다.

### Phase 1. 하위 Agent evidence contract 보강

#### Financial

- statement scope와 revenue breakdown reconciliation 추가
- 누적 현금흐름과 시점 재무상태 claim 분리
- annual trend, TTM, product concentration typed fact 추가
- peer용 normalized profitability/cash-flow metric 추가

#### News

- event status, company specificity, materiality, canonical event key 추가
- evidence map에 compact snippet/source 유지
- `unique_publisher_count`, `deduplicated_article_count`, `coverage_quality` 계산에 필요한 raw metadata 유지
- 산업 일반 뉴스의 company risk 승격 차단

#### YFinance

- calculated valuation을 primary selected-date valuation으로 명시
- provider-direct를 reference layer로 분리
- evidence family 단위로 market claim 중복 축약

#### 완료 기준

- Strategy candidate card의 각 핵심 fact가 날짜, 기간, 단위, scope, evidence role을 가진다.
- News candidate card가 대표 snippet, 기사 수, 고유 publisher 수와 중복 제거 기사 수를 가진다.
- 원천 evidence ID와 상세 경로는 domain evidence store에 보존되며 LLM-facing card 내용과 분리된다.

### Phase 2. Peer pair builder와 v2 compact packet 기반 구축

#### 작업

- 45개 scalar를 비교 가능한 pair object로 변환한다.
- 날짜와 기간 metadata를 제거하지 않는다.
- scale, rate, margin, valuation, market-relative 비교 type을 구분한다.
- `compact_strategy_valuation()`에 calculated valuation을 포함한다.
- 공통 card envelope에 semantic `card_key`, `allowed_sections`, `evidence_role`, primary observation, secondary context, reader/machine limitation을 구현한다.
- 다른 도메인의 요약은 `secondary_context[].usage = framing_only`로 유지하고 primary evidence와 합치지 않는다.
- `section_inputs`를 결정론적으로 만들고 모든 배치가 card의 `allowed_sections` 안에 있는지 검사한다.
- Financial 4~6개(다사업 예외 최대 7개), News 0~8개(critical overflow 최대 10개), Market 0~3개, Valuation 0~2개, Peer 0~6개의 적응형 budget을 적용한다.
- reader-facing limitation은 0~8개로 통합하되 comparability와 validity에 필요한 machine limitation/blocker에는 상한을 두지 않는다.
- 같은 evidence family는 한 방향에서 한 번만 포함하고 반대 근거가 존재하면 상한 처리 과정에서 모두 제거하지 않는다.
- Strategy LLM 입력에는 opaque evidence ID를 넣지 않고 card의 실제 수치·날짜·범위·대표 발췌를 넣는다.
- `emit_strategy_v2_shadow_artifacts=true`일 때 `strategy_compact_packet_v2.json`과 `strategy_packet_provenance_v2.json`을 추가 생성한다.
- provenance map은 `card_key`별 원천 evidence ID, source path와 `strategy_card_sha256`를 기록한다.
- packet preflight telemetry에 영역별 card 수, JSON bytes, 전체 추정 input tokens와 overflow 여부를 기록한다.
- Phase 2에서는 기존 v1 packet과 Content Planner 정상 경로를 유지한다. v2 전체 전환과 Planner 제거는 Phase 6에서 수행한다.

#### 완료 기준

- SK바이오팜 peer valuation은 2025-10-30 calculated P/E 26.97배 대 13.48배로 비교된다.
- 성장 비교는 64.68% 대 -0.77%를 사용한다.
- target/peer 날짜나 basis가 다르면 pair가 `incomparable`로 표시된다.
- compact packet 데이터 부분이 현재 packet보다 유의하게 작아지고 필수 evidence axis는 모두 보존된다.
- 모든 v2 card가 유일한 `card_key`를 가지며 `section_inputs`, provenance map과 content hash가 완전하게 연결된다.
- v2 packet fixture는 opaque raw evidence ID 없이도 각 card의 의미를 독립적으로 이해할 수 있다.
- Phase 3~5가 완료되기 전에는 기존 v1 정상 경로가 계속 통과한다.

### Phase 3. Strategy output의 typed decision contract 적용

#### 작업

- prose-only pairwise finding을 typed `peer_findings`로 교체한다.
- `decision_risk_factors`를 News risk와 valuation/market/financial blocker까지 포함하도록 분리한다.
- catalyst와 reported expectation을 구분한다.
- evidence sufficiency를 coverage rubric으로 검증한다.
- evidence sufficiency와 Buy/Hold/Sell 방향을 독립 필드와 독립 판단 기준으로 유지하고, 데이터 누락만으로 Hold를 선택하지 못하게 한다.
- Strategy의 decision factor, evidence assessment, peer finding과 risk factor가 유효한 semantic `card_key`를 반환하게 한다.
- Strategy가 핵심 card별 `interpretation`과 `investment_effect`를 소유하고, Writer가 이를 다시 판단하지 않도록 계약을 고정한다.
- 각 Strategy 결과가 참조한 `card_key`가 해당 결과 section의 `allowed_sections` 안에 있는지 검증할 수 있게 한다.
- machine limitation/blocker가 comparability를 막으면 해당 card를 결정 근거로 사용하지 못하게 한다.
- Strategy report의 prose는 typed object와 같은 호출에서 계속 생성한다.
- v2 Strategy는 feature flag 경로에서만 실행하고 v1 정상 경로를 아직 교체하지 않는다.

#### 완료 기준

- Strategy가 size를 growth로 해석하거나 날짜가 다른 valuation을 비교할 수 없다.
- 같은 제네릭 사건이 risk에 한 번만 존재한다.
- Hold의 핵심 negative factor와 Writer용 risk card가 연결된다.
- 모든 결정적 긍정·부정 factor와 risk가 입력에 존재하는 `card_key`로 역추적된다.
- 핵심 evidence의 관찰값, Strategy 해석과 투자 영향이 구분된 typed field로 존재한다.
- 동일한 충분도에서 긍정·부정 근거를 대칭적으로 바꾼 fixture가 특정 방향, 특히 Hold로 고정되지 않는다.

### Phase 4. Writer editorial packet 재구성

#### 작업

- Writer 입력에서 전체 peer metrics, 중복 하위 Agent 해석, 불필요한 55개 ref map을 제거한다.
- Strategy가 승인한 decision/evidence/catalyst/risk/limitation card만 전달한다.
- `required_card_keys_by_component`를 결정론적으로 생성해 thesis, Key Evidence Table과 risk matrix의 필수 card 집합을 명시한다.
- Writer prompt에서 `grounding_ref_map`, OP ID와 원천 evidence ID를 제거하고 provenance는 별도 artifact에 저장한다.
- Writer card에는 Strategy가 승인한 semantic `card_key`, observation, `strategy_interpretation`, `investment_effect`를 함께 전달한다.
- Writer는 문장 구성과 압축만 담당하고 Strategy 해석이나 투자 영향의 방향을 새로 결정하지 않는다.
- Writer 응답은 사용한 `card_key`를 hidden metadata로 반환하고 renderer는 독자용 본문에 이를 노출하지 않는다.
- `writer_packet_provenance_v2.json`에서 Writer card -> Strategy card -> 원천 evidence의 연결을 보존하고 `source_strategy_card_sha256`와 `writer_editorial_card_sha256`를 분리해 기록한다.
- `required_key_evidence`의 exact-token-once 지시를 axis coverage 방식으로 교체한다.
- Key Evidence Table을 5~8개 evidence axis로 구성한다.
- risk matrix는 typed risk card의 의미와 방향을 유지한 상태에서 Writer가 문장만 다듬는다.
- 투자기간, 선택일 계산 valuation, 제품 표 scope를 필수 reader-facing 항목으로 지정한다.
- v2 Writer는 feature flag 경로에서만 실행하고 Phase 5 검증 전에는 정상 경로를 전환하지 않는다.

#### 완료 기준

- 제품 3개와 valuation 3개가 각각 하나의 통합 행에 들어간다.
- 회사명 단독 행과 내부 field name이 존재하지 않는다.
- Writer는 원본 45개 peer metric을 다시 해석하지 않는다.
- Writer가 Strategy의 해석 또는 투자 영향 방향을 바꾼 payload는 validation에서 실패한다.
- Writer가 사용한 모든 card가 semantic `card_key`와 외부 provenance map으로 역추적된다.
- Writer input tokens가 줄어들되, 축소 자체보다 필수 evidence axis coverage를 우선한다.

### Phase 5. 결정론적 의미 validator 적용

#### 작업

- Gate A/B/C를 각각 독립 함수로 구현한다.
- Gate A는 packet schema, 기간·단위·scope, comparability, card budget과 machine blocker를 검증한다.
- Gate B는 Strategy가 참조한 `card_key`, `allowed_sections`, observation/interpretation/effect 구조와 provenance hash를 검증한다.
- Gate C는 Writer card coverage, Strategy 해석·투자 영향 보존, thesis-risk 정렬과 hidden `card_key` 반환을 검증한다.
- 기존 HTML validator는 렌더링 검증 역할만 유지한다.
- 의미 validator 실패 시 새 LLM 호출로 repair하지 않고 실행을 명확히 실패 처리한다.
- 실패 결과에는 `packet_path`, `card_key`, semantic axis/metric key, `reason`, source dates/bases를 기록한다.
- 결정론적 validator가 자유 한국어 문장의 모든 의미를 판별할 수 있다고 가정하지 않는다. 핵심 해석은 Strategy typed field로 고정하고 Writer는 이를 보존하며, 나머지 문체 수준의 의미 변형은 회귀 fixture와 정성 평가의 잔여 위험으로 관리한다.

#### 완료 기준

- 현재 `writer_validation_report.json`에서 pass한 다음 오류를 새 validator가 탐지한다.
  - `required_key_evidence` 노출
  - 매출 규모 -> 성장 우위
  - 9월 target P/E와 10월 peer P/E 혼합
  - investment horizon 누락
  - Strategy negative factor와 risk matrix 불일치
- 존재하지 않는 `card_key`, section 범위를 벗어난 참조, content hash 불일치가 각각 명시적 사유로 실패한다.
- 열거된 typed·구조적 의미 오류는 추가 Review/Repair LLM 없이 차단된다.

### Phase 6. v2 전체 전환과 운영 계약 수정

#### 작업

- 동일 fixture로 v1과 v2 Strategy/Writer 결과를 병행 생성해 필수 evidence axis와 투자 방향의 누락을 비교한다.
- Phase 2~5의 schema, provenance, semantic validation이 모두 통과한 뒤에만 정상 경로를 `strategy_packet_version=v2`로 전환한다.
- v2 정상 경로에서 Content Planner 호출과 기존 ID 중심 packet 전달을 제거한다.
- cold-cache 정상 예상 호출 수를 15회에서 14회로 변경한다.
- `src/orchestration/usage_summary.py`의 final role 예상 호출 수를 3회에서 2회로 변경하고 관련 테스트 기대값을 함께 수정한다.
- orchestration manifest, dependency graph, source-file 목록, cache fingerprint, output path와 README/workflow 문서를 v2 계약에 맞춘다.
- v1/v2 cache가 섞이지 않도록 모든 fingerprint에 packet/contract version을 포함한다.
- 전환 회귀가 끝날 때까지 v1 artifact를 보존하고, 이후 기존 planner cache와 decision-basis artifact를 deprecated 처리한다.
- feature flag로 v1 경로를 제한적으로 되돌릴 수 있게 하되 v1/v2 artifact를 하나의 downstream 실행 입력으로 혼합하지 않는다.

#### 완료 기준

- v2 정상 경로에 Content Planner LLM 호출이 없고 cold-cache 기대 호출 수가 정확히 14회다.
- usage summary, manifest, cache와 output 계약이 실제 호출 구조와 일치한다.
- v2 Strategy와 Writer artifact만으로 최종 보고서와 원천 provenance를 모두 재구성할 수 있다.
- 전환 시점까지 v1 정상 경로 테스트가 유지되고, 전환 후에는 stale v1 artifact가 v2 입력으로 사용되지 않는다.

### Phase 7. 전체 회귀 테스트

#### 테스트 범위

- Financial/News/YFinance 단위 테스트
- peer pair builder 테스트
- Strategy content selection 및 semantic contract 테스트
- Writer payload 및 HTML validator 테스트
- `SK바이오팜_20251031` 전체 파이프라인 재실행
- 데이터 누락, 적자 기업, 제품 표 미공시, peer 일부 metric 누락 fixture
- 뉴스가 거의 없는 기업, 이벤트가 많은 기업, 다사업 일반기업 fixture
- 동일한 evidence sufficiency에서 긍정·부정 근거를 대칭적으로 바꾼 recommendation-bias fixture
- compact packet과 provenance map의 path coverage 테스트
- v1/v2 cache isolation과 cold-cache 14회 호출 계약 테스트
- Strategy 해석과 Writer 표현의 card별 의미 보존 테스트

#### 품질 비교 지표

- 필수 evidence axis coverage
- unsupported interpretation 수
- invalid date/basis comparison 수
- 중복 event/evidence family 수
- 내부 identifier 노출 수
- thesis와 risk matrix의 핵심 factor 정렬 여부
- Writer input 중 최종 보고서에 사용되지 않은 card 비율
- LLM 호출 수와 token 사용량
- evidence sufficiency별 Buy/Hold/Sell 전이와 누락 데이터의 Hold 자동 귀결 여부

#### 최종 완료 기준

1. 추가 Review/Repair LLM 호출 없이 동작한다.
2. 모든 LLM 호출이 usage summary에 집계되고 cache hit/miss와 v1/v2 contract version이 구분된다.
3. 기준일 이후 데이터가 사용되지 않는다.
4. 제품 표 scope가 불일치하면 전체 매출 구성으로 표현하지 않는다.
5. peer 비교는 동일 metric, 동일 단위, 동일 기간·날짜에서만 수행한다.
6. 선택일 계산 valuation이 primary이고 provider-direct는 참고로 분리된다.
7. Strategy의 결정적 긍정·부정 근거가 Writer thesis와 risk matrix에 일관되게 반영된다.
8. Key Evidence Table에 내부 metadata 행, 회사명 단독 행, token별 중복 행이 없다.
9. 현재 보고서에서 열거한 typed·구조적 의미 오류를 validator가 모두 차단하며, 자유 문장 의미의 잔여 위험은 별도 정성 회귀로 기록한다.
10. 기존 전체 테스트와 새 의미 회귀 테스트가 모두 통과한다.
11. Strategy와 Writer LLM packet에 opaque raw evidence/claim/opinion ID가 없고, 모든 self-contained card에는 semantic `card_key`가 있다.
12. 데이터가 적은 기업에서 최소 card 수를 억지로 채우거나 데이터 누락만으로 Hold를 선택하지 않는다.
13. 중요 반대 사건이 News 상한이나 기사 수 부족 때문에 제거되지 않는다.
14. 기사 count가 투자 방향 점수로 사용되지 않는다.
15. 외부 provenance map이 모든 `card_key`를 원천 ID·source path·content hash로 추적한다.
16. machine limitation/blocker가 reader-facing 상한 때문에 삭제되지 않는다.
17. Content Planner 제거 후 cold-cache 정상 경로가 14회 호출이며 Strategy input token이 telemetry상 감소한다.

## 10. 우선순위

| 우선순위 | 작업 | 이유 |
| --- | --- | --- |
| P0 | v2 card·provenance 계약과 semantic `card_key` | 이후 Strategy, Writer와 validator가 공유할 기준 계약 |
| P0 | selected-date valuation과 peer valuation 날짜 정합성 | 현재 투자 판단의 핵심 배수가 잘못 비교됨 |
| P0 | peer comparable pair 구조 | size -> growth 오류를 구조적으로 차단 |
| P0 | 제품 매출 표 scope reconciliation | 회사 전체 매출 구성에 대한 과대 해석 방지 |
| P0 | Strategy typed decision과 해석 소유권 | Writer 이전에 판단 의미를 고정하고 재해석을 차단 |
| P1 | Gate A/B/C 결정론적 validation | 확정된 v2 계약의 참조·비교·의미 보존 오류 차단 |
| P1 | Writer evidence axis 계층화 | 표 문구와 정보 밀도 개선 |
| P1 | thesis-risk alignment | Hold 근거와 risk matrix 일치 |
| P1 | News materiality/company specificity | 산업 일반 뉴스의 회사 risk 승격 방지 |
| P1 | Content Planner 제거와 운영 마이그레이션 | v2 end-to-end 검증 후 13,703-token 호출과 관련 계약을 함께 제거 |
| P2 | Financial normalized peer metrics | 규모가 다른 기업의 질적 비교 보강 |
| P2 | 섹션별 정보 밀도 기준 | one-paper 범위에서 분석 깊이 회복 |

## 11. 이번 진단에서 수정하지 않은 항목

이 문서 작성 단계에서는 코드와 기존 산출물을 수정하지 않는다. 다음 항목도 범위에 추가하지 않는다.

- 시장점유율 신규 수집
- 컨센서스와 목표주가
- View change 조건
- 복수 peer 또는 업종 평균
- 은행·금융업 특수 스키마
- 연결 재무제표용 별도 처리
- Review Agent 또는 Repair Agent
- 규칙 기반 투자 문장 생성

다음 작업은 Phase 0의 실패 fixture와 기대 violation contract를 먼저 고정한 뒤 v2 공통 계약을 기준으로 P0 항목부터 순차적으로 수행한다. 정상 경로 전환과 Content Planner 제거는 Phase 2~5가 모두 통과한 뒤 Phase 6에서 수행한다.

## 11. 구현 및 검증 결과

2026-07-12 기준으로 v2 정상 경로 전환과 1개월 cold-cache 회귀 실행을 완료했다.

### 구현 완료

- Financial statement scope, 제품 매출 reconciliation과 normalized margin 계약 추가
- News event status, company specificity, materiality, financial link와 기사 coverage metadata 추가
- target/peer scalar를 동일 metric·단위·기간·날짜의 comparable pair card로 변환
- Financial 6, News 5, Market 3, Valuation 2, Peer 5개의 self-contained Strategy card 생성
- raw evidence ID를 LLM packet에서 제거하고 content hash가 있는 외부 provenance map으로 분리
- Content Planner 제거, Strategy Decision 단일 호출과 dynamic strict schema 적용
- 중복 direction, factor와 section routing은 typed assessment에서 결정론적으로 도출
- Gate A/B에서 card budget, eligibility, allowed section, factor effect, peer basis·direction과 provenance 검증
- Writer 입력을 18개 editorial card 합집합으로 축소하고 Strategy interpretation/effect를 고정
- Key Evidence Table을 8개 evidence axis, risk matrix를 Strategy typed risk 5개 행으로 구성
- Writer 표시값을 억원·%·배·공시 단위로 사전 정규화하고 semantic card key는 HTML에서 숨김
- Gate C에서 component card coverage, Strategy 의미 보존, risk 정렬과 identifier leakage 검증
- Review/Repair 호출 없이 semantic failure를 명시적으로 실패 처리
- 검증 실패 후 동일 raw Writer 응답을 fingerprint로 재검증할 수 있는 response cache 추가
- v2 성공 후 planner, decision-basis와 v1 Writer handoff/cache artifact 자동 정리
- 전체 파이프라인, usage summary, manifest, README와 Agent workflow를 v2 계약으로 전환

### 1개월 cold-cache 회귀

- 입력: SK바이오팜, 2025-10-31 장 시작 전, 뉴스 1개월
- 비교기업: 일성아이에스
- 정보 cutoff: 2025-10-30
- cold-cache 실행 최종 의견: Hold, 6~12개월, evidence sufficiency high
- 호출: target 6 + peer 6 + Strategy 1 + Writer 1 = 14
- transport: 14회 성공, 오류·retry 0회
- tokens: input 127,131, output 29,338, total 156,469
- 최대 단일 입력: 28,214 tokens, 100K target 초과 0건
- Strategy data packet: 30,576 bytes, 추정 7,900 tokens
- Strategy 실제 input: 13,100 tokens
- Writer 실제 input: 12,917 tokens
- Writer validation: 전체 check pass, notes 0건
- 관련 Agent 회귀 테스트: 188 passed

실행 manifest는 `Output_total/runs/SK바이오팜_20251031/executions/20260712T021706081119Z/`에 있고 최종 보고서는 `Output_total/Writer/SK바이오팜_20251031/report.html`에 있다.

cold-cache 실행 후 제품 표 reconciliation이 `partial`인데 일부 문장이 회사 전체 매출 비중처럼 읽힐 수 있는 잔여 문제를 발견했다. Strategy assessment와 product-basis risk에 `주요 제품·서비스 공시표 기준` scope qualifier를 추가하고 Gate B/C에 필수 검사를 적용했다. 당시 final-stage 재실행 결과는 Buy였으나, 아래 범용 비교·추천 bridge 계약을 적용한 현재 결과와는 구분한다.

### 범용 비교·추천 bridge 보완

- 시장지수, selected peer, 회사 과거와 industry aggregate를 `comparison_scope`로 분리했다.
- KOSPI와 실제 비교기업명을 typed metadata로 전달하고 업종·동종 비교로 변형하는 출력을 Gate B/C에서 차단했다.
- `evidence_family` 기준으로 Financial, peer, market, valuation의 동일 기초 사실이 독립 factor로 중복 집계되지 않게 했다.
- News event를 `confirmed_financial`, `probable_financial`, `occurrence_only`, `operational_context`로 분리하고 context-only 사건을 forward factor에서 제외했다.
- data coverage와 decision confidence를 분리하고, Buy/Sell은 서로 다른 forward evidence family가 최소 2개 필요하도록 했다.
- LLM이 만들던 중복 `strategy_report` 자유문장을 제거하고 typed decision을 `strategy_report.json/.md`로 구조적으로 투영한다.
- Writer thesis는 검증된 recommendation bridge를 사용하고 Key Evidence Table과 risk matrix는 결정론적으로 생성한다.
- 문장별 `_claim_units`, point-in-time 추세 오판, component 외 카드 사용과 5개 필수 limitation category를 Gate C에서 검사한다.
- selected peer는 기업명 고정 없이 처리하며 복수 selected peer pair도 동일 계약으로 만들 수 있다. industry aggregate로 일반화하지 않는다.
- 현재 SK바이오팜 final-stage 결과는 Hold, data coverage high, decision confidence medium이며 Writer validation은 notes 없이 pass다.
- 전체 회귀 테스트는 207개를 기준으로 다시 실행한다.

### 잔여 위험

- 결정론적 Gate는 typed field와 구조적 의미를 강하게 보장하지만 자유 한국어 문장의 모든 뉘앙스까지 판별하지 않는다.
- 이번 실행의 최대 입력은 target News analysis 28,214 tokens였다. context limit 문제는 없지만 다음 token 최적화 우선순위는 News period summary와 News analysis다.
- 데이터가 희소한 기업, 적자 기업, 제품 표 미공시 기업과 peer metric 일부 누락 기업은 fixture 회귀를 계속 유지해야 한다.
