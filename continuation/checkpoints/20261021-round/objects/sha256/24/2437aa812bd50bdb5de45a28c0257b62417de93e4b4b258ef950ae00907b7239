"""R6C revision: one-shot task-private same-SID probe with best-effort diagnostics and an unconditional original-owner hold.

The helper is inert unless a fresh exact-source binding and Main once approval
are supplied. It never edits the caller's process token, user accounts, token
groups/privileges/session, or any filesystem ACL. The sole native process birth
is a hidden standard-library Python child, admitted once, whose only file write
is one fixed marker file below the task-private output root.

This source is a candidate for Main review. Do not execute the parent mode until
the exact helper/binding pair has a fresh approval. The child mode is likewise
restricted to the single fixed output file and is only launched by the approved
parent through CreateProcessWithTokenW.
"""
from __future__ import annotations

import ctypes as C
from ctypes import wintypes as W
import hashlib
import json
import ntpath
import os
from pathlib import Path
import signal
import subprocess
import sys
import threading
import time
from typing import Any, Mapping


TASK_ROOT = r"C:\Users\Everwalker\AppData\Local\cvw26-current-9f07a952265c"
PYTHON_EXE = r"C:\Users\Everwalker\AppData\Local\Programs\Python\Python312-comsol-mcp\python.exe"
PYTHON_VERSION = "3.12.10"
HELPER_PATH = TASK_ROOT + r"\helpers-primary-owner-r6c\windows_primary_token_owner_probe_r6c.py"
AUTHORITY_ROOT = TASK_ROOT + r"\authority-primary-owner-r6c"
BINDING_PATH = AUTHORITY_ROOT + r"\BINDING.json"
APPROVAL_PATH = AUTHORITY_ROOT + r"\MAIN_APPROVAL.json"
ONCE_PATH = AUTHORITY_ROOT + r"\ONCE_USED.json"
OUTPUT_ROOT = TASK_ROOT + r"\primary-owner-probe-r6c"
OUTPUT_FILE = OUTPUT_ROOT + r"\owner-probe.bin"
LEASE_EVENTS = OUTPUT_ROOT + r"\OWNER_LEASE_EVENTS.jsonl"
EXPECTED_USER_SID = "S-1-5-21-2844656816-882443001-4055095909-1001"

MAX_ACTIVE_SECONDS = 60
CHILD_EVENT_WAIT_MS = 30_000
PASSIVE_WAIT_SLICE_MS = 5_000
MAX_TOKEN_INFO_BYTES = 1_048_576
MAX_GROUPS = 4096
MAX_PRIVILEGES = 4096
PROBE_BYTES = b"cv3-primary-owner-probe-r6c\n"

TOKEN_QUERY = 0x0008
TOKEN_DUPLICATE = 0x0002
TOKEN_ASSIGN_PRIMARY = 0x0001
TOKEN_ADJUST_DEFAULT = 0x0080
TOKEN_USER = 1
TOKEN_GROUPS = 2
TOKEN_PRIVILEGES = 3
TOKEN_OWNER = 4
TOKEN_PRIMARY_GROUP = 5
TOKEN_TYPE = 8
TOKEN_STATISTICS = 10
TOKEN_SESSION_ID = 12
TOKEN_ELEVATION_TYPE = 18
TOKEN_ELEVATION = 20
TOKEN_INTEGRITY_LEVEL = 25
TOKEN_LOGON_SID = 28
TOKEN_PRIMARY = 1
SECURITY_IMPERSONATION = 2
SE_PRIVILEGE_ENABLED = 0x0002
ERROR_INSUFFICIENT_BUFFER = 122
ERROR_ALREADY_EXISTS = 183
SYNCHRONIZE = 0x00100000
WAIT_OBJECT_0 = 0x00000000
WAIT_TIMEOUT = 0x00000102
WAIT_FAILED = 0xFFFFFFFF
STILL_ACTIVE = 259
CREATE_NO_WINDOW = 0x08000000
OWNER_SECURITY_INFORMATION = 0x00000001
SE_FILE_OBJECT = 1
FILE_ATTRIBUTE_DIRECTORY = 0x00000010
FILE_ATTRIBUTE_REPARSE_POINT = 0x00000400
INVALID_FILE_ATTRIBUTES = 0xFFFFFFFF


class ProbeError(RuntimeError):
    pass


class LUID(C.Structure):
    _fields_ = [("LowPart", W.DWORD), ("HighPart", W.LONG)]


class LUID_AND_ATTRIBUTES(C.Structure):
    _fields_ = [("Luid", LUID), ("Attributes", W.DWORD)]


class SID_AND_ATTRIBUTES(C.Structure):
    _fields_ = [("Sid", C.c_void_p), ("Attributes", W.DWORD)]


class TOKEN_GROUPS_HEADER(C.Structure):
    _fields_ = [("GroupCount", W.DWORD), ("Groups", SID_AND_ATTRIBUTES * 1)]


class TOKEN_PRIVILEGES_HEADER(C.Structure):
    _fields_ = [("PrivilegeCount", W.DWORD), ("Privileges", LUID_AND_ATTRIBUTES * 1)]


class TOKEN_STATISTICS(C.Structure):
    _fields_ = [
        ("TokenId", LUID), ("AuthenticationId", LUID), ("ExpirationTime", C.c_int64),
        ("TokenType", W.DWORD), ("ImpersonationLevel", W.DWORD),
        ("DynamicCharged", W.DWORD), ("DynamicAvailable", W.DWORD),
        ("GroupCount", W.DWORD), ("PrivilegeCount", W.DWORD), ("ModifiedId", LUID),
    ]


class FILETIME(C.Structure):
    _fields_ = [("dwLowDateTime", W.DWORD), ("dwHighDateTime", W.DWORD)]


class STARTUPINFOW(C.Structure):
    _fields_ = [
        ("cb", W.DWORD), ("lpReserved", W.LPWSTR), ("lpDesktop", W.LPWSTR),
        ("lpTitle", W.LPWSTR), ("dwX", W.DWORD), ("dwY", W.DWORD),
        ("dwXSize", W.DWORD), ("dwYSize", W.DWORD), ("dwXCountChars", W.DWORD),
        ("dwYCountChars", W.DWORD), ("dwFillAttribute", W.DWORD), ("dwFlags", W.DWORD),
        ("wShowWindow", W.WORD), ("cbReserved2", W.WORD),
        ("lpReserved2", C.POINTER(W.BYTE)), ("hStdInput", W.HANDLE),
        ("hStdOutput", W.HANDLE), ("hStdError", W.HANDLE),
    ]


class PROCESS_INFORMATION(C.Structure):
    _fields_ = [
        ("hProcess", W.HANDLE), ("hThread", W.HANDLE),
        ("dwProcessId", W.DWORD), ("dwThreadId", W.DWORD),
    ]


# The local R6CProcessOwnerController in _main is the lifetime owner. These
# module lists are secondary diagnostics/retention indexes, never the only
# reference that can keep an unresolved PROCESS_INFORMATION alive.
_PROCESS_LEASES: list["ProcessLease"] = []
_PARTIAL_PROCESS_INFORMATION: list[Any] = []


def _defer_console_interrupts() -> tuple[dict[int, Any], list[int]]:
    """Defer Ctrl-C only while the native birth call's output handles are captured."""
    if threading.current_thread() is not threading.main_thread():
        _fail("native process birth must run on the main thread to retain PROCESS_INFORMATION")
    previous: dict[int, Any] = {}
    pending: list[int] = []

    def record_interrupt(signum, _frame):
        pending.append(int(signum))

    try:
        for name in ("SIGINT", "SIGBREAK"):
            signum = getattr(signal, name, None)
            if signum is None:
                continue
            previous[signum] = signal.getsignal(signum)
            signal.signal(signum, record_interrupt)
    except (OSError, RuntimeError, ValueError) as exc:
        for signum, handler in reversed(tuple(previous.items())):
            signal.signal(signum, handler)
        raise ProbeError("could not defer console interruption until original process handles are captured") from exc
    return previous, pending


def _restore_console_interrupts(previous: Mapping[int, Any]) -> None:
    errors = []
    for signum, handler in reversed(tuple(previous.items())):
        try:
            signal.signal(signum, handler)
        except BaseException as exc:
            errors.append(_safe_error_text(exc))
    if errors:
        _fail("could not restore the original console interrupt handlers", errors=errors)


def _fail(message: str, **details: Any) -> None:
    suffix = " " + json.dumps(details, sort_keys=True, ensure_ascii=False) if details else ""
    raise ProbeError(message + suffix)


def _last_win_error() -> int:
    getter = getattr(C, "get_last_error", None)
    return int(getter()) if callable(getter) else 0


def _strict_json(raw: bytes) -> dict[str, Any]:
    if not isinstance(raw, bytes) or not 1 <= len(raw) <= 1_048_576:
        _fail("authority JSON size is outside the fixed limit")

    def pairs(items):
        value = {}
        for key, item in items:
            if key in value:
                raise ValueError("duplicate JSON key")
            value[key] = item
        return value

    try:
        value = json.loads(
            raw.decode("utf-8", "strict"), object_pairs_hook=pairs,
            parse_constant=lambda item: (_ for _ in ()).throw(ValueError("invalid JSON constant " + item)),
        )
    except (UnicodeError, ValueError, json.JSONDecodeError) as exc:
        raise ProbeError("authority must be strict UTF-8 JSON without duplicate keys") from exc
    if not isinstance(value, dict):
        _fail("authority JSON root must be an object")
    return value


