from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

pytestmark = pytest.mark.skipif(sys.platform != "darwin", reason="macOS-only Phase 3 owned runtime lifecycle tests")

from tools import phase3_owned_runtime as runtime


def test_reviewed_server_xml_is_exact_one_attribute_change():
    original, proposed = runtime._proposal_bytes()
    assert proposed.replace(b'address="127.0.0.1" ', b"", 1) == original
    assert b'address="127.0.0.1"' in proposed


def test_proposal_rejects_an_unreviewed_byte_change(tmp_path: Path, monkeypatch):
    original = tmp_path / "original.xml"
    proposed = tmp_path / "proposed.xml"
    original.write_bytes(b'<Server><Connector port="${port}" protocol="HTTP/1.1" /></Server>')
    proposed.write_bytes(b'<Server><Connector address="127.0.0.1" port="${port}" protocol="HTTP/1.1" /></Server>\n')
    monkeypatch.setattr(runtime, "PROPOSAL_ORIGINAL", original)
    monkeypatch.setattr(runtime, "PROPOSAL_LOOPBACK", proposed)
    monkeypatch.setattr(runtime, "EXPECTED_ORIGINAL_SHA256", runtime._sha256(original))
    monkeypatch.setattr(runtime, "EXPECTED_LOOPBACK_SHA256", runtime._sha256(proposed))
    with pytest.raises(runtime.GuardError, match="more than one attribute"):
        runtime._proposal_bytes()


def test_listener_records_require_numeric_loopback(monkeypatch):
    def fake_run(args, *, check=False):
        assert args[0] == runtime.LSOF
        return subprocess.CompletedProcess(
            args,
            0,
            "COMMAND PID USER   FD TYPE DEVICE SIZE/OFF NODE NAME\n"
            "java 123 user  10u IPv4 0x1 0t0 TCP 127.0.0.1:56389 (LISTEN)\n",
            "",
        )

    monkeypatch.setattr(runtime, "_run", fake_run)
    assert runtime._listener_records() == [
        {"pid": 123, "endpoint": "127.0.0.1:56389", "raw": "java 123 user  10u IPv4 0x1 0t0 TCP 127.0.0.1:56389 (LISTEN)"}
    ]


def test_loopback_guard_rejects_wildcard(monkeypatch):
    monkeypatch.setattr(
        runtime,
        "_listener_records",
        lambda: [{"pid": 123, "endpoint": "*:56389", "raw": "wildcard"}],
    )
    with pytest.raises(runtime.GuardError, match="not exactly 127.0.0.1"):
        runtime._assert_listener_loopback(123)


def test_wrong_address_is_terminal_loopback_violation(monkeypatch):
    monkeypatch.setattr(
        runtime,
        "_listener_records",
        lambda: [{"pid": 222, "endpoint": "*:56389", "raw": "wildcard"}],
    )
    with pytest.raises(runtime.LoopbackViolation, match="not exactly 127.0.0.1"):
        runtime._resolve_listener_identity(111)


def test_failed_attempt_snapshots_child_before_launcher_stop(monkeypatch):
    child = {
        "pid": 222,
        "ppid": 111,
        "birth": "child-birth",
        "command": "/Applications/COMSOL64/Multiphysics/bin/comsol mphserver",
        "command_sha256": "child-sha",
    }
    launcher = {
        "pid": 111,
        "ppid": 1,
        "birth": "launcher-birth",
        "command": "/Applications/COMSOL64/Multiphysics/bin/comsol mphserver",
        "command_sha256": "launcher-sha",
    }
    monkeypatch.setattr(runtime, "_listener_records", lambda: [{"pid": 222, "endpoint": "*:56389", "raw": "wildcard"}])
    monkeypatch.setattr(runtime, "_process_snapshot", lambda pid: {111: launcher, 222: child}.get(pid))
    monkeypatch.setattr(runtime, "_is_descendant", lambda pid, ancestor: pid == 222 and ancestor == 111)
    assert runtime._collect_owned_listener_identities(111) == [child]


def test_failed_attempt_stops_child_before_launcher(monkeypatch):
    child = {
        "pid": 222,
        "ppid": 111,
        "birth": "child-birth",
        "command": "/Applications/COMSOL64/Multiphysics/bin/comsol mphserver",
        "command_sha256": "child-sha",
    }
    launcher = {
        "pid": 111,
        "ppid": 1,
        "birth": "launcher-birth",
        "command": "/Applications/COMSOL64/Multiphysics/bin/comsol mphserver",
        "command_sha256": "launcher-sha",
    }
    killed: set[int] = set()
    monkeypatch.setattr(runtime, "_collect_owned_listener_identities", lambda pid: [child])
    monkeypatch.setattr(runtime, "_listener_records", lambda: [] if killed else [{"pid": 222, "endpoint": "*:56389", "raw": "wildcard"}])
    monkeypatch.setattr(runtime, "_process_snapshot", lambda pid: None if pid in killed else {111: launcher, 222: child}.get(pid))
    monkeypatch.setattr(runtime.os, "kill", lambda pid, sig: killed.add(pid))

    class FakeProcess:
        pid = 111

    runtime._stop_failed_attempt(FakeProcess(), timeout_s=0.1)
    assert killed == {111, 222}


def test_process_parser_preserves_birth_and_command():
    parsed = runtime._parse_process_line(
        "123 1 Fri Sep 19 22:10:00 2026 /Applications/COMSOL64/Multiphysics/bin/comsol mphserver"
    )
    assert parsed is not None
    assert parsed["pid"] == 123
    assert parsed["ppid"] == 1
    assert parsed["birth"] == "Fri Sep 19 22:10:00 2026"
    assert parsed["command"].endswith("comsol mphserver")


def test_in_place_write_preserves_inode_and_metadata(tmp_path: Path):
    target = tmp_path / "server.xml"
    target.write_bytes(b"original-content")
    before = target.stat()
    metadata = runtime._file_metadata(target)

    result = runtime._in_place_write(target, b"loopback-content-with-more-bytes", metadata)

    after = target.stat()
    assert target.read_bytes() == b"loopback-content-with-more-bytes"
    assert int(after.st_ino) == int(before.st_ino) == result["inode"]
    assert runtime._file_metadata(target) == metadata


def test_target_metadata_rejects_unknown_xattr():
    with pytest.raises(runtime.GuardError, match="unapproved extended-attribute"):
        runtime._assert_approved_target_metadata({
            "xattrs": ["com.example.unknown"],
            "xattr_values": {"com.example.unknown": {"hex": "00", "length": 1, "sha256": "x"}},
            "acl_entries": [],
        })


def test_target_metadata_accepts_recorded_provenance_residual():
    runtime._assert_approved_target_metadata({
        "xattrs": [runtime.KNOWN_RESIDUAL_XATTR_NAME],
        "xattr_values": {
            runtime.KNOWN_RESIDUAL_XATTR_NAME: {
                "hex": runtime.KNOWN_RESIDUAL_XATTR_HEX,
                "length": len(bytes.fromhex(runtime.KNOWN_RESIDUAL_XATTR_HEX)),
                "sha256": runtime.KNOWN_RESIDUAL_XATTR_SHA256,
            }
        },
        "acl_entries": [],
    })
