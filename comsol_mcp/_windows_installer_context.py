"""Finite local installer TCB for an existing current-primary-token SeRestore.

Not imported by MCP/bootstrap/runtime. Intent/preview JSON is not a capability.
Native setters are source-only until explicit local installation authorization;
prepare/preview/close use only the closed readonly SDK list below. No token is
created/duplicated, no right assigned, and no service/LSA/host policy is touched.
"""
from __future__ import annotations

import ctypes as C
from dataclasses import asdict, dataclass
import os
import threading

from . import _windows_host_bootstrap as b
from ._windows_sid import is_canonical_sid

_NATIVE, _SOFTWARE = object(), object()
_ROOTS = {}
_LOCK = threading.RLock()
MAX_TOKEN = 65536
RESTORE = "SeRestorePrivilege"
READONLY_SDK = ("GetCurrentProcess", "GetCurrentProcessId", "GetCurrentThread",
                "GetProcessTimes", "OpenProcessToken", "OpenThreadToken",
                "GetTokenInformation", "LookupPrivilegeValueW", "CloseHandle")
D, B, H = C.c_uint32, C.c_int32, C.c_void_p


class ContextError(RuntimeError):
    def __init__(self, code, message, *, owner=None):
        super().__init__(message)
        self.code, self.owner = code, owner


def _fail(code, message):
    raise ContextError(code, message)


class _Luid(C.Structure):
    _fields_ = [("low", D), ("high", C.c_int32)]


class _Privilege(C.Structure):
    _fields_ = [("luid", _Luid), ("attributes", D)]


class _OnePrivilege(C.Structure):
    _fields_ = [("count", D), ("entry", _Privilege)]


class _SidAttributes(C.Structure):
    _fields_ = [("sid", H), ("attributes", D)]


class _Groups(C.Structure):
    _fields_ = [("count", D), ("first", _SidAttributes)]


class _Statistics(C.Structure):
    _fields_ = [("token_id", _Luid), ("authentication_id", _Luid),
                ("expiration", C.c_int64), ("token_type", C.c_int32),
                ("impersonation_level", C.c_int32), ("dynamic_charged", D),
                ("dynamic_available", D), ("group_count", D),
                ("privilege_count", D), ("modified_id", _Luid)]


def _luid(value):
    return value.low, value.high


def _scalar(raw, low, high):
    # Exactly one consumption of a ctypes/software result wrapper.
    value = getattr(raw, "value", raw)
    if type(value) is not int or not low <= value <= high:
        _fail("SDK_RETURN_UNKNOWN", "strict bounded original SDK scalar required")
    return value


def _sid(buffer, used, address, floor):
    base = C.addressof(buffer)
    if type(address) is not int or not base + floor <= address <= base + used - 8:
        _fail("TOKEN_BOUNDS", "SID escapes its original output storage")
    head = C.string_at(address, 8)
    extent = 8 + 4 * head[1]
    if head[0] != 1 or not 1 <= head[1] <= 15 or address + extent > base + used:
        _fail("TOKEN_BOUNDS", "bounded canonical SID required")
    raw = C.string_at(address, extent)
    authority = int.from_bytes(raw[2:8], "big")
    auth = str(authority) if authority < 2**32 else f"0x{authority:012x}"
    text = "S-1-" + auth + "".join("-" + str(int.from_bytes(raw[p:p+4], "little"))
                                  for p in range(8, extent, 4))
    if not is_canonical_sid(text):
        _fail("TOKEN_BOUNDS", "noncanonical native SID")
    return text


def _privileges(raw):
    if type(raw) is not bytes or len(raw) < 4:
        _fail("TOKEN_BOUNDS", "privilege header required")
    count = int.from_bytes(raw[:4], "little")
    if count > 512 or len(raw) != 4 + count * C.sizeof(_Privilege):
        _fail("TOKEN_BOUNDS", "closed bounded TOKEN_PRIVILEGES extent required")
    rows = []
    for n in range(count):
        item = _Privilege.from_buffer_copy(raw, 4 + n * C.sizeof(_Privilege))
        if item.attributes & ~0x80000007:
            _fail("TOKEN_ATTRIBUTES", "unsupported privilege attribute bits")
        rows.append((*_luid(item.luid), item.attributes))
    if len({r[:2] for r in rows}) != len(rows):
        _fail("TOKEN_DUPLICATE", "duplicate privilege LUID")
    return tuple(sorted(rows))


