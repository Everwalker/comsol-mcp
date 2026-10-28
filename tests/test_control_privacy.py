"""Synthetic Windows privacy policy + real local fd wiring; no Windows SDK."""
import copy
import json
import os
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from comsol_mcp import _control_client as client
from comsol_mcp import _control_daemon as daemon
from comsol_mcp import _control_privacy as privacy
from comsol_mcp import _host_scoped_provider as provider
from comsol_mcp import _server
from comsol_mcp._execution_contract import ExecutionContractError


@pytest.fixture
def win(monkeypatch, tmp_path):
    calls = []
    denied = {}
    def identity(path):
        s = Path(path).stat()
        return {"device": s.st_dev, "inode": s.st_ino, "security_sha256": "a" * 64}
    def consume(path, *, directory, consume, expected_identity=None):
        calls.append(("directory_acl", Path(path)))
        if str(path) in denied:
            raise ExecutionContractError("HOST_PROVIDER_CONFIG_INVALID", denied[str(path)])
        value = identity(path)
        if expected_identity is not None and value != expected_identity:
            raise ExecutionContractError("AUTHORIZATION_STATE_UNKNOWN", "drift-private-native-path")
        return consume(object(), object(), {"identity": value})
    def fd_security(path, fd, info, *, directory, expected_identity=None, _control_access=None):
        assert _control_access is provider._CONTROL_PRIVACY_ACCESS
        assert info.st_ino == os.fstat(fd).st_ino == Path(path).stat().st_ino
        calls.append(("file_acl", Path(path), info.st_size))
        if str(path) in denied:
            raise ExecutionContractError("HOST_PROVIDER_CONFIG_INVALID", denied[str(path)])
        value = identity(path)
        if expected_identity is not None and value != expected_identity:
            raise ExecutionContractError("AUTHORIZATION_STATE_UNKNOWN", "file-id-drift")
        return {"identity": value}
    monkeypatch.setattr(privacy, "_is_windows", lambda: True)
    monkeypatch.setattr(client, "_is_windows", lambda: True)
    monkeypatch.setattr(provider, "_with_control_private_path", consume)
    monkeypatch.setattr(provider, "_assert_opened_path_security", fd_security)
    monkeypatch.setenv("COMSOL_SERVER_MCP_CONTROL_MODE", "attach-only")
    monkeypatch.delenv("COMSOL_SERVER_MCP_SCM_ENDPOINT_CONFIG", raising=False)
    root = tmp_path / "new-state"
    home = root / "control-private"
    home.mkdir(parents=True)
    monkeypatch.setattr(_server, "COMSOL_SERVER_MCP_HOME", root)
    endpoint = {"pid": 123, "port": 2345, "token": "fixture-secret-token"}
    (home / "control.json").write_text(json.dumps(endpoint))
    return SimpleNamespace(calls=calls, denied=denied, root=root, home=home, endpoint=endpoint)


@pytest.mark.parametrize("mode", [0o777, 0o555])
def test_windows_private_validator_accepts_even_when_posix_mode_cannot(win, mode):
    win.home.chmod(mode)
    with patch.object(Path, "mkdir", side_effect=AssertionError("mkdir")), patch.object(os, "chmod", side_effect=AssertionError("chmod")):
        assert client.control_home(create=False) == win.home
    assert any(x[0] == "directory_acl" for x in win.calls)


@pytest.mark.parametrize("reason", ["NULL DACL", "absent DACL", "untrusted ACE", "wrong owner", "no persistent ACLs", "wrong volume", "SDK permission failure"])
def test_attach_failure_zero_effect_and_sanitized(monkeypatch, win, reason):
    win.denied[str(win.home)] = reason + " fixture-secret-token native-path"
    forbidden = lambda *a, **k: pytest.fail("operation after privacy refusal")
    monkeypatch.setattr(client, "_request", forbidden)
    monkeypatch.setattr(client, "_spawn_control_daemon", forbidden)
    with patch.object(Path, "mkdir", side_effect=AssertionError("mkdir")), patch.object(os, "chmod", side_effect=AssertionError("chmod")):
        with pytest.raises(client.LocalAttachOnlyError) as failed:
            client.ensure_control()
    assert "fixture-secret" not in str(failed.value) and "native-path" not in str(failed.value)


