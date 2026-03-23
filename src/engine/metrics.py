"""Core risk metrics: Absorption Ratio, Turbulence, Diversification Ratio, HHI.

References:
- Absorption Ratio: Kritzman, Li, Page, Rigobon (2010) SSRN 1582687
- Turbulence Index: Kritzman & Li (2010) via Mahalanobis distance
- Diversification Ratio: Choueifaty & Coignard (2008)
- Factor HHI: Herfindahl-Hirschman Index of factor concentration
"""

from __future__ import annotations

import logging

import numpy as np
import pandas as pd
from sklearn.decomposition import PCA

logger = logging.getLogger(__name__)


def absorption_ratio(
    returns: pd.DataFrame,
    n_components: int | None = None,
    window: int = 252,
) -> float:
    """
    Calculate the Absorption Ratio (Kritzman et al. 2010).

    AR = variance explained by top N eigenvectors / total variance.
    High AR → risk compressing into fewer sources → market fragility.

    Args:
        returns: DataFrame of asset returns (rows=dates, cols=assets).
        n_components: Number of PCA components. Default: N_assets / 5.
        window: Lookback window in trading days.

    Returns:
        AR value in [0, 1]. Higher = more systemic risk.
    """
    if len(returns) < window:
        window = len(returns)
    if window < 10:
        return 0.5  # Insufficient data, return neutral

    data = returns.iloc[-window:].dropna(axis=1, how="any")
    n_assets = data.shape[1]
    if n_assets < 3:
        return 0.5

    if n_components is None:
        n_components = max(1, n_assets // 5)
    n_components = min(n_components, n_assets)

    pca = PCA(n_components=n_components)
    pca.fit(data.values)

    total_variance = np.sum(np.var(data.values, axis=0))
    if total_variance == 0:
        return 0.5

    explained_variance = np.sum(pca.explained_variance_)
    ar = explained_variance / total_variance

    return float(np.clip(ar, 0, 1))


def turbulence_index(
    returns_today: np.ndarray | pd.Series,
    returns_history: pd.DataFrame,
) -> float:
    """
    Calculate the Turbulence Index (Mahalanobis distance).

    Measures how unusual today's return vector is relative to history.
    turb = (r - μ)ᵀ Σ⁻¹ (r - μ)

    Args:
        returns_today: Today's return vector for each asset.
        returns_history: Historical returns DataFrame.

    Returns:
        Turbulence value (>0). Higher = more unusual.
    """
    if isinstance(returns_today, pd.Series):
        returns_today = returns_today.values

    history = returns_history.dropna(axis=1, how="any")
    if len(history) < 30 or history.shape[1] < 2:
        return 0.0

    mu = history.mean().values
    cov = history.cov().values.copy()

    # Regularize covariance for numerical stability
    cov += np.eye(cov.shape[0]) * 1e-8

    try:
        cov_inv = np.linalg.inv(cov)
    except np.linalg.LinAlgError:
        cov_inv = np.linalg.pinv(cov)

    diff = returns_today[:len(mu)] - mu
    turb = float(diff @ cov_inv @ diff)

    return max(turb, 0.0)


def diversification_ratio(
    weights: np.ndarray,
    returns: pd.DataFrame,
    window: int = 252,
) -> float:
    """
    Calculate the Diversification Ratio (Choueifaty & Coignard 2008).

    DR = (weighted average vol) / (portfolio vol)
    DR = 1 means no diversification. DR >> 1 means well-diversified.

    Args:
        weights: Portfolio weights array.
        returns: Returns DataFrame.
        window: Lookback window.

    Returns:
        DR value (≥ 1). Higher = more diversified.
    """
    data = returns.iloc[-window:].dropna(axis=1, how="any")
    if data.shape[1] < 2 or len(data) < 20:
        return 1.0

    # Align weights with available columns
    w = np.array(weights[:data.shape[1]])
    if len(w) == 0 or np.sum(np.abs(w)) == 0:
        return 1.0

    # Normalize weights
    w = w / np.sum(np.abs(w))

    individual_vols = data.std().values
    weighted_avg_vol = np.sum(np.abs(w) * individual_vols)

    cov_matrix = data.cov().values
    port_variance = w @ cov_matrix @ w
    port_vol = np.sqrt(max(port_variance, 1e-12))

    if port_vol == 0:
        return 1.0

    dr = weighted_avg_vol / port_vol
    return max(float(dr), 1.0)


def factor_hhi(factor_exposures: dict[str, float]) -> float:
    """
    Calculate Herfindahl-Hirschman Index of factor concentration.

    HHI = Σ(share²) where share = |factor_exposure| / Σ|all_exposures|

    Args:
        factor_exposures: Dict of factor_name -> total_exposure.

    Returns:
        HHI in [0, 1]. 0 = perfectly diversified, 1 = single factor dominance.
    """
    if not factor_exposures:
        return 0.0

    abs_exposures = {k: abs(v) for k, v in factor_exposures.items()}
    total = sum(abs_exposures.values())

    if total == 0:
        return 0.0

    shares = [v / total for v in abs_exposures.values()]
    hhi = sum(s ** 2 for s in shares)

    return float(hhi)


def avg_pairwise_correlation(corr_matrix: pd.DataFrame | np.ndarray) -> float:
    """
    Calculate average off-diagonal pairwise correlation.

    Args:
        corr_matrix: Correlation matrix (N × N).

    Returns:
        Average correlation in [-1, 1].
    """
    if isinstance(corr_matrix, pd.DataFrame):
        corr_matrix = corr_matrix.values

    n = corr_matrix.shape[0]
    if n < 2:
        return 0.0

    # Sum off-diagonal elements
    mask = ~np.eye(n, dtype=bool)
    off_diag = corr_matrix[mask]

    return float(np.mean(off_diag))


def percentile_rank(value: float, history: list[float] | np.ndarray) -> float:
    """
    Calculate rolling percentile rank of a value within its history.

    Args:
        value: Current value.
        history: Historical values to rank against.

    Returns:
        Percentile rank in [0, 1].
    """
    history = np.array(history)
    history = history[~np.isnan(history)]

    if len(history) == 0:
        return 0.5  # Neutral if no history

    rank = np.sum(history <= value) / len(history)
    return float(np.clip(rank, 0, 1))
