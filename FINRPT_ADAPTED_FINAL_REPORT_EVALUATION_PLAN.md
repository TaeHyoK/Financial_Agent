# FinRpt 기반 최종 리포트 평가 계획

## 1. 문서 목적

이 문서는 FinRpt의 equity research report 평가 방법을 현재 프로젝트에 적용하기 위한 구현 계획을 정의한다.

평가 대상은 Strategy JSON 자체가 아니라 사용자가 실제로 읽는 최종 `report.html`이다. Strategy 결과와 evidence card는 다음 용도로만 사용한다.

- Strategy 결과: 모델별 최종 리포트를 생성하기 위한 중간 산출물
- Evidence card: 최종 리포트의 사실성, 날짜, 단위와 비교 범위를 확인하기 위한 공통 근거
- Gate A/B: 최종 리포트 생성 전에 중간 산출물이 유효한지 확인하는 진입 조건
- Gate C와 HTML Validator: 최종 리포트가 평가 가능한 상태인지 확인하는 진입 조건

따라서 기존에 수행한 `gpt-5.4-mini`와 `gpt-5.4` Strategy 결과 비교는 예비 진단이며, FinRpt 기반 최종 리포트 평가는 아니다.

## 2. 확인한 FinRpt 평가 방법

FinRpt는 다음 11개 지표를 사용한다.

### 2.1 기본 지표

1. `CompletionRate`: 요구된 보고서를 생성한 비율
2. `Accuracy`: reference의 Buy/Sell 추천과 생성 추천의 일치율
3. `BERTScore`: reference 보고서와 생성 보고서의 의미 유사도
4. `ROUGE-L`: reference 보고서와 생성 보고서의 문자열·요약 유사도
5. `NumberRate`: reference 대비 생성 보고서의 숫자 토큰 수 비율

논문 설명과 달리 공식 코드의 `CompletionRate`는 일정 길이를 넘는 출력인지 확인하는 수준이며, `NumberRate`는 정규식으로 숫자 토큰 개수를 세고 후보가 reference보다 많으면 1로 제한한다. 현재 프로젝트에는 이 구현을 그대로 이식하지 않는다.

### 2.2 LLM 평가 축

1. `Financial Numeric`: 수치의 정확성과 재무 분석의 깊이
2. `News`: 뉴스 분석의 관련성과 포괄성
3. `Company & Market & Industry`: 기업, 시장, 산업에 대한 이해
4. `Invest`: 투자 추천이 충분하고 논리적인 분석에 근거하는지
5. `Risk`: 투자 위험을 충분히 분석했는지
6. `Writing`: 전체 일관성, 가독성과 논리성

FinRpt는 각 축을 별도 평가하고 후보 순서를 바꿔 두 번 판정한다. 같은 후보가 두 순서에서 모두 선택된 경우에만 Win으로 처리하며, 순서별 판정이 다르거나 한 번이라도 Tie이면 최종 Tie로 처리한다.

```text
Adjusted Win Rate = (Win + 0.5 * Tie) / (Win + Loss + Tie)
```

### 2.3 확인 기준

- 논문: <https://arxiv.org/html/2511.07322>
- AAAI 게재본: <https://ojs.aaai.org/index.php/AAAI/article/view/37014>
- 공식 저장소: <https://github.com/jinsong8/FinRpt>
- Pairwise 평가 코드: <https://github.com/jinsong8/FinRpt/blob/main/finrpt/benchmark/llm_judgment.py>
- 기본 지표 코드: <https://github.com/jinsong8/FinRpt/blob/main/finrpt/benchmark/eval_utils.py>
- 확인한 공식 저장소 commit: `55e0e8516245001ba5c45e6067db4739d6b1f038`

## 3. 우리 프로젝트의 적용 범위

