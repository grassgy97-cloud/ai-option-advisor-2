from __future__ import annotations

import json
import re
import traceback
from importlib import metadata
from types import SimpleNamespace
from typing import Any, Dict, Optional

from app.ai.client import DEFAULT_ANTHROPIC_MODEL, get_anthropic_client, get_anthropic_proxy_debug
from app.core.config import ANTHROPIC_API_KEY


COMMENTARY_PROVIDER = "anthropic"
ADVISOR_PROMPT_VERSION = "v2"

SECTION_MARKET = "\u3010\u5e02\u573a\u7ed3\u6784\u3011"
SECTION_PRIMARY = "\u3010\u4e3b\u7b56\u7565\u903b\u8f91\u3011"
SECTION_ALT = "\u3010\u4e0e\u5907\u9009\u7b56\u7565\u5bf9\u6bd4\u3011"
SECTION_EXECUTION = "\u3010\u6267\u884c\u8981\u70b9\u3011"
SECTION_RISK = "\u3010\u98ce\u9669\u63d0\u793a\u3011"

FORBIDDEN_VISIBLE_TERMS = (
    "score=",
    "score",
    "评分",
    "iv_percentile",
    "greeks_report",
    "execution_guidance",
    "strategy_rankings",
)


def _mask_api_key(value: str) -> str:
    if not value:
        return ""
    if len(value) <= 8:
        return "***"
    return f"{value[:3]}***{value[-4:]}"


def get_commentary_llm_config() -> Dict[str, Any]:
    return {
        "provider": COMMENTARY_PROVIDER,
        "model": DEFAULT_ANTHROPIC_MODEL,
        "api_key_present": bool(ANTHROPIC_API_KEY),
        "api_key_hint": _mask_api_key(ANTHROPIC_API_KEY),
    }


def _safe_anthropic_version() -> Optional[str]:
    try:
        return metadata.version("anthropic")
    except metadata.PackageNotFoundError:
        return None


def _client_debug_info(client: Any = None) -> Dict[str, Any]:
    if client is None:
        try:
            client = get_anthropic_client()
        except Exception:
            client = SimpleNamespace()
    base_url = getattr(client, "base_url", None)
    timeout = getattr(client, "timeout", None)
    proxy_debug = get_anthropic_proxy_debug()
    return {
        "anthropic_version": _safe_anthropic_version(),
        "model": DEFAULT_ANTHROPIC_MODEL,
        "timeout": str(timeout) if timeout is not None else None,
        "base_url": str(base_url) if base_url is not None else None,
        "proxy_env": proxy_debug["proxy_env"],
        "proxy_detected": proxy_debug["proxy_detected"],
        "proxy_used": proxy_debug["proxy_used"],
        "proxy_invalid_reason": proxy_debug["proxy_invalid_reason"],
        "ignore_proxy_config": proxy_debug["ignore_proxy_config"],
        "ignore_proxy_effective": proxy_debug["ignore_proxy_effective"],
    }


def _traceback_last_line(exc: BaseException) -> str:
    lines = traceback.format_exception(type(exc), exc, exc.__traceback__)
    return lines[-1].strip() if lines else ""


def _exception_debug_info(exc: BaseException) -> Dict[str, Any]:
    cause = getattr(exc, "__cause__", None)
    return {
        "error_repr": repr(exc),
        "error_cause_repr": repr(cause) if cause is not None else None,
        "traceback_last": _traceback_last_line(exc),
    }


def _log_commentary_context(request_type: str) -> None:
    config = get_commentary_llm_config()
    debug = _client_debug_info(SimpleNamespace())
    print(
        "[llm_commentary] "
        f"provider={config['provider']} "
        f"model={config['model']} "
        f"api_key_present={str(config['api_key_present']).lower()} "
        f"api_key_hint={config['api_key_hint'] or '-'} "
        f"request_type={request_type} "
        f"anthropic_version={debug['anthropic_version']} "
        f"proxy_detected={str(debug['proxy_detected']).lower()} "
        f"proxy_used={str(debug['proxy_used']).lower()} "
        f"proxy_invalid_reason={debug['proxy_invalid_reason'] or '-'} "
        f"ignore_proxy_config={str(debug['ignore_proxy_config']).lower()}"
    )


