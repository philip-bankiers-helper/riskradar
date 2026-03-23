"""Tests for crowding detection engine."""

import numpy as np
import pandas as pd
import pytest

from src.engine.crowding import CrowdingEngine


@pytest.fixture
def engine():
    return CrowdingEngine(lookback=60, history_max=504)


@pytest.fixture
def sample_position_returns():
    np.random.seed(42)
    n = 120
    symbols = ["AAPL", "MSFT", "GOOGL", "NVDA", "AMD"]
    # Highly correlated returns (crowded)
    base = np.random.randn(n) * 0.015
    data = {}
    for s in symbols:
        data[s] = base + np.random.randn(n) * 0.005
    return pd.DataFrame(data)


@pytest.fixture
def sample_factor_returns():
    np.random.seed(42)
    n = 120
    factors = ["TLT", "HYG", "UUP", "SMH", "QUAL", "MTUM", "SPY", "USMV", "VTV"]
    data = {f: np.random.randn(n) * 0.01 for f in factors}
    return pd.DataFrame(data)


@pytest.fixture
def sample_exposures():
    return {
        "rates": -0.2,
        "credit": 0.15,
        "market": 0.8,
        "ai_tech": 0.5,
        "quality": 0.1,
        "momentum": 0.3,
        "dollar": -0.05,
        "value": 0.1,
        "low_vol": -0.1,
    }


class TestCrowdingEngine:
    def test_compute_crowding(self, engine, sample_position_returns,
                              sample_factor_returns, sample_exposures):
        state = engine.compute_crowding(
            position_returns=sample_position_returns,
            factor_returns=sample_factor_returns,
            factor_exposures=sample_exposures,
            avg_correlation=0.6,
        )
        assert 0 <= state.composite_score <= 1
        assert state.level in ("normal", "elevated", "crowded", "extreme")
        assert len(state.signals) == 5
        assert state.most_crowded_factor != ""

    def test_crowding_levels(self, engine, sample_position_returns,
                             sample_factor_returns, sample_exposures):
        # Compute multiple times to build history
        for _ in range(15):
            state = engine.compute_crowding(
                position_returns=sample_position_returns,
                factor_returns=sample_factor_returns,
                factor_exposures=sample_exposures,
                avg_correlation=0.6,
            )
        assert state.level in ("normal", "elevated", "crowded", "extreme")

    def test_alpha_signal_range(self, engine, sample_position_returns,
                                sample_factor_returns, sample_exposures):
        state = engine.compute_crowding(
            position_returns=sample_position_returns,
            factor_returns=sample_factor_returns,
            factor_exposures=sample_exposures,
            avg_correlation=0.6,
        )
        assert -1 <= state.crowding_alpha_signal <= 1

    def test_empty_data(self, engine):
        state = engine.compute_crowding(
            position_returns=pd.DataFrame(),
            factor_returns=pd.DataFrame(),
            factor_exposures={},
            avg_correlation=0.0,
        )
        assert state.composite_score >= 0

    def test_high_correlation_increases_crowding(self, engine, sample_factor_returns,
                                                 sample_exposures):
        np.random.seed(42)
        n = 120
        # Create highly correlated returns
        base = np.random.randn(n) * 0.015
        correlated = pd.DataFrame({
            s: base + np.random.randn(n) * 0.001  # Very correlated
            for s in ["A", "B", "C", "D", "E"]
        })
        uncorrelated = pd.DataFrame({
            s: np.random.randn(n) * 0.015  # Independent
            for s in ["A", "B", "C", "D", "E"]
        })

        high_state = engine.compute_crowding(
            position_returns=correlated,
            factor_returns=sample_factor_returns,
            factor_exposures=sample_exposures,
            avg_correlation=0.9,
        )

        engine2 = CrowdingEngine(lookback=60)
        low_state = engine2.compute_crowding(
            position_returns=uncorrelated,
            factor_returns=sample_factor_returns,
            factor_exposures=sample_exposures,
            avg_correlation=0.1,
        )

        # Higher correlation should produce higher crowding
        assert high_state.composite_score > low_state.composite_score

    def test_signal_names(self, engine, sample_position_returns,
                          sample_factor_returns, sample_exposures):
        state = engine.compute_crowding(
            position_returns=sample_position_returns,
            factor_returns=sample_factor_returns,
            factor_exposures=sample_exposures,
            avg_correlation=0.5,
        )
        signal_names = {s.name for s in state.signals}
        expected = {
            "factor_concentration", "return_dispersion",
            "factor_comovement", "correlation_level", "beta_convergence",
        }
        assert signal_names == expected

    def test_compute_and_store(self, engine, sample_position_returns,
                               sample_factor_returns, sample_exposures):
        state = engine.compute_and_store(
            position_returns=sample_position_returns,
            factor_returns=sample_factor_returns,
            factor_exposures=sample_exposures,
            avg_correlation=0.5,
        )
        d = engine.to_dict()
        assert "composite_score" in d
        assert "signals" in d
        assert len(d["signals"]) == 5

    def test_concentrated_exposure(self, engine, sample_position_returns,
                                   sample_factor_returns):
        # One dominant factor
        concentrated = {"market": 2.5, "rates": 0.01, "credit": 0.01}
        state = engine.compute_crowding(
            position_returns=sample_position_returns,
            factor_returns=sample_factor_returns,
            factor_exposures=concentrated,
            avg_correlation=0.5,
        )
        assert state.most_crowded_factor == "market"
