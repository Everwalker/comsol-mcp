"""Real Controller/Store/ledger/adapter/Worker; COMSOL RPC and OS boundary fakes."""
import copy,json,time
from pathlib import Path
from types import SimpleNamespace
import pytest
from comsol_mcp import _g3_results as results
from comsol_mcp._java_worker import PersistentJavaWorker,JavaWorkerPaths
from comsol_mcp._execution_service import ExecutionService
from comsol_mcp._execution_contract import SessionLedger
from comsol_mcp._control_daemon import ControlDaemon
from comsol_mcp._managed_backend import ManagedBackend
from comsol_mcp._ephemeral_result_ticket import current_scope
from tests.test_g3_results import _activity015_partial_tree,Entity

class _RpcSDK:
    def __init__(self,tree):self.tree=tree;self.objects={};self.ids={};self.calls=[];self.scope=None;self.fingerprint='persistent-stable';self.counter=0;self.fault=None;self.inject=None
    def value(self,value):
        if isinstance(value,Entity):
            key=id(value)
            if key not in self.ids:self.ids[key]='handle-'+str(len(self.ids)+1);self.objects[self.ids[key]]=value
            return {'$worker_handle':self.ids[key],'generation':1,'java_type':'com.comsol.model.FixtureSDKEntity'}
        return value
    def request(self,body,timeout_s=None):
        self.calls.append(copy.deepcopy(body));self.scope=current_scope() or self.scope
        if self.inject is not None:
            injected=self.inject(body)
            if injected is not None:return injected
        try:
            kind=body['type']
            if kind=='model':value=self.tree.model
            elif kind=='model_snapshot':value={'model_tag':body['tag'],'server_instance_id':'public-test-epoch','fingerprint':self.fingerprint,'external_event_counter':self.counter}
            elif kind=='call':
                node=self.objects[body['handle']];method=body['method'];args=body.get('args',[])
                value=getattr(node,method)(*args)
            elif kind=='health':value={'instance_id':'synthetic-point-worker','generation':1,'connected':True,'server':'endpoint'}
            else:raise ValueError('unsupported synthetic RPC '+kind)
            return {'ok':True,'status':'SUCCEEDED','type':kind,'request_id':body['request_id'],'result':self.value(value)}
        except Exception as exc:return {'ok':False,'status':'FAILED','type':body['type'],'request_id':body['request_id'],'failure':{'code':'ENGINE_CALL_FAILED','message':str(exc)},'result':None}

def _public(tmp_path,monkeypatch):
    tree,nodes,args,partial=_activity015_partial_tree(monkeypatch)
    args['points']=[{'x':-10.0,'y':0.0}];args['frame']='spatial'
    sdk=_RpcSDK(tree);root=tmp_path/'project';root.mkdir()
    worker=PersistentJavaWorker(JavaWorkerPaths(root,Path('/usr/bin/java'),project_root=root),state_dir=tmp_path/'worker')
    worker._generation=1;worker._request=sdk.request
    service=ExecutionService(SessionLedger('public-test-session','public-test-epoch'),SimpleNamespace(model_snapshot=worker.backend_snapshot),project_root=root)
    ref=service.bind_model('Model')['execution']['model_ref']
    monkeypatch.setattr(ManagedBackend,'_require_g2_isolation',lambda self:{'status':'SYNTHETIC_OS_BOUNDARY_ONLY'})
    daemon=ControlDaemon(tmp_path/'control',service=service,registry={},worker=worker,project_root=root)
    daemon.backend.worker_identity={'connection_epoch':1,'worker_instance_id':'synthetic-point-worker','server_instance_id':'public-test-epoch'}
    created=daemon.dispatch({'operation':'project.create','arguments':{'label':'ephemeral read ticket software test','workspace':str(root/'workspace'),'policy':{'permissions':['inspect','project_write','compute'],'timeouts':{}}},'execution':{'request_id':'create','idempotency_key':'create'}})
    assert created['success'];project=created['data']['project']['project_id'];daemon.backend._bind_model_project(ref,project);daemon.backend.persist()
    return daemon,service,ref,project,sdk,tree,nodes,args

