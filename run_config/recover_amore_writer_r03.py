"""Audited offline recovery of the saved r03 one-team Amore Writer response.

Only two chart-link metadata arrays change. No prompt, prose, source evidence,
model setting or shared validator is changed. No API call is permitted here.
"""
import argparse
import copy
import fcntl
import json
import os
import shutil
import subprocess
import sys
from unittest.mock import patch

import run_repeated_reports as run

KEY = "r03/one_team/아모레퍼시픽"
ROOT = run.BATCH / "replicate_03/one_team/아모레퍼시픽"
WRITER = ROOT / "Writer/20251107"
ARCHIVE = ROOT / "recovery/chart_basis_links"
sys.path.insert(0, str(run.REPO / "src/Agent_Team/Writer Agent"))
import writer_agent
import html_report_writer as writer


def correction():
    original = ARCHIVE / "llm_writer_output.original.json"
    saved = run.read(original if original.exists() else WRITER / "llm_writer_output.json")
    handoff = run.read(WRITER / "writer_editorial_packet_v3.json")
    sources = run.read(WRITER / "source_files.json")
    catalog = run.read(sources["chart_catalog"])
    fingerprint = writer.writer_request_fingerprint(writer_handoff=handoff, model=saved["model"],
        writer_mode=saved["writer_mode"], chart_catalog=catalog)
    if saved["fingerprint"] != fingerprint:
        raise ValueError("Saved response no longer matches the frozen Writer input")
    updated = copy.deepcopy(saved)
    expected = {
        "profitability_margin": ["financial.same_period_trend", "integrated.finding_1"],
        "stock_vs_kospi": ["market.relative_performance", "integrated.finding_6"],
    }
    changes = []
    for item in updated["raw_payload"]["chart_selection_details"]:
        key = item["chart_key"]
        if item["basis_card_keys"] != expected[key]:
            raise ValueError("Response differs from the inspected failure")
        before = list(item["basis_card_keys"])
        item["basis_card_keys"] = before[:1]
        changes.append({"chart_key": key, "before": before, "after": item["basis_card_keys"],
            "reason": "Keep the original direct compatible source card; remove the redundant integrated chart link only."})
    restored = copy.deepcopy(updated)
    for item in restored["raw_payload"]["chart_selection_details"]:
        item["basis_card_keys"] = expected[item["chart_key"]]
    assert restored == saved, "Recovery must not change any other response field"
    writer.validate_raw_writer_payload(updated["raw_payload"])
    writer.normalize_report_payload(copy.deepcopy(updated["raw_payload"]), writer_handoff=handoff,
        writer_mode=saved["writer_mode"], chart_catalog=catalog)
    updated["offline_recovery"] = {"original_response": str(ARCHIVE / "llm_writer_output.original.json"),
        "changes": changes, "prose_unchanged": True, "new_api_calls": 0}
    return updated, changes


