"""Original purpose-bound one-use Resume; unqualified/detached calls refuse."""
from __future__ import annotations
import threading
from contextlib import contextmanager
from ._execution_contract import ExecutionContractError

_SEAL=object()
_lock=threading.RLock()
_pending={} # Issuer-owned in-memory identity registry; never persisted or wire-minted.


class _OriginalResumeAttempt:
    """Strong original-object root and child dispatch reservation.

    This object records a possible Resume effect; it is not issuer authority.
    Its lifetime is tied to the actual retained child and its original factory.
    """
    __slots__ = (
        "process", "api", "factory", "profile", "job", "owner", "registry",
        "process_info", "output_address", "call_storage", "create_arguments",
        "native_create", "thread_handle", "process_entry", "thread_entry",
        "entry_bindings", "resume_function", "resume_arguments", "launch_inputs",
        "preparation", "parent", "image_api", "image_entry", "image_binding",
        "launch_role", "launch_profile_sha256",
        "connected_b", "connected_b_state", "connected_b_result", "connected_b_api",
        "connected_b_borrowed", "connected_b_pin", "dacl_observation", "job_bindings", "job_binding",
        "job_entry", "job_close_call", "registry_entries", "factory_participant",
        "gate", "gate_activity", "gate_depth", "held_a_present", "held_a",
        "owner_thread_id", "epoch_before", "epoch_consumed", "capability",
        "state", "dispatch_reserved", "reserved_entries", "release_errors",
        "finalizer_entries", "finalizer_consumed", "finalizer_active", "finalizer_started",
        "entered", "raw_return", "raw_return_captured", "previous_count", "raw_return_storage",
        "call_error", "transfer_error", "ownership_error", "ownership_error_entry",
        "ownership_error_owner", "ownership_error_stage", "return_transfer", "raw_type_name",
    )

    def __init__(self, process, capability):
        api = process._api
        factory = process._original_factory
        self.process, self.api, self.factory = process, api, factory
        self.profile, self.job, self.owner = process._original_profile, process._original_job, process._owner
        self.registry = process._registry
        self.process_info, self.output_address = process._process_info, process._output_address
        self.call_storage, self.create_arguments = process._call_storage, process._create_arguments
        self.native_create = process._native_create
        self.thread_handle = process._thread_handle
        self.process_entry, self.thread_entry = process._process_entry, process._thread_entry
        self.entry_bindings = tuple(process._entry_bindings.items())
        self.resume_function = api.k.ResumeThread
        self.resume_arguments = (self.thread_handle,)
        self.launch_inputs, self.preparation = process._launch_inputs, process._birth_preparation
        self.parent, self.image_api, self.image_entry = (
            process._parent_binding, process._image_api, process._image_entry)
        self.image_binding = process._image_binding
        self.launch_role = process._launch_role
        self.launch_profile_sha256 = process._launch_profile_sha256
        self.connected_b = getattr(process, "_connected_b_observer", None)
        self.connected_b_state = getattr(self.connected_b, "state", None)
        self.connected_b_result = getattr(self.connected_b, "result", None)
        self.connected_b_api = getattr(self.connected_b, "api", None)
        self.connected_b_borrowed = getattr(self.connected_b, "borrowed", None)
        self.connected_b_pin = getattr(self.connected_b, "pin", None)
        self.dacl_observation = getattr(api, "_last_child_dacl_observation", None)
        self.job_bindings = getattr(api, "_job_limit_bindings", None)
        self.job_binding = (self.job_bindings.get(self.job)
                            if type(self.job_bindings) is dict else None)
        self.job_entry = (self.job_binding.get("entry")
                          if type(self.job_binding) is dict else None)
        self.job_close_call = getattr(api, "_job_close_call", None)
        self.registry_entries = ()
        self.factory_participant = getattr(factory, "_profile_participant", None)
        self.gate = getattr(self.factory_participant, "gate", None)
        self.gate_activity = getattr(self.gate, "activity", None)
        self.gate_depth = getattr(self.gate, "depth", None)
        self.held_a_present = factory is not None and hasattr(factory, "_held_static_a")
        self.held_a = getattr(factory, "_held_static_a", None)
        self.owner_thread_id = threading.get_ident()
        self.epoch_before = process._resume_epoch
        self.epoch_consumed = None
        self.capability = capability
        self.state = "INSTALLING"
        self.dispatch_reserved = False
        self.reserved_entries = ()
        self.release_errors = {}
        self.finalizer_entries = ()
        self.finalizer_consumed = set()
        self.finalizer_active = False
        self.finalizer_started = False
        self.entered = False
        self.raw_return = None
        self.raw_return_storage = [None]
        self.raw_return_captured = False
        self.previous_count = None
        self.call_error = None
        self.transfer_error = None
        self.ownership_error = None
        self.ownership_error_entry = None
        self.ownership_error_owner = None
        self.ownership_error_stage = None
        self.return_transfer = "NOT_ENTERED"
        self.raw_type_name = None

    def capture_raw_return(self, raw_return):
        self.raw_return = raw_return

    def install_registry_reservation(self):
        entries = tuple(self.registry._entries.values())
        required = (self.process_entry, self.thread_entry, self.job_entry)
        if any(entry is None or not any(entry is item for item in entries) for entry in required):
            raise ExecutionContractError("NATIVE_CHILD_OWNER_UNKNOWN",
                "original process/thread/Job registry entries are unavailable for dispatch reservation",
                safe_retry=False, stage="pre_dispatch")
        self.registry_entries = entries
        self.reserved_entries = required
        for entry in required:
            self.release_errors[entry] = ExecutionContractError(
                "NATIVE_CHILD_DISPATCH_RESERVED",
                "original child dispatch owns the process/thread/Job registry entry",
                safe_retry=False, stage="pre_dispatch")
            entry._resume_dispatch_reservation = self
        self.registry._resume_dispatch_reservation = self
        self.dispatch_reserved = True
        self.state = "RESERVED"

    def blocks_registry_release(self, entry):
        if not any(entry is item for item in self.reserved_entries):
            return False
        if self.state in {"NO_EFFECT", "HANDED_OFF"}:
            return False
        return not self.may_release_main_thread(entry)

    def authorize_factory_finalizer_release(self, entry):
        if (not self.finalizer_active or not self.dispatch_reserved
                or self.state != "RETURNED_ONE"
                or threading.get_ident() != self.owner_thread_id
                or self.owner is not self.api
                or self.process._owner is not self.owner
                or self.process._resume_confirmed is not True
                or self.process._resume_attempt is not self
                or self.process._original_factory is not self.factory
                or getattr(self.registry, "_resume_finalizer_reservation", None) is not self
                or self.thread_entry.state != "RELEASED"
                or self.ownership_error is not None or self.transfer_error is not None
                or any(entry.state == "UNKNOWN" for entry in self.registry_entries)
                or not any(entry is item for item in self.finalizer_entries)
                or any(entry is item for item in self.reserved_entries)
                or entry in self.finalizer_consumed):
            return False
        self.finalizer_consumed.add(entry)
        return True

    def may_release_main_thread(self, entry):
        request = getattr(self.process, "_release_request", None)
        return (entry is self.thread_entry and self.state == "RETURNED_ONE"
                and self.process._resume_confirmed is True
                and type(request) is tuple and len(request) == 2
                and request[0] is entry and request[1] is self.process._owner
                and self.process._owner is self.owner)

    def defer_registry_release(self, entry):
        error = self.release_errors.get(entry)
        if error is None:
            error = ExecutionContractError("NATIVE_CHILD_DISPATCH_RESERVED",
                "original child dispatch owns this native registry entry",
                safe_retry=False, stage="pre_dispatch")
            self.release_errors[entry] = error
        return error


