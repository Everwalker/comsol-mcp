"""Local administrator TCB for one *new* Windows SCM/private-tree namespace.

Not imported by the MCP runtime. JSON is installation intent, never a T037,
profile, child, resume or native-issuer capability. The original in-process
installer and its retained SDK handles/journal are required for restoration.
There is deliberately no start/stop/process-spawn/privilege-enabling API.

Input is an approved, already offline-materialized normal CPython prefix
(pip --no-index runs in the separate packaging step), JDK and bundle inventory.
This module copies bytes, not archives or executable installation scripts.
"""
from __future__ import annotations

import ctypes as C
from dataclasses import dataclass
import hashlib
import io
import json
import ntpath
import os
import re
import struct
import subprocess
import threading
import zipfile

SCHEMA = "comsol-mcp.windows-initial-host-bootstrap/1"
SCHEMA_V2 = "comsol-mcp.windows-initial-host-bootstrap/2"
MAX_PLAN = 2 * 1024 * 1024
MAX_FILES = 8192
MAX_TOTAL = 8 * 1024**3
MAX_FILE = 2 * 1024**3
MAX_ARCHIVE = 256 * 1024**2
MAX_SD = 65536
MAX_SCM_BUFFER = 8192
SY = "S-1-5-18"
BA = "S-1-5-32-544"
PRIVILEGES = ("SeTcbPrivilege", "SeAssignPrimaryTokenPrivilege",
              "SeIncreaseQuotaPrivilege", "SeChangeNotifyPrivilege")
SDK_SOURCES = (
    "https://learn.microsoft.com/en-us/windows/win32/api/winsvc/nf-winsvc-createservicew",
    "https://learn.microsoft.com/en-us/windows/win32/api/winsvc/nf-winsvc-changeserviceconfig2w",
    "https://learn.microsoft.com/en-us/windows/win32/api/winsvc/ns-winsvc-service_required_privileges_infow",
    "https://learn.microsoft.com/en-us/windows/win32/api/winsvc/ns-winsvc-service_sid_info",
    "https://learn.microsoft.com/en-us/windows/win32/api/winsvc/nf-winsvc-queryserviceobjectsecurity",
    "https://learn.microsoft.com/en-us/windows/win32/api/winsvc/nf-winsvc-closeservicehandle",
    "https://learn.microsoft.com/en-us/windows/win32/api/winsvc/nf-winsvc-deleteservice",
    "https://learn.microsoft.com/en-us/windows/win32/api/winsvc/nf-winsvc-enumdependentservicesw",
    "https://learn.microsoft.com/en-us/windows/win32/api/winsvc/ns-winsvc-service_failure_actionsw",
    "https://learn.microsoft.com/en-us/windows/win32/api/winbase/ns-winbase-file_disposition_info",
    "https://learn.microsoft.com/en-us/windows/win32/api/aclapi/nf-aclapi-setsecurityinfo",
    "https://learn.microsoft.com/en-us/windows/win32/api/fileapi/nf-fileapi-createdirectoryw",
    "https://learn.microsoft.com/en-us/windows/win32/api/fileapi/nf-fileapi-setfileinformationbyhandle",
    "https://learn.microsoft.com/en-us/windows/win32/api/fileapi/nf-fileapi-createfilew",
    "https://learn.microsoft.com/en-us/windows/win32/api/winbase/nf-winbase-getfileinformationbyhandleex",
    "https://learn.microsoft.com/en-us/windows/win32/api/winbase/ns-winbase-file_id_info",
    "https://learn.microsoft.com/en-us/windows/win32/api/fileapi/ns-fileapi-by_handle_file_information",
)
_SEAL = object()
_TEST_SEAL = object()
_retained = []
_lock = threading.RLock()
DWORD = C.c_uint32
BOOL = C.c_int32
HANDLE = C.c_void_p


class BootstrapError(RuntimeError):
    def __init__(self, code, message, *, owner=None):
        super().__init__(message)
        self.code, self.owner = code, owner


class KnownSdkFailure(BootstrapError):
    def __init__(self, function, error):
        super().__init__("SDK_KNOWN_FAILURE", f"{function} failed: Win32 {error}")
        self.function, self.winerror = function, error


def fail(code, message):
    raise BootstrapError(code, message)


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=False, allow_nan=False).encode("utf-8")


def digest(raw):
    return hashlib.sha256(raw).hexdigest()


def _fields(value, keys):
    if type(value) is not dict or set(value) != set(keys):
        fail("PLAN_SCHEMA", "closed fields required")


def _hex(value):
    if type(value) is not str or not re.fullmatch("[0-9a-f]{64}", value):
        fail("PLAN_HASH", "lowercase SHA-256 required")
    return value


def _part(value):
    if (not value or value in {".", ".."} or value[-1] in " ."
            or any(ord(c) < 32 or c in '<>:"/\\|?*~' for c in value)
            or re.fullmatch(r"(?i)(CON|PRN|AUX|NUL|COM[1-9]|LPT[1-9])(\..*)?", value)):
        fail("PLAN_PATH", "noncanonical or reserved Windows path component")


def _absolute(value):
    if type(value) is not str or len(value) > 220 or not re.match(r"^[A-Z]:\\", value):
        fail("PLAN_PATH", "bounded local absolute drive path required")
    parts = value[3:].split("\\")
    if not parts:
        fail("PLAN_PATH", "a drive root cannot be the namespace")
    for part in parts:
        _part(part)
    if ntpath.normpath(value) != value:
        fail("PLAN_PATH", "canonical Windows path required")
    return value


def _relative(value):
    if type(value) is not str or len(value) > 200 or "\\" in value:
        fail("PLAN_PATH", "bounded slash-separated relative inventory path required")
    parts = value.split("/")
    for part in parts:
        _part(part)
    if len(parts) > 20:
        fail("PLAN_PATH", "inventory depth exceeds bound")
    return value


def _within(left, right):
    return left.casefold() == right.casefold() or left.casefold().startswith(right.casefold() + "\\")


@dataclass(frozen=True)
class FileIntent:
    path: str
    size: int
    sha256: str
    role: str


@dataclass(frozen=True)
class BootstrapIntent:
    """Immutable intent only; constructed data is not an installer owner."""
    prefix: str
    source_root: str
    service_name: str
    files: tuple[FileIntent, ...]
    materials: tuple[tuple[str, str], ...]
    argv: tuple[str, ...]
    command_line: str
    sha256: str
    schema: str = SCHEMA
    original_raw: bytes = b""
    provisioning: bytes = b""

    @property
    def directories(self):
        answer = {"", "profiles", "service-state"}
        for item in self.files:
            parts = item.path.split("/")[:-1]
            answer.update("/".join(parts[:n]) for n in range(1, len(parts) + 1))
        return tuple(sorted(answer, key=lambda p: (p.count("/"), len(p), p)))

    def destination(self, relative):
        return self.prefix if not relative else ntpath.join(self.prefix, *relative.split("/"))


def service_argv(prefix, name, *, version=1):
    if version == 2:
        return (ntpath.join(prefix, "python312", "tools", "python.exe"), "-B", "-I",
                "-m", "comsol_mcp._control_daemon", "--home",
                ntpath.join(prefix, "service-state"), "--windows-scm-service-name", name,
                "--windows-t037-profile", ntpath.join(prefix, "profiles", "trusted-profile.json"),
                "--host-provider-config", ntpath.join(prefix, "profiles", "host-provider.json"),
                "--memory-resource-config", ntpath.join(prefix, "profiles", "memory-resource.json"),
                "--disk-resource-config", ntpath.join(prefix, "profiles", "disk-resource.json"))
    if version != 1:
        fail("PLAN_SCHEMA", "unsupported Bootstrap argv version")
    return (ntpath.join(prefix, "python312", "tools", "python.exe"), "-B", "-I",
            "-m", "comsol_mcp._control_daemon", "--home",
            ntpath.join(prefix, "service-state"), "--windows-t037-profile",
            ntpath.join(prefix, "profiles", "trusted-profile.json"),
            "--windows-scm-service-name", name)


def parse_plan(raw):
    if type(raw) is not bytes or not 0 < len(raw) <= MAX_PLAN:
        fail("PLAN_BYTES", "bounded UTF-8 JSON bytes required")

    def pairs(items):
        result = {}
        for k, v in items:
            if k in result:
                fail("PLAN_DUPLICATE", "duplicate JSON field")
            result[k] = v
        return result

    try:
        doc = json.loads(raw, object_pairs_hook=pairs,
                         parse_constant=lambda _: fail("PLAN_JSON", "nonfinite JSON"))
    except (ValueError, UnicodeError, RecursionError) as exc:
        raise BootstrapError("PLAN_JSON", "invalid bounded JSON") from exc
    version = 2 if type(doc) is dict and doc.get("schema") == SCHEMA_V2 else 1
    _fields(doc, {"schema", "prefix", "source_root", "service_name", "files",
                  "materials", "runtime_layout", "service_argv", "quoted_command_line"}
            | ({"provisioning"} if version == 2 else set()))
    if doc["schema"] not in {SCHEMA, SCHEMA_V2} or doc["runtime_layout"] != "CPYTHON312_NORMAL_OFFLINE_MATERIALIZED":
        fail("PLAN_SCHEMA", "only the initial normal-CPython materialized layout is supported")
    name = doc["service_name"]
    if type(name) is not str or not re.fullmatch(r"ComsolMCP_[A-Za-z0-9_-]{1,60}", name):
        fail("PLAN_SERVICE", "one explicit new ComsolMCP service namespace required")
    prefix, source = _absolute(doc["prefix"]), _absolute(doc["source_root"])
    if (ntpath.basename(prefix) != name or _within(source, prefix) or _within(prefix, source)):
        fail("PLAN_NAMESPACE", "new prefix must match the service and be disjoint from its source")
    _fields(doc["materials"], {"python_nupkg_sha256", "bundle_sha256", "jdk_inventory_sha256"})
    materials = {k: _hex(v) for k, v in doc["materials"].items()}
    rows = doc["files"]
    if type(rows) is not list or not 6 <= len(rows) <= MAX_FILES:
        fail("PLAN_INVENTORY", "finite runtime/JDK/material inventory required")
    files, names, total = [], set(), 0
    for row in rows:
        _fields(row, {"path", "size", "sha256", "role"})
        p = _relative(row["path"])
        if (p.casefold() in names or ntpath.basename(p).casefold() == "pyvenv.cfg"
                or p.split("/")[0] not in {"python312", "jdk11", "offline", "materials"}
                or len(ntpath.join(prefix, *p.split("/"))) > 240):
            fail("PLAN_INVENTORY", "duplicate, venv, out-of-root or overlong inventory entry")
        if type(row["size"]) is not int or not 0 < row["size"] <= MAX_FILE:
            fail("PLAN_INVENTORY", "positive bounded file size required")
        if row["role"] not in {"python", "runtime", "jdk", "python_archive", "bundle_archive"}:
            fail("PLAN_INVENTORY", "unsupported material role")
        if (row["role"] == "jdk") != p.startswith("jdk11/"):
            fail("PLAN_INVENTORY", "JDK role must match its exact subtree")
        names.add(p.casefold())
        total += row["size"]
        files.append(FileIntent(p, row["size"], _hex(row["sha256"]), row["role"]))
    if total > MAX_TOTAL:
        fail("PLAN_INVENTORY", "total install bytes exceed bound")
    for p in names:
        if any("/".join(p.split("/")[:n]) in names for n in range(1, len(p.split("/")))):
            fail("PLAN_COLLISION", "file/directory inventory collision")
    by_path = {x.path: x for x in files}
    required = {"python312/tools/python.exe", "python312/tools/python312.dll",
                "python312/tools/Lib/site-packages/comsol_mcp/_control_daemon.py",
                "jdk11/bin/java.exe", "materials/python.nupkg", "materials/offline.zip"}
    if not required <= by_path.keys():
        fail("PLAN_INVENTORY", "normal Python, installed MCP, JDK and original archives are required")
    for p, role in (("python312/tools/python.exe", "python"), ("materials/python.nupkg", "python_archive"),
                    ("materials/offline.zip", "bundle_archive")):
        if by_path[p].role != role:
            fail("PLAN_INVENTORY", "material role binding mismatch")
    if (by_path["materials/python.nupkg"].sha256 != materials["python_nupkg_sha256"]
            or by_path["materials/offline.zip"].sha256 != materials["bundle_sha256"]):
        fail("PLAN_HASH", "archive hashes do not bind the approved inventory")
    jdk = [{"path": f.path[len("jdk11/"):], "size": f.size, "sha256": f.sha256}
           for f in files if f.role == "jdk"]
    if digest(canonical(sorted(jdk, key=lambda r: r["path"]))) != materials["jdk_inventory_sha256"]:
        fail("PLAN_HASH", "JDK inventory digest mismatch")
    argv = service_argv(prefix, name, version=version)
    command_line = subprocess.list2cmdline(argv)  # formatting only: never executed
    if doc["service_argv"] != list(argv) or doc["quoted_command_line"] != command_line:
        fail("PLAN_COMMAND", "exact original argv array and Windows quoted command line required")
    if version == 2:
        from ._windows_scm_config_materialization import validate_recipe
        validate_recipe(doc["provisioning"], prefix=prefix, service_name=name, files=files, argv=argv)
    return BootstrapIntent(prefix, source, name, tuple(sorted(files, key=lambda f: f.path)),
                           tuple(sorted(materials.items())), argv, command_line,
                           digest(raw) if version == 2 else digest(canonical(doc)),
                           doc["schema"], raw if version == 2 else b"",
                           canonical(doc["provisioning"]) if version == 2 else b"")


