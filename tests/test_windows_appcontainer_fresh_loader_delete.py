"""Normal trusted-loader AppContainer RESTORE path with explicit software SDK seams.

The production loader, Windows path verifier, Controller, factory, source-origin
checks, original-effect selector, RESTORE validator, Store and Delete owner run
normally. Only the Win32/file/process SDK observations are synthetic; no Windows
ACL, service, AppContainer or host mutation is claimed by this fixture.
"""
from __future__ import annotations

import ctypes as C
import hashlib
import json
import os
import stat
import struct
import sys
import time
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import pytest

from comsol_mcp import (
    _host_scoped_provider as host_provider,
    _operation_store as operation_store,
    _platform_process,
    _windows_appcontainer_lifecycle as lifecycle,
    _windows_host_change as changes,
    _windows_parameter_publish as publish,
    _windows_t037 as windows_t037,
    _windows_t037_targets as targets,
    _windows_t037_wfp as wfp,
    _windows_host_preflight_owner as preflight_owner,
    _w26_os_resources as resources,
)
from comsol_mcp._windows_owned_resources import registry_for
from tests.test_windows_appcontainer import environment
from tests.test_windows_appcontainer_delete import _delete_sdk, running_original_controller
from tests.test_windows_host_change import _grant, _write_provider


class _Function:
    def __init__(self, callback):
        self.callback = callback
        self.argtypes = None
        self.restype = None

    def __call__(self, *args):
        value = self.callback(*args)
        return int(value) if type(value) is bool else value


def _sid(text):
    parts = [int(part) for part in text.split("-")[1:]]
    return (bytes((parts[0], len(parts) - 2)) + parts[1].to_bytes(6, "big")
            + b"".join(part.to_bytes(4, "little") for part in parts[2:]))


class _SourceHandle:
    def __init__(self, path, fd):
        self.path, self.fd, self.value = path, fd, fd


class _PinnedSourceObservation:
    """Local read-only file observer standing at the loader's native SDK seam."""

    def __init__(self, expected_path):
        self.expected_path = Path(expected_path)
        self._open_files = {}

    def open_security_path(self, path, *, directory, read_data=False):
        path = Path(path)
        if not directory or path != self.expected_path.parent or read_data:
            raise AssertionError("loader source parent differed from the bounded fixture")
        fd = os.open(path, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
                     | getattr(os, "O_NOFOLLOW", 0))
        handle = _SourceHandle(path, fd)
        self._open_files[fd] = handle
        registry_for(self).acquire(
            self, handle, lambda value: self._close(value), mode="VOID",
            label="fixture_source_parent", keepalive=(handle,),
            value_reader=lambda item: item.value,
        )
        return [handle]

    def open_source(self, parent, leaf):
        path = parent.path / leaf
        if path != self.expected_path:
            raise AssertionError("loader opened an unexpected source file")
        fd = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
        handle = _SourceHandle(path, fd)
        self._open_files[fd] = handle
        registry_for(self).acquire(
            self, handle, lambda value: self._close(value), mode="VOID",
            label="fixture_source_file", keepalive=(handle,),
            value_reader=lambda item: item.value,
        )
        return handle

    def source_fingerprint(self, handle):
        info = os.fstat(handle.fd)
        return ((int(info.st_dev), int(info.st_ino)), int(info.st_size), int(info.st_ctime_ns),
                int(info.st_mtime_ns), int(info.st_nlink))

    def read_chunk(self, handle, size):
        return os.read(handle.fd, size)

    def _close(self, fd):
        os.close(fd)
        self._open_files.pop(fd, None)


class _PathHandle:
    def __init__(self, path, identity, serial):
        self.path = path
        self.identity = identity
        self.value = serial


