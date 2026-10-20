from __future__ import annotations

import contextlib
import http.client
import json
import socket
import struct
import threading
import time
from pathlib import Path

import pytest

from comsol_mcp import _control_daemon as daemon_module
from comsol_mcp._control_daemon import ControlDaemon


TOKEN = "test-only-loopback-token-7e3500"


@contextlib.contextmanager
def _running_listener(home: Path, monkeypatch, *, token: str = TOKEN):
    servers = []

    class CapturedServer(daemon_module._StdlibThreadingHTTPServer):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            self.request_done = threading.Event()
            servers.append(self)

        def process_request_thread(self, request, client_address):
            try:
                return super().process_request_thread(request, client_address)
            finally:
                self.request_done.set()

    monkeypatch.setattr(daemon_module, "ThreadingHTTPServer", CapturedServer)
    daemon = ControlDaemon(home)
    listener = threading.Thread(
        target=daemon_module._serve_control_listener,
        args=(home, daemon, token, None, None, None),
        name="synthetic-control-listener",
        daemon=True,
    )
    listener.start()
    endpoint_path = home / "control.json"
    deadline = time.monotonic() + 5
    while not endpoint_path.is_file() and time.monotonic() < deadline:
        time.sleep(0.01)
    if not endpoint_path.is_file() or not servers:
        raise RuntimeError("synthetic control listener did not publish its loopback endpoint")
    endpoint = json.loads(endpoint_path.read_text(encoding="utf-8"))
    try:
        yield daemon, servers[0], endpoint
    finally:
        servers[0].shutdown()
        listener.join(timeout=5)
        servers[0].server_close()
        daemon.close()
        assert not listener.is_alive()


def _post(endpoint, body: bytes, *, token: str = TOKEN):
    connection = http.client.HTTPConnection("127.0.0.1", endpoint["port"], timeout=5)
    try:
        connection.request(
            "POST", "/rpc", body=body,
            headers={"Authorization": "Bearer " + token, "Content-Type": "application/json"},
        )
        response = connection.getresponse()
        return response.status, response.read()
    finally:
        connection.close()


def test_real_loopback_dispatch_exception_logs_trace_without_request_secrets(tmp_path, monkeypatch):
    raw_body_canary = "PRIVATE-REQUEST-BODY-CANARY"
    token_canary = "PRIVATE-AUTH-TOKEN-CANARY"
    calls = []
    with _running_listener(tmp_path, monkeypatch, token=token_canary) as (daemon, _server, endpoint):
        def failing_dispatch(request):
            calls.append(request)
            raise RuntimeError("synthetic dispatch trace marker")

        daemon.dispatch = failing_dispatch
        body = json.dumps({"operation": "synthetic", "private": raw_body_canary}).encode()
        status, response = _post(endpoint, body, token=token_canary)
        assert status == 500
        assert b"internal control error" in response
        assert len(calls) == 1 and calls[0]["private"] == raw_body_canary
        log = (tmp_path / "control-errors.log").read_text(encoding="utf-8")
        assert "RuntimeError: synthetic dispatch trace marker" in log
        assert raw_body_canary not in log and token_canary not in log


def test_real_loopback_strict_json_response_failure_logs_and_keeps_500(tmp_path, monkeypatch):
    calls = []
    with _running_listener(tmp_path, monkeypatch) as (daemon, _server, endpoint):
        def nonfinite_response(request):
            calls.append(request)
            return {"success": True, "value": float("nan")}

        daemon.dispatch = nonfinite_response
        status, response = _post(endpoint, b'{"operation":"synthetic"}')
        assert status == 500 and b"internal control error" in response
        assert len(calls) == 1
        log = (tmp_path / "control-errors.log").read_text(encoding="utf-8")
        assert "ValueError: Out of range float values are not JSON compliant" in log


def test_real_loopback_diagnostic_write_failure_keeps_one_dispatch_and_500(tmp_path, monkeypatch):
    calls = []
    log_attempts = []
    with _running_listener(tmp_path, monkeypatch) as (daemon, _server, endpoint):
        def failing_dispatch(request):
            calls.append(request)
            raise RuntimeError("synthetic dispatch with failed logger")

        def failing_logger():
            log_attempts.append("called")
            raise OSError("synthetic private log sink failure")

        daemon.dispatch = failing_dispatch
        daemon._log_exception = failing_logger
        status, response = _post(endpoint, b'{"operation":"synthetic"}')
        assert status == 500 and b"internal control error" in response
        assert len(calls) == 1 and log_attempts == ["called"]


def test_real_loopback_disconnect_keeps_original_single_dispatch_behavior(tmp_path, monkeypatch):
    entered = threading.Event()
    release = threading.Event()
    calls = []
    log_attempts = []
    with _running_listener(tmp_path, monkeypatch) as (daemon, server, endpoint):
        def delayed_dispatch(request):
            calls.append(request)
            entered.set()
            if not release.wait(5):
                raise TimeoutError("test dispatch release timed out")
            return {"success": True}

        daemon.dispatch = delayed_dispatch
        daemon._log_exception = lambda: log_attempts.append("called")
        body = b'{"operation":"synthetic"}'
        request = (
            b"POST /rpc HTTP/1.1\r\nHost: 127.0.0.1\r\n"
            + f"Authorization: Bearer {TOKEN}\r\n".encode()
            + f"Content-Length: {len(body)}\r\nConnection: close\r\n\r\n".encode()
            + body
        )
        client = socket.create_connection(("127.0.0.1", endpoint["port"]), timeout=5)
        client.sendall(request)
        try:
            assert entered.wait(5), "listener did not reach the one dispatch"
            client.setsockopt(socket.SOL_SOCKET, socket.SO_LINGER, struct.pack("ii", 1, 0))
        finally:
            client.close()
            release.set()
        assert server.request_done.wait(5), "loopback handler did not reach a terminal response path"
        assert len(calls) == 1
        assert log_attempts == []
