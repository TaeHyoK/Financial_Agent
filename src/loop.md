# Agent Team 통합 실행 가이드라인

## 목적

이 문서는 `/home/agent2/Financial_Agent_Final` 프로젝트에서 `Agent_Team` 하위의 팀별 Agent를 하나의 실행 흐름으로 통합하기 위한 가이드라인이다.

현재 `News_Agent`, `Financial_Agent`, `YFinance_Agent`는 각자 Layer 1부터 Layer 2까지 독립적으로 구현되어 있고, 실행 파이프라인도 팀별로 따로 돌아간다. 앞으로의 목표는 각 팀별 독립 구현을 유지하되, 최상위 레벨에서 한 번의 실행으로 Layer 1부터 Layer 2까지 순차적으로 완료되도록 통합하는 것이다.

Layer 1.5는 각 Layer 2 내부에서 팀별로 이미 처리한 것으로 간주한다. 따라서 통합 작업자는 Layer 1.5를 별도 공통 모듈로 다시 만들거나 재해석하지 않는다.

## 참고 아키텍처

사용자가 제공한 레이어 다이어그램은 아래 흐름으로 이해한다.

```text
Layer 1
  Index(KOSPI)
  Currency
  Y-Finance
  News
  DART
        |
        v
Layer 1.5
  Data Preprocessing / Summarization Agent
        |
        v
Layer 2
  Y-Finance Agent
    - Stock Price Analyst
    - SY Agent

  News Analysis Agent
    - Sentiment Analysis
    - SY Agent

  Financial Analysis Agent
    - Fundamental Analyst
    - SY Agent
        |
        v
Layer 3
  Critic Agent
```

현재 통합 목표는 이 전체 그림 중 **Layer 1부터 Layer 2까지**다.

```text
이번 통합 범위:
  Layer 1 데이터 수집
  Layer 1.5 팀별 전처리/요약 산출물 사용
  Layer 2 팀별 Analyst Agent 실행
  Layer 2 팀별 SY Agent 검증

이번 통합 범위 제외:
  Layer 3 Critic Agent 실행
```

다만 Layer 2 output은 상위 통합 레이어가 바로 읽을 수 있도록 경로와 manifest 계약을 명확히 맞춰야 한다.

## 프로젝트 기준 경로

```text
PROJECT_ROOT=/home/agent2/Financial_Agent_Final
SRC_ROOT=/home/agent2/Financial_Agent_Final/src
AGENT_TEAM_ROOT=/home/agent2/Financial_Agent_Final/src/Agent_Team
OUTPUT_ROOT=/home/agent2/Financial_Agent_Final/Output_total
CONFIG_ROOT=/home/agent2/Financial_Agent_Final/configs
```

통합 실행은 항상 `PROJECT_ROOT`를 기준으로 수행한다.

```bash
cd /home/agent2/Financial_Agent_Final
PYTHONPATH=src ...
```

## 현재 팀별 구조

```text
src/Agent_Team/
  Financial_Agent/
  News_Agent/
  YFinance_Agent/
```

각 Agent의 책임은 다음과 같다.

```text
Financial_Agent:
  DART 공시 수집
  재무제표 정규화
  재무지표 계산
  Financial Analyst report 생성
  SY 검증

News_Agent:
  뉴스 수집
  뉴스 context export
  LLM 요약 및 분석
  News Agent handoff 생성
  SY 검증

YFinance_Agent:
  YFinance 시장 데이터 수집
  주가, KOSPI, 환율, 기술지표 계산
  Market summary 생성
  YFinance Analyst report 생성
  SY 검증
```

## 통합 대상 레이어

통합 대상은 Layer 1부터 Layer 2까지다.

```text
Layer 1:
  각 도메인의 원천 데이터 수집 및 정규화
  Index(KOSPI), Currency, Y-Finance, News, DART 수집
  현재 코드 기준 Index(KOSPI)와 Currency는 YFinance_Agent 내부에서 함께 처리

Layer 1.5:
  각 팀별 중간 정제, context export, lightweight handoff 생성
  이미 각 Layer 2 내부에서 처리한 것으로 간주
  통합 오케스트레이터에서 별도 재구현하지 않음

Layer 2:
  각 팀별 Analyst Agent 실행
  각 팀별 SY 검증
  상위 통합 레이어가 읽을 수 있는 검증된 handoff/report 산출
```

