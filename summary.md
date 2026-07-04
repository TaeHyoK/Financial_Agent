# Financial_Agent_Final 디렉터리 구조 요약

## 1. 전체 개요

`/home/agent2/Financial_Agent_Final`은 기업 투자 판단 보조를 위한 Python 기반 다중 금융 에이전트 프로젝트이다. 핵심 구조는 `src/Agent_Team` 아래의 도메인별 에이전트와 `src/orchestration`의 통합 실행 레이어로 나뉜다.

현재 코드베이스는 다음 데이터를 수집/분석한다.

- DART 재무/공시 데이터
- YFinance 기반 주가, KOSPI, 환율 데이터
- Google News 기반 뉴스 데이터
- 각 도메인별 LLM 분석 결과
- 각 도메인별 SY 검증 결과
- 경쟁사 요약 및 최종 Strategy 리포트

프로젝트의 기본 실행 단위는 `<회사명>_<YYYYMMDD>` 형식의 `run_key`이며, 예시는 `SK바이오팜_20251031`, `더블유에스아이_20251031`, `위더스제약_20251031`이다. 현재 `Output_total`에 여러 기업이 함께 존재하는 이유는 SK바이오팜을 target 기업으로 분석하면서, 네이버증권의 해당 종목 `종목분석 > 업종분석` 경쟁사 비교 페이지에 언급된 더블유에스아이와 위더스제약을 경쟁사용 비교 기업으로 선정했기 때문이다.

## 2. 최상위 구조

```text
Financial_Agent_Final/
  README.md
  pyproject.toml
  requirements.txt
  Contribution.md
  progress_report_to_professor.md
  strategy_agent_codex_guideline.md
  configs/
  logs/
  schemas/
  src/
  tests/
  Output_total/
  .env
  .venv/
  .pytest_cache/
```

| 경로 | 역할 | 확인 내용 |
| --- | --- | --- |
| `README.md` | 프로젝트 기본 안내 | News Agent 이식과 기본 실행 순서를 중심으로 설명한다. |
| `pyproject.toml` | Python 패키지/엔트리포인트 설정 | `financial-agent-final` 패키지, Python `>=3.10`, CLI 스크립트들이 정의되어 있다. |
| `requirements.txt` | 런타임 의존성 | pandas, pyarrow, OpenAI, LangGraph, yfinance, pykrx, publicdatareader, NLP/클러스터링 패키지 등이 포함되어 있다. |
| `configs/` | 회사별 입력 및 뉴스 설정 | SK바이오팜, 더블유에스아이, 위더스제약 샘플 입력 JSON과 `news_default.yaml`이 있다. |
| `src/` | 실제 소스 코드 | 도메인별 Agent, 통합 오케스트레이션, shared 유틸리티가 있다. |
| `Output_total/` | 실행 산출물 | Financial, News, Y_Finance, Competitor, Strategy, runs 결과가 저장되어 있다. |
| `logs/` | 실행 로그 | `withus_20251031.log`, `wsi_20251031.log`가 있다. |
| `schemas/` | 스키마 디렉터리 | 디렉터리는 존재하지만 현재 파일은 없다. |
| `tests/` | 최상위 테스트 디렉터리 | 디렉터리는 존재하지만 현재 파일은 없다. 실제 테스트는 각 패키지 내부에 있다. |
| `.env`, `configs/.env` | 환경변수 파일 | API 키가 포함될 수 있으므로 내용은 열람하지 않았다. |
| `.venv/`, `.pytest_cache/`, `__pycache__/` | 로컬 실행/테스트 캐시 | 배포 또는 구조 이해의 핵심 소스는 아니다. |

캐시/가상환경을 제외하고 확인된 파일은 약 507개이며, `__pycache__`까지 제외하면 약 359개이다. 주요 확장자는 `py`, `json`, `md`, `csv`, `parquet`, `png`, `yaml`이다.

## 3. 패키지 및 실행 진입점

