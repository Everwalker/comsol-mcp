"""Offline guards for the approved RemoteAddrValve lifecycle helper."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import subprocess

import pytest

from comsol_mcp import _g2_isolation as isolation
from tools import phase3_remote_addr_valve_runtime as runtime


PORT = 56389


def _socket_rows(server_pid: int = 123, worker_pid: int = 456) -> list[dict[str, object]]:
    return [
        {
            "pid": server_pid,
            "state": "LISTEN",
            "endpoint": f"*:{PORT}",
            "local_endpoint": f"*:{PORT}",
            "remote_endpoint": None,
        },
        {
            "pid": server_pid,
            "state": "ESTABLISHED",
            "endpoint": f"127.0.0.1:{PORT}->127.0.0.1:58412",
            "local_endpoint": f"127.0.0.1:{PORT}",
            "remote_endpoint": "127.0.0.1:58412",
        },
        {
            "pid": worker_pid,
            "state": "ESTABLISHED",
            "endpoint": f"127.0.0.1:58412->127.0.0.1:{PORT}",
            "local_endpoint": "127.0.0.1:58412",
            "remote_endpoint": f"127.0.0.1:{PORT}",
        },
    ]


def test_approved_remote_addr_valve_proposal_is_exact_and_preserves_access_log():
    original, proposed = runtime._proposal_bytes()

    assert hashlib.sha256(original).hexdigest() == runtime.EXPECTED_ORIGINAL_SHA256
    assert hashlib.sha256(proposed).hexdigest() == runtime.EXPECTED_VALVE_SHA256
    original_valves = runtime._active_valves(original)
    proposed_valves = runtime._active_valves(proposed)
    assert any(row.get("className") == "org.apache.catalina.valves.AccessLogValve" for row in original_valves)
    assert not any(row.get("className") == runtime.EXPECTED_VALVE_CLASS for row in original_valves)
    assert sum(row.get("className") == runtime.EXPECTED_VALVE_CLASS for row in proposed_valves) == 1


def test_proposal_hash_guard_rejects_modified_private_copy(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    original, proposed = runtime._proposal_bytes()
    original_path = tmp_path / "server.original.xml"
    proposed_path = tmp_path / "server.remote_addr_valve.xml"
    original_path.write_bytes(original)
    proposed_path.write_bytes(proposed + b"\r\n")
    monkeypatch.setattr(runtime, "PROPOSAL_ORIGINAL", original_path)
    monkeypatch.setattr(runtime, "PROPOSAL_VALVE", proposed_path)

    with pytest.raises(runtime.GuardError, match="proposal"):
        runtime._proposal_bytes()


def test_webbridge_restore_uses_recorded_target_and_metadata_snapshot(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    original, proposed = runtime._proposal_bytes()
    target = tmp_path / "server.xml"
    target.write_bytes(proposed)
    metadata = runtime.base._file_metadata(target)
    residual = metadata.get("xattr_values", {}).get(runtime.base.KNOWN_RESIDUAL_XATTR_NAME)
    if isinstance(residual, dict) and residual.get("hex") != runtime.base.KNOWN_RESIDUAL_XATTR_HEX:
        # Some hosts stamp every newly created file with an unremovable
        # ``com.apple.provenance`` value whose tail differs per creator
        # process, so a fixture can never reproduce the pinned residual of the
        # installed file.  Pin the observed value for this fixture snapshot
        # only; the fail-closed policy itself is covered by
        # test_webbridge_metadata_guard_rejects_unapproved_provenance_value.
        monkeypatch.setattr(runtime.base, "KNOWN_RESIDUAL_XATTR_HEX", residual["hex"])
        monkeypatch.setattr(runtime.base, "KNOWN_RESIDUAL_XATTR_SHA256", residual["sha256"])
    monkeypatch.setattr(runtime, "SERVER_XML", target)
    receipt = {
        "config": {
            "target": str(target),
            "proposed_sha256": runtime.EXPECTED_VALVE_SHA256,
            "original_mode": runtime.base._mode(target),
            "original_metadata": metadata,
            "target_inode": int(target.stat().st_ino),
        }
    }

    restored = runtime._restore_original_webbridge(receipt, original)

    assert restored["sha256"] == runtime.EXPECTED_ORIGINAL_SHA256
    assert target.read_bytes() == original
    assert int(target.stat().st_ino) == receipt["config"]["target_inode"]


def test_webbridge_metadata_guard_preserves_known_provenance_residual():
    expected = {
        "uid": 501,
        "gid": 80,
        "mode": 0o755,
        "xattrs": [],
        "xattr_values": {},
        "acl_entries": [],
    }
    observed = {
        **expected,
        "xattrs": [runtime.base.KNOWN_RESIDUAL_XATTR_NAME],
        "xattr_values": {
            runtime.base.KNOWN_RESIDUAL_XATTR_NAME: {
                "hex": runtime.base.KNOWN_RESIDUAL_XATTR_HEX,
                "length": len(bytes.fromhex(runtime.base.KNOWN_RESIDUAL_XATTR_HEX)),
                "sha256": runtime.base.KNOWN_RESIDUAL_XATTR_SHA256,
            }
        },
    }

    runtime._assert_webbridge_metadata(observed, expected)

    # A token outside the recorded family is refused; a re-minted member of the
    # family is accepted (and recorded by the receipts).
    observed["xattr_values"][runtime.base.KNOWN_RESIDUAL_XATTR_NAME] = {
        "hex": "deadbeef", "length": 4, "sha256": "0" * 64,
    }
    with pytest.raises((runtime.GuardError, runtime.base.GuardError), match="xattrs|provenance"):
        runtime._assert_webbridge_metadata(observed, expected)


def test_webbridge_metadata_guard_rejects_unapproved_provenance_value():
    """The in-place update policy stays fail-closed for any other xattr value."""
    metadata = {
        "uid": 501,
        "gid": 80,
        "mode": 0o755,
        "xattrs": [runtime.base.KNOWN_RESIDUAL_XATTR_NAME],
        "xattr_values": {
            runtime.base.KNOWN_RESIDUAL_XATTR_NAME: {
                "hex": "010200ffffffffffff",
                "length": 10,
                "sha256": "0" * 64,
            }
        },
        "acl_entries": [],
    }
    with pytest.raises(runtime.base.GuardError, match="provenance"):
        runtime.base._assert_approved_target_metadata(metadata)


def test_authenticated_socket_pair_requires_exact_loopback_bidirectional_pair():
    rows = runtime._assert_authenticated_socket_pair(
        _socket_rows(), server_pid=123, worker_pid=456, port=PORT,
    )
    assert {row["pid"] for row in rows if row["state"] == "ESTABLISHED"} == {123, 456}

    broken = _socket_rows()
    broken[-1]["remote_endpoint"] = "192.168.100.152:56389"
    with pytest.raises(runtime.GuardError, match="non-loopback"):
        runtime._assert_authenticated_socket_pair(broken, server_pid=123, worker_pid=456, port=PORT)


def test_authenticated_socket_pair_rejects_unapproved_client():
    rows = _socket_rows()
    rows.append({
        "pid": 789,
        "state": "ESTABLISHED",
        "endpoint": f"127.0.0.1:59000->127.0.0.1:{PORT}",
        "local_endpoint": "127.0.0.1:59000",
        "remote_endpoint": f"127.0.0.1:{PORT}",
    })
    with pytest.raises(runtime.GuardError, match="unapproved"):
        runtime._assert_authenticated_socket_pair(rows, server_pid=123, worker_pid=456, port=PORT)


@pytest.mark.parametrize(
    ("diagnostic", "expected"),
    [
        ("HTTP_403", True),
        ("status=403", True),
        ("response code 403 Forbidden", True),
        ("port=403", False),
        ("value=1403", False),
    ],
)
def test_worker_failure_403_classification_requires_http_context(diagnostic: str, expected: bool):
    error = runtime.JavaWorkerError("sanitized", reply={"failure": {"code": diagnostic}})

    record = runtime._worker_failure_record(error)

    assert record["http_403_explicitly_observed_in_worker_error"] is expected
    assert record["http_failure_category"] == ("HTTP_403" if expected else "UNCLASSIFIED")


class _FakeConnection:
    def __init__(self, response: bytes):
        self.response = response
        self.sent = b""
        self.local = ("192.168.100.152", 49000)
        self.peer = ("192.168.100.152", PORT)

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def settimeout(self, _timeout):
        return None

    def bind(self, address):
        self.local = (address[0], 49000)

    def connect(self, address):
        self.peer = (address[0], address[1])

    def getsockname(self):
        return self.local

    def getpeername(self):
        return self.peer

    def sendall(self, data: bytes):
        self.sent += data

    def recv(self, _size: int) -> bytes:
        response, self.response = self.response, b""
        return response


def test_websocket_api_probe_records_only_status_facts(monkeypatch: pytest.MonkeyPatch):
    fake = _FakeConnection(b"HTTP/1.1 403 Forbidden\r\nX-Private: should-not-be-recorded\r\n\r\nsecret")
    monkeypatch.setattr(runtime.socket, "socket", lambda *_args, **_kwargs: fake)

    result = runtime._websocket_api_probe("192.168.100.152", source_address="192.168.100.152")

    assert result["http_status"] == 403
    assert result["route"] == runtime.API_ROUTE
    assert "Private" not in json.dumps(result)
    assert b"Sec-WebSocket-Key:" in fake.sent
    assert result["local_endpoint"]["host"] == "192.168.100.152"
    assert result["peer_endpoint"] == {"host": "192.168.100.152", "port": PORT}


def test_access_log_correlation_binds_java_interval_and_rejects_other_nonloopback_rows(tmp_path: Path):
    path = tmp_path / "mphserverlocalhost_access_log.2026-09-20.txt"
    path.write_text(
        '127.0.0.1 - - [20/Sep/2026:08:21:07 +0800] "GET /webbridge/websocket HTTP/1.1" 101 -\n',
        encoding="utf-8",
    )
    before = runtime._access_log_snapshot(path)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(
            '192.168.100.152 - - [20/Sep/2026:08:21:08 +0800] "GET /webbridge/websocket HTTP/1.1" 403 -\n'
        )

    proof = runtime._correlate_access_log(
        path,
        before,
        nonloopback="192.168.100.152",
        java_request_id="g2-valve-nonloopback-connect",
    )

    assert proof["status"] == "PASS"
    assert proof["phase"] == "java_connect_only"
    assert proof["path"] == str(path.resolve())
    assert proof["offset_after"] > proof["offset_before"]
    assert len(proof["before_sha256"]) == 64
    assert len(proof["after_sha256"]) == 64
    assert len(proof["segment_sha256"]) == 64
    assert proof["java_request_id"] == "g2-valve-nonloopback-connect"
    assert proof["entries"] == [
        {"peer": "192.168.100.152", "method": "GET", "route": runtime.API_ROUTE, "http_status": 403}
    ]

    second_before = runtime._access_log_snapshot(path)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(
            '192.168.100.152 - - [20/Sep/2026:08:21:09 +0800] "GET /webbridge/websocket HTTP/1.1" 200 -\n'
        )
        handle.write(
            '192.168.100.152 - - [20/Sep/2026:08:21:10 +0800] "GET /webbridge/websocket HTTP/1.1" 403 -\n'
        )
    failed = runtime._correlate_access_log(
        path,
        second_before,
        nonloopback="192.168.100.152",
        java_request_id="g2-valve-nonloopback-connect",
    )
    assert failed["status"] == "FAIL"
    assert [entry["http_status"] for entry in failed["entries"]] == [200, 403]


def test_current_nonloopback_address_is_discovered_each_run(monkeypatch: pytest.MonkeyPatch):
    answers = {"en0": "127.0.0.1\n", "en1": "192.168.100.139\n", "en2": "", "bridge0": ""}

    def fake_run(args, **_kwargs):
        interface = args[-1]
        return subprocess.CompletedProcess(args, 0 if answers[interface] else 1, answers[interface], "")

    monkeypatch.setattr(runtime.base, "_run", fake_run)
    assert runtime._current_nonloopback_ipv4() == "192.168.100.139"


def test_probe_producer_emits_comsol_java_api_shape_for_consumer(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    access_log = tmp_path / "mphserverlocalhost_access_log.2026-09-20.txt"
    access_log.write_text(
        '127.0.0.1 - - [20/Sep/2026:08:21:07 +0800] "GET /webbridge/websocket HTTP/1.1" 101 -\n',
        encoding="utf-8",
    )

    class FakeWorker:
        instances: list["FakeWorker"] = []

        def __init__(self, _paths, *, state_dir):
            self.state_dir = state_dir
            self.address = ""
            self.connected = False
            self.pid = 456 if state_dir.name.endswith("loopback") else 457
            self.__class__.instances.append(self)

        def start(self, **_kwargs):
            return {"status": "HEALTHY"}

        def client(self):
            return self

        def connect(self, _port, address, **_kwargs):
            self.address = address
            if address != "127.0.0.1":
                with access_log.open("a", encoding="utf-8") as handle:
                    handle.write(
                        '192.168.100.152 - - [20/Sep/2026:08:21:08 +0800] "GET /webbridge/websocket HTTP/1.1" 403 -\n'
                    )
                raise runtime.JavaWorkerError(
                    "rejected", reply={"failure": {"code": "HTTP_403"}}
                )
            self.connected = True
            return {"ok": True}

        def runtime_metadata(self):
            return {
                "pid": self.pid,
                "connected": self.connected,
                "server": f"{self.address}:{PORT}",
                "status": "HEALTHY",
            }

        def disconnect(self, **_kwargs):
            self.connected = False
            return {"status": "DISCONNECTED"}

        def close(self):
            self.connected = False

    local_rows = _socket_rows(server_pid=123, worker_pid=456)
    monkeypatch.setattr(runtime, "PersistentJavaWorker", FakeWorker)
    monkeypatch.setattr(runtime, "_current_nonloopback_ipv4", lambda: "192.168.100.152")
    monkeypatch.setattr(runtime, "_access_log_path", lambda _run_dir: access_log)
    monkeypatch.setattr(runtime, "_socket_rows", lambda: local_rows if len(FakeWorker.instances) == 1 else [local_rows[0]])
    monkeypatch.setattr(
        runtime,
        "_websocket_api_probe",
        lambda address, **_kwargs: {
            "status": "HTTP_RESPONSE",
            "address": address,
            "route": runtime.API_ROUTE,
            "source_bind_requested": address,
            "local_endpoint": {"host": address, "port": 49000},
            "peer_endpoint": {"host": address, "port": PORT},
            "http_status": 101 if address == "127.0.0.1" else 403,
        },
    )
    monkeypatch.setattr(runtime.base, "_process_snapshot", lambda _pid: {"birth": "birth-1"})

    proof = runtime._probe_api(tmp_path, 123)

    assert proof["status"] == "PASS"
    assert proof["probe_kind"] == "comsol_java_api"
    assert proof["loopback"]["status"] == "PASS"
    assert proof["loopback"]["authenticated"] is True
    assert proof["nonloopback"]["status"] == "PASS"
    assert proof["nonloopback"]["http_status"] == 403
    assert proof["nonloopback"]["server_access_log_http_status"] == 403
    assert proof["nonloopback"]["server_access_log"]["status"] == "PASS"
    assert proof["nonloopback"]["server_access_log"]["phase"] == "java_connect_only"
    assert proof["nonloopback"]["server_access_log"]["raw_probe_started_after_capture"] is True
    assert proof["nonloopback"]["server_access_log"]["entries"] == [
        {"peer": "192.168.100.152", "method": "GET", "route": runtime.API_ROUTE, "http_status": 403}
    ]
    assert proof["nonloopback"]["explicit_source_match"] is True
    assert [item.state_dir.name for item in FakeWorker.instances] == ["worker-loopback", "worker-nonloopback"]

    target = tmp_path / "server.xml"
    target.write_bytes(runtime.PROPOSAL_VALVE.read_bytes())
    monkeypatch.setattr(isolation, "_REMOTE_ADDR_VALVE_TARGET", target)
    monkeypatch.setattr(isolation, "_REMOTE_ADDR_VALVE_APPLIED_SHA256", runtime.EXPECTED_VALVE_SHA256)
    command = "/Applications/COMSOL64/Multiphysics/bin/comsol mphserver -port 56389"
    command_sha = hashlib.sha256(command.encode("utf-8")).hexdigest()
    proof["target"] = str(target)
    receipt = {
        "status": "RUNNING",
        "isolation_mode": "remote_addr_valve",
        "run_id": "run",
        "config": {
            "target": str(target),
            "target_inode": int(target.stat().st_ino),
            "content_sha256_after_apply": runtime.EXPECTED_VALVE_SHA256,
        },
        "process": {
            "pid": 123,
            "birth": "birth-1",
            "command_sha256": command_sha,
            "port": PORT,
        },
        "paths": {"run_dir": str(tmp_path)},
    }
    receipt_path = tmp_path / "lifecycle.receipt.json"
    receipt_path.write_text(json.dumps(receipt), encoding="utf-8")
    public_path = tmp_path / "public.json"
    public_path.write_text(json.dumps({"run_id": "run"}), encoding="utf-8")
    monkeypatch.setattr(runtime.base, "_assert_owned_identity", lambda _receipt: {
        "pid": 123, "birth": "birth-1", "command": command, "command_sha256": command_sha,
    })
    monkeypatch.setattr(runtime.base, "_listener_records", lambda _port: [{"pid": 123, "endpoint": f"*:{PORT}"}])
    monkeypatch.setattr(runtime, "_probe_api", lambda _run_dir, _pid: proof)
    monkeypatch.setattr(runtime, "_public_path", lambda _run_dir: public_path)
    runtime._probe(receipt_path)
    receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
    assert receipt["status"] == "RUNNING"
    assert receipt["isolation_mode"] == "remote_addr_valve"
    assert receipt["probe"]["probe_kind"] == "comsol_java_api"
    assert receipt["proof"] == receipt["probe"]

    consumed = isolation._remote_probe_identity(
        receipt, target=target, port=PORT, config_sha=runtime.EXPECTED_VALVE_SHA256,
    )
    assert consumed["probe_kind"] == "comsol_java_api"
    assert consumed["authenticated_api_success"] is True
    assert consumed["nonloopback_api_exception_http_status"] == 403

    monkeypatch.setattr(isolation, "_require_mac_isolation_adapter", lambda: None)
    monkeypatch.setattr(
        isolation,
        "_process_snapshot",
        lambda _pid: {
            "pid": 123,
            "birth": "birth-1",
            "command": command,
            "command_sha256": command_sha,
        },
    )
    monkeypatch.setattr(isolation, "_socket_rows", lambda _port: _socket_rows())

    verified = isolation.verify_owned_server(
        receipt_path, endpoint=f"127.0.0.1:{PORT}", worker_pid=456,
    )
    assert verified["verified"] is True
    assert verified["api_probe"]["probe_kind"] == "comsol_java_api"

    failed_receipt = dict(receipt)
    failed_receipt["status"] = "PROBED"
    failed_path = tmp_path / "failed-receipt.json"
    failed_path.write_text(json.dumps(failed_receipt), encoding="utf-8")
    with pytest.raises(isolation.ExecutionContractError) as exc:
        isolation.verify_owned_server(failed_path, endpoint=f"127.0.0.1:{PORT}", worker_pid=456)
    assert exc.value.code == "ISOLATION_PROOF_REQUIRED"


def test_probe_cli_resolves_run_directory_to_private_receipt(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    run_dir = tmp_path / "run"
    observed: list[Path] = []
    monkeypatch.setattr(runtime, "_probe", lambda path: observed.append(path) or {})

    args = runtime._parser().parse_args(["probe", "--run-dir", str(run_dir)])
    assert args.handler(args) == 0
    assert observed == [run_dir.resolve() / "lifecycle.receipt.json"]


def test_run_surfaces_cleanup_failure_instead_of_suppressing_it(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    receipt_path = run_dir / "lifecycle.receipt.json"
    receipt_path.write_text(json.dumps({"status": "RUNNING", "run_id": "run"}), encoding="utf-8")
    public_path = tmp_path / "public.json"
    public_path.write_text(json.dumps({"run_id": "run"}), encoding="utf-8")
    monkeypatch.setattr(runtime, "_start", lambda _args: receipt_path)
    monkeypatch.setattr(runtime, "_probe", lambda _path: (_ for _ in ()).throw(runtime.GuardError("probe failed")))
    monkeypatch.setattr(runtime, "_cleanup", lambda _path: (_ for _ in ()).throw(runtime.GuardError("restore failed")))
    monkeypatch.setattr(runtime, "_public_path", lambda _path: public_path)

    with pytest.raises(runtime.GuardError, match=r"probe failed; cleanup failed:.*restore failed"):
        runtime._run(subprocess.Namespace() if hasattr(subprocess, "Namespace") else type("Args", (), {})())

    public = json.loads(public_path.read_text(encoding="utf-8"))
    assert public["status"] == "CLEANUP_FAILED"
    assert public["failure_reason"] == "probe failed"


def test_run_records_probe_failure_after_successful_cleanup(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    receipt_path = run_dir / "lifecycle.receipt.json"
    receipt_path.write_text(json.dumps({"status": "RUNNING", "run_id": "run"}), encoding="utf-8")
    public_path = tmp_path / "public.json"
    public_path.write_text(json.dumps({"run_id": "run", "status": "STOPPED_RESTORED"}), encoding="utf-8")
    monkeypatch.setattr(runtime, "_start", lambda _args: receipt_path)
    monkeypatch.setattr(runtime, "_probe", lambda _path: (_ for _ in ()).throw(runtime.GuardError("unexpected nonloopback success")))
    monkeypatch.setattr(runtime, "_cleanup", lambda _path: {})
    monkeypatch.setattr(runtime, "_public_path", lambda _path: public_path)

    with pytest.raises(runtime.GuardError, match="unexpected nonloopback success"):
        runtime._run(type("Args", (), {})())

    public = json.loads(public_path.read_text(encoding="utf-8"))
    receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
    assert public["status"] == "FAIL_RESTORED"
    assert public["cleanup_status"] == "STOPPED_RESTORED"
    assert receipt["run_failure"]["reason"] == "unexpected nonloopback success"