다이어그램 기준 Layer 2의 역할 매핑은 다음과 같다.

```text
Y-Finance Agent:
  Stock Price Analyst
  가격, 수익률, 거래량, 기술지표, KOSPI 상대성과, 환율 context 분석
  SY Agent 검증 포함

News Analysis Agent:
  Sentiment Analysis
  뉴스 이슈, 감성, 촉매, 리스크, 기간별 context 분석
  SY Agent 검증 포함

Financial Analysis Agent:
  Fundamental Analyst
  DART 재무제표, 재무지표, 현금흐름, 재무상태표, 자본구조, 부채, 유동성 분석
  SY Agent 검증 포함
```

Layer 3는 현재 문서에서 후속 확장 대상으로만 정의한다.

```text
Layer 3:
  Critic Agent
```

## 통합 설계 원칙

1. 기존 팀별 Agent 내부 로직은 최대한 보존한다.
2. 통합 레이어는 각 Agent의 CLI 또는 공개 진입점만 호출한다.
3. 각 팀별 output 경로를 통일된 `run_key` 기준으로 맞춘다.
4. 팀별 파이프라인은 실패 시 어느 단계에서 실패했는지 명확히 기록한다.
5. News, DART, YFinance는 최종 투자판단을 하지 않는다.
6. 최종 투자판단 레이어는 현재 통합 범위에 포함하지 않는다.
7. 통합 실행은 재현 가능해야 하며, 입력 config와 manifest를 남겨야 한다.
8. Layer 1.5는 팀별 구현을 신뢰하고, 통합 단계에서 중복 구현하지 않는다.

## 공통 입력 계약

통합 실행의 최소 입력은 회사와 기준일 정보다.

```json
{
  "company_code": "00878696",
  "company_name": "SK바이오팜",
  "ticker": "326030.KS",
  "corp_code": "00878696",
  "date_range": "20241101-20251031",
  "selected_date": "20251031"
}
```

현재 기본 config 예시는 다음 파일이다.

```text
/home/agent2/Financial_Agent_Final/configs/company_input.json
```

통합 오케스트레이터를 만들 때는 Financial 전용 config 이름에 종속되지 않도록 한다. 장기적으로는 아래와 같은 공통 run config를 권장한다.

```text
/home/agent2/Financial_Agent_Final/configs/runs/<company>_<YYYYMMDD>.json
```

## run_key 규칙

모든 팀별 output은 같은 `run_key`를 사용한다.

```text
run_key = <company_name>_<selected_date YYYYMMDD>
```

예시:

```text
SK바이오팜_20251031
삼성전자_20251031
셀트리온_20251031
```

슬래시나 경로 구분자가 포함될 수 있는 회사명은 파일시스템 안전 문자열로 치환한다.

## 통합 output 구조

통합 실행 결과는 반드시 `Output_total` 아래에 저장한다. Agent 코드 폴더 내부에 실행 결과를 저장하지 않는다.

```text
Output_total/
  Financial/<run_key>/
  News/<run_key>/
  Y_Finance/<run_key>/
  runs/<run_key>/
```

`Output_total/runs/<run_key>`는 통합 실행 레벨의 manifest, status, trace를 저장하는 위치로 사용한다.

권장 구조:

```text
Output_total/runs/<run_key>/
  run_config.json
  run_manifest.json
  run_status.json
  run_trace.json
  errors.json
```

## 팀별 output 계약

### Financial_Agent

기본 output:

```text
Output_total/Financial/<run_key>/
  dart_master.json
  dart_2y_handoff.json
  dart_main.json
  dart_lightweight.json
  agent_pipeline/
    pipeline_financial_analyst_report_output.json
    pipeline_financial_analyst_report_trace.json
    pipeline_sy_validation_output.json
    pipeline_sy_validation_trace.json
    pipeline_verified_financial_report_output.json
    pipeline_manifest.json
```

상위 통합 레이어가 읽을 핵심 파일:

```text
Output_total/Financial/<run_key>/dart_lightweight.json
Output_total/Financial/<run_key>/dart_main.json
Output_total/Financial/<run_key>/agent_pipeline/pipeline_verified_financial_report_output.json
```

### News_Agent

기본 output:

```text
Output_total/News/<run_key>/
  context_exports/
  output/
    news_agent_input_payload.json
    news_agent_llm_request.json
    news_agent_handoff.json
    news_agent_evidence_map.json
    sy_agent/
      sy_claim_validations.json
      sy_audit_trace.json
      news_agent_verified_handoff.json
```

