from __future__ import annotations

import time
from typing import Any, Dict, List, Optional

from sqlalchemy.engine import Engine
from app.ai.llm_parser import parse_with_llm
from app.ai.llm_commentary import build_briefing_llm_commentary
from app.models.schemas import (
    AdvisorRunResponse,
    IntentSpec,
    ResolvedStrategy,
)
from app.strategy.compiler import compile_intent_to_strategies
from app.strategy.strategy_ranker import rank_strategies
from app.strategy.strategy_resolver import (
    load_market_snapshot,
    resolve_strategy_from_snapshot,
)
from app.strategy.greeks_monitor import build_strategy_greeks_report
from app.strategy.iv_percentile import build_iv_percentile_report
from app.strategy.briefing import build_briefing
from app.strategy.recommendation_selector import select_recommendations
from app.strategy.execution_guidance import attach_execution_guidance

UNDERLYING_KEYWORDS = {
    "510050": ["上证50", "上证 50", "50etf", "510050", "上证五十", "50 etf"],
    "510300": ["沪深300", "沪深 300", "300etf", "510300", "沪深三百", "300 etf", "沪深300etf"],
    "510500": ["中证500", "中证 500", "500etf", "510500", "中证五百", "500 etf"],
    "588000": ["科创50", "科创 50", "科创板50", "科创板 50", "科创etf", "588000", "科创五十"],
    "588080": ["588080", "科创50易方达", "易方达科创", "易方达588080"],
    "159901": ["深证100", "深证 100", "深100", "100etf", "159901", "深证一百"],
    "159915": ["创业板", "创业板etf", "159915", "创业板100", "创业etf"],
    "159919": ["159919", "沪深300深", "300etf深", "华泰柏瑞", "嘉实300"],
    "159922": ["159922", "中证500深", "嘉实500", "嘉实中证500"],
}

UNDERLYING_KEYWORDS.update(
    {
        "510050": UNDERLYING_KEYWORDS.get("510050", []) + ["上证50", "上证 50", "上证五十"],
        "510300": UNDERLYING_KEYWORDS.get("510300", []) + ["沪深300", "沪深 300", "沪深三百", "沪深300etf"],
        "510500": UNDERLYING_KEYWORDS.get("510500", []) + ["中证500", "中证 500", "中证五百"],
        "588000": UNDERLYING_KEYWORDS.get("588000", []) + ["科创50", "科创 50", "科创板50", "科创板 50", "科创五十"],
        "588080": UNDERLYING_KEYWORDS.get("588080", []) + ["科创50易方达", "易方达科创"],
        "159901": UNDERLYING_KEYWORDS.get("159901", []) + ["深证100", "深证 100", "深100", "深证一百"],
        "159915": UNDERLYING_KEYWORDS.get("159915", []) + ["创业板", "创业板etf", "创业etf"],
        "159919": UNDERLYING_KEYWORDS.get("159919", []) + ["华泰柏瑞"],
        "159922": UNDERLYING_KEYWORDS.get("159922", []) + ["嘉实500", "嘉实中证500"],
    }
)

ALL_UNDERLYING_IDS = [
    "510300", "510050", "510500",
    "588000", "588080",
    "159915", "159901", "159919", "159922",
]
_FAMILY_DIAG_STRATEGY_TYPES = (
    "naked_call",
    "naked_put",
    "iron_condor",
    "iron_fly",
)
_FAMILY_DIAG_TOP_N = 5

_VALID_STRATEGY_NAMES = {
    "bear_call_spread",
    "bull_put_spread",
    "call_calendar",
    "put_calendar",
    "diagonal_call",
    "diagonal_put",
    "bull_call_spread",
    "bear_put_spread",
    "iron_condor",
    "iron_fly",
    "long_call",
    "long_put",
    "naked_call",
    "naked_put",
    "covered_call",
}

_STRATEGY_ALIASES = {
    "call_diagonal": "diagonal_call",
    "put_diagonal": "diagonal_put",
    "calendar_call": "call_calendar",
    "calendar_put": "put_calendar",
    "callcalendar": "call_calendar",
    "putcalendar": "put_calendar",
    "diagonalcall": "diagonal_call",
    "diagonalput": "diagonal_put",
    "bull_call": "bull_call_spread",
    "bull_put": "bull_put_spread",
    "bear_call": "bear_call_spread",
    "bear_put": "bear_put_spread",
    "longcall": "long_call",
    "longput": "long_put",
    "nakedcall": "naked_call",
    "nakedput": "naked_put",
    "coveredcall": "covered_call",
    "ironcondor": "iron_condor",
    "ironfly": "iron_fly",
}

def _canonicalize_strategy_name(name: Any) -> Optional[str]:
    if not isinstance(name, str):
        return None

    key = name.strip().lower()
    if not key:
        return None

    key = key.replace("-", "_").replace(" ", "_")
    key = _STRATEGY_ALIASES.get(key, key)

    if key in _VALID_STRATEGY_NAMES:
        return key
    return None


def _normalize_strategy_names(raw_names: Any, field_name: str) -> List[str]:
    if not isinstance(raw_names, list):
        return []

    normalized: List[str] = []
    seen = set()

    for raw_name in raw_names:
        canonical = _canonicalize_strategy_name(raw_name)
        if canonical is None:
            if isinstance(raw_name, str) and raw_name.strip():
                print(f"[parse_text_to_intent] ignore invalid {field_name}: {raw_name}")
            continue
        if canonical not in seen:
            normalized.append(canonical)
            seen.add(canonical)

    return normalized


