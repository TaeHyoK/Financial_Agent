from __future__ import annotations

import pytest

from shared.evidence_cards import (
    card_content_sha256,
    validate_provenance_map,
    validate_self_contained_card,
)


SECTIONS = {"investment_thesis", "financial_view"}


def _card() -> dict:
    return {
        "card_key": "financial.same_period_trend",
        "domain": "financial",
        "card_type": "same_period_trend",
        "label": "동일기간 재무 추세",
        "allowed_sections": ["investment_thesis", "financial_view"],
        "evidence_role": "primary",
        "eligibility": "eligible",
        "primary_observation": {"current_revenue": 100, "previous_revenue": 80},
        "secondary_context": [
            {
                "source_domain": "news",
                "effect": "neutral",
                "usage": "framing_only",
                "statement": "관련 뉴스의 재무 기여는 아직 계량되지 않았다.",
            }
        ],
        "reader_limitations": [],
        "machine_blockers": [],
    }


def test_card_and_provenance_hash_contract() -> None:
    card = _card()
    validate_self_contained_card(card, allowed_section_names=SECTIONS)
    validate_provenance_map(
        {card["card_key"]: card},
        {
            "cards": {
                card["card_key"]: {
                    "source_evidence_ids": ["E001"],
                    "source_paths": ["financial.financial_trends.current_vs_same_period"],
                    "strategy_card_sha256": card_content_sha256(card),
                }
            }
        },
    )


def test_llm_card_rejects_raw_evidence_id() -> None:
    card = _card()
    card["primary_observation"]["evidence"] = "NEWS_RAW_2025-10-01_1"

    with pytest.raises(ValueError, match="Opaque evidence ID leaked"):
        validate_self_contained_card(card, allowed_section_names=SECTIONS)


def test_provenance_rejects_changed_card_content() -> None:
    card = _card()
    old_hash = card_content_sha256(card)
    card["primary_observation"]["current_revenue"] = 101

    with pytest.raises(ValueError, match="hash mismatch"):
        validate_provenance_map(
            {card["card_key"]: card},
            {
                "cards": {
                    card["card_key"]: {
                        "source_evidence_ids": [],
                        "source_paths": [],
                        "strategy_card_sha256": old_hash,
                    }
                }
            },
        )
