"""Original sealed SCM issuer/Controller/Store/mutator and backup SDK boundary."""
import ctypes as C
import os,gc,weakref,hashlib,json
from dataclasses import replace
from types import SimpleNamespace
import pytest
from comsol_mcp import _windows_service_sid as leaf,_windows_service_sid_native as scm
from comsol_mcp import _windows_host_change as changes,_windows_t037_native as native,_windows_t037_wfp as wfp,_windows_wfp_mutation_owner as owned
from comsol_mcp._operation_store import OperationStore
from comsol_mcp._project_authority import ProjectAuthority
from comsol_mcp._control_daemon import ControlDaemon
from comsol_mcp._windows_t037 import WindowsT037ProfileV2
from comsol_mcp._windows_t037_broker import WindowsScmServiceBroker,WindowsT037HostControlIssuer,_SCM_TEST_API_TOKEN
from comsol_mcp import _w26_os_resources as resources,_windows_parameter_publish as paths,_windows_host_preflight_owner as preflights
from tests.test_windows_host_change import _profile,_write_provider,_grant,_events_v2
from tests.test_windows_t037_broker import _ScmApi
from tests.test_windows_t037_native import _security_descriptor
from tests.test_windows_service_sid_native import SDK,actual
from tests.test_windows_service_sid_backup import BackupSDK
from tests.test_windows_host_preflight_owner import install,Function

