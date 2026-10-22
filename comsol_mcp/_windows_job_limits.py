"""Operator-selected committed-memory/active-process limits for the original Job.

A successful SDK readback is configuration evidence, never isolation or Resume
power. No time limit, RSS limit, shared-job mutation, replacement Job or configuration retry.
"""
from __future__ import annotations
from dataclasses import dataclass
import ctypes as C
import hashlib
import json
from typing import Mapping
from ._execution_contract import ExecutionContractError

SCHEMA = 'comsol-mcp.windows-job-limits/1'
FLAGS = 0x00000100 | 0x00000200 | 0x00000008


def _fail(code, message):
    raise ExecutionContractError(code, message, safe_retry=False, stage='pre_dispatch')


@dataclass(frozen=True)
class JobLimitsV1:
    process_memory_limit_bytes: int
    job_memory_limit_bytes: int
    active_process_limit: int

    def __post_init__(self):
        for name, upper in (('process_memory_limit_bytes', 2**64-1),
                            ('job_memory_limit_bytes', 2**64-1), ('active_process_limit', 2**32-1)):
            value = getattr(self, name)
            if type(value) is not int or not 0 < value <= upper:
                _fail('JOB_LIMITS_INVALID', 'explicit positive bounded integer Job limits required')
        if self.process_memory_limit_bytes > self.job_memory_limit_bytes:
            _fail('JOB_LIMITS_INVALID', 'per-process committed cap exceeds whole Job committed cap')

    def as_dict(self):
        return {'schema': SCHEMA, 'process_memory_limit_bytes': self.process_memory_limit_bytes,
                'job_memory_limit_bytes': self.job_memory_limit_bytes,
                'active_process_limit': self.active_process_limit}

    @property
    def sha256(self):
        return hashlib.sha256(json.dumps(self.as_dict(), sort_keys=True,
            separators=(',', ':'), allow_nan=False).encode()).hexdigest()


def parse_job_limits(document):
    if document is None:
        _fail('JOB_LIMITS_NOT_CONFIGURED', 'trusted host profile Job limits are not configured')
    if not isinstance(document, Mapping) or set(document) != {
            'schema', 'process_memory_limit_bytes', 'job_memory_limit_bytes', 'active_process_limit'}:
        _fail('JOB_LIMITS_INVALID', 'Job limits must match the closed version-1 contract')
    if document['schema'] != SCHEMA:
        _fail('JOB_LIMITS_INVALID', 'Job limits schema is unsupported')
    return JobLimitsV1(*(document[k] for k in (
        'process_memory_limit_bytes', 'job_memory_limit_bytes', 'active_process_limit')))


def require_limits(profile):
    from ._windows_t037 import WindowsT037ProfileV2, PROFILE_SCHEMA_V3, PROFILE_SCHEMA_V4
    if (type(profile) is not WindowsT037ProfileV2 or profile.job_limits is None
            or profile.wrapper_schema not in {PROFILE_SCHEMA_V3, PROFILE_SCHEMA_V4}):
        _fail('JOB_LIMITS_NOT_CONFIGURED', 'explicit trusted v3/v4 Job limits required before owned Job/child birth')
    if type(profile.job_limits) is not JobLimitsV1:
        _fail('JOB_LIMITS_INVALID', 'Job limits are not the closed typed host configuration')
    # Revalidate to reject tampered in-process data before entering the SDK.
    JobLimitsV1(profile.job_limits.process_memory_limit_bytes,
                profile.job_limits.job_memory_limit_bytes, profile.job_limits.active_process_limit)
    return profile.job_limits


class BASIC_LIMIT(C.Structure):
    _fields_ = [('PerProcessUserTimeLimit', C.c_int64), ('PerJobUserTimeLimit', C.c_int64),
                ('LimitFlags', C.c_uint32), ('MinimumWorkingSetSize', C.c_size_t),
                ('MaximumWorkingSetSize', C.c_size_t), ('ActiveProcessLimit', C.c_uint32),
                ('Affinity', C.c_size_t), ('PriorityClass', C.c_uint32), ('SchedulingClass', C.c_uint32)]


class IO_COUNTERS(C.Structure):
    _fields_ = [(name, C.c_uint64) for name in ('ReadOperationCount', 'WriteOperationCount',
        'OtherOperationCount', 'ReadTransferCount', 'WriteTransferCount', 'OtherTransferCount')]


