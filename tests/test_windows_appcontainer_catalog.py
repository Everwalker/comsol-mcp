"""SOFTWARE_ONLY raw ctypes exports and owned buffers; no Windows/SDK dispatch."""
import copy
import ctypes as C
import gc
import inspect
import weakref

import pytest

from comsol_mcp import _windows_appcontainer_catalog as m


def sid(*sub, authority=15):
    return bytes((1, len(sub))) + authority.to_bytes(6, "big") + b"".join(x.to_bytes(4, "little") for x in sub)


APP, USER, CAP = sid(2, 0xFEDCBA98, 0xFFFFFFFF), sid(21, 123, authority=5), sid(3, 1)


@pytest.fixture(autouse=True)
def isolated_software_roots(monkeypatch):
    # Test-only dictionary isolation, not a native cleanup/release assertion.
    monkeypatch.setattr(m, "_roots", {})


class Once:
    def __init__(self, value):
        self.result, self.reads = value, 0

    @property
    def value(self):
        self.reads += 1
        if self.reads > 1: raise ValueError("SOFTWARE raw scalar consumed twice")
        return self.result


class Fixture:
    """Only real, strongly held local buffers are reported readable by VirtualQuery."""
    def __init__(self, rows=1):
        self.keep, self.regions, self.calls = [], [], []
        self.enum_status = self.free_status = 0
        self.enum_hook = self.free_hook = self.query_hook = None
        self.query_result = None
        self.array = (m._Container * max(rows, 1))()
        self.hold(self.array)
        self.count, self.pointer = rows, C.addressof(self.array)
        self.slot = self.count_slot = None
        for row in self.array:
            row.appContainerSid, row.userSid = self.buffer(APP), self.buffer(USER)
            row.appContainerName = self.buffer("Example.应用😀\0".encode("utf-16-le"))
            caps = (m._SidAttributes * 1)()
            caps[0].Sid, caps[0].Attributes = self.buffer(CAP), 0x80000004
            row.capabilities.count, row.capabilities.capabilities = 1, self.hold(caps)
            # Deliberately unreadable unparsed pointers: none may be queried/dereferenced.
            row.displayName = row.description = row.workingDirectory = row.packageFullName = 1
            row.binaries.count, row.binaries.binaries = 2, 1

    def hold(self, value):
        self.keep.append(value)
        start = C.addressof(value)
        self.regions.append([start, start + C.sizeof(value), 0x1000, 4])
        return start

    def buffer(self, data):
        return self.hold(C.create_string_buffer(data, len(data)))

    def enum(self, flags, count, pointer):
        self.calls.append(("enum", flags))
        self.slot, self.count_slot = pointer._obj, count._obj
        assert any(o.slot is self.slot and o.count is self.count_slot for o in m.retained_catalog_owners())
        C.cast(count, C.POINTER(m.DWORD))[0] = self.count
        C.cast(pointer, C.POINTER(m.PTR))[0] = self.pointer
        if self.enum_hook: self.enum_hook()
        return self.enum_status

    def free(self, pointer):
        assert pointer is self.slot
        self.calls.append(("free", pointer.value))
        if self.free_hook: self.free_hook()
        return self.free_status

    def query(self, pointer, output, length):
        address = pointer.value
        self.calls.append(("query", address, length))
        info = C.cast(output, C.POINTER(m._MemoryInfo)).contents
        for start, end, state, protection in self.regions:
            if start <= address < end:
                info.BaseAddress, info.AllocationBase = start, start
                info.RegionSize, info.State, info.Protect = end - start, state, protection
                if self.query_hook: self.query_hook(info)
                return C.sizeof(info) if self.query_result is None else self.query_result
        return 0

    def owner(self, **kwargs):
        return m._software_owner(self.enum, self.free, self.query, keepalive=(self, *self.keep),
                                 seal=m._TEST_SEAL, **kwargs)


