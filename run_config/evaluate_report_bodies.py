"""Frozen, body-only ROUGE-L and BERTScore evaluation; no paid API calls."""
import argparse
import csv
import fcntl
import hashlib
import json
import os
from pathlib import Path
import statistics
import sys

WORKSPACE = Path(__file__).resolve().parents[1]
BASELINE_OUTPUT = WORKSPACE / "evaluation"
OUTPUT = BASELINE_OUTPUT / "with_one_team_gpt54"
ONE_TEAM_RUN_ID = "one_team_gpt54_r01"
sys.path[:0] = [str(WORKSPACE), str(WORKSPACE / "src")]
os.environ.setdefault("HF_HUB_OFFLINE", "1")
os.environ.setdefault("TRANSFORMERS_OFFLINE", "1")
from real_report_evaluation import extract
from real_report_evaluation.metrics import rouge_l
from real_report_evaluation.runner import _compute_bert_scores
from transformers import AutoTokenizer

CONDITIONS = ["full", "random_news", "no_peer", "no_subdata", "one_team"]
CONDITION_LABELS = ["Full", "Random news", "No-peer", "No-subdata", "One-team"]
COMPANIES = ["현대건설", "두산", "삼성전자", "BGF리테일", "아모레퍼시픽", "코웨이", "SK바이오팜"]
NEW_REGIONS = {
    "20251020_company_현대건설.pdf": [(1, [44, 200, 358, 809]), (2, [203, 118, 535, 551])],
    "20251031_company_삼성전자.pdf": [(1, [45, 280, 550, 450])] + [(page, [183, 120, 527, 765]) for page in range(2, 8)],
    "20251111_company_두산.pdf": [(1, [45, 280, 555, 525])],
}


def read(path):
    return json.loads(path.read_text(encoding="utf-8"))


def save(path, payload):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def build_report_index(status, one_team_status):
    """Require one completed final report for each company and condition."""
    if status.get("state") != "success" or one_team_status.get("state") != "success":
        raise ValueError("Both original and one-team report generation must be complete")
    reports = {}
    for source, allowed in ((status, set(CONDITIONS) - {"one_team"}), (one_team_status, {"one_team"})):
        for row in source["completed"]:
            key = (row["company"], row["condition"])
            if row["company"] not in COMPANIES or row["condition"] not in allowed or key in reports:
                raise ValueError(f"Unexpected or duplicate completed report: {key}")
            reports[key] = Path(row["report"])
    expected = {(company, condition) for company in COMPANIES for condition in CONDITIONS}
    if set(reports) != expected:
        raise ValueError(f"Missing completed reports: {sorted(expected - set(reports))}")
    return reports



def generation_model_note(models, original_model="gpt-5.4"):
    integrated, downstream = models["integrated_analysis"], models["downstream"]
    if integrated == downstream == original_model:
        return f"One-team과 기존 4개 조건의 분석 모델을 모두 {original_model}로 맞췄다. 월별 뉴스 요약은 기존 결과를 공통으로 재사용했다."
    return (f"One-team 통합 분석은 {integrated}, 비교·Strategy·Writer는 {downstream}로, "
            f"기존 4개 조건의 분석은 {original_model}로 생성했다. "
            "점수 차이는 통합 구조와 모델 변경이 함께 반영된 결과이다.")


