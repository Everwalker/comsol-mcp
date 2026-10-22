from __future__ import annotations

import ctypes as C
from dataclasses import replace
import hashlib
import ipaddress

import pytest

from comsol_mcp import _windows_t037_events as events


NOW = 133_801_234_567_890_000
APP_ID = b"software-fixture-app-id"
USER_SID_RAW = b"\x01\x01\x00\x00\x00\x00\x00\x05" + (1001).to_bytes(4, "little")
PACKAGE_SID_RAW = b"\x01\x01\x00\x00\x00\x00\x00\x0f" + (2001).to_bytes(4, "little")


def _sid_storage(raw: bytes):
    return C.create_string_buffer(raw, len(raw))


def _handle_value(value):
    return getattr(value, "value", value)


class _FixtureApi:
    """Explicit software-only fake; never evidence of native WFP behavior."""
    sdk_metadata = (("fixture", "SOFTWARE_ONLY"),)

    def __init__(self, net_events=(), *, collection=1, status=None):
        self.net_events = list(net_events)
        self.collection = collection
        self.status = dict(status or {})
        self.calls = []
        self.allocations = []
        self.event_arrays = []
        self._sid_data = {}
        self.fail_page_free_once = False
        self.free_failure_after_clear = False
        self.fail_enum_destroy = 0
        self.fail_engine_close = 0
        self.raise_after_output = set()
        self.replace_closers_after_output = {}
        self.drift_slots_after_output = {}
        self.output_slots = {}
        self.enum_template = None
        self.requested = []

    @staticmethod
    def _put_pointer(out, pointer_type, value):
        C.cast(out, C.POINTER(pointer_type))[0] = value

    def _remember_output(self, name, out):
        self.output_slots[name] = C.cast(out, C.POINTER(C.c_void_p))

    def _after_output(self, name, status_key):
        for slot_name, value in self.drift_slots_after_output.get(name, {}).items():
            self.output_slots[slot_name].contents.value = value
        for closer_name in self.replace_closers_after_output.get(name, ()):
            def replacement(*args, _name=closer_name):
                self.calls.append(("replacement_closer", _name))
                return 0
            setattr(self, closer_name, replacement)
        if name in self.raise_after_output:
            raise OSError(f"fixture {name} raised after writing its output")
        return self.status.get(status_key, 0)

    def last_error(self):
        return 1234

    def system_time_filetime(self):
        return events._filetime_from_value(NOW)

    def read_sid(self, pointer):
        address = int(pointer)
        first_eight = C.string_at(address, 8)
        count = first_eight[1]
        if count > 15:
            raise ValueError("fixture SID has too many subauthorities")
        raw = C.string_at(address, 8 + 4 * count)
        return events._sid_to_string(raw), raw

    def FwpmGetAppIdFromFileName0(self, path, out):
        self.calls.append("FwpmGetAppIdFromFileName0")
        self._remember_output("appid", out)
        raw = C.create_string_buffer(APP_ID, len(APP_ID))
        blob = events.FWP_BYTE_BLOB(len(APP_ID), C.cast(raw, C.POINTER(C.c_ubyte)))
        ptr = C.pointer(blob)
        self.allocations.extend((raw, blob, ptr))
        self._put_pointer(out, C.POINTER(events.FWP_BYTE_BLOB), ptr)
        return self._after_output("appid", "appid")

    def FwpmEngineOpen0(self, server, auth, identity, session, out):
        self.calls.append("FwpmEngineOpen0")
        self._remember_output("engine", out)
        C.cast(out, C.POINTER(C.c_void_p))[0] = C.c_void_p(0x101)
        return self._after_output("engine", "engine_open")

    def FwpmEngineGetOption0(self, engine, option, out):
        self.calls.append(("FwpmEngineGetOption0", _handle_value(engine), option))
        self._remember_output("option", out)
        value = events.FWP_VALUE0()
        value.type = 3  # FWP_UINT32
        value.value.uint32 = self.collection
        ptr = C.pointer(value)
        self.allocations.extend((value, ptr))
        self._put_pointer(out, C.POINTER(events.FWP_VALUE0), ptr)
        return self._after_output("option", "get_option")

    def FwpmNetEventCreateEnumHandle0(self, engine, template, out):
        self.calls.append("FwpmNetEventCreateEnumHandle0")
        self._remember_output("enum", out)
        t = C.cast(template, C.POINTER(events.FWPM_NET_EVENT_ENUM_TEMPLATE0)).contents
        self.enum_template = {
            "start": events._filetime_value(t.startTime),
            "end": events._filetime_value(t.endTime),
            "count": int(t.numFilterConditions),
            "conditions": [],
        }
        for row in t.filterCondition[:t.numFilterConditions]:
            key = (
                int(row.fieldKey.Data1), int(row.fieldKey.Data2), int(row.fieldKey.Data3),
                bytes(row.fieldKey.Data4),
            )
            condition = {"key": key, "kind": int(row.conditionValue.type),
                         "match": int(row.matchType)}
            if condition["kind"] == events.FWP_UINT8:
                condition["value"] = int(row.conditionValue.value.uint8)
            elif condition["kind"] == events.FWP_BYTE_BLOB_TYPE:
                blob = C.cast(row.conditionValue.value.byteBlob,
                              C.POINTER(events.FWP_BYTE_BLOB)).contents
                condition["value"] = C.string_at(blob.data, blob.size)
            elif condition["kind"] == events.FWP_V4_ADDR_MASK:
                mask = C.cast(row.conditionValue.value.v4AddrMask,
                              C.POINTER(events.FWP_V4_ADDR_AND_MASK)).contents
                condition["value"] = (int(mask.addr), int(mask.mask))
            self.enum_template["conditions"].append(condition)
        C.cast(out, C.POINTER(C.c_void_p))[0] = C.c_void_p(0x202)
        return self._after_output("enum", "create_enum")

    def FwpmNetEventEnum2(self, engine, enum_handle, requested, out_array, out_count):
        self.calls.append("FwpmNetEventEnum2")
        self._remember_output("array", out_array)
        self.output_slots["count"] = C.cast(out_count, C.POINTER(C.c_uint32))
        self.requested.append(int(requested))
        begin = sum(self.requested[:-1])
        selected = self.net_events[begin:begin + requested]
        C.cast(out_count, C.POINTER(C.c_uint32)).contents.value = len(selected)
        if selected:
            array = (C.POINTER(events.FWPM_NET_EVENT2) * len(selected))(
                *[C.pointer(row) for row in selected]
            )
            self.event_arrays.append(array)
            self.allocations.append(array)
            ptr = C.cast(array, C.POINTER(C.POINTER(events.FWPM_NET_EVENT2)))
            self._put_pointer(
                out_array,
                C.POINTER(C.POINTER(events.FWPM_NET_EVENT2)),
                ptr,
            )
        return self._after_output("array", "enum")

    def FwpmFreeMemory0(self, out):
        slot = C.cast(out, C.POINTER(C.c_void_p))
        value = slot.contents.value
        self.calls.append(("FwpmFreeMemory0", value))
        if self.fail_page_free_once and self.event_arrays:
            page_address = C.cast(self.event_arrays[0], C.c_void_p).value
            if value == page_address:
                self.fail_page_free_once = False
                if self.free_failure_after_clear:
                    slot.contents.value = None
                raise OSError("fixture FreeMemory failure")
        slot.contents.value = None

    def FwpmNetEventDestroyEnumHandle0(self, engine, enum_handle):
        self.calls.append(("FwpmNetEventDestroyEnumHandle0", _handle_value(engine), _handle_value(enum_handle)))
        if self.fail_enum_destroy:
            self.fail_enum_destroy -= 1
            return 5
        return self.status.get("destroy_enum", 0)

    def FwpmEngineClose0(self, engine):
        self.calls.append(("FwpmEngineClose0", _handle_value(engine)))
        if self.fail_engine_close:
            self.fail_engine_close -= 1
            return 5
        return self.status.get("close_engine", 0)


