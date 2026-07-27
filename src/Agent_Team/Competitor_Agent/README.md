# Peer Resolution and Comparison

This package contains deterministic peer identity and pairwise comparison components. It does not call an LLM and does not generate competitor prose.

## Inputs

- Target stock code and selected date
- Naver Finance / WiseReport FG000 industry candidates
- Target and peer `Financial/final_report.json`
- Target and peer `Y_Finance/market_full_dataset.csv`
- Target and peer `Y_Finance/final_report.json`

Naver values are used only to select one peer identity. Financial and market comparison values are rebuilt from local DART and Yahoo Finance outputs.

## Commands

Resolve one Naver peer:

```bash
PYTHONPATH=src python -m Agent_Team.Competitor_Agent.peer_resolver \
  --stock-code 326030 \
  --output Output_total/Competitor/SK바이오팜_20251031/peer_resolution.json
```

Build the structured pairwise dataset after both company pipelines complete:

```bash
PYTHONPATH=src python -m Agent_Team.Competitor_Agent.peer_comparison_cli \
  --target-config Output_total/runs/SK바이오팜_20251031/resolved_inputs/target_company.json \
  --run-key SK바이오팜_20251031 \
  --company-name SK바이오팜 \
  --selected-date 20251031 \
  --peer-run-key 일성아이에스_20251031 \
  --output-root Output_total
```

## Output

```text
Output_total/Competitor/{target_run_key}/peer_resolution.json
Output_total/Competitor/{target_run_key}/peer_comparison_dataset.json
```

The dataset preserves period, date, unit, and missing-field metadata. It does not contain ranks, industry averages, strengths, risks, investment implications, or generated summary sentences.
