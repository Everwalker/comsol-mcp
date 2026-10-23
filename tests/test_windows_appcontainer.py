"""Original CREATE handler/SCM issuer/Controller/Store against synthetic SDK."""
import ctypes as C
import json
from types import SimpleNamespace
from dataclasses import replace
import pytest
from comsol_mcp import _windows_appcontainer as leaf, _windows_appcontainer_native as native
from comsol_mcp import _windows_appcontainer_lifecycle as appcontainer_lifecycle
from comsol_mcp import _windows_host_change as changes, _w26_os_resources as resources
from comsol_mcp._project_authority import ProjectAuthority
from comsol_mcp._windows_owned_resources import registry_for
from tests.test_windows_service_sid_controller import environment as service_environment, dispatch
from tests.test_windows_host_change import _write_provider, _grant
from tests.test_windows_host_preflight_owner import Function
from comsol_mcp._windows_t037_bootstrap import parse_bootstrap, SCHEMA as BOOTSTRAP_SCHEMA


class UserenvSDK:
    def __init__(self, p, store):
        self.policy = p.windows_isolation; self.store = store; self.calls = []; self.buffers = []
        self.fault = None; self.primary = OSError('original-userenv-after-output')
        self.userenv = SimpleNamespace(CreateAppContainerProfile=Function(self.create))

    def sid(self, text):
        parts = list(map(int, text.split('-')[1:])); subs = parts[2:]
        raw = bytes((1, len(subs))) + parts[1].to_bytes(6, 'big') + b''.join(n.to_bytes(4, 'little') for n in subs)
        b = C.create_string_buffer(raw); self.buffers.append(b); return C.addressof(b)

    def convert(self, text, out):
        C.cast(out, C.POINTER(C.c_void_p))[0] = self.sid(text)
        self.calls.append(('convert', text)); return 1

    def thread(self, thread, rights, as_self, out):
        self.calls.append('thread')
        if self.fault == 'impersonation':
            C.cast(out, C.POINTER(C.c_void_p))[0] = 555; return 1
        return 0

    def create(self, name, display, description, caps, count, out):
        row = self.store.db.execute("SELECT metadata FROM job_events WHERE event='HostControlMutationJournalV2' ORDER BY id DESC LIMIT 1").fetchone()
        assert row is not None and json.loads(row[0])['event'] == 'APPLY_STEP_INTENT'
        assert name == display == description == self.policy.appcontainer_profile_name
        assert count == len(self.policy.capability_sids)
        assert count == 0 or caps[0].attributes == 4
        self.calls.append('create')
        value = None if self.fault == 'null' else C.c_void_p(-1).value if self.fault == 'invalid_pointer' else self.sid('S-1-15-2-999' if self.fault == 'sid_mismatch' else self.policy.appcontainer_sid)
        if self.fault == 'exists': return -2147024713
        C.cast(out, C.POINTER(C.c_void_p))[0] = value
        if self.fault == 'io': raise self.primary
        return False if self.fault == 'bool_hresult' else 2**31 if self.fault == 'overflow_hresult' else -2147024891 if self.fault == 'failed_output' else 0

    def free(self, pointer):
        self.calls.append(('FreeSid', pointer.value))
        if self.fault == 'free_io': raise self.primary
        return pointer.value if self.fault == 'free_pointer' else False if self.fault == 'free_bool' else None


