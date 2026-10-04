"""Versioned pure-getter projections; bytes/digests are provenance, never equality."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import stat
import time
from typing import Any, Mapping
from uuid import uuid4

from ._execution_contract import ExecutionContractError, canonical_project_path, model_ref_from_mapping
from ._g2_contract import ACCESSOR_METHODS, NodePath, engine_value_spec, typed_value_from_engine, validate_typed_value
from ._g2_engine import _parse_search_budget
from ._g2_public_api import VERSION, IDENTITY_FIELDS, describe_receiver
from ._g2_checkpoint_ops import pointer, pointer_equal, source_identity, retain_terminal_source_guard

SCHEMA='comsol-public-semantic-projection-v1'
STRING='java.lang.String'
_MISSING=object()

def canonical(value):return json.dumps(value,sort_keys=True,separators=(',',':'),allow_nan=False)
def fail(code,message):raise ExecutionContractError(code,message)
def path_key(path):return canonical(path)
def java_name(name):
    return {'[I':'int[]','[J':'long[]','[D':'double[]','[Z':'boolean[]','[Ljava.lang.String;':'java.lang.String[]',
            '[[I':'int[][]','[[D':'double[][]','[[Z':'boolean[][]','[[Ljava.lang.String;':'java.lang.String[][]'}.get(name,name)

def normalize(arguments):
    if not isinstance(arguments,Mapping) or set(arguments)-{'left','right','scope'}-IDENTITY_FIELDS or not {'left','right'}<=set(arguments):
        fail('INVALID_REQUEST','checkpoint.diff requires closed left/right/scope body')
    for side in ('left','right'):
        if not isinstance(arguments[side],str) or not arguments[side] or '\x00' in arguments[side]:fail('INVALID_REQUEST',side+' must be exact current or checkpoint ID')
    scope=arguments.get('scope',{})
    if not isinstance(scope,Mapping) or set(scope)-{'mode','paths','properties','budget','page_size'}:fail('INVALID_REQUEST','diff scope has unknown fields')
    mode=scope.get('mode','paths' if 'paths' in scope or 'properties' in scope else 'model')
    if mode not in {'model','paths'}:fail('INVALID_REQUEST','scope mode must be model or paths')
    out={'mode':mode}
    if mode=='model':
        if 'paths' in scope or 'properties' in scope:fail('INVALID_REQUEST','model mode cannot narrow paths or properties')
    else:
        roots=scope.get('paths',[]);props=scope.get('properties',[])
        if not isinstance(roots,list) or not isinstance(props,list) or not roots and not props:fail('INVALID_REQUEST','paths mode requires paths or named properties')
        parsed=[NodePath.from_wire(p).as_dict() for p in roots];named=[]
        for p in props:
            if not isinstance(p,Mapping) or set(p)!={'path','names'} or not isinstance(p['names'],list) or not p['names'] or not all(isinstance(n,str) and n for n in p['names']):
                fail('INVALID_REQUEST','property scope requires exact path and nonempty names')
            if len(p['names'])!=len(set(p['names'])):fail('INVALID_REQUEST','duplicate requested property name')
            named.append({'path':NodePath.from_wire(p['path']).as_dict(),'names':sorted(p['names'])})
        if len({path_key(p) for p in parsed})!=len(parsed) or len({path_key(p['path']) for p in named})!=len(named):fail('INVALID_REQUEST','duplicate requested scope path')
        out.update(paths=sorted(parsed,key=path_key),properties=sorted(named,key=lambda p:path_key(p['path'])))
    budget=_parse_search_budget(scope.get('budget'));page=scope.get('page_size',100)
    if type(page) is not int or not 1<=page<=500:fail('INVALID_REQUEST','page_size must be an integer1..500')
    return {'left':arguments['left'],'right':arguments['right'],'scope':out,'budget':budget,'page_size':page}

def input_schema():
    from ._g2_checkpoint_ops import input_schema as b_schema
    identities=b_schema('api.probe')['properties'];identities={k:v for k,v in identities.items() if k!='probe'}
    # READ retains optional idempotency through the shared outer envelope; it
    # does not add an expected_revision requirement or a caller load policy.
    return {'type':'object','required':['project_id','session_id','model_ref','left','right'],
            'properties':{**identities,'left':{'type':'string','minLength':1},'right':{'type':'string','minLength':1},
                          'scope':{'type':'object','properties':{'mode':{'enum':['model','paths']},'paths':{'type':'array','items':{'type':'object'}},
                            'properties':{'type':'array','items':{'type':'object'}},'budget':{'type':'object'},'page_size':{'type':'integer','minimum':1,'maximum':500}},'additionalProperties':False}},'additionalProperties':False}

def data_schema():
    names=['schema_version','operation','status','complete','source_identity','left_identity','right_identity','normalized_scope','projection_schema',
           'comparison','changes','coverage','errors','source_observation','owned_copies','cleanup','provenance','evidence']
    properties={n:{'type':'object'} for n in names}
    properties.update(schema_version={'const':1},operation={'const':'checkpoint.diff'},status={'enum':['SUCCEEDED','INCOMPLETE','UNKNOWN']},complete={'type':'boolean'},projection_schema={'const':SCHEMA},
        comparison={'type':'object','required':['status','equal','complete'],'properties':{'status':{'enum':['EQUAL','DIFFERENT','INCOMPLETE']},'equal':{'type':['boolean','null']},'complete':{'type':'boolean'}},'additionalProperties':False},
        changes={'type':'array','items':{'type':'object','required':['path','field','change'],'properties':{'path':{'type':'object'},'field':{'type':'string'},'change':{'enum':['added','removed','changed']},'before':{},'after':{}},'additionalProperties':False}},
        coverage={'type':'array'},errors={'type':'array'},owned_copies={'type':'array'},cleanup={'type':'object'})
    properties['execution_state_unknown']={'type':'boolean'}
    return {'type':'object','required':names,'properties':properties,'additionalProperties':False}

class BudgetStop(Exception):pass

class UnknownObservationStop(Exception):
    """Stop a model.compare projection after an unresolved Worker observation."""

class Budget:
    SERIALIZED_BYTES_CAP=500*1024*1024
    def __init__(self,limits):self.limits=limits;self.started=time.monotonic();self.rpc=0;self.nodes=0;self.pages=0;self.projected_bytes=0
    def take(self,node=False):
        field='max_nodes' if node else 'max_rpc';value=self.nodes if node else self.rpc
        if value>=self.limits[field] or time.monotonic()-self.started>=self.limits['max_seconds']:raise BudgetStop(field)
        if node:self.nodes+=1
        else:self.rpc+=1
    def record_bytes(self,value):
        self.projected_bytes+=len(canonical(value).encode())
        if self.projected_bytes>self.SERIALIZED_BYTES_CAP:raise BudgetStop('serialized_projection_500MiB')
    def as_dict(self):return {'limits':self.limits,'nodes':self.nodes,'getter_rpc':self.rpc,'native_order_pages':self.pages,'serialized_projection_bytes_observed':self.projected_bytes,'serialized_projection_bytes_cap':self.SERIALIZED_BYTES_CAP,'elapsed_seconds':time.monotonic()-self.started,'hard_native_cancellation':False,'rss_or_native_allocation_hard_cap':False}

def semantic_metadata():
    return {
        'Model':[('getComsolVersion',{STRING}),('getUsedProducts',{'java.lang.String[]'})],
        'GeomSequence':[('getSDim',{'int'}),('lengthUnit',{STRING}),('isAxisymmetric',{'boolean'}),('getNEntities',{'int[]'}),('getBoundingBox',{'double[]'}),('angularUnit',{STRING}),('current',{STRING}),('objectNames',{'java.lang.String[]'}),('problems',{'java.lang.String[]'})],
        'GeomFeature':[('objectNames',{'java.lang.String[]'})],
        'MeshSequence':[('geom',{STRING}),('getSDim',{'int'}),('isComplete',{'boolean'}),('isEmpty',{'boolean'}),('isAutomatic',{'boolean'}),('isGeometry',{'boolean'}),('current',{STRING}),('getNumElem',{'int'}),('getNumVertex',{'int'}),('getTypes',{'java.lang.String[]'}),('problems',{'java.lang.String[]'})],
        'Study':[(x,{'long'} if x in {'getLastComputationDate','getLastComputationTime'} else {STRING} if x=='getLastComputationVersion' else {'boolean'}) for x in ('getLastComputationDate','getLastComputationTime','getLastComputationVersion','isGenConv','isGenIntermediatePlots','isGenPlots','isStoreSolution','isPlotUndefVals','isStoreCompleteHistory')],
        'SolverSequence':[('study',{STRING}),('getType',{STRING}),('getSequenceType',{STRING}),('isEmpty',{'boolean'}),('isInitialized',{'boolean'}),('isAttached',{'boolean'}),('getDefaultSolnum',{'int'}),('getPNames',{'java.lang.String[]'}),('getParamNames',{'java.lang.String[]'}),('getParamVals',{'double[]'})],
        'SolverFeature':[('hasError',{'boolean'}),('hasWarning',{'boolean'})],
        'SolutionInfo':[('getOuterSolnum',{'int[]'}),('getLevelNames',{'java.lang.String[]'})],
    }

class Reader:
    def __init__(self,worker,tag,scope,budget,page_size,role,*,stop_on_unknown=False):
        self.worker=worker;self.tag=tag;self.scope=scope;self.budget=budget;self.page_size=page_size;self.role=role
        self.stop_on_unknown=stop_on_unknown
        self.nodes={};self.coverage=[];self.errors=[];self.descriptors={};self.unknown=False;self.root_tag=None;self.receiver_receipts=[];self.receivers=[];self.receipt_keys=set()
    def coverage_row(self,path,field,status,signature=None,reason=None):
        self.coverage.append({'side':self.role,'path':path,'field':field,'status':status,'getter':signature,'reason':reason})
    def error(self,path,field,exc,*,dispatched=False,status='UNREADABLE'):
        code=getattr(exc,'code','GETTER_FAILED')
        self.coverage_row(path,field,status,reason=str(exc))
        self.errors.append({'side':self.role,'stage':'projection','path':path,'field':field,'code':code,'message':str(exc),'cause_type':type(exc).__name__,'dispatched':dispatched,'mutation_possible':False})
        if code in {'EXECUTION_STATE_UNKNOWN','ENGINE_UNRESPONSIVE','ENGINE_TIMEOUT','STALE_WORKER_HANDLE'}:self.unknown=True
    def descriptor(self,node,path):
        key=id(node)
        if key not in self.descriptors:
            self.budget.take();desc=describe_receiver(self.worker,node)
            if desc['runtime_version']!=VERSION:fail('API_UNSUPPORTED','receiver runtime has no reviewed6.4 projection adapter')
            if not desc['interfaces'] or not any(x.startswith('com.comsol.model.') for x in desc['interfaces']):fail('API_UNSUPPORTED','not an actual public COMSOL typed receiver')
            self.descriptors[key]=desc
            self.receivers.append(node)  # Do not reuse a discarded proxy's id.
        if (key,path_key(path)) not in self.receipt_keys:
            self.receiver_receipts.append({'path':path,'descriptor':self.descriptors[key]});self.receipt_keys.add((key,path_key(path)))
        return self.descriptors[key]
    def methods(self,desc,name,params):
        return [m for m in desc['methods'] if m['method']==name and m['parameters']==list(params)
                and m['interface'] in desc['interfaces'] and m['interface'].startswith('com.comsol.model.')]
    def get(self,node,desc,path,field,name,params=(),args=(),returns=None,*,nullable=False):
        rows=self.methods(desc,name,params)
        if len({java_name(r['returns']) for r in rows})>1:
            self.error(path,field,ExecutionContractError('API_UNSUPPORTED','ambiguous public return signature'),status='UNSUPPORTED');return _MISSING
        if returns is not None:rows=[r for r in rows if java_name(r['returns']) in returns]
        if not rows:
            self.error(path,field,ExecutionContractError('API_UNSUPPORTED','no exact reviewed public '+name+str(params)),status='UNSUPPORTED');return _MISSING
        if len({java_name(r['returns']) for r in rows})!=1:
            self.error(path,field,ExecutionContractError('API_UNSUPPORTED','ambiguous public return signature'),status='UNSUPPORTED');return _MISSING
        signature={'interface':rows[0]['interface'],'method':name,'parameters':list(params),'returns':rows[0]['returns'],'runtime_version':VERSION}
        try:
            self.budget.take();value=getattr(node,name)(*args)
            if value is None and not nullable:
                self.error(path,field,ExecutionContractError('NULL_GETTER','actual getter returned null'),dispatched=True);return {'kind':'null','java_type':rows[0]['returns']}
            self.coverage_row(path,field,'VERIFIED',signature);return value
        except BudgetStop:raise
        except Exception as exc:
            self.error(path,field,exc,dispatched=True)
            if self.stop_on_unknown and self.unknown:raise UnknownObservationStop() from exc
            return _MISSING
    def scalar(self,node,desc,path,field,name,params=(),args=(),returns=None):
        value=self.get(node,desc,path,field,name,params,args,returns)
        if value is _MISSING:return _MISSING
        if isinstance(value,Mapping) and value.get('kind')=='null':return value
        row=self.methods(desc,name,params)
        if returns is not None:row=[r for r in row if java_name(r['returns']) in returns]
        result=java_name(row[0]['returns'])
        spec={'boolean':('boolean',0),'int':('int32',0),'long':('int64',0),'double':('float64',0),STRING:('string',0),
              'boolean[]':('boolean',1),'int[]':('int32',1),'double[]':('float64',1),'java.lang.String[]':('string',1)}.get(result)
        try:
            if spec is None:fail('API_UNSUPPORTED','non-value return not a semantic scalar')
            typed=typed_value_from_engine(value,kind=spec[0]);validate_typed_value(typed)
            if len(typed['shape'])!=spec[1]:fail('PROPERTY_TYPE_MISMATCH','actual public getter rank/type differs')
            return {'kind':'value','java_type':row[0]['returns'],'value':typed}
        except Exception as exc:self.error(path,field,exc,dispatched=True);return _MISSING
    def record(self,path,field,value):
        if value is _MISSING:return
        try:canonical(value)
        except Exception as exc:self.error(path,field,exc,dispatched=True);return
        self.budget.record_bytes(value)
        self.nodes.setdefault(path_key(path),{'path':path,'fields':{}})['fields'][field]=value
    def role_has(self,desc,name):return 'com.comsol.model.'+name in desc['interfaces']
    def property(self,node,desc,path,name):
        field='property:'+name
        value_type=self.get(node,desc,path,field+'.value_type','getValueType',(STRING,),(name,),{STRING})
        allowed=self.get(node,desc,path,field+'.allowed_values','getAllowedPropertyValues',(STRING,),(name,),{'java.lang.String[]'})
        spec=engine_value_spec(value_type)
        payload={'value_type':None if value_type is _MISSING else value_type,'allowed_values':None if allowed is _MISSING else allowed}
        if spec is None:
            self.error(path,field,ExecutionContractError('API_UNSUPPORTED','authoritative PropFeature value metadata unsupported'),status='UNSUPPORTED')
            self.record(path,field,payload);return
        raw=self.get(node,desc,path,field+'.value',spec['getter'],(STRING,),(name,),{spec['java_signature']})
        if raw is not _MISSING:
            try:
                if isinstance(raw,Mapping) and raw.get('kind')=='null':payload['value']=raw
                else:
                    typed=typed_value_from_engine(raw,kind=spec['kind']);validate_typed_value(typed)
                    if len(typed['shape'])!=spec['rank']:fail('PROPERTY_TYPE_MISMATCH','actual array shape does not establish declared rank')
                    typed['java_signature']=spec['java_signature'];payload['value']=typed
                    if isinstance(allowed,(list,tuple)) and allowed and all(isinstance(x,str) for x in allowed):
                        validate_typed_value(typed,expected={'kind':spec['kind'],'shape_rank':spec['rank'],'java_signature':spec['java_signature'],'allowed_values':allowed})
            except Exception as exc:self.error(path,field+'.shape',exc,dispatched=True);payload['raw_value']=raw
        if allowed is not _MISSING and not isinstance(allowed,Mapping) and (not isinstance(allowed,(list,tuple)) or not all(isinstance(x,str) for x in allowed)):
            self.error(path,field+'.range',ExecutionContractError('PROPERTY_TYPE_MISMATCH','allowed-values public String[] type mismatch'),dispatched=True)
        # PropFeature has no verified generic getUnit(String). Boolean/string
        # transport values have no numeric unit; numeric property units remain
        # an explicit coverage gap, never guessed from current/model revision.
        if spec['kind'] in {'boolean','string'}:
            payload['unit']=None;self.coverage_row(path,field+'.unit','NOT_APPLICABLE',reason='boolean/textual PropFeature transport; no numeric unit asserted')
        else:
            payload['unit']=None;self.error(path,field+'.unit',ExecutionContractError('API_UNSUPPORTED','no verified generic numeric PropFeature unit getter'),status='UNSUPPORTED')
        self.record(path,field,payload)
    def metadata(self,node,desc,path,*,root=False):
        entity=self.role_has(desc,'ModelEntity');prop=self.role_has(desc,'PropFeature')
        self.record(path,'public_interfaces',sorted(desc['interfaces']))
        self.coverage_row(path,'public_interfaces','VERIFIED',{'method':'describe_public','runtime_version':VERSION})
        for field,name,returns in [('label','label',{STRING}),('active','isActive',{'boolean'}),('comments','comments',{STRING})]:
            if entity:self.record(path,field,self.scalar(node,desc,path,field,name,returns=returns))
            else:self.coverage_row(path,field,'NOT_APPLICABLE',reason='actual public receiver is not ModelEntity')
        if entity:
            tag=self.scalar(node,desc,path,'tag','tag',returns={STRING})
            if root:self.root_tag=tag
            else:self.record(path,'tag',tag)
            expected=self.tag if root else path['segments'][-1].get('tag') if path['segments'] else self.tag
            if expected is not None and tag is not _MISSING and tag.get('value',{}).get('data')!=expected:
                self.error(path,'node_identity',ExecutionContractError('MODEL_IDENTITY_MISMATCH','actual public tag differs from exact selected path'),dispatched=True)
                self.unknown=True
        if prop or self.methods(desc,'getType',()):self.record(path,'type',self.scalar(node,desc,path,'type','getType',returns={STRING}))
        else:self.coverage_row(path,'type','NOT_APPLICABLE',reason='actual public receiver has no feature-type getter; its public typed interfaces are retained')
        if prop:
            names=self.get(node,desc,path,'properties','properties',returns={'java.lang.String[]'})
            if isinstance(names,(list,tuple)) and all(isinstance(n,str) and n for n in names) and len(names)==len(set(names)):
                self.record(path,'property_names',list(names))
                for name in names:self.property(node,desc,path,name)
            elif names is not _MISSING:self.error(path,'properties',ExecutionContractError('PROPERTY_TYPE_MISMATCH','invalid actual property enumeration'),dispatched=True)
        else:self.coverage_row(path,'properties','NOT_APPLICABLE',reason='actual public receiver is not PropFeature')
        if self.role_has(desc,'ExpressionBase'):
            names=self.get(node,desc,path,'expressions','varnames',returns={'java.lang.String[]'})
            if isinstance(names,(list,tuple)) and all(isinstance(n,str) and n for n in names) and len(names)==len(set(names)):
                self.record(path,'expression_order',list(names))
                for name in names:
                    value=self.scalar(node,desc,path,'expression:'+name,'get',(STRING,),(name,),{STRING})
                    descr=self.scalar(node,desc,path,'expression:'+name+'.description','descr',(STRING,),(name,),{STRING})
                    # ParamBase.evaluateUnit returns unit metadata without
                    # evaluating the expression to a floating point value.
                    if self.role_has(desc,'ParamBase'):unit=self.scalar(node,desc,path,'expression:'+name+'.unit','evaluateUnit',(STRING,),(name,),{STRING})
                    else:
                        unit=_MISSING;self.error(path,'expression:'+name+'.unit',ExecutionContractError('API_UNSUPPORTED','ExpressionBase has no verified local unit getter'),status='UNSUPPORTED')
                    for field,item in [('expression:'+name,value),('expression:'+name+'.description',descr),('expression:'+name+'.unit',unit)]:self.record(path,field,item)
            elif names is not _MISSING:self.error(path,'expressions',ExecutionContractError('PROPERTY_TYPE_MISMATCH','invalid varnames() type'),dispatched=True)
        domain=semantic_metadata()
        for interface,methods in domain.items():
            if self.role_has(desc,interface):
                for name,returns in methods:self.record(path,'metadata:'+name,self.scalar(node,desc,path,'metadata:'+name,name,returns=returns))
        if self.role_has(desc,'Model'):
            # Model.getComsolVersion is the version used to save the model,
            # not ModelUtil's current runtime version. Keep it as semantic
            # metadata; runtime admission comes from actual describe_public.
            products=self.nodes.get(path_key(path),{}).get('fields',{}).get('metadata:getUsedProducts',{}).get('value',{}).get('data')
            if products:self.error(path,'product_state',ExecutionContractError('API_UNSUPPORTED','used product names observed; product-specific hidden model state has no reviewed getter adapter'),status='UNSUPPORTED')
        if self.role_has(desc,'GeomFeature') or self.role_has(desc,'MeshFeature'):
            self.error(path,'metadata:build_status',ExecutionContractError('API_UNSUPPORTED','existing inspectors have no verified transported complete feature build-status/problem adapter'),status='UNSUPPORTED')
        if self.role_has(desc,'Study'):self.record(path,'metadata:getSolverSequences',self.scalar(node,desc,path,'metadata:getSolverSequences','getSolverSequences',(STRING,),('SolverSequence',),{'java.lang.String[]'}))
        if self.role_has(desc,'Selection') or self.role_has(desc,'LocalSelection') or self.role_has(desc,'GeomObjectSelection'):
            for name,returns in [('named',{STRING}),('isInheriting',{'boolean'}),('entities',{'int[]'}),('dimension',{'int[]'}),('geom',{STRING})]:
                self.record(path,'selection:'+name,self.scalar(node,desc,path,'selection:'+name,name,returns=returns))
            if self.role_has(desc,'GeomObjectSelection'):
                tags=self.get(node,desc,path,'selection:object_tags','objects',returns={'java.lang.String[]'})
                if isinstance(tags,(list,tuple)) and all(isinstance(t,str) and t for t in tags):
                    self.record(path,'selection:object_tags',list(tags))
                    for tag in tags:self.record(path,'selection:object_entities:'+tag,self.scalar(node,desc,path,'selection:object_entities:'+tag,'entities',(STRING,),(tag,),{'int[]'}))
                elif tags is not _MISSING:self.error(path,'selection:object_tags',ExecutionContractError('PROPERTY_TYPE_MISMATCH','objects() must return actual String[] object tags'),dispatched=True)
            self.error(path,'selection:geometry_revision',ExecutionContractError('API_UNSUPPORTED','no verified geometry revision getter; model revision is not geometry revision'),status='UNSUPPORTED')
        if self.role_has(desc,'Model'):
            # Existing Worker fixed adapter is strictly Model.file().tags(),
            # not a generic Java/FileResourceList escape hatch.
            rows=self.methods(desc,'file',())
            if any(r['returns']=='com.comsol.model.FileResourceList' for r in rows):
                try:
                    self.budget.take();tags=getattr(node,'getFileResourceTags')()
                    if not isinstance(tags,(list,tuple)) or not all(isinstance(t,str) and t for t in tags):fail('PROPERTY_TYPE_MISMATCH','file resource tags are not String[]')
                    self.record(path,'file_resource_tags',list(tags));self.coverage_row(path,'file_resource_tags','VERIFIED',{'method':'Model.file().tags() via existing getFileResourceTags'})
                    if tags:self.error(path,'file_resource_metadata',ExecutionContractError('API_UNSUPPORTED','existing Worker exposes tags only; resource references/content metadata incomplete'),status='UNSUPPORTED')
                except BudgetStop:raise
                except Exception as exc:self.error(path,'file_resource_tags',exc,dispatched=True)
            else:self.error(path,'file_resources',ExecutionContractError('API_UNSUPPORTED','Model file-resource getter not verified'),status='UNSUPPORTED')
    def resolve(self,model,path):
        node=model;prefix={'segments':[]}
        for segment in NodePath.from_wire(path).as_dict()['segments']:
            desc=self.descriptor(node,prefix);method=segment.get('accessor') or segment['collection'];args=() if 'accessor' in segment else (segment['tag'],)
            params=() if not args else (STRING,)
            value=self.get(node,desc,prefix,'resolve:'+method,method,params,args,nullable=True)
            if value is _MISSING or value is None:fail('NODE_NOT_FOUND','requested exact typed path unavailable')
            returned={m['returns'] for m in self.methods(desc,method,params) if m['returns'].startswith('com.comsol.model.')}
            actual=self.descriptor(value,{'segments':prefix['segments']+[segment]})
            if not returned.intersection(actual['interfaces']):fail('API_UNSUPPORTED','typed path actual receiver differs from exact public accessor return')
            node=value;prefix={'segments':prefix['segments']+[segment]}
        return node
    def walk(self,node,path,ancestors=(),root=False):
        self.budget.take(node=True)
        marker=getattr(node,'_handle',None) or id(node)
        if marker in ancestors:self.error(path,'subtree',ExecutionContractError('API_UNSUPPORTED','typed accessor cycle cannot be fully projected'),status='UNSUPPORTED');return
        desc=self.descriptor(node,path);self.metadata(node,desc,path,root=root)
        for method in sorted(ACCESSOR_METHODS):
            # active is a setter overload, never an accessor read.
            if method=='active':continue
            rows=self.methods(desc,method,())
            typed=[r for r in rows if r['returns'].startswith('com.comsol.model.')]
            if not typed:
                if any(r['returns'] in {STRING,'int','boolean'} for r in rows):continue
                if self.methods(desc,method,(STRING,)) and method=='selection':self.error(path,'collection:selection_names',ExecutionContractError('API_UNSUPPORTED','no verified enumeration of named input selections'),status='UNSUPPORTED')
                elif self.role_has(desc,'Model') and method in {'component','param','func','study','sol','result'}:
                    self.error(path,'collection:'+method,ExecutionContractError('API_UNSUPPORTED','required full Model domain accessor not exposed by actual receiver'),status='UNSUPPORTED')
                elif self.role_has(desc,'ModelParam') and method=='group':
                    self.error(path,'collection:group',ExecutionContractError('API_UNSUPPORTED','actual ModelParam group enumeration unavailable'),status='UNSUPPORTED')
                else:self.coverage_row(path,'collection:'+method,'NOT_APPLICABLE',reason='actual public descriptor has no zero-arg typed collection/accessor')
                continue
            child=self.get(node,desc,path,'collection:'+method,method,returns={r['returns'] for r in typed},nullable=True)
            if child is _MISSING:continue
            if child is None:self.error(path,'collection:'+method,ExecutionContractError('NULL_GETTER','typed accessor returned null'),dispatched=True);continue
            child_path={'segments':path['segments']+[{'accessor':method}]};child_desc=self.descriptor(child,child_path)
            if not {r['returns'] for r in typed}.intersection(child_desc['interfaces']):
                self.error(child_path,'receiver',ExecutionContractError('API_UNSUPPORTED','actual accessor receiver differs from public return interface'),status='UNSUPPORTED');continue
            if self.role_has(child_desc,'ModelEntityList'):
                tags=self.get(child,child_desc,path,'native_order:'+method,'tags',returns={'java.lang.String[]'})
                if not isinstance(tags,(list,tuple)) or not all(isinstance(t,str) and t for t in tags) or len(tags)!=len(set(tags)):
                    if tags is not _MISSING:self.error(path,'native_order:'+method,ExecutionContractError('PROPERTY_TYPE_MISMATCH','invalid actual native list tags'),dispatched=True)
                    continue
                self.record(path,'native_order:'+method,list(tags))
                for start in range(0,len(tags),self.page_size):
                    self.budget.pages+=1
                    for tag in tags[start:start+self.page_size]:
                        entity=self.get(child,child_desc,child_path,'member:'+tag,'get',(STRING,),(tag,),nullable=True)
                        target={'segments':path['segments']+[{'collection':method,'tag':tag}]}
                        if entity is None:self.error(target,'node',ExecutionContractError('NULL_GETTER','typed member getter returned null'),dispatched=True)
                        elif entity is not _MISSING:
                            actual=self.descriptor(entity,target)
                            returned={m['returns'] for m in self.methods(child_desc,'get',(STRING,))}
                            if not returned.intersection(actual['interfaces']):self.error(target,'receiver',ExecutionContractError('API_UNSUPPORTED','actual member receiver differs from typed list return'),status='UNSUPPORTED')
                            else:self.walk(entity,target,ancestors+(marker,))
            else:self.walk(child,child_path,ancestors+(marker,))
        if self.role_has(desc,'SolverSequence'):
            rows=self.methods(desc,'getSolutioninfo',())
            if rows:
                info=self.get(node,desc,path,'solution_metadata','getSolutioninfo',returns={'com.comsol.model.SolutionInfo'},nullable=True)
                if info is not _MISSING and info is not None:
                    info_desc=self.descriptor(info,path)
                    if not self.role_has(info_desc,'SolutionInfo'):
                        self.error(path,'solution_metadata',ExecutionContractError('API_UNSUPPORTED','actual receiver is not SolutionInfo'),status='UNSUPPORTED')
                    else:
                        for name,returns in [('getOuterSolnum',{'int[]'}),('getLevelNames',{'java.lang.String[]'}),('getPNamesOuter',{'java.lang.String[]'}),('getPUnitsOuter',{'java.lang.String[]'})]:
                            self.record(path,'solution_info:'+name,self.scalar(info,info_desc,path,'solution_info:'+name,name,returns=returns))
                        self.error(path,'solution_info:parameter_units',ExecutionContractError('API_UNSUPPORTED','SolutionInfo getPNames/getUnits require verified solnums matrix; no guessed zero-arg overload'),status='UNSUPPORTED')
                elif info is None:self.error(path,'solution_metadata',ExecutionContractError('NULL_GETTER','solution metadata unavailable'),dispatched=True)
            else:self.error(path,'solution_metadata',ExecutionContractError('API_UNSUPPORTED','no verified SolutionInfo metadata getter'),status='UNSUPPORTED')
        # Public reflection is transport-filtered. A zero-argument typed API
        # outside the reviewed traversal cannot silently become empty coverage.
        for method in sorted({m['method'] for m in desc['methods'] if not m['parameters'] and m['returns'].startswith('com.comsol.model.')} - ACCESSOR_METHODS - {'file','getSolutioninfo'}):
            self.error(path,'unprojected_accessor:'+method,ExecutionContractError('API_UNSUPPORTED','typed public domain has no reviewed pure projection adapter'),status='UNSUPPORTED')
    def project(self):
        try:
            self.budget.take();model=self.worker.client().model(self.tag)
            if self.scope['mode']=='model':self.walk(model,{'segments':[]},root=True)
            else:
                for path in self.scope['paths']:self.walk(self.resolve(model,path),path,root=not path['segments'])
                for p in self.scope['properties']:
                    self.budget.take(node=True);node=self.resolve(model,p['path']);desc=self.descriptor(node,p['path'])
                    for name in p['names']:self.property(node,desc,p['path'],name)
        except UnknownObservationStop:
            pass
        except BudgetStop as exc:self.error({'segments':[]},'requested_scope',exc,status='TRUNCATED')
        except Exception as exc:self.error({'segments':[]},'requested_scope',exc)
        return {'schema':SCHEMA,'runtime_version':VERSION,'normalized_scope':self.scope,'nodes':[self.nodes[k] for k in sorted(self.nodes)],
                'coverage':self.coverage,'errors':self.errors,'complete':all(c['status'] in {'VERIFIED','NOT_APPLICABLE'} for c in self.coverage),
                'unknown':self.unknown,'root_tag_provenance':self.root_tag,'receiver_receipts':self.receiver_receipts,
                'producer':SCHEMA,'coverage_contract':[{'path':c['path'],'field':c['field']} for c in self.coverage]}

def compare(left,right):
    def fields(projection):return {(path_key(n['path']),f):(n['path'],v) for n in projection['nodes'] for f,v in n['fields'].items()}
    l,r=fields(left),fields(right);changes=[]
    for key in sorted(set(l)|set(r)):
        # A failed/unobserved getter is not a semantic deletion. Added/removed
        # fields require complete opposite-side coverage; observed native list
        # order and property-name changes still appear in partial results.
        if key not in l:
            if left['complete']:changes.append({'path':r[key][0],'field':key[1],'change':'added','after':r[key][1]})
        elif key not in r:
            if right['complete']:changes.append({'path':l[key][0],'field':key[1],'change':'removed','before':l[key][1]})
        elif canonical(l[key][1])!=canonical(r[key][1]):changes.append({'path':l[key][0],'field':key[1],'change':'changed','before':l[key][1],'after':r[key][1]})
    return changes

def checkpoint(backend,selector,project_id,rows):
    matches=[r for r in rows if r.get('checkpoint_id')==selector]
    if not matches:fail('NODE_NOT_FOUND','exact checkpoint ID absent (hash/label aliases are not accepted)')
    if len({canonical(r) for r in matches})!=1:fail('CHECKPOINT_CONFLICT','checkpoint ID has inconsistent authoritative records')
    row=dict(matches[0]);binding=row.get('source_binding')
    if row.get('project_id')!=project_id or not isinstance(binding,Mapping):fail('PROJECT_IDENTITY_MISMATCH','checkpoint project/source binding not authoritative')
    if not isinstance(binding.get('model_ref'),Mapping):fail('MODEL_IDENTITY_MISMATCH','checkpoint source ModelRef object absent')
    source_ref=model_ref_from_mapping(binding.get('model_ref'))
    if binding['model_ref']!=source_ref.as_dict() or any(type(binding['model_ref'].get(k)) is not type(v) for k,v in source_ref.as_dict().items()) or type(binding.get('revision')) is not int or binding['revision']<0:fail('MODEL_IDENTITY_MISMATCH','checkpoint exact source ref/revision absent')
    if row.get('source_model_ref',source_ref.as_dict())!=source_ref.as_dict() or row.get('source_revision',binding['revision'])!=binding['revision']:fail('MODEL_IDENTITY_MISMATCH','checkpoint lineage fields disagree')
    sha=row.get('sha256');raw=row.get('path')
    if not isinstance(sha,str) or len(sha)!=64 or any(c not in '0123456789abcdef' for c in sha) or not isinstance(raw,str) or not raw:fail('ARTIFACT_MISSING','checkpoint path/hash absent')
    p=Path(raw)
    if p.is_symlink():fail('ARTIFACT_MISSING','checkpoint symlink refused')
    p=canonical_project_path(backend.project_root,p)
    if not p.is_file() or not stat.S_ISREG(p.lstat().st_mode) or hashlib.sha256(p.read_bytes()).hexdigest()!=sha:fail('ARTIFACT_MISSING','checkpoint bytes not authoritative')
    return row,p

class _RetainedView:
    """Pure memory view for independently replaying Reader's obligations.

    Only retained descriptor/semantic fields are visible. There is no Worker,
    engine/current-model fallback, filesystem operation or mutation method.
    Reader, not a cache status/reason, decides applicability and pure getters.
    """
    def __init__(self,value):
        self.value=value;self.nodes={};self.descriptors={}
        for node in value['nodes']:
            path=NodePath.from_wire(node['path']).as_dict();key=path_key(path)
            if key in self.nodes or not isinstance(node['fields'],dict):fail('PROJECTION_INCOMPATIBLE','duplicate/malformed historical node')
            self.nodes[key]=node['fields']
        for receipt in value['receiver_receipts']:
            path=NodePath.from_wire(receipt['path']).as_dict();key=path_key(path);desc=receipt['descriptor']
            if desc.get('runtime_version')!=VERSION or not desc.get('interfaces') or not isinstance(desc.get('methods'),list):
                fail('PROJECTION_INCOMPATIBLE','historical public receiver descriptor absent')
            if not all(isinstance(x,str) and x.startswith('com.comsol.model.') for x in desc['interfaces']):
                fail('PROJECTION_INCOMPATIBLE','historical receiver is not a public COMSOL interface')
            bucket=self.descriptors.setdefault(key,[])
            if any(canonical(d)==canonical(desc) for d in bucket):fail('PROJECTION_INCOMPATIBLE','duplicate historical receiver receipt')
            bucket.append(desc)
        self.tag=self.raw(value.get('root_tag_provenance')) if value.get('root_tag_provenance') is not None else '_retained_root'
        if not isinstance(self.tag,str):fail('PROJECTION_INCOMPATIBLE','historical root tag provenance type differs')

    @staticmethod
    def raw(value):
        if isinstance(value,Mapping) and value.get('kind')=='value':
            validate_typed_value(value['value']);return value['value']['data']
        if isinstance(value,Mapping) and {'kind','shape','data'}<=set(value):
            validate_typed_value(value);return value['data']
        if isinstance(value,Mapping) and value.get('kind')=='null':
            fail('PROJECTION_INCOMPATIBLE','null is not complete getter evidence')
        return value

    def node(self,path,interface=None,list_context=None,solution=False):
        key=path_key(path);candidates=self.descriptors.get(key,[])
        if interface is not None:candidates=[d for d in candidates if interface in d['interfaces']]
        elif 'public_interfaces' in self.nodes.get(key,{}):
            actual=self.nodes[key]['public_interfaces'];candidates=[d for d in candidates if sorted(d['interfaces'])==actual]
        if len(candidates)!=1:fail('PROJECTION_INCOMPATIBLE','historical path has absent/ambiguous typed receiver: '+key)
        return _RetainedNode(self,path,candidates[0],list_context,solution)

    def client(self):return self
    def model(self,tag):
        if tag!=self.tag:fail('PROJECTION_INCOMPATIBLE','historical root identity mismatch')
        return self.node({'segments':[]})
    def describe_public(self,node):return node.descriptor

    def field(self,node,name):
        fields=self.nodes.get(path_key(node.path),{})
        if name not in fields:fail('PROJECTION_INCOMPATIBLE','missing historical getter payload '+name+' at '+path_key(node.path))
        return fields[name]

    def read(self,node,name,args):
        roles=node.descriptor['interfaces']
        if name=='getFileResourceTags':return self.field(node,'file_resource_tags')
        if 'com.comsol.model.ModelEntityList' in roles:
            parent,collection=node.list_context
            if name=='tags':return self.nodes[path_key(parent)]['native_order:'+collection]
            if name=='get' and len(args)==1:
                return self.node({'segments':parent['segments']+[{'collection':collection,'tag':args[0]}]})
        if name in ACCESSOR_METHODS and name!='active':
            rows=[r for r in node.descriptor['methods'] if r['method']==name and len(r['parameters'])==len(args)
                  and r['returns'].startswith('com.comsol.model.')]
            if rows:
                returns={r['returns'] for r in rows}
                if len(returns)!=1:fail('PROJECTION_INCOMPATIBLE','ambiguous historical accessor return')
                if args:
                    if len(args)!=1 or not isinstance(args[0],str):fail('PROJECTION_INCOMPATIBLE','unsupported historical accessor arguments')
                    child={'segments':node.path['segments']+[{'collection':name,'tag':args[0]}]}
                    return self.node(child,interface=next(iter(returns)))
                child={'segments':node.path['segments']+[{'accessor':name}]}
                target=self.node(child,interface=next(iter(returns)),list_context=(node.path,name))
                return target
        if name=='getSolutioninfo' and not args:
            return self.node(node.path,interface='com.comsol.model.SolutionInfo',solution=True)
        if node.solution:return self.raw(self.field(node,'solution_info:'+name))
        if 'com.comsol.model.PropFeature' in roles and args:
            prop=self.field(node,'property:'+args[0]);spec=engine_value_spec(prop['value_type'])
            if name=='getValueType':return prop['value_type']
            if name=='getAllowedPropertyValues':return prop['allowed_values']
            if spec and name==spec['getter']:return self.raw(prop['value'])
        if 'com.comsol.model.ExpressionBase' in roles:
            if name=='varnames':return self.field(node,'expression_order')
            if name in {'get','descr','evaluateUnit'} and len(args)==1:
                suffix={'get':'','descr':'.description','evaluateUnit':'.unit'}[name]
                return self.raw(self.field(node,'expression:'+args[0]+suffix))
        if name=='properties':return self.field(node,'property_names')
        if name in {'tag','label','isActive','comments','getType'} and not args:
            field={'isActive':'active','getType':'type'}.get(name,name)
            if name=='tag' and not node.path['segments']:return self.raw(self.value['root_tag_provenance'])
            return self.raw(self.field(node,field))
        if any('com.comsol.model.'+role in roles for role in ('Selection','LocalSelection','GeomObjectSelection')):
            if name=='objects':return self.field(node,'selection:object_tags')
            if name=='entities' and args:return self.raw(self.field(node,'selection:object_entities:'+args[0]))
            if name in {'named','isInheriting','entities','dimension','geom'}:
                return self.raw(self.field(node,'selection:'+name))
        return self.raw(self.field(node,'metadata:'+name))


class _RetainedNode:
    def __init__(self,view,path,descriptor,list_context=None,solution=False):
        self.view=view;self.path=path;self.descriptor=descriptor;self.list_context=list_context;self.solution=solution
        self._handle=(path_key(path),canonical(descriptor))
    def __getattr__(self,name):
        # Reader's fixed pure getter path is the only caller. This cannot
        # dispatch an engine method or import a class named by cached metadata.
        return lambda *args:self.view.read(self,name,args)


def _validate_retained_coverage(value,scope):
    """Reconstruct obligations without using cached coverage assertions."""
    view=_RetainedView(value)
    # This validation performs zero RPC. Reader's existing engineering bounds
    # also bound the in-memory replay; a truncated replay cannot claim equality.
    limits=_parse_search_budget({'max_nodes':100000,'max_seconds':20,'max_rpc':1000000})
    expected=Reader(view,view.tag,scope,Budget(limits),100,'retained_validation').project()
    if not expected['complete'] or expected['unknown']:
        details=[{'path':c['path'],'field':c['field'],'status':c['status'],'reason':c['reason']}
                 for c in expected['coverage'] if c['status'] not in {'VERIFIED','NOT_APPLICABLE'}]
        fail('PROJECTION_INCOMPATIBLE','historical applicable getter obligations incomplete: '+canonical(details))
    def coverage(rows):
        result={}
        for c in rows:
            key=(path_key(NodePath.from_wire(c['path']).as_dict()),c['field'])
            if key in result:fail('PROJECTION_INCOMPATIBLE','duplicate historical coverage key '+str(key))
            result[key]={'status':c['status'],'getter':c.get('getter')}
        return result
    if coverage(value['coverage'])!=coverage(expected['coverage']):
        fail('PROJECTION_INCOMPATIBLE','historical coverage rows/status/public getter facts differ from independently required scope')
    actual_nodes={path_key(n['path']):n['fields'] for n in value['nodes']}
    replayed_nodes={path_key(n['path']):n['fields'] for n in expected['nodes']}
    if canonical(actual_nodes)!=canonical(replayed_nodes):
        fail('PROJECTION_INCOMPATIBLE','historical typed payloads differ from required getter projection')
    receipts=lambda rows:{canonical({'path':r['path'],'descriptor':r['descriptor']}) for r in rows}
    if receipts(value['receiver_receipts'])!=receipts(expected['receiver_receipts']):
        fail('PROJECTION_INCOMPATIBLE','historical receiver receipts differ from requested traversal')
    contract=[{'path':c['path'],'field':c['field']} for c in value['coverage']]
    if value.get('coverage_contract')!=contract:fail('PROJECTION_INCOMPATIBLE','historical self-contract inconsistent')
    canonical(value)


def cached_projection(row,scope,role):
    value=row.get('semantic_projection')
    if value is None:return None
    good=(isinstance(value,Mapping) and value.get('schema')==SCHEMA and value.get('runtime_version')==VERSION
          and value.get('source_sha256')==row['sha256'] and value.get('source_binding')==row['source_binding']
          and value.get('source_version')==row.get('runtime_version')==VERSION and value.get('normalized_scope')==scope
          and value.get('producer')==SCHEMA and value.get('complete') is True and value.get('unknown') is False and isinstance(value.get('nodes'),list) and value['nodes']
          and isinstance(value.get('coverage'),list) and value['coverage'] and not value.get('errors')
          and all(isinstance(c,Mapping) and c.get('status') in {'VERIFIED','NOT_APPLICABLE'} for c in value['coverage']))
    diagnostic='historical schema/source/version/scope/coverage incompatible'
    if good:
        try:_validate_retained_coverage(value,scope)
        except Exception as exc:
            good=False;diagnostic=str(exc) or type(exc).__name__
    if good:return {**dict(value),'unknown':False,'coverage':[{**c,'side':role} for c in value['coverage']]}
    return {'schema':SCHEMA,'runtime_version':VERSION,'normalized_scope':scope,'nodes':[],'complete':False,'unknown':False,
            'coverage':[{'side':role,'path':{'segments':[]},'field':'retained_projection','status':'UNSUPPORTED','getter':None,'reason':diagnostic}],
            'errors':[{'side':role,'stage':'retained_projection','path':{'segments':[]},'code':'PROJECTION_INCOMPATIBLE','message':'retained projection is not authoritative for requested coverage: '+diagnostic,'dispatched':False,'mutation_possible':False}]}

def run_diff(backend,ref,body,execution,selected,isolation):
    saved=pointer();identity=source_identity(backend,ref);snapshot=backend.worker.backend_snapshot(ref.model_tag)
    budget=Budget(body['budget']);copies=[];cleanup={'complete':True,'errors':[]};projections={};unknown=False;cause=[];input_observations=[];lifecycle_calls=[]
    originals={role:(path.lstat().st_dev,path.lstat().st_ino) for role,(row,path) in selected.items()}
    def client_call(method,*args,**kwargs):
        lifecycle_calls.append({'method':method,'arguments':[str(a) for a in args],'keywords':kwargs})
        return getattr(backend.worker.client(),method)(*args,**kwargs)
    def project(tag,role):return Reader(backend.worker,tag,body['scope'],budget,body['page_size'],role).project()
    before=project(ref.model_tag,'source_before')
    try:
        for role in ('left','right'):
            if body[role]=='current':projections[role]=project(ref.model_tag,role);continue
            row,path=selected[role];cached=cached_projection(row,body['scope'],role)
            if cached is not None:projections[role]=cached;continue
            tag='mcp_diff_'+uuid4().hex;target=canonical_project_path(backend.project_root,backend.project_root/'g2_artifacts/diffs'/(tag+'.mph'))
            copy={'side':role,'tag':tag,'path':str(target),'loaded':False,'removed':False,'input_deleted':False,'source_sha256':row['sha256']};copies.append(copy)
            owned=False;owned_stat=None;attempted=False
            try:
                current=path.lstat()
                if path.is_symlink() or not stat.S_ISREG(current.st_mode) or (current.st_dev,current.st_ino)!=originals[role] or hashlib.sha256(path.read_bytes()).hexdigest()!=row['sha256']:fail('ARTIFACT_MISSING','checkpoint input identity/bytes drifted before copy')
                if current.st_size>Budget.SERIALIZED_BYTES_CAP:fail('BUDGET_EXCEEDED','private checkpoint input exceeds500MiB engineering copy bound')
                if tag in client_call('tags') or tag in backend.service.ledger._models or tag in backend.service.ledger._generations:fail('MODEL_IDENTITY_MISMATCH','fresh owned diff tag collides')
                if target.exists() or target.is_symlink():fail('ARTIFACT_MISSING','fresh private input collision')
                target.parent.mkdir(parents=True,exist_ok=True,mode=0o700)
                copy['copy']=backend._copy_trial_checkpoint(path,target,row['sha256']);owned=True;owned_stat=target.lstat()
                attempted=True;client_call('load',target,tag=tag);copy['loaded']=True
                if tag not in client_call('tags'):fail('EXECUTION_STATE_UNKNOWN','private copy load identity missing')
                projections[role]=project(tag,role)
                if hashlib.sha256(path.read_bytes()).hexdigest()!=row['sha256']:fail('EXECUTION_STATE_UNKNOWN','original checkpoint bytes changed during projection')
            except Exception as exc:
                unknown=unknown or attempted;cause.append({'side':role,'stage':'owned_copy','path':{'segments':[]},'code':getattr(exc,'code','COPY_FAILED'),'message':str(exc),'cause_type':type(exc).__name__,'dispatched':attempted,'mutation_possible':attempted})
            finally:
                try:
                    if attempted and tag in client_call('tags'):client_call('remove',tag)
                    copy['removed']=not attempted or tag not in client_call('tags')
                    if not copy['removed']:fail('EXECUTION_STATE_UNKNOWN','owned tag remains')
                    if owned:
                        actual=target.lstat()
                        if target.is_symlink() or (actual.st_dev,actual.st_ino)!=(owned_stat.st_dev,owned_stat.st_ino):fail('EXECUTION_STATE_UNKNOWN','owned input identity replaced; foreign cleanup refused')
                        target.unlink();copy['input_deleted']=True
                except Exception as exc:
                    unknown=True;cleanup['complete']=False;cleanup['errors'].append({'side':role,'tag':tag,'path':str(target),'code':getattr(exc,'code','CLEANUP_FAILED'),'message':str(exc),'cause_type':type(exc).__name__})
            if role not in projections:projections[role]={'nodes':[],'coverage':[],'errors':[],'complete':False,'unknown':unknown}
    finally:
        for role,(row,path) in selected.items():
            try:
                actual=path.lstat();sha=hashlib.sha256(path.read_bytes()).hexdigest()
                same=not path.is_symlink() and stat.S_ISREG(actual.st_mode) and (actual.st_dev,actual.st_ino)==originals[role] and sha==row['sha256']
                input_observations.append({'side':role,'path':str(path),'sha256':sha,'identity_unchanged':same})
                if not same:unknown=True
            except Exception as exc:
                unknown=True;cause.append({'side':role,'stage':'checkpoint_after','code':'ARTIFACT_MISSING','message':str(exc),'cause_type':type(exc).__name__,'dispatched':False,'mutation_possible':False})
        after=project(ref.model_tag,'source_after')
        observation={'before_snapshot':snapshot,'before_identity':identity,'before_projection':before,'after_projection':after,'selected_pointer_unchanged':pointer_equal(saved)}
        try:
            observation.update(after_snapshot=backend.worker.backend_snapshot(ref.model_tag),after_identity=source_identity(backend,ref))
            observation['snapshot_identity_unchanged']=(snapshot==observation['after_snapshot'] and identity==observation['after_identity'] and pointer_equal(saved))
            observation['semantic_changes']=compare(before,after)
            if observation['semantic_changes'] or not observation['snapshot_identity_unchanged']:unknown=True
            observation['requested_coverage_complete']=before['complete'] and after['complete']
            before_errors={(path_key(c['path']),c['field'],c['status']) for c in before['coverage'] if c['status'] not in {'VERIFIED','NOT_APPLICABLE'}}
            after_errors={(path_key(c['path']),c['field'],c['status']) for c in after['coverage'] if c['status'] not in {'VERIFIED','NOT_APPLICABLE'}}
            if {e for e in after_errors-before_errors if e[2]!='TRUNCATED'}:unknown=True
        except Exception as exc:
            unknown=True;observation['error']={'code':getattr(exc,'code','SOURCE_OBSERVATION_FAILED'),'message':str(exc),'cause_type':type(exc).__name__}
    unknown=unknown or any(p.get('unknown') for p in projections.values()) or before['unknown'] or after['unknown']
    for role in ('left','right'):projections.setdefault(role,{'nodes':[],'coverage':[],'errors':[],'complete':False,'unknown':True})
    left,right=projections['left'],projections['right'];changes=compare(left,right)
    complete=(not unknown and left['complete'] and right['complete'] and cleanup['complete'] and observation.get('requested_coverage_complete') and observation.get('snapshot_identity_unchanged'))
    comparison={'status':'EQUAL' if complete and not changes else 'DIFFERENT' if complete else 'INCOMPLETE','equal':not changes if complete else None,'complete':bool(complete)}
    identities={role:({'selector':'current','model_ref':ref.as_dict(),'revision':identity['revision']} if body[role]=='current' else {'selector':body[role],'source_binding':selected[role][0]['source_binding'],'runtime_version':selected[role][0].get('runtime_version')}) for role in ('left','right')}
    data={'schema_version':1,'operation':'checkpoint.diff','status':'UNKNOWN' if unknown else 'SUCCEEDED' if complete else 'INCOMPLETE','complete':bool(complete),
        'source_identity':identity,'left_identity':identities['left'],'right_identity':identities['right'],'normalized_scope':body['scope'],'projection_schema':SCHEMA,
        'comparison':comparison,'changes':changes,'coverage':left['coverage']+right['coverage']+before['coverage']+after['coverage'],
        'errors':left['errors']+right['errors']+before['errors']+after['errors']+cause,'source_observation':observation,'owned_copies':copies,'cleanup':cleanup,
        'provenance':{'checkpoints':{role:{'path':str(path),'sha256':row['sha256']} for role,(row,path) in selected.items()},
                      'projection_digests':{role:hashlib.sha256(canonical(p['nodes']).encode()).hexdigest() for role,p in projections.items()},'checkpoint_after':input_observations,'temporary_root_tags_not_semantic':True,'isolation':isolation},
        'evidence':{'work_budget':budget.as_dict(),'lifecycle_rpc_budget':'mandatory copy/cleanup/source snapshots remain separate from getter dispatch budget','lifecycle_calls':lifecycle_calls,'source_snapshot_calls':2,'read_policy':'verified owned-copy READ; no source ephemeral mutation or WRITE ticket','full_model_untouched':None}}
    if unknown:
        data['execution_state_unknown']=True
        return {'success':False,'data':data,'execution_state_unknown':True,'error':{'code':'EXECUTION_STATE_UNKNOWN','message':'diff owned cleanup/source observation unresolved','safe_retry':False}}
    if cause:return {'success':False,'data':data,'error':{'code':cause[0]['code'],'message':cause[0]['message'],'safe_retry':False}}
    return {'success':True,'data':data}

def invoke_diff(backend,arguments,execution,operation_id):
    if not all(isinstance(execution.get(k),str) and execution[k] for k in ('project_id','session_id')) or execution['session_id']!=backend.service.ledger.session_id:fail('MODEL_IDENTITY_MISMATCH','diff requires exact outer project/session/ref')
    if not isinstance(execution.get('model_ref'),Mapping):fail('MODEL_IDENTITY_MISMATCH','diff requires exact outer ModelRef object')
    ref=model_ref_from_mapping(execution['model_ref'])
    if execution['model_ref']!=ref.as_dict() or any(type(execution['model_ref'].get(k)) is not type(v) for k,v in ref.as_dict().items()):fail('MODEL_IDENTITY_MISMATCH','diff exact ModelRef fields/types required')
    for k in IDENTITY_FIELDS:
        if k in arguments and arguments[k]!=execution.get(k):fail('MODEL_IDENTITY_MISMATCH','diff body identity conflicts with envelope')
    if 'expected_revision' in arguments or 'expected_revision' in execution:fail('INVALID_REQUEST','diff is READ and has no caller expected_revision')
    body=normalize(arguments);state=backend.service.ledger._state_for(ref)
    if 'inspect' not in backend.service.ledger.permissions:fail('PERMISSION_DENIED','diff requires inspect')
    if state.dirty or state.active_operation_id:fail('EXECUTION_STATE_UNKNOWN','diff source dirty or busy')
    if backend.model_project_binding(ref.as_dict())!={'project_id':execution['project_id'],'attribution':'PROJECT_BOUND'}:fail('PROJECT_IDENTITY_MISMATCH','diff source exact project binding required')
    rows=backend.store.list_metadata('checkpoints')
    if not isinstance(rows,list) or not all(isinstance(r,Mapping) for r in rows):fail('CHECKPOINT_CONFLICT','checkpoint authority returned malformed records')
    if any(r.get('checkpoint_id')=='current' for r in rows):fail('CHECKPOINT_CONFLICT','reserved checkpoint ID current conflicts with literal selector')
    selected={role:checkpoint(backend,body[role],execution['project_id'],rows) for role in ('left','right') if body[role]!='current'}
    isolation=None
    if any(row.get('semantic_projection') is None for row,path in selected.values()):isolation=backend._require_g2_isolation()
    observed={}
    def callback(_):
        current=backend.service.ledger._state_for(ref)
        if current.dirty or current.active_operation_id:fail('EXECUTION_STATE_UNKNOWN','actual queued source observation dirtied/busied the source before diff dispatch')
        result=run_diff(backend,ref,body,execution,selected,isolation);observed['result']=result;return result
    try:
        result=backend.service.execute_legacy('checkpoint_diff',callback,body,model_ref=ref,expected_revision=None,request_id=execution.get('request_id'),session_id=execution['session_id'],effect='inspect')
    except Exception as exc:
        if 'result' not in observed:raise
        result=observed['result'];result.update(success=False,execution_state_unknown=True,error={'code':'EXECUTION_STATE_UNKNOWN','message':str(exc),'safe_retry':False})
        result['data'].update(status='UNKNOWN',complete=False,execution_state_unknown=True)
        result['data']['comparison'].update(status='INCOMPLETE',equal=None,complete=False)
        result['data']['evidence']['service_failure']={'type':type(exc).__name__,'message':str(exc)}
        retain_terminal_source_guard(backend,ref,result,'diff_service')
    try:backend.persist()
    except Exception as exc:
        result.update(success=False,execution_state_unknown=True,error={'code':'EXECUTION_STATE_UNKNOWN','message':str(exc),'safe_retry':False})
        result['data'].update(status='UNKNOWN',complete=False,execution_state_unknown=True);result['data']['comparison'].update(status='INCOMPLETE',equal=None,complete=False)
        result['data']['evidence']['terminal_persistence_failure']={'type':type(exc).__name__,'message':str(exc)}
        retain_terminal_source_guard(backend,ref,result,'diff_persistence')
    return result
