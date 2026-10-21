from __future__ import annotations
from dataclasses import replace
import hashlib,json
from pathlib import Path
from types import SimpleNamespace
import pytest
from comsol_mcp import _windows_t037_bootstrap as bootstrap
from comsol_mcp._windows_t037 import WindowsT037ProfileV2
from comsol_mcp._windows_t037_broker import WindowsScmError
from tests.test_windows_host_change import _profile,_plan


def _document():
    profile=WindowsT037ProfileV2(host_resource_profile=_profile(),source_sha256='d'*64,
                               source_identity={'device':1,'inode':2},security_semantics='SOFTWARE_FIXTURE_ONLY')
    plan=_plan(profile.host_resource_profile)
    d={'schema':bootstrap.SCHEMA,'service_name':profile.service_name,'profile_source_sha256':profile.source_sha256,
       'host_profile_sha256':profile.sha256,'project_id':profile.host_resource_profile.project_id,
       'phase':'APPLY','authorization_ref':'apply-once','plan':{'payload_json':plan.payload_json,'sha256':plan.sha256},'receipt':None}
    return profile,plan,d


def _raw(d):return json.dumps(d,separators=(',',':')).encode()


def test_bootstrap_apply_parses_exact_typed_plan_and_source_binding():
    profile,plan,d=_document();request=bootstrap.parse_bootstrap(_raw(d),profile)
    assert request.plan==plan and request.receipt is None and request.phase=='APPLY'
    assert request.source_sha256==hashlib.sha256(_raw(d)).hexdigest()
    with pytest.raises(Exception):request.phase='RESTORE'


@pytest.mark.parametrize('field,value',[
 ('service_name','Other'),('profile_source_sha256','0'*64),('host_profile_sha256','0'*64),
 ('project_id','Other'),('phase','REPLAY'),('receipt',{}),('surprise',True)])
def test_bootstrap_rejects_changed_scope_and_unknown_fields(field,value):
    profile,_,d=_document();d[field]=value
    with pytest.raises(WindowsScmError):bootstrap.parse_bootstrap(_raw(d),profile)


@pytest.mark.parametrize('where',['outer','plan','payload'])
def test_bootstrap_duplicate_keys_rejected_at_every_json_boundary(where):
    profile,_,d=_document()
    if where=='outer':raw=_raw(d)[:-1]+b',"phase":"APPLY"}'
    elif where=='plan':raw=_raw(d).replace(b'"sha256":',b'"sha256":"duplicate","sha256":',1)
    else:
        d['plan']['payload_json']=d['plan']['payload_json'][:-1]+',"schema":"duplicate"}'
        d['plan']['sha256']=hashlib.sha256(d['plan']['payload_json'].encode()).hexdigest();raw=_raw(d)
    with pytest.raises(WindowsScmError) as captured:bootstrap.parse_bootstrap(raw,profile)
    assert captured.value.code=='SCM_BOOTSTRAP_DUPLICATE_KEY'


def test_bootstrap_restore_requires_exact_receipt_digest_and_deep_schema():
    from comsol_mcp._windows_host_change import _receipt_payload
    profile,plan,d=_document()
    row={'action_id':plan.as_dict()['change_manifest'][0]['action_id'],
         'created_resource_sha256':'a'*64,'prior_resource_sha256':None,'prior_backup_path':None}
    body=_receipt_payload(plan,profile.host_resource_profile,(row,))
    digest=hashlib.sha256(json.dumps(body,sort_keys=True,ensure_ascii=False,separators=(',',':'),allow_nan=False).encode()).hexdigest()
    d['phase']='RESTORE';d['authorization_ref']='restore-separate'
    d['receipt']={'plan_sha256':plan.sha256,'host_id':profile.host_resource_profile.host_id,
                  'platform':'windows','actions':[row],'receipt_sha256':digest}
    r=bootstrap.parse_bootstrap(_raw(d),profile)
    assert r.receipt.receipt_sha256==digest
    with pytest.raises(TypeError):r.receipt.actions[0]['created_resource_sha256']='b'*64
    d['receipt']['actions'][0]['invented']=True
    with pytest.raises(Exception):bootstrap.parse_bootstrap(_raw(d),profile)


