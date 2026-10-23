"""Ordinary TokenGroups interface controls; software ABI leaves, no native effects."""
import ctypes as C
from dataclasses import FrozenInstanceError, fields, replace
import struct
from types import SimpleNamespace

import pytest

from comsol_mcp import _windows_t037_child_evidence as e
from comsol_mcp import _windows_owned_resources as owned
from comsol_mcp import _windows_t037_resume as resume
from tests.test_windows_t037_child_evidence import Boundary, comparisons, observe, token
from tests.test_windows_t037_connected_b import connected_chain, launch_refusal, known_child_cleanup


def sid_bytes(text):
    parts = text.split('-')
    return bytes((1, len(parts) - 3)) + int(parts[2]).to_bytes(6, 'big') + b''.join(
        struct.pack('<I', int(v)) for v in parts[3:])


def group_bytes(rows, base):
    tail = b''
    entries = []
    offset = 8 + 16 * len(rows)
    for text, attrs in rows:
        entries.append(struct.pack('<QI4x', base + offset + len(tail), attrs))
        tail += sid_bytes(text)
    return struct.pack('<I4x', len(rows)) + b''.join(entries) + tail


ROWS = (('S-1-5-32-545', 0x4), ('S-1-5-32-544', 0x10),
        ('S-1-5-99-7', 0x87654321), ('S-1-5-32-545', 0x80000004))


class TokenSDK:
    """Only Open/Get/Lookup/Close exports are fake; the whole production token method runs."""
    def __init__(self, monkeypatch, *, rows=(ROWS,), fault=None, close_failure=False, owner=None):
        self.rows = rows
        self.fault = fault
        self.close_failure = close_failure
        self.opens = []
        self.queries = []
        self.closed = []
        self.close_entries = []
        self.error = 122
        self.stat_reads = 0
        self.api = e.WindowsChildEvidenceApi.__new__(e.WindowsChildEvidenceApi)
        if owner is not None:
            owned.attach_registry(self.api, owned.registry_for(owner))
        self.api.a = SimpleNamespace(OpenProcessToken=self.open_token,
            GetTokenInformation=self.get_token, LookupPrivilegeNameW=self.privilege)
        self.api.k = SimpleNamespace(CloseHandle=self.close)
        monkeypatch.setattr(C, 'get_last_error', lambda: self.error, raising=False)

    def open_token(self, process, access, out):
        assert process == 101 and access == 8
        self.opens.append((process, access))
        self.stat_reads = 0
        C.cast(out, C.POINTER(e.PTR)).contents.value = 900 + len(self.opens)
        return 1

    def data(self, kind, base, read):
        if kind == 10:
            raw = bytearray(56)
            struct.pack_into('<QQ', raw, 0, 3, 4)
            modified = 18 if self.fault == 'stats_drift' and self.stat_reads else 17
            struct.pack_into('<Q', raw, 48, modified)
            if read:
                self.stat_reads += 1
            return bytes(raw)
        if kind in (1, 25):
            text = 'S-1-5-21-1' if kind == 1 else 'S-1-16-4096'
            return struct.pack('<QI4x', base + 16, 0x60) + sid_bytes(text)
        if kind == 2:
            rows = self.rows[min(len(self.opens) - 1, len(self.rows) - 1)]
            raw = bytearray(group_bytes(rows, base))
            if self.fault == 'short_count':
                return bytes(raw[:3])
            if self.fault == 'count_budget':
                struct.pack_into('<I', raw, 0, 4097)
            if self.fault == 'short_row':
                return bytes(raw[:8])
            if self.fault == 'pointer_before':
                struct.pack_into('<Q', raw, 8, max(0, base - 1))
            if self.fault == 'pointer_after':
                struct.pack_into('<Q', raw, 8, base + len(raw))
            if self.fault == 'sid_revision':
                raw[8 + 16 * len(rows)] = 0
            if self.fault == 'sid_subauthority_count':
                raw[8 + 16 * len(rows) + 1] = 16
            if self.fault == 'truncated_sid':
                raw = raw[:-1]
            return bytes(raw)
        if kind in (11, 30):
            return group_bytes((), base)
        if kind in (27, 29):
            return struct.pack('<I', 1)
        if kind == 3:
            return struct.pack('<IQI', 1, 23, 0x80000002)
        raise AssertionError(kind)

    def get_token(self, handle, kind, storage, capacity, returned):
        phase = 'size' if storage is None else 'read'
        assert getattr(handle, 'value', handle) == 900 + len(self.opens)
        self.queries.append((getattr(handle, 'value', handle), kind, phase))
        raw = self.data(kind, C.addressof(storage) if storage is not None else 0, storage is not None)
        C.cast(returned, C.POINTER(e.U32)).contents.value = len(raw)
        if storage is None:
            if kind == 2 and self.fault == 'size_zero':
                C.cast(returned, C.POINTER(e.U32)).contents.value = 0
            self.error = 122
            return 0
        if kind == 2 and self.fault == 'read_error':
            self.error = 5
            return 0
        assert len(raw) <= capacity
        C.memmove(storage, raw, len(raw))
        return 1

    def privilege(self, system, luid, storage, used):
        assert system is None
        text = 'SeFixturePrivilege'
        raw = text.encode('utf-16-le')
        C.memmove(storage, raw + b'\0\0', len(raw) + 2)
        C.cast(used, C.POINTER(e.U32)).contents.value = len(text)
        return 1

    def close(self, handle):
        self.closed.append(handle)
        entries = tuple(x for x in owned.registry_for(self.api).unresolved()
                        if x.label == 'child_evidence_token')
        assert len(entries) == 1 and entries[0].value == handle
        self.close_entries.append(entries[0])
        if self.close_failure:
            self.error = 6
            return 0
        return 1


