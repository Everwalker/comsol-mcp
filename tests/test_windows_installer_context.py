"""Synthetic SDK boundary tests, not native privilege or installer approval."""
import copy
import ctypes as C
from dataclasses import replace
from types import SimpleNamespace

import pytest

from comsol_mcp import _windows_installer_context as m
from comsol_mcp import _windows_host_bootstrap as b
from tests.test_windows_host_bootstrap import intent_doc


RESTORE = (0xFEDCBA98, -12345)  # SOFTWARE_ONLY, exercises full signed-high LUID
USER = "S-1-5-21-101-102-103-1001"


class Once:
    def __init__(self, value):
        self.n, self.result = 0, value

    @property
    def value(self):
        self.n += 1
        if self.n != 1:
            raise RuntimeError("SOFTWARE result consumed twice")
        return self.result


class SDK:
    """Real ctypes copy-out ABI, synthetic token and no real setters."""
    def __init__(self, *, pid=3101, birth=2**48+9, attrs=1, system=False, admin=True, elevated=True):
        self.pid, self.birth = pid, birth
        self.user = b.SY if system else USER
        self.groups, self.elevated = [(b.BA, 4 if admin else 16)], elevated
        self.privileges = {(12, 0): 3, (19, -1): 0x80000000}
        if attrs is not None:
            self.privileges[RESTORE] = attrs
        self.modified, self.token_id, self.auth_id = 20, (41, -1), (43, 0)
        self.current_token_id = self.token_type = None
        self.calls, self.adjusts, self.closed, self.wrappers = [], [], [], []
        self.err, self.next_handle, self.impersonation = 0, 500, False
        self.adjust_fault, self.close_fault, self.info_hook = None, None, None
        self.once = False
        self.sdk = {name: getattr(self, name) for name in (*m.READONLY_SDK, "AdjustTokenPrivileges")}

    def result(self, value):
        if self.once:
            value = Once(value)
            self.wrappers.append(value)
        return value

    def error(self):
        return self.err

    def clear(self, value):
        assert value == 0
        self.err = 0

    def GetCurrentProcessId(self):
        self.calls.append(("GetCurrentProcessId",))
        self.err = 0
        return self.result(self.pid)

    def GetCurrentProcess(self):
        self.calls.append(("GetCurrentProcess",))
        return self.result(C.c_void_p(-1).value)

    def GetCurrentThread(self):
        self.calls.append(("GetCurrentThread",))
        return self.result(C.c_void_p(-2).value)

    def GetProcessTimes(self, handle, *outputs):
        self.calls.append(("GetProcessTimes", handle.value))
        C.memmove(outputs[0], self.birth.to_bytes(8, "little"), 8)
        self.err = 0
        return self.result(1)

    def OpenThreadToken(self, thread, access, as_self, output):
        self.calls.append(("OpenThreadToken", access, as_self))
        assert access == 8 and as_self == 1
        if self.impersonation:
            self.next_handle += 1
            C.cast(output, C.POINTER(m.H))[0] = self.next_handle
            self.err = 0
            return self.result(1)
        self.err = 1008
        return self.result(0)

    def OpenProcessToken(self, process, access, output):
        self.calls.append(("OpenProcessToken", access))
        assert process.value == C.c_void_p(-1).value and access == 0x28
        self.next_handle += 1
        C.cast(output, C.POINTER(m.H))[0] = self.next_handle
        self.err = 0
        return self.result(1)

    def LookupPrivilegeValueW(self, machine, name, output):
        self.calls.append(("LookupPrivilegeValueW", machine, name))
        assert machine is None and name == m.RESTORE
        item = C.cast(output, C.POINTER(m._Luid)).contents
        item.low, item.high = RESTORE
        self.err = 0
        return self.result(1)

    def GetTokenInformation(self, handle, level, buffer, maximum, used):
        self.calls.append(("GetTokenInformation", handle.value, level))
        if level == 20 and maximum != C.sizeof(m.D):
            self.err = 24  # actual Windows fixed TOKEN_ELEVATION length contract
            return self.result(0)
        assert maximum == (C.sizeof(m.D) if level == 20 else m.MAX_TOKEN)
        base = C.addressof(buffer)
        if level == 10:
            value = m._Statistics.from_buffer(buffer)
            value.token_id.low, value.token_id.high = self.current_token_id if handle.value == C.c_void_p(-4).value and self.current_token_id else self.token_id
            value.authentication_id.low, value.authentication_id.high = self.auth_id
            value.modified_id.low = self.modified
            value.token_type = self.token_type or 1
            value.group_count, value.privilege_count = len(self.groups), len(self.privileges)
            extent = C.sizeof(value)
        elif level == 1:
            value = m._SidAttributes.from_buffer(buffer)
            raw = b._sid_bytes(self.user)
            extent = C.sizeof(value)
            value.sid = base + extent
            C.memmove(value.sid, raw, len(raw))
            extent += len(raw)
        elif level == 2:
            m.D.from_buffer(buffer).value = len(self.groups)
            extent = m._Groups.first.offset + len(self.groups) * C.sizeof(m._SidAttributes)
            for n, (sid, flags) in enumerate(self.groups):
                item = m._SidAttributes.from_buffer(buffer, m._Groups.first.offset + n * C.sizeof(m._SidAttributes))
                raw = b._sid_bytes(sid)
                item.sid, item.attributes = base + extent, flags
                C.memmove(item.sid, raw, len(raw))
                extent += len(raw)
        elif level == 3:
            m.D.from_buffer(buffer).value = len(self.privileges)
            extent = 4 + len(self.privileges) * C.sizeof(m._Privilege)
            for n, (luid, flags) in enumerate(self.privileges.items()):
                item = m._Privilege.from_buffer(buffer, 4 + n * C.sizeof(m._Privilege))
                item.luid.low, item.luid.high = luid
                item.attributes = flags
        elif level == 20:
            m.D.from_buffer(buffer).value, extent = int(self.elevated), 4
        else:
            pytest.fail("unreviewed token info class")
        C.cast(used, C.POINTER(m.D))[0] = extent
        if self.info_hook:
            self.info_hook(handle, level, buffer, used)
        self.err = 0
        return self.result(1)

    def AdjustTokenPrivileges(self, handle, disable_all, request, size, previous, used):
        value = C.cast(request, C.POINTER(m._OnePrivilege)).contents
        old = C.cast(previous, C.POINTER(m._OnePrivilege)).contents
        luid = m._luid(value.entry.luid)
        self.calls.append(("AdjustTokenPrivileges", handle.value))
        self.adjusts.append((disable_all, value.count, luid, value.entry.attributes, size, previous))
        assert disable_all == 0 and value.count == 1 and luid == RESTORE and not value.entry.attributes & 4
        assert size == C.sizeof(m._OnePrivilege)
        if self.adjust_fault == "before-raise":
            raise OSError("SOFTWARE no SDK result")
        if self.adjust_fault == "not-all-assigned":
            old.count, self.err = 0, 1300
            C.cast(used, C.POINTER(m.D))[0] = 4
            return self.result(1)
        old.count, old.entry.luid.low, old.entry.luid.high = 1, *luid
        old.entry.attributes = self.privileges[luid]
        C.cast(used, C.POINTER(m.D))[0] = C.sizeof(old)
        self.privileges[luid] = value.entry.attributes
        self.modified += 1
        self.err = 0
        if self.adjust_fault == "after-raise":
            raise OSError("SOFTWARE adjusted then lost result")
        if self.adjust_fault == "bad-previous":
            old.entry.luid.low += 1
        if isinstance(self.adjust_fault, tuple):
            return self.adjust_fault[0]
        return self.result(1)

    def CloseHandle(self, value):
        self.calls.append(("CloseHandle", value.value))
        if self.close_fault == "before-raise":
            raise OSError("SOFTWARE no close return")
        self.closed.append(value.value)
        if self.close_fault == "after-raise":
            raise OSError("SOFTWARE closed then lost return")
        if self.close_fault is not None:
            return self.close_fault[0]
        self.err = 0
        return self.result(1)