`pyproject.toml` 기준 프로젝트명은 `financial-agent-final`이고, 소스 루트는 `src`이다.

등록된 주요 CLI 엔트리포인트는 다음과 같다.

| 스크립트 | Python 대상 | 역할 |
| --- | --- | --- |
| `news-workflow` | `Agent_Team.News_Agent.cli:main` | News Agent 전체/단계별 실행 |
| `news-context-export` | `Agent_Team.News_Agent.context_export:main` | 뉴스 context export |
| `news-analysis-agent` | `Agent_Team.News_Agent.analysis_agent:main` | 뉴스 분석 handoff 생성 |
| `news-sy-agent` | `Agent_Team.News_Agent.sy_agent_cli:main` | News SY 검증 |
| `yfinance-pipeline` | `Agent_Team.YFinance_Agent.run_pipeline:main` | YFinance 수집, 보고서, SY 검증 통합 실행 |
| `agent-team-loop` | `orchestration.cli:main` | 전체 Agent Team 오케스트레이션 |
| `financial-dart` | `Agent_Team.Financial_Agent.main:main` | DART 수집 및 canonical output 생성 |
| `financial-index` | `Agent_Team.Financial_Agent.financial_index_calculator:main` | 재무지표 계산 |
| `financial-analyst-agent` | `Agent_Team.Financial_Agent.langgraph_flow:main` | Financial Analyst LangGraph 실행 |
| `financial-sy-agent` | `Agent_Team.Financial_Agent.SY_Agent.langgraph_flow:main` | Financial SY 검증 |
| `financial-sy-pipeline` | `Agent_Team.Financial_Agent.SY_Agent.run_pipeline:main` | Financial Analyst + SY 통합 실행 |
| `competitor-agent` | `Agent_Team.Competitor_Agent.cli:main` | 경쟁사 summary report 생성 |
| `strategy-agent` | `Agent_Team.Strategy_Agent.cli:main` | 최종 Strategy report 생성 |

## 4. `src/` 구조

```text
src/
  Agent_Team/
    Financial_Agent/
    News_Agent/
    YFinance_Agent/
    Competitor_Agent/
    Strategy_Agent/
  orchestration/
  shared/
  Preprocessed/
  Raw/
  financial_agent_final.egg-info/
  loop.md
```

| 경로 | 역할 |
| --- | --- |
| `src/Agent_Team/Financial_Agent` | DART 재무 수집, 정규화, 재무지표 계산, Financial Analyst 보고서, Financial SY 검증 |
| `src/Agent_Team/News_Agent` | 뉴스 수집, DART 사업 context chunking, 뉴스 context export, LLM 요약, 뉴스 분석, News SY 검증 |
| `src/Agent_Team/YFinance_Agent` | 주가/KOSPI/환율 수집, 기술지표 계산, 차트 생성, 시장 보고서, YFinance SY 검증 |
| `src/Agent_Team/Competitor_Agent` | 경쟁사별 Financial/News/YFinance final report를 종합해 summary 생성 |
| `src/Agent_Team/Strategy_Agent` | Target 3개 final report와 경쟁사 summary들을 읽어 Buy/Hold/Sell Strategy report 생성 |
| `src/orchestration` | Agent Team 전체 실행 순서, 경로, manifest/status/trace, 검증 관리 |
| `src/shared` | 날짜, env, evidence ref, JSON I/O, LLM client, logging 공통 유틸 |
| `src/Preprocessed`, `src/Raw`, `src/shared/Report_output` | 현재 파일 없는 placeholder 성격의 디렉터리 |
| `src/financial_agent_final.egg-info` | 패키지 설치 메타데이터 |

## 5. Financial Agent

위치:

```text
src/Agent_Team/Financial_Agent/
```

주요 책임:

- OpenDART/DART 재무 데이터 수집
- 재무제표 canonical JSON 생성
- `dart_master.json`, `dart_main.json`, `dart_lightweight.json`, `dart_2y_handoff.json` 생성
- 재무지표 계산
- DART를 primary anchor로 사용하는 Financial Analyst 보고서 생성
- Financial SY Agent로 claim과 원천 수치 일치 여부 검증

