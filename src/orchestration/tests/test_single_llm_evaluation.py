from __future__ import annotations

from orchestration.single_llm_evaluation import (
    _load_candidate_snapshot,
    _single_llm_company_rows,
    build_single_llm_evidence_bundle,
    union_evidence_bundles,
)


def _single_bundle() -> dict:
    return {
        "selected_date": "2025-10-31",
        "selected_date_policy": "before_market_open",
        "bundle_sha256": "single-hash",
        "target": {
            "company_name": "테스트기업",
            "run_key": "테스트기업_20251031",
            "ticker": "000000.KS",
            "corp_code": "00000000",
        },
        "news_selection": {},
        "evidence_catalog": [
            {
                "evidence_id": "FINANCIAL_TARGET",
                "role": "target",
                "domain": "financial",
                "evidence_type": "raw_financial",
                "as_of_date": "2025-08-14",
                "payload": {"revenue": 100},
            }
        ],
    }


def test_single_llm_bundle_converts_to_candidate_neutral_cards() -> None:
    evidence = build_single_llm_evidence_bundle(_single_bundle())

    assert evidence["target_company"]["company_name"] == "테스트기업"
    assert evidence["target_company"]["as_of_date"] == "2025-10-31"
    assert [item["card_key"] for item in evidence["cards"]] == [
        "FINANCIAL_TARGET"
    ]
    assert evidence["cards"][0]["primary_observation"] == {"revenue": 100}


def test_evidence_union_preserves_distinct_candidate_card_keys() -> None:
    single = build_single_llm_evidence_bundle(_single_bundle())
    revised = {
        "version": "candidate_neutral_evidence_v1",
        "target_company": {
            "company_name": "테스트기업",
            "as_of_date": "2025-10-31",
        },
        "selected_date_policy": "before_market_open",
        "cards": [
            {
                "card_key": "financial.same_period_trend",
                "domain": "financial",
                "primary_observation": {"growth": 10},
            }
        ],
        "reader_limitations": [],
        "limitation_requirements": [],
        "bundle_sha256": "revised-hash",
    }

    union = union_evidence_bundles(revised, single)

    assert [item["card_key"] for item in union["cards"]] == [
        "FINANCIAL_TARGET",
        "financial.same_period_trend",
    ]
    assert union["coverage_summary"]["source_bundle_count"] == 2


def test_load_candidate_snapshot_uses_archived_judge_visible_reports(tmp_path) -> None:
    pair_dir = tmp_path / "comparisons" / "pair-1"
    pair_dir.mkdir(parents=True)
    (pair_dir / "candidate_full_visible.json").write_text(
        '{"text":"full"}', encoding="utf-8"
    )
    (pair_dir / "candidate_single_llm_visible.json").write_text(
        '{"text":"single"}', encoding="utf-8"
    )

    snapshot = _load_candidate_snapshot("pair-1", tmp_path)

    assert snapshot is not None
    assert snapshot["full"] == {"text": "full"}
    assert snapshot["single_llm"] == {"text": "single"}
    assert snapshot["provenance"]["mode"] == "frozen_judge_visible_snapshot"


def test_company_rows_aggregate_three_pairs_and_six_axes() -> None:
    axes = {
        axis: {
            "outcome": "full_win" if index < 4 else "tie",
            "score_for_full": 1.0 if index < 4 else 0.5,
        }
        for index, axis in enumerate(
            (
                "financial_numeric",
                "news",
                "company_market_peer",
                "investment",
                "risk",
                "writing",
            )
        )
    }
    pairs = [
        {
            "status": "success",
            "company_name": "테스트기업",
            "axes": axes,
            "order_consistency_rate": 5 / 6,
        }
        for _ in range(3)
    ]

    rows = _single_llm_company_rows(pairs)

    assert rows == [
        {
            "company": "테스트기업",
            "report_pairs": 3,
            "full_win": 12,
            "tie": 6,
            "single_llm_win": 0,
            "adjusted_win_rate_for_revised_full": 15 / 18,
            "order_consistency": 5 / 6,
        }
    ]
