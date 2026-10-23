"""Connected original-child B diagnostics. Never admission or Resume authority.

Only the normal original V4 factory/held-A lifetime can acquire this observer.
All SDK queries remain the existing WindowsChildEvidenceApi implementation.
Undefined token targets are explicit; matching finite facts are UNQUALIFIED.
"""
from __future__ import annotations

from dataclasses import dataclass, field
import hashlib
import json
import threading
from types import MappingProxyType

from . import _windows_t037_child_evidence as evidence
from ._execution_contract import ExecutionContractError
from ._windows_owned_resources import registry_for

_SEAL = object()
_UNDEFINED = (
    'token_modified_id_absolute', 'privilege_allowlist', 'restricted_sid_allowlist',
    'group_attribute_policy', 'integrity_level_target', 'mandatory_policy_target',
    'actual_C_and_purpose_provenance', 'native_controls',
)


def _fail(code, message):
    raise ExecutionContractError(code, message, safe_retry=False, stage='pre_dispatch',
        details={'phase': 'ORIGINAL_CHILD_B_DIAGNOSTIC', 'reasons': [code]})


@dataclass(frozen=True)
class ConnectedChildBObservation:
    observations: evidence.ChildObservations | None
    finite_comparisons: tuple[evidence.Comparison, ...]
    issues: tuple[evidence.Issue, ...]
    finite_state: str
    creation_parameter_digests: tuple[tuple[str, str], ...]
    undefined_targets: tuple[str, ...] = field(default=_UNDEFINED, init=False)
    qualification: str = field(default='UNQUALIFIED', init=False)
    state: str = field(default='DIAGNOSTIC_ONLY_NOT_ADMISSION', init=False)


class _OriginalBObserver:
    """Actual in-memory observer/partial-result owner, not a proof value."""
    def __init__(self, process, factory, held, *, seal):
        if seal is not _SEAL:
            _fail('B_ORIGINAL_OWNER_REQUIRED', 'original launch observer required')
        self._seal = seal
        self.process, self.factory, self.held = process, factory, held
        self.launch_api, self.profile = process._api, factory.profile
        self.parent, self.job = process._parent_binding, process._original_job
        self.parent_context = (self.parent.launch_api, self.parent.query, self.parent.pid,
            getattr(self.parent.handle, 'value', self.parent.handle), self.parent.birth,
            getattr(self.parent, '_resource_owner', None))
        self.registry = registry_for(self.launch_api)
        self.thread = threading.get_ident()
        self.scope = self.registry.scope()
        self.api = self.borrowed = self.pin = self.result = self.error = self.launch_error = None
        self.state = 'ACQUIRED'


def _creation(process):
    """Use pre-CREATE immutable strings/digests, never the writable SDK buffer."""
    captured, digests = process._creation_parameters, process._creation_parameter_digests
    if (type(captured) is not tuple or len(captured) != 3
            or type(captured[0]) is not str or not captured[0]
            or type(captured[1]) is not tuple or not captured[1]
            or any(type(v) is not str or '\x00' in v for v in captured[1])
            or type(captured[2]) is not str or not captured[2] or '\x00' in captured[2]
            or type(digests) is not MappingProxyType
            or captured[0] != process._create_arguments[1]
            or captured[1] != tuple(process.args)):
        _fail('B_CREATION_PARAMETERS_CHANGED', 'original immutable creation parameters unavailable')
    expected = {
        'classification': 'ACTUAL_CREATEPROCESS_PARAMETERS_NOT_REMOTE_READBACK',
        'argv_sha256': hashlib.sha256(json.dumps(list(captured[1]), ensure_ascii=False,
            separators=(',', ':')).encode('utf-8')).hexdigest(),
        'command_line_sha256': hashlib.sha256(captured[2].encode('utf-16-le')).hexdigest(),
        'application_name_sha256': hashlib.sha256(captured[0].encode('utf-16-le')).hexdigest(),
    }
    if dict(digests) != expected:
        _fail('B_CREATION_PARAMETERS_CHANGED', 'original creation parameter digest differs')
    return captured[2], tuple(sorted(expected.items()))


def _current(owner):
    p, factory = owner.process, owner.factory
    if (owner._seal is not _SEAL or owner.thread != threading.get_ident()
            or getattr(p, '_connected_b_observer', None) is not owner
            or getattr(owner.launch_api, '_connected_b_observers', {}).get(id(p)) is not owner
            or p._api is not owner.launch_api or p._owner is not owner.launch_api
            or p._original_factory is not factory or p._original_profile is not owner.profile
            or factory.profile is not owner.profile or factory._api is not owner.launch_api
            or factory._job is not owner.job or p._job_handle is not owner.job
            or p._parent_binding is not owner.parent or owner.parent.closed
            or (owner.parent.launch_api, owner.parent.query, owner.parent.pid,
                getattr(owner.parent.handle, 'value', owner.parent.handle), owner.parent.birth,
                getattr(owner.parent, '_resource_owner', None)) != owner.parent_context
            or registry_for(owner.launch_api) is not owner.registry or not p.never_resumed):
        _fail('B_ORIGINAL_CONTEXT_CHANGED', 'original child/factory/profile/Job/parent/thread changed')
    p._require_observable()
    _creation(p)


