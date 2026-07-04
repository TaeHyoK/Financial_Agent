# Financial Agent Final

통합 하위 에이전트 레포입니다. 현재 News Agent 파트가 이식되어 있습니다.

## News Agent

코드 위치:

```text
src/Agent_Team/News_Agent
```

샘플 output 위치:

```text
Output_total/News/SK바이오팜_20251031/output
```

설치 없이 가볍게 CLI를 확인하려면:

```bash
PYTHONPATH=src python -m Agent_Team.News_Agent.cli --help
PYTHONPATH=src python -m Agent_Team.News_Agent.sy_agent_cli --help
```

전체 실행 기본 순서:

```text
collect -> export -> llm -> analysis -> sy
```

News Agent와 News SY Agent는 최종 투자 의견을 생성하지 않습니다.