def test_endpoint_file_has_independent_acl_not_just_private_directory(monkeypatch, win):
    win.denied[str(win.home / "control.json")] = "file NULL DACL"
    monkeypatch.setattr(client, "_request", lambda *a: pytest.fail("request"))
    monkeypatch.setattr(client, "_spawn_control_daemon", lambda *a: pytest.fail("spawn"))
    with pytest.raises(client.LocalAttachOnlyError):
        client.ensure_control()
    assert any(x[0] == "file_acl" for x in win.calls)


def test_endpoint_hardlink_rejected_before_secret_read(win, tmp_path):
    os.link(win.home / "control.json", tmp_path / "second-name")
    with pytest.raises(privacy.ControlPrivacyError):
        privacy.read_endpoint(win.home)
    assert not any(x[0] == "file_acl" for x in win.calls)


def test_endpoint_symlink_rejected(win, tmp_path):
    target = tmp_path / "target"
    target.write_text(json.dumps(win.endpoint))
    (win.home / "control.json").unlink()
    (win.home / "control.json").symlink_to(target)
    with pytest.raises(privacy.ControlPrivacyError):
        privacy.read_endpoint(win.home)


def test_endpoint_revalidated_before_network_and_mutation_refused(monkeypatch, win):
    endpoint = privacy.read_endpoint(win.home)
    submitted = []
    assert privacy.request_if_current(endpoint, lambda e: submitted.append(e) or "OK") == "OK"
    assert submitted == [win.endpoint]
    endpoint["token"] = "mutated"
    with pytest.raises(privacy.ControlPrivacyError):
        privacy.request_if_current(endpoint, lambda e: pytest.fail("network"))


def test_endpoint_identity_drift_stops_before_request(win):
    endpoint = privacy.read_endpoint(win.home)
    replacement = win.home / "replacement"
    replacement.write_text(json.dumps(win.endpoint))
    replacement.replace(win.home / "control.json")
    with pytest.raises(privacy.ControlPrivacyError):
        privacy.request_if_current(endpoint, lambda e: pytest.fail("network"))


def test_transport_error_stays_transport_error_after_admission(win):
    endpoint = privacy.read_endpoint(win.home)
    with pytest.raises(TimeoutError, match="entered-RPC"):
        privacy.request_if_current(endpoint, lambda e: (_ for _ in ()).throw(TimeoutError("entered-RPC")))


def test_auto_privacy_drift_never_pid_queries_or_replaces(monkeypatch, win):
    monkeypatch.delenv("COMSOL_SERVER_MCP_CONTROL_MODE")
    monkeypatch.setattr(client, "_request", lambda *a: (_ for _ in ()).throw(privacy.ControlPrivacyError()))
    monkeypatch.setattr(client, "_alive", lambda *a: pytest.fail("PID query"))
    monkeypatch.setattr(client, "_spawn_control_daemon", lambda *a: pytest.fail("spawn"))
    with pytest.raises(client.ControlStartupError, match="Private control preflight failed"):
        client.ensure_control()


def test_new_home_verifies_inherited_acl_without_chmod(monkeypatch, win):
    fresh = win.root / "new-private"
    with patch.object(os, "chmod", side_effect=AssertionError("ACL/mode mutation")):
        assert privacy.prepare_home(fresh, create=True) == fresh
    assert fresh.is_dir()
    assert [x[1] for x in win.calls if x[0] == "directory_acl"] == [win.root, fresh]


def test_new_home_bad_inherited_acl_is_refused_and_not_repaired(win):
    fresh = win.root / "bad-inherited"
    win.denied[str(fresh)] = "inherited untrusted access"
    with pytest.raises(privacy.ControlPrivacyError):
        privacy.prepare_home(fresh, create=True)
    assert fresh.is_dir()  # Preserve the actual failed creation; no cleanup.


