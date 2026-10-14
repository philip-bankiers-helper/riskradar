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
        candidates, near, mismatches = scan_sessions(tmp_path, since=SINCE, sender=SENDER)
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
        candidates, near, mismatches = scan_sessions(tmp_path, since=SINCE, sender=SENDER)
        assert candidates == []
        assert len(near) == 1

    def test_other_sender_ignored(self, tmp_path):
        write_session(
            tmp_path,
            "session_other.json",
            messages=[user("[someone else] yes")],
            mtime=datetime(2026, 10, 2, 16, 45),
        )
        candidates, near, mismatches = scan_sessions(tmp_path, since=SINCE, sender=SENDER)
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
        assert scan_sessions(tmp_path, since=SINCE, sender=SENDER) == ([], [], [])

    def test_session_before_since_ignored(self, tmp_path):
        write_session(
            tmp_path,
            "session_old.json",
            session_start="2026-09-20T09:00:00",
            messages=[user("[philip Bankier] yes")],
            mtime=datetime(2026, 9, 20, 16, 45),
        )
        assert scan_sessions(tmp_path, since=SINCE, sender=SENDER) == ([], [], [])

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
        candidates, _, _ = scan_sessions(tmp_path, since=SINCE, sender=SENDER)
        assert [c["vote"] for c in candidates] == ["no", "yes"]  # oldest -> newest
        assert candidates[-1]["vote"] == "yes"

    def test_bare_emoji_tap_is_candidate_with_canonical_vote(self, tmp_path):
        write_session(
            tmp_path,
            "session_emoji.json",
            messages=[user("[philip Bankier] \U0001f44d\U0001f3fb")],
            mtime=datetime(2026, 10, 2, 16, 45),
        )
        candidates, near, mismatches = scan_sessions(tmp_path, since=SINCE, sender=SENDER)
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
        candidates, near, mismatches = scan_sessions(tmp_path, since=SINCE, sender=SENDER)
        assert candidates == []
        assert len(near) == 1


