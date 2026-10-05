#!/usr/bin/env python
"""Backstop capture for Philip's YES/NO tap (W2).

Primary path: the RiskRadar-topic Hermes session sees Philip's one-word
reply and records it via ``scripts/record_feedback.py``. That path has a
single point of failure — if the session misses the relay rule, the tap
is lost and W2 stalls on evidence that exists but was never written.

This script is the nightly backstop. It scans the LOCAL Hermes gateway
transcript store (JSON files under ``~/.hermes/sessions``) for telegram
sessions carrying a bare ``[philip Bankier] yes|no`` user message, and
attributes the session to the RiskRadar topic (thread 4799) by joining
against the gateway log, which records the full session key
``agent:main:telegram:group:-1003820528092:4799`` whenever the topic is
active.

It NEVER talks to Telegram (the bot token is shared with the gateway —
no getUpdates, no API reads). It reads local files only. Without
``--apply`` it is a pure detector: it prints a JSON report and exits 0.

Detection rules (deliberately strict):
- platform must be "telegram"
- session_start must be on/after --since (default FIRST_W2_EVIDENCE)
- the user message must be BARE: ``[philip Bankier] yes`` (case/punct
  tolerant). Anything with extra words is a near-miss, reported but
  never recorded.
- a candidate is only "topic_verified" when the gateway log shows
  thread-4799 activity within +/- 1 day of the transcript's mtime.
- with --apply, only the NEWEST topic_verified candidate is recorded,
  and only if the feedback log has no record on/after its date.
"""

from __future__ import annotations

import argparse
import json
import logging
import re
import sys
from datetime import date, datetime, timedelta
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from src.delivery_log import FIRST_W2_EVIDENCE  # noqa: E402
from src.feedback import append_feedback, read_feedback  # noqa: E402

logger = logging.getLogger(__name__)

DEFAULT_SESSIONS_DIR = Path.home() / ".hermes" / "sessions"
DEFAULT_GATEWAY_LOG = Path.home() / ".hermes" / "logs" / "agent.log"
DEFAULT_FEEDBACK_LOG = REPO_ROOT / "data" / "feedback_log.jsonl"

DEFAULT_SENDER = "philip Bankier"
TOPIC_LOG_KEY = "group:-1003820528092:4799"
TOPIC_LOG_SLACK = timedelta(days=1)

BARE_VOTE_RE = re.compile(r"^\[(?P<sender>[^\]]+)\]\s*(?P<vote>yes|no)[\s.!]*$", re.IGNORECASE)
LEADING_VOTE_RE = re.compile(r"^\[[^\]]+\]\s*(yes|no)\b", re.IGNORECASE)
LOG_TS_RE = re.compile(r"^(?P<ts>\d{4}-\d{2}-\d{2}) \d{2}:\d{2}:\d{2}")


def _parse_session_dt(value: object) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return None


def scan_sessions(
    sessions_dir: str | Path,
    *,
    since: date,
    sender: str = DEFAULT_SENDER,
) -> tuple[list[dict], list[dict]]:
    """Return (candidates, near_misses) from telegram transcripts.

    A candidate is a bare yes/no from ``sender``; a near-miss is any
    user message that starts with yes/no but carries extra words.
    """
    sessions_dir = Path(sessions_dir)
    candidates: list[dict] = []
    near_misses: list[dict] = []
    if not sessions_dir.is_dir():
        return candidates, near_misses

    for path in sorted(sessions_dir.glob("session_*.json")):
        try:
            data = json.loads(path.read_text(encoding="utf-8", errors="replace"))
        except (json.JSONDecodeError, OSError) as exc:
            logger.warning("Skipping unreadable session file %s: %s", path, exc)
            continue
        if data.get("platform") != "telegram":
            continue
        started = _parse_session_dt(data.get("session_start"))
        if started is None or started.date() < since:
            continue
        for index, message in enumerate(data.get("messages", [])):
            if not isinstance(message, dict) or message.get("role") != "user":
                continue
            content = message.get("content")
            if not isinstance(content, str):
                continue
            text = content.strip()
            bare = BARE_VOTE_RE.match(text)
            sender_ok = (
                bare is not None and bare.group("sender").strip().lower() == sender.lower()
            ) or text.lower().startswith(f"[{sender.lower()}]")
            if bare is not None and sender_ok:
                candidates.append(
                    {
                        "session_id": str(data.get("session_id", path.stem)),
                        "file": path.name,
                        "mtime": datetime.fromtimestamp(path.stat().st_mtime),
                        "vote": bare.group("vote").lower(),
                        "message_index": index,
                        "text": text,
                    }
                )
            elif sender_ok and LEADING_VOTE_RE.match(text):
                near_misses.append(
                    {
                        "session_id": str(data.get("session_id", path.stem)),
                        "file": path.name,
                        "text": text[:120],
                    }
                )
    candidates.sort(key=lambda c: (c["mtime"], c["session_id"], c["message_index"]))
    return candidates, near_misses


