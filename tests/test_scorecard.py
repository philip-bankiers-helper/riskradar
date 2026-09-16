"""Tests for the W3 scorecard drawdown-episode extractor (no network)."""

import pandas as pd
import pytest

from src.engine.scorecard import (
    DrawdownEpisode,
    EpisodeWarning,
    extract_drawdown_episodes,
    measure_warning_leads,
)


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


# --- measure_warning_leads -------------------------------------------------

# Peak idx3 (100), trough idx6 (92, -8%), recovery idx8.
LEAD_PRICES = [100, 100, 100, 100, 97, 94, 92, 96, 100]


def test_leads_first_fire_day_counts_and_lead_days():
    s = series_from(LEAD_PRICES)
    vix = pd.Series([20, 20, 20, 20, 20, 26, 27, 21, 20], index=s.index)
    warns = measure_warning_leads(s, vix, ma_window=3)
    assert len(warns) == 1
    w = warns[0]
    # MA(3) over the span 100,97,94,92 -> fires at 97<99, 94<97, 92<94.67
    assert w.ma_first_date == s.index[4]
    assert w.ma_days_active == 3
    assert w.ma_lead_days == (s.index[6] - s.index[4]).days
    # VIX crosses on idx5, stays over on idx6 (trough day)
    assert w.vix_first_date == s.index[5]
    assert w.vix_days_active == 2
    assert w.vix_lead_days == (s.index[6] - s.index[5]).days
    assert w.vix_fired and w.ma_fired


def test_leads_misses_are_honest_and_threshold_is_strict():
    s = series_from(LEAD_PRICES)
    # VIX pinned exactly at 25 -> strict > never fires; MA warmup absent
    vix = pd.Series([25.0] * len(s), index=s.index)
    warns = measure_warning_leads(s, vix, ma_window=50)
    w = warns[0]
    assert not w.vix_fired and w.vix_first_date is None
    assert w.vix_days_active == 0 and w.vix_lead_days is None
    assert not w.ma_fired and w.ma_days_active == 0 and w.ma_lead_days is None
    # No VIX supplied at all -> same honest miss
    warns_none = measure_warning_leads(s, None, ma_window=50)
    assert warns_none[0].vix_fired is False


def test_leads_pre_peak_fire_is_not_attributed():
    # Peak idx2 (102), trough idx4 (94). VIX spiked at idx0, before the peak.
    s = series_from([100, 101, 102, 96, 94, 97, 102])
    vix = pd.Series([30, 20, 20, 20, 20, 20, 20], index=s.index)
    warns = measure_warning_leads(s, vix, ma_window=2)
    w = warns[0]
    assert w.vix_first_date is None and w.vix_days_active == 0
    # MA(2) does fire inside the episode: contrast proves the attribution rule
    assert w.ma_first_date == s.index[3]
    assert w.ma_lead_days == (s.index[4] - s.index[3]).days


def test_leads_coarse_vix_stamps_are_ffill_aligned():
    s = series_from(LEAD_PRICES)
    vix = pd.Series([26.0, 26.0, 26.0], index=s.index[::3])  # idx 0, 3, 6
    warns = measure_warning_leads(s, vix, ma_window=50)
    w = warns[0]
    # Stamp at idx3 forward-fills across the whole span idx3..idx6
    assert w.vix_first_date == s.index[3]
    assert w.vix_days_active == 4  # idx3, 4, 5, 6
    assert w.vix_lead_days == (s.index[6] - s.index[3]).days


def test_leads_fire_on_trough_day_scores_zero_lead():
    s = series_from(LEAD_PRICES)
    values = [20.0] * len(s)
    values[6] = 26.0  # exactly the trough day
    vix = pd.Series(values, index=s.index)
    w = measure_warning_leads(s, vix, ma_window=50)[0]
    assert w.vix_first_date == w.episode.trough_date
    assert w.vix_days_active == 1
    assert w.vix_lead_days == 0


def test_leads_unrecovered_episode_is_measured():
    s = series_from([100, 96, 90, 92])  # ends under water
    vix = pd.Series([20, 26, 26, 26], index=s.index)
    w = measure_warning_leads(s, vix, ma_window=2)[0]
    assert w.episode.recovery_date is None
    assert w.vix_first_date == s.index[1]
    assert w.vix_lead_days == (s.index[2] - s.index[1]).days
    assert w.ma_first_date == s.index[1] and w.ma_days_active == 2


def test_leads_two_episodes_measured_independently():
    # Ep1 peak idx0 trough idx1 (-5%); ep2 peak idx3 trough idx4 (-6%)
    s = series_from([100, 95, 100, 100, 94, 97, 100])
    vix = pd.Series([20, 26, 20, 20, 20, 20, 20], index=s.index)
    warns = measure_warning_leads(s, vix, ma_window=3)
    assert len(warns) == 2
    assert warns[0].episode.peak_date == s.index[0]
    assert warns[0].vix_lead_days == 0  # fired on ep1's trough day
    assert warns[1].vix_fired is False  # ep1's spike does not leak into ep2
    assert warns[1].ma_lead_days == 0  # MA fires on ep2's trough day


def test_leads_explicit_episodes_argument_is_used_as_is():
    s = series_from(LEAD_PRICES)
    assert measure_warning_leads(s, None, episodes=[], ma_window=3) == []
    ep = extract_drawdown_episodes(s)[0]
    warns = measure_warning_leads(s, None, episodes=[ep], ma_window=3)
    assert len(warns) == 1 and warns[0].episode is ep
    assert isinstance(warns[0], EpisodeWarning)
