"""Same-process prebirth gate for the reviewed Windows primary-owner helper.

The gate is intentionally inert without a fresh exact Main approval. It checks
the complete prepared R14 source/helper closure, this gate and the one-shot
helper bytes, the current interpreter image, the signed outer/inner bindings,
and the private NTFS path proofs. Only after every check passes does it execute
the already-reviewed helper with runpy in this same Python process. It never
spawns a process, adjusts the process primary token, creates the named event,
consumes the once record, or touches the output tree itself.
"""
from __future__ import annotations

import ctypes as C
from ctypes import wintypes as W
import hashlib
import json
import ntpath
import os
from pathlib import Path
import re
import runpy
import sys
from typing import Any, Callable, Mapping


TASK_ROOT = r"C:\Users\Everwalker\AppData\Local\cvw26-current-9f07a952265c"
PYTHON_EXE = r"C:\Users\Everwalker\AppData\Local\Programs\Python\Python312-comsol-mcp\python.exe"
PYTHON_VERSION = "3.12.10"
HELPER_DIR = TASK_ROOT + r"\helpers-primary-owner-r1"
AUTHORITY_ROOT = TASK_ROOT + r"\authority-primary-owner-r1"
HELPER_PATH = HELPER_DIR + r"\windows_primary_token_owner_probe.py"
GATE_PATH = HELPER_DIR + r"\windows_primary_token_owner_outer_gate.py"
INNER_BINDING_PATH = AUTHORITY_ROOT + r"\BINDING.json"
OUTER_BINDING_PATH = AUTHORITY_ROOT + r"\PREBIRTH_BINDING.json"
APPROVAL_PATH = AUTHORITY_ROOT + r"\MAIN_APPROVAL.json"
ONCE_PATH = AUTHORITY_ROOT + r"\ONCE_USED.json"
OUTPUT_ROOT = TASK_ROOT + r"\primary-owner-probe-r1"
OUTPUT_FILE = OUTPUT_ROOT + r"\owner-probe.bin"
LEASE_EVENTS = OUTPUT_ROOT + r"\OWNER_LEASE_EVENTS.jsonl"
R14_BINDING_PATH = TASK_ROOT + r"\authority-r14\BINDING.json"
R14_SOURCE_ROOT = TASK_ROOT + r"\source-r14"
R14_HELPER_ROOT = TASK_ROOT + r"\helpers-r14"
R14_BINDING_SHA256 = "075250aa28a0ae33eec9ac414ab0f143c046df090b00613669ffdf0fbf59dabb"
EXPECTED_USER_SID = "S-1-5-21-2844656816-882443001-4055095909-1001"
OUTER_SCHEMA = "cv3-windows-primary-owner-prebirth-binding/2"
APPROVAL_SCHEMA = "cv3-Main-primary-owner-once/2"
APPROVAL_BUDGET = {
    "case_processes": 1,
    "max_active_seconds": 60,
    "COMSOL": 0,
    "GUI": 0,
    "JVM": 0,
    "Study": 0,
    "solver": 0,
}
OUTER_CANDIDATE = "Windows same-SID duplicate-primary TokenOwner probe with same-process outer gate R5-G"
SOURCE_FINGERPRINT_FIELDS = frozenset({
    "source_plan_sha256", "source_review_sha256", "helper_sha256", "helper_controls_sha256",
    "gate_sha256", "gate_controls_sha256", "inner_binding_sha256",
    "protected13_manifest_sha256", "current_resume_sha256", "r14_binding_sha256",
    "r14_source_closure_sha256", "source_path_proofs_sha256",
    "python_executable_sha256", "python_executable_path_proof_sha256",
})
OUTER_ENVELOPE_FIELDS = frozenset({
    "schema", "status", "candidate", "identity_domains", "source_fingerprints", "paths",
    "path_proofs", "r14_source_path_readback", "r14_source_path_readback_count", "limits",
    "native_execution", "closed_authority",
})
PATH_PROOF_FIELDS = frozenset({
    "task_root", "helper_directory", "authority_root", "helper_path", "gate_path",
    "inner_binding_path", "r14_binding_path", "python_executable",
    "task_root_windows_state_restore_identity",
})
APPROVAL_FIELDS = frozenset({
    "schema", "status", "binding_sha256", "outer_binding_sha256", "helper_sha256",
    "helper_controls_sha256", "gate_sha256", "gate_controls_sha256",
    "python_executable_sha256", "python_executable_path_proof_sha256",
    "protected13_manifest_sha256", "current_resume_sha256", "source_plan_sha256",
    "source_review_sha256", "r14_binding_sha256", "r14_source_closure_sha256",
    "source_path_proofs_sha256", "outer_binding_file_proof_sha256", "budget", "native_nonce",
})
OUTER_LIMITS = {
    "child_processes": 1, "max_active_seconds": 60,
    "COMSOL": 0, "GUI": 0, "JVM": 0, "Study": 0, "solver": 0,
}
OUTER_NATIVE_EXECUTION = {"CreateProcessWithTokenW": "NOT_CALLED", "child_processes": 0, "status": "NOT_RUN"}
OUTER_CLOSED_AUTHORITY = {
    "MAIN_APPROVAL": "ABSENT", "ONCE_USED": "ABSENT", "output_root": "ABSENT", "cases_root": "ABSENT",
}
ROOT_IDENTITY_DOMAINS = {
    "R14_restore_chain": "comsol_mcp._windows_state_restore._chain.full_fingerprint(device,inode)",
    "private_path": "comsol_mcp._host_scoped_provider Win32 volume-serial/file-id plus descriptor chain",
    "comparison": "compare each domain only to its own R14/prep value; never cross-compare device fields",
}
EXPECTED_PATHS = {
    "task_root": TASK_ROOT,
    "python_executable": PYTHON_EXE,
    "helper_directory": HELPER_DIR,
    "helper_path": HELPER_PATH,
    "gate_path": GATE_PATH,
    "authority_root": AUTHORITY_ROOT,
    "inner_binding_path": INNER_BINDING_PATH,
    "outer_binding_path": OUTER_BINDING_PATH,
    "approval_path": APPROVAL_PATH,
    "once_path": ONCE_PATH,
    "output_root": OUTPUT_ROOT,
    "output_file": OUTPUT_FILE,
    "lease_events_path": LEASE_EVENTS,
    "r14_binding_path": R14_BINDING_PATH,
    "r14_source_root": R14_SOURCE_ROOT,
    "r14_helper_root": R14_HELPER_ROOT,
}
FILE_ATTRIBUTE_DIRECTORY = 0x10
FILE_ATTRIBUTE_REPARSE_POINT = 0x400
INVALID_FILE_ATTRIBUTES = 0xFFFFFFFF


