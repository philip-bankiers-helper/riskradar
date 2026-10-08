"""Offline tests for the tap backstop (scripts/check_topic_tap.py).

The backstop reads local Hermes transcript files and the gateway log;
all fixtures here are synthetic files in tmp_path. No network, no
Telegram, no real ~/.hermes paths are touched.
"""

from __future__ import annotations

import json
import os
from datetime import date, datetime, timedelta

import pytest

from scripts.check_topic_tap import (
    BARE_EMOJI_RE,
    BARE_VOTE_RE,
    main,
    run_check,
    scan_sessions,
    topic_active_dates,
)

SENDER = "philip Bankier"
SINCE = date(2026, 10, 1)


def write_session(
    directory,
    name,
    *,
    platform="telegram",
    session_start="2026-10-02T09:00:00",
    messages,
    mtime=None,
    session_id=None,
):
    path = directory / name
    payload = {
        "session_id": session_id or name,
        "platform": platform,
        "session_start": session_start,
        "last_updated": session_start,
        "message_count": len(messages),
        "messages": messages,
    }
    path.write_text(json.dumps(payload), encoding="utf-8")
    if mtime is not None:
        os.utime(path, (mtime.timestamp(), mtime.timestamp()))
    return path


def user(text):
    return {"role": "user", "content": text}


def assistant(text):
    return {"role": "assistant", "content": text}


def write_log(path, entries):
    lines = []
    for day, text in entries:
        lines.append(f"{day.isoformat()} 12:00:00,123 INFO gateway.run: {text}")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


class TestBareVoteRegex:
    def test_bare_yes_variants(self):
        for text in ("[philip Bankier] yes", "[philip Bankier] YES", "[philip Bankier] Yes!", "[philip Bankier]  no."):
            match = BARE_VOTE_RE.match(text)
            assert match is not None, text

    def test_non_bare_rejected(self):
        for text in ("[philip Bankier] yes but add PLTR", "[philip Bankier] nope", "yes", "[philip Bankier]"):
            assert BARE_VOTE_RE.match(text) is None, text


class TestBareEmojiRegex:
    """A bare thumbs up/down is a first-class tap (same strictness as words)."""

    def test_bare_emoji_variants(self):
        for text in (
            "[philip Bankier] \U0001f44d",
            "[philip Bankier] \U0001f44e",
            "[philip Bankier] \U0001f44d\ufe0f",  # variation selector
            "[philip Bankier] \U0001f44d\U0001f3fb!",  # skin tone + punct
            "[philip Bankier]  \U0001f44e.",
        ):
            assert BARE_EMOJI_RE.match(text) is not None, text

    def test_non_bare_emoji_rejected(self):
        for text in (
            "[philip Bankier] \U0001f44d but too noisy",  # extra words
            "\U0001f44d",  # no sender bracket
            "[philip Bankier] \U0001f444",  # wrong emoji
            "[philip Bankier] \U0001f44d\U0001f44d",  # doubled
        ):
            assert BARE_EMOJI_RE.match(text) is None, text


