# Ablation 방법과 재현

논문 실험은 5개 기업, 5개 조건, 3회 생성으로 최종 보고서 75개를 만들고 평가했다. 이 문서는 조건을 어떻게 구현했는지, 어느 커밋에서 무엇을 실행했는지, 산출물이 어디에 있는지 정리한다. 실행기 사용법의 세부는 `run_config/` 아래 문서(`ONE_TEAM.md`, `REPEATED_REPORTS.md`, `FINAL_REPORT_LLM_JUDGE.md`)를 본다.

## 조건

| 조건 | 무엇을 빼거나 바꾸는가 | 구현 위치 | 실행기 |
|---|---|---|---|
| Full | 기준 조건이다. DART 기업 필터를 거친 뉴스에서 공시 섹션별 가중 임베딩 유사도로 주별 최대 3건을 고른다. 하위 분석은 다른 영역의 보조자료를 받고, 비교기업 분석을 거친다. | `src/` 기본 경로 | r01 `run_prepared_reports.py`, r02·r03 `run_repeated_reports.py` |
| Random news | 뉴스 선정에서 DART 기업 필터와 공시 관련성 선정을 함께 뺀다. 기업 필터 전 후보(`news_events_prefilter`)에서 Full과 같은 주·월 구간별 수량을 무작위로 뽑는다. 월별 요약도 이 기사로 따로 만든다. | `ablation_suite/annual_random.py` 의 `select_annual_random_events`, 호출은 `run_config/prepare_condition_inputs.py` | Full과 같음 |
| No-subdata | 재무·뉴스·시장 하위 분석에서 다른 영역의 보조자료를 뺀다. 주 자료와 가치평가 계산은 그대로 둔다. 월별 요약은 만들지 않는다. | `--primary-data-only`(`src/orchestration/full_report_pipeline.py`), 요청은 `prepare_condition_inputs.py` 가 따로 만든다 | Full과 같음 |
| No-peer | 비교기업 분석과 비교 근거를 뺀다. 같은 회차 Full의 대상기업 하위 분석을 그대로 쓰고 Strategy와 Writer만 새로 만든다. | `--no-competitor`(`src/orchestration/full_report_pipeline.py`) | Full과 같음 |
| One-team | 재무·뉴스·시장 세 하위 분석을 기업당 통합 분석 1회로 바꾼다. 입력은 Full의 영역별 입력과 같다. 비교 분석·Strategy·Writer는 그대로 이어 붙인다. | `src/Agent_Team/Unified_Agent/`, 후속 단계 연결은 `runtime/sitecustomize.py` import hook(`ONE_TEAM_RUNTIME=1`) | r01 `run_one_team_reports.py`, r02·r03 `run_repeated_reports.py` |

Random news 표본은 r01 에서 한 번만 뽑았다. 시드는 기본값 `20251031`, 기준일, `"<기업명>:replicate:1"` 을 이어 붙인 문자열의 SHA-256 앞 8바이트다(`ablation_suite/utils.py` 의 `stable_seed`). 대상기업과 비교기업은 각자 시드를 받는다. r02·r03 은 같은 표본을 다시 쓴다. 따라서 3회의 차이는 같은 입력에서 생성 결과가 달라진 폭이고, 여러 무작위 표본의 차이가 아니다.

Random news 는 순위 알고리즘 하나만 뺀 조건이 아니다. 기업 필터와 관련성 선정을 함께 뺀 조건이다. 세부 처리는 [Random 뉴스 분기 변경](random_news_prefilter.md)에 있다.

## 기업과 비교기업

비교기업은 기업마다 손으로 정해 `run_config/collection_manifest.json` 에 고정했다. 자동으로 고르지 않았다. 실행기는 이 쌍을 `frozen_manual_pair` 로 기록한다.