주요 파일:

| 파일 | 역할 |
| --- | --- |
| `main.py` | DART 수집 및 표준 산출물 생성 진입점 |
| `dart_client.py` | DART API 클라이언트 |
| `report_selector.py`, `report_resolver.py` | 대상 보고서 선택/해결 |
| `section_extractor.py`, `table_parser.py` | 공시 섹션 및 표 추출 |
| `normalizer.py`, `models.py` | 재무 데이터 정규화 및 모델 |
| `financial_index_calculator.py` | 재무지표 계산 |
| `financial_index.json` | 계산 대상 재무지표 정의 |
| `handoff_builder.py` | 상위 Agent 전달용 handoff 생성 |
| `langgraph_flow.py` | Financial Analyst Agent LangGraph |
| `output_schema.json` | Financial Analyst 출력 스키마 |
| `Agent.md` | Financial Analyst Agent v3.4 명세 |
| `shared/persona_rules.md` | persona 규칙 |
| `shared/validation_rules.json` | 검증 규칙 |
| `tests/` | Financial Agent 테스트 |

Financial Analyst Agent 설계상 DART는 재무 claim의 primary evidence이고, News는 촉매/리스크 context, YFinance는 시장 반응 context로만 사용한다. 이 Agent는 매수/매도/보유 판단을 직접 하지 않도록 설계되어 있다.

### Financial SY Agent

위치:

```text
src/Agent_Team/Financial_Agent/SY_Agent/
```

주요 역할:

- Financial Analyst output에서 claim 추출
- DART 원천값과 보고서 수치 대조
- 질문/답변 기반으로 claim의 keep/revise/delete 판단
- 검증 반영 Financial report 재출력

주요 파일:

| 파일 | 역할 |
| --- | --- |
| `langgraph_flow.py` | SY Agent LangGraph runner |
| `run_validation.py` | rule-based 검증 및 source audit |
| `run_pipeline.py` | Financial Analyst 생성부터 SY 검증까지 연결 |
| `run_regression_tests.py` | 최소 regression test |
| `output_schema.json` | 검증 출력 스키마 |

기본 산출물 경로:

```text
Output_total/Financial/<run_key>/agent_pipeline/
  pipeline_financial_analyst_report_output.json
  pipeline_financial_analyst_report_trace.json
  pipeline_sy_validation_output.json
  pipeline_sy_validation_trace.json
  pipeline_verified_financial_report_output.json
  pipeline_manifest.json
```

## 6. News Agent

위치:

```text
src/Agent_Team/News_Agent/
```

주요 책임:

- Google News RSS 기반 뉴스 수집
- DART 사업 섹션 수집/파싱/chunking으로 corporate context 생성
- 뉴스 이벤트 정규화, 중복 제거, 클러스터링, 랭킹
- 월별 context export 및 LLM 요약
- Financial/YFinance 데이터를 보조 context로 사용한 뉴스 분석 handoff 생성
- News SY Agent로 뉴스 claim 검증

주요 하위 구조:

| 경로 | 역할 |
| --- | --- |
| `collectors/google_news_collector.py` | Google News RSS/HTML 기반 뉴스 수집 |
| `dart/` | DART XML 수집, 사업 섹션 파싱, chunk 생성, schema |
| `io/` | normalization, JSONL/Parquet/JSON 저장 유틸 |
| `ranking/` | embedding, clustering, rerank, scoring |
| `pipelines/` | 뉴스 수집 파이프라인과 corporate context DB 생성 |
| `context_export.py` | 기간별 뉴스 context export 및 LLM summary request 생성 |
| `analysis_agent.py` | 뉴스 분석 input payload, LLM request, handoff/evidence map 생성 |
| `SY_Agent/sy_agent.py` | News claim 추출, 질문, evidence 검증, critic queue 생성 |
| `workflow.py` | NewsWorkflow와 관련 서비스 클래스 |
| `cli.py` | `collect -> export -> llm -> analysis -> sy` 단계 실행 CLI |

