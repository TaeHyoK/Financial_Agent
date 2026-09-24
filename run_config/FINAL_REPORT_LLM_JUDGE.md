# 최종 보고서 LLM-as-a-Judge 실행 안내

> 이 평가는 실행을 마쳤다. 리포에는 코드와 테스트만 있고, 입력·요청·응답·결과 파일은 없다. 다시 돌리거나 결과를 보려면 이동용 번들을 리포 루트에 풀어야 한다.

최종 구현은 `run_config/final_report_llm_judge.py`다. FinRPT식 순서 교환 쌍대비교에 기업별 실제 애널리스트 보고서 본문을 비정답형 공통 reference로 추가한다.

## 고정 설계

- 기업 5개: 현대건설, 두산, BGF리테일, 아모레퍼시픽, SK바이오팜
- 생성 회차 3개: r01, r02, r03
- 비교 4개: Full vs Random news / No-subdata / No-peer / One-team
- 기준 3개: R1 핵심 이슈, R2 투자 논지, R3 전망·위험요인
- 각 기준에서 A/B와 B/A를 모두 평가
- 총 호출: `5 × 3 × 4 × 3 × 2 = 360`
- Judge: `gpt-5.6-terra`, `reasoning.effort=low`
- 응답: `A / B / C`; `C`는 명시적 동률
- 두 순서에서 동일한 실제 후보가 모두 선택된 경우에만 그 후보의 승리로 확정하고, 그 외의 모든 유효 조합은 Tie
- 집계: `(Win + 0.5 × Tie) / (Win + Loss + Tie)`
- API·형식 오류는 Tie로 바꾸지 않고 Error로 분리

Reference의 공식 투자의견과 목표주가는 준비 과정에서 마스킹한다. Reference는 정답이나 모범답안이 아니라 전문가가 중요하게 본 이슈를 보여주는 앵커다.

## 오프라인 준비

다음 명령은 API를 호출하지 않는다.

```bash
python run_config/final_report_llm_judge.py prepare
python run_config/final_report_llm_judge.py validate
```

기본 출력 디렉터리는 `evaluation/final_report_llm_judge`다. 준비 결과에는 다음 파일이 생긴다.

- `manifest.json`: 프로토콜·모델·해시·호출 수·토큰 추정치
- `requests_PREPARED_NOT_SUBMITTED.jsonl`: 동결한 360개 요청
- `task_audit.jsonl`: 익명 요청 ID와 실제 조건·순서·원천 파일의 대응표
- `status.json`: `prepared_not_run`, `paid_api_calls: 0`

## 유료 실행

유료 호출은 다음 두 확인 옵션을 동시에 줘야만 시작된다. 단순히 스크립트를 실행하거나 `run`만 지정하면 차단된다.

```bash
python run_config/final_report_llm_judge.py run \
  --execute-paid-api \
  --confirm-call-count 360 \
  --workers 4
```

일시적 API 오류 또는 유효하지 않은 JSON만 기본 최대 2회 재시도한다. 정상 A/B/C 결과는 재평가하지 않는다. 성공한 요청은 개별 결과 파일로 저장되어 재실행 시 재사용하고, 실패한 요청만 다시 시도한다.

## 결과 집계

결과 파일이 생긴 뒤 다음 명령은 추가 API 호출 없이 집계만 수행한다.

```bash
python run_config/final_report_llm_judge.py aggregate
```

산출물:

- `raw_normalized_results.json`: 위치 라벨을 실제 후보로 복원한 원시 판정
- `pair_results.json`: 두 순서를 결합한 Win/Loss/Tie/Error
- `summary.json`, `summary.csv`: Ablation × 기준별 adjusted win rate
- `aggregation_status.json`: 유효 호출 및 유효 쌍 수

Judge는 overall winner를 만들지 않는다. 주 결과는 Ablation 조건과 R1/R2/R3별 Win/Loss/Tie 및 adjusted win rate다.

## 테스트

```bash
pytest -q run_config/tests/test_final_report_llm_judge.py
```

테스트는 API를 호출하지 않는다.
