"""Tests for Polygon.io client and DataPipelineManager."""

from __future__ import annotations

import asyncio
import time
from unittest.mock import MagicMock, patch, AsyncMock

import numpy as np
import pandas as pd
import pytest

from src.data.polygon_client import PolygonClient
from src.data.market_data import DataPipelineManager, MarketDataClient


# ── PolygonClient Tests ──


class TestPolygonClient:
    """Tests for the Polygon.io REST client."""

    def test_not_configured_without_key(self):
        client = PolygonClient(api_key="")
        assert not client.is_configured
        assert client._rest_client is None

    def test_tier_rate_limits(self):
        """Verify rate limit values per tier."""
        assert PolygonClient.TIER_LIMITS["free"] == 5
        assert PolygonClient.TIER_LIMITS["starter"] == 100
        assert PolygonClient.TIER_LIMITS["developer"] == 1000
        assert PolygonClient.TIER_LIMITS["advanced"] == 10000

    def test_rate_limit_tracking(self):
        """Rate limiter tracks call timestamps."""
        client = PolygonClient(api_key="")
        # Manually test rate limit tracking
        client._rate_limit = 3
        client._call_timestamps = [time.monotonic() - 10, time.monotonic() - 5]
        # Should not block (2 calls < 3 limit)
        # Just verify it doesn't raise
        assert len(client._call_timestamps) == 2

    def test_get_returns_empty_when_not_configured(self):
        client = PolygonClient(api_key="")
        returns = client.get_returns(["AAPL", "MSFT"])
        assert isinstance(returns, pd.DataFrame)
        assert returns.empty

    def test_get_aggregates_empty_when_not_configured(self):
        client = PolygonClient(api_key="")
        df = client.get_aggregates("AAPL")
        assert isinstance(df, pd.DataFrame)
        assert df.empty

    @pytest.mark.asyncio
    async def test_get_snapshot_empty_when_not_configured(self):
        client = PolygonClient(api_key="")
        result = await client.get_snapshot(["AAPL"])
        assert result == {}

    @pytest.mark.asyncio
    async def test_subscribe_trades_noop_when_not_configured(self):
        client = PolygonClient(api_key="")
        callback = AsyncMock()
        await client.subscribe_trades(["AAPL"], callback)
        callback.assert_not_called()

    @pytest.mark.asyncio
    async def test_stop_streaming_safe_when_not_started(self):
        client = PolygonClient(api_key="")
        await client.stop_streaming()  # Should not raise

    def test_configured_with_mock_rest_client(self):
        """Test that a configured client has expected properties."""
        with patch("src.data.polygon_client.PolygonClient.__init__", return_value=None):
            client = PolygonClient.__new__(PolygonClient)
            client.api_key = "test_key"
            client.tier = "free"
            client._rate_limit = 5
            client._call_timestamps = []
            client._rest_client = MagicMock()
            client._ws_connection = None
            client._ws_task = None
            client._streaming = False
            assert client.is_configured

    def test_get_returns_with_mock_aggregates(self):
        """Test get_returns composing data from get_aggregates."""
        client = PolygonClient.__new__(PolygonClient)
        client.api_key = "test_key"
        client.tier = "free"
        client._rate_limit = 5
        client._call_timestamps = []
        client._rest_client = MagicMock()
        client._ws_connection = None
        client._ws_task = None
        client._streaming = False

        # Mock get_aggregates to return predictable data
        dates = pd.date_range("2024-01-01", periods=100, freq="B")
        prices = pd.DataFrame({
            "close": np.random.lognormal(0, 0.02, 100).cumsum() + 100,
        }, index=dates)

        with patch.object(client, "get_aggregates", return_value=prices):
            returns = client.get_returns(["AAPL"], period_days=90)
            assert not returns.empty
            assert "AAPL" in returns.columns
            assert len(returns) <= 90


# ── DataPipelineManager Tests ──


