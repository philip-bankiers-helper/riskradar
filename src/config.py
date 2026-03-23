"""Configuration management for Risk Radar."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

import yaml
from pydantic import Field
from pydantic_settings import BaseSettings

logger = logging.getLogger(__name__)

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_CONFIG = PROJECT_ROOT / "config" / "settings.yaml"


class FactorETF(BaseSettings):
    """Single factor ETF definition."""
    name: str
    symbol: str
    description: str = ""


class Settings(BaseSettings):
    """Application settings — loaded from YAML + env vars."""

    # API Keys
    polygon_api_key: str = ""
    fred_api_key: str = ""
    telegram_bot_token: str = ""
    telegram_chat_id: str = ""

    # Data
    factor_etfs: list[dict[str, str]] = Field(default_factory=list)
    portfolio_positions: list[dict[str, Any]] = Field(default_factory=list)

    # Engine parameters
    rolling_window: int = 60
    ewma_span: int = 60
    ar_lookback: int = 252
    heat_update_interval_seconds: int = 60

    # Heat score weights
    heat_weight_absorption_ratio: float = 0.25
    heat_weight_turbulence: float = 0.20
    heat_weight_diversification: float = 0.20
    heat_weight_factor_hhi: float = 0.15
    heat_weight_avg_correlation: float = 0.20

    # Alert thresholds (calibrated from backtest: mean=0.565, std=0.210)
    heat_threshold_warm: float = 0.55
    heat_threshold_hot: float = 0.75
    heat_threshold_critical: float = 0.85
    heat_threshold_emergency: float = 0.93

    # Data pipeline
    data_source_priority: list[str] = Field(default_factory=lambda: ["polygon", "yfinance", "cache"])
    polygon_tier: str = "free"
    data_cache_ttl_seconds: int = 300

    # Alert cooldowns (minutes)
    alert_cooldown_daily_summary: int = 1440
    alert_cooldown_level_change: int = 30
    alert_cooldown_threshold_cross: int = 60
    alert_cooldown_regime_shift: int = 15
    alert_cooldown_emergency: int = 0

    # Storage
    duckdb_path: str = str(PROJECT_ROOT / "data" / "riskradar.duckdb")
    redis_url: str = "redis://localhost:6379"

    # Server
    host: str = "0.0.0.0"
    port: int = 8000

    model_config = {"env_prefix": "RISKRADAR_", "env_file": ".env"}

    @classmethod
    def from_yaml(cls, path: Path | str = DEFAULT_CONFIG) -> "Settings":
        """Load settings from YAML file, with env var overrides."""
        path = Path(path)
        data: dict[str, Any] = {}
        if path.exists():
            with open(path) as f:
                data = yaml.safe_load(f) or {}
            logger.info("Loaded config from %s", path)
        else:
            logger.warning("Config file not found: %s — using defaults", path)
        return cls(**data)


# Default factor ETFs
DEFAULT_FACTOR_ETFS = [
    {"name": "rates", "symbol": "TLT", "description": "Duration / rate sensitivity"},
    {"name": "credit", "symbol": "HYG", "description": "HY spread / deleveraging"},
    {"name": "dollar", "symbol": "UUP", "description": "DXY / FX translation"},
    {"name": "ai_tech", "symbol": "SMH", "description": "Semiconductor / AI beta"},
    {"name": "quality", "symbol": "QUAL", "description": "Quality factor crowding"},
    {"name": "momentum", "symbol": "MTUM", "description": "Momentum crowding"},
    {"name": "market", "symbol": "SPY", "description": "Beta baseline"},
    {"name": "low_vol", "symbol": "USMV", "description": "Defensive positioning"},
    {"name": "value", "symbol": "VTV", "description": "Value factor"},
]

# Default FRED series
DEFAULT_FRED_SERIES = {
    "DGS10": "10Y Treasury Yield",
    "DGS2": "2Y Treasury Yield",
    "BAMLH0A0HYM2": "HY Credit Spread (OAS)",
    "SOFR": "SOFR Overnight Rate",
    "DTWEXBGS": "Broad Dollar Index",
    "VIXCLS": "VIX",
    "T10YIE": "10Y Breakeven Inflation",
}
