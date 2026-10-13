"""D1 controlled public-reader software observations; no native COMSOL proof."""
import copy
import json
from contextlib import nullcontext
from pathlib import Path
import pytest
from comsol_mcp._execution_contract import ExecutionContractError, SessionLedger, model_ref_from_mapping
from comsol_mcp._execution_service import ExecutionService
from comsol_mcp._managed_backend import ManagedBackend
from comsol_mcp._operation_store import OperationStore
from comsol_mcp._control_daemon import ControlDaemon
from comsol_mcp._g2_registry import operation_describe
from comsol_mcp import _g2_copy_subtree as subtree

P='com.comsol.model.';S='java.lang.String'
def parent():return {'segments':[{'collection':'study','tag':'std1'}]}
def node_path(tag):return {'segments':parent()['segments']+[{'collection':'feature','tag':tag}]}
def m(interface,name,returns,params=()):return {'interface':P+interface,'method':name,'parameters':list(params),'returns':returns}
def entity_methods():return [m('ModelEntity','tag',S),m('ModelEntity','label',S),m('ModelEntity','comments',S),m('ModelEntity','isActive','boolean')]

class Selection:
    def __init__(self):self.tag_value='selection';self.calls=[]
    def tag(self):return self.tag_value
    def label(self):return 'selection'
    def comments(self):return ''
    def isActive(self):return True
    def named(self):return ''
    def isInheriting(self):return False
    def entities(self):return [1,2]
    def dimension(self):return [2]
    def geom(self):return 'g'

class Node:
    def __init__(self,tag,child=False,type_id='fixture-study-step'):
        self.tag_value=tag;self.type_id=type_id;self.display='label-'+tag;self.comment='';self.values={'flag':True,'matrix':[[1.0]],'expr':'a+b'}
        self.children=Collection([]);self.sel=Selection();self.calls=[]
        if child:self.children=Collection([Node('child1'),Node('child2')])
    def tag(self):self.calls.append(('tag',));return self.tag_value
    def getType(self):return self.type_id
    def label(self):return self.display
    def comments(self):return self.comment
    def isActive(self):return True
    def properties(self):return list(self.values)
    def getValueType(self,name):return {'flag':'Boolean','matrix':'DoubleMatrix','expr':'String'}[name]
    def getAllowedPropertyValues(self,name):return []
    def getBoolean(self,name):return self.values[name]
    def getDoubleMatrix(self,name):return self.values[name]
    def getString(self,name):return self.values[name]
    def feature(self,tag=None):return self.children if tag is None else self.children.items[tag]
    def selection(self,name=None):return self.sel

class Collection:
    def __init__(self,nodes):self.items={n.tag_value:n for n in nodes};self.order=list(self.items);self.calls=[];self.worker=None;self.corrupt=None
    def tags(self):return list(self.order)
    def get(self,tag):return self.items[tag]
    def copy(self,tag,source):
        self.calls.append(('copy',tag,source));n=copy.deepcopy(self.items[source]);n.tag_value=tag;self.items[tag]=n;self.order.append(tag)
        if self.worker:self.worker.counter+=1
        if self.corrupt:self.corrupt(n)
        return n

class Study:
    def __init__(self,collection):self.collection=collection
    def feature(self,tag=None):return self.collection if tag is None else self.collection.items[tag]
class Model:
    def __init__(self,collection):self.study_node=Study(collection)
    def study(self,tag):assert tag=='std1';return self.study_node

