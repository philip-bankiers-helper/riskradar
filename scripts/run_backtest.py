#!/usr/bin/env python3
"""Run the real historical backtest against all crisis events.

Fetches 2019-2026 data from yfinance and replays heat score,
correlation, and crowding computations day by day.
"""

import json
import sys
import time
from pathlib import Path

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent.parent))

import numpy as np
import pandas as pd

from src.engine.backtest import BacktestEngine, CRISIS_EVENTS
from src.engine.correlation import CorrelationEngine
from src.engine.heat_score import HeatScoreCalculator
from src.engine.crowding import CrowdingEngine


POSITION_SYMBOLS = ["NVDA", "AAPL", "MSFT", "AMZN", "GOOGL", "META", "TSLA", "AMD", "AVGO"]
FACTOR_SYMBOLS = ["TLT", "HYG", "UUP", "SMH", "QUAL", "MTUM", "SPY", "USMV", "VTV"]


def run_full_rolling_backtest(returns: pd.DataFrame) -> dict:
    """Run day-by-day rolling heat score computation across entire history.
    
    This gives us:
    1. Full heat score time series for calibration
    2. Per-crisis analysis
    3. False positive / true positive rates
    4. Signal lead times
    """
    pos_cols = [s for s in POSITION_SYMBOLS if s in returns.columns]
    fac_cols = [s for s in FACTOR_SYMBOLS if s in returns.columns]
    
    print(f"Available positions: {pos_cols}")
    print(f"Available factors: {fac_cols}")
    print(f"Date range: {returns.index[0]} to {returns.index[-1]}")
    print(f"Total trading days: {len(returns)}")
    print()
    
    pos_returns = returns[pos_cols]
    fac_returns = returns[fac_cols]
    
    n_pos = len(pos_cols)
    equal_weights = np.ones(n_pos) / n_pos
    
    corr_engine = CorrelationEngine(ewma_span=60)
    heat_calc = HeatScoreCalculator()
    crowding_engine = CrowdingEngine(lookback=60)
    
    # Rolling computation
    window = 60
    results = []
    
    print(f"Running rolling {window}-day heat score computation...")
    start_time = time.time()
    
    for i in range(window, len(pos_returns)):
        date = pos_returns.index[i]
        pos_window = pos_returns.iloc[i - window:i]
        fac_window = fac_returns.iloc[i - window:i]
        
        try:
            # Correlation
            corr = corr_engine.compute_correlation_matrix(pos_window)
            avg_corr = corr_engine.compute_avg_correlation(corr)
            sym_a, sym_b, top_corr = corr_engine.get_top_correlated_pair(corr)
            
            # Heat score
            heat = heat_calc.compute(
                returns=pos_window,
                portfolio_weights=equal_weights,
                factor_exposures={},
                avg_correlation=avg_corr,
            )
            
            # Crowding (simplified — no factor exposures in backtest)
            crowding = crowding_engine.compute_crowding(
                position_returns=pos_window,
                factor_returns=fac_window,
                factor_exposures={},
                avg_correlation=avg_corr,
            )
            
            results.append({
                "date": str(date.date()),
                "heat_score": round(heat.score, 4),
                "heat_level": heat.level.value,
                "absorption_ratio": round(heat.components.absorption_ratio, 4),
                "turbulence": round(heat.components.turbulence, 4),
                "turbulence_pct": round(heat.components.turbulence_percentile, 4),
                "div_ratio": round(heat.components.diversification_ratio, 4),
                "div_ratio_pct": round(heat.components.diversification_percentile, 4),
                "factor_hhi": round(heat.components.factor_hhi, 4),
                "avg_corr": round(avg_corr, 4),
                "avg_corr_pct": round(heat.components.avg_correlation_percentile, 4),
                "top_pair": f"{sym_a}-{sym_b}" if sym_a else "",
                "top_corr": round(top_corr, 4),
                "crowding_score": round(crowding.composite_score, 4),
                "crowding_level": crowding.level,
                "crowding_alpha": round(crowding.crowding_alpha_signal, 4),
            })
            
        except Exception as e:
            if i % 100 == 0:
                print(f"  Error at {date}: {e}")
    
    elapsed = time.time() - start_time
    print(f"Computed {len(results)} daily observations in {elapsed:.1f}s")
    print()
    
    return results


