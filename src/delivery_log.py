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
    telegram_date: int | None = None,
    now: datetime | None = None,
) -> bool:
    """Append one delivery record. Never raises into the alert path.

    ``telegram_date`` is Telegram's server-side send timestamp (unix
    seconds) echoed by the Bot API alongside ``message_id``. Storing
    it makes each record self-corroborating: the ET day key can be
    re-derived from Telegram's own clock, not just the local one.
    """
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
            "telegram_date": telegram_date,
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


def verify_corroboration(records: list[dict]) -> dict:
    """Cross-check each delivery record against Telegram's server clock.

    For every daily-summary record that carries a ``telegram_date``
    (unix seconds echoed by the Bot API at send time), re-derive the ET
    calendar day and compare it to the locally recorded ``et_date``. A
    match means two independent clocks (local and Telegram's server)
    agree the summary was delivered on that ET day — evidence that does
    not rely solely on the Mac Studio's clock.

    Records without ``telegram_date`` (pre-feature history) are counted
    as ``missing`` and excluded from the verdict.
    """
    checked = 0
    corroborated = 0
    missing = 0
    mismatches: list[dict] = []

    for rec in records:
        if rec.get("tier") != DAILY_SUMMARY:
            continue
        tg_date = rec.get("telegram_date")
        if tg_date is None:
            missing += 1
            continue
        try:
            tg_et_day = datetime.fromtimestamp(int(tg_date), tz=timezone.utc)
            tg_et_day = tg_et_day.astimezone(ET).date().isoformat()
        except (TypeError, ValueError, OSError, OverflowError):
            missing += 1
            continue
        checked += 1
        if tg_et_day == rec.get("et_date"):
            corroborated += 1
        else:
            mismatches.append(
                {
                    "message_id": rec.get("message_id"),
                    "et_date": rec.get("et_date"),
                    "telegram_et_date": tg_et_day,
                }
            )

    return {
        "checked": checked,
        "corroborated": corroborated,
        "mismatched": len(mismatches),
        "missing": missing,
        "mismatches": mismatches,
    }
