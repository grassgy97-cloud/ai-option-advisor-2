from __future__ import annotations

import math
from typing import Any, Dict, List, Optional

from app.models.schemas import (
    FamilyCandidate,
    FamilyConstraintBundle,
    IntentSpec,
    OpportunityCandidate,
    OpportunityType,
    StrategyFamily,
    StrategySpec,
    StrategyLegSpec,
    StrategyConstraint,
    LegConstraint,
)


# ==============================
# 辅助：从price_levels提取strike_pct_target
# ==============================

def _get_support(intent: IntentSpec) -> Optional[float]:
    """下方支撑/保底位，负数百分比"""
    return intent.price_levels.get("support")

def _get_resistance(intent: IntentSpec) -> Optional[float]:
    """上方压力位，正数百分比"""
    return intent.price_levels.get("resistance")

def _get_target(intent: IntentSpec) -> Optional[float]:
    """目标价位，正负均可"""
    return intent.price_levels.get("target")


_STRATEGY_FAMILY_MAP: Dict[str, StrategyFamily] = {
    "bear_call_spread": "vertical",
    "bull_put_spread": "vertical",
    "bull_call_spread": "vertical",
    "bear_put_spread": "vertical",
    "call_calendar": "calendar",
    "put_calendar": "calendar",
    "diagonal_call": "diagonal",
    "diagonal_put": "diagonal",
    "iron_condor": "iron",
    "iron_fly": "iron",
    "long_call": "long_single",
    "long_put": "long_single",
    "naked_call": "naked_short",
    "naked_put": "naked_short",
    "covered_call": "covered_call",
}

_FAMILY_STRATEGY_TYPES: Dict[StrategyFamily, set[str]] = {
    "vertical": {"bear_call_spread", "bull_put_spread", "bull_call_spread", "bear_put_spread"},
    "calendar": {"call_calendar", "put_calendar"},
    "diagonal": {"diagonal_call", "diagonal_put"},
    "iron": {"iron_condor", "iron_fly"},
    "naked_short": {"naked_call", "naked_put"},
    "long_single": {"long_call", "long_put"},
    "covered_call": {"covered_call"},
}
_VERTICAL_STRATEGY_TYPES = (
    "bull_call_spread",
    "bear_call_spread",
    "bull_put_spread",
    "bear_put_spread",
)

_OPPORTUNITY_FAMILY_WEIGHTS: Dict[OpportunityType, Dict[StrategyFamily, float]] = {
    "directional_defined_risk": {"vertical": 1.0},
    "directional_convexity": {"long_single": 1.0, "diagonal": 0.75},
    "range_income": {"iron": 1.0, "naked_short": 0.8},
    "vol_rich_carry": {"iron": 0.85, "vertical": 0.7, "naked_short": 0.8},
    "term_structure_carry": {"calendar": 1.0, "diagonal": 0.9},
    "covered_income": {"covered_call": 1.0},
}

_CALENDAR_TERM_THRESHOLD = 0.45
_CALENDAR_SURFACE_THRESHOLD = 0.60
_DIAGONAL_DIRECTION_THRESHOLD = 0.40
_USER_SOFT_PRIORITY_WEIGHT = 1.00
_INFERRED_SOFT_PRIORITY_WEIGHT = 0.60
_MACHINE_SOFT_PRIORITY_WEIGHT = 0.35
_CALENDAR_DIAGONAL_GATE_PENALTY = 0.08
_GATE_FAILED_FAMILY_MIN_SCORE = 0.50


def _clip01(v: float) -> float:
    return max(0.0, min(1.0, v))


def _vertical_presence(keys: List[str] | set[str] | tuple[str, ...]) -> Dict[str, bool]:
    key_set = set(keys)
    return {
        strategy_type: strategy_type in key_set
        for strategy_type in _VERTICAL_STRATEGY_TYPES
    }


