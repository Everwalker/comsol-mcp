"""Unit A isolated software contracts. Doubles are not COMSOL native evidence."""
from contextlib import nullcontext
from types import SimpleNamespace
import copy
import pytest
from comsol_mcp._execution_contract import ExecutionContractError, SessionLedger, model_ref_from_mapping
from comsol_mcp._execution_service import ExecutionService
from comsol_mcp._managed_backend import ManagedBackend
from comsol_mcp._operation_store import OperationStore
from comsol_mcp._control_daemon import ControlDaemon
from comsol_mcp import _g2_public_api as api
from comsol_mcp._g2_engine import validate_node_action, prepare_node_action, execute_node_action
from comsol_mcp import _g2_registry as registry

ROOT = {"segments": []}
def path(tag): return {"segments": [{"collection": "feature", "tag": tag}]}
def tv(value,kind='string',shape=None,signature=None):
    row={'kind':kind,'shape':[] if shape is None else shape,'data':value}
    if signature is not None:row['java_signature']=signature
    return row
def method(interface,name,parameters=(),returns='void'):
    return {'interface':interface,'method':name,'parameters':list(parameters),'returns':returns}

class Selection:
    def __init__(self):self.ids=[];self.name='';self.dimension=2;self.geometry='g';self.inheriting=False;self.calls=[]
    def named(self,*args):
        if args:self.calls.append(('named',args));self.name=args[0];return self
        return self.name
    def entities(self):return list(self.ids)
    def dim(self):return self.dimension
    def geom(self,*args):
        if args:self.calls.append(('geom',args));self.geometry=args[0] if isinstance(args[0],str) else self.geometry;self.dimension=args[-1];return self
        return self.geometry
    def set(self,ids):self.calls.append(('set',ids));self.ids=list(ids)
    def all(self):self.calls.append(('all',));self.ids=[1,2]
    def inherit(self,value):self.calls.append(('inherit',value));self.inheriting=value
    def isInheriting(self):return self.inheriting

class Entity:
    def __init__(self,tag='a',type_id='Rectangle'):
        self.tag_value=tag;self.type_id=type_id;self.comment='';self.display='label-'+tag;self.flag=True;self.calls=[]
        self.values={'flag':True,'empty':[],'matrix':[[1.0]],'expr':'a+b'};self.sel=Selection()
    def feature(self,tag=None):return self.list if tag is None else self.list.items[tag]
    def getType(self):return self.type_id
    def tag(self):return self.tag_value
    def resolveModelPath(self):return 'm/'+self.tag_value
    def comments(self,*args):
        if args:self.calls.append(('comments',args));self.comment=args[0]['data'] if isinstance(args[0],dict) else args[0];return self
        return self.comment
    def label(self,*args):
        if args:self.calls.append(('label',args));self.display=args[0];return self
        return self.display
    def active(self,value):self.calls.append(('active',value));self.flag=value;return self
    def isActive(self):return self.flag
    def selection(self,*args):return self.sel
    def properties(self):return list(self.values)
    def getValueType(self,name):return {'flag':'Boolean','empty':'DoubleArray','matrix':'DoubleMatrix','expr':'String'}.get(name,'FutureType')
    def getAllowedPropertyValues(self,name):return None
    def getBoolean(self,name):return self.values[name]
    def getDoubleArray(self,name):return self.values[name]
    def getDoubleMatrix(self,name):return self.values[name]
    def getString(self,name):return self.values[name]
    def set(self,name,value):self.calls.append(('set',name,value));self.values[name]=value['data']

class Collection:
    def __init__(self):self.items={tag:Entity(tag) for tag in ['b','a','c']};self.order=['b','a','c'];self.calls=[]
    def tags(self):return list(self.order)
    def create(self,tag,type_id):self.calls.append(('create',tag,type_id));self.items[tag]=Entity(tag,type_id);self.order.append(tag);return self.items[tag]
    def copy(self,tag,source):self.calls.append(('copy',tag,source));e=copy.deepcopy(self.items[source]);e.tag_value=tag;self.items[tag]=e;self.order.append(tag);return e
    def move(self,tag,index):self.calls.append(('move',tag,index));self.order.remove(tag);self.order.insert(index['data'],tag)
    def remove(self,tag):self.calls.append(('remove',tag));del self.items[tag];self.order.remove(tag)