def _call(daemon,ref,project,args,key,revision):
    response=daemon.dispatch({'operation':'operation_call','arguments':{'operation_id':'result.at_points','arguments':copy.deepcopy(args)},'execution':{'project_id':project,'session_id':ref['session_id'],'model_ref':ref,'expected_revision':revision,'request_id':key,'idempotency_key':key,'rpc_timeout_s':10,'queue_timeout_s':10,'execution_timeout_s':None}})
    if response.get('execution',{}).get('job_id'):
        from tools.run_native_w23_te_science import _wait_job
        response=_wait_job(daemon,response,deadline_epoch=time.time()+10,cleanup_reserve_s=1,label=key,evidence=daemon.state_dir if hasattr(daemon,'state_dir') else daemon.backend.home)
    return response


def test_actual_public_builtin_ticket_preserves_revision_for_consecutive_units(tmp_path,monkeypatch):
    daemon,service,ref,project,sdk,tree,nodes,args=_public(tmp_path,monkeypatch)
    try:
        first=_call(daemon,ref,project,args,'Hz',0)
        assert first['success'],first
        failed=[r for r in sdk.scope.rows if r['status']=='FAILED']
        assert failed and all(r['handled_failure'] is True and r['ok'] is False and r['failure']['code']=='ENGINE_CALL_FAILED' for r in failed)
        assert first['data']['solution_axes']['pair_mapping_complete'] is False
        assert service.ledger._models['Model'].revision==0, (sdk.scope.invalid,sdk.scope.entered,sdk.scope.finished,sdk.scope.container,sdk.scope.feature,[(r['kind'],r['method'],r.get('value')) for r in sdk.scope.rows])
        args['spec']['units']=['GHz']
        second=_call(daemon,ref,project,args,'GHz',0)
        assert second['success'],second
        assert service.ledger._models['Model'].revision==0
        assert tree.numerical.items=={} and all(node.type_id=='Interp' for node in nodes)
    finally:daemon.close()


def _failed(body,status='FAILED'):
    return {'ok':False,'status':status,'type':body['type'],'request_id':body['request_id'],
            'failure':{'code':'ENGINE_CALL_FAILED','message':'synthetic native boundary refusal'},'result':None}

@pytest.mark.parametrize('fault', ['required-unit','required-data','required-budget','mutation','cleanup','timeout','unobserved',
    'incomplete','unknown-failure','wrong-request','wrong-receiver','wrong-mutation-receiver','epoch','generation','ref','ticket','callback','fingerprint','counter','tags'])