def _merge_greeks_with_context(
    greeks_preference: dict,
    market_context: dict,
    user_weight: float = 0.8,
    machine_weight: float = 0.2,
) -> dict:
    if not market_context:
        return greeks_preference

    machine_signals: Dict[str, Dict[str, Any]] = {}

    for uid, ctx in market_context.items():
        trend = ctx.get("trend")
        hv20 = ctx.get("hv20") or 0.0
        skew = ctx.get("put_call_skew") or 0.0

        if trend == "downtrend":
            sig = {"sign": "negative", "strength": 0.4}
        elif trend == "uptrend":
            sig = {"sign": "positive", "strength": 0.4}
        else:
            sig = None

        if sig:
            existing = machine_signals.get("delta")
            if existing is None or sig["strength"] > existing["strength"]:
                machine_signals["delta"] = sig

        if hv20 > 0.25:
            sig = {"sign": "positive", "strength": 0.3}
            existing = machine_signals.get("gamma")
            if existing is None or sig["strength"] > existing["strength"]:
                machine_signals["gamma"] = sig

        if abs(skew) > 0.03:
            sig = {"sign": "positive", "strength": 0.25}
            existing = machine_signals.get("theta")
            if existing is None or sig["strength"] > existing["strength"]:
                machine_signals["theta"] = sig

    merged = dict(greeks_preference)

    for greek, m_pref in machine_signals.items():
        m_sign = m_pref["sign"]
        m_strength = m_pref["strength"]

        if greek in merged:
            u_pref = merged[greek]
            u_sign = u_pref["sign"]
            u_strength = u_pref["strength"]

            if u_sign == m_sign:
                new_strength = min(1.0, u_strength * user_weight + m_strength * machine_weight)
            else:
                new_strength = max(0.0, u_strength * user_weight - m_strength * machine_weight)

            merged[greek] = {"sign": u_sign, "strength": round(new_strength, 3)}
        else:
            merged[greek] = {
                "sign": m_sign,
                "strength": round(m_strength * machine_weight, 3),
            }

    return merged


def _merge_greek_pref(
    existing: Dict[str, Dict[str, Any]],
    greek: str,
    sign: str,
    strength: float,
) -> None:
    current = existing.get(greek)
    if current is None or float(current.get("strength", 0.0) or 0.0) < strength:
        existing[greek] = {"sign": sign, "strength": strength}


def _extract_text_intent_overrides(text: str) -> Dict[str, Any]:
    t = (text or "").strip().lower()

    slight_bullish = any(k in t for k in ("轻微看多", "略看多", "小幅看多", "slightly bullish"))
    slight_bearish = any(k in t for k in ("轻微看空", "略看空", "小幅看空", "slightly bearish"))
    bullish = slight_bullish or any(k in t for k in ("偏多", "看多", "看涨", "bullish"))
    bearish = slight_bearish or any(k in t for k in ("偏空", "看空", "看跌", "bearish"))
    strong_direction = any(k in t for k in ("强烈看多", "明显看多", "强烈看空", "明显看空", "strongly bullish", "strongly bearish"))

    positive_theta = any(
        k in t
        for k in (
            "theta为正",
            "theta 为正",
            "正theta",
            "正 theta",
            "收theta",
            "收 theta",
            "能收theta",
            "能收 theta",
            "收时间价值",
            "time decay income",
        )
    )
    defined_risk = any(k in t for k in ("风险可控", "有限风险", "defined risk", "定义风险"))
    no_naked_short = any(k in t for k in ("不想裸卖", "不裸卖", "禁止裸卖", "不要裸卖", "no naked short"))
    directional_backup = any(
        k in t
        for k in ("保留一个方向性备选", "方向性备选", "方向备选", "备选方案", "directional backup")
    )
    weak_bearish_range = any(
        k in t
        for k in ("震荡偏弱", "上方空间有限", "不会大涨", "有压力", "有cap", "有 cap")
    )
    weak_bullish_range = any(
        k in t
        for k in ("震荡偏强", "下方空间有限", "不会大跌", "有支撑")
    )
    neutral_structure = any(
        k in t
        for k in ("没有明确方向判断", "没有方向判断", "无明确方向", "中性", "震荡", "区间", "不确定涨跌方向", "range")
    )
    range_bias = None
    if weak_bearish_range:
        range_bias = "weak_bearish_range"
    elif weak_bullish_range:
        range_bias = "weak_bullish_range"
    elif neutral_structure:
        range_bias = "strict_range"
    neutral_structure = bool(neutral_structure or range_bias)
    income_family = positive_theta or any(
        k in t
        for k in ("优先考虑能收theta", "优先收theta", "优先收 theta", "收权利金", "权利金收入", "income")
    )

    market_view = None
    market_view_strength = None
    if bullish and not bearish:
        market_view = "bullish"
        market_view_strength = 0.35 if slight_bullish else 0.90 if strong_direction else 0.65
    elif bearish and not bullish:
        market_view = "bearish"
        market_view_strength = 0.35 if slight_bearish else 0.90 if strong_direction else 0.65

    return {
        "market_view": market_view,
        "market_view_strength": market_view_strength,
        "require_positive_theta": positive_theta,
        "prefer_income_family": income_family,
        "ban_naked_short": no_naked_short,
        "prefer_directional_backup": directional_backup,
        "prefer_neutral_structure": neutral_structure,
        "range_bias": range_bias,
        "horizon_views": _extract_horizon_views(text),
        "vol_view_detail": _extract_vol_view_detail(text),
        "defined_risk_only": defined_risk or no_naked_short,
        "prefer_multi_leg": positive_theta or defined_risk or income_family,
    }