class _UnconvertibleDword:
    @property
    def value(self):
        raise RuntimeError("fixture lost SDK DWORD conversion after release")


class _OnceDword:
    def __init__(self, first_value=0):
        self.first_value = first_value
        self.reads = 0

    @property
    def value(self):
        self.reads += 1
        if self.reads == 1:
            return self.first_value
        raise ValueError("fixture SDK result wrapper was consumed more than once")


class _FailOneRegistryPop(dict):
    def __init__(self, entries, target_label):
        super().__init__(entries)
        self.target_label = target_label
        self.failed = False

    def pop(self, key, *default):
        entry = self.get(key)
        if not self.failed and entry is not None and entry.label == self.target_label:
            self.failed = True
            raise RuntimeError("injected registry bookkeeping failure after native release")
        return super().pop(key, *default)


def _make_event(*, timestamp=NOW - 1_000_000, app_id=APP_ID,
                package_sid=PACKAGE_SID_RAW, user_sid=USER_SID_RAW,
                event_type=events.FWPM_NET_EVENT_TYPE_CLASSIFY_DROP,
                flags=None, local=0x7F000001, remote=0x7F000001,
                local_port=41001, remote_port=45678, filter_id=7001,
                layer_id=48, direction=events.FWP_DIRECTION_OUT,
                loopback=1, ip_version=events.FWP_IP_VERSION_V4):
    event = events.FWPM_NET_EVENT2()
    event.header.timeStamp = events._filetime_from_value(timestamp)
    if flags is None:
        flags = (
            events.FWPM_NET_EVENT_FLAG_IP_PROTOCOL_SET |
            events.FWPM_NET_EVENT_FLAG_LOCAL_ADDR_SET |
            events.FWPM_NET_EVENT_FLAG_REMOTE_ADDR_SET |
            events.FWPM_NET_EVENT_FLAG_LOCAL_PORT_SET |
            events.FWPM_NET_EVENT_FLAG_REMOTE_PORT_SET |
            events.FWPM_NET_EVENT_FLAG_APP_ID_SET |
            events.FWPM_NET_EVENT_FLAG_USER_ID_SET |
            events.FWPM_NET_EVENT_FLAG_IP_VERSION_SET |
            events.FWPM_NET_EVENT_FLAG_PACKAGE_ID_SET
        )
    event.header.flags = flags
    event.header.ipVersion = ip_version
    event.header.ipProtocol = 6
    event.header.localAddress.localAddrV4 = local
    event.header.remoteAddress.remoteAddrV4 = remote
    event.header.localPort = local_port
    event.header.remotePort = remote_port
    app_storage = C.create_string_buffer(app_id, max(1, len(app_id)))
    event.header.appId = events.FWP_BYTE_BLOB(
        len(app_id), C.cast(app_storage, C.POINTER(C.c_ubyte))
    )
    user_storage = _sid_storage(user_sid)
    package_storage = _sid_storage(package_sid)
    event.header.userId = C.addressof(user_storage)
    event.header.packageSid = C.addressof(package_storage)
    drop = events.FWPM_NET_EVENT_CLASSIFY_DROP2()
    drop.filterId = filter_id
    drop.layerId = layer_id
    drop.msFwpDirection = direction
    drop.isLoopback = loopback
    event.type = event_type
    event.eventData.classifyDrop = C.cast(C.pointer(drop), C.c_void_p).value
    event._fixture_keepalive = (app_storage, user_storage, package_storage, drop)
    return event


def _read_fixture_event(event, api=None):
    fake = api or _FixtureApi()
    return events._decode_event(fake, C.pointer(event), NOW - 5 * 10_000_000, NOW)


def _run_fixture(api):
    return events.observe_recent_classify_drop_events(
        r"C:\Users\Everwalker\Python\python.exe", window_seconds=5, _api=api
    )


