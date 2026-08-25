# Strategy Agent

Strategy Agent는 대상 기업의 검증된 Financial, News, YFinance 보고서와 구조화된 비교기업 자료를 바탕으로 기준일 시점의 판단과 투자자 대응을 생성한다. 기본 v4 경로는 LLM이 사용할 근거를 직접 선택하고 보고서를 작성하며, 한 번 호출한다.

## 입력

- `Financial/{target_run_key}/final_report.json`
- `News/{target_run_key}/final_report.json`
- `Y_Finance/{target_run_key}/final_report.json`
- `Competitor/{target_run_key}/peer_comparison_dataset.json`
- `Competitor/{target_run_key}/peer_comparison_report.json`

비교 데이터셋은 동일 지표·단위·날짜·기간 기준이 확인된 값을 제공하고, 비교 분석 보고서는 대상기업과 비교기업에 동일 절차를 적용한 하위 에이전트 결과를 종합해 두 기업의 상대적 위치를 설명한다.

## v4 추론 계약

입력 구성기는 하위 에이전트의 주요 분석, 교차 자료 판단과 사실 기반 근거 카드를 하나의 `strategy_context_package_v4`로 전달한다. 날짜·기간·단위·비교 대상·자료의 적용 범위는 유지하지만, 카드의 투자 방향, 중요도와 보고서 배치는 미리 결정하지 않는다.

- Financial: 일반 기업 4~6개, 다사업 예외 최대 7개
- News: 기본 6개, 일반 최대 8개, 중요 반대 사건 overflow 최대 10개
- Market: 최대 3개
- Valuation: 최대 2개
- Peer: 최대 6개
- reader limitation: 최대 8개. machine blocker는 별도 보존

기존 card builder의 크기 제한은 입력량 관리를 위해 유지한다. v4 문맥 패키지는 여기서 `allowed_sections`, `decision_use`, `eligibility`, 뉴스의 파생 event materiality 같은 판단 정책 필드를 제거한다. 하위 에이전트의 `main_view`와 기간별 분석은 별도 handoff로 함께 제공한다.

Strategy LLM은 한 호출에서 다음을 반환한다.

- 독자가 그대로 읽을 수 있는 `strategy_brief`
- 기존 편입자와 신규 접근자를 구분한 현재 대응
- 결론에 필요한 논점만 선택한 `rationale`
- 실제 사용한 근거와 선택 이유를 기록한 `basis_cards`
- 현재 입력에서 확인되는 주요 위험과 결론을 제약하는 실질적인 판단 한계

모든 카드를 평가하거나 Financial, News, Market과 비교기업 근거를 의무적으로 하나씩 선택하지 않는다. Strategy LLM이 결론에 필요한 근거만 `primary`, `counter`, `monitoring`, `context` 역할로 선택한다. 원자료 ID와 파일 경로는 LLM 문맥에서 제외하고 외부 provenance map에 보존한다.

## 실행 계약

운영 경로에는 별도의 입력·판단 게이트를 두지 않는다. 기준일, 재무기간, 단위와 비교 기준은 문맥 패키지를 만드는 과정에서 확정하고, 사용할 수 있는 근거 카드는 구조화 출력 선택지로 제공한다. 언어모형 응답은 지정된 JSON 형식으로 해석할 수 없을 때만 실행 오류로 처리한다. 판단 방향, 근거의 중요도와 문체는 Strategy Agent가 결정한다.

`validate_compact_strategy_packet_v2`와 버전별 `validate_strategy_decision` 함수는 회귀시험과 실험 평가에서 직접 사용할 수 있지만 운영 결과를 폐기하거나 재생성하는 데 사용하지 않는다.

## 실행

```bash
PYTHONPATH=src python -m Agent_Team.Strategy_Agent.cli \
  --target-company-name SK바이오팜 \
  --target-run-key SK바이오팜_20251031 \
  --target-financial Output_total/Financial/SK바이오팜_20251031/final_report.json \
  --target-news Output_total/News/SK바이오팜_20251031/final_report.json \
  --target-yfinance Output_total/Y_Finance/SK바이오팜_20251031/final_report.json \
  --peer-comparison Output_total/Competitor/SK바이오팜_20251031/peer_comparison_dataset.json \
  --peer-analysis Output_total/Competitor/SK바이오팜_20251031/peer_comparison_report.json \
  --output-dir Output_total/Strategy/SK바이오팜_20251031 \
  --packet-version v4
```

## 산출물

```text
strategy_input_bundle.json
strategy_compact_packet_v2.json
strategy_packet_provenance_v2.json
strategy_context_package_v4.json
strategy_context_telemetry_v4.json
strategy_decision_output_v4.json
strategy_decision_cache_v4.json
strategy_report.json
strategy_report.md
```

v4 성공 후 같은 output directory의 이전 판단 산출물은 제거된다. `--packet-version v3`, `v2`, `v1`은 비교 및 호환 경로이며 서로 다른 버전의 판단 파일을 하나의 downstream 입력으로 혼합하지 않는다.

## 이전 의견 등급 계약 평가

```bash
PYTHONPATH=src python -m Agent_Team.Strategy_Agent.evaluate_recommendation_bias \
  --llm-model gpt-5.4-mini \
  --env-file configs/.env
```

평가 호출은 `LLM_RUN_ROLE=evaluation`으로 기록되며 정상 15-call 보고서 파이프라인 집계에서 제외된다.
