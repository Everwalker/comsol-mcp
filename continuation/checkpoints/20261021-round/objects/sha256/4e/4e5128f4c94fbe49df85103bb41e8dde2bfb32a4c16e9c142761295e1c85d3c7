"""Real listener regression for the observed control-startup DNS stall."""
import socket
from http.server import BaseHTTPRequestHandler

import pytest

from comsol_mcp._control_daemon import ThreadingHTTPServer


def test_private_control_listener_never_uses_reverse_dns(monkeypatch):
    def blocked_dns(*_args):
        raise AssertionError("reverse DNS must not precede private endpoint publication")

    monkeypatch.setattr(socket, "getfqdn", blocked_dns)
    with ThreadingHTTPServer(("127.0.0.1", 0), BaseHTTPRequestHandler) as server:
        address, port = server.socket.getsockname()
        assert address == server.server_name == "127.0.0.1"
        assert port == server.server_port and port > 0
        with socket.create_connection((address, port), timeout=1):
            pass


@pytest.mark.parametrize("address", ["0.0.0.0", "localhost", "::1"])
def test_private_control_listener_rejects_noncanonical_bind_before_socket_bind(address, monkeypatch):
    from socketserver import TCPServer

    def forbidden_bind(*_args):
        raise AssertionError("a noncanonical control address reached OS bind")

    monkeypatch.setattr(TCPServer, "server_bind", forbidden_bind)
    with pytest.raises(ValueError, match="numeric IPv4 loopback"):
        ThreadingHTTPServer((address, 0), BaseHTTPRequestHandler)
