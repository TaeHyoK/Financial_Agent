# Strategy Agent

Strategy Agent는 대상 기업의 검증된 Financial, News, YFinance 보고서와 구조화된 selected-peer dataset으로 6~12개월 Buy/Hold/Sell 판단을 생성한다. 정상 v2 경로는 Content Planner 없이 LLM을 한 번만 호출한다.

## 입력

- `Financial/{target_run_key}/final_report.json`
- `News/{target_run_key}/final_report.json`
- `Y_Finance/{target_run_key}/final_report.json`
- `Competitor/{target_run_key}/peer_comparison_dataset.json`

독립적인 경쟁사 서술 보고서는 받지 않는다. peer dataset은 동일 metric, 단위, 날짜와 기간 기준이 확인된 pair를 제공하며 기업명과 비교기업 수를 동적으로 처리한다.

## v2 추론 계약

결정론적 packet builder가 upstream 전체 보고서를 다음 범위의 self-contained card로 압축한다.

- Financial: 일반 기업 4~6개, 다사업 예외 최대 7개
- News: 기본 6개, 일반 최대 8개, 중요 반대 사건 overflow 최대 10개
- Market: 최대 3개
- Valuation: 최대 2개
- Peer: 최대 6개
- reader limitation: 최대 8개. machine blocker는 별도 보존

각 card에는 semantic `card_key`, `evidence_family`, `observation_basis`, `comparison_scope`, primary observation, 날짜·기간·단위, evidence 역할, eligibility, 허용 section과 limitation이 포함된다. 시장지수는 `market_benchmark`, 비교기업은 `selected_peer`로 구분한다. `secondary_context`는 `usage=framing_only`이며 독립 근거나 data coverage 충족 항목으로 사용할 수 없다.

Strategy LLM은 한 호출에서 다음을 반환한다.

- 모든 card에 대한 `evidence_assessments`
- Buy/Hold/Sell, 투자기간과 data coverage
- 현재 가격, forward support, valuation counterweight와 불확실성을 카드에 연결한 `recommendation_bridge`
- comparable metric만 사용하는 `peer_findings`
- valuation, market, financial, news를 포괄하는 `decision_risk_factors`
- financial link가 없는 News를 decision factor에서 분리한 event materiality

중복 필드인 assessment direction, positive/negative factor와 section routing은 typed assessment에서 결정론적으로 도출한다. 같은 `evidence_family`는 독립 factor로 중복 집계하지 않는다. 별도의 중복 자유문장 보고서는 LLM이 생성하지 않으며, `strategy_report.json`과 Markdown은 typed 결과를 구조적으로 투영한다. raw evidence ID와 원천 파일 경로는 LLM packet에 넣지 않고 외부 provenance map에 보존한다.

## 검증

Gate A는 다음을 검사한다.

- card schema와 budget
- selected-date cutoff
- 재무 기간·scope와 제품 매출 reconciliation
- peer metric 단위·기간·날짜 comparability
- semantic card와 provenance content hash의 완전한 연결

Gate B는 다음을 검사한다.

- 모든 card가 정확히 한 번 평가됐는지 여부
- card의 `allowed_sections`, evidence role과 eligibility 준수
- factor와 assessment investment effect 일치
- peer finding의 metric, 비교 basis와 수치상 우열 방향
- context-only News와 비교 불가능한 card의 의사결정 근거 사용 차단
- point-in-time, period comparison과 event materiality의 의사결정 사용 범위
- risk basis card와 reader summary의 의미 일치
- opaque raw ID 누출 여부
- 독자용 문장에 JSON field명이나 semantic card key가 노출되는지 여부

Gate B의 hard fail은 출처·card reference·비교 가능성·typed field 내부 일관성 같은 무결성 위반에만 적용한다. 사용 가능한 가격/valuation/forward card를 반드시 채택해야 한다는 규칙, Buy/Sell의 독립 evidence family 2개 규칙, evidence sufficiency 권고와 파생 family 목록 차이는 `advisories`에 기록하되 분석 실행을 중단하지 않는다.

Gate B는 card key, comparison scope, peer company, 관측 기준처럼 구조화된 필드로 검증한다. 독자용 문장의 내부 JSON field명과 semantic card key 노출은 무결성 위반으로 차단하지만, `동종`, `업종`, 특정 회사명 같은 일반 업무 문구 자체는 실패 조건으로 사용하지 않는다. 무결성 검증 실패 시에는 해당 산출물을 성공 cache로 인정하지 않으며 상위 실행기가 설정한 semantic attempt 범위에서 새 응답을 생성할 수 있다.

## 실행

```bash
PYTHONPATH=src python -m Agent_Team.Strategy_Agent.cli \
  --target-company-name SK바이오팜 \
  --target-run-key SK바이오팜_20251031 \
  --target-financial Output_total/Financial/SK바이오팜_20251031/final_report.json \
  --target-news Output_total/News/SK바이오팜_20251031/final_report.json \
  --target-yfinance Output_total/Y_Finance/SK바이오팜_20251031/final_report.json \
  --peer-comparison Output_total/Competitor/SK바이오팜_20251031/peer_comparison_dataset.json \
  --output-dir Output_total/Strategy/SK바이오팜_20251031 \
  --packet-version v2
```

## 산출물

```text
strategy_input_bundle.json
strategy_compact_packet_v2.json
strategy_packet_provenance_v2.json
strategy_packet_telemetry_v2.json
strategy_decision_output_v2.json
strategy_decision_cache_v2.json
strategy_semantic_validation_v2.json
strategy_report.json
strategy_report.md
```

v2 성공 후 같은 output directory의 v1 planner, decision packet과 decision-basis artifact는 제거된다. `--packet-version v1`은 제한적인 rollback 실행에만 사용하며 v1/v2 파일을 하나의 downstream 입력으로 혼합하지 않는다.

## Hold 편향 평가

```bash
PYTHONPATH=src python -m Agent_Team.Strategy_Agent.evaluate_recommendation_bias \
  --llm-model gpt-5.4-mini \
  --env-file configs/.env
```

평가 호출은 `LLM_RUN_ROLE=evaluation`으로 기록되며 정상 14-call 보고서 파이프라인 집계에서 제외된다.