News Agent 기본 단계:

```text
collect -> export -> llm -> analysis -> sy
```

상위 레이어가 주로 읽는 파일:

```text
Output_total/News/<run_key>/final_report.json
Output_total/News/<run_key>/final_validation.json
```

`final_report.json`은 raw `news_agent_handoff.json`이 아니라 `output/sy_agent/news_agent_verified_handoff.json`을 복사한 Strategy 입력용 검증 handoff이다. 원본 handoff 구조는 유지하되 `sy_validation`, `verification_summary`, SY 기반 `strategy_handoff_notes`가 추가된다. `final_validation.json`은 상세 claim 검증 결과인 `output/sy_agent/sy_claim_validations.json`을 복사한 파일이다.

News Agent도 최종 투자 판단을 직접 생성하지 않는다. 뉴스 자체 분석, 재무와의 연결, 시장 반응과의 연결, 통합 context를 만들어 Strategy 또는 상위 레이어가 사용할 수 있게 하는 역할이다.

## 7. YFinance Agent

위치:

```text
src/Agent_Team/YFinance_Agent/
```

주요 책임:

- yfinance 기반 주가 OHLCV 수집
- KOSPI 지수 및 USD/KRW 환율 수집
- 5일/20일/60일 수익률, 이동평균, RSI, MACD, Bollinger Band, 변동성, 거래량 지표, OBV, 상대성과 계산
- `selected_date` 기준 market summary 생성
- 차트 PNG 생성
- YFinance Analyst report 생성
- YFinance SY Agent로 시장 분석 claim 검증

주요 파일:

| 파일 | 역할 |
| --- | --- |
| `main.py` | 시장 데이터 수집 CLI |
| `pipeline.py` | 데이터 다운로드, 지표 계산, dataset/manifest/chart 생성 |
| `indicators.py` | 기술지표 계산 |
| `report.py` | 보고서 생성 CLI |
| `reporting.py` | market summary, cross analysis, report JSON/Markdown 생성 |
| `run_pipeline.py` | 수집, 보고서, SY 검증 통합 실행 |
| `SY_Agent/sy_agent.py` | YFinance report claim 검증 |
| `tests/` | indicator, pipeline, reporting 테스트 |

대표 산출물:

```text
Output_total/Y_Finance/<run_key>/
  market_full_dataset.csv
  market_full_dataset.json
  market_summary.json
  market_summary_20251031.csv
  market_summary_20251031.json
  charts/
  manifest.json
  yfinance_analyst_report.json
  yfinance_analyst_report.md
  sy_verified_yfinance_report.json
  yfinance_verified_report.json
  sy_question_answer_log.json
  final_report.json
  final_validation.json
```

`sy_verified_yfinance_report.json`은 SY 상세 검증 wrapper이고, `yfinance_verified_report.json`은 기존 YFinance report schema를 유지하면서 `sy_validation`과 SY 기반 divergence 제약을 반영한 Strategy 입력용 검증 보고서이다. 따라서 `final_report.json`은 `yfinance_verified_report.json`의 alias이고, `final_validation.json`은 `sy_verified_yfinance_report.json`의 alias이다.

## 8. Competitor Agent

위치:

```text
src/Agent_Team/Competitor_Agent/
```

주요 책임:

- Target 기업을 제외한 경쟁사 run을 찾거나 명시된 competitor config를 읽는다.
- 각 경쟁사의 `Financial`, `News`, `Y_Finance` final report를 로드한다.
- LLM으로 경쟁사별 `summary`, `strengths`, `risks`, `data_gaps`를 합성한다.
- 경쟁사별 JSON 및 Markdown 리포트를 생성한다.

기본 입력:

```text
Output_total/News/<competitor_run_key>/final_report.json
Output_total/Financial/<competitor_run_key>/final_report.json
Output_total/Y_Finance/<competitor_run_key>/final_report.json
```