def _log_commentary_error(request_type: str, exc: Exception) -> None:
    details = _exception_debug_info(exc)
    print(
        "[llm_commentary] "
        f"request_type={request_type} "
        f"error_type={type(exc).__name__} "
        f"error_message={str(exc)} "
        f"error_repr={details['error_repr']} "
        f"error_cause_repr={details['error_cause_repr']} "
        f"traceback_last={details['traceback_last']}"
    )


def _unavailable(reason: str) -> Dict[str, Any]:
    return {
        "available": False,
        "text": "",
        "market_structure": "",
        "primary_logic": "",
        "alternative_comparison": "",
        "execution_points": "",
        "risk_warning": "",
        "summary": "",
        "why_primary": "",
        "what_to_watch": [],
        "risk_explanation": "",
        "actionable_suggestions": [],
        "error": reason,
    }


def _trim_text(value: Any, limit: int) -> str:
    text = str(value or "").strip()
    if len(text) <= limit:
        return text
    return text[: max(0, limit - 1)].rstrip("\uFF0C\u3002\uFF1B\u3001,.;: \t\r\n") + "\u3002"


def _trim_list(values: Any, limit: int, item_limit: int) -> list[str]:
    if not isinstance(values, list):
        return []
    return [_trim_text(item, item_limit) for item in values[:limit] if str(item or "").strip()]


def _extract_json(text: str) -> Dict[str, Any]:
    raw = (text or "").strip()
    if raw.startswith("```"):
        raw = raw.strip("`")
        if raw.lower().startswith("json"):
            raw = raw[4:].strip()
    start = raw.find("{")
    end = raw.rfind("}")
    if start == -1 or end == -1 or end <= start:
        raise ValueError("no_json_object")
    return json.loads(raw[start:end + 1])


def _call_commentary_llm(
    system_prompt: str,
    payload: Dict[str, Any],
    *,
    request_type: str,
    max_tokens: int = 900,
) -> Optional[Dict[str, Any]]:
    _log_commentary_context(request_type)
    if not ANTHROPIC_API_KEY:
        print(f"[llm_commentary] request_type={request_type} error_type=MissingApiKey error_message=ANTHROPIC_API_KEY is not set")
        return None
    client = get_anthropic_client()
    response = client.messages.create(
        model=DEFAULT_ANTHROPIC_MODEL,
        max_tokens=max_tokens,
        temperature=0.2,
        system=system_prompt,
        messages=[
            {
                "role": "user",
                "content": json.dumps(payload, ensure_ascii=False, default=str),
            }
        ],
    )
    raw_text = response.content[0].text
    print(f"[llm_commentary] parse_mode=json")
    print(f"[llm_commentary] raw_output_preview={str(raw_text or '').replace(chr(10), ' ')[:220]}")
    try:
        return _extract_json(raw_text)
    except Exception as exc:
        print(f"[llm_commentary] parse_error={type(exc).__name__}: {exc}")
        raise


def _call_commentary_text(
    system_prompt: str,
    payload: Dict[str, Any],
    *,
    request_type: str,
    max_tokens: int = 900,
) -> Optional[str]:
    _log_commentary_context(request_type)
    if not ANTHROPIC_API_KEY:
        print(f"[llm_commentary] request_type={request_type} error_type=MissingApiKey error_message=ANTHROPIC_API_KEY is not set")
        return None
    client = get_anthropic_client()
    response = client.messages.create(
        model=DEFAULT_ANTHROPIC_MODEL,
        max_tokens=max_tokens,
        temperature=0.2,
        system=system_prompt,
        messages=[
            {
                "role": "user",
                "content": json.dumps(payload, ensure_ascii=False, default=str),
            }
        ],
    )
    raw_text = str(getattr(response.content[0], "text", "") or "").strip()
    print("[llm_commentary] parse_mode=text")
    print(f"[llm_commentary] raw_output_preview={raw_text.replace(chr(10), ' ')[:220]}")
    return raw_text


