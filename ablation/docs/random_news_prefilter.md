# Random news 조건

Full은 기업 필터 후 공시 섹션별 임베딩 유사도로 뉴스를 선정한다. Random news는 기업 필터 전 중복 처리 후보 집합에서 무작위로 표본을 추출하여 기업 필터와 관련성 선정의 효과를 함께 제거한다.

주간·월간 기사 수는 Full과 맞춘다. 표본 시드는 20251031이며, 선정 결과와 후보 집합의 해시를 저장한다. 후보 스니펫은 선정 전에 공통으로 확보하고, 선택 후에만 원문을 추가 수집하지 않는다. 두 조건은 같은 후속 분석 프롬프트·모델을 사용한다.

Random의 월별 요약은 Random이 선택한 기사로 만든다. Full 요약을 복사하지 않는다. 기사 수를 맞추더라도 기사 길이와 토큰 수가 동일하다는 뜻은 아니다.

표본 추출 구현은 `ablation/ablation_suite/annual_random.py`, 입력 준비 실행기는 `ablation/run_config/prepare_condition_inputs.py`다. [전체 실험 정의](ABLATION.md)를 참고한다.
