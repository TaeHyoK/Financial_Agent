from __future__ import annotations

import io
import json
import zipfile
from datetime import date

import pytest

from orchestration.company_resolver import (
    CompanyResolutionError,
    build_resolved_company_config,
    parse_dart_company_directory,
    resolve_company_identity,
    resolve_company_identity_by_stock_code,
    resolve_news_date_range,
    resolve_naver_market,
    resolve_yahoo_market,
)
from orchestration.config import load_run_config


DIRECTORY = [
    {
        "corp_code": "00878696",
        "company_name": "SK바이오팜",
        "stock_code": "326030",
        "modify_date": "20250101",
    },
    {
        "corp_code": "00146214",
        "company_name": "일성아이에스",
        "stock_code": "003120",
        "modify_date": "20250102",
    },
]


def test_parse_zipped_dart_company_directory() -> None:
    xml = b"""<?xml version='1.0' encoding='UTF-8'?>
    <result><list><corp_code>00878696</corp_code><corp_name>SKBIO</corp_name>
    <stock_code>326030</stock_code><modify_date>20250101</modify_date></list></result>"""
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("CORPCODE.xml", xml)

    result = parse_dart_company_directory(buffer.getvalue())

    assert result == [
        {
            "corp_code": "00878696",
            "company_name": "SKBIO",
            "stock_code": "326030",
            "modify_date": "20250101",
        }
    ]


def test_resolve_company_name_and_market_suffix() -> None:
    identity = resolve_company_identity(
        "(주) SK바이오팜",
        selected_date="20251031",
        directory=DIRECTORY,
        market_resolver=lambda stock_code, selected: "KOSPI",
    )

    assert identity.corp_code == "00878696"
    assert identity.stock_code == "326030"
    assert identity.ticker == "326030.KS"


def test_resolve_latin_initials_against_korean_dart_legal_name() -> None:
    directory = [
        {
            "corp_code": "00878696",
            "company_name": "에스케이바이오팜",
            "stock_code": "326030",
            "modify_date": "20230329",
        }
    ]

    identity = resolve_company_identity(
        "SK바이오팜",
        selected_date="20251031",
        directory=directory,
        market_resolver=lambda stock_code, selected: "KOSPI",
    )

    assert identity.company_name == "SK바이오팜"
    assert identity.source["dart_corp_name"] == "에스케이바이오팜"


def test_resolve_peer_by_stock_code() -> None:
    identity = resolve_company_identity_by_stock_code(
        "3120",
        selected_date="20251031",
        directory=DIRECTORY,
        market_resolver=lambda stock_code, selected: "KOSPI",
    )

    assert identity.company_name == "일성아이에스"
    assert identity.ticker == "003120.KS"


def test_ambiguous_normalized_company_name_fails() -> None:
    duplicated = [
        *DIRECTORY,
        {
            "corp_code": "99999999",
            "company_name": "주식회사 SK바이오팜",
            "stock_code": "123456",
            "modify_date": "20250103",
        },
    ]

    with pytest.raises(CompanyResolutionError, match="ambiguous"):
        resolve_company_identity(
            "SK바이오팜",
            selected_date="20251031",
            directory=duplicated,
            market_resolver=lambda stock_code, selected: "KOSPI",
        )


def test_news_window_ends_before_preopen_selected_date() -> None:
    start, end = resolve_news_date_range(date(2025, 10, 31), "1m")

    assert start == date(2025, 10, 1)
    assert end == date(2025, 10, 30)


def test_resolved_config_uses_existing_per_company_contract() -> None:
    identity = resolve_company_identity(
        "SK바이오팜",
        selected_date="20251031",
        directory=DIRECTORY,
        market_resolver=lambda stock_code, selected: "KOSPI",
    )

    config = build_resolved_company_config(identity, selected_date="20251031", news_window="1m")

    assert config["company_code"] == "00878696"
    assert config["date_range"] == "20251001-20251030"
    assert config["selected_date_policy"] == "before_market_open"


def test_existing_config_is_clamped_before_selected_date(tmp_path) -> None:
    path = tmp_path / "company.json"
    path.write_text(
        json.dumps(
            {
                "company_code": "00878696",
                "company_name": "SK바이오팜",
                "ticker": "326030.KS",
                "date_range": "20251001-20251031",
                "selected_date": "20251031",
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    config = load_run_config(path)

    assert config.requested_end_date == "20251031"
    assert config.end_date == "20251030"
    assert config.information_cutoff_date == "20251030"
    assert config.effective_date_range == "20251001-20251030"


def test_yahoo_market_fallback_chooses_only_suffix_with_history(monkeypatch) -> None:
    class FakeResponse:
        def __init__(self, payload: dict) -> None:
            self.payload = payload

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def read(self) -> bytes:
            return json.dumps(self.payload).encode("utf-8")

    def fake_urlopen(request, timeout):
        del timeout
        has_history = ".KS?" in request.full_url
        return FakeResponse(
            {
                "chart": {
                    "result": [{"timestamp": [1, 2]}] if has_history else None,
                    "error": None if has_history else {"code": "Not Found"},
                }
            }
        )

    monkeypatch.setattr("orchestration.company_resolver.urllib.request.urlopen", fake_urlopen)

    assert resolve_yahoo_market("326030", date(2025, 10, 31)) == "KOSPI"


def test_naver_market_fallback_reads_item_identity_header(monkeypatch) -> None:
    class Headers:
        @staticmethod
        def get_content_charset():
            return "utf-8"

    class FakeResponse:
        headers = Headers()

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        @staticmethod
        def read() -> bytes:
            return '<img src="btn_kosdaq.gif" alt="코스닥" class="kosdaq">'.encode("utf-8")

    monkeypatch.setattr(
        "orchestration.company_resolver.urllib.request.urlopen",
        lambda request, timeout: FakeResponse(),
    )

    assert resolve_naver_market("123456") == "KOSDAQ"
