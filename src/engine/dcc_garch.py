"""DCC-GARCH engine — Dynamic Conditional Correlation.

Replaces static EWMA correlation with time-varying conditional correlations
that better capture crisis dynamics and regime shifts.

Uses the `arch` library for univariate GARCH and `mgarch` for DCC estimation.
Falls back to EWMA when DCC fails (numerical instability with small datasets).

References:
- Engle (2002): Dynamic Conditional Correlation
- Bollerslev (1990): GARCH(1,1)
- arXiv 2506.02796 (Jun 2025): Deep Learning Enhanced Multivariate GARCH
"""

from __future__ import annotations

import logging
from typing import Optional

import numpy as np
import pandas as pd
from arch import arch_model

logger = logging.getLogger(__name__)


class DCCGarchEngine:
    """Dynamic Conditional Correlation via GARCH(1,1) + DCC(1,1)."""

    def __init__(
        self,
        garch_p: int = 1,
        garch_q: int = 1,
        min_obs: int = 100,
        rescale: bool = True,
    ):
        """
        Args:
            garch_p: GARCH lag order for variance.
            garch_q: GARCH lag order for residuals.
            min_obs: Minimum observations required for DCC estimation.
            rescale: Whether to rescale returns (multiply by 100) for numerical stability.
        """
        self.garch_p = garch_p
        self.garch_q = garch_q
        self.min_obs = min_obs
        self.rescale = rescale
        self._last_cond_corr: Optional[pd.DataFrame] = None
        self._last_cond_vol: Optional[pd.DataFrame] = None
        self._garch_models: dict[str, object] = {}

    def fit_univariate_garch(
        self, returns: pd.Series, symbol: str = ""
    ) -> dict:
        """
        Fit univariate GARCH(1,1) to a single return series.

        Returns dict with:
            - 'conditional_vol': Series of conditional volatilities
            - 'standardized_resid': Series of standardized residuals
            - 'params': model parameters
        """
        y = returns.dropna()
        if len(y) < self.min_obs:
            logger.debug("Insufficient data for GARCH on %s (%d < %d)", symbol, len(y), self.min_obs)
            return self._fallback_univariate(y, symbol)

        scale = 100.0 if self.rescale else 1.0
        y_scaled = y * scale

        try:
            model = arch_model(
                y_scaled,
                mean="Constant",
                vol="Garch",
                p=self.garch_p,
                q=self.garch_q,
                dist="normal",
                rescale=False,
            )
            result = model.fit(disp="off", show_warning=False)

            cond_vol = result.conditional_volatility / scale
            std_resid = result.std_resid

            self._garch_models[symbol] = result

            return {
                "conditional_vol": cond_vol,
                "standardized_resid": std_resid,
                "params": {
                    "omega": result.params.get("omega", 0),
                    "alpha": result.params.get("alpha[1]", 0),
                    "beta": result.params.get("beta[1]", 0),
                },
            }

        except Exception as e:
            logger.warning("GARCH fit failed for %s: %s — using EWMA fallback", symbol, e)
            return self._fallback_univariate(y, symbol)

    def _fallback_univariate(self, returns: pd.Series, symbol: str) -> dict:
        """EWMA fallback when GARCH fails."""
        ewma_vol = returns.ewm(span=60).std()
        std_resid = returns / ewma_vol.clip(lower=1e-10)
        return {
            "conditional_vol": ewma_vol,
            "standardized_resid": std_resid,
            "params": {"omega": 0, "alpha": 0.05, "beta": 0.94},
        }

    def compute_dcc(
        self, returns: pd.DataFrame
    ) -> tuple[pd.DataFrame, pd.DataFrame]:
        """
        Compute DCC correlation matrix and conditional volatilities.

        Two-step procedure:
        1. Fit univariate GARCH(1,1) to each asset → standardized residuals
        2. Apply DCC(1,1) to standardized residuals → dynamic correlations

        Args:
            returns: DataFrame of daily returns (rows=dates, cols=assets).

        Returns:
            Tuple of (conditional_correlation_matrix, conditional_volatility_df).
            Both are for the most recent time step.
        """
        if returns.empty or returns.shape[1] < 2:
            return pd.DataFrame(), pd.DataFrame()

        assets = returns.columns.tolist()
        n = len(assets)

        # Step 1: Univariate GARCH for each asset
        std_resids = {}
        cond_vols = {}

        for asset in assets:
            result = self.fit_univariate_garch(returns[asset], symbol=asset)
            std_resids[asset] = result["standardized_resid"]
            cond_vols[asset] = result["conditional_vol"]

        # Align all series to common index
        std_resid_df = pd.DataFrame(std_resids).dropna()
        cond_vol_df = pd.DataFrame(cond_vols).dropna()

        if len(std_resid_df) < 30:
            logger.warning("Insufficient aligned data for DCC (%d rows)", len(std_resid_df))
            return self._fallback_dcc(returns)

        # Step 2: DCC(1,1) on standardized residuals
        try:
            corr_matrix = self._dcc_correlation(std_resid_df)
        except Exception as e:
            logger.warning("DCC estimation failed: %s — using EWMA fallback", e)
            return self._fallback_dcc(returns)

        # Build conditional correlation DataFrame
        corr_df = pd.DataFrame(corr_matrix, index=assets[:corr_matrix.shape[0]], columns=assets[:corr_matrix.shape[1]])

        # Latest conditional vols
        latest_vols = cond_vol_df.iloc[-1] if not cond_vol_df.empty else pd.Series()

        self._last_cond_corr = corr_df
        self._last_cond_vol = latest_vols

        return corr_df, cond_vol_df

    def _dcc_correlation(self, std_resid_df: pd.DataFrame) -> np.ndarray:
        """
        Estimate DCC(1,1) parameters and compute terminal correlation.

        DCC model:
            Q_t = (1 - a - b) * Q_bar + a * (e_{t-1} @ e_{t-1}') + b * Q_{t-1}
            R_t = diag(Q_t)^{-1/2} @ Q_t @ diag(Q_t)^{-1/2}

        We estimate a, b via quasi-maximum likelihood.
        """
        data = std_resid_df.values
        T, n = data.shape

        # Unconditional correlation of standardized residuals
        Q_bar = np.corrcoef(data, rowvar=False)

        # Initialize DCC parameters (typical starting values)
        # Grid search for a, b that maximize pseudo-log-likelihood
        best_a, best_b = self._estimate_dcc_params(data, Q_bar)

        # Forward pass with estimated parameters
        Q_t = Q_bar.copy()
        for t in range(T):
            e_t = data[t:t+1].T  # column vector
            outer = e_t @ e_t.T
            Q_t = (1 - best_a - best_b) * Q_bar + best_a * outer + best_b * Q_t

        # Normalize Q_t to correlation matrix R_t
        diag_sqrt = np.sqrt(np.diag(Q_t))
        diag_sqrt[diag_sqrt == 0] = 1e-10
        D_inv = np.diag(1.0 / diag_sqrt)
        R_t = D_inv @ Q_t @ D_inv

        # Ensure valid correlation matrix
        np.fill_diagonal(R_t, 1.0)
        R_t = np.clip(R_t, -1.0, 1.0)

        return R_t

    def _estimate_dcc_params(
        self, data: np.ndarray, Q_bar: np.ndarray
    ) -> tuple[float, float]:
        """
        Estimate DCC(1,1) parameters a, b via grid search.

        Constraint: a > 0, b > 0, a + b < 1.
        We use a coarse grid + refinement for speed.
        """
        T, n = data.shape
        best_ll = -np.inf
        best_a, best_b = 0.05, 0.90

        # Coarse grid
        for a in np.arange(0.01, 0.15, 0.02):
            for b in np.arange(0.80, 0.99, 0.02):
                if a + b >= 1.0:
                    continue
                ll = self._dcc_loglik(data, Q_bar, a, b)
                if ll > best_ll:
                    best_ll = ll
                    best_a, best_b = a, b

        # Fine grid around best
        for a in np.arange(max(0.001, best_a - 0.02), best_a + 0.02, 0.005):
            for b in np.arange(max(0.5, best_b - 0.02), best_b + 0.02, 0.005):
                if a + b >= 1.0 or a <= 0 or b <= 0:
                    continue
                ll = self._dcc_loglik(data, Q_bar, a, b)
                if ll > best_ll:
                    best_ll = ll
                    best_a, best_b = a, b

        logger.info("DCC params estimated: a=%.4f, b=%.4f (loglik=%.2f)", best_a, best_b, best_ll)
        return best_a, best_b

    def _dcc_loglik(
        self, data: np.ndarray, Q_bar: np.ndarray, a: float, b: float
    ) -> float:
        """Compute pseudo-log-likelihood for DCC(1,1)."""
        T, n = data.shape
        Q_t = Q_bar.copy()
        ll = 0.0

        for t in range(T):
            e_t = data[t:t+1].T
            outer = e_t @ e_t.T
            Q_t = (1 - a - b) * Q_bar + a * outer + b * Q_t

            # Normalize to R_t
            diag_sqrt = np.sqrt(np.diag(Q_t))
            diag_sqrt[diag_sqrt == 0] = 1e-10
            D_inv = np.diag(1.0 / diag_sqrt)
            R_t = D_inv @ Q_t @ D_inv

            # Log-likelihood contribution
            try:
                sign, logdet = np.linalg.slogdet(R_t)
                if sign <= 0:
                    return -np.inf
                R_inv = np.linalg.inv(R_t)
                e_row = data[t]
                ll += -0.5 * (logdet + e_row @ R_inv @ e_row - e_row @ e_row)
            except np.linalg.LinAlgError:
                return -np.inf

        return ll

    def _fallback_dcc(
        self, returns: pd.DataFrame
    ) -> tuple[pd.DataFrame, pd.DataFrame]:
        """EWMA fallback when DCC fails entirely."""
        logger.info("Using EWMA correlation fallback")
        ewm_cov = returns.ewm(span=60).cov()
        n = returns.shape[1]
        last_block = ewm_cov.iloc[-n:]

        if isinstance(last_block, pd.DataFrame):
            std = np.sqrt(np.diag(last_block.values))
            std[std == 0] = 1e-10
            outer = np.outer(std, std)
            corr_vals = last_block.values / outer
            np.fill_diagonal(corr_vals, 1.0)
            corr_df = pd.DataFrame(corr_vals, index=returns.columns, columns=returns.columns)
        else:
            corr_df = returns.corr()

        vol_df = returns.ewm(span=60).std()
        return corr_df, vol_df

    @property
    def last_conditional_correlation(self) -> Optional[pd.DataFrame]:
        """Most recently computed DCC correlation matrix."""
        return self._last_cond_corr

    @property
    def last_conditional_volatility(self) -> Optional[pd.DataFrame]:
        """Most recently computed conditional volatilities."""
        return self._last_cond_vol

    def get_correlation_regime_indicator(self) -> float:
        """
        Return a 0-1 indicator of how much correlations have shifted
        from their unconditional (long-run) levels.

        Higher = correlations are elevated relative to normal = stress.
        """
        if self._last_cond_corr is None:
            return 0.5

        corr = self._last_cond_corr.values
        n = corr.shape[0]
        if n < 2:
            return 0.5

        mask = ~np.eye(n, dtype=bool)
        avg_corr = np.mean(corr[mask])

        # Map avg correlation to 0-1 stress indicator
        # Typical equity correlations: 0.2-0.4 normal, 0.5-0.8 stress
        stress = np.clip((avg_corr - 0.2) / 0.6, 0, 1)
        return float(stress)
