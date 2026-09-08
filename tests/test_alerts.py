"""Tests for multi-tier alert system."""

from __future__ import annotations

import time
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.alerts.alert_manager import AlertManager, AlertTier, DEFAULT_COOLDOWNS
from src.models import (
    HeatLevel,
    HeatScore,
    HeatScoreComponents,
    PositionAttribution,
    PortfolioRecommendation,
    TradeAction,
)


def _make_heat(score: float, level: HeatLevel) -> HeatScore:
    """Create a test HeatScore."""
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
        dominant_factor="ai_tech",
        top_correlated_pair="NVDA↔AMD (0.85)",
        action="Monitor positions",
    )


def _make_attribution() -> list[PositionAttribution]:
    return [
        PositionAttribution(
            symbol="NVDA",
            weight=0.20,
            marginal_heat=0.05,
            correlation_contribution=0.3,
            factor_concentration=0.25,
            risk_contribution=0.3,
            heat_share=0.28,
            dominant_factor="ai_tech",
            dominant_factor_beta=1.5,
            recommendation="reduce",
            recommendation_reason="High concentration in AI tech",
        ),
        PositionAttribution(
            symbol="TSLA",
            weight=0.10,
            marginal_heat=0.03,
            correlation_contribution=0.15,
            factor_concentration=0.12,
            risk_contribution=0.18,
            heat_share=0.15,
            dominant_factor="momentum",
            dominant_factor_beta=1.2,
            recommendation="monitor",
            recommendation_reason="Moderate factor exposure",
        ),
    ]


def _make_recommendations() -> PortfolioRecommendation:
    return PortfolioRecommendation(
        summary="Reduce NVDA and add hedges",
        current_heat=0.85,
        estimated_heat_after=0.70,
        actions=[
            TradeAction(
                action="reduce",
                symbol="NVDA",
                current_weight=0.20,
                target_weight=0.10,
                delta=-0.10,
                impact_estimate=0.08,
                priority=1,
                reason="Largest heat contributor",
                urgency="today",
            ),
        ],
        hedges=[
            TradeAction(
                action="hedge",
                symbol="SOXS",
                current_weight=0.0,
                target_weight=0.05,
                delta=0.05,
                impact_estimate=0.03,
                priority=2,
                reason="Inverse semiconductor exposure",
                urgency="today",
            ),
        ],
    )


# ── Initialization Tests ──


class TestAlertManagerInit:
    def test_default_cooldowns(self):
        mgr = AlertManager()
        assert mgr._cooldowns["daily_summary"] == 1440
        assert mgr._cooldowns["level_change"] == 30
        assert mgr._cooldowns["emergency"] == 0

    def test_custom_cooldowns(self):
        mgr = AlertManager(cooldown_minutes={"level_change": 60, "emergency": 5})
        assert mgr._cooldowns["level_change"] == 60
        assert mgr._cooldowns["emergency"] == 5
        # Others should be default
        assert mgr._cooldowns["daily_summary"] == 1440

    def test_no_telegram_config(self):
        mgr = AlertManager()
        assert mgr.bot_token == ""
        assert mgr.chat_id == ""


# ── Cooldown Tests ──


class TestCooldowns:
    def test_not_on_cooldown_initially(self):
        mgr = AlertManager()
        assert not mgr._is_on_cooldown(AlertTier.LEVEL_CHANGE)

    def test_on_cooldown_after_send(self):
        mgr = AlertManager()
        mgr._mark_sent(AlertTier.LEVEL_CHANGE)
        assert mgr._is_on_cooldown(AlertTier.LEVEL_CHANGE)

    def test_emergency_never_on_cooldown(self):
        mgr = AlertManager()
        mgr._mark_sent(AlertTier.EMERGENCY)
        # Emergency has 0 cooldown
        assert not mgr._is_on_cooldown(AlertTier.EMERGENCY)

    def test_cooldown_expires(self):
        mgr = AlertManager(cooldown_minutes={"level_change": 1})
        mgr._last_sent["level_change"] = time.time() - 120  # 2 minutes ago
        assert not mgr._is_on_cooldown(AlertTier.LEVEL_CHANGE)

    def test_cooldown_status_report(self):
        mgr = AlertManager()
        status = mgr.get_cooldown_status()
        assert "daily_summary" in status
        assert "emergency" in status
        for tier_status in status.values():
            assert "cooldown_minutes" in tier_status
            assert "on_cooldown" in tier_status
            assert "remaining_minutes" in tier_status


# ── Formatting Tests ──


