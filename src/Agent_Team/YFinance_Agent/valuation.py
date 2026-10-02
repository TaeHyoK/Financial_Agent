"""Point-in-time valuation collection and DART-backed calculations."""

from __future__ import annotations

import math
import re
from datetime import date, datetime
from typing import Any

import pandas as pd


DIRECT_METRICS = {
    "Market Cap": ("market_cap", "KRW"),
    "Enterprise Value": ("enterprise_value", "KRW"),
    "Trailing P/E": ("trailing_pe", "times"),
    "Price/Sales": ("price_to_sales", "times"),
    "Price/Book": ("price_to_book", "times"),
    "Enterprise Value/Revenue": ("enterprise_value_to_revenue", "times"),
    "Enterprise Value/EBITDA": ("enterprise_value_to_ebitda", "times"),
}
COMMON_ONLY_MARKET_CAP_LIMIT = (
    "보통주 외 주식 종류가 있거나 확인되지 않아, 보통주 종가×보통주 발행주식 수 기준 시가총액이며 "
    "우선주 등 다른 종류 주식의 시가총액은 포함하지 않는다."
)
EARNINGS_BASE_WARNING_LIMIT = (
    "후행 P/E의 분모인 순이익에 손실 구간이나 영업외 손익 변동이 섞여 있어, P/E가 평상시 이익 수준보다 "
    "배수를 과대 또는 과소하게 보여줄 수 있으므로 영업이익 기준 배수(P/영업이익)를 먼저 읽는다."
)
PRIOR_YEAR_END_REFERENCE_LIMIT = (
    "직전 회계연도 말 종가와 그 회계연도의 연간 실적·기말 자본으로 계산한 같은 산식의 참고 배수다."
)
PRIOR_YEAR_END_CURRENT_SHARES_LIMIT = (
    "직전 회계연도 말 시가총액은 그 시점의 주식 수가 아니라 현재 계산에 쓴 공시 주식 수를 적용한 추정값이며, "
    "그 사이 주식 수가 변했다면 배수도 달라질 수 있다."
)
# A fiscal-year-end close older than this is too far from the period end to stand in for it.
PRIOR_YEAR_END_MAX_GAP_DAYS = 10
# Base metrics whose availability defines the calculated block status; later
# additive metrics must not change the status contract of existing outputs.
_BASE_CALCULATED_METRICS = ("market_cap", "trailing_pe", "price_to_sales", "price_to_book")
_REFERENCE_MULTIPLES = ("trailing_pe", "price_to_book", "price_to_sales", "price_to_operating_profit")
_SUFFIX_MULTIPLIERS = {
    "K": 1_000.0,
    "M": 1_000_000.0,
    "B": 1_000_000_000.0,
    "T": 1_000_000_000_000.0,
}


def collect_historical_valuation(ticker: str, *, selected_date: date) -> dict[str, Any]:
    """Collect and normalize historical valuation columns from yfinance."""

    try:
        import yfinance as yf
    except ImportError as exc:  # pragma: no cover - dependency error path
        raise RuntimeError("Missing dependency: yfinance") from exc

    frame = yf.Ticker(ticker).get_valuation_measures()
    return normalize_valuation_measures(
        frame,
        ticker=ticker,
        selected_date=selected_date,
    )


