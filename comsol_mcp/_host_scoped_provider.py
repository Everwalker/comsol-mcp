"""Host-only scoped grants and registered local publication sinks.

The configuration is an administrator-managed capability file, never an MCP
request payload. It stores only SHA-256 digests for authorization references
and canonical scopes. There is deliberately no public grant/destination
registration operation.
"""
from __future__ import annotations

from datetime import datetime, timezone
import ctypes
import errno
import hashlib
import hmac
import json
import os
from pathlib import Path
import platform
import re
import stat
import sys
import threading
from typing import Any, Mapping
from uuid import uuid4

from ._execution_contract import ExecutionContractError

CONFIG_SCHEMA = "comsol-mcp.host-scoped-provider-config/1"
MAX_CONFIG_BYTES = 1024 * 1024
MAX_DESTINATIONS = 64
MAX_GRANTS = 1024
WINDOWS_PRIVATE_PATH_SECURITY_SEMANTICS = "windows-read-control-owner-dacl-persistent-acls"
_CONTROL_PRIVACY_ACCESS = object()
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_IDENTIFIER = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
_QUALIFIED_CLASS = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z_][A-Za-z0-9_]*)+$")
_TARGET_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,255}$")
_GRANT_ACTIONS = frozenset({"model.close", "artifact.publish", "session.stop",
                            "host.change.apply", "host.change.restore"})
_MODEL_REF_KEYS = frozenset({"schema_version", "session_id", "server_instance_id", "model_tag", "generation"})
MANAGED_STOP_SCOPE_SCHEMA = "comsol-mcp-managed-stop-scope/1"

# macOS SDK sys/mount.h defines MNT_IGNORE_OWNERSHIP as 0x00200000.  The
# Darwin statfs ABI is checked below before interpreting f_flags; statvfs
# intentionally does not expose this ownership-policy bit.
_DARWIN_MNT_IGNORE_OWNERSHIP = 0x00200000
# Verified in the installed macOS SDK sys/acl.h.  ``acl_get_fd_np`` returns
# ENOENT for an existing descriptor with no ACL_TYPE_EXTENDED ACL; every other
# lookup error fails closed.  We never call an ACL mutation API here.
_DARWIN_ACL_TYPE_EXTENDED = 0x00000100


class _DarwinFsid(ctypes.Structure):
    _fields_ = [("val", ctypes.c_int32 * 2)]


class _DarwinStatfs(ctypes.Structure):
    _fields_ = [
        ("f_bsize", ctypes.c_uint32), ("f_iosize", ctypes.c_int32),
        ("f_blocks", ctypes.c_uint64), ("f_bfree", ctypes.c_uint64),
        ("f_bavail", ctypes.c_uint64), ("f_files", ctypes.c_uint64),
        ("f_ffree", ctypes.c_uint64), ("f_fsid", _DarwinFsid),
        ("f_owner", ctypes.c_uint32), ("f_type", ctypes.c_uint32),
        ("f_flags", ctypes.c_uint32), ("f_fssubtype", ctypes.c_uint32),
        ("f_fstypename", ctypes.c_char * 16),
        ("f_mntonname", ctypes.c_char * 1024),
        ("f_mntfromname", ctypes.c_char * 1024),
        ("f_flags_ext", ctypes.c_uint32),
        ("f_reserved", ctypes.c_uint32 * 7),
    ]


def _darwin_fstatfs_symbol(machine: str) -> str:
    """Choose the SDK's 64-bit-inode fstatfs symbol for a Darwin target."""
    normalized = machine.lower()
    if normalized in {"arm64", "aarch64"}:
        return "fstatfs"
    if normalized == "x86_64":
        return "fstatfs$INODE64"
    raise ExecutionContractError(
        "HOST_PROVIDER_UNSUPPORTED",
        f"Darwin fstatfs ABI is not verified for architecture {machine!r}",
    )


def _darwin_mount_flags(fd: int, *, _machine: str | None = None) -> tuple[int, str]:
    """Return mount flags from an open fd, rejecting an unknown Darwin ABI."""
    if ctypes.sizeof(_DarwinStatfs) != 2168 or _DarwinStatfs.f_flags.offset != 64:
        raise ExecutionContractError("HOST_PROVIDER_CONFIG_INVALID", "Darwin statfs ABI is not the verified SDK layout")
    result = _DarwinStatfs()
    machine = _machine if _machine is not None else platform.machine()
    symbol = _darwin_fstatfs_symbol(machine)
    libc = ctypes.CDLL(None, use_errno=True)
    try:
        fstatfs = getattr(libc, symbol)
    except AttributeError as exc:
        raise ExecutionContractError(
            "HOST_PROVIDER_UNSUPPORTED",
            f"Darwin libc does not export the required {symbol} ABI for {machine!r}",
        ) from exc
    fstatfs.argtypes = [ctypes.c_int, ctypes.POINTER(_DarwinStatfs)]
    fstatfs.restype = ctypes.c_int
    if fstatfs(fd, ctypes.byref(result)) != 0:
        raise OSError(ctypes.get_errno(), "fstatfs failed")
    try:
        filesystem = bytes(result.f_fstypename).split(b"\0", 1)[0].decode("ascii")
    except UnicodeDecodeError as exc:
        raise ExecutionContractError("HOST_PROVIDER_CONFIG_INVALID", "filesystem type could not be verified") from exc
    return int(result.f_flags), filesystem


def _darwin_require_no_extended_acl(fd: int, field: str) -> None:
    """Reject Darwin extended ACLs on the already-pinned object descriptor."""
    libc = ctypes.CDLL(None, use_errno=True)
    get_acl = libc.acl_get_fd_np
    get_acl.argtypes = [ctypes.c_int, ctypes.c_int]
    get_acl.restype = ctypes.c_void_p
    get_entry = libc.acl_get_entry
    get_entry.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.POINTER(ctypes.c_void_p)]
    get_entry.restype = ctypes.c_int
    free_acl = libc.acl_free
    free_acl.argtypes = [ctypes.c_void_p]
    free_acl.restype = ctypes.c_int

    ctypes.set_errno(0)
    acl = get_acl(fd, _DARWIN_ACL_TYPE_EXTENDED)
    if not acl:
        error = ctypes.get_errno()
        # The local Darwin runtime probe returned ENOENT for ACL_TYPE_EXTENDED
        # on an existing pinned object with no extended ACL. The SDK man page
        # specifies NULL plus errno on failure, but does not define ENOENT as
        # the no-ACL sentinel; accepting that exact result is therefore limited
        # to this observed fd-based behavior. Other errors remain unknown.
        if error == errno.ENOENT:
            return
        raise ExecutionContractError(
            "HOST_PROVIDER_UNSUPPORTED",
            f"{field} extended ACL state could not be verified (errno={error})",
        )
    try:
        entry = ctypes.c_void_p()
        ctypes.set_errno(0)
        result = int(get_entry(acl, 0, ctypes.byref(entry)))  # ACL_FIRST_ENTRY
        if result == 0 and entry.value:
            raise ExecutionContractError(
                "HOST_PROVIDER_CONFIG_INVALID",
                f"{field} has an extended ACL; only owner-and-mode access is accepted",
            )
        # acl_get_entry is specified to return 0 for an entry and -1 on error.
        # An empty/malformed ACL or any unexpected result cannot be interpreted
        # as a verified no-ACL state.
        error = ctypes.get_errno()
        raise ExecutionContractError(
            "HOST_PROVIDER_UNSUPPORTED",
            f"{field} extended ACL entries could not be verified (result={result}, errno={error})",
        )
    finally:
        free_acl(acl)


def _canonical(value: Any) -> bytes:
    try:
        return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False,
                          allow_nan=False).encode("utf-8")
    except (TypeError, ValueError, UnicodeEncodeError) as exc:
        raise ExecutionContractError("HOST_PROVIDER_CONFIG_INVALID", "host provider data must be finite canonical JSON") from exc


def scope_sha256(project_id: str, action: str, scope: Mapping[str, Any]) -> str:
    """Compute the documented exact-scope digest for host-admin config authoring."""
    normalized = _normalize_scope(project_id, action, scope)
    return hashlib.sha256(_canonical({"project_id": project_id, "action": action, "scope": normalized})).hexdigest()


def destination_registration_sha256(project_id: str, destination_ref: str,
                                   sink_root: str | os.PathLike[str]) -> str:
    """Compute a destination digest after checking its current private directory identity."""
    path, identity = _private_directory(sink_root, "publish sink")
    descriptor = {"schema": "comsol-mcp-local-publish-sink/1", "project_id": project_id,
                  "destination_ref": destination_ref, "sink_path": str(path),
                  "sink_identity": identity}
    return hashlib.sha256(_canonical(descriptor)).hexdigest()


