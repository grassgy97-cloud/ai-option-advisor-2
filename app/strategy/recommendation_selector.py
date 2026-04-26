from __future__ import annotations

from collections import defaultdict
from typing import Any, Dict, List, Optional


def _extract_family(strategy: Any) -> str:
    metadata = getattr(strategy, "metadata", {}) or {}
    family = metadata.get("strategy_family")
    if family:
        return str(family)
    strategy_metadata = metadata.get("strategy_metadata", {}) or {}
    family = strategy_metadata.get("strategy_family")
    return str(family or "unknown")


def _extract_execution_quality(strategy: Any) -> float:
    metadata = getattr(strategy, "metadata", {}) or {}
    ranking_components = metadata.get("ranking_components", {}) or {}
    value = ranking_components.get("execution_quality")
    if value is None:
        breakdown = getattr(strategy, "score_breakdown", {}) or {}
        value = breakdown.get("execution_quality")
    try:
        return float(value)
    except Exception:
        return 0.5


def _extract_risk_flags(strategy: Any) -> List[str]:
    metadata = getattr(strategy, "metadata", {}) or {}
    greeks_report = metadata.get("greeks_report", {}) or {}
    flags = greeks_report.get("risk_flags", []) or []
    return [str(flag) for flag in flags]


def _is_poor_primary_candidate(strategy: Any) -> bool:
    execution_quality = _extract_execution_quality(strategy)
    risk_flags = _extract_risk_flags(strategy)
    return execution_quality < 0.25 or len(risk_flags) >= 3


def _is_reasonable_secondary(strategy: Any) -> bool:
    score = float(getattr(strategy, "score", 0.0) or 0.0)
    execution_quality = _extract_execution_quality(strategy)
    return score >= 0.58 and execution_quality >= 0.20


def _is_watchlist_candidate(strategy: Any) -> bool:
    score = float(getattr(strategy, "score", 0.0) or 0.0)
    return score >= 0.40


def _is_near_duplicate(lhs: Any, rhs: Any) -> bool:
    return (
        getattr(lhs, "underlying_id", None) == getattr(rhs, "underlying_id", None)
        and _extract_family(lhs) == _extract_family(rhs)
    )


def _build_item(strategy: Any, decision_label: str, decision_reason: str) -> Dict[str, Any]:
    return {
        "underlying_id": strategy.underlying_id,
        "strategy_type": strategy.strategy_type,
        "score": round(float(strategy.score or 0.0), 4),
        "family": _extract_family(strategy),
        "decision_label": decision_label,
        "decision_reason": decision_reason,
    }


def _group_ranked(ranked: List[Any]) -> Dict[str, List[Any]]:
    grouped: Dict[str, List[Any]] = defaultdict(list)
    for strategy in ranked:
        grouped[strategy.underlying_id].append(strategy)
    for underlying_id in grouped:
        grouped[underlying_id] = sorted(
            grouped[underlying_id],
            key=lambda strategy: float(strategy.score or 0.0),
            reverse=True,
        )
    return dict(grouped)


def _choose_preferred_underlying(grouped: Dict[str, List[Any]]) -> Optional[str]:
    if not grouped:
        return None
    ordered = sorted(
        grouped.items(),
        key=lambda item: (
            -float(item[1][0].score or 0.0),
            -len(item[1]),
            item[0],
        ),
    )
    return ordered[0][0]


