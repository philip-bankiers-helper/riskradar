"""Tests for dashboard module."""

import json
import pytest
from src.dashboard.charts import (
    heat_gauge_config,
    heat_history_config,
    correlation_heatmap_config,
    mst_network_config,
    factor_bars_config,
    hrp_comparison_config,
)


class TestHeatGauge:
    def test_generates_valid_json(self):
        result = heat_gauge_config(0.65, "hot")
        config = json.loads(result)
        assert config["data"][0]["type"] == "indicator"
        assert config["data"][0]["value"] == 0.65

    def test_all_levels(self):
        for level in ["cool", "warm", "hot", "critical", "emergency"]:
            result = heat_gauge_config(0.5, level)
            config = json.loads(result)
            assert config["data"][0]["gauge"]["bar"]["color"]


class TestHeatHistory:
    def test_empty_history(self):
        result = heat_history_config([])
        config = json.loads(result)
        assert config["data"] == []

    def test_with_data(self):
        history = [
            {"timestamp": "2026-03-15T10:00:00", "score": 0.3, "level": "cool"},
            {"timestamp": "2026-03-15T10:05:00", "score": 0.5, "level": "warm"},
            {"timestamp": "2026-03-15T10:10:00", "score": 0.7, "level": "hot"},
        ]
        result = heat_history_config(history)
        config = json.loads(result)
        assert len(config["data"]) == 1
        assert len(config["data"][0]["x"]) == 3


class TestCorrelationHeatmap:
    def test_empty(self):
        result = correlation_heatmap_config({})
        config = json.loads(result)
        assert config["data"] == []

    def test_with_matrix(self):
        matrix = {
            "AAPL": {"AAPL": 1.0, "MSFT": 0.7},
            "MSFT": {"AAPL": 0.7, "MSFT": 1.0},
        }
        result = correlation_heatmap_config(matrix)
        config = json.loads(result)
        assert config["data"][0]["type"] == "heatmap"
        assert len(config["data"][0]["z"]) == 2


class TestMSTNetwork:
    def test_empty(self):
        result = mst_network_config([], [])
        config = json.loads(result)
        assert config["data"] == []

    def test_with_edges(self):
        edges = [
            {"source": "AAPL", "target": "MSFT", "distance": 0.3, "correlation": 0.7},
            {"source": "MSFT", "target": "GOOGL", "distance": 0.4, "correlation": 0.6},
        ]
        communities = [
            {"cluster_id": 0, "members": ["AAPL", "MSFT", "GOOGL"], "size": 3},
        ]
        weights = {"AAPL": 0.15, "MSFT": 0.15, "GOOGL": 0.10}
        result = mst_network_config(edges, communities, weights)
        config = json.loads(result)
        assert len(config["data"]) == 2  # edges + nodes


class TestFactorBars:
    def test_empty(self):
        result = factor_bars_config([])
        config = json.loads(result)
        assert config["data"] == []

    def test_with_exposures(self):
        exposures = [
            {"factor_name": "rates", "total_exposure": -0.5},
            {"factor_name": "credit", "total_exposure": 0.3},
            {"factor_name": "market", "total_exposure": 1.2},
        ]
        result = factor_bars_config(exposures)
        config = json.loads(result)
        assert config["data"][0]["type"] == "bar"
        assert len(config["data"][0]["x"]) == 3


class TestHRPComparison:
    def test_empty(self):
        result = hrp_comparison_config({}, {})
        config = json.loads(result)
        assert config["data"] == []

    def test_comparison(self):
        actual = {"AAPL": 0.15, "MSFT": 0.15, "NVDA": 0.20}
        hrp = {"AAPL": 0.20, "MSFT": 0.10, "NVDA": 0.15}
        result = hrp_comparison_config(actual, hrp)
        config = json.loads(result)
        assert len(config["data"]) == 2  # actual + hrp bars
        assert config["layout"]["barmode"] == "group"
