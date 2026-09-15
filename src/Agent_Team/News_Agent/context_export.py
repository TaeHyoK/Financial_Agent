from __future__ import annotations

import argparse
import hashlib
import copy
import difflib
import json
import os
import re
import shutil
import time
from datetime import date, timedelta
from pathlib import Path
from typing import Any, Literal

from tqdm.auto import tqdm
from shared.llm_clients import execute_with_telemetry
from shared.time_windows import monthly_windows
from shared.news_selection import MONTHLY_NEWS_POLICY, MONTHLY_NEWS_LABEL, ANNUAL_NEWS_LIMIT, validate_monthly_news

from .io.storage import save_json


Granularity = Literal["day", "week", "month"]
NEWS_AGENT_TOP_K = 20


DESCRIPTION = {
    "event_id": "뉴스 이벤트 클러스터 식별자입니다.",
    "mention_count": "동일하거나 유사한 이슈로 묶인 기사 수입니다. 값이 클수록 반복 언급된 이슈입니다.",
    "title": "이벤트를 대표하는 기사 제목입니다.",
    "snippet": "대표 기사에서 추출한 요약 또는 본문 일부입니다. 원문 접근 제한 등으로 비어 있을 수 있습니다.",
    "time": "대표 기사 발행일입니다.",
    "event_timeline": "여러 날짜에 걸쳐 보도된 사건의 진행 내역입니다. 날짜별로 기업보고서와 가장 관련성이 높은 기사 제목 1건만 포함합니다.",
    "final_score": "DART 관련성, 섹션 관련성, 언급량, 최신성, 중요도 점수를 조합한 최종 랭킹 점수입니다.",
    "coverage": "기사 수, 고유 매체 수, 근접 복제 제거 후 기사 수와 1차 출처 포함 여부입니다.",
}

