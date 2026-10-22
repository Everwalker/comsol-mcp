"""Software SDK boundary controls; synthetic handles/SIDs never native proof."""
import copy
import ctypes as C
from dataclasses import replace
import hashlib
import json
import os
from types import SimpleNamespace
import weakref

import pytest

from comsol_mcp import _w26_os_resources as r
from comsol_mcp import _windows_disk_resources as disk
from comsol_mcp import _windows_parameter_publish as paths
from comsol_mcp import _windows_host_preflight_owner as owners
from comsol_mcp import _windows_owned_resources as owned
from comsol_mcp._execution_contract import ExecutionContractError
from tests.test_windows_host_preflight_owner import SDK, Function, install

ROOT = r"C:\SoftwareFixture\project"
CONFIG = r"C:\SoftwareFixture\disk.json"
HOST = "software-host"
VOLUME = (1 << 48) + 7
ROOT_ID = (1 << 100) + 12


class DiskSDK(SDK):
    """Replace only native SDK calls; all ownership/path/SD/config code runs."""
    def __init__(self):
        super().__init__()
        self.payload = b"{}"
        self.positions = {}
        self.capacity = (8 * 1024**3, 64 * 1024**3, 16 * 1024**3)
        self.capacity_return = 1
        self.close_mode = self.free_mode = None
        self.effects = []
        self.queries = 0
        self.drift = None
        self.after_read = self.after_capacity = False
        self.token_count = 0
        sid = bytes((1, 1, 0, 0, 0, 0, 0, 5, 18, 0, 0, 0))
        self.descriptor = C.create_string_buffer(bytes(20) + sid, 32)
        self.owner_sid = C.c_void_p(C.addressof(self.descriptor) + 20)
        self.keep += [self.descriptor, self.owner_sid]
        self.a.GetSecurityDescriptorLength = Function(lambda p: 32)
        self.k.GetFileInformationByHandleEx = Function(self.full_info)
        self.k.GetDiskFreeSpaceExW = Function(self.disk_space)
        self.k.ReadFile = Function(self.read)

    def token(self, process, access, out):
        # Nested private-config verification retains both tokens concurrently;
        # a real successful OpenProcessToken cannot return the same live HANDLE.
        self.token_count += 1
        C.cast(out, C.POINTER(C.c_void_p))[0] = 10000 + self.token_count
        self.calls.append("token")
        return 1

    def security(self, h, kind, flags, owner, group, dacl, sacl, descriptor):
        if flags == 1:
            assert kind == 1 and group is dacl is sacl is None
            C.cast(owner, C.POINTER(C.c_void_p))[0] = self.owner_sid
            C.cast(descriptor, C.POINTER(C.c_void_p))[0] = C.cast(self.descriptor, C.c_void_p)
            self.calls.append(("owner_sid", self.address(h)))
            return 0
        return super().security(h, kind, flags, owner, group, dacl, sacl, descriptor)

    def full_info(self, h, kind, out, size):
        directory, inode = self.handles[self.address(h)]
        if kind == 18:
            item = C.cast(out, C.POINTER(paths._FullIdInfo)).contents
            item.volume = VOLUME
            number = (1 << 100) + inode + (1 if self.drift == "root-id" and self.after_capacity else 0)
            item.file_id[:] = number.to_bytes(16, "little")
        elif kind == 0:
            item = C.cast(out, C.POINTER(paths._BasicInfo)).contents
            item.created, item.changed, item.written = 1, 2, 3
            item.accessed = len(self.calls)  # Read-induced access changes are irrelevant.
            if self.drift == "write" and self.after_read:
                item.written += 1
            item.attributes = 16 if directory else 128
            if self.drift == "reparse":
                item.attributes |= 0x400
        elif kind == 1:
            item = C.cast(out, C.POINTER(paths._StandardInfo)).contents
            item.size = 0 if directory else len(self.payload)
            item.directory, item.links = directory, 2 if self.drift == "hardlink" else 1
        else:
            raise AssertionError(kind)
        self.calls.append(("full", self.address(h), kind))
        return 1

    def disk_space(self, path, available, total, free):
        assert path == ROOT
        self.queries += 1
        assert self.handles  # Actual production original-entry validation also runs.
        for out, number in zip((available, total, free), self.capacity):
            C.cast(out, C.POINTER(C.c_uint64))[0] = number
        self.after_capacity = True
        if self.drift == "sd":
            self.descriptor[0] = b"X"
        return self.capacity_return

    def read(self, handle, buffer, size, count, overlapped):
        h = self.address(handle)
        offset = self.positions.get(h, 0)
        chunk = self.payload[offset:offset + size]
        C.memmove(buffer, chunk, len(chunk))
        C.cast(count, C.POINTER(C.c_uint32))[0] = len(chunk)
        self.positions[h] = offset + len(chunk)
        self.after_read = True
        return 1

    def close(self, handle):
        value = self.address(handle)
        self.calls.append(("close", value))
        if self.close_mode == "after-raise":
            self.effects.append(value)
            raise OSError("synthetic close effect then lost result")
        if self.close_mode == "before-raise":
            raise OSError("synthetic close no result")
        if self.close_mode == "known-failure":
            return 0
        if self.close_mode is not None:
            return self.close_mode[0]
        self.effects.append(value)
        return 1

    def free(self, handle):
        value = self.address(handle)
        self.calls.append(("free", value))
        if self.free_mode == "after-raise":
            self.effects.append(value)
            raise OSError("synthetic free effect then lost result")
        if self.free_mode == "before-raise":
            raise OSError("synthetic free no result")
        if self.free_mode == "known-failure":
            return value
        if self.free_mode is not None:
            return self.free_mode[0] if self.free_mode != "other-hlocal" else value + 8
        return None


