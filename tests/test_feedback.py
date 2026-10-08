"""Tests for the W2 yes/no feedback path (src/feedback.py + wiring).

Design constraint pinned here: the Telegram bot token is shared with
the Hermes gateway, so the feedback path must NEVER grow a Telegram
read side (getUpdates) — a second consumer would fight the gateway.
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.alerts.alert_manager import AlertManager
from src.api import routes as api_routes
from src.delivery_log import ET
from src.feedback import (
    append_feedback,
    last_feedback,
    normalize_vote,
    read_feedback,
)
from src.models import HeatLevel, HeatScore, HeatScoreComponents


def _make_heat(score: float = 0.2) -> HeatScore:
    return HeatScore(
        score=score,
        level=HeatLevel.COOL,
        components=HeatScoreComponents(
            absorption_ratio=0.5,
            turbulence=10.0,
            turbulence_percentile=0.5,
            diversification_ratio=1.5,
            diversification_percentile=0.5,
            factor_hhi=0.3,
            factor_hhi_percentile=0.5,
            avg_correlation=0.4,
            avg_correlation_percentile=0.5,
        ),
        dominant_factor="ai_tech",
        top_correlated_pair="NVDA↔AMD (0.85)",
        action="Monitor positions",
    )


class TestFeedbackModule:
    def test_roundtrip_append_and_last(self, tmp_path: Path):
        log = tmp_path / "feedback_log.jsonl"
        append_feedback(log, vote="yes", et_date="2026-09-29")
        fb = last_feedback(log)
        assert fb is not None
        assert fb["vote"] == "yes"
        assert fb["et_date"] == "2026-09-29"
        assert fb["tapped_at"]

    def test_invalid_vote_rejected(self, tmp_path: Path):
        with pytest.raises(ValueError):
            normalize_vote("maybe")
        with pytest.raises(ValueError):
            append_feedback(tmp_path / "f.jsonl", vote="perhaps")

    def test_missing_file_is_none(self, tmp_path: Path):
        assert last_feedback(tmp_path / "nope.jsonl") is None

    def test_latest_tap_wins(self, tmp_path: Path):
        log = tmp_path / "feedback_log.jsonl"
        append_feedback(log, vote="yes", et_date="2026-09-29")
        append_feedback(log, vote="no", et_date="2026-09-29")
        assert last_feedback(log)["vote"] == "no"

    def test_default_et_date_is_today_et(self, tmp_path: Path):
        log = tmp_path / "feedback_log.jsonl"
        append_feedback(log, vote="yes")
        assert last_feedback(log)["et_date"] == datetime.now(ET).date().isoformat()

    def test_corrupt_lines_skipped(self, tmp_path: Path):
        log = tmp_path / "feedback_log.jsonl"
        log.write_text("not json\n", encoding="utf-8")
        append_feedback(log, vote="no", et_date="2026-09-29")
        assert [r["vote"] for r in read_feedback(log)] == ["no"]

    def test_no_telegram_read_side(self):
        """Shared-bot invariant: never grow a getUpdates consumer here."""
        source = (
            Path(__file__).resolve().parents[1] / "src" / "feedback.py"
        ).read_text(encoding="utf-8")
        # The docstring explains WHY there is no read side; the guard
        # pins the absence of an actual call or Bot API URL.
        assert "/getUpdates" not in source
        assert "getUpdates(" not in source


class TestEmojiVotes:
    """A bare thumbs up/down is a first-class tap; records stay words."""

    def test_bare_emoji_canonicalized(self):
        assert normalize_vote("\U0001f44d") == "yes"
        assert normalize_vote("\U0001f44e") == "no"
        assert normalize_vote("\U0001f44d\ufe0f") == "yes"  # variation selector
        assert normalize_vote("\U0001f44e\U0001f3fd") == "no"  # skin tone

    def test_emoji_written_as_canonical_word(self, tmp_path: Path):
        log = tmp_path / "feedback_log.jsonl"
        append_feedback(log, vote="\U0001f44d", et_date="2026-10-08")
        fb = last_feedback(log)
        assert fb is not None
        assert fb["vote"] == "yes"  # downstream readers never see the emoji

    def test_emoji_with_junk_rejected(self):
        for bad in ("\U0001f44d\U0001f44d", "yes \U0001f44d", "\U0001f44d!", "\U0001f444"):
            with pytest.raises(ValueError):
                normalize_vote(bad)


class TestSummaryRendering:
    async def _send(self, manager: AlertManager) -> str:
        sent: list[str] = []

        async def fake_send(message: str) -> bool:
            sent.append(message)
            return True

        manager._send_telegram = fake_send  # type: ignore[assignment]
        await manager.send_daily_summary(
            heat_score=_make_heat(),
            regime_state={"regime": "calm", "confidence": 0.9},
            attribution=None,
            recommendations=None,
        )
        return sent[0]

    async def test_summary_shows_last_feedback_and_ask(self, tmp_path: Path):
        log = tmp_path / "feedback_log.jsonl"
        append_feedback(log, vote="yes", et_date="2026-09-28")
        manager = AlertManager(feedback_log_path=str(log))
        msg = await self._send(manager)
        assert "<b>Last feedback:</b>" in msg
        assert "YES" in msg
        assert "2026-09-28" in msg
        assert "Reply <b>YES</b> / <b>NO</b>" in msg

    async def test_summary_ask_mentions_emoji_tap(self):
        manager = AlertManager()
        msg = await self._send(manager)
        assert (
            "Reply <b>YES</b> / <b>NO</b> (or \U0001f44d / \U0001f44e) in this topic." in msg
        )

    async def test_summary_renders_emoji_tap_recorded_as_word(self, tmp_path: Path):
        log = tmp_path / "feedback_log.jsonl"
        append_feedback(log, vote="\U0001f44d", et_date="2026-10-07")
        manager = AlertManager(feedback_log_path=str(log))
        msg = await self._send(manager)
        assert "<b>Last feedback:</b> \U0001f44d YES" in msg
        assert "2026-10-07" in msg

    async def test_summary_without_feedback_asks_only(self):
        manager = AlertManager()
        msg = await self._send(manager)
        assert "Last feedback" not in msg
        assert "Reply <b>YES</b> / <b>NO</b>" in msg

    async def test_no_tap_is_displayed_as_no(self, tmp_path: Path):
        log = tmp_path / "feedback_log.jsonl"
        append_feedback(log, vote="no")
        manager = AlertManager(feedback_log_path=str(log))
        msg = await self._send(manager)
        assert "NO" in msg


class TestFeedbackAPI:
    @pytest.fixture()
    def client_and_log(self, tmp_path: Path):
        log = tmp_path / "feedback_log.jsonl"
        app = FastAPI()
        app.include_router(api_routes.router)
        api_routes.set_app_state({"feedback_log_path": str(log)})
        return TestClient(app), log

    def test_get_empty_then_post_then_get(self, client_and_log):
        client, log = client_and_log
        resp = client.get("/feedback")
        assert resp.status_code == 200
        assert resp.json()["feedback"] is None

        resp = client.post("/feedback", params={"vote": "yes"})
        assert resp.status_code == 200
        assert resp.json()["recorded"]["vote"] == "yes"

        resp = client.get("/feedback")
        assert resp.json()["feedback"]["vote"] == "yes"
        assert last_feedback(log)["source"] == "api"

    def test_invalid_vote_is_422(self, client_and_log):
        client, _ = client_and_log
        resp = client.post("/feedback", params={"vote": "maybe"})
        assert resp.status_code == 422

    def test_emoji_vote_accepted_and_canonicalized(self, client_and_log):
        client, _ = client_and_log
        resp = client.post("/feedback", params={"vote": "\U0001f44e"})
        assert resp.status_code == 200
        assert resp.json()["recorded"]["vote"] == "no"

    def test_doubled_emoji_is_422(self, client_and_log):
        client, _ = client_and_log
        resp = client.post("/feedback", params={"vote": "\U0001f44d\U0001f44d"})
        assert resp.status_code == 422


class TestRecordFeedbackCLI:
    def test_api_path(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
        import scripts.record_feedback as rf

        monkeypatch.setattr(rf, "post_to_service", lambda *a, **k: True)
        assert rf.main(["yes"]) == 0

    def test_api_path_canonicalizes_emoji_before_post(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ):
        import scripts.record_feedback as rf

        seen: list[str] = []

        def fake_post(base_url: str, vote: str, et_date: str | None) -> bool:
            seen.append(vote)
            return True

        monkeypatch.setattr(rf, "post_to_service", fake_post)
        assert rf.main(["\U0001f44d"]) == 0
        assert seen == ["yes"]  # the wire format stays the word

    def test_offline_fallback_appends_directly(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ):
        import scripts.record_feedback as rf

        monkeypatch.setattr(rf, "post_to_service", lambda *a, **k: False)
        log = tmp_path / "fb.jsonl"
        monkeypatch.setenv("RISKRADAR_FEEDBACK_LOG_PATH", str(log))
        assert rf.main(["no", "--date", "2026-09-29"]) == 0
        fb = last_feedback(log)
        assert fb is not None
        assert fb["vote"] == "no"
        assert fb["et_date"] == "2026-09-29"
        assert fb["source"] == "cli-offline"

    def test_offline_fallback_emoji_appends_canonical_word(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ):
        import scripts.record_feedback as rf

        monkeypatch.setattr(rf, "post_to_service", lambda *a, **k: False)
        log = tmp_path / "fb.jsonl"
        monkeypatch.setenv("RISKRADAR_FEEDBACK_LOG_PATH", str(log))
        assert rf.main(["\U0001f44d", "--date", "2026-10-08"]) == 0
        fb = last_feedback(log)
        assert fb is not None
        assert fb["vote"] == "yes"
        assert fb["source"] == "cli-offline"

    def test_invalid_choice_rejected_by_argparse(
        self, monkeypatch: pytest.MonkeyPatch
    ):
        import scripts.record_feedback as rf

        monkeypatch.setattr(rf, "post_to_service", lambda *a, **k: True)
        with pytest.raises(SystemExit):
            rf.main(["maybe"])