def _sid(raw, offset=0):
    if not 0 <= offset <= len(raw) - 8 or raw[offset] != 1 or raw[offset + 1] > 15:
        fail("SD_BOUNDS", "invalid bounded SID")
    end = offset + 8 + 4 * raw[offset + 1]
    if end > len(raw):
        fail("SD_BOUNDS", "SID exceeds buffer")
    nums = [str(int.from_bytes(raw[offset + 2:offset + 8], "big"))]
    nums += [str(v) for v in struct.unpack("<" + "I" * raw[offset + 1], raw[offset + 8:end])]
    return "S-1-" + "-".join(nums), end


def _sid_bytes(text):
    parts = text.split("-")
    if len(parts) < 3 or parts[:2] != ["S", "1"] or len(parts) > 18:
        fail("SD_SID", "invalid SID text")
    values = [int(x) for x in parts[2:]]
    return bytes((1, len(values) - 1)) + values[0].to_bytes(6, "big") + b"".join(
        n.to_bytes(4, "little") for n in values[1:])


def decode_sd(raw):
    """Bounded self-relative SD/standard ACE parser; raw bytes remain primary."""
    if type(raw) is not bytes or not 20 <= len(raw) <= MAX_SD:
        fail("SD_BOUNDS", "bounded serialized SD required")
    revision, _, control, owner, group, sacl, dacl = struct.unpack_from("<BBHIIII", raw)
    if revision != 1 or not control & 0x8000 or not control & 4 or not dacl or sacl:
        fail("SD_SHAPE", "self-relative owner/group/non-null DACL without SACL required")
    if any(n < 20 or n >= len(raw) for n in (owner, group, dacl)):
        fail("SD_BOUNDS", "SD offset outside serialized bytes")
    owner_sid, _ = _sid(raw, owner)
    group_sid, _ = _sid(raw, group)
    if dacl + 8 > len(raw):
        fail("SD_BOUNDS", "ACL header outside SD")
    rev, _, size, count, _ = struct.unpack_from("<BBHHH", raw, dacl)
    if rev not in (2, 4) or not 8 <= size <= len(raw) - dacl or count > 128:
        fail("SD_BOUNDS", "bounded ACL required")
    cursor, aces = dacl + 8, []
    for _ in range(count):
        if cursor + 8 > dacl + size:
            fail("SD_BOUNDS", "ACE header outside ACL")
        kind, flags, length, mask = struct.unpack_from("<BBHI", raw, cursor)
        if kind not in (0, 1) or length < 16 or cursor + length > dacl + size:
            fail("SD_SHAPE", "only bounded standard allow/deny ACEs supported")
        sid, end = _sid(raw, cursor + 8)
        if end != cursor + length:
            fail("SD_SHAPE", "unexpected ACE trailing bytes")
        aces.append((kind, flags, mask, sid))
        cursor += length
    if cursor != dacl + size:
        fail("SD_SHAPE", "unexpected ACL trailing bytes")
    return {"owner": owner_sid, "group": group_sid, "control": control,
            "aces": tuple(aces), "offsets": (owner, group, dacl)}


def check_security(raw, *, directory=False, service=False, service_sid=None, parent=False):
    sd = decode_sd(raw)
    if sd["owner"] not in {SY, BA}:
        fail("SD_OWNER", "SYSTEM/Administrators TCB owner required; no privilege is acquired here")
    if parent:
        # Inherit-only ACEs do not authorize parent mutation. Unknown/object
        # ACEs are rejected by decode_sd rather than guessed safe.
        write = 0x10000000 | 0x40000000 | 0x000D0156
        for kind, flags, mask, sid in sd["aces"]:
            if kind == 0 and not flags & 8 and sid not in {SY, BA} and mask & write:
                fail("PARENT_WRITABLE", "parent is writable outside the administrator TCB")
        return
    if sd["owner"] != SY or sd["group"] != BA or (not service and not sd["control"] & 0x1000):
        fail("SD_POLICY", "SYSTEM-owned protected code/config security required")
    flags, full = (0 if service or not directory else 3), (0xF01FF if service else 0x1F01FF)
    expected = {(0, flags, full, SY), (0, flags, full, BA)}
    if service_sid is not None:
        if not re.fullmatch(r"S-1-5-80-(\d+-){4}\d+", service_sid):
            fail("SERVICE_SID", "actual LookupAccountName service SID namespace required")
        expected.add((0, flags, 0x1200A9, service_sid))
    if len(sd["aces"]) != len(expected) or set(sd["aces"]) != expected:
        fail("SD_POLICY", "exact TCB control and optional service-only read/execute DACL required")


def _zip_inventory(raw):
    if not 0 < len(raw) <= MAX_ARCHIVE:
        fail("MATERIAL_ARCHIVE", "archive bytes exceed bound")
    try:
        archive = zipfile.ZipFile(io.BytesIO(raw))
        seen, total = set(), 0
        rows = archive.infolist()
        if len(rows) > MAX_FILES:
            fail("MATERIAL_ARCHIVE", "archive entry count exceeds bound")
        for row in rows:
            name = row.filename.rstrip("/")
            _relative(name)
            if name.casefold() in seen or row.flag_bits & 1 or (row.external_attr >> 16) & 0o170000 == 0o120000:
                fail("MATERIAL_ARCHIVE", "archive duplicate, encryption or symlink")
            seen.add(name.casefold())
            total += row.file_size
            if row.file_size > MAX_FILE or total > MAX_TOTAL:
                fail("MATERIAL_ARCHIVE", "archive expansion exceeds finite budget")
        return archive
    except (zipfile.BadZipFile, OSError) as exc:
        raise BootstrapError("MATERIAL_ARCHIVE", "invalid approved archive") from exc


