# Financial Agent

재무공시·뉴스·시장 자료와 비교기업 분석을 결합하여 국내 상장기업의 투자 분석 보고서를 생성하는 연구용 다중 에이전트 시스템이다. 기업명과 분석 기준일을 입력하면 각 자료를 수집·가공하고, 재무·뉴스·시장 분석을 거쳐 투자 의견과 HTML 보고서를 생성한다.

본 저장소는 논문 실험에 사용한 파이프라인과 재현 스크립트를 공개하기 위한 것이다. 생성된 보고서는 연구 결과물이며 투자 자문이나 매매 권유가 아니다.

## 연구 개요

재무공시, 뉴스 기사, 시장 자료를 별도의 처리 없이 언어 모델에 입력하면 동일 내용이 반복되거나 분석에 필요한 항목을 구분하기 어렵다. 특히 뉴스는 기업명이 언급되더라도 사업과 직접 관련된 기사인지 판단하기 어렵다. 또한 자료 유형별 에이전트의 결과를 마지막 단계에서만 종합하면 각 분석 단계가 다른 자료의 상황을 충분히 반영하지 못할 수 있다.

이를 위해 다음과 같이 분석 절차를 구성하였다.

1. 공시에서 재무 항목과 제품별 매출을 추출하고, 시장 자료에서 수익률·기술지표·가치평가 지표를 계산한다.
2. 뉴스 기사는 URL 중복을 제거한 뒤 공시의 사업 내용과 기사 간 유사도를 계산하여 순위를 정하고, 같은 날의 유사 기사를 사건 단위로 묶는다.
3. 각 영역별 에이전트는 담당 자료를 주 분석자료로 사용하고, 다른 분석 영역에서 산출한 자료를 보조자료로 함께 참조한다.
4. 대상기업과 국내 비교기업에 같은 분석 절차를 적용하고 두 기업의 재무·뉴스·시장 분석과 동일 기준의 수치 차이를 함께 비교한다.
5. 분석 기준일 이전에 공개된 공시, 기사, 시장 자료만 사용한다.

보조자료는 다른 에이전트가 작성한 주장이 아니라 자료 가공 단계의 산출물을 직접 사용한다. 시장 분석에는 30일 날짜별 뉴스 요약을 제공하여 거래일별 가격·거래량 변화와 함께 살펴보고, 재무 분석에는 기업 관련도 상위 10개 뉴스 사건의 날짜·제목·본문 일부를 제공한다.

## 분석 구조

![자료 유형별 분석과 비교기업 분석을 결합한 기업 분석 보고서 생성 구조](docs/assets/pipeline_architecture.jpg)

*그림 1. 자료 유형별 분석과 비교기업 분석을 결합한 기업 분석 보고서 생성 구조*

파이프라인은 자료 수집 및 가공, 영역별 분석, 비교기업 분석, 투자 의견 생성, 보고서 작성 순서로 실행된다. 현재 구현은 국내 비교기업 한 곳을 선정하며, 비교기업 선정에 사용한 현재 시가총액은 기업 식별에만 사용하고 투자 판단의 근거로 사용하지 않는다.

현대모비스를 2025년 10월 31일 기준으로 분석한 결과는 [보고서 샘플(PDF)](docs/assets/report_mobis.pdf)에서 확인할 수 있다.

| 구분 | 입력 | 처리 내용 |
| --- | --- | --- |
| 재무 분석 | OpenDART 정기공시 | 재무제표, 제품·서비스 매출, 주식 수 및 재무비율 산출 |
| 뉴스 분석 | Google News와 원 기사 | 중복 제거, 사업 내용 기반 관련도 순위, 일자별 사건 구성 |
| 시장 분석 | 종목, KOSPI, 원/달러 자료 | 수익률, 기술지표, 상대성과 및 가치평가 지표 산출 |
| 비교기업 분석 | 대상기업과 비교기업의 재무·뉴스·시장 분석 결과 | 동일 기준 수치 비교와 두 기업의 상대적 강점·약점 해석 |
| 투자전략 | 세 영역의 분석 결과와 비교 자료 | 매수·보유·매도 의견, 근거와 위험 요인 작성 |
| 보고서 작성 | 투자전략 결과 | 투자 의견, 주요 근거, 위험 요인 및 자료 한계로 구성된 HTML 생성 |