def _segment_between(text: str, start_keywords: tuple[str, ...], stop_keywords: tuple[str, ...]) -> str:
    lower = text.lower()
    starts = [lower.find(k.lower()) for k in start_keywords if lower.find(k.lower()) >= 0]
    if not starts:
        return ""
    start = min(starts)
    stops = [lower.find(k.lower(), start + 1) for k in stop_keywords if lower.find(k.lower(), start + 1) >= 0]
    end = min(stops) if stops else len(text)
    return text[start:end]


def _infer_horizon_direction(segment: str) -> tuple[str, float]:
    s = (segment or "").lower()
    if any(k in s for k in ("不悲观", "不太悲观", "修复", "企稳", "中性", "震荡", "区间")):
        return "neutral", 0.45
    if any(k in s for k in ("非常看空", "明显偏空", "明显看空", "大跌")):
        return "bearish", 0.80
    if any(k in s for k in ("偏空", "看空", "偏弱", "下跌", "走弱")):
        return "bearish", 0.60
    if any(k in s for k in ("非常看多", "明显偏多", "明显看多", "大涨")):
        return "bullish", 0.80
    if any(k in s for k in ("偏多", "看多", "偏强", "上涨", "走强")):
        return "bullish", 0.60
    return "unknown", 0.0


def _infer_horizon_vol_bias(segment: str) -> str:
    s = (segment or "").lower()
    if any(k in s for k in ("波动上升", "iv上升", "iv 上升", "波动抬头", "iv抬头", "iv 抬头", "波动率上升")):
        return "up"
    if "抬头" in s and ("波动" in s or "iv" in s):
        return "up"
    if any(k in s for k in ("波动回落", "iv回落", "iv 回落", "波动下降", "iv下降", "iv 下降", "波动率下降")):
        return "down"
    if "回落" in s and ("波动" in s or "iv" in s or "中期" in s):
        return "down"
    if any(k in s for k in ("波动不大", "不会明显上升", "iv不会明显上升", "iv 不会明显上升", "波动平稳")):
        return "flat"
    return "unknown"


def _extract_horizon_views(text: str) -> Optional[Dict[str, Dict[str, Any]]]:
    raw = text or ""
    t = raw.lower()
    short_keywords = ("短期", "近期", "近月", "本周", "未来几天", "这几天")
    medium_keywords = ("中期", "后续", "一两个月", "未来一个月", "中远期")
    has_short = any(k in t for k in short_keywords)
    has_medium = any(k in t for k in medium_keywords)
    if not has_short and not has_medium:
        return None

    out: Dict[str, Dict[str, Any]] = {}
    if has_short:
        segment = _segment_between(raw, short_keywords, medium_keywords) or raw
        direction, strength = _infer_horizon_direction(segment)
        out["short_term"] = {
            "direction": direction,
            "direction_strength": strength,
            "vol_bias": _infer_horizon_vol_bias(segment),
        }
    if has_medium:
        segment = _segment_between(raw, medium_keywords, short_keywords) or raw
        direction, strength = _infer_horizon_direction(segment)
        out["medium_term"] = {
            "direction": direction,
            "direction_strength": strength,
            "vol_bias": _infer_horizon_vol_bias(segment),
        }
    return out or None


def _normalize_horizon_views(value: Any) -> Optional[Dict[str, Dict[str, Any]]]:
    if not isinstance(value, dict):
        return None
    out: Dict[str, Dict[str, Any]] = {}
    for key in ("short_term", "medium_term"):
        item = value.get(key)
        if not isinstance(item, dict):
            continue
        direction = item.get("direction", "unknown")
        if direction not in ("bullish", "bearish", "neutral", "unknown"):
            direction = "unknown"
        vol_bias = item.get("vol_bias", "unknown")
        if vol_bias not in ("up", "down", "flat", "unknown"):
            vol_bias = "unknown"
        try:
            strength = float(item.get("direction_strength", 0.0) or 0.0)
        except (TypeError, ValueError):
            strength = 0.0
        out[key] = {
            "direction": direction,
            "direction_strength": max(0.0, min(1.0, strength)),
            "vol_bias": vol_bias,
        }
    return out or None


def _unknown_vol_detail() -> Dict[str, Dict[str, str]]:
    return {
        "atm": {"level": "unknown", "expected_change": "unknown", "horizon": "unknown"},
        "call": {"level": "unknown", "expected_change": "unknown"},
        "put": {"level": "unknown", "expected_change": "unknown"},
        "skew": {"direction": "unknown", "expected_change": "unknown"},
        "term": {"front": "unknown", "back": "unknown", "expected_shape_change": "unknown"},
    }


