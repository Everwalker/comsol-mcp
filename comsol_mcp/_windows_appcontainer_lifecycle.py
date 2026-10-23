"""Private same-daemon lifetime for one original AppContainer CREATE/DELETE.

The retained effect authorizes one API-return-only Delete through its original
SDK owner. It does not prove profile removal, storage absence, or generation.
"""
from __future__ import annotations

import hashlib
import json
import ntpath
import os
import sys
import threading
from typing import Any
from uuid import uuid4

from ._windows_service_sid import fail

_PROFILE_EFFECT_SEAL = object()
_RESTORE_SELECTION_SEAL = object()
_RESTORE_ATTEMPT_SEAL = object()


class ProfileRestoreSelection:
    """Result of selecting one exact daemon-rooted original profile effect."""

    __slots__ = ("_seal", "daemon", "request", "record", "executor", "effect",
                 "action", "plan_sha256", "receipt_sha256", "restore_sha256",
                 "restore_body", "expected_resource_sha256")

    def __init__(self, *, daemon: Any, request: Any, record: Any, executor: Any,
                 effect: "ProfileEffect", action: dict[str, Any],
                 plan_sha256: str, receipt_sha256: str, restore_sha256: str,
                 restore_body: dict[str, Any], expected_resource_sha256: str,
                 seal: object):
        if seal is not _RESTORE_SELECTION_SEAL:
            fail("HOST_CHANGE_APPCONTAINER_OWNER_SEAL_REQUIRED",
                 "restore selection can only be issued by the original-effect selector")
        self._seal = seal
        self.daemon, self.request, self.record = daemon, request, record
        self.executor, self.effect, self.action = executor, effect, dict(action)
        self.plan_sha256, self.receipt_sha256 = plan_sha256, receipt_sha256
        self.restore_sha256, self.restore_body = restore_sha256, dict(restore_body)
        self.expected_resource_sha256 = expected_resource_sha256


class ProfileRestoreAttempt:
    """One non-replayable restore request bound before its native SDK entry."""

    __slots__ = ("_seal", "attempt_id", "selection", "admission",
                 "authorization_ref_sha256", "dependency_sha256",
                 "baseline_sha256", "native_attempt", "pause")

    def __init__(self, *, selection: ProfileRestoreSelection, admission: Any,
                 authorization_ref_sha256: str, dependency_sha256: str,
                 baseline_sha256: str, native_attempt: Any, pause: Any,
                 seal: object):
        if seal is not _RESTORE_ATTEMPT_SEAL:
            fail("HOST_CHANGE_APPCONTAINER_OWNER_SEAL_REQUIRED",
                 "restore attempts can only be issued by the original lifecycle")
        self._seal = seal
        self.attempt_id = str(uuid4())
        self.selection, self.admission = selection, admission
        self.authorization_ref_sha256 = authorization_ref_sha256
        self.dependency_sha256 = dependency_sha256
        self.baseline_sha256 = baseline_sha256
        self.native_attempt, self.pause = native_attempt, pause


def _canonical(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=False, allow_nan=False).encode("utf-8")


def _sha(value: Any) -> str:
    return hashlib.sha256(_canonical(value)).hexdigest()


def _namespace(user_sid: str, profile_name: str, appcontainer_sid: str) -> tuple[str, str, str]:
    try:
        normalized_name = profile_name.encode("ascii").decode("ascii").lower()
    except (AttributeError, UnicodeEncodeError):
        fail("HOST_CHANGE_APPCONTAINER_OWNER_BINDING_MISMATCH",
             "profile names in a retained namespace must use the validated ASCII form")
    return (user_sid.casefold(), normalized_name, appcontainer_sid.casefold())


def _same_user_namespace_conflict(existing: "ProfileEffect", candidate: "ProfileEffect") -> bool:
    """Fence either durable identity within one actual user namespace."""
    if existing.user_sid.casefold() != candidate.user_sid.casefold():
        return False
    return (existing.profile_name.encode("ascii").decode("ascii").lower()
            == candidate.profile_name.encode("ascii").decode("ascii").lower()
            or existing.appcontainer_sid.casefold() == candidate.appcontainer_sid.casefold())