def execute():
    with (run.BATCH / "run.lock").open("a+") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        manifest = run.check()
        state = run.read(run.STATUS)
        if any(r["key"] == KEY for r in state["completed"]):
            raise RuntimeError("This report is already complete; refusing duplicate recovery")
        usage_hash = run.sha(run.USAGE)
        other_hashes = {r["report"]: run.sha(r["report"]) for r in state["completed"]}
        # Include all existing downstream analytical inputs, not just the other reports.
        protected = {}
        for domain in ("Strategy", "Competitor", "runs/20251107/unified_domain_team",
                       "비교기업/LG생활건강/runs/20251107/unified_domain_team"):
            for p in (ROOT / domain).rglob("*"):
                if p.is_file():
                    protected[str(p)] = run.sha(p)
        updated, changes = correction()
        ARCHIVE.mkdir(parents=True, exist_ok=True)
        for source, name in ((WRITER / "llm_writer_output.json", "llm_writer_output.original.json"),
                             (WRITER / "writer_failure_report.json", "writer_failure_report.original.json"),
                             (run.STATUS, "batch_status.before.json")):
            if not (ARCHIVE / name).exists():
                shutil.copy2(source, ARCHIVE / name)
        run.save(ARCHIVE / "recovery_audit.json", {"state": "started", "at": run.now(), "changes": changes,
            "new_api_calls": 0, "prose_unchanged": True, "usage_sha256_before": usage_hash,
            "original_response_sha256": run.sha(ARCHIVE / "llm_writer_output.original.json")})
        spec = next(s for s in manifest["jobs"] if s["replicate"] == 3 and s["condition"] == "one_team"
                    and s["target_company"] == "아모레퍼시픽")
        paths = run.make_paths(spec)
        commands = run.read(paths.execution_dir / "commands.json")
        command = next(s["command"] for s in commands if s["stage"] == "writer")
        def replay(**kwargs):
            assert kwargs["model"] == updated["model"]
            return copy.deepcopy(updated["raw_payload"]), copy.deepcopy(updated)
        # Request replacement is confined to this offline recovery process.
        with patch.object(writer_agent, "request_html_report_payload", side_effect=replay), \
             patch("socket.create_connection", side_effect=RuntimeError("Network forbidden during offline recovery")):
            writer_agent.main(command[2:])
        state["stages"].append({"key": KEY, "stage": "writer", "completed_at": run.now(),
                                "offline_recovery": str(ARCHIVE / "recovery_audit.json")})
        for item in commands:
            if item["stage"] not in {"charts", "render"}:
                continue
            log = ARCHIVE / f"{item['stage']}.log"
            with log.open("a") as stream:
                subprocess.run(item["command"], cwd=run.REPO, env=os.environ.copy(),
                    stdout=stream, stderr=subprocess.STDOUT, timeout=900, check=True)
            state["stages"].append({"key": KEY, "stage": item["stage"], "completed_at": run.now()})
        if run.read(paths.writer_dir / "writer_run_status.json")["status"] != "success":
            raise RuntimeError("Recovered Writer did not finish")
        run.flow._publish_final_report(paths)
        assert run.sha(run.USAGE) == usage_hash, "Unexpected API usage change"
        assert all(run.sha(p) == digest for p, digest in {**other_hashes, **protected}.items())
        run.check()
        state["completed"].append({"key": KEY, "report": str(paths.published_report),
            "sha256": run.sha(paths.published_report), "completed_at": run.now(),
            "offline_recovery": str(ARCHIVE / "recovery_audit.json")})
        for failure in state["failures"]:
            if failure["key"] == KEY:
                failure.update(resolved_at=run.now(), resolution="offline_chart_link_metadata_recovery")
        state.update(state="success", reports_completed=len(state["completed"]), current_job=KEY,
            current_stage="complete", completed_at=run.now(), updated_at=run.now(), usage=run.usage_summary())
        run.save(paths.execution_dir / "repeat_manifest.json", {"status": "success", "specification": spec,
            "source_preparation": str(run.MANIFEST), "report": str(paths.published_report),
            "offline_recovery": str(ARCHIVE / "recovery_audit.json")})
        run.save(run.STATUS, state)
        run.write_index(state["completed"])
        run.save(ARCHIVE / "recovery_audit.json", {"state": "success", "at": run.now(), "changes": changes,
            "new_api_calls": 0, "prose_unchanged": True, "model_and_prompts_unchanged": True,
            "other_49_reports_unchanged": True, "prior_analyses_unchanged": True,
            "usage_sha256_before": usage_hash, "usage_sha256_after": run.sha(run.USAGE),
            "original_response_sha256": run.sha(ARCHIVE / "llm_writer_output.original.json"),
            "report": str(paths.published_report)})
        print(json.dumps({"reports_completed": len(state["completed"]), "additional_api_calls": 0,
            "report": str(paths.published_report)}, ensure_ascii=False))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("check", "run"))
    if parser.parse_args().action == "check":
        _, changes = correction()
        print(json.dumps({"validation": "passed", "changes": changes, "paid_calls": 0}, ensure_ascii=False))
    else:
        execute()