class TestQuotedReplyTaps:
    """A reply-to-summary tap renders with a [Replying to: "..."] envelope.

    This is the natural way Philip answers the 16:30 summary; without
    envelope stripping the tap would be invisible (not even a near-miss).
    """

    SUMMARY_QUOTE = (
        '[Replying to: "RiskRadar — daily summary 📉\\n'
        "Heat 0.19 (cool) in unknown regime.\\n"
        'Reply YES / NO (or 👍 / 👎) in this topic."]'
    )

    def test_quoted_word_tap_is_candidate(self, tmp_path):
        write_session(
            tmp_path,
            "session_quote.json",
            messages=[user(f"[philip Bankier] {self.SUMMARY_QUOTE}\n\nyes")],
            mtime=datetime(2026, 10, 2, 16, 45),
        )
        candidates, near, mismatches = scan_sessions(tmp_path, since=SINCE, sender=SENDER)
        assert len(candidates) == 1
        assert candidates[0]["vote"] == "yes"
        assert near == []

    def test_quoted_emoji_tap_is_candidate(self, tmp_path):
        write_session(
            tmp_path,
            "session_quote_emoji.json",
            messages=[user(f"[philip Bankier] {self.SUMMARY_QUOTE}\n\n\U0001f44d\ufe0f")],
            mtime=datetime(2026, 10, 2, 16, 45),
        )
        candidates, near, mismatches = scan_sessions(tmp_path, since=SINCE, sender=SENDER)
        assert len(candidates) == 1
        assert candidates[0]["vote"] == "yes"  # canonical word, not the emoji
        assert near == []

    def test_quoted_no_tap_is_candidate(self, tmp_path):
        write_session(
            tmp_path,
            "session_quote_no.json",
            messages=[user(f"[philip Bankier] {self.SUMMARY_QUOTE}\n\nNO.")],
            mtime=datetime(2026, 10, 2, 16, 45),
        )
        candidates, _, _ = scan_sessions(tmp_path, since=SINCE, sender=SENDER)
        assert len(candidates) == 1
        assert candidates[0]["vote"] == "no"

    def test_quoted_wordy_reply_is_near_miss(self, tmp_path):
        write_session(
            tmp_path,
            "session_quote_wordy.json",
            messages=[
                user(f"[philip Bankier] {self.SUMMARY_QUOTE}\n\nyes but the heat section is too dense")
            ],
            mtime=datetime(2026, 10, 2, 16, 45),
        )
        candidates, near, mismatches = scan_sessions(tmp_path, since=SINCE, sender=SENDER)
        assert candidates == []
        assert len(near) == 1

    def test_quoted_tap_from_other_sender_is_reported_mismatch(self, tmp_path):
        write_session(
            tmp_path,
            "session_quote_other.json",
            messages=[user(f"[Vic] {self.SUMMARY_QUOTE}\n\nyes")],
            mtime=datetime(2026, 10, 2, 16, 45),
        )
        candidates, near, mismatches = scan_sessions(tmp_path, since=SINCE, sender=SENDER)
        assert candidates == []
        assert near == []
        assert len(mismatches) == 1
        assert mismatches[0]["sender"] == "Vic"
        assert mismatches[0]["vote"] == "yes"

    def test_quote_with_brackets_and_newlines_does_not_swallow_reply(self, tmp_path):
        quote = '[Replying to: "Vic said [hello there] and\nalso mentioned [RiskRadar] twice"]'
        write_session(
            tmp_path,
            "session_quote_nested.json",
            messages=[user(f"[philip Bankier] {quote}\n\nno")],
            mtime=datetime(2026, 10, 2, 16, 45),
        )
        candidates, near, mismatches = scan_sessions(tmp_path, since=SINCE, sender=SENDER)
        assert len(candidates) == 1
        assert candidates[0]["vote"] == "no"
        assert near == []

    def test_envelope_with_no_reply_text_is_not_a_tap(self, tmp_path):
        write_session(
            tmp_path,
            "session_quote_bare.json",
            messages=[user(f"[philip Bankier] {self.SUMMARY_QUOTE}")],
            mtime=datetime(2026, 10, 2, 16, 45),
        )
        assert scan_sessions(tmp_path, since=SINCE, sender=SENDER) == ([], [], [])

    def test_quoted_tap_full_check_dry_run_reports_would_record(self, tmp_path):
        sessions = tmp_path / "sessions"
        sessions.mkdir()
        write_session(
            sessions,
            "session_quote.json",
            messages=[user(f"[philip Bankier] {self.SUMMARY_QUOTE}\n\nyes")],
            mtime=datetime(2026, 10, 2, 16, 45),
        )
        log = tmp_path / "agent.log"
        write_log(
            log,
            [(date(2026, 10, 2), "Flushing text batch agent:main:telegram:group:-1003820528092:4799 (1 chars)")],
        )
        report = run_check(
            sessions_dir=sessions,
            gateway_log=log,
            feedback_log=tmp_path / "feedback.jsonl",
            since=SINCE,
        )
        assert report["topic_verified"] == 1
        assert report["recorded"] is None
        assert "would record vote=yes" in report["reason"]
        assert not (tmp_path / "feedback.jsonl").exists()


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