def _compact_strategy(strategy: Any) -> Dict[str, Any]:
    metadata = getattr(strategy, "metadata", {}) or {}
    greeks_report = metadata.get("greeks_report") or {}
    return {
        "underlying_id": getattr(strategy, "underlying_id", None),
        "strategy_type": getattr(strategy, "strategy_type", None),
        "score": getattr(strategy, "score", None),
        "net_credit": getattr(strategy, "net_credit", None),
        "net_debit": getattr(strategy, "net_debit", None),
        "net_delta": getattr(strategy, "net_delta", None),
        "net_gamma": getattr(strategy, "net_gamma", None),
        "net_theta": getattr(strategy, "net_theta", None),
        "net_vega": getattr(strategy, "net_vega", None),
        "score_tier": metadata.get("score_tier"),
        "execution_guidance": metadata.get("execution_guidance"),
        "greeks_report": greeks_report,
        "iv_percentile": greeks_report.get("iv_percentile") if isinstance(greeks_report, dict) else None,
        "risk_flags": greeks_report.get("risk_flags") if isinstance(greeks_report, dict) else None,
        "intent_constraints": metadata.get("intent_constraints"),
    }


def _compose_advisor_commentary(parsed: Dict[str, Any]) -> str:
    sections = [
        (SECTION_MARKET, parsed.get("market_structure")),
        (SECTION_PRIMARY, parsed.get("primary_logic")),
        (SECTION_ALT, parsed.get("alternative_comparison")),
        (SECTION_EXECUTION, parsed.get("execution_points")),
        (SECTION_RISK, parsed.get("risk_warning")),
    ]
    return "\n".join(f"{title}{str(body or '').strip()}" for title, body in sections if str(body or "").strip())


def _sanitize_visible_text(text: str) -> str:
    cleaned = str(text or "")
    cleaned = re.sub(r"score\s*=\s*[-+]?\d+(?:\.\d+)?", "", cleaned, flags=re.IGNORECASE)
    for term in FORBIDDEN_VISIBLE_TERMS:
        cleaned = re.sub(re.escape(term), "", cleaned, flags=re.IGNORECASE)
    return cleaned.strip()


def _matched_banned_terms(text: str) -> list[str]:
    lowered = str(text or "").lower()
    return [term for term in FORBIDDEN_VISIBLE_TERMS if term.lower() in lowered]


def _log_advisor_output_quality(raw_text: str, cleaned_text: str) -> None:
    raw_hits = _matched_banned_terms(raw_text)
    cleaned_hits = _matched_banned_terms(cleaned_text)
    if raw_hits or cleaned_hits:
        print(
            "[llm_commentary] advisor warning=banned_terms "
            f"raw_hits={raw_hits} cleaned_hits={cleaned_hits}"
        )
    preview = cleaned_text.replace("\n", " ")[:160]
    print(f"[llm_commentary] advisor output_preview={preview}")


def _extract_section_text(text: str, heading: str) -> str:
    raw = str(text or "")
    start = raw.find(heading)
    if start == -1:
        return ""
    start += len(heading)
    following = [
        idx
        for title in (SECTION_MARKET, SECTION_PRIMARY, SECTION_ALT, SECTION_EXECUTION, SECTION_RISK)
        if title != heading
        for idx in [raw.find(title, start)]
        if idx != -1
    ]
    end = min(following) if following else len(raw)
    return raw[start:end].strip()


