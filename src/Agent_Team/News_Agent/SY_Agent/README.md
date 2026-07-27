# News SY Agent

News Agent handoff의 claim을 실제 evidence catalog와 연결해 다음 단계의 근거 사용 수준을 결정한다.

```text
handoff/evidence catalog 로드
  -> claim 추출
  -> 날짜·수치·evidence id 검사
  -> claim별 적용 evidence domain을 semantic batch에서 판정
  -> strong / context_only / exclude ledger 저장
```

- `strong`: 직접 근거가 있고 문장 범위가 근거와 일치
- `context_only`: 데이터 한계, 불확실성 또는 제한된 해석
- `exclude`: 입력에 없는 사실·수치·인과 또는 입력과 충돌하는 주장

상위 통합 블록에 속한다는 이유만으로 뉴스·재무·시장 세 도메인을 모두 강제하지 않는다. LLM이 문장별 적용 도메인을 선언하고 코드는 선택된 evidence의 실제 domain coverage를 검증한다.

다회차 질문·답변, revision brief, critic queue, News report 재작성은 수행하지 않는다.

주요 출력:

```text
sy_claim_validations.json
news_agent_verified_handoff.json
sy_audit_trace.json
```
