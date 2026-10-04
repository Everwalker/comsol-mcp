"""Owned-copy positives/negatives with actual synthetic store/ledger, no engine."""
import copy
import hashlib
from pathlib import Path
from types import SimpleNamespace
import pytest
from test_unit_a_node_public_api import Worker, Entity, Collection, method, path, request, ROOT
from comsol_mcp import _g2_checkpoint_ops as ops
from comsol_mcp import _g2_registry as registry
from comsol_mcp._execution_contract import ExecutionContractError, SessionLedger, model_ref_from_mapping
from comsol_mcp._execution_service import ExecutionService
from comsol_mcp._managed_backend import ManagedBackend
from comsol_mcp._operation_store import OperationStore
from comsol_mcp._control_daemon import ControlDaemon

def validate_output(data,schema):
    import json,jsonschema
    from referencing import Registry
    from referencing.jsonschema import DRAFT202012
    common=Path(__file__).resolve().parents[1]/'comsol_mcp/data/g2/common.schema.json'
    def no_external(uri):raise RuntimeError('UNIT_B_EXTERNAL_SCHEMA_RETRIEVAL_FORBIDDEN: '+uri)
    resources=Registry(retrieve=no_external).with_resource('common.schema.json',DRAFT202012.create_resource(json.loads(common.read_text())))
    jsonschema.Draft202012Validator(schema,registry=resources).validate(data)

class CloneWorker(Worker):
    def __init__(self):
        super().__init__();self.models={'m':self.model_node};self.calls=[];self.failure=None;self.fingerprint='source';self.counter=0
    def model(self,tag):return self.models[tag]
    def tags(self):return list(self.models)
    def load(self,file,tag=None):
        self.calls.append(('load',str(file),tag));self.models[tag]=copy.deepcopy(self.model_node);self.models[tag].tag_value=tag
        if self.failure=='load':raise RuntimeError('partial-load-original-error')
        return self.models[tag]
    def remove(self,tag):
        self.calls.append(('remove',tag))
        if self.failure=='cleanup':raise RuntimeError('cleanup-original-error')
        del self.models[tag]
    def model_snapshot(self,tag):
        assert tag in self.models
        return {'model_tag':tag,'server_instance_id':'server','fingerprint':self.fingerprint if tag=='m' else 'clone:'+tag,'external_event_counter':self.counter if tag=='m' else 0}
    backend_snapshot=model_snapshot
    def describe_public(self,node):
        d=super().describe_public(node)
        if isinstance(node,Collection):d['methods'].append(method('com.comsol.model.ModelEntityList','tags',(),'[Ljava.lang.String;'))
        elif isinstance(node,Entity):d['methods'].append(method('com.comsol.model.PropFeature','getType',(),'java.lang.String'))
        return d

@pytest.fixture
def env(tmp_path,monkeypatch):
    w=CloneWorker();store=OperationStore(tmp_path/'synthetic.sqlite');ledger=SessionLedger('session','server');service=ExecutionService(ledger,w,project_root=tmp_path)
    ref=service.bind_model('m')['execution']['model_ref'];b=ManagedBackend(tmp_path,store,service=service,worker=w,registry={});b.project_root=tmp_path
    b.worker_identity={'runtime_id':'synthetic','worker_instance_id':'synthetic','connection_epoch':1}
    b._bind_model_project(ref,'p');b.persist();monkeypatch.setattr(b,'_require_g2_isolation',lambda:{'scope':'SYNTHETIC_ONLY'})
    cp=tmp_path/'g2_artifacts/checkpoints/old.mph';cp.parent.mkdir(parents=True);cp.write_bytes(b'synthetic-historical-checkpoint')
    metadata={'checkpoint_id':'exact-old','path':str(cp),'sha256':hashlib.sha256(cp.read_bytes()).hexdigest(),'project_id':'p','source_binding':{'model_ref':ref,'revision':0,'fingerprint':'historical','external_event_counter':0}}
    store.persist_checkpoint(metadata['sha256'],metadata)
    saved=ops.pointer();monkeypatch.setattr(saved[0],'_current_model',w.model_node);monkeypatch.setattr(saved[0],'_current_model_origin','synthetic');monkeypatch.setattr(saved[0],'_current_model_path',str(cp))
    tickets=[];begin=SessionLedger.begin_write
    monkeypatch.setattr(SessionLedger,'begin_write',lambda self,*a,**kw:(tickets.append((a,kw)),begin(self,*a,**kw))[1])
    yield b,w,ref,metadata,tickets
    b.docs_index.close();store.close()

def execution(ref,revision=0,probe=False):
    d={'project_id':'p','session_id':'session','model_ref':ref,'request_id':'request','idempotency_key':'key'}
    if not probe:d['expected_revision']=revision
    return d

