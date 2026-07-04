# Competitor Agent

Competitor Agent calls an LLM to synthesize existing `final_report.json` files from:

- `Output_total/News/{run_key}/final_report.json`
- `Output_total/Financial/{run_key}/final_report.json`
- `Output_total/Y_Finance/{run_key}/final_report.json`

It excludes the target company and writes a separate summary report for each competitor. Each company report contains:

- `summary`
- `strengths`
- `risks`

Default LLM run:

```bash
PYTHONPATH=src python -m Agent_Team.Competitor_Agent.cli \
  --target-config configs/company_input.json \
  --competitor-config configs/company_input_peer_a_YYYYMMDD.json \
  --competitor-config configs/company_input_peer_b_YYYYMMDD.json
```

Auto-discover complete competitor runs for the target date:

```bash
PYTHONPATH=src python -m Agent_Team.Competitor_Agent.cli \
  --target-config configs/company_input.json
```

`OPENAI_API_KEY` is required.

Default outputs:

```text
Output_total/Competitor/{competitor_run_key}/competitor_summary_report.json
Output_total/Competitor/{competitor_run_key}/competitor_summary_report.md
```

For example, if two peers are selected, the agent calls the LLM separately for each company and writes:

```text
Output_total/Competitor/{peer_a_run_key}/competitor_summary_report.json
Output_total/Competitor/{peer_b_run_key}/competitor_summary_report.json
```