SUMMARY_OUTPUT_DESCRIPTION = {
    "period": "요약 대상 기간입니다. 운영 기본값은 기준일에 맞춘 월 구간 12개입니다.",
    "issues": "서로 구분되는 사건별 요약 목록입니다. 재무·시장 분석의 보조자료로 전달됩니다.",
    "summary": "사건의 주체·날짜·실적 대상 기간·수치·진행 상태를 보존한 설명입니다.",
    "source_event_ids": "이 설명의 근거로 실제 사용한 해당 기간 입력 event_id의 문자열 목록입니다.",
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
    if granularity == "week":
        iso_year, iso_week, _ = parsed.isocalendar()
        return f"{iso_year:04d}-W{iso_week:02d}"
    return parsed.isoformat()


def _compact_event(event: dict[str, Any]) -> dict[str, Any] | None:
    representative = event.get("representative") or {}
    scores = event.get("scores") or {}
    event_id = event.get("event_id")
    time = representative.get("time")
    if not event_id or not time:
        return None
    compact = {
        "event_id": str(event_id),
        "relevance_rank": int(event.get("relevance_rank") or 0),
        "mention_count": int(event.get("mention_count") or 0),
        "title": str(representative.get("title") or ""),
        "snippet": str(representative.get("snippet") or ""),
        "source": str(representative.get("source") or ""),
        "time": str(time),
        "final_score": float(scores.get("final_score") or 0.0),
        "coverage": _event_coverage(event),
    }
    event_timeline = [
        {
            "date": str(item.get("date") or ""),
            "title": str(item.get("title") or ""),
        }
        for item in event.get("event_timeline") or []
        if isinstance(item, dict)
        and str(item.get("date") or "").strip()
        and str(item.get("title") or "").strip()
    ]
    if len(event_timeline) > 1:
        compact["event_timeline"] = sorted(
            event_timeline,
            key=lambda item: (item["date"], item["title"]),
        )
    return compact


def _event_coverage(event: dict[str, Any]) -> dict[str, Any]:
    articles = [item for item in event.get("articles") or [] if isinstance(item, dict)]
    article_count = len(articles) or int(event.get("mention_count") or 0)
    publishers = {
        _normalize_publisher(item.get("source"))
        for item in articles
        if _normalize_publisher(item.get("source"))
    }
    deduplicated_count = _deduplicated_article_count(articles) if articles else article_count
    primary_source_present = any(_is_primary_source(item) for item in articles)
    if articles and all(str(item.get("title") or "").strip() for item in articles):
        quality = "verified"
    elif article_count:
        quality = "partial"
    else:
        quality = "insufficient"
    return {
        "article_count": article_count,
        "unique_publisher_count": len(publishers),
        "publisher_names": sorted(publishers),
        "deduplicated_article_count": deduplicated_count,
        "primary_source_present": primary_source_present,
        "coverage_quality": quality,
    }


def _deduplicated_article_count(articles: list[dict[str, Any]]) -> int:
    representatives: list[str] = []
    for article in articles:
        normalized = _normalize_article_text(article)
        if not normalized:
            continue
        if any(difflib.SequenceMatcher(None, normalized, prior).ratio() >= 0.88 for prior in representatives):
            continue
        representatives.append(normalized)
    return len(representatives)


def _normalize_article_text(article: dict[str, Any]) -> str:
    text = f"{article.get('title') or ''} {article.get('snippet') or ''}".lower()
    return re.sub(r"[^0-9a-z가-힣]+", " ", text).strip()


def _normalize_publisher(value: Any) -> str:
    return re.sub(r"\s+", "", str(value or "")).lower()


def _is_primary_source(article: dict[str, Any]) -> bool:
    source = _normalize_publisher(article.get("source"))
    url = str(article.get("url") or "").lower()
    return any(
        marker in source or marker in url
        for marker in ("dart", "전자공시", "보도자료", "newsroom", "/ir/", "/press/")
    )


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


def _select_summary_periods(
    *,
    grouped: dict[str, list[dict[str, Any]]],
    collect_date: str,
    granularity: Granularity,
    period_count: int,
) -> list[str]:
    """Return the requested calendar window, including periods without news."""

    if granularity not in {"day", "week"} or period_count <= 0:
        return _select_periods(grouped, period_count)
    try:
        end_date = date.fromisoformat(collect_date[:10])
    except ValueError:
        return _select_periods(grouped, period_count)
    if granularity == "day":
        return [
            (end_date - timedelta(days=offset)).isoformat()
            for offset in range(period_count)
        ]
    periods: list[str] = []
    for offset in range(period_count):
        current = end_date - timedelta(weeks=offset)
        iso_year, iso_week, _ = current.isocalendar()
        periods.append(f"{iso_year:04d}-W{iso_week:02d}")
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
    """Build a compact relevance profile from already-ranked DART context chunks."""

    company = report.get("company") or {}
    corporate_context = report.get("corporate_context") or {}
    chunks = corporate_context.get("chunks_by_section") or {}
    overview = [
        str(item.get("text") or "")
        for item in (chunks.get("overview") or [])[:3]
        if isinstance(item, dict) and str(item.get("text") or "").strip()
    ]
    product_names: list[str] = []
    for item in chunks.get("products") or []:
        if not isinstance(item, dict):
            continue
        for line in str(item.get("text") or "").splitlines():
            cells = [cell.strip() for cell in line.strip().strip("|").split("|")]
            if len(cells) < 3 or not any("%" in cell for cell in cells[1:]):
                continue
            name = cells[0]
            if name and name not in {"품목", "매출액", "기타", "계", "---"} and name not in product_names:
                product_names.append(name)
        if len(product_names) >= 8:
            break
    return {
        "company_name": company.get("company_name", ""),
        "ksic_code": company.get("ksic_code") or [],
        "report_date": corporate_context.get("report_date", ""),
        "business_context": overview,
        "major_products": product_names,
    }


def _build_llm_summary_request(summary_prompt_input: dict[str, Any], llm_model: str) -> dict[str, Any]:
    expected_output_schema = {
        "description": SUMMARY_OUTPUT_DESCRIPTION,
        "company": summary_prompt_input.get("metadata", {}).get("company", {}),
        "granularity": summary_prompt_input.get("metadata", {}).get("granularity", ""),
        "periods": [
            {
                "period": "copy the exact input period key",
                "issues": [
                    {
                        "summary": "string",
                        "source_event_ids": ["copy an input event_id from this period"],
                    }
                ],
            }
        ],
    }
    metadata = summary_prompt_input.get("metadata", {})
    user_payload = {
        "company_profile": summary_prompt_input.get("company_profile", {}),
        "target": {
            "company": metadata.get("company", {}),
            "collect_date": metadata.get("collect_date", ""),
            "granularity": metadata.get("granularity", ""),
        },
        # Ranking is an upstream selection mechanism, not an instruction about
        # which facts the summary model should consider important.
        # Detailed coverage and counts stay in source artifacts, not the summary request.
        "periods": [
            {**{key: value for key, value in period.items() if key != "event_count"}, "events": [
                {key: value for key, value in event.items()
                 if key not in {"relevance_rank", "final_score", "scores", "ablation_selection", "coverage", "mention_count"}}
                for event in sorted(period.get("events", []), key=lambda row: (str(row.get("time") or ""), str(row.get("event_id") or "")))
            ]}
            for period in summary_prompt_input.get("periods", [])
        ],
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
                    "company_profile과 기간별 뉴스 이벤트를 바탕으로 독립 사건을 구분해 요약하고 실제 사용한 기사 ID를 기록하세요. "
                    "반드시 유효한 JSON만 출력하고, 입력에 없는 사실을 추가하지 마세요."
                ),
            },
            {
                "role": "user",
                "content": json.dumps(
                    {
                        "instructions": [
                            "각 period의 issues에 사건별 summary와 source_event_ids를 작성합니다. 별도의 월 전체 서술을 반복하지 않습니다. 각 summary는 다른 항목 없이도 이해할 수 있게 사실·시점·금액·진행 상태를 보존합니다. 출처에 없는 전망이나 인과관계를 만들지 않습니다.",
                            "입력의 모든 period를 정확히 한 번씩 반환합니다. events가 비어 있거나 근거로 사용할 수 있는 사건이 없으면 issues는 빈 배열로 둡니다. 관행적인 한계 문구로 사건 설명을 대신하지 않습니다.",
                            "같은 발표나 사건의 반복 보도만 하나의 issue로 정리합니다. 같은 회사·제품이라는 이유만으로 계약·허가·출시·실적·안전성 사건을 합치지 않습니다. 연결된 사건은 시간순으로 구분합니다.",
                            "event_timeline이 있으면 날짜별 제목을 시간순 사건 진행으로 반영하되, 제목에 없는 변화나 인과관계를 추가하지 않습니다.",
                            "issues의 항목 수와 글자 수에 고정 제한은 없습니다. 분석에 필요한 독립 사건은 보존하되 반복 보도와 비핵심 설명은 줄입니다. 기사 발행일, 사건 발생일, 실적 대상 기간을 구분하고 월 구간을 사건 날짜로 해석하지 않습니다.",
                            "각 실적 문장에는 주체를 원문에 명시된 기업명으로 적습니다. 수치 바로 앞에 해당 기업명을 명시하고 앞 문장의 기업명이나 문장 뒤의 '그룹 기여' 표현으로 대신하지 않습니다. 계약·투자도 당사자를 보존합니다. 대상기업·지주회사·그룹·계열사의 수치와 연결·별도 기준을 바꾸지 않습니다. 제목에서 기업명이 축약됐더라도 스니펫에 수치의 주체가 명시되어 있으면 이를 따릅니다. 기업명이 비슷하다는 이유로 같은 기업으로 취급하거나 입력에 없는 지배관계를 추정하지 않습니다.",
                            "원문에 대상기업의 사업 변화가 그룹 실적에 기여했다고 명시되어 있으면 그 변화와 기여를 사실 중심으로 설명합니다. 그룹 실적만으로 대상기업의 성장이나 기여 원인을 추정하지 않습니다. 확인된 기여를 '대상기업 실적으로 사용할 수 없다' 같은 검토 문구로 대체하지 않으며, 주체가 불분명한 수치는 임의 귀속하지 않고 확인 가능한 사건과 의미를 남깁니다. 산업 사건도 확인되는 연결 범위에서 활용합니다.",
                            "언론사 소개·서비스 안내·주변 기사 문구는 사건의 근거가 아니며, 유효한 내용이 없으면 사실을 보충하지 않습니다.",
                            "단순 주가 등락보다 실적, 수요, 공급, 투자, 고객사, 제품/기술, 규제 이슈를 우선합니다.",
                            "source_event_ids에는 해당 summary의 사실을 뒷받침하는 같은 period의 event_id만 입력 그대로 복사합니다. 실제 사용한 근거는 개수 제한 없이 남기되, 읽었다는 이유만으로 해당 월의 기사 전체를 붙이지 않습니다. 같은 기사가 서로 다른 사건을 뒷받침하면 여러 항목에서 인용할 수 있으며, 같은 ID를 인용했다는 이유로 사건을 합치지 않습니다.",
                        ],
                        **user_payload,
                    },
                    ensure_ascii=False,
                ),
            },
        ],
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
        safe_period = period.replace("/", "_")
        request_path = output_dir / f"{safe_period}.json"
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
    project_root = Path(__file__).resolve().parents[3]
    _load_env_file(project_root / "configs" / ".env")
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
    model = str(request_payload["model"])
    transport_payload = {
        "model": model,
        "messages": request_payload["messages"],
        "temperature": float(request_payload.get("temperature", 0.2)),
        "response_format": request_payload.get("response_format", {"type": "json_object"}),
    }
    response = execute_with_telemetry(
        lambda: client.chat.completions.create(**transport_payload),
        request_payload=transport_payload,
        model=model,
        step="news:period_summary",
        usage_getter=lambda result: getattr(result, "usage", None),
    )
    content = response.choices[0].message.content or ""
    try:
        parsed_output = json.loads(content)
    except json.JSONDecodeError:
        parsed_output = {"raw_content": content, "parse_error": "json_decode_error"}
    _attach_source_event_ids(parsed_output, request_payload)

    usage = None
    if getattr(response, "usage", None) is not None:
        usage_obj = response.usage
        usage = usage_obj.model_dump() if hasattr(usage_obj, "model_dump") else dict(usage_obj)

    return {
        "usage": usage,
        "output": parsed_output,
    }


