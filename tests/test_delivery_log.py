"""Tests for the delivery-evidence audit trail (W1 streak counting)."""

from __future__ import annotations

from datetime import date, datetime, timedelta
from unittest.mock import AsyncMock, patch

import pytest

from src.alerts.alert_manager import AlertManager
from src.delivery_log import append_delivery, daily_streak, read_deliveries
from src.models import HeatLevel
from tests.test_alerts import _make_attribution, _make_heat, _make_recommendations

TODAY = date(2026, 9, 8)
ET_1630 = lambda d: datetime(d.year, d.month, d.day, 16, 30, 0)  # noqa: E731


def _append_scheduled(path, day: date, message_id: int, heat: float = 0.5):
    ok = append_delivery(
        path,
        tier="daily_summary",
        message_id=message_id,
        heat_score=heat,
        scheduled=True,
        now=ET_1630(day),
    )
    assert ok is True


# ── append / read round-trip ──


class TestAppendRead:
    def test_append_creates_jsonl_record(self, tmp_path):
        log = tmp_path / "delivery_log.jsonl"
        _append_scheduled(log, TODAY, 4801, heat=0.42)
        records = read_deliveries(log)
        assert len(records) == 1
        rec = records[0]
        assert rec["tier"] == "daily_summary"
        assert rec["et_date"] == "2026-09-08"
        assert rec["et_time"] == "16:30:00"
        assert rec["scheduled"] is True
        assert rec["message_id"] == 4801
        assert rec["heat_score"] == 0.42

    def test_append_never_raises_on_bad_path(self):
        # Evidence logging must never break the alert path.
        ok = append_delivery(
            "/dev/null/nope/delivery_log.jsonl",
            tier="daily_summary",
            message_id=1,
        )
        assert ok is False

    def test_read_missing_file_returns_empty(self, tmp_path):
        assert read_deliveries(tmp_path / "missing.jsonl") == []

    def test_read_skips_corrupt_lines(self, tmp_path):
        log = tmp_path / "log.jsonl"
        _append_scheduled(log, TODAY, 1)
        log.write_text(log.read_text() + "{not json}\n")
        _append_scheduled(log, TODAY - timedelta(days=1), 2)
        assert len(read_deliveries(log)) == 2


# ── streak counting ──


class TestDailyStreak:
    def test_missing_file_is_zero_streak(self, tmp_path):
        result = daily_streak(tmp_path / "missing.jsonl", today=TODAY)
        assert result == {
            "streak": 0,
            "last_date": None,
            "live": False,
            "delivered_days": 0,
        }

    def test_single_delivery_today(self, tmp_path):
        log = tmp_path / "log.jsonl"
        _append_scheduled(log, TODAY, 4801)
        result = daily_streak(log, today=TODAY)
        assert result["streak"] == 1
        assert result["last_date"] == "2026-09-08"
        assert result["live"] is True

    def test_seven_consecutive_days(self, tmp_path):
        log = tmp_path / "log.jsonl"
        for offset in range(7):
            _append_scheduled(log, TODAY - timedelta(days=offset), 4800 + offset)
        result = daily_streak(log, today=TODAY)
        assert result["streak"] == 7
        assert result["live"] is True

    def test_gap_resets_streak(self, tmp_path):
        log = tmp_path / "log.jsonl"
        _append_scheduled(log, TODAY - timedelta(days=3), 1)
        _append_scheduled(log, TODAY - timedelta(days=2), 2)
        # yesterday missed
        _append_scheduled(log, TODAY, 4)
        result = daily_streak(log, today=TODAY)
        assert result["streak"] == 1
        assert result["delivered_days"] == 3

    def test_streak_ending_yesterday_is_live(self, tmp_path):
        # Before today's 16:30 ET slot, yesterday's delivery is current.
        log = tmp_path / "log.jsonl"
        _append_scheduled(log, TODAY - timedelta(days=1), 4800)
        result = daily_streak(log, today=TODAY)
        assert result["streak"] == 1
        assert result["live"] is True

    def test_stale_streak_is_not_live(self, tmp_path):
        log = tmp_path / "log.jsonl"
        _append_scheduled(log, TODAY - timedelta(days=2), 4800)
        _append_scheduled(log, TODAY - timedelta(days=3), 4799)
        result = daily_streak(log, today=TODAY)
        assert result["streak"] == 2
        assert result["live"] is False

    def test_same_day_dedupe(self, tmp_path):
        log = tmp_path / "log.jsonl"
        _append_scheduled(log, TODAY, 1)
        _append_scheduled(log, TODAY, 2)
        result = daily_streak(log, today=TODAY)
        assert result["streak"] == 1
        assert result["delivered_days"] == 1

    def test_manual_sends_excluded_from_streak(self, tmp_path):
        log = tmp_path / "log.jsonl"
        append_delivery(
            log,
            tier="daily_summary",
            message_id=4800,
            scheduled=False,
            now=ET_1630(TODAY),
        )
        result = daily_streak(log, today=TODAY)
        assert result["streak"] == 0

    def test_manual_sends_counted_when_inclusive(self, tmp_path):
        log = tmp_path / "log.jsonl"
        append_delivery(
            log,
            tier="daily_summary",
            message_id=4800,
            scheduled=False,
            now=ET_1630(TODAY),
        )
        result = daily_streak(log, today=TODAY, scheduled_only=False)
        assert result["streak"] == 1

    def test_other_tiers_ignored(self, tmp_path):
        log = tmp_path / "log.jsonl"
        append_delivery(
            log,
            tier="level_change",
            message_id=1,
            scheduled=True,
            now=ET_1630(TODAY),
        )
        assert daily_streak(log, today=TODAY)["streak"] == 0