def test_legacy_unknown_groups_are_not_known_empty_and_observation_stays_frozen():
    legacy = token()
    assert fields(e.TokenObservation)[-1].name == 'groups'
    assert legacy.groups is None
    empty = replace(legacy, groups=())
    assert empty.groups == () and empty != legacy
    with pytest.raises(FrozenInstanceError):
        empty.groups = None


def test_complete_production_token_reader_preserves_group_order_duplicates_and_every_attribute(monkeypatch):
    sdk = TokenSDK(monkeypatch)
    actual = sdk.api.token(101)
    assert actual.groups == tuple(e.SidAttributes(*row) for row in ROWS)
    assert actual.groups[0].attributes == 4 and actual.groups[1].attributes == 0x10
    assert actual.groups[2].attributes == 0x87654321 and actual.groups[3].sid == actual.groups[0].sid
    assert actual.privileges[0] == e.Privilege(23, 0x80000002, 'SeFixturePrivilege')
    assert sdk.opens == [(101, 8)] and sdk.closed == [901]
    reads = [kind for _, kind, phase in sdk.queries if phase == 'read']
    assert reads == [10, 1, 2, 11, 30, 25, 27, 29, 3, 10]
    assert [(kind, phase) for _, kind, phase in sdk.queries if kind == 2] == [(2, 'size'), (2, 'read')]
    assert sdk.close_entries[0].state == 'RELEASED' and not owned.registry_for(sdk.api).unresolved()


def test_production_known_empty_groups_are_an_empty_tuple_not_historical_none(monkeypatch):
    sdk = TokenSDK(monkeypatch, rows=((),))
    actual = sdk.api.token(101)
    assert actual.groups == () and actual.groups is not None
    assert replace(actual, groups=None) != actual
    assert sdk.opens == [(101, 8)] and sdk.closed == [901]


@pytest.mark.parametrize('fault', ['short_count', 'count_budget', 'short_row', 'pointer_before',
    'pointer_after', 'sid_revision', 'sid_subauthority_count', 'truncated_sid', 'size_zero', 'read_error'])
def test_malformed_ordinary_groups_refuse_with_original_token_closed_exactly_once(monkeypatch, fault):
    sdk = TokenSDK(monkeypatch, fault=fault)
    with pytest.raises(e.ObservationError):
        sdk.api.token(101)
    assert sdk.opens == [(101, 8)] and sdk.closed == [901]
    assert any(kind == 2 for _, kind, _ in sdk.queries)
    assert not any(kind == 11 for _, kind, _ in sdk.queries)
    assert sdk.close_entries[0].state == 'RELEASED' and not owned.registry_for(sdk.api).unresolved()


