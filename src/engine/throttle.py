"""Dynamic risk throttle — Kelly sizing + pre-trade impact simulation.

Two components:

1. **Fractional Kelly with heat adjustment:**
   - Half Kelly baseline (conservative)
   - Scales down linearly as heat score rises
   - Full halt at emergency level (≥0.85)

2. **Pre-trade cluster impact simulation:**
   - "If I add this position, what happens to my Heat Score?"
   - Computes marginal factor contribution
   - Identifies which cluster the new position joins
   - Returns PROCEED / CAUTION / BLOCK recommendation

References:
- Kelly (1956): A New Interpretation of Information Rate
- Research Report v2 Stage 6: Dynamic Risk Throttle
"""

from __future__ import annotations

import logging
from copy import deepcopy
from typing import Optional

import numpy as np
import pandas as pd

from src.engine.correlation import CorrelationEngine
from src.engine.factor_model import FactorModel
from src.engine.heat_score import HeatScoreCalculator
from src.engine.clustering import ClusteringEngine
from src.models import HeatScore, Position

logger = logging.getLogger(__name__)


class RiskThrottle:
    """Dynamic position sizing based on portfolio heat."""

    def __init__(
        self,
        kelly_fraction: float = 0.5,
        heat_floor: float = 0.4,
        heat_ceiling: float = 0.85,
    ):
        """
        Args:
            kelly_fraction: Fraction of full Kelly to use (0.5 = half Kelly).
            heat_floor: Heat score below which no throttling occurs.
            heat_ceiling: Heat score at which sizing goes to zero.
        """
        self.kelly_fraction = kelly_fraction
        self.heat_floor = heat_floor
        self.heat_ceiling = heat_ceiling

    def adjusted_kelly(
        self,
        expected_return: float,
        variance: float,
        heat_score: float,
        regime: str = "normal",
    ) -> dict:
        """
        Compute heat-adjusted Kelly fraction.

        Args:
            expected_return: Expected return for the position.
            variance: Variance of position returns.
            heat_score: Current portfolio heat score (0-1).
            regime: Current market regime (low_vol/normal/crisis).

        Returns:
            Dict with kelly_raw, kelly_adjusted, multiplier, action.
        """
        if variance <= 0:
            return {
                "kelly_raw": 0.0,
                "kelly_adjusted": 0.0,
                "multiplier": 0.0,
                "heat_score": heat_score,
                "regime": regime,
                "action": "SKIP — zero or negative variance",
            }

        raw_kelly = (expected_return / variance) * self.kelly_fraction

        # Regime adjustment
        regime_mult = self._regime_multiplier(regime)

        # Heat adjustment
        if heat_score < self.heat_floor:
            heat_mult = 1.0
        elif heat_score >= self.heat_ceiling:
            heat_mult = 0.0
        else:
            heat_mult = 1.0 - ((heat_score - self.heat_floor) / (self.heat_ceiling - self.heat_floor))

        multiplier = heat_mult * regime_mult
        adjusted = raw_kelly * multiplier

        # Determine action
        if heat_score >= self.heat_ceiling:
            action = "HALT — emergency heat level"
        elif heat_score >= 0.7:
            action = "REDUCE — critical heat, -50% exposure"
        elif heat_score >= 0.6:
            action = "CAUTION — hot, -25% exposure, no new correlated"
        elif heat_score >= 0.4:
            action = "TRIM — warm, -25% new position sizes"
        else:
            action = "FULL — normal trading"

        return {
            "kelly_raw": float(raw_kelly),
            "kelly_adjusted": float(adjusted),
            "multiplier": float(multiplier),
            "heat_score": heat_score,
            "regime": regime,
            "action": action,
        }

    def _regime_multiplier(self, regime: str) -> float:
        """Scale sizing based on market regime."""
        multipliers = {
            "low_vol": 1.0,
            "normal": 0.85,
            "crisis": 0.5,
            "unknown": 0.75,
        }
        return multipliers.get(regime, 0.75)

    def compute_exposure_limits(
        self, heat_score: float, current_exposure: float, regime: str = "normal"
    ) -> dict:
        """
        Compute target exposure limits given current heat and regime.

        Args:
            heat_score: Current heat score (0-1).
            current_exposure: Current total exposure (sum of absolute weights).
            regime: Market regime.

        Returns:
            Dict with max_exposure, target_reduction, new_position_allowed.
        """
        if heat_score >= self.heat_ceiling:
            max_exp = current_exposure * 0.5
            new_allowed = False
            reduction = 0.5
        elif heat_score >= 0.7:
            max_exp = current_exposure * 0.5
            new_allowed = False
            reduction = 0.5
        elif heat_score >= 0.6:
            max_exp = current_exposure * 0.75
            new_allowed = False  # No new correlated positions
            reduction = 0.25
        elif heat_score >= 0.4:
            max_exp = current_exposure * 0.75
            new_allowed = True
            reduction = 0.25
        else:
            max_exp = current_exposure
            new_allowed = True
            reduction = 0.0

        # Regime override
        if regime == "crisis":
            max_exp *= 0.75
            reduction = max(reduction, 0.25)

        return {
            "max_exposure": float(max_exp),
            "current_exposure": float(current_exposure),
            "target_reduction": float(reduction),
            "new_position_allowed": new_allowed,
            "regime": regime,
            "heat_score": heat_score,
        }


