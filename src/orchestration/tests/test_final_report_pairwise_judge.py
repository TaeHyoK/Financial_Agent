from __future__ import annotations

import json

from orchestration.final_report_evaluation_metrics import AXES
from orchestration.final_report_pairwise_judge import (
    ERROR_TAGS,
    build_judge_request,
    judge_response_format,
)


def test_judge_schema_is_strict_and_enumerates_supplied_cards() -> None:
    response_format = judge_response_format(["news.event", "financial.revenue"])
    schema = response_format["json_schema"]["schema"]
    axis = schema["properties"]["axes"]["properties"][AXES[0]]

    assert response_format["json_schema"]["strict"] is True
    assert schema["additionalProperties"] is False
    assert set(schema["properties"]["axes"]["properties"]) == set(AXES)
    assert axis["additionalProperties"] is False
    assert axis["properties"]["winner"]["enum"] == ["A", "B", "tie"]
    assert axis["properties"]["supporting_card_keys"]["items"]["enum"] == [
        "financial.revenue",
        "news.event",
    ]
    assert axis["properties"]["candidate_a_error_tags"]["items"]["enum"] == list(
        ERROR_TAGS
    )


def test_judge_request_contains_no_condition_or_model_identity_in_candidates(tmp_path) -> None:
    prompt = tmp_path / "prompt.md"
    prompt.write_text("후보를 블라인드 평가하라.", encoding="utf-8")
    evidence = {
        "cards": [{"card_key": "financial.revenue", "primary_observation": {"value": 1}}]
    }
    request = build_judge_request(
        candidate_a={"title": "보고서", "sections": []},
        candidate_b={"title": "보고서", "sections": []},
        evidence_bundle=evidence,
        model="gpt-5.4",
        prompt_path=prompt,
        candidate_a_available_card_keys=["financial.revenue"],
        candidate_b_available_card_keys=[],
    )
    user_payload = json.loads(request["messages"][1]["content"])

    assert "condition" not in json.dumps(user_payload, ensure_ascii=False).lower()
    assert "gpt-5.4" not in json.dumps(user_payload, ensure_ascii=False)
    assert user_payload["evaluation_contract"]["candidate_evidence_access"] == {
        "A": ["financial.revenue"],
        "B": [],
    }
    assert "temperature" not in request
    assert request["max_completion_tokens"] == 8000


def test_union_blind_request_omits_candidate_access_metadata(tmp_path) -> None:
    prompt = tmp_path / "prompt.md"
    prompt.write_text("정보량을 우대하지 말고 블라인드 평가하라.", encoding="utf-8")
    evidence = {
        "cards": [
            {"card_key": "financial.revenue", "primary_observation": {"value": 1}},
            {"card_key": "peer.profitability", "primary_observation": {"value": 2}},
        ]
    }

    request = build_judge_request(
        candidate_a={"title": "보고서 A", "sections": []},
        candidate_b={"title": "보고서 B", "sections": []},
        evidence_bundle=evidence,
        model="gpt-5.4",
        prompt_path=prompt,
        candidate_a_available_card_keys=["financial.revenue", "peer.profitability"],
        candidate_b_available_card_keys=["financial.revenue"],
        candidate_a_evidence_bundle={"cards": [{"card_key": "peer.profitability"}]},
        candidate_b_evidence_bundle={"cards": []},
        evidence_scope="union_blind",
    )
    user_payload = json.loads(request["messages"][1]["content"])

    assert "candidate_accessible_evidence" not in user_payload
    assert "candidate_evidence_access" not in user_payload["evaluation_contract"]
    assert user_payload["evaluation_contract"]["evidence_scope"] == "union_blind"
    assert user_payload["evaluation_contract"]["do_not_penalize_unused_cards"] is True
    assert user_payload["evaluation_contract"]["do_not_reward_raw_information_quantity"] is True
