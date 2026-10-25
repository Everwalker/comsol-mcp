"""Isolated original enrollment/admission units, NOT the complete C Controller path.

Real SCM runtime, provider, normal fresh profile loader/native executor ctor,
token parser and durable Store admission; only SDK/file observations are inert.
The test arranges the retained record and callback as a unit fixture. It does
not claim the still-incomplete normal Controller qualification coordinator.
"""
from contextlib import ExitStack, contextmanager
import copy
import ctypes as C
from dataclasses import replace
import json
import ntpath
import os
import struct
import threading
from types import SimpleNamespace as NS

import pytest

from comsol_mcp import _windows_t037 as win
from comsol_mcp import _windows_host_change as changes
from comsol_mcp import _windows_t037_qualification as q
from comsol_mcp import _windows_t037_qualification_recipe as recipe
from comsol_mcp import _windows_t037_admission as admission
from comsol_mcp import _w26_os_resources as resources
from comsol_mcp._execution_contract import ExecutionContractError
from comsol_mcp._windows_owned_resources import registry_for
from tests._prepared_launch_software_fixture import PreparedEnvironment
from tests import test_windows_scm_prebirth as scm
from tests.test_windows_host_change import _grant, _write_provider


def make_plan(profile):
    host = profile.host_resource_profile
    policy = host.windows_isolation
    return resources.build_host_change_plan(host, change_manifest=[dict(
        action_id='qualification-unit-create', resource_kind='appcontainer_profile',
        resource_name=policy.appcontainer_profile_name, operation='CREATE',
        desired=dict(profile_id=host.applied_policy_id, policy_sha256=resources._sha256(policy.as_dict())),
        baseline_identity=None, baseline_sha256=None, backup_path=None, backup_sha256=None)],
        operator_inputs={recipe.KEY: recipe.QualificationRecipe(
            recipe.manifest_digest(profile.prepared_launch), 'software-runtime', '6.4.0.293',
            ('server', 'worker')).as_dict()})


def bind_token_sdk(monkeypatch, env):
    """Actual readonly token parser/close registry; only fixed-width SDK exports fake."""
    keep = {}; calls = []
    def sid(text):
        pieces = text.split('-')
        return bytes((1, len(pieces)-3)) + int(pieces[2]).to_bytes(6, 'big') + b''.join(
            int(v).to_bytes(4, 'little') for v in pieces[3:])
    def put(out, value): C.cast(out, C.POINTER(C.c_void_p)).contents.value = value
    def token(process, access, out):
        assert process == 1 and access == 8
        calls.append('OpenProcessToken'); put(out, 0x6600); return 1
    def info(token, kind, buffer, size, used):
        assert token.value == 0x6600
        base = C.addressof(buffer) if buffer is not None else 0
        if kind in (1, 25):
            text = env.profile.service_account_sid if kind == 1 else 'S-1-16-16384'
            raw = struct.pack('<QI4x', base+16, 0 if kind == 1 else 0x60) + sid(text)
        elif kind == 2: raw = struct.pack('<I4x', 0)
        elif kind == 29: raw = struct.pack('<I', 0)
        else: raise AssertionError(kind)
        calls.append(('GetTokenInformation', kind, buffer is not None))
        C.cast(used, C.POINTER(C.c_uint32)).contents.value = len(raw)
        if buffer is None: return 0
        assert len(raw) == size
        C.memmove(buffer, raw, len(raw)); return 1
    def sid_text(pointer, out):
        value = getattr(pointer, 'value', pointer); head = C.string_at(value, 8)
        subs = struct.unpack('<'+'I'*head[1], C.string_at(value+8, 4*head[1]))
        text = 'S-1-'+str(int.from_bytes(head[2:8], 'big'))+''.join('-'+str(x) for x in subs)
        storage = C.create_unicode_buffer(text); address = C.addressof(storage)
        keep[address] = storage; put(out, address); return 1
    def free(value):
        address = getattr(value, 'value', value); assert address in keep
        calls.append(('LocalFree', address)); keep.pop(address); return None
    def close(value):
        assert getattr(value, 'value', value) == 0x6600
        calls.append('CloseHandle'); return 1
    def initialize(api):
        api.C = C; api.W = NS(HANDLE=C.c_void_p, DWORD=C.c_uint32, LPWSTR=C.c_wchar_p)
        api.k = NS(GetCurrentProcess=lambda: 1, CloseHandle=close, LocalFree=free)
        api.a = NS(OpenProcessToken=token, GetTokenInformation=info, IsValidSid=lambda p: 1,
            GetLengthSid=lambda p: 8+4*C.string_at(getattr(p, 'value', p), 2)[1],
            ConvertSidToStringSidW=sid_text)
    monkeypatch.setattr(win._Win32ReadOnlyApi, '__init__', initialize)
    monkeypatch.setattr(C, 'get_last_error', lambda: 122, raising=False)
    return calls, keep


