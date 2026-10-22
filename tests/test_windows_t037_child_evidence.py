"""Software controls for the readonly observer; these are NOT native acceptance."""
import ctypes as C
from dataclasses import FrozenInstanceError, replace
from types import SimpleNamespace
import struct

import pytest

from comsol_mcp import _windows_t037_child_evidence as e
from comsol_mcp._windows_owned_resources import registry_for, NativeResourceError


def sid(rid, authority=16):
    return bytes([1, 1]) + authority.to_bytes(6, "big") + struct.pack("<I", rid)


def sid_buffer(rid=8192, *, attributes=0x60, base=0x1000):
    return e.SdkBuffer(struct.pack("<QI4x", base + 16, attributes) + sid(rid), base)


def token(modified=10, *, rid=8192, privileges=None):
    return e.TokenObservation(e.SidAttributes("S-1-5-21-1", 0), (), (),
        e.SidAttributes("S-1-16-" + str(rid), 0x60), rid, 1,
        privileges if privileges is not None else (e.Privilege(23, 3, "SeChangeNotifyPrivilege"),),
        3, 4, modified, False)


class Boundary:
    def __init__(self):
        self.closed = []
        r = registry_for(self)
        self.p = r.acquire(self, 101, self.close, label="test_original_process")
        self.t = r.acquire(self, 102, self.close, label="test_original_thread")
        self.pin = e.PinnedImage(r.acquire(self, 103, self.close, label="test_pin"), self)
        self.binding = e.BorrowedChildHandles(self, self.p, self.t, 123, 234, 100)
        self.actual = e.IdentityObservation(123, 234, 123, 100, 101, True, True)
        self.tokens = [token(), token()]
        self.fail = None

    def close(self, handle):
        self.closed.append(handle)

    def identity(self, p, t):
        assert (p, t) == (101, 102)
        return self.actual

    def token(self, p):
        assert p == 101
        if self.fail == "token":
            raise e.ObservationError("GetTokenInformation", "SDK_ERROR", 5)
        return self.tokens.pop(0)

    def child_policy(self, p):
        return e.ChildPolicyObservation(0, False, False, False)

    def command_line(self, actual_pid):
        assert actual_pid == 123  # Never query a caller's mismatched expected PID.
        if self.fail == "wmi":
            raise e.ObservationError("IWbemClassObject.Get(CommandLine)", "UNVERIFIED", detail="VARIANT vt=1")
        return e.CommandLineObservation(123, '"python.exe" -I -c "pass"', "16010101000000.000010+000", 100, None)

    def image(self, p, pin):
        if self.fail == "mapping":
            raise e.ObservationError("VirtualQueryEx", "SDK_ERROR", 5)
        f = e.FileObservation(r"\Device\volume\python.exe", 1, 2, 100, 1, 32, 4, "a" * 64)
        return e.ImageObservation("C:\\python.exe", f.native_path, f.native_path, 4096, 4096,
                                  8192, 0x1000, 0x1000000, f, f, f, True, True)


def observe(api, **kw):
    return e.observe_child(api.binding, api.pin, api=api,
                           expected_command_line='"python.exe" -I -c "pass"', **kw)


def comparisons(result):
    return {item.name: item.matches for item in result.comparisons}


def test_readonly_partial_interface_borrows_original_entries_without_closing():
    a = Boundary()
    result = observe(a)
    assert result.state == "READ_ONLY_OBSERVATIONS_NOT_ADMISSION"
    assert result.source == "SOFTWARE_BOUNDARY_TEST_NOT_NATIVE" and not result.issues
    assert all(value is True for name, value in comparisons(result).items() if name != "expected_modified_id")
    assert comparisons(result)["expected_modified_id"] is None
    assert not a.closed and a.p.state == a.t.state == a.pin.entry.state == "ACQUIRED"
    assert not result.token_before.is_appcontainer and result.token_before.integrity_rid == 8192
    assert not result.child_policy.no_child_process_creation
    with pytest.raises(FrozenInstanceError):
        result.identity_before.pid = 9
    with pytest.raises(ValueError):
        replace(result, state="VERIFIED")


