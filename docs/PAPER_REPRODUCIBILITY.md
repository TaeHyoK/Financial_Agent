# 논문 실험 재현

이 문서는 논문의 6개 기업 구성요소 제외 실험과 단일 언어 모델 비교를 재현하는 최소 실행 순서를 정리합니다. 모든 명령은 저장소 루트에서 실행합니다.

## 준비

```bash
python -m venv .venv
source .venv/bin/activate
python -m pip install -e ".[dev]"
cp configs/.env.example configs/.env
```

`configs/.env`에 `OPENAI_API_KEY`와 `DART_API_KEY`를 설정합니다. 셸 스크립트의 상태 검증에는 `jq`가 필요합니다. 실행 결과와 로그는 Git에 포함되지 않는 `Output_total/` 아래에 저장됩니다.

## 1. 구성요소 제외 실험

먼저 API를 호출하지 않는 실행 계획을 확인합니다.

```bash
bash scripts/run_six_company_v3_background.sh plan
```

백그라운드 실행과 상태 확인:

```bash
bash scripts/run_six_company_v3_background.sh start
bash scripts/run_six_company_v3_background.sh status
```

`RUN_JUDGE=0`을 지정하면 보고서만 생성하고 유료 평가 호출은 생략합니다. 중단된 실행은 같은 명령으로 다시 시작하면 검증된 산출물을 재사용합니다.

## 2. 단일 언어 모델 비교

18개 입력의 경로와 크기를 API 호출 없이 점검한 뒤 실행합니다.

```bash
bash scripts/run_six_company_single_llm_v3_background.sh plan
bash scripts/run_six_company_single_llm_v3_background.sh preflight
bash scripts/run_six_company_single_llm_v3_background.sh start
bash scripts/run_six_company_single_llm_v3_background.sh status
```

## 3. 근거 합집합 블라인드 평가

구성요소 제외 보고서와 단일 언어 모델 보고서가 모두 생성된 뒤 각각 실행합니다.

```bash
bash scripts/run_six_company_union_blind_judge_background.sh start
bash scripts/run_six_company_union_blind_judge_background.sh status

bash scripts/run_six_company_single_llm_union_blind_judge_background.sh preflight
bash scripts/run_six_company_single_llm_union_blind_judge_background.sh start
bash scripts/run_six_company_single_llm_union_blind_judge_background.sh status
```

평가 스크립트는 두 보고서가 사용한 근거의 합집합을 평가 자료로 제공하고, 제시 순서를 바꾼 두 판정이 일치할 때만 승패로 확정합니다.

## 4. 논문용 결과 집계

```bash
PYTHONPATH=src python -m orchestration.revised_no_sy_aggregate --preset v3_coway_v4
PYTHONPATH=src python -m orchestration.union_blind_sensitivity
```

첫 번째 명령은 현재 6개 기업 결과를 집계하고, 두 번째 명령은 기존 평가와 근거 합집합 블라인드 평가의 민감도를 비교합니다. 단일 언어 모델 비교 결과는 2·3단계의 평가 디렉터리에 함께 기록됩니다.

실험 식별자와 6개 기업·비교기업 설정은 각 스크립트 상단에 고정되어 있습니다. 다른 기준일이나 기업을 사용할 때는 새 실험 식별자를 지정하여 기존 결과와 분리하십시오.
