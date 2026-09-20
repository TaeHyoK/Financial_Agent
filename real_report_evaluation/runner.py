"""CLI runner for FinRpt-style real-report evaluation."""

from __future__ import annotations

import argparse
import csv
import importlib.metadata
import json
import math
import platform
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from statistics import mean
from typing import Any

from transformers import AutoConfig, AutoTokenizer

from ablation_suite.recommendations import read_explicit_rating

from .discovery import ComparisonInput, discover_inputs
from .extract import (
    extract_generated_recommendations,
    extract_html_text,
    extract_pdf_text,
    extract_html_body,
    extract_pdf_body,
    BODY_POLICY_VERSION,
    body_policy_hash,
    extract_reference_recommendation,
)
from .metrics import (
    existing_position_agreement,
    explicit_rating_agreement,
    finrpt_accuracy,
    number_rate,
    rouge_l,
    tokenize_for_rouge,
)


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_MODEL = "BAAI/bge-m3"
DEFAULT_CONDITIONS = (
    "full",
    "no_peer",
    "no_subdata",
    "unified_domain_team",
    "random_news",
)


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    output_dir = Path(args.output_dir).expanduser().resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    comparisons = discover_inputs(
        real_report_dir=args.real_report_dir,
        full_suite=args.full_suite,
        ablation_suite=args.ablation_suite,
        selected_date=args.selected_date,
        replicates=_parse_replicates(args.replicates),
        conditions=_parse_conditions(args.conditions),
        company_keys=None if args.companies == 'all' else tuple(key.strip() for key in args.companies.split(',')),
    )

    rows, bert_inputs = _compute_base_metrics(
        comparisons,
        output_dir=output_dir,
        reference_max_pages=args.reference_max_pages,
        bert_model=args.bert_model,
        bert_max_tokens=args.bert_max_tokens,
        require_explicit_rating=args.selected_date == "report",
    )
    unresolved = [
        f"{row['condition']}/{row['company_name']}/r{row['replicate']:02d}"
        for row in rows
        if row["accuracy"] is None
    ]
    if unresolved:
        raise ValueError(
            "Recommendation extraction is unresolved; refusing to change the Accuracy "
            "denominator silently: " + ", ".join(unresolved)
        )
    if not args.skip_bertscore:
        _compute_bert_scores(
            rows,
            bert_inputs,
            model_type=args.bert_model,
            num_layers=args.bert_num_layers,
            batch_size=args.bert_batch_size,
            device=args.device,
        )

    summary = _aggregate(rows, args=args)
    _write_outputs(output_dir, rows, summary)
    print(_render_console(summary, output_dir))
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Compare six real analyst PDFs with Full HTML reports using a "
            "Korean adaptation of FinRpt Accuracy, ROUGE-L, BERTScore, and NumberRate."
        )
    )
    parser.add_argument(
        "--real-report-dir",
        default=str(ROOT / "real_report"),
        help="Directory containing one YYYYMMDD_company_*.pdf per company.",
    )
    parser.add_argument(
        "--full-suite",
        default=str(ROOT / "experiments" / "full_replicates_20251031"),
        help="Suite containing Full replicate 2 and 3; replicate 1 is auto-discovered.",
    )
    parser.add_argument(
        "--ablation-suite",
        default=str(ROOT / "experiments" / "ablation_20251031"),
        help="Suite containing the four ablation conditions.",
    )
    parser.add_argument("--selected-date", default="20251031",
                        help="YYYYMMDD, or 'report' to match company-specific reference dates without historical Full reuse.")
    parser.add_argument('--companies', default='all',
                        help='all, or explicit comma-separated company keys; missing requested reports remain errors.')
    parser.add_argument(
        "--replicates",
        default="1,2,3",
        help="Comma-separated Full replicate numbers.",
    )
    parser.add_argument(
        "--conditions",
        default=",".join(DEFAULT_CONDITIONS),
        help="Comma-separated conditions; defaults to Full and all four ablations.",
    )
    parser.add_argument(
        "--output-dir",
        default=str(ROOT / "evaluations" / "real_report_finrpt_all_conditions"),
    )
    parser.add_argument(
        "--reference-max-pages",
        type=int,
        default=0,
        help="Must be 0: reviewed narrative regions span all relevant pages.",
    )
    parser.add_argument("--bert-model", default=DEFAULT_MODEL)
    parser.add_argument(
        "--bert-num-layers",
        type=int,
        default=None,
        help="BERTScore layer; defaults to benchmark mapping or the model's final layer.",
    )
    parser.add_argument(
        "--bert-max-tokens",
        type=int,
        default=0,
        help="Full-text token ceiling including special tokens; 0 uses the tokenizer limit. Overflow fails without truncation.",
    )
    parser.add_argument("--bert-batch-size", type=int, default=1)
    parser.add_argument(
        "--device",
        default="auto",
        help="auto, cpu, cuda, or a concrete torch device such as cuda:0.",
    )
    parser.add_argument(
        "--skip-bertscore",
        action="store_true",
        help="Run extraction, Accuracy, ROUGE-L, and NumberRate only.",
    )
    return parser