@pytest.fixture
def sdk(monkeypatch):
    value = DiskSDK()
    install(monkeypatch, value)
    monkeypatch.setattr(disk, "os", SimpleNamespace(name="nt", fspath=os.fspath))
    monkeypatch.setattr(r, "os", SimpleNamespace(name="nt", fspath=os.fspath))
    monkeypatch.setattr(r, "_current_host_id", lambda: HOST)
    monkeypatch.setattr(r, "_current_platform_name", lambda: "windows")
    # Test-only synthetic owners are isolated; never clears live native roots.
    monkeypatch.setattr(disk, "_pending", [])
    monkeypatch.setattr(owners, "_ROOTS", {})
    return value


def profile():
    return r.DiskResourceProfile("software-disk", "software-project", "windows", HOST,
        r.PathIdentity(ROOT, str(VOLUME), str(ROOT_ID), "S-1-5-18"), "windows-volume:" + str(VOLUME),
        r.DiskBudgetPolicy("ADMISSION_ONLY", 1024, 1024 * 1024, None),
        ("study.run",), (r.DiskOperationEstimate("study.run", 65536),))


def config_bytes(*, transform=None):
    p = profile().as_dict()
    row = {k: p[k] for k in ("profile_id", "project_id", "root", "storage_id", "disk_budget", "allowed_operations", "trusted_estimates")}
    data = dict(schema=r.DISK_CONFIG_SCHEMA, host_id=HOST, platform="windows", profiles=[row])
    if transform:
        transform(data)
    return (json.dumps(data, indent=3) + "\n").encode()


def test_real_production_traversal_capacity_full_ids_and_independent_windows_origin(sdk):
    observation = disk.WindowsStorageObservationProvider().read_observation(profile())
    assert observation.free_bytes == 8 * 1024**3 > 2**32
    assert observation.volume_id == str(VOLUME) and observation.root_file_id == str(ROOT_ID)
    assert observation.quota_state == "ADMISSION_ONLY" and observation._native_attestation is None
    assert observation._observation_attestation is not r._POSIX_STORAGE_OBSERVATION_ATTESTATION
    assert disk.is_windows_storage_observation(profile(), observation)
    admission = r.evaluate_disk_admission(profile(), observation, 65536, active_reserved_bytes=0,
                                         now_monotonic_ns=observation.observed_monotonic_ns)
    assert admission.enforcement == "ADMISSION_ONLY"
    assert admission.evidence_classification == "HOST_OBSERVED_ADMISSION_ONLY"
    assert sdk.queries == 1 and not owners.retained_original_owners()
    assert not disk._pending


def test_windows_private_config_exact_raw_bytes_hash_shared_resolver(sdk):
    sdk.payload = config_bytes()
    result = r.TrustedDiskResourceConfig.load(CONFIG)
    assert result.source_sha256 == hashlib.sha256(sdk.payload).hexdigest()
    assert result.source_sha256 != hashlib.sha256(json.dumps(json.loads(sdk.payload), sort_keys=True).encode()).hexdigest()
    assert result.trusted_startup_source and result.resolve("software-project").trusted_startup_config
    assert disk.is_windows_disk_config(result) and disk.is_windows_disk_profile(result.profiles[0])
    assert result._attestation is not r._TRUSTED_DISK_CONFIG_ATTESTATION
    assert result.profiles[0]._trusted_attestation is not r._TRUSTED_DISK_PROFILE_ATTESTATION
    assert not owners.retained_original_owners()


@pytest.mark.parametrize("change", ["extra", "host", "platform", "empty", "duplicate-project", "duplicate-profile", "quota", "boolean-budget", "estimate", "storage", "owner", "root-id"])
def test_config_rejects_untrusted_or_misbound_inputs(sdk, change):
    def mutate(data):
        row = data["profiles"][0]
        if change == "extra": data["verified"] = True
        elif change in ("host", "platform"): data["host_id" if change == "host" else "platform"] = "other"
        elif change == "empty": data["profiles"] = []
        elif change.startswith("duplicate"):
            other = copy.deepcopy(row)
            other["profile_id" if change == "duplicate-project" else "project_id"] += "-other"
            data["profiles"].append(other)
        elif change == "quota": row["disk_budget"]["mode"] = "REQUIRE_HARD_QUOTA"
        elif change == "boolean-budget": row["disk_budget"]["reserve_free_bytes"] = True
        elif change == "estimate": row["trusted_estimates"][0]["estimate_bytes"] = 0
        elif change == "storage": row["storage_id"] = "posix:7"
        elif change == "owner": row["root"]["owner_id"] = "S-1-5-19"
        else: row["root"]["file_id"] = str(ROOT_ID + 1)
    sdk.payload = config_bytes(transform=mutate)
    with pytest.raises((ExecutionContractError, ValueError)):
        r.TrustedDiskResourceConfig.load(CONFIG)


@pytest.mark.parametrize("raw", [b'{"schema":1,"schema":2}', b'{"x":NaN}', b'[]', b'', b' ' * (r._DISK_CONFIG_MAX_BYTES + 1)])
def test_config_strict_json_size_and_empty_controls(sdk, raw):
    sdk.payload = raw
    with pytest.raises(ExecutionContractError):
        r.TrustedDiskResourceConfig.load(CONFIG)


