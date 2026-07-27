from __future__ import annotations

import json

from orchestration.ablation_adapters import build_parser, run_adapter


def test_news_no_sy_adapter_preserves_raw_claims_without_validation(tmp_path) -> None:
    source = tmp_path / "news_agent_handoff.json"
    source.write_text(
        json.dumps(
            {
                "output": {
                    "analysis_blocks": {
                        "news_only": {
                            "positive_signals": [
                                {
                                    "claim": "신규 사업을 발표했다.",
                                    "evidence_ids": ["NEWS_RAW_2025-01-01_1"],
                                    "event_status": "announced",
                                    "company_specificity": "direct",
                                    "materiality_status": "plausible_unquantified",
                                    "financial_link_status": "not_observed",
                                }
                            ],
                            "negative_signals": [],
                            "key_risks": [],
                            "uncertainties": [],
                        }
                    }
                }
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    verified = tmp_path / "news_agent_verified_handoff.json"
    validation = tmp_path / "sy_claim_validations.json"
    args = build_parser().parse_args(
        [
            "--domain",
            "news",
            "--input",
            str(source),
            "--verified-report",
            str(verified),
            "--validation",
            str(validation),
        ]
    )

    result = run_adapter(args)

    claim = result["claim_validations"][0]
    assert result["verification_mode"] == "sy_bypassed"
    assert claim["evidence_use"] == "strong"
    assert claim["support_level"] == "unverified"
    assert claim["section"] == "analysis_blocks.news_only.positive_signals[0]"
    assert json.loads(verified.read_text(encoding="utf-8"))["report_status"] == "sy_bypassed_ablation"
