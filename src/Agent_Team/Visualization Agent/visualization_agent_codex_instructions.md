# Visualization Agent 구축 지시서

## 0. 목적

현재 Financial Agent / Y-Finance Agent / Strategy Agent의 산출물을 기반으로 **Writer Agent가 증권사 리포트 PDF를 생성할 때 사용할 시각화 산출물**을 만드는 `Visualization Agent`를 구축한다.

핵심 원칙은 다음과 같다.

1. **Writer Agent는 숫자를 새로 계산하지 않는다.**
2. 숫자 계산, 시계열 가공, 차트 생성은 `Visualization Agent` 또는 deterministic Python 코드가 담당한다.
3. 시각화 결과는 단순 이미지 파일만이 아니라, Writer Agent가 안전하게 사용할 수 있도록 `chart_manifest.json` 형태의 메타데이터와 함께 저장한다.
4. 현재 데이터에 없는 지표를 임의 생성하지 않는다. 특히 P/E Band, P/B Band, OPM, ROE는 현재 데이터만으로 정확히 만들 수 없으므로 이번 버전에서는 제외한다.
5. 이 문서에 등장하는 특정 기업명, run_key, 날짜, 제품명, 수치, 차트 파일명은 샘플 입력을 설명하기 위한 예시로만 사용한다. 구현체는 특정 기업이나 특정 리포트 케이스에 종속되면 안 된다.

---

## 1. 현재 컨펌된 시각화 범위

이번 Visualization Agent가 생성해야 하는 핵심 차트는 아래 2개다.

### Chart 1. Stock Price Graph with MA20/MA60, Volume Ratio, and Relative Strength

목적:

- 대상 기업의 주가가 20일/60일 이동평균선 대비 어떤 위치에 있는지 보여준다.
- 거래량 활성도(`stock_volume_ratio_20`)를 함께 보여준다.
- 시장 대비 상대성과(`stock_excess_return_20d`, `stock_relative_strength_60`)를 함께 보여준다.
- Strategy Agent의 “절대 주가는 상승했지만 KOSPI 대비 상대강도는 약세”라는 논리를 시각적으로 보조한다.

권장 구조:

- 하나의 composite figure로 생성한다.
- 세로 3단 구성이 적절하다.
  1. Top panel: `stock_close`, derived `MA20`, derived `MA60`
  2. Middle panel: `stock_volume_ratio_20`, 기준선 1.0
  3. Bottom panel: `stock_excess_return_20d`, `stock_relative_strength_60`, 기준선 0

### Chart 2. Contribution Margin and SG&A Margin Time-Series Fundamental Trend

목적:

- DART 기반 재무 지표에서 수익성 구조 변화를 보여준다.
- OPM/ROE가 아닌, 현재 데이터에 실제 존재하는 `contribution_margin`과 `sga_margin`을 사용한다.
- 2025년 수치는 `Q3 YTD` 기준이므로 2022~2024년 연간 수치와 직접 YoY 비교하지 않도록 주석을 반드시 포함한다.

권장 구조:

- X축: `2022 FY`, `2023 FY`, `2024 FY`, `2025 Q3 YTD`
- Y축: percentage
- Line 1: `Contribution Margin`
- Line 2: `SG&A Margin`
- 2025년 포인트에는 YTD 기준 주석 또는 caption limitation을 반드시 추가한다.

---

## 2. 입력 파일 경로

Visualization Agent는 `{run_key}`를 단일 실행 키로 사용해 표준 입력 경로를 동적으로 해석한다. CLI argument로 특정 파일을 override할 수는 있지만, 기본 동작은 특정 기업명이 아니라 `{run_key}` 기반이어야 한다.

