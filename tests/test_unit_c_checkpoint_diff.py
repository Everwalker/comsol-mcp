"""Actual synthetic store/ledger/queue plus fixed public getter receivers.

These receivers implement documented pure signatures. They are software
fixtures, never COMSOL native, model correctness, or full API acceptance.
"""
import copy
import hashlib
import json
from pathlib import Path
from types import SimpleNamespace
import pytest
from comsol_mcp import _g2_checkpoint_diff as diff
from comsol_mcp import _g2_checkpoint_ops as ops
from comsol_mcp import _g2_registry as registry
from comsol_mcp._execution_contract import ExecutionContractError, SessionLedger, model_ref_from_mapping
from comsol_mcp._execution_service import ExecutionService
from comsol_mcp._managed_backend import ManagedBackend
from comsol_mcp._operation_store import OperationStore
from comsol_mcp._control_daemon import ControlDaemon
from test_unit_a_node_public_api import method
from test_unit_b_checkpoint_probe import validate_output

P='com.comsol.model.'
S='java.lang.String'
SA='java.lang.String[]'
ROOT={'segments':[]}
def path(*segments):return {'segments':list(segments)}
def member(collection,tag):return {'collection':collection,'tag':tag}
def accessor(name):return {'accessor':name}

class PureNode:
    """No guessed reflection: fixed public descriptors and getter calls only."""
    def __init__(self,tag,roles=('ModelEntity',),properties=None,expressions=None):
        self.tag_value=tag;self.roles=list(roles);self.label_value=tag;self.active_value=True;self.comment='';self.calls=[];self.specs={};self.values={};self.children={};self.props=properties;self.expr=expressions;self.faults={}
        if 'ModelEntity' in self.roles:
            for n,r,v in [('tag',S,tag),('label',S,tag),('isActive','boolean',True),('comments',S,'')]:self.add(n,r,v,'ModelEntity')
        if 'PropFeature' in self.roles:
            self.add('getType',S,'Rectangle','PropFeature');self.add('properties',SA,list(properties or {}),'PropFeature')
            for n,r in [('getValueType',S),('getAllowedPropertyValues',SA),('getBoolean','boolean'),('getString',S),('getIntArray','int[]'),('getDoubleMatrix','double[][]')]:self.add(n,r,None,'PropFeature',(S,))
        if 'ExpressionBase' in self.roles:
            for n,r in [('varnames',SA),('get',S),('descr',S)]:self.add(n,r,None,'ExpressionBase',() if n=='varnames' else (S,))
            if 'ParamBase' in self.roles:self.add('evaluateUnit',S,None,'ParamBase',(S,))
    def add(self,name,returns,value,owner,params=()):
        self.specs[(name,tuple(params))]=method(P+owner,name,params,returns);self.values[(name,tuple(params))]=value
    def child(self,name,node,returns):
        self.children[name]=node;self.add(name,P+returns,None,self.roles[0])
        if isinstance(node,PureList):self.add(name,P+'ModelEntity',None,self.roles[0],(S,))
        return self
    def __getattr__(self,name):
        specs=object.__getattribute__(self,'__dict__').get('specs',{})
        if not any(n==name for n,p in specs):raise AttributeError(name)
        def call(*args):
            self.calls.append((name,args))
            if name in self.faults:
                problem=self.faults[name]
                if isinstance(problem,Exception):raise problem
                return problem
            if name in self.children:return self.children[name].get(args[0]) if args else self.children[name]
            if name=='tag':return self.tag_value
            if name=='label':return self.label_value
            if name=='comments':return self.comment
            if name=='isActive':return self.active_value
            if name=='varnames':return list(self.expr or {})
            if name in {'get','descr','evaluateUnit'} and self.expr is not None:
                item=self.expr[args[0]];return item[{'get':0,'descr':1,'evaluateUnit':2}[name]]
            if self.props is not None and name in {'getValueType','getAllowedPropertyValues','getBoolean','getString','getIntArray','getDoubleMatrix'}:
                item=self.props[args[0]];return item[0] if name=='getValueType' else item[2] if name=='getAllowedPropertyValues' else item[1]
            candidates=[v for (n,p),v in self.values.items() if n==name and len(p)==len(args)]
            assert len(candidates)==1,(name,args)
            return candidates[0]
        return call

class PureList(PureNode):
    def __init__(self,items=()):
        super().__init__('list',('ModelEntityList',));self.items={n.tag_value:n for n in items};self.order=list(self.items)
        self.add('tags',SA,None,'ModelEntityList');self.add('get',P+'ModelEntity',None,'ModelEntityList',(S,))
    def tags(self):self.calls.append(('tags',()));return list(self.order)
    def get(self,tag):self.calls.append(('get',(tag,)));return self.items[tag]

def model():
    root=PureNode('m',('Model','ModelEntity'))
    root.add('getComsolVersion',S,diff.VERSION,'Model');root.add('getUsedProducts',SA,[],'Model');root.add('file',P+'FileResourceList',None,'Model');root.add('getFileResourceTags',SA,[],'Model')
    for n in ('component','func','study','sol'):root.child(n,PureList(),'ModelEntityList')
    root.child('result',PureNode('results'),'ModelEntity')
    param=PureNode('parameters',('ModelParam','ParamBase','ExpressionBase'),expressions={'length':('1[mm]','length','mm')})
    group=PureNode('pg1',('ModelParamGroup','ModelEntity','ParamBase','ExpressionBase'),expressions={'width':('2[mm]','width','mm')})
    param.child('group',PureList([group]),'ModelEntityList');root.child('param',param,'ModelParam')
    node=PureNode('a',('ModelEntity','PropFeature'),properties={'flag':('Boolean',True,[])})
    root.child('feature',PureList([node]),'ModelEntityList');return root

