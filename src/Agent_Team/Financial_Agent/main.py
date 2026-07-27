"""CLI entrypoint for the deterministic DART financial statement collector."""

from __future__ import annotations

import argparse
import json
import logging
import os
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any

try:
    from . import (
        AGENT_DIR,
        DEFAULT_CONFIG_PATH,
        DEFAULT_ENV_FILE,
        DEFAULT_FINANCIAL_INDEX_PATH,
        DEFAULT_OUTPUT_ROOT,
        PROJECT_ROOT,
        build_run_key,
    )
    from .dart_client import DartClient
    from .financial_index_calculator import calculate_financial_index_files
    from .handoff_builder import build_master_canonical, build_trend_canonical
    from .models import Filing, PipelineInput, TargetReport
    from .normalizer import normalize_primary_report
    from .revenue_breakdown_extractor import extract_revenue_breakdown
    from .report_resolver import build_primary_target, load_pipeline_input, resolve_report_set
    from .section_extractor import extract_section_four
    from .share_information_extractor import extract_share_information
except ImportError:  # pragma: no cover - supports direct script execution
    from dart_client import DartClient
    from financial_index_calculator import calculate_financial_index_files
    from handoff_builder import build_master_canonical, build_trend_canonical
    from models import Filing, PipelineInput, TargetReport
    from normalizer import normalize_primary_report
    from revenue_breakdown_extractor import extract_revenue_breakdown
    from report_resolver import build_primary_target, load_pipeline_input, resolve_report_set
    from section_extractor import extract_section_four
    from share_information_extractor import extract_share_information
    AGENT_DIR = Path(__file__).resolve().parent
    PROJECT_ROOT = AGENT_DIR.parents[2]
    DEFAULT_CONFIG_PATH = PROJECT_ROOT / "configs" / "company_input.json"
    DEFAULT_ENV_FILE = PROJECT_ROOT / "configs" / ".env"
    DEFAULT_OUTPUT_ROOT = PROJECT_ROOT / "Output_total" / "Financial"
    DEFAULT_FINANCIAL_INDEX_PATH = AGENT_DIR / "financial_index.json"

    def build_run_key(company_name: str | None, selected_date: Any, company_code: str | None = None) -> str:
        selected_date_label = selected_date.strftime("%Y%m%d")
        company = str(company_name or company_code or "financial").strip() or "financial"
        return f"{company.replace('/', '_').replace(chr(92), '_')}_{selected_date_label}"


DEFAULT_INPUT_PATH = DEFAULT_CONFIG_PATH


def main() -> None:
    """Run DART collection and write statement and financial index outputs."""

    parser = argparse.ArgumentParser(description="Deterministic DART financial statement collector")
    parser.add_argument(
        "--input",
        default=str(DEFAULT_INPUT_PATH),
        help="Input JSON path. Defaults to configs/company_input.json.",
    )
    parser.add_argument(
        "--output-dir",
        default=None,
        help="Directory where DART output JSON files are written. Defaults to Output_total/Financial/<company>_<date>.",
    )
    parser.add_argument(
        "--financial-index",
        default=str(DEFAULT_FINANCIAL_INDEX_PATH),
        help="Financial index definition JSON path. Defaults to ./financial_index.json.",
    )
    parser.add_argument(
        "--skip-financial-index",
        action="store_true",
        help="Write only dart_master.json and dart_2y_handoff.json.",
    )
    parser.add_argument(
        "--env-file",
        default=str(DEFAULT_ENV_FILE),
        help="Environment file containing DART_API_KEY. Defaults to configs/.env.",
    )
    parser.add_argument("--log-level", default="INFO", help="Python logging level.")
    args = parser.parse_args()

    logging.basicConfig(level=getattr(logging, args.log_level.upper(), logging.INFO), format="%(levelname)s %(message)s")
    logger = logging.getLogger("dart_collector")

    input_path = Path(args.input).expanduser().resolve()
    _load_env_file(Path(args.env_file).expanduser().resolve())
    _load_env_file(input_path.parent / ".env")
    _load_env_file(PROJECT_ROOT / ".env")
    _load_env_file(AGENT_DIR / ".env")
    pipeline_input = load_pipeline_input(input_path)

    api_key = os.getenv("DART_API_KEY", "").strip()
    client = DartClient(api_key, max_retries=pipeline_input.max_retries, logger=logger)

    primary_target = build_primary_target(pipeline_input.selected_date)
    logger.info(
        "Resolved theoretical target: primary=%s %s",
        primary_target.period_type,
        primary_target.period_end.isoformat(),
    )

    resolved = resolve_report_set(
        client=client,
        company_code=pipeline_input.company_code,
        selected_date=pipeline_input.selected_date,
    )
    for role, (target, filing) in resolved.items():
        logger.info("%s filing: %s %s %s", role, filing.rcept_dt, filing.rcept_no, filing.report_nm)

    collected = _collect_parallel(client, resolved)
    matrix_master = _build_matrix_master(collected)
    master = build_trend_canonical(
        matrix_master,
        resolved,
        selected_date=pipeline_input.selected_date,
        theoretical_target=primary_target,
        annual_history_limit=3,
    )
    handoff = build_trend_canonical(
        matrix_master,
        resolved,
        selected_date=pipeline_input.selected_date,
        theoretical_target=primary_target,
        annual_history_limit=1,
    )
    revenue_breakdown = collected.get("primary", {}).get("revenue_breakdown") or {}
    share_information = collected.get("primary", {}).get("share_information") or {}
    master["revenue_breakdown"] = revenue_breakdown
    handoff["revenue_breakdown"] = revenue_breakdown
    master["share_information"] = share_information
    handoff["share_information"] = share_information

    output_dir = _resolve_output_dir(args.output_dir, pipeline_input)
    financial_index_path = Path(args.financial_index).expanduser().resolve()
    _write_outputs(
        output_dir=output_dir,
        master=master,
        handoff=handoff,
        financial_index_path=financial_index_path,
        calculate_financial_index=not args.skip_financial_index,
        logger=logger,
    )


