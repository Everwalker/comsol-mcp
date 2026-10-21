"""Versioned R6B repair candidate for a pinned authority-root/relative-once lease.

This module is a local source candidate only. It does not create a process,
change an account, adjust privileges, or edit ACLs. It is intentionally separate
from the frozen R4 helper and R5J gate. A future R6 gate must call it only after
the exact R5J prebirth checks pass, then retain this same R14 resource owner
through original-process-handle natural terminal observation.

The R14 SDK's ``_API._open_relative`` uses OBJECT_ATTRIBUTES.RootDirectory,
FILE_CREATE, OBJ_DONT_REPARSE, and FILE_OPEN_REPARSE_POINT. The authority-root
pin below opens one exclusive same-volume root handle by NtCreateFile relative
to a pinned parent chain. The ONCE_USED child is then created by the unchanged
R14 ``api.create_owned(root_handle, leaf)`` API, so the create is relative to
that exact retained directory handle and cannot overwrite an existing name.
"""
from __future__ import annotations

import ctypes as C
import hashlib
import json
import ntpath
import os
from pathlib import Path
from typing import Any, Mapping, Protocol


R6B_SCHEMA = "cv3-windows-primary-owner-authority-root-lease-r6b/1"
ONCE_LEAF = "ONCE_USED.json"
ONCE_STATUS = "CONSUMED_BEFORE_TOKEN_DUPLICATION"
FILE_SHARE_NONE = 0
FILE_OPEN = 1
FILE_OPENED = 1
FILE_ATTRIBUTE_NORMAL = 0x80
FILE_DIRECTORY_FILE = 0x00000001
FILE_SYNCHRONOUS_IO_NONALERT = 0x00000020
FILE_OPEN_REPARSE_POINT = 0x00200000
OBJ_CASE_INSENSITIVE = 0x00000040
OBJ_DONT_REPARSE = 0x00001000
FILE_LIST_DIRECTORY = 0x00000001
FILE_ADD_FILE = 0x00000002
FILE_READ_CONTROL = 0x00020000
FILE_DIRECTORY_ACCESS = 0x001000A0
WAIT_OBJECT_0 = 0x00000000
WAIT_TIMEOUT = 0x00000102
STILL_ACTIVE = 259

_ACTIVE_LEASES: list["AuthorityRootLeaseR6B"] = []
_UNKNOWN_LEASES: list["AuthorityRootLeaseR6B"] = []


class LeaseError(RuntimeError):
    """Fail-closed R6 authority-root lease error."""


class LeaseOps(Protocol):
    """Small effect boundary used by the state machine and software controls."""

    def pin_and_verify_prebirth(self) -> Mapping[str, Any]: ...
    def create_once_relative(self, raw: bytes, expected_sha256: str) -> Mapping[str, Any]: ...
    def finish_original_owner(self) -> None: ...
    def cleanup_before_once(self, cause: BaseException) -> None: ...


def _fail(message: str, **details: Any) -> None:
    suffix = " " + json.dumps(details, sort_keys=True, ensure_ascii=False) if details else ""
    raise LeaseError(message + suffix)


def _strict_once_json(raw: bytes) -> dict[str, Any]:
    if not isinstance(raw, bytes) or not 1 <= len(raw) <= 1_048_576:
        _fail("once record must be nonempty bytes within the fixed 1 MiB bound")

    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                raise ValueError("duplicate JSON key")
            result[key] = value
        return result

    try:
        value = json.loads(raw.decode("utf-8", "strict"), object_pairs_hook=pairs,
                           parse_constant=lambda item: (_ for _ in ()).throw(ValueError(item)))
    except (UnicodeError, ValueError, json.JSONDecodeError) as exc:
        raise LeaseError("once record must be strict duplicate-free UTF-8 JSON") from exc
    if not isinstance(value, dict) or value.get("status") != ONCE_STATUS:
        _fail("once record schema/status is not the reviewed consume-before-token contract")
    return value


def _handle_number(value: Any) -> int:
    raw = getattr(value, "value", value)
    if type(raw) is not int or raw <= 0 or raw == C.c_void_p(-1).value:
        _fail("retained original handle is malformed")
    return raw


