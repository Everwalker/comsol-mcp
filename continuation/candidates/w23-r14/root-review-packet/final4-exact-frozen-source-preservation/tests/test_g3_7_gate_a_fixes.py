import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from comsol_mcp._operation_store import OperationStore
from comsol_mcp._platform_process import is_process_in_job
from comsol_mcp._java_worker import JavaWorkerPaths
from comsol_mcp._security_os import set_private_directory_permissions
from comsol_mcp._g2_isolation import _windows_process_snapshot
import comsol_mcp._control_daemon as control_daemon
import comsol_mcp._g2_isolation as g2_iso
import comsol_mcp._platform_process as plat_proc

# ==============================================================================
# F02: Terminal State & RPC Consistency
# ==============================================================================

def test_f02_operation_store_finish_normal(tmp_path):
    store = OperationStore(tmp_path / "f02_1.sqlite")
    record, reused = store.begin(request_id="r1", idempotency_key="k1", request_hash="h1", operation="op1")
    accepted, status = store.finish(record["operation_id"], status="SUCCEEDED", result={"ok": True})
    assert accepted is True
    assert status == "SUCCEEDED"

def test_f02_operation_store_finish_already_terminal(tmp_path):
    store = OperationStore(tmp_path / "f02_2.sqlite")
    record, _ = store.begin(request_id="r1", idempotency_key="k1", request_hash="h1", operation="op1")
    
    # Cancel it while QUEUED
    success, reason, job_record = store.cancel_queued(record["job_id"], "cancel_req")
    assert success is True
    
    # Now try to finish it with late success
    accepted, status = store.finish(record["operation_id"], status="SUCCEEDED", result={"ok": True})
    assert accepted is False
    assert status == "CANCELLED"

def test_f02_operation_store_transition_status_already_terminal(tmp_path):
    store = OperationStore(tmp_path / "f02_trans.sqlite")
    record, _ = store.begin(request_id="r1", idempotency_key="k1", request_hash="h1", operation="op1")
    store.cancel_queued(record["job_id"], "cancel_req")
    # Try transitioning CANCELLED job to SUCCEEDED even if CANCELLED is in expected
    ok, cur, job = store.transition_status(record["job_id"], ("CANCELLED", "RUNNING"), "SUCCEEDED")
    assert ok is False
    assert cur == "CANCELLED"
    assert job["status"] == "CANCELLED"

def test_f02_control_daemon_cancel_late_success(tmp_path):
    store = OperationStore(tmp_path / "f02_3.sqlite")
    record, _ = store.begin(request_id="r1", idempotency_key="k1", request_hash="h1", operation="op1")
    
    daemon = control_daemon.ControlDaemon(tmp_path)
    daemon.store = store
    
    # Cancel job
    store.cancel_queued(record["job_id"], "cancel_req")
    
    # Late success finish via daemon
    result = {"ok": True}
    returned_result = daemon._finish(record, result, "SUCCEEDED")
    
    # Authoritative status is returned
    assert returned_result["data"]["status"] == "CANCELLED"
    
    # Check events to ensure LateResultRecorded was emitted
    events = store.db.execute("SELECT event, metadata FROM job_events WHERE job_id=? ORDER BY id", (record["job_id"],)).fetchall()
    
    # Find the LateResultRecorded event
    late_events = [e for e in events if e["event"] == "LateResultRecorded"]
    assert len(late_events) >= 1
    meta = json.loads(late_events[-1]["metadata"])
    assert meta["rejected_status"] == "SUCCEEDED"
    assert meta["authoritative_status"] == "CANCELLED"

# ==============================================================================
# F03: Windows DACL
# ==============================================================================

def test_f03_dacl_non_windows(tmp_path, monkeypatch):
    if sys.platform == "win32":
        pytest.skip("POSIX permission test not applicable on native Windows")
    monkeypatch.setattr(sys, "platform", "linux")
    monkeypatch.setattr(os, "name", "posix")
    
    # Mock os.chmod to verify it's called
    chmod_called = False
    def mock_chmod(path, mode):
        nonlocal chmod_called
        chmod_called = True
        assert mode == 0o700
    
    monkeypatch.setattr(os, "chmod", mock_chmod)
    
    test_dir = tmp_path / "priv_dir"
    set_private_directory_permissions(test_dir)
    assert chmod_called

def test_f03_dacl_windows_real(tmp_path):
    if sys.platform != "win32":
        pytest.skip("Windows native test only")
    real_dir = tmp_path / "real_private_dir"
    set_private_directory_permissions(real_dir)
    assert real_dir.is_dir()