def environment(tmp_path, monkeypatch, capabilities=True, with_restore=False):
    env = list(service_environment(tmp_path, monkeypatch, baseline=3))
    p, _, scm, files, api, store, _, _, _, daemon, wrapper = env
    if not capabilities:
        p = replace(p, windows_isolation=replace(p.windows_isolation, capability_sids=()))
        wrapper = replace(wrapper, host_resource_profile=p)
    policy = p.windows_isolation
    manifest = {'action_id': 'appcontainer-create', 'resource_kind': 'appcontainer_profile',
        'resource_name': policy.appcontainer_profile_name, 'operation': 'CREATE',
        'desired': {'profile_id': p.applied_policy_id, 'policy_sha256': resources._sha256(policy.as_dict())},
        'baseline_identity': None, 'baseline_sha256': None, 'backup_path': None, 'backup_sha256': None}
    plan = resources.build_host_change_plan(p, change_manifest=[manifest], operator_inputs={})
    grants = [_grant(p, 'host.change.apply', 'apply-once', plan.sha256, phase='APPLY')]
    if with_restore:
        action = changes._normalize_supported_actions(plan, p)[1][0]
        expected = {
            'profile_name': policy.appcontainer_profile_name,
            'appcontainer_sid': policy.appcontainer_sid,
            'service_user_sid': policy.service_logon_sid,
            'creation_result': 'S_OK',
            'capabilities_state': 'NOT_READ_BACK',
            'generation_state': 'UNAVAILABLE',
        }
        expected_sha = resources._sha256(expected)
        predicted_rows = ({
            'action_id': action['action_id'],
            'resource_kind': 'appcontainer_profile',
            'resource_key': action['resource_key'],
            'created_resource_sha256': expected_sha,
            'prior_resource_sha256': None,
            'prior_backup_path': None,
        },)
        receipt_body = changes._receipt_payload_v2(plan, p, predicted_rows)
        receipt_sha = resources._sha256(receipt_body)
        predicted = resources.AppliedHostChangeReceipt(
            plan.sha256, p.host_id, 'windows', predicted_rows, receipt_sha)
        restore_body, restore_sha, _ = appcontainer_lifecycle._restore_selection_digest(
            plan, p, predicted, action)
        grants.append(_grant(
            p, 'host.change.restore', 'restore-once', restore_sha, phase='RESTORE',
            source_plan=plan.sha256, source_receipt=receipt_sha,
        ))
    provider = _write_provider(tmp_path/'provider-app', p, grants)
    authority = ProjectAuthority(store, workspace_root=tmp_path, permission_provider=lambda: {'host_control','inspect'},
        grant_ceiling={'host_control','inspect'}, authorization_verifier=provider)
    monkeypatch.setattr(authority, 'authorize_operation', lambda project_id, permission: {'project_id':project_id,'workspace':p.root.path,'policy':{'permissions':[permission]}})
    executor = changes.WindowsHostChangeExecutor._for_software_test(store=store,authority=authority,profile=p,api=api,token=changes._SOFTWARE_TEST_TOKEN)
    daemon.project_authority = authority; daemon._windows_t037_profile = wrapper
    monkeypatch.setattr('comsol_mcp._windows_t037.load_trusted_profile', lambda path: wrapper)
    monkeypatch.setattr(changes.WindowsHostChangeExecutor, 'from_trusted_profile_path', classmethod(lambda cls, **kw: executor))
    sdk = UserenvSDK(p, store); api._appcontainer_userenv = sdk.userenv
    api._advapi.FreeSid = Function(sdk.free)
    api._advapi.IsValidSid = Function(lambda ptr: 0 if sdk.fault == 'invalid_sid' else 1)
    api._advapi.GetLengthSid = Function(lambda ptr: 7 if sdk.fault == 'invalid_length' else 8 + C.string_at(ptr, 2)[1]*4)
    api._advapi.OpenThreadToken = Function(sdk.thread)
    api._advapi.ConvertStringSidToSidW = Function(sdk.convert)
    api._kernel32.GetCurrentThread = Function(lambda: C.c_void_p(-2).value)
    api._kernel32.GetLastError = Function(lambda: 5 if sdk.fault == 'thread_error' else 1008)
    api._kernel32.CloseHandle = Function(lambda ptr: sdk.calls.append(('thread_close',ptr.value)) or 1)
    return (p, plan, scm, files, api, store, provider, authority, executor, daemon, wrapper), sdk


