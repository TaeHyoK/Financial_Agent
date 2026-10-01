"""Run Strategy only on one frozen input bundle with an explicit decision style."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from Agent_Team.Strategy_Agent.agent import (  # noqa: E402
    DECISION_STYLES,
    build_strategy_generation_payload,
    decision_generation_prompt,
    load_env_file,
    run_decision_agent,
    save_json,
)
from Agent_Team.Strategy_Agent.decision import (  # noqa: E402
    align_strategy_decision_evidence_plan,
    build_strategy_context_package,
    validate_strategy_decision,
)
from Agent_Team.Strategy_Agent.packet import build_compact_strategy_packet  # noqa: E402
from Agent_Team.Strategy_Agent.price_claim_audit import (  # noqa: E402
    PRICE_CLAIM_AUDIT_VERSION,
    audit_strategy_price_claims,
)


def run(args: argparse.Namespace) -> None:
    source = Path(args.input_bundle).resolve()
    source_hash = hashlib.sha256(source.read_bytes()).hexdigest()
    bundle = json.loads(source.read_text(encoding="utf-8"))
    company = bundle["target_company"]["company_name"]
    output = Path(args.output_dir).resolve()
    prior_attempts = 0
    if output.exists():
        if not args.retry_incomplete:
            raise FileExistsError(f"Output directory already exists: {output}")
        status_path = output / "status.json"
        if not status_path.is_file() or (output / "strategy_decision.json").exists():
            raise ValueError("Retry requires an incomplete run with status.json and no decision")
        previous = json.loads(status_path.read_text(encoding="utf-8"))
        if previous.get("status") not in {"failed", "interrupted"}:
            raise ValueError("Retry requires a failed or interrupted run")
        if any((previous.get(key) != value) for key, value in {
            "company": company,
            "decision_style": args.decision_style,
            "news_date_mode": args.news_date_mode,
            "source_sha256": source_hash,
        }.items()):
            raise ValueError("Retry parameters or frozen source hash do not match the incomplete run")
        prior_attempts = int(previous.get("attempts") or 1)
    else:
        output.mkdir(parents=True)
    status = {
        "company": company,
        "decision_style": args.decision_style,
        "news_date_mode": args.news_date_mode,
        "source": str(source),
        "source_sha256": source_hash,
        "attempts": prior_attempts + 1,
        "status": "prepared",
        "scope": "Strategy only on frozen input; no external source added",
    }
    save_json(output / "status.json", status)
    if args.env_file:
        load_env_file(Path(args.env_file).resolve())
    os.environ.update(
        LLM_EXECUTION_ID=output.name,
        LLM_RUN_ID=output.name,
        LLM_RUN_ROLE="final",
        LLM_COMPANY_NAME=company,
        LLM_USAGE_MANIFEST=str(output / "usage.jsonl"),
    )
    try:
        packet, provenance, _, _ = build_compact_strategy_packet(
            bundle, model=args.llm_model,
            split_mixed_date_news=args.news_date_mode == "split",
            canonical_news_sources=args.news_date_mode == "source_events",
        )
        context = build_strategy_context_package(packet, input_bundle=bundle)
        context_mode = str((bundle.get("ablation") or {}).get("strategy_context_mode") or "compact_cards")
        payload = build_strategy_generation_payload(input_bundle=bundle, context=context, context_mode=context_mode)
        prompt = decision_generation_prompt(
            "annual", context_mode=context_mode, decision_style=args.decision_style
        )
        save_json(output / "strategy_packet.json", packet)
        save_json(output / "strategy_provenance.json", provenance)
        save_json(output / "strategy_context.json", context)
        save_json(output / "strategy_generation_context.json", payload)
        (output / "strategy_prompt.md").write_text(prompt, encoding="utf-8")
        raw = run_decision_agent(
            context,
            llm_provider="openai",
            llm_model=args.llm_model,
            llm_timeout=args.llm_timeout,
            decision_horizon_profile="annual",
            generation_payload=payload,
            generation_prompt=prompt,
        )
        save_json(output / "strategy_raw.json", raw)
        decision = align_strategy_decision_evidence_plan(raw, context=context)
        validate_strategy_decision(decision, context=context, required_horizon="12개월")
        save_json(output / "strategy_decision.json", decision)
        price_findings = audit_strategy_price_claims(decision)
        save_json(output / "price_claim_audit.json", {
            "audit_type": "lexical_review_flags_not_semantic_validation",
            "audit_version": PRICE_CLAIM_AUDIT_VERSION,
            "finding_count": len(price_findings),
            "findings": price_findings,
        })
        status["status"] = "completed"
        status["price_claim_review_flags"] = len(price_findings)
        print(json.dumps({
            "company": company,
            "decision_style": args.decision_style,
            "news_date_mode": args.news_date_mode,
            "recommendation": decision["strategy_brief"]["recommendation"],
            "price_claim_review_flags": len(price_findings),
        }, ensure_ascii=False), flush=True)
    except Exception as exc:
        status.update(status="failed", error=str(exc))
        raise
    finally:
        status["source_unchanged"] = hashlib.sha256(source.read_bytes()).hexdigest() == source_hash
        save_json(output / "status.json", status)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--input-bundle", required=True)
    parser.add_argument("--output-dir", required=True)
    parser.add_argument("--decision-style", choices=DECISION_STYLES, default="standard")
    parser.add_argument("--news-date-mode", choices=("preserve", "split", "source_events"), default="preserve")
    parser.add_argument("--llm-model", default="gpt-5.4")
    parser.add_argument("--llm-timeout", type=int, default=300)
    parser.add_argument("--env-file")
    parser.add_argument("--retry-incomplete", action="store_true")
    run(parser.parse_args())
