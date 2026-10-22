"""Unit A isolated software contracts. Doubles are not COMSOL native evidence."""
from contextlib import nullcontext
from dataclasses import replace
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
def raw(value):return value.get('data') if isinstance(value,dict) and {'kind','shape','data'}<=set(value) else value
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
        self.values={'flag':True,'empty':[],'matrix':[[1.0]],'expr':'a+b','bool':False,'integer':7,'double':1.25,
                     'string':'value','bool_empty':[],'int_array':[1,2],
                     'double_matrix':[[1.0,2.0],[3.0,4.0]],'string_matrix':[['a','b'],['c','d']],
                     'bool_matrix_empty':[],'string_array_null':None,'PortName':'1','PortType':'Numeric'}
        self.sel=Selection()
        self.metadata_calls=[];self.presence_overrides={}
    def feature(self,tag=None):return self.list if tag is None else self.list.items[tag]
    def getType(self):return self.type_id
    def tag(self):return self.tag_value
    def resolveModelPath(self):return 'm/'+self.tag_value
    def comments(self,*args):
        if args:self.calls.append(('comments',args));self.comment=raw(args[0]);return self
        return self.comment
    def label(self,*args):
        if args:self.calls.append(('label',args));self.display=raw(args[0]);return self
        return self.display
    def active(self,value):self.calls.append(('active',value));self.flag=raw(value);return self
    def isActive(self):return self.flag
    def selection(self,*args):return self.sel
    def properties(self):return list(self.values)
    def hasProperty(self,name):
        name=raw(name)
        self.metadata_calls.append(('hasProperty',name))
        if name in self.presence_overrides:
            value=self.presence_overrides[name]
            if isinstance(value,BaseException):raise value
            return value
        return name in self.values
    def getValueType(self,name):
        name=raw(name)
        self.metadata_calls.append(('getValueType',name))
        return {'flag':'Boolean','empty':'DoubleArray','matrix':'DoubleMatrix','expr':'String',
                'PortName':'String','PortType':'String','PortOrientation':'String','bool':'Boolean',
                'integer':'Int','double':'Double','string':'String','bool_empty':'BooleanArray',
                'int_array':'IntArray','double_matrix':'DoubleMatrix','string_matrix':'StringMatrix',
                'bool_matrix_empty':'BooleanMatrix','string_array_null':'StringArray',
                'PortName':'String','PortType':'String'}.get(name,'FutureType')
    def getAllowedPropertyValues(self,name):
        name=raw(name)
        self.metadata_calls.append(('getAllowedPropertyValues',name))
        return {'PortName':['1','2'],'PortType':['Numeric','Periodic']}.get(name)
    def getType(self):return self.type_id
    def getBoolean(self,name):return self.values[raw(name)]
    def getBooleanArray(self,name):return self.values[raw(name)]
    def getBooleanMatrix(self,name):return self.values[raw(name)]
    def getDouble(self,name):return self.values[raw(name)]
    def getDoubleArray(self,name):return self.values[raw(name)]
    def getDoubleMatrix(self,name):return self.values[raw(name)]
    def getInt(self,name):return self.values[raw(name)]
    def getIntArray(self,name):return self.values[raw(name)]
    def getIntMatrix(self,name):return self.values[raw(name)]
    def getString(self,name):self.metadata_calls.append(('getString',raw(name)));return self.values[raw(name)]
    def getStringArray(self,name):return self.values[raw(name)]
    def getStringMatrix(self,name):return self.values[raw(name)]
    def set(self,name,value):self.calls.append(('set',name,value));self.values[name]=value['data']

class Collection:
    def __init__(self):self.items={tag:Entity(tag) for tag in ['b','a','c']};self.order=['b','a','c'];self.calls=[]
    def tags(self):return list(self.order)
    def create(self,tag,type_id):self.calls.append(('create',tag,type_id));self.items[tag]=Entity(tag,type_id);self.order.append(tag);return self.items[tag]
    def copy(self,tag,source):self.calls.append(('copy',tag,source));e=copy.deepcopy(self.items[source]);e.tag_value=tag;self.items[tag]=e;self.order.append(tag);return e
    def move(self,tag,index):self.calls.append(('move',tag,index));self.order.remove(tag);self.order.insert(index['data'],tag)
    def remove(self,tag):self.calls.append(('remove',tag));del self.items[tag];self.order.remove(tag)

