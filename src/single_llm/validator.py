"""Deterministic post-generation checks for the Single-LLM report."""

from __future__ import annotations

import re
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from typing import Any, Iterable

from .contracts import (
    ANALYSIS_SECTIONS,
    CONVICTIONS,
    RECOMMENDATIONS,
    REPORT_VERSION,
    VALIDATION_VERSION,
)


_NUMBER_PATTERN = re.compile(r"(?<![A-Za-z0-9_])[-+]?\d[\d,]*(?:\.\d+)?%?")
_KEY_NUMBER_PATTERN = re.compile(r"(?<!\d)\d+(?:\.\d+)?")
_KOREAN_COMPOUND_UNIT_PATTERN = re.compile(
    r"(?<![A-Za-z0-9_])"
    r"(?P<high>[-+]?\d[\d,]*(?:\.\d+)?)\s*"
    r"(?P<high_unit>조|만)\s*"
    r"(?P<low>\d[\d,]*(?:\.\d+)?)\s*"
    r"(?P<low_unit>억|원)?"
)
_KOREAN_SIMPLE_UNIT_PATTERN = re.compile(
    r"(?<![A-Za-z0-9_])"
    r"(?P<number>[-+]?\d[\d,]*(?:\.\d+)?)\s*"
    r"(?P<unit>조|억|만)(?:\s*원)?"
)
_KOREAN_UNIT_MULTIPLIERS = {
    "만": Decimal("1e4"),
    "억": Decimal("1e8"),
    "조": Decimal("1e12"),
}


def validate_report(
    report: dict[str, Any],
    *,
    bundle: dict[str, Any],
    strict_numeric_grounding: bool,
) -> dict[str, Any]:
    """Validate identity, required sections, evidence references, and numbers."""

    errors: list[str] = []
    warnings: list[str] = []
    expected_target = str((bundle.get("target") or {}).get("company_name") or "")
    expected_date = str(bundle.get("selected_date") or "")
    expected_horizon = str(bundle.get("decision_horizon") or "")

    if report.get("report_version") != REPORT_VERSION:
        errors.append(f"report_version must equal {REPORT_VERSION}")
    metadata = _require_object(report.get("metadata"), "metadata", errors)
    if str(metadata.get("company_name") or "") != expected_target:
        errors.append("metadata.company_name does not match the target company")
    if str(metadata.get("selected_date") or "") != expected_date:
        errors.append("metadata.selected_date does not match the frozen selected_date")
    if str(metadata.get("decision_horizon") or "") != expected_horizon:
        errors.append("metadata.decision_horizon does not match the experiment contract")

    call = _require_object(report.get("investment_call"), "investment_call", errors)
    recommendation = str(call.get("recommendation") or "")
    if recommendation not in RECOMMENDATIONS:
        errors.append(f"investment_call.recommendation must be one of {RECOMMENDATIONS}")
    conviction = str(call.get("conviction") or "")
    if conviction not in CONVICTIONS:
        errors.append(f"investment_call.conviction must be one of {CONVICTIONS}")

    key_evidence = _require_array(report.get("key_evidence"), "key_evidence", errors)
    if not 5 <= len(key_evidence) <= 12:
        errors.append("key_evidence must contain 5 to 12 rows")
    analysis = _require_object(report.get("analysis"), "analysis", errors)
    for section in ANALYSIS_SECTIONS:
        items = _require_array(analysis.get(section), f"analysis.{section}", errors)
        if not 2 <= len(items) <= 8:
            errors.append(f"analysis.{section} must contain 2 to 8 claim units")
    risks = _require_array(report.get("risks"), "risks", errors)
    if not 3 <= len(risks) <= 8:
        errors.append("risks must contain 3 to 8 rows")
    limits = _require_array(report.get("data_limits"), "data_limits", errors)
    if not 1 <= len(limits) <= 6:
        errors.append("data_limits must contain 1 to 6 rows")

    evidence_items = [
        item
        for item in bundle.get("evidence_catalog") or []
        if isinstance(item, dict) and item.get("evidence_id")
    ]
    allowed_ids = {str(item["evidence_id"]) for item in evidence_items}
    referenced_ids: set[str] = set()
    empty_reference_paths: list[str] = []
    invalid_references: list[dict[str, str]] = []
    _collect_references(
        report,
        path="$report",
        allowed_ids=allowed_ids,
        referenced_ids=referenced_ids,
        empty_reference_paths=empty_reference_paths,
        invalid_references=invalid_references,
    )
    if empty_reference_paths:
        errors.extend(f"empty evidence_ids at {path}" for path in empty_reference_paths)
    if invalid_references:
        errors.extend(
            f"unknown evidence ID {item['evidence_id']} at {item['path']}"
            for item in invalid_references
        )

    numeric = _numeric_grounding(report, bundle)
    if numeric["unsupported_count"]:
        message = (
            f"{numeric['unsupported_count']} report numbers were not found in their "
            "referenced evidence"
        )
        if strict_numeric_grounding:
            errors.append(message)
        else:
            warnings.append(message)

    domains_by_id = {
        str(item["evidence_id"]): str(item.get("domain") or "unknown")
        for item in evidence_items
    }
    referenced_domains = sorted(
        {domains_by_id[item] for item in referenced_ids if item in domains_by_id}
    )
    required_domains = {"financial", "news", "market", "valuation"}
    missing_domains = sorted(required_domains - set(referenced_domains))
    if missing_domains:
        warnings.append(f"No final-report references for domains: {missing_domains}")

    return {
        "version": VALIDATION_VERSION,
        "status": "valid" if not errors else "invalid",
        "errors": errors,
        "warnings": warnings,
        "identity": {
            "expected_company_name": expected_target,
            "expected_selected_date": expected_date,
            "expected_decision_horizon": expected_horizon,
        },
        "evidence_references": {
            "available_count": len(allowed_ids),
            "referenced_count": len(referenced_ids),
            "referenced_domains": referenced_domains,
            "missing_domains": missing_domains,
            "unknown": invalid_references,
            "empty_paths": empty_reference_paths,
        },
        "numeric_grounding": numeric,
    }