def _parse_replicates(value: str) -> tuple[int, ...]:
    result = tuple(sorted({int(part.strip()) for part in value.split(",") if part.strip()}))
    if not result or any(item <= 0 for item in result):
        raise ValueError("--replicates must contain positive integers")
    return result


def _parse_conditions(value: str) -> tuple[str, ...]:
    requested = tuple(part.strip() for part in value.split(",") if part.strip())
    if not requested:
        raise ValueError("--conditions cannot be empty")
    if len(requested) != len(set(requested)):
        raise ValueError("--conditions contains duplicates")
    return requested


def _compute_base_metrics(
    comparisons: list[ComparisonInput],
    *,
    output_dir: Path,
    reference_max_pages: int,
    bert_model: str,
    bert_max_tokens: int,
    require_explicit_rating: bool = False,
) -> tuple[list[dict[str, Any]], list[tuple[str, str]]]:
    text_root = output_dir / "texts"
    if reference_max_pages != 0:
        raise ValueError("Body-only evaluation requires --reference-max-pages 0; use reviewed regions, not a page prefix")
    reference_cache: dict[Path, tuple[str, int, str]] = {}
    tokenizer = AutoTokenizer.from_pretrained(bert_model, use_fast=False)
    model_limit = int(tokenizer.model_max_length)
    if model_limit > 1000000 or model_limit <= 0:
        raise ValueError("Tokenizer has no reliable context limit; configure a verified model")
    token_limit = min(bert_max_tokens, model_limit) if bert_max_tokens > 0 else model_limit
    rows: list[dict[str, Any]] = []
    bert_inputs: list[tuple[str, str]] = []

    for item in comparisons:
        if item.reference_pdf not in reference_cache:
            body, page_count = extract_pdf_body(item.reference_pdf)
            raw, _ = extract_pdf_text(item.reference_pdf)
            reference_cache[item.reference_pdf] = (body, page_count, raw)
        reference_text, reference_page_count, reference_raw = reference_cache[item.reference_pdf]
        candidate_text = extract_html_body(item.candidate_html)
        reference_rec = extract_reference_recommendation(reference_raw)
        candidate_rec = extract_generated_recommendations(item.candidate_html)
        explicit = read_explicit_rating(item.candidate_html)
        # Report-date runs require the new contract; malformed/absent ratings
        # must not silently fall back to a guess from legacy wording.
        rating_mode = "explicit_rating" if explicit["present"] or require_explicit_rating else "legacy_new_entry"
        rouge = rouge_l(reference_text, candidate_text)
        number, reference_numbers, candidate_numbers = number_rate(
            reference_text, candidate_text
        )
        reference_ids = tokenizer.encode(reference_text, add_special_tokens=True, truncation=False)
        candidate_ids = tokenizer.encode(candidate_text, add_special_tokens=True, truncation=False)
        if max(len(reference_ids), len(candidate_ids)) > token_limit:
            raise ValueError(f"Incomplete BERTScore input: {item.company_key}/{item.condition}; "
                             f"full body exceeds {token_limit} tokens. No prefix score was computed.")
        # ROUGE, NumberRate and BERTScore receive the exact same narrative strings.
        bert_inputs.append((reference_text, candidate_text))

        reference_dump = text_root / "reference" / f"{item.company_key}.txt"
        candidate_dump = (
            text_root
            / "generated"
            / item.condition
            / f"replicate_{item.replicate:02d}"
            / f"{item.company_key}.txt"
        )
        _write_text_once(reference_dump, reference_text)
        _write_text_once(text_root / "reference_raw" / f"{item.company_key}.txt", reference_raw)
        _write_text_once(text_root / "generated_raw" / item.condition / f"replicate_{item.replicate:02d}" / f"{item.company_key}.txt",
                         extract_html_text(item.candidate_html))
        candidate_dump.parent.mkdir(parents=True, exist_ok=True)
        candidate_dump.write_text(candidate_text + "\n", encoding="utf-8")

        rows.append(
            {
                "condition": item.condition,
                "company_key": item.company_key,
                "company_name": item.company_name,
                "replicate": item.replicate,
                "selected_date": item.selected_date,
                "reference_date": item.reference_date,
                "date_gap_days": item.date_gap_days,
                "reference_pdf": str(item.reference_pdf),
                "candidate_html": str(item.candidate_html),
                "reference_page_count": reference_page_count,
                "body_policy": BODY_POLICY_VERSION,
                "reference_regions_sha256": body_policy_hash(),
                "bert_effective_token_limit": token_limit,
                "reference_rating": reference_rec.label,
                "reference_rating_evidence": reference_rec.matched_text,
                "candidate_rating": candidate_rec["rating"].label,
                "candidate_rating_evidence": candidate_rec["rating"].matched_text,
                "accuracy_mode": rating_mode,
                "candidate_new_entry": candidate_rec["new_entry"].label,
                "candidate_new_entry_evidence": candidate_rec["new_entry"].matched_text,
                "candidate_existing_position": candidate_rec["existing_position"].label,
                "candidate_existing_position_evidence": candidate_rec[
                    "existing_position"
                ].matched_text,
                "accuracy": (
                    explicit_rating_agreement(reference_rec.label, candidate_rec["rating"].label)
                    if rating_mode == "explicit_rating"
                    else finrpt_accuracy(reference_rec.label, candidate_rec["new_entry"].label)
                ),
                "rating_three_class_agreement": explicit_rating_agreement(
                    reference_rec.label, candidate_rec["rating"].label, binary=False,
                ),
                "existing_position_agreement": existing_position_agreement(
                    reference_rec.label, candidate_rec["existing_position"].label
                ),
                "rouge_l_precision": rouge.precision,
                "rouge_l_recall": rouge.recall,
                "rouge_l_f1": rouge.f1,
                "bertscore_precision": None,
                "bertscore_recall": None,
                "bertscore_f1": None,
                "number_rate": number,
                "reference_number_count": reference_numbers,
                "candidate_number_count": candidate_numbers,
                "reference_char_count": len(reference_text),
                "candidate_char_count": len(candidate_text),
                "reference_rouge_token_count": len(tokenize_for_rouge(reference_text)),
                "candidate_rouge_token_count": len(tokenize_for_rouge(candidate_text)),
                "reference_bert_token_count": len(reference_ids),
                "candidate_bert_token_count": len(candidate_ids),
                "reference_bert_truncated": False,
                "candidate_bert_truncated": False,
            }
        )
    return rows, bert_inputs


