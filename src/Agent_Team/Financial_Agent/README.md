# Financial Agent

DART financial statement collector and Financial Analyst Agent workflow.

## Layout

- `Agent.md`: current primary/secondary evidence contract
- `main.py`: OpenDART collection and canonical financial statement output
- `financial_index_calculator.py`: financial metric calculation from canonical DART JSON
- `langgraph_flow.py`: Financial Analyst Agent report LangGraph
- `output_schema.json`: Financial Analyst report schema

## Run DART

```bash
cd /path/to/Financial_Agent
PYTHONPATH=src python -m Agent_Team.Financial_Agent.main
```

Default input:

- `configs/company_input.json`

Default outputs are grouped by company and selected date:

- `Output_total/Financial/<company>_<YYYYMMDD>/dart_master.json`
- `Output_total/Financial/<company>_<YYYYMMDD>/dart_2y_handoff.json`
- `Output_total/Financial/<company>_<YYYYMMDD>/dart_main.json`
- `Output_total/Financial/<company>_<YYYYMMDD>/dart_lightweight.json`

For the default SK바이오팜 config, `<company>_<YYYYMMDD>` is `SK바이오팜_20251031`.

The collector reads `DART_API_KEY` from the configured `.env` file.

## Run Financial Analyst

```bash
cd /path/to/Financial_Agent
PYTHONPATH=src python -m Agent_Team.Financial_Agent.langgraph_flow \
  --manifest /path/to/runtime_manifest.json \
  --output Output_total/Financial/SK바이오팜_20251031/agent_pipeline/pipeline_financial_analyst_report_output.json
```

The report is written to the same company/date folder under `agent_pipeline`:

- `Output_total/Financial/<company>_<YYYYMMDD>/agent_pipeline`

The orchestration pipeline copies this analyst report to the domain `final_report.json`, which Strategy reads directly.

## Tests

```bash
cd /path/to/Financial_Agent
python -m pytest src/Agent_Team/Financial_Agent/tests
```