def select_recommendations(
    ranked: List[Any],
    market_context: Optional[Dict[str, Any]] = None,
    intent: Optional[Any] = None,
) -> Dict[str, Any]:
    grouped = _group_ranked(ranked)
    preferred_underlying_id = _choose_preferred_underlying(grouped)

    primary_recommendations: List[Dict[str, Any]] = []
    secondary_recommendations: List[Dict[str, Any]] = []
    watchlist: List[Dict[str, Any]] = []

    if not grouped:
        payload = {
            "preferred_underlying_id": None,
            "primary_recommendations": [],
            "secondary_recommendations": [],
            "watchlist": [],
            "decision_notes": {
                "mode": "empty",
                "primary_count": 0,
                "secondary_count": 0,
                "watchlist_count": 0,
                "cross_underlying_gap": None,
                "family_diversity_applied": False,
            },
        }
        print("[decision_layer] preferred_underlying_id=None primary=0 secondary=0 watchlist=0")
        return payload

    ordered_underlyings = sorted(
        grouped.keys(),
        key=lambda uid: (
            -float(grouped[uid][0].score or 0.0),
            -len(grouped[uid]),
            uid,
        ),
    )
    top_score = float(grouped[ordered_underlyings[0]][0].score or 0.0)
    second_score = (
        float(grouped[ordered_underlyings[1]][0].score or 0.0)
        if len(ordered_underlyings) > 1
        else None
    )
    cross_underlying_gap = (
        round(top_score - second_score, 4)
        if second_score is not None
        else None
    )

    primary_source_group = grouped[preferred_underlying_id] if preferred_underlying_id else []
    family_diversity_applied = False

    if primary_source_group:
        top1 = primary_source_group[0]
        if not _is_poor_primary_candidate(top1):
            primary_recommendations.append(
                _build_item(top1, "primary", "top_score_leads")
            )
        elif _is_reasonable_secondary(top1):
            secondary_recommendations.append(
                _build_item(top1, "secondary", "top_candidate_demoted_by_risk_execution")
            )
        elif _is_watchlist_candidate(top1):
            watchlist.append(
                _build_item(top1, "watchlist", "top_candidate_watchlist_due_to_risk_execution")
            )

        if len(primary_source_group) >= 2:
            top2 = primary_source_group[1]
            gap = float(top1.score or 0.0) - float(top2.score or 0.0)
            if (
                primary_recommendations
                and not _is_poor_primary_candidate(top2)
                and gap <= 0.05
                and not _is_near_duplicate(top1, top2)
            ):
                primary_recommendations.append(
                    _build_item(top2, "primary", "small_gap_and_family_diverse")
                )
                family_diversity_applied = True
            elif _is_reasonable_secondary(top2):
                secondary_recommendations.append(
                    _build_item(top2, "secondary", "close_alternative_but_not_primary")
                )
            elif _is_watchlist_candidate(top2):
                watchlist.append(
                    _build_item(top2, "watchlist", "weaker_alternative_watchlist")
                )

        for strategy in primary_source_group[2:]:
            if _is_reasonable_secondary(strategy):
                secondary_recommendations.append(
                    _build_item(strategy, "secondary", "ranked_reasonable_non_primary")
                )
            elif _is_watchlist_candidate(strategy):
                watchlist.append(
                    _build_item(strategy, "watchlist", "tradable_but_low_conviction")
                )

    if len(ordered_underlyings) > 1:
        for index, underlying_id in enumerate(ordered_underlyings[1:], start=1):
            group = grouped[underlying_id]
            if not group:
                continue
            candidate = group[0]
            gap = top_score - float(candidate.score or 0.0)
            if (
                index == 1
                and gap <= 0.05
                and not _is_poor_primary_candidate(candidate)
            ):
                secondary_recommendations.append(
                    _build_item(candidate, "secondary", "cross_underlying_close_second")
                )
            elif _is_reasonable_secondary(candidate):
                secondary_recommendations.append(
                    _build_item(candidate, "secondary", "other_underlying_reference")
                )
            elif _is_watchlist_candidate(candidate):
                watchlist.append(
                    _build_item(candidate, "watchlist", "other_underlying_watchlist")
                )

            for strategy in group[1:]:
                if _is_reasonable_secondary(strategy):
                    secondary_recommendations.append(
                        _build_item(strategy, "secondary", "non_preferred_underlying_secondary")
                    )
                elif _is_watchlist_candidate(strategy):
                    watchlist.append(
                        _build_item(strategy, "watchlist", "non_preferred_underlying_watchlist")
                    )

    secondary_recommendations = secondary_recommendations[:4]
    watchlist = watchlist[:4]

    payload = {
        "preferred_underlying_id": preferred_underlying_id,
        "primary_recommendations": primary_recommendations,
        "secondary_recommendations": secondary_recommendations,
        "watchlist": watchlist,
        "decision_notes": {
            "mode": "multi_underlying" if len(grouped) > 1 else "single_underlying",
            "primary_count": len(primary_recommendations),
            "secondary_count": len(secondary_recommendations),
            "watchlist_count": len(watchlist),
            "cross_underlying_gap": cross_underlying_gap,
            "family_diversity_applied": family_diversity_applied,
        },
    }
    print(
        f"[decision_layer] preferred_underlying_id={preferred_underlying_id} "
        f"primary={len(primary_recommendations)} "
        f"secondary={len(secondary_recommendations)} "
        f"watchlist={len(watchlist)}"
    )
    return payload
