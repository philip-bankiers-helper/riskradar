"""Tests for the weekly scorecard cadence wiring (no network)."""

from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

import pandas as pd
import pytest

from src.alerts.alert_manager import AlertManager
from src.delivery_log import daily_streak, read_deliveries
from src.engine.weekly_scorecard import (
    WEEKLY_SCORECARD_SYMBOL,
    WEEKLY_SCORECARD_TIER,
    build_scorecard_text,
    is_weekly_scorecard_slot,
    produce_weekly_scorecard_text,
)

ET = ZoneInfo("America/New_York")


def series_from(prices, start="2024-01-02"):
    return pd.Series(
        [float(p) for p in prices],
        index=pd.bdate_range(start, periods=len(prices)),
    )


def drawdown_series():
    # 100 -> 90 (-10%) -> 100 twice over a long enough window
    base = [100] * 10 + [100, 95, 90, 95, 100] + [100] * 10 + [100, 97, 94, 97, 100] + [100] * 10
    return series_from(base)


# --- is_weekly_scorecard_slot -------------------------------------------


def test_monday_slot_qualifies():
    assert is_weekly_scorecard_slot(datetime(2026, 9, 28, 16, 30, tzinfo=ET))


def test_non_monday_slots_do_not_qualify():
    for day in (22, 23, 24, 25, 26, 27):  # Tue 09-22 .. Sun 09-27
        assert not is_weekly_scorecard_slot(datetime(2026, 9, day, 16, 30, tzinfo=ET))


def test_late_firing_monday_slot_still_counts():
    # A slot that fires late must still count for its week.
    assert is_weekly_scorecard_slot(datetime(2026, 9, 28, 17, 5, tzinfo=ET))


# --- build_scorecard_text -------------------------------------------------


def test_build_scorecard_text_renders_symbol_header():
    text = build_scorecard_text(drawdown_series(), None)
    assert text is not None
    assert "WEEKLY LEAD-TIME SCORECARD" in text
    assert WEEKLY_SCORECARD_SYMBOL in text


def test_build_scorecard_text_none_for_unusable_closes():
    assert build_scorecard_text(None, None) is None
    assert build_scorecard_text(pd.Series(dtype=float), None) is None
    assert build_scorecard_text("not-a-series", None) is None


# --- produce_weekly_scorecard_text ---------------------------------------


@pytest.mark.asyncio
async def test_produce_uses_injected_fetcher():
    async def fetch(symbol):
        assert symbol == WEEKLY_SCORECARD_SYMBOL
        return drawdown_series(), None

    text = await produce_weekly_scorecard_text(fetch=fetch)
    assert text is not None
    assert "WEEKLY LEAD-TIME SCORECARD" in text


@pytest.mark.asyncio
async def test_produce_returns_none_on_fetch_failure():
    async def fetch(symbol):
        raise RuntimeError("network down")

    assert await produce_weekly_scorecard_text(fetch=fetch) is None


@pytest.mark.asyncio
async def test_produce_returns_none_on_empty_data():
    async def fetch(symbol):
        return None, None

    assert await produce_weekly_scorecard_text(fetch=fetch) is None


# --- AlertManager.send_weekly_scorecard -----------------------------------


def _manager(tmp_path, monkeypatch, *, ok=True):
    manager = AlertManager(
        telegram_bot_token="x",
        telegram_chat_id="1",
        telegram_thread_id=4799,
        delivery_log_path=str(tmp_path / "delivery_log.jsonl"),
    )

    async def fake_send(message):
        if ok:
            manager.last_message_id = 5199
            manager.last_telegram_date = 1750000000
            return True
        return False

    monkeypatch.setattr(manager, "_send_telegram", fake_send)
    return manager


@pytest.mark.asyncio
async def test_send_weekly_scorecard_logs_own_tier(tmp_path, monkeypatch):
    manager = _manager(tmp_path, monkeypatch)
    assert await manager.send_weekly_scorecard("<b>WEEKLY LEAD-TIME SCORECARD</b>")
    records = read_deliveries(tmp_path / "delivery_log.jsonl")
    assert len(records) == 1
    rec = records[0]
    assert rec["tier"] == WEEKLY_SCORECARD_TIER
    assert rec["scheduled"] is True
    assert rec["message_id"] == 5199


@pytest.mark.asyncio
async def test_weekly_tier_never_counts_into_daily_streak(tmp_path, monkeypatch):
    # The W1 evidence pipeline must ignore weekly scorecard records.
    manager = _manager(tmp_path, monkeypatch)
    await manager.send_weekly_scorecard("text")
    log = tmp_path / "delivery_log.jsonl"
    assert daily_streak(log)["streak"] == 0
    assert daily_streak(log)["delivered_days"] == 0


@pytest.mark.asyncio
async def test_send_weekly_scorecard_failure_returns_false(tmp_path, monkeypatch):
    manager = _manager(tmp_path, monkeypatch, ok=False)
    assert not await manager.send_weekly_scorecard("text")
    assert read_deliveries(tmp_path / "delivery_log.jsonl") == []


@pytest.mark.asyncio
async def test_send_weekly_scorecard_empty_text_short_circuits(tmp_path, monkeypatch):
    manager = _manager(tmp_path, monkeypatch)
    assert not await manager.send_weekly_scorecard("")
    assert read_deliveries(tmp_path / "delivery_log.jsonl") == []
