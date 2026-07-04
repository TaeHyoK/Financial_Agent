"""CLI entrypoint for Peer Comparison Agent v1."""

from __future__ import annotations

import argparse
import logging
from pathlib import Path

from . import OUTPUT_ROOT, PROJECT_ROOT
from .peer_comparison import (
    RunIdentity,
    build_run_key,
    generate_peer_comparison,
    load_identity_from_config,
    normalize_date,
)


DEFAULT_TARGET_CONFIG = PROJECT_ROOT / "configs" / "company_input.json"


def build_parser() -> argparse.ArgumentParser:
    """Build CLI parser."""

    parser = argparse.ArgumentParser(
        description=(
            "Create domestic-only peer comparison artifacts from existing Financial and Y_Finance outputs. "
            "Global peer, valuation, and complete industry-average comparison are intentionally excluded."
        )
    )
    parser.add_argument("--target-config", default=str(DEFAULT_TARGET_CONFIG), help="Target company config JSON.")
    parser.add_argument(
        "--run-key",
        default="",
        help="Target run_key. When provided, this overrides --target-config run_key inference.",
    )
    parser.add_argument(
        "--company-name",
        default="",
        help="Target company display name. Defaults to config or run_key prefix.",
    )
    parser.add_argument(
        "--selected-date",
        default="",
        help="YYYYMMDD date suffix used for peer discovery. Defaults to config or run_key suffix.",
    )
    parser.add_argument(
        "--peer-run-key",
        action="append",
        default=[],
        help="Domestic peer run_key to include. Can be repeated. Defaults to same-date Output_total peers.",
    )
    parser.add_argument("--output-root", default=str(OUTPUT_ROOT), help="Output_total root.")
    parser.add_argument("--output-dir", default="", help="Optional output directory override.")
    parser.add_argument("--log-level", default="INFO")
    return parser


def main(argv: list[str] | None = None) -> int:
    """Run Peer Comparison Agent v1."""

    parser = build_parser()
    args = parser.parse_args(argv)
    logging.basicConfig(level=getattr(logging, args.log_level.upper(), logging.INFO), format="%(levelname)s %(message)s")
    target = _target_identity_from_args(args)
    paths = generate_peer_comparison(
        target=target,
        peer_run_keys=args.peer_run_key,
        output_root=Path(args.output_root).expanduser().resolve(),
        output_dir=Path(args.output_dir).expanduser().resolve() if args.output_dir else None,
    )
    logger = logging.getLogger("peer_comparison_agent")
    logger.info("Wrote peer comparison dataset: %s", paths.dataset_json)
    logger.info("Wrote peer positioning summary: %s", paths.positioning_json)
    logger.info("Wrote peer comparison markdown: %s", paths.summary_md)
    return 0


def _target_identity_from_args(args: argparse.Namespace) -> RunIdentity:
    config_identity = load_identity_from_config(Path(args.target_config).expanduser().resolve())
    selected_date = normalize_date(args.selected_date) if args.selected_date else config_identity.selected_date
    company_name = args.company_name.strip() or config_identity.company_name
    run_key = args.run_key.strip() or build_run_key(company_name, selected_date, config_identity.corp_code)
    if args.run_key.strip() and not args.company_name.strip():
        company_name = args.run_key.rsplit("_", 1)[0]
    return RunIdentity(
        run_key=run_key,
        company_name=company_name,
        selected_date=selected_date,
        ticker=config_identity.ticker,
        corp_code=config_identity.corp_code,
        stock_code=config_identity.stock_code,
    )


if __name__ == "__main__":
    raise SystemExit(main())