def _read_small_file(path: str, maximum: int = 1_048_576) -> bytes:
    if os.name != "nt":
        _fail("actual primary-token probe requires Windows")
    api = Win32()
    attrs = int(api.k.GetFileAttributesW(path))
    if attrs == INVALID_FILE_ATTRIBUTES or attrs & FILE_ATTRIBUTE_REPARSE_POINT or attrs & FILE_ATTRIBUTE_DIRECTORY:
        _fail("fixed authority/source path is absent, a directory, or a reparse point", path=path)
    with open(path, "rb", buffering=0) as stream:
        first = os.fstat(stream.fileno())
        raw = stream.read(maximum + 1)
        last = os.fstat(stream.fileno())
    if len(raw) > maximum or (first.st_dev, first.st_ino, first.st_size, first.st_mtime_ns) != (
        last.st_dev, last.st_ino, last.st_size, last.st_mtime_ns,
    ) or last.st_size != len(raw):
        _fail("fixed source/authority changed or exceeded its read bound", path=path)
    return raw


def _sha256(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _sid_text(raw: bytes) -> str:
    if len(raw) < 8 or raw[0] != 1 or raw[1] > 15 or len(raw) != 8 + 4 * raw[1]:
        _fail("SID bytes are malformed")
    authority = int.from_bytes(raw[2:8], "big")
    subauth = [int.from_bytes(raw[offset:offset + 4], "little") for offset in range(8, len(raw), 4)]
    return "S-1-" + str(authority) + "".join("-" + str(item) for item in subauth)


def _sid_bytes_in_buffer(buffer, size: int, pointer: int | None) -> bytes:
    if not pointer:
        _fail("token SID pointer is null")
    start = C.addressof(buffer)
    end = start + size
    if pointer < start or pointer > end - 8:
        _fail("token SID pointer is outside its original GetTokenInformation buffer")
    header = C.string_at(pointer, 8)
    if header[0] != 1 or header[1] > 15:
        _fail("token SID header is invalid")
    extent = 8 + 4 * header[1]
    if pointer + extent > end:
        _fail("token SID extent exceeds its original buffer")
    raw = C.string_at(pointer, extent)
    if _sid_text(raw) == "":
        _fail("SID conversion returned empty text")
    return raw


def _luid_value(value: LUID) -> tuple[int, int]:
    return int(value.LowPart), int(value.HighPart)


def _handle_value(value: Any) -> int:
    raw = getattr(value, "value", value)
    if isinstance(raw, bool) or not isinstance(raw, int) or raw <= 0:
        _fail("native handle is not a positive retained handle value")
    return raw


def _raw_handle_value(value: Any) -> int | None:
    """Read a returned HANDLE's exact scalar without validating a partial result."""
    raw = getattr(value, "value", value)
    return raw if type(raw) is int else None


class Win32:
    """The small read/create/wait API surface used by this probe only."""

    def __init__(self) -> None:
        if os.name != "nt":
            _fail("actual primary-token probe requires Windows")
        try:
            self.a = C.WinDLL("advapi32", use_last_error=True)
            self.k = C.WinDLL("kernel32", use_last_error=True)
        except (AttributeError, OSError) as exc:
            raise ProbeError("required Windows API DLL is unavailable") from exc
        h = W.HANDLE
        d = W.DWORD
        pp = C.POINTER(h)
        signatures = {
            "OpenProcessToken": ([h, d, pp], W.BOOL),
            "GetTokenInformation": ([h, d, C.c_void_p, d, C.POINTER(d)], W.BOOL),
            "LookupPrivilegeValueW": ([W.LPCWSTR, W.LPCWSTR, C.POINTER(LUID)], W.BOOL),
            "DuplicateTokenEx": ([h, d, C.c_void_p, d, d, pp], W.BOOL),
            "SetTokenInformation": ([h, d, C.c_void_p, d], W.BOOL),
            "CreateProcessWithTokenW": ([h, d, W.LPCWSTR, W.LPWSTR, d, C.c_void_p,
                                         W.LPCWSTR, C.POINTER(STARTUPINFOW),
                                         C.POINTER(PROCESS_INFORMATION)], W.BOOL),
            "GetNamedSecurityInfoW": ([W.LPWSTR, d, d, pp, pp, pp, pp, pp], d),
            "GetSecurityDescriptorOwner": ([h, pp, C.POINTER(W.BOOL)], W.BOOL),
            "IsValidSid": ([C.c_void_p], W.BOOL),
            "GetLengthSid": ([C.c_void_p], d),
            "CreateEventW": ([C.c_void_p, W.BOOL, W.BOOL, W.LPCWSTR], h),
            "OpenEventW": ([d, W.BOOL, W.LPCWSTR], h),
            "SetEvent": ([h], W.BOOL),
        }
        for name, (args, result) in signatures.items():
            fn = getattr(self.a if name in {
                "OpenProcessToken", "GetTokenInformation", "LookupPrivilegeValueW",
                "DuplicateTokenEx", "SetTokenInformation", "CreateProcessWithTokenW",
                "GetNamedSecurityInfoW", "GetSecurityDescriptorOwner", "IsValidSid", "GetLengthSid",
            } else self.k, name)
            fn.argtypes = args
            fn.restype = result
        self.k.GetCurrentProcess.argtypes = []
        self.k.GetCurrentProcess.restype = h
        self.k.GetProcessId.argtypes = [h]
        self.k.GetProcessId.restype = d
        self.k.GetProcessTimes.argtypes = [h, C.POINTER(FILETIME), C.POINTER(FILETIME),
                                           C.POINTER(FILETIME), C.POINTER(FILETIME)]
        self.k.GetProcessTimes.restype = W.BOOL
        self.k.QueryFullProcessImageNameW.argtypes = [h, d, W.LPWSTR, C.POINTER(d)]
        self.k.QueryFullProcessImageNameW.restype = W.BOOL
        self.k.WaitForSingleObject.argtypes = [h, d]
        self.k.WaitForSingleObject.restype = d
        self.k.GetExitCodeProcess.argtypes = [h, C.POINTER(d)]
        self.k.GetExitCodeProcess.restype = W.BOOL
        self.k.CloseHandle.argtypes = [h]
        self.k.CloseHandle.restype = W.BOOL
        self.k.LocalFree.argtypes = [C.c_void_p]
        self.k.LocalFree.restype = C.c_void_p
        self.k.GetFileAttributesW.argtypes = [W.LPCWSTR]
        self.k.GetFileAttributesW.restype = d

    @staticmethod
    def _check(result: Any, name: str) -> None:
        if not result:
            _fail(name + " failed", winerror=int(C.get_last_error()))

    def token_info(self, token: Any, info_class: int):
        needed = W.DWORD()
        ok = self.a.GetTokenInformation(token, info_class, None, 0, C.byref(needed))
        error = int(C.get_last_error())
        if ok or error != ERROR_INSUFFICIENT_BUFFER or not 4 <= int(needed.value) <= MAX_TOKEN_INFO_BYTES:
            _fail("bounded GetTokenInformation size query was not confirmed", info_class=info_class,
                  winerror=error, required=int(needed.value))
        buffer = C.create_string_buffer(int(needed.value))
        self._check(self.a.GetTokenInformation(token, info_class, buffer, needed.value, C.byref(needed)),
                    "GetTokenInformation")
        if not 4 <= int(needed.value) <= len(buffer):
            _fail("GetTokenInformation returned an invalid byte count", info_class=info_class)
        return buffer, int(needed.value)

    def sid_info(self, token: Any, info_class: int) -> bytes:
        buffer, size = self.token_info(token, info_class)
        pointer = C.cast(buffer, C.POINTER(C.c_void_p)).contents.value
        raw = _sid_bytes_in_buffer(buffer, size, pointer)
        self._check(self.a.IsValidSid(pointer), "IsValidSid")
        if int(self.a.GetLengthSid(pointer)) != len(raw):
            _fail("GetLengthSid disagrees with the bounded token SID")
        return raw

    def groups_info(self, token: Any, info_class: int) -> tuple[tuple[str, int], ...]:
        buffer, size = self.token_info(token, info_class)
        head = TOKEN_GROUPS_HEADER.from_buffer(buffer)
        count = int(head.GroupCount)
        offset = TOKEN_GROUPS_HEADER.Groups.offset
        stride = C.sizeof(SID_AND_ATTRIBUTES)
        if count > MAX_GROUPS or offset + count * stride > size:
            _fail("token groups are outside the bounded ABI extent", group_count=count, size=size)
        values = []
        for index in range(count):
            item = SID_AND_ATTRIBUTES.from_buffer(buffer, offset + index * stride)
            sid = _sid_bytes_in_buffer(buffer, size, int(item.Sid or 0))
            self._check(self.a.IsValidSid(item.Sid), "IsValidSid(token group)")
            if int(self.a.GetLengthSid(item.Sid)) != len(sid):
                _fail("GetLengthSid disagrees with a bounded token group SID")
            values.append((_sid_text(sid), int(item.Attributes)))
        return tuple(sorted(values))

    def privileges_info(self, token: Any) -> tuple[tuple[int, int, int], ...]:
        buffer, size = self.token_info(token, TOKEN_PRIVILEGES)
        head = TOKEN_PRIVILEGES_HEADER.from_buffer(buffer)
        count = int(head.PrivilegeCount)
        offset = TOKEN_PRIVILEGES_HEADER.Privileges.offset
        stride = C.sizeof(LUID_AND_ATTRIBUTES)
        if count > MAX_PRIVILEGES or offset + count * stride > size:
            _fail("token privileges are outside the bounded ABI extent", privilege_count=count, size=size)
        rows = []
        for index in range(count):
            item = LUID_AND_ATTRIBUTES.from_buffer(buffer, offset + index * stride)
            low, high = _luid_value(item.Luid)
            rows.append((low, high, int(item.Attributes)))
        return tuple(sorted(rows))

    def scalar_info(self, token: Any, info_class: int) -> int:
        buffer, size = self.token_info(token, info_class)
        if size != C.sizeof(W.DWORD):
            _fail("scalar token-information ABI has unexpected size", info_class=info_class, size=size)
        return int(W.DWORD.from_buffer(buffer).value)

    def statistics_info(self, token: Any) -> TOKEN_STATISTICS:
        buffer, size = self.token_info(token, TOKEN_STATISTICS)
        if size != C.sizeof(TOKEN_STATISTICS):
            _fail("TOKEN_STATISTICS ABI has unexpected size", size=size, expected=C.sizeof(TOKEN_STATISTICS))
        return TOKEN_STATISTICS.from_buffer_copy(buffer)

    def token_snapshot(self, token: Any) -> dict[str, Any]:
        stats = self.statistics_info(token)
        snapshot = {
            "user_sid": _sid_text(self.sid_info(token, TOKEN_USER)),
            "owner_sid": _sid_text(self.sid_info(token, TOKEN_OWNER)),
            "primary_group_sid": _sid_text(self.sid_info(token, TOKEN_PRIMARY_GROUP)),
            "groups": self.groups_info(token, TOKEN_GROUPS),
            "privileges": self.privileges_info(token),
            "token_id": _luid_value(stats.TokenId),
            "authentication_id": _luid_value(stats.AuthenticationId),
            "modified_id": _luid_value(stats.ModifiedId),
            "token_type": self.scalar_info(token, TOKEN_TYPE),
            "session_id": self.scalar_info(token, TOKEN_SESSION_ID),
            "elevation_type": self.scalar_info(token, TOKEN_ELEVATION_TYPE),
            "elevated": bool(self.scalar_info(token, TOKEN_ELEVATION)),
            "integrity_sid": _sid_text(self.sid_info(token, TOKEN_INTEGRITY_LEVEL)),
            "logon_sids": self.groups_info(token, TOKEN_LOGON_SID),
        }
        return snapshot

    def process_identity(self, process_handle: Any) -> dict[str, Any]:
        pid = int(self.k.GetProcessId(process_handle))
        if pid <= 0:
            _fail("GetProcessId on original CreateProcessWithTokenW handle returned no PID",
                  winerror=int(C.get_last_error()))
        creation = FILETIME()
        exit_time = FILETIME()
        kernel = FILETIME()
        user = FILETIME()
        self._check(self.k.GetProcessTimes(process_handle, C.byref(creation), C.byref(exit_time),
                                          C.byref(kernel), C.byref(user)), "GetProcessTimes")
        birth = (int(creation.dwHighDateTime) << 32) | int(creation.dwLowDateTime)
        if birth <= 0:
            _fail("GetProcessTimes returned no exact process birth FILETIME")
        buffer = C.create_unicode_buffer(32768)
        count = W.DWORD(len(buffer))
        self._check(self.k.QueryFullProcessImageNameW(process_handle, 0, buffer, C.byref(count)),
                    "QueryFullProcessImageNameW")
        image = buffer.value
        if not image or not ntpath.isabs(image):
            _fail("QueryFullProcessImageNameW returned no absolute image path")
        return {"pid": pid, "birth_filetime_100ns": birth, "image_path": image}

    def child_token_snapshot(self, process_handle: Any) -> dict[str, Any]:
        token = W.HANDLE()
        self._check(self.a.OpenProcessToken(process_handle, TOKEN_QUERY, C.byref(token)), "OpenProcessToken(child)")
        if not token:
            _fail("OpenProcessToken(child) returned no handle")
        try:
            return self.token_snapshot(token)
        finally:
            self._check(self.k.CloseHandle(token), "CloseHandle(child token)")

    def file_owner_sid(self, path: str) -> str:
        owner = W.HANDLE()
        descriptor = W.HANDLE()
        status = int(self.a.GetNamedSecurityInfoW(
            path, SE_FILE_OBJECT, OWNER_SECURITY_INFORMATION,
            C.byref(owner), None, None, None, C.byref(descriptor),
        ))
        if status != 0:
            _fail("GetNamedSecurityInfoW(file owner) failed", winerror=status)
        try:
            if not owner or not descriptor:
                _fail("GetNamedSecurityInfoW returned an incomplete owner descriptor")
            valid = W.BOOL()
            sid_pointer = W.HANDLE()
            self._check(self.a.GetSecurityDescriptorOwner(descriptor, C.byref(sid_pointer), C.byref(valid)),
                        "GetSecurityDescriptorOwner")
            if not valid.value or not sid_pointer:
                _fail("GetSecurityDescriptorOwner returned no valid owner SID")
            length = int(self.a.GetLengthSid(sid_pointer))
            if not 8 <= length <= 68:
                _fail("file owner SID length is outside the supported SID bound", length=length)
            raw = C.string_at(sid_pointer, length)
            self._check(self.a.IsValidSid(sid_pointer), "IsValidSid(file owner)")
            return _sid_text(raw)
        finally:
            if self.k.LocalFree(descriptor):
                _fail("LocalFree(security descriptor) did not confirm release")

    def close(self, handle: Any, label: str) -> None:
        if handle:
            self._check(self.k.CloseHandle(handle), "CloseHandle(" + label + ")")


def _assert_duplicate_owner_transition(
    primary_before: Mapping[str, Any], primary_after: Mapping[str, Any],
    duplicate_before: Mapping[str, Any], duplicate_after: Mapping[str, Any],
    *, expected_user_sid: str,
) -> None:
    # The original primary is an invariant. DuplicateTokenEx changes token ID;
    # SetTokenInformation(TokenOwner) may change ModifiedId, so compare only
    # identity/security fields that must stay fixed across the duplicate.
    if dict(primary_before) != dict(primary_after):
        _fail("original process primary token changed during duplicate-only owner assignment")
    if duplicate_before.get("user_sid") != expected_user_sid or duplicate_after.get("user_sid") != expected_user_sid:
        _fail("duplicate primary token user SID differs from the existing TokenUser SID")
    if duplicate_after.get("owner_sid") != expected_user_sid:
        _fail("duplicate TokenOwner did not become its existing TokenUser SID")
    # TOKEN_OWNER accepts a SID already represented by TokenUser or one of
    # TokenGroups. The exact current TokenUser SID above is sufficient; it need
    # not also be duplicated in TOKEN_GROUPS.
    invariant_fields = (
        "user_sid", "groups", "privileges", "primary_group_sid", "token_id", "authentication_id",
        "token_type", "session_id", "elevation_type", "elevated", "integrity_sid", "logon_sids",
    )
    changed = [field for field in invariant_fields if duplicate_before.get(field) != duplicate_after.get(field)]
    if changed:
        _fail("duplicate owner update changed fields other than TokenOwner", changed=changed)
    if duplicate_after.get("token_type") != TOKEN_PRIMARY:
        _fail("DuplicateTokenEx did not produce a primary token")


def _assert_child_identity(
    process: Mapping[str, Any], token: Mapping[str, Any],
    *, expected_user_sid: str, expected_session: int, expected_image: str,
) -> None:
    if ntpath.normcase(process.get("image_path", "")) != ntpath.normcase(expected_image):
        _fail("original process handle reports a different executable image")
    if token.get("user_sid") != expected_user_sid or token.get("owner_sid") != expected_user_sid:
        _fail("child token User/Owner SID did not match the existing user SID")
    if token.get("session_id") != expected_session or token.get("token_type") != TOKEN_PRIMARY:
        _fail("child token type or caller session differs from the exact source token")


def _assert_child_token_matches_duplicate(
    duplicate: Mapping[str, Any], child: Mapping[str, Any],
) -> None:
    # The process token may have its own TokenId/ModifiedId, but its security
    # context must otherwise be the exact owner-updated duplicate supplied to
    # CreateProcessWithTokenW.
    fields = (
        "user_sid", "owner_sid", "primary_group_sid", "groups", "privileges",
        "authentication_id", "token_type", "session_id", "elevation_type",
        "elevated", "integrity_sid", "logon_sids",
    )
    changed = [field for field in fields if duplicate.get(field) != child.get(field)]
    if changed:
        _fail("child process token differs from the exact owner-updated duplicate", changed=changed)


class ProcessLease:
    """Strong owner for original PROCESS_INFORMATION handles until terminal."""

    def __init__(self, api: Win32, process_info: PROCESS_INFORMATION, *,
                 command_line: str, started_monotonic: float) -> None:
        self.api = api
        self.process_info = process_info
        self.process_handle = process_info.hProcess
        self.thread_handle = process_info.hThread
        self.pid = int(process_info.dwProcessId)
        self.thread_id = int(process_info.dwThreadId)
        self.command_line = command_line
        self.started_monotonic = started_monotonic
        self.identity: dict[str, Any] | None = None
        self.child_token: dict[str, Any] | None = None
        self.state = "CREATED_UNVERIFIED"
        self.deadline_exceeded = False
        self.terminal_code: int | None = None
        self.terminal_observed = False
        if not self.process_handle:
            _fail("CreateProcessWithTokenW returned no original process handle")
        _PROCESS_LEASES.append(self)

    def original_handles(self) -> dict[str, Any]:
        return {
            "process_handle_value": _handle_value(self.process_handle),
            "thread_handle_value": _handle_value(self.thread_handle) if self.thread_handle else None,
            "pid_from_PROCESS_INFORMATION": self.pid,
            "thread_id_from_PROCESS_INFORMATION": self.thread_id,
        }

    def record_event(self, record: Mapping[str, Any]) -> None:
        # Post-birth diagnostics are never allowed to unwind the owner stack.
        # In particular, stdout may itself be closed or raise BrokenPipeError.
        ok, error_text = _append_event_best_effort(LEASE_EVENTS, record)
        if ok:
            return
        try:
            errors = getattr(self, "persistence_errors", None)
            if errors is None:
                errors = []
                self.persistence_errors = errors
            if len(errors) < 32:
                errors.append(error_text or "unknown event persistence error")
            else:
                self.persistence_error_count = getattr(self, "persistence_error_count", 32) + 1
        except BaseException:
            # Even diagnostic bookkeeping is subordinate to retaining the
            # original PROCESS_INFORMATION owner.
            pass

    def observe_identity(self) -> None:
        self.identity = self.api.process_identity(self.process_handle)
        if self.identity["pid"] != self.pid or self.pid <= 0 or self.thread_id <= 0:
            _fail("PROCESS_INFORMATION PID/thread identity differs from its original handles")
        self.child_token = self.api.child_token_snapshot(self.process_handle)
        self.state = "IDENTITY_OBSERVED"

    def close_after_terminal(self) -> None:
        if not self.terminal_observed:
            _fail("process handles cannot be released before original-handle natural terminal")
        errors = []
        for handle, label in ((self.thread_handle, "original process-information thread"),
                              (self.process_handle, "original process-information process")):
            try:
                self.api.close(handle, label)
            except BaseException as exc:
                errors.append(_safe_error_text(exc))
        if errors:
            self.state = "UNKNOWN_HANDLE_CLOSE"
            self.record_event({"status": self.state, "close_errors": errors,
                               "original_process_information": self.original_handles(),
                               "terminal_return_code": self.terminal_code})
            _fail("terminal process-information handle close was not confirmed", errors=errors)
        self.state = "RELEASED_AFTER_NATURAL_TERMINAL"
        if self in _PROCESS_LEASES:
            _PROCESS_LEASES.remove(self)


class R6CProcessOwnerController:
    """Local-stack R6C holder for the reviewed R6B authority lease and exact process handle.

    This controller is held directly by parent-mode ``_main`` and its
    unconditional ``finally``. The global R6B registries are diagnostic
    indexes only: they are never the sole reference keeping the native root or
    PROCESS_INFORMATION alive. Any unresolved state parks this same controller
    in this process and prevents parent-token/event cleanup or helper return.
    """

    def __init__(self, root_lease: Any):
        self.root_lease = root_lease
        self.process_lease: ProcessLease | None = None
        self.partial_hold: PartialProcessInformationHold | None = None
        self.state = "R6C_PARENT_CONTROLLER_ACTIVE"
        self.bind_error: str | None = None
        self.unknown_recorded = False

    def hold_partial_process_information(self, partial_hold: "PartialProcessInformationHold") -> None:
        # Keep both the R6B root owner and original PROCESS_INFORMATION holder
        # on this parent stack. A thread signal is not process terminal proof.
        self.partial_hold = partial_hold
        self.state = "UNKNOWN_R6C_PARTIAL_PROCESS_INFORMATION_CONTROLLER_RETAINED"
        partial_hold.hold_forever()
        self._park_forever("partial PROCESS_INFORMATION hold unexpectedly returned")

    def attach_process(self, process_lease: ProcessLease) -> None:
        if self.process_lease is not None:
            self._mark_unqualified_unknown(ProbeError("a second PROCESS_INFORMATION owner was returned"))
            self._park_forever("second process information refused")
        self.process_lease = process_lease
        try:
            identity = process_lease.api.process_identity(process_lease.process_handle)
            if identity.get("pid") != process_lease.pid:
                raise ProbeError("original process handle PID differs from PROCESS_INFORMATION")
            self.root_lease.bind_original_birth(
                process_lease.process_handle, process_lease.pid,
                identity.get("birth_filetime_100ns"), identity.get("image_path"),
            )
            process_lease.record_event({
                "status": "R6B_ROOT_LEASE_BOUND_TO_ORIGINAL_PROCESS_INFORMATION",
                "process": identity,
                "original_process_information": process_lease.original_handles(),
                "same_handle_object_for_root_and_process_lease": (
                    self.root_lease.original_process_handle is process_lease.process_handle
                ),
            })
        except BaseException as exc:
            self.bind_error = _safe_error_text(exc)
            self._mark_unqualified_unknown(exc)
            process_lease.state = "UNKNOWN_R6C_ORIGINAL_PROCESS_BIND"
            process_lease.record_event({
                "status": process_lease.state,
                "error": self.bind_error,
                "original_process_information": process_lease.original_handles(),
                "action": "RETAIN_CONTROLLER_AND_EXACT_PI; PASSIVE_WAIT_ONLY; NO_PID_REOPEN; NO_SIGNAL; NO_CLOSE",
            })

    def _mark_unqualified_unknown(self, cause: BaseException) -> None:
        if getattr(self.root_lease, "state", None) not in {
            "ABORTED_BEFORE_ONCE_NO_BIRTH", "TERMINAL_AND_ORIGINAL_OWNER_RELEASED",
        }:
            try:
                self.root_lease._mark_unknown(cause)
            except BaseException:
                # The local controller remains the authoritative owner even if
                # state diagnostics themselves fail.
                pass

    def _record_unknown_once(self, status: str, details: Mapping[str, Any] | None = None) -> None:
        try:
            if self.unknown_recorded:
                return
            # Mark first so an error in any following diagnostic accessor can
            # never trigger an evidence retry or escape the owner path.
            self.unknown_recorded = True
            lease = self.process_lease
            if lease is None:
                return
            record = {
                "status": status,
                "original_process_information": lease.original_handles(),
                "root_lease_state": getattr(self.root_lease, "state", "UNKNOWN"),
                "root_lease_unknown_reason": getattr(self.root_lease, "unknown_reason", None),
                "action": "KEEP_LOCAL_R6C_CONTROLLER_LIVE; RETAIN_ALL_UNRESOLVED_HANDLES; PASSIVE_WAIT_ONLY; NO_PID_REOPEN; NO_SIGNAL; NO_CLOSE",
            }
            if details:
                record.update(dict(details))
            lease.record_event(record)
        except BaseException as exc:
            _best_effort_print("PRIMARY_OWNER_UNKNOWN_DIAGNOSTIC_UNAVAILABLE " + _safe_error_text(exc))

    def _park_forever(self, reason: str) -> None:
        self.state = "UNKNOWN_R6C_LOCAL_CONTROLLER_RETAINED"
        try:
            self._record_unknown_once("UNKNOWN_R6C_LOCAL_CONTROLLER_RETAINED", {"reason": reason})
        except BaseException as exc:
            _best_effort_print("PRIMARY_OWNER_PARK_DIAGNOSTIC_UNAVAILABLE " + _safe_error_text(exc))
        # The local controller itself is the lifetime owner. Diagnostic
        # failures cannot prevent this passive hold from being entered.
        while True:
            try:
                time.sleep(PASSIVE_WAIT_SLICE_MS / 1000)
            except BaseException:
                pass

    def wait_to_natural_terminal(self) -> int:
        lease = self.process_lease
        if lease is None:
            self._park_forever("no original process handle exists after ONCE consumption/dispatch")
        deadline = lease.started_monotonic + MAX_ACTIVE_SECONDS
        while True:
            try:
                remaining = max(0.0, deadline - time.monotonic())
                if lease.deadline_exceeded:
                    wait_ms = PASSIVE_WAIT_SLICE_MS
                else:
                    wait_ms = (max(1, min(0xFFFFFFFE, int(remaining * 1000)))
                               if remaining > 0 else 0)
                result = int(lease.api.k.WaitForSingleObject(lease.process_handle, wait_ms))
            except BaseException as exc:
                try:
                    self.root_lease.observe_wait_timeout(lease.process_handle, WAIT_FAILED)
                except BaseException:
                    self._mark_unqualified_unknown(exc)
                lease.state = "UNKNOWN_R6C_ORIGINAL_HANDLE_WAIT_FAILED"
                lease.record_event({
                    "status": lease.state,
                    "observation_error": _safe_error_text(exc),
                    "winerror": int(C.get_last_error()),
                    "original_process_information": lease.original_handles(),
                    "action": "RETAIN_LOCAL_CONTROLLER_AND_PI; PASSIVE_WAIT_ONLY; NO_PID_REOPEN; NO_SIGNAL; NO_CLOSE",
                })
                try:
                    time.sleep(1.0)
                except BaseException:
                    pass
                continue

            now = time.monotonic()
            if result == WAIT_TIMEOUT:
                if now >= deadline and not lease.deadline_exceeded:
                    lease.deadline_exceeded = True
                    lease.state = "UNKNOWN_RUNNING_AFTER_60S_CUTOFF"
                    try:
                        self.root_lease.observe_wait_timeout(lease.process_handle, WAIT_TIMEOUT)
                    except BaseException as exc:
                        self._mark_unqualified_unknown(exc)
                    lease.record_event({
                        "status": lease.state,
                        "deadline_exceeded": True,
                        "process": lease.identity,
                        "child_token": lease.child_token,
                        "original_process_information": lease.original_handles(),
                        "root_lease_state": getattr(self.root_lease, "state", "UNKNOWN"),
                        "action_after_cutoff": "NO_FURTHER_ADMISSION; KEEP_LOCAL_CONTROLLER_LIVE; RETAIN_SAME_PI_AND_ROOT_HANDLES; PASSIVE_WAIT_ONLY; NO_SIGNAL_OR_CLOSE",
                    })
                continue

            if result == WAIT_OBJECT_0:
                code = W.DWORD()
                try:
                    lease.api._check(
                        lease.api.k.GetExitCodeProcess(lease.process_handle, C.byref(code)),
                        "GetExitCodeProcess(exact original PROCESS_INFORMATION handle)",
                    )
                except BaseException as exc:
                    try:
                        self.root_lease.observe_wait_timeout(lease.process_handle, WAIT_FAILED)
                    except BaseException:
                        self._mark_unqualified_unknown(exc)
                    self._record_unknown_once("UNKNOWN_R6C_NATURAL_TERMINAL_EXITCODE_READ")
                    self._park_forever("WAIT_OBJECT_0 without a readable exact exit code")
                exit_code = int(code.value)
                if exit_code == STILL_ACTIVE or not 0 <= exit_code <= 0xFFFFFFFF:
                    try:
                        self.root_lease.observe_wait_timeout(lease.process_handle, WAIT_OBJECT_0)
                    except BaseException as exc:
                        self._mark_unqualified_unknown(exc)
                    self._park_forever("WAIT_OBJECT_0 did not produce a valid natural terminal exit code")
                try:
                    root_receipt = self.root_lease.finish_after_natural_terminal(
                        lease.process_handle, WAIT_OBJECT_0, exit_code,
                    )
                except BaseException as exc:
                    self._mark_unqualified_unknown(exc)
                    self._record_unknown_once("UNKNOWN_R6C_OWNER_RELEASE_AFTER_TERMINAL", {
                        "error": _safe_error_text(exc),
                        "exit_code": exit_code,
                    })
                    self._park_forever("R6B authority owner could not be released after the exact terminal tuple")
                lease.terminal_code = exit_code
                lease.terminal_observed = True
                lease.state = "NATURALLY_TERMINAL_R6C_ROOT_RELEASED"
                lease.record_event({
                    "status": lease.state,
                    "original_process_information": lease.original_handles(),
                    "root_owner_retained_through_terminal": root_receipt.get("root_owner_retained_through_terminal"),
                    "timeout_unknown_resolved_by_original_handle_terminal": root_receipt.get(
                        "timeout_unknown_resolved_by_original_handle_terminal",
                    ),
                    "terminal_exit_code": exit_code,
                })
                try:
                    lease.close_after_terminal()
                except BaseException as exc:
                    self._record_unknown_once("UNKNOWN_R6C_PROCESS_INFORMATION_HANDLE_CLOSE", {
                        "error": _safe_error_text(exc),
                    })
                    self._park_forever("process-information handle cleanup failed after natural terminal")
                self.state = "R6C_OWNER_AND_PROCESS_HANDLES_RELEASED_AFTER_TERMINAL"
                return exit_code

            try:
                self.root_lease.observe_wait_timeout(lease.process_handle, result)
            except BaseException as exc:
                self._mark_unqualified_unknown(exc)
            lease.state = ("UNKNOWN_R6C_ORIGINAL_HANDLE_WAIT_FAILED" if result == WAIT_FAILED
                           else "UNKNOWN_R6C_UNEXPECTED_WAIT_RESULT")
            lease.record_event({
                "status": lease.state,
                "wait_result": result,
                "winerror": _last_win_error() if result == WAIT_FAILED else None,
                "original_process_information": lease.original_handles(),
                "action": "RETAIN_LOCAL_CONTROLLER_AND_PI; PASSIVE_WAIT_ONLY; NO_PID_REOPEN; NO_SIGNAL; NO_CLOSE",
            })
            try:
                time.sleep(1.0)
            except BaseException:
                pass

    def hold_before_parent_handle_cleanup(self) -> None:
        try:
            self._hold_before_parent_handle_cleanup_once()
        except BaseException as exc:
            # This is the final owner guard in _main's finally block. Any
            # unexpected wait, cleanup, or diagnostic failure parks this same
            # controller; it never retries a close or releases the PI handles.
            try:
                self._mark_unqualified_unknown(exc)
            except BaseException:
                pass
            try:
                self.state = "UNKNOWN_R6C_OUTER_HOLDER_EXCEPTION_RETAINED"
            except BaseException:
                pass
            try:
                self._record_unknown_once("UNKNOWN_R6C_OUTER_HOLDER_EXCEPTION_RETAINED", {
                    "error": _safe_error_text(exc),
                })
            except BaseException:
                pass
            while True:
                try:
                    time.sleep(PASSIVE_WAIT_SLICE_MS / 1000)
                except BaseException:
                    pass

    def _hold_before_parent_handle_cleanup_once(self) -> None:
        if self.state == "R6C_OWNER_AND_PROCESS_HANDLES_RELEASED_AFTER_TERMINAL":
            return
        if self.process_lease is not None:
            self.wait_to_natural_terminal()
            if self.state != "R6C_OWNER_AND_PROCESS_HANDLES_RELEASED_AFTER_TERMINAL":
                self._park_forever("controller could not establish exact released terminal state")
            return
        root_state = getattr(self.root_lease, "state", None)
        if root_state == "PINNED_PREBIRTH":
            try:
                self.root_lease.close_before_once_without_birth()
            except BaseException as exc:
                self._mark_unqualified_unknown(exc)
                self._park_forever("pre-once authority-root owner cleanup failed")
            return
        if root_state in {
            "NEW", "ABORTED_BEFORE_ONCE_NO_BIRTH", "TERMINAL_AND_ORIGINAL_OWNER_RELEASED",
        }:
            return
        self._mark_unqualified_unknown(ProbeError(
            "parent unwound after once intent without an original PROCESS_INFORMATION handle",
        ))
        self._park_forever("once is consumed or ambiguous without an original process handle")


def _append_event(path: str, record: Mapping[str, Any]) -> None:
    raw = (json.dumps(dict(record), sort_keys=True, separators=(",", ":"), ensure_ascii=False,
                      allow_nan=False) + "\n").encode("utf-8")
    flags = os.O_WRONLY | os.O_CREAT | os.O_APPEND | getattr(os, "O_BINARY", 0)
    fd = os.open(path, flags, 0o600)
    try:
        written = 0
        while written < len(raw):
            count = os.write(fd, raw[written:])
            if count <= 0:
                _fail("owner-lease event append made no progress")
            written += count
        os.fsync(fd)
    finally:
        os.close(fd)


def _safe_error_text(exc: BaseException) -> str:
    try:
        kind = type(exc).__name__
    except BaseException:
        kind = "BaseException"
    try:
        detail = str(exc)
    except BaseException:
        detail = "<exception formatting failed>"
    return (kind + ":" + detail)[:2048]


def _best_effort_print(message: str) -> None:
    try:
        print(message, flush=True)
    except BaseException:
        # stdout/stderr are diagnostic channels only and can never own or
        # release a live native process.
        pass


def _append_event_best_effort(path: str, record: Mapping[str, Any]) -> tuple[bool, str | None]:
    try:
        _append_event(path, record)
        return True, None
    except BaseException as exc:
        error_text = _safe_error_text(exc)
        _best_effort_print("PRIMARY_OWNER_EVENT_WRITE_UNKNOWN " + error_text)
        return False, error_text


class PartialProcessInformationHold:
    """Permanent UNKNOWN owner for a partial birth with no process handle.

    The exact PROCESS_INFORMATION storage and any returned thread handle stay
    rooted in this live owner process. A signaled thread handle is only thread
    evidence; it never establishes process terminal status or permits release.
    """

    def __init__(self, api: Win32, process_info: PROCESS_INFORMATION, *,
                 reason: str, command_line: str, started_monotonic: float) -> None:
        self.api = api
        self.process_info = process_info
        self.thread_handle = process_info.hThread
        self.pid = int(process_info.dwProcessId)
        self.thread_id = int(process_info.dwThreadId)
        self.reason = reason
        self.command_line = command_line
        self.started_monotonic = started_monotonic
        self.has_any_process_information = bool(
            process_info.hProcess or process_info.hThread or self.pid or self.thread_id
        )
        self.state = (
            "UNKNOWN_PARTIAL_PROCESS_INFORMATION_NO_PROCESS_HANDLE"
            if self.has_any_process_information else "UNKNOWN_CREATE_DISPATCH_OUTCOME_NO_PROCESS_HANDLE"
        )
        self.thread_terminal_observed = False
        self.deadline_exceeded = False
        self.last_persist_error: str | None = None
        self.last_wait_problem: str | None = None
        _PARTIAL_PROCESS_INFORMATION.append(self)
        self._record_best_effort({
            "status": self.state,
            "creation_outcome": reason,
            "process": None,
            "process_terminal_observed": False,
            "thread_terminal_observed": False,
            "original_process_information": self.original_information(),
            "command_line": command_line,
            "action": "RETAIN_EXACT_PROCESS_INFORMATION_AND_RETURNED_THREAD_HANDLE; PASSIVE_THREAD_WAIT_ONLY; NO_PID_REOPEN; NO_SIGNAL; NO_CLOSE; NO_REPLACEMENT_BIRTH",
        })

    def original_information(self) -> dict[str, Any]:
        return {
            "process_handle_value": _raw_handle_value(self.process_info.hProcess),
            "thread_handle_value": _raw_handle_value(self.process_info.hThread),
            "pid_from_PROCESS_INFORMATION": self.pid,
            "thread_id_from_PROCESS_INFORMATION": self.thread_id,
        }

    def _record_best_effort(self, record: Mapping[str, Any]) -> bool:
        ok, error_text = _append_event_best_effort(LEASE_EVENTS, record)
        self.last_persist_error = error_text
        return ok

    def observe_once(self) -> str:
        if not self.deadline_exceeded and time.monotonic() - self.started_monotonic >= MAX_ACTIVE_SECONDS:
            self.deadline_exceeded = True
            self._record_best_effort({
                "status": "UNKNOWN_PROCESS_INFORMATION_AFTER_60S_CUTOFF",
                "creation_outcome": self.reason,
                "process_terminal_observed": False,
                "thread_terminal_observed": self.thread_terminal_observed,
                "original_process_information": self.original_information(),
                "action_after_cutoff": "NO_FURTHER_ADMISSION; RETAIN_EXACT_INFORMATION; PASSIVE_THREAD_WAIT_ONLY; NO_PID_REOPEN; NO_SIGNAL; NO_CLOSE",
            })
        if not self.thread_handle:
            # A PID/TID without an original native handle is not authority to
            # reopen a process. Keep the exact PI and owner alive indefinitely.
            try:
                time.sleep(PASSIVE_WAIT_SLICE_MS / 1000)
            except BaseException:
                pass
            return self.state
        result = int(self.api.k.WaitForSingleObject(self.thread_handle, PASSIVE_WAIT_SLICE_MS))
        if result == WAIT_OBJECT_0 and not self.thread_terminal_observed:
            self.thread_terminal_observed = True
            self.last_wait_problem = None
            self._record_best_effort({
                "status": "PARTIAL_THREAD_TERMINAL_PROCESS_STILL_UNKNOWN",
                "creation_outcome": self.reason,
                "process_terminal_observed": False,
                "thread_terminal_observed": True,
                "original_process_information": self.original_information(),
                "action": "KEEP_EXACT_INFORMATION_AND_THREAD_HANDLE; NO_PROCESS_TERMINAL_CLAIM; NO_CLOSE",
            })
        elif result == WAIT_TIMEOUT:
            self.last_wait_problem = None
        elif result == WAIT_FAILED:
            problem = "WAIT_FAILED:" + str(int(C.get_last_error()))
            if self.last_wait_problem != problem:
                self.last_wait_problem = problem
                self._record_best_effort({
                    "status": "UNKNOWN_PARTIAL_THREAD_WAIT_FAILED",
                    "winerror": int(C.get_last_error()),
                    "process_terminal_observed": False,
                    "thread_terminal_observed": self.thread_terminal_observed,
                    "original_process_information": self.original_information(),
                    "action": "RETAIN_EXACT_INFORMATION; PASSIVE_WAIT_ONLY; NO_PID_REOPEN; NO_SIGNAL; NO_CLOSE",
                })
        elif result != WAIT_OBJECT_0:
            problem = "UNEXPECTED_WAIT_RESULT:" + str(result)
            if self.last_wait_problem != problem:
                self.last_wait_problem = problem
                self._record_best_effort({
                    "status": "UNKNOWN_PARTIAL_THREAD_WAIT_RESULT",
                    "wait_result": result,
                    "process_terminal_observed": False,
                    "thread_terminal_observed": self.thread_terminal_observed,
                    "original_process_information": self.original_information(),
                    "action": "RETAIN_EXACT_INFORMATION; PASSIVE_WAIT_ONLY; NO_PID_REOPEN; NO_SIGNAL; NO_CLOSE",
                })
        if self.thread_terminal_observed:
            try:
                time.sleep(PASSIVE_WAIT_SLICE_MS / 1000)
            except BaseException:
                pass
        return self.state

    def hold_forever(self) -> None:
        while True:
            try:
                self.observe_once()
            except BaseException as exc:
                # An interrupt or observation failure cannot release the exact
                # returned information or make a thread signal mean process end.
                self._record_best_effort({
                    "status": "UNKNOWN_PARTIAL_PROCESS_INFORMATION_HOLD_CONTINUES",
                    "observation_error": _safe_error_text(exc),
                    "process_terminal_observed": False,
                    "thread_terminal_observed": self.thread_terminal_observed,
                    "original_process_information": self.original_information(),
                    "action": "RETAIN_EXACT_INFORMATION; PASSIVE_THREAD_WAIT_ONLY; NO_PID_REOPEN; NO_SIGNAL; NO_CLOSE",
                })
                try:
                    time.sleep(1.0)
                except BaseException:
                    pass


def _partial_creation_reason(*, create_ok: bool, create_error: str | None) -> str:
    if create_error is not None:
        return "CREATE_PROCESS_CALL_RAISED_AFTER_NATIVE_DISPATCH"
    if not create_ok:
        return "CREATE_PROCESS_RETURNED_FALSE_WITH_PARTIAL_PROCESS_INFORMATION"
    return "CREATE_PROCESS_RETURNED_TRUE_WITHOUT_PROCESS_HANDLE"


def _adopt_process_information_result(
    api: Win32, process_info: PROCESS_INFORMATION, *, create_ok: bool,
    create_error: str | None, command_line: str, started_monotonic: float,
) -> tuple[ProcessLease | None, PartialProcessInformationHold | None]:
    if process_info.hProcess:
        return ProcessLease(api, process_info, command_line=command_line,
                            started_monotonic=started_monotonic), None
    partial_fields = bool(process_info.hThread or process_info.dwProcessId or process_info.dwThreadId)
    if partial_fields or create_error is not None or create_ok:
        reason = _partial_creation_reason(create_ok=create_ok, create_error=create_error)
        return None, PartialProcessInformationHold(
            api, process_info, reason=reason, command_line=command_line,
            started_monotonic=started_monotonic,
        )
    # A documented FALSE result with a fully zeroed PROCESS_INFORMATION
    # reports no process. An exception after dispatch or TRUE without any
    # returned handles is handled above as a permanent UNKNOWN owner hold.
    return None, None


def _record_handle_cleanup_anomaly(
    errors: list[str], final_status: str | None, *, prior_exception_active: bool,
) -> None:
    if not errors:
        return
    cleanup_event = {
        "status": "UNKNOWN_PARENT_TOKEN_OR_EVENT_HANDLE_CLOSE",
        "cleanup_errors": list(errors),
        "probe_status_before_cleanup": final_status,
        "first_cause_preserved": prior_exception_active or final_status != "PASS_SAME_SID_PRIMARY_OWNER_FILE_PROBE",
        "action": "DO_NOT_REPORT_CLEAN_PASS; PRESERVE_PRIOR_CAUSE; RETAIN_PRIVATE_EVIDENCE",
    }
    _append_event_best_effort(LEASE_EVENTS, cleanup_event)
    if not prior_exception_active and final_status == "PASS_SAME_SID_PRIMARY_OWNER_FILE_PROBE":
        _fail("token or event handle cleanup was not confirmed; persisted probe result is superseded by UNKNOWN")


def _read_authorized_candidate(binding_arg: str, approval_arg: str, once_arg: str):
    fixed = ((binding_arg, BINDING_PATH), (approval_arg, APPROVAL_PATH), (once_arg, ONCE_PATH))
    for supplied, expected in fixed:
        if ntpath.normcase(ntpath.abspath(supplied)) != ntpath.normcase(expected):
            _fail("parent argv does not match the exact fixed authority path", supplied=supplied, expected=expected)
    root = Path(TASK_ROOT)
    if not root.is_dir() or root.is_symlink():
        _fail("task-private root is absent or is a link")
    if Path(ONCE_PATH).exists() or Path(ONCE_PATH).is_symlink():
        _fail("fresh R6C once path already exists; no replay")
    if Path(OUTPUT_ROOT).exists() or Path(OUTPUT_ROOT).is_symlink():
        _fail("fresh R6C output root already exists; no overwrite")
    helper_path = ntpath.normcase(ntpath.abspath(__file__))
    if helper_path != ntpath.normcase(HELPER_PATH):
        _fail("loaded source path is not the exact reviewed helper path", actual=__file__, expected=HELPER_PATH)
    helper_raw = _read_small_file(HELPER_PATH)
    binding_raw = _read_small_file(BINDING_PATH)
    approval_raw = _read_small_file(APPROVAL_PATH)
    binding = _strict_json(binding_raw)
    approval = _strict_json(approval_raw)
    r6b_outer = globals().get("_CV3_R5J_OUTER")
    if not isinstance(r6b_outer, Mapping) or not isinstance(r6b_outer.get("source_fingerprints"), Mapping):
        _fail("same-process R5J gate did not pass the exact verified outer source map")
    r6b_source = r6b_outer["source_fingerprints"]
    helper_sha = _sha256(helper_raw)
    binding_sha = _sha256(binding_raw)
    approval_sha = _sha256(approval_raw)
    expected_binding = {
        "schema": "cv3-windows-primary-token-owner-binding-r6c/1",
        "task_root": TASK_ROOT,
        "python_executable": PYTHON_EXE,
        "python_version": PYTHON_VERSION,
        "helper_path": HELPER_PATH,
        "helper_sha256": helper_sha,
        "r6b_lease_source_sha256": r6b_source.get("r6b_lease_source_sha256"),
        "r6b_lease_controls_sha256": r6b_source.get("r6b_lease_controls_sha256"),
        "authority_root": AUTHORITY_ROOT,
        "binding_path": BINDING_PATH,
        "approval_path": APPROVAL_PATH,
        "once_path": ONCE_PATH,
        "output_root": OUTPUT_ROOT,
        "output_file": OUTPUT_FILE,
        "expected_user_sid": EXPECTED_USER_SID,
        "case_processes": 1,
        "max_active_seconds": MAX_ACTIVE_SECONDS,
        "COMSOL": 0,
        "GUI": 0,
        "JVM": 0,
        "Study": 0,
        "solver": 0,
        "no_privilege_enablement": True,
        "no_account_or_acl_mutation": True,
        "no_process_signal_or_pid_reopen": True,
    }
    if binding != expected_binding:
        _fail("binding does not match the fixed reviewed candidate and scope")
    if ntpath.normcase(ntpath.abspath(sys.executable)) != ntpath.normcase(PYTHON_EXE):
        _fail("current interpreter path differs from the fixed reviewed Python path", actual=sys.executable)
    if sys.version.split()[0] != PYTHON_VERSION:
        _fail("current interpreter version differs from the fixed reviewed Python version", actual=sys.version)
    if approval.get("schema") != "cv3-Main-primary-owner-once-r6c/1" or approval.get("status") != "APPROVED_EXECUTE_ONCE":
        _fail("Main approval schema/status is not an execute-once authority")
    if approval.get("binding_sha256") != binding_sha or approval.get("helper_sha256") != helper_sha:
        _fail("Main approval does not match the exact binding/source bytes")
    if (approval.get("r6b_lease_source_sha256") != binding["r6b_lease_source_sha256"]
            or approval.get("r6b_lease_controls_sha256") != binding["r6b_lease_controls_sha256"]):
        _fail("Main approval does not bind the exact R6B source and controls")
    if approval.get("budget") != {
        "case_processes": 1, "max_active_seconds": MAX_ACTIVE_SECONDS,
        "COMSOL": 0, "GUI": 0, "JVM": 0, "Study": 0, "solver": 0,
    }:
        _fail("Main approval budget exceeds or differs from the fixed probe bound")
    nonce = approval.get("native_nonce")
    if not isinstance(nonce, str) or len(nonce) != 32 or any(char not in "0123456789abcdef" for char in nonce):
        _fail("Main approval native nonce must be exactly 32 lower-case hexadecimal characters")
    return binding, approval, {"helper_sha256": helper_sha, "binding_sha256": binding_sha,
                               "approval_sha256": approval_sha, "native_nonce_sha256": _sha256(nonce.encode())}


def _privilege_luid(api: Win32, token: Any, privilege_name: str) -> tuple[int, int]:
    luid = LUID()
    api._check(api.a.LookupPrivilegeValueW(None, privilege_name, C.byref(luid)),
               "LookupPrivilegeValueW(" + privilege_name + ")")
    expected = _luid_value(luid)
    if not any((low, high) == expected and (attrs & SE_PRIVILEGE_ENABLED)
               for low, high, attrs in api.privileges_info(token)):
        _fail("required caller privilege is absent or disabled; no privilege will be enabled",
              privilege=privilege_name)
    return expected


def _is_reparse_free_directory(api: Win32, path: str, *, must_exist: bool) -> None:
    attrs = int(api.k.GetFileAttributesW(path))
    if must_exist:
        if attrs == INVALID_FILE_ATTRIBUTES or not (attrs & FILE_ATTRIBUTE_DIRECTORY) or attrs & FILE_ATTRIBUTE_REPARSE_POINT:
            _fail("fixed path component is not an existing ordinary directory", path=path)
    elif attrs != INVALID_FILE_ATTRIBUTES:
        _fail("new private output directory already exists or is a reparse point", path=path, attributes=attrs)


def _child_wait_and_create(event_name: str) -> int:
    if os.name != "nt" or ntpath.normcase(ntpath.abspath(__file__)) != ntpath.normcase(HELPER_PATH):
        return 70
    if sys.executable.casefold() != PYTHON_EXE.casefold() or sys.version.split()[0] != PYTHON_VERSION:
        return 71
    if len(sys.argv) != 3 or sys.argv[1] != "--child-wait" or sys.argv[2] != event_name:
        return 72
    api = Win32()
    event = api.k.OpenEventW(SYNCHRONIZE, False, event_name)
    if not event:
        return 73
    try:
        result = int(api.k.WaitForSingleObject(event, CHILD_EVENT_WAIT_MS))
        if result != WAIT_OBJECT_0:
            return 74
        attributes = int(api.k.GetFileAttributesW(OUTPUT_ROOT))
        if attributes == INVALID_FILE_ATTRIBUTES or not (attributes & FILE_ATTRIBUTE_DIRECTORY) or attributes & FILE_ATTRIBUTE_REPARSE_POINT:
            return 75
        fd = os.open(OUTPUT_FILE, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_BINARY", 0), 0o600)
        with os.fdopen(fd, "wb", buffering=0) as stream:
            count = stream.write(PROBE_BYTES)
            if count != len(PROBE_BYTES):
                return 76
            os.fsync(stream.fileno())
        return 0
    except FileExistsError:
        return 77
    except BaseException:
        return 78
    finally:
        try:
            api.close(event, "child event")
        except BaseException:
            pass


def _main(binding_arg: str, approval_arg: str, once_arg: str) -> int:
    if os.name != "nt":
        _fail("actual primary-token probe requires Windows")
    if len(sys.argv) != 7 or sys.argv[1] != "--binding" or sys.argv[3] != "--approval" or sys.argv[5] != "--once":
        _fail("parent mode requires the exact binding/approval/once argv")
    binding, approval, fingerprint = _read_authorized_candidate(binding_arg, approval_arg, once_arg)
    api = Win32()
    if C.sizeof(TOKEN_STATISTICS) != 56 or C.sizeof(STARTUPINFOW) != 104 or C.sizeof(PROCESS_INFORMATION) != 24:
        # The current target is the documented 64-bit CPython 3.12 runtime.
        _fail("native ABI sizes differ from the reviewed 64-bit Windows target",
              token_statistics=C.sizeof(TOKEN_STATISTICS), startup_info=C.sizeof(STARTUPINFOW),
              process_information=C.sizeof(PROCESS_INFORMATION))

    r6b_outer = globals().get("_CV3_R5J_OUTER")
    r6b_live = globals().get("_CV3_R5J_LIVE")
    r6b_gate = globals().get("_CV3_R5J_GATE")
    r6b_module = globals().get("_CV3_R6B_LEASE_MODULE")
    if (not isinstance(r6b_outer, Mapping) or not isinstance(r6b_live, Mapping)
            or r6b_gate is None or r6b_module is None):
        _fail("R6C parent mode requires the same-process R5J proof and embedded reviewed lease module")
    try:
        from comsol_mcp import _host_scoped_provider as r6b_provider
        provider_path = ntpath.normcase(ntpath.abspath(r6b_provider.__file__ or ""))
        expected_provider_path = ntpath.normcase(ntpath.abspath(ntpath.join(
            r6b_gate.R14_SOURCE_ROOT, "comsol_mcp", "_host_scoped_provider.py",
        )))
        if provider_path != expected_provider_path:
            _fail("R6B provider was not imported from the verified R14 source bundle",
                  actual=provider_path, expected=expected_provider_path)
        r6b_path_api, _r6b_hold_original, _r6b_finish_original = r6b_gate._runtime_path_binding_api()
        r6b_ops = r6b_module.R14AuthorityRootOpsR6B(
            r6b_path_api, r6b_provider, r6b_gate, r6b_outer, r6b_live, AUTHORITY_ROOT,
        )
        r6b_root_lease = r6b_module.AuthorityRootLeaseR6B(r6b_ops)
    except BaseException as exc:
        _fail("R6C same-process authority-root owner could not be initialized",
              error_type=type(exc).__name__, error=_safe_error_text(exc))
    r6b_controller = R6CProcessOwnerController(r6b_root_lease)

    process_token = W.HANDLE()
    desired_original_access = TOKEN_QUERY | TOKEN_DUPLICATE
    api._check(api.a.OpenProcessToken(api.k.GetCurrentProcess(), desired_original_access,
                                      C.byref(process_token)), "OpenProcessToken(current process)")
    duplicate_token = W.HANDLE()
    event = W.HANDLE()
    process_lease: ProcessLease | None = None
    partial_hold: PartialProcessInformationHold | None = None
    token_close_errors: list[str] = []
    event_name = "Local\\CV3PrimaryOwnerR6C-" + approval["native_nonce"]
    events_started = time.monotonic()
    try:
        parent_before = api.token_snapshot(process_token)
        if parent_before["token_type"] != TOKEN_PRIMARY or parent_before["user_sid"] != EXPECTED_USER_SID:
            _fail("current process token is not the exact existing primary user")
        if parent_before["owner_sid"] == EXPECTED_USER_SID:
            _fail("the suspected default-owner gap is absent in this current primary token")
        _privilege_luid(api, process_token, "SeImpersonatePrivilege")
        parent_session = parent_before["session_id"]
        if not 0 <= parent_session <= 0x7FFFFFFF:
            _fail("current process token session ID is outside the supported range")
        once_record = {
            "schema": "cv3-primary-owner-once-use-r6c/1",
            "status": "CONSUMED_BEFORE_TOKEN_DUPLICATION",
            **fingerprint,
            "r6b_lease_source_sha256": binding["r6b_lease_source_sha256"],
            "r6b_lease_controls_sha256": binding["r6b_lease_controls_sha256"],
            "budget": approval["budget"],
            "processes": 0,
            "started_utc_unix_ns": time.time_ns(),
            "parent_user_sid": parent_before["user_sid"],
            "parent_owner_sid": parent_before["owner_sid"],
            "parent_session_id": parent_session,
        }
        once_raw = (json.dumps(once_record, sort_keys=True, separators=(",", ":"), ensure_ascii=False,
                              allow_nan=False) + "\n").encode("utf-8")
        r6b_prebirth_owner = r6b_root_lease.pin_after_prebirth()
        r6b_once_owner = r6b_root_lease.consume_once_relative(once_raw, _sha256(once_raw))
        _append_event(LEASE_EVENTS, {
            "status": "R6B_ROOT_PINNED_AND_ONCE_CONSUMED_RELATIVE_BEFORE_TOKEN_DUPLICATION",
            "r6b_prebirth_owner": r6b_prebirth_owner,
            "r6b_once_owner": r6b_once_owner,
            "once_sha256": _sha256(once_raw),
            "once_bytes": len(once_raw),
            "processes": 0,
        })

        requested_duplicate_access = (TOKEN_QUERY | TOKEN_DUPLICATE | TOKEN_ASSIGN_PRIMARY | TOKEN_ADJUST_DEFAULT)
        api._check(api.a.DuplicateTokenEx(
            process_token, requested_duplicate_access, None, SECURITY_IMPERSONATION, TOKEN_PRIMARY,
            C.byref(duplicate_token),
        ), "DuplicateTokenEx(primary)")
        if not duplicate_token:
            _fail("DuplicateTokenEx returned success without a duplicate primary token")
        duplicate_before = api.token_snapshot(duplicate_token)
        if duplicate_before["token_type"] != TOKEN_PRIMARY or duplicate_before["user_sid"] != EXPECTED_USER_SID:
            _fail("DuplicateTokenEx output is not a same-user primary token")
        user_sid_buffer = api.token_info(process_token, TOKEN_USER)[0]
        user_sid_pointer = C.cast(user_sid_buffer, C.POINTER(C.c_void_p)).contents.value
        owner_info = C.c_void_p(user_sid_pointer)
        api._check(api.a.SetTokenInformation(
            duplicate_token, TOKEN_OWNER, C.byref(owner_info), C.sizeof(owner_info),
        ), "SetTokenInformation(TokenOwner duplicate-only)")
        duplicate_after = api.token_snapshot(duplicate_token)
        parent_after_owner = api.token_snapshot(process_token)
        _assert_duplicate_owner_transition(
            parent_before, parent_after_owner, duplicate_before, duplicate_after,
            expected_user_sid=EXPECTED_USER_SID,
        )
        if duplicate_after["session_id"] != parent_session:
            _fail("duplicate TokenSessionId differs from current process session")

        # The caller's user SID is unchanged and already owns its interactive
        # desktop. Set lpDesktop explicitly so CreateProcessWithTokenW does not
        # receive NULL/empty desktop input that requests implicit desktop ACL
        # additions. No ACL is modified by this source; failure stays closed.
        startup = STARTUPINFOW()
        startup.cb = C.sizeof(STARTUPINFOW)
        desktop_name = "WinSta0\\Default"
        startup.lpDesktop = desktop_name
        command_argv = [PYTHON_EXE, "-I", "-B", HELPER_PATH, "--child-wait", event_name]
        command_line_buffer = C.create_unicode_buffer(subprocess.list2cmdline(command_argv))
        command_line = command_line_buffer.value
        if len(command_line) > 1024:
            _fail("fixed child command exceeds CreateProcessWithTokenW's documented bound")

        _is_reparse_free_directory(api, ntpath.dirname(TASK_ROOT), must_exist=True)
        _is_reparse_free_directory(api, TASK_ROOT, must_exist=True)
        _is_reparse_free_directory(api, AUTHORITY_ROOT, must_exist=True)
        _is_reparse_free_directory(api, OUTPUT_ROOT, must_exist=False)
        C.set_last_error(0)
        event = api.k.CreateEventW(None, True, False, event_name)
        if not event:
            _fail("CreateEventW(primary-owner gate) failed", winerror=int(C.get_last_error()))
        if int(C.get_last_error()) == ERROR_ALREADY_EXISTS:
            api.close(event, "preexisting named event")
            event = W.HANDLE()
            _fail("one-shot event name already exists; no child was created")
        Path(OUTPUT_ROOT).mkdir()
        _append_event(LEASE_EVENTS, {
            "status": "PREBIRTH_READY",
            "scope": "one same-SID Python file-owner child; COMSOL/JVM/GUI/Study/solver=0",
            "source": fingerprint,
            "parent_token_snapshot": parent_before,
            "duplicate_before_owner": duplicate_before,
            "duplicate_after_owner": duplicate_after,
            "parent_unchanged_after_owner": True,
            "processes": 0,
            "deadline_seconds": MAX_ACTIVE_SECONDS,
            "child_wait_ms": CHILD_EVENT_WAIT_MS,
            "command_argv": command_argv,
            "command_line": command_line,
            "event_name_sha256": _sha256(event_name.encode("utf-8")),
        })

        process_info = PROCESS_INFORMATION()
        started_monotonic = time.monotonic()
        previous_interrupt_handlers, pending_interrupts = _defer_console_interrupts()
        create_error: str | None = None
        create_ok = False
        try:
            try:
                create_ok = bool(api.a.CreateProcessWithTokenW(
                    duplicate_token, 0, PYTHON_EXE, command_line_buffer, CREATE_NO_WINDOW, None,
                    OUTPUT_ROOT, C.byref(startup), C.byref(process_info),
                ))
            except BaseException as exc:
                create_error = _safe_error_text(exc)
        finally:
            # Defer SIGINT/SIGBREAK through the API return so any exact returned
            # process handle is rooted before the caller's handler is restored.
            process_lease, partial_hold = _adopt_process_information_result(
                api, process_info, create_ok=create_ok, create_error=create_error,
                command_line=command_line, started_monotonic=started_monotonic,
            )
            try:
                if process_lease is not None:
                    r6b_controller.attach_process(process_lease)
            finally:
                _restore_console_interrupts(previous_interrupt_handlers)
        if partial_hold is not None:
            partial_hold.hold_forever()
            _fail("partial PROCESS_INFORMATION hold returned unexpectedly")
        if not create_ok:
            if process_lease is None:
                if process_info.hThread or process_info.dwProcessId or process_info.dwThreadId:
                    _fail("CreateProcessWithTokenW returned FALSE with partial PROCESS_INFORMATION; preserve and escalate",
                          thread_handle_value=_handle_value(process_info.hThread) if process_info.hThread else None,
                          pid=int(process_info.dwProcessId), thread_id=int(process_info.dwThreadId))
                _fail("CreateProcessWithTokenW returned FALSE with no process information; no replacement birth")
            process_lease.state = "UNKNOWN_CREATE_RETURN_WITH_PROCESS_HANDLE"
            process_lease.record_event({
                "status": process_lease.state, "winerror": int(C.get_last_error()),
                "original_process_information": process_lease.original_handles(),
                "command_line": command_line,
            })

        if process_lease is None:
            _fail("CreateProcessWithTokenW returned no original process handle")

        identity_error: str | None = None if create_ok else "CreateProcessWithTokenW returned FALSE with a retained process handle"
        if create_error is not None:
            identity_error = "CreateProcessWithTokenW call raised after native dispatch: " + create_error
        if pending_interrupts:
            interrupt_note = "console interrupt deferred during native process birth: " + ",".join(
                str(value) for value in pending_interrupts
            )
            identity_error = (identity_error + "; " if identity_error else "") + interrupt_note
        try:
            process_lease.observe_identity()
            _assert_child_identity(
                process_lease.identity, process_lease.child_token,
                expected_user_sid=EXPECTED_USER_SID, expected_session=parent_session,
                expected_image=PYTHON_EXE,
            )
            _assert_child_token_matches_duplicate(duplicate_after, process_lease.child_token)
            if process_lease.identity["pid"] != int(process_info.dwProcessId):
                _fail("GetProcessId differs from the PID returned by PROCESS_INFORMATION")
            parent_after_birth = api.token_snapshot(process_token)
            if parent_after_birth != parent_before:
                _fail("original process token changed across CreateProcessWithTokenW")
        except BaseException as exc:
            if identity_error is None:
                identity_error = _safe_error_text(exc)

        process_lease.record_event({
            "status": "CHILD_IDENTITY_OBSERVED" if identity_error is None else "CHILD_IDENTITY_UNKNOWN",
            "identity_error": identity_error,
            "process": process_lease.identity,
            "child_token": process_lease.child_token,
            "original_process_information": process_lease.original_handles(),
            "command_argv": command_argv,
            "command_line": command_line,
        })
        if identity_error is None:
            try:
                api._check(api.k.SetEvent(event), "SetEvent(after exact child identity observation)")
            except BaseException as exc:
                identity_error = _safe_error_text(exc)
            else:
                process_lease.record_event({"status": "CHILD_PROBE_RELEASED_AFTER_IDENTITY_CHECK",
                                            "pid": process_lease.pid,
                                            "event_name_sha256": _sha256(event_name.encode("utf-8"))})
        if identity_error is not None:
            # The child gate remains closed. It can only exit with its bounded
            # no-write code; no file is created and no process signal is sent.
            process_lease.record_event({"status": "CHILD_GATE_LEFT_CLOSED",
                                        "pid": process_lease.pid, "reason": identity_error})

        terminal_code = r6b_controller.wait_to_natural_terminal()
        after_child = api.token_snapshot(process_token)
        parent_unchanged = after_child == parent_before
        file_present = int(api.k.GetFileAttributesW(OUTPUT_FILE)) != INVALID_FILE_ATTRIBUTES
        file_owner = api.file_owner_sid(OUTPUT_FILE) if file_present else None
        file_bytes = Path(OUTPUT_FILE).read_bytes() if file_present else b""
        if (not parent_unchanged or identity_error is not None or process_lease.deadline_exceeded
                or terminal_code != 0 or not file_present or file_owner != EXPECTED_USER_SID
                or file_bytes != PROBE_BYTES):
            final_status = "FAIL_OR_HOLD_OWNER_PROBE"
        else:
            final_status = "PASS_SAME_SID_PRIMARY_OWNER_FILE_PROBE"
        result = {
            "schema": "cv3-windows-primary-token-owner-probe-result-r6c/1",
            "status": final_status,
            "source": fingerprint,
            "process": process_lease.identity,
            "child_token": process_lease.child_token,
            "original_process_information": process_lease.original_handles(),
            "command_argv": command_argv,
            "command_line": command_line,
            "child_return_code": terminal_code,
            "deadline_exceeded": process_lease.deadline_exceeded,
            "elapsed_seconds": round(time.monotonic() - started_monotonic, 6),
            "parent_primary_unchanged": parent_unchanged,
            "file_owner_sid": file_owner,
            "file_sha256": _sha256(file_bytes) if file_present else None,
            "file_bytes": len(file_bytes) if file_present else 0,
            "event_persistence_errors": list(getattr(process_lease, "persistence_errors", [])),
            "event_persistence_error_count": getattr(process_lease, "persistence_error_count", 0),
            "native_tests": 0,
            "COMSOL": 0, "GUI": 0, "JVM": 0, "Study": 0, "solver": 0,
            "scope": "one same-SID process-primary owner control; not COMSOL/product/scientific acceptance",
        }
        _append_event_best_effort(LEASE_EVENTS, result)
        return 0 if final_status == "PASS_SAME_SID_PRIMARY_OWNER_FILE_PROBE" else 1
    finally:
        # The partial-handle branch normally never returns. If it does, this
        # must hold again before any remaining parent handles can be closed.
        if partial_hold is not None:
            r6b_controller.partial_hold = partial_hold
        try:
            if partial_hold is not None:
                partial_hold.hold_forever()
        finally:
            # The original controller remains on this parent stack through
            # exact PROCESS_INFORMATION natural terminal and R6B root-owner
            # release. This nested unconditional guard also runs if the
            # partial-information observer itself raises.
            r6b_controller.hold_before_parent_handle_cleanup()
        for handle, label in ((duplicate_token, "duplicate primary token"),
                              (process_token, "original current-process token"),
                              (event, "owner probe event")):
            if handle:
                try:
                    api.close(handle, label)
                except BaseException as exc:
                    token_close_errors.append(label + ":" + _safe_error_text(exc))
        _record_handle_cleanup_anomaly(
            token_close_errors, locals().get("final_status"),
            prior_exception_active=sys.exc_info()[0] is not None,
        )


def main(argv: list[str] | None = None) -> int:
    values = list(sys.argv[1:] if argv is None else argv)
    if values and values[0] == "--child-wait":
        return _child_wait_and_create(values[1] if len(values) > 1 else "")
    if len(values) != 6 or values[0] != "--binding" or values[2] != "--approval" or values[4] != "--once":
        raise ProbeError("expected --binding PATH --approval PATH --once PATH")
    return _main(values[1], values[3], values[5])


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except ProbeError as error:
        raise SystemExit("PRIMARY_OWNER_PROBE_BLOCKED: " + str(error))