def test_real_sdk_struct_layout_and_member_offsets_are_x64_windows_abi():
    assert C.sizeof(events.FILETIME) == 8
    assert C.sizeof(events.FWP_BYTE_BLOB) == 16
    assert C.sizeof(events.FWP_VALUE0) == 16
    assert C.sizeof(events.FWP_CONDITION_VALUE0) == 16
    assert C.sizeof(events.FWPM_FILTER_CONDITION0) == 40
    assert C.sizeof(events.FWPM_NET_EVENT_ENUM_TEMPLATE0) == 32
    assert C.sizeof(events.FWPM_NET_EVENT_HEADER2) == 104
    assert C.sizeof(events.FWPM_NET_EVENT_CLASSIFY_DROP2) == 56
    assert C.sizeof(events.FWPM_NET_EVENT2) == 120
    assert {name: getattr(events.FWPM_NET_EVENT_HEADER2, name).offset for name in (
        "timeStamp", "flags", "ipVersion", "ipProtocol", "localAddress",
        "remoteAddress", "localPort", "remotePort", "scopeId", "appId",
        "userId", "addressFamily", "packageSid",
    )} == {
        "timeStamp": 0, "flags": 8, "ipVersion": 12, "ipProtocol": 16,
        "localAddress": 20, "remoteAddress": 36, "localPort": 52,
        "remotePort": 54, "scopeId": 56, "appId": 64, "userId": 80,
        "addressFamily": 88, "packageSid": 96,
    }
    assert {name: getattr(events.FWPM_NET_EVENT_CLASSIFY_DROP2, name).offset for name in (
        "filterId", "layerId", "reauthReason", "msFwpDirection", "isLoopback",
        "vSwitchId", "vSwitchSourcePort", "vSwitchDestinationPort",
    )} == {
        "filterId": 0, "layerId": 8, "reauthReason": 12,
        "msFwpDirection": 24, "isLoopback": 28, "vSwitchId": 32,
        "vSwitchSourcePort": 48, "vSwitchDestinationPort": 52,
    }
    assert events.FWPM_NET_EVENT_TYPE_CLASSIFY_DROP == 3
    assert events.FWPM_ENGINE_COLLECT_NET_EVENTS == 0


def test_enum_template_has_strict_time_appid_tcp_and_two_host_order_loopback_selectors():
    start, end = NOW - 50_000_000, NOW
    template, rows, local, remote, app_storage, app_blob = events._build_enum_template(start, end, APP_ID)
    assert events._filetime_value(template.startTime) == start
    assert events._filetime_value(template.endTime) == end
    assert template.numFilterConditions == 4
    assert C.string_at(app_blob.data, app_blob.size) == APP_ID
    assert (local.addr, local.mask) == (0x7F000001, 0xFFFFFFFF)
    assert (remote.addr, remote.mask) == (0x7F000001, 0xFFFFFFFF)
    assert [rows[i].conditionValue.type for i in range(4)] == [
        events.FWP_BYTE_BLOB_TYPE, events.FWP_UINT8,
        events.FWP_V4_ADDR_MASK, events.FWP_V4_ADDR_MASK,
    ]
    assert rows[1].conditionValue.value.uint8 == 6
    assert rows[2].conditionValue.value.v4AddrMask == C.cast(C.pointer(local), C.c_void_p).value
    assert rows[3].conditionValue.value.v4AddrMask == C.cast(C.pointer(remote), C.c_void_p).value
    assert app_storage.raw[:len(APP_ID)] == APP_ID


@pytest.mark.parametrize("path", ["relative.exe", r"\\host\share\x.exe", "C:\\x\x00.exe"])
def test_image_path_requires_bounded_absolute_local_drive_path(path):
    with pytest.raises(ValueError):
        events._canonical_image_path(path)


def test_event_decoder_copies_actual_blob_sid_bytes_and_all_classify_fields():
    event = _make_event()
    api = _FixtureApi()
    api._sid_data[int(event.header.userId)] = USER_SID_RAW
    api._sid_data[int(event.header.packageSid)] = PACKAGE_SID_RAW
    decoded = events._decode_event(api, C.pointer(event), NOW - 5 * 10_000_000, NOW)
    assert decoded.event_reasons == ()
    assert decoded.local_address == "127.0.0.1" and decoded.remote_address == "127.0.0.1"
    assert decoded.local_address_sdk_u32 == decoded.remote_address_sdk_u32 == 0x7F000001
    assert decoded.local_port == 41001 and decoded.remote_port == 45678
    assert decoded.app_id_bytes_hex == APP_ID.hex()
    assert decoded.app_id_sha256 == hashlib.sha256(APP_ID).hexdigest()
    assert decoded.user_sid == "S-1-5-1001" and decoded.user_sid_bytes_hex == USER_SID_RAW.hex()
    assert decoded.package_sid == "S-1-15-2001" and decoded.package_sid_bytes_hex == PACKAGE_SID_RAW.hex()
    assert decoded.filter_id == 7001 and decoded.layer_id == 48
    assert decoded.direction == "OUT" and decoded.is_loopback is True


def test_unset_header_flags_never_dereference_appid_or_sid_pointers():
    event = _make_event(flags=events.FWPM_NET_EVENT_FLAG_IP_VERSION_SET)
    event.header.appId.size = 500
    event.header.appId.data = C.cast(C.c_void_p(1), C.POINTER(C.c_ubyte))
    event.header.userId = 1
    event.header.packageSid = 1

    class NoSidReads:
        def read_sid(self, _pointer):
            raise AssertionError("unset SID pointer was dereferenced")

    decoded = events._decode_event(NoSidReads(), C.pointer(event), NOW - 5 * 10_000_000, NOW)
    assert decoded.app_id_bytes_hex is None and decoded.user_sid is None and decoded.package_sid is None
    assert "MISSING_FLAG_APP_ID" in decoded.event_reasons
    assert "MISSING_FLAG_USER_ID" in decoded.event_reasons
    assert "MISSING_FLAG_PACKAGE_ID" in decoded.event_reasons


def test_set_blob_flag_checks_size_before_reading_pointer_and_bad_sid_is_unverified():
    event = _make_event()
    event.header.appId.size = events.MAX_APP_ID_BYTES + 1
    event.header.appId.data = C.cast(C.c_void_p(1), C.POINTER(C.c_ubyte))
    api = _FixtureApi()
    api.read_sid = lambda _pointer: ("S-1-5-1", b"x" * (events.MAX_SID_BYTES + 1))
    decoded = events._decode_event(api, C.pointer(event), NOW - 5 * 10_000_000, NOW)
    assert "APP_ID_SIZE_OUT_OF_BOUNDS" in decoded.event_reasons
    assert any(reason.startswith("USER_SID_INVALID") for reason in decoded.event_reasons)
    assert any(reason.startswith("PACKAGE_SID_INVALID") for reason in decoded.event_reasons)


