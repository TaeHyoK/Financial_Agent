"""LLM-based competitor report synthesis from News, DART, and YFinance outputs."""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any
from urllib import error, request

from . import DEFAULT_ENV_FILE, OUTPUT_ROOT


OUTPUT_VERSION = "2.0"
DEFAULT_OPENAI_MODEL = "gpt-5.4-mini"
INTERNAL_LLM_SOURCE_KEY = "_llm_source_reports"
SOURCE_SPECS = {
    "dart": ("Financial", "final_report.json"),
    "news": ("News", "final_report.json"),
    "yfinance": ("Y_Finance", "final_report.json"),
}
SOURCE_LABELS = {
    "dart": "DART",
    "news": "News",
    "yfinance": "YFinance",
}
REQUIRED_SOURCES = tuple(SOURCE_SPECS)


@dataclass(frozen=True)
class RunIdentity:
    """Company/date identity used to find existing output reports."""

    run_key: str
    company_name: str
    selected_date: str | None = None
    ticker: str | None = None
    corp_code: str | None = None
    stock_code: str | None = None


@dataclass(frozen=True)
class ReportPaths:
    """Paths written by the competitor agent."""

    json: Path
    markdown: Path
    run_key: str
    company_name: str


def generate_competitor_report(
    *,
    target: RunIdentity,
    competitors: list[RunIdentity],
    output_root: Path = OUTPUT_ROOT,
    output_json: Path | None = None,
    output_md: Path | None = None,
    max_items_per_section: int = 6,
    llm_provider: str = "openai",
    llm_model: str = "auto",
    llm_timeout: int = 90,
    env_file: Path | None = DEFAULT_ENV_FILE,
) -> list[ReportPaths]:
    """Call an LLM once per competitor and write separate company reports."""

    output_root = output_root.expanduser().resolve()

    if env_file:
        load_env_file(env_file)

    competitor_entries = [
        build_competitor_entry(identity=identity, output_root=output_root)
        for identity in _unique_competitors(competitors, target)
    ]
    if not competitor_entries:
        raise ValueError("No competitors to summarize after excluding the target company.")
    if (output_json or output_md) and len(competitor_entries) != 1:
        raise ValueError("--output-json/--output-md can only be used when exactly one competitor is selected.")

    written_paths: list[ReportPaths] = []
    for entry in competitor_entries:
        report = build_company_report(entry)
        apply_llm_synthesis(
            report,
            provider=llm_provider,
            model=llm_model,
            timeout=llm_timeout,
            max_items_per_section=max_items_per_section,
        )
        strip_internal_fields(report)

        report_json = output_json or output_root / "Competitor" / entry["run_key"] / "competitor_summary_report.json"
        report_md = output_md or output_root / "Competitor" / entry["run_key"] / "competitor_summary_report.md"
        write_json(report_json, report)
        report_md.parent.mkdir(parents=True, exist_ok=True)
        report_md.write_text(render_markdown_report(report), encoding="utf-8")
        written_paths.append(
            ReportPaths(
                json=report_json,
                markdown=report_md,
                run_key=entry["run_key"],
                company_name=entry["company_name"],
            )
        )

    return written_paths


def build_company_report(entry: dict[str, Any]) -> dict[str, Any]:
    """Build the single-company report shell before LLM synthesis."""

    return {
        "agent_name": "Competitor Agent",
        "role": "LLM-based single-company competitor report synthesizer",
        "output_version": OUTPUT_VERSION,
        "created_at": datetime.now().isoformat(timespec="seconds"),
        "input_policy": {
            "required_source_reports": list(REQUIRED_SOURCES),
            "source_semantics": {
                "dart": "Financial_Agent/DART final report",
                "news": "News_Agent final report",
                "yfinance": "YFinance_Agent final report",
            },
            "llm_policy": "The Competitor Agent always calls an LLM once per company using only that company's three source reports.",
            "investment_advice_policy": "No buy/sell/hold, target price, or personalized investment advice.",
        },
        "company": {
            "company_name": entry["company_name"],
            "run_key": entry["run_key"],
            "ticker": entry.get("ticker"),
            "corp_code": entry.get("corp_code"),
            "stock_code": entry.get("stock_code"),
            "as_of_date": entry.get("as_of_date"),
        },
        "source_reports": entry.get("source_reports") or {},
        INTERNAL_LLM_SOURCE_KEY: entry.get(INTERNAL_LLM_SOURCE_KEY) or {},
        "summary": "",
        "strengths": [],
        "risks": [],
        "data_gaps": entry.get("data_gaps") or [],
        "llm_synthesis": {
            "requested": True,
            "used": False,
            "provider": None,
            "model": None,
            "error": None,
        },
    }