class Worker:
    def __init__(self):self.model_node=Entity('m');self.model_node.list=Collection();self.descriptors=[];self.override=None;self.include_model_root_interface=False
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
            if node is self.model_node and self.include_model_root_interface:
                owners.append('com.comsol.model.Model')
            rows=[method(cap.receiver,cap.method,cap.parameters,cap.returns) for cap in api.CAPABILITIES]
            rows.extend([method(api.MODEL_ENTITY,'resolveModelPath',(),'java.lang.String'),
                         method(api.MODEL_ENTITY,'isactive',(),'boolean'),
                         method(owners[1],'selection'),
                         method(owners[1],'selection',('java.lang.String',)),
                         method(owners[1],'set',('java.lang.String','double'),owners[1]),
                         method(owners[1],'remove',('java.lang.String','int'),owners[1])])
            if node is self.model_node and self.include_model_root_interface:
                rows.append(method('com.comsol.model.Model','getComsolVersion',(),'java.lang.String'))
        return {'runtime_version':api.VERSION,'interfaces':owners,'methods':rows,
                'metadata_complete':True,'metadata_limits':{'interfaces':1024,'methods':8192}}
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
def api_request(method_name,args=(),effect='READ',signature=None):
    body={'path':path('a'),'method':method_name,'arguments':list(args),'declared_effect':effect}
    if signature is not None:body['java_signature']=list(signature)
    return body

@pytest.mark.parametrize('route',['api.invoke','api_invoke','registry_call','operation_call'])
def test_api_comments_actual_receiver_read_write_permissions_before_ticket(backend,route):
    b,w,ref,tickets=backend;e=execution(ref);args=request()
    if route in {'registry_call','operation_call'}:args={'operation_id':'api.invoke','arguments':args}
    read=b.invoke(route,args,e,'read',None);assert read['success'];assert read['data']['typed_return']['value']==tv('',signature='java.lang.String');assert not tickets
    args=request('new')
    if route in {'registry_call','operation_call'}:args={'operation_id':'api.invoke','arguments':args}
    write=b.invoke(route,args,e,'write',None);assert write['success'];assert len(tickets)==1;assert write['data']['typed_return']['model_ref']==ref;assert write['execution']['revision']==1
    assert write['data']['resolved_effect']=='WRITE';assert w.model_node.list.items['a'].comment=='new'

@pytest.mark.parametrize('case',['declared_read','unknown_method','wrong_signature','boxed_primitive_signature','wrong_typed_value_signature','wrong_receiver','wrong_version','missing_ref','body_ref_mismatch','stale_revision','permission'])
def test_api_invalid_requests_fail_before_ticket_or_setter(backend,case):
    b,w,ref,tickets=backend;e=execution(ref);args=request('new')
    if case=='declared_read':args['declared_effect']='READ'
    elif case=='unknown_method':args['method']='getClass'
    elif case=='wrong_signature':args['java_signature']=['int']
    elif case=='boxed_primitive_signature':args=api_request('active',[tv(True,'boolean',signature='java.lang.Boolean')],'WRITE',signature=['java.lang.Boolean'])
    elif case=='wrong_typed_value_signature':args=api_request('getString',[tv('flag',signature='java.lang.StringBuilder')],'READ')
    elif case in {'wrong_receiver','wrong_version'}:
        d=w.describe_public(w.model_node.list.items['a']);d['interfaces']=[] if case=='wrong_receiver' else d['interfaces'];d['runtime_version']='6.3' if case=='wrong_version' else d['runtime_version'];w.override=d
    elif case=='missing_ref':del e['model_ref']
    elif case=='body_ref_mismatch':args['model_ref']={**ref,'model_tag':'other'}
    elif case=='stale_revision':e['expected_revision']=2
    else:b.service.ledger.permissions={'inspect'}
    with pytest.raises(ExecutionContractError):b.invoke('api.invoke',args,e,'reject',None)
    assert tickets==[];assert w.model_node.list.items['a'].calls==[]