class GateError(RuntimeError):
    """Fail-closed prebirth gate error."""


def _fail(message: str, **details: Any) -> None:
    suffix = " " + json.dumps(details, sort_keys=True, ensure_ascii=False) if details else ""
    raise GateError(message + suffix)


def _sha256(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _canonical_json(value: Any) -> bytes:
    return (json.dumps(value, sort_keys=True, indent=2, ensure_ascii=False, allow_nan=False) + "\n").encode("utf-8")


def _strict_json(raw: bytes, *, label: str) -> dict[str, Any]:
    if not isinstance(raw, bytes) or not 1 <= len(raw) <= 8 * 1024 * 1024:
        _fail("bounded JSON input required", label=label)

    def pairs(items):
        out = {}
        for key, value in items:
            if key in out:
                raise ValueError("duplicate JSON key")
            out[key] = value
        return out

    try:
        result = json.loads(raw.decode("utf-8", "strict"), object_pairs_hook=pairs,
                            parse_constant=lambda item: (_ for _ in ()).throw(ValueError(item)))
    except (UnicodeError, ValueError, json.JSONDecodeError) as exc:
        raise GateError("strict UTF-8 JSON without duplicate keys required: " + label) from exc
    if not isinstance(result, dict):
        _fail("JSON root must be an object", label=label)
    return result


def _fd_signature(value: os.stat_result) -> tuple[int, int, int, int, int, int]:
    return (int(value.st_dev), int(value.st_ino), int(value.st_size), int(value.st_mtime_ns),
            int(value.st_ctime_ns), int(getattr(value, "st_birthtime_ns", 0)))


def _named_signature(value: os.stat_result) -> tuple[int, int, int, int, int, int]:
    # On Windows CRT path-stat and descriptor-stat expose ctime from different
    # time domains. Compare ctime only within each domain, while binding the
    # name to the opened handle through stable volume/file ID, size, mtime and
    # creation time; the qualified R14 API then verifies the native identity.
    return (int(value.st_dev), int(value.st_ino), int(value.st_size), int(value.st_mtime_ns),
            int(value.st_ctime_ns), int(getattr(value, "st_birthtime_ns", 0)))


def _shared_signature(value: os.stat_result) -> tuple[int, int, int, int, int]:
    return (int(value.st_dev), int(value.st_ino), int(value.st_size), int(value.st_mtime_ns),
            int(getattr(value, "st_birthtime_ns", 0)))


def _metadata_stable(name_before: os.stat_result, fd_before: os.stat_result,
                     fd_after: os.stat_result, name_after: os.stat_result) -> bool:
    return (_fd_signature(fd_before) == _fd_signature(fd_after)
            and _named_signature(name_before) == _named_signature(name_after)
            and _shared_signature(fd_before) == _shared_signature(name_after))


def _win32_attributes(path: str) -> int:
    if os.name != "nt":
        _fail("Windows path checks require Windows")
    kernel = C.WinDLL("kernel32", use_last_error=True)
    kernel.GetFileAttributesW.argtypes = [W.LPCWSTR]
    kernel.GetFileAttributesW.restype = W.DWORD
    return int(kernel.GetFileAttributesW(path))


def _assert_path_kind(path: str, *, directory: bool, must_exist: bool = True) -> None:
    attrs = _win32_attributes(path)
    if not must_exist:
        if attrs != INVALID_FILE_ATTRIBUTES:
            _fail("a no-overwrite destination already exists", path=path, attributes=attrs)
        return
    if attrs == INVALID_FILE_ATTRIBUTES:
        _fail("required path is absent", path=path)
    if attrs & FILE_ATTRIBUTE_REPARSE_POINT:
        _fail("reparse point is forbidden", path=path)
    if bool(attrs & FILE_ATTRIBUTE_DIRECTORY) != directory:
        _fail("path object kind differs from the binding", path=path)


def _stable_read(path: str, *, maximum: int = 256 * 1024 * 1024) -> tuple[bytes, dict[str, Any]]:
    """Read one ordinary file without following reparse names; retain FD metadata."""
    _assert_path_kind(path, directory=False)
    target = Path(path)
    if target.is_symlink():
        _fail("symlink file is forbidden", path=path)
    try:
        before_name = target.stat()
        with target.open("rb", buffering=0) as stream:
            before = os.fstat(stream.fileno())
            if before.st_size < 0 or before.st_size > maximum:
                _fail("file size exceeds the fixed read bound", path=path, size=before.st_size)
            raw = stream.read(maximum + 1)
            after = os.fstat(stream.fileno())
        after_name = target.stat()
    except OSError as exc:
        raise GateError("stable file read failed: " + path) from exc
    if (len(raw) > maximum or not _metadata_stable(before_name, before, after, after_name)
            or len(raw) != after.st_size):
        _fail("file changed during its original-handle read", path=path)
    return raw, {"bytes": len(raw), "sha256": _sha256(raw), "fd_signature": list(_fd_signature(after))}


def _r14_closure(binding: Mapping[str, Any]) -> dict[str, dict[str, dict[str, Any]]]:
    closure = {}
    for group in ("source175", "helpers"):
        records = binding.get(group)
        if not isinstance(records, dict) or not records:
            _fail("R14 source closure is missing a group", group=group)
        closure[group] = {}
        for name, row in records.items():
            if not isinstance(name, str) or not isinstance(row, dict):
                _fail("R14 source closure record is malformed", group=group)
            byte_count, digest = row.get("bytes"), row.get("sha256")
            if type(byte_count) is not int or byte_count < 0 or not isinstance(digest, str) or len(digest) != 64:
                _fail("R14 source closure digest is malformed", group=group, name=name)
            closure[group][name] = {"bytes": byte_count, "sha256": digest}
    return closure


def _r14_source_path(group: str, relative: str) -> str:
    if group not in ("source175", "helpers"):
        _fail("unsupported R14 source group", group=group)
    group_root = R14_SOURCE_ROOT if group == "source175" else TASK_ROOT
    return ntpath.join(group_root, relative.replace("/", "\\"))


def _validate_restore_chain_identity(r14_identity: Mapping[str, Any],
                                     current_identity: Mapping[str, Any],
                                     prepared_identity: Mapping[str, Any]) -> None:
    if (not isinstance(r14_identity, Mapping) or not isinstance(current_identity, Mapping)
            or not isinstance(prepared_identity, Mapping)
            or dict(current_identity) != dict(r14_identity)
            or dict(prepared_identity) != dict(r14_identity)):
        _fail("task root full identity differs from the qualified R14 restore-chain identity")


def _validate_root_acl_descriptor(r14_digest: str, private_path_proof: Mapping[str, Any]) -> None:
    chain = private_path_proof.get("path_chain")
    identity = private_path_proof.get("identity")
    if not isinstance(chain, list) or not chain or not isinstance(identity, dict):
        _fail("private-path proof does not contain a root descriptor chain")
    leaf = chain[-1]
    if (not isinstance(leaf, dict) or leaf.get("identity") != {key: identity.get(key) for key in ("device", "inode")}
            or leaf.get("descriptor_sha256") != r14_digest):
        _fail("task root leaf DACL digest differs from the R14 root-security digest")


def _validate_outer_envelope(outer: Mapping[str, Any]) -> None:
    if set(outer) != OUTER_ENVELOPE_FIELDS:
        _fail("outer binding fields differ from the exact closed schema",
              missing=sorted(OUTER_ENVELOPE_FIELDS - set(outer)),
              extra=sorted(set(outer) - OUTER_ENVELOPE_FIELDS))
    if (outer.get("schema") != OUTER_SCHEMA or outer.get("status") != "REMOTE_PREPARED_CLOSED"
            or outer.get("candidate") != OUTER_CANDIDATE):
        _fail("outer binding schema/status/candidate is not the reviewed R5-G candidate")
    if outer.get("identity_domains") != ROOT_IDENTITY_DOMAINS:
        _fail("outer binding does not declare the qualified R14 identity domains")
    if outer.get("limits") != OUTER_LIMITS:
        _fail("outer binding native budget differs from the exact R5-G limits")
    if outer.get("native_execution") != OUTER_NATIVE_EXECUTION:
        _fail("outer binding must attest that no native child has run")
    if outer.get("closed_authority") != OUTER_CLOSED_AUTHORITY:
        _fail("outer binding must be the closed PREP state without approval, once, output, or cases")
    source = outer.get("source_fingerprints")
    if not isinstance(source, dict) or set(source) != SOURCE_FINGERPRINT_FIELDS:
        missing = sorted(SOURCE_FINGERPRINT_FIELDS - set(source)) if isinstance(source, dict) else sorted(SOURCE_FINGERPRINT_FIELDS)
        extra = sorted(set(source) - SOURCE_FINGERPRINT_FIELDS) if isinstance(source, dict) else []
        _fail("outer source fingerprint fields differ from the exact R5-G projection", missing=missing, extra=extra)
    for name, value in source.items():
        if not isinstance(value, str) or re.fullmatch(r"[0-9a-f]{64}", value) is None:
            _fail("outer source fingerprint is not lowercase SHA-256", field=name)
    path_proofs = outer.get("path_proofs")
    if not isinstance(path_proofs, dict) or set(path_proofs) != PATH_PROOF_FIELDS:
        missing = sorted(PATH_PROOF_FIELDS - set(path_proofs)) if isinstance(path_proofs, dict) else sorted(PATH_PROOF_FIELDS)
        extra = sorted(set(path_proofs) - PATH_PROOF_FIELDS) if isinstance(path_proofs, dict) else []
        _fail("outer path proof fields differ from the exact R5-G projection", missing=missing, extra=extra)
    source_readback = outer.get("r14_source_path_readback")
    if (not isinstance(source_readback, dict) or type(outer.get("r14_source_path_readback_count")) is not int
            or outer["r14_source_path_readback_count"] != 179 or len(source_readback) != 179):
        _fail("outer binding must contain the full 179-member R14 source/helper readback")


def _validate_approval(outer: Mapping[str, Any], approval: Mapping[str, Any], *,
                       outer_sha256: str, inner_sha256: str, helper_sha256: str,
                       gate_sha256: str) -> None:
    if set(approval) != APPROVAL_FIELDS:
        _fail("Main authority fields differ from the exact closed approval schema",
              missing=sorted(APPROVAL_FIELDS - set(approval)),
              extra=sorted(set(approval) - APPROVAL_FIELDS))
    if approval.get("schema") != APPROVAL_SCHEMA or approval.get("status") != "APPROVED_EXECUTE_ONCE":
        _fail("Main authority schema/status is not an execute-once approval")
    expected = {
        "binding_sha256": inner_sha256,
        "outer_binding_sha256": outer_sha256,
        "helper_sha256": helper_sha256,
        "helper_controls_sha256": outer["source_fingerprints"]["helper_controls_sha256"],
        "gate_sha256": gate_sha256,
        "gate_controls_sha256": outer["source_fingerprints"]["gate_controls_sha256"],
        "python_executable_sha256": outer["source_fingerprints"]["python_executable_sha256"],
        "python_executable_path_proof_sha256": outer["source_fingerprints"]["python_executable_path_proof_sha256"],
        "protected13_manifest_sha256": outer["source_fingerprints"]["protected13_manifest_sha256"],
        "current_resume_sha256": outer["source_fingerprints"]["current_resume_sha256"],
        "source_plan_sha256": outer["source_fingerprints"]["source_plan_sha256"],
        "source_review_sha256": outer["source_fingerprints"]["source_review_sha256"],
        "r14_binding_sha256": outer["source_fingerprints"]["r14_binding_sha256"],
        "r14_source_closure_sha256": outer["source_fingerprints"]["r14_source_closure_sha256"],
        "source_path_proofs_sha256": outer["source_fingerprints"]["source_path_proofs_sha256"],
        "budget": APPROVAL_BUDGET,
    }
    for key, value in expected.items():
        if approval.get(key) != value:
            _fail("Main authority differs from the prepared exact candidate", field=key)
    nonce = approval.get("native_nonce")
    if (not isinstance(nonce, str) or len(nonce) != 32
            or any(char not in "0123456789abcdef" for char in nonce)):
        _fail("Main authority nonce must be 32 lowercase hexadecimal characters")


def _expect_proof(actual: Mapping[str, Any], expected: Any, *, label: str) -> None:
    if not isinstance(expected, dict) or dict(actual) != expected:
        _fail("live private-path proof differs from the prepared exact proof", path=label)


def _private_file_proof(provider: Any, path: str) -> dict[str, Any]:
    _assert_path_kind(path, directory=False)
    target = Path(path)
    with target.open("rb", buffering=0) as stream:
        return provider._assert_opened_path_security(
            target, stream.fileno(), os.fstat(stream.fileno()), directory=False,
        )


def _native_json_value(value: Any) -> Any:
    if isinstance(value, (tuple, list)):
        return [_native_json_value(item) for item in value]
    return value


def _normalize_native_full_fingerprint(value: Any, *, label: str) -> list[Any]:
    """Normalize only tuple/list containers; retain every native integer exactly."""
    normalized = _native_json_value(value)
    if type(normalized) is not list or len(normalized) != 6:
        _fail("runtime image full native fingerprint must have exactly six fields", label=label)
    file_identity = normalized[0]
    if type(file_identity) is not list or len(file_identity) != 2:
        _fail("runtime image full native fingerprint file identity must have two fields", label=label)

    # These ranges match _API.full_fingerprint's declared Win32 integer domains.
    # Exact type checks deliberately reject bool and float values that compare
    # equal to integers in Python; values are never masked, truncated, or skipped.
    integer_fields = (
        ("volume_64", file_identity[0], 0, (1 << 64) - 1),
        ("file_id_128", file_identity[1], 1, (1 << 128) - 1),
        ("size", normalized[1], 0, (1 << 63) - 1),
        ("creation_time", normalized[2], -(1 << 63), (1 << 63) - 1),
        ("change_time", normalized[3], -(1 << 63), (1 << 63) - 1),
        ("write_time", normalized[4], -(1 << 63), (1 << 63) - 1),
        ("link_count", normalized[5], 1, (1 << 32) - 1),
    )
    for field, item, minimum, maximum in integer_fields:
        if type(item) is not int:
            _fail("runtime image full native fingerprint field must be an exact integer",
                  label=label, field=field, actual_type=type(item).__name__)
        if item < minimum or item > maximum:
            _fail("runtime image full native fingerprint field is outside its native range",
                  label=label, field=field)
    return normalized


def _runtime_component_paths(path: str) -> list[str]:
    drive, tail = ntpath.splitdrive(path)
    if (not re.fullmatch(r"[A-Za-z]:", drive) or not tail.startswith("\\")
            or path.startswith(("\\\\", "\\\\?\\"))):
        _fail("runtime image requires an ordinary absolute drive path", path=path)
    current = drive + "\\"
    result = [current]
    for part in (item for item in tail[1:].split("\\") if item):
        current = ntpath.join(current, part)
        result.append(current)
    return result


def _runtime_owner_sid_text(api: Any, handle: Any) -> str:
    """Read the exact owner SID from the same pinned object handle.

    ACL acceptance stays in the R14 ``_win32_descriptor_digest`` policy API;
    this query only makes its already-validated owner explicit in the binding.
    The original preflight owner retains every allocated native output until
    its normal, one-pass cleanup at the end of the enclosing proof.
    """
    advapi = C.WinDLL("advapi32", use_last_error=True)
    advapi.GetSecurityInfo.argtypes = [
        C.c_void_p, W.DWORD, W.DWORD, C.POINTER(C.c_void_p), C.POINTER(C.c_void_p),
        C.POINTER(C.c_void_p), C.POINTER(C.c_void_p), C.POINTER(C.c_void_p),
    ]
    advapi.GetSecurityInfo.restype = W.DWORD
    advapi.IsValidSid.argtypes = [C.c_void_p]
    advapi.IsValidSid.restype = W.BOOL
    advapi.GetLengthSid.argtypes = [C.c_void_p]
    advapi.GetLengthSid.restype = W.DWORD
    advapi.ConvertSidToStringSidW.argtypes = [C.c_void_p, C.POINTER(C.c_void_p)]
    advapi.ConvertSidToStringSidW.restype = W.BOOL
    from comsol_mcp._windows_host_preflight_owner import hold_original, scalar

    owner_root = hold_original(api)
    owner = C.c_void_p()
    descriptor = C.c_void_p()
    status = scalar(owner_root.allocation_call(
        "GetSecurityInfo", advapi, descriptor, handle, 1, 1,
        C.byref(owner), None, None, None, C.byref(descriptor),
        family="local", keepalive=(owner,),
    ), "DWORD")
    if status != 0 or not descriptor.value or not owner.value:
        _fail("runtime image owner SID query failed", status=status)
    if not scalar(advapi.IsValidSid(owner), "BOOL"):
        _fail("runtime image owner SID is invalid")
    sid_size = scalar(advapi.GetLengthSid(owner), "DWORD")
    if sid_size < 8 or sid_size > 68:
        _fail("runtime image owner SID size is outside the Windows SID bound", size=sid_size)
    sid_text = C.c_void_p()
    if not scalar(owner_root.allocation_call(
        "ConvertSidToStringSidW", advapi, sid_text, owner, C.byref(sid_text),
        family="local", keepalive=(owner, descriptor),
    ), "BOOL") or not sid_text.value:
        _fail("runtime image owner SID could not be represented as text")
    value = C.wstring_at(sid_text.value)
    if len(value) > 184 or not value.startswith("S-1-"):
        _fail("runtime image owner SID text is malformed")
    return value


def _runtime_path_binding_api() -> tuple[Any, Callable[..., Any], Callable[..., Any]]:
    from comsol_mcp._windows_parameter_publish import _API
    from comsol_mcp._windows_host_preflight_owner import finish_original, hold_original

    api = _API()
    return api, hold_original, finish_original


def _runtime_image_proof(provider: Any, stable_native_bytes: Callable[[Path], tuple[bytes, str, dict[str, Any]]]) -> dict[str, Any]:
    """Validate and bind the runtime image with the qualified R14 path policy."""
    _assert_path_kind(PYTHON_EXE, directory=False)
    raw, digest, native = stable_native_bytes(Path(PYTHON_EXE))
    if _sha256(raw) != digest:
        _fail("runtime image original-handle bytes differ from their SHA-256")
    if not isinstance(native, Mapping):
        _fail("runtime image original-handle metadata is malformed")
    native_fingerprint = _normalize_native_full_fingerprint(
        native.get("full_native_fingerprint"), label="original-byte handle",
    )
    attributes_before = _win32_attributes(PYTHON_EXE)
    if (attributes_before == INVALID_FILE_ATTRIBUTES
            or attributes_before & (FILE_ATTRIBUTE_DIRECTORY | FILE_ATTRIBUTE_REPARSE_POINT)):
        _fail("runtime image must be an ordinary non-reparse file", attributes=attributes_before)

    api, hold_original, finish_original = _runtime_path_binding_api()
    original_owner = hold_original(api)
    primary = None
    try:
        persistent_acls, expected_volume = provider._windows_volume_has_persistent_acls(api, Path(PYTHON_EXE))
        if not persistent_acls:
            _fail("runtime image volume does not provide persistent ACLs")
        daemon_sid, trusted_sids, _security_context = provider._windows_security_context(api)
        full_getter = provider._windows_original_full_getter(api)
        component_paths = _runtime_component_paths(PYTHON_EXE)

        def inspect_chain() -> list[dict[str, Any]]:
            handles = api.open_security_path(Path(PYTHON_EXE), directory=False)
            if len(handles) != len(component_paths):
                _fail("runtime image component count differs from the pinned path", expected=len(component_paths), actual=len(handles))
            rows = []
            for index, (path, handle) in enumerate(zip(component_paths, handles, strict=True)):
                directory = index < len(handles) - 1
                try:
                    (volume, file_index), _size = api.info(handle, directory=directory)
                    if int(volume) != expected_volume:
                        _fail("runtime image component is outside the ACL-verified volume", path=path, index=index)
                    descriptor_sha, owner_class = provider._win32_descriptor_digest(
                        api, handle, directory=directory, require_daemon_owner=False,
                        daemon_sid=daemon_sid, trusted_sids=trusted_sids,
                        **({"pinned_ancestor_traversal": True} if directory else {}),
                    )
                    owner_sid = _runtime_owner_sid_text(api, handle)
                    full_fingerprint = _normalize_native_full_fingerprint(
                        full_getter(handle, directory=directory),
                        label=f"pinned path component {index}",
                    )
                    rows.append({
                        "path": path,
                        "directory": directory,
                        "identity": {"device": int(volume), "inode": int(file_index)},
                        "native_full_fingerprint": full_fingerprint,
                        "descriptor_sha256": descriptor_sha,
                        "owner_classification": owner_class,
                        "owner_sid": owner_sid,
                    })
                except BaseException as exc:
                    if isinstance(exc, GateError):
                        raise
                    raise GateError(
                        "qualified runtime image descriptor/path proof failed at "
                        + json.dumps({"path": path, "index": index,
                                      "code": getattr(exc, "code", None),
                                      "error_type": type(exc).__name__, "error": str(exc)},
                                     sort_keys=True, ensure_ascii=False)
                    ) from exc
            return rows

        first_chain = inspect_chain()
        second_chain = inspect_chain()
        if first_chain != second_chain:
            first_difference = next((index for index, pair in enumerate(zip(first_chain, second_chain))
                                     if pair[0] != pair[1]), None)
            if first_difference is None:
                first_difference = min(len(first_chain), len(second_chain))
            changed_path = (component_paths[first_difference]
                            if first_difference < len(component_paths) else PYTHON_EXE)
            _fail("runtime image native identity, owner, or descriptor changed during pinned traversal",
                  path=changed_path, index=first_difference)
        leaf = first_chain[-1]
        if leaf["native_full_fingerprint"] != native_fingerprint:
            _fail("runtime image native full identity differs from the original-byte handle", path=PYTHON_EXE)
        attributes_after = _win32_attributes(PYTHON_EXE)
        if (attributes_after != attributes_before
                or attributes_after & (FILE_ATTRIBUTE_DIRECTORY | FILE_ATTRIBUTE_REPARSE_POINT)):
            _fail("runtime image path attributes changed during proof", before=attributes_before, after=attributes_after)
        path_identity = {
            **leaf["identity"],
            "security_sha256": _sha256(_canonical_json(first_chain)),
        }
        private_path = {
            "identity": path_identity,
            "security_sha256": path_identity["security_sha256"],
            "security_semantics": provider.WINDOWS_PRIVATE_PATH_SECURITY_SEMANTICS,
            "path_chain": [
                {"identity": row["identity"], "descriptor_sha256": row["descriptor_sha256"],
                 "owner": row["owner_classification"], "owner_sid": row["owner_sid"],
                 "native_full_fingerprint": row["native_full_fingerprint"],
                 "path": row["path"], "directory": row["directory"]}
                for row in first_chain
            ],
        }
        proof = {
            "path": PYTHON_EXE,
            "bytes": len(raw),
            "sha256": digest,
            "native_fingerprint": native_fingerprint,
            "file_attributes": attributes_after,
            "reparse_free_leaf": True,
            "private_path": private_path,
        }
    except BaseException as error:
        primary = error
        original_owner.remember(error)
        raise
    finally:
        finish_original(api, primary)
    return proof


def _verify_remote_snapshot(outer: Mapping[str, Any]) -> dict[str, Any]:
    """Verify the same prepared source bytes and current NTFS path chain before dispatch."""
    if os.name != "nt":
        _fail("the prebirth gate is Windows-only")
    if ntpath.normcase(ntpath.abspath(sys.executable)) != ntpath.normcase(PYTHON_EXE):
        _fail("current interpreter path differs from the prepared candidate", actual=sys.executable)
    if sys.version.split()[0] != PYTHON_VERSION:
        _fail("current interpreter version differs from the prepared candidate", actual=sys.version)

    source = outer.get("source_fingerprints")
    paths = outer.get("paths")
    path_proofs = outer.get("path_proofs")
    if not all(isinstance(value, dict) for value in (source, paths, path_proofs)):
        _fail("outer binding source/path proof maps are malformed")
    if paths != EXPECTED_PATHS:
        _fail("outer binding paths differ from the reviewed fixed path set")

    _assert_path_kind(TASK_ROOT, directory=True)
    _assert_path_kind(HELPER_DIR, directory=True)
    _assert_path_kind(AUTHORITY_ROOT, directory=True)
    _assert_path_kind(OUTPUT_ROOT, directory=True, must_exist=False)
    _assert_path_kind(ONCE_PATH, directory=False, must_exist=False)
    _assert_path_kind(OUTPUT_FILE, directory=False, must_exist=False)
    _assert_path_kind(LEASE_EVENTS, directory=False, must_exist=False)
    cases_path = TASK_ROOT + r"\cases-primary-owner-r1"
    _assert_path_kind(cases_path, directory=True, must_exist=False)

    # Bootstrap trust in the R14 verifier only after checking its exact binding
    # and complete source/helper closure using the standard library.
    r14_raw, r14_meta = _stable_read(R14_BINDING_PATH)
    if r14_meta["sha256"] != R14_BINDING_SHA256 or source.get("r14_binding_sha256") != R14_BINDING_SHA256:
        _fail("qualified R14 binding bytes differ", actual=r14_meta["sha256"])
    r14 = _strict_json(r14_raw, label="R14 BINDING.json")
    if ntpath.normcase(r14.get("taskroot", "")) != ntpath.normcase(TASK_ROOT):
        _fail("qualified R14 binding targets a different root")
    closure = _r14_closure(r14)
    closure_sha = _sha256(_canonical_json(closure))
    if closure_sha != source.get("r14_source_closure_sha256"):
        _fail("qualified R14 source/helper closure fingerprint differs")

    stdlib_records = {}
    for group in ("source175", "helpers"):
        for relative, expected in closure[group].items():
            full_path = _r14_source_path(group, relative)
            raw, metadata = _stable_read(full_path)
            if metadata["sha256"] != expected["sha256"] or metadata["bytes"] != expected["bytes"]:
                _fail("qualified R14 source/helper bytes differ", group=group, path=full_path)
            stdlib_records[relative] = metadata

    # Import only from the verified R14 bundle, then use the already-qualified
    # production path API for every source/helper and all candidate paths.
    sys.path[:0] = [R14_SOURCE_ROOT, R14_HELPER_ROOT]
    from comsol_mcp import _host_scoped_provider as provider
    from comsol_mcp import _windows_state_restore as state_restore
    from windows_task_thread_owner import stable_native_bytes

    expected_provider = ntpath.normcase(ntpath.abspath(ntpath.join(R14_SOURCE_ROOT, "comsol_mcp", "_host_scoped_provider.py")))
    if ntpath.normcase(ntpath.abspath(provider.__file__ or "")) != expected_provider:
        _fail("qualified private-path API was imported from an unexpected source", module=provider.__file__)

    live_source_proofs = {}
    for group in ("source175", "helpers"):
        for relative, expected in closure[group].items():
            full_path = _r14_source_path(group, relative)
            raw, digest, native_metadata = stable_native_bytes(Path(full_path))
            if digest != expected["sha256"] or len(raw) != expected["bytes"]:
                _fail("native original-handle source read differs", group=group, path=full_path)
            proof = _private_file_proof(provider, full_path)
            live_source_proofs[relative] = {"content_sha256": digest, "bytes": len(raw), "private_path": proof}
    if outer["r14_source_path_readback"] != live_source_proofs:
        _fail("live R14 source/helper path proofs differ from the complete outer readback")
    source_proof_sha = _sha256(_canonical_json(live_source_proofs))
    if source_proof_sha != source.get("source_path_proofs_sha256"):
        _fail("live R14 source/helper private path proofs differ")

    def directory_proof(path: str, key: str) -> dict[str, Any]:
        _assert_path_kind(path, directory=True)
        proof = provider._verify_windows_private_path(Path(path), directory=True, expected_identity=None)
        _expect_proof(proof, path_proofs.get(key), label=path)
        return proof

    def file_proof(path: str, key: str, expected_sha: str | None = None) -> dict[str, Any]:
        raw, metadata = _stable_read(path)
        if expected_sha is not None and metadata["sha256"] != expected_sha:
            _fail("candidate file bytes differ from the prepared exact source", path=path)
        proof = _private_file_proof(provider, path)
        _expect_proof(proof, path_proofs.get(key), label=path)
        return {"sha256": metadata["sha256"], "bytes": metadata["bytes"], "private_path": proof}

    root_proof = directory_proof(TASK_ROOT, "task_root")
    with state_restore._chain(Path(TASK_ROOT)) as root_chain:
        root_chain.verify()
        root_identity = dict(root_chain.root_id)
    _validate_restore_chain_identity(
        r14.get("root_identity", {}), root_identity,
        path_proofs.get("task_root_windows_state_restore_identity", {}),
    )
    _validate_root_acl_descriptor(r14.get("root_security", ""), root_proof)
    directory_proof(HELPER_DIR, "helper_directory")
    directory_proof(AUTHORITY_ROOT, "authority_root")

    helper_record = file_proof(HELPER_PATH, "helper_path", source.get("helper_sha256"))
    gate_record = file_proof(GATE_PATH, "gate_path", source.get("gate_sha256"))
    inner_binding_record = file_proof(INNER_BINDING_PATH, "inner_binding_path", source.get("inner_binding_sha256"))
    r14_record = file_proof(R14_BINDING_PATH, "r14_binding_path", R14_BINDING_SHA256)
    outer_raw, outer_metadata = _stable_read(OUTER_BINDING_PATH)
    outer_record = {"sha256": outer_metadata["sha256"], "bytes": outer_metadata["bytes"],
                    "private_path": _private_file_proof(provider, OUTER_BINDING_PATH)}
    # The approval is created only after Main signs the prepared candidate, so
    # its exact file identity cannot be part of the earlier outer binding. Its
    # bytes and private-path proof are checked live here.
    approval_raw, approval_metadata = _stable_read(APPROVAL_PATH)
    approval_path_proof = _private_file_proof(provider, APPROVAL_PATH)
    approval_record = {"sha256": approval_metadata["sha256"], "bytes": approval_metadata["bytes"],
                       "private_path": approval_path_proof}
    if _sha256(approval_raw) != approval_metadata["sha256"]:
        _fail("approval bytes changed during the private-path read")
    python_proof = _runtime_image_proof(provider, stable_native_bytes)
    python_sha = python_proof["sha256"]
    if python_sha != source.get("python_executable_sha256"):
        _fail("Python executable bytes differ from the prepared exact image", actual=python_sha)
    python_path_proof_sha = _sha256(_canonical_json(python_proof))
    if python_path_proof_sha != source.get("python_executable_path_proof_sha256"):
        _fail("Python executable full path proof differs from the prepared exact image")
    _expect_proof(python_proof, path_proofs.get("python_executable"), label=PYTHON_EXE)
    return {
        "root_proof": root_proof,
        "r14_binding": r14_record,
        "helper": helper_record,
        "gate": gate_record,
        "inner_binding": inner_binding_record,
        "outer_binding": outer_record,
        "approval": approval_record,
        "python_executable": python_proof,
        "r14_source_helper_count": len(live_source_proofs),
        "source_path_proofs_sha256": source_proof_sha,
    }


def _invoke_reviewed_helper(*, run_path: Callable[..., Any] = runpy.run_path) -> Any:
    """Run the exact helper in this same process and preserve its exact argv."""
    previous = list(sys.argv)
    sys.argv = [HELPER_PATH, "--binding", INNER_BINDING_PATH,
                "--approval", APPROVAL_PATH, "--once", ONCE_PATH]
    try:
        return run_path(HELPER_PATH, run_name="__main__")
    finally:
        sys.argv = previous


def _dispatch_after_prebirth(*, outer: Mapping[str, Any], approval: Mapping[str, Any],
                             outer_raw: bytes, inner_raw: bytes, approval_raw: bytes,
                             helper_raw: bytes,
                             gate_raw: bytes, verify_snapshot: Callable[[], Any],
                             invoke_helper: Callable[[], Any]) -> Any:
    """Order the exact static authority checks and live proof before any dispatch."""
    inner = _strict_json(inner_raw, label="inner helper binding")
    if _sha256(helper_raw) != outer["source_fingerprints"].get("helper_sha256"):
        _fail("helper bytes differ from the outer binding")
    if _sha256(gate_raw) != outer["source_fingerprints"].get("gate_sha256"):
        _fail("gate bytes differ from the outer binding")
    inner_sha, helper_sha, gate_sha = _sha256(inner_raw), _sha256(helper_raw), _sha256(gate_raw)
    outer_sha = _sha256(outer_raw)
    _validate_outer_envelope(outer)
    if _sha256(inner_raw) != outer["source_fingerprints"]["inner_binding_sha256"]:
        _fail("inner binding SHA-256 differs from the closed outer source fingerprint")
    _validate_approval(outer, approval, outer_sha256=outer_sha, inner_sha256=inner_sha,
                       helper_sha256=helper_sha, gate_sha256=gate_sha)
    expected_inner = {
        "schema": "cv3-windows-primary-token-owner-binding/1",
        "task_root": TASK_ROOT,
        "python_executable": PYTHON_EXE,
        "python_version": PYTHON_VERSION,
        "helper_path": HELPER_PATH,
        "helper_sha256": helper_sha,
        "authority_root": AUTHORITY_ROOT,
        "binding_path": INNER_BINDING_PATH,
        "approval_path": APPROVAL_PATH,
        "once_path": ONCE_PATH,
        "output_root": OUTPUT_ROOT,
        "output_file": OUTPUT_FILE,
        "expected_user_sid": EXPECTED_USER_SID,
        "case_processes": 1,
        "max_active_seconds": 60,
        "COMSOL": 0,
        "GUI": 0,
        "JVM": 0,
        "Study": 0,
        "solver": 0,
        "no_privilege_enablement": True,
        "no_account_or_acl_mutation": True,
        "no_process_signal_or_pid_reopen": True,
    }
    if inner != expected_inner:
        _fail("inner binding is not the exact reviewed candidate dictionary")
    live = verify_snapshot()
    proof_sha = _sha256(_canonical_json(live["outer_binding"]["private_path"]))
    if approval.get("outer_binding_file_proof_sha256") != proof_sha:
        _fail("Main authority does not bind the exact prepared outer-binding file proof")
    if live["outer_binding"].get("sha256") != outer_sha:
        _fail("outer binding changed after authority parsing")
    if live["approval"].get("sha256") != _sha256(approval_raw):
        _fail("Main authority bytes changed after strict parsing")
    return invoke_helper()


def _main(argv: list[str] | None = None) -> int:
    if os.name != "nt":
        _fail("outer gate requires Windows")
    args = list(sys.argv[1:] if argv is None else argv)
    if (len(sys.argv) < 1 or ntpath.normcase(ntpath.abspath(__file__)) != ntpath.normcase(GATE_PATH)
            or ntpath.normcase(ntpath.abspath(sys.argv[0])) != ntpath.normcase(GATE_PATH)
            or args != ["--binding", INNER_BINDING_PATH, "--approval", APPROVAL_PATH, "--once", ONCE_PATH]):
        _fail("gate argv must match the exact fixed helper authority paths")
    outer_raw, _ = _stable_read(OUTER_BINDING_PATH)
    inner_raw, _ = _stable_read(INNER_BINDING_PATH)
    approval_raw, _ = _stable_read(APPROVAL_PATH)
    helper_raw, _ = _stable_read(HELPER_PATH)
    gate_raw, _ = _stable_read(GATE_PATH)
    outer = _strict_json(outer_raw, label="outer prebirth binding")
    approval = _strict_json(approval_raw, label="Main one-shot approval")
    _dispatch_after_prebirth(
        outer=outer, approval=approval, outer_raw=outer_raw,
        inner_raw=inner_raw, approval_raw=approval_raw, helper_raw=helper_raw, gate_raw=gate_raw,
        verify_snapshot=lambda: _verify_remote_snapshot(outer),
        invoke_helper=_invoke_reviewed_helper,
    )
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(_main())
    except GateError as exc:
        print("PRIMARY_OWNER_PREBIRTH_GATE_REJECTED " + str(exc), file=sys.stderr, flush=True)
        raise SystemExit(70)
