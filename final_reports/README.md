# final_reports

논문 ablation 의 최종 보고서 HTML 75개다. 경로는 `r0N/<조건>/<기업>.html`.

- 회차 `r01`, `r02`, `r03`: 같은 입력으로 독립 생성한 3회
- 조건 `full`, `random_news`, `no_subdata`, `no_peer`, `one_team`
- 기업: 현대건설, 두산, BGF리테일, 아모레퍼시픽, SK바이오팜

차트는 HTML 안에 포함돼 있어 파일 하나로 열린다. 조건 정의와 재현 절차는 [Ablation 방법과 재현](../docs/ABLATION.md), 자동 지표는 `ablation_results/repeated_standard_5companies/` 를 본다. 실제 애널리스트 보고서 PDF, 추출 본문, LLM Judge 요청·응답 같은 중간 산출물은 저작권과 크기 때문에 Git 에 두지 않는다.

## 생성 경위

모든 보고서는 태그 `paper-generation`(커밋 `da85eb3`)의 코드로 만들었다. 원자료는 태그 `paper-collection`(커밋 `7e20b3d`)에서 수집했다.

| 묶음 | 보고서 수 | 실행기 |
|---|---:|---|
| r01 Full·Random news·No-subdata·No-peer | 20 | `run_config/run_prepared_reports.py run` |
| r01 One-team | 5 | `run_config/run_one_team_reports.py run --model gpt-5.4 --run-id one_team_gpt54_r01` |
| r02·r03 다섯 조건 | 50 | `run_config/run_repeated_reports.py launch` |

r01 의 두 실행기는 삼성전자와 코웨이까지 7개 기업을 생성했다. 논문은 그중 5개 기업만 쓴다.

모델은 모든 조건에서 같다. 하위 분석, 비교 분석, Strategy, Writer 는 `gpt-5.4` 를 썼다. 월별 뉴스 요약은 `gpt-5.6-luna` 로 r01 의 Full 과 Random news 에서 만들었고, r02·r03 와 One-team 은 이 요약을 다시 썼다. No-subdata 는 요약을 쓰지 않는다.

`r03/one_team/아모레퍼시픽.html` 은 오프라인으로 복구한 보고서다. Writer 응답이 차트 근거 연결 검증에서 실패했다. `run_config/recover_amore_writer_r03.py` 로 저장된 Writer 응답에서 차트 2개의 근거 카드 목록만 고쳤다. 각 목록에서 통합 분석 카드 연결 1건을 빼고 원래 직접 근거 카드는 남겼다. 그 응답으로 Writer 이후 단계를 오프라인으로 다시 돌렸다. 본문, 투자의견, 차트 설명은 바꾸지 않았고 API 를 추가로 호출하지 않았다. `metrics.csv` 의 `offline_recovery` 열이 이 보고서의 복구 기록을 가리킨다.

## 현재 코드와 다른 점

이 HTML 들은 PR #26 과 PR #27 이전 코드로 만들었다. 그래서 현재 코드의 출력과 다음이 다르다.

- 일부 보고서의 표에 투자의견이 영문(Buy/Hold/Sell)으로 나오고, 내부 필드 이름이 그대로 드러난 곳이 있다. PR #26 이후 코드는 매수/중립/매도로 표시한다.
- 끝의 고지문이 PR #27 이전 문구다.

논문의 평가는 이 HTML 그대로를 대상으로 했다. `ablation_results/repeated_standard_5companies/metrics.csv` 의 `report_sha256` 은 75개 파일과 모두 일치한다.


같은 보고서를 현재 디자인으로 다시 그린 판은 [`final_reports_redesigned/`](../final_reports_redesigned/README.md) 에 있다. 본문은 같고 겉모습과 표시 방식이 다르다.