class Worker:
    def __init__(self):self.model_node=Entity('m');self.model_node.list=Collection();self.descriptors=[];self.override=None
    def client(self):return self
    def model(self,tag):assert tag=='m';return self.model_node
    def operation_context(self,*a,**kw):return nullcontext()
    def describe_public(self,node):
        self.descriptors.append(node)
        if self.override is not None:return copy.deepcopy(self.override)
        if isinstance(node,Collection):
            owners=['com.comsol.model.ModelEntityList','com.comsol.model.PropFeatureList','com.comsol.model.IListMove']
            rows=[method(owners[0],'copy',('java.lang.String','java.lang.String')),method(owners[0],'remove',('java.lang.String',)),method(owners[1],'create',('java.lang.String','java.lang.String')),method(owners[2],'move',('java.lang.String','int'))]
        elif isinstance(node,Selection):
            owners=['com.comsol.model.Selection','com.comsol.model.LocalSelection','com.comsol.model.AbstractSelection']
            rows=[method(owners[0],'geom',('int',)),method(owners[0],'geom',('java.lang.String','int')),method(owners[0],'set',('[I',)),method(owners[0],'all'),method(owners[0],'inherit',('boolean',)),method(owners[0],'isInheriting'),method(owners[1],'named',('java.lang.String',))]
        else:
            owners=[api.MODEL_ENTITY,'com.comsol.model.PropFeature']
            rows=[method(api.MODEL_ENTITY,'comments',(),'java.lang.String'),method(api.MODEL_ENTITY,'comments',('java.lang.String',),api.MODEL_ENTITY),method(api.MODEL_ENTITY,'label',(),'java.lang.String'),method(api.MODEL_ENTITY,'label',('java.lang.String',),api.MODEL_ENTITY),method(api.MODEL_ENTITY,'active',('boolean',),api.MODEL_ENTITY),method(api.MODEL_ENTITY,'isActive',(),'boolean'),method(owners[1],'selection'),method(owners[1],'selection',('java.lang.String',))]
        return {'runtime_version':api.VERSION,'interfaces':owners,'methods':rows}
    def model_snapshot(self,tag):return {'model_tag':tag,'server_instance_id':'server','fingerprint':'software-fingerprint','external_event_counter':0}

@pytest.fixture
def backend(tmp_path,monkeypatch):
    worker=Worker();store=OperationStore(tmp_path/'store.sqlite');ledger=SessionLedger('session','server');service=ExecutionService(ledger,worker,project_root=tmp_path)
    ref=service.bind_model('m')['execution']['model_ref'];b=ManagedBackend(tmp_path,store,service=service,worker=worker,registry={});b.project_root=tmp_path
    monkeypatch.setattr(b,'_require_g2_isolation',lambda:{'scope':'MOCK_SOFTWARE_ONLY'})
    tickets=[];begin=SessionLedger.begin_write
    monkeypatch.setattr(SessionLedger,'begin_write',lambda self,*a,**kw:(tickets.append((a,kw)),begin(self,*a,**kw))[1])
    yield b,worker,ref,tickets
    b.docs_index.close();store.close()
def execution(ref,revision=0):return {'project_id':'p','session_id':'session','model_ref':ref,'expected_revision':revision,'idempotency_key':'key','request_id':'request'}
def request(value=None,declared=None):return {'path':path('a'),'method':'comments','arguments':[] if value is None else [tv(value)],'declared_effect':declared or ('READ' if value is None else 'WRITE')}

@pytest.mark.parametrize('route',['api.invoke','api_invoke','registry_call','operation_call'])
def test_api_comments_actual_receiver_read_write_permissions_before_ticket(backend,route):
    b,w,ref,tickets=backend;e=execution(ref);args=request()
    if route in {'registry_call','operation_call'}:args={'operation_id':'api.invoke','arguments':args}
    read=b.invoke(route,args,e,'read',None);assert read['success'];assert read['data']['typed_return']['value']==tv('');assert not tickets
    args=request('new')
    if route in {'registry_call','operation_call'}:args={'operation_id':'api.invoke','arguments':args}
    write=b.invoke(route,args,e,'write',None);assert write['success'];assert len(tickets)==1;assert write['data']['typed_return']['model_ref']==ref;assert write['execution']['revision']==1
    assert write['data']['resolved_effect']=='WRITE';assert w.model_node.list.items['a'].comment=='new'

