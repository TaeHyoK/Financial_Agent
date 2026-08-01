#!/usr/bin/env bash
set -euo pipefail

PROJECT_ROOT="/home/agent2/Financial_Agent_Final_hyo"
SUITE_ID="paper_coway_20251031_ablation_v1"
EVALUATION_ID="paper_coway_20251031_judge_v1"
SUITE_ROOT="${PROJECT_ROOT}/Output_total/experiments/ablations/${SUITE_ID}"
SUITE_MANIFEST="${SUITE_ROOT}/ablation_suite_manifest.json"
FULL_EXECUTION_ID="${SUITE_ID}__full__r01"
SOURCE_MANIFEST="${SUITE_ROOT}/conditions/full/replicate_01/runs/코웨이_20251031/executions/${FULL_EXECUTION_ID}/full_pipeline_manifest.json"

cd "${PROJECT_ROOT}"
export PYTHONPATH="${PROJECT_ROOT}/src"

common_args=(
  --company-name "코웨이"
  --selected-date 20251031
  --news-window 1m
  --news-total-max-results 40
  --decision-horizon-profile short_term
  --llm-model gpt-5.4-mini
  --peer-stock-code 284740
  --suite-id "${SUITE_ID}"
  --no-progress
)

echo "[$(date --iso-8601=seconds)] phase=seed_full_r01 start"
if [[ -f "${SUITE_MANIFEST}" ]]; then
  PYTHONPATH=src python -m orchestration.ablation_experiment \
    "${common_args[@]}" \
    --condition full \
    --replicates 1 \
    --resume
else
  PYTHONPATH=src python -m orchestration.ablation_experiment \
    "${common_args[@]}" \
    --condition full \
    --replicates 1
fi

if [[ ! -f "${SOURCE_MANIFEST}" ]]; then
  echo "Missing successful Full r01 manifest: ${SOURCE_MANIFEST}" >&2
  exit 1
fi
if [[ "$(jq -r '.status // ""' "${SOURCE_MANIFEST}")" != "success" ]]; then
  echo "Full r01 did not succeed: ${SOURCE_MANIFEST}" >&2
  exit 1
fi
echo "[$(date --iso-8601=seconds)] phase=seed_full_r01 success"

echo "[$(date --iso-8601=seconds)] phase=ablation_matrix start"
PYTHONPATH=src python -m orchestration.ablation_experiment \
  "${common_args[@]}" \
  --condition full \
  --condition no_sy \
  --condition no_competitor \
  --condition primary_only \
  --replicates 3 \
  --reuse-collected-from "${SOURCE_MANIFEST}" \
  --resume

SUMMARY_PATH="${SUITE_ROOT}/ablation_summary.json"
if [[ "$(jq -r '.status // ""' "${SUMMARY_PATH}")" != "success" ]]; then
  echo "Ablation matrix did not succeed: ${SUMMARY_PATH}" >&2
  exit 1
fi
if [[ "$(jq -r '.counts.success // 0' "${SUMMARY_PATH}")" != "12" ]]; then
  echo "Ablation matrix did not produce 12 successful reports: ${SUMMARY_PATH}" >&2
  exit 1
fi
echo "[$(date --iso-8601=seconds)] phase=ablation_matrix success"

echo "[$(date --iso-8601=seconds)] phase=llm_judge start"
PYTHONPATH=src python -m orchestration.final_report_evaluation_cli \
  --suite-root "${SUITE_ROOT}" \
  --judge-model gpt-5.4 \
  --evaluation-id "${EVALUATION_ID}"
echo "[$(date --iso-8601=seconds)] phase=llm_judge success"
echo "[$(date --iso-8601=seconds)] job=complete"
