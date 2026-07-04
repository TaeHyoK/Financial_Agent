파이프라인 순서

[Input Specialist Output]
      ↓
[Claim Extraction Node]
      ↓
[SY Question 1 Node]
      ↓
[Specialist Answer 1 Node]
      ↓
[SY Question 2 Node]
      ↓
[Specialist Answer 2 Node]
      ↓
[SY Deletion / Revision Decision Node]
      ↓
[Specialist Final Rewrite Node]
      ↓
[Verified Handoff Output]

참고할 sy_agent: 
/home/agent2/SY/YFinance/sy_agent.py,
/home/agent2/SY/DART/SY Agent/langgraph_flow.py


News Agent handoff 기준 key 명칭

대상 입력 파일:
/home/agent2/SY/News/data/artifacts/context_exports/SK바이오팜_20251031/output/news_agent_handoff.json

최상위 payload는 JSON의 output 블록을 기준으로 한다.

top-level keys:
- agent_name
- output_version
- output_mode
- target_entity
- input_summary
- analysis_blocks
- evidence_map_path

target_entity keys:
- company_name
- ticker
- corp_code
- as_of_date

analysis_blocks keys:
- news_only
- news_plus_financial
- news_plus_market
- news_plus_financial_plus_market

news_only keys:
- summary
- positive_signals
- negative_signals
- key_risks
- uncertainties

news_only의 positive_signals, negative_signals, key_risks, uncertainties는 string list로 처리한다.
SY Agent claim 추출 시 아래 표준 claim 필드로 변환한다.

standard claim fields:
- claim_id
- section
- claim_type
- claim
- source_block
- source_key
- source_index
- original_item

news_only claim mapping:
- analysis_blocks.news_only.summary
  - claim_type: summary
  - source_block: news_only
  - source_key: summary
- analysis_blocks.news_only.positive_signals[i]
  - claim_type: positive_signal
  - source_block: news_only
  - source_key: positive_signals
- analysis_blocks.news_only.negative_signals[i]
  - claim_type: negative_signal
  - source_block: news_only
  - source_key: negative_signals
- analysis_blocks.news_only.key_risks[i]
  - claim_type: key_risk
  - source_block: news_only
  - source_key: key_risks
- analysis_blocks.news_only.uncertainties[i]
  - claim_type: uncertainty
  - source_block: news_only
  - source_key: uncertainties

news_plus_financial keys:
- summary
- cross_points
- conflicting_points
- financial_context_limits

news_plus_financial의 cross_points와 conflicting_points는 object list로 처리한다.
각 object의 표준 key:
- point
- cross_analysis
- interpretation_limit

news_plus_financial claim mapping:
- analysis_blocks.news_plus_financial.summary
  - claim_type: summary
  - source_block: news_plus_financial
  - source_key: summary
- analysis_blocks.news_plus_financial.cross_points[i]
  - claim_type: cross_point
  - source_block: news_plus_financial
  - source_key: cross_points
  - claim: point
  - reasoning: cross_analysis
  - limitation: interpretation_limit
- analysis_blocks.news_plus_financial.conflicting_points[i]
  - claim_type: conflicting_point
  - source_block: news_plus_financial
  - source_key: conflicting_points
  - claim: point
  - reasoning: cross_analysis
  - limitation: interpretation_limit
- analysis_blocks.news_plus_financial.financial_context_limits[i]
  - claim_type: context_limit
  - source_block: news_plus_financial
  - source_key: financial_context_limits
  - claim: limit

news_plus_market keys:
- summary
- reaction_points
- divergences

news_plus_market의 reaction_points와 divergences는 object list로 처리한다.
각 object의 표준 key:
- point
- cross_analysis
- reaction_interpretation

news_plus_market claim mapping:
- analysis_blocks.news_plus_market.summary
  - claim_type: summary
  - source_block: news_plus_market
  - source_key: summary
- analysis_blocks.news_plus_market.reaction_points[i]
  - claim_type: reaction_point
  - source_block: news_plus_market
  - source_key: reaction_points
  - claim: point
  - reasoning: cross_analysis
  - interpretation: reaction_interpretation
- analysis_blocks.news_plus_market.divergences[i]
  - claim_type: divergence
  - source_block: news_plus_market
  - source_key: divergences
  - claim: point
  - reasoning: cross_analysis
  - interpretation: reaction_interpretation

news_plus_financial_plus_market keys:
- summary
- integrated_signals
- integrated_risks
- handoff_notes