@pytest.mark.parametrize("name", [r"relative\root", r"\\server\share", r"\\?\C:\root", r"C:\a\..\b", r"C:\a\NUL", r"C:\a\file:stream", r"C:\a\\b", r"C:\a\b."])
def test_unsafe_paths_reject_before_sdk_acquisition(sdk, name):
    with pytest.raises(ExecutionContractError):
        disk.observe_windows_root(name)
    assert sdk.calls == []


@pytest.mark.parametrize("drift", ["root-id", "reparse", "sd"])
def test_root_full_identity_and_security_stable_across_capacity(sdk, drift):
    sdk.drift = drift
    with pytest.raises(ExecutionContractError):
        disk.observe_windows_root(ROOT)
    assert not disk._pending


@pytest.mark.parametrize("drift", ["write", "hardlink"])
def test_config_original_handle_extent_write_and_link_count_are_required(sdk, drift):
    sdk.payload = config_bytes()
    sdk.drift = drift
    with pytest.raises(ExecutionContractError):
        r.TrustedDiskResourceConfig.load(CONFIG)


@pytest.mark.parametrize("capacity", [(0, 0, 0), (12, 10, 12), (10, 12, 9), (0, 2**63, 0), (2**63, 2**63, 2**63)])
def test_advisory_capacity_validation_preserves_uint64_bounds(sdk, capacity):
    sdk.capacity = capacity
    with pytest.raises(ExecutionContractError):
        disk.observe_windows_root(ROOT)


@pytest.mark.parametrize("result", [0, False, None, "1", 1.0])
def test_capacity_requires_documented_strict_bool_success(sdk, result):
    sdk.capacity_return = result
    with pytest.raises(ExecutionContractError):
        disk.observe_windows_root(ROOT)


@pytest.mark.parametrize("family,mode", [("close", "after-raise"), ("close", "before-raise"), ("close", (None,)),
    ("close", (False,)), ("close", ("1",)), ("close", (1.0,)),
    ("free", "after-raise"), ("free", "before-raise"), ("free", (False,)), ("free", ("0",)), ("free", "other-hlocal")])
def test_opaque_unknown_retains_original_owner_and_never_calls_closer_or_query_twice(sdk, family, mode):
    setattr(sdk, family + "_mode", mode)
    with pytest.raises(ExecutionContractError) as error:
        disk.observe_windows_root(ROOT)
    owner = error.value._original_disk_owner
    owner_ref = weakref.ref(owner)
    entries = owner.registry.unresolved()
    assert entries and all(e.registry is owner.registry and e.adapter is owner.api for e in entries)
    originals = [(e, e.handle, e.closer, e.keepalive) for e in entries]
    before = list(sdk.calls)
    with pytest.raises(ExecutionContractError):
        disk.recover_windows_disk_resources(owner)
    with pytest.raises(ExecutionContractError):
        disk.observe_windows_root(ROOT)
    assert sdk.calls == before and owner_ref() is owner
    assert all(e.handle is h and e.closer is c and e.keepalive is keep for e, h, c, keep in originals)
    with pytest.raises(ExecutionContractError):
        disk.recover_windows_disk_resources(SimpleNamespace(api=owner.api))
    assert sdk.calls == before


def test_sdk_close_zero_is_not_no_effect_or_second_release_authority(sdk):
    sdk.close_mode = "known-failure"
    with pytest.raises(ExecutionContractError) as error:
        disk.observe_windows_root(ROOT)
    owner = error.value._original_disk_owner
    retained = [(e, e.handle, e.closer, e.keepalive) for e in owner.registry.unresolved()]
    before = list(sdk.calls)
    sdk.close_mode = None
    with pytest.raises(ExecutionContractError):
        disk.recover_windows_disk_resources(owner)
    with pytest.raises(ExecutionContractError):
        disk.observe_windows_root(ROOT)
    assert sdk.calls == before and disk._pending and not owners.confirmed_original_cleanup(owner)
    assert all(e.handle is h and e.closer is c and e.keepalive is keep for e, h, c, keep in retained)


def test_documented_original_local_allocation_failure_can_be_explicitly_coordinated(sdk):
    sdk.free_mode = "known-failure"
    with pytest.raises(ExecutionContractError) as failure:
        disk.observe_windows_root(ROOT)
    owner = failure.value._original_disk_owner
    scope = next(s for s in disk._pending if s.owner is owner)
    retained = [f for f in scope._facts() if f.local_failure]
    assert retained and all(scope.local_allocation(f.entry) and f.result == f.entry.value for f in retained)
    assert all(f.entry.keepalive is f.keepalive and not f.confirmed for f in retained)
    before = list(sdk.calls)
    # A general cleanup/release call does not grant the explicit local retry.
    with pytest.raises(ExecutionContractError):
        owners.cleanup_original(owner.api)
    assert sdk.calls == before
    sdk.free_mode = None
    disk.recover_windows_disk_resources(owner)
    assert sdk.calls[len(before):] and all(c[0] == "free" for c in sdk.calls[len(before):])
    assert len(sdk.calls[len(before):]) == len(retained)
    assert all(f.confirmed and f.complete() and not f.local_failure for f in retained)
    assert owners.confirmed_original_cleanup(owner) and not disk._pending