@pytest.mark.parametrize('case',['declared_read','unknown_method','wrong_signature','wrong_receiver','wrong_version','missing_ref','body_ref_mismatch','stale_revision','permission'])
def test_api_invalid_requests_fail_before_ticket_or_setter(backend,case):
    b,w,ref,tickets=backend;e=execution(ref);args=request('new')
    if case=='declared_read':args['declared_effect']='READ'
    elif case=='unknown_method':args['method']='getClass'
    elif case=='wrong_signature':args['java_signature']=['int']
    elif case in {'wrong_receiver','wrong_version'}:
        d=w.describe_public(w.model_node.list.items['a']);d['interfaces']=[] if case=='wrong_receiver' else d['interfaces'];d['runtime_version']='6.3' if case=='wrong_version' else d['runtime_version'];w.override=d
    elif case=='missing_ref':del e['model_ref']
    elif case=='body_ref_mismatch':args['model_ref']={**ref,'model_tag':'other'}
    elif case=='stale_revision':e['expected_revision']=2
    else:b.service.ledger.permissions={'inspect'}
    with pytest.raises(ExecutionContractError):b.invoke('api.invoke',args,e,'reject',None)
    assert tickets==[];assert w.model_node.list.items['a'].calls==[]

def test_describe_only_publishes_reviewed_exact_overloads(backend):
    b,w,ref,tickets=backend
    out=b.invoke('api_describe',{'path':path('a')},execution(ref),'describe',None)
    assert out['success'];methods=out['data']['methods'];assert [row['effect'] for row in methods]==['READ','WRITE'];assert all(row['method']=='comments' for row in methods);assert tickets==[]
    with pytest.raises(ExecutionContractError):b.invoke('api.describe',{'path':path('a'),'method':'getClass'},execution(ref),'no',None)

@pytest.mark.parametrize('route',['registry_call','operation_call','api_invoke','api.invoke'])
def test_project_read_acl_uses_provisional_signature_not_declared_effect(route):
    daemon=object.__new__(ControlDaemon);calls=[]
    daemon.backend=SimpleNamespace(model_project_binding=lambda ref:{'attribution':'PROJECT_BOUND','project_id':'p'})
    def authorize(project,permission):
        calls.append((project,permission))
        if permission!='inspect':raise ExecutionContractError('PERMISSION_DENIED','project READ only')
    daemon.project_authority=SimpleNamespace(authorize_operation=authorize)
    e={'project_id':'p','model_ref':{'model_tag':'m'}}
    def wrapped(body):return {'operation_id':'api.invoke','arguments':body} if route in {'registry_call','operation_call'} else body
    daemon._authorize_project_execution(route,wrapped(request()),e);assert calls[-1]==('p','inspect')
    with pytest.raises(ExecutionContractError):daemon._authorize_project_execution(route,wrapped(request('new','READ')),e)
    assert calls[-1]==('p','project_write')

@pytest.mark.parametrize('case',['extra_outer','wrong_inner','unknown','nonobject','nested_identity','foreign_project','ambiguous'])
def test_project_api_route_ambiguity_and_identity_fail_closed(case,monkeypatch):
    daemon=object.__new__(ControlDaemon);calls=[];daemon.backend=SimpleNamespace(model_project_binding=lambda ref:{'attribution':'PROJECT_BOUND','project_id':'p'});daemon.project_authority=SimpleNamespace(authorize_operation=lambda *a:calls.append(a))
    body=request('new');args={'operation_id':'api.invoke','arguments':body};e={'project_id':'p','model_ref':{'model_tag':'m'}}
    if case=='extra_outer':args['method']='comments'
    elif case=='wrong_inner':args['operation_id']='api_invoke'
    elif case=='unknown':body['method']='getClass'
    elif case=='nonobject':args['arguments']=[]
    elif case=='nested_identity':body['model_ref']={}
    elif case=='foreign_project':e['project_id']='other'
    else:monkeypatch.setattr(api,'CAPABILITIES',api.CAPABILITIES+(api.Capability(api.MODEL_ENTITY,'comments',('java.lang.String',),api.MODEL_ENTITY,'READ'),))
    # A route alias in operation_id is not a canonical fallback request.
    with pytest.raises(ExecutionContractError):daemon._authorize_project_execution('registry_call',args,e)
    assert calls==[]

