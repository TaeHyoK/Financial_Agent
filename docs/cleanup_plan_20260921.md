# dead code 정리·버전 통일 계획

기준 시각: 2026-09-21
대상: `ablation-final-handoff-20260920` 브랜치
전제: 최종 보고서 75개를 만든 산출 동작과 프롬프트는 바꾸지 않는다. 정리는 동작을 보존하는 리팩터로만 한다.

## 1. 조사 방법과 확인된 사실

네 갈래로 조사했고 서로 교차 확인했다.

- 실제 75개를 만든 실행 경로를 import·호출 단위로 추적
- v1~v5 계열 인벤토리와 `agent.py` 최상위 함수 164개 분류
- vulture·pyflakes·git grep 으로 미참조 함수·상수·import·모듈 추출
- venv 를 만들어 테스트 기준선 확보

### 1-1. 살아 있는 경로

75개는 `run_config/` 다섯 실행기가 만들었다. 순서는 원자료 수집 → 조건별 입력 동결 → r01 4조건 생성 → r01 one-team 생성 → r02·r03 50개 생성.

- Strategy 는 `--packet-version v5` 하드코딩(`src/orchestration/full_report_pipeline.py:891`)으로 v5 경로만 실행된다. v1~v4 실행 함수와 프롬프트 4개는 어디서도 도달하지 않는다. (이름 변경 이후: `--packet-version` 인자는 제거)
- v5 는 세 모듈을 쌓아 쓴다. `contracts_v2` 의 패킷·카드 빌더, `contracts_v4` 의 컨텍스트 골격, `contracts_v5` 의 투영·검증. 그래서 "구버전 파일 삭제" 식 통일은 불가능하고, 구버전 전용 함수만 걷어내야 한다. (이름 변경 이후: `contracts_v2` → `packet`, `contracts_v4` → `context`, `contracts_v5` → `decision`)
- Writer 는 Strategy 가 늘 v5 결정을 내므로 `writer_handoff` 의 v5 분기만 탄다. v4·v2 분기와 v1 유물은 도달하지 않는다.
- One-team 조건은 코드 분기가 아니라 `PYTHONPATH` 앞에 `Unified_Agent/runtime` 을 두고 `sitecustomize` 가 모듈 이름 문자열로 골라 함수를 갈아끼우는 방식이다. grep 에는 이 의존이 보이지 않는다.
- `orchestration.full_report_pipeline.run_full_pipeline` 과 `end_to_end_loop` 는 75개 생성에 한 번도 불리지 않았다. 실행기는 같은 모듈의 커맨드 빌더와 경로 데이터클래스만 쓴다.
- LLM Judge(`run_config/final_report_llm_judge.py`)는 리포 내부 모듈을 하나도 import 하지 않는다. 정리 작업이 Judge 에 영향을 줄 경로가 없다.

### 1-2. 테스트 기준선

```
cd /home/tkim298/agent2/Financial_Agent && .venv/bin/python -m pytest -q -rs
→ 257 passed, 19 skipped, 27 subtests passed (약 23초)
```

인계 문서 수치와 총계는 같지만 구성이 다르다. ZIP 을 풀어 Judge 준비 테스트 3개가 돌게 됐고, 대신 `tests/test_one_team.py` 의 러너 테스트 3개가 리포 바깥 경로를 찾다가 스킵된다. 그 3개는 스킵을 풀면 `YFinance_Agent/reporting.py:31` 의 `from valuation import` 때문에 setUpClass 에서 실패한다. 이번 정리에서는 고치지 않고 스킵 상태를 유지한다. 나머지 16개 스킵은 Git 에 없는 127MB 동결 입력 때문이며 인계 문서와 같다.

### 1-3. 정리 작업을 묶는 제약

