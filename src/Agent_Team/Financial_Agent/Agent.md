# Financial Analyst Contract

Financial Analyst는 기준일까지 공개된 DART 정기보고서에서 재무 사실과 기간 비교를 정규화한다. 투자 의견은 생성하지 않는다.

## Primary Evidence

- `dart_main.json`: 손익, 마진, EPS, 재무 추세, 제품·서비스별 매출
- `dart_master.json`: 현금흐름, 재무상태, 자본 구조, 유동성
- 공시 접수일이 기준일보다 앞선 보고서만 사용한다.
- 누적, 분기, 연간, 시점 값을 서로 다른 기간 기준으로 보존한다.

## Secondary Context

- News SY가 검증한 주요 사건과 원 뉴스 evidence ID
- YFinance 기준일 시장·상대성과 evidence ID
- `secondary_context`는 원천 사실 catalog만 보존한다.
- Financial SY가 `corroborates`, `contradicts`, `neutral`, `insufficient` 중 하나로 평가한다.
- 사용 목적은 항상 `framing_and_limitation_only`다.
- News/Market 근거로 DART claim의 `strong`, `context_only`, `exclude` 상태를 변경하지 않는다.
- 교차 도메인 인과관계를 생성하지 않는다.

## Output

- `financial_trends`, `revenue_breakdown`, `share_information`
- DART 기반 `financial_statement_view`, `detailed_analysis`
- `sy_handoff.financial_claims`
- `sy_handoff.key_evidence`에는 DART evidence만 포함
- `secondary_context`

Financial Analyst는 LLM을 호출하지 않는다. Financial SY가 DART claim admissibility와 secondary context assessment를 한 semantic batch에서 처리한다.
