#!/usr/bin/env python
"""Record Philip's yes/no verdict on the daily summary (W2).

Usage:
    python scripts/record_feedback.py {yes|no|👍|👎} [--date YYYY-MM-DD] [--base-url URL]

Tries the local service's POST /feedback first (single source of
truth, works with any future storage change); falls back to a direct
JSONL append when the service is down so a tap is never lost.

This script NEVER sends anything to Telegram. The bot token is shared
with the Hermes gateway, so RiskRadar has no Telegram read path — the
"tap" is Philip's one-word (or one-emoji) topic reply, relayed by
whoever sees it. Emoji votes are canonicalized to yes/no before
recording, so the log format never changes.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from src.feedback import (  # noqa: E402
    THUMBS_DOWN,
    THUMBS_UP,
    VALID_VOTES,
    append_feedback,
    normalize_vote,
)

DEFAULT_BASE_URL = "http://127.0.0.1:8001"

# CLI accepts the emoji forms too; they canonicalize to words before use.
CLI_VOTES = (*VALID_VOTES, THUMBS_UP, THUMBS_DOWN)


def post_to_service(base_url: str, vote: str, et_date: str | None) -> bool:
    """POST the vote to the local API; True on recorded."""
    url = f"{base_url.rstrip('/')}/feedback"
    params = f"vote={urllib.parse.quote(vote)}"
    if et_date:
        params += f"&et_date={urllib.parse.quote(et_date)}"
    try:
        req = urllib.request.Request(
            f"{url}?{params}", method="POST", data=b""
        )  # nosec B310 - fixed localhost base URL
        with urllib.request.urlopen(req, timeout=5) as resp:
            return resp.status == 200
    except (urllib.error.URLError, OSError, ValueError):
        return False


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("vote", choices=CLI_VOTES, help="Philip's verdict")
    parser.add_argument("--date", default=None, help="ET date of the summary (default: today)")
    parser.add_argument("--base-url", default=DEFAULT_BASE_URL, help="local service base URL")
    args = parser.parse_args(argv)
    vote = normalize_vote(args.vote)

    if post_to_service(args.base_url, vote, args.date):
        print(f"recorded via API: {vote} ({args.date or 'today ET'})")
        return 0

    # Service down (restart window, crash) — append directly so the tap
    # still counts; the file is exactly what the API would have written.
    from src.config import PROJECT_ROOT

    log_path = Path(
        os.environ.get("RISKRADAR_FEEDBACK_LOG_PATH")
        or PROJECT_ROOT / "data" / "feedback_log.jsonl"
    )
    record = append_feedback(
        str(log_path), vote=vote, et_date=args.date, source="cli-offline"
    )
    print(f"recorded via direct append (service unreachable): {json.dumps(record)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