def normalize_valuation_measures(
    frame: pd.DataFrame,
    *,
    ticker: str,
    selected_date: date,
    retrieved_at: str | None = None,
) -> dict[str, Any]:
    """Filter yfinance valuation columns to dates before a pre-open selected_date."""

    retrieval_time = retrieved_at or datetime.now().astimezone().isoformat(timespec="seconds")
    if not isinstance(frame, pd.DataFrame) or frame.empty:
        return unavailable_direct_valuation(
            ticker=ticker,
            selected_date=selected_date,
            reason="empty_provider_response",
            retrieved_at=retrieval_time,
        )

    dated_columns: list[tuple[date, Any]] = []
    future_period_count = 0
    for column in frame.columns:
        parsed = _parse_period_label(column)
        if parsed is None:
            continue
        if parsed >= selected_date:
            future_period_count += 1
            continue
        dated_columns.append((parsed, column))
    dated_columns.sort(key=lambda item: item[0], reverse=True)

    periods = [_normalized_period(frame, period_date, column) for period_date, column in dated_columns]
    if not periods:
        return unavailable_direct_valuation(
            ticker=ticker,
            selected_date=selected_date,
            reason="no_valuation_period_on_or_before_selected_date",
            retrieved_at=retrieval_time,
            future_period_count=future_period_count,
            current_snapshot_excluded="Current" in frame.columns,
        )

    return {
        "status": "available",
        "ticker": ticker,
        "selected_date": selected_date.isoformat(),
        "date_policy": "valuation_period_before_selected_date",
        "source": {
            "provider": "YFinance",
            "method": "Ticker.get_valuation_measures",
            "retrieved_at": retrieval_time,
        },
        "latest_period": periods[0],
        "periods": periods,
        "filter_validation": {
            "at_or_after_selected_date_count_excluded": future_period_count,
            "current_snapshot_excluded": "Current" in frame.columns,
            "all_included_periods_before_selected_date": all(
                period["valuation_date"] < selected_date.isoformat() for period in periods
            ),
        },
        "data_limits": [
            "Provider historical valuation columns can be revised after their labeled period.",
            "The valuation date is a labeled provider period, not the retrieval timestamp.",
        ],
    }


def unavailable_direct_valuation(
    *,
    ticker: str,
    selected_date: date,
    reason: str,
    retrieved_at: str | None = None,
    future_period_count: int = 0,
    current_snapshot_excluded: bool = False,
) -> dict[str, Any]:
    """Return a stable non-fatal payload when provider valuation is unavailable."""

    return {
        "status": "unavailable",
        "reason": reason,
        "ticker": ticker,
        "selected_date": selected_date.isoformat(),
        "date_policy": "valuation_period_before_selected_date",
        "source": {
            "provider": "YFinance",
            "method": "Ticker.get_valuation_measures",
            "retrieved_at": retrieved_at or datetime.now().astimezone().isoformat(timespec="seconds"),
        },
        "latest_period": {},
        "periods": [],
        "filter_validation": {
            "at_or_after_selected_date_count_excluded": future_period_count,
            "current_snapshot_excluded": current_snapshot_excluded,
            "all_included_periods_before_selected_date": True,
        },
        "data_limits": ["Historical valuation data was unavailable from the provider."],
    }


