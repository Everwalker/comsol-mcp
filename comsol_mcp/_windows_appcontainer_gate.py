"""Original SCM profile lifetime/terminal lock producer, never Delete or Resume.

Strong records cover this runtime's factories, including removed dictionary
entries. A terminal pause is irreversible here; settlement cannot re-enable
births on a deleted or uncertain old profile. No storage or native API lives
in this module.
"""
from __future__ import annotations

from contextlib import contextmanager
import threading

from ._execution_contract import ExecutionContractError

_SEAL = object()
_GATES = {}
_FACTORIES = {}
MAX_PARTICIPANTS = 256


def _fail(reason, message):
    raise ExecutionContractError('T037_POLICY_UNVERIFIED', message, safe_retry=False,
        stage='pre_dispatch', details={'phase':'PROFILE_RUNTIME_LIFETIME', 'reasons':[reason]})


class _ProfileGate:
    def __init__(self, binding, *, seal):
        if seal is not _SEAL:
            _fail('PROFILE_GATE_ORIGINAL_REQUIRED', 'only the original SCM binding creates this coordinator')
        self._seal=seal;self.binding=binding;self.runtime,self.daemon,self.profile,self.provider=binding[:4]
        self.namespace=(self.profile.service_account_sid,
            self.profile.windows_policy.appcontainer_profile_name,self.profile.appcontainer_sid)
        self.participants={};self.state='ACTIVE';self.activity=None;self.depth=0
        self.pause=None;self.first_error=None

    def check(self, *, live):
        from ._windows_t037_broker import _require_runtime_identity
        from ._windows_t037_admission import _require_daemon_binding
        if (self._seal is not _SEAL or _GATES.get(id(self.runtime)) is not self
                or getattr(self.runtime,'_profile_gate',None) is not self):
            _fail('PROFILE_GATE_ORIGINAL_REQUIRED', 'original runtime coordinator was replaced')
        _require_runtime_identity(self.runtime)
        runtime,daemon,profile,provider,authority,path,source,identity,digest,provider_digest=self.binding
        if (runtime._daemon_binding is not self.binding or daemon._windows_t037_scm_binding is not self.binding
                or daemon._windows_t037_scm_runtime is not runtime
                or profile.source_sha256!=source or dict(profile.source_identity)!=identity
                or profile.sha256!=digest or provider.config_sha256!=provider_digest
                or self.namespace!=(profile.service_account_sid,profile.windows_policy.appcontainer_profile_name,profile.appcontainer_sid)):
            _fail('PROFILE_GATE_BINDING_CHANGED', 'original runtime/profile/source/provider identity changed')
        if live:
            _require_daemon_binding(self.binding)
            provider._assert_stable()

    def require_active(self):
        self.check(live=False)
        if self.state!='ACTIVE':
            _fail('PROFILE_GATE_PAUSED', 'this original profile lifetime no longer admits activity')
        self.check(live=True)


class _Participant:
    def __init__(self, gate, factory, binding, *, seal):
        if seal is not _SEAL:
            _fail('PROFILE_FACTORY_ORIGINAL_REQUIRED', 'participant requires its original registration')
        self._seal=seal;self.gate=gate;self.factory=factory;self.binding=binding;self.key=binding[2]
        self.observers=factory._prebirth_observers;self.observer_refs=tuple(self.observers)
        self.api=factory._api;self.job_call=None;self.close_returned=False
        self.terminal=None;self.terminal_error=None;self.close_thread=None

    def check(self, *, registered=False, acquiring_api=False):
        factory=self.factory;gate=self.gate
        if (self._seal is not _SEAL or _FACTORIES.get(id(factory)) is not self
                or gate.participants.get(id(factory)) is not self
                or getattr(factory,'_profile_participant',None) is not self
                or factory._scm_binding is not self.binding or self.binding[0] is not gate.binding
                or self.binding[1] is not gate.daemon or self.binding[3] is not factory
                or factory.profile is not gate.profile or factory.session_id!=self.key[1]
                or factory._prebirth_observers is not self.observers
                or tuple(self.observers)!=self.observer_refs
                or factory._api is not self.api and not (acquiring_api and self.api is None and not self.close_returned)):
            _fail('PROFILE_FACTORY_BINDING_CHANGED', 'original factory/observation/API participant changed')
        if registered and gate.daemon._session_t037_process_factories.get(self.key) is not factory:
            _fail('PROFILE_FACTORY_BINDING_CHANGED', 'birth requires the registered original factory')

    def capture_observers(self):
        if self.factory._prebirth_observers is not self.observers:
            _fail('PROFILE_FACTORY_BINDING_CHANGED', 'original observation list was replaced')
        current=tuple(self.observers)
        if current[:len(self.observer_refs)]!=self.observer_refs:
            _fail('PROFILE_FACTORY_BINDING_CHANGED', 'an original observation lifetime was removed or replaced')
        self.observer_refs=current  # Actual preflight outputs stay strongly retained even on failure.

    def acknowledge_close(self):
        # This is only the post-return producer. It never calls close/poll or
        # reconstructs an owner from a terminal receipt/Job number.
        self.check()
        if not self.close_returned:
            _fail('PROFILE_FACTORY_NOT_CLOSED', 'the original factory close has not returned')
        if self.terminal_error is not None:raise self.terminal_error
        try:snapshot=_terminal_snapshot(self)
        except BaseException as error:
            self.terminal_error=error
            raise
        if self.terminal is not None and snapshot!=self.terminal:
            _fail('PROFILE_FACTORY_TERMINAL_CHANGED', 'original terminal resource bindings changed')
        self.terminal=snapshot


