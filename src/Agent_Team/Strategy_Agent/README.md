# Strategy Agent

Strategy Agent는 대상 기업의 검증된 Financial, News, YFinance 보고서와 구조화된 비교기업 자료를 바탕으로 기준일 시점의 판단과 투자자 대응을 생성한다. 기본 v5 경로는 LLM이 판단 근거를 직접 선택하고, 기존 편입자와 신규 자금의 현재 대응을 구분해 한 번의 호출로 작성한다.

## 입력

- `Financial/{target_run_key}/final_report.json`
- `News/{target_run_key}/final_report.json`
- `Y_Finance/{target_run_key}/final_report.json`
- `Competitor/{target_run_key}/peer_comparison_dataset.json`
- `Competitor/{target_run_key}/peer_comparison_report.json`

비교 데이터셋은 동일 지표·단위·날짜·기간 기준이 확인된 값을 제공하고, 비교 분석 보고서는 대상기업과 비교기업에 동일 절차를 적용한 하위 에이전트 결과를 종합해 두 기업의 상대적 위치를 설명한다.

## v5 추론 계약

입력 구성기는 하위 에이전트의 주요 분석, 교차 자료 판단과 사실 기반 근거 카드를 하나의 `strategy_context_package_v5`로 전달한다. 날짜·기간·단위·비교 대상·자료의 적용 범위는 유지하지만, 카드의 투자 방향과 중요도는 미리 결정하지 않는다.

- Financial: 일반 기업 4~6개, 다사업 예외 최대 7개
- News: 기본 6개, 일반 최대 8개, 중요 반대 사건 overflow 최대 10개
- Market: 최대 3개
- Valuation: 최대 2개
- Peer: 최대 6개
- reader limitation: 최대 8개. machine blocker는 별도 보존

기존 card builder의 크기 제한은 입력량 관리를 위해 유지한다. v5 문맥 패키지는 `allowed_sections`, `decision_use`, `eligibility`, 뉴스의 파생 event materiality 같은 판단 정책 필드를 제거한다. 하위 에이전트의 `main_view`와 기간별 분석은 별도 handoff로 함께 제공한다.

Strategy LLM은 한 호출에서 다음을 반환한다.

- 독자가 그대로 읽을 수 있는 `strategy_brief`
- 기존 편입자와 신규 자금을 구분한 현재 대응
- 결론과 반대 논리, 판단 한계를 담은 `report_insights`
- 판단에 직접 사용한 근거와 역할을 기록한 `decision_basis_cards`
- 보고서 문장에서 실제 참조된 근거로부터 시스템이 구성하는 `report_context_cards`
- 대상기업 판단에 실제로 사용한 구조화 비교 지표만 기록한 `target_peer_context`
- 현재 입력에서 확인되는 주요 위험, 각 위험의 기업별 구체적 제목과 결론을 제약하는 실질적인 판단 한계

모든 카드를 평가하거나 Financial, News, Market과 비교기업 근거를 의무적으로 하나씩 선택하지 않는다. Strategy LLM은 결론에 필요한 근거만 `supports`, `opposes`, `limits` 관계로 선택한다. 원자료 ID와 파일 경로는 LLM 문맥에서 제외하고 외부 provenance map에 보존한다.

판단 근거는 최대 6개이며 이 수는 채워야 할 할당량이 아니다. Strategy 응답은 `decision_basis_cards`를 먼저 확정하고, 판단 요약·논점·위험은 허용된 근거만 참조한다. 보고서 보충 문맥은 모델에게 별도 할당량으로 요구하지 않고 실제 본문 참조를 기준으로 최대 10개까지 구성한다. 이는 코드가 투자 의미를 정하는 규칙이 아니라 한 호출 안에서 근거 선택과 서술의 일관성을 유지하기 위한 출력 계약이다.

비교 에이전트의 종합 카드는 내부 비교 문맥으로만 사용한다. 최종 보고서에 비교 결과를 사용할 때는 Strategy가 동일 기준으로 비교 가능한 `peer.*` 카드 중 최대 2개를 `decision_basis_cards` 안에 선택하고, 각 카드의 `target_peer_context`에 최대 2개 지표와 대상기업 판단상 의미를 함께 작성한다. 비교 카드가 아닌 근거의 해당 필드는 `null`이다. 저장 단계에서는 이 중첩 정보를 downstream이 사용하기 쉬운 최상위 `target_peer_context` 배열로 옮기되 의미나 지표 선택은 변경하지 않는다.

## 실행 계약

운영 경로에는 자연어 판단을 채점하거나 방향을 바꾸는 입력·판단 게이트를 두지 않는다. 기준일, 재무기간, 단위와 비교 기준은 문맥 패키지를 만드는 과정에서 확정하고, 사용할 수 있는 근거 카드는 구조화 출력 선택지로 제공한다. 언어모형 응답은 지정된 JSON 형식으로 해석할 수 없거나, 선택하지 않은 카드를 본문·위험·비교 문맥에서 참조하는 등 구조화 계약의 참조 무결성이 깨진 경우에만 실행 오류로 처리한다. 판단 방향, 근거의 중요도와 문체는 Strategy Agent가 결정한다.

`validate_compact_strategy_packet_v2`와 버전별 `validate_strategy_decision` 함수는 회귀시험과 실험 평가에서도 사용한다. v5 운영 경로에서는 카드 존재 여부, 중복, 선택 카드 참조와 비교 지표의 동일 기준 사용 같은 구조적 무결성만 확인하며 자연어 의미를 규칙으로 판정하거나 응답을 재생성하지 않는다.

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
  --packet-version v5
```

## 산출물

```text
strategy_input_bundle.json
strategy_compact_packet_v2.json
strategy_packet_provenance_v2.json
strategy_context_package_v5.json
strategy_context_telemetry_v5.json
strategy_generation_context_v5.json
strategy_decision_output_v5.json
strategy_decision_profile_v5.json
strategy_decision_cache_v5.json
strategy_report.json
strategy_report.md
```

`decision_basis_card.json`은 과거 v1 계약에서 판단 근거를 Writer와 Visualization 단계에 전달하던 파일이다. v5에서는 `strategy_decision_output_v5.json`의 `decision_basis_cards`, 파생된 `report_context_cards`와 외부 provenance 파일이 그 역할을 나누어 맡으므로 이 파일을 생성하거나 downstream 입력으로 사용하지 않는다. v5 성공 후 같은 output directory의 이전 판단 산출물은 제거된다. 이전 버전은 비교 및 호환 경로이며 서로 다른 버전의 판단 파일을 하나의 downstream 입력으로 혼합하지 않는다.

## 이전 의견 등급 계약 평가

```bash
PYTHONPATH=src python -m Agent_Team.Strategy_Agent.evaluate_recommendation_bias \
  --llm-model gpt-5.4 \
  --env-file configs/.env
```

평가 호출은 `LLM_RUN_ROLE=evaluation`으로 기록되며 정상 15-call 보고서 파이프라인 집계에서 제외된다.