def build_valuation_snapshot(
    *,
    market_summary: dict[str, Any],
    dart_payload: dict[str, Any],
    direct_valuation: dict[str, Any],
    market_frame: pd.DataFrame | None = None,
) -> dict[str, Any]:
    """Estimate valuation from historical close and explicitly scoped disclosures."""

    latest_market = market_summary.get("latest_snapshot") or {}
    market_date = str(latest_market.get("date") or "")
    selected_date = str(direct_valuation.get("selected_date") or market_date)
    close = _number(latest_market.get("stock_close"))

    shares_payload = dart_payload.get("share_information") or {}
    shares = _number(shares_payload.get("common_issued_shares"))
    scope = (dart_payload.get("collection_context") or {}).get("statement_scope", "unknown")
    income_key = "parent_net_income" if scope == "consolidated" else "net_income"
    equity_key = "parent_equity" if scope == "consolidated" else "total_equity"
    periods = dart_payload.get("periods") or {}
    annual_primary = (periods.get("current_fiscal_year") or {}).get("basis") == "FULL_YEAR"
    flow_period = "current_fiscal_year" if annual_primary else "ttm"
    input_problems = []
    if close is None or close <= 0 or not market_date or market_date >= selected_date:
        close = None
        input_problems.append("historical_close_not_eligible")
    share_date = str(shares_payload.get("as_of_date") or "")
    receipt_date = str((shares_payload.get("source") or {}).get("receipt_date") or "")
    # With other share classes, a known common count still values the common shares only.
    common_only_market_cap = (
        shares_payload.get("share_class") != "common_only" and shares is not None and shares > 0
    )
    if shares_payload.get("share_class") != "common_only" and not common_only_market_cap:
        input_problems.append("multiple_or_unknown_share_classes")
    if not share_date or not receipt_date or share_date > market_date or receipt_date >= selected_date:
        input_problems.append("share_count_date_not_eligible")
    if shares is None or shares <= 0:
        input_problems.append("missing_or_non_positive_common_issued_shares")
    if market_frame is not None and "stock_splits" in market_frame:
        split_rows = market_frame[(pd.to_datetime(market_frame["date"]) > pd.Timestamp(share_date or selected_date))
                                  & (pd.to_datetime(market_frame["date"]) <= pd.Timestamp(market_date))]
        if (pd.to_numeric(split_rows["stock_splits"], errors="coerce").fillna(0) != 0).any():
            input_problems.append("split_after_disclosed_share_count")
    if input_problems:
        shares = None
    ttm_revenue = _dart_metric_value(dart_payload, "revenue", flow_period)
    ttm_net_income = _dart_metric_value(dart_payload, income_key, flow_period) if scope in {"consolidated", "separate"} else None
    # Operating profit has no parent/non-controlling split, so like revenue it
    # does not depend on the statement scope; the period rules are shared.
    ttm_operating_profit = _dart_metric_value(dart_payload, "operating_profit", flow_period)
    total_equity = _dart_metric_value(dart_payload, equity_key, "current_fiscal_year") if scope in {"consolidated", "separate"} else None

    def period_eligible(key: str) -> bool:
        period = periods.get(key) or {}
        components = period.get("component_period_keys") or [key]
        return all(bool((periods.get(component) or {}).get("receipt_date"))
                   and str(periods[component]["receipt_date"]) < selected_date
                   and bool(periods[component].get("period_end"))
                   and str(periods[component]["period_end"]) <= market_date for component in components)

    if not period_eligible(flow_period):
        ttm_revenue = ttm_net_income = ttm_operating_profit = None
        input_problems.append("income_period_not_eligible")
    if not period_eligible("current_fiscal_year"):
        total_equity = None
        input_problems.append("equity_period_not_eligible")

    market_cap = close * shares if close is not None and shares is not None else None
    calculated_metrics = {
        "market_cap": _calculated_metric(
            value=market_cap,
            unit="KRW",
            formula="historical_close * disclosed_common_issued_shares",
            missing_reason=_missing_reason(
                ("selected_date_close", close),
                ("common_issued_shares", shares),
            ),
        ),
        "trailing_pe": _ratio_metric(
            market_cap,
            ttm_net_income,
            denominator_name="ttm_net_income",
            formula=f"estimated_market_cap / {flow_period}_{income_key}",
        ),
        "price_to_sales": _ratio_metric(
            market_cap,
            ttm_revenue,
            denominator_name="ttm_revenue",
            formula="market_cap / ttm_revenue",
        ),
        "price_to_book": _ratio_metric(
            market_cap,
            total_equity,
            denominator_name="total_equity",
            formula=f"estimated_market_cap / latest_disclosed_{equity_key}",
        ),
        "price_to_operating_profit": _ratio_metric(
            market_cap,
            ttm_operating_profit,
            denominator_name="ttm_operating_profit",
            formula=f"estimated_market_cap / {flow_period}_operating_profit",
        ),
    }
    calculation_statuses = [calculated_metrics[key]["status"] for key in _BASE_CALCULATED_METRICS]
    calculated_status = (
        "available"
        if all(status == "ok" for status in calculation_statuses)
        else "partial"
        if any(status == "ok" for status in calculation_statuses)
        else "unavailable"
    )

    calculated_limits = [
        "공시된 보통주 발행주식 수에 과거 종가를 적용한 추정값이며, 공시 후 주식 수 변동을 모두 확인한 기준일 시가총액은 아니다.",
        "가격 제공업체의 사후 분할 조정 가능성은 남아 있으며, 관측된 분할이 주식 수 기준일 뒤에 있으면 계산하지 않는다.",
    ]
    if common_only_market_cap:
        calculated_limits.append(COMMON_ONLY_MARKET_CAP_LIMIT)
    earnings_base_warnings = _earnings_base_warnings(
        dart_payload,
        income_key=income_key,
        flow_period=flow_period,
        ttm_net_income=ttm_net_income,
        ttm_operating_profit=ttm_operating_profit,
    )
    if earnings_base_warnings:
        calculated_limits.append(EARNINGS_BASE_WARNING_LIMIT)
    calculated = {
        "status": calculated_status,
        "calculation_basis": "disclosed_share_count_estimate",
        "statement_scope": scope,
        "input_problems": input_problems,
        "earnings_base_warnings": earnings_base_warnings,
        "data_limits": calculated_limits,
        "as_of_date": market_date,
        "inputs": {
            "selected_date_close": _input_value(
                close,
                as_of_date=market_date,
                source={"provider": "YFinance", "method": "historical_ohlcv_close"},
            ),
            "common_issued_shares": _input_value(
                shares,
                as_of_date=str(shares_payload.get("as_of_date") or ""),
                source=shares_payload.get("source") or {},
            ),
            "ttm_revenue": _dart_metric_input(dart_payload, "revenue", flow_period),
            "ttm_net_income": _dart_metric_input(dart_payload, income_key, flow_period),
            "total_equity": _dart_metric_input(dart_payload, equity_key, "current_fiscal_year"),
            "ttm_operating_profit": _dart_metric_input(dart_payload, "operating_profit", flow_period),
        },
        "metrics": calculated_metrics,
        "prior_year_end_reference": _prior_year_end_reference(
            dart_payload,
            selected_date=selected_date,
            market_frame=market_frame,
            shares=shares,
            shares_payload=shares_payload,
            scope=scope,
            income_key=income_key,
            equity_key=equity_key,
            common_only_market_cap=common_only_market_cap,
        ),
    }

    direct_latest = direct_valuation.get("latest_period") or {}
    direct_metrics = direct_latest.get("metrics") or {}
    comparisons = {}
    for metric_key in ("market_cap", "trailing_pe", "price_to_sales", "price_to_book"):
        direct_value = _number((direct_metrics.get(metric_key) or {}).get("value"))
        calculated_value = _number(calculated_metrics[metric_key].get("value"))
        dates_match = bool(market_date and direct_latest.get("valuation_date") == market_date)
        relative_difference = _relative_difference(calculated_value, direct_value)
        within_tolerance = relative_difference is not None and abs(relative_difference) <= 0.10
        comparisons[metric_key] = {
            "direct_value": direct_value,
            "direct_valuation_date": direct_latest.get("valuation_date"),
            "calculated_value": calculated_value,
            "calculated_as_of_date": market_date,
            "relative_difference": relative_difference,
            "relative_difference_tolerance": 0.10,
            "status": (
                "insufficient_data"
                if direct_value is None or calculated_value is None
                else "different_as_of_dates"
                if not dates_match
                else "aligned"
                if within_tolerance
                else "discrepancy"
            ),
            "strong_evidence_eligible": False,
        }

    return {
        "status": calculated_status if direct_valuation.get("status") != "available" else "available",
        "selected_date": selected_date,
        "market_date": market_date,
        "direct_yfinance": direct_valuation,
        "calculated_from_close_and_dart": calculated,
        "validation": {
            "direct_vs_calculated": comparisons,
            "no_at_or_after_selected_date_valuation_periods": bool(
                (direct_valuation.get("filter_validation") or {}).get(
                    "all_included_periods_before_selected_date",
                    False,
                )
            ),
        },
        "data_limits": [
            "Direct provider values and calculated values may have different as-of dates.",
            "Provider historical valuation dates do not establish what was available on the analysis date.",
            "Enterprise-value multiples are not recalculated without point-in-time debt, cash, and EBITDA inputs.",
        ],
    }