def _intent(value, raw):
    if type(value) is not b.BootstrapIntent:
        _fail("INTENT_TYPE", "exact parsed local bootstrap intent required")
    # Existing parser sorts files but hashes original inventory ordering;
    # retain original bytes rather than inventing a reconstructed plan/hash.
    parsed = b.parse_plan(raw)
    if parsed != value:
        _fail("INTENT_BINDING", "intent shape/hash differs from the existing parser")
    return value


@dataclass(frozen=True)
class _Snapshot:
    scope: tuple[int, int]
    token_id: tuple[int, int]
    authentication_id: tuple[int, int]
    modified_id: tuple[int, int]
    user: str
    elevated: bool
    groups: tuple
    privileges: tuple

    def unchanged_identity(self, other):
        return (type(other) is _Snapshot and self.scope == other.scope
                and self.token_id == other.token_id and self.authentication_id == other.authentication_id
                and self.user == other.user and self.elevated == other.elevated and self.groups == other.groups)


class _Api:
    def __init__(self):
        if os.name != "nt" or C.sizeof(H) != 8:
            _fail("WINDOWS_REQUIRED", "64-bit Windows SDK required")
        self.kernel, self.advapi = C.WinDLL("kernel32", use_last_error=True), C.WinDLL("advapi32", use_last_error=True)
        specs = {
            "GetCurrentProcess": (self.kernel, H, []),
            "GetCurrentProcessId": (self.kernel, D, []),
            "GetCurrentThread": (self.kernel, H, []),
            "GetProcessTimes": (self.kernel, B, [H, *([C.POINTER(b._FileTime)] * 4)]),
            "OpenProcessToken": (self.advapi, B, [H, D, C.POINTER(H)]),
            "OpenThreadToken": (self.advapi, B, [H, D, B, C.POINTER(H)]),
            "GetTokenInformation": (self.advapi, B, [H, C.c_int32, H, D, C.POINTER(D)]),
            "LookupPrivilegeValueW": (self.advapi, B, [C.c_wchar_p, C.c_wchar_p, C.POINTER(_Luid)]),
            "CloseHandle": (self.kernel, B, [H]),
        }
        self.sdk = {}
        for name, (dll, result, args) in specs.items():
            fn = getattr(dll, name)
            fn.restype, fn.argtypes = result, args
            self.sdk[name] = fn
        self.error, self.clear_error = C.get_last_error, C.set_last_error
        self.adjust = None
        self._freeze()

    def _freeze(self):
        self._sdk, self._bindings = self.sdk, dict(self.sdk)
        self._error, self._clear_error = self.error, self.clear_error

    def check(self):
        if (self.sdk is not self._sdk or set(self.sdk) != set(self._bindings)
                or any(self.sdk[k] is not f for k, f in self._bindings.items())
                or self.error is not self._error or self.clear_error is not self._clear_error):
            _fail("SDK_BINDING", "original installer SDK bindings changed")

    def mutation_function(self):
        self.check()
        if self.adjust is None:
            fn = self.advapi.AdjustTokenPrivileges  # loaded only by approved source setter
            fn.restype, fn.argtypes = B, [H, B, C.POINTER(_OnePrivilege), D,
                                        C.POINTER(_OnePrivilege), C.POINTER(D)]
            self.adjust = self._adjust = fn
        if self.adjust is not self._adjust:
            _fail("SDK_BINDING", "original AdjustTokenPrivileges changed")
        return self.adjust


class _SoftwareApi(_Api):
    """Private explicit software producer; cannot become native provenance."""
    def __init__(self, sdk, error, clear_error, *, seal):
        if seal is not _SOFTWARE:
            _fail("SOFTWARE_SEAL", "private software boundary required")
        self.sdk = {name: sdk[name] for name in READONLY_SDK}
        self.adjust = self._adjust = sdk["AdjustTokenPrivileges"]
        self.error, self.clear_error = error, clear_error
        self._freeze()


