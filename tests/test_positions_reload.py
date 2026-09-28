"""W2 positions hot-reload: load, validate, and swap holdings safely."""

from pathlib import Path

import pytest

from src.config import Settings
from src.models import Position
from src.positions import (
    PositionsLoadError,
    load_positions,
    positions_signature,
    reload_positions_if_changed,
    validate_positions,
)

REAL_HOLDINGS = """\
# Saved by Philip from Autopilot HQ.
portfolio_positions:
  - symbol: NVDA
    weight: 0.10
  - symbol: TSLA
    weight: 0.20
  - symbol: META
    weight: 0.20
  - symbol: SPCX
    weight: 0.20
  - symbol: GOOG
    weight: 0.10
  - symbol: PLTR
    weight: 0.20
"""


def _write(tmp_path: Path, text: str) -> Path:
    p = tmp_path / "positions.yaml"
    p.write_text(text)
    return p


def _book(*symbols_weights: tuple[str, float]) -> list[Position]:
    return [Position(symbol=s, weight=w) for s, w in symbols_weights]


def _bump_mtime(p: Path) -> None:
    """Force a signature change without waiting for clock granularity."""
    st = p.stat()
    import os

    os.utime(p, ns=(st.st_atime_ns, st.st_mtime_ns + 1_000_000))


class TestLoadPositions:
    def test_loads_real_holdings_schema(self, tmp_path: Path):
        positions = load_positions(_write(tmp_path, REAL_HOLDINGS))
        assert [p.symbol for p in positions] == [
            "NVDA", "TSLA", "META", "SPCX", "GOOG", "PLTR",
        ]
        assert sum(p.weight for p in positions) == pytest.approx(1.0)

    def test_missing_file_raises(self, tmp_path: Path):
        with pytest.raises(PositionsLoadError, match="not found"):
            load_positions(tmp_path / "absent.yaml")

    def test_malformed_yaml_raises(self, tmp_path: Path):
        with pytest.raises(PositionsLoadError, match="not valid YAML"):
            load_positions(_write(tmp_path, "portfolio_positions: [oops"))

    def test_missing_key_raises(self, tmp_path: Path):
        with pytest.raises(PositionsLoadError, match="portfolio_positions"):
            load_positions(_write(tmp_path, "other: []\n"))

    def test_negative_weight_row_raises(self, tmp_path: Path):
        text = "portfolio_positions:\n  - symbol: SPY\n    weight: -0.5\n"
        with pytest.raises(PositionsLoadError, match="row 0 invalid"):
            load_positions(_write(tmp_path, text))

    def test_consistent_with_settings_from_yaml_parser(self, tmp_path: Path):
        _write(tmp_path, REAL_HOLDINGS)
        (tmp_path / "settings.yaml").write_text("host: 127.0.0.1\n")
        via_settings = Settings.from_yaml(tmp_path / "settings.yaml")
        via_loader = load_positions(tmp_path / "positions.yaml")
        assert [p.symbol for p in via_loader] == [
            r["symbol"] for r in via_settings.portfolio_positions
        ]
        assert [p.weight for p in via_loader] == [
            r["weight"] for r in via_settings.portfolio_positions
        ]


class TestValidatePositions:
    def test_real_book_valid(self):
        validate_positions(_book(("SPY", 1.0)))

    def test_sum_within_tolerance_valid(self):
        validate_positions(_book(("SPY", 0.6), ("TLT", 0.4)), sum_tolerance=0.05)

    def test_sum_out_of_tolerance_refused(self):
        with pytest.raises(PositionsLoadError, match="sum to 0.8000"):
            validate_positions(_book(("SPY", 0.6), ("TLT", 0.2)))

    def test_duplicate_symbols_refused(self):
        with pytest.raises(PositionsLoadError, match="duplicate symbols: SPY"):
            validate_positions(_book(("SPY", 0.5), ("SPY", 0.5)))

    def test_empty_refused(self):
        with pytest.raises(PositionsLoadError, match="empty"):
            validate_positions([])

    def test_zero_weight_refused(self):
        with pytest.raises(PositionsLoadError, match="non-positive"):
            validate_positions(_book(("SPY", 1.0), ("TLT", 0.0)))