def prepare(*, output=OUTPUT, one_team_run_id=ONE_TEAM_RUN_ID):
    status_path = WORKSPACE / "status/report_generation_status.json"
    one_team_path = WORKSPACE / "status/one_team" / f"{one_team_run_id}.json"
    status, one_team_status = read(status_path), read(one_team_path)
    reports = build_report_index(status, one_team_status)
    frozen_regions = BASELINE_OUTPUT / "reference_regions.json"
    if frozen_regions.is_file():
        regions = read(frozen_regions)
    else:
        regions = read(extract.REFERENCE_REGIONS)
        for name, boxes in NEW_REGIONS.items():
            regions["documents"][name] = {"sha256": sha(WORKSPACE / "references" / name),
                "regions": [{"page": page, "bbox": bbox} for page, bbox in boxes]}
    baseline_protocol = read(BASELINE_OUTPUT / "protocol.json") if (BASELINE_OUTPUT / "protocol.json").is_file() else {}
    if baseline_protocol.get("extractor_sha256", sha(Path(extract.__file__))) != sha(Path(extract.__file__)):
        raise ValueError("Narrative body extractor differs from original evaluation")
    baseline_rows = read(BASELINE_OUTPUT / "metrics.json") if (BASELINE_OUTPUT / "metrics.json").is_file() else []
    baseline_index = {(row["company"], row["condition"]): row for row in baseline_rows}
    save(output / "reference_regions.json", regions)
    extract.REFERENCE_REGIONS = output / "reference_regions.json"
    tokenizer = AutoTokenizer.from_pretrained("BAAI/bge-m3", use_fast=False, local_files_only=True)
    inputs, rows = [], []
    for company in COMPANIES:
        reference = next(p for p in (WORKSPACE / "references").glob("*.pdf") if p.stem.split("_company_")[-1].casefold() == company.casefold())
        reference_body, pages = extract.extract_pdf_body(reference)
        ref_path = output / "texts/reference" / f"{company}.txt"
        ref_path.parent.mkdir(parents=True, exist_ok=True)
        ref_path.write_text(reference_body + "\n", encoding="utf-8")
        for condition in CONDITIONS:
            html = reports[(company, condition)]
            body = extract.extract_html_body(html)
            body_path = output / "texts/generated" / condition / f"{company}.txt"
            body_path.parent.mkdir(parents=True, exist_ok=True)
            body_path.write_text(body + "\n", encoding="utf-8")
            lengths = [len(tokenizer.encode(t.strip(), add_special_tokens=True, truncation=False)) for t in (reference_body, body)]
            if max(lengths) > tokenizer.model_max_length:
                raise ValueError(f"Full body exceeds model limit; no truncation: {company}/{condition} {lengths}")
            if any("\ufffd" in t for t in (reference_body, body)):
                raise ValueError(f"Broken text extraction: {company}/{condition}")
            rouge = rouge_l(reference_body, body)
            rows.append({"company": company, "condition": condition, "replicate": 1,
                "rouge_l_precision": rouge.precision, "rouge_l_recall": rouge.recall, "rouge_l_f1": rouge.f1,
                "reference_tokens": lengths[0], "generated_tokens": lengths[1], "reference_pages": pages,
                "reference": str(reference), "reference_sha256": sha(reference), "reference_body_sha256": sha(ref_path),
                "report": str(html), "report_sha256": sha(html), "generated_body_sha256": sha(body_path)})
            baseline = baseline_index.get((company, condition))
            if baseline:
                for key in ("reference_sha256", "reference_body_sha256", "report_sha256", "generated_body_sha256"):
                    if rows[-1][key] != baseline[key]:
                        raise ValueError(f"Original comparison input changed: {company}/{condition}/{key}")
            inputs.append((reference_body, body))
            print(company, condition, "tokens", lengths, "ROUGE-L", round(rouge.f1, 5), flush=True)
    save(output / "protocol.json", {"body_policy": "narrative_body_v1", "regions_sha256": sha(extract.REFERENCE_REGIONS),
        "bert_model": "BAAI/bge-m3", "layers": 24, "idf": False, "rescale": False,
        "truncated": False, "same_body_for_both_metrics": True, "replicates": 1, "paid_judge_called": False,
        "reference_policy": regions["policy"], "new_regions_review": "Rendered reference pages reviewed before scoring; prose earnings-call Q&A included",
        "extractor_sha256": sha(Path(extract.__file__)), "scorer_source": str(Path(sys.modules[_compute_bert_scores.__module__].__file__)),
        "scorer_sha256": sha(Path(sys.modules[_compute_bert_scores.__module__].__file__)),
        "evaluation_script_sha256": sha(Path(__file__)), "conditions": CONDITIONS, "comparisons": len(rows),
        "generation_status_sha256": sha(status_path), "one_team_status_sha256": sha(one_team_path),
        "one_team_run_id": one_team_run_id, "original_inputs_verified": len(baseline_index),
        "generation_models": {"original_analysis": "gpt-5.4", "one_team": one_team_status["models"]},
        "interpretation_note": generation_model_note(one_team_status["models"]),
        "analysis_models_matched": one_team_status["models"]["integrated_analysis"] == one_team_status["models"]["downstream"] == "gpt-5.4"})
    save(output / "rouge_metrics.json", rows)
    return rows, inputs