1. **코드 해시 동결.** 세 실행기가 `src/**/*.py` 전체의 sha256 을 상태 파일에 적어 두고 `check()` 에서 대조한다(`run_config/run_repeated_reports.py:80-81, 103-106`, `run_prepared_reports.py:67-76`, `run_one_team_reports.py:109, 127`). src 아래 파일을 하나라도 고치면 생성 재실행·재개 검사가 막힌다. 생성은 이미 끝났으므로 Judge 에는 영향이 없지만, "이 트리로 생성을 재현할 수 있다"는 성질은 정리 시점에 끊긴다.
2. **sitecustomize 패치 대상 이름.** `Agent_Team.Strategy_Agent.agent`, `contracts_v2`, `contracts_v4`, `Competitor_Agent.peer_comparison`, `comparison_agent`, `data_loader`, `html_report_writer`, `shared.domain_llm` 모듈 이름과 그 안의 `build_strategy_input_bundle`, `load_required_json`, `_financial_cards`, `_card`, `STRATEGY_SECTIONS`, `build_strategy_context_package_v4`, `_limitation_card_assignments_v2`, `execute_with_telemetry` 이름은 바꿀 수 없다. (이름 변경 이후: `_limitation_card_assignments_v2` → `_limitation_card_assignments`, `build_strategy_context_package_v4` → `build_base_strategy_context`, `contracts_v2` → `packet`, `contracts_v4` → `context`)
3. **import 순서.** `contracts_v5.py:14` 가 `build_strategy_context_package_v4` 를 import 시점에 바인딩한다. `agent.py` 가 `contracts_v4` 를 먼저 import 하기 때문에 패치된 함수가 바인딩된다. v4 함수를 v5 안으로 옮기거나 import 순서를 바꾸면 one-team 산출이 조용히 바뀐다. (이름 변경 이후: `build_strategy_context_package_v4` → `build_base_strategy_context`, `contracts_v4` → `context`, `contracts_v5` → `decision`)
4. **산출물 파일명과 계약 문자열.** `strategy_compact_packet_v2.json`, `strategy_packet_provenance_v2.json`, `strategy_decision_output_v5.json`, `writer_editorial_packet_v3.json` 등 파일명, `DECISION_VERSION`·`STRATEGY_CACHE_VERSION` 상수값은 Writer 입력·캐시 키·계약 판별에 쓰이므로 바꾸지 않는다.
5. **테스트가 private 헬퍼를 직접 import.** `_news_cards`, `_reader_limitations`, `_news_claim_card`, `_card`, `_attach_secondary_context`, `_market_cards`, `_news_handoff`, `_writer_card_v5`, `_select_v5_limitations`, `_writer_report_schema_v2`, `_output_contract_v2`, `_system_prompt_v2` 등. 이름을 바꾸면 테스트가 바로 깨진다. (이름 변경 이후: `_output_contract_v2` → `_output_contract`, `_select_v5_limitations` → `_select_limitations`, `_system_prompt_v2` → `_editorial_system_prompt`, `_writer_card_v5` → `_writer_card`, `_writer_report_schema_v2` → `_writer_report_schema`)
6. **평가 코드 해시.** `run_config/evaluate_repeated_bert.py:30-34` 가 `real_report_evaluation/extract.py`·`runner.py` 의 sha256 을 `protocol.json` 과 대조한다. 이 두 파일은 건드리지 않는다.

## 2. 파일별 분류

등급 뜻:
- **A 확실한 dead** — 실행 경로·테스트·스크립트 어디에서도 참조 없음. 지워도 동작·테스트 불변.
- **B 이전 버전** — v5 경로에서 도달 불가. 테스트도 잡지 않음. 지우면 코드가 통일됨.
- **C 결정 필요** — 75개 생성에는 안 쓰였지만 테스트·스크립트·다른 엔트리포인트가 붙잡고 있음.
- **D 건드리지 않음** — 살아 있거나, 이름 자체가 계약임.

### 2-1. 등급 A — 확실한 dead

파일 통째:

