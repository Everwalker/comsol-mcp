"""Offline checks for the retained acceptance runners' v2 fixture adapter."""
import hashlib

import pytest

from tools.java_snapshot_runner import execute_java_fixture


def test_fixture_adapter_sends_the_same_bounded_raw_bytes_and_hash(tmp_path):
    source = tmp_path / "Fixture.java"
    raw = "public class Fixture { // 温度\r\n}\r\n".encode("utf-8")
    source.write_bytes(raw)

    class Worker:
        def execute_java(self, tag, name, entrypoint, arguments, **kwargs):
            assert (tag, name, entrypoint, arguments) == ("model", "Fixture.java", "Fixture", {"x": 1})
            assert kwargs["source_bytes"] == raw
            assert kwargs["expected_source_sha256"] == hashlib.sha256(raw).hexdigest()
            # A subsequent path change must not change the snapshot already sent.
            source.write_bytes(b"different")
            return {"ok": True, "status": "SUCCEEDED", "_source_binding_verified": True}

    assert execute_java_fixture(Worker(), "model", source, "Fixture", {"x": 1})["ok"] is True


@pytest.mark.parametrize("reply", [
    {"ok": True, "status": "SUCCEEDED"},
    {"ok": False, "status": "UNKNOWN", "_source_binding_verified": False},
])
def test_fixture_adapter_stops_on_unverified_worker_result(tmp_path, reply):
    source = tmp_path / "Fixture.java"
    source.write_bytes(b"public class Fixture {}")

    class Worker:
        def execute_java(self, *_args, **_kwargs):
            return reply

    with pytest.raises(AssertionError):
        execute_java_fixture(Worker(), "model", source, "Fixture")