class Worker:
    def __init__(self,child=False,type_id='fixture-study-step'):
        self.collection=Collection([Node('a',child,type_id),Node('b',False,type_id)]);self.collection.worker=self;self.model_node=Model(self.collection);self.counter=0;self.calls=[];self.override=None
    def client(self):return self
    def model(self,tag):assert tag=='m';self.calls.append(('model',tag));return self.model_node
    def operation_context(self,*a,**kw):return nullcontext()
    def model_snapshot(self,tag):return {'model_tag':tag,'server_instance_id':'server','fingerprint':'d1:'+str(self.counter),'external_event_counter':0}
    backend_snapshot=model_snapshot
    def describe_public(self,node):
        self.calls.append(('describe',type(node).__name__))
        if isinstance(node,Model):roles=['Model','AbstractModel','ModelEntity'];rows=[m('Model','study',P+'Study',(S,))]
        elif isinstance(node,Study):roles=['Study','ModelEntity'];rows=[m('Study','feature',P+'StudyFeatureList'),m('Study','feature',P+'StudyFeature',(S,))]
        elif isinstance(node,Collection):roles=['StudyFeatureList','PropFeatureList','ModelEntityList','ModelEntity'];rows=[m('ModelEntityList','tags',S+'[]'),m('ModelEntityList','get',P+'ModelEntity',(S,)),m('ModelEntityList','copy','void',(S,S)),m('ModelEntityList','remove','void',(S,))]
        elif isinstance(node,Selection):roles=['LocalSelection','Selection','AbstractSelection','ModelEntity'];rows=entity_methods()+[m('LocalSelection','named',S),m('Selection','isInheriting','boolean'),m('Selection','entities','int[]'),m('Selection','dimension','int[]'),m('Selection','geom',S)]
        else:
            roles=['StudyFeature','PropFeature','SelectionEntity','SelectionContainer','ModelEntity'];rows=entity_methods()+[m('PropFeature','getType',S),m('PropFeature','properties',S+'[]'),m('PropFeature','getValueType',S,(S,)),m('PropFeature','getAllowedPropertyValues',S+'[]',(S,)),m('PropFeature','getBoolean','boolean',(S,)),m('PropFeature','getDoubleMatrix','double[][]',(S,)),m('PropFeature','getString',S,(S,)),m('StudyFeature','feature',P+'StudyFeatureList'),m('StudyFeature','feature',P+'StudyFeature',(S,)),m('SelectionEntity','selection',P+'LocalSelection'),m('StudyFeature','selection',P+'LocalSelection',(S,))]
        result={'runtime_version':'6.4.0.293','interfaces':[P+r for r in roles],'methods':rows}
        return self.override(node,result) if self.override else result

def setup_copy_backend(b,w,ref,monkeypatch):
    b.worker=w
    if b.service.adapter is not w:
        b.service=ExecutionService(SessionLedger('session','server'),w,project_root=b.project_root)
        assert b.service.bind_model('m')['execution']['model_ref']==ref
    monkeypatch.setattr(b,'_require_g2_isolation',lambda:{'scope':'D1_SOFTWARE_TYPED_FAKE_ONLY'})
    b._bind_model_project(ref,'p')

def install_a_copy_fixture(env,monkeypatch):
    b,old,ref,tickets=env;w=Worker(type_id='Rectangle');setup_copy_backend(b,w,ref,monkeypatch)
    return b,w,ref,tickets

def arguments(tag='copy'):return {'source':node_path('a'),'target_parent':parent(),'tag':tag}
def execution(ref,revision=0,key='d1'):return {'project_id':'p','session_id':'session','model_ref':ref,'expected_revision':revision,'idempotency_key':key,'request_id':key}

@pytest.fixture
def env(tmp_path,monkeypatch):
    w=Worker();store=OperationStore(tmp_path/'operations.sqlite');service=ExecutionService(SessionLedger('session','server'),w,project_root=tmp_path)
    ref=service.bind_model('m')['execution']['model_ref'];b=ManagedBackend(tmp_path,store,service=service,worker=w,registry={});b.project_root=tmp_path;setup_copy_backend(b,w,ref,monkeypatch)
    tickets=[];original=SessionLedger.begin_write
    def begin(self,*a,**kw):tickets.append((a,kw));return original(self,*a,**kw)
    monkeypatch.setattr(SessionLedger,'begin_write',begin)
    yield b,w,ref,tickets
    b.docs_index.close();store.close()

def invoke(env):
    b,w,ref,tickets=env;return b.invoke('node.copy',arguments(),execution(ref),'d1-op',None)
def evidence(env,out,name):
    b,w,ref,tickets=env;state=b.service.ledger._state_for(model_ref_from_mapping(ref))
    row={'scope':'D1_TYPED_FAKE_SOFTWARE_NOT_NATIVE','case':name,'result':out,'copy_calls':w.collection.calls,'worker_calls':w.calls,'tickets':len(tickets),'source_state':{'ref':state.ref.as_dict(),'revision':state.revision,'dirty':state.dirty,'active_operation_id':state.active_operation_id},'native_tags':w.collection.tags()}
    (b.project_root/('d1-'+name+'.json')).write_text(json.dumps(row,indent=2,allow_nan=False)+'\n')

