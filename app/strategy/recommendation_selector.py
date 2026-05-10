from __future__ import annotations

from collections import defaultdict
from typing import Any, Dict, List, Optional

_RANGE_INCOME_STRATEGIES = {"iron_condor", "iron_fly"}
_SINGLE_SIDE_CREDIT_STRATEGIES = {"bear_call_spread", "bull_put_spread"}
_AGGRESSIVE_SHORT_GAMMA_STRATEGIES = {"naked_call", "naked_put"}
_STRICT_RANGE_IRON_REASON = "strict_range_income_prefers_iron"
_STRICT_RANGE_SINGLE_SIDE_REASON = "single_side_credit_downgraded_for_strict_range"
_SHORT_TERM_UPSIDE_BLOCKS_NAKED_CALL_REASON = "short_term_upside_risk_blocks_naked_call"
_LONG_TERM_OPPOSITE_PREFERS_DEFINED_RISK_REASON = "long_term_opposite_view_prefers_defined_risk"
_HORIZON_CONFLICT_REASON = "horizon_conflict_detected"
_PRIMARY_EXECUTION_QUALITY_MIN = 0.60
_DEFAULT_MAX_REL_SPREAD = 0.05
_QUOTE_WATCHLIST_REASONS = {
    "quote_missing",
    "quote_abnormal",
    "bid_ask_inverted",
    "relative_spread_too_wide",
}
_GUIDANCE_WATCHLIST_REASONS = {
    "do_not_chase",
    "bad_execution_guidance",
    "not_tradable",
}
_EXECUTION_VETO_REASON_CODES = {
    "execution_quality_below_primary_threshold": "execution_quality_too_low",
    "quote_missing": "quote_invalid",
    "quote_abnormal": "quote_invalid",
    "bid_ask_inverted": "quote_invalid",
    "relative_spread_too_wide": "spread_too_wide",
    "do_not_chase": "do_not_chase",
    "bad_execution_guidance": "do_not_chase",
    "not_tradable": "not_tradable",
}


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


def _leg_quote_values(leg: Any) -> tuple[Optional[float], Optional[float], Optional[float]]:
    values = []
    for field in ("bid", "ask", "mid"):
        value = getattr(leg, field, None)
        try:
            values.append(float(value) if value is not None else None)
        except Exception:
            values.append(None)
    return values[0], values[1], values[2]


def _max_rel_spread_for_intent(intent: Optional[Any]) -> float:
    try:
        value = float(getattr(intent, "max_rel_spread", _DEFAULT_MAX_REL_SPREAD))
    except Exception:
        return _DEFAULT_MAX_REL_SPREAD
    return value if value > 0 else _DEFAULT_MAX_REL_SPREAD


def _quote_watchlist_reason(strategy: Any, max_rel_spread: float) -> Optional[str]:
    legs = list(getattr(strategy, "legs", []) or [])
    if not legs:
        return None

    for leg in legs:
        bid, ask, mid = _leg_quote_values(leg)
        if bid is None or ask is None or mid is None:
            return "quote_missing"
        if bid <= 0 or ask <= 0 or mid <= 0:
            return "quote_abnormal"
        if ask < bid:
            return "bid_ask_inverted"
        if (ask - bid) / mid > max_rel_spread:
            return "relative_spread_too_wide"

    return None


def _execution_guidance_veto_reason(strategy: Any) -> Optional[str]:
    metadata = getattr(strategy, "metadata", {}) or {}
    guidance = getattr(strategy, "execution_guidance", None) or metadata.get("execution_guidance")
    if not isinstance(guidance, dict):
        return None

    status_fields = (
        "execution_status",
        "status",
        "tradeability",
        "recommendation",
        "decision",
        "verdict",
        "spread_quality",
    )
    for field in status_fields:
        value = guidance.get(field)
        if not isinstance(value, str):
            continue
        normalized = value.strip().lower().replace("-", "_").replace(" ", "_")
        if normalized == "do_not_chase":
            return "do_not_chase"
        if normalized == "not_tradable":
            return "not_tradable"
        if normalized == "bad":
            return "bad_execution_guidance"
        if field == "spread_quality" and normalized == "poor":
            return "do_not_chase"
    return None