def bind_directory_security_sdk(monkeypatch, env, *, fault=None):
    """Actual original double-path/ACL + full getter, with explicit SDK leaves."""
    from comsol_mcp import _host_scoped_provider as provider, _windows_parameter_publish as paths
    from tests.test_windows_host_preflight_owner import SDK, Function, install
    original = provider.verify_private_path_security
    full_route = provider._verify_windows_host_control_root
    observations = []
    @contextmanager
    def exports():
        sdk = SDK(); sdk.volume = int(env.profile.host_resource_profile.root.volume_id)
        final_id = int(env.profile.host_resource_profile.root.file_id)
        original_relative = sdk.relative
        counts = {}
        def relative(out, access, attributes, *args):
            result = original_relative(out, access, attributes, *args)
            value = C.cast(attributes, C.POINTER(paths._ObjectAttributes)).contents
            name = value.ObjectName.contents
            if C.wstring_at(name.Buffer, name.Length//2) == env.profile.workspace_root.split('\\')[-1]:
                sdk.handles[sdk.seq] = (True, final_id)
            return result
        def full(handle, kind, out, size):
            key = sdk.address(handle); directory, inode = sdk.handles[key]
            counts[(key, kind)] = counts.get((key, kind), 0)+1
            root = inode == final_id
            if fault == 'getter_error': raise OSError('software full SDK result unavailable')
            if kind == 18:
                value = C.cast(out, C.POINTER(paths._FullIdInfo)).contents
                volume = sdk.volume
                if root and fault == 'high_collision': inode += 1 << 64
                if root and fault == 'second_high_drift' and sdk.open_count == 2: inode += 1 << 64
                if root and fault == 'after_high_drift' and counts[(key, kind)] > 1: inode += 1 << 64
                if root and fault == 'volume_collision': volume += 1 << 32
                value.volume = volume; value.file_id[:] = inode.to_bytes(16, 'little')
            elif kind == 0:
                value = C.cast(out, C.POINTER(paths._BasicInfo)).contents
                value.created=1; value.changed=2; value.written=3
                value.attributes=16 if directory else 128
                if root and fault == 'reparse': value.attributes |= 0x400
                if root and fault == 'type': value.attributes = 128
            elif kind == 1:
                value = C.cast(out, C.POINTER(paths._StandardInfo)).contents
                value.size=0; value.links=1; value.directory=int(directory)
                if root and fault == 'pending': value.delete_pending=1
                if root and fault == 'links': value.links=0
            else: raise AssertionError(kind)
            sdk.calls.append(('full', key, kind)); return 1
        sdk.nt.NtCreateFile = Function(relative)
        sdk.k.GetFileInformationByHandleEx = Function(full)
        if fault == 'missing_getter': sdk.k.GetFileInformationByHandleEx = None
        if fault == 'acl': sdk.fault = 'descriptor_status'
        if fault == 'unknown_close': sdk.close_result = 0
        if fault == 'legacy_volume':
            old_info = sdk.info
            def changed_info(handle, out):
                result=old_info(handle, out)
                C.cast(out, C.POINTER(paths._FileInfo)).contents.volume += 1
                return result
            sdk.k.GetFileInformationByHandle=Function(changed_info)
        observations.append(sdk)
        with monkeypatch.context() as scope:
            # Original SCM fixture copied ctypes to isolate its host libc seam.
            scope.setattr(provider, 'ctypes', C)
            install(scope, sdk)
            yield sdk
    def verify(path, *, directory=False, expected_identity=None):
        if not directory or str(path) != env.profile.workspace_root:
            return original(path, directory=directory, expected_identity=expected_identity)
        with exports():
            return provider._verify_windows_private_path(env.Path(path), directory=True,
                                                       expected_identity=expected_identity)
    def complete(path):
        with exports(): return full_route(path)
    monkeypatch.setattr(provider, 'verify_private_path_security', verify)
    monkeypatch.setattr(provider, '_verify_windows_host_control_root', complete)
    return observations


@contextmanager
def original_owner(monkeypatch, tmp_path, *, fixed_probe=False, callback_hold_seconds=10):
    # Provider source is written once before original SCM/provider binding. This
    # is a private software grant fixture, never a live host grant or issuer.
    def provider(directory, profile, grants):
        # The prepared wrapper exists before the service fixture creates its provider.
        plan = current.get('plan')
        assert plan is not None
        return _write_provider(directory, profile, [*grants,
            _grant(profile, 'host.change.apply', 'qualification-unit', plan.sha256, phase='APPLY')])
    # PreparedEnvironment starts its SCM fixture in __init__, before returning.
    # Derive the exact plan from the already-installed normal wrapper held by
    # the daemon passed into that original fixture, rather than a prebuilt hash.
    current = {}; normal_chain = scm.running_factory_chain
    @contextmanager
    def chain(daemon, factory, directory, m, **options):
        current['plan'] = make_plan(daemon._windows_t037_profile)
        with normal_chain(daemon, factory, directory, m, callback_hold_seconds=callback_hold_seconds, **options) as result: yield result
    monkeypatch.setattr(scm, '_write_provider', provider)
    monkeypatch.setattr('tests._prepared_launch_software_fixture.running_factory_chain', chain)
    if fixed_probe:
        from tests import _prepared_launch_software_fixture as fixture
        from tests import test_windows_prepared_launch as compiler
        from comsol_mcp import _windows_worker_bootstrap as prepared
        compiler_sdk = fixture._wire_compiler_sdk
        def compile_fixture(backend, **options):
            result = compiler_sdk(backend, **options)
            wait = backend.sdk['WaitForSingleObject']
            def observed_outputs(*args):
                returned = wait(*args)
                package = ntpath.join(compiler.PREFIX, *prepared.OUTPUT_DIR.split('/'), 'comsol_mcp', 'worker_java')
                path = ntpath.join(package, 'PersistentComsolWorker$OsQualificationProbe.class')
                if path not in backend.tree:
                    backend.add(path, False, b'SOFTWARE_ONLY inert fixed helper compiler output', sd=backend.tree[package]['sd'])
                return returned
            backend.sdk['WaitForSingleObject'] = observed_outputs
            return result
        monkeypatch.setattr(fixture, '_wire_compiler_sdk', compile_fixture)
    with ExitStack() as stack:
        env = PreparedEnvironment(monkeypatch, tmp_path, stack, controller=True)
        daemon = env.daemon; plan = current['plan']
        monkeypatch.setattr(changes, 'os', NS(**dict(vars(os), name='nt')))
        monkeypatch.setattr(changes, 'socket', NS(gethostname=lambda: env.profile.host_resource_profile.host_id))
        calls, keep = bind_token_sdk(monkeypatch, env)
        bind_directory_security_sdk(monkeypatch, env)
        # Only native export/ABI initialization is replaced. Normal classmethod,
        # source loader, constructor/origin checks and all owner methods run.
        def initialize_wfp(api):
            api.api = NS()
            registry_for(api)
        monkeypatch.setattr(changes.CtypesWindowsWfpMutationApi, '__init__', initialize_wfp)
        executor = changes.WindowsHostChangeExecutor.from_trusted_profile_path(
            store=daemon.store, authority=daemon.project_authority,
            trusted_profile_path=daemon._windows_t037_profile_path)
        assert executor.software_only is False
        loaded = executor._trusted_profile_load_origin.profile_wrapper
        assert loaded is not env.profile and loaded.host_resource_profile is executor.profile
        assert loaded.host_resource_profile is not env.profile.host_resource_profile
        assert loaded.source_identity == env.profile.source_identity
        runtime = daemon._windows_t037_scm_runtime
        issuer = daemon._windows_t037_host_control_issuer(runtime)
        request = issuer.issue(runtime, phase='APPLY', plan=plan, authorization_ref='qualification-unit')
        request.consume(service_name=env.profile.service_name, project_id=executor.profile.project_id,
                        profile_sha256=executor.profile.sha256)
        key = ('pending', id(request))
        record = dict(key=key, request=request, executor=executor, first_cause=None)
        daemon._retained_host_control_executors[key] = record
        enrolled = q.enroll_executor(daemon, request, record, executor)
        def on_admission(actual, accepted):
            assert actual is executor
            q.bind_admission(actual, accepted)
        executor._on_original_host_admission = on_admission
        admitted = executor._admit(phase='APPLY', plan_sha256=plan.sha256,
            approval=request.approval, baseline={'scope': 'isolated admission unit; no resource APPLY'})
        operation = enrolled.operation
        assert operation.admission is admitted and operation.check(live=True) is operation
        assert calls.count('OpenProcessToken') == calls.count('CloseHandle') == 1 and not keep
        yield NS(env=env, executor=executor, owner=enrolled, operation=operation,
                 record=record, calls=calls, plan=plan)


def test_normal_fresh_loader_and_original_store_admission_can_own_bounded_purpose(monkeypatch, tmp_path):
    with original_owner(monkeypatch, tmp_path) as value:
        operation = value.operation
        factory = operation.new_factory(q.Purpose.QUALIFY_OS)
        owner = q.require_purpose(factory, purpose=q.Purpose.QUALIFY_OS, live=True)
        assert owner.operation is operation and owner.profile is value.env.profile
        assert admission._require_factory_runtime(factory)[1] is value.env.daemon
        assert factory._api is None and factory._job is None
        before = dict(value.env.daemon._session_t037_process_factories)
        with pytest.raises(ExecutionContractError) as duplicate: operation.new_factory(q.Purpose.QUALIFY_OS)
        assert duplicate.value.code == 'T037_QUALIFICATION_REPLAYED'
        assert value.env.daemon._session_t037_process_factories == before
        with pytest.raises(ExecutionContractError): operation.new_factory(q.Purpose.PROJECT)
        with pytest.raises(ExecutionContractError): q.require_purpose(factory, purpose=q.Purpose.QUALIFY_VENDOR)
        assert not q.retirement_known(value.record) and operation.live_c is None


@pytest.mark.parametrize('change', ['copy', 'record', 'provider', 'profile', 'thread', 'durable_status'])
def test_original_owner_substitution_refuses_before_any_new_sdk_or_purpose(monkeypatch, tmp_path, change):
    with original_owner(monkeypatch, tmp_path) as value:
        operation = value.operation; before = list(value.calls); errors = []
        if change == 'copy':
            target = copy.copy(operation)
        else:
            target = operation
            if change == 'record': value.record['executor'] = object()
            elif change == 'provider': monkeypatch.setattr(value.env.daemon, '_scoped_host_provider', object())
            elif change == 'profile': monkeypatch.setattr(value.env.daemon, '_windows_t037_profile', replace(value.env.profile))
            elif change == 'durable_status':
                # No result rewrite; the test's original Store operation is normally terminalized.
                value.env.daemon.store.finish(operation.admission.operation_id, status='FAILED', result={'success': False})
        def call():
            try: target.new_factory(q.Purpose.QUALIFY_OS)
            except ExecutionContractError as error: errors.append(error)
        if change == 'thread':
            thread = threading.Thread(target=call); thread.start(); thread.join(2)
            assert not thread.is_alive()
        else: call()
        assert len(errors) == 1 and operation.purposes == []
        assert value.calls == before and operation.live_c is None
        if change == 'durable_status':
            assert operation.first_error is errors[0] and operation.state == 'UNKNOWN'


def test_full_installer_root_bridge_preserves_wide_ids_and_legacy_domain(monkeypatch, tmp_path):
    """Legacy metadata unchanged; complete root bridge alone is NOT qualification."""
    from comsol_mcp import _host_scoped_provider as provider
    with ExitStack() as stack:
        env = PreparedEnvironment(monkeypatch, tmp_path, stack, controller=True)
        monkeypatch.setattr(changes, 'os', NS(**dict(vars(os), name='nt')))
        monkeypatch.setattr(changes, 'socket', NS(gethostname=lambda: env.profile.host_resource_profile.host_id))
        calls, keep = bind_token_sdk(monkeypatch, env)
        bind_directory_security_sdk(monkeypatch, env)
        root = env.profile.host_resource_profile.root
        observed = provider.verify_private_path_security(root.path, directory=True)
        actual = observed['identity']
        assert int(root.volume_id) > 0xffffffff and int(root.file_id) > 0xffffffffffffffff
        assert actual['device'] == int(root.volume_id) & 0xffffffff
        assert actual['inode'] == int(root.file_id) & 0xffffffffffffffff
        assert actual['device'] != int(root.volume_id) and actual['inode'] != int(root.file_id)
        complete = provider._verify_windows_host_control_root(root.path)
        assert complete['identity']['device'] == int(root.volume_id)
        assert complete['identity']['inode'] == int(root.file_id)
        assert complete['path_chain'][-1]['identity'] == {k:complete['identity'][k] for k in ('device','inode')}
        assert complete['security_sha256'] != observed['security_sha256']
        result = changes._current_windows_binding(env.profile.host_resource_profile)
        assert result['root_identity'] == complete['identity']
        assert calls.count('OpenProcessToken') == calls.count('CloseHandle') == 1 and not keep
        (tmp_path/'actual-root-identity-bridge.json').write_text(json.dumps(dict(
            scope='SOURCE_SOFTWARE_NOT_QUALIFICATION', expected=root.as_dict(),
            original_legacy=observed, complete=complete, normal_binding=result), indent=1)+'\n')


@pytest.mark.parametrize('fault', ['high_collision', 'volume_collision', 'second_high_drift',
    'after_high_drift', 'reparse', 'type', 'pending', 'links', 'legacy_volume',
    'missing_getter', 'getter_error', 'acl', 'unknown_close'])
def test_complete_root_identity_never_uses_low_bit_fallback_or_unsafe_path(monkeypatch, tmp_path, fault):
    from comsol_mcp import _windows_host_preflight_owner as owned
    from comsol_mcp import _windows_parameter_publish as paths
    with ExitStack() as stack:
        env = PreparedEnvironment(monkeypatch, tmp_path, stack, controller=True)
        monkeypatch.setattr(changes, 'os', NS(**dict(vars(os), name='nt')))
        monkeypatch.setattr(changes, 'socket', NS(gethostname=lambda: env.profile.host_resource_profile.host_id))
        calls, keep = bind_token_sdk(monkeypatch, env)
        observations = bind_directory_security_sdk(monkeypatch, env, fault=fault)
        roots = {id(o) for o in owned.retained_original_owners()}
        with pytest.raises(ExecutionContractError) as error:
            changes._current_windows_binding(env.profile.host_resource_profile)
        assert error.value.safe_retry is False
        assert len(observations) == 1 and not calls and not keep
        sdk = observations[0]
        closes = [e[1] for e in sdk.calls if type(e) is tuple and e[0] == 'close']
        assert len(closes) == len(set(closes))  # every original entry attempted at most once
        if fault in ('high_collision', 'volume_collision'):
            assert error.value.code == 'HOST_CHANGE_ROOT_IDENTITY_MISMATCH'
        if fault == 'unknown_close':
            retained = [o for o in owned.retained_original_owners() if id(o) not in roots]
            assert len(retained) == 1
            owner = retained[0]
            assert owner.first_cause is not None and owner.registry.owner is owner.api
            assert any(e.state == 'UNKNOWN' for e in owner.entries())
            assert owner.sdk['GetFileInformationByHandleEx'] is sdk.k.GetFileInformationByHandleEx
            assert any(isinstance(buffer, paths._FullIdInfo)
                for e in owner.registry._entries.values() for buffer in e.keepalive)
        assert env.factory._job is None and 'CREATE_SUSPENDED' not in env.events


def test_original_os_purpose_normal_fixed_input_and_one_consumption_without_birth(monkeypatch, tmp_path):
    from comsol_mcp import _windows_t037_launch_inputs as launch, _windows_os_probe_protocol as wire
    from comsol_mcp import _java_worker as java
    from tests.test_windows_suspended_ownership import _launch_api
    from tests.test_windows_prepared_launch import WINROOT, SYSTEMROOT
    with original_owner(monkeypatch, tmp_path, fixed_probe=True) as value:
        env=value.env
        factory=value.operation.new_factory(q.Purpose.QUALIFY_OS)
        # A fresh, exact native API instance is supplied at its constructor
        # seam; no reader, preflight, owner or input producer is replaced.
        descriptor=bytes.fromhex(next(t.descriptor_hex for t in env.profile.finite_targets.files if t.path==env.profile.workspace_root))
        _,api,events,state=_launch_api(monkeypatch,profile_override=env.profile,acl_descriptor=descriptor)
        api.k.GetSystemWindowsDirectoryW=lambda buffer,capacity:env._root(buffer,WINROOT)
        api.k.GetSystemDirectoryW=lambda buffer,capacity:env._root(buffer,SYSTEMROOT)
        made=[]
        def construct(cls):
            assert cls is win._Win32LaunchApi and not made
            assert env.daemon._windows_t037_scm_runtime._profile_gate.activity[0].factory is factory
            assert factory._api is None and factory._job is None
            made.append(api);return api
        monkeypatch.setattr(win._Win32LaunchApi,'__new__',staticmethod(construct))
        monkeypatch.setattr(win._Win32LaunchApi,'__init__',lambda self:None)  # inert export binding only
        assert factory.read_launch_os_roots()==(WINROOT,SYSTEMROOT)
        owner=q.require_purpose(factory)
        manifest=env.profile.prepared_launch;java_pin=manifest.jdk_files['java']
        locks=env.Path(env.profile.workspace_root)/'locks'
        paths=java.JavaWorkerPaths(env.Path(manifest.vendor_source_root),
            env.Path(ntpath.dirname(ntpath.dirname(java_pin['source_path']))),
            project_root=env.Path(env.profile.workspace_root),global_lock_root=locks,platform_name='nt')
        request=dict(version=1,nonce=owner.nonce,recipe_sha256=value.operation.recipe.sha256,
            records=[wire.read_hash_record(0,java_pin['source_path'],java_pin['size']+1)])
        frame=wire.encode_request(request)
        original_io=value.operation.prepare_probe_io(factory,frame)
        stdio=original_io.files
        inputs=launch.build_os_probe_inputs(factory,paths,frame=frame,stdio=stdio)
        assert inputs.argv[-3:]==(wire.MAIN_CLASS,owner.nonce,value.operation.recipe.sha256)
        assert inputs.classpath==() and inputs.role=='worker' and inputs.text is False
        assert inputs.purpose_owner is owner and inputs.probe_input.owner is owner
        assert inputs.probe_input.frame==frame and inputs.stdio==stdio
        assert 'COMSOL' not in inputs.environment['PATH'] and inputs.environment['TEMP']==inputs.environment['TMP']
        options=dict(executable=inputs.executable,cwd=inputs.cwd,env=dict(inputs.environment),
            stdin=stdio[0],stdout=stdio[1],stderr=stdio[2],text=False,start_new_session=True,creationflags=0)
        with launch.activate_launch_inputs(factory,inputs):
            assert launch.consume_launch_inputs(factory,inputs.argv,options,'worker') is inputs
            with pytest.raises(ExecutionContractError):launch.consume_launch_inputs(factory,inputs.argv,options,'worker')
        with pytest.raises(ExecutionContractError):
            launch.build_os_probe_inputs(factory,paths,frame=frame,stdio=stdio)
        copied=copy.copy(owner)
        monkeypatch.setattr(factory,'_qualification_purpose',copied)
        with pytest.raises(ExecutionContractError):inputs.validate_submission(factory,inputs.argv,options,'worker')
        monkeypatch.setattr(factory,'_qualification_purpose',owner)
        assert original_io.submission is inputs
        before=[slot['close_entered'] for slot in original_io.acquisitions]
        with pytest.raises(ExecutionContractError):original_io.retire_unsubmitted()
        assert [slot['close_entered'] for slot in original_io.acquisitions]==before
        assert original_io.state=='READY' and all(not f.closed for f in stdio)
        assert factory._job is None and 'CREATE_SUSPENDED' not in events and 'RESUME' not in events
        assert value.operation.live_c is None and not q.retirement_known(value.record)


def request_frame(value, factory):
    from comsol_mcp import _windows_os_probe_protocol as wire
    owner=q.require_purpose(factory);pin=owner.profile.prepared_launch.jdk_files['java']
    return wire.encode_request(dict(version=1,nonce=owner.nonce,recipe_sha256=value.operation.recipe.sha256,
        records=[wire.read_hash_record(0,pin['source_path'],pin['size']+1)]))


@pytest.mark.parametrize('damage',['stdin_bytes','stdin_cursor','stdout_bytes','copy','thread'])
def test_original_parent_stdio_rejects_drift_or_foreign_lifetime_without_child(monkeypatch,tmp_path,damage):
    with original_owner(monkeypatch,tmp_path) as value:
        factory=value.operation.new_factory(q.Purpose.QUALIFY_OS)
        frame=request_frame(value,factory)
        io=value.operation.prepare_probe_io(factory,frame)
        if damage=='stdin_bytes':io.files[0].write(b'changed');io.files[0].seek(0)
        elif damage=='stdin_cursor':io.files[0].seek(1)
        elif damage=='stdout_bytes':io.files[1].write(b'old data');io.files[1].seek(0)
        before=len(io.calls);errors=[]
        def call():
            try:(copy.copy(io) if damage=='copy' else io).check_input()
            except ExecutionContractError as error:errors.append(error)
        if damage=='thread':
            thread=threading.Thread(target=call);thread.start();thread.join(2);assert not thread.is_alive()
        else:call()
        assert len(errors)==1 and factory._job is None and value.operation.live_c is None
        if damage in ('copy','thread'):
            assert len(io.calls)==before and io.first_error is None and value.operation.first_error is None
            io.retire_unsubmitted()
        else:
            assert io.state=='UNKNOWN' and io.first_error is value.operation.first_error
            before=len(io.calls)
            with pytest.raises(ExecutionContractError):io.check_input()
            with pytest.raises(ExecutionContractError):io.retire_unsubmitted()
            assert len(io.calls)==before and all(not f.closed for f in io.files)


@pytest.mark.parametrize('fault',['acquire_after_file','acquire_marks_unknown','duplicate_return','register_after_entry','close_before','close_after'])
def test_original_parent_io_partial_effect_retains_original_slots_functions_and_no_retry(monkeypatch,tmp_path,fault):
    import tempfile
    from comsol_mcp import _windows_owned_resources as resources_owned
    with original_owner(monkeypatch,tmp_path) as value:
        factory=value.operation.new_factory(q.Purpose.QUALIFY_OS)
        frame=request_frame(value,factory);created=[];closes=[];original_error=OSError('inert original file outcome lost')
        creator=tempfile.TemporaryFile
        def create(**arguments):
            if fault=='duplicate_return' and created:return created[0]
            file=creator(**arguments);created.append(file)
            if fault=='acquire_marks_unknown':value.operation.unknown(original_error)
            if fault=='acquire_after_file' and len(created)==2:raise original_error
            if fault in ('close_before','close_after'):
                original_close=file.close
                def close():
                    closes.append(file)
                    if fault=='close_after':original_close()
                    raise original_error
                file.close=close  # explicit original filesystem SDK/return-loss leaf
            return file
        monkeypatch.setattr(tempfile,'TemporaryFile',create)
        if fault=='register_after_entry':
            acquire=resources_owned.NativeOwnedRegistry.acquire
            def registration(registry,adapter,*args,**options):
                entry=acquire(registry,adapter,*args,**options)
                if type(adapter) is q.OriginalProbeIO:raise original_error
                return entry
            monkeypatch.setattr(resources_owned.NativeOwnedRegistry,'acquire',registration)
        if fault.startswith('close'):
            io=value.operation.prepare_probe_io(factory,frame)
            with pytest.raises(OSError) as captured:io.retire_unsubmitted()
            assert len(closes)==1 and (closes[0].closed is (fault=='close_after'))
            assert any(e.state=='UNKNOWN' for e in io.registry.unresolved())
        else:
            expected_error=ExecutionContractError if fault=='duplicate_return' else OSError
            with pytest.raises(expected_error) as captured:value.operation.prepare_probe_io(factory,frame)
            io=value.operation.probe_io[0]
            assert len(created)==(2 if fault=='acquire_after_file' else 1)
            assert io.acquisitions[0]['returned'] is created[0]
            if fault=='acquire_after_file':
                assert io.acquisitions[1]['entered'] and io.acquisitions[1]['returned'] is None
                assert io.acquisitions[1]['function'] is create
            elif fault=='acquire_marks_unknown':
                assert len(io.acquisitions)==1 and io.acquisitions[0]['entry'] is None and not io.registry.unresolved()
            else:assert io.registry.unresolved()[0].keepalive[1] is created[0]
        if fault=='duplicate_return':
            assert captured.value.code=='T037_QUALIFICATION_IO_CHANGED'
            assert io.acquisitions[1]['returned'] is created[0]
            assert io.first_error is value.operation.first_error is captured.value
        else:assert captured.value is original_error and io.first_error is value.operation.first_error is original_error
        assert io.state=='UNKNOWN' and factory._job is None and value.operation.live_c is None
        before=(len(created),len(closes),len(io.calls))
        with pytest.raises(ExecutionContractError):io.check_input()
        with pytest.raises(ExecutionContractError):io.retire_unsubmitted()
        with pytest.raises(ExecutionContractError):value.operation.prepare_probe_io(factory,frame)
        assert before==(len(created),len(closes),len(io.calls))


@pytest.mark.parametrize('selector',[True,False,'full',{}])
def test_root_full_mode_is_closed_internal_and_refuses_caller_selectors_before_sdk(selector):
    from comsol_mcp import _host_scoped_provider as provider
    from pathlib import Path
    with pytest.raises(ExecutionContractError) as error:
        provider._verify_windows_private_path(Path(r'C:\fixture'),directory=True,
            expected_identity=None,_identity_mode=selector)
    assert error.value.code=='HOST_PROVIDER_CONFIG_INVALID'