```text
/home/agent2/Financial_Agent_Final/Output_total/Financial/{run_key}/dart_main.json
/home/agent2/Financial_Agent_Final/Output_total/Financial/{run_key}/dart_lightweight.json

/home/agent2/Financial_Agent_Final/Output_total/Y_Finance/{run_key}/market_full_dataset.csv
fallback: /home/agent2/Financial_Agent_Final/Output_total/Y_Finance/market_full_dataset.csv

/home/agent2/Financial_Agent_Final/Output_total/Strategy/{run_key}/strategy_report.json
/home/agent2/Financial_Agent_Final/Output_total/Strategy/{run_key}/strategy_report.md
```

주의:

- `{run_key}`가 명시되지 않으면 `Output_total/Strategy`에서 최신 `strategy_report.json` 보유 run을 자동 탐색한다.
- 위 경로에 파일이 없을 수 있으므로 CLI 실행 시 파일 존재 여부를 반드시 검증한다.
- 경로가 깨졌을 때는 명확한 에러 메시지를 출력한다.
- `strategy_report.json`과 `strategy_report.md`는 차트의 텍스트 근거 및 manifest의 source trace 용도로만 사용한다. 차트 계산에는 사용하지 않는다.

---

## 3. 출력 경로

아래 경로를 기본 출력 디렉토리로 사용한다.

```text
/home/agent2/Financial_Agent_Final/Output_total/Visualization/{run_key}/
```

디렉토리 구조는 다음과 같이 생성한다.

```text
Visualization/{run_key}/
├── figures/
│   ├── stock_price_ma_volume_relative_strength.pdf
│   ├── stock_price_ma_volume_relative_strength.png
│   ├── fundamental_margin_trend.pdf
│   └── fundamental_margin_trend.png
├── chart_manifest.json
├── visualization_summary.md
└── data_quality_report.json
```

---

## 4. 구현 위치 제안

기존 repository 구조를 먼저 확인한 뒤, 아래 구조를 우선적으로 따른다.

```text
/home/agent2/Financial_Agent_Final/src/Agent_Team/Visualization Agent/
├── __init__.py
├── data_loader.py
├── chart_builders.py
├── manifest_builder.py
├── visualization_agent.py
└── run_visualization_agent.py
```

테스트가 있는 repository라면 아래 파일도 추가한다.

```text
/home/agent2/Financial_Agent_Final/tests/test_visualization_agent.py
```

---

## 5. 필수 입력 컬럼 및 필드

### 5.1 Market CSV 필수 컬럼

`market_full_dataset.csv`에서 아래 컬럼을 사용한다.

```text
date
stock_close
stock_close_to_ma20
stock_close_to_ma60
stock_volume_ratio_20
stock_excess_return_20d
stock_relative_strength_60
```

선택적으로 아래 컬럼이 있으면 `data_quality_report.json`에 요약값을 기록한다.

```text
stock_return_5d
stock_return_20d
stock_return_60d
stock_rsi_14
stock_macd_hist
stock_volatility_20
kospi_close
kospi_return_20d
fx_close
```

### 5.2 DART JSON 필수 필드

`dart_main.json`에서 아래 구조를 사용한다.

```text
periods
metrics_by_key.contribution_margin.values_by_period
metrics_by_key.sga_margin.values_by_period
```

가능하면 아래 값도 `data_quality_report.json`에 함께 기록한다.

```text
metrics_by_key.revenue.values_by_period
metrics_by_key.contribution_profit.values_by_period
metrics_by_key.eps.values_by_period
```

---

## 6. 데이터 가공 규칙

### 6.1 날짜 처리

- `market_full_dataset.csv`의 `date`는 pandas `datetime64`로 변환한다.
- 날짜 오름차순으로 정렬한다.
- 중복 날짜가 있으면 중복 제거 전에 warning을 남긴다.
- `stock_close`가 없는 행은 차트 계산에서 제외한다.

### 6.2 MA20 / MA60 역산

현재 CSV에는 실제 MA20, MA60 값이 직접 들어있지 않고, 아래 비율 컬럼만 있다.

```text
stock_close_to_ma20
stock_close_to_ma60
```

비율 정의가 아래와 같다고 가정한다.