def test_official_win64_abi_is_natural_not_host_wchar_or_packed():
    assert C.sizeof(m.DWORD) == 4 and C.sizeof(m.PTR) == 8
    assert C.sizeof(m._Container) == 88 and C.alignment(m._Container) == 8
    assert {name: getattr(m._Container, name).offset for name, _ in m._Container._fields_} == {
        "appContainerSid": 0, "userSid": 8, "appContainerName": 16, "displayName": 24,
        "description": 32, "capabilities": 40, "binaries": 56, "workingDirectory": 72, "packageFullName": 80}
    assert C.sizeof(m._Capabilities) == C.sizeof(m._Binaries) == C.sizeof(m._SidAttributes) == 16
    assert m._Capabilities.capabilities.offset == m._Binaries.binaries.offset == m._SidAttributes.Attributes.offset == 8
    assert C.sizeof(m._MemoryInfo) == 48
    assert [getattr(m._MemoryInfo, n).offset for n, _ in m._MemoryInfo._fields_] == [0, 8, 16, 20, 24, 32, 36, 40]


@pytest.mark.parametrize("flags", [0, 1])
def test_exact_copy_utf16_sid_high_bits_caps_and_unparsed_pointer_fields(flags):
    sdk = Fixture()
    enum_result, free_result = sdk.enum_status, sdk.free_status = Once(0), Once(0)
    owner = sdk.owner(flags=flags)
    result = owner.read()
    obs, row = result.observation, result.observation.rows[0]
    assert row.appcontainer_sid == APP and row.user_sid == USER
    assert row.name == "Example.应用😀" and row.name_utf16le == row.name.encode("utf-16-le")
    assert row.capabilities == (m.Capability(CAP, 0x80000004),)
    assert row.binaries == row.display_description_working_directory_package == "NOT_PARSED"
    assert row.binary_count == 2 and not any(c[0] == "query" and c[1] == 1 for c in sdk.calls)
    assert obs.state == "ROWS_OBSERVED_UNVERIFIED" and obs.source == "SOFTWARE_ONLY"
    assert obs.completeness == obs.visibility == obs.absence == "UNVERIFIED"
    assert obs.generation == "UNAVAILABLE" and obs.authority == "DIAGNOSTIC_ONLY"
    assert "FLAG1_IS_NOT_COMPLETENESS_PROOF" in obs.caveats
    assert obs.binary_computation == ("REQUESTED_FLAGS1" if flags else "NOT_REQUESTED_FLAGS0")
    assert sdk.calls.count(("free", sdk.pointer)) == 1 and owner.state == "RELEASED"
    assert enum_result.reads == free_result.reads == 1 and not m.retained_catalog_owners()
    selected = m.select_exact(obs, APP, USER, row.name)
    assert selected.state == "MATCH_OBSERVED_UNVERIFIED" and selected.authority == "DIAGNOSTIC_ONLY"
    assert m.select_exact(obs, APP, sid(999), row.name).state == "NOT_OBSERVED"
    assert m.select_exact(obs, APP, USER, row.name.upper()).state == "NOT_OBSERVED"
    before = list(sdk.calls)
    m.recover_catalog_owner(owner)
    assert sdk.calls == before and free_result.reads == 1


@pytest.mark.parametrize("pointer", [None, "valid"])
@pytest.mark.parametrize("flags", [0, 1])
def test_empty_catalog_is_not_absence_or_generation_proof(pointer, flags):
    sdk = Fixture(0)
    if pointer is None: sdk.pointer = None
    owner = sdk.owner(flags=flags)
    obs = owner.read().observation
    assert obs.rows == () and obs.state == "NOT_OBSERVED" and obs.absence == "UNVERIFIED"
    assert obs.generation == "UNAVAILABLE" and obs.completeness == "UNVERIFIED"
    assert len([c for c in sdk.calls if c[0] == "free"]) == (pointer is not None)
    assert m.select_exact(obs, APP, USER, "missing").state == "NOT_OBSERVED"


@pytest.mark.parametrize("status", [5, True, False, "0", 0.0, None, -1, 1 << 32, object()])
def test_enum_unknown_or_error_with_output_retains_exact_owner_without_free_or_requery(status):
    sdk = Fixture();sdk.enum_status = status
    owner = sdk.owner()
    with pytest.raises(m.CatalogError) as caught: owner.read()
    assert caught.value.owner is owner and owner in m.retained_catalog_owners()
    assert owner.state == "UNKNOWN" and owner.enum_call.raw is status
    assert owner.original_output_slot is sdk.slot and owner.original_count_slot is sdk.count_slot
    before = list(sdk.calls)
    with pytest.raises(m.CatalogError): owner.read()
    with pytest.raises(m.CatalogError): m.recover_catalog_owner(owner)
    assert sdk.calls == before == [("enum", 0)]