def analyze_crisis_performance(results: list[dict], crisis_events: list) -> list[dict]:
    """Analyze heat score behavior around each crisis."""
    df = pd.DataFrame(results)
    df["date"] = pd.to_datetime(df["date"])
    
    crisis_analysis = []
    
    for crisis in crisis_events:
        start = pd.Timestamp(crisis.start_date)
        peak = pd.Timestamp(crisis.peak_date)
        end = pd.Timestamp(crisis.end_date)
        
        # Pre-crisis: 30 trading days before start
        pre_mask = (df["date"] >= start - pd.Timedelta(days=45)) & (df["date"] < start)
        pre = df[pre_mask]
        
        # During crisis
        during_mask = (df["date"] >= start) & (df["date"] <= end)
        during = df[during_mask]
        
        # Post-crisis: 20 trading days after
        post_mask = (df["date"] > end) & (df["date"] <= end + pd.Timedelta(days=30))
        post = df[post_mask]
        
        analysis = {
            "crisis": crisis.name,
            "period": f"{crisis.start_date} to {crisis.end_date}",
            "description": crisis.description,
        }
        
        if not pre.empty:
            analysis["pre_heat_avg"] = round(float(pre["heat_score"].mean()), 4)
            analysis["pre_heat_max"] = round(float(pre["heat_score"].max()), 4)
            analysis["pre_avg_corr"] = round(float(pre["avg_corr"].mean()), 4)
            analysis["pre_crowding"] = round(float(pre["crowding_score"].mean()), 4)
        else:
            analysis["pre_heat_avg"] = None
            
        if not during.empty:
            analysis["during_heat_avg"] = round(float(during["heat_score"].mean()), 4)
            analysis["during_heat_max"] = round(float(during["heat_score"].max()), 4)
            analysis["during_heat_min"] = round(float(during["heat_score"].min()), 4)
            analysis["during_avg_corr"] = round(float(during["avg_corr"].mean()), 4)
            analysis["during_max_corr"] = round(float(during["avg_corr"].max()), 4)
            analysis["during_crowding_avg"] = round(float(during["crowding_score"].mean()), 4)
            analysis["during_crowding_max"] = round(float(during["crowding_score"].max()), 4)
            analysis["during_crowding_alpha"] = round(float(during["crowding_alpha"].mean()), 4)
            analysis["n_days_hot"] = int((during["heat_level"].isin(["hot", "critical", "emergency"])).sum())
            analysis["n_days_critical"] = int((during["heat_level"].isin(["critical", "emergency"])).sum())
            analysis["n_trading_days"] = len(during)
            
            # Heat score levels distribution during crisis
            level_counts = during["heat_level"].value_counts().to_dict()
            analysis["heat_distribution"] = level_counts
            
            # Early warning: first day heat crosses warm BEFORE the peak
            pre_peak = during[during["date"] < peak]
            warm_days = pre_peak[pre_peak["heat_score"] >= 0.4]
            if not warm_days.empty:
                first_warm = warm_days.iloc[0]["date"]
                days_before_peak = (peak - first_warm).days
                analysis["early_warning_days"] = days_before_peak
                analysis["first_warm_date"] = str(first_warm.date())
            else:
                analysis["early_warning_days"] = 0
                analysis["first_warm_date"] = None
                
            # First day heat crosses hot
            hot_days = pre_peak[pre_peak["heat_score"] >= 0.6]
            if not hot_days.empty:
                first_hot = hot_days.iloc[0]["date"]
                days_before_peak_hot = (peak - first_hot).days
                analysis["early_warning_hot_days"] = days_before_peak_hot
                analysis["first_hot_date"] = str(first_hot.date())
            else:
                analysis["early_warning_hot_days"] = 0
        else:
            analysis["during_heat_avg"] = None
            analysis["n_trading_days"] = 0
            
        if not post.empty:
            analysis["post_heat_avg"] = round(float(post["heat_score"].mean()), 4)
            analysis["post_crowding"] = round(float(post["crowding_score"].mean()), 4)
        
        # Verdict
        if analysis.get("during_heat_avg") is not None and analysis.get("pre_heat_avg") is not None:
            heat_increase = analysis["during_heat_avg"] - analysis["pre_heat_avg"]
            analysis["heat_increase"] = round(heat_increase, 4)
            analysis["detected"] = analysis["during_heat_max"] >= 0.6  # At least "hot"
            analysis["strong_signal"] = analysis["during_heat_max"] >= 0.7  # "critical"
        else:
            analysis["detected"] = False
            analysis["strong_signal"] = False
            
        crisis_analysis.append(analysis)
        
    return crisis_analysis