def _extract_vol_view_detail(text: str) -> Optional[Dict[str, Dict[str, str]]]:
    t = (text or "").strip().lower()
    detail = _unknown_vol_detail()
    changed = False

    def mark(section: str, key: str, value: str) -> None:
        nonlocal changed
        detail[section][key] = value
        changed = True

    if any(k in t for k in ("put iv 偏高", "put iv偏高", "认沽 iv 偏高", "认沽iv偏高", "put仍贵", "put 仍贵", "put贵", "认沽端仍然偏贵", "认沽端仍贵", "认沽贵")):
        mark("put", "level", "rich")
    if any(k in t for k in ("call iv 偏高", "call iv偏高", "认购 iv 偏高", "认购iv偏高", "call贵", "认购端偏贵", "认购贵")):
        mark("call", "level", "rich")
    if any(k in t for k in ("call iv 不会大涨", "call iv不会大涨", "认购 iv 涨不动", "认购iv涨不动", "认购端涨不动", "call涨不动", "call iv 涨不动")):
        mark("call", "expected_change", "flat")
    if any(k in t for k in ("call iv 回落", "认购iv回落", "认购 iv 回落", "call iv 下降", "认购iv下降")):
        mark("call", "expected_change", "down")
    if any(k in t for k in ("put iv 可能再抬头", "put iv可能再抬头", "认沽iv抬头", "认沽 iv 抬头", "put iv 抬头")):
        mark("put", "expected_change", "up")

    if any(k in t for k in ("iv 偏高", "iv偏高", "波动率高", "隐波偏高", "整体iv高")):
        mark("atm", "level", "high")
    if any(k in t for k in ("iv 偏低", "iv偏低", "波动率低", "隐波偏低", "整体iv低")):
        mark("atm", "level", "low")
    if any(k in t for k in ("iv 回落", "iv回落", "波动率回落", "波动回落", "隐波回落")):
        mark("atm", "expected_change", "down")
    if any(k in t for k in ("iv 抬头", "iv抬头", "iv 可能再抬头", "iv可能再抬头", "波动再起", "波动抬头", "波动率上升")):
        mark("atm", "expected_change", "up")
    if "抬头" in t and ("iv" in t or "波动" in t):
        mark("atm", "expected_change", "up")
    if any(k in t for k in ("波动不大", "iv 不会明显上升", "iv不会明显上升", "波动率不会明显上升")):
        mark("atm", "expected_change", "flat")

    if any(k in t for k in ("短期iv", "短期 iv", "近期iv", "近期 iv", "近月iv", "近月 iv", "短期波动", "近期波动")) and detail["atm"]["expected_change"] != "unknown":
        mark("atm", "horizon", "short_term")
    if any(k in t for k in ("中期iv", "中期 iv", "后续iv", "后续 iv", "中期波动", "后续波动")) and detail["atm"]["expected_change"] != "unknown":
        mark("atm", "horizon", "medium_term")

    if any(k in t for k in ("下行保护更贵", "put 更贵", "put更贵", "skew 偏向下行保护", "skew偏向下行保护", "偏向下行保护")):
        mark("skew", "direction", "put_rich")
    if any(k in t for k in ("call 更贵", "call更贵", "skew 偏向认购", "skew偏向认购")):
        mark("skew", "direction", "call_rich")
    if any(k in t for k in ("skew 走平", "skew走平")):
        mark("skew", "expected_change", "flatten")
    if any(k in t for k in ("skew 变陡", "skew变陡")):
        mark("skew", "expected_change", "steepen")

    if any(k in t for k in ("近月 iv 高", "近月iv高", "近月 iv 偏高", "近月iv偏高", "front rich")):
        mark("term", "front", "rich")
    if any(k in t for k in ("远月更高", "远月 iv 高", "远月iv高", "back rich")):
        mark("term", "back", "rich")
    if any(k in t for k in ("期限结构变陡", "term steepen", "term structure steepen")):
        mark("term", "expected_shape_change", "steepen")
    if any(k in t for k in ("期限结构走平", "term flatten", "term structure flatten")):
        mark("term", "expected_shape_change", "flatten")

    return detail if changed else None


def _normalize_vol_view_detail(value: Any) -> Optional[Dict[str, Dict[str, str]]]:
    if not isinstance(value, dict):
        return None
    out = _unknown_vol_detail()
    changed = False
    allowed = {
        "atm": {"level": {"high", "normal", "low", "unknown"}, "expected_change": {"up", "down", "flat", "unknown"}, "horizon": {"short_term", "medium_term", "unknown"}},
        "call": {"level": {"rich", "cheap", "normal", "unknown"}, "expected_change": {"up", "down", "flat", "unknown"}},
        "put": {"level": {"rich", "cheap", "normal", "unknown"}, "expected_change": {"up", "down", "flat", "unknown"}},
        "skew": {"direction": {"put_rich", "call_rich", "neutral", "unknown"}, "expected_change": {"steepen", "flatten", "stable", "unknown"}},
        "term": {"front": {"rich", "cheap", "normal", "unknown"}, "back": {"rich", "cheap", "normal", "unknown"}, "expected_shape_change": {"steepen", "flatten", "unknown"}},
    }
    for section, fields in allowed.items():
        raw_section = value.get(section)
        if not isinstance(raw_section, dict):
            continue
        for key, valid_values in fields.items():
            raw_value = raw_section.get(key)
            if raw_value in valid_values:
                out[section][key] = raw_value
                changed = changed or raw_value != "unknown"
    return out if changed else None