def test_documented_enum_failure_without_allocation_has_no_free_authority():
    sdk = Fixture(0);sdk.pointer, sdk.enum_status = None, 8
    owner = sdk.owner()
    with pytest.raises(m.CatalogError) as caught: owner.read()
    assert caught.value.native_error == 8 and owner.state == "RELEASED"
    assert sdk.calls == [("enum", 0)] and not m.retained_catalog_owners()


def test_enum_slots_written_then_raise_are_rooted_before_call_and_survive_gc():
    sdk = Fixture();owner = sdk.owner()
    def written(): raise OSError("SOFTWARE SDK wrote both outputs then lost return")
    sdk.enum_hook = written
    with pytest.raises(m.CatalogError): owner.read()
    root, api = weakref.ref(owner), weakref.ref(owner.api)
    address = sdk.pointer
    del owner, sdk;gc.collect()
    assert root() is not None and api() is root().api
    assert root().slot.value == address and root().enum_call.state == "UNKNOWN"
    assert root().enum_call.outputs[0] is root().count and root().enum_call.outputs[1] is root().slot
    assert root().api.keepalive and not any(c[0] == "free" for c in root().api.keepalive[0].calls)


@pytest.mark.parametrize("bad", ["null", "unaligned", "unmapped", "overflow", "guard", "noaccess", "reserved", "short_query", "malformed_query", "query_raise"])
def test_invalid_original_base_or_unknown_query_never_grants_free_authority(bad):
    sdk = Fixture()
    if bad == "null": sdk.pointer = None
    elif bad == "unaligned": sdk.pointer += 1
    elif bad == "unmapped": sdk.pointer = 8
    elif bad == "overflow": sdk.pointer = (1 << 64) - 8
    elif bad == "guard": sdk.regions[0][3] = 0x104
    elif bad == "noaccess": sdk.regions[0][3] = 1
    elif bad == "reserved": sdk.regions[0][2] = 0x2000
    elif bad == "short_query": sdk.query_result = 47
    elif bad == "malformed_query": sdk.query_result = True
    else:
        def lost(info): raise OSError("SOFTWARE VirtualQuery output then exception")
        sdk.query_hook = lost
    owner = sdk.owner()
    with pytest.raises(m.CatalogError): owner.read()
    assert owner.state == "UNKNOWN" and owner in m.retained_catalog_owners()
    assert not any(c[0] == "free" for c in sdk.calls)
    before = list(sdk.calls)
    with pytest.raises(m.CatalogError): owner.release()
    assert sdk.calls == before


@pytest.mark.parametrize("bad", ["rows", "cap_count", "cap_total", "binary_count", "sid_revision", "sid_length", "sid_pointer", "cap_pointer", "cap_sid", "name_pointer", "utf16", "name_bound", "copy_budget", "query_budget"])
def test_known_success_malformed_rows_clean_exact_valid_original_allocation_once(bad):
    sdk, bounds = Fixture(), m.Bounds()
    row = sdk.array[0]
    if bad == "rows": sdk.count = 0xFFFFFFFF
    elif bad == "cap_count": row.capabilities.count = 0xFFFFFFFF
    elif bad == "cap_total": bounds = m.Bounds(capabilities_total=1);row.capabilities.count = 2
    elif bad == "binary_count": row.binaries.count = 0xFFFFFFFF
    elif bad == "sid_revision": row.appContainerSid = sdk.buffer(b"\x02\x00" + b"\0" * 6)
    elif bad == "sid_length": row.appContainerSid = sdk.buffer(b"\x01\x10" + b"\0" * 6)
    elif bad == "sid_pointer": row.appContainerSid = 8
    elif bad == "cap_pointer": row.capabilities.capabilities = 8
    elif bad == "cap_sid": C.cast(row.capabilities.capabilities, C.POINTER(m._SidAttributes))[0].Sid = 8
    elif bad == "name_pointer": row.appContainerName = 9
    elif bad == "utf16": row.appContainerName = sdk.buffer(b"\x00\xd8\0\0")
    elif bad == "name_bound": bounds = m.Bounds(name_utf16_units=1)
    elif bad == "copy_budget": bounds = m.Bounds(copied_bytes=88)
    else: bounds = m.Bounds(span_queries=2)
    owner = sdk.owner(bounds=bounds)
    with pytest.raises(m.CatalogError) as caught: owner.read()
    assert caught.value.owner is owner and owner.state == "RELEASED"
    assert len([c for c in sdk.calls if c[0] == "free"]) == 1
    before = list(sdk.calls);m.recover_catalog_owner(owner)
    assert sdk.calls == before