class _Call:
    def __init__(self, context, name, args, *, outputs=(), adjust=False, function=None):
        self.context, self.name, self.args, self.outputs = context, name, args, outputs
        self.function = context.api.sdk[name] if function is None else function
        self.adjust, self.entered, self.raw, self._result = adjust, False, None, None
        self._function, self._args, self._outputs = self.function, args, outputs
        context.calls.append(self)  # original args/output holders rooted before dispatch

    def check(self):
        if self.function is not self._function or self.args is not self._args or self.outputs is not self._outputs:
            _fail("SDK_BINDING", "original SDK call/output storage changed")

    @property
    def outcome(self):
        return self._result

    def run(self):
        self.check()
        if self.outcome is not None:
            if self.outcome[0] == "SUCCEEDED":
                return self.outcome[1]
            _fail(self.outcome[0], "original SDK refusal cannot be replayed")
        if self.entered:
            _fail("SDK_UNKNOWN", "lost original SDK result cannot be replayed")
        self.context._guard_sdk()
        if self.adjust:
            self.context.api.clear_error(0)
        self.entered = True
        self.raw = self.function(*self.args)
        if self.name in {"GetCurrentProcess", "GetCurrentThread"}:
            value = _scalar(self.raw, -2, 2**64-1)
            expected = H(-1 if self.name == "GetCurrentProcess" else -2).value
            success = value == expected
        elif self.name == "GetCurrentProcessId":
            value = _scalar(self.raw, 1, 2**32-1)
            success = True
        else:
            value = _scalar(self.raw, -(1 << 31), (1 << 31)-1)
            success = value != 0
        error = _scalar(self.context.api.error(), 0, 2**32-1)
        state = ("ERROR_NOT_ALL_ASSIGNED" if self.adjust and error == 1300 else
                 "SDK_REFUSED" if not success or self.adjust and error else "SUCCEEDED")
        self._result = (state, value, error)  # one normalized result, never consumed twice
        if state != "SUCCEEDED":
            _fail(state, f"{self.name} refused (Win32 {error})")
        return value


class _Handle:
    def __init__(self, context, call, slot):
        self.context, self.call, self.slot = context, call, slot
        self._context, self._call, self._slot = context, call, slot
        self.closer = context.api.sdk["CloseHandle"]
        self._closer = self.closer
        self.value, self.argument, self.close_call, self.closed = None, None, None, False
        context.handles.append(self)

    def capture(self):
        value = self.slot.value
        if type(value) is not int or not 0 < value < (1 << 64)-4:
            _fail("TOKEN_HANDLE_UNKNOWN", "original successful output lacks a legal HANDLE")
        self.value, self.argument = value, H(value)
        self._value, self._argument = value, self.argument

    def check(self):
        if (self.context is not self._context or self.call is not self._call or self.slot is not self._slot
                or self.closer is not self._closer):
            _fail("TOKEN_HANDLE_BINDING", "original token HANDLE/storage changed")
        if self.value is not None and (self.argument is not self._argument or self.value != self._value
                or self.argument.value != self.value or self.slot.value != self.value):
            _fail("TOKEN_HANDLE_BINDING", "original token HANDLE/storage value changed")

    def arg(self):
        self.check()
        if self.closed or self.argument is None:
            _fail("TOKEN_HANDLE_BINDING", "original token HANDLE is not live")
        return self.argument

    def close(self):
        self.check()
        if self.closed:
            if self.value is None:
                if self.slot.value is not None or self.call.outcome is None or self.call.outcome[0] != "SDK_REFUSED":
                    _fail("TOKEN_HANDLE_BINDING", "no-output bookkeeping lacks its original SDK refusal")
            elif self.close_call is None or self.close_call.outcome is None or self.close_call.outcome[0] != "SUCCEEDED":
                _fail("TOKEN_HANDLE_BINDING", "closed metadata is not a confirmed original CloseHandle")
            return
        self.context._guard_sdk()
        if self.closer is not self.context.api.sdk["CloseHandle"]:
            _fail("TOKEN_HANDLE_BINDING", "original token closer changed")
        if self.close_call is None:
            self.close_call = _Call(self.context, "CloseHandle", (self.arg(),), outputs=(self.slot, self.argument), function=self.closer)
        self.close_call.run()
        self.context.checkpoint("close_confirmed", self)
        self.closed = True