@pytest.fixture(autouse=True)
def isolated_roots(monkeypatch):
    monkeypatch.setattr(m, "_ROOTS", {})  # synthetic-only roots, never clears native owners


def setup(sdk=None):
    sdk = sdk or SDK()
    doc, _ = intent_doc()
    raw = b.canonical(doc)
    intent = b.parse_plan(raw)
    original = m._software_prepare(intent, sdk.sdk, sdk.error, sdk.clear, original_plan_bytes=raw, seal=m._SOFTWARE)
    return original, sdk, intent, raw


def enable(original, intent):
    original.enable(intent, approved_intent_sha256=intent.sha256)


@pytest.mark.parametrize("attrs,system,expected", [(1, False, "ASSIGNED_DISABLED_ONE_ENABLE"),
    (0, False, "ASSIGNED_DISABLED_ONE_ENABLE"), (3, False, "ALREADY_ENABLED_NO_ADJUST"),
    (None, True, "SYSTEM_NO_ADJUST")])
def test_complete_local_source_lifecycle_only_original_se_restore(attrs, system, expected):
    original, sdk, intent, _ = setup(SDK(attrs=attrs, system=system))
    before = dict(sdk.privileges)
    assert original.eligibility == expected and original.preview()["origin"] == "SOFTWARE_ONLY"
    enable(original, intent)
    original.restore()
    assert sdk.privileges == before
    assert len(sdk.adjusts) == (2 if expected == "ASSIGNED_DISABLED_ONE_ENABLE" else 0)
    assert all(row[1] == 1 and row[2] == RESTORE for row in sdk.adjusts)
    original.close()
    assert original.state == "CLOSED" and len(sdk.closed) == 1 and not m.retained_contexts()


