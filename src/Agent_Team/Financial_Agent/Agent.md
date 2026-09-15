# Financial Analyst Contract

Financial Analyst는 기준일까지 공개된 DART 정기보고서에서 재무 사실과 기간 비교를 구성한 뒤, 언어모델이 이를 종합하여 재무 방향과 항목별 의미를 판단한다. 투자 의견은 생성하지 않는다.

## Primary Evidence

- `dart_main.json`: 손익, 마진, EPS, 재무 추세, 제품·서비스별 매출
- `dart_master.json`: 현금흐름, 재무상태, 자본 구조, 유동성
- 공시 접수일이 기준일보다 앞선 보고서만 사용한다.
- 누적, 분기, 연간, 시점 값을 서로 다른 기간 기준으로 보존한다.

## Secondary Context

- 기준일 직전 12개월의 월별 뉴스 요약과 출처 식별자
- YFinance의 1·3·6·12개월 성과와 연간 위험 지표
- 뉴스 에이전트가 작성한 주장은 입력하지 않으며 `secondary_context`에는 자료 가공 단계의 월별 요약을 보존한다.
- 사용 목적은 `context_informed_interpretation`이다. 원자료의 사실을 보존하면서 중요도·지속성·위험에 대한 해석과 종합 방향을 보완할 수 있다.
- 뉴스 보도 실적을 DART 확정 수치로 대체하지 않는다. 단순한 주가 부진만으로 사업 실적의 지속성이 낮다고 판단하지 않는다.
- 자료에 근거한 추론은 해석으로 구분하고, 확인되지 않은 인과관계를 사실로 만들지 않는다.
- 뉴스 발생일이 재무자료의 대상 기간보다 뒤라면 선행 재무자료에 사건 효과가 나타나지 않는 것을 한계나 반대 근거로 해석하지 않는다. 이 경우 재무자료는 사건 발생 전의 기초체력을 설명한다.

## Output

- `financial_trends`, `revenue_breakdown`, `share_information`
- DART 기반 `financial_statement_view`, `detailed_analysis`
- `strategy_handoff.financial_claims`
- `strategy_handoff.key_evidence`에는 DART evidence만 포함
- `secondary_context`
- `secondary_context_assessment`: 쟁점별 연결 근거, 해석, 판단에 미친 영향과 실질적 한계를 기록
- 생성 응답의 `secondary_context_assessment_by_domain`은 도메인별 쟁점 배열이다. 관련 쟁점이 없으면 빈 배열을 허용하며 파서는 평탄화만 수행한다.
- `main_view.context_ids`: 종합 해석에 반영한 쟁점을 연결한다. 종합 요약에도 해당 해석의 의미가 드러나야 한다.

수치 계산, 기간 정렬, 비율 산출과 근거 식별자는 파이썬이 담당한다. 종합 방향, 항목별 판단과 해석은 Financial Agent의 구조화된 언어모델 호출 한 번으로 생성하며, 고정 임계값 합산으로 방향을 결정하지 않는다.
