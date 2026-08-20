from __future__ import annotations

import pytest

from orchestration.revised_no_sy_ablation import _primary_source_condition


def _rows(condition: str, replicates: int = 3) -> dict[tuple[str, int], dict[str, str]]:
    return {
        (condition, replicate): {"status": "success"}
        for replicate in range(1, replicates + 1)
    }


def test_direct_no_sy_primary_only_source_is_preferred() -> None:
    rows = {**_rows("primary_only"), **_rows("no_sy_primary_only")}

    assert _primary_source_condition(rows, 3) == "no_sy_primary_only"


def test_legacy_primary_only_source_remains_supported() -> None:
    assert _primary_source_condition(_rows("primary_only"), 3) == "primary_only"


def test_incomplete_primary_source_is_rejected() -> None:
    with pytest.raises(ValueError, match="Missing successful no_subdata sources"):
        _primary_source_condition(
            {
                ("no_sy_primary_only", 1): {"status": "success"},
                ("no_sy_primary_only", 2): {"status": "success"},
            },
            3,
        )
