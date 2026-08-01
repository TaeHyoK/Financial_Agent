from __future__ import annotations

import json

from orchestration.final_report_evaluation_bundle import (
    assert_candidate_neutral,
    build_common_evidence_bundle,
    build_union_evidence_bundle,
    extract_visible_report,
)


def test_visible_report_excludes_non_visible_content_and_preserves_tables(tmp_path) -> None:
    report = tmp_path / "report.html"
    report.write_text(
        """
        <html><head><title>Fallback</title><style>.secret{display:none}</style></head>
        <body><main class="a4-sheet">
          <header><p class="report-name">테스트 보고서</p>
            <div class="meta-grid"><div><span>투자의견</span>Hold</div></div>
          </header>
          <section id="thesis"><h1>투자의견</h1><p>표시되는 근거입니다.</p>
            <p hidden>숨겨진 모델명 gpt-test</p>
            <table><thead><tr><th>항목</th><th>값</th></tr></thead>
              <tbody><tr><td>매출</td><td>100억원</td></tr></tbody></table>
          </section>
          <script>internal_metadata = true;</script>
        </main></body></html>
        """,
        encoding="utf-8",
    )

    visible = extract_visible_report(report)

    serialized = json.dumps(visible, ensure_ascii=False)
    assert visible["title"] == "테스트 보고서"
    assert visible["metadata"] == [{"label": "투자의견", "value": "Hold"}]
    assert "표시되는 근거입니다." in serialized
    assert "100억원" in serialized
    assert "gpt-test" not in serialized
    assert "internal_metadata" not in serialized


def test_evidence_bundle_removes_candidate_interpretation_and_article_excerpt(tmp_path) -> None:
    packet = tmp_path / "strategy_compact_packet_v2.json"
    packet.write_text(
        json.dumps(
            {
                "target_company": {
                    "company_name": "회사A",
                    "run_key": "회사A_20250101",
                    "as_of_date": "2025-01-01",
                },
                "selected_date_policy": "before_selected_date",
                "coverage_summary": {"card_counts": {"financial": 1}},
                "reader_limitations": [],
                "limitation_requirements": [],
                "cards": {
                    "financial.revenue": {
                        "domain": "financial",
                        "label": "매출",
                        "evidence_family": "financial_performance",
                        "observation_basis": "period_comparison",
                        "comparison_scope": "company_history",
                        "decision_use": "factor_eligible",
                        "primary_observation": {"revenue": 100},
                        "strategy_interpretation": "후보가 만든 해석",
                    },
                    "news.event": {
                        "domain": "news",
                        "label": "사건",
                        "primary_observation": {
                            "event_summary": "사건이 발생했다.",
                            "representative_excerpts": ["기사 원문"],
                        },
                    },
                },
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    bundle = build_common_evidence_bundle(packet)
    serialized = json.dumps(bundle, ensure_ascii=False)

    assert [card["card_key"] for card in bundle["cards"]] == [
        "financial.revenue",
        "news.event",
    ]
    assert "후보가 만든 해석" not in serialized
    assert "기사 원문" not in serialized
    assert "사건이 발생했다." in serialized
    assert_candidate_neutral(bundle)


def test_candidate_neutral_guard_rejects_model_metadata() -> None:
    try:
        assert_candidate_neutral({"nested": {"model_name": "candidate-model"}})
    except ValueError as exc:
        assert "model_name" in str(exc)
    else:  # pragma: no cover - assertion clarity
        raise AssertionError("candidate-specific metadata was not rejected")


def test_union_bundle_includes_both_packets_without_candidate_mapping(tmp_path) -> None:
    def packet(path, card_key, value):
        path.write_text(
            json.dumps(
                {
                    "target_company": {
                        "company_name": "회사A",
                        "run_key": "회사A_20250101",
                        "as_of_date": "2025-01-01",
                        "ticker": "000001.KS",
                    },
                    "selected_date_policy": "before_selected_date",
                    "cards": {
                        card_key: {
                            "domain": card_key.split(".", 1)[0],
                            "label": card_key,
                            "primary_observation": {"value": value},
                        }
                    },
                }
            ),
            encoding="utf-8",
        )

    first = tmp_path / "first.json"
    second = tmp_path / "second.json"
    packet(first, "financial.revenue", 100)
    packet(second, "news.event", "occurred")

    forward = build_union_evidence_bundle([first, second])
    reverse = build_union_evidence_bundle([second, first])
    serialized = json.dumps(forward, ensure_ascii=False)

    assert [card["card_key"] for card in forward["cards"]] == [
        "financial.revenue",
        "news.event",
    ]
    assert forward["bundle_sha256"] == reverse["bundle_sha256"]
    assert "candidate_a" not in serialized.lower()
    assert "candidate_b" not in serialized.lower()
