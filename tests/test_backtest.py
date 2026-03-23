"""Tests for backtest engine (unit tests — no network calls)."""

import numpy as np
import pandas as pd
import pytest

from src.engine.backtest import (
    BacktestEngine,
    BacktestResult,
    CrisisEvent,
    FullBacktestResult,
    CRISIS_EVENTS,
)


@pytest.fixture
def sample_returns():
    """Create synthetic returns covering a 'crisis' period."""
    np.random.seed(42)
    dates = pd.bdate_range("2020-01-01", "2020-06-30")
    symbols = ["AAPL", "MSFT", "GOOGL", "NVDA", "AMD",
               "TLT", "HYG", "UUP", "SMH", "SPY"]
    n = len(dates)

    data = {}
    for s in symbols:
        # Normal returns with a crisis dip in Feb-Mar 2020
        rets = np.random.randn(n) * 0.015
        # Add crisis: big negative returns in Feb-Mar 2020
        crisis_start = 30  # ~Feb 10
        crisis_end = 55  # ~Mar 20
        rets[crisis_start:crisis_end] -= 0.03  # Extra negative
        # Increase correlation during crisis
        if s != "TLT":
            base_crisis = np.random.randn(crisis_end - crisis_start) * 0.02
            rets[crisis_start:crisis_end] += base_crisis * 0.5
        data[s] = rets

    return pd.DataFrame(data, index=dates)


@pytest.fixture
def test_crisis():
    return CrisisEvent(
        name="Test Crisis",
        start_date="2020-02-19",
        peak_date="2020-03-23",
        end_date="2020-04-17",
        description="Test crisis for unit testing",
    )


class TestBacktestEngine:
    def test_run_crisis_backtest(self, sample_returns, test_crisis):
        engine = BacktestEngine(
            position_symbols=["AAPL", "MSFT", "GOOGL", "NVDA", "AMD"],
            factor_symbols=["TLT", "HYG", "UUP", "SMH", "SPY"],
        )

        result = engine.run_crisis_backtest(test_crisis, sample_returns)

        assert isinstance(result, BacktestResult)
        assert result.crisis.name == "Test Crisis"
        assert result.heat_during >= 0
        assert result.max_drawdown <= 0  # Drawdown should be negative

    def test_run_crisis_missing_symbols(self):
        engine = BacktestEngine(
            position_symbols=["ZZZZZ"],  # Non-existent
            factor_symbols=["TLT"],
        )
        # Returns with no matching symbols — should return empty result gracefully
        dummy = pd.DataFrame({"TLT": np.random.randn(100) * 0.01})
        result = engine.run_crisis_backtest(
            CrisisEvent("X", "2020-01-01", "2020-02-01", "2020-03-01", "X"),
            dummy,
        )
        assert result.heat_during == 0  # No data to compute

    def test_run_full_backtest(self, sample_returns, test_crisis):
        engine = BacktestEngine(
            position_symbols=["AAPL", "MSFT", "GOOGL", "NVDA", "AMD"],
            factor_symbols=["TLT", "HYG", "UUP", "SMH", "SPY"],
        )

        result = engine.run_full_backtest(
            returns=sample_returns,
            crises=[test_crisis],
        )

        assert isinstance(result, FullBacktestResult)
        assert len(result.crisis_results) == 1
        assert 0 <= result.overall_accuracy <= 1

    def test_to_dict(self, sample_returns, test_crisis):
        engine = BacktestEngine(
            position_symbols=["AAPL", "MSFT", "GOOGL", "NVDA", "AMD"],
            factor_symbols=["TLT", "HYG", "UUP", "SMH", "SPY"],
        )

        result = engine.run_full_backtest(
            returns=sample_returns,
            crises=[test_crisis],
        )

        d = engine.to_dict(result)
        assert "overall_accuracy" in d
        assert "crises" in d
        assert len(d["crises"]) == 1
        assert "heat_before" in d["crises"][0]
        assert "detected" in d["crises"][0]


class TestCrisisEvents:
    def test_known_crises_have_valid_dates(self):
        for crisis in CRISIS_EVENTS:
            start = pd.Timestamp(crisis.start_date)
            peak = pd.Timestamp(crisis.peak_date)
            end = pd.Timestamp(crisis.end_date)
            assert start < peak, f"{crisis.name}: start should be before peak"
            assert peak <= end, f"{crisis.name}: peak should be before end"

    def test_crisis_events_not_empty(self):
        assert len(CRISIS_EVENTS) >= 3
