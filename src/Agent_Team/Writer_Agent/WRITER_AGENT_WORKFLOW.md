# Writer Agent Workflow

## 목적

Writer Agent는 Strategy 결정(`strategy_decision_output.json`, `decision_version = strategy_decision_output`)의 12개월 투자 의견, 판단 근거와 보고서 문맥을 한국어 one-paper HTML 보고서로 편집한다. `decision.opinion`에 Buy/Hold/Sell 중 하나가 있어야 하며, 보유자와 신규 자금을 나누어 대응하지 않는다. LLM은 투자 판단 요약과 본문을 작성하고 핵심 근거표의 독자용 근거명을 정한다. 표의 사실·수치와 투자 해석은 연결된 카드에서 옮기며, 위험 제목과 내용은 Strategy가 작성한 값을 사용한다.

별도 Review 또는 Repair LLM은 사용하지 않는다. 검증에 실패하면 raw 응답을 fingerprint cache에 보존해 코드 검증만 다시 수행할 수 있지만, 새로운 분석 문장을 규칙으로 생성하지 않는다.

## 입력

보고서 생성에는 Strategy 산출물 세 개와 Visualization Agent의 차트 목록이 필요하다.

```text
Output_total/{company}/Strategy/{YYYYMMDD}/strategy_compact_packet.json
Output_total/{company}/Strategy/{YYYYMMDD}/strategy_packet_provenance.json
Output_total/{company}/Strategy/{YYYYMMDD}/strategy_decision_output.json
Output_total/{company}/Visualization/{YYYYMMDD}/chart_catalog.json
```

`writer_handoff.py`는 Strategy가 실제 사용한 판단 근거와 보고서 문맥의 합집합만 선별해 `writer_editorial_packet`를 만든다.

```json
{
  "packet_version": "writer_editorial_packet",
  "target": {},
  "decision": {},
  "recommendation_bridge": {},
  "required_card_keys_by_component": {},
  "cards": {
    "financial.same_period_trend": {
      "primary_observation": {},
      "reader_observation": {},
      "strategy_interpretation": "...",
      "evidence_tier": "decision_basis"
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

raw evidence ID와 원천 경로는 LLM 입력에서 제외하고 `writer_packet_provenance.json`에만 저장한다. `reader_observation`은 원시값을 없애지 않으면서 재무 금액을 억원, 비율을 %, valuation을 배 단위로 미리 표시한다.

## 처리 순서

```text
Strategy 산출물 3개 로드
  -> 생성 가능한 차트 목록 로드
  -> editorial card 합집합과 component routing 생성
  -> provenance content hash 검증
  -> LLM Writer 단일 호출로 판단 요약, 본문, 근거명, requested_chart_keys와 근거 연결 생성
  -> 구조화된 카드 사실·수치와 Strategy 해석으로 근거·위험 표 구성
  -> 문장별 _claim_units와 card scope 연결
  -> Visualization Agent가 선택 차트 생성(사용 가능한 차트 안에서, 2개 권장)
  -> chart_manifest 순서대로 차트 자산 연결
  -> HTML 렌더링
  -> 생성 상태와 파일 기록