def _bind_original_profile_gate(binding):
    from ._windows_t037_admission import _require_daemon_binding
    runtime,*_=_require_daemon_binding(binding)
    with runtime._lock:
        _require_daemon_binding(binding)
        if id(runtime) in _GATES or getattr(runtime,'_profile_gate',None) is not None:
            _fail('PROFILE_GATE_REUSED', 'the original runtime has already bound its profile lifetime')
        gate=_ProfileGate(binding,seal=_SEAL)
        _GATES[id(runtime)]=gate
        runtime._profile_gate=gate


def _register_factory(factory, binding):
    runtime=binding[0][0];gate=_GATES.get(id(runtime))
    if type(gate) is not _ProfileGate:
        _fail('PROFILE_GATE_ORIGINAL_REQUIRED', 'factory registration requires the original profile coordinator')
    with _runtime_transition(runtime):
        _active_transition(gate)
        if gate.activity is not None:
            _fail('PROFILE_ACTIVITY_BUSY', 'factory enrollment cannot overlap an original reservation')
    gate.require_active()  # Current provider/file observations never run under the runtime lock.
    with _runtime_transition(runtime):
        gate.check(live=False)
        if len(gate.participants)>=MAX_PARTICIPANTS:
            _fail('PROFILE_FACTORY_BUDGET_EXHAUSTED', 'bounded original factory participant budget is exhausted')
        _active_transition(gate)
        if gate.activity is not None:
            _fail('PROFILE_ACTIVITY_BUSY', 'factory enrollment cannot overlap an original reservation')
        if (id(factory) in _FACTORIES or getattr(factory,'_profile_participant',None) is not None
                or factory._closed or factory._scm_binding is not None
                or factory.profile is not gate.profile or binding[1] is not gate.daemon
                or binding[3] is not factory or binding[2][1] != factory.session_id
                or gate.daemon._session_t037_process_factories.get(binding[2]) is not factory):
            _fail('PROFILE_FACTORY_ORIGINAL_REQUIRED', 'a fresh original registered factory is required')
        for original in gate.participants.values():
            if original.key==binding[2]:
                _require_terminal(original)
        participant=_Participant(gate,factory,binding,seal=_SEAL)
        if factory._job is not None:
            # Some original internal consumers already hold their own Job.
            # Borrow that captured creation/close object, never adopt a bare
            # number or create a replacement owner at registration.
            from ._windows_job_limits import _original_job_close_call
            call=_original_job_close_call(factory._api,factory._job)
            if (call.binding.get('profile') is not factory.profile
                    or call.binding.get('source_sha256')!=factory.profile.source_sha256
                    or call.binding.get('profile_sha256')!=factory.profile.sha256
                    or call.phase!='PRE_SDK' or call.entry.state!='ACQUIRED'):
                _fail('PROFILE_FACTORY_ORIGINAL_REQUIRED', 'preexisting Job must be this original owned configured lifetime')
            participant.job_call=call
        gate.participants[id(factory)]=participant;_FACTORIES[id(factory)]=participant
        factory._scm_binding=binding;factory._profile_participant=participant


def _participant(factory, *, optional=False):
    from ._windows_t037 import T037SessionProcessFactory
    if type(factory) is not T037SessionProcessFactory:
        _fail('PROFILE_FACTORY_ORIGINAL_REQUIRED', 'only the exact original factory participates')
    participant=_FACTORIES.get(id(factory))
    if participant is None:
        if (optional and factory._scm_binding is None
                and getattr(factory,'_profile_participant',None) is None):return None
        _fail('PROFILE_FACTORY_ORIGINAL_REQUIRED', 'no original participant; metadata cannot replace registration')
    if type(participant) is not _Participant or participant.factory is not factory:
        _fail('PROFILE_FACTORY_ORIGINAL_REQUIRED', 'foreign participant is not an original factory')
    return participant


