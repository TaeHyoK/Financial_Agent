# Financial Agent Final 프로젝트 진행 현황 및 향후 플래닝 보고서

작성일: 2026-05-21  
프로젝트 경로: `/home/agent2/Financial_Agent_Final`

## 1. 프로젝트 개요

본 프로젝트는 기업 투자 판단을 보조하기 위한 다중 금융 에이전트 시스템을 구축하는 것을 목표로 한다. 현재 구현의 중심은 기업별로 시장 데이터, 뉴스 데이터, DART 재무 데이터를 수집하고, 각 도메인별 분석 에이전트가 보고서를 생성한 뒤, SY 검증 에이전트가 각 분석 결과의 근거성과 일관성을 점검하는 구조이다.

현재까지의 개발 범위는 주로 Layer 1부터 Layer 2까지에 해당한다.

- Layer 1: 원천 데이터 수집 및 정규화
- Layer 1.5: 팀별 전처리, 요약, handoff 생성
- Layer 2: 도메인별 Analyst Agent 및 SY 검증 Agent 실행

아직 Competitor Analysis Agent, 최종 의사결정 Agent, Critique Agent는 최종 에이전트 형태로 완성되지 않았다. 코드상 일부 초기 스캐폴딩 또는 실험용 파일이 존재할 수 있으나, 교수님 보고 기준으로는 미구현 또는 후속 구현 대상으로 보는 것이 정확하다.

## 2. 현재 아키텍처

현재 프로젝트는 아래 세 개의 주요 분석 에이전트를 중심으로 구성되어 있다.

| 구분 | 위치 | 주요 역할 | 현재 상태 |
| --- | --- | --- | --- |
| Financial Agent | `src/Agent_Team/Financial_Agent` | DART 공시 수집, 재무제표 정규화, 재무지표 계산, Fundamental Analyst 보고서 생성, SY 검증 | 구현 및 샘플 실행 완료 |
| News Agent | `src/Agent_Team/News_Agent` | 뉴스 수집, 뉴스 context export, LLM 요약 및 뉴스 분석 handoff 생성, SY 검증 | 구현 및 샘플 실행 완료 |
| YFinance Agent | `src/Agent_Team/YFinance_Agent` | 주가, KOSPI, 환율 데이터 수집, 기술지표 계산, 시장 보고서 생성, SY 검증 | 구현 및 샘플 실행 완료 |
| Orchestration | `src/orchestration` | 위 세 팀의 실행 순서, 경로, manifest, status, validation 관리 | 1차 통합 구현 완료 |

전체 실행 흐름은 다음과 같다.

```text
YFinance Layer 1
Financial/DART Layer 1
News collect/export/LLM
News Analysis + News SY
Financial Analyst + Financial SY
YFinance Report + YFinance SY
통합 run_manifest / run_status / run_trace 생성
```

각 도메인 Agent는 최종 투자 의견을 직접 내리지 않는다. 현재 구조에서는 News, Financial, YFinance Agent가 각각 분석과 검증 결과를 만들고, 최종 `buy/sell/hold` 판단은 향후 최종 의사결정 Agent에서 수행하도록 설계되어 있다.

## 3. 구현 완료 및 진행 현황

### 3.1 공통 실행 설정

현재 기본 입력 설정은 `configs/company_input.json`에 정의되어 있다.

```json
{
  "company_name": "SK바이오팜",
  "ticker": "326030.KS",
  "date_range": "20241101-20251031",
  "selected_date": "20251031"
}
```

공통 실행 단위는 `<회사명>_<YYYYMMDD>` 형식의 `run_key`로 관리된다. 예시는 다음과 같다.

- `SK바이오팜_20251031`
- `리가켐바이오_20251031`

산출물은 모두 `Output_total` 하위에 저장되며, 팀별 결과와 통합 실행 결과가 분리되어 있다.

