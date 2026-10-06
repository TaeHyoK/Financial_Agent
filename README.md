# Financial Agent

재무공시·뉴스·시장 자료와 국내 비교기업 분석을 결합해 상장기업 분석 보고서를 생성하는 다중 에이전트 파이프라인이다. 기업명과 분석 기준일을 입력하면 자료 수집부터 재무·뉴스·시장 분석, 비교기업 분석, 투자전략, 차트 선택과 HTML 보고서 작성까지 순서대로 실행한다.

생성된 보고서는 투자 판단을 위한 참고자료이며 투자 자문이나 매매 권유가 아니다.

## 분석 절차

1. OpenDART 정기공시에서 재무제표, 제품·서비스 매출과 주식 수를 수집한다.
2. 기준일 직전 1년 뉴스에서 동일 URL을 정리하고 전체 후보의 스니펫을 확보한다. 대상기업 명시 여부와 그룹·공시상 관계회사 언급을 확인한 뒤, 통과한 기사의 제목·스니펫으로 같은 달력 주간의 유사 기사를 사건 단위로 묶는다.
3. 공시의 여섯 섹션과 기사 간 임베딩 유사도를 가중합해 주별 상위 3건을 선정한다. 별도 재정렬 없이 선정 기사 전체를 월별로 배열해 뉴스 에이전트에 전달하고, 같은 기사들의 월별 요약을 재무·시장 보조자료로 만든다.
4. 재무·뉴스·시장 에이전트가 담당 자료와 다른 영역의 보조자료를 함께 분석한다.
5. 대상기업과 국내 비교기업 한 곳에 같은 절차를 적용하고 동일 기준의 차이를 비교한다.
6. Strategy Agent가 향후 12개월 투자의견(매수·중립·매도), 실적 검토와 전망·근거·위험을 작성하고, Writer Agent가 필요한 차트를 선택해 최종 HTML 보고서를 구성한다.

차트 목록에는 실제 도식에 사용된 기간과 주요 지표가 구조화되어 전달된다. Writer Agent는 이를 Strategy 근거와 연결해 차트 관찰과 대상기업 판단에 미치는 의미를 작성하며, 고정 문구는 축·단위와 대체 설명 같은 도식 정보에만 사용한다.

![기업 분석 보고서 생성 구조](docs/assets/pipeline_architecture.jpg)

공통 subdata는 **월별 뉴스 요약, 시장 지표표 1개, 재무 추세표 1개**다. 같은 자료를 받는 두 에이전트는 동일한 구성과 값을 사용한다. 시장 원자료는 보관하며, 언어모델에는 월별 관측치와 최근 20거래일만 전달한다. 세부 지표·기간·출력 계약과 검증 방법은 [12개월 분석 설계](docs/annual_analysis.md)를 참고한다.

뉴스 주 자료는 날짜·제목·스니펫·기사 ID를 보존한 선정 기사 전체이며 월별 2건 제한은 없다. 재무·시장 에이전트는 해당 기사들의 월별 요약을 받는다. 요약 여부가 뉴스 주 자료의 기사 수를 줄이지 않는다. 월 구간은 분석 기준일에 맞추며, [기사와 보조자료 전달 구조](docs/news_article_only.md)에 실험 조건과 검증 범위를 정리했다.

## 설치

Python 3.10 이상을 사용한다.

```bash
python -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -e .
cp configs/.env.example configs/.env
```

실제 애널리스트 보고서 PDF 의 본문 추출과 BERTScore 평가에는 `python -m pip install -e ".[eval]"` 로 추가 의존성(bert-score, pdfplumber, torch, transformers)을 설치한다. 테스트에는 `python -m pip install -e ".[dev]"` 로 pytest 를 설치한다.

`configs/.env`에 다음 값을 입력한다.

```dotenv
OPENAI_API_KEY=your_openai_api_key_here
DART_API_KEY=your_dart_api_key_here
```

## 실행