def test_bootstrap_bounds_utf8_and_deep_json():
    profile,_,d=_document()
    for raw in [b'\xff',b'{}'*(bootstrap.MAX_REQUEST_BYTES//2+1),b'{"x":NaN}',b'['*40+b'0'+b']'*40]:
        with pytest.raises(WindowsScmError):bootstrap.parse_bootstrap(raw,profile)


@pytest.mark.parametrize('change',['none','inode','fingerprint','acl','links','oversize','truncated','grow'])
def test_private_bootstrap_pins_handle_path_identity_acl_and_closes_every_handle(tmp_path,monkeypatch,change):
    profile,_,d=_document();raw=_raw(d);calls=[]
    class Api:
        def __init__(self):self.offset=0;self.samples=0
        def open_security_path(self,path,*,directory):return [10,11]
        def open_source(self,parent,name):assert parent==11;return 12
        def source_fingerprint(self,h):
            self.samples+=1
            return ((1,2 if change!='inode' else 3),len(raw)+(1 if change=='truncated' else 0),
                    5,6+(self.samples-1 if change=='fingerprint' else 0),2 if change=='links' else 1) if change!='oversize' else ((1,2),bootstrap.MAX_REQUEST_BYTES+1,5,6,1)
        def read_chunk(self,h,size):
            block=raw[self.offset:self.offset+size];self.offset+=len(block)
            if not block and change=='grow':return b'x'
            return block
        def close(self,h):calls.append(h)
    checks=[]
    def verify(path,*,directory,expected_identity=None):
        checks.append(expected_identity)
        digest=('b'*64 if change=='acl' and expected_identity is not None else 'a'*64)
        return {'identity':{'device':1,'inode':2,'security_sha256':digest},'security_sha256':digest,
                'security_semantics':'windows-read-control-owner-dacl-persistent-acls'}
    monkeypatch.setattr(bootstrap,'_WINDOWS',True)
    monkeypatch.setattr('comsol_mcp._windows_parameter_publish._API',Api)
    monkeypatch.setattr('comsol_mcp._host_scoped_provider.verify_private_path_security',verify)
    if change=='none':assert bootstrap.read_private_bootstrap(tmp_path/'request.json')==raw
    else:
        with pytest.raises(WindowsScmError):bootstrap.read_private_bootstrap(tmp_path/'request.json')
    assert calls==[12,11,10]


def test_bootstrap_console_cli_and_direct_serve_refuse_before_side_effects(tmp_path):
    from comsol_mcp import _control_daemon as control
    with pytest.raises(SystemExit) as parsed:
        control.main(['--home',str(tmp_path/'uncreated'),'--windows-scm-bootstrap-request','request.json'])
    assert parsed.value.code==2 and not (tmp_path/'uncreated').exists()
    with pytest.raises(WindowsScmError) as direct:
        control.serve(tmp_path/'uncreated',_scm_bootstrap_request='request.json')
    assert direct.value.code=='SCM_BOOTSTRAP_SERVICE_REQUIRED'
    assert not (tmp_path/'uncreated').exists()


from tests.test_windows_host_change import host_env


@pytest.mark.parametrize('effect_unknown',[False,True])
def test_actual_bootstrap_scm_daemon_serial_lane_and_store_consumed_grant_never_replay(
        host_env,monkeypatch,tmp_path,effect_unknown):
    from concurrent.futures import ThreadPoolExecutor
    import threading
    from comsol_mcp._control_daemon import ControlDaemon
    from comsol_mcp._windows_host_change import WindowsHostChangeExecutor
    from comsol_mcp._w26_os_resources import ResourcePolicyError
    from comsol_mcp._windows_t037_broker import WindowsScmServiceBroker,_SCM_TEST_API_TOKEN,SERVICE_CONTROL_STOP
    from tests.test_windows_t037_broker import _ScmApi
    profile,plan,action,api,store,provider,authority,executor=host_env
    from tests.test_windows_host_change import _write_provider,_apply_grant
    provider.close()
    provider=_write_provider(tmp_path/'bootstrap-provider',profile,[_apply_grant(profile,plan)])
    authority.authorization_verifier=provider
    executor.provider=provider
    wrapper=WindowsT037ProfileV2(host_resource_profile=profile,source_sha256='d'*64,
                               source_identity={'device':1,'inode':2},security_semantics='SOFTWARE_FIXTURE_ONLY')
    # Real production methods use the fixture's actual authority/provider/Store.
    # The native constructor seam remains explicitly SOFTWARE_ONLY, exercising
    # actual executor journaling without calling Windows or changing OS policy.
    daemon=ControlDaemon.__new__(ControlDaemon)
    daemon._retained_host_control_executors={}
    daemon._windows_t037_profile=wrapper;daemon._windows_t037_profile_path='fixture-profile'
    daemon._scoped_host_provider=provider;daemon.project_authority=authority;daemon.store=store
    daemon.queue=ThreadPoolExecutor(max_workers=1)
    daemon._dispatch_condition=threading.Condition();daemon._closing=False;daemon._control_dispatch_inflight=0
    monkeypatch.setattr('comsol_mcp._windows_t037.load_trusted_profile',lambda _path:wrapper)
    monkeypatch.setattr(WindowsHostChangeExecutor,'from_trusted_profile_path',classmethod(lambda cls,**kw:executor))
    d={'schema':bootstrap.SCHEMA,'service_name':wrapper.service_name,'profile_source_sha256':wrapper.source_sha256,
       'host_profile_sha256':wrapper.sha256,'project_id':profile.project_id,'phase':'APPLY',
       'authorization_ref':'apply-once','plan':{'payload_json':plan.payload_json,'sha256':plan.sha256},'receipt':None}
    monkeypatch.setattr(bootstrap,'read_private_bootstrap',lambda _path:_raw(d))
    api.fail_after_create=effect_unknown
    scm=_ScmApi();result=[]
    def service_main(runtime):
        runtime.mark_running()
        future=bootstrap.submit_bootstrap(daemon,runtime,'explicit-private-request')
        if effect_unknown:
            with pytest.raises(Exception) as unknown:future.result(timeout=2)
            assert unknown.value.code=='HOST_CHANGE_RECOVERY_REQUIRED'
        else:result.append(future.result(timeout=2))
        admission=store.db.execute('SELECT * FROM host_control_admissions').fetchone()
        assert admission['state']==('RECOVERY_REQUIRED' if effect_unknown else 'APPLIED')
        before=list(api.calls)
        api.filters.clear() # Even restoring the synthetic baseline cannot reuse a consumed grant.
        with pytest.raises(ResourcePolicyError) as replay:
            bootstrap.submit_bootstrap(daemon,runtime,'same-explicit-request').result(timeout=2)
        assert replay.value.code=='HOST_CONTROL_AUTHORIZATION_REPLAYED'
        assert api.calls==before
        assert scm.handler(SERVICE_CONTROL_STOP,0,None,None)==0
    try:
        WindowsScmServiceBroker._for_software_test('ComsolHost',service_main,api=scm,token=_SCM_TEST_API_TOKEN).run()
    finally:
        daemon.queue.shutdown(wait=True)
        provider.close()
    assert daemon._control_dispatch_inflight==0
    if not effect_unknown:assert result[0].plan_sha256==plan.sha256