class PureWorker:
    def __init__(self):self.model_node=model();self.models={'m':self.model_node};self.calls=[];self.descriptors=[];self.failure=None;self.fingerprint='source';self.counter=0;self.historical=copy.deepcopy(self.model_node);self.override=None
    def client(self):return self
    def model(self,tag):self.calls.append(('model',tag));return self.models[tag]
    def tags(self):self.calls.append(('tags',));return list(self.models)
    def load(self,file,tag=None):
        self.calls.append(('load',str(file),tag));self.models[tag]=copy.deepcopy(self.historical);self.models[tag].tag_value=tag
        if self.failure=='load':raise RuntimeError('partial-load-original')
        return self.models[tag]
    def remove(self,tag):
        self.calls.append(('remove',tag))
        if self.failure=='cleanup':raise RuntimeError('cleanup-original')
        del self.models[tag]
    def describe_public(self,node):
        self.descriptors.append(node)
        if self.override is not None:return copy.deepcopy(self.override)
        return {'runtime_version':diff.VERSION,'interfaces':[P+r for r in node.roles],'methods':list(node.specs.values())}
    def backend_snapshot(self,tag):
        assert tag in self.models
        return {'model_tag':tag,'server_instance_id':'server','fingerprint':self.fingerprint if tag=='m' else 'clone:'+tag,'external_event_counter':self.counter if tag=='m' else 0}
    model_snapshot=backend_snapshot
    def operation_context(self,*a,**kw):
        from contextlib import nullcontext
        return nullcontext()

@pytest.fixture
def env(tmp_path,monkeypatch):
    w=PureWorker();store=OperationStore(tmp_path/'synthetic.sqlite');service=ExecutionService(SessionLedger('session','server'),w,project_root=tmp_path)
    ref=service.bind_model('m')['execution']['model_ref'];b=ManagedBackend(tmp_path,store,service=service,worker=w,registry={});b.project_root=tmp_path
    b.worker_identity={'runtime_id':'synthetic','worker_instance_id':'synthetic','connection_epoch':1};b._bind_model_project(ref,'p');b.persist();monkeypatch.setattr(b,'_require_g2_isolation',lambda:{'scope':'SYNTHETIC_OWNED_ONLY'})
    cp=tmp_path/'g2_artifacts/checkpoints/old.mph';cp.parent.mkdir(parents=True);cp.write_bytes(b'synthetic-checkpoint')
    row={'checkpoint_id':'exact-old','path':str(cp),'sha256':hashlib.sha256(cp.read_bytes()).hexdigest(),'project_id':'p','runtime_version':diff.VERSION,'source_binding':{'model_ref':ref,'revision':0,'fingerprint':'historical'}};store.persist_checkpoint(row['sha256'],row)
    saved=ops.pointer();monkeypatch.setattr(saved[0],'_current_model',w.model_node);monkeypatch.setattr(saved[0],'_current_model_origin','synthetic');monkeypatch.setattr(saved[0],'_current_model_path',str(cp))
    tickets=[];begin=SessionLedger.begin_write;monkeypatch.setattr(SessionLedger,'begin_write',lambda self,*a,**kw:(tickets.append((a,kw)),begin(self,*a,**kw))[1])
    yield b,w,ref,row,tickets
    b.docs_index.close();store.close()

def envelope(ref):return {'project_id':'p','session_id':'session','model_ref':ref,'request_id':'c-request','idempotency_key':'c-key'}
def invoke(env,left='current',right='current',scope=None,route='checkpoint.diff'):
    b,w,ref,row,tickets=env;args={'left':left,'right':right}
    if scope is not None:args['scope']=scope
    if route in {'registry_call','operation_call'}:args={'operation_id':'checkpoint.diff','arguments':args}
    return b.invoke(route,args,envelope(ref),'c-op',None)
def projection(worker,scope=None):
    norm=diff.normalize({'left':'current','right':'current',**({'scope':scope} if scope is not None else {})})
    return diff.Reader(worker,'m',norm['scope'],diff.Budget(norm['budget']),norm['page_size'],'software').project()
def fields(proj):return {diff.path_key(n['path']):n['fields'] for n in proj['nodes']}
def preserve_control(env,name,out):
    b,w,ref,row,tickets=env
    state=b.service.ledger._state_for(model_ref_from_mapping(ref))
    evidence={'scope':'SYNTHETIC_SOFTWARE_ONLY_NO_COMSOL','case':name,'result':out,'actual_synthetic_client_calls':w.calls,'actual_write_ticket_count':len(tickets),'source_state':{'model_ref':state.ref.as_dict(),'revision':state.revision,'dirty':state.dirty,'fingerprint':state.fingerprint,'active_operation_id':state.active_operation_id}}
    (b.project_root/('unit-c-control-'+name+'.json')).write_text(json.dumps(evidence,indent=2,allow_nan=False)+'\n')

@pytest.mark.parametrize('route',['checkpoint.diff','checkpoint_diff','registry_call','operation_call'])
def test_full_model_complete_actual_getters_no_write_ticket_all_routes(env,route):
    b,w,ref,row,tickets=env;out=invoke(env,route=route);assert out['success'],out;data=out['data']
    assert data['comparison']=={'status':'EQUAL','equal':True,'complete':True};assert not tickets;assert data['normalized_scope']=={'mode':'model'}
    assert len(w.descriptors)>1;assert w.model_node.children['param'].calls.count(('varnames',()))>=4;assert out['execution']['revision']==0
    validate_output(data,registry.BY_ID['checkpoint.diff'].as_dict()['data_schema'])
    preserve_control(env,'complete-'+route.replace('.','_'),out)

@pytest.mark.parametrize('case',['label','active','comments','expression','unit','native_order','add_node','remove_node','property','type'])
def test_historical_real_getters_changes_despite_same_shallow_snapshot(env,case):
    b,w,ref,row,tickets=env;old=w.historical;node=old.children['feature'].items['a']
    if case=='label':node.label_value='different'
    elif case=='active':node.active_value=False
    elif case=='comments':node.comment='changed'
    elif case in {'expression','unit'}:old.children['param'].expr['length']=('3[mm]' if case=='expression' else '1[mm]','length','cm' if case=='unit' else 'mm')
    elif case=='native_order':
        second=PureNode('b');w.model_node.children['feature'].items['b']=copy.deepcopy(second);w.model_node.children['feature'].order.append('b');old.children['feature'].items['b']=second;old.children['feature'].order.insert(0,'b')
    elif case=='add_node':old.children['feature'].items['b']=PureNode('b');old.children['feature'].order.append('b')
    elif case=='remove_node':old.children['feature'].items.clear();old.children['feature'].order.clear()
    elif case=='type':node.values[('getType',())]='Block'
    else:node.props['flag']=('Boolean',False,[])
    out=invoke(env,'exact-old','current');assert out['success'],out;data=out['data'];assert data['comparison']['status']=='DIFFERENT';assert data['changes'];assert data['source_observation']['snapshot_identity_unchanged']
    assert not tickets and len([c for c in w.calls if c[0]=='load'])==1;assert w.tags()==['m'];assert all(c['removed'] and c['input_deleted'] for c in data['owned_copies']);assert data['provenance']['checkpoint_after'][0]['identity_unchanged']
    assert data['provenance']['temporary_root_tags_not_semantic'];assert not any(c['field']=='tag' and not c['path']['segments'] for c in data['changes'])
    preserve_control(env,'difference-'+case,out)

