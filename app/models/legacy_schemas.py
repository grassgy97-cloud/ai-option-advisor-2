from __future__ import annotations

from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field


class ParsedIntent(BaseModel):
    underlying_id: str = "510300"
    raw_text: Optional[str] = None

    market_view: str = "neutral"
    vol_view: str = "none"
    direction_bias: str = "neutral"

    holding_period_days: Optional[int] = 20
    risk_preference: str = "low"

    defined_risk_only: bool = True
    prefer_multi_leg: bool = True
    allow_single_leg: bool = False

    strategy_whitelist: List[str] = Field(default_factory=list)
    strategy_blacklist: List[str] = Field(default_factory=list)

    target_greeks_json: Dict[str, Any] = Field(default_factory=dict)
    scenario_filters_json: Dict[str, Any] = Field(default_factory=dict)

    status: str = "parsed"

    dte_min: Optional[int] = 20
    dte_max: Optional[int] = 45
    max_rel_spread: Optional[float] = 0.03
    min_quote_size: Optional[int] = 1


class ChatRequest(BaseModel):
    text: str
    underlying_id: Optional[str] = "510300"


class ChatResponse(BaseModel):
    reply: str
    parsed_intent: Optional[dict] = None
    data: Optional[dict] = None


class IntentRequest(BaseModel):
    text: str
    underlying_id: Optional[str] = "510300"


class IntentResponse(BaseModel):
    parsed_intent: dict
