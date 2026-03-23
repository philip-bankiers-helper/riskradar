"""Tests for clustering engine — Louvain, HRP, MST, migration detection."""

import numpy as np
import pandas as pd
import pytest

from src.engine.clustering import ClusteringEngine


@pytest.fixture
def corr_matrix_clustered():
    """Correlation matrix with two clear clusters."""
    # Cluster 1: A, B, C (high correlation)
    # Cluster 2: D, E (high correlation)
    # Low correlation between clusters
    symbols = ["A", "B", "C", "D", "E"]
    n = len(symbols)
    corr = np.eye(n)

    # Cluster 1 correlations
    corr[0, 1] = corr[1, 0] = 0.85
    corr[0, 2] = corr[2, 0] = 0.80
    corr[1, 2] = corr[2, 1] = 0.75

    # Cluster 2 correlations
    corr[3, 4] = corr[4, 3] = 0.90

    # Cross-cluster (low)
    corr[0, 3] = corr[3, 0] = 0.15
    corr[0, 4] = corr[4, 0] = 0.10
    corr[1, 3] = corr[3, 1] = 0.20
    corr[1, 4] = corr[4, 1] = 0.12
    corr[2, 3] = corr[3, 2] = 0.18
    corr[2, 4] = corr[4, 2] = 0.14

    return pd.DataFrame(corr, index=symbols, columns=symbols)


@pytest.fixture
def sample_returns():
    """Generate return data with two clusters."""
    np.random.seed(42)
    n_days = 200
    dates = pd.bdate_range("2025-01-01", periods=n_days)

    # Cluster 1: driven by factor 1
    f1 = np.random.randn(n_days) * 0.01
    # Cluster 2: driven by factor 2
    f2 = np.random.randn(n_days) * 0.01

    returns = pd.DataFrame({
        "A": f1 + np.random.randn(n_days) * 0.003,
        "B": f1 + np.random.randn(n_days) * 0.004,
        "C": f1 + np.random.randn(n_days) * 0.005,
        "D": f2 + np.random.randn(n_days) * 0.003,
        "E": f2 + np.random.randn(n_days) * 0.004,
    }, index=dates)

    return returns


@pytest.fixture
def engine():
    return ClusteringEngine(correlation_threshold=0.4, migration_lookback=5)


class TestLouvainCommunities:
    def test_detect_communities(self, engine, corr_matrix_clustered):
        communities = engine.detect_communities(corr_matrix_clustered)
        assert len(communities) == 5  # all symbols assigned
        assert all(isinstance(v, int) for v in communities.values())

    def test_two_clusters_detected(self, engine, corr_matrix_clustered):
        communities = engine.detect_communities(corr_matrix_clustered)
        # A, B, C should be in same cluster
        assert communities["A"] == communities["B"] == communities["C"]
        # D, E should be in same cluster
        assert communities["D"] == communities["E"]
        # Different clusters
        assert communities["A"] != communities["D"]

    def test_community_summary(self, engine, corr_matrix_clustered):
        communities = engine.detect_communities(corr_matrix_clustered)
        summary = engine.get_community_summary(communities)
        assert len(summary) == 2
        sizes = sorted([s["size"] for s in summary])
        assert sizes == [2, 3]

    def test_modularity(self, engine, corr_matrix_clustered):
        communities = engine.detect_communities(corr_matrix_clustered)
        mod = engine.get_modularity(corr_matrix_clustered, communities)
        assert mod > 0  # Good partition should have positive modularity

    def test_empty_matrix(self, engine):
        empty = pd.DataFrame()
        communities = engine.detect_communities(empty)
        assert communities == {}

    def test_single_asset(self, engine):
        single = pd.DataFrame([[1.0]], index=["A"], columns=["A"])
        communities = engine.detect_communities(single)
        assert communities == {}

    def test_n_communities(self, engine, corr_matrix_clustered):
        engine.detect_communities(corr_matrix_clustered)
        assert engine.n_communities == 2