class AuthorityRootLeaseR6B:
    """Single-use lifecycle retaining the original authority-root owner.

    ``ops`` must be the R14-backed implementation below in a future R6 gate.
    Software tests inject an in-memory implementation, never a parallel native
    implementation. This state machine never releases a live child or an
    unqualified UNKNOWN. Only exact active-deadline WAIT_TIMEOUT provenance can
    later be resolved by same-handle WAIT_OBJECT_0 and a non-STILL_ACTIVE code.
    """

    def __init__(self, ops: LeaseOps):
        self.ops = ops
        self.state = "NEW"
        self.first_cause: BaseException | None = None
        self.prebirth_receipt: Mapping[str, Any] | None = None
        self.once_receipt: Mapping[str, Any] | None = None
        self.original_process_handle: Any = None
        self.original_pid: int | None = None
        self.original_birth_filetime: int | None = None
        self.original_image_path: str | None = None
        self.unknown_terminal_release_eligible = False
        self.unknown_reason: str | None = None
        if not any(item is self for item in _ACTIVE_LEASES):
            _ACTIVE_LEASES.append(self)

    def pin_after_prebirth(self) -> Mapping[str, Any]:
        if self.state != "NEW":
            _fail("R6 root pin may run exactly once", state=self.state)
        self.state = "PIN_INTENT"
        try:
            receipt = self.ops.pin_and_verify_prebirth()
            if not isinstance(receipt, Mapping) or receipt.get("status") != "PREBIRTH_ROOT_PINNED_STRICT":
                _fail("R14 root pin did not return the exact strict prebirth receipt")
            self.prebirth_receipt = dict(receipt)
            self.state = "PINNED_PREBIRTH"
            return self.prebirth_receipt
        except BaseException as exc:
            try:
                self.ops.cleanup_before_once(exc)
            except BaseException as cleanup_error:
                self._mark_unknown(cleanup_error)
            else:
                self.state = "ABORTED_BEFORE_ONCE_NO_BIRTH"
                _remove_identity(_ACTIVE_LEASES, self)
            raise

    def consume_once_relative(self, raw: bytes, expected_sha256: str) -> Mapping[str, Any]:
        if self.state != "PINNED_PREBIRTH":
            _fail("relative once creation requires the pinned strict prebirth root", state=self.state)
        self.state = "ONCE_VALIDATION_INTENT"
        try:
            if not isinstance(expected_sha256, str) or len(expected_sha256) != 64:
                _fail("Main-bound once digest is malformed")
            if hashlib.sha256(raw).hexdigest() != expected_sha256:
                _fail("once bytes differ from their separately reviewed exact digest")
            _strict_once_json(raw)
            self.state = "ONCE_CREATE_INTENT"
            receipt = self.ops.create_once_relative(raw, expected_sha256)
            if (not isinstance(receipt, Mapping)
                    or receipt.get("status") != "ONCE_CREATED_RELATIVE_FILE_CREATE_READBACK_PASS"
                    or receipt.get("create_disposition") != "FILE_CREATE"
                    or receipt.get("relative_to_original_root_handle") is not True
                    or receipt.get("sha256") != expected_sha256
                    or receipt.get("bytes") != len(raw)):
                _fail("handle-relative exclusive ONCE receipt differs from the exact contract")
            self.once_receipt = dict(receipt)
            self.state = "ONCE_CONSUMED_ROOT_RETAINED"
            return self.once_receipt
        except BaseException as exc:
            # Once create/write may have taken effect. Preserve the original
            # root and any native output; there is no unlink, retry, or replay.
            if not bool(getattr(self.ops, "effect_started", False)):
                try:
                    self.ops.cleanup_before_once(exc)
                except BaseException as cleanup_error:
                    self._mark_unknown(cleanup_error)
                else:
                    self.state = "ABORTED_BEFORE_ONCE_NO_BIRTH"
                    _remove_identity(_ACTIVE_LEASES, self)
            else:
                self._mark_unknown(exc)
            raise

    def bind_original_birth(self, process_handle: Any, pid: int, birth_filetime: int,
                            image_path: str) -> None:
        if self.state != "ONCE_CONSUMED_ROOT_RETAINED":
            _fail("original process handle can be bound only after once consumption", state=self.state)
        try:
            _handle_number(process_handle)
            if type(pid) is not int or pid <= 0 or type(birth_filetime) is not int or birth_filetime <= 0:
                _fail("original process birth tuple is malformed")
            if not isinstance(image_path, str) or not image_path:
                _fail("original process image path is missing")
        except BaseException as exc:
            self._mark_unknown(exc)
            raise
        self.original_process_handle = process_handle
        self.original_pid = pid
        self.original_birth_filetime = birth_filetime
        self.original_image_path = image_path
        self.state = "ORIGINAL_PROCESS_LIVE_ROOT_RETAINED"

    def observe_wait_timeout(self, process_handle: Any, wait_result: int) -> None:
        if (self.state == "UNKNOWN_RETAIN_ORIGINAL_HANDLES_NO_REPLAY"
                and self.unknown_terminal_release_eligible
                and self.unknown_reason == "ACTIVE_DEADLINE_WAIT_TIMEOUT"
                and process_handle is self.original_process_handle
                and type(wait_result) is int and wait_result == WAIT_TIMEOUT):
            # Repeated passive waits preserve the exact timeout provenance.
            return
        if self.state != "ORIGINAL_PROCESS_LIVE_ROOT_RETAINED" or process_handle is not self.original_process_handle:
            self._mark_unknown(LeaseError("timeout observation did not use the original retained process handle"))
            return
        if type(wait_result) is not int or wait_result != WAIT_TIMEOUT:
            self._mark_unknown(LeaseError("wait result was not the exact bounded WAIT_TIMEOUT code"))
            return
        self._mark_unknown(
            LeaseError("active deadline reached; retain original process and authority-root handles"),
            terminal_release_eligible=True,
            reason="ACTIVE_DEADLINE_WAIT_TIMEOUT",
        )

    def finish_after_natural_terminal(self, process_handle: Any, wait_result: int,
                                      exit_code: int) -> Mapping[str, Any]:
        live_terminal_candidate = self.state == "ORIGINAL_PROCESS_LIVE_ROOT_RETAINED"
        timeout_unknown_candidate = (
            self.state == "UNKNOWN_RETAIN_ORIGINAL_HANDLES_NO_REPLAY"
            and self.unknown_terminal_release_eligible
            and self.unknown_reason == "ACTIVE_DEADLINE_WAIT_TIMEOUT"
        )
        if not live_terminal_candidate and not timeout_unknown_candidate:
            _fail("root lease cleanup requires a live process or timeout-qualified UNKNOWN", state=self.state)
        if process_handle is not self.original_process_handle:
            self._mark_unknown(LeaseError("terminal observation used a different process-handle object"))
            _fail("terminal observation did not use the original returned PROCESS_INFORMATION handle")
        if (type(wait_result) is not int or wait_result != WAIT_OBJECT_0
                or type(exit_code) is not int or not 0 <= exit_code <= 0xFFFFFFFF
                or exit_code == STILL_ACTIVE):
            self._mark_unknown(LeaseError("original process natural terminal was not confirmed"))
            _fail("root owner cannot close before exact original-handle terminal observation")
        self.state = "TERMINAL_OBSERVED_CLEANUP_INTENT"
        self.unknown_terminal_release_eligible = False
        self.unknown_reason = None
        try:
            self.ops.finish_original_owner()
        except BaseException as exc:
            self._mark_unknown(exc)
            raise
        self.state = "TERMINAL_AND_ORIGINAL_OWNER_RELEASED"
        _remove_identity(_ACTIVE_LEASES, self)
        _remove_identity(_UNKNOWN_LEASES, self)
        return {
            "status": self.state,
            "original_pid": self.original_pid,
            "original_birth_filetime": self.original_birth_filetime,
            "original_image_path": self.original_image_path,
            "exit_code": exit_code,
            "timeout_unknown_resolved_by_original_handle_terminal": timeout_unknown_candidate,
            "root_owner_retained_through_terminal": True,
            "replay": False,
        }

    def close_before_once_without_birth(self) -> None:
        if self.state != "PINNED_PREBIRTH":
            _fail("pre-once cleanup is unavailable after create intent or process birth", state=self.state)
        self.state = "PREBIRTH_ABORT_CLEANUP_INTENT"
        try:
            self.ops.finish_original_owner()
        except BaseException as exc:
            self._mark_unknown(exc)
            raise
        self.state = "ABORTED_BEFORE_ONCE_NO_BIRTH"
        _remove_identity(_ACTIVE_LEASES, self)

    def _mark_unknown(self, cause: BaseException, *, terminal_release_eligible: bool = False,
                      reason: str | None = None) -> None:
        if self.first_cause is None:
            self.first_cause = cause
        self.state = "UNKNOWN_RETAIN_ORIGINAL_HANDLES_NO_REPLAY"
        self.unknown_terminal_release_eligible = terminal_release_eligible
        self.unknown_reason = reason if terminal_release_eligible else "UNQUALIFIED_UNKNOWN"
        if not any(item is self for item in _UNKNOWN_LEASES):
            _UNKNOWN_LEASES.append(self)
        if not any(item is self for item in _ACTIVE_LEASES):
            _ACTIVE_LEASES.append(self)


