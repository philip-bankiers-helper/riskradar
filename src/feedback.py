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

# Natural one-token affirmations/denials count as the same tap (found
# 2026-11-23: a bare "y"/"nope" was invisible to normalize_vote AND to
# the nightly backstop — not even a near-miss — so a real tap in the
# most casual form would have stalled W2 forever). Ambiguous
# acknowledgments ("ok", "sure") deliberately stay OUT: in a topic that
# also carries conversation they cannot be attributed to the summary
# question. Longest-first within each polarity so regex alternation
# built from these keys never half-matches ("nope" as "no" + "pe").
AFFIRMATIVE_TOKENS = ("yeah", "yep", "yup", "yes", "y")
NEGATIVE_TOKENS = ("nope", "nah", "no", "n")
WORD_VOTES: dict[str, str] = {token: "yes" for token in AFFIRMATIVE_TOKENS}
WORD_VOTES.update({token: "no" for token in NEGATIVE_TOKENS})


def normalize_vote(value: str) -> str:
    """Validate and canonicalize a vote to 'yes' or 'no'.

    Accepts yes/no and their natural one-token synonyms y/yep/yup/yeah,
    n/nah/nope (case-insensitive), or a bare thumbs up/down emoji (with
    optional variation selector / skin tone). Records always store the
    canonical word so downstream readers never see the alias form.
    """
    vote = (value or "").strip().lower()
    stripped = "".join(ch for ch in vote if ch not in _EMOJI_MODIFIERS)
    if stripped in EMOJI_VOTES:
        return EMOJI_VOTES[stripped]
    if vote in WORD_VOTES:
        return WORD_VOTES[vote]
    raise ValueError(
        f"vote must be a bare yes/no token ({'/'.join(WORD_VOTES)}), "
        f"or a bare {THUMBS_UP}/{THUMBS_DOWN}, got {value!r}"
    )


def _normalize_tapped_at(value: datetime | str | None) -> str:
    """Normalize a tap moment to aware-UTC ISO for storage.

    None means "now" (correct only for a live relay). A naive datetime
    is interpreted as local machine time — the convention of transcript
    file mtimes, the only moment the offline backstop store carries.
    """
    if value is None:
        return datetime.now(timezone.utc).isoformat()
    if isinstance(value, datetime):
        moment = value
    else:
        raw = str(value).strip()
        try:
            moment = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        except ValueError:
            raise ValueError(
                f"tapped_at must be a datetime or ISO datetime string, got {value!r}"
            ) from None
    if moment.tzinfo is None:
        moment = moment.astimezone()  # naive -> system-local aware
    return moment.astimezone(timezone.utc).isoformat()


def append_feedback(
    path: str | Path,
    *,
    vote: str,
    et_date: str | None = None,
    source: str = "reply",
    tapped_at: datetime | str | None = None,
) -> dict:
    """Append one feedback record. Mirrors ``append_delivery`` semantics.

    Raises ``ValueError`` on an invalid vote (callers must validate
    intent before recording); any other failure to write is logged and
    re-raised, since recording a tap is the caller's whole job.

    ``tapped_at`` pins the record to the tap's real moment. The default
    (None) stamps now, which is only correct when the caller is relaying
    the tap live. A DELAYED relay (the nightly transcript backstop,
    which can run days after the tap on a weekday-only runner) must pass
    the tap's own moment or the permanent W2 evidence — and the ack the
    next summary shows Philip — carries the apply-run date instead.
    Accepts a datetime (naive = local machine time, matching transcript
    file mtimes) or an ISO string; stored normalized to aware UTC.
    """
    record = {
        "et_date": et_date or datetime.now(ET).date().isoformat(),
        "vote": normalize_vote(vote),
        "tapped_at": _normalize_tapped_at(tapped_at),
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
