"""Tests for deterministic peer resolution and comparison."""

from __future__ import annotations

import json
from pathlib import Path

from Agent_Team.Competitor_Agent.identity import RunIdentity
from Agent_Team.Competitor_Agent.peer_comparison import _valuation_metrics, generate_peer_comparison
from Agent_Team.Competitor_Agent.peer_resolver import (
    build_fg000_ajax_request,
    build_industry_analysis_url,
    extract_company_iframe_url,
    extract_naver_market_cap_100m_krw,
    resolve_naver_peer,
    select_peer_from_fg000,
)


def test_discovers_wisereport_industry_and_ajax_urls() -> None:
    iframe = extract_company_iframe_url(
        '<iframe id="coinfo_cp" src="https://navercomp.wisereport.co.kr/v2/company/c1010001.aspx?cmp_cd=326030"></iframe>',
        page_url="https://finance.naver.com/item/coinfo.naver?code=326030",
    )
    industry = build_industry_analysis_url(iframe, stock_code="326030")
    ajax_url, params = build_fg000_ajax_request(
        '<select id="finGubun"><option value="MAIN" selected>main</option></select>',
        industry_url=industry,
        stock_code="326030",
    )

    assert industry == "https://navercomp.wisereport.co.kr/v2/company/c1060001.aspx?cmp_cd=326030"
    assert ajax_url == "https://navercomp.wisereport.co.kr/v2/company/ajax/cF6001.aspx"
    assert params["sec_cd"] == "FG000"
    assert params["finGubun"] == "MAIN"


def test_fg000_fixture_selects_only_ilsung_is() -> None:
    fixture = Path(__file__).resolve().parent / "fixtures" / "naver_fg000_326030.json"
    payload = json.loads(fixture.read_text(encoding="utf-8"))

    result = select_peer_from_fg000(payload, target_stock_code="326030")

    assert result["status"] == "selected"
    assert result["selected_peer"]["stock_code"] == "003120"
    assert result["selected_peer"]["company_name"] == "일성아이에스"
    assert len(result["candidates"]) == 4


def test_formatted_fg000_market_caps_are_parsed() -> None:
    fixture = Path(__file__).resolve().parent / "fixtures" / "naver_fg000_326030.json"
    payload = json.loads(fixture.read_text(encoding="utf-8"))
    payload["oDt_header"][0]["MKT_VAL"] = "63,120.4795"

    result = select_peer_from_fg000(payload, target_stock_code="326030")

    assert result["status"] == "selected"
    assert result["target"]["market_cap_100m_krw"] == 63_120.4795
    assert result["selection_basis"]["target_market_cap_source"] == "fg000_header"


def test_nonpositive_target_market_cap_is_treated_as_missing() -> None:
    fixture = Path(__file__).resolve().parent / "fixtures" / "naver_fg000_326030.json"
    payload = json.loads(fixture.read_text(encoding="utf-8"))
    payload["oDt_header"][0]["MKT_VAL"] = 0

    result = select_peer_from_fg000(payload, target_stock_code="326030")

    assert result["status"] == "peer_unavailable"
    assert result["reason"] == "target_market_cap_missing"
    assert result["selected_peer"] == {}


def test_missing_fg000_target_market_cap_uses_naver_item_fixture() -> None:
    fixtures = Path(__file__).resolve().parent / "fixtures"
    payload = json.loads(
        (fixtures / "naver_fg000_326030_missing_target_cap.json").read_text(encoding="utf-8")
    )
    item_html = (fixtures / "naver_item_main_326030.html").read_text(encoding="utf-8")
    target_market_cap = extract_naver_market_cap_100m_krw(item_html)

    result = select_peer_from_fg000(
        payload,
        target_stock_code="326030",
        target_market_cap_100m_krw=target_market_cap,
    )

    assert target_market_cap == 63_120
    assert result["status"] == "selected"
    assert result["selected_peer"]["stock_code"] == "003120"
    assert result["selection_basis"]["target_market_cap_source"] == "naver_item_main"