월별 뉴스 요약에는 `gpt-5.6-luna`를 사용하고, 대상기업·비교기업의 하위 분석과 비교 분석, Strategy·Writer에는 `gpt-5.4`를 사용한다. 뉴스 요약은 월별로 한 번씩 총 12회 생성하여 재무·시장 분석의 보조자료로만 제공한다. 뉴스 분석에는 선정 기사 전체를 제공한다. 두 모델은 각각 `--news-summary-model`과 `--llm-model`로 변경할 수 있으며 실행 명세에 기록된다. 제거 실험에서도 같은 단계에는 같은 모델을 사용한다. 기존 결과는 그대로 보존하며, 모델이 다른 결과를 같은 실험 조건으로 합산하지 않는다.

다음 예시는 2025년 10월 31일 장 시작 전 시점의 현대모비스를 분석한다. 뉴스와 시장 분석 기간은 과거 1년, 재무 자료는 3개 연도와 최신 중간보고서, 투자 전망 기간은 향후 12개월이다.

```bash
financial-report \
  --company-name 현대모비스 \
  --selected-date 20251031 \
  --news-window 1y \
  --decision-horizon-profile annual \
  --llm-model gpt-5.4 \
  --news-summary-model gpt-5.6-luna \
  --no-progress
```

설치하지 않고 실행할 때는 다음 명령을 사용한다.

```bash
PYTHONPATH=src python -m orchestration.full_report_pipeline \
  --company-name 현대모비스 \
  --selected-date 20251031 \
  --news-window 1y \
  --decision-horizon-profile annual \
  --llm-model gpt-5.4 \
  --news-summary-model gpt-5.6-luna \
  --no-progress
```

`selected_date`는 장 시작 전 분석 시점을 뜻한다. 위 실행에서 사용할 수 있는 공시·뉴스·시장 자료의 마지막 날짜는 2025년 10월 30일이다. 비교기업을 직접 지정하려면 `--peer-stock-code`에 여섯 자리 종목코드를 입력한다.

실행 계획과 기업 식별 결과만 확인하려면 `--dry-run`을 추가한다. 산출물은 기본으로 `Output_total/` 아래에 저장되며, 다른 위치를 쓰려면 `--output-root`를 지정한다.

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
    ├── Strategy/{selected_date}/strategy_decision_output.json
    ├── Visualization/{selected_date}/chart_manifest.json
    ├── Writer/{selected_date}/report.html
    ├── 비교기업/{peer_company_name}/
    │   ├── Financial/{selected_date}/
    │   ├── News/{selected_date}/
    │   ├── Y_Finance/{selected_date}/
    │   └── runs/{selected_date}/
    └── runs/{selected_date}/full_pipeline_manifest.json
```

산출물은 대상기업별로 모이며 에이전트별 자료는 그 아래에서 기준일별로 구분된다. 비교기업의 하위 분석은 독립된 최상위 결과로 취급하지 않고 대상기업의 `비교기업/{peer_company_name}` 아래에 저장한다. 최종 보고서는 기업 폴더의 최상위인 `Output_total/{company_name}/report_{company_name}.html`에 저장된다. Writer 폴더의 `report.html`은 생성 과정과 검증을 위한 내부 사본이다. 논문 실험의 최종 보고서 예시는 [final_reports/r01/full/현대건설.html](final_reports/r01/full/현대건설.html)에서 볼 수 있다. 이 보고서는 논문 생성 시점의 코드로 만들었으므로 현재 코드의 출력과 표기가 일부 다르다.

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
│   ├── Unified_Agent/         # One-team 실험의 통합 분석과 후속 단계 연결
│   ├── Visualization_Agent/   # 차트 목록과 선택 차트 생성
│   └── Writer_Agent/          # 최종 HTML 보고서 작성
├── orchestration/             # 전체 파이프라인 실행
└── shared/                    # 공통 근거 계약, 모델 호출, 공통 유틸(coerce·jsonio·schema·env)
```

리포 루트에는 다음 폴더가 함께 있다.

