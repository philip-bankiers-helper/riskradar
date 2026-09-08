"""Daily-summary scheduling tests."""

from datetime import datetime
from zoneinfo import ZoneInfo

from src.main import _next_daily_summary_target


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
