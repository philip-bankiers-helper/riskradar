"""Position-level heat attribution engine.

Decomposes portfolio heat score into per-position contributions using:
- Marginal heat: leave-one-out heat change
- Correlation contribution: position's share of average pairwise correlation
- Factor concentration: position's contribution to factor HHI
- Risk contribution: Euler risk decomposition (marginal variance contribution)
"""

from __future__ import annotations

import logging

import numpy as np
import pandas as pd

from src.models import PositionAttribution

logger = logging.getLogger(__name__)


class PositionAttributor:
    """Decompose portfolio heat score into per-position contributions."""

    def compute_attribution(
        self,
        positions: list,
        returns: pd.DataFrame,
        factor_exposures: dict[str, dict[str, float]],
        correlation_matrix: pd.DataFrame,
        heat_score: float,
        avg_correlation: float,
    ) -> list[PositionAttribution]:
        """
        For each position, compute its contribution to portfolio heat.

        Args:
            positions: List of Position objects.
            returns: Historical returns DataFrame (cols=symbols).
            factor_exposures: Dict of symbol -> {factor_name: beta}.
            correlation_matrix: Pairwise correlation matrix.
            heat_score: Current composite heat score.
            avg_correlation: Current average pairwise correlation.

        Returns:
            List of PositionAttribution sorted by heat_share descending.
        """
        if not positions or returns.empty:
            return []

        symbols = [p.symbol for p in positions]
        weights = {p.symbol: p.weight for p in positions}
        n = len(symbols)

        if n == 0:
            return []

        # Compute component contributions
        corr_contributions = self._correlation_contributions(
            symbols, weights, correlation_matrix
        )
        factor_contributions = self._factor_concentration_contributions(
            symbols, weights, factor_exposures
        )
        risk_contributions = self._euler_risk_decomposition(
            symbols, weights, returns
        )

        # Marginal heat is expensive (leave-one-out), approximate with
        # weighted combination of the three component contributions
        attributions = []
        total_raw = 0.0

        for sym in symbols:
            w = weights.get(sym, 0.0)
            corr_c = corr_contributions.get(sym, 0.0)
            factor_c = factor_contributions.get(sym, 0.0)
            risk_c = risk_contributions.get(sym, 0.0)

            # Composite attribution: weighted blend of contributions
            raw_share = 0.35 * corr_c + 0.30 * factor_c + 0.35 * risk_c
            total_raw += raw_share

            # Find dominant factor for this position
            pos_factors = factor_exposures.get(sym, {})
            dominant_factor = ""
            dominant_beta = 0.0
            if pos_factors:
                dominant_factor = max(pos_factors, key=lambda f: abs(pos_factors[f]))
                dominant_beta = pos_factors[dominant_factor]

            attributions.append({
                "symbol": sym,
                "weight": w,
                "corr_c": corr_c,
                "factor_c": factor_c,
                "risk_c": risk_c,
                "raw_share": raw_share,
                "dominant_factor": dominant_factor,
                "dominant_factor_beta": dominant_beta,
            })

        # Normalize shares to sum to 1.0
        if total_raw > 0:
            for a in attributions:
                a["heat_share"] = a["raw_share"] / total_raw
        else:
            equal_share = 1.0 / max(n, 1)
            for a in attributions:
                a["heat_share"] = equal_share

        # Compute marginal heat (approximation: heat_share * total_heat)
        for a in attributions:
            a["marginal_heat"] = a["heat_share"] * heat_score

        # Generate recommendations
        results = []
        for a in attributions:
            rec, reason = self._recommend(
                a["heat_share"], a["corr_c"], a["factor_c"],
                a["risk_c"], a["weight"], heat_score,
            )
            results.append(PositionAttribution(
                symbol=a["symbol"],
                weight=a["weight"],
                marginal_heat=float(a["marginal_heat"]),
                correlation_contribution=float(a["corr_c"]),
                factor_concentration=float(a["factor_c"]),
                risk_contribution=float(a["risk_c"]),
                heat_share=float(a["heat_share"]),
                dominant_factor=a["dominant_factor"],
                dominant_factor_beta=float(a["dominant_factor_beta"]),
                recommendation=rec,
                recommendation_reason=reason,
            ))

        # Sort by heat_share descending
        results.sort(key=lambda x: x.heat_share, reverse=True)
        return results

    def _correlation_contributions(
        self,
        symbols: list[str],
        weights: dict[str, float],
        correlation_matrix: pd.DataFrame,
    ) -> dict[str, float]:
        """Compute each position's contribution to average correlation."""
        contributions: dict[str, float] = {}
        n = len(symbols)
        if n < 2:
            return {s: 1.0 for s in symbols}

        available = [s for s in symbols if s in correlation_matrix.columns]
        if len(available) < 2:
            return {s: 1.0 / n for s in symbols}

        total = 0.0
        for sym in available:
            w = weights.get(sym, 0.0)
            # Sum of weight-adjusted correlations with other positions
            corr_sum = 0.0
            for other in available:
                if other != sym:
                    c = correlation_matrix.loc[sym, other] if sym in correlation_matrix.index else 0.0
                    w_other = weights.get(other, 0.0)
                    corr_sum += abs(float(c)) * w * w_other
            contributions[sym] = corr_sum
            total += corr_sum

        # Normalize
        if total > 0:
            for sym in contributions:
                contributions[sym] /= total

        # Include symbols not in corr matrix
        for sym in symbols:
            if sym not in contributions:
                contributions[sym] = 0.0

        return contributions

    def _factor_concentration_contributions(
        self,
        symbols: list[str],
        weights: dict[str, float],
        factor_exposures: dict[str, dict[str, float]],
    ) -> dict[str, float]:
        """Compute each position's contribution to factor HHI."""
        if not factor_exposures:
            return {s: 1.0 / max(len(symbols), 1) for s in symbols}

        # Aggregate portfolio-level factor exposures
        portfolio_factors: dict[str, float] = {}
        for sym in symbols:
            w = weights.get(sym, 0.0)
            pos_factors = factor_exposures.get(sym, {})
            for factor, beta in pos_factors.items():
                portfolio_factors[factor] = portfolio_factors.get(factor, 0.0) + w * beta

        total_abs = sum(abs(v) for v in portfolio_factors.values())
        if total_abs == 0:
            return {s: 1.0 / max(len(symbols), 1) for s in symbols}

        # Compute each position's marginal contribution to HHI
        contributions: dict[str, float] = {}
        total = 0.0
        for sym in symbols:
            w = weights.get(sym, 0.0)
            pos_factors = factor_exposures.get(sym, {})
            # Position's contribution = sum of (its weighted exposure * portfolio share)^2-ish
            contrib = 0.0
            for factor, beta in pos_factors.items():
                portfolio_share = abs(portfolio_factors.get(factor, 0.0)) / total_abs
                contrib += abs(w * beta) * portfolio_share
            contributions[sym] = contrib
            total += contrib

        if total > 0:
            for sym in contributions:
                contributions[sym] /= total

        return contributions

    def _euler_risk_decomposition(
        self,
        symbols: list[str],
        weights: dict[str, float],
        returns: pd.DataFrame,
    ) -> dict[str, float]:
        """Euler risk decomposition: marginal variance contribution per position."""
        available = [s for s in symbols if s in returns.columns]
        n = len(available)
        if n < 2:
            return {s: 1.0 / max(len(symbols), 1) for s in symbols}

        w = np.array([weights.get(s, 0.0) for s in available])
        w_sum = np.sum(np.abs(w))
        if w_sum == 0:
            return {s: 1.0 / max(len(symbols), 1) for s in symbols}

        cov = returns[available].cov().values
        # Regularize
        cov += np.eye(n) * 1e-10

        # Marginal risk contribution: w_i * (Sigma @ w)_i / (w' Sigma w)
        sigma_w = cov @ w
        port_var = w @ sigma_w
        if port_var <= 0:
            return {s: 1.0 / max(len(symbols), 1) for s in symbols}

        marginal = w * sigma_w / port_var

        contributions: dict[str, float] = {}
        total = np.sum(np.abs(marginal))
        for i, sym in enumerate(available):
            contributions[sym] = abs(float(marginal[i])) / total if total > 0 else 1.0 / n

        for sym in symbols:
            if sym not in contributions:
                contributions[sym] = 0.0

        return contributions

    @staticmethod
    def _recommend(
        heat_share: float,
        corr_contribution: float,
        factor_contribution: float,
        risk_contribution: float,
        weight: float,
        heat_score: float,
    ) -> tuple[str, str]:
        """Generate recommendation for a position."""
        # High heat + high contribution = reduce
        if heat_score >= 0.85 and heat_share > 0.15:
            return "reduce", f"Top risk contributor ({heat_share:.0%} of heat) during critical conditions"
        if heat_score >= 0.75 and heat_share > 0.20:
            return "reduce", f"Large heat share ({heat_share:.0%}) in hot conditions"

        # High correlation contribution = hedge
        if corr_contribution > 0.25 and heat_score >= 0.55:
            return "hedge", f"High correlation contribution ({corr_contribution:.0%}) driving portfolio risk"

        # High factor concentration = hedge
        if factor_contribution > 0.25 and heat_score >= 0.55:
            return "hedge", f"Concentrated factor exposure ({factor_contribution:.0%} of factor HHI)"

        # Default: hold in cool conditions
        if heat_score < 0.55:
            return "hold", "Normal conditions, no action needed"

        # Overweight + high risk contribution = monitor
        if weight > 0.15 and risk_contribution > 0.20:
            return "monitor", f"Overweight ({weight:.0%}) with elevated risk contribution ({risk_contribution:.0%})"

        return "monitor", f"Heat share {heat_share:.0%}, monitoring"
