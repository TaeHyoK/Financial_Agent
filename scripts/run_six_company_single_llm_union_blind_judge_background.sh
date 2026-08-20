#!/usr/bin/env bash

# Re-evaluate the frozen six-company Revised Full vs Single-LLM reports.
# No reports or news are regenerated; only 18 pairs x A/B and B/A are judged.

set -uo pipefail

SCRIPT_PATH="$(readlink -f "${BASH_SOURCE[0]}")"
PROJECT_ROOT="$(dirname "$(dirname "${SCRIPT_PATH}")")"
PYTHON_BIN="${PYTHON_BIN:-python}"
JUDGE_MODEL="${JUDGE_MODEL:-gpt-5.4}"
EVALUATION_ID="paper_revised_full_vs_single_llm_union_blind_v1"
DRY_EVALUATION_ID="paper_revised_full_vs_single_llm_union_blind_dryrun_v1"
OUTPUT_ROOT="${PROJECT_ROOT}/Output_total/Evaluation/Final_Report_Single_LLM"
OUTPUT_DIR="${OUTPUT_ROOT}/${EVALUATION_ID}"
SINGLE_ROOT="${PROJECT_ROOT}/Output_total/Single_LLM/paper_six_company_single_llm_gpt5_4_mini_v3"
SNAPSHOT_ROOT="${OUTPUT_ROOT}/paper_revised_full_vs_single_llm_v3"
PROMPT_PATH="${PROJECT_ROOT}/src/orchestration/prompts/final_report_pairwise_judge_union_blind.md"
JOB_ROOT="${PROJECT_ROOT}/Output_total/experiments/background/${EVALUATION_ID}"
LOG_FILE="${JOB_ROOT}/worker.log"
STATUS_FILE="${JOB_ROOT}/job.status"
PID_FILE="${JOB_ROOT}/worker.pid"

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

suite_args() {
  printf '%s\n' \
    --revised-suite-root "${PROJECT_ROOT}/Output_total/experiments/ablations/paper_skbiopharm_20251031_revised_nosy_ablation_v3" \
    --revised-suite-root "${PROJECT_ROOT}/Output_total/experiments/ablations/paper_amorepacific_20251031_revised_nosy_ablation_v3" \
    --revised-suite-root "${PROJECT_ROOT}/Output_total/experiments/ablations/paper_coway_20251031_revised_nosy_ablation_v3" \
    --revised-suite-root "${PROJECT_ROOT}/Output_total/experiments/ablations/paper_hyundai_mobis_20251031_revised_nosy_ablation_v3" \
    --revised-suite-root "${PROJECT_ROOT}/Output_total/experiments/ablations/paper_bgf_retail_20251031_revised_nosy_ablation_v3" \
    --revised-suite-root "${PROJECT_ROOT}/Output_total/experiments/ablations/paper_s_oil_20251031_revised_nosy_ablation_v3"
}

run_evaluation() {
  local evaluation_id="$1"
  shift
  local -a suites=()
  mapfile -t suites < <(suite_args)
  "${PYTHON_BIN}" -m orchestration.single_llm_evaluation \
    "${suites[@]}" \
    --single-experiment-root "${SINGLE_ROOT}" \
    --candidate-snapshot-root "${SNAPSHOT_ROOT}" \
    --output-root "${OUTPUT_ROOT}" \
    --evaluation-id "${evaluation_id}" \
    --judge-model "${JUDGE_MODEL}" \
    --prompt-path "${PROMPT_PATH}" \
    --evidence-mode union_blind \
    "$@"
}

run_worker() {
  cd "${PROJECT_ROOT}" || return 1
  export PYTHONPATH="${PROJECT_ROOT}/src${PYTHONPATH:+:${PYTHONPATH}}"
  write_status "running" "18 frozen pairs, 36 cross-order union-blind judgments"
  if ! run_evaluation "${EVALUATION_ID}"; then
    write_status "failed" "evaluation returned non-zero; rerun to reuse valid caches"
    return 1
  fi
  if ! jq -e \
    '.status == "success" and .counts.planned_pairs == 18 and .counts.successful_pairs == 18 and .counts.failed_pairs == 0' \
    "${OUTPUT_DIR}/evaluation_summary.json" >/dev/null; then
    write_status "failed" "evaluation summary is incomplete"
    return 1
  fi
  write_status "success" "18 pairs and 36 union-blind judgments completed"
}

run_preflight() {
  cd "${PROJECT_ROOT}" || return 1
  export PYTHONPATH="${PROJECT_ROOT}/src${PYTHONPATH:+:${PYTHONPATH}}"
  run_evaluation "${DRY_EVALUATION_ID}" --dry-run
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
  echo "Started Single-LLM union-blind Judge worker (PID ${pid})."
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
    jq -r '"Pairs: \([.pairs[]? | select(.status == \"success\")] | length)/\(.planned_pairs // 18) successful"' \
      "${OUTPUT_DIR}/experiment_manifest.json"
  fi
  if [[ -f "${LOG_FILE}" ]]; then
    tail -n 20 "${LOG_FILE}"
  fi
}

case "${1:-}" in
  preflight) run_preflight ;;
  start) start_background ;;
  run) run_worker ;;
  status) show_status ;;
  *) echo "Usage: $(basename "${SCRIPT_PATH}") {preflight|start|run|status}"; exit 2 ;;
esac