| FinRpt 지표 | 적용 여부 | 현재 프로젝트의 정의 |
| --- | --- | --- |
| CompletionRate | 적용 | 최종 HTML 생성과 Gate A/B/C, HTML Validator를 모두 통과한 비율 |
| Accuracy | 최종 리포트 평가에는 미적용 | 실제 기업에 Buy/Hold/Sell reference label이 없으므로 계산하지 않음 |
| BERTScore | 미적용 | reference 보고서가 없고 공식 중국어 embedding을 한국어에 그대로 사용할 수 없음 |
| ROUGE-L | 미적용 | reference 보고서가 없으며 표현 유사도가 투자보고서 품질을 의미하지 않음 |
| NumberRate | 대체 | 숫자 개수가 아니라 숫자 근거 정밀도와 필수 수치 재현율을 사용 |
| Financial Numeric | 적용 | 수치 정확성과 재무 해석 깊이를 분리해서 평가 |
| News | 적용 | 사건 선택, 중요도, 재무 연결 수준과 과대해석 여부를 평가 |
| CMI | 수정 적용 | 산업 집계가 없으므로 `Company, Market & Selected Peer`로 한정 |
| Invest | 적용 | 추천과 긍정·부정 근거, 밸류에이션과 불확실성의 논리적 일관성 평가 |
| Risk | 적용 | 근거 있는 위험, 점검사항, risk와 data limitation의 구분 평가 |
| Writing | 적용 | 문장 일관성, 가독성, 논리성, 중복과 표·본문 역할 구분 평가 |

이 평가 결과는 `FinRpt score`가 아니라 `FinRpt-adapted final report evaluation`으로 명명한다. 원 benchmark와 데이터, reference 보고서와 일부 지표가 다르므로 FinRpt 논문 결과와 수치를 직접 비교하지 않는다.

## 4. 평가 범위와 해석 한계

본 평가는 **동일하게 수집된 데이터가 주어졌을 때 어느 Strategy 모델이 더 나은 최종 리포트를 만드는지** 비교한다.

평가 가능한 항목은 다음과 같다.

- 제공된 수치와 사실을 정확하게 사용했는가
- 뉴스와 재무·시장 정보를 적절한 중요도로 해석했는가
- 최종 Buy/Hold/Sell이 리포트 내부 근거와 논리적으로 일치하는가
- 위험과 데이터 한계를 구분했는가
- 최종 리포트가 읽기 쉽고 중복 없이 구성됐는가

다음 항목은 이 평가만으로 판단할 수 없다.

- upstream 수집기가 중요한 공시나 기사를 누락했는가
- 제공되지 않은 산업 자료가 실제로 필요했는가
- 추천 이후 실제 주가가 상승하거나 하락했는가
- HTML의 시각 디자인이 사용자에게 더 선호되는가

upstream 수집 coverage, 투자 성과 backtest와 시각적 사용성 평가는 별도 실험으로 분리한다.

## 5. 후보 생성 조건

Strategy 모델만 독립 변수로 유지해야 한다.

### 5.1 고정 조건

- 동일 기업명
- 동일 `selected_date`
- 동일 뉴스 기간
- 동일 비교기업
- 동일 upstream input 파일
- 동일 `strategy_compact_packet_v2.json` SHA-256
- 동일 코드 commit
- 동일 Strategy prompt와 response schema
- 동일 Writer 모델, prompt와 response schema
- 동일 deterministic Writer assembler와 validator

### 5.2 변경 조건

- Candidate A Strategy model: `gpt-5.4-mini`
- Candidate B Strategy model: `gpt-5.4`

모델명은 Judge 입력에 포함하지 않는다. 각 후보는 격리된 evaluation output directory에 저장한다.

기존 mini 최종 리포트를 재사용하려면 Writer model, prompt, schema, 코드 버전과 packet fingerprint가 새 후보와 동일하다는 것이 manifest로 확인되어야 한다. 확인할 수 없다면 두 후보 모두 다시 생성한다.

## 6. 최종 리포트 평가 대상 추출

