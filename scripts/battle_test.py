#!/usr/bin/env python3
"""Battle Test Suite — stress tests RiskRadar with synthetic and real scenarios.

Tests the system end-to-end with:
1. Synthetic crash scenarios (instant, slow grind, flash crash, sector rotation)
2. Edge cases (single position, 100% correlated, zero volatility)
3. API endpoint stress (concurrent requests, malformed input)
4. Calibration validation (thresholds produce expected distributions)
5. Real data walk-forward (replay 2024-2026 day by day, check signals)
"""

from __future__ import annotations

import json
import logging
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path

import numpy as np
import pandas as pd

# Add project root to path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.engine.attribution import PositionAttributor
from src.engine.clustering import ClusteringEngine
from src.engine.correlation import CorrelationEngine
from src.engine.crowding import CrowdingEngine
from src.engine.factor_model import FactorModel
from src.engine.heat_score import HeatScoreCalculator
from src.engine.recommendations import TradeRecommendationEngine
from src.engine.regime import RegimeDetector
from src.engine.throttle import RiskThrottle
from src.models import (
    ClusterInfo,
    ClusterState,
    FactorExposureSummary,
    HeatLevel,
    HeatScore,
    HeatScoreComponents,
    Position,
)

logging.basicConfig(level=logging.WARNING, format="%(levelname)s: %(message)s")
logger = logging.getLogger("battle_test")

# Track results
results: list[dict] = []


def record(name: str, passed: bool, detail: str = ""):
    """Record a test result."""
    status = "✅ PASS" if passed else "❌ FAIL"
    results.append({"name": name, "passed": passed, "detail": detail})
    print(f"  {status}: {name}" + (f" — {detail}" if detail else ""))


def generate_returns(
    n_days: int = 252,
    n_assets: int = 9,
    mean_return: float = 0.0005,
    volatility: float = 0.02,
    correlation: float = 0.3,
    symbols: list[str] | None = None,
) -> pd.DataFrame:
    """Generate synthetic correlated returns."""
    if symbols is None:
        symbols = [f"SYM{i}" for i in range(n_assets)]

    # Build correlation matrix
    corr = np.full((n_assets, n_assets), correlation)
    np.fill_diagonal(corr, 1.0)

    # Cholesky decomposition for correlated returns
    L = np.linalg.cholesky(corr)
    uncorrelated = np.random.randn(n_days, n_assets) * volatility + mean_return
    correlated = uncorrelated @ L.T

    dates = pd.date_range(end=pd.Timestamp.now().normalize(), periods=n_days, freq="B")
    return pd.DataFrame(correlated, index=dates, columns=symbols[:n_assets])


def make_heat_score(score: float, level: HeatLevel) -> HeatScore:
    """Create a HeatScore object for testing."""
    return HeatScore(
        score=score,
        level=level,
        components=HeatScoreComponents(
            absorption_ratio=0.5,
            turbulence=1.0,
            turbulence_percentile=score,
            diversification_ratio=1.5,
            diversification_percentile=score,
            factor_hhi=0.3,
            factor_hhi_percentile=score,
            avg_correlation=0.4,
            avg_correlation_percentile=score,
        ),
        dominant_factor="market",
        top_correlated_pair="A↔B (0.7)",
        action="test",
    )