def test_same_selector_reloads_both_and_same_bytes_do_not_bypass_getters(env):
    b,w,ref,row,tickets=env;out=invoke(env,'exact-old','exact-old');assert out['data']['comparison']['status']=='EQUAL';assert len([c for c in w.calls if c[0]=='load'])==2
    w.model_node.faults['label']=RuntimeError('actual-getter-failed');out=invoke(env);assert out['data']['comparison']=={'status':'INCOMPLETE','equal':None,'complete':False};assert any('actual-getter-failed' in e['message'] for e in out['data']['errors'])

@pytest.mark.parametrize('scope',[{}, {'mode':'model'}, {'paths':[path(member('feature','a'))]}, {'properties':[{'path':path(member('feature','a')),'names':['flag']}] }])
def test_closed_scope_defaults_subtree_property_and_same_current_read(env,scope):
    out=invoke(env,scope=scope);assert out['success'],out;assert out['data']['comparison']['complete'];assert out['data']['normalized_scope']['mode']==('paths' if 'paths' in scope or 'properties' in scope else 'model')
    if 'properties' in scope:assert all(c['field'].startswith(('resolve:','property:')) for c in out['data']['coverage'])

@pytest.mark.parametrize('body',[{}, {'left':'Current','right':'current'},{'left':'current','right':''},{'left':'current','right':'current','bad':1},
 {'left':'current','right':'current','scope':{'mode':'model','paths':[]}}, {'left':'current','right':'current','scope':{'paths':[]}},
 {'left':'current','right':'current','scope':{'paths':[{'segments':[{'accessor':'getClass'}]}]}},
 {'left':'current','right':'current','scope':{'properties':[{'path':ROOT,'names':['x','x']}]}},
 {'left':'current','right':'current','scope':{'page_size':True}}, {'left':'current','right':'current','scope':{'budget':{'max_nodes':0}}},
 {'left':'current','right':'current','scope':{'budget':{'new_limit':1}}}, {'left':'current','right':'current','scope':{'unknown':1}}])
def test_selector_scope_wire_invalid_before_getters(env,body):
    b,w,ref,row,tickets=env;before=len(w.descriptors)
    with pytest.raises(ExecutionContractError):b.invoke('checkpoint.diff',body,envelope(ref),'invalid',None)
    assert len(w.descriptors)==before and not tickets and not any(c[0]=='load' for c in w.calls)

@pytest.mark.parametrize('case',['sha_alias','label_alias','tamper','symlink','reserved','duplicate','other_project','no_binding','bool_generation','binding_disagree','dirty','no_inspect','no_isolation'])
def test_authoritative_selector_identity_read_policy_preload_refusal(env,monkeypatch,case):
    b,w,ref,row,tickets=env;selector='exact-old'
    if case=='sha_alias':selector=row['sha256']
    elif case=='label_alias':selector='human-label'
    elif case=='tamper':Path(row['path']).write_bytes(b'tampered')
    elif case=='symlink':
        p=Path(row['path']);original=p.with_suffix('.real');p.rename(original);p.symlink_to(original)
    elif case in {'reserved','other_project','no_binding','bool_generation','binding_disagree'}:
        row=copy.deepcopy(row)
        if case=='reserved':row['checkpoint_id']='current'
        elif case=='other_project':row['project_id']='elsewhere'
        elif case=='no_binding':del row['source_binding']
        elif case=='bool_generation':row['source_binding']['model_ref']['generation']=True
        else:row['source_revision']=1
        b.store.persist_checkpoint(row['sha256'],row)
    elif case=='duplicate':monkeypatch.setattr(b.store,'list_metadata',lambda table:[row,{**row,'path':str(Path(row['path']).with_name('other.mph'))}])
    elif case=='dirty':b.service.ledger._state_for(model_ref_from_mapping(ref)).dirty=True
    elif case=='no_inspect':b.service.ledger.permissions={'compute','project_write'}
    else:monkeypatch.setattr(b,'_require_g2_isolation',lambda:(_ for _ in ()).throw(ExecutionContractError('PERMISSION_DENIED','owned-copy READ not allowed')))
    with pytest.raises(ExecutionContractError):invoke(env,selector,'current')
    assert not tickets and not any(c[0]=='load' for c in w.calls)

@pytest.mark.parametrize('case',['load','cleanup','pointer','source_snapshot','source_semantic','checkpoint_bytes','source_after_getter','persist','terminal_metadata'])
def test_uncertain_owned_lifecycle_preserves_cause_and_freezes_source(env,monkeypatch,case):
    b,w,ref,row,tickets=env
    if case in {'load','cleanup'}:w.failure=case
    elif case=='persist':monkeypatch.setattr(b,'persist',lambda:(_ for _ in ()).throw(RuntimeError('terminal-persist-original')))
    elif case=='terminal_metadata':monkeypatch.setattr(b.service,'_metadata',lambda ref:(_ for _ in ()).throw(RuntimeError('terminal-read-metadata-original')))
    else:
        load=w.load
        def changed(*a,**kw):
            value=load(*a,**kw)
            if case=='pointer':setattr(ops.pointer()[0],'_current_model',object())
            elif case=='source_snapshot':w.fingerprint='changed'
            elif case=='source_semantic':w.model_node.children['feature'].items['a'].comment='unexpected-source-write'
            elif case=='source_after_getter':w.model_node.faults['label']=RuntimeError('after-source-getter-original')
            else:Path(row['path']).write_bytes(b'changed')
            return value
        monkeypatch.setattr(w,'load',changed)
    out=invoke(env,'exact-old','current');assert not out['success'],out;assert out['data']['status']=='UNKNOWN';assert out['data']['comparison']['equal'] is None;assert out['error']['safe_retry'] is False
    state=b.service.ledger._state_for(model_ref_from_mapping(ref));assert state.dirty;assert not tickets
    if case=='cleanup':assert len(w.tags())==2 and Path(out['data']['owned_copies'][0]['path']).exists()
    else:assert all(c['removed'] for c in out['data']['owned_copies'])
    preserve_control(env,'unknown-'+case,out)