def _normalize_scope(project_id: str, action: str, scope: Mapping[str, Any]) -> dict[str, Any]:
    if not isinstance(project_id, str) or not project_id or len(project_id) > 128:
        raise ExecutionContractError("AUTHORIZATION_SCOPE_INVALID", "project scope is malformed")
    if action == "model.close":
        if not isinstance(scope, Mapping) or set(scope) != {"model_ref", "expected_revision", "destination_ref"}:
            raise ExecutionContractError("AUTHORIZATION_SCOPE_INVALID", "model.close scope fields are malformed")
        ref = scope.get("model_ref")
        if (not isinstance(ref, Mapping) or set(ref) != _MODEL_REF_KEYS
                or type(ref.get("schema_version")) is not int or ref["schema_version"] != 1
                or type(ref.get("generation")) is not int or ref["generation"] < 1
                or any(not isinstance(ref.get(field), str) or not ref[field]
                       for field in ("session_id", "server_instance_id", "model_tag"))
                or type(scope.get("expected_revision")) is not int or scope["expected_revision"] < 0
                or scope.get("destination_ref") is not None):
            raise ExecutionContractError("AUTHORIZATION_SCOPE_INVALID", "model.close identity/revision scope is malformed")
        return {"model_ref": dict(ref), "expected_revision": scope["expected_revision"],
                "destination_ref": None}
    if action == "artifact.publish":
        required = {"project_id", "action", "destination_ref", "destination_registration_sha256", "artifacts"}
        if not isinstance(scope, Mapping) or set(scope) != required:
            raise ExecutionContractError("AUTHORIZATION_SCOPE_INVALID", "artifact.publish scope fields are malformed")
        if (scope.get("project_id") != project_id or scope.get("action") != action
                or not isinstance(scope.get("destination_ref"), str)
                or not _IDENTIFIER.fullmatch(scope["destination_ref"])
                or not isinstance(scope.get("destination_registration_sha256"), str)
                or not _SHA256.fullmatch(scope["destination_registration_sha256"])):
            raise ExecutionContractError("AUTHORIZATION_SCOPE_INVALID", "artifact.publish destination scope is malformed")
        artifacts = scope.get("artifacts")
        if not isinstance(artifacts, list) or not 1 <= len(artifacts) <= 128:
            raise ExecutionContractError("AUTHORIZATION_SCOPE_INVALID", "artifact.publish artifact scope is malformed")
        normalized: list[dict[str, Any]] = []
        seen: set[str] = set()
        for row in artifacts:
            if (not isinstance(row, Mapping) or set(row) != {"artifact_id", "sha256", "size_bytes"}
                    or not isinstance(row.get("artifact_id"), str) or not _SHA256.fullmatch(row["artifact_id"])
                    or not isinstance(row.get("sha256"), str) or not _SHA256.fullmatch(row["sha256"])
                    or type(row.get("size_bytes")) is not int or row["size_bytes"] <= 0
                    or row["artifact_id"] in seen):
                raise ExecutionContractError("AUTHORIZATION_SCOPE_INVALID", "artifact.publish artifact identity is malformed")
            seen.add(row["artifact_id"])
            normalized.append({"artifact_id": row["artifact_id"], "sha256": row["sha256"],
                               "size_bytes": row["size_bytes"]})
        normalized.sort(key=lambda row: row["artifact_id"])
        return {"project_id": project_id, "action": action,
                "destination_ref": scope["destination_ref"],
                "destination_registration_sha256": scope["destination_registration_sha256"],
                "artifacts": normalized}
    if action == "session.stop":
        required = {"schema", "runtime_id", "platform", "ownership_mode", "host_source"}
        runtime = scope.get("runtime_id") if isinstance(scope, Mapping) else None
        host_source = scope.get("host_source") if isinstance(scope, Mapping) else None
        if (not isinstance(scope, Mapping) or set(scope) != required
                or scope.get("schema") != MANAGED_STOP_SCOPE_SCHEMA
                or scope.get("platform") != "windows"
                or scope.get("ownership_mode") != "originalManagedLifetime"
                or not isinstance(runtime, Mapping) or set(runtime) != {"id", "class"}
                or not isinstance(runtime.get("id"), str) or not _IDENTIFIER.fullmatch(runtime["id"])
                or not isinstance(runtime.get("class"), str) or len(runtime["class"]) > 256
                or not _QUALIFIED_CLASS.fullmatch(runtime["class"])
                or not isinstance(host_source, Mapping)
                or set(host_source) != {"t037_source_sha256", "host_resource_profile_sha256", "host_id"}
                or not isinstance(host_source.get("t037_source_sha256"), str)
                or not _SHA256.fullmatch(host_source["t037_source_sha256"])
                or not isinstance(host_source.get("host_resource_profile_sha256"), str)
                or not _SHA256.fullmatch(host_source["host_resource_profile_sha256"])
                or not isinstance(host_source.get("host_id"), str)
                or not _IDENTIFIER.fullmatch(host_source["host_id"])):
            raise ExecutionContractError("AUTHORIZATION_SCOPE_INVALID", "session.stop scope is malformed or outside the Windows original-owner class")
        return {"schema": MANAGED_STOP_SCOPE_SCHEMA,
                "runtime_id": {"id": runtime["id"], "class": runtime["class"]},
                "platform": "windows", "ownership_mode": "originalManagedLifetime",
                "host_source": {
                    "t037_source_sha256": host_source["t037_source_sha256"],
                    "host_resource_profile_sha256": host_source["host_resource_profile_sha256"],
                    "host_id": host_source["host_id"],
                }}
    if action in {"host.change.apply", "host.change.restore"}:
        required = {"root", "host_id", "profile_sha256", "plan_sha256", "phase",
                    "source_plan_sha256", "source_receipt_sha256"}
        if not isinstance(scope, Mapping) or set(scope) != required:
            raise ExecutionContractError("AUTHORIZATION_SCOPE_INVALID", "host.change scope fields are malformed")
        root = scope.get("root")
        if (not isinstance(root, Mapping) or set(root) != {"path", "volume_id", "file_id", "owner_id"}
                or any(not isinstance(root.get(key), str) or not root[key] or len(root[key]) > 4096
                       or any(ord(char) < 32 for char in root[key])
                       for key in ("path", "volume_id", "file_id", "owner_id"))):
            raise ExecutionContractError("AUTHORIZATION_SCOPE_INVALID", "host.change root identity is malformed")
        if (not isinstance(scope.get("host_id"), str) or not _IDENTIFIER.fullmatch(scope["host_id"])
                or any(not isinstance(scope.get(key), str) or not _SHA256.fullmatch(scope[key])
                       for key in ("profile_sha256", "plan_sha256"))):
            raise ExecutionContractError("AUTHORIZATION_SCOPE_INVALID", "host.change host/profile/plan binding is malformed")
        phase = "APPLY" if action == "host.change.apply" else "RESTORE"
        if scope.get("phase") != phase:
            raise ExecutionContractError("AUTHORIZATION_SCOPE_INVALID", "host.change phase differs from its action")
        source_plan, source_receipt = scope.get("source_plan_sha256"), scope.get("source_receipt_sha256")
        if phase == "APPLY":
            if source_plan is not None or source_receipt is not None:
                raise ExecutionContractError("AUTHORIZATION_SCOPE_INVALID", "apply authorization cannot contain restore-source digests")
        elif (not isinstance(source_plan, str) or not _SHA256.fullmatch(source_plan)
              or not isinstance(source_receipt, str) or not _SHA256.fullmatch(source_receipt)):
            raise ExecutionContractError("AUTHORIZATION_SCOPE_INVALID", "restore authorization must bind its source plan and receipt")
        return {"root": dict(root), "host_id": scope["host_id"],
                "profile_sha256": scope["profile_sha256"], "plan_sha256": scope["plan_sha256"],
                "phase": phase, "source_plan_sha256": source_plan,
                "source_receipt_sha256": source_receipt}
    raise ExecutionContractError("AUTHORIZATION_SCOPE_INVALID", "host provider action is not grantable")


def _strict_json(raw: bytes) -> dict[str, Any]:
    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                raise ValueError("duplicate JSON key")
            result[key] = value
        return result

    def reject_constant(value):
        raise ValueError(f"non-finite JSON constant {value}")

    try:
        value = json.loads(raw.decode("utf-8"), object_pairs_hook=pairs,
                           parse_constant=reject_constant)
    except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
        raise ExecutionContractError("HOST_PROVIDER_CONFIG_INVALID", "host provider config is not strict UTF-8 JSON") from exc
    if not isinstance(value, dict):
        raise ExecutionContractError("HOST_PROVIDER_CONFIG_INVALID", "host provider config root must be an object")
    return value


def _safe_absolute(value: Any, field: str) -> Path:
    if not isinstance(value, str) or not value or len(value) > 4096:
        raise ExecutionContractError("HOST_PROVIDER_CONFIG_INVALID", f"{field} path is malformed")
    raw = Path(value).expanduser()
    if not raw.is_absolute() or ".." in raw.parts:
        raise ExecutionContractError("HOST_PROVIDER_CONFIG_INVALID", f"{field} path must be absolute and canonical")
    cursor = Path(raw.anchor)
    try:
        for part in raw.parts[1:]:
            cursor = cursor / part
            info = cursor.lstat()
            if stat.S_ISLNK(info.st_mode):
                raise ExecutionContractError("HOST_PROVIDER_CONFIG_INVALID", f"{field} path contains a symlink")
        resolved = raw.resolve(strict=True)
    except ExecutionContractError:
        raise
    except OSError as exc:
        raise ExecutionContractError("HOST_PROVIDER_CONFIG_INVALID", f"{field} path cannot be inspected") from exc
    if resolved != raw:
        raise ExecutionContractError("HOST_PROVIDER_CONFIG_INVALID", f"{field} path is not canonical")
    return raw


