# Writer Agent Workflow

## 목적

Writer Agent는 Strategy v2가 확정한 투자의견, recommendation bridge, card 해석과 투자 영향 방향을 바꾸지 않고 한국어 one-paper HTML 보고서로 편집한다. LLM은 사업·시장 맥락, 촉매와 데이터 한계 문장을 작성한다. thesis는 검증된 recommendation bridge를 사용하고 Key Evidence Table과 risk matrix는 코드가 구조화 카드에서 결정론적으로 구성한다.

별도 Review 또는 Repair LLM은 사용하지 않는다. 검증에 실패하면 raw 응답을 fingerprint cache에 보존해 코드 검증만 다시 수행할 수 있지만, 새로운 분석 문장을 규칙으로 생성하지 않는다.

## 입력

한 실행에는 Strategy v2 산출물 세 개가 필요하다.

```text
Output_total/Strategy/{run_key}/strategy_compact_packet_v2.json
Output_total/Strategy/{run_key}/strategy_packet_provenance_v2.json
Output_total/Strategy/{run_key}/strategy_decision_output_v2.json
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
Strategy v2 산출물 3개 로드
  -> editorial card 합집합과 component routing 생성
  -> provenance content hash 검증
  -> LLM Writer 단일 호출
  -> recommendation bridge thesis와 결정론적 evidence/risk 표 구성
  -> 문장별 _claim_units와 card scope 연결
  -> Gate C 의미·coverage 검증
  -> HTML 렌더링 및 렌더링 검증
```

## 6개 섹션

| 순서 | key | 목적 |
|---:|---|---|
| 1 | `investment_call_thesis` | 투자의견, 투자기간, 결정적 긍정·부정 근거 |
| 2 | `business_market_context` | 매출 구조와 시장 맥락 |
| 3 | `key_evidence_table` | 재무, 제품 매출, 시장, valuation, selected peer의 최대 8개 evidence axis |
| 4 | `catalysts_execution` | 확인된 event와 미확인 재무 기여의 구분 |
| 5 | `risk_monitoring_matrix` | Strategy typed risk와 확인 항목 |
| 6 | `data_limits` | 자료 시점, 기간, 범위, 비교와 인과 한계 |

각 text item은 component별 `card_keys`와 문장별 `_claim_units`를 hidden metadata로 가진다. 핵심 근거표의 `확인된 수치·사실`, `투자 해석`, `영향`과 risk의 독자용 요약은 Strategy typed field에서 생성한다. renderer는 metadata와 semantic card key를 HTML에 표시하지 않는다.

Writer handoff를 만들 때 Strategy의 recommendation bridge, assessment 해석, peer finding과 risk 문장을 사전 검사한다. 내부 JSON field명이나 semantic card key가 문장에 들어 있으면 LLM을 호출하기 전에 거부한다. `_claim_units.claim`은 나중에 독자용 문단으로 복원될 수 있으므로 card label 치환 대상에 포함하고, 실제 `card_keys` 배열은 hidden identifier로 유지한다.

## Gate C

- 완결된 HTML, 필수 section ID와 핵심 표 존재 여부 확인
- component별 card 집합이 `required_card_keys_by_component`와 정확히 일치하는지 확인
- Key Evidence 행당 card 하나와 중복·누락 확인
- Strategy interpretation과 investment effect의 hidden metadata 변경 차단
- Strategy negative factor와 thesis/risk 연결 확인
- risk 행 수, basis card와 risk summary 보존 확인
- 공시 시차, peer 범위, valuation 입력일, 제품표 범위와 News 재무 연결 한계 coverage 확인
- 투자기간과 투자의견 일치 확인
- raw ID, semantic card key, 절대 경로와 내부 field의 HTML 노출 차단
- 입력에 없는 큰 원 단위 정수와 금지된 schema key 차단
- A4 CSS, 목차/강조 태그, H1 개수, 표 열·행 순서, Strategy 문구의 완전 일치, claim 문장의 verbatim 일치, 문단 수와 전체 글자 수는 실행 중단이 아닌 advisory로 기록

따라서 정상적으로 렌더링된 HTML은 표현이나 CSS 차이만으로 실패하지 않는다. 자유 한국어 문장의 비교·추세·문체를 키워드로 판별하지 않으며, 핵심 의미는 Strategy typed metadata와 card 연결로 보존하고 나머지 문체 위험은 advisory, 회귀 fixture와 정성 평가로 관리한다.

## 산출물

```text
Output_total/Writer/{run_key}/writer_editorial_packet_v2.json
Output_total/Writer/{run_key}/writer_packet_provenance_v2.json
Output_total/Writer/{run_key}/writer_report_payload.json
Output_total/Writer/{run_key}/llm_writer_output.json
Output_total/Writer/{run_key}/writer_execution_cache_v2.json
Output_total/Writer/{run_key}/source_files.json
Output_total/Writer/{run_key}/writer_validation_report.json
Output_total/Writer/{run_key}/report.html
```

## 실행

```bash
PYTHONPATH=src python 'src/Agent_Team/Writer Agent/writer_agent.py' \
  --run-key SK바이오팜_20251031 \
  --strategy-packet Output_total/Strategy/SK바이오팜_20251031/strategy_compact_packet_v2.json \
  --strategy-provenance Output_total/Strategy/SK바이오팜_20251031/strategy_packet_provenance_v2.json \
  --strategy-decision Output_total/Strategy/SK바이오팜_20251031/strategy_decision_output_v2.json \
  --output-dir Output_total/Writer/SK바이오팜_20251031 \
  --env-file configs/.env
```

## 테스트

```bash
pytest -q 'src/Agent_Team/Writer Agent/tests'
```
