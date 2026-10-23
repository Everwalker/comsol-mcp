"""Narrow original-Bootstrap LABEL/byte writer. No standalone installer authority.

Future native execution only: this module does not acquire tokens, enable
privileges, start processes, or change preexisting filesystem security.
"""
from __future__ import annotations

import ctypes as C
from dataclasses import dataclass
import socket
import struct

from . import _windows_host_bootstrap as b
from ._windows_t037_mic import parse_label_security_descriptor

SDK_SOURCES = (
    "https://learn.microsoft.com/en-us/windows/win32/api/aclapi/nf-aclapi-setsecurityinfo",
    "https://learn.microsoft.com/en-us/windows/win32/secauthz/security-information",
    "https://learn.microsoft.com/en-us/windows/win32/api/securitybaseapi/nf-securitybaseapi-getkernelobjectsecurity",
    "https://learn.microsoft.com/en-us/windows/win32/api/winnt/ns-winnt-system_mandatory_label_ace",
)
LABEL_SECURITY_INFORMATION = 0x10
_MISSING_SIGNATURE = object()


def _signature(function):
    result = getattr(function, 'restype', _MISSING_SIGNATURE)
    arguments = getattr(function, 'argtypes', _MISSING_SIGNATURE)
    if type(arguments) in (list, tuple):
        arguments = tuple(arguments)  # Freeze mutable ctypes argtypes contents.
    elif arguments is not _MISSING_SIGNATURE and arguments is not None:
        b.fail('CONFIG_NATIVE_ORIGIN', 'original SDK signature required')
    return result, arguments


def _same_signature(actual, frozen):
    if type(frozen) is not tuple or len(frozen) != 2:
        return False
    if actual[0] is not frozen[0]:
        return False
    if type(actual[1]) is tuple and type(frozen[1]) is tuple:
        return len(actual[1]) == len(frozen[1]) and all(a is f for a, f in zip(actual[1], frozen[1]))
    return actual[1] is frozen[1]


def label_bytes(policy):
    """Literal single non-inheritable label, separately from OWNER/GROUP/DACL.

    Flags zero deliberately prevent propagation to already-created children.
    Every owned directory/file receives its own explicit approved label.
    """
    b._fields(policy, {"rid", "mask", "flags"})
    if (any(type(v) is not int for v in policy.values())
            or policy["rid"] not in (0x2000, 0x3000, 0x4000)
            or not 1 <= policy["mask"] <= 7 or not policy["mask"] & 1
            or policy["flags"] != 0):
        b.fail("LABEL_POLICY", "explicit Medium-or-higher NO_WRITE_UP, non-inheritable label required")
    sid = b._sid_bytes("S-1-16-" + str(policy["rid"]))
    ace = struct.pack("<BBHI", 0x11, 0, 8 + len(sid), policy["mask"]) + sid
    acl = struct.pack("<BBHHH", 2, 0, 8 + len(ace), 1, 0) + ace
    raw = struct.pack("<BBHIIII", 1, 0, 0x8010, 0, 0, 20, 0) + acl
    explicit_label(raw, expected=policy)
    return raw


def explicit_label(raw, *, expected=None):
    observation = parse_label_security_descriptor(raw)
    ace = observation.ace
    if (ace is None or ace.rid < 0x2000 or ace.rid > 0x4000
            or not ace.mask & 1 or ace.flags != 0 or observation.reasons):
        b.fail("LABEL_NOT_EXPLICIT", "default/absent/inherited/unsupported labels do not qualify")
    if expected is not None and (ace.rid, ace.mask, ace.flags) != tuple(expected[k] for k in ("rid", "mask", "flags")):
        b.fail("LABEL_READBACK", "actual explicit LABEL differs from approved policy")
    return observation


def host_domains():
    # Distinct domains are intentional; no hostname-to-fingerprint substitution.
    from ._windows_t037 import windows_host_fingerprint
    return {"hostname": socket.gethostname(), "machine_fingerprint_sha256": windows_host_fingerprint()}


@dataclass
class LabelCall:
    """Original dispatch and whole input storage, including a lost result."""
    owner: object
    handle: object
    argument: object
    value: int
    storage: object
    raw: bytes
    call: object
    before: object
    before_label: bytes
    original_root: object
    arguments: tuple
    function: object
    kind: str = 'LABEL'