@pytest.mark.parametrize("free_status", [5, True, False, None, "0", 0.0, -1, 1 << 32, object()])
def test_free_is_strict_dword_zero_and_unknown_never_retries(free_status):
    sdk = Fixture();sdk.free_status = free_status
    owner = sdk.owner()
    with pytest.raises(m.CatalogError): owner.read()
    assert owner.state == "UNKNOWN" and owner.free_call.raw is free_status
    before = list(sdk.calls)
    with pytest.raises(m.CatalogError): owner.release()
    with pytest.raises(m.CatalogError): owner.read()
    assert sdk.calls == before and len([c for c in sdk.calls if c[0] == "free"]) == 1


def test_free_effect_then_null_slot_and_exception_is_sticky_even_if_slot_restored():
    sdk = Fixture();owner = sdk.owner()
    def lost():
        sdk.slot.value = None
        raise OSError("SOFTWARE free effect then null output slot then lost return")
    sdk.free_hook = lost
    with pytest.raises(m.CatalogError): owner.read()
    sdk.slot.value = sdk.pointer
    before = list(sdk.calls)
    with pytest.raises(m.CatalogError): owner.release()
    assert sdk.calls == before and owner.free_call.state == "UNKNOWN"


def ack_pending(monkeypatch):
    sdk = Fixture();sdk.free_status = Once(0)
    owner = sdk.owner();fired = []
    def checkpoint(event):
        if not fired:
            fired.append(event)
            raise OSError("SOFTWARE bookkeeping after confirmed original Free0")
    monkeypatch.setattr(owner, "_checkpoint", checkpoint)
    with pytest.raises(m.CatalogError) as caught: owner.read()
    assert caught.value.code == "FREE_ACK_PENDING"
    return owner, sdk


def test_confirmed_free0_metadata_fault_ack_never_consumes_raw_or_calls_sdk_again(monkeypatch):
    owner, sdk = ack_pending(monkeypatch)
    assert owner.state == "FREE_ACK_PENDING" and owner.free_call.scalar == 0 and sdk.free_status.reads == 1
    before = list(sdk.calls)
    m.recover_catalog_owner(owner)
    assert sdk.calls == before and sdk.free_status.reads == 1 and owner.state == "RELEASED"
    assert not m.retained_catalog_owners()


@pytest.mark.parametrize("drift", ["slot", "count_slot", "pointer", "count", "free_fn", "api", "enum_args", "free_args", "free_call", "cache", "owner_copy", "equivalent_free_binding", "libraries"])
def test_original_ack_rejects_changed_owner_slot_pointer_function_call_or_args(drift, monkeypatch):
    owner, sdk = ack_pending(monkeypatch)
    if drift == "slot": owner.slot = m.PTR(sdk.pointer)
    elif drift == "count_slot": owner.count = m.DWORD(sdk.count)
    elif drift == "pointer": owner.slot.value += 8
    elif drift == "count": owner.count.value += 1
    elif drift == "free_fn": owner.api.free = lambda arg: 0
    elif drift == "api": owner.api = copy.copy(owner.api)
    elif drift == "enum_args": owner.enum_call.args = (1, owner.args[1], owner.args[2])
    elif drift == "free_args": owner.free_call.args = (m.PTR(sdk.pointer),)
    elif drift == "free_call": owner.free_call = copy.copy(owner.free_call)
    elif drift == "cache": owner.free_call.scalar = 1
    elif drift == "equivalent_free_binding": owner.api.free = sdk.free
    elif drift == "libraries": owner.api.keepalive = ()
    else: owner = copy.copy(owner)
    before = list(sdk.calls)
    with pytest.raises(m.CatalogError): m.recover_catalog_owner(owner)
    assert sdk.calls == before and sdk.free_status.reads == 1


def test_committed_readable_sid_crosses_regions_with_each_exact_span_checked():
    sdk = Fixture();pointer = sdk.array[0].appContainerSid
    region = next(r for r in sdk.regions if r[0] == pointer)
    sdk.regions.remove(region)
    sdk.regions.extend([[pointer, pointer + 4, 0x1000, 2], [pointer + 4, region[1], 0x1000, 4]])
    result = sdk.owner().read()
    assert result.observation.rows[0].appcontainer_sid == APP
    assert any(c[0] == "query" and c[1] == pointer + 4 for c in sdk.calls)


