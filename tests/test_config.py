"""Configuration boundary tests."""

from pathlib import Path

import pytest

from src.config import Settings


def test_default_host_is_loopback():
    assert Settings().host == "127.0.0.1"


def test_yaml_rejects_secret_and_telegram_routing_keys(tmp_path: Path):
    settings_path = tmp_path / "settings.yaml"
    settings_path.write_text("telegram_bot_token: forbidden\n")
    with pytest.raises(ValueError, match="environment variables"):
        Settings.from_yaml(settings_path)


def test_positions_are_loaded_from_separate_file(tmp_path: Path):
    settings_path = tmp_path / "settings.yaml"
    settings_path.write_text("host: 127.0.0.1\n")
    (tmp_path / "positions.yaml").write_text(
        "portfolio_positions:\n  - symbol: SPY\n    weight: 1.0\n"
    )
    settings = Settings.from_yaml(settings_path)
    assert settings.portfolio_positions == [{"symbol": "SPY", "weight": 1.0}]


def test_unprefixed_telegram_environment_variables(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "synthetic-test-value")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "-100123")
    monkeypatch.setenv("TELEGRAM_THREAD_ID", "456")
    settings = Settings()
    assert settings.telegram_bot_token == "synthetic-test-value"
    assert settings.telegram_chat_id == "-100123"
    assert settings.telegram_thread_id == 456


def test_environment_overrides_yaml(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    settings_path = tmp_path / "settings.yaml"
    settings_path.write_text("port: 8000\n")
    monkeypatch.setenv("RISKRADAR_PORT", "8001")
    assert Settings.from_yaml(settings_path).port == 8001