@pytest.mark.parametrize("field,name", [("expected_pid", "created_pid"),
    ("expected_thread_id", "created_thread"), ("expected_birth_filetime", "created_birth")])
def test_wrong_creation_binding_keeps_actual_values_and_records_false(field, name):
    a = Boundary()
    a.binding = replace(a.binding, **{field: 999})
    result = observe(a)
    assert comparisons(result)[name] is False
    assert result.identity_before == a.actual and not a.closed


@pytest.mark.parametrize("change", ["association", "dead", "identity_changed"])
def test_actual_association_liveness_and_before_after_changes(change):
    a = Boundary()
    if change == "association":
        a.actual = replace(a.actual, thread_process_id=999)
    elif change == "dead":
        a.actual = replace(a.actual, thread_live=False)
    else:
        calls = []
        def identity(*_):
            calls.append(True)
            return replace(a.actual, birth_filetime=100 if len(calls) == 1 else 999)
        a.identity = identity
    result = observe(a)
    key = {"association": "thread_process_association", "dead": "live_before",
           "identity_changed": "identity_stable_and_live"}[change]
    assert comparisons(result)[key] is False
    if change == "dead":
        assert result.token_before is None and result.command_line is None and result.issues


@pytest.mark.parametrize("mode", ["released", "unknown", "wrong_owner", "handle_changed", "dict", "alias"])
def test_stale_borrow_rejected_before_any_sdk_call(mode):
    a = Boundary()
    if mode == "released":
        a.p.release_owned(a)
    elif mode == "unknown":
        a.p.state = "UNKNOWN"
    elif mode == "wrong_owner":
        a.binding = replace(a.binding, owner_api=Boundary())
    elif mode == "handle_changed":
        a.p.handle = 555
    elif mode == "dict":
        a.binding = {"process": 101, "state": "VERIFIED"}
    else:
        a.binding = replace(a.binding, thread_entry=a.p)
    a.identity = lambda *_: pytest.fail("must reject before SDK")
    with pytest.raises(e.ObservationError):
        observe(a)


@pytest.mark.parametrize("value", [0, -1, -2, C.c_void_p(-1), C.c_void_p(-2), None, "12", True])
def test_pseudo_invalid_handles_never_enter_registry_or_close(value):
    api = e.WindowsChildEvidenceApi.__new__(e.WindowsChildEvidenceApi)
    closed = []
    api.k = SimpleNamespace(CloseHandle=lambda h: closed.append(h) or 1)
    with pytest.raises(e.ObservationError):
        api._owned_handle(value, "pseudo")
    assert not closed and not registry_for(api).unresolved()


def test_changed_token_modified_id_and_extra_privileges_are_not_normalized_away():
    a = Boundary()
    extra = e.Privilege(99, 0x80000002, "ExtraEnabledPrivilege")
    a.tokens = [token(10, rid=0x4444, privileges=(extra,)), token(11, rid=0x4444, privileges=(extra,))]
    result = observe(a, expected_modified_id=11)
    assert comparisons(result)["token_stable"] is False
    assert comparisons(result)["expected_modified_id"] is False
    assert result.token_before.modified_id == 10 and result.token_after.modified_id == 11
    assert result.token_before.integrity_rid == 0x4444
    assert result.token_before.privileges[0] == extra and extra.enabled


@pytest.mark.parametrize("fault,missing", [("token", "token_before"), ("wmi", "command_line"), ("mapping", "image")])
def test_sdk_failure_returns_partial_facts_and_exact_unverified_component(fault, missing):
    a = Boundary()
    a.fail = fault
    result = observe(a)
    assert getattr(result, missing) is None and result.identity_before == a.actual
    assert result.issues and not a.closed
    assert any(x.native_code == 5 for x in result.issues) if fault != "wmi" else any("vt=1" in x.detail for x in result.issues)


def test_remote_argv_and_mapped_file_mismatch_remain_negative_observations():
    a = Boundary()
    command, image = a.command_line, a.image
    a.command_line = lambda pid: replace(command(pid), command_line="actual different argv")
    a.image = lambda p, pin: replace(image(p, pin), backing_file_matches_pin=False)
    result = observe(a)
    assert comparisons(result)["remote_command_line"] is False
    assert comparisons(result)["mapped_main_backing_matches_pin"] is False
    assert result.command_line.command_line == "actual different argv"


