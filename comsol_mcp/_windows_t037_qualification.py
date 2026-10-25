"""Original APPLY-owned purpose lifetimes. No serialized qualification authority.

Only the original admitted APPLY route may own a qualification lifetime. A
PROJECT purpose additionally requires its original successful live-C owner.
"""
from __future__ import annotations

from enum import Enum
from dataclasses import dataclass
import ipaddress
import ntpath
import threading
from uuid import uuid4

from ._execution_contract import ExecutionContractError
from . import _windows_t037_qualification_recipe as recipes

_SEAL = object()
_ENROLLMENTS = {}
_OPERATIONS = {}
_PURPOSES = {}
_PROBE_INPUTS = {}
_PROBE_IO = {}


def _fail(code, message):
    raise ExecutionContractError(code, message, safe_retry=False, stage='pre_dispatch')


class Purpose(Enum):
    QUALIFY_OS = 'QUALIFY_OS'
    QUALIFY_VENDOR = 'QUALIFY_VENDOR'
    PROJECT = 'PROJECT'


class _FixedOwner:
    _fixed = ()
    def __setattr__(self, name, value):
        if name in self._fixed and hasattr(self, name):
            _fail('T037_QUALIFICATION_OWNER_CHANGED', 'original qualification identity is immutable')
        object.__setattr__(self, name, value)


class _Enrollment(_FixedOwner):
    _fixed = ('daemon', 'request', 'record', 'executor', 'binding', 'runtime', 'gate',
              'profile', 'loaded', 'origin', 'provider', 'authority', 'recipe', 'plan', '_seal')

    def __init__(self, daemon, request, record, executor, recipe, *, seal):
        if seal is not _SEAL: _fail('T037_QUALIFICATION_OWNER_REQUIRED', 'original admission route required')
        from ._windows_t037_admission import _require_daemon_binding
        self.daemon, self.request, self.record, self.executor = daemon, request, record, executor
        self.binding = daemon._windows_t037_scm_binding
        self.runtime, _, self.profile, self.provider = _require_daemon_binding(self.binding)
        self.gate = self.runtime._profile_gate
        self.authority = daemon.project_authority
        self.origin = executor._trusted_profile_load_origin
        self.loaded = self.origin.profile_wrapper if self.origin is not None else None
        self.recipe, self.plan, self._seal = recipe, request.plan, seal
        self.operation = None

    def check(self, *, live=False):
        from ._control_daemon import ControlDaemon
        from ._windows_t037_broker import InternalHostControlRequest
        from ._windows_host_change import WindowsHostChangeExecutor, _TrustedProfileLoadOrigin
        from ._windows_t037_admission import _require_daemon_binding
        d,e,r,q = self.daemon,self.executor,self.record,self.request
        if (self._seal is not _SEAL or _ENROLLMENTS.get(id(e)) is not self
                or type(d) is not ControlDaemon or type(q) is not InternalHostControlRequest
                or type(e) is not WindowsHostChangeExecutor or e.software_only
                or q.phase != 'APPLY' or not q._used or q.plan is not self.plan
                or r.get('request') is not q or r.get('executor') is not e or r.get('first_cause') is not None
                or d._retained_host_control_executors.get(r.get('key')) is not r
                or d._windows_t037_scm_binding is not self.binding or q.runtime is not self.runtime
                or q.authority is not self.authority or e.authority is not self.authority
                or q.provider is not self.provider or e.provider is not self.provider
                or e.store is not d.store or e._qualification_enrollment is not self
                or type(self.origin) is not _TrustedProfileLoadOrigin
                or e._trusted_profile_load_origin is not self.origin
                or not self.origin.matches(self.loaded, d._windows_t037_profile_path)
                or e.profile is not self.loaded.host_resource_profile
                or self.loaded.source_sha256 != self.profile.source_sha256
                or dict(self.loaded.source_identity) != dict(self.profile.source_identity)
                or self.loaded.sha256 != self.profile.sha256
                or e.profile.sha256 != q.profile_sha256 or q.profile_source_sha256 != self.profile.source_sha256
                or recipes.from_plan(self.plan) != self.recipe):
            _fail('T037_QUALIFICATION_OWNER_CHANGED', 'original request/executor/profile/admission root changed')
        _require_daemon_binding(self.binding)
        self.gate.check(live=False)
        if self.gate.state != 'ACTIVE': _fail('T037_QUALIFICATION_NOT_ACTIVE', 'original profile gate is not active')
        self.recipe.require_manifest(self.profile.prepared_launch)
        self.recipe.require_manifest(self.loaded.prepared_launch)
        if live:
            # No SDK/file observation underneath runtime/global/Store locks.
            self.provider._assert_stable()
            from ._windows_t037 import load_trusted_profile
            current = load_trusted_profile(d._windows_t037_profile_path)
            if (current.source_sha256 != self.profile.source_sha256
                    or dict(current.source_identity) != dict(self.profile.source_identity)
                    or current.sha256 != self.profile.sha256):
                _fail('T037_QUALIFICATION_SOURCE_CHANGED', 'current protected source no longer matches original load')
            self.recipe.require_manifest(current.prepared_launch)
            _require_daemon_binding(self.binding)
        return self


