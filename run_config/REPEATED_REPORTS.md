# 5개 기업 추가 반복 생성

대상: 현대건설, 두산, BGF리테일, 아모레퍼시픽, SK바이오팜.
삼성전자와 코웨이는 제외한다.

- 조건: full, random_news, no_subdata, no_peer, one_team.
- 추가 회차: replicate_02, replicate_03. 최종 보고서 50개.
- 모델: 기존 GPT-5.4 Standard. 기존 프롬프트·생성 설정을 변경하지 않는다.
- 수집·전처리·뉴스 선정·월별 요약: 기존 결과 고정. Random도 다시 추출하지 않는다.
- 하위 분석, 비교기업 종합 분석, Strategy, Writer: 새로 생성한다.
- No-peer만 같은 추가 회차 Full의 대상기업 하위 분석을 공유한다.
- One-team은 기존 GPT-5.4 통합 입력으로 대상·비교기업 분석을 새로 생성한다.
- 1회차 결과와 다른 회차의 분석은 재사용하지 않는다.
- 정상 완료 기준 340회 LLM 호출, 캐시 할인 없는 예상 비용 $57.02.
- 자동 평가나 보고서 내용 수정은 실행하지 않는다.

실행기는 기존 에이전트 구현과 호출 설정을 가져와 사용하며, 별도 모델 정책을 추가하지 않는다.

```bash
cd /data/agent2/financial_agent_ablation_2025h2
python run_config/run_repeated_reports.py prepare
python run_config/run_repeated_reports.py check
python run_config/run_repeated_reports.py launch
```

결과는 `reports/repeated_standard_5companies/replicate_02/<조건>/<기업>/report_<기업>.html`과
`replicate_03`의 동일 구조에 저장한다. 비교기업은 각 대상기업의 `비교기업/` 아래에 있다.

공통 디렉토리 `reports/repeated_standard_5companies/`:

- `index.html`: 완료된 보고서 링크.
- `preparation_manifest.json`: 입력·코드 해시와 실행 범위.
- `status.json`: 진행 단계, 완료 보고서, 실패, 사용량과 추정 비용.
- `llm_usage.jsonl`: 실제 호출별 사용량.
- `background.log`, `worker.pid`: 백그라운드 로그와 프로세스 번호.

기존 결과·코드를 변경하지 않고, 실패한 보고서는 기록한 뒤 다른 보고서를 계속한다.
실행 중인 프로세스가 없고 오류 원인을 검토한 다음에만 `launch-resume`으로 재개한다.
완료 단계는 건너뛰며, 의미·스키마 오류를 이유로 프롬프트를 바꾸거나 자동 재생성하지 않는다.
