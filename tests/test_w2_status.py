"""W2 verdict tests: evidence fields on daily records + w2_status().

The mechanical flip instrument for W2 (mirrors w1_status/w3_status):
scheduled daily summaries must carry positions / top-3 attributions /
an action, the scored book must match the repo positions file, and
Philip's tap must be recorded AND surfaced by a later summary before
the increment can flip.
"""

from __future__ import annotations

import json
from datetime import date, datetime, timezone
from pathlib import Path

from src.alerts.alert_manager import summary_action_line
from src.delivery_log import (
    FIRST_W2_EVIDENCE,
    append_delivery,
    read_deliveries,
    w2_status,
)
from src.models import PortfolioRecommendation, TradeAction
from src.delivery_log import ET

PROJECT_ROOT = Path(__file__).resolve().parent.parent

BOOK = ["META", "NVDA", "PLTR", "SPCX", "TSLA"]


def _daily(path, day: date, *, scheduled=True, payload=True, message_id=100):
    append_delivery(
        path,
        tier="daily_summary",
        message_id=message_id,
        heat_score=0.21,
        scheduled=scheduled,
        telegram_date=int(
            datetime(day.year, day.month, day.day, 20, 30, tzinfo=timezone.utc).timestamp()
        ),
        positions=sorted(BOOK) if payload else None,
        top_attributions=[("PLTR", 0.26), ("TSLA", 0.21), ("META", 0.18)]
        if payload
        else None,
        action="HOLD: hold all positions (no changes recommended)" if payload else None,
        now=datetime(day.year, day.month, day.day, 16, 30, tzinfo=ET),
    )


def _tap(path, vote: str, day: date, hour_et: int, minute_et: int = 0):
    tapped = datetime(day.year, day.month, day.day, hour_et, minute_et, tzinfo=ET)
    path.write_text(
        json.dumps(
            {
                "et_date": day.isoformat(),
                "vote": vote,
                "tapped_at": tapped.astimezone(timezone.utc).isoformat(),
                "source": "reply",
            }
        )
        + "\n",
        encoding="utf-8",
    )


def test_append_delivery_records_w2_fields(tmp_path):
    log = tmp_path / "delivery.jsonl"
    _daily(log, date(2026, 10, 1))
    append_delivery(
        log,
        tier="weekly_scorecard",
        message_id=999,
        scheduled=True,
        telegram_date=1234567890,
    )
    records = read_deliveries(log)
    daily, weekly = records
    assert daily["positions"] == sorted(BOOK)
    assert daily["top_attributions"] == [["PLTR", 0.26], ["TSLA", 0.21], ["META", 0.18]]
    assert daily["action"].startswith("HOLD:")
    # Non-daily tiers keep their schema — no W2 keys leaked in.
    assert "positions" not in weekly
    assert "top_attributions" not in weekly
    assert "action" not in weekly


def test_w2_met_on_full_path(tmp_path):
    log = tmp_path / "delivery.jsonl"
    _daily(log, date(2026, 10, 1), message_id=101)
    _daily(log, date(2026, 10, 2), message_id=102)
    feedback = tmp_path / "feedback.jsonl"
    _tap(feedback, "yes", date(2026, 10, 1), 10)  # before that day's 16:30 send

    verdict = w2_status(log, feedback, expected_positions=sorted(BOOK))
    assert verdict["w2_met"] is True
    assert verdict["reasons"] == []
    assert verdict["summaries_with_payload"] == 2
    assert verdict["book_matches_repo"] is True
    assert verdict["surfaced_message_id"] == 102
    assert "book==repo" in verdict["evidence"]
    assert "feedback yes 2026-10-01 surfaced in message 102" in verdict["evidence"]


def test_w2_waiting_for_first_tap(tmp_path):
    log = tmp_path / "delivery.jsonl"
    _daily(log, date(2026, 10, 1), message_id=101)
    feedback = tmp_path / "feedback.jsonl"  # absent — no tap yet

    verdict = w2_status(log, feedback)
    assert verdict["w2_met"] is False
    assert any("waiting for Philip" in r for r in verdict["reasons"])
    assert verdict["feedback"] is None
    assert "no tap recorded yet" in verdict["evidence"]


def test_w2_tap_same_day_after_send_not_surfaced(tmp_path):
    log = tmp_path / "delivery.jsonl"
    _daily(log, date(2026, 10, 1), message_id=101)
    feedback = tmp_path / "feedback.jsonl"
    _tap(feedback, "yes", date(2026, 10, 1), 18)  # after the 16:30 send

    verdict = w2_status(log, feedback)
    assert verdict["w2_met"] is False
    assert any("not yet surfaced" in r for r in verdict["reasons"])