def _assert_posix_owner_and_mount(fd: int, info: os.stat_result, field: str) -> dict[str, Any]:
    if os.name == "nt":
        raise ExecutionContractError("HOST_PROVIDER_UNSUPPORTED", "Windows host ACL verification is unavailable")
    if not hasattr(os, "geteuid"):
        raise ExecutionContractError("HOST_PROVIDER_UNSUPPORTED", "effective daemon ownership cannot be verified on this platform")
    if info.st_uid != os.geteuid():
        raise ExecutionContractError("HOST_PROVIDER_CONFIG_INVALID", f"{field} must be owned by the daemon user")
    if stat.S_IMODE(info.st_mode) & 0o077:
        raise ExecutionContractError("HOST_PROVIDER_CONFIG_INVALID", f"{field} permissions must exclude group and other access")
    mount: dict[str, Any] = {"platform": sys.platform}
    if sys.platform == "darwin":
        _darwin_require_no_extended_acl(fd, field)
        flags, filesystem = _darwin_mount_flags(fd)
        if flags & _DARWIN_MNT_IGNORE_OWNERSHIP:
            raise ExecutionContractError("HOST_PROVIDER_CONFIG_INVALID", f"{field} resides on a volume that ignores ownership")
        if filesystem.lower() not in {"apfs", "hfs"}:
            raise ExecutionContractError("HOST_PROVIDER_UNSUPPORTED", f"Darwin filesystem {filesystem or '<unknown>'!r} has no verified ownership policy")
        mount.update({"filesystem": filesystem, "mount_flags": flags,
                      "extended_acl": "absent"})
    return mount


def verify_private_path_security(value: str | os.PathLike[str], *, directory: bool,
                                 expected_identity: Mapping[str, Any] | None = None) -> dict[str, Any]:
    """Verify one private provider path using the host OS ownership semantics.

    Callers must retain their own pinned handle when later operations depend on
    this result; this function reopens and validates the current named object.
    """
    path = _safe_absolute(os.fspath(value), "host-scoped private path")
    if os.name == "nt":
        return _verify_windows_private_path(path, directory=directory, expected_identity=expected_identity)
    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    if directory:
        flags |= getattr(os, "O_DIRECTORY", 0)
    try:
        fd = os.open(path, flags)
    except OSError as exc:
        raise ExecutionContractError("HOST_PROVIDER_CONFIG_INVALID", "private path cannot be opened without following its leaf") from exc
    try:
        opened = os.fstat(fd)
        named = path.lstat()
        mode_ok = stat.S_ISDIR(opened.st_mode) if directory else stat.S_ISREG(opened.st_mode)
        if (not mode_ok or stat.S_ISLNK(named.st_mode)
                or (opened.st_dev, opened.st_ino) != (named.st_dev, named.st_ino)):
            raise ExecutionContractError("HOST_PROVIDER_CONFIG_INVALID", "private path is not the pinned ordinary object")
        _assert_posix_owner_and_mount(fd, opened, "host-scoped private path")
        identity = {"device": int(opened.st_dev), "inode": int(opened.st_ino)}
        if expected_identity is not None and dict(expected_identity) != identity:
            raise ExecutionContractError("AUTHORIZATION_STATE_UNKNOWN", "private path identity changed")
        semantics = "posix-owner-mode"
        if sys.platform == "darwin":
            semantics += "+filesystem-ownership+no-extended-acl"
        return {"identity": identity, "security_sha256": None,
                "security_semantics": semantics}
    finally:
        os.close(fd)


def _private_directory(value: str | os.PathLike[str], field: str) -> tuple[Path, dict[str, Any]]:
    path = _safe_absolute(os.fspath(value), field)
    if os.name == "nt":
        evidence = _verify_windows_private_path(path, directory=True, expected_identity=None)
        return path, evidence["identity"]
    try:
        flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
        flags |= getattr(os, "O_DIRECTORY", 0)
        fd = os.open(path, flags)
    except OSError as exc:
        raise ExecutionContractError("HOST_PROVIDER_CONFIG_INVALID", f"{field} is unavailable") from exc
    try:
        info = os.fstat(fd)
        named = path.lstat()
        if (not stat.S_ISDIR(info.st_mode) or stat.S_ISLNK(named.st_mode)
                or (info.st_dev, info.st_ino) != (named.st_dev, named.st_ino)):
            raise ExecutionContractError("HOST_PROVIDER_CONFIG_INVALID", f"{field} must be a pinned ordinary directory")
        _assert_posix_owner_and_mount(fd, info, field)
        return path, {"device": int(info.st_dev), "inode": int(info.st_ino)}
    finally:
        os.close(fd)


def _file_fingerprint(info: os.stat_result) -> tuple[int, int, int, int, int]:
    return (int(info.st_dev), int(info.st_ino), int(info.st_size),
            int(getattr(info, "st_mtime_ns", int(info.st_mtime * 1e9))),
            int(getattr(info, "st_ctime_ns", int(info.st_ctime * 1e9))))


def _assert_opened_path_security(path: Path, fd: int, info: os.stat_result, *, directory: bool,
                                 expected_identity: Mapping[str, Any] | None = None,
                                 _control_access=None) -> dict[str, Any]:
    if _control_access is not None and _control_access is not _CONTROL_PRIVACY_ACCESS:
        raise ExecutionContractError("HOST_PROVIDER_CONFIG_INVALID", "invalid private control access")
    if os.name == "nt":
        import msvcrt
        from ._windows_host_preflight_owner import scalar
        # This fd is borrowed from its existing Python owner. No registry
        # acquisition/adoption/close or caller-declared metadata comparison.
        def observe(api, leaf, full_getter):
            get_handle = msvcrt.get_osfhandle
            fstat = os.fstat
            before = fstat(fd)
            handle = ctypes.c_void_p(get_handle(fd))
            borrowed_getter = (full_getter.borrowed
                               if _control_access is _CONTROL_PRIVACY_ACCESS else full_getter)
            pinned = full_getter(leaf, directory=directory)
            borrowed = borrowed_getter(handle, directory=directory)
            if (_control_access is _CONTROL_PRIVACY_ACCESS and not directory
                    and (pinned[5] != 1 or borrowed[5] != 1 or info.st_nlink != 1)):
                raise ExecutionContractError("HOST_PROVIDER_CONFIG_INVALID", "private control file must have one link")
            if pinned[0] != borrowed[0]:
                raise ExecutionContractError("AUTHORIZATION_STATE_UNKNOWN", "Windows stdio and pinned leaf full identities differ")
            if full_getter(leaf, directory=directory) != pinned or borrowed_getter(handle, directory=directory) != borrowed:
                raise ExecutionContractError("AUTHORIZATION_STATE_UNKNOWN", "Windows original object metadata changed during stdio observation")
            after = fstat(fd)
            fields = ("st_dev", "st_ino", "st_size", "st_mtime_ns", "st_ctime_ns")
            signature = lambda value: tuple(getattr(value, field) for field in fields)
            if (signature(info) != signature(before) or signature(before) != signature(after)
                    or get_handle(fd) != handle.value):
                raise ExecutionContractError("AUTHORIZATION_STATE_UNKNOWN", "Windows original stdio descriptor changed or was reused")
        if _control_access is _CONTROL_PRIVACY_ACCESS:
            return _verify_windows_private_path(path, directory=directory,
                                                 expected_identity=expected_identity,
                                                 _internal_stdio_observer=observe,
                                                 _control_access=_control_access,
                                                 _identity_mode=_HOST_CONTROL_FULL_IDENTITY)
        return _verify_windows_private_path(path, directory=directory,
                                             expected_identity=expected_identity,
                                             _internal_stdio_observer=observe)
    _assert_posix_owner_and_mount(fd, info, "host-scoped private path")
    identity = {"device": int(info.st_dev), "inode": int(info.st_ino)}
    if expected_identity is not None and dict(expected_identity) != identity:
        raise ExecutionContractError("AUTHORIZATION_STATE_UNKNOWN", "private path identity changed")
    semantics = "posix-owner-mode"
    if sys.platform == "darwin":
        semantics += "+filesystem-ownership+no-extended-acl"
    return {"identity": identity, "security_sha256": None,
            "security_semantics": semantics}


from ._windows_host_preflight_owner import serialized_original_preflight


