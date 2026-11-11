"""The unit suite never reaches the network (constitution Principle VIII). The guard lives in
tests/conftest.py; these tests prove it is active for unit tests and absent for integration
tests, which may reach the local database."""

import socket

import pytest
import requests

BLOCKED = "network access is blocked in unit tests"


def test_opening_a_connection_fails() -> None:
    with pytest.raises(RuntimeError, match=BLOCKED):
        # 192.0.2.1 is TEST-NET-1 (RFC 5737), never routed: no real host is ever contacted.
        socket.create_connection(("192.0.2.1", 80), timeout=1)


def test_resolving_a_host_name_fails() -> None:
    with pytest.raises(RuntimeError, match=BLOCKED):
        socket.getaddrinfo("example.com", 443)


def test_an_http_request_fails_before_leaving_the_process() -> None:
    with pytest.raises(RuntimeError, match=BLOCKED):
        requests.get("http://192.0.2.1/", timeout=1)


ORIGINAL_CONNECT = socket.socket.connect


@pytest.mark.integration
def test_integration_tests_are_not_blocked() -> None:
    assert socket.socket.connect is ORIGINAL_CONNECT
