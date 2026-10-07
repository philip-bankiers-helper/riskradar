"""Offline tests: percentile histories advance once per data date.

Root-cause regression for the 2026-10-03..05 phantom EMERGENCY storm:
compute() runs every ~5 minutes, but daily-bar data changes once per trading
day. Appending the unchanged value to the percentile histories on every
cycle self-inflates percentile_rank toward 1.0 (each duplicate of v counts
as history <= v), which crept the composite 0.21 -> 1.00 (clipped) across a
closed weekend and triggered ~850 EMERGENCY broadcasts. See PROGRESS.md
2026-10-06 / 2026-10-07.
"""

import numpy as np
import pandas as pd
import pytest

from src.engine.heat_score import HeatScoreCalculator
from src.engine.metrics import percentile_rank

WEIGHTS = np.array([0.25, 0.25, 0.25, 0.25])
FACTORS = {"market": 0.6, "rates": 0.2}


def make_returns(days: int = 80, seed: int = 7) -> pd.DataFrame:
    """Deterministic daily-bar frame; same seed longer = same walk + new day."""
    rng = np.random.default_rng(seed)
    idx = pd.bdate_range("2026-06-01", periods=days)
    return pd.DataFrame(
        {
            "NVDA": rng.normal(0, 0.03, days),
            "TSLA": rng.normal(0, 0.035, days),
            "META": rng.normal(0, 0.025, days),
            "PLTR": rng.normal(0, 0.04, days),
        },
        index=idx,
    )


def run_compute(calc: HeatScoreCalculator, returns: pd.DataFrame, avg_corr: float = 0.25):
    return calc.compute(
        returns=returns,
        portfolio_weights=WEIGHTS,
        factor_exposures=dict(FACTORS),
        avg_correlation=avg_corr,
    )


def history_lens(calc: HeatScoreCalculator) -> list[int]:
    return [
        len(calc._ar_history),
        len(calc._turb_history),
        len(calc._dr_history),
        len(calc._hhi_history),
        len(calc._corr_history),
    ]


class TestDuplicateAppendsSuppressed:
    def test_repeated_cycles_on_unchanged_frame_do_not_drift(self):
        """5-min cadence on unchanged data: score frozen, histories frozen."""
        calc = HeatScoreCalculator()
        frame = make_returns()
        first = run_compute(calc, frame)
        lens_after_first = history_lens(calc)
        scores = [run_compute(calc, frame).score for _ in range(20)]
        assert scores == [first.score] * 20  # pre-fix: +~0.001/cycle inflation
        assert history_lens(calc) == lens_after_first

    def test_component_percentiles_constant_on_unchanged_frame(self):
        calc = HeatScoreCalculator()
        frame = make_returns()
        heat = run_compute(calc, frame)
        pcts = (
            heat.components.turbulence_percentile,
            heat.components.diversification_percentile,
            heat.components.factor_hhi_percentile,
            heat.components.avg_correlation_percentile,
        )
        for _ in range(10):
            again = run_compute(calc, frame)
            assert (
                again.components.turbulence_percentile,
                again.components.diversification_percentile,
                again.components.factor_hhi_percentile,
                again.components.avg_correlation_percentile,
            ) == pcts

    def test_closed_weekend_simulation_stays_flat(self):
        """3 days x 288 five-minute cycles with markets closed (no data change).

        Pre-fix this is the exact storm generator: the composite crept past
        the emergency threshold without a single new bar.
        """
        calc = HeatScoreCalculator()
        frame = make_returns()
        first = run_compute(calc, frame)
        for _ in range(3 * 288):
            run_compute(calc, frame)
        final = run_compute(calc, frame)
        assert final.score == first.score
        assert final.level == first.level


class TestOneAppendPerDataDate:
    def test_new_trading_day_appends_exactly_once(self):
        calc = HeatScoreCalculator()
        frame = make_returns()
        run_compute(calc, frame)
        assert history_lens(calc) == [1, 1, 1, 1, 1]
        for _ in range(5):  # intraday cycles on the same daily bar
            run_compute(calc, frame)
        assert history_lens(calc) == [1, 1, 1, 1, 1]
        run_compute(calc, make_returns(days=81))  # next day's bar landed
        assert history_lens(calc) == [2, 2, 2, 2, 2]
        for _ in range(5):
            run_compute(calc, make_returns(days=81))
        assert history_lens(calc) == [2, 2, 2, 2, 2]

    def test_multi_day_walk_appends_once_per_day(self):
        calc = HeatScoreCalculator()
        for days in (80, 81, 82):
            run_compute(calc, make_returns(days))
        assert history_lens(calc) == [3, 3, 3, 3, 3]

    def test_same_date_backfill_does_not_append(self):
        """Earlier-row corrections on the same last date: one observation per
        trading day still — the history key is the data date, not the cycle."""
        calc = HeatScoreCalculator()
        frame = make_returns()
        run_compute(calc, frame)
        mutated = frame.copy()
        mutated.iloc[0, 0] += 0.10
        run_compute(calc, mutated)
        assert history_lens(calc) == [1, 1, 1, 1, 1]

    def test_seeded_history_plus_repeats_stay_stable(self):
        calc = HeatScoreCalculator()
        calc.seed_history(
            {k: list(np.linspace(0.1, 0.9, 30)) for k in ("ar", "turb", "dr", "hhi", "corr")}
        )
        first = run_compute(calc, make_returns())
        for _ in range(50):
            run_compute(calc, make_returns())
        assert run_compute(calc, make_returns()).score == first.score
        assert history_lens(calc) == [31, 31, 31, 31, 31]


class TestRootCauseMechanics:
    def test_percentile_rank_self_inflates_on_duplicate_appends(self):
        """Documents WHY duplicates are fatal: every appended copy of v
        counts as history <= v, so the rank saturates at 1.0 with zero data
        change. Pins the metric behavior the append-once fix works around;
        percentile_rank itself is intentionally untouched."""
        v = 0.4
        history = [0.1, 0.2, 0.3, 0.5]  # v sits at the 75th percentile
        ranks = [percentile_rank(v, list(history))]
        for _ in range(200):
            history.append(v)
            ranks.append(percentile_rank(v, list(history)))
        assert ranks[0] == 0.75
        assert ranks[-1] > 0.99  # duplicates asymptotically saturate the rank
        assert all(b >= a for a, b in zip(ranks, ranks[1:]))  # monotone climb
