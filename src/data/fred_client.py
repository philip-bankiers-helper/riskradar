"""FRED API client for macro factor data."""

from __future__ import annotations

import logging
from datetime import datetime, timedelta

import pandas as pd

logger = logging.getLogger(__name__)


class FREDClient:
    """Fetch macro economic data from FRED."""

    DEFAULT_SERIES = {
        "DGS10": "10Y Treasury Yield",
        "DGS2": "2Y Treasury Yield",
        "BAMLH0A0HYM2": "HY Credit Spread (OAS)",
        "SOFR": "SOFR Overnight Rate",
        "DTWEXBGS": "Broad Dollar Index",
        "VIXCLS": "VIX",
        "T10YIE": "10Y Breakeven Inflation",
    }

    def __init__(self, api_key: str = ""):
        self.api_key = api_key
        self._fred = None

    def _get_fred(self):
        """Lazy init Fred client."""
        if self._fred is None:
            if not self.api_key:
                raise ValueError(
                    "FRED API key required. Get one free at https://fred.stlouisfed.org/docs/api/api_key.html"
                )
            from fredapi import Fred
            self._fred = Fred(api_key=self.api_key)
        return self._fred

    def get_series(
        self,
        series_id: str,
        start_date: datetime | None = None,
        end_date: datetime | None = None,
    ) -> pd.Series:
        """Fetch a single FRED series."""
        fred = self._get_fred()
        end = end_date or datetime.now()
        start = start_date or (end - timedelta(days=756))  # ~3 years

        try:
            data = fred.get_series(
                series_id,
                observation_start=start.strftime("%Y-%m-%d"),
                observation_end=end.strftime("%Y-%m-%d"),
            )
            data = data.dropna()
            data.name = series_id
            logger.info("Fetched FRED %s: %d observations", series_id, len(data))
            return data
        except Exception as e:
            logger.error("Failed to fetch FRED %s: %s", series_id, e)
            raise

    def get_all_macro(
        self,
        series_ids: dict[str, str] | None = None,
        start_date: datetime | None = None,
    ) -> pd.DataFrame:
        """Fetch all macro series into a single DataFrame."""
        series_ids = series_ids or self.DEFAULT_SERIES
        frames = {}

        for sid, name in series_ids.items():
            try:
                frames[sid] = self.get_series(sid, start_date=start_date)
            except Exception as e:
                logger.warning("Skipping %s (%s): %s", sid, name, e)

        if not frames:
            return pd.DataFrame()

        df = pd.DataFrame(frames)
        df.index = pd.to_datetime(df.index)

        # Add derived series
        if "DGS10" in df.columns and "DGS2" in df.columns:
            df["YIELD_CURVE_2S10S"] = df["DGS10"] - df["DGS2"]

        return df.ffill().dropna(how="all")