| 대상기업 | 비교기업 | 기준일 | 참조 보고서 파일 |
|---|---|---|---|
| 현대건설 | GS건설 | 2025-10-20 | `references/20251020_company_현대건설.pdf` |
| SK바이오팜 | 유한양행 | 2025-11-06 | `references/20251106_company_sk바이오팜.pdf` |
| BGF리테일 | GS리테일 | 2025-11-07 | `references/20251107_company_bgf리테일.pdf` |
| 아모레퍼시픽 | LG생활건강 | 2025-11-07 | `references/20251107_company_아모레퍼시픽.pdf` |
| 두산 | SK | 2025-11-11 | `references/20251111_company_두산.pdf` |

기준일은 참조하는 실제 애널리스트 보고서의 작성일이다. 파이프라인은 기준일 장 시작 전을 분석 시점으로 보므로, 뉴스와 시장 자료는 기준일 전날까지 12개월을 쓴다. 투자 전망 기간은 향후 12개월이다.

수집과 r01 생성에는 삼성전자(비교기업 SK하이닉스, 2025-10-31)와 코웨이(비교기업 쿠쿠홀딩스, 2025-11-10)도 들어 있다. 논문은 이 두 기업을 쓰지 않는다. r02·r03 은 5개 기업만 생성했다.

## 모델

| 단계 | 모델 |
|---|---|
| 하위 분석, 비교 분석, Strategy, Writer(다섯 조건 모두) | `gpt-5.4` |
| One-team 통합 분석과 후속 단계 | `gpt-5.4`(`--model gpt-5.4 --run-id one_team_gpt54_r01`) |
| 월별 뉴스 요약 | `gpt-5.6-luna`, 기업·조건마다 월 12회 |
| LLM Judge | `gpt-5.6-terra`, `reasoning.effort=low` |
| BERTScore | `BAAI/bge-m3`, 24층, IDF·기준선 재조정 없음, 배치 1 |

월별 요약은 r01 의 Full 과 Random news 에서 만들었다. No-peer 와 One-team 은 Full 의 요약을, r02·r03 은 r01 의 요약을 다시 쓴다. `run_one_team_reports.py` 의 `--model` 기본값은 `gpt-5.6-luna` 이므로 논문 설정을 재현하려면 위 플래그를 꼭 준다.

## 커밋

| 단계 | 태그 | 커밋 |
|---|---|---|
| 원자료 수집 | `paper-collection` | `7e20b3d` |
| 보고서 생성(r01, r02·r03, 오프라인 복구) | `paper-generation` | `da85eb3` |

