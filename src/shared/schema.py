"""Structured Outputs schema fragments shared by the LLM-calling modules."""

from __future__ import annotations

from typing import Any


def strict_object(properties: dict[str, Any]) -> dict[str, Any]:
    """Return an object schema that requires every declared property."""

    return {
        "type": "object",
        "properties": properties,
        "required": list(properties),
        "additionalProperties": False,
    }
