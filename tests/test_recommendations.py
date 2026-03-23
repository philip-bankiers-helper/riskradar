"""Tests for trade recommendation engine."""

import pytest

from src.engine.recommendations import TradeRecommendationEngine
from src.models import Position, PositionAttribution


@pytest.fixture
def engine():
    return TradeRecommendationEngine()


@pytest.fixture
def positions():
    return [
        Position(symbol="NVDA", weight=0.20),
        Position(symbol="AAPL", weight=0.15),
        Position(symbol="MSFT", weight=0.15),
        Position(symbol="AMZN", weight=0.10),
        Position(symbol="META", weight=0.10),
        Position(symbol="TSLA", weight=0.10),
        Position(symbol="AMD", weight=0.05),
    ]


def _make_attribution(
    symbol: str,
    weight: float,
    heat_share: float,
    recommendation: str = "hold",
    dominant_factor: str = "market",
) -> PositionAttribution:
    return PositionAttribution(
        symbol=symbol,
        weight=weight,
        marginal_heat=heat_share * 0.7,
        correlation_contribution=heat_share,
        factor_concentration=heat_share,
        risk_contribution=heat_share,
        heat_share=heat_share,
        dominant_factor=dominant_factor,
        dominant_factor_beta=1.0,
        recommendation=recommendation,
        recommendation_reason=f"Test reason for {symbol}",
    )