def test_actual_public_untrusted_or_incomplete_audit_never_suppresses_revision(tmp_path,monkeypatch,fault):
    daemon,service,ref,project,sdk,tree,nodes,args=_public(tmp_path,monkeypatch)
    injected=False
    def inject(body):
        nonlocal injected
        method=body.get('method')
        target={'required-unit':'getStringArray','required-data':'getData','required-budget':'getSize',
                'mutation':'set','cleanup':'remove','timeout':'getSolnums','unobserved':'getSolnums','incomplete':'getSolnums',
                'unknown-failure':'getSolnums','wrong-request':'getSolnums','wrong-receiver':'getSolnums','wrong-mutation-receiver':'getData',
                'epoch':'getData','generation':'getData','ref':'getData','ticket':'getData','callback':'getData','fingerprint':'getData','counter':'getData','tags':'remove'}[fault]
        if injected or method!=target or fault=='required-unit' and body.get('args')!=['unit']:return None
        injected=True
        if fault in {'required-unit','required-data','required-budget','mutation','cleanup'}:return _failed(body)
        if fault=='timeout':
            from comsol_mcp._java_worker import JavaWorkerTimeout
            raise JavaWorkerTimeout('synthetic native timeout')
        if fault=='unobserved':return _failed(body,'RUNNING')
        if fault=='incomplete':return {'ok':False,'type':body['type'],'request_id':body['request_id']}
        if fault=='unknown-failure':return {**_failed(body),'failure':{'code':'ENGINE_CALL_FAILED','execution_state_unknown':True,'message':'terminal FAILED but effect unknown'}}
        if fault=='wrong-request':return {**_failed(body),'request_id':'different actual request'}
        if fault in {'wrong-receiver','wrong-mutation-receiver'}:
            from comsol_mcp._java_worker import RemoteJava
            receiver=sdk.scope.container if fault=='wrong-mutation-receiver' else sdk.ids[id(tree.numerical)]
            try:RemoteJava(daemon.backend.worker,receiver,1)._call('set' if fault=='wrong-mutation-receiver' else 'getSolnums',*(['data','dset1'] if fault=='wrong-mutation-receiver' else [[]]))
            except Exception:pass
        if fault=='epoch':daemon.backend.worker_identity={**daemon.backend.worker_identity,'worker_instance_id':'changed-actor'}
        if fault=='generation':daemon.backend.worker._generation+=1
        if fault=='ref':
            from dataclasses import replace
            service.ledger._models['Model'].ref=replace(service.ledger._models['Model'].ref,generation=2)
        if fault=='ticket':object.__setattr__(sdk.scope.ticket,'request_hash','different-ticket')
        if fault=='callback':sdk.scope.binding.callback=lambda _: None
        if fault=='fingerprint':sdk.fingerprint='persistent changed'
        if fault=='counter':sdk.counter+=1
        if fault=='tags':tree.numerical.items['foreign-persistent']=Entity('Foreign')
        return None
    sdk.inject=inject
    try:
        response=_call(daemon,ref,project,args,'fault-'+fault,0)
        assert injected
        assert service.ledger._models['Model'].revision>0 or not response['success'],response
        if response['success']:assert service.ledger._models['Model'].revision==1
        assert sdk.scope is not None
        # The same server-only context cannot be consumed a second time.
        assert sdk.scope.consume({},'succeeded',False,sdk.scope.callback) is False
    finally:daemon.close()


def test_caller_json_cannot_mint_builtin_context_and_generic_evaluate_bumps(tmp_path,monkeypatch):
    daemon,service,ref,project,sdk,tree,nodes,args=_public(tmp_path,monkeypatch)
    try:
        from comsol_mcp._execution_contract import ModelRef
        result=service.execute_legacy('result_at_points',lambda _: {'success':True,'readonly':True,'changed':False,
            'cleanup':{'created':True,'removed':True},'data':{'cleanup':{'created':True,'removed':True}}},
            {'readonly':True},model_ref=service.ledger._models[ref['model_tag']].ref,expected_revision=0,request_id='fake-json',effect='evaluate')
        assert service.ledger._models['Model'].revision==1
    finally:daemon.close()


def test_actual_store_idempotency_replay_does_not_reuse_or_redispatch_private_proof(tmp_path,monkeypatch):
    daemon,service,ref,project,sdk,tree,nodes,args=_public(tmp_path,monkeypatch)
    try:
        first=_call(daemon,ref,project,args,'same-ticket',0)
        assert first['success'] and service.ledger._models['Model'].revision==0
        count=len(sdk.calls);scope=sdk.scope
        second=_call(daemon,ref,project,args,'same-ticket',0)
        assert second['success'] and len(sdk.calls)==count
        assert scope.consumed is True and scope.binding.used is True
        assert scope.consume(first,'succeeded',False,scope.callback) is False
    finally:daemon.close()


def test_ordinary_write_ticket_still_increments_managed_revision(tmp_path,monkeypatch):
    daemon,service,ref,project,sdk,tree,nodes,args=_public(tmp_path,monkeypatch)
    try:
        service.execute_legacy('node_property_set',lambda _: {'success':True,'data':{'status':'APPLIED'}}, {},
            model_ref=service.ledger._models['Model'].ref,expected_revision=0,request_id='ordinary-write',effect='project_write')
        assert service.ledger._models['Model'].revision==1
    finally:daemon.close()