def test_describe_lists_actual_signatures_and_separates_reviewed_policy(backend):
    b,w,ref,tickets=backend
    out=b.invoke('api_describe',{'path':path('a')},execution(ref),'describe',None)
    assert out['success'];methods=out['data']['methods']
    comments=[row for row in methods if row['method']=='comments']
    assert [row['effect'] for row in comments]==['READ','WRITE']
    assert all(row['policy_status']=='REVIEWED' and row['invoke_allowed'] is True for row in comments)
    unknown=next(row for row in methods if row['method']=='resolveModelPath')
    assert unknown['policy_status']=='UNREVIEWED' and unknown['invoke_allowed'] is False
    assert unknown['controlled_alternative']['operation']=='code.execute_java'
    prop_set=next(row for row in methods if row['method']=='set')
    assert prop_set['policy_status']=='UNREVIEWED'
    assert prop_set['controlled_alternative']['operation']=='node.property_set'
    assert prop_set['controlled_alternative']['authorizes_this_signature'] is False
    assert out['data']['runtime_version_raw']==api.VERSION and out['data']['metadata_complete'] is True
    doc=comments[0]['documentation']
    assert doc=={'path':api.MODEL_ENTITY_DOC,'sha256':api.MODEL_ENTITY_DOC_SHA,
                 'source_version':'6.4.0.293','reference_only':True,
                 'signature_observation_scope':api.SIGNATURE_OBSERVATION_SCOPE}
    doc_schema=registry.operation_describe('api.describe')['data_schema']['properties']['methods']['items']['properties']['documentation']['oneOf'][1]
    assert set(doc_schema['required'])==set(doc)
    assert doc_schema['properties']['source_version']['const']=='6.4.0.293'
    assert doc_schema['properties']['reference_only']['const'] is True
    assert tickets==[]
    with pytest.raises(ExecutionContractError):b.invoke('api.describe',{'path':path('a'),'method':'getClass'},execution(ref),'no',None)


@pytest.mark.parametrize(('method_name','args','effect','expected'),[
    ('tag',(), 'READ', tv('a',signature='java.lang.String')),
    ('isActive',(), 'READ', tv(True,'boolean',signature='boolean')),
    ('getType',(), 'READ', tv('Rectangle',signature='java.lang.String')),
    ('properties',(), 'READ', tv(list(Entity().values),'string',[len(Entity().values)],'[Ljava.lang.String;')),
    ('getValueType',(tv('bool'),), 'READ', tv('Boolean',signature='java.lang.String')),
    ('hasProperty',(tv('bool'),), 'READ', tv(True,'boolean',signature='boolean')),
    ('getAllowedPropertyValues',(tv('PortType'),), 'READ', tv(['Numeric','Periodic'],'string',[2],'[Ljava.lang.String;')),
])
def test_public_model_and_propfeature_metadata_getters_have_typed_wire(backend,method_name,args,effect,expected):
    b,w,ref,tickets=backend
    result=b.invoke('api.invoke',api_request(method_name,args,effect),execution(ref),'read-'+method_name,None)
    assert result['success'],result
    assert result['data']['typed_return']['kind']=='value'
    assert result['data']['typed_return']['value']==expected
    assert tickets==[]


@pytest.mark.parametrize(('property_name','method_name','expected'),[
    ('bool','getBoolean',tv(False,'boolean',signature='boolean')),
    ('integer','getInt',tv(7,'int32',signature='int')),
    ('double','getDouble',tv(1.25,'float64',signature='double')),
    ('string','getString',tv('value',signature='java.lang.String')),
    ('bool_empty','getBooleanArray',tv([],'boolean',[0],'[Z')),
    ('int_array','getIntArray',tv([1,2],'int32',[2],'[I')),
    ('double_matrix','getDoubleMatrix',tv([[1.0,2.0],[3.0,4.0]],'float64',[2,2],'[[D')),
    ('string_matrix','getStringMatrix',tv([['a','b'],['c','d']],'string',[2,2],'[[Ljava.lang.String;')),
    ('bool_matrix_empty','getBooleanMatrix',tv([],'boolean',[0],'[[Z')),
    ('string_array_null','getStringArray',None),
])
def test_propfeature_typed_getters_preserve_scalar_array_rank_empty_and_null(backend,property_name,method_name,expected):
    b,w,ref,tickets=backend
    result=b.invoke('api.invoke',api_request(method_name,[tv(property_name)]),execution(ref),'typed-'+property_name,None)
    assert result['success'],result
    actual=result['data']['typed_return']
    if expected is None:
        assert actual=={'kind':'null','java_type':'[Ljava.lang.String;'}
    else:
        assert actual['kind']=='value' and actual['value']==expected
    assert tickets==[]


