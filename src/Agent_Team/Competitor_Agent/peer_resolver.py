"""Resolve one domestic peer from Naver/WiseReport FG000 industry analysis."""

from __future__ import annotations

import argparse
import json
import math
import re
import urllib.parse
import urllib.request
from datetime import datetime
from pathlib import Path
from typing import Any

from bs4 import BeautifulSoup


NAVER_COINFO_URL = "https://finance.naver.com/item/coinfo.naver"
NAVER_ITEM_MAIN_URL = "https://finance.naver.com/item/main.naver"
DEFAULT_TIMEOUT = 20


def resolve_naver_peer(
    stock_code: str,
    *,
    timeout: int = DEFAULT_TIMEOUT,
) -> dict[str, Any]:
    """Fetch FG000 candidates and select the closest market-cap peer."""

    normalized_code = _stock_code(stock_code)
    coinfo_url = f"{NAVER_COINFO_URL}?{urllib.parse.urlencode({'code': normalized_code})}"
    retrieved_at = datetime.now().astimezone().isoformat(timespec="seconds")
    try:
        coinfo_html = _fetch_text(coinfo_url, timeout=timeout, encoding="euc-kr")
        iframe_url = extract_company_iframe_url(coinfo_html, page_url=coinfo_url)
        industry_url = build_industry_analysis_url(iframe_url, stock_code=normalized_code)
        industry_html = _fetch_text(industry_url, timeout=timeout)
        ajax_url, params = build_fg000_ajax_request(
            industry_html,
            industry_url=industry_url,
            stock_code=normalized_code,
        )
        payload_text = _fetch_text(
            f"{ajax_url}?{urllib.parse.urlencode(params)}",
            timeout=timeout,
            referer=industry_url,
        )
        payload = json.loads(payload_text)
        result = select_peer_from_fg000(payload, target_stock_code=normalized_code)
        fallback: dict[str, Any] | None = None
        if result.get("reason") == "target_market_cap_missing":
            # WiseReport occasionally returns a valid FG000 candidate set with an
            # empty target MKT_VAL.  Use Naver's own item page only for the missing
            # target value; candidate values still come from the same FG000 set.
            item_main_url = (
                f"{NAVER_ITEM_MAIN_URL}?"
                f"{urllib.parse.urlencode({'code': normalized_code})}"
            )
            try:
                item_main_html = _fetch_text(
                    item_main_url,
                    timeout=timeout,
                    encoding="euc-kr",
                )
                fallback_market_cap = extract_naver_market_cap_100m_krw(item_main_html)
            except (OSError, TimeoutError, ValueError) as exc:
                fallback = {
                    "status": "failed",
                    "provider": "Naver Finance item main",
                    "reason": "target_market_cap_fallback_failed",
                    "error_type": type(exc).__name__,
                }
            else:
                result = select_peer_from_fg000(
                    payload,
                    target_stock_code=normalized_code,
                    target_market_cap_100m_krw=fallback_market_cap,
                )
                fallback = {
                    "status": "used" if result.get("status") == "selected" else "available",
                    "provider": "Naver Finance item main",
                    "url": item_main_url,
                }
        if fallback is not None:
            result["target_market_cap_fallback"] = fallback
    except (OSError, TimeoutError, ValueError, json.JSONDecodeError) as exc:
        return peer_unavailable(
            stock_code=normalized_code,
            reason="naver_peer_resolution_failed",
            error_type=type(exc).__name__,
            retrieved_at=retrieved_at,
        )

    result["source"] = {
        "provider": "Naver Finance / WiseReport",
        "coinfo_url": coinfo_url,
        "industry_analysis_url": industry_url,
        "ajax_endpoint": ajax_url,
        "industry_group": "FG000",
        "retrieved_at": retrieved_at,
    }
    result["usage_policy"] = {
        "purpose": "peer_identity_selection_only",
        "market_cap_values_are_financial_evidence": False,
        "point_in_time_financial_evidence": False,
    }
    return result


def extract_company_iframe_url(html: str, *, page_url: str) -> str:
    """Extract the WiseReport company iframe from the Naver coinfo page."""

    soup = BeautifulSoup(html or "", "html.parser")
    iframe = soup.find("iframe", id="coinfo_cp")
    if iframe is None:
        iframe = next(
            (
                item
                for item in soup.find_all("iframe")
                if "wisereport" in str(item.get("src") or "").lower()
            ),
            None,
        )
    src = str(iframe.get("src") or "").strip() if iframe is not None else ""
    if not src:
        raise ValueError("WiseReport company iframe was not found.")
    return urllib.parse.urljoin(page_url, src)


