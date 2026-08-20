from __future__ import annotations

import argparse
import importlib.util
import sys
from datetime import date
from pathlib import Path
from typing import Any

from tqdm.auto import tqdm

from . import analysis_agent
from .context_export import build_context_exports, execute_llm_summary_request
from .workflow import NewsWorkflow, WorkflowRequest


PIPELINE_PHASES = ["collect", "export", "llm", "analysis", "sy"]


def _project_root() -> Path:
    return Path(__file__).resolve().parents[3]


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run News workflow phases")
    parser.add_argument(
        "--phase",
        choices=[*PIPELINE_PHASES, "all"],
        default="all",
        help=(
            "collect: 수집/청킹/스코어링, export: LLM 입력 생성, llm: 기간 요약 LLM 실행, "
            "analysis: News Agent handoff 생성, sy: News SY 검증, all: 전체 실행"
        ),
    )
    parser.add_argument("--collect-date", required=True, help="Search date in YYYY-MM-DD")
    parser.add_argument("--company-id", required=True, help="Company id / DART corp code")
    parser.add_argument("--company-name", required=True, help="Company name")
    parser.add_argument("--query", default=None, help="Override Google News query")
    parser.add_argument(
        "--collection-days",
        type=int,
        default=None,
        help="Number of days to collect (collect_date 포함). 기본값은 config.news.collection_days",
    )
    parser.add_argument(
        "--lookback-days",
        type=int,
        default=None,
        help="Deprecated alias. collection_days = lookback_days + 1",
    )
    parser.add_argument("--max-results", type=int, default=None, help="Max collected news articles per day")
    parser.add_argument(
        "--event-top-k",
        "--total-max-results",
        dest="total_max_results",
        type=int,
        default=None,
        help=(
            "Max same-day-deduplicated news events retained after article reranking. "
            "--total-max-results is a deprecated compatibility alias."
        ),
    )
    parser.add_argument("--config", default=None, help="Path to workflow config YAML")
    parser.add_argument("--granularity", choices=["day", "month"], default="month", help="Export period granularity")
    parser.add_argument("--period-count", type=int, default=12, help="Number of periods for LLM summary input")
    parser.add_argument("--raw-period-count", type=int, default=3, help="Recent periods kept as raw input")
    parser.add_argument("--min-mention-count", type=int, default=3, help="Minimum mention_count for export")
    parser.add_argument("--llm-model", default="gpt-5.4-mini", help="LLM model for summary execution")
    parser.add_argument("--context-export-dir", default=None, help="Context export root. Defaults to Output_total/News/{run_key}/context_exports.")
    parser.add_argument("--ticker", default=None, help="Ticker for News Agent target_entity.")
    parser.add_argument("--corp-code", default=None, help="DART corp code for News Agent target_entity.")
    parser.add_argument("--as-of-date", default=None, help="News Agent as-of date. Defaults to --collect-date.")
    parser.add_argument("--dart-lightweight", default=None, help="DART lightweight JSON for News Agent cross-domain context.")
    parser.add_argument("--market-summary", default=None, help="YFinance market summary JSON for News Agent cross-domain context.")
    parser.add_argument(
        "--primary-data-only",
        action="store_true",
        help="Use News evidence only and omit DART/market secondary context.",
    )
    parser.add_argument("--analysis-output-dir", default=None, help="News Agent output dir. Defaults to Output_total/News/{run_key}/output.")
    parser.add_argument("--analysis-model", default=None, help="News Agent LLM model. Defaults to NEWS_AGENT_LLM_MODEL or gpt-5.4-mini.")
    parser.add_argument("--max-raw-events-per-period", type=int, default=40, help="Raw events per recent period for News Agent.")
    parser.add_argument("--sy-input", default=None, help="News SY input handoff path. Defaults to <analysis-output-dir>/news_agent_handoff.json.")
    parser.add_argument("--sy-output-dir", default=None, help="News SY output dir. Defaults to <analysis-output-dir>/sy_agent.")
    parser.add_argument("--sy-model", default=None, help="News SY LLM model. Defaults to NEWS_SY_AGENT_LLM_MODEL or gpt-5.4-mini.")
    parser.add_argument("--news-claim-limit", type=int, default=10, help="Max news_only claims for News SY Agent.")
    parser.add_argument("--timeout-seconds", type=float, default=300.0, help="OpenAI request timeout for analysis and SY phases.")
    parser.add_argument(
        "--split-by-period",
        action="store_true",
        help="Run LLM once per period and merge the results into llm_period_summaries.json",
    )
    parser.add_argument("--api-key-env", default="OPENAI_API_KEY", help="Environment variable containing OpenAI API key")
    parser.add_argument("--env-path", default=None, help="Optional .env path")
    return parser


