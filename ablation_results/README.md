# ablation_results

최종 보고서 75개(`final_reports/`)에 대한 자동 지표 결과만 둔다.

- `repeated_standard_5companies/`: 5개 기업 × 3회차 × 5조건의 BERTScore·ROUGE-L. `protocol.json` 은 추출·채점 코드의 sha256 을 기록한 평가 규약이다.

생성 실행기의 상태 파일, r01 단독 결과, LLM Judge 요청·응답 같은 중간 산출물은 Git 에 두지 않는다. 남아 있는 경로 문자열의 `${ABLATION_WORKSPACE}` 는 생성 당시 작업공간, `${REPO}` 는 이 저장소 루트를 뜻한다.
