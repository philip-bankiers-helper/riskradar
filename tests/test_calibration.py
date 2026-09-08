"""Backtest calibration tests using synthetic, offline return data."""

import numpy as np
import pandas as pd

from scripts.run_backtest import (
    FACTOR_SYMBOLS,
    POSITION_SYMBOLS,
    derive_thresholds,
    run_full_rolling_backtest,
)


def test_derived_thresholds_follow_committed_quantiles():
    results = [{"heat_score": score} for score in np.linspace(0, 1, 101)]
    assert derive_thresholds(results) == {
        "warm": 0.5,
        "hot": 0.75,
        "critical": 0.9,
        "emergency": 0.97,
    }


def test_rolling_backtest_computes_nonzero_factor_hhi():
    rng = np.random.default_rng(7)
    dates = pd.bdate_range("2025-01-02", periods=65)
    factors = rng.normal(0, 0.01, (len(dates), len(FACTOR_SYMBOLS)))
    data = {symbol: factors[:, i] for i, symbol in enumerate(FACTOR_SYMBOLS)}
    for i, symbol in enumerate(POSITION_SYMBOLS):
        data[symbol] = 0.7 * factors[:, i % len(FACTOR_SYMBOLS)] + rng.normal(
            0, 0.002, len(dates)
        )
    results = run_full_rolling_backtest(pd.DataFrame(data, index=dates))
    assert results
    assert all(row["factor_hhi"] > 0 for row in results)
