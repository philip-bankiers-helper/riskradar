"""Multi-tier alert system with rate limiting, cooldowns, and rich formatting.

Tiers:
- DAILY_SUMMARY: End-of-day recap at 16:30 ET
- LEVEL_CHANGE: Heat level transitions (cool->warm, warm->hot, etc.)
- THRESHOLD_CROSS: Specific threshold crossed
- REGIME_SHIFT: Regime detector transition
- EMERGENCY: Emergency broadcast (no cooldown)
"""

from __future__ import annotations

import asyncio
import logging
import time
from datetime import datetime, timezone
from enum import Enum

import httpx

from src.delivery_log import ET
from src.models import HeatLevel, HeatScore, PositionAttribution, PortfolioRecommendation

logger = logging.getLogger(__name__)
# httpx's INFO request line includes the Bot API token in the URL.
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("httpcore").setLevel(logging.WARNING)

HEAT_EMOJI = {
    HeatLevel.COOL: "\U0001f7e2",      # green circle
    HeatLevel.WARM: "\U0001f7e1",      # yellow circle
    HeatLevel.HOT: "\U0001f7e0",       # orange circle
    HeatLevel.CRITICAL: "\U0001f534",  # red circle
    HeatLevel.EMERGENCY: "\u26d4",     # no entry
}

LEVEL_SEVERITY = {
    HeatLevel.COOL: 0,
    HeatLevel.WARM: 1,
    HeatLevel.HOT: 2,
    HeatLevel.CRITICAL: 3,
    HeatLevel.EMERGENCY: 4,
}


class AlertTier(str, Enum):
    DAILY_SUMMARY = "daily_summary"
    LEVEL_CHANGE = "level_change"
    THRESHOLD_CROSS = "threshold_cross"
    REGIME_SHIFT = "regime_shift"
    EMERGENCY = "emergency"


DEFAULT_COOLDOWNS = {
    AlertTier.DAILY_SUMMARY: 1440,     # 24 hours
    AlertTier.LEVEL_CHANGE: 30,        # 30 minutes
    AlertTier.THRESHOLD_CROSS: 60,     # 1 hour
    AlertTier.REGIME_SHIFT: 15,        # 15 minutes
    AlertTier.EMERGENCY: 0,            # No cooldown
}


