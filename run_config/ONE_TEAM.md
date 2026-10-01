# One-team integration

## Paper setting

The paper's r01 One-team reports come from the model-matched gpt-5.4 run.
Run from the repository root at tag `paper-generation` (commit `da85eb3`),
after restoring the frozen inputs (`prepared_inputs/`, `reports/`, `status/`)
from outside Git:

```bash
python run_config/run_one_team_reports.py prepare --model gpt-5.4 --run-id one_team_gpt54_r01
python run_config/run_one_team_reports.py check --model gpt-5.4 --run-id one_team_gpt54_r01
python run_config/run_one_team_reports.py run --model gpt-5.4 --run-id one_team_gpt54_r01
# Resume an interrupted run with the same flags:
python run_config/run_one_team_reports.py resume --model gpt-5.4 --run-id one_team_gpt54_r01
```

Integrated analysis, Comparison, Strategy and Writer all use gpt-5.4, the same
analysis model as the other four conditions. `run` and `resume` read the API
key from `--env-file` (default: the `environment_file` recorded in the
preparation manifest, `configs/.env` in the current collection manifest). The stored gpt-5.6-luna monthly
news summaries are reused; no new summary calls occur. The script default for
`--model` is still `gpt-5.6-luna`, so always pass the flags above. The run
covers all seven collected companies; the paper uses five of them (현대건설,
두산, BGF리테일, 아모레퍼시픽, SK바이오팜). The r02/r03 One-team reports come
from `run_repeated_reports.py` (see `REPEATED_REPORTS.md`). The full
reproduction map is in `docs/ABLATION.md`.

## Earlier record

The sections below are the earlier development record. They describe the
default `gpt-5.6-luna` run on seven companies, which the paper does not use,
and the gpt-5.4 run as it was first documented.

This condition uses the existing 2025H2 workspace and its completed Full inputs.
The original four conditions, runner and 28 published reports remain unchanged.
The new code has no dependency on the earlier ablation workspace.

## Flow

For each company, the Financial, News and market inputs are combined into one
request, producing one integrated company analysis. The peer gets its own single
integrated analysis. Comparison consumes those two reports; Strategy consumes
the target integrated analysis and comparison result. Writer produces one final
HTML report per target company.

The domain instructions and preprocessed user inputs are preserved. The adapter
checks the financial and news inputs against the actual requests saved by Full,
and reconstructs market inputs using the native deterministic preprocessing.
Saved monthly summaries and all articles selected for the Full analysis are reused.
Existing domain analysis reports are not used as analytical inputs or copied.
Deterministic financial/market facts remain available for metrics and charts.

## Models and experimental interpretation

All new model calls default to `gpt-5.6-luna`. Integrated analysis and Writer
apply reasoning effort `none`; Comparison and Strategy retain their native
request settings.
`--model` selects integrated analysis and, unless explicitly overridden,
Comparison/Strategy/Writer. `--downstream-model` overrides just those last three.
The reused monthly summaries were generated using `gpt-5.6-luna`.
Existing Full domain analysis used `gpt-5.4`: this default comparison changes both
architecture and analysis model. For an architecture-only comparison, prepare a
separate run with `--model gpt-5.4 --run-id one_team_gpt54_r01`.

## Prepare, verify and execute

From the repository root (the runner treats it as the workspace). The frozen inputs
(`prepared_inputs/`, `reports/`, `status/`) are not in Git; restore them from the
transfer bundle first. `prepare` reads `status/report_generation_status.json`.
`evaluate_report_bodies.py` also needs the analyst PDFs in `references/`.

```bash
python run_config/run_one_team_reports.py prepare
python run_config/run_one_team_reports.py check
```

These commands make no model calls. Preparation covers the existing seven
company/peer pairs and saves 14 complete requests. A real run has 14 integrated
calls plus 21 Comparison/Strategy/Writer calls, with zero new summary calls.
Retries may add physical requests; telemetry records the actual usage.

**Code hashes.** `prepare` (on an existing run), `check`, `run` and `resume`
compare the SHA-256 of every `src/**/*.py` file with the preparation manifest.
They fail on any tree after the cleanup commits. To re-run or re-verify, check
out the generation-time code (commit `da85eb3`).

To generate reports:

```bash
python run_config/run_one_team_reports.py run
# After an interrupted or failed run:
python run_config/run_one_team_reports.py resume
```

`--companies 삼성전자` limits a new preparation to one target; provide a new
`--run-id` when changing models or company selection. Input, code and request
hashes must match the preparation before generation can start (see **Code hashes** above). An exclusive lock
prevents concurrent generation of the same run. Resume skips completed stages.

## Files

- Code and prompts: `src/Agent_Team/Unified_Agent/` (`report.py` builds the prompt).
- Input adapter: `src/Agent_Team/Unified_Agent/inputs.py`.
- Launcher: `run_config/run_one_team_reports.py`.
- Prepared requests: `prepared_inputs/one_team/one_team_gpt-5_6-luna_r01/<target>/<target|peer>/unified_request.json`.
- New outputs: `reports/one_team/one_team_gpt-5_6-luna_r01/`.
- Canonical company analysis: `<company>/runs/<date>/unified_domain_team/unified_report.json` and `.md`.
- Peers remain under `<target>/비교기업/<peer>/` within the new output root.
- Status and usage: `status/one_team/`.