@pytest.mark.parametrize("sdk,reason", [(SDK(attrs=None), "REFUSED_SE_RESTORE_UNASSIGNED"),
    (SDK(attrs=4), "REFUSED_SE_RESTORE_UNASSIGNED"), (SDK(admin=False), "REFUSED_NOT_ELEVATED_ADMIN"),
    (SDK(elevated=False), "REFUSED_NOT_ELEVATED_ADMIN")])
def test_missing_actual_assigned_capability_cannot_acquire_privilege(sdk, reason):
    original, sdk, intent, _ = setup(sdk)
    assert original.eligibility == reason
    with pytest.raises(m.ContextError):
        enable(original, intent)
    assert sdk.adjusts == []
    original.close()


def test_thread_impersonation_refuses_actual_primary_source_and_closes_only_original_handle():
    sdk = SDK()
    sdk.impersonation = True
    with pytest.raises(m.ContextError) as raised:
        setup(sdk)
    original = raised.value.owner
    assert raised.value.code == "THREAD_IMPERSONATION" and sdk.adjusts == []
    original.close()
    assert sdk.closed == [501]


@pytest.mark.parametrize("wrong", ["dict", "string", "subclass", "sha", "raw", "copy"])
def test_closed_intent_and_exact_original_approval_before_any_effect(wrong):
    original, sdk, intent, raw = setup()
    if wrong == "sha": candidate, sha = intent, "0" * 64
    elif wrong == "raw":
        with pytest.raises((m.ContextError, b.BootstrapError)):
            m._software_prepare(intent, sdk.sdk, sdk.error, sdk.clear, original_plan_bytes=b"{}", seal=m._SOFTWARE)
        assert not sdk.adjusts
        return
    elif wrong == "dict": candidate, sha = original.preview(), intent.sha256
    elif wrong == "string": candidate, sha = raw.decode(), intent.sha256
    elif wrong == "copy": candidate, sha = copy.deepcopy(intent), intent.sha256
    else:
        class Derived(b.BootstrapIntent): pass
        candidate, sha = Derived(**intent.__dict__), intent.sha256
    before = list(sdk.calls)
    with pytest.raises(m.ContextError):
        original.enable(candidate, approved_intent_sha256=sha)
    assert sdk.calls == before and sdk.adjusts == []


