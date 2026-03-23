"""Factor exposure engine — rolling OLS regression against factor ETFs.

For each portfolio position, estimates:
    R_position = α + Σ(β_i · R_factor_i) + ε

using a rolling window of daily returns.
"""

from __future__ import annotations

import logging

import numpy as np
import pandas as pd
import statsmodels.api as sm

from src.models import FactorExposure, FactorExposureSummary

logger = logging.getLogger(__name__)


class FactorModel:
    """Rolling factor regression engine."""

    def __init__(
        self,
        factor_etfs: list[dict[str, str]],
        rolling_window: int = 60,
    ):
        """
        Args:
            factor_etfs: List of dicts with 'name' and 'symbol' keys.
            rolling_window: Number of trading days for rolling regression.
        """
        self.factor_etfs = factor_etfs
        self.factor_symbols = [f["symbol"] for f in factor_etfs]
        self.factor_names = {f["symbol"]: f["name"] for f in factor_etfs}
        self.rolling_window = rolling_window

    def estimate_exposures(
        self,
        position_returns: pd.DataFrame,
        factor_returns: pd.DataFrame,
    ) -> list[FactorExposure]:
        """
        Run rolling OLS for each position against factor ETFs.

        Args:
            position_returns: DataFrame of position daily returns.
            factor_returns: DataFrame of factor ETF daily returns.

        Returns:
            List of FactorExposure objects (most recent window estimate).
        """
        # Align dates
        common_idx = position_returns.index.intersection(factor_returns.index)
        if len(common_idx) < self.rolling_window:
            logger.warning(
                "Insufficient data: %d days < %d window", len(common_idx), self.rolling_window
            )
            common_idx = common_idx[-max(20, len(common_idx)):]

        pos_ret = position_returns.loc[common_idx]
        fac_ret = factor_returns.loc[common_idx]

        # Use last rolling_window days
        pos_ret = pos_ret.iloc[-self.rolling_window:]
        fac_ret = fac_ret.iloc[-self.rolling_window:]

        # Align factor columns with available data
        available_factors = [s for s in self.factor_symbols if s in fac_ret.columns]
        X = fac_ret[available_factors].dropna()

        exposures = []
        for symbol in pos_ret.columns:
            y = pos_ret[symbol].loc[X.index].dropna()
            if len(y) < 20:
                logger.debug("Skipping %s: only %d data points", symbol, len(y))
                continue

            common = y.index.intersection(X.index)
            y_aligned = y.loc[common]
            X_aligned = sm.add_constant(X.loc[common])

            try:
                model = sm.OLS(y_aligned, X_aligned).fit()

                for factor_sym in available_factors:
                    factor_name = self.factor_names.get(factor_sym, factor_sym)
                    beta = model.params.get(factor_sym, 0.0)
                    t_stat = model.tvalues.get(factor_sym, 0.0)

                    exposures.append(FactorExposure(
                        symbol=symbol,
                        factor_name=factor_name,
                        beta=float(beta),
                        t_stat=float(t_stat),
                        r_squared=float(model.rsquared),
                    ))

            except Exception as e:
                logger.warning("OLS failed for %s: %s", symbol, e)

        return exposures

    def aggregate_exposures(
        self,
        exposures: list[FactorExposure],
        weights: dict[str, float],
    ) -> list[FactorExposureSummary]:
        """
        Aggregate per-position factor exposures to portfolio level.

        Args:
            exposures: Individual position factor betas.
            weights: Dict of symbol -> portfolio weight.

        Returns:
            List of FactorExposureSummary — total portfolio exposure per factor.
        """
        # Group by factor
        factor_totals: dict[str, float] = {}

        for exp in exposures:
            w = weights.get(exp.symbol, 0.0)
            factor_key = exp.factor_name
            factor_totals[factor_key] = factor_totals.get(factor_key, 0.0) + w * exp.beta

        # Calculate shares
        total_abs = sum(abs(v) for v in factor_totals.values())
        if total_abs == 0:
            total_abs = 1.0

        summaries = []
        factor_name_to_symbol = {f["name"]: f["symbol"] for f in self.factor_etfs}

        for factor_name, total_exp in factor_totals.items():
            summaries.append(FactorExposureSummary(
                factor_name=factor_name,
                factor_symbol=factor_name_to_symbol.get(factor_name, ""),
                total_exposure=float(total_exp),
                exposure_share=float(abs(total_exp) / total_abs),
            ))

        # Sort by absolute exposure
        summaries.sort(key=lambda x: abs(x.total_exposure), reverse=True)
        return summaries

    def get_dominant_factor(self, summaries: list[FactorExposureSummary]) -> str:
        """Return the name of the factor with highest absolute exposure."""
        if not summaries:
            return "none"
        return summaries[0].factor_name
