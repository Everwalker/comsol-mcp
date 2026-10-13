"""Narrow Windows Filtering Platform filter mutation adapter.

This module supports one bounded subset only: persistent PERMIT filters at
the outbound IPv4 ALE_AUTH_CONNECT layer, tied to an exact configured
application, AppContainer SID, protocol, local/remote CIDR and remote port.
It does not create providers/sublayers, block traffic, install callouts, or
claim effective firewall arbitration or T037 isolation.

All Win32 APIs are late-bound and use fixed-width SDK structures imported
from the read-only T037 observer. Importing on another platform is safe;
constructing the native adapter there is a typed refusal.
"""
from __future__ import annotations

import ctypes as C
import hashlib
import ipaddress
import json
import os
import re
import uuid
from typing import Any, Mapping

from ._execution_contract import ExecutionContractError
from . import _windows_t037_native as native

_GUID_BODY = (r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-"
              r"[0-9a-fA-F]{4}-[0-9a-fA-F]{12}")
_GUID = re.compile(r"^(?:\{" + _GUID_BODY + r"\}|" + _GUID_BODY + r")$")
_SHA = re.compile(r"^[0-9a-f]{64}$")

# GUID constants from the Windows Filtering Platform SDK (fwpmu.h).
FWPM_LAYER_ALE_AUTH_CONNECT_V4 = "{C38D57D1-05A7-4C33-904F-7FBCEEE60E82}"
FWPM_CONDITION_ALE_APP_ID = "{D78E1E87-8644-4EA5-9436-EDF447820E2D}"
FWPM_CONDITION_ALE_USER_ID = "{AF043A0A-B34D-4F86-979C-C90371AF6E66}"
FWPM_CONDITION_IP_LOCAL_ADDRESS = "{D9EE00DE-C1EF-4617-BFE3-FFD8F5A08957}"
FWPM_CONDITION_IP_REMOTE_ADDRESS = "{B235AE9A-1D64-49B8-A44C-5FF3D9095045}"
FWPM_CONDITION_IP_PROTOCOL = "{3971EF2B-623E-4F9A-8CB1-6E79B806B9A7}"
FWPM_CONDITION_IP_LOCAL_PORT = "{0C1BA1AF-5765-453F-AF22-A8F791AC775B}"
FWPM_CONDITION_IP_REMOTE_PORT = "{C35A604D-D22B-4E1A-91B4-68F674EE674B}"

FWP_EMPTY = 0
FWP_UINT8 = 1
FWP_UINT16 = 2
FWP_BYTE_BLOB_TYPE = 12
FWP_SECURITY_DESCRIPTOR_TYPE = 14
FWP_V4_ADDR_MASK = 19
FWP_MATCH_EQUAL = 0
FWP_ACTRL_MATCH_FILTER = 0x00000001
FWPM_FILTER_FLAG_PERSISTENT = 0x00000001
FWP_ACTION_PERMIT = 0x00001002
FWP_E_FILTER_NOT_FOUND = 0x80320003


class WindowsWfpMutationError(ExecutionContractError):
    """Typed failure from the narrow WFP mutation API."""


def _fail(code: str, message: str, *, details: Mapping[str, Any] | None = None,
          safe_retry: bool = False, stage: str = "validation") -> None:
    raise WindowsWfpMutationError(code, message, safe_retry=safe_retry,
                                  details=details, stage=stage)


def _canonical(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, ensure_ascii=False,
                      separators=(",", ":"), allow_nan=False).encode("utf-8")


def sha256(value: Any) -> str:
    return hashlib.sha256(_canonical(value)).hexdigest()


def guid_key(value: str) -> str:
    if not isinstance(value, str) or not _GUID.fullmatch(value):
        _fail("WFP_GUID_INVALID", "WFP key is not a canonical GUID")
    return value.strip("{}").casefold()


def guid_struct(value: str):
    """Return the SDK GUID structure in Windows UUID byte order."""
    key = guid_key(value)
    raw = uuid.UUID(key).bytes_le
    return native.GUID.from_buffer_copy(raw)