@pytest.mark.parametrize(('method_name','property_name','value_type'),[
    ('isActive',None,None),('getBoolean','bool','Boolean'),('getInt','integer','Int'),
    ('getDouble','double','Double'),
])
def test_primitive_public_getters_returning_null_are_unknown(backend,method_name,property_name,value_type):
    b,w,ref,tickets=backend;node=w.model_node if property_name is None else w.model_node.list.items['a']
    if property_name is None:
        node.flag=None;args=api_request(method_name,(), 'READ');args['path']=ROOT
    else:
        node.values[property_name]=None;args=api_request(method_name,[tv(property_name)],'READ')
    result=b.invoke('api.invoke',args,execution(ref),'null-primitive-'+method_name,None)
    assert result['success'] is False
    assert result['data']['status']=='UNKNOWN'
    assert result['data']['errors'][0]['code']=='EXECUTION_STATE_UNKNOWN'
    assert tickets==[]


def test_nullable_reference_getter_returns_remain_typed_null(backend):
    b,w,ref,tickets=backend;node=w.model_node.list.items['a'];node.values['string']=None;node.values['double_matrix']=None
    for method_name,property_name,java_type in [('getString','string','java.lang.String'),
                                                 ('getDoubleMatrix','double_matrix','[[D')]:
        result=b.invoke('api.invoke',api_request(method_name,[tv(property_name)]),execution(ref),
                        'null-reference-'+method_name,None)
        assert result['success'] is True
        assert result['data']['typed_return']=={'kind':'null','java_type':java_type}
    assert tickets==[]


def test_api_describe_and_invoke_accept_bound_model_root_without_global_access(backend):
    b,w,ref,tickets=backend;e=execution(ref);w.include_model_root_interface=True
    described=b.invoke('api.describe',{'path':ROOT},e,'root-describe',None)
    assert described['success'] is True
    assert described['data']['path']==ROOT
    assert 'com.comsol.model.Model' in described['data']['receiver_interfaces']
    assert 'com.comsol.model.ModelUtil' not in described['data']['receiver_interfaces']
    model_version=next(row for row in described['data']['methods'] if row['method']=='getComsolVersion')
    assert model_version['policy_status']=='UNREVIEWED' and model_version['invoke_allowed'] is False
    request_body=api_request('comments',(), 'READ');request_body['path']=ROOT
    invoked=b.invoke('api.invoke',request_body,e,'root-invoke',None)
    assert invoked['success'] is True
    assert invoked['data']['path']==ROOT
    assert invoked['data']['typed_return']['value']==tv('',signature='java.lang.String')
    assert tickets==[]
    registry.validate_call('api.describe',{'path':ROOT},allow_unbound_identity=True)


@pytest.mark.parametrize(('method_name','args','effect','getter','expected'),[
    ('label',(tv('renamed'),),'WRITE','label','renamed'),
    ('comments',(tv('new note'),),'WRITE','comments','new note'),
    ('active',(tv(False,'boolean'),),'WRITE','isActive',False),
])
def test_modelentity_setters_require_exact_signature_and_typed_readback(backend,method_name,args,effect,getter,expected):
    b,w,ref,tickets=backend
    result=b.invoke('api.invoke',api_request(method_name,args,effect),execution(ref),'set-'+method_name,None)
    assert result['success'],result
    assert result['data']['readback']==expected
    assert result['data']['typed_return']['kind']=='node_ref'
    assert len(tickets)==1


