#!/usr/bin/env python
"""Report the daily-summary delivery streak from the delivery log.

Read-only evidence for W1: prints one JSON object with the count of
consecutive ET days whose 16:30 ET scheduled summary was delivered by
the launchd service, plus the last delivered date and message ids.

Usage:
    .venv/bin/python scripts/delivery_streak.py
    RISKRADAR_DELIVERY_LOG=/path/to/log.jsonl .venv/bin/python scripts/delivery_streak.py
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from src.delivery_log import daily_streak, read_deliveries  # noqa: E402


def main() -> int:
    log_path = os.environ.get(
        "RISKRADAR_DELIVERY_LOG",
        str(PROJECT_ROOT / "data" / "delivery_log.jsonl"),
    )

    streak = daily_streak(log_path)
    records = [
        r
        for r in read_deliveries(log_path)
        if r.get("tier") == "daily_summary"
    ]
    scheduled = [r for r in records if r.get("scheduled")]
    last_scheduled = scheduled[-1] if scheduled else None

    print(
        json.dumps(
            {
                "log_path": log_path,
                "streak_days": streak["streak"],
                "last_delivered": streak["last_date"],
                "live": streak["live"],
                "scheduled_deliveries": len(scheduled),
                "manual_deliveries": len(records) - len(scheduled),
                "last_scheduled_message_id": (last_scheduled or {}).get("message_id"),
            }
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