def parse_text_to_intent(
    text: str,
    underlying_id: str = "510300",
    market_context: Optional[dict] = None,
) -> IntentSpec:
    result = parse_with_llm(text, market_context=market_context)

    if result is None:
        print("[parse_text_to_intent] LLM failed, falling back to rule parser")
        return _rule_parse_text_to_intent(text, underlying_id)

    overrides = _extract_text_intent_overrides(text)

    underlying_ids = result.get("underlying_ids", [])
    if not underlying_ids:
        underlying_ids = [underlying_id]

    preferred = _normalize_strategy_names(
        result.get("preferred_strategies", []),
        field_name="preferred_strategies",
    )
    allowed_strategies = preferred if preferred else None

    banned_strategies = _normalize_strategy_names(
        result.get("banned_strategies", []),
        field_name="banned_strategies",
    )
    if bool(result.get("ban_naked_short", False)) or overrides["ban_naked_short"]:
        for strategy_type in ("naked_call", "naked_put"):
            if strategy_type not in banned_strategies:
                banned_strategies.append(strategy_type)

    raw_greeks = result.get("greeks_preference", {})
    greeks_preference: Dict[str, Dict[str, Any]] = {}
    if isinstance(raw_greeks, dict):
        for greek, pref in raw_greeks.items():
            if not isinstance(pref, dict):
                continue
            sign = pref.get("sign")
            strength = pref.get("strength")
            if sign not in ("positive", "negative", "neutral"):
                continue
            try:
                strength = float(strength)
            except (TypeError, ValueError):
                continue
            if 0.0 <= strength <= 1.0:
                greeks_preference[greek] = {"sign": sign, "strength": strength}

    market_view = overrides["market_view"] or result.get("market_view", "neutral")
    try:
        market_view_strength = float(result.get("market_view_strength", 0.5) or 0.5)
    except (TypeError, ValueError):
        market_view_strength = 0.5
    if overrides["market_view_strength"] is not None:
        market_view_strength = overrides["market_view_strength"]
    market_view_strength = max(0.0, min(1.0, market_view_strength))
    range_bias = result.get("range_bias")
    if range_bias not in ("strict_range", "weak_bearish_range", "weak_bullish_range"):
        range_bias = None
    if overrides["range_bias"] is not None:
        range_bias = overrides["range_bias"]
    horizon_views = _normalize_horizon_views(result.get("horizon_views"))
    if overrides["horizon_views"] is not None:
        horizon_views = overrides["horizon_views"]
    vol_view_detail = _normalize_vol_view_detail(result.get("vol_view_detail"))
    if overrides["vol_view_detail"] is not None:
        vol_view_detail = overrides["vol_view_detail"]

    if bool(result.get("require_positive_theta", False)) or overrides["require_positive_theta"]:
        _merge_greek_pref(greeks_preference, "theta", "positive", 0.9)
    if market_view == "bearish":
        _merge_greek_pref(greeks_preference, "delta", "negative", max(0.35, market_view_strength))
    elif market_view == "bullish":
        _merge_greek_pref(greeks_preference, "delta", "positive", max(0.35, market_view_strength))

    if market_context:
        greeks_preference = _merge_greeks_with_context(
            greeks_preference, market_context, user_weight=0.85, machine_weight=0.15
        )

    raw_price_levels = result.get("price_levels", {})
    price_levels: Dict[str, float] = {}
    if isinstance(raw_price_levels, dict):
        for key in ("support", "resistance", "target"):
            value = raw_price_levels.get(key)
            if value is not None:
                try:
                    price_levels[key] = float(value)
                except (TypeError, ValueError):
                    pass

    asymmetry = result.get("asymmetry")
    if asymmetry not in ("upside", "downside", "symmetric", None):
        asymmetry = None

    return IntentSpec(
        underlying_id=underlying_ids[0],
        underlying_ids=underlying_ids,
        market_view=market_view,
        vol_view=result.get("vol_view", "none"),
        risk_preference=result.get("risk_preference", "low"),
        defined_risk_only=bool(result.get("defined_risk_only", False) or overrides["defined_risk_only"]),
        prefer_multi_leg=bool(result.get("prefer_multi_leg", False) or overrides["prefer_multi_leg"]),
        dte_min=result.get("dte_min", 20),
        dte_max=result.get("dte_max", 45),
        max_rel_spread=0.03,
        min_quote_size=1,
        allowed_strategies=allowed_strategies,
        banned_strategies=banned_strategies,
        raw_text=text,
        require_positive_theta=bool(result.get("require_positive_theta", False) or overrides["require_positive_theta"]),
        prefer_income_family=bool(result.get("prefer_income_family", False) or overrides["prefer_income_family"]),
        ban_naked_short=bool(result.get("ban_naked_short", False) or overrides["ban_naked_short"]),
        prefer_directional_backup=bool(result.get("prefer_directional_backup", False) or overrides["prefer_directional_backup"]),
        prefer_neutral_structure=bool(result.get("prefer_neutral_structure", False) or overrides["prefer_neutral_structure"]),
        range_bias=range_bias,
        market_view_strength=market_view_strength,
        horizon_views=horizon_views,
        vol_view_detail=vol_view_detail,
        greeks_preference=greeks_preference,
        price_levels=price_levels,
        asymmetry=asymmetry,
        market_context_data=market_context or {},
    )


def _parse_underlying_ids(text: str, default_id: str) -> List[str]:
    t = text.lower()
    found = []
    for uid, keywords in UNDERLYING_KEYWORDS.items():
        if any(k in t for k in keywords):
            found.append(uid)
    return found if found else [default_id]