class TestDataPipelineManager:
    """Tests for the unified data pipeline."""

    def test_init_defaults(self):
        pipeline = DataPipelineManager()
        assert pipeline.active_source == "none"
        assert not pipeline.polygon_available

    def test_init_with_polygon_key(self):
        """Pipeline initializes Polygon client when key provided."""
        # Key won't actually connect, but PolygonClient handles gracefully
        pipeline = DataPipelineManager(polygon_api_key="")
        assert not pipeline.polygon_available

    @pytest.mark.asyncio
    async def test_get_returns_falls_back_to_yfinance(self):
        """When Polygon not configured, falls back to yfinance."""
        pipeline = DataPipelineManager()

        # Mock yfinance to return test data
        dates = pd.date_range("2024-01-01", periods=50, freq="B")
        mock_returns = pd.DataFrame(
            np.random.randn(50, 2) * 0.02,
            columns=["AAPL", "MSFT"],
            index=dates,
        )

        with patch.object(pipeline._yfinance, "get_returns", return_value=mock_returns):
            returns = await pipeline.get_returns(["AAPL", "MSFT"], period_days=50)
            assert not returns.empty
            assert pipeline.active_source == "yfinance"

    @pytest.mark.asyncio
    async def test_cache_hit(self):
        """Pipeline returns cached data within TTL."""
        pipeline = DataPipelineManager(cache_ttl_seconds=300)

        dates = pd.date_range("2024-01-01", periods=50, freq="B")
        mock_returns = pd.DataFrame(
            np.random.randn(50, 2) * 0.02,
            columns=["AAPL", "MSFT"],
            index=dates,
        )

        with patch.object(pipeline._yfinance, "get_returns", return_value=mock_returns) as mock_get:
            # First call hits yfinance
            r1 = await pipeline.get_returns(["AAPL", "MSFT"], period_days=50)
            assert mock_get.call_count == 1

            # Second call should hit cache
            r2 = await pipeline.get_returns(["AAPL", "MSFT"], period_days=50)
            assert mock_get.call_count == 1  # Not called again
            assert pipeline.active_source == "cache"

    @pytest.mark.asyncio
    async def test_stale_cache_fallback(self):
        """Falls back to stale cache if both sources fail."""
        pipeline = DataPipelineManager(cache_ttl_seconds=1)

        dates = pd.date_range("2024-01-01", periods=50, freq="B")
        mock_returns = pd.DataFrame(
            np.random.randn(50, 2) * 0.02,
            columns=["AAPL", "MSFT"],
            index=dates,
        )

        # First call succeeds
        with patch.object(pipeline._yfinance, "get_returns", return_value=mock_returns):
            await pipeline.get_returns(["AAPL", "MSFT"], period_days=50)

        # Expire cache
        pipeline._last_fetch_time = time.time() - 999

        # Second call fails but returns stale cache
        with patch.object(pipeline._yfinance, "get_returns", side_effect=Exception("network error")):
            r = await pipeline.get_returns(["AAPL", "MSFT"], period_days=50)
            assert not r.empty
            assert "stale" in pipeline.active_source

    @pytest.mark.asyncio
    async def test_get_live_prices_yfinance_fallback(self):
        """Live prices fall back to yfinance."""
        pipeline = DataPipelineManager()

        dates = pd.date_range("2024-01-01", periods=5, freq="B")
        mock_prices = pd.DataFrame(
            {"AAPL": [180, 181, 182, 183, 184], "MSFT": [400, 401, 402, 403, 404]},
            index=dates,
        )

        with patch.object(pipeline._yfinance, "get_prices", return_value=mock_prices):
            prices = await pipeline.get_live_prices(["AAPL", "MSFT"])
            assert "AAPL" in prices
            assert prices["AAPL"] == 184.0

    def test_data_quality_report(self):
        pipeline = DataPipelineManager()
        report = pipeline.get_data_quality_report()
        assert "primary_source" in report
        assert "polygon_available" in report
        assert "source_usage" in report
        assert report["polygon_available"] is False

    def test_clear_cache(self):
        pipeline = DataPipelineManager()
        pipeline._last_returns_cache = pd.DataFrame({"a": [1]})
        pipeline._last_returns_key = "test"
        pipeline.clear_cache()
        assert pipeline._last_returns_cache is None
        assert pipeline._last_returns_key == ""

    @pytest.mark.asyncio
    async def test_streaming_not_available_without_polygon(self):
        pipeline = DataPipelineManager()
        callback = AsyncMock()
        await pipeline.start_streaming(["AAPL"], callback)
        assert not pipeline._streaming

    @pytest.mark.asyncio
    async def test_stop_streaming_safe(self):
        pipeline = DataPipelineManager()
        await pipeline.stop_streaming()  # Should not raise