def normalize_endpoint_grant(grant: Any, *, policy: Any,
                             launch_images: tuple[Any, ...]) -> dict[str, Any]:
    """Validate and normalize the one WFP grant shape implemented here."""
    expected_fields = {
        "role", "direction", "layer", "layer_key", "address_family", "protocol",
        "application_path", "principal_sid", "local_addresses", "remote_addresses",
        "local_ports", "remote_ports",
    }
    if (not hasattr(grant, "as_dict") or set(grant.as_dict()) != expected_fields
            or grant.role not in {"worker", "server"}
            or grant.direction != "OUTBOUND" or grant.layer != "ALE_AUTH_CONNECT"
            or guid_key(grant.layer_key) != guid_key(FWPM_LAYER_ALE_AUTH_CONNECT_V4)
            or grant.address_family != "IPV4" or type(grant.protocol) is not int
            or grant.protocol not in {6, 17}
            or grant.principal_sid.casefold() != policy.appcontainer_sid.casefold()
            or len(grant.local_addresses) != 1 or len(grant.remote_addresses) != 1
            or len(grant.remote_ports) != 1 or len(grant.local_ports) > 1):
        _fail("WFP_GRANT_UNSUPPORTED",
              "WFP mutation supports one outbound IPv4 TCP/UDP ALE connect grant with exact selectors")
    images = [row for row in launch_images if row.role == grant.role]
    if len(images) != 1 or images[0].path.casefold() != grant.application_path.casefold():
        _fail("WFP_GRANT_PROFILE_MISMATCH", "WFP application does not match one exact trusted launch image")
    if grant.role not in {"worker", "server"}:
        _fail("WFP_GRANT_UNSUPPORTED", "WFP role is outside the supported Server/Worker profile")
    networks: dict[str, str] = {}
    for label, raw in (("local", grant.local_addresses[0]), ("remote", grant.remote_addresses[0])):
        try:
            network = ipaddress.ip_network(raw, strict=True)
        except ValueError as exc:
            _fail("WFP_GRANT_UNSUPPORTED", "WFP IPv4 CIDR is not canonical", details={"selector": label})
        if network.version != 4 or network.with_prefixlen != raw:
            _fail("WFP_GRANT_UNSUPPORTED", "WFP selector is not one canonical IPv4 CIDR",
                  details={"selector": label})
        networks[label] = network.with_prefixlen
    remote_port = grant.remote_ports[0]
    if type(remote_port) is not int or not 1 <= remote_port <= 65535:
        _fail("WFP_GRANT_UNSUPPORTED", "WFP remote port is outside the supported TCP/UDP range")
    local_port = grant.local_ports[0] if grant.local_ports else None
    if local_port is not None and (type(local_port) is not int or not 1 <= local_port <= 65535):
        _fail("WFP_GRANT_UNSUPPORTED", "WFP local port is outside the supported TCP/UDP range")
    return {
        "role": grant.role,
        "direction": "OUTBOUND",
        "layer": "ALE_AUTH_CONNECT",
        "layer_key": FWPM_LAYER_ALE_AUTH_CONNECT_V4,
        "address_family": "IPV4",
        "protocol": grant.protocol,
        "application_path": grant.application_path,
        "principal_sid": grant.principal_sid,
        "local_address": networks["local"],
        "remote_address": networks["remote"],
        "local_port": local_port,
        "remote_port": remote_port,
    }


