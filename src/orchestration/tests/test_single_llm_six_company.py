from __future__ import annotations

from pathlib import Path

from orchestration.single_llm_six_company import _rebase_output_path


def test_rebase_output_path_uses_active_checkout(tmp_path: Path) -> None:
    project_root = tmp_path / "active-checkout"
    stored = "/old/checkout/Output_total/experiments/example/manifest.json"

    resolved = _rebase_output_path(stored, project_root=project_root)

    assert resolved == (
        project_root / "Output_total/experiments/example/manifest.json"
    ).resolve()


def test_rebase_output_path_resolves_relative_to_project(tmp_path: Path) -> None:
    project_root = tmp_path / "active-checkout"

    resolved = _rebase_output_path(
        "Output_total/experiments/example/manifest.json",
        project_root=project_root,
    )

    assert resolved == (
        project_root / "Output_total/experiments/example/manifest.json"
    ).resolve()