def test_statistics_guard_still_refuses_changes_while_groups_were_read(monkeypatch):
    sdk = TokenSDK(monkeypatch, fault='stats_drift')
    with pytest.raises(e.ObservationError) as error:
        sdk.api.token(101)
    assert error.value.issue.code == 'TOKEN_CHANGED_DURING_QUERY'
    assert [kind for _, kind, phase in sdk.queries if phase == 'read'].count(2) == 1
    assert sdk.closed == [901] and not owned.registry_for(sdk.api).unresolved()


@pytest.mark.parametrize('fault', [None, 'read_error'])
def test_group_result_never_hides_original_token_close_unknown_or_retries_close(monkeypatch, fault):
    sdk = TokenSDK(monkeypatch, fault=fault, close_failure=True)
    with pytest.raises(e.ObservationError):
        sdk.api.token(101)
    assert any(kind == 2 for _, kind, _ in sdk.queries)
    entry = sdk.close_entries[0]
    assert entry.state == 'UNKNOWN' and owned.registry_for(sdk.api).unresolved() == (entry,)
    with pytest.raises(e.ObservationError):
        entry.release_owned(sdk.api)
    assert sdk.opens == [(101, 8)] and sdk.closed == [901]


@pytest.mark.parametrize('change', ['sid', 'attributes', 'order'])
def test_actual_reader_group_change_breaks_observe_child_token_stability_with_original_borrows(monkeypatch, change):
    boundary = Boundary()
    after = list(ROWS)
    if change == 'sid':
        after[0] = ('S-1-5-99-999', after[0][1])
    elif change == 'attributes':
        after[0] = (after[0][0], after[0][1] | 0x80000000)
    else:
        after.reverse()
    sdk = TokenSDK(monkeypatch, rows=(ROWS, tuple(after)), owner=boundary)
    boundary.token = sdk.api.token
    result = observe(boundary)
    assert not result.issues and comparisons(result)['token_stable'] is False
    assert result.token_before.groups == tuple(e.SidAttributes(*row) for row in ROWS)
    assert result.token_after.groups == tuple(e.SidAttributes(*row) for row in after)
    assert result.token_before.token_id == result.token_after.token_id == 3
    assert result.token_before.modified_id == result.token_after.modified_id == 17
    assert sdk.closed == [901, 902] and not boundary.closed
    assert boundary.p.state == boundary.t.state == boundary.pin.entry.state == 'ACQUIRED'


def test_legacy_unknown_and_new_known_empty_groups_do_not_compare_stable():
    boundary = Boundary()
    legacy = token()
    boundary.tokens = [legacy, replace(legacy, groups=())]
    result = observe(boundary)
    assert comparisons(result)['token_stable'] is False
    assert result.token_before.groups is None and result.token_after.groups == ()
    assert not boundary.closed


@pytest.mark.parametrize('drift', [False, True])
def test_normal_factory_connected_B_carries_actual_groups_and_keeps_issuer_unqualified(monkeypatch, tmp_path, drift):
    with connected_chain(monkeypatch, tmp_path) as (_, factory, api, sdk, command, events, _, _):
        original = sdk.token_bytes
        def token_bytes(kind, base, read=False):
            if kind == 2:
                attrs = 0x11 if drift and sdk.opens == 2 else 7
                return group_bytes((('S-1-5-32-545', attrs), ('S-1-5-5-1-2', 0xC0000007),
                    (factory.profile.service_sid, 7), ('S-1-16-4096', 0x60)), base)
            return original(kind, base, read)
        monkeypatch.setattr(sdk, 'token_bytes', token_bytes)
        error = launch_refusal(factory, command)
        assert error.code == 'NATIVE_CHILD_PROOF_UNAVAILABLE'
        owner = api._last_connected_b_observer
        actual = owner.result
        assert actual.qualification == 'UNQUALIFIED' and actual.state == 'DIAGNOSTIC_ONLY_NOT_ADMISSION'
        assert actual.finite_state == ('MISMATCH' if drift else 'MATCHED_FINITE_CHECKS_ONLY')
        assert dict((c.name, c.matches) for c in actual.finite_comparisons)['token_stable'] is (not drift)
        assert actual.observations.token_before.groups[0] == e.SidAttributes('S-1-5-32-545', 7)
        assert actual.observations.token_after.groups[0].attributes == (0x11 if drift else 7)
        assert sdk.opens == 2 and sdk.closed == [901, 944, 902]
        assert 'RESUME' not in events and not resume._pending
        known_child_cleanup(api, factory)
