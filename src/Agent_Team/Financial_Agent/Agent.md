# Financial Analyst Contract

Financial Analyst는 기준일까지 공개된 DART 정기보고서에서 재무 사실과 기간 비교를 구성한 뒤, 언어모델이 이를 종합하여 재무 방향과 항목별 의미를 판단한다. 투자 의견은 생성하지 않는다.

## Primary Evidence

- `dart_main.json`: 손익, 마진, EPS, 재무 추세, 제품·서비스별 매출
- `dart_master.json`: 현금흐름, 재무상태, 자본 구조, 유동성
- 공시 접수일이 기준일보다 앞선 보고서만 사용한다.
- 누적, 분기, 연간, 시점 값을 서로 다른 기간 기준으로 보존한다.

## Secondary Context

- 기준일 직전 90일의 주간 뉴스 요약과 원 뉴스 evidence ID
- YFinance 기준일 시장·상대성과 evidence ID
- 뉴스 에이전트가 작성한 주장은 입력하지 않으며 `secondary_context`에는 자료 가공 단계에서 생성한 주간 요약을 보존한다.
- 사용 목적은 항상 `framing_and_limitation_only`다.
- 뉴스·시장 자료로 DART 근거의 채택 여부를 변경하지 않는다.
- 교차 도메인 인과관계를 생성하지 않는다.
- 뉴스 발생일이 재무자료의 대상 기간보다 뒤라면 선행 재무자료에 사건 효과가 나타나지 않는 것을 한계나 반대 근거로 해석하지 않는다. 이 경우 재무자료는 사건 발생 전의 기초체력을 설명한다.

## Output

- `financial_trends`, `revenue_breakdown`, `share_information`
- DART 기반 `financial_statement_view`, `detailed_analysis`
- `strategy_handoff.financial_claims`
- `strategy_handoff.key_evidence`에는 DART evidence만 포함
- `secondary_context`
- `secondary_context_assessment`: 뉴스·시장 보조자료가 DART 판단과 부합하는지 또는 해석 범위를 제한하는지 기록

수치 계산, 기간 정렬, 비율 산출과 근거 식별자는 파이썬이 담당한다. 종합 방향, 항목별 판단과 해석은 Financial Agent의 구조화된 언어모델 호출 한 번으로 생성하며, 고정 임계값 합산으로 방향을 결정하지 않는다.