def _advisor_system_prompt() -> str:
    return f"""
You are the explanation-only layer for an A-share ETF options advisor.
Your job is to give a decisive trading-desk recommendation explanation, not to describe data.
Write like a sell-side / prop-desk options trader making a clear call.

STRICT BANS:
- Do not mention exact scores or write "score=...".
- Do not mention raw field names: iv_percentile, greeks_report, execution_guidance, strategy_rankings.
- Do not restate the user's raw input.
- Do not use vague phrases such as "该策略较优", "风险可控", "适合", "合理", "可以考虑".
- If you mention risk control, name the exact leg or strike that creates the risk.

MANDATORY REASONING ORDER:
STEP 1 Market structure: state whether PUT IV is above CALL IV, what that means for protection demand / downside premium, and whether term structure matters.
STEP 2 Primary logic: state the single key reason the primary is better than the secondary; connect skew / term / theta / direction into one cause-effect chain.
STEP 3 Alternative comparison: name one secondary strategy and explain the one key reason it loses to the primary. Use the form "不是X，而是Y，因为...".
STEP 4 Execution judgment: give a yes/no trading call. Say "值得做" or "暂不追价"; if yes, say whether to work near mid or wait for a pullback.
STEP 5 Risk warning: identify the exact risky leg or strike, and state the condition under which it becomes a problem.

CRITICAL SKEW RULE:
If PUT IV is materially above CALL IV, emphasize "下行保护更贵 / PUT端溢价更高" rather than simply saying "市场偏空".
If CALL IV is materially above PUT IV, explain whether call-side selling or call-side buying is better justified.

DECISION INTENSITY:
- Each section must contain a decision or causal judgment, not a field recap.
- The primary-vs-secondary comparison is the most important part.
- Always answer: why this primary, why not the named secondary, whether to trade now, and which leg can hurt.

Output plain Chinese text only. Do not output JSON, Markdown fences, or explanations outside the five sections.
Use exactly this section format:
{SECTION_MARKET}
...

{SECTION_PRIMARY}
...

{SECTION_ALT}
...

{SECTION_EXECUTION}
...

{SECTION_RISK}
...

Keep each section <= 2 sentences. Keep total text <= 180 Chinese characters if possible.
Be concise, concrete, and judgmental. No filler.
""".strip()