def build_competitor_entry(*, identity: RunIdentity, output_root: Path) -> dict[str, Any]:
    """Read the three source reports and prepare one LLM input entry."""

    source_reports: dict[str, dict[str, Any]] = {}
    loaded_reports: dict[str, Any] = {}
    data_gaps: list[str] = []

    for source, path in source_report_paths(output_root, identity.run_key).items():
        report_meta = {"path": str(path), "available": False, "error": None}
        if not path.exists():
            report_meta["error"] = "missing_final_report"
            data_gaps.append(f"{SOURCE_LABELS[source]} final_report.json 없음")
        else:
            try:
                loaded_reports[source] = load_json(path)
                report_meta["available"] = True
            except (OSError, json.JSONDecodeError) as exc:
                report_meta["error"] = f"load_failed: {exc}"
                data_gaps.append(f"{SOURCE_LABELS[source]} final_report.json 로드 실패")
        source_reports[source] = report_meta

    inferred = infer_identity(identity, loaded_reports)
    return {
        "company_name": inferred.company_name,
        "run_key": inferred.run_key,
        "ticker": inferred.ticker,
        "corp_code": inferred.corp_code,
        "stock_code": inferred.stock_code,
        "as_of_date": infer_as_of_date(loaded_reports),
        "source_reports": source_reports,
        INTERNAL_LLM_SOURCE_KEY: build_llm_source_reports(loaded_reports),
        "summary": "",
        "strengths": [],
        "risks": [],
        "data_gaps": dedupe(data_gaps, 20),
    }


def discover_competitor_identities(
    *,
    output_root: Path = OUTPUT_ROOT,
    target: RunIdentity,
    selected_date: str | None = None,
    include_partial: bool = False,
) -> list[RunIdentity]:
    """Find competitor run_keys from existing output directories."""

    output_root = output_root.expanduser().resolve()
    suffix = normalize_date(selected_date or target.selected_date) if (selected_date or target.selected_date) else None
    run_keys: set[str] = set()
    for folder, _filename in SOURCE_SPECS.values():
        root = output_root / folder
        if not root.exists():
            continue
        for child in root.iterdir():
            if not child.is_dir():
                continue
            if suffix and not child.name.endswith(f"_{suffix}"):
                continue
            run_keys.add(child.name)

    identities: list[RunIdentity] = []
    for run_key in sorted(run_keys):
        company = company_from_run_key(run_key)
        if is_same_company_or_run(run_key, company, target):
            continue
        paths = source_report_paths(output_root, run_key)
        has_all = all(path.exists() for path in paths.values())
        if not include_partial and not has_all:
            continue
        identities.append(RunIdentity(run_key=run_key, company_name=company, selected_date=selected_date))
    return identities


def source_report_paths(output_root: Path, run_key: str) -> dict[str, Path]:
    """Return expected source report paths for one run_key."""

    return {
        source: output_root / folder / run_key / filename
        for source, (folder, filename) in SOURCE_SPECS.items()
    }


def load_identity_from_config(path: Path) -> RunIdentity:
    """Read a company config JSON and convert it to a RunIdentity."""

    payload = load_json(path.expanduser().resolve())
    selected_date = normalize_date(payload.get("selected_date"))
    company_name = str(payload.get("company_name") or payload.get("company_code") or "company").strip()
    ticker = str(payload.get("ticker") or "").strip() or None
    corp_code = str(payload.get("corp_code") or payload.get("company_code") or "").strip() or None
    stock_code = ticker.split(".", 1)[0] if ticker else None
    return RunIdentity(
        run_key=build_run_key(company_name, selected_date, corp_code),
        company_name=company_name,
        selected_date=selected_date,
        ticker=ticker,
        corp_code=corp_code,
        stock_code=stock_code,
    )


