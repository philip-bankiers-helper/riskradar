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
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

logger = logging.getLogger(__name__)

ET = ZoneInfo("America/New_York")
DAILY_SUMMARY = "daily_summary"

# Mirrors WEEKLY_SCORECARD_TIER in src.engine.weekly_scorecard —
# duplicated so this leaf module stays pandas-free; pinned equal by test.
WEEKLY_SCORECARD = "weekly_scorecard"

# First Monday 16:30 ET slot the cadence code (4688bdc, deployed
# 2026-09-22 after that week's Monday had passed) was armed for.
# Weeks before it were never expected to fire and never read as misses.
FIRST_WEEKLY_MONDAY = date(2026, 9, 28)


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
    positions: list[str] | None = None,
    top_attributions: list[tuple[str, float]] | None = None,
    action: str | None = None,
    now: datetime | None = None,
) -> bool:
    """Append one delivery record. Never raises into the alert path.

    ``telegram_date`` is Telegram's server-side send timestamp (unix
    seconds) echoed by the Bot API alongside ``message_id``. Storing
    it makes each record self-corroborating: the ET day key can be
    re-derived from Telegram's own clock, not just the local one.

    The W2 evidence fields (``positions`` = the scored book's symbols,
    ``top_attributions`` = top-3 (symbol, heat_share) pairs, ``action``
    = the action line the summary carried) are recorded only when
    supplied, so non-daily tiers keep their schema. They let
    ``w2_status`` verify mechanically that holdings drove the score and
    every summary carried attributions and an action — the same
    self-evidence pattern as ``telegram_date``.
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
        if positions is not None:
            record["positions"] = list(positions)
        if top_attributions is not None:
            record["top_attributions"] = [
                [symbol, float(share)] for symbol, share in top_attributions
            ]
        if action is not None:
            record["action"] = str(action)
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


W1_REQUIRED_DAYS = 7


def w1_status(
    path: str | Path,
    *,
    today: date | None = None,
    required: int = W1_REQUIRED_DAYS,
) -> dict:
    """Mechanical verdict for the W1 done_when.

    W1 is met when ``required`` consecutive ET days each have a scheduled
    16:30 ET daily summary, the streak is still live, and no record's ET
    day disagrees with Telegram's server clock. Records predating the
    corroboration feature count toward the streak (reported as
    ``missing``, never as failures); only an actual clock disagreement
    blocks the verdict.

    Returns the verdict plus a ready-to-paste ``evidence`` line (day
    range, per-day message ids oldest→newest, corroboration counts) so
    the ROADMAP flip quotes assembled evidence instead of hand-copied
    numbers.
    """
    all_records = read_deliveries(path)
    streak = daily_streak(path, today=today)
    corroboration = verify_corroboration(all_records)

    by_day: dict[date, int | None] = {}
    for rec in all_records:
        if rec.get("tier") != DAILY_SUMMARY or not rec.get("scheduled", False):
            continue
        try:
            by_day[date.fromisoformat(rec["et_date"])] = rec.get("message_id")
        except (KeyError, ValueError):
            continue

    window: list[tuple[date, int | None]] = []
    last: date | None = None
    if streak["last_date"]:
        try:
            last = date.fromisoformat(streak["last_date"])
        except ValueError:
            last = None
    if last is not None:
        cursor = last
        while cursor in by_day:
            window.append((cursor, by_day[cursor]))
            cursor -= timedelta(days=1)
        window.reverse()

    reasons: list[str] = []
    if streak["streak"] < required:
        reasons.append(f"streak {streak['streak']}/{required}")
    if not streak["live"]:
        reasons.append("streak not live")
    if corroboration["mismatched"]:
        reasons.append(f"{corroboration['mismatched']} clock mismatch(es)")

    evidence = ""
    if window:
        ids = ",".join(str(mid) for _, mid in window)
        evidence = (
            f"{window[0][0].isoformat()}..{window[-1][0].isoformat()} "
            f"messages {ids}; "
            f"corroborated={corroboration['corroborated']} "
            f"missing={corroboration['missing']} "
            f"mismatched={corroboration['mismatched']}"
        )

    return {
        "w1_met": not reasons,
        "required_days": required,
        "streak_days": streak["streak"],
        "live": streak["live"],
        "days_remaining": max(0, required - streak["streak"]),
        "reasons": reasons,
        "evidence": evidence,
    }


def verify_corroboration(
    records: list[dict],
    *,
    tier: str = DAILY_SUMMARY,
) -> dict:
    """Cross-check delivery records against Telegram's server clock.

    For every record of ``tier`` that carries a ``telegram_date``
    (unix seconds echoed by the Bot API at send time), re-derive the ET
    calendar day and compare it to the locally recorded ``et_date``. A
    match means two independent clocks (local and Telegram's server)
    agree the delivery landed on that ET day — evidence that does not
    rely solely on the Mac Studio's clock.

    Records without ``telegram_date`` (pre-feature history) are counted
    as ``missing`` and excluded from the verdict.
    """
    checked = 0
    corroborated = 0
    missing = 0
    mismatches: list[dict] = []

    for rec in records:
        if rec.get("tier") != tier:
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


# ── W3: weekly scorecard verdict ──


def _iso_week_key(day: date) -> tuple[int, int]:
    """(ISO year, ISO week) — the week identity a Monday slot owns."""
    iso = day.isocalendar()
    return iso[0], iso[1]


def due_week_monday(now: datetime | None = None) -> date:
    """Monday of the week whose 16:30 ET scorecard slot has most recently fired.

    Mirrors ``is_weekly_scorecard_slot`` semantics: a Monday slot counts
    for its week regardless of time-of-day, and on Monday itself the
    slot only becomes due after 16:30 ET (before that, the previous
    Monday's week is still the one under judgment).
    """
    moment = _et_now(now)
    today = moment.date()
    candidate = today - timedelta(days=today.weekday())
    if today.weekday() == 0 and moment.time() < time(16, 30):
        candidate -= timedelta(days=7)
    return candidate


def w3_status(
    path: str | Path,
    *,
    now: datetime | None = None,
    first_expected: date | None = None,
) -> dict:
    """Mechanical verdict for the W3 weekly-post delivery leg.

    W3's runtime proof is the automatic Monday 16:30 ET scorecard post:
    the verdict is met when the due week — the most recent Monday slot
    that should have fired, never earlier than ``first_expected`` —
    has a scheduled ``weekly_scorecard`` delivery record, and no
    record's locally recorded ET day disagrees with Telegram's server
    clock. Manual/test sends never count (same reasoning as W1: the
    cadence is the scheduler's Monday gate, so only scheduler sends are
    evidence), and a missing server timestamp is reported rather than
    blocking.

    Returns the verdict plus a ready-to-paste ``evidence`` line (week
    span, message ids, corroboration counts) so the ROADMAP flip quotes
    assembled evidence instead of hand-copied numbers.
    """
    if first_expected is None:
        first_expected = FIRST_WEEKLY_MONDAY

    slot_monday = due_week_monday(now)
    due_monday = max(slot_monday, first_expected)
    due_week = _iso_week_key(due_monday)

    weekly: list[dict] = []
    for rec in read_deliveries(path):
        if rec.get("tier") != WEEKLY_SCORECARD or not rec.get("scheduled", False):
            continue
        try:
            weekly.append(
                {
                    "day": date.fromisoformat(rec["et_date"]),
                    "message_id": rec.get("message_id"),
                    "telegram_date": rec.get("telegram_date"),
                }
            )
        except (KeyError, ValueError):
            continue

    due_records = [w for w in weekly if _iso_week_key(w["day"]) == due_week]
    corroboration = verify_corroboration(
        [
            {
                "tier": WEEKLY_SCORECARD,
                "et_date": w["day"].isoformat(),
                "message_id": w["message_id"],
                "telegram_date": w["telegram_date"],
            }
            for w in due_records
        ],
        tier=WEEKLY_SCORECARD,
    )

    reasons: list[str] = []
    if slot_monday < first_expected:
        reasons.append(
            f"not yet due; first expected weekly post {first_expected.isoformat()}"
        )
    elif not due_records:
        reasons.append(
            f"no scheduled weekly_scorecard in week of {due_monday.isoformat()}"
        )
    if corroboration["mismatched"]:
        reasons.append(f"{corroboration['mismatched']} clock mismatch(es)")

    evidence = ""
    if due_records:
        ordered = sorted(due_records, key=lambda w: (w["day"], w["message_id"] or 0))
        ids = ",".join(str(w["message_id"]) for w in ordered)
        span = (
            ordered[-1]["day"].isoformat()
            if len(ordered) == 1
            else f"{ordered[0]['day'].isoformat()}..{ordered[-1]['day'].isoformat()}"
        )
        plural = "s" if len(ordered) > 1 else ""
        evidence = (
            f"weekly_scorecard{plural} {span} "
            f"message{plural} {ids}; "
            f"corroborated={corroboration['corroborated']} "
            f"missing={corroboration['missing']} "
            f"mismatched={corroboration['mismatched']}"
        )

    last_post = None
    if weekly:
        newest = max(weekly, key=lambda w: w["day"])
        last_post = {
            "et_date": newest["day"].isoformat(),
            "message_id": newest["message_id"],
        }

    return {
        "w3_met": not reasons,
        "due_week_monday": due_monday.isoformat(),
        "delivered": bool(due_records),
        "weekly_posts": len(weekly),
        "last_post": last_post,
        "corroboration": corroboration,
        "reasons": reasons,
        "evidence": evidence,
    }


# ── W2: portfolio-driven summary verdict ──


# First 16:30 ET slot expected to carry the W2 evidence fields
# (positions / top_attributions / action). Deployed ahead of this
# slot; earlier records never carried the fields and never read as
# failures.
FIRST_W2_EVIDENCE = date(2026, 10, 1)


def _tapped_et_moment(record: dict) -> datetime | None:
    """Feedback ``tapped_at`` (ISO UTC) as an ET moment, or None."""
    raw = record.get("tapped_at")
    if not isinstance(raw, str):
        return None
    try:
        ts = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return None
    if ts.tzinfo is None:
        return None
    return ts.astimezone(ET)


def w2_status(
    path: str | Path,
    feedback_path: str | Path,
    *,
    now: datetime | None = None,
    first_expected: date | None = None,
    expected_positions: list[str] | None = None,
) -> dict:
    """Mechanical verdict for the W2 done_when.

    W2 is met when, for every scheduled daily summary since
    ``first_expected`` (the first slot that carried W2 evidence
    fields): (a) the record proves the scored book (``positions``) and
    carries top-3 ``top_attributions`` and a non-empty ``action`` —
    and, when ``expected_positions`` is supplied, the latest record's
    book equals it (holdings from the repo actually drove the score);
    and (b) Philip's tap exists in the feedback log and has been
    surfaced — a scheduled summary was delivered strictly after the
    tap (same-day sends must postdate the tap's moment, since the
    summary reads the feedback log at send time). Manual/test sends
    never count, matching w1/w3 provenance rules.

    Returns the verdict plus a ready-to-paste ``evidence`` line so the
    ROADMAP flip quotes assembled evidence instead of hand-copied
    numbers.
    """
    if first_expected is None:
        first_expected = FIRST_W2_EVIDENCE
    _et_now(now)  # validates/normalizes `now` semantics like w1/w3

    records: list[dict] = []
    for rec in read_deliveries(path):
        if rec.get("tier") != DAILY_SUMMARY or not rec.get("scheduled", False):
            continue
        try:
            if date.fromisoformat(rec["et_date"]) < first_expected:
                continue
        except (KeyError, ValueError):
            continue
        records.append(rec)

    def _carries(rec: dict) -> bool:
        return bool(
            rec.get("positions")
            and rec.get("top_attributions")
            and isinstance(rec.get("action"), str)
            and rec.get("action").strip()
        )

    payload_records = [r for r in records if _carries(r)]
    bare_records = [r for r in records if not _carries(r)]

    reasons: list[str] = []
    if not records:
        reasons.append(
            f"no scheduled daily summaries since {first_expected.isoformat()} yet"
        )
    elif bare_records:
        days = ", ".join(str(r.get("et_date")) for r in bare_records[:3])
        reasons.append(
            f"{len(bare_records)} scheduled summaries missing W2 payload ({days})"
        )

    book_match: bool | None = None
    if expected_positions is not None and payload_records:
        latest_book = payload_records[-1].get("positions") or []
        book_match = set(s.upper() for s in latest_book) == set(
            s.upper() for s in expected_positions
        )
        if not book_match:
            reasons.append("latest summary's scored book != repo positions")

    # Local import: src.feedback imports this module (ET); importing at
    # module level would be circular.
    from src.feedback import last_feedback

    try:
        fb = last_feedback(feedback_path)
    except Exception:  # noqa: BLE001 - a missing log is just "no tap yet"
        fb = None

    surfaced: dict | None = None
    vote = None
    vote_day: date | None = None
    if fb is None:
        reasons.append("waiting for Philip's first YES/NO tap")
    else:
        vote = str(fb.get("vote", "?"))
        try:
            vote_day = date.fromisoformat(str(fb["et_date"]))
        except (KeyError, ValueError):
            vote_day = None
            reasons.append("latest feedback record has no parseable et_date")
        if vote_day is not None:
            tapped = _tapped_et_moment(fb)
            for rec in payload_records:
                try:
                    day = date.fromisoformat(rec["et_date"])
                except (KeyError, ValueError):
                    continue
                later = day > vote_day
                if day == vote_day and tapped is not None:
                    try:
                        sent = datetime.strptime(rec["et_time"], "%H:%M:%S").time()
                    except (KeyError, ValueError):
                        sent = None
                    later = sent is not None and sent > tapped.time()
                if later:
                    surfaced = rec

    if fb is not None and vote_day is not None and surfaced is None:
        reasons.append(
            f"tap {vote} {vote_day.isoformat()} not yet surfaced in a summary"
        )

    evidence = ""
    if payload_records and fb is not None and surfaced is not None:
        ids = ", ".join(str(r.get("message_id")) for r in payload_records)
        span = (
            f"{payload_records[0].get('et_date')}.."
            f"{payload_records[-1].get('et_date')}"
        )
        book = (
            "book==repo"
            if book_match
            else "book==" + ",".join(payload_records[-1].get("positions") or [])
        )
        evidence = (
            f"{span} messages {ids} carrying top-3 attributions + action; "
            f"{book}; "
            f"feedback {vote} {vote_day.isoformat()} surfaced in message "
            f"{surfaced.get('message_id')}"
        )
    elif payload_records:
        ids = ", ".join(str(r.get("message_id")) for r in payload_records)
        span = (
            f"{payload_records[0].get('et_date')}.."
            f"{payload_records[-1].get('et_date')}"
        )
        evidence = (
            f"{span} messages {ids} carrying top-3 attributions + action; "
            "no tap recorded yet"
        )

    return {
        "w2_met": not reasons,
        "first_expected": first_expected.isoformat(),
        "summaries_expected": len(records),
        "summaries_with_payload": len(payload_records),
        "last_payload": (
            {
                "et_date": payload_records[-1].get("et_date"),
                "message_id": payload_records[-1].get("message_id"),
            }
            if payload_records
            else None
        ),
        "book_matches_repo": book_match,
        "feedback": (
            None if fb is None else {"vote": vote, "et_date": str(fb.get("et_date"))}
        ),
        "surfaced_message_id": (surfaced or {}).get("message_id"),
        "reasons": reasons,
        "evidence": evidence,
    }