def _earnings_base_warnings(
    payload: dict[str, Any],
    *,
    income_key: str,
    flow_period: str,
    ttm_net_income: float | None,
    ttm_operating_profit: float | None,
) -> list[str]:
    """Flag trailing net income that is a poor proxy for the recurring earnings level."""

    warnings: list[str] = []
    if ttm_net_income is None:
        return warnings
    if flow_period == "ttm":
        # TTM = FY_prev + YTD_cur - YTD_prev, so it spans the remainder of the
        # prior year after the same YTD window and the current YTD window.
        previous_year = _dart_metric_value(payload, income_key, "previous_fiscal_year")
        previous_ytd = _dart_metric_value(payload, income_key, "same_period_previous_year")
        current_ytd = _dart_metric_value(payload, income_key, "current_fiscal_year")
        prior_remainder = (
            previous_year - previous_ytd if previous_year is not None and previous_ytd is not None else None
        )
        if (prior_remainder is not None and prior_remainder < 0) or (current_ytd is not None and current_ytd < 0):
            warnings.append("ttm_includes_loss_half_year")
    if (
        ttm_operating_profit is not None
        and ttm_operating_profit > 0
        and ttm_net_income < 0.5 * ttm_operating_profit
    ):
        warnings.append("ttm_net_income_far_below_operating_profit")
    return warnings


