> 기본 실행은 `schema_revision=12m_v3`입니다. 관측 자료·하위 해석·적용 범위를 구분해 제공하며, 실적·전망·가격 분석과 대안 해석을 먼저 작성한 뒤 12개월 Buy/Hold/Sell 의견을 정리합니다. 근거의 역할은 분류 라벨 대신 `investment_implication`으로 서술합니다. 실제 인용에 고정 개수 상한을 두지 않으며, 핵심 표에 쓰지 않은 인용도 문맥 근거로 보존합니다. 이 계약으로 바뀐 과정은 [변경 기록](../../../docs/strategy_analysis_first.md)에 있습니다. 아래 설명이 현재 계약이며, 앞선 계약 코드는 제거되었습니다.

# Strategy Agent

Strategy Agent는 Financial, News, YFinance 분석과 비교기업 자료를 바탕으로 기준일 시점의 12개월 투자 의견을 한 번의 LLM 호출로 작성한다. 보유자와 신규 자금을 나누어 대응하지 않는다.

## 입력

- `{company}/Financial/{YYYYMMDD}/final_report.json`
- `{company}/News/{YYYYMMDD}/final_report.json`
- `{company}/Y_Finance/{YYYYMMDD}/final_report.json`
- `{company}/Competitor/{YYYYMMDD}/peer_comparison_dataset.json`
- `{company}/Competitor/{YYYYMMDD}/peer_comparison_report.json`

비교 데이터셋은 동일 지표·단위·날짜·기간 기준이 확인된 값을 제공하고, 비교 분석 보고서는 대상기업과 비교기업에 동일 절차를 적용한 하위 에이전트 결과를 종합해 두 기업의 상대적 위치를 설명한다.

## 추론 계약

입력 구성기는 하위 에이전트의 주요 분석, 교차 자료 판단과 사실 기반 근거 카드를 하나의 `strategy_context_package`로 전달한다. 날짜·기간·단위·비교 대상·자료의 적용 범위는 유지하지만, 카드의 투자 방향과 중요도는 미리 결정하지 않는다.

카드 개수에는 고정 상한이 없다. `packet.py`의 `CARD_BUDGETS`는 영역별 카드 수가 예전 기준을 넘을 때 텔레메트리에 경고를 남기는 용도로만 쓰며, 개수 때문에 근거를 버리지 않는다. 문맥 패키지는 `allowed_sections`, `decision_use`, `eligibility`, 뉴스의 파생 event materiality 같은 판단 정책 필드를 제거한다. 하위 에이전트의 `main_view`와 기간별 분석은 별도 handoff로 함께 제공한다.

Strategy LLM은 한 호출에서 다음을 반환한다.

- 독자가 그대로 읽을 수 있는 `strategy_brief`
- `strategy_brief` 안의 12개월 `recommendation`(Buy/Hold/Sell)과 이를 뒷받침하는 `earnings_review`·`outlook`·`price_assessment`·`counterview`·`thesis`·`decision_rationale`
- 결론과 반대 논리, 판단 한계를 담은 `report_insights`
- 판단에 직접 사용한 근거와 그 의미(`importance`·`investment_implication`)를 기록한 `decision_basis_cards`
- 보고서 문장에서 실제 참조된 근거로부터 시스템이 구성하는 `report_context_cards`
- 대상기업 판단에 실제로 사용한 구조화 비교 지표만 기록한 `target_peer_context`
- 현재 입력에서 확인되는 주요 위험, 각 위험의 기업별 구체적 제목과 결론을 제약하는 실질적인 판단 한계

모든 카드를 평가하거나 Financial, News, Market과 비교기업 근거를 의무적으로 하나씩 선택하지 않는다. Strategy LLM은 결론에 필요한 근거만 고르고, 각 근거의 무게와 판단상 의미를 `importance`와 `investment_implication`으로 서술한다. 원자료 ID와 파일 경로는 LLM 문맥에서 제외하고 외부 provenance map에 보존한다.