def evaluate(*, output=OUTPUT, one_team_run_id=ONE_TEAM_RUN_ID, device="cuda:2", prepare_only=False):
    rows, inputs = prepare(output=output, one_team_run_id=one_team_run_id)
    if prepare_only:
        return
    _compute_bert_scores(rows, inputs, model_type="BAAI/bge-m3", num_layers=24, batch_size=1, device=device)
    save(output / "metrics.json", rows)
    with (output / "metrics.csv").open("w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)
    means = [{"condition": c, **{k: statistics.mean(r[k] for r in rows if r["condition"] == c)
             for k in ("rouge_l_f1", "bertscore_f1", "bertscore_precision", "bertscore_recall")}} for c in CONDITIONS]
    save(output / "condition_means.json", means)
    text = "# 7개 기업 보고서 본문 비교\n\n각 기업 1회 실행. 기업별 동일 애널리스트 보고서의 서술 본문을 비교한다. 표·차트·예측 재무제표 표·공시 안내를 제외하며, 서술형 전망과 실적발표 질의응답은 포함한다.\n\n"
    for metric, title in [("bertscore_f1", "BERTScore F1"), ("rouge_l_f1", "ROUGE-L F1")]:
        text += f"## {title}\n\n| 기업 | " + " | ".join(CONDITION_LABELS) + " |\n|---|" + "---:|" * len(CONDITIONS) + "\n"
        for name in COMPANIES:
            values = [next(r[metric] for r in rows if r['company'] == name and r['condition'] == c) for c in CONDITIONS]
            text += "| " + name + " | " + " | ".join(f"{v:.4f}" for v in values) + " |\n"
        text += "| 평균 | " + " | ".join(f"{r[metric]:.4f}" for r in means) + " |\n\n"
    text += "BERTScore는 기존 설정(BAAI/bge-m3, 24층, IDF·기준선 재조정 없음)을 사용했다. 두 지표 모두 동일한 본문 전체를 사용하고 입력을 자르지 않았다. 평균은 기업별 동일 가중 평균이다. 이 점수는 실제 보고서와의 표현·의미 유사도이며 사실 정확성이나 투자판단 품질을 직접 평가하지 않는다. 기업별 1회 생성이므로 통계적 우위를 단정하지 않는다. 유료 LLM 평가와 기사 보존율은 이번 산출에 포함하지 않았다.\n"
    text += "\n" + read(output / "protocol.json")["interpretation_note"] + "\n"
    (output / "평가결과.md").write_text(text, encoding="utf-8")
    save(output / "evaluation_status.json", {"state": "success", "reports_evaluated": len(rows),
        "companies": COMPANIES, "conditions": CONDITIONS, "device": device, "paid_api_calls": 0})
    print(json.dumps(means, ensure_ascii=False, indent=2), flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--prepare-only", action="store_true")
    parser.add_argument("--one-team-run-id", default=ONE_TEAM_RUN_ID)
    parser.add_argument("--output-dir", type=Path, default=OUTPUT)
    parser.add_argument("--device", default="cuda:2")
    args = parser.parse_args()
    args.output_dir.mkdir(parents=True, exist_ok=True)
    with (args.output_dir / ".evaluation.lock").open("a+") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        save(args.output_dir / "evaluation_status.json", {"state": "preparing" if args.prepare_only else "running",
            "reports_planned": len(COMPANIES) * len(CONDITIONS), "device": args.device, "paid_api_calls": 0})
        try:
            evaluate(output=args.output_dir, one_team_run_id=args.one_team_run_id, device=args.device, prepare_only=args.prepare_only)
            if args.prepare_only:
                save(args.output_dir / "evaluation_status.json", {"state": "prepared", "reports_prepared": 35, "paid_api_calls": 0})
        except Exception as exc:
            save(args.output_dir / "evaluation_status.json", {"state": "failed", "error": str(exc), "paid_api_calls": 0})
            raise


if __name__ == "__main__":
    main()
