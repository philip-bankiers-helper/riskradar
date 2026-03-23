"""Market data fetching via yfinance, Polygon, and unified DataPipelineManager."""

from __future__ import annotations

import asyncio
import logging
import time
from datetime import datetime, timedelta

import numpy as np
import pandas as pd
import yfinance as yf

from src.data.polygon_client import PolygonClient

logger = logging.getLogger(__name__)


class MarketDataClient:
    """Fetch historical and current market data."""

    def __init__(self, cache_dir: str | None = None):
        self._cache: dict[str, pd.DataFrame] = {}

    def get_returns(
        self,
        symbols: list[str],
        period_days: int = 504,  # ~2 years
        end_date: datetime | None = None,
    ) -> pd.DataFrame:
        """
        Fetch daily returns for a list of symbols.

        Returns:
            DataFrame with columns=symbols, index=dates, values=daily returns.
        """
        end = end_date or datetime.now()
        start = end - timedelta(days=int(period_days * 1.5))  # Buffer for trading days

        cache_key = f"{'_'.join(sorted(symbols))}_{start.date()}_{end.date()}"
        if cache_key in self._cache:
            return self._cache[cache_key]

        logger.info("Fetching price data for %d symbols via yfinance", len(symbols))

        try:
            data = yf.download(
                symbols,
                start=start.strftime("%Y-%m-%d"),
                end=end.strftime("%Y-%m-%d"),
                progress=False,
                auto_adjust=True,
            )

            if isinstance(data.columns, pd.MultiIndex):
                prices = data["Close"]
            else:
                prices = data[["Close"]]
                prices.columns = symbols

            # Calculate log returns
            returns = np.log(prices / prices.shift(1)).dropna()

            # Trim to requested period
            if len(returns) > period_days:
                returns = returns.iloc[-period_days:]

            # Drop any symbols with all NaN
            returns = returns.dropna(axis=1, how="all")

            missing = set(symbols) - set(returns.columns)
            if missing:
                logger.warning("Missing data for symbols: %s", missing)

            self._cache[cache_key] = returns
            logger.info(
                "Got %d days of returns for %d/%d symbols",
                len(returns), len(returns.columns), len(symbols),
            )
            return returns

        except Exception as e:
            logger.error("Failed to fetch market data: %s", e)
            raise

    def get_prices(
        self,
        symbols: list[str],
        period_days: int = 504,
        end_date: datetime | None = None,
    ) -> pd.DataFrame:
        """Fetch daily closing prices."""
        end = end_date or datetime.now()
        start = end - timedelta(days=int(period_days * 1.5))

        data = yf.download(
            symbols,
            start=start.strftime("%Y-%m-%d"),
            end=end.strftime("%Y-%m-%d"),
            progress=False,
            auto_adjust=True,
        )

        if isinstance(data.columns, pd.MultiIndex):
            prices = data["Close"]
        else:
            prices = data[["Close"]]
            prices.columns = symbols

        return prices.dropna(how="all")

    def clear_cache(self) -> None:
        """Clear the in-memory cache."""
        self._cache.clear()


