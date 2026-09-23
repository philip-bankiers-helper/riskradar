"""Tests for the delivery-evidence audit trail (W1 streak counting)."""

from __future__ import annotations

from datetime import date, datetime, timedelta
from unittest.mock import AsyncMock, patch

import pytest

from src.alerts.alert_manager import AlertManager
from src.delivery_log import (
    ET,
    WEEKLY_SCORECARD,
    append_delivery,
    daily_streak,
    read_deliveries,
    verify_corroboration,
    w1_status,
    w3_status,
)
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

    @pytest.mark.asyncio
    async def test_send_persists_telegram_server_date(self, tmp_path):
        # The delivery record must carry Telegram's own send timestamp so
        # the ET day key is corroborated by a second, independent clock.
        mgr = self._manager(tmp_path)

        async def fake_send(message):
            mgr.last_message_id = 5556
            mgr.last_telegram_date = 1757361000
            return True

        with patch.object(mgr, "_send_telegram", new=fake_send):
            sent = await mgr.send_daily_summary(
                heat_score=_make_heat(0.5, HeatLevel.WARM),
                regime_state=None,
                attribution=None,
                recommendations=None,
                scheduled=True,
            )
        assert sent is True
        records = read_deliveries(tmp_path / "delivery_log.jsonl")
        assert records[0]["message_id"] == 5556
        assert records[0]["telegram_date"] == 1757361000


# ── Telegram server-clock corroboration ──


def _tg_ts(day: date, hours_later: float = 0) -> int:
    """Unix seconds of the 16:30 ET slot on *day*, optionally shifted."""
    moment = ET_1630(day) + timedelta(hours=hours_later)
    return int(moment.replace(tzinfo=ET).timestamp())


class TestVerifyCorroboration:
    def test_append_stores_telegram_date(self, tmp_path):
        log = tmp_path / "log.jsonl"
        append_delivery(
            log,
            tier="daily_summary",
            message_id=4823,
            scheduled=True,
            telegram_date=_tg_ts(TODAY),
            now=ET_1630(TODAY),
        )
        assert read_deliveries(log)[0]["telegram_date"] == _tg_ts(TODAY)

    def test_append_without_telegram_date_records_null(self, tmp_path):
        log = tmp_path / "log.jsonl"
        append_delivery(
            log,
            tier="daily_summary",
            message_id=1,
            scheduled=True,
            now=ET_1630(TODAY),
        )
        assert read_deliveries(log)[0]["telegram_date"] is None

    def test_matching_server_date_corroborates(self, tmp_path):
        log = tmp_path / "log.jsonl"
        append_delivery(
            log,
            tier="daily_summary",
            message_id=4823,
            scheduled=True,
            telegram_date=_tg_ts(TODAY),
            now=ET_1630(TODAY),
        )
        result = verify_corroboration(read_deliveries(log))
        assert result["checked"] == 1
        assert result["corroborated"] == 1
        assert result["mismatched"] == 0
        assert result["missing"] == 0
        assert result["mismatches"] == []

    def test_server_date_on_next_et_day_is_mismatch(self, tmp_path):
        # Recorded locally at 23:40 ET, but Telegram's server clock puts
        # the send past midnight ET: the two clocks disagree on the day.
        log = tmp_path / "log.jsonl"
        late = datetime(TODAY.year, TODAY.month, TODAY.day, 23, 40, 0)
        append_delivery(
            log,
            tier="daily_summary",
            message_id=99,
            scheduled=True,
            telegram_date=_tg_ts(TODAY, hours_later=12),
            now=late,
        )
        result = verify_corroboration(read_deliveries(log))
        assert result["checked"] == 1
        assert result["corroborated"] == 0
        assert result["mismatched"] == 1
        assert result["mismatches"][0]["message_id"] == 99
        assert result["mismatches"][0]["telegram_et_date"] == "2026-09-09"

    def test_missing_telegram_date_counted_not_failed(self, tmp_path):
        # Pre-feature history has no server date; it must not read as a
        # mismatch, only as missing corroboration.
        log = tmp_path / "log.jsonl"
        append_delivery(
            log,
            tier="daily_summary",
            message_id=1,
            scheduled=True,
            now=ET_1630(TODAY),
        )
        result = verify_corroboration(read_deliveries(log))
        assert result["missing"] == 1
        assert result["checked"] == 0
        assert result["mismatched"] == 0

    def test_other_tiers_ignored(self):
        records = [
            {"tier": "level_change", "et_date": "2026-09-08", "telegram_date": 1},
            {"tier": "emergency", "et_date": "2026-09-08", "telegram_date": 1},
        ]
        result = verify_corroboration(records)
        assert result["checked"] == 0
        assert result["missing"] == 0

    def test_garbage_telegram_date_counts_missing(self):
        records = [{"tier": "daily_summary", "et_date": "2026-09-08", "telegram_date": "not-a-number"}]
        result = verify_corroboration(records)
        assert result["missing"] == 1
        assert result["checked"] == 0