class NativeCompanion:
    def __init__(self, original):
        if type(original) is not b.Bootstrap or original.intent.schema != b.SCHEMA_V2:
            b.fail("ORIGINAL_OWNER", "original versioned Bootstrap required")
        original._guard({"NEW"})
        self.original, self.backend, self.owner = original, original.backend, original.owner
        self.setter = self.backend.function("SetSecurityInfo")
        self.getter = self.backend.function("GetKernelObjectSecurity")
        self.bindings = (b._binding(self.setter), b._binding(self.getter))
        self.signatures = (_signature(self.setter), _signature(self.getter))
        self.label_calls = []

    def check(self):
        o = self.original
        if (type(o) is not b.Bootstrap or o.backend is not self.backend or o.owner is not self.owner
                or o._original_backend is not self.backend or o._original_owner is not self.owner
                or o._configuration is not o._original_configuration or o._configuration.native is not self
                or self.owner.api is not self.backend):
            b.fail("CONFIG_NATIVE_ORIGIN", "original Bootstrap SDK bindings required")
        setter = self.backend.function('SetSecurityInfo')
        getter = self.backend.function('GetKernelObjectSecurity')
        if (self.setter is not setter or self.getter is not getter
                or self.bindings != (b._binding(setter), b._binding(getter))
                or type(self.signatures) is not tuple or len(self.signatures) != 2
                or not all(_same_signature(_signature(fn), frozen)
                           for fn, frozen in zip((setter, getter), self.signatures))):
            b.fail("CONFIG_NATIVE_ORIGIN", "original Bootstrap SDK bindings required")
        self.owner.check()
        return setter, getter

    def read_label(self, handle):
        _, getter = self.check()
        self.original._original_handle(handle)
        storage, needed = C.create_string_buffer(b.MAX_SD), b.DWORD()
        arguments = (handle.arg(), LABEL_SECURITY_INFORMATION, storage, len(storage), C.byref(needed))
        self.check()
        b._Call(self.owner, 'GetKernelObjectSecurity', getter, arguments, 'BOOL', (storage, needed)).run()
        if not 20 <= needed.value <= len(storage):
            b.fail("LABEL_BOUNDS", "original LABEL SD exceeds bounded output storage")
        raw = bytes(storage.raw[:needed.value])
        parse_label_security_descriptor(raw)
        return raw

    def set_label(self, node, raw):
        setter, _ = self.check()
        if (node not in self.original.nodes.values() or node.handle is None
                or node.path != self.original.intent.destination(node.relative)):
            b.fail("CONFIG_NATIVE_ORIGIN", "only exact newly created original nodes can be labelled")
        self.original._original_handle(node.handle)
        explicit_label(raw)
        directory = node.relative in self.original._configuration.directories
        before = self.backend.image(node.handle, node.path, directory)
        before_label = self.read_label(node.handle)
        storage = C.create_string_buffer(raw, len(raw))
        argument = node.handle.arg()
        # aclapi.h: DWORD SetSecurityInfo(HANDLE, SE_OBJECT_TYPE,
        # SECURITY_INFORMATION, PSID, PSID, PACL, PACL). LABEL uses only pSacl.
        # No SACL_SECURITY_INFORMATION/PROTECTED_SACL/audit operation.
        args = (argument, 1, LABEL_SECURITY_INFORMATION, None, None, None,
                b.HANDLE(C.addressof(storage) + 20))
        self.check()
        call = b._Call(self.owner, "SetSecurityInfo", setter, args, "DWORD", (storage, raw, node))
        record = LabelCall(self.owner, node.handle, argument, node.handle.original_value, storage, raw, call,
                           before, before_label, self.original.nodes.get(''), args, setter)
        self.label_calls.append(record)  # strong original slot before potential setter effect
        call.run()  # DWORD 0 only; any failure/opaque effect is never replayed
        return record

    def create_bytes(self, node, raw, descriptor):
        self.check()
        if (type(raw) is not bytes or not 0 < len(raw) <= b.MAX_PLAN
                or node not in self.original.nodes.values() or node.handle is not None):
            b.fail("CONFIG_BYTES", "registered original generated node and frozen bounded bytes required")
        if descriptor != self.original._configuration.descriptors[False]:
            b.fail('CONFIG_SD', 'only the frozen original immutable config descriptor may be used')
        b.check_security(descriptor)
        handle = self.backend._open(node.path, False, write=True, create=True, raw_sd=descriptor)
        node.handle = handle
        buffer = C.create_string_buffer(raw, len(raw))
        done = 0
        while done < len(raw):
            count = b.DWORD()
            self.owner.call("WriteFile", (handle.arg(), b.HANDLE(C.addressof(buffer) + done),
                len(raw) - done, C.byref(count), None), outputs=(buffer, count, raw, node))
            if not 0 < count.value <= len(raw) - done:
                b.fail("CONFIG_WRITE_UNKNOWN", "original WriteFile progress is zero/overflow after dispatch")
            done += count.value
        self.owner.call("FlushFileBuffers", (handle.arg(),), outputs=(buffer, raw, node))
        return handle

    def set_descriptor(self, node, raw):
        setter, _ = self.check()
        config = self.original._configuration
        if node not in self.original.nodes.values() or raw != config.descriptor_for(node):
            b.fail('CONFIG_NATIVE_ORIGIN', 'only the exact approved original node descriptor is eligible')
        self.original._original_handle(node.handle)
        before = self.backend.image(node.handle, node.path, node.image.directory)
        label = self.read_label(node.handle)
        sd = b.decode_sd(raw)
        storage = C.create_string_buffer(raw,len(raw));base=C.addressof(storage)
        owner,group,dacl = sd['offsets']
        argument=node.handle.arg()
        args=(argument,1,0x80000007,b.HANDLE(base+owner),b.HANDLE(base+group),b.HANDLE(base+dacl),None)
        self.check()
        call=b._Call(self.owner,'SetSecurityInfo',setter,args,'DWORD',(storage,raw,node,before,label))
        record=LabelCall(self.owner,node.handle,argument,node.handle.original_value,storage,raw,call,
            before,label,self.original.nodes.get(''),args,setter,'DACL')
        self.label_calls.append(record)
        call.run()
        return record
