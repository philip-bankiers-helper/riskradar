#!/usr/bin/env bash
set -euo pipefail

REPO_ROOT=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
cd "$REPO_ROOT"

python3 - <<'PY'
import json
import os
from datetime import datetime, timezone
from pathlib import Path

output_dir = Path("/Users/kairox/.hermes/cron/output/0ce739f220e1")
reports = sorted(output_dir.glob("*.md"), key=lambda path: path.stat().st_mtime, reverse=True)
frontier = None
for report in reports:
    for line in report.read_text(encoding="utf-8", errors="replace").splitlines():
        if line.startswith("FRONTIER:"):
            frontier = line
            break
    if frontier:
        break

if frontier is None:
    progress = Path("PROGRESS.md")
    if progress.exists():
        blocks = [block.strip() for block in progress.read_text(encoding="utf-8").split("\n## ") if block.strip()]
        if blocks:
            lines = [line.strip("- ") for line in blocks[-1].splitlines() if line.strip() and not line.startswith("#")]
            if lines:
                frontier = f"FRONTIER: {lines[-1]}"
if frontier is None:
    frontier = "FRONTIER: no nightly report yet"

answers_path = Path("STATE.answers.md")
answer_headings = (
    "What it is",
    "What is currently happening",
    "What you can do next",
)
answers = {heading: "(not written yet)" for heading in answer_headings}
roadmap = json.loads(Path("ROADMAP.json").read_text(encoding="utf-8"))
done_increments = [str(item.get("id")) for item in roadmap if item.get("passes") is True]
open_increments = [str(item.get("id")) for item in roadmap if item.get("passes") is not True]
roadmap_parts = []
if done_increments:
    roadmap_parts.append(f"{', '.join(done_increments)} {'is' if len(done_increments) == 1 else 'are'} done")
if open_increments:
    roadmap_parts.append(f"{', '.join(open_increments)} {'is' if len(open_increments) == 1 else 'are'} still open")
roadmap_happening = "; ".join(roadmap_parts) + "." if roadmap_parts else "No roadmap increments are recorded."
if answers_path.exists():
    current = None
    chunks = {heading: [] for heading in answer_headings}
    answer_lines = answers_path.read_text(encoding="utf-8").splitlines()
    plain_lines = [line.strip() for line in answer_lines if line.strip() and not line.startswith("## ")]
    for raw_line in answer_lines:
        if raw_line.startswith("## "):
            candidate = raw_line[3:].strip()
            current = candidate if candidate in chunks else None
        elif current and raw_line.strip():
            chunks[current].append(raw_line.strip())
    for heading, lines in chunks.items():
        if lines:
            answers[heading] = " ".join(lines)
    for index, heading in enumerate(answer_headings):
        if not chunks[heading] and index < len(plain_lines):
            answers[heading] = plain_lines[index]
    if not chunks["What is currently happening"]:
        if len(plain_lines) < 2:
            answers["What is currently happening"] = roadmap_happening

timestamp = datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")
sources = "Mac Studio · /Users/kairox/riskradar · nightly 02:30 Mon–Fri · Telegram Kairox HQ topic 4799 · github.com/philip-bankiers-helper/riskradar · Substack none"
content = f"""{frontier}

## What it is
{answers['What it is']}

## What is currently happening
{answers['What is currently happening']}

## What you can do next
{answers['What you can do next']}

## Sources and tools
{sources}

## Document details
_written nightly by Manager Hermes · {timestamp}_
"""

tmp_path = Path(f"STATE.md.tmp.{os.getpid()}")
tmp_path.write_text(content, encoding="utf-8")
tmp_path.replace("STATE.md")
PY
