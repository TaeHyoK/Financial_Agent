"""Read-only accounting of recorded generation attempts, including preparation failures.

This is a spending ledger, not a per-condition cost allocation or provider invoice.
Only explicitly supplied suites and common-source runs are included.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any


def audit_recorded_costs(
    suite_root: Path, *, common_usage_summaries: list[Path] | None = None,
) -> dict[str, Any]:
    root = suite_root.resolve()
    if not root.is_dir():
        raise ValueError(f"Suite directory not found: {root}")
    groups: dict[str, list[Path]] = {
        "analysis_and_report": sorted((root / "outputs").rglob("llm_usage_summary.json")),
        # Includes retained .building and .stale attempts, not only successful snapshots.
        "random_preprocessing": sorted((root / "snapshots").rglob("random_news_preprocessing_usage.json")),
        "common_source_runs": list(common_usage_summaries or []),
    }
    seen: dict[str, tuple[str, dict[str, Any]]] = {}
    attempts = []
    for group, paths in groups.items():
        for path in paths:
            value = json.loads(path.read_text(encoding="utf-8"))
            execution_id = value.get("execution_id")
            if not execution_id or not isinstance(value.get("usage"), dict):
                raise ValueError(f"Not an execution usage summary: {path}")
            signature = {key: value.get(key) for key in (
                "usage", "estimated_api_cost", "pipeline_completed", "transport_attempts", "by_step",
            )}
            if execution_id in seen:
                old_group, old_signature = seen[execution_id]
                if old_group != group or old_signature != signature:
                    raise ValueError(f"Conflicting usage summaries for execution {execution_id}: {path}")
                continue  # Canonical/execution copies and cache reuse are not new spending.
            seen[execution_id] = (group, signature)
            pricing = value.get("estimated_api_cost") or {}
            cost = pricing.get("total_cost_usd") if pricing.get("status") == "available" else None
            attempts.append({
                "group": group, "execution_id": execution_id, "summary_path": str(path.resolve()),
                "pipeline_completed": value.get("pipeline_completed") is True,
                "total_tokens": int(value["usage"].get("total_tokens") or 0),
                "cost_usd": float(cost) if cost is not None else None,
            })

    def totals(rows):
        known = [row["cost_usd"] for row in rows if row["cost_usd"] is not None]
        return {
            "execution_count": len(rows),
            "incomplete_execution_count": sum(not row["pipeline_completed"] for row in rows),
            "total_tokens": sum(row["total_tokens"] for row in rows),
            "known_cost_usd": sum(known),
            "cost_usd": sum(known) if rows and len(known) == len(rows) else None,
            "unpriced_execution_count": len(rows) - len(known),
        }

    unsummarized = [str(path) for path in (root / "snapshots").rglob("random_news_usage.jsonl")
                    if not (path.parent / "random_news_preprocessing_usage.json").is_file()]
    return {
        "scope": "recorded_generation_spending_v1",
        "suite_root": str(root),
        "common_source_runs_supplied": bool(common_usage_summaries),
        "unsummarized_random_usage_logs": unsummarized,
        "by_group": {group: totals([row for row in attempts if row["group"] == group]) for group in groups},
        "recorded_total": totals(attempts),
        "attempts": attempts,
        "scope_note": (
            "Totals cover only supplied recorded LLM usage, including unsuccessful and stale attempts. "
            "Common-source execution totals are counted once, including any analysis run during preparation. "
            "They are not allocated to condition means. Missing common-source records, unsummarized logs, "
            "unrecorded provider usage, non-LLM services and evaluation costs are not treated as zero or included. "
            "This is not a complete provider invoice."
        ),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--suite-root", type=Path, required=True)
    parser.add_argument("--common-usage-summary", type=Path, action="append", default=[])
    args = parser.parse_args()
    print(json.dumps(audit_recorded_costs(args.suite_root, common_usage_summaries=args.common_usage_summary),
                     ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
