# YFinance Market Pipeline

`configs/company_input.json` 또는 `--input`으로 넘긴 JSON의 `company_name`, `ticker`, `date_range`, `selected_date`를 사용해서 주가 OHLCV, KOSPI, USD/KRW 환율, 기술 지표, 시각화 파일, Y-Finance Agent 보고서, SY 검증 결과를 생성합니다.

기본 출력 기간은 입력 파일의 `20251018-20251031`이며, `selected_date`인 `20251031` 하루 요약도 함께 만듭니다. 지표 계산용 원본 데이터는 `end_date` 기준 2년치로 내려받고, `market_full_dataset.csv/json`은 입력된 출력 기간만 저장합니다.

## Setup

```bash
cd /path/to/Financial_Agent
python -m pip install -r requirements.txt
```

## Run All

```bash
cd /path/to/Financial_Agent
python src/Agent_Team/YFinance_Agent/run_pipeline.py
```

기본 실행은 아래 순서로 동작합니다.

- `main.py`: YFinance/KOSPI/FX 데이터 수집, 지표 계산, 차트 생성
- `report.py`: YFinance 시장 데이터를 기준으로 News/DART를 보조 반영한 LLM 보고서 생성
- `SY_Agent/sy_agent.py`: YFinance 보고서 주장 검증

기본 env 파일은 `configs/.env`입니다. 기본 산출물 위치는 기업별 폴더인 `Output_total/Y_Finance/SK바이오팜_20251031`입니다.

설치된 패키지 엔트리포인트를 사용할 수 있는 환경이면 같은 실행을 아래처럼 호출할 수 있습니다.

```bash
yfinance-pipeline
```

옵션으로 입력, 출력, 모델, 보조 데이터 경로를 덮어쓸 수 있습니다.

```bash
python src/Agent_Team/YFinance_Agent/run_pipeline.py \
  --input configs/company_input.json \
  --output-dir Output_total/Y_Finance/SK바이오팜_20251031 \
  --env-file configs/.env \
  --dart-json Output_total/Financial/SK바이오팜_20251031/dart_lightweight.json \
  --news-json Output_total/News/SK바이오팜_20251031/context_exports/day/llm_period_summaries.json
```

이미 시장 데이터가 있으면 `--skip-collect`, 이미 보고서가 있으면 `--skip-report`, SY 검증을 생략하려면 `--skip-sy`를 사용할 수 있습니다.

SY 검증은 날짜·수치·source path를 코드로 검사한 뒤 정성 claim을 한 번의 semantic batch로 평가합니다. 결과는 근거 사용 ledger로 저장하며 YFinance report 본문을 재작성하지 않습니다.

## Individual Steps

필요할 때만 개별 단계를 직접 호출할 수 있습니다.

```bash
cd /path/to/Financial_Agent
python src/Agent_Team/YFinance_Agent/main.py --input configs/company_input.json
```

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

이미 생성된 YFinance, News, DART JSON만 사용해서 LLM 기반 애널리스트 보고서를 만듭니다. 이 명령은 yfinance에서 시장 데이터를 새로 다운로드하지 않습니다.

```bash
cd /path/to/Financial_Agent
python src/Agent_Team/YFinance_Agent/report.py
```

기본적으로 `configs/.env`를 읽어 `OPENAI_API_KEY`를 사용합니다. 다른 env 파일을 쓰려면 `--env-file`로 지정합니다.

모델은 `--model` 또는 `OPENAI_MODEL`로 지정할 수 있으며, 기본값은 `gpt-5.4-mini`입니다.

```bash
python src/Agent_Team/YFinance_Agent/report.py --model gpt-5.4-mini
```

보고서는 YFinance `market_full_dataset.json`을 주 분석자료로 사용합니다. 뉴스 에이전트의 주장을 전달받지 않고 30개 날짜별 뉴스 요약을 보조자료로 직접 읽으며, 같은 날짜 또는 다음 거래일의 일간 수익률·시장 대비 수익률·거래량 변화와 함께 분석합니다. DART lightweight 지표도 보조자료로 사용합니다. 뉴스와 가격의 시간적 대응은 살펴보되 인과관계로 단정하지 않습니다.
`Output_total/Y_Finance/yfinance_analyst_report.json`은 `Y-Finance Agent` 스키마로 생성되며, `score` 필드는 포함하지 않습니다.
`secondary_context_assessment`는 `corroborates`, `contradicts`, `neutral`, `insufficient`만 허용하며 primary 시장 claim의 근거 상태를 변경하지 않습니다.

## Outputs

`run_pipeline.py` 기준 기본 산출물은 모두 `Output_total/Y_Finance/<company>_<YYYYMMDD>` 아래에 저장합니다.

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
- `sy_verified_yfinance_report.json`
- `yfinance_verified_report.json`
- `pipeline_manifest.json`

YFinance SY는 시장 날짜·수치·source path를 결정론적으로 검사하고, 정성 claim만 한 번의 semantic batch로 평가합니다. 원 보고서를 재작성하지 않습니다.

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