@contextmanager
def _runtime_transition(runtime):
    if not runtime._lock.acquire(blocking=False):
        _fail('PROFILE_ACTIVITY_BUSY', 'runtime transitions do not wait for other activity')
    try:yield
    finally:runtime._lock.release()


def _active_transition(gate):
    from ._windows_t037_admission import _require_daemon_binding
    gate.check(live=False)
    if gate.state != 'ACTIVE':
        _fail('PROFILE_GATE_PAUSED', 'this original profile lifetime no longer admits activity')
    _require_daemon_binding(gate.binding)  # Pure original-object/runtime validation, no SDK observation.


def _check_factory_activity(factory):
    from ._windows_t037_admission import _require_factory_runtime
    participant=_participant(factory);gate=participant.gate
    with _runtime_transition(gate.runtime):
        _require_factory_runtime(factory);_active_transition(gate)
        participant.check(registered=True,acquiring_api=True)
        if gate.activity != (participant,threading.get_ident()) or gate.depth < 1:
            _fail('PROFILE_ACTIVITY_ORIGINAL_REQUIRED','same-thread original activity reservation required')
    return participant


@contextmanager
def _factory_activity_scope(factory):
    from ._windows_t037_admission import _require_factory_runtime
    runtime,daemon,*_=_require_factory_runtime(factory)  # Never wait under the global condition.
    participant=_participant(factory);gate=participant.gate;thread=threading.get_ident()
    with _runtime_transition(runtime):
        if participant.close_returned:
            _fail('PROFILE_FACTORY_NOT_ACTIVE', 'an original close return permanently ends this factory activity')
        nested=gate.activity is not None and gate.activity[1]==thread
        if gate.activity is not None and (not nested or gate.activity[0] is not participant
                                         or participant.close_thread is not None):
            _fail('PROFILE_ACTIVITY_BUSY','original profile activity is already reserved')
        if nested:
            _require_factory_runtime(factory);participant.check(registered=True,acquiring_api=True);_active_transition(gate)
            gate.depth+=1
    if nested:
        try:
            gate.require_active()
            yield
        finally:
            with runtime._lock:gate.depth-=1
        return
    with daemon._dispatch_condition:
        if daemon._closing:
            _fail('PROFILE_GATE_CLOSING', 'closing daemon cannot admit profile activity')
        daemon._worker_admission_inflight+=1
    try:
        with _runtime_transition(runtime):
            _require_factory_runtime(factory);participant.check(registered=True,acquiring_api=True);_active_transition(gate)
            if gate.activity is not None:
                _fail('PROFILE_ACTIVITY_BUSY', 'original runtime activity did not settle')
            gate.activity=(participant,thread);gate.depth=1
        try:
            gate.require_active()
            yield  # Reservation/inflight stay rooted; SDK/waits have no runtime/global lock.
        finally:
            with runtime._lock:
                try:participant.capture_observers()
                finally:gate.depth=0;gate.activity=None
    finally:
        with daemon._dispatch_condition:
            daemon._worker_admission_inflight-=1;daemon._dispatch_condition.notify_all()


def _record_factory_api(factory, api):
    participant=_participant(factory);gate=participant.gate
    if gate.activity!=(participant,threading.get_ident()) or factory._api is not api:
        _fail('PROFILE_FACTORY_BINDING_CHANGED', 'API binding requires the original active factory call')
    if participant.api is not None and participant.api is not api:
        _fail('PROFILE_FACTORY_BINDING_CHANGED', 'the original factory API cannot be replaced')
    participant.api=api


def _record_factory_job(factory, api, job):
    from ._windows_job_limits import _original_job_close_call
    participant=_participant(factory)
    if participant.gate.activity!=(participant,threading.get_ident()) or participant.api is not api or factory._job is not job:
        _fail('PROFILE_FACTORY_BINDING_CHANGED', 'Job binding requires the original factory acquisition')
    call=_original_job_close_call(api,job,expected=participant.job_call)
    if participant.job_call is not None and participant.job_call is not call:
        _fail('PROFILE_FACTORY_BINDING_CHANGED', 'the original captured Job cannot be replaced')
    participant.job_call=call