def test_serialized_json_plain_constructors_and_posix_tokens_do_not_become_windows_origin(sdk):
    p = profile()
    observation = disk.WindowsStorageObservationProvider().read_observation(p)
    assert not disk.is_windows_disk_profile(p)
    assert not disk.is_windows_disk_profile(replace(p, _trusted_attestation=r._TRUSTED_DISK_PROFILE_ATTESTATION))
    for fake in [replace(observation, _observation_attestation=None),
                 replace(observation, _observation_attestation=r._POSIX_STORAGE_OBSERVATION_ATTESTATION),
                 replace(observation, host_id="other"), replace(observation, storage_id="other")]:
        with pytest.raises(ExecutionContractError):
            r.evaluate_disk_admission(p, fake, 1, active_reserved_bytes=0, now_monotonic_ns=fake.observed_monotonic_ns)
    raw = observation.as_dict()
    with pytest.raises(ExecutionContractError):
        r.evaluate_disk_admission(p, raw, 1, active_reserved_bytes=0, now_monotonic_ns=observation.observed_monotonic_ns)
    assert not r.TrustedDiskResourceConfig(HOST, "windows", (p,), "a" * 64, r._TRUSTED_DISK_CONFIG_ATTESTATION).trusted_startup_source


@pytest.mark.parametrize("fault", ["host", "project", "workspace", "file-id"])
def test_windows_authority_root_binding_remains_exact(sdk, fault):
    p, project_id, project = profile(), "software-project", {"workspace": ROOT}
    if fault == "host": p = replace(p, host_id="other")
    elif fault == "project": project_id = "other"
    elif fault == "workspace": project["workspace"] += "-other"
    else: p = replace(p, root=replace(p.root, file_id=str(ROOT_ID + 1)))
    with pytest.raises(ExecutionContractError):
        disk.verify_windows_profile_project(p, project_id, project)


def test_coordinator_refuses_arbitrary_or_cross_platform_provider(sdk, tmp_path):
    from comsol_mcp._operation_store import OperationStore
    from comsol_mcp._project_authority import ProjectAuthority
    sdk.payload = config_bytes()
    config = r.TrustedDiskResourceConfig.load(CONFIG)
    store = OperationStore(tmp_path / "state")
    workspace = tmp_path / "projects"
    workspace.mkdir()
    permissions = {"inspect", "compute", "project_write"}
    authority = ProjectAuthority(store, workspace_root=workspace,
                                 permission_provider=lambda: permissions, grant_ceiling=permissions)
    class Derived(disk.WindowsStorageObservationProvider):
        pass
    try:
        for provider in [SimpleNamespace(read_observation=lambda p: {}), {}, Derived(), r.PosixStorageObservationProvider()]:
            with pytest.raises(ExecutionContractError, match="exact built-in provider"):
                r.DiskResourceCoordinator(config=config, store=store, authority=authority, storage_provider=provider)
        coordinator = r.DiskResourceCoordinator(config=config, store=store, authority=authority)
        assert type(coordinator.storage_provider) is disk.WindowsStorageObservationProvider
    finally:
        store.close()


@pytest.mark.parametrize("fault", ["escaped-pointer", "sid-count", "sid-revision", "sid-extent"])
def test_native_owner_sid_is_bounded_by_its_original_sdk_buffer(sdk, fault):
    original = sdk.a.GetSecurityInfo.callback
    def changed(h, kind, flags, owner, group, dacl, sacl, descriptor):
        result = original(h, kind, flags, owner, group, dacl, sacl, descriptor)
        if flags == 1:
            if fault == "escaped-pointer":
                C.cast(owner, C.POINTER(C.c_void_p))[0] = C.addressof(sdk.descriptor) + 40
            else:
                sdk.descriptor[20 + (0 if fault == "sid-revision" else 1)] = bytes([
                    2 if fault == "sid-revision" else 0 if fault == "sid-count" else 15])
        return result
    sdk.a.GetSecurityInfo.callback = changed
    with pytest.raises(ExecutionContractError):
        disk.observe_windows_root(ROOT)
    assert sdk.queries == 0 and not disk._pending


def test_partial_sdk_allocation_without_return_has_no_free_or_retry_permission(sdk):
    sdk.fault = "relative_io"
    with pytest.raises(ExecutionContractError) as error:
        disk.observe_windows_root(ROOT)
    owner = error.value._original_disk_owner
    partial = [e for e in owner.registry.unresolved() if any(isinstance(v, BaseException) for v in e.keepalive)]
    assert partial
    before = list(sdk.calls)
    assert all(("close", e.value) not in before for e in partial)
    with pytest.raises(ExecutionContractError):
        disk.recover_windows_disk_resources(owner)
    assert sdk.calls == before


def test_replacing_sdk_closer_cannot_recover_original_unknown_resource(sdk):
    sdk.close_mode = "after-raise"
    with pytest.raises(ExecutionContractError) as error:
        disk.observe_windows_root(ROOT)
    before = list(sdk.calls)
    sdk.k.CloseHandle = Function(lambda handle: pytest.fail("replacement closer called"))
    with pytest.raises(ExecutionContractError):
        disk.recover_windows_disk_resources(error.value._original_disk_owner)
    assert sdk.calls == before


@pytest.mark.parametrize("wrong", ["handle", "adapter"])
def test_capacity_requires_original_live_handle_and_adapter_before_query(sdk, monkeypatch, wrong):
    original = disk._capacity
    def changed(api, handle, path):
        if wrong == "handle":
            handle = C.c_void_p(999999)
        else:
            api = SimpleNamespace(k=api.k, _readonly_preflight_owner=api._readonly_preflight_owner)
        return original(api, handle, path)
    monkeypatch.setattr(disk, "_capacity", changed)
    with pytest.raises(ExecutionContractError):
        disk.observe_windows_root(ROOT)
    assert sdk.queries == 0


