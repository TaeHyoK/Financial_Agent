# News Agent

통합 레포 기준 News 하위 에이전트입니다. News Agent는 `buy/sell/hold`를 판단하지 않고, 상위 레이어가 사용할 수 있는 뉴스 분석 handoff와 SY 검증 결과를 생성합니다.

## 위치

```text
/home/agent2/Financial_Agent_Final/src/Agent_Team/News_Agent
```

## 전체 실행

`--phase`를 생략하면 전체가 실행됩니다.

```text
collect -> export -> llm -> analysis -> sy
```

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

설치 후에는 아래 스크립트도 사용할 수 있습니다.

```bash
news-workflow ...
news-analysis-agent ...
news-sy-agent ...
```

## 단계별 실행

```bash
PYTHONPATH=src python -m Agent_Team.News_Agent.cli --phase collect ...
PYTHONPATH=src python -m Agent_Team.News_Agent.cli --phase export ...
PYTHONPATH=src python -m Agent_Team.News_Agent.cli --phase llm ...
PYTHONPATH=src python -m Agent_Team.News_Agent.cli --phase analysis ...
PYTHONPATH=src python -m Agent_Team.News_Agent.cli --phase sy ...
```

터미널에는 `tqdm` 기반 진행 상황이 표시됩니다.

## Output 경로

통합 후 새 output은 반드시 아래에 생성합니다.

```text
/home/agent2/Financial_Agent_Final/Output_total/News/{run_key}/output
```

예시:

```text
/home/agent2/Financial_Agent_Final/Output_total/News/SK바이오팜_20251031/output
```

News Agent output:

```text
news_agent_input_payload.json
news_agent_llm_request.json
news_agent_handoff.json
news_agent_evidence_map.json
```

News SY Agent output:

```text
sy_agent/sy_claim_validations.json
sy_agent/sy_question_answer_log.json
sy_agent/sy_audit_trace.json
sy_agent/critic_queue.json
```

News SY는 LangGraph 안에서 `Q1/A1 -> Q2/A2 -> SY evaluation`을 수행합니다. 모든 claim이 `keep`이 아니면 `Revision Brief`가 SY 질문/답변과 평가 이유를 재작성 지시로 정리하고, `news_agent_handoff.output`을 한 번 자연스럽게 다시 작성합니다. 재작성된 output은 다시 SY 검증하지 않고 바로 최종 산출물로 저장합니다.

상위 레이어가 기본으로 읽을 파일:

```text
Output_total/News/{run_key}/output/news_agent_handoff.json
Output_total/News/{run_key}/output/sy_agent/sy_claim_validations.json
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
  --context-export-dir /home/agent2/Financial_Agent_Final/Output_total/News/SK바이오팜_20251031/context_exports \
  --dart-lightweight /home/agent2/Financial_Agent_Final/Output_total/Financial/SK바이오팜_20251031/dart_lightweight.json \
  --market-summary /home/agent2/Financial_Agent_Final/Output_total/Y_Finance/SK바이오팜_20251031/market_summary.json \
  --analysis-output-dir /home/agent2/Financial_Agent_Final/Output_total/News/SK바이오팜_20251031/output
```

## 최종 산출물 계약

`news_agent_handoff.json`:

- `usage`: News Agent LLM 토큰 사용량
- `output.analysis_blocks.news_only`
- `output.analysis_blocks.news_plus_financial`
- `output.analysis_blocks.news_plus_market`
- `output.analysis_blocks.news_plus_financial_plus_market`
- `output.evidence_map_path`

`sy_claim_validations.json`:

- `summary`
- `claim_validations`
- `llm_usage`

판정값:

```text
supported -> keep
weakly_supported -> revise
unsupported -> hallucination_candidate
contradicted -> remove
```

News/SY Agent는 최종 투자 판단을 생성하지 않습니다.
