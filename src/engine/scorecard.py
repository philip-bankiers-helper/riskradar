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
import statistics
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


@dataclass(frozen=True)
class SignalStat:
    """Hit/miss and lead-time aggregates for one warning signal.

    ``fired`` is how many episodes the signal fired in; ``total`` is how
    many episodes were on the table — misses and unrecovered losses
    included, so the hit rate can never look good by dropping bad weeks.
    ``median_lead_days`` is over fired episodes only; None when the
    signal never fired or there were no episodes at all.
    """

    fired: int
    total: int
    median_lead_days: float | None

    @property
    def hit_rate(self) -> float:
        """Fraction of episodes caught (0.0 when there was nothing to catch)."""
        return self.fired / self.total if self.total else 0.0


@dataclass(frozen=True)
class WeeklyScorecard:
    """The W3 report body: episodes x warning leads plus aggregates."""

    symbol: str
    window_start: pd.Timestamp
    window_end: pd.Timestamp
    warnings: tuple[EpisodeWarning, ...]
    vix: SignalStat
    ma: SignalStat
    either: SignalStat

    @property
    def episode_count(self) -> int:
        return len(self.warnings)


def _signal_stat(
    warnings: tuple[EpisodeWarning, ...],
    fired_of,
    lead_of,
) -> SignalStat:
    leads = [lead_of(w) for w in warnings if fired_of(w)]
    return SignalStat(
        fired=len(leads),
        total=len(warnings),
        median_lead_days=statistics.median(leads) if leads else None,
    )


def build_weekly_scorecard(
    symbol: str,
    closes: pd.Series,
    vix: pd.Series | None,
    ma_window: int = 50,
    vix_threshold: float = 25.0,
    lookback_months: int = 24,
) -> WeeklyScorecard:
    """Assemble the weekly lead-time scorecard for one symbol.

    Chains extract_drawdown_episodes + measure_warning_leads into the
    report body W3 asks for: every >=5% drawdown in the trailing
    ``lookback_months`` (unrecovered losses included), each signal's
    hit/miss and lead days per episode, and hit-rate / median-lead
    aggregates per signal plus an "either signal fired" row whose lead
    is the earliest firing signal's (the max of the available leads).
    Empty or non-series input yields an episode-count-zero scorecard
    with NaT window bounds — a week with no qualifying drawdown is a
    valid report, not an exception.
    """
    if not isinstance(closes, pd.Series) or closes.empty:
        empty = SignalStat(fired=0, total=0, median_lead_days=None)
        return WeeklyScorecard(
            symbol=symbol,
            window_start=pd.NaT,
            window_end=pd.NaT,
            warnings=(),
            vix=empty,
            ma=empty,
            either=empty,
        )

    window = closes.iloc[-max_lookback_slice(lookback_months, closes):]
    warnings = tuple(
        measure_warning_leads(
            closes,
            vix,
            ma_window=ma_window,
            vix_threshold=vix_threshold,
            lookback_months=lookback_months,
        )
    )

    def either_lead(w: EpisodeWarning) -> int:
        leads = [d for d in (w.vix_lead_days, w.ma_lead_days) if d is not None]
        return max(leads)

    return WeeklyScorecard(
        symbol=symbol,
        window_start=window.index[0],
        window_end=window.index[-1],
        warnings=warnings,
        vix=_signal_stat(
            warnings, lambda w: w.vix_fired, lambda w: w.vix_lead_days
        ),
        ma=_signal_stat(
            warnings, lambda w: w.ma_fired, lambda w: w.ma_lead_days
        ),
        either=_signal_stat(
            warnings,
            lambda w: w.vix_fired or w.ma_fired,
            either_lead,
        ),
    )


def _fmt_day(ts: pd.Timestamp) -> str:
    return ts.strftime("%Y-%m-%d") if pd.notna(ts) else "n/a"


def _mark(fired: bool, lead_days: int | None) -> str:
    return f"HIT +{lead_days}d" if fired else "MISS"


def _lead_txt(stat: SignalStat) -> str:
    if stat.median_lead_days is None:
        return "n/a"
    return f"{stat.median_lead_days:g}d"


def render_scorecard_text(card: WeeklyScorecard) -> str:
    """Render the weekly scorecard as Telegram-HTML text (pure, no sending).

    One line per episode — peak -> trough, depth, recovery status, and
    each signal's HIT/MISS with lead days — then the aggregate hit-rate
    line. Uses the daily summary's <b> style so a future weekly post
    reads native next to it.
    """
    head = (
        f"\U0001f4ca <b>WEEKLY LEAD-TIME SCORECARD</b> \u2014 {card.symbol}\n"
        f"Window: {_fmt_day(card.window_start)} \u2192 "
        f"{_fmt_day(card.window_end)}"
    )
    if not card.warnings:
        return head + "\n\nNo qualifying drawdowns in the window."

    lines = [head, "", f"<b>Drawdown episodes (\u22655%): {card.episode_count}</b>"]
    for i, w in enumerate(card.warnings, 1):
        e = w.episode
        status = "recovered" if e.recovery_date is not None else "UNRECOVERED"
        lines.append(
            f"{i}. {_fmt_day(e.peak_date)}\u2192{_fmt_day(e.trough_date)} "
            f"{e.depth:.1%} ({e.days_peak_to_trough}d, {status}) | "
            f"VIX {_mark(w.vix_fired, w.vix_lead_days)} | "
            f"MA {_mark(w.ma_fired, w.ma_lead_days)}"
        )
    lines.append("")
    lines.append(
        f"<b>Hits:</b> VIX {card.vix.fired}/{card.vix.total} "
        f"({card.vix.hit_rate:.0%}) lead {_lead_txt(card.vix)} \u00b7 "
        f"MA {card.ma.fired}/{card.ma.total} ({card.ma.hit_rate:.0%}) "
        f"lead {_lead_txt(card.ma)} \u00b7 "
        f"either {card.either.fired}/{card.either.total} "
        f"({card.either.hit_rate:.0%}) lead {_lead_txt(card.either)}"
    )
    return "\n".join(lines)