기본 출력:

```text
Output_total/Competitor/<competitor_run_key>/
  competitor_summary_report.json
  competitor_summary_report.md
```

현재 산출물 기준 경쟁사 결과는 `더블유에스아이_20251031`, `위더스제약_20251031`에 대해 존재한다.

이 두 기업은 SK바이오팜 분석을 위한 경쟁사 비교 대상으로 선정된 기업이다. 선정 기준은 네이버증권에서 SK바이오팜 종목의 `종목분석 > 업종분석` 내 경쟁사 비교 페이지에 언급된 기업이라는 점이다.

## 9. Strategy Agent

위치:

```text
src/Agent_Team/Strategy_Agent/
```

주요 책임:

- Target 기업의 Financial/News/YFinance final report 3개를 읽는다.
- 경쟁사 `competitor_summary_report.json` N개를 읽는다.
- Content Planner로 판단 재료를 구조화한다.
- Decision Agent로 최종 `Buy / Hold / Sell` 판단을 생성한다.
- JSON 및 Markdown 형태의 최종 strategy report를 저장한다.

주요 파일:

| 파일 | 역할 |
| --- | --- |
| `agent.py` | 입력 로드, LLM 호출, 결과 저장 등 핵심 로직 |
| `cli.py` | CLI 진입점 |
| `prompts/content_planner.md` | 판단 재료 정리 prompt |
| `prompts/decision_agent.md` | Buy/Hold/Sell 판단 prompt |
| `tests/test_agent.py` | Strategy Agent 테스트 |

기본 출력:

```text
Output_total/Strategy/<target_run_key>/
  strategy_input_bundle.json
  strategy_content_plan.json
  strategy_report.json
  strategy_report.md
```

현재 `Output_total/Strategy/SK바이오팜_20251031`에 Strategy 산출물이 존재한다.

## 10. Orchestration

위치:

```text
src/orchestration/
```

이 디렉터리는 각 팀 Agent의 내부 로직을 직접 합치지 않고, 기존 CLI/공개 진입점을 subprocess로 호출하는 통합 실행 레이어이다.

주요 파일:

| 파일 | 역할 |
| --- | --- |
| `config.py` | 공통 run config 로딩, 날짜 정규화, run_key 생성 |
| `paths.py` | `Output_total` 아래의 run_key 기반 경로 resolver |
| `dependency_graph.py` | 단계별 실행 순서와 의존성 정의 |
| `end_to_end_loop.py` | 전체 실행 루프, subprocess 호출, progress/status 처리 |
| `manifest.py` | `run_manifest.json`, `run_status.json`, `run_trace.json`, `errors.json` 생성 |
| `run_state.py` | StepRecord와 상태 관리 |
| `validators.py` | 팀별 핵심 output 존재 여부와 검증 요약 확인 |
| `cli.py` | `agent-team-loop` 엔트리포인트 |
| `tests/test_end_to_end_loop.py` | orchestration parser 테스트 |

정의된 실행 단계는 다음 순서이다.

```text
1. yfinance_layer_1
2. financial_layer_1
3. news_collect
4. news_export
5. news_llm
6. news_analysis
7. news_sy
8. financial_analyst
9. financial_sy
10. yfinance_report
11. yfinance_sy
```

LLM이 필요한 단계는 `--use-llm`이 설정되어야 실행되고, `--dry-run`, `--reuse-existing`, `--skip-step`, `--continue-on-error` 같은 실행 옵션이 존재한다.

통합 실행 결과는 다음 위치에 저장된다.

```text
Output_total/runs/<run_key>/
  run_config.json
  financial_input_manifest.json
  run_manifest.json
  run_status.json
  run_trace.json
  errors.json
```

## 11. 설정 파일

```text
configs/
  .env
  company_input.json
  company_input_wsi_20251031.json
  company_input_withus_20251031.json
  companies.yaml
  default.yaml
  news_default.yaml
```

