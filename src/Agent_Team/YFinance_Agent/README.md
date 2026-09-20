# YFinance Market Pipeline

`configs/company_input.json` 또는 `--input`으로 넘긴 JSON의 `company_name`, `ticker`, `date_range`, `selected_date`를 사용해서 주가 OHLCV, KOSPI, USD/KRW 환율, 기술 지표, 시각화 파일과 Y-Finance Agent 보고서를 생성합니다.

기본 출력 기간은 입력 파일의 `20251018-20251031`이며, `selected_date`인 `20251031` 하루 요약도 함께 만듭니다. 지표 계산용 원본 데이터는 `end_date` 기준 2년치로 내려받고, `market_full_dataset.csv/json`은 입력된 출력 기간만 저장합니다.

## Setup

```bash
cd /path/to/Financial_Agent
python -m pip install -r requirements.txt
```

## Run

시장 데이터 수집과 지표 계산은 `main.py` 가 담당합니다. 최종 보고서 파이프라인(`run_config/`)도 이 스크립트를 직접 호출합니다.

```bash
cd /path/to/Financial_Agent
python src/Agent_Team/YFinance_Agent/main.py --input configs/company_input.json
```

기본 env 파일은 `configs/.env`입니다. 기본 산출물 위치는 기업별 폴더인 `Output_total/Y_Finance/SK바이오팜_20251031`입니다.

LLM 보고서는 `reporting.generate_analyst_report` 가 만들며, 최종 보고서 파이프라인 안에서 조건별 입력과 함께 호출됩니다. 단독 실행용 스크립트는 두지 않습니다.

## Options

옵션으로 기간과 티커를 덮어쓸 수 있습니다.

```bash
python src/Agent_Team/YFinance_Agent/main.py \
  --input configs/company_input.json \
  --output-dir Output_total/Y_Finance \
  --start-date 20241101 \
  --end-date 20251031 \
  --selected-date 20251031 \
  --kospi-ticker ^KS11 \
  --fx-ticker KRW=X
```

보고서는 YFinance `market_full_dataset.json`을 주 분석자료로 사용합니다. 뉴스 에이전트의 주장을 전달받지 않고 최근 1년의 월별 요약 12개와 DART 3개년 재무 추세표를 공통 subdata로 읽습니다. 주 분석 입력은 1·3·6·12개월 지표, 월별 관측치 12개와 최근 20거래일로 제한하며 전체 일별 자료는 파일에 보관합니다. 가격 수익률은 배당을 제외한 공급자 분할조정 종가 기준입니다. 뉴스와 가격의 시간적 대응은 살펴보되 인과관계로 단정하지 않습니다.

보조자료는 시장 관측의 의미·지속성·위험을 해석하는 데 활용합니다. 도메인별 쟁점 배열에 연결 근거, `statement`, `judgment_impact`를 작성하고 `main_view.context_ids`로 종합 판단에 반영한 쟁점을 연결합니다. 관련성이 없으면 배열을 비워 두며 보조자료의 유무만으로 방향을 정하지 않습니다. 정규화된 산출물에는 보조자료의 출처와 기간 메타데이터도 함께 보존합니다.
`Output_total/Y_Finance/yfinance_analyst_report.json`은 `Y-Finance Agent` 스키마로 생성되며, `score` 필드는 포함하지 않습니다.
`secondary_context_assessment`는 `corroborates`, `contradicts`, `neutral`, `insufficient`만 허용하며 primary 시장 claim의 근거 상태를 변경하지 않습니다.

## Outputs

기본 산출물은 모두 `Output_total/Y_Finance/<company>_<YYYYMMDD>` 아래에 저장합니다.

- `market_full_dataset.csv`
- `market_full_dataset.json`
- `market_summary_20251031.csv`
- `market_summary_20251031.json`
- `market_summary.json`
- `charts/full_period_technical.png`
- `charts/full_period_kospi_fx.png`
- `charts/summary_20251031.png`
- `manifest.json`
- `yfinance_analyst_report.md`
- `yfinance_analyst_report.json`
- `final_report.json`
- `pipeline_manifest.json`

## Included Columns

CSV/JSON 출력 컬럼은 아래 순서로 고정됩니다.

- `date`
- `stock_close`
- `stock_return_5d`
- `stock_return_20d`
- `stock_return_60d`
- `stock_close_to_ma20`
- `stock_close_to_ma60`
- `stock_ma5_to_ma20`
- `stock_rsi_14`
- `stock_macd_hist`
- `stock_macd_hist_change_1d`
- `stock_bb_width_20`
- `stock_volatility_20`
- `stock_volume_ratio_20`
- `stock_obv_trend`
- `kospi_close`
- `kospi_return_5d`
- `kospi_return_20d`
- `kospi_close_to_ma20`
- `kospi_rsi_14`
- `kospi_volatility_20`
- `fx_close`
- `fx_return_5d`
- `fx_return_20d`
- `fx_close_to_ma20`
- `fx_rsi_14`
- `fx_volatility_20`
- `stock_excess_return_5d`
- `stock_excess_return_20d`
- `stock_relative_strength_60`

요약 데이터는 `selected_date` 당일 행을 우선 사용하고, 거래일이 아니면 직전 거래일 행을 사용합니다. 이 매칭 정보는 `manifest.json`에 기록합니다.