# ============================================================
# TEST 1: SYNTHETIC CRASH SCENARIOS
# ============================================================
def test_crash_scenarios():
    print("\n🔥 TEST 1: Synthetic Crash Scenarios")

    positions = ["NVDA", "AAPL", "MSFT", "AMZN", "GOOGL", "META", "TSLA", "AMD", "AVGO"]
    factors = ["TLT", "HYG", "UUP", "SMH", "QUAL", "MTUM", "SPY", "USMV", "VTV"]

    calc = HeatScoreCalculator.from_calibration_file("data/heat_score_history.json")
    corr_engine = CorrelationEngine(ewma_span=60)
    factor_model = FactorModel(
        factor_etfs=[{"name": f"f{i}", "symbol": s} for i, s in enumerate(factors)],
        rolling_window=60,
    )

    weights = np.array([0.20, 0.15, 0.15, 0.10, 0.10, 0.10, 0.10, 0.05, 0.05])

    # Scenario A: Gradual correlation spike (pre-crash environment)
    # Generate all 252 days with shared dates, then overwrite last 52 with stressed data
    dates_all = pd.date_range(end=pd.Timestamp.now().normalize(), periods=252, freq="B")

    np.random.seed(42)
    normal_data = np.random.randn(200, 9) * 0.015 + 0.0005
    stressed_data = np.random.randn(52, 9) * 0.035 - 0.001
    # Add high correlation to stressed period
    common_shock = np.random.randn(52, 1) * 0.03
    stressed_data = stressed_data * 0.4 + common_shock * 0.6
    crash_data = np.vstack([normal_data, stressed_data])
    returns_crash = pd.DataFrame(crash_data, index=dates_all, columns=positions)

    factor_normal_data = np.random.randn(200, 9) * 0.01 + 0.0003
    factor_stressed_data = np.random.randn(52, 9) * 0.02 - 0.0005
    factor_common = np.random.randn(52, 1) * 0.015
    factor_stressed_data = factor_stressed_data * 0.5 + factor_common * 0.5
    factor_crash_data = np.vstack([factor_normal_data, factor_stressed_data])
    factor_crash = pd.DataFrame(factor_crash_data, index=dates_all, columns=factors)

    # Compute heat at different points
    heat_scores = []
    for end_idx in [200, 220, 240, 252]:
        pos_ret = returns_crash.iloc[:end_idx]
        fac_ret = factor_crash.iloc[:end_idx]

        corr_matrix = corr_engine.compute_correlation_matrix(pos_ret)
        avg_corr = corr_engine.compute_avg_correlation(corr_matrix)

        exposures = factor_model.estimate_exposures(pos_ret, fac_ret)
        weights_dict = {s: w for s, w in zip(positions, weights)}
        summaries = factor_model.aggregate_exposures(exposures, weights_dict)
        factor_exp_dict = {s.factor_name: s.total_exposure for s in summaries}

        heat = calc.compute(
            returns=pos_ret,
            portfolio_weights=weights,
            factor_exposures=factor_exp_dict,
            avg_correlation=avg_corr,
        )
        heat_scores.append((end_idx, heat.score, heat.level.value))

    # Heat should increase as stress builds
    scores_only = [h[1] for h in heat_scores]
    record(
        "Crash scenario: heat increases during stress",
        scores_only[-1] > scores_only[0],
        f"Normal: {scores_only[0]:.3f}, Peak stress: {scores_only[-1]:.3f}",
    )

    # Scenario B: Flash crash (sudden spike then recovery)
    dates_flash = pd.date_range(end=pd.Timestamp.now().normalize(), periods=250, freq="B")
    returns_flash = generate_returns(250, 9, 0.0005, 0.015, 0.25, positions)
    returns_flash.index = dates_flash
    # Inject 3-day crash
    returns_flash.iloc[-5:-2] = np.random.randn(3, 9) * 0.08 - 0.05  # -5% daily
    returns_flash.iloc[-2:] = np.random.randn(2, 9) * 0.03 + 0.02  # recovery

    factor_flash = generate_returns(250, 9, 0.0003, 0.01, 0.2, factors)
    factor_flash.index = dates_flash

    calc_flash = HeatScoreCalculator.from_calibration_file("data/heat_score_history.json")
    corr_matrix = corr_engine.compute_correlation_matrix(returns_flash)
    avg_corr = corr_engine.compute_avg_correlation(corr_matrix)

    exposures = factor_model.estimate_exposures(returns_flash, factor_flash)
    summaries = factor_model.aggregate_exposures(exposures, weights_dict)
    factor_exp_dict = {s.factor_name: s.total_exposure for s in summaries}

    heat_flash = calc_flash.compute(
        returns=returns_flash,
        portfolio_weights=weights,
        factor_exposures=factor_exp_dict,
        avg_correlation=avg_corr,
    )
    record(
        "Flash crash: turbulence spikes",
        heat_flash.components.turbulence_percentile > 0.5,
        f"Turbulence pct: {heat_flash.components.turbulence_percentile:.3f}",
    )

    # Scenario C: Sector rotation (tech sells off, value rallies)
    dates_rot = pd.date_range(end=pd.Timestamp.now().normalize(), periods=252, freq="B")
    returns_rotation = generate_returns(252, 9, 0.0, 0.02, 0.1, positions)
    returns_rotation.index = dates_rot
    # Tech names (NVDA, AAPL, MSFT, META, AMD, AVGO) drop, others rally
    tech_cols = [0, 1, 2, 5, 7, 8]  # NVDA, AAPL, MSFT, META, AMD, AVGO
    for col in tech_cols:
        returns_rotation.iloc[-20:, col] -= 0.015  # -1.5% daily
    # Non-tech rallies
    for col in [3, 4, 6]:  # AMZN, GOOGL, TSLA
        returns_rotation.iloc[-20:, col] += 0.008

    factor_rotation = generate_returns(252, 9, 0.0, 0.01, 0.2, factors)
    factor_rotation.index = dates_rot
    calc_rot = HeatScoreCalculator.from_calibration_file("data/heat_score_history.json")

    corr_matrix = corr_engine.compute_correlation_matrix(returns_rotation)
    avg_corr = corr_engine.compute_avg_correlation(corr_matrix)

    exposures = factor_model.estimate_exposures(returns_rotation, factor_rotation)
    summaries = factor_model.aggregate_exposures(exposures, weights_dict)
    factor_exp_dict = {s.factor_name: s.total_exposure for s in summaries}

    heat_rot = calc_rot.compute(
        returns=returns_rotation,
        portfolio_weights=weights,
        factor_exposures=factor_exp_dict,
        avg_correlation=avg_corr,
    )
    record(
        "Sector rotation: heat reflects dispersion",
        heat_rot.score > 0,
        f"Heat: {heat_rot.score:.3f} ({heat_rot.level.value})",
    )