@pytest.mark.parametrize('body',[
 {'parent':ROOT,'collection':'feature','tag':'new','type_id':'Rectangle','properties':[{'name':'x','value':tv(2147483648,'int32')}]},
 {'parent':ROOT,'collection':'feature','tag':'new','type_id':'Rectangle','properties':[{'name':'x','value':tv([1.0],'float64',[2])}]},
 {'parent':ROOT,'collection':'feature','tag':'new','type_id':'Rectangle','properties':[{'name':'x','value':tv(1,'int64')}]},
 {'parent':ROOT,'collection':'bad','tag':'new','type_id':'Rectangle'},
 {'parent':ROOT,'collection':'feature','tag':'new/path','type_id':'Rectangle'},
])
def test_create_preflight_rejects_structure_typed_range_shape_before_create(body):
    w=Worker()
    with pytest.raises(ExecutionContractError):prepare_node_action(w,'m','node.create',body)
    assert w.model_node.list.calls==[]

def test_create_defaults_and_actual_typed_property_readback(backend):
    b,w,ref,tickets=backend
    body={'parent':ROOT,'collection':'feature','tag':'new','type_id':'Rectangle','properties':[{'name':'matrix','value':tv([[2.0,3.0]],'float64',[1,2])},{'name':'flag','value':tv(False,'boolean')}]}
    result=b.invoke('node_create',body,execution(ref),'create',None)
    assert result['success'];assert result['data']['created'];assert result['data']['created_path']==path('new');assert len(tickets)==1;assert w.model_node.list.items['new'].values['matrix']==[[2.0,3.0]]
    assert validate_node_action('node.create',{'parent':ROOT,'collection':'feature','tag':'fresh','type_id':'Rectangle'})['properties']==[]

def test_created_node_property_metadata_failure_preserves_partial_unknown(backend):
    b,w,ref,tickets=backend;body={'parent':ROOT,'collection':'feature','tag':'new','type_id':'Rectangle','properties':[{'name':'runtime-unknown','value':tv('x')}]}
    result=b.invoke('node.create',body,execution(ref),'create',None)
    assert not result['success'];assert result['data']['created'] is True;assert 'new' in w.model_node.list.items;assert result['data']['status']=='UNKNOWN';assert result['error']['safe_retry'] is False;assert result['execution']['dirty'] is True

@pytest.mark.parametrize('operation,body',[('node.create',{'parent':ROOT,'collection':'feature','tag':'a','type_id':'Rectangle'}),('node.copy',{'source':path('a'),'target_parent':ROOT,'tag':'b'}),('node.remove',{'path':path('a')}),('node.move',{'path':path('a'),'before':path('a')}),('node.move',{'path':path('a'),'before':path('b'),'after':path('c')}),('node.active_set',{'path':path('a'),'active':1})])
def test_collision_dependency_and_order_controls_refuse_before_ticket(backend,operation,body):
    b,w,ref,tickets=backend
    with pytest.raises(ExecutionContractError):b.invoke(operation,body,execution(ref),'reject',None)
    assert tickets==[];assert w.model_node.list.calls==[];assert w.model_node.list.items['a'].calls==[]

@pytest.mark.parametrize('operation,body,expected',[('node.label_set',{'path':path('a'),'label':'new label'},'new label'),('node.active_set',{'path':path('a'),'active':False},False),('node.move',{'path':path('a'),'after':path('c')},['b','c','a'])])
def test_node_setters_and_move_verify_actual_readback(backend,operation,body,expected):
    b,w,ref,tickets=backend;out=b.invoke(operation,body,execution(ref),'write',None);assert out['success'];assert len(tickets)==1
    assert out['data']['order_after' if operation=='node.move' else 'after']==expected
    assert w.model_node.list.items['a'].tag_value=='a'

def test_copy_keeps_same_model_scope_and_reports_dependency_incomplete(backend,monkeypatch):
    from test_unit_d_same_list_copy import install_a_copy_fixture,arguments
    b,w,ref,tickets=install_a_copy_fixture(backend,monkeypatch);out=b.invoke('node.copy',arguments(),execution(ref),'copy',None)
    assert out['data']['complete'] is False;assert out['data']['dependency_check']['complete'] is False;assert out['data']['source_identity']['model_ref']==ref;assert out['data']['readback']['target']['type_id']=='Rectangle';assert w.collection.calls==[('copy','copy','a')]

