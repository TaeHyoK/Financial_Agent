> 기록: 이 문서는 2026-09-10 시점 상태를 적은 것이다. 현재 구조는 README 와 각 에이전트 README 를 본다.

# 개발 기본 모델: GPT-5.4 mini

2026-09-10부터 비용 절감을 위해 현재 실행 경로의 기본 모델을 `gpt-5.4-mini`로 변경했다.

- FINAL 전체 실행, 기업 식별, 뉴스 요약·분석, 재무·시장 분석, 비교기업 비교, Strategy, Writer.
- FINAL의 `check_subdata_guidance.py`, `check_optional_limits.py`, `compare_strategy_stages.py`. `compare_preserved_analysis.py`도 공통 실험 모델을 가져오므로 mini로 실행된다.
- ABLATION 기본 생성 모델과 LLM 평가 기본 모델.

분석 프롬프트·스키마·출력 예산·재시도 정책은 변경하지 않았다. 과거 결과·사용량·평가 기록과 GPT-5.4 단가는 보존했다. `.env`에 모델을 덮어쓰는 설정이 없음을 확인했고 API 키는 수정하지 않았다. 명시적으로 전달한 CLI 모델이나 환경변수는 기본값보다 우선할 수 있다.

과거 실험 경로에 고정된 ABLATION 재현 스크립트 3개(`run_yuhan_pilot.py`, `run_section_weight_reports.py`, `finish_peer_yuhan_preparation.py`)의 GPT-5.4 지정은 유지한다. 일반 개발은 기본 실행기를 사용한다. 기존 평가 및 생성 결과와 mini 결과는 별도 실험으로 관리한다. 저장된 GPT-5.4 분석을 mini Strategy·Writer에 주는 개발 검사는 전체 mini 파이프라인 실험으로 간주하지 않는다.

OpenAI Docs 스킬로 [모델 ID 및 구조화 출력 지원](https://developers.openai.com/api/docs/models/gpt-5.4-mini)을 확인했다. 이번 작업은 설정 변경과 네트워크를 차단한 오프라인 테스트만 수행한다. 실제 모델 품질이나 계정 접근 가능 여부는 유료 호출로 검증하지 않았다.

추가 유료 실행은 예상 비용을 알리고 사용자 승인을 받은 뒤 진행한다.

검증: 네트워크 연결을 차단한 상태에서 FINAL 111개·ABLATION 81개, 총 192개 테스트가 통과했다. 실제 API 호출은 0회다. 테스트 로그의 GPT-5.4 표시는 과거 모델도 처리할 수 있는지 확인하는 모의 호출이며 유료 실행이 아니다.