def test_resolver_fetches_item_page_only_for_missing_target_market_cap(monkeypatch) -> None:
    fixtures = Path(__file__).resolve().parent / "fixtures"
    payload_text = (fixtures / "naver_fg000_326030_missing_target_cap.json").read_text(
        encoding="utf-8"
    )
    item_html = (fixtures / "naver_item_main_326030.html").read_text(encoding="utf-8")
    requested_urls: list[str] = []

    def fake_fetch(url: str, **_kwargs) -> str:
        requested_urls.append(url)
        if "coinfo.naver" in url:
            return (
                '<iframe id="coinfo_cp" '
                'src="https://navercomp.wisereport.co.kr/v2/company/c1010001.aspx?cmp_cd=326030">'
                "</iframe>"
            )
        if url.endswith("c1060001.aspx?cmp_cd=326030"):
            return '<select id="finGubun"><option value="MAIN" selected>main</option></select>'
        if "cF6001.aspx" in url:
            return payload_text
        if "item/main.naver" in url:
            return item_html
        raise AssertionError(f"Unexpected URL: {url}")

    monkeypatch.setattr("Agent_Team.Competitor_Agent.peer_resolver._fetch_text", fake_fetch)

    result = resolve_naver_peer("326030")

    assert result["status"] == "selected"
    assert result["selected_peer"]["stock_code"] == "003120"
    assert result["target_market_cap_fallback"]["status"] == "used"
    assert sum("item/main.naver" in url for url in requested_urls) == 1


def test_resolver_fetches_all_missing_fg000_market_caps_and_selects_closest_peer(
    monkeypatch,
) -> None:
    fixtures = Path(__file__).resolve().parent / "fixtures"
    payload = json.loads(
        (fixtures / "naver_fg000_326030.json").read_text(encoding="utf-8")
    )
    for row in payload["oDt_header"]:
        row["MKT_VAL"] = None
    payload_text = json.dumps(payload, ensure_ascii=False)
    market_caps = {
        "326030": "64,139",
        "330350": "846",
        "299170": "521",
        "204840": "631",
        "003120": "2,780",
    }
    requested_item_codes: list[str] = []

    def fake_fetch(url: str, **_kwargs) -> str:
        if "coinfo.naver" in url:
            return (
                '<iframe id="coinfo_cp" '
                'src="https://navercomp.wisereport.co.kr/v2/company/c1010001.aspx?cmp_cd=326030">'
                "</iframe>"
            )
        if url.endswith("c1060001.aspx?cmp_cd=326030"):
            return '<select id="finGubun"><option value="MAIN" selected>main</option></select>'
        if "cF6001.aspx" in url:
            return payload_text
        if "item/main.naver" in url:
            code = url.split("code=", 1)[1].split("&", 1)[0]
            requested_item_codes.append(code)
            return f'<em id="_market_sum">{market_caps[code]}</em>'
        raise AssertionError(f"Unexpected URL: {url}")

    monkeypatch.setattr("Agent_Team.Competitor_Agent.peer_resolver._fetch_text", fake_fetch)

    result = resolve_naver_peer("326030")

    assert result["status"] == "selected"
    assert result["selected_peer"]["stock_code"] == "003120"
    assert result["selection_basis"]["target_market_cap_source"] == "naver_item_main"
    assert result["selection_basis"]["selected_peer_market_cap_source"] == "naver_item_main"
    assert result["target_market_cap_fallback"]["status"] == "used"
    assert result["candidate_market_cap_fallback"]["status"] == "used"
    assert result["candidate_market_cap_fallback"]["attempted_count"] == 4
    assert result["candidate_market_cap_fallback"]["available_count"] == 4
    assert requested_item_codes == ["326030", "330350", "299170", "204840", "003120"]


