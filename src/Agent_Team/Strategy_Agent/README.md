# Strategy Agent

Strategy Agent reads:

- target Financial `final_report.json`
- target News `final_report.json`
- target YFinance `final_report.json`
- N competitor `competitor_summary_report.json` files

It writes:

```text
Output_total/Strategy/{target_run_key}/strategy_input_bundle.json
Output_total/Strategy/{target_run_key}/strategy_content_plan.json
Output_total/Strategy/{target_run_key}/strategy_report.json
Output_total/Strategy/{target_run_key}/strategy_report.md
Output_total/Strategy/{target_run_key}/decision_basis_by_section.json
Output_total/Strategy/{target_run_key}/decision_basis_card.json
```

`decision_basis_by_section.json` is the primary basis ledger. Its keys match editable
`strategy_report.json` paths such as `financial_view.revenue` and
`risk_view.regulatory_risks[0]`, so critique agents can revise one opinion by ID/path.
`decision_basis_card.json` is a derived flat compatibility artifact for downstream
visualization and review tools.

Example:

```bash
PYTHONPATH=src python -m Agent_Team.Strategy_Agent.cli \
  --target-company-name "SK바이오팜" \
  --target-run-key "SK바이오팜_20251031" \
  --target-financial Output_total/Financial/SK바이오팜_20251031/final_report.json \
  --target-news Output_total/News/SK바이오팜_20251031/final_report.json \
  --target-yfinance Output_total/Y_Finance/SK바이오팜_20251031/final_report.json \
  --competitor-report Output_total/Competitor/더블유에스아이_20251031/competitor_summary_report.json \
  --competitor-report Output_total/Competitor/위더스제약_20251031/competitor_summary_report.json \
  --output-dir Output_total/Strategy/SK바이오팜_20251031
```