def _artifact_dirname(company_name: str, collect_date: date) -> str:
    safe_name = company_name.strip()
    safe_name = "".join("_" if ch in '\\/:*?"<>|' else ch for ch in safe_name)
    safe_name = "_".join(part for part in safe_name.split() if part).strip("._")
    return f"{safe_name or 'company'}_{collect_date.strftime('%Y%m%d')}"


def _context_export_dir(args: argparse.Namespace, project_root: Path, collect_date: date) -> Path:
    if args.context_export_dir:
        path = Path(args.context_export_dir).expanduser()
        return path if path.is_absolute() else project_root / path
    return project_root / "Output_total" / "News" / _artifact_dirname(args.company_name, collect_date) / "context_exports"


def _analysis_output_dir(args: argparse.Namespace, project_root: Path, collect_date: date) -> Path:
    if args.analysis_output_dir:
        path = Path(args.analysis_output_dir).expanduser()
        return path if path.is_absolute() else project_root / path
    context_dir = _context_export_dir(args, project_root, collect_date)
    return (context_dir.parent if context_dir.name == "context_exports" else context_dir) / "output"


def _selected_phases(phase: str) -> list[str]:
    return list(PIPELINE_PHASES) if phase == "all" else [phase]


def _run_export_phase(args: argparse.Namespace, project_root: Path, collect_date: date) -> dict[str, str]:
    config_path = Path(args.config) if args.config else project_root / "configs" / "news_default.yaml"
    workflow = NewsWorkflow.from_config_path(project_root, config_path)
    report_context_path = workflow.layout.report_context_path(collect_date, args.company_name)
    context_export_dir = _context_export_dir(args, project_root, collect_date) / args.granularity
    return build_context_exports(
        report_context_path=report_context_path,
        output_dir=context_export_dir,
        granularity=args.granularity,
        period_count=args.period_count,
        raw_period_count=args.raw_period_count,
        min_mention_count=args.min_mention_count,
        llm_model=args.llm_model,
        run_llm=False,
        split_by_period=args.split_by_period,
        api_key_env=args.api_key_env,
        env_path=args.env_path,
    )


def _run_llm_phase(args: argparse.Namespace, project_root: Path, collect_date: date) -> str:
    context_export_dir = _context_export_dir(args, project_root, collect_date)
    llm_request_path = context_export_dir / args.granularity / "llm_summary_request.json"
    return execute_llm_summary_request(
        llm_request_path=llm_request_path,
        api_key_env=args.api_key_env,
        env_path=args.env_path,
        split_by_period=args.split_by_period,
    )


def _run_analysis_phase(args: argparse.Namespace, project_root: Path, collect_date: date) -> analysis_agent.AnalysisPaths:
    context_export_dir = _context_export_dir(args, project_root, collect_date)
    output_dir = _analysis_output_dir(args, project_root, collect_date)
    return analysis_agent.run_analysis_agent(
        context_export_dir=context_export_dir,
        granularity=args.granularity,
        company_name=args.company_name,
        ticker=args.ticker,
        corp_code=args.corp_code,
        as_of_date=args.as_of_date or collect_date.isoformat(),
        dart_lightweight=args.dart_lightweight,
        market_summary=args.market_summary,
        output_dir=str(output_dir),
        model=args.analysis_model,
        env_path=args.env_path,
        timeout_seconds=args.timeout_seconds,
        max_raw_events_per_period=args.max_raw_events_per_period,
        include_secondary_context=not args.primary_data_only,
        show_progress=True,
    )


