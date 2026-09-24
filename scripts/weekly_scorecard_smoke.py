#!/usr/bin/env python3
"""Pre-Monday readiness probe: render the weekly scorecard, never send.

Runs the exact fetch+build path the Monday 16:30 ET slot runs
(``produce_weekly_scorecard_text`` with its default network fetch),
then reports OK/FAIL with window and episode diagnostics. The script
never imports the alert manager, so it is structurally incapable of
sending anything to Telegram — a green run tonight means Monday's
weekly post should render, without touching the slot itself.

Usage: ``.venv/bin/python scripts/weekly_scorecard_smoke.py`` (network).
Exit 0 = ready, exit 1 = the Monday post would skip (fetch/build/None)
or render a stale/unparseable window.
"""

from __future__ import annotations

import asyncio
import re
import sys
from datetime import date, datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.engine.weekly_scorecard import produce_weekly_scorecard_text

_HEADER_MARK = "WEEKLY LEAD-TIME SCORECARD"
_WINDOW_RE = re.compile(r"Window:\s*(\d{4}-\d{2}-\d{2})\s*→\s*(\d{4}-\d{2}-\d{2})")
_EPISODES_RE = re.compile(r"Drawdown episodes \(≥5%\): (\d+)")
# A healthy daily fetch is at most a long weekend + holiday stale
# (~4-5 calendar days). Anything past this means the data path is
# frozen and the Monday post would misreport its window.
_STALE_AFTER_DAYS = 10


def smoke_verdict(
    text: str | None,
    *,
    today: date | None = None,
    stale_after_days: int = _STALE_AFTER_DAYS,
) -> tuple[int, list[str]]:
    """Grade a rendered scorecard for Monday readiness (pure, offline).

    Returns ``(exit_code, diagnostic_lines)``. ``None`` text — what the
    production path yields on any fetch failure — is a FAIL, not a
    crash: that is exactly the skip the scheduler would take.
    """
    if today is None:
        today = datetime.now(timezone.utc).date()
    if text is None:
        return (
            1,
            [
                "FAIL weekly-scorecard smoke: production path returned None",
                "Monday slot would SKIP the post — suspect the fetch path",
                "(yfinance failure logs carry 'Weekly scorecard fetch')",
            ],
        )
    if _HEADER_MARK not in text:
        return 1, ["FAIL weekly-scorecard smoke: output is not a scorecard render"]
    window = _WINDOW_RE.search(text)
    if window is None:
        return 1, ["FAIL weekly-scorecard smoke: could not parse window line"]
    start_s, end_s = window.group(1), window.group(2)
    episodes_m = _EPISODES_RE.search(text)
    episodes = int(episodes_m.group(1)) if episodes_m else 0
    end = date.fromisoformat(end_s)
    age = (today - end).days
    lines = [
        f"OK weekly-scorecard smoke: renders through the production path",
        f"window={start_s}→{end_s} episodes={episodes} window_age_days={age}",
    ]
    if age > stale_after_days:
        return (
            1,
            lines[:2]
            + [
                f"FAIL window end {end_s} is {age} days stale (>{stale_after_days})",
                "data path looks frozen — investigate before Monday 16:30 ET",
            ],
        )
    return 0, lines


async def _run() -> int:
    text = await produce_weekly_scorecard_text()
    code, lines = smoke_verdict(text)
    for line in lines:
        print(line)
    if code == 0 and text:
        print()
        print(text)
    return code


def main() -> None:
    raise SystemExit(asyncio.run(_run()))


if __name__ == "__main__":
    main()
