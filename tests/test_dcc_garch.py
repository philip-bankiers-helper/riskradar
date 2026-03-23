"""Tests for DCC-GARCH engine."""

import numpy as np
import pandas as pd
import pytest

from src.engine.dcc_garch import DCCGarchEngine


@pytest.fixture
def sample_returns():
    """Generate correlated return data."""
    np.random.seed(42)
    n_days = 200
    n_assets = 5

    # Generate correlated returns
    base = np.random.randn(n_days)
    returns = {}
    for i in range(n_assets):
        noise = np.random.randn(n_days) * 0.5
        returns[f"ASSET{i}"] = base * 0.01 + noise * 0.01

    dates = pd.bdate_range("2025-01-01", periods=n_days)
    return pd.DataFrame(returns, index=dates)


@pytest.fixture
def dcc_engine():
    return DCCGarchEngine(min_obs=50)


class TestUnivarateGarch:
    def test_fit_garch(self, dcc_engine, sample_returns):
        result = dcc_engine.fit_univariate_garch(sample_returns["ASSET0"], "ASSET0")
        assert "conditional_vol" in result
        assert "standardized_resid" in result
        assert "params" in result
        assert len(result["conditional_vol"]) > 0
        assert len(result["standardized_resid"]) > 0

    def test_garch_params(self, dcc_engine, sample_returns):
        result = dcc_engine.fit_univariate_garch(sample_returns["ASSET0"], "ASSET0")
        params = result["params"]
        assert "omega" in params
        assert "alpha" in params
        assert "beta" in params

    def test_fallback_on_insufficient_data(self, dcc_engine):
        short = pd.Series(np.random.randn(10) * 0.01)
        result = dcc_engine.fit_univariate_garch(short, "SHORT")
        # Should return EWMA fallback without error
        assert "conditional_vol" in result
        assert len(result["conditional_vol"]) == 10


class TestDCC:
    def test_compute_dcc(self, dcc_engine, sample_returns):
        corr_df, vol_df = dcc_engine.compute_dcc(sample_returns)
        assert not corr_df.empty
        assert corr_df.shape[0] == corr_df.shape[1]
        assert corr_df.shape[0] == sample_returns.shape[1]

        # Diagonal should be 1.0
        for i in range(corr_df.shape[0]):
            assert abs(corr_df.iloc[i, i] - 1.0) < 0.01

        # Off-diagonal should be in [-1, 1]
        for i in range(corr_df.shape[0]):
            for j in range(corr_df.shape[1]):
                assert -1.0 <= corr_df.iloc[i, j] <= 1.0

    def test_dcc_symmetric(self, dcc_engine, sample_returns):
        corr_df, _ = dcc_engine.compute_dcc(sample_returns)
        diff = corr_df.values - corr_df.values.T
        assert np.max(np.abs(diff)) < 0.01

    def test_empty_returns(self, dcc_engine):
        empty = pd.DataFrame()
        corr, vol = dcc_engine.compute_dcc(empty)
        assert corr.empty

    def test_single_asset(self, dcc_engine):
        single = pd.DataFrame({"A": np.random.randn(100) * 0.01})
        corr, vol = dcc_engine.compute_dcc(single)
        assert corr.empty

    def test_stress_indicator(self, dcc_engine, sample_returns):
        dcc_engine.compute_dcc(sample_returns)
        stress = dcc_engine.get_correlation_regime_indicator()
        assert 0.0 <= stress <= 1.0

    def test_last_properties(self, dcc_engine, sample_returns):
        dcc_engine.compute_dcc(sample_returns)
        assert dcc_engine.last_conditional_correlation is not None
        assert dcc_engine.last_conditional_volatility is not None


class TestDCCFallback:
    def test_fallback_dcc(self, dcc_engine, sample_returns):
        """Test EWMA fallback directly."""
        corr, vol = dcc_engine._fallback_dcc(sample_returns)
        assert not corr.empty
        assert corr.shape[0] == sample_returns.shape[1]