```text
Output_total/
  Financial/<run_key>/
  News/<run_key>/
  Y_Finance/<run_key>/
  runs/<run_key>/
```

### 3.2 Financial Agent

Financial Agent는 DART 기반 재무 데이터 수집과 Fundamental Analyst 보고서 생성을 담당한다.

현재 구현된 기능은 다음과 같다.

- DART 공시 데이터 수집
- 재무제표 canonical JSON 생성
- `dart_master.json`, `dart_main.json`, `dart_lightweight.json` 생성
- 재무지표 계산
- Financial Analyst LLM 보고서 생성
- SY Agent를 통한 재무 분석 결과 검증
- 검증 완료 보고서를 `final_report.json`으로 alias 처리

주요 산출물은 다음 위치에 생성된다.

```text
Output_total/Financial/<run_key>/dart_main.json
Output_total/Financial/<run_key>/dart_lightweight.json
Output_total/Financial/<run_key>/agent_pipeline/pipeline_verified_financial_report_output.json
Output_total/Financial/<run_key>/final_report.json
Output_total/Financial/<run_key>/final_validation.json
```

### 3.3 News Agent

News Agent는 뉴스 수집, 기간별 요약, 뉴스 분석 handoff 생성, SY 검증을 담당한다.

현재 구현된 기능은 다음과 같다.

- Google News 기반 뉴스 수집
- 월별 context export
- LLM 기반 뉴스 요약
- 뉴스 단독 분석 및 재무/시장 데이터와의 cross-domain 분석
- News SY Agent를 통한 주장 단위 검증
- 최종 의사결정 Agent가 읽을 수 있는 `news_agent_handoff.json` 생성

주요 산출물은 다음 위치에 생성된다.

```text
Output_total/News/<run_key>/output/news_agent_handoff.json
Output_total/News/<run_key>/output/sy_agent/sy_claim_validations.json
Output_total/News/<run_key>/final_report.json
Output_total/News/<run_key>/final_validation.json
```

News Agent는 현재 구조상 최종 투자 의견을 생성하지 않고, 최종 의사결정 Agent가 사용할 수 있는 뉴스 기반 판단 재료를 제공하는 역할로 제한되어 있다.

### 3.4 YFinance Agent

YFinance Agent는 시장 데이터와 기술지표 분석을 담당한다.

현재 구현된 기능은 다음과 같다.

- YFinance 기반 주가 OHLCV 수집
- KOSPI 지수 및 USD/KRW 환율 데이터 수집
- 수익률, 이동평균, RSI, MACD, Bollinger Band, 변동성, 거래량 지표 계산
- selected date 기준 시장 요약 생성
- 차트 이미지 생성
- YFinance Analyst 보고서 생성
- SY Agent를 통한 시장 분석 보고서 검증

주요 산출물은 다음 위치에 생성된다.

```text
Output_total/Y_Finance/<run_key>/market_full_dataset.json
Output_total/Y_Finance/<run_key>/market_summary.json
Output_total/Y_Finance/<run_key>/yfinance_analyst_report.json
Output_total/Y_Finance/<run_key>/sy_verified_yfinance_report.json
Output_total/Y_Finance/<run_key>/final_report.json
Output_total/Y_Finance/<run_key>/final_validation.json
```

### 3.5 통합 오케스트레이션

`src/orchestration`에 Layer 1부터 Layer 2까지를 순차 실행하기 위한 1차 통합 오케스트레이터가 구현되어 있다.

주요 구성 요소는 다음과 같다.

- `config.py`: 공통 실행 config 로딩
- `paths.py`: run_key 기반 output path resolver
- `dependency_graph.py`: 단계별 실행 순서 및 의존성 정의
- `end_to_end_loop.py`: subprocess 기반 통합 실행 루프
- `manifest.py`: run_manifest, run_status, run_trace, errors 파일 생성
- `validators.py`: 팀별 산출물 존재 여부와 주요 검증 결과 확인

통합 실행 후에는 다음 파일들이 생성된다.

