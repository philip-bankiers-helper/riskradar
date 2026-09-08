"""Telegram alert sender for heat score notifications."""

from __future__ import annotations

import logging

import httpx

from src.models import HeatLevel, HeatScore

logger = logging.getLogger(__name__)
# Never allow credential-bearing Bot API URLs into application logs.
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("httpcore").setLevel(logging.WARNING)

HEAT_EMOJI = {
    HeatLevel.COOL: "🟢",
    HeatLevel.WARM: "🟡",
    HeatLevel.HOT: "🟠",
    HeatLevel.CRITICAL: "🔴",
    HeatLevel.EMERGENCY: "⛔",
}


class TelegramAlerter:
    """Send alerts via Telegram Bot API."""

    def __init__(self, bot_token: str, chat_id: str, thread_id: int | None = None):
        self.bot_token = bot_token
        self.chat_id = chat_id
        self.thread_id = thread_id
        self._base_url = f"https://api.telegram.org/bot{bot_token}"
        self._last_level: HeatLevel | None = None

    def should_alert(self, heat: HeatScore) -> bool:
        """Determine if an alert should be sent (level change or first reading)."""
        if self._last_level is None:
            self._last_level = heat.level
            return heat.level != HeatLevel.COOL  # Don't alert on cool

        changed = heat.level != self._last_level
        self._last_level = heat.level
        return changed

    def format_message(self, heat: HeatScore) -> str:
        """Format a heat score into a Telegram message."""
        emoji = HEAT_EMOJI.get(heat.level, "❓")
        c = heat.components

        msg = f"""{emoji} <b>RISK RADAR ALERT</b>

<b>Heat Score:</b> {heat.score:.2f} ({heat.level.value.upper()})

<b>Components:</b>
  📊 Absorption Ratio: {c.absorption_ratio:.3f}
  🌊 Turbulence: {c.turbulence:.1f} (P{c.turbulence_percentile:.0%})
  🎯 Diversification Ratio: {c.diversification_ratio:.2f} (P{c.diversification_percentile:.0%})
  📦 Factor HHI: {c.factor_hhi:.3f} (P{c.factor_hhi_percentile:.0%})
  🔗 Avg Correlation: {c.avg_correlation:.3f} (P{c.avg_correlation_percentile:.0%})

<b>Dominant Factor:</b> {heat.dominant_factor or 'N/A'}
<b>Top Correlated Pair:</b> {heat.top_correlated_pair or 'N/A'}

<b>Action:</b> {heat.action}"""

        return msg

    async def send_alert(self, heat: HeatScore) -> bool:
        """Send a heat score alert to Telegram."""
        if not self.bot_token or not self.chat_id:
            logger.warning("Telegram not configured — skipping alert")
            return False

        message = self.format_message(heat)

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
                    logger.info("Telegram alert sent: heat=%.2f level=%s", heat.score, heat.level.value)
                    return True
                else:
                    logger.error("Telegram API error %d: %s", resp.status_code, resp.text)
                    return False
        except Exception as e:
            logger.error("Failed to send Telegram alert: %s", e)
            return False
