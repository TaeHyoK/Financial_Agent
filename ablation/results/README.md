# Evaluation results

5개 기업 × 5개 조건 × 3회 생성 실험의 BERTScore와 Full 대 No-peer 대상기업 통찰 평가를 공개한다.

- `repeated_standard_5companies/`: BERTScore Precision·Recall·F1, 기업·조건별 평균·표준편차 및 평가 규약.
- `no_peer_target_llm_judge/`: NP1·NP2·NP3의 기준별 집계, 쌍 판정, 순서별 판정과 공통 비교 근거. [평가 설명](no_peer_target_llm_judge/README.md)을 참고한다.

[실험 방법](../docs/ABLATION.md)과 [최종 보고서](../../final_reports/README.md)는 각각의 디렉터리에 있다. 기존 평가 점수를 재계산하거나 새 생성 결과와 합산하지 않았다.

`metrics.csv`의 `report_sha256`은 평가 당시 HTML 해시다. 공개 파일의 SHA-256과 서술 본문 대응은 [보고서 명세](../../final_reports/report_manifest.json)에 기록한다. `${ABLATION_WORKSPACE}`는 평가 당시 개인 작업공간, `${REPO}`는 저장소 루트의 자리표시자다. 실제 개인 경로나 API 키는 공개하지 않는다.

참조 PDF·추출 참조 본문·Judge 요청 전문·원응답·생성 상태 로그는 포함하지 않는다. 공개 판정 결과만으로 원본 `aggregate` 실행을 대신할 수는 없다.