class OriginalQualificationOperation(_FixedOwner):
    _fixed = ('enrollment', 'admission', 'thread', 'recipe', 'plan', '_seal')

    def __init__(self, enrollment, admission, *, seal):
        if seal is not _SEAL: _fail('T037_QUALIFICATION_OWNER_REQUIRED', 'original admitted executor required')
        self.enrollment,self.admission = enrollment,admission
        self.recipe,self.plan = enrollment.recipe,enrollment.plan
        self.thread = threading.get_ident();self._seal = seal
        self.state = 'ADMITTED';self.first_error = None;self.purposes = []
        self.os_result = self.vendor_result = self.live_c = None
        self.probe_io = []
        self.coordinator = None

    def unknown(self, error):
        if self.first_error is None: self.first_error = error
        self.state = 'UNKNOWN'
        return self.first_error

    def check(self, *, live=False):
        e=self.enrollment
        if (self._seal is not _SEAL or _OPERATIONS.get(id(self)) is not self
                or e.operation is not self or e.record.get('qualification') is not self
                or e.executor._qualification_operation is not self
                or self.thread != threading.get_ident()
                or self.state not in ('ADMITTED','RUNNING','READY') or self.first_error is not None):
            _fail('T037_QUALIFICATION_OWNER_CHANGED', 'original active qualification operation/thread required')
        e.check()
        if live:
            return self._check_live()
        return self

    def _check_live(self):
        e=self.enrollment
        try:
            e.check(live=True)
            row=e.daemon.store.get_operation(self.admission.operation_id)
            job=e.daemon.store.job(self.admission.job_id)
            if (row is None or job is None or job['job_id'] != self.admission.job_id
                    or row['operation'] != 'host.change.apply' or row['status'] not in ('QUEUED','RUNNING')
                    or row['metadata'].get('host_control',{}).get('admission_id') != self.admission.admission_id
                    or row['metadata'].get('host_control',{}).get('plan_sha256') != self.plan.sha256
                    or job['status'] != row['status'] or job['operation_id'] != self.admission.operation_id
                    or e.daemon.store.host_control_admission_state(self.admission.admission_id) not in ('ADMITTED','APPLIED')):
                _fail('T037_QUALIFICATION_ADMISSION_CHANGED', 'durable original operation/job is no longer active')
            return self
        except BaseException as error:
            raise self.unknown(error)

    def new_factory(self, purpose, *, _control_stage=None):
        self.check(live=True)
        if type(purpose) is not Purpose or purpose not in (Purpose.QUALIFY_OS,Purpose.QUALIFY_VENDOR):
            _fail('T037_QUALIFICATION_PURPOSE_INVALID', 'qualification cannot mint PROJECT purpose')
        if _control_stage is not None:
            from ._windows_t037_qualification_run import OriginalOsStage
            if type(_control_stage) is not OriginalOsStage or purpose is not Purpose.QUALIFY_OS:
                _fail('T037_QUALIFICATION_PURPOSE_INVALID', 'only an original fixed OS stage may select a second OS lifetime')
            _control_stage.check(self)
            if _control_stage.factory is not None or _control_stage.owner is not None:
                _fail('T037_QUALIFICATION_REPLAYED', 'original fixed OS stage has already entered')
        if any(owner.purpose is purpose and (getattr(owner, 'stage', None) is _control_stage
                or _control_stage is None or getattr(owner, 'stage', None) is None) for owner in self.purposes):
            _fail('T037_QUALIFICATION_REPLAYED', 'one original factory per bounded qualification purpose')
        e=self.enrollment
        key=(e.profile.host_resource_profile.project_id,'qual-'+uuid4().hex)
        factory=e.daemon._new_t037_session_factory(*key)
        try:
            owner=OriginalPurposeOwner(self,factory,purpose,stage=_control_stage,seal=_SEAL)
            self.purposes.append(owner)
            _PURPOSES[id(factory)]=owner
            factory._qualification_purpose=owner
            if _control_stage is not None:
                _control_stage.factory, _control_stage.owner = factory, owner
            owner.check()
        except BaseException as error:
            # Registered original factory remains rooted even before any native entry.
            raise self.unknown(error)
        return factory

    def after_resource_readback(self):
        try:
            self.check(live=True)
            if self.state != 'ADMITTED': _fail('T037_QUALIFICATION_REPLAYED','qualification cannot reenter')
            self.state='RUNNING'
            from ._windows_t037_qualification_run import OriginalQualificationRun
            self.coordinator = OriginalQualificationRun.__new__(OriginalQualificationRun)
            OriginalQualificationRun.__init__(self.coordinator, self)
            self.live_c = self.coordinator.execute()
            self.state = 'READY'
        except BaseException as error:
            raise self.unknown(error)

    def prepare_probe_io(self, factory, frame):
        """Original parent-only stdio; a frame or caller file tuple is not provenance."""
        owner = require_purpose(factory, purpose=Purpose.QUALIFY_OS, live=True)
        if owner.operation is not self or id(owner) in _PROBE_IO:
            _fail('T037_QUALIFICATION_REPLAYED', 'one original OS stdio acquisition required')
        from . import _windows_os_probe_protocol as wire
        request = wire.decode_request(frame, expected_nonce=owner.nonce, expected_recipe_sha256=self.recipe.sha256)
        validate_os_request_scope(owner.profile, request)
        value = OriginalProbeIO(owner, frame, seal=_SEAL)
        self.probe_io.append(value)
        _PROBE_IO[id(owner)] = value  # before private-path observation or first file acquisition
        value.prepare()
        return value


class OriginalPurposeOwner(_FixedOwner):
    _fixed = ('operation', 'factory', 'purpose', 'thread', 'binding', 'profile', 'participant', 'nonce', 'stage', '_seal')
    def __init__(self, operation, factory, purpose, *, stage=None, seal):
        if seal is not _SEAL: _fail('T037_QUALIFICATION_OWNER_REQUIRED','original purpose producer required')
        self.operation,self.factory,self.purpose=operation,factory,purpose
        self.thread=threading.get_ident();self.binding=factory._scm_binding
        self.profile=factory.profile;self.participant=factory._profile_participant
        self.nonce=uuid4().hex
        self.stage=stage
        self._seal=seal

    def check(self, *, live=False, terminal=False):
        f=self.factory;e=self.operation.enrollment
        if (self._seal is not _SEAL or _PURPOSES.get(id(f)) is not self
                or getattr(f,'_qualification_purpose',None) is not self
                or self not in self.operation.purposes or self.thread != threading.get_ident()
                or self.purpose not in (Purpose.QUALIFY_OS,Purpose.QUALIFY_VENDOR)
                or f._closed is not terminal or f.profile is not self.profile or self.profile is not e.profile
                or f._scm_binding is not self.binding or self.binding[0] is not e.binding
                or f._profile_participant is not self.participant
                or e.daemon._session_t037_process_factories.get(self.binding[2]) is not f):
            _fail('T037_QUALIFICATION_PURPOSE_CHANGED','same original factory/purpose/thread required')
        self.operation.check(live=live)
        if self.stage is not None and not terminal:
            self.stage.check(self.operation)
            if self.stage.factory is not f or self.stage.owner is not self:
                _fail('T037_QUALIFICATION_PURPOSE_CHANGED', 'original fixed stage changed')
        if terminal:
            from ._windows_appcontainer_gate import _participant, _require_terminal
            if _participant(f) is not self.participant:
                _fail('T037_QUALIFICATION_PURPOSE_CHANGED','original terminal participant changed')
            _require_terminal(self.participant)  # Original pure post-close producer, no SDK or PID query.
        return self