def test_release_plus_exact_signature_admits_63_and_64_but_rejects_other_release(backend):
    b,w,ref,tickets=backend
    for raw_version in ('COMSOL Multiphysics 6.3 (Build: 290)', '6.4.0.901'):
        w.override={**w.describe_public(w.model_node.list.items['a']), 'runtime_version':raw_version}
        prepared=api.prepare_invoke(w,'m',api_request('comments',(), 'READ'))
        assert prepared['descriptor']['runtime_version_raw']==raw_version
        assert prepared['capability'].method=='comments'
    w.override={**w.describe_public(w.model_node.list.items['a']), 'runtime_version':'6.5.0.100'}
    with pytest.raises(ExecutionContractError) as error:
        api.prepare_invoke(w,'m',api_request('comments',(), 'READ'))
    assert error.value.code=='API_UNSUPPORTED'
    assert tickets==[]


def test_63_write_adapter_uses_actual_signature_and_preserves_observed_build(backend):
    b,w,ref,tickets=backend
    descriptor=w.describe_public(w.model_node.list.items['a'])
    descriptor['runtime_version']='COMSOL Multiphysics 6.3 (Build: 290)'
    w.override=descriptor
    result=b.invoke('api.invoke',api_request('comments',[tv('6.3 write')],'WRITE'),execution(ref),'m1-63-write',None)
    assert result['success'],result
    assert result['data']['evidence']['descriptor']['runtime_version_raw']==descriptor['runtime_version']
    assert result['data']['readback']=='6.3 write' and len(tickets)==1


def test_63_node_action_requires_the_actual_reviewed_signature_before_write(backend):
    b,w,ref,tickets=backend
    descriptor=w.describe_public(w.model_node.list.items['a'])
    descriptor['runtime_version']='COMSOL Multiphysics 6.3 (Build: 290)'
    w.override=descriptor
    result=b.invoke('node.label_set',{'path':path('a'),'label':'6.3 label'},execution(ref),'m1-63-node-write',None)
    assert result['success'],result
    assert result['data']['evidence']['descriptor']['runtime_version_raw']==descriptor['runtime_version']
    assert len(tickets)==1
    bad=copy.deepcopy(descriptor)
    for row in bad['methods']:
        if row['interface']==api.MODEL_ENTITY and row['method']=='label' and row['parameters']==['java.lang.String']:
            row['returns']='java.lang.Object'
    w.override=bad
    with pytest.raises(ExecutionContractError) as error:
        b.invoke('node.label_set',{'path':path('a'),'label':'refused'},execution(ref,1),'m1-63-bad-sig',None)
    assert error.value.code=='API_UNSUPPORTED'
    assert len(tickets)==1 and w.model_node.list.items['a'].display=='6.3 label'


def test_method_descriptor_effect_claim_cannot_override_server_policy(backend):
    b,w,ref,tickets=backend
    descriptor=w.describe_public(w.model_node.list.items['a'])
    for row in descriptor['methods']:
        if row['method']=='comments' and row['parameters']==['java.lang.String']:
            row['effect']='READ'
    w.override=descriptor
    with pytest.raises(ExecutionContractError) as error:
        api.prepare_invoke(w,'m',api_request('comments',[tv('write')],'READ'))
    assert error.value.code=='PERMISSION_DENIED'
    assert tickets==[] and w.model_node.list.items['a'].calls==[]

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
    else:
        original=next(cap for cap in api.CAPABILITIES if cap.method=='comments' and cap.parameters==('java.lang.String',))
        monkeypatch.setattr(api,'CAPABILITIES',api.CAPABILITIES+(replace(original,effect='READ'),))
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
    if operation=='api.invoke':
        capability_schema=schema['properties']['capability']
        capability=data['capability']
        assert capability_schema['additionalProperties'] is False
        assert set(capability_schema['required'])==set(capability)
        assert capability['documentation']['source_version']=='6.4.0.293'
        assert capability['documentation']['reference_only'] is True
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
        elif case=='ambiguous':
            original=next(cap for cap in api.CAPABILITIES if cap.method=='comments' and cap.parameters==())
            monkeypatch.setattr(api,'CAPABILITIES',api.CAPABILITIES+(replace(original,effect='WRITE'),))
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


