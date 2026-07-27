# Financial SY Agent

Financial Analyst output의 근거 사용 가능 여부를 검증하는 LangGraph 단계다.

## Flow

```text
Input Specialist Output
  -> Claim and Evidence Extraction
  -> DART Source Context
  -> Deterministic Evidence Checks
  -> Semantic Batch Evaluation
  -> Admissibility Ledger Output
```

검증 상태는 다음 세 값만 사용한다.

- `strong`: 문장 범위가 직접 근거로 지지됨
- `context_only`: 방향성 참고 또는 제한을 붙여 사용 가능
- `exclude`: 근거 누락, 기준일 위반 또는 충돌로 사용 불가

## Run

```bash
PYTHONPATH=src python src/Agent_Team/Financial_Agent/SY_Agent/langgraph_flow.py \
  --input Output_total/Financial/{run_key}/agent_pipeline/pipeline_financial_analyst_report_output.json \
  --output Output_total/Financial/{run_key}/agent_pipeline/pipeline_sy_validation_output.json \
  --dart-main Output_total/Financial/{run_key}/dart_main.json \
  --dart-master Output_total/Financial/{run_key}/dart_master.json \
  --verified-report-output Output_total/Financial/{run_key}/agent_pipeline/pipeline_verified_financial_report_output.json \
  --trace-output Output_total/Financial/{run_key}/agent_pipeline/pipeline_sy_validation_trace.json \
  --use-llm
```

LLM 입력은 claim과 해당 evidence만 포함하고 API 호출 전에 토큰 크기를 측정한다. 10만 토큰을 넘는 경우 evidence 경계를 보존해 batch를 나눈다.

원 Financial 보고서는 재작성하지 않는다. verified report는 `exclude` claim과 연결 evidence만 차단한다.
