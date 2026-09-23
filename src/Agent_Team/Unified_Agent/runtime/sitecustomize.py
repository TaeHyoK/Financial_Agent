"""Narrow runtime fixes used only by subprocesses launched from one-team.

Existing frozen generation modules remain unchanged.  Python loads this module because the
one-team runner prepends this directory to PYTHONPATH and opts in through an
environment variable.
"""

from __future__ import annotations

import importlib.abc
import importlib.machinery
import os
import sys
from types import ModuleType
from typing import Any


_WRITER_TARGET = "Agent_Team.Writer_Agent.html_report_writer"


def _patch_writer(module: ModuleType) -> None:
    from model_policy import apply_request_policy
    execute = getattr(module, "execute_with_telemetry", None)
    if execute is not None and not getattr(execute, "_ablation_luna_policy", False):
        def with_policy(*args, **kwargs):
            request = kwargs.get("request_payload")
            if isinstance(request, dict):
                apply_request_policy(request)
            return execute(*args, **kwargs)
        with_policy._ablation_luna_policy = True
        module.execute_with_telemetry = with_policy
    original = getattr(module, "_limitation_card_assignments", None)
    if original is None or getattr(original, "_ablation_empty_limitations_safe", False):
        return

    def safe_assignments(
        writer_packet: dict[str, Any],
        limitations: list[dict[str, Any]],
    ) -> dict[str, list[str]]:
        if not limitations:
            return {}
        return original(writer_packet, limitations)

    safe_assignments._ablation_empty_limitations_safe = True  # type: ignore[attr-defined]
    module._limitation_card_assignments = safe_assignments


class _PatchLoader(importlib.abc.Loader):
    def __init__(self, wrapped: importlib.abc.Loader, patch: Any) -> None:
        self.wrapped = wrapped
        self.patch = patch

    def create_module(self, spec: Any) -> ModuleType | None:
        creator = getattr(self.wrapped, "create_module", None)
        return creator(spec) if creator is not None else None

    def exec_module(self, module: ModuleType) -> None:
        self.wrapped.exec_module(module)
        self.patch(module)


class _PatchFinder(importlib.abc.MetaPathFinder):
    def find_spec(
        self,
        fullname: str,
        path: list[str] | None,
        target: ModuleType | None = None,
    ) -> Any:
        integrated_targets = {
            "Agent_Team.Competitor_Agent.peer_comparison",
            "Agent_Team.Competitor_Agent.comparison_agent",
            "Agent_Team.Strategy_Agent.agent",
            "Agent_Team.Strategy_Agent.packet",
            "Agent_Team.Strategy_Agent.context",
            "Agent_Team.Visualization_Agent.data_loader",
        }
        single_report = os.getenv("ONE_TEAM_SINGLE_REPORT") == "1"
        if fullname not in {_WRITER_TARGET, "shared.domain_llm"} and not (single_report and fullname in integrated_targets):
            return None
        spec = importlib.machinery.PathFinder.find_spec(fullname, path)
        if spec is not None and spec.loader is not None:
            if fullname == _WRITER_TARGET:
                patch = _patch_writer
            elif fullname == "shared.domain_llm":
                from model_policy import patch_domain_module
                patch = patch_domain_module
            else:
                from integrated_handoff import patch_module
                patch = patch_module
            spec.loader = _PatchLoader(spec.loader, patch)
        return spec


if os.getenv("ONE_TEAM_RUNTIME") == "1":
    sys.meta_path.insert(0, _PatchFinder())