```text
stock_close_to_ma20 = (stock_close - MA20) / MA20
stock_close_to_ma60 = (stock_close - MA60) / MA60
```

따라서 이동평균선은 아래 공식으로 역산한다.

```python
ma20 = stock_close / (1 + stock_close_to_ma20)
ma60 = stock_close / (1 + stock_close_to_ma60)
```

주의:

- `1 + stock_close_to_ma20` 또는 `1 + stock_close_to_ma60`이 0이거나 결측이면 해당 MA 값은 `NaN` 처리한다.
- 역산된 컬럼명은 아래처럼 둔다.

```text
derived_ma20
derived_ma60
```

### 6.3 수익률/상대강도 단위 변환

CSV의 수익률 계열은 ratio로 들어있다. 차트에는 percentage point로 표시한다.

```python
stock_excess_return_20d_pct = stock_excess_return_20d * 100
stock_relative_strength_60_pct = stock_relative_strength_60 * 100
```

`stock_volume_ratio_20`은 ratio 그대로 사용한다.

### 6.4 DART margin 단위 변환

DART JSON의 `contribution_margin`, `sga_margin` 값은 ratio로 들어있다. 차트에서는 percentage로 표시한다.

```python
contribution_margin_pct = contribution_margin * 100
sga_margin_pct = sga_margin * 100
```

기간 label은 아래 규칙으로 만든다.

```text
FULL_YEAR → "{fiscal_year} FY"
Q3 + YTD → "{fiscal_year} Q3 YTD"
기타 → "{fiscal_year} {period_type} {basis}"
```

정렬 기준은 `period.period_end` 오름차순이다.

---

## 7. 차트 생성 상세 요구사항

### 7.1 Chart 1: stock_price_ma_volume_relative_strength

파일명:

```text
figures/stock_price_ma_volume_relative_strength.pdf
figures/stock_price_ma_volume_relative_strength.png
```

권장 크기:

```python
figsize=(12, 8)
dpi=200 이상
```

구성:

#### Panel 1: Stock price and moving averages

- X축: `date`
- Y축: KRW
- Lines:
  - `stock_close`
  - `derived_ma20`
  - `derived_ma60`

표시:

- 마지막 날짜의 종가를 annotation으로 표시한다.
- Y축 라벨: `Price (KRW)`
- 제목: `{company_name} Stock Price with MA20/MA60`

#### Panel 2: Volume ratio

- X축: `date`
- Y축: `stock_volume_ratio_20`
- 기준선: `1.0`
- Y축 라벨: `Volume Ratio (20D)`
- 제목: `20D Volume Ratio`

#### Panel 3: Relative performance

- X축: `date`
- Y축: percentage
- Lines:
  - `stock_excess_return_20d_pct`
  - `stock_relative_strength_60_pct`
- 기준선: `0`
- Y축 라벨: `Relative Performance (%)`
- 제목: `20D Excess Return and 60D Relative Strength`

Caption 후보:

```text
주가는 20일 및 60일 이동평균선 대비 위치, 20일 거래량 비율, 20일 초과수익률 및 60일 상대강도를 함께 보여준다. 가격 추세는 개선 신호를 보일 수 있으나, 시장 대비 상대성과가 약할 경우 펀더멘털 개선을 단정하지 않는다.
```

Writer allowed interpretation:

```text
주가의 절대 추세, 이동평균선 대비 위치, 거래량 활성도, 시장 대비 상대성과를 설명할 수 있다. 단, 주가 상승만으로 펀더멘털 개선이나 투자 판단을 단정하지 않는다.
```

Forbidden interpretation:

```text
- 이동평균선 상회만으로 매수 신호라고 단정하지 않는다.
- 거래량 증가만으로 실적 개선을 단정하지 않는다.
- 상대강도 약세를 기업 펀더멘털 악화로 단정하지 않는다.
- 목표주가, upside/downside를 이 차트에서 산출하지 않는다.
```

### 7.2 Chart 2: fundamental_margin_trend

파일명:

```text
figures/fundamental_margin_trend.pdf
figures/fundamental_margin_trend.png
```

권장 크기:

```python
figsize=(10, 6)
dpi=200 이상
```

구성:

- X축: period label
- Y축: percentage
- Lines:
  - `contribution_margin_pct`
  - `sga_margin_pct`
- 각 포인트에 marker 표시
- 2025 Q3 YTD point에는 footnote 또는 caption에 limitation 반영

제목:

```text
{company_name} Contribution Margin and SG&A Margin Trend
```

Caption 후보:

```text
DART 기준 공헌이익률과 판관비율의 추이를 보여준다. 2025년 수치는 3분기 누적 기준이며, 2022~2024년 연간 수치와 직접적인 YoY 비교에는 제한이 있다.
```

Writer allowed interpretation:

```text
공헌이익률과 판관비율의 방향성을 바탕으로 수익성 구조 변화를 설명할 수 있다. 단, 2025년 Q3 YTD 수치를 연간 수치와 직접 YoY 개선으로 단정하지 않는다.
```

Forbidden interpretation:

```text
- contribution_margin을 OPM으로 표현하지 않는다.
- sga_margin 개선만으로 영업이익률 개선을 단정하지 않는다.
- 2025 Q3 YTD와 2024 FY를 동일 기준 YoY로 단정하지 않는다.
- ROE, ROA, P/E, P/B 등 현재 데이터에 없는 지표를 임의 생성하지 않는다.
```

---

## 8. chart_manifest.json schema

`chart_manifest.json`은 Writer Agent가 직접 참조할 수 있도록 아래 구조로 생성한다.

```json
{
  "agent_name": "Visualization Agent",
  "output_version": "1.0",
  "target_company_name": "{company_name}",
  "target_run_key": "{run_key}",
  "created_at": "<ISO_TIMESTAMP>",
  "source_files": {
    "market_full_dataset": "/home/agent2/Financial_Agent_Final/Output_total/Y_Finance/market_full_dataset.csv",
    "dart_main": "/home/agent2/Financial_Agent_Final/Output_total/Financial/{run_key}/dart_main.json",
    "dart_lightweight": "/home/agent2/Financial_Agent_Final/Output_total/Financial/{run_key}/dart_lightweight.json",
    "strategy_report_json": "/home/agent2/Financial_Agent_Final/Output_total/Strategy/{run_key}/strategy_report.json",
    "strategy_report_md": "/home/agent2/Financial_Agent_Final/Output_total/Strategy/{run_key}/strategy_report.md"
  },
  "charts": [
    {
      "figure_id": "fig_stock_price_ma_volume_relative_strength",
      "title": "Stock Price with MA20/MA60, Volume Ratio, and Relative Strength",
      "chart_type": "multi_panel_time_series",
      "section_recommendation": "Market / Price View",
      "asset_path_pdf": "figures/stock_price_ma_volume_relative_strength.pdf",
      "asset_path_png": "figures/stock_price_ma_volume_relative_strength.png",
      "data_source": "market_full_dataset.csv",
      "used_columns": [
        "date",
        "stock_close",
        "stock_close_to_ma20",
        "stock_close_to_ma60",
        "stock_volume_ratio_20",
        "stock_excess_return_20d",
        "stock_relative_strength_60"
      ],
      "derived_columns": [
        "derived_ma20",
        "derived_ma60",
        "stock_excess_return_20d_pct",
        "stock_relative_strength_60_pct"
      ],
      "caption": "주가는 20일 및 60일 이동평균선 대비 위치, 20일 거래량 비율, 20일 초과수익률 및 60일 상대강도를 함께 보여준다.",
      "writer_allowed_interpretation": "주가의 절대 추세, 이동평균선 대비 위치, 거래량 활성도, 시장 대비 상대성과를 설명할 수 있다.",
      "writer_forbidden_interpretation": [
        "이동평균선 상회만으로 매수 신호라고 단정하지 않는다.",
        "거래량 증가만으로 실적 개선을 단정하지 않는다.",
        "목표주가를 산출하지 않는다."
      ],
      "data_limitations": [
        "시장 데이터는 가격 및 거래 지표이며 펀더멘털 개선의 직접 증거가 아니다."
      ]
    },
    {
      "figure_id": "fig_fundamental_margin_trend",
      "title": "Contribution Margin and SG&A Margin Time-Series Fundamental Trend",
      "chart_type": "line_time_series",
      "section_recommendation": "Financial Analysis",
      "asset_path_pdf": "figures/fundamental_margin_trend.pdf",
      "asset_path_png": "figures/fundamental_margin_trend.png",
      "data_source": "dart_main.json",
      "used_metrics": [
        "contribution_margin",
        "sga_margin"
      ],
      "caption": "DART 기준 공헌이익률과 판관비율의 추이를 보여준다. 2025년 수치는 3분기 누적 기준이며, 연간 수치와 직접 비교에는 제한이 있다.",
      "writer_allowed_interpretation": "공헌이익률과 판관비율의 방향성을 바탕으로 수익성 구조 변화를 설명할 수 있다.",
      "writer_forbidden_interpretation": [
        "contribution_margin을 OPM으로 표현하지 않는다.",
        "2025 Q3 YTD와 2024 FY를 동일 기준 YoY로 단정하지 않는다.",
        "ROE를 임의 생성하지 않는다."
      ],
      "data_limitations": [
        "2025년 수치는 Q3 YTD 기준이다.",
        "OPM과 ROE는 현재 입력 데이터만으로 정확히 산출하지 않는다."
      ]
    }
  ]
}
```