def probe(kind='invoke',setter=True):
    if kind=='invoke':p={'kind':kind,'checkpoint_id':'exact-old',**request('new' if setter else None),'readback':[{'path':path('a'),'names':['flag']}]}
    else:p={'kind':'create_feature','checkpoint_id':'exact-old','parent':ROOT,'collection':'feature','tag':'created','type_id':'Rectangle','readback':[{'path':{'segments':[{'accessor':'feature'}]}}]}
    return {'probe':p}

@pytest.mark.parametrize('route',['checkpoint.branch','checkpoint_branch','registry_call','operation_call'])
def test_branch_historical_input_fresh_independent_ref_durable_direction_and_source_revision(env,route):
    b,w,ref,cp,tickets=env;state=b.service.ledger._state_for(model_ref_from_mapping(ref));state.revision=3;b.persist();saved=ops.pointer()
    args={'checkpoint_id':'exact-old','label':'branch metadata only'}
    if route in {'registry_call','operation_call'}:args={'operation_id':'checkpoint.branch','arguments':args}
    out=b.invoke(route,args,execution(ref,3),'branch-op',None)
    assert out['success'],out;d=out['data'];new=d['branch_model_ref'];assert new!=ref and new['model_tag'] in w.models
    assert out['execution']['revision']==3 and d['checkpoint_source_binding']['revision']==0 and d['admission_source_identity']['revision']==3
    assert d['branch_revision']==b.service.ledger._state_for(model_ref_from_mapping(new)).revision==0
    assert b.model_project_binding(new)=={'attribution':'PROJECT_BOUND','project_id':'p'};assert d['persistence']['verified'];assert ops.pointer_equal(saved)
    assert Path(d['clone_input']['path']).read_bytes()==Path(cp['path']).read_bytes();assert len(tickets)==1;assert d['main_model_untouched'] is None
    record=d['persistence']['revision_record'];assert record==d['persistence']['expected_revision_record']
    assert set(record)=={'model_ref','revision','dirty','fingerprint','active_operation_id','project_id','attribution'}
    assert record['model_ref']==new and record['revision']==0 and record['dirty'] is False and record['active_operation_id'] is None
    assert record['project_id']=='p' and record['attribution']=='PROJECT_BOUND'
    assert d['revision_proof']['persisted_revision_record']==record
    branch=w.models[new['model_tag']];assert branch is not w.model_node
    changed=b.invoke('api.invoke',request('branch-only'),execution(new),'explicit-branch',None);assert changed['success'];assert branch.list.items['a'].comment=='branch-only';assert w.model_node.list.items['a'].comment==''

@pytest.mark.parametrize('case',['stale','retired','tamper','other_project','missing_lineage','different_epoch','future_revision','reserved','sha_alias','dirty','permissions'])
def test_branch_admission_refusal_before_copy_ticket(env,case):
    b,w,ref,cp,tickets=env;e=execution(ref);body={'checkpoint_id':'exact-old','label':'x'}
    if case=='stale':e['expected_revision']=9
    elif case=='retired':b.service.ledger.retire_model(model_ref_from_mapping(ref))
    elif case=='tamper':Path(cp['path']).write_bytes(b'tampered')
    elif case=='other_project':e['project_id']='other'
    elif case in {'missing_lineage','different_epoch','future_revision'}:
        cp=copy.deepcopy(cp)
        if case=='missing_lineage':del cp['source_binding']
        elif case=='different_epoch':cp['source_binding']['model_ref']['generation']+=1
        else:cp['source_binding']['revision']=9
        b.store.persist_checkpoint(cp['sha256'],cp)
    elif case=='reserved':body['checkpoint_id']='current'
    elif case=='sha_alias':body['checkpoint_id']=cp['sha256']
    elif case=='dirty':b.service.ledger._state_for(model_ref_from_mapping(ref)).dirty=True
    else:b.service.ledger.permissions={'inspect'}
    with pytest.raises(ExecutionContractError):b.invoke('checkpoint.branch',body,e,'reject',None)
    assert not tickets and not w.calls

