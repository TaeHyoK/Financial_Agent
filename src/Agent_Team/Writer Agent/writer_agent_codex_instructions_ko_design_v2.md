# Writer Agent 구축 지시서 (Codex 전달용)

## 0. 목적

이 문서는 `Writer Agent`를 구현하기 위한 Codex용 개발 지시서이다.

Writer Agent의 목적은 Strategy Agent, Visualization Agent, Financial Agent, Y-Finance Agent의 결과물을 입력으로 받아 **시각화 친화적인 증권사 리포트 형식의 최종 PDF 리포트**를 생성하는 것이다.

Writer Agent는 새로운 숫자를 계산하거나 새로운 투자 판단을 만들지 않는다.  
Writer Agent의 핵심 역할은 다음과 같다.

```text
1. Strategy Agent 결과를 증권사 리포트 문체로 재구성
2. Visualization Agent가 생성한 차트를 리포트 논리와 연결
3. 최종 리포트용 broker_report_contract_v1.json 생성
4. LaTeX 템플릿에 들어갈 섹션별 원고 생성
5. LaTeX Renderer를 통해 final_report.pdf 생성
6. 최종 리포트의 숫자, 차트, 금지 표현, 해석 제한을 검증
```

---

## 0-1. 자동화 및 비하드코딩 원칙

이 지시서에 등장하는 특정 기업명, run_key, 날짜, 제품명, 수치, 차트 파일명은 샘플 입력을 설명하기 위한 예시로만 사용한다.

Writer Agent 구현체는 특정 기업이나 특정 리포트 케이스에 종속되면 안 된다.

필수 원칙은 다음과 같다.

```text
1. 기업명, run_key, 기준일은 strategy_report.json 또는 입력 경로에서 동적으로 읽는다.
2. 매출, 마진, EPS, 주가, 거래량, 상대강도 등 모든 수치는 입력 JSON/CSV/chart_manifest에서 읽는다.
3. 제품명, 촉매, 리스크, 경쟁 환경은 Strategy Agent 결과에 존재할 때만 사용한다.
4. 특정 기업 전용 문장, 특정 제품 전용 문장, 특정 수치 전용 문장을 Python 코드에 하드코딩하지 않는다.
5. 차트 선택은 특정 figure_id 고정 목록이 아니라 chart_manifest의 section, asset, interpretation metadata를 기준으로 수행한다.
6. 샘플 문서의 특정 기업 관련 내용은 테스트 fixture 또는 예시일 뿐이며 자동화 로직의 기본값이 아니다.
```

---

## 1. 전체 파이프라인 내 Writer Agent 위치

Writer Agent는 아래 파이프라인의 마지막 리포트 생성 단계에 위치한다.

```text
Financial Agent
Y-Finance Agent
News Agent
Competitor Agent
        ↓
Strategy Agent
        ↓
Visualization Agent
        ↓
Writer Agent
        ↓
LaTeX Renderer
        ↓
final_report.pdf
```

Writer Agent는 분석을 새로 수행하는 에이전트가 아니다.  
이미 생성된 분석 결과와 시각화를 바탕으로 **최종 리포트 문서화**를 담당한다.

---

## 2. 입력 파일 경로

Writer Agent는 `{run_key}`를 단일 실행 키로 사용해 아래 파일들을 입력으로 사용한다. 기본 실행에서는 `--run-key`만 전달하면 표준 경로를 자동으로 해석하고, 특정 파일 교체가 필요할 때만 개별 path argument를 사용한다.

### 2.1 Strategy Agent 입력

```text
/home/agent2/Financial_Agent_Final/Output_total/Strategy/{run_key}/strategy_report.json
/home/agent2/Financial_Agent_Final/Output_total/Strategy/{run_key}/strategy_report.md
```

### 2.2 DART Financial 입력

```text
/home/agent2/Financial_Agent_Final/Output_total/Financial/{run_key}/dart_main.json
/home/agent2/Financial_Agent_Final/Output_total/Financial/{run_key}/dart_lightweight.json
```

### 2.3 Market / Y-Finance 입력

```text
/home/agent2/Financial_Agent_Final/Output_total/Y_Finance/{run_key}/market_full_dataset.csv
fallback: /home/agent2/Financial_Agent_Final/Output_total/Y_Finance/market_full_dataset.csv
```

### 2.4 Visualization Agent 출력 입력

Visualization Agent는 아래 파일들을 생성한다고 가정한다.

```text
/home/agent2/Financial_Agent_Final/Output_total/Visualization/{run_key}/chart_manifest.json
/home/agent2/Financial_Agent_Final/Output_total/Visualization/{run_key}/data_quality_report.json
/home/agent2/Financial_Agent_Final/Output_total/Visualization/{run_key}/figures/*.pdf
/home/agent2/Financial_Agent_Final/Output_total/Visualization/{run_key}/figures/*.png
```

---

## 3. 차트 선택 원칙

Writer Agent는 특정 기업이나 특정 figure_id에 고정된 차트 목록을 사용하지 않는다.

Visualization Agent가 생성한 `chart_manifest.json`을 후보 풀로 사용하고, Strategy Agent의 투자 논리를 가장 직접적으로 보조하는 차트를 선택한다.

차트 선택은 다음 우선순위를 따른다.

```text
1. Strategy Agent의 final_recommendation, investment_thesis, financial_view, market_price_view, catalyst_view, risk_view와 직접 연결되는 차트
2. chart_manifest.json에 analyst_takeaway, chart_insights, interpretation_limit, data_limitations가 충분히 제공된 차트
3. 증권사 리포트 독자가 투자 논리를 이해하는 데 도움이 되는 investor-facing 차트
4. 내부 검증용 또는 근거 개수 집계형 차트보다 실제 재무/시장/경쟁/리스크 데이터를 보여주는 차트
5. 사용 가능한 차트가 부족할 경우 deterministic selector가 보수적으로 fallback 선택
```

Writer Agent 내부 LLM Writer의 역할은 아래와 같다.

```text
1. chart_manifest 후보를 읽고 최대 2개의 핵심 차트를 선택한다.
2. 선택 이유를 Strategy Agent의 투자 판단과 연결한다.
3. chart_manifest의 interpretation_limit와 data_limitations를 유지한다.
4. 차트 해석을 애널리스트 코멘트로 확장한다.
5. LLM 사용이 불가능한 환경에서는 deterministic selector 결과를 사용한다.
```

각 차트의 해석은 chart_manifest에 명시된 허용 범위 안에서만 작성한다.

