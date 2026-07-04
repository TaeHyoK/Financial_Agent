# Strategy Agent 구현 가이드라인

## 0. 목적

이 문서는 Codex가 `Strategy Agent`를 구현할 때 참고할 개발 지침이다.

현재 프로젝트에서는 이미 다음 두 종류의 입력이 생성되어 있다.

1. **Target 기업의 SY 검증 완료 output 3개**
   - Financial final report
   - News final report
   - YFinance final report

2. **Competitor Agent의 경쟁사별 summary report N개**
   - `competitor_summary_report.json`
   - 경쟁사 수에 따라 N개 입력 가능
   - 현재는 2개지만 고정값으로 구현하면 안 됨

Strategy Agent의 목표는 위 입력을 바탕으로 target 기업에 대한 최종 투자 의견을 생성하는 것이다.

최종 output은 다음을 포함해야 한다.

```text
Buy / Hold / Sell
최종 판단 요약
판단 근거
Target 기업의 주요 강점
Target 기업의 주요 리스크
경쟁사 대비 비교 요약
판단 한계 및 주의사항
```

---

## 1. 현재 선택한 구현 방향

이번 Strategy Agent 구현에서는 **선택지 A: 현재 Competitor output 그대로 읽기**를 사용한다.

즉, 별도의 `competitor_handoff.json`을 만들지 않고, Strategy Agent가 아래 파일들을 직접 읽는다.

```text
Output_total/Competitor/{competitor_run_key}/competitor_summary_report.json
```

경쟁사 수는 고정하지 않는다.

```text
competitor_summary_report.json 1개 가능
competitor_summary_report.json 2개 가능
competitor_summary_report.json N개 가능
```

---

## 2. Strategy Agent의 핵심 역할

Strategy Agent는 다음 역할만 수행한다.

```text
1. Target 기업의 Financial / News / YFinance output을 읽는다.
2. Competitor summary report N개를 읽는다.
3. Target 기업의 자체 분석 내용을 구조화한다.
4. 경쟁사 summary들을 보조 context로 정리한다.
5. Content Planner를 통해 최종 보고서의 판단 재료를 정리한다.
6. Decision Agent를 통해 Buy / Hold / Sell 중 하나를 결정한다.
7. 최종 strategy_report.json과 strategy_report.md를 저장한다.
```

Strategy Agent는 다음을 하지 않는다.

```text
- Competitor Agent를 다시 실행하지 않음
- 경쟁사 raw Financial / News / YFinance output을 직접 읽지 않음
- 새로운 재무 데이터나 주가 데이터를 수집하지 않음
- 없는 수치를 만들어내지 않음
- 경쟁사 수를 2개로 하드코딩하지 않음
```

---

## 3. 입력 구조

## 3.1 Target 기업 입력

Target 기업 입력은 항상 3개로 고정된다.

```text
--target-financial
--target-news
--target-yfinance
```

예시:

```text
Output_total/Financial/SK바이오팜_20251031/final_report.json
Output_total/News/SK바이오팜_20251031/final_report.json
Output_total/Y_Finance/SK바이오팜_20251031/final_report.json
```

### Target Financial report에서 특히 반영해야 할 caution

SK바이오팜 Financial final report에는 다음과 같은 중요한 주의사항이 있다.

```text
2025 Q3 YTD와 2024 ANNUAL FULL_YEAR는 집계 기준이 다르므로 동일 기간 YoY로 단정하면 안 됨
News 촉매는 재무 수치의 직접 증거가 아님
주가 상승만으로 펀더멘털 개선을 주장하면 안 됨
시장 상대성과 혼재 시 가격 확인 강도를 낮춰야 함
```

Strategy Agent는 이런 caution을 `decision_constraints`와 `limitations`에 반드시 반영해야 한다.

---

## 3.2 Competitor 입력

Competitor 입력은 N개로 유동적이다.

CLI에서는 같은 옵션을 여러 번 받을 수 있게 구현한다.

```text
--competitor-report path/to/competitor_summary_report.json
--competitor-report path/to/competitor_summary_report.json
--competitor-report path/to/competitor_summary_report.json
```

예시:

```text
Output_total/Competitor/더블유에스아이_20251031/competitor_summary_report.json
Output_total/Competitor/위더스제약_20251031/competitor_summary_report.json
```

각 competitor report는 아래 필드를 가진다고 가정한다.

```json
{
  "agent_name": "Competitor Agent",
  "company": {
    "company_name": "...",
    "run_key": "...",
    "ticker": "...",
    "as_of_date": "..."
  },
  "summary": "...",
  "strengths": ["..."],
  "risks": ["..."],
  "data_gaps": []
}
```

---

## 4. 추천 파일 구조

최소 구현 파일 구조는 다음과 같다.

```text
src/Strategy_Agent/
  __init__.py
  agent.py
  cli.py
  prompts/
    content_planner.md
    decision_agent.md
```

각 파일의 역할:

| 파일 | 역할 |
|---|---|
| `agent.py` | 입력 로드, LLM 호출, output 저장 등 핵심 실행 로직 |
| `cli.py` | CLI 실행 진입점 |
| `prompts/content_planner.md` | Content Planner LLM prompt |
| `prompts/decision_agent.md` | Buy/Hold/Sell 판단 LLM prompt |
| `__init__.py` | 패키지 초기화 |

복잡한 `scorer.py`, `aggregator.py`, `ranker.py`는 만들지 않는다.

---

## 5. 전체 실행 흐름

```text
Target Financial final report
Target News final report
Target YFinance final report
Competitor summary reports N개
        ↓
strategy_input_bundle.json 저장
        ↓
Content Planner LLM 호출
        ↓
strategy_content_plan.json 저장
        ↓
Strategy Decision Agent LLM 호출
        ↓
strategy_report.json 저장
        ↓
strategy_report.md 저장
```

---

## 6. Output 저장 위치

기본 output 위치는 다음과 같다.

```text
Output_total/Strategy/{target_run_key}/
  strategy_input_bundle.json
  strategy_content_plan.json
  strategy_report.json
  strategy_report.md
```

각 파일 역할:

| 파일 | 역할 |
|---|---|
| `strategy_input_bundle.json` | Strategy Agent가 실제로 읽은 입력 전체 기록 |
| `strategy_content_plan.json` | Content Planner 결과 |
| `strategy_report.json` | 최종 Buy/Hold/Sell 판단 결과 |
| `strategy_report.md` | 사람이 읽는 최종 투자 리포트 |

---

## 7. Step 1: Strategy Input Bundle 생성

먼저 모든 입력을 하나의 bundle로 묶는다.

### 7.1 Input Bundle Schema

```json
{
  "agent_name": "Strategy Agent",
  "target_company": {
    "company_name": "SK바이오팜",
    "run_key": "SK바이오팜_20251031",
    "as_of_date": "2025-10-31",
    "ticker": "326030.KS"
  },
  "target_reports": {
    "financial": {},
    "news": {},
    "yfinance": {}
  },
  "competitor_reports": [
    {
      "company_name": "더블유에스아이",
      "run_key": "더블유에스아이_20251031",
      "summary": "...",
      "strengths": ["..."],
      "risks": ["..."],
      "data_gaps": []
    }
  ],
  "input_metadata": {
    "competitor_count": 1,
    "created_at": "..."
  }
}
```

### 7.2 Competitor N개 처리 규칙

```python
competitor_reports = []

for path in competitor_report_paths:
    report = load_json(path)
    competitor_reports.append({
        "company_name": report["company"]["company_name"],
        "run_key": report["company"]["run_key"],
        "summary": report["summary"],
        "strengths": report.get("strengths", []),
        "risks": report.get("risks", []),
        "data_gaps": report.get("data_gaps", []),
        "source_path": str(path),
    })
```

하드코딩 금지:

```text
competitor_1
competitor_2
peer_a
peer_b
```

반드시 list 기반으로 처리한다.

---

## 8. Step 2: Content Planner

Content Planner는 최종 투자 판단을 내리지 않는다.

### 8.1 Content Planner의 역할

