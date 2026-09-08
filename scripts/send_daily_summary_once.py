#!/usr/bin/env python3
"""Compute current risk through the production path and send one daily summary."""

from __future__ import annotations

import asyncio
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.alerts.alert_manager import AlertManager
from src.config import Settings
from src.main import compute_heat_loop, state
from src.models import Position


async def main() -> None:
    settings = Settings.from_yaml()
    if not settings.telegram_bot_token or not settings.telegram_chat_id:
        raise RuntimeError("Telegram environment is not configured")
    if settings.telegram_thread_id is None:
        raise RuntimeError("TELEGRAM_THREAD_ID is required for the one-time send")

    state["positions"] = [Position(**position) for position in settings.portfolio_positions]
    state["last_heat_score"] = None
    state["_previous_heat_score"] = None

    silent_settings = settings.model_copy(
        update={"telegram_bot_token": "", "telegram_chat_id": "", "telegram_thread_id": None}
    )
    compute_task = asyncio.create_task(compute_heat_loop(silent_settings))
    deadline = time.monotonic() + 420
    try:
        while state.get("_previous_heat_score") is None:
            if compute_task.done():
                await compute_task
            if time.monotonic() >= deadline:
                raise TimeoutError("current heat computation did not finish within 420 seconds")
            await asyncio.sleep(1)
    finally:
        compute_task.cancel()
        try:
            await compute_task
        except asyncio.CancelledError:
            pass

    manager = AlertManager(
        telegram_bot_token=settings.telegram_bot_token,
        telegram_chat_id=settings.telegram_chat_id,
        telegram_thread_id=settings.telegram_thread_id,
    )
    sent = await manager.send_daily_summary(
        heat_score=state["last_heat_score"],
        regime_state=state.get("regime_state"),
        attribution=state.get("attribution"),
        recommendations=state.get("recommendations"),
        data_quality=state.get("data_quality"),
    )
    if not sent or manager.last_message_id is None:
        raise RuntimeError("Telegram did not confirm the daily summary")
    print(f"telegram_message_id={manager.last_message_id}")


if __name__ == "__main__":
    asyncio.run(main())
