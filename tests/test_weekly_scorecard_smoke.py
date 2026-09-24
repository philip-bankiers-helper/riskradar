"""Offline tests for the weekly-scorecard readiness smoke (no network).

``smoke_verdict`` is pure: the tests grade rendered text (including a
render produced by the real ``build_scorecard_text``) without any
fetch. The script's live path is run manually, never in the suite.
"""

from __future__ import annotations

import re
from datetime import date
from pathlib import Path

import pandas as pd

from scripts.weekly_scorecard_smoke import smoke_verdict
from src.engine.weekly_scorecard import build_scorecard_text

SCRIPT_PATH = Path(__file__).parent.parent / "scripts" / "weekly_scorecard_smoke.py"


def _rendered_fixture() -> str:
    """Real renderer output: 200 daily bars containing one ~10% drawdown."""
    idx = pd.date_range("2026-06-01", periods=200, freq="D", tz="UTC")
    prices = (
        [100 + 0.1 * i for i in range(150)]
        + [115 - 0.6 * i for i in range(20)]
        + [103 + 0.2 * i for i in range(30)]
    )
    text = build_scorecard_text(pd.Series(prices, index=idx), None)
    assert text is not None
    return text


class TestSmokeVerdictFailures:
    def test_none_text_fails_with_skip_hint(self):
        code, lines = smoke_verdict(None)
        assert code == 1
        assert any("returned None" in line for line in lines)
        assert any("SKIP" in line for line in lines)

    def test_non_scorecard_text_fails(self):
        code, lines = smoke_verdict("heat score 42 — everything fine")
        assert code == 1
        assert any("not a scorecard" in line for line in lines)

    def test_header_without_window_line_fails(self):
        code, lines = smoke_verdict("WEEKLY LEAD-TIME SCORECARD — SPY\njunk")
        assert code == 1
        assert any("window" in line for line in lines)


class TestSmokeVerdictOk:
    def test_real_render_passes_and_reports_diagnostics(self):
        code, lines = smoke_verdict(_rendered_fixture(), today=date(2026, 12, 18))
        assert code == 0
        joined = "\n".join(lines)
        assert "window=2026-06-01→2026-12-17" in joined
        assert "episodes=1" in joined

    def test_zero_episode_render_is_valid(self):
        # Monotonic up-trend: no qualifying drawdown, still a valid post.
        idx = pd.date_range("2026-06-01", periods=200, freq="D", tz="UTC")
        text = build_scorecard_text(pd.Series([100 + 0.1 * i for i in range(200)], index=idx), None)
        assert text is not None
        code, lines = smoke_verdict(text, today=date(2026, 12, 18))
        assert code == 0
        assert "episodes=0" in "\n".join(lines)

    def test_age_exactly_at_threshold_still_passes(self):
        code, _lines = smoke_verdict(_rendered_fixture(), today=date(2026, 12, 27))
        assert code == 0  # window_end 2026-12-17 + 10 days == threshold

    def test_stale_window_fails(self):
        code, lines = smoke_verdict(_rendered_fixture(), today=date(2026, 12, 29))
        assert code == 1
        assert any("stale" in line for line in lines)
        # The failure must still surface the diagnostics it gathered.
        assert any(line.startswith("window=") for line in lines)


class TestSmokeCannotSend:
    def test_script_never_imports_alert_or_telegraph_paths(self):
        source = SCRIPT_PATH.read_text(encoding="utf-8")
        imported = [
            line.strip()
            for line in source.splitlines()
            if re.match(r"^\s*(from|import)\s+\S", line)
        ]
        assert imported, "expected imports to inspect"
        forbidden = ("alerts", "telegram", "telegraph", "httpx")
        for line in imported:
            assert not any(bad in line.lower() for bad in forbidden), line