```text
- chart_insights: 차트에서 관찰되는 정량/정성 신호
- analyst_takeaway: 투자 판단과 연결되는 해석
- interpretation_limit: 해석 가능 범위
- data_limitations: 기간, 기준, 산식, 데이터 한계
```

금지 원칙은 다음과 같다.

```text
- chart_manifest에 없는 차트 사용 금지
- 내부 evidence map을 투자자용 핵심 차트로 우선 사용 금지
- 가격 신호가 펀더멘털 개선을 직접 증명한다고 단정 금지
- 특정 지표가 없는데 OPM, ROE, PER, PBR 등 미제공 지표를 임의 생성 금지
- 기간 기준이 다른 재무 수치를 직접 YoY 개선으로 단정 금지
```

---

## 4. Writer Agent 페르소나

Writer Agent는 아래와 같은 증권사 리포트 애널리스트 페르소나를 따른다.

```text
당신은 한국 상장기업을 담당하는 전문 증권사 리서치 애널리스트이다.
업종별 핵심 지표와 리스크 구조를 Strategy Agent 입력 범위 안에서 해석하는 애널리스트로서,
투자자에게 제공할 수 있는 근거 기반 기업분석 리포트를 작성한다.

당신의 역할은 Strategy Agent, Financial Agent, Y-Finance Agent,
Visualization Agent의 구조화된 결과물을 바탕으로 전문적인 증권사 리포트 초안을 작성하는 것이다.

당신은 새로운 수치를 계산하지 않는다.
당신은 목표주가, 적정주가, 상승여력, 밸류에이션 멀티플을 임의로 생성하지 않는다.
당신은 제공된 데이터와 차트가 직접적으로 뒷받침하는 주장만 작성한다.

리포트 문체는 간결하고 전문적이어야 한다.
긍정 요인과 리스크를 균형 있게 제시해야 하며,
불확실성과 해석 제한을 명확히 드러내야 한다.
```

---

## 5. Writer Agent의 핵심 원칙

Writer Agent는 아래 원칙을 반드시 따른다.

```text
1. Strategy Agent의 최종 투자의견을 변경하지 않는다.
2. Strategy Agent의 투자의견은 Buy, Hold, Sell, Neutral 등 어떤 값이든 변경하지 않고 최종 리포트에 그대로 유지한다.
3. 새로운 목표주가를 생성하지 않는다.
4. 새로운 EPS, OPM, ROE, PER, PBR, DCF 값을 생성하지 않는다.
5. Visualization Agent가 제공한 chart_manifest.json에 없는 차트는 사용하지 않는다.
6. 모든 차트는 특정 Strategy Agent 필드와 연결되어야 한다.
7. 장식용 차트 사용을 금지한다.
8. 가격 신호와 펀더멘털 개선 사이의 직접 인과관계를 단정하지 않는다.
9. 2025 Q3 YTD와 2024 FY를 비교할 때는 반드시 기간 기준 차이 주석을 포함한다.
10. 리포트의 모든 핵심 수치와 주장은 source_trace.json에 근거를 남긴다.
```

---

## 6. Writer Agent가 생성해야 하는 최종 스키마

Writer Agent는 최종적으로 아래 JSON을 생성해야 한다.

```text
broker_report_contract_v1.json
```

이 JSON은 LaTeX Renderer가 최종 PDF를 만들 때 사용하는 핵심 입력이다.

---

## 7. broker_report_contract_v1.json 스키마

아래 구조를 기본 스키마로 구현한다.

```json
{
  "report_metadata": {
    "report_type": "Equity Research Draft",
    "company_name": "{company_name}",
    "base_date": "{base_date}",
    "language": "ko",
    "recommendation": "Hold",
    "target_price": "N/A",
    "valuation_status": "Valuation Agent not applied",
    "writer_agent_version": "1.0"
  },
  "cover_summary": {
    "headline": "",
    "one_line_view": "",
    "recommendation_rationale_short": "",
    "positive_signals": [],
    "negative_signals": [],
    "monitoring_points": []
  },
  "investment_view": {
    "final_recommendation": "",
    "investment_thesis": [
      {
        "title": "",
        "body": "",
        "source_fields": []
      }
    ],
    "recommendation_rationale": "",
    "not_buy_reason": "",
    "not_sell_reason": ""
  },
  "key_metrics_table": {
    "metrics": [
      {
        "metric_name": "Revenue",
        "value": "",
        "period": "",
        "interpretation": "",
        "source_field": ""
      },
      {
        "metric_name": "Contribution Margin",
        "value": "",
        "period": "",
        "interpretation": "",
        "source_field": ""
      },
      {
        "metric_name": "SG&A Margin",
        "value": "",
        "period": "",
        "interpretation": "",
        "source_field": ""
      },
      {
        "metric_name": "EPS",
        "value": "",
        "period": "",
        "interpretation": "",
        "source_field": ""
      }
    ]
  },
  "visual_report_blocks": [
    {
      "block_id": "market_price_signal",
      "section": "Market / Price View",
      "figure_id": "fig_market_price_signal",
      "figure_path": "",
      "figure_title": "Stock Price Graph with MA20/MA60, Volume Ratio, and Relative Strength",
      "caption": "",
      "analyst_takeaway": "",
      "linked_strategy_fields": [
        "market_price_view.price_trend",
        "market_price_view.volume",
        "market_price_view.relative_strength"
      ],
      "interpretation_limit": ""
    },
    {
      "block_id": "fundamental_margin_trend",
      "section": "Financial View",
      "figure_id": "fig_fundamental_margin_trend",
      "figure_path": "",
      "figure_title": "Contribution Margin and SG&A Margin Time-Series Fundamental Trend",
      "caption": "",
      "analyst_takeaway": "",
      "linked_strategy_fields": [
        "financial_view.profitability",
        "investment_thesis.thesis_1"
      ],
      "interpretation_limit": ""
    }
  ],
  "sections": {
    "investment_summary": {
      "title": "Investment Summary",
      "body": "",
      "key_points": []
    },
    "financial_view": {
      "title": "Financial View",
      "body": "",
      "subsections": {
        "revenue": "",
        "profitability": "",
        "cash_flow": "",
        "balance_sheet": ""
      },
      "linked_figures": [
        "fig_fundamental_margin_trend"
      ]
    },
    "market_price_view": {
      "title": "Market / Price View",
      "body": "",
      "subsections": {
        "price_trend": "",
        "volume": "",
        "relative_strength": "",
        "market_interpretation": ""
      },
      "linked_figures": [
        "fig_market_price_signal"
      ]
    },
    "catalyst_and_risk": {
      "title": "Catalyst & Risk",
      "positive_catalysts": [],
      "business_expansion": [],
      "risk_blocks": {
        "financial_risks": [],
        "regulatory_risks": [],
        "market_risks": [],
        "execution_risks": []
      }
    },
    "peer_positioning": {
      "title": "Peer / Competitor Positioning",
      "body": "",
      "target_relative_strength": [],
      "target_relative_weakness": []
    },
    "final_rationale": {
      "title": "Final Rationale",
      "body": ""
    }
  },
  "limitations": {
    "data_limitations": [],
    "interpretation_limitations": [],
    "monitoring_points": []
  },
  "source_trace": [
    {
      "claim": "",
      "source_file": "",
      "source_field": "",
      "used_in_section": ""
    }
  ],
  "layout_plan": {
    "page_1": [
      "cover_summary",
      "key_metrics_table",
      "positive_negative_signal_boxes"
    ],
    "page_2": [
      "visual_report_blocks"
    ],
    "page_3": [
      "financial_view",
      "market_price_view"
    ],
    "page_4": [
      "catalyst_and_risk",
      "peer_positioning"
    ],
    "appendix": [
      "limitations",
      "source_trace"
    ]
  },
  "validation_rules": {
    "recommendation_must_match_strategy": true,
    "target_price_allowed": false,
    "new_number_generation_allowed": false,
    "forbidden_terms_without_source": [
      "목표주가",
      "적정주가",
      "상승여력",
      "하락여력",
      "P/E Band",
      "P/B Band",
      "PER 밴드",
      "PBR 밴드",
      "OPM",
      "ROE",
      "DCF",
      "fair value",
      "upside",
      "downside"
    ],
    "basis_mismatch_warning_required": true,
    "price_signal_causality_warning_required": true
  }
}
```

