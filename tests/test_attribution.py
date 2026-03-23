"""Tests for position-level heat attribution."""

import numpy as np
import pandas as pd
import pytest

from src.engine.attribution import PositionAttributor
from src.models import Position


@pytest.fixture
def attributor():
    return PositionAttributor()


@pytest.fixture
def positions():
    return [
        Position(symbol="NVDA", weight=0.20),
        Position(symbol="AAPL", weight=0.15),
        Position(symbol="MSFT", weight=0.15),
        Position(symbol="AMZN", weight=0.10),
        Position(symbol="GOOGL", weight=0.10),
        Position(symbol="META", weight=0.10),
        Position(symbol="TSLA", weight=0.10),
        Position(symbol="AMD", weight=0.05),
        Position(symbol="AVGO", weight=0.05),
    ]


@pytest.fixture
def sample_returns():
    np.random.seed(42)
    n = 252
    symbols = ["NVDA", "AAPL", "MSFT", "AMZN", "GOOGL", "META", "TSLA", "AMD", "AVGO"]
    data = {}
    for sym in symbols:
        data[sym] = np.random.randn(n) * 0.02
    # Make NVDA and AMD more correlated (semiconductor)
    data["AMD"] = data["NVDA"] * 0.7 + np.random.randn(n) * 0.01
    return pd.DataFrame(data)


@pytest.fixture
def correlation_matrix(sample_returns):
    return sample_returns.corr()


@pytest.fixture
def factor_exposures():
    return {
        "NVDA": {"market": 1.2, "ai_tech": 0.8, "momentum": 0.3},
        "AAPL": {"market": 1.0, "quality": 0.5, "momentum": 0.2},
        "MSFT": {"market": 1.1, "ai_tech": 0.4, "quality": 0.3},
        "AMZN": {"market": 1.3, "momentum": 0.4, "credit": -0.2},
        "GOOGL": {"market": 1.1, "ai_tech": 0.3, "quality": 0.2},
        "META": {"market": 1.2, "momentum": 0.5, "ai_tech": 0.2},
        "TSLA": {"market": 1.5, "momentum": 0.8, "dollar": -0.3},
        "AMD": {"market": 1.3, "ai_tech": 0.9, "momentum": 0.4},
        "AVGO": {"market": 1.0, "ai_tech": 0.6, "quality": 0.2},
    }