def _load_news_sy_module(project_root: Path) -> Any:
    module_path = project_root / "src" / "Agent_Team" / "News_Agent" / "SY_Agent" / "sy_agent.py"
    spec = importlib.util.spec_from_file_location("news_sy_agent_runtime", module_path)
    if spec is None or spec.loader is None:
        raise ImportError(f"Unable to load News SY Agent module: {module_path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _run_sy_phase(args: argparse.Namespace, project_root: Path, collect_date: date) -> dict[str, Any]:
    sy_module = _load_news_sy_module(project_root)
    if hasattr(sy_module, "_load_env_file"):
        sy_module._load_env_file(project_root / ".env")
        if args.env_path:
            sy_module._load_env_file(Path(args.env_path).expanduser())

    analysis_output_dir = _analysis_output_dir(args, project_root, collect_date)
    handoff_path = Path(args.sy_input).expanduser() if args.sy_input else analysis_output_dir / "news_agent_handoff.json"
    if not handoff_path.is_absolute():
        handoff_path = project_root / handoff_path
    output_dir = Path(args.sy_output_dir).expanduser() if args.sy_output_dir else analysis_output_dir / "sy_agent"
    if not output_dir.is_absolute():
        output_dir = project_root / output_dir

    paths = sy_module.OutputPaths(
        output_dir=output_dir,
        verified_output=output_dir / "sy_claim_validations.json",
        verified_report=output_dir / "news_agent_verified_handoff.json",
        audit_trace=output_dir / "sy_audit_trace.json",
    )
    model = args.sy_model or sy_module.os.getenv("NEWS_SY_AGENT_LLM_MODEL") or sy_module.os.getenv("NEWS_AGENT_LLM_MODEL") or sy_module.DEFAULT_MODEL
    return sy_module.run_sy_agent(
        handoff_path=handoff_path.resolve(),
        paths=paths,
        model=model,
        claim_limit=args.news_claim_limit,
        timeout_seconds=args.timeout_seconds,
        show_progress=True,
    )


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()

    collection_days = args.collection_days
    if collection_days is None and args.lookback_days is not None:
        collection_days = int(args.lookback_days) + 1

    collect_date = date.fromisoformat(args.collect_date)
    project_root = _project_root()
    config_path = Path(args.config) if args.config else project_root / "configs" / "news_default.yaml"
    phases = _selected_phases(args.phase)

    with tqdm(total=len(phases), desc="News pipeline", unit="stage") as progress:
        for phase in phases:
            progress.set_description(f"News pipeline: {phase}")
            tqdm.write(f"[news:{phase}] start")
            if phase == "collect":
                workflow = NewsWorkflow.from_config_path(project_root, config_path)
                artifacts = workflow.run(
                    WorkflowRequest(
                        collect_date=collect_date,
                        company_id=args.company_id,
                        company_name=args.company_name,
                        query=args.query,
                        collection_days=collection_days,
                        max_results=args.max_results,
                        total_max_results=args.total_max_results,
                    )
                )
                tqdm.write(f"report_key={artifacts.report_key}")
                tqdm.write(f"dart_xml={artifacts.dart_xml_path}")
                tqdm.write(f"context_db={artifacts.context_db_path}")
                tqdm.write(f"raw_news_candidates={artifacts.raw_news_candidates_path}")
                tqdm.write(f"raw_news={artifacts.raw_news_path}")
                tqdm.write(f"article_ranking={artifacts.article_ranking_path}")
                tqdm.write(f"news_events={artifacts.news_events_path}")
                tqdm.write(f"all_news_events={artifacts.all_news_events_path}")
                tqdm.write(f"event_ranking={artifacts.event_ranking_path}")
                tqdm.write(f"report_context={artifacts.report_context_path}")
                tqdm.write(f"manifest={artifacts.manifest_path}")
            elif phase == "export":
                paths = _run_export_phase(args, project_root, collect_date)
                for key, value in paths.items():
                    tqdm.write(f"{key}={value}")
            elif phase == "llm":
                path = _run_llm_phase(args, project_root, collect_date)
                tqdm.write(f"llm_period_summaries_path={path}")
            elif phase == "analysis":
                paths = _run_analysis_phase(args, project_root, collect_date)
                tqdm.write(f"input_payload={paths.input_payload_path}")
                tqdm.write(f"llm_request={paths.llm_request_path}")
                tqdm.write(f"handoff={paths.handoff_path}")
                tqdm.write(f"evidence_map={paths.evidence_map_path}")
            elif phase == "sy":
                result = _run_sy_phase(args, project_root, collect_date)
                tqdm.write(json_summary(result.get("summary") or {}))
            else:
                raise ValueError(f"Unsupported phase: {phase}")
            tqdm.write(f"[news:{phase}] done")
            progress.update(1)


def json_summary(payload: dict[str, Any]) -> str:
    import json

    return json.dumps(payload, ensure_ascii=False, indent=2)


if __name__ == "__main__":
    main()
