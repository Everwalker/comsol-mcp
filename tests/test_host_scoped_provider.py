from __future__ import annotations

from datetime import datetime, timedelta, timezone
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile

import pytest

from comsol_mcp._artifact_store import (
    local_artifact_host_identity,
    local_engine_host_identity,
    register_project_artifact,
)
from comsol_mcp._execution_contract import ExecutionContractError
from comsol_mcp._host_scoped_provider import (
    CONFIG_SCHEMA,
    HostScopedProvider,
    destination_registration_sha256,
    load_host_scoped_provider,
    scope_sha256,
    verify_private_path_security,
    _DARWIN_MNT_IGNORE_OWNERSHIP,
    _darwin_fstatfs_symbol,
    _darwin_mount_flags,
    _darwin_require_no_extended_acl,
    _assert_posix_owner_and_mount,
    _validate_windows_acl_policy,
    _verify_windows_private_path,
)


def _private_dir(path: Path) -> Path:
    path.mkdir(parents=True, mode=0o700)
    os.chmod(path, 0o700)
    return path


def _expiry(*, delta: timedelta = timedelta(hours=1)) -> str:
    return (datetime.now(timezone.utc) + delta).strftime("%Y-%m-%dT%H:%M:%SZ")


def _grant(project_id: str, action: str, authorization_ref: str, scope: dict, *, expiry=None) -> dict:
    return {"project_id": project_id, "action": action,
            "authorization_ref_sha256": hashlib.sha256(authorization_ref.encode()).hexdigest(),
            "scope_sha256": scope_sha256(project_id, action, scope),
            "expires_at_utc": expiry or _expiry()}


def _write_config(directory: Path, config: dict) -> Path:
    _private_dir(directory)
    path = directory / "host-provider.json"
    path.write_text(json.dumps(config, sort_keys=True, separators=(",", ":")), encoding="utf-8")
    os.chmod(path, 0o600)
    return path


def test_provider_exact_scopes_expiry_pinned_config_and_local_noclobber_sink(tmp_path):
    project_id = "project-test"
    sink = _private_dir(tmp_path / "local-sink")
    destination_ref = "local-test-sink"
    registration = destination_registration_sha256(project_id, destination_ref, sink)
    artifacts = [
        {"artifact_id": "b" * 64, "sha256": "d" * 64, "size_bytes": 27},
        {"artifact_id": "a" * 64, "sha256": "c" * 64, "size_bytes": 19},
    ]
    publish_scope = {"project_id": project_id, "action": "artifact.publish",
                     "destination_ref": destination_ref,
                     "destination_registration_sha256": registration,
                     "artifacts": artifacts}
    model_ref = {"schema_version": 1, "session_id": "session-1", "server_instance_id": "server-1",
                 "model_tag": "mcp1", "generation": 1}
    close_scope = {"model_ref": model_ref, "expected_revision": 4, "destination_ref": None}
    config = {"schema": CONFIG_SCHEMA,
              "destinations": [{"project_id": project_id, "destination_ref": destination_ref,
                                "sink_root": str(sink)}],
              "grants": [
                  _grant(project_id, "artifact.publish", "publish-once", publish_scope),
                  _grant(project_id, "model.close", "close-once", close_scope),
                  _grant(project_id, "model.close", "expired-close", close_scope,
                         expiry=_expiry(delta=timedelta(seconds=-1))),
              ]}
    config_path = _write_config(tmp_path / "host-config", config)
    provider = load_host_scoped_provider(config_path)
    try:
        assert provider.verify_scope(project_id, "artifact.publish", publish_scope, "publish-once") is True
        reordered = {**publish_scope, "artifacts": list(reversed(artifacts))}
        assert provider.verify_scope(project_id, "artifact.publish", reordered, "publish-once") is True
        changed = {**publish_scope, "artifacts": [dict(artifacts[0], size_bytes=28), artifacts[1]]}
        assert provider.verify_scope(project_id, "artifact.publish", changed, "publish-once") is False
        assert provider.verify_scope(project_id, "artifact.publish", publish_scope, "wrong-ref") is False
        assert provider.verify_scope(project_id, "artifact.publish", publish_scope, "close-once") is False
        assert provider.verify_scope(project_id, "model.close", close_scope, "close-once") is True
        assert provider.verify_scope(project_id, "model.close",
                                     {**close_scope, "expected_revision": 5}, "close-once") is False
        assert provider.verify_scope(project_id, "model.close", close_scope, "expired-close") is False

        destination = provider.resolve_destination(project_id, destination_ref)
        payload = b"registered local sink bytes"
        artifact_id = "e" * 64
        digest = hashlib.sha256(payload).hexdigest()
        receipt = destination["publish_chunks"](
            destination, artifact_id + ".csv", iter((payload[:8], b"", payload[8:])),
            expected_sha256=digest, expected_size=len(payload), artifact_id=artifact_id,
        )
        assert receipt["status"] == "PUBLISHED"
        assert receipt["no_clobber"] is True and receipt["readback_verified"] is True
        assert (sink / (artifact_id + ".csv")).read_bytes() == payload
        with pytest.raises(ExecutionContractError) as duplicate:
            destination["publish_chunks"](
                destination, artifact_id + ".csv", iter((payload,)),
                expected_sha256=digest, expected_size=len(payload), artifact_id=artifact_id,
            )
        assert duplicate.value.code == "DESTINATION_EXISTS"

        # Same-inode byte modification is detected before consuming any grant.
        with config_path.open("ab") as handle:
            handle.write(b" ")
        with pytest.raises(ExecutionContractError) as tampered:
            provider.verify_scope(project_id, "model.close", close_scope, "close-once")
        assert tampered.value.code == "AUTHORIZATION_STATE_UNKNOWN"
    finally:
        provider.close()


