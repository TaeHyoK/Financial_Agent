# NP1·NP2·NP3: Full 대 No-peer

논문 실험의 5개 기업 × 3회차에서, 같은 기업·회차의 Full과 No-peer 최종 보고서를 비교한 LLM Judge 결과다. NP1~NP3는 **새 보고서 생성 조건이 아니라 No-peer 효과를 살피는 평가 기준**이다.

| 기준 | 평가 대상 | Full Win / Loss / Tie | 조정 승률 |
|---|---|---:|---:|
| NP1 | 대상기업의 차별적 강점·약점 식별 | 9 / 0 / 6 | 80.0% |
| NP2 | 대상기업 성과의 의미 해석 | 7 / 0 / 8 | 73.3% |
| NP3 | 대상기업 투자 매력의 변별력 | 9 / 0 / 6 | 80.0% |

각 기준마다 15쌍을 A/B 순서를 바꿔 두 번 평가했다(총 90호출, 모두 성공). 동일한 후보가 두 순서에서 모두 이길 때만 Win 또는 Loss로, 그 밖에는 Tie로 집계한다. 조정 승률은 `(Win + 0.5 × Tie) / (Win + Loss + Tie)`다. 세 기준을 합친 별도 종합 승자는 산출하지 않았다.

판정 모델은 `gpt-5.6-terra`(`reasoning.effort=low`)이다. 두 후보에게 같은 마스킹된 애널리스트 참조 본문과 비교기업 공통 근거를 제공했다. 공통 근거는 해당 회차 Full의 비교 데이터셋에서 사실·수치·비교 한계만 추려 생성 해석을 제외한 것이다. 경쟁사 언급의 양 자체를 평가하지 않는다. 정확한 프롬프트와 평가·집계 코드는 [`run_config/no_peer_target_llm_judge.py`](../../run_config/no_peer_target_llm_judge.py)에 있다.

- `summary.json`/`.csv`: 기준별 전체 집계.
- `summary_by_company.json`/`.csv`: 기업별·기준별 집계.
- `pair_results.json`: 45개 쌍·기준의 두 순서 판정과 최종 Win/Loss/Tie.
- `raw_normalized_results.json`: 90개 호출의 판정, 짧은 근거, 토큰 사용량, 요청·입력 SHA-256. 보고서 전문이나 요청 본문은 없다.
- `common_peer_evidence/`: 평가 때 동결한 15개 공통 비교 근거. 원본 평가 경로 `evaluation/no_peer_target_llm_judge/common_peer_evidence/`에서 옮겨 왔다.

원본 평가의 요청 전문·모델 원응답·마스킹된 참조 본문은 저작권 및 크기 때문에 Git에 넣지 않았다. 여기의 집계는 원본 2026-09-24 실행에서 내보낸 결과이며, 이 Git 추가 과정에서 유료 모델을 다시 호출하지 않았다. 새 평가를 재현하려면 Git 밖의 `evaluation/texts/`, `reports/` 원본과 API 키가 필요하다. `python run_config/no_peer_target_llm_judge.py prepare`는 오프라인으로 요청을 만들고, `run --execute-paid-api --confirm-call-count 90`만 유료 호출을 시작한다. 커밋된 결과 폴더는 원본 요청·응답을 생략한 **공개용 내보내기**이므로 이 폴더에서 `aggregate`를 직접 실행할 수는 없다.