def test_windows_authority_branch_rechecks_actual_root_after_project_permission(sdk, monkeypatch):
    p = profile()
    # Software authority seam only; native permission/admission is NOT_RUN.
    monkeypatch.setattr(r, "_authorize_project", lambda *args: {"workspace": ROOT})
    result = r._authorize_profile_project(object(), p, p.project_id, "compute", code_prefix="TEST")
    assert result == {"workspace": ROOT} and sdk.queries == 1
    with pytest.raises(ExecutionContractError):
        r._authorize_profile_project(object(), p, "other-project", "compute", code_prefix="TEST")
    assert sdk.queries == 1


def split_owner_sdk(sdk, *, private_file_daemon_owned):
    """Coherent synthetic SID/SD bytes for trusted-admin root vs private config."""
    def binary(text):
        parts = text.split("-")
        subs = [int(n) for n in parts[3:]]
        return bytes((1, len(subs))) + int(parts[2]).to_bytes(6, "big") + b"".join(n.to_bytes(4, "little") for n in subs)
    system = binary("S-1-5-18")
    admin = binary("S-1-5-32-544")
    buffers = [C.create_string_buffer(bytes(20) + sid, 20 + len(sid)) for sid in (system, admin)]
    sdk.keep.extend(buffers)
    sizes = {C.addressof(b): len(b) for b in buffers}
    def security(h, kind, flags, owner, group, dacl, sacl, descriptor):
        directory = sdk.handles[sdk.address(h)][0]
        b = buffers[0 if not directory and private_file_daemon_owned else 1]
        C.cast(owner, C.POINTER(C.c_void_p))[0] = C.addressof(b) + 20
        C.cast(descriptor, C.POINTER(C.c_void_p))[0] = C.addressof(b)
        if flags == 5:
            C.cast(dacl, C.POINTER(C.c_void_p))[0] = C.cast(sdk.acl, C.c_void_p)
        return 0
    def token_info(token, kind, buffer, size, needed):
        C.cast(needed, C.POINTER(C.c_uint32))[0] = 32
        if buffer is None:
            return 0
        C.cast(buffer, C.POINTER(C.c_void_p))[0] = C.addressof(buffer) + 16
        C.memmove(C.addressof(buffer) + 16, system, len(system))
        return 1
    def convert(text, out):
        value = binary(text)
        b = C.create_string_buffer(value, len(value))
        sdk.keep.append(b)
        C.cast(out, C.POINTER(C.c_void_p))[0] = C.addressof(b)
        return 1
    def sid_bytes(pointer):
        header = C.string_at(pointer, 8)
        assert header[0] == 1 and 1 <= header[1] <= 15
        return C.string_at(pointer, 8 + header[1] * 4)
    sdk.a.GetSecurityInfo = Function(security)
    sdk.a.GetSecurityDescriptorLength = Function(lambda p: sizes[sdk.address(p)])
    sdk.a.GetTokenInformation = Function(token_info)
    sdk.a.ConvertStringSidToSidW = Function(convert)
    sdk.a.EqualSid = Function(lambda p, q: int(sid_bytes(p) == sid_bytes(q)))


def test_trusted_admin_root_binds_actual_profile_owner_without_waiving_private_config_owner(sdk):
    split_owner_sdk(sdk, private_file_daemon_owned=False)
    p = replace(profile(), root=replace(profile().root, owner_id="S-1-5-32-544"))
    observation = disk.WindowsStorageObservationProvider().read_observation(p)
    assert observation.root_file_id == p.root.file_id
    with pytest.raises(ExecutionContractError, match="owner"):
        disk.WindowsStorageObservationProvider().read_observation(profile())
    sdk.payload = config_bytes(transform=lambda d: d["profiles"][0]["root"].update(owner_id="S-1-5-32-544"))
    with pytest.raises(ExecutionContractError, match="owned by the daemon SID"):
        r.TrustedDiskResourceConfig.load(CONFIG)
    assert not disk._pending


def test_daemon_private_config_can_resolve_exact_trusted_admin_project_root(sdk):
    split_owner_sdk(sdk, private_file_daemon_owned=True)
    sdk.payload = config_bytes(transform=lambda d: d["profiles"][0]["root"].update(owner_id="S-1-5-32-544"))
    config = r.TrustedDiskResourceConfig.load(CONFIG)
    p = config.resolve("software-project")
    assert p.root.owner_id == "S-1-5-32-544" and p.trusted_startup_config
    assert disk.is_windows_storage_observation(p, disk.WindowsStorageObservationProvider().read_observation(p))
    assert not disk._pending


def bookkeeping_fault(monkeypatch, family, stage, *, remaining=1):
    """Fault only Python ack after a real software SDK normalized success."""
    fired = []
    def target(entry):
        owner = getattr(getattr(entry, "adapter", None), "_readonly_preflight_owner", None)
        guard = owner.sdk.get("CloseHandle" if family == "close" else "LocalFree") if owner else None
        return (len(fired) < remaining and (not fired or entry is fired[0])
                and getattr(guard, "outcomes", {}).get(entry.seq) == "RELEASED")
    if stage.startswith("pop-"):
        class Entries(dict):
            def pop(self, seq, *default):
                entry = self.get(seq)
                if entry is not None and target(entry):
                    fired.append(entry)
                    if stage == "pop-after":
                        super().pop(seq)
                    raise OSError("SOFTWARE registry pop acknowledgement fault")
                return super().pop(seq, *default)
        original_init = owned.NativeOwnedRegistry.__init__
        def initialize(registry, *args, **kwargs):
            original_init(registry, *args, **kwargs)
            registry._entries = Entries()
        monkeypatch.setattr(owned.NativeOwnedRegistry, "__init__", initialize)
    else:
        prior = owned._OwnedResource.__setattr__
        field, moment = stage.split("-")
        def store(entry, name, value):
            wanted = (field == "state" and name == "state" and value == "RELEASED"
                      or field == "keepalive" and name == "keepalive" and value == ())
            if wanted and target(entry):
                fired.append(entry)
                if moment == "after":
                    prior(entry, name, value)
                raise OSError("SOFTWARE original entry acknowledgement fault")
            return prior(entry, name, value)
        monkeypatch.setattr(owned._OwnedResource, "__setattr__", store)
    return fired


