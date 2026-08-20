#!/usr/bin/env bash

# Evaluate six companies x two ablations x three replicates with a union-blind Judge.
# Existing reports and archived Judge-visible snapshots are reused; only Judge calls are made.

set -uo pipefail

SCRIPT_PATH="$(readlink -f "${BASH_SOURCE[0]}")"
PROJECT_ROOT="$(dirname "$(dirname "${SCRIPT_PATH}")")"
PYTHON_BIN="${PYTHON_BIN:-python}"
JUDGE_MODEL="${JUDGE_MODEL:-gpt-5.4}"
EVALUATION_ID="paper_six_company_revised_nosy_judge_union_blind_v1"
OUTPUT_ROOT="${PROJECT_ROOT}/Output_total/Evaluation/Final_Report_Ablation"
OUTPUT_DIR="${OUTPUT_ROOT}/${EVALUATION_ID}"
JOB_ROOT="${PROJECT_ROOT}/Output_total/experiments/background/${EVALUATION_ID}"
LOG_FILE="${JOB_ROOT}/worker.log"
STATUS_FILE="${JOB_ROOT}/job.status"
PID_FILE="${JOB_ROOT}/worker.pid"
PROMPT_PATH="${PROJECT_ROOT}/src/orchestration/prompts/final_report_pairwise_judge_union_blind.md"

timestamp() { date --iso-8601=seconds; }

write_status() {
  local state="$1"
  local message="$2"
  mkdir -p "${JOB_ROOT}"
  printf '%s\t%s\t%s\n' "$(timestamp)" "${state}" "${message}" > "${STATUS_FILE}"
  printf '[%s] status=%s %s\n' "$(timestamp)" "${state}" "${message}"
}

worker_pid() {
  local pid=""
  if [[ -f "${PID_FILE}" ]]; then
    pid="$(tr -dc '0-9' < "${PID_FILE}")"
    if [[ -n "${pid}" ]] && kill -0 "${pid}" 2>/dev/null; then
      printf '%s' "${pid}"
      return 0
    fi
  fi
  pgrep -f -- "^bash ${SCRIPT_PATH} run$" | head -n 1 || true
}

run_worker() {
  cd "${PROJECT_ROOT}" || return 1
  export PYTHONPATH="${PROJECT_ROOT}/src${PYTHONPATH:+:${PYTHONPATH}}"
  write_status "running" "36 pairs, 72 cross-order union-blind judgments"

  if ! "${PYTHON_BIN}" -m orchestration.final_report_evaluation_cli \
    --suite-root "${PROJECT_ROOT}/Output_total/experiments/ablations/paper_skbiopharm_20251031_revised_nosy_ablation_v3" \
    --suite-root "${PROJECT_ROOT}/Output_total/experiments/ablations/paper_amorepacific_20251031_revised_nosy_ablation_v3" \
    --suite-root "${PROJECT_ROOT}/Output_total/experiments/ablations/paper_coway_20251031_revised_nosy_ablation_v4_revenuefix" \
    --suite-root "${PROJECT_ROOT}/Output_total/experiments/ablations/paper_hyundai_mobis_20251031_revised_nosy_ablation_v3" \
    --suite-root "${PROJECT_ROOT}/Output_total/experiments/ablations/paper_bgf_retail_20251031_revised_nosy_ablation_v3" \
    --suite-root "${PROJECT_ROOT}/Output_total/experiments/ablations/paper_s_oil_20251031_revised_nosy_ablation_v3" \
    --candidate-snapshot-root "${OUTPUT_ROOT}/paper_skbiopharm_20251031_revised_nosy_judge_v3" \
    --candidate-snapshot-root "${OUTPUT_ROOT}/paper_amorepacific_20251031_revised_nosy_judge_v3" \
    --candidate-snapshot-root "${OUTPUT_ROOT}/paper_coway_20251031_revised_nosy_judge_v4_revenuefix" \
    --candidate-snapshot-root "${OUTPUT_ROOT}/paper_hyundai_mobis_20251031_revised_nosy_judge_v3" \
    --candidate-snapshot-root "${OUTPUT_ROOT}/paper_bgf_retail_20251031_revised_nosy_judge_v3" \
    --candidate-snapshot-root "${OUTPUT_ROOT}/paper_s_oil_20251031_revised_nosy_judge_v3" \
    --baseline-condition full \
    --ablation no_peer \
    --ablation no_subdata \
    --judge-model "${JUDGE_MODEL}" \
    --prompt-path "${PROMPT_PATH}" \
    --evidence-mode union_blind \
    --output-root "${OUTPUT_ROOT}" \
    --evaluation-id "${EVALUATION_ID}"; then
    write_status "failed" "evaluation CLI returned non-zero; start again to reuse valid cached pairs"
    return 1
  fi

  if ! jq -e \
    '.status == "success" and .counts.planned_pairs == 36 and .counts.successful_pairs == 36 and .counts.failed_pairs == 0' \
    "${OUTPUT_DIR}/evaluation_summary.json" >/dev/null; then
    write_status "failed" "evaluation summary is incomplete"
    return 1
  fi
  write_status "success" "36 pairs and 72 union-blind judgments completed"
}

start_background() {
  mkdir -p "${JOB_ROOT}"
  local pid
  pid="$(worker_pid)"
  if [[ -n "${pid}" ]]; then
    printf '%s\n' "${pid}" > "${PID_FILE}"
    echo "Worker is already running (PID ${pid})."
    return 1
  fi
  nohup setsid bash "${SCRIPT_PATH}" run >> "${LOG_FILE}" 2>&1 < /dev/null &
  pid=$!
  printf '%s\n' "${pid}" > "${PID_FILE}"
  echo "Started union-blind Judge worker (PID ${pid})."
  echo "Status: ${SCRIPT_PATH} status"
  echo "Log: ${LOG_FILE}"
}

show_status() {
  local pid state="not running"
  pid="$(worker_pid)"
  if [[ -n "${pid}" ]]; then
    state="running"
    printf '%s\n' "${pid}" > "${PID_FILE}"
  fi
  echo "Worker: ${state}${pid:+ (PID ${pid})}"
  if [[ -f "${STATUS_FILE}" ]]; then
    echo "Job: $(tr '\t' ' ' < "${STATUS_FILE}")"
  fi
  if [[ -f "${OUTPUT_DIR}/experiment_manifest.json" ]]; then
    jq -r '"Pairs: \([.pairs[]? | select(.status == "success")] | length)/\(.planned_pairs // 36) successful"' \
      "${OUTPUT_DIR}/experiment_manifest.json"
  fi
  if [[ -f "${LOG_FILE}" ]]; then
    tail -n 20 "${LOG_FILE}"
  fi
}

case "${1:-}" in
  start) start_background ;;
  run) run_worker ;;
  status) show_status ;;
  *) echo "Usage: $(basename "${SCRIPT_PATH}") {start|run|status}"; exit 2 ;;
esac
