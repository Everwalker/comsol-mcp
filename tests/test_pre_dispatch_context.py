"""Pure in-memory controls for the independently portable F2 subset."""
from __future__ import annotations

import json
import errno
import traceback
import unittest
from unittest.mock import patch

from comsol_mcp import _java_worker


SECRET = "R16_PRIVATE_SENTINEL_9b1d7e3a"


class MemoryStream:
    def __init__(self, *, fail_probe: bool = False) -> None:
        self.fail_probe = fail_probe
        self.writes: list[bytes] = []
        self.reads = 0

    def write(self, frame: bytes) -> int:
        raw = bytes(frame)
        self.writes.append(raw)
        return len(raw)

    def flush(self) -> None:
        return None

    def readline(self) -> bytes:
        self.reads += 1
        if self.reads == 1:
            return b'{"ok":true,"type":"authenticated"}\n'
        last = json.loads(self.writes[-1].decode("utf-8"))
        if last.get("type") == "source_binding_capabilities":
            if self.fail_probe:
                raise json.JSONDecodeError(SECRET, SECRET, 0)
            capability = {
                "ok": True,
                "type": "source_binding_capabilities",
                "request_id": last["request_id"],
                "status": "SUCCEEDED",
                "result": {
                    "source_binding_protocol": _java_worker._SOURCE_BINDING_PROTOCOL,
                    "worker_build_id": _java_worker._SOURCE_BINDING_WORKER_BUILD_ID,
                    "instance_id": "memory-instance",
                },
            }
            return (json.dumps(capability) + "\n").encode("utf-8")
        response = {
            "ok": True,
            "type": last.get("type"),
            "request_id": last.get("request_id"),
            "status": "SUCCEEDED",
            "result": "memory-result",
        }
        return (json.dumps(response) + "\n").encode("utf-8")

    def frame_types(self) -> list[str | None]:
        return [json.loads(frame.decode("utf-8")).get("type") for frame in self.writes]


class MemoryConnection:
    def __init__(self, stream: MemoryStream) -> None:
        self.stream = stream

    def __enter__(self) -> "MemoryConnection":
        return self

    def __exit__(self, *_args: object) -> bool:
        return False

    def settimeout(self, _timeout: float | None) -> None:
        return None

    def makefile(self, _mode: str) -> MemoryStream:
        return self.stream


class MemoryWorker(_java_worker.PersistentJavaWorker):
    @property
    def endpoint(self) -> tuple[str, int]:
        return "127.0.0.1", 32123


def make_worker() -> MemoryWorker:
    worker = MemoryWorker.__new__(MemoryWorker)
    worker._port = 32123
    worker._token = "in-memory-token"
    worker._known_requests = {}
    worker._sync_generation = lambda _reply: None
    return worker


def exception_material(exc: BaseException) -> str:
    material = [str(exc), repr(getattr(exc, "reply", None)),
                "".join(traceback.format_exception(type(exc), exc, exc.__traceback__))]
    seen: set[int] = set()
    cause = exc
    while id(cause) not in seen:
        seen.add(id(cause))
        material.extend((str(cause), repr(getattr(cause, "reply", None))))
        cause = cause.__cause__ or cause.__context__
        if cause is None:
            break
    return "\n".join(material)


