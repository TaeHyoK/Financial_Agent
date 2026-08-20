#!/usr/bin/env bash

# Generate 18 Single-LLM baselines and compare them with Revised Full.

set -uo pipefail

SCRIPT_PATH="$(readlink -f "${BASH_SOURCE[0]}")"
SCRIPT_DIR="$(dirname "${SCRIPT_PATH}")"
PROJECT_ROOT="$(dirname "${SCRIPT_DIR}")"
PYTHON_BIN="${PYTHON_BIN:-python}"
EXPERIMENT_ID="${EXPERIMENT_ID:-paper_six_company_single_llm_gpt5_4_mini_v3}"
EVALUATION_ID="${EVALUATION_ID:-paper_revised_full_vs_single_llm_v3}"
SINGLE_MODEL="${SINGLE_MODEL:-gpt-5.4-mini}"
JUDGE_MODEL="${JUDGE_MODEL:-gpt-5.4}"
RUN_JUDGE="${RUN_JUDGE:-1}"

OUTPUT_ROOT="${PROJECT_ROOT}/Output_total"
SINGLE_ROOT="${OUTPUT_ROOT}/Single_LLM/${EXPERIMENT_ID}"
EVALUATION_ROOT="${OUTPUT_ROOT}/Evaluation/Final_Report_Single_LLM"
JOB_ROOT="${OUTPUT_ROOT}/experiments/background/six_company_single_llm_v3"
MASTER_LOG="${JOB_ROOT}/master.log"
PID_FILE="${JOB_ROOT}/worker.pid"
STATUS_FILE="${JOB_ROOT}/job.status"

timestamp() {
  date --iso-8601=seconds
}

ensure_dirs() {
  mkdir -p "${JOB_ROOT}"
}

write_status() {
  ensure_dirs
  printf '%s\t%s\t%s\n' "$(timestamp)" "$1" "$2" > "${STATUS_FILE}"
}

find_worker_pid() {
  local recorded_pid=""
  if [[ -f "${PID_FILE}" ]]; then
    recorded_pid="$(tr -dc '0-9' < "${PID_FILE}")"
    if [[ -n "${recorded_pid}" ]] && kill -0 "${recorded_pid}" 2>/dev/null; then
      printf '%s\n' "${recorded_pid}"
      return 0
    fi
  fi
  pgrep -f -x "bash ${SCRIPT_PATH} run" | head -n 1
}

revised_suite_args() {
  printf '%s\n' \
    --revised-suite-root "${OUTPUT_ROOT}/experiments/ablations/paper_skbiopharm_20251031_revised_nosy_ablation_v3" \
    --revised-suite-root "${OUTPUT_ROOT}/experiments/ablations/paper_amorepacific_20251031_revised_nosy_ablation_v3" \
    --revised-suite-root "${OUTPUT_ROOT}/experiments/ablations/paper_coway_20251031_revised_nosy_ablation_v3" \
    --revised-suite-root "${OUTPUT_ROOT}/experiments/ablations/paper_hyundai_mobis_20251031_revised_nosy_ablation_v3" \
    --revised-suite-root "${OUTPUT_ROOT}/experiments/ablations/paper_bgf_retail_20251031_revised_nosy_ablation_v3" \
    --revised-suite-root "${OUTPUT_ROOT}/experiments/ablations/paper_s_oil_20251031_revised_nosy_ablation_v3"
}