@pytest.mark.parametrize('selection',[{'kind':'explicit','entities':[0],'entity_dimension':2},{'kind':'explicit','entities':[1]},{'kind':'explicit','entities':[1],'entity_dimension':2,'component':'foreign'},{'kind':'explicit','entities':[1],'entity_dimension':2,'geometry_revision':0},{'kind':'objects','object_tags':['x'],'component':'c','geometry':'g'},{'kind':'spatial','component':'c','geometry':'g','entity_dimension':2,'query':{} }])
def test_selection_preflight_precedes_model_geom_or_set_mutations(backend,selection):
    b,w,ref,tickets=backend
    with pytest.raises(ExecutionContractError):b.invoke('node.selection_set',{'path':path('a'),'selection':selection},execution(ref),'reject',None)
    assert not tickets;assert w.model_node.list.items['a'].sel.calls==[]

def test_local_explicit_selection_preserves_positive_entity_ids_and_readback(backend):
    b,w,ref,tickets=backend;body={'path':path('a'),'selection':{'kind':'explicit','entities':[1,3],'entity_dimension':2}}
    out=b.invoke('node_selection_set',body,execution(ref),'set',None);assert out['success'];assert out['data']['readback']['entities']==[1,3];assert len(tickets)==1
    read=b.invoke('node.selection_get',{'path':path('a')},execution(ref,1),'read',None);assert read['success'];assert read['data']['selection']['entities']==[1,3];assert len(tickets)==1

def test_registry_publication_and_closed_body_for_all_a_actions():
    identity={'project_id':'p','session_id':'session','model_ref':{'schema_version':1,'session_id':'session','server_instance_id':'server','model_tag':'m','generation':1},'expected_revision':0,'idempotency_key':'k'}
    assert registry.validate_call('api.invoke',{**identity,**request()}).effect=='DYNAMIC'
    names={row['operation_id'] for row in registry.registry_manifest('expert')['operations']};assert registry.NODE_ACTIONS|{'api.describe','api.invoke'}<=names
    assert registry.is_tool_published('api_invoke',profile='expert')
    for name in registry.NODE_ACTIONS:assert registry.operation_for_tool(name.replace('.','_'))==name
    with pytest.raises(ExecutionContractError):registry.validate_call('api.invoke',{**identity,**request(),'unsafe':True})

def test_api_setter_postdispatch_readback_failure_is_unknown(backend,monkeypatch):
    b,w,ref,tickets=backend;node=w.model_node.list.items['a'];original=node.comments
    monkeypatch.setattr(node,'comments',lambda *a:original(*a) if a else 'wrong')
    out=b.invoke('api.invoke',request('new'),execution(ref),'write',None)
    assert not out['success'];assert out['error']['safe_retry'] is False;assert out['execution']['dirty'];assert len(tickets)==1;assert node.comment=='new'

@pytest.mark.parametrize('operation,body',[('api.describe',{'path':path('a')}),('api.invoke',request()),('api.invoke',request('new')),('node.create',{'parent':ROOT,'collection':'feature','tag':'fresh','type_id':'Rectangle'}),('node.label_set',{'path':path('a'),'label':'x'}),('node.active_set',{'path':path('a'),'active':False}),('node.move',{'path':path('a'),'before':path('b')}),('node.copy',{'source':path('a'),'target_parent':ROOT,'tag':'copy'}),('node.selection_get',{'path':path('a')}),('node.selection_set',{'path':path('a'),'selection':{'kind':'explicit','entities':[1],'entity_dimension':2}})])
def test_a_runtime_outputs_match_published_closed_domain_schema(backend,operation,body,monkeypatch):
    if operation=='node.copy':
        from test_unit_d_same_list_copy import install_a_copy_fixture,arguments
        backend=install_a_copy_fixture(backend,monkeypatch);body=arguments()
    b,w,ref,tickets=backend;out=b.invoke(operation,body,execution(ref),'schema',None)
    data=out['data'];schema=registry.operation_describe(operation)['data_schema']
    assert set(schema['required'])<=set(data);assert set(data)<=set(schema['properties']);assert schema['additionalProperties'] is False
    assert data['schema_version']==1 and data['operation']==operation
    if operation.startswith('node.selection_') or operation=='node.copy':assert not data['complete']

