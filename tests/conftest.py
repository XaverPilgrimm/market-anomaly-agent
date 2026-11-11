"""Suite-wide fixtures."""

import socket
from typing import Any, NoReturn

import pytest

NETWORK_BLOCKED = "network access is blocked in unit tests (constitution Principle VIII)"


@pytest.fixture(autouse=True)
def _no_network(request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch) -> None:
    """Make every connection attempt and host-name lookup raise in unit tests. Integration
    tests are exempt: they reach the local PostgreSQL."""
    if request.node.get_closest_marker("integration") is not None:
        return

    def blocked(*args: Any, **kwargs: Any) -> NoReturn:
        raise RuntimeError(NETWORK_BLOCKED)

    monkeypatch.setattr(socket.socket, "connect", blocked)
    monkeypatch.setattr(socket.socket, "connect_ex", blocked)
    monkeypatch.setattr(socket, "getaddrinfo", blocked)