Judge가 평가하는 후보 내용은 `writer_report_payload.json` 전체나 Strategy JSON이 아니다. `report.html`에서 사용자가 실제로 볼 수 있는 내용만 추출한 canonical visible report를 사용한다.

### 6.1 포함 항목

- 보고서 제목과 metadata의 표시값
- 각 section 제목
- 화면에 표시되는 문단
- 핵심 근거표의 column과 row
- risk monitoring table의 column과 row
- data limitation 문단

### 6.2 제외 항목

- `_claim_units`
- `_card_key`, `_basis_card_keys`
- `_strategy_interpretation`
- grounding reference ID
- provenance path
- execution ID와 cache metadata
- CSS, JavaScript와 숨김 HTML metadata
- 모델명

HTML extractor는 section과 table 구조를 보존한 JSON을 생성한다. Judge가 HTML markup이나 숨김 내부 정보를 평가하지 않도록 한다.

## 7. 평가 근거 Bundle

각 후보에는 해당 후보의 `strategy_compact_packet_v2.json`에서 해석을 제거한 `candidate_accessible_evidence`를 연결한다. 사실성, 근거 누락과 불가피한 정보 부재는 이 후보별 접근 가능 범위 안에서만 판정한다. 서로 다른 evidence scope를 의도적으로 비교하는 ablation에서 한쪽 packet만 공통 reference로 쓰면 다른 후보의 유효한 관찰을 근거 없음으로 오판할 수 있으므로, 양쪽의 candidate-neutral 관찰을 합친 evidence union도 함께 제공한다. 이 합집합에는 어느 후보에서 온 관찰인지 표시하지 않으며 전체 커버리지 비교에만 사용한다.

### 7.1 포함 필드

- `card_key`
- `domain`과 `label`
- `primary_observation`
- `reader_observation`
- 날짜, 기간과 단위
- `evidence_family`
- `observation_basis`
- `comparison_scope`와 실제 비교 대상
- `decision_use`
- News의 발생 사실과 `financial_link_status`
- `reader_limitations`
- 결정론적으로 확인된 필수 limitation category와 facts

### 7.2 제외 필드

- 후보 모델이 생성한 `strategy_interpretation`
- 후보 모델의 `investment_effect`와 `materiality`
- 후보별 recommendation bridge
- 후보별 risk summary
- secondary context의 장문 해석
- 원문 전체 기사와 DART 전체 문서

후보별 Strategy decision은 Gate B와 Gate C의 일관성 검사 및 오류 진단에만 사용한다. Pairwise Judge에게는 제공하지 않는다. Judge가 최종 리포트 대신 내부 작성 의도를 평가하는 것을 막기 위함이다.

현재 SK바이오팜 파일 기준으로 공통 card의 필요한 필드와 최종 report payload 두 개를 합친 크기는 약 16,559 tokens였다. 실제 visible report를 사용하면 이보다 작아질 수 있으며, rubric과 response schema를 포함해도 호출당 약 2만 tokens대로 예상된다. 모든 요청은 기존 `preflight_request`로 실제 크기를 다시 측정한다.

## 8. 결정론적 최종 리포트 지표

LLM Judge 전에 기존 Gate와 다음 지표를 실행한다.

### 8.1 Completion

```text
CompletionRate = 평가 가능한 최종 리포트 수 / 유효한 생성 시도 수
```

유효한 생성 시도는 공통 upstream packet 생성까지 완료된 company-date case다. 일시적인 transport 오류는 retry 후 terminal 결과로 판정한다. schema, Gate 또는 Writer validation 실패는 non-completion으로 집계한다.

한 후보만 completion에 실패하면 해당 case에는 정성 pairwise 판정을 실행하지 않는다. Completion 결과는 별도 지표로 유지하고 정성 Win/Loss에 임의로 합산하지 않는다.

### 8.2 숫자 Grounding

최종 visible report의 숫자를 다음 유형으로 추출한다.

- KRW, 원, 천원, 백만원, 억원
- 주가와 시가총액
- 비율, `%`와 `%p`
- valuation multiple `배`
- 날짜와 기간
- 투자기간