---

## 9. data_quality_report.json 요구사항

`data_quality_report.json`에는 최소 아래 내용을 포함한다.

```json
{
  "market_dataset": {
    "path": "...",
    "row_count": 241,
    "date_min": "2024-11-01",
    "date_max": "{base_date}",
    "required_columns_present": true,
    "missing_required_columns": [],
    "null_counts_for_required_columns": {},
    "latest_snapshot": {
      "date": "{base_date}",
      "stock_close": 115500.0,
      "stock_volume_ratio_20": 1.625938,
      "stock_excess_return_20d_pct": -7.5134,
      "stock_relative_strength_60_pct": -8.3032
    }
  },
  "dart_dataset": {
    "path": "...",
    "required_metrics_present": true,
    "missing_required_metrics": [],
    "periods": [
      "2022 FY",
      "2023 FY",
      "2024 FY",
      "2025 Q3 YTD"
    ],
    "basis_warning": "2025년 수치는 Q3 YTD 기준이므로 2024 FY와 직접 YoY 비교하지 않는다."
  }
}
```

주의:

- 위 숫자는 현재 데이터 기준 예시다. 구현 시에는 파일에서 직접 계산해서 작성한다.
- 값이 달라질 수 있으므로 hard-code하지 않는다.

---

## 10. visualization_summary.md 요구사항

`visualization_summary.md`는 사람이 빠르게 확인할 수 있는 요약 문서다.

필수 포함:

```markdown
# Visualization Agent Summary

## Target
- Company: {company_name}
- Run key: {run_key}

## Generated Charts
1. Stock Price with MA20/MA60, Volume Ratio, and Relative Strength
2. Contribution Margin and SG&A Margin Time-Series Fundamental Trend

## Key Data Notes
- Market data range: <date_min> ~ <date_max>
- DART periods: 2022 FY, 2023 FY, 2024 FY, 2025 Q3 YTD
- 2025 DART data is YTD, not full-year.

## Writer Agent Usage
- Use chart_manifest.json as the source of truth for chart captions and allowed interpretation.
- Do not infer P/E, P/B, OPM, or ROE from these charts.
```

---

## 11. CLI 실행 방식

아래 CLI를 구현한다. 표준 자동화 실행은 `{run_key}`만 전달하면 된다.

