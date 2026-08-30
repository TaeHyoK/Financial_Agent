"""CLI for Writer-aware chart catalog and selected chart generation."""

from __future__ import annotations

import argparse
import logging
from pathlib import Path

from report_chart_pipeline import (
    ReportChartConfig,
    build_report_chart_catalog,
    generate_requested_report_charts,
    load_chart_selection_request,
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="phase", required=True)
    for phase in ("catalog", "generate"):
        command = subparsers.add_parser(phase)
        command.add_argument("--output-root", type=Path, required=True)
        command.add_argument("--peer-output-root", type=Path, default=None)
        command.add_argument("--run-key", required=True)
        command.add_argument("--company-name", required=True)
        command.add_argument("--output-dir", type=Path, required=True)
        command.add_argument("--peer-run-key", action="append", default=[])
        command.add_argument("--log-level", default="INFO")
        if phase == "generate":
            command.add_argument("--selection-file", type=Path, required=True)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    logging.basicConfig(
        level=getattr(logging, args.log_level.upper(), logging.INFO),
        format="%(levelname)s %(message)s",
    )
    config = ReportChartConfig(
        output_root=args.output_root.expanduser().resolve(),
        peer_output_root=(
            args.peer_output_root.expanduser().resolve()
            if args.peer_output_root is not None
            else None
        ),
        run_key=args.run_key,
        company_name=args.company_name,
        output_dir=args.output_dir.expanduser().resolve(),
        peer_run_keys=tuple(args.peer_run_key),
    )
    if args.phase == "catalog":
        result = build_report_chart_catalog(config)
        print(f"available_chart_count={len(result['available_charts'])}")
        print(f"chart_catalog={config.output_dir / 'chart_catalog.json'}")
        return 0
    requested, selection_details = load_chart_selection_request(args.selection_file)
    result = generate_requested_report_charts(
        config,
        requested,
        selection_details=selection_details,
    )
    print(f"generated_chart_count={len(result['selection']['generated_chart_keys'])}")
    print(f"chart_manifest={result['chart_manifest']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
