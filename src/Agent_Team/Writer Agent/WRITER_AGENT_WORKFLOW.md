# Writer Agent Workflow

## 목적

Writer Agent는 Strategy v5가 작성한 판단 방향, 기존 편입자·신규 자금 대응, 판단 근거와 보고서 문맥을 한국어 one-paper HTML 보고서로 편집한다. 정형화된 의견 등급은 입력받지 않는다. LLM은 투자 판단 요약과 본문을 작성하고 핵심 근거표의 독자용 근거명을 정한다. 표의 사실·수치와 투자 해석은 연결된 카드에서 옮기며, 위험 제목과 내용은 Strategy가 작성한 값을 사용한다.

별도 Review 또는 Repair LLM은 사용하지 않는다. 검증에 실패하면 raw 응답을 fingerprint cache에 보존해 코드 검증만 다시 수행할 수 있지만, 새로운 분석 문장을 규칙으로 생성하지 않는다.

## 입력

보고서 생성에는 Strategy 산출물 세 개와 Visualization Agent의 차트 목록이 필요하다.

```text
Output_total/Strategy/{run_key}/strategy_compact_packet_v2.json
Output_total/Strategy/{run_key}/strategy_packet_provenance_v2.json
Output_total/Strategy/{run_key}/strategy_decision_output_v5.json
Output_total/Visualization/{run_key}/chart_catalog.json
```

`writer_handoff.py`는 Strategy가 실제 사용한 판단 근거와 보고서 문맥의 합집합만 선별해 `writer_editorial_packet_v3`를 만든다.

```json
{
  "packet_version": "writer_editorial_packet_v3",
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
  "target_peer_context": [
    {
      "basis_card_key": "peer.financial_position",
      "metric_keys": ["debt_ratio_pct", "current_ratio_pct"],
      "decision_role": "reinforce",
      "target_implication": "대상기업 판단에 미치는 의미"
    }
  ],
  "risk_factors": [],
  "general_limitations": [],
  "required_limitations": []
}
```

raw evidence ID와 원천 경로는 LLM 입력에서 제외하고 `writer_packet_provenance_v3.json`에만 저장한다. `reader_observation`은 원시값을 없애지 않으면서 재무 금액을 억원, 비율을 %, valuation을 배 단위로 미리 표시한다.

## 처리 순서

```text
Strategy v5 산출물 3개 로드
  -> 생성 가능한 차트 목록 로드
  -> editorial card 합집합과 component routing 생성
  -> provenance content hash 검증
  -> LLM Writer 단일 호출로 판단 요약, 본문, 근거명, requested_chart_keys와 근거 연결 생성
  -> 구조화된 카드 사실·수치와 Strategy 해석으로 근거·위험 표 구성
  -> 문장별 _claim_units와 card scope 연결
  -> Visualization Agent가 선택 차트 최대 2개 생성
  -> chart_manifest 순서대로 차트 자산 연결
  -> HTML 렌더링
  -> 생성 상태와 파일 기록
```

## 6개 섹션

| 순서 | key | 목적 |
|---:|---|---|
| 1 | `investment_call_thesis` | 판단 방향, 투자기간, 기존 편입자·신규 접근자 대응 |
| 2 | `business_market_context` | 매출 구조와 시장 맥락 |
| 3 | `key_evidence_table` | 재무, 제품 매출, 시장, 가치평가와 대상기업 판단에 사용된 비교 지표 중 Strategy가 선택한 최대 6개 근거 축 |
| 4 | `catalysts_execution` | 확인된 event와 미확인 재무 기여의 구분 |
| 5 | `risk_monitoring_matrix` | Strategy 위험과 투자 판단에 미치는 영향 |
| 6 | `data_limits` | 자료 시점, 기간, 범위, 비교와 인과 한계 |

각 text item은 component별 `card_keys`와 문장별 `_claim_units`를 hidden metadata로 가진다. 투자 판단 요약은 Writer LLM이 Strategy 판단과 투자자별 대응을 두 문단 이내로 편집한다. 핵심 근거표의 `핵심 근거`는 Writer LLM이 카드마다 작성하고, `확인된 수치·사실`, `투자 해석`, `판단상 역할`은 각각 구조화된 관찰값과 Strategy 필드에서 구성한다. 위험표의 제목·내용·판단 영향은 Strategy의 `risk_title`, `risk`, `current_implication`을 그대로 전달한다. renderer는 metadata와 semantic card key를 HTML에 표시하지 않는다.