def enroll_executor(daemon, request, record, executor):
    from ._control_daemon import ControlDaemon
    from ._windows_t037_broker import InternalHostControlRequest
    from ._windows_host_change import WindowsHostChangeExecutor
    if type(daemon) is not ControlDaemon or type(request) is not InternalHostControlRequest or type(record) is not dict:
        _fail('T037_QUALIFICATION_OWNER_REQUIRED','original SCM route/request record required')
    recipe=recipes.from_plan(request.plan)
    if recipe is None: return None
    if type(executor) is not WindowsHostChangeExecutor or executor.software_only:
        _fail('T037_QUALIFICATION_OWNER_REQUIRED','actual native constructor and original SCM route required')
    if id(executor) in _ENROLLMENTS:
        _fail('T037_QUALIFICATION_REPLAYED','original executor already enrolled')
    owner=_Enrollment(daemon,request,record,executor,recipe,seal=_SEAL)
    _ENROLLMENTS[id(executor)]=owner
    executor._qualification_enrollment=owner
    owner.check()
    return owner


def prepare_apply(executor, plan):
    recipe=recipes.from_plan(plan)  # Validate optional closed recipe before baseline SDK/admission.
    owner=_ENROLLMENTS.get(id(executor))
    if recipe is None and owner is None: return
    if type(owner) is not _Enrollment or owner.plan is not plan or owner.recipe != recipe:
        _fail('T037_QUALIFICATION_OWNER_REQUIRED','recipe requires original SCM APPLY enrollment')
    owner.check()


def bind_admission(executor, admission):
    owner=_ENROLLMENTS.get(id(executor))
    if owner is None: return
    from ._windows_host_change import _ExecutionAdmission
    owner.check()
    if type(admission) is not _ExecutionAdmission or owner.operation is not None:
        _fail('T037_QUALIFICATION_REPLAYED','one original durable admission required')
    if executor.store.host_control_admission_state(admission.admission_id) != 'ADMITTED':
        _fail('T037_QUALIFICATION_ADMISSION_CHANGED','original admission did not commit')
    operation=OriginalQualificationOperation(owner,admission,seal=_SEAL)
    owner.operation=operation
    _OPERATIONS[id(operation)]=operation
    owner.record['qualification']=operation
    executor._qualification_operation=operation
    operation.check(live=True)


def before_apply_finish(executor, admission):
    owner=_ENROLLMENTS.get(id(executor))
    if owner is None: return
    operation=owner.operation
    if type(operation) is not OriginalQualificationOperation or operation.admission is not admission:
        _fail('T037_QUALIFICATION_OWNER_REQUIRED','original admitted qualification owner required')
    operation.after_resource_readback()


def retirement_known(record):
    operation=record.get('qualification')
    # No status flag alone discharges an original owner. The accepted complete
    # retirement producer is integrated with the peer dependencies, not guessed here.
    return operation is None


def require_purpose(factory, *, purpose=None, live=False):
    owner=_PURPOSES.get(id(factory))
    if type(owner) not in (OriginalPurposeOwner, OriginalProjectOwner):
        _fail('T037_QUALIFICATION_OWNER_REQUIRED','original purpose lifetime is unavailable')
    owner.check(live=live)
    if purpose is not None and owner.purpose is not purpose:
        _fail('T037_QUALIFICATION_PURPOSE_CHANGED','original purpose does not match this fixed producer')
    return owner


@dataclass(frozen=True)
class OriginalProbeInput:
    """Original purpose-bound input storage; bytes/frames alone are not authority."""
    owner: OriginalPurposeOwner
    frame: bytes
    stdio: tuple
    private_token: str
    io_owner: object
    _seal: object

    def check(self):
        if (self._seal is not _SEAL or _PROBE_INPUTS.get(id(self.owner)) is not self
                or self.owner.purpose is not Purpose.QUALIFY_OS
                or type(self.frame) is not bytes or type(self.stdio) is not tuple or len(self.stdio)!=3):
            _fail('T037_QUALIFICATION_INPUT_CHANGED','same original OS input storage required')
        self.owner.check()
        if (type(self.io_owner) is not OriginalProbeIO or _PROBE_IO.get(id(self.owner)) is not self.io_owner
                or self.io_owner.frame is not self.frame or self.io_owner.files != self.stdio):
            _fail('T037_QUALIFICATION_INPUT_CHANGED', 'original parent stdio/input owner changed')
        self.io_owner.check_input()
        from . import _windows_os_probe_protocol as wire
        request=wire.decode_request(self.frame,expected_nonce=self.owner.nonce,
            expected_recipe_sha256=self.owner.operation.recipe.sha256)
        validate_os_request_scope(self.owner.profile,request)
        return self


def validate_os_request_scope(profile, request):
    """Pure data comparison against approved finite material/roles, not an issuer."""
    from . import _windows_os_probe_protocol as wire
    from ._windows_t037_network import derive_session_network_intent
    from ._windows_t037_targets import require_contract
    manifest=profile.prepared_launch
    if manifest is None: _fail('T037_QUALIFICATION_INPUT_INVALID','protected prepared material required')
    targets=require_contract(profile)
    canonical=lambda p:ntpath.normcase(ntpath.normpath(p))
    readable={canonical(row['source_path']):row['size'] for row in manifest.jdk_files.values()}
    readable[canonical(ntpath.join(manifest.installation_prefix,*manifest.worker_source_pin['target'].split('/')))]=manifest.worker_source_pin['size']
    readable.update({canonical(ntpath.join(manifest.installation_prefix,*r['path'].split('/'))):r['size'] for r in manifest.generated_classes})
    parents={canonical(t.path) for t in targets.files if t.kind=='directory'}
    intent=derive_session_network_intent(profile)
    address=ipaddress.ip_address(intent.bind_address).packed
    for row in request['records']:
        operation=row['operation']
        if operation=='READ_HASH':
            size=readable.get(canonical(row['path']))
            if size is None or row['maxbytes'] != size+1 or row['maxbytes']>wire.MAX_READ_BYTES:
                _fail('T037_QUALIFICATION_INPUT_INVALID','read probe must bind complete finite prepared-file extent')
        elif operation=='CREATE_NEW':
            if (canonical(ntpath.dirname(row['path'])) not in parents
                    or ntpath.basename(row['path'])!=wire.create_basename(request['nonce'],row['index'])):
                _fail('T037_QUALIFICATION_INPUT_INVALID','create probe must use an approved pinned parent and fixed nonce leaf')
        elif operation=='TCP_ECHO':
            if row['address']!=address or row['port'] not in (intent.server_listen_port,intent.worker_listen_port):
                _fail('T037_QUALIFICATION_INPUT_INVALID','network probe must use the original finite role endpoints')
        else: _fail('T037_QUALIFICATION_INPUT_INVALID','unknown fixed operation')
    return request


