# News Agent

통합 레포 기준 News 하위 에이전트입니다. News Agent는 `buy/sell/hold`를 판단하지 않고, 상위 레이어가 사용할 뉴스 분석 handoff를 생성합니다.

## 위치

```text
src/Agent_Team/News_Agent
```

## 전체 실행

`--phase`를 생략하면 전체가 실행됩니다.

```text
collect -> export -> analysis
```

```bash
cd /path/to/Financial_Agent

PYTHONPATH=src python -m Agent_Team.News_Agent.cli \
  --collect-date 2025-10-31 \
  --company-id 00878696 \
  --company-name SK바이오팜 \
  --ticker 326030.KS \
  --corp-code 00878696 \
  --granularity month
```

설치 후에는 아래 스크립트도 사용할 수 있습니다.

```bash
news-workflow ...
news-analysis-agent ...
```

## 단계별 실행

```bash
PYTHONPATH=src python -m Agent_Team.News_Agent.cli --phase collect ...
PYTHONPATH=src python -m Agent_Team.News_Agent.cli --phase export ...
PYTHONPATH=src python -m Agent_Team.News_Agent.cli --phase analysis ...
```

터미널에는 `tqdm` 기반 진행 상황이 표시됩니다.

기준일 직전 1년을 7일 단위로 조회한 뒤 동일 URL을 정리하고 전체 기사 후보의 스니펫을 확보합니다. 제목이 같아도 URL이 다른 기사는 이 단계에서 제거하지 않습니다. 제목·스니펫에서 대상기업을 확인할 수 없는 기사, '그룹' 표현이나 공시상 관계회사 이름이 포함된 기사, 스니펫 미확보 기사는 분석 후보에서 제외하고 사유를 보관합니다. 기업 관계는 해당 실행에 사용한 공시 원문을 기준으로 하며, 모든 기업명이나 해외법인의 한국어 통칭까지 인식하는 것은 아닙니다.

통과한 기사의 제목과 스니펫을 임베딩하여 같은 달력 주간의 유사 기사를 하나의 사건으로 묶습니다. 사건 대표 기사와 기업보고서 섹션 간 임베딩 유사도의 가중합으로 주별 최대 3건을 확정하며 교차 인코더는 사용하지 않습니다. 이 사건 전체를 기준일에 맞춘 12개 월 구간에 배치해 뉴스 분석에 시간순으로 제공합니다. 월별 2건 제한은 없으며, LLM 월별 요약은 재무·시장 보조자료에만 사용합니다. 여러 날짜의 기사가 한 사건으로 묶이면 날짜별 제목 한 건을 시간순 진행 내역으로 함께 제공합니다. 기사 페이지에서 짧은 발췌문을 얻으며, 전체 본문을 언어모델에 전달하지 않습니다.

선정 전 사건 집합은 기업 필터 이전(`news_events_prefilter`)과 이후(`news_events_all`)를 구분해 보관합니다. Random 실험은 제거할 처리에 따라 어느 집합에서 추출했는지 명시해야 합니다. 두 집합 모두 선정 전에 전체 스니펫을 확보하며, Random 선택 이후 스니펫을 다시 수집하지 않습니다. 이전의 선택 후 스니펫 추출 결과는 새 입력으로 재사용할 수 없습니다. 전처리 기록과 검증 범위는 `docs/news_preselection_snippets_20260913.md`에 정리했습니다.

기본 뉴스 선정에는 기업보고서의 여섯 섹션에 대한 유사도를 사용합니다. 매출·수주 0.30, 손익 0.20, 제품·서비스 0.15, 계약·연구개발 0.15, 원재료·생산설비 0.10, 사업 개요 0.10을 적용합니다. 각 섹션 내 최대 코사인 유사도를 [0,1]로 제한한 후 가중합하며, 대상기업과 비교기업에 동일하게 적용합니다. 가중치는 고정된 설계값이며 최적값으로 검증된 것은 아닙니다.

설정은 `configs/news_default.yaml`의 `scoring.section_weighting`에 있습니다. 여섯 섹션을 새로 읽어야 하므로 이전 뉴스 결과를 재사용하는 실행에서는 기존 선정 결과가 그대로 유지됩니다. 새 가중치를 평가할 때는 뉴스 선정과 기사 입력부터 새 실험 디렉토리에 생성해야 합니다. 처리 방식과 검증 범위는 `docs/news_section_weighting.md`를 참고하세요.

News Agent는 선정 기사 전체를 날짜·제목·스니펫·기사 ID·진행 내역과 함께 월별로 받습니다. Financial·YFinance는 같은 기사들로 생성한 월별 요약을 보조자료로 받습니다. 월 구간과 기사 보도일, 사건 발생일, 실적 대상 기간을 구분해 해석합니다. 뉴스 분석은 직접 기사의 `NEWS_RAW` ID에 연결하고, 재무·시장에서는 월별 요약 근거와 원문 기사를 구분합니다.

