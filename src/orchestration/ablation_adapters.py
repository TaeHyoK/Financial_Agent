"""Schema-preserving passthroughs used when SY agents are ablated.

These adapters do not validate claims. They only materialize the filenames and
minimal ledgers expected by downstream deterministic packet builders, marking
every artifact as an explicit ``no_sy`` experiment.
"""

from __future__ import annotations

import argparse
import copy
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Materialize an unverified no-SY domain handoff.")
    parser.add_argument("--domain", required=True, choices=["financial", "news", "yfinance"])
    parser.add_argument("--input", required=True, type=Path)
    parser.add_argument("--verified-report", required=True, type=Path)
    parser.add_argument("--validation", required=True, type=Path)
    parser.add_argument("--trace-output", default=None, type=Path)
    parser.add_argument("--strategy-report", default=None, type=Path)
    return parser


def run_adapter(args: argparse.Namespace) -> dict[str, Any]:
    source = _load_json(args.input)
    verified = copy.deepcopy(source)
    verified["report_status"] = "sy_bypassed_ablation"
    verified["ablation"] = {
        "name": "no_sy",
        "sy_validation_applied": False,
        "warning": "Claims are unverified domain-agent outputs.",
    }

    if args.domain == "financial":
        validation = _financial_ledger(source, args.input)
    elif args.domain == "news":
        validation = _news_ledger(source, args.input)
        output = verified.get("output") if isinstance(verified.get("output"), dict) else None
        if output is not None:
            output["report_status"] = "sy_bypassed_ablation"
    else:
        validation = _yfinance_ledger(source, args.input)

    _write_json(args.verified_report, verified)
    _write_json(args.validation, validation)
    if args.strategy_report:
        _write_json(args.strategy_report, verified)
    if args.trace_output:
        _write_json(
            args.trace_output,
            {
                "status": "sy_bypassed_ablation",
                "domain": args.domain,
                "source": str(args.input.expanduser().resolve()),
                "llm_usage_summary": {"by_field": {}},
            },
        )
    return validation


def _financial_ledger(source: dict[str, Any], source_path: Path) -> dict[str, Any]:
    claims = []
    for index, item in enumerate(_list(_dict(source.get("sy_handoff")).get("financial_claims"))):
        if not isinstance(item, dict):
            continue
        claims.append(
            {
                "claim_id": str(item.get("claim_id") or f"F_RAW_{index + 1:03d}"),
                "claim_ko": str(item.get("claim_ko") or item.get("dart_anchor_summary_ko") or ""),
                "section_path": "",
                "financial_dimension": str(item.get("financial_dimension") or ""),
                "evidence_ids": _list(item.get("evidence_ids")),
                "evidence_use": "strong",
                "support_level": "unverified",
                "decision": "passthrough",
                "limitations": _texts([item.get("caution_ko")]),
            }
        )
    return {
        "agent_name": "No-SY Ablation Adapter",
        "output_mode": "financial_unverified_passthrough",
        "source_agent": {"output_path": str(source_path.expanduser().resolve())},
        "validation_summary": _summary(claims),
        "claim_validations": claims,
        "secondary_context_assessments": [],
        "report_rewritten": False,
    }


def _news_ledger(source: dict[str, Any], source_path: Path) -> dict[str, Any]:
    output = _dict(source.get("output")) or source
    news_only = _dict(_dict(output.get("analysis_blocks")).get("news_only"))
    claims = []
    for source_key in ("positive_signals", "negative_signals", "key_risks", "uncertainties"):
        for index, item in enumerate(_list(news_only.get(source_key))):
            if not isinstance(item, dict) or not str(item.get("claim") or "").strip():
                continue
            claim = {
                "claim_id": f"N_RAW_{len(claims) + 1:03d}",
                "section": f"analysis_blocks.news_only.{source_key}[{index}]",
                "claim": str(item.get("claim") or ""),
                "evidence_ids": _texts(item.get("evidence_ids")),
                "claim_kind": "unverified_agent_claim",
                "evidence_use": "strong",
                "support_level": "unverified",
                "decision": "passthrough",
                "limitations": [],
            }
            for key in (
                "event_status",
                "company_specificity",
                "materiality_status",
                "financial_link_status",
            ):
                if item.get(key):
                    claim[key] = str(item[key])
            claims.append(claim)
    return {
        "agent_name": "No-SY Ablation Adapter",
        "output_mode": "news_unverified_passthrough",
        "source_agent": {"output_path": str(source_path.expanduser().resolve())},
        "verification_mode": "sy_bypassed",
        "summary": _summary(claims),
        "claim_validations": claims,
        "secondary_context_assessments": [],
        "elapsed_seconds": 0.0,
    }


def _yfinance_ledger(source: dict[str, Any], source_path: Path) -> dict[str, Any]:
    return {
        "validation_version": "no_sy_ablation",
        "source_agent": source.get("agent_name") or "Y-Finance Agent",
        "verifier_agent": "No-SY Ablation Adapter",
        "target_company": source.get("target_company"),
        "ticker": source.get("ticker"),
        "as_of_date": source.get("as_of_date"),
        "selected_date": source.get("selected_date"),
        "selected_date_policy": source.get("selected_date_policy"),
        "source_report": {"path": str(source_path.expanduser().resolve())},
        "verification_mode": "sy_bypassed",
        "summary": _summary([]),
        "verified_claims": [],
        "context_only_claims": [],
        "excluded_claims": [],
        "secondary_context_assessments": [],
        "elapsed_seconds": 0.0,
    }


def _summary(claims: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "overall_status": "sy_bypassed_ablation",
        "verification_mode": "unverified_passthrough",
        "total_claims": len(claims),
        "evidence_use_counts": {"strong": len(claims), "context_only": 0, "exclude": 0},
        "llm_call_count": 0,
        "report_rewritten": False,
        "created_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }


def _load_json(path: Path) -> dict[str, Any]:
    resolved = path.expanduser().resolve()
    payload = json.loads(resolved.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"Input JSON must be an object: {resolved}")
    return payload


def _write_json(path: Path, payload: Any) -> None:
    resolved = path.expanduser().resolve()
    resolved.parent.mkdir(parents=True, exist_ok=True)
    resolved.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _texts(value: Any) -> list[str]:
    values = value if isinstance(value, list) else [value]
    return list(dict.fromkeys(str(item).strip() for item in values if str(item or "").strip()))


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    run_adapter(args)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