---

## 8. 섹션별 입력 매핑 규칙

Writer Agent는 아래 매핑에 따라 Strategy Agent 결과를 리포트 섹션으로 재구성한다.

### 8.1 Investment Summary

사용 입력:

```text
strategy_report.final_recommendation
strategy_report.investment_thesis
strategy_report.key_strengths
strategy_report.key_risks
```

작성 목적:

```text
- 최종 투자의견 요약
- 핵심 긍정 요인
- 핵심 리스크
- 왜 해당 투자의견인지 요약
```

---

### 8.2 Financial View

사용 입력:

```text
strategy_report.financial_view
dart_main.metrics_by_key.revenue
dart_main.metrics_by_key.contribution_margin
dart_main.metrics_by_key.sga_margin
dart_main.metrics_by_key.eps
chart_manifest.fig_fundamental_margin_trend
```

작성 목적:

```text
- 매출 성장 흐름 설명
- 공헌이익률 개선 설명
- 판관비율 하락 설명
- EPS 해석 제한 설명
- 2025 Q3 YTD와 2024 FY 비교 한계 명시
```

필수 연결 시각화:

```text
Contribution Margin and SG&A Margin Time-Series Fundamental Trend
```

필수 주석:

```text
2025년 수치는 3분기 누적 기준이며, 2024년 연간 수치와 직접적인 YoY 비교에는 제한이 있다.
```

---

### 8.3 Market / Price View

사용 입력:

```text
strategy_report.market_price_view
chart_manifest.fig_market_price_signal
market_full_dataset.csv
```

작성 목적:

```text
- 주가 추세 설명
- MA20/MA60 대비 위치 설명
- 거래량 비율 설명
- 코스피 대비 상대강도 설명
- 가격 신호의 한계 설명
```

필수 연결 시각화:

```text
Stock Price Graph with MA20/MA60, Volume Ratio, and Relative Strength
```

필수 주석:

```text
주가와 거래량 신호는 시장 관심과 가격 흐름을 보여주는 지표이며, 펀더멘털 개선의 직접 증거로 단정할 수 없다.
```

---

### 8.4 Catalyst & Risk

사용 입력:

```text
strategy_report.catalyst_view
strategy_report.risk_view
strategy_report.limitations.monitoring_points
```

작성 목적:

```text
- Strategy Agent가 제시한 핵심 제품 또는 서비스의 상업화 모멘텀
- Strategy Agent가 제시한 디지털, 플랫폼, 연구개발 또는 신사업 모멘텀
- Strategy Agent가 제시한 지역 확장, 파트너십, 인허가 또는 경쟁 환경 변화
- Strategy Agent가 제시한 규제, 경쟁, 비용, 실행 리스크
```

---

### 8.5 Peer / Competitor Positioning

사용 입력:

```text
strategy_report.peer_competitor_positioning
```

작성 목적:

```text
- 경쟁사 대비 상대 강점
- 경쟁사 대비 상대 약점
- 경쟁사 비교 기반 투자 시사점
```

---

### 8.6 Final Rationale

사용 입력:

```text
strategy_report.final_rationale
strategy_report.cross_agent_consistency_check
```

작성 목적:

```text
- 왜 해당 투자의견인지 최종 정리
- 긍정 신호와 리스크를 균형 있게 종합
- 적극적 매수보다 보수적 관망이 적절하다는 논리 제시
```

---

## 9. Figure Selection Logic

Writer Agent는 모든 차트를 자동으로 넣지 않는다.  
아래 조건을 만족하는 차트만 사용한다.

```text
1. chart_manifest.json에 존재해야 한다.
2. figure_path 파일이 실제로 존재해야 한다.
3. linked_strategy_fields가 1개 이상 있어야 한다.
4. analyst_takeaway가 존재해야 한다.
5. interpretation_limit가 존재해야 한다.
6. 해당 차트가 특정 섹션의 주장을 직접 뒷받침해야 한다.
```

사용 금지 조건:

```text
1. 단순 장식용 차트
2. Strategy Agent 내용과 연결되지 않는 차트
3. 데이터 한계가 명시되지 않은 차트
4. 파일 경로가 존재하지 않는 차트
5. P/E, P/B, OPM, ROE처럼 현재 데이터에서 지원하지 않는 차트
```

---

## 10. source_trace.json 생성 규칙

Writer Agent는 최종 리포트의 핵심 문장과 수치에 대해 source trace를 생성해야 한다.

출력 파일:

```text
source_trace.json
```

예시:

```json
[
  {
    "claim": "공헌이익률 상승과 판관비율 하락은 수익성 구조 및 비용 효율성 개선 신호로 해석된다.",
    "source_file": "strategy_report.json",
    "source_field": "financial_view.profitability",
    "used_in_section": "Financial View"
  },
  {
    "claim": "주가는 단기 및 중기 이동평균선 위에 위치하나, 코스피 대비 상대강도는 약세다.",
    "source_file": "strategy_report.json",
    "source_field": "market_price_view.relative_strength",
    "used_in_section": "Market / Price View"
  }
]
```

source trace는 나중에 Validator가 숫자와 문장의 근거를 확인하는 데 사용한다.

---

## 11. 리포트 디자인 원칙

최종 리포트는 시각화 친화적이어야 한다.  
LaTeX 템플릿은 아래 디자인 원칙을 따른다.

```text
1. 첫 페이지는 요약 중심으로 구성한다.
2. 핵심 차트는 넓게 배치한다.
3. 각 차트 아래에는 Analyst Takeaway 박스를 넣는다.
4. 긍정 요인과 리스크 요인은 박스로 분리한다.
5. 긴 문단보다 표, 박스, 불릿을 적극 활용한다.
6. 한 문단은 2~4문장 이내로 유지한다.
7. 과도한 텍스트 나열을 피한다.
8. 데이터 한계는 Appendix 또는 Note 박스로 분리한다.
```

추천 페이지 구성:

```text
Page 1:
- 기업명
- 투자의견
- 목표주가 N/A
- 핵심 요약
- 핵심 긍정 요인
- 핵심 리스크
- 주요 지표 테이블

Page 2:
- 핵심 시각화 1
- 핵심 시각화 2
- 각 차트별 Analyst Takeaway

Page 3:
- Financial View
- Market / Price View

Page 4:
- Catalyst & Risk
- Peer / Competitor Positioning
- Final Rationale

Appendix:
- Limitations
- Source Trace
- Data Quality Note
```

---

## 12. LaTeX 렌더링 구조

Writer Agent는 LaTeX 코드를 직접 모두 생성하기보다, `broker_report_contract_v1.json`을 생성하고 Jinja2 기반 LaTeX 템플릿을 렌더링하는 구조를 사용한다.

```text
broker_report_contract_v1.json
        ↓
Jinja2 LaTeX Template
        ↓
main.tex
        ↓
latexmk -xelatex
        ↓
final_report.pdf
```

---

## 13. 추천 LaTeX 패키지

한국어 증권사 리포트 PDF 생성을 위해 아래 패키지를 사용한다.

```latex
\usepackage{kotex}
\usepackage{fontspec}
\usepackage{geometry}
\usepackage{graphicx}
\usepackage{xcolor}
\usepackage{booktabs}
\usepackage{tabularx}
\usepackage{array}
\usepackage{tcolorbox}
\usepackage{fancyhdr}
\usepackage{titlesec}
\usepackage{caption}
\usepackage{subcaption}
\usepackage{hyperref}
```

컴파일 엔진:

```text
XeLaTeX
```

컴파일 명령:

```bash
latexmk -xelatex -interaction=nonstopmode -halt-on-error main.tex
```

---

## 14. 구현 파일 구조

아래 구조로 구현한다.

```text
src/
└── Agent_Team/
    └── Writer_Agent/
        ├── writer_agent.py
        ├── report_contract_builder.py
        ├── figure_selector.py
        ├── source_trace_builder.py
        ├── latex_renderer.py
        ├── writer_validator.py
        ├── prompts/
        │   └── writer_agent_persona.md
        ├── templates/
        │   ├── broker_report_main.tex.j2
        │   └── sections/
        │       ├── cover_summary.tex.j2
        │       ├── key_charts.tex.j2
        │       ├── financial_view.tex.j2
        │       ├── market_price_view.tex.j2
        │       ├── catalyst_risk.tex.j2
        │       ├── peer_positioning.tex.j2
        │       └── appendix.tex.j2
        └── schemas/
            └── broker_report_contract_v1.schema.json
```

---

## 15. 출력 파일 구조

Writer Agent 실행 후 아래 파일들을 생성한다.

```text
/home/agent2/Financial_Agent_Final/Output_total/Writer/{run_key}/
├── broker_report_contract_v1.json
├── source_trace.json
├── writer_validation_report.json
├── main.tex
├── compile_log.txt
├── final_report.pdf
└── sections/
    ├── cover_summary.tex
    ├── key_charts.tex
    ├── financial_view.tex
    ├── market_price_view.tex
    ├── catalyst_risk.tex
    ├── peer_positioning.tex
    └── appendix.tex
```

---

## 16. CLI 실행 방식

아래 CLI 형태로 실행할 수 있게 구현한다. 표준 자동화 실행은 `{run_key}` 중심으로 수행한다.

```bash
python "/home/agent2/Financial_Agent_Final/src/Agent_Team/Writer Agent/writer_agent.py" \
  --run-key "{run_key}" \
  --render-format html \
  --embed-images true
```

개별 입력 파일을 표준 `{run_key}` 경로가 아닌 다른 위치에서 읽어야 할 때만 `--strategy-json`, `--dart-main`, `--chart-manifest`, `--output-dir` 등을 명시한다.

---

## 17. Writer Validator 규칙

Writer Agent 실행 후 `writer_validator.py`는 아래 항목을 검증한다.

```text
1. 최종 투자의견이 Strategy Agent의 final_recommendation.opinion과 일치하는지 확인
2. 목표주가가 N/A인지 확인
3. 새로운 수치가 생성되지 않았는지 확인
4. 금지 표현이 사용되지 않았는지 확인
5. chart_manifest.json에 없는 차트가 사용되지 않았는지 확인
6. figure_path 파일이 실제 존재하는지 확인
7. 모든 figure에 analyst_takeaway와 interpretation_limit가 있는지 확인
8. 2025 Q3 YTD와 2024 FY 비교 시 기간 기준 차이 주석이 포함되었는지 확인
9. 가격 신호가 펀더멘털 개선의 직접 증거라고 단정되지 않았는지 확인
10. source_trace.json이 비어 있지 않은지 확인
11. LaTeX 컴파일이 성공했는지 확인
```

---

## 18. 금지 표현

아래 표현은 명시적 데이터가 없으면 사용 금지한다.