| 파일 | 상태 및 역할 |
| --- | --- |
| `company_input.json` | SK바이오팜 기본 입력. `company_code=00878696`, `ticker=326030.KS`, `date_range=20241101-20251031`, `selected_date=20251031`, `llm_model=gpt-4.1-mini`. |
| `company_input_wsi_20251031.json` | 더블유에스아이 입력. `corp_code=01318261`, `ticker=299170.KQ`. |
| `company_input_withus_20251031.json` | 위더스제약 입력. `corp_code=00765851`, `ticker=330350.KQ`. |
| `news_default.yaml` | 뉴스 수집, DART section 추출, chunking, embedding/reranking, clustering, scoring 기본값. |
| `companies.yaml` | 파일은 존재하지만 현재 0 bytes. |
| `default.yaml` | 파일은 존재하지만 현재 0 bytes. |
| `.env` | API 키 등 민감정보 가능성이 있어 내용은 확인하지 않았다. |

`news_default.yaml`의 주요 설정:

- 뉴스 수집 기간 기본값: 365일
- Google News RSS collector 사용
- DART section 추출 대상: 사업의 개요, 주요 제품 및 서비스, 원재료 및 생산설비, 주요계약 및 연구개발활동
- embedding model: `BAAI/bge-m3`
- reranker model: `BAAI/bge-reranker-v2-m3`
- clustering: `hdbscan`
- 출력 형식: `parquet`, `jsonl`

## 12. Output_total 구조

```text
Output_total/
  Financial/
  News/
  Y_Finance/
  Competitor/
  Strategy/
  runs/
```

| 경로 | 내용 |
| --- | --- |
| `Output_total/Financial/<run_key>` | DART 원천/정규화 결과, Financial Analyst/SY 결과, final report |
| `Output_total/News/<run_key>` | 뉴스 context export, 뉴스 handoff, News SY 검증, final report |
| `Output_total/Y_Finance/<run_key>` | 시장 데이터 CSV/JSON, chart, YFinance report/SY 결과, final report |
| `Output_total/Competitor/<run_key>` | 경쟁사 summary report |
| `Output_total/Strategy/<run_key>` | 최종 Strategy input bundle, content plan, strategy report |
| `Output_total/runs/<run_key>` | 통합 실행 config, manifest, status, trace, error 기록 |
| `Output_total/News/artifacts` | 뉴스/DART context DB, raw/events parquet, report context pack, workflow manifest |
| `Output_total/News/inputs` | DART XML 원본 및 report metadata |

현재 `Output_total`에 SK바이오팜 외 더블유에스아이와 위더스제약 산출물이 함께 있는 것은 별도 target 분석이 섞인 것이 아니라, SK바이오팜 최종 Strategy 작성을 위한 경쟁사 비교 데이터가 함께 생성되었기 때문이다. 더블유에스아이와 위더스제약은 네이버증권의 SK바이오팜 `종목분석 > 업종분석` 경쟁사 비교 페이지에 언급된 2개 기업을 기준으로 선정되었다.

현재 확인된 대표 run:

| run_key | 확인된 영역 |
| --- | --- |
| `SK바이오팜_20251031` | Financial, News, Y_Finance, Strategy, runs |
| `더블유에스아이_20251031` | Financial, News, Y_Finance, Competitor, runs |
| `위더스제약_20251031` | Financial, News, Y_Finance, Competitor, runs |

`Output_total/runs`의 현재 상태 파일 기준:

| run_key | status | dry_run | 비고 |
| --- | --- | --- | --- |
| `SK바이오팜_20251031` | `skipped` | `true` | 2026-05-28 기준 dry-run 상태 파일이 남아 있다. 다만 도메인별 산출물과 Strategy 산출물은 존재한다. |
| `더블유에스아이_20251031` | `success` | `false` | 11개 orchestration 단계 모두 성공. |
| `위더스제약_20251031` | `success` | `false` | 11개 orchestration 단계 모두 성공. |

## 13. 테스트 구조

최상위 `tests/`는 비어 있고, 실제 테스트는 각 모듈 내부에 분산되어 있다.

