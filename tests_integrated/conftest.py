"""Fail fast on accidental networking in the offline application regression suite."""
import socket
import pytest

@pytest.fixture(autouse=True)
def deny_network(monkeypatch):
    def blocked(*args, **kwargs):
        raise AssertionError('Live network is forbidden in offline regression tests')
    monkeypatch.setattr(socket, 'create_connection', blocked)
    monkeypatch.setattr(socket.socket, 'connect', blocked)
