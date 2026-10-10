# 반복 생성

현대건설, 두산, BGF리테일, 아모레퍼시픽, SK바이오팜의 Full·Random news·No-subdata·No-peer·One-team 보고서를 총 3회 생성한다. `run_repeated_reports.py`는 완료된 r01 입력을 바탕으로 r02·r03의 50개 보고서를 생성한다.

동결한 관측 자료와 월별 요약은 재사용하고 하위 분석·비교·Strategy·Writer 응답은 새로 생성한다. No-peer는 같은 회차 Full의 대상기업 하위 분석을 재사용한다. Random 표본과 모델 설정은 반복 간 동일하게 유지한다.

```bash
python ablation/run_config/run_repeated_reports.py --help
python ablation/run_config/run_repeated_reports.py prepare
python ablation/run_config/run_repeated_reports.py check
```

`launch`는 유료 생성 작업이다. 완료된 원본 입력·상태와 개인 API 키를 준비하고 `check`가 통과한 뒤 실행한다. 실패한 작업은 `launch-resume`로 재개할 수 있다. 새 실행은 기존 논문 결과를 덮어쓰지 않는 별도 작업공간을 사용한다.

평균과 표본 표준편차의 집계 방식은 [실험 방법](../docs/ABLATION.md), 공개 결과는 [results](../results/README.md)에 있다.