@pytest.mark.parametrize('case',['load','bind','persist','persistence_readback','pointer','source','checkpoint_bytes','metadata_collision','tag_collision','ledger_collision','file_collision'])
def test_branch_partial_failures_no_false_unchanged_proof_preserve_evidence(env,monkeypatch,case):
    b,w,ref,cp,tickets=env
    if case=='load':w.failure='load'
    elif case=='bind':monkeypatch.setattr(b.service,'bind_model',lambda *a,**kw:(_ for _ in ()).throw(RuntimeError('bind-original')))
    elif case=='persist':monkeypatch.setattr(b,'persist',lambda:(_ for _ in ()).throw(RuntimeError('persist-original')))
    elif case=='persistence_readback':
        original=b.store.get_metadata
        def metadata(table,key):
            value=original(table,key)
            if table=='artifacts' and value is not None:return {**value,'label':'corrupted-readback'}
            return value
        monkeypatch.setattr(b.store,'get_metadata',metadata)
    elif case in {'pointer','source','checkpoint_bytes'}:
        load=w.load
        def changed(*a,**kw):
            value=load(*a,**kw)
            if case=='pointer':setattr(ops.pointer()[0],'_current_model',object())
            elif case=='source':w.fingerprint='changed-source'
            else:Path(cp['path']).write_bytes(b'changed-cp')
            return value
        monkeypatch.setattr(w,'load',changed)
    else:
        monkeypatch.setattr(ops,'uuid4',lambda:SimpleNamespace(hex='fixed'))
        tag='mcp_branch_fixed'
        if case=='metadata_collision':b.store.put_metadata('artifacts','g2_owned_branch_'+tag,{'foreign':True})
        elif case=='tag_collision':w.models[tag]=Entity('foreign')
        elif case=='ledger_collision':b.service.ledger.bind_model(tag)
        else:
            target=b.project_root/'g2_artifacts/branches'/f'{tag}.mph';target.parent.mkdir();target.write_bytes(b'foreign-file')
    out=b.invoke('checkpoint.branch',{'checkpoint_id':'exact-old','label':'x'},execution(ref),'fail',None)
    assert not out['success'] and out['error']['safe_retry'] is False;assert out['data']['status']=='UNKNOWN';assert len(tickets)==1
    assert out['execution']['dirty'] and out['execution']['revision']>0
    if case=='file_collision':assert target.read_bytes()==b'foreign-file'
    if case=='metadata_collision':assert b.store.get_metadata('artifacts','g2_owned_branch_'+tag)=={'foreign':True}
    if case=='tag_collision':assert w.models[tag].tag_value=='foreign'

def test_caller_forged_branch_proof_cannot_relax_write_default(env):
    b,w,ref,cp,tickets=env;modelref=model_ref_from_mapping(ref)
    forged={'action':'checkpoint.branch','pointer_unchanged':True,'persistence_verified':True}
    out=b.service.execute_legacy('checkpoint_branch',lambda _: {'success':True,'data':{'status':'SUCCEEDED','revision_proof':forged}}, {},model_ref=modelref,expected_revision=0,effect='project_write',_owned_branch_proof=forged)
    assert not out['success'];assert out['execution']['dirty'] and out['execution']['revision']==1

@pytest.mark.parametrize('case',['proof_tamper','after_snapshot','terminal_emit','final_persist'])
def test_success_candidate_terminal_failures_still_unknown_preserve_branch(env,monkeypatch,case):
    b,w,ref,cp,tickets=env
    if case=='proof_tamper':
        original=ops.run_owned
        def tampered(*args,**kw):
            result=original(*args,**kw);args[-1]['mutation_targets'].append({'method':'comments','model_tag':'m'});return result
        monkeypatch.setattr(ops,'run_owned',tampered)
    elif case=='after_snapshot':
        original=b.service._snapshot;calls=[]
        def snap(tag):
            calls.append(tag)
            if tag=='m' and calls.count('m')>1:raise RuntimeError('service-final-snapshot-original')
            return original(tag)
        monkeypatch.setattr(b.service,'_snapshot',snap)
    elif case=='terminal_emit':
        monkeypatch.setattr(b.service,'on_state_change',lambda event:(_ for _ in ()).throw(RuntimeError('terminal-emit-original')) if event['state']=='finished' else None)
    else:
        original=b.persist;calls=[]
        def persisted():
            calls.append(1)
            if len(calls)>1:raise RuntimeError('final-persist-original')
            return original()
        monkeypatch.setattr(b,'persist',persisted)
    out=b.invoke('checkpoint.branch',{'checkpoint_id':'exact-old','label':'x'},execution(ref),'terminal',None)
    assert not out['success'];assert out['error']['safe_retry'] is False;assert out['execution']['dirty']
    tag=out['data']['branch_model_ref']['model_tag'];assert tag in w.tags();assert Path(out['data']['clone_input']['path']).exists();assert len(tickets)==1