@pytest.mark.parametrize('case',['tag','ledger','file','replacement'])
def test_owned_collision_and_foreign_file_never_removed(env,monkeypatch,case):
    b,w,ref,row,tickets=env;monkeypatch.setattr(diff,'uuid4',lambda:SimpleNamespace(hex='fixed'));tag='mcp_diff_fixed';target=b.project_root/'g2_artifacts/diffs'/f'{tag}.mph'
    if case=='tag':w.models[tag]=PureNode('foreign')
    elif case=='ledger':b.service.ledger.bind_model(tag)
    elif case=='file':target.parent.mkdir();target.write_bytes(b'foreign')
    else:
        load=w.load
        def replaced(file,tag=None):
            value=load(file,tag=tag);p=Path(file);p.rename(p.with_suffix('.owned'));p.write_bytes(b'foreign');return value
        monkeypatch.setattr(w,'load',replaced)
    out=invoke(env,'exact-old','current');assert not out['success'];assert out['data']['comparison']['equal'] is None
    if case=='tag':assert w.models[tag].tag_value=='foreign'
    if case in {'file','replacement'}:assert target.read_bytes()==b'foreign'
    if case=='replacement':assert out['data']['status']=='UNKNOWN' and b.service.ledger._state_for(model_ref_from_mapping(ref)).dirty
    assert not tickets
    preserve_control(env,'collision-'+case,out)

@pytest.mark.parametrize('case',['null','error','bad_boolean','bad_range','matrix','empty_matrix','unknown_metadata','unit_missing','wrong_receiver','wrong_signature','product_state','ambiguous_return','resource_metadata'])
def test_typed_getter_gaps_and_partial_differences_never_equal(env,case):
    b,w,ref,row,tickets=env;old=w.historical;node=old.children['feature'].items['a'];node.comment='observed-difference'
    if case=='null':node.faults['getBoolean']=None
    elif case=='error':node.faults['getBoolean']=RuntimeError('getter-original')
    elif case=='bad_boolean':node.props['flag']=('Boolean',1,[])
    elif case=='bad_range':node.props['flag']=('Boolean',True,[3])
    elif case in {'matrix','empty_matrix','unit_missing'}:
        node.props={'flag':('DoubleMatrix',[[1.0,2.0]] if case=='matrix' else [] if case=='empty_matrix' else [[3.0]],[])}
    elif case=='unknown_metadata':node.props['flag']=('UnsupportedNewType',True,[])
    elif case=='wrong_receiver':old.children['param'].children['group']=PureNode('wrong')
    elif case in {'wrong_signature','ambiguous_return'}:
        node.specs[('getBoolean',(S,))]['returns']='int'
        if case=='ambiguous_return':node.specs[('getBoolean',(S,S))]=method(P+'PropFeature','getBoolean',(S,),'boolean')
    elif case=='product_state':old.values[('getUsedProducts',())]=['unadapted-product']
    else:old.values[('getFileResourceTags',())]=['resource']
    out=invoke(env,'exact-old','current');d=out['data'];assert d['comparison']['equal'] is None and not d['comparison']['complete'],out;assert d['errors'];assert d['changes'];assert not tickets
    if case in {'null','error'}:assert not any(c['change']=='removed' and c['field']=='property:flag' for c in d['changes'])
    if case=='matrix':assert any(c['field']=='property:flag' and c['before'].get('value',{}).get('shape')==[1,2] for c in d['changes'])

def test_workplane_group_real_path_native_order_and_accessor_receiver_failure(env):
    b,w,ref,row,tickets=env
    geom=PureNode('g1',('GeomSequence','GeomInfo','ModelEntity'));geom.add('getSDim','int',3,'GeomInfo');geom.add('lengthUnit',S,'mm','GeomSequence');geom.add('isAxisymmetric','boolean',False,'GeomSequence')
    wp=PureNode('wp1',('GeomFeature','PropFeature','ModelEntity'),properties={});inner=PureNode('inner',('GeomSequence','GeomInfo','ModelEntity'));inner.add('getSDim','int',2,'GeomInfo');inner.add('lengthUnit',S,'mm','GeomSequence');inner.add('isAxisymmetric','boolean',False,'GeomSequence');inner.child('feature',PureList([PureNode('r1')]),'ModelEntityList');wp.child('geom',inner,'GeomSequence');geom.child('feature',PureList([wp]),'ModelEntityList');w.model_node.child('geom',PureList([geom]),'ModelEntityList')
    proj=projection(w);assert not proj['complete'];assert any(e['field']=='metadata:build_status' for e in proj['errors']);f=fields(proj);nested=path(member('geom','g1'),member('feature','wp1'),accessor('geom'),member('feature','r1'));assert diff.path_key(nested) in f
    assert diff.path_key(path(accessor('param'),member('group','pg1'))) in f
    inner.roles=['ModelEntity'];bad=projection(w);assert not bad['complete'];assert any(e['field']=='receiver' for e in bad['errors'])