@pytest.mark.parametrize('capabilities', [True, False])
def test_original_scm_controller_intent_create_configured_only(tmp_path, monkeypatch, capabilities):
    env, sdk = environment(tmp_path,monkeypatch,capabilities)
    try:
        result = dispatch(env)
        assert 'error' not in result, repr(result.get('error'))
        receipt = result['result']; changes._verify_sequence_receipt(env[1], env[0], receipt)
        row = json.loads(env[5].db.execute('SELECT result FROM operations').fetchone()[0])
        assert row['host_change_state'] == 'CONFIGURED_UNVERIFIED'
        assert row['restore_state'] == 'API_ONLY_DELETE_AVAILABLE_POSTIMAGE_UNVERIFIED'
        assert row['capabilities_state'] == 'NOT_READ_BACK'
        assert row['generation_state'] == 'UNAVAILABLE' and row['t037_state'] == 'NOT_VERIFIED'
        assert sdk.calls.count('create') == 1 and len([c for c in sdk.calls if isinstance(c,tuple) and c[0]=='FreeSid']) == 1
        assert not registry_for(env[4]).unresolved() and not env[9]._retained_host_control_executors
        with pytest.raises(Exception,match='selector') as error:
            env[8].restore(env[1],receipt,authorization_ref='restore-once')
        assert error.value.code == 'HOST_CHANGE_APPCONTAINER_OWNER_REQUIRED'
        assert not hasattr(sdk.userenv, 'DeleteAppContainerProfile')
    finally: env[5].close()


@pytest.mark.parametrize('fault', ['exists','null','invalid_pointer','sid_mismatch','io','bool_hresult','overflow_hresult','failed_output','invalid_sid','invalid_length','free_pointer','free_bool','free_io'])
def test_original_sdk_after_intent_unknown_no_delete_no_replay(tmp_path,monkeypatch,fault):
    env,sdk=environment(tmp_path,monkeypatch);sdk.fault=fault
    try:
        result=dispatch(env);assert 'error' in result
        row=json.loads(env[5].db.execute('SELECT result FROM operations').fetchone()[0])
        assert row['execution_state']=='UNKNOWN' and row['safe_retry'] is False
        assert 'applied_receipt' not in row and sdk.calls.count('create')==1
        if fault in {'exists','null','invalid_pointer','io','bool_hresult','overflow_hresult','failed_output','invalid_sid','invalid_length'}:
            assert not [c for c in sdk.calls if isinstance(c,tuple) and c[0]=='FreeSid']
        assert env[9]._retained_host_control_executors
        sdk.fault=None
        second=dispatch(env);assert 'error' in second and sdk.calls.count('create')==1
        assert not hasattr(sdk.userenv,'DeleteAppContainerProfile')
    finally:env[5].close()


@pytest.mark.parametrize('fault',['impersonation','thread_error'])
def test_thread_context_refused_before_admission(tmp_path,monkeypatch,fault):
    env,sdk=environment(tmp_path,monkeypatch);sdk.fault=fault
    try:
        assert 'error' in dispatch(env)
        assert not env[5].db.execute('SELECT * FROM operations').fetchall()
        assert 'create' not in sdk.calls
        assert not registry_for(env[4]).unresolved()
    finally:env[5].close()


@pytest.mark.parametrize('field,value',[('resource_name','foreign'),('policy_sha256','f'*64),('profile_id','foreign'),('operation','UPDATE')])
def test_original_action_binding_refused_before_sdk(tmp_path,monkeypatch,field,value):
    env,sdk=environment(tmp_path,monkeypatch)
    try:
        payload=env[1].as_dict();action=payload['change_manifest'][0]
        if field in {'policy_sha256','profile_id'}:action['desired'][field]=value;action['desired_identity_sha256']=resources._sha256(action['desired'])
        else:action[field]=value
        plan=resources.HostChangePlan(resources._canonical_json(payload),resources._sha256(payload))
        with pytest.raises(Exception):changes._normalize_supported_actions(plan,env[0])
        assert 'create' not in sdk.calls
    finally:env[5].close()


def test_original_free_closer_retained_after_failure(tmp_path,monkeypatch):
    env,sdk=environment(tmp_path,monkeypatch);sdk.fault='free_pointer'
    try:
        assert 'error' in dispatch(env)
        r=registry_for(env[4]);entries=[e for e in r.unresolved() if e.domain=='appcontainer_sid'];assert len(entries)==1
        original=sdk.free;sdk.fault=None
        env[4]._advapi.FreeSid=Function(lambda *args:pytest.fail('replacement FreeSid'))
        env[4]._captured_mutation_sdk['FreeSid']=env[4]._advapi.FreeSid
        r.retry_owned(entries[0],env[4]);assert not r.unresolved()
        assert len([c for c in sdk.calls if isinstance(c,tuple) and c[0]=='FreeSid'])==2
    finally:env[5].close()