def _attempt_refusal(code, message):
    raise ExecutionContractError(code, message, safe_retry=False, stage="pre_dispatch")


def mark_original_resume_ownership_unknown(process, error, *, stage, entry=None, owner=None):
    """Latch first post-Resume ownership uncertainty without releasing original roots."""
    attempt = getattr(process, "_resume_attempt", None)
    if type(attempt) is not _OriginalResumeAttempt or attempt.process is not process:
        return None
    with process._ownership_lock:
        if process._resume_attempt is not attempt:
            return None
        with attempt.registry._lock:
            if attempt.state == "UNKNOWN":
                return attempt
            if attempt.state != "RETURNED_ONE":
                return attempt
            if attempt.ownership_error is None:
                attempt.ownership_error = error
                attempt.ownership_error_entry = entry
                attempt.ownership_error_owner = owner
                attempt.ownership_error_stage = stage
            attempt.state = "UNKNOWN"
            # Preserve raw Resume=1 and the dispatch barrier; only ownership
            # finalization is unknown, so no cleanup or retry is inferred.
            return attempt


def _check_attempt_roots(process, attempt, *, consumed):
    api, factory = attempt.api, attempt.factory
    capability = attempt.capability
    expected_used = True if consumed else False
    if (process is not attempt.process or process._resume_attempt is not attempt
            or process._api is not api or attempt.owner is not api
            or process._owner is not attempt.owner or process._registry is not attempt.registry
            or attempt.registry.owner is not api
            or process._process_info is not attempt.process_info
            or process._output_address != attempt.output_address
            or process._call_storage is not attempt.call_storage
            or process._create_arguments is not attempt.create_arguments
            or process._native_create is not attempt.native_create
            or process._thread_entry is not attempt.thread_entry
            or process._process_entry is not attempt.process_entry
            or process._thread_handle != attempt.thread_handle
            or process._job_handle is not attempt.job
            or process._original_job is not attempt.job
            or process._original_factory is not factory
            or process._original_profile is not attempt.profile
            or process._launch_role != attempt.launch_role
            or process._launch_profile_sha256 != attempt.launch_profile_sha256
            or process._image_api is not attempt.image_api
            or process._image_entry is not attempt.image_entry
            or process._image_binding is not attempt.image_binding
            or process._parent_binding is not attempt.parent
            or process._birth_preparation is not attempt.preparation
            or process._launch_inputs is not attempt.launch_inputs
            or getattr(process, "_connected_b_observer", None) is not attempt.connected_b
            or getattr(attempt.connected_b, "state", None) != attempt.connected_b_state
            or getattr(attempt.connected_b, "result", None) is not attempt.connected_b_result
            or getattr(attempt.connected_b, "api", None) is not attempt.connected_b_api
            or getattr(attempt.connected_b, "borrowed", None) is not attempt.connected_b_borrowed
            or getattr(attempt.connected_b, "pin", None) is not attempt.connected_b_pin
            or getattr(api, "_last_child_dacl_observation", None) is not attempt.dacl_observation
            or api.k.ResumeThread is not attempt.resume_function
            or process._resume_epoch != (attempt.epoch_consumed if consumed else attempt.epoch_before)
            or (not consumed and (process._resume_attempted or process._resume_confirmed
                                  or process._returned))
            or (consumed and not process._resume_attempted)
            or type(capability) is not _NativeResumeCapability or capability._seal is not _SEAL
            or capability.process is not process or capability.used is not expected_used
            or capability.birth != process.birth_filetime_100ns
            or capability.profile_sha256 != attempt.launch_profile_sha256
            or capability.thread_handle != attempt.thread_handle
            or capability.job_handle != attempt.job
            or capability.epoch != attempt.epoch_before):
        _attempt_refusal("NATIVE_CHILD_OWNER_UNKNOWN", "original ResumeThread/child/API bindings changed")
    process._check_original()
    if attempt.launch_inputs is not None:
        attempt.launch_inputs.validate_native_storage(process, api)
    if factory is not None:
        if (factory._api is not api or factory._job is not attempt.job
                or factory.profile is not attempt.profile or factory._closed):
            _attempt_refusal("NATIVE_CHILD_OWNER_UNKNOWN", "original factory/profile/Job binding changed")
        if attempt.held_a_present and getattr(factory, "_held_static_a", None) is not attempt.held_a:
            _attempt_refusal("NATIVE_CHILD_OWNER_UNKNOWN", "original held-A reader changed during Resume dispatch")
        if getattr(factory, "_profile_participant", None) is not attempt.factory_participant:
            _attempt_refusal("NATIVE_CHILD_OWNER_UNKNOWN", "original Gate participant changed during Resume dispatch")
        gate = attempt.gate
        if gate is not None and (getattr(gate, "activity", None) is not attempt.gate_activity
                                 or getattr(gate, "depth", None) != attempt.gate_depth):
            _attempt_refusal("NATIVE_CHILD_OWNER_UNKNOWN", "original Gate activity changed during Resume dispatch")
    if (type(attempt.job_bindings) is not dict
            or getattr(api, "_job_limit_bindings", None) is not attempt.job_bindings
            or attempt.job_bindings.get(attempt.job) is not attempt.job_binding
            or type(attempt.job_binding) is not dict
            or attempt.job_binding.get("entry") is not attempt.job_entry
            or attempt.job_binding.get("profile") is not attempt.profile
            or attempt.job_binding.get("close_call") is not attempt.job_close_call
            or getattr(api, "_job_close_call", None) is not attempt.job_close_call
            or attempt.job_close_call.phase != "PRE_SDK"
            or attempt.job_entry.state != "ACQUIRED"):
        _attempt_refusal("JOB_LIMITS_OWNER_UNKNOWN", "original configured Job/registry binding changed")
    for entry in (attempt.process_entry, attempt.thread_entry):
        if entry.state != "ACQUIRED" or entry.registry is not attempt.registry:
            _attempt_refusal("NATIVE_CHILD_OWNER_UNKNOWN", "original process/thread registry acquisition changed")
    if (threading.get_ident() != attempt.owner_thread_id
            or attempt.process_entry.thread_id != attempt.owner_thread_id
            or attempt.thread_entry.thread_id != attempt.owner_thread_id):
        _attempt_refusal("NATIVE_CHILD_OWNER_UNKNOWN", "Resume dispatch left the original child/Gate thread")
    if (attempt.registry._resume_dispatch_reservation is not attempt
            or getattr(attempt.registry, "_resume_finalizer_reservation", None) is not None
            or len(attempt.registry._entries) != len(attempt.registry_entries)
            or any(current is not pinned for current, pinned
                   in zip(attempt.registry._entries.values(), attempt.registry_entries))
            or any(getattr(entry, "_resume_dispatch_reservation", None) is not attempt
                   for entry in attempt.reserved_entries)):
        _attempt_refusal("NATIVE_CHILD_OWNER_UNKNOWN", "original child dispatch reservation changed")


