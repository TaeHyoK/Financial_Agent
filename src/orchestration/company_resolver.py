"""Resolve runtime company identities from a company name or stock code."""

from __future__ import annotations

import io
import json
import re
import urllib.parse
import urllib.request
import zipfile
from dataclasses import asdict, dataclass
from datetime import date, datetime, timedelta, timezone
from typing import Any, Callable
from xml.etree import ElementTree

from .config import normalize_date


DART_CORP_CODE_URL = "https://opendart.fss.or.kr/api/corpCode.xml"
DEFAULT_RESOLVER_TIMEOUT = 30
NEWS_WINDOW_DAYS = {"2w": 14, "1m": 30, "3m": 90}
KOREAN_LETTER_NAMES = {
    "a": "에이",
    "b": "비",
    "c": "씨",
    "d": "디",
    "e": "이",
    "f": "에프",
    "g": "지",
    "h": "에이치",
    "i": "아이",
    "j": "제이",
    "k": "케이",
    "l": "엘",
    "m": "엠",
    "n": "엔",
    "o": "오",
    "p": "피",
    "q": "큐",
    "r": "알",
    "s": "에스",
    "t": "티",
    "u": "유",
    "v": "브이",
    "w": "더블유",
    "x": "엑스",
    "y": "와이",
    "z": "지",
}


class CompanyResolutionError(ValueError):
    """Raised when one unambiguous listed-company identity cannot be resolved."""


@dataclass(frozen=True)
class CompanyIdentity:
    """Provider identifiers required by the domain pipelines."""

    company_name: str
    corp_code: str
    stock_code: str
    market: str
    ticker: str
    source: dict[str, Any]

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