통화 단위는 KRW로 정규화하고, 비율과 multiple은 원래 단위를 유지한다. 표시용 반올림은 원본 card의 formatting precision 범위에서 허용한다. 날짜와 투자기간은 target metadata allowlist와 비교한다.

```text
numeric_grounding_precision
  = 근거 또는 허용 metadata와 일치하는 표시 숫자 수 / 전체 표시 숫자 수

required_numeric_recall
  = 최종 리포트에 정확히 반영된 필수 숫자 수 / 표시해야 할 필수 숫자 수
```

다음은 hard failure다.

- `unsupported_numeric_count > 0`
- point-in-time 위반 숫자 또는 날짜 존재
- 단위 변환 오류
- 시장 benchmark와 selected peer 비교 대상 혼동
- 필수 table 수치의 누락 또는 변경

### 8.3 기존 Validator 집계

- recommendation consistency
- required section과 table
- card coverage
- comparison scope preservation
- temporal meaning preservation
- product scope preservation
- required limitation coverage
- risk coverage와 Strategy meaning preservation
- 내부 metadata 비노출

Hard validation을 통과한 두 후보만 LLM pairwise 평가에 진입한다.

## 9. LLM Pairwise 평가 Rubric

### 9.1 Financial Numeric

평가 내용:

- 공통 evidence와 비교한 재무·시장·valuation 수치 정확성
- 기간, 단위와 비교 기준의 정확성
- 단순 나열을 넘어 수치가 투자 판단에 연결되는 깊이
- point-in-time 재무 입력과 시장가격 날짜 혼합을 적절히 설명했는지

수치의 사실성은 deterministic metric을 우선하며, Judge는 분석 깊이와 의미 연결을 중심으로 비교한다.

### 9.2 News

평가 내용:

- 중요한 사건을 선택했는지
- occurrence-only event를 실적 촉매로 과대해석하지 않았는지
- 기사와 실제 매출·손익 연결이 확인되지 않은 경우 한계를 유지했는지
- 긍정·부정 뉴스의 중요도를 균형 있게 반영했는지
- 뉴스가 투자 thesis 또는 risk와 자연스럽게 연결되는지

### 9.3 Company, Market & Selected Peer

평가 내용:

- 제품·서비스 매출 범위와 사업구조를 정확하게 설명했는지
- 절대 주가 흐름과 KOSPI 상대성과를 구분했는지
- selected peer 1개사를 산업 전체로 일반화하지 않았는지
- 비교기업 이름, 기간과 수치가 정확한지
- 회사, 시장과 비교기업 정보가 서로 모순되지 않는지

산업 aggregate evidence가 없으므로 산업 평균이나 시장점유율 분석의 누락을 감점하지 않는다.

### 9.4 Invest

평가 내용:

- 최종 Buy/Hold/Sell이 보고서에 제시된 evidence와 일치하는지
- 현재 가격, forward support, valuation counterweight와 residual uncertainty가 균형을 이루는지
- Hold가 단순 데이터 부족의 기본 선택으로 사용되지 않았는지
- Buy 또는 Sell이 독립적인 forward evidence에 의해 뒷받침되는지
- 추천과 thesis, 핵심 근거표, risk matrix가 같은 방향성을 유지하는지

### 9.5 Risk

평가 내용:

- 중요한 downside를 누락하지 않았는지
- 위험이 실제 evidence에 근거하는지
- 데이터 limitation을 사업·재무 risk로 중복 또는 확대하지 않았는지
- 위험별 monitoring point가 구체적이고 관찰 가능한지
- 긍정적 event의 실행·수익화 불확실성을 적절히 분리했는지

### 9.6 Writing

평가 내용:

- 문장과 문단의 논리적 연결
- 전문용어와 문구의 명확성
- 불필요한 반복과 장황함
- 표와 본문의 역할 구분
- 추천, 근거, 위험과 데이터 한계의 탐색 용이성
- 보고서 길이를 품질의 대리 지표로 사용하지 않음