class ProfileEffect:
    """Strong private references to the exact pre-CREATE runtime and owners."""

    _IMMUTABLE = frozenset({
        "daemon", "request", "record", "admission", "executor", "api", "runtime",
        "profile", "profile_wrapper", "daemon_profile_wrapper", "loader_origin",
        "profile_path", "profile_source_sha256", "profile_source_identity",
        "provider", "provider_config_sha256", "authority", "subject", "subject_sha256",
        "policy", "profile_name", "appcontainer_sid", "service_user_sid", "pid",
        "birth_filetime_100ns", "user_sid", "namespace", "_seal", "_software_only",
        "_initial_original_call",
    })

    __slots__ = (
        "daemon", "request", "record", "admission", "executor", "api", "runtime",
        "profile", "profile_wrapper", "daemon_profile_wrapper", "loader_origin",
        "profile_path", "profile_source_sha256", "profile_source_identity",
        "provider", "provider_config_sha256", "authority", "subject", "subject_sha256",
        "policy", "profile_name", "appcontainer_sid", "service_user_sid", "pid",
        "birth_filetime_100ns", "user_sid", "namespace", "_software_only",
        "_seal", "_original_call", "_create_entered", "_hresult", "_sid_bytes",
        "_sid_text", "_store_result", "_root_key", "_last_reconcile",
        "_initial_original_call", "_native_attempt", "_original_userenv",
        "_apply_result_sha256", "_apply_receipt_sha256", "_apply_job_id",
        "_apply_admission_id", "_restore_attempt",
    )

    def __setattr__(self, name, value):
        if name in self._IMMUTABLE and hasattr(self, name):
            raise AttributeError("original ProfileEffect bindings are immutable")
        object.__setattr__(self, name, value)

    def __init__(self, *, daemon: Any, request: Any, record: Any, admission: Any,
                 executor: Any, runtime: Any, profile: Any, profile_path: Any,
                 profile_wrapper: Any, daemon_profile_wrapper: Any, loader_origin: Any,
                 profile_source_sha256: Any, profile_source_identity: Any,
                 provider: Any, authority: Any, subject: Any, user_sid: str,
                 pid: int, birth_filetime_100ns: int, software_only: bool,
                 seal: object):
        if seal is not _PROFILE_EFFECT_SEAL:
            fail("HOST_CHANGE_APPCONTAINER_OWNER_SEAL_REQUIRED",
                 "profile lifetime owner can only be issued by its private lifecycle")
        if type(software_only) is not bool:
            fail("HOST_CHANGE_APPCONTAINER_OWNER_BINDING_MISMATCH",
                 "profile effect classification must come from the private constructor")
        policy = profile.windows_isolation
        self.daemon, self.request, self.record, self.admission = daemon, request, record, admission
        self.executor, self.api, self.runtime = executor, executor.api, runtime
        self.profile = profile
        self.profile_wrapper = profile_wrapper
        self.daemon_profile_wrapper = daemon_profile_wrapper
        self.loader_origin = loader_origin
        self.profile_path = profile_path
        self.profile_source_sha256 = profile_source_sha256
        self.profile_source_identity = profile_source_identity
        self.provider, self.authority = provider, authority
        self.provider_config_sha256 = getattr(provider, "config_sha256", None)
        self.subject, self.subject_sha256 = subject, _sha(subject)
        self.policy = policy
        self.profile_name = policy.appcontainer_profile_name
        self.appcontainer_sid = policy.appcontainer_sid
        self.service_user_sid = policy.service_logon_sid
        self.user_sid, self.pid, self.birth_filetime_100ns = user_sid, pid, birth_filetime_100ns
        self.namespace = _namespace(user_sid, self.profile_name, self.appcontainer_sid)
        self._software_only = bool(software_only)
        self._seal = seal
        self._original_call = None
        self._create_entered = False
        self._hresult = None
        self._sid_bytes = None
        self._sid_text = None
        self._store_result = None
        self._root_key = None
        self._last_reconcile = None
        self._initial_original_call = getattr(self.api, "_appcontainer_original_call", None)
        self._native_attempt = None
        self._original_userenv = None
        self._apply_result_sha256 = None
        self._apply_receipt_sha256 = None
        self._apply_job_id = None
        self._apply_admission_id = None
        self._restore_attempt = None

    @property
    def software_only(self) -> bool:
        return self._software_only

    @property
    def evidence_classification(self) -> str:
        return "SOFTWARE_ONLY" if self._software_only else "PRIVATE_NATIVE_OWNER_BINDING"

    def _is_rooted(self) -> bool:
        roots = getattr(self.daemon, "_retained_appcontainer_profile_effects", None)
        return (self._seal is _PROFILE_EFFECT_SEAL and isinstance(roots, dict)
                and self._root_key is not None and roots.get(self._root_key) is self)

    def require_rooted(self, api: Any | None = None) -> None:
        if not self._is_rooted() or (api is not None and api is not self.api):
            fail("HOST_CHANGE_APPCONTAINER_OWNER_UNKNOWN",
                 "the exact daemon-rooted original profile effect is unavailable")

    def bind_original_call(self, api: Any, original_call: Any) -> None:
        self.require_rooted(api)
        if (self._original_call is not None or api._appcontainer_original_call is not original_call
                or type(original_call) is not tuple or len(original_call) < 7
                or not isinstance(original_call[0], dict)
                or not callable(original_call[0].get("CreateAppContainerProfile"))
                or not callable(original_call[0].get("FreeSid"))
                or not callable(original_call[5]) or not callable(original_call[6])
                or self._original_userenv is not None
                or getattr(api, "_appcontainer_userenv", None) is None):
            fail("HOST_CHANGE_APPCONTAINER_OWNER_UNKNOWN",
                 "the exact original SDK call and closer lifetime could not be retained")
        self._original_call = original_call
        self._original_userenv = api._appcontainer_userenv

    def bind_native_attempt(self, api: Any, attempt: Any) -> None:
        self.require_rooted(api)
        from ._windows_appcontainer_native import _NativeCreateAttempt
        if (type(attempt) is not _NativeCreateAttempt or self._native_attempt is not None
                or self._initial_original_call is not None
                or getattr(api, "_appcontainer_original_call", None) is not self._initial_original_call):
            fail("HOST_CHANGE_APPCONTAINER_OWNER_UNKNOWN",
                 "the original native CREATE attempt cannot bind to this profile effect")
        self._native_attempt = attempt

    def mark_create_entered(self, api: Any, original_call: Any) -> None:
        self.require_rooted(api)
        if (original_call is not self._original_call or api._appcontainer_original_call is not original_call
                or self._create_entered or original_call[6]() is not True):
            fail("HOST_CHANGE_APPCONTAINER_OWNER_UNKNOWN",
                 "the original CREATE call entry does not match its retained owner")
        self._create_entered = True

    def capture_hresult(self, api: Any, original_call: Any, value: Any) -> None:
        self.require_rooted(api)
        if (original_call is not self._original_call or api._appcontainer_original_call is not original_call
                or not self._create_entered or type(value) is not int
                or not -(2**31) <= value < 2**31 or self._hresult is not None):
            fail("HOST_CHANGE_APPCONTAINER_HRESULT_UNKNOWN",
                 "the original signed HRESULT was not captured exactly once")
        self._hresult = value

    def capture_validated_sid(self, api: Any, original_call: Any,
                              raw_sid: bytes, sid_text: str) -> None:
        self.require_rooted(api)
        expected_text = None
        if type(raw_sid) is bytes and 8 <= len(raw_sid) <= 68 and raw_sid[0] == 1 and raw_sid[1] <= 15 and len(raw_sid) == 8 + 4 * raw_sid[1]:
            expected_text = "S-1-" + str(int.from_bytes(raw_sid[2:8], "big")) + "".join(
                "-" + str(int.from_bytes(raw_sid[index:index + 4], "little"))
                for index in range(8, len(raw_sid), 4))
        if (original_call is not self._original_call or api._appcontainer_original_call is not original_call
                or self._hresult != 0 or type(raw_sid) is not bytes or not isinstance(sid_text, str)
                or not 8 <= len(raw_sid) <= 68 or raw_sid[0] != 1 or raw_sid[1] > 15
                or len(raw_sid) != 8 + 4 * raw_sid[1]
                or sid_text.casefold() != expected_text.casefold()
                or self._sid_bytes is not None):
            fail("HOST_CHANGE_APPCONTAINER_SID_UNKNOWN",
                 "validated original SID bytes are unavailable or differ from the trusted profile")
        self._sid_bytes = bytes(raw_sid)
        self._sid_text = sid_text

    def record_store_success(self, admission: Any, store: Any,
                             accepted: Any, status: Any, receipt: Any) -> None:
        self.require_rooted(self.api)
        if (admission is not self.admission or store is not self.executor.store
                or accepted is not True or status != "SUCCEEDED"
                or self._store_result is not None
                or self._hresult != 0 or self._sid_bytes is None
                or self._sid_text.casefold() != self.appcontainer_sid.casefold()):
            fail("HOST_CHANGE_APPCONTAINER_RESULT_PERSISTENCE_UNKNOWN",
                 "the exact original CREATE operation result did not persist successfully")
        from ._w26_os_resources import AppliedHostChangeReceipt, _sha256
        from ._windows_host_change import _receipt_payload_v2
        if (not isinstance(receipt, AppliedHostChangeReceipt)
                or receipt.plan_sha256 != self.request.plan.sha256
                or receipt.host_id != self.profile.host_id
                or receipt.platform != "windows"
                or len(receipt.actions) != 1
                or receipt.actions[0].get("resource_kind") != "appcontainer_profile"
                or receipt.actions[0].get("resource_key") != self.request.plan.as_dict()["change_manifest"][0]["ownership_guid"]):
            fail("HOST_CHANGE_APPCONTAINER_RESULT_PERSISTENCE_UNKNOWN",
                 "the persisted AppContainer receipt does not bind the exact original CREATE plan")
        operation = store.get_operation(admission.operation_id)
        job = store.operation_job(admission.operation_id)
        if (not isinstance(operation, dict) or operation.get("status") != "SUCCEEDED"
                or not isinstance(job, dict) or job.get("job_id") != admission.job_id
                or job.get("status") != "SUCCEEDED"
                or store.host_control_admission_state(admission.admission_id) != "APPLIED"):
            fail("HOST_CHANGE_APPCONTAINER_RESULT_PERSISTENCE_UNKNOWN",
                 "the original CREATE result, job, and journal state did not durably agree")
        result = operation.get("result")
        expected_receipt = {
            **_receipt_payload_v2(self.request.plan, self.profile, tuple(receipt.actions)),
            "receipt_sha256": receipt.receipt_sha256,
        }
        if (not isinstance(result, dict)
                or result.get("execution_state") != "SUCCEEDED"
                or result.get("receipt_sha256") != receipt.receipt_sha256
                or result.get("applied_receipt") != expected_receipt):
            fail("HOST_CHANGE_APPCONTAINER_RESULT_PERSISTENCE_UNKNOWN",
                 "the exact successful CREATE receipt was not durably read back")
        self._store_result = (store, admission.operation_id, status)
        self._apply_result_sha256 = _sha256(result)
        self._apply_receipt_sha256 = receipt.receipt_sha256
        self._apply_job_id = admission.job_id
        self._apply_admission_id = admission.admission_id

    def bind_restore_attempt(self, api: Any, attempt: ProfileRestoreAttempt) -> None:
        self.require_rooted(api)
        if (type(attempt) is not ProfileRestoreAttempt
                or attempt._seal is not _RESTORE_ATTEMPT_SEAL
                or attempt.selection.effect is not self
                # RESTORE's executor is freshly loaded and necessarily owns a
                # different adapter.  The Delete attempt must remain bound to
                # the original CREATE effect's API, which is the owner retained
                # by this ProfileEffect.
                or attempt.selection.effect.api is not api
                or self._restore_attempt is not None
                or attempt.native_attempt.effect is not self
                or attempt.native_attempt.api is not api):
            fail("HOST_CHANGE_APPCONTAINER_OWNER_UNKNOWN",
                 "the original profile effect cannot bind this restore attempt")
        self._restore_attempt = attempt

    def reconcile_original_native_resources(self, request: Any, executor: Any) -> str:
        """Validate the exact original SDK owners while keeping successful CREATE rooted."""
        self.require_rooted(self.api)
        if request is not self.request or executor is not self.executor:
            fail("HOST_CONTROL_OWNER_UNKNOWN", "original profile-effect request and executor are required")
        original_call = self._original_call
        from ._windows_owned_resources import registry_for
        registry = registry_for(self.api)
        from ._windows_service_sid import original_preflights_terminal
        if (registry.owner is not self.api or registry.unresolved()
                or not original_preflights_terminal(self.api)):
            fail("HOST_CONTROL_OWNER_UNKNOWN", "original AppContainer SDK allocations remain unresolved")
        if original_call is None:
            attempt = self._native_attempt
            if (attempt is None or not attempt.finished or attempt.entered
                    or getattr(self.api, "_appcontainer_original_call", None) is not self._initial_original_call):
                fail("HOST_CONTROL_OWNER_UNKNOWN", "original native CREATE dispatch state is unresolved")
            self._last_reconcile = ("SOFTWARE_ONLY_CREATE_NOT_ENTERED" if self.software_only
                                    else "ORIGINAL_CREATE_NOT_ENTERED")
            roots = self.daemon._retained_appcontainer_profile_effects
            with self.daemon._appcontainer_profile_effect_lock:
                if roots.get(self._root_key) is self:
                    roots.pop(self._root_key)
            return self._last_reconcile
        if (self.api._appcontainer_original_call is not original_call
                or original_call[6]() is not self._create_entered
                or original_call[5]() is not True):
            fail("HOST_CONTROL_OWNER_UNKNOWN", "original AppContainer SDK call is not terminal")
        if not self._create_entered:
            self._last_reconcile = ("SOFTWARE_ONLY_CREATE_NOT_ENTERED" if self.software_only
                                    else "ORIGINAL_CREATE_NOT_ENTERED")
            roots = self.daemon._retained_appcontainer_profile_effects
            with self.daemon._appcontainer_profile_effect_lock:
                if roots.get(self._root_key) is self:
                    roots.pop(self._root_key)
            return self._last_reconcile
        if self.software_only:
            self._last_reconcile = "SOFTWARE_ONLY"
            return self._last_reconcile
        if self._hresult == 0 and self._sid_bytes is None:
            fail("HOST_CONTROL_OWNER_UNKNOWN", "successful original CREATE lacks copied validated SID bytes")
        self._last_reconcile = "ORIGINAL_EFFECT_RETAINED"
        return self._last_reconcile