def test_unknown_event_type_does_not_dereference_union_pointer():
    event = _make_event(event_type=2)
    event.eventData.classifyDrop = 1
    decoded = events._decode_event(_FixtureApi(), C.pointer(event), NOW - 5 * 10_000_000, NOW)
    assert decoded.filter_id is None and decoded.layer_id is None
    assert decoded.event_reasons == ("UNSUPPORTED_NET_EVENT_TYPE",)


def test_ipv6_union_is_read_only_when_both_address_and_version_flags_are_set():
    event = _make_event(ip_version=events.FWP_IP_VERSION_V6)
    event.header.localAddress.localAddrV6.byteArray16[:] = bytes.fromhex("20010db8000000000000000000000001")
    event.header.remoteAddress.remoteAddrV6.byteArray16[:] = bytes.fromhex("20010db8000000000000000000000002")
    decoded = _read_fixture_event(event)
    assert decoded.local_address == "2001:db8::1"
    assert decoded.remote_address == "2001:db8::2"


def test_paged_read_frees_every_original_allocation_and_reports_zero_as_not_observed():
    api = _FixtureApi()
    result = _run_fixture(api)
    assert result.state == "UNVERIFIED"
    assert result.diagnostic_state == "NOT_OBSERVED"
    assert result.collection_enabled is True and result.exhausted is True
    assert result.records_returned == 0 and result.events == ()
    assert result.reason_codes == ("SOFTWARE_FIXTURE_API_ONLY",)
    assert api.calls.count("FwpmNetEventEnum2") == 1
    assert [call[0] for call in api.calls if isinstance(call, tuple) and call[0] in {
        "FwpmEngineClose0", "FwpmNetEventDestroyEnumHandle0"
    }] == ["FwpmNetEventDestroyEnumHandle0", "FwpmEngineClose0"]
    assert sum(isinstance(call, tuple) and call[0] == "FwpmFreeMemory0" for call in api.calls) == 2
    assert all(row.status == 0 for row in result.sdk_statuses)


def test_collection_disabled_reads_option_but_never_creates_enum():
    api = _FixtureApi(collection=0)
    result = _run_fixture(api)
    assert result.collection_enabled is False
    assert result.diagnostic_state == "NOT_OBSERVED"
    assert result.reason_codes == ("SOFTWARE_FIXTURE_API_ONLY", "WFP_COLLECTION_DISABLED")
    assert "FwpmNetEventCreateEnumHandle0" not in api.calls
    assert "FwpmNetEventEnum2" not in api.calls


def test_multiple_pages_return_only_classify_drop_fields_and_free_each_array():
    records = [_make_event(timestamp=NOW - 1_000_000 - i) for i in range(10)]
    api = _FixtureApi(records)
    result = _run_fixture(api)
    assert result.pages == 2 and result.records_returned == 10
    assert len(result.events) == 10 and result.exhausted is True and result.truncated is False
    assert all(item.event_reasons == () for item in result.events)
    freed = [call[1] for call in api.calls if isinstance(call, tuple) and call[0] == "FwpmFreeMemory0"]
    assert len(freed) == 4  # AppID, option, and two returned event arrays.
    assert api.requested == [8, 8]


def test_event_limit_uses_one_sentinel_and_never_decodes_more_than_64_rows():
    api = _FixtureApi([_make_event() for _ in range(70)])
    result = _run_fixture(api)
    assert result.pages == events.MAX_PAGES
    assert api.requested == [8] * 8 + [1]
    assert result.records_returned == events.MAX_EVENTS_READ
    assert len(result.events) == events.MAX_EVENTS
    assert result.truncated is True and result.exhausted is False
    assert "EVENT_COUNT_BOUND_REACHED" in result.reason_codes
    assert all(isinstance(call, tuple) for call in api.calls if isinstance(call, tuple))


def test_enum2_access_error_and_disabled_status_are_preserved_without_bypass():
    api = _FixtureApi(status={"enum": events.FWP_E_NET_EVENTS_DISABLED})
    result = _run_fixture(api)
    assert result.collection_enabled is True and result.events == ()
    assert "WFP_NET_EVENTS_DISABLED" in result.reason_codes
    enum_status = [row for row in result.sdk_statuses if row.function == "FwpmNetEventEnum2"]
    assert len(enum_status) == 1 and enum_status[0].status == events.FWP_E_NET_EVENTS_DISABLED
    assert not any("SetOption" in call if isinstance(call, str) else False for call in api.calls)


def test_failed_getoption_allocation_is_freed_and_win32_status_remains_unverified():
    api = _FixtureApi(status={"get_option": 0x80320015})
    result = _run_fixture(api)
    assert result.state == "UNVERIFIED" and result.events == ()
    assert "WFP_SDK_CALL_FAILED" in result.reason_codes
    status = [row for row in result.sdk_statuses if row.function == "FwpmEngineGetOption0"][0]
    assert status.status_hex == "0x80320015" and status.win32_error == 1234
    assert [call[0] for call in api.calls if isinstance(call, tuple) and call[0] in {
        "FwpmEngineClose0", "FwpmNetEventDestroyEnumHandle0"
    }] == ["FwpmEngineClose0"]


def test_enum_creation_error_with_returned_handle_is_owned_and_destroyed_once():
    api = _FixtureApi(status={"create_enum": 0x80320015})
    result = _run_fixture(api)
    assert result.state == "UNVERIFIED" and result.events == ()
    assert "WFP_SDK_CALL_FAILED" in result.reason_codes
    assert [call[0] for call in api.calls if isinstance(call, tuple) and call[0] in {
        "FwpmNetEventDestroyEnumHandle0", "FwpmEngineClose0"
    }] == ["FwpmNetEventDestroyEnumHandle0", "FwpmEngineClose0"]


