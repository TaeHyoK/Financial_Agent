# Financial/Strategy/Writer 전면 보완 계획

## 1. 목적

기업명과 기준일만 입력하면 특정 산업에 종속되지 않은 투자 보고서를 생성하도록 전체 데이터 흐름을 보완한다.

최종 데이터 흐름은 다음과 같다.

```text
기업명 + 기준일
  -> 기준일 시점에 공개된 DART 공시
  -> 기준일까지의 News
  -> 기준일까지의 YFinance 시장 데이터
  -> 네이버 종목분석에서 비교 기업 1개 식별
  -> 비교 기업의 DART/News/YFinance 신규 수집
  -> Strategy의 중립적인 Buy/Hold/Sell 판단
  -> Writer용 계층화 handoff
  -> 최종 보고서
```

이번 보완의 핵심은 다음 네 가지다.

1. 기준일 이후 공개된 자료가 섞이지 않도록 point-in-time 정합성을 보장한다.
2. 과거 재무 추세, 제품·서비스별 매출 구성, 밸류에이션을 추가한다.
3. 현재 SK바이오팜 실행의 비교 기업은 일성아이에스 하나로 새로 수집한다.
4. Strategy와 Writer의 고정 프롬프트 및 스키마를 산업 중립적으로 정리한다.

## 2. 확정 원칙

### 2.1 데이터 원칙

- 모든 원천 데이터는 `source_as_of <= selected_date`를 만족해야 한다.
- DART 접수 시각을 확보하지 못한 현재 계약에서는 `rcept_dt < selected_date`인 공시만 사용한다. 따라서 접수 당일 공시는 다음 날부터 사용할 수 있다.
- 기준일에 목표 분기 보고서가 아직 제출되지 않았다면 직전의 사용 가능한 정기보고서로 후퇴한다.
- YTD, 분기 단독, 연간, 시점 잔액을 서로 다른 기간 기준으로 관리한다.
- 기간 기준이 다른 수치로 성장률을 계산하지 않는다.
- 원천 수집과 수치 계산은 결정론적으로 처리한다.
- 투자 문장과 정성적 해석은 LLM이 근거를 보고 생성한다.
- 데이터가 없으면 추정하지 않고 `not_available`과 사유를 전달한다.

### 2.2 범용성 원칙

- SK바이오팜, 제약·바이오, FDA, 임상, 제네릭 같은 사례를 고정 프롬프트에 넣지 않는다.
- 일성아이에스는 이번 SK바이오팜 실행의 선택 결과이지 전역 하드코딩 값이 아니다.
- 기업마다 제품, 서비스, 사업부문, 지역 등 매출 구분 방식이 다를 수 있음을 스키마에 반영한다.
- 데이터 미제공은 곧바로 Hold를 의미하지 않는다.
- 투자의견과 근거 충분도를 별도 필드로 관리한다.

### 2.3 명시적 제외 범위

- 시장점유율
- 컨센서스
- 목표주가
- 증권사 투자의견 평균
- Buy/Hold/Sell 변경 조건
- View change 조건
- Review Agent와 Repair Agent의 재도입
- 규칙 기반 투자 문장 생성

결정론적 공시 선택, 테이블 파싱, 단위 변환, 기간 비교 가능성 판정, 재무비율 계산은 문장 생성 규칙이 아니므로 허용한다.

## 3. 현재 확인된 핵심 문제

### 3.1 기준일 이후 DART 공시 사용

현재 Financial Agent는 입력 기준일이 아니라 실행 당일을 DART 검색 종료일로 사용한다. 또한 이론상 가장 가까운 분기를 고른 뒤 해당 보고서 한 건만 수집한다.

`2025-10-31` 기준 SK바이오팜 사례는 다음과 같다.

- 2025년 3분기 보고서 제출일: `2025-11-14`
- 2025-10-31 당시 최신 사용 가능 보고서: `2025-08-14` 제출 반기보고서
- 기존 테스트 산출물: 기준일 이후 제출된 3분기 보고서를 사용

따라서 기존 1개월 산출물은 투자 판단 품질을 평가하기 전에 point-in-time 기준으로 다시 생성해야 한다.

### 3.2 과거 기간 제거

- `main.py` 실행 경로는 `resolve_primary_report()`만 사용한다.
- 과거 보고서용 resolver와 canonical builder 일부는 존재하지만 실행 경로에서 빠져 있다.
- 현재 정규화는 손익계산서와 현금흐름표의 전년 동기 비교열을 제거한다.
- `dart_2y_handoff.json`이라는 이름과 달리 실제 산출물은 단일 기간이다.

### 3.3 사업 정보 미수집

현재 XML 추출기는 `4. 재무제표`만 읽는다. 정기보고서의 `주요 제품 및 서비스`에 있는 제품·서비스별 매출액과 비중은 Strategy에 전달되지 않는다.

### 3.4 YFinance의 시장 데이터 한정

현재 YFinance Agent는 OHLCV, KOSPI, 환율, 기술 지표만 수집한다. 설치된 yfinance가 제공하는 valuation measure는 사용하지 않는다.

### 3.5 경쟁사 선택과 비교 범위

