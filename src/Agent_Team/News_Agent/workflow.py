from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import date, datetime, timedelta, timezone
import os
from pathlib import Path
import re
from typing import Any

from .dart.collect import fetch_latest_periodic_xml, save_xml
from .io.storage import save_json
from .pipelines.build_corporate_context_db import build_context_db
from .pipelines.run_news_pipeline import run_daily_news
from .pipelines.utils import load_config


KST = timezone(timedelta(hours=9))


@dataclass(frozen=True)
class WorkflowRequest:
    collect_date: date
    company_id: str
    company_name: str
    query: str | None = None
    collection_days: int | None = None
    max_results: int | None = None
    total_max_results: int | None = None
    dedup_on_url: bool = True


@dataclass(frozen=True)
class PeriodicReportInfo:
    company_id: str
    company_name: str
    report_key: str
    report_name: str
    report_date: str
    receipt_no: str
    report_type: str
    xml_path: Path
    metadata_path: Path


@dataclass(frozen=True)
class WorkflowArtifacts:
    collect_date: str
    company_id: str
    company_name: str
    report_key: str
    dart_xml_path: str
    dart_metadata_path: str
    context_db_path: str
    raw_news_candidates_path: str
    raw_news_path: str
    article_ranking_path: str
    news_events_path: str
    all_news_events_path: str
    event_ranking_path: str
    report_context_path: str
    manifest_path: str


class EnvironmentLoader:
    def __init__(self, project_root: Path):
        self.project_root = project_root
        self.kca_root = project_root.parents[1] if len(project_root.parents) >= 2 else project_root.parent

    def load(self) -> None:
        self._load_env_file(self.kca_root / ".env")
        self._load_env_file(self.kca_root / "env" / ".env")
        self._load_env_file(self.project_root / ".env")

    @staticmethod
    def _load_env_file(path: Path) -> None:
        if not path.exists():
            return
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            key = key.strip()
            value = value.strip().strip('"').strip("'")
            if key and key not in os.environ:
                os.environ[key] = value


class ProjectLayout:
    def __init__(self, project_root: Path, config: dict[str, Any]):
        self.project_root = project_root
        self.data_root = self._resolve_path(config.get("data_root", "data/artifacts"))
        self.inputs_root = self._resolve_path(config.get("inputs_root", "data/inputs"))

    def _resolve_path(self, value: str | Path) -> Path:
        path = Path(value)
        return path if path.is_absolute() else self.project_root / path

    @staticmethod
    def date_token(value: date) -> str:
        return value.strftime("%Y%m%d")

    @staticmethod
    def request_dirname(company_name: str, collect_date: date) -> str:
        safe_name = re.sub(r'[\\/:*?"<>|]+', "_", company_name or "").strip()
        safe_name = re.sub(r"\s+", "_", safe_name).strip("._")
        return f"{safe_name or 'company'}_{ProjectLayout.date_token(collect_date)}"

    def dart_report_dir(self, company_id: str, report_key: str) -> Path:
        return self.inputs_root / "dart" / company_id / report_key

    def dart_report_xml_path(self, company_id: str, company_name: str, report_key: str) -> Path:
        return self.dart_report_dir(company_id, report_key) / f"{company_name}_latest_periodic.xml"

    def dart_report_metadata_path(self, company_id: str, report_key: str) -> Path:
        return self.dart_report_dir(company_id, report_key) / "report_metadata.json"

    def context_db_path(self, company_id: str, report_key: str) -> Path:
        return self.data_root / "db" / "corporate_context" / company_id / report_key / "corporate_context_db.jsonl"

    def raw_news_path(self, collect_date: date, company_name: str) -> Path:
        return self.data_root / "news" / "raw" / self.request_dirname(company_name, collect_date) / "raw_news.parquet"

    def raw_news_candidates_path(self, collect_date: date, company_name: str) -> Path:
        return self.data_root / "news" / "raw" / self.request_dirname(company_name, collect_date) / "raw_news_candidates.parquet"

    def article_ranking_path(self, collect_date: date, company_name: str) -> Path:
        return self.data_root / "news" / "raw" / self.request_dirname(company_name, collect_date) / "article_ranking.parquet"

    def news_events_path(self, collect_date: date, company_name: str) -> Path:
        return self.data_root / "news" / "events" / self.request_dirname(company_name, collect_date) / "news_events.parquet"

    def all_news_events_path(self, collect_date: date, company_name: str) -> Path:
        return self.data_root / "news" / "events" / self.request_dirname(company_name, collect_date) / "news_events_all.parquet"

    def event_ranking_path(self, collect_date: date, company_name: str) -> Path:
        return self.data_root / "news" / "events" / self.request_dirname(company_name, collect_date) / "event_ranking.parquet"

    def report_context_path(self, collect_date: date, company_name: str) -> Path:
        return self.data_root / "reports" / "packs" / self.request_dirname(company_name, collect_date) / "report_context.json"

    def manifest_path(self, collect_date: date, company_name: str) -> Path:
        return self.data_root / "runs" / self.request_dirname(company_name, collect_date) / "workflow_manifest.json"