def _binding_tuple(daemon: Any, runtime: Any, profile_wrapper: Any,
                   provider: Any, authority: Any) -> tuple[Any, ...]:
    from ._windows_t037_broker import _require_original_runtime
    _require_original_runtime(runtime)
    binding = getattr(daemon, "_windows_t037_scm_binding", None)
    if (type(binding) is not tuple or len(binding) != 10
            or binding[0] is not runtime or binding[1] is not daemon
            or binding[2] is not profile_wrapper or binding[3] is not provider
            or binding[4] is not authority
            or getattr(daemon, "_windows_t037_scm_runtime", None) is not runtime
            or runtime._daemon_binding is not binding
            or authority.authorization_verifier is not provider or provider._closed
            or getattr(daemon, "_closing", True) is not False
            or binding[5] != daemon._windows_t037_profile_path
            or binding[6] != profile_wrapper.source_sha256
            or binding[7] != dict(profile_wrapper.source_identity)
            or binding[8] != profile_wrapper.sha256
            or binding[9] != provider.config_sha256):
        fail("HOST_CHANGE_APPCONTAINER_SCM_BINDING_UNKNOWN",
             "the exact original running SCM daemon/profile/provider binding is unavailable")
    return binding


def _native_process_identity(pid: int, service_user_sid: str,
                             expected_image: str) -> tuple[int, str]:
    if os.name != "nt" or sys.platform not in {"win32", "nt"}:
        fail("HOST_CHANGE_APPCONTAINER_OWNER_SOURCE_UNAVAILABLE",
             "native AppContainer ownership requires Windows process/token observers")
    from ._windows_t037 import WindowsT037Observer, _Win32ReadOnlyApi
    from ._windows_owned_resources import registry_for
    observer = WindowsT037Observer(platform_name="windows")
    if type(observer.api) is not _Win32ReadOnlyApi:
        fail("HOST_CHANGE_APPCONTAINER_OWNER_SOURCE_UNAVAILABLE",
             "the exact native process/token observer is unavailable")
    try:
        row = observer.observe_process(pid)
        required = {"pid", "birth_filetime_100ns", "exit_filetime_100ns", "image_path", "token",
                    "job_membership_any", "job_membership_exact", "job", "observation_state"}
        token = row.get("token") if isinstance(row, dict) else None
        token_fields = {"state", "user_sid", "group_sids", "logon_sids", "is_appcontainer",
                        "appcontainer_sid", "capability_sids", "integrity_sid", "integrity_attributes"}
        if (not isinstance(row, dict) or set(row) != required or row.get("pid") != pid
                or type(row.get("birth_filetime_100ns")) is not int or row["birth_filetime_100ns"] <= 0
                or row.get("exit_filetime_100ns") is not None
                or row.get("observation_state") != "OBSERVED"
                or type(row.get("job_membership_any")) is not bool
                or row.get("job_membership_exact") is not None or row.get("job") is not None
                or not isinstance(token, dict) or set(token) != token_fields
                or token.get("state") != "OBSERVED"
                or not isinstance(token.get("user_sid"), str)
                or token["user_sid"].casefold() != service_user_sid.casefold()
                or not isinstance(token.get("group_sids"), list)
                or any(not isinstance(item, str) for item in token["group_sids"])
                or not isinstance(token.get("logon_sids"), list)
                or any(not isinstance(item, str) for item in token["logon_sids"])
                or token.get("is_appcontainer") is not False or token.get("appcontainer_sid") is not None
                or not isinstance(token.get("capability_sids"), list)
                or any(not isinstance(item, str) for item in token["capability_sids"])
                or not isinstance(token.get("integrity_sid"), str)
                or type(token.get("integrity_attributes")) is not int
                or not isinstance(row.get("image_path"), str)
                or ntpath.normcase(ntpath.normpath(row["image_path"]))
                != ntpath.normcase(ntpath.normpath(expected_image))):
            fail("HOST_CHANGE_APPCONTAINER_OWNER_SOURCE_MISMATCH",
                 "actual SCM process PID/birth/user/image did not match the exact configured subject")
        birth = row["birth_filetime_100ns"]
        process_birth = birth // 10_000 - 11_644_473_600_000
        if process_birth <= 0:
            fail("HOST_CHANGE_APPCONTAINER_OWNER_SOURCE_MISMATCH",
                 "native SCM process birth is outside the supported epoch")
        from ._platform_process import process_identity
        observed = process_identity(pid, platform_name="nt")
        if observed != {"alive": True, "start_epoch_ms": process_birth}:
            fail("HOST_CHANGE_APPCONTAINER_OWNER_SOURCE_MISMATCH",
                 "independent native PID/birth observation differs from the SCM process handle")
        registry = registry_for(observer.api)
        if registry.unresolved():
            fail("HOST_CHANGE_APPCONTAINER_OWNER_SOURCE_UNKNOWN",
                 "native SCM process/token observation left original handles unresolved")
        return birth, token["user_sid"]
    finally:
        registry = registry_for(observer.api)
        if registry.unresolved():
            fail("HOST_CHANGE_APPCONTAINER_OWNER_SOURCE_UNKNOWN",
                 "native SCM process/token observer failed to release its original handles")


