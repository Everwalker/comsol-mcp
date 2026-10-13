"""Backend-owned refinement snapshots for the frozen transient benchmark."""
import hashlib
import json
import math
import uuid

from ._g2_engine import _call
from ._g3_common import bound_model, tag_list
from ._execution_contract import PreWriteRefusal
from ._observation_store import current_context, verify_producer, observation_context, resolve_observation


def digest(value):
    return hashlib.sha256(json.dumps(value,sort_keys=True,allow_nan=False).encode()).hexdigest()


def require(condition, message):
    if not condition: raise PreWriteRefusal('INVALID_CONVERGENCE_REF',message)


def plain(value):
    return value['data'] if isinstance(value,dict) and 'kind' in value and 'data' in value else value


def register_case(worker, tag, observation_ref, oracle, oracle_results):
    ctx=current_context()
    observation, sample=resolve_observation(worker,tag,observation_ref)
    model=bound_model(worker,tag)
    sol=_call(model,'sol',observation['solution'])
    size=plain(_call(sol,'getSize'))
    study=_call(model,'study',observation['source_identity']['study'])
    steps=[_call(study,'feature',t) for t in tag_list(_call(study,'feature'))]
    transient=[s for s in steps if _call(s,'getType')=='Transient']
    require(len(transient)==1,'Exactly one actual transient step required')
    step=transient[0]
    require(_call(step,'getString','usertol')=='on','Explicit solver tolerance required')
    tolerance=float(_call(step,'getString','rtol'))
    meshes=tag_list(_call(model,'mesh'))
    require(len(meshes)==1,'Unambiguous actual mesh required')
    mesh=_call(model,'mesh',meshes[0])
    elements=int(_call(mesh,'getNumElem'))
    max_volume=float(_call(mesh,'getMaxVolume'))
    require(elements>0 and max_volume>0 and tolerance>0,'Native refinement settings unavailable')
    # Frozen B2 errors come from the registered field and native time axis,
    # independent of caller-selected names or array indices.
    points=[{'x':x,'y':.05,'z':.05} for x in (.25,.5,.75)]
    require(sample['points']==points and sample['coordinate_unit']=='m', 'Frozen B2 sampling coordinates required')
    field=sample['field_array']
    require(sample['expressions']==['T'] and field['shape']==[1,1,4,3], 'Frozen B2 field layout required')
    require(field['units']['expression']=={'T':'K'}, 'Frozen B2 temperature unit must be K')
    times=plain(observation['source_identity']['times'])
    require(len(times)==4 and all(abs(float(a)-b)<1e-12 for a,b in zip(times,[0,.01,.03,.1])), 'Frozen B2 stored times required')
    oracle_results={}
    for i,t in enumerate((.01,.03,.1),1):
        for j,x in enumerate((.25,.5,.75)):
            error=abs(float(sample['values'][0][0][i][j])-(300+10*math.sin(math.pi*x)*math.exp(-math.pi**2*t)))
            oracle_results[f'T_{x}_{t}']={'error':error,'status':'PASS' if error<=.1 else 'FAIL'}
    errors=[float(v['error']) for v in oracle_results.values()]
    require(errors and all(math.isfinite(v) for v in errors),'Finite actual oracle errors required')
    payload={'kind':'w20_convergence','convergence_id':'conv_'+uuid.uuid4().hex,
        'producer':ctx['producer'],'model_ref':ctx['model_ref'],'revision':ctx['revision'],
        'observation_ref':observation_ref,'oracle':oracle,'oracle_results':oracle_results,
        'source_identity':observation['source_identity'],'error':max(errors),
        'mesh':meshes[0],'elements':elements,'max_element_volume':max_volume,
        'mesh_size_metric':max_volume**(1/3),'dofs':int(size[0]),'tolerance':tolerance,
        'tlist':_call(step,'getString','tlist'),'sample_points':sample['points'],
        'coordinate_unit':sample['coordinate_unit']}
    payload['sha256']=digest(payload)
    ctx['store'].persist_artifact(payload['convergence_id'],payload)
    return {'convergence_id':payload['convergence_id'],'sha256':payload['sha256']}


def resolve_cases(worker, refs):
    ctx=current_context();records=[];seen=set()
    require(len(refs)>=3,'At least three registered refinement cases required')
    for ref in refs:
        require(isinstance(ref,dict) and set(ref)=={'convergence_id','sha256'},'Only registered references accepted; caller error/settings overrides refused')
        require(ref['convergence_id'] not in seen,'Repeated convergence case refused')
        seen.add(ref['convergence_id'])
        record=ctx['store'].get_metadata('artifacts',ref['convergence_id'])
        require(record and record.get('kind')=='w20_convergence','Unregistered convergence case')
        require(record['sha256']==ref['sha256']==digest({k:v for k,v in record.items() if k!='sha256'}),'Convergence record hash mismatch')
        verify_producer(ctx,record['producer'])
        for key in ('server_instance_id','session_id'):
            require(record['model_ref'].get(key)==ctx['model_ref'].get(key),'Cross-runtime refinement case refused')
        with observation_context(ctx['store'],record['model_ref'],record['revision'],ctx['producer']):
            resolve_observation(worker,record['model_ref']['model_tag'],record['observation_ref'],allow_historical=True)
        records.append(record)
    settings=[(r['elements'],r['max_element_volume'],r['dofs'],r['tolerance'],r['tlist']) for r in records]
    require(len(set(settings))==len(settings),'Repeated native refinement settings refused')
    first=records[0]
    for record in records[1:]:
        for key in ('oracle','tlist','sample_points','coordinate_unit'):
            require(record[key]==first[key],'Refinement observations must use the same benchmark and sampling')
        require(record['source_identity']['computation_version']==first['source_identity']['computation_version'],'Cross-build refinement case refused')
        require(record['source_identity']['times']==first['source_identity']['times'],'Stored output times must remain fixed')
    return records