class TestFormatting:
    def test_level_change_format(self):
        mgr = AlertManager()
        heat = _make_heat(0.80, HeatLevel.HOT)
        msg = mgr.format_level_change_alert(heat, HeatLevel.WARM, _make_attribution())
        assert "HEAT LEVEL CHANGE" in msg
        assert "WARM" in msg
        assert "HOT" in msg
        assert "NVDA" in msg
        assert "0.800" in msg

    def test_level_change_direction_up(self):
        mgr = AlertManager()
        heat = _make_heat(0.80, HeatLevel.HOT)
        msg = mgr.format_level_change_alert(heat, HeatLevel.WARM, None)
        # Should show UP arrow
        assert "\u2b06" in msg

    def test_level_change_direction_down(self):
        mgr = AlertManager()
        heat = _make_heat(0.50, HeatLevel.COOL)
        msg = mgr.format_level_change_alert(heat, HeatLevel.WARM, None)
        # Should show DOWN arrow
        assert "\u2b07" in msg

    def test_regime_shift_format_crisis(self):
        mgr = AlertManager()
        msg = mgr.format_regime_shift_alert("normal", "crisis", 0.85)
        assert "REGIME SHIFT" in msg
        assert "NORMAL" in msg
        assert "CRISIS" in msg
        assert "85%" in msg
        assert "Reduce gross exposure" in msg

    def test_regime_shift_format_low_vol(self):
        mgr = AlertManager()
        msg = mgr.format_regime_shift_alert("normal", "low_vol", 0.70)
        assert "carry strategies" in msg

    def test_emergency_format(self):
        mgr = AlertManager()
        heat = _make_heat(0.95, HeatLevel.EMERGENCY)
        recs = _make_recommendations()
        msg = mgr.format_emergency_alert(heat, _make_attribution(), recs)
        assert "EMERGENCY RISK ALERT" in msg
        assert "0.950" in msg
        assert "REDUCE" in msg or "reduce" in msg.lower()

    def test_emergency_format_without_recommendations(self):
        mgr = AlertManager()
        heat = _make_heat(0.95, HeatLevel.EMERGENCY)
        msg = mgr.format_emergency_alert(heat, None, None)
        assert "EMERGENCY" in msg
        assert "Reduce largest positions" in msg


# ── Alert Logic Tests ──


class TestAlertLogic:
    @pytest.mark.asyncio
    async def test_emergency_triggers_alert(self):
        mgr = AlertManager(telegram_bot_token="test", telegram_chat_id="123")
        heat = _make_heat(0.95, HeatLevel.EMERGENCY)
        mgr._previous_heat_level = HeatLevel.CRITICAL

        with patch.object(mgr, "_send_telegram", new_callable=AsyncMock, return_value=True):
            sent = await mgr.check_and_alert(
                heat_score=heat,
                previous_heat=None,
                regime_state={"regime": "crisis"},
                throttle_state=None,
                attribution=_make_attribution(),
                recommendations=_make_recommendations(),
            )
            assert "emergency" in sent

    @pytest.mark.asyncio
    async def test_level_change_triggers_alert(self):
        mgr = AlertManager(telegram_bot_token="test", telegram_chat_id="123")
        mgr._previous_heat_level = HeatLevel.WARM
        heat = _make_heat(0.80, HeatLevel.HOT)

        with patch.object(mgr, "_send_telegram", new_callable=AsyncMock, return_value=True):
            sent = await mgr.check_and_alert(
                heat_score=heat,
                previous_heat=None,
                regime_state={"regime": "normal"},
                throttle_state=None,
                attribution=None,
                recommendations=None,
            )
            assert "level_change" in sent

    @pytest.mark.asyncio
    async def test_no_alert_when_level_unchanged(self):
        mgr = AlertManager(telegram_bot_token="test", telegram_chat_id="123")
        mgr._previous_heat_level = HeatLevel.WARM
        heat = _make_heat(0.60, HeatLevel.WARM)

        with patch.object(mgr, "_send_telegram", new_callable=AsyncMock, return_value=True):
            sent = await mgr.check_and_alert(
                heat_score=heat,
                previous_heat=None,
                regime_state={"regime": "normal"},
                throttle_state=None,
                attribution=None,
                recommendations=None,
            )
            assert "level_change" not in sent

    @pytest.mark.asyncio
    async def test_regime_shift_triggers_alert(self):
        mgr = AlertManager(telegram_bot_token="test", telegram_chat_id="123")
        mgr._previous_heat_level = HeatLevel.HOT
        mgr._previous_regime = "normal"
        heat = _make_heat(0.80, HeatLevel.HOT)

        with patch.object(mgr, "_send_telegram", new_callable=AsyncMock, return_value=True):
            sent = await mgr.check_and_alert(
                heat_score=heat,
                previous_heat=None,
                regime_state={"regime": "crisis", "confidence": 0.9},
                throttle_state=None,
                attribution=None,
                recommendations=None,
            )
            assert "regime_shift" in sent

    @pytest.mark.asyncio
    async def test_cooldown_prevents_duplicate_alerts(self):
        mgr = AlertManager(telegram_bot_token="test", telegram_chat_id="123")
        mgr._previous_heat_level = HeatLevel.WARM
        heat = _make_heat(0.80, HeatLevel.HOT)

        with patch.object(mgr, "_send_telegram", new_callable=AsyncMock, return_value=True):
            # First alert
            sent1 = await mgr.check_and_alert(
                heat_score=heat,
                previous_heat=None,
                regime_state={"regime": "normal"},
                throttle_state=None,
                attribution=None,
                recommendations=None,
            )
            assert "level_change" in sent1

            # Simulate level going back to warm and then hot again
            mgr._previous_heat_level = HeatLevel.WARM
            sent2 = await mgr.check_and_alert(
                heat_score=heat,
                previous_heat=None,
                regime_state={"regime": "normal"},
                throttle_state=None,
                attribution=None,
                recommendations=None,
            )
            # Should be blocked by cooldown
            assert "level_change" not in sent2

    @pytest.mark.asyncio
    async def test_no_telegram_no_error(self):
        """Alert manager works without Telegram config (logs only)."""
        mgr = AlertManager()
        mgr._previous_heat_level = HeatLevel.WARM
        heat = _make_heat(0.80, HeatLevel.HOT)

        sent = await mgr.check_and_alert(
            heat_score=heat,
            previous_heat=None,
            regime_state={"regime": "normal"},
            throttle_state=None,
            attribution=None,
            recommendations=None,
        )
        # No alerts sent because Telegram not configured
        assert sent == []


