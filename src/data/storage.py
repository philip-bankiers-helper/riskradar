"""DuckDB storage layer for Risk Radar."""

from __future__ import annotations

import logging
from datetime import datetime
from pathlib import Path

import duckdb
import pandas as pd

logger = logging.getLogger(__name__)


class Storage:
    """DuckDB-based persistent storage."""

    def __init__(self, db_path: str | Path):
        self.db_path = str(db_path)
        Path(db_path).parent.mkdir(parents=True, exist_ok=True)
        self.conn = duckdb.connect(self.db_path)
        self._init_tables()

    def _init_tables(self) -> None:
        """Create tables if they don't exist."""
        self.conn.execute("""
            CREATE TABLE IF NOT EXISTS heat_score_history (
                timestamp TIMESTAMPTZ NOT NULL,
                score DOUBLE NOT NULL,
                level VARCHAR NOT NULL,
                absorption_ratio DOUBLE,
                turbulence DOUBLE,
                diversification_ratio DOUBLE,
                factor_hhi DOUBLE,
                avg_correlation DOUBLE,
                dominant_factor VARCHAR,
                top_correlated_pair VARCHAR
            )
        """)

        self.conn.execute("""
            CREATE TABLE IF NOT EXISTS factor_exposures (
                timestamp TIMESTAMPTZ NOT NULL,
                symbol VARCHAR NOT NULL,
                factor_name VARCHAR NOT NULL,
                beta DOUBLE NOT NULL,
                t_stat DOUBLE,
                r_squared DOUBLE
            )
        """)

        self.conn.execute("""
            CREATE TABLE IF NOT EXISTS positions (
                symbol VARCHAR PRIMARY KEY,
                weight DOUBLE NOT NULL,
                shares DOUBLE DEFAULT 0,
                entry_price DOUBLE,
                entry_date TIMESTAMPTZ,
                updated_at TIMESTAMPTZ DEFAULT NOW()
            )
        """)

        self.conn.execute("""
            CREATE TABLE IF NOT EXISTS macro_data (
                date DATE NOT NULL,
                series_id VARCHAR NOT NULL,
                value DOUBLE NOT NULL,
                PRIMARY KEY (date, series_id)
            )
        """)

        logger.info("DuckDB tables initialized at %s", self.db_path)

    def save_heat_score(self, heat_data: dict) -> None:
        """Persist a heat score snapshot."""
        self.conn.execute("""
            INSERT INTO heat_score_history VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, [
            heat_data.get("timestamp", datetime.utcnow()),
            heat_data["score"],
            heat_data["level"],
            heat_data.get("absorption_ratio"),
            heat_data.get("turbulence"),
            heat_data.get("diversification_ratio"),
            heat_data.get("factor_hhi"),
            heat_data.get("avg_correlation"),
            heat_data.get("dominant_factor"),
            heat_data.get("top_correlated_pair"),
        ])

    def get_heat_history(
        self, start: datetime | None = None, end: datetime | None = None, limit: int = 1000
    ) -> pd.DataFrame:
        """Retrieve heat score history."""
        query = "SELECT * FROM heat_score_history"
        conditions = []
        params = []

        if start:
            conditions.append("timestamp >= ?")
            params.append(start)
        if end:
            conditions.append("timestamp <= ?")
            params.append(end)

        if conditions:
            query += " WHERE " + " AND ".join(conditions)
        query += f" ORDER BY timestamp DESC LIMIT {limit}"

        return self.conn.execute(query, params).fetchdf()

    def save_positions(self, positions: list[dict]) -> None:
        """Upsert portfolio positions."""
        for pos in positions:
            self.conn.execute("""
                INSERT OR REPLACE INTO positions (symbol, weight, shares, entry_price, entry_date, updated_at)
                VALUES (?, ?, ?, ?, ?, NOW())
            """, [
                pos["symbol"],
                pos["weight"],
                pos.get("shares", 0),
                pos.get("entry_price"),
                pos.get("entry_date"),
            ])

    def get_positions(self) -> pd.DataFrame:
        """Get current positions."""
        return self.conn.execute("SELECT * FROM positions ORDER BY weight DESC").fetchdf()

    def delete_position(self, symbol: str) -> bool:
        """Remove a position. Returns True if it existed."""
        result = self.conn.execute("DELETE FROM positions WHERE symbol = ?", [symbol])
        return result.fetchone() is not None

    def close(self) -> None:
        """Close the database connection."""
        self.conn.close()
