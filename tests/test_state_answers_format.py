"""Guard: STATE.answers.md must carry the literal headings write_state.sh parses.

The nightly cron prompt asks for "three plain sentences" — that is a CONTENT
instruction, not a format instruction. The file format is the template skeleton
(``templates/STATE.answers.md``) with three literal ``##`` headings that
``scripts/write_state.sh`` keys on exactly. Without them the parser falls back
to positional plain-line mapping and every STATE.md section can shift by one.

This defect shipped FIVE times (2026-10-28, 11-19→20, 11-23, 11-24, 11-25 —
rendering correctly only by positional-fallback luck on the last one). These
tests make the heading-less (or wrong-heading) form fail the blocking suite
BEFORE any nightly change, so it can never land silently again.
"""

from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
EXPECTED_HEADINGS = [
    "What it is",
    "What is currently happening",
    "What you can do next",
]


def _heading_lines(path: Path) -> list[str]:
    lines = path.read_text(encoding="utf-8").splitlines()
    return [line[3:].strip() for line in lines if line.startswith("## ")]


def test_state_answers_md_is_tracked_file() -> None:
    assert (REPO_ROOT / "STATE.answers.md").is_file(), (
        "STATE.answers.md is a tracked repo file — it must exist"
    )


def test_state_answers_md_headings_exact_ordered() -> None:
    found = _heading_lines(REPO_ROOT / "STATE.answers.md")
    assert found == EXPECTED_HEADINGS, (
        "STATE.answers.md must carry exactly the three literal write_state.sh "
        f"headings in order (copy templates/STATE.answers.md, replace bodies); found {found!r}"
    )


def test_state_answers_md_every_heading_has_body() -> None:
    """A heading without body text silently re-enters the positional fallback."""
    lines = (REPO_ROOT / "STATE.answers.md").read_text(encoding="utf-8").splitlines()
    seen: dict[str, list[str]] = {}
    current: str | None = None
    for line in lines:
        if line.startswith("## "):
            current = line[3:].strip()
            seen.setdefault(current, [])
        elif current is not None and line.strip():
            seen[current].append(line.strip())
    for heading in EXPECTED_HEADINGS:
        assert seen.get(heading), f"heading '{heading}' has no body text"


def test_repo_template_matches_parser_headings() -> None:
    found = _heading_lines(REPO_ROOT / "templates" / "STATE.answers.md")
    assert found == EXPECTED_HEADINGS, (
        "templates/STATE.answers.md drifted from the write_state.sh parser contract"
    )


def test_write_state_parser_headings_are_the_expected_set() -> None:
    """Pin the parser-side contract: the answer_headings tuple in write_state.sh."""
    script = (REPO_ROOT / "scripts" / "write_state.sh").read_text(encoding="utf-8")
    for heading in EXPECTED_HEADINGS:
        assert f'"{heading}"' in script, (
            f"write_state.sh no longer parses the '{heading}' heading — "
            "update EXPECTED_HEADINGS here together with the script and template"
        )