run_worker() {
  cd "${PROJECT_ROOT}" || return 1
  export PYTHONPATH="${PROJECT_ROOT}/src${PYTHONPATH:+:${PYTHONPATH}}"
  write_status "running_generation" "18 Single-LLM reports"
  echo "[$(timestamp)] single_llm generation start"
  if ! "${PYTHON_BIN}" -m orchestration.single_llm_six_company generate \
    --experiment-id "${EXPERIMENT_ID}" \
    --model "${SINGLE_MODEL}"; then
    write_status "generation_failed" "inspect ${MASTER_LOG}"
    return 1
  fi
  echo "[$(timestamp)] single_llm generation success"
  if [[ "${RUN_JUDGE}" == "0" ]]; then
    write_status "success_generation_only" "RUN_JUDGE=0"
    return 0
  fi
  write_status "running_judge" "18 pairs, A/B and B/A"
  local -a suite_args=()
  mapfile -t suite_args < <(revised_suite_args)
  if ! "${PYTHON_BIN}" -m orchestration.single_llm_evaluation \
    "${suite_args[@]}" \
    --single-experiment-root "${SINGLE_ROOT}" \
    --output-root "${EVALUATION_ROOT}" \
    --evaluation-id "${EVALUATION_ID}" \
    --judge-model "${JUDGE_MODEL}"; then
    write_status "judge_failed" "inspect ${MASTER_LOG}"
    return 1
  fi
  write_status "success" "generation and cross-order Judge complete"
  echo "[$(timestamp)] single_llm experiment success"
}

start_background() {
  ensure_dirs
  local existing_pid
  existing_pid="$(find_worker_pid)"
  if [[ -n "${existing_pid}" ]]; then
    echo "Single-LLM worker is already running (PID ${existing_pid})."
    return 1
  fi
  write_status "starting" "background worker requested"
  nohup setsid bash "${SCRIPT_PATH}" run \
    >> "${MASTER_LOG}" 2>&1 < /dev/null &
  local worker_pid=$!
  printf '%s\n' "${worker_pid}" > "${PID_FILE}"
  echo "Started Single-LLM v3 worker (PID ${worker_pid})."
  echo "Log: ${MASTER_LOG}"
  echo "Status: ${SCRIPT_PATH} status"
}

show_status() {
  ensure_dirs
  local worker_state="not running"
  local worker_pid=""
  worker_pid="$(find_worker_pid)"
  if [[ -n "${worker_pid}" ]]; then
    worker_state="running"
  fi
  echo "Worker: ${worker_state}${worker_pid:+ (PID ${worker_pid})}"
  if [[ -f "${STATUS_FILE}" ]]; then
    echo "Job: $(tr '\t' ' ' < "${STATUS_FILE}")"
  fi
  cd "${PROJECT_ROOT}" || return 1
  PYTHONPATH=src "${PYTHON_BIN}" -m orchestration.single_llm_six_company status \
    --experiment-id "${EXPERIMENT_ID}" || true
  local evaluation_summary="${EVALUATION_ROOT}/${EVALUATION_ID}/evaluation_summary.json"
  if [[ -f "${evaluation_summary}" ]]; then
    echo
    jq '{status,counts,overall:.aggregation.by_condition.single_llm.overall}' \
      "${evaluation_summary}"
  fi
  if [[ -f "${MASTER_LOG}" ]]; then
    echo
    echo "Recent log:"
    tail -n 30 "${MASTER_LOG}"
  fi
}

run_preflight() {
  cd "${PROJECT_ROOT}" || return 1
  export PYTHONPATH="${PROJECT_ROOT}/src${PYTHONPATH:+:${PYTHONPATH}}"
  "${PYTHON_BIN}" -m orchestration.single_llm_six_company build \
    --experiment-id "${EXPERIMENT_ID}" \
    --model "${SINGLE_MODEL}"
}

usage() {
  cat <<EOF
Usage: $(basename "${SCRIPT_PATH}") {plan|preflight|start|run|status}

  plan       Resolve six v3 source snapshots and fixed peers; no API calls.
  preflight  Build and size all 18 requests; no API calls.
  start      Run generation and Judge as a resumable background worker.
  run        Run generation and Judge in the foreground.
  status     Show report and Judge status.

Environment:
  RUN_JUDGE=0             Generate reports only.
  SINGLE_MODEL=...        Default: gpt-5.4-mini
  JUDGE_MODEL=...         Default: gpt-5.4
EOF
}

main() {
  case "${1:-}" in
    plan)
      cd "${PROJECT_ROOT}" || return 1
      PYTHONPATH=src "${PYTHON_BIN}" -m orchestration.single_llm_six_company plan
      ;;
    preflight)
      run_preflight
      ;;
    start)
      start_background
      ;;
    run)
      run_worker
      ;;
    status)
      show_status
      ;;
    *)
      usage
      return 2
      ;;
  esac
}

main "$@"