def _collect_references(
    value: Any,
    *,
    path: str,
    allowed_ids: set[str],
    referenced_ids: set[str],
    empty_reference_paths: list[str],
    invalid_references: list[dict[str, str]],
) -> None:
    if isinstance(value, dict):
        if "evidence_ids" in value:
            refs = value.get("evidence_ids")
            if not isinstance(refs, list):
                empty_reference_paths.append(f"{path}.evidence_ids")
            elif not refs and not path.startswith("$report.data_limits"):
                empty_reference_paths.append(f"{path}.evidence_ids")
            else:
                for ref in refs:
                    evidence_id = str(ref)
                    if evidence_id not in allowed_ids:
                        invalid_references.append(
                            {"path": f"{path}.evidence_ids", "evidence_id": evidence_id}
                        )
                    else:
                        referenced_ids.add(evidence_id)
        for key, item in value.items():
            _collect_references(
                item,
                path=f"{path}.{key}",
                allowed_ids=allowed_ids,
                referenced_ids=referenced_ids,
                empty_reference_paths=empty_reference_paths,
                invalid_references=invalid_references,
            )
    elif isinstance(value, list):
        for index, item in enumerate(value):
            _collect_references(
                item,
                path=f"{path}[{index}]",
                allowed_ids=allowed_ids,
                referenced_ids=referenced_ids,
                empty_reference_paths=empty_reference_paths,
                invalid_references=invalid_references,
            )


