# Ablation 방법과 재현

## 실험 설정

현대건설, 두산, BGF리테일, 아모레퍼시픽, SK바이오팜에 대해 5개 조건을 3회 독립 생성했다. 조건·기업·회차당 보고서 1개, 총 75개다. 기업별 기준일과 비교기업은 [수집 명세](../run_config/collection_manifest.json)에 고정한다.

| 조건 | Full과 다른 점 |
|---|---|
| Full | 공시 기반 뉴스 선정, 영역 간 보조자료, 비교기업 분석을 모두 사용 |
| Random news | 기업 필터 전 중복 처리 후보에서 무작위 추출. 주간·월간 입력 건수는 Full과 맞춤 |
| No-subdata | 뉴스 주 자료 등 담당 영역 입력은 유지하고 다른 영역의 보조자료를 제거 |
| No-peer | 같은 회차 Full의 대상기업 하위 분석은 유지하고 비교기업 분석을 제거 |
| One-team | 영역별 전문 분석을 하나의 통합 분석 요청으로 대체. 비교·Strategy·Writer는 유지 |

하위 분석·비교·Strategy·Writer는 모든 조건에서 `gpt-5.4`를 사용했다. 월별 뉴스 요약은 `gpt-5.6-luna`로 만들고 동일 조건의 반복 및 One-team에서 재사용했다. Random news에는 Random이 선정한 기사의 요약을 사용한다. No-subdata는 보조 요약을 사용하지 않는다. Random 표본의 시드는 20251031이다.

기준일은 장 시작 전 시점으로, 뉴스·시장 자료와 공시는 기준일 전에 이용 가능한 자료만 사용한다. 투자 판단 기간은 향후 12개월이다. 실제 애널리스트 보고서는 평가 참조용이며 에이전트 생성 입력에 넣지 않는다.

## 코드와 원자료

실험 생성 코드 기준은 `paper-generation`, 수집 코드 기준은 `paper-collection` 태그다. 현재 코드로 새로 실행하면 출력과 표시가 달라질 수 있다. 입력, 모델, 실행 버전과 출력 해시는 실행별로 기록한다.

Git에는 코드·설정·공개 최종 보고서와 평가 집계가 있다. 다음 자료는 별도 준비가 필요하다.

- `collected_data/`, `prepared_inputs/`: 동결한 수집 자료와 조건별 입력.
- `reports/`, `status/`: 평가 당시 생성 결과와 실행 상태.
- `evaluation/`: 추출한 후보·참조 본문, Judge 입력 및 원응답.
- `references/`: 실제 애널리스트 PDF. 저작권 때문에 저장소에 포함하지 않는다.
- `configs/.env`: 개인 `OPENAI_API_KEY`, `DART_API_KEY`.

수집 명세의 경로는 저장소 루트 기준이며, 개인 경로를 코드에 직접 기록하지 않는다. 과거 동결 명세를 사용하려면 별도 작업공간에서 입력·해시 검사를 먼저 확인해야 한다. 공개 HTML만으로 모든 하위 분석을 재실행할 수는 없다.

## 실행 순서

아래 명령은 저장소 루트 기준이다. 실행 옵션은 각 실행기의 `--help`를 확인한다. 데이터가 필요한 명령은 준비된 개인 작업공간에서 실행하며, 생성 단계는 API 비용이 발생한다.

