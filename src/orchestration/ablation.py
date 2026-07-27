"""Shared ablation configuration for orchestration and final-stage agents."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable


DOMAIN_ORDER = ("financial", "news", "yfinance")
DOMAIN_ALIASES = {
    "dart": "financial",
    "financial": "financial",
    "news": "news",
    "market": "yfinance",
    "yf": "yfinance",
    "yfinance": "yfinance",
}


@dataclass(frozen=True)
class AblationConfig:
    """One reproducible set of components exposed to the final decision."""

    included_domains: tuple[str, ...] = DOMAIN_ORDER
    use_sy: bool = True
    primary_data_only: bool = False
    include_competitor: bool = True
    strategy_context_mode: str = "compact_cards"
    writer_mode: str = "deterministic"
    experiment_name: str = "baseline"

    @property
    def active(self) -> bool:
        return (
            self.included_domains != DOMAIN_ORDER
            or not self.use_sy
            or self.primary_data_only
            or not self.include_competitor
            or self.strategy_context_mode != "compact_cards"
            or self.writer_mode != "deterministic"
        )

    @property
    def excluded_domains(self) -> tuple[str, ...]:
        return tuple(domain for domain in DOMAIN_ORDER if domain not in self.included_domains)

    def as_dict(self) -> dict[str, Any]:
        return {
            "experiment_name": self.experiment_name,
            "active": self.active,
            "included_domains": list(self.included_domains),
            "excluded_domains": list(self.excluded_domains),
            "use_sy": self.use_sy,
            "primary_data_only": self.primary_data_only,
            "include_competitor": self.include_competitor,
            "strategy_context_mode": self.strategy_context_mode,
            "writer_mode": self.writer_mode,
            "domain_ablation_stage": "strategy_evidence_inclusion",
        }


def normalize_domain(value: str) -> str:
    key = str(value or "").strip().lower()
    try:
        return DOMAIN_ALIASES[key]
    except KeyError as exc:
        allowed = ", ".join(sorted(DOMAIN_ALIASES))
        raise ValueError(f"Unknown ablation domain {value!r}; choose one of: {allowed}.") from exc


def config_from_args(args: Any) -> AblationConfig:
    """Normalize CLI include/exclude flags and reject confounded empty inputs."""

    only_raw = str(getattr(args, "only_domain", "") or "").strip()
    excluded_raw = list(getattr(args, "exclude_domain", None) or [])
    if only_raw and excluded_raw:
        raise ValueError("--only-domain and --exclude-domain cannot be used together.")

    if only_raw:
        included = (normalize_domain(only_raw),)
    else:
        excluded = {normalize_domain(value) for value in excluded_raw}
        included = tuple(domain for domain in DOMAIN_ORDER if domain not in excluded)
    if not included:
        raise ValueError("At least one of financial/DART, news, or yfinance must remain.")

    use_sy = not bool(getattr(args, "no_sy", False))
    primary_data_only = bool(getattr(args, "primary_data_only", False))
    include_competitor = not bool(getattr(args, "no_competitor", False))
    strategy_context_mode = (
        "full_reports" if bool(getattr(args, "full_context", False)) else "compact_cards"
    )
    writer_mode = (
        "free_form" if bool(getattr(args, "free_form_writer", False)) else "deterministic"
    )
    requested_name = str(getattr(args, "experiment_name", "") or "").strip()
    auto_name = ablation_slug(
        included_domains=included,
        use_sy=use_sy,
        primary_data_only=primary_data_only,
        include_competitor=include_competitor,
        strategy_context_mode=strategy_context_mode,
        writer_mode=writer_mode,
    )
    return AblationConfig(
        included_domains=included,
        use_sy=use_sy,
        primary_data_only=primary_data_only,
        include_competitor=include_competitor,
        strategy_context_mode=strategy_context_mode,
        writer_mode=writer_mode,
        experiment_name=_safe_label(requested_name or auto_name),
    )


def config_from_mapping(value: Any) -> AblationConfig:
    """Read an artifact-friendly mapping produced by :meth:`as_dict`."""

    payload = value if isinstance(value, dict) else {}
    included_raw: Iterable[Any] = payload.get("included_domains") or DOMAIN_ORDER
    included_set = {normalize_domain(str(item)) for item in included_raw}
    included = tuple(domain for domain in DOMAIN_ORDER if domain in included_set)
    if not included:
        raise ValueError("Ablation mapping must include at least one source domain.")
    return AblationConfig(
        included_domains=included,
        use_sy=bool(payload.get("use_sy", True)),
        primary_data_only=bool(payload.get("primary_data_only", False)),
        include_competitor=bool(payload.get("include_competitor", True)),
        strategy_context_mode=str(payload.get("strategy_context_mode") or "compact_cards"),
        writer_mode=str(payload.get("writer_mode") or "deterministic"),
        experiment_name=_safe_label(str(payload.get("experiment_name") or "baseline")),
    )


def ablation_slug(
    *,
    included_domains: tuple[str, ...],
    use_sy: bool,
    primary_data_only: bool,
    include_competitor: bool,
    strategy_context_mode: str = "compact_cards",
    writer_mode: str = "deterministic",
) -> str:
    parts: list[str] = []
    if included_domains != DOMAIN_ORDER:
        if len(included_domains) == 1:
            parts.append(f"only_{included_domains[0]}")
        else:
            excluded = [domain for domain in DOMAIN_ORDER if domain not in included_domains]
            parts.append("no_" + "_".join(excluded))
    if not use_sy:
        parts.append("no_sy")
    if primary_data_only:
        parts.append("primary_only")
    if not include_competitor:
        parts.append("no_competitor")
    if strategy_context_mode == "full_reports":
        parts.append("full_context")
    if writer_mode == "free_form":
        parts.append("free_form_writer")
    return "__".join(parts) or "baseline"


def _safe_label(value: str) -> str:
    label = value.strip() or "baseline"
    for character in ('\\', '/', ':', '*', '?', '"', '<', '>', '|', ' '):
        label = label.replace(character, "_")
    return label.strip("._") or "baseline"