판단 근거는 최소 1개이고 고정 상한은 없다. 프롬프트는 통상 1~3개를 권하지만 채워야 할 할당량은 아니다. Strategy 응답은 `decision_basis_cards`를 먼저 확정하고, 판단 요약·논점·위험은 허용된 근거만 참조한다. 보고서 보충 문맥은 모델에게 별도 할당량으로 요구하지 않고 실제 본문 참조를 기준으로 구성한다. 이는 코드가 투자 의미를 정하는 규칙이 아니라 한 호출 안에서 근거 선택과 서술의 일관성을 유지하기 위한 출력 계약이다.

비교 에이전트의 종합 카드는 내부 비교 문맥으로만 사용한다. 최종 보고서에 비교 결과를 사용할 때는 Strategy가 동일 기준으로 비교 가능한 `peer.*` 카드를 필요한 만큼 `decision_basis_cards` 안에 선택하고, 각 카드의 `target_peer_context`에 판단에 쓴 지표와 대상기업 판단상 의미를 함께 작성한다. 비교 카드가 아닌 근거의 해당 필드는 `null`이다. 저장 단계에서는 이 중첩 정보를 downstream이 사용하기 쉬운 최상위 `target_peer_context` 배열로 옮기되 의미나 지표 선택은 변경하지 않는다.

## 실행 계약

운영 경로에는 자연어 판단을 채점하거나 방향을 바꾸는 입력·판단 게이트를 두지 않는다. 기준일, 재무기간, 단위와 비교 기준은 문맥 패키지를 만드는 과정에서 확정하고, 사용할 수 있는 근거 카드는 구조화 출력 선택지로 제공한다. 언어모형 응답은 지정된 JSON 형식으로 해석할 수 없거나, 선택하지 않은 카드를 본문·위험·비교 문맥에서 참조하는 등 구조화 계약의 참조 무결성이 깨진 경우에만 실행 오류로 처리한다. 판단 방향, 근거의 중요도와 문체는 Strategy Agent가 결정한다.

`validate_compact_strategy_packet`와 `validate_strategy_decision`는 회귀시험과 실험 평가에서도 사용한다. 운영 경로에서는 카드 존재 여부, 중복, 선택 카드 참조와 비교 지표의 동일 기준 사용 같은 구조적 무결성만 확인하며 자연어 의미를 규칙으로 판정하거나 응답을 재생성하지 않는다.

## 실행

```bash
PYTHONPATH=src python -m Agent_Team.Strategy_Agent.cli \
  --target-company-name SK바이오팜 \
  --target-run-key SK바이오팜_20251031 \
  --target-financial Output_total/SK바이오팜/Financial/20251031/final_report.json \
  --target-news Output_total/SK바이오팜/News/20251031/final_report.json \
  --target-yfinance Output_total/SK바이오팜/Y_Finance/20251031/final_report.json \
  --peer-comparison Output_total/SK바이오팜/Competitor/20251031/peer_comparison_dataset.json \
  --peer-analysis Output_total/SK바이오팜/Competitor/20251031/peer_comparison_report.json \
  --output-dir Output_total/SK바이오팜/Strategy/20251031
```

경로는 파이프라인과 같은 기업 우선 구조(`Output_total/<company>/<Agent>/<YYYYMMDD>`)를 따른다. `--output-dir`를 생략하면 `--output-root` 아래 같은 구조로 저장한다.

## 산출물

```text
strategy_input_bundle.json
strategy_compact_packet.json
strategy_packet_provenance.json
strategy_context_package.json
strategy_context_telemetry.json
strategy_generation_context.json
strategy_decision_output.json
strategy_decision_profile.json
strategy_decision_cache.json
strategy_report.json
strategy_report.md
```

판단 생성에 실패하면 원인을 `strategy_failure_report.json`에 남긴다.

`decision_basis_card.json`은 앞선 계약에서 판단 근거를 Writer와 Visualization 단계에 전달하던 파일이다. 현재는 `strategy_decision_output.json`의 `decision_basis_cards`, 파생된 `report_context_cards`와 외부 provenance 파일이 그 역할을 나누어 맡으므로 이 파일을 생성하거나 downstream 입력으로 사용하지 않는다. 판단 성공 후 같은 output directory의 이전 판단 산출물은 제거된다. 서로 다른 버전의 판단 파일을 하나의 downstream 입력으로 혼합하지 않는다.