@pytest.mark.parametrize('kind,setter',[('invoke',False),('invoke',True),('create_feature',True)])
def test_probe_success_actual_private_target_readback_and_precise_cleanup_one_ticket(env,kind,setter):
    import jsonschema
    b,w,ref,cp,tickets=env;before=copy.deepcopy(w.model_node.list.order);saved=ops.pointer()
    out=b.invoke('api.probe',probe(kind,setter),execution(ref,probe=True),'probe-op',None)
    assert out['success'],out;d=out['data'];assert d['probe_result']=='supported';assert d['readback']['source_before']==d['readback']['source_after'];assert d['readback']['private_before'] and d['readback']['private_after']
    validate_output(d,registry.BY_ID['api.probe'].as_dict()['data_schema'])
    assert w.model_node.list.order==before and w.model_node.list.items['a'].comment=='';assert ops.pointer_equal(saved);assert len(tickets)==1
    assert d['cleanup']['model_removed'] and d['cleanup']['artifact_deleted'] and d['cleanup']['private_ref_retired'];assert w.tags()==['m'];assert out['execution']['revision']==1
    private=d['private_model_identity']['model_ref']
    with pytest.raises(ExecutionContractError):b.service.ledger._state_for(model_ref_from_mapping(private))
    if kind=='invoke':assert d['typed_return']['kind']==('node_ref' if setter else 'value')
    else:assert d['created_path']==path('created') and d['readback']['private_after'][0]['observations']['tags'][-1]=='created'

@pytest.mark.parametrize('case',['revision','empty_readback','wrong_kind','extra','untyped','claimed_read','unknown_method','compute_only','write_only','wrongref','refextra','refboolean'])
def test_probe_structural_permission_negatives_before_ticket_or_load(env,case):
    b,w,ref,cp,tickets=env;body=probe();e=execution(ref,probe=True);p=body['probe']
    if case=='revision':e['expected_revision']=0
    elif case=='empty_readback':p['readback']=[]
    elif case=='wrong_kind':p['kind']='java'
    elif case=='extra':p['Object']='arbitrary'
    elif case=='untyped':p['arguments']=['notTyped']
    elif case=='claimed_read':p['declared_effect']='READ'
    elif case=='unknown_method':p['method']='getClass'
    elif case=='compute_only':b.service.ledger.permissions={'compute'}
    elif case=='write_only':b.service.ledger.permissions={'project_write'}
    elif case=='wrongref':e['model_ref']={**ref,'generation':9}
    elif case=='refextra':e['model_ref']={**ref,'extra':'unknown'}
    else:e['model_ref']={**ref,'generation':True}
    with pytest.raises(ExecutionContractError):b.invoke('api.probe',body,e,'no',None)
    assert not tickets and not w.calls

@pytest.mark.parametrize('case',['load','cleanup','readback','source_readback','pointer','foreign_file','wrong_version'])
def test_probe_postdispatch_failure_unknown_preserves_original_and_owned_cleanup_scope(env,monkeypatch,case):
    b,w,ref,cp,tickets=env
    if case in {'load','cleanup'}:w.failure=case
    else:
        load=w.load
        def changed(*a,**kw):
            value=load(*a,**kw)
            if case=='readback':value.list.items['a'].getBoolean=lambda _:(_ for _ in ()).throw(RuntimeError('actual-readback-error'))
            elif case=='source_readback':w.model_node.list.items['a'].values['flag']=False
            elif case=='pointer':setattr(ops.pointer()[0],'_current_model',object())
            elif case=='foreign_file':
                target=Path(a[0]);target.unlink();target.write_bytes(b'foreign-replacement')
            else:w.override={'runtime_version':'6.3','interfaces':[],'methods':[]}
            return value
        monkeypatch.setattr(w,'load',changed)
    out=b.invoke('api.probe',probe(),execution(ref,probe=True),'bad',None)
    assert not out['success'];assert out['data']['status']=='UNKNOWN';assert out['error']['safe_retry'] is False;assert out['execution']['dirty'];assert len(tickets)==1
    if case=='foreign_file':assert Path(out['data']['private_model_identity']['path']).read_bytes()==b'foreign-replacement'
    if case=='cleanup':assert out['data']['cleanup']['errors'] and len(w.tags())==2
    else:assert w.tags()==['m']

@pytest.mark.parametrize('action',['checkpoint.branch','api.probe'])
def test_B_publication_closed_defaults_and_no_diff_activation(action):
    published=registry.BY_ID[action].as_dict();assert published['implementation_status']=='SUPPORTED_UNVERIFIED'
    schema=published['input_schema'];assert schema['additionalProperties'] is False
    if action=='api.probe':assert 'expected_revision' not in schema['properties'] and len(schema['properties']['probe']['oneOf'])==2
    assert published['data_schema']['additionalProperties'] is False;assert registry.is_implemented('checkpoint.diff') and registry.registry_describe('checkpoint.diff')['runtime_dispatch_contract']['handler']=='serial managed public typed pure-getter semantic projection'

