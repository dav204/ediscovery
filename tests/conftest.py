"""Shared fixtures. Hard rule: no network in tests, ever.

The mock Anthropic batch client lands in P2; the socket guard lives here from
day one so a network call can never sneak into the suite.
"""

import socket

import pytest


@pytest.fixture(autouse=True)
def _no_network(monkeypatch):
    def _blocked(*args, **kwargs):
        raise RuntimeError("network access is forbidden in tests")

    monkeypatch.setattr(socket.socket, "connect", _blocked)