def _json_safe_value(value: Any) -> Any:
    if isinstance(value, float):
        if math.isnan(value) or math.isinf(value):
            return None
        return value
    if isinstance(value, dict):
        return {str(k): _json_safe_value(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_json_safe_value(v) for v in value]
    if isinstance(value, tuple):
        return [_json_safe_value(v) for v in value]
    return value


def _get_uid_ctx(intent: IntentSpec) -> Dict[str, Any]:
    ctx_data = getattr(intent, "market_context_data", {}) or {}
    return ctx_data.get(intent.underlying_id) or (next(iter(ctx_data.values())) if ctx_data else {})


def _strategy_family_for_type(strategy_type: str) -> StrategyFamily:
    return _STRATEGY_FAMILY_MAP.get(strategy_type, "vertical")


def _default_opportunity_for_family(family: StrategyFamily) -> OpportunityType:
    mapping: Dict[StrategyFamily, OpportunityType] = {
        "vertical": "directional_defined_risk",
        "calendar": "term_structure_carry",
        "diagonal": "term_structure_carry",
        "iron": "range_income",
        "naked_short": "vol_rich_carry",
        "long_single": "directional_convexity",
        "covered_call": "covered_income",
    }
    return mapping[family]


def _build_opportunity_evidence_summary(
    intent: IntentSpec,
    iv_pct: Optional[float] = None,
) -> Dict[str, Any]:
    uid_ctx = _get_uid_ctx(intent)
    term_slope_call = float(uid_ctx.get("term_slope_call") or 0.0)
    term_slope_put = float(uid_ctx.get("term_slope_put") or 0.0)
    skew = float(uid_ctx.get("put_call_skew") or 0.0)
    trend = uid_ctx.get("trend")

    direction_strength = 0.0
    if intent.market_view in ("bullish", "bearish"):
        direction_strength = max(direction_strength, 0.55)
    if intent.asymmetry in ("upside", "downside"):
        direction_strength = max(direction_strength, 0.65)
    delta_pref = intent.greeks_preference.get("delta", {})
    if isinstance(delta_pref, dict) and delta_pref.get("sign") in ("positive", "negative"):
        direction_strength = max(direction_strength, 0.25 + 0.5 * float(delta_pref.get("strength", 0.0) or 0.0))
    if trend in ("uptrend", "downtrend"):
        direction_strength = max(direction_strength, 0.45)

    term_carry_strength = max(
        _clip01(max(term_slope_call, 0.0) / 0.05),
        _clip01(max(term_slope_put, 0.0) / 0.05),
    )

    surface_rv_strength = _clip01(abs(skew) / 0.05)
    if intent.vol_view in ("call_iv_rich", "put_iv_rich"):
        surface_rv_strength = max(surface_rv_strength, 0.65)

    iv_rich_strength = 0.0
    if intent.vol_view == "iv_high":
        iv_rich_strength = max(iv_rich_strength, 0.70)
    if iv_pct is not None and iv_pct >= 0.70:
        iv_rich_strength = max(iv_rich_strength, _clip01((iv_pct - 0.55) / 0.30))

    low_iv_convexity_strength = 0.0
    if intent.vol_view == "iv_low":
        low_iv_convexity_strength = max(low_iv_convexity_strength, 0.70)
    if iv_pct is not None and iv_pct <= 0.30:
        low_iv_convexity_strength = max(low_iv_convexity_strength, _clip01((0.45 - iv_pct) / 0.30))

    covered_income_signal = 1.0 if intent.allowed_strategies == ["covered_call"] else 0.0

    return {
        "market_view": intent.market_view,
        "vol_view": intent.vol_view,
        "asymmetry": intent.asymmetry,
        "iv_percentile": iv_pct,
        "trend": trend,
        "term_slope_call": term_slope_call,
        "term_slope_put": term_slope_put,
        "put_call_skew": skew,
        "direction_strength": round(direction_strength, 4),
        "term_carry_strength": round(term_carry_strength, 4),
        "surface_rv_strength": round(surface_rv_strength, 4),
        "iv_rich_strength": round(iv_rich_strength, 4),
        "low_iv_convexity_strength": round(low_iv_convexity_strength, 4),
        "covered_income_signal": round(covered_income_signal, 4),
        "explicit_term_signal": intent.vol_view in ("term_front_high", "term_back_high"),
    }


def derive_opportunity_candidates(
    intent: IntentSpec,
    iv_pct: Optional[float] = None,
) -> List[OpportunityCandidate]:
    evidence = _build_opportunity_evidence_summary(intent, iv_pct=iv_pct)
    candidates: List[OpportunityCandidate] = []

    direction_strength = float(evidence["direction_strength"])
    term_carry_strength = float(evidence["term_carry_strength"])
    surface_rv_strength = float(evidence["surface_rv_strength"])
    iv_rich_strength = float(evidence["iv_rich_strength"])
    low_iv_convexity_strength = float(evidence["low_iv_convexity_strength"])
    covered_income_signal = float(evidence["covered_income_signal"])

    if intent.market_view in ("bullish", "bearish") or intent.asymmetry in ("upside", "downside"):
        score = max(0.55, 0.55 + 0.25 * direction_strength)
        candidates.append(OpportunityCandidate(
            opportunity_type="directional_defined_risk",
            underlying_id=intent.underlying_id,
            score=round(score, 4),
            confidence=round(direction_strength, 4),
            evidence=evidence,
            rationale="directional view with controlled-risk structure preference",
            source_flags={
                "explicit_user_signal": intent.market_view in ("bullish", "bearish"),
                "inferred_signal": intent.asymmetry in ("upside", "downside"),
                "machine_signal": bool(evidence.get("trend")),
            },
        ))

    if direction_strength > 0 or low_iv_convexity_strength > 0:
        score = max(direction_strength, low_iv_convexity_strength, 0.45)
        candidates.append(OpportunityCandidate(
            opportunity_type="directional_convexity",
            underlying_id=intent.underlying_id,
            score=round(score, 4),
            confidence=round(max(direction_strength, low_iv_convexity_strength), 4),
            evidence=evidence,
            rationale="directional or asymmetric move with convexity demand",
            source_flags={
                "explicit_user_signal": intent.asymmetry in ("upside", "downside"),
                "inferred_signal": intent.market_view in ("bullish", "bearish"),
                "machine_signal": low_iv_convexity_strength > 0,
            },
        ))

    if intent.market_view == "neutral" or iv_rich_strength > 0.50:
        base = 0.50 if intent.market_view == "neutral" else 0.35
        score = max(base, 0.45 + 0.25 * iv_rich_strength)
        candidates.append(OpportunityCandidate(
            opportunity_type="range_income",
            underlying_id=intent.underlying_id,
            score=round(score, 4),
            confidence=round(max(iv_rich_strength, 0.5 if intent.market_view == "neutral" else 0.0), 4),
            evidence=evidence,
            rationale="range-bound or income-oriented setup",
            source_flags={
                "explicit_user_signal": False,
                "inferred_signal": intent.market_view == "neutral",
                "machine_signal": iv_rich_strength > 0.50,
            },
        ))

    if intent.vol_view in ("iv_high", "call_iv_rich", "put_iv_rich") or iv_rich_strength > 0:
        score = max(iv_rich_strength, surface_rv_strength, 0.50)
        candidates.append(OpportunityCandidate(
            opportunity_type="vol_rich_carry",
            underlying_id=intent.underlying_id,
            score=round(score, 4),
            confidence=round(max(iv_rich_strength, surface_rv_strength), 4),
            evidence=evidence,
            rationale="sell-vol or carry opportunity from rich surface conditions",
            source_flags={
                "explicit_user_signal": intent.vol_view in ("iv_high", "call_iv_rich", "put_iv_rich"),
                "inferred_signal": False,
                "machine_signal": iv_rich_strength > 0 or surface_rv_strength > 0,
            },
        ))

    if term_carry_strength > 0 or surface_rv_strength > 0 or evidence["explicit_term_signal"]:
        score = max(term_carry_strength, min(0.85, surface_rv_strength * 0.9), 0.45 if evidence["explicit_term_signal"] else 0.0)
        candidates.append(OpportunityCandidate(
            opportunity_type="term_structure_carry",
            underlying_id=intent.underlying_id,
            score=round(score, 4),
            confidence=round(max(term_carry_strength, surface_rv_strength), 4),
            evidence=evidence,
            rationale="surface carry or relative-value signal between maturities",
            source_flags={
                "explicit_user_signal": bool(evidence["explicit_term_signal"]),
                "inferred_signal": intent.vol_view in ("call_iv_rich", "put_iv_rich"),
                "machine_signal": term_carry_strength > 0 or surface_rv_strength > 0,
            },
        ))

    if covered_income_signal > 0:
        candidates.append(OpportunityCandidate(
            opportunity_type="covered_income",
            underlying_id=intent.underlying_id,
            score=1.0,
            confidence=1.0,
            evidence=evidence,
            rationale="explicit covered-call income use case",
            source_flags={
                "explicit_user_signal": True,
                "inferred_signal": False,
                "machine_signal": False,
            },
        ))

    return candidates


def build_family_constraints(
    intent: IntentSpec,
    iv_pct: Optional[float] = None,
) -> FamilyConstraintBundle:
    uid_ctx = _get_uid_ctx(intent)
    bundle = FamilyConstraintBundle()

    if intent.allowed_strategies:
        bundle.user_hard.allowed_families = sorted({
            _strategy_family_for_type(s) for s in intent.allowed_strategies
        })
        bundle.user_hard.notes.append("derived from allowed_strategies")

    banned_types = set(intent.banned_strategies or [])
    for family, members in _FAMILY_STRATEGY_TYPES.items():
        if members and members.issubset(banned_types):
            bundle.user_hard.banned_families.append(family)
    if bundle.user_hard.banned_families:
        bundle.user_hard.notes.append("derived from full-family banned_strategies")

    if intent.defined_risk_only:
        bundle.user_hard.require_defined_risk = True
        if "naked_short" not in bundle.user_hard.banned_families:
            bundle.user_hard.banned_families.append("naked_short")
        bundle.user_hard.notes.append("defined_risk_only")

    if intent.prefer_multi_leg:
        bundle.user_soft.prefer_multi_leg = True
        bundle.user_soft.weights.update({
            "vertical": 0.12,
            "calendar": 0.08,
            "diagonal": 0.08,
            "iron": 0.10,
        })
        bundle.user_soft.notes.append("prefer_multi_leg")

    if intent.market_view in ("bullish", "bearish"):
        bundle.inferred_soft.weights["vertical"] = 0.10
        bundle.inferred_soft.weights["long_single"] = 0.08
        bundle.inferred_soft.notes.append("directional market_view")
    elif intent.market_view == "neutral":
        bundle.inferred_soft.weights["iron"] = 0.10
        bundle.inferred_soft.weights["naked_short"] = 0.05
        bundle.inferred_soft.notes.append("neutral market_view")

    if intent.asymmetry in ("upside", "downside"):
        bundle.inferred_soft.weights["long_single"] = bundle.inferred_soft.weights.get("long_single", 0.0) + 0.06
        bundle.inferred_soft.weights["diagonal"] = bundle.inferred_soft.weights.get("diagonal", 0.0) + 0.04
        bundle.inferred_soft.notes.append("asymmetry preference")

    if intent.risk_preference == "low":
        bundle.inferred_soft.weights["vertical"] = bundle.inferred_soft.weights.get("vertical", 0.0) + 0.06
        bundle.inferred_soft.weights["iron"] = bundle.inferred_soft.weights.get("iron", 0.0) + 0.04
        bundle.inferred_soft.notes.append("low risk preference")
    elif intent.risk_preference == "high":
        bundle.inferred_soft.weights["long_single"] = bundle.inferred_soft.weights.get("long_single", 0.0) + 0.05
        bundle.inferred_soft.weights["naked_short"] = bundle.inferred_soft.weights.get("naked_short", 0.0) + 0.05
        bundle.inferred_soft.notes.append("high risk preference")

    term_strength = max(
        _clip01(max(float(uid_ctx.get("term_slope_call") or 0.0), 0.0) / 0.05),
        _clip01(max(float(uid_ctx.get("term_slope_put") or 0.0), 0.0) / 0.05),
    )
    if term_strength > 0:
        bundle.machine_soft.weights["calendar"] = max(bundle.machine_soft.weights.get("calendar", 0.0), round(0.15 * term_strength, 4))
        bundle.machine_soft.weights["diagonal"] = max(bundle.machine_soft.weights.get("diagonal", 0.0), round(0.10 * term_strength, 4))
        bundle.machine_soft.notes.append("term structure support")

    skew = abs(float(uid_ctx.get("put_call_skew") or 0.0))
    if skew > 0:
        bundle.machine_soft.weights["calendar"] = max(bundle.machine_soft.weights.get("calendar", 0.0), round(0.08 * _clip01(skew / 0.05), 4))
        bundle.machine_soft.weights["diagonal"] = max(bundle.machine_soft.weights.get("diagonal", 0.0), round(0.06 * _clip01(skew / 0.05), 4))
        bundle.machine_soft.notes.append("surface relative-value support")

    if iv_pct is not None and iv_pct >= 0.70:
        bundle.machine_soft.weights["iron"] = max(bundle.machine_soft.weights.get("iron", 0.0), round(0.12 * _clip01((iv_pct - 0.55) / 0.30), 4))
        bundle.machine_soft.weights["naked_short"] = max(bundle.machine_soft.weights.get("naked_short", 0.0), round(0.10 * _clip01((iv_pct - 0.55) / 0.30), 4))
        bundle.machine_soft.weights["vertical"] = max(bundle.machine_soft.weights.get("vertical", 0.0), 0.04)
        bundle.machine_soft.notes.append("high IV percentile")
    elif iv_pct is not None and iv_pct <= 0.30:
        bundle.machine_soft.weights["long_single"] = max(bundle.machine_soft.weights.get("long_single", 0.0), round(0.12 * _clip01((0.45 - iv_pct) / 0.30), 4))
        bundle.machine_soft.notes.append("low IV percentile")

    return bundle


def _build_calendar_diagonal_gate_results(
    intent: IntentSpec,
    iv_pct: Optional[float] = None,
) -> Dict[str, Dict[str, Any]]:
    evidence = _build_opportunity_evidence_summary(intent, iv_pct=iv_pct)
    term_strength = float(evidence["term_carry_strength"])
    surface_strength = float(evidence["surface_rv_strength"])
    direction_strength = float(evidence["direction_strength"])
    explicit_term_signal = bool(evidence["explicit_term_signal"])

    calendar_reasons: List[str] = []
    if term_strength >= _CALENDAR_TERM_THRESHOLD:
        calendar_reasons.append("term_carry_strength above threshold")
    if surface_strength >= _CALENDAR_SURFACE_THRESHOLD:
        calendar_reasons.append("surface_rv_strength above threshold")
    if explicit_term_signal:
        calendar_reasons.append("explicit term-structure signal")

    calendar_passed = bool(calendar_reasons)
    if not calendar_reasons:
        calendar_reasons.append("no strong term/carry evidence yet")

    diagonal_reasons = [r for r in calendar_reasons if r != "no strong term/carry evidence yet"]
    if direction_strength >= _DIAGONAL_DIRECTION_THRESHOLD:
        diagonal_reasons.append("direction_strength above threshold")

    diagonal_passed = calendar_passed and direction_strength >= _DIAGONAL_DIRECTION_THRESHOLD
    if not diagonal_reasons:
        diagonal_reasons.append("no combined carry plus directional evidence yet")

    gate_inputs = {
        "term_carry_strength": round(term_strength, 4),
        "surface_rv_strength": round(surface_strength, 4),
        "direction_strength": round(direction_strength, 4),
        "explicit_term_signal": explicit_term_signal,
        "calendar_term_threshold": _CALENDAR_TERM_THRESHOLD,
        "calendar_surface_threshold": _CALENDAR_SURFACE_THRESHOLD,
        "diagonal_direction_threshold": _DIAGONAL_DIRECTION_THRESHOLD,
    }

    return {
        "calendar": {
            "family": "calendar",
            "passed": calendar_passed,
            "reasons": calendar_reasons,
            "inputs": gate_inputs,
            "mode": "metadata_only",
        },
        "diagonal": {
            "family": "diagonal",
            "passed": diagonal_passed,
            "reasons": diagonal_reasons,
            "inputs": gate_inputs,
            "mode": "metadata_only",
        },
    }


def _family_soft_signal_summary(
    bundle: FamilyConstraintBundle,
    family: StrategyFamily,
) -> Dict[str, float]:
    return {
        "user_soft": round(float(bundle.user_soft.weights.get(family, 0.0)), 4),
        "inferred_soft": round(float(bundle.inferred_soft.weights.get(family, 0.0)), 4),
        "machine_soft": round(float(bundle.machine_soft.weights.get(family, 0.0)), 4),
    }


def _family_soft_priority_adjustment(
    bundle: FamilyConstraintBundle,
    family: StrategyFamily,
) -> tuple[Dict[str, float], float]:
    soft_signals = _family_soft_signal_summary(bundle, family)
    adjustment = (
        soft_signals["user_soft"] * _USER_SOFT_PRIORITY_WEIGHT
        + soft_signals["inferred_soft"] * _INFERRED_SOFT_PRIORITY_WEIGHT
        + soft_signals["machine_soft"] * _MACHINE_SOFT_PRIORITY_WEIGHT
    )
    return soft_signals, round(adjustment, 4)


def _family_best_strategy_weight(
    best_map: Dict[str, float],
    family: StrategyFamily,
) -> float:
    weights = [
        float(weight)
        for strategy_type, weight in best_map.items()
        if _strategy_family_for_type(strategy_type) == family
    ]
    return max(weights) if weights else 0.0


def _has_explicit_hard_family_constraints(bundle: FamilyConstraintBundle) -> bool:
    return bool(bundle.user_hard.allowed_families or bundle.user_hard.banned_families)


def _hard_family_exclusion_reason(
    family: StrategyFamily,
    constraints: FamilyConstraintBundle,
) -> Optional[str]:
    if constraints.user_hard.allowed_families and family not in constraints.user_hard.allowed_families:
        return "outside explicit allowed_families"
    if family in constraints.user_hard.banned_families:
        return "explicitly banned family"
    if constraints.user_hard.require_defined_risk and family == "naked_short":
        return "require_defined_risk removes naked_short"
    return None


def _directional_family_balance_adjustment(
    intent: IntentSpec,
    family: StrategyFamily,
    iv_pct: Optional[float] = None,
) -> tuple[float, Optional[str]]:
    evidence = _build_opportunity_evidence_summary(intent, iv_pct=iv_pct)
    directional = (
        intent.market_view in ("bullish", "bearish")
        or intent.asymmetry in ("upside", "downside")
    )
    if not directional:
        return 0.0, None

    gamma_pref = intent.greeks_preference.get("gamma", {})
    gamma_positive_strength = 0.0
    if isinstance(gamma_pref, dict) and gamma_pref.get("sign") == "positive":
        gamma_positive_strength = float(gamma_pref.get("strength", 0.0) or 0.0)

    convexity_evidence = max(
        float(evidence.get("low_iv_convexity_strength") or 0.0),
        gamma_positive_strength,
    )
    conservative_directional = (
        intent.defined_risk_only
        or intent.risk_preference == "low"
        or intent.allowed_strategies is None
    )

    if family == "vertical" and conservative_directional and convexity_evidence < 0.45:
        return 0.08, "directional controlled-risk preservation"

    if family == "long_single":
        if convexity_evidence >= 0.55:
            return 0.05, "real convexity evidence"
        if convexity_evidence < 0.35 and conservative_directional:
            return -0.08, "ordinary directional case without convexity evidence"

    return 0.0, None


def _has_real_convexity_evidence(
    intent: IntentSpec,
    iv_pct: Optional[float] = None,
) -> bool:
    evidence = _build_opportunity_evidence_summary(intent, iv_pct=iv_pct)
    gamma_pref = intent.greeks_preference.get("gamma", {})
    gamma_positive_strength = 0.0
    if isinstance(gamma_pref, dict) and gamma_pref.get("sign") == "positive":
        gamma_positive_strength = float(gamma_pref.get("strength", 0.0) or 0.0)

    return (
        float(evidence.get("low_iv_convexity_strength") or 0.0) >= 0.45
        or gamma_positive_strength >= 0.55
    )


def _ensure_family_candidates_from_best_map(
    family_candidates: List[FamilyCandidate],
    best_map: Dict[str, float],
) -> Dict[StrategyFamily, FamilyCandidate]:
    candidate_map: Dict[StrategyFamily, FamilyCandidate] = {c.family: c for c in family_candidates}

    for family in {_strategy_family_for_type(strategy_type) for strategy_type in best_map}:
        family_weight = _family_best_strategy_weight(best_map, family)
        existing = candidate_map.get(family)
        if existing is None:
            candidate_map[family] = FamilyCandidate(
                family=family,
                underlying_id="",
                opportunity_type=_default_opportunity_for_family(family),
                score=round(family_weight, 4),
                confidence=round(min(1.0, family_weight), 4),
                metadata={"metadata_mode": "best_map_fallback"},
            )
        elif family_weight > existing.score:
            existing.score = round(family_weight, 4)
            existing.confidence = round(max(existing.confidence, min(1.0, family_weight)), 4)
            existing.metadata["best_map_weight"] = round(family_weight, 4)

    return candidate_map


def _backfill_vertical_strategy_types(
    best_map: Dict[str, float],
    family_candidate_map: Dict[StrategyFamily, FamilyCandidate],
    intent: IntentSpec,
) -> Dict[str, float]:
    vertical_candidate = family_candidate_map.get("vertical")
    if vertical_candidate is None or not vertical_candidate.shortlisted:
        print(
            "[vertical_trace] "
            f"uid={intent.underlying_id} "
            "backfill_triggered=False "
            "reason=vertical_family_not_shortlisted"
        )
        return best_map

    existing_verticals = [
        strategy_type
        for strategy_type in best_map
        if _strategy_family_for_type(strategy_type) == "vertical"
    ]
    if existing_verticals:
        print(
            "[vertical_trace] "
            f"uid={intent.underlying_id} "
            "backfill_triggered=False "
            f"reason=vertical_already_present existing={existing_verticals}"
        )
        return best_map

    family_score = float(vertical_candidate.score or 0.0)
    base = min(0.85, max(0.68, round(family_score, 3)))
    inserted: List[str] = []

    if intent.market_view == "bullish" or intent.asymmetry == "upside":
        best_map["bull_call_spread"] = max(best_map.get("bull_call_spread", 0.0), base)
        best_map["bull_put_spread"] = max(best_map.get("bull_put_spread", 0.0), round(max(0.65, base - 0.04), 3))
        inserted.extend(["bull_call_spread", "bull_put_spread"])
    elif intent.market_view == "bearish" or intent.asymmetry == "downside":
        best_map["bear_call_spread"] = max(best_map.get("bear_call_spread", 0.0), base)
        best_map["bear_put_spread"] = max(best_map.get("bear_put_spread", 0.0), round(max(0.65, base - 0.04), 3))
        inserted.extend(["bear_call_spread", "bear_put_spread"])
    else:
        best_map["bear_call_spread"] = max(best_map.get("bear_call_spread", 0.0), round(max(0.65, base - 0.02), 3))
        best_map["bull_put_spread"] = max(best_map.get("bull_put_spread", 0.0), round(max(0.65, base - 0.02), 3))
        inserted.extend(["bear_call_spread", "bull_put_spread"])

    print(
        "[vertical_trace] "
        f"uid={intent.underlying_id} "
        "backfill_triggered=True "
        f"inserted={inserted}"
    )

    return best_map


def _select_family_shortlist(
    best_map: Dict[str, float],
    family_candidates: List[FamilyCandidate],
    constraints: FamilyConstraintBundle,
    intent: IntentSpec,
    iv_pct: Optional[float] = None,
) -> Dict[StrategyFamily, FamilyCandidate]:
    candidate_map = _ensure_family_candidates_from_best_map(family_candidates, best_map)
    active_families = {
        _strategy_family_for_type(strategy_type)
        for strategy_type in best_map
    }
    explicit_hard = _has_explicit_hard_family_constraints(constraints)

    eligible: List[FamilyCandidate] = []
    for family in active_families:
        candidate = candidate_map[family]
        shortlist_reasons: List[str] = []
        hard_reason = _hard_family_exclusion_reason(family, constraints)
        if hard_reason is not None:
            candidate.shortlisted = False
            candidate.shortlist_reasons = [hard_reason]
            candidate.hard_constraints_applied = list({
                *candidate.hard_constraints_applied,
                hard_reason,
            })
            continue

        candidate.shortlisted = True
        soft_signals, weighted_adjustment = _family_soft_priority_adjustment(constraints, family)
        candidate.soft_signals = soft_signals
        candidate.score = round(max(candidate.score, _family_best_strategy_weight(best_map, family)) + weighted_adjustment, 4)
        candidate.confidence = round(max(candidate.confidence, min(1.0, candidate.score)), 4)

        directional_balance_adjustment, directional_balance_reason = _directional_family_balance_adjustment(
            intent,
            family,
            iv_pct=iv_pct,
        )
        if directional_balance_adjustment != 0.0:
            candidate.score = round(max(0.0, candidate.score + directional_balance_adjustment), 4)
            shortlist_reasons.append(
                f"{directional_balance_reason} ({directional_balance_adjustment:+.2f})"
            )

        if family in ("calendar", "diagonal") and not candidate.gating_passed:
            candidate.score = round(max(0.0, candidate.score - _CALENDAR_DIAGONAL_GATE_PENALTY), 4)
            shortlist_reasons.append("calendar/diagonal gate soft penalty")

        if weighted_adjustment > 0:
            shortlist_reasons.append(f"soft priority +{weighted_adjustment:.3f}")
        candidate.shortlist_reasons = shortlist_reasons
        eligible.append(candidate)

    if not eligible:
        if explicit_hard:
            return candidate_map

        fallback_order: List[StrategyFamily] = [
            "vertical",
            "iron",
            "long_single",
            "covered_call",
            "calendar",
            "diagonal",
            "naked_short",
        ]
        for family in fallback_order:
            if family in active_families and _hard_family_exclusion_reason(family, constraints) is None:
                candidate_map[family].shortlisted = True
                candidate_map[family].shortlist_reasons = ["conservative fallback family"]
                return candidate_map
        return candidate_map

    if explicit_hard:
        return candidate_map

    directional_case = (
        intent.market_view in ("bullish", "bearish")
        or intent.asymmetry in ("upside", "downside")
    )
    if directional_case and "vertical" in active_families and "long_single" in active_families:
        vertical_candidate = candidate_map.get("vertical")
        long_single_candidate = candidate_map.get("long_single")
        if (
            vertical_candidate is not None
            and long_single_candidate is not None
            and vertical_candidate.shortlisted
            and long_single_candidate.shortlisted
            and not _has_real_convexity_evidence(intent, iv_pct=iv_pct)
        ):
            long_single_candidate.shortlisted = False
            long_single_candidate.shortlist_reasons = [
                "ordinary directional case without real convexity evidence"
            ]
            eligible = [c for c in eligible if c.family != "long_single"]

    for candidate in eligible:
        if not candidate.gating_passed and candidate.score < _GATE_FAILED_FAMILY_MIN_SCORE:
            candidate.shortlisted = False
            candidate.shortlist_reasons = [
                f"gate failed and family score below {_GATE_FAILED_FAMILY_MIN_SCORE:.2f}"
            ]
            continue
        if not candidate.shortlist_reasons:
            candidate.shortlist_reasons = ["compatible family retained"]

    return candidate_map


def derive_family_candidates(
    opportunities: List[OpportunityCandidate],
    constraints: FamilyConstraintBundle,
    intent: IntentSpec,
    iv_pct: Optional[float] = None,
) -> List[FamilyCandidate]:
    gate_results = _build_calendar_diagonal_gate_results(intent, iv_pct=iv_pct)
    candidates_by_family: Dict[StrategyFamily, FamilyCandidate] = {}

    for opp in opportunities:
        family_weights = _OPPORTUNITY_FAMILY_WEIGHTS.get(opp.opportunity_type, {})
        for family, multiplier in family_weights.items():
            soft_signals, soft_total = _family_soft_priority_adjustment(constraints, family)
            base_score = opp.score * multiplier
            score = round(base_score + soft_total, 4)

            gating_passed = True
            gating_reasons: List[str] = []
            gate_metadata: Dict[str, Any] = {}
            if family in ("calendar", "diagonal"):
                gate = gate_results[family]
                gating_passed = bool(gate["passed"])
                gating_reasons = list(gate["reasons"])
                gate_metadata = dict(gate)

            hard_constraints_applied: List[str] = []
            if constraints.user_hard.allowed_families and family not in constraints.user_hard.allowed_families:
                hard_constraints_applied.append("outside explicit allowed_families")
            if family in constraints.user_hard.banned_families:
                hard_constraints_applied.append("explicitly banned family")
            if constraints.user_hard.require_defined_risk and family == "naked_short":
                hard_constraints_applied.append("require_defined_risk removes naked_short")

            candidate = FamilyCandidate(
                family=family,
                underlying_id=opp.underlying_id,
                opportunity_type=opp.opportunity_type,
                score=score,
                confidence=round(max(opp.confidence, min(1.0, base_score)), 4),
                shortlisted=True,
                gating_passed=gating_passed,
                gating_reasons=gating_reasons,
                shortlist_reasons=[],
                hard_constraints_applied=hard_constraints_applied,
                soft_signals=soft_signals,
                rationale=opp.rationale,
                metadata={
                    "gate": gate_metadata,
                    "opportunity_evidence": opp.evidence,
                    "source_flags": opp.source_flags,
                    "metadata_mode": "planning_only",
                },
            )

            existing = candidates_by_family.get(family)
            if existing is None or candidate.score > existing.score:
                candidates_by_family[family] = candidate

    return list(candidates_by_family.values())


def _select_opportunity_for_family(
    opportunities: List[OpportunityCandidate],
    family: StrategyFamily,
) -> Optional[OpportunityCandidate]:
    mapped = [
        opp for opp in opportunities
        if family in _OPPORTUNITY_FAMILY_WEIGHTS.get(opp.opportunity_type, {})
    ]
    if not mapped:
        return None
    return max(mapped, key=lambda x: x.score)


def _serialize_family_gate_results(family_candidates: List[FamilyCandidate]) -> Dict[str, Dict[str, Any]]:
    out: Dict[str, Dict[str, Any]] = {}
    for candidate in family_candidates:
        if candidate.family not in ("calendar", "diagonal"):
            continue
        gate = candidate.metadata.get("gate") or {}
        out[candidate.family] = {
            "passed": candidate.gating_passed,
            "reasons": candidate.gating_reasons,
            "inputs": gate.get("inputs", {}),
            "mode": gate.get("mode", "metadata_only"),
        }
    return out


# ==============================
# strategy spec factory
# ==============================

def build_strategy_spec(strategy_type: str, intent: IntentSpec) -> StrategySpec | None:
    underlying_id = intent.underlying_id

    common_constraints = StrategyConstraint(
        defined_risk_only=intent.defined_risk_only,
        dte_min=intent.dte_min,
        dte_max=intent.dte_max,
        max_rel_spread=intent.max_rel_spread,
        min_quote_size=intent.min_quote_size,
    )

    support = _get_support(intent)
    resistance = _get_resistance(intent)
    target = _get_target(intent)

    # ===== calendar =====
    if strategy_type == "call_calendar":
        near_dte_min = max(10, min(intent.dte_min, 35))
        near_dte_max = max(35, intent.dte_max)
        return StrategySpec(
            strategy_type="call_calendar",
            underlying_id=underlying_id,
            legs=[
                StrategyLegSpec(
                    action="SELL", option_type="CALL", expiry_rule="nearest",
                    strike=None, delta_target=None, quantity=1,
                    leg_constraints=LegConstraint(
                        dte_min=near_dte_min, dte_max=near_dte_max,
                        max_rel_spread=intent.max_rel_spread,
                        min_quote_size=intent.min_quote_size,
                    ),
                ),
                StrategyLegSpec(
                    action="BUY", option_type="CALL", expiry_rule="next_expiry",
                    strike=None, delta_target=None, quantity=1,
                    leg_constraints=LegConstraint(
                        dte_min=36, dte_max=120,
                        max_rel_spread=intent.max_rel_spread,
                        min_quote_size=intent.min_quote_size,
                    ),
                ),
            ],
            constraints=common_constraints,
            rationale="卖近买远 call calendar（sell near / buy far，同strike ATM）",
            metadata={
                "selection_mode": "atm_like_same_strike_calendar",
                "near_dte_min": near_dte_min, "near_dte_max": near_dte_max,
                "far_dte_min": 36, "far_dte_max": 120,
                "atm_moneyness_low": 0.9, "atm_moneyness_high": 1.1,
            },
        )

    if strategy_type == "put_calendar":
        near_dte_min = max(10, min(intent.dte_min, 35))
        near_dte_max = max(35, intent.dte_max)
        return StrategySpec(
            strategy_type="put_calendar",
            underlying_id=underlying_id,
            legs=[
                StrategyLegSpec(
                    action="SELL", option_type="PUT", expiry_rule="nearest",
                    strike=None, delta_target=None, quantity=1,
                    leg_constraints=LegConstraint(
                        dte_min=near_dte_min, dte_max=near_dte_max,
                        max_rel_spread=intent.max_rel_spread,
                        min_quote_size=intent.min_quote_size,
                    ),
                ),
                StrategyLegSpec(
                    action="BUY", option_type="PUT", expiry_rule="next_expiry",
                    strike=None, delta_target=None, quantity=1,
                    leg_constraints=LegConstraint(
                        dte_min=36, dte_max=120,
                        max_rel_spread=intent.max_rel_spread,
                        min_quote_size=intent.min_quote_size,
                    ),
                ),
            ],
            constraints=common_constraints,
            rationale="卖近买远 put calendar（sell near / buy far，同strike ATM）",
            metadata={
                "selection_mode": "atm_like_same_strike_calendar",
                "near_dte_min": near_dte_min, "near_dte_max": near_dte_max,
                "far_dte_min": 36, "far_dte_max": 120,
                "atm_moneyness_low": 0.9, "atm_moneyness_high": 1.1,
            },
        )

    # ===== diagonal =====
    if strategy_type == "diagonal_call":
        short_pct = resistance
        return StrategySpec(
            strategy_type="diagonal_call",
            underlying_id=underlying_id,
            legs=[
                StrategyLegSpec(
                    action="SELL", option_type="CALL", expiry_rule="nearest",
                    strike=None, delta_target=0.3, quantity=1,
                    strike_pct_target=short_pct,
                    strike_forced=(short_pct is not None),
                    leg_constraints=LegConstraint(
                        dte_min=10, dte_max=35,
                        max_rel_spread=intent.max_rel_spread,
                        min_quote_size=intent.min_quote_size,
                    ),
                ),
                StrategyLegSpec(
                    action="BUY", option_type="CALL", expiry_rule="next_expiry",
                    strike=None, delta_target=0.5, quantity=1,
                    leg_constraints=LegConstraint(
                        dte_min=36, dte_max=120,
                        max_rel_spread=intent.max_rel_spread,
                        min_quote_size=intent.min_quote_size,
                    ),
                ),
            ],
            constraints=common_constraints,
            rationale="call diagonal：卖近月虚值call（delta~0.3），买远月ATM call（delta~0.5），轻度看涨+收theta",
            metadata={
                "selection_mode": "diagonal",
                "near_delta_target": 0.3, "far_delta_target": 0.5,
                "near_dte_min": 10, "near_dte_max": 35,
                "far_dte_min": 36, "far_dte_max": 120,
            },
        )

    if strategy_type == "diagonal_put":
        short_pct = support
        return StrategySpec(
            strategy_type="diagonal_put",
            underlying_id=underlying_id,
            legs=[
                StrategyLegSpec(
                    action="SELL", option_type="PUT", expiry_rule="nearest",
                    strike=None, delta_target=0.3, quantity=1,
                    strike_pct_target=short_pct,
                    strike_forced=(short_pct is not None),
                    leg_constraints=LegConstraint(
                        dte_min=10, dte_max=35,
                        max_rel_spread=intent.max_rel_spread,
                        min_quote_size=intent.min_quote_size,
                    ),
                ),
                StrategyLegSpec(
                    action="BUY", option_type="PUT", expiry_rule="next_expiry",
                    strike=None, delta_target=0.5, quantity=1,
                    leg_constraints=LegConstraint(
                        dte_min=36, dte_max=120,
                        max_rel_spread=intent.max_rel_spread,
                        min_quote_size=intent.min_quote_size,
                    ),
                ),
            ],
            constraints=common_constraints,
            rationale="put diagonal：卖近月虚值put（delta~0.3），买远月ATM put（delta~0.5），轻度看跌+收theta",
            metadata={
                "selection_mode": "diagonal",
                "near_delta_target": 0.3, "far_delta_target": 0.5,
                "near_dte_min": 10, "near_dte_max": 35,
                "far_dte_min": 36, "far_dte_max": 120,
            },
        )

    # ===== vertical =====
    if strategy_type == "bull_call_spread":
        buy_pct = target
        return StrategySpec(
            strategy_type="bull_call_spread", underlying_id=underlying_id,
            legs=[
                StrategyLegSpec(
                    action="BUY", option_type="CALL", expiry_rule="nearest",
                    delta_target=0.5,
                    strike_pct_target=buy_pct,
                    strike_forced=(buy_pct is not None),
                ),
                StrategyLegSpec(
                    action="SELL", option_type="CALL", expiry_rule="same_expiry",
                    delta_target=0.3,
                    strike_pct_target=resistance,
                    strike_forced=(resistance is not None),
                ),
            ],
            constraints=common_constraints,
            rationale="bull call spread（debit，买平值卖虚值）", metadata={},
        )

    if strategy_type == "bear_call_spread":
        short_pct = resistance
        return StrategySpec(
            strategy_type="bear_call_spread", underlying_id=underlying_id,
            legs=[
                StrategyLegSpec(
                    action="SELL", option_type="CALL", expiry_rule="nearest",
                    delta_target=0.3,
                    strike_pct_target=short_pct,
                    strike_forced=(short_pct is not None),
                ),
                StrategyLegSpec(
                    action="BUY", option_type="CALL", expiry_rule="same_expiry",
                    delta_target=0.15,
                ),
            ],
            constraints=common_constraints,
            rationale="bear call spread（credit，卖虚值买更虚值）", metadata={},
        )

    if strategy_type == "bull_put_spread":
        buy_pct = support
        return StrategySpec(
            strategy_type="bull_put_spread", underlying_id=underlying_id,
            legs=[
                StrategyLegSpec(
                    action="SELL", option_type="PUT", expiry_rule="nearest",
                    delta_target=0.3,
                ),
                StrategyLegSpec(
                    action="BUY", option_type="PUT", expiry_rule="same_expiry",
                    delta_target=0.15,
                    strike_pct_target=buy_pct,
                    strike_forced=(buy_pct is not None),
                ),
            ],
            constraints=common_constraints,
            rationale="bull put spread（credit，卖虚值买更虚值）", metadata={},
        )

    if strategy_type == "bear_put_spread":
        sell_pct = support
        buy_pct = target
        return StrategySpec(
            strategy_type="bear_put_spread", underlying_id=underlying_id,
            legs=[
                StrategyLegSpec(
                    action="BUY", option_type="PUT", expiry_rule="nearest",
                    delta_target=0.5,
                    strike_pct_target=buy_pct,
                    strike_forced=(buy_pct is not None),
                ),
                StrategyLegSpec(
                    action="SELL", option_type="PUT", expiry_rule="same_expiry",
                    delta_target=0.3,
                    strike_pct_target=sell_pct,
                    strike_forced=(sell_pct is not None),
                ),
            ],
            constraints=common_constraints,
            rationale="bear put spread（debit，买平值卖虚值）", metadata={},
        )

    # ===== condor =====
    if strategy_type == "iron_condor":
        return StrategySpec(
            strategy_type="iron_condor", underlying_id=underlying_id,
            legs=[
                StrategyLegSpec(
                    action="SELL", option_type="CALL", expiry_rule="nearest",
                    delta_target=0.3,
                    strike_pct_target=resistance,
                    strike_forced=(resistance is not None),
                ),
                StrategyLegSpec(
                    action="BUY", option_type="CALL", expiry_rule="same_expiry",
                    delta_target=0.15,
                ),
                StrategyLegSpec(
                    action="SELL", option_type="PUT", expiry_rule="nearest",
                    delta_target=0.3,
                    strike_pct_target=support,
                    strike_forced=(support is not None),
                ),
                StrategyLegSpec(
                    action="BUY", option_type="PUT", expiry_rule="same_expiry",
                    delta_target=0.15,
                ),
            ],
            constraints=common_constraints,
            rationale="iron condor（IV高+中性，卖双侧虚值）", metadata={},
        )

    if strategy_type == "iron_fly":
        return StrategySpec(
            strategy_type="iron_fly", underlying_id=underlying_id,
            legs=[
                StrategyLegSpec(action="SELL", option_type="CALL", expiry_rule="nearest",    delta_target=0.5),
                StrategyLegSpec(action="SELL", option_type="PUT",  expiry_rule="nearest",    delta_target=0.5),
                StrategyLegSpec(action="BUY",  option_type="CALL", expiry_rule="same_expiry", delta_target=0.2),
                StrategyLegSpec(action="BUY",  option_type="PUT",  expiry_rule="same_expiry", delta_target=0.2),
            ],
            constraints=common_constraints,
            rationale="iron fly（IV高+极度中性，卖平值双侧）", metadata={},
        )

    # ===== 单腿买方 =====
    if strategy_type == "long_call":
        buy_pct = target if target and target > 0 else None
        return StrategySpec(
            strategy_type="long_call", underlying_id=underlying_id,
            legs=[
                StrategyLegSpec(
                    action="BUY", option_type="CALL", expiry_rule="nearest",
                    strike=None, delta_target=0.50, quantity=1,
                    strike_pct_target=buy_pct,
                    strike_forced=(buy_pct is not None),
                    leg_constraints=LegConstraint(
                        dte_min=45, dte_max=90,
                        max_rel_spread=0.04, min_quote_size=1,
                    ),
                ),
            ],
            constraints=common_constraints,
            rationale="IV极低时买远月平值偏虚call（delta~0.5），持有方向性敞口",
            metadata={"selection_mode": "long_single"},
        )

    if strategy_type == "long_put":
        buy_pct = target if target and target < 0 else None
        return StrategySpec(
            strategy_type="long_put", underlying_id=underlying_id,
            legs=[
                StrategyLegSpec(
                    action="BUY", option_type="PUT", expiry_rule="nearest",
                    strike=None, delta_target=0.50, quantity=1,
                    strike_pct_target=buy_pct,
                    strike_forced=(buy_pct is not None),
                    leg_constraints=LegConstraint(
                        dte_min=45, dte_max=90,
                        max_rel_spread=0.04, min_quote_size=1,
                    ),
                ),
            ],
            constraints=common_constraints,
            rationale="IV极低时买远月平值偏虚put（delta~0.5），持有方向性敞口",
            metadata={"selection_mode": "long_single"},
        )

    # ===== 单腿卖方 =====
    if strategy_type == "naked_call":
        short_pct = resistance
        return StrategySpec(
            strategy_type="naked_call", underlying_id=underlying_id,
            legs=[
                StrategyLegSpec(
                    action="SELL", option_type="CALL", expiry_rule="nearest",
                    strike=None, delta_target=0.18, quantity=1,
                    strike_pct_target=short_pct,
                    strike_forced=(short_pct is not None),
                    leg_constraints=LegConstraint(
                        dte_min=10, dte_max=35,
                        max_rel_spread=0.03, min_quote_size=1,
                    ),
                ),
            ],
            constraints=common_constraints,
            rationale="卖虚值call（delta~0.18），收theta，适合IV偏高+中性偏空市场",
            metadata={"selection_mode": "naked_single"},
        )

    if strategy_type == "naked_put":
        short_pct = support
        return StrategySpec(
            strategy_type="naked_put", underlying_id=underlying_id,
            legs=[
                StrategyLegSpec(
                    action="SELL", option_type="PUT", expiry_rule="nearest",
                    strike=None, delta_target=0.18, quantity=1,
                    strike_pct_target=short_pct,
                    strike_forced=(short_pct is not None),
                    leg_constraints=LegConstraint(
                        dte_min=10, dte_max=35,
                        max_rel_spread=0.03, min_quote_size=1,
                    ),
                ),
            ],
            constraints=common_constraints,
            rationale="卖虚值put（delta~0.18），收theta，适合IV偏高+中性偏多市场",
            metadata={"selection_mode": "naked_single"},
        )

    # Advisor-path covered_call expression only.
    # The dedicated covered-call scan path lives in app.strategy.covered_call_service.
    if strategy_type == "covered_call":
        short_pct = resistance
        return StrategySpec(
            strategy_type="covered_call", underlying_id=underlying_id,
            legs=[
                StrategyLegSpec(
                    action="SELL", option_type="CALL", expiry_rule="nearest",
                    strike=None, delta_target=0.20, quantity=1,
                    strike_pct_target=short_pct,
                    strike_forced=(short_pct is not None),
                    leg_constraints=LegConstraint(
                        dte_min=60, dte_max=180,
                        max_rel_spread=0.03, min_quote_size=1,
                    ),
                ),
            ],
            constraints=common_constraints,
            rationale="备兑卖出虚值call（DTE 60-180天），目标年化收益率3-5%",
            metadata={"selection_mode": "covered_call"},
        )

    return None


# ==============================
# 辅助：term_slope → calendar/diagonal动态prior
# ==============================

def _calendar_prior_from_term_slope(term_slope: Optional[float]) -> float:
    """
    根据期限结构斜率（近月IV - 远月IV）动态计算calendar/diagonal的prior。
    近月比远月贵越多→prior越高（上限0.95，5个vol点饱和）。
    近月比远月便宜→prior越低（下限0.15，强烈不推）。
    数据缺失时返回中性值0.55。
    """
    if term_slope is None:
        return 0.55

    if term_slope >= 0.05:
        return 0.95
    elif term_slope >= 0.03:
        return 0.85
    elif term_slope >= 0.01:
        return 0.75
    elif term_slope >= 0.0:
        return 0.60
    elif term_slope >= -0.01:
        return 0.45
    elif term_slope >= -0.02:
        return 0.30
    else:
        return 0.15


# ==============================
# main compiler
# ==============================

def compile_intent_to_strategies(
    intent: IntentSpec,
    iv_pct: Optional[float] = None,
) -> List[StrategySpec]:
    candidates: List[tuple[str, float]] = []
    opportunity_candidates = derive_opportunity_candidates(intent, iv_pct=iv_pct)
    family_constraints = build_family_constraints(intent, iv_pct=iv_pct)
    family_candidates = derive_family_candidates(
        opportunity_candidates,
        family_constraints,
        intent,
        iv_pct=iv_pct,
    )
    family_candidate_map: Dict[StrategyFamily, FamilyCandidate] = {c.family: c for c in family_candidates}
    family_gate_results: Dict[str, Dict[str, Any]] = _json_safe_value(
        _build_calendar_diagonal_gate_results(intent, iv_pct=iv_pct)
    )
    family_constraints_meta: Dict[str, Any] = _json_safe_value(family_constraints.model_dump())
    fallback_evidence: Dict[str, Any] = _json_safe_value(
        _build_opportunity_evidence_summary(intent, iv_pct=iv_pct)
    )

    # ===== 取market_context里的term_slope，计算calendar动态prior =====
    uid_ctx = _get_uid_ctx(intent)
    term_slope_call = uid_ctx.get("term_slope_call")
    term_slope_put  = uid_ctx.get("term_slope_put")

    cal_prior_call = _calendar_prior_from_term_slope(term_slope_call)
    cal_prior_put  = _calendar_prior_from_term_slope(term_slope_put)

    # diagonal比calendar多一个方向性因子，prior略高（+0.05，上限0.95）
    diag_prior_call = min(0.95, cal_prior_call + 0.05)
    diag_prior_put  = min(0.95, cal_prior_put  + 0.05)

    # ===== vol_view 驱动 =====
    if intent.vol_view == "call_iv_rich":
        candidates += [
            ("call_calendar",    cal_prior_call),
            ("diagonal_call",    diag_prior_call),
            ("diagonal_put",     max(0.15, diag_prior_put - 0.05)),
            ("put_calendar",     max(0.15, cal_prior_put  - 0.05)),
            ("bear_call_spread", 0.75),
            ("bull_call_spread", 0.70),
        ]
    elif intent.vol_view == "put_iv_rich":
        candidates += [
            ("put_calendar",    cal_prior_put),
            ("diagonal_put",    diag_prior_put),
            ("diagonal_call",   max(0.15, diag_prior_call - 0.05)),
            ("call_calendar",   max(0.15, cal_prior_call  - 0.05)),
            ("bear_put_spread", 0.75),
            ("bull_put_spread", 0.70),
        ]
    elif intent.vol_view == "iv_high":
        candidates += [
            ("iron_condor",      0.80),
            ("iron_fly",         0.75),
            ("call_calendar",    min(0.75, cal_prior_call)),
            ("put_calendar",     min(0.75, cal_prior_put)),
            ("bear_call_spread", 0.70),
            ("bull_put_spread",  0.70),
            ("naked_call",       0.70),
            ("naked_put",        0.70),
        ]

    # ===== market_view 驱动 =====
    if intent.market_view == "bullish":
        candidates += [
            ("diagonal_call",    min(0.95, diag_prior_call + 0.10)),
            ("bull_call_spread", 0.70),
            ("bull_put_spread",  0.65),
            ("naked_put",        0.50),
        ]
    elif intent.market_view == "bearish":
        candidates += [
            ("bear_call_spread", 0.90),
            ("bear_put_spread",  0.85),
            ("naked_call",       0.65),
            ("long_put",         0.70),
        ]
    else:  # neutral
        candidates += [
            ("iron_condor",  0.70),
            ("iron_fly",     0.65),
            ("naked_put",    0.65),
        ]

    # ===== asymmetry 驱动 =====
    if intent.asymmetry == "downside":
        candidates += [
            ("bear_put_spread", 0.80),
            ("long_put",        0.65),
            ("diagonal_put",    min(0.95, diag_prior_put + 0.05)),
        ]
    elif intent.asymmetry == "upside":
        candidates += [
            ("bull_call_spread", 0.80),
            ("long_call",        0.65),
            ("diagonal_call",    min(0.95, diag_prior_call + 0.05)),
        ]
    elif intent.asymmetry == "symmetric":
        candidates += [
            ("long_call", 0.70),
            ("long_put",  0.70),
        ]

    # ===== prefer_multi_leg 驱动 =====
    if intent.prefer_multi_leg:
        candidates += [
            ("diagonal_call", min(0.85, diag_prior_call)),
            ("diagonal_put",  min(0.85, diag_prior_put)),
        ]

    # ===== best_map：各策略取最高prior =====
    seeded_types = [strategy_type for strategy_type, _ in candidates]
    print(
        "[vertical_trace] "
        f"uid={intent.underlying_id} "
        "stage=seeded_candidates "
        f"types={seeded_types} "
        f"vertical_presence={_vertical_presence(seeded_types)}"
    )

    best_map: dict[str, float] = {}
    for s, w in candidates:
        if s not in best_map or w > best_map[s]:
            best_map[s] = w

    # ===== call/put_iv_rich 时压低 iron、买方debit spread =====
    if intent.vol_view in ("call_iv_rich", "put_iv_rich"):
        for k in ("iron_condor", "iron_fly"):
            if k in best_map:
                best_map[k] = min(best_map[k], 0.50)
        if intent.vol_view == "call_iv_rich":
            if "bull_call_spread" in best_map:
                best_map["bull_call_spread"] = min(best_map["bull_call_spread"], 0.40)
        if intent.vol_view == "put_iv_rich":
            if "bear_put_spread" in best_map:
                best_map["bear_put_spread"] = min(best_map["bear_put_spread"], 0.40)

    # ===== IV percentile 驱动调整 =====
    if iv_pct is not None:
        if iv_pct <= 0.15:
            for k in ("iron_condor", "iron_fly", "bear_call_spread",
                      "bull_put_spread", "naked_call", "naked_put"):
                if k in best_map:
                    best_map[k] = min(best_map[k], 0.20)
            best_map["long_call"] = max(best_map.get("long_call", 0), 0.85)
            best_map["long_put"]  = max(best_map.get("long_put",  0), 0.85)

        elif iv_pct <= 0.30:
            for k in ("iron_condor", "iron_fly", "naked_call", "naked_put"):
                if k in best_map:
                    best_map[k] = round(best_map[k] * 0.7, 3)

        elif iv_pct >= 0.85:
            for k in ("iron_condor", "iron_fly", "bear_call_spread",
                      "bull_put_spread", "naked_call", "naked_put"):
                if k in best_map:
                    best_map[k] = min(1.0, round(best_map[k] * 1.4, 3))
            best_map.pop("long_call", None)
            best_map.pop("long_put", None)

        elif iv_pct >= 0.70:
            for k in ("iron_condor", "iron_fly", "bear_call_spread",
                      "bull_put_spread", "naked_call", "naked_put"):
                if k in best_map:
                    best_map[k] = min(1.0, round(best_map[k] * 1.2, 3))
            for k in ("long_call", "long_put"):
                if k in best_map:
                    best_map[k] = round(best_map[k] * 0.5, 3)

    # ===== put/call skew 驱动调整 =====
    skew = uid_ctx.get("put_call_skew") or 0.0

    if skew > 0.03:
        for k in ("put_calendar", "diagonal_put"):
            if k in best_map:
                best_map[k] = min(1.0, round(best_map[k] * 1.1, 3))
        if "naked_put" in best_map:
            best_map["naked_put"] = round(best_map["naked_put"] * 0.9, 3)
        if "bear_put_spread" in best_map:
            best_map["bear_put_spread"] = min(1.0, round(best_map["bear_put_spread"] * 1.05, 3))

    elif skew < -0.03:
        for k in ("call_calendar", "diagonal_call"):
            if k in best_map:
                best_map[k] = min(1.0, round(best_map[k] * 1.1, 3))
        if "naked_call" in best_map:
            best_map["naked_call"] = round(best_map["naked_call"] * 0.9, 3)
        if "bull_call_spread" in best_map:
            best_map["bull_call_spread"] = min(1.0, round(best_map["bull_call_spread"] * 1.05, 3))

    # ===== Greeks意图驱动prior调整 =====
    vega_pref  = intent.greeks_preference.get("vega", {})
    gamma_pref = intent.greeks_preference.get("gamma", {})

    vega_sign     = vega_pref.get("sign")
    vega_strength = float(vega_pref.get("strength", 0))
    gamma_sign    = gamma_pref.get("sign")
    gamma_strength = float(gamma_pref.get("strength", 0))

    if vega_sign == "positive" and vega_strength > 0.5:
        # 用户预期IV上升：压制short vega策略，激活long方向
        for k in ("naked_call", "naked_put",
                  "bear_call_spread", "bull_put_spread",
                  "iron_condor", "iron_fly"):
            if k in best_map:
                best_map[k] = round(best_map[k] * 0.5, 3)
        best_map["long_put"]  = max(best_map.get("long_put",  0), 0.80)
        best_map["long_call"] = max(best_map.get("long_call", 0), 0.70)

    elif vega_sign == "negative" and vega_strength > 0.5:
        # 用户预期IV下降：加权short vega策略
        for k in ("naked_call", "naked_put", "iron_condor", "iron_fly"):
            if k in best_map:
                best_map[k] = min(1.0, round(best_map[k] * 1.2, 3))

    if gamma_sign == "positive" and gamma_strength > 0.5:
        # 用户预期大幅波动：压制short gamma策略
        for k in ("naked_call", "naked_put", "iron_condor", "iron_fly"):
            if k in best_map:
                best_map[k] = round(best_map[k] * 0.6, 3)
        best_map["long_put"]  = max(best_map.get("long_put",  0), 0.75)
        best_map["long_call"] = max(best_map.get("long_call", 0), 0.75)

    elif gamma_sign == "negative" and gamma_strength > 0.5:
        # 用户预期窄幅震荡：加权short gamma策略
        for k in ("iron_condor", "iron_fly"):
            if k in best_map:
                best_map[k] = min(1.0, round(best_map[k] * 1.15, 3))

    # ===== allowed_strategies 提权 =====
    if intent.allowed_strategies:
        for k in intent.allowed_strategies:
            if k in best_map:
                best_map[k] = max(best_map[k], 0.90)
            else:
                best_map[k] = 0.90

    # ===== banned_strategies 过滤 =====
    for banned in (intent.banned_strategies or []):
        best_map.pop(banned, None)

    # ===== defined_risk_only 过滤 =====
    if intent.defined_risk_only:
        for k in ("naked_call", "naked_put"):
            best_map.pop(k, None)

    print(
        "[vertical_trace] "
        f"uid={intent.underlying_id} "
        "stage=best_map_before_family_filter "
        f"keys={list(best_map.keys())} "
        f"vertical_presence={_vertical_presence(best_map.keys())}"
    )

    # ===== 构建 StrategySpec 列表 =====
    family_candidate_map = _select_family_shortlist(
        best_map=best_map,
        family_candidates=family_candidates,
        constraints=family_constraints,
        intent=intent,
        iv_pct=iv_pct,
    )
    vertical_family_candidate = family_candidate_map.get("vertical")
    print(
        "[vertical_trace] "
        f"uid={intent.underlying_id} "
        "stage=family_shortlist "
        f"vertical_shortlisted={vertical_family_candidate.shortlisted if vertical_family_candidate is not None else False} "
        f"reasons={(vertical_family_candidate.shortlist_reasons if vertical_family_candidate is not None else ['vertical_family_missing'])}"
    )
    shortlisted_families = {
        family
        for family in {_strategy_family_for_type(strategy_type) for strategy_type in best_map}
        if family_candidate_map.get(family) is not None and family_candidate_map[family].shortlisted
    }
    if shortlisted_families:
        best_map = {
            strategy_type: weight
            for strategy_type, weight in best_map.items()
            if _strategy_family_for_type(strategy_type) in shortlisted_families
        }

    best_map = _backfill_vertical_strategy_types(
        best_map=best_map,
        family_candidate_map=family_candidate_map,
        intent=intent,
    )
    print(
        "[vertical_trace] "
        f"uid={intent.underlying_id} "
        "stage=post_backfill_best_map "
        f"keys={list(best_map.keys())} "
        f"vertical_presence={_vertical_presence(best_map.keys())}"
    )
    shortlisted_families = {
        family
        for family in {_strategy_family_for_type(strategy_type) for strategy_type in best_map}
        if family_candidate_map.get(family) is not None and family_candidate_map[family].shortlisted
    }

    for vertical_type in _VERTICAL_STRATEGY_TYPES:
        if vertical_type not in best_map:
            print(
                "[vertical_trace] "
                f"uid={intent.underlying_id} "
                "stage=build_strategy_spec "
                f"strategy_type={vertical_type} "
                "result=not_attempted"
            )

    specs: List[StrategySpec] = []
    for strategy_type, weight in best_map.items():
        spec = build_strategy_spec(strategy_type, intent)
        if strategy_type in _VERTICAL_STRATEGY_TYPES:
            print(
                "[vertical_trace] "
                f"uid={intent.underlying_id} "
                "stage=build_strategy_spec "
                f"strategy_type={strategy_type} "
                f"result={'valid_spec' if spec is not None else 'none'}"
            )
        if spec is None:
            continue

        spec.metadata = spec.metadata or {}
        spec.metadata["prior_weight"] = weight
        family = _strategy_family_for_type(strategy_type)
        family_candidate = family_candidate_map.get(family)
        opportunity_candidate = _select_opportunity_for_family(opportunity_candidates, family)
        opportunity_type = (
            family_candidate.opportunity_type
            if family_candidate is not None
            else _default_opportunity_for_family(family)
        )

        spec.metadata["opportunity_type"] = opportunity_type
        spec.metadata["strategy_family"] = family
        spec.metadata["opportunity_score"] = _json_safe_value(round(
            opportunity_candidate.score if opportunity_candidate is not None else weight,
            4,
        ))
        spec.metadata["opportunity_confidence"] = _json_safe_value(round(
            opportunity_candidate.confidence if opportunity_candidate is not None else weight,
            4,
        ))
        spec.metadata["opportunity_evidence_summary"] = _json_safe_value(
            dict(opportunity_candidate.evidence)
            if opportunity_candidate is not None
            else dict(fallback_evidence)
        )
        spec.metadata["family_score"] = _json_safe_value(round(
            family_candidate.score if family_candidate is not None else weight,
            4,
        ))
        spec.metadata["family_confidence"] = _json_safe_value(round(
            family_candidate.confidence if family_candidate is not None else weight,
            4,
        ))
        spec.metadata["family_constraints"] = family_constraints_meta
        spec.metadata["family_gate_results"] = family_gate_results
        spec.metadata["family_shortlist"] = {
            "shortlisted_families": sorted(shortlisted_families),
            "active": bool(shortlisted_families),
        }
        if family_candidate is not None:
            spec.metadata["family_soft_signals"] = _json_safe_value(dict(family_candidate.soft_signals))
            spec.metadata["family_hard_constraints_applied"] = _json_safe_value(list(family_candidate.hard_constraints_applied))
            spec.metadata["family_gate_passed"] = family_candidate.gating_passed
            spec.metadata["family_gate_reasons"] = _json_safe_value(list(family_candidate.gating_reasons))
            spec.metadata["family_shortlisted"] = family_candidate.shortlisted
            spec.metadata["family_shortlist_reasons"] = _json_safe_value(list(family_candidate.shortlist_reasons))
        specs.append(spec)

    print(
        "[compiler] "
        f"uid={intent.underlying_id} "
        f"best_map={len(best_map)} "
        f"specs={len(specs)}"
    )
    final_types = [spec.strategy_type for spec in specs]
    print(
        "[vertical_trace] "
        f"uid={intent.underlying_id} "
        "stage=final_specs "
        f"types={final_types} "
        f"vertical_presence={_vertical_presence(final_types)}"
    )

    return specs
