import importlib.util
import json
import sys
from pathlib import Path

import pytest


SCRIPT = Path(__file__).resolve().parents[1] / "final_report_llm_judge.py"
SPEC = importlib.util.spec_from_file_location("final_report_llm_judge", SCRIPT)
MODULE = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
sys.modules[SPEC.name] = MODULE
SPEC.loader.exec_module(MODULE)


def require_report_bundle():
    if not MODULE.reference_path(MODULE.COMPANIES[0]).is_file():
        pytest.skip("portable report bundle is not present in this checkout")


def ordered_rows(first, second):
    return [
        {"orientation": 1, "state": "success", "normalized_winner": first},
        {"orientation": 2, "state": "success", "normalized_winner": second},
    ]


@pytest.mark.parametrize(
    ("first", "second", "expected"),
    [
        ("full", "full", "Win"),
        ("no_peer", "no_peer", "Loss"),
        ("full", "no_peer", "Tie"),
        ("full", None, "Tie"),
        (None, None, "Tie"),
    ],
)
def test_finrpt_order_combination(first, second, expected):
    assert MODULE.combine_ordered_judgments(ordered_rows(first, second), "no_peer") == expected


def test_error_is_not_converted_to_tie():
    rows = ordered_rows("full", "full")
    rows[1]["state"] = "error"
    assert MODULE.combine_ordered_judgments(rows, "no_peer") == "Error"


def test_pair_specs_are_complete_and_balanced():
    specs = MODULE.pair_specs(MODULE.DEFAULT_SEED)
    assert len(specs) == 60
    assert len({(company, replicate, ablation) for company, replicate, ablation, _ in specs}) == 60
    assert sum(full_first for *_, full_first in specs) == 30


def test_reference_masking_removes_rating_and_target_price_cues():
    require_report_bundle()
    for company in MODULE.COMPANIES:
        raw = MODULE.read_nonempty(MODULE.reference_path(company))
        masked = MODULE.mask_reference(company, raw)
        assert masked != raw
        assert not MODULE.SENSITIVE_REFERENCE_PATTERN.search(masked)


def test_build_tasks_has_two_swapped_orders_per_pair_and_criterion():
    require_report_bundle()
    tasks = MODULE.build_tasks(model=MODULE.DEFAULT_MODEL, seed=MODULE.DEFAULT_SEED)
    assert len(tasks) == 360
    assert len({task.custom_id for task in tasks}) == 360
    groups = {}
    for task in tasks:
        groups.setdefault((task.pair_id, task.criterion), []).append(task)
    assert len(groups) == 180
    for rows in groups.values():
        assert {row.orientation for row in rows} == {1, 2}
        first, second = sorted(rows, key=lambda row: row.orientation)
        assert (first.label_a, first.label_b) == (second.label_b, second.label_a)


def test_prepare_is_offline_and_paid_run_is_double_gated(tmp_path):
    require_report_bundle()
    output = tmp_path / "judge"
    manifest = MODULE.prepare(output, model=MODULE.DEFAULT_MODEL, seed=MODULE.DEFAULT_SEED)
    assert manifest["expected_calls"] == 360
    assert manifest["paid_api_calls"] == 0
    assert json.loads((output / "status.json").read_text(encoding="utf-8"))["state"] == "prepared_not_run"
    with pytest.raises(RuntimeError, match="Paid run blocked"):
        MODULE.run_paid(
            output,
            workers=1,
            retry_count=0,
            execute_paid_api=False,
            confirm_call_count=None,
        )