def test_session_stop_provider_scope_is_closed_windows_original_lifetime_class(tmp_path):
    project_id = "managed-stop-project"
    scope = {
        "schema": "comsol-mcp-managed-stop-scope/1",
        "runtime_id": {"id": "comsol64", "class": "comsol_mcp._session_context.SessionRuntimeConfig"},
        "platform": "windows",
        "ownership_mode": "originalManagedLifetime",
        "host_source": {
            "t037_source_sha256": "a" * 64,
            "host_resource_profile_sha256": "b" * 64,
            "host_id": "host-17",
        },
    }
    config = {"schema": CONFIG_SCHEMA, "destinations": [],
              "grants": [_grant(project_id, "session.stop", "stop-ref", scope)]}
    provider = load_host_scoped_provider(_write_config(tmp_path / "managed-stop-config", config))
    try:
        assert provider.verify_scope(project_id, "session.stop", scope, "stop-ref") is True
        assert provider(project_id, "stop-ref") is False
        assert provider.verify_scope("another-project", "session.stop", scope, "stop-ref") is False
        assert provider.verify_scope(project_id, "session.stop", scope, "wrong-ref") is False

        invalid = [
            {**scope, "project_id": project_id},
            {**scope, "session_id": "caller-selected"},
            {**scope, "platform": "darwin"},
            {**scope, "ownership_mode": "storeReconstructed"},
            {**scope, "runtime_id": {"id": "comsol64", "class": "Runtime"}},
            {**scope, "host_source": {**scope["host_source"], "t037_source_sha256": "A" * 64}},
            {**scope, "host_source": {**scope["host_source"], "host_id": "host/other"}},
        ]
        for candidate in invalid:
            assert provider.verify_scope(project_id, "session.stop", candidate, "stop-ref") is False
            with pytest.raises(ExecutionContractError) as malformed:
                scope_sha256(project_id, "session.stop", candidate)
            assert malformed.value.code == "AUTHORIZATION_SCOPE_INVALID"
        assert provider.verify_scope(project_id, "project.policy_set", scope, "stop-ref") is False
        with pytest.raises(ExecutionContractError) as ungrantable:
            scope_sha256(project_id, "project.policy_set", scope)
        assert ungrantable.value.code == "AUTHORIZATION_SCOPE_INVALID"
    finally:
        provider.close()


def test_provider_refuses_insecure_config_and_sink_replacement(tmp_path):
    project_id = "project-test"
    sink = _private_dir(tmp_path / "registered-sink")
    destination_ref = "sink"
    scope = {"model_ref": {"schema_version": 1, "session_id": "s", "server_instance_id": "i",
                           "model_tag": "m", "generation": 1},
             "expected_revision": 0, "destination_ref": None}
    config = {"schema": CONFIG_SCHEMA,
              "destinations": [{"project_id": project_id, "destination_ref": destination_ref,
                                "sink_root": str(sink)}],
              "grants": [_grant(project_id, "model.close", "ref", scope)]}
    config_path = _write_config(tmp_path / "host-config", config)
    os.chmod(config_path, 0o640)
    with pytest.raises(ExecutionContractError) as insecure:
        load_host_scoped_provider(config_path)
    assert insecure.value.code == "HOST_PROVIDER_CONFIG_INVALID"
    os.chmod(config_path, 0o600)

    provider = load_host_scoped_provider(config_path)
    parked = tmp_path / "registered-sink-parked"
    sink.rename(parked)
    replacement = _private_dir(sink)
    try:
        with pytest.raises(ExecutionContractError) as changed:
            provider.resolve_destination(project_id, destination_ref)
        assert changed.value.code == "DESTINATION_STATE_UNKNOWN"
    finally:
        provider.close()
    assert replacement.is_dir() and parked.is_dir()


def test_provider_rejects_duplicate_json_keys_and_schema_extras(tmp_path):
    config_dir = _private_dir(tmp_path / "host-config")
    path = config_dir / "duplicate.json"
    path.write_text('{"schema":"x","schema":"y","destinations":[],"grants":[]}', encoding="utf-8")
    os.chmod(path, 0o600)
    with pytest.raises(ExecutionContractError) as duplicate:
        load_host_scoped_provider(path)
    assert duplicate.value.code == "HOST_PROVIDER_CONFIG_INVALID"

    extra = {"schema": CONFIG_SCHEMA, "destinations": [], "grants": [], "allow_all": True}
    path.write_text(json.dumps(extra), encoding="utf-8")
    with pytest.raises(ExecutionContractError) as schema:
        load_host_scoped_provider(path)
    assert schema.value.code == "HOST_PROVIDER_CONFIG_INVALID"