class TestScanSessions:
    def test_bare_tap_found(self, tmp_path):
        write_session(
            tmp_path,
            "session_tap.json",
            messages=[user("[philip Bankier] YES")],
            mtime=datetime(2026, 10, 2, 16, 45),
        )
        candidates, near = scan_sessions(tmp_path, since=SINCE, sender=SENDER)
        assert len(candidates) == 1
        assert candidates[0]["vote"] == "yes"
        assert near == []

    def test_wordy_reply_is_near_miss_not_candidate(self, tmp_path):
        write_session(
            tmp_path,
            "session_wordy.json",
            messages=[user("[philip Bankier] yes but the heat number is confusing")],
            mtime=datetime(2026, 10, 2, 16, 45),
        )
        candidates, near = scan_sessions(tmp_path, since=SINCE, sender=SENDER)
        assert candidates == []
        assert len(near) == 1

    def test_other_sender_ignored(self, tmp_path):
        write_session(
            tmp_path,
            "session_other.json",
            messages=[user("[someone else] yes")],
            mtime=datetime(2026, 10, 2, 16, 45),
        )
        candidates, near = scan_sessions(tmp_path, since=SINCE, sender=SENDER)
        assert candidates == []
        assert near == []

    def test_non_telegram_platform_ignored(self, tmp_path):
        write_session(
            tmp_path,
            "session_cron.json",
            platform="cron",
            messages=[user("[philip Bankier] yes")],
            mtime=datetime(2026, 10, 2, 16, 45),
        )
        assert scan_sessions(tmp_path, since=SINCE, sender=SENDER) == ([], [])

    def test_session_before_since_ignored(self, tmp_path):
        write_session(
            tmp_path,
            "session_old.json",
            session_start="2026-09-20T09:00:00",
            messages=[user("[philip Bankier] yes")],
            mtime=datetime(2026, 9, 20, 16, 45),
        )
        assert scan_sessions(tmp_path, since=SINCE, sender=SENDER) == ([], [])

    def test_multiple_taps_latest_wins_ordering(self, tmp_path):
        write_session(
            tmp_path,
            "session_a.json",
            session_start="2026-10-02T09:00:00",
            messages=[user("[philip Bankier] no")],
            mtime=datetime(2026, 10, 2, 16, 45),
        )
        write_session(
            tmp_path,
            "session_b.json",
            session_start="2026-10-03T09:00:00",
            messages=[user("[philip Bankier] yes")],
            mtime=datetime(2026, 10, 3, 16, 45),
        )
        candidates, _ = scan_sessions(tmp_path, since=SINCE, sender=SENDER)
        assert [c["vote"] for c in candidates] == ["no", "yes"]  # oldest -> newest
        assert candidates[-1]["vote"] == "yes"

    def test_bare_emoji_tap_is_candidate_with_canonical_vote(self, tmp_path):
        write_session(
            tmp_path,
            "session_emoji.json",
            messages=[user("[philip Bankier] \U0001f44d\U0001f3fb")],
            mtime=datetime(2026, 10, 2, 16, 45),
        )
        candidates, near = scan_sessions(tmp_path, since=SINCE, sender=SENDER)
        assert len(candidates) == 1
        assert candidates[0]["vote"] == "yes"  # canonical word, not the emoji
        assert near == []

    def test_wordy_emoji_reply_is_near_miss_not_candidate(self, tmp_path):
        write_session(
            tmp_path,
            "session_emoji_wordy.json",
            messages=[user("[philip Bankier] \U0001f44d but the alerts are too loud")],
            mtime=datetime(2026, 10, 2, 16, 45),
        )
        candidates, near = scan_sessions(tmp_path, since=SINCE, sender=SENDER)
        assert candidates == []
        assert len(near) == 1


class TestTopicActiveDates:
    def test_extracts_4799_dates_only(self, tmp_path):
        log = tmp_path / "agent.log"
        write_log(
            log,
            [
                (date(2026, 10, 2), "Flushing text batch agent:main:telegram:group:-1003820528092:4799 (3 chars)"),
                (date(2026, 10, 3), "Flushing text batch agent:main:telegram:group:-1003820528092:528 (10 chars)"),
                (date(2026, 10, 4), "something unrelated"),
            ],
        )
        assert topic_active_dates(log) == {date(2026, 10, 2)}

    def test_missing_log_is_empty(self, tmp_path):
        assert topic_active_dates(tmp_path / "nope.log") == set()