def create_native_profile_effect(*, daemon: Any, request: Any, record: Any,
                                 executor: Any, admission: Any, action: Any,
                                 subject: Any) -> ProfileEffect:
    """Build a source/SCM-bound effect from live native process and service observations."""
    from ._control_daemon import ControlDaemon
    from ._host_scoped_provider import HostScopedProvider
    from ._windows_t037 import WindowsT037ProfileV2, load_trusted_profile
    from ._windows_t037_broker import InternalHostControlRequest, _HOST_CONTROL_REQUEST_SEAL
    from ._windows_t037_wfp import CtypesWindowsWfpMutationApi
    from ._windows_service_sid import check_subject
    from ._windows_host_change import (WindowsHostChangeExecutor, _ExecutionAdmission,
        _TrustedProfileLoadOrigin, _NATIVE_PROFILE_LOADER_TOKEN, _normalize_supported_actions)
    from ._project_authority import ProjectAuthority

    if type(daemon) is not ControlDaemon or type(executor) is not WindowsHostChangeExecutor:
        fail("HOST_CHANGE_APPCONTAINER_OWNER_BINDING_MISMATCH",
             "the exact ControlDaemon and native host executor are required")
    daemon_wrapper = getattr(daemon, "_windows_t037_profile", None)
    origin = getattr(executor, "_trusted_profile_load_origin", None)
    if (type(daemon_wrapper) is not WindowsT037ProfileV2
            or type(origin) is not _TrustedProfileLoadOrigin
            or origin._seal is not _NATIVE_PROFILE_LOADER_TOKEN
            or executor._trusted_profile_load_origin is not origin
            or origin.profile_wrapper is daemon_wrapper
            or not origin.matches(origin.profile_wrapper, daemon._windows_t037_profile_path)):
        fail("HOST_CHANGE_APPCONTAINER_PROFILE_LOAD_ORIGIN_REQUIRED",
             "the exact fresh wrapper from the normal trusted loader is required")
    loaded_wrapper = origin.profile_wrapper

    if (type(daemon) is not ControlDaemon or type(executor) is not WindowsHostChangeExecutor
            or type(executor.api) is not CtypesWindowsWfpMutationApi
            or executor.software_only is not False or executor._software_test_seal is not None
            or type(request) is not InternalHostControlRequest
            or type(record) is not dict or type(admission) is not _ExecutionAdmission
            or type(subject) is not dict or type(request.authority) is not ProjectAuthority
            or type(request.provider) is not HostScopedProvider
            or request._seal is not _HOST_CONTROL_REQUEST_SEAL or request._used is not True
            or request.phase != "APPLY" or request.runtime is not daemon._windows_t037_scm_runtime
            or request.authority is not daemon.project_authority
            or request.provider is not daemon._scoped_host_provider
            or request.project_id != daemon_wrapper.host_resource_profile.project_id
            or executor.profile is not loaded_wrapper.host_resource_profile
            or executor.profile is daemon_wrapper.host_resource_profile
            or executor.provider is not request.provider or executor.authority is not request.authority
            or record.get("request") is not request or record.get("executor") is not executor
            or daemon._retained_host_control_executors.get(record.get("key")) is not record):
        fail("HOST_CHANGE_APPCONTAINER_OWNER_BINDING_MISMATCH",
             "the exact original SCM-issued request/executor/API owner is required before CREATE")

    wrapper = daemon_wrapper
    binding = _binding_tuple(daemon, request.runtime, wrapper,
                             daemon._scoped_host_provider, daemon.project_authority)
    current = load_trusted_profile(daemon._windows_t037_profile_path)
    from . import _w26_os_resources as resources

    def wrapper_matches_pinned(candidate: Any) -> bool:
        if type(candidate) is not WindowsT037ProfileV2:
            return False
        profile = candidate.host_resource_profile
        return (
            candidate.source_sha256 == binding[6]
            and dict(candidate.source_identity) == binding[7]
            and candidate.sha256 == binding[8]
            and candidate.security_semantics == wrapper.security_semantics
            and candidate.wrapper_schema == wrapper.wrapper_schema
            and profile.sha256 == wrapper.host_resource_profile.sha256
            and resources._sha256(profile.windows_isolation.as_dict())
                == resources._sha256(wrapper.host_resource_profile.windows_isolation.as_dict())
            and profile.windows_isolation.host_fingerprint_sha256
                == wrapper.host_resource_profile.windows_isolation.host_fingerprint_sha256
        )

    if (not wrapper_matches_pinned(loaded_wrapper)
            or not wrapper_matches_pinned(current)
            or not origin.matches(loaded_wrapper, binding[5])
            or origin.profile_path != binding[5]
            or current.source_sha256 != loaded_wrapper.source_sha256
            or dict(current.source_identity) != dict(loaded_wrapper.source_identity)
            or current.sha256 != loaded_wrapper.sha256
            or current.host_resource_profile.sha256 != loaded_wrapper.host_resource_profile.sha256):
        fail("HOST_CHANGE_APPCONTAINER_PROFILE_SOURCE_CHANGED",
             "fresh loaded profile, daemon-pinned source, or current protected file differs before CREATE")

    profile = loaded_wrapper.host_resource_profile
    policy = profile.windows_isolation
    normalized = _normalize_supported_actions(request.plan, profile)[1]
    if (request.profile_sha256 != profile.sha256
            or request.profile_source_sha256 != wrapper.source_sha256
            or admission.operation_id == "" or admission.job_id == ""
            or len(normalized) != 1 or _canonical(normalized[0]) != _canonical(action)
            or not isinstance(action, dict)
            or action.get("resource_kind") != "appcontainer_profile"
            or action.get("operation") != "CREATE"
            or action.get("resource_name") != policy.appcontainer_profile_name
            or subject.get("status", {}).get("process_id") != os.getpid()):
        fail("HOST_CHANGE_APPCONTAINER_OWNER_BINDING_MISMATCH",
             "the original admission, CREATE action, service subject, or SCM process differs")

    check_subject(subject, profile, os.getpid())
    launcher = [image for image in wrapper.launch_images if image.role == "launcher"]
    if len(launcher) != 1:
        fail("HOST_CHANGE_APPCONTAINER_OWNER_SOURCE_MISMATCH",
             "exactly one pinned SCM launcher image is required")
    executor.api._appcontainer_no_impersonation()
    if getattr(executor.api, "_appcontainer_original_call", None) is not None:
        fail("HOST_CHANGE_APPCONTAINER_OWNER_UNKNOWN",
             "an original SDK CREATE call is already attached to this API")
    birth, user_sid = _native_process_identity(os.getpid(), policy.service_logon_sid, launcher[0].path)
    return ProfileEffect(
        daemon=daemon, request=request, record=record, admission=admission,
        executor=executor, runtime=request.runtime, profile=profile,
        profile_wrapper=loaded_wrapper, daemon_profile_wrapper=wrapper,
        loader_origin=origin,
        profile_path=daemon._windows_t037_profile_path,
        profile_source_sha256=loaded_wrapper.source_sha256,
        profile_source_identity=dict(loaded_wrapper.source_identity), provider=request.provider,
        authority=request.authority, subject=subject, user_sid=user_sid,
        pid=os.getpid(), birth_filetime_100ns=birth, software_only=False,
        seal=_PROFILE_EFFECT_SEAL,
    )


