"""Lexical Strategy checks reserved for controlled evaluation runs."""

from __future__ import annotations

import re
from typing import Any

from shared.llm_clients import compact_json


_RATING_LABEL_PATTERN = re.compile(
    r"(?<![A-Za-z])(?:buy|hold|sell)(?![A-Za-z])|(?:매수|보유|매도)",
    re.IGNORECASE,
)
_ACTION_PATTERN = re.compile(
    r"비중|진입|추격|관망|관찰|대기|기다|확인(?:한|\s*후|이)?|"
    r"확대|축소|늘리|줄이|감축|유지|접근|대응|재검토|우선"
)


def validate_experimental_prose(
    *,
    reader_text: dict[str, Any],
    current_response: str,
) -> None:
    """Apply lexical/action heuristics only in an explicit evaluation run."""

    if not _ACTION_PATTERN.search(current_response):
        raise ValueError(
            "decision.current_response must state a concrete present response, not only facts."
        )
    serialized_reader_text = compact_json(reader_text, sort_keys=True)
    label_match = _RATING_LABEL_PATTERN.search(serialized_reader_text)
    if label_match:
        raise ValueError(
            f"Recommendation label leaked into Strategy prose: {label_match.group(0)!r}"
        )