def topic_active_dates(gateway_log: str | Path) -> set[date]:
    """Dates (local) on which the gateway log shows thread-4799 activity."""
    gateway_log = Path(gateway_log)
    dates: set[date] = set()
    if not gateway_log.is_file():
        return dates
    try:
        lines = gateway_log.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError as exc:
        logger.warning("Cannot read gateway log %s: %s", gateway_log, exc)
        return dates
    for line in lines:
        if TOPIC_LOG_KEY not in line:
            continue
        match = LOG_TS_RE.match(line)
        if match:
            try:
                dates.add(date.fromisoformat(match.group("ts")))
            except ValueError:
                continue
    return dates


def _topic_verified(candidate: dict, active_dates: set[date]) -> bool:
    """True when the gateway log shows 4799 activity near the transcript."""
    day = candidate["mtime"].date()
    return any(
        (day - TOPIC_LOG_SLACK) <= active_day <= (day + TOPIC_LOG_SLACK)
        for active_day in active_dates
    )


def run_check(
    *,
    sessions_dir: str | Path = DEFAULT_SESSIONS_DIR,
    gateway_log: str | Path = DEFAULT_GATEWAY_LOG,
    feedback_log: str | Path = DEFAULT_FEEDBACK_LOG,
    since: date | None = None,
    sender: str = DEFAULT_SENDER,
    apply: bool = False,
) -> dict:
    """Full check; returns the report dict (and records when apply=True)."""
    since = since or FIRST_W2_EVIDENCE
    candidates, near_misses = scan_sessions(sessions_dir, since=since, sender=sender)
    active_dates = topic_active_dates(gateway_log)
    verified = [dict(c, topic_verified=True) for c in candidates if _topic_verified(c, active_dates)]
    feedback = read_feedback(feedback_log)
    last = feedback[-1] if feedback else None
    last_day = date.fromisoformat(last["tapped_at"][:10]) if last else None

    report: dict = {
        "since": since.isoformat(),
        "candidates": [
            {**c, "mtime": c["mtime"].isoformat()} for c in candidates
        ],
        "near_misses": near_misses,
        "topic_verified": len(verified),
        "feedback_last": last,
        "recorded": None,
        "reason": "",
    }

    if not candidates:
        report["reason"] = "no bare yes/no from sender in telegram transcripts since " + since.isoformat()
        return report
    if not verified:
        report["reason"] = (
            "candidates found but none topic-verified against gateway log; "
            "inspect manually before recording"
        )
        return report

    newest = verified[-1]
    newest_day = newest["mtime"].date()
    if last_day is not None and last_day >= newest_day:
        report["reason"] = (
            f"feedback log already has a record on/after {newest_day}; latest tap wins, nothing to do"
        )
        return report
    if not apply:
        report["reason"] = (
            f"would record vote={newest['vote']} from session {newest['session_id']} "
            f"({newest_day}); rerun with --apply"
        )
        return report

    record = append_feedback(
        feedback_log,
        vote=newest["vote"],
        source=f"transcript-relay:{newest['session_id']}",
    )
    report["recorded"] = record
    report["reason"] = f"recorded vote={record['vote']} via transcript backstop"
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--sessions-dir", default=str(DEFAULT_SESSIONS_DIR))
    parser.add_argument("--gateway-log", default=str(DEFAULT_GATEWAY_LOG))
    parser.add_argument("--feedback-log", default=str(DEFAULT_FEEDBACK_LOG))
    parser.add_argument("--since", default=FIRST_W2_EVIDENCE.isoformat())
    parser.add_argument("--sender", default=DEFAULT_SENDER)
    parser.add_argument(
        "--apply",
        action="store_true",
        help="record the newest topic-verified tap (default: report only)",
    )
    args = parser.parse_args(argv)
    try:
        since = date.fromisoformat(args.since)
    except ValueError:
        parser.error(f"--since must be YYYY-MM-DD, got {args.since!r}")
    report = run_check(
        sessions_dir=args.sessions_dir,
        gateway_log=args.gateway_log,
        feedback_log=args.feedback_log,
        since=since,
        sender=args.sender,
        apply=args.apply,
    )
    print(json.dumps(report, indent=2))
    # The backstop never fails the nightly run: a quiet report (no tap
    # found) is a successful check, and a refused --apply is reported in
    # the JSON reason field for the operator to read.
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
