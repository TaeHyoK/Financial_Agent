# final_reports

논문 ablation 의 최종 보고서 HTML 75개다. 경로는 `r0N/<조건>/<기업>.html`.

- 회차 `r01`, `r02`, `r03`: 같은 입력으로 독립 생성한 3회
- 조건 `full`, `random_news`, `no_subdata`, `no_peer`, `one_team`
- 기업: 현대건설, 두산, BGF리테일, 아모레퍼시픽, SK바이오팜

차트는 HTML 안에 포함돼 있어 파일 하나로 열린다. 생성 조건과 평가 규약은 `docs/ABLATION_HANDOFF_20260920.md`, 자동 지표는 `ablation_results/repeated_standard_5companies/` 를 본다. 실제 애널리스트 보고서 PDF, 추출 본문, LLM Judge 요청·응답 같은 중간 산출물은 저작권과 크기 때문에 Git 에 두지 않는다.
