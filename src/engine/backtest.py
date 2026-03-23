"""Historical backtest engine for Risk Radar.

Validates the system against known crisis periods:
1. COVID crash (Feb-Mar 2020)
2. 2022 bear market (Jan-Oct 2022)
3. Aug 2024 vol spike (Yen carry unwind)
4. Jan 2026 quant blowup

Tests: Did our heat score, regime detector, and crowding signals
actually spike BEFORE or DURING these events?
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)


@dataclass
class CrisisEvent:
    """A known historical crisis for backtesting."""
    name: str
    start_date: str  # YYYY-MM-DD
    peak_date: str  # Worst day
    end_date: str
    description: str
    expected_heat_direction: str = "up"  # Heat should go up during crisis


# Known crisis events for validation
CRISIS_EVENTS = [
    CrisisEvent(
        name="COVID Crash",
        start_date="2020-02-19",
        peak_date="2020-03-23",
        end_date="2020-04-17",
        description="Pandemic selloff, circuit breakers, VIX > 80",
    ),
    CrisisEvent(
        name="2022 Bear Market",
        start_date="2022-01-03",
        peak_date="2022-06-16",
        end_date="2022-10-12",
        description="Fed rate hikes, tech crash, -27% drawdown",
    ),
    CrisisEvent(
        name="Aug 2024 Vol Spike",
        start_date="2024-07-31",
        peak_date="2024-08-05",
        end_date="2024-08-16",
        description="Yen carry trade unwind, VIX spike to 65",
    ),
    CrisisEvent(
        name="Jan 2026 Quant Blowup",
        start_date="2026-01-06",
        peak_date="2026-01-17",
        end_date="2026-02-07",
        description="Worst quant drawdown since Oct, US long-short deleveraging",
    ),
]


@dataclass
class BacktestResult:
    """Result of backtesting against a single crisis."""
    crisis: CrisisEvent
    heat_before: float = 0.0  # Average heat in 10 days before crisis
    heat_during: float = 0.0  # Average heat during crisis
    heat_peak: float = 0.0  # Max heat during crisis
    heat_warned_early: bool = False  # Did heat rise before peak?
    days_early_warning: int = 0  # How many days before peak did heat cross warm?
    correlation_spike: float = 0.0  # Max avg correlation during crisis
    regime_detected: str = ""  # What regime was detected
    max_drawdown: float = 0.0  # Max drawdown during period
    crowding_before: float = 0.0  # Crowding score before crisis
    success: bool = False  # Did the system flag this crisis?


@dataclass
class FullBacktestResult:
    """Complete backtest results across all crises."""
    crisis_results: list[BacktestResult] = field(default_factory=list)
    overall_accuracy: float = 0.0  # % of crises correctly flagged
    avg_early_warning_days: float = 0.0
    false_positive_rate: float = 0.0  # % of non-crisis periods flagged
    sharpe_of_signals: float = 0.0  # Sharpe ratio of following crowding alpha signal
    timestamp: str = ""


class BacktestEngine:
    """Run historical backtests to validate risk signals.

    Uses yfinance for historical data and replays the heat score,
    regime, and crowding computations over historical windows.
    """

    def __init__(
        self,
        position_symbols: list[str] | None = None,
        factor_symbols: list[str] | None = None,
        heat_threshold_warm: float = 0.4,
    ):
        self.position_symbols = position_symbols or [
            "NVDA", "AAPL", "MSFT", "AMZN", "GOOGL",
            "META", "TSLA", "AMD", "AVGO",
        ]
        self.factor_symbols = factor_symbols or [
            "TLT", "HYG", "UUP", "SMH", "QUAL",
            "MTUM", "SPY", "USMV", "VTV",
        ]
        self.heat_threshold_warm = heat_threshold_warm
        self._historical_data: pd.DataFrame | None = None

    def fetch_historical_data(
        self,
        start_date: str = "2019-01-01",
        end_date: str | None = None,
    ) -> pd.DataFrame:
        """Fetch historical price data for all symbols."""
        import yfinance as yf

        all_symbols = list(set(self.position_symbols + self.factor_symbols))

        if end_date is None:
            end_date = datetime.now().strftime("%Y-%m-%d")

        logger.info(
            "Fetching historical data for %d symbols (%s to %s)...",
            len(all_symbols), start_date, end_date,
        )

        prices = yf.download(
            all_symbols,
            start=start_date,
            end=end_date,
            auto_adjust=True,
        )

        if isinstance(prices.columns, pd.MultiIndex):
            prices = prices["Close"]
        elif "Close" in prices.columns:
            prices = prices[["Close"]]

        returns = prices.pct_change().dropna()
        self._historical_data = returns

        logger.info(
            "Fetched %d days of returns for %d symbols",
            len(returns), len(returns.columns),
        )
        return returns

    def run_crisis_backtest(
        self,
        crisis: CrisisEvent,
        returns: pd.DataFrame | None = None,
    ) -> BacktestResult:
        """Backtest a single crisis event.

        Replays the heat score computation over the crisis window.
        """
        if returns is None:
            returns = self._historical_data
        if returns is None:
            raise ValueError("No historical data. Call fetch_historical_data() first.")

        from src.engine.correlation import CorrelationEngine
        from src.engine.heat_score import HeatScoreCalculator

        result = BacktestResult(crisis=crisis)

        # Parse dates
        start = pd.Timestamp(crisis.start_date)
        peak = pd.Timestamp(crisis.peak_date)
        end = pd.Timestamp(crisis.end_date)

        # Get pre-crisis window (30 trading days before)
        pre_start = start - pd.Timedelta(days=45)  # Calendar days, ~30 trading

        # Filter data
        available_pos = [s for s in self.position_symbols if s in returns.columns]
        available_fac = [s for s in self.factor_symbols if s in returns.columns]

        if not available_pos:
            logger.warning("No position symbols available for %s", crisis.name)
            return result

        pos_returns = returns[available_pos]
        fac_returns = returns[available_fac] if available_fac else pd.DataFrame()

        # Compute rolling heat scores
        corr_engine = CorrelationEngine(ewma_span=60)
        heat_calc = HeatScoreCalculator()

        n_positions = len(available_pos)
        equal_weights = np.ones(n_positions) / n_positions

        # Pre-crisis heat scores
        pre_crisis_mask = (pos_returns.index >= pre_start) & (pos_returns.index < start)
        pre_crisis_data = pos_returns[pre_crisis_mask]

        pre_heat_scores = []
        for i in range(60, len(pre_crisis_data)):
            window = pre_crisis_data.iloc[i - 60:i]
            try:
                corr = corr_engine.compute_correlation_matrix(window)
                avg_corr = corr_engine.compute_avg_correlation(corr)
                heat = heat_calc.compute(
                    returns=window,
                    portfolio_weights=equal_weights,
                    factor_exposures={},
                    avg_correlation=avg_corr,
                )
                pre_heat_scores.append(heat.score)
            except Exception:
                pass

        if pre_heat_scores:
            result.heat_before = float(np.mean(pre_heat_scores))

        # Crisis period heat scores
        crisis_mask = (pos_returns.index >= start) & (pos_returns.index <= end)
        # Include pre-crisis data for rolling window
        extended_start = start - pd.Timedelta(days=90)
        extended_mask = (pos_returns.index >= extended_start) & (pos_returns.index <= end)
        extended_data = pos_returns[extended_mask]

        crisis_heat_scores = []
        crisis_dates = []
        crisis_correlations = []

        crisis_start_idx = len(extended_data[extended_data.index < start])

        for i in range(max(60, crisis_start_idx), len(extended_data)):
            window = extended_data.iloc[i - 60:i]
            current_date = extended_data.index[i]

            if current_date < start:
                continue

            try:
                corr = corr_engine.compute_correlation_matrix(window)
                avg_corr = corr_engine.compute_avg_correlation(corr)
                heat = heat_calc.compute(
                    returns=window,
                    portfolio_weights=equal_weights,
                    factor_exposures={},
                    avg_correlation=avg_corr,
                )
                crisis_heat_scores.append(heat.score)
                crisis_dates.append(current_date)
                crisis_correlations.append(avg_corr)
            except Exception:
                pass

        if crisis_heat_scores:
            result.heat_during = float(np.mean(crisis_heat_scores))
            result.heat_peak = float(np.max(crisis_heat_scores))
            result.correlation_spike = float(np.max(crisis_correlations))

            # Did heat rise before the peak?
            peak_idx = None
            for i, d in enumerate(crisis_dates):
                if d >= peak:
                    peak_idx = i
                    break

            if peak_idx is not None and peak_idx > 0:
                # Check if heat crossed warm threshold before the peak
                for i in range(peak_idx):
                    if crisis_heat_scores[i] >= self.heat_threshold_warm:
                        result.heat_warned_early = True
                        days_before_peak = (peak - crisis_dates[i]).days
                        result.days_early_warning = max(0, days_before_peak)
                        break

        # Max drawdown during crisis
        crisis_prices = pos_returns[crisis_mask].mean(axis=1)
        if len(crisis_prices) > 1:
            cumulative = (1 + crisis_prices).cumprod()
            peak_cum = cumulative.expanding().max()
            drawdown = (cumulative / peak_cum - 1)
            result.max_drawdown = float(drawdown.min())

        # Success criteria: heat during > heat before AND heat crossed warm
        result.success = (
            result.heat_during > result.heat_before
            and result.heat_peak >= self.heat_threshold_warm
        )

        logger.info(
            "Backtest %s: heat_before=%.3f, heat_during=%.3f, peak=%.3f, "
            "early_warning=%s (%d days), drawdown=%.1f%%, success=%s",
            crisis.name,
            result.heat_before,
            result.heat_during,
            result.heat_peak,
            result.heat_warned_early,
            result.days_early_warning,
            result.max_drawdown * 100,
            result.success,
        )
        return result

    def run_full_backtest(
        self,
        returns: pd.DataFrame | None = None,
        crises: list[CrisisEvent] | None = None,
    ) -> FullBacktestResult:
        """Run backtests across all crisis events.

        Returns comprehensive validation results.
        """
        if returns is None:
            if self._historical_data is None:
                self.fetch_historical_data()
            returns = self._historical_data

        if crises is None:
            crises = CRISIS_EVENTS

        results = []
        for crisis in crises:
            try:
                result = self.run_crisis_backtest(crisis, returns)
                results.append(result)
            except Exception as e:
                logger.error("Backtest failed for %s: %s", crisis.name, e)

        # Overall metrics
        n_success = sum(1 for r in results if r.success)
        accuracy = n_success / len(results) if results else 0

        early_warning_days = [
            r.days_early_warning for r in results if r.heat_warned_early
        ]
        avg_early = float(np.mean(early_warning_days)) if early_warning_days else 0

        # False positive rate: % of non-crisis 60-day windows where heat > warm
        fp_rate = self._estimate_false_positive_rate(returns)

        return FullBacktestResult(
            crisis_results=results,
            overall_accuracy=accuracy,
            avg_early_warning_days=avg_early,
            false_positive_rate=fp_rate,
            timestamp=datetime.utcnow().isoformat(),
        )

    def _estimate_false_positive_rate(
        self,
        returns: pd.DataFrame,
        sample_windows: int = 50,
    ) -> float:
        """Estimate false positive rate by sampling non-crisis periods."""
        if returns is None or returns.empty:
            return 0.0

        from src.engine.correlation import CorrelationEngine
        from src.engine.heat_score import HeatScoreCalculator

        # Find non-crisis dates
        crisis_dates = set()
        for crisis in CRISIS_EVENTS:
            start = pd.Timestamp(crisis.start_date)
            end = pd.Timestamp(crisis.end_date)
            for date in returns.index:
                if start <= date <= end:
                    crisis_dates.add(date)

        non_crisis = returns[~returns.index.isin(crisis_dates)]
        available_pos = [s for s in self.position_symbols if s in non_crisis.columns]

        if not available_pos or len(non_crisis) < 120:
            return 0.0

        pos_returns = non_crisis[available_pos]
        corr_engine = CorrelationEngine(ewma_span=60)
        heat_calc = HeatScoreCalculator()
        n_pos = len(available_pos)
        weights = np.ones(n_pos) / n_pos

        # Sample random 60-day windows
        max_start = len(pos_returns) - 60
        if max_start <= 0:
            return 0.0

        rng = np.random.default_rng(42)
        starts = rng.choice(max_start, size=min(sample_windows, max_start), replace=False)

        false_positives = 0
        total_samples = 0

        for start_idx in starts:
            window = pos_returns.iloc[start_idx:start_idx + 60]
            try:
                corr = corr_engine.compute_correlation_matrix(window)
                avg_corr = corr_engine.compute_avg_correlation(corr)
                heat = heat_calc.compute(
                    returns=window,
                    portfolio_weights=weights,
                    factor_exposures={},
                    avg_correlation=avg_corr,
                )
                total_samples += 1
                if heat.score >= self.heat_threshold_warm:
                    false_positives += 1
            except Exception:
                pass

        return false_positives / total_samples if total_samples > 0 else 0.0

    def to_dict(self, result: FullBacktestResult) -> dict:
        """Serialize backtest results for API response."""
        return {
            "overall_accuracy": round(result.overall_accuracy, 4),
            "avg_early_warning_days": round(result.avg_early_warning_days, 1),
            "false_positive_rate": round(result.false_positive_rate, 4),
            "n_crises_tested": len(result.crisis_results),
            "n_crises_detected": sum(1 for r in result.crisis_results if r.success),
            "timestamp": result.timestamp,
            "crises": [
                {
                    "name": r.crisis.name,
                    "period": f"{r.crisis.start_date} to {r.crisis.end_date}",
                    "heat_before": round(r.heat_before, 4),
                    "heat_during": round(r.heat_during, 4),
                    "heat_peak": round(r.heat_peak, 4),
                    "early_warning": r.heat_warned_early,
                    "days_early_warning": r.days_early_warning,
                    "correlation_spike": round(r.correlation_spike, 4),
                    "max_drawdown_pct": round(r.max_drawdown * 100, 2),
                    "detected": r.success,
                }
                for r in result.crisis_results
            ],
        }