상위 통합 레이어가 읽을 핵심 파일:

```text
Output_total/News/<run_key>/output/news_agent_handoff.json
Output_total/News/<run_key>/output/sy_agent/sy_claim_validations.json
```

### YFinance_Agent

기본 output은 장기적으로 run_key 단위 폴더로 통일한다.

권장 구조:

```text
Output_total/Y_Finance/<run_key>/
  market_full_dataset.csv
  market_full_dataset.json
  market_summary.json
  market_summary_<YYYYMMDD>.json
  charts/
  manifest.json
  yfinance_analyst_report.md
  yfinance_analyst_report.json
  sy_verified_yfinance_report.json
  yfinance_verified_report.json
```

현재 일부 YFinance output은 `Output_total/Y_Finance` 바로 아래에 생성될 수 있다. 통합 작업 시에는 run_key 단위 저장으로 정리하는 것을 우선한다.

상위 통합 레이어가 읽을 핵심 파일:

```text
Output_total/Y_Finance/<run_key>/market_summary.json
Output_total/Y_Finance/<run_key>/market_summary_<YYYYMMDD>.json
Output_total/Y_Finance/<run_key>/sy_verified_yfinance_report.json
```

## 권장 통합 실행 순서

통합 실행은 데이터 의존성을 고려해 아래 순서를 기본으로 한다.

```text
1. 공통 run_config 로드
2. run_key 생성
3. output 디렉터리 생성
4. YFinance Layer 1 실행
5. Financial/DART Layer 1 실행
6. News Layer 1 실행
7. News context export 확인
8. Financial Layer 2 실행
9. YFinance Layer 2 실행
10. News Layer 2 실행
11. 팀별 SY 검증 결과 확인
12. 통합 manifest/status 생성
```

의존성 관점에서 보면 다음과 같다.

```text
YFinance Layer 1:
  market summary와 market dataset 생성

Financial Layer 1:
  dart_main, dart_lightweight 생성

News Layer 1:
  news collection, context export 생성

Financial Layer 2:
  dart_main + yfinance summary + news context를 사용

YFinance Layer 2:
  market dataset + news context + dart_lightweight를 사용

News Layer 2:
  news context + dart_lightweight + market summary를 사용
```

팀별 구현 상태에 따라 일부 순서는 조정 가능하지만, cross-domain 입력을 쓰는 Layer 2는 필요한 Layer 1 산출물이 먼저 존재해야 한다.

## 현재 기준 팀별 실행 예시

### Financial Layer 1

```bash
cd /home/agent2/Financial_Agent_Final

PYTHONPATH=src python -m Agent_Team.Financial_Agent.main \
  --input /home/agent2/Financial_Agent_Final/configs/company_input.json \
  --env-file /home/agent2/Financial_Agent_Final/configs/.env
```

### Financial Layer 2

```bash
cd /home/agent2/Financial_Agent_Final

PYTHONPATH=src python -m Agent_Team.Financial_Agent.SY_Agent.run_pipeline \
  --financial-manifest /home/agent2/Financial_Agent_Final/src/Agent_Team/Financial_Agent/input_manifest.skbiopharm_20251031.json \
  --env-file /home/agent2/Financial_Agent_Final/configs/.env \
  --use-llm \
  --llm-provider openai \
  --llm-model auto
```

### News 전체 실행

```bash
cd /home/agent2/Financial_Agent_Final

PYTHONPATH=src python -m Agent_Team.News_Agent.cli \
  --collect-date 2025-10-31 \
  --company-id 00878696 \
  --company-name SK바이오팜 \
  --ticker 326030.KS \
  --corp-code 00878696 \
  --granularity month \
  --split-by-period
```

### News Layer 2 분석 실행 시 cross-domain 명시 예시

```bash
cd /home/agent2/Financial_Agent_Final

PYTHONPATH=src python -m Agent_Team.News_Agent.cli \
  --phase analysis \
  --collect-date 2025-10-31 \
  --company-id 00878696 \
  --company-name SK바이오팜 \
  --ticker 326030.KS \
  --corp-code 00878696 \
  --context-export-dir /home/agent2/Financial_Agent_Final/Output_total/News/SK바이오팜_20251031/context_exports \
  --dart-lightweight /home/agent2/Financial_Agent_Final/Output_total/Financial/SK바이오팜_20251031/dart_lightweight.json \
  --market-summary /home/agent2/Financial_Agent_Final/Output_total/Y_Finance/SK바이오팜_20251031/market_summary.json \
  --analysis-output-dir /home/agent2/Financial_Agent_Final/Output_total/News/SK바이오팜_20251031/output
```