def _primary_execution_veto_reason(strategy: Any, max_rel_spread: float) -> Optional[str]:
    quote_reason = _quote_watchlist_reason(strategy, max_rel_spread)
    if quote_reason is not None:
        return quote_reason
    if _extract_execution_quality(strategy) < _PRIMARY_EXECUTION_QUALITY_MIN:
        return "execution_quality_below_primary_threshold"
    guidance_reason = _execution_guidance_veto_reason(strategy)
    if guidance_reason is not None:
        return guidance_reason
    return None


def _record_downgrade(strategy: Any, reason: str, target_tier: str) -> None:
    metadata = getattr(strategy, "metadata", None)
    if not isinstance(metadata, dict):
        metadata = {}
        try:
            strategy.metadata = metadata
        except Exception:
            return
    metadata["downgrade_reason"] = reason
    metadata["downgrade_target_tier"] = target_tier


def _record_reason_code(strategy: Any, reason: str) -> None:
    metadata = getattr(strategy, "metadata", None)
    if not isinstance(metadata, dict):
        metadata = {}
        try:
            strategy.metadata = metadata
        except Exception:
            return

    reason_codes = metadata.setdefault("reason_codes", [])
    if isinstance(reason_codes, list) and reason not in reason_codes:
        reason_codes.append(reason)


def _record_execution_veto(strategy: Any, reason: str, target_tier: str) -> None:
    reason_code = _EXECUTION_VETO_REASON_CODES.get(reason, reason)
    _record_downgrade(strategy, reason, target_tier)
    metadata = getattr(strategy, "metadata", None)
    if not isinstance(metadata, dict):
        return
    metadata["execution_checked"] = True
    metadata["execution_quality"] = round(_extract_execution_quality(strategy), 4)
    metadata["spread_check_passed"] = reason not in _QUOTE_WATCHLIST_REASONS
    metadata["execution_veto"] = True
    metadata["reason_code"] = reason_code
    _record_reason_code(strategy, reason_code)


def _record_execution_check(strategy: Any, max_rel_spread: float) -> None:
    metadata = getattr(strategy, "metadata", None)
    if not isinstance(metadata, dict):
        metadata = {}
        try:
            strategy.metadata = metadata
        except Exception:
            return
    quote_reason = _quote_watchlist_reason(strategy, max_rel_spread)
    metadata["execution_checked"] = True
    metadata["execution_quality"] = round(_extract_execution_quality(strategy), 4)
    metadata["spread_check_passed"] = quote_reason is None
    metadata.setdefault("execution_veto", False)


def _horizon_view(intent: Optional[Any], key: str) -> Dict[str, Any]:
    views = getattr(intent, "horizon_views", None) if intent is not None else None
    if not isinstance(views, dict):
        return {}
    item = views.get(key)
    return item if isinstance(item, dict) else {}


def _direction_of(view: Dict[str, Any]) -> str:
    return str(view.get("direction", "unknown") or "unknown")


def _is_bullish_or_upside_risk(view: Dict[str, Any]) -> bool:
    return _direction_of(view) in ("bullish", "upside_risk", "recovery")


def _has_bearish_short_upside_conflict(intent: Optional[Any]) -> bool:
    if intent is None or getattr(intent, "market_view", None) != "bearish":
        return False
    short = _horizon_view(intent, "short_term")
    return _is_bullish_or_upside_risk(short)


def _has_medium_bear_long_bull_conflict(intent: Optional[Any]) -> bool:
    if intent is None:
        return False
    medium = _horizon_view(intent, "medium_term")
    long_term = _horizon_view(intent, "long_term")
    return _direction_of(medium) == "bearish" and _is_bullish_or_upside_risk(long_term)


def _primary_horizon_veto_reason(strategy: Any, intent: Optional[Any]) -> Optional[str]:
    strategy_type = getattr(strategy, "strategy_type", None)
    if strategy_type == "naked_call" and _has_bearish_short_upside_conflict(intent):
        return _SHORT_TERM_UPSIDE_BLOCKS_NAKED_CALL_REASON
    if (
        strategy_type in _AGGRESSIVE_SHORT_GAMMA_STRATEGIES
        and _has_medium_bear_long_bull_conflict(intent)
    ):
        return _LONG_TERM_OPPOSITE_PREFERS_DEFINED_RISK_REASON
    return None