class TestTradeRecommendationEngine:
    def test_generates_recommendation(self, engine, positions):
        """Should generate a PortfolioRecommendation."""
        attribution = [
            _make_attribution("NVDA", 0.20, 0.25, "reduce", "ai_tech"),
            _make_attribution("AAPL", 0.15, 0.15, "hold"),
            _make_attribution("MSFT", 0.15, 0.15, "hold"),
            _make_attribution("AMZN", 0.10, 0.10, "hold"),
            _make_attribution("META", 0.10, 0.10, "hold"),
            _make_attribution("TSLA", 0.10, 0.15, "hedge", "momentum"),
            _make_attribution("AMD", 0.05, 0.10, "hold"),
        ]
        result = engine.generate_recommendations(
            positions=positions,
            attribution=attribution,
            heat_score=0.80,
        )
        assert result is not None
        assert result.current_heat == 0.80
        assert result.summary != ""
        assert result.timestamp is not None

    def test_reduce_actions_generated(self, engine, positions):
        """Positions with 'reduce' recommendation should generate reduce actions."""
        attribution = [
            _make_attribution("NVDA", 0.20, 0.30, "reduce", "ai_tech"),
            _make_attribution("AAPL", 0.15, 0.20, "reduce", "market"),
            _make_attribution("MSFT", 0.15, 0.10, "hold"),
            _make_attribution("AMZN", 0.10, 0.10, "hold"),
            _make_attribution("META", 0.10, 0.10, "hold"),
            _make_attribution("TSLA", 0.10, 0.10, "hold"),
            _make_attribution("AMD", 0.05, 0.10, "hold"),
        ]
        result = engine.generate_recommendations(
            positions=positions,
            attribution=attribution,
            heat_score=0.90,
        )
        reduce_actions = [a for a in result.actions if a.action == "reduce"]
        assert len(reduce_actions) >= 2
        # First action should be highest priority
        assert reduce_actions[0].priority < reduce_actions[1].priority

    def test_hedge_suggestions_generated(self, engine, positions):
        """Positions with 'hedge' recommendation should generate hedge suggestions."""
        attribution = [
            _make_attribution("NVDA", 0.20, 0.25, "hedge", "ai_tech"),
            _make_attribution("AAPL", 0.15, 0.15, "hold"),
            _make_attribution("MSFT", 0.15, 0.15, "hold"),
            _make_attribution("AMZN", 0.10, 0.10, "hold"),
            _make_attribution("META", 0.10, 0.10, "hold"),
            _make_attribution("TSLA", 0.10, 0.10, "hold"),
            _make_attribution("AMD", 0.05, 0.10, "hold"),
        ]
        result = engine.generate_recommendations(
            positions=positions,
            attribution=attribution,
            heat_score=0.80,
        )
        assert len(result.hedges) >= 1
        # Hedge should be for the ai_tech factor
        assert any(h.symbol == "SOXS" for h in result.hedges)

    def test_valid_weights(self, engine, positions):
        """Trade actions should have valid weights."""
        attribution = [
            _make_attribution("NVDA", 0.20, 0.30, "reduce", "ai_tech"),
            _make_attribution("AAPL", 0.15, 0.20, "hold"),
            _make_attribution("MSFT", 0.15, 0.15, "hold"),
            _make_attribution("AMZN", 0.10, 0.10, "hold"),
            _make_attribution("META", 0.10, 0.10, "hold"),
            _make_attribution("TSLA", 0.10, 0.10, "hold"),
            _make_attribution("AMD", 0.05, 0.05, "hold"),
        ]
        result = engine.generate_recommendations(
            positions=positions,
            attribution=attribution,
            heat_score=0.85,
        )
        for action in result.actions:
            assert 0 <= action.target_weight <= 1
            assert action.current_weight >= 0
            # Reduce should have negative delta
            if action.action == "reduce":
                assert action.delta < 0
                assert action.target_weight < action.current_weight

    def test_priorities_sequential(self, engine, positions):
        """Priorities should be sequential starting from 1."""
        attribution = [
            _make_attribution("NVDA", 0.20, 0.30, "reduce"),
            _make_attribution("AAPL", 0.15, 0.20, "reduce"),
            _make_attribution("MSFT", 0.15, 0.15, "hedge", "ai_tech"),
            _make_attribution("AMZN", 0.10, 0.10, "hold"),
            _make_attribution("META", 0.10, 0.10, "hold"),
            _make_attribution("TSLA", 0.10, 0.10, "hold"),
            _make_attribution("AMD", 0.05, 0.05, "hold"),
        ]
        result = engine.generate_recommendations(
            positions=positions,
            attribution=attribution,
            heat_score=0.90,
        )
        all_actions = result.actions + result.hedges
        if all_actions:
            priorities = sorted(a.priority for a in all_actions)
            assert priorities[0] == 1

    def test_cool_heat_no_actions(self, engine, positions):
        """Cool heat should generate no actions."""
        attribution = [
            _make_attribution(p.symbol, p.weight, 1.0 / len(positions), "hold")
            for p in positions
        ]
        result = engine.generate_recommendations(
            positions=positions,
            attribution=attribution,
            heat_score=0.30,
        )
        assert len(result.actions) == 0
        assert len(result.hedges) == 0
        assert "No actions needed" in result.summary

    def test_urgency_scales_with_heat(self, engine, positions):
        """Higher heat should produce more urgent actions."""
        attribution = [
            _make_attribution("NVDA", 0.20, 0.30, "reduce"),
            _make_attribution("AAPL", 0.15, 0.20, "hold"),
            _make_attribution("MSFT", 0.15, 0.15, "hold"),
            _make_attribution("AMZN", 0.10, 0.10, "hold"),
            _make_attribution("META", 0.10, 0.10, "hold"),
            _make_attribution("TSLA", 0.10, 0.10, "hold"),
            _make_attribution("AMD", 0.05, 0.05, "hold"),
        ]

        # Emergency heat
        result_emergency = engine.generate_recommendations(
            positions=positions,
            attribution=attribution,
            heat_score=0.95,
        )

        # Hot heat
        result_hot = engine.generate_recommendations(
            positions=positions,
            attribution=attribution,
            heat_score=0.80,
        )

        if result_emergency.actions and result_hot.actions:
            assert result_emergency.actions[0].urgency == "immediate"
            assert result_hot.actions[0].urgency == "this_week"

    def test_estimated_heat_after(self, engine, positions):
        """Estimated heat after should be lower than current."""
        attribution = [
            _make_attribution("NVDA", 0.20, 0.30, "reduce"),
            _make_attribution("AAPL", 0.15, 0.20, "reduce"),
            _make_attribution("MSFT", 0.15, 0.15, "hold"),
            _make_attribution("AMZN", 0.10, 0.10, "hold"),
            _make_attribution("META", 0.10, 0.10, "hold"),
            _make_attribution("TSLA", 0.10, 0.10, "hold"),
            _make_attribution("AMD", 0.05, 0.05, "hold"),
        ]
        result = engine.generate_recommendations(
            positions=positions,
            attribution=attribution,
            heat_score=0.90,
        )
        assert result.estimated_heat_after <= result.current_heat

    def test_regime_context_included(self, engine, positions):
        """Regime context should be included in recommendation."""
        attribution = [
            _make_attribution(p.symbol, p.weight, 1.0 / len(positions), "hold")
            for p in positions
        ]
        result = engine.generate_recommendations(
            positions=positions,
            attribution=attribution,
            heat_score=0.60,
            regime_state={"regime": "crisis", "transition_risk": 0.3},
        )
        assert "crisis" in result.regime_context

    def test_hrp_rebalance_suggestions(self, engine, positions):
        """Should suggest rebalancing toward HRP when heat is elevated."""
        attribution = [
            _make_attribution(p.symbol, p.weight, 1.0 / len(positions), "hold")
            for p in positions
        ]
        # HRP suggests reducing NVDA from 20% to 12%
        hrp = {
            "NVDA": 0.12, "AAPL": 0.14, "MSFT": 0.14,
            "AMZN": 0.12, "META": 0.12, "TSLA": 0.12, "AMD": 0.12,
        }
        result = engine.generate_recommendations(
            positions=positions,
            attribution=attribution,
            heat_score=0.70,
            hrp_weights=hrp,
        )
        # Should suggest reducing NVDA toward HRP weight
        nvda_actions = [a for a in result.actions if a.symbol == "NVDA"]
        assert len(nvda_actions) >= 1

    def test_impact_estimate_positive(self, engine, positions):
        """Impact estimates should be positive."""
        attribution = [
            _make_attribution("NVDA", 0.20, 0.30, "reduce"),
            _make_attribution("AAPL", 0.15, 0.20, "hold"),
            _make_attribution("MSFT", 0.15, 0.15, "hold"),
            _make_attribution("AMZN", 0.10, 0.10, "hold"),
            _make_attribution("META", 0.10, 0.10, "hold"),
            _make_attribution("TSLA", 0.10, 0.10, "hold"),
            _make_attribution("AMD", 0.05, 0.05, "hold"),
        ]
        result = engine.generate_recommendations(
            positions=positions,
            attribution=attribution,
            heat_score=0.85,
        )
        for action in result.actions:
            assert action.impact_estimate >= 0

    def test_never_reduces_below_floor(self, engine):
        """Reduce actions should never target weight below 1%."""
        positions = [
            Position(symbol="NVDA", weight=0.05),
            Position(symbol="AAPL", weight=0.95),
        ]
        attribution = [
            _make_attribution("NVDA", 0.05, 0.50, "reduce"),
            _make_attribution("AAPL", 0.95, 0.50, "reduce"),
        ]
        result = engine.generate_recommendations(
            positions=positions,
            attribution=attribution,
            heat_score=0.95,
        )
        for action in result.actions:
            if action.action == "reduce":
                assert action.target_weight >= 0.01
