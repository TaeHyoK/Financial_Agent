# One-team 조건

Full과 같은 영역별 관측 자료를 하나의 통합 분석 요청으로 처리한다. 재무·시장에는 월별 뉴스 요약을, 뉴스에는 선정된 기사 전체를 제공한다. 비교기업에도 같은 통합 분석 절차를 적용하고 이후 비교 분석·Strategy·Writer는 유지한다.

모델은 통합 분석과 후속 단계 모두 `gpt-5.4`다. 월별 뉴스 요약은 Full의 동일 요약을 재사용하며 One-team을 위해 새로 생성하지 않는다. 동일 조건의 3회 생성에서 하위 분석 응답 자체는 독립적으로 생성한다.

```bash
python ablation/run_config/run_one_team_reports.py --help
python ablation/run_config/run_one_team_reports.py prepare --model gpt-5.4 --run-id one_team_gpt54_r01
python ablation/run_config/run_one_team_reports.py check --model gpt-5.4 --run-id one_team_gpt54_r01
```

`prepare`와 `check`에는 동결한 Full 입력·상태가 필요하다. `run` 또는 `resume`는 유료 분석 호출을 수행하므로 입력 검증 후 실행한다. 키 파일은 개인 `--env-file` 경로로 지정하며 Git에 넣지 않는다.

실행기는 생성 당시 버전·입력 해시를 검사한다. 과거 실험을 현재 코드로 실행할 때의 차이와 원자료 요건은 [전체 실험 방법](../docs/ABLATION.md)에 있다.
