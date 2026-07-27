# Financial Agent

Deterministic DART financial statement collector and financial analyst validation workflow.

## Layout

- `Agent.md`: current primary/secondary evidence contract
- `main.py`: OpenDART collection and canonical financial statement output
- `financial_index_calculator.py`: financial metric calculation from canonical DART JSON
- `langgraph_flow.py`: Financial Analyst Agent report LangGraph
- `output_schema.json`: Financial Analyst report schema
- `SY_Agent/`: Financial Analyst output validation LangGraph

## Run DART

```bash
cd /home/agent2/Financial_Agent_Final_hyo
PYTHONPATH=src python -m Agent_Team.Financial_Agent.main
```

Default input:

- `/home/agent2/Financial_Agent_Final_hyo/configs/company_input.json`

Default outputs are grouped by company and selected date:

- `/home/agent2/Financial_Agent_Final_hyo/Output_total/Financial/<company>_<YYYYMMDD>/dart_master.json`
- `/home/agent2/Financial_Agent_Final_hyo/Output_total/Financial/<company>_<YYYYMMDD>/dart_2y_handoff.json`
- `/home/agent2/Financial_Agent_Final_hyo/Output_total/Financial/<company>_<YYYYMMDD>/dart_main.json`
- `/home/agent2/Financial_Agent_Final_hyo/Output_total/Financial/<company>_<YYYYMMDD>/dart_lightweight.json`

For the default SK바이오팜 config, `<company>_<YYYYMMDD>` is `SK바이오팜_20251031`.

The collector reads `DART_API_KEY` from the configured `.env` file.

## Run Financial Analyst + SY Validation

```bash
cd /home/agent2/Financial_Agent_Final_hyo
PYTHONPATH=src python -m Agent_Team.Financial_Agent.SY_Agent.run_pipeline
```

Pipeline outputs are written to the same company/date folder under `agent_pipeline`:

- `/home/agent2/Financial_Agent_Final_hyo/Output_total/Financial/<company>_<YYYYMMDD>/agent_pipeline`

`run_pipeline` derives `<company>_<YYYYMMDD>` from `target_entity.company_name` and `target_entity.as_of_date` in the Financial Analyst manifest unless `--output-dir` or `--run-key` is provided.

To run only the Financial Analyst report graph:

```bash
cd /home/agent2/Financial_Agent_Final_hyo
PYTHONPATH=src python -m Agent_Team.Financial_Agent.langgraph_flow \
  --manifest /path/to/runtime_manifest.json \
  --output /home/agent2/Financial_Agent_Final_hyo/Output_total/Financial/SK바이오팜_20251031/agent_pipeline/pipeline_financial_analyst_report_output.json
```

## Tests

```bash
cd /home/agent2/Financial_Agent_Final_hyo
python -m pytest src/Agent_Team/Financial_Agent/tests
```