@pytest.mark.parametrize('child',[False,True])
def test_observed_copy_leaf_and_children_numeric_unit_stays_incomplete(env,child):
    b,w,ref,tickets=env
    if child:w.collection.items['a'].children=Collection([Node('child1'),Node('child2')])
    out=invoke(env);evidence(env,out,'positive-before-assert-'+str(child));assert out['success'],out;d=out['data'];assert d['status']=='INCOMPLETE' and d['complete'] is False
    assert d['dependency_check']['observed_comparison']['whole_equality']=='UNVERIFIED';assert len(tickets)==1 and w.collection.calls==[('copy','copy','a')]
    assert any(r['field']=='property:matrix.unit' and r['status']=='UNSUPPORTED' for r in d['coverage'])
    assert any(r['field']=='selection:geometry_revision' and r['status']=='UNSUPPORTED' for r in d['coverage'])
    assert d['readback']['source']['projection']['complete'] is False and d['readback']['target']['projection']['complete'] is False
    assert out['execution']['revision']==1 and out['execution']['model_ref']==ref;assert w.collection.items['copy'].display==w.collection.items['a'].display
    assert d['dependency_check']['observed_comparison']['matched_observed_nodes']>=(6 if child else 2)
    evidence(env,out,'positive-child-'+str(child))

@pytest.mark.parametrize('case',['missing_child','child_value','child_type','child_order','extra_child','label','expression','root_value','source_value','missing_target','extra_sibling','old_order','child_descriptor','partial_exception'])
def test_postcopy_observed_mismatch_unknown_retains_target(env,case):
    b,w,ref,tickets=env;w.collection.items['a'].children=Collection([Node('child1'),Node('child2')])
    def corrupt(n):
        if case=='missing_child':del n.children.items['child1'];n.children.order.remove('child1')
        elif case=='child_value':n.children.items['child1'].values['flag']=False
        elif case=='child_type':n.children.items['child1'].type_id='different'
        elif case=='child_order':n.children.order.reverse()
        elif case=='extra_child':n.children.items['extra']=Node('extra');n.children.order.append('extra')
        elif case=='label':n.display='not-authorized-label-normalization'
        elif case=='expression':n.values['expr']='copy/child1'
        elif case=='root_value':n.values['flag']=False
        elif case=='source_value':w.collection.items['a'].values['flag']=False
        elif case=='missing_target':del w.collection.items['copy'];w.collection.order.remove('copy')
        elif case=='extra_sibling':w.collection.items['extra']=Node('extra');w.collection.order.append('extra')
        elif case=='old_order':w.collection.order.reverse()
        elif case=='child_descriptor':n.children.items['child1'].getBoolean=lambda name:(_ for _ in ()).throw(RuntimeError('original-child-getter-failure'))
        elif case=='partial_exception':raise RuntimeError('original-partial-copy-cause')
    w.collection.corrupt=corrupt;out=invoke(env)
    assert not out['success'],out;assert out['data']['status']=='UNKNOWN' and out['execution']['dirty'] is True and out['error']['safe_retry'] is False
    assert len(tickets)==1 and w.collection.calls==[('copy','copy','a')];assert ('copy' in w.collection.items)==(case!='missing_target')
    if case=='partial_exception':assert 'original-partial-copy-cause' in json.dumps(out)
    if case=='child_descriptor':assert 'original-child-getter-failure' in json.dumps(out)
    evidence(env,out,'unknown-'+case)

@pytest.mark.parametrize('case',['missing_feature','missing_property','missing_tag','unknown_domain','null_allowed','getter_failure','known_paramcase','wrong_interface','wrong_runtime','selection_getter_failure','unknown_unit_reason','budget'])
def test_structural_source_failure_zero_ticket_zero_mutation(env,case,monkeypatch):
    b,w,ref,tickets=env;n=w.collection.items['a'];original=w.describe_public
    def override(node,r):
        if node is n:
            if case in {'missing_feature','missing_property','missing_tag'}:
                name={'missing_feature':'feature','missing_property':'properties','missing_tag':'tag'}[case];r['methods']=[m for m in r['methods'] if m['method']!=name]
            elif case=='unknown_domain':r['methods'].append(m('StudyFeature','unprojectedChild',P+'StudyFeatureList'))
            elif case=='known_paramcase':r['interfaces'].append(P+'ModelParamGroup')
            elif case=='wrong_interface':r['interfaces']=[P+'ModelEntity']
            elif case=='wrong_runtime':r['runtime_version']='6.3.0.290'
        return r
    w.override=override
    if case=='null_allowed':n.getAllowedPropertyValues=lambda name:None
    elif case=='getter_failure':n.getBoolean=lambda name:(_ for _ in ()).throw(RuntimeError('original-required-getter-cause'))
    elif case=='selection_getter_failure':n.sel.entities=lambda:(_ for _ in ()).throw(RuntimeError('original-selection-getter-cause'))
    elif case=='unknown_unit_reason':
        original_aux=subtree._auxiliary;monkeypatch.setattr(subtree,'_auxiliary',lambda row,interfaces:False if row['field']=='property:matrix.unit' else original_aux(row,interfaces))
    elif case=='budget':monkeypatch.setattr(subtree,'new_budget',lambda path:subtree.Budget({'max_nodes':0,'max_rpc':5000,'max_seconds':20.0}))
    with pytest.raises(ExecutionContractError) as caught:invoke(env)
    assert not tickets and not w.collection.calls and w.collection.tags()==['a','b'];assert not b.service.ledger._state_for(model_ref_from_mapping(ref)).dirty
    if case=='getter_failure':assert 'original-required-getter-cause' in json.dumps(caught.value.details)
    evidence(env,caught.value.as_dict(),'prewrite-'+case)