@pytest.mark.parametrize("first_readable", [16, 88])
def test_array_extent_is_checked_before_parsing_and_bad_base_is_retained(first_readable):
    sdk = Fixture(2)
    start, end, _, _ = sdk.regions.pop(0)
    sdk.regions.extend([[start, start + first_readable, 0x1000, 4], [start + first_readable, end, 0x1000, 1]])
    owner = sdk.owner()
    with pytest.raises(m.CatalogError): owner.read()
    assert owner.state == ("UNKNOWN" if first_readable == 16 else "RELEASED")
    assert len([c for c in sdk.calls if c[0] == "free"]) == (first_readable == 88)


def test_maximum_sid_bytes_are_preserved_without_host_integer_or_byte_order_conversion():
    sdk = Fixture();raw = sid(*([0xFFFFFFFF] * 15), authority=0xFEDCBA987654)
    sdk.array[0].appContainerSid = sdk.buffer(raw)
    assert sdk.owner().read().observation.rows[0].appcontainer_sid == raw and len(raw) == 68


def test_sid_header_change_between_bounded_reads_is_rejected_and_original_array_freed():
    sdk = Fixture();pointer = sdk.array[0].appContainerSid;seen = []
    def changing(info):
        if info.BaseAddress == pointer:
            seen.append(pointer)
            if len(seen) == 2: C.memmove(pointer, b"\x01\x02", 2)
    sdk.query_hook = changing;owner = sdk.owner()
    with pytest.raises(m.CatalogError) as caught: owner.read()
    assert caught.value.code == "SID_DRIFT" and owner.state == "RELEASED"
    assert len([c for c in sdk.calls if c[0] == "free"]) == 1


def test_empty_output_bookkeeping_ack_needs_no_sdk_query_or_free(monkeypatch):
    sdk = Fixture(0);sdk.pointer = None;owner = sdk.owner();fired = []
    def checkpoint(event):
        if not fired:
            fired.append(event)
            raise OSError("SOFTWARE empty-output local metadata fault")
    monkeypatch.setattr(owner, "_checkpoint", checkpoint)
    with pytest.raises(m.CatalogError) as caught: owner.read()
    assert caught.value.code == "NO_ALLOCATION_ACK_PENDING"
    before = list(sdk.calls);m.recover_catalog_owner(owner)
    assert sdk.calls == before == [("enum", 0)] and owner.state == "RELEASED"


def test_output_slot_drift_during_parse_never_frees_or_adopts_replacement():
    sdk = Fixture();owner = sdk.owner()
    def drift(info): sdk.slot.value = 8
    sdk.query_hook = drift
    with pytest.raises(m.CatalogError): owner.read()
    assert owner.state == "UNKNOWN" and not any(c[0] == "free" for c in sdk.calls)
    before = list(sdk.calls)
    sdk.slot.value = sdk.pointer
    with pytest.raises(m.CatalogError): owner.release()
    assert sdk.calls == before


def test_released_original_is_not_reenumerated_and_keeps_confirmed_release_state():
    sdk = Fixture();owner = sdk.owner();owner.read();before = list(sdk.calls)
    with pytest.raises(m.CatalogError): owner.read()
    assert sdk.calls == before and owner.state == "RELEASED"


def test_zero_capability_count_never_dereferences_unused_pointer():
    sdk = Fixture();sdk.array[0].capabilities.count = 0;sdk.array[0].capabilities.capabilities = 1
    obs = sdk.owner().read().observation
    assert obs.rows[0].capabilities == () and not any(c[0] == "query" and c[1] == 1 for c in sdk.calls)


def test_unknown_allocation_does_not_block_an_independent_catalog_resource():
    bad = Fixture();bad.enum_status = 5;unknown = bad.owner()
    with pytest.raises(m.CatalogError): unknown.read()
    other = Fixture();result = other.owner().read()
    assert result.owner.state == "RELEASED" and m.retained_catalog_owners() == (unknown,)
    assert len([c for c in other.calls if c[0] == "enum"]) == len([c for c in other.calls if c[0] == "free"]) == 1