def test_provider_rejects_noowners_volume_and_detects_parent_permission_changes(tmp_path):
    if sys.platform != "darwin":
        pytest.skip("Darwin fstatfs ownership mount flags are platform-specific")

    repository = Path(__file__).resolve().parents[1]
    with tempfile.TemporaryDirectory(prefix="host-provider-noowners-", dir=repository / "local-preservation") as raw_root:
        root = Path(raw_root)
        os.chmod(root, 0o700)
        fd = os.open(root, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
        try:
            flags, filesystem = _darwin_mount_flags(fd)
        finally:
            os.close(fd)
        assert filesystem
        if flags & _DARWIN_MNT_IGNORE_OWNERSHIP:
            with pytest.raises(ExecutionContractError) as noowners:
                verify_private_path_security(root, directory=True)
            assert noowners.value.code == "HOST_PROVIDER_CONFIG_INVALID"
            assert "ignores ownership" in str(noowners.value)

    project_id = "parent-mode-change"
    scope = {"model_ref": {"schema_version": 1, "session_id": "s", "server_instance_id": "i",
                           "model_tag": "m", "generation": 1},
             "expected_revision": 0, "destination_ref": None}
    config = {"schema": CONFIG_SCHEMA, "destinations": [],
              "grants": [_grant(project_id, "model.close", "close-ref", scope)]}
    parent = tmp_path / "host-config"
    config_path = _write_config(parent, config)
    provider = load_host_scoped_provider(config_path)
    try:
        os.chmod(parent, 0o750)
        with pytest.raises(ExecutionContractError) as changed:
            provider.verify_scope(project_id, "model.close", scope, "close-ref")
        assert changed.value.code == "AUTHORIZATION_STATE_UNKNOWN"
    finally:
        os.chmod(parent, 0o700)
        provider.close()


def test_private_path_security_returns_pinned_identity_on_supported_posix(tmp_path):
    private = _private_dir(tmp_path / "private")
    evidence = verify_private_path_security(private, directory=True)
    assert evidence["identity"]["device"] >= 0
    assert evidence["identity"]["inode"] > 0
    if sys.platform == "darwin":
        assert evidence["security_semantics"] == "posix-owner-mode+filesystem-ownership+no-extended-acl"
    else:
        assert evidence["security_semantics"] == "posix-owner-mode"


def test_darwin_pinned_extended_acl_gate_accepts_none_and_rejects_fixture_acl():
    if sys.platform != "darwin":
        pytest.skip("Darwin extended ACL behavior is platform-specific")
    with tempfile.TemporaryDirectory(prefix="host-provider-acl-", dir="/private/tmp") as raw:
        path = Path(raw)
        os.chmod(path, 0o700)
        fd = os.open(path, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
        try:
            _darwin_require_no_extended_acl(fd, "fixture directory")
            evidence = verify_private_path_security(path, directory=True)
            assert evidence["security_semantics"].endswith("+no-extended-acl")
        finally:
            os.close(fd)

        # This ACL is applied only to a fresh, private task fixture in /private/tmp;
        # TemporaryDirectory removes the fixture and its ACL on exit.
        subprocess.run(["/bin/chmod", "+a", "everyone allow read", str(path)],
                       check=True, capture_output=True, text=True)
        with pytest.raises(ExecutionContractError) as denied:
            verify_private_path_security(path, directory=True)
        assert denied.value.code == "HOST_PROVIDER_CONFIG_INVALID"
        assert "extended ACL" in str(denied.value)


def test_darwin_extended_acl_query_failure_fails_closed(monkeypatch):
    if sys.platform != "darwin":
        pytest.skip("Darwin ACL API behavior is platform-specific")
    import comsol_mcp._host_scoped_provider as provider_module

    class Function:
        def __init__(self, result):
            self.result = result
            self.argtypes = None
            self.restype = None

        def __call__(self, *_args):
            return self.result

    class Lib:
        def __init__(self):
            self.acl_get_fd_np = Function(None)
            self.acl_get_entry = Function(-1)
            self.acl_free = Function(0)

    lib = Lib()
    monkeypatch.setattr(provider_module.ctypes, "CDLL", lambda *_args, **_kwargs: lib)
    with pytest.raises(ExecutionContractError) as unknown:
        _darwin_require_no_extended_acl(123, "mock pinned object")
    assert unknown.value.code == "HOST_PROVIDER_UNSUPPORTED"
    assert "could not be verified" in str(unknown.value)


def test_darwin_unknown_filesystem_policy_fails_closed(tmp_path, monkeypatch):
    if sys.platform != "darwin":
        pytest.skip("Darwin mount semantics are platform-specific")
    import comsol_mcp._host_scoped_provider as provider_module

    private = _private_dir(tmp_path / "private-unknown-fs")
    fd = os.open(private, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
    try:
        monkeypatch.setattr(provider_module, "_darwin_mount_flags", lambda _fd: (0, "mysteryfs"))
        with pytest.raises(ExecutionContractError) as unknown:
            _assert_posix_owner_and_mount(fd, os.fstat(fd), "test directory")
        assert unknown.value.code == "HOST_PROVIDER_UNSUPPORTED"
    finally:
        os.close(fd)


def test_darwin_fstatfs_uses_architecture_specific_64bit_inode_symbol(monkeypatch):
    import ctypes
    import comsol_mcp._host_scoped_provider as provider_module

    class Function:
        def __init__(self, callback):
            self.callback = callback
            self.argtypes = None
            self.restype = None

        def __call__(self, *args):
            result = self.callback(*args)
            return int(result) if type(result) is bool else result

    def library_for(expected_symbol):
        calls = []

        def fill(_fd, result_ptr):
            calls.append(expected_symbol)
            result = ctypes.cast(result_ptr, ctypes.POINTER(provider_module._DarwinStatfs)).contents
            result.f_flags = 0x1234
            result.f_fstypename = b"apfs"
            return 0

        other = "fstatfs" if expected_symbol == "fstatfs$INODE64" else "fstatfs$INODE64"
        return type("FakeLib", (), {
            expected_symbol: Function(fill),
            other: Function(lambda *_args: pytest.fail(f"wrong ABI symbol selected: {other}")),
            "calls": calls,
        })()

    assert _darwin_fstatfs_symbol("arm64") == "fstatfs"
    assert _darwin_fstatfs_symbol("aarch64") == "fstatfs"
    assert _darwin_fstatfs_symbol("x86_64") == "fstatfs$INODE64"
    with pytest.raises(ExecutionContractError) as unknown:
        _darwin_fstatfs_symbol("i386")
    assert unknown.value.code == "HOST_PROVIDER_UNSUPPORTED"

    for machine, symbol in (("arm64", "fstatfs"), ("x86_64", "fstatfs$INODE64")):
        fake_lib = library_for(symbol)
        monkeypatch.setattr(provider_module.ctypes, "CDLL", lambda *_args, _lib=fake_lib, **_kwargs: _lib)
        flags, filesystem = _darwin_mount_flags(19, _machine=machine)
        assert flags == 0x1234 and filesystem == "apfs"
        assert fake_lib.calls == [symbol]


def test_darwin_x86_64_missing_inode64_symbol_fails_closed(monkeypatch):
    import comsol_mcp._host_scoped_provider as provider_module

    class Function:
        def __init__(self):
            self.argtypes = None
            self.restype = None

        def __call__(self, *_args):
            pytest.fail("legacy fstatfs must not be used as an x86_64 fallback")

    class FakeLib:
        fstatfs = Function()

    monkeypatch.setattr(provider_module.ctypes, "CDLL", lambda *_args, **_kwargs: FakeLib())
    with pytest.raises(ExecutionContractError) as missing:
        _darwin_mount_flags(19, _machine="x86_64")
    assert missing.value.code == "HOST_PROVIDER_UNSUPPORTED"
    assert "fstatfs$INODE64" in str(missing.value)


@pytest.mark.parametrize("policy", [
    {"owner_trusted": True, "owner_is_daemon": True, "require_daemon_owner": True,
     "dacl_present": False, "ace_rows": []},
    {"owner_trusted": False, "owner_is_daemon": True, "require_daemon_owner": True,
     "dacl_present": True, "ace_rows": []},
    {"owner_trusted": True, "owner_is_daemon": False, "require_daemon_owner": True,
     "dacl_present": True, "ace_rows": []},
    {"owner_trusted": True, "owner_is_daemon": True, "require_daemon_owner": True,
     "dacl_present": True, "ace_rows": [{"type": 0, "flags": 0, "sid_trusted": False}]},
    {"owner_trusted": True, "owner_is_daemon": True, "require_daemon_owner": True,
     "dacl_present": True, "ace_rows": [{"type": 5, "flags": 0, "sid_trusted": True}]},
])
def test_windows_acl_policy_negative_controls_are_fail_closed(policy):
    # Software-only policy reducer coverage; this does not exercise a Windows
    # kernel, token, ACL, or filesystem.
    with pytest.raises(ExecutionContractError):
        _validate_windows_acl_policy(**policy)


def test_windows_acl_policy_accepts_only_daemon_or_explicit_host_admin_allow_aces():
    # SOFTWARE reducer fixture only. Real Windows ACL and token verification
    # remains a separate platform test requirement.
    _validate_windows_acl_policy(
        owner_trusted=True, owner_is_daemon=True, require_daemon_owner=True,
        dacl_present=True,
        ace_rows=[{"type": 0, "flags": 0x10, "sid_trusted": True},
                  {"type": 1, "flags": 0, "sid_trusted": False}],
    )


def _owned_metadata_fixture_path(api, handles):
    from comsol_mcp._windows_host_preflight_owner import hold_original
    owner = hold_original(api)
    for handle in handles:
        api.k.labels[handle.value] = handle.label
        owner.capture(handle, family='handle', closer=owner.function('CloseHandle', api.k))
    return handles


def _owned_metadata_fixture_context(api):
    import ctypes as C
    from comsol_mcp._windows_host_preflight_owner import hold_original
    owner = hold_original(api); token = C.c_void_p(100); sids = [C.c_void_p(201), C.c_void_p(202)]
    owner.capture(token, family='handle', closer=owner.function('CloseHandle', api.k))
    for sid in sids: owner.capture(sid, family='local', closer=owner.function('LocalFree', api.k))
    return 'daemon-sid', ['daemon-sid', 'system-sid', 'admins-sid'], (token, sids, object())


def test_windows_verifier_mock_abi_checks_identity_hash_and_closes_handles(monkeypatch):
    # Software-only mock ABI seam; this is not evidence of Windows kernel ACL behavior.
    import comsol_mcp._host_scoped_provider as provider_module
    import comsol_mcp._windows_parameter_publish as windows_publish

    class Handle:
        def __init__(self, identity, label):
            self.identity, self.label = identity, label
            self.value = id(self)

    class Kernel:
        def __init__(self):
            self.closed_tokens = []
            self.freed = []

        def CloseHandle(self, handle):
            value = getattr(handle, "value", handle)
            label = self.labels.get(value)
            if label is None: self.closed_tokens.append(value)
            else: self.close_calls.append(label)
            return 1

        def LocalFree(self, handle):
            self.freed.append(getattr(handle, "value", handle))
            return None

    class FakeApi:
        def __init__(self, *, second_final_inode=22):
            self.k = Kernel()
            self.close_calls = []
            self.opens = 0
            self.k.labels = {}; self.k.close_calls = self.close_calls
            self.second_final_inode = second_final_inode

        def open_security_path(self, _path, *, directory):
            self.opens += 1
            final_inode = 22 if self.opens == 1 else self.second_final_inode
            return _owned_metadata_fixture_path(self, [Handle((7, 11), f"root-{self.opens}"),
                    Handle((7, final_inode), f"leaf-{self.opens}")])

        def info(self, handle, *, directory):
            return handle.identity, 0

        def close(self, handle):
            self.close_calls.append(handle.label)

    api = FakeApi()
    monkeypatch.setattr(windows_publish, "_API", lambda: api)
    monkeypatch.setattr(provider_module, "_windows_volume_has_persistent_acls", lambda _api, _path: (True, 7))
    monkeypatch.setattr(provider_module, "_windows_security_context",
                        _owned_metadata_fixture_context)
    monkeypatch.setattr(provider_module, "_win32_descriptor_digest",
                        lambda _api, handle, **_kwargs: (hashlib.sha256(repr(handle.identity).encode()).hexdigest(), "daemon"))

    evidence = _verify_windows_private_path(Path("C:/host/private/config"), directory=False,
                                            expected_identity=None)
    assert evidence["security_semantics"] == "windows-read-control-owner-dacl-persistent-acls"
    assert evidence["identity"]["device"] == 7
    assert evidence["identity"]["inode"] == 22
    assert len(evidence["security_sha256"]) == 64
    assert len(api.close_calls) == 4
    assert api.k.closed_tokens == [100]
    assert api.k.freed == [202, 201]


def test_windows_verifier_mock_abi_rejects_replacement_and_releases_pins(monkeypatch):
    # Software-only mock ABI negative control; no Windows path or DACL is touched.
    import comsol_mcp._host_scoped_provider as provider_module
    import comsol_mcp._windows_parameter_publish as windows_publish

    class Handle:
        def __init__(self, identity, label):
            self.identity, self.label = identity, label
            self.value = id(self)

    class Kernel:
        def __init__(self):
            self.closed_tokens = []
            self.freed = []
        def CloseHandle(self, handle):
            value = getattr(handle, "value", handle)
            label = self.labels.get(value)
            if label is None: self.closed_tokens.append(value)
            else: self.close_calls.append(label)
            return 1
        def LocalFree(self, handle): self.freed.append(getattr(handle, "value", handle)); return None

    class FakeApi:
        def __init__(self):
            self.k, self.close_calls, self.opens = Kernel(), [], 0
            self.k.labels = {}; self.k.close_calls = self.close_calls
        def open_security_path(self, _path, *, directory):
            self.opens += 1
            leaf = 22 if self.opens == 1 else 99
            return _owned_metadata_fixture_path(self, [Handle((7, 11), f"root-{self.opens}"), Handle((7, leaf), f"leaf-{self.opens}")])
        def info(self, handle, *, directory): return handle.identity, 0
        def close(self, handle): self.close_calls.append(handle.label)

    api = FakeApi()
    monkeypatch.setattr(windows_publish, "_API", lambda: api)
    monkeypatch.setattr(provider_module, "_windows_volume_has_persistent_acls", lambda _api, _path: (True, 7))
    monkeypatch.setattr(provider_module, "_windows_security_context",
                        _owned_metadata_fixture_context)
    monkeypatch.setattr(provider_module, "_win32_descriptor_digest",
                        lambda _api, handle, **_kwargs: ("a" * 64, "daemon"))

    with pytest.raises(ExecutionContractError) as replacement:
        _verify_windows_private_path(Path("C:/host/private/config"), directory=False,
                                    expected_identity=None)
    assert replacement.value.code == "AUTHORIZATION_STATE_UNKNOWN"
    assert len(api.close_calls) == 4
    assert api.k.closed_tokens == [100]
    assert api.k.freed == [202, 201]


@pytest.mark.parametrize(
    ("case", "ace_type", "ace_sid_is_trusted", "null_dacl", "should_refuse"),
    [
        ("valid", 0, True, False, False),
        ("null-dacl", 0, True, True, True),
        ("foreign-allow", 0, False, False, True),
        ("unknown-ace", 5, True, False, True),
    ],
)
def test_windows_descriptor_digest_calls_real_ctypes_path_with_correct_outputs_and_cleanup(
    monkeypatch, case, ace_type, ace_sid_is_trusted, null_dacl, should_refuse
):
    """Exercise _win32_descriptor_digest itself through a function-level ABI shim.

    This is SOFTWARE evidence only: the shim validates pointer positions,
    decoding and cleanup without claiming Windows kernel behavior.
    """
    import ctypes as C
    from ctypes import wintypes
    import struct
    import comsol_mcp._host_scoped_provider as provider_module

    # ctypes.wintypes follows the host C ABI when these tests run off Windows.
    # Supply the fixed-width Win32 scalar sizes the real DLL ABI uses.
    monkeypatch.setattr(wintypes, "DWORD", C.c_uint32, raising=False)
    monkeypatch.setattr(wintypes, "BOOL", C.c_int32, raising=False)

    def sid(rid):
        return bytes((1, 1, 0, 0, 0, 0, 0, 5)) + int(rid).to_bytes(4, "little")

    owner_bytes = sid(1001)
    allowed_bytes = owner_bytes if ace_sid_is_trusted else sid(2002)
    owner_buffer = C.create_string_buffer(owner_bytes)
    ace_sid_buffer = C.create_string_buffer(allowed_bytes)
    ace_size = 8 + len(allowed_bytes)
    ace_bytes = bytes((ace_type, 0)) + ace_size.to_bytes(2, "little") + bytes(4) + allowed_bytes
    acl_bytes = bytes(8) + ace_bytes
    acl_buffer = C.create_string_buffer(acl_bytes)
    descriptor_buffer = C.create_string_buffer(b"SDDLTEST")
    class Function:
        def __init__(self, callback):
            self.callback = callback
            self.argtypes = None
            self.restype = None

        def __call__(self, *args):
            result = self.callback(*args)
            return int(result) if type(result) is bool else result

    def address(pointer):
        if isinstance(pointer, int):
            return pointer
        if isinstance(pointer, C.c_void_p):
            return int(pointer.value or 0)
        return int(C.cast(pointer, C.c_void_p).value or 0)

    def sid_length(pointer):
        data = C.string_at(address(pointer), 8)
        return 8 + data[1] * 4

    class Advapi:
        def __init__(self):
            self.get_security_info_args = None
            self.get_security_info = Function(self._get_security_info)
            self.GetSecurityInfo = self.get_security_info
            self.GetSecurityDescriptorDacl = Function(self._get_dacl)
            self.GetSecurityDescriptorLength = Function(lambda _desc: len(b"SDDLTEST"))
            self.GetAclInformation = Function(self._get_acl_info)
            self.GetAce = Function(self._get_ace)
            self.IsValidSid = Function(lambda value: address(value) in {
                C.addressof(owner_buffer), C.addressof(ace_sid_buffer),
                C.addressof(acl_buffer) + 16,
            })
            self.GetLengthSid = Function(sid_length)
            self.EqualSid = Function(lambda left, right: C.string_at(address(left), sid_length(left))
                                     == C.string_at(address(right), sid_length(right)))

        def _get_security_info(self, *args):
            self.get_security_info_args = args
            # Win32 signature: owner, group, DACL, SACL, security descriptor.
            C.cast(args[3], C.POINTER(C.c_void_p)).contents.value = C.addressof(owner_buffer)
            C.cast(args[5], C.POINTER(C.c_void_p)).contents.value = C.addressof(acl_buffer)
            C.cast(args[7], C.POINTER(C.c_void_p)).contents.value = C.addressof(descriptor_buffer)
            return 0

        def _get_dacl(self, _descriptor, present_ptr, dacl_ptr, defaulted_ptr):
            C.cast(present_ptr, C.POINTER(wintypes.BOOL)).contents.value = 1
            C.cast(dacl_ptr, C.POINTER(C.c_void_p)).contents.value = (
                0 if null_dacl else C.addressof(acl_buffer)
            )
            C.cast(defaulted_ptr, C.POINTER(wintypes.BOOL)).contents.value = 0
            return 1

        def _get_acl_info(self, _acl, size_info_ptr, _size, _kind):
            class AclSizeInfo(C.Structure):
                _fields_ = [("AceCount", wintypes.DWORD), ("AclBytesInUse", wintypes.DWORD),
                            ("AclBytesFree", wintypes.DWORD)]
            out = C.cast(size_info_ptr, C.POINTER(AclSizeInfo)).contents
            out.AceCount = 1
            out.AclBytesInUse = len(acl_bytes)
            out.AclBytesFree = 0
            return 1

        def _get_ace(self, _acl, index, ace_ptr):
            assert index == 0
            C.cast(ace_ptr, C.POINTER(C.c_void_p)).contents.value = C.addressof(acl_buffer) + 8
            return 1

    class Kernel:
        def __init__(self):
            self.freed = []
            self.LocalFree = Function(self._local_free)

        def _local_free(self, pointer):
            self.freed.append(address(pointer))
            return None

    advapi = Advapi()
    kernel = Kernel()
    class Api:
        k = kernel

    monkeypatch.setattr(provider_module.ctypes, "WinDLL",
                        lambda name, **_kwargs: advapi if name == "advapi32" else kernel,
                        raising=False)
    daemon_sid = C.c_void_p(C.addressof(owner_buffer))
    trusted_sids = [daemon_sid]
    if should_refuse:
        with pytest.raises(ExecutionContractError) as refusal:
            provider_module._win32_descriptor_digest(
                Api(), C.c_void_p(0x1234), directory=False, require_daemon_owner=True,
                daemon_sid=daemon_sid, trusted_sids=trusted_sids,
            )
        assert refusal.value.code == "HOST_PROVIDER_CONFIG_INVALID"
    else:
        result = provider_module._win32_descriptor_digest(
            Api(), C.c_void_p(0x1234), directory=False, require_daemon_owner=True,
            daemon_sid=daemon_sid, trusted_sids=trusted_sids,
        )

    args = advapi.get_security_info_args
    assert args is not None and len(args) == 8
    assert args[0].value == 0x1234
    assert (args[1], args[2]) == (1, 5)  # SE_FILE_OBJECT and OWNER|DACL info
    assert args[3] is not None and args[4] is None  # owner output, no group output
    assert args[5] is not None and args[6] is None  # DACL output, no SACL output
    assert args[7] is not None  # descriptor output
    assert kernel.freed == [C.addressof(descriptor_buffer)]
    if not should_refuse:
        digest, owner_name = result
        assert len(digest) == 64 and owner_name == "daemon"


def test_windows_pinned_child_requests_read_control_access(monkeypatch):
    # Test the real _API access-mask construction through a narrow Native API shim.
    import ctypes as C
    import comsol_mcp._windows_parameter_publish as windows_publish

    api = windows_publish._API.__new__(windows_publish._API)
    observed = {}

    class Native:
        def NtCreateFile(self, out_handle, access, _attributes, io_status, *_rest):
            observed["access"] = int(access)
            C.cast(out_handle, C.POINTER(C.c_void_p)).contents.value = 0xA11
            C.cast(io_status, C.POINTER(windows_publish._IoStatusBlock)).contents.Information = 1
            return 0

    class Kernel:
        def CloseHandle(self, handle):
            observed["closed"] = handle.value
            return 1
    monkeypatch.setattr(C, "get_last_error", lambda: 5, raising=False)
    monkeypatch.setattr(C, "WinError", lambda value: OSError(value, "software boundary error"), raising=False)
    api.nt = Native()
    api.k = Kernel()
    handle = api._open_relative(C.c_void_p(0xB22), "config.json", read_control=True)
    try:
        assert handle.value == 0xA11
        assert observed["access"] == (0x00100080 | 0x00020000)
    finally:
        api.close(handle)
    assert observed["closed"] == 0xA11


def test_provider_constant_time_compares_both_digests_for_each_bounded_grant(tmp_path, monkeypatch):
    import comsol_mcp._host_scoped_provider as provider_module

    project_id = "constant-time-provider"
    scope = {"model_ref": {"schema_version": 1, "session_id": "s", "server_instance_id": "i",
                           "model_tag": "m", "generation": 1},
             "expected_revision": 2, "destination_ref": None}
    config = {"schema": CONFIG_SCHEMA, "destinations": [],
              "grants": [_grant(project_id, "model.close", "a", scope),
                         _grant(project_id, "model.close", "b", scope)]}
    provider = load_host_scoped_provider(_write_config(tmp_path / "host-config", config))
    calls = []
    real_compare = provider_module.hmac.compare_digest

    def tracked(left, right):
        calls.append((left, right))
        return real_compare(left, right)

    monkeypatch.setattr(provider_module.hmac, "compare_digest", tracked)
    try:
        assert provider.verify_scope(project_id, "model.close", scope, "not-a-grant") is False
        assert len(calls) == 4
        assert all(len(left) == len(right) == 64 for left, right in calls)
    finally:
        provider.close()


def test_control_daemon_loads_host_config_and_publishes_to_registered_local_sink(tmp_path, monkeypatch):
    from comsol_mcp._control_daemon import ControlDaemon

    endpoint = "127.0.0.1:24971"
    raw_authorization_ref = "only-in-test-memory"
    monkeypatch.setenv("COMSOL_MCP_HOST_CONTROL", "1")
    projects = _private_dir(tmp_path / "projects")
    control_home = tmp_path / "control"
    daemon = ControlDaemon(control_home, project_root=projects)
    try:
        created = daemon.dispatch({
            "operation": "project.create",
            "arguments": {"label": "provider-project", "workspace": "provider-project",
                          "policy": {"permissions": ["inspect", "project_write", "host_control"]}},
            "execution": {"request_id": "provider-project-create",
                          "idempotency_key": "provider-project-create"},
        })
        assert created["success"] is True, created
        project_id = created["data"]["project"]["project_id"]
        project_root = projects / "provider-project"
        source = project_root / "reports" / "source.csv"
        source.parent.mkdir(parents=True)
        source.write_bytes(b"x,y\n0,1\n")
        daemon.backend.endpoint_key = endpoint
        registered = register_project_artifact(
            project_root, daemon.store, "reports/source.csv", project_id=project_id,
            role="result_data", classification="project_local",
            host_identity=local_artifact_host_identity(),
            engine_host_identity=local_engine_host_identity(endpoint),
            request_id="provider-artifact-register", registering_operation_id="artifact.register",
            artifact_type="text/csv", format_version="test-csv/1",
        )
    finally:
        daemon.close()

    sink = _private_dir(tmp_path / "registered-sink")
    destination_ref = "local-provider-sink"
    registration = destination_registration_sha256(project_id, destination_ref, sink)
    scope = {"project_id": project_id, "action": "artifact.publish",
             "destination_ref": destination_ref,
             "destination_registration_sha256": registration,
             "artifacts": [{"artifact_id": registered["artifact_id"],
                            "sha256": registered["sha256"],
                        "size_bytes": registered["size"]}]}
    config = {"schema": CONFIG_SCHEMA,
              "destinations": [{"project_id": project_id, "destination_ref": destination_ref,
                                "sink_root": str(sink)}],
              "grants": [_grant(project_id, "artifact.publish", raw_authorization_ref, scope)]}
    config_path = _write_config(tmp_path / "host-config", config)

    daemon = ControlDaemon(control_home, project_root=projects, host_provider_config=config_path)
    daemon.backend.endpoint_key = endpoint
    try:
        denied = daemon.dispatch({
            "operation": "artifact.publish",
            "arguments": {"artifact_ids": [registered["artifact_id"]],
                          "destination_ref": destination_ref, "authorization_ref": "wrong-ref"},
            "execution": {"project_id": project_id, "request_id": "provider-publish-denied",
                          "idempotency_key": "provider-publish-denied"},
        })
        assert denied["success"] is False
        assert denied["error"]["code"] == "AUTHORIZATION_REQUIRED"
        assert list(sink.iterdir()) == []
        denied_record = daemon.store.get_operation(denied["execution"]["operation_id"])
        persisted = denied_record["metadata"]["arguments"]
        assert "authorization_ref" not in persisted
        assert persisted["authorization_ref_sha256"] == hashlib.sha256(b"wrong-ref").hexdigest()

        request = {
            "operation": "artifact.publish",
            "arguments": {"artifact_ids": [registered["artifact_id"]],
                          "destination_ref": destination_ref,
                          "authorization_ref": raw_authorization_ref},
            "execution": {"project_id": project_id, "request_id": "provider-publish-valid",
                          "idempotency_key": "provider-publish-valid"},
        }
        published = daemon.dispatch(request)
        assert published["success"] is True, published
        receipt = published["data"]["published"][0]["provider_receipt"]
        assert receipt["no_clobber"] is True and receipt["readback_verified"] is True
        output = sink / (registered["artifact_id"] + ".csv")
        assert output.read_bytes() == source.read_bytes()
        assert hashlib.sha256(output.read_bytes()).hexdigest() == registered["sha256"]
        operation = daemon.store.get_operation(published["execution"]["operation_id"])
        persisted = operation["metadata"]["arguments"]
        assert "authorization_ref" not in persisted
        assert persisted["authorization_ref_sha256"] == hashlib.sha256(raw_authorization_ref.encode()).hexdigest()
        assert daemon.backend.scoped_host_provider.config_sha256
    finally:
        daemon.close()


def test_control_daemon_serve_installs_host_provider_config_from_startup_argument(tmp_path, monkeypatch):
    from comsol_mcp import _control_daemon as daemon_module

    observed = {}

    class FakeLock:
        def __init__(self, _path): pass
        def close(self): pass

    class FakeDaemon:
        def __init__(self, _home, *, host_provider_config=None):
            observed["config"] = host_provider_config
            self._control_singleton_lock_held = False

    class FakeServer:
        server_port = 24972
        daemon_threads = False
        def serve_forever(self): pass

    monkeypatch.setattr(daemon_module, "ProcessLock", FakeLock)
    monkeypatch.setattr(daemon_module, "ControlDaemon", FakeDaemon)
    monkeypatch.setattr(daemon_module, "ThreadingHTTPServer", lambda *_args: FakeServer())
    monkeypatch.setattr(daemon_module, "process_identity", lambda _pid: {"start_epoch_ms": 100})
    config = tmp_path / "managed-by-host.json"
    daemon_module.serve(tmp_path / "serve-home", host_provider_config=config)
    assert observed["config"] == config


def test_control_daemon_serve_reads_host_provider_config_from_environment(tmp_path, monkeypatch):
    from comsol_mcp import _control_daemon as daemon_module

    observed = {}

    class FakeLock:
        def __init__(self, _path): pass
        def close(self): pass

    class FakeDaemon:
        def __init__(self, _home, *, host_provider_config=None):
            observed["config"] = host_provider_config
            self._control_singleton_lock_held = False

    class FakeServer:
        server_port = 24973
        daemon_threads = False
        def serve_forever(self): pass

    monkeypatch.setattr(daemon_module, "ProcessLock", FakeLock)
    monkeypatch.setattr(daemon_module, "ControlDaemon", FakeDaemon)
    monkeypatch.setattr(daemon_module, "ThreadingHTTPServer", lambda *_args: FakeServer())
    monkeypatch.setattr(daemon_module, "process_identity", lambda _pid: {"start_epoch_ms": 100})
    config = tmp_path / "host-provider-from-env.json"
    monkeypatch.setenv("COMSOL_MCP_HOST_PROVIDER_CONFIG", str(config))
    daemon_module.serve(tmp_path / "serve-home-from-env")
    assert observed["config"] == str(config)