def _prior_year_end_reference(
    payload: dict[str, Any],
    *,
    selected_date: str,
    market_frame: pd.DataFrame | None,
    shares: float | None,
    shares_payload: dict[str, Any],
    scope: str,
    income_key: str,
    equity_key: str,
    common_only_market_cap: bool,
) -> dict[str, Any]:
    """Recompute the same multiples at the previous fiscal year end as a history reference."""

    period_key = "previous_fiscal_year"
    period = (payload.get("periods") or {}).get(period_key) or {}
    period_end = str(period.get("period_end") or "")
    receipt_date = str(period.get("receipt_date") or "")
    share_date = str(shares_payload.get("as_of_date") or "")
    base = {
        "status": "unavailable",
        "reason": None,
        "fiscal_period_key": period_key,
        "fiscal_period_end": period_end or None,
        "reference_date": None,
        "statement_scope": scope,
        "share_count_basis": None,
        "inputs": {},
        "metrics": {},
        "data_limits": [],
    }

    def unavailable(reason: str) -> dict[str, Any]:
        return {**base, "reason": reason}

    if not period or period.get("basis") != "FULL_YEAR" or not period_end:
        return unavailable("previous_fiscal_year_not_available")
    # Same pre-open rule as the current calculation: the annual report must be
    # filed strictly before the selected date to be usable on that date.
    if not receipt_date or receipt_date >= selected_date:
        return unavailable("fiscal_year_report_not_received_before_selected_date")
    if market_frame is None or not {"date", "stock_close"}.issubset(market_frame.columns):
        return unavailable("price_history_not_provided")
    history = pd.DataFrame({
        "date": pd.to_datetime(market_frame["date"], errors="coerce"),
        "close": pd.to_numeric(market_frame["stock_close"], errors="coerce"),
    }).dropna()
    # Never read prices on or after the selected date, nor after the period end.
    history = history[(history["date"] <= pd.Timestamp(period_end))
                      & (history["date"] < pd.Timestamp(selected_date))
                      & (history["close"] > 0)]
    if history.empty:
        return unavailable("no_close_on_or_before_fiscal_year_end")
    reference_row = history.sort_values("date").iloc[-1]
    reference_timestamp = pd.Timestamp(reference_row["date"])
    reference_date = reference_timestamp.date().isoformat()
    if (pd.Timestamp(period_end) - reference_timestamp).days > PRIOR_YEAR_END_MAX_GAP_DAYS:
        return unavailable("no_close_near_fiscal_year_end")
    close = _number(reference_row["close"])
    if shares is None:
        return unavailable("missing_common_issued_shares")
    fiscal_year_end_count = share_date == period_end
    if not fiscal_year_end_count and "stock_splits" in market_frame and share_date:
        # A split between the two dates makes the later share count inconsistent
        # with the earlier unadjusted close.
        frame_dates = pd.to_datetime(market_frame["date"], errors="coerce")
        start, end = sorted((reference_timestamp, pd.Timestamp(share_date)))
        split_rows = market_frame[(frame_dates > start) & (frame_dates <= end)]
        if (pd.to_numeric(split_rows["stock_splits"], errors="coerce").fillna(0) != 0).any():
            return unavailable("split_between_reference_date_and_share_count")

    attributable = scope in {"consolidated", "separate"}
    revenue = _dart_metric_value(payload, "revenue", period_key)
    operating_profit = _dart_metric_value(payload, "operating_profit", period_key)
    net_income = _dart_metric_value(payload, income_key, period_key) if attributable else None
    equity = _dart_metric_value(payload, equity_key, period_key) if attributable else None
    market_cap = close * shares if close is not None else None
    metrics = {
        "market_cap": _calculated_metric(
            value=market_cap,
            unit="KRW",
            formula="fiscal_year_end_close * common_issued_shares",
            missing_reason=_missing_reason(("reference_close", close)),
        ),
        "trailing_pe": _ratio_metric(
            market_cap, net_income, denominator_name="annual_net_income",
            formula=f"reference_market_cap / {period_key}_{income_key}",
        ),
        "price_to_book": _ratio_metric(
            market_cap, equity, denominator_name="total_equity",
            formula=f"reference_market_cap / {period_key}_{equity_key}",
        ),
        "price_to_sales": _ratio_metric(
            market_cap, revenue, denominator_name="annual_revenue",
            formula=f"reference_market_cap / {period_key}_revenue",
        ),
        "price_to_operating_profit": _ratio_metric(
            market_cap, operating_profit, denominator_name="annual_operating_profit",
            formula=f"reference_market_cap / {period_key}_operating_profit",
        ),
    }
    statuses = [metrics[key]["status"] for key in _REFERENCE_MULTIPLES]
    status = (
        "available"
        if all(value == "ok" for value in statuses)
        else "partial"
        if any(value == "ok" for value in statuses)
        else "unavailable"
    )
    limits = [PRIOR_YEAR_END_REFERENCE_LIMIT]
    if not fiscal_year_end_count:
        limits.append(PRIOR_YEAR_END_CURRENT_SHARES_LIMIT)
    if common_only_market_cap:
        limits.append(COMMON_ONLY_MARKET_CAP_LIMIT)
    return {
        **base,
        "status": status,
        "reason": None if status != "unavailable" else "no_reference_multiple_available",
        "reference_date": reference_date,
        "share_count_basis": (
            "fiscal_year_end_disclosed_common_shares"
            if fiscal_year_end_count
            else "current_calculation_common_shares"
        ),
        "inputs": {
            "reference_close": _input_value(
                close,
                as_of_date=reference_date,
                source={"provider": "YFinance", "method": "historical_ohlcv_close"},
            ),
            "common_issued_shares": _input_value(
                shares,
                as_of_date=share_date,
                source=shares_payload.get("source") or {},
            ),
            "annual_revenue": _dart_metric_input(payload, "revenue", period_key),
            "annual_operating_profit": _dart_metric_input(payload, "operating_profit", period_key),
            "annual_net_income": _dart_metric_input(payload, income_key, period_key),
            "total_equity": _dart_metric_input(payload, equity_key, period_key),
        },
        "metrics": metrics,
        "data_limits": limits,
    }