| 파일 | 근거 |
|---|---|
| `src/Agent_Team/Financial_Agent/same_period_financial_chart.py` (511줄) | 자기 파일 밖 참조 없음. 상태 JSON 의 해시 목록에만 등장 |
| `src/Agent_Team/YFinance_Agent/target_kospi_chart.py` (344줄) | 동일 |
| `src/Agent_Team/YFinance_Agent/report.py` | 어떤 코드도 import 안 함. README 에만 등장 |
| `src/Agent_Team/Strategy_Agent/contracts_v3.py` (597줄) | `agent.py:33-38` 이 import 만 함. v3 실행 경로 제거와 함께 삭제 |
| `ablation_suite/legacy_unified.py` (124줄) | import 하는 곳 0. 의존 방향이 legacy→unified 라 삭제해도 unified 무영향 |
| `src/Agent_Team/Writer Agent/__init__.py`, `src/Agent_Team/Visualization Agent/__init__.py` | 폴더명에 공백이 있어 패키지 import 자체가 불가. `from .writer_agent import` 는 영원히 실행 안 됨 |

함수·클래스 (외부 참조 0, 파일 내부 호출 0):

| 파일 | 이름 (줄) |
|---|---|
| `Strategy_Agent/agent.py` | `normalize_strategy_decision_output` 2878, `dedupe_paths` 5249 |
| `Strategy_Agent/contracts_v2.py` | `_preserve_news_counterevidence` 2449-2484 (이름 변경 이후: `contracts_v2` → `packet`) |
| `Writer Agent/writer_handoff.py` | `build_writer_handoff` 1649-1745, `handoff_json_size` 1789-1792, `reformat_financial_reader_observations` 1169-1185 (v1 유물). `validate_writer_handoff` 1748 은 html_report_writer 레거시 가지가 부르므로 3단계에서 함께 제거 |
| `Writer Agent/html_report_writer.py` | `build_html_report_payload` 73 |
| `Visualization Agent/chart_builders.py` | `_safe_category_label` 785 |
| `Financial_Agent/handoff_builder.py` | `build_single_report_canonical` 124, `build_2y_handoff` 370 |
| `Financial_Agent/report_resolver.py` | `resolve_reports` 222, `resolve_primary_report` 238 |
| `Financial_Agent/main.py` | `_build_master` 225 |
| `Financial_Agent/langgraph_flow.py` | `_weekly_period_start` 295 |
| `News_Agent/analysis_agent.py` | `_first_comparison` 1154, `_market_unit` 1165, `_month_window` 1229 |
| `News_Agent/dart/collect.py` | `fetch_latest_quarterly_xml` 246 |
| `News_Agent/dart/schemas.py` | `CorporateContextChunk` 55 |
| `News_Agent/ranking/embedding.py` | `cosine_similarity_matrix` 57 |
| `YFinance_Agent/reporting.py` | `_weekly_period_start` 424, `_news_period_summary_items` 436, `_first_usable_comparison` 464 |
| `shared/llm_clients.py` | `partition_by_prompt_budget` 300 |
| `orchestration/dependency_graph.py` | `dependency_names` 27 |
| `ablation_suite/section_weighting.py` | `select_by_week` 119, `make_report_context` 135 |
| `ablation_suite/unified.py` | `_evidence_ids` 513 |
| `run_config/final_report_llm_judge.py` | `append_jsonl` 193 |

모듈 상수 (리포 전체 출현 1회, 17건): `EQUAL_WEIGHTS` ×2, `SECONDARY_MARKET_METRICS`, `RECENT_RAW_MONTH_COUNT`, `SECONDARY_FINANCIAL_METRICS` ×2, `TABLE_HEADER_SEP`, `SECTION_MAP`, `OPTIONAL_MARKET_COLUMNS`, `OPTIONAL_DART_METRICS`, `DEFAULT_MARKET_JSON`, `DEFAULT_DART_JSON`, `DEFAULT_NEWS_JSON`, `DOMAIN_POLICY_VERSION`, `NEWS_CRITICAL_OVERFLOW_LIMIT`, `NEWS_DEFAULT_CARD_LIMIT`, `READER_LIMITATION_LIMIT`.
`DOMAIN_POLICY_VERSION` 처럼 버전 스탬프 성격이면 문서 목적으로 남길지 실행 때 판단한다.

