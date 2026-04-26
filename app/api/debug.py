from __future__ import annotations

import traceback

from fastapi import APIRouter
import requests

from app.ai.client import DEFAULT_ANTHROPIC_MODEL, get_anthropic_proxy_debug
from app.ai.llm_commentary import debug_llm_commentary_connectivity, get_commentary_llm_config
from app.core.config import ANTHROPIC_API_KEY


router = APIRouter(prefix="/debug", tags=["debug"])


@router.get("/llm-commentary")
def llm_commentary_debug():
    try:
        return debug_llm_commentary_connectivity()
    except Exception as exc:
        traceback.print_exc()
        return {
            "ok": False,
            "provider": "anthropic",
            "model": None,
            "api_key_present": False,
            "message": "",
            "error_type": type(exc).__name__,
            "error_message": str(exc),
        }


@router.get("/anthropic-raw")
def anthropic_raw_debug():
    config = get_commentary_llm_config()
    proxy_debug = get_anthropic_proxy_debug()
    result = {
        "ok": False,
        "provider": config["provider"],
        "model": DEFAULT_ANTHROPIC_MODEL,
        "api_key_present": config["api_key_present"],
        "api_key_hint": config["api_key_hint"],
        "proxy_detected": proxy_debug["proxy_detected"],
        "proxy_used": proxy_debug["proxy_used"],
        "proxy_invalid_reason": proxy_debug["proxy_invalid_reason"],
        "ignore_proxy_config": proxy_debug["ignore_proxy_config"],
        "ignore_proxy_effective": proxy_debug["ignore_proxy_effective"],
        "proxy_env": proxy_debug["proxy_env"],
        "status_code": None,
        "response_text": "",
        "error_type": None,
        "error_message": None,
    }
    if not ANTHROPIC_API_KEY:
        result["error_type"] = "MissingApiKey"
        result["error_message"] = "ANTHROPIC_API_KEY is not set"
        return result

    try:
        session = requests.Session()
        session.trust_env = bool(proxy_debug["proxy_used"])
        response = session.post(
            "https://api.anthropic.com/v1/messages",
            headers={
                "x-api-key": ANTHROPIC_API_KEY,
                "anthropic-version": "2023-06-01",
                "content-type": "application/json",
            },
            json={
                "model": DEFAULT_ANTHROPIC_MODEL,
                "max_tokens": 32,
                "messages": [
                    {
                        "role": "user",
                        "content": "Reply with: raw anthropic connected",
                    }
                ],
            },
            timeout=30,
        )
        result["status_code"] = response.status_code
        result["response_text"] = response.text[:500]
        result["ok"] = 200 <= response.status_code < 300
        return result
    except Exception as exc:
        traceback.print_exc()
        result["error_type"] = type(exc).__name__
        result["error_message"] = str(exc)
        return result
