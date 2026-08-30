# Visualization Agent

Visualization Agent는 보고서에 사용할 수 있는 차트 목록을 만들고, Writer가 선택한 차트만 자료에서 직접 생성한다. 언어 모델은 호출하지 않으며 Strategy의 판단문이나 `decision_basis_card.json`을 입력으로 사용하지 않는다.

## 운영 순서

```text
재무·시장·비교기업 산출물 확인
  -> 생성 가능한 차트 목록 작성
  -> Writer가 보고서 본문과 함께 차트 key를 최대 2개 선택하고 판단 근거 카드를 연결
  -> 선택된 차트만 PNG·PDF로 생성
  -> Writer가 최종 보고서 끝에 PNG를 첨부
```

Writer에 제공하는 차트는 다음 일곱 종류 중 실제 자료 요건을 충족한 항목으로 제한한다.

| key | 내용 |
|---|---|
| `stock_technical` | 주가, 이동평균과 거래량 |
| `stock_vs_kospi` | 대상기업과 코스피의 지수화 성과 |
| `profitability_margin` | 공헌이익률과 판관비율 추이 |
| `revenue_profit_sga` | 매출, 공헌이익과 판관비 추이 |
| `peer_return` | 대상기업과 비교기업의 기간 수익률 |
| `peer_profitability` | 대상기업과 비교기업의 수익성 |
| `liquidity_leverage` | 대상기업과 비교기업의 유동성·자본구조 |

## 산출물

```text
Output_total/Visualization/{run_key}/chart_catalog.json
Output_total/Visualization/{run_key}/chart_selection.json
Output_total/Visualization/{run_key}/chart_manifest.json
Output_total/Visualization/{run_key}/figures/{chart_key}.png
Output_total/Visualization/{run_key}/figures/{chart_key}.pdf
```

`chart_catalog.json`에는 Writer가 선택할 수 있는 key와 차트 설명, 연결 가능한 Strategy 카드, 사용하기 적합한 보고서 항목, 해석상의 한계가 기록된다. 실제 선택된 Strategy 카드와 연결되지 않는 차트는 Writer 후보에서 제외된다. 비교 차트는 Strategy의 `target_peer_context`에 대응하는 구조화 비교 카드가 있을 때만 후보가 된다. 비교 에이전트의 내부 종합 카드만으로는 비교 차트를 만들지 않는다. Writer 결과의 `chart_selection_details`에는 선택 차트와 이를 뒷받침하는 Strategy 근거 카드가 연결된다. Visualization Agent는 이 연결을 새로운 투자 판단으로 해석하지 않고 지정된 차트를 자료에서 생성한다. `chart_manifest.json`에는 실제 생성된 차트의 순서, 캡션과 파일 경로가 기록된다.

## 개별 실행

먼저 차트 목록을 작성한다.

```bash
python "src/Agent_Team/Visualization Agent/report_chart_cli.py" catalog \
  --output-root Output_total \
  --run-key 현대모비스_20251031 \
  --company-name 현대모비스 \
  --peer-run-key 한온시스템_20251031 \
  --output-dir Output_total/Visualization/현대모비스_20251031
```

Writer의 `writer_report_payload.json`이 만들어진 뒤 선택된 차트를 생성한다.

```bash
python "src/Agent_Team/Visualization Agent/report_chart_cli.py" generate \
  --output-root Output_total \
  --run-key 현대모비스_20251031 \
  --company-name 현대모비스 \
  --peer-run-key 한온시스템_20251031 \
  --output-dir Output_total/Visualization/현대모비스_20251031 \
  --selection-file Output_total/Writer/현대모비스_20251031/writer_report_payload.json
```

일반 실행에서는 위 명령을 직접 호출할 필요 없이 `orchestration.full_report_pipeline`을 사용한다.