def bind_probe_input(factory, frame, stdio):
    """One exact original purpose chooses one immutable, bounded request/stdio input."""
    from . import _windows_os_probe_protocol as wire
    owner=require_purpose(factory,purpose=Purpose.QUALIFY_OS,live=True)
    if id(owner) in _PROBE_INPUTS:
        _fail('T037_QUALIFICATION_REPLAYED','OS input cannot be rebound or retried')
    # Wire is data from the coordinator, constrained by its recipe/nonce/finite
    # namespace. It cannot choose a new purpose, main, role, account or command.
    request=wire.decode_request(frame,expected_nonce=owner.nonce,
        expected_recipe_sha256=owner.operation.recipe.sha256)
    validate_os_request_scope(owner.profile,request)
    original_io = _PROBE_IO.get(id(owner))
    if (type(original_io) is not OriginalProbeIO or original_io.frame is not frame
            or original_io.files != stdio):
        _fail('T037_QUALIFICATION_INPUT_INVALID','original parent stdio required; no caller files/PIPE/inherited fallback')
    original_io.check_input()
    value=OriginalProbeInput(owner,frame,stdio,uuid4().hex,original_io,_SEAL)
    _PROBE_INPUTS[id(owner)]=value
    value.check()
    return value


class OriginalProbeIO(_FixedOwner):
    """Original CPython file acquisitions, never reopened/adopted by path or fd.

    Temporary file names are in the original daemon's private parent directory;
    the child receives only the intended inherited stdio handles. No ACL or
    inheritance setting is changed here. Unknown creation/write/read/close holds
    the actual returned objects, bound functions and caller frames permanently.
    """
    _fixed = ('owner', 'frame', 'thread', 'directory', 'creator', 'registry', '_seal', 'file_bindings')

    def __init__(self, owner, frame, *, seal):
        if seal is not _SEAL: _fail('T037_QUALIFICATION_OWNER_REQUIRED','original parent IO producer required')
        import tempfile
        from ._windows_owned_resources import registry_for
        self.owner, self.frame, self._seal = owner, frame, seal
        self.thread = threading.get_ident()
        self.directory = owner.operation.enrollment.daemon.home
        self.creator = tempfile.TemporaryFile
        self.registry = registry_for(self)
        self.acquisitions = []
        self.calls = []
        self.files = ()
        self.state = 'NEW'
        self.first_error = None
        self.path_observation = None
        self.submission = None
        self.child = self.child_binding = self.response = None

    def _check(self, state, *, terminal=False):
        if (self._seal is not _SEAL or _PROBE_IO.get(id(self.owner)) is not self
                or self not in self.owner.operation.probe_io or self.thread != threading.get_ident()
                or self.state != state or self.first_error is not None
                or getattr(self, '_native_owned_registry', None) is not self.registry
                or self.registry.owner is not self):
            _fail('T037_QUALIFICATION_IO_CHANGED', 'same original parent IO/thread/state required')
        self.owner.check(terminal=terminal)

    def _unknown(self, error):
        if self.first_error is None: self.first_error = self.owner.operation.first_error or error
        self.state = 'UNKNOWN'
        return self.owner.operation.unknown(self.first_error)

    def _call(self, value, name, *arguments):
        state = self.state
        if state not in ('ACQUIRING', 'READY', 'READING_RESPONSE'):
            _fail('T037_QUALIFICATION_IO_CHANGED','original IO call is outside its active lifetime')
        self._check(state)
        record = dict(file=value, function=getattr(value, name), arguments=arguments,
                      entered=False, returned=None)
        self.calls.append(record)
        record['entered'] = True
        record['returned'] = record['function'](*arguments)
        self._check(state)
        return record['returned']

    def prepare(self):
        self._check('NEW')
        self.state = 'ACQUIRING'
        try:
            from ._host_scoped_provider import verify_private_path_security
            self.path_observation = verify_private_path_security(self.directory, directory=True)
            import io, tempfile
            values = []
            for index in range(3):
                slot = dict(index=index, function=self.creator, arguments=dict(mode='w+b', dir=self.directory),
                            entered=False, returned=None, closer=None, entry=None,
                            close_entered=False, close_return=None)
                self.acquisitions.append(slot)
                self._check('ACQUIRING')
                slot['entered'] = True
                slot['returned'] = self.creator(**slot['arguments'])
                self._check('ACQUIRING')
                value = slot['returned']
                # Windows TemporaryFile uses the standard owned wrapper. Do
                # not accept caller proxy classes or reopen a returned fd.
                if type(value) not in (io.BufferedRandom, io.FileIO, tempfile._TemporaryFileWrapper):
                    _fail('T037_QUALIFICATION_IO_CHANGED','ordinary original binary temporary file required')
                if any(previous['returned'] is value for previous in self.acquisitions[:-1]):
                    _fail('T037_QUALIFICATION_IO_CHANGED','temporary-file producer returned an already owned file')
                slot['closer'] = value.close
                binding = dict(file=value, closer=slot['closer'], entry=None)
                def close(fd, actual=slot, original=binding):
                    state = self.state
                    if state not in ('RETIRING_UNSUBMITTED', 'RETIRING_SUBMITTED'):
                        _fail('T037_QUALIFICATION_IO_CHANGED','original file close is outside its lifetime')
                    self._check(state, terminal=state == 'RETIRING_SUBMITTED')
                    entry = original['entry']
                    if (actual['returned'] is not original['file'] or actual['closer'] is not original['closer']
                            or actual['entry'] is not entry or entry.value != fd
                            or self.registry._entries.get(entry.seq) is not entry):
                        _fail('T037_QUALIFICATION_IO_CHANGED','original file/entry changed')
                    actual['close_entered'] = True
                    actual['close_return'] = actual['closer']()
                    self._check(state, terminal=state == 'RETIRING_SUBMITTED')
                    if actual['close_return'] is not None or actual['returned'].closed is not True:
                        _fail('T037_QUALIFICATION_IO_CHANGED','original file close not confirmed')
                slot['entry'] = self.registry.acquire(self, value, close, mode='VOID',
                    label='qualification_stdio_'+str(index), domain='qualification_parent_file',
                    keepalive=(slot, value, slot['closer']), value_reader=lambda actual: actual.fileno())
                binding['entry'] = slot['entry']
                if any(previous['entry'].value == slot['entry'].value for previous in self.acquisitions[:-1]):
                    _fail('T037_QUALIFICATION_IO_CHANGED','temporary-file producer repeated an already owned fd')
                if value.closed or not value.readable() or not value.writable() or not value.seekable():
                    _fail('T037_QUALIFICATION_IO_CHANGED','original stdio must be open binary read/write/seekable')
                values.append(value)
            self.files = tuple(values)
            self.file_bindings = tuple((slot, slot['returned'], slot['entry'], slot['closer'],
                slot['entry'].closer, slot['entry'].value) for slot in self.acquisitions)
            written = self._call(self.files[0], 'write', self.frame)
            if type(written) is not int or written != len(self.frame):
                _fail('T037_QUALIFICATION_IO_CHANGED','complete original frame was not written')
            if self._call(self.files[0], 'flush') is not None:
                _fail('T037_QUALIFICATION_IO_CHANGED','original input flush return not confirmed')
            rewind = self._call(self.files[0], 'seek', 0)
            if type(rewind) is not int or rewind != 0: _fail('T037_QUALIFICATION_IO_CHANGED','original input rewind not confirmed')
            self.state = 'READY'
            self.check_input()
        except BaseException as error:
            raise self._unknown(error)

    def check_input(self):
        self._check('READY')  # Pure wrong-owner/thread/UNKNOWN refusal precedes all file IO.
        try:
            from . import _windows_os_probe_protocol as wire
            if len(self.files) != 3 or len(self.acquisitions) != 3:
                _fail('T037_QUALIFICATION_IO_CHANGED','original stdio set incomplete')
            for slot, value in zip(self.acquisitions, self.files):
                entry = slot['entry']
                if (slot['returned'] is not value or entry is None or entry.state != 'ACQUIRED'
                        or self.registry._entries.get(entry.seq) is not entry or entry.registry is not self.registry
                        or entry.adapter is not self or value.closed or entry.value != value.fileno()):
                    _fail('T037_QUALIFICATION_IO_CHANGED','original file/registry entry changed')
            position = self._call(self.files[0], 'tell')
            if type(position) is not int or position != 0:
                _fail('T037_QUALIFICATION_IO_CHANGED','original inherited stdin cursor changed')
            actual = self._call(self.files[0], 'read', wire.MAX_REQUEST+1)
            if type(actual) is not bytes or actual != self.frame:
                _fail('T037_QUALIFICATION_IO_CHANGED','actual inherited stdin differs from original fixed frame')
            rewind = self._call(self.files[0], 'seek', 0)
            if type(rewind) is not int or rewind != 0: _fail('T037_QUALIFICATION_IO_CHANGED','original input rewind failed')
            for value in self.files[1:]:
                position = self._call(value, 'tell')
                if type(position) is not int or position != 0 or self._call(value, 'read', 1) != b'':
                    _fail('T037_QUALIFICATION_IO_CHANGED','probe output is not initially empty')
            return self
        except BaseException as error:
            raise self._unknown(error)

    def retire_unsubmitted(self):
        """Only original prebirth IO; launched child retirement is a separate producer."""
        self._check('READY')
        if self.submission is not None:
            _fail('T037_QUALIFICATION_IO_CHANGED','a consumed original submission requires child/factory retirement proof')
        factory = self.owner.factory
        api = factory._api
        from ._windows_owned_resources import registry_for
        if (factory._job is not None or factory._active_launch_input is not None
                or registry_for(factory).unresolved()
                or api is not None and registry_for(api).unresolved()
                or api is not None and any(getattr(api, key, None) for key in (
                    '_retained_children', '_unclassified_create_handles', '_retained_launch_cleanup',
                    '_retained_parent_bindings', '_birth_preparations'))):
            _fail('T037_QUALIFICATION_IO_CHANGED','prebirth retirement cannot discharge a possible child/Job')
        try:
            self.state = 'RETIRING_UNSUBMITTED'
            for slot in reversed(self.acquisitions): slot['entry'].release_owned(self)
            if self.registry.unresolved(): _fail('T037_QUALIFICATION_IO_CHANGED','parent files remain owned')
            self.state = 'RETIRED_UNSUBMITTED'
        except BaseException as error:
            raise self._unknown(error)

    def note_submission(self, inputs):
        """Fence before the normal factory can acquire a Job or enter CREATE."""
        self._check('READY')
        if (self.submission is not None or inputs.probe_input is not _PROBE_INPUTS.get(id(self.owner))
                or inputs.probe_input.io_owner is not self or inputs.factory is not self.owner.factory):
            _fail('T037_QUALIFICATION_IO_CHANGED','only this original input may enter the factory once')
        self.submission = inputs

    def bind_returned_child(self, child):
        """Capture the normal factory return, not a PID or reconstructed receipt.

        This is raw-output ownership only. It cannot issue C or authorize
        Resume; the accepted issuer and its original child checks are separate.
        """
        self._check('READY')
        self._require_original_files()
        from ._windows_t037 import _RetainedSuspendedChild, _native_g2_session_launch_owner
        inputs = self.submission
        factory = self.owner.factory
        if (self.child is not None or inputs is None or type(child) is not _RetainedSuspendedChild
                or child._launch_inputs is not inputs or inputs.probe_input.io_owner is not self
                or child._original_factory is not factory or child._api is not factory._api
                or child._owner is not child._api or not child._returned
                or child._creation_confirmed is not True or child._resume_confirmed is not True
                or child._resume_attempted is not True or child._closed
                or child._original_role != 'worker'):
            _fail('T037_QUALIFICATION_IO_CHANGED','same original successfully returned OS child required')
        child._check_original()  # Pure original storage/registry check; never polls an exited HANDLE.
        receipt = _native_g2_session_launch_owner(child)
        if receipt is None:
            _fail('T037_QUALIFICATION_IO_CHANGED','original API-registered launch return required')
        self.child = child
        self.child_binding = (receipt, child._api, child._registry, child._parent_binding,
            child._process_info, child._output_address, child._call_storage, child._create_arguments,
            child._birth_preparation, child._process_entry, child._thread_entry,
            tuple(child._entry_bindings.items()), child._outputs, child.birth_filetime_100ns,
            self._parent_context(child._parent_binding))
        return child

    @staticmethod
    def _parent_context(parent):
        entry = getattr(parent, '_resource_owner', None)
        return (parent.launch_api, parent.query, parent.pid, parent.handle, parent.birth, entry,
                None if entry is None else (entry.registry, entry.adapter, entry.closer,
                    entry.handle, entry.value, entry.mode, entry.label, entry.domain))

    def _known_child_close(self, *, terminal=False):
        """Pure exact-object checks before any parent I/O, also after factory close."""
        from ._windows_t037 import _RetainedSuspendedChild
        from ._windows_owned_resources import registry_for
        from ._windows_t037_child import _PinnedParent, _PARENT_SEAL
        child = self.child
        binding = self.child_binding
        if type(child) is not _RetainedSuspendedChild or type(binding) is not tuple or len(binding) != 15:
            _fail('T037_QUALIFICATION_IO_CHANGED','original returned-child lifetime is absent')
        (receipt, api, registry, parent, output, address, storage, arguments, primary,
         process_entry, thread_entry, entries, outputs, birth, parent_context) = binding
        factory = self.owner.factory
        if (child._api is not api or child._registry is not registry or registry_for(api) is not registry
                or registry.owner is not api or factory._api is not api
                or child._launch_inputs is not self.submission or self.submission.factory is not factory
                or child._original_factory is not factory or child._original_profile is not self.owner.profile
                or child._owner is not api or child._parent_binding is not parent
                or type(parent) is not _PinnedParent or parent._seal is not _PARENT_SEAL
                or parent.launch_api is not api or parent.closed is not True
                or self._parent_context(parent) != parent_context
                or child._process_info is not output or child._output_address != address
                or api.C.addressof(output) != address or child._call_storage is not storage
                or child._create_arguments is not arguments or child._birth_preparation is not primary
                or primary.state != 'RELEASED' or child._process_entry is not process_entry
                or child._thread_entry is not thread_entry or tuple(child._entry_bindings.items()) != entries
                or child._outputs != outputs or child.birth_filetime_100ns != birth
                or child._closed is not True or child._returned is not True
                or child._creation_confirmed is not True or child._resume_confirmed is not True
                or child._resume_attempted is not True or type(child.returncode) is not int
                or child._retirement_error is not None or child._terminate_error is not None
                or child._termination_requested or child._pending_resource_release_jobs
                or process_entry.state != 'RELEASED' or thread_entry.state != 'RELEASED'
                or child._thread_handle is not None or receipt.process is not child
                or receipt.api is not api or receipt.session_factory is not factory
                or receipt.profile is not self.owner.profile or receipt.argv != self.submission.argv
                or any(entry.state == 'UNKNOWN' for entry in registry.unresolved())
                or any(getattr(api, name, None) for name in ('_retained_children',
                    '_unclassified_create_handles', '_retained_launch_cleanup', '_retained_parent_bindings',
                    '_retained_child_tokens', '_birth_preparations'))):
            _fail('T037_QUALIFICATION_IO_CHANGED','original child/parent/registry close is not known')
        parent_entry = parent_context[5]
        if parent_entry is not None and (parent_entry.registry is not registry or parent_entry.adapter is not api
                or parent_entry.state != 'RELEASED' or registry._entries.get(parent_entry.seq) is not None):
            _fail('T037_QUALIFICATION_IO_CHANGED','original pinned-parent release binding changed')
        for name, (entry, closer, value, label) in entries:
            if (entry.registry is not registry or entry.adapter is not api or entry.closer is not closer
                    or entry.handle != value or entry.value != value or entry.label != label
                    or entry.state != 'RELEASED' or registry._entries.get(entry.seq) is not None):
                _fail('T037_QUALIFICATION_IO_CHANGED','original child release binding changed')
        if outputs != (output.hProcess, output.hThread, output.dwProcessId, output.dwThreadId):
            _fail('T037_QUALIFICATION_IO_CHANGED','original output slot changed after close')
        if terminal:
            if registry.unresolved() or registry_for(factory).unresolved():
                _fail('T037_QUALIFICATION_IO_CHANGED','original factory/Job/observation release is not known')
        else:
            child._check_original()
        self.submission.validate_native_storage(child, api)
        return child

    def _require_original_files(self):
        """Original stdio/slot/registry identities, not three caller file objects."""
        if (len(self.files) != 3 or len(self.acquisitions) != 3
                or len(self.file_bindings) != 3
                or self.submission is not None and self.submission.stdio != self.files):
            _fail('T037_QUALIFICATION_IO_CHANGED','original inherited stdio set changed')
        for actual, file, binding in zip(self.acquisitions, self.files, self.file_bindings):
            slot, returned, entry, closer, release, value = binding
            if (actual is not slot or file is not returned or slot['returned'] is not returned
                    or slot['entry'] is not entry or slot['closer'] is not closer
                    or entry.registry is not self.registry or entry.adapter is not self
                    or entry.closer is not release or entry.value != value
                    or self.registry._entries.get(entry.seq) is not entry or entry.state != 'ACQUIRED'
                    or entry.domain != 'qualification_parent_file' or returned.closed
                    or returned.fileno() != value):
                _fail('T037_QUALIFICATION_IO_CHANGED','original inherited file acquisition changed')

    def capture_response(self):
        """Bounded original-file readback after known natural child exit/close.

        Canonical frame parsing establishes data identity only. Denial, finite
        controls, original B/C and factory retirement require their own facts.
        """
        self._check('READY')
        self._known_child_close()  # Wrong/foreign/unfinished child refuses before parent I/O.
        self._require_original_files()
        if self.response is not None:
            _fail('T037_QUALIFICATION_REPLAYED','original response cannot be recaptured')
        try:
            from . import _windows_os_probe_protocol as wire
            self.state = 'READING_RESPONSE'
            raw = []
            for file in self.files[1:]:
                end = self._call(file, 'seek', 0, 2)
                if type(end) is not int or not 0 <= end <= wire.MAX_RESPONSE:
                    _fail('T037_QUALIFICATION_IO_CHANGED','actual output extent exceeds the fixed bound')
                if self._call(file, 'seek', 0) != 0:
                    _fail('T037_QUALIFICATION_IO_CHANGED','original output rewind failed')
                data = self._call(file, 'read', wire.MAX_RESPONSE+1)
                if type(data) is not bytes or len(data) != end or self._call(file, 'tell') != end:
                    _fail('T037_QUALIFICATION_IO_CHANGED','complete original output read was not confirmed')
                if self._call(file, 'seek', 0) != 0 or self._call(file, 'read', wire.MAX_RESPONSE+1) != data:
                    _fail('T037_QUALIFICATION_IO_CHANGED','original output changed during bounded readback')
                raw.append(data)
            self._known_child_close()
            self._require_original_files()
            request = wire.decode_request(self.frame, expected_nonce=self.owner.nonce,
                expected_recipe_sha256=self.owner.operation.recipe.sha256)
            decoded = wire.decode_response(raw[0], expected_nonce=self.owner.nonce,
                expected_recipe_sha256=self.owner.operation.recipe.sha256, expected_request=request)
            self.response = OriginalProbeResponse(self, raw[0], raw[1], self.child.returncode, _SEAL)
            self.state = 'CAPTURED'
            return self.response
        except BaseException as error:
            raise self._unknown(error)

    def retire_submitted(self):
        """Only after original child AND normal factory/Job/Gate acknowledgement."""
        self._check('CAPTURED', terminal=True)
        self._known_child_close(terminal=True)
        self._require_original_files()
        try:
            self.response.check()
            self.state = 'RETIRING_SUBMITTED'
            for slot in reversed(self.acquisitions): slot['entry'].release_owned(self)
            if self.registry.unresolved():
                _fail('T037_QUALIFICATION_IO_CHANGED','original submitted parent files remain owned')
            self.state = 'RETIRED_SUBMITTED'
        except BaseException as error:
            raise self._unknown(error)