class TestReloadIfChanged:
    def test_unchanged_file_keeps_book(self, tmp_path: Path):
        p = _write(tmp_path, REAL_HOLDINGS)
        sig = positions_signature(p)
        current = _book(("SPY", 1.0))
        out = reload_positions_if_changed(p, sig, current)
        assert (out.changed, out.applied) == (False, False)
        assert out.positions is current

    def test_valid_edit_applies(self, tmp_path: Path):
        p = _write(tmp_path, "portfolio_positions:\n  - symbol: SPY\n    weight: 1.0\n")
        current = _book(("SPY", 1.0))
        stale = positions_signature(p)
        p.write_text(REAL_HOLDINGS)
        _bump_mtime(p)
        out = reload_positions_if_changed(p, stale, current)
        assert (out.changed, out.applied) == (True, True)
        assert [q.symbol for q in out.positions] == ["NVDA", "TSLA", "META", "SPCX", "GOOG", "PLTR"]
        assert out.signature != stale

    def test_invalid_edit_keeps_current_and_does_not_reparse(
        self, tmp_path: Path
    ):
        p = _write(tmp_path, "portfolio_positions:\n  - symbol: SPY\n    weight: 1.0\n")
        stale = positions_signature(p)
        current = _book(("SPY", 1.0))
        # Sum drifts to 0.7 — refused.
        p.write_text(
            "portfolio_positions:\n  - symbol: SPY\n    weight: 0.5\n"
            "  - symbol: TLT\n    weight: 0.2\n"
        )
        _bump_mtime(p)
        refused = reload_positions_if_changed(p, stale, current)
        assert (refused.changed, refused.applied) == (True, False)
        assert refused.positions is current
        assert "0.7000" in refused.reason
        # Next cycle with the SAME bad file: signature already recorded, no
        # re-parse storm.
        again = reload_positions_if_changed(p, refused.signature, current)
        assert (again.changed, again.applied) == (False, False)

    def test_fixed_after_refused_reloads_normally(self, tmp_path: Path):
        p = _write(tmp_path, "portfolio_positions:\n  - symbol: SPY\n    weight: 1.0\n")
        current = _book(("SPY", 1.0))
        bad_sig = positions_signature(p)
        p.write_text("portfolio_positions:\n  - symbol: SPY\n    weight: 0.4\n")
        _bump_mtime(p)
        refused = reload_positions_if_changed(p, bad_sig, current)
        assert refused.applied is False
        p.write_text("portfolio_positions:\n  - symbol: TLT\n    weight: 1.0\n")
        _bump_mtime(p)
        fixed = reload_positions_if_changed(p, refused.signature, current)
        assert (fixed.changed, fixed.applied) == (True, True)
        assert [q.symbol for q in fixed.positions] == ["TLT"]

    def test_deleted_file_keeps_current(self, tmp_path: Path):
        p = _write(tmp_path, "portfolio_positions:\n  - symbol: SPY\n    weight: 1.0\n")
        sig = positions_signature(p)
        current = _book(("SPY", 1.0))
        p.unlink()
        out = reload_positions_if_changed(p, sig, current)
        assert out.signature is None
        assert out.positions is current

    def test_created_after_absent_applies(self, tmp_path: Path):
        p = tmp_path / "positions.yaml"
        current: list[Position] = []
        p.write_text(REAL_HOLDINGS)
        out = reload_positions_if_changed(p, None, current)
        assert (out.changed, out.applied) == (True, True)
        assert len(out.positions) == 6


class TestMainWiring:
    def test_compute_loop_wired_for_hot_reload(self):
        """Source guard: the live loop must consult the reload helper."""
        source = (Path(__file__).resolve().parents[1] / "src" / "main.py").read_text()
        assert "reload_positions_if_changed(" in source
        assert "positions_signature(" in source
        # A refused or applied reload must never orphan the loop's book:
        # the applied branch writes state["positions"] before the loop reads it.
        assert 'state["positions"] = outcome.positions' in source