def _normalized_period(frame: pd.DataFrame, period_date: date, column: Any) -> dict[str, Any]:
    metrics: dict[str, Any] = {}
    for provider_label, (metric_key, unit) in DIRECT_METRICS.items():
        raw = frame.at[provider_label, column] if provider_label in frame.index else None
        value = _parse_provider_number(raw)
        metrics[metric_key] = {
            "value": value,
            "unit": unit,
            "source_value": None if raw is None or _is_missing(raw) else str(raw),
            "status": "ok" if value is not None else "unavailable",
        }
    return {
        "valuation_date": period_date.isoformat(),
        "metrics": metrics,
    }


def _parse_period_label(value: Any) -> date | None:
    text = str(value or "").strip()
    for fmt in ("%m/%d/%Y", "%Y-%m-%d"):
        try:
            return datetime.strptime(text, fmt).date()
        except ValueError:
            continue
    return None


def _parse_provider_number(value: Any) -> float | None:
    if _is_missing(value):
        return None
    if isinstance(value, int | float):
        number = float(value)
        return number if math.isfinite(number) else None
    text = str(value).strip().replace(",", "")
    match = re.fullmatch(r"(-?\d+(?:\.\d+)?)([KMBT]?)", text, flags=re.IGNORECASE)
    if not match:
        return None
    multiplier = _SUFFIX_MULTIPLIERS.get(match.group(2).upper(), 1.0)
    return float(match.group(1)) * multiplier