@contextmanager
def _factory_close_scope(factory):
    participant=_participant(factory,optional=True)
    if participant is None:
        yield None  # Existing guarded legacy software cleanup, never a pause participant.
        return
    gate=participant.gate;runtime=gate.runtime;daemon=gate.daemon;thread=threading.get_ident()
    if gate.activity is not None and gate.activity[1]==thread or participant.close_thread==thread:
        _fail('PROFILE_CLOSE_REENTRANT', 'close cannot retire its own in-flight factory activity')
    with daemon._dispatch_condition:
        daemon._worker_admission_inflight+=1  # Cleanup is permitted during shutdown.
    try:
        with _runtime_transition(runtime):
            gate.check(live=False);participant.check()
            if gate.activity is not None or participant.close_thread is not None:
                _fail('PROFILE_ACTIVITY_BUSY','close cannot retire or wait on a reserved original activity')
            participant.close_thread=thread
            gate.activity=(participant,thread);gate.depth=1
        try:yield participant
        finally:
            with runtime._lock:
                participant.close_thread=None;gate.depth=0;gate.activity=None
    finally:
        with daemon._dispatch_condition:
            daemon._worker_admission_inflight-=1;daemon._dispatch_condition.notify_all()


def _terminal_snapshot(participant):
    from ._windows_t037 import T037SessionProcessFactory
    return T037SessionProcessFactory._profile_terminal_snapshot(participant.factory,participant)


def _require_terminal(participant):
    participant.check()
    if not participant.close_returned or participant.terminal is None or participant.close_thread is not None:
        _fail('PROFILE_FACTORY_NOT_CLOSED', 'every original factory must complete its original close and acknowledgement')
    if _terminal_snapshot(participant)!=participant.terminal:
        _fail('PROFILE_FACTORY_TERMINAL_CHANGED', 'original terminal state changed after close')


class _TerminalPause:
    def __init__(self, gate, participants, *, seal):
        if seal is not _SEAL:_fail('PROFILE_PAUSE_ORIGINAL_REQUIRED','original coordinator required')
        self._seal=seal;self.gate=gate;self.participants=participants;self.state='PAUSED';self.error=None

    def _check_locked(self):
        gate=self.gate
        if (self._seal is not _SEAL or gate.pause is not self or gate.state!='PAUSED'
                or self.state!='PAUSED' or tuple(gate.participants.values())!=self.participants
                or gate.activity is not None or gate.depth):
            _fail('PROFILE_PAUSE_INVALID','original terminal pause lifetime changed')
        gate.check(live=False)
        known={id(p.factory):p.factory for p in self.participants}
        if any(known.get(id(f)) is not f for f in gate.daemon._session_t037_process_factories.values()):
            _fail('PROFILE_FACTORY_UNREGISTERED','an unregistered factory appeared during profile pause')
        for participant in self.participants:_require_terminal(participant)

    def check(self):
        gate=self.gate
        try:
            with gate.runtime._lock:self._check_locked()
            gate.check(live=True)  # Original provider/source observation outside the runtime lock.
            with gate.runtime._lock:self._check_locked()
            return self
        except BaseException as error:
            with gate.runtime._lock:
                if self.error is None:self.error=error
                if gate.first_error is None:gate.first_error=error
                gate.state='UNKNOWN';self.state='INVALID'
            raise

    def settle(self):
        self.check()
        with self.gate.runtime._lock:
            self._check_locked();self.gate.state='SETTLED';self.state='SETTLED'

    def fail(self, error):
        with self.gate.runtime._lock:
            if self.gate.pause is not self or self._seal is not _SEAL:
                _fail('PROFILE_PAUSE_INVALID','only the original pause may retain its failure')
            if self.error is None:self.error=error
            if self.gate.first_error is None:self.gate.first_error=error
            self.gate.state='UNKNOWN';self.state='INVALID'


def _pause_terminal_profile(runtime):
    gate=_GATES.get(id(runtime))
    if type(gate) is not _ProfileGate or gate.runtime is not runtime:
        _fail('PROFILE_GATE_ORIGINAL_REQUIRED','only the original bound runtime can pause its profile')
    if not runtime._lock.acquire(blocking=False):
        _fail('PROFILE_ACTIVITY_BUSY','profile pause does not wait for in-flight runtime work')
    try:
        _active_transition(gate)
        if gate.activity is not None or gate.depth:
            _fail('PROFILE_ACTIVITY_BUSY','profile pause cannot run inside its own factory activity')
    finally:runtime._lock.release()
    gate.require_active()
    with _runtime_transition(runtime):
        _active_transition(gate)
        if gate.activity is not None or gate.depth:
            _fail('PROFILE_ACTIVITY_BUSY','profile pause cannot overlap an original reservation')
        participants=tuple(gate.participants.values())
        known={id(p.factory):p.factory for p in participants}
        if any(known.get(id(f)) is not f for f in gate.daemon._session_t037_process_factories.values()):
            _fail('PROFILE_FACTORY_UNREGISTERED','every current factory requires its original participant')
        for participant in participants:_require_terminal(participant)
        pause=_TerminalPause(gate,participants,seal=_SEAL)
        gate.pause=pause;gate.state='PAUSED'
        return pause
