"""Trade recommendation engine.

Generates actionable trade suggestions based on current risk state,
position attribution, regime detection, and throttle status.
"""

from __future__ import annotations

import logging
from datetime import datetime

from src.models import (
    PortfolioRecommendation,
    PositionAttribution,
    TradeAction,
)

logger = logging.getLogger(__name__)

# Hedge candidates by factor
HEDGE_MAP = {
    "market": {"symbol": "SH", "description": "Short S&P 500"},
    "ai_tech": {"symbol": "SOXS", "description": "Inverse semiconductors"},
    "rates": {"symbol": "TBF", "description": "Short 20+ Year Treasury"},
    "credit": {"symbol": "SJB", "description": "Short high-yield bonds"},
    "momentum": {"symbol": "USMV", "description": "Low volatility ETF"},
    "dollar": {"symbol": "FXE", "description": "Euro currency trust"},
    "quality": {"symbol": "VTV", "description": "Value factor (quality offset)"},
    "low_vol": {"symbol": "MTUM", "description": "Momentum factor"},
    "value": {"symbol": "QUAL", "description": "Quality factor"},
}


class TradeRecommendationEngine:
    """Generate actionable trade recommendations based on current risk state."""

    def __init__(
        self,
        max_single_reduction: float = 0.10,
        min_action_threshold: float = 0.10,
    ):
        self.max_single_reduction = max_single_reduction
        self.min_action_threshold = min_action_threshold

    def generate_recommendations(
        self,
        positions: list,
        attribution: list[PositionAttribution],
        heat_score: float,
        regime_state: dict | None = None,
        throttle_state: dict | None = None,
        cluster_state=None,
        crowding_state=None,
        hrp_weights: dict[str, float] | None = None,
    ) -> PortfolioRecommendation:
        """
        Generate specific, actionable recommendations.

        Args:
            positions: Current Position objects.
            attribution: Per-position heat attribution.
            heat_score: Current composite heat score.
            regime_state: Market regime dict.
            throttle_state: Risk throttle dict.
            cluster_state: Cluster state object.
            crowding_state: Crowding state object.
            hrp_weights: HRP optimal weights for rebalance targets.

        Returns:
            PortfolioRecommendation with prioritized actions and hedges.
        """
        regime = regime_state or {}
        throttle = throttle_state or {}
        regime_label = regime.get("regime", "normal") if isinstance(regime, dict) else "normal"
        target_reduction = throttle.get("target_reduction", 0.0) if isinstance(throttle, dict) else 0.0

        actions: list[TradeAction] = []
        hedges: list[TradeAction] = []
        total_heat_reduction = 0.0

        # Determine urgency based on heat level
        if heat_score >= 0.93:
            base_urgency = "immediate"
        elif heat_score >= 0.85:
            base_urgency = "today"
        elif heat_score >= 0.75:
            base_urgency = "this_week"
        else:
            base_urgency = "optional"

        weights_dict = {p.symbol: p.weight for p in positions}
        hrp = hrp_weights or {}

        # 1. Position sizing adjustments from attribution
        priority = 1
        for attr in attribution:
            if attr.recommendation == "reduce":
                current_w = attr.weight
                # Target: reduce by proportional to heat_share, capped
                reduction = min(
                    current_w * attr.heat_share * 2,
                    self.max_single_reduction,
                    current_w * 0.5,  # Never reduce more than half
                )
                target_w = max(current_w - reduction, 0.01)

                # If HRP weights available, use as floor
                if attr.symbol in hrp:
                    target_w = max(target_w, hrp[attr.symbol])

                delta = target_w - current_w
                impact = abs(delta) * heat_score * 10000  # bps estimate

                actions.append(TradeAction(
                    action="reduce",
                    symbol=attr.symbol,
                    current_weight=current_w,
                    target_weight=round(target_w, 4),
                    delta=round(delta, 4),
                    impact_estimate=round(impact, 1),
                    priority=priority,
                    reason=attr.recommendation_reason,
                    urgency=base_urgency,
                ))
                total_heat_reduction += impact
                priority += 1

            elif attr.recommendation == "hedge":
                # Suggest a hedge instrument for the dominant factor
                hedge_info = HEDGE_MAP.get(attr.dominant_factor, {})
                if hedge_info:
                    hedge_weight = min(attr.weight * 0.3, 0.05)
                    impact = hedge_weight * heat_score * 5000

                    hedges.append(TradeAction(
                        action="hedge",
                        symbol=hedge_info["symbol"],
                        current_weight=0.0,
                        target_weight=round(hedge_weight, 4),
                        delta=round(hedge_weight, 4),
                        impact_estimate=round(impact, 1),
                        priority=priority,
                        reason=f"Hedge {attr.symbol}'s {attr.dominant_factor} exposure via {hedge_info['description']}",
                        urgency="this_week" if base_urgency in ("immediate", "today") else "optional",
                    ))
                    total_heat_reduction += impact
                    priority += 1

        # 2. HRP rebalance suggestions (if heat is elevated and HRP differs significantly)
        if heat_score >= 0.55 and hrp:
            for sym, hrp_w in hrp.items():
                current_w = weights_dict.get(sym, 0.0)
                diff = hrp_w - current_w
                # Only suggest if deviation > 3%
                if abs(diff) > 0.03 and diff < 0:
                    # Already covered by reduce actions above?
                    already_covered = any(a.symbol == sym for a in actions)
                    if not already_covered:
                        impact = abs(diff) * heat_score * 5000
                        actions.append(TradeAction(
                            action="reduce" if diff < 0 else "increase",
                            symbol=sym,
                            current_weight=round(current_w, 4),
                            target_weight=round(hrp_w, 4),
                            delta=round(diff, 4),
                            impact_estimate=round(impact, 1),
                            priority=priority,
                            reason=f"Rebalance toward HRP optimal weight ({hrp_w:.1%} vs current {current_w:.1%})",
                            urgency="this_week",
                        ))
                        total_heat_reduction += impact
                        priority += 1

        # Sort by priority
        actions.sort(key=lambda a: a.priority)
        hedges.sort(key=lambda a: a.priority)

        # Estimate post-action heat
        estimated_after = max(
            heat_score - total_heat_reduction / 10000,
            heat_score * 0.6,  # Floor: can't reduce more than 40%
        )

        # Summary
        n_actions = len(actions) + len(hedges)
        if n_actions == 0:
            summary = f"No actions needed. Heat {heat_score:.3f} in {regime_label} regime."
        else:
            summary = (
                f"{n_actions} actions recommended. "
                f"Heat {heat_score:.3f} -> est. {estimated_after:.3f} "
                f"({regime_label} regime)."
            )

        regime_context = (
            f"Regime: {regime_label} | "
            f"Transition risk: {regime.get('transition_risk', 0):.1%} | "
            f"Target reduction: {target_reduction:.0%}"
        )

        return PortfolioRecommendation(
            summary=summary,
            current_heat=heat_score,
            estimated_heat_after=round(estimated_after, 4),
            actions=actions,
            hedges=hedges,
            total_expected_heat_reduction=round(total_heat_reduction, 1),
            regime_context=regime_context,
            timestamp=datetime.utcnow(),
        )
