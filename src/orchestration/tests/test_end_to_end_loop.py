from __future__ import annotations

from orchestration.end_to_end_loop import build_parser


def test_orchestration_parser_keeps_single_company_defaults() -> None:
    args = build_parser().parse_args(["--dry-run"])

    assert args.dry_run is True
    assert args.skip_step == []
    assert args.reuse_existing is False