미사용 import (pyflakes 32건): `langgraph_flow.py:2,12`, `financial_analysis_agent.py:21,160`, `html_report_validator.py:9`, `analysis_agent.py:3,18`, `reporting.py:5,29,236`, `full_report_pipeline.py:36`, `recover_amore_writer_r03.py:11`, tests 4건, scripts 2건 등.
단 `shared.subdata.*_subdata` 3건(langgraph_flow·analysis_agent·reporting)과 `ablation_suite/unified.py` 의 9건, `Unified_Agent/runtime/model_policy.py:1` 은 모듈 로드 부작용이나 이름공간 노출을 노린 것일 수 있어 실행 단계에서 확인 후 결정한다.

미사용 지역변수 3건: `financial_analysis_agent.py:170`, `reporting.py:247,256`.

### 2-2. 등급 B — 이전 버전 (v5 경로 도달 불가)

**Strategy `agent.py` (5,296줄 중 약 3,000줄 추정)**

| 계열 | 제거 대상 (줄) | 남기는 것 |
|---|---|---|
| 실행 경로 | `_run_strategy_agent_v1` 295-400, `_v2` 403-540, `_v3` 543-682, `_v4` 685-835 | `_run_strategy_agent_v5` 838-986 (이름 변경 이후: `_run_strategy_agent_v5` 는 `run_strategy_agent` 본문으로 인라인) |
| 디스패처 | `run_strategy_agent` 165-292 의 v1~v4 분기와 `STRATEGY_PACKET_VERSION` env 스위치, `_emit_v2_shadow_artifacts` 1137-1170 | v5 분기. `packet_version` 인자는 v5 외 값이면 명시적 오류 |
| v1 2-call 구조 | `run_content_planner` 2458, `run_decision_agent` 2479, `build_strategy_llm_packet` 1890 부터 `validate_decision_basis_card` 4436 까지의 v1 전용 77개 함수 | — |
| 투영·렌더 | `build_strategy_report_projection_v2/v3/v4`, `render_strategy_projection_markdown_v2/v3/v4` 1173-1499 | v5 1502-1610 |
| 지문 | `strategy_v2/v3/v4_fingerprint` 1613-1696 | v5 1699-1726 |
| 결정 호출 | `run_decision_agent_v2/v3/v4` 2521-2637, `build_strategy_generation_payload_v2/v4` 2678-2704, 2751-2776, `decision_generation_prompt_v2/v3/v4` 2707-2797 | v5 2640-2675, 2800-2846 |
| 프롬프트 로더 | `decision_prompt_v2/v3/v4` 4955-4997 | `decision_prompt_v5` 5000-5012 (이름 변경 이후: `decision_prompt_v5` → `decision_prompt`) |
| 산출물 청소 | `_remove_strategy_v5_artifacts` 1121-1134 (v4 경로만 사용) | `_remove_deprecated_v1_strategy_artifacts`, `_remove_strategy_v2/v3/v4_artifacts` 는 v5 경로 끝(979-982)에서 구버전 산출물 청소용으로 호출되므로 유지 (이름 변경 이후: `_remove_deprecated_v1_strategy_artifacts` → `_remove_legacy_strategy_artifacts`) |
| 호환 래퍼 | `generate_strategy_report` 1729-1798 의 `output_md` 분기 1789-1796 은 v5 케이스가 없어 v1 렌더러로 떨어짐. 구버전 렌더러 제거 시 `NameError` 가 나므로 v5 단일 분기로 고침 | `cli.py:137` 이 부르므로 함수는 유지 |
| import | `contracts_v3` 전체, `contracts_v2` 의 `finalize_strategy_decision_v2`·`strategy_decision_response_format_v2`, `contracts_v4` 의 `finalize_strategy_decision_v4`·`strategy_decision_response_format_v4`·`validate_strategy_decision_v4` | — (이름 변경 이후: `contracts_v2` → `packet`, `contracts_v4` → `context`) |

**`contracts_v2.py` (2,545줄)**: v2 결정 블록 248-969 의 19개 함수. `strategy_decision_response_format_v2`, `finalize_strategy_decision_v2`, `validate_strategy_decision_v2`, `_derive_factor_card_keys`, `_factor_families`, `_filter_forward_support_card_keys`, `_strategy_reader_text`, `_assert_no_reader_recommendation_labels`, `_validate_factor_keys`, `_validate_recommendation_bridge`, `_is_valuation_bridge_card`, `_bridge_keys`, 그리고 v3 만 쓰던 `_is_price_bridge_card`, `_reject_duplicate_strings`, `_assessment_schema_for_card`, `_peer_finding_schema`, `_peer_pair_direction`, `_card_array_schema`. 나머지 44개 함수와 상수는 v5 가 쓰므로 유지. (이름 변경 이후: `contracts_v2` → `packet`)