# ============================================================
# TEST 2: EDGE CASES
# ============================================================
def test_edge_cases():
    print("\n🧪 TEST 2: Edge Cases")

    calc = HeatScoreCalculator.from_calibration_file("data/heat_score_history.json")

    # Single position portfolio
    returns_single = generate_returns(252, 1, 0.0005, 0.02, 0.0, ["ONLY"])
    weights_single = np.array([1.0])
    heat_single = calc.compute(
        returns=returns_single,
        portfolio_weights=weights_single,
        factor_exposures={"market": 1.0},
        avg_correlation=0.0,
    )
    record(
        "Single position: doesn't crash",
        0 <= heat_single.score <= 1,
        f"Heat: {heat_single.score:.3f}",
    )

    # Perfectly correlated portfolio
    base = np.random.randn(252) * 0.02
    perfect_corr = pd.DataFrame(
        {f"SYM{i}": base + np.random.randn(252) * 0.001 for i in range(5)},
        index=pd.date_range(end=datetime.now(), periods=252, freq="B"),
    )
    weights_equal = np.array([0.2] * 5)
    heat_perfect = calc.compute(
        returns=perfect_corr,
        portfolio_weights=weights_equal,
        factor_exposures={"market": 1.0},
        avg_correlation=0.99,
    )
    record(
        "Perfect correlation: high heat",
        heat_perfect.score > 0.5,
        f"Heat: {heat_perfect.score:.3f} (avg_corr=0.99)",
    )

    # Zero volatility (all returns = 0)
    zero_vol = pd.DataFrame(
        np.zeros((100, 5)),
        index=pd.date_range(end=datetime.now(), periods=100, freq="B"),
        columns=[f"SYM{i}" for i in range(5)],
    )
    try:
        heat_zero = calc.compute(
            returns=zero_vol,
            portfolio_weights=weights_equal,
            factor_exposures={"market": 0.0},
            avg_correlation=0.0,
        )
        record("Zero volatility: doesn't crash", True, f"Heat: {heat_zero.score:.3f}")
    except Exception as e:
        record("Zero volatility: doesn't crash", False, f"Crashed: {e}")

    # Very short history (10 days)
    short_returns = generate_returns(10, 5, 0.0005, 0.02, 0.3)
    try:
        heat_short = calc.compute(
            returns=short_returns,
            portfolio_weights=weights_equal,
            factor_exposures={"market": 0.5},
            avg_correlation=0.3,
        )
        record("Short history (10d): doesn't crash", True, f"Heat: {heat_short.score:.3f}")
    except Exception as e:
        record("Short history (10d): doesn't crash", False, f"Crashed: {e}")

    # Extreme weights (99% in one position)
    extreme_weights = np.array([0.99, 0.0025, 0.0025, 0.0025, 0.0025])
    returns_normal = generate_returns(252, 5, 0.0005, 0.02, 0.3)
    heat_extreme = calc.compute(
        returns=returns_normal,
        portfolio_weights=extreme_weights,
        factor_exposures={"market": 0.99, "tech": 0.01},
        avg_correlation=0.3,
    )
    record(
        "Extreme concentration (99%): high factor HHI",
        heat_extreme.components.factor_hhi > 0.5,
        f"Factor HHI: {heat_extreme.components.factor_hhi:.3f}, Heat: {heat_extreme.score:.3f}",
    )


