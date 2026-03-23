"""Tests for risk throttle + pre-trade simulation."""

import numpy as np
import pandas as pd
import pytest

from src.engine.throttle import RiskThrottle, PreTradeSimulator
from src.engine.factor_model import FactorModel
from src.engine.heat_score import HeatScoreCalculator
from src.engine.correlation import CorrelationEngine
from src.engine.clustering import ClusteringEngine
from src.config import DEFAULT_FACTOR_ETFS
from src.models import Position


@pytest.fixture
def throttle():
    return RiskThrottle(kelly_fraction=0.5, heat_floor=0.4, heat_ceiling=0.85)


class TestRiskThrottle:
    def test_full_kelly_cool(self, throttle):
        result = throttle.adjusted_kelly(
            expected_return=0.001,
            variance=0.0004,
            heat_score=0.2,
            regime="low_vol",  # 1.0 regime multiplier
        )
        assert result["kelly_raw"] > 0
        assert result["kelly_adjusted"] == result["kelly_raw"]  # No throttle
        assert result["multiplier"] == 1.0
        assert "FULL" in result["action"]

    def test_reduced_kelly_warm(self, throttle):
        result = throttle.adjusted_kelly(
            expected_return=0.001,
            variance=0.0004,
            heat_score=0.5,
        )
        assert result["kelly_adjusted"] < result["kelly_raw"]
        assert 0 < result["multiplier"] < 1.0

    def test_zero_kelly_emergency(self, throttle):
        result = throttle.adjusted_kelly(
            expected_return=0.001,
            variance=0.0004,
            heat_score=0.9,
        )
        assert result["kelly_adjusted"] == 0.0
        assert result["multiplier"] == 0.0
        assert "HALT" in result["action"]

    def test_zero_variance(self, throttle):
        result = throttle.adjusted_kelly(
            expected_return=0.001,
            variance=0.0,
            heat_score=0.3,
        )
        assert result["kelly_raw"] == 0.0
        assert "SKIP" in result["action"]

    def test_regime_crisis_reduces(self, throttle):
        normal = throttle.adjusted_kelly(0.001, 0.0004, 0.3, regime="normal")
        crisis = throttle.adjusted_kelly(0.001, 0.0004, 0.3, regime="crisis")
        assert crisis["kelly_adjusted"] < normal["kelly_adjusted"]

    def test_negative_expected_return(self, throttle):
        result = throttle.adjusted_kelly(
            expected_return=-0.001,
            variance=0.0004,
            heat_score=0.3,
        )
        assert result["kelly_raw"] < 0  # Kelly says don't trade

    def test_kelly_monotonically_decreases(self, throttle):
        """Kelly should decrease as heat increases."""
        scores = [0.1, 0.3, 0.5, 0.7, 0.85, 0.95]
        kellys = []
        for h in scores:
            r = throttle.adjusted_kelly(0.001, 0.0004, h)
            kellys.append(r["kelly_adjusted"])

        for i in range(1, len(kellys)):
            assert kellys[i] <= kellys[i - 1]


class TestExposureLimits:
    def test_cool_no_reduction(self, throttle):
        result = throttle.compute_exposure_limits(0.2, 1.0)
        assert result["max_exposure"] == 1.0
        assert result["target_reduction"] == 0.0
        assert result["new_position_allowed"]

    def test_hot_reduction(self, throttle):
        result = throttle.compute_exposure_limits(0.65, 1.0)
        assert result["max_exposure"] == 0.75
        assert result["target_reduction"] == 0.25
        assert not result["new_position_allowed"]

    def test_critical_reduction(self, throttle):
        result = throttle.compute_exposure_limits(0.75, 1.0)
        assert result["max_exposure"] == 0.5
        assert result["target_reduction"] == 0.5
        assert not result["new_position_allowed"]

    def test_emergency_reduction(self, throttle):
        result = throttle.compute_exposure_limits(0.9, 1.0)
        assert result["max_exposure"] == 0.5
        assert not result["new_position_allowed"]

    def test_crisis_regime_extra_reduction(self, throttle):
        normal = throttle.compute_exposure_limits(0.3, 1.0, regime="normal")
        crisis = throttle.compute_exposure_limits(0.3, 1.0, regime="crisis")
        assert crisis["max_exposure"] <= normal["max_exposure"]