def test_object_selection_string_tags_dimension_array_and_domain_metadata_are_partial(env):
    b,w,ref,row,tickets=env
    sel=PureNode('s1',('ModelEntity','GeomObjectSelection','AbstractSelection'))
    for n,r,v in [('objects',SA,['a','b']),('dimension','int[]',[2,3]),('geom',S,'g1'),('entities','int[]',[1]),('named',S,'named1'),('isInheriting','boolean',False)]:sel.add(n,r,v,'GeomObjectSelection')
    sel.add('entities','int[]',[7,8],'GeomObjectSelection',(S,));w.model_node.child('selection',PureList([sel]),'ModelEntityList')
    p=projection(w);d=fields(p)[diff.path_key(path(member('selection','s1')))];assert d['selection:object_tags']==['a','b'];assert d['selection:object_entities:a']['value']['data']==[7,8];assert d['selection:dimension']['value']['shape']==[2]
    assert not p['complete'];assert any(e['field']=='selection:geometry_revision' for e in p['errors'])
    assert not any(name in {'build','run','evaluate','set','init'} for node in w.descriptors for name,args in node.calls)

@pytest.mark.parametrize('budget',[{'max_nodes':1},{'max_rpc':1},{'max_seconds':0}])
def test_engineering_budget_stops_getters_honest_truncation_not_equality(env,budget):
    out=invoke(env,scope={'budget':budget});assert out['data']['comparison']['equal'] is None;assert any(c['status']=='TRUNCATED' for c in out['data']['coverage']);assert not out['data']['evidence']['work_budget']['hard_native_cancellation']

def test_page_native_order_and_serialized_byte_engineering_cap(env,monkeypatch):
    b,w,ref,row,tickets=env;out=invoke(env,scope={'page_size':1});assert out['data']['comparison']['complete'];assert out['data']['evidence']['work_budget']['native_order_pages']>=8
    monkeypatch.setattr(diff.Budget,'SERIALIZED_BYTES_CAP',1);out=invoke(env);assert out['data']['comparison']['equal'] is None;assert any(c['status']=='TRUNCATED' for c in out['data']['coverage'])

@pytest.mark.parametrize('case',['good','schema','source_sha','version','scope','coverage','label_only','missing_node','typed_shape','complete','unknown'])
def test_retained_projection_exact_provenance_and_real_coverage_validation(env,case):
    b,w,ref,row,tickets=env;p=projection(w);assert p['complete'];p.update(source_sha256=row['sha256'],source_binding=row['source_binding'],source_version=diff.VERSION);row=copy.deepcopy(row);row['semantic_projection']=p
    if case=='schema':p['schema']='old'
    elif case=='source_sha':p['source_sha256']='0'*64
    elif case=='version':p['source_version']='6.3'
    elif case=='scope':p['normalized_scope']={'mode':'paths','paths':[ROOT],'properties':[]}
    elif case=='coverage':p['coverage'].pop()
    elif case=='label_only':p['nodes']=[{'path':ROOT,'fields':{'label':'fake'}}];p['coverage']=[{'path':ROOT,'field':'label','status':'VERIFIED'}];p['coverage_contract']=[{'path':ROOT,'field':'label'}]
    elif case=='missing_node':p['nodes']=[n for n in p['nodes'] if n['path']!=path(member('feature','a'))]
    elif case=='typed_shape':p['nodes'][0]['fields']['bad']={'kind':'int32','shape':[1],'data':[1,2]}
    elif case=='complete':p['complete']=False
    elif case=='unknown':p['unknown']=True
    b.store.persist_checkpoint(row['sha256'],row);out=invoke(env,'exact-old','current');assert not any(c[0]=='load' for c in w.calls)
    assert out['data']['comparison']['complete']==(case=='good'),out
    if case!='good':assert out['data']['comparison']['equal'] is None and any(e['code']=='PROJECTION_INCOMPATIBLE' for e in out['data']['errors'])
    preserve_control(env,'cache-'+case,out)

def test_publication_schema_alias_profiles_and_no_cached_hash_equal(env):
    entry=registry.BY_ID['checkpoint.diff'].as_dict();assert entry['implementation_status']=='SUPPORTED_UNVERIFIED';assert entry['input_schema']['additionalProperties'] is False;assert 'expected_revision' not in entry['input_schema']['properties'];assert entry['runtime_dispatch_contract']['permissions'].startswith('inspect;')
    assert registry.is_tool_published('checkpoint_diff',profile='domain') and registry.is_tool_published('checkpoint_diff',profile='expert')
    from comsol_mcp._control_daemon import CONTROL_READS
    assert 'checkpoint_diff' not in CONTROL_READS

@pytest.mark.parametrize('value,shape', [([], [0]),([4],[1]),([4,7],[2])])
def test_integer_property_shapes_preserved_and_units_not_invented(env,value,shape):
    b,w,ref,row,tickets=env;node=w.model_node.children['feature'].items['a'];node.props={'flag':('IntArray',value,[])}
    p=projection(w);prop=fields(p)[diff.path_key(path(member('feature','a')))]['property:flag'];assert prop['value']['shape']==shape and prop['value']['data']==value
    assert prop['unit'] is None and not p['complete'];assert any(e['field']=='property:flag.unit' for e in p['errors'])

@pytest.mark.parametrize('value',[[2**31],[True],[[1],[2,3]]])
def test_integer_range_boolean_and_ragged_shape_refuse_coverage(env,value):
    b,w,ref,row,tickets=env;w.model_node.children['feature'].items['a'].props={'flag':('IntArray',value,[])}
    p=projection(w);assert not p['complete'];assert any(e['code']=='PROPERTY_TYPE_MISMATCH' for e in p['errors'])

