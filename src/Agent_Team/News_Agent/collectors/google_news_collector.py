"""
Google News (Google Search news tab) HTML collector.
Updated to target specific classes provided by user:
- Title: .n0jPhd, .ynAwRc
- Snippet: .UqSP2b
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from email.utils import parsedate_to_datetime
from html import unescape
import json
import logging
import random
import re
import time
from datetime import date, datetime, timedelta, timezone
from typing import Any, Optional
from urllib.parse import quote, quote_plus, urlencode, urlparse, parse_qs, unquote

import requests
from bs4 import BeautifulSoup, Tag

from ..io.normalization import parse_date_from_text
from ..dart.schemas import RawNewsRecord

LOGGER = logging.getLogger(__name__)
KST = timezone(timedelta(hours=9))

# ---- Heuristics / signals ----------------------------------------------------

_CAPTCHA_MARKERS = ("unusual traffic", "Our systems have detected unusual traffic", "보안문자")
# 상대시간(한국어) 감지
_RELATIVE_KR = re.compile(r"(방금|어제|(\d+)\s*분\s*전|(\d+)\s*시간\s*전|(\d+)\s*일\s*전|(\d+)\s*주\s*전)")
_GENERIC_GOOGLE_SNIPPETS = (
    "Google 뉴스가 전세계 매체로부터 종합한 최신 뉴스",
    "Google News aggregates",
)
_BAD_SNIPPET_MARKERS = (
    "All rights reserved",
    "무단 전재",
    "재배포 금지",
    "AI학습 활용 금지",
    "Copyright",
    "ⓒ",
)
_BODY_SNIPPET_KEYS = {
    "articlebody": 0,
    "body": 1,
    "description": 2,
    "summary": 2,
    "abstract": 2,
    "lead": 2,
    "deck": 2,
    "sub_title": 3,
    "subtitle": 3,
}

# ---- URL helpers ------------------------------------------------------------

def _fmt_cd(d: date) -> str:
    return f"{d.month}/{d.day}/{d.year}"

def _normalize_url(raw_href: str) -> str:
    if not raw_href: return ""
    u = raw_href.strip()
    # 구글 내부 리다이렉션인 경우 실제 URL 추출
    if "/url?" in u or "google.com/url?" in u:
        try:
            parsed = urlparse(u)
            qs = parse_qs(parsed.query)
            # q=... 또는 url=... 파라미터 확인
            real = qs.get("q", [""])[0] or qs.get("url", [""])[0]
            return unquote(real) if real else u
        except Exception:
            return u
    return u


def _parse_rss_pub_date(pub_date: str) -> date | None:
    if not pub_date:
        return None
    try:
        parsed = parsedate_to_datetime(pub_date)
    except Exception:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(KST).date()


def _clean_snippet_text(text: str | None, title: str | None = None) -> str:
    if not text:
        return ""
    cleaned = re.sub(r"\s+", " ", unescape(str(text))).strip()
    title_text = re.sub(r"\s+", " ", str(title or "")).strip()
    if title_text and cleaned == title_text:
        return ""
    return cleaned


def _is_google_news_host(url: str) -> bool:
    host = (urlparse(url).hostname or "").lower()
    return host.endswith("news.google.com")


def _is_generic_google_snippet(text: str | None) -> bool:
    cleaned = _clean_snippet_text(text)
    if not cleaned:
        return False
    lowered = cleaned.lower()
    return any(marker.lower() in lowered for marker in _GENERIC_GOOGLE_SNIPPETS)


def _truncate_snippet(text: str, max_chars: int = 280) -> str:
    cleaned = _clean_snippet_text(text)
    if len(cleaned) <= max_chars:
        return cleaned
    cut = cleaned[:max_chars].rsplit(" ", 1)[0].strip()
    return f"{cut or cleaned[:max_chars].strip()}..."


def _looks_like_content_snippet(text: str | None, title: str | None = None) -> bool:
    cleaned = _clean_snippet_text(text, title)
    if not cleaned or len(cleaned) < 20:
        return False
    if _is_generic_google_snippet(cleaned):
        return False
    lowered = cleaned.lower()
    if any(marker.lower() in lowered for marker in _BAD_SNIPPET_MARKERS):
        return False
    if cleaned.startswith("http://") or cleaned.startswith("https://"):
        return False
    return True


def _extract_meta_description(html_text: str) -> str:
    if not html_text:
        return ""
    soup = BeautifulSoup(html_text, "html.parser")
    selectors = [
        'meta[property="og:description"]',
        'meta[name="description"]',
        'meta[name="twitter:description"]',
    ]
    for selector in selectors:
        tag = soup.select_one(selector)
        if not tag:
            continue
        content = _clean_snippet_text(tag.get("content", ""))
        if content:
            return content
    return ""


def _iter_script_payloads(soup: BeautifulSoup):
    for tag in soup.select('script[type="application/ld+json"], script#__NEXT_DATA__'):
        text = tag.string or tag.get_text(" ", strip=True)
        if not text:
            continue
        try:
            yield json.loads(text)
        except Exception:
            continue


def _collect_json_snippet_candidates(node: Any, path: tuple[str, ...] = ()) -> list[tuple[int, str]]:
    candidates: list[tuple[int, str]] = []
    if isinstance(node, dict):
        for key, value in node.items():
            key_norm = str(key).strip().lower()
            next_path = path + (key_norm,)
            if isinstance(value, str):
                priority = None
                if key_norm == "content" and any(part in {"contentarrange", "articleview"} for part in path):
                    priority = 1
                elif key_norm in _BODY_SNIPPET_KEYS:
                    priority = _BODY_SNIPPET_KEYS[key_norm]
                if priority is not None:
                    candidates.append((priority, value))
            else:
                candidates.extend(_collect_json_snippet_candidates(value, next_path))
    elif isinstance(node, list):
        for item in node:
            candidates.extend(_collect_json_snippet_candidates(item, path))
    return candidates


def _extract_script_backed_snippet(html_text: str, title: str) -> str:
    if not html_text:
        return ""
    soup = BeautifulSoup(html_text, "html.parser")
    ranked: list[tuple[int, int, str]] = []
    order = 0
    for payload in _iter_script_payloads(soup):
        for priority, candidate in _collect_json_snippet_candidates(payload):
            cleaned = _clean_snippet_text(candidate, title)
            if not _looks_like_content_snippet(cleaned, title):
                continue
            ranked.append((priority, order, cleaned))
            order += 1
    if not ranked:
        return ""
    ranked.sort(key=lambda item: (item[0], item[1]))
    return _truncate_snippet(ranked[0][2])


def _extract_dom_text_snippet(html_text: str, title: str) -> str:
    if not html_text:
        return ""
    soup = BeautifulSoup(html_text, "html.parser")
    selectors = [
        "article p",
        "[itemprop='articleBody'] p",
        "[data-testid='article-body'] p",
        ".article-body p",
        ".article_body p",
        ".article_txt p",
        ".view_cont p",
        ".news_cnt_detail_wrap p",
        "main p",
    ]
    ranked: list[tuple[int, str]] = []
    for priority, selector in enumerate(selectors):
        for tag in soup.select(selector):
            cleaned = _clean_snippet_text(tag.get_text(" ", strip=True), title)
            if not _looks_like_content_snippet(cleaned, title):
                continue
            ranked.append((priority, cleaned))
            break
        if ranked:
            break
    if not ranked:
        return ""
    ranked.sort(key=lambda item: (item[0], -len(item[1])))
    return _truncate_snippet(ranked[0][1])


def _extract_article_snippet(html_text: str, title: str) -> str:
    script_snippet = _extract_script_backed_snippet(html_text, title)
    if script_snippet:
        return script_snippet
    dom_snippet = _extract_dom_text_snippet(html_text, title)
    if dom_snippet:
        return dom_snippet
    meta_snippet = _clean_snippet_text(_extract_meta_description(html_text), title)
    if _looks_like_content_snippet(meta_snippet, title):
        return _truncate_snippet(meta_snippet)
    return ""

# ---- Parsing helpers (User-Specific Classes) --------------------------------

def _iter_news_cards(soup: BeautifulSoup):
    """
    뉴스 기사 하나를 감싸는 컨테이너를 찾습니다.
    사용자가 제공한 내부 태그들이 존재하는 부모 div를 찾습니다.
    """
    # SoaBEf, Mg7eCe: 구글 뉴스의 전통적인 카드 컨테이너 클래스
    # g: 일반 검색 결과 컨테이너
    found_cards = soup.select("div.SoaBEf, div.Mg7eCe, div.g, g-card")
    
    # 만약 컨테이너를 못 찾았다면, 사용자 제공 클래스(n0jPhd)의 부모들을 역추적해서 처리할 수도 있음
    if not found_cards:
        # 제목 태그(n0jPhd)가 있는 부모 요소를 임시 카드로 간주
        titles = soup.select(".n0jPhd")
        for t in titles:
            # 제목의 부모의 부모 정도를 카드 컨테이너로 추정
            yield t.find_parent("div", class_="SoaBEf") or t.find_parent("div")
    else:
        for card in found_cards:
            yield card

def _extract_card_data(card: Tag) -> tuple[Optional[str], Optional[str], Optional[str], Optional[str], Optional[str]]:
    """
    사용자가 제공한 특정 클래스(.n0jPhd, .UqSP2b)를 우선적으로 추출합니다.
    """
    # 1. Link (a 태그)
    # 카드 전체를 감싸는 a태그 혹은 제목 근처의 a태그 검색
    a_tag = card.find("a", href=True)
    if not a_tag:
        return None, None, None, None, None
    
    url = _normalize_url(a_tag.get("href", ""))
    
    # 2. Title (사용자 제공 클래스 우선 적용)
    # .n0jPhd, .ynAwRc, .MBeuO 순서로 검색
    title_tag = card.select_one(".n0jPhd, .ynAwRc, .MBeuO, div[role='heading']")
    if title_tag:
        title = title_tag.get_text(strip=True)
    else:
        # 태그를 못 찾았으면 a 태그 텍스트라도 사용
        title = a_tag.get_text(strip=True)
    
    # 3. Snippet (사용자 제공 클래스: .UqSP2b)
    snippet_tag = card.select_one(".UqSP2b")
    if not snippet_tag:
        # 못 찾을 경우 일반적인 스니펫 클래스 시도
        snippet_tag = card.select_one(".GI74Re, .V8B7Ee, .MUwY0b")
    
    snippet = snippet_tag.get_text(strip=True) if snippet_tag else None

    # 4. Source & Date (보조 정보)
    # 소스: .Mg7eCe, .NUnG9d 등
    source_tag = card.select_one(".Mg7eCe, .rbguR, .NUnG9d")
    source = source_tag.get_text(strip=True) if source_tag else None
    
    # 날짜: 보통 소스 근처나 span에 '전' 텍스트 포함
    date_tag = card.select_one(".OSrE9b, span:contains('전')")
    # 정규식으로 '전'이 포함된 텍스트 찾기 (태그가 명확치 않을 때)
    if not date_tag:
        for span in card.find_all("span"):
            if _RELATIVE_KR.search(span.get_text()):
                date_tag = span
                break
    
    date_text = date_tag.get_text(strip=True) if date_tag else None

    return title, url, source, date_text, snippet


# ---- Collector ---------------------------------------------------------------

class GoogleNewsCollector:
    def __init__(self, language="ko", region="KR", max_retries=2, timeout_sec=15):
        self.language = language
        self.region = region
        self.max_retries = max_retries
        self.timeout_sec = timeout_sec
        self._session = requests.Session()
        
        # User-Agent 설정
        self._session.headers.update({
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8",
            "Accept-Language": "ko-KR,ko;q=0.9,en-US;q=0.8,en;q=0.7",
        })

    def _request(self, method: str, url: str, **kwargs):
        headers = dict(self._session.headers)
        extra_headers = kwargs.pop("headers", None)
        if isinstance(extra_headers, dict):
            headers.update(extra_headers)
        return self._session.request(method, url, headers=headers, timeout=self.timeout_sec, **kwargs)

    @staticmethod
    def _extract_google_article_token(source_url: str) -> str:
        parsed = urlparse(source_url)
        path = parsed.path.split("/")
        if parsed.hostname != "news.google.com" or len(path) <= 1 or path[-2] not in {"articles", "read"}:
            return ""
        return path[-1]

    def _with_google_news_locale(self, source_url: str) -> str:
        parsed = urlparse(source_url)
        if parsed.hostname != "news.google.com":
            return source_url
        query = parse_qs(parsed.query)
        query.setdefault("hl", [self.language])
        query.setdefault("gl", [self.region])
        query.setdefault("ceid", [f"{self.region}:{self.language}"])
        return parsed._replace(query=urlencode(query, doseq=True)).geturl()

    def _decode_google_news_url_fast(self, source_url: str) -> str:
        token = self._extract_google_article_token(source_url)
        if not token:
            return source_url
        try:
            import base64

            decoded_bytes = base64.urlsafe_b64decode(token + "==")
            decoded_str = decoded_bytes.decode("latin1")
            prefix = bytes([0x08, 0x13, 0x22]).decode("latin1")
            if decoded_str.startswith(prefix):
                decoded_str = decoded_str[len(prefix) :]
            suffix = bytes([0xD2, 0x01, 0x00]).decode("latin1")
            if decoded_str.endswith(suffix):
                decoded_str = decoded_str[: -len(suffix)]

            byte_arr = bytearray(decoded_str, "latin1")
            length = byte_arr[0]
            if length >= 0x80:
                decoded_str = decoded_str[2 : length + 1]
            else:
                decoded_str = decoded_str[1 : length + 1]
            if decoded_str.startswith("http://") or decoded_str.startswith("https://"):
                return decoded_str
        except Exception:
            return source_url
        return source_url

    def _decode_google_news_url(self, source_url: str) -> str:
        fast_decoded = self._decode_google_news_url_fast(source_url)
        if fast_decoded != source_url:
            return fast_decoded

        token = self._extract_google_article_token(source_url)
        if not token:
            return source_url

        locale = f"{self.region}:{self.language}"
        candidate_pages: list[str] = []
        for candidate in (
            self._with_google_news_locale(source_url),
            f"https://news.google.com/articles/{token}?hl={self.language}&gl={self.region}&ceid={quote_plus(locale)}",
        ):
            if candidate not in candidate_pages:
                candidate_pages.append(candidate)

        for page_url in candidate_pages:
            try:
                page_resp = self._request("GET", page_url)
                page_resp.raise_for_status()
                soup = BeautifulSoup(page_resp.text, "html.parser")
                node = soup.select_one("c-wiz > div[jscontroller]")
                if not node:
                    continue
                signature = node.get("data-n-a-sg", "")
                timestamp = node.get("data-n-a-ts", "")
                if not signature or not timestamp:
                    continue

                payload = [
                    "Fbv4je",
                    (
                        f'["garturlreq",[["X","X",["X","X"],null,null,1,1,"{locale}",null,1,null,null,null,null,null,0,1],'
                        f'"X","X",1,[1,1,1],1,1,null,0,0,null,0],"{token}",{timestamp},"{signature}"]'
                    ),
                ]
                batch_resp = self._request(
                    "POST",
                    "https://news.google.com/_/DotsSplashUi/data/batchexecute",
                    headers={"Content-Type": "application/x-www-form-urlencoded;charset=UTF-8"},
                    data=f"f.req={quote(json.dumps([[payload]]))}",
                )
                batch_resp.raise_for_status()
                parsed = json.loads(batch_resp.text.split("\n\n")[1])[:-2]
                decoded_url = json.loads(parsed[0][2])[1]
                if isinstance(decoded_url, str) and decoded_url.startswith(("http://", "https://")):
                    return decoded_url
            except Exception:
                continue
        return source_url

    def _fetch_publisher_snippet(self, article_url: str, title: str) -> str:
        parsed = urlparse(article_url)
        if parsed.scheme not in {"http", "https"}:
            return ""
        if _is_google_news_host(article_url):
            return ""
        try:
            resp = self._request("GET", article_url, allow_redirects=True)
            resp.raise_for_status()
            if _is_google_news_host(resp.url):
                return ""
            return _extract_article_snippet(resp.text, title)
        except Exception:
            return ""

    def _enrich_record(self, record: RawNewsRecord) -> RawNewsRecord:
        metadata = dict(record.metadata or {})
        snippet = _clean_snippet_text(record.snippet, record.title)
        final_url = record.url

        resolved_url = self._decode_google_news_url(record.url)
        if resolved_url and resolved_url != record.url:
            metadata["google_news_url"] = record.url
            metadata["resolved_url"] = resolved_url
            metadata["resolved_via"] = "google_news_decoder"
            final_url = resolved_url

        if _is_generic_google_snippet(snippet):
            snippet = ""

        if not snippet:
            snippet = self._fetch_publisher_snippet(final_url, record.title)
            if snippet:
                metadata["snippet_source"] = "publisher_article_excerpt"
        elif "snippet_source" not in metadata:
            metadata["snippet_source"] = "collector"

        if not snippet:
            metadata["snippet_source"] = "missing"

        return RawNewsRecord(
            collect_date=record.collect_date,
            article_id=record.article_id,
            article_date=record.article_date,
            source=record.source,
            url=final_url,
            title=record.title,
            snippet=snippet or None,
            doc_text=f"{record.title} [SEP] {snippet or ''}",
            query_used=record.query_used,
            lang=record.lang,
            fetched_at=record.fetched_at,
            author=record.author,
            publisher=record.publisher,
            metadata=metadata,
        )

    def _enrich_records(self, records: list[RawNewsRecord], notes: list[str]) -> list[RawNewsRecord]:
        if not records:
            return []

        needs_enrichment = [
            idx
            for idx, record in enumerate(records)
            if (not _clean_snippet_text(record.snippet, record.title))
            or urlparse(record.url).hostname == "news.google.com"
        ]
        if not needs_enrichment:
            return records

        enriched = list(records)
        max_workers = min(2, max(1, len(needs_enrichment)))
        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            future_map = {
                executor.submit(self._enrich_record, records[idx]): idx
                for idx in needs_enrichment
            }
            for future in as_completed(future_map):
                idx = future_map[future]
                try:
                    enriched[idx] = future.result()
                except Exception as exc:
                    notes.append(f"snippet_enrichment_failed:{records[idx].article_id}:{exc}")
        notes.append(
            f"snippet_populated_{sum(1 for record in enriched if _clean_snippet_text(record.snippet, record.title))}"
        )
        return enriched

    def _collect_via_rss(self, query, collect_date, lookback_days=1, max_results=None, dedup_on_url=True):
        lookback_days = max(0, int(lookback_days))
        range_start = collect_date - timedelta(days=lookback_days)
        range_end = collect_date
        query_after = range_start - timedelta(days=1)
        query_before = range_end + timedelta(days=1)

        collected = []
        notes = []
        seen_urls = set()
        seen_titles = set()

        query_with_range = f"{query} after:{query_after.isoformat()} before:{query_before.isoformat()}"
        params = {
            "q": query_with_range,
            "hl": self.language,
            "gl": self.region,
            "ceid": f"{self.region}:{self.language}",
        }
        notes.append(f"rss_request: https://news.google.com/rss/search?{params}")

        items = []
        for attempt in range(self.max_retries + 1):
            try:
                resp = self._session.get("https://news.google.com/rss/search", params=params, timeout=self.timeout_sec)
                if resp.status_code != 200:
                    notes.append(f"rss_status_{resp.status_code}_attempt_{attempt + 1}")
                    time.sleep(random.uniform(0.8, 1.6))
                    continue
                soup = BeautifulSoup(resp.text, "xml")
                items = soup.find_all("item")
                if items:
                    break
                notes.append(f"rss_no_items_found_attempt_{attempt + 1}")
                time.sleep(random.uniform(0.8, 1.6))
            except Exception as exc:
                notes.append(f"rss_error_attempt_{attempt + 1}_{exc}")
                time.sleep(random.uniform(0.8, 1.6))

        if not items:
            notes.append("rss_no_items_found")
            return [], {
                "query": query,
                "collected": 0,
                "collection_notes": notes,
            }

        for item in items:
            if max_results and len(collected) >= max_results:
                break

            title_tag = item.find("title")
            link_tag = item.find("link")
            source_tag = item.find("source")
            pub_date_tag = item.find("pubDate")
            desc_tag = item.find("description")

            title = title_tag.get_text(strip=True) if title_tag else ""
            url = link_tag.get_text(strip=True) if link_tag else ""
            source = source_tag.get_text(strip=True) if source_tag else None
            source_url = source_tag.get("url", "").strip() if source_tag else ""
            pub_date = pub_date_tag.get_text(strip=True) if pub_date_tag else ""
            description_html = desc_tag.get_text(strip=True) if desc_tag else ""

            if source and title.endswith(f" - {source}"):
                title = title[: -(len(source) + 3)].strip()

            description_text = ""
            if description_html:
                description_soup = BeautifulSoup(unescape(description_html), "html.parser")
                description_text = description_soup.get_text(" ", strip=True)
                if source:
                    description_text = re.sub(rf"\s*{re.escape(source)}\s*$", "", description_text).strip()
                description_text = _clean_snippet_text(description_text, title)

            if not title or not url:
                continue
            if dedup_on_url and url in seen_urls:
                continue
            if title in seen_titles:
                continue

            article_date = _parse_rss_pub_date(pub_date)

            seen_urls.add(url)
            seen_titles.add(title)
            collected.append(
                RawNewsRecord(
                    collect_date=collect_date.isoformat(),
                    article_id=str(len(collected) + 1),
                    article_date=article_date.isoformat() if article_date else None,
                    source=source,
                    url=url,
                    title=title,
                    snippet=description_text or None,
                    doc_text=f"{title} [SEP] {description_text or ''}",
                    query_used=query_with_range,
                    lang=self.language,
                    fetched_at=datetime.now().isoformat(),
                    metadata={
                        "collector_mode": "google_news_rss",
                        "rss_pub_date": pub_date,
                        "source_url": source_url or None,
                    },
                )
            )

        collected = self._enrich_records(collected, notes)
        notes.append(f"rss_items_found_{len(collected)}")
        return collected, {
            "query": query,
            "collected": len(collected),
            "collection_notes": notes,
        }

    def _collect_via_html(self, query, collect_date, lookback_days=1, max_results=None, dedup_on_url=True):
        lookback_days = max(0, int(lookback_days))
        range_start = collect_date - timedelta(days=lookback_days)
        range_end = collect_date
        query_after = range_start - timedelta(days=1)
        query_before = range_end + timedelta(days=1)
        
        collected = []
        notes = []
        seen_urls = set()
        seen_titles = set()

        query_with_range = f"{query} after:{query_after.isoformat()} before:{query_before.isoformat()}"
        encoded_query = quote_plus(query_with_range)
        cd_min = _fmt_cd(range_start)
        cd_max = _fmt_cd(range_end)

        start = 0
        
        while True:
            # URL 생성
            url = (
                f"https://www.google.com/search?q={encoded_query}&tbm=nws"
                f"&hl={self.language}&gl={self.region}"
                f"&tbs=sbd:1,cdr:1,cd_min:{cd_min},cd_max:{cd_max}"
                f"&start={start}"
            )
            notes.append(f"request_url: {url}")
            
            try:
                resp = self._session.get(url, timeout=self.timeout_sec)
                
                # 캡차 확인
                if any(marker in resp.text for marker in _CAPTCHA_MARKERS):
                    notes.append("captcha_detected")
                    LOGGER.warning("Google Captcha detected.")
                    break
                
                if resp.status_code != 200:
                    notes.append(f"status_{resp.status_code}")
                    break
            except Exception as e:
                notes.append(f"error_{str(e)}")
                break

            soup = BeautifulSoup(resp.text, "html.parser")
            
            # 카드 추출 시도
            cards = list(_iter_news_cards(soup))
            
            # 카드가 없으면 검색 결과 없음으로 간주
            if not cards:
                notes.append("no_cards_found")
                # 디버깅용: HTML 일부를 로그로 확인하고 싶을 때 사용
                # print(soup.prettify()[:1000]) 
                break

            new_items_on_page = 0
            for card in cards:
                if max_results and len(collected) >= max_results: break

                title, norm_url, source, date_text, snippet = _extract_card_data(card)

                # 제목이나 URL이 없으면 스킵
                if not title or not norm_url: 
                    continue
                
                if dedup_on_url and norm_url in seen_urls: continue
                if title in seen_titles: continue

                seen_urls.add(norm_url)
                seen_titles.add(title)

                article_date = parse_date_from_text(date_text or "", fallback=range_end)

                record = RawNewsRecord(
                    collect_date=collect_date.isoformat(),
                    article_id=str(len(collected) + 1),
                    article_date=article_date.isoformat() if article_date else None,
                    source=source,
                    url=norm_url,
                    title=title,
                    snippet=_clean_snippet_text(snippet, title) or None,
                    doc_text=f"{title} [SEP] {snippet or ''}",
                    query_used=query_with_range,
                    lang=self.language,
                    fetched_at=datetime.now().isoformat(),
                    metadata={"raw_date_text": date_text}
                )
                collected.append(record)
                new_items_on_page += 1

            if max_results and len(collected) >= max_results: break
            if new_items_on_page == 0: break
            
            start += 10
            # 봇 탐지 회피를 위한 지연 시간
            time.sleep(random.uniform(2.0, 4.0))

        collected = self._enrich_records(collected, notes)
        meta = {
            "query": query,
            "collected": len(collected),
            "collection_notes": notes,
        }
        return collected, meta

    def collect(self, query, collect_date, lookback_days=1, max_results=None, dedup_on_url=True):
        rss_records, rss_meta = self._collect_via_rss(
            query,
            collect_date,
            lookback_days=lookback_days,
            max_results=max_results,
            dedup_on_url=dedup_on_url,
        )
        if rss_records:
            return rss_records, rss_meta

        html_records, html_meta = self._collect_via_html(
            query,
            collect_date,
            lookback_days=lookback_days,
            max_results=max_results,
            dedup_on_url=dedup_on_url,
        )
        html_meta["collection_notes"] = list(rss_meta.get("collection_notes", [])) + list(html_meta.get("collection_notes", []))
        return html_records, html_meta
