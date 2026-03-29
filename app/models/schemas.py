from __future__ import annotations

from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, Field
from typing import Literal


# ===== 新系统 schema =====

MarketView = Literal["bullish", "bearish", "neutral"]
VolView = Literal[
    "none",
    "iv_high",
    "iv_low",
    "call_iv_rich",
    "put_iv_rich",
    "term_front_high",
    "term_back_high",
]
RiskPreference = Literal["low", "medium", "high"]
StrategyType = Literal[
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
]
OptionType = Literal["CALL", "PUT"]
ActionType = Literal["BUY", "SELL"]
ExpiryRule = Literal["nearest", "same_expiry", "next_expiry", "farther_expiry"]


class IntentSpec(BaseModel):
    underlying_id: str = Field(..., description="标的ID，例如 510300")
    market_view: MarketView = "neutral"
    vol_view: VolView = "none"
    risk_preference: RiskPreference = "low"

    defined_risk_only: bool = True
    prefer_multi_leg: bool = True

    dte_min: int = 20
    dte_max: int = 45

    max_rel_spread: float = 0.03
    min_quote_size: int = 1

    allowed_strategies: Optional[List[StrategyType]] = None
    banned_strategies: List[str] = Field(default_factory=list)

    raw_text: Optional[str] = None


class StrategyConstraint(BaseModel):
    defined_risk_only: bool = True
    dte_min: int = 20
    dte_max: int = 45
    max_rel_spread: float = 0.03
    min_quote_size: int = 1


class LegConstraint(BaseModel):
    dte_min: Optional[int] = None
    dte_max: Optional[int] = None
    max_rel_spread: Optional[float] = None
    min_quote_size: Optional[int] = None


class StrategyLegSpec(BaseModel):
    action: ActionType
    option_type: OptionType
    expiry_rule: ExpiryRule = "nearest"
    strike: Optional[float] = None
    delta_target: Optional[float] = None
    quantity: int = 1

    # 新增：为 calendar / diagonal 预留分腿约束
    leg_constraints: Optional[LegConstraint] = None


class StrategySpec(BaseModel):
    strategy_type: StrategyType
    underlying_id: str
    legs: List[StrategyLegSpec]
    constraints: StrategyConstraint
    rationale: Optional[str] = None

    # metadata 继续保留，适合放编译器额外信息
    # 比如 near/far dte 范围、calendar 偏好参数等
    metadata: Dict[str, Any] = Field(default_factory=dict)


class ExitRules(BaseModel):
    take_profit_pct: Optional[float] = 0.5
    stop_loss_pct: Optional[float] = 1.5
    max_holding_days: Optional[int] = 10


class SampleWindow(BaseModel):
    start_date: str
    end_date: str


class BacktestRequest(BaseModel):
    strategy_type: StrategyType
    entry_rules: Dict[str, Any] = Field(default_factory=dict)
    exit_rules: ExitRules = Field(default_factory=ExitRules)
    sample_window: SampleWindow


class ResolvedLeg(BaseModel):
    contract_id: str
    action: ActionType
    option_type: OptionType
    expiry_date: str
    strike: float
    bid: float
    ask: float
    mid: float
    delta: Optional[float] = None
    gamma: Optional[float] = None
    theta: Optional[float] = None
    vega: Optional[float] = None
    iv: Optional[float] = None
    dte: Optional[int] = None
    quantity: int = 1


class ResolvedStrategy(BaseModel):
    strategy_type: StrategyType
    underlying_id: str
    spot_price: float
    legs: List[ResolvedLeg]
    net_premium: float
    net_credit: Optional[float] = None
    net_debit: Optional[float] = None
    score: Optional[float] = None
    score_breakdown: Dict[str, float] = Field(default_factory=dict)
    rationale: Optional[str] = None
    metadata: Dict[str, Any] = Field(default_factory=dict)


class ScanCandidate(BaseModel):
    strategy: ResolvedStrategy
    signal_strength: float = 0.0
    liquidity_score: float = 0.0
    cost_score: float = 0.0
    total_score: float = 0.0


class CalendarRecommendation(BaseModel):
    strategy_type: str = "calendar_spread"
    underlying_id: str
    option_type: OptionType

    strike: float
    moneyness: Optional[float] = None

    near_expiry: str
    far_expiry: str

    near_contract_id: str
    far_contract_id: str

    net_debit: Optional[float] = None
    iv_diff: Optional[float] = None
    total_score: Optional[float] = None

    reason: Optional[str] = None


class AdvisorRunRequest(BaseModel):
    text: str
    underlying_id: Optional[str] = "510300"


class AdvisorRunResponse(BaseModel):
    parsed_intent: IntentSpec
    candidate_strategies: List[StrategySpec]
    resolved_candidates: List[ResolvedStrategy]
    backtest_result: Dict[str, Any] = Field(default_factory=dict)
    calendar_recommendations: List[CalendarRecommendation] = Field(default_factory=list)


# ===== 兼容旧系统 schema =====

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