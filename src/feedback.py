"""Yes/no feedback on the daily summary (W2).

Philip answers "was this summary useful?" with a one-word reply
(YES / NO) in the RiskRadar topic; the Manager session for that topic
(or any local caller) records the verdict through the local API or
``scripts/record_feedback.py``. Records land in a JSONL log and the
latest one is surfaced in the next daily summary, closing the loop.

Design constraint (found 2026-09-29): the Telegram bot token is
``kairox_manager_bot`` — the SAME bot the Hermes gateway uses. A
second ``getUpdates`` consumer would fight the gateway for updates
(409 conflicts, stolen messages), so this module deliberately has NO
Telegram read path at all. The "tap" is a one-word topic reply relayed
by the Manager session; true inline buttons would require a dedicated
bot token, which only Philip can create.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path

from src.delivery_log import ET

logger = logging.getLogger(__name__)

VALID_VOTES = ("yes", "no")

# A bare thumbs emoji is as unambiguous a tap as a bare word on Telegram
# (arguably the more likely form), so both are first-class votes. The
# canonical record format stays the word — downstream readers (summary
# render, w2_status) never see the emoji form.
THUMBS_UP = "\U0001f44d"
THUMBS_DOWN = "\U0001f44e"
EMOJI_VOTES = {THUMBS_UP: "yes", THUMBS_DOWN: "no"}
# Variation selector + skin-tone modifiers that may trail the base emoji
# in a bare tap ("👍️", "👍🏽") without changing its meaning.
_EMOJI_MODIFIERS = frozenset("\ufe0f\U0001f3fb\U0001f3fc\U0001f3fd\U0001f3fe\U0001f3ff")


def normalize_vote(value: str) -> str:
    """Validate and canonicalize a vote to 'yes' or 'no'.

    Accepts the words yes/no (case-insensitive) or a bare thumbs
    up/down emoji (with optional variation selector / skin tone).
    """
    vote = (value or "").strip().lower()
    stripped = "".join(ch for ch in vote if ch not in _EMOJI_MODIFIERS)
    if stripped in EMOJI_VOTES:
        return EMOJI_VOTES[stripped]
    if vote in VALID_VOTES:
        return vote
    raise ValueError(f"vote must be one of {VALID_VOTES} (or a bare {THUMBS_UP}/{THUMBS_DOWN}), got {value!r}")


def append_feedback(
    path: str | Path,
    *,
    vote: str,
    et_date: str | None = None,
    source: str = "reply",
) -> dict:
    """Append one feedback record. Mirrors ``append_delivery`` semantics.

    Raises ``ValueError`` on an invalid vote (callers must validate
    intent before recording); any other failure to write is logged and
    re-raised, since recording a tap is the caller's whole job.
    """
    record = {
        "et_date": et_date or datetime.now(ET).date().isoformat(),
        "vote": normalize_vote(vote),
        "tapped_at": datetime.now(timezone.utc).isoformat(),
        "source": source,
    }
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    with target.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(record) + "\n")
    return record


def read_feedback(path: str | Path) -> list[dict]:
    """Read all feedback records; skips corrupt lines."""
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
                logger.warning("Skipping corrupt feedback line in %s", target)
    return records


def last_feedback(path: str | Path) -> dict | None:
    """Return the most recent feedback record, or None if none exist.

    A later tap always wins — Philip changing his mind is signal, not
    noise — so this is simply the last valid record in the file.
    """
    records = read_feedback(path)
    return records[-1] if records else None
