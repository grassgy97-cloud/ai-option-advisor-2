from __future__ import annotations

from functools import lru_cache

import anthropic

from app.core.config import ANTHROPIC_API_KEY


DEFAULT_ANTHROPIC_MODEL = "claude-haiku-4-5-20251001"


@lru_cache(maxsize=1)
def get_anthropic_client() -> anthropic.Anthropic:
    return anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
