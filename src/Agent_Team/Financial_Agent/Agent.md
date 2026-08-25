# Financial Analyst Contract

Financial Analyst는 기준일까지 공개된 DART 정기보고서에서 재무 사실과 기간 비교를 정규화한다. 투자 의견은 생성하지 않는다.

## Primary Evidence

- `dart_main.json`: 손익, 마진, EPS, 재무 추세, 제품·서비스별 매출
- `dart_master.json`: 현금흐름, 재무상태, 자본 구조, 유동성
- 공시 접수일이 기준일보다 앞선 보고서만 사용한다.
- 누적, 분기, 연간, 시점 값을 서로 다른 기간 기준으로 보존한다.

## Secondary Context

- 기업 관련도 상위 10개 뉴스 사건의 날짜, 제목, 본문 일부와 원 뉴스 evidence ID
- YFinance 기준일 시장·상대성과 evidence ID
- 뉴스 에이전트가 작성한 주장은 입력하지 않으며 `secondary_context`에는 전처리된 뉴스 사건 자체를 보존한다.
- Financial SY가 `corroborates`, `contradicts`, `neutral`, `insufficient` 중 하나로 평가한다.
- 사용 목적은 항상 `framing_and_limitation_only`다.
- News/Market 근거로 DART claim의 `strong`, `context_only`, `exclude` 상태를 변경하지 않는다.
- 교차 도메인 인과관계를 생성하지 않는다.
- 뉴스 발생일이 재무자료의 대상 기간보다 뒤라면 선행 재무자료에 사건 효과가 나타나지 않는 것을 한계나 반대 근거로 해석하지 않는다. 이 경우 재무자료는 사건 발생 전의 기초체력을 설명한다.

## Output

- `financial_trends`, `revenue_breakdown`, `share_information`
- DART 기반 `financial_statement_view`, `detailed_analysis`
- `sy_handoff.financial_claims`
- `sy_handoff.key_evidence`에는 DART evidence만 포함
- `secondary_context`

Financial Analyst는 LLM을 호출하지 않는다. Financial SY가 DART claim admissibility와 secondary context assessment를 한 semantic batch에서 처리한다.