**`contracts_v4.py` (594줄)**: v4 결정 블록 `strategy_decision_response_format_v4` 100-224, `finalize_strategy_decision_v4` 227-253, `validate_strategy_decision_v4` 256-413, `_decision_card_keys` 550, `_structured_peer_card_keys` 556, `_reader_text` 568-594. `build_strategy_context_package_v4`, `validate_strategy_context_package_v4`, `_neutralize_card`, `_same_period_comparison_directions`, `_clean_handoff`, `_financial_handoff`, `_news_handoff`, `_market_handoff` 는 v5 가 쓰므로 유지. (이름 변경 이후: `build_strategy_context_package_v4` → `build_base_strategy_context`, `contracts_v4` → `context`, `validate_strategy_context_package_v4` → `validate_base_strategy_context`)

**프롬프트**: `prompts/decision_agent.md`, `decision_agent_v2.md`, `decision_agent_v3.md`, `decision_agent_v4.md`, `content_planner.md` 삭제. `decision_agent_v5.md` 만 유지. (이름 변경 이후: `decision_agent_v5.md` → `decision_agent.md`)

**Writer `writer_handoff.py`**: `_build_writer_editorial_packet_v4` 225-431, `_writer_card_v4` 786-842, v2 폴백 68-222 와 v2 전용 헬퍼 `_writer_card` 1138, `_select_key_evidence_cards` 1604, `_select_keys` 1631, `_eligible_keys` 1635. 디스패처 `build_writer_editorial_packet` 50-67 은 v5 아니면 명시적 오류로 단순화.

**Writer `html_report_writer.py` / `html_report_validator.py`**: `_is_v2_writer_packet` 이 거짓인 레거시 handoff 가지 (`html_report_writer.py` 의 `_output_contract` 1019-1029, `_system_prompt` 비-v2 분기 1544-1571, `validate_writer_handoff` 호출 1698, `json_object` 응답 형식 1224 등 약 20곳). writer_handoff 가 늘 v2/v3 패킷을 내므로 도달 불가. 단 `_is_v2_writer_packet`·`_is_label_free_writer_packet`·`_uses_narrative_evidence` 함수 이름과 `_limitation_card_assignments_v2`(sitecustomize 패치 대상)는 유지. (이름 변경 이후: `_is_v2_writer_packet` → `_is_editorial_packet`, `_limitation_card_assignments_v2` → `_limitation_card_assignments`)

**`Unified_Agent/runtime/integrated_handoff.py`, `sitecustomize.py`**: 건드리지 않음. 단 `contracts_v2`/`contracts_v4` 패치 대상 함수가 남아 있는지 실행 후 확인. (이름 변경 이후: `contracts_v2` → `packet`, `contracts_v4` → `context`)

### 2-3. 등급 C — 결정 필요