def test_domain_metadata_real_pure_getters_links_solution_info_no_fake_path(env):
    b,w,ref,row,tickets=env
    mesh=PureNode('mesh1',('MeshSequence','ModelEntity'));study=PureNode('std1',('Study','ModelEntity'));sol=PureNode('sol1',('SolverSequence','ModelEntity'));info=PureNode('info',('SolutionInfo',))
    values={'geom':'g1','getSDim':3,'getNumElem':4,'getNumVertex':7,'current':'size1','getTypes':['tet'],'problems':[], 'study':'std1','getType':'StudyStep','getSequenceType':'Solution','getDefaultSolnum':1,'getPNames':['p'],'getParamNames':['p'],'getParamVals':[2.0],'getLastComputationDate':123,'getLastComputationTime':9,'getLastComputationVersion':diff.VERSION}
    for node,role in ((mesh,'MeshSequence'),(study,'Study'),(sol,'SolverSequence')):
        for name,returns in diff.semantic_metadata()[role]:
            result=next(iter(returns));value=values.get(name,True if result=='boolean' else [] if result.endswith('[]') else '' if result==S else 1)
            node.add(name,result,value,role)
    study.add('getSolverSequences',SA,['sol1'],'Study',(S,))
    for name,returns,val in [('getOuterSolnum','int[]',[1,2]),('getLevelNames',SA,['p']),('getPNamesOuter',SA,['p']),('getPUnitsOuter',SA,['s'])]:info.add(name,returns,val,'SolutionInfo')
    sol.child('getSolutioninfo',info,'SolutionInfo')
    w.model_node.child('mesh',PureList([mesh]),'ModelEntityList');w.model_node.children['study']=PureList([study]);w.model_node.children['sol']=PureList([sol]);p=projection(w);f=fields(p)
    assert f[diff.path_key(path(member('mesh','mesh1')))]['metadata:geom']['value']['data']=='g1'
    assert f[diff.path_key(path(member('study','std1')))]['metadata:getSolverSequences']['value']['data']==['sol1']
    sf=f[diff.path_key(path(member('sol','sol1')))];assert sf['metadata:study']['value']['data']=='std1';assert sf['solution_info:getPUnitsOuter']['value']['data']==['s']
    assert not any(s.get('accessor')=='sol' for n in p['nodes'] for s in n['path']['segments']);assert not p['complete'];assert any(e['field']=='solution_info:parameter_units' for e in p['errors'])
    assert not any(name in {'build','run','evaluate','set','xmeshInfo','clearXmesh','getStrictFieldReadback'} for n in w.descriptors for name,args in n.calls)

def test_missing_full_model_domain_and_unknown_public_accessor_are_not_empty(env):
    b,w,ref,row,tickets=env;del w.model_node.specs[('param',())];w.model_node.add('unsafeDomain',P+'ModelEntity',None,'Model');w.model_node.faults['unsafeDomain']=RuntimeError('must-never-dispatch')
    p=projection(w);assert not p['complete'];assert any(e['field']=='collection:param' for e in p['errors']);assert any(e['field']=='unprojected_accessor:unsafeDomain' for e in p['errors']);assert not any(n=='unsafeDomain' for n,args in w.model_node.calls)

def test_current_actual_receiver_runtime_mismatch_and_read_signature_failure(env):
    b,w,ref,row,tickets=env;w.override={'runtime_version':'6.3.0.290','interfaces':[P+'Model'],'methods':[]};out=invoke(env);assert out['data']['comparison']['equal'] is None;assert not tickets

def test_model_saved_version_is_semantic_metadata_not_runtime_admission(env):
    b,w,ref,row,tickets=env;w.historical.values[('getComsolVersion',())]='6.3.0.290';out=invoke(env,'exact-old','current');assert out['data']['comparison']['status']=='DIFFERENT';assert any(c['field']=='metadata:getComsolVersion' for c in out['data']['changes']);assert not tickets

def test_historical_lineage_revision_independent_of_current_admission_and_identical_duplicate(env,monkeypatch):
    b,w,ref,row,tickets=env;state=b.service.ledger._state_for(model_ref_from_mapping(ref));state.revision=7;b.persist();monkeypatch.setattr(b.store,'list_metadata',lambda table:[row,copy.deepcopy(row)])
    out=invoke(env,'exact-old','current');assert out['success'];assert out['data']['left_identity']['source_binding']['revision']==0 and out['data']['right_identity']['revision']==7;assert out['execution']['revision']==7 and not tickets

@pytest.mark.parametrize('field,value',[('expected_revision',0),('session_id','other'),('project_id','other'),('model_ref',{}),('load_policy','write')])
def test_body_and_outer_identity_cannot_bypass_closed_read_envelope(env,field,value):
    b,w,ref,row,tickets=env;body={'left':'current','right':'current',field:value}
    with pytest.raises(ExecutionContractError):b.invoke('checkpoint.diff',body,envelope(ref),'invalid',None)
    assert not w.descriptors and not tickets

def test_source_unknown_blocks_next_new_key_write_before_receiver_dispatch(env):
    from test_unit_a_node_public_api import request
    b,w,ref,row,tickets=env;w.failure='cleanup';out=invoke(env,'exact-old','current');assert not out['success'];before=len(w.descriptors)
    with pytest.raises(ExecutionContractError):b.invoke('api.invoke',request('must-not-write'),{**envelope(ref),'expected_revision':0,'idempotency_key':'fresh-key'},'after-unknown',None)
    assert len(w.descriptors)==before and not tickets

@pytest.mark.parametrize('allowed,value,complete',[(['on','off'],True,True),(['on'],False,False),(['only'],True,False)])
def test_actual_property_range_checked_using_existing_typed_vocabulary(env,allowed,value,complete):
    b,w,ref,row,tickets=env;w.model_node.children['feature'].items['a'].props={'flag':('Boolean',value,allowed)};p=projection(w);assert p['complete']==complete
    if not complete:assert any(e['code']=='INVALID_PROPERTY_VALUE' for e in p['errors'])

def test_variables_original_expression_not_numeric_evaluation_and_missing_unit_explicit(env):
    b,w,ref,row,tickets=env;v=PureNode('v1',('ModelEntity','ExpressionBase'),expressions={'velocity':('length/t','original formula',None)});w.model_node.child('variable',PureList([v]),'ModelEntityList');p=projection(w);vf=fields(p)[diff.path_key(path(member('variable','v1')))];assert vf['expression:velocity']['value']['data']=='length/t';assert any(e['field']=='expression:velocity.unit' for e in p['errors']);assert not p['complete'];assert not any(n=='evaluate' for n,args in v.calls)

def test_non_prop_public_gettype_is_observed_not_replaced_by_interface_guess(env):
    b,w,ref,row,tickets=env;node=PureNode('b',('ModelEntity','ModelNode'));node.add('getType',S,'Component','ModelNode');w.model_node.children['feature'].items['b']=node;w.model_node.children['feature'].order.append('b');p=projection(w);nf=fields(p)[diff.path_key(path(member('feature','b')))];assert nf['type']['value']['data']=='Component';assert nf['public_interfaces']==[P+'ModelEntity',P+'ModelNode']