class TestSenderMismatches:
    """A bare tap from any other sender is reported, never recorded.

    If Philip renames his Telegram display name, every real tap would
    otherwise be silently dropped — not even a near-miss — and W2 would
    stall on "0 candidates, 0 near-misses" with nothing to distinguish
    silence from a sender mismatch.
    """

    def test_bare_word_from_renamed_sender_is_mismatch(self, tmp_path):
        write_session(
            tmp_path,
            "session_renamed.json",
            messages=[user("[Philip B.] yes")],
            mtime=datetime(2026, 10, 2, 16, 45),
        )
        candidates, near, mismatches = scan_sessions(tmp_path, since=SINCE, sender=SENDER)
        assert candidates == []
        assert near == []
        assert len(mismatches) == 1
        assert mismatches[0]["sender"] == "Philip B."
        assert mismatches[0]["vote"] == "yes"

    def test_bare_emoji_from_other_sender_is_mismatch(self, tmp_path):
        write_session(
            tmp_path,
            "session_emoji_other.json",
            messages=[user("[philip bankier-backup] \U0001f44e")],
            mtime=datetime(2026, 10, 2, 16, 45),
        )
        candidates, near, mismatches = scan_sessions(tmp_path, since=SINCE, sender=SENDER)
        assert candidates == []
        assert len(mismatches) == 1
        assert mismatches[0]["vote"] == "no"  # emoji canonicalized

    def test_right_sender_tap_is_not_a_mismatch(self, tmp_path):
        write_session(
            tmp_path,
            "session_right.json",
            messages=[user("[philip Bankier] yes")],
            mtime=datetime(2026, 10, 2, 16, 45),
        )
        candidates, near, mismatches = scan_sessions(tmp_path, since=SINCE, sender=SENDER)
        assert len(candidates) == 1
        assert mismatches == []

    def test_wordy_reply_from_other_sender_stays_invisible(self, tmp_path):
        # Wordy messages from other senders are not mismatches: without a
        # sender anchor, "yes ..." from anyone would drown the report.
        write_session(
            tmp_path,
            "session_wordy_other.json",
            messages=[user("[Vic] yes the summary was great")],
            mtime=datetime(2026, 10, 2, 16, 45),
        )
        candidates, near, mismatches = scan_sessions(tmp_path, since=SINCE, sender=SENDER)
        assert candidates == []
        assert near == []
        assert mismatches == []

    def test_report_carries_mismatches_and_reason(self, tmp_path):
        sessions = tmp_path / "sessions"
        sessions.mkdir()
        write_session(
            sessions,
            "session_renamed.json",
            messages=[user("[Philip B.] yes")],
            mtime=datetime(2026, 10, 2, 16, 45),
        )
        log = tmp_path / "agent.log"
        write_log(log, [])
        fb = tmp_path / "fb.jsonl"
        report = run_check(sessions_dir=sessions, gateway_log=log, feedback_log=fb, since=SINCE)
        assert report["candidates"] == []
        assert len(report["sender_mismatches"]) == 1
        assert report["recorded"] is None
        assert "no bare yes/no" in report["reason"]
        assert "sender_mismatches" in report["reason"] or "display-name" in report["reason"]

    def test_mismatch_never_recorded_even_with_apply(self, tmp_path):
        sessions = tmp_path / "sessions"
        sessions.mkdir()
        write_session(
            sessions,
            "session_renamed.json",
            messages=[user("[Philip B.] yes")],
            mtime=datetime(2026, 10, 2, 16, 45),
        )
        log = tmp_path / "agent.log"
        write_log(log, [(date(2026, 10, 2), "telegram group -1003820528092 thread 4799 active")])
        fb = tmp_path / "fb.jsonl"
        report = run_check(
            sessions_dir=sessions, gateway_log=log, feedback_log=fb, since=SINCE, apply=True
        )
        assert report["recorded"] is None
        assert not fb.exists()  # nothing written, even topic-adjacent

    def test_sender_override_matches_renamed_sender(self, tmp_path):
        # The self-heal path: rerun with --sender once the rename is known.
        write_session(
            tmp_path,
            "session_renamed.json",
            messages=[user("[Philip B.] yes")],
            mtime=datetime(2026, 10, 2, 16, 45),
        )
        candidates, near, mismatches = scan_sessions(
            tmp_path, since=SINCE, sender="Philip B."
        )
        assert len(candidates) == 1
        assert candidates[0]["vote"] == "yes"
        assert mismatches == []


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