class LatestPeriodicReportCollector:
    def __init__(self, config: dict[str, Any], layout: ProjectLayout):
        self.config = config
        self.layout = layout

    def fetch(self, request: WorkflowRequest) -> PeriodicReportInfo:
        dart_cfg = self.config.get("dart", {})
        api_key_env = str(dart_cfg.get("api_key_env", "DART_API_KEY"))
        api_key = os.getenv(api_key_env, "").strip()
        if not api_key:
            raise RuntimeError(f"Missing DART API key. Set environment variable {api_key_env}.")

        fetch_lookback_days = int(dart_cfg.get("fetch_lookback_days", 550))
        bgn_de = (request.collect_date - timedelta(days=fetch_lookback_days)).strftime("%Y%m%d")
        end_de = request.collect_date.strftime("%Y%m%d")

        result = fetch_latest_periodic_xml(
            api_key=api_key,
            corp_code=request.company_id,
            bgn_de=bgn_de,
            end_de=end_de,
        )
        report_key = self._build_report_key(result["report_dt"], result["rcept_no"])
        xml_path = self.layout.dart_report_xml_path(request.company_id, request.company_name, report_key)
        metadata_path = self.layout.dart_report_metadata_path(request.company_id, report_key)

        save_xml(result["xml_text"], str(xml_path))
        save_json(
            {
                "company_id": request.company_id,
                "company_name": request.company_name,
                "collect_date": request.collect_date.isoformat(),
                "report_key": report_key,
                "report_name": result["report_nm"],
                "report_date": result["report_dt"],
                "report_type": result.get("report_tp", ""),
                "receipt_no": result["rcept_no"],
                "xml_path": str(xml_path),
            },
            metadata_path,
        )
        return PeriodicReportInfo(
            company_id=request.company_id,
            company_name=request.company_name,
            report_key=report_key,
            report_name=result["report_nm"],
            report_date=result["report_dt"],
            report_type=result.get("report_tp", ""),
            receipt_no=result["rcept_no"],
            xml_path=xml_path,
            metadata_path=metadata_path,
        )

    @staticmethod
    def _build_report_key(report_date: str, receipt_no: str) -> str:
        date_token = re.sub(r"\D", "", report_date)
        receipt_token = re.sub(r"\D", "", receipt_no)
        if date_token and receipt_token:
            return f"{date_token}_{receipt_token}"
        if date_token:
            return date_token
        return receipt_token or "latest"


class CorporateContextDatabaseService:
    def __init__(self, config: dict[str, Any], layout: ProjectLayout):
        self.config = config
        self.layout = layout

    def build(self, request: WorkflowRequest, report: PeriodicReportInfo) -> Path:
        build_context_db(
            config=self.config,
            company_id=request.company_id,
            company_name=request.company_name,
            report_key=report.report_key,
            report_date=report.report_date,
            report_path=str(report.xml_path),
        )
        context_db_path = self.layout.context_db_path(request.company_id, report.report_key)
        if not context_db_path.exists():
            raise FileNotFoundError(f"Context DB was not created: {context_db_path}")
        return context_db_path


class DailyNewsPipelineService:
    def __init__(self, config: dict[str, Any], layout: ProjectLayout):
        self.config = config
        self.layout = layout

    def run(self, request: WorkflowRequest, report_key: str) -> dict[str, Any]:
        result = run_daily_news(
            config=self.config,
            collect_date=request.collect_date,
            company_id=request.company_id,
            company_name=request.company_name,
            report_key=report_key,
            query_override=request.query,
            collection_days_override=request.collection_days,
            max_results_override=request.max_results,
            total_max_results_override=request.total_max_results,
            dedup_on_url_override=request.dedup_on_url,
        )
        report_context_path = self.layout.report_context_path(request.collect_date, request.company_name)
        if not report_context_path.exists():
            raise RuntimeError(
                "No report_context.json was produced. "
                "The news search may have returned zero usable articles for the requested date."
            )
        return result


