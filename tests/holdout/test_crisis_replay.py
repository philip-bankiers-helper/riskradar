"""Private loop-evaluation tests. Autonomous agents must not read or edit this file."""

import numpy as np
import pandas as pd

from src.engine.backtest import BacktestEngine, BacktestResult, CrisisEvent, CRISIS_EVENTS


def _sample_returns() -> pd.DataFrame:
    rng = np.random.default_rng(42)
    dates = pd.bdate_range("2019-10-01", "2020-05-29")
    symbols = ["AAPL", "MSFT", "GOOGL", "NVDA", "AMD", "TLT", "HYG", "UUP", "SMH", "SPY"]
    market = rng.normal(0, 0.008, len(dates))
    crisis = (dates >= "2020-02-19") & (dates <= "2020-03-23")
    market[crisis] -= 0.025
    data = {symbol: market + rng.normal(0, 0.006, len(dates)) for symbol in symbols}
    return pd.DataFrame(data, index=dates)


def _test_crisis() -> CrisisEvent:
    return CrisisEvent(
        name="Test Crisis",
        start_date="2020-02-19",
        peak_date="2020-03-23",
        end_date="2020-04-17",
        description="Synthetic holdout crisis",
    )


def test_run_crisis_backtest_composition():
    engine = BacktestEngine(
        position_symbols=["AAPL", "MSFT", "GOOGL", "NVDA", "AMD"],
        factor_symbols=["TLT", "HYG", "UUP", "SMH", "SPY"],
    )
    result = engine.run_crisis_backtest(_test_crisis(), _sample_returns())
    assert isinstance(result, BacktestResult)
    assert result.crisis.name == "Test Crisis"
    assert result.heat_during >= 0
    assert result.max_drawdown <= 0


def test_run_full_crisis_replay_composition():
    engine = BacktestEngine(
        position_symbols=["AAPL", "MSFT", "GOOGL", "NVDA", "AMD"],
        factor_symbols=["TLT", "HYG", "UUP", "SMH", "SPY"],
    )
    result = engine.run_full_backtest(_sample_returns(), crises=[_test_crisis()])
    assert len(result.crisis_results) == 1
    assert 0 <= result.overall_accuracy <= 1


def test_known_crises_have_valid_dates():
    for crisis in CRISIS_EVENTS:
        assert pd.Timestamp(crisis.start_date) < pd.Timestamp(crisis.peak_date)
        assert pd.Timestamp(crisis.peak_date) <= pd.Timestamp(crisis.end_date)


def test_crisis_catalog_is_composed():
    assert len(CRISIS_EVENTS) >= 3
