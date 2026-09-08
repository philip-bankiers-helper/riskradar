#!/usr/bin/env python3
"""Render the empirical README tables from committed backtest artifacts."""

import json
from pathlib import Path


RESULTS = Path(__file__).parent.parent / "data" / "backtest_results.json"


def main() -> None:
    data = json.loads(RESULTS.read_text())
    stats = data["signal_stats"]
    thresholds = data["calibration"]["thresholds"]
    print("Calibration")
    ranges = {
        "cool": f"< {thresholds['warm']:.4f}",
        "warm": f"{thresholds['warm']:.4f}-{thresholds['hot']:.4f}",
        "hot": f"{thresholds['hot']:.4f}-{thresholds['critical']:.4f}",
        "critical": f"{thresholds['critical']:.4f}-{thresholds['emergency']:.4f}",
        "emergency": f">= {thresholds['emergency']:.4f}",
    }
    for level, score_range in ranges.items():
        pct = stats[f"pct_days_{level}"]
        print(f"{level}: range={score_range} days={pct:.1f}%")

    print("\nCrisis validation")
    for crisis in data["crisis_analysis"]:
        print(
            f"{crisis['crisis']}: warm_lead={crisis.get('early_warning_days', 0)} "
            f"peak={crisis.get('during_heat_max', 0):.4f} detected={crisis['detected']}"
        )

    print("\nBenchmark")
    for result in data["benchmark"]["results"]:
        if result["signal_name"] not in {"RiskRadar Heat Score", "VIX > 25", "50-Day MA Cross"}:
            continue
        print(
            f"{result['signal_name']}: F1={result['f1_score']:.4f} "
            f"precision={result['precision']:.2%} recall={result['recall']:.2%} "
            f"lead={result['lead_time_days']:.1f} FPR={result['false_positive_rate']:.2%} "
            f"Sharpe={result['sharpe_ratio']:.4f}"
        )


if __name__ == "__main__":
    main()