def test_enum_count_overrun_is_freed_and_rejected_before_pointer_iteration():
    class OverrunApi(_FixtureApi):
        def FwpmNetEventEnum2(self, engine, enum_handle, requested, out_array, out_count):
            self.calls.append("FwpmNetEventEnum2")
            C.cast(out_count, C.POINTER(C.c_uint32)).contents.value = requested + 1
            array = (C.POINTER(events.FWPM_NET_EVENT2) * (requested + 1))(
                *[C.pointer(_make_event()) for _ in range(requested + 1)]
            )
            self.event_arrays.append(array)
            self.allocations.append(array)
            ptr = C.cast(array, C.POINTER(C.POINTER(events.FWPM_NET_EVENT2)))
            self._put_pointer(out_array, C.POINTER(C.POINTER(events.FWPM_NET_EVENT2)), ptr)
            return 0

    api = OverrunApi()
    result = _run_fixture(api)
    assert result.events == () and result.truncated is False
    assert "ENUM_RETURN_COUNT_EXCEEDS_REQUEST" in result.reason_codes
    assert sum(isinstance(call, tuple) and call[0] == "FwpmFreeMemory0" for call in api.calls) == 3


def test_free_after_null_and_exception_retains_unknown_owner_and_never_retries(monkeypatch):
    monkeypatch.setattr(events, "_active_owner", None)
    api = _FixtureApi([_make_event()])
    api.fail_page_free_once = True
    api.free_failure_after_clear = True
    with pytest.raises(events.WfpEventOwnershipError) as raised:
        _run_fixture(api)
    owner = raised.value.owner
    assert owner is events.retained_wfp_event_owner_for_recovery()
    entry = next(item for item in owner.registry.unresolved() if item.label == "wfp_net_event_page")
    page_address = C.cast(api.event_arrays[0], C.c_void_p).value
    assert entry.value == page_address
    assert api.output_slots["array"].contents.value is None
    attempted = sum(isinstance(call, tuple) and call[0] == "FwpmFreeMemory0" for call in api.calls)
    assert attempted == 3  # AppID, option and one page; no retry during cleanup.
    assert not any(isinstance(call, tuple) and call[0] in {
        "FwpmNetEventDestroyEnumHandle0", "FwpmEngineClose0"
    } for call in api.calls)
    # Even restoring the exact original pointer cannot resolve whether the
    # void FreeMemory0 call freed it before throwing; it must remain UNKNOWN.
    api.output_slots["array"].contents.value = page_address
    with pytest.raises(events.NativeResourceError):
        events.recover_wfp_event_owner(owner)
    assert sum(isinstance(call, tuple) and call[0] == "FwpmFreeMemory0" for call in api.calls) == 3
    assert api.output_slots["array"].contents.value == page_address
    assert not any(isinstance(call, tuple) and call[0] in {
        "FwpmNetEventDestroyEnumHandle0", "FwpmEngineClose0"
    } for call in api.calls)
    blocked_api = _FixtureApi()
    with pytest.raises(events.WfpEventOwnershipError):
        _run_fixture(blocked_api)
    assert blocked_api.calls == []
    monkeypatch.setattr(events, "_active_owner", None)


def test_enum_destroy_failure_keeps_engine_open_until_explicit_original_owner_recovery():
    api = _FixtureApi()
    api.fail_enum_destroy = 1
    with pytest.raises(events.WfpEventOwnershipError) as raised:
        _run_fixture(api)
    owner = raised.value.owner
    assert not any(isinstance(call, tuple) and call[0] == "FwpmEngineClose0" for call in api.calls)
    assert sum(isinstance(call, tuple) and call[0] == "FwpmNetEventDestroyEnumHandle0" for call in api.calls) == 1
    events.recover_wfp_event_owner(owner)
    assert sum(isinstance(call, tuple) and call[0] == "FwpmNetEventDestroyEnumHandle0" for call in api.calls) == 2
    assert sum(isinstance(call, tuple) and call[0] == "FwpmEngineClose0" for call in api.calls) == 1


def test_engine_close_failure_retains_original_handle_until_explicit_recovery():
    api = _FixtureApi()
    api.fail_engine_close = 1
    with pytest.raises(events.WfpEventOwnershipError) as raised:
        _run_fixture(api)
    owner = raised.value.owner
    assert events.retained_wfp_event_owner_for_recovery() is owner
    assert sum(isinstance(call, tuple) and call[0] == "FwpmEngineClose0" for call in api.calls) == 1
    events.recover_wfp_event_owner(owner)
    assert sum(isinstance(call, tuple) and call[0] == "FwpmEngineClose0" for call in api.calls) == 2


@pytest.mark.parametrize(("resource", "status_key", "release_name"), [
    ("enum", "destroy_enum", "FwpmNetEventDestroyEnumHandle0"),
    ("engine", "close_engine", "FwpmEngineClose0"),
])
def test_once_dword_is_normalized_once_on_full_observer_registry_path(
    monkeypatch, resource, status_key, release_name,
):
    monkeypatch.setattr(events, "_active_owner", None)
    api = _FixtureApi([])
    once = _OnceDword(0)
    api.status[status_key] = once

    result = _run_fixture(api)

    assert result.state == "UNVERIFIED"
    assert once.reads == 1
    assert sum(isinstance(call, tuple) and call[0] == release_name for call in api.calls) == 1
    assert events.retained_wfp_event_owner_for_recovery() is None


@pytest.mark.parametrize(("target_label", "release_name"), [
    ("wfp_net_event_enum", "FwpmNetEventDestroyEnumHandle0"),
    ("wfp_engine", "FwpmEngineClose0"),
])
def test_registry_bookkeeping_failure_after_native_success_recovers_without_reclose(
    monkeypatch, target_label, release_name,
):
    monkeypatch.setattr(events, "_active_owner", None)
    original_registry_for = events.registry_for
    injected = {}

    def registry_for_with_pop_fault(adapter):
        registry = original_registry_for(adapter)
        if isinstance(adapter, events._WfpEventOwner) and "entries" not in injected:
            entries = _FailOneRegistryPop(registry._entries, target_label)
            registry._entries = entries
            injected["entries"] = entries
        return registry

    monkeypatch.setattr(events, "registry_for", registry_for_with_pop_fault)
    api = _FixtureApi([])
    with pytest.raises(events.WfpEventOwnershipError) as raised:
        _run_fixture(api)
    owner = raised.value.owner
    assert owner is events.retained_wfp_event_owner_for_recovery()
    assert owner.state == "UNKNOWN"
    assert injected["entries"].failed is True
    assert sum(isinstance(call, tuple) and call[0] == release_name for call in api.calls) == 1

    resource_entry = next(item for item in owner.registry.unresolved() if item.label == target_label)
    output = next(item for item in resource_entry.keepalive if isinstance(item, events._WfpOutputSlot))
    assert output.release_call_state == "SUCCEEDED"
    assert output.release_status == 0

    with pytest.raises(events.NativeResourceError):
        events.recover_wfp_event_owner(owner)
    assert sum(isinstance(call, tuple) and call[0] == release_name for call in api.calls) == 1
    assert owner.state == "UNKNOWN"
    assert events.retained_wfp_event_owner_for_recovery() is owner
    if target_label == "wfp_net_event_enum":
        # The enum's SDK success was confirmed, so parent cleanup may continue;
        # the registry's stale RELEASED entry still keeps the owner UNKNOWN.
        assert sum(isinstance(call, tuple) and call[0] == "FwpmEngineClose0" for call in api.calls) == 1
    blocked_api = _FixtureApi()
    with pytest.raises(events.WfpEventOwnershipError):
        _run_fixture(blocked_api)
    assert blocked_api.calls == []