```text
Output_total/runs/<run_key>/run_config.json
Output_total/runs/<run_key>/run_manifest.json
Output_total/runs/<run_key>/run_status.json
Output_total/runs/<run_key>/run_trace.json
Output_total/runs/<run_key>/errors.json
```

## 4. 샘플 실행 결과

### 4.1 SK바이오팜 실행 결과

`SK바이오팜_20251031` 기준 통합 실행은 성공 상태로 기록되어 있다.

- run status: `success`
- status updated at: `2026-05-15T20:13:25Z`
- 실행 단계 수: 11개
- 실패 단계: 없음
- errors: 빈 배열

성공한 단계는 다음과 같다.

```text
yfinance_layer_1
financial_layer_1
news_collect
news_export
news_llm
news_analysis
news_sy
financial_analyst
financial_sy
yfinance_report
yfinance_sy
```

검증 결과 요약은 다음과 같다.

- Financial SY: `overall_status = pass`
- News SY: 총 34개 claim 중 weaken 15개, hallucination candidate 19개
- YFinance SY: 총 21개 claim 중 verified 12개, weaken 9개
- 통합 manifest, status, trace 파일 생성 완료

해석상 주의할 점은 News SY에서 hallucination candidate 비율이 높게 나타났다는 점이다. 이는 뉴스 분석 결과의 일부 주장에 대해 근거 연결이 충분하지 않거나 표현을 약화해야 한다는 의미이므로, 향후 뉴스 evidence mapping과 프롬프트 개선이 필요하다.

### 4.2 리가켐바이오 실행 결과

`리가켐바이오_20251031` 기준 실행은 부분 성공 상태로 기록되어 있다.

- run status: `partial_success`
- status updated at: `2026-05-19T07:19:35Z`
- 대부분의 단계는 성공
- 실패 단계: `yfinance_sy`

실패 원인은 다음과 같다.

```text
ModuleNotFoundError: No module named 'dotenv'
```

즉, YFinance SY Agent 실행 환경에서 `python-dotenv` 패키지를 찾지 못해 검증 단계가 실패했다. Financial Agent와 News Agent 산출물은 생성되었고, YFinance Analyst 보고서도 생성되었으나, YFinance SY 최종 검증 산출물은 누락되어 있다.

후속 조치로는 실행 환경에 `python-dotenv` 설치 여부를 보장하고, 리가켐바이오 run을 재실행하여 `sy_verified_yfinance_report.json`과 `final_validation.json`을 생성해야 한다.

## 5. 현재 미구현 또는 미완성 항목

현재 교수님께 보고할 때 명확히 구분해야 할 미구현 항목은 다음과 같다.

| 항목 | 현재 상태 | 후속 구현 방향 |
| --- | --- | --- |
| 최종 의사결정 Agent | 최종 의사결정 에이전트로는 미완성 | Financial, News, YFinance 결과를 종합하여 `buy/sell/hold` 또는 투자 가이드라인 생성 |
| Critique Agent | 미구현 | 최종 의사결정 결과의 근거성, 논리 일관성, 과대해석 여부, 데이터 누락 여부 검토 |

현재 코드 기준으로는 세 도메인 Agent와 각 SY 검증 Agent 중심의 실행 파이프라인이 핵심 구현 범위이며, 최종 의사결정 Agent와 Critique Agent는 후속 구현 대상으로 구분하는 것이 적절하다.

## 6. 향후 플래닝

### 6.1 단기 계획

가장 먼저 안정화해야 할 부분은 현재 구현된 Layer 1부터 Layer 2까지의 실행 재현성이다.

단기 작업 계획은 다음과 같다.

1. 리가켐바이오 `yfinance_sy` 실패 원인 해결
2. `python-dotenv` 등 실행 의존성 설치 및 requirements 정리
3. SK바이오팜 외 기업에서도 동일한 E2E 실행이 재현되는지 확인
4. `run_manifest.json`, `run_status.json`, `errors.json`의 schema를 고정
5. News SY에서 hallucination candidate가 높게 나온 claim의 근거 연결 방식 개선
6. YFinance report/SY 단계의 token usage 기록 누락 보완