class _Effect:
    def __init__(self, context, snapshot, attributes, prior):
        self.context, self.before = context, snapshot
        self.request, self.previous, self.used = _OnePrivilege(), _OnePrivilege(), D()
        self.request.count, self.request.entry.luid.low, self.request.entry.luid.high = 1, *context.restore_luid
        self.request.entry.attributes = attributes
        self.input_bytes = bytes(self.request)
        self.expected_previous, self.post = prior, None
        fn = context.api.mutation_function()
        self.call = _Call(context, "AdjustTokenPrivileges",
            (context.token.arg(), 0, C.byref(self.request), C.sizeof(self.previous), C.byref(self.previous), C.byref(self.used)),
            outputs=(self.request, self.previous, self.used, snapshot), adjust=True, function=fn)
        self.seal()

    def seal(self):
        self._storage = (self.request, self.previous, self.used, self.call, self.before)
        self.call._args, self.call._outputs = self.call.args, self.call.outputs
        self._post = None
        self.previous_image = None

    def check(self):
        if any(a is not z for a, z in zip((self.request, self.previous, self.used, self.call, self.before), self._storage)) or self.post is not self._post:
            _fail("EFFECT_BINDING", "original effect request/output/snapshot changed")
        self.call.check()
        if bytes(self.request) != self.input_bytes or (self.previous_image is not None and bytes(self.previous) != self.previous_image):
            _fail("EFFECT_BINDING", "original request or validated PreviousState bytes changed")

    def validate_previous(self):
        self.check()
        if bytes(self.request) != self.input_bytes or self.used.value != C.sizeof(self.previous):
            _fail("ADJUST_OUTPUT_UNKNOWN", "original request/previous-state extent changed")
        if _privileges(bytes(self.previous)) != ((*self.context.restore_luid, self.expected_previous),):
            _fail("ADJUST_OUTPUT_UNKNOWN", "actual PreviousState is not the one original SeRestore state")
        self.previous_image = bytes(self.previous)


