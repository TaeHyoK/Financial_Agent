#!/usr/bin/env python3
import argparse
import json
import subprocess
import sys
from pathlib import Path

try:
    from .. import DEFAULT_ENV_FILE, DEFAULT_OUTPUT_ROOT, AGENT_DIR as FINANCIAL_AGENT_DIR, resolve_agent_pipeline_output_dir
except ImportError:  # pragma: no cover - supports direct script execution
    FINANCIAL_AGENT_DIR = Path(__file__).resolve().parents[1]
    PROJECT_ROOT = FINANCIAL_AGENT_DIR.parents[2]
    DEFAULT_ENV_FILE = PROJECT_ROOT / "configs" / ".env"
    DEFAULT_OUTPUT_ROOT = PROJECT_ROOT / "Output_total" / "Financial"

    def resolve_agent_pipeline_output_dir(
        company_name: str | None,
        selected_date: str,
        company_code: str | None = None,
        output_root: str | Path = DEFAULT_OUTPUT_ROOT,
    ) -> Path:
        company = str(company_name or company_code or "financial").strip() or "financial"
        date_label = "".join(character for character in str(selected_date) if character.isdigit())
        if len(date_label) != 8:
            raise ValueError("selected_date must be YYYYMMDD or YYYY-MM-DD.")
        company = company.replace("/", "_").replace("\\", "_")
        return Path(output_root).expanduser().resolve() / f"{company}_{date_label}" / "agent_pipeline"

DEFAULT_FA_SCRIPT = FINANCIAL_AGENT_DIR / "langgraph_flow.py"


def run_command(cmd):
    completed = subprocess.run(cmd, check=True, text=True, capture_output=True)
    if completed.stdout.strip():
        print(completed.stdout.strip())
    if completed.stderr.strip():
        print(completed.stderr.strip(), file=sys.stderr)


def load_financial_manifest(manifest_path: str | Path) -> dict:
    return json.loads(Path(manifest_path).expanduser().resolve().read_text(encoding="utf-8"))


def resolve_dart_master_path(dart_main_path: str | Path | None) -> Path | None:
    if not dart_main_path:
        return None
    candidate = Path(dart_main_path).expanduser().resolve().with_name("dart_master.json")
    return candidate if candidate.exists() else None


def resolve_pipeline_output_dir(
    *,
    financial_manifest: str | Path,
    output_dir: str | None = None,
    output_root: str | Path = DEFAULT_OUTPUT_ROOT,
    run_key: str | None = None,
) -> Path:
    if output_dir:
        return Path(output_dir).expanduser().resolve()
    if run_key:
        return (Path(output_root).expanduser().resolve() / run_key / "agent_pipeline").resolve()

    manifest = load_financial_manifest(financial_manifest)
    target = manifest.get("target_entity", {})
    source_notes = manifest.get("source_notes", {})
    selected_date = target.get("as_of_date") or source_notes.get("selected_date")
    if not selected_date:
        raise ValueError(
            "financial manifest must include target_entity.as_of_date or source_notes.selected_date "
            "when --output-dir is omitted."
        )
    return resolve_agent_pipeline_output_dir(
        company_name=target.get("company_name"),
        selected_date=selected_date,
        company_code=target.get("corp_code"),
        output_root=output_root,
    ).resolve()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--financial-manifest", required=True)
    parser.add_argument(
        "--output-dir",
        default=None,
        help="Exact agent_pipeline output directory. Defaults to Output_total/Financial/<company>_<date>/agent_pipeline.",
    )
    parser.add_argument(
        "--output-root",
        default=str(DEFAULT_OUTPUT_ROOT),
        help="Financial output root used when --output-dir is omitted.",
    )
    parser.add_argument(
        "--run-key",
        default=None,
        help="Optional explicit <company>_<YYYYMMDD> folder name under --output-root.",
    )
    parser.add_argument("--env-file", default=str(DEFAULT_ENV_FILE))
    parser.add_argument("--use-llm", action="store_true")
    parser.add_argument("--llm-provider", default="openai", choices=["auto", "openai"])
    parser.add_argument("--llm-model", default="auto")
    parser.add_argument("--llm-timeout", type=int, default=300)
    args = parser.parse_args()

    output_dir = resolve_pipeline_output_dir(
        financial_manifest=args.financial_manifest,
        output_dir=args.output_dir,
        output_root=args.output_root,
        run_key=args.run_key,
    )
    output_dir.mkdir(parents=True, exist_ok=True)
    financial_manifest = load_financial_manifest(args.financial_manifest)
    input_paths = financial_manifest.get("input_paths", {})
    dart_main_path = input_paths.get("dart_main")
    dart_master_path = input_paths.get("dart_master") or resolve_dart_master_path(dart_main_path)

    fa_output = output_dir / "pipeline_financial_analyst_report_output.json"
    fa_trace = output_dir / "pipeline_financial_analyst_report_trace.json"
    sy_output = output_dir / "pipeline_sy_validation_output.json"
    sy_trace = output_dir / "pipeline_sy_validation_trace.json"
    verified_report_output = output_dir / "pipeline_verified_financial_report_output.json"

    fa_cmd = [
        sys.executable,
        str(DEFAULT_FA_SCRIPT),
        "--manifest",
        args.financial_manifest,
        "--output",
        str(fa_output),
        "--trace-output",
        str(fa_trace),
        "--env-file",
        args.env_file,
        "--llm-provider",
        args.llm_provider,
        "--llm-model",
        args.llm_model,
        "--llm-timeout",
        str(args.llm_timeout),
    ]
    sy_cmd = [
        sys.executable,
        str(Path(__file__).resolve().parent / "langgraph_flow.py"),
        "--input",
        str(fa_output),
        "--output",
        str(sy_output),
        "--dart-main",
        str(dart_main_path or ""),
        "--dart-master",
        str(dart_master_path or ""),
        "--trace-output",
        str(sy_trace),
        "--verified-report-output",
        str(verified_report_output),
        "--env-file",
        args.env_file,
        "--llm-provider",
        args.llm_provider,
        "--llm-model",
        args.llm_model,
    ]
    if args.use_llm:
        fa_cmd.append("--use-llm")
        sy_cmd.append("--use-llm")

    run_command(fa_cmd)
    run_command(sy_cmd)

    manifest = {
        "financial_analyst_output": str(fa_output),
        "financial_analyst_trace": str(fa_trace),
        "sy_validation_output": str(sy_output),
        "sy_validation_trace": str(sy_trace),
        "verified_financial_report_output": str(verified_report_output),
    }
    manifest_path = output_dir / "pipeline_manifest.json"
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n")
    print(manifest_path)


if __name__ == "__main__":
    main()