def create_software_test_profile_effect(*, daemon: Any, request: Any,
                                        record: Any, executor: Any,
                                        admission: Any, action: Any,
                                        subject: Any, test_token: object) -> ProfileEffect:
    """Explicit fixture-only owner; it is never accepted by the native producer."""
    from ._windows_host_change import _SOFTWARE_TEST_TOKEN
    from ._windows_t037_broker import InternalHostControlRequest
    if (test_token is not _SOFTWARE_TEST_TOKEN or not executor.software_only
            or executor._software_test_seal is not _SOFTWARE_TEST_TOKEN
            or type(request) is not InternalHostControlRequest
            or record.get("request") is not request or record.get("executor") is not executor
            or not isinstance(action, dict) or action.get("operation") != "CREATE"):
        fail("HOST_CHANGE_APPCONTAINER_TEST_OWNER_REQUIRED",
             "only the explicit sealed software test path can create a SOFTWARE_ONLY effect")
    policy = executor.profile.windows_isolation
    return ProfileEffect(
        daemon=daemon, request=request, record=record, admission=admission,
        executor=executor, runtime=request.runtime, profile=executor.profile,
        profile_wrapper=None, daemon_profile_wrapper=getattr(daemon, "_windows_t037_profile", None),
        loader_origin=None,
        profile_path=None, profile_source_sha256=None, profile_source_identity=None,
        provider=request.provider, authority=request.authority, subject=subject,
        user_sid=policy.service_logon_sid, pid=os.getpid(), birth_filetime_100ns=0,
        software_only=True, seal=_PROFILE_EFFECT_SEAL,
    )


