"""Correlation engine — EWMA rolling correlation matrix."""

from __future__ import annotations

import logging

import numpy as np
import pandas as pd

from src.engine.metrics import avg_pairwise_correlation

logger = logging.getLogger(__name__)


class CorrelationEngine:
    """Compute and track rolling correlation matrices."""

    def __init__(self, ewma_span: int = 60):
        """
        Args:
            ewma_span: Exponential weighted moving average span in days.
        """
        self.ewma_span = ewma_span
        self._history: list[float] = []  # avg correlation history for percentile ranking

    def compute_correlation_matrix(
        self, returns: pd.DataFrame
    ) -> pd.DataFrame:
        """
        Compute EWMA correlation matrix from returns.

        Args:
            returns: DataFrame of daily returns (rows=dates, cols=assets).

        Returns:
            Correlation matrix DataFrame (N × N).
        """
        if returns.empty or returns.shape[1] < 2:
            return pd.DataFrame()

        # EWMA covariance → correlation
        ewm_cov = returns.ewm(span=self.ewma_span).cov()

        # Get the most recent correlation block
        last_date = returns.index[-1]
        try:
            cov_block = ewm_cov.loc[last_date]
        except KeyError:
            # Fallback: use iloc for last block
            n = returns.shape[1]
            cov_block = ewm_cov.iloc[-n:]

        # Convert covariance to correlation
        if isinstance(cov_block, pd.DataFrame):
            std = np.sqrt(np.diag(cov_block.values))
            std[std == 0] = 1e-10  # Avoid division by zero
            outer_std = np.outer(std, std)
            corr_values = cov_block.values / outer_std
            np.fill_diagonal(corr_values, 1.0)
            corr_matrix = pd.DataFrame(
                corr_values,
                index=cov_block.index if hasattr(cov_block.index, '__len__') else returns.columns,
                columns=cov_block.columns if hasattr(cov_block, 'columns') else returns.columns,
            )
        else:
            # Simpler fallback
            corr_matrix = returns.iloc[-self.ewma_span:].corr()

        return corr_matrix

    def get_top_correlated_pair(self, corr_matrix: pd.DataFrame) -> tuple[str, str, float]:
        """
        Find the most correlated pair of assets (excluding self-correlation).

        Returns:
            Tuple of (symbol_a, symbol_b, correlation).
        """
        if corr_matrix.empty or corr_matrix.shape[0] < 2:
            return ("", "", 0.0)

        # Zero out diagonal
        corr_vals = corr_matrix.values.copy()
        np.fill_diagonal(corr_vals, 0)

        # Find max absolute correlation
        max_idx = np.unravel_index(np.argmax(np.abs(corr_vals)), corr_vals.shape)
        sym_a = corr_matrix.index[max_idx[0]]
        sym_b = corr_matrix.columns[max_idx[1]]
        corr_val = corr_vals[max_idx]

        return (str(sym_a), str(sym_b), float(corr_val))

    def compute_avg_correlation(self, corr_matrix: pd.DataFrame) -> float:
        """Get average off-diagonal correlation and update history."""
        avg = avg_pairwise_correlation(corr_matrix)
        self._history.append(avg)

        # Keep rolling history bounded
        if len(self._history) > 504:
            self._history = self._history[-504:]

        return avg

    @property
    def correlation_history(self) -> list[float]:
        """Historical average correlations for percentile ranking."""
        return self._history
