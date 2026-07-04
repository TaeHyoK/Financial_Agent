"""Execution order and dependencies for the first orchestration version."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class StepSpec:
    name: str
    dependencies: tuple[str, ...] = ()
    requires_llm: bool = False


STEP_SPECS: tuple[StepSpec, ...] = (
    StepSpec("yfinance_layer_1"),
    StepSpec("financial_layer_1"),
    StepSpec("news_collect"),
    StepSpec("news_export", dependencies=("news_collect",)),
    StepSpec("news_llm", dependencies=("news_export",), requires_llm=True),
    StepSpec("news_analysis", dependencies=("financial_layer_1", "yfinance_layer_1", "news_llm"), requires_llm=True),
    StepSpec("news_sy", dependencies=("news_analysis",), requires_llm=True),
    StepSpec("financial_analyst", dependencies=("financial_layer_1", "yfinance_layer_1", "news_llm"), requires_llm=True),
    StepSpec("financial_sy", dependencies=("financial_analyst",), requires_llm=True),
    StepSpec("yfinance_report", dependencies=("financial_layer_1", "yfinance_layer_1", "news_llm"), requires_llm=True),
    StepSpec("yfinance_sy", dependencies=("yfinance_report",), requires_llm=True),
)


def dependency_names(step_name: str) -> tuple[str, ...]:
    for spec in STEP_SPECS:
        if spec.name == step_name:
            return spec.dependencies
    raise KeyError(f"Unknown orchestration step: {step_name}")
