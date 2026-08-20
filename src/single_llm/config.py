"""Configuration contract for the Single-LLM Direct baseline."""

from __future__ import annotations

from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any

import yaml


PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_CONFIG_PATH = Path(__file__).resolve().parent / "configs" / "gpt4_1.yaml"


@dataclass(frozen=True)
class SingleLLMConfig:
    """All settings that can affect one baseline report generation."""

    model: str = "gpt-4.1"
    decision_horizon: str = "1개월"
    target_input_tokens: int = 75_000
    hard_input_tokens: int = 90_000
    max_output_tokens: int = 12_000
    max_news_items_per_company: int = 0
    min_news_items_per_company: int = 10
    temperature: float = 0.0
    timeout_seconds: float = 300.0
    transport_retries: int = 0
    strict_numeric_grounding: bool = True
    input_price_per_million: float = 2.0
    cached_input_price_per_million: float = 0.5
    output_price_per_million: float = 8.0
    pricing_model: str = "gpt-4.1"
    pricing_as_of: str = "2026-08-04"

    def validate(self) -> "SingleLLMConfig":
        if not self.model.strip():
            raise ValueError("model must not be empty")
        if not self.decision_horizon.strip():
            raise ValueError("decision_horizon must not be empty")
        if not 0 < self.target_input_tokens <= self.hard_input_tokens:
            raise ValueError("token budgets must satisfy 0 < target <= hard")
        if self.max_output_tokens <= 0:
            raise ValueError("max_output_tokens must be positive")
        if self.max_news_items_per_company < 0:
            raise ValueError("max_news_items_per_company must be zero or positive")
        if self.min_news_items_per_company < 0:
            raise ValueError("min_news_items_per_company must be zero or positive")
        if (
            self.max_news_items_per_company
            and self.min_news_items_per_company > self.max_news_items_per_company
        ):
            raise ValueError("min_news_items_per_company cannot exceed the nonzero maximum")
        if self.transport_retries < 0:
            raise ValueError("transport_retries must be zero or positive")
        if self.timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be positive")
        if not 0.0 <= self.temperature <= 2.0:
            raise ValueError("temperature must be between 0 and 2")
        prices = (
            self.input_price_per_million,
            self.cached_input_price_per_million,
            self.output_price_per_million,
        )
        if any(price < 0 for price in prices):
            raise ValueError("token prices must not be negative")
        return self

    def with_overrides(self, **overrides: Any) -> "SingleLLMConfig":
        clean = {key: value for key, value in overrides.items() if value is not None}
        return replace(self, **clean).validate()

    def as_dict(self) -> dict[str, Any]:
        return {
            "model": self.model,
            "decision_horizon": self.decision_horizon,
            "target_input_tokens": self.target_input_tokens,
            "hard_input_tokens": self.hard_input_tokens,
            "max_output_tokens": self.max_output_tokens,
            "max_news_items_per_company": self.max_news_items_per_company,
            "min_news_items_per_company": self.min_news_items_per_company,
            "temperature": self.temperature,
            "timeout_seconds": self.timeout_seconds,
            "transport_retries": self.transport_retries,
            "strict_numeric_grounding": self.strict_numeric_grounding,
            "pricing": {
                "input_per_million_usd": self.input_price_per_million,
                "cached_input_per_million_usd": self.cached_input_price_per_million,
                "output_per_million_usd": self.output_price_per_million,
                "model": self.pricing_model,
                "as_of": self.pricing_as_of,
                "source": "https://developers.openai.com/api/docs/pricing",
            },
        }


def load_single_llm_config(path: str | Path | None = None) -> SingleLLMConfig:
    """Load a YAML config without reading environment-dependent model defaults."""

    source = Path(path).expanduser().resolve() if path else DEFAULT_CONFIG_PATH
    payload = yaml.safe_load(source.read_text(encoding="utf-8")) or {}
    if not isinstance(payload, dict):
        raise ValueError(f"Single-LLM config must be an object: {source}")
    allowed_keys = {
        "model",
        "decision_horizon",
        "target_input_tokens",
        "hard_input_tokens",
        "max_output_tokens",
        "max_news_items_per_company",
        "min_news_items_per_company",
        "temperature",
        "timeout_seconds",
        "transport_retries",
        "strict_numeric_grounding",
        "pricing",
    }
    unknown_keys = sorted(set(payload) - allowed_keys)
    if unknown_keys:
        raise ValueError(f"Unknown Single-LLM config keys: {unknown_keys}")
    pricing = payload.pop("pricing", {}) or {}
    if not isinstance(pricing, dict):
        raise ValueError("pricing must be an object")
    config = SingleLLMConfig(
        model=str(payload.get("model") or "gpt-4.1"),
        decision_horizon=str(payload.get("decision_horizon") or "1개월"),
        target_input_tokens=int(payload.get("target_input_tokens", 75_000)),
        hard_input_tokens=int(payload.get("hard_input_tokens", 90_000)),
        max_output_tokens=int(payload.get("max_output_tokens", 12_000)),
        max_news_items_per_company=int(payload.get("max_news_items_per_company", 0)),
        min_news_items_per_company=int(payload.get("min_news_items_per_company", 10)),
        temperature=float(payload.get("temperature", 0.0)),
        timeout_seconds=float(payload.get("timeout_seconds", 300.0)),
        transport_retries=int(payload.get("transport_retries", 0)),
        strict_numeric_grounding=_as_bool(
            payload.get("strict_numeric_grounding", True),
            key="strict_numeric_grounding",
        ),
        input_price_per_million=float(pricing.get("input_per_million_usd", 2.0)),
        cached_input_price_per_million=float(
            pricing.get("cached_input_per_million_usd", 0.5)
        ),
        output_price_per_million=float(pricing.get("output_per_million_usd", 8.0)),
        pricing_model=str(pricing.get("model") or payload.get("model") or "gpt-4.1"),
        pricing_as_of=str(pricing.get("as_of") or "2026-08-04"),
    )
    return config.validate()


def _as_bool(value: Any, *, key: str) -> bool:
    if isinstance(value, bool):
        return value
    normalized = str(value).strip().lower()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False
    raise ValueError(f"{key} must be a boolean")


__all__ = [
    "DEFAULT_CONFIG_PATH",
    "PROJECT_ROOT",
    "SingleLLMConfig",
    "load_single_llm_config",
]