```text
- Target 기업의 핵심 financial/news/market 내용을 정리
- Target 기업의 주요 강점과 리스크 후보를 정리
- 경쟁사별 summary, strengths, risks를 정리
- Target과 경쟁사 비교에서 봐야 할 핵심 포인트 정리
- 최종 Decision Agent가 사용할 판단 재료를 구조화
```

### 8.2 Content Planner가 하지 않는 일

```text
- Buy / Hold / Sell 판단 금지
- 점수화 금지
- 새로운 사실 생성 금지
- 경쟁사 report에 없는 내용 추가 금지
```

### 8.3 Content Planner Output Schema

```json
{
  "target_company": "SK바이오팜",
  "target_core_summary": "...",
  "target_strength_candidates": ["..."],
  "target_risk_candidates": ["..."],
  "competitor_context": [
    {
      "company_name": "더블유에스아이",
      "summary": "...",
      "strengths": ["..."],
      "risks": ["..."]
    }
  ],
  "comparison_points": {
    "target_possible_advantages": ["..."],
    "target_possible_disadvantages": ["..."],
    "mixed_or_uncertain_points": ["..."]
  },
  "decision_constraints": ["..."],
  "report_outline": [
    "1. Investment Summary",
    "2. Target Company Analysis",
    "3. Competitor Context",
    "4. Key Strengths",
    "5. Key Risks",
    "6. Final Recommendation"
  ]
}
```

---

## 9. Content Planner Prompt 지침

`prompts/content_planner.md`에 아래 내용을 기반으로 prompt를 작성한다.

```text
You are the Content Planner for a financial Strategy Agent.

Your task is not to make a final investment recommendation.
Your task is to organize the provided target company reports and competitor summaries into a structured content plan.

Inputs:
1. Target Financial final report
2. Target News final report
3. Target YFinance final report
4. Competitor summary reports N개

Rules:
- Do not generate Buy, Hold, or Sell.
- Do not score the company.
- Use only the provided inputs.
- Do not invent financial facts.
- Treat Financial/DART data as the primary anchor for financial claims.
- Treat News and YFinance as supporting context, not direct proof of financial performance.
- Competitor reports are supporting context.
- Keep competitor information separated by company.
- Extract decision constraints from warnings, cautions, data gaps, and reconciliation flags.
- Return valid JSON only.
```

---

## 10. Step 3: Strategy Decision Agent

Strategy Decision Agent는 Content Planner 결과를 바탕으로 최종 판단을 생성한다.

### 10.1 Persona

```text
You are a financial data analyst.
Your role is to evaluate the target company using Financial, News, YFinance, and competitor context.
You must define one final recommendation: Buy, Hold, or Sell.
```

### 10.2 Decision Agent 핵심 제약

```text
- Use only the strategy_input_bundle and strategy_content_plan.
- Do not invent new financial facts.
- Do not overstate news catalysts.
- Do not treat stock price movement as direct evidence of fundamentals.
- Competitor reports are supporting context, not primary evidence.
- Final recommendation must include rationale, risks, and limitations.
- If evidence is mixed, prefer Hold over aggressive Buy or Sell.
- If critical risks exist, do not output Buy unless the rationale clearly explains why the risks are manageable.
```

---

## 11. Buy / Hold / Sell 판단 규칙

점수화하지 않고 rule-based label 판단을 사용한다.

### 11.1 Buy 조건

아래 조건이 대부분 충족될 때만 `Buy`를 선택한다.

```text
- Financial output에서 개선 신호가 명확함
- News output에서 실적 또는 성장 catalyst가 존재함
- YFinance output에서 시장 반응이 최소 중립 이상
- 경쟁사 대비 target의 핵심 강점이 분명함
- critical risk가 없음
```

### 11.2 Hold 조건

아래 경우 `Hold`를 선택한다.

```text
- Financial은 긍정적이지만 News 또는 Market에서 불확실성이 있음
- 성장 catalyst는 있으나 경쟁사도 유사한 catalyst를 보유함
- 주가 흐름이 절대적으로는 양호하지만 시장 대비 상대성과가 약함
- 긍정 요인과 리스크가 혼재함
- 데이터 기간 차이, source 간 괴리, data gap이 존재함
```

### 11.3 Sell 조건

아래 조건이 강하게 나타날 때 `Sell`을 선택한다.

