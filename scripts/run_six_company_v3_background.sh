#!/usr/bin/env bash

# Run the six-company revised no-SY experiment as one resumable background job.
#
# The worker is deliberately sequential.  Each company uses the GPU-heavy News
# embedding/reranking models and paid LLM calls, so parallel company execution
# would add contention without changing the experimental design.

set -uo pipefail

SCRIPT_PATH="$(readlink -f "${BASH_SOURCE[0]}")"
SCRIPT_DIR="$(dirname "${SCRIPT_PATH}")"
PROJECT_ROOT="$(dirname "${SCRIPT_DIR}")"

SELECTED_DATE="${SELECTED_DATE:-20251031}"
EXPERIMENT_VERSION="${EXPERIMENT_VERSION:-v3}"
NEWS_WINDOW="${NEWS_WINDOW:-1m}"
NEWS_EVENT_TOP_K="${NEWS_EVENT_TOP_K:-40}"
REPLICATES="${REPLICATES:-3}"
DECISION_HORIZON_PROFILE="${DECISION_HORIZON_PROFILE:-short_term}"
REPORT_MODEL="${REPORT_MODEL:-gpt-5.4-mini}"
JUDGE_MODEL="${JUDGE_MODEL:-gpt-5.4}"
RUN_JUDGE="${RUN_JUDGE:-1}"
ONLY_COMPANY="${ONLY_COMPANY:-}"
PYTHON_BIN="${PYTHON_BIN:-python}"

ABLATION_ROOT="${PROJECT_ROOT}/Output_total/experiments/ablations"
EVALUATION_ROOT="${PROJECT_ROOT}/Output_total/Evaluation/Final_Report_Ablation"
JOB_ROOT="${PROJECT_ROOT}/Output_total/experiments/background/six_company_revised_nosy_${EXPERIMENT_VERSION}"
STATUS_DIR="${JOB_ROOT}/status"
COMPANY_LOG_DIR="${JOB_ROOT}/company_logs"
MASTER_LOG="${JOB_ROOT}/master.log"
EVENTS_FILE="${JOB_ROOT}/events.tsv"
JOB_STATUS_FILE="${JOB_ROOT}/job.status"
PID_FILE="${JOB_ROOT}/worker.pid"

# slug | exact OpenDART company name | optional target-only Google News query
COMPANY_SPECS=(
  "skbiopharm|SK바이오팜|"
  "amorepacific|아모레퍼시픽|"
  "coway|코웨이|"
  "hyundai_mobis|현대모비스|"
  "bgf_retail|BGF리테일|"
  "s_oil|S-OIL|(\"S-OIL\" OR \"에쓰오일\" OR \"에스오일\")"
)

timestamp() {
  date --iso-8601=seconds
}

ensure_job_dirs() {
  mkdir -p "${JOB_ROOT}" "${STATUS_DIR}" "${COMPANY_LOG_DIR}"
}

write_job_status() {
  local state="$1"
  local message="$2"
  ensure_job_dirs
  printf '%s\t%s\t%s\n' "$(timestamp)" "${state}" "${message}" > "${JOB_STATUS_FILE}"
  printf '%s\tjob\t-\t%s\t%s\n' "$(timestamp)" "${state}" "${message}" >> "${EVENTS_FILE}"
}

write_company_status() {
  local slug="$1"
  local company="$2"
  local phase="$3"
  local state="$4"
  local message="$5"
  ensure_job_dirs
  printf '%s\t%s\t%s\t%s\t%s\t%s\n' \
    "$(timestamp)" "${slug}" "${company}" "${phase}" "${state}" "${message}" \
    > "${STATUS_DIR}/${slug}.status"
  printf '%s\t%s\t%s\t%s\t%s\n' \
    "$(timestamp)" "${slug}" "${phase}" "${state}" "${message}" \
    >> "${EVENTS_FILE}"
}

require_commands() {
  local command_name
  for command_name in "${PYTHON_BIN}" jq nohup setsid tee; do
    if ! command -v "${command_name}" >/dev/null 2>&1; then
      echo "Required command is unavailable: ${command_name}" >&2
      return 1
    fi
  done
}

condition_success_count() {
  local summary_path="$1"
  local condition="$2"
  jq -r --arg condition "${condition}" \
    '[.runs[] | select(.condition == $condition and .status == "success")] | length' \
    "${summary_path}"
}