@dataclass(frozen=True)
class OriginalProbeResponse:
    """Original complete raw output data. Never C, Resume or OS-denial authority."""
    io_owner: OriginalProbeIO
    stdout: bytes
    stderr: bytes
    exit_code: int
    _seal: object

    def check(self):
        io = self.io_owner
        if (self._seal is not _SEAL or type(io) is not OriginalProbeIO or io.response is not self
                or io.state not in ('CAPTURED', 'RETIRED_SUBMITTED')
                or type(self.stdout) is not bytes or type(self.stderr) is not bytes
                or type(self.exit_code) is not int or self.exit_code != io.child.returncode):
            _fail('T037_QUALIFICATION_IO_CHANGED','same original immutable raw response required')
        terminal = io.state == 'RETIRED_SUBMITTED' or io.owner.factory._closed
        io._check(io.state, terminal=terminal)
        io._known_child_close(terminal=terminal)
        from . import _windows_os_probe_protocol as wire
        request = wire.decode_request(io.frame, expected_nonce=io.owner.nonce,
            expected_recipe_sha256=io.owner.operation.recipe.sha256)
        return wire.decode_response(self.stdout, expected_nonce=io.owner.nonce,
            expected_recipe_sha256=io.owner.operation.recipe.sha256, expected_request=request)


    def terminal_data(self):
        """Pure cached original retirement witness, usable by later session threads.

        This does not re-observe a released HANDLE or reenter the acquisition
        owner's same-thread methods. Gate terminal acknowledgement is checked by
        the live-C consumer in addition to this exact original file/registry map.
        """
        io=self.io_owner
        if (self._seal is not _SEAL or type(io) is not OriginalProbeIO or io.response is not self
                or _PROBE_IO.get(id(io.owner)) is not io or io not in io.owner.operation.probe_io
                or io.state!='RETIRED_SUBMITTED' or io.first_error is not None
                or len(io.acquisitions)!=3 or len(io.file_bindings)!=3
                or io.registry.owner is not io or io.registry.unresolved()
                or io.child is None or not io.child._closed or io.child.returncode!=self.exit_code
                or io.owner.factory._closed is not True):
            _fail('T037_QUALIFICATION_IO_CHANGED','original complete retired IO witness changed')
        for slot,value,entry,closer,entry_closer,fd in io.file_bindings:
            if (slot not in io.acquisitions or slot['returned'] is not value or slot['entry'] is not entry
                    or slot['closer'] is not closer or entry.closer is not entry_closer or entry.value!=fd
                    or io.registry._entries.get(entry.seq) is not None or entry.state!='RELEASED'
                    or slot['close_entered'] is not True or slot['close_return'] is not None
                    or value.closed is not True):
                _fail('T037_QUALIFICATION_IO_CHANGED','original file release binding changed')
        from . import _windows_os_probe_protocol as wire
        request=wire.decode_request(io.frame,expected_nonce=io.owner.nonce,
            expected_recipe_sha256=io.owner.operation.recipe.sha256)
        return wire.decode_response(self.stdout,expected_nonce=io.owner.nonce,
            expected_recipe_sha256=io.owner.operation.recipe.sha256,expected_request=request)