Only one-team subprocesses launched by `run_one_team_reports.py` or
`run_repeated_reports.py` enable the compatibility readers. Both set
`ONE_TEAM_RUNTIME=1`, `ONE_TEAM_SINGLE_REPORT=1` and
`PYTHONPATH=src/Agent_Team/Unified_Agent/runtime:src`.
They resolve legacy domain paths to the single integrated report in memory;
Financial/News/Y_Finance `final_report.json` analyses are never created.

## Validation

`tests/test_one_team.py` checks preserved inputs, the single-report schema,
evidence validation, Luna model policy, report files, and native Comparison and
Strategy consumers in a subprocess with the actual compatibility hooks.
The existing frozen experiment can still be checked independently:

```bash
python run_config/run_prepared_reports.py check
python -m pytest -q
```

Both commands run from the repository root. The frozen-input check needs the
bundle contents described above and also compares generation-time code hashes
(see **Code hashes** above).

## Response audit and execution repairs

Every integrated API response is saved as `unified_response.json` before local
validation. Resume reuses that response after verifying its request hash.
Finding/risk `domains` display metadata is derived from the domains of cited
evidence. The original response, any changed tags and actual usage are recorded;
analysis prose and citation IDs are preserved. Input/code repair history is kept
in the preparation manifest without changing the frozen source inputs.

## Completed generation

The default run completed all seven final HTML reports and 14 integrated
company/peer analyses. The report list is at
`reports/one_team/one_team_gpt-5_6-luna_r01/index.html`. Verification is recorded
in `status/one_team/one_team_gpt-5_6-luna_r01_verification.json`.
There were 36 actual API attempts (the planned 35 plus one initial response
that failed domain-label validation). All attempts used `gpt-5.6-luna`;
Comparison/Strategy reported 2,777 reasoning tokens in total. Integrated analysis
and Writer reported zero reasoning tokens. No completed reports were regenerated.

## BERTScore evaluation

```bash
python run_config/evaluate_report_bodies.py
```

This evaluates all 35 final HTML reports (7 companies × 5 conditions, including
one_team) against the same fixed narrative regions of the analyst PDFs.
BERTScore uses cached BAAI/bge-m3, 24 layers and batch size 1. The original
run used cuda:2; `--device` (default `auto`: CUDA when available, else CPU)
selects the device. No paid API calls or text truncation are used.
The original 28 report scores and all original body hashes were exactly reproduced.
The default output directory is now `evaluation/with_one_team_gpt54/` (the
Luna run's results were written to `evaluation/with_one_team/`): `metrics.json`,
`metrics.csv`, `condition_means.json`, `평가결과.md`, `protocol.json`, and
`verification.json`. Existing `evaluation/` result files remain unchanged.
Run `--prepare-only` to stop after body extraction, before BERTScore;
`--one-team-run-id` and `--output-dir` select another completed one-team run/output.

## Model-matched gpt-5.4 run

```bash
python run_config/run_one_team_reports.py prepare --model gpt-5.4 --run-id one_team_gpt54_r01
python run_config/run_one_team_reports.py check --model gpt-5.4 --run-id one_team_gpt54_r01
python run_config/run_one_team_reports.py run --model gpt-5.4 --run-id one_team_gpt54_r01
# Resume an interrupted run with the same flags:
python run_config/run_one_team_reports.py resume --model gpt-5.4 --run-id one_team_gpt54_r01
```

Integrated analysis, Comparison, Strategy and Writer all use gpt-5.4, matching
the original Full/random_news/no_peer/no_subdata analysis model. Stored monthly
news summaries remain shared gpt-5.6-luna summaries; no new summary calls occur.
All source hashes match the Luna one-team run, and the 14 integrated requests
were verified to be identical except for the model field.

The new output root is `reports/one_team/one_team_gpt54_r01/`.
Generation defaults remain selectable with explicit model/run-id flags.
Evaluation now defaults to this gpt-5.4 run and `evaluation/with_one_team_gpt54/`:

```bash
python run_config/evaluate_report_bodies.py
```

The historical Luna reports and `evaluation/with_one_team/` results are preserved.
To explicitly evaluate that historical run, pass
`--one-team-run-id one_team_gpt-5_6-luna_r01 --output-dir evaluation/with_one_team`.
The evaluation protocol and report derive the model comparison note from actual
generation status, rather than assuming the one-team model is Luna.

The gpt-5.4 run completed all seven HTML reports and 14 integrated company/peer
analyses with 35 successful API attempts and zero reasoning tokens. All embedded
charts were decoded and verified. The new report list is
`reports/one_team/one_team_gpt54_r01/index.html`.
The matched 35-report evaluation was completed and verified: original 28 scores
were reproduced exactly, all analysis models are gpt-5.4, and no body was truncated.
One-team mean BERTScore F1 over the seven r01 companies is
0.8779025333268302. Detailed results are in `evaluation/with_one_team_gpt54/`.
