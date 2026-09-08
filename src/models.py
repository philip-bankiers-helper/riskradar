"""Pydantic models for Risk Radar."""

from __future__ import annotations

from datetime import datetime
from enum import Enum

from pydantic import BaseModel, Field


class HeatLevel(str, Enum):
    """Heat score threshold classification."""
    COOL = "cool"           # below 0.4636 (50% of calibration days)
    WARM = "warm"           # 0.4636 to 0.6732 (25%)
    HOT = "hot"             # 0.6732 to 0.8029 (15%)
    CRITICAL = "critical"   # 0.8029 to 0.8781 (7%)
    EMERGENCY = "emergency" # 0.8781 and above (3%)


class Position(BaseModel):
    """A portfolio position."""
    symbol: str
    weight: float = Field(ge=0, le=1, description="Portfolio weight 0-1")
    shares: float = 0.0
    entry_price: float | None = None
    entry_date: datetime | None = None


class FactorExposure(BaseModel):
    """Factor beta for a single position."""
    symbol: str
    factor_name: str
    beta: float
    t_stat: float = 0.0
    r_squared: float = 0.0


class FactorExposureSummary(BaseModel):
    """Aggregated factor exposure across portfolio."""
    factor_name: str
    factor_symbol: str
    total_exposure: float  # Σ(weight * beta)
    exposure_share: float  # % of total factor risk


class CorrelationEntry(BaseModel):
    """Single pairwise correlation."""
    symbol_a: str
    symbol_b: str
    correlation: float


class HeatScoreComponents(BaseModel):
    """Individual heat score components."""
    absorption_ratio: float = Field(ge=0, le=1)
    turbulence: float = Field(ge=0)
    turbulence_percentile: float = Field(ge=0, le=1)
    diversification_ratio: float = Field(ge=0)
    diversification_percentile: float = Field(ge=0, le=1)
    factor_hhi: float = Field(ge=0, le=1)
    factor_hhi_percentile: float = Field(ge=0, le=1)
    avg_correlation: float = Field(ge=-1, le=1)
    avg_correlation_percentile: float = Field(ge=0, le=1)


class HeatScore(BaseModel):
    """Composite portfolio heat score."""
    score: float = Field(ge=0, le=1)
    level: HeatLevel
    components: HeatScoreComponents
    timestamp: datetime = Field(default_factory=datetime.utcnow)
    dominant_factor: str = ""
    top_correlated_pair: str = ""
    action: str = ""


class AlertMessage(BaseModel):
    """Alert to send when heat threshold is crossed."""
    heat_score: HeatScore
    previous_level: HeatLevel | None = None
    message: str = ""
    timestamp: datetime = Field(default_factory=datetime.utcnow)


class ClusterInfo(BaseModel):
    """Community/cluster information for a group of assets."""
    cluster_id: int
    members: list[str]
    size: int


class ClusterMigration(BaseModel):
    """A cluster migration event — asset moved between communities."""
    symbol: str
    from_peers: list[str]
    to_peers: list[str]
    overlap: float = 0.0
    detected_at: str = ""
    compared_to: str = ""


class MSTEdge(BaseModel):
    """Minimum Spanning Tree edge."""
    source: str
    target: str
    distance: float
    correlation: float


class ClusterState(BaseModel):
    """Full clustering state snapshot."""
    communities: list[ClusterInfo] = Field(default_factory=list)
    n_communities: int = 0
    modularity: float = 0.0
    migrations: list[ClusterMigration] = Field(default_factory=list)
    migration_risk: float = 0.0
    mst_edges: list[MSTEdge] = Field(default_factory=list)
    hrp_weights: dict[str, float] = Field(default_factory=dict)
    hrp_concentration: float = 0.0
    dcc_stress_indicator: float = 0.0
    timestamp: datetime = Field(default_factory=datetime.utcnow)


class RegimeState(BaseModel):
    """Current market regime information."""
    regime: str = "unknown"
    regime_id: int = -1
    probabilities: dict[str, float] = Field(default_factory=dict)
    confidence: float = 0.0
    transition_risk: float = 0.0
    is_fitted: bool = False


class ThrottleState(BaseModel):
    """Current risk throttle status."""
    max_exposure: float = 1.0
    current_exposure: float = 1.0
    target_reduction: float = 0.0
    new_position_allowed: bool = True
    regime: str = "normal"
    heat_score: float = 0.0


class PreTradeResult(BaseModel):
    """Result of pre-trade impact simulation."""
    candidate: str
    candidate_weight: float = 0.0
    heat_before: float = 0.0
    heat_after: float = 0.0
    heat_delta: float = 0.0
    factor_impact: dict = Field(default_factory=dict)
    cluster_impact: dict = Field(default_factory=dict)
    recommendation: str = "PROCEED"
    reason: str = ""


class PositionAttribution(BaseModel):
    """Per-position heat attribution."""
    symbol: str
    weight: float
    marginal_heat: float  # Heat change if position removed
    correlation_contribution: float  # Contribution to avg correlation
    factor_concentration: float  # Contribution to factor HHI
    risk_contribution: float  # Euler risk decomposition share
    heat_share: float  # % of total heat from this position
    dominant_factor: str  # This position's most significant factor
    dominant_factor_beta: float  # Beta to dominant factor
    recommendation: str  # "hold", "reduce", "hedge", "monitor"
    recommendation_reason: str  # Human readable reason


class TradeAction(BaseModel):
    """A single recommended trade action."""
    action: str  # "reduce", "increase", "add", "remove", "hedge"
    symbol: str
    current_weight: float
    target_weight: float
    delta: float  # target - current
    impact_estimate: float  # Expected heat reduction in bps
    priority: int  # 1 = highest
    reason: str  # Human-readable explanation
    urgency: str  # "immediate", "today", "this_week", "optional"


class PortfolioRecommendation(BaseModel):
    """Full portfolio recommendation package."""
    summary: str  # One-line summary
    current_heat: float
    estimated_heat_after: float
    actions: list[TradeAction] = Field(default_factory=list)
    hedges: list[TradeAction] = Field(default_factory=list)
    total_expected_heat_reduction: float = 0.0
    regime_context: str = ""
    timestamp: datetime = Field(default_factory=datetime.utcnow)


class BenchmarkResult(BaseModel):
    """Result of comparing one signal against drawdowns."""
    signal_name: str
    precision: float = 0.0
    recall: float = 0.0
    f1_score: float = 0.0
    lead_time_days: float = 0.0
    false_positive_rate: float = 0.0
    strategy_return: float = 0.0
    buy_hold_return: float = 0.0
    max_drawdown_avoided: float = 0.0
    sharpe_ratio: float = 0.0


class BenchmarkReport(BaseModel):
    """Full benchmark comparison report."""
    results: list[BenchmarkResult] = Field(default_factory=list)
    best_signal: str = "N/A"
    heat_vs_vix_summary: str = ""
    timestamp: datetime = Field(default_factory=datetime.utcnow)


class PortfolioState(BaseModel):
    """Full portfolio state snapshot."""
    positions: list[Position]
    factor_exposures: list[FactorExposureSummary]
    heat_score: HeatScore | None = None
    cluster_state: ClusterState | None = None
    regime_state: RegimeState | None = None
    throttle_state: ThrottleState | None = None
    correlation_avg: float = 0.0
    timestamp: datetime = Field(default_factory=datetime.utcnow)