뉴스 주 자료는 `context_exports/month/selected_articles.json`, 재무·시장 보조자료는 같은 디렉토리의 `llm_period_summaries.json`입니다. 요약은 조건별 선정 기사에서 새로 만들거나 입력이 일치하는 결과만 재사용합니다. 뉴스 분석은 요약에 인용된 기사만으로 입력을 줄이지 않습니다. 처리와 검증 범위는 `docs/news_article_only.md`에 정리했습니다.

월별 요약은 `issues` 배열의 사건별 `summary`와 실제 인용한 `source_event_ids`로 구성합니다. 이 설명과 인용 ID가 재무·시장 보조자료에 그대로 전달됩니다. 같은 기사를 인용한 별개 사건도 합치지 않습니다. 저장 결과의 `input_event_ids`는 월별 입력 전체이며, `source_event_ids`는 실제 인용의 합집합이므로 서로 구분해야 합니다.

통합 실행은 월별 요약에 `gpt-5.6-luna`, 뉴스 분석에 `gpt-5.4`를 사용합니다. 요약은 12개 월 구간을 각각 호출하여 생성합니다. 직접 실행할 때는 `--llm-model gpt-5.6-luna --analysis-model gpt-5.4 --split-by-period`로 같은 구성을 지정할 수 있습니다. 통합 실행의 `--news-summary-model`은 요약에만 적용되며 뉴스 분석 모델을 변경하지 않습니다.

## Output 경로

통합 후 새 output은 반드시 아래에 생성합니다.

```text
Output_total/News/{run_key}/output
```

예시:

```text
Output_total/News/SK바이오팜_20251031/output
```

News Agent output:

```text
news_agent_input_payload.json
news_agent_llm_request.json
news_agent_handoff.json
news_agent_evidence_map.json
```

상위 레이어가 기본으로 읽을 파일:

```text
Output_total/News/{run_key}/final_report.json
```

## Cross-Domain 입력

News Agent handoff 단계는 Financial/YFinance 산출물을 함께 사용합니다. 통합 실행에서는 명시 경로를 권장합니다.

```bash
PYTHONPATH=src python -m Agent_Team.News_Agent.cli \
  --phase analysis \
  --collect-date 2025-10-31 \
  --company-id 00878696 \
  --company-name SK바이오팜 \
  --ticker 326030.KS \
  --corp-code 00878696 \
  --context-export-dir Output_total/News/SK바이오팜_20251031/context_exports \
  --dart-lightweight Output_total/Financial/SK바이오팜_20251031/dart_lightweight.json \
  --market-summary Output_total/Y_Finance/SK바이오팜_20251031/market_summary.json \
  --analysis-output-dir Output_total/News/SK바이오팜_20251031/output
```

## 최종 산출물 계약

`news_agent_handoff.json`:

- `usage`: News Agent LLM 토큰 사용량
- `output.analysis_blocks.news_only`
- `output.secondary_context_assessment`
- `output.evidence_map_path`

Financial/Market 보조자료는 `context_informed_interpretation`으로 사용합니다. 뉴스 사건의 발생 사실·출처를 바꾸지 않으면서 중요도·지속성·위험에 대한 해석을 보완할 수 있습니다. 쟁점별 평가에는 실제 연결한 근거와 `judgment_impact`를 기록하고, `overall_assessment`에는 이를 반영한 뉴스 종합 해석과 `context_ids`를 작성합니다. 뉴스만으로 확인되는 사실은 기존 `news_only`에 별도로 보존합니다. 발표된 실적과 예상 실적을 구분하며, 월별 요약의 수치를 활용하면 해당 요약을 직접 인용합니다.
재무자료의 대상 기간이 뉴스 발생일보다 앞서는 경우에는 해당 자료를 후행 사건의 효과를 확인하거나 반박하는 근거로 사용하지 않고, 사건 발생 전의 재무 상태를 설명하는 문맥으로만 사용합니다.

News Agent는 최종 투자 판단을 생성하지 않습니다.

뉴스 종합 해석은 핵심 사업 변화와 실제 반대 근거의 중요도를 비교하여 작성합니다. 기사 건수가 아니라 사업과의 연결·발생 여부·영향의 지속성을 근거로 판단하며, 반대 근거나 우열을 억지로 만들지 않습니다. 영향 규모 미확인을 이유로 확인된 호재·악재를 일반적인 불확실성으로 대체하지 않습니다. 보조자료를 사용하면 어떤 관측이 해당 사건의 해석을 바꾸거나 유지시켰는지 종합 문장과 근거 참조에 반영합니다. 같은 지시를 모든 뉴스 선정 조건에 적용하며 별도 점수 규칙이나 의미 판정 후처리는 사용하지 않습니다.