def test_f03_dacl_windows_mocked(tmp_path, monkeypatch):
    monkeypatch.setattr(sys, "platform", "win32")
    
    def mock_run(cmd, *args, **kwargs):
        raise OSError("Mock error")
    
    monkeypatch.setattr(subprocess, "run", mock_run)
    
    with pytest.raises(PermissionError, match="Cannot query current user SID"):
        set_private_directory_permissions(tmp_path / "win_mock")

def test_f08_dacl_windows_mocked_exact_user_success(tmp_path, monkeypatch):
    monkeypatch.setattr(sys, "platform", "win32")
    test_dir = tmp_path / "win_exact"
    test_dir.mkdir()
    
    class Completed:
        def __init__(self, returncode=0, stdout="", stderr=""):
            self.returncode = returncode
            self.stdout = stdout
            self.stderr = stderr

    def mock_run(cmd, *args, **kwargs):
        cmd_str = " ".join(cmd) if isinstance(cmd, list) else str(cmd)
        if "whoami" in cmd_str:
            return Completed(0, '"DOMAIN\\alice","S-1-5-21-123456789-123456789-123456789-1001"\n')
        elif "/grant" in cmd_str:
            return Completed(0, "processed")
        else: # readback
            out = (
                f"{test_dir} S-1-5-21-123456789-123456789-123456789-1001:(OI)(CI)(F)\n"
                f"             NT AUTHORITY\\SYSTEM:(I)(OI)(CI)(F)\n"
                f"Successfully processed 1 files; Failed processing 0 files\n"
            )
            return Completed(0, out)

    monkeypatch.setattr(subprocess, "run", mock_run)
    set_private_directory_permissions(test_dir)

def test_f08_dacl_windows_mocked_rejects_empty_dacl(tmp_path, monkeypatch):
    monkeypatch.setattr(sys, "platform", "win32")
    test_dir = tmp_path / "win_empty"
    test_dir.mkdir()

    class Completed:
        def __init__(self, returncode=0, stdout=""):
            self.returncode = returncode
            self.stdout = stdout
            self.stderr = ""

    def mock_run(cmd, *args, **kwargs):
        cmd_str = " ".join(cmd) if isinstance(cmd, list) else str(cmd)
        if "whoami" in cmd_str:
            return Completed(0, '"DOMAIN\\alice","S-1-5-21-123456789-123456789-123456789-1001"\n')
        elif "/grant" in cmd_str:
            return Completed(0, "processed")
        else:
            return Completed(0, "Successfully processed 1 files\n")

    monkeypatch.setattr(subprocess, "run", mock_run)
    with pytest.raises(PermissionError, match="No ACEs could be parsed"):
        set_private_directory_permissions(test_dir)

def test_f08_dacl_windows_mocked_rejects_substring_spoofing(tmp_path, monkeypatch):
    monkeypatch.setattr(sys, "platform", "win32")
    test_dir = tmp_path / "win_spoof"
    test_dir.mkdir()

    class Completed:
        def __init__(self, returncode=0, stdout=""):
            self.returncode = returncode
            self.stdout = stdout
            self.stderr = ""

    def mock_run(cmd, *args, **kwargs):
        cmd_str = " ".join(cmd) if isinstance(cmd, list) else str(cmd)
        if "whoami" in cmd_str:
            return Completed(0, '"DOMAIN\\alice","S-1-5-21-123456789-123456789-123456789-1001"\n')
        elif "/grant" in cmd_str:
            return Completed(0, "processed")
        else:
            # Contains alice_guests instead of alice!
            out = (
                f"{test_dir} DOMAIN\\alice_guests:(OI)(CI)(F)\n"
                f"             NT AUTHORITY\\SYSTEM:(I)(OI)(CI)(F)\n"
            )
            return Completed(0, out)

    monkeypatch.setattr(subprocess, "run", mock_run)
    with pytest.raises(PermissionError, match="Required user ACE granting Full Control"):
        set_private_directory_permissions(test_dir)

def test_f08_dacl_windows_mocked_rejects_unexpected_aces(tmp_path, monkeypatch):
    monkeypatch.setattr(sys, "platform", "win32")
    test_dir = tmp_path / "win_unexpected"
    test_dir.mkdir()

    class Completed:
        def __init__(self, returncode=0, stdout=""):
            self.returncode = returncode
            self.stdout = stdout
            self.stderr = ""

    def mock_run(cmd, *args, **kwargs):
        cmd_str = " ".join(cmd) if isinstance(cmd, list) else str(cmd)
        if "whoami" in cmd_str:
            return Completed(0, '"DOMAIN\\alice","S-1-5-21-123456789-123456789-123456789-1001"\n')
        elif "/grant" in cmd_str:
            return Completed(0, "processed")
        else:
            out = (
                f"{test_dir} S-1-5-21-123456789-123456789-123456789-1001:(OI)(CI)(F)\n"
                f"             NT AUTHORITY\\SYSTEM:(I)(OI)(CI)(F)\n"
                f"             Everyone:(OI)(CI)(R)\n"
            )
            return Completed(0, out)

    monkeypatch.setattr(subprocess, "run", mock_run)
    with pytest.raises(PermissionError, match="(?i)unexpected ACEs"):
        set_private_directory_permissions(test_dir)