@serialized_original_preflight
def _win32_descriptor_digest(api, handle, *, directory: bool, require_daemon_owner: bool,
                            daemon_sid, trusted_sids, pinned_ancestor_traversal: bool = False) -> tuple[str, str]:
    """Read and validate an object DACL through its already pinned handle."""
    C = ctypes
    from ctypes import wintypes

    advapi = C.WinDLL("advapi32", use_last_error=True)
    advapi.GetSecurityInfo.argtypes = [C.c_void_p, wintypes.DWORD, wintypes.DWORD,
                                      C.POINTER(C.c_void_p), C.POINTER(C.c_void_p),
                                      C.POINTER(C.c_void_p), C.POINTER(C.c_void_p),
                                      C.POINTER(C.c_void_p)]
    advapi.GetSecurityInfo.restype = wintypes.DWORD
    advapi.GetSecurityDescriptorDacl.argtypes = [C.c_void_p, C.POINTER(wintypes.BOOL),
                                                  C.POINTER(C.c_void_p), C.POINTER(wintypes.BOOL)]
    advapi.GetSecurityDescriptorDacl.restype = wintypes.BOOL
    advapi.GetSecurityDescriptorLength.argtypes = [C.c_void_p]
    advapi.GetSecurityDescriptorLength.restype = wintypes.DWORD
    advapi.GetAclInformation.argtypes = [C.c_void_p, C.c_void_p, wintypes.DWORD, wintypes.DWORD]
    advapi.GetAclInformation.restype = wintypes.BOOL
    advapi.GetAce.argtypes = [C.c_void_p, wintypes.DWORD, C.POINTER(C.c_void_p)]
    advapi.GetAce.restype = wintypes.BOOL
    advapi.IsValidSid.argtypes = [C.c_void_p]
    advapi.IsValidSid.restype = wintypes.BOOL
    advapi.GetLengthSid.argtypes = [C.c_void_p]
    advapi.GetLengthSid.restype = wintypes.DWORD
    advapi.EqualSid.argtypes = [C.c_void_p, C.c_void_p]
    advapi.EqualSid.restype = wintypes.BOOL
    kernel = api.k
    kernel.LocalFree.argtypes = [C.c_void_p]
    kernel.LocalFree.restype = C.c_void_p
    owner = C.c_void_p()
    dacl = C.c_void_p()
    descriptor = C.c_void_p()
    ancestor_sid = C.c_void_p()
    if pinned_ancestor_traversal and (not directory or require_daemon_owner):
        raise ExecutionContractError("HOST_PROVIDER_CONFIG_INVALID", "pinned ancestor traversal role requires a directory before the private root")
    # SE_FILE_OBJECT=1, OWNER_SECURITY_INFORMATION=1,
    # DACL_SECURITY_INFORMATION=4. READ_CONTROL on the opened handle suffices;
    # no SACL privilege or write-security call is used.
    from ._windows_host_preflight_owner import hold_original, scalar
    actual_owner = hold_original(api)
    primary = None
    try:
        status = scalar(actual_owner.allocation_call('GetSecurityInfo', advapi, descriptor,
                          handle, 1, 1 | 4, C.byref(owner), None, C.byref(dacl), None,
                          C.byref(descriptor), family='local', keepalive=(owner, dacl)), 'DWORD')
        if status != 0 or not descriptor.value:
            raise ExecutionContractError("HOST_PROVIDER_UNSUPPORTED", f"Windows security descriptor query failed ({status})")
        if not owner.value or not scalar(advapi.IsValidSid(owner), 'BOOL'):
            raise ExecutionContractError("HOST_PROVIDER_CONFIG_INVALID", "Windows owner SID is absent or invalid")
        owner_trusted = any(scalar(advapi.EqualSid(owner, sid), 'BOOL') for sid in trusted_sids)
        owner_is_daemon = bool(scalar(advapi.EqualSid(owner, daemon_sid), 'BOOL'))
        owner_is_trustedinstaller = False
        if pinned_ancestor_traversal:
            advapi.ConvertStringSidToSidW.argtypes = [wintypes.LPCWSTR, C.POINTER(C.c_void_p)]
            advapi.ConvertStringSidToSidW.restype = wintypes.BOOL
            if not scalar(actual_owner.allocation_call('ConvertStringSidToSidW', advapi, ancestor_sid,
                          "S-1-5-80-956008885-3418522649-1831038044-1853292631-2271478464",
                          C.byref(ancestor_sid), family='local'), 'BOOL') or not ancestor_sid.value:
                raise ExecutionContractError("HOST_PROVIDER_UNSUPPORTED", "fixed ancestor owner SID could not be constructed")
            owner_is_trustedinstaller = bool(scalar(advapi.EqualSid(owner, ancestor_sid), 'BOOL'))
        present = wintypes.BOOL()
        dacl_defaulted = wintypes.BOOL()
        if not scalar(advapi.GetSecurityDescriptorDacl(descriptor, C.byref(present), C.byref(dacl), C.byref(dacl_defaulted)), 'BOOL'):
            raise ExecutionContractError("HOST_PROVIDER_UNSUPPORTED", "Windows DACL could not be decoded")
        if not present.value or not dacl.value:
            raise ExecutionContractError("HOST_PROVIDER_CONFIG_INVALID", "Windows null or absent DACL is not trusted")

        class _AclSizeInformation(C.Structure):
            _fields_ = [("AceCount", wintypes.DWORD), ("AclBytesInUse", wintypes.DWORD),
                        ("AclBytesFree", wintypes.DWORD)]

        size_info = _AclSizeInformation()
        if not scalar(advapi.GetAclInformation(dacl, C.byref(size_info), C.sizeof(size_info), 2), 'BOOL'):
            raise ExecutionContractError("HOST_PROVIDER_UNSUPPORTED", "Windows DACL inventory could not be read")
        if size_info.AceCount > 4096:
            raise ExecutionContractError("HOST_PROVIDER_CONFIG_INVALID", "Windows DACL exceeds the bounded ACE count")
        ace_policy_rows = []
        for index in range(int(size_info.AceCount)):
            ace = C.c_void_p()
            if not scalar(advapi.GetAce(dacl, index, C.byref(ace)), 'BOOL') or not ace.value:
                raise ExecutionContractError("HOST_PROVIDER_UNSUPPORTED", "Windows DACL ACE could not be read")
            header = C.string_at(ace, 4)
            ace_type, ace_flags = header[0], header[1]
            ace_size = int.from_bytes(header[2:4], "little")
            # Only the two basic, unconditional ACE encodings are admitted.
            # Callback, object, compound, and unknown ACEs fail closed.
            if ace_type not in (0, 1) or ace_flags & ~0x1F or ace_size < 16:
                raise ExecutionContractError("HOST_PROVIDER_CONFIG_INVALID", "Windows DACL contains an unsupported or truncated ACE")
            acl_start = int(dacl.value)
            acl_end = acl_start + int(size_info.AclBytesInUse)
            ace_start = int(ace.value)
            if ace_start < acl_start or ace_start + ace_size > acl_end:
                raise ExecutionContractError("HOST_PROVIDER_CONFIG_INVALID", "Windows DACL ACE extends outside the ACL bounds")
            sid_address = int(ace.value) + 8
            sid = C.c_void_p(sid_address)
            if not scalar(advapi.IsValidSid(sid), 'BOOL'):
                raise ExecutionContractError("HOST_PROVIDER_CONFIG_INVALID", "Windows DACL contains an invalid ACE SID")
            sid_size = scalar(advapi.GetLengthSid(sid), 'DWORD')
            if ace_size != 8 + sid_size:
                raise ExecutionContractError("HOST_PROVIDER_CONFIG_INVALID", "Windows DACL ACE length is noncanonical")
            ace_policy_rows.append({"type": ace_type, "flags": ace_flags,
                                    "sid_trusted": any(scalar(advapi.EqualSid(sid, trusted), 'BOOL') for trusted in trusted_sids),
                                    "access_mask": int.from_bytes(C.string_at(ace, 8)[4:8], "little")})
        _validate_windows_acl_policy(owner_trusted=owner_trusted, owner_is_daemon=owner_is_daemon,
                                     require_daemon_owner=require_daemon_owner,
                                     dacl_present=bool(present.value and dacl.value),
                                     ace_rows=ace_policy_rows, pinned_ancestor_traversal=pinned_ancestor_traversal,
                                     owner_is_trustedinstaller=owner_is_trustedinstaller)
        descriptor_size = scalar(advapi.GetSecurityDescriptorLength(descriptor), 'DWORD')
        if descriptor_size <= 0 or descriptor_size > 1024 * 1024:
            raise ExecutionContractError("HOST_PROVIDER_CONFIG_INVALID", "Windows security descriptor size is outside bounds")
        digest = hashlib.sha256(C.string_at(descriptor, descriptor_size)).hexdigest()
        owner_text = "daemon" if scalar(advapi.EqualSid(owner, daemon_sid), 'BOOL') else "trusted-host-admin"
        return digest, owner_text
    except BaseException as error:
        primary = error
        actual_owner.remember(error)
        error._original_preflight_owner = actual_owner
        raise
    finally:
        if descriptor.value:
            actual_owner.release(descriptor, family='local', primary=primary)
        if ancestor_sid.value:
            actual_owner.release(ancestor_sid, family='local', primary=primary)
        if primary is None and not actual_owner.entries():
            actual_owner.reconcile()