```bash
python "/home/agent2/Financial_Agent_Final/src/Agent_Team/Visualization Agent/run_visualization_agent.py" \
  --run-key "{run_key}" \
  --output-root /home/agent2/Financial_Agent_Final/Output_total
```

특정 입력 파일만 교체해야 할 때만 `--market-csv`, `--dart-main`, `--strategy-json`, `--output-dir` 등을 명시한다.

실행 결과로 아래 파일이 생성되어야 한다.

```text
/home/agent2/Financial_Agent_Final/Output_total/Visualization/{run_key}/figures/stock_price_ma_volume_relative_strength.pdf
/home/agent2/Financial_Agent_Final/Output_total/Visualization/{run_key}/figures/stock_price_ma_volume_relative_strength.png
/home/agent2/Financial_Agent_Final/Output_total/Visualization/{run_key}/figures/fundamental_margin_trend.pdf
/home/agent2/Financial_Agent_Final/Output_total/Visualization/{run_key}/figures/fundamental_margin_trend.png
/home/agent2/Financial_Agent_Final/Output_total/Visualization/{run_key}/chart_manifest.json
/home/agent2/Financial_Agent_Final/Output_total/Visualization/{run_key}/visualization_summary.md
/home/agent2/Financial_Agent_Final/Output_total/Visualization/{run_key}/data_quality_report.json
```

---

## 12. 코드 품질 요구사항

1. 모든 계산은 deterministic Python 코드로 수행한다.
2. LLM 호출을 사용하지 않는다.
3. 파일 경로, 컬럼명, metric key가 없을 때 명확한 에러를 발생시킨다.
4. chart 생성 실패 시 어느 chart에서 실패했는지 로그로 남긴다.
5. matplotlib 사용 시 headless 서버에서도 동작하도록 backend를 설정한다.

예시:

```python
import matplotlib
matplotlib.use("Agg")
```

6. 한글 폰트가 없을 수 있으므로 기본 제목은 영어로 작성한다. 단, caption과 manifest는 한국어를 허용한다.
7. PDF와 PNG를 모두 저장한다.
8. 기존 Agent output을 수정하지 않는다.
9. Writer Agent가 바로 사용할 수 있도록 상대 경로와 절대 경로를 모두 manifest에 포함하는 것을 권장한다.

---

## 13. 구현 함수 제안

### data_loader.py

```python
def load_market_dataset(path: str) -> pd.DataFrame:
    """Load market_full_dataset.csv, validate required columns, parse dates, sort ascending."""


def load_dart_index(path: str) -> dict:
    """Load dart_main.json and validate basic schema."""


def extract_margin_trend(dart_index: dict) -> pd.DataFrame:
    """Extract contribution_margin and sga_margin by period, return ordered DataFrame."""
```

### chart_builders.py

```python
def build_stock_price_ma_volume_relative_strength_chart(
    market_df: pd.DataFrame,
    output_pdf: str,
    output_png: str,
    company_name: str,
) -> dict:
    """Create composite market chart and return chart metadata."""


def build_fundamental_margin_trend_chart(
    margin_df: pd.DataFrame,
    output_pdf: str,
    output_png: str,
    company_name: str,
) -> dict:
    """Create margin trend chart and return chart metadata."""
```

### manifest_builder.py

```python
def build_chart_manifest(
    company_name: str,
    run_key: str,
    source_files: dict,
    chart_metadata: list,
    output_path: str,
) -> dict:
    """Build and write chart_manifest.json."""


def build_data_quality_report(
    market_df: pd.DataFrame,
    margin_df: pd.DataFrame,
    output_path: str,
) -> dict:
    """Build and write data_quality_report.json."""
```

### visualization_agent.py

```python
def run_visualization_agent(config: dict) -> dict:
    """End-to-end execution: load data, generate charts, write manifest and reports."""
```

### run_visualization_agent.py

```python
def main():
    """Parse CLI arguments and call run_visualization_agent."""
```

---

## 14. 테스트 요구사항