def _release_known_no_effect(process, attempt, *, clear_attempt=False):
    with process._ownership_lock:
        with attempt.registry._lock:
            if process._resume_attempt is attempt:
                attempt.state = "NO_EFFECT"
                attempt.dispatch_reserved = False
                process._resume_attempted = False
            if getattr(attempt.registry, "_resume_dispatch_reservation", None) is attempt:
                attempt.registry._resume_dispatch_reservation = None
            for entry in attempt.reserved_entries:
                if getattr(entry, "_resume_dispatch_reservation", None) is attempt:
                    entry._resume_dispatch_reservation = None
            if getattr(attempt.registry, "_resume_finalizer_reservation", None) is attempt:
                attempt.registry._resume_finalizer_reservation = None
            if clear_attempt and process._resume_attempt is attempt:
                object.__setattr__(process, "_resume_attempt", None)


@contextmanager
def original_resume_factory_finalization(process):
    """Permit one exact original-owner cleanup pass after Resume returned one."""
    attempt = getattr(process, "_resume_attempt", None)
    if attempt is None or type(attempt) is not _OriginalResumeAttempt:
        yield None
        return
    if attempt.state in {"NO_EFFECT", "HANDED_OFF"}:
        yield None
        return
    with process._ownership_lock:
        with attempt.registry._lock:
            if (attempt.process is not process or process._resume_attempt is not attempt
                    or attempt.state != "RETURNED_ONE" or not attempt.dispatch_reserved
                    or not process._resume_confirmed
                    or threading.get_ident() != attempt.owner_thread_id
                    or attempt.owner is not attempt.api
                    or process._owner is not attempt.owner
                    or attempt.thread_entry.state != "RELEASED"
                    or attempt.ownership_error is not None or attempt.transfer_error is not None
                    or any(entry.state == "UNKNOWN" for entry in attempt.registry_entries)
                    or process._returned
                    or attempt.registry._resume_dispatch_reservation is not attempt
                    or getattr(attempt.registry, "_resume_finalizer_reservation", None) is not None
                    or attempt.finalizer_started):
                _attempt_refusal("NATIVE_CHILD_OWNER_REQUIRED",
                    "only the original factory owner may finalize known Resume success")
            attempt.finalizer_started = True
            attempt.finalizer_active = True
            attempt.finalizer_entries = tuple(
                entry for entry in attempt.registry_entries
                if not any(entry is child_entry for child_entry in attempt.reserved_entries))
            attempt.finalizer_consumed.clear()
            attempt.registry._resume_finalizer_reservation = attempt
    try:
        yield attempt
    finally:
        with process._ownership_lock:
            with attempt.registry._lock:
                if getattr(attempt.registry, "_resume_finalizer_reservation", None) is attempt:
                    attempt.registry._resume_finalizer_reservation = None
                attempt.finalizer_active = False