def root_profile_effect(daemon: Any, effect: ProfileEffect) -> ProfileEffect:
    if type(effect) is not ProfileEffect or effect.daemon is not daemon:
        fail("HOST_CHANGE_APPCONTAINER_OWNER_BINDING_MISMATCH",
             "only the exact private ProfileEffect may enter the daemon root")
    lock = getattr(daemon, "_appcontainer_profile_effect_lock", None)
    if lock is None:
        lock = threading.RLock()
        daemon._appcontainer_profile_effect_lock = lock
    roots = getattr(daemon, "_retained_appcontainer_profile_effects", None)
    if roots is None:
        roots = {}
        daemon._retained_appcontainer_profile_effects = roots
    if not isinstance(roots, dict):
        fail("HOST_CHANGE_APPCONTAINER_OWNER_UNKNOWN", "daemon profile-effect root is malformed")
    with lock:
        if any(_same_user_namespace_conflict(existing, effect) for existing in roots.values()):
            fail("HOST_CHANGE_APPCONTAINER_NAMESPACE_BUSY",
                 "an original live or uncertain profile effect already owns this user's profile name or AppContainer SID")
        key = id(effect)
        if key in roots:
            fail("HOST_CHANGE_APPCONTAINER_OWNER_UNKNOWN", "profile effect was already rooted")
        roots[key] = effect
        effect._root_key = key
        if roots.get(key) is not effect:
            fail("HOST_CHANGE_APPCONTAINER_OWNER_UNKNOWN", "daemon failed to retain the exact original profile effect")
    return effect


def _expected_created_resource(profile: Any) -> dict[str, Any]:
    policy = profile.windows_isolation
    return {
        "profile_name": policy.appcontainer_profile_name,
        "appcontainer_sid": policy.appcontainer_sid,
        "service_user_sid": policy.service_logon_sid,
        "creation_result": "S_OK",
        "capabilities_state": "NOT_READ_BACK",
        "generation_state": "UNAVAILABLE",
    }


def _restore_selection_digest(plan: Any, profile: Any, receipt: Any,
                              action: dict[str, Any]) -> tuple[dict[str, Any], str, str]:
    from . import _w26_os_resources as resources
    from ._windows_host_change import _verify_sequence_receipt
    _payload, actions, created_hashes = _verify_sequence_receipt(plan, profile, receipt)
    if (len(actions) != 1 or actions[0].get("resource_kind") != "appcontainer_profile"
            or actions[0].get("operation") != "CREATE"
            or _canonical(actions[0]) != _canonical(action)
            or action.get("resource_key") != action.get("ownership_guid")
            or set(created_hashes) != {action.get("action_id")}):
        fail("HOST_CHANGE_APPCONTAINER_RECEIPT_MISMATCH",
             "restore requires the sole exact original AppContainer CREATE receipt")
    expected_resource_sha256 = resources._sha256(_expected_created_resource(profile))
    if created_hashes[action["action_id"]] != expected_resource_sha256:
        fail("HOST_CHANGE_APPCONTAINER_RECEIPT_MISMATCH",
             "the CREATE receipt differs from the deterministic original API-return record")
    body = {
        "schema": "comsol-mcp.appcontainer-profile-restore/1",
        "source_plan_sha256": plan.sha256,
        "source_receipt_sha256": receipt.receipt_sha256,
        "profile_sha256": profile.sha256,
        "resource_kind": "appcontainer_profile",
        "resource_key": action["resource_key"],
        "expected_created_resource_sha256": expected_resource_sha256,
        "operation": "DELETE",
        "acceptance_target": "NATIVE_API_RETURN_ONLY",
    }
    return body, resources._sha256(body), expected_resource_sha256