def build_industry_analysis_url(iframe_url: str, *, stock_code: str) -> str:
    """Convert the company-summary iframe URL to the industry-analysis page."""

    parsed = urllib.parse.urlparse(iframe_url)
    path = re.sub(r"/c\d{7}\.aspx$", "/c1060001.aspx", parsed.path, flags=re.IGNORECASE)
    if path == parsed.path and not parsed.path.lower().endswith("/c1060001.aspx"):
        path = parsed.path.rsplit("/", 1)[0] + "/c1060001.aspx"
    query = urllib.parse.urlencode({"cmp_cd": _stock_code(stock_code)})
    return urllib.parse.urlunparse((parsed.scheme or "https", parsed.netloc, path, "", query, ""))


def build_fg000_ajax_request(
    industry_html: str,
    *,
    industry_url: str,
    stock_code: str,
) -> tuple[str, dict[str, str]]:
    """Build the request used by the FG000 header table."""

    soup = BeautifulSoup(industry_html or "", "html.parser")
    fin_select = soup.find("select", id="finGubun")
    selected_option = fin_select.find("option", selected=True) if fin_select is not None else None
    fin_gubun = str(selected_option.get("value") or "MAIN") if selected_option is not None else "MAIN"
    parsed = urllib.parse.urlparse(industry_url)
    base_path = parsed.path.rsplit("/", 1)[0]
    ajax_path = f"{base_path}/ajax/cF6001.aspx"
    ajax_url = urllib.parse.urlunparse((parsed.scheme, parsed.netloc, ajax_path, "", "", ""))
    return ajax_url, {
        "cmp_cd": _stock_code(stock_code),
        "finGubun": fin_gubun,
        "sec_cd": "FG000",
        "frq": "Y",
        "cmp_cd1": "",
        "cmp_cd2": "",
        "cmp_cd3": "",
        "cmp_cd4": "",
    }


def extract_naver_market_cap_100m_krw(html: str) -> float:
    """Extract Naver's current market cap, expressed in KRW 100 millions."""

    soup = BeautifulSoup(html or "", "html.parser")
    market_cap = soup.find(id="_market_sum")
    if market_cap is None:
        # Keep a label-based fallback in case Naver removes the element id while
        # preserving the public market-cap table.
        for header in soup.find_all("th"):
            if " ".join(header.get_text(" ", strip=True).split()) != "시가총액":
                continue
            row = header.find_parent("tr")
            market_cap = row.find("td") if row is not None else None
            if market_cap is not None:
                break
    text = " ".join(market_cap.get_text(" ", strip=True).split()) if market_cap is not None else ""
    if not text:
        raise ValueError("Naver item market cap was not found.")

    normalized = text.replace(",", "")
    trillion_match = re.search(r"(\d+(?:\.\d+)?)\s*조", normalized)
    trillion_krw = float(trillion_match.group(1)) if trillion_match else 0.0
    if trillion_match:
        normalized = normalized[trillion_match.end() :]
    hundred_million_match = re.search(r"(\d+(?:\.\d+)?)", normalized)
    hundred_million_krw = (
        float(hundred_million_match.group(1)) if hundred_million_match else 0.0
    )
    value = (trillion_krw * 10_000) + hundred_million_krw
    if not math.isfinite(value) or value <= 0:
        raise ValueError("Naver item market cap was not a positive finite number.")
    return value