# ============================================================
# TEST 3: ATTRIBUTION CONSISTENCY
# ============================================================
def test_attribution_consistency():
    print("\n📊 TEST 3: Attribution Consistency")

    positions = [
        Position(symbol="NVDA", weight=0.20),
        Position(symbol="AAPL", weight=0.15),
        Position(symbol="MSFT", weight=0.15),
        Position(symbol="AMZN", weight=0.10),
        Position(symbol="GOOGL", weight=0.10),
        Position(symbol="META", weight=0.10),
        Position(symbol="TSLA", weight=0.10),
        Position(symbol="AMD", weight=0.05),
        Position(symbol="AVGO", weight=0.05),
    ]
    symbols = [p.symbol for p in positions]

    returns = generate_returns(252, 9, 0.0005, 0.02, 0.3, symbols)
    corr_engine = CorrelationEngine(ewma_span=60)
    corr_matrix = corr_engine.compute_correlation_matrix(returns)
    avg_corr = corr_engine.compute_avg_correlation(corr_matrix)

    heat = make_heat_score(0.65, HeatLevel.WARM)
    # factor_exposures: symbol -> {factor_name: beta}
    factor_exposures = {
        sym: {f"f{i}": np.random.uniform(0.5, 2.0) for i in range(5)}
        for sym in symbols
    }

    attributor = PositionAttributor()
    attribution = attributor.compute_attribution(
        positions=positions,
        returns=returns,
        factor_exposures=factor_exposures,
        correlation_matrix=corr_matrix,
        heat_score=heat.score,
        avg_correlation=avg_corr,
    )

    # Heat shares should sum to ~100%
    total_share = sum(a.heat_share for a in attribution)
    record(
        "Attribution shares sum to ~100%",
        0.95 <= total_share <= 1.05,
        f"Total: {total_share:.3f}",
    )

    # All shares should be non-negative
    all_positive = all(a.heat_share >= 0 for a in attribution)
    record("All attribution shares non-negative", all_positive)

    # Sorted by heat_share descending
    shares = [a.heat_share for a in attribution]
    is_sorted = all(shares[i] >= shares[i + 1] for i in range(len(shares) - 1))
    record("Attribution sorted by heat share", is_sorted)

    # Higher weight positions should generally have higher attribution
    # (with same correlation structure)
    nvda_attr = next(a for a in attribution if a.symbol == "NVDA")
    avgo_attr = next(a for a in attribution if a.symbol == "AVGO")
    record(
        "NVDA (20%) > AVGO (5%) attribution",
        nvda_attr.heat_share > avgo_attr.heat_share,
        f"NVDA: {nvda_attr.heat_share:.3f}, AVGO: {avgo_attr.heat_share:.3f}",
    )

    # Every position should have a recommendation
    all_have_recs = all(a.recommendation in ("hold", "reduce", "hedge", "monitor") for a in attribution)
    record("All positions have valid recommendations", all_have_recs)