def _remove_identity(rows: list[Any], target: Any) -> None:
    for index, value in enumerate(rows):
        if value is target:
            del rows[index]
            return


class R14AuthorityRootOpsR6B:
    """Concrete R14 SDK bridge used by a separately reviewed R6 gate/helper.

    The gate module is the exact R5J source object loaded after the full R14
    hash closure is verified. ``live_prebirth`` is the result of that gate's
    _verify_remote_snapshot call, not a caller-assembled expected-current proof.
    """

    def __init__(self, api: Any, provider: Any, gate: Any, outer: Mapping[str, Any],
                 live_prebirth: Mapping[str, Any], root_path: str):
        self.api, self.provider, self.gate = api, provider, gate
        self.outer, self.live_prebirth = outer, live_prebirth
        self.root_path = root_path
        self.owner: Any = None
        self.security_context: Any = None
        self.parent_handles: list[Any] = []
        self.root_handle: Any = None
        self.once_handle: Any = None
        self.expected_root: Mapping[str, Any] | None = None
        self.root_full_before: list[Any] | None = None
        self.owner_sid_reader = gate._runtime_owner_sid_text
        self.full_getter: Any = None
        self._finish_original_fn: Any = None
        self.effect_started = False

    def pin_and_verify_prebirth(self) -> Mapping[str, Any]:
        if os.name != "nt":
            _fail("R14 native root pin is Windows-only")
        self.gate._validate_outer_envelope(self.outer)
        expected = self.outer.get("path_proofs", {}).get("authority_root")
        if not isinstance(expected, dict):
            _fail("R5J strict authority-root proof is absent")
        # R5J _verify_remote_snapshot returns its `root_proof` for TASK_ROOT;
        # it does not return the AUTHORITY_ROOT proof. Recompute the latter
        # through the same reviewed two-chain R5J API before acquiring this
        # longer-lived owner, and bind it to the immutable prepared proof.
        live_authority_root = self.gate._r5j_private_path_proof(
            self.provider, self.root_path, directory=True,
        )
        self.gate._expect_proof(live_authority_root, expected, label=self.root_path)
        if (self.live_prebirth.get("r14_source_helper_count") != 179
                or self.live_prebirth.get("source_path_proofs_sha256")
                != self.outer["source_fingerprints"]["source_path_proofs_sha256"]):
            _fail("R5J prebirth readback does not bind the complete 179-member proof")
        self.expected_root = expected
        from comsol_mcp._windows_host_preflight_owner import finish_original, hold_original

        self._finish_original_fn = finish_original
        self.owner = hold_original(self.api)
        self.full_getter = self.provider._windows_original_full_getter(self.api)
        self.security_context = self.provider._windows_security_context(self.api)
        persistent_acls, volume_serial = self.provider._windows_volume_has_persistent_acls(
            self.api, Path(self.root_path),
        )
        if not persistent_acls:
            _fail("authority-root volume does not provide persistent ACLs")
        self.volume_serial = volume_serial
        parent_path = ntpath.dirname(self.root_path)
        self.parent_handles = self.api.open_security_path(Path(parent_path), directory=True)
        expected_chain = expected.get("path_chain")
        if not isinstance(expected_chain, list) or len(expected_chain) < 2:
            _fail("prebirth authority-root proof must contain its drive and parent chain")
        if len(self.parent_handles) != len(expected_chain) - 1:
            _fail("pinned parent chain length differs from R5J prebirth authority-root proof")
        for index, handle in enumerate(self.parent_handles):
            self._verify_component(handle, expected_chain[index], index=index)
        leaf = ntpath.basename(self.root_path)
        if not self.parent_handles or not leaf:
            _fail("authority-root path lacks a pinned parent and leaf")
        self.root_handle = self._open_exclusive_directory(self.parent_handles[-1], leaf)
        self._verify_component(self.root_handle, expected_chain[-1], index=len(expected_chain) - 1)
        self.root_full_before = self.gate._normalize_native_full_fingerprint(
            self.full_getter(self.root_handle, directory=True), label="exclusive original authority-root handle",
        )
        pre_entries = self._directory_entries(self.root_handle)
        expected_entries = self._expected_pre_once_entries()
        if pre_entries != expected_entries:
            _fail("authority-root entry set differs from the exact closed PREP candidate",
                  expected=expected_entries, actual=pre_entries)
        if any(name.casefold() == ONCE_LEAF.casefold() for name in pre_entries):
            _fail("ONCE_USED already exists in the authority root; no replay")
        self._assert_once_absent_by_original_handle(self.root_handle)
        return {
            "schema": R6B_SCHEMA,
            "status": "PREBIRTH_ROOT_PINNED_STRICT",
            "root_path": self.root_path,
            "root_handle_identity": self._root_identity(self.root_handle),
            "prebirth_root_native_full_fingerprint": list(self.root_full_before),
            "prebirth_root_proof_sha256": self.gate._sha256(self.gate._canonical_json(expected)),
            "live_r5j_authority_root_proof_sha256": self.gate._sha256(
                self.gate._canonical_json(live_authority_root),
            ),
            "expected_entry_names": sorted(pre_entries),
            "once_absent_relative_to_original_handle": True,
            "exclusive_share_mode": FILE_SHARE_NONE,
            "same_api_owner_retained": True,
        }

    def create_once_relative(self, raw: bytes, expected_sha256: str) -> Mapping[str, Any]:
        if self.root_handle is None or self.expected_root is None or self.root_full_before is None:
            _fail("relative ONCE create requires the exact pinned R5J authority-root object")
        digest = hashlib.sha256(raw).hexdigest()
        if digest != expected_sha256:
            _fail("relative ONCE bytes differ from the separately reviewed digest")
        if self.effect_started:
            _fail("ONCE creation already entered; no retry/replay")
        self.effect_started = True
        # _API.create_owned calls _open_relative with FILE_CREATE,
        # OBJ_DONT_REPARSE, FILE_OPEN_REPARSE_POINT and the retained root handle.
        self.once_handle = self.api.create_owned(self.root_handle, ONCE_LEAF)
        info_identity, info_size = self.api.info(self.once_handle, directory=False)
        once_native_before_write = self.gate._normalize_native_full_fingerprint(
            self.full_getter(self.once_handle, directory=False), label="new empty relative ONCE file handle",
        )
        if (once_native_before_write[1] != 0 or once_native_before_write[5] != 1
                or tuple(info_identity) != tuple(once_native_before_write[0])
                or type(info_size) is not int or info_size != 0):
            _fail("new FILE_CREATE ONCE object is not the exact empty ordinary single-link file")
        write_digest, write_size = self._write_flush_readback(self.once_handle, raw)
        if write_digest != expected_sha256 or write_size != len(raw):
            _fail("relative ONCE same-handle write/flush/readback differs")
        after_identity, after_info_size = self.api.info(self.once_handle, directory=False)
        once_native_after_write = self.gate._normalize_native_full_fingerprint(
            self.full_getter(self.once_handle, directory=False), label="written relative ONCE file handle",
        )
        if (once_native_after_write[1] != len(raw) or once_native_after_write[5] != 1
                or tuple(after_identity) != tuple(once_native_after_write[0])
                or tuple(after_identity) != tuple(info_identity)
                or type(after_info_size) is not int or after_info_size != len(raw)):
            _fail("relative ONCE object identity or exact content size changed across the same-handle write")
        # Re-check the pinned private parents and the same authority-root
        # object/security after the one authorized directory-entry effect.
        # The root timestamp transition is recorded separately below and never
        # substitutes for the immutable R5J prebirth proof.
        self._verify_parent_chain_after_once()
        root_security_after = self._verify_root_security_after_once()
        root_after = self.gate._normalize_native_full_fingerprint(
            self.full_getter(self.root_handle, directory=True), label="same pinned root after relative ONCE create",
        )
        self._validate_once_effect_transition(self.root_full_before, root_after)
        entries_after = self._directory_entries(self.root_handle)
        expected_after = dict(self._expected_pre_once_entries())
        expected_after[ONCE_LEAF] = int(once_native_after_write[0][1])
        if entries_after != expected_after:
            _fail("authority-root entry set changed beyond the single relative ONCE create",
                  expected=expected_after, actual=entries_after)
        # The full prebirth proof remains immutable; post-create timestamps are
        # recorded only as an explicit effect receipt, never substituted for it.
        return {
            "schema": R6B_SCHEMA,
            "status": "ONCE_CREATED_RELATIVE_FILE_CREATE_READBACK_PASS",
            "create_disposition": "FILE_CREATE",
            "relative_to_original_root_handle": True,
            "leaf": ONCE_LEAF,
            "sha256": write_digest,
            "bytes": write_size,
            "new_file_size_before_write": int(info_size),
            "file_size_after_same_handle_write": int(after_info_size),
            "same_handle_file_identity_before_after_write": list(info_identity) == list(after_identity),
            "once_native_before_write_full_fingerprint": list(once_native_before_write),
            "once_native_after_write_full_fingerprint": list(once_native_after_write),
            "once_native_full_fingerprint": list(once_native_after_write),
            "authority_root_prebirth_native_full_fingerprint": list(self.root_full_before),
            "authority_root_post_create_native_full_fingerprint": list(root_after),
            "authority_root_prebirth_proof_replaced": False,
            "root_path_binding_unchanged": {
                "verified_by": "same exclusive-share-zero original root handle plus exact native object identity",
                "root_handle_identity": self._root_identity(self.root_handle),
            },
            "root_owner_dacl_rechecked_post_effect": root_security_after,
            "pinned_parent_chain_rechecked_post_effect": True,
            "directory_names_before": sorted(self._expected_pre_once_entries()),
            "directory_names_after": sorted(entries_after),
            "directory_entry_exact_set_delta": {
                "created": [{"name": ONCE_LEAF, "file_id": int(once_native_after_write[0][1])}],
                "removed": [],
                "replaced": [],
            },
            "unknown_entries": 0,
            "original_root_handle_retained": True,
        }

    def finish_original_owner(self) -> None:
        if self.owner is None or self._finish_original_fn is None:
            _fail("original R14 owner was not initialized")
        self._finish_original_fn(self.api, None)

    def cleanup_before_once(self, cause: BaseException) -> None:
        if self.effect_started:
            _fail("original root owner cannot be released after relative ONCE create intent")
        if self.owner is None:
            return
        self.owner.remember(cause)
        if self._finish_original_fn is None:
            _fail("original R14 owner has no qualified finish callback")
        self._finish_original_fn(self.api, cause)

    def _verify_component(self, handle: Any, expected: Mapping[str, Any], *, index: int) -> None:
        path = expected.get("path")
        if not isinstance(path, str) or type(expected.get("directory")) is not bool or not expected["directory"]:
            _fail("authority-root proof component is not an ordinary directory", index=index)
        identity, size = self.api.info(handle, directory=True)
        volume = int(identity[0])
        if volume != int(self.volume_serial):
            _fail("pinned authority-root component is outside the ACL-qualified volume", path=path)
        mutable = bool(self.gate._outside_task_root_ancestor(path, directory=True))
        daemon_sid, trusted_sids, _ = self.security_context
        descriptor_sha, owner_class = self.provider._win32_descriptor_digest(
            self.api, handle, directory=True,
            require_daemon_owner=self.gate._path_within_task_root(self.root_path) and not mutable,
            daemon_sid=daemon_sid, trusted_sids=trusted_sids,
            **({"pinned_ancestor_traversal": True} if mutable else {}),
        )
        full = self.gate._normalize_native_full_fingerprint(
            self.full_getter(handle, directory=True), label="pinned authority-root component " + path,
        )
        owner_sid = self.owner_sid_reader(self.api, handle)
        actual = {
            "path": path, "directory": True,
            "identity": {"device": int(identity[0]), "inode": int(identity[1])},
            "info_size": int(size),
            "native_full_fingerprint": full,
            "native_full_fingerprint_second": list(full),
            "descriptor_sha256": descriptor_sha,
            "owner_classification": owner_class,
            "owner_sid": owner_sid,
            "outside_task_root_ancestor": mutable,
            "outside_task_root_ancestor_time_differences": {},
        }
        if self.gate._r5j_identity_projection(actual) != self.gate._r5j_identity_projection(dict(expected)):
            _fail("pinned R6 path component differs from the exact R5J prebirth proof", path=path, index=index)

    def _open_exclusive_directory(self, parent_handle: Any, leaf: str) -> Any:
        from comsol_mcp._windows_host_preflight_owner import scalar
        from comsol_mcp._windows_parameter_publish import (
            HANDLE, _IoStatusBlock, _ObjectAttributes, _UnicodeString,
        )
        if not leaf or "\\" in leaf or "/" in leaf or leaf in {".", ".."}:
            _fail("authority-root leaf is not one fixed relative component")
        encoded = leaf.encode("utf-16-le", "strict")
        buffer = C.create_unicode_buffer(leaf)
        name = _UnicodeString(len(encoded), len(encoded) + 2, C.cast(buffer, C.POINTER(C.c_wchar)))
        attributes = _ObjectAttributes(C.sizeof(_ObjectAttributes), parent_handle, C.pointer(name),
                                       OBJ_CASE_INSENSITIVE | OBJ_DONT_REPARSE, None, None)
        output = HANDLE()
        io_status = _IoStatusBlock()
        access = FILE_DIRECTORY_ACCESS | FILE_LIST_DIRECTORY | FILE_ADD_FILE | FILE_READ_CONTROL
        options = FILE_DIRECTORY_FILE | FILE_SYNCHRONOUS_IO_NONALERT | FILE_OPEN_REPARSE_POINT
        try:
            status = scalar(self.owner.allocation_call(
                "NtCreateFile", self.api.nt, output,
                C.byref(output), access, C.byref(attributes), C.byref(io_status),
                None, FILE_ATTRIBUTE_NORMAL, FILE_SHARE_NONE, FILE_OPEN, options, None, 0,
                family="handle", keepalive=(buffer, name, attributes, io_status, leaf, self),
            ), "NTSTATUS")
        except BaseException as exc:
            if output.value not in {None, 0, C.c_void_p(-1).value}:
                self._mark_output_unknown(output, exc)
            raise
        if status < 0:
            if output.value:
                self._mark_output_unknown(output, LeaseError("exclusive authority-root open returned a partial handle"))
            _fail("exclusive same-object authority-root open failed", ntstatus=status & 0xFFFFFFFF)
        if not output.value:
            _fail("exclusive authority-root open returned success without a handle")
        if int(io_status.Information) != FILE_OPENED:
            self._mark_output_unknown(output, LeaseError("authority-root open disposition was not FILE_OPENED"))
            _fail("authority-root open did not confirm FILE_OPENED", information=int(io_status.Information))
        return output

    def _mark_output_unknown(self, output: Any, cause: BaseException) -> None:
        self.owner.remember(cause)
        registry = self.owner.registry
        entry = registry.find(self.api, output)
        if entry is not None:
            entry.state = "UNKNOWN"
            entry.error = cause
        _fail("ambiguous native authority-root output retained; no close/retry")

    def _expected_pre_once_entries(self) -> dict[str, int]:
        source = self.live_prebirth
        paths = {
            "BINDING.json": source.get("inner_binding"),
            "PREBIRTH_BINDING.json": source.get("outer_binding"),
            "MAIN_APPROVAL.json": source.get("approval"),
        }
        result = {}
        for name, record in paths.items():
            if not isinstance(record, dict) or not isinstance(record.get("private_path"), dict):
                _fail("live strict prebirth result is missing an exact authority file proof", name=name)
            identity = record["private_path"].get("identity")
            if not isinstance(identity, dict) or type(identity.get("inode")) is not int:
                _fail("live authority file proof lacks the native file-id domain", name=name)
            result[name] = identity["inode"]
        return result

    def _directory_entries(self, handle: Any) -> dict[str, int]:
        rows = self.api.enumerate_directory(handle)
        result = {}
        for row in rows:
            name, file_id = row.get("name"), row.get("file_id")
            if (not isinstance(name, str) or type(file_id) is not int or file_id <= 0
                    or name.casefold() in {item.casefold() for item in result}):
                _fail("authority-root handle enumeration contains malformed or duplicate entries")
            result[name] = file_id
        return result

    def _assert_once_absent_by_original_handle(self, root_handle: Any) -> None:
        try:
            handle = self.api.open_source(root_handle, ONCE_LEAF)
        except FileNotFoundError:
            return
        except BaseException as exc:
            _fail("handle-relative ONCE absence could not be confirmed", error_type=type(exc).__name__)
        try:
            self.api.info(handle, directory=False)
        finally:
            self.api.close(handle)
        _fail("ONCE_USED already exists relative to the pinned authority root; no replay")

    def _write_flush_readback(self, handle: Any, raw: bytes) -> tuple[str, int]:
        from comsol_mcp._windows_host_preflight_owner import scalar
        owner = self.owner
        write_file = owner.function("WriteFile", self.api.k)
        flush_file = owner.function("FlushFileBuffers", self.api.k)
        set_pointer = owner.function("SetFilePointerEx", self.api.k)
        read_file = owner.function("ReadFile", self.api.k)
        offset = 0
        while offset < len(raw):
            block = raw[offset:offset + 1024 * 1024]
            buffer = C.create_string_buffer(block)
            written = C.c_uint32()
            if not scalar(write_file(handle, buffer, len(block), C.byref(written), None), "BOOL"):
                _fail("same-handle relative ONCE write failed")
            if type(written.value) is not int or written.value <= 0 or written.value > len(block):
                _fail("same-handle relative ONCE write made no bounded progress")
            offset += written.value
            owner.hold(handle, buffer, written)
        if not scalar(flush_file(handle), "BOOL"):
            _fail("same-handle relative ONCE FlushFileBuffers failed")
        position = C.c_int64()
        if not scalar(set_pointer(handle, 0, C.byref(position), 0), "BOOL"):
            _fail("same-handle relative ONCE readback seek failed")
        digest = hashlib.sha256()
        total = 0
        while True:
            buffer = C.create_string_buffer(65536)
            count = C.c_uint32()
            if not scalar(read_file(handle, buffer, len(buffer), C.byref(count), None), "BOOL"):
                _fail("same-handle relative ONCE readback failed")
            if count.value == 0:
                break
            if count.value > len(buffer) or total + count.value > len(raw):
                _fail("same-handle relative ONCE readback exceeded its exact byte bound")
            digest.update(buffer.raw[:count.value])
            total += count.value
            owner.hold(handle, buffer, count)
        return digest.hexdigest(), total

    def _validate_once_effect_transition(self, before: list[Any], after: list[Any]) -> None:
        left = self.gate._normalize_native_full_fingerprint(before, label="R6 prebirth root native tuple")
        right = self.gate._normalize_native_full_fingerprint(after, label="R6 post-create root native tuple")
        if left[0] != right[0] or left[1] != right[1] or left[2] != right[2] or left[5] != right[5]:
            _fail("relative once effect changed authority-root object identity or non-time metadata")
        if right[3] < left[3] or right[4] < left[4]:
            _fail("authority-root change/write timestamps regressed across relative once creation")

    def _verify_parent_chain_after_once(self) -> None:
        expected_chain = self.expected_root.get("path_chain") if self.expected_root else None
        if not isinstance(expected_chain, list) or len(self.parent_handles) != len(expected_chain) - 1:
            _fail("pinned parent chain no longer matches the R5J prebirth proof shape")
        for index, handle in enumerate(self.parent_handles):
            self._verify_component(handle, expected_chain[index], index=index)

    def _verify_root_security_after_once(self) -> dict[str, Any]:
        if self.root_handle is None or not isinstance(self.expected_root, Mapping):
            _fail("post-effect root check lacks the retained R5J root object")
        chain = self.expected_root.get("path_chain")
        if not isinstance(chain, list) or not chain:
            _fail("post-effect root check lacks the exact R5J root proof component")
        expected_leaf = chain[-1]
        identity, size = self.api.info(self.root_handle, directory=True)
        actual_identity = {"device": int(identity[0]), "inode": int(identity[1])}
        if (int(identity[0]) != int(self.volume_serial)
                or actual_identity != expected_leaf.get("identity")
                or int(size) != expected_leaf.get("info_size")):
            _fail("same pinned authority-root identity, volume, or non-time size changed across ONCE effect",
                  actual_identity=actual_identity, actual_size=int(size))
        daemon_sid, trusted_sids, _ = self.security_context
        descriptor_sha, owner_class = self.provider._win32_descriptor_digest(
            self.api, self.root_handle, directory=True, require_daemon_owner=True,
            daemon_sid=daemon_sid, trusted_sids=trusted_sids,
        )
        owner_sid = self.owner_sid_reader(self.api, self.root_handle)
        if (descriptor_sha != expected_leaf.get("descriptor_sha256")
                or owner_class != expected_leaf.get("owner_classification")
                or owner_sid != expected_leaf.get("owner_sid")):
            _fail("same authority-root owner or DACL changed across the exact ONCE effect",
                  descriptor_sha256=descriptor_sha, owner_classification=owner_class, owner_sid=owner_sid)
        return {
            "identity": actual_identity,
            "info_size": int(size),
            "descriptor_sha256": descriptor_sha,
            "owner_classification": owner_class,
            "owner_sid": owner_sid,
            "rechecked_on_same_exclusive_root_handle": True,
        }

    def _root_identity(self, handle: Any) -> dict[str, int]:
        identity, _size = self.api.info(handle, directory=True)
        return {"device": int(identity[0]), "inode": int(identity[1])}


def active_lease_count() -> int:
    return len(_ACTIVE_LEASES)


def unknown_lease_count() -> int:
    return len(_UNKNOWN_LEASES)