### YFinance Layer 1

```bash
cd /home/agent2/Financial_Agent_Final/src/Agent_Team/YFinance_Agent

python main.py \
  --input /home/agent2/Financial_Agent_Final/configs/company_input.json \
  --output-dir /home/agent2/Financial_Agent_Final/Output_total/Y_Finance/SK바이오팜_20251031 \
  --start-date 20241101 \
  --end-date 20251031 \
  --selected-date 20251031 \
  --kospi-ticker ^KS11 \
  --fx-ticker KRW=X
```

### YFinance Layer 2

YFinance report 단계는 이미 생성된 YFinance, News, DART JSON을 사용한다. 통합 구현 시에는 기본 경로 하드코딩 대신 run_key 기반 경로를 명시하거나 manifest 기반으로 전달하도록 정리한다.

```bash
cd /home/agent2/Financial_Agent_Final/src/Agent_Team/YFinance_Agent

python report.py \
  --env-file /home/agent2/Financial_Agent_Final/configs/.env
```

## 통합 오케스트레이터가 해야 할 일

향후 Codex가 코드 수정을 시작할 때는 다음 순서로 작업한다.

1. 공통 run config loader 작성
2. `run_key` 생성 유틸 작성
3. 팀별 output path resolver 작성
4. 기존 Agent CLI를 subprocess 또는 함수 호출로 실행하는 wrapper 작성
5. 단계별 성공/실패 status 기록
6. 팀별 산출물 존재 여부 검증
7. `Output_total/runs/<run_key>/run_manifest.json` 생성
8. end-to-end 테스트 스크립트 작성

권장 위치:

```text
src/orchestration/
  __init__.py
  run_agent_team.py
  config.py
  paths.py
  validators.py
```

또는 더 단순한 초기 버전:

```text
src/run_agent_team.py
```

초기 구현은 단순성과 안정성을 우선한다. 각 팀 내부 로직을 리팩터링하지 말고, 기존 CLI를 안정적으로 호출하는 방식으로 시작한다.

## 통합 run_manifest 계약

통합 실행이 끝나면 아래 정보를 담은 manifest를 남긴다.

```json
{
  "run_key": "SK바이오팜_20251031",
  "company_name": "SK바이오팜",
  "ticker": "326030.KS",
  "corp_code": "00878696",
  "selected_date": "20251031",
  "date_range": "20241101-20251031",
  "status": "success",
  "outputs": {
    "financial": {
      "dart_main": "...",
      "dart_lightweight": "...",
      "verified_report": "..."
    },
    "news": {
      "handoff": "...",
      "sy_validations": "..."
    },
    "yfinance": {
      "market_summary": "...",
      "verified_report": "..."
    }
  },
  "steps": [
    {
      "name": "financial_layer_1",
      "status": "success",
      "started_at": "",
      "ended_at": ""
    }
  ]
}
```

## 검증 기준

통합 실행 성공 조건은 다음이다.

```text
Financial:
  dart_main.json 존재
  dart_lightweight.json 존재
  pipeline_verified_financial_report_output.json 존재
  SY validation overall_status == pass

News:
  news_agent_handoff.json 존재
  sy_claim_validations.json 존재
  unsupported/contradicted claim 처리 결과 확인

YFinance:
  market_summary 또는 market_summary_<YYYYMMDD>.json 존재
  yfinance analyst report 존재
  sy_verified_yfinance_report.json 존재

Global:
  run_manifest.json 존재
  run_status.json 존재
  실패 단계가 없거나 실패 단계가 명시적으로 기록됨
```

## 실패 처리 원칙

통합 실행 중 한 팀이 실패하면 다음 원칙을 따른다.

1. 실패한 명령어, stderr, exit code를 기록한다.
2. 이미 성공한 팀별 산출물은 삭제하지 않는다.
3. 후속 단계가 해당 산출물에 의존하면 후속 단계를 `skipped`로 기록한다.
4. 전체 status는 `failed` 또는 `partial_success`로 기록한다.
5. 재실행 시 기존 산출물을 사용할지 새로 생성할지 옵션으로 분리한다.