def _rule_parse_text_to_intent(text: str, underlying_id: str = "510300") -> IntentSpec:
    t = (text or "").strip().lower()
    overrides = _extract_text_intent_overrides(text)

    market_view = "neutral"
    vol_view = "none"
    risk_preference = "low"
    defined_risk_only = False
    prefer_multi_leg = False
    dte_min = 20
    dte_max = 45
    max_rel_spread = 0.03
    min_quote_size = 1
    banned_strategies: List[str] = []
    allowed_strategies = None

    if overrides["market_view"]:
        market_view = overrides["market_view"]
    elif any(k in t for k in ["轻微看多", "略看多", "小幅看多", "偏多", "看涨", "bullish"]):
        market_view = "bullish"
    elif any(k in t for k in ["轻微看空", "略看空", "小幅看空", "偏空", "看跌", "bearish"]):
        market_view = "bearish"

    if any(k in t for k in ["波动率偏高", "隐波高", "iv高", "vol high", "high iv"]):
        vol_view = "iv_high"
    elif any(k in t for k in ["波动率偏低", "隐波低", "iv低", "vol low", "low iv"]):
        vol_view = "iv_low"
    elif any(k in t for k in ["认购偏贵", "call贵", "call iv rich", "call_iv_rich"]):
        vol_view = "call_iv_rich"
    elif any(k in t for k in ["认沽偏贵", "put贵", "put iv rich", "put_iv_rich"]):
        vol_view = "put_iv_rich"
    elif any(k in t for k in ["近月更贵", "近月波动率高", "front high", "term_front_high"]):
        vol_view = "term_front_high"
    elif any(k in t for k in ["远月更贵", "远月波动率高", "back high", "term_back_high"]):
        vol_view = "term_back_high"

    if any(k in t for k in ["低风险", "保守", "low risk"]):
        risk_preference = "low"
    elif any(k in t for k in ["高风险", "激进", "high risk"]):
        risk_preference = "high"
    else:
        risk_preference = "medium" if "中风险" in t else "low"

    if any(k in t for k in ["不裸卖", "defined risk", "定义损失", "有限风险"]):
        defined_risk_only = True
    if overrides["defined_risk_only"]:
        defined_risk_only = True
    if any(k in t for k in ["多腿", "组合", "spread", "calendar", "diagonal", "跨期", "价差"]):
        prefer_multi_leg = True
    if overrides["prefer_multi_leg"]:
        prefer_multi_leg = True
    if any(k in t for k in ["近月", "front month"]):
        dte_min, dte_max = 10, 35
    if any(k in t for k in ["中期", "30到60天", "30-60天"]):
        dte_min, dte_max = 30, 60
    if any(k in t for k in ["不做日历", "不要calendar", "no calendar"]):
        banned_strategies.extend(["call_calendar", "put_calendar"])
    if any(k in t for k in ["不做对角", "不要diagonal", "no diagonal"]):
        banned_strategies.extend(["diagonal_call", "diagonal_put"])
    if overrides["ban_naked_short"]:
        banned_strategies.extend(["naked_call", "naked_put"])

    if any(k in t for k in ["备兑", "covered call", "卖备兑"]):
        allowed_strategies = ["covered_call"]

    greeks_preference: Dict[str, Dict[str, Any]] = {}
    if overrides["require_positive_theta"]:
        _merge_greek_pref(greeks_preference, "theta", "positive", 0.9)
    direction_strength = overrides["market_view_strength"] if overrides["market_view_strength"] is not None else 0.5
    if market_view == "bearish":
        _merge_greek_pref(greeks_preference, "delta", "negative", max(0.35, direction_strength))
    elif market_view == "bullish":
        _merge_greek_pref(greeks_preference, "delta", "positive", max(0.35, direction_strength))

    underlying_ids = _parse_underlying_ids(t, underlying_id)
    return IntentSpec(
        underlying_id=underlying_ids[0],
        underlying_ids=underlying_ids,
        market_view=market_view,
        vol_view=vol_view,
        risk_preference=risk_preference,
        defined_risk_only=defined_risk_only,
        prefer_multi_leg=prefer_multi_leg,
        dte_min=dte_min,
        dte_max=dte_max,
        max_rel_spread=max_rel_spread,
        min_quote_size=min_quote_size,
        allowed_strategies=allowed_strategies,
        banned_strategies=list(dict.fromkeys(banned_strategies)),
        raw_text=text,
        require_positive_theta=overrides["require_positive_theta"],
        prefer_income_family=overrides["prefer_income_family"],
        ban_naked_short=overrides["ban_naked_short"],
        prefer_directional_backup=overrides["prefer_directional_backup"],
        prefer_neutral_structure=overrides["prefer_neutral_structure"],
        range_bias=overrides["range_bias"],
        market_view_strength=direction_strength,
        horizon_views=overrides["horizon_views"],
        vol_view_detail=overrides["vol_view_detail"],
        greeks_preference=greeks_preference,
        price_levels={},
        asymmetry=None,
        market_context_data={},
    )


def build_disabled_backtest(resolved_candidates):
    items = []
    for s in resolved_candidates[:5]:
        items.append({
            "strategy_type": s.strategy_type,
            "score": s.score,
            "net_credit": s.net_credit,
            "net_debit": s.net_debit,
            "legs": [
                {
                    "contract_id": leg.contract_id,
                    "action": leg.action,
                    "option_type": leg.option_type,
                    "expiry_date": leg.expiry_date,
                    "strike": leg.strike,
                    "mid": leg.mid,
                    "delta": leg.delta,
                    "gamma": leg.gamma,
                    "theta": leg.theta,
                    "vega": leg.vega,
                    "iv": leg.iv,
                    "dte": leg.dte,
                }
                for leg in s.legs
            ],
            "greeks_report": s.metadata.get("greeks_report", {}),
        })
    return {
        "status": "disabled",
        "summary": "当前阶段暂不做期权回测，重点转向真实选腿、评分统一与 Greeks 监控。",
        "items": items,
    }