```text
목표주가
적정주가
상승여력
하락여력
upside
downside
fair value
DCF
P/E Band
P/B Band
PER 밴드
PBR 밴드
OPM
ROE
영업이익률 개선
자기자본이익률 개선
저평가
고평가
강력 매수
매수 전환
목표주가 상향
실적 개선 확정
펀더멘털 개선이 주가 상승을 견인
```

---

## 19. 허용 표현

아래 표현은 현재 데이터 범위에서 사용 가능하다.

```text
투자의견 {recommendation}
보수적 관망
재무 개선 신호
수익성 구조 개선 신호
비용 효율성 개선 신호
공헌이익률 개선
판관비율 하락
시장 대비 상대 약세
가격 신호의 확인 강도 제한
거래 활성화 신호
기간 기준 차이로 해석 제한
펀더멘털과 가격 신호 간 직접 인과관계는 제한적
```

---

## 20. Fallback 처리

실행 중 일부 입력이 없을 경우 아래처럼 처리한다.

```text
1. strategy_report.json이 없으면 실행 중단
2. strategy_report.md가 없으면 경고 후 JSON만으로 진행
3. chart_manifest.json이 없으면 텍스트 중심 리포트 생성 후 경고 기록
4. 특정 figure 파일이 없으면 해당 차트 블록 제외 후 writer_validation_report.json에 기록
5. dart_main.json이 없으면 financial key metrics table 생성 생략
6. market_full_dataset.csv가 없으면 market chart 관련 설명을 chart_manifest 기반으로만 작성
7. LaTeX 컴파일 실패 시 main.tex와 compile_log.txt를 반드시 저장
8. 한국어 폰트 오류 발생 시 Noto Sans CJK 또는 시스템 기본 CJK 폰트로 fallback
```

---

## 21. writer_validation_report.json 예시

```json
{
  "status": "pass",
  "recommendation_consistency": "pass",
  "target_price_policy": "pass",
  "number_consistency": "pass",
  "forbidden_terms": "pass",
  "figure_assets": "pass",
  "chart_manifest_consistency": "pass",
  "basis_mismatch_warning": "pass",
  "price_signal_causality_warning": "pass",
  "latex_compile": "pass",
  "notes": []
}
```

실패 시 예시:

```json
{
  "status": "fail",
  "recommendation_consistency": "pass",
  "target_price_policy": "pass",
  "number_consistency": "pass",
  "forbidden_terms": "fail",
  "figure_assets": "pass",
  "chart_manifest_consistency": "pass",
  "basis_mismatch_warning": "pass",
  "price_signal_causality_warning": "fail",
  "latex_compile": "pass",
  "notes": [
    "금지 표현 'ROE'가 입력 근거 없이 사용됨",
    "가격 상승을 펀더멘털 개선의 직접 증거로 단정한 문장이 발견됨"
  ]
}
```

---

## 22. 테스트 요구사항

아래 테스트를 구현한다.

```text
1. test_contract_schema_valid
   - broker_report_contract_v1.json이 schema를 만족하는지 확인

2. test_recommendation_consistency
   - Strategy Agent의 투자의견이 최종 리포트에도 유지되는지 확인

3. test_forbidden_terms
   - 금지 표현이 근거 없이 사용되지 않았는지 확인

4. test_figure_manifest_mapping
   - 사용된 figure_id가 chart_manifest.json에 존재하는지 확인

5. test_figure_file_exists
   - figure_path 파일이 실제 존재하는지 확인

6. test_basis_mismatch_warning
   - 2025 Q3 YTD와 2024 FY 비교 시 주석이 포함되는지 확인

7. test_price_causality_warning
   - 가격 신호를 펀더멘털 개선의 직접 증거로 단정하지 않는지 확인

8. test_latex_compile
   - main.tex가 XeLaTeX로 컴파일되는지 확인
```

---

## 23. 구현 우선순위

Codex는 아래 순서로 구현한다.

```text
1. 입력 파일 로더 구현
2. chart_manifest 로더 구현
3. broker_report_contract_v1.schema.json 작성
4. report_contract_builder.py 구현
5. figure_selector.py 구현
6. source_trace_builder.py 구현
7. LaTeX Jinja2 템플릿 작성
8. latex_renderer.py 구현
9. writer_validator.py 구현
10. CLI 실행 진입점 구현
11. 테스트 코드 작성
```

---

## 24. 최종 성공 조건

Writer Agent 구현은 아래 조건을 만족해야 완료로 본다.

```text
1. broker_report_contract_v1.json 생성
2. source_trace.json 생성
3. writer_validation_report.json 생성
4. main.tex 생성
5. final_report.pdf 생성
6. 최종 투자의견 원문 유지
7. 목표주가 N/A 유지
8. 승인된 두 개의 시각화만 사용
9. 금지 표현 미사용
10. 2025 Q3 YTD와 2024 FY 비교 한계 주석 포함
11. 가격 신호와 펀더멘털 간 인과관계 제한 문구 포함
12. LaTeX 컴파일 성공
13. design_spec 포함
14. Recommendation Card 포함
15. Positive / Risk Signal Box 포함
16. 핵심 차트별 Analyst Takeaway Box 포함
17. Limitation Note Box 포함
```

---

---

# 26. 리포트 디자인 시스템 명세

Writer Agent는 단순히 텍스트를 작성하는 역할만 수행하지 않는다.  
최종 증권사 리포트의 **시각적 구조, 정보 위계, 차트 배치, 요약 박스, 리스크 박스, 표 구성**까지 설계해야 한다.

다만 Writer Agent가 직접 PDF 디자인을 렌더링하는 것이 아니라, 아래 구조를 따른다.

```text
Writer Agent
  → broker_report_contract_v1.json에 디자인 의사결정 포함
  → LaTeX Jinja2 템플릿에 전달
  → LaTeX Renderer가 최종 PDF 생성
```

즉, Writer Agent는 **디자인 설계자**이고, LaTeX Renderer는 **디자인 실행자**이다.

---

## 26.1 디자인 목표

최종 리포트는 다음 목표를 만족해야 한다.

```text
1. 실제 증권사 리포트처럼 보일 것
2. 첫 페이지에서 투자의견과 핵심 논리가 즉시 보일 것
3. 긴 문단보다 표, 박스, 차트 중심으로 정보를 전달할 것
4. 핵심 차트와 Analyst Takeaway를 강조할 것
5. 긍정 요인과 리스크 요인을 시각적으로 분리할 것
6. 데이터 한계와 해석 제한을 숨기지 않고 명확히 표시할 것
7. PDF 출력 시 가독성이 유지될 것
```