def _record_horizon_veto(strategy: Any, reason: str, target_tier: str) -> None:
    _record_downgrade(strategy, reason, target_tier)
    metadata = getattr(strategy, "metadata", None)
    if not isinstance(metadata, dict):
        return
    metadata["horizon_veto"] = True
    metadata["reason_code"] = reason
    _record_reason_code(strategy, reason)
    _record_reason_code(strategy, _HORIZON_CONFLICT_REASON)


def _record_horizon_note_if_applicable(strategy: Any, intent: Optional[Any], target_tier: str) -> None:
    reason = _primary_horizon_veto_reason(strategy, intent)
    if reason is None:
        return
    _record_horizon_veto(strategy, reason, target_tier)


def _extract_risk_flags(strategy: Any) -> List[str]:
    metadata = getattr(strategy, "metadata", {}) or {}
    greeks_report = metadata.get("greeks_report", {}) or {}
    flags = greeks_report.get("risk_flags", []) or []
    return [str(flag) for flag in flags]


def _is_poor_primary_candidate(strategy: Any, max_rel_spread: float = _DEFAULT_MAX_REL_SPREAD) -> bool:
    if _primary_execution_veto_reason(strategy, max_rel_spread) is not None:
        return True
    execution_quality = _extract_execution_quality(strategy)
    risk_flags = _extract_risk_flags(strategy)
    return execution_quality < 0.25 or len(risk_flags) >= 3


def _is_reasonable_secondary(strategy: Any, max_rel_spread: float = _DEFAULT_MAX_REL_SPREAD) -> bool:
    score = float(getattr(strategy, "score", 0.0) or 0.0)
    execution_quality = _extract_execution_quality(strategy)
    return (
        score >= 0.58
        and execution_quality >= 0.20
        and _quote_watchlist_reason(strategy, max_rel_spread) is None
    )


def _is_watchlist_candidate(strategy: Any) -> bool:
    score = float(getattr(strategy, "score", 0.0) or 0.0)
    return score >= 0.40


def _is_near_duplicate(lhs: Any, rhs: Any) -> bool:
    return (
        getattr(lhs, "underlying_id", None) == getattr(rhs, "underlying_id", None)
        and _extract_family(lhs) == _extract_family(rhs)
    )


def _build_item(strategy: Any, decision_label: str, decision_reason: str) -> Dict[str, Any]:
    metadata = getattr(strategy, "metadata", {}) or {}
    item = {
        "underlying_id": strategy.underlying_id,
        "strategy_type": strategy.strategy_type,
        "score": round(float(strategy.score or 0.0), 4),
        "family": _extract_family(strategy),
        "decision_label": decision_label,
        "decision_reason": decision_reason,
    }
    if metadata.get("downgrade_reason"):
        item["downgrade_reason"] = metadata.get("downgrade_reason")
        item["downgrade_target_tier"] = metadata.get("downgrade_target_tier")
    if "execution_checked" in metadata:
        item["execution_checked"] = metadata.get("execution_checked")
        item["execution_quality"] = metadata.get("execution_quality")
        item["spread_check_passed"] = metadata.get("spread_check_passed")
    if "execution_veto" in metadata:
        item["execution_veto"] = metadata.get("execution_veto")
        item["reason_code"] = metadata.get("reason_code")
    elif "reason_code" in metadata:
        item["reason_code"] = metadata.get("reason_code")
    if "horizon_veto" in metadata:
        item["horizon_veto"] = metadata.get("horizon_veto")
    if isinstance(metadata.get("reason_codes"), list):
        item["reason_codes"] = metadata.get("reason_codes")
    return item


def _add_execution_vetoed_candidate(
    strategy: Any,
    secondary_recommendations: List[Dict[str, Any]],
    watchlist: List[Dict[str, Any]],
    decision_reason: str,
    max_rel_spread: float,
) -> bool:
    reason = _primary_execution_veto_reason(strategy, max_rel_spread)
    if reason is None:
        return False

    if (
        reason in _QUOTE_WATCHLIST_REASONS
        or reason in _GUIDANCE_WATCHLIST_REASONS
        or not _is_reasonable_secondary(strategy, max_rel_spread)
    ):
        if _is_watchlist_candidate(strategy):
            _record_execution_veto(strategy, reason, "watchlist")
            watchlist.append(_build_item(strategy, "watchlist", decision_reason))
        return True

    _record_execution_veto(strategy, reason, "secondary")
    secondary_recommendations.append(_build_item(strategy, "secondary", decision_reason))
    return True


