from __future__ import annotations

import argparse
import copy
import json
import os
import shutil
import time
from datetime import date
from pathlib import Path
from typing import Any, Literal

from tqdm.auto import tqdm

from .io.storage import save_json


Granularity = Literal["day", "month"]


DESCRIPTION = {
    "event_id": "뉴스 이벤트 클러스터 식별자입니다.",
    "mention_count": "동일하거나 유사한 이슈로 묶인 기사 수입니다. 값이 클수록 반복 언급된 이슈입니다.",
    "title": "이벤트를 대표하는 기사 제목입니다.",
    "snippet": "대표 기사에서 추출한 요약 또는 본문 일부입니다. 원문 접근 제한 등으로 비어 있을 수 있습니다.",
    "time": "대표 기사 발행일입니다.",
    "final_score": "DART 관련성, 섹션 관련성, 언급량, 최신성, 중요도 점수를 조합한 최종 랭킹 점수입니다.",
}

COMPANY_PROFILE_SECTION_NAMES = {"사업의 개요", "주요 제품 및 서비스"}

SUMMARY_OUTPUT_DESCRIPTION = {
    "period": "요약 대상 기간입니다. 테스트에서는 일 단위, 운영에서는 월 단위입니다.",
    "period_summary": "해당 기간의 뉴스 흐름을 2~4문장으로 요약한 내용입니다.",
    "issues": "해당 기간의 핵심 이슈 목록입니다.",
    "issue": "핵심 이슈명입니다.",
    "mention_count": "해당 이슈로 병합된 원본 이벤트들의 mention_count 합계입니다.",
    "importance": "high, medium, low 중 하나입니다.",
    "rationale": "해당 이슈가 중요하다고 판단한 간단한 이유입니다.",
}


def _artifact_dirname(company_name: str, collect_date: str) -> str:
    safe_name = company_name.strip()
    safe_name = "".join("_" if ch in '\\/:*?"<>|' else ch for ch in safe_name)
    safe_name = "_".join(part for part in safe_name.split() if part).strip("._")
    return f"{safe_name or 'company'}_{collect_date.replace('-', '')}"


def _period_key(value: str | None, granularity: Granularity) -> str | None:
    if not value:
        return None
    try:
        parsed = date.fromisoformat(value[:10])
    except ValueError:
        return None
    if granularity == "month":
        return parsed.strftime("%Y-%m")
    return parsed.isoformat()


def _compact_event(event: dict[str, Any]) -> dict[str, Any] | None:
    representative = event.get("representative") or {}
    scores = event.get("scores") or {}
    event_id = event.get("event_id")
    time = representative.get("time")
    if not event_id or not time:
        return None
    return {
        "event_id": str(event_id),
        "mention_count": int(event.get("mention_count") or 0),
        "title": str(representative.get("title") or ""),
        "snippet": str(representative.get("snippet") or ""),
        "time": str(time),
        "final_score": float(scores.get("final_score") or 0.0),
    }


def _group_events(
    events: list[dict[str, Any]],
    *,
    granularity: Granularity,
    min_mention_count: int,
) -> dict[str, list[dict[str, Any]]]:
    grouped: dict[str, list[dict[str, Any]]] = {}
    for event in events:
        if int(event.get("mention_count") or 0) < min_mention_count:
            continue
        compact = _compact_event(event)
        if compact is None:
            continue
        period = _period_key(compact["time"], granularity)
        if period is None:
            continue
        grouped.setdefault(period, []).append(compact)

    for period_events in grouped.values():
        period_events.sort(
            key=lambda item: (float(item["final_score"]), int(item["mention_count"]), item["time"]),
            reverse=True,
        )
    return grouped


def _select_periods(grouped: dict[str, list[dict[str, Any]]], period_count: int) -> list[str]:
    periods = sorted(grouped.keys(), reverse=True)
    if period_count > 0:
        periods = periods[:period_count]
    return periods


def _period_payload(period: str, events: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "period": period,
        "event_count": len(events),
        "events": events,
    }


