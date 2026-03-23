"""Polygon.io data client for real-time and historical market data.

Supports free tier (5 API calls/min, 15-min delayed) and paid tiers.
Falls back gracefully to yfinance if Polygon is not configured.
"""

from __future__ import annotations

import asyncio
import logging
import time
from datetime import datetime, timedelta

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)


class PolygonClient:
    """Real-time and historical data via Polygon.io REST + WebSocket.

    Supports free tier (5 API calls/min, 15-min delayed) and paid tiers.
    Falls back gracefully to yfinance if Polygon is not configured.
    """

    # Rate limits by tier (calls per minute)
    TIER_LIMITS = {
        "free": 5,
        "starter": 100,
        "developer": 1000,
        "advanced": 10000,
    }

    def __init__(self, api_key: str = "", tier: str = "free"):
        self.api_key = api_key
        self.tier = tier
        self._rate_limit = self.TIER_LIMITS.get(tier, 5)
        self._call_timestamps: list[float] = []
        self._ws_connection = None
        self._ws_task: asyncio.Task | None = None
        self._streaming = False

        if api_key:
            try:
                from polygon import RESTClient
                self._rest_client = RESTClient(api_key=api_key)
                logger.info("Polygon client initialized (tier=%s, rate_limit=%d/min)", tier, self._rate_limit)
            except ImportError:
                logger.warning("polygon-api-client not installed, Polygon features disabled")
                self._rest_client = None
                self.api_key = ""
        else:
            self._rest_client = None

    @property
    def is_configured(self) -> bool:
        """Check if Polygon is configured and ready."""
        return bool(self.api_key and self._rest_client)

    def _rate_limit_wait(self) -> None:
        """Enforce rate limiting by waiting if necessary."""
        now = time.monotonic()
        # Remove timestamps older than 60 seconds
        self._call_timestamps = [t for t in self._call_timestamps if now - t < 60]

        if len(self._call_timestamps) >= self._rate_limit:
            wait_time = 60 - (now - self._call_timestamps[0])
            if wait_time > 0:
                logger.debug("Rate limit: waiting %.1fs", wait_time)
                time.sleep(wait_time)

        self._call_timestamps.append(time.monotonic())

    async def get_snapshot(self, symbols: list[str]) -> dict[str, dict]:
        """Get latest price snapshot for symbols (REST).

        Returns dict of symbol -> {price, change, change_pct, volume, timestamp}.
        """
        if not self.is_configured:
            return {}

        results = {}
        try:
            self._rate_limit_wait()
            snapshots = self._rest_client.get_snapshot_all("stocks")

            symbol_set = {s.upper() for s in symbols}
            for snap in snapshots:
                if snap.ticker in symbol_set:
                    day = snap.day if snap.day else None
                    prev = snap.prev_day if snap.prev_day else None
                    results[snap.ticker] = {
                        "price": day.close if day else 0,
                        "open": day.open if day else 0,
                        "high": day.high if day else 0,
                        "low": day.low if day else 0,
                        "volume": day.volume if day else 0,
                        "prev_close": prev.close if prev else 0,
                        "change": (day.close - prev.close) if (day and prev) else 0,
                        "change_pct": ((day.close - prev.close) / prev.close * 100)
                        if (day and prev and prev.close) else 0,
                        "timestamp": snap.updated / 1e9 if snap.updated else 0,
                    }

            logger.info("Polygon snapshot: got %d/%d symbols", len(results), len(symbols))

        except Exception as e:
            logger.warning("Polygon snapshot failed: %s", e)

        return results

    async def subscribe_trades(self, symbols: list[str], callback) -> None:
        """Subscribe to real-time trades via WebSocket.

        callback(symbol, price, volume, timestamp) called for each trade.
        Requires paid tier for real-time; free tier gets 15-min delayed.
        """
        if not self.is_configured:
            logger.warning("Polygon not configured — cannot start WebSocket")
            return

        try:
            from polygon import WebSocketClient
            from polygon.websocket.models import WebSocketMessage

            ws_client = WebSocketClient(
                api_key=self.api_key,
                market="stocks",
            )

            self._streaming = True
            self._ws_connection = ws_client

            # Subscribe to trades for each symbol
            channels = [f"T.{s.upper()}" for s in symbols]

            async def _handle_messages(msgs: list[WebSocketMessage]) -> None:
                for msg in msgs:
                    if hasattr(msg, "symbol") and hasattr(msg, "price"):
                        await callback(msg.symbol, msg.price, msg.size or 0, msg.timestamp or 0)

            ws_client.subscribe(*channels)

            logger.info("Polygon WebSocket: subscribed to %d symbols", len(symbols))

            # Run in background
            self._ws_task = asyncio.create_task(
                asyncio.to_thread(ws_client.run, handle_msg=_handle_messages)
            )

        except ImportError:
            logger.warning("polygon-api-client websocket not available")
        except Exception as e:
            logger.warning("Polygon WebSocket subscription failed: %s", e)

    def get_aggregates(
        self,
        symbol: str,
        timespan: str = "day",
        limit: int = 252,
        start_date: str | None = None,
        end_date: str | None = None,
    ) -> pd.DataFrame:
        """Get OHLCV bars (REST). Works on free tier.

        Returns DataFrame with columns: open, high, low, close, volume, vwap.
        """
        if not self.is_configured:
            return pd.DataFrame()

        if end_date is None:
            end_date = datetime.now().strftime("%Y-%m-%d")
        if start_date is None:
            start_date = (datetime.now() - timedelta(days=int(limit * 1.5))).strftime("%Y-%m-%d")

        try:
            self._rate_limit_wait()
            aggs = self._rest_client.get_aggs(
                ticker=symbol.upper(),
                multiplier=1,
                timespan=timespan,
                from_=start_date,
                to=end_date,
                limit=limit,
            )

            if not aggs:
                return pd.DataFrame()

            records = []
            for bar in aggs:
                records.append({
                    "timestamp": pd.Timestamp(bar.timestamp, unit="ms"),
                    "open": bar.open,
                    "high": bar.high,
                    "low": bar.low,
                    "close": bar.close,
                    "volume": bar.volume,
                    "vwap": bar.vwap if hasattr(bar, "vwap") else 0,
                })

            df = pd.DataFrame(records)
            df.set_index("timestamp", inplace=True)
            df.index = df.index.tz_localize(None)  # Remove timezone for consistency

            logger.debug("Polygon aggregates for %s: %d bars", symbol, len(df))
            return df

        except Exception as e:
            logger.warning("Polygon aggregates failed for %s: %s", symbol, e)
            return pd.DataFrame()

    def get_returns(
        self,
        symbols: list[str],
        period_days: int = 252,
    ) -> pd.DataFrame:
        """Get daily returns — drop-in replacement for MarketDataClient.get_returns().

        Fetches daily bars from Polygon and computes log returns.
        """
        if not self.is_configured:
            return pd.DataFrame()

        all_closes = {}

        for symbol in symbols:
            df = self.get_aggregates(symbol, timespan="day", limit=period_days)
            if not df.empty and "close" in df.columns:
                all_closes[symbol] = df["close"]

        if not all_closes:
            return pd.DataFrame()

        prices = pd.DataFrame(all_closes)
        prices = prices.dropna(how="all")

        # Compute log returns
        returns = np.log(prices / prices.shift(1)).dropna()

        # Trim to requested period
        if len(returns) > period_days:
            returns = returns.iloc[-period_days:]

        # Drop symbols with all NaN
        returns = returns.dropna(axis=1, how="all")

        missing = set(symbols) - set(returns.columns)
        if missing:
            logger.warning("Polygon: missing data for symbols: %s", missing)

        logger.info("Polygon returns: %d days for %d/%d symbols", len(returns), len(returns.columns), len(symbols))
        return returns

    async def stop_streaming(self) -> None:
        """Stop WebSocket streaming."""
        self._streaming = False
        if self._ws_connection is not None:
            try:
                self._ws_connection.close()
            except Exception:
                pass
            self._ws_connection = None

        if self._ws_task is not None:
            self._ws_task.cancel()
            try:
                await self._ws_task
            except (asyncio.CancelledError, Exception):
                pass
            self._ws_task = None

        logger.info("Polygon WebSocket stopped")