class PreTradeSimulator:
    """Simulate the impact of adding a position on portfolio risk."""

    def __init__(
        self,
        factor_model: FactorModel,
        heat_calculator: HeatScoreCalculator,
        corr_engine: CorrelationEngine,
        cluster_engine: ClusteringEngine,
    ):
        self.factor_model = factor_model
        self.heat_calculator = heat_calculator
        self.corr_engine = corr_engine
        self.cluster_engine = cluster_engine

    def simulate_impact(
        self,
        candidate_symbol: str,
        candidate_weight: float,
        current_positions: list[Position],
        returns: pd.DataFrame,
        factor_returns: pd.DataFrame,
        current_heat: Optional[HeatScore] = None,
    ) -> dict:
        """
        Simulate the impact of adding a new position.

        Args:
            candidate_symbol: Symbol to add.
            candidate_weight: Weight for the new position (0-1).
            current_positions: Current portfolio positions.
            returns: Full returns DataFrame (must include candidate_symbol).
            factor_returns: Factor ETF returns.
            current_heat: Current heat score (if available, avoids recomputation).

        Returns:
            Dict with heat_before, heat_after, heat_delta, factor_impact,
            cluster_impact, recommendation.
        """
        if candidate_symbol not in returns.columns:
            return {
                "error": f"No return data for {candidate_symbol}",
                "recommendation": "SKIP",
            }

        # Current portfolio symbols
        current_symbols = [p.symbol for p in current_positions]
        position_cols = [s for s in current_symbols if s in returns.columns]

        if not position_cols:
            return {
                "error": "No current position data available",
                "recommendation": "PROCEED",
            }

        # ── Compute BEFORE heat ──
        pos_returns_before = returns[position_cols]
        weights_before = self._get_weights(current_positions, position_cols)

        if current_heat is not None:
            heat_before = current_heat.score
        else:
            heat_before = self._compute_heat_score(
                pos_returns_before, weights_before, factor_returns, current_positions
            )

        # ── Compute AFTER heat (with candidate added) ──
        sim_positions = list(current_positions) + [
            Position(symbol=candidate_symbol, weight=candidate_weight)
        ]
        sim_cols = position_cols + [candidate_symbol]
        sim_cols = list(dict.fromkeys(sim_cols))  # deduplicate preserving order
        pos_returns_after = returns[[c for c in sim_cols if c in returns.columns]]
        weights_after = self._get_weights(sim_positions, list(pos_returns_after.columns))

        heat_after = self._compute_heat_score(
            pos_returns_after, weights_after, factor_returns, sim_positions
        )

        heat_delta = heat_after - heat_before

        # ── Factor impact ──
        factor_impact = self._compute_factor_impact(
            candidate_symbol, candidate_weight, returns, factor_returns
        )

        # ── Cluster impact ──
        cluster_impact = self._compute_cluster_impact(
            candidate_symbol, pos_returns_after
        )

        # ── Recommendation ──
        if heat_after >= 0.85:
            recommendation = "BLOCK"
            reason = f"Would push heat to {heat_after:.2f} (emergency)"
        elif heat_after >= 0.7:
            recommendation = "BLOCK"
            reason = f"Would push heat to {heat_after:.2f} (critical)"
        elif heat_after >= 0.6:
            recommendation = "CAUTION"
            reason = f"Would push heat to {heat_after:.2f} (hot)"
        elif heat_delta > 0.1:
            recommendation = "CAUTION"
            reason = f"Significant heat increase: +{heat_delta:.3f}"
        elif heat_delta > 0.05:
            recommendation = "PROCEED_WITH_CARE"
            reason = f"Moderate heat increase: +{heat_delta:.3f}"
        else:
            recommendation = "PROCEED"
            reason = f"Minimal impact: {heat_delta:+.3f}"

        return {
            "candidate": candidate_symbol,
            "candidate_weight": candidate_weight,
            "heat_before": float(heat_before),
            "heat_after": float(heat_after),
            "heat_delta": float(heat_delta),
            "factor_impact": factor_impact,
            "cluster_impact": cluster_impact,
            "recommendation": recommendation,
            "reason": reason,
        }

    def _get_weights(self, positions: list[Position], symbols: list[str]) -> np.ndarray:
        """Extract weight array aligned with symbols."""
        weight_map = {p.symbol: p.weight for p in positions}
        return np.array([weight_map.get(s, 0.0) for s in symbols])

    def _compute_heat_score(
        self,
        position_returns: pd.DataFrame,
        weights: np.ndarray,
        factor_returns: pd.DataFrame,
        positions: list[Position],
    ) -> float:
        """Compute heat score for a given portfolio configuration."""
        try:
            # Factor exposures
            weights_dict = {p.symbol: p.weight for p in positions}
            exposures = self.factor_model.estimate_exposures(position_returns, factor_returns)
            summaries = self.factor_model.aggregate_exposures(exposures, weights_dict)
            factor_exp_dict = {s.factor_name: s.total_exposure for s in summaries}

            # Correlation
            corr_matrix = self.corr_engine.compute_correlation_matrix(position_returns)
            avg_corr = self.corr_engine.compute_avg_correlation(corr_matrix)

            dominant = self.factor_model.get_dominant_factor(summaries)
            sym_a, sym_b, corr_val = self.corr_engine.get_top_correlated_pair(corr_matrix)
            top_pair = f"{sym_a}↔{sym_b}" if sym_a else ""

            # Heat score (using a fresh calculator to avoid polluting history)
            calc = HeatScoreCalculator()
            heat = calc.compute(
                returns=position_returns,
                portfolio_weights=weights,
                factor_exposures=factor_exp_dict,
                avg_correlation=avg_corr,
                dominant_factor=dominant,
                top_correlated_pair=top_pair,
            )
            return heat.score

        except Exception as e:
            logger.warning("Heat score computation failed in simulation: %s", e)
            return 0.5  # Neutral on failure

    def _compute_factor_impact(
        self,
        candidate_symbol: str,
        candidate_weight: float,
        returns: pd.DataFrame,
        factor_returns: pd.DataFrame,
    ) -> dict:
        """Compute factor exposure of the candidate position."""
        if candidate_symbol not in returns.columns:
            return {"error": "No data"}

        try:
            candidate_returns = returns[[candidate_symbol]]
            exposures = self.factor_model.estimate_exposures(candidate_returns, factor_returns)

            factor_betas = {}
            for exp in exposures:
                factor_betas[exp.factor_name] = {
                    "beta": round(exp.beta, 4),
                    "t_stat": round(exp.t_stat, 2),
                    "weighted_contribution": round(exp.beta * candidate_weight, 6),
                }

            # Find dominant factor
            if factor_betas:
                dominant = max(factor_betas, key=lambda k: abs(factor_betas[k]["beta"]))
            else:
                dominant = "none"

            return {
                "factor_betas": factor_betas,
                "dominant_factor": dominant,
            }

        except Exception as e:
            logger.warning("Factor impact computation failed: %s", e)
            return {"error": str(e)}

    def _compute_cluster_impact(
        self,
        candidate_symbol: str,
        sim_returns: pd.DataFrame,
    ) -> dict:
        """Determine which cluster the candidate would join."""
        try:
            corr_matrix = sim_returns.corr()

            # Find highest correlated existing position
            if candidate_symbol in corr_matrix.columns:
                candidate_corrs = corr_matrix[candidate_symbol].drop(candidate_symbol)
                if not candidate_corrs.empty:
                    most_correlated = candidate_corrs.abs().idxmax()
                    corr_value = candidate_corrs[most_correlated]

                    # Check current communities
                    communities = self.cluster_engine.last_communities
                    joins_cluster = communities.get(most_correlated, -1)
                    cluster_peers = [
                        s for s, c in communities.items()
                        if c == joins_cluster and s != candidate_symbol
                    ]

                    return {
                        "most_correlated_with": most_correlated,
                        "correlation": round(float(corr_value), 4),
                        "joins_cluster": int(joins_cluster),
                        "cluster_peers": cluster_peers,
                        "increases_concentration": bool(corr_value > 0.5),
                    }

            return {"cluster": "new", "peers": []}

        except Exception as e:
            logger.warning("Cluster impact computation failed: %s", e)
            return {"error": str(e)}