def test_source_changes_at_actual_service_admission_refuse_before_copy_or_ticket(env):
    b,w,ref,row,tickets=env;w.fingerprint='external-before-dispatch'
    with pytest.raises(ExecutionContractError):invoke(env,'exact-old','current')
    assert b.service.ledger._state_for(model_ref_from_mapping(ref)).dirty;assert not tickets and not w.descriptors and not any(c[0]=='load' for c in w.calls)

@pytest.mark.parametrize('route',['checkpoint.diff','checkpoint_diff','registry_call','operation_call'])
def test_stateful_daemon_queue_alias_replay_project_read_and_conflict(tmp_path,monkeypatch,route):
    root=tmp_path/'projects';root.mkdir();daemon=ControlDaemon(tmp_path/'control',project_root=root,registry={})
    try:
        project=daemon.dispatch({'operation':'project.create','arguments':{'label':'semantic diff','workspace':'work','policy':{'permissions':['inspect']}},'execution':{'request_id':'create','idempotency_key':'create'}})['data']['project']
        w=PureWorker();b=daemon.backend;b.worker=w;b.worker_identity={'runtime_id':'synthetic','worker_instance_id':'synthetic','connection_epoch':1};b.service=ExecutionService(SessionLedger('session','server'),w,project_root=root)
        ref=b.service.bind_model('m')['execution']['model_ref'];b._bind_model_project(ref,project['project_id']);b.persist()
        e={**envelope(ref),'project_id':project['project_id'],'rpc_timeout_s':5};body={'left':'current','right':'current'};args={'operation_id':'checkpoint.diff','arguments':body} if route in {'registry_call','operation_call'} else body
        first=daemon.dispatch({'operation':route,'arguments':args,'execution':e});assert first['success'],first
        count=len(w.descriptors);again=daemon.dispatch({'operation':'checkpoint_diff','arguments':body,'execution':e});assert again['success'];assert first['execution']['operation_id']==again['execution']['operation_id'];assert len(w.descriptors)==count
        changed=daemon.dispatch({'operation':'checkpoint.diff','arguments':{**body,'scope':{'paths':[ROOT]}},'execution':e});assert not changed['success'] and changed['error']['code']=='IDEMPOTENCY_CONFLICT';assert len(w.descriptors)==count
        bad=daemon.dispatch({'operation':'registry_call','arguments':{'operation_id':'checkpoint.diff','arguments':{**body,'project_id':'other'}},'execution':{**e,'idempotency_key':'bad'}});assert not bad['success'];assert len(w.descriptors)==count
    finally:daemon.close()

# Cache applicability repair: these exercise real public production dispatch,
# with retained actual Reader fixtures only; they are not native API evidence.
def retained_row(env,scope=None):
    b,w,ref,row,tickets=env;p=projection(w,scope)
    p.update(source_sha256=row['sha256'],source_binding=row['source_binding'],source_version=diff.VERSION)
    return {**copy.deepcopy(row),'semantic_projection':p}

def publish_retained(env,row,scope=None):
    b,w,ref,original,tickets=env;b.store.persist_checkpoint(row['sha256'],row)
    out=invoke(env,'exact-old','current',scope=scope)
    assert not any(c[0]=='load' for c in w.calls) and not tickets
    return out

def forged_complete(p,status='NOT_APPLICABLE'):
    for c in p['coverage']:
        if c['status'] not in {'VERIFIED','NOT_APPLICABLE'}:
            c.update(status=status,reason='caller/cache says this gap is complete',getter=None)
    p.update(complete=True,unknown=False,errors=[])
    p['coverage_contract']=[{'path':c['path'],'field':c['field']} for c in p['coverage']]

@pytest.mark.parametrize('gap',['selection','local_selection','object_selection','geom_build','mesh_build','product','numeric_unit','expression_unit','solution'])
def test_retained_applicable_gap_forged_na_never_complete(env,gap):
    b,w,ref,row,tickets=env
    if gap in {'selection','local_selection','object_selection'}:
        role={'selection':'Selection','local_selection':'LocalSelection','object_selection':'GeomObjectSelection'}[gap]
        sel=PureNode('sel1',('ModelEntity',role,'AbstractSelection'))
        for name,ret,value in [('named',S,'sourceSelection'),('isInheriting','boolean',False),('entities','int[]',[2]),('dimension','int[]',[3]),('geom',S,'geom1')]:
            sel.add(name,ret,value,role)
        if gap=='object_selection':
            sel.add('objects',SA,['obj1'],role);sel.add('entities','int[]',[2],role,(S,))
        w.model_node.child('selection',PureList([sel]),'ModelEntityList')
    elif gap in {'geom_build','mesh_build'}:
        n=w.model_node.children['feature'].items['a'];n.roles.append('GeomFeature' if gap=='geom_build' else 'MeshFeature')
        if gap=='geom_build':n.add('objectNames',SA,[],'GeomFeature')
    elif gap=='product':w.model_node.values[('getUsedProducts',())]=['HeatTransfer']
    elif gap=='numeric_unit':w.model_node.children['feature'].items['a'].props={'number':('IntArray',[1],[])}
    elif gap=='expression_unit':
        var=PureNode('v',('ModelEntity','ExpressionBase'),expressions={'temperature':('300[K]','raw','K')})
        w.model_node.child('variable',PureList([var]),'ModelEntityList')
    elif gap=='solution':
        sol=PureNode('s1',('ModelEntity','SolverSequence'))
        values={'java.lang.String':'study1','java.lang.String[]':[],'boolean':False,'int':0,'double[]':[]}
        for name,returns in diff.semantic_metadata()['SolverSequence']:
            ret=next(iter(returns));sol.add(name,ret,values[ret],'SolverSequence')
        w.model_node.child('sol',PureList([sol]),'ModelEntityList')
    cached=retained_row(env);p=cached['semantic_projection'];assert not p['complete']
    original_gaps=[c for c in p['coverage'] if c['status'] not in {'VERIFIED','NOT_APPLICABLE'}];assert original_gaps
    forged_complete(p);out=publish_retained(env,cached)
    assert out['data']['comparison']=={'status':'INCOMPLETE','equal':None,'complete':False}
    assert any(e['code']=='PROJECTION_INCOMPATIBLE' and 'obligations incomplete' in e['message'] for e in out['data']['errors'])
    preserve_control(env,'repair-applicable-'+gap,out)