## 설치

Python 3.10 이상을 사용한다.

```bash
git clone https://github.com/TaeHyoK/Financial_Agent.git
cd Financial_Agent

python -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -e ".[dev]"
```

환경 변수 예시 파일을 복사한 뒤 API 키를 입력한다.

```bash
cp configs/.env.example configs/.env
```

```dotenv
OPENAI_API_KEY=your_openai_api_key_here
DART_API_KEY=your_dart_api_key_here
```

`configs/.env`는 Git 추적 대상이 아니다. OpenAI 호환 API를 사용하는 경우 `OPENAI_BASE_URL`을 추가할 수 있다.

## 보고서 생성

다음 명령은 현대모비스를 2025년 10월 31일 장 시작 전 시점에서 분석한다. 뉴스 수집 기간과 투자 판단 기간은 각각 1개월이다.

```bash
PYTHONPATH=src python -m orchestration.full_report_pipeline \
  --company-name 현대모비스 \
  --selected-date 20251031 \
  --news-window 1m \
  --decision-horizon-profile short_term \
  --llm-model gpt-5.4-mini \
  --no-progress
```

설치 후에는 같은 명령을 `financial-report`로 실행할 수 있다.

```bash
financial-report \
  --company-name 현대모비스 \
  --selected-date 20251031 \
  --news-window 1m \
  --decision-horizon-profile short_term
```

`selected_date`는 장 시작 전의 분석 시점을 뜻한다. 따라서 위 실행에서 공시·뉴스·시장 자료의 마지막 사용일은 2025년 10월 30일이다. 기업명으로 OpenDART 법인번호와 종목코드를 확인하고, 국내 비교기업 한 곳을 자동으로 선정한다. 비교기업을 직접 지정해야 할 때는 `--peer-stock-code`를 사용한다.

실행 전에 기업 식별 결과와 하위 명령만 확인하려면 `--dry-run`을 추가한다.

## 주요 산출물

실행 결과는 `Output_total/` 아래에 저장된다.

```text
Output_total/
├── Financial/{run_key}/final_report.json
├── News/{run_key}/final_report.json
├── Y_Finance/{run_key}/final_report.json
├── Competitor/{run_key}/peer_comparison_dataset.json
├── Competitor/{run_key}/peer_comparison_report.json
├── Strategy/{run_key}/strategy_report.json
├── Writer/{run_key}/report.html
└── runs/{run_key}/full_pipeline_manifest.json
```

최종 보고서는 다음 경로에서 확인할 수 있다.

```text
Output_total/Writer/{company_name}_{selected_date}/report.html
```

위 실행 예시의 최종 HTML 경로는 다음과 같다.

```text
Output_total/Writer/현대모비스_20251031/report.html
```

실험 산출물, API 응답, 로그 및 생성 보고서는 Git에 포함되지 않는다.

## 논문 실험 재현

논문에서는 서로 다른 업종의 국내 상장기업 6개를 2025년 10월 31일 기준으로 분석하였다. 전체 모형, 보조자료 제외 조건, 비교기업 제외 조건과 단일 언어 모델 조건을 비교하였다. 보고서 생성에는 GPT-5.4 mini, 자동 평가에는 GPT-5.4를 사용하였다.

실험 실행 순서와 결과 집계 명령은 [논문 실험 재현 문서](docs/PAPER_REPRODUCIBILITY.md)에 정리되어 있다. API를 호출하지 않고 실행 계획만 확인하려면 다음 명령을 사용한다.