def compute_signal_stats(results: list[dict]) -> dict:
    """Compute overall signal statistics."""
    df = pd.DataFrame(results)
    
    stats = {
        "total_trading_days": len(df),
        "date_range": f"{df['date'].iloc[0]} to {df['date'].iloc[-1]}",
        "heat_score": {
            "mean": round(float(df["heat_score"].mean()), 4),
            "median": round(float(df["heat_score"].median()), 4),
            "std": round(float(df["heat_score"].std()), 4),
            "min": round(float(df["heat_score"].min()), 4),
            "max": round(float(df["heat_score"].max()), 4),
            "p25": round(float(df["heat_score"].quantile(0.25)), 4),
            "p75": round(float(df["heat_score"].quantile(0.75)), 4),
            "p90": round(float(df["heat_score"].quantile(0.90)), 4),
            "p95": round(float(df["heat_score"].quantile(0.95)), 4),
        },
        "heat_level_distribution": df["heat_level"].value_counts().to_dict(),
        "avg_correlation": {
            "mean": round(float(df["avg_corr"].mean()), 4),
            "std": round(float(df["avg_corr"].std()), 4),
            "max": round(float(df["avg_corr"].max()), 4),
        },
        "crowding": {
            "mean": round(float(df["crowding_score"].mean()), 4),
            "std": round(float(df["crowding_score"].std()), 4),
            "max": round(float(df["crowding_score"].max()), 4),
        },
        "component_means": {
            "absorption_ratio": round(float(df["absorption_ratio"].mean()), 4),
            "turbulence_pct": round(float(df["turbulence_pct"].mean()), 4),
            "div_ratio_pct": round(float(df["div_ratio_pct"].mean()), 4),
            "avg_corr_pct": round(float(df["avg_corr_pct"].mean()), 4),
        },
    }
    
    # Days in each level
    total = len(df)
    for level in ["cool", "warm", "hot", "critical", "emergency"]:
        count = int((df["heat_level"] == level).sum())
        stats[f"pct_days_{level}"] = round(count / total * 100, 1)
    
    return stats


def compute_forward_returns_analysis(results: list[dict], returns: pd.DataFrame) -> dict:
    """Key question: does high heat actually predict negative forward returns?"""
    df = pd.DataFrame(results)
    df["date"] = pd.to_datetime(df["date"])
    
    pos_cols = [s for s in POSITION_SYMBOLS if s in returns.columns]
    port_returns = returns[pos_cols].mean(axis=1)  # Equal-weighted portfolio
    
    # Compute forward 1-day, 5-day, 10-day, 20-day returns
    for horizon in [1, 5, 10, 20]:
        fwd = port_returns.rolling(horizon).sum().shift(-horizon)
        fwd_map = fwd.to_dict()
        df[f"fwd_{horizon}d"] = df["date"].map(lambda d: fwd_map.get(d, np.nan))
    
    analysis = {}
    
    # For each heat level, what are the average forward returns?
    for level in ["cool", "warm", "hot", "critical", "emergency"]:
        mask = df["heat_level"] == level
        subset = df[mask]
        if len(subset) < 5:
            continue
            
        level_stats = {"n_days": len(subset)}
        for horizon in [1, 5, 10, 20]:
            col = f"fwd_{horizon}d"
            valid = subset[col].dropna()
            if len(valid) > 0:
                level_stats[f"avg_fwd_{horizon}d_bps"] = round(float(valid.mean()) * 10000, 1)
                level_stats[f"median_fwd_{horizon}d_bps"] = round(float(valid.median()) * 10000, 1)
                level_stats[f"pct_negative_{horizon}d"] = round(float((valid < 0).mean()) * 100, 1)
        
        analysis[level] = level_stats
    
    # Crowding quintile analysis
    df["crowding_quintile"] = pd.qcut(df["crowding_score"], 5, labels=False, duplicates="drop")
    crowding_fwd = {}
    for q in sorted(df["crowding_quintile"].dropna().unique()):
        mask = df["crowding_quintile"] == q
        subset = df[mask]
        valid_5d = subset["fwd_5d"].dropna()
        valid_20d = subset["fwd_20d"].dropna()
        crowding_fwd[f"Q{int(q)+1}"] = {
            "n_days": len(subset),
            "avg_fwd_5d_bps": round(float(valid_5d.mean()) * 10000, 1) if len(valid_5d) > 0 else None,
            "avg_fwd_20d_bps": round(float(valid_20d.mean()) * 10000, 1) if len(valid_20d) > 0 else None,
        }
    analysis["crowding_quintiles"] = crowding_fwd
    
    return analysis


