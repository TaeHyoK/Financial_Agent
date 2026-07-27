from __future__ import annotations

import pytest

from shared.evidence_contracts import (
    SECONDARY_CONTEXT_USAGE,
    validate_secondary_context_assessments,
)


def test_secondary_context_keeps_primary_and_secondary_ids_separate() -> None:
    result = validate_secondary_context_assessments(
        [
            {
                "context_id": "CTX_001",
                "source_domain": "news",
                "effect": "corroborates",
                "statement": "가격 관찰과 같은 시기에 관련 사건이 확인됐다.",
                "primary_evidence_ids": ["YF_STOCK_RETURN_20D"],
                "secondary_evidence_ids": ["NEWS_RAW_1"],
                "usage": SECONDARY_CONTEXT_USAGE,
                "limitation": "인과관계는 확인할 수 없다.",
            }
        ],
        primary_evidence_ids={"YF_STOCK_RETURN_20D"},
        secondary_catalog={
            "NEWS_RAW_1": {
                "evidence_id": "NEWS_RAW_1",
                "domain": "news",
            }
        },
        allowed_source_domains={"news"},
        required_source_domains={"news"},
    )

    assert result[0]["usage"] == SECONDARY_CONTEXT_USAGE


def test_secondary_context_rejects_secondary_id_as_primary() -> None:
    with pytest.raises(ValueError, match="Primary evidence reused"):
        validate_secondary_context_assessments(
            [
                {
                    "context_id": "CTX_001",
                    "source_domain": "news",
                    "effect": "neutral",
                    "statement": "중립적이다.",
                    "primary_evidence_ids": ["YF_STOCK_RETURN_20D"],
                    "secondary_evidence_ids": ["YF_STOCK_RETURN_20D"],
                    "usage": SECONDARY_CONTEXT_USAGE,
                    "limitation": "",
                }
            ],
            primary_evidence_ids={"YF_STOCK_RETURN_20D"},
            secondary_catalog={
                "YF_STOCK_RETURN_20D": {
                    "evidence_id": "YF_STOCK_RETURN_20D",
                    "domain": "news",
                }
            },
            allowed_source_domains={"news"},
        )