@pytest.mark.parametrize("raw,units,terminated", [(b"", 0, False), (b"x\0", 2, False),
    (b"x\0", 1, True), (b"x\0y\0", 1, True), (b"\0\0", 1, False),
    (b"\x00\xd8", 1, False), (b"x\0", 32768, False), (b"x\0", -1, False)])
def test_utf16_rejects_empty_truncated_outside_or_invalid(raw, units, terminated):
    with pytest.raises(e.ObservationError):
        e.decode_utf16(raw, units, terminated=terminated)


def test_utf16_valid_surrogate_pair_and_counted_terminator():
    raw = "a😀".encode("utf-16-le")
    assert e.decode_utf16(raw + b"\0\0", 3, terminated=True) == "a😀"


@pytest.mark.parametrize("bad", ["", "20261022143000.******+000", "20261322143000.123456+000",
    "20261022143000.123456+***", "20261022143000.123456+9999"])
def test_wmi_creation_timestamp_no_guessed_birth_or_timezone(bad):
    with pytest.raises(e.ObservationError):
        e.command_birth(bad)


def test_dmtf_birth_exact_microsecond_precision():
    assert e.command_birth("16010101010000.000001+060") == 10


def test_bounded_token_sid_groups_privilege_parsers_preserve_raw_values():
    b = sid_buffer(rid=0x4567, attributes=0x87654321)
    assert e.parse_sid_attributes(b) == e.SidAttributes("S-1-16-17767", 0x87654321)
    base = 0x1000
    groups = e.SdkBuffer(struct.pack("<I4xQI4x", 1, base + 24, 0x1234) + sid(1, 15), base)
    assert e.parse_groups(groups) == (e.SidAttributes("S-1-15-1", 0x1234),)
    privileges = e.SdkBuffer(struct.pack("<IQI", 1, 0x1122334455667788, 0x80000002), 0)
    actual = e.parse_privileges(privileges)
    assert actual == (e.Privilege(0x1122334455667788, 0x80000002, None),) and actual[0].enabled


@pytest.mark.parametrize("fault", ["pointer_before", "pointer_after", "count", "truncated",
    "sid_revision", "sid_count", "privilege_count", "short_user"])
def test_token_buffer_pointers_never_dereferenced_outside_returned_length(fault):
    b = sid_buffer()
    raw = bytearray(b.raw)
    if fault == "pointer_before":
        struct.pack_into("<Q", raw, 0, b.base - 1)
    if fault == "pointer_after":
        struct.pack_into("<Q", raw, 0, b.base + len(raw))
    if fault == "truncated":
        raw = raw[:-1]
    if fault == "sid_revision":
        raw[16] = 0
    if fault == "sid_count":
        raw[17] = 16
    if fault == "short_user":
        raw = raw[:15]
    with pytest.raises(e.ObservationError):
        if fault == "count":
            e.parse_groups(e.SdkBuffer(struct.pack("<I4x", 4097), b.base))
        elif fault == "privilege_count":
            e.parse_privileges(e.SdkBuffer(struct.pack("<I", 1), b.base))
        else:
            e.parse_sid_attributes(e.SdkBuffer(bytes(raw), b.base))


@pytest.mark.parametrize("fault", ["unexpected_success", "size_zero", "size_oversized", "bad_error",
    "read_error", "returned_zero", "returned_overflow", "valid"])
