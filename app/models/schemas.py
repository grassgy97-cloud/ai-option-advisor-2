from __future__ import annotations

from typing import Any, Dict, List, Optional
from pydantic import BaseModel, Field
from typing import Literal

from app.models.legacy_schemas import (
    ChatRequest,
    ChatResponse,
    IntentRequest,
    IntentResponse,
    ParsedIntent,
)


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
OpportunityType = Literal[
    "directional_defined_risk",
    "directional_convexity",
    "range_income",
    "vol_rich_carry",
    "term_structure_carry",
    "covered_income",
]
StrategyFamily = Literal[
    "vertical",
    "calendar",
    "diagonal",
    "iron",
    "naked_short",
    "long_single",
    "covered_call",
]
StrategyType = Literal[
    "bear_call_spread", "bull_put_spread",
    "call_calendar",    "put_calendar",
    "diagonal_call",    "diagonal_put",
    "bull_call_spread", "bear_put_spread",
    "iron_condor",      "iron_fly",
    "long_call",        "long_put",       # 买单边（IV极低时触发）
    "naked_call",       "naked_put",      # 卖虚值单腿
    "covered_call",                       # 备兑卖出
]
OptionType = Literal["CALL", "PUT"]
ActionType = Literal["BUY", "SELL"]
ExpiryRule = Literal["nearest", "same_expiry", "next_expiry", "farther_expiry"]


class OpportunityCandidate(BaseModel):
    opportunity_type: OpportunityType
    underlying_id: str
    score: float = 0.0
    confidence: float = 0.0
    evidence: Dict[str, Any] = Field(default_factory=dict)
    rationale: Optional[str] = None
    source_flags: Dict[str, Any] = Field(default_factory=dict)
    metadata: Dict[str, Any] = Field(default_factory=dict)


class FamilyCandidate(BaseModel):
    family: StrategyFamily
    underlying_id: str
    opportunity_type: OpportunityType
    score: float = 0.0
    confidence: float = 0.0
    shortlisted: bool = True
    gating_passed: bool = True
    gating_reasons: List[str] = Field(default_factory=list)
    shortlist_reasons: List[str] = Field(default_factory=list)
    hard_constraints_applied: List[str] = Field(default_factory=list)
    soft_signals: Dict[str, float] = Field(default_factory=dict)
    rationale: Optional[str] = None
    metadata: Dict[str, Any] = Field(default_factory=dict)


class FamilyConstraintSet(BaseModel):
    allowed_families: List[StrategyFamily] = Field(default_factory=list)
    banned_families: List[StrategyFamily] = Field(default_factory=list)
    require_defined_risk: Optional[bool] = None
    require_income_family: Optional[bool] = None
    prefer_multi_leg: Optional[bool] = None
    weights: Dict[str, float] = Field(default_factory=dict)
    notes: List[str] = Field(default_factory=list)


class FamilyConstraintBundle(BaseModel):
    user_hard: FamilyConstraintSet = Field(default_factory=FamilyConstraintSet)
    user_soft: FamilyConstraintSet = Field(default_factory=FamilyConstraintSet)
    inferred_soft: FamilyConstraintSet = Field(default_factory=FamilyConstraintSet)
    machine_soft: FamilyConstraintSet = Field(default_factory=FamilyConstraintSet)


class IntentSpec(BaseModel):
    underlying_id: str = Field(..., description="主标的ID，例如 510300")
    underlying_ids: List[str] = Field(default_factory=list, description="多标的列表，为空时用underlying_id")
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
    require_positive_theta: bool = False
    prefer_income_family: bool = False
    ban_naked_short: bool = False
    prefer_directional_backup: bool = False
    prefer_neutral_structure: bool = False
    range_bias: Optional[str] = None
    market_view_strength: float = 0.5
    horizon_views: Optional[Dict[str, Any]] = None
    vol_view_detail: Optional[Dict[str, Any]] = None

    # ===== 新增：Greeks意图偏好 =====
    # 格式：{"delta": {"sign": "positive"|"negative"|"neutral", "strength": 0.0-1.0}, ...}
    # 只包含用户明确表达了偏好的Greek，未提及的不出现
    greeks_preference: Dict[str, Any] = Field(default_factory=dict)

    # ===== 新增：价格水平（相对当前价的百分比，负数=下方）=====
    # 格式：{"support": -0.12, "resistance": 0.08, "target": -0.05}
    # 只包含用户明确提到的价位
    price_levels: Dict[str, Optional[float]] = Field(default_factory=dict)

    # ===== 新增：非对称预期 =====
    # "upside" | "downside" | "symmetric" | None
    asymmetry: Optional[str] = None

    # 机器计算的市场背景，供compiler/briefing使用
    market_context_data: Dict[str, Any] = Field(default_factory=dict)

    @property
    def effective_underlying_ids(self) -> List[str]:
        """返回实际要跑的标的列表"""
        if self.underlying_ids:
            return self.underlying_ids
        return [self.underlying_id]


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

    # 为 calendar / diagonal 预留分腿约束
    leg_constraints: Optional[LegConstraint] = None

    # ===== 新增：用户指定的strike百分比目标（相对spot，负数=下方）=====
    # 有值时resolver优先按此选腿，忽略delta_target
    strike_pct_target: Optional[float] = None

    # ===== 新增：是否由用户指定strike =====
    # True时ranker跳过该腿的delta评分
    strike_forced: bool = False


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

    # ===== 新增：是否由用户指定strike，True时ranker跳过delta评分 =====
    strike_forced: bool = False