@pytest.mark.parametrize("drift", ["other-privilege", "user", "group", "elevation", "token-id", "current-primary", "pid", "birth", "modified"])
@pytest.mark.parametrize("phase", ["enable", "restore"])
def test_full_preimage_compare_preserves_foreign_state(drift, phase):
    original, sdk, intent, _ = setup()
    if phase == "restore": enable(original, intent)
    if drift == "other-privilege": sdk.privileges[(12, 0)] ^= 2
    elif drift == "user": sdk.user = "S-1-5-19"
    elif drift == "group": sdk.groups.append(("S-1-5-32-545", 4))
    elif drift == "elevation": sdk.elevated = False
    elif drift == "token-id": sdk.token_id = (77, 0)
    elif drift == "current-primary": sdk.current_token_id = (79, 0)
    elif drift == "pid": sdk.pid += 1
    elif drift == "birth": sdk.birth += 1
    else: sdk.modified += 1
    before, count = dict(sdk.privileges), len(sdk.adjusts)
    with pytest.raises(m.ContextError):
        enable(original, intent) if phase == "enable" else original.restore()
    assert sdk.privileges == before and len(sdk.adjusts) == count


@pytest.mark.parametrize("mode", ["after-raise", "before-raise", (None,), (False,), ("1",), (1.0,), "not-all-assigned", "bad-previous"])
@pytest.mark.parametrize("phase", ["enable", "restore"])
def test_unknown_effect_fences_new_api_owner_same_pid_birth_before_open_or_fresh_token_proof(mode, phase):
    original, sdk, intent, raw = setup()
    if phase == "restore": enable(original, intent)
    sdk.adjust_fault = mode
    with pytest.raises(m.ContextError) as raised:
        enable(original, intent) if phase == "enable" else original.restore()
    assert original.privilege_state == "UNKNOWN"
    if mode == "not-all-assigned": assert raised.value.code == "ERROR_NOT_ALL_ASSIGNED"
    before = list(sdk.calls)
    for action in (lambda: enable(original, intent), original.restore, original.close):
        with pytest.raises(m.ContextError): action()
    assert sdk.calls == before
    with pytest.raises(m.ContextError): original._snapshot()
    assert sdk.calls == before
    # New API instance/new owner must not acquire a replacement HANDLE to
    # observe away original process-primary-token uncertainty.
    with pytest.raises(m.ContextError) as fenced:
        m._software_prepare(intent, sdk.sdk, sdk.error, sdk.clear, original_plan_bytes=raw, seal=m._SOFTWARE)
    assert fenced.value.code == "PRIMARY_TOKEN_SCOPE_FENCED"
    assert all(row[0] in {"GetCurrentProcessId", "GetCurrentProcess", "GetProcessTimes"} for row in sdk.calls[len(before):])
    assert original in m.retained_contexts()
    other, independent, other_intent, _ = setup(SDK(pid=9002, birth=2**48+99))
    enable(other, other_intent)
    other.restore()
    other.close()
    assert len(independent.adjusts) == 2 and original in m.retained_contexts()


@pytest.mark.parametrize("phase", ["enable", "restore"])
def test_normalized_effect_success_metadata_fault_reconciles_without_any_sdk_calls(monkeypatch, phase):
    original, sdk, intent, _ = setup()
    if phase == "restore": enable(original, intent)
    def checkpoint(event, record):
        if event == phase + "_confirmed":
            raise OSError("SOFTWARE metadata ack fault after actual postimage cached")
    monkeypatch.setattr(original, "checkpoint", checkpoint)
    with pytest.raises(m.ContextError):
        enable(original, intent) if phase == "enable" else original.restore()
    before = list(sdk.calls)
    original.reconcile()
    assert sdk.calls == before and original.state == ("ACTIVE_CHANGED" if phase == "enable" else "RESTORED")


