"""DART API utility for periodic reports (business / half-year / quarterly)."""

from __future__ import annotations

import io
import json
import zipfile
from pathlib import Path
from typing import Any
from urllib.request import Request, urlopen


DART_BASE_URL = "https://opendart.fss.or.kr/api"
REPORT_TYPE_MAP = {
    "A001": "사업보고서",
    "A002": "반기보고서",
    "A003": "분기보고서",
}


def _http_get(url: str, timeout: int = 20) -> bytes:
    req = Request(url, headers={"User-Agent": "KCA-NewsAgent-DART"})
    with urlopen(req, timeout=timeout) as resp:  # nosec B310
        return resp.read()


def _date_to_int(value: str) -> int:
    digits = "".join(ch for ch in str(value) if ch.isdigit())
    return int(digits) if digits else 0


def _is_official_report(report_nm: str) -> bool:
    for token in ("정정", "첨부", "추가", "기재정정", "정정신고서", "첨부정정"):
        if token in report_nm:
            return False
    return True


def fetch_periodic_list(
    api_key: str,
    corp_code: str,
    bgn_de: str,
    end_de: str,
    report_tp: str,
    *,
    page_no: int = 1,
    timeout: int = 20,
) -> dict[str, Any]:
    query = {
        "crtfc_key": api_key,
        "corp_code": corp_code,
        "bgn_de": bgn_de,
        "end_de": end_de,
        "report_tp": report_tp,
        "page_no": str(page_no),
        "page_count": "100",
    }
    query_str = "&".join(f"{k}={v}" for k, v in query.items() if v)
    url = f"{DART_BASE_URL}/list.json?{query_str}"
    payload = _http_get(url, timeout=timeout)
    return json.loads(payload.decode("utf-8", errors="ignore"))


def fetch_document_xml(api_key: str, rcept_no: str, *, timeout: int = 30) -> str:
    url = f"{DART_BASE_URL}/document.xml?crtfc_key={api_key}&rcept_no={rcept_no}"
    payload = _http_get(url, timeout=timeout)

    if payload.startswith(b"PK"):
        with zipfile.ZipFile(io.BytesIO(payload)) as zf:
            xml_names = [name for name in zf.namelist() if name.lower().endswith(".xml")]
            if not xml_names:
                return ""
            with zf.open(xml_names[0]) as f:
                return f.read().decode("utf-8", errors="ignore")

    return payload.decode("utf-8", errors="ignore")


def fetch_latest_periodic_xml(
    api_key: str,
    corp_code: str,
    bgn_de: str,
    end_de: str,
    *,
    list_timeout: int = 20,
    doc_timeout: int = 30,
    max_pages: int = 20,
) -> dict[str, str]:
    candidates: list[dict[str, str]] = []

    for report_tp, report_keyword in REPORT_TYPE_MAP.items():
        items: list[dict[str, Any]] = []
        total_page = 1
        page_no = 1

        while page_no <= total_page and page_no <= max_pages:
            listing = fetch_periodic_list(
                api_key,
                corp_code,
                bgn_de,
                end_de,
                report_tp,
                page_no=page_no,
                timeout=list_timeout,
            )
            total_page = int(listing.get("total_page") or total_page)
            page_items = listing.get("list") or []
            if isinstance(page_items, list):
                items.extend(page_items)
            page_no += 1

        for item in items:
            if not isinstance(item, dict):
                continue
            report_nm = str(item.get("report_nm") or "")
            if report_keyword not in report_nm:
                continue
            rcept_no = str(item.get("rcept_no") or "")
            if not rcept_no:
                continue
            candidates.append(
                {
                    "rcept_no": rcept_no,
                    "report_nm": report_nm,
                    "report_dt": str(item.get("rcept_dt") or ""),
                    "report_tp": report_tp,
                }
            )

    if not candidates:
        raise ValueError("사업/반기/분기 보고서를 찾지 못했습니다.")

    selected = max(
        candidates,
        key=lambda x: (
            _date_to_int(x["report_dt"]),
            _is_official_report(x["report_nm"]),
            _date_to_int(x["rcept_no"]),
        ),
    )
    xml_text = fetch_document_xml(api_key, selected["rcept_no"], timeout=doc_timeout)

    return {
        "rcept_no": selected["rcept_no"],
        "report_nm": selected["report_nm"],
        "report_dt": selected["report_dt"],
        "report_tp": selected["report_tp"],
        "xml_text": xml_text,
    }


def fetch_latest_quarterly_xml(
    api_key: str,
    corp_code: str,
    bgn_de: str,
    end_de: str,
    *,
    list_timeout: int = 20,
    doc_timeout: int = 30,
    max_pages: int = 20,
) -> dict[str, str]:
    # Backward-compatible alias.
    return fetch_latest_periodic_xml(
        api_key=api_key,
        corp_code=corp_code,
        bgn_de=bgn_de,
        end_de=end_de,
        list_timeout=list_timeout,
        doc_timeout=doc_timeout,
        max_pages=max_pages,
    )


def save_xml(xml_text: str, output_path: str) -> None:
    path = Path(output_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(xml_text, encoding="utf-8")