news_plus_financial_plus_market의 integrated_signals, integrated_risks, handoff_notes는 string list로 처리한다.

news_plus_financial_plus_market claim mapping:
- analysis_blocks.news_plus_financial_plus_market.summary
  - claim_type: summary
  - source_block: news_plus_financial_plus_market
  - source_key: summary
- analysis_blocks.news_plus_financial_plus_market.integrated_signals[i]
  - claim_type: integrated_signal
  - source_block: news_plus_financial_plus_market
  - source_key: integrated_signals
- analysis_blocks.news_plus_financial_plus_market.integrated_risks[i]
  - claim_type: integrated_risk
  - source_block: news_plus_financial_plus_market
  - source_key: integrated_risks
- analysis_blocks.news_plus_financial_plus_market.handoff_notes[i]
  - claim_type: handoff_note
  - source_block: news_plus_financial_plus_market
  - source_key: handoff_notes

SY Agent 질문 템플릿은 source_block과 source_key 기준으로 작성한다.

질문 템플릿 key:
- source_block
- source_key
- claim_type
- question

권장 질문 템플릿:
- source_block=news_only
  - claim_type=positive_signal 질문: 이 항목을 긍정 신호로 본 이유를 설명하라. 과거 월별 요약과 최신 raw 뉴스 중 어떤 근거가 이 신호를 뒷받침하는지 구분하라.
  - claim_type=negative_signal 또는 key_risk 질문: 이 항목을 부정 신호 또는 핵심 리스크로 본 이유를 설명하라. 실제 뉴스 근거와 아직 불확실한 해석을 구분하라.
  - claim_type=uncertainty 질문: 이 항목을 불확실성으로 분류한 이유를 설명하라. 확정된 사실과 추가 관찰이 필요한 부분을 구분하라.
  - 그 외 질문: 이 뉴스 요약이 전체 뉴스 흐름을 균형 있게 반영하는지 설명하라. 긍정 신호, 리스크, 불확실성을 구분하라.
- source_block=news_plus_financial
  - 질문: 이 주장에서 연결된 뉴스 이벤트와 DART 재무지표를 각각 특정하라. 두 근거가 같은 방향인지, 괴리인지, 단순 병렬 나열인지 구분하고 재무제표 단독 해석이면 한계를 인정하라.
- source_block=news_plus_market
  - 질문: 이 주장에서 연결된 뉴스 이벤트와 시장 지표를 각각 특정하라. 주가 수익률, 초과수익률, 거래량, 상대강도 중 어떤 지표가 뉴스 반응을 뒷받침하거나 반박하는지 설명하라.
- source_block=news_plus_financial_plus_market
  - 질문: 이 통합 해석에서 뉴스, 재무, 시장 세 도메인의 근거를 각각 특정하라. 세 도메인 중 누락된 근거가 있으면 supported가 아니라 약화 또는 보류해야 하는 이유를 설명하라.

주의사항:
- news_agent_handoff.json에는 evidence_ids를 포함하지 않는다.
- 상세 근거 확인이 필요할 때만 evidence_map_path의 JSON을 로드한다.
- SY Agent의 evidence_used에는 evidence_map_path의 JSON에 실제 존재하는 evidence id만 기록한다.
  - 허용 예: NEWS_SUMMARY_2025-02, NEWS_RAW_2025-09_1018, DART_REVENUE, YF_STOCK_RETURN_20D
  - 허용하지 않음: news_only.summary, news_plus_market.summary 같은 handoff 내부 section path
- claim_validations에는 다음 검증 필드를 포함한다.
  - required_evidence_domains
  - declared_evidence_ids
  - evidence_ids_used
  - evidence_domain_coverage
  - missing_evidence_domains
  - invalid_evidence_ids
  - question_round_1
  - answer_round_1_summary
  - answer_round_2_summary
- source_block별 필수 evidence domain:
  - news_only: news
  - news_plus_financial: news, financial
  - news_plus_market: news, market
  - news_plus_financial_plus_market: news, financial, market
- 필수 evidence domain, invalid evidence id, 유효 evidence id 부재 여부는 LLM 평가 입력으로 제공한다.
- 최종 supported/keep, weakly_supported/revise, unsupported/hallucination_candidate, contradicted/remove 판단은 LLM evaluator가 생성한다.
- SY Agent의 출력에서도 buy/sell/hold, 목표주가, 투자판단은 생성하지 않는다.
- News Agent의 역할은 상위 레이어 입력용 분석이며 최종 투자 판단이 아니다.