# ── AlertManager integration ──


class TestAlertManagerDeliveryLog:
    def _manager(self, tmp_path):
        return AlertManager(
            telegram_bot_token="test",
            telegram_chat_id="123",
            telegram_thread_id=4799,
            delivery_log_path=str(tmp_path / "delivery_log.jsonl"),
        )

    @pytest.mark.asyncio
    async def test_successful_send_appends_scheduled_record(self, tmp_path):
        mgr = self._manager(tmp_path)
        mgr.last_message_id = None

        async def fake_send(message):
            mgr.last_message_id = 5555
            return True

        with patch.object(mgr, "_send_telegram", new=fake_send):
            sent = await mgr.send_daily_summary(
                heat_score=_make_heat(0.5, HeatLevel.WARM),
                regime_state={"regime": "normal", "confidence": 0.9},
                attribution=_make_attribution(),
                recommendations=_make_recommendations(),
                scheduled=True,
            )
        assert sent is True
        records = read_deliveries(tmp_path / "delivery_log.jsonl")
        assert len(records) == 1
        assert records[0]["scheduled"] is True
        assert records[0]["message_id"] == 5555

    @pytest.mark.asyncio
    async def test_failed_send_appends_nothing(self, tmp_path):
        mgr = self._manager(tmp_path)
        with patch.object(mgr, "_send_telegram", new=AsyncMock(return_value=False)):
            sent = await mgr.send_daily_summary(
                heat_score=_make_heat(0.5, HeatLevel.WARM),
                regime_state=None,
                attribution=None,
                recommendations=None,
                scheduled=True,
            )
        assert sent is False
        assert read_deliveries(tmp_path / "delivery_log.jsonl") == []

    @pytest.mark.asyncio
    async def test_scheduled_send_bypasses_cooldown(self, tmp_path):
        # A manual test send must never suppress the real 16:30 ET slot.
        from src.alerts.alert_manager import AlertTier

        mgr = self._manager(tmp_path)
        mgr._mark_sent(AlertTier.DAILY_SUMMARY)

        with patch.object(mgr, "_send_telegram", new=AsyncMock(return_value=True)):
            sent = await mgr.send_daily_summary(
                heat_score=_make_heat(0.5, HeatLevel.WARM),
                regime_state=None,
                attribution=None,
                recommendations=None,
                scheduled=True,
            )
        assert sent is True

    @pytest.mark.asyncio
    async def test_manual_send_still_respects_cooldown(self, tmp_path):
        from src.alerts.alert_manager import AlertTier

        mgr = self._manager(tmp_path)
        mgr._mark_sent(AlertTier.DAILY_SUMMARY)
        with patch.object(mgr, "_send_telegram", new=AsyncMock(return_value=True)):
            sent = await mgr.send_daily_summary(
                heat_score=_make_heat(0.5, HeatLevel.WARM),
                regime_state=None,
                attribution=None,
                recommendations=None,
                scheduled=False,
            )
        assert sent is False

    @pytest.mark.asyncio
    async def test_no_delivery_log_writes_nothing(self, tmp_path):
        mgr = AlertManager(telegram_bot_token="test", telegram_chat_id="123")
        assert mgr.delivery_log_path == ""
        with patch.object(mgr, "_send_telegram", new=AsyncMock(return_value=True)):
            sent = await mgr.send_daily_summary(
                heat_score=_make_heat(0.5, HeatLevel.WARM),
                regime_state=None,
                attribution=None,
                recommendations=None,
            )
        assert sent is True