현재 산출물에는 더블유에스아이와 위더스제약이 들어 있다. 네이버 종목분석의 `업종분석 > 펀더멘털 비교 > FG000 경쟁사 비교`에는 일성아이에스 `003120`이 포함되며, 이번 실행은 일성아이에스 하나만 사용해야 한다.

### 3.6 Strategy와 Writer 전달 구조

- Strategy 고정 프롬프트가 Hold를 우선하도록 작성돼 있다.
- 고정 프롬프트에 특정 산업 및 특정 기간 예시가 포함돼 있다.
- 검증 과정의 내부 지시문과 `SY revise:` 문장이 Strategy 제약으로 유입된다.
- `opinion_index`가 보고서 대부분을 중복하며 Writer context를 크게 차지한다.
- Writer의 마지막 섹션이 불필요한 View change 조건을 요구한다.

## 4. 목표 데이터 계약

### 4.1 Financial handoff

```json
{
  "as_of_context": {
    "selected_date": "YYYY-MM-DD",
    "latest_available_filing": {
      "report_type": "half",
      "period_end": "YYYY-MM-DD",
      "receipt_date": "YYYY-MM-DD",
      "receipt_no": "string"
    },
    "future_filing_excluded": true
  },
  "financial_trends": {
    "current_period": {},
    "same_period_previous_year": {},
    "annual_history": [],
    "ttm": {},
    "comparison_availability": []
  },
  "revenue_breakdown": {
    "status": "available",
    "dimension_type": "product_service",
    "period": {},
    "items": []
  }
}
```

`revenue_breakdown.dimension_type`은 `product`, `service`, `product_service`, `segment`, `region`, `other` 중 공시 구조에 맞게 정한다.

### 4.2 Valuation handoff

```json
{
  "valuation": {
    "status": "available",
    "valuation_as_of": "YYYY-MM-DD",
    "price_as_of": "YYYY-MM-DD",
    "metrics": {
      "market_cap": null,
      "trailing_pe": null,
      "price_to_book": null,
      "price_to_sales": null,
      "enterprise_value": null,
      "ev_to_ebitda": null
    },
    "sources": [],
    "derivations": [],
    "limitations": []
  }
}
```

- 적자 또는 분모가 유효하지 않은 배수는 `null`로 둔다.
- EV/EBITDA는 모든 구성값과 기간 기준이 유효할 때만 계산한다.
- 현재 시점의 `Ticker.info` 값을 과거 기준일 보고서에 사용하지 않는다.

### 4.3 Peer handoff

```json
{
  "peer_selection": {
    "provider": "naver_wisereport",
    "selection_mode": "single_peer",
    "selected_peer": {
      "company_name": "일성아이에스",
      "stock_code": "003120",
      "ticker": "003120.KS"
    },
    "selection_basis": "closest_market_cap_among_fg000_peers",
    "collected_at": "timestamp"
  },
  "peer_comparison": {
    "comparison_scope": "pairwise_only",
    "financial": {},
    "market": {},
    "valuation": {},
    "business_context": {},
    "limitations": []
  }
}
```

네이버는 비교 기업 식별에만 사용한다. 비교 수치와 투자 근거는 새로 수집한 DART, News, YFinance 산출물만 사용한다.

### 4.4 Writer handoff

Writer에는 전체 audit ledger를 그대로 넣는 대신 다음 계층을 갖는 압축 handoff를 전달한다.

```json
{
  "decision": {},
  "decisive_positive_evidence": [],
  "decisive_negative_evidence": [],
  "contrary_evidence": [],
  "business_context": {},
  "financial_trend": {},
  "revenue_breakdown": {},
  "valuation": {},
  "market_context": {},
  "peer_comparison": {},
  "catalysts": [],
  "risks": [],
  "data_limits": [],
  "evidence_refs": []
}
```

`decision_basis_by_section`은 감사·추적용 산출물로 유지할 수 있지만 Writer의 주 입력은 위 handoff로 변경한다.

## 5. 단계별 구현 계획

## Phase 0. 기준선 고정 및 회귀 fixture 준비

상태: `[x] 완료`

### 작업

- 현재 코드와 1개월 산출물의 구조를 기준선으로 기록한다.
- SK바이오팜 `2025-10-31`을 point-in-time 회귀 fixture로 지정한다.
- 다음 DART 공시를 테스트 fixture로 고정한다.
  - 2024 사업보고서: `2025-03-18` 제출
  - 2025 1분기보고서: `2025-05-15` 제출
  - 2025 반기보고서: `2025-08-14` 제출
  - 2025 3분기보고서: `2025-11-14` 제출
- 네이버 FG000 응답과 yfinance valuation 응답은 네트워크 변화에 영향받지 않도록 테스트 fixture를 만든다.
- 구현 중 수정 대상 파일과 기존 사용자 변경을 다시 확인한다.

### 완료 조건

- 기준일 전후 공시 선택을 재현하는 테스트 입력이 준비돼 있다.
- 네트워크 없이도 parser와 selector 단위 테스트를 수행할 수 있다.

## Phase 1. Financial Agent point-in-time 및 과거 추세

상태: `[x] 완료`

### 작업