def _numeric_grounding(report: dict[str, Any], bundle: dict[str, Any]) -> dict[str, Any]:
    evidence_by_id = {
        str(item.get("evidence_id")): item.get("payload")
        for item in bundle.get("evidence_catalog") or []
        if isinstance(item, dict) and item.get("evidence_id")
    }
    contract_values = _number_aliases(
        [
            bundle.get("selected_date"),
            bundle.get("information_cutoff_date"),
            bundle.get("decision_horizon"),
        ]
    )
    checked = 0
    unsupported: list[dict[str, Any]] = []
    grounded = 0

    def visit(value: Any, path: str) -> None:
        nonlocal checked, grounded
        if isinstance(value, dict):
            refs = value.get("evidence_ids")
            if isinstance(refs, list):
                object_ref_ids = [str(ref) for ref in refs]
                for key, item in value.items():
                    if key == "evidence_ids" or not isinstance(item, str):
                        continue
                    # Some schema fields are plain strings rather than nested claim
                    # objects. The prompt therefore permits an exact evidence ID in
                    # the string itself. Limit this expansion to IDs that actually
                    # exist in the frozen catalog.
                    field_ref_ids = list(object_ref_ids)
                    for evidence_id in evidence_by_id:
                        if evidence_id in item and evidence_id not in field_ref_ids:
                            field_ref_ids.append(evidence_id)
                    evidence_values = set(contract_values)
                    for evidence_id in field_ref_ids:
                        evidence_values.update(
                            _number_aliases(
                                [evidence_by_id.get(evidence_id)],
                                include_korean_unit_scales=True,
                            )
                        )
                    for token, aliases in _numeric_mentions(item):
                        checked += 1
                        if aliases & evidence_values:
                            grounded += 1
                        else:
                            unsupported.append(
                                {
                                    "path": f"{path}.{key}",
                                    "number": token,
                                    "evidence_ids": field_ref_ids,
                                }
                            )
            for key, item in value.items():
                visit(item, f"{path}.{key}")
        elif isinstance(value, list):
            for index, item in enumerate(value):
                visit(item, f"{path}[{index}]")

    visit(report, "$report")
    return {
        "checked_count": checked,
        "grounded_count": grounded,
        "unsupported_count": len(unsupported),
        "precision": round(grounded / checked, 6) if checked else 1.0,
        "unsupported": unsupported,
        "policy": (
            "Exact or rounded numeric match against referenced evidence; percentages and "
            "Korean 만/억/조 display-unit conversions are normalized."
        ),
    }


def _number_aliases(
    values: Iterable[Any],
    *,
    include_korean_unit_scales: bool = False,
) -> set[str]:
    aliases: set[str] = set()

    def visit(value: Any) -> None:
        if isinstance(value, bool) or value is None:
            return
        if isinstance(value, (int, float, Decimal)):
            decimal = _to_decimal(value)
            aliases.update(_evidence_decimal_aliases(decimal, include_korean_unit_scales))
        elif isinstance(value, str):
            for token in _NUMBER_PATTERN.findall(value):
                decimal = _token_decimal(token)
                aliases.update(_token_aliases(token))
                aliases.update(
                    _evidence_decimal_aliases(decimal, include_korean_unit_scales)
                )
        elif isinstance(value, dict):
            for key, item in value.items():
                # Horizon labels are often encoded in semantic keys (for example,
                # stock_relative_strength_60). They are legitimate support for
                # prose such as "60일 상대강도" even though 60 is not a value.
                for token in _KEY_NUMBER_PATTERN.findall(str(key)):
                    aliases.update(_token_aliases(token))
                visit(item)
        elif isinstance(value, list):
            for item in value:
                visit(item)

    for value in values:
        visit(value)
    return aliases


