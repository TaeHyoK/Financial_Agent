# ablation_results

최종 보고서 75개(`final_reports/`)에 대한 BERTScore 결과와 Full 대 No-peer 대상기업 통찰 평가 결과를 둔다.

- `repeated_standard_5companies/`: 5개 기업 × 3회차 × 5조건의 BERTScore(Precision·Recall·F1)와 기업·조건별 평균·표준편차. `protocol.json` 은 추출·채점 코드의 sha256 을 기록한 평가 규약이다.
- `no_peer_target_llm_judge/`: Full 대 No-peer의 NP1·NP2·NP3 평가 기준별 결과, 45개 쌍 판정, 90개 순서별 판정과 공통 비교 근거. [평가 설명](no_peer_target_llm_judge/README.md)을 본다.

자동 지표는 BERTScore 이고 NP1·NP2·NP3는 LLM Judge 평가다. 계산 절차는 [Ablation 방법과 재현](../docs/ABLATION.md)을 본다.

생성 실행기의 상태 파일, r01 단독 결과, LLM Judge 요청 전문·원응답 같은 중간 산출물은 Git 에 두지 않는다. 남아 있는 경로 문자열의 `${ABLATION_WORKSPACE}` 는 생성 당시 작업공간, `${REPO}` 는 이 저장소 루트를 뜻한다.