class EXTENDED_LIMIT(C.Structure):
    _fields_ = [('BasicLimitInformation', BASIC_LIMIT), ('IoInfo', IO_COUNTERS),
                ('ProcessMemoryLimit', C.c_size_t), ('JobMemoryLimit', C.c_size_t),
                ('PeakProcessMemoryUsed', C.c_size_t), ('PeakJobMemoryUsed', C.c_size_t)]


def _abi():
    if (C.sizeof(C.c_void_p) != 8 or C.sizeof(BASIC_LIMIT) != 64 or C.sizeof(EXTENDED_LIMIT) != 144
            or EXTENDED_LIMIT.ProcessMemoryLimit.offset != 112):
        _fail('JOB_LIMITS_ABI_UNVERIFIED', 'owned Job limits support only the checked Win64 ABI')


def _bool_result(result):
    value = getattr(result, 'value', result)
    if type(value) is not int or not -(2**31) <= value <= 2**31-1:
        _fail('JOB_LIMITS_SDK_UNKNOWN', 'SDK BOOL return domain is malformed')
    if value == 0:
        _fail('JOB_LIMITS_SDK_FAILED', 'native Job limits call returned failure')


def _query(api, job):
    _abi(); limits = EXTENDED_LIMIT(); returned = C.c_uint32(0)
    _bool_result(api.k.QueryInformationJobObject(job, 9, C.byref(limits),
        C.sizeof(limits), C.byref(returned)))
    if returned.value != C.sizeof(limits):
        _fail('JOB_LIMITS_READBACK_UNKNOWN', 'native Job limits readback length differs from exact ABI')
    return limits


def _job_ids(api, job):
    ids = api._job_process_ids(job)
    if (type(ids) is not list or len(ids) > 4096
            or any(type(pid) is not int or not 0 < pid < 2**32 for pid in ids)
            or len(set(ids)) != len(ids)):
        _fail('JOB_LIMITS_MEMBERSHIP_UNKNOWN', 'whole Job membership readback is malformed')
    return ids


def _check_limits(limits, targets):
    b = limits.BasicLimitInformation
    if (b.LimitFlags != FLAGS or b.ActiveProcessLimit != targets.active_process_limit
            or limits.ProcessMemoryLimit != targets.process_memory_limit_bytes
            or limits.JobMemoryLimit != targets.job_memory_limit_bytes
            or any((b.PerProcessUserTimeLimit, b.PerJobUserTimeLimit, b.MinimumWorkingSetSize,
                    b.MaximumWorkingSetSize, b.Affinity, b.PriorityClass, b.SchedulingClass))):
        _fail('JOB_LIMITS_READBACK_MISMATCH', 'actual Job flags/targets differ from exact trusted limits')


def _native_job_handle(raw):
    value = raw.value if type(raw) is C.c_void_p else raw
    if type(value) is not int or not 0 < value < 2**64-1:
        _fail('JOB_LIMITS_HANDLE_UNKNOWN', 'CreateJobObject returned an unclassified Win64 HANDLE')
    return value


def _require_original_job_close_safe(api, registry, entry, acquired_value, handle):
    """The captured closer gates every registry release route before native effect."""
    from ._windows_owned_resources import _OwnedResource
    value = _native_job_handle(handle)
    if (type(entry) is not _OwnedResource or entry.registry is not registry
            or registry.owner is not api or entry.adapter is not api
            or entry.value != acquired_value or value != acquired_value
            or _native_job_handle(entry.handle) != acquired_value or entry.label != 'owned_session_job'
            or registry._entries.get(entry.seq) is not entry
            or entry.state not in {'ACQUIRED', 'UNKNOWN'}):
        _fail('JOB_LIMITS_OWNER_UNKNOWN', 'original Job release acquisition binding changed')
    if any(other is not entry for other in registry.unresolved()):
        _fail('JOB_LIMITS_CLEANUP_UNKNOWN', 'original ancillary native resources remain owned')
    if (getattr(api, '_unclassified_create_handles', None)
            or getattr(api, '_retained_launch_cleanup', None)
            or any(not parent.closed for parent in getattr(api, '_retained_parent_bindings', {}).values())):
        _fail('JOB_LIMITS_CLEANUP_UNKNOWN', 'original launch cleanup remains unconfirmed')
    if _job_ids(api, value):
        _fail('JOB_LIMITS_CLEANUP_UNKNOWN', 'original whole Job membership is nonempty')
    for child in getattr(api, '_retained_children', {}).values():
        if type(child.poll()) is not int:
            _fail('JOB_LIMITS_CLEANUP_UNKNOWN', 'original retained child exit is unknown')
    return value