---

## 26.2 전체 디자인 콘셉트

리포트의 디자인 콘셉트는 다음과 같다.

```text
스타일: 전문 증권사 리서치 리포트
톤: 차분함, 신뢰감, 제도권 리서치 느낌
레이아웃: 시각화 중심, 요약 중심, 박스 기반
색상: Navy / Blue / Gray 기반
강조색: 긍정 신호는 Blue, 리스크는 Red 또는 Dark Gray
차트 배치: 본문 중간 삽입이 아니라 핵심 페이지에서 넓게 배치
텍스트 밀도: 낮게 유지
```

리포트는 블로그 글이나 일반 보고서처럼 보여서는 안 된다.  
기관투자자에게 전달되는 **Equity Research Draft** 느낌을 목표로 한다.

---

## 26.3 색상 팔레트

LaTeX 템플릿은 아래 색상 팔레트를 사용한다.

```latex
\definecolor{ReportNavy}{HTML}{0B1F3A}
\definecolor{ReportBlue}{HTML}{1F5AA6}
\definecolor{ReportLightBlue}{HTML}{EAF2FF}
\definecolor{ReportGray}{HTML}{F4F6F8}
\definecolor{ReportDarkGray}{HTML}{4A5568}
\definecolor{ReportText}{HTML}{1A202C}
\definecolor{ReportRiskRed}{HTML}{B42318}
\definecolor{ReportLightRed}{HTML}{FFF1F0}
\definecolor{ReportGreen}{HTML}{067647}
\definecolor{ReportLightGreen}{HTML}{ECFDF3}
```

사용 규칙:

```text
ReportNavy:
- 제목
- 헤더
- 투자의견 박스 상단
- 주요 구분선

ReportBlue:
- 긍정 신호
- 핵심 지표 강조
- 차트 제목

ReportGray:
- 표 배경
- Appendix 영역
- 보조 박스

ReportRiskRed:
- 리스크 제목
- 부정 신호
- 주의 문구

ReportLightBlue:
- Analyst Takeaway 박스 배경

ReportLightRed:
- Risk Box 배경
```

---

## 26.4 폰트 및 타이포그래피

한국어 PDF 생성을 위해 XeLaTeX 기반 폰트를 사용한다.

권장 폰트:

```text
본문: Noto Sans CJK KR
제목: Noto Sans CJK KR Bold
숫자/표: Noto Sans 또는 기본 sans-serif
```

LaTeX 설정 예시:

```latex
\usepackage{fontspec}
\usepackage{kotex}

\setmainfont{Noto Sans CJK KR}
\setsansfont{Noto Sans CJK KR}
```

폰트 크기 규칙:

```text
리포트 제목: 20~24pt
섹션 제목: 13~15pt
본문: 9.5~10.5pt
표 내용: 8.5~9.5pt
차트 캡션: 8.5~9pt
주석 / 데이터 한계: 8pt
```

문단 규칙:

```text
1. 한 문단은 2~4문장 이내
2. 한 문단은 6줄을 넘지 않도록 구성
3. 핵심 문장은 박스 또는 불릿으로 분리
4. 긴 설명은 Appendix로 이동
```

---

## 26.5 페이지 구성

최종 리포트는 기본적으로 4~6페이지 구성을 목표로 한다.

### Page 1: Cover / Investment Summary

목적:

```text
첫 페이지에서 투자의견, 핵심 요약, 긍정 요인, 리스크, 주요 지표를 한눈에 보여준다.
```

포함 요소:

```text
1. 상단 헤더
   - 리포트 제목
   - 기업명
   - 기준일
   - Report Type

2. 투자의견 카드
   - Recommendation: {recommendation}
   - Target Price: N/A
   - Valuation Status: Valuation Agent not applied

3. 핵심 요약 박스
   - 한 줄 요약
   - 투자의견 근거 요약

4. Positive Signals 박스
   - 재무 개선 신호
   - 공헌이익률 개선
   - 판관비율 하락
   - {key_product} / 신사업 모멘텀

5. Risk Signals 박스
   - EPS 해석 제한
   - 규제 리스크
   - 대체재 또는 경쟁 심화 가능성
   - 상대강도 약세

6. Key Metrics Table
   - Revenue
   - Contribution Margin
   - SG&A Margin
   - EPS
```

디자인 규칙:

```text
- 첫 페이지는 2-column summary layout 사용 가능
- 투자의견 카드는 가장 눈에 띄게 배치
- Positive Signals와 Risk Signals는 좌우 박스로 배치
- 핵심 지표 테이블은 첫 페이지 하단에 배치
```

---

### Page 2: Key Charts

목적:

```text
최종 리포트의 시각화 중심 페이지이다.
차트와 분석가 해석을 연결하여 투자 논리를 시각적으로 보여준다.
```

포함 차트:

```text
1. Stock Price Graph with MA20/MA60, Volume Ratio, and Relative Strength
2. Contribution Margin and SG&A Margin Time-Series Fundamental Trend
```

구성 방식:

```text
- 차트 1개당 full-width 또는 0.9\textwidth 이상 사용
- 각 차트 아래 Analyst Takeaway 박스 배치
- 각 차트 아래 Interpretation Limit 문구 배치
- 차트는 단순 장식이 아니라 Strategy Agent의 특정 필드와 연결되어야 함
```

차트별 박스 예시:

```text
[Analyst Takeaway]
주가는 단기·중기 이동평균선 위에 위치하나, 코스피 대비 상대강도는 약세로 가격 신호의 확인 강도는 제한적이다.

[Interpretation Limit]
가격 및 거래량 신호는 펀더멘털 개선의 직접 증거가 아니다.
```

---

### Page 3: Financial View / Market Price View

목적:

```text
차트에서 보여준 내용을 본문 분석으로 확장한다.
```

Financial View 구성:

```text
- Revenue
- Contribution Margin
- SG&A Margin
- EPS
- Cash Flow
- Balance Sheet
- 데이터 기준 차이 주석
```

Market Price View 구성:

```text
- Price Trend
- Volume
- Relative Strength
- Market Interpretation
- 가격 신호의 한계
```

디자인 규칙:

```text
- Financial View와 Market View는 두 개의 별도 섹션으로 분리
- 각 섹션은 1개 요약 박스 + 본문 2~3문단으로 구성
- 숫자는 본문에 묻지 말고 표 또는 강조 텍스트로 노출
```

---

### Page 4: Catalyst & Risk / Peer Positioning

목적:

```text
투자 판단의 긍정 요인과 리스크를 균형 있게 보여준다.
```

Catalyst 구성:

```text
- 핵심 제품 또는 서비스의 상업화 확대
- 디지털, 플랫폼 또는 기술 기반 성장 옵션
- 신사업, 연구개발 또는 파이프라인 확장
- 글로벌 사업 확장
```

Risk 구성:

```text
- Financial Risk
- Regulatory Risk
- Market Risk
- Execution Risk
```

Peer Positioning 구성:

```text
- 경쟁사 대비 상대 강점
- 경쟁사 대비 상대 약점
- Peer-based investment implication
```

디자인 규칙:

```text
- Catalyst는 Blue 계열 박스
- Risk는 Light Red 또는 Gray 계열 박스
- 리스크는 카테고리별로 분리
- Peer Positioning은 표 또는 2-column 비교 박스로 구성
```

---

### Page 5: Final Rationale / Appendix

목적:

```text
최종적으로 왜 해당 투자의견인지 정리하고, 데이터 한계와 해석 제한을 명확히 표시한다.
```

포함 요소:

```text
- 투자의견 근거?
- Not Buy Reason
- Not Sell Reason
- Monitoring Points
- Data Limitations
- Interpretation Limitations
- Source Trace Summary
```

디자인 규칙:

```text
- Final Rationale은 강조 박스로 구성
- Limitations는 Appendix 스타일로 작게 배치
- Source Trace는 상세 내용 전체가 아니라 요약 테이블로 표시
```

---

## 26.6 핵심 컴포넌트 디자인

LaTeX 템플릿은 아래 컴포넌트를 지원해야 한다.

### 26.6.1 Recommendation Card

목적:

```text
최종 투자의견을 가장 먼저 보여주는 카드
```

포함 필드:

```text
- Recommendation
- Target Price
- Valuation Status
- Base Date
```

디자인:

```text
- Navy 배경 또는 Navy 상단바
- Recommendation 값은 크게 표시
- 투자의견 색상은 Strategy Agent의 recommendation tone에 맞춰 일관되게 사용
- Target Price가 N/A인 경우 "Valuation Agent not applied"를 함께 표시
```

---

### 26.6.2 Summary Box

목적:

```text
한 줄 투자 판단과 핵심 근거를 압축적으로 보여준다.
```

포함 필드:

```text
- headline
- one_line_view
- recommendation_rationale_short
```

디자인:

```text
- LightBlue 배경
- 왼쪽에 굵은 제목
- 오른쪽에 간결한 요약 문장
```

---

### 26.6.3 Positive / Risk Signal Boxes

목적:

```text
긍정 요인과 리스크를 시각적으로 분리한다.
```

Positive Signal Box:

```text
- Blue 또는 LightBlue 계열
- 최대 4개 bullet
- 재무 개선, 사업 모멘텀 중심
```

Risk Signal Box:

```text
- LightRed 또는 Gray 계열
- 최대 4개 bullet
- 규제, 경쟁, 시장 상대 약세 중심
```

---

### 26.6.4 Key Metrics Table

목적:

```text
주요 재무 지표를 한눈에 보여준다.
```

필수 항목:

```text
- Revenue
- Contribution Margin
- SG&A Margin
- EPS
```

권장 컬럼:

```text
Metric | Period | Value | Interpretation
```

디자인:

```text
- booktabs 사용
- 헤더는 Navy 배경 또는 Bold 처리
- 숫자는 오른쪽 정렬
- 해석은 짧게 유지
```

---

### 26.6.5 Chart Block

목적:

```text
차트, 캡션, 분석가 해석, 해석 제한을 하나의 블록으로 묶는다.
```

구조:

```text
Figure Title
Chart Image
Caption
Analyst Takeaway Box
Interpretation Limit Note
```

LaTeX 구조 예시:

```latex
\begin{figure}[htbp]
\centering
\includegraphics[width=0.95\linewidth]{figures/example.pdf}
\caption{...}
\end{figure}

\begin{tcolorbox}[colback=ReportLightBlue, colframe=ReportBlue, title=Analyst Takeaway]
...
\end{tcolorbox}

{\footnotesize \textcolor{ReportDarkGray}{Note: ...}}
```

---

### 26.6.6 Risk Matrix / Risk Block

목적:

```text
리스크를 종류별로 구분하여 표시한다.
```

구성:

```text
Financial Risk
Regulatory Risk
Market Risk
Execution Risk
```

디자인:

```text
- 각 리스크 카테고리를 작은 박스로 분리
- 리스크 박스는 Red 또는 Gray 톤 사용
- 각 박스는 2~3개 bullet 이내
```

---

### 26.6.7 Limitation Note

목적:

```text
데이터 한계와 해석 제한을 리포트에 명시한다.
```

반드시 포함할 내용:

```text
- 2025년 수치는 3분기 누적 기준
- 2024년 수치는 연간 기준
- 두 기간의 직접적인 YoY 비교에는 제한 존재
- 가격 신호는 펀더멘털 개선의 직접 증거가 아님
```

디자인:

```text
- 작은 Gray box 또는 footnote 스타일
- 본문 흐름을 방해하지 않되 반드시 보이게 배치
```

---

## 26.7 LaTeX 레이아웃 규칙

LaTeX 문서 기본 설정:

```latex
\documentclass[10pt,a4paper]{article}

\usepackage[a4paper,margin=16mm]{geometry}
\usepackage{kotex}
\usepackage{fontspec}
\usepackage{graphicx}
\usepackage{xcolor}
\usepackage{booktabs}
\usepackage{tabularx}
\usepackage{array}
\usepackage{tcolorbox}
\usepackage{fancyhdr}
\usepackage{titlesec}
\usepackage{caption}
\usepackage{subcaption}
\usepackage{hyperref}
\usepackage{multicol}
```

권장 레이아웃:

```text
- A4 portrait
- margin 16mm
- 본문 10pt
- 첫 페이지는 summary 중심
- 핵심 차트 페이지는 full-width 차트 사용
- 표는 \textwidth 기준으로 맞춤
```

---

## 26.8 Section Title 스타일

섹션 제목은 증권사 리포트처럼 간결하게 유지한다.

예시:

```text
Investment Summary
Financial View
Market / Price View
Catalyst & Risk
Peer Positioning
Final Rationale
Appendix
```

LaTeX 예시:

```latex
\titleformat{\section}
  {\large\bfseries\color{ReportNavy}}
  {\thesection}
  {0.5em}
  {}
```