@pytest.mark.parametrize("family", ["close", "free"])
@pytest.mark.parametrize("stage", ["state-before", "state-after", "pop-before", "pop-after", "keepalive-before", "keepalive-after"])
def test_confirmed_sdk_success_can_finish_only_original_bookkeeping(sdk, monkeypatch, family, stage):
    fired = bookkeeping_fault(monkeypatch, family, stage)
    with pytest.raises(ExecutionContractError) as failure:
        disk.observe_windows_root(ROOT)
    assert len(fired) == 1
    owner = failure.value._original_disk_owner
    scope = next(s for s in disk._pending if s.owner is owner)
    guard = scope.guards["CloseHandle" if family == "close" else "LocalFree"]
    fact = guard.facts[fired[0].seq]
    assert fact.entry is fired[0] and fact.confirmed and fact.keepalive
    assert fact.result == (1 if family == "close" else None)
    before = list(sdk.calls)
    with pytest.raises(ExecutionContractError):
        disk.observe_windows_root(ROOT)
    disk.recover_windows_disk_resources(owner)
    assert sdk.calls == before and fact.complete()
    assert not disk._pending and owners.confirmed_original_cleanup(owner)
    with pytest.raises(ExecutionContractError):
        disk.recover_windows_disk_resources(owner)
    assert sdk.calls == before


@pytest.mark.parametrize("family", ["close", "free"])
def test_another_ack_fault_retains_the_same_success_fact_until_explicit_coordination(sdk, monkeypatch, family):
    fired = bookkeeping_fault(monkeypatch, family, "state-before", remaining=2)
    with pytest.raises(ExecutionContractError) as failure:
        disk.observe_windows_root(ROOT)
    owner = failure.value._original_disk_owner
    scope = next(s for s in disk._pending if s.owner is owner)
    entry = fired[0]
    fact = next(f for f in scope._facts() if f.entry is entry)
    before = list(sdk.calls)
    with pytest.raises(ExecutionContractError):
        disk.recover_windows_disk_resources(owner)
    assert fired == [entry, entry] and sdk.calls == before
    assert fact.keepalive is entry.keepalive and fact.confirmed and scope in disk._pending
    disk.recover_windows_disk_resources(owner)
    assert sdk.calls == before and owners.confirmed_original_cleanup(owner)


@pytest.mark.parametrize("stage", ["root-pop-before", "root-pop-after", "retired-before", "retired-after"])
def test_owner_retirement_ack_can_continue_after_pop_or_retired_write_fault(sdk, monkeypatch, stage):
    fired = []
    if stage.startswith("root-pop"):
        class Roots(dict):
            def pop(self, key, *default):
                owner = self.get(key)
                if not fired and owner is not None:
                    fired.append(owner)
                    if stage == "root-pop-after":
                        super().pop(key)
                    raise OSError("SOFTWARE owner root pop fault")
                return super().pop(key, *default)
        monkeypatch.setattr(owners, "_ROOTS", Roots())
    else:
        prior = owners._PreflightOwner.__setattr__
        def store(owner, name, value):
            if not fired and name == "retired" and value is True:
                fired.append(owner)
                if stage == "retired-after":
                    prior(owner, name, value)
                raise OSError("SOFTWARE owner retired acknowledgement fault")
            return prior(owner, name, value)
        monkeypatch.setattr(owners._PreflightOwner, "__setattr__", store)
    with pytest.raises(ExecutionContractError) as failure:
        disk.observe_windows_root(ROOT)
    owner = failure.value._original_disk_owner
    assert fired == [owner] and any(s.owner is owner for s in disk._pending)
    assert not owner.registry.unresolved()
    before = list(sdk.calls)
    disk.recover_windows_disk_resources(owner)
    assert sdk.calls == before and owners.confirmed_original_cleanup(owner) and not disk._pending


@pytest.mark.parametrize("binding", ["slot", "handle", "closer", "adapter", "keepalive", "registry-owner", "sdk-closer", "owner-root", "registry-slots", "entry-release", "owner-retire"])
def test_success_ack_refuses_replacement_original_bindings_without_native_reentry(sdk, monkeypatch, binding):
    fired = bookkeeping_fault(monkeypatch, "close", "state-before")
    with pytest.raises(ExecutionContractError) as failure:
        disk.observe_windows_root(ROOT)
    owner = failure.value._original_disk_owner
    entry = fired[0]
    if binding == "slot": owner.registry._entries[entry.seq] = copy.copy(entry)
    elif binding == "handle": entry.handle = C.c_void_p(entry.value)
    elif binding == "closer": entry.closer = lambda h: pytest.fail("replacement closer")
    elif binding == "adapter": entry.adapter = SimpleNamespace()
    elif binding == "keepalive": entry.keepalive = tuple(list(entry.keepalive))
    elif binding == "registry-owner": owner.registry.owner = SimpleNamespace()
    elif binding == "sdk-closer": owner.sdk["CloseHandle"] = Function(lambda h: pytest.fail("replacement SDK"))
    elif binding == "owner-root": owners._ROOTS[id(owner.api)] = SimpleNamespace()
    elif binding == "registry-slots": owner.registry._entries = dict(owner.registry._entries)
    elif binding == "entry-release": entry.release_owned = lambda *a, **kw: pytest.fail("replacement bookkeeping")
    else: owner.reconcile = lambda: pytest.fail("replacement owner retirement")
    before = list(sdk.calls)
    with pytest.raises(ExecutionContractError):
        disk.recover_windows_disk_resources(owner)
    assert sdk.calls == before and any(s.owner is owner for s in disk._pending)