@pytest.mark.parametrize('field,value',[('source_pointer_restored','claimed'),('complete',1),('typed_return',{'kind':'value','java_type':'java.lang.String','value':'untyped'}),('extra',True)])
def test_probe_output_type_and_closed_unknown_fields_rejected(env,field,value):
    import jsonschema
    b,w,ref,cp,tickets=env;out=b.invoke('api.probe',probe(setter=False),execution(ref,probe=True),'schema',None)
    assert out['success'];bad=copy.deepcopy(out['data']);bad[field]=value
    with pytest.raises(jsonschema.ValidationError):validate_output(bad,registry.BY_ID['api.probe'].as_dict()['data_schema'])

@pytest.mark.parametrize('route',['api.probe','api_probe','registry_call','operation_call'])
def test_stateful_probe_project_inner_acl_and_alias_one_claim(tmp_path,monkeypatch,route):
    root=tmp_path/'projects';root.mkdir();daemon=ControlDaemon(tmp_path/'control',project_root=root,registry={});calls=[]
    try:
        project=daemon.dispatch({'operation':'project.create','arguments':{'label':'probe','workspace':'probe','policy':{'permissions':['inspect','compute']}},'execution':{'request_id':'create','idempotency_key':'create'}})['data']['project']
        w=CloneWorker();daemon.backend.service=ExecutionService(SessionLedger('session','server'),w,project_root=root)
        ref=daemon.service.bind_model('m')['execution']['model_ref'];daemon.backend._bind_model_project(ref,project['project_id'])
        daemon.store.put_metadata('revisions',daemon.backend._model_project_key(ref),{'model_ref':ref,'project_id':project['project_id'],'revision':0})
        monkeypatch.setattr(daemon.backend,'invoke',lambda *a:calls.append(a) or {'success':True,'data':{'scope':'SW_BACKEND_STUB'}})
        e={**execution(ref,probe=True),'project_id':project['project_id'],'rpc_timeout_s':2}
        def dispatch(body,key):
            args={'operation_id':'api.probe','arguments':body} if route in {'registry_call','operation_call'} else body
            return daemon.dispatch({'operation':route,'arguments':args,'execution':{**e,'idempotency_key':key}})
        first=dispatch(probe(setter=False),'read');again=dispatch(probe(setter=False),'read');assert first['success'] and again['success'];assert len(calls)==1
        refused=dispatch(probe(setter=True),'write');assert not refused['success'] and refused['error']['code']=='PERMISSION_DENIED';assert len(calls)==1
    finally:daemon.close()

@pytest.mark.parametrize('action',['checkpoint.branch','api.probe'])
def test_stateful_daemon_real_synthetic_backend_alias_replay_never_second_private_load(tmp_path,monkeypatch,action):
    import jsonschema
    root=tmp_path/'projects';root.mkdir();daemon=ControlDaemon(tmp_path/'control',project_root=root,registry={})
    try:
        project=daemon.dispatch({'operation':'project.create','arguments':{'label':'owned copy','workspace':'work','policy':{'permissions':['inspect','compute','project_write']}},'execution':{'request_id':'create','idempotency_key':'create'}})['data']['project']
        w=CloneWorker();b=daemon.backend;b.worker=w;b.worker_identity={'runtime_id':'synthetic','worker_instance_id':'synthetic','connection_epoch':1}
        b.service=ExecutionService(SessionLedger('session','server'),w,project_root=root)
        ref=b.service.bind_model('m')['execution']['model_ref'];b._bind_model_project(ref,project['project_id']);b.persist()
        monkeypatch.setattr(b,'_require_g2_isolation',lambda:{'scope':'SYNTHETIC_ONLY'})
        cp=Path(project['workspace'])/'g2_artifacts/checkpoints/source.mph';cp.parent.mkdir(parents=True);cp.write_bytes(b'synthetic')
        sha=hashlib.sha256(cp.read_bytes()).hexdigest();daemon.store.persist_checkpoint(sha,{'checkpoint_id':'exact-old','path':str(cp),'sha256':sha,'project_id':project['project_id'],'source_binding':{'model_ref':ref,'revision':0}})
        args={'checkpoint_id':'exact-old','label':'copy'} if action=='checkpoint.branch' else probe()
        e={**execution(ref,probe=action=='api.probe'),'project_id':project['project_id'],'rpc_timeout_s':5}
        first=daemon.dispatch({'operation':action.replace('.','_'),'arguments':args,'execution':e});assert first['success'],first
        again=daemon.dispatch({'operation':'registry_call','arguments':{'operation_id':action,'arguments':args},'execution':e});assert again['success'],again
        assert len([c for c in w.calls if c[0]=='load'])==1
        assert first['execution']['operation_id']==again['execution']['operation_id']
        validate_output(first['data'],registry.BY_ID[action].as_dict()['data_schema'])
        changed=copy.deepcopy(args)
        if action=='checkpoint.branch':changed['label']='different'
        else:changed['probe']['arguments']=[{'kind':'string','shape':[],'data':'different'}]
        refused=daemon.dispatch({'operation':action,'arguments':changed,'execution':e});assert not refused['success'] and refused['error']['code']=='IDEMPOTENCY_CONFLICT';assert len([c for c in w.calls if c[0]=='load'])==1
    finally:daemon.close()