def _validate_windows_acl_policy(*, owner_trusted: bool, owner_is_daemon: bool,
                                 require_daemon_owner: bool, dacl_present: bool,
                                 ace_rows: list[Mapping[str, Any]], pinned_ancestor_traversal: bool = False,
                                 owner_is_trustedinstaller: bool = False) -> None:
    """Pure policy gate for Windows ACL parsing; it does not emulate Win32."""
    if pinned_ancestor_traversal and require_daemon_owner:
        raise ExecutionContractError("HOST_PROVIDER_CONFIG_INVALID", "pinned ancestor traversal cannot grant private ownership")
    if not owner_trusted and not (pinned_ancestor_traversal and owner_is_trustedinstaller):
        raise ExecutionContractError("HOST_PROVIDER_CONFIG_INVALID", "Windows path component owner is not a trusted host SID")
    if require_daemon_owner and not owner_is_daemon:
        raise ExecutionContractError("HOST_PROVIDER_CONFIG_INVALID", "Windows provider file or private directory must be owned by the daemon SID")
    if not dacl_present:
        raise ExecutionContractError("HOST_PROVIDER_CONFIG_INVALID", "Windows null or absent DACL is not trusted")
    for row in ace_rows:
        ace_type, ace_flags = row.get("type"), row.get("flags")
        if type(ace_type) is not int or ace_type not in (0, 1) or type(ace_flags) is not int or ace_flags & ~0x1F:
            raise ExecutionContractError("HOST_PROVIDER_CONFIG_INVALID", "Windows DACL contains an unsupported ACE type or flags")
        if ace_type == 0 and row.get("sid_trusted") is not True:
            if not pinned_ancestor_traversal:
                raise ExecutionContractError("HOST_PROVIDER_CONFIG_INVALID", "Windows DACL grants access to an untrusted SID")
            mask = row.get("access_mask")
            if type(mask) is not int or not 0 <= mask <= 0xFFFFFFFF:
                raise ExecutionContractError("HOST_PROVIDER_CONFIG_INVALID", "Windows ancestor ACE mask is absent or malformed")
            if mask & ~0xF01F01FF:
                raise ExecutionContractError("HOST_PROVIDER_CONFIG_INVALID", "Windows ancestor ACE contains unknown or maximum-access bits")
            # INHERIT_ONLY is ineffective here. Every child is independently
            # rechecked; this never authorizes a private descendant ACL.
            if ace_flags & 0x08:
                continue
            # PINNED_ANCESTOR_TRAVERSAL_WITH_CHILD_CREATE: existing edges
            # remain pinned without SHARE_DELETE; ADD_FILE/ADD_SUBDIRECTORY
            # permits new siblings, never replacement of the opened edge.
            # FILE_GENERIC_READ=0x120089, FILE_GENERIC_EXECUTE=0x1200a0.
            # No deny ACE is used to cancel an untrusted write grant.
            mapped = mask & ~(0x80000000 | 0x20000000)
            if mask & 0x80000000: mapped |= 0x120089
            if mask & 0x20000000: mapped |= 0x1200A0
            if mapped & ~0x1200AF:
                raise ExecutionContractError("HOST_PROVIDER_CONFIG_INVALID", "Windows pinned ancestor traversal grants mutation or unknown access to an untrusted SID")


@serialized_original_preflight
def _windows_security_context(api):
    C = ctypes
    from ctypes import wintypes

    advapi = C.WinDLL("advapi32", use_last_error=True)
    kernel = api.k
    advapi.OpenProcessToken.argtypes = [C.c_void_p, wintypes.DWORD, C.POINTER(C.c_void_p)]
    advapi.OpenProcessToken.restype = wintypes.BOOL
    advapi.GetTokenInformation.argtypes = [C.c_void_p, wintypes.DWORD, C.c_void_p,
                                           wintypes.DWORD, C.POINTER(wintypes.DWORD)]
    advapi.GetTokenInformation.restype = wintypes.BOOL
    advapi.ConvertStringSidToSidW.argtypes = [wintypes.LPCWSTR, C.POINTER(C.c_void_p)]
    advapi.ConvertStringSidToSidW.restype = wintypes.BOOL
    kernel.GetCurrentProcess.restype = C.c_void_p
    kernel.CloseHandle.argtypes = [C.c_void_p]
    kernel.CloseHandle.restype = wintypes.BOOL
    kernel.LocalFree.argtypes = [C.c_void_p]
    kernel.LocalFree.restype = C.c_void_p

    from ._windows_host_preflight_owner import hold_original, scalar, finish_original
    actual_owner = hold_original(api)
    token = C.c_void_p()
    allocated = []
    try:
        if not scalar(actual_owner.allocation_call('OpenProcessToken', advapi, token,
                      kernel.GetCurrentProcess(), 0x0008, C.byref(token), family='handle'), 'BOOL') or not token.value:
            raise ExecutionContractError("HOST_PROVIDER_UNSUPPORTED", "current daemon token SID could not be opened")
        needed = wintypes.DWORD()
        scalar(actual_owner.function('GetTokenInformation', advapi)(token, 1, None, 0, C.byref(needed)), 'BOOL')  # TokenUser, size probe
        if not needed.value or needed.value > 1024 * 1024:
            raise ExecutionContractError("HOST_PROVIDER_UNSUPPORTED", "current daemon token SID size is invalid")
        buffer = C.create_string_buffer(needed.value)
        actual_owner.hold(token, buffer, needed)
        if not scalar(actual_owner.function('GetTokenInformation', advapi)(token, 1, buffer, needed.value, C.byref(needed)), 'BOOL'):
            raise ExecutionContractError("HOST_PROVIDER_UNSUPPORTED", "current daemon token SID could not be read")
        class _SidAndAttributes(C.Structure):
            _fields_ = [("Sid", C.c_void_p), ("Attributes", wintypes.DWORD)]
        if needed.value < C.sizeof(_SidAndAttributes) or needed.value > len(buffer):
            raise ExecutionContractError("HOST_PROVIDER_UNSUPPORTED", "TokenUser result exceeds its original buffer")
        daemon_sid = C.cast(buffer, C.POINTER(_SidAndAttributes)).contents.Sid
        start, end = C.addressof(buffer), C.addressof(buffer) + needed.value
        if not daemon_sid or not start <= daemon_sid <= end - 8:
            raise ExecutionContractError("HOST_PROVIDER_UNSUPPORTED", "current daemon SID is outside its original token buffer")
        sid_header = C.string_at(daemon_sid, 8)
        if sid_header[0] != 1 or sid_header[1] > 15 or daemon_sid + 8 + 4 * sid_header[1] > end:
            raise ExecutionContractError("HOST_PROVIDER_UNSUPPORTED", "current daemon SID extent is invalid")
        trusted = [C.c_void_p(daemon_sid)]
        for textual_sid in ("S-1-5-18", "S-1-5-32-544"):
            sid = C.c_void_p()
            if not scalar(actual_owner.allocation_call('ConvertStringSidToSidW', advapi, sid,
                          textual_sid, C.byref(sid), family='local'), 'BOOL') or not sid.value:
                raise ExecutionContractError("HOST_PROVIDER_UNSUPPORTED", "trusted Windows host SID could not be constructed")
            allocated.append(sid)
            trusted.append(sid)
        return C.c_void_p(daemon_sid), trusted, (token, allocated, buffer)
    except BaseException as error:
        error._original_preflight_owner = actual_owner
        finish_original(api, error)
        raise


def _windows_volume_has_persistent_acls(api, path: Path) -> tuple[bool, int]:
    C = ctypes
    from ctypes import wintypes
    kernel = api.k
    from ._windows_host_preflight_owner import hold_original, scalar
    actual_owner = hold_original(api)
    kernel.GetVolumePathNameW.argtypes = [wintypes.LPCWSTR, wintypes.LPWSTR, wintypes.DWORD]
    kernel.GetVolumePathNameW.restype = wintypes.BOOL
    kernel.GetVolumeInformationW.argtypes = [wintypes.LPCWSTR, wintypes.LPWSTR, wintypes.DWORD,
                                              C.POINTER(wintypes.DWORD), C.POINTER(wintypes.DWORD),
                                              C.POINTER(wintypes.DWORD), wintypes.LPWSTR, wintypes.DWORD]
    kernel.GetVolumeInformationW.restype = wintypes.BOOL
    volume = C.create_unicode_buffer(32768)
    if not scalar(actual_owner.function('GetVolumePathNameW', kernel)(str(path), volume, len(volume)), 'BOOL'):
        raise ExecutionContractError("HOST_PROVIDER_UNSUPPORTED", "Windows volume for provider path could not be resolved")
    filesystem = C.create_unicode_buffer(256)
    serial, maximum_component, flags = wintypes.DWORD(), wintypes.DWORD(), wintypes.DWORD()
    if not scalar(actual_owner.function('GetVolumeInformationW', kernel)(volume, None, 0, C.byref(serial), C.byref(maximum_component),
                                        C.byref(flags), filesystem, len(filesystem)), 'BOOL'):
        raise ExecutionContractError("HOST_PROVIDER_UNSUPPORTED", "Windows volume ACL capability could not be read")
    return bool(flags.value & 0x00000008), int(serial.value)  # FILE_PERSISTENT_ACLS


_HOST_CONTROL_FULL_IDENTITY = object()


def _with_control_private_path(value, *, directory, consume, expected_identity=None):
    """Internal continuation: retain original ACL/name chains through use.

    This adds no ACL setter or SDK declaration. The control route retains the
    original preflight on uncertainty rather than unwinding further native calls.
    """
    path = _safe_absolute(os.fspath(value), "private control path")
    return _verify_windows_private_path(path, directory=directory,
        expected_identity=expected_identity, _control_callback=consume,
        _control_access=_CONTROL_PRIVACY_ACCESS,
        _identity_mode=_HOST_CONTROL_FULL_IDENTITY)


def _verify_windows_host_control_root(path: str | os.PathLike[str]) -> dict[str, Any]:
    """Complete installer identity for the normal host-control root only.

    Provider/source/stdio callers retain their original legacy identity domain.
    This private selector changes identity encoding, never ACL admission.
    """
    return _verify_windows_private_path(Path(path), directory=True, expected_identity=None,
                                       _identity_mode=_HOST_CONTROL_FULL_IDENTITY)


