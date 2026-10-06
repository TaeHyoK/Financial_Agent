import importlib.util
import json
import sys
from pathlib import Path

import pytest


RUN_CONFIG = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(RUN_CONFIG))
SCRIPT = RUN_CONFIG / "no_peer_target_llm_judge.py"
SPEC = importlib.util.spec_from_file_location("no_peer_target_llm_judge", SCRIPT)
MODULE = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
sys.modules[SPEC.name] = MODULE
SPEC.loader.exec_module(MODULE)


def peer_payload(company="현대건설", peer="GS건설"):
    return {
        "target_company": company,
        "peer_scope": "domestic_only",
        "peer_groups": {
            "target": {"company_name": company},
            "domestic_peers": [{"company_name": peer}],
            "global_peers": [],
        },
        "metrics": [
            {"company_name": company, "financial_metrics": {"operating_margin_pct": 3.0}},
            {"company_name": peer, "financial_metrics": {"operating_margin_pct": 4.0}},
        ],
        "comparison_limits": ["국내 비교기업 한 곳만 포함한다."],
        "excluded_scope": ["complete_industry_average_comparison"],
        "source_files": {"secret": "/absolute/path"},
        "comparison_brief": "generated interpretation must not be evidence",
    }


def ordered_rows(first, second):
    return [
        {"orientation": 1, "state": "success", "normalized_winner": first},
        {"orientation": 2, "state": "success", "normalized_winner": second},
    ]


def test_protocol_has_15_pairs_and_90_balanced_order_calls():
    specs = MODULE.pair_specs(MODULE.DEFAULT_SEED)
    assert len(specs) == 15
    assert len({(company, replicate) for company, replicate, _ in specs}) == 15
    assert sum(full_first for _, _, full_first in specs) in {7, 8}
    assert MODULE.EXPECTED_CALLS == 90


def test_peer_evidence_keeps_facts_and_drops_paths_and_generated_prose():
    clean = MODULE.sanitize_peer_evidence(peer_payload(), company="현대건설", replicate="r01")
    assert clean["target_company"] == "현대건설"
    assert len(clean["metrics"]) == 2
    assert "source_files" not in clean
    assert "comparison_brief" not in clean


def test_peer_evidence_requires_target_and_peer_metrics():
    payload = peer_payload()
    payload["metrics"] = payload["metrics"][:1]
    with pytest.raises(ValueError, match="Target/peer metrics missing"):
        MODULE.sanitize_peer_evidence(payload, company="현대건설", replicate="r01")


def test_prompt_is_target_centered_and_contains_common_peer_evidence():
    evidence = MODULE.sanitize_peer_evidence(
        peer_payload(), company="현대건설", replicate="r01"
    )
    prompt = MODULE.user_prompt(
        company="현대건설",
        as_of_date="2025-10-20",
        reference="reference",
        peer_evidence=evidence,
        candidate_a="candidate a",
        candidate_b="candidate b",
        criterion="NP1",
    )
    assert "[Common Peer Evidence]" in prompt
    assert "대상기업의 실적과 사업구조" in prompt
    assert "경쟁사 이름" in prompt
    assert "/absolute/path" not in prompt


@pytest.mark.parametrize(
    ("first", "second", "expected"),
    [
        ("full", "full", "Win"),
        ("no_peer", "no_peer", "Loss"),
        ("full", "no_peer", "Tie"),
        (None, None, "Tie"),
    ],
)
def test_order_combination(first, second, expected):
    rows = ordered_rows(first, second)
    assert MODULE.base.combine_ordered_judgments(rows, "no_peer") == expected


def test_paid_run_is_double_gated(tmp_path):
    with pytest.raises(RuntimeError, match="confirm-call-count 90"):
        MODULE.run_paid(
            tmp_path,
            workers=1,
            retry_count=0,
            execute_paid_api=False,
            confirm_call_count=None,
            env_file=tmp_path / ".env",
        )


def test_load_openai_api_key_reads_only_requested_value(tmp_path):
    env_file = tmp_path / ".env"
    env_file.write_text(
        "OPENAI_API_KEY=test-secret\nDART_API_KEY=unrelated-secret\n",
        encoding="utf-8",
    )
    assert MODULE.load_openai_api_key(env_file) == "test-secret"


def test_load_openai_api_key_rejects_missing_or_empty_value(tmp_path):
    missing = tmp_path / "missing.env"
    with pytest.raises(FileNotFoundError, match="environment file not found"):
        MODULE.load_openai_api_key(missing)

    empty = tmp_path / ".env"
    empty.write_text("OPENAI_API_KEY=\n", encoding="utf-8")
    with pytest.raises(RuntimeError, match="missing or empty"):
        MODULE.load_openai_api_key(empty)


def test_run_parser_defaults_to_repo_env_file():
    args = MODULE.parser().parse_args(["run"])
    assert args.env_file == MODULE.WORKSPACE / "configs/.env"


def test_summary_keeps_errors_out_of_denominator():
    pair_results = [
        {"criterion": "NP1", "company": "현대건설", "outcome_for_full": "Win"},
        {"criterion": "NP1", "company": "현대건설", "outcome_for_full": "Tie"},
        {"criterion": "NP1", "company": "현대건설", "outcome_for_full": "Error"},
    ]
    row = MODULE.summary_rows_for(pair_results)[0]
    assert row["win"] == 1
    assert row["tie"] == 1
    assert row["error"] == 1
    assert row["valid_n"] == 2
    assert row["adjusted_win_rate"] == 0.75


def test_committed_np_results_match_pair_aggregation_and_evidence_hashes():
    exported = MODULE.WORKSPACE / "ablation_results/no_peer_target_llm_judge"
    pairs = json.loads((exported / "pair_results.json").read_text(encoding="utf-8"))
    raw = json.loads((exported / "raw_normalized_results.json").read_text(encoding="utf-8"))
    summary = json.loads((exported / "summary.json").read_text(encoding="utf-8"))
    by_company = json.loads((exported / "summary_by_company.json").read_text(encoding="utf-8"))

    assert len(pairs) == 45
    assert len(raw) == 90
    assert all(row["state"] == "success" for row in raw)
    assert MODULE.summary_rows_for(pairs) == summary
    assert [
        row for company in MODULE.COMPANIES for row in MODULE.summary_rows_for(pairs, company=company)
    ] == by_company

    for row in raw:
        old_path = Path(row["sources"]["peer_evidence"])
        evidence = exported / "common_peer_evidence" / old_path.relative_to(
            "evaluation/no_peer_target_llm_judge/common_peer_evidence"
        )
        assert MODULE.base.sha_file(evidence) == row["source_sha256"]["peer_evidence"]