class CtypesWindowsWfpMutationApi:
    """Documented WFP user-mode filter CRUD for the narrow supported subset."""

    evidence_classification = "WINDOWS_NATIVE_WFP_READBACK_ONLY"

    def __init__(self):
        if os.name != "nt":
            _fail("WFP_NATIVE_UNSUPPORTED", "native WFP mutation is available only on Windows")
        self.api = native.CtypesWindowsPolicyApi()
        self._fwpu = self.api._fwpu
        self._advapi = self.api._advapi
        self._bind_mutation()

    def _bind_mutation(self) -> None:
        f = self._fwpu
        C0 = C
        f.FwpmProviderGetByKey0.argtypes = [native.HANDLE, C0.POINTER(native.GUID),
                                            C0.POINTER(C0.POINTER(native.FWPM_PROVIDER0))]
        f.FwpmProviderGetByKey0.restype = native.DWORD
        f.FwpmSubLayerGetByKey0.argtypes = [native.HANDLE, C0.POINTER(native.GUID),
                                            C0.POINTER(C0.POINTER(native.FWPM_SUBLAYER0))]
        f.FwpmSubLayerGetByKey0.restype = native.DWORD
        f.FwpmFilterGetByKey0.argtypes = [native.HANDLE, C0.POINTER(native.GUID),
                                          C0.POINTER(C0.POINTER(native.FWPM_FILTER0))]
        f.FwpmFilterGetByKey0.restype = native.DWORD
        f.FwpmFilterAdd0.argtypes = [native.HANDLE, C0.POINTER(native.FWPM_FILTER0),
                                     C0.c_void_p, C0.POINTER(C0.c_uint64)]
        f.FwpmFilterAdd0.restype = native.DWORD
        f.FwpmFilterDeleteByKey0.argtypes = [native.HANDLE, C0.POINTER(native.GUID)]
        f.FwpmFilterDeleteByKey0.restype = native.DWORD
        f.FwpmGetAppIdFromFileName0.argtypes = [native.LPCWSTR,
                                                C0.POINTER(C0.POINTER(native.FWP_BYTE_BLOB))]
        f.FwpmGetAppIdFromFileName0.restype = native.DWORD
        self._advapi.ConvertStringSidToSidW.argtypes = [native.LPCWSTR, C0.POINTER(C0.c_void_p)]
        self._advapi.ConvertStringSidToSidW.restype = native.BOOL
        self._advapi.BuildSecurityDescriptorW.argtypes = [
            C0.c_void_p, C0.c_void_p, native.DWORD, C0.POINTER(_EXPLICIT_ACCESS_W),
            native.DWORD, C0.c_void_p, C0.c_void_p, C0.POINTER(native.DWORD), C0.POINTER(C0.c_void_p),
        ]
        self._advapi.BuildSecurityDescriptorW.restype = native.DWORD
        self._advapi.LocalFree.argtypes = [C0.c_void_p]
        self._advapi.LocalFree.restype = C0.c_void_p

    def _check(self, status: int, field: str, *, code: str = "WFP_API_FAILED") -> None:
        if int(status) != 0:
            _fail(code, f"Windows Filtering Platform {field} failed",
                  details={"status": int(status)}, stage="execution")

    def _free(self, pointer: int | None, *, primary: BaseException | None = None) -> None:
        if not pointer:
            return
        try:
            handle = C.c_void_p(int(pointer))
            self._fwpu.FwpmFreeMemory0(C.byref(handle))
        except BaseException as exc:
            _fail("WFP_ALLOCATION_RELEASE_UNKNOWN", "WFP-owned allocation could not be released",
                  details={"release_type": type(exc).__name__,
                           "primary_type": type(primary).__name__ if primary is not None else None},
                  stage="execution")

    def _free_local(self, pointer: C.c_void_p, *, primary: BaseException | None = None) -> None:
        if not pointer.value:
            return
        try:
            result = self._advapi.LocalFree(pointer)
            if result:
                _fail("WFP_LOCAL_ALLOCATION_RELEASE_UNKNOWN", "LocalFree did not release the WFP condition descriptor",
                      details={"primary_type": type(primary).__name__ if primary is not None else None},
                      stage="execution")
        except WindowsWfpMutationError:
            raise
        except BaseException as exc:
            _fail("WFP_LOCAL_ALLOCATION_RELEASE_UNKNOWN", "LocalFree raised while releasing a WFP condition descriptor",
                  details={"release_type": type(exc).__name__,
                           "primary_type": type(primary).__name__ if primary is not None else None},
                  stage="execution")

    def _session(self, *, readonly: bool, callback):
        f = self._fwpu
        engine = native.HANDLE()
        status = f.FwpmEngineOpen0(None, native.CtypesWindowsPolicyApi.RPC_C_AUTHN_WINNT,
                                   None, None, C.byref(engine))
        if int(status) != 0:
            if engine.value:
                try:
                    close_status = int(f.FwpmEngineClose0(engine))
                except BaseException as close_exc:
                    _fail("WFP_SESSION_RELEASE_UNKNOWN", "failed WFP open returned a handle that could not be closed",
                          details={"open_status": int(status), "close_type": type(close_exc).__name__},
                          stage="execution")
                if close_status != 0:
                    _fail("WFP_SESSION_RELEASE_UNKNOWN", "failed WFP open returned a handle that could not be closed",
                          details={"open_status": int(status), "close_status": close_status}, stage="execution")
            self._check(status, "FwpmEngineOpen0")
        if not engine.value:
            _fail("WFP_NATIVE_ABI_INVALID", "FwpmEngineOpen0 returned a null engine handle", stage="execution")
        transaction = False
        primary: BaseException | None = None
        result = None
        cleanup_errors: list[tuple[str, int | str]] = []
        try:
            flags = self.api.FWPM_TXN_READ_ONLY if readonly else 0
            status = f.FwpmTransactionBegin0(engine, flags)
            self._check(status, "FwpmTransactionBegin0")
            transaction = True
            result = callback(engine)
            status = f.FwpmTransactionCommit0(engine)
            if int(status) != 0:
                # A failed commit does not prove whether BFE applied the write.
                _fail("WFP_COMMIT_OUTCOME_UNKNOWN", "WFP transaction commit outcome is unknown",
                      details={"status": int(status)}, stage="execution")
            transaction = False
        except BaseException as exc:
            primary = exc
            if transaction:
                try:
                    abort_status = int(f.FwpmTransactionAbort0(engine))
                    if abort_status != 0:
                        cleanup_errors.append(("abort_status", abort_status))
                except BaseException as abort_exc:
                    cleanup_errors.append(("abort_type", type(abort_exc).__name__))
                transaction = False
        finally:
            if transaction:
                try:
                    abort_status = int(f.FwpmTransactionAbort0(engine))
                    if abort_status != 0:
                        cleanup_errors.append(("abort_status", abort_status))
                except BaseException as abort_exc:
                    cleanup_errors.append(("abort_type", type(abort_exc).__name__))
            try:
                close_status = int(f.FwpmEngineClose0(engine))
                if close_status != 0:
                    cleanup_errors.append(("close_status", close_status))
            except BaseException as close_exc:
                cleanup_errors.append(("close_type", type(close_exc).__name__))
        if primary is not None or cleanup_errors:
            if cleanup_errors:
                _fail("WFP_CLEANUP_OUTCOME_UNKNOWN", "WFP transaction/session cleanup could not be verified",
                      details={"primary_code": getattr(primary, "code", None),
                               "primary_type": type(primary).__name__ if primary is not None else None,
                               "cleanup": cleanup_errors}, stage="execution")
            assert primary is not None
            raise primary
        return result

    def _provider_row(self, engine: Any, key: str) -> dict[str, Any]:
        pointer = C.POINTER(native.FWPM_PROVIDER0)()
        guid = guid_struct(key)
        status = int(self._fwpu.FwpmProviderGetByKey0(engine, C.byref(guid), C.byref(pointer)))
        if status != 0 or not pointer:
            if pointer:
                self._free(C.cast(pointer, C.c_void_p).value)
            self._check(status, "FwpmProviderGetByKey0")
            _fail("WFP_NATIVE_ABI_INVALID", "provider lookup succeeded without a result object", stage="execution")
        try:
            item = pointer.contents
            return {
                "provider_key": self.api._guid(item.providerKey),
                "display_data": self.api._display_data(item.displayData, "provider"),
                "flags": int(item.flags),
                "provider_data_sha256": hashlib.sha256(bytes.fromhex(self.api._bounded_blob(item.providerData))).hexdigest(),
                "service_name": native._bounded_utf16(item.serviceName, "WFP provider service name", nullable=True),
                "security_sha256": self.api._wfp_security_sha(engine, "Provider", item.providerKey),
            }
        finally:
            self._free(C.cast(pointer, C.c_void_p).value)

    def _sublayer_row(self, engine: Any, key: str) -> dict[str, Any]:
        pointer = C.POINTER(native.FWPM_SUBLAYER0)()
        guid = guid_struct(key)
        status = int(self._fwpu.FwpmSubLayerGetByKey0(engine, C.byref(guid), C.byref(pointer)))
        if status != 0 or not pointer:
            if pointer:
                self._free(C.cast(pointer, C.c_void_p).value)
            self._check(status, "FwpmSubLayerGetByKey0")
            _fail("WFP_NATIVE_ABI_INVALID", "sublayer lookup succeeded without a result object", stage="execution")
        try:
            item = pointer.contents
            return {
                "sublayer_key": self.api._guid(item.subLayerKey),
                "display_data": self.api._display_data(item.displayData, "sublayer"),
                "flags": int(item.flags),
                "provider_key": self.api._guid(item.providerKey.contents) if item.providerKey else None,
                "provider_data_sha256": hashlib.sha256(bytes.fromhex(self.api._bounded_blob(item.providerData))).hexdigest(),
                "weight": int(item.weight),
                "security_sha256": self.api._wfp_security_sha(engine, "SubLayer", item.subLayerKey),
            }
        finally:
            self._free(C.cast(pointer, C.c_void_p).value)

    def _filter_row_or_none(self, engine: Any, key: str) -> dict[str, Any] | None:
        pointer = C.POINTER(native.FWPM_FILTER0)()
        guid = guid_struct(key)
        status = int(self._fwpu.FwpmFilterGetByKey0(engine, C.byref(guid), C.byref(pointer)))
        if status == 0 and pointer:
            try:
                return self.api._filter_row(engine, pointer.contents)
            finally:
                self._free(C.cast(pointer, C.c_void_p).value)
        if pointer:
            self._free(C.cast(pointer, C.c_void_p).value)
            _fail("WFP_NATIVE_ABI_INVALID", "failed filter lookup returned an allocated object",
                  details={"status": status}, stage="execution")
        if status == FWP_E_FILTER_NOT_FOUND:
            return None
        if status == 0:
            _fail("WFP_NATIVE_ABI_INVALID", "filter lookup succeeded without a result object", stage="execution")
        self._check(status, "FwpmFilterGetByKey0")
        return None

    def observe_snapshot(self) -> dict[str, Any]:
        return self.api.observe_wfp_snapshot()

    def read_filter(self, filter_key: str) -> dict[str, Any] | None:
        return self._session(readonly=True,
                             callback=lambda engine: self._filter_row_or_none(engine, filter_key))

    def _check_dependencies(self, engine: Any, profile: Any, expected_sha256: str) -> dict[str, Any]:
        policy = profile.windows_isolation
        provider = self._provider_row(engine, policy.wfp_provider_key)
        sublayer = self._sublayer_row(engine, policy.wfp_sublayer_key)
        if guid_key(sublayer.get("provider_key")) != guid_key(policy.wfp_provider_key):
            _fail("WFP_DEPENDENCY_IDENTITY_MISMATCH", "configured sublayer is not owned by the exact configured provider",
                  stage="execution")
        dependency = {"provider": provider, "sublayer": sublayer}
        actual_sha = sha256(dependency)
        if actual_sha != expected_sha256:
            _fail("WFP_DEPENDENCY_CHANGED", "provider or sublayer identity changed after preflight",
                  details={"expected_sha256": expected_sha256, "observed_sha256": actual_sha},
                  stage="execution")
        return dependency

    def create_filter(self, *, profile: Any, action: Mapping[str, Any],
                      expected_dependency_sha256: str) -> dict[str, Any]:
        policy = profile.windows_isolation
        grant = action["grant"]
        marker = _canonical({"schema": "comsol-mcp.t037-owned-wfp-filter/1",
                             "profile_sha256": profile.sha256,
                             "plan_sha256": action["plan_sha256"],
                             "action_id": action["action_id"],
                             "ownership_guid": action["ownership_guid"]})
        if len(marker) > 4096:
            _fail("WFP_FILTER_MARKER_INVALID", "owned filter marker exceeds its bound")

        def write(engine):
            self._check_dependencies(engine, profile, expected_dependency_sha256)
            if self._filter_row_or_none(engine, action["filter_key"]) is not None:
                _fail("WFP_FILTER_KEY_EXISTS", "configured WFP filter key already exists; no overwrite is permitted",
                      stage="execution")
            return self._add_filter_in_transaction(engine, policy, action, grant, marker)

        row = self._session(readonly=False, callback=write)
        # Transactional readback is followed by a fresh read-only session. A
        # failure here is a post-effect unknown; callers must not replay.
        persisted = self.read_filter(action["filter_key"])
        if persisted is None or sha256(persisted) != sha256(row):
            _fail("WFP_FILTER_READBACK_UNKNOWN", "committed WFP filter differs from the exact post-commit readback",
                  stage="execution")
        self._validate_filter_semantics(persisted, profile, action, grant, marker)
        return persisted

    def _add_filter_in_transaction(self, engine: Any, policy: Any, action: Mapping[str, Any],
                                   grant: Mapping[str, Any], marker: bytes) -> dict[str, Any]:
        app_blob_ptr = C.POINTER(native.FWP_BYTE_BLOB)()
        sid_ptr = C.c_void_p()
        security_descriptor = C.c_void_p()
        primary: BaseException | None = None
        app_id_bytes = None
        try:
            status = int(self._fwpu.FwpmGetAppIdFromFileName0(
                grant["application_path"], C.byref(app_blob_ptr)))
            if status != 0 or not app_blob_ptr:
                # If the API returned an allocation alongside an error, the
                # common finally block releases it exactly once.
                self._check(status, "FwpmGetAppIdFromFileName0")
                _fail("WFP_NATIVE_ABI_INVALID", "application-ID lookup succeeded without an app blob",
                      stage="execution")
            app_blob = app_blob_ptr.contents
            if int(app_blob.size) <= 0 or int(app_blob.size) > 256 * 1024 or not app_blob.data:
                _fail("WFP_NATIVE_ABI_INVALID", "WFP application-ID blob length/pointer is invalid",
                      stage="execution")
            app_id_bytes = C.string_at(app_blob.data, int(app_blob.size))
            sd_bytes = self._build_user_match_sd(grant["principal_sid"], sid_out=sid_ptr,
                                                 sd_out=security_descriptor)
            app_condition = self._condition(FWPM_CONDITION_ALE_APP_ID, FWP_BYTE_BLOB_TYPE)
            app_condition.conditionValue.byteBlob = app_blob_ptr
            sd_blob_data = (native.BYTE * len(sd_bytes)).from_buffer_copy(sd_bytes)
            sd_blob = native.FWP_BYTE_BLOB(len(sd_bytes), C.cast(sd_blob_data, C.POINTER(native.BYTE)))
            user_condition = self._condition(FWPM_CONDITION_ALE_USER_ID, FWP_SECURITY_DESCRIPTOR_TYPE)
            user_condition.conditionValue.sd = C.pointer(sd_blob)

            conditions, backing = self._conditions_for(grant, app_condition, user_condition)
            _ = backing, app_id_bytes, sd_blob_data
            filter_obj = self._make_filter(policy, action, marker, conditions)
            filter_id = C.c_uint64(0)
            filter_guid = guid_struct(action["filter_key"])
            if guid_key(self.api._guid(filter_obj.filterKey)) != guid_key(action["filter_key"]):
                _fail("WFP_NATIVE_ABI_INVALID", "filter key layout changed before FwpmFilterAdd0",
                      stage="execution")
            status = int(self._fwpu.FwpmFilterAdd0(
                engine, C.byref(filter_obj), None, C.byref(filter_id)))
            if status != 0:
                self._check(status, "FwpmFilterAdd0", code="WFP_FILTER_ADD_FAILED")
            if not 0 < int(filter_id.value) <= 0xffffffffffffffff:
                _fail("WFP_NATIVE_ABI_INVALID", "FwpmFilterAdd0 did not return a valid runtime filter ID",
                      stage="execution")
            row = self._filter_row_or_none(engine, action["filter_key"])
            if row is None or row.get("filter_id") != int(filter_id.value):
                _fail("WFP_FILTER_READBACK_UNKNOWN",
                      "new WFP filter ID/key did not match before transaction commit",
                      stage="execution")
            self._validate_filter_semantics(row, None, action, grant, marker,
                                            expected_policy=policy,
                                            expected_app_blob=app_id_bytes,
                                            expected_sd=sd_bytes)
            return row
        except BaseException as exc:
            primary = exc
            raise
        finally:
            release_error: BaseException | None = None
            if app_blob_ptr:
                try:
                    self._free(C.cast(app_blob_ptr, C.c_void_p).value, primary=primary)
                except BaseException as exc:
                    release_error = exc
            if security_descriptor.value:
                try:
                    self._free_local(security_descriptor, primary=primary)
                except BaseException as exc:
                    release_error = release_error or exc
            if sid_ptr.value:
                try:
                    self._advapi.LocalFree(sid_ptr)
                except BaseException as exc:
                    release_error = release_error or exc
            if release_error is not None:
                _fail("WFP_CONDITION_ALLOCATION_RELEASE_UNKNOWN",
                      "WFP condition construction allocations could not all be released",
                      details={"release_type": type(release_error).__name__,
                               "primary_type": type(primary).__name__ if primary is not None else None},
                      stage="execution")

    def _build_user_match_sd(self, sid_text: str, *, sid_out: C.c_void_p,
                             sd_out: C.c_void_p) -> bytes:
        if not self._advapi.ConvertStringSidToSidW(sid_text, C.byref(sid_out)) or not sid_out.value:
            _fail("WFP_USER_SID_UNAVAILABLE", "AppContainer SID could not be converted for the WFP user condition",
                  details={"winerror": int(C.get_last_error())}, stage="execution")
        trustee = _TRUSTEE_W(None, 0, 0, 0, sid_out.value)
        access = _EXPLICIT_ACCESS_W(FWP_ACTRL_MATCH_FILTER, 1, 0, trustee)  # GRANT_ACCESS, no inheritance
        size = native.DWORD(0)
        status = int(self._advapi.BuildSecurityDescriptorW(
            None, None, 1, C.byref(access), 0, None, None, C.byref(size), C.byref(sd_out)))
        if status != 0 or not sd_out.value or not 20 <= size.value <= 256 * 1024:
            _fail("WFP_USER_CONDITION_SD_INVALID", "Windows could not build a bounded user-match security descriptor",
                  details={"status": status, "size": int(size.value)}, stage="execution")
        return C.string_at(sd_out, int(size.value))

    @staticmethod
    def _condition(field_key: str, data_type: int):
        item = native.FWPM_FILTER_CONDITION0()
        item.fieldKey = guid_struct(field_key)
        item.matchType = FWP_MATCH_EQUAL
        item.conditionValue.type = data_type
        return item

    def _conditions_for(self, grant: Mapping[str, Any], app_condition: Any,
                        user_condition: Any):
        conditions = [app_condition, user_condition]
        backing: list[Any] = []
        for field, cidr in ((FWPM_CONDITION_IP_LOCAL_ADDRESS, grant["local_address"]),
                            (FWPM_CONDITION_IP_REMOTE_ADDRESS, grant["remote_address"])):
            network = ipaddress.ip_network(cidr, strict=True)
            address = native.FWP_V4_ADDR_AND_MASK(int(network.network_address), int(network.netmask))
            backing.append(address)
            item = self._condition(field, FWP_V4_ADDR_MASK)
            item.conditionValue.v4AddrMask = C.cast(C.pointer(address), C.c_void_p)
            conditions.append(item)
        protocol = self._condition("{3971EF2B-623E-4F9A-8CB1-6E79B806B9A7}", FWP_UINT8)
        protocol.conditionValue.uint8 = int(grant["protocol"])
        conditions.append(protocol)
        if grant["local_port"] is not None:
            local_port = self._condition("{0C1BA1AF-5765-453F-AF22-A8F791AC775B}", FWP_UINT16)
            local_port.conditionValue.uint16 = int(grant["local_port"])
            conditions.append(local_port)
        remote_port = self._condition("{C35A604D-D22B-4E1A-91B4-68F674EE674B}", FWP_UINT16)
        remote_port.conditionValue.uint16 = int(grant["remote_port"])
        conditions.append(remote_port)
        return (native.FWPM_FILTER_CONDITION0 * len(conditions))(*conditions), backing

    def _make_filter(self, policy: Any, action: Mapping[str, Any], marker: bytes, conditions: Any):
        filter_obj = native.FWPM_FILTER0()
        filter_key = guid_struct(action["filter_key"])
        provider_key = guid_struct(policy.wfp_provider_key)
        sublayer_key = guid_struct(policy.wfp_sublayer_key)
        grant = action["grant"]
        filter_obj.filterKey = filter_key
        display_name = C.create_unicode_buffer("COMSOL MCP T037 " + action["action_id"])
        filter_obj.displayData.name = C.cast(display_name, C.c_void_p)
        filter_obj.displayData.description = None
        filter_obj.flags = FWPM_FILTER_FLAG_PERSISTENT
        filter_obj.providerKey = C.pointer(provider_key)
        marker_buffer = (native.BYTE * len(marker)).from_buffer_copy(marker)
        filter_obj.providerData.size = len(marker)
        filter_obj.providerData.data = C.cast(marker_buffer, C.POINTER(native.BYTE))
        filter_obj.layerKey = guid_struct(grant["layer_key"])
        filter_obj.subLayerKey = sublayer_key
        filter_obj.weight.type = FWP_EMPTY
        filter_obj.numFilterConditions = len(conditions)
        filter_obj.filterCondition = C.cast(conditions, C.POINTER(native.FWPM_FILTER_CONDITION0))
        filter_obj.action.type = FWP_ACTION_PERMIT
        filter_obj.rawContext = 0
        # Keep every pointer target alive for the duration of Add0/GetByKey0.
        filter_obj._keepalive = (display_name, provider_key, sublayer_key, marker_buffer,
                                 conditions, filter_key)
        return filter_obj

    def _expected_conditions(self, grant: Mapping[str, Any], app_blob: bytes | None,
                             sd_bytes: bytes | None) -> list[dict[str, Any]]:
        app_value = {"type": FWP_BYTE_BLOB_TYPE}
        user_value = {"type": FWP_SECURITY_DESCRIPTOR_TYPE}
        if app_blob is not None:
            app_value.update({"size": len(app_blob), "sha256": hashlib.sha256(app_blob).hexdigest()})
        if sd_bytes is not None:
            user_value.update({"size": len(sd_bytes), "sha256": hashlib.sha256(sd_bytes).hexdigest()})
        values = [(FWPM_CONDITION_ALE_APP_ID, app_value),
                  (FWPM_CONDITION_ALE_USER_ID, user_value)]
        for field, cidr in ((FWPM_CONDITION_IP_LOCAL_ADDRESS, grant["local_address"]),
                            (FWPM_CONDITION_IP_REMOTE_ADDRESS, grant["remote_address"])):
            network = ipaddress.ip_network(cidr, strict=True)
            values.append((field, {"type": FWP_V4_ADDR_MASK,
                                   "address_u32": int(network.network_address),
                                   "mask_u32": int(network.netmask)}))
        values.append((FWPM_CONDITION_IP_PROTOCOL, {"type": FWP_UINT8,
                                                     "value": grant["protocol"]}))
        if grant["local_port"] is not None:
            values.append((FWPM_CONDITION_IP_LOCAL_PORT, {"type": FWP_UINT16,
                                                          "value": grant["local_port"]}))
        values.append((FWPM_CONDITION_IP_REMOTE_PORT, {"type": FWP_UINT16,
                                                        "value": grant["remote_port"]}))
        return [{"field_key": field.upper(), "match_type": FWP_MATCH_EQUAL,
                 "condition_value": value} for field, value in values]

    def _validate_filter_semantics(self, row: Mapping[str, Any], profile: Any,
                                   action: Mapping[str, Any], grant: Mapping[str, Any],
                                   marker: bytes, *, expected_policy: Any | None = None,
                                   expected_app_blob: bytes | None = None,
                                   expected_sd: bytes | None = None) -> None:
        policy = expected_policy if expected_policy is not None else profile.windows_isolation
        if (guid_key(row.get("filter_key")) != guid_key(action["filter_key"])
                or guid_key(row.get("provider_key")) != guid_key(policy.wfp_provider_key)
                or guid_key(row.get("sublayer_key")) != guid_key(policy.wfp_sublayer_key)
                or guid_key(row.get("layer_key")) != guid_key(grant["layer_key"])
                or row.get("flags") != FWPM_FILTER_FLAG_PERSISTENT
                or row.get("action_type") != FWP_ACTION_PERMIT
                or row.get("action_callout_key") is not None
                or row.get("has_provider_context") is not False
                or row.get("context") != {"raw_context": 0}
                or row.get("provider_data_sha256") != hashlib.sha256(marker).hexdigest()
                or row.get("display_data") != {"name": "COMSOL MCP T037 " + action["action_id"],
                                                "description": None}
                or row.get("weight") != {"type": FWP_EMPTY, "value": None}):
            _fail("WFP_FILTER_READBACK_MISMATCH", "WFP filter identity differs from the requested exact resource",
                  stage="execution")
        actual = row.get("conditions")
        if not isinstance(actual, list):
            _fail("WFP_FILTER_READBACK_MISMATCH", "WFP filter conditions are unavailable", stage="execution")
        # Native readback returns hashes for pointer-backed blobs; these are
        # checked by the creation path before this method. Exact key/type/value
        # cardinality is checked here for scalar/address conditions.
        fields = [item.get("field_key", "").upper() for item in actual if isinstance(item, Mapping)]
        expected_rows = self._expected_conditions(grant, expected_app_blob, expected_sd)
        expected_fields = [item["field_key"] for item in expected_rows]
        if len(actual) != len(expected_fields) or sorted(fields) != sorted(expected_fields):
            _fail("WFP_FILTER_READBACK_MISMATCH", "WFP filter has missing, duplicate, or unsupported conditions",
                  stage="execution")
        actual_by_field = {guid_key(item["field_key"]): item["condition_value"]
                           for item in actual if isinstance(item, Mapping)}
        for expected in expected_rows:
            actual_value = actual_by_field.get(guid_key(expected["field_key"]))
            expected_value = expected["condition_value"]
            if expected_value.get("type") in {FWP_BYTE_BLOB_TYPE, FWP_SECURITY_DESCRIPTOR_TYPE}:
                if set(actual_value or {}) != {"type", "size", "sha256"}:
                    _fail("WFP_FILTER_READBACK_MISMATCH", "WFP pointer-backed condition did not read back a bounded blob",
                          stage="execution")
                if (actual_value.get("type") != expected_value.get("type")
                        or ("size" in expected_value and actual_value.get("size") != expected_value["size"])
                        or ("sha256" in expected_value and actual_value.get("sha256") != expected_value["sha256"])):
                    _fail("WFP_FILTER_READBACK_MISMATCH", "WFP application or principal condition differs from its exact input",
                          stage="execution")
            elif actual_value != expected_value:
                _fail("WFP_FILTER_READBACK_MISMATCH", "WFP endpoint/protocol condition differs from its exact input",
                      stage="execution")

    def _owned_marker(self, profile: Any, action: Mapping[str, Any]) -> bytes:
        return _canonical({"schema": "comsol-mcp.t037-owned-wfp-filter/1",
                           "profile_sha256": profile.sha256,
                           "plan_sha256": action["plan_sha256"],
                           "action_id": action["action_id"],
                           "ownership_guid": action["ownership_guid"]})

    def delete_filter(self, *, profile: Any, action: Mapping[str, Any],
                      expected_resource_sha256: str,
                      expected_dependency_sha256: str) -> None:
        def write(engine):
            self._check_dependencies(engine, profile, expected_dependency_sha256)
            current = self._filter_row_or_none(engine, action["filter_key"])
            if current is None or sha256(current) != expected_resource_sha256:
                _fail("WFP_FILTER_DRIFTED", "owned WFP filter changed after restore preflight; it was preserved",
                      stage="execution")
            marker = self._owned_marker(profile, action)
            grant = action["grant"]
            self._validate_filter_semantics(current, profile, action, grant, marker)
            guid = guid_struct(action["filter_key"])
            status = int(self._fwpu.FwpmFilterDeleteByKey0(engine, C.byref(guid)))
            self._check(status, "FwpmFilterDeleteByKey0", code="WFP_FILTER_DELETE_FAILED")
            if self._filter_row_or_none(engine, action["filter_key"]) is not None:
                _fail("WFP_FILTER_DELETE_READBACK_MISMATCH", "WFP filter remained present in its delete transaction",
                      stage="execution")
            return None

        self._session(readonly=False, callback=write)
        if self.read_filter(action["filter_key"]) is not None:
            _fail("WFP_FILTER_DELETE_READBACK_UNKNOWN", "deleted WFP filter remains present after commit",
                  stage="execution")


class _TRUSTEE_W(C.Structure):
    _fields_ = [("pMultipleTrustee", C.c_void_p), ("MultipleTrusteeOperation", C.c_int32),
                ("TrusteeForm", C.c_int32), ("TrusteeType", C.c_int32),
                ("ptstrName", C.c_void_p)]


class _EXPLICIT_ACCESS_W(C.Structure):
    _fields_ = [("grfAccessPermissions", native.DWORD), ("grfAccessMode", C.c_int32),
                ("grfInheritance", native.DWORD), ("Trustee", _TRUSTEE_W)]


__all__ = [
    "CtypesWindowsWfpMutationApi", "WindowsWfpMutationError",
    "FWPM_LAYER_ALE_AUTH_CONNECT_V4", "normalize_endpoint_grant", "sha256",
]
