"""Small deterministic OpenDART API client.

This module intentionally uses only the standard library. It does not use any
agent framework, LLM, browser automation, OCR, embedding model, or vector DB.
"""

from __future__ import annotations

import io
import json
import logging
import time
import urllib.parse
import urllib.request
import zipfile
from typing import Any
from urllib.error import HTTPError, URLError

try:
    from .models import Filing
except ImportError:  # pragma: no cover - supports direct script execution
    from models import Filing


DART_BASE_URL = "https://opendart.fss.or.kr/api"


class DartApiError(RuntimeError):
    """Raised when DART returns an error response or an invalid payload."""


class DartClient:
    """Minimal OpenDART client with deterministic retry behavior."""

    def __init__(
        self,
        api_key: str,
        *,
        max_retries: int = 2,
        timeout: int = 30,
        logger: logging.Logger | None = None,
    ) -> None:
        if not api_key:
            raise DartApiError("DART_API_KEY is required.")
        self.api_key = api_key
        self.max_retries = max(0, int(max_retries))
        self.timeout = timeout
        self.logger = logger or logging.getLogger(__name__)

    def list_filings(
        self,
        *,
        corp_code: str,
        bgn_de: str,
        end_de: str,
        pblntf_detail_ty: str,
        page_count: int = 100,
        max_pages: int = 20,
    ) -> list[Filing]:
        """Fetch DART filings for one disclosure detail type.

        OpenDART's regular report detail filter works when
        ``pblntf_detail_ty`` is supplied without the broader ``pblntf_ty``.
        """

        filings: list[Filing] = []
        total_page = 1
        page_no = 1

        while page_no <= total_page and page_no <= max_pages:
            params = {
                "crtfc_key": self.api_key,
                "corp_code": corp_code,
                "bgn_de": bgn_de,
                "end_de": end_de,
                "pblntf_detail_ty": pblntf_detail_ty,
                "page_no": str(page_no),
                "page_count": str(page_count),
                "sort": "date",
                "sort_mth": "desc",
            }
            payload = self._get_json(f"{DART_BASE_URL}/list.json", params)
            status = str(payload.get("status") or "")
            message = str(payload.get("message") or "")

            if status == "013":
                return filings
            if status != "000":
                raise DartApiError(f"DART list.json failed: {status} {message}")

            try:
                total_page = int(payload.get("total_page") or 1)
            except (TypeError, ValueError):
                total_page = 1

            items = payload.get("list") or []
            if not isinstance(items, list):
                raise DartApiError("DART list.json returned an invalid list field.")
            filings.extend(Filing.from_api_item(item) for item in items if isinstance(item, dict))
            page_no += 1

        return filings

    def fetch_document_xml(self, *, rcept_no: str) -> str:
        """Fetch and unzip one DART document.xml payload."""

        params = {"crtfc_key": self.api_key, "rcept_no": rcept_no}
        payload = self._get_bytes(f"{DART_BASE_URL}/document.xml", params)

        if payload.startswith(b"PK"):
            with zipfile.ZipFile(io.BytesIO(payload)) as zf:
                xml_names = sorted(name for name in zf.namelist() if name.lower().endswith(".xml"))
                if not xml_names:
                    raise DartApiError(f"DART document {rcept_no} zip has no XML file.")
                with zf.open(xml_names[0]) as xml_file:
                    return xml_file.read().decode("utf-8", errors="ignore")

        text = payload.decode("utf-8", errors="ignore")
        if "<status>" in text and "<message>" in text and "<DOCUMENT" not in text.upper():
            raise DartApiError(f"DART document.xml returned an error payload for {rcept_no}: {text[:300]}")
        return text

    def _get_json(self, url: str, params: dict[str, str]) -> dict[str, Any]:
        raw = self._get_bytes(url, params)
        try:
            data = json.loads(raw.decode("utf-8", errors="ignore"))
        except json.JSONDecodeError as exc:
            raise DartApiError(f"Invalid JSON from DART: {exc}") from exc
        if not isinstance(data, dict):
            raise DartApiError("Invalid JSON object from DART.")
        return data

    def _get_bytes(self, url: str, params: dict[str, str]) -> bytes:
        query = urllib.parse.urlencode(params)
        full_url = f"{url}?{query}"
        last_error: Exception | None = None

        for attempt in range(self.max_retries + 1):
            request = urllib.request.Request(
                full_url,
                headers={"User-Agent": "Financial-Agent-DART-Collector/1.0"},
            )
            try:
                with urllib.request.urlopen(request, timeout=self.timeout) as response:  # nosec - OpenDART URL
                    return response.read()
            except (HTTPError, URLError, TimeoutError, OSError) as exc:
                last_error = exc
                if attempt >= self.max_retries:
                    break
                sleep_seconds = 0.5 * (attempt + 1)
                self.logger.warning("DART request failed, retrying in %.1fs: %s", sleep_seconds, exc)
                time.sleep(sleep_seconds)

        raise DartApiError(f"DART request failed after retries: {last_error}") from last_error
