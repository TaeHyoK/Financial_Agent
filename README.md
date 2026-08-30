# Financial Agent

재무공시·뉴스·시장 자료와 국내 비교기업 분석을 결합해 상장기업 분석 보고서를 생성하는 다중 에이전트 파이프라인이다. 기업명과 분석 기준일을 입력하면 자료 수집부터 재무·뉴스·시장 분석, 비교기업 분석, 투자전략, 차트 선택과 HTML 보고서 작성까지 순서대로 실행한다.

생성된 보고서는 투자 판단을 위한 참고자료이며 투자 자문이나 매매 권유가 아니다.

## 분석 절차

1. OpenDART 정기공시에서 재무제표, 제품·서비스 매출과 주식 수를 수집한다.
2. 기준일 직전 90일 뉴스에서 URL 중복을 제거하고 7일 범위의 유사 기사를 사건 단위로 묶는다.
3. 공시의 사업 내용과 기사 간 유사도를 바탕으로 주별 후보를 선정하고 주간 요약과 상위 사건을 구성한다.
4. 재무·뉴스·시장 에이전트가 담당 자료와 다른 영역의 보조자료를 함께 분석한다.
5. 대상기업과 국내 비교기업 한 곳에 같은 절차를 적용하고 동일 기준의 차이를 비교한다.
6. Strategy Agent가 현재 대응과 근거·위험을 작성하고, Writer Agent가 필요한 차트를 선택해 최종 HTML 보고서를 구성한다.

차트 목록에는 실제 도식에 사용된 기간과 주요 지표가 구조화되어 전달된다. Writer Agent는 이를 Strategy 근거와 연결해 차트 관찰과 대상기업 판단에 미치는 의미를 작성하며, 고정 문구는 축·단위와 대체 설명 같은 도식 정보에만 사용한다.

![기업 분석 보고서 생성 구조](docs/assets/pipeline_architecture.jpg)

## 설치

Python 3.10 이상을 사용한다.

```bash
python -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -e .
cp configs/.env.example configs/.env
```

`configs/.env`에 다음 값을 입력한다.

```dotenv
OPENAI_API_KEY=your_openai_api_key_here
DART_API_KEY=your_dart_api_key_here
```

## 실행

다음 예시는 2025년 10월 31일 장 시작 전 시점의 현대모비스를 분석한다. 뉴스 수집 기간은 3개월, 투자 판단 기간은 1개월이다.

```bash
financial-report \
  --company-name 현대모비스 \
  --selected-date 20251031 \
  --news-window 3m \
  --decision-horizon-profile short_term \
  --llm-model gpt-5.4 \
  --no-progress
```

설치하지 않고 실행할 때는 다음 명령을 사용한다.

```bash
PYTHONPATH=src python -m orchestration.full_report_pipeline \
  --company-name 현대모비스 \
  --selected-date 20251031 \
  --news-window 3m \
  --decision-horizon-profile short_term \
  --llm-model gpt-5.4 \
  --no-progress
```

`selected_date`는 장 시작 전 분석 시점을 뜻한다. 위 실행에서 사용할 수 있는 공시·뉴스·시장 자료의 마지막 날짜는 2025년 10월 30일이다. 비교기업을 직접 지정하려면 `--peer-stock-code`에 여섯 자리 종목코드를 입력한다.

실행 계획과 기업 식별 결과만 확인하려면 `--dry-run`을 추가한다.

## 산출물

```text
Output_total/
└── {company_name}/
    ├── report_{company_name}.html
    ├── Financial/{selected_date}/final_report.json
    ├── News/{selected_date}/final_report.json
    ├── Y_Finance/{selected_date}/final_report.json
    ├── Competitor/{selected_date}/peer_comparison_dataset.json
    ├── Competitor/{selected_date}/peer_comparison_report.json
    ├── Strategy/{selected_date}/strategy_decision_output_v5.json
    ├── Visualization/{selected_date}/chart_manifest.json
    ├── Writer/{selected_date}/report.html
    ├── 비교기업/{peer_company_name}/
    │   ├── Financial/{selected_date}/
    │   ├── News/{selected_date}/
    │   ├── Y_Finance/{selected_date}/
    │   └── runs/{selected_date}/
    └── runs/{selected_date}/full_pipeline_manifest.json
```

산출물은 대상기업별로 모이며 에이전트별 자료는 그 아래에서 기준일별로 구분된다. 비교기업의 하위 분석은 독립된 최상위 결과로 취급하지 않고 대상기업의 `비교기업/{peer_company_name}` 아래에 저장한다. 최종 보고서는 기업 폴더의 최상위인 `Output_total/{company_name}/report_{company_name}.html`에 저장된다. Writer 폴더의 `report.html`은 생성 과정과 검증을 위한 내부 사본이다. 저장소에 포함된 현대모비스 예시는 [sample/현대모비스_20251031/report.html](sample/현대모비스_20251031/report.html)에서 확인할 수 있다.

실행이 끝나면 터미널 마지막에 전체 언어 모델 토큰 사용량과 예상 OpenAI API 비용이 달러로 표시된다. 비용은 캐시되지 않은 입력, 캐시 입력과 출력 토큰을 각각의 단가로 계산한다. 같은 내용은 실행별 `llm_usage_summary.json`의 `estimated_api_cost`에도 기록된다. 단가는 OpenAI 공식 모델 문서의 표준 API 가격을 기준으로 하며 도구 호출 요금과 지역 처리 추가 요금은 포함하지 않는다.

## 코드 구성

```text
src/
├── Agent_Team/
│   ├── Financial_Agent/       # 공시 수집과 재무 분석
│   ├── News_Agent/            # 뉴스 수집·중복 병합·사건 분석
│   ├── YFinance_Agent/        # 시장 자료와 가치평가 분석
│   ├── Competitor_Agent/      # 비교기업 선정과 1:1 비교
│   ├── Strategy_Agent/        # 판단 방향·근거·위험 작성
│   ├── Visualization Agent/   # 차트 목록과 선택 차트 생성
│   └── Writer Agent/          # 최종 HTML 보고서 작성
├── orchestration/             # 전체 파이프라인 실행
└── shared/                    # 공통 근거 계약과 모델 호출
```

실험 실행기, 단일 모델 비교 코드, 자동 평가 코드와 과거 실행 결과는 포함하지 않는다. API 키가 포함될 수 있는 `.env`와 실행 결과 디렉터리도 Git 추적 대상에서 제외한다.

## 적용 범위

- 현재 구현은 국내 비금융 상장기업의 정기공시 분석을 중심으로 한다.
- 선정된 비교기업 한 곳과의 차이는 업종 전체의 순위나 평균을 뜻하지 않는다.
- 뉴스 수집 결과는 외부 사이트의 응답 상태와 수집 시점에 따라 달라질 수 있다.
- 언어 모델 응답은 같은 입력에서도 세부 표현이 달라질 수 있다.