def test_copy_readback_mismatch_preserves_created_target_unknown(backend,monkeypatch):
    from test_unit_d_same_list_copy import install_a_copy_fixture,arguments
    b,w,ref,tickets=install_a_copy_fixture(backend,monkeypatch);container=w.collection;original=container.copy
    def corrupt(tag,source):
        node=original(tag,source);node.values['flag']=False;return node
    monkeypatch.setattr(container,'copy',corrupt)
    out=b.invoke('node.copy',arguments(),execution(ref),'copy',None)
    assert not out['success'];assert out['error']['safe_retry'] is False;assert 'copy' in container.items;assert out['execution']['dirty']

def test_cross_parent_tagpath_copy_remains_explicitly_unsupported_before_write(backend):
    b,w,ref,tickets=backend;w.model_node.list.items['b'].list=Collection()
    with pytest.raises(ExecutionContractError) as error:b.invoke('node.copy',{'source':path('a'),'target_parent':path('b'),'tag':'copy'},execution(ref),'copy',None)
    assert error.value.code=='API_UNSUPPORTED';assert not tickets;assert not w.model_node.list.items['b'].list.calls

def test_inherited_selection_readback_none_never_becomes_pass(backend,monkeypatch):
    b,w,ref,tickets=backend;sel=w.model_node.list.items['a'].sel;monkeypatch.setattr(sel,'isInheriting',lambda:None)
    monkeypatch.setattr(w.model_node,'component',lambda tag:w.model_node,raising=False)
    component_path={'segments':[{'collection':'component','tag':'c'},*path('a')['segments']]}
    out=b.invoke('node.selection_set',{'path':component_path,'selection':{'kind':'inherited','component':'c'}},execution(ref),'set',None)
    assert not out['success'];assert out['error']['safe_retry'] is False;assert sel.calls==[('inherit',True)]

def test_public_setter_foreign_returned_node_is_unknown(backend,monkeypatch):
    b,w,ref,tickets=backend;node=w.model_node.list.items['a'];original=node.comments
    def foreign(*args):
        if args:original(*args);return Entity('foreign')
        return original()
    monkeypatch.setattr(node,'comments',foreign)
    out=b.invoke('api.invoke',request('new'),execution(ref),'write',None)
    assert not out['success'];assert out['error']['safe_retry'] is False;assert node.comment=='new';assert 'typed_return' not in out['data'];assert out['data']['status']=='UNKNOWN'

def test_public_descriptor_transport_fences_foreign_and_stale_handles_without_child(monkeypatch):
    from comsol_mcp._java_worker import PersistentJavaWorker,RemoteJava,JavaWorkerError
    worker=object.__new__(PersistentJavaWorker);worker._generation=3;sent=[]
    monkeypatch.setattr(worker,'submit',lambda command,body:sent.append((command,body)) or {'ok':True,'result':{'runtime_version':api.VERSION,'interfaces':[],'methods':[]}})
    current=RemoteJava(worker,'h',3)
    assert worker.describe_public(current)['runtime_version']==api.VERSION;assert sent[0][0]=='public_describe'
    for node in (RemoteJava(worker,'h',2),RemoteJava(object(),'h',3)):
        with pytest.raises(JavaWorkerError):worker.describe_public(node)
    assert len(sent)==1

def test_api_no_resolved_effect_in_shared_service_never_takes_ticket(backend):
    b,w,ref,tickets=backend
    with pytest.raises(ExecutionContractError):b.service.execute_legacy('api_invoke',lambda args: {'success':True,'data':{}},request('new'),model_ref=model_ref_from_mapping(ref),expected_revision=0)
    assert not tickets

def test_api_durable_key_replay_and_changed_body_conflict_before_second_backend(tmp_path,monkeypatch):
    root=tmp_path/'projects';root.mkdir();daemon=ControlDaemon(tmp_path/'control',project_root=root,registry={});calls=[]
    try:
        project=daemon.dispatch({'operation':'project.create','arguments':{'label':'api project','workspace':'api','policy':{'permissions':['inspect','project_write']}},'execution':{'request_id':'create','idempotency_key':'create'}})['data']['project']
        worker=Worker();daemon.backend.service=ExecutionService(SessionLedger('session','server'),worker,project_root=root)
        ref=daemon.service.bind_model('m')['execution']['model_ref'];daemon.backend._bind_model_project(ref,project['project_id'])
        daemon.store.put_metadata('revisions',daemon.backend._model_project_key(ref),{'model_ref':ref,'project_id':project['project_id'],'revision':0})
        assert daemon.backend.model_project_binding(ref)=={'attribution':'PROJECT_BOUND','project_id':project['project_id']}
        monkeypatch.setattr(daemon.backend,'invoke',lambda *a:calls.append(a) or {'success':True,'data':{'scope':'software backend stub'}})
        body={'operation':'api_invoke','arguments':request('value'),'execution':{**execution(ref),'project_id':project['project_id'],'request_id':'api','idempotency_key':'same','rpc_timeout_s':2}}
        first=daemon.dispatch(body)
        alias=copy.deepcopy(body);alias['operation']='api.invoke';again=daemon.dispatch(alias)
        nested=copy.deepcopy(body);nested.update(operation='registry_call',arguments={'operation_id':'api.invoke','arguments':request('value')});third=daemon.dispatch(nested)
        assert third['success'],third
        assert first['success'] and again['success'], (first,again);assert len(calls)==1
        changed=copy.deepcopy(body);changed['arguments']['arguments']=[tv('different')]
        refused=daemon.dispatch(changed);assert not refused['success'];assert refused['error']['code']=='IDEMPOTENCY_CONFLICT';assert len(calls)==1
    finally:daemon.close()

