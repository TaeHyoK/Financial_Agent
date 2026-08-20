#!/usr/bin/env bash

# Re-run only the Coway revised no-SY ablation after recognizing the DART label
# "매출" as revenue. The fixed v3 provider/News snapshot is reused, so this job
# makes no DART, yfinance, or News collection calls.

set -uo pipefail

SCRIPT_PATH="$(readlink -f "${BASH_SOURCE[0]}")"
PROJECT_ROOT="$(dirname "$(dirname "${SCRIPT_PATH}")")"
PYTHON_BIN="${PYTHON_BIN:-python}"
REPORT_MODEL="${REPORT_MODEL:-gpt-5.4-mini}"
JUDGE_MODEL="${JUDGE_MODEL:-gpt-5.4}"
REPLICATES="${REPLICATES:-3}"

VERSION="v4_revenuefix"
SELECTED_DATE="20251031"
SOURCE_SUITE_ID="paper_coway_${SELECTED_DATE}_source_ablation_${VERSION}"
REVISED_SUITE_ID="paper_coway_${SELECTED_DATE}_revised_nosy_ablation_${VERSION}"
EVALUATION_ID="paper_coway_${SELECTED_DATE}_revised_nosy_judge_${VERSION}"
ABLATION_ROOT="${PROJECT_ROOT}/Output_total/experiments/ablations"
SOURCE_SUITE_ROOT="${ABLATION_ROOT}/${SOURCE_SUITE_ID}"
REVISED_SUITE_ROOT="${ABLATION_ROOT}/${REVISED_SUITE_ID}"
EVALUATION_ROOT="${PROJECT_ROOT}/Output_total/Evaluation/Final_Report_Ablation/${EVALUATION_ID}"
REUSE_MANIFEST="${PROJECT_ROOT}/Output_total/experiments/fixed_domain_snapshots/coway_20251031_revenuefix_v4/reuse_manifest.json"

JOB_ROOT="${PROJECT_ROOT}/Output_total/experiments/background/coway_${VERSION}"
LOG_FILE="${JOB_ROOT}/worker.log"
STATUS_FILE="${JOB_ROOT}/job.status"
PID_FILE="${JOB_ROOT}/worker.pid"

timestamp() {
  date --iso-8601=seconds
}

write_status() {
  local phase="$1"
  local state="$2"
  local message="$3"
  mkdir -p "${JOB_ROOT}"
  printf '%s\t%s\t%s\t%s\n' "$(timestamp)" "${phase}" "${state}" "${message}" > "${STATUS_FILE}"
  printf '[%s] phase=%s status=%s %s\n' "$(timestamp)" "${phase}" "${state}" "${message}"
}

successful_count() {
  local summary_path="$1"
  local condition="$2"
  jq -r --arg condition "${condition}" \
    '[.runs[] | select(.condition == $condition and .status == "success")] | length' \
    "${summary_path}"
}

