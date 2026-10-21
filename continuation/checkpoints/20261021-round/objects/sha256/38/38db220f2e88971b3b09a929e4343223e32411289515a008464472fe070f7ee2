"""Operator-selected committed-memory/active-process limits for the original Job.

A successful SDK readback is configuration evidence, never isolation or Resume
power. No time limit, RSS limit, shared-job mutation, replacement Job or retry.
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
    from ._windows_t037 import WindowsT037ProfileV2, PROFILE_SCHEMA_V3
    if type(profile) is not WindowsT037ProfileV2 or profile.job_limits is None or profile.wrapper_schema != PROFILE_SCHEMA_V3:
        _fail('JOB_LIMITS_NOT_CONFIGURED', 'explicit trusted v3 Job limits required before owned Job/child birth')
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
    if _job_ids(api, value):
        _fail('JOB_LIMITS_CLEANUP_UNKNOWN', 'original whole Job membership is nonempty')
    for child in getattr(api, '_retained_children', {}).values():
        if type(child.poll()) is not int:
            _fail('JOB_LIMITS_CLEANUP_UNKNOWN', 'original retained child exit is unknown')
    return value


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
    original_closer = api.k.CloseHandle
    entry_box = []
    acquired_value = []
    first_error = []
    def close_original(handle):
        with registry._lock:
            try:
                # Invalid/unclassified HANDLEs cannot reach an arbitrary native close.
                _native_job_handle(handle)
                if len(entry_box) != 1 or len(acquired_value) != 1:
                    _fail('JOB_LIMITS_OWNER_UNKNOWN', 'original Job acquisition remains unclassified')
                value = _require_original_job_close_safe(api, registry, entry_box[0], acquired_value[0], handle)
                _bool_result(original_closer(value))
            except BaseException as error:
                if not first_error:
                    first_error.append(error)
                if error is first_error[0]:
                    raise
                raise first_error[0] from error
    raw = api.k.CreateJobObjectW(None, None)
    # Capture exact return before validating it. No fallback/close retry.
    entry = registry.acquire(api, raw, close_original, mode='VOID', label='owned_session_job',
                             keepalive=(profile,), value_reader=_native_job_handle)
    entry_box.append(entry)
    acquired_value.append(entry.value)
    job = entry.value
    binding = {'entry': entry, 'profile': profile, 'source_sha256': profile.source_sha256,
               'profile_sha256': profile.sha256, 'target_sha256': targets.sha256, 'state': 'CONFIGURING'}
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
    if (type(binding) is not dict or set(binding) != {'entry', 'profile', 'source_sha256',
            'profile_sha256', 'target_sha256', 'state'} or binding['entry'] is not entry or entry is None
            or entry.state != 'ACQUIRED' or entry.label != 'owned_session_job'
            or binding['profile'] is not profile or binding['source_sha256'] != profile.source_sha256
            or binding['profile_sha256'] != profile.sha256 or binding['target_sha256'] != targets.sha256
            or binding['state'] != 'CONFIGURED'):
        _fail('JOB_LIMITS_OWNER_UNKNOWN', 'child requires its exact original owned configured Job/profile')
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
    binding = getattr(api, '_job_limit_bindings', {}).get(job)
    if type(binding) is not dict or 'entry' not in binding:
        _fail('JOB_LIMITS_OWNER_UNKNOWN', 'foreign or shared Job cannot be closed by this owner')
    entry = binding['entry']; registry = registry_for(api)
    if entry.registry is not registry or entry.adapter is not api or entry.value != job:
        _fail('JOB_LIMITS_OWNER_UNKNOWN', 'original Job acquisition binding changed')
    if entry.state == 'RELEASED':
        binding['state'] = 'CLOSED'; api._job_limit_bindings.pop(job)
        return
    # Explicit original closer retry, never a reconfiguration or child retry.
    registry.retry_owned(entry, api)
    binding['state'] = 'CLOSED'; api._job_limit_bindings.pop(job)