def identity_to_payload(identity: RunIdentity) -> dict[str, Any]:
    """Serialize identity for report output."""

    return {
        "run_key": identity.run_key,
        "company_name": identity.company_name,
        "selected_date": identity.selected_date,
        "ticker": identity.ticker,
        "corp_code": identity.corp_code,
        "stock_code": identity.stock_code,
    }


def infer_identity(base: RunIdentity, loaded_reports: dict[str, Any]) -> RunIdentity:
    """Fill missing identity fields from source reports."""

    news_entity = get_path(loaded_reports.get("news") or {}, ["output", "target_entity"]) or {}
    dart = loaded_reports.get("dart") or {}
    yfinance = loaded_reports.get("yfinance") or {}
    company_name = first_non_empty(
        base.company_name,
        dart.get("target_company"),
        yfinance.get("target_company"),
        news_entity.get("company_name") if isinstance(news_entity, dict) else None,
        company_from_run_key(base.run_key),
    )
    ticker = first_non_empty(
        base.ticker,
        dart.get("ticker"),
        yfinance.get("ticker"),
        news_entity.get("ticker") if isinstance(news_entity, dict) else None,
    )
    corp_code = first_non_empty(
        base.corp_code,
        dart.get("corp_code"),
        news_entity.get("corp_code") if isinstance(news_entity, dict) else None,
    )
    stock_code = first_non_empty(base.stock_code, ticker.split(".", 1)[0] if ticker else None)
    return RunIdentity(
        run_key=base.run_key,
        company_name=company_name,
        selected_date=base.selected_date,
        ticker=ticker,
        corp_code=corp_code,
        stock_code=stock_code,
    )


def infer_as_of_date(loaded_reports: dict[str, Any]) -> str | None:
    """Find the report date from any source report."""

    for source in ("dart", "yfinance", "news"):
        report = loaded_reports.get(source) or {}
        value = report.get("as_of_date")
        if value:
            return str(value)
        news_date = get_path(report, ["output", "target_entity", "as_of_date"])
        if news_date:
            return str(news_date)
    return None


def build_llm_source_reports(loaded_reports: dict[str, Any]) -> dict[str, Any]:
    """Prepare source final reports for the LLM prompt."""

    return {
        "news_final_report": compact_source_report("news", loaded_reports.get("news") or {}),
        "dart_financial_final_report": compact_source_report("dart", loaded_reports.get("dart") or {}),
        "yfinance_final_report": compact_source_report("yfinance", loaded_reports.get("yfinance") or {}),
    }


def compact_source_report(source: str, report: dict[str, Any]) -> dict[str, Any]:
    """Keep report content useful for synthesis while dropping trace/noise fields."""

    if not isinstance(report, dict):
        return {}
    if source == "news":
        output = report.get("output") if isinstance(report.get("output"), dict) else report
        return {
            "target_entity": get_path(report, ["output", "target_entity"]),
            "analysis_blocks": output.get("analysis_blocks") if isinstance(output, dict) else None,
            "description": report.get("description"),
            "model": report.get("model"),
        }
    if source == "dart":
        return {
            "target_company": report.get("target_company"),
            "ticker": report.get("ticker"),
            "corp_code": report.get("corp_code"),
            "as_of_date": report.get("as_of_date"),
            "main_view": report.get("main_view"),
            "financial_statement_view": report.get("financial_statement_view"),
            "detailed_analysis": report.get("detailed_analysis"),
            "cross_data_reconciliation": report.get("cross_data_reconciliation"),
        }
    if source == "yfinance":
        return {
            "target_company": report.get("target_company"),
            "ticker": report.get("ticker"),
            "as_of_date": report.get("as_of_date"),
            "main_view": report.get("main_view"),
            "time_horizon_view": report.get("time_horizon_view"),
            "detailed_analysis": report.get("detailed_analysis"),
            "cross_data_reconciliation": report.get("cross_data_reconciliation"),
        }
    return report