```text
- Financial output에서 수익성, 현금흐름, 부채 측면의 중대 리스크가 확인됨
- News catalyst보다 부정적 리스크가 더 강함
- YFinance output에서 시장 반응이 명확히 부정적임
- 경쟁사 대비 target의 차별적 강점이 부족함
- critical risk가 존재함
```

---

## 12. Strategy Report Output Schema

`strategy_report.json`은 아래 구조로 저장한다.

```json
{
  "agent_name": "Strategy Agent",
  "target_company": "SK바이오팜",
  "target_run_key": "SK바이오팜_20251031",
  "final_recommendation": "Buy | Hold | Sell",
  "recommendation_summary": "...",
  "decision_rationale": ["..."],
  "target_strengths": ["..."],
  "target_risks": ["..."],
  "competitor_comparison": [
    {
      "competitor": "더블유에스아이",
      "comparison_summary": "...",
      "competitor_strengths_considered": ["..."],
      "competitor_risks_considered": ["..."]
    }
  ],
  "investment_view": {
    "financial_view": "...",
    "news_view": "...",
    "market_view": "...",
    "competitor_view": "..."
  },
  "limitations": ["..."],
  "source_files": {
    "target_financial": "...",
    "target_news": "...",
    "target_yfinance": "...",
    "competitor_reports": ["..."]
  }
}
```

---

## 13. Strategy Report Markdown 구조

`strategy_report.md`는 사람이 읽기 좋은 형태로 저장한다.

```markdown
# Strategy Report

## 1. Final Recommendation

- Final Recommendation: Buy / Hold / Sell
- Summary: ...

## 2. Target Company Summary

...

## 3. Financial View

...

## 4. News / Catalyst View

...

## 5. Market View

...

## 6. Competitor Context

### Competitor: 더블유에스아이
- Summary: ...
- Strengths considered: ...
- Risks considered: ...

### Competitor: 위더스제약
- Summary: ...
- Strengths considered: ...
- Risks considered: ...

## 7. Key Strengths

...

## 8. Key Risks

...

## 9. Limitations

...
```

---

## 14. CLI 설계

CLI는 다음 입력을 받는다.

```bash
python -m src.Strategy_Agent.cli \
  --target-company-name "SK바이오팜" \
  --target-run-key "SK바이오팜_20251031" \
  --target-financial Output_total/Financial/SK바이오팜_20251031/final_report.json \
  --target-news Output_total/News/SK바이오팜_20251031/final_report.json \
  --target-yfinance Output_total/Y_Finance/SK바이오팜_20251031/final_report.json \
  --competitor-report Output_total/Competitor/더블유에스아이_20251031/competitor_summary_report.json \
  --competitor-report Output_total/Competitor/위더스제약_20251031/competitor_summary_report.json \
  --output-dir Output_total/Strategy/SK바이오팜_20251031
```

중요:

```text
--competitor-report는 action="append"로 구현한다.
```

---

## 15. agent.py 핵심 함수 형태

```python
def run_strategy_agent(
    *,
    target_company_name: str,
    target_run_key: str,
    target_financial_path: Path,
    target_news_path: Path,
    target_yfinance_path: Path,
    competitor_report_paths: list[Path],
    output_dir: Path,
    llm_provider: str = "auto",
    llm_model: str = "auto",
    llm_timeout: int = 120,
) -> dict:
    """Run Strategy Agent and write strategy outputs."""
```

내부 흐름:

```python
def run_strategy_agent(...):
    input_bundle = build_strategy_input_bundle(...)
    save_json(output_dir / "strategy_input_bundle.json", input_bundle)

    content_plan = run_content_planner(input_bundle, ...)
    save_json(output_dir / "strategy_content_plan.json", content_plan)

    strategy_report = run_decision_agent(input_bundle, content_plan, ...)
    validate_strategy_report(strategy_report)
    save_json(output_dir / "strategy_report.json", strategy_report)

    markdown = render_strategy_markdown(strategy_report)
    save_text(output_dir / "strategy_report.md", markdown)

    return strategy_report
```

---

## 16. Validation Rules

저장 전 아래 검증을 수행한다.

