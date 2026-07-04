"""Editorial polish layer for Writer Agent contracts."""

from __future__ import annotations

from copy import deepcopy
from typing import Any


TEXT_REPLACEMENTS = {
    "Buy 전환": "투자의견 상향",
    "Buy로 높이기": "적극적 비중 확대",
    "Buy 논의": "투자의견 상향 논의",
    "Buy로 전환": "공격적 매수 판단으로 이동",
    "투자 의견을 적극적 비중 확대에는": "투자의견을 적극적 비중 확대로 조정하기에는",
    "투자 의견을 Buy로 높이기에는": "투자의견을 적극적 비중 확대로 조정하기에는",
    "Valuation Agent not applied": "가격 판단 미제시",
    "Valuation Agent 미적용": "가격 판단 미제시",
    "Strategy Agent": "투자 판단",
    "Writer Agent": "리포트 작성 과정",
    "Visualization Agent": "시각화 과정",
    "Strategy": "투자 전략",
    "valuation": "평가",
}

SKIP_REPLACEMENT_KEYS = {
    "source_files",
    "source_trace",
    "source_file",
    "source_field",
    "figure_path",
    "figure_path_png",
    "html_img_path",
    "asset_path_pdf",
    "asset_path_png",
    "asset_abs_path_pdf",
    "asset_abs_path_png",
}


def polish_contract(contract: dict[str, Any]) -> dict[str, Any]:
    """Remove internal agent language and normalize repeated expressions."""

    polished = deepcopy(contract)
    polished["report_metadata"]["valuation_status"] = "가격 판단 미제시"
    polished["report_metadata"]["report_type_display"] = "기업분석 리포트 초안"
    _walk_and_replace(polished)
    return polished


def _walk_and_replace(value: Any, key_name: str = "") -> Any:
    if key_name in SKIP_REPLACEMENT_KEYS:
        return value
    if isinstance(value, dict):
        for key, item in list(value.items()):
            value[key] = _walk_and_replace(item, key)
        return value
    if isinstance(value, list):
        for index, item in enumerate(value):
            value[index] = _walk_and_replace(item, key_name)
        return value
    if isinstance(value, str):
        return _replace_text(value)
    return value


def _replace_text(text: str) -> str:
    for source, target in TEXT_REPLACEMENTS.items():
        text = text.replace(source, target)
    return text