def _compute_bert_scores(
    rows: list[dict[str, Any]],
    inputs: list[tuple[str, str]],
    *,
    model_type: str,
    num_layers: int | None,
    batch_size: int,
    device: str,
) -> None:
    import torch
    from bert_score import score
    from bert_score.utils import model2layers, get_tokenizer, sent_encode

    # Check the scorer's actual encoding path, which otherwise truncates silently.
    scorer_tokenizer = get_tokenizer(model_type, use_fast=False)
    for reference, candidate in inputs:
        for text in (reference, candidate):
            expected = scorer_tokenizer.encode(text.strip(), add_special_tokens=True, truncation=False)
            if sent_encode(scorer_tokenizer, text) != expected:
                raise ValueError("BERTScore would truncate or alter full-body tokenization; evaluation incomplete")

    resolved_device = (
        "cuda" if device == "auto" and torch.cuda.is_available() else "cpu"
        if device == "auto"
        else device
    )
    resolved_layers = num_layers
    if resolved_layers is None:
        resolved_layers = model2layers.get(model_type)
    if resolved_layers is None:
        resolved_layers = int(AutoConfig.from_pretrained(model_type).num_hidden_layers)
    references = [item[0] for item in inputs]
    candidates = [item[1] for item in inputs]
    precision, recall, f1 = score(
        candidates,
        references,
        model_type=model_type,
        num_layers=resolved_layers,
        batch_size=batch_size,
        device=resolved_device,
        idf=False,
        rescale_with_baseline=False,
        verbose=True,
    )
    for row, p_value, r_value, f_value in zip(rows, precision, recall, f1):
        row["bertscore_precision"] = float(p_value.item())
        row["bertscore_recall"] = float(r_value.item())
        row["bertscore_f1"] = float(f_value.item())