def _validate_original_apply_store(effect: ProfileEffect, receipt: Any) -> None:
    from . import _w26_os_resources as resources
    if (effect._store_result is None or effect._store_result[0] is not effect.executor.store
            or effect._store_result[1] != effect.admission.operation_id
            or effect._store_result[2] != "SUCCEEDED"
            or effect._apply_admission_id != effect.admission.admission_id
            or effect._apply_job_id != effect.admission.job_id
            or effect._apply_receipt_sha256 != receipt.receipt_sha256
            or effect._apply_result_sha256 is None):
        fail("HOST_CHANGE_APPCONTAINER_APPLY_RESULT_UNKNOWN",
             "the exact original durable CREATE result is not retained")
    store = effect.executor.store
    operation = store.get_operation(effect.admission.operation_id)
    job = store.operation_job(effect.admission.operation_id)
    if (not isinstance(operation, dict) or operation.get("status") != "SUCCEEDED"
            or resources._sha256(operation.get("result")) != effect._apply_result_sha256
            or not isinstance(job, dict) or job.get("job_id") != effect.admission.job_id
            or job.get("status") != "SUCCEEDED"
            or store.host_control_admission_state(effect.admission.admission_id) != "APPLIED"):
        fail("HOST_CHANGE_APPCONTAINER_APPLY_RESULT_UNKNOWN",
             "the original CREATE operation, job, or terminal journal no longer matches")
    result = operation.get("result")
    if (not isinstance(result, dict) or result.get("receipt_sha256") != receipt.receipt_sha256
            or result.get("applied_receipt", {}).get("receipt_sha256") != receipt.receipt_sha256
            or result.get("execution_state") != "SUCCEEDED"):
        fail("HOST_CHANGE_APPCONTAINER_APPLY_RESULT_UNKNOWN",
             "the durable original CREATE receipt differs from the selected restore receipt")


def _validate_original_effect_for_restore(effect: ProfileEffect, *, daemon: Any,
                                          request: Any, executor: Any,
                                          action: dict[str, Any], receipt: Any) -> None:
    from ._windows_owned_resources import registry_for
    from ._windows_service_sid import original_preflights_terminal
    effect.require_rooted(effect.api)
    source_binding_matches = (
        effect.request.profile_source_sha256 == request.profile_source_sha256
        and (
            (effect.software_only and effect.profile_source_sha256 is None)
            or (not effect.software_only
                and effect.profile_source_sha256 == request.profile_source_sha256)
        )
    )
    if (effect.daemon is not daemon or effect.runtime is not request.runtime
            or effect.request.phase != "APPLY"
            or effect.request.runtime is not request.runtime
            or effect.request.authority is not request.authority
            or effect.request.provider is not request.provider
            or effect.request.project_id != request.project_id
            or effect.profile.sha256 != executor.profile.sha256
            or not source_binding_matches
            or effect.profile.windows_isolation is None
            or effect.policy.appcontainer_profile_name != action.get("resource_name")
            or effect.profile.project_id != request.project_id
            or effect.provider is not request.provider
            or effect.authority is not request.authority
            or effect.api is not effect.executor.api
            or effect.api._appcontainer_original_call is not effect._original_call
            or effect._original_call is None
            or effect._original_userenv is None
            or getattr(effect.api, "_appcontainer_userenv", None) is not effect._original_userenv
            or effect._hresult != 0 or not effect._create_entered
            or effect._sid_bytes is None
            or effect._sid_text is None
            or effect._sid_text.casefold() != effect.appcontainer_sid.casefold()
            or effect._native_attempt is None
            or not effect._native_attempt.entered or not effect._native_attempt.finished
            or effect._original_call[3].get("result") != 0
            or effect._original_call[5]() is not True
            or effect._original_call[6]() is not True
            or not original_preflights_terminal(effect.api)
            or registry_for(effect.api).owner is not effect.api
            or registry_for(effect.api).unresolved()):
        fail("HOST_CHANGE_APPCONTAINER_ORIGINAL_EFFECT_UNKNOWN",
             "original API, copied SID, process/runtime, or owned native closures are not terminal")
    _validate_original_apply_store(effect, receipt)
    if effect.software_only:
        from ._windows_host_change import _SOFTWARE_TEST_TOKEN
        if (executor.software_only is not True
                or executor._software_test_seal is not _SOFTWARE_TEST_TOKEN
                or effect.executor._software_test_seal is not _SOFTWARE_TEST_TOKEN
                or effect.pid != os.getpid()
                or effect.user_sid.casefold() != effect.service_user_sid.casefold()):
            fail("HOST_CHANGE_APPCONTAINER_TEST_OWNER_REQUIRED",
                 "software-only restore requires the exact same-process sealed fixture owner")
        return

    from ._control_daemon import ControlDaemon
    from ._host_scoped_provider import HostScopedProvider
    from ._windows_t037 import WindowsT037ProfileV2, load_trusted_profile
    from ._windows_t037_broker import InternalHostControlRequest, _HOST_CONTROL_REQUEST_SEAL
    from ._windows_t037_wfp import CtypesWindowsWfpMutationApi
    from ._windows_host_change import (
        WindowsHostChangeExecutor, _TrustedProfileLoadOrigin,
        _NATIVE_PROFILE_LOADER_TOKEN, _current_windows_binding,
    )
    from ._project_authority import ProjectAuthority
    if (type(daemon) is not ControlDaemon or type(request) is not InternalHostControlRequest
            or request._seal is not _HOST_CONTROL_REQUEST_SEAL or request._used is not True
            or request.phase != "RESTORE"
            or type(executor) is not WindowsHostChangeExecutor
            or executor.software_only is not False
            or executor._software_test_seal is not None
            or type(executor.api) is not CtypesWindowsWfpMutationApi
            or type(request.authority) is not ProjectAuthority
            or type(request.provider) is not HostScopedProvider
            or type(daemon._windows_t037_profile) is not WindowsT037ProfileV2
            or effect.software_only
            or executor.profile is effect.profile
            or executor.provider is not request.provider
            or executor.authority is not request.authority
            or request.authority is not daemon.project_authority
            or request.provider is not daemon._scoped_host_provider):
        fail("HOST_CHANGE_APPCONTAINER_OWNER_BINDING_MISMATCH",
             "restore must use the original daemon/runtime and fresh normal trusted-loader executor")
    wrapper = daemon._windows_t037_profile
    origin = executor._trusted_profile_load_origin
    binding = _binding_tuple(daemon, request.runtime, wrapper,
                             daemon._scoped_host_provider, daemon.project_authority)
    current = load_trusted_profile(daemon._windows_t037_profile_path)

    def same_source(candidate: Any) -> bool:
        return (
            type(candidate) is WindowsT037ProfileV2
            and candidate.source_sha256 == effect.profile_source_sha256
            and dict(candidate.source_identity) == dict(effect.profile_source_identity)
            and candidate.sha256 == effect.profile_wrapper.sha256
            and candidate.host_resource_profile.sha256 == effect.profile.sha256
            and candidate.security_semantics == effect.profile_wrapper.security_semantics
            and candidate.wrapper_schema == effect.profile_wrapper.wrapper_schema
        )

    if (type(origin) is not _TrustedProfileLoadOrigin
            or origin._seal is not _NATIVE_PROFILE_LOADER_TOKEN
            or origin is not executor._trusted_profile_load_origin
            or origin.profile_wrapper is wrapper
            or origin.profile_path != binding[5]
            or not origin.matches(origin.profile_wrapper, binding[5])
            or not same_source(origin.profile_wrapper)
            or not same_source(current)
            or not same_source(effect.daemon_profile_wrapper)
            or dict(current.source_identity) != dict(origin.profile_wrapper.source_identity)
            or current.source_sha256 != origin.profile_wrapper.source_sha256
            or current.sha256 != origin.profile_wrapper.sha256
            or executor.profile.sha256 != effect.profile.sha256
            or executor.profile.windows_isolation.as_dict() != effect.policy.as_dict()
            or wrapper.host_resource_profile.sha256 != effect.profile.sha256):
        fail("HOST_CHANGE_APPCONTAINER_PROFILE_SOURCE_CHANGED",
             "the original CREATE, current daemon source, and fresh trusted-loader restore source differ")
    _current_windows_binding(executor.profile)
    launcher = [image for image in effect.profile_wrapper.launch_images if image.role == "launcher"]
    if len(launcher) != 1:
        fail("HOST_CHANGE_APPCONTAINER_OWNER_SOURCE_MISMATCH",
             "the exact original configured SCM launcher image is not unique")
    birth, user_sid = _native_process_identity(effect.pid, effect.service_user_sid, launcher[0].path)
    if (birth != effect.birth_filetime_100ns
            or user_sid.casefold() != effect.user_sid.casefold()):
        fail("HOST_CHANGE_APPCONTAINER_OWNER_SOURCE_MISMATCH",
             "current process birth or token user differs from the original CREATE owner")