def run_advisor(
    engine: Engine,
    text: str,
    underlying_id: str = "510300",
    underlying_ids: Optional[List[str]] = None,
) -> AdvisorRunResponse:
    from app.data.market_context import build_market_context_multi
    import time

    t0_all = time.perf_counter()
    cache: Dict[Any, Any] = {}

    explicit_underlying_ids = [str(uid) for uid in (underlying_ids or []) if uid]
    print(
        "[multi_run_check] "
        f"service_input underlying_id={underlying_id} "
        f"underlying_ids={explicit_underlying_ids if explicit_underlying_ids else underlying_ids}"
    )
    if explicit_underlying_ids:
        seen = set()
        target_ids: List[str] = []
        for uid in explicit_underlying_ids:
            if uid not in seen:
                target_ids.append(uid)
                seen.add(uid)
    elif underlying_id == "ALL":
        target_ids = list(ALL_UNDERLYING_IDS)
    else:
        target_ids = [underlying_id]
    combined_mode = len(target_ids) > 1 or underlying_id == "ALL"
    print(f"[multi_run_check] request_underlying_ids={target_ids}")
    print(f"[multi_run_check] combined_mode={combined_mode}")
    print(f"[multi_run_check] target_ids_before_loop={target_ids}")

    # 扫描模式：先为全部候选标的准备上下文
    # 单标的模式：只准备当前标的
    ctx_ids = list(target_ids)

    def _get_iv_report(uid: str) -> Optional[Dict[str, Any]]:
        key = ("atm_iv", uid)
        if key in cache:
            return cache[key]
        rpt = build_iv_percentile_report(engine, uid)
        cache[key] = rpt
        return rpt

    def _get_market_context(
        uids: List[str],
        iv_pct_map: Dict[str, Optional[float]],
    ) -> Dict[str, Any]:
        missing_ids = [uid for uid in uids if ("market_context", uid) not in cache]
        if missing_ids:
            built = build_market_context_multi(engine, missing_ids, iv_pcts=iv_pct_map)
            for uid in missing_ids:
                cache[("market_context", uid)] = (built or {}).get(uid, {})
        return {uid: cache.get(("market_context", uid), {}) for uid in uids}

    # 先算 iv_percentile：既给 market_context 用，也给后续 greeks_report 复用
    t0 = time.perf_counter()
    iv_reports: Dict[str, Optional[Dict[str, Any]]] = {}
    iv_pcts: Dict[str, Optional[float]] = {}

    for uid in ctx_ids:
        try:
            rpt = _get_iv_report(uid)
            iv_reports[uid] = rpt
            if rpt:
                iv_pcts[uid] = rpt.get("composite_percentile")
            else:
                iv_pcts[uid] = None
        except Exception as e:
            print(f"[run_advisor] iv_percentile failed for {uid}: {e}")
            iv_reports[uid] = None
            iv_pcts[uid] = None

    print(f"[timing] build_iv_percentile_report total = {time.perf_counter() - t0:.3f}s")

    # 计算 market_context
    t0 = time.perf_counter()
    try:
        market_context = _get_market_context(ctx_ids, iv_pcts)
    except Exception as e:
        print(f"[run_advisor] market_context failed: {e}")
        market_context = {}
    print(f"[timing] build_market_context_multi = {time.perf_counter() - t0:.3f}s")

    # 解析意图（含二八合成）
    t0 = time.perf_counter()
    print(f"[multi_run_check] before_parse underlying_ids={target_ids}")
    intent = parse_text_to_intent(
        text=text,
        underlying_id=target_ids[0],
        market_context=market_context,
    )
    print("[multi_run_check] after_parse parse_called_once=True")
    print(f"[timing] parse_text_to_intent = {time.perf_counter() - t0:.3f}s")

    # 这里先保持你当前语义：
    # 单标的请求强制只跑传入 uid；
    # ALL 模式仍由 intent.effective_underlying_ids 决定。
    # （如果你后面要改成“ALL 强制全扫”，那是下一步。）
    intent = intent.model_copy(update={
        "underlying_id": target_ids[0],
        "underlying_ids": target_ids,
    })

    all_resolved: List[ResolvedStrategy] = []
    compile_resolve_summary: List[Dict[str, Any]] = []
    family_diag_tracker: Dict[str, Dict[str, Dict[str, int]]] = {
        uid: {
            strategy_type: {"compiled": 0, "resolved": 0}
            for strategy_type in _FAMILY_DIAG_STRATEGY_TYPES
        }
        for uid in target_ids
    }

    for uid in target_ids:
        t0_uid = time.perf_counter()

        uid_intent = intent.model_copy(update={"underlying_id": uid})
        iv_pct = iv_pcts.get(uid)

        t0 = time.perf_counter()
        candidate_specs = compile_intent_to_strategies(uid_intent, iv_pct=iv_pct)
        print(f"[timing] {uid} compile_intent_to_strategies = {time.perf_counter() - t0:.3f}s, specs={len(candidate_specs)}")
        for strategy_type in _FAMILY_DIAG_STRATEGY_TYPES:
            family_diag_tracker.setdefault(uid, {}).setdefault(strategy_type, {"compiled": 0, "resolved": 0})
            family_diag_tracker[uid][strategy_type]["compiled"] = sum(
                1 for spec in candidate_specs if spec.strategy_type == strategy_type
            )
        summary_item: Dict[str, Any] = {
            "underlying_id": uid,
            "compiled_specs": len(candidate_specs),
            "resolved": 0,
        }

        t0 = time.perf_counter()
        try:
            snapshot = load_market_snapshot(engine, uid)
        except Exception as e:
            print(f"[run_advisor] load snapshot failed for {uid}: {e}")
            compile_resolve_summary.append(summary_item)
            continue
        print(f"[timing] {uid} load_market_snapshot = {time.perf_counter() - t0:.3f}s, quotes={len(snapshot.merged_quotes)}")

        resolved_count = 0
        t0 = time.perf_counter()
        for spec in candidate_specs:
            try:
                rs = resolve_strategy_from_snapshot(snapshot, spec)
                if rs is not None:
                    rs.metadata["greeks_preference"] = uid_intent.greeks_preference
                    rs.metadata["intent_constraints"] = {
                        "require_positive_theta": uid_intent.require_positive_theta,
                        "prefer_income_family": uid_intent.prefer_income_family,
                        "ban_naked_short": uid_intent.ban_naked_short,
                        "prefer_directional_backup": uid_intent.prefer_directional_backup,
                        "prefer_neutral_structure": uid_intent.prefer_neutral_structure,
                        "range_bias": uid_intent.range_bias,
                        "horizon_views": uid_intent.horizon_views,
                        "defined_risk_only": uid_intent.defined_risk_only,
                        "prefer_multi_leg": uid_intent.prefer_multi_leg,
                        "market_view": uid_intent.market_view,
                        "market_view_strength": uid_intent.market_view_strength,
                        "vol_view_detail": uid_intent.vol_view_detail,
                    }
                    rs.metadata["iv_pct"] = iv_pct
                    all_resolved.append(rs)
                    resolved_count += 1
                    if spec.strategy_type in _FAMILY_DIAG_STRATEGY_TYPES:
                        family_diag_tracker[uid][spec.strategy_type]["resolved"] += 1
            except Exception as e:
                print(f"[run_advisor] {uid} {spec.strategy_type} failed: {e}")
        print(f"[timing] {uid} resolve all specs = {time.perf_counter() - t0:.3f}s, resolved={resolved_count}")
        for strategy_type in _FAMILY_DIAG_STRATEGY_TYPES:
            stats = family_diag_tracker[uid][strategy_type]
            print(
                f"[family_diag] uid={uid} stage=resolver_count "
                f"strategy={strategy_type} attempted={stats['compiled'] > 0} "
                f"compiled={stats['compiled']} resolved={stats['resolved']}"
            )
        summary_item["resolved"] = resolved_count
        compile_resolve_summary.append(summary_item)

        print(f"[timing] {uid} total = {time.perf_counter() - t0_uid:.3f}s")

    if compile_resolve_summary:
        summary_text = " | ".join(
            f"{item['underlying_id']}: specs={item['compiled_specs']}, resolved={item['resolved']}"
            for item in compile_resolve_summary
        )
        print(f"[advisor_summary] {summary_text}")
    resolved_underlying_ids_after_loop = sorted({s.underlying_id for s in all_resolved})
    print(f"[multi_run_check] resolved_underlying_ids_after_loop={resolved_underlying_ids_after_loop}")

    t0 = time.perf_counter()
    ranked = rank_strategies(all_resolved)
    print(f"[timing] rank_strategies = {time.perf_counter() - t0:.3f}s, ranked={len(ranked)}")
    top_ranked = ranked[:min(_FAMILY_DIAG_TOP_N, len(ranked))]
    top_cutoff = top_ranked[-1].score if top_ranked else None
    for uid in target_ids:
        for strategy_type in _FAMILY_DIAG_STRATEGY_TYPES:
            resolved_matches = [
                s for s in ranked
                if s.underlying_id == uid and s.strategy_type == strategy_type
            ]
            if not resolved_matches:
                stats = family_diag_tracker.get(uid, {}).get(strategy_type, {"compiled": 0, "resolved": 0})
                if stats.get("compiled", 0) > 0:
                    print(
                        f"[family_diag] uid={uid} stage=ranking strategy={strategy_type} "
                        f"final_score=None top_n_cutoff={top_cutoff} reason=not_resolved"
                    )
                continue

            best_match = max(resolved_matches, key=lambda s: s.score or 0.0)
            in_top_results = any(
                s.underlying_id == uid and s.strategy_type == strategy_type
                for s in top_ranked
            )
            print(
                f"[family_diag] uid={uid} stage=ranking strategy={strategy_type} "
                f"final_score={best_match.score} top_n_cutoff={top_cutoff} "
                f"reason={'in_top_results' if in_top_results else 'ranked_below_cutoff'}"
            )

    # 这里改为复用前面已经算过的 iv_reports，避免每个策略再次查库
    t0 = time.perf_counter()
    for s in ranked:
        s.metadata["greeks_report"] = build_strategy_greeks_report(
            strategy=s,
            iv_pct_report=iv_reports.get(s.underlying_id),
        )
    print(f"[timing] build_strategy_greeks_report total = {time.perf_counter() - t0:.3f}s")

    attach_execution_guidance(ranked)

    t0 = time.perf_counter()
    decision_payload = select_recommendations(
        ranked,
        market_context=market_context,
        intent=intent,
    )
    print(f"[timing] select_recommendations = {time.perf_counter() - t0:.3f}s")

    t0 = time.perf_counter()
    backtest_result = build_disabled_backtest(ranked)

    resp = AdvisorRunResponse(
        parsed_intent=intent,
        candidate_strategies=[],
        resolved_candidates=ranked,
        backtest_result=backtest_result,
        decision_payload=decision_payload,
    )
    resp.calendar_recommendations = []
    resp.briefing = build_briefing(
        ranked,
        text,
        market_context=market_context,
        decision_payload=decision_payload,
    )
    resp.briefing_llm_commentary = build_briefing_llm_commentary(
        briefing=resp.briefing,
        decision_payload=decision_payload,
        ranked=ranked,
        market_context=market_context,
    )
    if isinstance(resp.briefing, dict):
        resp.briefing["llm_commentary"] = resp.briefing_llm_commentary
    combined_resolved_underlying_ids = sorted({s.underlying_id for s in ranked})
    briefing_market_overview_ids = [
        item.get("underlying_id")
        for item in ((resp.briefing or {}).get("market_overview") or [])
        if item.get("underlying_id")
    ]
    print(f"[multi_run_check] combined_resolved_underlying_ids={combined_resolved_underlying_ids}")
    print(f"[multi_run_check] briefing_market_overview_ids={briefing_market_overview_ids}")
    print(f"[timing] build_briefing + response = {time.perf_counter() - t0:.3f}s")

    print(f"[timing] run_advisor TOTAL = {time.perf_counter() - t0_all:.3f}s")
    return resp