class FileSDK(BackupSDK):
    def __init__(self):super().__init__();self.image=b'public-launcher';self.kinds={};self.offsets={}
    def relative(self,out,access,attributes,io_status,*args):
        result=super().relative(out,access,attributes,io_status,*args)
        h=C.cast(out,C.POINTER(C.c_void_p))[0]
        attr=C.cast(attributes,C.POINTER(paths._ObjectAttributes)).contents
        text=C.wstring_at(attr.ObjectName.contents.Buffer,attr.ObjectName.contents.Length//2)
        self.kinds[h]='image' if text=='launcher.exe' else 'backup';self.offsets[h]=0
        return result
    def info(self,h,out):
        result=super().info(h,out);row=C.cast(out,C.POINTER(paths._FileInfo)).contents
        if not row.attributes&16:row.size_low=len(self.image if self.kinds.get(self.address(h))=='image' else self.raw)
        return result
    def read(self,h,buffer,size,count,overlapped):
        h=self.address(h);raw=self.image if self.kinds.get(h)=='image' else self.raw
        start=self.offsets[h];block=raw[start:start+size];self.offsets[h]+=len(block)
        self.calls.append(('read',h,size));C.memmove(buffer,block,len(block));C.cast(count,C.POINTER(C.c_uint32))[0]=len(block);return -1


def environment(tmp_path,monkeypatch,baseline=0):
    sdk=SDK(baseline);files=FileSDK();image_sha=hashlib.sha256(files.image).hexdigest();p=_profile()
    p=replace(p,launch_images=tuple(replace(i,image_sha256=image_sha,argv_sha256=leaf.digest(sdk.argv)) if i.role=='launcher' else i for i in p.launch_images))
    adapter=actual(monkeypatch,sdk);install(monkeypatch,files)
    monkeypatch.setattr(C,'WinDLL',lambda name,**kw:{'kernel32':files.k,'ntdll':files.nt,'advapi32':files.a,'shell32':sdk.shell}[name],raising=False)
    monkeypatch.setattr(C,'get_last_error',lambda:122,raising=False)
    before=adapter._service_sid_observe(p.windows_isolation.service_name)
    doc={'schema':leaf.BACKUP_SCHEMA,'profile_sha256':p.sha256,'project_id':p.project_id,'host_id':p.host_id,'root':p.root.as_dict(),'subject':before}
    files.raw=leaf.canonical(doc)
    policy=p.windows_isolation
    desired={key:getattr(policy,key) for key in ('service_name','service_start_name','service_logon_sid','service_sid','service_sid_type')};desired['schema']=leaf.DESIRED_SCHEMA
    manifest={'action_id':'service-sid-config','resource_kind':'windows_service','resource_name':'ComsolHost','operation':'UPDATE',
              'desired':desired,'baseline_identity':before,'baseline_sha256':leaf.digest(before),
              'backup_path':p.root.path+'\\backup','backup_sha256':hashlib.sha256(files.raw).hexdigest()}
    plan=resources.build_host_change_plan(p,change_manifest=[manifest],operator_inputs={})
    store=OperationStore(tmp_path/'operations.sqlite')
    # Provider startup is the actual POSIX path on the authorized APFS temp root.
    provider=_write_provider(tmp_path/'provider',p,[_grant(p,'host.change.apply','apply-once',plan.sha256,phase='APPLY')])
    authority=ProjectAuthority(store,workspace_root=tmp_path,permission_provider=lambda:{'host_control','inspect'},grant_ceiling={'host_control','inspect'},authorization_verifier=provider)
    monkeypatch.setattr(authority,'authorize_operation',lambda project_id,permission:{'project_id':project_id,'workspace':p.root.path,'policy':{'permissions':[permission]}})
    executor=changes.WindowsHostChangeExecutor._for_software_test(store=store,authority=authority,profile=p,api=adapter,token=changes._SOFTWARE_TEST_TOKEN)
    daemon=ControlDaemon.__new__(ControlDaemon);daemon.store=store;daemon.project_authority=authority
    wrapper=WindowsT037ProfileV2(p,'a'*64,{'device':1,'inode':2},'SOFTWARE_FIXTURE_ONLY')
    daemon._windows_t037_profile=wrapper;daemon._windows_t037_profile_path='public-profile';daemon._retained_host_control_executors={}
    monkeypatch.setattr('comsol_mcp._windows_t037.load_trusted_profile',lambda path:wrapper)
    monkeypatch.setattr(changes.WindowsHostChangeExecutor,'from_trusted_profile_path',classmethod(lambda cls,**kw:executor))
    return p,plan,sdk,files,adapter,store,provider,authority,executor,daemon,wrapper


def dispatch(env,phase='APPLY',receipt=None,ref='apply-once'):
    p,plan,sdk,files,api,store,provider,authority,exe,daemon,wrapper=env;holder={}
    def main(runtime):
        runtime.mark_running();issuer=WindowsT037HostControlIssuer(authority=authority,provider=provider,profile=p,profile_source_sha256=wrapper.source_sha256)
        request=issuer.issue(runtime,phase=phase,plan=plan,authorization_ref=ref,receipt=receipt);holder['request']=request
        request.consume(service_name=p.windows_isolation.service_name,project_id=p.project_id,profile_sha256=p.sha256)
        try:holder['result']=daemon._execute_internal_host_control(request)
        except BaseException as error:holder['error']=error
        service.handler(1,0,None,None)
    service=_ScmApi();broker=WindowsScmServiceBroker._for_software_test(p.windows_isolation.service_name,main,api=service,token=_SCM_TEST_API_TOKEN)
    broker.run();return holder

@pytest.mark.parametrize('baseline',[0,1])
def test_actual_controller_store_intent_then_level5_positive_restore(tmp_path,monkeypatch,baseline):
    env=environment(tmp_path,monkeypatch,baseline);p,plan,sdk,files,api,store,provider,authority,exe,daemon,wrapper=env
    original=sdk.change
    def change(*args):
        journal=store.db.execute("SELECT metadata FROM job_events WHERE event='HostControlMutationJournalV2' ORDER BY id DESC LIMIT 1").fetchone()
        assert json.loads(journal[0])['event'] in {'APPLY_STEP_INTENT','RESTORE_STEP_INTENT'}
        return original(*args)
    sdk.a.ChangeServiceConfig2W.callback=change
    try:
        result=dispatch(env);assert 'error' not in result,repr(result.get('error'));receipt=result['result']
        assert not daemon._retained_host_control_executors and sdk.sid_type==3
        row=store.db.execute('SELECT result FROM operations').fetchone();assert json.loads(row[0])['host_change_state']=='CONFIGURED_UNVERIFIED_SYSTEM_RESTART_REQUIRED'
        # Authorize exact fresh restore fingerprint through original provider.
        subject=api._service_sid_observe(p.windows_isolation.service_name)
        action=changes._normalize_supported_actions(plan,p)[1][0];backup=leaf.read_backup(action['proposed_backup_path'],action['proposed_backup_sha256'])
        original_subject=backup['document']['subject']
        restore_sha=leaf.digest({'schema':'comsol-mcp.windows-service-sid-restore/1','source_plan_sha256':plan.sha256,'source_receipt_sha256':receipt.receipt_sha256,
              'profile_sha256':p.sha256,'subject_sha256':leaf.digest(subject),'backup_sha256':backup['raw_sha256'],'original_subject_sha256':leaf.digest(original_subject),'resource_key':action['resource_key']})
        restore_provider=_write_provider(tmp_path/'restore-provider',p,[_grant(p,'host.change.restore','restore-once',restore_sha,phase='RESTORE',source_plan=plan.sha256,source_receipt=receipt.receipt_sha256)])
        authority.authorization_verifier=restore_provider;exe.provider=restore_provider;env=list(env);env[6]=restore_provider
        restored=dispatch(env,'RESTORE',receipt,'restore-once');assert 'error' not in restored,repr(restored.get('error'))
        assert sdk.sid_type==baseline and restored['result']['host_change_state']=='RESTORED_UNVERIFIED'
        assert store.db.execute("SELECT count(*) FROM jobs WHERE status='SUCCEEDED'").fetchone()[0]==2
        assert not daemon._retained_host_control_executors and not owned.registry(api).unresolved()
        restore_provider.close()
    finally:provider.close();store.close()


def test_actual_controller_after_effect_unknown_gc_cleanup_no_reapply(tmp_path,monkeypatch):
    env=environment(tmp_path,monkeypatch);p,plan,sdk,files,api,store,provider,authority,exe,daemon,wrapper=env
    original=sdk.change
    def change(*args):sdk.close_result=0;sdk.fault='change_io';return original(*args)
    sdk.a.ChangeServiceConfig2W.callback=change
    try:
        result=dispatch(env);assert 'error' in result and sdk.sid_type==3
        assert store.db.execute('SELECT status FROM jobs').fetchone()[0]=='UNKNOWN'
        record=next(iter(daemon._retained_host_control_executors.values()));assert record['executor'] is exe
        ref=weakref.ref(exe);env=None;del exe;gc.collect();retained=ref();assert retained is record['executor']
        assert api._first_mutation_cause is sdk.primary
        with pytest.raises(Exception):daemon._reconcile_original_host_control_executor(result['request'],retained)
        count=len(sdk.calls);sdk.a.CloseServiceHandle=Function(lambda *args:pytest.fail('redirected closer'))
        with pytest.raises(Exception):daemon._execute_internal_host_control(result['request'])
        assert len(sdk.calls)==count
        sdk.close_result=1
        for entry in reversed(owned.registry(api).unresolved()):owned.registry(api).retry_owned(entry,api)
        daemon._reconcile_original_host_control_executor(result['request'],retained)
        assert not daemon._retained_host_control_executors and sdk.sid_type==3
        assert store.db.execute('SELECT status FROM jobs').fetchone()[0]=='UNKNOWN'
        assert len([r for r in sdk.calls if isinstance(r,tuple) and r[0]=='change'])==1
    finally:provider.close();store.close()

@pytest.mark.parametrize('fault',['shared','foreign_pid','foreign_account','already3','bad_bits','backup_tamper','image_tamper'])
def test_actual_controller_preeffect_refusal_preserves_nonce_and_no_config(tmp_path,monkeypatch,fault):
    env=environment(tmp_path,monkeypatch);p,plan,sdk,files,api,store,provider,authority,exe,daemon,wrapper=env
    if fault=='shared':sdk.service_type=0x20
    elif fault=='foreign_pid':sdk.pid+=10
    elif fault=='foreign_account':sdk.account='OtherService'
    elif fault=='already3':sdk.sid_type=3
    elif fault=='bad_bits':sdk.fault='sid_bits'
    elif fault=='backup_tamper':files.raw+=b' '
    elif fault=='image_tamper':files.image+=b'changed'
    try:
        result=dispatch(env);assert 'error' in result
        assert store.db.execute('SELECT count(*) FROM jobs').fetchone()[0]==0
        assert not any(isinstance(r,tuple) and r[0]=='change' for r in sdk.calls)
        assert store.db.execute('SELECT count(*) FROM host_control_admissions').fetchone()[0]==0
        assert daemon._retained_host_control_executors
        for root in getattr(api,'_original_service_sid_preflight_owners',()):
            if not root.retired:preflights.cleanup_original(root.api)
        daemon._reconcile_original_host_control_executor(result['request'],exe)
    finally:provider.close();store.close()

@pytest.mark.parametrize('fault',['intent_io','readback_io','terminal_io'])
def test_actual_store_fault_unknown_no_replay_or_configuration_cleanup(tmp_path,monkeypatch,fault):
    env=environment(tmp_path,monkeypatch);p,plan,sdk,files,api,store,provider,authority,exe,daemon,wrapper=env
    primary=OSError('public durable after-write failure')
    append=store.append_host_control_mutation_step_event
    def event(**kw):
        if (fault=='intent_io' and kw['event']=='APPLY_STEP_INTENT') or (fault=='readback_io' and kw['event']=='APPLY_STEP_READBACK_VERIFIED'):
            append(**kw);raise primary
        return append(**kw)
    monkeypatch.setattr(store,'append_host_control_mutation_step_event',event)
    finish=store.finish
    def terminal(*args,**kwargs):
        if fault=='terminal_io' and kwargs['status']=='SUCCEEDED':raise primary
        return finish(*args,**kwargs)
    monkeypatch.setattr(store,'finish',terminal)
    try:
        result=dispatch(env);assert result.get('error') is primary
        assert store.db.execute('SELECT status FROM jobs').fetchone()[0]=='UNKNOWN'
        assert sdk.sid_type==(0 if fault=='intent_io' else 3)
        before=list(sdk.calls)
        daemon._reconcile_original_host_control_executor(result['request'],exe)
        assert sdk.calls==before and store.db.execute('SELECT status FROM jobs').fetchone()[0]=='UNKNOWN'
        assert not daemon._retained_host_control_executors
    finally:provider.close();store.close()


def test_original_leaf_group_mix_and_legacy_service_intent_never_implicit_upgrade(tmp_path,monkeypatch):
    env=environment(tmp_path,monkeypatch);p,plan,sdk,files,api,store,provider,authority,exe,daemon,wrapper=env
    payload=plan.as_dict();source=payload['change_manifest'][0]
    fields={'action_id':source['action_id'],'resource_kind':'windows_service','resource_name':source['resource_name'],'operation':'UPDATE',
            'desired':source['desired'],'baseline_identity':source['proposed_baseline'],'baseline_sha256':source['proposed_baseline_sha256'],
            'backup_path':source['proposed_backup_path'],'backup_sha256':source['proposed_backup_sha256']}
    try:
        mixed=resources.build_host_change_plan(p,change_manifest=[fields,{**fields,'action_id':'second'}],operator_inputs={})
        with pytest.raises(Exception):changes._normalize_supported_actions(mixed,p)
        old=resources.build_host_change_plan(p,change_manifest=[{**fields,'desired':{k:v for k,v in fields['desired'].items() if k!='schema'}}],operator_inputs={})
        with pytest.raises(Exception):exe.apply(old,approval=resources.HostChangeApproval(old.sha256,'apply-once'))
        assert store.db.execute('SELECT count(*) FROM jobs').fetchone()[0]==0
        assert not any(isinstance(r,tuple) and r[0]=='change' for r in sdk.calls)
    finally:provider.close();store.close()


def test_original_controller_keeps_auxiliary_backup_owner_until_exactcleanup(tmp_path,monkeypatch):
    env=environment(tmp_path,monkeypatch);p,plan,sdk,files,api,store,provider,authority,exe,daemon,wrapper=env
    original=files.read;files.close_result=0
    def fail_read(*args):original(*args);raise files.primary
    files.k.ReadFile.callback=fail_read
    try:
        result=dispatch(env);assert 'error' in result
        roots=api._original_service_sid_preflight_owners;root=next(r for r in roots if not r.retired)
        assert root.first_cause is files.primary
        record=next(iter(daemon._retained_host_control_executors.values()));ref=weakref.ref(exe)
        env=None;del exe;gc.collect();retained=ref();assert retained is record['executor']
        with pytest.raises(Exception):daemon._reconcile_original_host_control_executor(result['request'],retained)
        files.k.CloseHandle=Function(lambda *a:pytest.fail('redirected file closer'))
        files.close_result=1;preflights.cleanup_original(root.api)
        daemon._reconcile_original_host_control_executor(result['request'],retained)
        assert not daemon._retained_host_control_executors and sdk.sid_type==0
        assert not any(isinstance(r,tuple) and r[0]=='change' for r in sdk.calls)
    finally:
        for root in getattr(api,'_original_service_sid_preflight_owners',()):
            if not root.retired:
                files.close_result=1;preflights.cleanup_original(root.api)
        provider.close();store.close()

@pytest.mark.parametrize('fault',['foreign_static','backup_tamper','receipt_schema','missing_durable'])
def test_actual_restore_foreign_or_nonoriginal_receipt_preserved(tmp_path,monkeypatch,fault):
    env=environment(tmp_path,monkeypatch);p,plan,sdk,files,api,store,provider,authority,exe,daemon,wrapper=env
    try:
        applied=dispatch(env);assert 'error' not in applied;receipt=applied['result'];before=list(sdk.calls)
        if fault=='foreign_static':sdk.security=_security_descriptor(dacl_present=False)
        elif fault=='backup_tamper':files.raw+=b' '
        elif fault=='receipt_schema':receipt=replace(receipt,receipt_sha256=leaf.digest({**leaf.receipt_body(plan,p,receipt.actions),'schema':'comsol-mcp.applied-host-change-receipt/2'}))
        elif fault=='missing_durable':store.db.execute("UPDATE operations SET result='{}' WHERE operation='host.change.apply'");store.db.commit()
        restored=dispatch(env,'RESTORE',receipt,'unused-restore-ref');assert 'error' in restored
        assert sdk.sid_type==3
        assert len([r for r in sdk.calls if isinstance(r,tuple) and r[0]=='change'])==1
        assert store.db.execute('SELECT count(*) FROM host_control_admissions').fetchone()[0]==1
        daemon._reconcile_original_host_control_executor(restored['request'],exe)
    finally:provider.close();store.close()


def test_actual_one_step_store_schema4_does_not_accept_WFP_sequence(tmp_path,monkeypatch):
    env=environment(tmp_path,monkeypatch);p,plan,sdk,files,api,store,provider,authority,exe,daemon,wrapper=env
    baseline=leaf._baseline
    def wrong(*args,**kwargs):
        row=baseline(*args,**kwargs);row['mutation_sequence']['schema']='comsol-mcp.host-control-mutation-sequence/2';return row
    monkeypatch.setattr(leaf,'_baseline',wrong)
    try:
        result=dispatch(env);assert 'error' in result
        assert store.db.execute('SELECT status FROM jobs').fetchone()[0]=='UNKNOWN'
        assert not any(isinstance(r,tuple) and r[0]=='change' for r in sdk.calls)
        daemon._reconcile_original_host_control_executor(result['request'],exe)
    finally:provider.close();store.close()


def test_actual_restore_after_effect_unknown_original_baseline_no_second_config(tmp_path,monkeypatch):
    env=environment(tmp_path,monkeypatch,1);p,plan,sdk,files,api,store,provider,authority,exe,daemon,wrapper=env
    restore_provider=None
    try:
        applied=dispatch(env);assert 'error' not in applied;receipt=applied['result']
        subject=api._service_sid_observe(p.windows_isolation.service_name)
        action=changes._normalize_supported_actions(plan,p)[1][0];backup=leaf.read_backup(action['proposed_backup_path'],action['proposed_backup_sha256'])
        original=backup['document']['subject']
        restore_sha=leaf.digest({'schema':'comsol-mcp.windows-service-sid-restore/1','source_plan_sha256':plan.sha256,'source_receipt_sha256':receipt.receipt_sha256,
                'profile_sha256':p.sha256,'subject_sha256':leaf.digest(subject),'backup_sha256':backup['raw_sha256'],
                'original_subject_sha256':leaf.digest(original),'resource_key':action['resource_key']})
        restore_provider=_write_provider(tmp_path/'fault-restore-provider',p,[_grant(p,'host.change.restore','restore-once',restore_sha,phase='RESTORE',source_plan=plan.sha256,source_receipt=receipt.receipt_sha256)])
        authority.authorization_verifier=restore_provider;exe.provider=restore_provider;env=list(env);env[6]=restore_provider
        original_change=sdk.change
        def change(*args):sdk.fault='change_io';sdk.close_result=0;return original_change(*args)
        sdk.a.ChangeServiceConfig2W.callback=change
        restored=dispatch(env,'RESTORE',receipt,'restore-once');assert 'error' in restored and sdk.sid_type==1
        assert [r[0] for r in store.db.execute('SELECT status FROM jobs ORDER BY rowid').fetchall()]==['SUCCEEDED','UNKNOWN']
        with pytest.raises(Exception):daemon._reconcile_original_host_control_executor(restored['request'],exe)
        sdk.close_result=1
        for entry in reversed(owned.registry(api).unresolved()):owned.registry(api).retry_owned(entry,api)
        daemon._reconcile_original_host_control_executor(restored['request'],exe)
        assert sdk.sid_type==1 and len([r for r in sdk.calls if isinstance(r,tuple) and r[0]=='change'])==2
        assert store.db.execute("SELECT count(*) FROM jobs WHERE status='UNKNOWN'").fetchone()[0]==1
    finally:
        if restore_provider:restore_provider.close()
        provider.close();store.close()