def _add_horizon_vetoed_candidate(
    strategy: Any,
    intent: Optional[Any],
    secondary_recommendations: List[Dict[str, Any]],
    watchlist: List[Dict[str, Any]],
    max_rel_spread: float,
) -> bool:
    reason = _primary_horizon_veto_reason(strategy, intent)
    if reason is None:
        return False

    if _is_reasonable_secondary(strategy, max_rel_spread):
        _record_horizon_veto(strategy, reason, "secondary")
        secondary_recommendations.append(_build_item(strategy, "secondary", reason))
    elif _is_watchlist_candidate(strategy):
        _record_horizon_veto(strategy, reason, "watchlist")
        watchlist.append(_build_item(strategy, "watchlist", reason))
    return True


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


def _has_positive_theta_intent(intent: Any) -> bool:
    greeks_preference = getattr(intent, "greeks_preference", {}) or {}
    theta_pref = greeks_preference.get("theta", {}) or {}
    return bool(
        getattr(intent, "require_positive_theta", False)
        or getattr(intent, "prefer_income_family", False)
        or theta_pref.get("sign") == "positive"
    )


def _is_strict_neutral_theta_range_intent(intent: Optional[Any]) -> bool:
    if intent is None:
        return False
    return bool(
        getattr(intent, "market_view", None) == "neutral"
        and getattr(intent, "range_bias", None) == "strict_range"
        and _has_positive_theta_intent(intent)
    )


def _choose_preferred_underlying_for_intent(
    grouped: Dict[str, List[Any]],
    intent: Optional[Any],
    max_rel_spread: float,
) -> Optional[str]:
    if not _is_strict_neutral_theta_range_intent(intent):
        return _choose_preferred_underlying(grouped)

    iron_candidates: List[Any] = []
    for group in grouped.values():
        iron_candidates.extend(
            strategy
            for strategy in group
            if strategy.strategy_type in _RANGE_INCOME_STRATEGIES
            and not _is_poor_primary_candidate(strategy, max_rel_spread)
        )
    if not iron_candidates:
        return _choose_preferred_underlying(grouped)
    best_iron = max(iron_candidates, key=lambda strategy: float(strategy.score or 0.0))
    return best_iron.underlying_id