def _aggregate(rows: list[dict[str, Any]], *, args: argparse.Namespace) -> dict[str, Any]:
    metric_keys = (
        "accuracy",
        "rating_three_class_agreement",
        "existing_position_agreement",
        "rouge_l_f1",
        "bertscore_f1",
        "number_rate",
    )
    by_condition_company: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        by_condition_company[(row["condition"], row["company_key"])].append(row)

    condition_company_rows: list[dict[str, Any]] = []
    for (condition, company_key), company_items in sorted(by_condition_company.items()):
        aggregate = {
            "condition": condition,
            "company_key": company_key,
            "company_name": company_items[0]["company_name"],
            "reference_date": company_items[0]["reference_date"],
            "date_gap_days": company_items[0]["date_gap_days"],
            "replicates": len(company_items),
            "reference_rating": company_items[0]["reference_rating"],
        }
        for key in metric_keys:
            aggregate[key] = _mean_present(item[key] for item in company_items)
        condition_company_rows.append(aggregate)

    condition_summary: list[dict[str, Any]] = []
    for condition in _parse_conditions(args.conditions):
        companies = [
            item for item in condition_company_rows if item["condition"] == condition
        ]
        aggregate = {
            "condition": condition,
            "comparison_count": sum(int(item["replicates"]) for item in companies),
            "company_count": len(companies),
        }
        for key in metric_keys:
            aggregate[key] = _mean_present(item[key] for item in companies)
        condition_summary.append(aggregate)

    overall = {
        key: _mean_present(condition[key] for condition in condition_summary)
        for key in metric_keys
    }
    return {
        "evaluation_id": Path(args.output_dir).expanduser().resolve().name,
        "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "status": "success",
        "comparison_count": len(rows),
        "company_count": len({row["company_key"] for row in rows}),
        "conditions": _parse_conditions(args.conditions),
        "replicates": sorted({row["replicate"] for row in rows}),
        "method": {
            "accuracy": "Buy/non-Buy opinion agreement; explicit HTML rating for annual reports; legacy new_entry mapping only for historical reports",
            "accuracy_modes": sorted({row["accuracy_mode"] for row in rows}),
            "rating_three_class_agreement": "Exact Buy/Hold/Sell agreement for explicit ratings only",
            "rouge_l": "ROUGE-L F1 over regex-tokenized Korean/Latin/numeric narrative body",
            "bertscore": "Full narrative body, no truncation, IDF or baseline rescaling",
            "body_policy": BODY_POLICY_VERSION,
            "reference_regions_sha256": body_policy_hash(),
            "number_rate": "min(candidate numeric-string count / reference count, 1)",
            "number_regex": r"\d+\.?\d*",
            "reference_pdf_pages": "reviewed narrative regions across all relevant pages",
            "bert_model": args.bert_model,
            "bert_num_layers": args.bert_num_layers or "auto",
            "bert_max_tokens": args.bert_max_tokens,
            "bert_batch_size": args.bert_batch_size,
            "bertscore_skipped": bool(args.skip_bertscore),
        },
        "versions": _versions(),
        "warnings": _warnings(rows),
        "overall": overall,
        "by_condition": condition_summary,
        "by_condition_company": condition_company_rows,
    }


def _mean_present(values: Any) -> float | None:
    present = [float(value) for value in values if value is not None and math.isfinite(value)]
    return mean(present) if present else None


def _warnings(rows: list[dict[str, Any]]) -> list[str]:
    warnings: list[str] = []
    gaps = sorted({int(row["date_gap_days"]) for row in rows})
    if any(gap != 0 for gap in gaps):
        warnings.append(
            "Reference analyst reports are not date-aligned with the generated reports; "
            f"observed date gaps are {gaps} days. Scores measure document agreement, not "
            "same-information factual accuracy."
        )
    if any(row["reference_bert_truncated"] for row in rows) or any(
        row["candidate_bert_truncated"] for row in rows
    ):
        warnings.append(
            "BERTScore uses only the configured leading subword tokens of each report; "
            "ROUGE-L and NumberRate use all extracted text."
        )
    warnings.append(
        "FinRpt NumberRate measures numeric quantity only; it does not verify values, units, or periods."
    )
    warnings.append(
        "Accuracy here is analyst recommendation agreement because the real report is used as reference; "
        "it is not realized-return prediction accuracy."
    )
    return warnings


