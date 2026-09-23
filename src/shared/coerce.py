"""Value coercion helpers shared by the agent modules."""

from __future__ import annotations

import math
import re
from typing import Any


def as_dict(value: Any) -> dict[str, Any]:
    """Return the value when it is a dict, otherwise an empty dict."""

    return value if isinstance(value, dict) else {}


def as_list(value: Any) -> list[Any]:
    """Return the value when it is a list, otherwise an empty list."""

    return value if isinstance(value, list) else []


def as_text_list(value: Any) -> list[str]:
    """Return the stripped string items of a list, dropping blanks."""

    return [str(item).strip() for item in as_list(value) if str(item).strip()]


def is_finite_number(value: Any) -> bool:
    """Return True for a real numeric value that is neither a bool nor NaN/inf."""

    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(float(value))


def iso_date(value: str) -> str:
    """Return YYYY-MM-DD when the value holds eight digits, otherwise the raw text."""

    digits = re.sub(r"\D", "", value or "")
    if len(digits) == 8:
        return f"{digits[:4]}-{digits[4:6]}-{digits[6:]}"
    return str(value or "")