# Frozen semantic expectations from the accepted A publication; no external
# scratch baseline is read by these tests.
_A_CONTRACT_ACTIONS=("node.create","node.copy","node.remove","node.label_set","node.active_set","node.move","node.selection_get","node.selection_set","api.describe","api.invoke")
_OLD_NODE_ACTIONS=("node.inspect","node.children","node.find","node.property_schema","node.property_get","node.property_set","node.property_index_set","node.property_entry_set")
@pytest.mark.parametrize('action',_A_CONTRACT_ACTIONS+_OLD_NODE_ACTIONS)
def test_original18_publication_contract_matches_frozen_A_semantics(action):
    entry=registry.BY_ID[action];actual=entry.as_dict()
    if action in _OLD_NODE_ACTIONS:
        assert 'runtime_dispatch_contract' not in actual
        return
    expected={
        'handler':'serial managed actual public-interface adapter',
        'entrypoints':[action,entry.mcp_tool_name,'registry_call','operation_call'],
        'effect_resolution':'api.invoke: pure exact overload at project ACL; actual receiver/interface/version before write ticket; caller declaration is assertion only',
        'verification_scope':'software implementation only; native capability/dependency verification remains separately UNVERIFIED',
        'limits':['same-list copy only until cross-parent tag-path native verification','remove refuses unknown dependency coverage','selection geometric revision and spatial/object adapters remain unsupported'],
    }
    assert actual['runtime_dispatch_contract']==expected

@pytest.mark.parametrize('action',['checkpoint.branch','api.probe'])
def test_B_publication_contract_owned_lifetime_permissions_one_queue_and_no_A_limits(action):
    contract=registry.BY_ID[action].as_dict()['runtime_dispatch_contract']
    assert contract['handler']=='serial managed owned checkpoint-copy adapter'
    assert contract['entrypoints']==[action,registry.BY_ID[action].mcp_tool_name,'registry_call','operation_call']
    assert 'one existing serialized engine claim' in contract['engine_queue']
    assert 'UNKNOWN' in contract['failure_policy'] and 'safe_retry=false' in contract['failure_policy']
    assert 'UNVERIFIED' in contract['verification_scope']
    assert all('same-list copy' not in x and 'remove refuses' not in x and 'selection geometric' not in x for x in contract['limits'])
    if action=='checkpoint.branch':
        assert contract['permissions']=='project_write' and 'WRITE ticket' in contract['engine_queue']
        assert 'persist' in contract['lifetime'] and 'branch proof' in contract['revision_policy']
    else:
        assert 'compute plus server-resolved inner' in contract['permissions'] and 'COMPUTE ticket' in contract['engine_queue']
        assert 'retirement' in contract['lifetime'] and 'no automatic source apply' in contract['scope']
    assert registry.is_implemented('checkpoint.diff') and registry.registry_describe('checkpoint.diff')['runtime_dispatch_contract']['handler']=='serial managed public typed pure-getter semantic projection'

@pytest.mark.parametrize('case',['model_ref','revision','dirty','fingerprint','active_operation_id','missing','project_id','attribution'])
def test_branch_persisted_revision_readback_missing_or_tampered_unknown_retains_owned_evidence(env,monkeypatch,case):
    b,w,ref,cp,tickets=env;original=b.store.get_metadata;observed=[]
    def metadata(table,key):
        value=original(table,key)
        if table=='revisions' and isinstance(value,dict) and value.get('model_ref',{}).get('model_tag','').startswith('mcp_branch_') and 'dirty' in value:
            value=copy.deepcopy(value)
            if case=='missing':value=None
            elif case=='model_ref':value['model_ref']['model_tag']='foreign'
            elif case=='revision':value['revision']+=1
            elif case=='dirty':value['dirty']=True
            elif case=='fingerprint':value['fingerprint']='tampered'
            elif case=='active_operation_id':value['active_operation_id']='foreign'
            elif case=='project_id':value['project_id']='foreign'
            else:value['attribution']='UNATTRIBUTED'
            observed.append(copy.deepcopy(value))
        return value
    monkeypatch.setattr(b.store,'get_metadata',metadata)
    out=b.invoke('checkpoint.branch',{'checkpoint_id':'exact-old','label':'x'},execution(ref),'revision-corrupt',None)
    assert observed and not out['success'],out
    assert out['data']['status']=='UNKNOWN' and out['error']['safe_retry'] is False
    assert out['execution']['dirty'] and b.service.ledger._state_for(model_ref_from_mapping(ref)).dirty
    assert not out['data']['persistence']['verified'] and len(tickets)==1
    new=out['data']['branch_model_ref'];assert new['model_tag'] in w.tags()
    assert Path(out['data']['clone_input']['path']).read_bytes()==Path(cp['path']).read_bytes()
    assert w.model_node.list.items['a'].comment==''
    assert out['data']['revision_proof'] is None
    assert out['data']['persistence']['revision_record']!=out['data']['persistence']['expected_revision_record']

