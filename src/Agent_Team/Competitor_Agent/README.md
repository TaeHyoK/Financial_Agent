# Peer Resolution and Comparison

This package selects one domestic peer, builds a deterministic pairwise dataset, and runs one comparison analysis over both companies' Financial, News, and YFinance handoffs. Peer identity selection and numeric calculations do not use an LLM; only the final cross-company interpretation does.

## Inputs

- Target stock code and selected date
- Naver Finance / WiseReport FG000 industry candidates
- Target and peer `Financial/final_report.json`
- Target and peer `News/final_report.json`
- Target and peer `Y_Finance/market_full_dataset.csv`
- Target and peer `Y_Finance/final_report.json`

Naver values are used only to select one peer identity. When the FG000 header omits `MKT_VAL`, the resolver fetches current market caps from the Naver item pages for that same bounded FG000 candidate set and keeps the same absolute-distance selection rule. Financial and market comparison values are rebuilt from local DART and Yahoo Finance outputs.

## Commands

Resolve one Naver peer:

```bash
PYTHONPATH=src python -m Agent_Team.Competitor_Agent.peer_resolver \
  --stock-code 326030 \
  --output Output_total/SK바이오팜/Competitor/20251031/peer_resolution.json
```

Build the structured pairwise dataset after both company pipelines complete:

```bash
PYTHONPATH=src python -m Agent_Team.Competitor_Agent.peer_comparison_cli \
  --target-config Output_total/SK바이오팜/runs/20251031/resolved_inputs/target_company.json \
  --run-key SK바이오팜_20251031 \
  --company-name SK바이오팜 \
  --selected-date 20251031 \
  --peer-run-key 일성아이에스_20251031 \
  --output-root Output_total \
  --peer-output-root Output_total/SK바이오팜/비교기업
```

Run the comparison analysis after the dataset is ready:

```bash
PYTHONPATH=src python -m Agent_Team.Competitor_Agent.comparison_agent_cli \
  --target-company-name SK바이오팜 \
  --peer-company-name 일성아이에스 \
  --target-financial Output_total/SK바이오팜/Financial/20251031/final_report.json \
  --target-news Output_total/SK바이오팜/News/20251031/final_report.json \
  --target-yfinance Output_total/SK바이오팜/Y_Finance/20251031/final_report.json \
  --peer-financial Output_total/SK바이오팜/비교기업/일성아이에스/Financial/20251031/final_report.json \
  --peer-news Output_total/SK바이오팜/비교기업/일성아이에스/News/20251031/final_report.json \
  --peer-yfinance Output_total/SK바이오팜/비교기업/일성아이에스/Y_Finance/20251031/final_report.json \
  --pairwise-dataset Output_total/SK바이오팜/Competitor/20251031/peer_comparison_dataset.json \
  --output-dir Output_total/SK바이오팜/Competitor/20251031
```

## Output

```text
Output_total/{company}/Competitor/{YYYYMMDD}/peer_resolution.json
Output_total/{company}/Competitor/{YYYYMMDD}/peer_comparison_dataset.json
Output_total/{company}/Competitor/{YYYYMMDD}/peer_comparison_context.json
Output_total/{company}/Competitor/{YYYYMMDD}/peer_comparison_report.json
```

Paths follow the pipeline's company-first layout (`Output_total/<company>/<Agent>/<YYYYMMDD>`); peers analyzed only for comparison live under `Output_total/<target>/비교기업/<peer>/`. Without `--output-dir`, `peer_comparison_cli` writes to `Output_total/<company>/Competitor/<YYYYMMDD>/`.

The dataset preserves period, date, unit, and missing-field metadata. The comparison report records the LLM's relative findings and the exact basis cards it selected. Neither artifact represents an industry ranking or average, and the comparison agent does not issue an investment action or target price.