def main():
    print("=" * 70)
    print("RISK RADAR — REAL HISTORICAL BACKTEST")
    print("=" * 70)
    print()
    
    # 1. Fetch data
    engine = BacktestEngine(
        position_symbols=POSITION_SYMBOLS,
        factor_symbols=FACTOR_SYMBOLS,
    )
    
    print("Fetching historical data from yfinance (2019-01-01 to present)...")
    returns = engine.fetch_historical_data(start_date="2019-01-01")
    print()
    
    # 2. Run rolling backtest
    results = run_full_rolling_backtest(returns)
    
    # 3. Analyze crisis performance
    print("=" * 70)
    print("CRISIS ANALYSIS")
    print("=" * 70)
    print()
    
    crisis_analysis = analyze_crisis_performance(results, CRISIS_EVENTS)
    
    for ca in crisis_analysis:
        print(f"📌 {ca['crisis']} ({ca['period']})")
        print(f"   {ca['description']}")
        if ca.get("pre_heat_avg") is not None:
            print(f"   Pre-crisis heat:    avg={ca['pre_heat_avg']}, max={ca['pre_heat_max']}")
        if ca.get("during_heat_avg") is not None:
            print(f"   During crisis heat: avg={ca['during_heat_avg']}, max={ca['during_heat_max']}, min={ca['during_heat_min']}")
            print(f"   Heat increase:      {ca.get('heat_increase', 'N/A')}")
            print(f"   Correlation:        avg={ca['during_avg_corr']}, max={ca['during_max_corr']}")
            print(f"   Crowding:           avg={ca['during_crowding_avg']}, max={ca['during_crowding_max']}")
            print(f"   Alpha signal:       {ca['during_crowding_alpha']}")
            print(f"   Days hot+:          {ca['n_days_hot']}/{ca['n_trading_days']}")
            print(f"   Days critical+:     {ca['n_days_critical']}/{ca['n_trading_days']}")
            if ca.get("early_warning_days", 0) > 0:
                print(f"   ⚡ Early warning:   {ca['early_warning_days']} days before peak (first warm: {ca['first_warm_date']})")
            if ca.get("early_warning_hot_days", 0) > 0:
                print(f"   ⚡ Hot warning:     {ca['early_warning_hot_days']} days before peak (first hot: {ca.get('first_hot_date')})")
            print(f"   DETECTED: {'✅ YES' if ca['detected'] else '❌ NO'}")
            print(f"   STRONG:   {'✅ YES' if ca['strong_signal'] else '❌ NO'}")
            if ca.get("heat_distribution"):
                print(f"   Heat dist:          {ca['heat_distribution']}")
        print()
    
    # 4. Overall signal statistics
    print("=" * 70)
    print("OVERALL SIGNAL STATISTICS")
    print("=" * 70)
    print()
    
    stats = compute_signal_stats(results)
    print(f"Total trading days: {stats['total_trading_days']}")
    print(f"Date range: {stats['date_range']}")
    print()
    print(f"Heat Score Distribution:")
    print(f"  Mean={stats['heat_score']['mean']}, Median={stats['heat_score']['median']}, Std={stats['heat_score']['std']}")
    print(f"  Min={stats['heat_score']['min']}, Max={stats['heat_score']['max']}")
    print(f"  P25={stats['heat_score']['p25']}, P75={stats['heat_score']['p75']}, P90={stats['heat_score']['p90']}, P95={stats['heat_score']['p95']}")
    print()
    print(f"Days by heat level:")
    for level in ["cool", "warm", "hot", "critical", "emergency"]:
        pct = stats.get(f"pct_days_{level}", 0)
        count = stats["heat_level_distribution"].get(level, 0)
        print(f"  {level:12s}: {count:5d} days ({pct:.1f}%)")
    print()
    print(f"Component means (percentiles):")
    for comp, val in stats["component_means"].items():
        print(f"  {comp:20s}: {val:.4f}")
    print()
    print(f"Avg correlation: mean={stats['avg_correlation']['mean']}, max={stats['avg_correlation']['max']}")
    print(f"Crowding: mean={stats['crowding']['mean']}, max={stats['crowding']['max']}")
    
    # 5. Forward returns analysis (the money question)
    print()
    print("=" * 70)
    print("FORWARD RETURNS BY HEAT LEVEL (THE MONEY QUESTION)")
    print("=" * 70)
    print()
    
    fwd_analysis = compute_forward_returns_analysis(results, returns)
    
    print(f"{'Level':12s} {'N':>6s} {'Fwd 1d':>10s} {'Fwd 5d':>10s} {'Fwd 10d':>10s} {'Fwd 20d':>10s} {'%Neg 5d':>8s} {'%Neg 20d':>8s}")
    print("-" * 78)
    for level in ["cool", "warm", "hot", "critical", "emergency"]:
        if level not in fwd_analysis:
            continue
        la = fwd_analysis[level]
        print(f"{level:12s} {la['n_days']:6d} "
              f"{la.get('avg_fwd_1d_bps', 'N/A'):>10} "
              f"{la.get('avg_fwd_5d_bps', 'N/A'):>10} "
              f"{la.get('avg_fwd_10d_bps', 'N/A'):>10} "
              f"{la.get('avg_fwd_20d_bps', 'N/A'):>10} "
              f"{la.get('pct_negative_5d', 'N/A'):>8} "
              f"{la.get('pct_negative_20d', 'N/A'):>8}")
    
    print()
    print("Crowding quintile → forward returns:")
    cq = fwd_analysis.get("crowding_quintiles", {})
    for q_name, q_data in sorted(cq.items()):
        print(f"  {q_name}: n={q_data['n_days']}, fwd_5d={q_data.get('avg_fwd_5d_bps')}bps, fwd_20d={q_data.get('avg_fwd_20d_bps')}bps")
    
    # 6. Save full results
    output = {
        "crisis_analysis": crisis_analysis,
        "signal_stats": stats,
        "forward_returns": fwd_analysis,
    }
    
    output_path = Path(__file__).parent.parent / "data" / "backtest_results.json"
    output_path.parent.mkdir(exist_ok=True)
    with open(output_path, "w") as f:
        json.dump(output, f, indent=2, default=str)
    print(f"\nFull results saved to: {output_path}")
    
    # Save rolling time series for calibration
    ts_path = Path(__file__).parent.parent / "data" / "heat_score_history.json"
    with open(ts_path, "w") as f:
        json.dump(results, f, indent=2)
    print(f"Heat score time series saved to: {ts_path}")
    
    # Verdict
    print()
    print("=" * 70)
    print("VERDICT")
    print("=" * 70)
    n_detected = sum(1 for ca in crisis_analysis if ca.get("detected"))
    n_strong = sum(1 for ca in crisis_analysis if ca.get("strong_signal"))
    n_total = len(crisis_analysis)
    print(f"Crises detected (heat >= hot):      {n_detected}/{n_total}")
    print(f"Strong signals (heat >= critical):   {n_strong}/{n_total}")
    
    # Check if heat level predicts forward returns
    cool_fwd = fwd_analysis.get("cool", {}).get("avg_fwd_5d_bps")
    hot_fwd = fwd_analysis.get("hot", {}).get("avg_fwd_5d_bps")
    if cool_fwd is not None and hot_fwd is not None:
        if cool_fwd > hot_fwd:
            print(f"Forward return signal: ✅ Cool days outperform hot days (5d: {cool_fwd} vs {hot_fwd} bps)")
        else:
            print(f"Forward return signal: ❌ Cool days DON'T outperform hot days (5d: {cool_fwd} vs {hot_fwd} bps)")
    
    print()


if __name__ == "__main__":
    main()