def fetch_dart_company_directory(
    api_key: str,
    *,
    timeout: int = DEFAULT_RESOLVER_TIMEOUT,
) -> list[dict[str, str]]:
    """Download and parse OpenDART's complete corporate-code directory."""

    if not str(api_key or "").strip():
        raise CompanyResolutionError("DART_API_KEY is required for company resolution.")
    query = urllib.parse.urlencode({"crtfc_key": api_key})
    request = urllib.request.Request(
        f"{DART_CORP_CODE_URL}?{query}",
        headers={"User-Agent": "Financial-Agent-Company-Resolver/1.0"},
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:  # nosec - fixed OpenDART host
        payload = response.read()
    return parse_dart_company_directory(payload)


def parse_dart_company_directory(payload: bytes) -> list[dict[str, str]]:
    """Parse zipped or plain OpenDART CORPCODE XML bytes."""

    xml_payload = payload
    if payload.startswith(b"PK"):
        with zipfile.ZipFile(io.BytesIO(payload)) as archive:
            names = sorted(name for name in archive.namelist() if name.lower().endswith(".xml"))
            if not names:
                raise CompanyResolutionError("OpenDART corpCode archive contains no XML file.")
            xml_payload = archive.read(names[0])
    try:
        root = ElementTree.fromstring(xml_payload)
    except ElementTree.ParseError as exc:
        raise CompanyResolutionError(f"Invalid OpenDART corpCode XML: {exc}") from exc

    companies: list[dict[str, str]] = []
    for item in root.findall(".//list"):
        corp_code = _element_text(item, "corp_code")
        corp_name = _element_text(item, "corp_name")
        stock_code = _stock_code(_element_text(item, "stock_code"))
        if not corp_code or not corp_name:
            continue
        companies.append(
            {
                "corp_code": corp_code,
                "company_name": corp_name,
                "stock_code": stock_code,
                "modify_date": _element_text(item, "modify_date"),
            }
        )
    if not companies:
        raise CompanyResolutionError("OpenDART corpCode XML contains no company records.")
    return companies


def resolve_company_identity(
    company_name: str,
    *,
    selected_date: str | date,
    directory: list[dict[str, str]],
    market_resolver: Callable[[str, date], str] | None = None,
) -> CompanyIdentity:
    """Resolve one listed company from a normalized exact company-name match."""

    requested_aliases = _normalized_company_name_aliases(company_name)
    if not requested_aliases:
        raise CompanyResolutionError("company_name is required.")
    listed = [item for item in directory if _stock_code(item.get("stock_code"))]
    matches = [
        item
        for item in listed
        if requested_aliases & _normalized_company_name_aliases(item.get("company_name"))
    ]
    return _resolve_identity_from_matches(
        matches,
        query_label=company_name,
        selected_date=_as_date(selected_date),
        market_resolver=market_resolver,
        resolution_method="normalized_exact_company_name",
        display_company_name=" ".join(str(company_name or "").split()),
    )


def resolve_company_identity_by_stock_code(
    stock_code: str,
    *,
    selected_date: str | date,
    directory: list[dict[str, str]],
    market_resolver: Callable[[str, date], str] | None = None,
) -> CompanyIdentity:
    """Resolve one listed company by its six-digit stock code."""

    normalized = _stock_code(stock_code)
    if not normalized:
        raise CompanyResolutionError("stock_code is required.")
    matches = [
        item
        for item in directory
        if _stock_code(item.get("stock_code")) == normalized
    ]
    return _resolve_identity_from_matches(
        matches,
        query_label=normalized,
        selected_date=_as_date(selected_date),
        market_resolver=market_resolver,
        resolution_method="exact_stock_code",
    )


def resolve_krx_market(stock_code: str, selected_date: date) -> str:
    """Return KOSPI or KOSDAQ membership on the latest available prior date."""

    normalized = _stock_code(stock_code)
    try:
        from pykrx import stock
    except ImportError:  # pragma: no cover - depends on the runtime environment
        stock = None

    if stock is not None:
        for offset in range(1, 15):
            lookup_date = (selected_date - timedelta(days=offset)).strftime("%Y%m%d")
            for market in ("KOSPI", "KOSDAQ"):
                try:
                    tickers = set(stock.get_market_ticker_list(lookup_date, market=market))
                except Exception:
                    continue
                if normalized in tickers:
                    return market
    naver_market = resolve_naver_market(normalized)
    if naver_market:
        return naver_market
    yahoo_market = resolve_yahoo_market(normalized, selected_date)
    if yahoo_market:
        return yahoo_market
    raise CompanyResolutionError(
        f"Could not resolve KOSPI/KOSDAQ membership for stock code {normalized} before {selected_date}."
    )


def resolve_naver_market(stock_code: str) -> str:
    """Resolve the current KRX board from the Naver item identity header."""

    normalized = _stock_code(stock_code)
    url = f"https://finance.naver.com/item/main.naver?{urllib.parse.urlencode({'code': normalized})}"
    request = urllib.request.Request(
        url,
        headers={"User-Agent": "Financial-Agent-Company-Resolver/1.0"},
    )
    try:
        with urllib.request.urlopen(request, timeout=DEFAULT_RESOLVER_TIMEOUT) as response:  # nosec - fixed Naver host
            html = response.read().decode(response.headers.get_content_charset() or "utf-8", errors="replace")
    except (OSError, TimeoutError):
        return ""
    if re.search(r"<img[^>]+(?:btn_kospi\.gif|class=[\"']kospi[\"'])", html, re.IGNORECASE):
        return "KOSPI"
    if re.search(r"<img[^>]+(?:btn_kosdaq\.gif|class=[\"']kosdaq[\"'])", html, re.IGNORECASE):
        return "KOSDAQ"
    return ""


def resolve_yahoo_market(stock_code: str, selected_date: date) -> str:
    """Resolve KRX board from a short pre-cutoff Yahoo chart probe."""

    normalized = _stock_code(stock_code)
    start = selected_date - timedelta(days=30)
    period1 = int(datetime(start.year, start.month, start.day, tzinfo=timezone.utc).timestamp())
    period2 = int(
        datetime(selected_date.year, selected_date.month, selected_date.day, tzinfo=timezone.utc).timestamp()
    )
    matches: list[str] = []
    for market, suffix in (("KOSPI", ".KS"), ("KOSDAQ", ".KQ")):
        query = urllib.parse.urlencode(
            {
                "period1": period1,
                "period2": period2,
                "interval": "1d",
                "events": "history",
            }
        )
        url = f"https://query1.finance.yahoo.com/v8/finance/chart/{normalized}{suffix}?{query}"
        request = urllib.request.Request(
            url,
            headers={"User-Agent": "Financial-Agent-Company-Resolver/1.0"},
        )
        try:
            with urllib.request.urlopen(request, timeout=DEFAULT_RESOLVER_TIMEOUT) as response:  # nosec - fixed Yahoo host
                payload = json.loads(response.read().decode("utf-8"))
        except (OSError, TimeoutError, json.JSONDecodeError):
            continue
        chart = payload.get("chart") if isinstance(payload, dict) else {}
        results = chart.get("result") if isinstance(chart, dict) else None
        result = results[0] if isinstance(results, list) and results else {}
        timestamps = result.get("timestamp") if isinstance(result, dict) else None
        if isinstance(timestamps, list) and timestamps:
            matches.append(market)
    return matches[0] if len(matches) == 1 else ""


def build_resolved_company_config(
    identity: CompanyIdentity,
    *,
    selected_date: str | date,
    news_window: str = "1m",
    llm_model: str = "gpt-5.4-mini",
    max_retries: int = 1,
) -> dict[str, Any]:
    """Build the existing per-company config contract from a resolved identity."""

    selected = _as_date(selected_date)
    start_date, end_date = resolve_news_date_range(selected, news_window)
    return {
        "company_code": identity.corp_code,
        "corp_code": identity.corp_code,
        "company_name": identity.company_name,
        "stock_code": identity.stock_code,
        "ticker": identity.ticker,
        "report_type": "latest regular report available before selected_date",
        "date_range": f"{start_date:%Y%m%d}-{end_date:%Y%m%d}",
        "selected_date": f"{selected:%Y%m%d}",
        "selected_date_policy": "before_market_open",
        "llm_model": llm_model,
        "max_retries": max(0, int(max_retries)),
        "identity_resolution": identity.as_dict(),
    }


def resolve_news_date_range(selected_date: date, news_window: str) -> tuple[date, date]:
    """Return the inclusive window ending one day before a pre-open selected date."""

    normalized = str(news_window or "").strip().lower()
    if normalized not in NEWS_WINDOW_DAYS:
        raise CompanyResolutionError(
            f"Unsupported news_window {news_window!r}; choose one of {sorted(NEWS_WINDOW_DAYS)}."
        )
    days = NEWS_WINDOW_DAYS[normalized]
    return selected_date - timedelta(days=days), selected_date - timedelta(days=1)


def _resolve_identity_from_matches(
    matches: list[dict[str, str]],
    *,
    query_label: str,
    selected_date: date,
    market_resolver: Callable[[str, date], str] | None,
    resolution_method: str,
    display_company_name: str = "",
) -> CompanyIdentity:
    if not matches:
        raise CompanyResolutionError(f"No listed OpenDART company matched {query_label!r}.")
    if len(matches) != 1:
        candidates = [
            {
                "company_name": item.get("company_name"),
                "corp_code": item.get("corp_code"),
                "stock_code": _stock_code(item.get("stock_code")),
            }
            for item in matches
        ]
        raise CompanyResolutionError(
            f"Company identity is ambiguous for {query_label!r}: {candidates}"
        )
    match = matches[0]
    stock_code = _stock_code(match.get("stock_code"))
    resolver = market_resolver or resolve_krx_market
    market = str(resolver(stock_code, selected_date) or "").strip().upper()
    suffix = {"KOSPI": ".KS", "KOSDAQ": ".KQ"}.get(market)
    if suffix is None:
        raise CompanyResolutionError(
            f"Unsupported market {market!r} for stock code {stock_code}; expected KOSPI or KOSDAQ."
        )
    return CompanyIdentity(
        company_name=display_company_name or str(match.get("company_name") or "").strip(),
        corp_code=str(match.get("corp_code") or "").strip(),
        stock_code=stock_code,
        market=market,
        ticker=f"{stock_code}{suffix}",
        source={
            "provider": "OpenDART corpCode.xml + KRX market membership",
            "resolution_method": resolution_method,
            "dart_corp_name": str(match.get("company_name") or "").strip(),
            "directory_modify_date": str(match.get("modify_date") or ""),
            "market_lookup_policy": (
                "prior-date pykrx membership when available; current-board provider fallback for identity only"
            ),
        },
    )


def _normalized_company_name(value: Any) -> str:
    text = str(value or "").strip().lower()
    text = re.sub(r"\(주\)|주식회사", "", text)
    return re.sub(r"[^0-9a-z가-힣]", "", text)


def _normalized_company_name_aliases(value: Any) -> set[str]:
    """Return exact-match aliases, including Korean readings of Latin initials."""

    text = str(value or "").strip().lower()
    normalized = _normalized_company_name(text)
    if not normalized:
        return set()
    expanded = re.sub(
        r"[a-z]+",
        lambda match: "".join(KOREAN_LETTER_NAMES[letter] for letter in match.group(0)),
        text,
    )
    return {normalized, _normalized_company_name(expanded)}


def _stock_code(value: Any) -> str:
    digits = re.sub(r"\D", "", str(value or ""))
    if not digits:
        return ""
    return digits.zfill(6) if len(digits) <= 6 else digits


def _as_date(value: str | date) -> date:
    if isinstance(value, date):
        return value
    normalized = normalize_date(value)
    return date(int(normalized[:4]), int(normalized[4:6]), int(normalized[6:]))


def _element_text(item: ElementTree.Element, key: str) -> str:
    value = item.findtext(key)
    return str(value or "").strip()