@pytest.mark.parametrize(("target_label", "release_name"), [
    ("wfp_net_event_enum", "FwpmNetEventDestroyEnumHandle0"),
    ("wfp_engine", "FwpmEngineClose0"),
])
def test_recovery_after_post_closer_bookkeeping_error_uses_cached_success(
    monkeypatch, target_label, release_name,
):
    monkeypatch.setattr(events, "_active_owner", None)
    original_registry_for = events.registry_for
    injected = {}

    def registry_for_with_post_closer_fault(adapter):
        registry = original_registry_for(adapter)
        if isinstance(adapter, events._WfpEventOwner) and "acquire" not in injected:
            original_acquire = registry.acquire

            def acquire_with_fault(adapter_arg, handle, closer, **kwargs):
                entry = original_acquire(adapter_arg, handle, closer, **kwargs)
                if entry.label == target_label and "entry" not in injected:
                    original_release = entry.release_owned

                    def release_with_fault(owner, *, retry=False):
                        if "post_closer_error" not in injected:
                            result = entry.closer(entry.handle)
                            injected["result"] = result
                            injected["post_closer_error"] = RuntimeError(
                                "fixture registry bookkeeping failed after helper success"
                            )
                            entry.state = "UNKNOWN"
                            entry.error = injected["post_closer_error"]
                            raise injected["post_closer_error"]
                        return original_release(owner, retry=retry)

                    entry.release_owned = release_with_fault
                    injected["entry"] = entry
                return entry

            registry.acquire = acquire_with_fault
            injected["acquire"] = registry
        return registry

    monkeypatch.setattr(events, "registry_for", registry_for_with_post_closer_fault)
    api = _FixtureApi([])
    with pytest.raises(events.WfpEventOwnershipError) as raised:
        _run_fixture(api)

    owner = raised.value.owner
    assert owner is events.retained_wfp_event_owner_for_recovery()
    assert owner.state == "UNKNOWN"
    assert injected["result"] == 0 and type(injected["result"]) is int
    assert sum(isinstance(call, tuple) and call[0] == release_name for call in api.calls) == 1

    # Recovery retries registry bookkeeping from the same owner. The holder
    # returns its cached DWORD success, so the native closer remains one-shot.
    events.recover_wfp_event_owner(owner)
    assert owner.state == "RELEASED"
    assert events.retained_wfp_event_owner_for_recovery() is None
    assert sum(isinstance(call, tuple) and call[0] == release_name for call in api.calls) == 1


@pytest.mark.parametrize(("resource", "status_key", "release_name"), [
    ("enum", "destroy_enum", "FwpmNetEventDestroyEnumHandle0"),
    ("engine", "close_engine", "FwpmEngineClose0"),
])
@pytest.mark.parametrize("returned", ["invalid-shape", "conversion-raises"])
def test_close_or_destroy_with_unconvertible_result_is_unknown_and_never_retried(
    monkeypatch, resource, status_key, release_name, returned,
):
    monkeypatch.setattr(events, "_active_owner", None)
    api = _FixtureApi([])
    api.status[status_key] = _UnconvertibleDword() if returned == "conversion-raises" else "invalid-dword-shape"

    with pytest.raises(events.WfpEventOwnershipError) as raised:
        _run_fixture(api)
    owner = raised.value.owner
    assert owner is events.retained_wfp_event_owner_for_recovery()
    release_calls = [call for call in api.calls if isinstance(call, tuple) and call[0] == release_name]
    assert len(release_calls) == 1
    if resource == "enum":
        assert not any(isinstance(call, tuple) and call[0] == "FwpmEngineClose0" for call in api.calls)

    with pytest.raises(events.NativeResourceError):
        events.recover_wfp_event_owner(owner)
    assert len([call for call in api.calls if isinstance(call, tuple) and call[0] == release_name]) == 1
    if resource == "enum":
        assert not any(isinstance(call, tuple) and call[0] == "FwpmEngineClose0" for call in api.calls)

    blocked_api = _FixtureApi()
    with pytest.raises(events.WfpEventOwnershipError):
        _run_fixture(blocked_api)
    assert blocked_api.calls == []