class _WindowsPathObservationApi:
    """SDK values are derived from actual fixture paths and exact profile IDs."""

    def __init__(self, project_root, profile):
        self.project_root = Path(project_root)
        self.profile = profile
        self.volume_id = int(self.project_root.stat().st_dev)
        self._serial = 10000
        self._handles = {}
        self._borrowed_handles = {}
        self.k = SimpleNamespace()
        self.k.CloseHandle = _Function(lambda _handle: 1)
        self.k.LocalFree = _Function(lambda _pointer: None)
        self.k.GetCurrentProcess = _Function(lambda: C.c_void_p(-1).value)
        self.k.GetVolumePathNameW = _Function(self._volume_path)
        self.k.GetVolumeInformationW = _Function(self._volume_info)
        self.k.GetFileInformationByHandleEx = _Function(self._full_file_info)

    def borrow_fd(self, fd):
        value = 0x70000000 + int(fd)
        self._borrowed_handles[value] = int(fd)
        return value

    def _observed_stat(self, handle):
        value = getattr(handle, "value", None)
        borrowed_fd = self._borrowed_handles.get(value)
        if borrowed_fd is not None:
            return os.fstat(borrowed_fd), None
        if isinstance(handle, _PathHandle):
            return handle.path.stat(), handle
        raise AssertionError("full identity requested for an unbound fixture handle")

    def _full_file_info(self, handle, kind, output, _size):
        from comsol_mcp import _windows_parameter_publish as parameter_publish
        info, pinned = self._observed_stat(handle)
        file_id = int(info.st_ino).to_bytes(16, "little")
        if kind == 18:
            row = C.cast(output, C.POINTER(parameter_publish._FullIdInfo)).contents
            row.volume = self.volume_id
            for index, byte in enumerate(file_id):
                row.file_id[index] = byte
        elif kind == 0:
            row = C.cast(output, C.POINTER(parameter_publish._BasicInfo)).contents
            row.created = int(info.st_ctime_ns)
            row.accessed = int(info.st_atime_ns)
            row.written = int(info.st_mtime_ns)
            row.changed = int(info.st_ctime_ns)
            row.attributes = 0x10 if stat.S_ISDIR(info.st_mode) else 0x80
        elif kind == 1:
            row = C.cast(output, C.POINTER(parameter_publish._StandardInfo)).contents
            row.allocation = int(info.st_size)
            row.size = int(info.st_size)
            row.links = int(info.st_nlink)
            row.delete_pending = 0
            row.directory = int(stat.S_ISDIR(info.st_mode))
        else:
            raise AssertionError(f"unexpected full-file metadata class {kind}")
        return 1

    @staticmethod
    def _write_wide(output, text):
        raw = text.encode("utf-32-le")
        C.memmove(output, raw + b"\0\0\0\0", len(raw) + 4)

    def _volume_path(self, _path, output, _length):
        self._write_wide(output, "C:\\")
        return 1

    def _volume_info(self, _root, _fs_name, _fs_len, serial, _max_component,
                     flags, _fs, _fs_size):
        C.cast(serial, C.POINTER(C.c_uint32)).contents.value = self.volume_id
        C.cast(flags, C.POINTER(C.c_uint32)).contents.value = 0x8
        return 1

    def open_security_path(self, path, *, directory, read_data=False):
        path = Path(path)
        if read_data:
            raise AssertionError("profile loader/verifier fixture never requests file contents here")
        chain = [Path(path.anchor)]
        current = Path(path.anchor)
        for part in path.parts[1:]:
            current = current / part
            chain.append(current)
        owner = preflight_owner.hold_original(self)
        handles = []
        for item in chain:
            info = item.stat()
            if item == self.project_root:
                identity = (int(self.profile.root.volume_id), int(self.profile.root.file_id))
            else:
                identity = (self.volume_id, int(info.st_ino))
            self._serial += 1
            handle = _PathHandle(item, identity, self._serial)
            self._handles[handle.value] = handle
            closer = owner.function("CloseHandle", self.k)
            owner.capture(handle, family="handle", closer=closer, keepalive=(item, handle))
            handles.append(handle)
        if not handles:
            raise AssertionError("empty pinned path chain")
        return handles

    @staticmethod
    def info(handle, *, directory):
        expected_dir = handle.path.is_dir()
        if expected_dir is not directory:
            raise AssertionError("file/directory observation changed")
        return handle.identity, 0