def apply_llm_synthesis(
    report: dict[str, Any],
    *,
    provider: str,
    model: str,
    timeout: int,
    max_items_per_section: int,
) -> None:
    """Call the configured LLM and merge the returned competitor report."""

    resolved_provider = resolve_llm_provider(provider)
    resolved_model = resolve_llm_model(resolved_provider, model)
    report["llm_synthesis"].update({"provider": resolved_provider, "model": resolved_model})
    if resolved_provider == "none":
        raise RuntimeError("Competitor Agent requires OPENAI_API_KEY.")

    prompt = build_llm_prompt(report, max_items_per_section=max_items_per_section)
    if resolved_provider != "openai":
        raise RuntimeError(f"Unsupported LLM provider: {resolved_provider}")
    result = call_openai(prompt, resolved_model, timeout)

    llm_payload = parse_llm_json(result["text"])
    merge_llm_payload(report, llm_payload, max_items_per_section=max_items_per_section)
    report["llm_synthesis"].update(
        {
            "used": True,
            "usage": result.get("usage") or {},
            "error": None,
        }
    )


def build_llm_prompt(report: dict[str, Any], *, max_items_per_section: int) -> str:
    """Build the LLM synthesis prompt using the three source reports."""

    compact = {
        "company": report.get("company"),
        "source_report_paths": report.get("source_reports"),
        "source_reports": report.get(INTERNAL_LLM_SOURCE_KEY),
        "data_gaps": report.get("data_gaps"),
    }
    schema = {
        "company_name": "string",
        "run_key": "string",
        "summary": "string",
        "strengths": ["string"],
        "risks": ["string"],
    }
    return (
        "당신은 Competitor Agent입니다. 입력으로 제공된 한 회사의 News, DART/Financial, "
        "YFinance final_report 3개만 읽고 이 회사의 요약보고서를 한국어 JSON으로 작성하세요.\n"
        "필수 조건:\n"
        f"- summary, strengths 최대 {max_items_per_section}개, risks 최대 {max_items_per_section}개를 작성합니다.\n"
        "- summary는 News, DART/Financial, YFinance 세 보고서를 모두 종합해야 합니다.\n"
        "- strengths는 회사의 강세/강점만 적고, risks는 약세/리스크만 적습니다.\n"
        "- 다른 회사나 target 회사와 비교하지 말고, 입력된 이 회사의 세 보고서에 있는 근거에서만 도출합니다.\n"
        "- 세 source가 서로 엇갈리면 summary 또는 risks에 괴리를 명시합니다.\n"
        "- 매수/매도/보유, 목표주가, 개인화 투자 조언은 쓰지 않습니다.\n"
        "- JSON 외의 설명을 붙이지 않습니다.\n\n"
        f"반환 JSON 스키마 예시:\n{json.dumps(schema, ensure_ascii=False, indent=2)}\n\n"
        f"입력 보고서:\n{json.dumps(compact, ensure_ascii=False, indent=2)}"
    )


def merge_llm_payload(
    report: dict[str, Any],
    payload: dict[str, Any],
    *,
    max_items_per_section: int,
) -> None:
    """Merge validated LLM fields into the report."""

    validate_llm_payload(payload)
    report["summary"] = clean_text(payload.get("summary"))
    report["strengths"] = dedupe(text_items(payload.get("strengths")), max_items_per_section)
    report["risks"] = dedupe(text_items(payload.get("risks")), max_items_per_section)


def validate_llm_payload(payload: dict[str, Any]) -> None:
    """Validate the required LLM report shape."""

    if not isinstance(payload, dict):
        raise ValueError("LLM payload must be an object.")
    if not clean_text(payload.get("summary")):
        raise ValueError("LLM payload missing summary.")
    if not text_items(payload.get("strengths")):
        raise ValueError("LLM payload missing strengths.")
    if not text_items(payload.get("risks")):
        raise ValueError("LLM payload missing risks.")