def _load_json_if_exists(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def _load_company_profile(report: dict[str, Any], report_path: Path) -> dict[str, Any]:
    company = report.get("company") or {}
    company_id = str(company.get("company_id") or "")
    corporate_context = report.get("corporate_context") or {}
    report_key = str(corporate_context.get("report_key") or "")
    if not company_id or not report_key:
        return {"source": {}, "sections": []}

    data_dir = report_path.parents[4]
    artifacts_dir = report_path.parents[3]
    metadata_path = data_dir / "inputs" / "dart" / company_id / report_key / "report_metadata.json"
    sections_path = artifacts_dir / "dart" / "sections" / company_id / report_key / "dart_sections.json"

    metadata = _load_json_if_exists(metadata_path)
    sections_payload = _load_json_if_exists(sections_path)
    sections: list[dict[str, str]] = []
    for section in sections_payload.get("sections", []):
        section_name = str(section.get("section_name") or "")
        if section_name not in COMPANY_PROFILE_SECTION_NAMES:
            continue
        sections.append(
            {
                "section_name": section_name,
                "text": str(section.get("raw_text") or ""),
            }
        )

    return {
        "source": {
            "report_name": metadata.get("report_name", ""),
            "report_date": metadata.get("report_date", corporate_context.get("report_date", "")),
            "report_key": report_key,
            "receipt_no": metadata.get("receipt_no", ""),
        },
        "sections": sections,
    }


def _build_llm_summary_request(summary_prompt_input: dict[str, Any], llm_model: str) -> dict[str, Any]:
    expected_output_schema = {
        "description": SUMMARY_OUTPUT_DESCRIPTION,
        "company": summary_prompt_input.get("metadata", {}).get("company", {}),
        "granularity": summary_prompt_input.get("metadata", {}).get("granularity", ""),
        "periods": [
            {
                "period": "YYYY-MM-DD 또는 YYYY-MM",
                "period_summary": "string",
                "issues": [
                    {
                        "issue": "string",
                        "mention_count": 0,
                        "importance": "high | medium | low",
                        "rationale": "string",
                    }
                ],
            }
        ],
    }
    user_payload = {
        "input_description": summary_prompt_input.get("description", {}),
        "company_profile": summary_prompt_input.get("company_profile", {}),
        "metadata": summary_prompt_input.get("metadata", {}),
        "periods": summary_prompt_input.get("periods", []),
        "expected_output_schema": expected_output_schema,
    }
    return {
        "description": {
            "purpose": "LLM 기간별 뉴스 요약 호출 직전에 사용할 입력 payload입니다.",
            "execution_status": "not_executed",
        },
        "model": llm_model,
        "temperature": 0.2,
        "response_format": {"type": "json_object"},
        "messages": [
            {
                "role": "system",
                "content": (
                    "당신은 한국 상장사 뉴스 분석 보조자입니다. "
                    "company_profile과 기간별 뉴스 이벤트를 바탕으로 각 기간의 핵심 이슈를 병합해 요약하세요. "
                    "반드시 유효한 JSON만 출력하고, 입력에 없는 사실을 추가하지 마세요."
                ),
            },
            {
                "role": "user",
                "content": json.dumps(
                    {
                        "instructions": [
                            "period별로 2~4문장의 period_summary를 작성합니다.",
                            "제목과 snippet이 같은 사건을 다루면 하나의 issue로 병합합니다.",
                            "issue별 mention_count는 병합된 원본 이벤트들의 mention_count 합계로 계산합니다.",
                            "각 period의 issues는 최대 5개만 남깁니다.",
                            "importance는 final_score, mention_count, company_profile과의 사업 관련성을 함께 고려해 high/medium/low 중 하나로 지정합니다.",
                            "단순 주가 등락보다 실적, 수요, 공급, 투자, 고객사, 제품/기술, 규제 이슈를 우선합니다.",
                            "출력에는 event_id를 포함하지 않습니다.",
                        ],
                        **user_payload,
                    },
                    ensure_ascii=False,
                ),
            },
        ],
        "expected_output_schema": expected_output_schema,
    }


def _find_user_message_index(request_payload: dict[str, Any]) -> int:
    messages = request_payload.get("messages") or []
    for idx, message in enumerate(messages):
        if message.get("role") == "user":
            return idx
    raise ValueError("LLM request payload does not contain a user message.")


def _load_llm_user_payload(request_payload: dict[str, Any]) -> dict[str, Any]:
    messages = request_payload.get("messages") or []
    user_idx = _find_user_message_index(request_payload)
    content = str(messages[user_idx].get("content") or "")
    try:
        payload = json.loads(content)
    except json.JSONDecodeError as exc:
        raise ValueError("LLM request user message is not valid JSON.") from exc
    if not isinstance(payload, dict):
        raise ValueError("LLM request user message JSON must be an object.")
    return payload


def _build_period_llm_request(request_payload: dict[str, Any], period_payload: dict[str, Any]) -> dict[str, Any]:
    user_payload = _load_llm_user_payload(request_payload)
    period = str(period_payload.get("period") or "")
    if not period:
        raise ValueError("Period payload is missing period.")

    period_user_payload = copy.deepcopy(user_payload)
    metadata = dict(period_user_payload.get("metadata") or {})
    metadata.update(
        {
            "split_by_period": True,
            "current_period": period,
            "period_count": 1,
        }
    )
    period_user_payload["metadata"] = metadata
    period_user_payload["periods"] = [period_payload]

    period_request = copy.deepcopy(request_payload)
    period_request["description"] = {
        **dict(period_request.get("description") or {}),
        "execution_mode": "split_by_period",
        "period": period,
    }
    messages = list(period_request.get("messages") or [])
    user_idx = _find_user_message_index(period_request)
    messages[user_idx] = {
        **dict(messages[user_idx]),
        "content": json.dumps(period_user_payload, ensure_ascii=False),
    }
    period_request["messages"] = messages
    return period_request


def _build_period_llm_requests(request_payload: dict[str, Any]) -> list[tuple[str, dict[str, Any]]]:
    user_payload = _load_llm_user_payload(request_payload)
    periods = user_payload.get("periods") or []
    if not isinstance(periods, list):
        raise ValueError("LLM request user payload must contain a periods list.")

    period_requests: list[tuple[str, dict[str, Any]]] = []
    for period_payload in periods:
        if not isinstance(period_payload, dict):
            continue
        period = str(period_payload.get("period") or "")
        if not period:
            continue
        period_requests.append((period, _build_period_llm_request(request_payload, period_payload)))
    return period_requests


def _write_period_llm_requests(request_payload: dict[str, Any], output_dir: Path) -> list[str]:
    if output_dir.exists():
        shutil.rmtree(output_dir)
    paths: list[str] = []
    for period, period_request in _build_period_llm_requests(request_payload):
        request_path = output_dir / f"{period}.json"
        save_json(period_request, request_path)
        paths.append(str(request_path))
    return paths


def _load_env_file(path: Path) -> None:
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = value


def _load_llm_environment(api_key_env: str, env_path: str | Path | None) -> str:
    if env_path:
        _load_env_file(Path(env_path))
    project_root = Path(__file__).resolve().parents[2]
    _load_env_file(project_root.parent / ".env")
    _load_env_file(project_root / ".env")

    api_key = os.getenv(api_key_env, "").strip()
    if not api_key:
        raise RuntimeError(f"Missing OpenAI API key. Set {api_key_env} or provide --env-path.")
    return api_key


def _build_openai_client(api_key_env: str, env_path: str | Path | None) -> Any:
    api_key = _load_llm_environment(api_key_env, env_path)
    try:
        from openai import OpenAI
    except Exception as exc:
        raise RuntimeError("openai package is required to run LLM summarization.") from exc

    timeout_seconds = float(os.getenv("OPENAI_TIMEOUT_SECONDS", "300"))
    return OpenAI(api_key=api_key, timeout=timeout_seconds)


def _call_llm_summary(client: Any, request_payload: dict[str, Any]) -> dict[str, Any]:
    response = client.chat.completions.create(
        model=str(request_payload["model"]),
        messages=request_payload["messages"],
        temperature=float(request_payload.get("temperature", 0.2)),
        response_format=request_payload.get("response_format", {"type": "json_object"}),
    )
    content = response.choices[0].message.content or ""
    try:
        parsed_output = json.loads(content)
    except json.JSONDecodeError:
        parsed_output = {"raw_content": content, "parse_error": "json_decode_error"}

    usage = None
    if getattr(response, "usage", None) is not None:
        usage_obj = response.usage
        usage = usage_obj.model_dump() if hasattr(usage_obj, "model_dump") else dict(usage_obj)

    return {
        "usage": usage,
        "output": parsed_output,
    }


def _run_llm_summary(
    *,
    request_payload: dict[str, Any],
    output_path: Path,
    api_key_env: str,
    env_path: str | Path | None,
) -> None:
    client = _build_openai_client(api_key_env, env_path)
    print(f"llm_request_start=model:{request_payload['model']} output:{output_path}", flush=True)
    started_at = time.monotonic()
    result = _call_llm_summary(client, request_payload)
    print(f"llm_request_done=elapsed_seconds:{time.monotonic() - started_at:.1f}", flush=True)
    save_json(
        {
            "description": {
                "purpose": "LLM이 생성한 기간별 뉴스 요약 결과입니다.",
                "model": request_payload["model"],
                "execution_mode": "single_request",
            },
            "model": request_payload["model"],
            "usage": result["usage"],
            "output": result["output"],
        },
        output_path,
    )


def _split_summary_payload(request_payload: dict[str, Any], period_results: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "description": {
            "purpose": "LLM이 생성한 기간별 뉴스 요약 결과입니다.",
            "model": request_payload["model"],
            "execution_mode": "split_by_period",
        },
        "model": request_payload["model"],
        "usage": {
            "total": _aggregate_usage(period_results),
            "by_period": [
                {
                    "period": result["period"],
                    "status": result.get("status"),
                    "usage": result.get("usage"),
                }
                for result in period_results
            ],
        },
        "period_results": period_results,
        "output": {
            "periods": [
                result["output"]
                for result in period_results
                if result.get("status") == "success" and "output" in result
            ],
        },
    }


def _extract_period_output(period: str, parsed_output: Any) -> Any:
    if not isinstance(parsed_output, dict):
        return parsed_output
    periods = parsed_output.get("periods")
    if isinstance(periods, list):
        for item in periods:
            if isinstance(item, dict) and str(item.get("period") or "") == period:
                return item
        if periods:
            return periods[0]
    return parsed_output


def _aggregate_usage(period_results: list[dict[str, Any]]) -> dict[str, Any]:
    totals: dict[str, int | float] = {}
    for result in period_results:
        usage = result.get("usage") or {}
        if not isinstance(usage, dict):
            continue
        for key, value in usage.items():
            if isinstance(value, (int, float)):
                totals[key] = totals.get(key, 0) + value
    return totals


def _run_split_llm_summary(
    *,
    request_payload: dict[str, Any],
    output_path: Path,
    api_key_env: str,
    env_path: str | Path | None,
) -> None:
    client = _build_openai_client(api_key_env, env_path)
    period_requests = _build_period_llm_requests(request_payload)
    if not period_requests:
        raise ValueError("No period payloads found for split LLM execution.")

    period_results: list[dict[str, Any]] = []
    print(f"llm_split_start=period_count:{len(period_requests)} output:{output_path}", flush=True)
    progress = tqdm(period_requests, desc="LLM period summaries", unit="period")
    for idx, (period, period_request) in enumerate(progress, start=1):
        started_at = time.monotonic()
        progress.set_postfix_str(str(period))
        print(f"llm_period_start={idx}/{len(period_requests)} period:{period}", flush=True)
        result = _call_llm_summary(client, period_request)
        period_results.append(
            {
                "period": period,
                "status": "success",
                "usage": result["usage"],
                "output": _extract_period_output(period, result["output"]),
            }
        )
        save_json(_split_summary_payload(request_payload, period_results), output_path)
        print(f"llm_period_done={idx}/{len(period_requests)} period:{period} elapsed_seconds:{time.monotonic() - started_at:.1f}", flush=True)

    print(f"llm_split_done=period_count:{len(period_results)}", flush=True)


def execute_llm_summary_request(
    *,
    llm_request_path: str | Path,
    output_path: str | Path | None = None,
    api_key_env: str = "OPENAI_API_KEY",
    env_path: str | Path | None = None,
    split_by_period: bool = False,
) -> str:
    request_path = Path(llm_request_path)
    request_payload = json.loads(request_path.read_text(encoding="utf-8"))
    result_path = Path(output_path) if output_path else request_path.with_name("llm_period_summaries.json")
    if split_by_period:
        _run_split_llm_summary(
            request_payload=request_payload,
            output_path=result_path,
            api_key_env=api_key_env,
            env_path=env_path,
        )
    else:
        _run_llm_summary(
            request_payload=request_payload,
            output_path=result_path,
            api_key_env=api_key_env,
            env_path=env_path,
        )
    return str(result_path)


def build_context_exports(
    *,
    report_context_path: str | Path,
    output_dir: str | Path | None = None,
    granularity: Granularity = "day",
    period_count: int = 12,
    raw_period_count: int = 3,
    min_mention_count: int = 3,
    llm_model: str = "gpt-5.4-mini",
    run_llm: bool = False,
    split_by_period: bool = False,
    api_key_env: str = "OPENAI_API_KEY",
    env_path: str | Path | None = None,
) -> dict[str, str]:
    report_path = Path(report_context_path)
    report = json.loads(report_path.read_text(encoding="utf-8"))
    company_name = str((report.get("company") or {}).get("company_name") or "company")
    collect_date = str(report.get("collect_date") or "unknown")
    company_profile = _load_company_profile(report, report_path)

    grouped = _group_events(
        list(report.get("news_events_topk") or []),
        granularity=granularity,
        min_mention_count=min_mention_count,
    )
    selected_periods = _select_periods(grouped, period_count)
    raw_periods = selected_periods[: max(raw_period_count, 0)]
    summary_periods_for_news_agent = selected_periods[max(raw_period_count, 0) :]

    if output_dir is None:
        output_dir = (
            report_path.parents[3]
            / "context_exports"
            / _artifact_dirname(company_name, collect_date)
            / granularity
        )
    output_path = Path(output_dir)

    base_metadata = {
        "source_report_context": str(report_path),
        "company": report.get("company") or {},
        "collect_date": collect_date,
        "granularity": granularity,
        "period_count": period_count,
        "raw_period_count": raw_period_count,
        "min_mention_count": min_mention_count,
        "filter_rule": f"mention_count >= {min_mention_count}",
    }

    summary_prompt_input = {
        "description": DESCRIPTION,
        "company_profile": company_profile,
        "metadata": base_metadata,
        "task": "각 period의 events를 바탕으로 해당 기간의 핵심 뉴스 흐름을 요약합니다.",
        "periods": [_period_payload(period, grouped[period]) for period in selected_periods],
    }

    recent_raw_input = {
        "description": DESCRIPTION,
        "company_profile": company_profile,
        "metadata": {
            **base_metadata,
            "usage": "뉴스 에이전트에 제공할 최신 원문 이벤트 입력입니다.",
        },
        "periods": [_period_payload(period, grouped[period]) for period in raw_periods],
    }

    summary_path = output_path / "summary_prompt_input.json"
    llm_request_path = output_path / "llm_summary_request.json"
    llm_output_path = output_path / "llm_period_summaries.json"
    period_requests_dir = output_path / "period_requests"
    raw_path = output_path / "recent_raw_input.json"
    manifest_path = output_path / "context_export_manifest.json"
    llm_request = _build_llm_summary_request(summary_prompt_input, llm_model)
    period_request_paths = _write_period_llm_requests(llm_request, period_requests_dir) if split_by_period else []

    manifest = {
        "description": {
            "summary_prompt_input_path": "12개월 운영 또는 12일 테스트에서 LLM 기간별 요약을 만들기 위한 입력 파일입니다.",
            "llm_summary_request_path": "summary_prompt_input.json을 기반으로 만든 LLM 호출 직전 messages payload입니다.",
            "llm_period_summaries_path": "LLM 실행 결과입니다. --run-llm을 지정한 경우에만 생성됩니다.",
            "period_requests_dir": "--split-by-period 지정 시 period별 LLM request 파일이 저장되는 디렉터리입니다.",
            "recent_raw_input_path": "뉴스 에이전트에 원문으로 제공할 최신 기간 입력 파일입니다.",
            "summary_periods_for_news_agent": "뉴스 에이전트에 요약본으로 제공할 과거 기간입니다.",
            "raw_periods_for_news_agent": "뉴스 에이전트에 원문 이벤트로 제공할 최신 기간입니다.",
        },
        "metadata": base_metadata,
        "selected_periods": selected_periods,
        "summary_periods_for_news_agent": summary_periods_for_news_agent,
        "raw_periods_for_news_agent": raw_periods,
        "total_events_after_filter": sum(len(grouped[period]) for period in selected_periods),
        "llm": {
            "model": llm_model,
            "run_llm": run_llm,
            "execution_mode": "split_by_period" if split_by_period else "single_request",
            "split_by_period": split_by_period,
            "api_key_env": api_key_env,
        },
        "output_paths": {
            "summary_prompt_input_path": str(summary_path),
            "llm_summary_request_path": str(llm_request_path),
            "llm_period_summaries_path": str(llm_output_path),
            "period_requests_dir": str(period_requests_dir),
            "period_request_paths": period_request_paths,
            "recent_raw_input_path": str(raw_path),
            "manifest_path": str(manifest_path),
        },
    }

    save_json(summary_prompt_input, summary_path)
    save_json(llm_request, llm_request_path)
    save_json(recent_raw_input, raw_path)
    if run_llm:
        if split_by_period:
            _run_split_llm_summary(
                request_payload=llm_request,
                output_path=llm_output_path,
                api_key_env=api_key_env,
                env_path=env_path,
            )
        else:
            _run_llm_summary(
                request_payload=llm_request,
                output_path=llm_output_path,
                api_key_env=api_key_env,
                env_path=env_path,
            )
    save_json(manifest, manifest_path)

    return {
        "summary_prompt_input_path": str(summary_path),
        "llm_summary_request_path": str(llm_request_path),
        "llm_period_summaries_path": str(llm_output_path),
        "period_requests_dir": str(period_requests_dir),
        "recent_raw_input_path": str(raw_path),
        "manifest_path": str(manifest_path),
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Build compact context exports from report_context.json")
    parser.add_argument("--report-context", default=None, help="Path to report_context.json")
    parser.add_argument("--llm-request", default=None, help="Run an existing llm_summary_request.json without rebuilding exports")
    parser.add_argument("--llm-output", default=None, help="Output path for --llm-request. Defaults to llm_period_summaries.json")
    parser.add_argument("--output-dir", default=None, help="Output directory. Defaults to data/artifacts/context_exports/...")
    parser.add_argument("--granularity", choices=["day", "month"], default="day")
    parser.add_argument("--period-count", type=int, default=12)
    parser.add_argument("--raw-period-count", type=int, default=3)
    parser.add_argument("--min-mention-count", type=int, default=3)
    parser.add_argument("--llm-model", default="gpt-5.4-mini", help="LLM model for --run-llm")
    parser.add_argument("--run-llm", action="store_true", help="Call OpenAI and save llm_period_summaries.json")
    parser.add_argument(
        "--split-by-period",
        action="store_true",
        help="Call LLM once per period and merge results into one llm_period_summaries.json",
    )
    parser.add_argument("--api-key-env", default="OPENAI_API_KEY", help="Environment variable containing OpenAI API key")
    parser.add_argument("--env-path", default=None, help="Optional .env path. Defaults also check /home/agent2/SY/.env and News/.env")
    return parser


def main() -> None:
    args = build_parser().parse_args()
    if args.llm_request:
        path = execute_llm_summary_request(
            llm_request_path=args.llm_request,
            output_path=args.llm_output,
            api_key_env=args.api_key_env,
            env_path=args.env_path,
            split_by_period=args.split_by_period,
        )
        print(f"llm_period_summaries_path={path}")
        return
    if not args.report_context:
        raise SystemExit("--report-context is required unless --llm-request is provided.")
    paths = build_context_exports(
        report_context_path=args.report_context,
        output_dir=args.output_dir,
        granularity=args.granularity,
        period_count=args.period_count,
        raw_period_count=args.raw_period_count,
        min_mention_count=args.min_mention_count,
        llm_model=args.llm_model,
        run_llm=args.run_llm,
        split_by_period=args.split_by_period,
        api_key_env=args.api_key_env,
        env_path=args.env_path,
    )
    for key, value in paths.items():
        print(f"{key}={value}")


if __name__ == "__main__":
    main()