class _FakeAdvapi:
    """Bounded Win32 SDK seam; the production DACL parser still runs."""

    def __init__(self):
        self.owner_bytes = _sid("S-1-5-18")
        ace = struct.pack("<BBHI", 0, 0, 8 + len(self.owner_bytes), 0x120089) + self.owner_bytes
        self.acl_bytes = struct.pack("<BBHHH", 2, 0, 8 + len(ace), 1, 0) + ace
        self.owner = C.create_string_buffer(self.owner_bytes)
        self.acl = C.create_string_buffer(self.acl_bytes)
        self.descriptor = C.create_string_buffer(b"fixture-owner-dacl-observation")
        self.allocations = []
        self.OpenProcessToken = _Function(self._open_process_token)
        self.GetTokenInformation = _Function(self._get_token_information)
        self.ConvertStringSidToSidW = _Function(self._convert_sid)
        self.GetSecurityInfo = _Function(self._get_security_info)
        self.GetSecurityDescriptorDacl = _Function(self._get_dacl)
        self.GetSecurityDescriptorLength = _Function(lambda _descriptor: len(self.descriptor) - 1)
        self.GetAclInformation = _Function(self._get_acl_info)
        self.GetAce = _Function(self._get_ace)
        self.IsValidSid = _Function(self._is_valid_sid)
        self.GetLengthSid = _Function(self._sid_length)
        self.EqualSid = _Function(self._equal_sid)

    @staticmethod
    def _put_pointer(target, value):
        C.cast(target, C.POINTER(C.c_void_p)).contents.value = value

    def _open_process_token(self, _process, _access, output):
        self._put_pointer(output, 0x7711)
        return 1

    def _get_token_information(self, _token, _kind, output, size, needed):
        offset = (C.sizeof(C.c_void_p) + C.sizeof(C.c_uint32) + 7) & ~7
        required = offset + len(self.owner_bytes)
        C.cast(needed, C.POINTER(C.c_uint32)).contents.value = required
        if not output or not size:
            return 0
        C.cast(output, C.POINTER(C.c_void_p)).contents.value = C.addressof(output) + offset
        C.memmove(C.addressof(output) + offset, self.owner_bytes, len(self.owner_bytes))
        C.cast(needed, C.POINTER(C.c_uint32)).contents.value = required
        return 1

    def _convert_sid(self, text, output):
        value = C.create_string_buffer(_sid(text))
        self.allocations.append(value)
        self._put_pointer(output, C.addressof(value))
        return 1

    def _get_security_info(self, _handle, _object_type, _security_info,
                           owner, _group, dacl, _sacl, descriptor):
        self._put_pointer(owner, C.addressof(self.owner))
        self._put_pointer(dacl, C.addressof(self.acl))
        self._put_pointer(descriptor, C.addressof(self.descriptor))
        return 0

    def _get_dacl(self, _descriptor, present, dacl, defaulted):
        C.cast(present, C.POINTER(C.c_int32)).contents.value = 1
        self._put_pointer(dacl, C.addressof(self.acl))
        C.cast(defaulted, C.POINTER(C.c_int32)).contents.value = 0
        return 1

    def _get_acl_info(self, _acl, output, _size, _kind):
        class _AclInfo(C.Structure):
            _fields_ = [("AceCount", C.c_uint32), ("AclBytesInUse", C.c_uint32),
                        ("AclBytesFree", C.c_uint32)]
        row = C.cast(output, C.POINTER(_AclInfo)).contents
        row.AceCount = 1
        row.AclBytesInUse = len(self.acl_bytes)
        row.AclBytesFree = 0
        return 1

    def _get_ace(self, _acl, index, output):
        if index != 0:
            return 0
        self._put_pointer(output, C.addressof(self.acl) + 8)
        return 1

    @staticmethod
    def _sid_length(pointer):
        value = getattr(pointer, "value", pointer)
        raw = C.string_at(value, 8)
        return 8 + raw[1] * 4

    def _is_valid_sid(self, pointer):
        try:
            value = getattr(pointer, "value", pointer)
            raw = C.string_at(value, 8)
            return int(raw[0] == 1 and raw[1] <= 15)
        except (TypeError, ValueError, OSError):
            return 0

    def _equal_sid(self, left, right):
        left_value = getattr(left, "value", left)
        right_value = getattr(right, "value", right)
        try:
            left_size = self._sid_length(left_value)
            right_size = self._sid_length(right_value)
            return int(left_size == right_size and C.string_at(left_value, left_size)
                       == C.string_at(right_value, right_size))
        except (TypeError, ValueError, OSError):
            return 0