def render_markdown_report(report: dict[str, Any]) -> str:
    """Render a human-readable Markdown report."""

    company = report.get("company") if isinstance(report.get("company"), dict) else {}
    lines = [
        "# Competitor Summary Report",
        "",
        f"- Company: {company.get('company_name') or 'N/A'}",
        f"- Run key: {company.get('run_key') or 'N/A'}",
        f"- Ticker: {company.get('ticker') or 'N/A'}",
        f"- As of: {company.get('as_of_date') or 'N/A'}",
        f"- Created at: {report.get('created_at')}",
        f"- LLM: {get_path(report, ['llm_synthesis', 'provider']) or 'N/A'} / {get_path(report, ['llm_synthesis', 'model']) or 'N/A'}",
        "",
        "## Summary",
        "",
        clean_text(report.get("summary")) or "N/A",
        "",
        "## Strengths",
        "",
    ]
    strengths = text_items(report.get("strengths"))
    lines.extend(f"- {item}" for item in strengths) if strengths else lines.append("- N/A")
    lines.extend(["", "## Risks", ""])
    risks = text_items(report.get("risks"))
    lines.extend(f"- {item}" for item in risks) if risks else lines.append("- N/A")
    gaps = text_items(report.get("data_gaps"))
    if gaps:
        lines.extend(["", "## Data Gaps", ""])
        lines.extend(f"- {item}" for item in gaps)
    lines.append("")

    return "\n".join(lines).rstrip() + "\n"


def call_openai(prompt: str, model: str, timeout: int) -> dict[str, Any]:
    """Call OpenAI chat completions with urllib."""

    api_key = os.getenv("OPENAI_API_KEY")
    if not api_key:
        raise RuntimeError("OPENAI_API_KEY is not set")
    base_url = os.getenv("OPENAI_BASE_URL", "https://api.openai.com/v1").rstrip("/")
    payload = {
        "model": model,
        "messages": [
            {
                "role": "system",
                "content": "You are a Korean financial competitor-analysis agent. Return only valid JSON.",
            },
            {"role": "user", "content": prompt},
        ],
        "temperature": 0.2,
        "max_tokens": 2400,
        "response_format": {"type": "json_object"},
    }
    req = request.Request(
        f"{base_url}/chat/completions",
        data=json.dumps(payload).encode("utf-8"),
        headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
        method="POST",
    )
    try:
        with request.urlopen(req, timeout=timeout) as response:
            raw = response.read().decode("utf-8")
    except error.HTTPError as exc:
        body = exc.read().decode("utf-8", "ignore")
        raise RuntimeError(f"OpenAI HTTP {exc.code}: {body[:500]}") from exc
    result = json.loads(raw)
    choices = result.get("choices") or []
    if not choices:
        raise RuntimeError(f"OpenAI returned no choices: {result}")
    text = choices[0].get("message", {}).get("content", "").strip()
    if not text:
        raise RuntimeError("OpenAI returned empty text")
    return {"text": text, "usage": result.get("usage", {})}


def parse_llm_json(text: str) -> dict[str, Any]:
    """Parse JSON from a model response."""

    cleaned = text.strip()
    cleaned = re.sub(r"^```(?:json)?", "", cleaned).strip()
    cleaned = re.sub(r"```$", "", cleaned).strip()
    start = cleaned.find("{")
    end = cleaned.rfind("}")
    if start == -1 or end == -1 or end <= start:
        raise ValueError("LLM response did not contain a JSON object.")
    payload = json.loads(cleaned[start : end + 1])
    if not isinstance(payload, dict):
        raise ValueError("LLM response JSON is not an object.")
    return payload


def resolve_llm_provider(provider: str) -> str:
    """Resolve LLM provider. OpenAI is the only runtime provider."""

    if provider not in {"", "auto", "openai"}:
        raise RuntimeError(f"Unsupported LLM provider: {provider}. Only openai is supported.")
    if provider == "openai":
        return "openai"
    if os.getenv("OPENAI_API_KEY"):
        return "openai"
    return "none"


def resolve_llm_model(provider: str, model: str) -> str:
    """Resolve default model for provider."""

    if model and model != "auto":
        return model
    if provider == "openai":
        return os.getenv("OPENAI_MODEL", DEFAULT_OPENAI_MODEL)
    return model or DEFAULT_OPENAI_MODEL


def strip_internal_fields(report: dict[str, Any]) -> None:
    """Remove prompt-only source payloads before persisting the final report."""

    report.pop(INTERNAL_LLM_SOURCE_KEY, None)