# ── W1 verdict ──


class TestW1Status:
    def _streak_log(self, tmp_path, days, message_id_start=4800):
        log = tmp_path / "log.jsonl"
        for offset in range(days):
            day = TODAY - timedelta(days=offset)
            append_delivery(
                log,
                tier="daily_summary",
                message_id=message_id_start + offset,
                heat_score=0.5,
                scheduled=True,
                telegram_date=_tg_ts(day),
                now=ET_1630(day),
            )
        return log

    def test_six_days_not_met_one_remaining(self, tmp_path):
        log = self._streak_log(tmp_path, 6)
        result = w1_status(log, today=TODAY)
        assert result["w1_met"] is False
        assert result["days_remaining"] == 1
        assert result["reasons"] == ["streak 6/7"]
        assert result["live"] is True

    def test_seven_live_days_met_with_evidence(self, tmp_path):
        log = self._streak_log(tmp_path, 7)
        result = w1_status(log, today=TODAY)
        assert result["w1_met"] is True
        assert result["reasons"] == []
        assert result["days_remaining"] == 0
        assert result["streak_days"] == 7
        assert "2026-09-02..2026-09-08" in result["evidence"]
        assert "messages 4806,4805,4804,4803,4802,4801,4800" in result["evidence"]
        assert "corroborated=7" in result["evidence"]
        assert "mismatched=0" in result["evidence"]

    def test_stale_seven_day_streak_not_met(self, tmp_path):
        log = self._streak_log(tmp_path, 7)
        result = w1_status(log, today=TODAY + timedelta(days=3))
        assert result["w1_met"] is False
        assert "streak not live" in result["reasons"]

    def test_clock_mismatch_blocks_even_at_seven(self, tmp_path):
        log = self._streak_log(tmp_path, 7)
        # Same-day dupe whose server timestamp lands on the next ET day:
        # the two clocks disagree about a delivered day — verdict blocked.
        append_delivery(
            log,
            tier="daily_summary",
            message_id=9999,
            scheduled=True,
            telegram_date=_tg_ts(TODAY, hours_later=12),
            now=ET_1630(TODAY),
        )
        result = w1_status(log, today=TODAY)
        assert result["w1_met"] is False
        assert any("mismatch" in reason for reason in result["reasons"])

    def test_missing_corroboration_does_not_block(self, tmp_path):
        # Pre-feature records carry no server timestamp; the streak still
        # counts and the verdict holds with missing reported honestly.
        log = tmp_path / "log.jsonl"
        for offset in range(7):
            day = TODAY - timedelta(days=offset)
            append_delivery(
                log,
                tier="daily_summary",
                message_id=4800 + offset,
                scheduled=True,
                now=ET_1630(day),
            )
        result = w1_status(log, today=TODAY)
        assert result["w1_met"] is True
        assert "missing=7" in result["evidence"]

    def test_manual_sends_never_count(self, tmp_path):
        log = tmp_path / "log.jsonl"
        for offset in range(9):  # manual sends on 9 straight days
            day = TODAY - timedelta(days=offset)
            append_delivery(
                log,
                tier="daily_summary",
                message_id=7000 + offset,
                scheduled=False,
                telegram_date=_tg_ts(day),
                now=ET_1630(day),
            )
        result = w1_status(log, today=TODAY)
        assert result["w1_met"] is False
        assert result["streak_days"] == 0
        assert result["evidence"] == ""


# ── W3 verdict (weekly scorecard cadence) ──

MON_FIRST = date(2026, 9, 28)  # first Monday the cadence was armed for
TUE_AFTER = date(2026, 9, 29)
MON_PREV = date(2026, 9, 21)  # Monday of the deploy week (slot already passed)


def _append_weekly(path, day: date, message_id: int, *, scheduled=True, telegram_date=None, at=None):
    append_delivery(
        path,
        tier="weekly_scorecard",
        message_id=message_id,
        scheduled=scheduled,
        telegram_date=telegram_date,
        now=at or ET_1630(day),
    )


