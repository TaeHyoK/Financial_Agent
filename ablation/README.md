# Ablation experiments

논문의 실험 설정, 실행기, 평가 코드와 공개 결과를 모은 디렉터리다. 리포 루트에서 실행한다.

| 위치 | 내용 |
|---|---|
| `run_config/` | 수집 명세·기업 설정, 조건별 입력 준비, 생성·반복 실행, Judge |
| `ablation_suite/` | Random news 표본 추출과 입력 유틸 |
| `ablation_evaluation/` | 보고서 입력 발견과 평가 유틸 |
| `real_report_evaluation/` | 서술 본문 추출 및 BERTScore |
| `results/` | BERTScore와 NP1·NP2·NP3 판정·집계 |
| `docs/` | 실험 정의와 재현 절차 |

보고서는 [`../final_reports/`](../final_reports/README.md)에 있다. [실험 방법](docs/ABLATION.md)과 [평가 결과](results/README.md)를 참고한다.

```bash
python -m pip install -e ".[eval,dev]"
python ablation/run_config/final_report_llm_judge.py --help
python ablation/run_config/no_peer_target_llm_judge.py --help
PYTHONPATH=src:ablation python ablation/run_config/rerun_ablation.py --help
python -m pytest -q
```

키·원자료·참조 PDF·요청 전문·원응답은 별도로 준비해야 한다. `--help`는 모델을 호출하지 않는다. Judge 유료 실행에는 `--execute-paid-api`와 예상 호출 수 확인이 필요하다. 반복 생성과 새 모델 평가 결과를 기존 논문 결과에 자동 합산하지 않는다.