```text
src/Agent_Team/Financial_Agent/tests/
  test_financial_index_calculator.py
  test_pipeline.py

src/Agent_Team/YFinance_Agent/tests/
  test_indicators.py
  test_pipeline.py
  test_reporting.py

src/Agent_Team/Competitor_Agent/tests/
  test_agent.py

src/Agent_Team/Strategy_Agent/tests/
  test_agent.py

src/orchestration/tests/
  test_end_to_end_loop.py
```

`pyproject.toml`에는 pytest 설정으로 `pythonpath = ["src"]`가 지정되어 있다. 개발 의존성은 `pytest>=7.4`이다.

## 14. 실행 예시

프로젝트 루트 기준으로 실행하는 것이 기본 패턴이다.

```bash
cd /home/agent2/Financial_Agent_Final
PYTHONPATH=src python -m Agent_Team.Financial_Agent.main
PYTHONPATH=src python -m Agent_Team.Financial_Agent.SY_Agent.run_pipeline
PYTHONPATH=src python -m Agent_Team.News_Agent.cli --help
PYTHONPATH=src python -m Agent_Team.YFinance_Agent.run_pipeline
PYTHONPATH=src python -m Agent_Team.Competitor_Agent.cli --target-config configs/company_input.json
PYTHONPATH=src python -m Agent_Team.Strategy_Agent.cli --help
PYTHONPATH=src python -m orchestration.cli --help
```

패키지로 설치된 환경에서는 `pyproject.toml`에 등록된 console script도 사용할 수 있다.

## 15. 구조상 중요한 관찰

1. 프로젝트의 핵심은 `Financial`, `News`, `YFinance` 세 도메인 Agent와 각 SY 검증 Agent이다.
2. 각 도메인 Agent는 원칙적으로 최종 투자 판단을 직접 내리지 않고, 검증된 분석 결과 또는 handoff를 만든다.
3. `Strategy_Agent`는 세 도메인 final report와 경쟁사 summary를 바탕으로 최종 Buy/Hold/Sell 판단을 생성하는 레이어이다.
4. `orchestration`은 각 Agent 내부 로직을 재구현하지 않고 기존 CLI를 순서대로 호출하는 얇은 통합 실행 레이어이다.
5. 산출물은 대부분 `Output_total`에 있으며, `run_key` 단위로 재현성과 추적성을 확보하려는 구조이다.
6. `.venv`, `.pytest_cache`, `__pycache__`, `.pyc` 파일이 다수 존재한다. 구조 이해나 배포 관점에서는 캐시/로컬 실행 산물로 분리해서 보는 것이 좋다.
7. `configs/.env`와 최상위 `.env`가 있어 OpenAI, DART, Google/Gemini 등 외부 API 키 기반 실행을 전제로 한다.
8. `schemas/`, `src/Preprocessed`, `src/Raw`, `src/shared/Report_output`은 현재 비어 있어 향후 확장 또는 과거 구조의 placeholder로 보인다.

## 16. 빠른 파악용 요약

이 프로젝트는 다음 흐름으로 이해하면 된다.

```text
회사 입력 config
  -> YFinance Layer 1: 주가/KOSPI/환율/기술지표 수집
  -> Financial Layer 1: DART 재무제표 수집/정규화
  -> News Layer 1: 뉴스 및 DART 사업 context 수집
  -> News LLM 요약/분석 + News SY 검증
  -> Financial Analyst + Financial SY 검증
  -> YFinance Analyst + YFinance SY 검증
  -> Competitor Agent: 경쟁사 summary 생성
  -> Strategy Agent: Target + competitors 기반 최종 전략 리포트 생성
```

따라서 코드 구조를 볼 때는 `src/Agent_Team/*_Agent`가 도메인별 실제 기능이고, `src/orchestration`은 이 기능들을 `Output_total/<domain>/<run_key>` 계약에 맞춰 실행하고 추적하는 상위 레이어로 보면 된다.