@pytest.mark.parametrize('field',['label','properties','collection:param','expression:length.unit'])
def test_retained_missing_required_row_self_contract_cannot_authorize(env,field):
    cached=retained_row(env);p=cached['semantic_projection'];assert p['complete']
    rows=[c for c in p['coverage'] if c['field']==field];assert rows
    p['coverage'].remove(rows[0]);p['coverage_contract']=[{'path':c['path'],'field':c['field']} for c in p['coverage']]
    out=publish_retained(env,cached)
    assert out['data']['comparison']['equal'] is None
    assert any(e['code']=='PROJECTION_INCOMPATIBLE' for e in out['data']['errors'])

@pytest.mark.parametrize('contradictory',[False,True])
def test_retained_duplicate_coverage_keys_never_complete(env,contradictory):
    cached=retained_row(env);p=cached['semantic_projection'];row=copy.deepcopy(next(c for c in p['coverage'] if c['field']=='label' and c['status']=='VERIFIED'))
    if contradictory:row.update(status='NOT_APPLICABLE',getter=None)
    p['coverage'].append(row);p['coverage_contract']=[{'path':c['path'],'field':c['field']} for c in p['coverage']]
    out=publish_retained(env,cached)
    assert out['data']['comparison']['equal'] is None
    assert any('duplicate historical coverage key' in e['message'] for e in out['data']['errors'])

@pytest.mark.parametrize('case',['public_signature','typed_payload'])
def test_retained_verified_status_must_match_getter_and_payload(env,case):
    cached=retained_row(env);p=cached['semantic_projection']
    if case=='public_signature':
        c=next(c for c in p['coverage'] if c['field']=='label' and c['status']=='VERIFIED');c['getter']['parameters']=[S]
    else:
        n=next(n for n in p['nodes'] if 'property:flag' in n['fields']);n['fields']['property:flag']['value']={'kind':'string','shape':[],'data':'true','java_signature':'boolean'}
    out=publish_retained(env,cached);assert out['data']['comparison']['equal'] is None
    assert any(e['code']=='PROJECTION_INCOMPATIBLE' for e in out['data']['errors'])

def test_retained_genuine_absent_interface_and_boolean_unit_na_accepted(env):
    cached=retained_row(env);p=cached['semantic_projection'];assert p['complete']
    rows=[c for c in p['coverage'] if c['status']=='NOT_APPLICABLE'];assert rows
    assert any(c['field']=='property:flag.unit' for c in rows)
    for c in rows:c['reason']='different text does not prove or disprove applicability'
    out=publish_retained(env,cached)
    assert out['data']['comparison']=={'status':'EQUAL','equal':True,'complete':True}
    preserve_control(env,'repair-genuine-na',out)

def test_retained_properties_only_scope_does_not_add_selection_geometry_gate(env):
    b,w,ref,row,tickets=env
    n=w.model_node.children['feature'].items['a'];n.roles.append('Selection')
    scope={'properties':[{'path':path(member('feature','a')),'names':['flag']}]}
    cached=retained_row(env,scope);assert cached['semantic_projection']['complete']
    assert not any(c['field']=='selection:geometry_revision' for c in cached['semantic_projection']['coverage'])
    out=publish_retained(env,cached,scope)
    assert out['data']['comparison']=={'status':'EQUAL','equal':True,'complete':True}
    preserve_control(env,'repair-properties-only',out)

@pytest.mark.parametrize('gap',[False,True])
def test_retained_subtree_coverage_exact_na_applicability(env,gap):
    b,w,ref,row,tickets=env
    if gap:
        n=w.model_node.children['feature'].items['a'];n.roles.extend(['Selection','AbstractSelection'])
        for name,ret,value in [('named',S,'sel1'),('isInheriting','boolean',False),('entities','int[]',[4]),('dimension','int[]',[2]),('geom',S,'geom1')]:
            n.add(name,ret,value,'Selection')
    scope={'paths':[path(member('feature','a'))]}
    cached=retained_row(env,scope)
    if gap:
        assert not cached['semantic_projection']['complete'];forged_complete(cached['semantic_projection'])
    else:assert cached['semantic_projection']['complete']
    out=publish_retained(env,cached,scope)
    assert out['data']['comparison']==({'status':'INCOMPLETE','equal':None,'complete':False} if gap else {'status':'EQUAL','equal':True,'complete':True})
    if gap:assert any(e['code']=='PROJECTION_INCOMPATIBLE' and 'selection:geometry_revision' in e['message'] for e in out['data']['errors'])
    preserve_control(env,'repair-subtree-'+str(gap),out)

def test_retained_solution_info_axis_unit_gap_never_na(env):
    b,w,ref,row,tickets=env;sol=PureNode('s1',('ModelEntity','SolverSequence'))
    values={'java.lang.String':'study1','java.lang.String[]':[],'boolean':False,'int':0,'double[]':[]}
    for name,returns in diff.semantic_metadata()['SolverSequence']:
        ret=next(iter(returns));sol.add(name,ret,values[ret],'SolverSequence')
    info=PureNode('info',('SolutionInfo',))
    for name,ret,value in [('getOuterSolnum','int[]',[1]),('getLevelNames',SA,['parameter']),('getPNamesOuter',SA,['p']),('getPUnitsOuter',SA,['s'])]:
        info.add(name,ret,value,'SolutionInfo')
    sol.children['getSolutioninfo']=info;sol.add('getSolutioninfo',P+'SolutionInfo',None,'SolverSequence')
    w.model_node.child('sol',PureList([sol]),'ModelEntityList')
    cached=retained_row(env);p=cached['semantic_projection']
    assert any(c['field']=='solution_info:parameter_units' and c['status']=='UNSUPPORTED' for c in p['coverage'])
    forged_complete(p);out=publish_retained(env,cached)
    assert out['data']['comparison']=={'status':'INCOMPLETE','equal':None,'complete':False}
    assert any(e['code']=='PROJECTION_INCOMPATIBLE' and 'solution_info:parameter_units' in e['message'] for e in out['data']['errors'])
    preserve_control(env,'repair-solution-axis',out)