run_worker() {
  cd "${PROJECT_ROOT}" || return 1
  export PYTHONPATH="${PROJECT_ROOT}/src${PYTHONPATH:+:${PYTHONPATH}}"

  if [[ ! -f "${REUSE_MANIFEST}" ]]; then
    write_status "preflight" "failed" "fixed reuse manifest is missing"
    return 1
  fi

  write_status "source_conditions" "running" "reusing fixed v3 provider and News snapshot"
  local -a source_args=(
    --company-name "코웨이"
    --selected-date "${SELECTED_DATE}"
    --news-window 1m
    --news-event-top-k 40
    --decision-horizon-profile short_term
    --llm-model "${REPORT_MODEL}"
    --suite-id "${SOURCE_SUITE_ID}"
    --condition no_sy
    --condition no_sy_primary_only
    --replicates "${REPLICATES}"
    --reuse-collected-from "${REUSE_MANIFEST}"
    --no-progress
  )
  if [[ -f "${SOURCE_SUITE_ROOT}/ablation_suite_manifest.json" ]]; then
    source_args+=(--resume)
  fi
  if ! "${PYTHON_BIN}" -m orchestration.ablation_experiment "${source_args[@]}"; then
    write_status "source_conditions" "failed" "source ablation returned non-zero"
    return 1
  fi

  local source_summary="${SOURCE_SUITE_ROOT}/ablation_summary.json"
  if [[ ! -f "${source_summary}" ]] || \
     [[ "$(successful_count "${source_summary}" no_sy)" != "${REPLICATES}" ]] || \
     [[ "$(successful_count "${source_summary}" no_sy_primary_only)" != "${REPLICATES}" ]]; then
    write_status "source_conditions" "failed" "expected 3+3 successful source reports"
    return 1
  fi

  write_status "revised_ablation" "running" "building full, no-peer, and no-subdata reports"
  local -a revised_args=(
    --source-suite-root "${SOURCE_SUITE_ROOT}"
    --output-root "${ABLATION_ROOT}"
    --suite-id "${REVISED_SUITE_ID}"
    --replicates "${REPLICATES}"
    --llm-model "${REPORT_MODEL}"
    --decision-horizon-profile short_term
  )
  if [[ -f "${REVISED_SUITE_ROOT}/ablation_suite_manifest.json" ]]; then
    revised_args+=(--resume)
  fi
  if ! "${PYTHON_BIN}" -m orchestration.revised_no_sy_ablation "${revised_args[@]}"; then
    write_status "revised_ablation" "failed" "revised ablation returned non-zero"
    return 1
  fi

  local revised_summary="${REVISED_SUITE_ROOT}/ablation_summary.json"
  if [[ ! -f "${revised_summary}" ]] || \
     [[ "$(successful_count "${revised_summary}" full)" != "${REPLICATES}" ]] || \
     [[ "$(successful_count "${revised_summary}" no_peer)" != "${REPLICATES}" ]] || \
     [[ "$(successful_count "${revised_summary}" no_subdata)" != "${REPLICATES}" ]]; then
    write_status "revised_ablation" "failed" "expected 3x3 successful revised reports"
    return 1
  fi

  write_status "llm_judge" "running" "evaluating blind A/B and B/A pairs"
  if ! "${PYTHON_BIN}" -m orchestration.final_report_evaluation_cli \
    --suite-root "${REVISED_SUITE_ROOT}" \
    --baseline-condition full \
    --ablation no_peer \
    --ablation no_subdata \
    --judge-model "${JUDGE_MODEL}" \
    --evaluation-id "${EVALUATION_ID}"; then
    write_status "llm_judge" "failed" "judge returned non-zero"
    return 1
  fi

  if [[ ! -f "${EVALUATION_ROOT}/evaluation_summary.json" ]] || \
     ! jq -e '.status == "success" and .counts.successful_pairs == 6' \
       "${EVALUATION_ROOT}/evaluation_summary.json" >/dev/null; then
    write_status "llm_judge" "failed" "expected six successful evaluation pairs"
    return 1
  fi

  write_status "complete" "success" "Coway revenue-label rerun and Judge completed"
}

start_background() {
  mkdir -p "${JOB_ROOT}"
  local discovered_pid
  discovered_pid="$(pgrep -f -- "^bash ${SCRIPT_PATH} run$" | head -n 1 || true)"
  if [[ -n "${discovered_pid}" ]]; then
    printf '%s\n' "${discovered_pid}" > "${PID_FILE}"
    echo "Worker is already running (PID ${discovered_pid})."
    return 1
  fi
  if [[ -f "${PID_FILE}" ]]; then
    local existing_pid
    existing_pid="$(tr -dc '0-9' < "${PID_FILE}")"
    if [[ -n "${existing_pid}" ]] && kill -0 "${existing_pid}" 2>/dev/null; then
      echo "Worker is already running (PID ${existing_pid})."
      return 1
    fi
  fi

  # Start in a new session so closing VS Code or the launching terminal cannot
  # terminate the experiment's child processes.
  nohup setsid bash "${SCRIPT_PATH}" run >> "${LOG_FILE}" 2>&1 < /dev/null &
  local worker_pid=$!
  printf '%s\n' "${worker_pid}" > "${PID_FILE}"
  echo "Started Coway ${VERSION} worker (PID ${worker_pid})."
  echo "Status: ${SCRIPT_PATH} status"
  echo "Log: ${LOG_FILE}"
}

show_status() {
  local worker_state="not running"
  local worker_pid=""
  if [[ -f "${PID_FILE}" ]]; then
    worker_pid="$(tr -dc '0-9' < "${PID_FILE}")"
    if [[ -n "${worker_pid}" ]] && kill -0 "${worker_pid}" 2>/dev/null; then
      worker_state="running"
    fi
  fi
  if [[ "${worker_state}" != "running" ]]; then
    worker_pid="$(pgrep -f -- "^bash ${SCRIPT_PATH} run$" | head -n 1 || true)"
    if [[ -n "${worker_pid}" ]]; then
      worker_state="running"
      printf '%s\n' "${worker_pid}" > "${PID_FILE}"
    fi
  fi
  echo "Worker: ${worker_state}${worker_pid:+ (PID ${worker_pid})}"
  if [[ -f "${STATUS_FILE}" ]]; then
    echo "Job: $(tr '\t' ' ' < "${STATUS_FILE}")"
  fi
  if [[ -f "${LOG_FILE}" ]]; then
    tail -n 30 "${LOG_FILE}"
  fi
}

case "${1:-}" in
  start) start_background ;;
  run) run_worker ;;
  status) show_status ;;
  *) echo "Usage: $(basename "${SCRIPT_PATH}") {start|run|status}"; exit 2 ;;
esac
