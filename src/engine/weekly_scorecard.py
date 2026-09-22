"""Weekly scorecard cadence: fetch, assemble, and gate the W3 post.

W3's done_when is a *weekly-regenerated* scorecard. The pure episode
math lives in ``src.engine.scorecard``; this module is the service
glue the scheduler calls:

- ``is_weekly_scorecard_slot`` — the weekly gate: the scorecard posts
  in the Monday 16:30 ET slot (first slot after the weekend, so the
  24-month window always rolls a full week past the last post).
- ``build_scorecard_text`` — series -> rendered report (pure).
- ``produce_weekly_scorecard_text`` — async fetch + build; returns
  ``None`` instead of raising so a scorecard failure can never take
  the daily-summary scheduler loop down with it.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from datetime import datetime

import pandas as pd

from src.engine.scorecard import build_weekly_scorecard, render_scorecard_text

logger = logging.getLogger(__name__)

WEEKLY_SCORECARD_SYMBOL = "SPY"
WEEKLY_SCORECARD_TIER = "weekly_scorecard"

# get_prices derives start = end - period_days * 1.5, so 540 trading
# days gives ~810 calendar days of history: 24 months of episodes plus
# 50-day-MA warmup headroom.
_FETCH_PERIOD_DAYS = 540


def is_weekly_scorecard_slot(target: datetime) -> bool:
    """True iff the 16:30 ET slot ``target`` is the week's Monday slot.

    The scheduler passes the *slot* datetime (tz-aware, ET). Any
    Monday slot qualifies regardless of time-of-day so a slot that
    fires late for any reason still counts for that week.
    """
    return target.weekday() == 0


def build_scorecard_text(
    closes: pd.Series | None,
    vix: pd.Series | None,
    symbol: str = WEEKLY_SCORECARD_SYMBOL,
) -> str | None:
    """Render the weekly scorecard from fetched series (pure).

    Returns ``None`` when ``closes`` is unusable — a week with no
    data must skip the post, not crash the scheduler.
    """
    if not isinstance(closes, pd.Series) or closes.empty:
        return None
    card = build_weekly_scorecard(symbol, closes, vix)
    return render_scorecard_text(card)


def _fetch_series_sync(symbol: str) -> tuple[pd.Series | None, pd.Series | None]:
    """Fetch (closes, vix) for the scorecard window. Network, no test."""
    from src.data.market_data import MarketDataClient

    try:
        client = MarketDataClient()
        prices = client.get_prices([symbol, "^VIX"], period_days=_FETCH_PERIOD_DAYS)
    except Exception as exc:  # noqa: BLE001 - weekly post must not raise
        logger.error("Weekly scorecard fetch failed: %s", exc)
        return None, None

    closes = vix = None
    if symbol in prices.columns:
        closes = prices[symbol].dropna()
    if "^VIX" in prices.columns:
        vix = prices["^VIX"].dropna()
    return closes, vix


async def produce_weekly_scorecard_text(
    symbol: str = WEEKLY_SCORECARD_SYMBOL,
    fetch: Callable[[str], Awaitable[tuple[pd.Series | None, pd.Series | None]]] | None = None,
) -> str | None:
    """Fetch data and render the weekly scorecard text.

    ``fetch`` is injectable for offline tests; production uses
    ``_fetch_series_sync`` off the event loop via ``asyncio.to_thread``
    (yfinance is blocking). Returns ``None`` on any fetch failure.
    """
    try:
        if fetch is None:
            closes, vix = await asyncio.to_thread(_fetch_series_sync, symbol)
        else:
            closes, vix = await fetch(symbol)
    except Exception as exc:  # noqa: BLE001 - weekly post must not raise
        logger.error("Weekly scorecard fetch raised: %s", exc)
        return None
    return build_scorecard_text(closes, vix, symbol)