def _attach_source_event_ids(
    parsed_output: Any,
    request_payload: dict[str, Any],
) -> None:
    """Validate cited IDs; keep input provenance separate from model-used sources.

    Checks only structure and source membership, not importance or semantic support.
    """

    if not isinstance(parsed_output, dict):
        raise ValueError("News summary must be a JSON object")
    input_payload = _load_llm_user_payload(request_payload)
    source_periods = {p["period"]: p for p in input_payload.get("periods") or []}
    output_periods = parsed_output.get("periods")
    if not isinstance(output_periods, list):
        raise ValueError("News summary must contain a periods list")
    for period in output_periods:
        if not isinstance(period, dict):
            raise ValueError("News summary period must be an object")
        period_key = str(period.get("period") or "")
        if period_key not in source_periods:
            raise ValueError(f"Unknown summary period: {period_key}")
        input_ids = [str(event['event_id']) for event in source_periods[period_key].get('events', [])]
        issues = period.get('issues')
        if not isinstance(issues, list):
            raise ValueError(f"News summary must contain an issues list: {period_key}")
        used_ids = []
        for issue in issues:
            if not isinstance(issue, dict) or not isinstance(issue.get('summary'), str) or not issue['summary'].strip():
                raise ValueError(f"Empty news issue summary: {period_key}")
            ids = issue.get('source_event_ids')
            if not isinstance(ids, list) or not ids or any(not isinstance(key, str) or key not in input_ids for key in ids):
                raise ValueError(f"News issue cites missing or out-of-period source IDs: {period_key}")
            used_ids.extend(ids)
        period['input_event_ids'] = input_ids
        period['source_event_ids'] = list(dict.fromkeys(used_ids))
        for key in ("period_start", "period_end"):
            if key in source_periods[period_key]:
                period[key] = source_periods[period_key][key]
    actual = [p.get("period") for p in output_periods if isinstance(p, dict)]
    if len(actual) != len(set(actual)) or set(actual) != set(source_periods):
        raise ValueError("News summaries must cover every requested period exactly once.")


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
            "source_request_sha256": summary_request_hash(request_payload),
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
        "source_request_sha256": summary_request_hash(request_payload),
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
    from shared.news_articles import ARTICLE_NEWS_POLICY
    manifest = _load_json_if_exists(request_path.parent / "context_export_manifest.json")
    if ((manifest.get("metadata") or {}).get("raw_news_policy") == ARTICLE_NEWS_POLICY
            and (manifest.get("metadata") or {}).get("news_subdata_policy") != "monthly_summary_v1"):
        raise ValueError("Article-only export must not execute a stale summary request")
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