def finish_original_resume_attempt(process):
    """End the lease only after the original factory published its owner."""
    with process._ownership_lock:
        attempt = getattr(process, "_resume_attempt", None)
        if (type(attempt) is not _OriginalResumeAttempt or attempt.process is not process
                or attempt.state != "RETURNED_ONE" or not attempt.dispatch_reserved
                or attempt.finalizer_active
                or getattr(attempt.registry, "_resume_finalizer_reservation", None) is attempt
                or not process._resume_confirmed or not process._returned
                or process._thread_entry.state != "RELEASED"
                or attempt.owner is not attempt.api or process._owner is not attempt.owner
                or attempt.ownership_error is not None or attempt.transfer_error is not None
                or any(entry.state == "UNKNOWN" for entry in attempt.registry_entries)):
            _attempt_refusal("NATIVE_CHILD_OWNER_REQUIRED",
                "Resume dispatch reservation requires confirmed original factory ownership handoff")
        with attempt.registry._lock:
            if attempt.registry._resume_dispatch_reservation is not attempt:
                _attempt_refusal("NATIVE_CHILD_OWNER_UNKNOWN", "original child dispatch reservation was replaced")
            if any(getattr(entry, "_resume_dispatch_reservation", None) is not attempt
                   for entry in attempt.reserved_entries):
                _attempt_refusal("NATIVE_CHILD_OWNER_UNKNOWN", "original registry-entry reservation was replaced")
            attempt.state = "HANDED_OFF"
            attempt.dispatch_reserved = False
            attempt.registry._resume_dispatch_reservation = None
            for entry in attempt.reserved_entries:
                entry._resume_dispatch_reservation = None