@pytest.mark.parametrize("stage", ["appid", "engine", "option", "enum", "array"])
@pytest.mark.parametrize("fault", ["raise", "invalid_status"])
def test_output_slot_is_retained_before_sdk_raise_or_invalid_status(monkeypatch, stage, fault):
    monkeypatch.setattr(events, "_active_owner", None)
    status_keys = {
        "appid": "appid", "engine": "engine_open", "option": "get_option",
        "enum": "create_enum", "array": "enum",
    }
    api = _FixtureApi([_make_event()])
    if fault == "raise":
        api.raise_after_output.add(stage)
    else:
        api.status[status_keys[stage]] = "invalid-dword-shape"

    with pytest.raises(events.WfpEventOwnershipError) as raised:
        _run_fixture(api)
    owner = raised.value.owner
    assert owner is events.retained_wfp_event_owner_for_recovery()
    assert owner.state == "UNKNOWN"
    assert len(owner._pending_outputs) == 1
    output = owner._pending_outputs[0]
    assert output.function == {
        "appid": "FwpmGetAppIdFromFileName0", "engine": "FwpmEngineOpen0",
        "option": "FwpmEngineGetOption0", "enum": "FwpmNetEventCreateEnumHandle0",
        "array": "FwpmNetEventEnum2",
    }[stage]
    assert output.state == "UNKNOWN"
    assert output.failure == ("SDK_CALL_RAISED" if fault == "raise" else "SDK_STATUS_SHAPE_INVALID")
    assert output.raw_slots and type(output.raw_slots[0]) is int and output.raw_slots[0] > 0
    if stage == "array":
        assert output.raw_slots[1] == 1
    assert not any(isinstance(call, tuple) and call[0] in {
        "FwpmNetEventDestroyEnumHandle0", "FwpmEngineClose0", "FwpmFreeMemory0"
    } and call[0] != "FwpmFreeMemory0" for call in api.calls)
    expected_prior_frees = {"appid": 0, "engine": 1, "option": 1, "enum": 2, "array": 2}[stage]
    assert sum(isinstance(call, tuple) and call[0] == "FwpmFreeMemory0" for call in api.calls) == expected_prior_frees
    with pytest.raises(events.NativeResourceError):
        events.recover_wfp_event_owner(owner)
    calls_after_refusal = tuple(api.calls)
    blocked_api = _FixtureApi()
    with pytest.raises(events.WfpEventOwnershipError):
        _run_fixture(blocked_api)
    assert blocked_api.calls == []
    assert tuple(api.calls) == calls_after_refusal
    monkeypatch.setattr(events, "_active_owner", None)


@pytest.mark.parametrize("stage,closer", [
    ("appid", "FwpmFreeMemory0"),
    ("option", "FwpmEngineClose0"),
    ("array", "FwpmNetEventDestroyEnumHandle0"),
])
def test_release_uses_captured_closer_and_original_owner_recovery(monkeypatch, stage, closer):
    monkeypatch.setattr(events, "_active_owner", None)
    api = _FixtureApi([_make_event()])
    api.replace_closers_after_output[stage] = (closer,)
    with pytest.raises(events.WfpEventOwnershipError) as raised:
        _run_fixture(api)
    owner = raised.value.owner
    assert owner is events.retained_wfp_event_owner_for_recovery()
    assert not any(isinstance(call, tuple) and call[0] == "replacement_closer" for call in api.calls)
    with pytest.raises(events.NativeResourceError):
        events.recover_wfp_event_owner(owner)
    assert not any(isinstance(call, tuple) and call[0] == "replacement_closer" for call in api.calls)
    delattr(api, closer)
    events.recover_wfp_event_owner(owner)
    assert events.retained_wfp_event_owner_for_recovery() is None
    assert not any(isinstance(call, tuple) and call[0] == "replacement_closer" for call in api.calls)
    assert sum(isinstance(call, tuple) and call[0] == closer for call in api.calls) == 1


@pytest.mark.parametrize("stage,slot_name,original", [
    ("option", "engine", 0x101),
    ("array", "enum", 0x202),
])
def test_engine_and_enum_slot_drift_never_changes_release_target(monkeypatch, stage, slot_name, original):
    monkeypatch.setattr(events, "_active_owner", None)
    api = _FixtureApi([] if stage == "option" else [_make_event()], collection=0 if stage == "option" else 1)
    api.drift_slots_after_output[stage] = {slot_name: 0xD00D}
    with pytest.raises(events.WfpEventOwnershipError) as raised:
        _run_fixture(api)
    owner = raised.value.owner
    label = "wfp_engine" if slot_name == "engine" else "wfp_net_event_enum"
    entry = next(item for item in owner.registry.unresolved() if item.label == label)
    assert type(entry.handle) is int and entry.handle == original
    release_name = "FwpmEngineClose0" if slot_name == "engine" else "FwpmNetEventDestroyEnumHandle0"
    assert not any(isinstance(call, tuple) and call[0] == release_name for call in api.calls)
    if slot_name == "enum":
        assert not any(isinstance(call, tuple) and call[0] == "FwpmEngineClose0" for call in api.calls)
    api.output_slots[slot_name].contents.value = original
    events.recover_wfp_event_owner(owner)
    assert sum(isinstance(call, tuple) and call[0] == release_name for call in api.calls) == 1
    if slot_name == "enum":
        assert sum(isinstance(call, tuple) and call[0] == "FwpmEngineClose0" for call in api.calls) == 1


def test_free_slot_drift_retains_original_pointer_until_exact_slot_restored(monkeypatch):
    monkeypatch.setattr(events, "_active_owner", None)
    api = _FixtureApi([_make_event()])
    original_decode = events._decode_event

    def drift_page_slot(api_arg, event_pointer, start_ticks, end_ticks):
        api.output_slots["array"].contents.value = 0xD00D
        return original_decode(api_arg, event_pointer, start_ticks, end_ticks)

    monkeypatch.setattr(events, "_decode_event", drift_page_slot)
    with pytest.raises(events.WfpEventOwnershipError) as raised:
        _run_fixture(api)
    owner = raised.value.owner
    entry = next(item for item in owner.registry.unresolved() if item.label == "wfp_net_event_page")
    page_address = C.cast(api.event_arrays[0], C.c_void_p).value
    assert entry.value == page_address
    assert sum(isinstance(call, tuple) and call[0] == "FwpmFreeMemory0" for call in api.calls) == 2
    assert not any(isinstance(call, tuple) and call[0] in {
        "FwpmNetEventDestroyEnumHandle0", "FwpmEngineClose0"
    } for call in api.calls)
    with pytest.raises(events.NativeResourceError):
        events.recover_wfp_event_owner(owner)
    assert sum(isinstance(call, tuple) and call[0] == "FwpmFreeMemory0" for call in api.calls) == 2
    api.output_slots["array"].contents.value = page_address
    events.recover_wfp_event_owner(owner)
    freed = [call[1] for call in api.calls if isinstance(call, tuple) and call[0] == "FwpmFreeMemory0"]
    assert freed[-1] == page_address
    assert [call[0] for call in api.calls if isinstance(call, tuple) and call[0] in {
        "FwpmNetEventDestroyEnumHandle0", "FwpmEngineClose0"
    }] == ["FwpmNetEventDestroyEnumHandle0", "FwpmEngineClose0"]


