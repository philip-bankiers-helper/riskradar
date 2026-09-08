"""Persistent delivery evidence for daily summaries.

W1 requires 7 consecutive dated 16:30 ET summaries delivered by the
launchd service. Logs and in-memory state die with the process; this
module writes an append-only JSONL audit trail that survives restarts
and can be counted by ``scripts/delivery_streak.py``.

Day keys are America/New_York calendar dates: the 16:30 ET summary of a
US trading day must land on that day's key, and nothing else does.
"""

from __future__ import annotations

import json
import logging
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

logger = logging.getLogger(__name__)

ET = ZoneInfo("America/New_York")
DAILY_SUMMARY = "daily_summary"


def _et_now(now: datetime | None) -> datetime:
    """Return *now* as an ET-localized datetime (converted if naive/other tz)."""
    if now is None:
        return datetime.now(ET)
    if now.tzinfo is None:
        return now.replace(tzinfo=ET)
    return now.astimezone(ET)


def append_delivery(
    path: str | Path,
    *,
    tier: str,
    message_id: int | None = None,
    heat_score: float | None = None,
    scheduled: bool = False,
    now: datetime | None = None,
) -> bool:
    """Append one delivery record. Never raises into the alert path."""
    try:
        moment = _et_now(now)
        record = {
            "tier": tier,
            "et_date": moment.date().isoformat(),
            "et_time": moment.strftime("%H:%M:%S"),
            "utc": datetime.now(timezone.utc).isoformat()
            if now is None
            else moment.astimezone(timezone.utc).isoformat(),
            "scheduled": bool(scheduled),
            "message_id": message_id,
            "heat_score": heat_score,
        }
        target = Path(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        with target.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(record) + "\n")
        return True
    except Exception as exc:  # noqa: BLE001 - evidence must never break delivery
        logger.error("Failed to append delivery log: %s", exc)
        return False


def read_deliveries(path: str | Path) -> list[dict]:
    """Read all delivery records; skips corrupt lines."""
    target = Path(path)
    if not target.exists():
        return []
    records: list[dict] = []
    with target.open("r", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                records.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return records


def daily_streak(
    path: str | Path,
    *,
    today: date | None = None,
    scheduled_only: bool = True,
) -> dict:
    """Count consecutive ET days with a delivered daily summary.

    The streak counts backward from the most recent delivered day and is
    only considered live if that day is today or yesterday (before 16:30
    ET today, yesterday's delivery is still the current one). A gap of
    one or more missed days resets the streak to the days delivered up
    to the gap. ``scheduled_only`` restricts counting to summaries fired
    by the 16:30 ET scheduler, not manual test sends.
    """
    records = read_deliveries(path)
    days: set[date] = set()
    for rec in records:
        if rec.get("tier") != DAILY_SUMMARY:
            continue
        if scheduled_only and not rec.get("scheduled", False):
            continue
        try:
            days.add(date.fromisoformat(rec["et_date"]))
        except (KeyError, ValueError):
            continue

    et_today = _et_now(None).date() if today is None else today

    if not days:
        return {
            "streak": 0,
            "last_date": None,
            "live": False,
            "delivered_days": 0,
        }

    last = max(days)
    # The streak is live while the most recent delivery was today (after
    # 16:30 ET) or yesterday (before today's 16:30 ET slot).
    live = last >= et_today - timedelta(days=1)

    streak = 0
    cursor = last
    while cursor in days:
        streak += 1
        cursor -= timedelta(days=1)

    return {
        "streak": streak,
        "last_date": last.isoformat(),
        "live": live,
        "delivered_days": len(days),
    }