1. 공시 검색 상한을 `selected_date`로 고정한다.
2. 목표 보고서가 기준일까지 제출되지 않았으면 직전 정기보고서로 후퇴하는 selector를 만든다.
3. 최신 보고서, 전년 동기, 최근 3개 연간 보고서를 수집한다.
4. 최신 보고서 안의 비교열을 보존한다.
5. 재무상태표, 손익계산서, 현금흐름표별 기간 기준을 명시한다.
6. 동일 기간끼리만 증감률을 계산한다.
7. `FY + current YTD - prior-year same YTD`가 모두 존재할 때만 TTM을 계산한다.
8. 각 수치에 공시번호, 제출일, 기간 종료일, 연결/별도 기준을 붙인다.
9. Financial analyst 및 SY 입력 스키마가 추세 데이터를 보존하도록 수정한다.
10. 기존 `dart_master`, `dart_main`, `dart_lightweight`, `final_report` 계약을 새 기간 구조에 맞춘다.

### 예상 수정 영역

- `src/Agent_Team/Financial_Agent/main.py`
- `src/Agent_Team/Financial_Agent/report_resolver.py`
- `src/Agent_Team/Financial_Agent/report_selector.py`
- `src/Agent_Team/Financial_Agent/normalizer.py`
- `src/Agent_Team/Financial_Agent/handoff_builder.py`
- `src/Agent_Team/Financial_Agent/financial_index_calculator.py`
- Financial Agent schema, prompt, tests

### 필수 테스트

- `selected_date=2025-10-31`이면 2025 반기보고서가 최신 공시다.
- `2025-11-14` 당일 실행에서는 2025 3분기보고서가 배제된다.
- `2025-11-15`부터 2025 3분기보고서가 선택된다.
- 같은 기간의 전년 동기 값이 유지된다.
- 반기 YTD와 전년도 연간을 YoY로 계산하지 않는다.
- 수정공시가 여러 건이면 기준일까지 제출된 최신 유효본을 선택한다.

### 완료 조건

- 모든 Financial 핵심 수치에 기간과 공시 제출일이 있다.
- 미래 공시 사용 여부를 자동 검사하는 validation이 통과한다.
- 단일 기간 때문에 비어 있던 성장률과 추세가 동일 기간 자료가 있을 때 정상 생성된다.

## Phase 2. 주요 제품 및 서비스 매출 구성

상태: `[x] 완료`

### 작업

1. DART 원문 XML에서 사업 내용 영역을 추출한다.
2. `주요 제품 및 서비스`, `주요 제품 등의 현황` 등 의미가 같은 제목을 구조적으로 탐색한다.
3. 제목 문자열만 자르는 방식 대신 XML 노드와 HTML table parser를 사용한다.
4. 단위, 기간, 항목명, 매출액, 매출 비중을 정규화한다.
5. 제품이 아닌 사업부문·서비스·지역 구분도 같은 범용 스키마로 수용한다.
6. 공시된 여러 기간은 보존하되 서로 비교 가능한지 표시한다.
7. 합계와 항목 비중 합계를 검증한다.
8. 해당 표가 없는 기업은 실패시키지 않고 `not_disclosed`로 처리한다.
9. Financial final report와 Strategy handoff에 현재 매출 구성을 전달한다.

### 예상 수정 영역

- `src/Agent_Team/Financial_Agent/section_extractor.py`
- `src/Agent_Team/Financial_Agent/table_parser.py`
- 신규 business/revenue breakdown parser
- Financial Agent schema, prompt, tests

### 필수 테스트

- SK바이오팜 2025 반기보고서에서 세노바메이트, 솔리암페톨, 기타의 매출액과 비중을 추출한다.
- 금액 단위가 원, 천원, 백만원인 표를 각각 정규화한다.
- 제품 표가 없는 fixture는 `not_disclosed`로 종료한다.
- YTD 금액과 연간 금액으로 성장률을 만들지 않는다.
- 시장점유율 필드는 생성하지 않는다.

### 완료 조건

- Writer가 주력 매출원과 집중도를 원천 근거와 함께 서술할 수 있다.
- 산업별 제품명을 코드나 고정 프롬프트에 추가하지 않는다.

## Phase 3. YFinance 밸류에이션

상태: `[x] 완료`

### 작업

1. `Ticker.get_valuation_measures()` 수집기를 추가한다.
2. 반환된 기간 중 `valuation_period <= selected_date`인 값만 남긴다.
3. YFinance 현재 스냅샷과 과거 기준일 데이터를 구분한다.
4. 기준일 종가는 기존 OHLCV에서 선택한다.
5. DART의 기준일까지 공개된 발행주식수, 자본, TTM 매출, TTM 순이익과 결합한다.
6. 가능한 경우 기준일 시가총액, P/E, P/B, P/S를 재계산한다.
7. EV와 EBITDA 구성값이 모두 유효할 때만 EV/EBITDA를 제공한다.
8. YFinance 직접값과 파생값이 모두 있으면 출처를 분리하고 차이를 validation한다.
9. 한국 종목에서 누락되는 필드는 `null`과 누락 사유를 전달한다.
10. 컨센서스, 목표주가, forward estimate 수집은 추가하지 않는다.