def build_briefing_llm_commentary(
    *,
    briefing: Optional[Dict[str, Any]],
    decision_payload: Optional[Dict[str, Any]],
    ranked: list[Any],
    market_context: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    if not briefing or not decision_payload:
        return _unavailable("missing_structured_context")

    first_metadata = getattr(ranked[0], "metadata", {}) if ranked else {}
    intent_constraints = first_metadata.get("intent_constraints", {}) if isinstance(first_metadata, dict) else {}
    payload = {
        "primary_recommendations": decision_payload.get("primary_recommendations") or [],
        "secondary_recommendations": decision_payload.get("secondary_recommendations") or [],
        "decision_payload": decision_payload,
        "strategy_rankings": [_compact_strategy(item) for item in ranked[:3]],
        "briefing_table_top": (briefing.get("table") or [])[:5],
        "recommendation_groups": briefing.get("recommendation_groups") or [],
        "market_overview": briefing.get("market_overview") or [],
        "cross_underlying_summary": briefing.get("cross_underlying_summary") or {},
        "horizon_views": intent_constraints.get("horizon_views"),
        "vol_view_detail": intent_constraints.get("vol_view_detail"),
        "market_context": market_context or {},
    }

    try:
        print(f"[llm_commentary] advisor prompt_version={ADVISOR_PROMPT_VERSION}")
        print("[llm_commentary] advisor called=true")
        raw_text = _call_commentary_text(_advisor_system_prompt(), payload, request_type="advisor")
        if not raw_text:
            print("[llm_commentary] advisor output_preview=")
            return _unavailable("anthropic_api_key_missing")
        text = _sanitize_visible_text(_trim_text(raw_text, 1200))
        _log_advisor_output_quality(raw_text, text)
        return {
            "available": True,
            "text": text,
            "market_structure": _sanitize_visible_text(_trim_text(_extract_section_text(text, SECTION_MARKET), 80)),
            "primary_logic": _sanitize_visible_text(_trim_text(_extract_section_text(text, SECTION_PRIMARY), 100)),
            "alternative_comparison": _sanitize_visible_text(_trim_text(_extract_section_text(text, SECTION_ALT), 100)),
            "execution_points": _sanitize_visible_text(_trim_text(_extract_section_text(text, SECTION_EXECUTION), 80)),
            "risk_warning": _sanitize_visible_text(_trim_text(_extract_section_text(text, SECTION_RISK), 80)),
            "summary": _sanitize_visible_text(_trim_text(_extract_section_text(text, SECTION_MARKET), 80)),
            "why_primary": _sanitize_visible_text(_trim_text(_extract_section_text(text, SECTION_PRIMARY), 80)),
            "what_to_watch": [_sanitize_visible_text(_trim_text(_extract_section_text(text, SECTION_RISK), 48))],
        }
    except Exception as exc:
        _log_commentary_error("advisor", exc)
        print("[llm_commentary] advisor output_preview=")
        return _unavailable("llm_failed")


def build_monitoring_llm_commentary(monitor_payload: Dict[str, Any]) -> Dict[str, Any]:
    if not monitor_payload.get("monitoring_summary"):
        return _unavailable("missing_monitoring_summary")

    system_prompt = (
        "You are the explanation-only layer for A-share ETF options position monitoring. "
        "Only explain risks from the structured JSON in concise professional Chinese. "
        "Do not recalculate risk, invent numbers, or generate trading orders. "
        "Output plain Chinese text only. Do not output JSON. "
        "Keep text within 150 Chinese characters."
    )
    payload = {
        "underlying_id": monitor_payload.get("underlying_id"),
        "monitoring_summary": monitor_payload.get("monitoring_summary"),
        "monitored_legs": (monitor_payload.get("monitored_legs") or [])[:8],
        "risk_contributors": monitor_payload.get("risk_contributors") or [],
        "hedge_suggestions": monitor_payload.get("hedge_suggestions") or [],
    }

    try:
        raw_text = _call_commentary_text(system_prompt, payload, request_type="monitor", max_tokens=300)
        if not raw_text:
            return _unavailable("anthropic_api_key_missing")
        text = _sanitize_visible_text(_trim_text(raw_text, 150))
        return {
            "available": True,
            "text": text,
            "summary": _trim_text(text, 60),
            "risk_explanation": _trim_text(text, 80),
            "actionable_suggestions": [text] if text else [],
        }
    except Exception as exc:
        _log_commentary_error("monitor", exc)
        return _unavailable("llm_failed")


def debug_llm_commentary_connectivity() -> Dict[str, Any]:
    config = get_commentary_llm_config()
    client = None
    try:
        client = get_anthropic_client()
    except Exception:
        client = None
    sdk_debug = _client_debug_info(client)
    result: Dict[str, Any] = {
        "ok": False,
        "provider": config["provider"],
        "model": config["model"],
        "anthropic_version": sdk_debug["anthropic_version"],
        "timeout": sdk_debug["timeout"],
        "base_url": sdk_debug["base_url"],
        "proxy_env": sdk_debug["proxy_env"],
        "proxy_detected": sdk_debug["proxy_detected"],
        "proxy_used": sdk_debug["proxy_used"],
        "proxy_invalid_reason": sdk_debug["proxy_invalid_reason"],
        "ignore_proxy_config": sdk_debug["ignore_proxy_config"],
        "ignore_proxy_effective": sdk_debug["ignore_proxy_effective"],
        "api_key_present": config["api_key_present"],
        "api_key_hint": config["api_key_hint"],
        "message": "",
        "error_type": None,
        "error_message": None,
        "error_repr": None,
        "error_cause_repr": None,
        "traceback_last": None,
    }
    _log_commentary_context("debug")
    if not ANTHROPIC_API_KEY:
        result["error_type"] = "MissingApiKey"
        result["error_message"] = "ANTHROPIC_API_KEY is not set"
        print("[llm_commentary] request_type=debug error_type=MissingApiKey error_message=ANTHROPIC_API_KEY is not set")
        return result

    try:
        if client is None:
            client = get_anthropic_client()
        response = client.messages.create(
            model=DEFAULT_ANTHROPIC_MODEL,
            max_tokens=80,
            temperature=0.0,
            system="Reply with one concise Chinese sentence.",
            messages=[
                {
                    "role": "user",
                    "content": "Please reply in one Chinese sentence: LLM commentary connected.",
                }
            ],
        )
        message = getattr(response.content[0], "text", "")
        result["ok"] = True
        result["message"] = str(message or "").strip()
        return result
    except Exception as exc:
        _log_commentary_error("debug", exc)
        details = _exception_debug_info(exc)
        result["error_type"] = type(exc).__name__
        result["error_message"] = str(exc)
        result["error_repr"] = details["error_repr"]
        result["error_cause_repr"] = details["error_cause_repr"]
        result["traceback_last"] = details["traceback_last"]
        return result