def test_model_entity_list_index_is_typed_receiver_only_and_dispatches_primitive_int(tmp_path):
    """SDK proxy control for R31; no COMSOL Worker/model is started."""
    import os, subprocess
    from pathlib import Path
    root = Path(__file__).resolve().parents[1]
    jdk = Path('/Library/Java/JavaVirtualMachines/amazon-corretto-11.jdk/Contents/Home')
    comsol_root = Path('/Applications/COMSOL64/Multiphysics')
    from comsol_mcp._java_worker import JavaWorkerPaths
    classpath, _manifest_sha, jar_count, _jar_content_sha = JavaWorkerPaths(
        comsol_root, jdk, project_root=root).classpath()
    assert jar_count == 25
    classes = tmp_path / 'model-entity-list-index-classes'
    classes.mkdir()
    source = root / 'comsol_mcp/worker_java/PersistentComsolWorker.java'
    harness = root / 'tests/java/PersistentComsolWorkerModelEntityListIndexHarness.java'
    compiled = subprocess.run(
        [str(jdk / 'bin/javac'), '-encoding', 'UTF-8', '-classpath', classpath, '-d', str(classes),
         str(source), str(harness)], cwd=root, capture_output=True, text=True, timeout=120)
    assert compiled.returncode == 0, compiled.stdout + compiled.stderr
    executed = subprocess.run(
        [str(jdk / 'bin/java'), '-cp', str(classes) + os.pathsep + classpath,
         'comsol_mcp.worker_java.PersistentComsolWorkerModelEntityListIndexHarness'],
        cwd=root, capture_output=True, text=True, timeout=30)
    assert executed.returncode == 0, executed.stdout + executed.stderr
    assert executed.stdout.strip() == 'MODEL_ENTITY_LIST_INDEX_TYPED_RECEIVER_DISPATCH_SOFTWARE_PASS'

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


def _operation_call(backend,ref,operation,arguments,key):
    execution_context=execution(ref)
    execution_context.update({'idempotency_key':key,'request_id':key})
    return backend.invoke('operation_call',{'operation_id':operation,'arguments':arguments},
                          execution_context,key,None)


def test_named_public_property_schema_reads_has_property_before_typed_metadata_and_get(backend):
    b,w,ref,tickets=backend;node=w.model_node.list.items['a'];node.values['PortName']='1'
    schema=_operation_call(b,ref,'node.property_schema',{'path':path('a'),'name':'PortName'},'port-schema')
    assert schema['success'],schema
    assert schema['data']['has_property'] is True
    assert schema['data']['value_type']=='String'
    assert schema['data']['allowed_values']==['1','2']
    assert node.metadata_calls[:3]==[
        ('hasProperty','PortName'),('getValueType','PortName'),('getAllowedPropertyValues','PortName')]

    typed=_operation_call(b,ref,'node.property_get',{'path':path('a'),'names':['PortName']},'port-get')
    assert typed['success'],typed
    assert typed['data']['properties']==[
        {'name':'PortName','value':{'kind':'string','shape':[],'data':'1',
                                    'java_signature':'java.lang.String'}}]
    assert ('getString','PortName') in node.metadata_calls
    assert node.calls==[] and tickets==[]


def test_named_public_property_schema_reports_absent_or_unknown_before_metadata(backend):
    b,w,ref,tickets=backend;node=w.model_node.list.items['a']
    absent=_operation_call(b,ref,'node.property_schema',
                           {'path':path('a'),'name':'PortOrientation'},'missing-schema')
    assert absent['success'],absent
    assert absent['data']['has_property'] is False
    assert absent['data']['metadata_status']=='ABSENT'
    assert node.metadata_calls==[('hasProperty','PortOrientation')]

    node.metadata_calls.clear()
    node.presence_overrides['PortOrientation']=RuntimeError('presence probe unavailable')
    unknown=_operation_call(b,ref,'node.property_schema',
                            {'path':path('a'),'name':'PortOrientation'},'unknown-schema')
    assert unknown['success'],unknown
    assert unknown['data']['has_property'] is None
    assert unknown['data']['metadata_status']=='UNKNOWN'
    assert node.metadata_calls==[('hasProperty','PortOrientation')]
    assert node.calls==[] and tickets==[]