class TestRunCheck:
    def test_no_candidates_clean_report(self, tmp_path):
        report = run_check(
            sessions_dir=tmp_path / "sessions",
            gateway_log=tmp_path / "agent.log",
            feedback_log=tmp_path / "feedback.jsonl",
            since=SINCE,
        )
        assert report["candidates"] == []
        assert report["recorded"] is None
        assert "no bare yes/no" in report["reason"]

    def test_unverified_topic_never_recorded(self, tmp_path):
        sessions = tmp_path / "sessions"
        sessions.mkdir()
        write_session(
            sessions,
            "session_tap.json",
            messages=[user("[philip Bankier] yes")],
            mtime=datetime(2026, 10, 2, 16, 45),
        )
        log = tmp_path / "agent.log"
        write_log(log, [(date(2026, 10, 2), "activity for group:-1003820528092:528 only")])
        report = run_check(
            sessions_dir=sessions,
            gateway_log=log,
            feedback_log=tmp_path / "feedback.jsonl",
            since=SINCE,
            apply=True,
        )
        assert report["topic_verified"] == 0
        assert report["recorded"] is None
        assert "none topic-verified" in report["reason"]
        assert not (tmp_path / "feedback.jsonl").exists()

    def test_dry_run_reports_would_record_without_writing(self, tmp_path):
        sessions = tmp_path / "sessions"
        sessions.mkdir()
        write_session(
            sessions,
            "session_tap.json",
            messages=[user("[philip Bankier] no")],
            mtime=datetime(2026, 10, 2, 16, 45),
        )
        log = tmp_path / "agent.log"
        write_log(
            log,
            [(date(2026, 10, 2), "Flushing text batch agent:main:telegram:group:-1003820528092:4799 (1 chars)")],
        )
        fb = tmp_path / "feedback.jsonl"
        report = run_check(sessions_dir=sessions, gateway_log=log, feedback_log=fb, since=SINCE)
        assert report["topic_verified"] == 1
        assert report["recorded"] is None
        assert "would record" in report["reason"]
        assert not fb.exists()

    def test_apply_records_newest_verified(self, tmp_path):
        sessions = tmp_path / "sessions"
        sessions.mkdir()
        write_session(
            sessions,
            "session_a.json",
            session_start="2026-10-02T09:00:00",
            messages=[user("[philip Bankier] no")],
            mtime=datetime(2026, 10, 2, 16, 45),
        )
        write_session(
            sessions,
            "session_b.json",
            session_start="2026-10-03T09:00:00",
            messages=[user("[philip Bankier] yes")],
            mtime=datetime(2026, 10, 3, 16, 45),
        )
        log = tmp_path / "agent.log"
        write_log(
            log,
            [
                (date(2026, 10, 2), "Flushing ...group:-1003820528092:4799"),
                (date(2026, 10, 3), "Flushing ...group:-1003820528092:4799"),
            ],
        )
        fb = tmp_path / "feedback.jsonl"
        report = run_check(sessions_dir=sessions, gateway_log=log, feedback_log=fb, since=SINCE, apply=True)
        assert report["recorded"]["vote"] == "yes"
        assert report["recorded"]["source"].startswith("transcript-relay:session_b")
        lines = fb.read_text().strip().splitlines()
        assert len(lines) == 1
        assert json.loads(lines[0])["vote"] == "yes"

    def test_apply_records_emoji_tap_as_canonical_word(self, tmp_path):
        sessions = tmp_path / "sessions"
        sessions.mkdir()
        write_session(
            sessions,
            "session_emoji.json",
            messages=[user("[philip Bankier] \U0001f44d\ufe0f")],
            mtime=datetime(2026, 10, 2, 16, 45),
        )
        log = tmp_path / "agent.log"
        write_log(log, [(date(2026, 10, 2), "Flushing ...group:-1003820528092:4799")])
        fb = tmp_path / "feedback.jsonl"
        report = run_check(sessions_dir=sessions, gateway_log=log, feedback_log=fb, since=SINCE, apply=True)
        assert report["recorded"]["vote"] == "yes"
        assert json.loads(fb.read_text().strip())["vote"] == "yes"

    def test_apply_skips_when_feedback_already_newer(self, tmp_path):
        sessions = tmp_path / "sessions"
        sessions.mkdir()
        write_session(
            sessions,
            "session_tap.json",
            messages=[user("[philip Bankier] yes")],
            mtime=datetime(2026, 10, 2, 16, 45),
        )
        log = tmp_path / "agent.log"
        write_log(log, [(date(2026, 10, 2), "Flushing ...group:-1003820528092:4799")])
        fb = tmp_path / "feedback.jsonl"
        fb.write_text(
            json.dumps(
                {
                    "et_date": "2026-10-02",
                    "vote": "no",
                    "tapped_at": "2026-10-02T21:00:00+00:00",
                    "source": "reply",
                }
            )
            + "\n",
            encoding="utf-8",
        )
        report = run_check(sessions_dir=sessions, gateway_log=log, feedback_log=fb, since=SINCE, apply=True)
        assert report["recorded"] is None
        assert "latest tap wins" in report["reason"]
        # The pre-existing record is untouched.
        assert json.loads(fb.read_text().strip())["vote"] == "no"


class TestCli:
    def test_main_exit_zero_quiet(self, tmp_path, capsys):
        sessions = tmp_path / "sessions"
        sessions.mkdir()
        log = tmp_path / "agent.log"
        log.write_text("", encoding="utf-8")
        code = main(
            [
                "--sessions-dir",
                str(sessions),
                "--gateway-log",
                str(log),
                "--feedback-log",
                str(tmp_path / "fb.jsonl"),
                "--since",
                SINCE.isoformat(),
            ]
        )
        assert code == 0
        report = json.loads(capsys.readouterr().out)
        assert report["recorded"] is None


class TestSourceGuards:
    """The backstop must stay read-local-only: no Telegram read path."""

    def test_no_telegram_api_or_getupdates_in_script(self):
        source = (
            __import__("pathlib").Path(__import__("inspect").getsourcefile(run_check))
        ).read_text(encoding="utf-8")
        # Call-site forms only (mirrors the src/feedback.py guard): the
        # design-constraint docstring may mention getUpdates in prose.
        assert "api.telegram.org" not in source
        assert "getUpdates(" not in source
        assert "sendMessage(" not in source