| 대상 | 75개 생성에서 | 붙잡고 있는 것 | 판단 |
|---|---|---|---|
| `src/orchestration/full_report_pipeline.py` 의 `run_full_pipeline` 339, `build_domain_pipeline_command` 710, `validate_full_pipeline_outputs` 1042, `_resolve_peer_selection` 1135, 스냅숏 로더 1158-1250, 스테이지 실행기 1378-1608, `main` 1693 | 미실행 | `pyproject` 의 `financial-report` 엔트리, tests 2건 | 제품의 1-커맨드 경로. 실험에는 안 쓰였지만 "산출 과정 코드" 에 해당하므로 **유지 권고** |
| `src/orchestration/{cli,end_to_end_loop,paths,manifest,run_state,validators,dependency_graph,snapshot_validation}.py` | 미실행. `ablation_suite/snapshots.py:23` 경유로 로드만 됨 | `agent-team-loop` 엔트리, tests | 위와 같은 이유로 **유지 권고**. 지우려면 `ablation_suite` 슬림화가 선행 |
| `src/Agent_Team/Competitor_Agent/peer_resolver.py` | import 만 | `run_full_pipeline` 전용 | 위 결정에 종속 |
| `src/Agent_Team/YFinance_Agent/run_pipeline.py` | 미실행 | `yfinance-pipeline` 엔트리 | 실행기는 `main.py` 를 직접 부름. **삭제 후보**, 엔트리도 함께 제거 |
| `ablation_suite/{runner,protocol,source_lock,unified,integrated_report,recommendations,section_weighting}.py`, `run_ablation.py` | 미도달 | `run_ablation.py` 만 | 이전 실험 스택. `config`·`utils`·`annual_random`·`snapshots` 는 살아 있으므로 파일 단위 분리 필요. **삭제 후보** |
| `ablation_evaluation/` 전체, `run_evaluation.py` | 미도달 | `run_evaluation.py` 만 | 이번 Judge 와 별개 구현. **삭제 후보** |
| `real_report_evaluation/discovery.py`, `runner.py` 의 `main`, `run_real_report_evaluation.py` | `_compute_bert_scores` 만 사용 | 엔트리 | `runner.py` 는 해시 동결 대상이라 **손대지 않음**. `discovery.py`·`run_real_report_evaluation.py` 는 삭제 후보 |
| `run_config/prepare_cited_judge_pilot.py` | 산출물 미존재 | 없음 | 파일럿이 최종 Judge 설계에 흡수됨. **삭제 후보** |
| `run_config/recover_amore_writer_r03.py` | 75개 중 1개의 최종 산출에 관여 | 없음 | 실행 기록이므로 **유지** |
| `Writer Agent/html_report_validator.py` | 미사용. `writer_agent.py:346` 은 오히려 검증 파일을 삭제 | scripts 2, tests 7 | 테스트 전용 검증기. **유지**, 위치만 재고 |
| `scripts/*.py` 6개 | 미관여 | docs 근거 생성 | 문서 재현용. **유지** |
| 테스트·스크립트만 참조하는 함수 30건 (예: `langgraph_flow.build_financial_trends`, `company_resolver.resolve_naver_market`, `end_to_end_loop.AgentTeamOrchestrator`) | 미실행 | tests | 테스트를 같이 지우지 않는 한 **유지** |
| `contracts_v2.validate_strategy_decision_v2`, `PacketOverflowError` 등 `__all__` 에만 있는 4건 | 호출 0 | `__all__` | v2 블록 제거와 함께 정리 (이름 변경 이후: `contracts_v2` → `packet`) |

### 2-4. 등급 D — 건드리지 않음

- `src/Agent_Team/Unified_Agent/runtime/sitecustomize.py`, `runtime/model_policy.py`(1줄 shim), `runtime/integrated_handoff.py`
- 빈 `__init__.py` 패키지 마커 (`News_Agent/{collectors,io,pipelines,ranking}`, `Unified_Agent`)
- `Strategy_Agent/prompts/decision_agent_v5.md`, `Competitor_Agent/prompts/comparison_agent.md`, `configs/news_default.yaml` (이름 변경 이후: `decision_agent_v5.md` → `decision_agent.md`)
- `real_report_evaluation/extract.py`, `runner.py` (평가 해시 동결)
- 산출물 파일명, `DECISION_VERSION_*`, `STRATEGY_CACHE_VERSION_*`, `EDITORIAL_PACKET_VERSION*` 상수값
- `contracts_v2`, `contracts_v4` 모듈 이름과 sitecustomize 패치 대상 함수 이름 (이름 변경 이후: `contracts_v2` → `packet`, `contracts_v4` → `context`)
- `agent.py` 의 `contracts_v4` → `contracts_v5` import 순서 (이름 변경 이후: `contracts_v4` → `context`, `contracts_v5` → `decision`)
- `YFinance_Agent/reporting.py:31` 의 `from valuation import` (잠재 결함이지만 동작 변경 금지 범위)

## 3. 통일 방향