### 예상 수정 영역

- `src/Agent_Team/YFinance_Agent/pipeline.py`
- `src/Agent_Team/YFinance_Agent/reporting.py`
- `src/Agent_Team/YFinance_Agent/run_pipeline.py`
- YFinance Agent schema, prompt, tests
- 필요 시 Financial Agent의 발행주식수 수집 계약

### 필수 테스트

- 기준일 이후 valuation column이 제거된다.
- 과거 기준일에 현재 `Ticker.info` 값이 들어가지 않는다.
- 적자 기업의 P/E는 `null`이다.
- 분모가 없으면 배수를 생성하지 않는다.
- 직접값과 계산값의 차이가 허용 범위를 넘으면 강한 근거로 전달하지 않는다.

### 완료 조건

- Strategy가 현재 가격 수준을 실적·자본 대비로 설명할 수 있다.
- 밸류에이션 누락이 자동으로 Hold를 만들지 않는다.

## Phase 4. 네이버 비교 기업 식별 및 일성아이에스 신규 수집

상태: `[x] 완료`

### 작업

1. 네이버 `coinfo.naver`에서 WiseReport iframe URL을 찾는다.
2. `업종분석(c1060001)`의 `FG000 경쟁사 비교` header 데이터를 조회한다.
3. 대상 기업을 제외하고 유효한 국내 상장사 후보를 정규화한다.
4. 비교 기업 한 곳만 선택한다.
5. 기본 선택 규칙은 FG000 후보 중 대상과 시가총액 차이가 가장 작은 기업으로 한다.
6. 선택 결과, 후보 목록, 조회 시각, 선택 근거를 저장한다.
7. 외부 페이지 변경이나 조회 실패 시 명시적 `peer_unavailable`로 처리한다.
8. SK바이오팜 실행에서는 일성아이에스 `003120.KS`가 선택되는지 검증한다.
9. 일성아이에스의 DART corp code를 회사명/종목코드로 확인한다.
10. `일성아이에스_20251031`에 대해 Financial, News, YFinance를 모두 새로 실행한다.
11. 세 보고서가 완성된 뒤 Competitor Agent와 1:1 비교 산출물을 생성한다.
12. 더블유에스아이와 위더스제약을 SK바이오팜 Strategy 입력에서 제외한다.

### 예상 수정 영역

- 신규 peer resolver 모듈
- orchestration/config 생성 경로
- `src/Agent_Team/Competitor_Agent/agent.py`
- `src/Agent_Team/Competitor_Agent/peer_comparison.py`
- Competitor Agent schema, prompt, tests

### 필수 테스트

- 저장된 FG000 fixture에서 일성아이에스를 식별한다.
- 대상 기업 자신은 후보에서 제거된다.
- 시가총액 누락 후보에 대한 fallback이 결정론적이다.
- 네이버 조회 실패가 전체 대상 기업 보고서를 실패시키지 않는다.
- 일성아이에스 데이터도 `source_as_of <= 2025-10-31`을 만족한다.
- 결과 문구가 업계 전체 순위가 아니라 두 회사의 pairwise 비교로 제한된다.

### 완료 조건

- SK바이오팜 Strategy 입력의 경쟁사는 일성아이에스 한 곳뿐이다.
- 경쟁사 근거는 일성아이에스의 신규 Financial, News, YFinance 산출물을 참조한다.

## Phase 5. Strategy 판단 및 스키마 보완

상태: `[x] 완료`

### 구현 결과

- Strategy output contract를 `3.5`로 갱신했다.
- Content Plan을 `positive_evidence`, `negative_evidence`, `neutral_context`, `catalyst`, `pairwise`, `data_limits` 계층으로 정리했다.
- 위험은 범주별 필수 배열 대신 `observed_risks[{category, statement}]` 단일 목록으로 변경해 근거 없는 범주 채우기를 제거했다.
- 촉매는 `observed_catalysts` 단일 목록으로 변경해 같은 이벤트와 시장 반응의 중복을 줄였다.
- peer 비교는 양사에 모두 존재하는 자료만 `pairwise_findings`에 쓰고, 단독 자료와 누락 항목은 `comparison_limits`로 분리했다.
- `key_strengths`, `key_risks` 중복 요약을 제거하고 `decision_balance.positive_evidence/negative_evidence`로 통합했다.
- 선택일 계산 밸류에이션을 우선하고 날짜가 다른 provider-direct 값은 별도 문맥으로 분리했다.
- Strategy 기본 모델을 실제 지시 준수 비교 결과에 따라 `gpt-5.4`로 변경했다.
- validation workflow 상태는 `evidence_use=strong|context_only|exclude`로 변환해 내부 판정 문구가 Strategy 입력과 독자 문장에 유입되지 않게 했다.
- 전체 원 단위 정수는 input bundle에 정확히 존재하는 값인지 저장 전에 검증한다.
- Basis의 OP ID 오염, claim ID 유효성, 실제 input-bundle 경로 존재성을 구조적으로 검증한다.

### 검증 결과