def test_production_token_size_protocol_with_sdk_boundary_faults(monkeypatch, fault):
    state = {"error": 122}
    api = e.WindowsChildEvidenceApi.__new__(e.WindowsChildEvidenceApi)
    def query(token, kind, storage, capacity, returned):
        count = C.cast(returned, C.POINTER(e.U32)).contents
        if storage is None:
            count.value = 0 if fault == "size_zero" else e.MAX_BUFFER + 1 if fault == "size_oversized" else 4
            state["error"] = 5 if fault == "bad_error" else 122
            return fault == "unexpected_success"
        count.value = 0 if fault == "returned_zero" else 5 if fault == "returned_overflow" else 4
        if fault == "read_error":
            state["error"] = 5
            return 0
        C.memmove(storage, b"abcd", 4)
        return 1
    api.a = SimpleNamespace(GetTokenInformation=query)
    monkeypatch.setattr(C, "get_last_error", lambda: state["error"], raising=False)
    if fault == "valid":
        assert api._token_buffer(88, 3).raw == b"abcd"
    else:
        with pytest.raises(e.ObservationError):
            api._token_buffer(88, 3)


def test_release_failure_stays_owned_unknown_and_is_not_retried(monkeypatch):
    api = e.WindowsChildEvidenceApi.__new__(e.WindowsChildEvidenceApi)
    calls = []
    api.k = SimpleNamespace(CloseHandle=lambda h: calls.append(h) or 0)
    monkeypatch.setattr(C, "get_last_error", lambda: 6, raising=False)
    entry = api._owned_handle(1234, "failed_close")
    for _ in range(2):
        with pytest.raises(e.ObservationError) as exc:
            entry.release_owned(api)
        assert exc.value.issue.native_code == 6
    assert calls == [1234] and entry.state == "UNKNOWN"
    assert registry_for(api).unresolved() == (entry,)


def test_error_and_release_unknown_never_become_success(monkeypatch):
    a = Boundary()
    fail_entry = registry_for(a).acquire(a, 555, lambda _: False, mode="BOOL", label="ancillary")
    def image(*_):
        try:
            raise e.ObservationError("GetMappedFileNameW", "SDK_ERROR", 5)
        finally:
            fail_entry.release_owned(a)
    a.image = image
    result = observe(a)
    assert result.image is None and any("GetMappedFileNameW" in x.detail for x in result.issues)
    assert fail_entry.state == "UNKNOWN" and not a.closed


def test_sdk_structures_are_fixed_x64_sizes_and_no_import_time_native_query():
    assert C.sizeof(e._FileInfo) == 52 and e._FileInfo.volume.offset == 28
    assert C.sizeof(e._MemoryInfo) == 48 and e._MemoryInfo.size.offset == 24
    assert C.sizeof(e._Variant) == 24 and e._Variant.data.offset == 8


@pytest.mark.parametrize("fault", ["none", "truncated", "wrong_backing", "sdk_error", "non_progress", "release"])
def test_production_mapping_sdk_path_and_backing_file_comparison(monkeypatch, fault):
    api = e.WindowsChildEvidenceApi.__new__(e.WindowsChildEvidenceApi)
    closed = []
    native = r"\Device\HarddiskVolume3\python.exe"
    f = e.FileObservation(native, 1, 2, 10, 1, 32, 4, "a" * 64)
    pin = e.PinnedImage(registry_for(api).acquire(api, 99, lambda h: closed.append(h)), api)
    def text(buffer, value):
        data = value.encode("utf-16-le")
        C.memmove(buffer, data + b"\0\0", len(data) + 2)
        return len(data) // 2
    def process_name(handle, flags, buffer, used):
        C.cast(used, C.POINTER(e.U32)).contents.value = text(buffer, native if flags else "C:\\python.exe")
        return 1
    def query(handle, address, result, size):
        if fault == "sdk_error":
            return 0
        region = C.cast(result, C.POINTER(e._MemoryInfo)).contents
        region.base = 0 if fault == "non_progress" else 4096
        region.allocation_base, region.size = 4096, 0 if fault == "non_progress" else 8192
        region.state, region.type = 0x1000, 0x1000000
        return size - 1 if fault == "truncated" else size
    api.k = SimpleNamespace(QueryFullProcessImageNameW=process_name, VirtualQueryEx=query)
    api.ps = SimpleNamespace(GetMappedFileNameW=lambda p, b, buffer, cap: text(buffer, native))
    def pin_mapped(path):
        assert path == "\\\\?\\GLOBALROOT" + native
        def close(handle):
            closed.append(handle)
            return fault != "release"
        return e.PinnedImage(registry_for(api).acquire(api, 100, close, mode="BOOL", label="mapped"), api)
    api.pin_image = pin_mapped
    api._file = lambda entry: replace(f, file_id=999) if entry.value == 100 and fault == "wrong_backing" else f
    monkeypatch.setattr(C, "get_last_error", lambda: 5, raising=False)
    if fault in {"none", "wrong_backing"}:
        result = api.image(101, pin)
        assert result.mapped_path == native and result.allocation_base == 4096
        assert result.backing_file_matches_pin is (fault == "none") and result.pinned_stable
        assert closed == [100] and pin.entry.state == "ACQUIRED"
    else:
        with pytest.raises((e.ObservationError, NativeResourceError)):
            api.image(101, pin)
        if fault == "release":
            assert closed == [100] and any(x.state == "UNKNOWN" for x in registry_for(api).unresolved())