| 단계 | 실행기 | 역할 |
|---|---|---|
| 수집 | `ablation/run_config/collect_pre_llm_data.py` | DART·뉴스·시장 자료 수집. LLM 호출 없음 |
| 조건 준비 | `ablation/run_config/prepare_condition_inputs.py` | Full·Random·No-subdata의 동결 입력과 표본 명세 |
| r01 생성 | `ablation/run_config/run_prepared_reports.py` | Full·Random·No-subdata·No-peer |
| One-team | `ablation/run_config/run_one_team_reports.py` | 통합 분석 입력 준비와 후속 생성 |
| r02·r03 | `ablation/run_config/run_repeated_reports.py` | 동일 설정으로 추가 2회 생성 |
| BERTScore | `ablation/run_config/evaluate_report_bodies.py`, `evaluate_repeated_bert.py` | 서술 본문 추출·채점·3회 집계 |
| 일반 Judge | `ablation/run_config/final_report_llm_judge.py` | 순서 교환 쌍대비교 준비·검증·평가·집계 |
| NP Judge | `ablation/run_config/no_peer_target_llm_judge.py` | Full 대 No-peer의 NP1·NP2·NP3 평가 |

현재 코드에서 저장 응답을 재사용하는 실행기는 `ablation/run_config/rerun_ablation.py`다. 동일 요청 해시에만 응답을 재사용하고, 달라진 요청은 새 모델 호출로 처리한다. 기본 실행은 전체 75개이므로 소수 보고서 검증 후 전체 실행을 권장한다.

```bash
PYTHONPATH=src:ablation python ablation/run_config/rerun_ablation.py plan --source <개인_원자료_작업공간> --out <새_작업공간> --workers 1
PYTHONPATH=src:ablation python ablation/run_config/rerun_ablation.py run --source <개인_원자료_작업공간> --out <새_작업공간> --workers 1
PYTHONPATH=src:ablation python ablation/run_config/rerun_ablation.py collect --source <개인_원자료_작업공간> --out <새_작업공간>
```

## 평가와 공개 결과

BERTScore는 표·차트·시세 패널·고지문을 제외한 서술 본문에 대해 `BAAI/bge-m3`, 24개 층, IDF 및 재조정 없이 계산했다. 추출·채점 코드의 SHA-256은 [protocol.json](../results/repeated_standard_5companies/protocol.json)에 고정한다. 공개 결과의 점수와 평가 당시 해시는 변경하지 않는다.

[BERTScore 결과](../results/repeated_standard_5companies/평가결과.md)는 기업·조건별 3회 평균 및 표본 표준편차다. 조건 전체 평균의 표준편차는 회차별 5개 기업 평균을 구한 뒤 그 3개 값으로 계산한다.

일반 Judge는 후보 순서를 교환한 쌍대비교이며, 프롬프트·기준·집계 규칙은 [Judge 설명](../run_config/FINAL_REPORT_LLM_JUDGE.md)에 있다. NP1·NP2·NP3는 별도 생성 조건이 아니라 비교기업 효과를 평가하는 기준이다. 모두 `gpt-5.6-terra` 한 모델을 사용하며, [NP 평가 결과](../results/no_peer_target_llm_judge/README.md)에 정의·판정·집계가 있다.

NP 평가는 기준별 15쌍을 두 순서로 평가한 총 90호출이다. 두 순서 모두 같은 후보가 이겨야 Win/Loss, 그 밖에는 Tie다. 조정 승률은 `(Win + 0.5 × Tie) / 전체 쌍 수`다. Full의 결과는 NP1 80.0%, NP2 73.3%, NP3 80.0%다.

최종 보고서 경로는 `final_reports/r0N/<조건>/<기업>.html`이다. 평가 결과의 `report_sha256`은 평가 당시 HTML의 해시이며 현재 공개 파일 해시로 덮어쓰지 않는다. [report_manifest.json](../../final_reports/report_manifest.json)에 평가 해시, 공개 HTML 해시, 서술 본문 해시와 표시 지표의 기준을 대응시켰다. 공개 파일 자체의 변경을 새 실험 결과로 취급하지 않는다.

## 해석 범위

이 평가는 보고서의 의미적 유사도와 분석 품질을 다룬다. 투자등급 Accuracy나 사후 투자수익률을 검증한 결과가 아니다. 표본이 5개 기업·3회이고 Judge는 단일 모델이므로 다른 기업·시점·평가자에 대한 일반화에는 한계가 있다. 새 생성·평가는 기존 결과와 분리한다.