### 6.2 중기 계획

중기적으로는 현재 도메인별 Agent 산출물을 기반으로 최종 의사결정 레이어를 구현해야 한다.

1. 도메인별 산출물 schema 고정
2. Financial, News, YFinance 근거 우선순위 설계
3. 도메인별 claim 충돌 처리 규칙 정의
4. `buy/sell/hold` 또는 투자 가이드라인 출력 schema 설계
5. 최종 의사결정 Agent 입력으로 사용할 통합 handoff 생성

이 단계가 완료되면 최종 의사결정 Agent가 세 도메인 보고서를 일관된 근거 구조로 통합할 수 있다.

### 6.3 장기 계획

장기적으로는 최종 의사결정 Agent와 Critique Agent를 순차적으로 구현해야 한다.

최종 의사결정 Agent의 목표는 다음과 같다.

- Financial, News, YFinance 결과 통합
- 도메인별 근거 가중치 설정
- 최종 투자 가이드라인 생성
- `buy/sell/hold` 또는 유사한 최종 판단 출력
- 최종 판단의 핵심 근거와 리스크를 명확히 기록

Critique Agent의 목표는 다음과 같다.

- 최종 의사결정 Agent의 최종 판단 검토
- 근거 없는 주장, 과도한 해석, 데이터 누락 점검
- 도메인별 보고서와 최종 판단 간 충돌 여부 검토
- 최종 제출용 보고서의 신뢰도 보강

최종 목표 아키텍처는 다음과 같다.

```text
Financial Agent
News Agent
YFinance Agent
        |
        v
최종 의사결정 Agent
        |
        v
Critique Agent
        |
        v
Final Investment Report
```

## 7. 주요 리스크 및 보완 필요사항

현재 확인된 주요 리스크는 다음과 같다.

| 리스크 | 설명 | 대응 방향 |
| --- | --- | --- |
| 실행 환경 의존성 | `python-dotenv` 누락으로 일부 SY 검증 실패 사례 발생 | requirements와 실제 실행 환경 동기화 |
| Look-ahead bias | selected date 이후 공개된 DART 공시가 분석에 포함될 가능성 | `--strict-as-of-date` 옵션 설계 및 적용 |
| 뉴스 근거 연결 | News SY에서 근거 부족 claim 비율이 높게 나타남 | evidence map 강화 및 프롬프트 개선 |
| 최종 의사결정/Critique 미구현 | 최종 투자 판단 자동화는 아직 완성되지 않음 | 최종 의사결정 레이어와 Critique 단계 순차 구현 |
| token usage 추적 누락 | YFinance report/SY token usage가 manifest에 완전히 기록되지 않음 | YFinance Agent usage logging 추가 |

## 8. 정리

현재 프로젝트는 Financial, News, YFinance 세 개의 도메인 Agent와 각 SY 검증 Agent를 중심으로 Layer 1부터 Layer 2까지의 파이프라인을 구현한 상태이다. SK바이오팜 기준으로는 전체 E2E 실행이 성공했고, 리가켐바이오 기준으로도 대부분의 단계가 성공했으나 YFinance SY 단계에서 환경 의존성 문제가 확인되었다.

다만 아직 최종 의사결정 Agent와 Critique Agent는 완성되지 않았다. 따라서 현재까지의 성과는 “도메인별 데이터 수집, 분석, 검증, 통합 manifest 관리까지 가능한 1차 Agent Team 파이프라인 구축”으로 정리할 수 있다. 향후에는 실행 안정화 이후 최종 의사결정 Agent와 Critique Agent 순서로 확장하여 최종 투자 판단 및 검토까지 연결하는 것이 다음 개발 목표이다.
