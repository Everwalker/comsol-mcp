"""Control-plane tests for the persistent Java Worker; they never attach COMSOL."""
from __future__ import annotations

import json
import os
import shutil
import socket
import subprocess
import sys
import threading
import time
import zipfile
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from types import SimpleNamespace
from pathlib import Path

import pytest

from comsol_mcp import _atomic_save, _java_worker as java_worker
from comsol_mcp._java_worker import JavaWorkerError, JavaWorkerPaths, PersistentJavaWorker, RemoteClient, RemoteModel


DEFAULT_COMSOL = Path(r"C:\Program Files\COMSOL\COMSOL64\Multiphysics") if sys.platform == "win32" else Path("/Applications/COMSOL64/Multiphysics")
if sys.platform == "win32" and not DEFAULT_COMSOL.exists():
    DEFAULT_COMSOL = Path(r"C:\Program Files\COMSOL\COMSOL63\Multiphysics")
COMSOL_ROOT = Path(os.environ.get("COMSOL_ROOT", str(DEFAULT_COMSOL))).expanduser()
DEFAULT_JDK11 = Path(r"C:\Users\Everwalker\jdk11") if sys.platform == "win32" else Path("/Library/Java/JavaVirtualMachines/amazon-corretto-11.jdk/Contents/Home")
JDK11 = Path(
    os.environ.get("COMSOL_JAVA_HOME")
    or os.environ.get("JAVA_HOME")
    or str(DEFAULT_JDK11)
).expanduser()
JAVAC_NAME = "javac.exe" if sys.platform == "win32" else "javac"


def _worker_build_environment_available() -> bool:
    return (COMSOL_ROOT / "bin" / "comsolclientpath.txt").is_file() and (JDK11 / "bin" / JAVAC_NAME).is_file()


def _single_reply_socket(reply_factory):
    """Start a tiny token-authenticated endpoint for real Worker socket tests."""
    listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    listener.bind(("127.0.0.1", 0))
    listener.listen(1)
    requests = []
    errors = []

    def serve_once():
        try:
            conn, _ = listener.accept()
            with conn, conn.makefile("rwb") as stream:
                auth = json.loads(stream.readline())
                if not auth.get("token"):
                    raise AssertionError("Worker did not authenticate the socket request")
                stream.write(b'{"ok":true,"generation":5}\n')
                stream.flush()
                body = json.loads(stream.readline())
                requests.append(body)
                response = reply_factory(body)
                stream.write((json.dumps(response, separators=(",", ":")) + "\n").encode())
                stream.flush()
        except BaseException as exc:  # surfaced in the owning test thread below
            errors.append(exc)

    thread = threading.Thread(target=serve_once, daemon=True)
    thread.start()
    return listener, thread, listener.getsockname()[1], requests, errors


@contextmanager
def _socket_backed_worker(tmp_path, reply_factory):
    listener, thread, port, requests, errors = _single_reply_socket(reply_factory)
    worker = PersistentJavaWorker(
        JavaWorkerPaths(COMSOL_ROOT, JDK11, project_root=tmp_path),
        state_dir=tmp_path / "socket-worker-state",
    )
    worker._token = "socket-test-token"
    worker._port = port
    worker._generation = 5
    try:
        yield worker, requests
    finally:
        worker.close()
        listener.close()
        thread.join(timeout=2)
        if errors:
            raise AssertionError(f"fake Worker socket failed: {errors[0]}") from errors[0]


_WINDOWS_ACL_SCRIPT = r"""
$ErrorActionPreference = 'Stop'
$acl = Get-Acl -LiteralPath $env:COMSOL_MCP_ENDPOINT_PATH
$identity = [System.Security.Principal.WindowsIdentity]::GetCurrent()
$access = @(
    foreach ($ace in $acl.Access) {
        try {
            $sid = $ace.IdentityReference.Translate([System.Security.Principal.SecurityIdentifier]).Value
        } catch {
            $sid = [string]$ace.IdentityReference.Value
        }
        [pscustomobject]@{
            sid = [string]$sid
            type = [string]$ace.AccessControlType
            rights = [int64]$ace.FileSystemRights
        }
    }
)
[pscustomobject]@{
    current_user_sid = [string]$identity.User.Value
    owner_sid = [string]$acl.GetOwner([System.Security.Principal.SecurityIdentifier]).Value
    access = $access
} | ConvertTo-Json -Compress -Depth 8
"""


def _windows_acl_metadata(endpoint: Path) -> dict:
    powershell = Path(os.environ.get("WINDIR", r"C:\Windows")) / "System32" / "WindowsPowerShell" / "v1.0" / "powershell.exe"
    environment = dict(os.environ)
    environment["COMSOL_MCP_ENDPOINT_PATH"] = str(endpoint)
    completed = subprocess.run(
        [
            str(powershell),
            "-NoLogo",
            "-NoProfile",
            "-NonInteractive",
            "-ExecutionPolicy",
            "Bypass",
            "-Command",
            _WINDOWS_ACL_SCRIPT,
        ],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        env=environment,
        check=False,
    )
    assert completed.returncode == 0, f"Get-Acl failed: {completed.stderr.strip()}"
    try:
        metadata = json.loads(completed.stdout)
    except json.JSONDecodeError as exc:
        raise AssertionError(f"Get-Acl returned invalid JSON: {completed.stdout!r}") from exc
    assert isinstance(metadata, dict)
    return metadata