def test_original_bootstrap_restore_and_factory_still_refuse(tmp_path,monkeypatch):
    env,sdk=environment(tmp_path,monkeypatch)
    try:
        result=dispatch(env);receipt=result['result'];p,plan=env[:2];wrapper=env[10]
        request={'schema':BOOTSTRAP_SCHEMA,'service_name':p.windows_isolation.service_name,
            'profile_source_sha256':wrapper.source_sha256,'host_profile_sha256':p.sha256,'project_id':p.project_id,
            'phase':'RESTORE','authorization_ref':'restore-once',
            'plan':{'payload_json':plan.payload_json,'sha256':plan.sha256},
            'receipt':{'plan_sha256':receipt.plan_sha256,'host_id':receipt.host_id,'platform':receipt.platform,
                'actions':list(receipt.actions),'receipt_sha256':receipt.receipt_sha256}}
        parsed=parse_bootstrap(json.dumps(request).encode(),wrapper)
        assert parsed.receipt.receipt_sha256==receipt.receipt_sha256
        before=env[5].db.execute('SELECT count(*) FROM operations').fetchone()[0]
        rejected=dispatch(env,phase='RESTORE',receipt=receipt,ref='restore-once')
        assert rejected['error'].code=='HOST_CHANGE_APPCONTAINER_ORIGINAL_EFFECT_UNKNOWN'
        assert env[5].db.execute('SELECT count(*) FROM operations').fetchone()[0]==before
        from comsol_mcp._windows_t037 import T037SessionProcessFactory
        from comsol_mcp import _windows_t037_resume as resume
        factory=T037SessionProcessFactory(wrapper,'appcontainer-session')
        with pytest.raises(Exception):factory.preflight()
        assert not resume._pending and sdk.calls.count('create')==1
    finally:env[5].close()


@pytest.mark.parametrize('fault',['io','bool_hresult','failed_output','invalid_sid','invalid_length'])
def test_ambiguous_output_metadata_cannot_authorize_original_FreeSid(tmp_path,monkeypatch,fault):
    env,sdk=environment(tmp_path,monkeypatch);sdk.fault=fault
    try:
        assert 'error' in dispatch(env)
        r=registry_for(env[4]);entries=[e for e in r.unresolved() if e.domain=='appcontainer_sid'];assert len(entries)==1
        state=env[4]._appcontainer_original_call[3]
        state['allocation_confirmed']=True;state['address']=entries[0].value
        with pytest.raises(Exception):r.retry_owned(entries[0],env[4])
        assert not [c for c in sdk.calls if isinstance(c,tuple) and c[0]=='FreeSid']
        assert entries[0] in r.unresolved()
    finally:env[5].close()


@pytest.mark.parametrize('fault',['late_impersonation','capability_failure','capability_io','thread_close_false'])
def test_original_pre_create_allocations_and_context_fail_closed(tmp_path,monkeypatch,fault):
    env,sdk=environment(tmp_path,monkeypatch)
    try:
        if fault=='late_impersonation':
            original=sdk.thread;calls=[]
            def thread(*args):
                calls.append(1)
                if len(calls)>1:sdk.fault='impersonation'
                return original(*args)
            env[4]._advapi.OpenThreadToken.callback=thread
        elif fault=='thread_close_false':
            sdk.fault='impersonation';env[4]._kernel32.CloseHandle.callback=lambda *args:0
        else:
            original=sdk.convert
            def convert(*args):
                original(*args)
                if fault=='capability_io':raise sdk.primary
                return 0
            env[4]._advapi.ConvertStringSidToSidW.callback=convert
        result=dispatch(env);assert 'error' in result and 'create' not in sdk.calls
        if fault!='thread_close_false':
            row=json.loads(env[5].db.execute('SELECT result FROM operations').fetchone()[0])
            assert row['execution_state']=='UNKNOWN' and row['safe_retry'] is False
        else:
            assert registry_for(env[4]).unresolved()
        assert not hasattr(sdk.userenv,'DeleteAppContainerProfile')
    finally:env[5].close()