class ResolvedStrategy(BaseModel):
    strategy_type: StrategyType
    underlying_id: str
    spot_price: float
    legs: List[ResolvedLeg]
    net_premium: float
    net_credit: Optional[float] = None
    net_debit: Optional[float] = None
    score: Optional[float] = None
    # Ranking explainability is mostly numeric, but some additive fields such as
    # iv_side_used / iv_expression are categorical labels.
    score_breakdown: Dict[str, Any] = Field(default_factory=dict)
    rationale: Optional[str] = None
    metadata: Dict[str, Any] = Field(default_factory=dict)
    execution_guidance: Dict[str, Any] = Field(default_factory=dict)


class PositionLegInput(BaseModel):
    contract_id: Optional[str] = None
    side: ActionType
    option_type: OptionType
    strike: float
    expiry: str
    entry_price: Optional[float] = None
    quantity: int = 1


class PositionMonitorRequest(BaseModel):
    position_id: str
    underlying_id: str
    strategy_type: StrategyType
    opened_at: Optional[str] = None
    entry_credit_or_debit: Optional[float] = None
    pricing_type: Optional[Literal["credit", "debit"]] = None
    quantity: int = 1
    legs: List[PositionLegInput]


class PositionMonitorResponse(BaseModel):
    position_id: str
    underlying_id: str
    strategy_type: StrategyType
    monitoring_summary: Dict[str, Any]
    current_legs: List[Dict[str, Any]] = Field(default_factory=list)
    llm_commentary: Optional[Dict[str, Any]] = None
    monitoring_llm_commentary: Optional[Dict[str, Any]] = None


class PositionLegUpsertRequest(BaseModel):
    underlying_id: Optional[str] = None
    contract_id: str
    option_type: Optional[OptionType] = None
    strike: Optional[float] = None
    expiry_date: Optional[str] = None
    side: ActionType
    quantity: int
    avg_entry_price: float
    strategy_bucket: Optional[str] = None
    group_id: Optional[str] = None
    tag: Optional[str] = None
    include_in_portfolio_greeks: bool = True
    note: Optional[str] = None
    fee_rmb: float = 0.0
    reason: Optional[str] = None


class PositionLegRecord(BaseModel):
    leg_id: int
    underlying_id: str
    contract_id: str
    option_type: str
    strike: float
    expiry_date: str
    side: str
    quantity: int
    avg_entry_price: float
    strategy_bucket: Optional[str] = None
    group_id: Optional[str] = None
    tag: Optional[str] = None
    include_in_portfolio_greeks: bool = True
    status: str
    opened_at: Optional[str] = None
    updated_at: Optional[str] = None
    note: Optional[str] = None


class PositionLegUpsertResponse(BaseModel):
    leg: PositionLegRecord
    trade: Dict[str, Any]


class UnderlyingMonitorResponse(BaseModel):
    underlying_id: str
    monitoring_summary: Dict[str, Any]
    monitored_legs: List[Dict[str, Any]] = Field(default_factory=list)
    risk_contributors: List[Dict[str, Any]] = Field(default_factory=list)
    hedge_suggestions: List[Dict[str, Any]] = Field(default_factory=list)
    llm_commentary: Optional[Dict[str, Any]] = None
    monitoring_llm_commentary: Optional[Dict[str, Any]] = None


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
    underlying_ids: Optional[List[str]] = None


class AdvisorRunResponse(BaseModel):
    parsed_intent: IntentSpec
    candidate_strategies: List[StrategySpec]
    resolved_candidates: List[ResolvedStrategy]
    backtest_result: Dict[str, Any] = Field(default_factory=dict)
    calendar_recommendations: List[CalendarRecommendation] = Field(default_factory=list)
    decision_payload: Optional[Dict[str, Any]] = None
    briefing: Optional[Dict[str, Any]] = None
    briefing_llm_commentary: Optional[Dict[str, Any]] = None