def test_w2_tap_next_day_needs_later_summary(tmp_path):
    log = tmp_path / "delivery.jsonl"
    _daily(log, date(2026, 10, 1), message_id=101)
    feedback = tmp_path / "feedback.jsonl"
    _tap(feedback, "no", date(2026, 10, 2), 9)

    verdict = w2_status(log, feedback)
    assert verdict["w2_met"] is False
    assert any("not yet surfaced" in r for r in verdict["reasons"])

    _daily(log, date(2026, 10, 2), message_id=102)
    verdict = w2_status(log, feedback)
    assert verdict["w2_met"] is True
    assert verdict["surfaced_message_id"] == 102


def test_w2_bare_scheduled_record_after_clamp_blocks(tmp_path):
    log = tmp_path / "delivery.jsonl"
    _daily(log, date(2026, 10, 1), payload=False, message_id=101)
    _daily(log, date(2026, 10, 2), message_id=102)
    feedback = tmp_path / "feedback.jsonl"
    _tap(feedback, "yes", date(2026, 10, 1), 10)

    verdict = w2_status(log, feedback)
    assert verdict["w2_met"] is False
    assert any("missing W2 payload" in r for r in verdict["reasons"])


def test_w2_manual_bare_records_never_count(tmp_path):
    log = tmp_path / "delivery.jsonl"
    _daily(log, date(2026, 10, 1), message_id=101)
    _daily(log, date(2026, 10, 1), scheduled=False, payload=False, message_id=555)
    feedback = tmp_path / "feedback.jsonl"
    _tap(feedback, "yes", date(2026, 10, 1), 10)

    verdict = w2_status(log, feedback)
    assert verdict["w2_met"] is True


def test_w2_pre_clamp_records_without_payload_do_not_block(tmp_path):
    log = tmp_path / "delivery.jsonl"
    _daily(log, date(2026, 9, 29), payload=False, message_id=99)  # before clamp
    _daily(log, FIRST_W2_EVIDENCE, message_id=101)
    feedback = tmp_path / "feedback.jsonl"
    _tap(feedback, "yes", FIRST_W2_EVIDENCE, 10)

    verdict = w2_status(log, feedback, first_expected=FIRST_W2_EVIDENCE)
    assert verdict["w2_met"] is True
    assert verdict["summaries_expected"] == 1


def test_w2_book_mismatch_blocks(tmp_path):
    log = tmp_path / "delivery.jsonl"
    _daily(log, date(2026, 10, 1), message_id=101)
    _daily(log, date(2026, 10, 2), message_id=102)
    feedback = tmp_path / "feedback.jsonl"
    _tap(feedback, "yes", date(2026, 10, 1), 10)

    verdict = w2_status(log, feedback, expected_positions=["SPY"])
    assert verdict["w2_met"] is False
    assert verdict["book_matches_repo"] is False
    assert any("!= repo positions" in r for r in verdict["reasons"])


def test_summary_action_line_single_source_of_truth():
    assert (
        summary_action_line(None)
        == "HOLD: hold all positions (no recommendation signal)"
    )
    calm = PortfolioRecommendation(
        summary="No actions needed", current_heat=0.2, estimated_heat_after=0.2
    )
    assert (
        summary_action_line(calm)
        == "HOLD: hold all positions (no changes recommended)"
    )
    act = TradeAction(
        action="reduce",
        symbol="NVDA",
        current_weight=0.1,
        target_weight=0.05,
        delta=-0.05,
        impact_estimate=12.0,
        priority=1,
        reason="top heat contributor",
        urgency="today",
    )
    active = PortfolioRecommendation(
        summary="Trim NVDA", current_heat=0.6, estimated_heat_after=0.5, actions=[act]
    )
    assert summary_action_line(active) == "TODAY: reduce NVDA (top heat contributor)"


def test_send_path_and_streak_script_wired():
    manager_src = (
        PROJECT_ROOT / "src" / "alerts" / "alert_manager.py"
    ).read_text(encoding="utf-8")
    # The daily-summary record call passes the W2 evidence fields.
    assert "action=summary_action_line(recommendations)" in manager_src
    assert "top_attributions=evidence_attributions" in manager_src
    assert "positions=evidence_positions" in manager_src
    # The render keeps its byte-identical HOLD lines via the helper.
    assert 'msg += f"\\n  {summary_action_line(recommendations)}"' in manager_src

    streak_src = (PROJECT_ROOT / "scripts" / "delivery_streak.py").read_text(
        encoding="utf-8"
    )
    assert "w2_status" in streak_src
    assert '"w2": _w2_block(log_path)' in streak_src
