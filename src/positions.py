"""Portfolio positions loading, validation, and hot-reload (W2).

Holdings in ``config/positions.yaml`` must drive the score. The service
loads positions once at startup; this module adds a safe hot-reload so a
holdings update on disk takes effect on the next compute cycle without a
redeploy. Invalid or partial edits are refused atomically — the service
keeps scoring the previous book and says why, never a half-applied mix.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path

import yaml

from src.models import Position

logger = logging.getLogger(__name__)

# Weights must sum to ~1.0; hand-edited files say "approximately 1.0",
# so allow 5% drift either way before refusing the reload.
DEFAULT_SUM_TOLERANCE = 0.05

# (st_mtime_ns, st_size) — enough to detect any normal edit; None = absent.
PositionsSignature = tuple[int, int] | None


class PositionsLoadError(Exception):
    """Raised when a positions file cannot be parsed into Position models."""


@dataclass
class ReloadOutcome:
    """Result of one hot-reload check.

    ``positions`` is the book to score going forward (new if applied,
    unchanged otherwise). ``signature`` is the file signature observed this
    check — record it even when a reload is refused, so a malformed file is
    not re-parsed every cycle; the next successful edit changes the
    signature and reloads normally.
    """

    positions: list[Position]
    signature: PositionsSignature
    changed: bool
    applied: bool
    reason: str = ""
    symbols: list[str] = field(default_factory=list)


def positions_signature(path: Path | str) -> PositionsSignature:
    """Return a cheap change signature for a positions file, None if absent."""
    try:
        st = Path(path).stat()
    except OSError:
        return None
    return (st.st_mtime_ns, st.st_size)


def load_positions(path: Path | str) -> list[Position]:
    """Parse a positions.yaml-style file into Position models.

    Raises PositionsLoadError for a missing file, unparseable YAML, a
    missing ``portfolio_positions`` key, or rows that fail Position
    validation (e.g. negative weight, empty symbol).
    """
    p = Path(path)
    if not p.exists():
        raise PositionsLoadError(f"positions file not found: {p}")
    try:
        data = yaml.safe_load(p.read_text()) or {}
    except yaml.YAMLError as exc:
        raise PositionsLoadError(f"positions file is not valid YAML: {exc}") from exc
    rows = data.get("portfolio_positions")
    if not isinstance(rows, list) or not rows:
        raise PositionsLoadError(
            "positions file has no 'portfolio_positions' list"
        )
    positions: list[Position] = []
    for i, row in enumerate(rows):
        if not isinstance(row, dict):
            raise PositionsLoadError(f"position row {i} is not a mapping: {row!r}")
        try:
            positions.append(Position(**row))
        except Exception as exc:  # pydantic ValidationError et al.
            raise PositionsLoadError(f"position row {i} invalid: {exc}") from exc
    return positions


def validate_positions(
    positions: list[Position],
    sum_tolerance: float = DEFAULT_SUM_TOLERANCE,
) -> None:
    """Validate a candidate book; raise PositionsLoadError with the reason.

    Rules: at least one position, unique non-empty uppercase-able symbols,
    strictly positive weights, and a weight sum within tolerance of 1.0.
    """
    if not positions:
        raise PositionsLoadError("positions list is empty")
    symbols = [p.symbol.strip().upper() for p in positions]
    if any(not s for s in symbols):
        raise PositionsLoadError("a position has an empty symbol")
    dupes = sorted({s for s in symbols if symbols.count(s) > 1})
    if dupes:
        raise PositionsLoadError(f"duplicate symbols: {', '.join(dupes)}")
    if any(p.weight <= 0 for p in positions):
        raise PositionsLoadError("a position has non-positive weight")
    total = sum(p.weight for p in positions)
    if abs(total - 1.0) > sum_tolerance:
        raise PositionsLoadError(
            f"weights sum to {total:.4f}, expected ~1.0 "
            f"(tolerance {sum_tolerance}); refusing to score a mis-sized book"
        )


def reload_positions_if_changed(
    path: Path | str,
    last_signature: PositionsSignature,
    current: list[Position],
    sum_tolerance: float = DEFAULT_SUM_TOLERANCE,
) -> ReloadOutcome:
    """Reload holdings from ``path`` only if the file changed on disk.

    Never raises for file problems: a missing, malformed, or invalid file
    keeps ``current`` and reports the reason. ``applied`` is True only when
    a validated new book replaced ``current``.
    """
    signature = positions_signature(path)
    if signature == last_signature:
        return ReloadOutcome(
            positions=current,
            signature=signature,
            changed=False,
            applied=False,
            symbols=[p.symbol for p in current],
        )

    reason_prefix = "positions file changed"
    try:
        candidates = load_positions(path)
        validate_positions(candidates, sum_tolerance=sum_tolerance)
    except PositionsLoadError as exc:
        logger.warning(
            "%s but NOT applied — keeping %d current positions: %s",
            reason_prefix,
            len(current),
            exc,
        )
        return ReloadOutcome(
            positions=current,
            signature=signature,
            changed=True,
            applied=False,
            reason=str(exc),
            symbols=[p.symbol for p in current],
        )

    logger.info(
        "Positions hot-reloaded from %s: %d positions (%s)",
        path,
        len(candidates),
        ", ".join(f"{p.symbol} {p.weight:.0%}" for p in candidates),
    )
    return ReloadOutcome(
        positions=candidates,
        signature=signature,
        changed=True,
        applied=True,
        symbols=[p.symbol for p in candidates],
    )