def load_env_file(path: Path) -> None:
    """Load KEY=VALUE pairs without overriding exported environment variables."""

    if not path.exists():
        return
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        if key and key not in os.environ:
            os.environ[key] = strip_env_value(value.strip())


def strip_env_value(value: str) -> str:
    """Remove matching shell-style quotes from env values."""

    if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
        return value[1:-1]
    return value


def load_json(path: Path) -> Any:
    """Read JSON from path."""

    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, payload: Any) -> None:
    """Write formatted JSON."""

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def normalize_date(value: Any) -> str:
    """Return YYYYMMDD for supported date inputs."""

    digits = "".join(character for character in str(value or "") if character.isdigit())
    if len(digits) != 8:
        raise ValueError("date must be YYYYMMDD or YYYY-MM-DD.")
    return digits


def safe_label(value: str | None, fallback: str = "company") -> str:
    """Sanitize labels for run_key path fragments."""

    label = str(value or fallback).strip() or fallback
    for character in ('\\', '/', ':', '*', '?', '"', '<', '>', '|'):
        label = label.replace(character, "_")
    return "_".join(label.split())


def build_run_key(company_name: str | None, selected_date: Any, fallback: str | None = None) -> str:
    """Build the same run_key shape used by orchestration."""

    return f"{safe_label(company_name, fallback or 'company')}_{normalize_date(selected_date)}"


def company_from_run_key(run_key: str) -> str:
    """Infer company name from run_key."""

    match = re.match(r"^(?P<name>.+)_(?P<date>\d{8})$", run_key)
    if match:
        return match.group("name")
    return run_key


def _unique_competitors(competitors: list[RunIdentity], target: RunIdentity) -> list[RunIdentity]:
    """Dedupe competitors and remove the target entity."""

    seen: set[str] = set()
    unique: list[RunIdentity] = []
    for item in competitors:
        company = item.company_name or company_from_run_key(item.run_key)
        if is_same_company_or_run(item.run_key, company, target):
            continue
        if item.run_key in seen:
            continue
        seen.add(item.run_key)
        unique.append(item)
    return unique


def is_same_company_or_run(run_key: str, company_name: str | None, target: RunIdentity) -> bool:
    """Return True when a candidate points to the target company."""

    if run_key == target.run_key:
        return True
    if company_name and target.company_name and safe_label(company_name) == safe_label(target.company_name):
        return True
    return False


def get_path(payload: Any, path: list[str]) -> Any:
    """Get nested dict value by path."""

    current = payload
    for key in path:
        if not isinstance(current, dict):
            return None
        current = current.get(key)
    return current


def first_non_empty(*values: Any) -> str:
    """Return first non-empty value as string."""

    for value in values:
        text = clean_text(value)
        if text:
            return text
    return ""


def clean_text(value: Any) -> str:
    """Normalize arbitrary values into compact one-line text."""

    if value is None:
        return ""
    if isinstance(value, str):
        text = value
    elif isinstance(value, (int, float)):
        text = str(value)
    else:
        return ""
    return " ".join(text.split()).strip()


def ensure_list(value: Any) -> list[Any]:
    """Normalize scalar/list values to a list."""

    if value is None:
        return []
    if isinstance(value, list):
        return value
    return [value]


def text_items(value: Any) -> list[str]:
    """Extract readable text items from scalars and lists."""

    items: list[str] = []
    for item in ensure_list(value):
        if isinstance(item, str):
            text = clean_text(item)
        elif isinstance(item, dict):
            text = first_non_empty(item.get("summary"), item.get("point"), item.get("text"))
        else:
            text = clean_text(item)
        if text:
            items.append(text)
    return items


def dedupe(items: list[str], limit: int | None = None) -> list[str]:
    """Dedupe strings while preserving order."""

    seen: set[str] = set()
    output: list[str] = []
    for item in items:
        text = clean_text(item)
        if not text:
            continue
        key = text.casefold()
        if key in seen:
            continue
        seen.add(key)
        output.append(text)
        if limit is not None and len(output) >= limit:
            break
    return output
