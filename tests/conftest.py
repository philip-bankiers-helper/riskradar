"""Test-suite safety gates."""

import socket

import pytest


@pytest.fixture(autouse=True)
def block_network_in_blocking_suite(monkeypatch: pytest.MonkeyPatch, request: pytest.FixtureRequest):
    """Fail closed if an unmarked blocking test attempts a real socket connection."""
    if request.node.get_closest_marker("network"):
        return

    def blocked(*args, **kwargs):
        raise RuntimeError("network access is forbidden in the blocking test suite")

    monkeypatch.setattr(socket.socket, "connect", blocked)
    monkeypatch.setattr(socket.socket, "connect_ex", blocked)