def _verify_windows_private_path(path: Path, *, directory: bool,
                                 expected_identity: Mapping[str, Any] | None,
                                 _retained_callback=None, _original_owner_callback=None,
                                 _internal_stdio_observer=None, _identity_mode=None,
                                 _control_callback=None, _control_access=None) -> dict[str, Any]:
    if (_control_access is not None and _control_access is not _CONTROL_PRIVACY_ACCESS
            or _control_callback is not None and _control_access is not _CONTROL_PRIVACY_ACCESS):
        raise ExecutionContractError("HOST_PROVIDER_CONFIG_INVALID", "invalid private control continuation")
    if (_identity_mode is not None and (_identity_mode is not _HOST_CONTROL_FULL_IDENTITY
            or _retained_callback is not None
            or (_control_access is not _CONTROL_PRIVACY_ACCESS
                and (directory is not True or _internal_stdio_observer is not None)))):
        raise ExecutionContractError("HOST_PROVIDER_CONFIG_INVALID", "full root identity requires the private directory-only route")
    try:
        from ._windows_parameter_publish import PublishError, _API
        api = _API()
    except Exception as exc:
        raise ExecutionContractError("HOST_PROVIDER_UNSUPPORTED", "Windows handle-based ACL verifier is unavailable") from exc
    from ._windows_host_preflight_owner import hold_original, finish_original
    actual_owner = hold_original(api)
    if _control_access is _CONTROL_PRIVACY_ACCESS:
        actual_owner._control_strict = True
    if _original_owner_callback is not None:
        _original_owner_callback(actual_owner)
    if _retained_callback is not None:
        for name in ('ReadFile', 'GetFileInformationByHandle', 'CloseHandle'):
            actual_owner.function(name, api.k)
        import ctypes as _C
        actual_owner._retained_service_sid_read = True
        actual_owner._original_get_last_error = _C.get_last_error
        actual_owner._original_win_error = _C.WinError
    with actual_owner.registry._lock:
        handles = []
        primary = None
        try:
            return _verify_windows_private_path_owned(api, path, directory=directory,
                                                     expected_identity=expected_identity,
                                                     _retained_callback=_retained_callback,
                                                     _internal_stdio_observer=_internal_stdio_observer,
                                                     _identity_mode=_identity_mode,
                                                     _control_callback=_control_callback)
        except BaseException as error:
            primary = error
            actual_owner.remember(error)
            error._original_preflight_owner = actual_owner
            raise
        finally:
            finish_original(api, primary)


def _windows_original_full_getter(api, *, _retain_outputs=False):
    """Freeze the original readonly SDK getter; reuse native production ABI."""
    from ._windows_parameter_publish import _FullIdInfo, _BasicInfo, _StandardInfo
    from ._windows_host_preflight_owner import hold_original, scalar
    owner = hold_original(api)
    getter = owner.function("GetFileInformationByHandleEx", api.k)
    def query(handle, *, directory, retain):
        full, basic, standard = _FullIdInfo(), _BasicInfo(), _StandardInfo()
        if _retain_outputs:
            retain(handle, full, basic, standard)
        for kind, output in ((18, full), (0, basic), (1, standard)):
            if not scalar(getter(handle, kind, ctypes.byref(output), ctypes.sizeof(output)), "BOOL"):
                raise ExecutionContractError("HOST_PROVIDER_UNSUPPORTED", "original full identity SDK query failed")
        file_id = int.from_bytes(bytes(full.file_id), "little")
        if (basic.attributes & 0x400 or bool(basic.attributes & 0x10) != directory
                or bool(standard.directory) != directory or standard.delete_pending
                or standard.size < 0 or not file_id or standard.links < 1):
            raise ExecutionContractError("HOST_PROVIDER_CONFIG_INVALID", "full identity requires an ordinary non-pending object")
        return ((int(full.volume), file_id), int(standard.size), int(basic.created),
                int(basic.changed), int(basic.written), int(standard.links))
    def read(handle, *, directory):
        return query(handle, directory=directory, retain=owner.hold)
    def borrowed(handle, *, directory):
        return query(handle, directory=directory, retain=owner.retain_borrowed_outputs)
    read.borrowed = borrowed
    return read


def _verify_windows_private_path_owned(api, path, *, directory, expected_identity, _retained_callback=None,
                                      _internal_stdio_observer=None, _identity_mode=None,
                                      _control_callback=None):
    from ._windows_parameter_publish import PublishError
    persistent_acls, expected_volume = _windows_volume_has_persistent_acls(api, path)
    if not persistent_acls:
        raise ExecutionContractError("HOST_PROVIDER_CONFIG_INVALID", "Windows volume does not provide persistent ACLs")
    daemon_sid, trusted_sids, cleanup = _windows_security_context(api)
    token, allocated_sids, _token_buffer = cleanup
    handles = []
    try:
        handles = (api.open_security_path(path, directory=directory, read_data=True)
                   if _retained_callback is not None else api.open_security_path(path, directory=directory))
        if not handles:
            raise ExecutionContractError("HOST_PROVIDER_UNSUPPORTED", "Windows path did not produce pinned handles")
        full_identity = _identity_mode is _HOST_CONTROL_FULL_IDENTITY
        full_getter = (_windows_original_full_getter(api, _retain_outputs=full_identity)
                       if full_identity or _internal_stdio_observer is not None else None)
        original_full = []
        descriptors: list[dict[str, Any]] = []
        identities: list[dict[str, int]] = []
        for index, handle in enumerate(handles):
            is_directory = index < len(handles) - 1 or directory
            (volume, file_index), _ = api.info(handle, directory=is_directory)
            if int(volume) != expected_volume:
                raise ExecutionContractError("AUTHORIZATION_STATE_UNKNOWN", "Windows path handle is on a different volume than the ACL capability probe")
            digest, owner = _win32_descriptor_digest(api, handle, directory=is_directory,
                                                      require_daemon_owner=index == len(handles) - 1,
                                                      daemon_sid=daemon_sid, trusted_sids=trusted_sids,
                                                      **({"pinned_ancestor_traversal": True} if index < len(handles) - 1 else {}))
            if full_getter is not None:
                original_full.append(full_getter(handle, directory=is_directory))
            identity_pair = original_full[-1][0] if full_identity else (int(volume), int(file_index))
            identities.append({"device": identity_pair[0], "inode": identity_pair[1]})
            descriptors.append({"identity": identities[-1], "descriptor_sha256": digest, "owner": owner})
        # Re-open by pinned component traversal while the first chain remains
        # live. This detects name substitution between startup identity checks.
        second = api.open_security_path(path, directory=directory)
        try:
            second_ids = []
            second_full = []
            for index, handle in enumerate(second):
                is_directory = index < len(second) - 1 or directory
                (volume, file_index), _ = api.info(handle, directory=is_directory)
                if int(volume) != expected_volume:
                    raise ExecutionContractError("AUTHORIZATION_STATE_UNKNOWN", "Windows path moved to a volume without verified ACL capability")
                digest, owner = _win32_descriptor_digest(api, handle, directory=is_directory,
                                                          require_daemon_owner=index == len(second) - 1,
                                                          daemon_sid=daemon_sid, trusted_sids=trusted_sids,
                                                          **({"pinned_ancestor_traversal": True} if index < len(second) - 1 else {}))
                if full_getter is not None:
                    second_full.append(full_getter(handle, directory=is_directory))
                identity_pair = second_full[-1][0] if full_identity else (int(volume), int(file_index))
                second_ids.append({"identity": {"device": identity_pair[0], "inode": identity_pair[1]},
                                   "descriptor_sha256": digest, "owner": owner})
            if second_ids != descriptors or full_getter is not None and second_full != original_full:
                raise ExecutionContractError("AUTHORIZATION_STATE_UNKNOWN", "Windows private path chain changed during ACL verification")
        finally:
            # Original-entry final cleanup below covers the complete second chain.
            pass
        identity = {**identities[-1], "security_sha256": hashlib.sha256(_canonical(descriptors)).hexdigest()}
        if expected_identity is not None and dict(expected_identity) != identity:
            raise ExecutionContractError("AUTHORIZATION_STATE_UNKNOWN", "Windows private path identity or ACL changed")
        metadata = {"identity": identity, "security_sha256": identity["security_sha256"],
                    "security_semantics": WINDOWS_PRIVATE_PATH_SECURITY_SEMANTICS,
                    "path_chain": descriptors}
        if _internal_stdio_observer is not None:
            _internal_stdio_observer(api, handles[-1], full_getter)
        if full_identity or _internal_stdio_observer is not None:
            # Original chains remain live: no metadata change or descriptor
            # substitution may occur across the borrowed-fd observer.
            for chain, before in ((handles, original_full), (second, second_full)):
                for index, handle in enumerate(chain):
                    is_directory = index < len(chain) - 1 or directory
                    digest, _ = _win32_descriptor_digest(api, handle, directory=is_directory,
                        require_daemon_owner=index == len(chain) - 1,
                        daemon_sid=daemon_sid, trusted_sids=trusted_sids,
                        **({"pinned_ancestor_traversal": True} if index < len(chain) - 1 else {}))
                    if (full_getter(handle, directory=is_directory) != before[index]
                            or digest != descriptors[index]["descriptor_sha256"]):
                        raise ExecutionContractError("AUTHORIZATION_STATE_UNKNOWN", "original private path changed across stdio observation")
        if _control_callback is not None:
            answer = _control_callback(api, handles[-1], metadata)
            # The same complete verifier repeats while original chains are
            # retained. Only directory content/mtime may change, not identity/ACL.
            after = _verify_windows_private_path_owned(api, path, directory=directory,
                                                       expected_identity=identity,
                                                       _identity_mode=_identity_mode)
            if after != metadata:
                raise ExecutionContractError("AUTHORIZATION_STATE_UNKNOWN", "private control path changed during use")
            return answer
        if _retained_callback is not None:
            if directory:
                raise ExecutionContractError("HOST_PROVIDER_CONFIG_INVALID", "retained backup read requires an ordinary file")
            try:
                answer = _retained_callback(api, handles[-1], metadata)
            except BaseException as original_read_error:
                api._readonly_preflight_owner.remember(original_read_error)
                raise
            # The original complete ACL/name verifier runs again while the read
            # handle and both first chains stay owned, before returning bytes.
            after = _verify_windows_private_path_owned(api, path, directory=False,
                                                       expected_identity=identity,
                                                       _internal_stdio_observer=_internal_stdio_observer)
            if after != metadata:
                raise ExecutionContractError("AUTHORIZATION_STATE_UNKNOWN", "backup whole private path changed during read")
            return answer
        return metadata
    except ExecutionContractError:
        raise
    except PublishError as exc:
        raise ExecutionContractError("HOST_PROVIDER_CONFIG_INVALID", "Windows provider path contains an unsupported or unsafe component") from exc
    except Exception as exc:
        raise ExecutionContractError("HOST_PROVIDER_UNSUPPORTED", "Windows handle-based ACL verification failed closed") from exc