def original_no_effect_attempt_witness(process, *, retired_worker=None):
    """Pure exact-root witness for a consumed capability with no Resume entry.

    This predicate reports eligibility facts only. It performs no SDK call,
    retry, cleanup, epoch change, capability mint, or ownership transition.
    Callers must still prove their own terminal/pin/reader conditions. The
    optional retired_worker branch recognizes only the exact original Worker
    after its own known child retirement; default API-owned behavior is intact.
    """
    from ._windows_t037 import _NATIVE_SUSPENDED_CHILD_SEAL, _RetainedSuspendedChild
    if (type(process) is not _RetainedSuspendedChild
            or getattr(process, "_seal", None) is not _NATIVE_SUSPENDED_CHILD_SEAL):
        return False
    attempt = getattr(process, "_resume_attempt", None)
    if (type(attempt) is not _OriginalResumeAttempt or attempt.process is not process
            or process._resume_attempt is not attempt or attempt.state != "NO_EFFECT"
            or attempt.entered is not False or attempt.raw_return_captured is not False
            or attempt.raw_return is not None or type(attempt.raw_return_storage) is not list
            or len(attempt.raw_return_storage) != 1 or attempt.raw_return_storage[0] is not None
            or attempt.previous_count is not None or attempt.call_error is not None
            or attempt.transfer_error is not None or attempt.ownership_error is not None
            or attempt.return_transfer != "NOT_ENTERED" or attempt.raw_type_name is not None
            or attempt.dispatch_reserved is not False or attempt.epoch_consumed is None
            or type(attempt.epoch_before) is not int or type(attempt.epoch_consumed) is not int
            or attempt.epoch_before < 0 or attempt.epoch_consumed != attempt.epoch_before + 1
            or process._resume_epoch != attempt.epoch_consumed
            or process._resume_attempted is not False or process._resume_confirmed is not False
            or process._returned is not False):
        return False
    api, factory, registry = attempt.api, attempt.factory, attempt.registry
    current_owner = api
    if retired_worker is not None:
        from ._java_worker import PersistentJavaWorker
        if type(retired_worker) is not PersistentJavaWorker:
            return False
        if (getattr(retired_worker, "_original_process_factory", None) is not factory
                or getattr(retired_worker, "_process_factory", None) is not factory
                or getattr(retired_worker, "_process", None) is not None
                or getattr(retired_worker, "_fixed_process", None) is not None
                or getattr(retired_worker, "_fixed_launch_owner", None) is not None
                or getattr(retired_worker, "_startup_unknown", None) is not None
                or getattr(retired_worker, "_retirement_unknown", None) is not None):
            return False
        current_owner = retired_worker
    if (api is None or attempt.owner is not api or process._api is not api
            or process._owner is not current_owner or process._registry is not registry
            or registry.owner is not api or process._original_factory is not factory
            or process._original_profile is not attempt.profile
            or process._original_job is not attempt.job or process._job_handle is not attempt.job
            or process._process_info is not attempt.process_info
            or process._output_address != attempt.output_address
            or process._call_storage is not attempt.call_storage
            or process._create_arguments is not attempt.create_arguments
            or process._native_create is not attempt.native_create
            or process._process_entry is not attempt.process_entry
            or process._thread_entry is not attempt.thread_entry
            or process._launch_inputs is not attempt.launch_inputs
            or process._birth_preparation is not attempt.preparation
            or process._parent_binding is not attempt.parent
            or process._image_api is not attempt.image_api
            or process._image_entry is not attempt.image_entry
            or process._image_binding is not attempt.image_binding
            or process._launch_role != attempt.launch_role
            or process._launch_profile_sha256 != attempt.launch_profile_sha256
            or api.k.ResumeThread is not attempt.resume_function
            or getattr(process, "_connected_b_observer", None) is not attempt.connected_b
            or getattr(api, "_last_child_dacl_observation", None) is not attempt.dacl_observation):
        return False
    capability = attempt.capability
    if (type(capability) is not _NativeResumeCapability or capability._seal is not _SEAL
            or capability.process is not process or capability.used is not True
            or capability.epoch != attempt.epoch_before
            or capability.birth != process.birth_filetime_100ns
            or capability.profile_sha256 != attempt.launch_profile_sha256
            or capability.thread_handle != attempt.thread_handle
            or capability.job_handle != attempt.job):
        return False
    with _lock:
        if _pending.get(id(capability)) is capability:
            return False
    if factory is not None:
        if (factory._api is not api or factory._job is not attempt.job
                or factory.profile is not attempt.profile
                or getattr(factory, "_profile_participant", None) is not attempt.factory_participant
                or (attempt.factory_participant is not None
                    and getattr(attempt.factory_participant, "gate", None) is not attempt.gate)
                or (attempt.held_a_present
                    and getattr(factory, "_held_static_a", None) is not attempt.held_a)):
            return False
    if attempt.preparation is not None:
        prep = attempt.preparation
        if (prep.api is not api or prep.factory is not factory or prep.profile is not attempt.profile
                or prep.job is not attempt.job or prep.registry is not registry
                or prep.child is not process or prep.state == "UNKNOWN"):
            return False
    if (type(attempt.job_bindings) is not dict
            or getattr(api, "_job_limit_bindings", None) is not attempt.job_bindings
            or attempt.job_bindings.get(attempt.job) is not attempt.job_binding
            or type(attempt.job_binding) is not dict
            or attempt.job_binding.get("entry") is not attempt.job_entry
            or attempt.job_binding.get("profile") is not attempt.profile
            or attempt.job_binding.get("close_call") is not attempt.job_close_call
            or getattr(api, "_job_close_call", None) is not attempt.job_close_call):
        return False
    if (type(process._entry_bindings) is not dict
            or len(process._entry_bindings) != len(attempt.entry_bindings)):
        return False
    for key, binding in attempt.entry_bindings:
        if process._entry_bindings.get(key) is not binding:
            return False
    for entry in attempt.registry_entries:
        if (entry.registry is not registry or entry.state == "UNKNOWN"
                or (entry.state == "ACQUIRED" and registry.find(entry.adapter, entry.value) is not entry)):
            return False
    if (attempt.process_entry.state == "UNKNOWN" or attempt.thread_entry.state == "UNKNOWN"
            or attempt.job_entry is None or attempt.job_entry.state == "UNKNOWN"
            or attempt.registry._resume_dispatch_reservation is attempt
            or getattr(attempt.registry, "_resume_finalizer_reservation", None) is attempt
            or any(getattr(entry, "_resume_dispatch_reservation", None) is attempt
                   for entry in attempt.reserved_entries)
            or getattr(process, "_retirement_error", None) is not None
            or getattr(process, "_terminate_error", None) is not None
            or getattr(api, "_retained_launch_cleanup", None)):
        return False
    if attempt.connected_b is not None and getattr(attempt.connected_b, "state", None) == "UNKNOWN":
        return False
    return True