def _is_missing(value: Any) -> bool:
    if value is None:
        return True
    try:
        if pd.isna(value):
            return True
    except (TypeError, ValueError):
        pass
    return str(value).strip() in {"", "-", "--", "N/A", "n/a", "None"}


def _dart_metric_value(payload: dict[str, Any], metric_key: str, period_key: str) -> float | None:
    metric = (payload.get("metrics_by_key") or {}).get(metric_key) or {}
    period = (metric.get("values_by_period") or {}).get(period_key) or {}
    return _number(period.get("value"))


def _dart_metric_input(payload: dict[str, Any], metric_key: str, period_key: str) -> dict[str, Any]:
    metric = (payload.get("metrics_by_key") or {}).get(metric_key) or {}
    period = (metric.get("values_by_period") or {}).get(period_key) or {}
    period_meta = period.get("period") or {}
    return _input_value(
        _number(period.get("value")),
        as_of_date=str(period_meta.get("period_end") or ""),
        source={
            "provider": "DART",
            "receipt_no": period_meta.get("receipt_no"),
            "receipt_date": period_meta.get("receipt_date"),
            "period_basis": period_meta.get("basis"),
            "derivation": period_meta.get("derivation"),
        },
    )


def _input_value(value: float | None, *, as_of_date: str, source: dict[str, Any]) -> dict[str, Any]:
    return {
        "value": value,
        "as_of_date": as_of_date,
        "source": source,
        "status": "ok" if value is not None else "unavailable",
    }


def _calculated_metric(
    *,
    value: float | None,
    unit: str,
    formula: str,
    missing_reason: str | None,
) -> dict[str, Any]:
    return {
        "value": value,
        "unit": unit,
        "formula": formula,
        "status": "ok" if value is not None else "insufficient_data",
        "reason": missing_reason,
    }


def _ratio_metric(
    numerator: float | None,
    denominator: float | None,
    *,
    denominator_name: str,
    formula: str,
) -> dict[str, Any]:
    reason = _missing_reason(("market_cap", numerator), (denominator_name, denominator))
    if reason is None and denominator is not None and denominator <= 0:
        reason = f"non_positive_{denominator_name}"
    value = numerator / denominator if reason is None and numerator is not None and denominator is not None else None
    return _calculated_metric(value=value, unit="times", formula=formula, missing_reason=reason)


def _missing_reason(*items: tuple[str, float | None]) -> str | None:
    missing = [name for name, value in items if value is None]
    return f"missing_{'_and_'.join(missing)}" if missing else None


def _relative_difference(calculated: float | None, direct: float | None) -> float | None:
    if calculated is None or direct in (None, 0):
        return None
    return (calculated - direct) / abs(direct)


def _number(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None