# ── Daily Summary Tests ──


class TestDailySummary:
    @pytest.mark.asyncio
    async def test_daily_summary_format(self):
        mgr = AlertManager(telegram_bot_token="test", telegram_chat_id="123")
        heat = _make_heat(0.65, HeatLevel.WARM)

        with patch.object(mgr, "_send_telegram", new_callable=AsyncMock, return_value=True) as mock_send:
            result = await mgr.send_daily_summary(
                heat_score=heat,
                regime_state={"regime": "normal", "confidence": 0.75},
                attribution=_make_attribution(),
                recommendations=_make_recommendations(),
                data_quality={"primary_source": "yfinance", "data_age_seconds": 120},
            )
            assert result is True
            msg = mock_send.call_args[0][0]
            assert "DAILY RISK SUMMARY" in msg
            assert "NVDA" in msg
            assert "yfinance" in msg

    @pytest.mark.asyncio
    async def test_daily_summary_cooldown(self):
        mgr = AlertManager(telegram_bot_token="test", telegram_chat_id="123")
        mgr._mark_sent(AlertTier.DAILY_SUMMARY)
        heat = _make_heat(0.65, HeatLevel.WARM)

        result = await mgr.send_daily_summary(
            heat_score=heat,
            regime_state=None,
            attribution=None,
            recommendations=None,
        )
        assert result is False

    @pytest.mark.asyncio
    async def test_telegram_send_targets_configured_topic(self):
        mgr = AlertManager(
            telegram_bot_token="synthetic-token",
            telegram_chat_id="-100123",
            telegram_thread_id=456,
        )
        response = MagicMock()
        response.status_code = 200
        response.json.return_value = {"ok": True, "result": {"message_id": 789}}

        with patch("src.alerts.alert_manager.httpx.AsyncClient") as client_class:
            client = client_class.return_value.__aenter__.return_value
            client.post = AsyncMock(return_value=response)
            assert await mgr._send_telegram("summary") is True

        payload = client.post.call_args.kwargs["json"]
        assert payload["chat_id"] == "-100123"
        assert payload["message_thread_id"] == 456
        assert mgr.last_message_id == 789


# ── Alert History Tests ──


class TestAlertHistory:
    def test_record_alert(self):
        mgr = AlertManager()
        mgr._record_alert(AlertTier.LEVEL_CHANGE, 0.80)
        assert len(mgr.alert_history) == 1
        assert mgr.alert_history[0]["tier"] == "level_change"
        assert mgr.alert_history[0]["heat_score"] == 0.80

    def test_history_max_100(self):
        mgr = AlertManager()
        for i in range(120):
            mgr._record_alert(AlertTier.LEVEL_CHANGE, 0.5 + i * 0.001)
        assert len(mgr.alert_history) == 100

    def test_get_alert_history(self):
        mgr = AlertManager()
        mgr._record_alert(AlertTier.EMERGENCY, 0.95)
        history = mgr.get_alert_history()
        assert len(history) == 1
        assert history[0]["tier"] == "emergency"
