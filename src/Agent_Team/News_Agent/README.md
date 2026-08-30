# News Agent

통합 레포 기준 News 하위 에이전트입니다. News Agent는 `buy/sell/hold`를 판단하지 않고, 상위 레이어가 사용할 뉴스 분석 handoff를 생성합니다.

## 위치

```text
src/Agent_Team/News_Agent
```

## 전체 실행

`--phase`를 생략하면 전체가 실행됩니다.

```text
collect -> export -> llm -> analysis
```

```bash
cd /path/to/Financial_Agent

PYTHONPATH=src python -m Agent_Team.News_Agent.cli \
  --collect-date 2025-10-31 \
  --company-id 00878696 \
  --company-name SK바이오팜 \
  --ticker 326030.KS \
  --corp-code 00878696 \
  --granularity week
```

설치 후에는 아래 스크립트도 사용할 수 있습니다.

```bash
news-workflow ...
news-analysis-agent ...
```

## 단계별 실행

```bash
PYTHONPATH=src python -m Agent_Team.News_Agent.cli --phase collect ...
PYTHONPATH=src python -m Agent_Team.News_Agent.cli --phase export ...
PYTHONPATH=src python -m Agent_Team.News_Agent.cli --phase llm ...
PYTHONPATH=src python -m Agent_Team.News_Agent.cli --phase analysis ...
```

터미널에는 `tqdm` 기반 진행 상황이 표시됩니다.

기준일 직전 90일을 7일 단위로 조회한 뒤 URL 중복을 제거하고, 같은 달력 주간에 보도된 유사 기사를 하나의 사건으로 묶습니다. 사건과 기업보고서의 문장 표현 유사도를 기준으로 주별 15건을 후보로 남기고 교차 인코더로 재정렬하여 주별 최대 5건을 확정합니다. 이 사건들로 주간 요약을 한 번에 생성하고, 주별 확정 사건의 합집합에서 전체 상위 20건을 다시 선정합니다. 여러 날짜의 기사가 한 사건으로 묶이면 날짜별로 기업보고서와 가장 관련성이 높은 제목 한 건을 시간순 진행 내역으로 함께 제공합니다. 언어모델에는 제목과 스니펫을 제공하며 기사 전체 본문은 수집하지 않습니다.

News Agent에는 `3개월 주간 요약`과 `기업 관련 뉴스 상위 20건`을 이 순서로 제공합니다. 주간 요약은 기간 흐름을 파악하는 문맥으로 사용하고, 분석 문장의 직접 근거는 상위 20건의 `NEWS_RAW` 식별자로 제한합니다. Financial Agent와 YFinance Agent에는 주간 요약만 보조자료로 전달하며, 뉴스 에이전트의 주장과 URL은 전달하지 않습니다.

## Output 경로

통합 후 새 output은 반드시 아래에 생성합니다.

```text
Output_total/News/{run_key}/output
```

예시:

```text
Output_total/News/SK바이오팜_20251031/output
```

News Agent output:

```text
news_agent_input_payload.json
news_agent_llm_request.json
news_agent_handoff.json
news_agent_evidence_map.json
```

상위 레이어가 기본으로 읽을 파일:

```text
Output_total/News/{run_key}/final_report.json
```

## Cross-Domain 입력

News Agent handoff 단계는 Financial/YFinance 산출물을 함께 사용합니다. 통합 실행에서는 명시 경로를 권장합니다.

```bash
PYTHONPATH=src python -m Agent_Team.News_Agent.cli \
  --phase analysis \
  --collect-date 2025-10-31 \
  --company-id 00878696 \
  --company-name SK바이오팜 \
  --ticker 326030.KS \
  --corp-code 00878696 \
  --context-export-dir Output_total/News/SK바이오팜_20251031/context_exports \
  --dart-lightweight Output_total/Financial/SK바이오팜_20251031/dart_lightweight.json \
  --market-summary Output_total/Y_Finance/SK바이오팜_20251031/market_summary.json \
  --analysis-output-dir Output_total/News/SK바이오팜_20251031/output
```

## 최종 산출물 계약

`news_agent_handoff.json`:

- `usage`: News Agent LLM 토큰 사용량
- `output.analysis_blocks.news_only`
- `output.secondary_context_assessment`
- `output.evidence_map_path`

Financial/Market 보조 문맥은 `framing_and_limitation_only`로 유지되며 News 사건의 직접 증거나 인과 근거로 사용되지 않습니다.
재무자료의 대상 기간이 뉴스 발생일보다 앞서는 경우에는 해당 자료를 후행 사건의 효과를 확인하거나 반박하는 근거로 사용하지 않고, 사건 발생 전의 재무 상태를 설명하는 문맥으로만 사용합니다.

News Agent는 최종 투자 판단을 생성하지 않습니다.