class _JobCloseCall:
    """Phase of the existing captured closer; the registry still owns the HANDLE.

    No destructor, replacement acquisition, serialized state or launch power.
    NATIVE_UNKNOWN is set before invoking the captured SDK callable, and stays
    sticky through every exception or unconfirmed return normalization.
    """
    def __init__(self, api, registry, original_closer):
        self.api=api;self.registry=registry;self.original_closer=original_closer
        self.entry=None;self.value=None;self.binding=None
        self.phase='PRE_SDK';self.first_error=None

    def _raise_first(self, error):
        if self.first_error is None:self.first_error=error
        if error is self.first_error:raise error
        raise self.first_error from error

    def _check_original(self, handle):
        from ._windows_owned_resources import _OwnedResource
        entry=self.entry;registry=self.registry
        if type(self.phase) is not str or self.phase not in {'PRE_SDK','NATIVE_UNKNOWN','NATIVE_CONFIRMED'}:
            _fail('JOB_LIMITS_RELEASE_UNKNOWN','original Job close phase is unclassified')
        if (type(entry) is not _OwnedResource or entry.registry is not registry
                or registry.owner is not self.api or entry.adapter is not self.api
                or entry.closer is not self or entry.mode!='VOID' or entry.label!='owned_session_job'
                or entry.value!=self.value or _native_job_handle(handle)!=self.value
                or _native_job_handle(entry.handle)!=self.value
                or entry.state not in {'ACQUIRED','UNKNOWN','RELEASED'}):
            _fail('JOB_LIMITS_OWNER_UNKNOWN','original captured Job close binding changed')
        registered=registry._entries.get(entry.seq)
        if (registered is not entry and not (registered is None and self.phase=='NATIVE_CONFIRMED')
                or entry.state=='RELEASED' and self.phase!='NATIVE_CONFIRMED'):
            _fail('JOB_LIMITS_OWNER_UNKNOWN','original Job registry completion is unconfirmed')
        if self.phase=='NATIVE_UNKNOWN':
            error=self.first_error
            if error is not None:raise error
            _fail('JOB_LIMITS_RELEASE_UNKNOWN','original CloseHandle may already be effective')

    def __call__(self, handle):
        with self.registry._lock:
            try:
                self._check_original(handle)
                if self.phase=='NATIVE_CONFIRMED':return
                value=_require_original_job_close_safe(self.api,self.registry,self.entry,self.value,handle)
                self.phase='NATIVE_UNKNOWN'
                _bool_result(self.original_closer(value))
                self.phase='NATIVE_CONFIRMED'
            except BaseException as error:
                self._raise_first(error)


def _original_job_close_call(api, job, *, expected=None):
    """Local original-object bridge for factory cleanup, never a receipt flag."""
    from ._windows_owned_resources import registry_for
    registry=registry_for(api)
    call=getattr(api,'_job_close_call',None)
    if (type(call) is not _JobCloseCall or call.api is not api or call.registry is not registry
            or expected is not None and call is not expected or call.value!=job
            or type(call.binding) is not dict or call.binding.get('entry') is not call.entry
            or call.binding.get('close_call') is not call):
        _fail('JOB_LIMITS_OWNER_UNKNOWN','original Job close callback/state binding changed')
    with registry._lock:
        call._check_original(job)
        binding=getattr(api,'_job_limit_bindings',{}).get(job)
        if binding is not call.binding and not (binding is None and call.phase=='NATIVE_CONFIRMED'):
            _fail('JOB_LIMITS_OWNER_UNKNOWN','original Job configuration binding changed')
    return call


def _complete_confirmed_job_release(call):
    """Finish only this Job's local registry bookkeeping after known SDK success."""
    if call.phase!='NATIVE_CONFIRMED':
        _fail('JOB_LIMITS_RELEASE_UNKNOWN','native Job close success has not been confirmed')
    call._check_original(call.value)
    entry=call.entry;registry=call.registry
    entry.state='RELEASED'
    if registry._entries.get(entry.seq) is entry:registry._entries.pop(entry.seq)
    entry.keepalive=()


def create_owned_job(api, profile):
    targets = require_limits(profile); _abi()
    from ._windows_owned_resources import registry_for
    registry = registry_for(api)
    with registry._lock:
        return _create_under_original_registry_lock(api, profile, targets, registry)