class TestPositionAttributor:
    def test_attribution_returns_all_positions(
        self, attributor, positions, sample_returns, factor_exposures, correlation_matrix
    ):
        """Attribution should return an entry for each position."""
        result = attributor.compute_attribution(
            positions=positions,
            returns=sample_returns,
            factor_exposures=factor_exposures,
            correlation_matrix=correlation_matrix,
            heat_score=0.65,
            avg_correlation=0.3,
        )
        assert len(result) == len(positions)
        symbols = {a.symbol for a in result}
        assert symbols == {p.symbol for p in positions}

    def test_heat_shares_sum_to_one(
        self, attributor, positions, sample_returns, factor_exposures, correlation_matrix
    ):
        """Heat shares should sum to approximately 1.0."""
        result = attributor.compute_attribution(
            positions=positions,
            returns=sample_returns,
            factor_exposures=factor_exposures,
            correlation_matrix=correlation_matrix,
            heat_score=0.7,
            avg_correlation=0.4,
        )
        total_share = sum(a.heat_share for a in result)
        assert abs(total_share - 1.0) < 0.01

    def test_sorted_by_heat_share_descending(
        self, attributor, positions, sample_returns, factor_exposures, correlation_matrix
    ):
        """Results should be sorted by heat_share in descending order."""
        result = attributor.compute_attribution(
            positions=positions,
            returns=sample_returns,
            factor_exposures=factor_exposures,
            correlation_matrix=correlation_matrix,
            heat_score=0.7,
            avg_correlation=0.4,
        )
        shares = [a.heat_share for a in result]
        assert shares == sorted(shares, reverse=True)

    def test_marginal_heat_proportional(
        self, attributor, positions, sample_returns, factor_exposures, correlation_matrix
    ):
        """Marginal heat should be heat_share * total heat."""
        heat_score = 0.8
        result = attributor.compute_attribution(
            positions=positions,
            returns=sample_returns,
            factor_exposures=factor_exposures,
            correlation_matrix=correlation_matrix,
            heat_score=heat_score,
            avg_correlation=0.5,
        )
        for a in result:
            expected = a.heat_share * heat_score
            assert abs(a.marginal_heat - expected) < 0.001

    def test_dominant_factor_populated(
        self, attributor, positions, sample_returns, factor_exposures, correlation_matrix
    ):
        """Each attribution should have a dominant factor."""
        result = attributor.compute_attribution(
            positions=positions,
            returns=sample_returns,
            factor_exposures=factor_exposures,
            correlation_matrix=correlation_matrix,
            heat_score=0.6,
            avg_correlation=0.3,
        )
        for a in result:
            assert a.dominant_factor != ""
            assert a.dominant_factor_beta != 0.0

    def test_recommendations_valid(
        self, attributor, positions, sample_returns, factor_exposures, correlation_matrix
    ):
        """Recommendations should be valid action strings."""
        valid_actions = {"hold", "reduce", "hedge", "monitor"}
        result = attributor.compute_attribution(
            positions=positions,
            returns=sample_returns,
            factor_exposures=factor_exposures,
            correlation_matrix=correlation_matrix,
            heat_score=0.9,
            avg_correlation=0.6,
        )
        for a in result:
            assert a.recommendation in valid_actions
            assert a.recommendation_reason != ""

    def test_single_position(self, attributor, sample_returns, correlation_matrix):
        """Should handle a single position gracefully."""
        positions = [Position(symbol="NVDA", weight=1.0)]
        result = attributor.compute_attribution(
            positions=positions,
            returns=sample_returns,
            factor_exposures={"NVDA": {"market": 1.0}},
            correlation_matrix=correlation_matrix,
            heat_score=0.5,
            avg_correlation=0.0,
        )
        assert len(result) == 1
        assert abs(result[0].heat_share - 1.0) < 0.01

    def test_equal_weights(self, attributor, sample_returns, correlation_matrix):
        """Equal-weight portfolio should produce relatively balanced attribution."""
        n = 4
        syms = ["NVDA", "AAPL", "MSFT", "AMZN"]
        positions = [Position(symbol=s, weight=1.0 / n) for s in syms]
        factor_exp = {s: {"market": 1.0} for s in syms}

        result = attributor.compute_attribution(
            positions=positions,
            returns=sample_returns[syms],
            factor_exposures=factor_exp,
            correlation_matrix=sample_returns[syms].corr(),
            heat_score=0.5,
            avg_correlation=0.3,
        )
        shares = [a.heat_share for a in result]
        # With equal weights and same factor exposure, shares should be somewhat balanced
        assert max(shares) < 0.5  # No single position dominates

    def test_empty_positions(self, attributor, sample_returns, correlation_matrix):
        """Empty positions list should return empty result."""
        result = attributor.compute_attribution(
            positions=[],
            returns=sample_returns,
            factor_exposures={},
            correlation_matrix=correlation_matrix,
            heat_score=0.5,
            avg_correlation=0.3,
        )
        assert result == []

    def test_zero_exposures(self, attributor, positions, sample_returns, correlation_matrix):
        """Should handle zero factor exposures gracefully."""
        factor_exp = {p.symbol: {"market": 0.0} for p in positions}
        result = attributor.compute_attribution(
            positions=positions,
            returns=sample_returns,
            factor_exposures=factor_exp,
            correlation_matrix=correlation_matrix,
            heat_score=0.5,
            avg_correlation=0.3,
        )
        assert len(result) == len(positions)
        total = sum(a.heat_share for a in result)
        assert abs(total - 1.0) < 0.01

    def test_cool_heat_recommends_hold(
        self, attributor, positions, sample_returns, factor_exposures, correlation_matrix
    ):
        """In cool conditions, all positions should be 'hold'."""
        result = attributor.compute_attribution(
            positions=positions,
            returns=sample_returns,
            factor_exposures=factor_exposures,
            correlation_matrix=correlation_matrix,
            heat_score=0.3,
            avg_correlation=0.2,
        )
        for a in result:
            assert a.recommendation == "hold"

    def test_high_heat_triggers_reduce(
        self, attributor, sample_returns, correlation_matrix
    ):
        """In emergency conditions, high-contribution positions should get 'reduce'."""
        # Create a concentrated portfolio
        positions = [
            Position(symbol="NVDA", weight=0.60),
            Position(symbol="AAPL", weight=0.20),
            Position(symbol="MSFT", weight=0.20),
        ]
        factor_exp = {
            "NVDA": {"market": 1.5, "ai_tech": 1.0},
            "AAPL": {"market": 1.0, "quality": 0.3},
            "MSFT": {"market": 1.0, "quality": 0.3},
        }
        result = attributor.compute_attribution(
            positions=positions,
            returns=sample_returns[["NVDA", "AAPL", "MSFT"]],
            factor_exposures=factor_exp,
            correlation_matrix=sample_returns[["NVDA", "AAPL", "MSFT"]].corr(),
            heat_score=0.95,
            avg_correlation=0.7,
        )
        # The most concentrated position should get reduce
        top = result[0]
        assert top.recommendation in ("reduce", "hedge")