class NewsWorkflow:
    def __init__(self, project_root: Path, config: dict[str, Any]):
        self.project_root = project_root
        self.config = self._normalize_config(config)
        self.layout = ProjectLayout(project_root, self.config)
        self.environment = EnvironmentLoader(project_root)
        self.dart = LatestPeriodicReportCollector(self.config, self.layout)
        self.context_db = CorporateContextDatabaseService(self.config, self.layout)
        self.news = DailyNewsPipelineService(self.config, self.layout)

    @classmethod
    def from_config_path(cls, project_root: Path, config_path: str | Path) -> "NewsWorkflow":
        return cls(project_root, load_config(config_path))

    def _normalize_config(self, config: dict[str, Any]) -> dict[str, Any]:
        normalized = dict(config)
        data_root = normalized.get("data_root", "data/artifacts")
        inputs_root = normalized.get("inputs_root", "data/inputs")
        normalized["data_root"] = str(self._resolve_path(data_root))
        normalized["inputs_root"] = str(self._resolve_path(inputs_root))
        return normalized

    def _resolve_path(self, value: str | Path) -> Path:
        path = Path(value)
        return path if path.is_absolute() else self.project_root / path

    def run(self, request: WorkflowRequest) -> WorkflowArtifacts:
        self.environment.load()
        report = self.dart.fetch(request)
        context_db_path = self.context_db.build(request, report)
        news_result = self.news.run(request, report.report_key)
        report_context_path = self.layout.report_context_path(request.collect_date, request.company_name)
        manifest_path = self.layout.manifest_path(request.collect_date, request.company_name)

        artifacts = WorkflowArtifacts(
            collect_date=request.collect_date.isoformat(),
            company_id=request.company_id,
            company_name=request.company_name,
            report_key=report.report_key,
            dart_xml_path=str(report.xml_path),
            dart_metadata_path=str(report.metadata_path),
            context_db_path=str(context_db_path),
            raw_news_candidates_path=str(
                news_result.get("raw_news_candidates_path")
                or self.layout.raw_news_candidates_path(request.collect_date, request.company_name)
            ),
            raw_news_path=str(news_result.get("raw_news_path") or self.layout.raw_news_path(request.collect_date, request.company_name)),
            article_ranking_path=str(
                news_result.get("article_ranking_path")
                or self.layout.article_ranking_path(request.collect_date, request.company_name)
            ),
            news_events_path=str(news_result.get("news_events_path") or self.layout.news_events_path(request.collect_date, request.company_name)),
            all_news_events_path=str(
                news_result.get("all_news_events_path")
                or self.layout.all_news_events_path(request.collect_date, request.company_name)
            ),
            event_ranking_path=str(
                news_result.get("event_ranking_path")
                or self.layout.event_ranking_path(request.collect_date, request.company_name)
            ),
            report_context_path=str(report_context_path),
            manifest_path=str(manifest_path),
        )
        save_json(
            {
                "run_metadata": {
                    "generated_at_kst": datetime.now(KST).isoformat(),
                    "project_root": str(self.project_root),
                },
                "request": {
                    "collect_date": request.collect_date.isoformat(),
                    "company_id": request.company_id,
                    "company_name": request.company_name,
                    "query": request.query or request.company_name,
                    "collection_days": request.collection_days,
                    "max_results_per_day": request.max_results,
                    "news_event_top_k": request.total_max_results,
                    # Deprecated name kept so older experiment readers still load.
                    "total_max_results": request.total_max_results,
                },
                "dart_report": {
                    "report_key": report.report_key,
                    "report_name": report.report_name,
                    "report_date": report.report_date,
                    "report_type": report.report_type,
                    "receipt_no": report.receipt_no,
                    "xml_path": str(report.xml_path),
                    "metadata_path": str(report.metadata_path),
                },
                "news_collection": {
                    "collected_unique_count": news_result.get("collected_unique_count"),
                    "raw_news_count_before_total_cap": news_result.get(
                        "raw_news_count_before_total_cap"
                    ),
                    "selected_source_article_count": news_result.get("raw_news_count"),
                    "news_event_count_before_top_k": news_result.get(
                        "news_event_count_before_top_k"
                    ),
                    "news_event_count": news_result.get("news_event_count"),
                    "event_top_k": news_result.get("event_top_k"),
                    "selection_stage": news_result.get("selection_stage"),
                    "selection_method": news_result.get("selection_method"),
                    "cross_date_clustering": news_result.get("cross_date_clustering"),
                    # Deprecated fields kept for backward-compatible manifests.
                    "raw_news_count": news_result.get("raw_news_count"),
                    "total_max_results": news_result.get("total_max_results"),
                },
                "artifacts": asdict(artifacts),
            },
            manifest_path,
        )
        return artifacts