- Strategy 단위 테스트: `18 passed`
- Financial/YFinance/Competitor/Strategy 관련 회귀 테스트: `84 passed`
- 실제 SK바이오팜 2025-10-31 결과: `Hold`, 투자기간 `6~12개월`, evidence sufficiency `medium`
- Strategy 의견 69건과 Basis 69건이 일치한다.
- source evidence 120건 중 118건은 실제 bundle 경로에 연결됐고, 나머지 2건은 정확한 경로를 특정할 수 없어 공란으로 유지했다.
- 잘못된 source path, 잘못된 claim ID, OP source ref, workflow 문구, 2025-11-14 미래 공시 참조, consensus/target price/View change 문자열은 모두 0건이다.

### 작업

1. 새 Financial trend, revenue breakdown, valuation, single-peer 데이터를 input bundle에 추가한다.
2. `SY revise:`, 내부 재작성 지시, 검증 수행 여부 같은 운영 문구를 decision constraint에서 제거한다.
3. 혼합 증거면 Hold를 우선하도록 한 고정 지시를 제거한다.
4. Buy/Sell에만 비대칭적으로 높은 증거 기준을 부과하지 않는다.
5. 데이터 충분도와 추천 의견을 분리한다.
6. 추천의 기본 투자 기간을 명시한다.
7. 고정 프롬프트의 특정 기업, 특정 산업, 특정 연도 예시를 범용 원칙으로 교체한다.
8. 핵심 판단 순서를 다음과 같이 정리한다.
   - 재무 추세와 현금흐름
   - 매출 구성과 집중도
   - 시장 반응
   - 밸류에이션
   - 최근 촉매와 리스크
   - 일성아이에스와의 1:1 비교
   - 반대 근거와 데이터 한계
9. 각 의견은 기존과 같이 source/evidence id로 추적 가능하게 유지한다.
10. `opinion_index`는 audit용 별도 산출물로 분리하거나 Writer 입력에서 제외한다.
11. 컨센서스, 목표주가, View change 관련 필드를 추가하지 않는다.
12. Content Planner -> Decision Agent -> 구조 검증 -> Basis Agent 흐름을 유지한다.

### 예상 수정 영역

- `src/Agent_Team/Strategy_Agent/agent.py`
- `src/Agent_Team/Strategy_Agent/prompts/content_planner.md`
- `src/Agent_Team/Strategy_Agent/prompts/decision_agent.md`
- `src/Agent_Team/Strategy_Agent/prompts/decision_basis_agent.md`
- Strategy Agent schema와 tests

### 필수 테스트

- 동일한 증거 강도의 긍정/부정 fixture가 Buy와 Sell에 대칭적으로 반영된다.
- 데이터 누락만으로 Hold가 선택되지 않는다.
- `evidence_sufficiency=low`인 Buy/Sell/Hold가 모두 스키마상 가능하다.
- 고정 프롬프트에서 기업명, 제품명, FDA, 임상, 제네릭, 특정 연도 예시가 검출되지 않는다.
- 투자 판단 문장이 rule-based helper에서 생성되지 않는다.
- Review/Repair 단계가 호출되지 않는다.

### 완료 조건

- 추천 결과가 사전 Hold 규칙이 아니라 전체 증거 비교에서 나온다.
- Strategy 결과만으로 Writer가 결정적 근거, 반대 근거, 한계를 구분할 수 있다.

## Phase 6. Writer handoff 및 보고서 구조 보완

상태: `[x] 완료`

### 구현 결과

- Strategy report, input bundle, basis를 `writer_handoff.json`으로 결정론적으로 압축한다.
- handoff는 판단, 결정적 긍정/부정 근거, 반대 근거, 사업·재무·제품 매출·valuation·시장·peer·촉매·위험·한계 계층을 갖는다.
- 전체 basis 문장 대신 OP id, Strategy path, claim/source/evidence id만 `evidence_refs`로 전달한다.
- Writer LLM context에서 32,000자 문자열 절단과 전체 audit ledger 전달을 제거했다.
- Writer LLM은 `gpt-5.4` 단일 호출만 사용하며 구조 오류 시 추가 LLM 호출 없이 실패한다.
- 6번째 섹션을 `data_limits`로 변경하고 판단 변경 시나리오 필드를 제거했다.
- `key_evidence_table`에 제품·서비스별 매출액과 비중, 선택일 P/E/P/S/P/B, 일성아이에스 1:1 비교를 의무화했다.
- 각 본문/표 item은 실제 Strategy OP id를 `grounding_refs`로 가져야 하며 섹션 도메인과 path도 일치해야 한다.
- 큰 원 단위 숫자, 제품 매출·비중, valuation 날짜/배수, peer 이름, 절대 경로, 내부 용어, 금지 콘텐츠를 저장 전에 검증한다.
- 텍스트 섹션은 1~2개 문단, bullet 0건, 전체 문자 budget을 적용해 one-paper 밀도를 유지한다.
- 실제 데이터가 아닌 SVG 차트 슬롯을 제거하고 A4 본문 가용 폭을 196mm로 확대했다.

### 검증 결과