@pytest.mark.parametrize('route',['api_invoke','api.invoke','registry_call','operation_call'])
@pytest.mark.parametrize('case',['read_ok','setter_denied','unknown','ambiguous','body_ref_conflict','body_project_conflict','outer_type','alias_mismatch','nested_identity'])
def test_stateful_daemon_a_admission_acl_and_closed_routes_before_backend(tmp_path,monkeypatch,route,case):
    root=tmp_path/'projects';root.mkdir();daemon=ControlDaemon(tmp_path/'control',project_root=root,registry={});calls=[]
    try:
        project=daemon.dispatch({'operation':'project.create','arguments':{'label':'read','workspace':'read','policy':{'permissions':['inspect']}},'execution':{'request_id':'create','idempotency_key':'create'}})['data']['project']
        worker=Worker();daemon.backend.service=ExecutionService(SessionLedger('session','server'),worker,project_root=root)
        ref=daemon.service.bind_model('m')['execution']['model_ref'];daemon.backend._bind_model_project(ref,project['project_id'])
        daemon.store.put_metadata('revisions',daemon.backend._model_project_key(ref),{'model_ref':ref,'project_id':project['project_id'],'revision':0})
        assert daemon.backend.model_project_binding(ref)=={'attribution':'PROJECT_BOUND','project_id':project['project_id']}
        monkeypatch.setattr(daemon.backend,'invoke',lambda *a:calls.append(a) or {'success':True,'data':{'scope':'SOFTWARE_BACKEND_STUB'}})
        args=request();e={**execution(ref),'project_id':project['project_id'],'rpc_timeout_s':2}
        if case=='setter_denied':args=request('new')
        elif case=='unknown':args['method']='getClass'
        elif case=='ambiguous':monkeypatch.setattr(api,'CAPABILITIES',api.CAPABILITIES+(api.Capability(api.MODEL_ENTITY,'comments',(),'java.lang.String','WRITE'),))
        elif case=='body_ref_conflict':args['model_ref']={**ref,'model_tag':'foreign'}
        elif case=='body_project_conflict':args['project_id']='foreign'
        elif case=='outer_type':e['model_ref']='m'
        elif case=='nested_identity':args['request_id']='nested-request'
        if route in {'registry_call','operation_call'}:
            args={'operation_id':'api_invoke' if case=='alias_mismatch' else 'api.invoke','arguments':args}
        elif case=='alias_mismatch':args['operation_id']='api.invoke'
        out=daemon.dispatch({'operation':route,'arguments':args,'execution':e})
        if case=='read_ok':assert out['success'],out;assert len(calls)==1
        else:
            assert not out['success'],out;assert calls==[]
            if case=='setter_denied':assert out['error']['code']=='PERMISSION_DENIED'
            elif case in {'unknown','ambiguous'}:assert out['error']['code']=='API_UNSUPPORTED'
    finally:daemon.close()