def _normal_python_pe(raw):
    """Require AMD64 PE importing python312.dll, not a venv redirector."""
    if len(raw) < 256 or raw[:2] != b"MZ":
        fail("PYTHON_IMAGE", "normal CPython AMD64 PE required")
    pe = struct.unpack_from("<I", raw, 60)[0]
    if pe + 264 > len(raw) or raw[pe:pe + 4] != b"PE\0\0":
        fail("PYTHON_IMAGE", "invalid PE header")
    machine, sections = struct.unpack_from("<HH", raw, pe + 4)
    optional = struct.unpack_from("<H", raw, pe + 20)[0]
    opt = pe + 24
    if machine != 0x8664 or not 1 <= sections <= 96 or optional < 128 or struct.unpack_from("<H", raw, opt)[0] != 0x20B:
        fail("PYTHON_IMAGE", "normal CPython PE32+ AMD64 required")
    table = opt + optional
    if table + sections * 40 > len(raw):
        fail("PYTHON_IMAGE", "section table exceeds image")

    def offset(rva, size):
        for n in range(sections):
            base = table + n * 40
            virtual_size, virtual, raw_size, physical = struct.unpack_from("<IIII", raw, base + 8)
            delta = rva - virtual
            if 0 <= delta and delta + size <= min(virtual_size or raw_size, raw_size):
                if physical + delta + size <= len(raw):
                    return physical + delta
        fail("PYTHON_IMAGE", "PE RVA outside finite raw sections")

    rva, size = struct.unpack_from("<II", raw, opt + 120)
    if not rva or not 20 <= size <= 65536:
        fail("PYTHON_IMAGE", "bounded import descriptors required")
    imports = []
    for n in range(min(size // 20, 256)):
        descriptor = struct.unpack_from("<IIIII", raw, offset(rva + n * 20, 20))
        if not any(descriptor):
            if "python312.dll" not in imports:
                fail("PYTHON_IMAGE", "venv/indirect launcher is not the normal Python DLL importer")
            return
        start = offset(descriptor[3], 1)
        end = raw.find(b"\0", start, min(start + 128, len(raw)))
        if end < 0:
            fail("PYTHON_IMAGE", "unterminated import name")
        imports.append(raw[start:end].decode("ascii").lower())
    fail("PYTHON_IMAGE", "unterminated bounded import table")


def _binding(fn):
    return (getattr(fn, "__self__", None), getattr(fn, "__func__", fn))


class _Call:
    """Held before dispatch, including original function, args and output storage."""
    def __init__(self, owner, name, fn, args, mode, outputs=()):
        self.owner, self.name, self.fn, self.args = owner, name, fn, args
        self.mode, self.outputs = mode, outputs
        self.raw_result, self.scalar, self.error = None, None, None
        self.state = "NOT_INVOKED"
        owner.calls.append(self)

    def run(self):
        if self.state == "SUCCEEDED":
            return self.scalar
        if self.state != "NOT_INVOKED":
            fail("SDK_NO_REPEAT", "unreturned/failed original SDK call cannot be replayed")
        self.state = "INVOKING"
        try:
            self.raw_result = self.fn(*self.args)
            value = getattr(self.raw_result, "value", self.raw_result)
            if value is None and self.mode in {"HANDLE", "LOCALFREE"}:
                value = 0
            if type(value) is not int or not -(1 << 63) <= value < (1 << 64):
                fail("SDK_RETURN", "invalid original SDK scalar")
            if self.mode == "BOOL" and not -(1 << 31) <= value <= 0xFFFFFFFF:
                fail("SDK_RETURN", "invalid BOOL return")
            if self.mode in {"DWORD", "COUNT"} and not 0 <= value <= 0xFFFFFFFF:
                fail("SDK_RETURN", "invalid BOOL/DWORD return")
            self.scalar = value
            success = (value != 0 if self.mode in {"BOOL", "COUNT"} else value == 0
                       if self.mode in {"DWORD", "LOCALFREE"} else value not in {0, -1, (1 << 64) - 1})
            if not success:
                error = self.owner.api.last_error()
                if type(error) is not int or not 0 <= error <= 0xFFFFFFFF:
                    fail("SDK_ERROR", "invalid original GetLastError")
                self.error = value if self.mode == "DWORD" else error
                self.state = "KNOWN_FAILURE"
                raise KnownSdkFailure(self.name, self.error)
            self.state = "SUCCEEDED"
            return value
        except KnownSdkFailure:
            raise
        except BaseException:
            self.state = "UNKNOWN"
            raise


class _Handle:
    def __init__(self, owner, opened, closer_name, closer, parent=None, slot=None):
        self.owner, self.opened, self.value = owner, opened, None
        self.original_value, self.argument, self.parent = None, None, parent
        self.closer_name, self.closer = closer_name, closer
        self.original_binding, self.slot = _binding(self.closer), slot
        self.close_call, self.closed = None, False
        self.state = "PENDING"
        owner.handles.append(self)

    def adopt(self, value):
        if self.state != "PENDING" or type(value) is not int or not 0 < value < (1 << 64) - 1:
            fail("HANDLE_UNKNOWN", "original pending handle transfer is invalid")
        self.original_value = self.value = value
        self.argument = HANDLE(value)
        self.state = "ACQUIRED"
        return self

    def arg(self):
        if (self.state != "ACQUIRED" or self.closed or type(self.value) is not int or self.value != self.original_value
                or self.argument.value != self.original_value
                or (self.slot is not None and self.slot.value != self.original_value)):
            fail("HANDLE_DRIFT", "original SDK handle/storage drifted")
        return self.argument

    def close(self, *, retry_known=False):
        self.owner.check()
        if _binding(self.owner.api.function(self.closer_name)) != self.original_binding:
            fail("CLOSER_DRIFT", "original closer binding changed")
        if self.closed:
            return
        if any(h.parent is self and not h.closed for h in self.owner.handles):
            fail("CHILD_RETAINED", "parent handle still has an unresolved original child")
        arg = self.arg()
        if self.close_call is not None and self.close_call.state == "KNOWN_FAILURE" and retry_known:
            # A BOOL failure (notably INVALID_HANDLE) does not establish that
            # an original SCM/kernel handle still exists. LocalFree uniquely
            # returns the still-allocated original HLOCAL on documented failure.
            if self.closer_name != "LocalFree" or self.close_call.scalar != self.original_value:
                fail("CLOSE_RETRY_UNPROVEN", "SDK failure does not prove the original resource remains open")
            self.close_call = None
        if self.close_call is None:
            self.close_call = _Call(self.owner, self.closer_name, self.closer, (arg,),
                                    "LOCALFREE" if self.closer_name == "LocalFree" else "BOOL")
        self.close_call.run()
        # If bookkeeping raises, the confirmed scalar remains in the original
        # call holder; recovery reuses it, not the native closer.
        self.owner.checkpoint("close_confirmed", self)
        self.closed = True


class _Owner:
    def __init__(self, api):
        self.api, self.calls, self.handles, self.log = api, [], [], []
        self.thread = threading.get_ident()
        self.uncertain = False

    def checkpoint(self, event, original):
        self.log.append((event, original))

    def check(self):
        if self.thread != threading.get_ident():
            fail("OWNER_THREAD", "original installer thread required")
        if self.uncertain:
            fail("OWNER_UNKNOWN", "installer side-effect/output uncertainty retains original resources")
        if any(c.state in {"UNKNOWN", "INVOKING"} for c in self.calls):
            fail("SDK_UNKNOWN", "original output/result transfer remains unknown")
        if any(h.state in {"UNKNOWN", "PENDING"} for h in self.handles):
            fail("HANDLE_UNKNOWN", "original handle acquisition/registration remains unknown")

    def call(self, name, args, mode="BOOL", outputs=()):
        self.check()
        return _Call(self, name, self.api.function(name), args, mode, outputs).run()

    def acquire(self, name, args, closer, parent=None, keepalive=()):
        self.check()
        original_closer = self.api.function(closer)
        call = _Call(self, name, self.api.function(name), args, "HANDLE", (original_closer, *keepalive))
        handle = _Handle(self, call, closer, original_closer, parent)
        try:
            return handle.adopt(call.run())
        except KnownSdkFailure:
            handle.closed, handle.state = True, "NO_OUTPUT"
            raise
        except BaseException:
            handle.state = "UNKNOWN"
            raise

    def close_all(self):
        self.check()
        for handle in reversed(self.handles):
            if not handle.closed:
                handle.close()


@dataclass(frozen=True)
class NodeImage:
    # FILE_ID_INFO: full 64-bit volume serial and all 128 opaque ID bits.
    identity: tuple[int, int]
    directory: bool
    size: int
    sha256: str | None
    raw_sd: bytes
    attributes: int
    links: int
    file_times: tuple[int, int] = ()


@dataclass
class _Node:
    path: str
    relative: str
    handle: _Handle
    image: NodeImage | None = None
    bridge_handle: _Handle | None = None
    runtime_handle: _Handle | None = None
    delete_handle: _Handle | None = None
    label: bytes | None = None


@dataclass(frozen=True)
class _PinClose:
    """In-process pre-dispatch references for one pre-destructive pin close."""
    phase: str
    node: _Node
    role: str
    handle: _Handle
    opened: _Call
    argument: object
    value: int
    closer: object
    opened_args: tuple
    calls_before: tuple
    handles: tuple
    nodes: tuple


class Bootstrap:
    """Original local admin owner; never reconstructable from a receipt/dict."""
    def __init__(self, intent, backend, *, seal):
        if seal not in {_SEAL, _TEST_SEAL} or type(intent) is not BootstrapIntent:
            fail("ORIGINAL_OWNER", "private installer construction required")
        if seal is _SEAL and type(backend) is not _Windows:
            fail("ORIGINAL_OWNER", "native installation has no caller backend injection")
        self._seal, self.intent, self.backend = seal, intent, backend
        self.owner = backend.owner
        self._original_intent, self._original_backend, self._original_owner = intent, backend, self.owner
        self._original_schema, self._approved_raw = intent.schema, intent.original_raw
        self.state, self.sources, self.anchors, self.nodes = "NEW", {}, {}, {}
        self.manager = self.service = self.service_image = self.service_sid = None
        self.apply_started = self.restore_started = False
        self.handoff_started = False
        self._pin_close = None
        self._pin_close_ack = None
        self._restore_phase = None
        self.children_state = "NEVER_STARTED"
        self._retention_registered = False
        self._configuration = None
        self._original_configuration = None
        self._configuration_ack = None
        self.loaded_configs = None

    @property
    def children_state(self):
        return self._children_state

    @children_state.setter
    def children_state(self, value):
        # This installer has no child-start or child-drained API. Losing its
        # original never-started state cannot be reversed by a caller flag.
        if type(value) is not str or value not in {"NEVER_STARTED", "UNKNOWN", "LIVE"}:
            fail("CHILDREN_UNPROVEN", "closed diagnostic child states only; no caller proof wrapper")
        if hasattr(self, "_children_state") and self._children_state != "NEVER_STARTED" and value == "NEVER_STARTED":
            fail("CHILDREN_UNPROVEN", "a caller cannot reclassify children as never started")
        self._children_state = value

    def _retain(self):
        if not self._retention_registered:
            _retained.append(self)
            self._retention_registered = True

    def _guard(self, states):
        if (self._seal not in {_SEAL, _TEST_SEAL} or self.state not in states
                or self.intent is not self._original_intent or self.backend is not self._original_backend
                or self.owner is not self._original_owner or self.backend.owner is not self.owner
                or self.owner.api is not self.backend):
            fail("ORIGINAL_OWNER", "original installer state is not eligible")
        self.owner.check()
        if self.intent.schema != self._original_schema:
            fail("PLAN_DRIFT", "original plan version changed")
        if self._original_schema == SCHEMA_V2 and (self.intent.original_raw != self._approved_raw
                or parse_plan(self._approved_raw) != self.intent):
            fail("PLAN_DRIFT", "original approved V2 raw bytes and typed intent differ")
        if self._original_schema == SCHEMA_V2 and self._original_configuration is not None and (
                self._configuration is not self._original_configuration
                or self._original_configuration.original is not self):
            fail('CONFIG_ORIGIN', 'copied/replaced Bootstrap or configuration owner is ineligible')

    def prepare(self):
        self._guard({"NEW"})
        self._retain()
        try:
            p = self.intent
            self.manager = self.backend.manager(False)
            if not self.backend.service_absent(self.manager, p.service_name):
                fail("SERVICE_EXISTS", "initial service namespace must be absent")
            if not self.backend.absent(p.prefix):
                fail("PREFIX_EXISTS", "initial prefix must be absent, including empty directories")
            # Pin every ancestor through the exact source root and destination
            # parent. No existing directory or unrelated ACL is ever modified.
            paths = set()
            for path in (p.source_root, ntpath.dirname(p.prefix)):
                while len(path) > 3:
                    paths.add(path)
                    path = ntpath.dirname(path)
                paths.add(path)
            for path in sorted(paths, key=lambda x: (len(x), x)):
                handle = self.backend.open_node(path, True)
                image = self.backend.image(handle, path, True)
                self.anchors[path] = (handle, image)
            parent = ntpath.dirname(p.prefix)
            check_security(self.anchors[parent][1].raw_sd, parent=True)
            source_paths = set()
            for f in p.files:
                path = ntpath.join(p.source_root, *f.path.split("/"))
                directory = ntpath.dirname(path)
                while _within(directory, p.source_root) and directory != p.source_root:
                    source_paths.add(directory)
                    directory = ntpath.dirname(directory)
            for path in sorted(source_paths, key=lambda x: (len(x), x)):
                handle = self.backend.open_node(path, True)
                self.anchors[path] = (handle, self.backend.image(handle, path, True))
            for f in p.files:
                path = ntpath.join(p.source_root, *f.path.split("/"))
                handle = self.backend.open_node(path, False)
                image = self.backend.image(handle, path, False)
                if (image.size, image.sha256) != (f.size, f.sha256):
                    fail("SOURCE_BYTES", "actual pinned source bytes differ from approved inventory")
                self.sources[f.path] = (handle, image)
            self._materials()
            if p.schema == SCHEMA_V2:
                from ._windows_scm_config_materialization import prepare_configuration
                prepare_configuration(self)
            self.state = "PREPARED_READONLY"
            return self
        except BaseException as exc:
            unknown = (any(c.state in {"UNKNOWN", "INVOKING"} for c in self.owner.calls)
                       or any(h.state in {"UNKNOWN", "PENDING"} for h in self.owner.handles))
            self.state = "UNKNOWN" if unknown else "PREPARE_REFUSED"
            self.owner.uncertain = unknown
            raise BootstrapError(getattr(exc, "code", "PREPARE_FAILED"), str(exc), owner=self) from exc

    def _materials(self):
        def read(path):
            h, image = self.sources[path]
            if image.size > MAX_ARCHIVE:
                fail("MATERIAL_BYTES", "material required for parser exceeds read bound")
            return self.backend.read(h, image.size)
        python = read("python312/tools/python.exe")
        _normal_python_pe(python)
        with _zip_inventory(read("materials/python.nupkg")) as z:
            for name in ("python.exe", "python312.dll"):
                path = "python312/tools/" + name
                try:
                    member = z.getinfo("tools/" + name)
                    if member.file_size != self.sources[path][1].size or member.file_size > MAX_ARCHIVE:
                        fail("PYTHON_LAYOUT", "normal Python member length differs from approved source")
                    with z.open(member) as stream:
                        raw = stream.read(member.file_size + 1)
                except KeyError as exc:
                    raise BootstrapError("PYTHON_LAYOUT", "normal Python nupkg member missing") from exc
                if digest(raw) != self.sources[path][1].sha256:
                    fail("PYTHON_LAYOUT", "normal Python exe/DLL must match the pinned original nupkg")
        with _zip_inventory(read("materials/offline.zip")):
            pass  # hashed, bounded original retained; no pip/script is executed

    def preview(self):
        self._guard({"PREPARED_READONLY", "INSTALLED_STOPPED_START_BLOCKED", "PARTIAL_KNOWN",
                     "RUNTIME_READ_PINNED_START_BLOCKED"})
        return {"schema": self.intent.schema, "plan_sha256": self.intent.sha256,
                "state": self.state, "service_name": self.intent.service_name,
                "prefix": self.intent.prefix, "argv": list(self.intent.argv),
                "quoted_command_line": self.intent.command_line,
                "files": len(self.intent.files), "bytes": sum(f.size for f in self.intent.files),
                "required_privileges": list(PRIVILEGES), "SID_type": "RESTRICTED",
                "native_start": ("NOT_RUN_SYSTEM_RUNTIME_AND_POLICY_QUALIFICATION_REQUIRED" if self.intent.schema == SCHEMA_V2
                    else "BLOCKED_TRUSTED_PROFILE_APPCONTAINER_PROJECT_NOT_MATERIALIZED"),
                "authority": "LOCAL_INSTALLER_ONLY_NOT_T037",
                "runtime": "NORMAL_OFFLINE_MATERIALIZED_PREFIX; NO_VENV; NO_PIP_EXECUTION",
                "static_runtime_read_pins": self.state == "RUNTIME_READ_PINNED_START_BLOCKED",
                "service_state_write_acl": ("STATIC_BOUND_SERVICE_DATA_POLICY_NOT_NATIVE_VERIFIED"
                    if self._configuration is not None and self._configuration.state_descriptor is not None
                    and 'service-state' in self.nodes and self.nodes['service-state'].image is not None
                    and self.nodes['service-state'].image.raw_sd == self._configuration.state_descriptor
                    else "NOT_GRANTED_REQUIRES_LATER_EXPLICIT_SCOPE"),
                "trusted_profile_materialization": ("STATIC_FOUR_FILES_ONLY" if self._configuration is not None
                    and self._configuration.resolved is not None else "NOT_PERFORMED"),
                "token_precondition": self.backend.context(),
                "full_AppContainer_restore": "RESTORE_NOT_IMPLEMENTED_IN_EXISTING_LEAF",
                "apply_ready_package_claim": False}

    def _check_sources_anchors(self):
        for path, (handle, image) in self.anchors.items():
            if self.backend.image(handle, path, True) != image:
                fail("ANCHOR_DRIFT", "pinned source/parent directory identity or raw SD changed")
        for f in self.intent.files:
            handle, image = self.sources[f.path]
            path = ntpath.join(self.intent.source_root, *f.path.split("/"))
            if self.backend.image(handle, path, False) != image:
                fail("SOURCE_DRIFT", "pinned source bytes/identity/security changed")

    def apply(self, *, approved_plan_sha256):
        self._guard({"PREPARED_READONLY"})
        if approved_plan_sha256 != self.intent.sha256 or type(approved_plan_sha256) is not str:
            fail("LOCAL_APPROVAL_REQUIRED", "explicit exact local administrator plan approval required")
        if self.intent.schema == SCHEMA_V2:
            from ._windows_scm_config_materialization import apply_configuration
            return apply_configuration(self)
        context = self.backend.context()
        if not context.get("elevated_admin") or not context.get("can_assign_SYSTEM_owner"):
            fail("SYSTEM_OWNER_CONTEXT_REQUIRED", "already capable elevated token required; this installer enables no privileges")
        self._check_sources_anchors()
        if not self.backend.absent(self.intent.prefix) or not self.backend.service_absent(self.manager, self.intent.service_name):
            fail("NAMESPACE_COLLISION", "namespace changed since prepare")
        self.apply_started, self.state = True, "APPLYING"
        try:
            p = self.intent
            for relative in p.directories:
                path = p.destination(relative)
                handle = self.backend.create_directory(path, self.backend.sd(True), None)
                node = _Node(path, relative, handle)
                self.nodes[relative] = node
                node.image = self.backend.image(handle, path, True)
                check_security(node.image.raw_sd, directory=True)
            for f in p.files:
                path = p.destination(f.path)
                source, _ = self.sources[f.path]
                handle = self.backend.create_file(path, self.backend.sd(False), source, f.size)
                node = _Node(path, f.path, handle)
                self.nodes[f.path] = node
                node.image = self.backend.image(handle, path, False)
                check_security(node.image.raw_sd)
                if (node.image.size, node.image.sha256) != (f.size, f.sha256):
                    fail("INSTALL_READBACK", "new exclusive copy differs from approved bytes")
            writable_manager = self.backend.manager(True)
            self.service = self.backend.create_service(writable_manager, p)
            self.service_image = self.backend.service_image(self.service)
            self.backend.check_initial_service(self.service_image, p)
            for field, value in (("sid_type", 3), ("privileges", tuple(sorted(PRIVILEGES))),
                                 ("failure_actions", (0, "", "", ())), ("failure_flag", False)):
                before = self.service_image
                try:
                    self.backend.configure(self.service, field, value)
                except KnownSdkFailure:
                    if self.backend.service_image(self.service) != before:
                        fail("SCM_FAILURE_DRIFT", "failed configuration changed original postimage")
                    raise
                expected = {**before, field: value}
                actual = self.backend.service_image(self.service)
                if actual != expected:
                    fail("SCM_READBACK", "actual SCM configuration differs from exact expected postimage")
                self.service_image = actual
            before = self.service_image
            self.backend.service_security(self.service, self.backend.sd(False, service=True))
            actual = self.backend.service_image(self.service)
            check_security(actual["raw_sd"], service=True)
            if {k: v for k, v in actual.items() if k != "raw_sd"} != {k: v for k, v in before.items() if k != "raw_sd"}:
                fail("SCM_READBACK", "service base config changed during DACL setup")
            self.service_image = actual
            self.service_sid = self.backend.lookup_service_sid(p.service_name)
            for node in self.nodes.values():
                self.backend.file_security(node.handle, self.backend.sd(node.image.directory, service_sid=self.service_sid))
                image = self.backend.image(node.handle, node.path, node.image.directory)
                check_security(image.raw_sd, directory=image.directory, service_sid=self.service_sid)
                if (image.identity, image.sha256, image.size) != (node.image.identity, node.image.sha256, node.image.size):
                    fail("TREE_READBACK", "owned file identity/bytes changed during ACL setup")
                node.image = image
            self._compare()
            self.state = "INSTALLED_STOPPED_START_BLOCKED"
            return self.preview()
        except BaseException as exc:
            # No automatic rollback or write retry. A documented failure may
            # permit explicit rollback only if every known original postimage
            # is still exact; ambiguous outputs always remain retained.
            known = isinstance(exc, KnownSdkFailure)
            try:
                if not known:
                    raise exc
                self._compare()
                self.state = "PARTIAL_KNOWN"
            except BaseException:
                self.state = "UNKNOWN"
            if self.state == "UNKNOWN":
                self.owner.uncertain = True
            raise BootstrapError(getattr(exc, "code", "APPLY_UNKNOWN"), str(exc), owner=self) from exc

    def _original_handle(self, handle):
        if (type(handle) is not _Handle or handle.owner is not self.owner
                or handle not in self.owner.handles or handle.opened not in self.owner.calls
                or handle.opened.owner is not self.owner or handle.opened.state != "SUCCEEDED"
                or handle.opened.scalar != handle.original_value):
            fail("ORIGINAL_HANDLE", "only the actual original SDK acquisition is eligible")
        if handle.closed:
            if (handle.close_call is None or handle.close_call.state != "SUCCEEDED"
                    or not handle.close_call.scalar):
                fail("CLOSE_UNPROVEN", "original HANDLE close is not confirmed")
        else:
            handle.arg()
            if handle.close_call is not None:
                fail("CLOSE_UNPROVEN", "unfinished/failed close cannot be treated as an open resource")

    def _pin(self, node, handle, role):
        self._original_handle(handle)
        read = 0x20080 if node.image.directory else 0x80020000
        access, share = (read | 0x10000, 5) if role == "delete" else (read, 7 if role == "bridge" else 5)
        args = handle.opened.args
        if (handle.opened.name != "CreateFileW" or len(args) != 7 or args[0] != node.path
                or args[1:5] != (access, share, None, 3)
                or args[5:] != (0x00200000 | (0x02000000 if node.image.directory else 0x80), None)):
            fail("PIN_ACCESS", "new pin must be its exact scoped readonly/DELETE SDK acquisition")

    def _compare_node(self, node):
        if node.image is None:
            fail("TREE_DRIFT", "original created postimage is missing")
        self._original_handle(node.handle)  # never replace or rewrap the original writer
        if node.handle.closed and not self.handoff_started:
            fail("WRITER_UNPROVEN", "original writer was closed outside an explicit handoff")
        live = [] if node.handle.closed else [node.handle]
        for role, handle in (("bridge", node.bridge_handle), ("runtime", node.runtime_handle),
                             ("delete", node.delete_handle)):
            if handle is not None:
                self._pin(node, handle, role)
                if not handle.closed:
                    live.append(handle)
        if not live:
            fail("PIN_GAP", "no original owned HANDLE spans the known file object")
        for handle in live:
            if self.backend.image(handle, node.path, node.image.directory) != node.image:
                fail("TREE_DRIFT", "original live HANDLE identity/bytes/raw SD differ")
            if self.intent.schema == SCHEMA_V2:
                self._configuration.compare_label(node, handle)
        if self.backend.fresh_image(node.path, node.image.directory) != node.image:
            fail("TREE_DRIFT", "fresh fileID/bytes/raw SD differ from original created postimage")

    def _close_pin(self, node, role, phase):
        if self.intent.schema == SCHEMA_V2:
            self._configuration.compare_generated_manifest()
        handle = getattr(node, role)
        self._pin_close_ack = None
        self._pin_close = _PinClose(phase, node, role, handle, handle.opened, handle.argument,
            handle.original_value, handle.closer, handle.opened.args, tuple(self.owner.calls),
            tuple(self.owner.handles), tuple((key, n, n.handle, n.bridge_handle, n.runtime_handle,
                                             n.delete_handle) for key, n in self.nodes.items()))
        handle.close()  # original _Handle/_Call cache and original native closer only
        self._pin_close = None

    def _known_pin_close(self):
        """Pure metadata validation; never consume raw_result or query/close an OS object."""
        f = self._pin_close
        if type(f) is not _PinClose:
            return False
        self._guard({"HANDING_OFF_STATIC_PINS", "HANDOFF_CLOSE_ACK_PENDING",
                     "RESTORING", "RESTORE_CLOSE_ACK_PENDING"})
        if (self.handoff_started is not True
                or (f.phase == "handoff" and (self.restore_started is not False or self._restore_phase is not None
                    or f.role not in {"handle", "bridge_handle"}
                    or self.state not in {"HANDING_OFF_STATIC_PINS", "HANDOFF_CLOSE_ACK_PENDING"}))
                or (f.phase == "restore" and (self.restore_started is not True
                    or self._restore_phase != "PRE_DESTRUCTIVE_CLOSE"
                    or f.role not in {"runtime_handle", "bridge_handle"}
                    or self.state not in {"RESTORING", "RESTORE_CLOSE_ACK_PENDING"}))
                or f.phase not in {"handoff", "restore"}):
            return False
        h, c = f.handle, f.handle.close_call
        if (type(h) is not _Handle or h.owner is not self.owner or h.state != "ACQUIRED"
                or h.opened is not f.opened or h.argument is not f.argument
                or type(h.original_value) is not int or h.original_value != f.value
                or h.value != f.value or h.argument.value != f.value
                or (h.slot is not None and h.slot.value != f.value)
                or h.opened.owner is not self.owner or h.opened.state != "SUCCEEDED"
                or h.opened.scalar != f.value or h.opened.args != f.opened_args
                or h.closer is not f.closer or h.closer_name != "CloseHandle"
                or _binding(f.closer) != h.original_binding
                or _binding(self.backend.function(h.closer_name)) != h.original_binding
                or type(c) is not _Call or c.owner is not self.owner or c.fn is not f.closer
                or c.name != h.closer_name or c.mode != "BOOL" or c.state != "SUCCEEDED"
                or type(c.scalar) is not int or not c.scalar
                or len(c.args) != 1 or c.args[0] is not f.argument
                or tuple(self.owner.calls) != (*f.calls_before, c)
                or tuple(self.owner.handles) != f.handles):
            return False
        if self.state in {"HANDOFF_CLOSE_ACK_PENDING", "RESTORE_CLOSE_ACK_PENDING"}:
            if (self._pin_close_ack is None or self._pin_close_ack[0] is not f
                    or self._pin_close_ack[1] is not c or self._pin_close_ack[2] != c.scalar):
                return False
        current = tuple((key, n, n.handle, n.bridge_handle, n.runtime_handle, n.delete_handle)
                        for key, n in self.nodes.items())
        if (len(current) != len(f.nodes) or any(a[0] != z[0] or any(x is not y for x, y in zip(a[1:], z[1:]))
                                             for a, z in zip(current, f.nodes))
                or getattr(f.node, f.role) is not h):
            return False
        # No second unresolved release, even when a separate BOOL failure is known.
        return all(other is h or other.close_call is None or other.closed for other in self.owner.handles)

    def _acknowledged_pin_close(self):
        if self.state not in {"HANDOFF_CLOSE_ACK_PENDING", "RESTORE_CLOSE_ACK_PENDING"}:
            return None
        if not self._known_pin_close() or not self._pin_close.handle.closed:
            fail("CLOSE_ACK_REQUIRED", "exact original cached close must be acknowledged before restore")
        phase = self._pin_close.phase
        self._pin_close = None
        self._pin_close_ack = None
        self.state = "PARTIAL_KNOWN" if phase == "handoff" else "RESTORE_PRE_DESTRUCTIVE"
        return phase

    def handoff_runtime(self):
        """Static read pins only. No start, issuer, profile or runtime child authority."""
        self._guard({"INSTALLED_STOPPED_START_BLOCKED"})
        if self.handoff_started or self.restore_started:
            fail("HANDOFF_NO_REPEAT", "one original writer-to-read handoff only")
        try:
            self._compare()  # complete namespace/SCM/static inventory before any writer closes
            self.handoff_started, self.state = True, "HANDING_OFF_STATIC_PINS"
            for node in self.nodes.values():
                if self.intent.schema == SCHEMA_V2:
                    self._configuration.compare_generated_manifest()
                node.bridge_handle = self.backend.bridge_node(node.path, node.image.directory)
                self._compare_node(node)
                self._close_pin(node, "handle", "handoff")
                self._compare_node(node)  # bridge preserves identity across confirmed writer close
                if self.intent.schema == SCHEMA_V2:
                    self._configuration.compare_generated_manifest()
                node.runtime_handle = self.backend.runtime_node(node.path, node.image.directory)
                self._compare_node(node)
                self._close_pin(node, "bridge_handle", "handoff")
                self._compare_node(node)
            self._compare()
            if any(not n.handle.closed or not n.bridge_handle.closed or n.runtime_handle.closed
                   for n in self.nodes.values()):
                fail("HANDOFF_INCOMPLETE", "all original writers/bridges must close before static read readiness")
            self.state = "RUNTIME_READ_PINNED_START_BLOCKED"
            return self.preview()
        except BaseException as exc:
            try:
                known_ack = self._known_pin_close()
            except BaseException:
                known_ack = False
            if known_ack:
                self._pin_close_ack = (self._pin_close, self._pin_close.handle.close_call,
                                       self._pin_close.handle.close_call.scalar)
                self.state = "HANDOFF_CLOSE_ACK_PENDING"
                raise BootstrapError("CLOSE_ACK_REQUIRED", str(exc), owner=self) from exc
            # A new read-open can fail definitively without an unknown effect.
            # Exact original comparisons may allow explicit restore, never a
            # second handoff attempt or automatic acquisition/close retry.
            try:
                if (not isinstance(exc, KnownSdkFailure)
                        or exc.function in {"CloseHandle", "CloseServiceHandle", "FindClose", "LocalFree"}):
                    raise exc
                self._compare()
                self.state = "PARTIAL_KNOWN"
            except BaseException:
                self.state, self.owner.uncertain = "UNKNOWN", True
            raise BootstrapError(getattr(exc, "code", "HANDOFF_UNKNOWN"), str(exc), owner=self) from exc

    def _compare(self):
        self.owner.check()
        if any(h.close_call is not None and not h.closed for h in self.owner.handles):
            fail("CLOSE_UNPROVEN", "an unfinished/failed original close forbids new transition or deletion")
        if self.children_state != "NEVER_STARTED":
            fail("CHILDREN_UNKNOWN", "live/unknown owned children forbid restore")
        self._check_sources_anchors()
        if self.intent.schema == SCHEMA_V2:
            self._configuration.check_original(self)
            self._configuration.compare_external()
        if "" not in self.nodes and not self.backend.absent(self.intent.prefix):
            fail("PREFIX_UNOWNED", "created or replaced prefix has no original acquired HANDLE")
        if self.service is not None:
            actual = self.backend.service_image(self.service)
            if (actual != self.service_image or actual["state"] != 1 or actual["pid"] != 0
                    or actual.get("dependents")):
                fail("SERVICE_DRIFT", "original service config/raw SD/status differs or is live")
            self.backend.compare_service_name(self.manager, self.intent.service_name, actual)
        elif not self.backend.service_absent(self.manager, self.intent.service_name):
            fail("SERVICE_DRIFT", "unowned service exists; never adopt by name")
        for relative, node in self.nodes.items():
            self._compare_node(node)
            if node.image.directory:
                expected = {ntpath.basename(n.path) for n in self.nodes.values()
                            if ntpath.dirname(n.path) == node.path}
                if self.backend.names(node.path) != expected:
                    fail("EXTRA_CHILDREN", "new/unknown directory children are preserved")

    def restore(self):
        acknowledged_phase = self._acknowledged_pin_close()
        resuming = acknowledged_phase == "restore"
        self._guard({"INSTALLED_STOPPED_START_BLOCKED", "PARTIAL_KNOWN", "RUNTIME_READ_PINNED_START_BLOCKED",
                     "RESTORE_PRE_DESTRUCTIVE"})
        if (self.restore_started and not resuming) or not self.apply_started:
            fail("RESTORE_NO_REPEAT", "original one-shot restore only; no JSON/name adoption")
        try:
            self._compare()  # all comparisons precede the first destructive call
        except BaseException as exc:
            self.state = "UNKNOWN"
            self.owner.uncertain = True
            raise BootstrapError(getattr(exc, "code", "RESTORE_COMPARE_UNKNOWN"), str(exc), owner=self) from exc
        self.restore_started, self.state = True, "RESTORING"
        try:
            if self.handoff_started:
                self._restore_phase = "ACQUIRE_DELETE" if not resuming else "PRE_DESTRUCTIVE_CLOSE"
                for node in self.nodes.values():
                    if node.handle.closed and not resuming:
                        node.delete_handle = self.backend.restore_node(node.path, node.image.directory)
                        self._compare_node(node)
                self._compare()  # all new DELETE pins bound before any destructive call
                self._restore_phase = "PRE_DESTRUCTIVE_CLOSE"
                for node in reversed(tuple(self.nodes.values())):
                    for role in ("runtime_handle", "bridge_handle"):
                        handle = getattr(node, role)
                        if handle is not None and not handle.closed:
                            self._close_pin(node, role, "restore")
                self._compare()  # every closed runtime pin has a known original DELETE successor
            self._restore_phase = "DESTRUCTIVE"
            if self.service is not None:
                self.backend.delete_service(self.service)
                self.service.close()
                if not self.backend.service_absent(self.manager, self.intent.service_name):
                    fail("SERVICE_DELETE_PENDING", "marked service is not confirmed absent; retain tree")
            removed = set()
            for node in sorted(self.nodes.values(), key=lambda n: (len(n.path), n.path), reverse=True):
                self._compare_node(node)
                if node.image.directory:
                    expected = {ntpath.basename(n.path) for n in self.nodes.values()
                                if ntpath.dirname(n.path) == node.path and n.path not in removed}
                    if self.backend.names(node.path) != expected:
                        fail("EXTRA_CHILDREN", "unexpected children appeared during restore; preserve them")
                handle = node.delete_handle if node.handle.closed else node.handle
                self.backend.delete_node(handle)
                handle.close()
                removed.add(node.path)
            if not self.backend.absent(self.intent.prefix):
                fail("PREFIX_DELETE_UNKNOWN", "original namespace is not confirmed absent")
            self.owner.close_all()
            self.state = "RESTORED"
            if self in _retained:
                _retained.remove(self)
            return {"state": "RESTORED", "authority": "ORIGINAL_IN_PROCESS_INSTALLER",
                    "source_archives_preserved": True, "native_start": "NOT_RUN"}
        except BaseException as exc:
            try:
                known_ack = self._known_pin_close()
            except BaseException:
                known_ack = False
            if known_ack:
                self._pin_close_ack = (self._pin_close, self._pin_close.handle.close_call,
                                       self._pin_close.handle.close_call.scalar)
                self.state = "RESTORE_CLOSE_ACK_PENDING"
                raise BootstrapError("CLOSE_ACK_REQUIRED", str(exc), owner=self) from exc
            self.state = "UNKNOWN"
            self.owner.uncertain = True
            raise BootstrapError("RESTORE_UNKNOWN", str(exc), owner=self) from exc

    def abandon_readonly(self):
        self._guard({"PREPARED_READONLY", "PREPARE_REFUSED"})
        if self.apply_started:
            fail("ORIGINAL_OWNER", "installed resources require compare-and-restore")
        try:
            self.owner.close_all()
            self.state = "CLOSED_READONLY"
            if self in _retained:
                _retained.remove(self)
        except BaseException as exc:
            self.state = "UNKNOWN"
            self.owner.uncertain = True
            raise BootstrapError("CLOSE_UNKNOWN", str(exc), owner=self) from exc

    def acknowledge_configuration(self):
        """Original cached-success metadata only; never repeats a native setter."""
        from ._windows_scm_config_materialization import acknowledge_configuration
        return acknowledge_configuration(self)

    def load_configurations(self):
        """Normal four loaders under the original SYSTEM actor; no start/issuer."""
        from ._windows_scm_config_materialization import load_configurations
        return load_configurations(self)


def retained_bootstrap_owners():
    return tuple(_retained)


def prepare(raw):
    """Native read-only prepare. Returns original owner, not wire authority."""
    with _lock:
        if _retained:
            fail("ORIGINAL_OWNER_RETAINED", "finish original installer before a new query/acquisition")
        intent = parse_plan(raw)
        backend = _Windows()
        return Bootstrap(intent, backend, seal=_SEAL).prepare()


def inspect(raw):
    original = prepare(raw)
    result = original.preview()
    original.abandon_readonly()
    return result


# Windows SDK winsvc.h/winnt.h/fileapi.h ABI. Fixed-width scalars also make
# software layout tests meaningful on a non-Windows development host.
class _FileTime(C.Structure):
    _fields_ = [("low", DWORD), ("high", DWORD)]


class _FileInfo(C.Structure):
    _fields_ = [("attributes", DWORD), ("creation", _FileTime), ("access", _FileTime),
                ("write", _FileTime), ("volume", DWORD), ("size_high", DWORD),
                ("size_low", DWORD), ("links", DWORD), ("index_high", DWORD),
                ("index_low", DWORD)]


class _FileIdInfo(C.Structure):
    _fields_ = [("volume", C.c_uint64), ("file_id", C.c_ubyte * 16)]


class _SecurityAttributes(C.Structure):
    _fields_ = [("length", DWORD), ("descriptor", HANDLE), ("inherit", BOOL)]


class _QueryConfig(C.Structure):
    _fields_ = [("service_type", DWORD), ("start_type", DWORD), ("error_control", DWORD),
                ("binary", HANDLE), ("group", HANDLE), ("tag", DWORD),
                ("dependencies", HANDLE), ("account", HANDLE), ("display", HANDLE)]


class _ServiceStatus(C.Structure):
    _fields_ = [(n, DWORD) for n in ("service_type", "state", "controls", "win32_exit",
                "service_exit", "checkpoint", "wait_hint", "pid", "flags")]


class _Dependent(C.Structure):
    _fields_ = [("name", HANDLE), ("display", HANDLE), ("status", DWORD * 7)]


class _Disposition(C.Structure):
    _fields_ = [("delete_file", C.c_ubyte)]


class _SidInfo(C.Structure):
    _fields_ = [("value", DWORD)]


class _PrivilegesInfo(C.Structure):
    _fields_ = [("names", HANDLE)]


class _FailureActions(C.Structure):
    _fields_ = [("reset", DWORD), ("reboot", HANDLE), ("command", HANDLE),
                ("count", DWORD), ("actions", HANDLE)]


class _Action(C.Structure):
    _fields_ = [("kind", DWORD), ("delay", DWORD)]


class _Luid(C.Structure):
    _fields_ = [("low", DWORD), ("high", C.c_int32)]


class _FindData(C.Structure):
    _fields_ = [("attributes", DWORD), ("creation", _FileTime), ("access", _FileTime),
                ("write", _FileTime), ("size_high", DWORD), ("size_low", DWORD),
                ("reserved0", DWORD), ("reserved1", DWORD),
                ("name", C.c_uint16 * 260), ("alternate", C.c_uint16 * 14)]


def _bounded_string(buffer, used, pointer, *, multi=False):
    if pointer is None:
        return () if multi else ""
    base = C.addressof(buffer)
    if (type(pointer) is not int or pointer % 2 or not base <= pointer < base + used
            or used > C.sizeof(buffer)):
        fail("SDK_POINTER", "SCM string pointer outside its original output buffer")
    raw = C.string_at(pointer, used - (pointer - base))
    values, cursor = [], 0
    while cursor + 2 <= len(raw):
        end = next((n for n in range(cursor, len(raw) - 1, 2) if raw[n:n + 2] == b"\0\0"), -1)
        if end < 0:
            break
        text = raw[cursor:end].decode("utf-16-le", "strict")
        if not multi:
            return text
        if not text:
            return tuple(values)
        values.append(text)
        if len(values) > 128:
            break
        cursor = end + 2
    fail("SDK_POINTER", "unterminated bounded SCM string/multi-string")


class _Windows:
    """Real SDK backend. Injection exists only in tests through the private seal."""
    def __init__(self):
        if os.name != "nt" or C.sizeof(HANDLE) != 8:
            fail("WINDOWS_REQUIRED", "native bootstrap requires 64-bit Windows")
        self.kernel = C.WinDLL("kernel32", use_last_error=True)
        self.advapi = C.WinDLL("advapi32", use_last_error=True)
        self.sdk = {}
        self._get_last_error = C.get_last_error
        P, W, D, B = HANDLE, C.c_wchar_p, DWORD, BOOL
        signatures = {
            "OpenSCManagerW": (self.advapi, P, [W, W, D]),
            "OpenServiceW": (self.advapi, P, [P, W, D]),
            "CreateServiceW": (self.advapi, P, [P, W, W, D, D, D, D, W, W, C.POINTER(D), W, W, W]),
            "CloseServiceHandle": (self.advapi, B, [P]),
            "ChangeServiceConfig2W": (self.advapi, B, [P, D, P]),
            "QueryServiceConfigW": (self.advapi, B, [P, P, D, C.POINTER(D)]),
            "QueryServiceConfig2W": (self.advapi, B, [P, D, P, D, C.POINTER(D)]),
            "QueryServiceStatusEx": (self.advapi, B, [P, D, P, D, C.POINTER(D)]),
            "EnumDependentServicesW": (self.advapi, B, [P, D, P, D, C.POINTER(D), C.POINTER(D)]),
            "QueryServiceObjectSecurity": (self.advapi, B, [P, D, P, D, C.POINTER(D)]),
            "SetServiceObjectSecurity": (self.advapi, B, [P, D, P]),
            "DeleteService": (self.advapi, B, [P]),
            "LookupAccountNameW": (self.advapi, B, [W, W, P, C.POINTER(D), W, C.POINTER(D), C.POINTER(D)]),
            "ConvertStringSecurityDescriptorToSecurityDescriptorW": (self.advapi, B, [W, D, C.POINTER(P), C.POINTER(D)]),
            "GetKernelObjectSecurity": (self.advapi, B, [P, D, P, D, C.POINTER(D)]),
            "SetSecurityInfo": (self.advapi, D, [P, D, D, P, P, P, P]),
            "OpenProcessToken": (self.advapi, B, [P, D, C.POINTER(P)]),
            "GetTokenInformation": (self.advapi, B, [P, D, P, D, C.POINTER(D)]),
            "CheckTokenMembership": (self.advapi, B, [P, P, C.POINTER(B)]),
            "LookupPrivilegeValueW": (self.advapi, B, [W, W, C.POINTER(_Luid)]),
            "CreateFileW": (self.kernel, P, [W, D, D, P, D, D, P]),
            "CreateDirectoryW": (self.kernel, B, [W, P]),
            "GetFileInformationByHandle": (self.kernel, B, [P, C.POINTER(_FileInfo)]),
            "GetFileInformationByHandleEx": (self.kernel, B, [P, C.c_int32, P, D]),
            "GetFinalPathNameByHandleW": (self.kernel, D, [P, W, D, D]),
            "ReadFile": (self.kernel, B, [P, P, D, C.POINTER(D), P]),
            "WriteFile": (self.kernel, B, [P, P, D, C.POINTER(D), P]),
            "SetFilePointerEx": (self.kernel, B, [P, C.c_int64, C.POINTER(C.c_int64), D]),
            "FlushFileBuffers": (self.kernel, B, [P]),
            "SetFileInformationByHandle": (self.kernel, B, [P, D, P, D]),
            "CloseHandle": (self.kernel, B, [P]),
            "LocalFree": (self.kernel, P, [P]),
            "FindFirstFileW": (self.kernel, P, [W, C.POINTER(_FindData)]),
            "FindNextFileW": (self.kernel, B, [P, C.POINTER(_FindData)]),
            "FindClose": (self.kernel, B, [P]),
        }
        for name, (library, result, args) in signatures.items():
            fn = getattr(library, name)
            fn.restype, fn.argtypes = result, args
            self.sdk[name] = fn
        self.owner = _Owner(self)
        self._context = None

    @classmethod
    def _test(cls, sdk, get_last_error, *, seal):
        if seal is not _TEST_SEAL:
            fail("TEST_SEAL", "private software adapter seal required")
        obj = object.__new__(cls)
        obj.kernel = obj.advapi = sdk
        obj.sdk, obj._get_last_error = dict(sdk), get_last_error
        obj.owner, obj._context = _Owner(obj), None
        return obj

    def function(self, name):
        return self.sdk[name]

    def last_error(self):
        return self._get_last_error()

    def manager(self, writable):
        return self.owner.acquire("OpenSCManagerW", (None, None, 3 if writable else 1),
                                  "CloseServiceHandle")

    def service_absent(self, manager, name):
        try:
            handle = self.owner.acquire("OpenServiceW", (manager.arg(), name, 0x20005),
                                        "CloseServiceHandle", manager)
        except KnownSdkFailure as exc:
            if exc.winerror == 1060:
                return True
            raise
        handle.close()
        return False

    def create_service(self, manager, plan):
        # No start/stop rights, dependencies, password, account creation or
        # executable shell. Original argv/quoted command line are both pinned.
        return self.owner.acquire("CreateServiceW",
            (manager.arg(), plan.service_name, plan.service_name, 0xF000F,
             0x10, 3, 1, plan.command_line, None, None, None, "LocalSystem", None),
            "CloseServiceHandle", manager)

    def _query(self, name, handle, level=None):
        buffer, needed = C.create_string_buffer(MAX_SCM_BUFFER), DWORD()
        args = (handle.arg(), buffer, len(buffer), C.byref(needed))
        if level is not None:
            args = (handle.arg(), level, buffer, len(buffer), C.byref(needed))
        self.owner.call(name, args, outputs=(buffer, needed))
        if not 4 <= needed.value <= len(buffer):
            fail("SDK_BOUNDS", "SCM output size outside original fixed buffer")
        return buffer, needed.value

    def _raw_sd(self, handle, service=False):
        limit = MAX_SCM_BUFFER if service else MAX_SD
        buffer, needed = C.create_string_buffer(limit), DWORD()
        fn = "QueryServiceObjectSecurity" if service else "GetKernelObjectSecurity"
        self.owner.call(fn, (handle.arg(), 7, buffer, limit, C.byref(needed)), outputs=(buffer, needed))
        if not 20 <= needed.value <= limit:
            fail("SD_BOUNDS", "actual serialized SD size exceeds output storage")
        raw = bytes(buffer.raw[:needed.value])
        decode_sd(raw)
        return raw

    def service_image(self, handle):
        b, used = self._query("QueryServiceConfigW", handle)
        if used < C.sizeof(_QueryConfig):
            fail("SDK_BOUNDS", "SCM config header truncated")
        config = _QueryConfig.from_buffer(b)
        result = {"service_type": config.service_type, "start_type": config.start_type,
                  "error_control": config.error_control, "binary": _bounded_string(b, used, config.binary),
                  "group": _bounded_string(b, used, config.group), "tag": config.tag,
                  "dependencies": _bounded_string(b, used, config.dependencies, multi=True),
                  "account": _bounded_string(b, used, config.account),
                  "display": _bounded_string(b, used, config.display)}
        for level, field in ((5, "sid_type"), (4, "failure_flag")):
            b, used = self._query("QueryServiceConfig2W", handle, level)
            value = DWORD.from_buffer(b).value
            if field == "failure_flag" and value not in (0, 1):
                fail("SDK_BOUNDS", "invalid service failure flag")
            result[field] = bool(value) if field == "failure_flag" else value
        b, used = self._query("QueryServiceConfig2W", handle, 6)
        if used < C.sizeof(_PrivilegesInfo):
            fail("SDK_BOUNDS", "SCM privilege header truncated")
        privileges = _bounded_string(b, used, _PrivilegesInfo.from_buffer(b).names, multi=True)
        if len(privileges) != len(set(privileges)):
            fail("SDK_BOUNDS", "duplicate actual required privileges")
        result["privileges"] = tuple(sorted(privileges))
        b, used = self._query("QueryServiceConfig2W", handle, 2)
        if used < C.sizeof(_FailureActions):
            fail("SDK_BOUNDS", "SCM actions header truncated")
        actions = _FailureActions.from_buffer(b)
        if actions.count > 64:
            fail("SDK_BOUNDS", "service failure action count exceeds bound")
        copied = ()
        if actions.count:
            base = C.addressof(b)
            size = C.sizeof(_Action) * actions.count
            if not actions.actions or not base <= actions.actions <= base + used - size:
                fail("SDK_POINTER", "service action array outside original buffer")
            copied = tuple((a.kind, a.delay) for a in (_Action * actions.count).from_address(actions.actions))
        result["failure_actions"] = (actions.reset, _bounded_string(b, used, actions.reboot),
                                     _bounded_string(b, used, actions.command), copied)
        status, needed = _ServiceStatus(), DWORD()
        self.owner.call("QueryServiceStatusEx",
            (handle.arg(), 0, C.byref(status), C.sizeof(status), C.byref(needed)), outputs=(status, needed))
        if needed.value != C.sizeof(status):
            fail("SDK_BOUNDS", "service status output length mismatch")
        result.update({"state": status.state, "pid": status.pid,
                       "status": tuple(getattr(status, n) for n, _ in status._fields_),
                       "raw_sd": self._raw_sd(handle, True),
                       "dependents": self.dependents(handle)})
        return result

    def dependents(self, handle):
        buffer, needed, count = C.create_string_buffer(MAX_SCM_BUFFER), DWORD(), DWORD()
        self.owner.call("EnumDependentServicesW",
            (handle.arg(), 3, buffer, len(buffer), C.byref(needed), C.byref(count)),
            outputs=(buffer, needed, count))
        # On success pcbBytesNeeded may be zero; returned structures and all
        # pointed-to names must still fit the original fixed buffer.
        if needed.value > len(buffer) or count.value > 64 or count.value * C.sizeof(_Dependent) > len(buffer):
            fail("SDK_BOUNDS", "dependent service snapshot exceeds fixed count/byte budget")
        result = []
        for n in range(count.value):
            row = _Dependent.from_buffer(buffer, n * C.sizeof(_Dependent))
            result.append((_bounded_string(buffer, len(buffer), row.name),
                           _bounded_string(buffer, len(buffer), row.display), tuple(row.status)))
        return tuple(result)

    def check_initial_service(self, image, plan):
        expected = {"service_type": 0x10, "start_type": 3, "error_control": 1,
                    "binary": plan.command_line, "group": "", "tag": 0,
                    "dependencies": (), "account": "LocalSystem", "display": plan.service_name}
        if (any(image[k] != v for k, v in expected.items()) or image["state"] != 1
                or image["pid"] != 0 or image["dependents"]):
            fail("SCM_INITIAL_READBACK", "new service base config or stopped status differs")

    def compare_service_name(self, manager, name, expected):
        fresh = self.owner.acquire("OpenServiceW", (manager.arg(), name, 0x2000D),
                                   "CloseServiceHandle", manager)
        if self.service_image(fresh) != expected:
            fail("SERVICE_DRIFT", "fresh SCM lookup differs from original retained CreateService object")
        fresh.close()

    def configure(self, handle, field, value):
        keepalive = []
        if field == "sid_type":
            level, storage = 5, _SidInfo(value)
        elif field == "privileges":
            names = C.create_unicode_buffer("\0".join(value) + "\0")
            keepalive.append(names)
            level, storage = 6, _PrivilegesInfo(C.addressof(names))
        elif field == "failure_actions":
            empty, action = C.create_unicode_buffer(""), _Action(0, 0)
            keepalive.extend((empty, action))
            # NULL means "unchanged"; a non-NULL array with count zero deletes
            # failure actions. Empty strings explicitly delete old commands.
            level, storage = 2, _FailureActions(0, C.addressof(empty), C.addressof(empty), 0, C.addressof(action))
        elif field == "failure_flag":
            level, storage = 4, BOOL(value)
        else:
            fail("SCM_FIELD", "unsupported initial service field")
        self.owner.call("ChangeServiceConfig2W", (handle.arg(), level, C.byref(storage)),
                        outputs=(storage, *keepalive))

    def lookup_service_sid(self, name):
        sid, size = C.create_string_buffer(68), DWORD(68)
        domain, domain_size, use = C.create_unicode_buffer(256), DWORD(256), DWORD()
        self.owner.call("LookupAccountNameW", (None, "NT SERVICE\\" + name, sid, C.byref(size),
                        domain, C.byref(domain_size), C.byref(use)), outputs=(sid, size, domain, domain_size, use))
        if not 8 <= size.value <= 68 or domain_size.value > 256:
            fail("SERVICE_SID", "actual service SID output exceeds original buffers")
        actual, end = _sid(sid.raw[:size.value])
        if end != size.value or not re.fullmatch(r"S-1-5-80-(\d+-){4}\d+", actual):
            fail("SERVICE_SID", "actual service account SID is not a service SID")
        return actual

    def sd(self, directory, *, service=False, service_sid=None):
        flags, mask = ("OICI" if directory and not service else ""), (0xF01FF if service else 0x1F01FF)
        sddl = f"O:SYG:BAD:P(A;{flags};0x{mask:x};;;SY)(A;{flags};0x{mask:x};;;BA)"
        if service_sid is not None:
            if not re.fullmatch(r"S-1-5-80-(\d+-){4}\d+", service_sid):
                fail("SERVICE_SID", "actual original service SID required")
            sddl += f"(A;{flags};0x1200a9;;;{service_sid})"
        slot, length = HANDLE(), DWORD()
        original_closer = self.function("LocalFree")
        call = _Call(self.owner, "ConvertStringSecurityDescriptorToSecurityDescriptorW",
                     self.function("ConvertStringSecurityDescriptorToSecurityDescriptorW"),
                     (sddl, 1, C.byref(slot), C.byref(length)), "BOOL", (slot, length, original_closer))
        self.owner.check()
        try:
            call.run()
        except BaseException:
            if slot.value is not None:
                call.state = "UNKNOWN"
            raise
        if slot.value is None or not 20 <= length.value <= MAX_SD:
            call.state = "UNKNOWN"
            fail("SD_ALLOCATION_UNKNOWN", "original SDK SD allocation/size is invalid")
        try:
            allocation = _Handle(self.owner, call, "LocalFree", original_closer, slot=slot).adopt(slot.value)
        except BaseException:
            call.state = "UNKNOWN"
            raise
        raw = C.string_at(allocation.arg(), length.value)
        check_security(raw, directory=directory, service=service, service_sid=service_sid)
        allocation.close()
        return raw

    def service_security(self, handle, raw):
        check_security(raw, service=True)
        storage = C.create_string_buffer(raw, len(raw))
        self.owner.call("SetServiceObjectSecurity", (handle.arg(), 7, storage), outputs=(storage,))

    def file_security(self, handle, raw):
        sd = decode_sd(raw)
        storage = C.create_string_buffer(raw, len(raw))
        base = C.addressof(storage)
        owner, group, dacl = sd["offsets"]
        self.owner.call("SetSecurityInfo", (handle.arg(), 1, 0x80000007,
                        HANDLE(base + owner), HANDLE(base + group), HANDLE(base + dacl), None),
                        "DWORD", outputs=(storage,))

    def _open(self, path, directory, *, write=False, create=False, raw_sd=None, share=None, delete=False):
        storage = C.create_string_buffer(raw_sd, len(raw_sd)) if raw_sd is not None else None
        sa = _SecurityAttributes(C.sizeof(_SecurityAttributes), C.addressof(storage), 0) if storage is not None else None
        access = 0xC00F0000 if write else (0x20080 if directory else 0x80020000)
        if delete:
            access |= 0x10000
        if share is None:
            share = 1 if write or not directory else 3
        flags = 0x00200000 | (0x02000000 if directory else 0x80)
        return self.owner.acquire("CreateFileW",
            (path, access, share, C.byref(sa) if sa is not None else None, 1 if create else 3, flags, None),
            "CloseHandle", keepalive=(storage, sa))

    def open_node(self, path, directory):
        return self._open(path, directory)

    def bridge_node(self, path, directory):
        return self._open(path, directory, share=7)

    def runtime_node(self, path, directory):
        # READ|DELETE sharing permits a later *new* compared DELETE pin, while
        # no WRITE sharing admits a lingering writer or a writable file mapping.
        return self._open(path, directory, share=5)

    def restore_node(self, path, directory):
        # A new SDK acquisition with DELETE but no WRITE/WRITE_DAC/WRITE_OWNER;
        # original Bootstrap comparison binds it before any deletion is allowed.
        return self._open(path, directory, delete=True, share=5)

    def absent(self, path):
        try:
            handle = self._open(path, True)
        except KnownSdkFailure as exc:
            if exc.winerror in (2, 3):
                return True
            raise
        handle.close()
        return False

    def _read_blocks(self, handle, size):
        if type(size) is not int or not 0 <= size <= MAX_FILE:
            fail("FILE_BOUNDS", "bounded actual file length required")
        self.owner.call("SetFilePointerEx", (handle.arg(), 0, None, 0))
        buffer, consumed = C.create_string_buffer(65536), 0
        while consumed < size:
            count = DWORD()
            requested = min(len(buffer), size - consumed)
            self.owner.call("ReadFile", (handle.arg(), buffer, requested, C.byref(count), None),
                            outputs=(buffer, count))
            if not 0 < count.value <= requested:
                fail("FILE_BOUNDS", "short/oversized native file read")
            consumed += count.value
            yield bytes(buffer.raw[:count.value])
        count = DWORD()
        self.owner.call("ReadFile", (handle.arg(), buffer, 1, C.byref(count), None), outputs=(buffer, count))
        if count.value != 0:
            fail("FILE_DRIFT", "actual file exceeds pinned length")

    def read(self, handle, size):
        if size > MAX_ARCHIVE:
            fail("FILE_BOUNDS", "bounded parser material read required")
        return b"".join(self._read_blocks(handle, size))

    def _file_identity(self, handle):
        full = _FileIdInfo()
        try:
            # FILE_INFO_BY_HANDLE_CLASS.FileIdInfo = 18. The original call
            # holder owns this exact output buffer before native dispatch.
            self.owner.call("GetFileInformationByHandleEx",
                            (handle.arg(), 18, C.byref(full), C.sizeof(full)), outputs=(full,))
        except KnownSdkFailure as exc:
            if exc.winerror in (1, 50, 87, 120):
                raise BootstrapError("FILE_ID_CAPABILITY",
                    f"full FileIdInfo is required; no legacy identity fallback (Win32 {exc.winerror})") from exc
            raise
        # Treat the ID as opaque bytes; this lossless encoding assigns no
        # filesystem semantics to its high/low parts.
        return full.volume, int.from_bytes(bytes(full.file_id), "little")

    def image(self, handle, path, directory):
        identity = self._file_identity(handle)
        info = _FileInfo()
        self.owner.call("GetFileInformationByHandle", (handle.arg(), C.byref(info)), outputs=(info,))
        if info.attributes & 0x400 or bool(info.attributes & 0x10) != directory or (not directory and info.links != 1):
            fail("FILE_KIND", "no reparse points, hardlinks or file/directory substitution")
        final = C.create_unicode_buffer(32768)
        # Return is a character count (zero failure), never a BOOL conversion.
        length = self.owner.call("GetFinalPathNameByHandleW", (handle.arg(), final, len(final), 0),
                                 "COUNT", outputs=(final,))
        if not 0 < length < len(final):
            fail("FILE_PATH", "actual final path exceeds buffer")
        actual = final.value
        if actual.startswith("\\\\?\\"):
            actual = actual[4:]
        if ntpath.normcase(ntpath.normpath(actual)) != ntpath.normcase(ntpath.normpath(path)):
            fail("FILE_ESCAPE", "retained HANDLE does not resolve to exact canonical path")
        size = (info.size_high << 32) | info.size_low
        hasher = hashlib.sha256()
        if not directory:
            for block in self._read_blocks(handle, size):
                hasher.update(block)
        raw_sd = self._raw_sd(handle)
        after = _FileInfo()
        self.owner.call("GetFileInformationByHandle", (handle.arg(), C.byref(after)), outputs=(after,))
        after_identity = self._file_identity(handle)
        # Access time is not part of identity; reads can legitimately update it.
        def stable(x):
            return (x.attributes, x.size_high, x.size_low,
                    x.links, x.creation.low, x.creation.high, x.write.low, x.write.high)
        if identity != after_identity or stable(info) != stable(after):
            fail("FILE_DRIFT", "file identity/size/times changed during pinned read")
        return NodeImage(identity, directory,
                         0 if directory else size, None if directory else hasher.hexdigest(),
                         raw_sd, info.attributes, info.links,
                         () if directory else ((info.creation.high << 32) | info.creation.low,
                                               (info.write.high << 32) | info.write.low))

    def fresh_image(self, path, directory):
        # Retained installation handles include WRITE and DELETE access. The
        # readonly comparison must share both in addition to READ; it does not
        # request those rights or replace/close the original created owner.
        h = self._open(path, directory, share=7)
        result = self.image(h, path, directory)
        h.close()
        return result

    def create_directory(self, path, raw_sd, parent):
        check_security(raw_sd, directory=True)
        storage = C.create_string_buffer(raw_sd, len(raw_sd))
        sa = _SecurityAttributes(C.sizeof(_SecurityAttributes), C.addressof(storage), 0)
        self.owner.call("CreateDirectoryW", (path, C.byref(sa)), outputs=(storage, sa))
        # A failed/ambiguous open after successful mkdir cannot be adopted by
        # path. apply retains the original successful call and becomes UNKNOWN.
        return self._open(path, True, write=True)

    def create_file(self, path, raw_sd, source, size):
        handle = self._open(path, False, write=True, create=True, raw_sd=raw_sd)
        for block in self._read_blocks(source, size):
            buffer = C.create_string_buffer(block, len(block))
            consumed = 0
            while consumed < len(block):
                count = DWORD()
                self.owner.call("WriteFile", (handle.arg(), HANDLE(C.addressof(buffer) + consumed),
                    len(block) - consumed, C.byref(count), None), outputs=(buffer, count))
                if not 0 < count.value <= len(block) - consumed:
                    fail("FILE_WRITE_UNKNOWN", "write count invalid after native write")
                consumed += count.value
        self.owner.call("FlushFileBuffers", (handle.arg(),))
        return handle

    def names(self, path):
        data = _FindData()
        try:
            find = self.owner.acquire("FindFirstFileW", (path + "\\*", C.byref(data)), "FindClose")
        except KnownSdkFailure as exc:
            if exc.winerror == 2:
                return set()
            raise
        answer = set()
        while True:
            raw = bytes(data.name)
            end = next((n for n in range(0, len(raw), 2) if raw[n:n + 2] == b"\0\0"), -1)
            if end < 0:
                fail("FIND_BOUNDS", "unterminated native child name")
            name = raw[:end].decode("utf-16-le")
            if name not in {".", ".."}:
                _part(name)
                if data.attributes & 0x400 or name.casefold() in {x.casefold() for x in answer}:
                    fail("FIND_REPARSE", "unexpected reparse/duplicate child preserved")
                answer.add(name)
                if len(answer) > MAX_FILES * 20:
                    fail("FIND_BOUNDS", "directory child count exceeds finite bound")
            try:
                self.owner.call("FindNextFileW", (find.arg(), C.byref(data)), outputs=(data,))
            except KnownSdkFailure as exc:
                if exc.winerror == 18:
                    break
                raise
        find.close()
        return answer

    def delete_service(self, handle):
        self.owner.call("DeleteService", (handle.arg(),))

    def delete_node(self, handle):
        deletion = _Disposition(1)  # SDK BOOLEAN, not Win32 BOOL
        self.owner.call("SetFileInformationByHandle",
                        (handle.arg(), 4, C.byref(deletion), C.sizeof(deletion)), outputs=(deletion,))

    def _token_buffer(self, token, level):
        # TOKEN_ELEVATION is exactly one DWORD; other token classes are variable-sized.
        size = C.sizeof(DWORD) if level == 20 else 65536
        buffer, needed = C.create_string_buffer(size), DWORD()
        self.owner.call("GetTokenInformation", (token.arg(), level, buffer, len(buffer), C.byref(needed)),
                        outputs=(buffer, needed))
        if not 4 <= needed.value <= len(buffer):
            fail("TOKEN_BOUNDS", "native token output exceeds bound")
        return buffer, needed.value

    def context(self):
        self.owner.check()  # re-read the actual token; never approve from a cached context
        slot = HANDLE()
        original_closer = self.function("CloseHandle")
        call = _Call(self.owner, "OpenProcessToken", self.function("OpenProcessToken"),
                     (HANDLE(-1), 8, C.byref(slot)), "BOOL", (slot, original_closer))
        self.owner.check()
        try:
            call.run()
        except BaseException:
            if slot.value is not None:
                call.state = "UNKNOWN"
            raise
        if slot.value is None:
            call.state = "UNKNOWN"
            fail("TOKEN_UNKNOWN", "SDK token result has no original handle")
        try:
            token = _Handle(self.owner, call, "CloseHandle", original_closer, slot=slot).adopt(slot.value)
        except BaseException:
            call.state = "UNKNOWN"
            raise
        user, used = self._token_buffer(token, 1)
        ptr = HANDLE.from_buffer(user).value
        if not ptr or not C.addressof(user) <= ptr <= C.addressof(user) + used - 8:
            fail("TOKEN_BOUNDS", "TOKEN_USER SID pointer outside original buffer")
        text, _ = _sid(C.string_at(ptr, used - (ptr - C.addressof(user))))
        elevation, _ = self._token_buffer(token, 20)
        elevated = DWORD.from_buffer(elevation).value
        admin_sid, member = C.create_string_buffer(_sid_bytes(BA)), BOOL()
        self.owner.call("CheckTokenMembership", (None, admin_sid, C.byref(member)), outputs=(admin_sid, member))
        privileges, used = self._token_buffer(token, 3)
        count = DWORD.from_buffer(privileges).value
        if count > 128 or 4 + count * 12 > used:
            fail("TOKEN_BOUNDS", "TOKEN_PRIVILEGES count exceeds original buffer")
        luid = _Luid()
        self.owner.call("LookupPrivilegeValueW", (None, "SeRestorePrivilege", C.byref(luid)), outputs=(luid,))
        enabled = False
        for n in range(count):
            low, high, flags = struct.unpack_from("<IiI", privileges.raw, 4 + n * 12)
            if (low, high) == (luid.low, luid.high) and flags & 2:
                enabled = True
        result = {"elevated_admin": text == SY or bool(elevated and member.value),
                  "can_assign_SYSTEM_owner": text == SY or enabled,
                  "token_user_sid": text, "enables_privileges": False}
        token.close()
        return dict(result)