@pytest.mark.parametrize("fault", ["none", "empty", "null", "outsize", "query_error", "clear_error"])
def test_production_com_variant_bstr_ownership_and_partial_error(monkeypatch, fault):
    """Exercise production COM control flow; vtable boundary only is simulated."""
    api = e.WindowsChildEvidenceApi.__new__(e.WindowsChildEvidenceApi)
    allocations, releases, cleared, uninit = {}, [], [], []
    def allocate(value):
        storage = C.create_string_buffer(value.encode("utf-16-le") + b"\0\0")
        address = C.addressof(storage)
        allocations[address] = (storage, len(value.encode("utf-16-le")) // 2)
        return address
    def free(pointer):
        assert pointer in allocations
        allocations.pop(pointer)
    def clear(pointer):
        variant = C.cast(pointer, C.POINTER(e._Variant)).contents
        cleared.append(C.addressof(variant))
        if fault == "clear_error":
            return -1
        if variant.vt == 8 and variant.data[0]:
            free(variant.data[0])
        variant.vt = 0
        return 0
    def output(pointer, value):
        C.cast(pointer, C.POINTER(e.PTR)).contents.value = value
        return 0
    api.ole = SimpleNamespace(CoInitializeEx=lambda *a: 0,
        CoUninitialize=lambda: uninit.append(True),
        CoCreateInstance=lambda c, u, mode, iid, out: output(out, 201),
        CoSetProxyBlanket=lambda *a: 0)
    api.auto = SimpleNamespace(SysAllocString=allocate, SysFreeString=free,
        SysStringLen=lambda p: e.MAX_UTF16 if fault == "outsize" else allocations[p][1],
        VariantClear=clear)
    def method(pointer, index, result, *types):
        if index == 2:
            return lambda value: releases.append(value) or 0
        if pointer.value == 201 and index == 3:
            return lambda this, ns, user, password, loc, flags, auth, ctx, out: output(out, 202)
        if pointer.value == 202 and index == 20:
            def query(this, language, query, flags, ctx, out):
                if fault == "query_error":
                    return -2147217405  # retained HRESULT, no expected command substitution.
                assert flags == 0x30
                return output(out, 203)
            return query
        if pointer.value == 203 and index == 4:
            def next_row(this, timeout, count, objects, used):
                assert timeout == 1500 and count == 2
                objects[0] = 204
                C.cast(used, C.POINTER(e.U32)).contents.value = 1
                return 1
            return next_row
        assert pointer.value == 204 and index == 4
        def get(this, name, flags, result, typ, flavor):
            variant = C.cast(result, C.POINTER(e._Variant)).contents
            if name == "ProcessId":
                variant.vt, variant.data[0] = 3, 123
            elif name == "ExecutablePath" or (name == "CommandLine" and fault == "null"):
                variant.vt = 1
            else:
                value = "16010101000000.000010+000" if name == "CreationDate" else "python.exe -I -c pass"
                variant.vt = 8
                variant.data[0] = allocate("" if name == "CommandLine" and fault == "empty" else value)
            return 0
        return get
    api._method = method
    if fault == "none":
        result = api.command_line(123)
        assert result.command_line == "python.exe -I -c pass" and result.pid == 123
        assert result.birth_filetime_microsecond_precision == 100 and result.executable_path is None
    else:
        with pytest.raises(e.ObservationError):
            api.command_line(123)
    assert releases[-1] == 201 and len(releases) == len(set(releases))
    if fault != "clear_error":
        assert not allocations and uninit == [True] and not registry_for(api).unresolved()
    else:
        assert uninit == []  # keep exact storage/apartment on unresolved VariantClear.
        retained = registry_for(api).unresolved()
        assert retained and all(x.state == "UNKNOWN" and x.keepalive for x in retained)
    assert len(cleared) == len(set(cleared))  # no failed release retry within cleanup.


@pytest.mark.parametrize("change", ["unchanged", "access", "write", "creation", "id_low", "links", "attributes", "short_read"])
def test_production_file_hash_ignores_only_read_access_time_and_retains_it(change):
    """Regression for the real NTFS read side effect; all other guards stay."""
    api = e.WindowsChildEvidenceApi.__new__(e.WindowsChildEvidenceApi)
    calls, reads = [], []
    def info(handle, pointer):
        value = C.cast(pointer, C.POINTER(e._FileInfo)).contents
        value.attributes, value.creation, value.access, value.write = 32, 10, 11, 12
        value.volume, value.low, value.links, value.id_low = 1, 3, 1, 9
        if calls and change in {"access", "write", "creation", "id_low", "links", "attributes"}:
            setattr(value, change, getattr(value, change) + 1)
        calls.append(True)
        return 1
    def path(handle, buffer, cap, flags):
        assert flags == 2
        raw = r"\Device\volume\python.exe".encode("utf-16-le")
        C.memmove(buffer, raw + b"\0\0", len(raw) + 2)
        return len(raw) // 2
    def read(handle, storage, cap, used, overlap):
        payload = b"" if reads else b"ab" if change == "short_read" else b"abc"
        C.memmove(storage, payload, len(payload))
        C.cast(used, C.POINTER(e.U32)).contents.value = len(payload)
        reads.append(True)
        return 1
    api.k = SimpleNamespace(GetFileInformationByHandle=info, GetFinalPathNameByHandleW=path,
        GetFileType=lambda _: 1, SetFilePointerEx=lambda *a: 1, ReadFile=read)
    entry = registry_for(api).acquire(api, 44, lambda _: None)
    if change in {"unchanged", "access"}:
        result = api._file(entry)
        assert result.sha256 == __import__("hashlib").sha256(b"abc").hexdigest()
        assert result.access_before_filetime == 11
        assert result.access_after_filetime == (12 if change == "access" else 11)
        assert result.stability_key == replace(result, access_after_filetime=999).stability_key
    else:
        with pytest.raises(e.ObservationError) as exc:
            api._file(entry)
        assert exc.value.issue.code == "FILE_CHANGED" and "before=" in exc.value.issue.detail


def test_separate_query_adapter_must_share_the_original_owner_registry():
    from comsol_mcp._windows_owned_resources import attach_registry
    owner = Boundary()
    query = SimpleNamespace(identity=owner.identity, token=owner.token,
        child_policy=owner.child_policy, command_line=owner.command_line, image=owner.image)
    with pytest.raises(e.ObservationError) as exc:
        e.observe_child(owner.binding, owner.pin, api=query)
    assert exc.value.issue.code == "DIFFERENT_OWNER"
    attach_registry(query, registry_for(owner))
    observed = e.observe_child(owner.binding, owner.pin, api=query)
    assert observed.identity_before == owner.actual and not observed.issues
    assert not owner.closed and registry_for(query).owner is owner


def test_documented_child_policy_keeps_all_raw_flags():
    api = e.WindowsChildEvidenceApi.__new__(e.WindowsChildEvidenceApi)
    def policy(handle, kind, output, size):
        assert handle == 123 and kind == 13 and size == 4
        C.cast(output, C.POINTER(e.U32)).contents.value = 0x80000007
        return 1
    api.k = SimpleNamespace(GetProcessMitigationPolicy=policy)
    observed = api.child_policy(123)
    assert observed.flags == 0x80000007
    assert observed.no_child_process_creation and observed.audit_no_child_process_creation
    assert observed.allow_secure_process_creation
