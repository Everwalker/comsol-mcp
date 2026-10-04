"""Owned checkpoint clone lifecycles, with no implicit source-model mutation."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import stat
from typing import Any, Mapping
from uuid import uuid4

from ._execution_contract import ExecutionContractError, canonical_project_path, model_ref_from_mapping
from ._g2_contract import NodePath, resolve_node_path
from ._g2_public_api import IDENTITY_FIELDS, provisional_effect, prepare_invoke, execute_prepared, describe_receiver
from ._g2_engine import validate_node_action, prepare_node_action, execute_node_action, property_get

B_ACTIONS = frozenset({'checkpoint.branch', 'api.probe'})

def input_schema(operation):
    ref_properties={k:{'type':'string','minLength':1} for k in ['session_id','server_instance_id','model_tag']}
    ref_properties.update(schema_version={'type':'integer','minimum':1},generation={'type':'integer','minimum':1})
    identity={'project_id':{'type':'string','minLength':1},'session_id':{'type':'string','minLength':1},
              'model_ref':{'type':'object','required':list(ref_properties),'properties':ref_properties,'additionalProperties':False},'idempotency_key':{'type':'string','minLength':1},'request_id':{'type':'string','minLength':1}}
    if operation=='checkpoint.branch':
        properties={**identity,'checkpoint_id':{'type':'string','minLength':1},'label':{'type':'string'},'expected_revision':{'type':'integer','minimum':0}}
        required=['project_id','session_id','model_ref','idempotency_key','expected_revision','checkpoint_id','label']
    else:
        readback={'type':'array','minItems':1,'items':{'type':'object','required':['path'],'properties':{'path':{'type':'object'},'names':{'type':'array','minItems':1,'items':{'type':'string','minLength':1}}},'additionalProperties':False}}
        common={'kind':{'type':'string'},'checkpoint_id':{'type':'string','minLength':1},'readback':readback}
        inv={**common,'kind':{'const':'invoke'},'path':{'type':'object'},'method':{'type':'string','minLength':1},'arguments':{'type':'array'},'declared_effect':{'enum':['READ','WRITE']},'java_signature':{'type':'array','items':{'type':'string'}}}
        create={**common,'kind':{'const':'create_feature'},'parent':{'type':'object'},'collection':{'type':'string','minLength':1},'tag':{'type':'string','minLength':1},'type_id':{'type':'string','minLength':1},'properties':{'type':'array','default':[]}}
        properties={**identity,'probe':{'oneOf':[{'type':'object','required':['kind','checkpoint_id','readback','path','method','arguments','declared_effect'],'properties':inv,'additionalProperties':False},{'type':'object','required':['kind','checkpoint_id','readback','parent','collection','tag','type_id'],'properties':create,'additionalProperties':False}]}}
        required=['project_id','session_id','model_ref','idempotency_key','probe']
    return {'type':'object','required':required,'properties':properties,'additionalProperties':False}

def data_schema(operation):
    common=['schema_version','operation','status','complete','checkpoint_id','checkpoint_source_binding','source_before','source_after','source_pointer_restored','main_model_untouched','source_unchanged_within_scope','cleanup','evidence','coverage','isolation_proof']
    specific=(['admission_source_identity','branch_label','branch_model_ref','branch_revision','clone_input','revision_proof','persistence'] if operation=='checkpoint.branch'
              else ['kind','source_admission','private_model_identity','probe_result','readback','capability'])
    optional=['typed_return','created_path','probe_result_evidence','clone_input','execution_state_unknown','cleanup_failed','partial_change','effect','domain_state','verification_status','dispatch_stage','domain_outcome','verified_outcome']
    if operation=='checkpoint.branch':optional=[k for k in optional if k not in {'typed_return','created_path','probe_result_evidence'}]
    fields={k:{'type':'object'} for k in common+specific+optional}
    for k in ['source_before','source_after','branch_model_ref','clone_input','revision_proof','capability']:
        if k in fields:fields[k]={'type':['object','null']}
    fields.update(schema_version={'const':1},operation={'const':operation},status={'enum':['SUCCEEDED','UNKNOWN']},
                  complete={'type':'boolean'},checkpoint_id={'type':'string','minLength':1},main_model_untouched={'type':'null'},
                  source_pointer_restored={'type':'boolean'},source_unchanged_within_scope={'type':['boolean','null']},
                  coverage={'type':'array','items':{'type':'string'}})
    for k in ['execution_state_unknown','cleanup_failed','partial_change']:fields[k]={'type':'boolean'}
    for k in ['effect','domain_state','verification_status','dispatch_stage','verified_outcome']:fields[k]={'type':'string'}
    if operation=='checkpoint.branch':
        fields.update(branch_label={'type':'string'},branch_revision={'type':['integer','null'],'minimum':0})
    else:
        from ._g2_registry import _unit_a_data_schema
        fields.update(kind={'enum':['invoke','create_feature']},probe_result={'enum':['supported','unsupported','failed','unknown']},
                      typed_return=_unit_a_data_schema('api.invoke')['properties']['typed_return'])
    return {'type':'object','required':common+specific,'properties':fields,'additionalProperties':False}

def refuse(code: str, message: str):
    raise ExecutionContractError(code, message)

def normalize(operation: str, arguments: Mapping[str, Any]) -> dict[str, Any]:
    if not isinstance(arguments, Mapping): refuse('INVALID_REQUEST', 'body must be an object')
    body={k:v for k,v in arguments.items() if k not in IDENTITY_FIELDS}
    fields={'checkpoint_id','label'} if operation=='checkpoint.branch' else {'probe'}
    if set(body)!=fields: refuse('INVALID_REQUEST','missing or unknown Unit B body fields')
    if operation=='checkpoint.branch':
        if not isinstance(body['checkpoint_id'],str) or not body['checkpoint_id'].strip() or body['checkpoint_id']=='current':
            refuse('CHECKPOINT_REQUIRED','branch requires an exact nonreserved checkpoint ID')
        if not isinstance(body['label'],str): refuse('INVALID_REQUEST','branch label must be a string')
        return dict(body)
    p=body['probe']
    if not isinstance(p,Mapping): refuse('INVALID_REQUEST','probe must be a discriminated object')
    invoke={'kind','checkpoint_id','path','method','arguments','declared_effect','readback'}
    create={'kind','checkpoint_id','parent','collection','tag','type_id','readback'}
    required=invoke if p.get('kind')=='invoke' else create if p.get('kind')=='create_feature' else None
    optional={'java_signature'} if p.get('kind')=='invoke' else {'properties'}
    if required is None or not required<=set(p) or set(p)-required-optional:
        refuse('INVALID_REQUEST','probe discriminant has missing/unknown fields')
    if not isinstance(p['checkpoint_id'],str) or not p['checkpoint_id'].strip() or p['checkpoint_id']=='current':
        refuse('CHECKPOINT_REQUIRED','probe requires exact nonreserved checkpoint ID')
    q=dict(p)
    readback=q['readback']
    if not isinstance(readback,list) or not readback: refuse('INVALID_REQUEST','probe needs nonempty readback')
    for row in readback:
        if not isinstance(row,Mapping) or set(row)-{'path','names'} or 'path' not in row:
            refuse('INVALID_REQUEST','readback requires path and optional names only')
        NodePath.from_wire(row['path'],allow_empty=True)
        if 'names' in row and (not isinstance(row['names'],list) or not row['names'] or not all(isinstance(n,str) and n for n in row['names']) or len(set(row['names']))!=len(row['names'])):
            refuse('INVALID_REQUEST','readback names must be nonempty distinct strings')
    inner=inner_arguments(q)
    if q['kind']=='invoke':
        effect=provisional_effect(inner)
        if inner['declared_effect']!=effect: refuse('PERMISSION_DENIED','declared probe effect mismatches exact capability')
    else:
        q['properties']=validate_node_action('node.create',inner)['properties']
    return {'probe':q}

def inner_arguments(p):
    return {k:v for k,v in p.items() if k not in {'kind','checkpoint_id','readback'}}

def inner_permission(body):
    p=normalize('api.probe',body)['probe']
    return 'project_write' if p['kind']=='create_feature' or provisional_effect(inner_arguments(p))=='WRITE' else 'inspect'

def checkpoint_input(backend,ref,checkpoint_id,project_id):
    matches=[r for r in backend.store.list_metadata('checkpoints') if r.get('checkpoint_id')==checkpoint_id]
    if len(matches)!=1: refuse('NODE_NOT_FOUND','checkpoint ID is absent or ambiguous')
    row=matches[0];binding=row.get('source_binding')
    if not isinstance(binding,Mapping) or binding.get('model_ref')!=ref.as_dict():
        refuse('MODEL_IDENTITY_MISMATCH','checkpoint has no retained exact source epoch lineage')
    if type(binding.get('revision')) is not int or binding['revision']<0:
        refuse('CHECKPOINT_REQUIRED','checkpoint historical revision not authoritative')
    if binding['revision']>backend.service.ledger._state_for(ref).revision:
        refuse('REVISION_CONFLICT','checkpoint revision is not in the admitted source history')
    if row.get('source_model_ref',ref.as_dict())!=ref.as_dict() or row.get('source_revision',binding['revision'])!=binding['revision']:
        refuse('MODEL_IDENTITY_MISMATCH','checkpoint lineage fields disagree')
    attribution=backend.model_project_binding(ref.as_dict())
    if attribution.get('project_id')!=project_id or attribution.get('attribution')!='PROJECT_BOUND':
        refuse('PROJECT_IDENTITY_MISMATCH','checkpoint source must have persisted exact project binding')
    if row.get('project_id',project_id)!=project_id: refuse('PROJECT_IDENTITY_MISMATCH','checkpoint project differs')
    sha=row.get('sha256')
    if not isinstance(sha,str) or len(sha)!=64 or any(c not in '0123456789abcdef' for c in sha):
        refuse('ARTIFACT_MISSING','checkpoint hash not authoritative')
    raw=row.get('path')
    if not isinstance(raw,str) or not raw: refuse('ARTIFACT_MISSING','checkpoint path absent')
    candidate=Path(raw)
    if candidate.is_symlink(): refuse('ARTIFACT_MISSING','checkpoint cannot be a symlink')
    path=canonical_project_path(backend.project_root,candidate)
    if not path.exists() or not stat.S_ISREG(path.lstat().st_mode) or hashlib.sha256(path.read_bytes()).hexdigest()!=sha:
        refuse('ARTIFACT_MISSING','checkpoint bytes differ from authoritative hash')
    return dict(row),path

def pointer():
    from ._server import session_server as srv
    return (srv,tuple(getattr(srv,k,None) for k in ('_current_model','_current_model_origin','_current_model_path')))

def pointer_equal(saved):
    srv,values=saved
    actual=tuple(getattr(srv,k,None) for k in ('_current_model','_current_model_origin','_current_model_path'))
    return actual[0] is values[0] and actual[1:]==values[1:]

def capture(worker,tag,requests):
    """Only actual typed public scalar/list getters and requested property values."""
    rows=[]
    for request in requests:
        parsed=NodePath.from_wire(request['path'],allow_empty=True)
        node=resolve_node_path(worker.client().model(tag),parsed)
        descriptor=describe_receiver(worker,node)
        observations={}
        for name,method,returns in [('type','getType','java.lang.String'),('label','label','java.lang.String'),('active','isActive','boolean'),('tags','tags','[Ljava.lang.String;')]:
            present=any(m.get('method')==method and m.get('parameters')==[] and m.get('returns')==returns for m in descriptor['methods'])
            if not present: continue
            value=getattr(node,method)()
            valid=(isinstance(value,str) if returns=='java.lang.String' else type(value) is bool if returns=='boolean' else isinstance(value,list) and all(isinstance(v,str) for v in value))
            if not valid: refuse('EXECUTION_STATE_UNKNOWN','readback returned wrong public type for '+method)
            observations[name]=value
        if 'names' in request: observations['properties']=property_get(worker,tag,parsed.as_dict(),request['names'])['properties']
        if not observations: refuse('API_UNSUPPORTED','readback has no actual supported typed getter')
        rows.append({'path':parsed.as_dict(),'observations':observations,'coverage':'requested public typed getters/properties only'})
    return rows

def source_identity(backend,ref):
    state=backend.service.ledger._state_for(ref)
    return {'model_ref':ref.as_dict(),'revision':state.revision,'project_binding':backend.model_project_binding(ref.as_dict())}

def guard_unknown_owned_ref(backend,data,stage,*,persist=False):
    """Fence only the exact ref bound by this owned-copy callback, never rebind."""
    evidence=data['evidence'];mapping=evidence.get('owned_bound_ref')
    if mapping is None:return
    guard=evidence.setdefault('owned_ref_guard',{'status':'UNKNOWN','model_ref':mapping,'errors':[]})
    guard['stage']=stage
    try:
        ref=model_ref_from_mapping(mapping)
        state=backend.service.ledger._models.get(ref.model_tag)
        if (state is None or state.ref!=ref or ref.model_tag!=evidence['owned_tag']
                or backend.service.ledger.model_ownership.get(ref.model_tag)!='mcp_owned'):
            refuse('EXECUTION_STATE_UNKNOWN','owned ref guard identity/ownership disagrees')
        guard['retired']=state.retired
        if state.retired:
            guard['policy']='already retired; no reopening or rebinding'
            return
        state.dirty=True;state.fingerprint=None
        expected={'model_ref':state.ref.as_dict(),'revision':state.revision,'dirty':state.dirty,
                  'fingerprint':state.fingerprint,'active_operation_id':state.active_operation_id}
        guard.update(in_memory_guarded=True,state=expected)
        if persist:
            guard['persist_attempted']=True
            backend.persist()
            actual=backend.store.get_metadata('revisions',backend._model_project_key(mapping))
            guard['persisted_record']=actual
            guard['persisted_state_verified']=(isinstance(actual,Mapping) and all(
                json.dumps(actual.get(k),sort_keys=True,separators=(',',':'))==json.dumps(v,sort_keys=True,separators=(',',':'))
                and k in actual for k,v in expected.items()))
            if not guard['persisted_state_verified']:
                refuse('EXECUTION_STATE_UNKNOWN','owned dirty-state persistence readback disagrees')
    except Exception as exc:
        guard['errors'].append({'stage':stage,'type':type(exc).__name__,'message':str(exc)})

def retain_terminal_source_guard(backend,ref,result,stage):
    """A secondary bookkeeping failure must not replace the original cause."""
    errors=result['data']['evidence'].setdefault('terminal_source_guard_errors',[])
    try:
        state=backend.service.ledger._state_for(ref)
        state.dirty=True;state.fingerprint=None;state.active_operation_id=None
        result['data']['evidence']['terminal_source_guard']={'model_ref':state.ref.as_dict(),
            'revision':state.revision,'dirty':state.dirty,'fingerprint':state.fingerprint,
            'active_operation_id':state.active_operation_id}
    except Exception as exc:errors.append({'step':'in_memory','type':type(exc).__name__,'message':str(exc)})
    try:backend._freeze_g2_model(ref,'owned lifecycle '+stage+' failed')
    except Exception as exc:errors.append({'step':'freeze','type':type(exc).__name__,'message':str(exc)})
    try:result.setdefault('execution',{}).update(backend.service._metadata(ref)['execution'])
    except Exception as exc:errors.append({'step':'metadata','type':type(exc).__name__,'message':str(exc)})

def run_owned(backend,operation,ref,body,execution,operation_id,metadata,path,proof):
    branch=operation=='checkpoint.branch'
    kind='branch' if branch else 'probe'
    tag='mcp_'+kind+'_'+uuid4().hex
    destination=canonical_project_path(backend.project_root,backend.project_root/'g2_artifacts'/('branches' if branch else 'probes')/(tag+'.mph'))
    saved_pointer=pointer();before_identity=source_identity(backend,ref)
    before=backend.worker.backend_snapshot(ref.model_tag)
    data={'schema_version':1,'operation':operation,'status':'UNKNOWN','complete':False,
          'checkpoint_id':metadata['checkpoint_id'],'checkpoint_source_binding':metadata['source_binding'],
          'source_before':before,'source_after':None,'source_pointer_restored':False,
          'main_model_untouched':None,'source_unchanged_within_scope':None,
          'cleanup':{'model_removed':False,'artifact_deleted':False,'errors':[]},
          'evidence':{'stage':'pre_copy','owned_tag':tag,'owned_path':str(destination),'mutation_targets':[]},
          'coverage':['source shallow snapshot/counter/ref/selected pointer; full semantic untouched UNVERIFIED']}
    if branch:data.update({'admission_source_identity':before_identity,'branch_label':body['label'],'branch_model_ref':None,'branch_revision':None,'clone_input':None,'revision_proof':None,'persistence':{'verified':False}})
    else:data.update({'kind':body['probe']['kind'],'source_admission':before_identity,'private_model_identity':{'tag':tag,'path':str(destination)},'probe_result':'unknown','readback':{},'capability':None})
    owned_file=False;owned_stat=None;load_attempted=False;success=False;source_readback=None;private_ref=None;bind_attempted=False
    try:
        if not branch:
            source_readback=capture(backend.worker,ref.model_tag,body['probe']['readback'])
        if tag in backend.worker.client().tags(): refuse('ENGINE_BUSY','fresh private tag collision')
        if tag in backend.service.ledger._generations or tag in backend.service.ledger._models:
            refuse('MODEL_IDENTITY_MISMATCH','fresh private tag collides with retained ledger epoch')
        if branch and backend.store.get_metadata('artifacts','g2_owned_branch_'+tag) is not None:refuse('ARTIFACT_MISSING','fresh branch metadata collision')
        if destination.exists() or destination.is_symlink(): refuse('ARTIFACT_MISSING','private file collision')
        destination.parent.mkdir(mode=0o700,parents=True,exist_ok=True)
        data['clone_input']=backend._copy_trial_checkpoint(path,destination,metadata['sha256'])
        owned_file=True;owned_stat=destination.lstat();data['evidence']['stage']='copy_verified'
        # Snapshot and pointer guard precede any private load; never restore a foreign pointer.
        if not pointer_equal(saved_pointer): refuse('EXECUTION_STATE_UNKNOWN','source selected pointer changed before load')
        data['evidence']['mutation_targets'].append({'method':'load','model_tag':tag,'path':str(destination)})
        load_attempted=True
        backend.worker.client().load(destination,tag=tag)
        if tag not in backend.worker.client().tags(): refuse('EXECUTION_STATE_UNKNOWN','loaded private tag not present')
        data['evidence']['stage']='loaded'
        if branch:
            bind_attempted=True
            bound=backend.service.bind_model(tag,ownership='mcp_owned')
            newref=bound['execution']['model_ref'];newrev=bound['execution']['revision']
            data.update({'branch_model_ref':newref,'branch_revision':newrev})
            if newref==ref.as_dict():refuse('EXECUTION_STATE_UNKNOWN','branch ref is not fresh')
            backend._bind_model_project(newref,execution['project_id']);backend.persist()
            record={'kind':'checkpoint_branch','schema_version':1,'checkpoint_id':metadata['checkpoint_id'],'source_binding':metadata['source_binding'],
                    'admission_source_identity':before_identity,'label':body['label'],'model_ref':newref,
                    'revision':newrev,'clone_input':data['clone_input'],'private_path':str(destination),'operation_id':operation_id}
            key='g2_owned_branch_'+tag
            if backend.store.get_metadata('artifacts',key) is not None:refuse('EXECUTION_STATE_UNKNOWN','branch metadata collision')
            backend.store.put_metadata('artifacts',key,record)
            persisted=backend.store.get_metadata('artifacts',key)
            binding=backend.model_project_binding(newref)
            revision_record=backend.store.get_metadata('revisions',backend._model_project_key(newref))
            state=backend.service.ledger._state_for(model_ref_from_mapping(newref))
            expected_revision_record={'model_ref':state.ref.as_dict(),'revision':state.revision,'dirty':state.dirty,
                                      'fingerprint':state.fingerprint,'active_operation_id':state.active_operation_id,
                                      'project_id':execution['project_id'],'attribution':'PROJECT_BOUND'}
            data['persistence']={'verified':False,'record':persisted,'project_binding':binding,
                                 'revision_record':revision_record,'expected_revision_record':expected_revision_record}
            if persisted!=record or binding!={'attribution':'PROJECT_BOUND','project_id':execution['project_id']}:
                refuse('EXECUTION_STATE_UNKNOWN','branch persistence/readback disagrees')
            if json.dumps(revision_record,sort_keys=True,separators=(',',':'))!=json.dumps(expected_revision_record,sort_keys=True,separators=(',',':')):
                refuse('EXECUTION_STATE_UNKNOWN','persisted branch revision/ref/state readback disagrees')
            if state.revision!=newrev or state.ref.as_dict()!=newref:refuse('EXECUTION_STATE_UNKNOWN','bound branch identity/revision disagrees')
            data['persistence']['verified']=True
        else:
            p=body['probe'];requests=p['readback']
            bind_attempted=True
            private_binding=backend.service.bind_model(tag,ownership='mcp_owned')
            private_ref=model_ref_from_mapping(private_binding['execution']['model_ref'])
            backend._bind_model_project(private_ref.as_dict(),execution['project_id']);backend.persist()
            data['private_model_identity'].update(model_ref=private_ref.as_dict(),revision=private_binding['execution']['revision'],lifetime='owned probe only; retired after successful cleanup')
            private_before=capture(backend.worker,tag,requests)
            args=inner_arguments(p)
            if p['kind']=='invoke':
                prepared=prepare_invoke(backend.worker,tag,args)
                prepared['model_ref']=private_ref.as_dict()
                data['capability']=prepared['capability'].as_dict()
                data['evidence']['mutation_targets'].append({'method':'api.invoke','model_tag':tag})
                result=execute_prepared(backend.worker,prepared)
            else:
                prepared=prepare_node_action(backend.worker,tag,'node.create',args,model_revision=before_identity['revision'])
                data['capability']={'operation':'node.create','effect':'WRITE'}
                data['evidence']['mutation_targets'].append({'method':'node.create','model_tag':tag})
                result=execute_node_action(backend.worker,tag,'node.create',prepared,model_ref=private_ref.as_dict())
            data['probe_result_evidence']=result
            detail=result.get('data') if isinstance(result.get('data'),Mapping) else result
            if result.get('success') is not True or detail.get('status')=='UNKNOWN' or result.get('execution_state_unknown') or result.get('cleanup_failed'):
                refuse('EXECUTION_STATE_UNKNOWN','private probe operation unresolved')
            if detail.get('typed_return') is not None:data['typed_return']=detail['typed_return']
            if detail.get('created_path') is not None:data['created_path']=detail['created_path']
            private_after=capture(backend.worker,tag,requests)
            source_after_readback=capture(backend.worker,ref.model_tag,requests)
            data['readback']={'source_before':source_readback,'source_after':source_after_readback,'private_before':private_before,'private_after':private_after}
            if source_readback!=source_after_readback:refuse('EXECUTION_STATE_UNKNOWN','source scoped readback changed')
            data['probe_result']='supported'
        data['evidence']['stage']='verified';success=True
    except Exception as exc:
        data['evidence']['failure']={'type':type(exc).__name__,'message':str(exc),'code':getattr(exc,'code',None),'stage':data['evidence']['stage']}
        # Even a private-copy validation failure is not an automatic source/cleanup proof.
        if not branch and isinstance(exc,ExecutionContractError) and exc.code=='API_UNSUPPORTED':data['probe_result']='unsupported'
    finally:
        # bind_model can create the epoch and then raise during its state emit.
        # The tag had no ledger state at admission; retain only that newly
        # created exact owned epoch, not any pre-existing collision target.
        if bind_attempted:
            state=backend.service.ledger._models.get(tag)
            if (state is not None and state.ref.model_tag==tag
                    and backend.service.ledger.model_ownership.get(tag)=='mcp_owned'):
                data['evidence']['owned_bound_ref']=state.ref.as_dict()
        if not branch:
            try:
                if load_attempted and tag in backend.worker.client().tags():backend.worker.client().remove(tag)
                data['cleanup']['model_removed']=tag not in backend.worker.client().tags()
                if private_ref is not None and data['cleanup']['model_removed']:
                    backend.service.ledger.retire_model(private_ref)
                    data['cleanup']['private_ref_retired']=True
                    backend.persist()
            except Exception as exc:data['cleanup']['errors'].append('private model cleanup: '+type(exc).__name__+': '+str(exc))
            try:
                bound_state=backend.service.ledger._models.get(tag) if bind_attempted else None
                retained_ref=(bound_state is not None and not bound_state.retired)
                if owned_file and (not data['cleanup']['model_removed'] or retained_ref):
                    data['cleanup']['artifact_policy']='retained for unresolved owned model/ref cleanup'
                elif owned_file:
                    actual=destination.lstat()
                    if destination.is_symlink() or (actual.st_dev,actual.st_ino)!=(owned_stat.st_dev,owned_stat.st_ino):
                        refuse('EXECUTION_STATE_UNKNOWN','private file identity replaced; refusing foreign cleanup')
                    destination.unlink()
                data['cleanup']['artifact_deleted']=not destination.exists()
            except Exception as exc:data['cleanup']['errors'].append('private file cleanup: '+type(exc).__name__+': '+str(exc))
        else:
            data['cleanup'].update({'policy':'persistent owned branch retained, including uncertain failure','retained_path':str(destination),'retained_tag':tag})
        try:
            data['source_after']=backend.worker.backend_snapshot(ref.model_tag)
            identity_after=source_identity(backend,ref)
            unchanged=(before==data['source_after'] and before_identity==identity_after and pointer_equal(saved_pointer))
            data['source_pointer_restored']=pointer_equal(saved_pointer)
            data['source_unchanged_within_scope']=unchanged
            if not unchanged:success=False;data['evidence']['source_guard_failure']='snapshot/ref/revision/project/pointer changed'
            checkpoint_sha=hashlib.sha256(path.read_bytes()).hexdigest()
            data['evidence']['checkpoint_after_sha256']=checkpoint_sha
            if checkpoint_sha!=metadata['sha256']:success=False;data['evidence']['checkpoint_guard_failure']='original checkpoint bytes changed'
            if branch and owned_file:
                data['evidence']['clone_after_sha256']=hashlib.sha256(destination.read_bytes()).hexdigest()
                if data['evidence']['clone_after_sha256']!=metadata['sha256']:success=False
        except Exception as exc:
            success=False;data['evidence']['source_guard_failure']=type(exc).__name__+': '+str(exc)
        if data['cleanup']['errors']:success=False
    if success:
        data.update({'status':'SUCCEEDED','complete':True})
        if branch:
            proof.update({'action':operation,'source_ref':ref.as_dict(),'source_before':before,'source_after':data['source_after'],
                          'pointer_unchanged':True,'identity_unchanged':True,'branch_ref':data['branch_model_ref'],
                          'clone_sha256':metadata['sha256'],'copy_verified':True,'persistence_verified':True,
                          'persisted_revision_record':data['persistence']['revision_record'],
                          'owned_tag':tag,'operation_id':operation_id,'mutation_targets':data['evidence']['mutation_targets'],
                          'cleanup_errors':[],'unknown':False})
            data['revision_proof']=dict(proof)
        return {'success':True,'data':data}
    data.update({'status':'UNKNOWN','complete':False,'execution_state_unknown':True})
    guard_unknown_owned_ref(backend,data,'owned_callback_unknown')
    return {'success':False,'data':data,'execution_state_unknown':True,
            'cleanup_failed':bool(data['cleanup']['errors']),
            'error':{'code':'EXECUTION_STATE_UNKNOWN','message':'owned '+kind+' lifecycle not verified','safe_retry':False}}

def invoke_owned(backend,operation,arguments,execution,operation_id):
    from ._execution_contract import model_ref_from_mapping
    required={'project_id','session_id','model_ref','idempotency_key'}
    if not required<=set(execution) or not all(isinstance(execution[n],str) and execution[n] for n in ['project_id','session_id','idempotency_key']):
        refuse('MODEL_IDENTITY_MISMATCH','Unit B requires exact outer project/session/ref/key')
    if execution['session_id']!=backend.service.ledger.session_id:refuse('MODEL_IDENTITY_MISMATCH','session differs')
    for k in IDENTITY_FIELDS:
        if k in arguments and arguments[k]!=execution.get(k):refuse('MODEL_IDENTITY_MISMATCH','body identity conflicts with envelope')
    if operation=='api.probe' and ('expected_revision' in execution or 'expected_revision' in arguments):
        refuse('INVALID_REQUEST','probe caller expected_revision forbidden; serial admission captures it')
    ref=model_ref_from_mapping(execution['model_ref'])
    if (execution['model_ref']!=ref.as_dict()
            or any(type(execution['model_ref'].get(k)) is not type(v) for k,v in ref.as_dict().items())):
        refuse('MODEL_IDENTITY_MISMATCH','outer ModelRef must match its exact canonical field/type contract')
    state=backend.service.ledger._state_for(ref)
    body=normalize(operation,arguments)
    permissions={'project_write'} if operation=='checkpoint.branch' else {'compute',inner_permission(body)}
    if not permissions<=backend.service.ledger.permissions:refuse('PERMISSION_DENIED','Unit B actual permissions required: '+','.join(sorted(permissions)))
    revision=execution.get('expected_revision') if operation=='checkpoint.branch' else state.revision
    if type(revision) is not int or revision!=state.revision:refuse('REVISION_CONFLICT','current admission revision differs')
    if state.dirty or state.active_operation_id:refuse('EXECUTION_STATE_UNKNOWN','source is dirty or busy')
    checkpoint_id=body['checkpoint_id'] if operation=='checkpoint.branch' else body['probe']['checkpoint_id']
    metadata,path=checkpoint_input(backend,ref,checkpoint_id,execution['project_id'])
    isolation=backend._require_g2_isolation()
    # Backend invoke is already the daemon's single serialized engine claim.
    observed={}
    with backend.service._owned_branch_scope() as proof:
        def callback(_args):
            result=run_owned(backend,operation,ref,body,execution,operation_id,metadata,path,proof)
            observed['result']=result
            return result
        try:
            result=backend.service.execute_legacy(operation.replace('.','_'),callback,body,model_ref=ref,
                    expected_revision=revision,request_id=execution.get('request_id'),session_id=execution['session_id'],
                    effect='project_write' if operation=='checkpoint.branch' else 'compute',_owned_branch_proof=proof if operation=='checkpoint.branch' else None)
        except Exception as exc:
            if 'result' not in observed:
                raise
            result=observed['result']
            result.update(success=False,execution_state_unknown=True,
                          error={'code':'EXECUTION_STATE_UNKNOWN','message':str(exc),'safe_retry':False})
            result['data'].update(status='UNKNOWN',complete=False,execution_state_unknown=True)
            result['data']['evidence']['service_terminal_failure']={'type':type(exc).__name__,'message':str(exc)}
            retain_terminal_source_guard(backend,ref,result,'service_terminal')
    result.setdefault('data',{})['isolation_proof']=isolation
    try:
        backend.persist()
    except Exception as exc:
        result.update(success=False,execution_state_unknown=True,
                      error={'code':'EXECUTION_STATE_UNKNOWN','message':str(exc),'safe_retry':False})
        result['data'].update(status='UNKNOWN',complete=False,execution_state_unknown=True)
        result['data']['evidence']['terminal_persistence_failure']={'type':type(exc).__name__,'message':str(exc)}
        retain_terminal_source_guard(backend,ref,result,'terminal_persistence')
    if result.get('success') is not True:
        data=result['data'];data.update(status='UNKNOWN',complete=False,execution_state_unknown=True)
        if data.get('revision_proof') is not None:
            data['evidence']['unaccepted_candidate_revision_proof']=data['revision_proof']
            data['revision_proof']=None
        guard_unknown_owned_ref(backend,data,'owned_terminal_unknown',persist=True)
    return result