### 16.1 Input Bundle Validation

```text
1. Target Financial path exists
2. Target News path exists
3. Target YFinance path exists
4. competitor_report_paths 길이 >= 0
5. 각 competitor report JSON 파싱 가능
6. 각 competitor report에 summary, strengths, risks 존재
7. competitor report 수를 하드코딩하지 않음
```

### 16.2 Content Plan Validation

```text
1. target_core_summary 존재
2. competitor_context가 list 타입
3. comparison_points 존재
4. decision_constraints 존재
5. final_recommendation 필드가 없어야 함
```

### 16.3 Strategy Report Validation

```text
1. final_recommendation이 Buy/Hold/Sell 중 하나
2. recommendation_summary 존재
3. decision_rationale list 존재
4. target_strengths list 존재
5. target_risks list 존재
6. competitor_comparison list 존재
7. limitations list 존재
8. source_files 존재
```

---

## 17. LLM 사용 방식

기존 Competitor Agent의 LLM 호출 방식을 재사용해도 된다.

필요 조건:

```text
- OPENAI_API_KEY 또는 GOOGLE_API_KEY 사용
- llm_provider auto/openai/gemini 지원 가능
- response는 JSON만 반환하게 prompt에서 강제
- JSON parse 실패 시 명확한 error 발생
```

Content Planner와 Decision Agent는 각각 LLM을 한 번씩 호출한다.

```text
LLM call 1: Content Planner
LLM call 2: Strategy Decision Agent
```

---

## 18. 구현 순서

Codex는 아래 순서로 구현한다.

```text
Step 1. src/Strategy_Agent 디렉터리 생성 또는 기존 디렉터리 확인

Step 2. prompts/content_planner.md 작성
- Content Planner prompt 저장

Step 3. prompts/decision_agent.md 작성
- Strategy Decision prompt 저장

Step 4. agent.py 구현
- load_json
- save_json
- build_strategy_input_bundle
- run_content_planner
- run_decision_agent
- render_strategy_markdown
- validate_strategy_report
- run_strategy_agent

Step 5. cli.py 구현
- argparse 기반 CLI
- target 3개 path 입력
- competitor-report N개 입력
- output-dir 입력

Step 6. 기본 실행 테스트
- competitor report 2개 입력
- strategy_input_bundle.json 생성 확인
- strategy_content_plan.json 생성 확인
- strategy_report.json 생성 확인
- strategy_report.md 생성 확인

Step 7. N개 competitor 처리 테스트
- competitor report 1개
- competitor report 2개
- competitor report 3개 이상
- 빈 competitor list도 허용할지 정책 결정
```

---

## 19. Acceptance Criteria

구현 완료 기준은 다음과 같다.

```text
1. CLI로 Strategy Agent 실행 가능
2. Target Financial / News / YFinance 3개 report를 정상 로드
3. Competitor summary report N개를 정상 로드
4. competitor 개수를 하드코딩하지 않음
5. strategy_input_bundle.json 생성
6. strategy_content_plan.json 생성
7. strategy_report.json 생성
8. strategy_report.md 생성
9. final_recommendation이 Buy/Hold/Sell 중 하나
10. 최종 판단에 rationale, risks, limitations 포함
11. Competitor context는 보조 근거로만 사용
12. 없는 사실이나 새로운 수치를 생성하지 않도록 prompt 제약 포함
```

---

## 20. 최종 설계 요약

```text
Strategy Agent =
Target 기업의 SY-verified output 3개
+
Competitor summary report N개
        ↓
Content Planner
        ↓
Strategy Decision Agent
        ↓
Buy / Hold / Sell + 최종 투자 리포트
```

현재 구현 방향의 핵심 원칙:

```text
- Target output은 3개 고정이다.
- Competitor output은 N개로 유동적이다.
- competitor_handoff.json은 사용하지 않는다.
- 현재 존재하는 competitor_summary_report.json을 그대로 읽는다.
- 판단은 점수화하지 않고 rule-based label 판단으로 한다.
- Content Planner와 Decision Agent를 분리한다.
- Buy/Hold/Sell은 Decision Agent에서만 생성한다.
```