- **Strategy**: v5 단일 경로. 모듈 3개(`contracts_v2`·`v4`·`v5`)는 이름을 유지한 채 각각 "패킷·카드", "컨텍스트 골격", "투영·검증" 역할만 남긴다. 파일을 합치거나 이름을 바꾸는 것은 sitecustomize·테스트·파일명 제약 때문에 이번 범위에서 뺀다. (이름 변경 이후: `contracts_v2` → `packet`)
- **Writer**: v5 결정 입력 단일 경로. 패킷 버전 상수(`writer_editorial_packet_v3`)와 파일명은 유지.
- **접미사 `_v2`·`_v4`·`_v5` 제거**: 하지 않는다. 파일명·캐시 키·패치 대상·테스트 import 에 묶여 있어 이름을 바꾸면 산출물이나 테스트가 바뀐다. 논문 제출 뒤 별도 작업으로 미룬다.
- **중복 유틸**(`_load_json` 7곳, `_dict` 7곳, `_load_env_file` 4곳 등 66개 이름): 동작이 파일마다 미묘하게 다를 수 있어 이번 범위에서 뺀다. 필요하면 별도 단계.

## 4. 실행 순서

각 단계는 별도 커밋. 커밋 전에 diff 와 메시지를 보이고 허락을 받는다.

| 단계 | 내용 | 확인 방법 |
|---|---|---|
| 0 | 작업 브랜치 생성. 기준선 기록 (`pytest -q -rs` 257/19 구성, `validate` 360/360/0, `git rev-parse HEAD`) | — |
| 1 | 등급 A 파일 7개 삭제, 함수·클래스·상수·import 제거. `contracts_v3` 는 2단계로 미룸 | pytest 257/19 동일 구성. pyflakes 미정의 이름 0 |
| 2 | Strategy v1~v4 제거: `agent.py` 구버전 함수, `contracts_v3` 삭제, `contracts_v2`·`v4` 결정 블록 제거, 프롬프트 4개 삭제, 디스패처·`generate_strategy_report` v5 단일화 | pytest 동일. AST 로 v5 경로 도달 함수 집합이 정리 전후 같은지 비교. `ONE_TEAM_RUNTIME=1` + runtime PYTHONPATH 로 import 만 해서 `contracts_v5` 가 바인딩한 `build_strategy_context_package_v4` 가 패치된 함수인지 확인. `validate` 360/360/0 (이름 변경 이후: `build_strategy_context_package_v4` → `build_base_strategy_context`, `contracts_v2` → `packet`, `contracts_v5` → `decision`) |
| 3 | Writer v2·v4 분기 제거, `html_report_writer`·`validator` 레거시 가지 제거 | pytest 동일. 75개 중 1개의 Writer 응답(`recover_amore_writer_r03.py` 가 다루는 것)으로 `validate_raw_writer_payload`·`normalize_report_payload` 가 같은 결과를 내는지 확인 |
| 4 | 등급 C 중 결정된 것 처리 (`pyproject` 엔트리 정리 포함) | pytest. 삭제한 엔트리에 대응하는 테스트가 있으면 함께 정리 |
| 5 | 문서 정리: `Strategy_Agent/README.md` 의 존재하지 않는 `evaluate_recommendation_bias` 안내와 "과거 v5" 제목 정정, `WRITER_AGENT_WORKFLOW.md` 확인, 인계 문서에 해시 동결 상태 변화 기록 | — |

## 5. 결정이 필요한 것

1. **코드 해시 동결을 어떻게 다룰지.** → 2026-09-21 결정: 상태 파일은 그대로 두고 인계 문서에 기록한다.
   원안: 정리하면 세 생성 실행기의 `check()` 가 막힌다. 생성은 끝났고 Judge 는 영향이 없다. 선택지는 둘이다. 상태 파일을 역사 기록으로 그대로 두고 인계 문서에 "정리 커밋 이후 트리는 생성 시점과 다르다" 고 적거나, 정리 후 매니페스트를 다시 만든다. 다시 만들면 "생성 시점 코드" 라는 증거 가치가 사라지므로 **그대로 두고 문서에 적는 쪽을 권고**한다.