class InstallerContext:
    """Original live local installer context, never a serialized permission."""
    def __init__(self, intent, api, *, seal):
        if seal not in {_NATIVE, _SOFTWARE} or type(api) is not (_Api if seal is _NATIVE else _SoftwareApi):
            _fail("CONTEXT_ORIGIN", "original native/software construction boundary required")
        self.intent = self._intent = intent
        self.api = self._api = api
        self._seal, self.thread = seal, threading.get_ident()
        self.calls, self.handles, self.scope = [], [], None
        self._calls, self._handles, self._roots = self.calls, self.handles, _ROOTS
        self._scope = None
        self.token = self._token = None
        self.before = self._before = None
        self.restore_luid = self._restore_luid = None
        self.enable_effect = self.restore_effect = None
        self._enable_effect = self._restore_effect = None
        self.state, self.privilege_state = "PREPARING", "KNOWN_UNMODIFIED"
        self.eligibility, self.cleanup_unknown = None, False
        self._retiring = False
        _ROOTS[id(self)] = self

    def checkpoint(self, event, original):
        """Metadata-only acknowledgement boundary; no SDK dispatch."""

    def _binding(self):
        if (type(self) is not InstallerContext or self._seal not in {_NATIVE, _SOFTWARE}
                or _ROOTS is not self._roots or _ROOTS.get(id(self)) is not self
                or self.calls is not self._calls or self.handles is not self._handles
                or self.thread != threading.get_ident() or self.api is not self._api
                or self.intent is not self._intent or self.token is not self._token
                or self.before is not self._before or self.restore_luid != self._restore_luid
                or self.scope != self._scope or self.enable_effect is not self._enable_effect
                or self.restore_effect is not self._restore_effect):
            _fail("CONTEXT_BINDING", "original local installer objects/thread/root changed")
        self.api.check()
        for call in self.calls:
            call.check()
        for effect in (self.enable_effect, self.restore_effect):
            if effect is not None:
                effect.check()

    def _guard_sdk(self):
        self._binding()
        if (self.privilege_state == "UNKNOWN" or self.cleanup_unknown
                or self.state in {"ACK_PENDING", "CLOSE_ACK_PENDING"}
                or any(c.entered and c.outcome is None for c in self.calls)):
            _fail("CONTEXT_UNKNOWN", "original unresolved context cannot query/replay/close for proof")

    def _conflict(self):
        for old in _ROOTS.values():
            if old is not self and old.scope == self.scope and old.privilege_state in {
                    "MUTATING", "UNKNOWN", "OWNED_ENABLED", "OWNED_ENABLED_UNVERIFIED", "RESTORING"}:
                _fail("PRIMARY_TOKEN_SCOPE_FENCED", "original PID/birth primary-token effect remains owned/unknown")

    def _call(self, name, args=(), outputs=()):
        return _Call(self, name, args, outputs=outputs).run()

    def _process(self):
        pid = self._call("GetCurrentProcessId")
        pseudo = H(self._call("GetCurrentProcess"))
        times = tuple(b._FileTime() for _ in range(4))
        self._call("GetProcessTimes", (pseudo, *(C.byref(t) for t in times)), times)
        birth = int.from_bytes(bytes(times[0]), "little")
        if not birth or any(bytes(times[1])):
            _fail("PROCESS_IDENTITY", "actual live current process creation time required")
        return (pid, birth), pseudo

    def _no_impersonation(self):
        pseudo = H(self._call("GetCurrentThread"))
        slot = H()
        call = _Call(self, "OpenThreadToken", (pseudo, 8, 1, C.byref(slot)), outputs=(slot,))
        handle = _Handle(self, call, slot)
        try:
            call.run()
        except ContextError:
            if call.outcome == ("SDK_REFUSED", 0, 1008) and slot.value is None:
                handle.closed = True
                return
            raise
        handle.capture()
        _fail("THREAD_IMPERSONATION", "installer must use the actual current primary token without impersonation")

    def _info(self, handle, level):
        # TOKEN_ELEVATION is one fixed DWORD; Windows rejects an oversized
        # TokenInformationLength with ERROR_BAD_LENGTH for this class.
        limit = C.sizeof(D) if level == 20 else MAX_TOKEN
        buf, used = C.create_string_buffer(limit), D()
        self._call("GetTokenInformation", (handle, level, buf, len(buf), C.byref(used)), (buf, used))
        if not 4 <= used.value <= len(buf):
            _fail("TOKEN_BOUNDS", "token result exceeds original fixed output storage")
        return buf, used.value

    def _stats(self, handle):
        buf, used = self._info(handle, 10)
        if used != C.sizeof(_Statistics):
            _fail("TOKEN_BOUNDS", "exact TOKEN_STATISTICS extent required")
        value = _Statistics.from_buffer_copy(buf)
        if value.token_type != 1:
            _fail("PRIMARY_TOKEN_REQUIRED", "actual TOKEN_PRIMARY required")
        return (_luid(value.token_id), _luid(value.authentication_id), _luid(value.modified_id),
                value.group_count, value.privilege_count)

    def _snapshot(self):
        scope, _ = self._process()
        if scope != self.scope:
            _fail("PROCESS_IDENTITY", "original current process PID/birth drifted")
        self._no_impersonation()
        token = self.token.arg()
        before = self._stats(token)
        user, n = self._info(token, 1)
        if n < C.sizeof(_SidAttributes):
            _fail("TOKEN_BOUNDS", "TOKEN_USER header truncated")
        sid = _sid(user, n, _SidAttributes.from_buffer_copy(user).sid, C.sizeof(_SidAttributes))
        groups, n = self._info(token, 2)
        count = D.from_buffer_copy(groups).value
        floor = _Groups.first.offset + count * C.sizeof(_SidAttributes)
        if count > 1024 or floor > n:
            _fail("TOKEN_BOUNDS", "TOKEN_GROUPS count exceeds original buffer")
        rows = []
        for i in range(count):
            item = _SidAttributes.from_buffer_copy(groups, _Groups.first.offset + i * C.sizeof(_SidAttributes))
            rows.append((_sid(groups, n, item.sid, floor), item.attributes))
        if len({v[0] for v in rows}) != count:
            _fail("TOKEN_DUPLICATE", "duplicate native token group")
        privileges, n = self._info(token, 3)
        values = _privileges(bytes(privileges.raw[:n]))
        elevated, n = self._info(token, 20)
        elevation = D.from_buffer_copy(elevated).value
        if n != C.sizeof(D):
            _fail("TOKEN_BOUNDS", "exact TOKEN_ELEVATION DWORD required")
        after = self._stats(token)
        # Microsoft SDK processthreadsapi.h inline GetCurrentProcessToken:
        # (HANDLE)(LONG_PTR)-4, QUERY only. Never closed/duplicated or adjusted.
        current = self._stats(H(-4))
        if before != after or current != after or before[3:] != (count, len(values)):
            _fail("TOKEN_DRIFT", "full original/current-primary token changed during its read")
        if self._process()[0] != scope:
            _fail("PROCESS_IDENTITY", "current process changed during token read")
        return _Snapshot(scope, before[0], before[1], before[2], sid, bool(elevation), tuple(sorted(rows)), values)

    def _prepare(self):
        self.scope, process = self._process()
        self._scope = self.scope
        self._conflict()  # before any token acquisition or fresh token proof
        self._no_impersonation()
        slot = H()
        call = _Call(self, "OpenProcessToken", (process, 0x28, C.byref(slot)), outputs=(slot,))
        self.token = self._token = _Handle(self, call, slot)
        call.run()
        self.token.capture()
        luid = _Luid()
        self._call("LookupPrivilegeValueW", (None, RESTORE, C.byref(luid)), (luid,))
        self.restore_luid = self._restore_luid = _luid(luid)
        self.before = self._before = self._snapshot()
        attrs = dict((r[:2], r[2]) for r in self.before.privileges).get(self.restore_luid)
        admin = any(sid == b.BA and flags & 4 and not flags & 16 for sid, flags in self.before.groups)
        self.eligibility = ("SYSTEM_NO_ADJUST" if self.before.user == b.SY else
            "REFUSED_NOT_ELEVATED_ADMIN" if not self.before.elevated or not admin else
            "REFUSED_SE_RESTORE_UNASSIGNED" if attrs is None or attrs & 4 else
            "ALREADY_ENABLED_NO_ADJUST" if attrs & 2 else "ASSIGNED_DISABLED_ONE_ENABLE")
        self.state = "PREPARED_READONLY"
        return self

    def _approval(self, intent, sha):
        self._binding()
        if intent is not self._intent or type(sha) is not str or sha != self._intent.sha256:
            _fail("LOCAL_INTENT_APPROVAL", "exact original local intent and explicit reviewed SHA required")

    def _effect_error(self, error):
        effect = self.restore_effect or self.enable_effect
        if effect is not None and effect.call.entered:
            if effect.call.outcome is None or effect.call.outcome[0] != "SUCCEEDED":
                self.privilege_state, self.state = "UNKNOWN", "EFFECT_UNKNOWN"
            else:
                self.privilege_state = ("UNKNOWN" if effect.post is None else
                    "KNOWN_RESTORED" if self.restore_effect is not None else "OWNED_ENABLED")
                self.state = "ACK_PENDING"
        elif effect is not None:
            self.privilege_state = "OWNED_ENABLED" if self.restore_effect is not None else "KNOWN_UNMODIFIED"
            self.state = "PRE_EFFECT_REFUSED"
        error = ContextError(getattr(error, "code", "CONTEXT_FAILED"), str(error), owner=self)
        raise error

    def enable(self, intent, *, approved_intent_sha256):
        with _LOCK:
            self._approval(intent, approved_intent_sha256)
            if self.state != "PREPARED_READONLY" or self.enable_effect is not None:
                _fail("ENABLE_NO_REPEAT", "original context cannot enable again")
            self._conflict()
            if self.eligibility.startswith("REFUSED"):
                _fail(self.eligibility, "current token cannot acquire or assign installer privilege")
            if self._snapshot() != self.before:
                self.state = "DRIFT_PRESERVED"
                _fail("TOKEN_DRIFT", "foreign token state preserved; no privilege change")
            if self.eligibility in {"SYSTEM_NO_ADJUST", "ALREADY_ENABLED_NO_ADJUST"}:
                self.state = "ACTIVE_UNCHANGED"
                return
            attrs = dict((r[:2], r[2]) for r in self.before.privileges)[self.restore_luid]
            effect = self.enable_effect = self._enable_effect = _Effect(self, self.before, attrs | 2, attrs)
            self.privilege_state = "MUTATING"
            try:
                effect.call.run()
                effect.validate_previous()
                post = self._snapshot()
                expected = tuple(sorted((*r[:2], r[2] | 2) if r[:2] == self.restore_luid else r for r in self.before.privileges))
                if not post.unchanged_identity(self.before) or post.privileges != expected or post.modified_id == self.before.modified_id:
                    _fail("POSTIMAGE_DRIFT", "actual token differs from the single-LUID enabled postimage")
                effect.post = effect._post = post  # complete native postimage before metadata ack
                self.checkpoint("enable_confirmed", effect)
                self.privilege_state, self.state = "OWNED_ENABLED", "ACTIVE_CHANGED"
            except BaseException as error:
                self._effect_error(error)

    def restore(self):
        with _LOCK:
            self._guard_sdk()
            if self.state == "ACTIVE_UNCHANGED":
                self.state = "RESTORED_UNCHANGED"
                return
            if self.state != "ACTIVE_CHANGED" or self.restore_effect is not None:
                _fail("RESTORE_NO_REPEAT", "original changed context/postimage required")
            effect = self.enable_effect
            if self._snapshot() != effect.post:
                self.state = "DRIFT_PRESERVED"
                _fail("TOKEN_DRIFT", "foreign postimage preserved without restoration")
            attrs = dict((r[:2], r[2]) for r in self.before.privileges)[self.restore_luid]
            original = self.restore_effect = self._restore_effect = _Effect(self, effect.post, attrs, attrs | 2)
            # Restore from the actual original PreviousState, not JSON/data.
            original.request = effect.previous
            original.input_bytes = bytes(original.request)
            original.call.args = (self.token.arg(), 0, C.byref(original.request), C.sizeof(original.previous),
                                  C.byref(original.previous), C.byref(original.used))
            original.call.outputs = (original.request, original.previous, original.used, self.before)
            original.seal()
            self.privilege_state = "RESTORING"
            try:
                original.call.run()
                original.validate_previous()
                post = self._snapshot()
                if (not post.unchanged_identity(self.before) or post.privileges != self.before.privileges
                        or post.modified_id == effect.post.modified_id):
                    _fail("RESTORED_POSTIMAGE_DRIFT", "restore did not reproduce exact original privilege attributes")
                original.post = original._post = post
                self.checkpoint("restore_confirmed", original)
                self.privilege_state, self.state = "KNOWN_RESTORED", "RESTORED"
            except BaseException as error:
                self._effect_error(error)

    def reconcile(self):
        """Metadata only. Never acquires/queries/enables/restores/closes a token."""
        with _LOCK:
            self._binding()
            if self.state == "ACK_PENDING":
                effect = self.restore_effect or self.enable_effect
                if effect is None or effect.call.outcome is None or effect.call.outcome[0] != "SUCCEEDED":
                    _fail("EFFECT_UNKNOWN", "no cached normalized success")
                if effect.post is None:
                    _fail("POSTIMAGE_NOT_VERIFIED", "known SDK result lacks a complete cached postimage; no query for proof")
                if self.restore_effect is not None:
                    self.privilege_state, self.state = "KNOWN_RESTORED", "RESTORED"
                else:
                    self.privilege_state, self.state = "OWNED_ENABLED", "ACTIVE_CHANGED"
                return
            if self.state == "CLOSE_ACK_PENDING":
                for handle in self.handles:
                    if handle.close_call is not None and handle.close_call.outcome is not None and handle.close_call.outcome[0] == "SUCCEEDED":
                        if not handle.closed:
                            handle.arg()  # pure original binding check, not a re-query
                        handle.closed = True
                if all(h.closed for h in self.handles):
                    for handle in self.handles:
                        handle.close()  # closed branch verifies cached facts only
                    self._retire()
                return
            _fail("ACK_STATE", "original context has no known-success acknowledgement to finish")

    def _retire(self):
        self._retiring = True
        try:
            self.checkpoint("retire", self)
            self.state = "CLOSED"
            _ROOTS.pop(id(self))
        except BaseException:
            # Continuation of the exact original owner, never adoption.
            if _ROOTS.get(id(self)) not in (None, self):
                _fail("CONTEXT_BINDING", "original root slot replaced")
            _ROOTS[id(self)] = self
            self.state = "CLOSE_ACK_PENDING"
            raise

    def close(self):
        with _LOCK:
            self._guard_sdk()
            if self.privilege_state not in {"KNOWN_UNMODIFIED", "KNOWN_RESTORED"}:
                _fail("PRIVILEGE_OWNER_RETAINED", "restore or coordinate original token effect before close")
            try:
                for handle in reversed(self.handles):
                    if handle.value is None and not handle.closed:
                        if handle.call.outcome is not None and handle.call.outcome[0] == "SDK_REFUSED" and handle.slot.value is None:
                            handle.closed = True
                        else:
                            _fail("TOKEN_HANDLE_UNKNOWN", "unconfirmed original allocation cannot close")
                    handle.close()
                self._retire()
            except BaseException as error:
                opaque = any(h.close_call is not None and (h.close_call.outcome is None or h.close_call.outcome[0] != "SUCCEEDED") for h in self.handles)
                self.cleanup_unknown = opaque
                self.state = "CLOSE_UNKNOWN" if opaque else "CLOSE_ACK_PENDING"
                raise ContextError(getattr(error, "code", "CLOSE_UNKNOWN"), str(error), owner=self) from error

    def preview(self):
        return dict(authority="LOCAL_INSTALLER_TCB_ONLY_NOT_T037", origin="NATIVE_SDK" if self._seal is _NATIVE else "SOFTWARE_ONLY",
            state=self.state, privilege_state=self.privilege_state, cleanup_unknown=self.cleanup_unknown,
            scope=self.scope, intent_sha256=self._intent.sha256, eligibility=self.eligibility,
            token=asdict(self._before) if self._before is not None else None,
            SeRestore_luid=self._restore_luid, setter_dispatches=sum(c.entered and c.adjust for c in self.calls),
            apply_ready=False, T037="NOT_RUN")


def _prepare(intent, api, seal):
    original = InstallerContext(intent, api, seal=seal)
    try:
        return original._prepare()
    except BaseException as error:
        original.state = "PREPARE_REFUSED"
        raise ContextError(getattr(error, "code", "PREPARE_UNKNOWN"), str(error), owner=original) from error


def prepare(intent, *, original_plan_bytes):
    """Genuine current-token readonly preparation; no backend/provider injection."""
    with _LOCK:
        return _prepare(_intent(intent, original_plan_bytes), _Api(), _NATIVE)


def _software_prepare(intent, sdk, error, clear_error, *, original_plan_bytes, seal):
    with _LOCK:
        if seal is not _SOFTWARE:
            _fail("SOFTWARE_SEAL", "private software producer required")
        return _prepare(_intent(intent, original_plan_bytes), _SoftwareApi(sdk, error, clear_error, seal=seal), _SOFTWARE)


def retained_contexts():
    return tuple(_ROOTS.values())