def test_incoming_outcome_text_cannot_turn_sdk_zero_into_a_confirmed_success(sdk):
    sdk.close_mode = "known-failure"
    with pytest.raises(ExecutionContractError) as failure:
        disk.observe_windows_root(ROOT)
    owner = failure.value._original_disk_owner
    guard = owner.sdk["CloseHandle"]
    assert guard.facts and not any(f.confirmed for f in guard.facts.values())
    guard.outcomes.update({seq: "RELEASED" for seq in guard.facts})
    sdk.close_mode = None
    before = list(sdk.calls)
    with pytest.raises(ExecutionContractError):
        disk.recover_windows_disk_resources(owner)
    assert sdk.calls == before and owner.registry.unresolved()


def test_pre_sdk_deferral_is_not_a_normalized_sdk_failure_or_release_permission(sdk, monkeypatch):
    prior = disk._ReleaseGuard.__call__
    deferred = []
    def release(guard, handle):
        if guard.name == "CloseHandle" and not deferred:
            deferred.append((guard, guard.scope.owner.registry.find(guard.scope.api, handle)))
            raise OSError("SOFTWARE deferral before original SDK entry")
        return prior(guard, handle)
    monkeypatch.setattr(disk._ReleaseGuard, "__call__", release)
    with pytest.raises(ExecutionContractError) as failure:
        disk.observe_windows_root(ROOT)
    owner = failure.value._original_disk_owner
    guard, entry = deferred[0]
    assert entry.seq not in guard.facts and entry.seq not in guard.outcomes
    before = list(sdk.calls)
    assert ("close", entry.value) not in before
    with pytest.raises(ExecutionContractError):
        disk.recover_windows_disk_resources(owner)
    assert sdk.calls == before and entry in owner.registry.unresolved()


def test_sdk_return_normalization_exception_cannot_gain_release_retry_authority(sdk, monkeypatch):
    ctypes_module = disk.C
    def bad_sizeof(value):
        raise OSError("SOFTWARE exception normalizing an actual SDK return")
    # Scope-local ctypes view: only this post-SDK normalization fails, not
    # acquisition/ABI setup in the original shared native adapter.
    class CtypesView:
        def __getattr__(self, name):
            return getattr(ctypes_module, name)
    view = CtypesView()
    sdk.k.LocalFree.callback = lambda h: (setattr(view, "sizeof", bad_sizeof) or 0)
    monkeypatch.setattr(disk, "C", view)
    with pytest.raises(ExecutionContractError) as failure:
        disk.observe_windows_root(ROOT)
    owner = failure.value._original_disk_owner
    guard = owner.sdk["LocalFree"]
    assert guard.facts and not any(f.confirmed for f in guard.facts.values())
    before = list(sdk.calls)
    with pytest.raises(ExecutionContractError):
        disk.recover_windows_disk_resources(owner)
    assert sdk.calls == before and owner.registry.unresolved()