class TestMST:
    def test_compute_mst(self, engine, corr_matrix_clustered):
        edges = engine.compute_mst(corr_matrix_clustered)
        # MST has n-1 edges
        assert len(edges) == 4  # 5 assets - 1

    def test_mst_structure(self, engine, corr_matrix_clustered):
        edges = engine.compute_mst(corr_matrix_clustered)
        for e in edges:
            assert "source" in e
            assert "target" in e
            assert "distance" in e
            assert "correlation" in e
            assert e["distance"] >= 0

    def test_mst_empty(self, engine):
        edges = engine.compute_mst(pd.DataFrame())
        assert edges == []


class TestMigrationDetection:
    def test_no_migration_on_first_run(self, engine, corr_matrix_clustered):
        engine.detect_communities(corr_matrix_clustered)
        migrations = engine.detect_migrations()
        assert migrations == []  # Need at least 2 snapshots

    def test_no_migration_stable_clusters(self, engine, corr_matrix_clustered):
        # Run same data twice — no migration
        engine.detect_communities(corr_matrix_clustered)
        engine.detect_communities(corr_matrix_clustered)
        migrations = engine.detect_migrations()
        assert len(migrations) == 0

    def test_migration_detected(self, engine, corr_matrix_clustered):
        # First snapshot: normal clusters
        engine.detect_communities(corr_matrix_clustered)

        # Second snapshot: D joins cluster 1
        modified = corr_matrix_clustered.copy()
        modified.loc["D", "A"] = modified.loc["A", "D"] = 0.85
        modified.loc["D", "B"] = modified.loc["B", "D"] = 0.80
        modified.loc["D", "C"] = modified.loc["C", "D"] = 0.75
        modified.loc["D", "E"] = modified.loc["E", "D"] = 0.10  # break old cluster
        engine.detect_communities(modified)

        migrations = engine.detect_migrations()
        # D should have migrated
        migrated_symbols = [m["symbol"] for m in migrations]
        assert "D" in migrated_symbols

    def test_migration_risk_score(self, engine, corr_matrix_clustered):
        engine.detect_communities(corr_matrix_clustered)
        score = engine.get_migration_risk_score()
        assert 0.0 <= score <= 1.0


class TestHRP:
    def test_compute_hrp(self, engine, sample_returns):
        result = engine.compute_hrp(sample_returns)
        weights = result["weights"]
        assert not weights.empty
        assert len(weights) == sample_returns.shape[1]
        # Weights should sum to ~1
        assert abs(weights.sum() - 1.0) < 0.01
        # All weights positive
        assert all(w >= 0 for w in weights)

    def test_hrp_concentration(self, engine, sample_returns):
        result = engine.compute_hrp(sample_returns)
        conc = engine.compute_hrp_concentration(result["weights"])
        assert 0.0 < conc <= 1.0
        # 5 equal weights → HHI = 0.2; should be somewhere around there
        assert conc < 0.5  # Not overly concentrated

    def test_hrp_empty(self, engine):
        empty = pd.DataFrame()
        result = engine.compute_hrp(empty)
        assert result["weights"].empty

    def test_last_hrp_weights(self, engine, sample_returns):
        engine.compute_hrp(sample_returns)
        assert engine.last_hrp_weights is not None


class TestGraphConstruction:
    def test_corr_to_graph(self, engine, corr_matrix_clustered):
        G = engine._corr_to_graph(corr_matrix_clustered)
        assert G.number_of_nodes() == 5
        # Only edges above threshold (0.4)
        for u, v, d in G.edges(data=True):
            assert d["weight"] >= 0.4

    def test_threshold_filtering(self, corr_matrix_clustered):
        # High threshold — fewer edges
        engine_high = ClusteringEngine(correlation_threshold=0.8)
        G = engine_high._corr_to_graph(corr_matrix_clustered)
        edges_high = G.number_of_edges()

        # Low threshold — more edges
        engine_low = ClusteringEngine(correlation_threshold=0.1)
        G = engine_low._corr_to_graph(corr_matrix_clustered)
        edges_low = G.number_of_edges()

        assert edges_low >= edges_high