def select_recommendations(
    ranked: List[Any],
    market_context: Optional[Dict[str, Any]] = None,
    intent: Optional[Any] = None,
) -> Dict[str, Any]:
    grouped = _group_ranked(ranked)
    max_rel_spread = _max_rel_spread_for_intent(intent)
    preferred_underlying_id = _choose_preferred_underlying_for_intent(
        grouped,
        intent,
        max_rel_spread,
    )

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
    strict_neutral_theta_range = _is_strict_neutral_theta_range_intent(intent)
    strict_range_iron_primary = None
    if primary_source_group and strict_neutral_theta_range:
        strict_range_iron_primary = next(
            (
                strategy for strategy in primary_source_group
                if strategy.strategy_type in _RANGE_INCOME_STRATEGIES
                and not _is_poor_primary_candidate(strategy, max_rel_spread)
            ),
            None,
        )

    if primary_source_group and strict_range_iron_primary is not None:
        _record_reason_code(strict_range_iron_primary, _STRICT_RANGE_IRON_REASON)
        _record_execution_check(strict_range_iron_primary, max_rel_spread)
        primary_recommendations.append(
            _build_item(strict_range_iron_primary, "primary", _STRICT_RANGE_IRON_REASON)
        )

        for strategy in primary_source_group:
            if strategy is strict_range_iron_primary:
                continue
            if _add_execution_vetoed_candidate(
                strategy,
                secondary_recommendations,
                watchlist,
                "post_rank_execution_veto",
                max_rel_spread,
            ):
                continue
            if strategy.strategy_type in _SINGLE_SIDE_CREDIT_STRATEGIES:
                if _is_reasonable_secondary(strategy, max_rel_spread):
                    _record_downgrade(strategy, _STRICT_RANGE_SINGLE_SIDE_REASON, "secondary")
                    secondary_recommendations.append(
                        _build_item(strategy, "secondary", _STRICT_RANGE_SINGLE_SIDE_REASON)
                    )
                elif _is_watchlist_candidate(strategy):
                    _record_downgrade(strategy, _STRICT_RANGE_SINGLE_SIDE_REASON, "watchlist")
                    watchlist.append(
                        _build_item(strategy, "watchlist", _STRICT_RANGE_SINGLE_SIDE_REASON)
                    )
                continue

            if _is_reasonable_secondary(strategy, max_rel_spread):
                _record_horizon_note_if_applicable(strategy, intent, "secondary")
                secondary_recommendations.append(
                    _build_item(strategy, "secondary", "ranked_reasonable_non_primary")
                )
            elif _is_watchlist_candidate(strategy):
                _record_horizon_note_if_applicable(strategy, intent, "watchlist")
                watchlist.append(
                    _build_item(strategy, "watchlist", "tradable_but_low_conviction")
                )

    elif primary_source_group:
        handled_ids = set()
        primary_strategy = None
        for index, strategy in enumerate(primary_source_group):
            handled_ids.add(id(strategy))
            if _add_execution_vetoed_candidate(
                strategy,
                secondary_recommendations,
                watchlist,
                "post_rank_execution_veto",
                max_rel_spread,
            ):
                continue
            if _add_horizon_vetoed_candidate(
                strategy,
                intent,
                secondary_recommendations,
                watchlist,
                max_rel_spread,
            ):
                continue
            if not _is_poor_primary_candidate(strategy, max_rel_spread):
                primary_strategy = strategy
                _record_execution_check(strategy, max_rel_spread)
                primary_recommendations.append(
                    _build_item(
                        strategy,
                        "primary",
                        "top_score_leads" if index == 0 else "next_candidate_after_execution_veto",
                    )
                )
                break
            if _is_reasonable_secondary(strategy, max_rel_spread):
                secondary_recommendations.append(
                    _build_item(strategy, "secondary", "top_candidate_demoted_by_risk_execution")
                )
            elif _is_watchlist_candidate(strategy):
                watchlist.append(
                    _build_item(strategy, "watchlist", "top_candidate_watchlist_due_to_risk_execution")
                )

        if primary_strategy is not None:
            for strategy in primary_source_group:
                if id(strategy) in handled_ids:
                    continue
                gap = float(primary_strategy.score or 0.0) - float(strategy.score or 0.0)
                if (
                    not _is_poor_primary_candidate(strategy, max_rel_spread)
                    and _primary_execution_veto_reason(strategy, max_rel_spread) is None
                    and _primary_horizon_veto_reason(strategy, intent) is None
                    and gap <= 0.05
                    and not _is_near_duplicate(primary_strategy, strategy)
                ):
                    _record_execution_check(strategy, max_rel_spread)
                    primary_recommendations.append(
                        _build_item(strategy, "primary", "small_gap_and_family_diverse")
                    )
                    family_diversity_applied = True
                    handled_ids.add(id(strategy))
                    break

        for strategy in primary_source_group:
            if id(strategy) in handled_ids:
                continue
            if _add_execution_vetoed_candidate(
                strategy,
                secondary_recommendations,
                watchlist,
                "post_rank_execution_veto",
                max_rel_spread,
            ):
                continue
            if _is_reasonable_secondary(strategy, max_rel_spread):
                _record_horizon_note_if_applicable(strategy, intent, "secondary")
                secondary_recommendations.append(
                    _build_item(strategy, "secondary", "ranked_reasonable_non_primary")
                )
            elif _is_watchlist_candidate(strategy):
                _record_horizon_note_if_applicable(strategy, intent, "watchlist")
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
            if _add_execution_vetoed_candidate(
                candidate,
                secondary_recommendations,
                watchlist,
                "post_rank_execution_veto",
                max_rel_spread,
            ):
                pass
            elif (
                index == 1
                and gap <= 0.05
                and not _is_poor_primary_candidate(candidate, max_rel_spread)
            ):
                secondary_recommendations.append(
                    _build_item(candidate, "secondary", "cross_underlying_close_second")
                )
            elif _is_reasonable_secondary(candidate, max_rel_spread):
                secondary_recommendations.append(
                    _build_item(candidate, "secondary", "other_underlying_reference")
                )
            elif _is_watchlist_candidate(candidate):
                watchlist.append(
                    _build_item(candidate, "watchlist", "other_underlying_watchlist")
                )

            for strategy in group[1:]:
                if _add_execution_vetoed_candidate(
                    strategy,
                    secondary_recommendations,
                    watchlist,
                    "post_rank_execution_veto",
                    max_rel_spread,
                ):
                    continue
                if _is_reasonable_secondary(strategy, max_rel_spread):
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