class _NativeResumeCapability:
    __slots__=('process','profile_sha256','birth','thread_handle','job_handle','epoch','_seal','used')
    def __init__(self,process,profile_sha256,*,seal):
        if seal is not _SEAL:raise ValueError('native resume capability requires issuer authority')
        self.process=process;self.profile_sha256=profile_sha256
        self.birth=process.birth_filetime_100ns;self._seal=seal;self.used=False
        self.thread_handle=process._thread_handle;self.job_handle=process._job_handle
        self.epoch=process._resume_epoch


def issue_native_resume(process,profile,child_binding,dacl_observation):
    # Only the closed original qualification/PROJECT path may reach the
    # accepted one-use consumer. Legacy and detached calls still refuse below.
    factory=getattr(process, '_original_factory', None)
    if getattr(factory, '_qualification_purpose', None) is not None:
        from ._windows_t037_qualification import require_resume_purpose, _PURPOSES
        owner=require_resume_purpose(process,profile,child_binding,dacl_observation)
        roots=(process._api,process._owner,process._connected_b_observer,
               process._birth_preparation,getattr(factory,'_held_static_a',None))
        with _lock:
            if (_PURPOSES.get(id(factory)) is not owner or factory._qualification_purpose is not owner
                    or roots!=(process._api,process._owner,process._connected_b_observer,
                              process._birth_preparation,getattr(factory,'_held_static_a',None))
                    or process._resume_epoch!=0 or process._resume_attempt is not None
                    or any(c.process is process for c in _pending.values())):
                _attempt_refusal('NATIVE_CHILD_PROOF_UNAVAILABLE','original authorization roots changed or already issued')
            capability=_NativeResumeCapability(process,profile.sha256,seal=_SEAL)
            _pending[id(capability)]=capability
            return capability
    from ._windows_t037 import is_native_suspended_child,WindowsT037ProfileV2,WindowsT037Error
    from ._windows_t037_child import RetainedChildObservation
    from ._windows_t037_acl import ChildDaclObservation
    reasons=[]
    if not is_native_suspended_child(process) or process._owner is not process._api:
        reasons.append('EXACT_NATIVE_CHILD_OWNER_UNAVAILABLE')
    if type(profile) is not WindowsT037ProfileV2:reasons.append('TRUSTED_V2_PROFILE_REQUIRED')
    if (type(child_binding) is not RetainedChildObservation or child_binding.pid!=getattr(process,"pid",None)
            or child_binding.birth_filetime_100ns!=getattr(process,"birth_filetime_100ns",None)):
        reasons.append('FRESH_ORIGINAL_HANDLE_BINDING_UNAVAILABLE')
    if (type(dacl_observation) is not ChildDaclObservation or dacl_observation.child_pid!=getattr(process,"pid",None)
            or dacl_observation.birth_filetime_100ns!=getattr(process,"birth_filetime_100ns",None)):
        reasons.append('ACTUAL_CHILD_DACL_BINDING_UNAVAILABLE')
    # These requirements have no reviewed producer/target contract. A caller's
    # dict, read-only observation or frozen fixture cannot supply them.
    from ._windows_t037 import PROFILE_SCHEMA_V3
    job_reason = 'JOB_REQUIRED_TARGET_UNAVAILABLE'
    if type(profile) is WindowsT037ProfileV2 and profile.wrapper_schema == PROFILE_SCHEMA_V3:
        from ._windows_job_limits import require_configured_job
        try:
            require_configured_job(process._api, process._job_handle, profile, for_child_birth=False)
            job_reason = 'JOB_LIMITS_CONFIGURED_NATIVE_ACCEPTANCE_UNVERIFIED'
        except ExecutionContractError:
            job_reason = 'JOB_LIMITS_BINDING_UNVERIFIED'
    reasons.extend((job_reason,'REMOTE_ARGV_READBACK_UNAVAILABLE',
                    'LOADED_IMAGE_INTEGRITY_UNAVAILABLE','WFP_ACCESS_VISIBILITY_UNVERIFIED',
                    'WFP_DEFAULT_BLOCK_UNVERIFIED','WFP_EFFECTIVE_ARBITRATION_UNVERIFIED',
                    'MIC_UNVERIFIED','FULL_FILESYSTEM_ISOLATION_UNVERIFIED',
                    'VENDOR_LOOPBACK_LICENSE_COMPATIBILITY_UNVERIFIED'))
    raise WindowsT037Error('NATIVE_CHILD_PROOF_UNAVAILABLE',
        'native child requirements remain unverified; Resume authorization refused',
        safe_retry=False,stage='pre_dispatch',details={'phase':'SUSPENDED_CHILD_OBSERVED','reasons':reasons})