- Writer 단위/계약 테스트: `14 passed`
- Financial/YFinance/Competitor/Strategy/Writer 관련 회귀 테스트: `98 passed`
- 실제 1개월 Writer 실행은 한 번의 LLM 호출로 완료됐다.
- 실제 HTML validation은 구조, A4, grounding, 제품 매출, valuation, peer, 숫자, 경로, 금지 콘텐츠, 한국어 기간 표기, 소수점 정밀도 검사를 모두 통과했고 notes는 0건이다.
- 실제 출력에는 일성아이에스만 4회 등장하고 더블유에스아이·위더스제약은 0회다.
- 미래 공시일, 절대 경로, 금지 필드, 임의 SVG 시각자료는 최종 payload/HTML에서 0건이다.

### 작업

1. Strategy 결과에서 Writer 전용 압축 handoff를 만든다.
2. audit용 전체 basis와 Writer용 근거 계층을 분리한다.
3. 중복 `opinion_index`를 Writer prompt context에서 제거한다.
4. 절단된 JSON 문자열을 전달하는 현재 32,000자 fallback 의존을 제거한다.
5. Writer가 사용할 근거 우선순위를 명시한다.
   - 결정적 근거
   - 보조 근거
   - 반대 근거
   - 데이터 한계
6. 제품·서비스별 매출 구성과 밸류에이션을 표와 본문에 반영한다.
7. 일성아이에스 비교를 1:1 비교로 작성한다.
8. 마지막 섹션의 View change 조건을 제거한다.
9. Writer의 6개 섹션을 다음과 같이 정리한다.
   - `investment_call_thesis`
   - `business_market_context`
   - `key_evidence_table`
   - `catalysts_execution`
   - `risk_monitoring_matrix`
   - `data_limits`
10. 수치, 제품명, 이벤트, 경쟁사 이름을 Writer가 새로 만들지 못하도록 grounding 검증을 유지한다.
11. 중복 근거와 절대 파일 경로를 최종 보고서에서 제거한다.

### 예상 수정 영역

- `src/Agent_Team/Writer Agent/html_report_writer.py`
- Writer prompt/schema/workflow 문서
- Writer Agent tests

### 필수 테스트

- 6개 최상위 섹션이 모두 존재하며 서로 중첩되지 않는다.
- `view_change_conditions` 문자열이나 필드가 생성되지 않는다.
- 제품별 매출과 비중이 Financial source와 일치한다.
- 밸류에이션 값과 기준일이 source와 일치한다.
- 일성아이에스 외 경쟁사 이름이 나오지 않는다.
- Writer 입력이 중간에서 잘린 JSON 문자열이 아니다.
- 근거 없는 수치와 이벤트를 validation이 차단한다.

### 완료 조건

- Writer가 audit ledger 전체를 재해석하지 않고 계층화된 근거로 보고서를 작성한다.
- 최종 글에서 판단, 근거, 반대 근거, 데이터 한계가 명확히 분리된다.

## Phase 7. 통합 실행 및 정성 평가

상태: `[x] 완료`

### 7.1 자동 검증

- Financial Agent 단위/통합 테스트
- YFinance Agent 단위/통합 테스트
- Competitor Agent 단위/통합 테스트
- Strategy Agent 단위/계약 테스트
- Writer Agent 단위/계약 테스트
- 전체 관련 pytest 실행
- JSON schema validation
- 미래 자료 사용 검사
- source/evidence id 참조 무결성 검사
- 최종 HTML validation

검증 결과:

- Financial 테스트 45개가 통과했다.
- Financial/YFinance/Competitor/Strategy/Writer/orchestration 전체 관련 테스트 101개가 통과했다.
- Visualization을 포함한 저장소 전체 테스트 111개가 통과했다.
- Writer와 Visualization의 동명 `data_loader`가 pytest 수집 순서에 따라 충돌하던 문제는 Writer I/O 모듈을 `writer_io`로 고유화해 해결했다.
- 실제 Strategy report, input bundle, basis card, section basis를 현재 validator로 다시 검증했다.
- 실제 Strategy 의견 69건과 section basis 69건이 일치했다.
- source evidence 120건 중 118건은 실제 input bundle 경로에 연결됐고, 경로가 명시되지 않은 2건을 제외한 invalid 경로는 0건이다.
- 실제 Writer handoff와 최종 HTML을 현재 validator로 다시 검증했으며 status는 `pass`, notes는 0건이다.

### 7.2 1개월 end-to-end 회귀 실행

대상:

```text
company: SK바이오팜
selected_date: 2025-10-31
news window: 1 month
peer: 일성아이에스 only
```

필수 확인값:

- SK바이오팜 최신 DART 공시는 2025 반기보고서다.
- 2025-11-14 제출 3분기보고서는 모든 입력과 근거에서 제외된다.
- 제품·서비스 매출 구성은 기준일까지 제출된 반기보고서에서 나온다.
- YFinance 가격 데이터는 2025-10-31을 넘지 않는다.
- valuation의 기준 기간과 source가 표시된다.
- 일성아이에스도 같은 기준일 규칙으로 새로 수집된다.
- Strategy 경쟁사는 일성아이에스 한 곳뿐이다.
- 컨센서스, 목표주가, View change 조건이 없다.
- 고정 산업 프롬프트 없이 보고서가 생성된다.

