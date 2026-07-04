# SY Agent

SY Agent는 Financial Analyst Agent가 만든 1차 재무 분석 output을 검증하는 LangGraph 기반 validation agent입니다. 현재 역할은 claim 검증과 DART 원천값 대조입니다.

역할은 단순합니다.

```text
Financial Analyst Agent output에 대해 "왜 이런 의견을 냈어?"라고 묻는다.
DART 원천 파일과 리포트의 핵심 숫자를 LLM 평가 context로 제공한다.
근거 있게 답하면 keep.
근거는 있지만 표현/숫자 보강이 필요하면 revise.
답하지 못하면 hallucination_candidate.
입력 근거와 충돌하거나 제외가 필요하면 remove.
```

## Fixed Flow

```text
Input Specialist Output
Claim Extraction Node
DART Source Context Node
SY Question 1 Node
Specialist Answer 1 Node
SY Question 2 Node
Specialist Answer 2 Node
SY LLM Evaluation Node
Revision Brief Node (if any claim is not keep)
Specialist Report Rewrite Node (if any claim is not keep)
Specialist Final Rewrite Node
Verified Handoff Output
```

LLM은 질문/답변/확인 문장과 최종 판단을 생성하고, LangGraph는 순서와 재작성 분기를 통제합니다. 최종 `keep/revise/hallucination_candidate/remove` 판단은 LLM evaluation이 확정합니다. DART 파일은 deterministic source audit이 아니라 LLM 평가 context로만 사용합니다. 그래프 완료 후 runner가 검증 결과를 원본 Financial report에 반영해 Financial report 형식 JSON으로 다시 출력할 수 있습니다.

SY evaluation 결과가 모두 `keep`이 아니면 LangGraph가 `Revision Brief Node`에서 SY 질문/답변과 평가 이유를 재작성 브리프로 정리한 뒤, `Specialist Report Rewrite Node`에서 기존 report에 자연스럽게 반영합니다. 재작성된 report는 다시 SY 검증하지 않고 바로 최종 산출물로 저장합니다.

`SY Question 2 Node`에서는 SY Agent가 필요하다고 판단한 추가 질문을 같은 노드 안에 자율적으로 포함할 수 있습니다. 추가 질문은 별도 반복 노드를 만들지 않고, 입력 데이터와 evidence 검증에 필요한 범위로만 생성합니다.

## Files

- `Agent.md`: SY Agent 역할과 고정 LangGraph 흐름
- `output_schema.json`: 단순 검증 결과 schema
- `langgraph_flow.py`: SY Agent LangGraph runner
- `run_validation.py`: legacy wrapper. 현재 기본 경로는 LLM-only LangGraph 검증
- `run_pipeline.py`: Financial report 생성, SY 검증, 검증 반영 Financial report 재출력까지 연결
- `run_regression_tests.py`: 최소 regression test

## Run SY Agent

End-to-end Financial Analyst + SY 실행은 Financial Analyst manifest의 `target_entity.company_name`과 `target_entity.as_of_date`를 기준으로 output 폴더를 자동 생성합니다.

```bash
cd /home/agent2/Financial_Agent_Final
PYTHONPATH=src python -m Agent_Team.Financial_Agent.SY_Agent.run_pipeline \
  --financial-manifest "/home/agent2/Financial_Agent_Final/src/Agent_Team/Financial_Agent/input_manifest.skbiopharm_20251031.json"
```

기본 저장 위치:

```text
/home/agent2/Financial_Agent_Final/Output_total/Financial/<company>_<YYYYMMDD>/agent_pipeline
```

아래 명령은 SY Agent만 단독 실행할 때 사용합니다.

```bash
cd /home/agent2/Financial_Agent_Final
PYTHONPATH=src python -m Agent_Team.Financial_Agent.SY_Agent.langgraph_flow \
  --input "/home/agent2/Financial_Agent_Final/Output_total/Financial/SK바이오팜_20251031/agent_pipeline/pipeline_financial_analyst_report_output.json" \
  --output "/home/agent2/Financial_Agent_Final/Output_total/Financial/SK바이오팜_20251031/agent_pipeline/pipeline_sy_validation_output.json" \
  --trace-output "/home/agent2/Financial_Agent_Final/Output_total/Financial/SK바이오팜_20251031/agent_pipeline/pipeline_sy_validation_trace.json" \
  --verified-report-output "/home/agent2/Financial_Agent_Final/Output_total/Financial/SK바이오팜_20251031/agent_pipeline/pipeline_verified_financial_report_output.json" \
  --dart-main "/home/agent2/Financial_Agent_Final/Output_total/Financial/SK바이오팜_20251031/dart_main.json" \
  --dart-master "/home/agent2/Financial_Agent_Final/Output_total/Financial/SK바이오팜_20251031/dart_master.json"
```

LLM API는 Financial SY 검증에 필수입니다:

```bash
cd /home/agent2/Financial_Agent_Final
PYTHONPATH=src python -m Agent_Team.Financial_Agent.SY_Agent.langgraph_flow \
  --input "/home/agent2/Financial_Agent_Final/Output_total/Financial/SK바이오팜_20251031/agent_pipeline/pipeline_financial_analyst_report_output.json" \
  --output "/home/agent2/Financial_Agent_Final/Output_total/Financial/SK바이오팜_20251031/agent_pipeline/pipeline_sy_validation_llm_output.json" \
  --trace-output "/home/agent2/Financial_Agent_Final/Output_total/Financial/SK바이오팜_20251031/agent_pipeline/pipeline_sy_validation_llm_trace.json" \
  --verified-report-output "/home/agent2/Financial_Agent_Final/Output_total/Financial/SK바이오팜_20251031/agent_pipeline/pipeline_verified_financial_report_output.json" \
  --dart-main "/home/agent2/Financial_Agent_Final/Output_total/Financial/SK바이오팜_20251031/dart_main.json" \
  --dart-master "/home/agent2/Financial_Agent_Final/Output_total/Financial/SK바이오팜_20251031/dart_master.json" \
  --use-llm \
  --env-file "/home/agent2/Financial_Agent_Final/configs/.env" \
  --llm-provider openai \
  --llm-model gpt-5.4-mini
```

## Output Meaning

- `llm_evaluation_checks`: LLM-only 평가 실행 상태
- `source_context`: `dart_main.json`, `dart_master.json`에서 LLM 평가용으로 압축한 source context
- `dialogue_trace`: LangGraph 고정 노드 흐름에서 생성된 대화
- `claim_validations`: claim별 질문/답변/keep-revise-hallucination_candidate-remove 판단
- `verified_output`: 제외 대상 claim을 제외하고, 보강 대상 claim을 표시한 최종 검증 결과
- `--verified-report-output`: SY 검증 결과를 반영해 재출력한 Financial report JSON
