# Writer Agent Workflow

## 목적

Writer Agent는 Strategy v4가 작성한 판단 방향, 기존 편입자·신규 접근자 대응, 선택 근거와 위험을 한국어 one-paper HTML 보고서로 편집한다. 정형화된 의견 등급은 입력받지 않는다. LLM은 사업·시장 맥락, 촉매와 데이터 한계 문장을 작성하고, 핵심 근거표와 위험표는 Strategy가 선택한 카드에서 구성한다.

별도 Review 또는 Repair LLM은 사용하지 않는다. 검증에 실패하면 raw 응답을 fingerprint cache에 보존해 코드 검증만 다시 수행할 수 있지만, 새로운 분석 문장을 규칙으로 생성하지 않는다.

## 입력

한 실행에는 Strategy 산출물 세 개가 필요하다.

```text
Output_total/Strategy/{run_key}/strategy_compact_packet_v2.json
Output_total/Strategy/{run_key}/strategy_packet_provenance_v2.json
Output_total/Strategy/{run_key}/strategy_decision_output_v4.json
```

`writer_handoff.py`는 Strategy가 실제 사용한 card의 합집합만 선별해 `writer_editorial_packet_v2`를 만든다.

```json
{
  "packet_version": "writer_editorial_packet_v2",
  "target": {},
  "decision": {},
  "recommendation_bridge": {},
  "required_card_keys_by_component": {},
  "cards": {
    "financial.same_period_trend": {
      "primary_observation": {},
      "reader_observation": {},
      "strategy_interpretation": "...",
      "investment_effect": "positive"
    }
  },
  "peer_findings": [],
  "risk_factors": [],
  "general_limitations": [],
  "required_limitations": []
}
```

raw evidence ID와 원천 경로는 LLM 입력에서 제외하고 `writer_packet_provenance_v2.json`에만 저장한다. `reader_observation`은 원시값을 없애지 않으면서 재무 금액을 억원, 비율을 %, valuation을 배 단위로 미리 표시한다.

## 처리 순서

```text
Strategy v4 산출물 3개 로드
  -> editorial card 합집합과 component routing 생성
  -> provenance content hash 검증
  -> LLM Writer 단일 호출
  -> Strategy 판단 요약과 근거·위험 표 구성
  -> 문장별 _claim_units와 card scope 연결
  -> HTML 렌더링
  -> 생성 상태와 파일 기록
```

## 6개 섹션

| 순서 | key | 목적 |
|---:|---|---|
| 1 | `investment_call_thesis` | 판단 방향, 투자기간, 기존 편입자·신규 접근자 대응 |
| 2 | `business_market_context` | 매출 구조와 시장 맥락 |
| 3 | `key_evidence_table` | 재무, 제품 매출, 시장, valuation, selected peer의 최대 8개 evidence axis |
| 4 | `catalysts_execution` | 확인된 event와 미확인 재무 기여의 구분 |
| 5 | `risk_monitoring_matrix` | Strategy typed risk와 확인 항목 |
| 6 | `data_limits` | 자료 시점, 기간, 범위, 비교와 인과 한계 |

각 text item은 component별 `card_keys`와 문장별 `_claim_units`를 hidden metadata로 가진다. 핵심 근거표의 `확인된 수치·사실`, `투자 해석`, `영향`과 risk의 독자용 요약은 Strategy typed field에서 생성한다. renderer는 metadata와 semantic card key를 HTML에 표시하지 않는다.

Writer handoff를 만들 때 Strategy의 recommendation bridge, assessment 해석, peer finding과 risk 문장을 사전 검사한다. 내부 JSON field명이나 semantic card key가 문장에 들어 있으면 LLM을 호출하기 전에 거부한다. `_claim_units.claim`은 나중에 독자용 문단으로 복원될 수 있으므로 card label 치환 대상에 포함하고, 실제 `card_keys` 배열은 hidden identifier로 유지한다.

## 보고서 생성과 평가의 분리

운영 실행에서는 작성 결과를 자연어 규칙으로 판정하거나 같은 요청을 다시 호출하지 않는다. 표, 수치 표시, 내부 식별자 제외와 고정 면책문구는 입력 구성 및 HTML 렌더러가 직접 처리한다. 응답이 지정된 자료 형식으로 해석되지 않거나 `report.html`을 생성하지 못한 경우에만 실행 오류로 처리한다.

`html_report_validator.py`는 회귀시험과 논문 실험에서 보고서의 근거 연결, 의미 보존과 구성 품질을 측정하기 위해 남겨두며 운영 경로에서는 호출하지 않는다.

## 산출물

```text
Output_total/Writer/{run_key}/writer_editorial_packet_v2.json
Output_total/Writer/{run_key}/writer_packet_provenance_v2.json
Output_total/Writer/{run_key}/writer_report_payload.json
Output_total/Writer/{run_key}/llm_writer_output.json
Output_total/Writer/{run_key}/writer_execution_cache_v2.json
Output_total/Writer/{run_key}/source_files.json
Output_total/Writer/{run_key}/writer_run_status.json
Output_total/Writer/{run_key}/report.html
Output_total/Writer/{run_key}/assets/*.png
```

## 실행

```bash
PYTHONPATH=src python 'src/Agent_Team/Writer Agent/writer_agent.py' \
  --run-key SK바이오팜_20251031 \
  --strategy-packet Output_total/Strategy/SK바이오팜_20251031/strategy_compact_packet_v2.json \
  --strategy-provenance Output_total/Strategy/SK바이오팜_20251031/strategy_packet_provenance_v2.json \
  --strategy-decision Output_total/Strategy/SK바이오팜_20251031/strategy_decision_output_v4.json \
  --output-dir Output_total/Writer/SK바이오팜_20251031 \
  --market-chart Output_total/Y_Finance/SK바이오팜_20251031/charts/full_period_technical.png \
  --market-chart Output_total/Y_Finance/SK바이오팜_20251031/charts/full_period_kospi_fx.png \
  --env-file configs/.env
```

## 테스트

```bash
pytest -q 'src/Agent_Team/Writer Agent/tests'
```