def select_original_profile_effect(*, daemon: Any, request: Any, record: Any,
                                   executor: Any, plan: Any, receipt: Any,
                                   action: dict[str, Any]) -> ProfileRestoreSelection:
    """Select exactly one original rooted successful CREATE; never adopt by name."""
    from ._windows_host_change import _normalize_supported_actions
    if (request.phase != "RESTORE" or request.plan is not plan
            or request.receipt is not receipt
            or record.get("request") is not request
            or record.get("executor") is not executor):
        fail("HOST_CHANGE_APPCONTAINER_OWNER_BINDING_MISMATCH",
             "the sealed current RESTORE request/executor must bind the selection")
    roots = getattr(daemon, "_retained_appcontainer_profile_effects", None)
    lock = getattr(daemon, "_appcontainer_profile_effect_lock", None)
    if not isinstance(roots, dict) or lock is None:
        fail("HOST_CHANGE_APPCONTAINER_OWNER_UNKNOWN",
             "the original daemon profile-effect root is unavailable")
    _, actions = _normalize_supported_actions(plan, executor.profile)
    if len(actions) != 1 or _canonical(actions[0]) != _canonical(action):
        fail("HOST_CHANGE_APPCONTAINER_ACTION_MISMATCH",
             "restore selection action differs from the exact supported plan")
    with lock:
        candidates = [
            effect for effect in tuple(roots.values())
            if type(effect) is ProfileEffect and effect._is_rooted()
            and effect.daemon is daemon and effect.runtime is request.runtime
            and effect.request.authority is request.authority
            and effect.request.provider is request.provider
            and effect.request.project_id == request.project_id
            and effect.profile.sha256 == executor.profile.sha256
            and effect.request.profile_source_sha256 == request.profile_source_sha256
            and ((effect.software_only and effect.profile_source_sha256 is None)
                 or (not effect.software_only
                     and effect.profile_source_sha256 == request.profile_source_sha256))
            and effect._apply_receipt_sha256 == receipt.receipt_sha256
            and effect.request.plan.sha256 == plan.sha256
            and roots.get(effect._root_key) is effect
        ]
    if len(candidates) != 1:
        fail("HOST_CHANGE_APPCONTAINER_ORIGINAL_EFFECT_UNKNOWN",
             "restore requires exactly one matching daemon-rooted original CREATE effect")
    effect = candidates[0]
    _validate_original_effect_for_restore(effect, daemon=daemon, request=request,
                                          executor=executor, action=action,
                                          receipt=receipt)
    restore_body, restore_sha, expected_sha = _restore_selection_digest(
        plan, executor.profile, receipt, action)
    return ProfileRestoreSelection(
        daemon=daemon, request=request, record=record, executor=executor,
        effect=effect, action=action, plan_sha256=plan.sha256,
        receipt_sha256=receipt.receipt_sha256, restore_sha256=restore_sha,
        restore_body=restore_body, expected_resource_sha256=expected_sha,
        seal=_RESTORE_SELECTION_SEAL,
    )
