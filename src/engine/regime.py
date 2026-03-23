"""Regime detection engine — HMM + change point detection.

Identifies market regimes from return/volatility data:
- Low volatility (calm)
- Normal
- Crisis (high vol, high correlation)

Uses Gaussian HMM (hmmlearn) for online regime identification
and ruptures for offline change point detection (calibration).

References:
- Nature Scientific Reports (Nov 2025): ML approach to risk-based asset allocation
- QuantInsti (Dec 2025): Regime-adaptive trading with HMM
- Kritzman et al.: Regime shifts and asset allocation
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Optional

import numpy as np
import pandas as pd
from hmmlearn.hmm import GaussianHMM

logger = logging.getLogger(__name__)

# Try importing ruptures (optional)
try:
    import ruptures as rpt
    HAS_RUPTURES = True
except ImportError:
    HAS_RUPTURES = False
    logger.warning("ruptures not available — change point detection disabled")


# Regime labels ordered by expected volatility
REGIME_LABELS = {0: "low_vol", 1: "normal", 2: "crisis"}


class RegimeDetector:
    """HMM-based market regime detector with change point support."""

    def __init__(
        self,
        n_regimes: int = 3,
        n_iter: int = 100,
        lookback: int = 504,
        min_obs: int = 100,
        retrain_interval: int = 20,
    ):
        """
        Args:
            n_regimes: Number of HMM states (default 3: low_vol, normal, crisis).
            n_iter: Max EM iterations for HMM training.
            lookback: Training window in trading days (~2 years).
            min_obs: Minimum observations required for fitting.
            retrain_interval: Re-fit HMM every N updates.
        """
        self.n_regimes = n_regimes
        self.n_iter = n_iter
        self.lookback = lookback
        self.min_obs = min_obs
        self.retrain_interval = retrain_interval

        self._model: Optional[GaussianHMM] = None
        self._is_fitted = False
        self._update_count = 0
        self._regime_history: list[dict] = []  # [{timestamp, regime, probs}]
        self._max_history = 504

    def fit(self, features: pd.DataFrame) -> bool:
        """
        Fit HMM on feature matrix.

        Features should include columns like:
        - portfolio_return: aggregate portfolio return
        - realized_vol: rolling realized volatility
        - avg_correlation: average pairwise correlation
        - heat_score: composite heat score

        Args:
            features: DataFrame of regime features (rows=dates).

        Returns:
            True if fitting succeeded.
        """
        if len(features) < self.min_obs:
            logger.debug(
                "Insufficient data for HMM: %d < %d", len(features), self.min_obs
            )
            return False

        # Use last lookback days
        data = features.iloc[-self.lookback:].dropna()
        if len(data) < self.min_obs:
            return False

        X = data.values

        try:
            model = GaussianHMM(
                n_components=self.n_regimes,
                covariance_type="full",
                n_iter=self.n_iter,
                random_state=42,
                tol=1e-4,
            )
            model.fit(X)

            # Reorder states by mean volatility (if vol column exists)
            # so state 0 = low_vol, state 2 = crisis
            self._model = self._reorder_states(model, X)
            self._is_fitted = True

            logger.info(
                "HMM fitted on %d observations, %d features. Score: %.2f",
                len(data), X.shape[1], self._model.score(X),
            )
            return True

        except Exception as e:
            logger.error("HMM fitting failed: %s", e, exc_info=True)
            return False

    def _reorder_states(self, model: GaussianHMM, X: np.ndarray) -> GaussianHMM:
        """
        Reorder HMM states so that state 0 = lowest volatility, state N = highest.

        Uses the mean of the first feature (expected to be return or vol proxy)
        or the variance of means to determine ordering.
        """
        means = model.means_
        # Use variance of the means across features as a proxy for "regime intensity"
        # Higher variance in means = more extreme regime
        # Or simpler: order by the L2 norm of means (absolute magnitude)
        norms = np.sqrt(np.sum(means ** 2, axis=1))

        # For financial data, crisis regime typically has higher absolute returns
        # and higher volatility. Order by the covariance trace (total variance).
        traces = np.array([np.trace(model.covars_[i]) for i in range(self.n_regimes)])
        order = np.argsort(traces)  # low variance first

        # Reorder model parameters
        model.means_ = model.means_[order]
        model.covars_ = model.covars_[order]
        model.startprob_ = model.startprob_[order]
        model.transmat_ = model.transmat_[order][:, order]

        return model

    def predict(self, features: pd.DataFrame) -> dict:
        """
        Predict current regime and state probabilities.

        Args:
            features: Feature DataFrame (same columns as fit).

        Returns:
            Dict with:
                - regime: str (low_vol/normal/crisis)
                - regime_id: int (0/1/2)
                - probabilities: dict of regime -> probability
                - confidence: float (max probability)
                - transition_risk: float (probability of transitioning to crisis)
        """
        if not self._is_fitted or self._model is None:
            return self._default_prediction()

        # Auto-retrain periodically
        self._update_count += 1
        if self._update_count >= self.retrain_interval:
            self.fit(features)
            self._update_count = 0

        try:
            X = features.dropna().values
            if len(X) < 2:
                return self._default_prediction()

            # Get state probabilities for the latest observation
            log_prob, state_seq = self._model.decode(X, algorithm="viterbi")
            posteriors = self._model.predict_proba(X)

            current_state = state_seq[-1]
            current_probs = posteriors[-1]

            regime_label = REGIME_LABELS.get(current_state, f"state_{current_state}")
            confidence = float(np.max(current_probs))

            # Transition risk: probability of moving to crisis from current state
            trans_matrix = self._model.transmat_
            crisis_state = self.n_regimes - 1  # Highest state = crisis
            transition_risk = float(trans_matrix[current_state, crisis_state])

            prob_dict = {
                REGIME_LABELS.get(i, f"state_{i}"): float(current_probs[i])
                for i in range(self.n_regimes)
            }

            result = {
                "regime": regime_label,
                "regime_id": int(current_state),
                "probabilities": prob_dict,
                "confidence": confidence,
                "transition_risk": transition_risk,
                "is_fitted": True,
            }

            # Record history
            self._regime_history.append({
                "timestamp": datetime.utcnow().isoformat(),
                **result,
            })
            if len(self._regime_history) > self._max_history:
                self._regime_history = self._regime_history[-self._max_history:]

            return result

        except Exception as e:
            logger.warning("HMM prediction failed: %s", e)
            return self._default_prediction()

    def _default_prediction(self) -> dict:
        """Return neutral prediction when model isn't available."""
        return {
            "regime": "unknown",
            "regime_id": -1,
            "probabilities": {
                "low_vol": 0.33,
                "normal": 0.34,
                "crisis": 0.33,
            },
            "confidence": 0.34,
            "transition_risk": 0.33,
            "is_fitted": False,
        }

    def build_features(
        self,
        portfolio_returns: pd.Series,
        avg_correlations: list[float],
        heat_scores: list[float],
        window: int = 20,
    ) -> pd.DataFrame:
        """
        Build feature matrix for HMM from raw data.

        Features:
        1. Rolling mean return (20-day)
        2. Rolling realized volatility (20-day)
        3. Average pairwise correlation (latest available)
        4. Heat score (latest available)

        Args:
            portfolio_returns: Series of portfolio-level daily returns.
            avg_correlations: List of average correlations (one per computation cycle).
            heat_scores: List of heat scores (one per computation cycle).
            window: Rolling window for return/vol features.

        Returns:
            Feature DataFrame ready for fit/predict.
        """
        if len(portfolio_returns) < window:
            return pd.DataFrame()

        features = pd.DataFrame(index=portfolio_returns.index)

        # Rolling return
        features["rolling_return"] = portfolio_returns.rolling(window).mean()

        # Realized vol
        features["realized_vol"] = portfolio_returns.rolling(window).std()

        # Return skewness (negative skew = tail risk)
        features["return_skew"] = portfolio_returns.rolling(window).skew()

        # Pad correlation and heat score to match index length
        n = len(features)
        if avg_correlations:
            corr_padded = self._pad_series(avg_correlations, n)
            features["avg_correlation"] = corr_padded
        else:
            features["avg_correlation"] = 0.0

        if heat_scores:
            heat_padded = self._pad_series(heat_scores, n)
            features["heat_score"] = heat_padded
        else:
            features["heat_score"] = 0.5

        return features.dropna()

    @staticmethod
    def _pad_series(values: list[float], target_len: int) -> np.ndarray:
        """Pad a short list to target length by repeating the last value."""
        arr = np.full(target_len, np.nan)
        n = min(len(values), target_len)
        arr[-n:] = values[-n:]
        # Forward fill any remaining NaNs
        if n < target_len:
            arr[:target_len - n] = values[0] if values else 0.0
        return arr

    # ─── Change Point Detection ────────────────────────────────────

    def detect_change_points(
        self,
        series: pd.Series,
        model: str = "rbf",
        penalty: float = 10.0,
        min_size: int = 20,
    ) -> list[int]:
        """
        Detect structural change points in a time series using ruptures.

        Args:
            series: Time series to analyze.
            model: Cost model ("rbf", "l2", "normal").
            penalty: Penalty for adding a change point (higher = fewer points).
            min_size: Minimum segment size.

        Returns:
            List of change point indices.
        """
        if not HAS_RUPTURES:
            logger.debug("ruptures not available")
            return []

        data = series.dropna().values
        if len(data) < min_size * 2:
            return []

        try:
            algo = rpt.Pelt(model=model, min_size=min_size).fit(data)
            change_points = algo.predict(pen=penalty)
            # Remove the last element (always = len(data))
            return [cp for cp in change_points if cp < len(data)]
        except Exception as e:
            logger.warning("Change point detection failed: %s", e)
            return []

    # ─── Properties ────────────────────────────────────────────────

    @property
    def is_fitted(self) -> bool:
        return self._is_fitted

    @property
    def regime_history(self) -> list[dict]:
        return self._regime_history

    @property
    def current_regime(self) -> str:
        """Most recent regime label."""
        if self._regime_history:
            return self._regime_history[-1].get("regime", "unknown")
        return "unknown"

    @property
    def transition_matrix(self) -> Optional[np.ndarray]:
        """HMM transition probability matrix."""
        if self._model is not None:
            return self._model.transmat_
        return None