def _result(observation, profile, digests, issues=()):
    comparisons = [] if observation is None else [c for c in observation.comparisons if c.name != 'expected_modified_id']
    token_before = observation.token_before if observation else None
    token_after = observation.token_after if observation else None
    policy = observation.child_policy if observation else None

    def token_checks(token, suffix):
        actual = type(token) is evidence.TokenObservation
        comparisons.extend((
            evidence.Comparison('token_user_'+suffix, token.user.sid.casefold() == profile.service_account_sid.casefold() if actual else None),
            evidence.Comparison('token_capabilities_'+suffix,
                {c.sid.casefold() for c in token.capabilities} == {s.casefold() for s in profile.capability_sids} if actual else None),
            evidence.Comparison('token_is_appcontainer_'+suffix, token.is_appcontainer is True if actual else None),
        ))

    token_checks(token_before, 'before'); token_checks(token_after, 'after')
    comparisons.append(evidence.Comparison('fixed_child_creation_policy',
        type(policy.flags) is int and policy.flags == 1
        and policy.no_child_process_creation is True
        and policy.audit_no_child_process_creation is False
        and policy.allow_secure_process_creation is False
        if type(policy) is evidence.ChildPolicyObservation else None))
    values = [c.matches for c in comparisons]
    all_issues = (observation.issues if observation else ()) + tuple(issues)
    finite = ('UNVERIFIED' if observation is None or all_issues or any(v is None for v in values) else
        'MISMATCH' if any(v is False for v in values) else 'MATCHED_FINITE_CHECKS_ONLY')
    return ConnectedChildBObservation(observation, tuple(comparisons), tuple(all_issues), finite, digests)


def observe_original_child_b(process, *, factory):
    """Acquire once from the actual V4 launch, with no caller-supplied query API."""
    from ._windows_t037 import _RetainedSuspendedChild, T037SessionProcessFactory, PROFILE_SCHEMA_V4
    from ._windows_t037_targets import _require_held_finite_a
    if (type(process) is not _RetainedSuspendedChild or type(factory) is not T037SessionProcessFactory
            or process._original_factory is not factory or process._original_profile is not factory.profile
            or factory.profile.wrapper_schema != PROFILE_SCHEMA_V4
            or process._process_entry is None or process._process_entry.thread_id != threading.get_ident()
            or hasattr(process, '_connected_b_observer')):
        _fail('B_ORIGINAL_OWNER_REQUIRED', 'B requires the original same-thread V4 child and factory exactly once')
    process._require_observable()
    raw_command, digests = _creation(process)
    held = _require_held_finite_a(factory)
    owner = _OriginalBObserver(process, factory, held, seal=_SEAL)
    if not hasattr(process._api, '_connected_b_observers'):
        process._api._connected_b_observers = {}
    process._api._connected_b_observers[id(process)] = owner
    process._connected_b_observer = owner
    try:
        _current(owner)
        # Root the actual adapter before its possibly failing initialization.
        owner.api = evidence.WindowsChildEvidenceApi.__new__(evidence.WindowsChildEvidenceApi)
        evidence.WindowsChildEvidenceApi.__init__(owner.api, owner_api=owner.launch_api)
        _current(owner)
        owner.borrowed, owner.pin = evidence.borrow_original_child(process, api=owner.api)
        observation = evidence.observe_child(owner.borrowed, owner.pin,
            expected_command_line=raw_command)  # Absolute modified-ID target is deliberately unspecified.
        owner.result = _result(observation, owner.profile, digests)
        unknown = [e for e in owner.registry.unresolved() if e.state == 'UNKNOWN']
        if unknown:
            owner.state = 'UNKNOWN'; owner.error = unknown[0].error
            raise owner.error  # No later query, A read or resource-release retry.
        _current(owner)
        if _require_held_finite_a(factory) is not held:
            _fail('B_ORIGINAL_CONTEXT_CHANGED', 'held finite A was replaced during B')
        owner.state = 'OBSERVED'
        return owner.result
    except (evidence.ObservationError, OSError) as error:
        owner.error = error
        if owner.state == 'UNKNOWN' or any(e.state == 'UNKNOWN' for e in owner.registry.unresolved()):
            owner.state = 'UNKNOWN'
            raise  # Do not turn an unresolved release into a new A/SDK query.
        owner.result = _result(None, owner.profile, digests, (getattr(error, 'issue',
            evidence.Issue('B', type(error).__name__, 'UNVERIFIED')),))
        # A known unavailable getter is a partial diagnostic, never permission.
        _current(owner)
        if _require_held_finite_a(factory) is not held:
            _fail('B_ORIGINAL_CONTEXT_CHANGED', 'held finite A was replaced during unavailable B')
        owner.state = 'UNVERIFIED'
        return owner.result
    except BaseException as error:
        owner.error = error
        if owner.result is None:
            owner.result = _result(None, owner.profile, digests,
                (evidence.Issue('B', type(error).__name__, 'UNVERIFIED'),))
        if owner.state != 'UNKNOWN': owner.state = 'REJECTED'
        raise


def settle_original_child_b(process, error=None):
    """No SDK calls: root unresolved owners, bound the known-settled diagnostic."""
    owner = getattr(process, '_connected_b_observer', None)
    if owner is None: return
    if (type(owner) is not _OriginalBObserver or owner._seal is not _SEAL
            or owner.process is not process or owner.thread != threading.get_ident()
            or getattr(process._api, '_connected_b_observers', {}).get(id(process)) is not owner):
        _fail('B_ORIGINAL_CONTEXT_CHANGED', 'original B cleanup binding changed')
    owner.launch_error = error
    if owner.error is None: owner.error = error
    unknown = any(e.state == 'UNKNOWN' for e in owner.registry.unresolved())
    if unknown or not process._closed or not owner.parent.closed:
        owner.state = 'UNKNOWN'
        return
    owner.state = 'RELEASED'
    process._api._last_connected_b_observer = owner
    process._api._connected_b_observers.pop(id(process))
