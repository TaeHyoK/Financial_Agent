"""Build a portable ZIP containing the frozen inputs needed for final judging.

Kept to regenerate the 2026-09-20 handoff bundle. The final HTML reports are
also tracked in the repository under ``final_reports/``.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import zipfile
from datetime import datetime, timezone
from pathlib import Path


COMPANIES = ("현대건설", "두산", "BGF리테일", "아모레퍼시픽", "SK바이오팜")
CONDITIONS = ("full", "random_news", "no_subdata", "no_peer", "one_team")
REPLICATES = ("r01", "r02", "r03")
REFERENCE_PDFS = {
    "현대건설": "20251020_company_현대건설.pdf",
    "두산": "20251111_company_두산.pdf",
    "BGF리테일": "20251107_company_bgf리테일.pdf",
    "아모레퍼시픽": "20251107_company_아모레퍼시픽.pdf",
    "SK바이오팜": "20251106_company_sk바이오팜.pdf",
}


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def copy_required(source: Path, target: Path) -> None:
    if not source.is_file():
        raise FileNotFoundError(source)
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, target)


def report_source(root: Path, replicate: str, condition: str, company: str) -> Path:
    if replicate == "r01":
        if condition == "one_team":
            return root / "reports/one_team/one_team_gpt54_r01" / company / f"report_{company}.html"
        return root / "reports" / condition / "replicate_01" / company / f"report_{company}.html"
    return root / "reports/repeated_standard_5companies" / f"replicate_{replicate[-2:]}" / condition / company / f"report_{company}.html"


def generated_text_source(root: Path, replicate: str, condition: str, company: str) -> tuple[Path, Path]:
    if replicate == "r01":
        if condition == "one_team":
            relative = Path("evaluation/with_one_team_gpt54/texts/generated/one_team") / f"{company}.txt"
        else:
            relative = Path("evaluation/texts/generated") / condition / f"{company}.txt"
    else:
        relative = Path("evaluation/repeated_standard_5companies/texts/generated") / replicate / condition / f"{company}.txt"
    return root / relative, relative


def build(source_root: Path, output_dir: Path, zip_path: Path) -> dict:
    if output_dir.exists() or zip_path.exists():
        raise FileExistsError("Output directory or ZIP already exists; choose a fresh destination")
    output_dir.mkdir(parents=True)

    for replicate in REPLICATES:
        for condition in CONDITIONS:
            for company in COMPANIES:
                copy_required(
                    report_source(source_root, replicate, condition, company),
                    output_dir / "final_reports" / replicate / condition / f"{company}.html",
                )
                source, relative = generated_text_source(source_root, replicate, condition, company)
                copy_required(source, output_dir / relative)

    for company in COMPANIES:
        copy_required(
            source_root / "references" / REFERENCE_PDFS[company],
            output_dir / "references" / REFERENCE_PDFS[company],
        )
        relative = Path("evaluation/texts/reference") / f"{company}.txt"
        copy_required(source_root / relative, output_dir / relative)

    judge_root = source_root / "evaluation/final_report_llm_judge"
    for name in ("manifest.json", "status.json", "requests_PREPARED_NOT_SUBMITTED.jsonl", "task_audit.jsonl"):
        copy_required(judge_root / name, output_dir / "evaluation/final_report_llm_judge" / name)

    readme = """# Financial Agent Ablation report bundle

최종 HTML 75개는 저장소의 `final_reports/`에서 추적한다. 이 ZIP은 그 밖의
입력인 reference PDF, 추출 본문, Judge 요청을 담는다.

- `final_reports/`: 5개 기업 × 3회 × 5조건 = 최종 HTML 75개(저장소와 같은 사본)
- `references/`: LLM Judge 대상 5개 기업의 실제 애널리스트 PDF
- `evaluation/`: Judge가 사용하는 동일 추출 본문과 API 미제출 요청 360개

## 새 노트북 복원

1. Git 저장소의 `main` 브랜치를 clone한다.
2. 이 ZIP의 **내용물**을 clone한 저장소 루트에 푼다.
3. `python run_config/final_report_llm_judge.py validate`를 실행한다.
4. `paid_api_calls: 0`, `requests: 360`을 확인한다.

평가 실행 전 `docs/ABLATION_HANDOFF_20260920.md`와
`run_config/FINAL_REPORT_LLM_JUDGE.md`를 먼저 읽는다.
"""
    (output_dir / "BUNDLE_README.md").write_text(readme, encoding="utf-8")

    files = sorted(path for path in output_dir.rglob("*") if path.is_file())
    rows = [{
        "path": str(path.relative_to(output_dir)),
        "bytes": path.stat().st_size,
        "sha256": sha256(path),
    } for path in files]
    counts = {
        "final_report_html": sum(row["path"].startswith("final_reports/") for row in rows),
        "analyst_reference_pdf": sum(row["path"].startswith("references/") for row in rows),
        "generated_judge_text": sum("/texts/generated/" in row["path"] for row in rows),
        "reference_judge_text": sum("/texts/reference/" in row["path"] for row in rows),
        "prepared_judge_requests": sum(1 for _ in (output_dir / "evaluation/final_report_llm_judge/requests_PREPARED_NOT_SUBMITTED.jsonl").open(encoding="utf-8")),
    }
    expected = {
        "final_report_html": 75,
        "analyst_reference_pdf": 5,
        "generated_judge_text": 75,
        "reference_judge_text": 5,
        "prepared_judge_requests": 360,
    }
    if counts != expected:
        raise ValueError(f"Bundle count mismatch: {counts} != {expected}")
    manifest = {
        "created_at": datetime.now(timezone.utc).isoformat(),
        "source_root": str(source_root.resolve()),
        "counts": counts,
        "files": rows,
    }
    (output_dir / "bundle_manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    (output_dir / "SHA256SUMS.txt").write_text(
        "".join(f"{row['sha256']}  {row['path']}\n" for row in rows), encoding="utf-8"
    )

    with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
        for path in sorted(item for item in output_dir.rglob("*") if item.is_file()):
            archive.write(path, path.relative_to(output_dir))
    with zipfile.ZipFile(zip_path) as archive:
        bad_file = archive.testzip()
        if bad_file is not None:
            raise RuntimeError(f"ZIP integrity check failed: {bad_file}")
    manifest["zip"] = {"path": str(zip_path.resolve()), "bytes": zip_path.stat().st_size, "sha256": sha256(zip_path)}
    return manifest


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-root", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--zip-path", type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(build(args.source_root, args.output_dir, args.zip_path), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
