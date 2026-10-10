"""Check the public artifact layout without accessing private data or APIs."""

import csv
import hashlib
import json
from pathlib import Path

from real_report_evaluation.extract import extract_html_body


ROOT = Path(__file__).resolve().parents[1]


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_published_reports_match_manifest():
    manifest = json.loads((ROOT / "final_reports/report_manifest.json").read_text())
    reports = list((ROOT / "final_reports").rglob("*.html"))
    assert manifest["reports"] == len(reports) == 75
    assert {r["report"] for r in manifest["records"]} == {
        str(path.relative_to(ROOT)) for path in reports
    }
    for row in manifest["records"]:
        path = ROOT / row["report"]
        assert digest(path) == row["published_report_sha256"]
        body_hash = hashlib.sha256((extract_html_body(path) + "\n").encode()).hexdigest()
        assert body_hash == row["published_body_sha256"]
        assert row["body_identical_to_evaluation"] == (
            body_hash == row["evaluated_body_sha256"]
        )


def test_evaluation_hashes_are_preserved():
    manifest = json.loads((ROOT / "final_reports/report_manifest.json").read_text())
    with (ROOT / manifest["evaluation_metrics"]).open(encoding="utf-8-sig") as stream:
        metrics = {
            (r["company"], r["condition"], int(r["replicate"])): r
            for r in csv.DictReader(stream)
        }
    assert len(metrics) == 75
    assert manifest["evaluation_scores_recomputed"] is False
    for row in manifest["records"]:
        metric = metrics[row["company"], row["condition"], row["replicate"]]
        assert row["evaluated_report_sha256"] == metric["report_sha256"]
        assert row["evaluated_body_sha256"] == metric["generated_body_sha256"]


def test_frozen_body_extractor_and_scorer_are_unchanged():
    protocol = json.loads(
        (ROOT / "ablation/results/repeated_standard_5companies/protocol.json").read_text()
    )
    for filename, field in (("extract.py", "extractor_sha256"), ("runner.py", "scorer_sha256")):
        assert digest(ROOT / "ablation/real_report_evaluation" / filename) == protocol[field]


def test_public_layout_and_framework_asset():
    for name in ("run_config", "ablation_suite", "ablation_evaluation", "real_report_evaluation", "results"):
        assert (ROOT / "ablation" / name).is_dir()
    assert not (ROOT / "docs/history").exists()
    assert not (ROOT / "final_reports_redesigned").exists()
    assert not (ROOT / "docs/assets/pipeline_architecture.jpg").exists()
    assert (ROOT / "docs/assets/multi_agent_financial_framework.jpg").is_file()
    readme = (ROOT / "README.md").read_text()
    assert "redesigned" not in readme.lower()
    assert "docs/assets/multi_agent_financial_framework.jpg" in readme


def test_collection_manifest_uses_a_named_ref_not_a_machine_specific_commit():
    manifest = json.loads((ROOT / "ablation/run_config/collection_manifest.json").read_text())
    assert manifest["repository"]["ref"] == "paper-collection"
    assert "commit" not in manifest["repository"]
    assert manifest["workspace"] == "."
    assert manifest["environment_file"] == "configs/.env"