시각적 배치, 색상과 반응형 UI는 이 축의 평가 범위가 아니다.

## 10. Pairwise 판정 방식

### 10.1 Judge 응답 계약

각 축은 다음 필드를 strict schema로 반환한다.

```json
{
  "winner": "A | B | tie",
  "reason": "비교 근거를 설명하는 짧은 문장",
  "supporting_card_keys": [],
  "candidate_a_error_tags": [],
  "candidate_b_error_tags": []
}
```

Judge는 공통 bundle 이외의 외부 지식과 기준일 이후 정보를 사용하지 않는다. 길이와 문체 취향만으로 후보를 선택하지 않는다.

### 10.2 순서 교차

1. Call 1: Candidate A, Candidate B
2. Call 2: Candidate B, Candidate A
3. 두 응답을 실제 candidate identity로 다시 매핑
4. 동일 후보가 두 번 모두 이기면 Win/Loss
5. 판정 불일치, 한쪽 Tie 또는 schema error는 최종 Tie 또는 evaluation error로 분리

Schema error와 transport failure는 Tie로 숨기지 않는다. retry 후에도 실패하면 해당 axis를 `evaluation_error`로 기록하고 집계 denominator에서 제외하며 오류율을 별도로 공개한다.

## 11. 호출 수와 FinRpt 원형 검증

FinRpt 원형은 6개 축을 개별 호출하므로 보고서 한 쌍당 `6 axes * 2 orders = 12 calls`를 사용한다.

현재 프로젝트의 기본 실행은 다음과 같이 최적화한다.

- 한 호출에서 6개 축을 독립 object로 평가
- 순서를 바꿔 총 2회 호출
- strict response schema로 축 간 출력 누락 방지
- `LLM_RUN_ROLE=evaluation`으로 정상 14-call 파이프라인 집계에서 제외
- candidate pair, evidence bundle, prompt, schema와 judge model hash 기반 cache 사용

이 방식은 FinRpt 원형과 동일하지 않으므로 `batched FinRpt-adapted mode`로 명시한다.

초기 3개 company-date case에는 12-call axis-isolated 방식도 함께 실행한다. Batched mode와 axis-isolated mode의 축별 결과 일치율이 90% 미만이면 최종 모델 선택 실험에서는 12-call 방식을 사용한다.

## 12. Judge model과 편향 통제

Judge model은 CLI 인자로 교체 가능하게 구현한다.

- 가능하면 비교 후보에 포함되지 않은 별도 Judge model 사용
- 별도 모델을 사용할 수 없으면 `gpt-5.4` 사용
- 모델명, token 수, 생성시간과 내부 fingerprint를 Judge에게 숨김
- A/B 순서 교차 필수
- temperature를 지원하는 경우 0으로 고정
- human calibration을 통과하기 전에는 자동 Judge 결과만으로 모델을 변경하지 않음

`gpt-5.4`가 후보 중 하나이면서 Judge인 경우 self-preference 가능성이 남는다. 순서 교차와 blind label만으로 완전히 제거할 수 없으므로 사람 평가 결과를 함께 보고한다.

## 13. 평가 Dataset

### 13.1 Pilot

- 6~8개 company-date case
- 평가 pipeline과 error taxonomy 검증 목적
- Pilot 결과만으로 production 모델을 변경하지 않음

### 13.2 Model selection

- 최소 20개 company-date case
- 비금융 국내 상장사 범위 유지
- 업종, 시가총액, 수익성, valuation과 뉴스 밀도를 분산
- 흑자 성장기업, 안정기업, 적자기업, 고valuation, 저valuation, 뉴스 희소기업과 뉴스 집중기업 포함
- 동일 기업이 여러 날짜를 차지하는 경우 company 단위 cluster로 집계

`20개`는 통계적 우위를 자동 보장하는 수가 아니다. 회사 단위 bootstrap 신뢰구간을 계산하고 결론이 나지 않으면 표본을 추가한다.