def summary_request_hash(request: dict[str, Any]) -> str:
    return hashlib.sha256(json.dumps(
        {key: request.get(key) for key in ("model", "messages", "temperature", "response_format")},
        ensure_ascii=False, sort_keys=True,
    ).encode()).hexdigest()


def article_summary_input(packet: dict[str, Any], report: dict[str, Any], report_path: Path) -> dict[str, Any]:
    """Summarize exactly the selected articles, for financial/market subdata only."""
    return {"metadata": {**packet["metadata"], "news_subdata_policy": "monthly_summary_v1"},
            "company_profile": _load_company_profile(report, report_path),
            "periods": packet["periods"]}


def build_context_exports(
    *,
    report_context_path: str | Path,
    output_dir: str | Path | None = None,
    granularity: Granularity = "month",
    period_count: int = 12,
    raw_period_count: int = 12,
    min_mention_count: int = 1,
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
    from shared.news_articles import ARTICLE_NEWS_POLICY, build_article_packet
    if (report.get("news_selection") or {}).get("raw_news_policy") == ARTICLE_NEWS_POLICY:
        if granularity != "month":
            raise ValueError("Selected-article export requires monthly grouping")
        packet = build_article_packet(report, period_count=period_count)
        output_path = Path(output_dir) if output_dir else report_path.parent / "context_exports" / "month"
        raw_path = output_path / "selected_articles.json"
        manifest_path = output_path / "context_export_manifest.json"
        periods = [row["period"] for row in packet["periods"]]
        summary_input = article_summary_input(packet, report, report_path)
        request = _build_llm_summary_request(summary_input, llm_model)
        summary_path = output_path / "summary_prompt_input.json"
        request_path = output_path / "llm_summary_request.json"
        result_path = output_path / "llm_period_summaries.json"
        manifest = {"metadata": summary_input["metadata"], "selected_periods": periods,
                    "summary_periods_for_news_agent": [], "raw_periods_for_news_agent": periods,
                    "summary_periods_for_subdata": periods,
                    "company_related_news_count": len(packet["events"]),
                    "llm": {"run_llm": run_llm, "model": llm_model, "split_by_period": split_by_period},
                    "output_paths": {"news_articles_path": str(raw_path), "manifest_path": str(manifest_path),
                                     "summary_prompt_input_path": str(summary_path),
                                     "llm_summary_request_path": str(request_path),
                                     "llm_period_summaries_path": str(result_path)}}
        save_json(packet, raw_path)
        save_json(summary_input, summary_path)
        save_json(request, request_path)
        if split_by_period:
            _write_period_llm_requests(request, output_path / "period_requests")
        save_json(manifest, manifest_path)
        if run_llm:
            execute_llm_summary_request(llm_request_path=request_path, output_path=result_path,
                api_key_env=api_key_env, env_path=env_path, split_by_period=split_by_period)
        return manifest["output_paths"]
    company_profile = _load_company_profile(report, report_path)

    # An explicitly empty selection must not fall back to the unselected pool.
    summary_event_rows = list(next(
        (report[key] for key in ("news_events_weekly", "news_events_all", "news_events_final", "news_events_topk")
         if isinstance(report.get(key), list)), []
    ))
    selected_event_rows = list(
        report.get("news_events_final") or report.get("news_events_topk") or []
    )
    monthly_policy = (report.get("news_selection") or {}).get("raw_news_policy") == MONTHLY_NEWS_POLICY
    if monthly_policy:
        validate_monthly_news(selected_event_rows, end_exclusive=date.fromisoformat(collect_date[:10]) + timedelta(days=1),
            time_of=lambda e: (e.get("representative") or {}).get("time", ""), id_of=lambda e: e.get("event_id", ""))
    else:
        selected_event_rows = selected_event_rows[:NEWS_AGENT_TOP_K]
    raw_label = MONTHLY_NEWS_LABEL if monthly_policy else "기업 관련 뉴스 상위 20건"
    raw_limit = ANNUAL_NEWS_LIMIT if monthly_policy else NEWS_AGENT_TOP_K
    grouped = _group_events(
        summary_event_rows,
        granularity=granularity,
        min_mention_count=min_mention_count,
    )
    selected_periods = _select_summary_periods(
        grouped=grouped,
        collect_date=collect_date,
        granularity=granularity,
        period_count=period_count,
    )
    period_metadata = {}
    if granularity == "month":
        windows = monthly_windows(date.fromisoformat(collect_date[:10]) + timedelta(days=1), period_count)
        period_metadata = {w["period"]: w for w in windows}
        all_compact = [event for events in grouped.values() for event in events]
        grouped = {w["period"]: [e for e in all_compact if w["period_start"] <= str(e.get("time") or "")[:10] <= w["period_end"]] for w in windows}
        selected_periods = [w["period"] for w in windows]
    summary_periods_for_news_agent = list(selected_periods)
    top_news_events = [
        compact
        for compact in (_compact_event(event) for event in selected_event_rows)
        if compact is not None
    ]
    if granularity == "month":
        for event in top_news_events:
            event["period"] = next((key for key, w in period_metadata.items() if w["period_start"] <= str(event.get("time") or "")[:10] <= w["period_end"]), "")
        top_news_events = [event for event in top_news_events if event.get("period")]
    if monthly_policy:
        top_news_events.sort(key=lambda e: (str(e.get("time") or ""), str(e.get("event_id") or "")))
    elif top_news_events and all(
        int(event.get("relevance_rank") or 0) > 0 for event in top_news_events
    ):
        top_news_events.sort(key=lambda event: int(event["relevance_rank"]))
    top_news_periods = list(
        dict.fromkeys(
            period
            for event in top_news_events
            if (period := event.get("period") or _period_key(str(event.get("time") or ""), granularity))
        )
    )
    top_news_grouped: dict[str, list[dict[str, Any]]] = {
        period: [] for period in top_news_periods
    }
    for event in top_news_events:
        period = event.get("period") or _period_key(str(event.get("time") or ""), granularity)
        if period in top_news_grouped:
            top_news_grouped[period].append(event)

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
        "company_news_top_k": raw_limit,
        "raw_news_policy": MONTHLY_NEWS_POLICY if monthly_policy else "global_top_k_legacy",
        "monthly_top_k": 2 if monthly_policy else None,
        "min_mention_count": min_mention_count,
        "filter_rule": f"mention_count >= {min_mention_count}",
    }

    summary_prompt_input = {
        "description": DESCRIPTION,
        "company_profile": company_profile,
        "metadata": base_metadata,
        "task": "각 period의 events를 바탕으로 해당 기간의 핵심 뉴스 흐름을 요약합니다.",
        "periods": [
            {**_period_payload(period, grouped.get(period, [])), **period_metadata.get(period, {})}
            for period in selected_periods
        ],
    }

    recent_raw_input = {
        "description": DESCRIPTION,
        "company_profile": company_profile,
        "metadata": {
            **base_metadata,
            "usage": f"뉴스 에이전트에 제공할 {raw_label} 입력입니다.",
        },
        "selection": raw_label,
        "top_k": raw_limit,
        "events": top_news_events,
        "periods": [
            _period_payload(period, top_news_grouped[period])
            for period in top_news_periods
        ],
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
            "summary_prompt_input_path": "요청한 기간의 LLM 월별 요약을 만들기 위한 입력 파일입니다.",
            "llm_summary_request_path": "summary_prompt_input.json을 기반으로 만든 LLM 호출 직전 messages payload입니다.",
            "llm_period_summaries_path": "LLM 실행 결과입니다. --run-llm을 지정한 경우에만 생성됩니다.",
            "period_requests_dir": "--split-by-period 지정 시 period별 LLM request 파일이 저장되는 디렉터리입니다.",
            "recent_raw_input_path": f"뉴스 에이전트에 제공할 {raw_label} 입력 파일입니다.",
            "summary_periods_for_news_agent": "뉴스 에이전트에 제공할 월별 요약 기간입니다.",
            "raw_periods_for_news_agent": f"{raw_label}이 포함된 기간입니다.",
        },
        "metadata": base_metadata,
        "selected_periods": selected_periods,
        "summary_periods_for_news_agent": summary_periods_for_news_agent,
        "raw_periods_for_news_agent": top_news_periods,
        "company_related_news_top_k": raw_limit,
        "company_related_news_count": len(top_news_events),
        "total_events_after_filter": sum(
            len(grouped.get(period, [])) for period in selected_periods
        ),
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
    parser.add_argument("--granularity", choices=["day", "week", "month"], default="month")
    parser.add_argument("--period-count", type=int, default=12)
    parser.add_argument("--raw-period-count", type=int, default=12)
    parser.add_argument("--min-mention-count", type=int, default=1)
    parser.add_argument("--llm-model", default="gpt-5.4-mini", help="LLM model for --run-llm")
    parser.add_argument("--run-llm", action="store_true", help="Call OpenAI and save llm_period_summaries.json")
    parser.add_argument(
        "--split-by-period",
        action="store_true",
        help="Call LLM once per period and merge results into one llm_period_summaries.json",
    )
    parser.add_argument("--api-key-env", default="OPENAI_API_KEY", help="Environment variable containing OpenAI API key")
    parser.add_argument(
        "--env-path",
        default=None,
        help="Optional .env path. Defaults to configs/.env, then the repository-root .env.",
    )
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
