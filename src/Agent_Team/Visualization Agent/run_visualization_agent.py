"""CLI entrypoint for the Visualization Agent."""

from __future__ import annotations

import argparse
import logging
from pathlib import Path

from visualization_agent import (
    DEFAULT_COMPANY_NAME,
    DEFAULT_RUN_KEY,
    OUTPUT_ROOT,
    VisualizationAgentConfig,
    run_visualization_agent,
)


def build_parser() -> argparse.ArgumentParser:
    """Build CLI parser."""

    parser = argparse.ArgumentParser(
        description="Generate deterministic chart assets and metadata for Writer Agent report assembly."
    )
    parser.add_argument(
        "--market-csv",
        default=str(OUTPUT_ROOT / "Y_Finance" / "market_full_dataset.csv"),
        help="Path to market_full_dataset.csv.",
    )
    parser.add_argument(
        "--dart-main",
        default=str(OUTPUT_ROOT / "Financial" / DEFAULT_RUN_KEY / "dart_main.json"),
        help="Path to dart_main.json.",
    )
    parser.add_argument(
        "--dart-lightweight",
        default=str(OUTPUT_ROOT / "Financial" / DEFAULT_RUN_KEY / "dart_lightweight.json"),
        help="Path to dart_lightweight.json.",
    )
    parser.add_argument(
        "--strategy-json",
        default=str(OUTPUT_ROOT / "Strategy" / DEFAULT_RUN_KEY / "strategy_report.json"),
        help="Path to strategy_report.json.",
    )
    parser.add_argument(
        "--strategy-md",
        default=str(OUTPUT_ROOT / "Strategy" / DEFAULT_RUN_KEY / "strategy_report.md"),
        help="Path to strategy_report.md.",
    )
    parser.add_argument(
        "--decision-basis-card",
        default=str(OUTPUT_ROOT / "Strategy" / DEFAULT_RUN_KEY / "decision_basis_card.json"),
        help="Path to decision_basis_card.json.",
    )
    parser.add_argument(
        "--peer-comparison-dataset",
        default=str(OUTPUT_ROOT / "Competitor" / DEFAULT_RUN_KEY / "peer_comparison_dataset.json"),
        help="Optional path to Peer Comparison Agent peer_comparison_dataset.json.",
    )
    parser.add_argument(
        "--output-dir",
        default=str(OUTPUT_ROOT / "Visualization" / DEFAULT_RUN_KEY),
        help="Output directory for Visualization Agent files.",
    )
    parser.add_argument(
        "--output-root",
        default=str(OUTPUT_ROOT),
        help="Output_total root used to auto-discover peer market and financial datasets.",
    )
    parser.add_argument(
        "--env-file",
        default=str(VisualizationAgentConfig.env_file),
        help="Shared project env file containing OPENAI_API_KEY.",
    )
    parser.add_argument(
        "--peer-run-key",
        action="append",
        default=[],
        help="Peer run key to include in peer charts. Can be repeated. Defaults to all *_selected_date runs under Output_total.",
    )
    parser.add_argument("--company-name", default=DEFAULT_COMPANY_NAME, help="Target company display name.")
    parser.add_argument("--run-key", default=DEFAULT_RUN_KEY, help="Target run key, e.g. COMPANY_YYYYMMDD.")
    parser.add_argument("--log-level", default="INFO", help="Python logging level.")
    return parser


def main(argv: list[str] | None = None) -> int:
    """Run the Visualization Agent CLI."""

    parser = build_parser()
    args = parser.parse_args(argv)
    logging.basicConfig(level=getattr(logging, args.log_level.upper(), logging.INFO), format="%(levelname)s %(message)s")
    result = run_visualization_agent(
        VisualizationAgentConfig(
            market_csv=Path(args.market_csv).expanduser(),
            dart_main=Path(args.dart_main).expanduser(),
            dart_lightweight=Path(args.dart_lightweight).expanduser(),
            strategy_json=Path(args.strategy_json).expanduser(),
            strategy_md=Path(args.strategy_md).expanduser(),
            decision_basis_card=Path(args.decision_basis_card).expanduser(),
            peer_comparison_dataset=Path(args.peer_comparison_dataset).expanduser(),
            output_dir=Path(args.output_dir).expanduser(),
            output_root=Path(args.output_root).expanduser(),
            env_file=Path(args.env_file).expanduser(),
            peer_run_keys=tuple(args.peer_run_key),
            company_name=args.company_name,
            run_key=args.run_key,
        )
    )
    logging.getLogger(__name__).info("Wrote Visualization Agent outputs to %s", result["output_dir"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
