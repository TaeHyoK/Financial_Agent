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
6. 샘플 문서의 {company_name} 관련 내용은 테스트 fixture 또는 예시일 뿐이며 자동화 로직의 기본값이 아니다.
```

---

## 프로젝트 전체 맥락

본 프로젝트는 복수의 금융 분석 Agent가 생성한 결과물을 종합하여 최종 증권사 리포트를 생성하는 Multi-Agent 기반 리포트 생성 시스템이다.

Writer Agent는 전체 파이프라인의 마지막 단계에 위치하며, upstream Agent들이 생성한 분석 결과와 시각화 결과를 바탕으로 최종 증권사 리포트 PDF를 생성한다.

Writer Agent는 새로운 분석을 수행하지 않는다.  
Writer Agent는 Strategy Agent의 투자 판단을 변경하지 않는다.  
Writer Agent는 Visualization Agent가 생성한 차트를 임의로 재해석하지 않는다.  
Writer Agent는 제공된 데이터와 chart_manifest.json, strategy_report.json에 근거한 문장만 작성한다.

전체 흐름은 다음과 같다.

Financial Agent는 DART 기반 재무 데이터를 추출하고, 매출, 공헌이익률, 판관비율, EPS 등 핵심 재무 지표를 제공한다.

Y-Finance / Market Agent는 주가, 이동평균선, 거래량 비율, 상대강도 등 시장 데이터를 제공한다.

News Agent는 사업 모멘텀, 규제 리스크, 경쟁 환경, 신사업 추진 관련 정보를 제공한다.

Competitor Agent는 경쟁사별 summary, 강점, 리스크를 구조화하여 제공한다.

Strategy Agent는 Financial, Market, News, Competitor 결과를 종합하여 최종 투자의견, 투자포인트, 리스크, 시장 해석, 경쟁사 포지셔닝, 최종 판단을 생성한다.

Visualization Agent는 현재 데이터로 직접 설명 가능한 핵심 차트를 생성하고, chart_manifest.json을 통해 차트별 제목, 파일 경로, 사용 섹션, analyst takeaway, interpretation limit을 제공한다.

Writer Agent는 Strategy Agent와 Visualization Agent의 결과를 연결하여 최종 증권사 리포트 형식으로 재구성한다.

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


최종 리포트 저장 경로는 /home/agent2/Financial_Agent_Final/Output_total/Writer 이다


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
```

---

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

## 26. 디자인
디자인 부분은 /home/agent2/Financial_Agent_Final/src/Agent_Team/Writer Agent/writer_agent_codex_instructions_ko_design_v2.md 파일을 읽도록 참고하여 증권사 최종 리포트를 만들어.
