# Financial Agent Ablation 인수인계

기준 시각: 2026-09-20 KST

이 문서는 다른 노트북·계정에서 최종 보고서 정리와 LLM-as-a-Judge 평가를 이어가기 위한 현재 상태 기록이다. 생성 보고서와 실제 애널리스트 PDF는 Git에 넣지 않고 별도 ZIP으로 이동한다.

## 1. Git 상태

- 원격 저장소: `git@github.com:TaeHyoK/Financial_Agent.git`
- 인수인계 브랜치: `ablation-final-handoff-20260920`
- 분기 기준: `main`의 `ef91ffc` (`Merge pull request #5 from TaeHyoK/feature/luna-summaries-one-team`)
- 브랜치에는 기존 Financial Agent 전체 코드와 함께 다음을 추가한다.
  - `run_config/`: Ablation 생성·반복·평가·LLM Judge 실행 코드 및 기업 설정
  - `ablation_suite/`, `ablation_evaluation/`, `real_report_evaluation/`: 기존 Ablation 생성·평가 라이브러리
  - `ablation_results/`: 소형 상태 파일과 BERTScore·ROUGE-L 결과 (2026-09-23 이후: 최종 75개 지표 `repeated_standard_5companies/` 만 추적, 상태 파일·r01 단독 결과·Judge 상태는 제외)
  - `docs/ablation_handoff/`: 이전 실험·Judge 설계 기록
  - `scripts/prepare_ablation_handoff.py`: 보고서 이동용 ZIP 재생성 스크립트

## 2. 보고서 생성 상태

최종 LLM Judge 대상은 다음 75개 보고서다.

```text
5개 기업 × 3개 생성 회차 × 5개 조건 = 75개
```

- 기업: 현대건설, 두산, BGF리테일, 아모레퍼시픽, SK바이오팜
- 회차: r01, r02, r03
- 조건: Full, Random news, No-subdata, No-peer, One-team
- r01 기존 4조건 생성 상태: success
- r01 One-team(gpt-5.4 통일본) 생성 상태: success
- r02·r03 5조건 생성 상태: success, 총 50개

삼성전자와 코웨이의 r01 결과도 원 작업공간에는 있지만, 최종 5개 기업 Judge 대상과 이동용 ZIP에서는 제외했다.

## 3. 기존 자동 지표 상태

- 5개 기업 × 3회 × 5조건, 총 75개 본문에 대한 BERTScore·ROUGE-L 평가 완료
- 결과 위치: `ablation_results/repeated_standard_5companies/`
- r01 One-team gpt-5.4 검증 결과: `ablation_results/with_one_team_gpt54/` (2026-09-23 이후 Git 에서 제외, 이동용 ZIP 에 보존)
- 자동 지표 결과와 LLM Judge 결과는 서로 다른 품질 개념이므로 하나의 점수로 합치지 않는다.

## 4. LLM-as-a-Judge 현재 상태

코드: `run_config/final_report_llm_judge.py`

- 방식: FinRPT식 A/B 순서 교환 쌍대비교
- 공통 reference: 기업별 실제 애널리스트 보고서 1개
- reference는 정답이 아닌 전문가 앵커이며 공식 투자의견·목표주가는 마스킹
- 비교: Full vs Random news / No-subdata / No-peer / One-team
- 기준: R1 핵심 이슈, R2 투자 논지, R3 전망·위험요인
- 호출 수: `5 × 3 × 4 × 3 × 2 = 360`
- Judge: `gpt-5.6-terra`, `reasoning.effort=low`
- 응답: A / B / C
- 동일한 실제 후보가 두 순서에서 모두 선택된 경우에만 승리, 나머지 유효 조합은 Tie
- 집계: `(Win + 0.5 × Tie) / (Win + Loss + Tie)`
- 예상 입력: 총 1,715,172토큰, 호출당 평균 약 4,764토큰
- 현재 상태: `prepared_not_run`
- 실제 Judge 호출: 0회
- 유료 API 호출: 0회

동결 요청 360개와 감사 매핑은 이동용 ZIP의 `evaluation/final_report_llm_judge/`에 들어 있다. 매니페스트와 감사 경로는 저장소 기준 상대경로로 바꿔 다른 노트북에서도 검증 가능하다.

## 5. 이동용 보고서 ZIP

로컬 경로:

```text
/data/agent2/financial_agent_ablation_2025h2/handoff/financial_agent_ablation_report_bundle_20260920.zip
```

ZIP 정보:

- 크기: 26,644,137 bytes
- SHA-256: `2843325a120972eaa6f84a0b4ab9ec22934cd125d4cd1788953fdad19029dc4d`
- 최종 HTML: 75개
- 실제 애널리스트 PDF: 5개
- Candidate 추출 본문: 75개
- Reference 추출 본문: 5개
- API 미제출 Judge 요청: 360개

압축 전 폴더도 다음 위치에 보존돼 있다.

```text
/data/agent2/financial_agent_ablation_2025h2/handoff/financial_agent_ablation_report_bundle_20260920/
```

ZIP 내부의 `bundle_manifest.json`과 `SHA256SUMS.txt`로 개별 파일을 검증할 수 있다. ZIP 자체는 Google Drive 등 별도 저장소로 옮기고 Git에는 커밋하지 않는다.

## 6. 새 노트북에서 복원

```bash
git clone --branch ablation-final-handoff-20260920 \
  git@github.com:TaeHyoK/Financial_Agent.git
cd Financial_Agent
unzip /path/to/financial_agent_ablation_report_bundle_20260920.zip -d .
python run_config/final_report_llm_judge.py validate
pytest -q run_config/tests/test_final_report_llm_judge.py
```

검증 결과는 다음과 같아야 한다.

```text
state: valid
requests: 360
audits: 360
paid_api_calls: 0
```

