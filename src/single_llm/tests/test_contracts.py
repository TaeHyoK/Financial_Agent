from __future__ import annotations

from single_llm.contracts import REPORT_VERSION, single_llm_response_format


def test_response_schema_is_strict_and_evidence_bounded() -> None:
    response_format = single_llm_response_format(
        evidence_ids=["FIN_TARGET", "NEWS_TARGET"],
        company_name="테스트기업",
        selected_date="2025-10-31",
        decision_horizon="1개월",
    )

    assert response_format["type"] == "json_schema"
    assert response_format["json_schema"]["strict"] is True
    schema = response_format["json_schema"]["schema"]
    assert schema["additionalProperties"] is False
    assert schema["properties"]["report_version"]["enum"] == [REPORT_VERSION]
    evidence_enum = (
        schema["properties"]["key_evidence"]["items"]["properties"]
        ["evidence_ids"]["items"]["enum"]
    )
    assert evidence_enum == ["FIN_TARGET", "NEWS_TARGET"]
