# Financial SY Agent

Financial SY Agent는 Financial Analyst 보고서를 다시 작성하는 에이전트가 아니다. 각 claim이 기준일 당시 DART 자료와 연결되는지 검사하고, 다음 단계에서 사용할 수 있는 근거 수준만 결정한다.

## 입력

- Financial Analyst report
- `dart_main.json`
- 선택적으로 `dart_master.json`

## 처리

```text
claim/evidence 추출
  -> evidence id, claim link, 공시일, 기간, 수치 검사
  -> 적격 claim 전체를 semantic batch로 평가
  -> strong / context_only / exclude ledger 생성
  -> exclude claim만 verified handoff에서 차단
```

숫자·기간·날짜·source ref 검사는 결정론적으로 수행한다. LLM은 해석 범위와 과장 여부만 평가한다. 원 보고서 문장, Buy/Hold/Sell, 목표주가를 생성하거나 수정하지 않는다.

## 출력

- claim별 `deterministic_checks`
- claim별 `evidence_use`
- 사용 가능한 `evidence_ids`
- 원문을 재작성하지 않은 verified Financial report

Review, Repair, 다회차 질문·답변, revision brief, 보고서 재작성은 수행하지 않는다.