```bash
bash scripts/run_six_company_v3_background.sh plan
bash scripts/run_six_company_single_llm_v3_background.sh plan
```

실험 결과 파일은 저장소에 포함하지 않는다. 같은 명령을 실행하더라도 언어 모델 응답과 외부 자료 제공 상태에 따라 세부 결과가 달라질 수 있다.

## 코드 구성

```text
.
├── configs/                       # 환경 변수 예시와 뉴스 처리 설정
├── docs/                          # 실험 재현 문서와 그림
├── scripts/                       # 6개 기업 실험 실행 스크립트
└── src/
    ├── Agent_Team/
    │   ├── Financial_Agent/       # 공시 수집 및 재무 분석
    │   ├── News_Agent/            # 뉴스 수집, 관련도 계산 및 사건 분석
    │   ├── YFinance_Agent/        # 시장 자료와 가치평가 분석
    │   ├── Competitor_Agent/      # 비교기업 선정 및 비교 자료 생성
    │   ├── Strategy_Agent/        # 판단 방향과 위험 요인 생성
    │   └── Writer Agent/          # 최종 HTML 보고서 작성
    ├── orchestration/             # 전체 실행, 실험 및 평가 절차
    ├── shared/                    # 공통 자료 계약과 언어 모델 호출
    └── single_llm/                # 단일 언어 모델 비교 조건
```

전체 실행 진입점은 `src/orchestration/full_report_pipeline.py`이며, 대상기업과 비교기업의 영역별 분석 순서는 `src/orchestration/dependency_graph.py`에 정의되어 있다.

## 외부 자료와 모델

| 용도 | 서비스 또는 모델 |
| --- | --- |
| 기업 및 공시 자료 | OpenDART |
| 뉴스 수집 | Google News RSS와 원 기사 페이지 |
| 주가·지수·환율 | Yahoo Finance, yfinance |
| 비교기업 후보 | Naver Finance·WiseReport 업종분석 |
| 뉴스 문장 표현 | `BAAI/bge-m3` |
| 뉴스 관련도 재정렬 | `BAAI/bge-reranker-v2-m3` |
| 보고서 생성 및 평가 | OpenAI 호환 API |

뉴스 모형을 내려받을 수 없거나 실행 환경이 제한된 경우 대체 처리 경로를 사용하지만, 논문 실험과 같은 조건을 재현하려면 설정 파일에 기재된 모형을 사용해야 한다.

## 검사

```bash
pytest -q
python -m compileall -q src
git diff --check
```

2026년 8월 20일 기준 전체 회귀 검사는 `331 passed, 7 subtests passed`이다.

## 적용 범위와 한계

- 현재 회귀 검사는 국내 비금융 상장기업의 별도 재무제표 경로를 중심으로 작성되었다.
- 비교기업 한 곳과의 차이를 제시하므로 업종 전체의 순위나 평균으로 해석할 수 없다.
- 뉴스 수집 결과는 수집 시점과 외부 사이트의 응답 상태에 따라 달라질 수 있다.
- 언어 모델의 출력은 동일한 입력에서도 완전히 같지 않을 수 있다.
- 생성된 투자 의견의 실제 수익률이나 매매 성과를 검증하는 시스템은 아니다.

## 인용

논문 또는 프로젝트에서 이 저장소를 사용할 경우 저장소 주소와 사용한 커밋을 함께 기록한다.

```bibtex
@software{taehyok_financial_agent_2026,
  author = {TaeHyoK},
  title = {Financial Agent: Multi-Agent Equity Research Report Generation},
  year = {2026},
  url = {https://github.com/TaeHyoK/Financial_Agent}
}
```

## 라이선스

현재 별도 라이선스가 지정되어 있지 않다. 코드의 재사용이나 재배포가 필요한 경우 저장소 소유자에게 이용 조건을 확인해야 한다.