```

## 6개 섹션

표시 제목은 `html_report_spec.py`의 `REPORT_SECTIONS`를 따른다.

| 순서 | key | 표시 제목 | 담는 내용 |
|---:|---|---|---|
| 1 | `investment_call_thesis` | 투자 판단 요약 | 투자 의견과 핵심 논거 |
| 2 | `business_market_context` | 최근 실적과 가격 평가 | 손익·현금흐름과 시장 가격 |
| 3 | `key_evidence_table` | 핵심 판단 근거 | 판단을 구성한 주요 근거. Strategy가 고른 근거를 개수 제한 없이 옮긴다 |
| 4 | `catalysts_execution` | 향후 12개월 전망 | 성장 동인과 전망의 전제 |
| 5 | `risk_monitoring_matrix` | 리스크 점검 | 현재 위험과 투자 판단에 미치는 영향 |
| 6 | `data_limits` | 데이터 기준과 한계 | 자료 시점과 해석 범위 |

각 text item은 component별 `card_keys`와 문장별 `_claim_units`를 hidden metadata로 가진다. 투자 판단 요약은 Writer LLM이 Strategy 판단을 두 문단 이내로 편집한다. 핵심 근거표는 `핵심 근거`, `확인된 수치·사실`, `투자 판단에 미치는 의미` 3열이다. `핵심 근거`는 Writer LLM이 카드마다 작성하고, `확인된 수치·사실`은 구조화된 관찰값에서, `투자 판단에 미치는 의미`는 카드의 `strategy_interpretation`에서 옮긴다. 위험표의 제목·내용·판단 영향은 Strategy의 `risk_title`, `risk`, `current_implication`을 그대로 전달한다. renderer는 metadata와 semantic card key를 HTML에 표시하지 않는다.

비교 에이전트의 종합 문장이나 비교기업 전체 평가는 Writer에 직접 전달하지 않는다. Strategy가 `target_peer_context`에서 선택한 비교 축과 지표만 핵심 근거표에 넣으며, `target_implication`은 비교기업이 아니라 대상기업의 판단을 설명하는 문장으로 사용한다. 비교기업 전용 섹션은 만들지 않고 사업·시장 현황에서 같은 비교를 반복하지 않는다.

Writer handoff를 만들 때 Strategy의 recommendation bridge, assessment 해석, peer finding과 risk 문장을 사전 검사한다. 내부 JSON field명이나 semantic card key가 문장에 들어 있으면 LLM을 호출하기 전에 거부한다. `_claim_units.claim`은 나중에 독자용 문단으로 복원될 수 있으므로 card label 치환 대상에 포함하고, 실제 `card_keys` 배열은 hidden identifier로 유지한다.

## 보고서 생성과 평가의 분리

운영 실행에서는 작성 결과를 자연어 규칙으로 판정하거나 같은 요청을 다시 호출하지 않는다. 파이썬은 표의 사실·수치, Strategy 해석, 내부 식별자 제외와 고정 면책문구처럼 의미를 새로 결정하지 않는 작업만 처리한다. 응답이 지정된 자료 형식으로 해석되지 않거나 `report.html`을 생성하지 못한 경우에만 실행 오류로 처리한다.

`html_report_validator.py`는 회귀시험과 논문 실험에서 보고서의 근거 연결, 의미 보존과 구성 품질을 측정하기 위해 남겨두며 운영 경로에서는 호출하지 않는다.

## 산출물

```text
Output_total/{company}/Writer/{YYYYMMDD}/writer_editorial_packet.json
Output_total/{company}/Writer/{YYYYMMDD}/writer_packet_provenance.json
Output_total/{company}/Writer/{YYYYMMDD}/writer_report_payload.json
Output_total/{company}/Writer/{YYYYMMDD}/llm_writer_output.json
Output_total/{company}/Writer/{YYYYMMDD}/writer_execution_cache.json
Output_total/{company}/Writer/{YYYYMMDD}/source_files.json
Output_total/{company}/Writer/{YYYYMMDD}/writer_run_status.json
Output_total/{company}/Writer/{YYYYMMDD}/report.html
Output_total/{company}/Writer/{YYYYMMDD}/assets/*.png
```

`writer_report_payload.json`의 `requested_chart_keys`는 `chart_catalog.json`에 있는 key만 포함한다. 사용 가능한 차트 수가 유일한 구조상 한도이고, 2개는 권장값이다. `chart_selection_details`에는 각 차트가 보여주는 Strategy 근거 카드와 선택 이유가 같은 순서로 기록된다. 차트의 종류나 투자 방향은 코드가 정하지 않으며, Writer가 최종 판단에 직접 사용된 근거를 기준으로 선택한다. 차트는 본문 생성 뒤 만들어지고 최종 보고서의 본문 다음, 면책문구 앞에 배치된다.

## 실행

```bash
PYTHONPATH=src python -m Agent_Team.Writer_Agent.writer_agent \
  --phase generate \
  --run-key SK바이오팜_20251031 \
  --strategy-packet Output_total/SK바이오팜/Strategy/20251031/strategy_compact_packet.json \
  --strategy-provenance Output_total/SK바이오팜/Strategy/20251031/strategy_packet_provenance.json \
  --strategy-decision Output_total/SK바이오팜/Strategy/20251031/strategy_decision_output.json \
  --output-dir Output_total/SK바이오팜/Writer/20251031 \
  --chart-catalog Output_total/SK바이오팜/Visualization/20251031/chart_catalog.json \
  --env-file configs/.env
```

선택 차트가 생성된 뒤 최종 HTML을 렌더링한다.

```bash
PYTHONPATH=src python -m Agent_Team.Writer_Agent.writer_agent \
  --phase render \
  --run-key SK바이오팜_20251031 \
  --output-dir Output_total/SK바이오팜/Writer/20251031 \
  --chart-manifest Output_total/SK바이오팜/Visualization/20251031/chart_manifest.json
```

경로는 파이프라인과 같은 기업 우선 구조(`Output_total/<company>/<Agent>/<YYYYMMDD>`)를 따른다. Strategy 입력 경로와 `--output-dir`를 생략하면 `--run-key`로 같은 구조의 기본 경로를 찾는다.