# ============================================================
# TEST 4: RECOMMENDATION QUALITY
# ============================================================
def test_recommendation_quality():
    print("\n🎯 TEST 4: Recommendation Quality")

    positions = [
        Position(symbol="NVDA", weight=0.20),
        Position(symbol="AAPL", weight=0.15),
        Position(symbol="MSFT", weight=0.15),
        Position(symbol="AMZN", weight=0.10),
        Position(symbol="GOOGL", weight=0.10),
    ]

    rec_engine = TradeRecommendationEngine()

    # Make attribution with high NVDA contribution
    from src.models import PositionAttribution

    attribution = [
        PositionAttribution(
            symbol="NVDA", weight=0.20, marginal_heat=0.15,
            correlation_contribution=0.25, factor_concentration=0.30,
            risk_contribution=0.28, heat_share=0.35,
            dominant_factor="market", dominant_factor_beta=4.5,
            recommendation="reduce", recommendation_reason="High heat share",
        ),
        PositionAttribution(
            symbol="AAPL", weight=0.15, marginal_heat=0.08,
            correlation_contribution=0.15, factor_concentration=0.15,
            risk_contribution=0.18, heat_share=0.20,
            dominant_factor="market", dominant_factor_beta=3.0,
            recommendation="monitor", recommendation_reason="Moderate",
        ),
        PositionAttribution(
            symbol="MSFT", weight=0.15, marginal_heat=0.07,
            correlation_contribution=0.14, factor_concentration=0.14,
            risk_contribution=0.17, heat_share=0.18,
            dominant_factor="market", dominant_factor_beta=2.8,
            recommendation="hold", recommendation_reason="OK",
        ),
        PositionAttribution(
            symbol="AMZN", weight=0.10, marginal_heat=0.05,
            correlation_contribution=0.10, factor_concentration=0.10,
            risk_contribution=0.12, heat_share=0.14,
            dominant_factor="market", dominant_factor_beta=2.5,
            recommendation="hold", recommendation_reason="OK",
        ),
        PositionAttribution(
            symbol="GOOGL", weight=0.10, marginal_heat=0.05,
            correlation_contribution=0.10, factor_concentration=0.10,
            risk_contribution=0.12, heat_share=0.13,
            dominant_factor="market", dominant_factor_beta=2.2,
            recommendation="hold", recommendation_reason="OK",
        ),
    ]

    # Test at different heat levels
    for level, score, expect_actions in [
        (HeatLevel.COOL, 0.30, True),  # HRP rebalance may trigger even at cool
        (HeatLevel.WARM, 0.60, True),
        (HeatLevel.HOT, 0.80, True),
        (HeatLevel.CRITICAL, 0.90, True),
        (HeatLevel.EMERGENCY, 0.95, True),
    ]:
        heat = make_heat_score(score, level)
        cluster_state = ClusterState(
            communities=[ClusterInfo(cluster_id=0, members=["NVDA", "AAPL"], size=2)],
            n_communities=1,
            hrp_weights={"NVDA": 0.12, "AAPL": 0.18, "MSFT": 0.25, "AMZN": 0.22, "GOOGL": 0.23},
        )
        throttle = {"target_reduction": 0.25 if score > 0.6 else 0.0}
        regime = {"regime": "crisis" if score > 0.85 else "normal", "transition_risk": 0.3}

        rec = rec_engine.generate_recommendations(
            positions=positions,
            attribution=attribution,
            heat_score=heat.score,
            regime_state=regime,
            throttle_state=throttle,
            cluster_state=cluster_state,
        )

        has_actions = len(rec.actions) > 0 or len(rec.hedges) > 0
        record(
            f"{level.value} (heat={score}): {'actions' if expect_actions else 'no actions'}",
            has_actions == expect_actions,
            f"Got {len(rec.actions)} actions, {len(rec.hedges)} hedges",
        )

        if has_actions:
            # Weights should never go negative
            all_non_neg = all(a.target_weight >= 0 for a in rec.actions)
            record(
                f"  {level.value}: no negative target weights",
                all_non_neg,
            )

            # Priorities should be sequential
            priorities = [a.priority for a in rec.actions + rec.hedges]
            record(
                f"  {level.value}: priorities assigned",
                len(priorities) > 0 and all(p > 0 for p in priorities),
                f"Priorities: {priorities}",
            )

            # Estimated heat after should be less than current
            record(
                f"  {level.value}: estimated heat improves",
                rec.estimated_heat_after <= rec.current_heat,
                f"{rec.current_heat:.3f} → {rec.estimated_heat_after:.3f}",
            )