def test_compiled_public_receiver_exact_signature_worker_software_only(tmp_path):
    import os,subprocess
    from pathlib import Path
    root=Path(__file__).resolve().parents[1];jdk=Path('/Library/Java/JavaVirtualMachines/amazon-corretto-11.jdk/Contents/Home')
    jars=sorted(p for p in Path('/Applications/COMSOL64/Multiphysics/apiplugins').glob('*.jar') if not p.name.startswith('._'))
    classpath=os.pathsep.join(str(p) for p in jars);classes=tmp_path/'public-api-classes';classes.mkdir()
    source=root/'comsol_mcp/worker_java/PersistentComsolWorker.java';harness=root/'tests/java/PersistentComsolWorkerPublicApiHarness.java'
    compiled=subprocess.run([str(jdk/'bin/javac'),'-encoding','UTF-8','-classpath',classpath,'-d',str(classes),str(source),str(harness)],cwd=root,capture_output=True,text=True,timeout=120)
    assert compiled.returncode==0,compiled.stdout+compiled.stderr
    executed=subprocess.run([str(jdk/'bin/java'),'-cp',str(classes)+os.pathsep+classpath,'comsol_mcp.worker_java.PersistentComsolWorkerPublicApiHarness'],cwd=root,capture_output=True,text=True,timeout=30)
    assert executed.returncode==0,executed.stdout+executed.stderr
    assert executed.stdout.strip()=='PUBLIC_RECEIVER_EXACT_SIGNATURE_SOFTWARE_PASS'

def test_actual_python_submit_descriptor_route_and_unknown_command_preflight(monkeypatch):
    from comsol_mcp._java_worker import PersistentJavaWorker,RemoteJava,JavaWorkerError
    worker=object.__new__(PersistentJavaWorker);worker._generation=3;requests=[];events=[]
    monkeypatch.setattr(worker,'_emit_request_event',lambda *a,**kw:events.append((a,kw)))
    descriptor={'runtime_version':api.VERSION,'interfaces':[api.MODEL_ENTITY],'methods':[method(api.MODEL_ENTITY,'comments',(),'java.lang.String')]}
    monkeypatch.setattr(worker,'_request',lambda body,**kw:requests.append(dict(body)) or {'ok':True,'result':descriptor,'status':'succeeded'})
    out=worker.describe_public(RemoteJava(worker,'owned',3));assert out==descriptor
    assert requests[0]['type']=='public_describe' and requests[0]['handle']=='owned' and requests[0]['generation']==3
    assert [event[0][0] for event in events]==['submitted','observed']
    with pytest.raises(JavaWorkerError):worker.submit('describe_arbitrary_Class',{'class':'java.lang.Class'})
    assert len(requests)==1

@pytest.mark.parametrize('returned',[5,True,{},['string']])
def test_api_getter_unexpected_typed_return_is_unknown_with_no_write_ticket(backend,monkeypatch,returned):
    b,w,ref,tickets=backend;monkeypatch.setattr(w.model_node.list.items['a'],'comments',lambda:returned)
    out=b.invoke('api.invoke',request(),execution(ref),'read',None)
    assert not out['success'];assert out['data']['status']=='UNKNOWN';assert 'typed_return' not in out['data'];assert not tickets

@pytest.mark.parametrize('spec',[{'kind':'explicit','entities':[1],'entity_dimension':2,'tag':'ignored-named-tag'},{'kind':'inherited','component':'c','entities':[1]},{'kind':'all','component':'c','geometry':'g','entity_dimension':2,'query':{}}])
def test_selection_kind_inapplicable_fields_never_silently_ignored(spec):
    worker=Worker()
    with pytest.raises(ExecutionContractError) as error:prepare_node_action(worker,'m','node.selection_set',{'path':path('a'),'selection':spec})
    assert error.value.code=='INVALID_REQUEST';assert not worker.model_node.list.items['a'].sel.calls

def test_selection_wrong_runtime_fails_before_getter_or_mutation(backend):
    b,w,ref,tickets=backend;d=w.describe_public(w.model_node.list.items['a']);d['runtime_version']='6.3';w.override=d
    with pytest.raises(ExecutionContractError) as error:b.invoke('node.selection_get',{'path':path('a')},execution(ref),'read',None)
    assert error.value.code=='API_UNSUPPORTED';assert not tickets;assert not w.model_node.list.items['a'].sel.calls

def test_selection_untyped_entity_readback_is_explicit_incomplete(backend,monkeypatch):
    b,w,ref,tickets=backend;monkeypatch.setattr(w.model_node.list.items['a'].sel,'entities',lambda:[1.5])
    out=b.invoke('node.selection_get',{'path':path('a')},execution(ref),'read',None)
    assert out['success'];assert out['data']['complete'] is False;assert out['data']['selection']['entities'] is None;assert out['data']['selection']['entities_error'];assert out['data']['errors'];assert not tickets