def test_no_peer_is_selected_when_both_target_market_cap_sources_are_missing(monkeypatch) -> None:
    fixtures = Path(__file__).resolve().parent / "fixtures"
    payload_text = (fixtures / "naver_fg000_326030_missing_target_cap.json").read_text(
        encoding="utf-8"
    )

    def fake_fetch(url: str, **_kwargs) -> str:
        if "coinfo.naver" in url:
            return (
                '<iframe id="coinfo_cp" '
                'src="https://navercomp.wisereport.co.kr/v2/company/c1010001.aspx?cmp_cd=326030">'
                "</iframe>"
            )
        if url.endswith("c1060001.aspx?cmp_cd=326030"):
            return '<select id="finGubun"><option value="MAIN" selected>main</option></select>'
        if "cF6001.aspx" in url:
            return payload_text
        if "item/main.naver" in url:
            return "<html><body>market cap unavailable</body></html>"
        raise AssertionError(f"Unexpected URL: {url}")

    monkeypatch.setattr("Agent_Team.Competitor_Agent.peer_resolver._fetch_text", fake_fetch)

    result = resolve_naver_peer("326030")

    assert result["status"] == "peer_unavailable"
    assert result["reason"] == "target_market_cap_missing"
    assert result["selected_peer"] == {}
    assert result["target_market_cap_fallback"]["status"] == "failed"


def test_missing_target_returns_explicit_unavailable_status() -> None:
    result = select_peer_from_fg000(
        {"oDt_header": [{"SEQ": 1, "CMP_CD": "003120", "CMP_KOR": "일성아이에스", "MKT_VAL": 100}]},
        target_stock_code="326030",
    )

    assert result["status"] == "peer_unavailable"
    assert result["reason"] == "target_missing_from_fg000_response"


def test_peer_valuation_uses_calculated_date_and_keeps_direct_date() -> None:
    result = _valuation_metrics(
        {
            "valuation_snapshot": {
                "calculated_from_close_and_dart": {
                    "as_of_date": "2025-10-30",
                    "metrics": {
                        "market_cap": {"value": 9_000_000_000_000},
                        "trailing_pe": {"value": 27.5},
                        "price_to_book": {"value": 13.4},
                        "price_to_sales": {"value": 14.7},
                    },
                },
                "direct_yfinance": {
                    "latest_period": {
                        "valuation_date": "2025-09-30",
                        "metrics": {
                            "enterprise_value": {"value": 7_700_000_000_000},
                            "enterprise_value_to_revenue": {"value": 11.45},
                            "enterprise_value_to_ebitda": {"value": 44.16},
                        },
                    }
                },
            }
        }
    )

    assert result["calculated_as_of_date"] == "2025-10-30"
    assert result["market_cap_100m_krw"] == 90_000
    assert result["trailing_pe"] == 27.5
    assert result["direct_valuation_date"] == "2025-09-30"


def test_peer_comparison_writes_only_structured_dataset(tmp_path) -> None:
    target_run = "target_20251031"
    peer_run = "peer_20251031"
    for run_key in (target_run, peer_run):
        _write_json(tmp_path / "Financial" / run_key / "final_report.json", {"detailed_analysis": {}})
        market = tmp_path / "Y_Finance" / run_key / "market_full_dataset.csv"
        market.parent.mkdir(parents=True, exist_ok=True)
        market.write_text(
            "date,stock_return_5d,stock_return_20d,stock_return_60d,stock_excess_return_20d,stock_relative_strength_60,stock_volume_ratio_20\n"
            "2025-10-30,0.01,0.02,0.03,0.01,0.02,1.1\n",
            encoding="utf-8",
        )
        _write_json(tmp_path / "Y_Finance" / run_key / "final_report.json", {})

    paths = generate_peer_comparison(
        target=RunIdentity(run_key=target_run, company_name="target", selected_date="20251031"),
        peer_run_keys=[peer_run],
        output_root=tmp_path,
    )

    assert paths.dataset_json.exists()
    assert not (paths.dataset_json.parent / "peer_positioning_summary.json").exists()
    assert not (paths.dataset_json.parent / "peer_comparison_summary.md").exists()
    payload = json.loads(paths.dataset_json.read_text(encoding="utf-8"))
    assert [row["peer_group"] for row in payload["metrics"]] == ["target", "domestic_peer"]
    assert "created_at" not in payload


def _write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