def test_old_a_incomplete_fixture_retained_as_structural_negative(tmp_path,monkeypatch):
    from test_unit_a_node_public_api import Worker as OldWorker,ROOT,path
    w=OldWorker();store=OperationStore(tmp_path/'old.sqlite');service=ExecutionService(SessionLedger('session','server'),w,project_root=tmp_path);ref=service.bind_model('m')['execution']['model_ref'];b=ManagedBackend(tmp_path,store,service=service,worker=w,registry={});b.project_root=tmp_path
    monkeypatch.setattr(b,'_require_g2_isolation',lambda:{'scope':'SOFTWARE_ONLY'});tickets=[];monkeypatch.setattr(SessionLedger,'begin_write',lambda *a,**k:tickets.append(a))
    try:
        with pytest.raises(ExecutionContractError):b.invoke('node.copy',{'source':path('a'),'target_parent':ROOT,'tag':'copy'},execution(ref),'old',None)
        assert not tickets and not w.model_node.list.calls;assert 'matrix' in w.model_node.list.items['a'].values
    finally:b.docs_index.close();store.close()

def test_remove_reports_actual_scope_but_does_not_mutate(env):
    b,w,ref,tickets=env
    with pytest.raises(ExecutionContractError) as e:b.invoke('node.remove',{'path':node_path('a')},execution(ref),'remove',None)
    assert e.value.details['complete'] is False and 'unknown_obligations' in e.value.details;assert not tickets and not w.collection.calls

def test_root_mapping_is_exact_does_not_mutate_or_normalize_strings(env):
    b,w,ref,tickets=env;budget=subtree.new_budget(node_path('a'));a=subtree.observe(w,'m',node_path('a'),budget,'source');before=copy.deepcopy(a)
    mapped=subtree._mapped(a,node_path('a'),node_path('copy'));assert a==before
    root=next(n for n in mapped['nodes'] if n['path']==node_path('copy'));assert root['fields']['label']['value']['data']=='label-a';assert root['fields']['property:expr']['value']['data']=='a+b'

@pytest.mark.parametrize('alias',['node.copy','node_copy','registry_call'])
def test_durable_parent_replay_no_callback_or_worker_calls(tmp_path,monkeypatch,alias):
    root=tmp_path/'projects';root.mkdir();daemon=ControlDaemon(tmp_path/'control',project_root=root,registry={})
    try:
        project=daemon.dispatch({'operation':'project.create','arguments':{'label':'d1','workspace':'d1','policy':{'permissions':['inspect','project_write']}},'execution':{'request_id':'create','idempotency_key':'create'}})['data']['project']
        w=Worker();daemon.backend.worker=w;daemon.backend.service=ExecutionService(SessionLedger('session','server'),w,project_root=root)
        ref=daemon.service.bind_model('m')['execution']['model_ref'];daemon.backend._bind_model_project(ref,project['project_id']);daemon.store.put_metadata('revisions',daemon.backend._model_project_key(ref),{'model_ref':ref,'project_id':project['project_id'],'revision':0})
        monkeypatch.setattr(daemon.backend,'_require_g2_isolation',lambda:{'scope':'SOFTWARE_FAKE_ONLY'})
        calls=[];original=daemon.backend.invoke
        def invoke_once(*a,**k):calls.append(a);return original(*a,**k)
        monkeypatch.setattr(daemon.backend,'invoke',invoke_once)
        body={'operation':'node.copy','arguments':arguments(),'execution':{**execution(ref),'project_id':project['project_id'],'rpc_timeout_s':2}}
        first=daemon.dispatch(body);assert first['success'],first;count=len(w.calls)
        second=copy.deepcopy(body);second['operation']=alias
        if alias=='registry_call':second['arguments']={'operation_id':'node.copy','arguments':arguments()}
        out=daemon.dispatch(second);assert out['success'],out;assert len(calls)==1 and len(w.calls)==count and w.collection.calls==[('copy','copy','a')]
    finally:daemon.close()

