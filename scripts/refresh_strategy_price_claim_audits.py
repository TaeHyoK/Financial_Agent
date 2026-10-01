"""Refresh diagnostic price-claim flags for completed frozen-input decisions."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from Agent_Team.Strategy_Agent.agent import save_json  # noqa: E402
from Agent_Team.Strategy_Agent.price_claim_audit import (  # noqa: E402
    PRICE_CLAIM_AUDIT_VERSION,
    audit_strategy_price_claims,
)


def refresh(root: Path) -> int:
    count = 0
    for decision_path in sorted(root.rglob("strategy_decision.json")):
        output = decision_path.parent
        status_path = output / "status.json"
        if not status_path.is_file():
            continue
        status = json.loads(status_path.read_text(encoding="utf-8"))
        if status.get("status") != "completed":
            continue
        decision = json.loads(decision_path.read_text(encoding="utf-8"))
        findings = audit_strategy_price_claims(decision)
        save_json(output / "price_claim_audit.json", {
            "audit_type": "lexical_review_flags_not_semantic_validation",
            "audit_version": PRICE_CLAIM_AUDIT_VERSION,
            "finding_count": len(findings),
            "findings": findings,
        })
        status["price_claim_review_flags"] = len(findings)
        status["price_claim_audit_version"] = PRICE_CLAIM_AUDIT_VERSION
        save_json(status_path, status)
        count += 1
    return count


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", required=True)
    args = parser.parse_args()
    print(f"refreshed={refresh(Path(args.root).resolve())}")