class DataPipelineManager:
    """Unified data pipeline that tries sources in priority order.

    Priority: Polygon REST -> yfinance -> cached DuckDB
    For real-time: Polygon WebSocket -> Polygon REST snapshot -> yfinance
    """

    def __init__(
        self,
        polygon_api_key: str = "",
        polygon_tier: str = "free",
        fred_api_key: str = "",
        duckdb_path: str = "",
        cache_ttl_seconds: int = 300,
    ):
        self._polygon = PolygonClient(api_key=polygon_api_key, tier=polygon_tier)
        self._yfinance = MarketDataClient()
        self._duckdb_path = duckdb_path
        self._cache_ttl = cache_ttl_seconds

        # Track data source usage and freshness
        self._last_source: str = "none"
        self._last_fetch_time: float = 0
        self._source_stats: dict[str, int] = {"polygon": 0, "yfinance": 0, "cache": 0}
        self._last_returns_cache: pd.DataFrame | None = None
        self._last_returns_key: str = ""
        self._streaming = False

    @property
    def active_source(self) -> str:
        """Return which data source was last used."""
        return self._last_source

    @property
    def polygon_available(self) -> bool:
        return self._polygon.is_configured

    async def get_returns(
        self,
        symbols: list[str],
        period_days: int = 252,
    ) -> pd.DataFrame:
        """Get returns from best available source.

        Priority: Polygon -> yfinance -> cache
        """
        cache_key = f"{'_'.join(sorted(symbols))}_{period_days}"

        # Check in-memory cache
        if (
            self._last_returns_key == cache_key
            and self._last_returns_cache is not None
            and (time.time() - self._last_fetch_time) < self._cache_ttl
        ):
            self._source_stats["cache"] += 1
            self._last_source = "cache"
            return self._last_returns_cache

        # Try Polygon first
        if self._polygon.is_configured:
            try:
                returns = self._polygon.get_returns(symbols, period_days)
                if not returns.empty and len(returns.columns) >= len(symbols) * 0.5:
                    self._last_source = "polygon"
                    self._source_stats["polygon"] += 1
                    self._last_fetch_time = time.time()
                    self._last_returns_cache = returns
                    self._last_returns_key = cache_key
                    logger.info("Data source: Polygon (%d symbols, %d days)", len(returns.columns), len(returns))
                    return returns
                else:
                    logger.warning("Polygon returned incomplete data, falling back to yfinance")
            except Exception as e:
                logger.warning("Polygon failed, falling back to yfinance: %s", e)

        # Fall back to yfinance
        try:
            returns = self._yfinance.get_returns(symbols, period_days)
            self._last_source = "yfinance"
            self._source_stats["yfinance"] += 1
            self._last_fetch_time = time.time()
            self._last_returns_cache = returns
            self._last_returns_key = cache_key
            return returns
        except Exception as e:
            logger.error("yfinance also failed: %s", e)

            # Return cached data if available (stale but better than nothing)
            if self._last_returns_cache is not None:
                self._last_source = "cache (stale)"
                self._source_stats["cache"] += 1
                logger.warning("Using stale cached data")
                return self._last_returns_cache

            raise

    async def get_live_prices(self, symbols: list[str]) -> dict[str, float]:
        """Get most recent prices from best available source."""
        # Try Polygon snapshot
        if self._polygon.is_configured:
            try:
                snapshots = await self._polygon.get_snapshot(symbols)
                if snapshots:
                    prices = {sym: data["price"] for sym, data in snapshots.items() if data.get("price")}
                    if len(prices) >= len(symbols) * 0.5:
                        return prices
            except Exception as e:
                logger.warning("Polygon live prices failed: %s", e)

        # Fall back to yfinance
        try:
            price_df = self._yfinance.get_prices(symbols, period_days=5)
            if not price_df.empty:
                latest = price_df.iloc[-1]
                return {sym: float(latest[sym]) for sym in price_df.columns if not pd.isna(latest[sym])}
        except Exception as e:
            logger.warning("yfinance live prices failed: %s", e)

        return {}

    async def start_streaming(self, symbols: list[str], on_update) -> None:
        """Start WebSocket streaming if Polygon is available."""
        if self._polygon.is_configured:
            await self._polygon.subscribe_trades(symbols, on_update)
            self._streaming = True
        else:
            logger.info("Polygon not configured — WebSocket streaming unavailable")

    async def stop_streaming(self) -> None:
        """Stop all streams."""
        if self._streaming:
            await self._polygon.stop_streaming()
            self._streaming = False

    def clear_cache(self) -> None:
        """Clear all caches."""
        self._yfinance.clear_cache()
        self._last_returns_cache = None
        self._last_returns_key = ""

    def get_data_quality_report(self) -> dict:
        """Report on data freshness, gaps, source usage."""
        age = time.time() - self._last_fetch_time if self._last_fetch_time > 0 else -1
        return {
            "primary_source": self._last_source,
            "polygon_available": self._polygon.is_configured,
            "polygon_tier": self._polygon.tier if self._polygon.is_configured else "none",
            "data_age_seconds": round(age, 1) if age >= 0 else None,
            "source_usage": dict(self._source_stats),
            "cache_ttl_seconds": self._cache_ttl,
            "streaming_active": self._streaming,
            "last_fetch_time": datetime.fromtimestamp(self._last_fetch_time).isoformat() if self._last_fetch_time > 0 else None,
        }