가능하면 아래 테스트를 추가한다.

```text
tests/test_visualization_agent.py
```

테스트 항목:

1. Market CSV 필수 컬럼 검증
2. `derived_ma20`, `derived_ma60` 계산 검증
3. DART JSON에서 `contribution_margin`, `sga_margin` 추출 검증
4. `2025 Q3 YTD` label 생성 검증
5. chart_manifest.json 생성 검증
6. 없는 컬럼이 들어왔을 때 명확한 에러 발생 검증

---

## 15. 이번 버전에서 하지 말아야 할 것

아래 기능은 이번 Visualization Agent 범위에서 제외한다.

```text
- P/E Band 생성
- P/B Band 생성
- OPM 계산
- ROE 계산
- 목표주가 산출
- Upside/downside 산출
- Forecast 기반 실적 추정
- 새로운 투자 의견 생성
- Strategy Agent 결과 수정
- Writer Agent 본문 생성
- LaTeX PDF 컴파일
```

이유:

- 현재 데이터에는 P/B Band에 필요한 BPS 또는 발행주식수 기반 BPS가 없다.
- 현재 데이터에는 ROE 계산에 필요한 순이익과 평균자본이 없다.
- 현재 데이터의 DART 수치는 2025 Q3 YTD와 2022~2024 FY가 섞여 있으므로 직접 YoY 단정에 제한이 있다.
- Visualization Agent는 Writer Agent의 보조 입력을 만드는 역할이지, 투자판단을 새로 생성하는 Agent가 아니다.

---

## 16. Acceptance Criteria

작업 완료 기준은 아래와 같다.

1. CLI 명령 한 번으로 Visualization output directory가 생성된다.
2. 2개의 chart가 PDF와 PNG로 각각 저장된다.
3. `chart_manifest.json`이 생성된다.
4. `data_quality_report.json`이 생성된다.
5. `visualization_summary.md`가 생성된다.
6. `chart_manifest.json`에는 각 chart의 caption, allowed interpretation, forbidden interpretation, data limitations가 포함된다.
7. Writer Agent가 manifest만 읽고 차트를 리포트에 배치할 수 있어야 한다.
8. 현재 데이터에 없는 P/E, P/B, OPM, ROE, 목표주가를 생성하지 않는다.
9. 기존 Agent output 파일을 수정하지 않는다.
10. Headless Ubuntu 서버 환경에서 실행 가능해야 한다.

---

## 17. Writer Agent와의 연결 방식

Writer Agent에는 `chart_manifest.json`만 넘긴다.

Writer Agent는 아래 정보만 사용한다.

```text
- figure_id
- title
- asset_path_pdf
- caption
- section_recommendation
- writer_allowed_interpretation
- writer_forbidden_interpretation
- data_limitations
```

Writer Agent가 직접 chart를 해석하거나 숫자를 새로 계산하지 않도록 한다.

권장 연결 예시:

```json
{
  "strategy_report": "/home/agent2/Financial_Agent_Final/Output_total/Strategy/{run_key}/strategy_report.json",
  "chart_manifest": "/home/agent2/Financial_Agent_Final/Output_total/Visualization/{run_key}/chart_manifest.json"
}
```

---

## 18. 최종 요약

이번 작업의 핵심은 “증권사 리포트용 시각화 산출물 생성 Agent”를 만드는 것이다.

현재 데이터 기반으로 확정된 차트는 다음 2개다.

1. `Stock Price Graph with MA20/MA60, Volume Ratio, and Relative Strength`
2. `Contribution Margin and SG&A Margin Time-Series Fundamental Trend`

이 2개는 현재 제공된 `market_full_dataset.csv`, `dart_main.json`, `dart_lightweight.json`, `strategy_report.json`, `strategy_report.md`만으로 생성 가능하다.

반면 P/E Band, P/B Band, OPM, ROE는 현재 데이터만으로 정확하게 만들 수 없으므로 이번 버전에서는 생성하지 않는다.