def test_secret_not_written_until_same_original_fd_acl_passes(monkeypatch, win):
    win.denied[str(win.home / "control.json.tmp")] = "wide temporary DACL"
    writes = []
    monkeypatch.setattr(privacy.os, "write", lambda *a: writes.append(a) or pytest.fail("secret write"))
    with pytest.raises(privacy.ControlPrivacyError):
        privacy.publish_endpoint(win.home, win.endpoint)
    assert writes == [] and (win.home / "control.json.tmp").stat().st_size == 0


def test_endpoint_publication_checks_before_and_after_write_no_chmod(monkeypatch, win):
    original = os.write
    def write(fd, data):
        assert any(x[0] == "file_acl" and x[1].name == "control.json.tmp" and x[2] == 0 for x in win.calls)
        win.calls.append(("secret_write", len(data)))
        return original(fd, data)
    monkeypatch.setattr(privacy.os, "write", write)
    with patch.object(os, "chmod", side_effect=AssertionError("chmod")):
        privacy.publish_endpoint(win.home, {**win.endpoint, "pid": 456})
    assert json.loads((win.home / "control.json").read_bytes())["pid"] == 456
    assert sum(x[0] == "file_acl" and x[1].name == 'control.json.tmp' for x in win.calls) == 2
    assert sum(x[0] == "file_acl" and x[1].name == 'control.json' for x in win.calls) == 6
    assert not (win.home / "control.json.tmp").exists()


def test_publication_does_not_overwrite_old_failed_temporary(win):
    temporary = win.home / "control.json.tmp"
    temporary.write_bytes(b"old-failure")
    with pytest.raises(privacy.ControlPrivacyError):
        privacy.publish_endpoint(win.home, win.endpoint)
    assert temporary.read_bytes() == b"old-failure"


def test_read_sdk_unknown_does_not_close_original_python_fd(monkeypatch, win):
    calls = []
    monkeypatch.setattr(provider, "_assert_opened_path_security", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("SDK uncertain")))
    monkeypatch.setattr(privacy.os, "close", lambda fd: calls.append(fd) or pytest.fail("close after UNKNOWN"))
    with pytest.raises(privacy.ControlPrivacyError):
        privacy.read_endpoint(win.home)
    assert calls == []
    held = privacy._RETAINED[-1]
    assert held[0] is not None and held[1] is not None
    before = list(win.calls)
    with pytest.raises(privacy.ControlPrivacyError):
        privacy.read_endpoint(win.home)
    assert win.calls == before  # No fresh API/path owner bypass after uncertainty.
    with patch.object(Path, 'lstat', side_effect=AssertionError('fenced path observation')):
        with pytest.raises(privacy.ControlPrivacyError):
            client.control_home(create=False)


def test_explicit_daemon_home_denied_before_lock_daemon_listener(monkeypatch, win):
    win.denied[str(win.home)] = "unsupported ACL volume"
    monkeypatch.setattr(daemon, "ProcessLock", lambda *a: pytest.fail("lock"))
    monkeypatch.setattr(daemon, "ControlDaemon", lambda *a, **k: pytest.fail("daemon"))
    monkeypatch.setattr(daemon, "ThreadingHTTPServer", lambda *a: pytest.fail("listener"))
    with pytest.raises(privacy.ControlPrivacyError):
        daemon.serve(win.home)


def test_daemon_publishes_with_shared_policy_before_serve(monkeypatch, win):
    events = []
    class Server:
        server_port = 4321
        def serve_forever(self):
            events.append("serve")
            assert json.loads((win.home / "control.json").read_bytes())["token"] == "fixture-secret"
    monkeypatch.setattr(daemon, "ThreadingHTTPServer", lambda *a: Server())
    monkeypatch.setattr(daemon, "process_identity", lambda *a: {"start_epoch_ms": 123})
    monkeypatch.setattr(daemon, "_write_control_daemon_identity", lambda *a: None)
    daemon._serve_control_listener(win.home, SimpleNamespace(), "fixture-secret", None, None, None)
    assert events == ["serve"]
    assert any(x[0] == "file_acl" for x in win.calls)