class TestW3Status:
    def test_not_yet_due_before_first_expected_monday(self, tmp_path):
        # Tonight's reality: the cadence went live 2026-09-22, so no
        # weekly post is due until the week of 2026-09-28.
        result = w3_status(tmp_path / "log.jsonl", now=datetime(2026, 9, 23, 2, 30))
        assert result["w3_met"] is False
        assert result["delivered"] is False
        assert result["reasons"] == ["not yet due; first expected weekly post 2026-09-28"]

    def test_due_week_post_met_with_evidence(self, tmp_path):
        log = tmp_path / "log.jsonl"
        _append_weekly(log, MON_FIRST, 5301, telegram_date=_tg_ts(MON_FIRST))
        result = w3_status(log, now=datetime(2026, 9, 29, 2, 30))
        assert result["w3_met"] is True
        assert result["reasons"] == []
        assert result["delivered"] is True
        assert result["weekly_posts"] == 1
        assert result["last_post"] == {"et_date": "2026-09-28", "message_id": 5301}
        assert result["due_week_monday"] == "2026-09-28"
        assert result["evidence"] == (
            "weekly_scorecard 2026-09-28 message 5301; "
            "corroborated=1 missing=0 mismatched=0"
        )

    def test_manual_weekly_send_never_counts(self, tmp_path):
        # Invariant: a stray manual send must never satisfy (or
        # suppress) the week's real post — only scheduler sends count.
        log = tmp_path / "log.jsonl"
        _append_weekly(
            log, MON_FIRST, 5301, scheduled=False, telegram_date=_tg_ts(MON_FIRST)
        )
        result = w3_status(log, now=datetime(2026, 9, 29, 2, 30))
        assert result["w3_met"] is False
        assert result["weekly_posts"] == 0
        assert result["reasons"] == [
            "no scheduled weekly_scorecard in week of 2026-09-28"
        ]

    def test_daily_summary_records_do_not_count(self, tmp_path):
        log = tmp_path / "log.jsonl"
        _append_scheduled(log, MON_FIRST, 5300)  # daily tier, same week
        result = w3_status(log, now=datetime(2026, 9, 29, 2, 30))
        assert result["w3_met"] is False
        assert result["delivered"] is False

    def test_late_fire_after_midnight_counts_for_its_week(self, tmp_path):
        # is_weekly_scorecard_slot counts any Monday-slot fire for its
        # week; a fire delayed past ET midnight lands Tuesday but still
        # belongs to Monday's ISO week.
        log = tmp_path / "log.jsonl"
        late = datetime(2026, 9, 29, 0, 5, 0)
        ts = int(datetime(2026, 9, 29, 0, 5, 0, tzinfo=ET).timestamp())
        _append_weekly(log, TUE_AFTER, 5302, at=late, telegram_date=ts)
        result = w3_status(log, now=datetime(2026, 9, 29, 2, 30))
        assert result["w3_met"] is True
        assert result["last_post"]["et_date"] == "2026-09-29"

    def test_previous_week_post_does_not_satisfy_due_week(self, tmp_path):
        log = tmp_path / "log.jsonl"
        _append_weekly(log, MON_PREV, 5200, telegram_date=_tg_ts(MON_PREV))
        result = w3_status(log, now=datetime(2026, 9, 29, 2, 30))
        assert result["w3_met"] is False
        assert result["weekly_posts"] == 1
        assert result["last_post"]["et_date"] == "2026-09-21"
        assert result["reasons"] == [
            "no scheduled weekly_scorecard in week of 2026-09-28"
        ]

    def test_monday_before_slot_still_judges_previous_week(self, tmp_path):
        # At Monday 02:30 ET the day's slot hasn't fired; the previous
        # Monday's week is still the one under judgment.
        log = tmp_path / "log.jsonl"
        _append_weekly(log, MON_FIRST, 5301, telegram_date=_tg_ts(MON_FIRST))
        result = w3_status(log, now=datetime(2026, 10, 5, 2, 30))
        assert result["w3_met"] is True
        assert result["due_week_monday"] == "2026-09-28"

    def test_missed_monday_blocks_until_next_week(self, tmp_path):
        # Week of 09-28 delivered, Monday 10-05 missed: the verdict is
        # honest about the due week that has no post.
        log = tmp_path / "log.jsonl"
        _append_weekly(log, MON_FIRST, 5301, telegram_date=_tg_ts(MON_FIRST))
        result = w3_status(log, now=datetime(2026, 10, 7, 2, 30))
        assert result["w3_met"] is False
        assert result["due_week_monday"] == "2026-10-05"
        assert result["reasons"] == [
            "no scheduled weekly_scorecard in week of 2026-10-05"
        ]

    def test_clock_mismatch_blocks(self, tmp_path):
        log = tmp_path / "log.jsonl"
        # Local day says Monday; Telegram's server clock says Tuesday.
        _append_weekly(
            log, MON_FIRST, 5301, telegram_date=_tg_ts(MON_FIRST, hours_later=12)
        )
        result = w3_status(log, now=datetime(2026, 9, 29, 2, 30))
        assert result["w3_met"] is False
        assert any("mismatch" in reason for reason in result["reasons"])

    def test_missing_corroboration_reported_not_blocking(self, tmp_path):
        log = tmp_path / "log.jsonl"
        _append_weekly(log, MON_FIRST, 5301)  # no telegram_date
        result = w3_status(log, now=datetime(2026, 9, 29, 2, 30))
        assert result["w3_met"] is True
        assert "missing=1" in result["evidence"]
        assert "corroborated=0" in result["evidence"]

    def test_tier_constant_matches_engine(self):
        from src.engine.weekly_scorecard import WEEKLY_SCORECARD_TIER

        assert WEEKLY_SCORECARD == WEEKLY_SCORECARD_TIER