class OriginalProjectOwner(_FixedOwner):
    """One normal admitted session factory bound to the original live C.

    A session may later connect a Worker on another controller thread. Each
    birth still needs the original Gate activity and held-A/child binding; the
    immutable session admission is not replaced by a caller thread/PID value.
    """
    _fixed=('daemon','factory','profile','binding','participant','operation','purpose',
            'live_c','record','runtime','key','operation_id','job_id','request_hash','_seal')
    def __init__(self,daemon,factory,record,runtime,live_c,*,seal):
        if seal is not _SEAL: _fail('T037_QUALIFICATION_OWNER_REQUIRED','normal original session admission required')
        self.daemon,self.factory,self.record,self.runtime=daemon,factory,record,runtime
        self.profile,self.binding,self.participant=factory.profile,factory._scm_binding,factory._profile_participant
        self.live_c,self.operation,self.purpose=live_c,live_c.operation,Purpose.PROJECT
        self.key=(factory.profile.host_resource_profile.project_id,factory.session_id)
        self.operation_id,self.job_id,self.request_hash=record['operation_id'],record['job_id'],record['request_hash']
        self._seal=seal

    def check(self,*,live=False,terminal=False):
        from ._control_daemon import ControlDaemon
        from ._windows_appcontainer_gate import _participant,_require_terminal
        f,d=self.factory,self.daemon
        if (self._seal is not _SEAL or type(d) is not ControlDaemon
                or _PURPOSES.get(id(f)) is not self or f._qualification_purpose is not self
                or d._session_t037_process_factories.get(self.key) is not f
                or d._session_runtime_configs.get(self.key) is not self.runtime
                or f.profile is not self.profile or self.profile is not self.live_c.profile
                or f._scm_binding is not self.binding or self.binding[0] is not self.live_c.operation.enrollment.binding
                or _participant(f) is not self.participant or f._closed is not terminal
                or self.runtime.runtime_id!=self.live_c.recipe.runtime_id):
            _fail('T037_QUALIFICATION_PURPOSE_CHANGED','original PROJECT admission/runtime/factory changed')
        self.live_c.check(runtime_id=self.runtime.runtime_id,live=False)
        if live:
            self._durable_admission()
            self.live_c.check(runtime_id=self.runtime.runtime_id,live=True)
        if terminal: _require_terminal(self.participant)
        return self

    def _durable_admission(self):
        row=self.daemon.store.get_operation(self.operation_id);job=self.daemon.store.job(self.job_id)
        if (row is None or job is None or row['operation']!='session.start'
                or row['request_hash']!=self.request_hash or row['status'] not in ('QUEUED','RUNNING','SUCCEEDED')
                or job['operation_id']!=self.operation_id or job['status']!=row['status']
                or row['metadata'].get('project_id')!=self.key[0]
                or row['metadata'].get('session_id')!=self.key[1]
                or row['metadata'].get('runtime_id')!=self.runtime.runtime_id):
            _fail('T037_QUALIFICATION_PURPOSE_CHANGED','original durable PROJECT admission changed')
        return row

    def require_birth(self):
        """A successful old start record cannot restart a stopped session."""
        self.check()
        row=self._durable_admission()  # Caller is outside runtime/Gate/issuer transition locks.
        lifecycle=self.daemon.session_lifecycle.get(*self.key)
        if lifecycle is None:
            # The first normal preflight precedes STARTING persistence. It is
            # still the original active start and no native provider/Job exists.
            if (row['status'] not in ('QUEUED','RUNNING') or self.factory._api is not None
                    or self.factory._job is not None):
                _fail('T037_QUALIFICATION_PURPOSE_CHANGED','original first PROJECT admission is no longer active')
        elif (lifecycle['runtime_id']!=self.runtime.runtime_id
                or lifecycle['state'] not in ('STARTING','CONNECTING','CONNECTED','DISCONNECTED')):
            _fail('T037_QUALIFICATION_PURPOSE_CHANGED','current PROJECT lifecycle cannot create another child')
        return self