def test_posix_control_home_behavior_stays_uid_mode_based(monkeypatch, tmp_path):
    root = tmp_path / "state"
    monkeypatch.setattr(client, "_is_windows", lambda: False)
    monkeypatch.setattr(_server, "COMSOL_SERVER_MCP_HOME", root)
    home = client.control_home(create=True)
    assert (home.stat().st_mode & 0o777) == 0o700
    assert client.control_home(create=False) == home
    home.chmod(0o750)
    with pytest.raises(RuntimeError, match="not private"):
        client.control_home(create=False)


def test_verified_log_inherits_original_fd_and_retains_it_on_constructor_error(monkeypatch, win):
    closes = []
    cause = client.ControlStartupError('original detached failure', action='original-action')
    def launch(file):
        assert any(x[0] == 'file_acl' and x[1].name == 'control.log' for x in win.calls)
        assert file.fileno() == file.fd and file.state == 'OPEN'
        raise cause
    with patch.object(os, 'close', side_effect=lambda fd: closes.append(fd)):
        with pytest.raises(client.ControlStartupError) as failure:
            privacy.with_log(win.home, launch)
    assert failure.value is cause and failure.value.action == 'original-action'
    assert closes == []
    held = privacy._RETAINED[-1]
    assert held[0] is cause and held[2][0].state == 'OPEN'


def test_scm_serve_retains_its_original_profile_path_and_publisher(monkeypatch, tmp_path):
    from comsol_mcp import _windows_scm_publisher as scm
    from comsol_mcp import _state_backup
    events = []
    runtime = object()
    class Publisher:
        _lock = object()
        def transport_secret(self):return 'scm-original-token'
        def bind_original(self, current, profile):events.append(('bind', current, profile))
        def finish_after_daemon(self):events.append(('finish',))
    publisher = Publisher()
    product = SimpleNamespace(close=lambda:events.append(('daemon-close',)))
    monkeypatch.setattr(scm, 'prepare_home', lambda home, current: publisher if current is runtime else pytest.fail('runtime changed'))
    monkeypatch.setattr(_state_backup, 'assert_no_pending_restore', lambda *a:None)
    monkeypatch.setattr(daemon, 'ControlDaemon', lambda *a, **k:product)
    def listener(home, current, token, rt, bootstrap, profile, supplied):
        assert current is product and rt is runtime and supplied is publisher
        assert token == 'scm-original-token' and profile == 'original-profile'
        events.append(('listener',));return 'SCM_RESULT'
    monkeypatch.setattr(daemon, '_serve_control_listener', listener)
    monkeypatch.setattr(privacy, 'prepare_home', lambda *a, **k:pytest.fail('SCM entered local privacy preparation'))
    monkeypatch.setattr(privacy, 'publish_endpoint', lambda *a, **k:pytest.fail('SCM entered local publisher'))
    assert daemon.serve(tmp_path/'scm', _scm_runtime=runtime, _scm_profile_source_sha256='original-profile') == 'SCM_RESULT'
    assert [x[0] for x in events] == ['bind','listener','daemon-close','finish']


def test_publication_rejects_unsafe_existing_endpoint_before_secret_write(monkeypatch, win):
    win.denied[str(win.home / 'control.json')] = 'existing endpoint NULL DACL'
    monkeypatch.setattr(privacy.os, 'write', lambda *a:pytest.fail('secret written before all admission'))
    with pytest.raises(privacy.ControlPrivacyError):
        privacy.publish_endpoint(win.home, win.endpoint)
    assert not (win.home / 'control.json.tmp').exists()


def test_privacy_failure_between_health_and_operation_is_not_submitted(monkeypatch, win):
    endpoint = privacy.read_endpoint(win.home)
    monkeypatch.setattr(client, 'ensure_control', lambda:endpoint)
    endpoint['token'] = 'drift'
    monkeypatch.setattr(client, '_submit_request', lambda *a:pytest.fail('HTTP submission'))
    result = client.dispatch('runtime.discover', {'roots':[]}, {'request_id':'r','idempotency_key':'k','rpc_timeout_s':1})
    assert result['error']['code'] == 'CONTROL_REQUEST_NOT_SUBMITTED'
    assert result['error']['safe_retry'] is False and result['data']['operation_dispatched'] is False
    assert 'token' not in str(result) and 'drift' not in str(result)