---

## 26.9 Header / Footer 디자인

Header:

```text
왼쪽: {company_name} Equity Research Draft
오른쪽: 기준일 또는 작성일
```

Footer:

```text
왼쪽: AI-generated research draft
가운데: page number
오른쪽: Not investment advice / Internal draft
```

LaTeX 예시:

```latex
\pagestyle{fancy}
\fancyhf{}
\fancyhead[L]{{company_name} Equity Research Draft}
\fancyhead[R]{{base_date}}
\fancyfoot[L]{AI-generated research draft}
\fancyfoot[C]{\thepage}
\fancyfoot[R]{Internal draft}
```

---

## 26.10 표 디자인 규칙

표는 실제 증권사 리포트처럼 간결해야 한다.

```text
1. booktabs 사용
2. 세로선 최소화
3. 숫자는 오른쪽 정렬
4. 해석 문장은 짧게 작성
5. 표 안에 긴 문장 금지
6. 단위는 헤더 또는 주석에 표시
```

예시:

```text
Metric | Period | Value | Comment
Revenue | 2025 Q3 YTD | 5,011억원 | 성장 모멘텀 유지
Contribution Margin | 2025 Q3 YTD | 93.64% | 수익성 구조 개선
SG&A Margin | 2025 Q3 YTD | 50.10% | 비용 효율성 개선
EPS | 2025 Q3 YTD | 2,353원 | 기간 기준 차이로 해석 제한
```

---

## 26.11 차트 디자인 규칙

Visualization Agent가 차트를 만들 때도 아래 디자인 원칙을 따르는 것이 좋지만, Writer Agent는 최소한 차트 배치를 아래 기준으로 처리해야 한다.

```text
1. 핵심 차트는 본문 폭의 90~100%로 삽입
2. 차트 제목은 캡션과 중복되지 않도록 조정
3. 차트 아래 Analyst Takeaway를 반드시 배치
4. 차트 아래 해석 제한 문구를 반드시 배치
5. 하나의 페이지에 차트가 너무 많지 않도록 제한
6. 메인 리포트에는 승인된 핵심 차트 2개만 사용
```

---

## 26.12 디자인 관련 broker_report_contract_v1.json 확장 필드

기존 `broker_report_contract_v1.json`에 아래 `design_spec` 필드를 추가한다.

```json
{
  "design_spec": {
    "theme": {
      "style": "professional_equity_research",
      "primary_color": "ReportNavy",
      "accent_color": "ReportBlue",
      "risk_color": "ReportRiskRed",
      "background_color": "white",
      "box_style": "subtle_colored_tcolorbox"
    },
    "typography": {
      "main_font": "Noto Sans CJK KR",
      "title_font": "Noto Sans CJK KR",
      "body_font_size": "10pt",
      "caption_font_size": "8.5pt"
    },
    "layout": {
      "paper_size": "A4",
      "orientation": "portrait",
      "margin": "16mm",
      "page_count_target": "4-6",
      "summary_page_columns": 2,
      "chart_width": "0.95\\linewidth"
    },
    "components": {
      "recommendation_card": true,
      "summary_box": true,
      "positive_signal_box": true,
      "risk_signal_box": true,
      "key_metrics_table": true,
      "chart_takeaway_box": true,
      "limitation_note_box": true,
      "risk_matrix": true
    }
  }
}
```

이 필드는 LaTeX Renderer가 어떤 템플릿과 컴포넌트를 사용할지 결정하는 기준으로 사용한다.

---

## 26.13 디자인 검증 규칙

Writer Validator는 디자인 관련 항목도 검증해야 한다.

```text
1. broker_report_contract_v1.json에 design_spec이 존재하는지 확인
2. recommendation_card가 포함되었는지 확인
3. key_metrics_table이 포함되었는지 확인
4. visual_report_blocks마다 analyst_takeaway가 있는지 확인
5. visual_report_blocks마다 interpretation_limit가 있는지 확인
6. limitation_note가 최소 1개 이상 포함되었는지 확인
7. page_1에 cover_summary와 key_metrics_table이 포함되었는지 확인
8. page_2에 visual_report_blocks가 포함되었는지 확인
9. 최종 LaTeX에 figure 환경과 tcolorbox가 포함되었는지 확인
10. 차트가 0.9\linewidth 이상으로 삽입되었는지 확인
```

---

## 26.14 Codex 구현 시 디자인 우선순위

Codex는 디자인 구현을 아래 순서로 진행한다.

```text
1. broker_report_contract_v1.json에 design_spec 필드 추가
2. LaTeX 색상 팔레트 정의
3. Recommendation Card 구현
4. Summary Box 구현
5. Positive / Risk Signal Boxes 구현
6. Key Metrics Table 구현
7. Chart Block + Analyst Takeaway Box 구현
8. Limitation Note Box 구현
9. Header / Footer 구현
10. Risk Matrix 구현
11. Appendix 디자인 구현
12. 디자인 검증 규칙 추가
```

---

## 26.15 디자인 성공 조건

디자인 구현은 아래 조건을 만족해야 한다.

```text
1. 첫 페이지에서 Strategy Agent의 투자의견이 즉시 보일 것
2. 목표주가 N/A와 Valuation Agent not applied가 명확히 보일 것
3. 긍정 요인과 리스크 요인이 시각적으로 분리될 것
4. 핵심 차트 2개가 넓게 배치될 것
5. 각 차트 아래 Analyst Takeaway가 있을 것
6. 각 차트 아래 Interpretation Limit가 있을 것
7. 주요 지표가 표로 정리될 것
8. 데이터 한계가 Note 또는 Appendix에 표시될 것
9. 텍스트 밀도가 과도하지 않을 것
10. 최종 PDF가 A4 기준으로 깨지지 않고 컴파일될 것
```

## 25. 핵심 주의사항

이번 Writer Agent는 “리포트를 예쁘게 쓰는 에이전트”가 아니라,  
**검증 가능한 입력을 바탕으로 증권사 리포트 형식의 최종 산출물을 생성하는 문서화 에이전트**이다.

따라서 다음 원칙을 반드시 지킨다.

```text
계산은 하지 않는다.
없는 수치를 만들지 않는다.
투자의견을 바꾸지 않는다.
차트를 장식으로 쓰지 않는다.
데이터 한계를 숨기지 않는다.
시각화와 문장을 반드시 연결한다.
최종 PDF는 LaTeX 기반으로 생성한다.
```