@pytest.mark.parametrize("mode", ["after-raise", "before-raise", (0,), (None,), (False,), ("1",), (1.0,)])
def test_opaque_close_retains_original_handle_but_is_not_privilege_unknown_or_other_process_freeze(mode):
    original, sdk, intent, raw = setup()
    sdk.close_fault = mode
    with pytest.raises(m.ContextError): original.close()
    assert original.cleanup_unknown and original.privilege_state == "KNOWN_UNMODIFIED"
    before = list(sdk.calls)
    with pytest.raises(m.ContextError): original.close()
    assert sdk.calls == before
    sdk.close_fault = None
    fresh = m._software_prepare(intent, sdk.sdk, sdk.error, sdk.clear, original_plan_bytes=raw, seal=m._SOFTWARE)
    fresh.close()
    assert fresh is not original and original in m.retained_contexts()


@pytest.mark.parametrize("event", ["close_confirmed", "retire"])
def test_confirmed_close_and_root_retirement_ack_do_not_close_again(monkeypatch, event):
    original, sdk, _, _ = setup()
    fired = []
    def checkpoint(actual, record):
        if event == actual and not fired:
            fired.append(record)
            raise OSError("SOFTWARE confirmed cleanup ack lost")
    monkeypatch.setattr(original, "checkpoint", checkpoint)
    with pytest.raises(m.ContextError): original.close()
    before = list(sdk.calls)
    original.reconcile()
    assert sdk.calls == before and original.state == "CLOSED" and not m.retained_contexts()


@pytest.mark.parametrize("binding", ["token-wrapper", "token-slot", "token-argument", "sdk-close", "intent", "snapshot", "scope", "call-output"])
def test_foreign_original_bindings_refuse_without_setter(binding):
    original, sdk, intent, _ = setup()
    if binding == "token-wrapper": original.token = copy.copy(original.token)
    elif binding == "token-slot": original.token.slot = m.H(original.token.value)
    elif binding == "token-argument": original.token.argument = m.H(original.token.value)
    elif binding == "sdk-close": original.api.sdk["CloseHandle"] = lambda *args: pytest.fail("replacement Close")
    elif binding == "intent": original.intent = copy.deepcopy(intent)
    elif binding == "snapshot": original.before = replace(original.before)
    elif binding == "scope": original.scope = (9999, 8888)
    else: original.token.call.outputs = (m.H(original.token.value),)
    before = len(sdk.adjusts)
    with pytest.raises(m.ContextError): enable(original, intent)
    assert len(sdk.adjusts) == before


def test_actual_return_wrappers_are_consumed_once_including_adjust_and_close():
    sdk = SDK()
    sdk.once = True
    original, sdk, intent, _ = setup(sdk)
    enable(original, intent)
    original.restore()
    original.close()
    assert sdk.wrappers and all(w.n == 1 for w in sdk.wrappers)


@pytest.mark.parametrize("bad", ["user-pointer", "user-count", "groups-count", "privilege-count", "duplicate-privilege", "elevation", "statistics"])
def test_bounded_original_ctypes_outputs_and_closed_parsers(bad):
    sdk = SDK()
    def mutate(handle, level, buf, used):
        if bad == "user-pointer" and level == 1: m._SidAttributes.from_buffer(buf).sid = C.addressof(buf) + m.MAX_TOKEN + 4
        elif bad == "user-count" and level == 1: buf[C.sizeof(m._SidAttributes)+1] = b"\xff"
        elif bad == "groups-count" and level == 2: m.D.from_buffer(buf).value = 0xFFFFFFFF
        elif bad == "privilege-count" and level == 3: m.D.from_buffer(buf).value = 513
        elif bad == "duplicate-privilege" and level == 3:
            C.memmove(C.addressof(buf)+16, C.addressof(buf)+4, 8)
        elif bad == "elevation" and level == 20: C.cast(used, C.POINTER(m.D))[0] = 8
        elif bad == "statistics" and level == 10: C.cast(used, C.POINTER(m.D))[0] = 4
    sdk.info_hook = mutate
    with pytest.raises(m.ContextError): setup(sdk)
    assert not sdk.adjusts