```text
run_config/                    # 논문 실험의 수집·생성·평가·LLM Judge 실행기
final_reports/                 # 논문 실험 최종 보고서 HTML 75개
final_reports_redesigned/      # 같은 보고서를 현재 디자인으로 다시 그린 판
ablation_results/              # 최종 보고서 BERTScore 결과
docs/                          # 방법 문서, docs/history/ 는 작업 기록
scripts/                       # 점검·비교용 보조 스크립트
tests/                         # 회귀 테스트
configs/                       # 기업 입력과 .env 예시
ablation_suite/                # 이전 실험 코드(아래 참고)
ablation_evaluation/           # 이전 실험 코드(아래 참고)
real_report_evaluation/        # 이전 실험 코드(아래 참고)
run_real_report_evaluation.py  # 이전 평가 진입점
```

`ablation_suite/`, `ablation_evaluation/`, `real_report_evaluation/`, `run_real_report_evaluation.py` 는 이전 6개 기업 실험의 코드다. 논문 실험은 이 중 Random news 표본 추출(`ablation_suite/annual_random.py`)과 본문 추출·BERTScore 계산(`real_report_evaluation/extract.py`, `runner.py`)만 쓴다. 평가 규약(`ablation_results/repeated_standard_5companies/protocol.json`)이 이 두 파일의 sha256 을 고정하고, 두 파일이 나머지 모듈을 import 하므로 폴더째 남겨 둔다.

One-team 조건에서 사용하는 통합 분석 구성요소는 `Unified_Agent`에 포함한다. 재무·뉴스·시장 자료를 하나의 분석 요청으로 전달하고, 통합 결과를 비교 분석·Strategy·Writer에 연결한다. 일반 실행 결과와 API 키가 포함될 수 있는 `.env`는 Git 추적 대상에서 제외한다.

## Ablation 결과와 재현 코드

논문 실험의 실행기(`run_config/`), 최종 보고서 75개(`final_reports/`), BERTScore 결과(`ablation_results/`), 최종 보고서 LLM-as-a-Judge 코드가 들어 있다. 조건 정의와 단계별 재현 방법은 [Ablation 방법과 재현](docs/ABLATION.md)에 정리했다.

- 원자료는 태그 `paper-collection`(커밋 `7e20b3d`)에서 수집했다. 다시 수집할 때는 이 리포 안에 `git worktree add repo paper-collection` 으로 수집용 작업 트리를 만들고 `python run_config/collect_pre_llm_data.py` 를 실행한다. 결과는 `collected_data/` 에 저장된다.
- 보고서 75개는 태그 `paper-generation`(커밋 `da85eb3`)의 코드로 생성했다.
- 그 뒤 `main`에는 동작을 바꾸는 변경이 들어왔다. 산출물 파일명과 모듈 이름을 정리했고(버전 접미사 제거), 종류주식 가치평가를 고쳤고(PR #25), Writer 표의 투자의견을 매수/중립/매도로 표시하도록 바꿨고(PR #26), Writer 프롬프트를 정리하고 고지문을 새로 썼다(PR #27). 따라서 `main`을 실행해도 75개 보고서를 똑같이 재현하지 않는다.
- 다시 돌리려면 Git 밖 자료가 필요하다. 동결 입력(`collected_data/`, `prepared_inputs/`, `reports/`, `status/`), 평가 자료(`evaluation/`), 실제 애널리스트 PDF(`references/`)는 저작권과 크기 때문에 리포에 두지 않는다.

Judge 는 실행을 마쳤고, 요청·응답·결과 파일은 리포에 두지 않는다.

## 테스트

리포 루트에서 `python -m pytest -q` 를 실행한다. 루트 `conftest.py` 가 경로를 잡아 준다.

## 적용 범위

- 현재 구현은 국내 비금융 상장기업의 정기공시 분석을 중심으로 한다.
- 선정된 비교기업 한 곳과의 차이는 업종 전체의 순위나 평균을 뜻하지 않는다.
- 뉴스 수집 결과는 외부 사이트의 응답 상태와 수집 시점에 따라 달라질 수 있다.
- 언어 모델 응답은 같은 입력에서도 세부 표현이 달라질 수 있다.

## 라이선스

코드와 문서, `final_reports/` 의 생성 보고서는 [MIT 라이선스](LICENSE)를 따른다. 실제 애널리스트 보고서 PDF 는 저작권 때문에 저장소에 포함하지 않는다.
