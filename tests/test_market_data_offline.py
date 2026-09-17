"""Offline contract tests for the yfinance download call in MarketDataClient.

Pins the yf.download kwargs that keep the long-lived service healthy:
threads=False prevents the per-call file-descriptor leak (thread-local
session caches + never-reaped sockets) that exhausted the FD table on
2026-09-16 and killed the scheduled 16:30 ET summary with [Errno 24].
"""

from unittest.mock import patch

import numpy as np
import pandas as pd

from src.data.market_data import MarketDataClient


def _fake_download_frame(symbols: list[str]) -> pd.DataFrame:
    """Mimic yf.download output: MultiIndex (field, symbol) columns."""
    idx = pd.date_range("2026-01-01", periods=6, freq="D")
    cols = pd.MultiIndex.from_product([["Close", "High"], symbols])
    frame = pd.DataFrame(index=idx, columns=cols, dtype=float)
    for sym in symbols:
        frame[("Close", sym)] = np.linspace(100.0, 110.0, len(idx))
        frame[("High", sym)] = np.linspace(101.0, 111.0, len(idx))
    return frame


def test_get_returns_downloads_serial_no_thread_pool():
    client = MarketDataClient()
    with patch("src.data.market_data.yf.download") as mock_download:
        mock_download.return_value = _fake_download_frame(["SPY"])
        returns = client.get_returns(["SPY"], period_days=5)

    assert not returns.empty
    mock_download.assert_called_once()
    kwargs = mock_download.call_args.kwargs
    # Load-bearing: threaded download leaks FDs per call in the live loop.
    assert kwargs.get("threads") is False
    assert kwargs.get("progress") is False
    assert kwargs.get("auto_adjust") is True


def test_get_prices_downloads_serial_no_thread_pool():
    client = MarketDataClient()
    with patch("src.data.market_data.yf.download") as mock_download:
        mock_download.return_value = _fake_download_frame(["SPY", "TLT"])
        prices = client.get_prices(["SPY", "TLT"], period_days=5)

    assert not prices.empty
    mock_download.assert_called_once()
    kwargs = mock_download.call_args.kwargs
    assert kwargs.get("threads") is False
    assert kwargs.get("progress") is False
    assert kwargs.get("auto_adjust") is True


def test_repeated_fetches_do_not_grow_the_call_count():
    """The in-memory cache means an unchanged day-range refetch hits cache."""
    client = MarketDataClient()
    with patch("src.data.market_data.yf.download") as mock_download:
        mock_download.return_value = _fake_download_frame(["SPY"])
        client.get_returns(["SPY"], period_days=5, end_date=pd.Timestamp("2026-01-06"))
        client.get_returns(["SPY"], period_days=5, end_date=pd.Timestamp("2026-01-06"))

    assert mock_download.call_count == 1