def _versions() -> dict[str, str]:
    packages = ("pdfplumber", "beautifulsoup4", "lxml", "bert-score", "torch", "transformers")
    result = {"python": platform.python_version()}
    for package in packages:
        try:
            result[package] = importlib.metadata.version(package)
        except importlib.metadata.PackageNotFoundError:
            result[package] = "not-installed"
    return result


def _write_outputs(
    output_dir: Path, rows: list[dict[str, Any]], summary: dict[str, Any]
) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / "evaluation_summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    _write_csv(output_dir / "report_metrics.csv", rows)
    _write_csv(output_dir / "condition_summary.csv", summary["by_condition"])
    _write_csv(
        output_dir / "condition_company_summary.csv",
        summary["by_condition_company"],
    )
    (output_dir / "evaluation_summary.md").write_text(
        _render_markdown(summary), encoding="utf-8"
    )


def _write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    if not rows:
        return
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def _render_markdown(summary: dict[str, Any]) -> str:
    lines = [
        "# 실제 애널리스트 보고서 대비 FinRpt식 평가",
        "",
        f"- 비교: {summary['comparison_count']}건 ({summary['company_count']}개 기업)",
        "- Accuracy: 실제 보고서 Buy/non-Buy와 우리 보고서 신규 자금 enter/non-enter 일치율",
        "- 모든 점수는 0~1이며 높을수록 실제 보고서와 더 유사함",
        "",
        "| 조건 | 비교 수 | Accuracy | ROUGE-L | BERTScore | NumberRate |",
        "|---|---:|---:|---:|---:|---:|",
    ]
    for row in summary["by_condition"]:
        lines.append(
            f"| {row['condition']} | {row['comparison_count']} | "
            f"{_fmt(row['accuracy'])} | "
            f"{_fmt(row['rouge_l_f1'])} | {_fmt(row['bertscore_f1'])} | "
            f"{_fmt(row['number_rate'])} |"
        )
    lines.extend(
        [
            "",
            "## 기업별 3회 평균",
            "",
            "| 조건 | 기업 | 실제 의견 | 날짜 차이 | Accuracy | ROUGE-L | BERTScore | NumberRate |",
            "|---|---|---:|---:|---:|---:|---:|---:|",
        ]
    )
    for row in summary["by_condition_company"]:
        lines.append(
            f"| {row['condition']} | {row['company_name']} | {row['reference_rating']} | "
            f"{row['date_gap_days']:+d}일 | {_fmt(row['accuracy'])} | "
            f"{_fmt(row['rouge_l_f1'])} | {_fmt(row['bertscore_f1'])} | "
            f"{_fmt(row['number_rate'])} |"
        )
    lines.extend(
        [
            "",
            "## 주의사항",
            "",
        ]
    )
    lines.extend(f"- {warning}" for warning in summary["warnings"])
    return "\n".join(lines) + "\n"


def _render_console(summary: dict[str, Any], output_dir: Path) -> str:
    lines = [f"Completed {summary['comparison_count']} comparisons."]
    for row in summary["by_condition"]:
        lines.append(
            f"{row['condition']}: Accuracy={_fmt(row['accuracy'])}, "
            f"ROUGE-L={_fmt(row['rouge_l_f1'])}, "
            f"BERTScore={_fmt(row['bertscore_f1'])}, "
            f"NumberRate={_fmt(row['number_rate'])}"
        )
    lines.append(f"Results: {output_dir}")
    return "\n".join(lines)


def _fmt(value: float | None) -> str:
    return "N/A" if value is None else f"{value:.4f}"


def _write_text_once(path: Path, text: str) -> None:
    if path.exists():
        if path.read_text(encoding="utf-8").strip() != text.strip():
            raise ValueError(f"Evaluation text changed; use a new output directory: {path}")
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text + "\n", encoding="utf-8")
