"""Tests for regime detection engine."""

import numpy as np
import pandas as pd
import pytest

from src.engine.regime import RegimeDetector


@pytest.fixture
def regime_detector():
    return RegimeDetector(n_regimes=3, min_obs=50, retrain_interval=100)


@pytest.fixture
def feature_data():
    """Generate feature data with regime-like behavior."""
    np.random.seed(42)
    n = 300
    dates = pd.bdate_range("2024-01-01", periods=n)

    # Simulate 3 regimes
    regime_labels = np.zeros(n, dtype=int)
    regime_labels[:100] = 0   # low vol
    regime_labels[100:200] = 1  # normal
    regime_labels[200:] = 2   # crisis

    vol_levels = [0.005, 0.01, 0.03]
    returns = np.array([np.random.randn() * vol_levels[r] for r in regime_labels])

    features = pd.DataFrame({
        "rolling_return": pd.Series(returns, index=dates).rolling(20).mean(),
        "realized_vol": pd.Series(returns, index=dates).rolling(20).std(),
        "return_skew": pd.Series(returns, index=dates).rolling(20).skew(),
        "avg_correlation": np.random.uniform(0.2, 0.6, n),
        "heat_score": np.random.uniform(0.2, 0.8, n),
    }, index=dates).dropna()

    return features


class TestRegimeDetector:
    def test_fit(self, regime_detector, feature_data):
        success = regime_detector.fit(feature_data)
        assert success
        assert regime_detector.is_fitted

    def test_fit_insufficient_data(self, regime_detector):
        short = pd.DataFrame({"a": [1, 2, 3], "b": [4, 5, 6]})
        success = regime_detector.fit(short)
        assert not success

    def test_predict(self, regime_detector, feature_data):
        regime_detector.fit(feature_data)
        result = regime_detector.predict(feature_data)

        assert "regime" in result
        assert result["regime"] in ("low_vol", "normal", "crisis")
        assert "probabilities" in result
        assert "confidence" in result
        assert 0 <= result["confidence"] <= 1
        assert "transition_risk" in result
        assert 0 <= result["transition_risk"] <= 1
        assert result["is_fitted"]

    def test_predict_unfitted(self, regime_detector, feature_data):
        result = regime_detector.predict(feature_data)
        assert result["regime"] == "unknown"
        assert not result["is_fitted"]

    def test_probabilities_sum_to_one(self, regime_detector, feature_data):
        regime_detector.fit(feature_data)
        result = regime_detector.predict(feature_data)
        prob_sum = sum(result["probabilities"].values())
        assert abs(prob_sum - 1.0) < 0.01

    def test_regime_history(self, regime_detector, feature_data):
        regime_detector.fit(feature_data)
        regime_detector.predict(feature_data)
        assert len(regime_detector.regime_history) == 1

    def test_current_regime(self, regime_detector, feature_data):
        regime_detector.fit(feature_data)
        regime_detector.predict(feature_data)
        assert regime_detector.current_regime in ("low_vol", "normal", "crisis")

    def test_transition_matrix(self, regime_detector, feature_data):
        regime_detector.fit(feature_data)
        tm = regime_detector.transition_matrix
        assert tm is not None
        assert tm.shape == (3, 3)
        # Rows should sum to 1
        for i in range(3):
            assert abs(np.sum(tm[i]) - 1.0) < 0.01


class TestBuildFeatures:
    def test_build_features(self, regime_detector):
        np.random.seed(42)
        returns = pd.Series(
            np.random.randn(100) * 0.01,
            index=pd.bdate_range("2025-01-01", periods=100),
        )
        features = regime_detector.build_features(
            portfolio_returns=returns,
            avg_correlations=[0.3] * 50,
            heat_scores=[0.5] * 50,
        )
        assert not features.empty
        assert "rolling_return" in features.columns
        assert "realized_vol" in features.columns
        assert "avg_correlation" in features.columns
        assert "heat_score" in features.columns

    def test_build_features_short_data(self, regime_detector):
        short = pd.Series([0.01, -0.01], index=pd.bdate_range("2025-01-01", periods=2))
        features = regime_detector.build_features(short, [], [])
        assert features.empty


class TestChangePointDetection:
    def test_detect_change_points(self, regime_detector):
        np.random.seed(42)
        # Create series with obvious change point
        s1 = np.random.randn(100) * 0.01
        s2 = np.random.randn(100) * 0.05 + 0.02
        series = pd.Series(np.concatenate([s1, s2]))

        cps = regime_detector.detect_change_points(series, penalty=5.0)
        # Should detect at least one change point near index 100
        assert len(cps) >= 1
        # Should be somewhere near the actual transition
        assert any(80 <= cp <= 120 for cp in cps)

    def test_no_change_points(self, regime_detector):
        stable = pd.Series(np.random.randn(100) * 0.01)
        cps = regime_detector.detect_change_points(stable, penalty=50.0)
        # High penalty — should find few or no change points
        assert len(cps) <= 1

    def test_short_series(self, regime_detector):
        short = pd.Series([1, 2, 3])
        cps = regime_detector.detect_change_points(short)
        assert cps == []