실행 결과:

- SK바이오팜과 일성아이에스 upstream run status는 모두 `success`다.
- 양사 최신 사용 공시는 2025-08-14 접수 반기보고서이며 2025-11-14 접수 3분기보고서는 사용되지 않았다.
- SK바이오팜 시장 데이터는 2025-10-01부터 2025-10-31까지 18거래일이며 최종 관측일은 선택일과 같다.
- 선택일 종가와 기준일까지 공개된 DART 수치로 P/E, P/S, P/B를 계산했고 provider-direct 2025-09-30 값과 분리했다.
- Strategy는 일성아이에스 한 곳만 비교 기업으로 사용했다.
- Strategy 결과는 `Hold`, 투자기간 `6~12개월`, 근거 충분도 `medium`이며 Writer 최종 HTML까지 생성됐다.

### 7.3 경계일 회귀 실행

세 날짜를 추가 검증한다.

1. `2025-11-13`: 3분기보고서 사용 불가
2. `2025-11-14`: 접수 시각이 없으므로 3분기보고서 사용 불가
3. `2025-11-15`: 3분기보고서 사용 가능

날짜만 관리하고 접수 시각을 수집하지 않는 경우, 당일 공시는 보수적으로 다음 날부터 사용하도록 정책을 고정한다.

검증 결과:

- selector와 report-set fixture에서 `2025-11-14 -> 2025 반기`, `2025-11-15 -> 2025 3분기` 경계를 고정했다.
- collection context의 정책명과 미래 공시 제외 판정도 같은 strict prior-day 기준을 사용한다.

### 7.4 정성 평가

다음 질문을 사람이 직접 읽고 판정한다.

- 추천이 데이터 누락 때문에 자동으로 Hold가 되지 않았는가?
- 재무 추세가 동일 기간 자료로 설명됐는가?
- 제품 매출 구성이 사업 설명과 투자 판단에 실제로 연결됐는가?
- 밸류에이션이 기준일 현재 공개 정보만 사용했는가?
- 일성아이에스 비교를 업계 전체 우위로 과장하지 않았는가?
- News, DART, YFinance의 역할이 섞이지 않았는가?
- Writer가 같은 근거를 여러 섹션에서 반복하지 않았는가?
- 데이터 한계가 내부 지시문이 아니라 독자용 설명으로 작성됐는가?

평가 결과:

- Hold는 데이터 누락을 기본값으로 삼은 결과가 아니라 실적·현금흐름 개선과 밸류에이션·집중도·상대성과 약세를 함께 비교한 결과다.
- 재무 추세는 2025 반기 누적과 2024 반기 누적, 연간 추세는 2024/2023/2022 연간끼리만 비교했다.
- 세노바메이트 95.1% 매출 집중도는 사업 설명, 핵심 근거, 위험에 실제로 연결됐다.
- 선택일 계산 밸류에이션과 날짜가 다른 provider-direct 값이 명시적으로 분리됐다.
- 일성아이에스 비교는 1:1 비교로 한정됐으며 업계 전체 우위로 일반화하지 않았다.
- DART는 재무·제품 구성, News는 사건, YFinance는 가격·밸류에이션 역할로 분리됐다.
- 최종 글은 요약-근거표-위험표의 필요한 반복만 남기고 각 본문을 1~2개 문단으로 제한했다.
- 데이터 한계는 회계 시차, 기간 비교 범위, 시장 지표의 해석 한계, 단일 피어 대표성으로 독자 관점에서 설명됐다.
- 중대 정성 결함은 발견되지 않았다.

### 완료 조건

- 관련 자동 테스트가 모두 통과한다.
- 1개월 end-to-end 실행이 오류 없이 완료된다.
- 미래 데이터 참조가 0건이다.
- 정성 평가의 중대 결함이 0건이다.

## 6. 구현 순서와 의존성

```text
Phase 0 fixture
  -> Phase 1 Financial point-in-time/trend
  -> Phase 2 revenue breakdown
  -> Phase 3 valuation
  -> Phase 4 peer selection + 일성아이에스 신규 수집
  -> Phase 5 Strategy
  -> Phase 6 Writer
  -> Phase 7 end-to-end 검증
```

- Phase 1이 끝나기 전에는 기존 1개월 Strategy 결과를 판단 기준으로 사용하지 않는다.
- Phase 2와 Phase 3은 Phase 1의 기간·공시 metadata 계약을 사용한다.
- Phase 4의 일성아이에스 신규 수집도 Phase 1~3이 반영된 파이프라인으로 실행한다.
- Strategy 수정은 새 target/peer 데이터 계약이 확정된 후 진행한다.
- Writer 수정은 Strategy handoff가 확정된 후 진행한다.

## 7. 주요 위험과 대응

### 네이버/WiseReport 페이지 변경

- 전용 adapter로 격리한다.
- 저장된 fixture로 parser를 테스트한다.
- 조회 실패 시 peer 없는 보고서로 graceful degradation한다.

### DART 표 구조 차이

