"""Heat Score Calculator — composite risk metric.

Combines 5 components into a single [0, 1] score:
1. Absorption Ratio (25%) — PCA variance concentration
2. Turbulence (20%) — Mahalanobis distance
3. Diversification Ratio inverse (20%) — portfolio concentration
4. Factor HHI (15%) — factor exposure concentration
5. Average Correlation (20%) — pairwise correlation level

Each component is percentile-ranked against its rolling 252-day history.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime
from pathlib import Path

import numpy as np
import pandas as pd

from src.engine.metrics import (
    absorption_ratio,
    diversification_ratio,
    factor_hhi,
    percentile_rank,
    turbulence_index,
)
from src.models import HeatLevel, HeatScore, HeatScoreComponents

logger = logging.getLogger(__name__)


class HeatScoreCalculator:
    """Compute composite portfolio heat score."""

    def __init__(
        self,
        weight_ar: float = 0.25,
        weight_turb: float = 0.20,
        weight_dr: float = 0.20,
        weight_hhi: float = 0.15,
        weight_corr: float = 0.20,
        threshold_warm: float = 0.4636,
        threshold_hot: float = 0.6732,
        threshold_critical: float = 0.8029,
        threshold_emergency: float = 0.8781,
    ):
        self.weights = {
            "ar": weight_ar,
            "turb": weight_turb,
            "dr": weight_dr,
            "hhi": weight_hhi,
            "corr": weight_corr,
        }
        self.thresholds = {
            "warm": threshold_warm,
            "hot": threshold_hot,
            "critical": threshold_critical,
            "emergency": threshold_emergency,
        }

        # Rolling histories for percentile ranking
        self._ar_history: list[float] = []
        self._turb_history: list[float] = []
        self._dr_history: list[float] = []
        self._hhi_history: list[float] = []
        self._corr_history: list[float] = []
        self._max_history = 504  # ~2 years of trading days

        # Histories advance only when the underlying data date changes
        # (one observation per trading day, matching the 252/504 trading-day
        # design). Appending on every compute cycle re-inserts the same value
        # ~288x/day at the 5-min cadence; because percentile_rank counts
        # history <= value, the accumulating duplicates self-inflate the
        # percentiles toward 1.0 even with markets closed (root cause of the
        # 2026-10-03..05 phantom EMERGENCY storm). See PROGRESS.md 2026-10-06.
        self._last_append_key: str | None = None

    def compute(
        self,
        returns: pd.DataFrame,
        portfolio_weights: np.ndarray,
        factor_exposures: dict[str, float],
        avg_correlation: float,
        dominant_factor: str = "",
        top_correlated_pair: str = "",
    ) -> HeatScore:
        """
        Calculate composite heat score.

        Args:
            returns: Historical returns DataFrame (rows=dates, cols=positions).
            portfolio_weights: Array of position weights.
            factor_exposures: Dict of factor_name -> total portfolio exposure.
            avg_correlation: Average pairwise correlation.
            dominant_factor: Name of dominant factor.
            top_correlated_pair: "SYMB_A↔SYMB_B" string.

        Returns:
            HeatScore with all components and classification.
        """
        # Append-once-per-data-date: repeated cycles on an unchanged frame
        # (closed markets, or intraday refreshes of the same daily bar) rank
        # against the frozen history instead of polluting it with duplicates.
        append_key = str(returns.index[-1]) if len(returns.index) else ""
        should_append = append_key != self._last_append_key

        # 1. Absorption Ratio
        ar = absorption_ratio(returns)
        if should_append:
            self._ar_history.append(ar)
        ar_pct = percentile_rank(ar, self._ar_history[-self._max_history:])

        # 2. Turbulence Index
        if len(returns) > 1:
            turb = turbulence_index(returns.iloc[-1].values, returns.iloc[:-1])
        else:
            turb = 0.0
        if should_append:
            self._turb_history.append(turb)
        turb_pct = percentile_rank(turb, self._turb_history[-self._max_history:])

        # 3. Diversification Ratio (inverse — lower DR = more heat)
        dr = diversification_ratio(portfolio_weights, returns)
        if should_append:
            self._dr_history.append(1.0 / max(dr, 0.01))
        dr_pct = percentile_rank(
            1.0 / max(dr, 0.01), self._dr_history[-self._max_history:]
        )

        # 4. Factor HHI
        hhi = factor_hhi(factor_exposures)
        if should_append:
            self._hhi_history.append(hhi)
        hhi_pct = percentile_rank(hhi, self._hhi_history[-self._max_history:])

        # 5. Average Correlation
        if should_append:
            self._corr_history.append(avg_correlation)
        corr_pct = percentile_rank(avg_correlation, self._corr_history[-self._max_history:])

        # All five histories advanced together; only now lock the date key so
        # a mid-compute exception retries the append on the next cycle.
        if should_append:
            self._last_append_key = append_key

        # Composite score
        score = (
            self.weights["ar"] * ar_pct
            + self.weights["turb"] * turb_pct
            + self.weights["dr"] * dr_pct
            + self.weights["hhi"] * hhi_pct
            + self.weights["corr"] * corr_pct
        )
        score = float(np.clip(score, 0, 1))

        # Classify
        level = self._classify(score)

        # Action recommendation
        action = self._recommend_action(level)

        components = HeatScoreComponents(
            absorption_ratio=ar,
            turbulence=turb,
            turbulence_percentile=turb_pct,
            diversification_ratio=dr,
            diversification_percentile=dr_pct,
            factor_hhi=hhi,
            factor_hhi_percentile=hhi_pct,
            avg_correlation=avg_correlation,
            avg_correlation_percentile=corr_pct,
        )

        return HeatScore(
            score=score,
            level=level,
            components=components,
            timestamp=datetime.utcnow(),
            dominant_factor=dominant_factor,
            top_correlated_pair=top_correlated_pair,
            action=action,
        )

    def _classify(self, score: float) -> HeatLevel:
        """Classify heat score into a level."""
        if score >= self.thresholds["emergency"]:
            return HeatLevel.EMERGENCY
        elif score >= self.thresholds["critical"]:
            return HeatLevel.CRITICAL
        elif score >= self.thresholds["hot"]:
            return HeatLevel.HOT
        elif score >= self.thresholds["warm"]:
            return HeatLevel.WARM
        else:
            return HeatLevel.COOL

    def seed_history(self, historical_data: dict[str, list[float]]) -> None:
        """Pre-seed percentile histories from backtest data.

        Args:
            historical_data: Dict with keys 'ar', 'turb', 'dr', 'hhi', 'corr'
                             mapping to lists of historical values.
        """
        key_map = {
            "ar": "_ar_history",
            "turb": "_turb_history",
            "dr": "_dr_history",
            "hhi": "_hhi_history",
            "corr": "_corr_history",
        }
        for key, attr in key_map.items():
            values = historical_data.get(key, [])
            if values:
                # Keep only max_history most recent
                setattr(self, attr, list(values[-self._max_history:]))
                logger.info(
                    "Seeded %s with %d historical values", key, len(getattr(self, attr))
                )

    @classmethod
    def from_calibration_file(
        cls, path: str = "data/heat_score_history.json", **kwargs
    ) -> "HeatScoreCalculator":
        """Create a pre-calibrated calculator from historical data.

        Supports two formats:
        1. Dict with keys 'ar', 'turb', 'dr', 'hhi', 'corr' (direct seed format)
        2. List of daily records from backtest (auto-transformed)

        Args:
            path: Path to JSON file with historical component values.
            **kwargs: Additional arguments passed to constructor.

        Returns:
            HeatScoreCalculator with pre-seeded histories.
        """
        calc = cls(**kwargs)
        filepath = Path(path)
        if filepath.exists():
            try:
                with open(filepath) as f:
                    data = json.load(f)

                # Transform list-of-records format to seed format
                if isinstance(data, list) and data:
                    seed_data = {
                        "ar": [r["absorption_ratio"] for r in data if "absorption_ratio" in r],
                        "turb": [r["turbulence"] for r in data if "turbulence" in r],
                        "dr": [1.0 / max(r.get("div_ratio", 1.0), 0.01) for r in data if "div_ratio" in r],
                        "hhi": [r["factor_hhi"] for r in data if "factor_hhi" in r],
                        "corr": [r["avg_corr"] for r in data if "avg_corr" in r],
                    }
                    calc.seed_history(seed_data)
                elif isinstance(data, dict):
                    calc.seed_history(data)
                else:
                    logger.warning("Unrecognized calibration file format in %s", path)

                logger.info("Loaded calibration data from %s", path)
            except Exception as e:
                logger.warning("Failed to load calibration file %s: %s", path, e)
        else:
            logger.info("No calibration file found at %s, starting fresh", path)
        return calc

    @staticmethod
    def _recommend_action(level: HeatLevel) -> str:
        """Return action recommendation for a heat level."""
        actions = {
            HeatLevel.COOL: "Normal trading, full position sizes",
            HeatLevel.WARM: "Alert; reduce new position sizes by 25%",
            HeatLevel.HOT: "Reduce overall exposure by 25%; no new correlated positions",
            HeatLevel.CRITICAL: "Reduce exposure by 50%; hedge factor concentrations",
            HeatLevel.EMERGENCY: "Halt new entries; actively reduce largest factor exposures",
        }
        return actions.get(level, "")