비교 에이전트의 종합 문장이나 비교기업 전체 평가는 Writer에 직접 전달하지 않는다. Strategy가 `target_peer_context`에서 선택한 최대 2개 비교 축과 축별 최대 2개 지표만 핵심 근거표에 포함하며, `target_implication`은 비교기업이 아니라 대상기업의 판단을 설명하는 문장으로 사용한다. 비교기업 전용 섹션은 만들지 않고 사업·시장 현황에서 같은 비교를 반복하지 않는다.

Writer handoff를 만들 때 Strategy의 recommendation bridge, assessment 해석, peer finding과 risk 문장을 사전 검사한다. 내부 JSON field명이나 semantic card key가 문장에 들어 있으면 LLM을 호출하기 전에 거부한다. `_claim_units.claim`은 나중에 독자용 문단으로 복원될 수 있으므로 card label 치환 대상에 포함하고, 실제 `card_keys` 배열은 hidden identifier로 유지한다.

## 보고서 생성과 평가의 분리

운영 실행에서는 작성 결과를 자연어 규칙으로 판정하거나 같은 요청을 다시 호출하지 않는다. 파이썬은 표의 사실·수치, Strategy 해석, 내부 식별자 제외와 고정 면책문구처럼 의미를 새로 결정하지 않는 작업만 처리한다. 응답이 지정된 자료 형식으로 해석되지 않거나 `report.html`을 생성하지 못한 경우에만 실행 오류로 처리한다.

`html_report_validator.py`는 회귀시험과 논문 실험에서 보고서의 근거 연결, 의미 보존과 구성 품질을 측정하기 위해 남겨두며 운영 경로에서는 호출하지 않는다.

## 산출물

```text
Output_total/Writer/{run_key}/writer_editorial_packet_v3.json
Output_total/Writer/{run_key}/writer_packet_provenance_v3.json
Output_total/Writer/{run_key}/writer_report_payload.json
Output_total/Writer/{run_key}/llm_writer_output.json
Output_total/Writer/{run_key}/writer_execution_cache_v2.json
Output_total/Writer/{run_key}/source_files.json
Output_total/Writer/{run_key}/writer_run_status.json
Output_total/Writer/{run_key}/report.html
Output_total/Writer/{run_key}/assets/*.png
```

`writer_report_payload.json`의 `requested_chart_keys`는 `chart_catalog.json`에 있는 key만 포함하며 최대 두 개다. `chart_selection_details`에는 각 차트가 보여주는 Strategy 근거 카드와 선택 이유가 같은 순서로 기록된다. 차트의 종류나 투자 방향은 코드가 정하지 않으며, Writer가 최종 판단에 직접 사용된 근거를 기준으로 선택한다. 차트는 본문 생성 뒤 만들어지고 최종 보고서의 본문 다음, 면책문구 앞에 배치된다.

## 실행

```bash
PYTHONPATH=src python 'src/Agent_Team/Writer Agent/writer_agent.py' \
  --phase generate \
  --run-key SK바이오팜_20251031 \
  --strategy-packet Output_total/Strategy/SK바이오팜_20251031/strategy_compact_packet_v2.json \
  --strategy-provenance Output_total/Strategy/SK바이오팜_20251031/strategy_packet_provenance_v2.json \
  --strategy-decision Output_total/Strategy/SK바이오팜_20251031/strategy_decision_output_v5.json \
  --output-dir Output_total/Writer/SK바이오팜_20251031 \
  --chart-catalog Output_total/Visualization/SK바이오팜_20251031/chart_catalog.json \
  --env-file configs/.env
```

선택 차트가 생성된 뒤 최종 HTML을 렌더링한다.

```bash
PYTHONPATH=src python 'src/Agent_Team/Writer Agent/writer_agent.py' \
  --phase render \
  --run-key SK바이오팜_20251031 \
  --output-dir Output_total/Writer/SK바이오팜_20251031 \
  --chart-manifest Output_total/Visualization/SK바이오팜_20251031/chart_manifest.json
```

## 테스트

```bash
pytest -q 'src/Agent_Team/Writer Agent/tests'
```