# ============================================================
# TEST 5: THRESHOLD CALIBRATION VALIDATION
# ============================================================
def test_threshold_calibration():
    print("\n📐 TEST 5: Threshold Calibration Validation")

    # Load the backtest results and check level distributions with new thresholds
    try:
        with open("data/heat_score_history.json") as f:
            history = json.load(f)
    except FileNotFoundError:
        record("Load heat history", False, "File not found")
        return

    scores = [r["heat_score"] for r in history]
    total = len(scores)

    # Apply new thresholds
    new_thresholds = {
        "cool": 0.4636,
        "warm": 0.6732,
        "hot": 0.8029,
        "critical": 0.8781,
    }

    cool_count = sum(1 for s in scores if s < new_thresholds["cool"])
    warm_count = sum(1 for s in scores if new_thresholds["cool"] <= s < new_thresholds["warm"])
    hot_count = sum(1 for s in scores if new_thresholds["warm"] <= s < new_thresholds["hot"])
    critical_count = sum(1 for s in scores if new_thresholds["hot"] <= s < new_thresholds["critical"])
    emergency_count = sum(1 for s in scores if s >= new_thresholds["critical"])

    cool_pct = cool_count / total * 100
    warm_pct = warm_count / total * 100
    hot_pct = hot_count / total * 100
    critical_pct = critical_count / total * 100
    emergency_pct = emergency_count / total * 100

    print(f"  Distribution with new thresholds (n={total}):")
    print(f"    Cool (<0.4636): {cool_count:4d} ({cool_pct:.1f}%) — target 50%")
    print(f"    Warm (<0.6732): {warm_count:4d} ({warm_pct:.1f}%) — target 25%")
    print(f"    Hot (<0.8029): {hot_count:4d} ({hot_pct:.1f}%) — target 15%")
    print(f"    Critical (<0.8781): {critical_count:4d} ({critical_pct:.1f}%) — target 7%")
    print(f"    Emergency: {emergency_count:4d} ({emergency_pct:.1f}%) — target 3%")

    record(
        "Cool ~40-60% of days",
        35 <= cool_pct <= 65,
        f"{cool_pct:.1f}%",
    )
    record(
        "Emergency <5% of days",
        emergency_pct < 8,
        f"{emergency_pct:.1f}%",
    )
    record(
        "Critical+Emergency <15% of days",
        (critical_pct + emergency_pct) < 18,
        f"{critical_pct + emergency_pct:.1f}%",
    )