def _create_under_original_registry_lock(api, profile, targets, registry):
    if registry.unresolved():
        _fail('JOB_LIMITS_OWNER_UNKNOWN', 'unresolved original native resources prohibit another Job')
    bindings = getattr(api, '_job_limit_bindings', None)
    if bindings is None:
        bindings = {}; api._job_limit_bindings = bindings
    if bindings:
        _fail('JOB_LIMITS_OWNER_UNKNOWN', 'original launch API already owns its session Job')
    # Capture original callback before acquiring a possibly effective native return.
    close_call = _JobCloseCall(api, registry, api.k.CloseHandle)
    raw = api.k.CreateJobObjectW(None, None)
    # Capture exact return before validating it. No fallback/close retry.
    entry = registry.acquire(api, raw, close_call, mode='VOID', label='owned_session_job',
                             keepalive=(profile,), value_reader=_native_job_handle)
    close_call.entry=entry;close_call.value=entry.value
    api._job_close_call=close_call  # Retain known success even if final binding-pop raises after mutation.
    job = entry.value
    binding = {'entry': entry, 'close_call': close_call, 'profile': profile, 'source_sha256': profile.source_sha256,
               'profile_sha256': profile.sha256, 'target_sha256': targets.sha256, 'state': 'CONFIGURING'}
    close_call.binding=binding
    bindings[job] = binding
    try:
        before = _query(api, job)
        if bytes(before) != bytes(EXTENDED_LIMIT()) or _job_ids(api, job) != []:
            _fail('JOB_LIMITS_JOB_NOT_FRESH', 'only the original newly acquired empty unconfigured Job may be changed')
        desired = EXTENDED_LIMIT(); desired.BasicLimitInformation.LimitFlags = FLAGS
        desired.BasicLimitInformation.ActiveProcessLimit = targets.active_process_limit
        desired.ProcessMemoryLimit = targets.process_memory_limit_bytes
        desired.JobMemoryLimit = targets.job_memory_limit_bytes
        _bool_result(api.k.SetInformationJobObject(job, 9, C.byref(desired), C.sizeof(desired)))
        _check_limits(_query(api, job), targets)
        if _job_ids(api, job) != []:
            _fail('JOB_LIMITS_JOB_NOT_FRESH', 'child appeared during owned Job configuration')
        binding['state'] = 'CONFIGURED'
    except BaseException as error:
        binding['state'] = 'UNKNOWN'; binding['error'] = error
        raise
    return job


def require_configured_job(api, job, profile, *, for_child_birth=True):
    targets = require_limits(profile)
    from ._windows_owned_resources import registry_for
    binding = getattr(api, '_job_limit_bindings', {}).get(job)
    _native_job_handle(job)
    entry = registry_for(api).find(api, job)
    if (type(binding) is not dict or set(binding) != {'entry', 'close_call', 'profile', 'source_sha256',
            'profile_sha256', 'target_sha256', 'state'} or binding['entry'] is not entry or entry is None
            or entry.state != 'ACQUIRED' or entry.label != 'owned_session_job'
            or binding['profile'] is not profile or binding['source_sha256'] != profile.source_sha256
            or binding['profile_sha256'] != profile.sha256 or binding['target_sha256'] != targets.sha256
            or binding['state'] != 'CONFIGURED'):
        _fail('JOB_LIMITS_OWNER_UNKNOWN', 'child requires its exact original owned configured Job/profile')
    if _original_job_close_call(api, job).phase!='PRE_SDK':
        _fail('JOB_LIMITS_OWNER_UNKNOWN','original Job close has already entered the SDK')
    try:
        _check_limits(_query(api, job), targets)
        if (len(_job_ids(api, job)) >= targets.active_process_limit if for_child_birth
                else len(_job_ids(api, job)) > targets.active_process_limit):
            _fail('JOB_LIMITS_ACTIVE_CAPACITY_EXHAUSTED', 'owned Job has no active-process capacity before child birth')
    except BaseException as error:
        binding['state'] = 'UNKNOWN'; binding['error'] = error
        raise
    return {'state': 'CONFIGURED', 'native': False, 'verification': 'IMPLEMENTED_UNVERIFIED',
            'profile_sha256': profile.sha256, 'job_limits_sha256': targets.sha256,
            'memory_semantics': 'COMMITTED_MEMORY_LIMITS_ONLY'}


def close_owned_job(api, job):
    from ._windows_owned_resources import registry_for
    registry=registry_for(api)
    with registry._lock:
        call=_original_job_close_call(api,job)
        binding=call.binding
        try:
            if call.phase!='NATIVE_CONFIRMED':registry.retry_owned(call.entry,api)
            _complete_confirmed_job_release(call)
            binding['state']='CLOSED'
            if api._job_limit_bindings.get(job) is binding:api._job_limit_bindings.pop(job)
        except BaseException as error:
            call._raise_first(error)