def resume_native_child(process,capability):
    """Consume identity-bound authority before any possibly effective call.

    Current issuer never registers a capability. This entry cannot be unlocked
    by constructing its private value type, changing fields or passing JSON.
    """
    from ._windows_t037 import is_native_suspended_child
    if (not is_native_suspended_child(process) or getattr(process, "_resume_attempt", None) is not None):
        _attempt_refusal("NATIVE_CHILD_PROOF_UNAVAILABLE",
            "no live never-resumed original child is available for a first Resume attempt")

    attempt = None
    with process._ownership_lock:
        if (not is_native_suspended_child(process) or process._resume_attempt is not None):
            _attempt_refusal("NATIVE_CHILD_PROOF_UNAVAILABLE",
                "original child already has a Resume attempt or is no longer never-resumed")
        if process._owner is not process._api:
            _attempt_refusal("NATIVE_CHILD_OWNER_REQUIRED",
                "the original retained child must still be owned by its original launch API before Resume consume")
        attempt = _OriginalResumeAttempt(process, capability)
        object.__setattr__(process, "_resume_attempt", attempt)

    try:
        with attempt.registry._lock:
            if getattr(attempt.registry, "_resume_dispatch_reservation", None) is not None:
                _attempt_refusal("NATIVE_CHILD_DISPATCH_RESERVED",
                    "another original child dispatch already reserves this native registry")
            attempt.install_registry_reservation()

        with process._ownership_lock:
            _check_attempt_roots(process, attempt, consumed=False)
            with _lock:
                if (type(capability) is not _NativeResumeCapability or capability._seal is not _SEAL
                        or _pending.get(id(capability)) is not capability or capability.used
                        or capability.process is not process
                        or capability.birth != process.birth_filetime_100ns
                        or capability.profile_sha256 != process._launch_profile_sha256
                        or capability.thread_handle != attempt.thread_handle
                        or capability.job_handle != attempt.job
                        or capability.epoch != attempt.epoch_before):
                    _attempt_refusal("NATIVE_CHILD_PROOF_UNAVAILABLE",
                        "no live one-use issuer authority for this exact original child")
                capability.used = True
                _pending.pop(id(capability))
                object.__setattr__(process, "_resume_epoch", attempt.epoch_before + 1)
                process._resume_attempted = True
                attempt.epoch_consumed = process._resume_epoch
                attempt.state = "CONSUMED"

        # The Job query is the only potentially effective prerequisite and is
        # intentionally outside both the child lock and issuer registry lock.
        from ._windows_job_limits import require_configured_job
        require_configured_job(attempt.api, attempt.job, attempt.profile, for_child_birth=False)

        with process._ownership_lock:
            _check_attempt_roots(process, attempt, consumed=True)
            attempt.entered = True
            attempt.state = "ENTERED"
            attempt.return_transfer = "CALL_ENTERED"

        try:
            raw_return = attempt.resume_function(*attempt.resume_arguments)
        except BaseException as error:
            attempt.call_error = error
            attempt.return_transfer = "CALL_RAISED_OR_TRANSFER_UNKNOWN"
            attempt.state = "UNKNOWN"
            raise

        try:
            # Preallocated storage roots the exact object before diagnostic or
            # type-transfer work can fail.
            attempt.raw_return_storage[0] = raw_return
            attempt.raw_return_captured = True
            attempt.return_transfer = "RAW_RETURN_ROOTED"
            attempt.capture_raw_return(raw_return)
            attempt.return_transfer = "RAW_RETURN_CAPTURED"
            attempt.raw_type_name = type(raw_return).__module__ + "." + type(raw_return).__qualname__
        except BaseException as error:
            attempt.transfer_error = error
            attempt.return_transfer = "RETURN_TRANSFER_FAILED"
            attempt.state = "UNKNOWN"
            raise ExecutionContractError("NATIVE_CHILD_RESUME_UNKNOWN",
                "ResumeThread entered but its raw return could not be retained",
                safe_retry=False, stage="pre_dispatch") from error

        if type(raw_return) is int:
            previous_count = raw_return
        elif type(raw_return) is attempt.api.W.DWORD and type(raw_return.value) is int:
            previous_count = raw_return.value
        else:
            attempt.state = "UNKNOWN"
            raise ExecutionContractError("NATIVE_CHILD_RESUME_UNKNOWN",
                "ResumeThread returned an unclassified raw value; original child retained",
                safe_retry=False, stage="pre_dispatch",
                details={"raw_type": attempt.raw_type_name, "previous_count": "UNKNOWN"})
        attempt.previous_count = previous_count
        if previous_count == 1:
            attempt.state = "RETURNED_ONE"
            return 1
        attempt.state = "UNKNOWN"
        # Return the exact scalar value to preserve the factory's established
        # Windows-error branch. Its finally path recognizes UNKNOWN and retains.
        return previous_count
    except BaseException as error:
        if attempt.state in {"INSTALLING", "RESERVED"}:
            _release_known_no_effect(process, attempt, clear_attempt=True)
        elif attempt.state == "CONSUMED":
            _release_known_no_effect(process, attempt)
        elif attempt.entered and attempt.state in {"ENTERED", "RETURNED_ONE"}:
            if attempt.transfer_error is None:
                attempt.transfer_error = error
            attempt.return_transfer = "OUTPUT_TRANSFER_FAILED"
            attempt.state = "UNKNOWN"
        raise


