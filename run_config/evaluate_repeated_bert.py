"""Score saved repeats with the frozen body-only protocol and average three runs."""
import copy
import csv
import fcntl
import statistics
from pathlib import Path

import evaluate_report_bodies as evaluation

BASE = evaluation.WORKSPACE / "evaluation/with_one_team_gpt54"
BATCH = evaluation.WORKSPACE / "reports/repeated_standard_5companies"
OUTPUT = evaluation.WORKSPACE / "evaluation/repeated_standard_5companies"
COMPANIES = ["현대건설", "두산", "BGF리테일", "아모레퍼시픽", "SK바이오팜"]
METRICS = ["bertscore_f1", "bertscore_precision", "bertscore_recall"]


def write_csv(path, rows):
    with path.open("w", newline="", encoding="utf-8-sig") as stream:
        writer = csv.DictWriter(stream, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def main():
    OUTPUT.mkdir(parents=True, exist_ok=True)
    with (OUTPUT / ".evaluation.lock").open("a+") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        evaluation.save(OUTPUT / "evaluation_status.json", {"state": "running", "paid_api_calls": 0})
        protocol = evaluation.read(BASE / "protocol.json")
        if evaluation.sha(Path(evaluation.extract.__file__)) != protocol["extractor_sha256"]:
            raise ValueError("Body extraction differs from the frozen evaluation")
        import sys
        if evaluation.sha(Path(sys.modules[evaluation._compute_bert_scores.__module__].__file__)) != protocol["scorer_sha256"]:
            raise ValueError("BERTScore implementation differs from the frozen evaluation")
        assert protocol["bert_model"] == "BAAI/bge-m3" and protocol["layers"] == 24
        assert protocol["idf"] is False and protocol["rescale"] is False
        prior = [copy.deepcopy(r) for r in evaluation.read(BASE / "metrics.json") if r["company"] in COMPANIES]
        assert len(prior) == 25
        references = {}
        for company in COMPANIES:
            row = next(r for r in prior if r["company"] == company)
            path = BASE / "texts/reference" / f"{company}.txt"
            assert evaluation.sha(path) == row["reference_body_sha256"]
            assert evaluation.sha(Path(row["reference"])) == row["reference_sha256"]
            references[company] = path.read_text(encoding="utf-8").removesuffix("\n")
        for row in prior:
            assert evaluation.sha(Path(row["report"])) == row["report_sha256"]
            assert evaluation.sha(BASE / "texts/generated" / row["condition"] / f"{row['company']}.txt") == row["generated_body_sha256"]
        state = evaluation.read(BATCH / "status.json")
        assert state["state"] == "success" and len(state["completed"]) == 50
        tokenizer = evaluation.AutoTokenizer.from_pretrained(protocol["bert_model"], use_fast=False, local_files_only=True)
        rows, inputs = [], []
        for result in state["completed"]:
            repeat, condition, company = result["key"].split("/")
            report = Path(result["report"])
            assert evaluation.sha(report) == result["sha256"]
            reference = references[company]
            body = evaluation.extract.extract_html_body(report)
            assert "\ufffd" not in reference + body
            lengths = [len(tokenizer.encode(t.strip(), add_special_tokens=True, truncation=False)) for t in (reference, body)]
            assert max(lengths) <= tokenizer.model_max_length, (result["key"], lengths)
            body_path = OUTPUT / "texts/generated" / repeat / condition / f"{company}.txt"
            body_path.parent.mkdir(parents=True, exist_ok=True)
            body_path.write_text(body + "\n", encoding="utf-8")
            row = {k: v for k, v in next(r for r in prior if r["company"] == company and r["condition"] == condition).items() if not k.startswith("rouge_") and k not in METRICS}
            row.update(replicate=int(repeat[1:]), report=str(report), report_sha256=result["sha256"], generated_body_sha256=evaluation.sha(body_path), reference_tokens=lengths[0], generated_tokens=lengths[1], offline_recovery=result.get("offline_recovery", ""))
            rows.append(row)
            inputs.append((reference, body))
        evaluation.save(OUTPUT / "prepared_metrics.json", rows)
        evaluation._compute_bert_scores(rows, inputs, model_type=protocol["bert_model"], num_layers=protocol["layers"], batch_size=1, device="cuda:2")
        all_rows = [{k: v for k, v in r.items() if not k.startswith("rouge_")} for r in prior] + rows
        for r in all_rows:
            r.setdefault("offline_recovery", "")
        assert {(r["company"], r["condition"], r["replicate"]) for r in all_rows} == {(c, k, n) for c in COMPANIES for k in evaluation.CONDITIONS for n in (1, 2, 3)}
        means = []
        for company in COMPANIES:
            for condition in evaluation.CONDITIONS:
                group = [r for r in all_rows if r["company"] == company and r["condition"] == condition]
                means.append({"company": company, "condition": condition, "replicates": len(group), **{m: statistics.mean(r[m] for r in group) for m in METRICS}, "bertscore_f1_std": statistics.stdev(r["bertscore_f1"] for r in group)})
        condition_means = [{"condition": condition, **{m: statistics.mean(r[m] for r in means if r["condition"] == condition) for m in METRICS}} for condition in evaluation.CONDITIONS]
        for summary in condition_means:
            run_means = [statistics.mean(r["bertscore_f1"] for r in all_rows if r["condition"] == summary["condition"] and r["replicate"] == n) for n in (1, 2, 3)]
            summary["bertscore_f1_std"] = statistics.stdev(run_means)
        evaluation.save(OUTPUT / "metrics.json", all_rows)
        write_csv(OUTPUT / "metrics.csv", all_rows)
        evaluation.save(OUTPUT / "company_condition_means.json", means)
        write_csv(OUTPUT / "company_condition_means.csv", means)
        evaluation.save(OUTPUT / "condition_means.json", condition_means)
        protocol.update(replicates=3, comparisons=75, companies=COMPANIES, baseline_metrics_sha256=evaluation.sha(BASE / "metrics.json"), original_scores_reused=25, new_scores_computed=50, device="cuda:2", paid_api_calls=0,
                        offline_recovery_note="r03/one_team/아모레퍼시픽: two chart-link metadata arrays repaired offline; narrative body unchanged.")
        evaluation.save(OUTPUT / "protocol.json", protocol)
        text = "# 기업별 BERTScore 평균 및 표준편차\n\n각 기업·조건의 3회 실행 BERTScore F1을 평균 ± 표본 표준편차(n=3, 분모 n−1)로 표시했다. 기존 1회 점수와 추가 2회 점수를 사용했다. 전체 평균 행의 표준편차는 각 실행에서 5개 기업 점수를 평균한 뒤, 그 3개 평균값으로 계산했다.\n\n| 기업 | " + " | ".join(evaluation.CONDITION_LABELS) + " |\n|---|" + "---:|" * 5 + "\n"
        for company in COMPANIES:
            values = [next(r for r in means if r["company"] == company and r["condition"] == c) for c in evaluation.CONDITIONS]
            text += "| " + company + " | " + " | ".join(f"{v['bertscore_f1']:.4f} ± {v['bertscore_f1_std']:.4f}" for v in values) + " |\n"
        text += "| 전체 평균 | " + " | ".join(f"{r['bertscore_f1']:.4f} ± {r['bertscore_f1_std']:.4f}" for r in condition_means) + " |\n\n"
        text += "실제 애널리스트 보고서와 생성 보고서의 서술 본문을 비교했다. 표·차트·예측 재무제표 표·안내문은 제외하고 서술형 전망은 포함했다. 기존 BAAI/bge-m3 24층 설정(IDF·기준선 재조정 없음)을 유지했으며 본문을 자르지 않았다. 유료 API 호출은 없다. 정밀도·재현율과 실행 간 표준편차는 CSV에 보존했다. 이 점수는 본문 의미 유사도이며 분석 정확성이나 투자판단 품질을 직접 측정하지 않는다.\n\n아모레퍼시픽 3회차 One-team은 차트 근거 연결 2건만 오프라인 복구했으며 평가 본문은 수정되지 않았다.\n"
        (OUTPUT / "평가결과.md").write_text(text, encoding="utf-8")
        evaluation.save(OUTPUT / "evaluation_status.json", {"state": "success", "reports_evaluated": 75, "scores_reused": 25, "new_scores": 50, "paid_api_calls": 0})
        print(text, flush=True)


if __name__ == "__main__":
    main()
