"""W3 lead-time scorecard foundation.

W3 done_when: for every >=5% drawdown in the last 24 months, count the
warning days (VIX>25, price under its 50-day MA) the system flagged before
losses, including misses. Tonight's scope is the episode extractor only:
turn a close-price series into the list of peak->trough->recovery episodes
the scorecard will be built on. Pure computation, no network.
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
