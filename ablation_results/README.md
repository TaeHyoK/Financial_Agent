# ablation_results

논문 실험의 상태 파일과 자동 지표(BERTScore·ROUGE-L) 결과다. 생성 당시 작업공간의 절대경로는 아래 자리표시자로 바꿔 두었다. 값 자체(해시·지표·조건)는 생성 시점 그대로다.

| 자리표시자 | 뜻 |
|---|---|
| `${REPO}` | 이 저장소 루트 |
| `${ABLATION_WORKSPACE}` | 생성 당시 작업공간. `reports/`(생성 보고서), `references/`(실제 애널리스트 PDF), `prepared_inputs/`, `collected_data/` 가 그 아래에 있었다. Git 에는 없고 이동용 ZIP(`docs/ABLATION_HANDOFF_20260920.md` 5절)으로 옮겼다 |
| `${LEGACY_ABLATION_WORKSPACE}`, `${LEGACY_FINAL_WORKSPACE}` | 그 이전 실험의 작업공간 |

`status/*.json` 의 `code_hashes`·`source_hashes` 는 생성 시점 파일의 sha256 기록이다. 정리 커밋 이후 트리와는 맞지 않으며, 재현 검사용이 아니라 생성 시점 증거로 남겨 둔다(`docs/ABLATION_HANDOFF_20260920.md` 10절).