@pytest.mark.parametrize('case',['readback','persist','source_guard','proof_terminal','after_snapshot','terminal_emit','final_persist','bind_emit','guard_persist','guard_freeze'])
def test_unknown_branch_owned_epoch_fenced_before_new_key_ticket_or_receiver(env,monkeypatch,record_property,case):
    import json
    b,w,ref,cp,tickets=env
    if case in {'readback','guard_persist'}:
        original=b.store.get_metadata
        def metadata(table,key):
            value=original(table,key)
            if table=='revisions' and isinstance(value,dict) and value.get('model_ref',{}).get('model_tag','').startswith('mcp_branch_') and 'dirty' in value:
                value=copy.deepcopy(value);value['revision']+=1
            return value
        monkeypatch.setattr(b.store,'get_metadata',metadata)
        if case=='guard_persist':
            put=b.store.put_metadata
            def put_record(table,key,value):
                if table=='revisions' and value.get('model_ref',{}).get('model_tag','').startswith('mcp_branch_') and value.get('dirty'):
                    raise RuntimeError('owned-guard-persist-original')
                return put(table,key,value)
            monkeypatch.setattr(b.store,'put_metadata',put_record)
    elif case=='persist':monkeypatch.setattr(b,'persist',lambda:(_ for _ in ()).throw(RuntimeError('bind-persist-original')))
    elif case=='source_guard':
        load=w.load
        def loaded(*a,**kw):
            result=load(*a,**kw);w.fingerprint='source-guard-original';return result
        monkeypatch.setattr(w,'load',loaded)
    elif case=='proof_terminal':
        run=ops.run_owned
        def wrong_proof(*a,**kw):
            result=run(*a,**kw);a[-1]['mutation_targets'].append({'method':'comments','model_tag':'m'});return result
        monkeypatch.setattr(ops,'run_owned',wrong_proof)
    elif case=='after_snapshot':
        snap=b.service._snapshot;calls=[]
        def snapshot(tag):
            calls.append(tag)
            if tag=='m' and calls.count('m')>1:raise RuntimeError('terminal-snapshot-original')
            return snap(tag)
        monkeypatch.setattr(b.service,'_snapshot',snapshot)
    elif case in {'terminal_emit','guard_freeze','bind_emit'}:
        event='bound' if case=='bind_emit' else 'finished'
        monkeypatch.setattr(b.service,'on_state_change',lambda e:(_ for _ in ()).throw(RuntimeError(event+'-emit-original')) if e['state']==event else None)
        if case=='guard_freeze':monkeypatch.setattr(b,'_freeze_g2_model',lambda *a:(_ for _ in ()).throw(RuntimeError('source-freeze-secondary')))
    else:
        persist=b.persist;calls=[]
        def persisted():
            calls.append(1)
            if len(calls)>1:raise RuntimeError('terminal-persist-original')
            return persist()
        monkeypatch.setattr(b,'persist',persisted)
    out=b.invoke('checkpoint.branch',{'checkpoint_id':'exact-old','label':'fence'},execution(ref),'first',None)
    assert not out['success'] and out['data']['status']=='UNKNOWN' and out['error']['safe_retry'] is False,out
    guard=out['data']['evidence']['owned_ref_guard'];owned=guard['model_ref'];state=b.service.ledger._state_for(model_ref_from_mapping(owned))
    assert guard['in_memory_guarded'] and state.dirty and state.fingerprint is None and not state.retired
    assert state.ref.as_dict()==owned and state.revision==0 and state.active_operation_id is None
    assert owned['model_tag'] in w.tags() and Path(out['data']['evidence']['owned_path']).exists()
    assert out['execution']['dirty'] and out['data']['revision_proof'] is None
    if case=='guard_persist':assert any(x['message']=='owned-guard-persist-original' for x in guard['errors']) and 'persisted branch revision/ref/state' in out['data']['evidence']['failure']['message']
    if case=='guard_freeze':assert out['data']['evidence']['service_terminal_failure']['message']=='finished-emit-original' and out['data']['evidence']['terminal_source_guard_errors'][0]['message']=='source-freeze-secondary'
    if case in {'persist','final_persist'}:assert guard['errors'] and guard['persist_attempted']
    # Actual dirty gate must run before any receiver preparation/engine call,
    # not merely stop the subsequent mutation after public reflection.
    accesses=[]
    def no_engine(*a,**kw):accesses.append((a,kw));raise AssertionError('engine accessed for unknown owned ref')
    monkeypatch.setattr(w,'client',no_engine);monkeypatch.setattr(w,'describe_public',no_engine);monkeypatch.setattr(w,'model_snapshot',no_engine)
    before=len(tickets);e=execution(owned,state.revision);e.update(idempotency_key='new-key',request_id='new-request')
    with pytest.raises(ExecutionContractError) as refused:b.invoke('api.invoke',request('forbidden'),e,'new-key-operation',None)
    assert refused.value.code=='EXECUTION_STATE_UNKNOWN' and len(tickets)==before==1 and not accesses
    record_property('actual_owned_ref_guard',json.dumps({'scope':'SYNTHETIC_SOFTWARE_ONLY','first':out,'next_write_error':refused.value.code,'new_tickets':len(tickets)-before,'engine_accesses':accesses},sort_keys=True))

