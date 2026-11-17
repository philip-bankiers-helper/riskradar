"""Daily-summary scheduling tests."""

import asyncio
from datetime import datetime, timedelta
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


# ── Boot-time catch-up (day-58 postmortem, 2026-11-13) ──

from unittest.mock import AsyncMock, patch  # noqa: E402

import pytest  # noqa: E402

from src.alerts.alert_manager import AlertManager  # noqa: E402
from src.delivery_log import append_delivery, read_deliveries  # noqa: E402
from src.main import (  # noqa: E402
    BOOT_CATCHUP_GRACE_SECONDS,
    _boot_catchup,
    _fire_daily_summary_slot,
    _missed_boot_catchup_target,
)
from src.models import HeatLevel, HeatScore, HeatScoreComponents  # noqa: E402


def _make_heat(score: float = 0.5, level: HeatLevel = HeatLevel.COOL) -> HeatScore:
    return HeatScore(
        score=score,
        level=level,
        components=HeatScoreComponents(
            absorption_ratio=0.5,
            turbulence=10.0,
            turbulence_percentile=0.5,
            diversification_ratio=1.5,
            diversification_percentile=0.5,
            factor_hhi=0.3,
            factor_hhi_percentile=0.5,
            avg_correlation=0.4,
            avg_correlation_percentile=0.5,
        ),
        dominant_factor="market",
        top_correlated_pair="TSLA↔PLTR (0.64)",
        action="Monitor positions",
    )


class TestMissedBootCatchupTarget:
    def test_none_before_slot(self):
        """Before 16:30 the live loop owns the fire — catch-up must not arm."""
        early_morning = datetime(2026, 11, 17, 3, 31, tzinfo=ET)  # tonight's deploy case
        assert _missed_boot_catchup_target(early_morning) is None
        just_before = datetime(2026, 11, 13, 16, 29, 59, tzinfo=ET)
        assert _missed_boot_catchup_target(just_before) is None

    def test_returns_slot_at_exact_slot_time(self):
        slot = datetime(2026, 11, 13, 16, 30, 0, tzinfo=ET)
        assert _missed_boot_catchup_target(slot) == slot

    def test_day58_scenario_boot_33_minutes_after_slot(self):
        # 2026-11-13 postmortem: service booted 33 min after the slot.
        boot = datetime(2026, 11, 13, 17, 3, 0, tzinfo=ET)
        assert _missed_boot_catchup_target(boot) == datetime(2026, 11, 13, 16, 30, tzinfo=ET)

    def test_grace_boundary_inclusive_then_excludes(self):
        slot = datetime(2026, 11, 13, 16, 30, 0, tzinfo=ET)
        edge = slot + timedelta(seconds=BOOT_CATCHUP_GRACE_SECONDS)
        assert _missed_boot_catchup_target(edge) == slot.replace(second=0, microsecond=0)
        assert _missed_boot_catchup_target(edge + timedelta(seconds=1)) is None

    def test_next_morning_boot_is_outside_window(self):
        next_day = datetime(2026, 11, 14, 9, 0, tzinfo=ET)
        assert _missed_boot_catchup_target(next_day) is None

    def test_custom_grace_bounds_the_window(self):
        boot = datetime(2026, 11, 13, 16, 35, tzinfo=ET)  # 5 min after slot
        assert _missed_boot_catchup_target(boot, grace_seconds=60) is None
        assert _missed_boot_catchup_target(boot, grace_seconds=600) is not None


def _catchup_state(tmp_path, *, heat=_make_heat()):
    """App state + real AlertManager wired to a temp delivery log."""
    mgr = AlertManager(
        telegram_bot_token="test",
        telegram_chat_id="123",
        delivery_log_path=str(tmp_path / "delivery_log.jsonl"),
    )
    return {"alert_manager": mgr, "last_heat_score": heat, "regime_state": {"regime": "normal"}}