`da85eb3` 의 `src/` 는 `ef91ffc`(PR #5 병합)와 같다. 수집 실행기는 `collection_manifest.json` 의 `repository.path` 에서 `git rev-parse HEAD` 가 `7e20b3d` 인지 확인하고, 다르면 멈춘다. 생성 실행기의 `check` 는 `src/**/*.py` 와 실행기 파일의 sha256 을 준비 명세와 대조한다. One-team 실행기는 `run_config/collection_manifest.json` 의 sha256 도 대조한다. `main` 에서는 코드와 수집 명세(경로를 작업공간 기준 상대 경로로 바꿈)가 모두 달라져 이 검사가 통과하지 않는다. 생성 단계는 태그 `paper-generation` 에서 재현한다.

`paper-generation` 시점의 수집 명세에는 원 서버의 절대 경로(`environment_file` 등)가 남아 있다. 다른 머신에서 생성 단계를 돌리려면 그 시점 `run_config/collection_manifest.json` 의 `environment_file` 을 자기 키 파일 경로로 고쳐야 한다. 이렇게 고치면 One-team 실행기의 수집 명세 해시 대조는 통과하지 않는다. 원 서버 밖에서 생성 단계를 끝까지 다시 돌려 본 적은 없다.

`da85eb3` 이후 `main` 에는 동작을 바꾸는 변경이 들어왔다.

- 산출물 파일명과 모듈 이름 정리(버전 접미사 제거). 생성 시점 산출물을 현재 코드로 다시 읽을 수 없다.
- 종류주식 가치평가 수정(PR #25).
- Writer 표의 투자의견을 매수/중립/매도로 표시하고 내부 필드 이름 노출을 막음(PR #26).
- Writer 프롬프트 정리와 새 고지문(PR #27).

그래서 `main` 을 실행해도 75개 보고서를 똑같이 다시 만들지 않는다. 공개한 HTML 에는 PR #26·#27 이전의 영문 등급 표기와 이전 고지문이 남아 있다.

BERTScore 단계가 쓰는 본문 추출·채점 코드(`real_report_evaluation/extract.py`, `runner.py`)는 평가 규약 `ablation_results/repeated_standard_5companies/protocol.json` 에 sha256 으로 고정돼 있고, 현재 브랜치의 두 파일도 그 값과 같다.

## 재현 단계

이 리포를 클론한 폴더를 작업공간으로 쓰고, 명령은 모두 작업공간에서 실행한다. Git 밖 자료(아래 "필요한 입력")는 저작권과 크기 때문에 리포에 없다. API 키는 `configs/.env` 에 둔다. 수집 명세의 경로는 작업공간 기준으로 풀린다. 키 파일 위치를 바꾸려면 수집 실행기와 One-team 실행기(`run`·`resume`)는 `--env-file` 을 쓰고, `run_prepared_reports.py`·`run_repeated_reports.py` 는 명세의 `environment_file` 을 그대로 읽는다.

| 단계 | 명령 | 커밋 | 필요한 입력(Git 밖) | 산출물 |
|---|---|---|---|---|
| 1. 원자료 수집 | `python run_config/collect_pre_llm_data.py`(`--dry-run` 은 계획만 기록, `--env-file` 로 키 파일 지정) | `paper-collection`(`repo/` 작업 트리) | `DART_API_KEY`, `references/` PDF | `collected_data/<대상기업>/snapshot/`, `status/data_collection_status.json` |
| 2. 조건별 입력 준비 | `python run_config/prepare_condition_inputs.py`(기본 `--seed 20251031 --replicate 1`) | 아래 주 참고 | 1단계 산출물, `references/`, `experiments/luna_summary_pilot/pilot_manifest.json` | `prepared_inputs/replicate_01/`(Random 표본과 `selection_audit.json`, No-subdata 요청, 조건별 `condition_manifest.json`, `preparation_manifest.json`) |
| 3. r01 네 조건 | `python run_config/run_prepared_reports.py check` 뒤 `run`(백그라운드는 `launch`, 실패 뒤 재개는 `resume`·`launch-resume`) | `paper-generation` | 1·2단계 산출물, `OPENAI_API_KEY` | `reports/<조건>/replicate_01/<기업>/report_<기업>.html`, `status/report_generation_status.json` |
| 4. r01 One-team | `python run_config/run_one_team_reports.py prepare` → `check` → `run`, 모두 `--model gpt-5.4 --run-id one_team_gpt54_r01`(`run`·`resume` 은 `--env-file` 로 키 파일 지정) | `paper-generation` | 3단계 산출물과 상태 파일 | `prepared_inputs/one_team/one_team_gpt54_r01/`, `reports/one_team/one_team_gpt54_r01/`, `status/one_team/` |
| 5. r02·r03 다섯 조건 | `python run_config/run_repeated_reports.py prepare` → `check` → `launch`(재개는 `launch-resume`) | `paper-generation` | 3·4단계 산출물 | `reports/repeated_standard_5companies/replicate_0{2,3}/<조건>/<기업>/report_<기업>.html`, 같은 폴더의 `status.json` |
| 6. 보고서 1건 오프라인 복구 | `python run_config/recover_amore_writer_r03.py check` 뒤 `run` | `paper-generation`(정리 이후 트리에서는 동작하지 않음) | 5단계 산출물과 저장된 Writer 응답 | r03 One-team 아모레퍼시픽 보고서, `recovery/chart_basis_links/recovery_audit.json` |
| 7. r01 BERTScore | `python run_config/evaluate_report_bodies.py` | 현재 브랜치 가능 | 3·4단계 상태 파일과 보고서, `references/`, `evaluation/` 의 기존 평가 파일, 내려받아 둔 `BAAI/bge-m3` | `evaluation/with_one_team_gpt54/` |
| 8. r02·r03 BERTScore와 평균 | `python run_config/evaluate_repeated_bert.py` | 현재 브랜치 가능 | 7단계 산출물, 5·6단계 상태 파일과 보고서 | `evaluation/repeated_standard_5companies/` |
| 9. LLM Judge | `python run_config/final_report_llm_judge.py prepare` → `validate` → `run --execute-paid-api --confirm-call-count 360 --workers 4` → `aggregate` | 현재 브랜치 가능 | `evaluation/` 아래 추출 본문(r01 네 조건과 참조 본문은 7단계 이전의 r01 평가 `evaluation/texts/`, r01 One-team 은 7단계, r02·r03 은 8단계), `OPENAI_API_KEY` | `evaluation/final_report_llm_judge/` |

주:

- 1단계: 작업공간에서 `git worktree add repo paper-collection` 으로 수집용 작업 트리 `repo/` 를 만든다. 태그 `paper-collection` 은 `7e20b3d` 를 가리킨다. `configs/.env` 에 `DART_API_KEY` 를 넣고 `python run_config/collect_pre_llm_data.py` 를 실행한다. 실행기는 `repo/` 의 코드(수집 커밋)를 돌리고, 결과는 작업공간의 `collected_data/` 에 저장한다. 시작할 때 `repo/` 의 `HEAD` 가 `7e20b3d` 인지 확인한다.
- 1단계는 LLM 을 부르지 않는다. 실행기가 OpenAI 키를 비워 둔다. DART, 시장 자료, Google News 를 네트워크로 받는다.
- 2단계는 네트워크와 유료 호출을 막고 실행한다. 이 단계를 어느 커밋에서 실행했는지는 기록으로 확인하지 못했다. 준비 명세가 `src/` 의 sha256 을 기록하고 3단계가 이를 대조한다.
- 3단계는 r01 월별 요약(`gpt-5.6-luna`)도 만든다. 7개 기업 28개 보고서를 만들며 논문은 그중 20개를 쓴다.
- 7·8단계는 유료 API 를 부르지 않는다. 두 스크립트 모두 `--device` 로 장치를 고른다. 기본값 `auto` 는 CUDA 가 있으면 CUDA, 없으면 CPU 를 쓴다. 논문 결과는 `cuda:2` 에서 계산했다. 8단계는 7단계의 r01 점수 25개를 다시 쓰고 r02·r03 점수 50개를 새로 계산해 75개를 집계한다.
- 9단계 `run` 은 유료 호출이다. 두 확인 옵션을 함께 줘야 시작한다. Judge 는 실행을 마쳤지만 요청·응답·결과 파일은 리포에 없다. 설계는 `run_config/FINAL_REPORT_LLM_JUDGE.md` 를 본다.
- 7·8단계 전에 `python -m pip install -e ".[eval]"` 로 PDF 본문 추출(pdfplumber)과 BERTScore(bert-score, torch, transformers) 의존성을 설치한다.

## 현재 코드로 다시 생성

`run_config/rerun_ablation.py` 는 75개 보고서를 현재 `main` 코드로 다시 만든다. 단계 순서와 조건별 재사용 관계는 원래 생성과 같다. Full 을 먼저 돌리고 No-peer·One-team 을 돌리며, r01 을 먼저 돌리고 r02·r03 을 돌린다. 월별 요약은 r01 의 Full 과 Random news 에서만 만들고 나머지는 이를 다시 쓴다.

- 원래 작업공간을 `--source` 로 준다. `collected_data/`, `reports/`, `status/` 가 있어야 한다. 네트워크에서 자료를 다시 받지 않는다.
- 결정적 단계는 현재 코드로 다시 계산한다. 뉴스 선정, Random 표본(같은 시드 규칙), 월별 요약 요청, 조건별 입력, 재무 사실, 시장 요약, 가치평가가 여기에 든다. 주식 수 정보는 `--share-info recompute`(기본값)이면 저장해 둔 DART XML 에서 현재 추출기로 다시 뽑고, `frozen` 이면 수집할 때 값을 그대로 쓴다. 해당 XML 이 없는 기업은 두 경우 모두 수집 때 값을 쓴다.
- LLM 호출마다 요청 해시를 구한다. 단계·기업·조건·회차가 같은 원래 호출과 해시가 같으면 모델을 부르지 않고 원래 응답을 돌려준다. 해시는 `shared/llm_clients.py` 와 같이 compact JSON 의 SHA-256 이다. 원래 응답만 다시 쓰고, 응답 뒤의 검증·병합·보고서 작성은 모두 현재 코드로 돌린다. 해시가 다르면 모델을 새로 부른다.
- 모델 이름은 `gpt-5.4`(하위 분석, 통합 분석, 비교 분석, Strategy, Writer)와 `gpt-5.6-luna`(월별 요약)다. 요청 해시와 기록에도 이 이름이 그대로 남는다.
- 에이전트를 하위 프로세스로 띄우지 않고 한 프로세스 안에서 돌린다. `--workers N` 은 서로 기다릴 필요가 없는 보고서를 프로세스 N 개로 나눠 돌린다.

```bash
# 1. 계획: 모델을 부르지 않고, 단계·조건·회차마다 원래 응답을 쓸 호출과 새로 부를 호출을 센다
PYTHONPATH=src python run_config/rerun_ablation.py plan --source <원래 작업공간> --out <새 작업공간> --workers 6
# 2. 실행: 중단되면 같은 명령을 다시 준다. 끝난 보고서와 끝난 단계는 건너뛴다
PYTHONPATH=src python run_config/rerun_ablation.py run --source <원래 작업공간> --out <새 작업공간> --workers 4
# 3. 정리: final_reports/, strategy_decisions/, manifest.json 을 다시 쓴다(run 도 끝날 때 한 번 쓴다)
PYTHONPATH=src python run_config/rerun_ablation.py collect --source <원래 작업공간> --out <새 작업공간>
```

`--companies`, `--conditions`, `--replicates` 로 범위를 줄인다. 범위를 줄여 새 `--out` 에 돌려도 된다. No-peer 는 같은 회차 Full 의 대상기업 하위 분석을, One-team 과 r02·r03 은 r01 Full(또는 같은 조건 r01)의 입력과 월별 요약을 쓴다. 고르지 않은 앞선 보고서가 필요하면 `plan` 과 `run` 이 그 보고서를 더하되 필요한 단계만 돌린다. 이 보고서는 `partial` 상태로 남고 Strategy·Writer 는 돌리지 않는다. 무엇을 더했는지는 시작할 때 `dependency ...` 줄로 출력한다. `--no-dependencies` 를 주면 더하지 않고, 앞선 단계가 `--out` 에 없는 보고서는 멈춘다. `run` 을 시작하려면 환경에 `OPENAI_API_KEY` 가 있어야 한다. `--env-file` 로 키 파일을 줄 수 있지만 이미 내보낸 환경 변수가 먼저다. 같은 `--out` 에서 `--source` 나 `--share-info` 를 바꾸면 실행기가 멈춘다. `run --fake-transport stop|canned` 는 모델 없이 배선만 확인하는 점검용이며 결과로 쓰지 않는다.

2026-10-04 `plan` 결과(현재 코드, 원래 응답을 쓸 호출 / 새로 부를 호출):

| 단계 | `recompute` | `frozen` |
|---|---:|---:|
| 월별 요약 | 240 / 0 | 240 / 0 |
| 뉴스 하위 분석 | 90 / 0 | 90 / 0 |
| 재무 하위 분석 | 72 / 18 | 90 / 0 |
| 시장 하위 분석 | 90 / 0 | 90 / 0 |
| One-team 통합 분석 | 24 / 6 | 30 / 0 |
| 비교 분석 | 0 / 60 | 0 / 60 |
| Strategy | 0 / 75 | 0 / 75 |
| Writer | 0 / 75 | 0 / 75 |

`recompute` 에서 새로 부르는 재무·통합 분석 24회는 SK바이오팜(대상기업)과 SK(두산의 비교기업)다. 현재 추출기가 두 기업의 보통주 수를 새로 읽어 재무 요청이 바뀐다. 비교 분석은 비교 데이터셋 계산이 바뀌어 60회 모두 새로 부른다.

원래 응답은 다음 파일에서 읽는다. 월별 요약·뉴스·재무·시장은 모델이 돌려준 JSON 원문을 저장하지 않았다. 그래서 저장된 출력에서 모델 JSON 을 다시 묶어 돌려준다. `plan` 과 `run` 은 원래 응답을 쓴 단계마다 새 출력과 원래 출력을 비교해 `status/replay_fidelity.jsonl` 에 남긴다. 2026-10-04 비교에서 월별 요약·뉴스·재무 출력은 원래와 같았다. 시장 보고서는 `valuation_snapshot` 만, 통합 보고서는 `supporting_facts.market.valuation_snapshot` 만 달랐다. 둘 다 현재 코드가 다시 계산한 가치평가다.

| 단계 | 원래 응답 | 돌려주는 방법 |
|---|---|---|
| 월별 요약 | `News/<날짜>/context_exports/month/llm_period_summaries.json` 의 기간별 결과 | 기간 결과를 `{"periods": [...]}` 로 감싼다. 기간 ID 를 붙이는 후처리는 같은 값을 다시 쓴다 |
| 뉴스 | `News/<날짜>/output/news_agent_handoff.json` 의 `output` | 호출 뒤 붙인 필드를 빼고, 검증 때 빠진 기준 근거 ID 를 병합된 ID 목록의 첫 값으로 되살린다 |
| 재무 | `Financial/<날짜>/final_report.json` | 모델이 쓴 판단(주 판단, 항목별 판단, 보조 맥락 판단)을 요청 스키마대로 다시 묶는다 |
| 시장 | `Y_Finance/<날짜>/final_report.json` | 호출 뒤 붙인 필드를 빼고 보조 맥락 판단을 영역별로 다시 묶는다 |
| One-team 통합 분석 | `runs/<날짜>/unified_domain_team/unified_response.json` 의 `output` | 저장된 모델 JSON 을 그대로 쓴다 |

`--out` 에 남는 것:

- `reports/`: 원래와 같은 구조의 보고서별 작업 폴더. `prepared_inputs/` 와 `collected_data/`(주식 수를 반영한 재무 입력)도 같은 구조로 남는다.
- `final_reports/r0N/<조건>/<기업>.html`, `strategy_decisions/r0N/<조건>/<기업>.json`
- `manifest.json`: 코드 커밋, 단계별 모델 이름, 주식 수 처리 방식, 원래 응답을 쓴 호출 수와 새로 부른 호출 수, 시각
- `status/llm_calls.jsonl`: 호출마다 단계, 기업, 조건, 회차, 모델 이름, 요청 해시, 원래 응답을 썼는지
- `status/llm_usage.jsonl`: 모델에 실제로 보낸 호출만 남는 사용량 기록(`LLM_USAGE_MANIFEST`)
- `status/reports/r0N/<조건>/<기업>.json`: 보고서별 진행 상태. 이어서 돌릴 때 이 파일을 본다.

### 앞선 재생성의 응답 다시 쓰기

`--replay-from <앞선 --out>` 을 주면 앞선 재생성이 모델에서 받은 응답도 다시 쓴다. 여러 번 줄 수 있다. 키와 해시는 원래 응답과 같다(회차, 조건, 대상기업, 역할, 단계와 요청 해시). 같은 키와 해시가 여러 곳에 있으면 원래 작업공간을 먼저 쓰고, 그다음 `--replay-from` 을 준 순서대로 쓴다. `run` 을 실제 모델로 끝낸 작업공간만 받는다(`run_settings.json` 의 `transport` 가 `real`). 같은 단계를 여러 번 불렀다면 저장된 출력은 마지막 호출의 것이므로 마지막 호출만 다시 쓴다. 단계가 끝나지 않은 보고서의 호출은 쓰지 않는다.

| 단계 | 앞선 재생성에서 읽는 파일 | 돌려주는 방법 |
|---|---|---|
| 월별 요약·뉴스·재무·시장·통합 분석 | 원래 응답과 같은 파일 | 원래 응답과 같다. 뉴스·재무·통합 분석은 저장된 요청의 해시가 기록된 해시와 같은지도 본다 |
| 비교 분석 | `Competitor/<날짜>/peer_comparison_output.json` | 모델 JSON 을 그대로 쓴다 |
| Strategy | `Strategy/<날짜>/strategy_response_attempts/` 중 검증을 통과했고 `strategy_decision_cache.json` 과 지문이 같은 마지막 응답 | 모델 JSON 을 그대로 쓴다 |
| Writer | `Writer/<날짜>/llm_writer_output.json` 의 `raw_payload` 와 `usage` | 모델 JSON 을 그대로 쓴다 |

응답을 다시 쓴 단계는 응답을 가져온 작업공간의 출력과 비교해 `status/replay_fidelity.jsonl` 에 남긴다(`reference` 필드). Writer 를 다시 썼으면 그린 HTML 도 비교한다. `plan` 은 다시 쓴 호출 수를 작업공간별로 나눠 출력한다(`calls by source`).

2026-10-04 첫 재생성(75개)을 `--replay-from` 으로 준 `plan` 결과: 비교 분석 60회, 재무 18회, 통합 분석 6회를 첫 재생성의 응답으로 다시 쓰고, 출력 144개가 모두 같았다. Strategy 75회와 Writer 75회는 새로 부른다. Strategy 요청에 든 이익률 변화 항목의 키 순서가 실행할 때마다 달라질 수 있어(`Strategy_Agent/packet.py` 의 `_margin_changes` 가 집합 교집합 순서를 쓴다) 요청 해시가 맞지 않는다. Writer 요청은 Strategy 응답에 따라 정해지므로 함께 바뀐다.

## 결과 파일과 대응

`final_reports/r0N/<조건>/<기업>.html` 은 `ablation_results/repeated_standard_5companies/metrics.csv` 의 한 행(`company`, `condition`, `replicate`)에 대응한다. `report` 열은 생성 당시 경로이고 `report_sha256` 은 75개 파일과 모두 일치한다. 생성 당시 경로는 다음과 같다(`${ABLATION_WORKSPACE}` 는 생성 작업공간).

| 묶음 | 생성 당시 경로 |
|---|---|
| r01 네 조건 | `reports/<조건>/replicate_01/<기업>/report_<기업>.html` |
| r01 One-team | `reports/one_team/one_team_gpt54_r01/<기업>/report_<기업>.html` |
| r02·r03 | `reports/repeated_standard_5companies/replicate_0N/<조건>/<기업>/report_<기업>.html` |

`ablation_results/repeated_standard_5companies/` 는 8단계 산출물이다. 평균은 기업별 3회 평균이고, 표준편차는 표본 표준편차(n=3)다. 조건별 전체 평균의 표준편차는 회차마다 5개 기업 평균을 구한 뒤 그 3개 값으로 계산한다. `metrics.csv` 의 `offline_recovery` 열은 6단계에서 복구한 r03 One-team 아모레퍼시픽 보고서에만 값이 있다. 복구는 차트 2개의 근거 카드 목록만 고쳤고 본문은 바꾸지 않았다.

## 한계

- 기업마다 실제 애널리스트 보고서 1개만 참조로 쓴다. BERTScore 와 Judge 모두 이 한 보고서에 기댄다.
- Google News 수집 결과는 수집 시점과 외부 사이트 상태에 따라 달라진다. 수집을 다시 하면 같은 기사 집합이 나오지 않을 수 있다. 논문 결과를 다시 만들려면 동결 입력에서 시작한다.
- 언어 모델 응답은 같은 입력에서도 달라진다. 2단계 이후를 다시 돌려도 같은 HTML 을 얻지 못한다. 논문이 평가한 HTML 은 `final_reports/` 에 있다.
- `main` 의 코드는 생성 코드와 다르다. 위 "커밋" 절을 본다.
- Random news 표본은 기업마다 1개다. 3회 반복은 같은 표본에서의 생성 변동만 보여 준다.
- BERTScore 는 실제 보고서와의 의미 유사도다. 사실 정확성이나 투자 판단의 질을 직접 재지 않는다.