def select_peer_from_fg000(
    payload: dict[str, Any],
    *,
    target_stock_code: str,
    target_market_cap_100m_krw: float | None = None,
) -> dict[str, Any]:
    """Normalize FG000 rows and choose one candidate by market-cap distance."""

    rows = payload.get("oDt_header") if isinstance(payload, dict) else None
    if not isinstance(rows, list) or not rows:
        return peer_unavailable(stock_code=target_stock_code, reason="fg000_candidates_not_available")

    normalized_rows = [candidate for row in rows if (candidate := _normalize_candidate(row)) is not None]
    target_code = _stock_code(target_stock_code)
    target = next((candidate for candidate in normalized_rows if candidate["stock_code"] == target_code), None)
    candidates = [candidate for candidate in normalized_rows if candidate["stock_code"] != target_code]
    if target is None:
        return peer_unavailable(stock_code=target_code, reason="target_missing_from_fg000_response")
    target_market_cap_source = "fg000_header"
    if target.get("market_cap_100m_krw") is None:
        fallback_market_cap = _number(target_market_cap_100m_krw)
        if fallback_market_cap is not None and fallback_market_cap > 0:
            target["market_cap_100m_krw"] = fallback_market_cap
            target_market_cap_source = "naver_item_main"
    if target.get("market_cap_100m_krw") is None:
        return peer_unavailable(stock_code=target_code, reason="target_market_cap_missing")
    comparable = [candidate for candidate in candidates if candidate.get("market_cap_100m_krw") is not None]
    if not comparable:
        return peer_unavailable(stock_code=target_code, reason="comparable_peer_market_caps_missing")

    target_market_cap = float(target["market_cap_100m_krw"])
    for candidate in comparable:
        candidate["market_cap_distance_100m_krw"] = abs(
            float(candidate["market_cap_100m_krw"]) - target_market_cap
        )
    selected = min(
        comparable,
        key=lambda item: (float(item["market_cap_distance_100m_krw"]), int(item.get("sequence") or 0)),
    )
    return {
        "status": "selected",
        "target": target,
        "candidates": candidates,
        "selected_peer": selected,
        "selection_basis": {
            "method": "minimum_absolute_market_cap_distance_within_fg000_candidates",
            "candidate_count": len(candidates),
            "comparable_candidate_count": len(comparable),
            "market_cap_unit": "100m_KRW",
            "target_market_cap_source": target_market_cap_source,
        },
    }


def peer_unavailable(
    *,
    stock_code: str,
    reason: str,
    error_type: str | None = None,
    retrieved_at: str | None = None,
) -> dict[str, Any]:
    """Return the stable failure contract for peer discovery."""

    result: dict[str, Any] = {
        "status": "peer_unavailable",
        "reason": reason,
        "target": {"stock_code": _stock_code(stock_code)},
        "candidates": [],
        "selected_peer": {},
    }
    if error_type:
        result["error_type"] = error_type
    if retrieved_at:
        result["source"] = {"retrieved_at": retrieved_at}
    return result


def _normalize_candidate(row: Any) -> dict[str, Any] | None:
    if not isinstance(row, dict):
        return None
    stock_code = _stock_code(row.get("CMP_CD"))
    company_name = " ".join(str(row.get("CMP_KOR") or "").split())
    if not stock_code or not company_name:
        return None
    market_cap = _number(row.get("MKT_VAL"))
    if market_cap is not None and market_cap <= 0:
        market_cap = None
    return {
        "sequence": _integer(row.get("SEQ")),
        "stock_code": stock_code,
        "company_name": company_name,
        "period_label": str(row.get("YYMM") or ""),
        "market_cap_100m_krw": market_cap,
        "financial_statement_basis": str(row.get("FIN_GUBUN") or ""),
    }


def _fetch_text(
    url: str,
    *,
    timeout: int,
    encoding: str | None = None,
    referer: str | None = None,
) -> str:
    headers = {
        "User-Agent": "Mozilla/5.0 (compatible; Financial-Agent-Peer-Resolver/1.0)",
        "Accept": "text/html,application/json;q=0.9,*/*;q=0.8",
    }
    if referer:
        headers["Referer"] = referer
    request = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(request, timeout=timeout) as response:  # nosec - fixed trusted hosts
        raw = response.read()
        response_charset = response.headers.get_content_charset()
    for candidate in (encoding, response_charset, "utf-8", "euc-kr"):
        if not candidate:
            continue
        try:
            return raw.decode(candidate)
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", errors="replace")


def _stock_code(value: Any) -> str:
    digits = re.sub(r"\D", "", str(value or ""))
    return digits.zfill(6) if digits and len(digits) <= 6 else digits


def _number(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    normalized = value
    if isinstance(value, str):
        normalized = value.strip().replace(",", "")
        if normalized.lower() in {"", "-", "--", "n/a", "na", "null", "none"}:
            return None
    try:
        number = float(normalized)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _integer(value: Any) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def main() -> None:
    parser = argparse.ArgumentParser(description="Resolve one Naver FG000 domestic peer.")
    parser.add_argument("--stock-code", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--timeout", type=int, default=DEFAULT_TIMEOUT)
    args = parser.parse_args()

    result = resolve_naver_peer(args.stock_code, timeout=args.timeout)
    output = Path(args.output).expanduser().resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(output)


if __name__ == "__main__":
    main()
