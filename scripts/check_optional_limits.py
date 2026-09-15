"""Check Strategy and Writer on saved domain outputs, preserving every paid response."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "src"), str(ROOT / "src/Agent_Team/Writer Agent")]
from Agent_Team.Strategy_Agent.agent import run_decision_agent_v5, decision_prompt_v5
from Agent_Team.Strategy_Agent.contracts_v2 import build_compact_strategy_packet_v2
from Agent_Team.Strategy_Agent.contracts_v5 import (
    build_strategy_context_package_v5, align_strategy_decision_v5_evidence_plan,
    validate_strategy_decision_v5,
)
from writer_handoff import build_writer_editorial_packet
from html_report_writer import request_html_report_payload, validate_raw_writer_payload, normalize_report_payload, _build_context
from html_report_validator import validate_html_report
from formatted_html_renderer import build_complete_html
from orchestration.usage_summary import summarize_execution_usage


def save(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def run(args):
    source = Path(args.domain_run) / "normalized_domain_bundle.json"
    digest = hashlib.sha256(source.read_bytes()).hexdigest()
    bundle = json.loads(source.read_text())
    output = Path(args.output_dir).resolve()
    output.mkdir(parents=True, exist_ok=False)
    packet, provenance, _, _ = build_compact_strategy_packet_v2(bundle)
    context = build_strategy_context_package_v5(packet, input_bundle=bundle)
    save(output / "strategy_packet.json", packet)
    save(output / "strategy_context.json", context)
    save(output / "strategy_provenance.json", provenance)
    (output / "strategy_prompt.md").write_text(decision_prompt_v5("annual"), encoding="utf-8")
    status = {"status": "prepared", "model": "gpt-5.4-mini", "source_sha256": digest,
              "scope": "saved target domain outputs + retained peer context; no peer rerun, no chart generation, no ablation evaluation"}
    if args.strategy_output:
        status["reused_strategy_path"] = str(Path(args.strategy_output).resolve())
        status["reused_strategy_sha256"] = hashlib.sha256(Path(args.strategy_output).read_bytes()).hexdigest()
    save(output / "status.json", status)
    if not args.live:
        return
    from dotenv import load_dotenv
    load_dotenv(ROOT / "configs/.env")
    if not os.getenv("OPENAI_API_KEY"):
        raise RuntimeError("OPENAI_API_KEY unavailable")
    os.environ.update(LLM_EXECUTION_ID=output.name, LLM_RUN_ID=output.name, LLM_RUN_ROLE="final",
                      LLM_COMPANY_NAME=bundle["target_company"]["company_name"],
                      LLM_USAGE_MANIFEST=str(output / "usage.jsonl"))
    try:
        raw = (json.loads(Path(args.strategy_output).read_text()) if args.strategy_output else
               run_decision_agent_v5(context, llm_provider="openai", llm_model="gpt-5.4-mini", llm_timeout=300))
        save(output / "strategy_raw.json", raw)
        print("Strategy response saved", flush=True)
        decision = align_strategy_decision_v5_evidence_plan(raw, context=context)
        validate_strategy_decision_v5(decision, context=context, required_horizon="12개월")
        save(output / "strategy_decision.json", decision)
        if args.strategy_only:
            status["status"] = "strategy_completed"
            print(json.dumps({"status": status["status"], "brief": decision["strategy_brief"]}, ensure_ascii=False), flush=True)
            return
        handoff, _ = build_writer_editorial_packet(strategy_packet=packet, strategy_decision=decision,
                                                  strategy_provenance=provenance)
        save(output / "writer_handoff.json", handoff)
        save(output / "writer_context.json", _build_context(writer_handoff=handoff))
        raw_writer, metadata = request_html_report_payload(writer_handoff=handoff, model="gpt-5.4-mini")
        save(output / "writer_raw.json", raw_writer)
        save(output / "writer_call.json", metadata)
        print("Writer response saved", flush=True)
        validate_raw_writer_payload(raw_writer)
        report = normalize_report_payload(raw_writer, writer_handoff=handoff)
        save(output / "report_payload.json", report)
        html = build_complete_html(report)
        company = str(bundle["target_company"]["company_name"]).replace("/", "_").replace("\\", "_")
        (output / f"report_{company}.html").write_text(html, encoding="utf-8")
        validation = validate_html_report(report_payload=report, html_content=html, writer_handoff=handoff)
        save(output / "validation.json", validation)
        status["status"] = "completed" if validation["status"] == "pass" else "validation_failed"
        print(json.dumps({"status": status["status"], "decision_rationale": decision["strategy_brief"].get("decision_rationale"), "decision_limitation": decision["strategy_brief"]["decision_limitation"],
                          "blocking_failures": validation["blocking_failures"]}, ensure_ascii=False), flush=True)
    except Exception as exc:
        status.update(status="failed", error=str(exc))
        raise
    finally:
        status["source_unchanged"] = hashlib.sha256(source.read_bytes()).hexdigest() == digest
        if args.strategy_output:
            status["reused_strategy_unchanged"] = hashlib.sha256(Path(args.strategy_output).read_bytes()).hexdigest() == status["reused_strategy_sha256"]
        save(output / "status.json", status)
        save(output / "usage_summary.json", summarize_execution_usage(
            output / "usage.jsonl", execution_id=output.name, pipeline_completed=status["status"] in {"completed", "strategy_completed"},
            expected_logical_calls_by_role={"target": 0, "peer": 0, "final": (0 if args.strategy_output else 1) + (0 if args.strategy_only else 1)}))


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--domain-run", required=True)
    parser.add_argument("--output-dir", required=True)
    parser.add_argument("--live", action="store_true")
    parser.add_argument("--strategy-output", help="Reuse a saved Strategy response and call only Writer")
    parser.add_argument("--strategy-only", action="store_true", help="Stop after saving and validating Strategy for qualitative review")
    run(parser.parse_args())
