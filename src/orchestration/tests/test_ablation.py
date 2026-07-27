from __future__ import annotations

from argparse import Namespace

import pytest

from orchestration.ablation import config_from_args


def _args(**overrides):
    values = {
        "only_domain": "",
        "exclude_domain": [],
        "no_sy": False,
        "primary_data_only": False,
        "no_competitor": False,
        "experiment_name": "",
    }
    values.update(overrides)
    return Namespace(**values)


def test_only_dart_normalizes_to_financial() -> None:
    config = config_from_args(_args(only_domain="dart", no_sy=True))

    assert config.included_domains == ("financial",)
    assert config.use_sy is False
    assert config.experiment_name == "only_financial__no_sy"


def test_excluded_domains_keep_canonical_order() -> None:
    config = config_from_args(_args(exclude_domain=["news", "yf"]))

    assert config.included_domains == ("financial",)
    assert config.excluded_domains == ("news", "yfinance")


def test_only_and_exclude_are_mutually_exclusive() -> None:
    with pytest.raises(ValueError, match="cannot be used together"):
        config_from_args(_args(only_domain="news", exclude_domain=["financial"]))


def test_all_domains_cannot_be_excluded() -> None:
    with pytest.raises(ValueError, match="At least one"):
        config_from_args(_args(exclude_domain=["financial", "news", "yfinance"]))


def test_structural_ablation_modes_are_recorded_and_named() -> None:
    config = config_from_args(
        _args(full_context=True, free_form_writer=True)
    )

    assert config.active is True
    assert config.strategy_context_mode == "full_reports"
    assert config.writer_mode == "free_form"
    assert config.experiment_name == "full_context__free_form_writer"
    assert config.as_dict()["strategy_context_mode"] == "full_reports"