class AlertManager:
    """Multi-tier alerting with rate limiting, cooldowns, and rich formatting."""

    def __init__(
        self,
        telegram_bot_token: str = "",
        telegram_chat_id: str = "",
        telegram_thread_id: int | None = None,
        cooldown_minutes: dict[str, int] | None = None,
        delivery_log_path: str = "",
    ):
        self.bot_token = telegram_bot_token
        self.chat_id = telegram_chat_id
        self.thread_id = telegram_thread_id
        self.last_message_id: int | None = None
        self.delivery_log_path = delivery_log_path
        self._base_url = f"https://api.telegram.org/bot{telegram_bot_token}" if telegram_bot_token else ""

        # Cooldown tracking: tier -> last send timestamp
        self._last_sent: dict[str, float] = {}
        self._cooldowns: dict[str, int] = {}

        # Merge default cooldowns with overrides
        for tier in AlertTier:
            default = DEFAULT_COOLDOWNS.get(tier, 60)
            if cooldown_minutes and tier.value in cooldown_minutes:
                self._cooldowns[tier.value] = cooldown_minutes[tier.value]
            else:
                self._cooldowns[tier.value] = default

        # State tracking
        self._previous_heat_level: HeatLevel | None = None
        self._previous_regime: str | None = None

        # Alert history for dashboard
        self.alert_history: list[dict] = []

    def _is_on_cooldown(self, tier: AlertTier) -> bool:
        """Check if a tier is on cooldown."""
        cooldown_minutes = self._cooldowns.get(tier.value, 60)
        if cooldown_minutes == 0:
            return False

        last_sent = self._last_sent.get(tier.value, 0)
        elapsed_minutes = (time.time() - last_sent) / 60
        return elapsed_minutes < cooldown_minutes

    def _mark_sent(self, tier: AlertTier) -> None:
        """Record that an alert was sent for this tier."""
        self._last_sent[tier.value] = time.time()

    async def check_and_alert(
        self,
        heat_score: HeatScore,
        previous_heat: HeatScore | None,
        regime_state: dict | None,
        throttle_state: dict | None,
        attribution: list[PositionAttribution] | None,
        recommendations: PortfolioRecommendation | None,
    ) -> list[str]:
        """Evaluate all alert conditions and send appropriate alerts.

        Returns list of alert tier names that were sent.
        """
        sent: list[str] = []

        # 1. Emergency check
        if heat_score.level == HeatLevel.EMERGENCY:
            if not self._is_on_cooldown(AlertTier.EMERGENCY):
                msg = self.format_emergency_alert(heat_score, attribution, recommendations)
                if await self._send_telegram(msg):
                    self._mark_sent(AlertTier.EMERGENCY)
                    sent.append(AlertTier.EMERGENCY.value)
                    self._record_alert(AlertTier.EMERGENCY, heat_score.score)

        # 2. Level change check
        if self._previous_heat_level is not None and heat_score.level != self._previous_heat_level:
            if not self._is_on_cooldown(AlertTier.LEVEL_CHANGE):
                msg = self.format_level_change_alert(
                    heat_score, self._previous_heat_level, attribution
                )
                if await self._send_telegram(msg):
                    self._mark_sent(AlertTier.LEVEL_CHANGE)
                    sent.append(AlertTier.LEVEL_CHANGE.value)
                    self._record_alert(AlertTier.LEVEL_CHANGE, heat_score.score)

        self._previous_heat_level = heat_score.level

        # 3. Regime shift check
        current_regime = regime_state.get("regime", "unknown") if regime_state else "unknown"
        if (
            self._previous_regime is not None
            and current_regime != self._previous_regime
            and current_regime != "unknown"
        ):
            if not self._is_on_cooldown(AlertTier.REGIME_SHIFT):
                confidence = regime_state.get("confidence", 0) if regime_state else 0
                msg = self.format_regime_shift_alert(
                    self._previous_regime, current_regime, confidence
                )
                if await self._send_telegram(msg):
                    self._mark_sent(AlertTier.REGIME_SHIFT)
                    sent.append(AlertTier.REGIME_SHIFT.value)
                    self._record_alert(AlertTier.REGIME_SHIFT, heat_score.score)

        self._previous_regime = current_regime

        return sent

    async def send_daily_summary(
        self,
        heat_score: HeatScore,
        regime_state: dict | None,
        attribution: list[PositionAttribution] | None,
        recommendations: PortfolioRecommendation | None,
        data_quality: dict | None = None,
        scheduled: bool = False,
    ) -> bool:
        """Rich end-of-day summary.

        ``scheduled=True`` marks the launchd-driven 16:30 ET send. It
        bypasses the manual-send cooldown (the scheduler fires at most
        once per day by construction, and a stray manual test send must
        never suppress the real delivery) and is recorded in the
        delivery log as scheduled evidence for the W1 streak.
        """
        if not scheduled and self._is_on_cooldown(AlertTier.DAILY_SUMMARY):
            return False

        emoji = HEAT_EMOJI.get(heat_score.level, "?")
        c = heat_score.components
        regime = regime_state.get("regime", "unknown") if regime_state else "unknown"
        regime_conf = regime_state.get("confidence", 0) if regime_state else 0

        msg = f"""{emoji} <b>DAILY RISK SUMMARY</b>
{datetime.now(ET).strftime('%Y-%m-%d %H:%M ET')}

<b>Heat Score:</b> {heat_score.score:.3f} ({heat_score.level.value.upper()})
<b>Regime:</b> {regime.upper()} (confidence: {regime_conf:.0%})

<b>Components:</b>
  Absorption Ratio: {c.absorption_ratio:.3f}
  Turbulence: {c.turbulence:.1f} (P{c.turbulence_percentile:.0%})
  Diversification: {c.diversification_ratio:.2f} (P{c.diversification_percentile:.0%})
  Factor HHI: {c.factor_hhi:.3f} (P{c.factor_hhi_percentile:.0%})
  Avg Correlation: {c.avg_correlation:.3f} (P{c.avg_correlation_percentile:.0%})"""

        # Top 3 contributors
        if attribution:
            msg += "\n\n<b>Top Contributors:</b>"
            for a in attribution[:3]:
                msg += f"\n  {a.symbol}: {a.heat_share:.0%} heat share ({a.recommendation})"

        # Recommendations summary
        if recommendations and recommendations.actions:
            msg += f"\n\n<b>Recommendations:</b> {recommendations.summary}"
            for act in recommendations.actions[:3]:
                msg += f"\n  {act.urgency.upper()}: {act.action} {act.symbol} ({act.reason})"

        # Data quality
        if data_quality:
            source = data_quality.get("primary_source", "unknown")
            freshness = data_quality.get("data_age_seconds", 0)
            msg += f"\n\n<b>Data:</b> {source} | Age: {freshness:.0f}s"

        if await self._send_telegram(msg):
            self._mark_sent(AlertTier.DAILY_SUMMARY)
            self._record_alert(AlertTier.DAILY_SUMMARY, heat_score.score)
            if self.delivery_log_path:
                from src.delivery_log import append_delivery

                append_delivery(
                    self.delivery_log_path,
                    tier=AlertTier.DAILY_SUMMARY.value,
                    message_id=self.last_message_id,
                    heat_score=heat_score.score,
                    scheduled=scheduled,
                )
            return True
        return False

    def format_level_change_alert(
        self,
        heat: HeatScore,
        previous_level: HeatLevel,
        attribution: list[PositionAttribution] | None,
    ) -> str:
        """Format alert for heat level transition."""
        emoji = HEAT_EMOJI.get(heat.level, "?")
        prev_emoji = HEAT_EMOJI.get(previous_level, "?")

        direction = "UP" if LEVEL_SEVERITY.get(heat.level, 0) > LEVEL_SEVERITY.get(previous_level, 0) else "DOWN"
        arrow = "\u2b06\ufe0f" if direction == "UP" else "\u2b07\ufe0f"

        msg = f"""{arrow} <b>HEAT LEVEL CHANGE</b>

{prev_emoji} {previous_level.value.upper()} -> {emoji} {heat.level.value.upper()}
<b>Score:</b> {heat.score:.3f}

<b>Dominant Factor:</b> {heat.dominant_factor or 'N/A'}
<b>Top Correlated Pair:</b> {heat.top_correlated_pair or 'N/A'}"""

        if attribution:
            msg += "\n\n<b>Top Contributors:</b>"
            for a in attribution[:3]:
                msg += f"\n  {a.symbol} ({a.weight:.0%}): {a.heat_share:.0%} heat | {a.dominant_factor} beta={a.dominant_factor_beta:.2f}"

        msg += f"\n\n<b>Action:</b> {heat.action}"
        return msg

    def format_regime_shift_alert(
        self,
        old_regime: str,
        new_regime: str,
        confidence: float,
    ) -> str:
        """Format alert for regime transition."""
        regime_emoji = {
            "low_vol": "\U0001f7e2",
            "normal": "\U0001f7e1",
            "crisis": "\U0001f534",
        }

        old_em = regime_emoji.get(old_regime, "\u2753")
        new_em = regime_emoji.get(new_regime, "\u2753")

        msg = f"""\U0001f504 <b>REGIME SHIFT DETECTED</b>

{old_em} {old_regime.upper()} -> {new_em} {new_regime.upper()}
<b>Confidence:</b> {confidence:.0%}

<b>Implications:</b>"""

        if new_regime == "crisis":
            msg += """
  - Reduce gross exposure immediately
  - Tighten stop losses
  - Consider tail hedges (puts, VIX calls)
  - Expect correlation convergence"""
        elif new_regime == "low_vol":
            msg += """
  - Favorable for carry strategies
  - Can increase position sizes cautiously
  - Watch for complacency / vol compression"""
        else:
            msg += """
  - Standard risk management applies
  - Monitor for escalation signals"""

        return msg

    def format_emergency_alert(
        self,
        heat: HeatScore,
        attribution: list[PositionAttribution] | None,
        recommendations: PortfolioRecommendation | None,
    ) -> str:
        """Format emergency alert with immediate action items."""
        msg = f"""\u26a0\ufe0f\u26a0\ufe0f <b>EMERGENCY RISK ALERT</b> \u26a0\ufe0f\u26a0\ufe0f

<b>Heat Score:</b> {heat.score:.3f} (EMERGENCY)
<b>Timestamp:</b> {datetime.now(timezone.utc).strftime('%H:%M:%S UTC')}

<b>IMMEDIATE ACTIONS REQUIRED:</b>"""

        if recommendations and recommendations.actions:
            for i, act in enumerate(recommendations.actions[:5], 1):
                msg += f"\n  {i}. {act.action.upper()} {act.symbol}: {act.current_weight:.0%} -> {act.target_weight:.0%} ({act.reason})"
        else:
            msg += "\n  1. Reduce largest positions by 50%"
            msg += "\n  2. Add tail hedges (VIX calls, SPY puts)"
            msg += "\n  3. Halt all new entries"

        if attribution:
            msg += "\n\n<b>Biggest Risk Contributors:</b>"
            for a in attribution[:3]:
                msg += f"\n  {a.symbol}: {a.heat_share:.0%} of total heat"

        if recommendations and recommendations.hedges:
            msg += "\n\n<b>Hedge Recommendations:</b>"
            for h in recommendations.hedges[:3]:
                msg += f"\n  BUY {h.symbol} @ {h.target_weight:.0%}"

        return msg

    async def _send_telegram(self, message: str) -> bool:
        """Send a message via Telegram Bot API."""
        if not self.bot_token or not self.chat_id:
            logger.debug("Telegram not configured — alert suppressed")
            return False

        try:
            async with httpx.AsyncClient() as client:
                payload = {
                    "chat_id": self.chat_id,
                    "text": message,
                    "parse_mode": "HTML",
                }
                if self.thread_id is not None:
                    payload["message_thread_id"] = self.thread_id

                resp = await client.post(
                    f"{self._base_url}/sendMessage",
                    json=payload,
                    timeout=10.0,
                )
                if resp.status_code == 200:
                    body = resp.json()
                    self.last_message_id = body.get("result", {}).get("message_id")
                    logger.info("Alert sent via Telegram")
                    return True
                else:
                    logger.error("Telegram API error %d: %s", resp.status_code, resp.text)
                    return False
        except Exception as e:
            logger.error("Failed to send Telegram alert: %s", e)
            return False

    def _record_alert(self, tier: AlertTier, heat_score: float) -> None:
        """Record alert in history (for dashboard display)."""
        self.alert_history.append({
            "tier": tier.value,
            "heat_score": heat_score,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        })
        # Keep last 100 alerts
        if len(self.alert_history) > 100:
            self.alert_history = self.alert_history[-100:]

    def get_alert_history(self) -> list[dict]:
        """Return recent alert history."""
        return list(self.alert_history)

    def get_cooldown_status(self) -> dict[str, dict]:
        """Return cooldown status for each tier."""
        now = time.time()
        status = {}
        for tier in AlertTier:
            cooldown_min = self._cooldowns.get(tier.value, 60)
            last_sent = self._last_sent.get(tier.value, 0)
            elapsed = (now - last_sent) / 60 if last_sent > 0 else float("inf")
            remaining = max(0, cooldown_min - elapsed)
            status[tier.value] = {
                "cooldown_minutes": cooldown_min,
                "on_cooldown": remaining > 0 and last_sent > 0,
                "remaining_minutes": round(remaining, 1),
            }
        return status