- XML/HTML 구조 파서를 우선 사용한다.
- 제목 alias는 탐색용으로만 사용하고 투자 문장을 만들지 않는다.
- 원문 표, 정규화 결과, 누락 사유를 함께 저장한다.

### YFinance valuation 누락 또는 소급 갱신

- YFinance 값을 절대 단일 진실원으로 사용하지 않는다.
- 기준일 가격과 당시 공개된 DART 수치로 계산한 값을 우선한다.
- 직접값과 계산값의 provenance를 분리한다.

### 기간 혼용

- 모든 metric에 period metadata를 의무화한다.
- 비교 가능성 validation을 계산 전에 수행한다.
- YTD와 annual을 같은 성장률 계산에 사용하면 테스트가 실패하게 한다.

### 프롬프트 크기 증가

- 원천 전체를 Writer에 넣지 않는다.
- Strategy audit ledger와 Writer handoff를 분리한다.
- 중복 evidence를 id 기준으로 제거한다.

## 8. 최종 인수 기준

- [x] 입력 기준일 이후의 DART, News, YFinance 데이터가 사용되지 않는다.
- [x] SK바이오팜 2025-10-31 실행은 2025 반기보고서를 최신 DART로 사용한다.
- [x] 동일 기간 과거 재무 추세가 제공된다.
- [x] 제품·서비스별 매출액과 비중이 제공된다.
- [x] 시장점유율은 수집·생성하지 않는다.
- [x] 기준일 밸류에이션과 provenance가 제공된다.
- [x] 컨센서스와 목표주가는 수집·출력하지 않는다.
- [x] View change 조건은 Strategy와 Writer에 존재하지 않는다.
- [x] SK바이오팜의 경쟁사는 일성아이에스 한 곳만 사용한다.
- [x] 일성아이에스의 세 upstream 보고서가 새로 생성된다.
- [x] Strategy 고정 프롬프트에 산업·기업·특정 연도 예시가 없다.
- [x] Hold 우선 규칙이 없다.
- [x] Writer는 압축된 계층형 handoff를 사용한다.
- [x] Review/Repair 단계가 없다.
- [x] 관련 단위 테스트와 통합 테스트가 모두 통과한다.
- [x] 1개월 end-to-end 보고서를 직접 읽은 정성 평가가 완료된다.

## 9. 진행 보고 방식

각 Phase를 순차적으로 진행하고 다음 형식으로 사용자에게 보고한다.

```text
완료 Phase:
수정 파일:
핵심 변경:
실행한 테스트:
테스트 결과:
새로 발견한 문제:
다음 Phase:
```

한 Phase의 계약과 테스트가 통과하기 전에 다음 Phase로 넘어가지 않는다. 구현 중 계획 변경이 필요하면 이 문서의 해당 Phase와 변경 사유를 먼저 갱신한다.

## 10. 최신 통합 구현 결과

기준일: `2026-07-11`

이 절은 위 계획의 최종 구현 상태를 기록하며, 문서 앞부분의 중간 회귀 수치보다 우선한다.

- 기업명과 `selected_date`만 받는 `orchestration.full_report_pipeline`을 추가했다.
- OpenDART의 `에스케이바이오팜`과 사용자 입력 `SK바이오팜`을 일반 영문 약어 alias로 연결했다.
- Naver FG000에서 일성아이에스를 자동 선택하되 Naver 시가총액 값은 identity 감사 파일에만 보존했다.
- `selected_date=20251031`은 장 시작 전으로 해석해 News/YFinance cutoff를 `20251030`으로 고정했다.
- 2025-10-31 이전 최신 정기보고서는 실제 제출일 기준 2025-06-30 반기보고서였다.
- 수익률·기술지표는 adjusted close, valuation과 독자용 종가는 raw close를 사용한다.
- YFinance SY가 market date와 selected date를 혼동해 전 claim을 제외하던 문제를 수정했다. 최종 회귀에서 target과 peer 모두 exclude 0건이다.
- Competitor LLM prose, summary JSON/Markdown, Strategy competitor-report 입력을 제거하고 구조화 pairwise dataset 하나만 유지했다.
- Writer는 `required_key_evidence`에서 제품별 공시 표시값과 선택일 계산 valuation token을 그대로 사용한다.
- Writer validation 실패 결과는 cache하지 않으며 repair/review 호출 없이 실행 실패로 처리한다.
- 정상 cold-cache 호출 범위는 target 6, peer 6, final 3으로 총 15회다.
- 최종 동일 입력 warm 실행은 4.1초, LLM 0회, 0 tokens였다.
- 반사실 Hold 편향 평가는 strong positive=Buy, balanced=Hold, strong negative=Sell로 3/3 통과했다.
- 최종 결과는 `Hold`, 투자기간 `6~12개월`, evidence sufficiency `high`다.
- `writer_validation_report.json`은 18개 항목 전체 pass, notes 0건이다.
- 최종 전체 pytest는 `179 passed`다.

실행 manifest:

```text
Output_total/runs/SK바이오팜_20251031/executions/20260711T152838903305Z/
Output_total/runs/SK바이오팜_20251031/executions/20260711T160039932334Z/
Output_total/runs/SK바이오팜_20251031/executions/20260711T160112238369Z/
```