@pytest.mark.parametrize('case',['remove','bind_emit','source_guard','terminal_emit','final_persist','cleanup_persist'])
def test_unknown_probe_owned_epoch_fenced_or_exactly_retired_no_reopening(env,monkeypatch,record_property,case):
    import json
    b,w,ref,cp,tickets=env
    if case=='remove':w.failure='cleanup'
    elif case in {'bind_emit','terminal_emit'}:
        event='bound' if case=='bind_emit' else 'finished'
        monkeypatch.setattr(b.service,'on_state_change',lambda e:(_ for _ in ()).throw(RuntimeError(event+'-emit-original')) if e['state']==event else None)
    elif case=='source_guard':
        load=w.load
        def loaded(*a,**kw):
            value=load(*a,**kw);w.model_node.list.items['a'].values['flag']=False;return value
        monkeypatch.setattr(w,'load',loaded)
    else:
        persist=b.persist;calls=[];fail_at=2 if case=='cleanup_persist' else 3
        def persisted():
            calls.append(1)
            if len(calls)>=fail_at:raise RuntimeError(case+'-original')
            return persist()
        monkeypatch.setattr(b,'persist',persisted)
    out=b.invoke('api.probe',probe(),execution(ref,probe=True),'first',None)
    assert not out['success'] and out['data']['status']=='UNKNOWN' and out['error']['safe_retry'] is False,out
    guard=out['data']['evidence']['owned_ref_guard'];owned=guard['model_ref'];state=b.service.ledger._models[owned['model_tag']]
    assert state.ref.as_dict()==owned and guard['retired']==state.retired
    if case in {'remove','bind_emit'}:
        assert guard['in_memory_guarded'] and state.dirty and state.fingerprint is None and not state.retired
        assert Path(out['data']['evidence']['owned_path']).exists()
        if case=='remove':assert owned['model_tag'] in w.tags() and out['data']['cleanup']['errors']
    else:
        assert state.retired and guard['policy']=='already retired; no reopening or rebinding'
        assert 'in_memory_guarded' not in guard and out['data']['cleanup']['private_ref_retired']
    accesses=[]
    def no_engine(*a,**kw):accesses.append((a,kw));raise AssertionError('engine accessed for unknown/retired owned ref')
    monkeypatch.setattr(w,'client',no_engine);monkeypatch.setattr(w,'describe_public',no_engine);monkeypatch.setattr(w,'model_snapshot',no_engine)
    before=len(tickets);e=execution(owned,state.revision);e.update(idempotency_key='new-key',request_id='new-request')
    with pytest.raises(ExecutionContractError) as refused:b.invoke('api.invoke',request('forbidden'),e,'new-key-operation',None)
    assert refused.value.code==('MODEL_IDENTITY_MISMATCH' if state.retired else 'EXECUTION_STATE_UNKNOWN')
    assert len(tickets)==before==1 and not accesses and state.ref.as_dict()==owned
    record_property('actual_owned_ref_guard',json.dumps({'scope':'SYNTHETIC_SOFTWARE_ONLY','first':out,'next_write_error':refused.value.code,'new_tickets':len(tickets)-before,'engine_accesses':accesses},sort_keys=True))
