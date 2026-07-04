# Visualization Agent

Deterministic chart generator for Writer Agent report assembly.

## Outputs

- `figures/stock_price_ma_volume_relative_strength.pdf`
- `figures/stock_price_ma_volume_relative_strength.png`
- `figures/fundamental_margin_trend.pdf`
- `figures/fundamental_margin_trend.png`
- `figures/indexed_stock_vs_kospi.pdf`
- `figures/indexed_stock_vs_kospi.png`
- `figures/peer_return_comparison.pdf`
- `figures/peer_return_comparison.png`
- `figures/revenue_profit_sga_trend.pdf`
- `figures/revenue_profit_sga_trend.png`
- `figures/liquidity_leverage_peer_comparison.pdf`
- `figures/liquidity_leverage_peer_comparison.png`
- `figures/investment_thesis_evidence_map.pdf`
- `figures/investment_thesis_evidence_map.png`
- `chart_manifest.json`
- `data_quality_report.json`
- `visualization_summary.md`

## Run

Preferred run-key mode:

```bash
python "/home/agent2/Financial_Agent_Final/src/Agent_Team/Visualization Agent/run_visualization_agent.py" \
  --run-key "{run_key}" \
  --output-root "/home/agent2/Financial_Agent_Final/Output_total"
```

If `--run-key` is omitted, the agent discovers the newest Strategy run under `Output_total/Strategy`.
The agent resolves standard inputs and outputs as:

```text
Output_total/Financial/{run_key}/dart_main.json
Output_total/Financial/{run_key}/dart_lightweight.json
Output_total/Strategy/{run_key}/strategy_report.json
Output_total/Strategy/{run_key}/strategy_report.md
Output_total/Strategy/{run_key}/decision_basis_card.json
Output_total/Visualization/{run_key}/
```

Use explicit paths only when overriding the standard run-key layout:

```bash
python "/home/agent2/Financial_Agent_Final/src/Agent_Team/Visualization Agent/run_visualization_agent.py" \
  --market-csv "/home/agent2/Financial_Agent_Final/Output_total/Y_Finance/market_full_dataset.csv" \
  --dart-main "/home/agent2/Financial_Agent_Final/Output_total/Financial/{run_key}/dart_main.json" \
  --dart-lightweight "/home/agent2/Financial_Agent_Final/Output_total/Financial/{run_key}/dart_lightweight.json" \
  --strategy-json "/home/agent2/Financial_Agent_Final/Output_total/Strategy/{run_key}/strategy_report.json" \
  --strategy-md "/home/agent2/Financial_Agent_Final/Output_total/Strategy/{run_key}/strategy_report.md" \
  --decision-basis-card "/home/agent2/Financial_Agent_Final/Output_total/Strategy/{run_key}/decision_basis_card.json" \
  --output-dir "/home/agent2/Financial_Agent_Final/Output_total/Visualization/{run_key}" \
  --output-root "/home/agent2/Financial_Agent_Final/Output_total" \
  --company-name "{company_name}" \
  --run-key "{run_key}"
```

Peer charts auto-discover same-date run directories under `Output_total/Y_Finance`. Use repeated `--peer-run-key` arguments to override that set.

This agent does not calculate P/E, P/B, OPM, ROE, target price, or upside/downside.
