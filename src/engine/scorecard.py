"""W3 lead-time scorecard foundation.

W3 done_when: for every >=5% drawdown in the last 24 months, count the
warning days (VIX>25, price under its 50-day MA) the system flagged before
losses, including misses. Scope so far: the episode extractor turns a
close-price series into the list of peak->trough->recovery episodes, and
measure_warning_leads() measures, per episode, when each warning signal
first fired inside the episode and how much lead it gave before the trough.
Pure computation, no network.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

import pandas as pd

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class DrawdownEpisode:
    """One peak-to-trough-to-recovery drawdown cycle.

    depth is the worst loss from the episode peak, as a negative fraction
    (e.g. -0.08 for an 8% drawdown). recovery_date is None when the series
    ends before the price revisits its peak — unrecovered losses stay in
    the list so the scorecard "including losses" wording stays honest.
    """

    peak_date: pd.Timestamp
    trough_date: pd.Timestamp
    recovery_date: pd.Timestamp | None
    depth: float
    days_peak_to_trough: int


def extract_drawdown_episodes(
    closes: pd.Series,
    min_depth: float = 0.05,
    lookback_months: int = 24,
) -> list[DrawdownEpisode]:
    """Extract drawdown episodes of at least ``min_depth`` from ``closes``.

    An episode starts when the price falls below its running peak and ends
    the day the price first closes back at or above that same peak; ragged
    paths (dip, partial rally, deeper dip) count as one episode whose depth
    is the worst point. The window is the trailing ``lookback_months``
    calendar months of observations; episodes are detected entirely within
    the window (the window's running peak starts at its first close).

    Returns episodes ordered by peak_date. Series must be date-indexed and
    non-empty; otherwise an empty list is returned.
    """
    if not isinstance(closes, pd.Series) or closes.empty:
        return []

    window = closes.iloc[-max_lookback_slice(lookback_months, closes):]
    if len(window) < 2:
        return []

    episodes: list[DrawdownEpisode] = []
    peak_value = float(window.iloc[0])
    peak_date = window.index[0]
    trough_value = peak_value
    trough_date = peak_date
    in_episode = False

    for date, price_f in window.items():
        price = float(price_f)
        if price >= peak_value:
            if in_episode:
                depth = trough_value / peak_value - 1.0
                if depth <= -min_depth:
                    episodes.append(
                        DrawdownEpisode(
                            peak_date=peak_date,
                            trough_date=trough_date,
                            recovery_date=date,
                            depth=round(depth, 6),
                            days_peak_to_trough=int(
                                (trough_date - peak_date).days
                            ),
                        )
                    )
                in_episode = False
            peak_value = price
            peak_date = date
            trough_value = price
            trough_date = date
        else:
            if not in_episode:
                in_episode = True
            if price < trough_value:
                trough_value = price
                trough_date = date

    if in_episode:
        depth = trough_value / peak_value - 1.0
        if depth <= -min_depth:
            episodes.append(
                DrawdownEpisode(
                    peak_date=peak_date,
                    trough_date=trough_date,
                    recovery_date=None,
                    depth=round(depth, 6),
                    days_peak_to_trough=int((trough_date - peak_date).days),
                )
            )

    logger.debug(
        "extract_drawdown_episodes: %d episodes (min_depth=%.2f, window=%s..%s)",
        len(episodes),
        min_depth,
        window.index[0].date(),
        window.index[-1].date(),
    )
    return episodes


def max_lookback_slice(lookback_months: int, closes: pd.Series) -> int:
    """Number of trailing observations covering ``lookback_months`` months.

    Slices on calendar time from the last observation, so week-long gaps
    and holidays cannot shrink the window. Always >= 1.
    """
    if lookback_months <= 0:
        raise ValueError("lookback_months must be positive")
    last = closes.index[-1]
    cutoff = last - pd.DateOffset(months=lookback_months)
    n = int((closes.index > cutoff).sum())
    return max(n, 1)


@dataclass(frozen=True)
class EpisodeWarning:
    """Warning-signal measurement for one drawdown episode.

    A signal "fires" on a day when its condition holds (VIX strictly above
    the threshold, close strictly under its MA). Only fires inside the
    episode span [peak_date, trough_date] are attributed to the episode;
    pre-peak fires belong to earlier episodes and are deliberately not
    counted here. Misses are honest: first_date None and days_active 0.

    lead_days is the calendar-day gap from the first fire to the trough —
    how long before the worst point the signal was already shouting.
    A fire on the trough day itself scores 0.
    """

    episode: DrawdownEpisode
    vix_first_date: pd.Timestamp | None
    vix_days_active: int
    ma_first_date: pd.Timestamp | None
    ma_days_active: int

    @property
    def vix_fired(self) -> bool:
        return self.vix_first_date is not None

    @property
    def ma_fired(self) -> bool:
        return self.ma_first_date is not None

    @property
    def vix_lead_days(self) -> int | None:
        if self.vix_first_date is None:
            return None
        return int((self.episode.trough_date - self.vix_first_date).days)

    @property
    def ma_lead_days(self) -> int | None:
        if self.ma_first_date is None:
            return None
        return int((self.episode.trough_date - self.ma_first_date).days)


def measure_warning_leads(
    closes: pd.Series,
    vix: pd.Series | None,
    episodes: list[DrawdownEpisode] | None = None,
    ma_window: int = 50,
    vix_threshold: float = 25.0,
    lookback_months: int = 24,
) -> list[EpisodeWarning]:
    """Measure VIX>25 and under-50-day-MA warning leads for each episode.

    ``closes`` must be the full date-indexed history (sorted ascending) the
    episodes came from; the MA is computed over the full series so days near
    the lookback window's start still have a valid MA. ``vix`` is
    forward-filled onto the close index (stale between its own stamps, NaN
    before its first stamp); None or empty vix records an honest miss on
    every episode. Episodes are re-extracted from ``closes`` when not
    supplied. Missing MA warmup (fewer than ``ma_window`` observations so
    far) counts as not fired, never as a fire.
    """
    if episodes is None:
        episodes = extract_drawdown_episodes(
            closes, lookback_months=lookback_months
        )
    if not episodes:
        return []

    ma = closes.rolling(ma_window, min_periods=ma_window).mean()
    vix_aligned: pd.Series | None = None
    if vix is not None and not vix.empty:
        vix_sorted = vix.sort_index()
        vix_aligned = vix_sorted.reindex(closes.index, method="ffill")

    warnings: list[EpisodeWarning] = []
    for episode in episodes:
        span = closes.loc[episode.peak_date : episode.trough_date]
        vix_first: pd.Timestamp | None = None
        vix_days = 0
        ma_first: pd.Timestamp | None = None
        ma_days = 0
        for date, price_f in span.items():
            price = float(price_f)
            if vix_aligned is not None:
                value = vix_aligned.loc[date]
                if pd.notna(value) and float(value) > vix_threshold:
                    vix_days += 1
                    if vix_first is None:
                        vix_first = date
            ma_value = ma.loc[date]
            if pd.notna(ma_value) and price < float(ma_value):
                ma_days += 1
                if ma_first is None:
                    ma_first = date
        warnings.append(
            EpisodeWarning(
                episode=episode,
                vix_first_date=vix_first,
                vix_days_active=vix_days,
                ma_first_date=ma_first,
                ma_days_active=ma_days,
            )
        )

    logger.debug(
        "measure_warning_leads: %d episodes, vix fired %d, ma fired %d",
        len(warnings),
        sum(w.vix_fired for w in warnings),
        sum(w.ma_fired for w in warnings),
    )
    return warnings
