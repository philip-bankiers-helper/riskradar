"""VIX benchmark comparison engine for Risk Radar.

Compares RiskRadar heat signals against simple alternatives:
1. VIX > 25 (classic fear gauge)
2. 50-day MA cross (trend following)
3. Fixed calendar rebalance (monthly)
4. Correlation-only signal (just watch avg correlation)
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd

from src.models import BenchmarkReport, BenchmarkResult

logger = logging.getLogger(__name__)


class SignalBenchmark:
    """Compare RiskRadar signals against simple alternatives."""

    def __init__(
        self,
        heat_threshold: float = 0.55,
        vix_threshold: float = 25.0,
        ma_window: int = 50,
        correlation_threshold: float = 0.6,
        drawdown_threshold: float = -0.05,
    ):
        self.heat_threshold = heat_threshold
        self.vix_threshold = vix_threshold
        self.ma_window = ma_window
        self.correlation_threshold = correlation_threshold
        self.drawdown_threshold = drawdown_threshold

    def run_comparison(
        self,
        heat_history: list[dict],
        vix_history: pd.Series | None = None,
        portfolio_returns: pd.Series | None = None,
        correlation_history: list[float] | None = None,
    ) -> BenchmarkReport:
        """Run full comparison of all signals.

        Args:
            heat_history: List of dicts with 'date'/'timestamp' and 'score' keys.
            vix_history: VIX closing prices indexed by date.
            portfolio_returns: Daily portfolio returns indexed by date.
            correlation_history: Average correlation values aligned with heat_history dates.
        """
        # Build aligned DataFrames
        heat_df = self._parse_heat_history(heat_history)

        if heat_df.empty:
            logger.warning("No heat history data for benchmark")
            return BenchmarkReport(
                results=[],
                best_signal="N/A",
                heat_vs_vix_summary="Insufficient data",
                timestamp=datetime.now(timezone.utc),
            )

        # Get VIX data if not provided
        if vix_history is None:
            vix_history = self._fetch_vix(heat_df.index.min(), heat_df.index.max())

        # Get portfolio returns if not provided
        if portfolio_returns is None:
            portfolio_returns = self._fetch_portfolio_returns(
                heat_df.index.min(), heat_df.index.max()
            )

        if portfolio_returns is None or portfolio_returns.empty:
            logger.warning("No portfolio returns data for benchmark")
            return BenchmarkReport(
                results=[],
                best_signal="N/A",
                heat_vs_vix_summary="Insufficient portfolio returns data",
                timestamp=datetime.now(timezone.utc),
            )

        # Align all series to common dates
        common_idx = heat_df.index.intersection(portfolio_returns.index)
        if vix_history is not None and not vix_history.empty:
            common_idx = common_idx.intersection(vix_history.index)

        if len(common_idx) < 30:
            logger.warning("Insufficient aligned data points (%d) for benchmark", len(common_idx))
            return BenchmarkReport(
                results=[],
                best_signal="N/A",
                heat_vs_vix_summary="Insufficient aligned data",
                timestamp=datetime.now(timezone.utc),
            )

        heat_aligned = heat_df.loc[common_idx, "score"]
        returns_aligned = portfolio_returns.loc[common_idx]
        vix_aligned = vix_history.loc[common_idx] if vix_history is not None and not vix_history.empty else None

        # Build correlation series if available
        corr_series = None
        if correlation_history and len(correlation_history) >= len(heat_df):
            corr_series = pd.Series(
                correlation_history[: len(heat_df)],
                index=heat_df.index,
            ).loc[common_idx]

        results: list[BenchmarkResult] = []

        # 1. Heat score signal
        heat_signal = (heat_aligned >= self.heat_threshold).astype(int)
        heat_metrics = self.compute_signal_metrics(heat_signal, returns_aligned)
        heat_strategy = self.backtest_signal_strategy(heat_signal, returns_aligned)
        results.append(BenchmarkResult(
            signal_name="RiskRadar Heat Score",
            precision=heat_metrics["precision"],
            recall=heat_metrics["recall"],
            f1_score=heat_metrics["f1_score"],
            lead_time_days=heat_metrics["lead_time_days"],
            false_positive_rate=heat_metrics["false_positive_rate"],
            strategy_return=heat_strategy["strategy_return"],
            buy_hold_return=heat_strategy["buy_hold_return"],
            max_drawdown_avoided=heat_strategy["max_drawdown_avoided"],
            sharpe_ratio=heat_strategy["sharpe_ratio"],
        ))

        # 2. VIX > 25 signal
        if vix_aligned is not None:
            vix_signal = (vix_aligned > self.vix_threshold).astype(int)
            vix_metrics = self.compute_signal_metrics(vix_signal, returns_aligned)
            vix_strategy = self.backtest_signal_strategy(vix_signal, returns_aligned)
            results.append(BenchmarkResult(
                signal_name="VIX > 25",
                precision=vix_metrics["precision"],
                recall=vix_metrics["recall"],
                f1_score=vix_metrics["f1_score"],
                lead_time_days=vix_metrics["lead_time_days"],
                false_positive_rate=vix_metrics["false_positive_rate"],
                strategy_return=vix_strategy["strategy_return"],
                buy_hold_return=vix_strategy["buy_hold_return"],
                max_drawdown_avoided=vix_strategy["max_drawdown_avoided"],
                sharpe_ratio=vix_strategy["sharpe_ratio"],
            ))

        # 3. 50-day MA cross signal
        cumulative = (1 + returns_aligned).cumprod()
        ma_50 = cumulative.rolling(self.ma_window, min_periods=self.ma_window).mean()
        # Signal = 1 when price below MA (bearish)
        ma_signal = (cumulative < ma_50).astype(int)
        ma_signal = ma_signal.fillna(0).astype(int)

        if ma_signal.sum() > 0:
            ma_metrics = self.compute_signal_metrics(ma_signal, returns_aligned)
            ma_strategy = self.backtest_signal_strategy(ma_signal, returns_aligned)
            results.append(BenchmarkResult(
                signal_name=f"{self.ma_window}-Day MA Cross",
                precision=ma_metrics["precision"],
                recall=ma_metrics["recall"],
                f1_score=ma_metrics["f1_score"],
                lead_time_days=ma_metrics["lead_time_days"],
                false_positive_rate=ma_metrics["false_positive_rate"],
                strategy_return=ma_strategy["strategy_return"],
                buy_hold_return=ma_strategy["buy_hold_return"],
                max_drawdown_avoided=ma_strategy["max_drawdown_avoided"],
                sharpe_ratio=ma_strategy["sharpe_ratio"],
            ))

        # 4. Monthly rebalance signal (first trading day of each month)
        monthly_signal = pd.Series(0, index=common_idx)
        months_seen = set()
        for date in common_idx:
            month_key = (date.year, date.month)
            if month_key not in months_seen:
                months_seen.add(month_key)
                monthly_signal.loc[date] = 1

        monthly_metrics = self.compute_signal_metrics(monthly_signal, returns_aligned)
        monthly_strategy = self.backtest_signal_strategy(
            monthly_signal, returns_aligned, signal_action="rebalance"
        )
        results.append(BenchmarkResult(
            signal_name="Monthly Rebalance",
            precision=monthly_metrics["precision"],
            recall=monthly_metrics["recall"],
            f1_score=monthly_metrics["f1_score"],
            lead_time_days=monthly_metrics["lead_time_days"],
            false_positive_rate=monthly_metrics["false_positive_rate"],
            strategy_return=monthly_strategy["strategy_return"],
            buy_hold_return=monthly_strategy["buy_hold_return"],
            max_drawdown_avoided=monthly_strategy["max_drawdown_avoided"],
            sharpe_ratio=monthly_strategy["sharpe_ratio"],
        ))

        # 5. Correlation-only signal
        if corr_series is not None and not corr_series.empty:
            corr_signal = (corr_series > self.correlation_threshold).astype(int)
            corr_metrics = self.compute_signal_metrics(corr_signal, returns_aligned)
            corr_strategy = self.backtest_signal_strategy(corr_signal, returns_aligned)
            results.append(BenchmarkResult(
                signal_name="Correlation > 0.6",
                precision=corr_metrics["precision"],
                recall=corr_metrics["recall"],
                f1_score=corr_metrics["f1_score"],
                lead_time_days=corr_metrics["lead_time_days"],
                false_positive_rate=corr_metrics["false_positive_rate"],
                strategy_return=corr_strategy["strategy_return"],
                buy_hold_return=corr_strategy["buy_hold_return"],
                max_drawdown_avoided=corr_strategy["max_drawdown_avoided"],
                sharpe_ratio=corr_strategy["sharpe_ratio"],
            ))

        # Determine best signal by F1 score
        best = max(results, key=lambda r: r.f1_score) if results else None
        best_name = best.signal_name if best else "N/A"

        # Build summary
        heat_result = results[0] if results else None
        vix_result = next((r for r in results if "VIX" in r.signal_name), None)

        summary = self._build_summary(heat_result, vix_result)

        return BenchmarkReport(
            results=results,
            best_signal=best_name,
            heat_vs_vix_summary=summary,
            timestamp=datetime.now(timezone.utc),
        )

    def compute_signal_metrics(
        self,
        signal_series: pd.Series,
        returns: pd.Series,
        drawdown_threshold: float | None = None,
    ) -> dict:
        """Compute precision/recall/F1 for a binary signal vs drawdowns.

        A "true positive" is when the signal fires and a drawdown of at least
        `drawdown_threshold` occurs within the next 5 trading days.
        """
        threshold = drawdown_threshold or self.drawdown_threshold

        # Identify drawdown periods: forward-looking 5-day return < threshold
        fwd_returns = returns.rolling(5).sum().shift(-5)
        actual_danger = (fwd_returns < threshold).astype(int)

        # Align
        common = signal_series.index.intersection(actual_danger.dropna().index)
        if len(common) < 10:
            return {
                "precision": 0.0,
                "recall": 0.0,
                "f1_score": 0.0,
                "lead_time_days": 0.0,
                "false_positive_rate": 0.0,
            }

        sig = signal_series.loc[common]
        danger = actual_danger.loc[common]

        tp = int(((sig == 1) & (danger == 1)).sum())
        fp = int(((sig == 1) & (danger == 0)).sum())
        fn = int(((sig == 0) & (danger == 1)).sum())
        tn = int(((sig == 0) & (danger == 0)).sum())

        precision = tp / (tp + fp) if (tp + fp) > 0 else 0.0
        recall = tp / (tp + fn) if (tp + fn) > 0 else 0.0
        f1 = 2 * precision * recall / (precision + recall) if (precision + recall) > 0 else 0.0
        fpr = fp / (fp + tn) if (fp + tn) > 0 else 0.0

        # Lead time: average days between signal firing and drawdown start
        lead_times = []
        danger_starts = danger[danger == 1].index
        for d_start in danger_starts:
            # Look back up to 10 days for a signal
            lookback = sig.loc[:d_start].iloc[-10:]
            signals_before = lookback[lookback == 1]
            if not signals_before.empty:
                first_signal = signals_before.index[0]
                lead = (d_start - first_signal).days
                if lead >= 0:
                    lead_times.append(lead)

        avg_lead = float(np.mean(lead_times)) if lead_times else 0.0

        return {
            "precision": round(float(precision), 4),
            "recall": round(float(recall), 4),
            "f1_score": round(float(f1), 4),
            "lead_time_days": round(avg_lead, 1),
            "false_positive_rate": round(float(fpr), 4),
        }

    def backtest_signal_strategy(
        self,
        signal_series: pd.Series,
        returns: pd.Series,
        signal_action: str = "reduce_50pct",
    ) -> dict:
        """Backtest: what if you followed this signal? Returns vs buy-and-hold.

        signal_action:
            'reduce_50pct': Reduce exposure by 50% when signal fires
            'exit': Go to 0% exposure when signal fires
            'rebalance': No exposure change, just marks rebalance dates
        """
        common = signal_series.index.intersection(returns.index)
        if len(common) < 10:
            return {
                "strategy_return": 0.0,
                "buy_hold_return": 0.0,
                "max_drawdown_avoided": 0.0,
                "sharpe_ratio": 0.0,
            }

        sig = signal_series.loc[common]
        ret = returns.loc[common]

        # Buy-and-hold
        bh_cumulative = (1 + ret).cumprod()
        bh_return = float(bh_cumulative.iloc[-1] - 1)
        bh_peak = bh_cumulative.expanding().max()
        bh_drawdown = (bh_cumulative / bh_peak - 1)
        bh_max_dd = float(bh_drawdown.min())

        # Strategy returns
        if signal_action == "reduce_50pct":
            exposure = pd.Series(1.0, index=common)
            exposure[sig == 1] = 0.5
        elif signal_action == "exit":
            exposure = pd.Series(1.0, index=common)
            exposure[sig == 1] = 0.0
        else:  # rebalance or unknown
            exposure = pd.Series(1.0, index=common)

        strategy_ret = ret * exposure
        strat_cumulative = (1 + strategy_ret).cumprod()
        strat_return = float(strat_cumulative.iloc[-1] - 1)
        strat_peak = strat_cumulative.expanding().max()
        strat_drawdown = (strat_cumulative / strat_peak - 1)
        strat_max_dd = float(strat_drawdown.min())

        # Drawdown avoided
        dd_avoided = strat_max_dd - bh_max_dd  # Positive = strategy had less drawdown

        # Sharpe ratio (annualized)
        if strategy_ret.std() > 0:
            sharpe = float(strategy_ret.mean() / strategy_ret.std() * np.sqrt(252))
        else:
            sharpe = 0.0

        return {
            "strategy_return": round(float(strat_return), 4),
            "buy_hold_return": round(float(bh_return), 4),
            "max_drawdown_avoided": round(float(dd_avoided), 4),
            "sharpe_ratio": round(float(sharpe), 4),
        }

    def _parse_heat_history(self, heat_history: list[dict]) -> pd.DataFrame:
        """Parse heat history from various formats into a DatetimeIndex DataFrame."""
        if not heat_history:
            return pd.DataFrame()

        records = []
        for entry in heat_history:
            # Support different key names
            date_val = entry.get("date") or entry.get("timestamp") or entry.get("time")
            score_val = entry.get("score") or entry.get("heat_score") or entry.get("value")

            if date_val is None or score_val is None:
                continue

            try:
                if isinstance(date_val, str):
                    dt = pd.Timestamp(date_val)
                else:
                    dt = pd.Timestamp(date_val)

                records.append({"date": dt, "score": float(score_val)})
            except (ValueError, TypeError):
                continue

        if not records:
            return pd.DataFrame()

        df = pd.DataFrame(records)
        df.set_index("date", inplace=True)
        df = df.sort_index()
        # Remove timezone info for consistency
        if df.index.tz is not None:
            df.index = df.index.tz_localize(None)
        return df

    def _fetch_vix(self, start: pd.Timestamp, end: pd.Timestamp) -> pd.Series | None:
        """Fetch VIX data via yfinance."""
        try:
            import yfinance as yf

            start_str = start.strftime("%Y-%m-%d")
            end_str = end.strftime("%Y-%m-%d")

            vix = yf.download(
                "^VIX",
                start=start_str,
                end=end_str,
                progress=False,
                auto_adjust=True,
            )
            if vix.empty:
                return None

            if isinstance(vix.columns, pd.MultiIndex):
                close = vix["Close"]["^VIX"]
            elif "Close" in vix.columns:
                close = vix["Close"]
            else:
                return None

            close = close.dropna()
            if close.index.tz is not None:
                close.index = close.index.tz_localize(None)
            return close

        except Exception as e:
            logger.warning("Failed to fetch VIX data: %s", e)
            return None

    def _fetch_portfolio_returns(
        self, start: pd.Timestamp, end: pd.Timestamp
    ) -> pd.Series | None:
        """Fetch equal-weighted portfolio returns for the benchmark symbols."""
        try:
            import yfinance as yf

            symbols = ["NVDA", "AAPL", "MSFT", "AMZN", "GOOGL", "META", "TSLA", "AMD", "AVGO"]
            start_str = start.strftime("%Y-%m-%d")
            end_str = end.strftime("%Y-%m-%d")

            data = yf.download(
                symbols,
                start=start_str,
                end=end_str,
                progress=False,
                auto_adjust=True,
            )
            if data.empty:
                return None

            if isinstance(data.columns, pd.MultiIndex):
                prices = data["Close"]
            else:
                prices = data[["Close"]]
                prices.columns = symbols

            returns = prices.pct_change().dropna()
            # Equal-weighted portfolio
            portfolio = returns.mean(axis=1)
            if portfolio.index.tz is not None:
                portfolio.index = portfolio.index.tz_localize(None)
            return portfolio

        except Exception as e:
            logger.warning("Failed to fetch portfolio returns: %s", e)
            return None

    def _build_summary(
        self, heat_result: BenchmarkResult | None, vix_result: BenchmarkResult | None
    ) -> str:
        """Build a human-readable comparison summary."""
        if heat_result is None:
            return "Insufficient data for comparison"

        parts = []
        parts.append(
            f"RiskRadar Heat: F1={heat_result.f1_score:.2f}, "
            f"Precision={heat_result.precision:.0%}, "
            f"Sharpe={heat_result.sharpe_ratio:.2f}"
        )

        if vix_result:
            parts.append(
                f"VIX>25: F1={vix_result.f1_score:.2f}, "
                f"Precision={vix_result.precision:.0%}, "
                f"Sharpe={vix_result.sharpe_ratio:.2f}"
            )

            if heat_result.f1_score > vix_result.f1_score:
                diff = heat_result.f1_score - vix_result.f1_score
                parts.append(f"Heat score outperforms VIX by {diff:.2f} F1 points")
            elif vix_result.f1_score > heat_result.f1_score:
                diff = vix_result.f1_score - heat_result.f1_score
                parts.append(f"VIX outperforms Heat by {diff:.2f} F1 points")
            else:
                parts.append("Heat and VIX signals perform equally")

        return " | ".join(parts)

    @staticmethod
    def from_history_file(path: str = "data/heat_score_history.json") -> tuple[list[dict], list[float] | None]:
        """Load heat history and correlation data from backtest output file.

        Returns (heat_history, correlation_history).
        """
        filepath = Path(path)
        if not filepath.exists():
            logger.warning("Heat history file not found: %s", path)
            return [], None

        with open(filepath) as f:
            data = json.load(f)

        # Handle different formats
        if isinstance(data, list):
            # List of records
            return data, None
        elif isinstance(data, dict):
            heat_history = data.get("heat_scores", data.get("history", []))
            correlations = data.get("correlations", None)
            return heat_history, correlations

        return [], None