class TestBootCatchup:
    @pytest.mark.asyncio
    async def test_fires_missed_slot_once_with_scheduled_evidence(self, tmp_path):
        """Day-58 replay: boot 33 min after the slot → one send, logged scheduled."""
        app_state = _catchup_state(tmp_path)
        boot = datetime(2026, 11, 13, 17, 3, tzinfo=ET)

        with patch.object(
            app_state["alert_manager"], "_send_telegram", new_callable=AsyncMock, return_value=True
        ) as mock_send:
            await _boot_catchup(app_state, sleep=AsyncMock(), now=lambda tz: boot)

        assert mock_send.await_count == 1
        records = read_deliveries(app_state["alert_manager"].delivery_log_path)
        assert [r["tier"] for r in records] == ["daily_summary"]
        assert records[0]["scheduled"] is True
        # et_date is send-time ET date; inside the same-day grace window
        # a real catch-up send always lands on the slot's calendar day.
        # Under the test's fake boot clock the append still uses the real
        # wall clock, so assert against the current ET date.
        assert records[0]["et_date"] == datetime.now(ET).date().isoformat()

    @pytest.mark.asyncio
    async def test_suppressed_when_slot_already_delivered(self, tmp_path):
        """Same-slot idempotency: a delivered slot must never re-fire, even via catch-up."""
        app_state = _catchup_state(tmp_path)
        boot = datetime(2026, 11, 13, 17, 3, tzinfo=ET)
        append_delivery(
            app_state["alert_manager"].delivery_log_path,
            tier="daily_summary",
            scheduled=True,
            now=datetime(2026, 11, 13, 16, 30, tzinfo=ET),
        )

        with patch.object(
            app_state["alert_manager"], "_send_telegram", new_callable=AsyncMock, return_value=True
        ) as mock_send:
            await _boot_catchup(app_state, sleep=AsyncMock(), now=lambda tz: boot)

        mock_send.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_waits_bounded_for_heat_before_firing(self, tmp_path):
        """Boot race: compute_heat_loop hasn't produced a score yet — poll, then fire."""
        app_state = _catchup_state(tmp_path, heat=None)
        boot = datetime(2026, 11, 13, 17, 3, tzinfo=ET)

        sleeps = []

        async def fake_sleep(seconds):
            sleeps.append(seconds)
            if len(sleeps) == 1:  # heat lands during the first poll interval
                app_state["last_heat_score"] = _make_heat()

        with patch.object(
            app_state["alert_manager"], "_send_telegram", new_callable=AsyncMock, return_value=True
        ) as mock_send:
            await _boot_catchup(app_state, sleep=fake_sleep, now=lambda tz: boot)

        assert mock_send.await_count == 1
        assert sleeps == [5]  # exactly one poll, then inputs were ready

    @pytest.mark.asyncio
    async def test_noop_outside_window_sends_nothing(self, tmp_path):
        app_state = _catchup_state(tmp_path)
        early = datetime(2026, 11, 17, 3, 31, tzinfo=ET)

        sleeps = AsyncMock()

        with patch.object(
            app_state["alert_manager"], "_send_telegram", new_callable=AsyncMock, return_value=True
        ) as mock_send:
            await _boot_catchup(app_state, sleep=sleeps, now=lambda tz: early)

        mock_send.assert_not_awaited()
        sleeps.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_fires_without_heat_after_bounded_wait(self, tmp_path):
        """Heat never lands: the wait exhausts and the slot logs the same skip the live loop would."""
        app_state = _catchup_state(tmp_path, heat=None)
        boot = datetime(2026, 11, 13, 17, 3, tzinfo=ET)

        sleeps = []

        async def fake_sleep(seconds):
            sleeps.append(seconds)

        with patch.object(
            app_state["alert_manager"], "_send_telegram", new_callable=AsyncMock, return_value=True
        ) as mock_send:
            await _boot_catchup(app_state, sleep=fake_sleep, now=lambda tz: boot)

        # 300s budget at 5s polls = 60 waits, then a skip (no send).
        assert len(sleeps) == 60
        mock_send.assert_not_awaited()
        assert read_deliveries(app_state["alert_manager"].delivery_log_path) == []


class TestFireSlotWeeklyRide:
    @pytest.mark.asyncio
    async def test_monday_catchup_also_delivers_scorecard(self, tmp_path):
        """A missed Monday slot caught up at boot must still ride the scorecard."""
        app_state = _catchup_state(tmp_path)
        monday_slot = datetime(2026, 11, 16, 16, 30, tzinfo=ET)  # due-week Monday (scorecard #8)

        with patch.object(
            app_state["alert_manager"], "_send_telegram", new_callable=AsyncMock, return_value=True
        ) as mock_send, patch(
            "src.main.produce_weekly_scorecard_text", new_callable=AsyncMock, return_value="WEEKLY SCORECARD"
        ):
            await _fire_daily_summary_slot(app_state, monday_slot)

        assert mock_send.await_count == 2  # summary first, scorecard strictly after
        tiers = [r["tier"] for r in read_deliveries(app_state["alert_manager"].delivery_log_path)]
        assert tiers == ["daily_summary", "weekly_scorecard"]
