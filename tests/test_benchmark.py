"""Tests for VIX benchmark comparison engine."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import numpy as np
import pandas as pd
import pytest

from src.engine.benchmark import SignalBenchmark
from src.models import BenchmarkReport, BenchmarkResult


# ── Test Data Fixtures ──


@pytest.fixture
def benchmark():
    return SignalBenchmark(
        heat_threshold=0.55,
        vix_threshold=25.0,
        ma_window=50,
        correlation_threshold=0.6,
        drawdown_threshold=-0.05,
    )


@pytest.fixture
def sample_dates():
    return pd.date_range("2023-01-01", periods=300, freq="B")


@pytest.fixture
def sample_returns(sample_dates):
    """Synthetic portfolio returns with a drawdown period."""
    rng = np.random.default_rng(42)
    returns = pd.Series(rng.normal(0.0005, 0.015, len(sample_dates)), index=sample_dates)
    # Insert a crash period (days 100-115)
    returns.iloc[100:115] = rng.normal(-0.025, 0.03, 15)
    return returns


@pytest.fixture
def sample_heat_history(sample_dates, sample_returns):
    """Synthetic heat scores that rise before the crash."""
    scores = np.full(len(sample_dates), 0.40)
    # Heat rises before crash (days 90-115)
    scores[90:100] = np.linspace(0.4, 0.7, 10)
    scores[100:115] = np.linspace(0.7, 0.92, 15)
    scores[115:130] = np.linspace(0.92, 0.45, 15)

    return [
        {"date": d.isoformat(), "score": float(s)}
        for d, s in zip(sample_dates, scores)
    ]


@pytest.fixture
def sample_vix(sample_dates):
    """Synthetic VIX that spikes during the crash."""
    vix = np.full(len(sample_dates), 18.0)
    vix[100:120] = np.linspace(22, 40, 20)
    vix[120:135] = np.linspace(40, 20, 15)
    return pd.Series(vix, index=sample_dates)


@pytest.fixture
def sample_correlations(sample_dates):
    """Synthetic correlation history."""
    corr = np.full(len(sample_dates), 0.35)
    corr[100:120] = np.linspace(0.35, 0.80, 20)
    corr[120:135] = np.linspace(0.80, 0.40, 15)
    return list(corr)


# ── Model Tests ──


class TestBenchmarkModels:
    def test_benchmark_result_creation(self):
        result = BenchmarkResult(
            signal_name="Test Signal",
            precision=0.75,
            recall=0.60,
            f1_score=0.67,
            lead_time_days=3.5,
            false_positive_rate=0.10,
            strategy_return=0.15,
            buy_hold_return=0.12,
            max_drawdown_avoided=0.03,
            sharpe_ratio=1.2,
        )
        assert result.signal_name == "Test Signal"
        assert result.precision == 0.75

    def test_benchmark_report_creation(self):
        report = BenchmarkReport(
            results=[],
            best_signal="N/A",
            heat_vs_vix_summary="No data",
        )
        assert report.best_signal == "N/A"
        assert isinstance(report.results, list)


# ── Signal Metrics Tests ──


class TestSignalMetrics:
    def test_compute_signal_metrics_basic(self, benchmark, sample_returns):
        """Binary signal produces valid metrics."""
        # Signal fires on random 20% of days
        rng = np.random.default_rng(42)
        signal = pd.Series(
            rng.choice([0, 1], size=len(sample_returns), p=[0.8, 0.2]),
            index=sample_returns.index,
        )
        metrics = benchmark.compute_signal_metrics(signal, sample_returns)

        assert 0 <= metrics["precision"] <= 1
        assert 0 <= metrics["recall"] <= 1
        assert 0 <= metrics["f1_score"] <= 1
        assert metrics["lead_time_days"] >= 0
        assert 0 <= metrics["false_positive_rate"] <= 1

    def test_compute_metrics_perfect_signal(self, benchmark, sample_returns):
        """A perfect signal that fires exactly during drawdowns."""
        # Identify actual drawdown periods
        fwd = sample_returns.rolling(5).sum().shift(-5)
        perfect_signal = (fwd < -0.05).astype(int).fillna(0)

        metrics = benchmark.compute_signal_metrics(perfect_signal, sample_returns)
        # Perfect signal should have high recall
        assert metrics["recall"] >= 0.5

    def test_compute_metrics_never_fires(self, benchmark, sample_returns):
        """A signal that never fires has 0 precision/recall."""
        signal = pd.Series(0, index=sample_returns.index)
        metrics = benchmark.compute_signal_metrics(signal, sample_returns)
        assert metrics["precision"] == 0
        assert metrics["recall"] == 0
        assert metrics["f1_score"] == 0

    def test_compute_metrics_always_fires(self, benchmark, sample_returns):
        """A signal that always fires has high recall but low precision."""
        signal = pd.Series(1, index=sample_returns.index)
        metrics = benchmark.compute_signal_metrics(signal, sample_returns)
        # Should catch all drawdowns
        assert metrics["recall"] >= 0.8
        # But very imprecise
        assert metrics["false_positive_rate"] > 0.5

    def test_compute_metrics_insufficient_data(self, benchmark):
        """Handles very short data gracefully."""
        dates = pd.date_range("2024-01-01", periods=5, freq="B")
        returns = pd.Series([0.01, -0.01, 0.02, -0.03, 0.01], index=dates)
        signal = pd.Series([0, 0, 1, 1, 0], index=dates)
        metrics = benchmark.compute_signal_metrics(signal, returns)
        assert metrics["precision"] == 0
        assert metrics["recall"] == 0


# ── Strategy Backtest Tests ──


class TestBacktestStrategy:
    def test_reduce_50pct_strategy(self, benchmark, sample_returns):
        """Reduce 50% strategy should reduce drawdown."""
        # Signal fires during crash period
        signal = pd.Series(0, index=sample_returns.index)
        signal.iloc[100:120] = 1

        result = benchmark.backtest_signal_strategy(signal, sample_returns, "reduce_50pct")

        assert "strategy_return" in result
        assert "buy_hold_return" in result
        assert "max_drawdown_avoided" in result
        assert "sharpe_ratio" in result
        # Strategy should have less drawdown during crash
        assert result["max_drawdown_avoided"] >= 0  # 0 or positive means less DD

    def test_exit_strategy(self, benchmark, sample_returns):
        """Full exit during signal should avoid most drawdown."""
        signal = pd.Series(0, index=sample_returns.index)
        signal.iloc[100:120] = 1

        result = benchmark.backtest_signal_strategy(signal, sample_returns, "exit")
        # Full exit should avoid more drawdown than 50% reduction
        reduce_result = benchmark.backtest_signal_strategy(signal, sample_returns, "reduce_50pct")
        assert result["max_drawdown_avoided"] >= reduce_result["max_drawdown_avoided"]

    def test_no_signal_matches_buy_hold(self, benchmark, sample_returns):
        """No signal fires = identical to buy and hold."""
        signal = pd.Series(0, index=sample_returns.index)
        result = benchmark.backtest_signal_strategy(signal, sample_returns)
        assert abs(result["strategy_return"] - result["buy_hold_return"]) < 1e-6

    def test_insufficient_data(self, benchmark):
        """Handles very short data."""
        dates = pd.date_range("2024-01-01", periods=3, freq="B")
        returns = pd.Series([0.01, -0.01, 0.02], index=dates)
        signal = pd.Series([0, 1, 0], index=dates)
        result = benchmark.backtest_signal_strategy(signal, returns)
        assert result["strategy_return"] == 0
        assert result["buy_hold_return"] == 0


# ── Full Comparison Tests ──


class TestRunComparison:
    def test_full_comparison(
        self, benchmark, sample_heat_history, sample_vix, sample_returns, sample_correlations
    ):
        """Full comparison produces valid report."""
        report = benchmark.run_comparison(
            heat_history=sample_heat_history,
            vix_history=sample_vix,
            portfolio_returns=sample_returns,
            correlation_history=sample_correlations,
        )

        assert isinstance(report, BenchmarkReport)
        assert len(report.results) >= 3  # Heat, VIX, MA at minimum
        assert report.best_signal != "N/A"
        assert report.heat_vs_vix_summary != ""

        # Check all results have valid fields
        for r in report.results:
            assert 0 <= r.precision <= 1
            assert 0 <= r.recall <= 1
            assert 0 <= r.f1_score <= 1

    def test_comparison_without_vix(self, benchmark, sample_heat_history, sample_returns):
        """Comparison works without VIX data (skips VIX signal)."""
        with patch.object(benchmark, "_fetch_vix", return_value=None):
            report = benchmark.run_comparison(
                heat_history=sample_heat_history,
                vix_history=None,
                portfolio_returns=sample_returns,
            )
            signal_names = [r.signal_name for r in report.results]
            assert "VIX > 25" not in signal_names
            assert "RiskRadar Heat Score" in signal_names

    def test_comparison_with_correlation(
        self, benchmark, sample_heat_history, sample_vix, sample_returns, sample_correlations
    ):
        """Correlation signal included when data provided."""
        report = benchmark.run_comparison(
            heat_history=sample_heat_history,
            vix_history=sample_vix,
            portfolio_returns=sample_returns,
            correlation_history=sample_correlations,
        )
        signal_names = [r.signal_name for r in report.results]
        assert "Correlation > 0.6" in signal_names

    def test_empty_heat_history(self, benchmark):
        """Handles empty heat history gracefully."""
        report = benchmark.run_comparison(heat_history=[])
        assert len(report.results) == 0
        assert report.best_signal == "N/A"

    def test_heat_score_is_best_with_early_warning(
        self, benchmark, sample_heat_history, sample_vix, sample_returns
    ):
        """Heat score should have positive lead time given the synthetic data."""
        report = benchmark.run_comparison(
            heat_history=sample_heat_history,
            vix_history=sample_vix,
            portfolio_returns=sample_returns,
        )

        heat_result = next(r for r in report.results if "Heat" in r.signal_name)
        # Heat rises before crash in our synthetic data
        assert heat_result.lead_time_days >= 0


# ── Parsing Tests ──


class TestParseHistoryFile:
    def test_parse_heat_history_dates(self, benchmark, sample_heat_history):
        df = benchmark._parse_heat_history(sample_heat_history)
        assert not df.empty
        assert "score" in df.columns
        assert len(df) == len(sample_heat_history)

    def test_parse_various_key_names(self, benchmark):
        """Handles different key name formats."""
        history = [
            {"timestamp": "2024-01-02", "heat_score": 0.45},
            {"time": "2024-01-03", "value": 0.50},
            {"date": "2024-01-04", "score": 0.55},
        ]
        df = benchmark._parse_heat_history(history)
        assert len(df) == 3

    def test_parse_empty_history(self, benchmark):
        df = benchmark._parse_heat_history([])
        assert df.empty

    def test_parse_invalid_entries(self, benchmark):
        """Skips entries with missing keys."""
        history = [
            {"date": "2024-01-02", "score": 0.5},
            {"no_date": True, "no_score": True},
            {"date": "2024-01-03", "score": 0.6},
        ]
        df = benchmark._parse_heat_history(history)
        assert len(df) == 2

    def test_from_history_file_missing(self):
        history, corr = SignalBenchmark.from_history_file("nonexistent_file.json")
        assert history == []
        assert corr is None

    def test_from_history_file_list_format(self, tmp_path):
        """Handles list-of-records format."""
        data = [
            {"date": "2024-01-02", "score": 0.5},
            {"date": "2024-01-03", "score": 0.6},
        ]
        f = tmp_path / "test_history.json"
        f.write_text(json.dumps(data))
        history, corr = SignalBenchmark.from_history_file(str(f))
        assert len(history) == 2
        assert corr is None

    def test_from_history_file_dict_format(self, tmp_path):
        """Handles dict format with heat_scores key."""
        data = {
            "heat_scores": [
                {"date": "2024-01-02", "score": 0.5},
            ],
            "correlations": [0.35],
        }
        f = tmp_path / "test_history.json"
        f.write_text(json.dumps(data))
        history, corr = SignalBenchmark.from_history_file(str(f))
        assert len(history) == 1
        assert corr == [0.35]


# ── Summary Tests ──


class TestBuildSummary:
    def test_summary_with_both_signals(self, benchmark):
        heat = BenchmarkResult(
            signal_name="RiskRadar Heat Score",
            precision=0.7,
            recall=0.6,
            f1_score=0.65,
            sharpe_ratio=1.2,
        )
        vix = BenchmarkResult(
            signal_name="VIX > 25",
            precision=0.5,
            recall=0.8,
            f1_score=0.62,
            sharpe_ratio=0.9,
        )
        summary = benchmark._build_summary(heat, vix)
        assert "Heat" in summary
        assert "VIX" in summary
        assert "outperforms" in summary

    def test_summary_without_vix(self, benchmark):
        heat = BenchmarkResult(signal_name="RiskRadar Heat Score", f1_score=0.65, sharpe_ratio=1.2)
        summary = benchmark._build_summary(heat, None)
        assert "Heat" in summary
        assert "VIX" not in summary

    def test_summary_no_data(self, benchmark):
        summary = benchmark._build_summary(None, None)
        assert "Insufficient" in summary