successful_seed_manifest() {
  local suite_manifest="$1"
  [[ -f "${suite_manifest}" ]] || return 0
  jq -r '
    [
      .runs[]?
      | select(
          .condition == "no_sy"
          and .replicate == 1
          and .status == "success"
          and ((.pipeline_manifest // "") | length > 0)
        )
    ]
    | sort_by(.attempt // 1)
    | if length == 0 then "" else .[-1].pipeline_manifest end
  ' "${suite_manifest}"
}

run_company() {
  local slug="$1"
  local company="$2"
  local target_news_query="$3"

  local source_suite_id="paper_${slug}_${SELECTED_DATE}_source_ablation_${EXPERIMENT_VERSION}"
  local revised_suite_id="paper_${slug}_${SELECTED_DATE}_revised_nosy_ablation_${EXPERIMENT_VERSION}"
  local evaluation_id="paper_${slug}_${SELECTED_DATE}_revised_nosy_judge_${EXPERIMENT_VERSION}"
  local source_suite_root="${ABLATION_ROOT}/${source_suite_id}"
  local revised_suite_root="${ABLATION_ROOT}/${revised_suite_id}"
  local source_suite_manifest="${source_suite_root}/ablation_suite_manifest.json"
  local source_summary="${source_suite_root}/ablation_summary.json"
  local revised_manifest="${revised_suite_root}/ablation_suite_manifest.json"
  local revised_summary="${revised_suite_root}/ablation_summary.json"
  local evaluation_summary="${EVALUATION_ROOT}/${evaluation_id}/evaluation_summary.json"
  local source_manifest=""

  local -a common_args=(
    --company-name "${company}"
    --selected-date "${SELECTED_DATE}"
    --news-window "${NEWS_WINDOW}"
    --news-event-top-k "${NEWS_EVENT_TOP_K}"
    --decision-horizon-profile "${DECISION_HORIZON_PROFILE}"
    --llm-model "${REPORT_MODEL}"
    --suite-id "${source_suite_id}"
    --no-progress
  )
  if [[ -n "${target_news_query}" ]]; then
    common_args+=(--target-news-query "${target_news_query}")
  fi

  echo "[$(timestamp)] company=${company} suite=${revised_suite_id} start"
  write_company_status "${slug}" "${company}" "seed_no_sy" "running" "fresh collection, revised baseline, and automatic peer selection"

  local -a seed_args=(
    "${common_args[@]}"
    --condition no_sy
    --replicates 1
  )
  if [[ -f "${source_suite_manifest}" ]]; then
    seed_args+=(--resume)
  fi
  if ! "${PYTHON_BIN}" -m orchestration.ablation_experiment "${seed_args[@]}"; then
    write_company_status "${slug}" "${company}" "seed_no_sy" "failed" "ablation_experiment returned non-zero"
    return 1
  fi

  source_manifest="$(successful_seed_manifest "${source_suite_manifest}")"
  if [[ -z "${source_manifest}" || ! -f "${source_manifest}" ]]; then
    write_company_status "${slug}" "${company}" "seed_no_sy" "failed" "successful no_sy r01 manifest not found"
    return 1
  fi
  if ! jq -e '.status == "success"' "${source_manifest}" >/dev/null; then
    write_company_status "${slug}" "${company}" "seed_no_sy" "failed" "no_sy r01 manifest is not successful"
    return 1
  fi

  local peer_name
  local peer_code
  peer_name="$(jq -r '.peer.company_name // ""' "${source_manifest}")"
  peer_code="$(jq -r '.peer.stock_code // ""' "${source_manifest}")"
  write_company_status \
    "${slug}" "${company}" "seed_no_sy" "success" \
    "automatic peer=${peer_name:-unknown}(${peer_code:-unknown}); snapshot fixed"

  write_company_status "${slug}" "${company}" "source_conditions" "running" "no_sy and no_sy_primary_only, three replicates"
  if ! "${PYTHON_BIN}" -m orchestration.ablation_experiment \
    "${common_args[@]}" \
    --condition no_sy \
    --condition no_sy_primary_only \
    --replicates "${REPLICATES}" \
    --reuse-collected-from "${source_manifest}" \
    --resume; then
    write_company_status "${slug}" "${company}" "source_conditions" "failed" "source conditions returned non-zero"
    return 1
  fi
  if [[ ! -f "${source_summary}" ]]; then
    write_company_status "${slug}" "${company}" "source_conditions" "failed" "source summary missing"
    return 1
  fi
  if [[ "$(condition_success_count "${source_summary}" no_sy)" != "${REPLICATES}" ]]; then
    write_company_status "${slug}" "${company}" "source_conditions" "failed" "no_sy replicate count mismatch"
    return 1
  fi
  if [[ "$(condition_success_count "${source_summary}" no_sy_primary_only)" != "${REPLICATES}" ]]; then
    write_company_status "${slug}" "${company}" "source_conditions" "failed" "no_sy_primary_only replicate count mismatch"
    return 1
  fi
  write_company_status "${slug}" "${company}" "source_conditions" "success" "fixed snapshot reused"

  write_company_status "${slug}" "${company}" "revised_ablation" "running" "full=no_sy; no_peer and no_subdata"
  local -a revised_args=(
    --source-suite-root "${source_suite_root}"
    --output-root "${ABLATION_ROOT}"
    --suite-id "${revised_suite_id}"
    --replicates "${REPLICATES}"
    --llm-model "${REPORT_MODEL}"
    --decision-horizon-profile "${DECISION_HORIZON_PROFILE}"
  )
  if [[ -f "${revised_manifest}" ]]; then
    revised_args+=(--resume)
  fi
  if ! "${PYTHON_BIN}" -m orchestration.revised_no_sy_ablation "${revised_args[@]}"; then
    write_company_status "${slug}" "${company}" "revised_ablation" "failed" "revised suite returned non-zero"
    return 1
  fi
  if [[ ! -f "${revised_summary}" ]]; then
    write_company_status "${slug}" "${company}" "revised_ablation" "failed" "revised summary missing"
    return 1
  fi
  if [[ "$(condition_success_count "${revised_summary}" full)" != "${REPLICATES}" || \
        "$(condition_success_count "${revised_summary}" no_peer)" != "${REPLICATES}" || \
        "$(condition_success_count "${revised_summary}" no_subdata)" != "${REPLICATES}" ]]; then
    write_company_status "${slug}" "${company}" "revised_ablation" "failed" "expected 3x3 successful reports"
    return 1
  fi
  write_company_status "${slug}" "${company}" "revised_ablation" "success" "nine reports ready"

  if [[ "${RUN_JUDGE}" == "1" ]]; then
    if [[ -f "${evaluation_summary}" ]] && \
       jq -e '.status == "success" and .counts.successful_pairs == 6' "${evaluation_summary}" >/dev/null; then
      write_company_status "${slug}" "${company}" "llm_judge" "success" "existing six-pair evaluation reused"
    else
      write_company_status "${slug}" "${company}" "llm_judge" "running" "blind A/B and B/A evaluation"
      if ! "${PYTHON_BIN}" -m orchestration.final_report_evaluation_cli \
        --suite-root "${revised_suite_root}" \
        --baseline-condition full \
        --ablation no_peer \
        --ablation no_subdata \
        --judge-model "${JUDGE_MODEL}" \
        --evaluation-id "${evaluation_id}"; then
        write_company_status "${slug}" "${company}" "llm_judge" "failed" "evaluation CLI returned non-zero"
        return 1
      fi
      if [[ ! -f "${evaluation_summary}" ]] || \
         ! jq -e '.status == "success" and .counts.successful_pairs == 6' "${evaluation_summary}" >/dev/null; then
        write_company_status "${slug}" "${company}" "llm_judge" "failed" "six successful pairs were not produced"
        return 1
      fi
      write_company_status "${slug}" "${company}" "llm_judge" "success" "six pairs, both presentation orders"
    fi
  else
    write_company_status "${slug}" "${company}" "llm_judge" "skipped" "RUN_JUDGE=0"
  fi

  write_company_status "${slug}" "${company}" "complete" "success" "all requested phases complete"
  echo "[$(timestamp)] company=${company} complete"
  return 0
}

print_plan() {
  printf 'slug\tcompany\tsource suite\trevised suite\tjudge id\n'
  local spec slug company query
  for spec in "${COMPANY_SPECS[@]}"; do
    IFS='|' read -r slug company query <<< "${spec}"
    if [[ -n "${ONLY_COMPANY}" && "${slug}" != "${ONLY_COMPANY}" ]]; then
      continue
    fi
    printf '%s\t%s\tpaper_%s_%s_source_ablation_%s\tpaper_%s_%s_revised_nosy_ablation_%s\tpaper_%s_%s_revised_nosy_judge_%s\n' \
      "${slug}" "${company}" \
      "${slug}" "${SELECTED_DATE}" "${EXPERIMENT_VERSION}" \
      "${slug}" "${SELECTED_DATE}" "${EXPERIMENT_VERSION}" \
      "${slug}" "${SELECTED_DATE}" "${EXPERIMENT_VERSION}"
  done
}

worker_main() {
  ensure_job_dirs
  if ! require_commands; then
    write_job_status "failed" "required command check failed"
    return 1
  fi
  if [[ "${RUN_JUDGE}" != "0" && "${RUN_JUDGE}" != "1" ]]; then
    write_job_status "failed" "RUN_JUDGE must be 0 or 1"
    return 1
  fi

  cd "${PROJECT_ROOT}" || return 1
  export PYTHONPATH="${PROJECT_ROOT}/src${PYTHONPATH:+:${PYTHONPATH}}"
  write_job_status "running" "six-company sequential worker started"
  echo "[$(timestamp)] job=start version=${EXPERIMENT_VERSION} date=${SELECTED_DATE}"
  print_plan

  local -a failures=()
  local spec slug company query company_log
  for spec in "${COMPANY_SPECS[@]}"; do
    IFS='|' read -r slug company query <<< "${spec}"
    if [[ -n "${ONLY_COMPANY}" && "${slug}" != "${ONLY_COMPANY}" ]]; then
      continue
    fi
    company_log="${COMPANY_LOG_DIR}/${slug}.log"
    if run_company "${slug}" "${company}" "${query}" > >(tee -a "${company_log}") 2>&1; then
      :
    else
      failures+=("${slug}")
      echo "[$(timestamp)] company=${company} failed; continuing with the next company" >&2
    fi
  done

  if (( ${#failures[@]} > 0 )); then
    local failure_list
    failure_list="$(IFS=,; printf '%s' "${failures[*]}")"
    write_job_status "complete_with_failures" "failed companies=${failure_list}; rerun start to resume"
    echo "[$(timestamp)] job=complete_with_failures companies=${failure_list}" >&2
    return 1
  fi

  write_job_status "success" "all selected companies completed"
  echo "[$(timestamp)] job=success"
  return 0
}

start_background() {
  ensure_job_dirs
  if [[ -f "${PID_FILE}" ]]; then
    local existing_pid
    existing_pid="$(tr -dc '0-9' < "${PID_FILE}")"
    if [[ -n "${existing_pid}" ]] && kill -0 "${existing_pid}" 2>/dev/null; then
      echo "The v3 worker is already running (PID ${existing_pid})."
      echo "Check it with: ${SCRIPT_PATH} status"
      return 1
    fi
  fi

  write_job_status "starting" "background worker requested"
  nohup setsid --fork --wait bash "${SCRIPT_PATH}" run \
    >> "${MASTER_LOG}" 2>&1 < /dev/null &
  local worker_pid=$!
  printf '%s\n' "${worker_pid}" > "${PID_FILE}"
  echo "Started six-company ${EXPERIMENT_VERSION} worker in the background."
  echo "PID: ${worker_pid}"
  echo "Log: ${MASTER_LOG}"
  echo "Status: ${SCRIPT_PATH} status"
}

show_status() {
  ensure_job_dirs
  local worker_state="not running"
  local worker_pid=""
  if [[ -f "${PID_FILE}" ]]; then
    worker_pid="$(tr -dc '0-9' < "${PID_FILE}")"
    if [[ -n "${worker_pid}" ]] && kill -0 "${worker_pid}" 2>/dev/null; then
      worker_state="running"
    fi
  fi
  echo "Worker: ${worker_state}${worker_pid:+ (PID ${worker_pid})}"
  if [[ -f "${JOB_STATUS_FILE}" ]]; then
    echo "Job: $(tr '\t' ' ' < "${JOB_STATUS_FILE}")"
  fi
  echo
  printf '%-18s %-18s %-20s %-10s %s\n' "slug" "company" "phase" "state" "updated"
  local status_path updated slug company phase state message
  for status_path in "${STATUS_DIR}"/*.status; do
    [[ -e "${status_path}" ]] || continue
    IFS=$'\t' read -r updated slug company phase state message < "${status_path}"
    printf '%-18s %-18s %-20s %-10s %s\n' "${slug}" "${company}" "${phase}" "${state}" "${updated}"
    if [[ -n "${message}" ]]; then
      printf '  %s\n' "${message}"
    fi
  done
  if [[ -f "${MASTER_LOG}" ]]; then
    echo
    echo "Recent log:"
    tail -n 30 "${MASTER_LOG}"
  fi
}

usage() {
  cat <<EOF
Usage: $(basename "${SCRIPT_PATH}") {start|run|status|plan}

  start   Launch the resumable six-company worker with nohup + setsid.
  run     Run in the foreground (used internally by start).
  status  Show PID, per-company phase/status, and the recent master log.
  plan    Print suite/evaluation IDs without calling any API.

Optional environment variables:
  RUN_JUDGE=0            Generate reports only; skip paid LLM Judge calls.
  ONLY_COMPANY=s_oil     Run or resume one company slug only.
  REPORT_MODEL=...       Default: gpt-5.4-mini
  JUDGE_MODEL=...        Default: gpt-5.4
  SELECTED_DATE=YYYYMMDD Default: 20251031
EOF
}

main() {
  local action="${1:-}"
  case "${action}" in
    start)
      start_background
      ;;
    run)
      worker_main
      ;;
    status)
      show_status
      ;;
    plan)
      print_plan
      ;;
    *)
      usage
      return 2
      ;;
  esac
}

main "$@"