def _current_process_observers(monkeypatch, profile):
    class _ReadOnlyApi:
        def __init__(self):
            self.k = SimpleNamespace(GetCurrentProcess=lambda: C.c_void_p(-1).value)

        def open_process_token(self, _process):
            return object()

        def read_token(self, _token):
            return {"state": "OBSERVED", "user_sid": profile.windows_isolation.service_logon_sid}

        def close_handle(self, _handle):
            return None

    birth = ((time.time_ns() // 100) + 11644473600000 * 10000)
    process_birth_ms = birth // 10000 - 11644473600000

    class _Observer:
        def __init__(self, *, platform_name=None, api=None):
            assert platform_name == "windows"
            self.api = _ReadOnlyApi()

        def observe_process(self, pid):
            launcher, = [row for row in profile.launch_images if row.role == "launcher"]
            return {
                "pid": pid,
                "birth_filetime_100ns": birth,
                "exit_filetime_100ns": None,
                "image_path": launcher.path,
                "token": {
                    "state": "OBSERVED",
                    "user_sid": profile.windows_isolation.service_logon_sid,
                    "group_sids": [], "logon_sids": [], "is_appcontainer": False,
                    "appcontainer_sid": None, "capability_sids": [],
                    "integrity_sid": "S-1-16-8192", "integrity_attributes": 0,
                },
                "job_membership_any": False,
                "job_membership_exact": None,
                "job": None,
                "observation_state": "OBSERVED",
            }

    monkeypatch.setattr(windows_t037, "_Win32ReadOnlyApi", _ReadOnlyApi)
    monkeypatch.setattr(windows_t037, "WindowsT037Observer", _Observer)
    monkeypatch.setattr(_platform_process, "process_identity",
                        lambda pid, *, platform_name: (
                            {"alive": True, "start_epoch_ms": process_birth_ms}
                            if pid == os.getpid() and platform_name == "nt" else None))


def _setup_normal_loader(tmp_path, monkeypatch, *, drift_after_factory=None):
    real_loader = windows_t037.load_trusted_profile
    real_factory = changes.WindowsHostChangeExecutor.from_trusted_profile_path.__func__
    env, sdk = environment(tmp_path, monkeypatch, with_restore=True)
    env = list(env)
    profile, _old_plan, _scm, _files, software_api, store, provider, authority, _soft_executor, daemon, _old = env
    observed_service_subject = software_api._service_sid_observe(
        profile.windows_isolation.service_name)

    project_root = tmp_path / "observed-project-root"
    project_root.mkdir(mode=0o700)
    profile = replace(profile, root=replace(
        profile.root, volume_id=str(project_root.stat().st_dev),
        file_id=str(project_root.stat().st_ino)))
    policy = profile.windows_isolation
    manifest = {
        "action_id": "appcontainer-create", "resource_kind": "appcontainer_profile",
        "resource_name": policy.appcontainer_profile_name, "operation": "CREATE",
        "desired": {"profile_id": profile.applied_policy_id,
                    "policy_sha256": resources._sha256(policy.as_dict())},
        "baseline_identity": None, "baseline_sha256": None,
        "backup_path": None, "backup_sha256": None,
    }
    plan = resources.build_host_change_plan(profile, change_manifest=[manifest],
                                            operator_inputs={})
    action = changes._normalize_supported_actions(plan, profile)[1][0]
    expected_effect = {
        "profile_name": policy.appcontainer_profile_name,
        "appcontainer_sid": policy.appcontainer_sid,
        "service_user_sid": policy.service_logon_sid,
        "creation_result": "S_OK", "capabilities_state": "NOT_READ_BACK",
        "generation_state": "UNAVAILABLE",
    }
    expected_effect_sha = resources._sha256(expected_effect)
    predicted_rows = ({
        "action_id": action["action_id"], "resource_kind": "appcontainer_profile",
        "resource_key": action["resource_key"],
        "created_resource_sha256": expected_effect_sha,
        "prior_resource_sha256": None, "prior_backup_path": None,
    },)
    receipt_body = changes._receipt_payload_v2(plan, profile, predicted_rows)
    receipt_sha = resources._sha256(receipt_body)
    predicted = resources.AppliedHostChangeReceipt(
        plan.sha256, profile.host_id, "windows", predicted_rows, receipt_sha)
    _restore_body, restore_sha, _ = lifecycle._restore_selection_digest(
        plan, profile, predicted, action)
    grants = [
        _grant(profile, "host.change.apply", "apply-once", plan.sha256,
               phase="APPLY"),
        _grant(profile, "host.change.restore", "restore-once", restore_sha,
               phase="RESTORE", source_plan=plan.sha256,
               source_receipt=receipt_sha),
    ]
    env[0], env[1] = profile, plan

    source_dir = tmp_path / "normal-loader-profile"
    source_dir.mkdir(mode=0o700)
    source_path = source_dir / "profile.json"
    # Keep real loader and verifier functions. Replace only their platform
    # native SDK adapters with deterministic observations of this real file.
    monkeypatch.setattr(windows_t037, "load_trusted_profile", real_loader)
    monkeypatch.setattr(windows_t037, "os", SimpleNamespace(**{
        **vars(os), "name": "nt", "fspath": os.fspath}))
    monkeypatch.setattr(host_provider, "os", SimpleNamespace(**{
        **vars(os), "name": "nt", "fspath": os.fspath}))
    monkeypatch.setattr(changes, "os", SimpleNamespace(**{
        **vars(os), "name": "nt", "fspath": os.fspath}))
    monkeypatch.setattr(lifecycle, "os", SimpleNamespace(**{
        **vars(os), "name": "nt", "getpid": os.getpid}))
    monkeypatch.setattr(lifecycle, "sys", SimpleNamespace(platform="win32"))
    monkeypatch.setattr(changes.socket, "gethostname", lambda: profile.host_id)
    monkeypatch.setattr(windows_t037, "windows_host_fingerprint",
                        lambda **_kwargs: profile.windows_isolation.host_fingerprint_sha256)
    _current_process_observers(monkeypatch, profile)

    original_path = host_provider.Path

    def mapped_path(value):
        raw = os.fspath(value)
        root = profile.root.path
        if raw == root:
            return project_root
        if raw.startswith(root + "\\"):
            suffix = raw[len(root) + 1:].split("\\")
            return project_root.joinpath(*suffix)
        return original_path(value)

    monkeypatch.setattr(host_provider, "Path", mapped_path)
    path_apis = []

    def new_path_api():
        api = _WindowsPathObservationApi(project_root, profile)
        path_apis.append(api)
        return api

    monkeypatch.setattr(publish, "_API", new_path_api)
    monkeypatch.setitem(sys.modules, "msvcrt", SimpleNamespace(
        get_osfhandle=lambda fd: path_apis[-1].borrow_fd(fd)))
    fake_advapi = _FakeAdvapi()
    monkeypatch.setattr(C, "WinDLL", lambda name, **_kwargs: (
        fake_advapi if name == "advapi32" else SimpleNamespace()), raising=False)
    monkeypatch.setattr(C, "get_last_error", lambda: 122, raising=False)
    monkeypatch.setattr(C, "WinError", lambda code=0: OSError(code, "software SDK fixture"), raising=False)
    monkeypatch.setattr(targets, "_ORIGINS", {})
    monkeypatch.setattr(targets, "_READS", {})
    monkeypatch.setattr(targets, "_LOADER_APIS", {})
    monkeypatch.setattr(targets, "_TargetPathApi",
                        lambda: _PinnedSourceObservation(source_path))

    source_document = {"schema": windows_t037.PROFILE_SCHEMA_V2,
                       "host_resource_profile": profile.as_dict()}
    source_bytes = json.dumps(source_document, sort_keys=True, separators=(",", ":"),
                              allow_nan=False).encode("utf-8")
    source_path.write_bytes(source_bytes)
    os.chmod(source_path, 0o600)

    # Re-open the original provider through its real Windows verifier under
    # the same explicit SDK observation seam. The service lifetime calls its
    # real _assert_stable path, so its startup identity must have been derived
    # by the same platform verifier instead of reusing the POSIX fixture load.
    provider_path = tmp_path / "provider-normal-loader"
    provider.close()
    provider = _write_provider(provider_path, profile, grants)
    authority.authorization_verifier = provider
    daemon._scoped_host_provider = provider
    env[6] = provider

    # Read the daemon's own pinned startup wrapper through the production loader.
    daemon._windows_t037_profile_path = str(source_path)
    startup_wrapper = real_loader(str(source_path))
    assert startup_wrapper.security_semantics == "windows-read-control-owner-dacl-persistent-acls"
    assert startup_wrapper.source_sha256 == hashlib.sha256(source_bytes).hexdigest()
    assert startup_wrapper.host_resource_profile.sha256 == profile.sha256
    env[10] = startup_wrapper
    daemon._windows_t037_profile = startup_wrapper
    daemon._windows_t037_profile_path = str(source_path)

    executors = []

    def create_native_executor(cls, **kwargs):
        # Preserve the actual classmethod constructor and its normal loader.
        instance = real_factory(cls, **kwargs)
        executors.append(instance)
        if drift_after_factory is not None and drift_after_factory(len(executors)):
            source_path.write_bytes(source_path.read_bytes() + b" ")
        return instance

    monkeypatch.setattr(changes.WindowsHostChangeExecutor, "from_trusted_profile_path",
                        classmethod(create_native_executor))

    # The API object remains the exact production Ctypes adapter. Its constructor
    # binds the explicit software SDK functions already used by the original
    # CREATE test fixture; it does not inherit that fixture's registry/owner.
    def adapter_init(instance):
        copied = dict(vars(software_api))
        for key in ("_native_owned_registry", "_readonly_preflight_owner",
                    "_original_service_sid_preflight_owners",
                    "_original_scm_leaf_context", "_captured_mutation_sdk",
                    "_appcontainer_original_call", "_appcontainer_profile_effect",
                    "_first_mutation_cause"):
            copied.pop(key, None)
        instance.__dict__.update(copied)

    monkeypatch.setattr(wfp.CtypesWindowsWfpMutationApi, "__init__", adapter_init)
    monkeypatch.setattr(wfp.CtypesWindowsWfpMutationApi, "_service_sid_observe",
                        lambda _self, name: dict(observed_service_subject)
                        if name == profile.windows_isolation.service_name
                        else (_ for _ in ()).throw(AssertionError("service name changed")))
    return env, sdk, source_path, executors


def test_actual_factory_loader_origin_selector_and_original_delete_owner(tmp_path, monkeypatch):
    env, sdk, source_path, executors = _setup_normal_loader(tmp_path, monkeypatch)
    _delete_sdk(sdk)
    profile, _plan, _scm, _files, _software_api, store, provider, _authority, _soft, daemon, startup = env
    try:
        with running_original_controller(env, tmp_path=tmp_path) as (state, submit, _daemon):
            applied_request, receipt, error = submit("APPLY")
            assert error is None, repr(error)
            assert receipt.receipt_sha256
            effect, = daemon._retained_appcontainer_profile_effects.values()
            assert effect.software_only is False
            assert effect.executor.software_only is False
            assert effect.loader_origin is effect.executor._trusted_profile_load_origin
            assert effect.loader_origin.profile_wrapper is not startup
            assert effect.profile is effect.loader_origin.profile_wrapper.host_resource_profile
            assert effect.profile is not startup.host_resource_profile
            assert effect.profile_source_sha256 == startup.source_sha256
            assert effect.request is applied_request
            assert effect.api is effect.executor.api
            assert effect._original_call[3]["result"] == 0
            assert effect._native_attempt.entered and effect._native_attempt.finished
            assert Path(source_path).read_bytes()

            # The original lifetime owner is terminally paused before the one
            # Delete call; no software-only executor substitutes the RESTORE path.
            state["factory"].close()
            delete_calls = _delete_sdk(sdk)
            restore_request, result, error = submit("RESTORE", receipt, "restore-once")
            assert error is None, repr(error)
            restore_operation = store.db.execute(
                "SELECT operation_id FROM operations WHERE operation='host.change.restore' ORDER BY rowid DESC LIMIT 1"
            ).fetchone()
            operation_row = store.get_operation(restore_operation[0])
            restore_job = store.operation_job(restore_operation[0])
            assert result["native_delete_state"] == "API_CONFIRMED", repr(result)
            assert result["api_return_hresult"] == 0
            assert result["restore_state"] == "POSTIMAGE_UNVERIFIED"
            assert result["host_change_state"] == "RECOVERY_REQUIRED"
            assert result["safe_retry"] is False
            assert result["execution_state"] == "UNKNOWN"
            assert operation_row["status"] == restore_job["status"] == "UNKNOWN"
            assert delete_calls == [profile.windows_isolation.appcontainer_profile_name]
            assert len([call for call in sdk.calls if isinstance(call, tuple)
                        and call[0] == "delete"]) == 1
            assert result["source_receipt_sha256"] == receipt.receipt_sha256
            assert executors[0] is effect.executor
            assert executors[-1].software_only is False
            assert executors[-1].api is not effect.api
            assert executors[-1]._trusted_profile_load_origin.profile_wrapper is not startup
            assert executors[-1]._trusted_profile_load_origin.profile_wrapper is not effect.loader_origin.profile_wrapper
            assert restore_request.phase == "RESTORE"
            assert registry_for(effect.api).unresolved() == ()
            assert not targets._LOADER_APIS and not targets._READS
    finally:
        provider.close()
        store.close()


def test_actual_fresh_loader_restore_refuses_current_source_drift_before_delete(tmp_path, monkeypatch):
    env, sdk, source_path, executors = _setup_normal_loader(
        tmp_path, monkeypatch, drift_after_factory=lambda count: count == 2)
    delete_calls = _delete_sdk(sdk)
    profile, _plan, _scm, _files, _software_api, store, provider, _authority, _soft, daemon, _startup = env
    try:
        with running_original_controller(env, tmp_path=tmp_path) as (state, submit, _daemon):
            _apply_request, receipt, error = submit("APPLY")
            assert error is None, repr(error)
            effect, = daemon._retained_appcontainer_profile_effects.values()
            assert effect.software_only is False
            state["factory"].close()

            _restore_request, result, error = submit("RESTORE", receipt, "restore-once")
            assert result is None
            assert error is not None
            assert error.code == "HOST_CHANGE_APPCONTAINER_PROFILE_SOURCE_CHANGED"
            assert len(executors) == 2
            assert executors[-1].software_only is False
            assert executors[-1]._trusted_profile_load_origin.profile_wrapper is not effect.loader_origin.profile_wrapper
            assert Path(source_path).read_bytes().endswith(b" ")
            assert delete_calls == []
            assert store.db.execute(
                "SELECT count(*) FROM host_control_admissions WHERE phase='RESTORE'"
            ).fetchone()[0] == 0
            assert effect._restore_attempt is None
            assert effect._hresult == 0 and effect._sid_bytes is not None
    finally:
        provider.close()
        store.close()