def test_fixed_abi_and_no_service_privilege_contract_change():
    assert C.sizeof(m._Luid) == 8 and C.sizeof(m._Privilege) == 12 and C.sizeof(m._OnePrivilege) == 16
    assert C.sizeof(m._Statistics) == 56 and m._Groups.first.offset == 8
    assert m.RESTORE not in b.PRIVILEGES and len(b.PRIVILEGES) == 4


@pytest.mark.parametrize("phase", ["enable", "restore"])
def test_normalized_adjust_result_then_ack_loss_cannot_query_missing_postimage(monkeypatch, phase):
    original, sdk, intent, _ = setup()
    if phase == "restore": enable(original, intent)
    prior = m._Call.__setattr__
    fired = []
    def store(call, name, value):
        prior(call, name, value)
        if not fired and name == "_result" and value and value[0] == "SUCCEEDED" and call.adjust:
            fired.append(call)
            raise OSError("SOFTWARE metadata fault after normalized Adjust success")
    monkeypatch.setattr(m._Call, "__setattr__", store)
    with pytest.raises(m.ContextError):
        enable(original, intent) if phase == "enable" else original.restore()
    assert fired[0].outcome[0] == "SUCCEEDED" and original.privilege_state == "UNKNOWN"
    before = list(sdk.calls)
    with pytest.raises(m.ContextError, match="postimage"):
        original.reconcile()
    with pytest.raises(m.ContextError): original._snapshot()
    assert sdk.calls == before


@pytest.mark.parametrize("phase", ["enable", "restore"])
def test_return_normalization_raises_after_effect_retains_original_raw_holder_and_scope(phase):
    original, sdk, intent, raw = setup()
    if phase == "restore": enable(original, intent)
    class Opaque:
        @property
        def value(self):
            raise OSError("SOFTWARE normalization result unavailable")
    value = Opaque()
    sdk.adjust_fault = (value,)
    with pytest.raises(m.ContextError):
        enable(original, intent) if phase == "enable" else original.restore()
    effect = original.restore_effect or original.enable_effect
    assert effect.call.raw is value and effect.call.outcome is None
    assert effect.call.outputs[1] is effect.previous and effect.previous.count == 1
    before = list(sdk.calls)
    with pytest.raises(m.ContextError): original.reconcile()
    with pytest.raises(m.ContextError): original.close()
    assert sdk.calls == before and original in m.retained_contexts()


@pytest.mark.parametrize("stage", ["before", "after"])
def test_root_pop_ack_fault_preserves_original_owner_and_never_recloses(monkeypatch, stage):
    fired = []
    class Roots(dict):
        def pop(self, key):
            if not fired:
                fired.append(self[key])
                if stage == "after": super().pop(key)
                raise OSError("SOFTWARE original root pop ack failure")
            return super().pop(key)
    monkeypatch.setattr(m, "_ROOTS", Roots())
    original, sdk, _, _ = setup()
    with pytest.raises(m.ContextError): original.close()
    assert m._ROOTS[id(original)] is original
    before = list(sdk.calls)
    original.reconcile()
    assert sdk.calls == before and not m.retained_contexts()


@pytest.mark.parametrize("binding", ["request", "previous", "used", "postimage", "call", "function", "args"])
def test_original_mutation_outputs_cannot_be_replaced_for_compare_restore(binding):
    original, sdk, intent, _ = setup()
    enable(original, intent)
    effect = original.enable_effect
    if binding == "request": effect.request = copy.copy(effect.request)
    elif binding == "previous": effect.previous = copy.copy(effect.previous)
    elif binding == "used": effect.used = m.D(effect.used.value)
    elif binding == "postimage": effect.post = replace(effect.post)
    elif binding == "call": effect.call = copy.copy(effect.call)
    elif binding == "function": effect.call.function = lambda *args: pytest.fail("replacement effect")
    else: effect.call.args = tuple(list(effect.call.args))
    before = list(sdk.calls)
    with pytest.raises(m.ContextError): original.restore()
    assert sdk.calls == before and original in m.retained_contexts()