class PreDispatchContextTests(unittest.TestCase):
    def run_request(self, *, require_source_binding: bool = False,
                    fail_probe: bool = False, fail_probe_correlation: bool = False):
        stream = MemoryStream(fail_probe=fail_probe)
        connection = MemoryConnection(stream)
        worker = make_worker()
        if fail_probe_correlation:
            def fail_validation(_probe, _reply):
                raise _java_worker.JavaWorkerError(SECRET)
            worker._validate_request_reply = fail_validation
        request = {"type": "code_compile" if require_source_binding else "call",
                   "request_id": "memory-request"}
        with patch.object(_java_worker.socket, "create_connection", return_value=connection) as connect:
            try:
                reply = worker._request(
                    request,
                    timeout_s=1.0,
                    require_source_binding=require_source_binding,
                )
            except BaseException as exc:
                return exc, stream, worker, connect.call_count
        return reply, stream, worker, connect.call_count

    def test_compile_probe_transport_failure_is_sanitized_and_remains_unknown(self) -> None:
        exc, stream, _worker, connect_count = self.run_request(
            require_source_binding=True,
            fail_probe=True,
        )
        self.assertIsInstance(exc, _java_worker.JavaWorkerError)
        self.assertIsNone(exc.__context__)
        self.assertIsNone(exc.__cause__)
        self.assertNotIn(SECRET, exception_material(exc))
        self.assertEqual(exc.reply.get("status"), "UNKNOWN")
        self.assertEqual(exc.reply.get("request_id"), "memory-request")
        self.assertEqual(exc.reply.get("type"), "code_compile")
        self.assertEqual(exc.reply.get("transport_stage"), "source_binding_probe_reply")
        self.assertEqual(exc.reply.get("reason"), "source_binding_probe_transport_failed")
        self.assertEqual(exc.reply.get("cause_type"), "JSONDecodeError")
        self.assertNotIn("source_operation_dispatched", exc.reply)
        self.assertEqual(exc.reply.get("failure", {}).get("code"), "WORKER_SOURCE_BINDING_UNAVAILABLE")
        self.assertIs(exc.reply.get("execution_state_unknown"), True)
        self.assertIs(exc.reply.get("safe_retry"), False)
        self.assertIs(exc.reply.get("failure", {}).get("execution_state_unknown"), True)
        self.assertIs(exc.reply.get("failure", {}).get("safe_retry"), False)
        self.assertEqual(connect_count, 1)
        self.assertEqual(stream.frame_types(), [None, "source_binding_capabilities"])

    def test_uncorrelated_probe_exception_is_sanitized(self) -> None:
        exc, stream, _worker, connect_count = self.run_request(
            require_source_binding=True,
            fail_probe_correlation=True,
        )
        self.assertIsInstance(exc, _java_worker.JavaWorkerError)
        self.assertIsNone(exc.__context__)
        self.assertIsNone(exc.__cause__)
        self.assertNotIn(SECRET, exception_material(exc))
        self.assertEqual(exc.reply.get("status"), "UNKNOWN")
        self.assertEqual(exc.reply.get("request_id"), "memory-request")
        self.assertEqual(exc.reply.get("type"), "code_compile")
        self.assertEqual(exc.reply.get("transport_stage"), "source_binding_probe_reply")
        self.assertEqual(exc.reply.get("reason"), "source_binding_probe_uncorrelated")
        self.assertEqual(exc.reply.get("cause_type"), "JavaWorkerError")
        self.assertNotIn("source_operation_dispatched", exc.reply)
        self.assertEqual(exc.reply.get("failure", {}).get("code"), "WORKER_SOURCE_BINDING_UNAVAILABLE")
        self.assertIs(exc.reply.get("execution_state_unknown"), True)
        self.assertIs(exc.reply.get("safe_retry"), False)
        self.assertIs(exc.reply.get("failure", {}).get("execution_state_unknown"), True)
        self.assertIs(exc.reply.get("failure", {}).get("safe_retry"), False)
        self.assertEqual(connect_count, 1)
        self.assertEqual(stream.frame_types(), [None, "source_binding_capabilities"])

    def test_code_compile_connect_timeout_is_unknown_and_redacted(self) -> None:
        worker = make_worker()
        stream = MemoryStream()
        with patch.object(_java_worker.socket, "create_connection",
                          side_effect=TimeoutError(errno.ETIMEDOUT, SECRET)) as connect:
            try:
                worker._request(
                    {"type": "code_compile", "request_id": "memory-timeout"},
                    timeout_s=1.0,
                    require_source_binding=True,
                )
            except BaseException as exc:
                error = exc
            else:
                self.fail("code_compile connect unexpectedly succeeded")
        self.assertIsInstance(error, _java_worker.JavaWorkerTimeout)
        self.assertIsInstance(error, _java_worker.JavaWorkerError)
        self.assertIsNone(error.__context__)
        self.assertIsNone(error.__cause__)
        self.assertNotIn(SECRET, exception_material(error))
        self.assertEqual(error.reply.get("status"), "UNKNOWN")
        self.assertEqual(error.reply.get("request_id"), "memory-timeout")
        self.assertEqual(error.reply.get("type"), "code_compile")
        self.assertEqual(error.reply.get("transport_stage"), "connect")
        self.assertEqual(error.reply.get("errno"), errno.ETIMEDOUT)
        self.assertIs(error.reply.get("execution_state_unknown"), True)
        self.assertIs(error.reply.get("safe_retry"), False)
        self.assertEqual(worker._known_requests.get("memory-timeout"), {
            "request_id": "memory-timeout", "status": "UNKNOWN", "reason": "rpc_timeout",
        })
        self.assertEqual(connect.call_count, 1)
        self.assertEqual(stream.frame_types(), [])

    def test_normal_request_path_still_writes_and_correlates_reply(self) -> None:
        reply, stream, worker, connect_count = self.run_request()
        self.assertEqual(reply.get("status"), "SUCCEEDED")
        self.assertEqual(reply.get("request_id"), "memory-request")
        self.assertEqual(stream.frame_types(), [None, "call"])
        self.assertEqual(connect_count, 1)
        self.assertEqual(worker._known_requests["memory-request"], reply)


if __name__ == "__main__":
    unittest.main(verbosity=2)
