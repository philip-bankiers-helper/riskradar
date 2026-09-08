"""Explicitly non-blocking integration checks that require the public network."""

import pytest

from src.data.market_data import MarketDataClient


@pytest.mark.network
def test_yfinance_can_fetch_public_market_data():
    returns = MarketDataClient().get_returns(["SPY"], period_days=10)
    assert not returns.empty