@pytest.mark.parametrize("kind", ["duplicate", "conflicting_name", "duplicate_capability"])
def test_duplicate_or_conflicting_data_is_ambiguous_without_discarding_rows(kind):
    sdk = Fixture(2 if kind != "duplicate_capability" else 1)
    if kind == "conflicting_name": sdk.array[1].appContainerName = sdk.buffer("other\0".encode("utf-16-le"))
    if kind == "duplicate_capability":
        caps = (m._SidAttributes * 2)()
        for c in caps: c.Sid = sdk.buffer(CAP)
        sdk.array[0].capabilities.count, sdk.array[0].capabilities.capabilities = 2, sdk.hold(caps)
    obs = sdk.owner().read().observation
    assert obs.state == "AMBIGUOUS" and obs.issues and len(obs.rows) == sdk.count
    assert m.select_exact(obs, APP, USER, obs.rows[0].name).state == "AMBIGUOUS"


def test_fabricated_dataclass_or_json_never_becomes_absence_restore_generation_or_launch_authority():
    obs = m.Observation((), 1, "NOT_OBSERVED", (), "CALLER")
    result = m.select_exact(obs, APP, USER, "not-present")
    assert result.state == "NOT_OBSERVED" and result.absence == "UNVERIFIED" and result.generation == "UNAVAILABLE"
    assert result.authority == "DIAGNOSTIC_ONLY"
    with pytest.raises(m.CatalogError): m.select_exact(vars(obs), APP, USER, "missing")
    with pytest.raises(m.CatalogError): m.recover_catalog_owner({"pointer": 123, "success": True})
    assert not any(name in vars(m) for name in ("restore", "delete_profile", "launch", "resume", "qualify_absence"))


@pytest.mark.parametrize("flags", [-1, 2, 0xFFFFFFFF, True, False, "0", None])
def test_invalid_flags_never_load_or_dispatch_sdk(flags, monkeypatch):
    monkeypatch.setattr(m, "_Api", lambda: pytest.fail("native API construction forbidden"))
    with pytest.raises(m.CatalogError): m.observe_catalog(flags=flags)


@pytest.mark.parametrize("field", list(m.Bounds.__dataclass_fields__))
def test_bounds_cannot_be_unbounded_bool_zero_or_over_ceiling(field):
    for value in (0, -1, True, "1", 1 << 64):
        with pytest.raises(m.CatalogError): m.Bounds(**{field: value})


def test_native_entry_has_no_provider_json_or_callable_injection_and_no_side_effect_exports(monkeypatch):
    sdk, names = Fixture(), []
    class Export:
        def __init__(self, fn): self.fn = fn
        def __call__(self, *args): return self.fn(*args)
    enum, free, query = Export(sdk.enum), Export(sdk.free), Export(sdk.query)
    class Library:
        def __init__(self, mapping): self.mapping = mapping
        def __getattr__(self, name):
            names.append(name)
            return self.mapping[name]
    loaded = []
    def loader(name, **kwargs):
        loaded.append((name, kwargs))
        return Library({"NetworkIsolationEnumAppContainers": enum, "NetworkIsolationFreeAppContainers": free}
                       if name == "Firewallapi.dll" else {"VirtualQuery": query})
    with monkeypatch.context() as patch:
        patch.setattr(m.sys, "platform", "win32")
        patch.setattr(m.C, "WinDLL", loader, raising=False)
        result = m.observe_catalog()
    assert result.owner.state == "RELEASED"  # software mock of native constructor, not native evidence
    assert names == ["NetworkIsolationEnumAppContainers", "NetworkIsolationFreeAppContainers", "VirtualQuery"]
    assert loaded == [("Firewallapi.dll", {"use_last_error": True, "winmode": 0x800}),
                      ("kernel32.dll", {"use_last_error": True, "winmode": 0x800})]
    assert enum.restype is free.restype is m.DWORD
    assert enum.argtypes == [m.DWORD, C.POINTER(m.DWORD), C.POINTER(m.PTR)]
    assert free.argtypes == [m.PTR] and query.restype is m.SIZE_T
    assert query.argtypes == [m.PTR, C.POINTER(m._MemoryInfo), m.SIZE_T]
    assert list(inspect.signature(m.observe_catalog).parameters) == ["flags", "bounds"]
    with pytest.raises(TypeError): m.observe_catalog(api=sdk)