def refuse_unverified_prebirth(profile, *, owner=None):
    """Legacy read-only diagnostic, always refusing; never the A birth gate.

    Its mixed observation/reasons remain intact for historical diagnostic and
    resource-owner callers. The actual factory uses require_static_prebirth.
    """
    from ._windows_t037 import WindowsT037Observer,WindowsT037ProfileV2,T037SessionProcessFactory,windows_host_fingerprint
    from ._windows_t037_native import WindowsNativePolicyAdapter
    if type(profile) is not WindowsT037ProfileV2:
        raise ExecutionContractError('T037_POLICY_UNVERIFIED','native static admission requires v2',safe_retry=False,stage='pre_dispatch')
    if type(owner) is not T037SessionProcessFactory or owner.profile is not profile or owner._closed:
        raise ExecutionContractError('T037_POLICY_UNVERIFIED','prebirth observation requires its private factory lifetime owner',
            safe_retry=False,stage='pre_dispatch',details={'phase':'STATIC_HOST_BROKER_POLICY','reasons':['NATIVE_OBSERVATION_OWNER_REQUIRED']})
    from ._windows_owned_resources import registry_for,attach_registry
    registry=registry_for(owner)
    scope=registry.scope()
    observer=WindowsT037Observer()
    # The API is genuinely its own read-only adapter, not impersonated as the
    # launch API. Factory + shared typed registry hold original adapter/closer.
    owner._prebirth_observers.append(observer)
    attach_registry(observer.api,registry)
    try:
        adapter=WindowsNativePolicyAdapter()
        if not hasattr(observer,"_native_policy_adapters"):observer._native_policy_adapters=[]
        observer._native_policy_adapters.append(adapter)
        attach_registry(adapter.api,registry)
        observation=adapter.observe(profile,
            current_host_fingerprint=windows_host_fingerprint(resource_registry=registry),process_observer=observer)
    finally:
        failures=registry.drain(scope)
        if failures:raise failures[0][1]
    raise ExecutionContractError('T037_POLICY_UNVERIFIED','prebirth native policy requirements remain unverified',
        safe_retry=False,stage='pre_dispatch',details={'phase':'STATIC_HOST_BROKER_POLICY',
        'reasons':list(observation.reasons)+['JOB_REQUIRED_TARGET_UNAVAILABLE','MIC_UNVERIFIED',
        'FULL_FILESYSTEM_ISOLATION_UNVERIFIED','VENDOR_LOOPBACK_LICENSE_COMPATIBILITY_UNVERIFIED']})


def require_static_prebirth(profile, *, owner):
    """A static producer only; never issue B/C or ordinary Resume authority."""
    from ._windows_t037_admission import require_static_prebirth as require_static
    return require_static(profile, owner=owner)