def test_changed_source_after_prepare_refuses_before_copy_single_existing_ticket(env,monkeypatch):
    b,w,ref,tickets=env;original=b.service.execute_legacy
    def changed(*a,**kw):
        w.collection.items['a'].values['flag']=False
        return original(*a,**kw)
    monkeypatch.setattr(b.service,'execute_legacy',changed)
    out=invoke(env);assert not out['success'],out
    assert w.collection.calls==[] and w.collection.tags()==['a','b'] and len(tickets)==1
    assert out['execution']['dirty'] is False and out['execution']['revision']==0
    assert 'before mutation' in json.dumps(out)
    evidence(env,out,'source-changed-before-copy')

def test_budget_after_copy_preserves_target_unknown_no_retry(env,monkeypatch):
    b,w,ref,tickets=env;original=subtree.new_budget;budgets=[]
    def budget(path):
        value=original(path);budgets.append(value);return value
    monkeypatch.setattr(subtree,'new_budget',budget)
    w.collection.corrupt=lambda target:budgets[0].limits.update(max_nodes=0)
    out=invoke(env);assert not out['success'],out;assert out['execution']['dirty'] and out['error']['safe_retry'] is False
    assert len(tickets)==1 and w.collection.calls==[('copy','copy','a')] and 'copy' in w.collection.items
    assert 'TRUNCATED' in json.dumps(out);evidence(env,out,'budget-post-copy')

def test_unknown_durable_parent_replay_zero_calls_and_new_key_does_not_rewrite(tmp_path,monkeypatch):
    root=tmp_path/'projects';root.mkdir();daemon=ControlDaemon(tmp_path/'control',project_root=root,registry={})
    try:
        project=daemon.dispatch({'operation':'project.create','arguments':{'label':'d1 unknown','workspace':'d1u','policy':{'permissions':['inspect','project_write']}},'execution':{'request_id':'create','idempotency_key':'create'}})['data']['project']
        w=Worker();w.collection.corrupt=lambda n:setattr(n,'display','unexpected-label');daemon.backend.worker=w;daemon.backend.service=ExecutionService(SessionLedger('session','server'),w,project_root=root)
        ref=daemon.service.bind_model('m')['execution']['model_ref'];daemon.backend._bind_model_project(ref,project['project_id']);daemon.store.put_metadata('revisions',daemon.backend._model_project_key(ref),{'model_ref':ref,'project_id':project['project_id'],'revision':0})
        monkeypatch.setattr(daemon.backend,'_require_g2_isolation',lambda:{'scope':'SOFTWARE_FAKE_ONLY'});calls=[];original=daemon.backend.invoke
        def invoked(*a,**kw):calls.append(a);return original(*a,**kw)
        monkeypatch.setattr(daemon.backend,'invoke',invoked)
        body={'operation':'node.copy','arguments':arguments(),'execution':{**execution(ref),'project_id':project['project_id'],'rpc_timeout_s':2}}
        first=daemon.dispatch(body);assert not first['success'] and first['execution']['dirty'];before=len(w.calls)
        second=daemon.dispatch(copy.deepcopy(body));assert not second['success'] and len(calls)==1 and len(w.calls)==before
        changed=copy.deepcopy(body);changed['execution'].update(idempotency_key='new-key',request_id='new-request',expected_revision=1);changed['arguments']['tag']='second-copy'
        third=daemon.dispatch(changed);assert not third['success'] and w.collection.calls==[('copy','copy','a')] and len(w.calls)==before
        (root/'d1-unknown-replay.json').write_text(json.dumps({'scope':'SOFTWARE_ONLY_REAL_DURABLE_STORE','first':first,'replay':second,'new_key_refusal':third,'worker_calls_before_after':[before,len(w.calls)],'copy_calls':w.collection.calls,'backend_invocations':len(calls)},indent=2)+'\n')
    finally:daemon.close()