def bind_project_factory(daemon,factory,record,runtime):
    from ._control_daemon import ControlDaemon
    from ._windows_t037 import T037SessionProcessFactory
    from ._windows_t037_qualification_run import require_live_c
    if (type(daemon) is not ControlDaemon or type(factory) is not T037SessionProcessFactory
            or id(factory) in _PURPOSES or getattr(factory,'_qualification_purpose',None) is not None):
        _fail('T037_QUALIFICATION_OWNER_REQUIRED','one original normal PROJECT factory required')
    live_c=require_live_c(daemon,runtime_id=runtime.runtime_id,live=True)
    owner=OriginalProjectOwner(daemon,factory,record,runtime,live_c,seal=_SEAL)
    _PURPOSES[id(factory)]=owner
    factory._qualification_purpose=owner
    owner.check(live=True)
    owner.require_birth()
    return owner


def require_resume_purpose(process,profile,child_binding,dacl_observation):
    """Validate actual original A/B/purpose before the existing one-use issuer.

    All observation calls occur outside the issuer lock. The public diagnostic
    objects retain their diagnostic states and cannot themselves mint C.
    """
    from ._windows_t037 import (_RetainedSuspendedChild,T037SessionProcessFactory,PROFILE_SCHEMA_V4)
    from ._windows_t037_child import RetainedChildObservation
    from ._windows_t037_acl import ChildDaclObservation
    from ._windows_t037_targets import _require_held_finite_a
    from ._windows_t037_b_observation import _OriginalBObserver,_current
    from ._windows_job_limits import require_configured_job
    if (type(process) is not _RetainedSuspendedChild or type(process._original_factory) is not T037SessionProcessFactory
            or process._original_profile is not profile or profile.wrapper_schema!=PROFILE_SCHEMA_V4
            or process._owner is not process._api or not process.never_resumed
            or process._resume_attempt is not None or process._resume_epoch!=0):
        _fail('NATIVE_CHILD_PROOF_UNAVAILABLE','original never-resumed V4 child required')
    factory=process._original_factory
    owner=require_purpose(factory,live=True)
    if (factory._api is not process._api or factory.profile is not profile
            or process._launch_inputs.purpose_owner is not owner):
        _fail('NATIVE_CHILD_PROOF_UNAVAILABLE','normal original purpose/input owner changed')
    if type(owner) is OriginalPurposeOwner:
        from ._windows_t037_qualification_run import OriginalQualificationRun,_RUNS
        coordinator=owner.operation.coordinator
        if (type(coordinator) is not OriginalQualificationRun or _RUNS.get(id(owner.operation)) is not coordinator
                or coordinator.state!='RUNNING' or coordinator.first_error is not None):
            _fail('NATIVE_CHILD_PROOF_UNAVAILABLE','only the original running APPLY coordinator may qualify a child')
        if owner.purpose is Purpose.QUALIFY_OS:
            if owner.stage not in coordinator.stages or owner.stage.factory is not factory or owner.stage.owner is not owner:
                _fail('NATIVE_CHILD_PROOF_UNAVAILABLE','original fixed OS stage required')
        elif coordinator.vendor_factory is not factory or not all(s.accepted for s in coordinator.stages):
            _fail('NATIVE_CHILD_PROOF_UNAVAILABLE','vendor purpose requires both original completed OS stages')
    held=_require_held_finite_a(factory)
    observer=getattr(process,'_connected_b_observer',None)
    if (type(observer) is not _OriginalBObserver or observer.held is not held
            or observer.state!='OBSERVED' or observer.result is None or observer.error is not None
            or observer.result.finite_state!='MATCHED_FINITE_CHECKS_ONLY'
            or observer.result.issues or observer.result.selected_policy_issues
            or not observer.result.finite_comparisons
            or any(c.matches is not True for c in observer.result.finite_comparisons)):
        _fail('NATIVE_CHILD_PROOF_UNAVAILABLE','actual original finite B/selected token checks incomplete')
    _current(observer)
    observer.preparation.recheck_submitted()
    process._launch_inputs.validate_native_storage(process,process._api)
    parent=process._parent_binding
    if (type(child_binding) is not RetainedChildObservation
            or child_binding.pid!=process.pid or child_binding.birth_filetime_100ns!=process.birth_filetime_100ns
            or child_binding.parent_pid!=parent.pid or child_binding.parent_birth_filetime_100ns!=parent.birth
            or child_binding.same_job is not True or child_binding.image_path!=process._creation_parameters[0]
            or type(dacl_observation) is not ChildDaclObservation
            or process._api._last_child_dacl_observation is not dacl_observation
            or dacl_observation.child_pid!=process.pid or dacl_observation.birth_filetime_100ns!=process.birth_filetime_100ns
            or not dacl_observation.checks):
        _fail('NATIVE_CHILD_PROOF_UNAVAILABLE','actual child/parent/Job/DACL observations changed')
    require_configured_job(process._api,process._job_handle,profile,for_child_birth=False)
    _current(observer)
    if _require_held_finite_a(factory) is not held:
        _fail('NATIVE_CHILD_PROOF_UNAVAILABLE','original A pins changed during authorization')
    # Qualification purposes are rooted before the first birth by their APPLY
    # coordinator. PROJECT requires the completed live C for the same runtime.
    if type(owner) is OriginalProjectOwner:
        owner.live_c.check(runtime_id=owner.runtime.runtime_id,live=True)
    return owner
