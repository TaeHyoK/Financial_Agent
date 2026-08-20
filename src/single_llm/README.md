# Single-LLM Direct Baseline

현재 멀티에이전트 최종 보고서와 비교하기 위한 독립 baseline입니다. Target과 고정 Peer의 동결된 원천·결정론적 데이터를 한 모델에 제공하고, 최종 보고서 JSON을 한 번의 의미 생성 호출로 만듭니다.

## 실험 계약

- 기본 모델: `gpt-4.1`
- 의미 생성: 보고서당 1회
- 기본 transport retry: 0회
- 입력 목표/상한: 75K/90K tokens
- 출력 상한: 12K tokens
- 기준일 정책: 장 시작 전, 모든 실질 evidence는 `< selected_date`
- 출력: strict Structured Outputs JSON
- 검증: evidence ID, point-in-time, 수치 grounding, 필수 섹션
- 렌더링: 검증 후 결정론적 HTML 생성

포함하는 기존 artifact:

```text
Output_total/runs/<run_key>/run_config.json
Output_total/Financial/<run_key>/dart_main.json
Output_total/News/<run_key>/output/news_agent_evidence_map.json
Output_total/Y_Finance/<run_key>/market_full_dataset.json
Output_total/Y_Finance/<run_key>/valuation_snapshot.json
```

제외하는 입력:

- Financial/News/YFinance Agent 자연어 보고서
- SY 검증 서술
- Strategy packet과 투자 의견
- Writer 입력과 최종 보고서
- 기존 최종 보고서에서 생성한 Peer 비교 결과

Target과 Peer의 원천 데이터를 각각 직접 포함하므로 모델이 지정된 두 회사를 비교합니다. 선정 Peer 하나를 업종 평균으로 일반화하는 것은 금지됩니다.

## 명령 구조

### 1. 요청 번들만 준비

LLM을 호출하지 않습니다. 실제 프롬프트와 JSON schema까지 포함한 토큰 수를 계산하고 입력을 동결합니다.

```bash
PYTHONPATH=src python -m single_llm.cli build \
  --target-run-key SK바이오팜_20251031 \
  --peer-run-key 일성아이에스_20251031 \
  --experiment-id single_llm_gpt4_1_v1 \
  --replicate 1
```

원천 artifact와 Single-LLM 출력 위치를 분리할 때는 원천 snapshot을
`--source-root`로 지정하고, 실험 출력 상위 경로를 `--output-root`로 지정합니다.

### 2. Single-LLM 보고서 생성

`generate`만 OpenAI API를 호출합니다. 기본 설정에서는 semantic call과 transport attempt 모두 정확히 한 번입니다.

```bash
PYTHONPATH=src python -m single_llm.cli generate \
  --target-run-key SK바이오팜_20251031 \
  --peer-run-key 일성아이에스_20251031 \
  --experiment-id single_llm_gpt4_1_v1 \
  --replicate 1
```

반복 실험은 `--replicate 1`, `2`, `3`으로 분리합니다. 같은 위치가 이미 비어 있지 않으면 중단하며, 정확히 그 산출물 세트를 교체할 때만 `--overwrite`를 사용합니다.

### 3. 기존 응답 재검증

```bash
PYTHONPATH=src python -m single_llm.cli validate \
  --report Output_total/Single_LLM/single_llm_gpt4_1_v1/SK바이오팜_20251031/r01/report.json \
  --bundle Output_total/Single_LLM/single_llm_gpt4_1_v1/SK바이오팜_20251031/r01/input_bundle.json
```

## 입력 예산 처리

전체 request가 75K tokens를 넘으면 다음 순서로만 축소합니다.

1. 재무·시장·밸류에이션 evidence는 유지합니다.
2. 각 회사의 뉴스는 최소 10개를 유지합니다.
3. 남은 뉴스 중 `final_score`가 가장 낮은 항목을 먼저 제거합니다.
4. 동점이면 오래된 뉴스, evidence ID 순으로 제거합니다.
5. 그래도 90K를 넘으면 API 호출 전에 실패합니다.

이 규칙과 제거된 뉴스 ID는 `request_budget.json`과 `input_bundle.json`에 기록됩니다. LLM을 이용한 사전 뉴스 요약이나 선별은 하지 않습니다.

## 산출물

```text
Output_total/Single_LLM/<experiment_id>/<target_run_key>/r01/
├── config_resolved.json
├── source_manifest.json
├── temporal_validation.json
├── input_bundle.json
├── request.json
├── request_budget.json
├── llm_usage_manifest.jsonl       # generate에서만 생성
├── report.json                    # generate에서만 생성
├── validation.json                # generate/validate에서 생성
├── report.html                    # 모든 검증 통과 시에만 생성
└── run_manifest.json
```

`report.html`에는 내부 evidence ID가 노출되지 않습니다. 평가 시 기존 `orchestration.final_report_evaluation_bundle.extract_visible_report`가 읽을 수 있도록 `.a4-sheet`, `.report-name`, `.meta-grid`와 `<section>` 구조를 사용합니다.

## 비용 기록

사용 토큰은 실제 API 응답에서 기록합니다. 비용은 `configs/gpt4_1.yaml`에 명시한 가격 snapshot으로 계산하므로, 가격이 변경되면 YAML의 가격과 `as_of`를 함께 수정해야 합니다. 데이터 수집, pairwise Judge와 인간 평가는 이 생성 비용에 포함되지 않습니다.

## 검증 실패 정책

의미 repair 호출은 하지 않습니다. 응답은 다음 순서로 처리합니다.

1. strict JSON schema 응답 수신
2. 회사·기준일·판단 기간 확인
3. 존재하지 않는 evidence ID 차단
4. 참조 evidence 내 숫자 grounding 확인
5. 검증 통과 시에만 HTML 생성

실패한 JSON과 `validation.json`은 진단을 위해 보존하지만 비교 평가의 유효 후보로 사용하지 않습니다.