def collect_report(client: DartClient, target: TargetReport, filing: Filing) -> dict[str, Any]:
    """Fetch, extract, and normalize one resolved report."""

    xml_text = client.fetch_document_xml(rcept_no=filing.rcept_no)
    raw = extract_section_four(xml_text)
    if target.is_periodic:
        normalized = normalize_primary_report(raw, target)
    else:
        normalized = json.loads(json.dumps(raw, ensure_ascii=False))
    revenue_breakdown = (
        extract_revenue_breakdown(xml_text, target=target, filing=filing)
        if target.role == "primary"
        else {}
    )
    share_information = (
        extract_share_information(xml_text, target=target, filing=filing)
        if target.role == "primary"
        else {}
    )
    return {
        "raw": raw,
        "normalized": normalized,
        "revenue_breakdown": revenue_breakdown,
        "share_information": share_information,
    }


def _collect_parallel(
    client: DartClient,
    resolved: dict[str, tuple[TargetReport, Filing]],
) -> dict[str, dict[str, Any]]:
    results: dict[str, dict[str, Any]] = {}
    with ThreadPoolExecutor(max_workers=2) as executor:
        futures = {
            executor.submit(collect_report, client, target, filing): role
            for role, (target, filing) in resolved.items()
        }
        for future in as_completed(futures):
            role = futures[future]
            results[role] = future.result()
    return results


def _build_master(collected: dict[str, dict[str, Any]], secondary_target: TargetReport) -> dict[str, Any]:
    """Build the four-year canonical master payload."""

    return build_master_canonical(_build_matrix_master(collected), secondary_target)


def _build_matrix_master(collected: dict[str, dict[str, Any]]) -> dict[str, Any]:
    """Build the internal matrix master used only as canonicalization input."""

    return {
        role: payload["normalized"]
        for role, payload in collected.items()
    }


def _write_outputs(
    *,
    output_dir: Path,
    master: dict[str, Any],
    handoff: dict[str, Any],
    financial_index_path: Path,
    calculate_financial_index: bool,
    logger: logging.Logger | None = None,
) -> dict[str, Path]:
    """Write DART output files and optionally calculate financial index files."""

    output_dir.mkdir(parents=True, exist_ok=True)
    master_path = output_dir / "dart_master.json"
    handoff_path = output_dir / "dart_2y_handoff.json"
    _dump_json(master_path, master)
    _dump_json(handoff_path, handoff)
    if logger:
        logger.info("Wrote %s", master_path)
        logger.info("Wrote %s", handoff_path)

    written = {
        "master": master_path,
        "handoff": handoff_path,
    }
    if calculate_financial_index:
        master_index_path, handoff_index_path = calculate_financial_index_files(
            master_path=master_path,
            handoff_path=handoff_path,
            index_path=financial_index_path,
            output_dir=output_dir,
        )
        written["master_financial_index"] = master_index_path
        written["handoff_financial_index"] = handoff_index_path
        if logger:
            logger.info("Wrote %s", master_index_path)
            logger.info("Wrote %s", handoff_index_path)
    return written


def _resolve_output_dir(output_dir: str | None, pipeline_input: PipelineInput) -> Path:
    if output_dir:
        return Path(output_dir).expanduser().resolve()
    run_key = _run_key(pipeline_input)
    return (DEFAULT_OUTPUT_ROOT / run_key).resolve()


def _run_key(pipeline_input: PipelineInput) -> str:
    return build_run_key(pipeline_input.company_name, pipeline_input.selected_date, pipeline_input.company_code)


def _load_env_file(path: Path) -> None:
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        key, value = stripped.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = value


def _dump_json(path: Path, payload: Any) -> None:
    with path.open("w", encoding="utf-8") as file:
        json.dump(payload, file, ensure_ascii=False, indent=2)
        file.write("\n")


if __name__ == "__main__":
    main()