def test_native_prepare_constructor_uses_only_closed_readonly_sdk_software_boundary(monkeypatch):
    """Production acquisition path, all OS leaves explicitly replaced. NOT native."""
    sdk = SDK()
    loaded = []
    class Function:
        def __init__(self, name): self.callback = sdk.sdk[name]
        def __call__(self, *args): return self.callback(*args)
    functions = {name: Function(name) for name in m.READONLY_SDK}
    class Library:
        def __getattr__(self, name):
            assert name in m.READONLY_SDK, "readonly prepare loaded unapproved SDK name: " + name
            loaded.append(name)
            return functions[name]
    monkeypatch.setattr(m, "os", SimpleNamespace(name="nt"))
    monkeypatch.setattr(C, "WinDLL", lambda *args, **kwargs: Library(), raising=False)
    monkeypatch.setattr(C, "get_last_error", sdk.error, raising=False)
    monkeypatch.setattr(C, "set_last_error", lambda n: pytest.fail("readonly called TLS setter"), raising=False)
    doc, _ = intent_doc()
    raw = b.canonical(doc)
    original = m.prepare(b.parse_plan(raw), original_plan_bytes=raw)
    assert set(loaded) == set(m.READONLY_SDK) and original.api.adjust is None
    assert original.preview()["setter_dispatches"] == 0
    original.close()
    assert not sdk.adjusts and not m.retained_contexts()


def test_native_public_entry_has_no_incoming_provider_or_observation_parameter(monkeypatch):
    monkeypatch.setattr(m, "_Api", lambda: pytest.fail("invalid input reached native acquisition"))
    with pytest.raises(m.ContextError): m.prepare({}, original_plan_bytes=b"{}")
    with pytest.raises(TypeError): m.prepare({}, original_plan_bytes=b"{}", backend={})


@pytest.mark.parametrize("field", ["request-luid", "request-attrs", "previous-luid", "previous-attrs", "previous-count"])
def test_original_effect_storage_bytes_cannot_redirect_restore_or_other_privileges(field):
    original, sdk, intent, _ = setup()
    enable(original, intent)
    e = original.enable_effect
    if field == "request-luid": e.request.entry.luid.low += 1
    elif field == "request-attrs": e.request.entry.attributes ^= 2
    elif field == "previous-luid": e.previous.entry.luid.low += 1
    elif field == "previous-attrs": e.previous.entry.attributes ^= 2
    else: e.previous.count = 0
    before = list(sdk.calls)
    with pytest.raises(m.ContextError): original.restore()
    assert sdk.calls == before and len(sdk.adjusts) == 1


def test_plain_closed_metadata_is_not_original_handle_cleanup():
    original, sdk, _, _ = setup()
    original.token.closed = True
    with pytest.raises(m.ContextError): original.close()
    with pytest.raises(m.ContextError): original.reconcile()
    assert original in m.retained_contexts() and sdk.closed == []


def test_sdk_outcome_is_not_a_writable_diagnostic_receipt():
    original, sdk, intent, _ = setup()
    sdk.adjust_fault = "after-raise"
    with pytest.raises(m.ContextError): enable(original, intent)
    call = original.enable_effect.call
    with pytest.raises(AttributeError): call.outcome = ("SUCCEEDED", 1, 0)
    assert call.outcome is None and original.privilege_state == "UNKNOWN"


@pytest.mark.parametrize("nonzero", [1, 2, 0xFFFFFFFF])
def test_token_elevation_uses_exact_dword_and_documented_nonzero_semantics(nonzero):
    sdk = SDK(elevated=nonzero)
    original, sdk, _, _ = setup(sdk)
    call = next(c for c in original.calls if c.name == "GetTokenInformation" and c.args[1] == 20)
    assert call.args[3] == C.sizeof(m.D) and original.before.elevated is True
    original.close()
