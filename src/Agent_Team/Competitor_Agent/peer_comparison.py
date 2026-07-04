"""Domestic peer comparison v1 built from existing agent outputs.

This module intentionally avoids global peer, valuation, and industry-average
claims because those datasets are not available in the current pipeline.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

import pandas as pd

from . import OUTPUT_ROOT
from .agent import (
    RunIdentity,
    build_run_key,
    company_from_run_key,
    discover_competitor_identities,
    load_identity_from_config,
    normalize_date,
)


OUTPUT_VERSION = "1.0"
EXCLUDED_SCOPE = [
    "global_peer_comparison",
    "valuation_comparison",
    "complete_industry_average_comparison",
]


@dataclass(frozen=True)
class PeerComparisonPaths:
    """Paths written by Peer Comparison Agent v1."""

    dataset_json: Path
    positioning_json: Path
    summary_md: Path


def generate_peer_comparison(
    *,
    target: RunIdentity,
    peer_run_keys: list[str] | None = None,
    output_root: Path = OUTPUT_ROOT,
    output_dir: Path | None = None,
) -> PeerComparisonPaths:
    """Generate domestic-only peer comparison artifacts for one target run."""

    output_root = Path(output_root).expanduser().resolve()
    resolved_target = _resolve_target_identity(target, output_root)
    peers = _resolve_peer_identities(
        target=resolved_target,
        output_root=output_root,
        peer_run_keys=peer_run_keys or [],
    )
    run_identities = [resolved_target, *peers]
    rows = [
        _build_company_metric_row(
            identity=identity,
            output_root=output_root,
            peer_group="target" if identity.run_key == resolved_target.run_key else "domestic_peer",
        )
        for identity in run_identities
    ]
    dataset = _build_dataset_payload(target=resolved_target, peers=peers, rows=rows, output_root=output_root)
    positioning = _build_positioning_payload(dataset)

    destination = output_dir or output_root / "Competitor" / resolved_target.run_key
    destination = Path(destination).expanduser().resolve()
    destination.mkdir(parents=True, exist_ok=True)
    dataset_json = destination / "peer_comparison_dataset.json"
    positioning_json = destination / "peer_positioning_summary.json"
    summary_md = destination / "peer_comparison_summary.md"
    _write_json(dataset_json, dataset)
    _write_json(positioning_json, positioning)
    summary_md.write_text(_render_summary_md(dataset, positioning), encoding="utf-8")

    return PeerComparisonPaths(
        dataset_json=dataset_json,
        positioning_json=positioning_json,
        summary_md=summary_md,
    )


def _resolve_target_identity(target: RunIdentity, output_root: Path) -> RunIdentity:
    run_key = target.run_key
    if not run_key:
        raise ValueError("target.run_key is required for peer comparison.")
    financial_path = output_root / "Financial" / run_key / "final_report.json"
    yfinance_manifest = output_root / "Y_Finance" / run_key / "manifest.json"
    company_name = target.company_name or company_from_run_key(run_key)
    ticker = target.ticker
    if yfinance_manifest.exists():
        manifest = _load_json(yfinance_manifest)
        company_name = str(manifest.get("company_name") or company_name)
        ticker = str(manifest.get("ticker") or ticker or "").strip() or ticker
    elif financial_path.exists():
        report = _load_json(financial_path)
        company_name = str(report.get("target_company") or report.get("company_name") or company_name)
    selected_date = target.selected_date or _date_suffix(run_key)
    return RunIdentity(
        run_key=run_key,
        company_name=company_name,
        selected_date=selected_date,
        ticker=ticker,
        corp_code=target.corp_code,
        stock_code=target.stock_code,
    )


def _resolve_peer_identities(
    *,
    target: RunIdentity,
    output_root: Path,
    peer_run_keys: list[str],
) -> list[RunIdentity]:
    if peer_run_keys:
        candidates = [
            RunIdentity(run_key=run_key, company_name=company_from_run_key(run_key), selected_date=target.selected_date)
            for run_key in peer_run_keys
            if run_key != target.run_key
        ]
    else:
        candidates = discover_competitor_identities(
            output_root=output_root,
            target=target,
            selected_date=target.selected_date,
            include_partial=True,
        )
    peers: list[RunIdentity] = []
    for identity in candidates:
        if not _has_required_peer_files(output_root, identity.run_key):
            continue
        peers.append(_resolve_target_identity(identity, output_root))
    peers.sort(key=lambda item: item.run_key)
    return peers


def _has_required_peer_files(output_root: Path, run_key: str) -> bool:
    return (
        (output_root / "Financial" / run_key / "final_report.json").exists()
        and (output_root / "Y_Finance" / run_key / "market_full_dataset.csv").exists()
    )


def _build_company_metric_row(*, identity: RunIdentity, output_root: Path, peer_group: str) -> dict[str, Any]:
    financial_path = output_root / "Financial" / identity.run_key / "final_report.json"
    market_path = output_root / "Y_Finance" / identity.run_key / "market_full_dataset.csv"
    competitor_path = output_root / "Competitor" / identity.run_key / "competitor_summary_report.json"
    financial = _load_json(financial_path)
    market = _latest_market_row(market_path)
    competitor_summary = _load_json(competitor_path) if competitor_path.exists() else {}
    detailed = financial.get("detailed_analysis", {}) if isinstance(financial, dict) else {}

    revenue = _supporting_features(detailed, "revenue")
    margin = _supporting_features(detailed, "margin")
    expense = _supporting_features(detailed, "expense_efficiency")
    eps = _supporting_features(detailed, "eps")
    cash_flow = _supporting_features(detailed, "cash_flow")
    balance = _supporting_features(detailed, "balance_sheet")
    capital = _supporting_features(detailed, "capital_structure")
    liquidity = _supporting_features(detailed, "liquidity")

    row = {
        "company_name": identity.company_name or company_from_run_key(identity.run_key),
        "run_key": identity.run_key,
        "peer_group": peer_group,
        "ticker": identity.ticker,
        "as_of_date": _date_suffix(identity.run_key),
        "financial_metrics": {
            "revenue_100m": _krw_to_100m(revenue.get("revenue")),
            "revenue_growth_pct": _ratio_to_pct(revenue.get("revenue_growth")),
            "revenue_growth_basis": revenue.get("period"),
            "contribution_margin_pct": _ratio_to_pct(margin.get("contribution_margin")),
            "sga_margin_pct": _ratio_to_pct(expense.get("sga_margin")),
            "eps": _number(eps.get("eps")),
            "operating_cash_flow_100m": _krw_to_100m(cash_flow.get("operating_cash_flow")),
            "cash_and_equivalents_100m": _krw_to_100m(
                balance.get("cash_and_cash_equivalents") or liquidity.get("cash_and_cash_equivalents")
            ),
            "debt_ratio_pct": _ratio_to_pct(capital.get("debt_to_equity")),
            "current_ratio_pct": _ratio_to_pct(liquidity.get("current_ratio")),
            "cash_ratio_pct": _ratio_to_pct(liquidity.get("cash_ratio")),
            "equity_ratio_pct": _ratio_to_pct(capital.get("equity_ratio")),
            "financial_period": revenue.get("period") or margin.get("period") or eps.get("period"),
            "balance_sheet_basis": balance.get("period_basis") or capital.get("period_basis") or liquidity.get("period_basis"),
        },
        "market_metrics": {
            "market_date": market.get("date"),
            "stock_return_5d_pct": _ratio_to_pct(market.get("stock_return_5d")),
            "stock_return_20d_pct": _ratio_to_pct(market.get("stock_return_20d")),
            "stock_return_60d_pct": _ratio_to_pct(market.get("stock_return_60d")),
            "stock_excess_return_20d_pct": _ratio_to_pct(market.get("stock_excess_return_20d")),
            "stock_relative_strength_60_pct": _ratio_to_pct(market.get("stock_relative_strength_60")),
            "stock_volume_ratio_20": _number(market.get("stock_volume_ratio_20")),
        },
        "competitor_summary": _compact_competitor_summary(competitor_summary),
    }
    row["data_quality"] = {"missing_fields": _missing_fields(row)}
    return row


def _build_dataset_payload(
    *,
    target: RunIdentity,
    peers: list[RunIdentity],
    rows: list[dict[str, Any]],
    output_root: Path,
) -> dict[str, Any]:
    source_files = {
        row["run_key"]: {
            "financial_final_report": str(output_root / "Financial" / row["run_key"] / "final_report.json"),
            "market_full_dataset": str(output_root / "Y_Finance" / row["run_key"] / "market_full_dataset.csv"),
            "competitor_summary_report": str(output_root / "Competitor" / row["run_key"] / "competitor_summary_report.json")
            if (output_root / "Competitor" / row["run_key"] / "competitor_summary_report.json").exists()
            else "",
        }
        for row in rows
    }
    return {
        "agent_name": "Peer Comparison Agent",
        "role": "domestic-only peer comparison dataset builder",
        "output_version": OUTPUT_VERSION,
        "created_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "target_run_key": target.run_key,
        "target_company": target.company_name,
        "peer_scope": "domestic_only",
        "excluded_scope": EXCLUDED_SCOPE,
        "peer_groups": {
            "target": {"run_key": target.run_key, "company_name": target.company_name},
            "domestic_peers": [
                {"run_key": peer.run_key, "company_name": peer.company_name}
                for peer in peers
            ],
            "global_peers": [],
        },
        "metrics": rows,
        "comparison_limits": [
            "국내 비교군은 현재 확보된 동일 기준일 국내 비교 대상만 포함한다.",
            "글로벌 peer 비교는 현재 데이터 범위에 없어 생성하지 않는다.",
            "PER/PBR/PSR/EV/Sales 등 valuation 비교는 현재 데이터 범위에 없어 생성하지 않는다.",
            "완전한 업종 평균 비교는 업종 전체 표본이 없어 생성하지 않는다.",
            "누적 기간과 연간 기준이 섞일 수 있어 기간 기준을 확인한 뒤 해석해야 한다.",
        ],
        "source_files": source_files,
    }


def _build_positioning_payload(dataset: dict[str, Any]) -> dict[str, Any]:
    rows = dataset.get("metrics", [])
    target = next((row for row in rows if row.get("peer_group") == "target"), rows[0] if rows else {})
    ranks = _compute_ranks(rows, target)
    financial = target.get("financial_metrics", {})
    market = target.get("market_metrics", {})
    recommendation_context = "Strategy 투자의견은 별도 Strategy 산출물 기준으로 유지한다."

    strengths = _strength_sentences(target, ranks)
    discounts = _discount_sentences(target, rows, ranks)
    implication = _investment_implication(target, strengths, discounts)
    return {
        "agent_name": "Peer Comparison Agent",
        "output_version": OUTPUT_VERSION,
        "created_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "target_run_key": dataset.get("target_run_key"),
        "target_company": dataset.get("target_company"),
        "peer_scope": dataset.get("peer_scope"),
        "excluded_scope": dataset.get("excluded_scope", []),
        "peer_count": len(rows),
        "domestic_peer_count": max(len(rows) - 1, 0),
        "target_snapshot": {
            "financial_metrics": financial,
            "market_metrics": market,
        },
        "relative_positioning": {
            "metric_ranks": ranks,
            "revenue_scale": _rank_sentence("매출 규모", ranks.get("revenue_100m"), higher_is_better=True),
            "profitability": _profitability_sentence(financial, ranks),
            "financial_stability": _stability_sentence(financial, ranks),
            "market_performance": _market_sentence(market, ranks),
            "valuation": "가치평가 지표는 현재 데이터에 없어 peer 대비 할인/프리미엄을 판단하지 않는다.",
        },
        "relative_attractiveness": {
            "attractive_points": strengths,
            "discount_factors": discounts,
            "investment_implication": implication,
            "recommendation_context": recommendation_context,
        },
        "comparison_limits": dataset.get("comparison_limits", []),
    }


def _compute_ranks(rows: list[dict[str, Any]], target: dict[str, Any]) -> dict[str, Any]:
    specs = {
        "revenue_100m": ("financial_metrics", "revenue_100m", False),
        "revenue_growth_pct": ("financial_metrics", "revenue_growth_pct", False),
        "contribution_margin_pct": ("financial_metrics", "contribution_margin_pct", False),
        "sga_margin_pct": ("financial_metrics", "sga_margin_pct", True),
        "eps": ("financial_metrics", "eps", False),
        "operating_cash_flow_100m": ("financial_metrics", "operating_cash_flow_100m", False),
        "current_ratio_pct": ("financial_metrics", "current_ratio_pct", False),
        "debt_ratio_pct": ("financial_metrics", "debt_ratio_pct", True),
        "stock_excess_return_20d_pct": ("market_metrics", "stock_excess_return_20d_pct", False),
        "stock_relative_strength_60_pct": ("market_metrics", "stock_relative_strength_60_pct", False),
    }
    ranks: dict[str, Any] = {}
    target_run_key = target.get("run_key")
    for rank_key, (section, metric_key, ascending) in specs.items():
        values = []
        for row in rows:
            value = _number(row.get(section, {}).get(metric_key))
            if value is None:
                continue
            values.append((row.get("run_key"), value))
        target_value = _number(target.get(section, {}).get(metric_key))
        if target_value is None or not values:
            ranks[rank_key] = {
                "rank": None,
                "total_available": len(values),
                "target_value": target_value,
                "higher_is_better": not ascending,
                "available": False,
            }
            continue
        values.sort(key=lambda item: item[1], reverse=not ascending)
        rank = next((index for index, (run_key, _) in enumerate(values, start=1) if run_key == target_run_key), None)
        ranks[rank_key] = {
            "rank": rank,
            "total_available": len(values),
            "target_value": target_value,
            "higher_is_better": not ascending,
            "available": rank is not None,
        }
    return ranks


def _strength_sentences(target: dict[str, Any], ranks: dict[str, Any]) -> list[str]:
    company = target.get("company_name") or "대상 기업"
    financial = target.get("financial_metrics", {})
    strengths: list[str] = []
    revenue_rank = ranks.get("revenue_100m", {})
    if revenue_rank.get("rank") == 1:
        strengths.append(
            f"{company}은 비교 가능한 국내 peer 중 매출 규모가 가장 크게 나타나 사업 규모 측면에서 우위가 확인된다."
        )
    margin_rank = ranks.get("contribution_margin_pct", {})
    sga_rank = ranks.get("sga_margin_pct", {})
    if margin_rank.get("rank") == 1 and sga_rank.get("rank") == 1:
        strengths.append(
            "공헌이익률은 비교군 최상위, 판관비율은 가장 낮은 축으로 나타나 수익성 구조와 비용 효율성에서 상대 우위가 확인된다."
        )
    current_rank = ranks.get("current_ratio_pct", {})
    debt_rank = ranks.get("debt_ratio_pct", {})
    if current_rank.get("rank") == 1 or debt_rank.get("rank") == 1:
        strengths.append(
            "유동비율과 부채비율 기준 재무 안정성이 peer 대비 우수해 리스크 구간에서 방어 논리를 제공한다."
        )
    ocf = _number(financial.get("operating_cash_flow_100m"))
    if ocf is not None and ocf > 0:
        strengths.append(
            f"영업현금흐름이 {ocf:,.0f}억원으로 양수 구간에 있어 재무 개선 신호를 현금 창출 측면에서 보완한다."
        )
    return strengths[:4] or ["현재 비교 가능한 정량 지표 안에서는 일부 재무 안정성 지표가 확인된다."]


def _discount_sentences(target: dict[str, Any], rows: list[dict[str, Any]], ranks: dict[str, Any]) -> list[str]:
    market = target.get("market_metrics", {})
    missing_count = sum(1 for row in rows if row.get("data_quality", {}).get("missing_fields"))
    discounts: list[str] = []
    excess = _number(market.get("stock_excess_return_20d_pct"))
    relative = _number(market.get("stock_relative_strength_60_pct"))
    if excess is not None and excess < 0:
        discounts.append(
            f"20일 초과수익률이 {excess:.2f}%로 음수여서 재무 우위가 아직 시장 대비 성과로 충분히 전환됐다고 보기 어렵다."
        )
    if relative is not None and relative < 0:
        discounts.append(
            f"60일 상대강도도 {relative:.2f}%로 약세가 남아 있어 peer 대비 선호 회복은 추가 확인이 필요하다."
        )
    if missing_count:
        discounts.append(
            f"비교군 중 {missing_count}개 회사에 일부 재무 항목 결측이 있어 peer 우위 판단은 확인 가능한 지표로 제한된다."
        )
    discounts.append("가치평가 지표가 없어 peer 대비 할인 또는 프리미엄 여부는 판단하지 않는다.")
    return discounts[:4]


def _investment_implication(target: dict[str, Any], strengths: list[str], discounts: list[str]) -> str:
    company = target.get("company_name") or "대상 기업"
    if len(strengths) >= 3:
        strength_text = "매출 규모, 수익성 구조, 재무 안정성에서 상대 우위"
    elif len(strengths) >= 1:
        strength_text = "확인 가능한 재무 지표에서 상대 우위"
    else:
        strength_text = "일부 재무 안정성 지표"
    if any("초과수익률" in item or "상대강도" in item for item in discounts):
        discount_text = "시장 대비 성과 약세"
    else:
        discount_text = "비교 데이터의 범위 제한"
    return (
        f"{company}은 국내 peer 대비 {strength_text}가 확인되지만, {discount_text}가 함께 남아 있다. "
        "따라서 peer 비교는 긍정 요인을 보강하지만, 가치평가 지표와 글로벌 비교가 없는 현재 데이터 범위에서는 투자의견을 공격적으로 상향하기보다 균형 판단을 유지하는 상대 위치 증거로 쓰는 것이 적절하다."
    )


def _rank_sentence(label: str, rank_info: dict[str, Any] | None, *, higher_is_better: bool) -> str:
    if not rank_info or not rank_info.get("available"):
        return f"{label}는 비교 가능한 데이터가 부족하다."
    direction = "높은 순" if higher_is_better else "낮은 순"
    return f"{label}는 비교 가능 기업 {rank_info.get('total_available')}개 중 {direction} {rank_info.get('rank')}위다."


def _profitability_sentence(financial: dict[str, Any], ranks: dict[str, Any]) -> str:
    margin = _number(financial.get("contribution_margin_pct"))
    sga = _number(financial.get("sga_margin_pct"))
    margin_rank = ranks.get("contribution_margin_pct", {})
    sga_rank = ranks.get("sga_margin_pct", {})
    if margin is None and sga is None:
        return "수익성 비교는 공헌이익률과 판관비율 데이터가 부족해 제한적이다."
    return (
        f"공헌이익률 {margin:.2f}%"
        if margin is not None
        else "공헌이익률 N/A"
    ) + (
        f", 판관비율 {sga:.2f}%로 확인되며, "
        if sga is not None
        else ", 판관비율 N/A로 확인되며, "
    ) + (
        f"공헌이익률 순위 {margin_rank.get('rank')}/{margin_rank.get('total_available')}, "
        f"낮은 판관비율 순위 {sga_rank.get('rank')}/{sga_rank.get('total_available')}다."
    )


def _stability_sentence(financial: dict[str, Any], ranks: dict[str, Any]) -> str:
    current = _number(financial.get("current_ratio_pct"))
    debt = _number(financial.get("debt_ratio_pct"))
    if current is None and debt is None:
        return "유동성과 레버리지 비교는 데이터가 부족하다."
    return (
        f"유동비율 {current:.1f}%"
        if current is not None
        else "유동비율 N/A"
    ) + (
        f", 부채비율 {debt:.1f}%로 확인되며 "
        if debt is not None
        else ", 부채비율 N/A로 확인되며 "
    ) + (
        f"유동성 순위 {ranks.get('current_ratio_pct', {}).get('rank')}/{ranks.get('current_ratio_pct', {}).get('total_available')}, "
        f"낮은 부채비율 순위 {ranks.get('debt_ratio_pct', {}).get('rank')}/{ranks.get('debt_ratio_pct', {}).get('total_available')}다."
    )


def _market_sentence(market: dict[str, Any], ranks: dict[str, Any]) -> str:
    excess = _number(market.get("stock_excess_return_20d_pct"))
    relative = _number(market.get("stock_relative_strength_60_pct"))
    if excess is None and relative is None:
        return "시장 성과 비교는 데이터가 부족하다."
    return (
        f"20일 초과수익률 {excess:.2f}%"
        if excess is not None
        else "20일 초과수익률 N/A"
    ) + (
        f", 60일 상대강도 {relative:.2f}%로 "
        if relative is not None
        else ", 60일 상대강도 N/A로 "
    ) + (
        f"초과수익률 순위 {ranks.get('stock_excess_return_20d_pct', {}).get('rank')}/{ranks.get('stock_excess_return_20d_pct', {}).get('total_available')}, "
        f"상대강도 순위 {ranks.get('stock_relative_strength_60_pct', {}).get('rank')}/{ranks.get('stock_relative_strength_60_pct', {}).get('total_available')}다."
    )


def _render_summary_md(dataset: dict[str, Any], positioning: dict[str, Any]) -> str:
    target = dataset.get("target_company", "대상 기업")
    attractive = positioning.get("relative_attractiveness", {}).get("attractive_points", [])
    discounts = positioning.get("relative_attractiveness", {}).get("discount_factors", [])
    return "\n".join(
        [
            f"# Peer Comparison v1 - {target}",
            "",
            "## Scope",
            "- 국내 peer 비교만 포함",
            "- 글로벌 peer / valuation / 완전한 업종 평균 비교 제외",
            "",
            "## Attractive Points",
            *[f"- {item}" for item in attractive],
            "",
            "## Discount Factors",
            *[f"- {item}" for item in discounts],
            "",
            "## Investment Implication",
            positioning.get("relative_attractiveness", {}).get("investment_implication", ""),
            "",
        ]
    )


def _supporting_features(detailed: dict[str, Any], section: str) -> dict[str, Any]:
    value = detailed.get(section, {})
    if not isinstance(value, dict):
        return {}
    features = value.get("supporting_features", {})
    return features if isinstance(features, dict) else {}


def _latest_market_row(path: Path) -> dict[str, Any]:
    df = pd.read_csv(path)
    if df.empty:
        return {}
    if "date" in df.columns:
        df["date"] = pd.to_datetime(df["date"], errors="coerce")
        df = df.sort_values("date")
    latest = df.iloc[-1].to_dict()
    if isinstance(latest.get("date"), pd.Timestamp):
        latest["date"] = latest["date"].strftime("%Y-%m-%d")
    return latest


def _compact_competitor_summary(payload: dict[str, Any]) -> dict[str, Any]:
    if not payload:
        return {"available": False}
    return {
        "available": True,
        "summary": payload.get("summary", ""),
        "strengths": payload.get("strengths", [])[:3] if isinstance(payload.get("strengths"), list) else [],
        "risks": payload.get("risks", [])[:3] if isinstance(payload.get("risks"), list) else [],
    }


def _missing_fields(row: dict[str, Any]) -> list[str]:
    missing: list[str] = []
    for section_key in ["financial_metrics", "market_metrics"]:
        section = row.get(section_key, {})
        for key, value in section.items():
            if key.endswith("_basis") or key.endswith("_period") or key == "market_date":
                continue
            if value is None:
                missing.append(f"{section_key}.{key}")
    return missing


def _krw_to_100m(value: Any) -> float | None:
    numeric = _number(value)
    if numeric is None:
        return None
    return numeric / 100_000_000


def _ratio_to_pct(value: Any) -> float | None:
    numeric = _number(value)
    if numeric is None:
        return None
    return numeric * 100.0


def _number(value: Any) -> float | None:
    if value is None:
        return None
    try:
        if pd.isna(value):
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def _date_suffix(run_key: str) -> str | None:
    suffix = run_key.rsplit("_", 1)[-1]
    if len(suffix) == 8 and suffix.isdigit():
        return suffix
    return None


def _load_json(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as file:
        payload = json.load(file)
    if not isinstance(payload, dict):
        raise ValueError(f"Expected JSON object: {path}")
    return payload


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


__all__ = [
    "EXCLUDED_SCOPE",
    "OUTPUT_VERSION",
    "PeerComparisonPaths",
    "generate_peer_comparison",
    "load_identity_from_config",
    "RunIdentity",
    "build_run_key",
    "normalize_date",
]