### 13.3 Recommendation counterfactual

Buy/Hold/Sell 방향 대칭성 평가는 실제 최종 리포트 pairwise score에 섞지 않는다. 별도의 Strategy calibration 결과로 보고한다.

기존 `evaluate_recommendation_bias.py`는 이전 Planner 경로를 사용하므로, 실제 구현 시 현재 단일 Strategy Decision 구조에 맞춰 갱신한다.

## 14. 사람 평가 Calibration

- 무작위로 선택한 report pair의 20%, 최소 10개 pair 평가
- 팀원 3명이 모델명을 보지 않고 독립 평가
- LLM Judge와 동일한 6개 rubric 사용
- 축별 A/B/Tie 선택
- 사람 다수결과 LLM Judge 결과의 agreement 계산
- 사람 평가자 간 Fleiss' kappa 별도 보고

LLM과 사람 다수결 agreement가 80% 미만이면 자동 Judge 결과를 production 모델 선택의 단독 근거로 사용하지 않는다. rubric을 수정한 뒤 calibration을 다시 수행한다.

FinRpt 논문은 50개 pair에 대해 세 명의 senior financial analyst 다수결과 GPT-4o Judge를 비교하여 45/50, 90% agreement를 보고했다. 현재 프로젝트의 80% 기준은 FinRpt 결과를 재현했다는 의미가 아니라 자동 평가를 사용하기 위한 내부 최소 기준이다.

## 15. 집계와 모델 선택 규칙

### 15.1 보고 지표

- 후보별 CompletionRate
- hard validation failure count와 유형
- numeric grounding precision
- required numeric recall
- unsupported numeric count
- 축별 Win/Loss/Tie
- 축별 Adjusted Win Rate
- 회사별 macro Adjusted Win Rate
- 회사 단위 bootstrap 95% confidence interval
- Judge-human agreement
- Judge call input/output tokens와 latency
- 후보 생성 Strategy/Writer token과 latency

### 15.2 채택 조건

새 Strategy 모델을 더 낫다고 판단하려면 다음 조건을 모두 만족해야 한다.

1. terminal pipeline 기준 CompletionRate 100%
2. `unsupported_numeric_count = 0`
3. point-in-time과 comparison scope hard failure 0건
4. 전체 macro Adjusted Win Rate의 95% 신뢰구간 하한이 0.5 초과
5. Financial Numeric, Invest와 Risk 각 축의 Adjusted Win Rate가 0.5 이상
6. LLM Judge와 사람 다수결 agreement가 80% 이상
7. 기존 모델에만 통과하던 hard contract의 회귀가 없음

조건을 충족하지 못하면 결과를 `inconclusive` 또는 `regression`으로 기록한다. 품질 차이가 불확실한 경우에는 비용과 지연시간이 낮은 기존 모델을 유지한다.

품질 점수와 token·latency를 하나의 가중 합산 점수로 만들지 않는다. 품질 우위와 운영 비용을 별도 표로 제공한다.

## 16. 구현 구성

예상 모듈은 다음과 같다.

```text
src/orchestration/
├── final_report_evaluation_bundle.py
├── final_report_evaluation_metrics.py
├── final_report_pairwise_judge.py
├── final_report_evaluation_cli.py
├── prompts/
│   └── final_report_pairwise_judge.md
└── tests/
    ├── test_final_report_evaluation_bundle.py
    ├── test_final_report_evaluation_metrics.py
    └── test_final_report_pairwise_judge.py
```

`pyproject.toml`에는 다음 CLI를 추가한다.

```text
financial-report-evaluate = orchestration.final_report_evaluation_cli:main
```

예상 실행 형태:

```bash
financial-report-evaluate \
  --candidate-a /path/to/gpt-5.4-mini/output \
  --candidate-b /path/to/gpt-5.4/output \
  --common-strategy-packet /path/to/strategy_compact_packet_v2.json \
  --judge-model gpt-5.4 \
  --mode batched-pairwise \
  --execution-id strategy-model-final-report-comparison
```