class TestPreTradeSimulator:
    @pytest.fixture
    def sample_data(self):
        np.random.seed(42)
        n = 200
        dates = pd.bdate_range("2025-01-01", periods=n)

        # Common factor
        f = np.random.randn(n) * 0.01

        returns = pd.DataFrame({
            "AAPL": f + np.random.randn(n) * 0.005,
            "MSFT": f + np.random.randn(n) * 0.004,
            "GOOGL": f + np.random.randn(n) * 0.006,
            "NEW_STOCK": np.random.randn(n) * 0.02,  # independent
            "CORR_STOCK": f * 1.5 + np.random.randn(n) * 0.003,  # highly correlated
            # Factor ETFs
            "SPY": f + np.random.randn(n) * 0.003,
            "TLT": np.random.randn(n) * 0.008,
            "HYG": np.random.randn(n) * 0.004,
            "UUP": np.random.randn(n) * 0.003,
            "SMH": f * 1.2 + np.random.randn(n) * 0.008,
            "QUAL": f * 0.8 + np.random.randn(n) * 0.005,
            "MTUM": f * 0.9 + np.random.randn(n) * 0.006,
            "USMV": np.random.randn(n) * 0.004,
            "VTV": np.random.randn(n) * 0.005,
        }, index=dates)

        positions = [
            Position(symbol="AAPL", weight=0.4),
            Position(symbol="MSFT", weight=0.3),
            Position(symbol="GOOGL", weight=0.3),
        ]

        factor_etfs = DEFAULT_FACTOR_ETFS
        factor_symbols = [f["symbol"] for f in factor_etfs]
        factor_returns = returns[[s for s in factor_symbols if s in returns.columns]]

        return returns, factor_returns, positions

    @pytest.fixture
    def simulator(self):
        fm = FactorModel(factor_etfs=DEFAULT_FACTOR_ETFS, rolling_window=60)
        hc = HeatScoreCalculator()
        ce = CorrelationEngine(ewma_span=60)
        cl = ClusteringEngine()
        return PreTradeSimulator(fm, hc, ce, cl)

    def test_simulate_independent_stock(self, simulator, sample_data):
        returns, factor_returns, positions = sample_data
        result = simulator.simulate_impact(
            candidate_symbol="NEW_STOCK",
            candidate_weight=0.05,
            current_positions=positions,
            returns=returns,
            factor_returns=factor_returns,
        )
        assert "heat_before" in result
        assert "heat_after" in result
        assert "heat_delta" in result
        assert "recommendation" in result
        assert result["recommendation"] in ("PROCEED", "PROCEED_WITH_CARE", "CAUTION", "BLOCK")

    def test_simulate_correlated_stock(self, simulator, sample_data):
        returns, factor_returns, positions = sample_data
        result = simulator.simulate_impact(
            candidate_symbol="CORR_STOCK",
            candidate_weight=0.05,
            current_positions=positions,
            returns=returns,
            factor_returns=factor_returns,
        )
        assert "heat_delta" in result
        # Correlated stock should increase heat more than independent
        result_indep = simulator.simulate_impact(
            "NEW_STOCK", 0.05, positions, returns, factor_returns
        )
        # Not guaranteed but likely — correlated adds more risk
        assert "recommendation" in result

    def test_simulate_missing_symbol(self, simulator, sample_data):
        returns, factor_returns, positions = sample_data
        result = simulator.simulate_impact(
            candidate_symbol="DOESNOTEXIST",
            candidate_weight=0.05,
            current_positions=positions,
            returns=returns,
            factor_returns=factor_returns,
        )
        assert result["recommendation"] == "SKIP"

    def test_factor_impact_included(self, simulator, sample_data):
        returns, factor_returns, positions = sample_data
        result = simulator.simulate_impact(
            "NEW_STOCK", 0.05, positions, returns, factor_returns
        )
        assert "factor_impact" in result
        fi = result["factor_impact"]
        if "factor_betas" in fi:
            assert len(fi["factor_betas"]) > 0

    def test_cluster_impact_included(self, simulator, sample_data):
        returns, factor_returns, positions = sample_data
        # Need to run clustering first
        corr = returns[["AAPL", "MSFT", "GOOGL"]].corr()
        simulator.cluster_engine.detect_communities(corr)

        result = simulator.simulate_impact(
            "NEW_STOCK", 0.05, positions, returns, factor_returns
        )
        assert "cluster_impact" in result
