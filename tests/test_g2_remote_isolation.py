"""Software-only tests for the reviewed RemoteAddrValve isolation proof."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from comsol_mcp import _g2_isolation as isolation
from comsol_mcp._execution_contract import ExecutionContractError


PORT = 56389
SERVER_PID = 12345
WORKER_PID = 777
WORKER_PORT = 58412
COMMAND = "/Applications/COMSOL64/Multiphysics/bin/mphserver -port 56389"


def _fixture(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[Path, dict, str]:
    monkeypatch.setattr(isolation, "_require_mac_isolation_adapter", lambda: None)
    target = tmp_path / "server.xml"
    target.write_bytes(b"reviewed RemoteAddrValve configuration")
    config_sha = hashlib.sha256(target.read_bytes()).hexdigest()
    monkeypatch.setattr(isolation, "_REMOTE_ADDR_VALVE_TARGET", target)
    monkeypatch.setattr(isolation, "_REMOTE_ADDR_VALVE_APPLIED_SHA256", config_sha)
    command_sha = hashlib.sha256(COMMAND.encode("utf-8")).hexdigest()
    receipt = {
        "schema_version": 2,
        "status": "RUNNING",
        "isolation_mode": "remote_addr_valve",
        "config": {
            "target": str(target),
            "target_inode": int(target.stat().st_ino),
            "content_sha256_after_apply": config_sha,
        },
        "process": {
            "pid": SERVER_PID,
            "birth": "birth-1",
            "command_sha256": command_sha,
            "port": PORT,
        },
        "probe": {
            "probe_kind": "comsol_java_api",
            "status": "PASS",
            "observed_at": "2026-09-20T00:00:00Z",
            "process": {"pid": SERVER_PID, "birth": "birth-1"},
            "port": PORT,
            "config_sha256": config_sha,
            "loopback": {
                "address": "127.0.0.1",
                "port": PORT,
                "authenticated_api_success": True,
            },
            "nonloopback": {
                "address": "192.168.100.152",
                "port": PORT,
                "api_exception_http_status": 403,
            },
        },
    }
    path = tmp_path / "receipt.json"
    path.write_text(json.dumps(receipt), encoding="utf-8")
    monkeypatch.setattr(
        isolation,
        "_process_snapshot",
        lambda _pid: {
            "pid": SERVER_PID,
            "birth": "birth-1",
            "command": COMMAND,
            "command_sha256": command_sha,
        },
    )
    return path, receipt, config_sha


def _socket_rows() -> list[dict[str, object]]:
    return [
        {"pid": SERVER_PID, "endpoint": f"*:{PORT}", "state": "LISTEN"},
        {
            "pid": SERVER_PID,
            "endpoint": f"127.0.0.1:{PORT}->127.0.0.1:{WORKER_PORT}",
            "state": "ESTABLISHED",
        },
        {
            "pid": WORKER_PID,
            "endpoint": f"127.0.0.1:{WORKER_PORT}->127.0.0.1:{PORT}",
            "state": "ESTABLISHED",
        },
    ]


def test_remote_addr_valve_accepts_wildcard_listener_with_verified_api_probe(tmp_path, monkeypatch):
    receipt_path, _receipt, _sha = _fixture(tmp_path, monkeypatch)
    monkeypatch.setattr(isolation, "_socket_rows", lambda _port: _socket_rows())

    result = isolation.verify_owned_server(
        receipt_path,
        endpoint=f"127.0.0.1:{PORT}",
        worker_pid=WORKER_PID,
    )

    assert result["verified"] is True
    assert result["listener_binding"] == "wildcard"
    assert result["loopback_listener"] is False
    assert result["api_probe"]["probe_kind"] == "comsol_java_api"
    assert result["api_probe"]["authenticated_api_success"] is True
    assert result["api_probe"]["nonloopback_api_exception_http_status"] == 403
    assert result["established_client_pids"] == sorted([SERVER_PID, WORKER_PID])


@pytest.mark.parametrize("mutation", ["missing_probe", "stale_process", "changed_config", "changed_inode"])
def test_remote_addr_valve_rejects_missing_or_stale_identity_proof(tmp_path, monkeypatch, mutation):
    receipt_path, receipt, config_sha = _fixture(tmp_path, monkeypatch)
    target = Path(receipt["config"]["target"])
    if mutation == "missing_probe":
        receipt.pop("probe")
        receipt_path.write_text(json.dumps(receipt), encoding="utf-8")
    elif mutation == "stale_process":
        receipt["process"]["birth"] = "old-birth"
        receipt_path.write_text(json.dumps(receipt), encoding="utf-8")
    elif mutation == "changed_config":
        target.write_bytes(b"changed after probe")
    elif mutation == "changed_inode":
        target.unlink()
        target.write_bytes(b"reviewed RemoteAddrValve configuration")
        assert hashlib.sha256(target.read_bytes()).hexdigest() == config_sha
    monkeypatch.setattr(isolation, "_socket_rows", lambda _port: _socket_rows())

    with pytest.raises(ExecutionContractError) as exc:
        isolation.verify_owned_server(receipt_path, endpoint=f"127.0.0.1:{PORT}", worker_pid=WORKER_PID)
    assert exc.value.code == "ISOLATION_PROOF_REQUIRED"


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("process", {"pid": SERVER_PID + 1, "birth": "birth-1"}),
        ("process", {"pid": SERVER_PID, "birth": "different-birth"}),
        ("port", PORT + 1),
        ("config_sha256", "0" * 64),
    ],
)
def test_remote_addr_valve_rejects_probe_bound_to_another_process_port_or_config(
    tmp_path, monkeypatch, field, value
):
    receipt_path, receipt, _sha = _fixture(tmp_path, monkeypatch)
    receipt["probe"][field] = value
    receipt_path.write_text(json.dumps(receipt), encoding="utf-8")
    monkeypatch.setattr(isolation, "_socket_rows", lambda _port: _socket_rows())

    with pytest.raises(ExecutionContractError) as exc:
        isolation.verify_owned_server(receipt_path, endpoint=f"127.0.0.1:{PORT}", worker_pid=WORKER_PID)
    assert exc.value.code == "ISOLATION_PROOF_REQUIRED"


@pytest.mark.parametrize(
    "change",
    [
        {"loopback": {"authenticated_api_success": False}},
        {"nonloopback": {"api_exception_http_status": 200}},
        {"nonloopback": {"api_exception_http_status": 403, "address": "127.0.0.2"}},
        {"nonloopback": {"api_exception_http_status": 403, "address": "0.0.0.0"}},
    ],
)
def test_remote_addr_valve_requires_authenticated_loopback_and_specific_nonloopback_403(
    tmp_path, monkeypatch, change
):
    receipt_path, receipt, _sha = _fixture(tmp_path, monkeypatch)
    for section, values in change.items():
        receipt["probe"][section].update(values)
    receipt_path.write_text(json.dumps(receipt), encoding="utf-8")
    monkeypatch.setattr(isolation, "_socket_rows", lambda _port: _socket_rows())

    with pytest.raises(ExecutionContractError) as exc:
        isolation.verify_owned_server(receipt_path, endpoint=f"127.0.0.1:{PORT}", worker_pid=WORKER_PID)
    assert exc.value.code == "ISOLATION_PROOF_REQUIRED"


@pytest.mark.parametrize(
    ("section", "field", "value"),
    [
        ("loopback", "address", "127.0.0.2"),
        ("loopback", "port", PORT + 1),
        ("nonloopback", "port", PORT + 1),
    ],
)
def test_remote_addr_valve_binds_both_probe_addresses_to_current_port(
    tmp_path, monkeypatch, section, field, value
):
    receipt_path, receipt, _sha = _fixture(tmp_path, monkeypatch)
    receipt["probe"][section][field] = value
    receipt_path.write_text(json.dumps(receipt), encoding="utf-8")
    monkeypatch.setattr(isolation, "_socket_rows", lambda _port: _socket_rows())

    with pytest.raises(ExecutionContractError) as exc:
        isolation.verify_owned_server(receipt_path, endpoint=f"127.0.0.1:{PORT}", worker_pid=WORKER_PID)
    assert exc.value.code == "ISOLATION_PROOF_REQUIRED"


def test_remote_addr_valve_rejects_http_only_probe_without_java_api_kind(tmp_path, monkeypatch):
    receipt_path, receipt, _sha = _fixture(tmp_path, monkeypatch)
    receipt["probe"].pop("probe_kind")
    receipt["probe"]["loopback"]["http_status"] = 200
    receipt["probe"]["nonloopback"]["http_status"] = 403
    receipt_path.write_text(json.dumps(receipt), encoding="utf-8")
    monkeypatch.setattr(isolation, "_socket_rows", lambda _port: _socket_rows())

    with pytest.raises(ExecutionContractError) as exc:
        isolation.verify_owned_server(receipt_path, endpoint=f"127.0.0.1:{PORT}", worker_pid=WORKER_PID)
    assert exc.value.code == "ISOLATION_PROOF_REQUIRED"


def test_remote_addr_valve_rejects_extra_or_nonloopback_current_client(tmp_path, monkeypatch):
    receipt_path, _receipt, _sha = _fixture(tmp_path, monkeypatch)
    rows = _socket_rows()
    rows.append({
        "pid": 888,
        "endpoint": f"192.168.100.152:{PORT}->192.168.100.153:60123",
        "state": "ESTABLISHED",
    })
    monkeypatch.setattr(isolation, "_socket_rows", lambda _port: rows)

    with pytest.raises(ExecutionContractError) as exc:
        isolation.verify_owned_server(receipt_path, endpoint=f"127.0.0.1:{PORT}", worker_pid=WORKER_PID)
    assert exc.value.code == "ISOLATION_PROOF_REQUIRED"


def test_remote_addr_valve_does_not_accept_self_reported_unreviewed_target_or_hash(tmp_path, monkeypatch):
    receipt_path, receipt, _sha = _fixture(tmp_path, monkeypatch)
    receipt["config"]["content_sha256_after_apply"] = "a" * 64
    receipt_path.write_text(json.dumps(receipt), encoding="utf-8")
    monkeypatch.setattr(isolation, "_socket_rows", lambda _port: _socket_rows())

    with pytest.raises(ExecutionContractError) as exc:
        isolation.verify_owned_server(receipt_path, endpoint=f"127.0.0.1:{PORT}", worker_pid=WORKER_PID)
    assert exc.value.code == "ISOLATION_PROOF_REQUIRED"


def _add_server_access_log_proof(receipt_path: Path, receipt: dict, *, log_name: str = "mphserverlocalhost_access_log.2026-09-20.txt") -> Path:
    run_dir = receipt_path.parent
    log_dir = run_dir / "prefs" / "tomcat" / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    log_path = log_dir / log_name
    lines = (
        b'127.0.0.1 - - [20/Sep/2026:08:00:01 +0800] "GET /webbridge/websocket HTTP/1.1" 101 -\n'
        b'192.168.100.152 - - [20/Sep/2026:08:00:01 +0800] "GET /webbridge/websocket HTTP/1.1" 403 -\n'
    )
    log_path.write_bytes(lines)
    entries = [
        {"peer": "127.0.0.1", "method": "GET", "route": "/webbridge/websocket", "http_status": 101},
        {"peer": "192.168.100.152", "method": "GET", "route": "/webbridge/websocket", "http_status": 403},
    ]
    receipt["paths"] = {"run_dir": str(run_dir), "prefs": str(run_dir / "prefs")}
    receipt["probe"]["status"] = "FAIL"
    receipt["probe"]["nonloopback"].update({
        "status": "FAIL",
        "api_exception_http_status": None,
        "java_api": {
            "address": "192.168.100.152",
            "port": PORT,
            "authenticated": False,
            "status": "REJECTED",
            "error_code": "ENGINE_CALL_FAILED",
        },
        "http_probe": {
            "address": "192.168.100.152",
            "source_bind_requested": "192.168.100.152",
            "route": "/webbridge/websocket",
            "status": "HTTP_RESPONSE",
            "http_status": 403,
            "local_endpoint": {"host": "192.168.100.152", "port": 60123},
            "peer_endpoint": {"host": "192.168.100.152", "port": PORT},
        },
        "server_access_log": {
            "status": "PASS",
            "phase": "java_connect_only",
            "path": str(log_path),
            "inode": int(log_path.stat().st_ino),
            "offset_before": 0,
            "offset_after": len(lines),
            "segment_sha256": hashlib.sha256(lines).hexdigest(),
            "java_request_id": "g2-nonloopback-java-1",
            "started_at": "2026-09-20T00:00:00Z",
            "finished_at": "2026-09-20T00:00:02Z",
            "raw_probe_started_after_capture": True,
            "entries": entries,
        },
    })
    receipt_path.write_text(json.dumps(receipt), encoding="utf-8")
    return log_path


def test_remote_addr_valve_accepts_server_access_log_when_java_exception_has_no_http_status(tmp_path, monkeypatch):
    receipt_path, receipt, _sha = _fixture(tmp_path, monkeypatch)
    log_path = _add_server_access_log_proof(receipt_path, receipt)
    monkeypatch.setattr(isolation, "_socket_rows", lambda _port: _socket_rows())

    result = isolation.verify_owned_server(
        receipt_path,
        endpoint=f"127.0.0.1:{PORT}",
        worker_pid=WORKER_PID,
    )

    assert result["verified"] is True
    assert result["api_probe"]["denial_evidence_source"] == "server_access_log"
    assert result["api_probe"]["nonloopback_api_exception_http_status"] is None
    assert result["api_probe"]["server_access_log"]["path"] == str(log_path)
    assert result["api_probe"]["server_access_log"]["parsed_nonloopback_entry"] == {
        "peer": "192.168.100.152",
        "method": "GET",
        "route": "/webbridge/websocket",
        "http_status": 403,
    }


def test_server_access_log_segment_remains_valid_when_log_appends_after_capture(tmp_path, monkeypatch):
    receipt_path, receipt, _sha = _fixture(tmp_path, monkeypatch)
    log_path = _add_server_access_log_proof(receipt_path, receipt)
    log_path.open("ab").write(
        b'127.0.0.1 - - [20/Sep/2026:08:00:02 +0800] "GET /webbridge/websocket HTTP/1.1" 101 -\n'
    )
    monkeypatch.setattr(isolation, "_socket_rows", lambda _port: _socket_rows())

    result = isolation.verify_owned_server(receipt_path, endpoint=f"127.0.0.1:{PORT}", worker_pid=WORKER_PID)

    assert result["verified"] is True
    assert result["api_probe"]["server_access_log"]["offset_after"] < log_path.stat().st_size


@pytest.mark.parametrize("mutation", ["segment_hash", "inode", "wrong_route", "root_page", "extra_nonloop", "old_log"])
def test_server_access_log_rejects_tampering_or_unrelated_entries(tmp_path, monkeypatch, mutation):
    receipt_path, receipt, _sha = _fixture(tmp_path, monkeypatch)
    log_path = _add_server_access_log_proof(receipt_path, receipt)
    access_log = receipt["probe"]["nonloopback"]["server_access_log"]
    if mutation == "segment_hash":
        access_log["segment_sha256"] = "0" * 64
    elif mutation == "inode":
        old = log_path.read_bytes()
        log_path.unlink()
        log_path.write_bytes(old)
    elif mutation in {"wrong_route", "root_page"}:
        old = log_path.read_bytes()
        replacement = b"/wrong" if mutation == "wrong_route" else b"/"
        changed = old.replace(b"/webbridge/websocket", replacement)
        log_path.write_bytes(changed)
        access_log["offset_after"] = len(changed)
        access_log["segment_sha256"] = hashlib.sha256(changed).hexdigest()
        access_log["entries"][0]["route"] = replacement.decode()
        access_log["entries"][1]["route"] = replacement.decode()
    elif mutation == "extra_nonloop":
        extra = b'192.168.100.152 - - [20/Sep/2026:08:00:02 +0800] "GET /webbridge/websocket HTTP/1.1" 403 -\n'
        changed = log_path.read_bytes() + extra
        log_path.write_bytes(changed)
        access_log["offset_after"] = len(changed)
        access_log["segment_sha256"] = hashlib.sha256(changed).hexdigest()
        access_log["entries"].append({"peer": "192.168.100.152", "method": "GET", "route": "/webbridge/websocket", "http_status": 403})
    elif mutation == "old_log":
        old_path = log_path.parent / "mphserverlocalhost_access_log.2020-01-01.txt"
        old_line = b'192.168.100.152 - - [20/Sep/2026:08:00:01 +0800] "GET /webbridge/websocket HTTP/1.1" 403 -\n'
        old_path.write_bytes(old_line)
        access_log["path"] = str(old_path)
        access_log["inode"] = int(old_path.stat().st_ino)
        access_log["offset_after"] = len(old_line)
        access_log["segment_sha256"] = hashlib.sha256(old_line).hexdigest()
        access_log["entries"] = [{"peer": "192.168.100.152", "method": "GET", "route": "/webbridge/websocket", "http_status": 403}]
    receipt_path.write_text(json.dumps(receipt), encoding="utf-8")
    monkeypatch.setattr(isolation, "_socket_rows", lambda _port: _socket_rows())

    with pytest.raises(ExecutionContractError) as exc:
        isolation.verify_owned_server(receipt_path, endpoint=f"127.0.0.1:{PORT}", worker_pid=WORKER_PID)
    assert exc.value.code == "ISOLATION_PROOF_REQUIRED"


@pytest.mark.parametrize("mutation", ["missing_raw", "java_success", "generic_failure_only", "wrong_private_path"])
def test_server_access_log_rejects_missing_companion_proof(tmp_path, monkeypatch, mutation):
    receipt_path, receipt, _sha = _fixture(tmp_path, monkeypatch)
    log_path = _add_server_access_log_proof(receipt_path, receipt)
    nonloopback = receipt["probe"]["nonloopback"]
    if mutation == "missing_raw":
        nonloopback.pop("http_probe")
    elif mutation == "java_success":
        nonloopback["java_api"]["status"] = "UNEXPECTED_SUCCESS"
        nonloopback["java_api"]["authenticated"] = True
    elif mutation == "generic_failure_only":
        nonloopback.pop("server_access_log")
    elif mutation == "wrong_private_path":
        outside = tmp_path / "unrelated" / log_path.name
        outside.parent.mkdir()
        outside.write_bytes(log_path.read_bytes())
        nonloopback["server_access_log"]["path"] = str(outside)
    receipt_path.write_text(json.dumps(receipt), encoding="utf-8")
    monkeypatch.setattr(isolation, "_socket_rows", lambda _port: _socket_rows())

    with pytest.raises(ExecutionContractError) as exc:
        isolation.verify_owned_server(receipt_path, endpoint=f"127.0.0.1:{PORT}", worker_pid=WORKER_PID)
    assert exc.value.code == "ISOLATION_PROOF_REQUIRED"