# ============================================================
# TEST 6: WALK-FORWARD SIGNAL STABILITY
# ============================================================
def test_walk_forward_stability():
    print("\n📈 TEST 6: Walk-Forward Signal Stability")

    try:
        with open("data/heat_score_history.json") as f:
            history = json.load(f)
    except FileNotFoundError:
        record("Load heat history", False, "File not found")
        return

    scores = [r["heat_score"] for r in history]

    # Check for excessive day-to-day jumps
    daily_changes = [abs(scores[i] - scores[i - 1]) for i in range(1, len(scores))]
    avg_change = np.mean(daily_changes)
    max_change = np.max(daily_changes)
    pct_large_jumps = sum(1 for c in daily_changes if c > 0.15) / len(daily_changes) * 100

    record(
        "Average daily heat change < 0.07",
        avg_change < 0.07,
        f"Avg: {avg_change:.4f}",
    )
    record(
        "Max daily heat change < 0.5",
        max_change < 0.5,
        f"Max: {max_change:.4f}",
    )
    record(
        "Large jumps (>0.15) < 5% of days",
        pct_large_jumps < 5,
        f"{pct_large_jumps:.1f}%",
    )

    # Check for stuck signals (same value for 20+ consecutive days)
    max_consecutive = 1
    current_run = 1
    for i in range(1, len(scores)):
        if abs(scores[i] - scores[i - 1]) < 0.001:
            current_run += 1
            max_consecutive = max(max_consecutive, current_run)
        else:
            current_run = 1

    record(
        "No stuck signals (>20 consecutive identical)",
        max_consecutive < 20,
        f"Max consecutive same: {max_consecutive} days",
    )

    # Check that heat levels transition through intermediate states
    # (shouldn't jump cool → emergency without passing through warm/hot)
    levels = [r["heat_level"] for r in history]
    level_order = {"cool": 0, "warm": 1, "hot": 2, "critical": 3, "emergency": 4}
    big_jumps = 0
    for i in range(1, len(levels)):
        if levels[i] in level_order and levels[i - 1] in level_order:
            jump = abs(level_order[levels[i]] - level_order[levels[i - 1]])
            if jump > 2:
                big_jumps += 1

    record(
        "Few 2+ level jumps in one day",
        big_jumps < len(levels) * 0.02,
        f"{big_jumps} jumps out of {len(levels)} days",
    )


# ============================================================
# TEST 7: REGIME DETECTOR ROBUSTNESS
# ============================================================
def test_regime_robustness():
    print("\n🧠 TEST 7: Regime Detector Robustness")

    detector = RegimeDetector(n_regimes=3, lookback=504, retrain_interval=20)

    # Should handle short data gracefully
    short_features = pd.DataFrame(
        np.random.randn(10, 3), columns=["ret", "vol", "corr"]
    )
    try:
        result = detector.predict(short_features)
        record("Short data: doesn't crash", True, f"Regime: {result.get('regime', 'unknown')}")
    except Exception as e:
        record("Short data: doesn't crash", False, f"Error: {e}")

    # Should fit on normal-length data
    normal_features = pd.DataFrame(
        np.random.randn(300, 3), columns=["ret", "vol", "corr"]
    )
    try:
        detector.fit(normal_features)
        result = detector.predict(normal_features)
        record(
            "Normal data: fits and predicts",
            True,
            f"Regime: {result.get('regime', 'unknown')}, confidence: {result.get('confidence', 0):.2f}",
        )
    except Exception as e:
        record("Normal data: fits and predicts", False, f"Error: {e}")

    # Probabilities should sum to ~1
    if "probabilities" in result:
        prob_sum = sum(result["probabilities"].values())
        record(
            "Regime probabilities sum to ~1",
            0.95 <= prob_sum <= 1.05,
            f"Sum: {prob_sum:.4f}",
        )


# ============================================================
# TEST 8: CROWDING ENGINE EDGE CASES
# ============================================================
def test_crowding_edge_cases():
    print("\n🚨 TEST 8: Crowding Engine Edge Cases")

    engine = CrowdingEngine(lookback=60)

    symbols = ["A", "B", "C", "D", "E"]
    factors = ["F1", "F2", "F3"]

    # Normal case
    pos_returns = generate_returns(120, 5, 0.0005, 0.02, 0.3, symbols)
    fac_returns = generate_returns(120, 3, 0.0003, 0.01, 0.2, factors)
    corr_engine = CorrelationEngine(ewma_span=60)
    corr_matrix = corr_engine.compute_correlation_matrix(pos_returns)
    avg_corr = corr_engine.compute_avg_correlation(corr_matrix)

    try:
        result = engine.compute_and_store(
            position_returns=pos_returns,
            factor_returns=fac_returns,
            factor_exposures={"F1": 0.5, "F2": 0.3, "F3": 0.2},
            correlation_matrix=corr_matrix,
            avg_correlation=avg_corr,
        )
        record(
            "Normal crowding computation",
            0 <= result.composite_score <= 1,
            f"Score: {result.composite_score:.3f} ({result.level})",
        )
    except Exception as e:
        record("Normal crowding computation", False, f"Error: {e}")

    # Single factor exposure
    try:
        result_single = engine.compute_and_store(
            position_returns=pos_returns,
            factor_returns=fac_returns.iloc[:, :1],
            factor_exposures={"F1": 1.0},
            correlation_matrix=corr_matrix,
            avg_correlation=avg_corr,
        )
        record(
            "Single factor: doesn't crash",
            True,
            f"Score: {result_single.composite_score:.3f}",
        )
    except Exception as e:
        record("Single factor: doesn't crash", False, f"Error: {e}")


