"""Tests for the W3 scorecard drawdown-episode extractor (no network)."""

import pandas as pd
import pytest

from src.engine.scorecard import DrawdownEpisode, extract_drawdown_episodes


def series_from(prices, start="2024-01-02"):
    return pd.Series(
        [float(p) for p in prices],
        index=pd.bdate_range(start, periods=len(prices)),
    )


def test_two_clean_episodes_are_found_in_order():
    # 100 -> 90 (-10%) -> 100, later 100 -> 94 (-6%) -> 100
    s = series_from([100, 95, 90, 95, 100, 100, 97, 94, 97, 100, 101])
    eps = extract_drawdown_episodes(s)
    assert [round(e.depth, 4) for e in eps] == [-0.10, -0.06]
    assert eps[0].peak_date == s.index[0]
    assert eps[0].trough_date == s.index[2]
    assert eps[0].recovery_date == s.index[4]
    assert eps[1].recovery_date == s.index[9]
    assert eps[0].days_peak_to_trough == (s.index[2] - s.index[0]).days


def test_dip_below_threshold_is_excluded():
    s = series_from([100, 98, 100, 101])  # -2% dip
    assert extract_drawdown_episodes(s) == []


def test_unrecovered_episode_has_no_recovery_date():
    s = series_from([100, 95, 90, 92, 91])  # ends -9% under water
    eps = extract_drawdown_episodes(s)
    assert len(eps) == 1
    assert eps[0].recovery_date is None
    assert eps[0].depth == pytest.approx(-0.10)


def test_ragged_path_counts_as_one_episode_at_worst_depth():
    # -6%, partial rally, then -8% before recovering: one episode, depth -8%
    s = series_from([100, 94, 96, 92, 97, 100])
    eps = extract_drawdown_episodes(s)
    assert len(eps) == 1
    assert eps[0].depth == pytest.approx(-0.08)
    assert eps[0].trough_date == s.index[3]


def test_lookback_window_excludes_old_episodes():
    prices = [100, 90, 100] + [100 + i for i in range(80)]
    s = series_from(prices, start="2024-01-02")
    # 24-month lookback from the last obs (late 2024) excludes the early
    # 2024 drawdown only when the window shrinks below it.
    assert len(extract_drawdown_episodes(s, lookback_months=24)) == 1
    short = extract_drawdown_episodes(s, lookback_months=1)
    assert short == []


def test_monotonic_up_series_has_no_episodes():
    s = series_from(range(100, 130))
    assert extract_drawdown_episodes(s) == []


def test_empty_and_degenerate_inputs():
    assert extract_drawdown_episodes(pd.Series(dtype=float)) == []
    assert extract_drawdown_episodes(series_from([100])) == []
    with pytest.raises(ValueError):
        extract_drawdown_episodes(series_from([100, 90, 100]), lookback_months=0)


def test_episode_dataclass_is_frozen():
    ep = DrawdownEpisode(
        peak_date=pd.Timestamp("2024-01-02"),
        trough_date=pd.Timestamp("2024-02-01"),
        recovery_date=None,
        depth=-0.07,
        days_peak_to_trough=30,
    )
    with pytest.raises(Exception):
        ep.depth = -0.05  # type: ignore[misc]