def _observation_for_matching(rows, *, truncated=False, collection=True):
    return events.WfpNetEventObservation(
        state="UNVERIFIED", diagnostic_state="EVENTS_OBSERVED_UNVERIFIED",
        image_path=r"C:\fixture\python.exe", app_id_bytes_hex=APP_ID.hex(),
        app_id_sha256=hashlib.sha256(APP_ID).hexdigest(), collection_enabled=collection,
        window_start_filetime_100ns=NOW - 5 * 10_000_000,
        window_end_filetime_100ns=NOW, enum_create_started_filetime_100ns=NOW,
        enum_create_returned_filetime_100ns=NOW, pages=1, records_returned=len(rows),
        events=tuple(rows), exhausted=True, truncated=truncated,
        reason_codes=("SOFTWARE_FIXTURE_API_ONLY",), sdk_statuses=(),
    )


def _expectation(event):
    return events.EventCorrelationExpectation(
        filter_id=event.filter_id, layer_id=event.layer_id,
        ip_version=events.FWP_IP_VERSION_V4, protocol=6,
        local_address="127.0.0.1", local_port=event.local_port,
        remote_address="127.0.0.1", remote_port=event.remote_port,
        direction=event.direction,
        start_filetime_100ns=NOW - 5 * 10_000_000,
        end_filetime_100ns=NOW, app_id_bytes=APP_ID,
        package_sid="S-1-15-2001",
    )


def test_exact_correlation_is_diagnostic_only_and_never_block_eligible():
    event = _read_fixture_event(_make_event())
    diagnostic = events.diagnose_event_correlation(_observation_for_matching([event]), _expectation(event))
    assert diagnostic.state == "MATCHED_DIAGNOSTIC_ONLY"
    assert diagnostic.matched_indexes == (0,)
    assert diagnostic.qualification_state == "UNVERIFIED"
    assert diagnostic.blocking_eligible is False
    assert "ORIGINAL_FILTER_SEMANTIC_BINDING_NOT_SUPPLIED" in diagnostic.reason_codes


def test_matcher_rejects_partial_or_sdk_failed_snapshots_as_unverified():
    event = _read_fixture_event(_make_event())
    observation = _observation_for_matching([event])
    partial = replace(observation, exhausted=False)
    diagnostic = events.diagnose_event_correlation(partial, _expectation(event))
    assert diagnostic.state == "UNVERIFIED"
    assert diagnostic.reason_codes == ("EVENT_SNAPSHOT_NOT_EXHAUSTED",)

    failed = replace(
        observation,
        sdk_statuses=(events.SdkCallStatus("FwpmNetEventEnum2", 0x80320015, 5),),
    )
    diagnostic = events.diagnose_event_correlation(failed, _expectation(event))
    assert diagnostic.state == "UNVERIFIED"
    assert diagnostic.reason_codes == ("SDK_STATUS_FAILURE:FwpmNetEventEnum2:0x80320015",)

    missing_row = replace(observation, records_returned=2)
    diagnostic = events.diagnose_event_correlation(missing_row, _expectation(event))
    assert diagnostic.state == "UNVERIFIED"
    assert diagnostic.reason_codes == ("EVENT_ARRAY_DECODE_INCOMPLETE",)


def test_matcher_binds_expected_appid_to_the_observers_actual_appid():
    event = _read_fixture_event(_make_event())
    observation = replace(_observation_for_matching([event]), app_id_bytes_hex=b"other-image".hex())
    diagnostic = events.diagnose_event_correlation(observation, _expectation(event))
    assert diagnostic.state == "NO_MATCH"
    assert "OBSERVER_APP_ID_MISMATCH" in diagnostic.reason_codes
    assert diagnostic.blocking_eligible is False


@pytest.mark.parametrize(
    "event_changes,expected_changes,reason",
    [
        ({"timestamp": NOW - 60 * 10_000_000}, {}, "EVENT_FIELDS_INCOMPLETE_OR_UNKNOWN"),
        ({"app_id": b"other-app"}, {}, "APP_ID_MISMATCH"),
        ({"package_sid": b"\x01\x01\x00\x00\x00\x00\x00\x0f" + (9000).to_bytes(4, "little")}, {}, "PACKAGE_SID_MISMATCH"),
        ({"local": 0x0100007F}, {}, "ADDRESS_OR_BYTE_ORDER_MISMATCH"),
        ({"local_port": 1}, {}, "PORT_MISMATCH"),
        ({"filter_id": 999}, {}, "FILTER_ID_MISMATCH"),
        ({"layer_id": 49}, {}, "LAYER_ID_MISMATCH"),
        ({"direction": events.FWP_DIRECTION_IN}, {}, "DIRECTION_MISMATCH"),
        ({"loopback": 0}, {}, "NOT_LOOPBACK"),
        ({"event_type": 1}, {}, "EVENT_FIELDS_INCOMPLETE_OR_UNKNOWN"),
    ],
)
def test_mismatch_counterexamples_never_become_block_diagnostics(event_changes, expected_changes, reason):
    raw = _make_event(**event_changes)
    decoded = _read_fixture_event(raw)
    expectation = _expectation(_read_fixture_event(_make_event()))
    for key, value in expected_changes.items():
        setattr(expectation, key, value)
    diagnostic = events.diagnose_event_correlation(_observation_for_matching([decoded]), expectation)
    assert diagnostic.state == "NO_MATCH"
    assert diagnostic.blocking_eligible is False
    assert diagnostic.qualification_state == "UNVERIFIED"
    assert reason in diagnostic.reason_codes


def test_duplicate_matches_are_ambiguous_and_empty_or_truncated_sets_are_not_observed():
    event = _read_fixture_event(_make_event())
    expected = _expectation(event)
    duplicate = events.diagnose_event_correlation(_observation_for_matching([event, event]), expected)
    assert duplicate.state == "AMBIGUOUS" and duplicate.blocking_eligible is False
    empty = events.diagnose_event_correlation(_observation_for_matching([]), expected)
    assert empty.state == "NOT_OBSERVED"
    truncated = events.diagnose_event_correlation(
        _observation_for_matching([event], truncated=True), expected
    )
    assert truncated.state == "TRUNCATED_UNVERIFIED"


def test_caller_json_or_wrong_observation_shape_cannot_enter_matcher():
    with pytest.raises(TypeError):
        events.diagnose_event_correlation({}, {})