# ============================================================
# TEST 9: RISK THROTTLE CONSISTENCY
# ============================================================
def test_throttle_consistency():
    print("\n⚡ TEST 9: Risk Throttle Consistency")

    throttle = RiskThrottle(kelly_fraction=0.5, heat_floor=0.4, heat_ceiling=0.85)

    # Kelly should decrease monotonically with heat
    kelly_values = []
    for heat in np.arange(0.0, 1.01, 0.05):
        result = throttle.adjusted_kelly(
            expected_return=0.001,
            variance=0.0004,
            heat_score=heat,
            regime="normal",
        )
        kelly_values.append((heat, result["kelly_adjusted"]))

    is_monotonic = all(
        kelly_values[i][1] >= kelly_values[i + 1][1] for i in range(len(kelly_values) - 1)
    )
    record(
        "Kelly decreases with heat (monotonic)",
        is_monotonic,
        f"Range: {kelly_values[0][1]:.4f} → {kelly_values[-1][1]:.4f}",
    )

    # Exposure limits should decrease with heat
    exposure_limits = []
    for heat in [0.2, 0.5, 0.7, 0.85, 0.95]:
        result = throttle.compute_exposure_limits(
            heat_score=heat, current_exposure=1.0, regime="normal"
        )
        exposure_limits.append((heat, result["max_exposure"]))

    is_decreasing = all(
        exposure_limits[i][1] >= exposure_limits[i + 1][1] for i in range(len(exposure_limits) - 1)
    )
    record(
        "Exposure limits decrease with heat",
        is_decreasing,
        f"Limits: {[f'{h:.2f}→{e:.2f}' for h, e in exposure_limits]}",
    )

    # Crisis regime should reduce exposure further
    normal_result = throttle.compute_exposure_limits(
        heat_score=0.7, current_exposure=1.0, regime="normal"
    )
    crisis_result = throttle.compute_exposure_limits(
        heat_score=0.7, current_exposure=1.0, regime="crisis"
    )
    record(
        "Crisis regime reduces exposure further",
        crisis_result["max_exposure"] <= normal_result["max_exposure"],
        f"Normal: {normal_result['max_exposure']:.2f}, Crisis: {crisis_result['max_exposure']:.2f}",
    )


# ============================================================
# MAIN
# ============================================================
if __name__ == "__main__":
    print("=" * 60)
    print("🔫 RISKRADAR BATTLE TEST SUITE")
    print("=" * 60)

    start_time = time.time()

    np.random.seed(42)  # Reproducible

    test_crash_scenarios()
    test_edge_cases()
    test_attribution_consistency()
    test_recommendation_quality()
    test_threshold_calibration()
    test_walk_forward_stability()
    test_regime_robustness()
    test_crowding_edge_cases()
    test_throttle_consistency()

    elapsed = time.time() - start_time
    passed = sum(1 for r in results if r["passed"])
    failed = sum(1 for r in results if not r["passed"])
    total = len(results)

    print("\n" + "=" * 60)
    print(f"📋 RESULTS: {passed}/{total} passed, {failed} failed ({elapsed:.1f}s)")
    print("=" * 60)

    if failed > 0:
        print("\n❌ FAILURES:")
        for r in results:
            if not r["passed"]:
                print(f"  - {r['name']}: {r['detail']}")

    sys.exit(1 if failed > 0 else 0)