def _numeric_mentions(text: str) -> list[tuple[str, set[str]]]:
    """Extract numeric claims while treating Korean display units as one value."""

    mentions: list[tuple[str, set[str]]] = []
    masked = list(text)

    for match in _KOREAN_COMPOUND_UNIT_PATTERN.finditer(text):
        high = _token_decimal(match.group("high"))
        low = _token_decimal(match.group("low"))
        high_unit = match.group("high_unit")
        low_unit = match.group("low_unit") or ("원" if high_unit == "만" else "억")
        expected_low_unit = "원" if high_unit == "만" else "억"
        if high is None or low is None or low_unit != expected_low_unit:
            continue
        base_value = high * _KOREAN_UNIT_MULTIPLIERS[high_unit]
        if low_unit == "억":
            base_value += low * _KOREAN_UNIT_MULTIPLIERS["억"]
        else:
            base_value += low
        aliases = _decimal_aliases(base_value)
        aliases.update(_decimal_aliases(abs(base_value)))
        mentions.append((match.group(0).strip(), aliases))
        for index in range(match.start(), match.end()):
            masked[index] = " "

    remaining = "".join(masked)
    for match in _KOREAN_SIMPLE_UNIT_PATTERN.finditer(remaining):
        value = _token_decimal(match.group("number"))
        if value is None:
            continue
        base_value = value * _KOREAN_UNIT_MULTIPLIERS[match.group("unit")]
        aliases = _decimal_aliases(value)
        aliases.update(_decimal_aliases(abs(value)))
        aliases.update(_decimal_aliases(base_value))
        aliases.update(_decimal_aliases(abs(base_value)))
        mentions.append((match.group(0).strip(), aliases))
        for index in range(match.start(), match.end()):
            masked[index] = " "

    for token in _NUMBER_PATTERN.findall("".join(masked)):
        mentions.append((token, _token_aliases(token)))
    return mentions


def _token_aliases(token: str) -> set[str]:
    percent = token.endswith("%")
    value = _token_decimal(token)
    if value is None:
        return set()
    aliases = _decimal_aliases(value)
    if percent:
        aliases.update(_decimal_aliases(value / Decimal(100)))
    elif -1 <= value <= 1:
        aliases.update(_decimal_aliases(value * Decimal(100)))
    return aliases


def _token_decimal(token: str) -> Decimal | None:
    raw = token.rstrip("%").replace(",", "")
    try:
        return Decimal(raw)
    except InvalidOperation:
        return None


def _evidence_decimal_aliases(
    value: Decimal | None,
    include_korean_unit_scales: bool,
) -> set[str]:
    aliases = _decimal_aliases(value)
    if value is None or not value.is_finite():
        return aliases
    aliases.update(_decimal_aliases(abs(value)))
    if include_korean_unit_scales:
        for multiplier in _KOREAN_UNIT_MULTIPLIERS.values():
            scaled = value / multiplier
            aliases.update(_decimal_aliases(scaled))
            aliases.update(_decimal_aliases(abs(scaled)))
    return aliases


def _decimal_aliases(value: Decimal | None) -> set[str]:
    if value is None or not value.is_finite():
        return set()
    aliases = {_canonical_decimal(value)}
    for places in range(0, 5):
        quantum = Decimal(1).scaleb(-places)
        aliases.add(_canonical_decimal(value.quantize(quantum, rounding=ROUND_HALF_UP)))
    if -1 <= value <= 1:
        percent = value * Decimal(100)
        for places in range(0, 5):
            quantum = Decimal(1).scaleb(-places)
            aliases.add(_canonical_decimal(percent.quantize(quantum, rounding=ROUND_HALF_UP)))
    return aliases


def _canonical_decimal(value: Decimal) -> str:
    normalized = value.normalize()
    text = format(normalized, "f")
    if "." in text:
        text = text.rstrip("0").rstrip(".")
    return "0" if text in {"", "-0"} else text


def _to_decimal(value: Any) -> Decimal | None:
    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError):
        return None


def _require_object(value: Any, label: str, errors: list[str]) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    errors.append(f"{label} must be an object")
    return {}


def _require_array(value: Any, label: str, errors: list[str]) -> list[Any]:
    if isinstance(value, list):
        return value
    errors.append(f"{label} must be an array")
    return []


__all__ = ["validate_report"]