권장 status:

```text
pending
running
success
failed
skipped
partial_success
```

## 환경변수와 API Key

기본 env 파일:

```text
/home/agent2/Financial_Agent_Final/configs/.env
```

필요 key:

```text
DART_API_KEY
OPENAI_API_KEY
OPENAI_MODEL optional
```

API key 값은 로그, manifest, trace에 직접 기록하지 않는다. 기록이 필요하면 `api_key_loaded: true/false` 수준만 남긴다.

## 시점 기준 주의사항

통합 리포트는 `selected_date` 기준 분석인지, 최신 사용 가능 데이터 기준 분석인지 명확히 해야 한다.

특히 DART 공시는 접수일이 `selected_date` 이후일 수 있다. 백테스트 또는 시점 엄격성이 필요한 실행에서는 `selected_date` 당시에 공개된 공시만 사용해야 한다.

예시 문제:

```text
selected_date = 2025-10-31
사용 공시 = 2025-11-14 접수 분기보고서
```

이 경우 2025-10-31 기준 리포트로 보면 look-ahead bias가 발생한다.

통합 오케스트레이터는 장기적으로 다음 옵션을 제공해야 한다.

```text
--strict-as-of-date
```

이 옵션이 켜지면 selected_date 이후 공개된 데이터는 사용하지 않는다.

## Cross-Domain 명칭 기준

각 팀별 cross analysis 명칭은 도메인 기준을 명확히 한다.

Financial Agent 기준:

```text
news_plus_dart
market_plus_dart
market_plus_news_plus_dart
```

News Agent 기준:

```text
news_plus_financial
news_plus_market
news_plus_financial_plus_market
```

YFinance Agent 기준:

```text
news_plus_market
dart_plus_market
news_plus_dart_plus_market
```

명칭은 각 Agent의 기준 도메인을 유지하되, 통합 manifest에서는 어느 파일이 어떤 도메인의 산출물인지 명확히 적는다.

## 하지 말아야 할 것

1. 팀별 Agent 내부 로직을 통합 오케스트레이터에서 재구현하지 않는다.
2. Layer 1.5를 공통 레이어로 새로 만들지 않는다.
3. output을 `src/Agent_Team/...` 내부에 저장하지 않는다.
4. News, DART, YFinance Agent가 최종 투자판단을 내리게 하지 않는다.
5. API key 값을 로그에 남기지 않는다.
6. 기존 팀별 output을 임의로 삭제하지 않는다.
7. 팀별 schema를 무시하고 단일 임의 JSON으로 뭉치지 않는다.

## Codex 작업 지침

이 문서를 보고 Codex가 다음 작업을 수행할 때는 아래 순서를 따른다.

1. 먼저 현재 파일 구조와 README를 읽는다.
2. 각 팀별 CLI가 현재 실제로 동작하는지 독립 실행으로 확인한다.
3. output 경로가 run_key 기준으로 맞지 않는 팀이 있으면 그 팀부터 경로 정리를 한다.
4. 그 다음 통합 runner를 만든다.
5. 통합 runner는 처음에는 subprocess 기반으로 단순 구현한다.
6. 통합 runner가 안정화된 뒤에만 함수 호출 기반 리팩터링을 검토한다.
7. end-to-end 테스트는 SK바이오팜 기본 config로 먼저 수행한다.
8. 성공 후 삼성전자 등 다른 기업 config로 재현성을 확인한다.

## 1차 구현 목표

1차 구현에서 필요한 최소 기능:

```text
입력:
  --config configs/company_input.json
  --env-file configs/.env
  --use-llm

출력:
  Output_total/Financial/<run_key>/...
  Output_total/News/<run_key>/...
  Output_total/Y_Finance/<run_key>/...
  Output_total/runs/<run_key>/run_manifest.json
  Output_total/runs/<run_key>/run_status.json

동작:
  YFinance Layer 1 실행
  Financial Layer 1 실행
  News 전체 또는 Layer 1 실행
  각 팀 Layer 2 실행
  각 팀 SY 검증 확인
  통합 manifest 생성
```

1차 구현의 목적은 `Agent_Team` 하위 3개 팀의 Layer 1부터 Layer 2까지를 한 번에 안정적으로 실행하는 것이다.
