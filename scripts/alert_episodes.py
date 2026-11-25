#!/usr/bin/env python
"""Report the alert-tier episode state from the delivery log.

Reconstructs warm-episode open/close pairs from the ``alert_level_change``
delivery records (each carries the heat score at the moment of the
transition) and cross-checks the last episode against the live /heat
level from the running service. Prints one JSON object.

Why this exists: the nightly PROGRESS entries for 2026-11-23/24 claimed
"live /heat corroborates 0.363/0.366 cool, no new broadcasts since 6917"
while the service was continuously warm (0.58/0.62) with episode #4 open
since 2026-11-21 (message 7355) — hand-copied numbers nothing in the
repo could catch. The nightly runner should quote THIS verdict, never a
hand-copied score.

Usage:
    .venv/bin/python scripts/alert_episodes.py
    RISKRADAR_DELIVERY_LOG=/path/to/log.jsonl .venv/bin/python scripts/alert_episodes.py
    RISKRADAR_BASE_URL=http://127.0.0.1:8001 .venv/bin/python scripts/alert_episodes.py
"""

from __future__ import annotations

import json
import os
import sys
import urllib.request
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from src.delivery_log import alert_episodes  # noqa: E402


def _live_level(base_url: str, timeout: float = 2.0) -> dict | None:
    """Best-effort live /heat read: {'score': float, 'level': str} or None."""
    try:
        with urllib.request.urlopen(f"{base_url}/heat", timeout=timeout) as resp:
            payload = json.loads(resp.read().decode("utf-8"))
        return {"score": payload.get("score"), "level": payload.get("level")}
    except Exception:  # noqa: BLE001 - evidence tool, never crashes the night
        return None


def main() -> int:
    log_path = os.environ.get(
        "RISKRADAR_DELIVERY_LOG",
        str(PROJECT_ROOT / "data" / "delivery_log.jsonl"),
    )
    base_url = os.environ.get("RISKRADAR_BASE_URL", "http://127.0.0.1:8001").rstrip("/")

    live = _live_level(base_url)
    verdict = alert_episodes(
        log_path,
        current_level=live.get("level") if live else None,
    )
    verdict["live_heat"] = live
    print(json.dumps(verdict, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