API 키는 새 환경의 환경변수로만 설정하고 저장소나 ZIP에 기록하지 않는다.

## 7. Judge 실행과 집계

아래 명령은 실제 유료 평가를 시작하므로 프롬프트와 모델을 최종 검토한 뒤 실행한다.

```bash
python run_config/final_report_llm_judge.py run \
  --execute-paid-api \
  --confirm-call-count 360 \
  --workers 4
```

성공 결과는 요청별 파일로 저장된다. 재실행 시 성공 결과는 재사용하고 실패한 요청만 다시 시도한다. 완료 후 집계는 추가 API 호출 없이 수행한다.

```bash
python run_config/final_report_llm_judge.py aggregate
```

주요 결과는 `evaluation/final_report_llm_judge/summary.csv`, `summary.json`, `pair_results.json`이다. API·파싱 오류는 Tie로 바꾸지 말고 Error로 별도 보고한다.

## 8. 남은 작업

1. Git 브랜치를 새 노트북에서 clone하고 ZIP을 저장소 루트에 해제
2. SHA-256과 `validate` 결과 확인
3. 코드·프롬프트·reference 마스킹을 마지막으로 수동 검토
4. OpenAI API 키와 지출 한도 설정
5. 360회 Judge 실행
6. 결과 집계 및 표·논문 문구 작성
7. 기업별 reference가 1개뿐이라는 한계를 최종 논문에 명시

## 9. 인수인계 시점 검증

- 전체 테스트: 257 passed, 19 skipped
- skip 16개: Git에 넣지 않은 과거 127MB frozen 생성 입력이 필요한 반복생성 테스트
- skip 3개: 별도 보고서 ZIP을 풀기 전에는 입력 본문이 없어 실행할 수 없는 Judge 준비 테스트
- 이동용 ZIP의 Judge 요청 검증: valid, requests 360, audits 360, paid API calls 0

## 10. 정리 커밋 이후 상태 (2026-09-21 추가)

브랜치 `cleanup-dead-code-20260921` 에서 dead code 와 v1~v4 잔여 코드를 걷어냈다. 계획과 파일별 분류는 `docs/cleanup_plan_20260921.md` 에 있다.

- 최종 보고서 산출 동작과 프롬프트(`decision_agent_v5.md`, `comparison_agent.md`)는 바꾸지 않았다. 남은 v5 경로 코드는 정리 전과 같고, 테스트 픽스처로 Strategy·Writer 산출물과 캐시 지문이 전후 동일함을 확인했다. (이름 변경 이후: `decision_agent_v5.md` → `decision_agent.md`)
- LLM Judge(`run_config/final_report_llm_judge.py`)는 리포 내부 모듈을 import 하지 않아 영향이 없다. `validate` 는 여전히 valid / 360 / 360 / 0 이다.
- `real_report_evaluation/extract.py`·`runner.py` 는 손대지 않았고 `ablation_results/*/protocol.json` 의 해시와 일치한다.
- 테스트는 257 passed, 19 skipped 로 총계는 9절과 같지만 구성이 다르다. ZIP 을 풀면 Judge 준비 테스트 3개가 돌고, 대신 `tests/test_one_team.py` 의 러너 테스트 3개가 리포 바깥 경로(`../run_config/`)를 찾다가 스킵된다.

**생성 재현 검사는 이 브랜치에서 더 이상 통과하지 않는다.** `run_config/run_prepared_reports.py`·`run_one_team_reports.py`·`run_repeated_reports.py` 의 `check()` 는 생성 당시 `src/**/*.py` 의 sha256 을 상태 파일과 대조하는데, 정리 커밋 이후 트리는 생성 시점 코드와 다르다. `ablation_results/status/` 의 상태 파일은 생성 시점 기록으로 그대로 두었고 다시 만들지 않았다. 생성 시점 코드가 필요하면 커밋 `da85eb3`(`ablation-final-handoff-20260920` 브랜치 끝)을 본다.

정리 커밋 네 개와 규모:

| 커밋 | 내용 | 순 감소 |
|---|---|---|
| 9815f15 | 어디서도 참조되지 않는 파일·함수·상수·import | 1,708줄 |
| e084754 | Strategy v1~v4 실행 경로·결정 계약·프롬프트 제거, v5 단일화 | 5,681줄 |
| 0599ef3 | Writer 의 v1 handoff·v2 폴백·v4 분기 제거 | 822줄 |
| e119baf | 구 ablation·평가 스택과 미사용 엔트리포인트 삭제 | 3,757줄 |

2026-09-23 추가: 산출물 파일명과 계약 문자열에서 Strategy·Writer 의 버전 접미사를 없앴다. `strategy_decision_output_v5.json` 은 `strategy_decision_output.json`, `writer_editorial_packet_v3.json` 은 `writer_editorial_packet.json` 처럼 이름이 바뀌었고 `decision_version`·`packet_version` 같은 계약 값도 같은 규칙으로 바뀌었다. 새 코드는 새 이름만 읽고 쓰므로 생성 시점에 만든 `ablation_results/` 아래 산출 디렉터리는 이 트리의 코드로 다시 읽을 수 없다. 계약 값이 캐시 지문에 들어가므로 Strategy·Writer 의 캐시 지문도 생성 시점과 달라졌고, 같은 입력이라도 캐시가 다시 맞지 않는다. 생성 시점 산출물을 그대로 다루려면 정리 이전 커밋을 본다.

2026-09-23 추가: 최종 보고서 HTML 75개를 `final_reports/` 로 Git 에 추적한다. 그 외 중간 산출물(생성 상태 파일, r01 단독 지표, Judge 요청·응답·상태)은 Git 에서 빼고 이동용 ZIP 과 Release 로만 배포한다.