def distinct_root(sdk):
    other = r"C:\SoftwareFixture\independent"
    original = sdk.nt.NtCreateFile.callback
    def relative(out, access, attributes, *rest):
        result = original(out, access, attributes, *rest)
        attrs = C.cast(attributes, C.POINTER(paths._ObjectAttributes)).contents
        text = C.wstring_at(attrs.ObjectName.contents.Buffer, attrs.ObjectName.contents.Length // 2)
        if text == "independent":
            handle = C.cast(out, C.POINTER(C.c_void_p))[0]
            sdk.handles[handle] = (True, 13)
        return result
    sdk.nt.NtCreateFile.callback = relative
    amount = sdk.k.GetDiskFreeSpaceExW.callback
    def capacity(path, *outputs):
        assert path in (ROOT, other)
        return amount(ROOT, *outputs)
    sdk.k.GetDiskFreeSpaceExW.callback = capacity
    return other


def test_pending_root_fences_only_conflicts_and_preserves_distinct_root_and_config(sdk):
    other = distinct_root(sdk)
    control = disk.observe_windows_root(other)
    assert int(control.root_file_id) == ROOT_ID + 1
    sdk.close_mode = "after-raise"
    with pytest.raises(ExecutionContractError) as failure:
        disk.observe_windows_root(ROOT)
    owner = failure.value._original_disk_owner
    original_entries = [(e, e.handle, e.closer, e.keepalive) for e in owner.registry.unresolved()]
    sdk.close_mode = None
    actual = disk.observe_windows_root(other)
    assert actual == control
    facts, data = disk._native_read(CONFIG, directory=False)
    assert facts.root_path == CONFIG and data == b"{}"
    assert len(disk._pending) == 1 and disk._pending[0].owner is owner
    assert all(e.handle is h and e.closer is c and e.keepalive is keep for e, h, c, keep in original_entries)


@pytest.mark.parametrize("alias", ["case", "kind", "expected-identity", "observed-identity"])
def test_case_kind_and_same_fileid_aliases_cannot_bypass_pending_scope(sdk, alias):
    sdk.close_mode = "after-raise"
    with pytest.raises(ExecutionContractError) as failure:
        disk.observe_windows_root(ROOT)
    owner = failure.value._original_disk_owner
    assert any(s.identities == {(VOLUME, ROOT_ID)} for s in disk._pending)
    sdk.close_mode = None
    before, queries = list(sdk.calls), sdk.queries
    other_name = r"C:\SoftwareFixture\alias"
    with pytest.raises(ExecutionContractError):
        if alias == "case": disk.observe_windows_root(ROOT.lower())
        elif alias == "kind": disk._native_read(ROOT, directory=False)
        elif alias == "expected-identity": disk.verify_windows_root(replace(profile().root, path=other_name))
        else: disk.observe_windows_root(other_name)
    if alias != "observed-identity":
        assert sdk.calls == before  # Known conflict: no new SDK acquisition.
    # Previously unknown name is denied once its original HANDLE supplies the
    # conflicting ID, before capacity/content; never query/reclose old owner.
    assert sdk.queries == queries and owner.registry.unresolved()
    assert len(disk._pending) == 1 and disk._pending[0].owner is owner


def test_pending_config_does_not_freeze_a_distinct_project_root(sdk):
    sdk.free_mode = "known-failure"
    with pytest.raises(ExecutionContractError) as failure:
        disk._native_read(CONFIG, directory=False)
    owner = failure.value._original_disk_owner
    sdk.free_mode = None
    result = disk.observe_windows_root(ROOT)
    assert result.root_path == ROOT and result.free_bytes == sdk.capacity[0]
    assert owner.registry.unresolved() and len(disk._pending) == 1


@pytest.mark.parametrize("result", [5, False, None, SimpleNamespace(value=0), "raise-after-output"])
def test_unconfirmed_security_allocation_cannot_gain_localfree_failure_exception(sdk, result):
    original = sdk.a.GetSecurityInfo.callback
    def allocation(*args):
        original(*args)  # Simulate a partial output; never a successful SDK return.
        if result == "raise-after-output":
            raise OSError("SOFTWARE allocation returned storage then lost status")
        return result
    sdk.a.GetSecurityInfo.callback = allocation
    with pytest.raises(ExecutionContractError) as failure:
        disk.observe_windows_root(ROOT)
    owner = failure.value._original_disk_owner
    pointer = C.addressof(sdk.descriptor)
    assert ("free", pointer) not in sdk.calls
    entry = next(e for e in owner.registry.unresolved() if e.value == pointer)
    scope = next(s for s in disk._pending if s.owner is owner)
    assert not scope.local_allocation(entry)
    before = list(sdk.calls)
    with pytest.raises(ExecutionContractError):
        disk.recover_windows_disk_resources(owner)
    assert sdk.calls == before and entry in owner.registry.unresolved()


@pytest.mark.parametrize("mode", ["after-raise", "before-raise", (False,), ("0",), (1.0,), "other-hlocal"])
def test_opaque_result_during_documented_localfree_coordination_becomes_sticky_unknown(sdk, mode):
    sdk.free_mode = "known-failure"
    with pytest.raises(ExecutionContractError) as failure:
        disk.observe_windows_root(ROOT)
    owner = failure.value._original_disk_owner
    sdk.free_mode = mode
    with pytest.raises(ExecutionContractError):
        disk.recover_windows_disk_resources(owner)
    sdk.free_mode = None
    before = list(sdk.calls)
    with pytest.raises(ExecutionContractError):
        disk.recover_windows_disk_resources(owner)
    assert sdk.calls == before and owner.registry.unresolved()


@pytest.mark.parametrize("binding", ["holder", "allocation-producer", "closer", "entry", "keepalive"])
def test_documented_localfree_failure_keeps_original_allocation_and_release_bindings(sdk, binding):
    sdk.free_mode = "known-failure"
    with pytest.raises(ExecutionContractError) as failure:
        disk.observe_windows_root(ROOT)
    owner = failure.value._original_disk_owner
    scope = next(s for s in disk._pending if s.owner is owner)
    fact = next(f for f in scope._facts() if f.local_failure)
    allocation = scope.allocations[fact.entry.seq]
    if binding == "holder": allocation[1].value += 8
    elif binding == "allocation-producer": owner.sdk[allocation[3]] = Function(lambda *args: pytest.fail("replacement producer"))
    elif binding == "closer": fact.entry.closer = lambda h: pytest.fail("replacement LocalFree binding")
    elif binding == "entry": owner.registry._entries[fact.entry.seq] = copy.copy(fact.entry)
    else: fact.entry.keepalive = tuple(list(fact.entry.keepalive))
    sdk.free_mode = None
    before = list(sdk.calls)
    with pytest.raises(ExecutionContractError):
        disk.recover_windows_disk_resources(owner)
    assert sdk.calls == before and scope in disk._pending


def test_localfree_coordination_success_then_ack_fault_never_frees_that_allocation_again(sdk, monkeypatch):
    sdk.free_mode = "known-failure"
    with pytest.raises(ExecutionContractError) as failure:
        disk.observe_windows_root(ROOT)
    owner = failure.value._original_disk_owner
    sdk.free_mode = None
    fired = bookkeeping_fault(monkeypatch, "free", "state-before")
    with pytest.raises(ExecutionContractError):
        disk.recover_windows_disk_resources(owner)
    entry = fired[0]
    before = list(sdk.calls)
    disk.recover_windows_disk_resources(owner)
    assert ("free", entry.value) not in sdk.calls[len(before):]
    assert owners.confirmed_original_cleanup(owner) and not disk._pending