## 17. 평가 산출물

```text
Output_total/Evaluation/Final_Report_Model_Comparison/{evaluation_id}/
├── experiment_manifest.json
├── common_evidence_bundle.json
├── candidate_a_visible_report.json
├── candidate_b_visible_report.json
├── deterministic_metrics.json
├── judgments/
│   ├── order_ab.json
│   └── order_ba.json
├── pairwise_result.json
├── evaluation_summary.json
├── evaluation_summary.md
├── llm_usage_manifest.jsonl
└── llm_usage_summary.json
```

`experiment_manifest.json`에는 다음 재현 정보를 기록한다.

- company, selected date와 peer
- upstream input hash
- Strategy packet hash
- candidate model과 Writer model
- code commit
- prompt와 schema hash
- Judge model과 평가 mode
- candidate output path
- 실행시각과 execution ID

## 18. 필수 테스트

### 18.1 Bundle 및 HTML 추출

- visible text와 table만 추출되는지
- hidden metadata와 model name이 제거되는지
- section과 row 순서가 유지되는지
- 두 후보의 공통 packet hash가 다르면 실행을 거부하는지

### 18.2 숫자 검증

- 원·백만원·억원 단위 변환
- `%`와 `%p` 구분
- valuation `배` 처리
- 허용 반올림과 잘못된 반올림 구분
- 근거 없는 소수와 큰 숫자 탐지
- 날짜와 투자기간 allowlist

### 18.3 Pairwise 판정

- A/B 순서 매핑
- 두 순서가 같은 후보를 선택할 때 Win 처리
- 판정 불일치와 Tie 처리
- schema error와 evaluation error 분리
- 축 누락 거부
- 외부 card key와 기준일 이후 근거 거부

### 18.4 집계

- Win/Loss/Tie와 Adjusted Win Rate 계산
- company macro average
- cluster bootstrap
- completion 실패 denominator
- evaluation role token 집계 분리

## 19. 현재 상태

- HTML visible-content extractor와 candidate-neutral evidence bundle builder가 구현되었다.
- strict-schema 6축 Judge, A/B·B/A 순서 교차, 보수적 Win/Loss/Tie reconciliation이 구현되었다.
- 여러 ablation suite를 입력받는 평가 CLI, 추천 변경률·반복 안정성 집계, 기업 cluster bootstrap 95% CI와 evaluation-role token telemetry가 구현되었다.
- SK바이오팜의 `full`, `no_sy`, `no_competitor`, `primary_only` 세 report pair에 대해 A/B·B/A 총 6회의 실제 Judge 판정이 완료되었다.
- 논문용 유효 실행은 `pilot_skbiopharm_20251031_judge_v4`이다. `v1`부터 `v3`은 근거 접근 범위 계약을 교정하는 과정에서 생성된 방법론 감사 artifact이므로 결과 집계에서 제외한다.
- 유효 실행의 Full 기준 Adjusted Win Rate는 `no_competitor` 0.583, `no_sy` 0.250, `primary_only` 0.750이며 세 조건 모두 추천 변경은 없었다.
- 현재 기업 cluster는 SK바이오팜 1개뿐이므로 95% CI는 `insufficient_company_clusters`, 반복은 1회뿐이므로 반복 안정성은 산출 불가 상태이다.
- 나머지 5개 기업의 실제 판정과 6개 기업 통합 CI는 아직 실행되지 않았다.

## 20. 작업 순서

1. 조건별 3회 반복 결과를 생성해 recommendation stability를 확인한다.
2. 나머지 5개 기업 suite를 같은 입력 계약으로 생성한다.
3. 6개 기업 suite를 한 evaluation에 입력해 기업 cluster bootstrap CI를 계산한다.
4. 숫자 grounding precision과 required numeric recall의 결정론적 지표는 별도 모듈로 추가한다.