def _set_windows_owner(path: Path, sid: str) -> None:
    powershell = Path(os.environ.get("WINDIR", r"C:\Windows")) / "System32" / "WindowsPowerShell" / "v1.0" / "powershell.exe"
    environment = dict(os.environ)
    environment["COMSOL_MCP_OWNER_PATH"] = str(path)
    environment["COMSOL_MCP_OWNER_SID"] = sid
    script = r"""
$ErrorActionPreference = 'Stop'
$acl = Get-Acl -LiteralPath $env:COMSOL_MCP_OWNER_PATH
$acl.SetOwner([System.Security.Principal.SecurityIdentifier]$env:COMSOL_MCP_OWNER_SID)
Set-Acl -LiteralPath $env:COMSOL_MCP_OWNER_PATH -AclObject $acl
"""
    completed = subprocess.run(
        [str(powershell), "-NoLogo", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-Command", script],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        env=environment,
        check=False,
    )
    assert completed.returncode == 0, f"Set-Acl owner failed: {completed.stderr.strip()}"


def _validate_windows_endpoint_acl_metadata(endpoint: Path, metadata: dict) -> None:
    current_sid = metadata.get("current_user_sid")
    owner_sid = metadata.get("owner_sid")
    entries = metadata.get("access")
    assert isinstance(current_sid, str) and current_sid.startswith("S-1-")
    assert owner_sid in {current_sid, "S-1-5-18", "S-1-5-32-544"}
    assert isinstance(entries, list)
    safe_sids = {current_sid, "S-1-5-18", "S-1-5-32-544"}
    raw_safe_sids = safe_sids | {"S-1-3-4"}
    allow_entries = [entry for entry in entries if entry.get("type") == "Allow"]
    allow_sids = {entry.get("sid") for entry in allow_entries}
    assert allow_sids and allow_sids <= raw_safe_sids, f"unexpected endpoint Allow SIDs: {sorted(allow_sids)}"
    normalized_allow_sids = {owner_sid if sid == "S-1-3-4" else sid for sid in allow_sids}
    assert normalized_allow_sids <= safe_sids

    # OWNER RIGHTS and group membership are only metadata here. Opening the
    # actual endpoint with a writable handle proves the current token has the
    # required read/write access without modifying the file or simulating ACL
    # evaluation in Python.
    with endpoint.open("r+b"):
        pass


def _assert_windows_endpoint_acl(endpoint: Path) -> None:
    """Require the endpoint DACL to be limited to the worker's safe principals."""
    _validate_windows_endpoint_acl_metadata(endpoint, _windows_acl_metadata(endpoint))


@pytest.fixture(scope="module")
def worker(tmp_path_factory):
    if not _worker_build_environment_available():
        pytest.skip("COMSOL 6.4/JDK 11 local Worker build environment unavailable")
    instance = PersistentJavaWorker(JavaWorkerPaths(COMSOL_ROOT, JDK11), state_dir=tmp_path_factory.mktemp("worker-state"))
    instance.start()
    yield instance
    instance.close()


def test_java_worker_is_loopback_authenticated_and_reports_health(worker):
    host, port = worker.endpoint
    assert host == "127.0.0.1"
    health = worker.health()
    assert health["status"] == "HEALTHY"
    assert health["instance_id"]
    assert "token" not in worker.runtime_metadata()
    with socket.create_connection((host, port), timeout=1) as conn:
        stream = conn.makefile("rwb")
        stream.write(b'{"token":"wrong"}\n'); stream.flush()
        reply = json.loads(stream.readline())
    assert reply["code"] == "AUTH_FAILED"


def test_java_worker_keeps_original_request_queryable_after_nonblocking_submit(worker):
    request_id = "control-only-not-connected"
    submitted = worker.submit("model", {"tag": "not-connected"}, request_id=request_id, queue_timeout_s=0, rpc_timeout_s=1)
    assert submitted["request_id"] == request_id
    deadline = time.monotonic() + 2
    status = worker.status(request_id)
    while status["status"] in {"QUEUED", "RUNNING"} and time.monotonic() < deadline:
        time.sleep(0.02); status = worker.status(request_id)
    assert status["status"] == "FAILED"
    assert status["failure"]["code"] == "ENGINE_CALL_FAILED"
    assert worker.health(timeout_s=1)["status"] == "HEALTHY"


@pytest.mark.parametrize(
    ("reply_kind", "known_before"),
    [("wrong_id", False), ("missing_id", False), ("wrong_id", True)],
)
def test_socket_worker_rejects_uncorrelated_task_replies_without_replay(
    tmp_path, reply_kind, known_before,
):
    request_id = "socket-correlation-control"
    events = []

    def response(body):
        reply = {"ok": True, "status": "SUCCEEDED", "generation": 99}
        if reply_kind == "wrong_id":
            reply["request_id"] = f"foreign-{body['request_id']}"
        return reply

    with _socket_backed_worker(tmp_path, response) as (worker, received):
        known_reply = {"request_id": request_id, "status": "SUCCEEDED", "result": {"kept": True}}
        if known_before:
            worker._known_requests[request_id] = known_reply
        with worker.operation_context("socket-correlation-operation", on_request_event=events.append):
            with pytest.raises(JavaWorkerError, match="request_id") as raised:
                worker.submit(
                    "model", {"tag": "no-engine"}, request_id=request_id,
                    queue_timeout_s=0, rpc_timeout_s=1,
                )

        assert raised.value.reply["execution_state_unknown"] is True
        assert received == [{
            "type": "model", "request_id": request_id, "tag": "no-engine",
            "queue_timeout_ms": 0,
        }]
        assert worker.generation == 5  # the uncorrelated generation 99 was ignored
        assert [event["phase"] for event in events] == ["submitted", "unresponsive"]
        assert not any(event["phase"] == "observed" for event in events)
        if known_before:
            assert worker._known_requests[request_id] is known_reply
        else:
            assert worker._known_requests[request_id] == {
                "request_id": request_id,
                "status": "UNKNOWN",
                "reason": (
                    "worker_response_request_id_mismatch" if reply_kind == "wrong_id"
                    else "worker_response_request_id_missing"
                ),
            }


def test_socket_worker_accepts_exact_request_id_and_caches_task_reply(tmp_path):
    request_id = "socket-correlation-exact"

    def response(body):
        return {
            "ok": True, "request_id": body["request_id"], "status": "RUNNING",
            "generation": 8,
        }

    with _socket_backed_worker(tmp_path, response) as (worker, received):
        reply = worker.submit(
            "model", {"tag": "no-engine"}, request_id=request_id,
            queue_timeout_s=0, rpc_timeout_s=1,
        )
        assert reply["request_id"] == request_id
        assert worker._known_requests[request_id] == reply
        assert worker.generation == 8
        assert len(received) == 1


@pytest.mark.parametrize(
    ("request_kind", "request_id", "code"),
    [
        ("status", "missing-status-control", "REQUEST_NOT_FOUND"),
        ("submit", "conflict-control", "IDEMPOTENCY_KEY_CONFLICT"),
        ("unknown", "unknown-command-control", "UNKNOWN_COMMAND"),
    ],
)
def test_socket_worker_preserves_idless_protocol_rejection_diagnostics(
    tmp_path, request_kind, request_id, code,
):
    with _socket_backed_worker(
        tmp_path, lambda _body: {"ok": False, "code": code, "message": "protocol refusal"},
    ) as (worker, received):
        if request_kind == "status":
            reply = worker.status(request_id)
        elif request_kind == "submit":
            reply = worker.submit(
                "model", {"tag": "different-body"}, request_id=request_id,
                queue_timeout_s=0, rpc_timeout_s=1,
            )
        else:
            reply = worker._request(
                {"type": "not_a_worker_command", "request_id": request_id},
                timeout_s=1,
            )
        assert reply["code"] == code
        assert request_id not in worker._known_requests
        assert len(received) == 1


def test_java_worker_persists_private_endpoint_metadata(worker, tmp_path_factory):
    # The endpoint has the token needed to reconnect after a control restart,
    # so it must be private and callers must never create a replacement to
    # replay an unknown request.
    endpoint = worker.state_dir / "worker_endpoint.json"
    saved = json.loads(endpoint.read_text())
    assert saved["host"] == "127.0.0.1"
    assert saved["port"] == worker.endpoint[1]
    assert len(saved["token"]) >= 32
    assert isinstance(saved["process_start_epoch_ms"], int)
    assert saved["process_start_epoch_ms"] > 0
    if sys.platform == "win32":
        _assert_windows_endpoint_acl(endpoint)
    else:
        assert endpoint.stat().st_mode & 0o077 == 0


def test_windows_acl_owner_rights_is_resolved_to_verified_owner(tmp_path):
    endpoint = tmp_path / "worker_endpoint.json"
    endpoint.write_bytes(b"{}\n")
    current_sid = "S-1-5-21-100-200-300-400"
    metadata = {
        "current_user_sid": current_sid,
        "owner_sid": "S-1-5-32-544",
        "access": [
            {"sid": "S-1-3-4", "type": "Allow"},
            {"sid": "S-1-5-18", "type": "Allow"},
            {"sid": "S-1-5-32-544", "type": "Allow"},
        ],
    }
    _validate_windows_endpoint_acl_metadata(endpoint, metadata)
    assert endpoint.read_bytes() == b"{}\n"


@pytest.mark.parametrize(
    "metadata",
    [
        {
            "current_user_sid": "S-1-5-21-100-200-300-400",
            "owner_sid": "S-1-5-21-foreign",
            "access": [{"sid": "S-1-5-21-100-200-300-400", "type": "Allow"}],
        },
        {
            "current_user_sid": "S-1-5-21-100-200-300-400",
            "owner_sid": "S-1-5-21-100-200-300-400",
            "access": [{"sid": "S-1-1-0", "type": "Allow"}],
        },
    ],
    ids=["foreign-owner", "everyone-allow"],
)
def test_windows_acl_metadata_rejects_foreign_owner_and_broad_allow(tmp_path, metadata):
    endpoint = tmp_path / "worker_endpoint.json"
    endpoint.write_bytes(b"{}\n")
    with pytest.raises(AssertionError):
        _validate_windows_endpoint_acl_metadata(endpoint, metadata)


def test_windows_jdk_executables_keep_space_containing_paths(monkeypatch, tmp_path):
    paths = JavaWorkerPaths(tmp_path / "COMSOL 6.4", tmp_path / "JDK 11", project_root=tmp_path, platform_name="nt")
    assert paths.executable("java").name == "java.exe"
    assert paths.executable("javac").name == "javac.exe"
    assert "JDK 11" in str(paths.executable("java"))


def test_windows_compile_uses_exe_semicolon_classpath_and_utf8_decoding(monkeypatch, tmp_path):
    root, jdk = tmp_path / "COMSOL 6.4", tmp_path / "JDK 11"
    (root / "bin").mkdir(parents=True); (root / "apiplugins").mkdir()
    (root / "bin" / "comsolclientpath.txt").write_text("client.jar\nsecond.jar\n")
    (root / "apiplugins" / "client.jar").write_bytes(b"jar")
    (root / "apiplugins" / "second.jar").write_bytes(b"jar")
    (jdk / "bin").mkdir(parents=True)
    (jdk / "bin" / "java.exe").write_bytes(b"")
    (jdk / "bin" / "javac.exe").write_bytes(b"")
    observed = {}
    def compilation(command, **kwargs):
        observed.update(command=command, kwargs=kwargs)
        return SimpleNamespace(returncode=1, stdout="", stderr="编译失败")
    monkeypatch.setattr(java_worker.subprocess, "run", compilation)
    worker = PersistentJavaWorker(JavaWorkerPaths(root, jdk, project_root=tmp_path, platform_name="nt"), state_dir=tmp_path / "state with spaces")
    with pytest.raises(JavaWorkerError, match="编译失败"):
        worker.start()
    assert observed["command"][0].endswith("javac.exe")
    assert ";" in observed["command"][2]
    assert observed["kwargs"]["encoding"] == "utf-8"
    assert observed["kwargs"]["errors"] == "replace"


def test_windows_classpath_prefers_complete_apiplugins_without_scanning_plugins(tmp_path):
    root = tmp_path / "COMSOL 6.4"
    (root / "bin").mkdir(parents=True); (root / "plugins").mkdir(); (root / "apiplugins").mkdir()
    (root / "bin" / "comsolclientpath.txt").write_text("com.comsol.api_1.0.0.jar\n")
    expected = root / "apiplugins" / "com.comsol.api_1.0.0.jar"
    expected.write_bytes(b"api")
    (root / "plugins" / "unlisted.jar").write_bytes(b"must-not-be-scanned")
    (root / "plugins" / "com.comsol.api_1.0.0.jar").write_bytes(b"alternate-complete-single-entry")
    paths = JavaWorkerPaths(root, tmp_path / "JDK", project_root=tmp_path, platform_name="nt")
    classpath, _, count, _ = paths.classpath()
    assert classpath == str(expected)
    assert count == 1


def test_windows_classpath_uses_complete_plugins_only_when_apiplugins_is_incomplete(tmp_path):
    root = tmp_path / "COMSOL 6.4"
    (root / "bin").mkdir(parents=True); (root / "plugins").mkdir(); (root / "apiplugins").mkdir()
    (root / "bin" / "comsolclientpath.txt").write_text("first.jar\nsecond.jar\n")
    (root / "apiplugins" / "first.jar").write_bytes(b"partial")
    first, second = root / "plugins" / "first.jar", root / "plugins" / "second.jar"
    first.write_bytes(b"first"); second.write_bytes(b"second")
    paths = JavaWorkerPaths(root, tmp_path / "JDK", project_root=tmp_path, platform_name="nt")
    classpath, _, count, _ = paths.classpath()
    assert classpath == ";".join((str(first), str(second)))
    assert count == 2


def test_windows_classpath_rejects_partial_roots_instead_of_mixing(tmp_path):
    root = tmp_path / "COMSOL 6.4"
    (root / "bin").mkdir(parents=True); (root / "plugins").mkdir(); (root / "apiplugins").mkdir()
    (root / "bin" / "comsolclientpath.txt").write_text("first.jar\nsecond.jar\n")
    (root / "apiplugins" / "first.jar").write_bytes(b"first")
    (root / "plugins" / "second.jar").write_bytes(b"second")
    paths = JavaWorkerPaths(root, tmp_path / "JDK", project_root=tmp_path, platform_name="nt")
    with pytest.raises(JavaWorkerError, match="no complete official COMSOL classpath root"):
        paths.classpath()


def test_windows_classpath_missing_manifest_entry_fails_precisely(tmp_path):
    root = tmp_path / "COMSOL 6.4"
    (root / "bin").mkdir(parents=True); (root / "plugins").mkdir()
    (root / "bin" / "comsolclientpath.txt").write_text("missing.jar\n")
    paths = JavaWorkerPaths(root, tmp_path / "JDK", project_root=tmp_path, platform_name="nt")
    with pytest.raises(JavaWorkerError, match="missing.jar"):
        paths.classpath()


def test_reused_worker_pid_with_different_creation_time_is_stale(monkeypatch, tmp_path):
    endpoint = tmp_path / "worker_endpoint.json"
    endpoint.write_text(json.dumps({"pid": 4242, "port": 1, "token": "x" * 32,
                                    "generation": 5, "process_start_epoch_ms": 100}))
    probe = PersistentJavaWorker(JavaWorkerPaths(COMSOL_ROOT, JDK11, project_root=tmp_path), state_dir=tmp_path)
    monkeypatch.setattr(java_worker, "process_identity", lambda pid: {"alive": True, "start_epoch_ms": 101})
    assert probe._attach_existing(endpoint) is None
    assert probe._next_generation == 6
    assert not endpoint.exists()


def test_windows_unverifiable_live_worker_never_starts_replacement(monkeypatch, tmp_path):
    endpoint = tmp_path / "worker_endpoint.json"
    endpoint.write_text(json.dumps({"pid": 4242, "port": 1, "token": "x" * 32,
                                    "generation": 5, "process_start_epoch_ms": 100}))
    probe = PersistentJavaWorker(JavaWorkerPaths(COMSOL_ROOT, JDK11, project_root=tmp_path), state_dir=tmp_path)
    monkeypatch.setattr(java_worker, "process_identity", lambda pid: {"alive": True, "start_epoch_ms": None})
    probe.paths = JavaWorkerPaths(COMSOL_ROOT, JDK11, project_root=tmp_path, platform_name="nt")
    with pytest.raises(JavaWorkerError, match="identity cannot be verified"):
        probe._attach_existing(endpoint)
    assert endpoint.exists()


def test_java_worker_reconnects_existing_endpoint_without_spawning_or_replaying(worker):
    reattached = PersistentJavaWorker(worker.paths, state_dir=worker.state_dir)
    observed = reattached.start()
    assert observed["status"] == "HEALTHY"
    assert reattached.endpoint == worker.endpoint
    assert reattached.generation == worker.generation
    # It attached to the existing child, therefore close must not own or kill it.
    reattached.close()
    assert worker.health(timeout_s=1)["status"] == "HEALTHY"


@pytest.mark.skipif(sys.platform != "win32", reason="Windows owner semantics only")
def test_windows_existing_foreign_lock_root_is_rejected_without_takeover(tmp_path):
    lock_root = tmp_path / "foreign-lock-root"
    lock_root.mkdir()
    _set_windows_owner(lock_root, "S-1-5-32-544")
    assert _windows_acl_metadata(lock_root)["owner_sid"] == "S-1-5-32-544"
    paths = JavaWorkerPaths(COMSOL_ROOT, JDK11, project_root=tmp_path, global_lock_root=lock_root)
    instance = PersistentJavaWorker(paths, state_dir=tmp_path / "worker-state")
    try:
        instance.start()
        rejected = instance.submit("lock_selftest", {"port": 65001}, request_id="foreign-root", rpc_timeout_s=1)
        assert rejected["status"] == "FAILED"
        assert rejected["failure"]["code"] == "PERMISSION_DENIED"
        assert _windows_acl_metadata(lock_root)["owner_sid"] == "S-1-5-32-544"
    finally:
        instance.close()


def test_dead_owned_worker_increments_generation_before_replacement(tmp_path):
    if not _worker_build_environment_available():
        pytest.skip("COMSOL 6.4/JDK 11 local Worker build environment unavailable")
    first = PersistentJavaWorker(JavaWorkerPaths(COMSOL_ROOT, JDK11), state_dir=tmp_path)
    first.start(); previous_generation = first.generation; first.close()
    endpoint = tmp_path / "worker_endpoint.json"
    saved = json.loads(endpoint.read_text())
    # A stale endpoint's PID is intentionally made impossible here. A PID that
    # is alive but cannot serve its recorded endpoint is a reconciliation
    # blocker, never a reason to start another worker.
    saved["pid"] = 999999
    endpoint.write_text(json.dumps(saved))
    endpoint.chmod(0o600)
    replacement = PersistentJavaWorker(JavaWorkerPaths(COMSOL_ROOT, JDK11), state_dir=tmp_path)
    try:
        replacement.start()
        assert replacement.generation == previous_generation + 1
    finally:
        replacement.close()


def test_same_request_id_with_different_body_is_rejected_without_replay(worker):
    request_id = "idempotency-conflict-control-only"
    worker.submit("model", {"tag": "first"}, request_id=request_id, queue_timeout_s=0, rpc_timeout_s=1)
    conflict = worker.submit("model", {"tag": "second"}, request_id=request_id, queue_timeout_s=0, rpc_timeout_s=1)
    assert conflict["code"] == "IDEMPOTENCY_KEY_CONFLICT"
    assert worker.status(request_id)["request_id"] == request_id


def test_unknown_command_is_rejected_and_parallel_control_queries_remain_responsive(worker):
    unknown = worker._request({"type": "not_a_worker_command"}, timeout_s=1)
    assert unknown["code"] == "UNKNOWN_COMMAND"
    codec = worker._request({"type": "codec_selftest"}, timeout_s=1)
    assert codec["result"]["kind"] == "map"
    assert codec["result"]["nested"] == {"value": 7}
    assert codec["result"]["array"] == ["x", 2]
    assert codec["result"]["nonfinite_rejected"] is True
    assert codec["result"]["nonfinite_rejected_count"] == 4
    assert codec["result"]["nonfinite_code"] == "NON_FINITE_JSON_VALUE"
    assert codec["result"]["finite_passed"] is True
    assert codec["result"]["structured_failure_verified"] is True
    reflection = worker._request({"type": "reflection_selftest"}, timeout_s=1)
    assert reflection["result"]["duplicate_interface_tag"] == "resolved"
    assert reflection["result"]["numerical_allowed"] is True
    assert reflection["result"]["selection_init_dimension"] == {
        "valid_2": True,
        "valid_3": True,
        "oversized_rejected": True,
        "negative_rejected": True,
        "fractional_rejected": True,
    }
    with ThreadPoolExecutor(max_workers=8) as executor:
        replies = list(executor.map(lambda _: worker.health(timeout_s=1), range(16)))
    assert all(reply["status"] == "HEALTHY" for reply in replies)

def test_operation_context_publishes_request_linkage_and_redacts_credentials(worker):
    events: list[dict] = []
    with worker.operation_context("op-worker-link", on_request_event=events.append):
        worker.submit("model", {"tag": "no-engine", "password": "do-not-log"},
                      request_id="linked-control-only", queue_timeout_s=0, rpc_timeout_s=1)
    assert events[0]["operation_id"] == "op-worker-link"
    assert events[0]["request_id"] == "linked-control-only"
    assert len(events[0]["request_hash"]) == 64
    assert events[0]["metadata"]["password"] == "<redacted>"
    assert any(event["phase"] == "observed" for event in events)


def test_generation_sync_updates_private_reconnect_record(tmp_path):
    endpoint = tmp_path / "worker_endpoint.json"
    endpoint.write_text(json.dumps({"generation": 1, "token": "x" * 32, "pid": 1, "port": 1}))
    endpoint.chmod(0o600)
    probe = PersistentJavaWorker(JavaWorkerPaths(COMSOL_ROOT, JDK11, project_root=tmp_path), state_dir=tmp_path)
    probe._sync_generation({"result": {"generation": 2}})
    assert probe.generation == 2
    assert json.loads(endpoint.read_text())["generation"] == 2


class _SaveModel(RemoteModel):
    def __init__(self, project_root: Path, current_path: Path) -> None:
        self._worker = SimpleNamespace(generation=1, paths=JavaWorkerPaths(COMSOL_ROOT, JDK11, project_root=project_root))
        self._generation, self._handle, self.java_type = 1, "test", "test"
        self.current_path, self.raw_paths = current_path, []

    def _call(self, method, *args, **kwargs):
        assert method == "getFilePath"
        return str(self.current_path)

    def _raw_save(self, path: str, **kwargs):
        self.raw_paths.append(Path(path))
        with zipfile.ZipFile(path, "w") as archive:
            archive.writestr("model.txt", "complete candidate")


def test_remote_model_save_never_uses_empty_path_as_in_place_bypass(tmp_path):
    destination = tmp_path / "current.mph"
    model = _SaveModel(tmp_path, destination)
    published = model.save("")
    assert published["path"] == str(destination)
    assert model.raw_paths and model.raw_paths[0] != destination
    assert model.raw_paths[0].name.startswith(".current.mph.")
    assert destination.is_file()


def test_remote_model_save_rejects_paths_outside_configured_project_root(tmp_path):
    model = _SaveModel(tmp_path, tmp_path / "current.mph")
    with pytest.raises(Exception):
        model.save(str(tmp_path.parent / "outside.mph"))
    assert not model.raw_paths


def test_remote_model_save_case_copy_uses_no_clobber_python_policy_and_java_save_copy_true(tmp_path):
    class RawRemoteModel(RemoteModel):
        def __init__(self, project_root):
            self._worker = SimpleNamespace(generation=1, paths=JavaWorkerPaths(COMSOL_ROOT, JDK11, project_root=project_root))
            self._generation, self._handle, self.java_type = 1, "test", "test"
            self.calls = []
            self.source_paths = []

        def _call(self, method, *args):
            self.calls.append((method, args))
            if method == "getFilePath":
                return ""
            if method == "save" and args[1] is True:
                with zipfile.ZipFile(args[0], "w") as archive:
                    archive.writestr("model.txt", "case copy")
                return None
            if method == "save" and args[1] == "java":
                source_path = Path(args[0])
                self.source_paths.append(source_path)
                # Model the basename-derived public source identifier instead
                # of writing a fixed payload that would hide a temp-name bug.
                source_path.write_text(f"public class {source_path.stem} {{}}\n", encoding="utf-8")
                return None
            if method == "save" and args[1] == "m":
                source_path = Path(args[0])
                self.source_paths.append(source_path)
                source_path.write_text(f"function model = {source_path.stem}()\nend\n", encoding="utf-8")
                return None
            raise AssertionError((method, args))

    root = tmp_path.resolve()
    model = RawRemoteModel(root)
    destination = root / "case.mph"
    published = model.save(str(destination), True, overwrite=False)
    assert published["artifact"]["verified"] is True
    assert model.calls[-1][0] == "save"
    assert model.calls[-1][1][1] is True  # Java's boolean remains saveCopy=true.

    # The historical second-string overload remains a Java source export. It
    # is published as text and never passed through the MPH ZIP verifier.
    java_path = root / "Config.java"
    export = model.save(str(java_path), "java", overwrite=False)
    assert export["artifact"]["verified"] is True
    assert java_path.read_text(encoding="utf-8") == "public class Config {}\n"
    assert model.calls[-1][0] == "save"
    assert model.calls[-1][1][1] == "java"
    source_path = model.source_paths[-1]
    assert source_path.name == java_path.name
    assert source_path.parent != java_path.parent
    assert export["checkpoint"]["temporary_basename_matches"] is True
    assert export["checkpoint"]["source_basename"] == java_path.name
    assert export["checkpoint"]["temporary_directory_removed"] is True
    assert not source_path.parent.exists()

    java_path.write_text("old source", encoding="utf-8")
    replaced = model.save(str(java_path), "java", overwrite=True)
    assert java_path.read_text(encoding="utf-8") == "public class Config {}\n"
    assert replaced["checkpoint"]["publish_mode"] == "atomic_replace"
    assert model.source_paths[-1].name == java_path.name

    matlab_path = root / "Busbar.m"
    matlab_export = model.save(str(matlab_path), "m", overwrite=False)
    assert matlab_path.read_text(encoding="utf-8") == "function model = Busbar()\nend\n"
    assert model.source_paths[-1].name == matlab_path.name
    assert model.source_paths[-1].parent != matlab_path.parent
    assert matlab_export["checkpoint"]["temporary_basename_matches"] is True

    with pytest.raises(Exception):
        model.save(str(java_path), "java", overwrite=False)
    assert java_path.read_text(encoding="utf-8") == "public class Config {}\n"
    with pytest.raises(Exception):
        model.save(str(root.parent / "outside.java"), "java")

    # The legacy bool argument remains adapter-compatible: its value does not
    # become the Python overwrite policy or get passed as Java saveCopy=false.
    legacy_bool_path = root / "legacy-bool.mph"
    model.save(str(legacy_bool_path), False, overwrite=False)
    assert model.calls[-1][0] == "save"
    assert model.calls[-1][1][1] is True

    destination.write_bytes(b"preserve existing file")
    with pytest.raises(Exception):
        model.save(str(destination), True, overwrite=False)
    assert destination.read_bytes() == b"preserve existing file"

    model._call("save", str(java_path), "java")
    assert model.calls[-1] == ("save", (str(java_path), "java"))


def test_atomic_source_export_fsync_uses_windows_writable_handle_and_retains_failed_source(tmp_path, monkeypatch):
    destination = tmp_path / "Model.java"
    writable_handles = {}
    open_records = []
    real_fsync = _atomic_save.os.fsync
    real_path_open = Path.open

    def windows_fsync(fd):
        # Simulate the Windows FlushFileBuffers contract: syncing a source file
        # opened read-only is rejected; only the shared r+b helper is accepted.
        assert writable_handles.get(fd) == ("r+b", True)
        return real_fsync(fd)

    def tracked_open(path, mode="r", *args, **kwargs):
        handle = real_path_open(path, mode, *args, **kwargs)
        if path.name == destination.name:
            record = (mode, handle.writable())
            writable_handles[handle.fileno()] = record
            open_records.append(record)
        return handle

    monkeypatch.setattr(Path, "open", tracked_open)
    monkeypatch.setattr(_atomic_save.os, "fsync", windows_fsync)
    monkeypatch.setattr(_atomic_save, "_fsync_parent", lambda _path: True)

    published = java_worker._atomic_export_file(
        destination,
        lambda path: path.write_text(f"public class {path.stem} {{}}\n", encoding="utf-8"),
        tmp_path,
        overwrite=False,
    )
    assert published["artifact"]["verified"] is True
    assert ("r+b", True) in open_records

    failed_destination = tmp_path / "Broken.java"

    def partial_then_fail(path):
        path.write_text(f"public class {path.stem} {{", encoding="utf-8")
        raise RuntimeError("injected source-export failure")

    with pytest.raises(JavaWorkerError, match="temporary evidence retained at") as raised:
        java_worker._atomic_export_file(failed_destination, partial_then_fail, tmp_path, overwrite=False)
    retained_path = Path(str(raised.value).rsplit("temporary evidence retained at ", 1)[1])
    assert retained_path.name == failed_destination.name
    assert retained_path.is_file()
    assert "public class Broken" in retained_path.read_text(encoding="utf-8")
    assert not failed_destination.exists()


def test_remote_client_load_serializes_path_objects_as_strings(tmp_path):
    class Worker:
        generation = 1
        def __init__(self): self.payload = None
        def submit(self, kind, payload, **_kwargs):
            self.payload = (kind, payload)
            return {"ok": True, "status": "SUCCEEDED", "result": {"$worker_handle": "model", "generation": 1}}
    worker = Worker()
    RemoteClient(worker).load(tmp_path / "model.mph", tag="loaded")
    assert worker.payload == ("modelutil", {"method": "load", "args": ["loaded", str(tmp_path / "model.mph")]})


def test_two_workers_cannot_own_the_same_global_endpoint_lock(tmp_path):
    if not _worker_build_environment_available():
        pytest.skip("COMSOL 6.4/JDK 11 local Worker build environment unavailable")
    lock_root = tmp_path / "global-endpoint-locks"
    paths = JavaWorkerPaths(COMSOL_ROOT, JDK11, project_root=tmp_path, global_lock_root=lock_root)
    first = PersistentJavaWorker(paths, state_dir=tmp_path / "first")
    second = PersistentJavaWorker(paths, state_dir=tmp_path / "second")
    try:
        first.start(); second.start()
        held = first.submit("lock_selftest", {"port": 65001}, request_id="first-lock", rpc_timeout_s=1)
        rejected = second.submit("lock_selftest", {"port": 65001}, request_id="second-lock", rpc_timeout_s=1)
        assert held["status"] == "SUCCEEDED"
        assert rejected["status"] == "FAILED"
        assert rejected["failure"]["code"] == "ENGINE_BUSY"
    finally:
        second.close(); first.close()


# ---------------------------------------------------------------------------
# Allow-list consistency (C06): every engine method the G3 modules dispatch is
# in the worker's verified sets, and the model-util set stays minimal.
# ---------------------------------------------------------------------------

WORKER_SOURCE = Path(java_worker.__file__).resolve().parent / "worker_java" / "PersistentComsolWorker.java"
TABLE_API_JAVAP = (Path(__file__).resolve().parents[1]
                   / "evidence" / "phase4_3" / "runs"
                   / "independent_acceptance_20260922T004753Z" / "api_probe_table.javap.txt")

#: Names that would give a caller a global, cross-model side effect.  They must
#: never appear in MODEL_UTIL (the ModelUtil surface the worker exposes).
DANGEROUS_GLOBAL_METHODS = frozenset(
    {"clearAll", "shutdown", "quit", "exit", "stop", "disconnect", "restart", "killAll", "deleteAll"}
)

#: ``clearAll`` is the one reviewed non-global exception in METHODS: the product
#: only calls it on a *container* (a physics/mesh feature list) and re-reads the
#: container afterwards, and it is not a Model/ModelUtil method.
REVIEWED_CONTAINER_SCOPED = frozenset({"clearAll"})

#: Dispatched names that the installed API does not declare at all, so the probe
#: can only ever report "no such method".  They are kept out of the allow-list
#: test with their evidence instead of widening the worker surface (a whitelist
#: entry would have to be invented).
KNOWN_NON_API_PROBES = {
    # ``_g3_w14.py`` probes ``node.identifier()`` on a component node; javap of
    # apiplugins/com.comsol.api_1.0.0.jar shows neither ModelEntity nor
    # ComponentEntity declares identifier() (reported as a product-side gap).
    "identifier": "javap com.comsol.model.ModelEntity / ComponentEntity: no identifier() in 6.4.0.293",
}

#: Dispatched names that *are* real API methods but must stay out of the Java
#: allow-list until another module's refusal table is repaired.  ``objects``/
#: ``object`` exist on GeomObjectSelection (javap) and ``_g3_common`` probes
#: them, while ``_g3_w14.WORKER_UNAVAILABLE_METHODS`` still declares them
#: unavailable and its consistency test forbids the overlap -- so the selection
#: kinds needing them remain explicitly unusable instead of being half-enabled.
WITHHELD_PENDING_TABLE_REPAIR = {
    "objects": "GeomObjectSelection.objects(); blocked by _g3_w14.WORKER_UNAVAILABLE_METHODS",
    "object": "GeomObjectSelection.object(String[,int]); blocked by _g3_w14.WORKER_UNAVAILABLE_METHODS",
}


def _allowlist_block(name: str) -> set[str]:
    source = WORKER_SOURCE.read_text(encoding="utf-8")
    start = source.index(f"{name} = new HashSet")
    end = source.index("));", start)
    import re as _re

    return set(_re.findall(r'"([A-Za-z_][A-Za-z0-9_]*)"', source[start:end]))


def _dispatched_engine_methods() -> dict[str, set[str]]:
    """Every engine method name the G3 modules pass to the worker.

    The worker rejects an unlisted name with ``METHOD_REJECTED`` before the
    engine sees it, so a dispatched-but-unlisted name is a silent functional
    hole (exactly how ``mesh.statistics`` lost its element counts in the
    recorded W16_T018 run).  This mirrors the worker's own dispatch surface:
    ``_call``/``call_probe`` (single method) and ``_probe_snapshot`` (a tuple of
    method names).
    """
    import re as _re

    package = Path(java_worker.__file__).resolve().parent
    patterns = (
        _re.compile(r'_call\(\s*[A-Za-z_][\w\.\[\]]*\s*,\s*"([A-Za-z_]\w*)"'),
        _re.compile(r'call_probe\(\s*[A-Za-z_][\w\.\[\]]*\s*,\s*"([A-Za-z_]\w*)"'),
    )
    snapshot = _re.compile(r'_probe_snapshot\(\s*[\w\.\[\]]+\s*,\s*\(([^)]*)\)')
    found: dict[str, set[str]] = {}
    for path in sorted(package.glob("_g3_*.py")):
        for index, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            names: set[str] = set()
            for pattern in patterns:
                names |= set(pattern.findall(line))
            for group in snapshot.findall(line):
                names |= set(_re.findall(r'"([A-Za-z_]\w*)"', group))
            for name in names:
                found.setdefault(name, set()).add(f"{path.name}:{index}")
    return found


class TestWorkerAllowlist:
    def test_every_dispatched_engine_method_is_allowlisted(self):
        methods = _allowlist_block("METHODS")
        model_util = _allowlist_block("MODEL_UTIL")
        dispatched = _dispatched_engine_methods()
        missing = {name: sorted(where)[:3] for name, where in dispatched.items()
                   if name not in methods | model_util
                   and name not in KNOWN_NON_API_PROBES
                   and name not in WITHHELD_PENDING_TABLE_REPAIR}
        assert not missing, (
            "these engine methods are dispatched by the G3 modules but the worker allow-list would reject "
            f"them with METHOD_REJECTED: {missing}"
        )
        # Both exception lists stay closed and evidence-backed: a new unlisted
        # dispatch has to be resolved by the allow-list, never by adding it here.
        assert set(KNOWN_NON_API_PROBES) <= set(dispatched)
        assert set(WITHHELD_PENDING_TABLE_REPAIR) <= set(dispatched)

    def test_table_column_header_setter_is_javap_verified_and_allowlisted(self):
        """The typed table header mutation reaches the reviewed COMSOL API."""
        methods = _allowlist_block("METHODS")
        assert "setColumnHeaders" in methods
        assert TABLE_API_JAVAP.is_file()
        assert "public abstract void setColumnHeaders(java.lang.String[]);" in TABLE_API_JAVAP.read_text(encoding="utf-8")
        source = WORKER_SOURCE.read_text(encoding="utf-8")
        assert "TableBaseFeature.setColumnHeaders(String[])" in source

    def test_the_c06_merge_entries_are_present_with_their_api_evidence(self):
        methods = _allowlist_block("METHODS")
        assert {"stat", "isGeometry"} <= methods
        source = WORKER_SOURCE.read_text(encoding="utf-8")
        # The version boundary of the merge: the javap evidence and the jar hash
        # of the API the entries were verified against stay next to the entries.
        assert "MeshSequence.stat() -> com.comsol.model.MeshStatistics" in source
        assert "9bdc47a9e320be57" in source
        assert "MeshSequence.isGeometry() -> boolean" in source

    def test_w14_selection_init_is_javap_verified_and_allowlisted(self):
        """The W14 selection initializer reaches the reviewed COMSOL API."""
        methods = _allowlist_block("METHODS")
        assert "init" in methods
        source = WORKER_SOURCE.read_text(encoding="utf-8")
        assert "GeomObjectSelection.init(int)" in source
        assert "9bdc47a9e320be5721956336f44f5afa4cb06a20cfa887bc7d32d1a837483a67" in source
        assert '"init".equals(method)' in source
        assert "target instanceof GeomObjectSelection" in source
        assert "long dimension = ((Number) value).longValue()" in source
        assert "dimension != 2L && dimension != 3L" in source
        assert "requireSelectionDimension(args.get(0))" in source

    def test_the_withheld_selection_accessors_stay_coupled_to_the_w14_table(self):
        """``objects``/``object`` are now allow-listed and removed from W14 unavailable.

        They exist on GeomObjectSelection (javap) and were added to the Java
        allow-list so ``resolve_selection_entities`` can call them on live
        selections.  The W14 WORKER_UNAVAILABLE_METHODS table was updated in
        tandem (R-15 closure).
        """
        from comsol_mcp import _g3_w14 as w14

        methods = _allowlist_block("METHODS")
        assert {"objects", "object"} <= methods
        assert not ({"objects", "object"} & set(w14.WORKER_UNAVAILABLE_METHODS))

    def test_model_util_stays_minimal_and_has_no_dangerous_global(self):
        model_util = _allowlist_block("MODEL_UTIL")
        assert model_util == {
            "create", "load", "model", "remove", "tags", "uniquetag", "modelsUsedByOtherClients",
            "getComsolVersion", "hasProduct",
        }
        assert not (model_util & DANGEROUS_GLOBAL_METHODS)

    def test_the_node_allowlist_only_carries_the_reviewed_container_scoped_names(self):
        methods = _allowlist_block("METHODS")
        assert methods & DANGEROUS_GLOBAL_METHODS == set(REVIEWED_CONTAINER_SCOPED)


def test_production_json_integer_parser_and_overload_dispatch(tmp_path):
    """Compile and exercise the production Worker parser and dispatcher without COMSOL."""
    javac = shutil.which("javac")
    java = shutil.which("java")
    if javac is None or java is None:
        pytest.skip("NOT_RUN: Java compiler/runtime unavailable for production Worker parser regression")
    api_dir = COMSOL_ROOT / "apiplugins"
    jars = sorted(api_dir.glob("*.jar")) if api_dir.is_dir() else []
    if not jars:
        pytest.skip("NOT_RUN: COMSOL Java API classpath unavailable for production Worker parser regression")

    repo = Path(__file__).resolve().parents[1]
    harness = repo / "tests" / "java" / "PersistentComsolWorkerJsonNumberHarness.java"
    classes = tmp_path / "production-worker-json-classes"
    classes.mkdir()
    classpath = os.pathsep.join(str(path) for path in jars)
    compile_result = subprocess.run(
        [javac, "-encoding", "UTF-8", "-classpath", classpath, "-d", str(classes),
         str(WORKER_SOURCE), str(harness)],
        cwd=repo, capture_output=True, text=True, timeout=120,
    )
    assert compile_result.returncode == 0, compile_result.stdout + compile_result.stderr

    execution = subprocess.run(
        [java, "-cp", str(classes) + os.pathsep + classpath,
         "comsol_mcp.worker_java.PersistentComsolWorkerJsonNumberHarness"],
        cwd=repo, capture_output=True, text=True, timeout=30,
    )
    assert execution.returncode == 0, execution.stdout + execution.stderr
    assert execution.stdout.strip() == "PARSER_NUM_INTEGER_PRESERVATION_PASS"
