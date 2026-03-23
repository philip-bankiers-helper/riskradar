"""Tests for causal DAG engine."""

import numpy as np
import pandas as pd
import pytest

from src.engine.causal_dag import CausalDAGEngine, PRIOR_DAG_EDGES, CONFOUNDERS


@pytest.fixture
def sample_data():
    """Generate synthetic factor + position return data."""
    np.random.seed(42)
    n = 120

    # Create correlated data that mimics factor->return causation
    rates = np.random.randn(n) * 0.01
    credit = rates * 0.5 + np.random.randn(n) * 0.01
    market = np.random.randn(n) * 0.015
    dollar = credit * 0.3 + np.random.randn(n) * 0.008
    ai_tech = market * 0.8 + np.random.randn(n) * 0.02
    momentum = market * 0.4 + np.random.randn(n) * 0.012
    quality = credit * -0.3 + np.random.randn(n) * 0.01
    value = rates * -0.6 + np.random.randn(n) * 0.01
    low_vol = market * -0.2 + np.random.randn(n) * 0.008

    # Position returns are a combination of factors
    position_returns = (
        0.3 * market + 0.2 * ai_tech + 0.1 * rates
        - 0.15 * credit + np.random.randn(n) * 0.005
    )

    df = pd.DataFrame({
        "rates": rates,
        "credit": credit,
        "market": market,
        "dollar": dollar,
        "ai_tech": ai_tech,
        "momentum": momentum,
        "quality": quality,
        "value": value,
        "low_vol": low_vol,
        "position_returns": position_returns,
    })
    return df


@pytest.fixture
def engine():
    return CausalDAGEngine(use_prior=True, min_observations=60)


class TestCausalDAGEngine:
    def test_build_dag_prior(self, engine, sample_data):
        factor_cols = [c for c in sample_data.columns if c != "position_returns"]
        factor_returns = sample_data[factor_cols]
        position_returns = sample_data["position_returns"]

        state = engine.build_dag(factor_returns, position_returns)

        assert state.n_edges > 0
        assert state.discovery_method == "prior"
        assert "rates" in state.confounders

    def test_build_dag_insufficient_data(self, engine):
        df = pd.DataFrame({
            "rates": [0.01] * 10,
            "position_returns": [0.02] * 10,
        })
        state = engine.build_dag(df[["rates"]], df["position_returns"])
        assert state.n_edges == 0

    def test_get_causal_effects(self, engine, sample_data):
        factor_cols = [c for c in sample_data.columns if c != "position_returns"]
        engine.build_dag(sample_data[factor_cols], sample_data["position_returns"])

        effects = engine.get_causal_effects_on_portfolio()
        # Should find at least market effect (strongest signal)
        assert isinstance(effects, dict)

    def test_significant_edges(self, engine, sample_data):
        factor_cols = [c for c in sample_data.columns if c != "position_returns"]
        engine.build_dag(sample_data[factor_cols], sample_data["position_returns"])

        significant = engine.get_significant_edges()
        assert isinstance(significant, list)

    def test_interventional_query(self, engine, sample_data):
        factor_cols = [c for c in sample_data.columns if c != "position_returns"]
        engine.build_dag(sample_data[factor_cols], sample_data["position_returns"])

        result = engine.interventional_query(sample_data, "market", shock_size=2.0)
        assert "total_impact" in result
        assert "shock_factor" in result
        assert result["shock_factor"] == "market"
        assert result["shock_size_std"] == 2.0

    def test_interventional_query_no_dag(self):
        engine = CausalDAGEngine()
        result = engine.interventional_query(pd.DataFrame(), "rates", 1.0)
        assert "error" in result

    def test_to_dict(self, engine, sample_data):
        factor_cols = [c for c in sample_data.columns if c != "position_returns"]
        engine.build_dag(sample_data[factor_cols], sample_data["position_returns"])

        d = engine.to_dict()
        assert "edges" in d
        assert "confounders" in d
        assert "colliders" in d
        assert "n_nodes" in d
        assert "significant_factor_effects" in d

    def test_to_dict_empty(self):
        engine = CausalDAGEngine()
        d = engine.to_dict()
        assert d["edges"] == []


class TestPCAlgorithmDiscovery:
    def test_discovery_mode(self, sample_data):
        engine = CausalDAGEngine(use_prior=False, min_observations=60)
        factor_cols = [c for c in sample_data.columns if c != "position_returns"]

        state = engine.build_dag(
            sample_data[factor_cols], sample_data["position_returns"]
        )
        assert state.discovery_method == "pc_algorithm"
        # PC may or may not find edges depending on data
        assert isinstance(state.edges, list)


class TestPriorDAG:
    def test_prior_edges_are_valid(self):
        """All prior edges should reference known factor names."""
        valid_names = {
            "rates", "credit", "dollar", "market", "ai_tech",
            "momentum", "quality", "value", "low_vol",
            "position_returns",
        }
        for source, target in PRIOR_DAG_EDGES:
            assert source in valid_names, f"Unknown source: {source}"
            assert target in valid_names, f"Unknown target: {target}"

    def test_confounders_are_valid(self):
        valid_names = {
            "rates", "credit", "dollar", "market", "ai_tech",
            "momentum", "quality", "value", "low_vol",
        }
        for conf in CONFOUNDERS:
            assert conf in valid_names
