"""Child-bound one-use Resume boundary. No native isolation issuer exists yet."""
from __future__ import annotations
import threading
from ._execution_contract import ExecutionContractError

_SEAL=object()
_lock=threading.RLock()
_pending={} # Issuer-owned in-memory identity registry; never persisted or wire-minted.

class _NativeResumeCapability:
    __slots__=('process','profile_sha256','birth','thread_handle','job_handle','epoch','_seal','used')
    def __init__(self,process,profile_sha256,*,seal):
        if seal is not _SEAL:raise ValueError('native resume capability requires issuer authority')
        self.process=process;self.profile_sha256=profile_sha256
        self.birth=process.birth_filetime_100ns;self._seal=seal;self.used=False
        self.thread_handle=process._thread_handle;self.job_handle=process._job_handle
        self.epoch=process._resume_epoch


def issue_native_resume(process,profile,child_binding,dacl_observation):
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
    reasons.extend(('JOB_REQUIRED_TARGET_UNAVAILABLE','REMOTE_ARGV_READBACK_UNAVAILABLE',
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
    with _lock:
        if (type(capability) is not _NativeResumeCapability or capability._seal is not _SEAL
                or _pending.get(id(capability)) is not capability or capability.used
                or capability.process is not process or not is_native_suspended_child(process)
                or capability.birth!=process.birth_filetime_100ns or process._owner is not process._api
                or capability.profile_sha256!=process._launch_profile_sha256
                or capability.thread_handle!=process._thread_handle or capability.job_handle!=process._job_handle
                or capability.epoch!=process._resume_epoch):
            raise ExecutionContractError('NATIVE_CHILD_PROOF_UNAVAILABLE','no live one-use issuer authority for original child',safe_retry=False,stage='pre_dispatch')
        capability.used=True
        process._resume_epoch += 1
        _pending.pop(id(capability))
        # Record possible effect before entering the API; no exception or
        # timeout can restore never-resumed cleanup eligibility.
        process.note_resume_attempt()
        return int(process._api.k.ResumeThread(process._thread_handle))


def refuse_unverified_prebirth(profile, *, owner=None):
    """Observe host/broker/policy only. No unborn child evidence is requested."""
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