2. 등급 C 의 삭제 후보(`ablation_suite` 구 스택, `ablation_evaluation`, `run_pipeline.py`, `prepare_cited_judge_pilot.py`, `discovery.py`)를 지울지. → 테스트가 붙잡지 않는 것만 삭제. 단 해시 동결 파일 `real_report_evaluation/runner.py`·`extract.py` 가 `discovery.py` → `ablation_evaluation/inputs.py`, `ablation_suite/protocol.py`·`recommendations.py` 를 import 하므로 이 넷과 `ablation_evaluation/metrics.py`(패키지 `__init__` 이 import)는 유지.
3. `full_report_pipeline.run_full_pipeline`·`end_to_end_loop` 를 제품 경로로 남길지. → 유지.

## 6. 실행 결과 (2026-09-21)

브랜치 `cleanup-dead-code-20260921`, 기준 커밋 `da85eb3`. 네 커밋으로 60개 파일, 238줄 추가, 12,206줄 삭제. Python 파일은 189개에서 169개로.

| 단계 | 커밋 | 순 감소 | 확인 |
|---|---|---|---|
| 1 등급 A | 9815f15 | 1,708 | pytest 257/19 동일 구성, pyflakes 미정의 0, validate 360/360/0 |
| 2 Strategy v1~v4 | e084754 | 5,681 | v5 도달 함수 119개 전후 동일. 남은 정의 중 소스 변경은 `run_strategy_agent`·`generate_strategy_report` 둘뿐. one-team 패치 바인딩 동일. LLM 목업 실행으로 산출 파일 11개 바이트 동일, 캐시 지문 동일 |
| 3 Writer | 0599ef3 | 822 | 픽스처 기반 61개 출력 해시 전후 동일, Writer 캐시 지문 불변, sitecustomize 패치 상태 동일 |
| 4 등급 C | e119baf | 3,757 | 삭제만. 해시 동결 두 파일이 protocol.json 과 일치, 유지 체인 import 스모크 통과 |
| 5 문서 | (이 커밋) | — | Strategy·YFinance README 정정, 인계 문서 10절 추가 |

### 남긴 것과 이유

- `agent.py` 의 `from .contracts_v4 import build_strategy_context_package_v4` 는 이제 쓰이지 않지만 import 순서 앵커로 주석과 함께 유지. 검토 결과 sitecustomize 는 모듈 실행 직후 패치하므로 순서와 무관하게 패치본이 바인딩되지만, 제약을 임의로 풀지 않았다. (이름 변경 이후: `build_strategy_context_package_v4` → `build_base_strategy_context`)
- `financial_analysis_agent.py`·`reporting.py` 의 `from openai import OpenAI` 는 `try/except ImportError` 로 의존성 검사 역할이라 유지.
- `writer_agent.py` 의 입력 탐색 폴백(`strategy_decision_output_v5.json` → `_v4` → `_v2`)과 파일명 분기는 기존 산출 디렉터리 호환에 관여하므로 유지.
- `writer_handoff.py` 의 `_selected_date`, `_contrary_evidence`, `_compact_evidence_refs`, `_remove_path_metadata` 는 정리 전부터 호출자 0 이던 v1 유물. 이번 목록 밖이라 남김. 다음 라운드 후보.
- `html_report_writer.py` 에서 `_is_label_free_writer_packet` 이 거짓인 분기(v2 Strategy 결정용)와 `normalize_report_payload` 의 `single_call_llm_with_compact_handoff` 분기는 도달 불가지만 최소 diff 원칙으로 남김. 다음 라운드 후보.
- `tests/test_one_team.py` 러너 테스트 3개의 스킵 조건과 `YFinance_Agent/reporting.py:31` 의 `from valuation import` 잠재 결함은 동작 변경 범위라 손대지 않음.
- `docs/annual_validation.json` 이 삭제된 `run_pipeline.py` 를 언급하지만 과거 검증 기록이라 그대로 둠.

### 3절에서 미룬 것 (논문 뒤 별도 작업)

- `_v2`·`_v4`·`_v5` 접미사 제거와 contracts 모듈 병합
- 중복 유틸 66개 이름 통합
