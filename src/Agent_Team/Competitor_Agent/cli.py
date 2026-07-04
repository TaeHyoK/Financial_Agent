"""CLI entrypoint for the Competitor Agent."""

from __future__ import annotations

import argparse
import logging
from pathlib import Path

from . import DEFAULT_ENV_FILE, OUTPUT_ROOT, PROJECT_ROOT
from .agent import (
    RunIdentity,
    discover_competitor_identities,
    generate_competitor_report,
    load_identity_from_config,
)


DEFAULT_TARGET_CONFIG = PROJECT_ROOT / "configs" / "company_input.json"


def build_parser() -> argparse.ArgumentParser:
    """Build CLI parser."""

    parser = argparse.ArgumentParser(
        description="Create a competitor summary report from existing News, DART/Financial, and YFinance final reports."
    )
    parser.add_argument(
        "--target-config",
        default=str(DEFAULT_TARGET_CONFIG),
        help="Target company config JSON. This company is excluded from competitor summaries.",
    )
    parser.add_argument(
        "--competitor-config",
        action="append",
        default=[],
        help="Competitor company config JSON. Can be repeated.",
    )
    parser.add_argument(
        "--competitor-run-key",
        action="append",
        default=[],
        help="Existing competitor run_key, e.g. COMPANY_YYYYMMDD. Can be repeated.",
    )
    parser.add_argument(
        "--output-root",
        default=str(OUTPUT_ROOT),
        help="Output_total root containing Financial, News, and Y_Finance folders.",
    )
    parser.add_argument("--output-json", default=None, help="JSON report output path.")
    parser.add_argument("--output-md", default=None, help="Markdown report output path.")
    parser.add_argument(
        "--include-partial",
        action="store_true",
        help="When auto-discovering competitors, include run_keys with missing source final reports.",
    )
    parser.add_argument(
        "--selected-date",
        default=None,
        help="YYYYMMDD/YY-MM-DD date suffix used for auto-discovery. Defaults to target selected_date.",
    )
    parser.add_argument("--max-items-per-section", type=int, default=6)
    parser.add_argument("--llm-provider", default="openai", choices=["auto", "openai"])
    parser.add_argument("--llm-model", default="auto")
    parser.add_argument("--llm-timeout", type=int, default=90)
    parser.add_argument("--env-file", default=str(DEFAULT_ENV_FILE))
    parser.add_argument("--log-level", default="INFO")
    return parser


def main(argv: list[str] | None = None) -> int:
    """Run Competitor Agent CLI."""

    parser = build_parser()
    args = parser.parse_args(argv)
    logging.basicConfig(level=getattr(logging, args.log_level.upper(), logging.INFO), format="%(levelname)s %(message)s")
    logger = logging.getLogger("competitor_agent")

    target = load_identity_from_config(Path(args.target_config))
    output_root = Path(args.output_root).expanduser().resolve()
    competitors = load_competitor_identities(args, target=target, output_root=output_root)
    if not competitors:
        parser.error(
            "No competitors found. Provide --competitor-config/--competitor-run-key, "
            "or make sure existing output directories contain complete competitor final_report.json files."
        )

    paths = generate_competitor_report(
        target=target,
        competitors=competitors,
        output_root=output_root,
        output_json=Path(args.output_json).expanduser().resolve() if args.output_json else None,
        output_md=Path(args.output_md).expanduser().resolve() if args.output_md else None,
        max_items_per_section=args.max_items_per_section,
        llm_provider=args.llm_provider,
        llm_model=args.llm_model,
        llm_timeout=args.llm_timeout,
        env_file=Path(args.env_file).expanduser().resolve() if args.env_file else None,
    )
    for item in paths:
        logger.info("Wrote %s JSON report: %s", item.company_name, item.json)
        logger.info("Wrote %s Markdown report: %s", item.company_name, item.markdown)
    return 0


def load_competitor_identities(
    args: argparse.Namespace,
    *,
    target: RunIdentity,
    output_root: Path,
) -> list[RunIdentity]:
    """Load explicit competitors or discover them from output folders."""

    competitors: list[RunIdentity] = []
    for config_path in args.competitor_config:
        competitors.append(load_identity_from_config(Path(config_path)))
    for run_key in args.competitor_run_key:
        competitors.append(RunIdentity(run_key=run_key, company_name=run_key.rsplit("_", 1)[0]))

    if competitors:
        return competitors

    return discover_competitor_identities(
        output_root=output_root,
        target=target,
        selected_date=args.selected_date or target.selected_date,
        include_partial=args.include_partial,
    )


if __name__ == "__main__":
    raise SystemExit(main())