# ==============================================================================
# F04: is_process_in_job
# ==============================================================================

def test_f04_is_process_in_job_non_windows(monkeypatch):
    if sys.platform == "win32":
        pytest.skip("POSIX process test not applicable on native Windows")
    monkeypatch.setattr(os, "name", "posix")
    assert is_process_in_job() is False
    assert is_process_in_job(pid=1234) is False

def test_f04_is_process_in_job_windows():
    if sys.platform != "win32":
        pytest.skip("Windows native test only")
    res = is_process_in_job()
    assert res in (True, False, None)
    
    import inspect
    sig = inspect.signature(is_process_in_job)
    assert "pid" in sig.parameters
    assert sig.parameters["pid"].default is None
    # Cannot strictly assert the return annotation object because it might be `bool | None` depending on python version
    assert "None" in str(sig.return_annotation)

# ==============================================================================
# F05: Isolation fail-closed
# ==============================================================================

def test_f05_windows_process_snapshot_cim_failure(monkeypatch):
    # Mock the adapter check
    monkeypatch.setattr(g2_iso, "_require_isolation_adapter", lambda: None)
    
    # Mock process_identity
    monkeypatch.setattr(plat_proc, "process_identity", lambda pid, platform_name: {"alive": True, "start_epoch_ms": 123})
    
    # Mock subprocess.run to simulate CIM failure
    class MockResult:
        returncode = 1
        stdout = ""
        stderr = "failed"
        
    def mock_run(*args, **kwargs):
        return MockResult()
    
    monkeypatch.setattr(subprocess, "run", mock_run)
    
    res = _windows_process_snapshot(9999)
    assert res is None

def test_f05_windows_process_snapshot_cim_exception(monkeypatch):
    monkeypatch.setattr(g2_iso, "_require_isolation_adapter", lambda: None)
    monkeypatch.setattr(plat_proc, "process_identity", lambda pid, platform_name: {"alive": True})
    
    def mock_run(*args, **kwargs):
        raise Exception("Command failed")
        
    monkeypatch.setattr(subprocess, "run", mock_run)
    
    res = _windows_process_snapshot(9999)
    assert res is None

# ==============================================================================
# F06: JAR content hash
# ==============================================================================

def test_f06_classpath_hash(tmp_path, monkeypatch):
    comsol_root = tmp_path / "comsol"
    bin_dir = comsol_root / "bin"
    bin_dir.mkdir(parents=True)
    manifest = bin_dir / "comsolclientpath.txt"
    manifest.write_text("plugins/test.jar\n")
    
    plugins_dir = comsol_root / "plugins"
    plugins_dir.mkdir(parents=True)
    jar_file = plugins_dir / "test.jar"
    jar_file.write_bytes(b"initial_content")
    
    worker = JavaWorkerPaths(
        comsol_root=comsol_root,
        jdk_home=tmp_path / "jdk",
        platform_name="posix"
    )
    
    # 13. Test that classpath() returns 4 values
    classpath_str, manifest_hash, jar_count, jar_content_hash = worker.classpath()
    assert classpath_str == str(jar_file)
    assert jar_count == 1
    
    # 16. Deterministic hashes
    _, manifest_hash2, _, jar_content_hash2 = worker.classpath()
    assert manifest_hash == manifest_hash2
    assert jar_content_hash == jar_content_hash2
    
    # 14. Change content but not manifest
    jar_file.write_bytes(b"modified_content")
    _, manifest_hash3, _, jar_content_hash3 = worker.classpath()
    
    assert manifest_hash == manifest_hash3 # Manifest hasn't changed
    assert jar_content_hash != jar_content_hash3 # Content HAS changed

    # 15. compilation_cache_fingerprint includes jar_content_sha256
    monkeypatch.setattr(JavaWorkerPaths, "jdk_version_info", lambda self: "mock_jdk")
    monkeypatch.setattr(JavaWorkerPaths, "comsol_version_info", lambda self: "mock_comsol")
    
    cache_key, fp_data = worker.compilation_cache_fingerprint(
        b"source_code", 
        classpath_hash=manifest_hash3, 
        jar_content_hash=jar_content_hash3
    )
    assert "jar_content_sha256" in fp_data
    assert fp_data["jar_content_sha256"] == jar_content_hash3

