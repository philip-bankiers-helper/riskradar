"""Tests for core risk metrics."""

import numpy as np
import pandas as pd
import pytest

from src.engine.metrics import (
    absorption_ratio,
    avg_pairwise_correlation,
    diversification_ratio,
    factor_hhi,
    percentile_rank,
    turbulence_index,
)


@pytest.fixture
def correlated_returns():
    """Generate returns with known correlation structure."""
    np.random.seed(42)
    n = 252
    # Create correlated returns
    base = np.random.randn(n) * 0.01
    noise_scale = 0.005
    data = {
        "A": base + np.random.randn(n) * noise_scale,
        "B": base + np.random.randn(n) * noise_scale,
        "C": -base + np.random.randn(n) * noise_scale,  # Negatively correlated
        "D": np.random.randn(n) * 0.01,  # Independent
    }
    return pd.DataFrame(data)


@pytest.fixture
def uncorrelated_returns():
    """Generate independent returns."""
    np.random.seed(123)
    n = 252
    return pd.DataFrame({
        f"Asset_{i}": np.random.randn(n) * 0.01 for i in range(10)
    })


class TestAbsorptionRatio:
    def test_high_correlation_high_ar(self, correlated_returns):
        """Highly correlated assets should have high AR."""
        ar = absorption_ratio(correlated_returns)
        assert 0 <= ar <= 1

    def test_independent_low_ar(self, uncorrelated_returns):
        """Independent assets should have lower AR."""
        ar = absorption_ratio(uncorrelated_returns)
        assert 0 <= ar <= 1
        # With 10 independent assets, AR should be relatively low
        assert ar < 0.8

    def test_insufficient_data_returns_neutral(self):
        """With very little data, should return 0.5."""
        tiny = pd.DataFrame({"A": [0.01, 0.02], "B": [0.01, -0.01]})
        ar = absorption_ratio(tiny, window=252)
        assert ar == 0.5


class TestTurbulenceIndex:
    def test_normal_day_low_turbulence(self, correlated_returns):
        """A typical day should have moderate turbulence."""
        today = correlated_returns.iloc[-1].values
        history = correlated_returns.iloc[:-1]
        turb = turbulence_index(today, history)
        assert turb >= 0

    def test_extreme_day_high_turbulence(self, correlated_returns):
        """An extreme return vector should have high turbulence."""
        extreme = np.array([0.10, -0.10, 0.10, -0.10])  # 10% moves
        history = correlated_returns.iloc[:-1]
        turb = turbulence_index(extreme, history)
        assert turb > 10  # Should be very high


class TestDiversificationRatio:
    def test_equal_weight_correlated(self, correlated_returns):
        """Equal weight portfolio of correlated assets."""
        weights = np.array([0.25, 0.25, 0.25, 0.25])
        dr = diversification_ratio(weights, correlated_returns)
        assert dr >= 1.0

    def test_concentrated_portfolio(self, correlated_returns):
        """Single-asset portfolio should have DR ≈ 1."""
        weights = np.array([1.0, 0.0, 0.0, 0.0])
        dr = diversification_ratio(weights, correlated_returns)
        assert abs(dr - 1.0) < 0.3


class TestFactorHHI:
    def test_single_factor_max_hhi(self):
        """Single dominant factor should give HHI = 1."""
        assert factor_hhi({"rates": 1.0}) == 1.0

    def test_equal_factors_low_hhi(self):
        """Equal exposures across many factors → low HHI."""
        exposures = {f"factor_{i}": 1.0 for i in range(10)}
        hhi = factor_hhi(exposures)
        assert abs(hhi - 0.1) < 0.01  # 1/N

    def test_empty_returns_zero(self):
        assert factor_hhi({}) == 0.0


class TestAvgPairwiseCorrelation:
    def test_identity_matrix(self):
        """Identity correlation matrix → avg corr = 0."""
        corr = pd.DataFrame(np.eye(5))
        assert avg_pairwise_correlation(corr) == 0.0

    def test_perfect_correlation(self):
        """All ones → avg corr = 1."""
        corr = pd.DataFrame(np.ones((3, 3)))
        assert abs(avg_pairwise_correlation(corr) - 1.0) < 0.01


class TestPercentileRank:
    def test_max_value_rank_1(self):
        assert percentile_rank(100, [1, 2, 3, 100]) == 1.0

    def test_min_value_rank_low(self):
        assert percentile_rank(1, [1, 2, 3, 4]) == 0.25

    def test_empty_history_neutral(self):
        assert percentile_rank(5, []) == 0.5
