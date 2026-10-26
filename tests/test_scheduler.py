"""Daily-summary scheduling tests."""

import asyncio
from datetime import datetime
from zoneinfo import ZoneInfo

from src.main import _next_daily_summary_target, _wait_for_slot


ET = ZoneInfo("America/New_York")


def test_next_daily_summary_target_before_close():
    now = datetime(2026, 9, 8, 9, 15, tzinfo=ET)
    assert _next_daily_summary_target(now) == datetime(2026, 9, 8, 16, 30, tzinfo=ET)


def test_next_daily_summary_target_at_close_moves_to_tomorrow():
    now = datetime(2026, 9, 8, 16, 30, tzinfo=ET)
    assert _next_daily_summary_target(now) == datetime(2026, 9, 9, 16, 30, tzinfo=ET)


def test_next_daily_summary_target_preserves_dst_timezone():
    now = datetime(2026, 11, 1, 17, 0, tzinfo=ET)
    target = _next_daily_summary_target(now)
    assert target == datetime(2026, 11, 2, 16, 30, tzinfo=ET)
    assert target.utcoffset().total_seconds() == -5 * 60 * 60


def test_wait_for_slot_returns_immediately_when_already_past():
    target = datetime(2026, 10, 24, 16, 30, 0, tzinfo=ET)
    sleeps: list[float] = []

    async def fake_sleep(seconds):
        sleeps.append(seconds)

    reads = iter([datetime(2026, 10, 24, 16, 30, 1, tzinfo=ET)])
    asyncio.run(_wait_for_slot(target, ET, sleep=fake_sleep, now=lambda tz: next(reads)))
    assert sleeps == []


def test_wait_for_slot_resleeps_remainder_on_early_wake():
    """2026-10-24 regression: the scheduler woke 2 s early (16:29:58 for
    a 16:30:00 target) and double-fired messages 6411/6412 — the send
    landed before the slot, then the loop re-armed the SAME-day target.
    The guard must re-sleep exactly the remainder instead of returning.
    """
    target = datetime(2026, 10, 24, 16, 30, 0, tzinfo=ET)
    sleeps: list[float] = []

    async def fake_sleep(seconds):
        sleeps.append(seconds)

    reads = iter([
        datetime(2026, 10, 24, 16, 29, 58, tzinfo=ET),
        datetime(2026, 10, 24, 16, 30, 0, 500000, tzinfo=ET),
    ])
    asyncio.run(_wait_for_slot(target, ET, sleep=fake_sleep, now=lambda tz: next(reads)))
    assert sleeps == [2.0]


def test_wait_for_slot_survives_backward_clock_jump():
    """A backward wall-clock jump mid-wait must keep re-sleeping, never
    return early (the loop bound is the target, not a sleep count)."""
    target = datetime(2026, 10, 24, 16, 30, 0, tzinfo=ET)
    sleeps: list[float] = []

    async def fake_sleep(seconds):
        sleeps.append(seconds)

    reads = iter([
        datetime(2026, 10, 24, 16, 29, 58, tzinfo=ET),
        datetime(2026, 10, 24, 16, 29, 50, tzinfo=ET),  # clock jumped back 8 s
        datetime(2026, 10, 24, 16, 30, 0, 100000, tzinfo=ET),
    ])
    asyncio.run(_wait_for_slot(target, ET, sleep=fake_sleep, now=lambda tz: next(reads)))
    assert sleeps == [2.0, 10.0]