class HostScopedProvider:
    """Pinned startup config, exact-scope grants and no-clobber local sinks."""

    def __init__(self, path: Path, fd: int, config: dict[str, Any], raw_sha256: str,
                 file_fingerprint: tuple[int, int, int, int, int], parent_identity: dict[str, Any],
                 destinations: dict[tuple[str, str], dict[str, Any]],
                 grants: list[tuple[str, str, str, str, str]],
                 config_identity: dict[str, Any]):
        self._path = path
        self._fd = fd
        self._config = config
        self.config_sha256 = raw_sha256
        self._file_fingerprint = file_fingerprint
        self._parent_identity = parent_identity
        self._config_identity = config_identity
        self._destinations = destinations
        self._grants = grants
        self._lock = threading.RLock()
        self._closed = False

    @classmethod
    def load(cls, value: str | os.PathLike[str]) -> "HostScopedProvider":
        path = _safe_absolute(os.fspath(value), "host provider config")
        try:
            parent, parent_identity = _private_directory(path.parent, "host provider config directory")
            if os.name != "nt" and hasattr(os, "geteuid") and path.lstat().st_uid != os.geteuid():
                raise ExecutionContractError("HOST_PROVIDER_CONFIG_INVALID", "host provider config must be owned by the daemon user")
            flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
            fd = os.open(path, flags)
        except ExecutionContractError:
            raise
        except OSError as exc:
            raise ExecutionContractError("HOST_PROVIDER_CONFIG_INVALID", "host provider config cannot be opened safely") from exc
        try:
            before = os.fstat(fd)
            named = path.lstat()
            _, parent_identity_before = _private_directory(path.parent, "host provider config directory")
            if (not stat.S_ISREG(before.st_mode) or stat.S_ISLNK(named.st_mode)
                    or (before.st_dev, before.st_ino) != (named.st_dev, named.st_ino)
                    or parent_identity_before != parent_identity
                    or before.st_size <= 0 or before.st_size > MAX_CONFIG_BYTES):
                raise ExecutionContractError("HOST_PROVIDER_CONFIG_INVALID", "host provider config identity, permissions, or size is unsafe")
            security_before = _assert_opened_path_security(path, fd, before, directory=False)
            raw = bytearray()
            while len(raw) <= MAX_CONFIG_BYTES:
                block = os.read(fd, min(64 * 1024, MAX_CONFIG_BYTES + 1 - len(raw)))
                if not block:
                    break
                raw.extend(block)
            after = os.fstat(fd)
            named_after = path.lstat()
            _, parent_identity_after = _private_directory(path.parent, "host provider config directory")
            if (_file_fingerprint(before) != _file_fingerprint(after)
                    or (after.st_dev, after.st_ino) != (named_after.st_dev, named_after.st_ino)
                    or parent_identity_after != parent_identity
                    or len(raw) != before.st_size or len(raw) > MAX_CONFIG_BYTES):
                raise ExecutionContractError("HOST_PROVIDER_CONFIG_INVALID", "host provider config changed while pinned read was in progress")
            security_after = _assert_opened_path_security(path, fd, after, directory=False,
                                                          expected_identity=security_before["identity"])
            if security_after["security_sha256"] != security_before["security_sha256"]:
                raise ExecutionContractError("HOST_PROVIDER_CONFIG_INVALID", "host provider config ACL changed while being read")
            config = _strict_json(bytes(raw))
            destinations, grants = cls._validate_config(config)
            return cls(path, fd, config, hashlib.sha256(raw).hexdigest(),
                       _file_fingerprint(after), parent_identity, destinations, grants,
                       security_before["identity"])
        except BaseException:
            os.close(fd)
            raise

    @staticmethod
    def _validate_config(config: dict[str, Any]):
        if set(config) != {"schema", "destinations", "grants"} or config.get("schema") != CONFIG_SCHEMA:
            raise ExecutionContractError("HOST_PROVIDER_CONFIG_INVALID", "host provider config schema or fields are invalid")
        raw_destinations = config.get("destinations")
        raw_grants = config.get("grants")
        if (not isinstance(raw_destinations, list) or len(raw_destinations) > MAX_DESTINATIONS
                or not isinstance(raw_grants, list) or len(raw_grants) > MAX_GRANTS):
            raise ExecutionContractError("HOST_PROVIDER_CONFIG_INVALID", "host provider destination/grant inventory exceeds its bound")
        destinations: dict[tuple[str, str], dict[str, Any]] = {}
        for entry in raw_destinations:
            if not isinstance(entry, dict) or set(entry) != {"project_id", "destination_ref", "sink_root"}:
                raise ExecutionContractError("HOST_PROVIDER_CONFIG_INVALID", "destination registration fields are invalid")
            project_id, destination_ref = entry.get("project_id"), entry.get("destination_ref")
            if (not isinstance(project_id, str) or not project_id or len(project_id) > 128
                    or not isinstance(destination_ref, str) or not _IDENTIFIER.fullmatch(destination_ref)):
                raise ExecutionContractError("HOST_PROVIDER_CONFIG_INVALID", "destination project or reference is malformed")
            key = (project_id, destination_ref)
            if key in destinations:
                raise ExecutionContractError("HOST_PROVIDER_CONFIG_INVALID", "duplicate destination registration")
            sink_root, identity = _private_directory(entry.get("sink_root"), "registered publish sink")
            registration = {"schema": "comsol-mcp-local-publish-sink/1", "project_id": project_id,
                            "destination_ref": destination_ref, "sink_path": str(sink_root),
                            "sink_identity": identity}
            registration_sha = hashlib.sha256(_canonical(registration)).hexdigest()
            destinations[key] = {"path": sink_root, "identity": identity,
                                 "registration_sha256": registration_sha}

        grants: list[tuple[str, str, str, str, str]] = []
        now = datetime.now(timezone.utc)
        for entry in raw_grants:
            required = {"project_id", "action", "authorization_ref_sha256", "scope_sha256", "expires_at_utc"}
            if not isinstance(entry, dict) or set(entry) != required:
                raise ExecutionContractError("HOST_PROVIDER_CONFIG_INVALID", "authorization grant fields are invalid")
            project_id, action = entry.get("project_id"), entry.get("action")
            ref_hash, scope_hash, expiry = (entry.get("authorization_ref_sha256"),
                                             entry.get("scope_sha256"), entry.get("expires_at_utc"))
            if (not isinstance(project_id, str) or not project_id or len(project_id) > 128
                    or not isinstance(action, str) or action not in _GRANT_ACTIONS
                    or not isinstance(ref_hash, str) or not _SHA256.fullmatch(ref_hash)
                    or not isinstance(scope_hash, str) or not _SHA256.fullmatch(scope_hash)
                    or not isinstance(expiry, str)):
                raise ExecutionContractError("HOST_PROVIDER_CONFIG_INVALID", "authorization grant identity is malformed")
            try:
                parsed_expiry = datetime.strptime(expiry, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
            except ValueError as exc:
                raise ExecutionContractError("HOST_PROVIDER_CONFIG_INVALID", "grant expiry must be canonical UTC YYYY-MM-DDTHH:MM:SSZ") from exc
            if parsed_expiry.strftime("%Y-%m-%dT%H:%M:%SZ") != expiry or (parsed_expiry - now).total_seconds() > 366 * 24 * 3600:
                raise ExecutionContractError("HOST_PROVIDER_CONFIG_INVALID", "grant expiry is noncanonical or exceeds the one-year bound")
            row = (project_id, action, ref_hash, scope_hash, expiry)
            if any(existing[:4] == row[:4] for existing in grants):
                raise ExecutionContractError("HOST_PROVIDER_CONFIG_INVALID", "duplicate authorization grant")
            grants.append(row)
        return destinations, grants

    def _assert_stable(self) -> None:
        with self._lock:
            if self._closed:
                raise ExecutionContractError("AUTHORIZATION_STATE_UNKNOWN", "host provider is closed")
            try:
                parent_info = self._path.parent.lstat()
                named = self._path.lstat()
                before = os.fstat(self._fd)
                try:
                    _, parent_identity = _private_directory(self._path.parent, "host provider config directory")
                except ExecutionContractError as exc:
                    raise ExecutionContractError("AUTHORIZATION_STATE_UNKNOWN", "host provider config directory permissions or identity changed") from exc
                if ({"device": int(parent_info.st_dev), "inode": int(parent_info.st_ino)}
                        != {key: self._parent_identity[key] for key in ("device", "inode")}
                        or parent_identity != self._parent_identity
                        or stat.S_ISLNK(named.st_mode)
                        or (named.st_dev, named.st_ino) != (before.st_dev, before.st_ino)
                        or _file_fingerprint(before) != self._file_fingerprint):
                    raise ExecutionContractError("AUTHORIZATION_STATE_UNKNOWN", "host provider configuration identity or permissions changed")
                try:
                    security = _assert_opened_path_security(self._path, self._fd, before, directory=False,
                                                            expected_identity=self._config_identity)
                except ExecutionContractError as exc:
                    raise ExecutionContractError("AUTHORIZATION_STATE_UNKNOWN", "host provider config security state changed") from exc
                if security["security_sha256"] != self._config_identity.get("security_sha256"):
                    raise ExecutionContractError("AUTHORIZATION_STATE_UNKNOWN", "host provider config ACL changed")
                digest = hashlib.sha256()
                total = 0
                os.lseek(self._fd, 0, os.SEEK_SET)
                while True:
                    block = os.read(self._fd, 64 * 1024)
                    if not block:
                        break
                    total += len(block)
                    if total > MAX_CONFIG_BYTES:
                        raise ExecutionContractError("AUTHORIZATION_STATE_UNKNOWN", "host provider configuration grew beyond its startup bound")
                    digest.update(block)
                after = os.fstat(self._fd)
                if (_file_fingerprint(after) != self._file_fingerprint or total != after.st_size
                        or digest.hexdigest() != self.config_sha256):
                    raise ExecutionContractError("AUTHORIZATION_STATE_UNKNOWN", "host provider configuration bytes changed after startup")
            except ExecutionContractError:
                raise
            except OSError as exc:
                raise ExecutionContractError("AUTHORIZATION_STATE_UNKNOWN", "pinned host provider configuration could not be revalidated") from exc

    def __call__(self, _project_id: str, _authorization_ref: str) -> bool:
        # This provider never grants the broader project.policy_set action.
        return False

    def verify_scope(self, project_id: str, action: str, scope: Mapping[str, Any], authorization_ref: str) -> bool:
        self._assert_stable()
        if action not in _GRANT_ACTIONS or not isinstance(authorization_ref, str):
            return False
        if (not authorization_ref or len(authorization_ref) > 512
                or any(ord(char) < 32 for char in authorization_ref)):
            return False
        try:
            normalized = _normalize_scope(project_id, action, scope)
        except ExecutionContractError:
            return False
        ref_hash = hashlib.sha256(authorization_ref.encode("utf-8")).hexdigest()
        scope_hash = hashlib.sha256(_canonical({"project_id": project_id, "action": action,
                                                "scope": normalized})).hexdigest()
        matching_expiry = None
        for grant_project, grant_action, grant_ref_hash, grant_scope_hash, expiry in self._grants:
            # Compare secret-derived digests in constant time even when another
            # grant dimension differs; project/action remain exact public scope.
            ref_matches = hmac.compare_digest(grant_ref_hash, ref_hash)
            scope_matches = hmac.compare_digest(grant_scope_hash, scope_hash)
            if (grant_project == project_id and grant_action == action
                    and ref_matches and scope_matches):
                matching_expiry = expiry
        if matching_expiry is None:
            return False
        deadline = datetime.strptime(matching_expiry, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
        return datetime.now(timezone.utc) < deadline

    def resolve_destination(self, project_id: str, destination_ref: str) -> dict[str, Any]:
        self._assert_stable()
        entry = self._destinations.get((project_id, destination_ref))
        if entry is None:
            raise KeyError("destination is not registered for this project")
        path, identity = _private_directory(entry["path"], "registered publish sink")
        if identity != entry["identity"]:
            raise ExecutionContractError("DESTINATION_STATE_UNKNOWN", "registered local sink directory identity changed")
        registration_sha = entry["registration_sha256"]

        def publish_chunks(destination, target_name, chunks, *, expected_sha256, expected_size, artifact_id):
            self._assert_stable()
            if (destination.get("project_id") != project_id
                    or destination.get("destination_ref") != destination_ref
                    or destination.get("registration_sha256") != registration_sha
                    or not isinstance(artifact_id, str) or not _SHA256.fullmatch(artifact_id)
                    or not isinstance(target_name, str) or not _TARGET_NAME.fullmatch(target_name)
                    or not (target_name == artifact_id or target_name.startswith(artifact_id + "."))
                    or not isinstance(expected_sha256, str) or not _SHA256.fullmatch(expected_sha256)
                    or type(expected_size) is not int or expected_size <= 0):
                raise ExecutionContractError("DESTINATION_IDENTITY_MISMATCH", "local sink publication identity or byte scope is invalid")
            current_path, current_identity = _private_directory(path, "registered publish sink")
            if current_path != path or current_identity != identity:
                raise ExecutionContractError("DESTINATION_STATE_UNKNOWN", "registered local sink changed before publication")
            from ._artifact_store import _assert_pinned_artifact_stable, _open_pinned_artifact, _publish_project_chunks_no_clobber
            _publish_project_chunks_no_clobber(path, target_name, iter(chunks),
                                               expected_sha256=expected_sha256,
                                               expected_size=expected_size)
            target = path / target_name
            readback, before = _open_pinned_artifact(target)
            try:
                digest = hashlib.sha256()
                size = 0
                while True:
                    block = readback.read(1024 * 1024)
                    if not block:
                        break
                    digest.update(block)
                    size += len(block)
                _assert_pinned_artifact_stable(target, before, os.fstat(readback.fileno()))
            finally:
                readback.close()
            if digest.hexdigest() != expected_sha256 or size != expected_size:
                raise ExecutionContractError("PUBLISH_READBACK_MISMATCH", "local sink readback differs from authorized artifact hash/size")
            return {"status": "PUBLISHED", "destination_ref": destination_ref,
                    "artifact_id": artifact_id, "sha256": expected_sha256,
                    "size_bytes": expected_size, "no_clobber": True,
                    "readback_verified": True,
                    "receipt_id": hashlib.sha256(_canonical({
                        "config_sha256": self.config_sha256, "project_id": project_id,
                        "destination_ref": destination_ref, "artifact_id": artifact_id,
                        "sha256": expected_sha256, "size_bytes": expected_size,
                        "nonce": str(uuid4()),
                    })).hexdigest()}

        return {"schema": "comsol-mcp-publish-destination/1", "project_id": project_id,
                "destination_ref": destination_ref, "registration_sha256": registration_sha,
                "publish_chunks": publish_chunks}

    def close(self) -> None:
        with self._lock:
            if not self._closed:
                self._closed = True
                os.close(self._fd)


def load_host_scoped_provider(value: str | os.PathLike[str]) -> HostScopedProvider:
    return HostScopedProvider.load(value)


def _verify_windows_owned_handle_security(api, handle, *, directory: bool,
                                         require_daemon_owner: bool = True, pinned_ancestor_traversal: bool = False) -> str:
    """Query the original owned READ_CONTROL handle without reopening its name.

    Token/SID/descriptor allocations have a separate original preflight owner;
    that owner never acquires or closes the borrowed cleanup handle.
    """
    from ._windows_parameter_publish import _API
    from ._windows_owned_resources import registry_for
    from ._windows_host_preflight_owner import hold_original, finish_original
    entry = registry_for(api).find(api, handle)
    if entry is None or entry.state != "ACQUIRED":
        raise ExecutionContractError("AUTHORIZATION_STATE_UNKNOWN", "ACL query requires an original live owned handle")
    query = _API()
    owner = hold_original(query)
    primary = None
    try:
        daemon_sid, trusted, keepalive = _windows_security_context(query)
        owner.hold(keepalive[0], api, entry, handle)
        digest, _sid = _win32_descriptor_digest(query, handle, directory=directory,
            require_daemon_owner=require_daemon_owner, daemon_sid=daemon_sid, trusted_sids=trusted,
            **({"pinned_ancestor_traversal": True} if pinned_ancestor_traversal else {}))
        if registry_for(api).find(api, handle) is not entry or entry.state != "ACQUIRED":
            raise ExecutionContractError("AUTHORIZATION_STATE_UNKNOWN", "original ACL handle ownership changed")
        return digest
    except BaseException as exc:
        primary = exc
        owner.remember(exc)
        raise
    finally:
        finish_original(query, primary)
