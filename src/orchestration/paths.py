"""Path resolver for one Agent_Team orchestration run."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from .config import (
    OUTPUT_ROOT,
    PROJECT_ROOT,
    RunConfig,
    agent_output_dir,
    company_output_dir,
    run_output_dir,
    safe_label,
)


@dataclass(frozen=True)
class RunPaths:
    """All output paths that the orchestration layer owns or references."""

    project_root: Path
    output_root: Path
    run_key: str
    company_name: str
    selected_date: str

    @property
    def company_dir(self) -> Path:
        return company_output_dir(self.output_root, self.company_name)

    @property
    def financial_dir(self) -> Path:
        return agent_output_dir(self.output_root, self.run_key, "Financial")

    @property
    def financial_agent_pipeline_dir(self) -> Path:
        return self.financial_dir / "agent_pipeline"

    @property
    def news_dir(self) -> Path:
        return agent_output_dir(self.output_root, self.run_key, "News")

    @property
    def news_context_export_dir(self) -> Path:
        return self.news_dir / "context_exports"

    @property
    def news_context_export_week_dir(self) -> Path:
        return self.news_context_export_dir / "week"

    @property
    def news_report_context(self) -> Path:
        return (
            self.news_dir
            / "artifacts"
            / "reports"
            / "packs"
            / f"{safe_label(self.company_name)}_{self._information_cutoff_date}"
            / "report_context.json"
        )

    @property
    def news_analysis_output_dir(self) -> Path:
        return self.news_dir / "output"

    @property
    def yfinance_dir(self) -> Path:
        return agent_output_dir(self.output_root, self.run_key, "Y_Finance")

    @property
    def run_dir(self) -> Path:
        return run_output_dir(self.output_root, self.run_key)

    @property
    def _information_cutoff_date(self) -> str:
        from datetime import datetime, timedelta

        selected = datetime.strptime(self.selected_date, "%Y%m%d").date()
        return (selected - timedelta(days=1)).strftime("%Y%m%d")

    @property
    def run_config_copy(self) -> Path:
        return self.run_dir / "run_config.json"

    @property
    def run_manifest(self) -> Path:
        return self.run_dir / "run_manifest.json"

    @property
    def run_status(self) -> Path:
        return self.run_dir / "run_status.json"

    @property
    def run_trace(self) -> Path:
        return self.run_dir / "run_trace.json"

    @property
    def llm_usage_manifest(self) -> Path:
        return self.run_dir / "llm_usage_manifest.jsonl"

    @property
    def step_fingerprints(self) -> Path:
        return self.run_dir / "step_fingerprints.json"

    @property
    def errors(self) -> Path:
        return self.run_dir / "errors.json"

    @property
    def financial_runtime_manifest(self) -> Path:
        return self.run_dir / "financial_input_manifest.json"

    @property
    def dart_main(self) -> Path:
        return self.financial_dir / "dart_main.json"

    @property
    def dart_master(self) -> Path:
        return self.financial_dir / "dart_master.json"

    @property
    def dart_lightweight(self) -> Path:
        return self.financial_dir / "dart_lightweight.json"

    @property
    def financial_analyst_report(self) -> Path:
        return self.financial_agent_pipeline_dir / "pipeline_financial_analyst_report_output.json"

    @property
    def financial_analyst_trace(self) -> Path:
        return self.financial_agent_pipeline_dir / "pipeline_financial_analyst_report_trace.json"

    @property
    def financial_pipeline_manifest(self) -> Path:
        return self.financial_agent_pipeline_dir / "pipeline_manifest.json"

    @property
    def financial_final_report(self) -> Path:
        return self.financial_dir / "final_report.json"

    @property
    def news_llm_period_summaries(self) -> Path:
        return self.news_context_export_week_dir / "llm_period_summaries.json"

    @property
    def news_company_top20(self) -> Path:
        return self.news_context_export_week_dir / "recent_raw_input.json"

    @property
    def news_handoff(self) -> Path:
        return self.news_analysis_output_dir / "news_agent_handoff.json"

    @property
    def news_evidence_map(self) -> Path:
        return self.news_analysis_output_dir / "news_agent_evidence_map.json"

    @property
    def news_final_report(self) -> Path:
        return self.news_dir / "final_report.json"

    @property
    def market_summary_dated(self) -> Path:
        return self.yfinance_dir / f"market_summary_{self.selected_date}.json"

    @property
    def market_summary(self) -> Path:
        return self.yfinance_dir / "market_summary.json"

    @property
    def valuation_snapshot(self) -> Path:
        return self.yfinance_dir / "valuation_snapshot.json"

    @property
    def yfinance_analyst_report(self) -> Path:
        return self.yfinance_dir / "yfinance_analyst_report.json"

    @property
    def yfinance_analyst_report_md(self) -> Path:
        return self.yfinance_dir / "yfinance_analyst_report.md"

    @property
    def yfinance_final_report(self) -> Path:
        return self.yfinance_dir / "final_report.json"

    def final_alias_sources(self) -> dict[str, dict[str, Path]]:
        return {
            "financial": {
                "final_report": self.financial_analyst_report,
            },
            "news": {
                "final_report": self.news_handoff,
            },
            "yfinance": {
                "final_report": self.yfinance_analyst_report,
            },
        }

    def final_alias_targets(self) -> dict[str, dict[str, Path]]:
        return {
            "financial": {
                "final_report": self.financial_final_report,
            },
            "news": {
                "final_report": self.news_final_report,
            },
            "yfinance": {
                "final_report": self.yfinance_final_report,
            },
        }

    def ensure_directories(self) -> None:
        for path in (
            self.financial_dir,
            self.news_context_export_dir,
            self.news_analysis_output_dir,
            self.yfinance_dir,
            self.run_dir,
        ):
            path.mkdir(parents=True, exist_ok=True)


def resolve_run_paths(run_config: RunConfig, output_root: str | Path | None = None) -> RunPaths:
    root = Path(output_root).expanduser().resolve() if output_root else OUTPUT_ROOT
    return RunPaths(
        project_root=PROJECT_ROOT,
        output_root=root,
        run_key=run_config.run_key,
        company_name=run_config.company_name,
        selected_date=run_config.selected_date,
    )
