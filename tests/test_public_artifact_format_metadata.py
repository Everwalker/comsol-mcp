"""Real public registration/store/rebuild preflight; zero recipe-step effects."""
import copy
import json
from pathlib import Path
import pytest
from comsol_mcp import _g3_model_delivery as delivery
from comsol_mcp._g2_registry import BY_ID,validate_call
from tests.test_g3_model_delivery import _environment,_execution

@pytest.fixture
def case(tmp_path,monkeypatch):
    daemon,worker,service,project,workspace,ref,install=_environment(tmp_path)
    # Synthetic Worker boundary; native isolation/execution is not claimed.
    monkeypatch.setattr(daemon.backend,'_require_g2_isolation',lambda:{'software_only':True})
    from comsol_mcp._g3_ops import DISPATCH
    steps=[]
    monkeypatch.setitem(DISPATCH,'parameter.set',lambda *a,**k:steps.append(a) or pytest.fail('no recipe step may execute in this preflight test'))
    recipe={'schema':delivery.RECIPE_SCHEMA,'version':1,'runtime':{'comsol_versions':['6.3.0.290']},'products':[],
            'steps':[{'operation_id':'parameter.set','arguments':{'parameters':[{'name':'x','expression':'1'}]}}]}
    path=workspace/'recipe.json';path.write_text(json.dumps(recipe))
    yield daemon,project,workspace,ref,path,steps
    daemon.close()

def register(case,*,metadata=True,updates=None,key='register',execution_updates=None):
    daemon,project,workspace,ref,path,steps=case
    body={'project_id':project['project_id'],'path':path.name,'role':'model_recipe','classification':'project_local','idempotency_key':key}
    if metadata:body.update(artifact_type='application/json',format_version=delivery.RECIPE_FORMAT_VERSION)
    body.update(updates or {})
    execution={'project_id':project['project_id'],'request_id':key,'idempotency_key':key};execution.update(execution_updates or {})
    return daemon.dispatch({'operation':'operation_call','arguments':{'operation_id':'artifact.register','arguments':body},'execution':execution})

def rebuild(case,artifact,*,request='rebuild'):
    daemon,project,workspace,ref,path,steps=case
    return daemon.dispatch({'operation':'operation_call','arguments':{'operation_id':'model.rebuild_target','arguments':{
        'target_runtime_id':daemon.backend.worker_identity['runtime_id'],'recipe_artifact':artifact}},'execution':_execution(project,ref,request=request)})

def test_public_typed_registration_reaches_real_recipe_preflight_and_zero_step_boundary(case,monkeypatch):
    daemon,project,workspace,ref,path,steps=case
    parsed=[];prepared=[]
    original=delivery._read_registered_recipe;validate=delivery._validate_recipe_steps
    def read(*a,**k):
        result=original(*a,**k);parsed.append(result);return result
    def preflight(*a,**k):
        result=validate(*a,**k);prepared.append(result);return result
    monkeypatch.setattr(delivery,'_read_registered_recipe',read);monkeypatch.setattr(delivery,'_validate_recipe_steps',preflight)
    registered=register(case);assert registered['success'] is True,registered
    data=registered['data'];record=daemon.store.get_metadata('artifacts',data['artifact_id'])
    assert record['artifact_type']==data['artifact_type']=='application/json'
    assert record['format_version']==data['format_version']==delivery.RECIPE_FORMAT_VERSION
    again=register(case);assert again['success'] and again['data']['artifact_id']==data['artifact_id']
    result=rebuild(case,data['artifact_id'])
    assert not result['success'] and result['error']['code']=='RUNTIME_VERSION_MISMATCH',result
    assert parsed and prepared and not steps
    assert daemon.store.get_metadata('artifacts',data['artifact_id'])==record

@pytest.mark.parametrize('field',['artifact_type','format_version'])
@pytest.mark.parametrize('value',[None,True,1,[],{},'', '  ', 'x'*129])
def test_declared_metadata_type_nonempty_and_existing_store_bound_are_public_closed_schema(case,field,value):
    result=register(case,updates={field:value})
    assert not result['success'] and not case[-1]

@pytest.mark.parametrize('updates',[{'artifact_type':'text/plain'},{'format_version':'comsol-mcp-model-recipe/2'},{'role':'geometry_source'}])
def test_wrong_typed_recipe_metadata_stays_consumer_refusal_before_any_step(case,updates):
    registered=register(case,updates=updates);assert registered['success'],registered
    result=rebuild(case,registered['data']['artifact_id'])
    assert not result['success'] and result['error']['code']=='ARTIFACT_ROLE_MISMATCH',result
    assert not case[-1]

def test_legacy_registration_remains_untyped_and_is_not_inferred_into_recipe_acceptance(case):
    registered=register(case,metadata=False);assert registered['success']
    record=case[0].store.get_metadata('artifacts',registered['data']['artifact_id'])
    assert 'artifact_type' not in record and 'format_version' not in record
    assert 'artifact_type' not in registered['data'] and 'format_version' not in registered['data']
    result=rebuild(case,registered['data']['artifact_id']);assert result['error']['code']=='ARTIFACT_ROLE_MISMATCH'
    assert not case[-1]

@pytest.mark.parametrize('raw',['not JSON','{}','{"schema":"wrong","version":1}','{"schema":"a","schema":"b"}'])
def test_typed_metadata_does_not_bypass_actual_recipe_json_and_closed_recipe_schema(case,raw):
    case[4].write_text(raw)
    registered=register(case);assert registered['success']
    result=rebuild(case,registered['data']['artifact_id'])
    assert not result['success'] and result['error']['code']=='RECIPE_INVALID',result
    assert not case[-1]

@pytest.mark.parametrize('fault',['relative_escape','absolute','symlink','wrong_project','wrong_key','extra_proof','no_permission'])
def test_public_metadata_addition_preserves_path_permission_identity_and_closed_schema(case,fault,tmp_path):
    updates={};execution={}
    if fault=='relative_escape':updates['path']='../outside.json'
    elif fault=='absolute':updates['path']=str(case[4])
    elif fault=='symlink':
        outside=tmp_path/'outside.json';outside.write_text('{}');link=case[2]/'alias.json';link.symlink_to(outside);updates['path']=link.name
    elif fault=='wrong_project':updates['project_id']='other'
    elif fault=='wrong_key':execution['idempotency_key']='other'
    elif fault=='extra_proof':updates['native_acceptance']='PASS'
    elif fault=='no_permission':case[0].backend.service.ledger.permissions.discard('project_write')
    result=register(case,updates=updates,execution_updates=execution)
    assert not result['success'] and not case[-1],result

def test_registration_metadata_cannot_replace_registered_immutable_origin(case):
    registered=register(case);assert registered['success']
    daemon=case[0];key=registered['data']['artifact_id'];record=daemon.store.get_metadata('artifacts',key)
    altered=copy.deepcopy(record);altered['format_version']='wrong'
    with pytest.raises(ValueError,match='immutable'):daemon.store.put_metadata('artifacts',key,altered)
    conflicting=register(case,updates={'format_version':'wrong'},key='different-register')
    assert not conflicting['success'],conflicting
    assert daemon.store.get_metadata('artifacts',key)==record and not case[-1]

def test_registered_file_tamper_still_refused_before_recipe_steps(case):
    registered=register(case);assert registered['success']
    stored=case[0].store.get_metadata('artifacts',registered['data']['artifact_id'])
    path=Path(stored['path']);path=path if path.is_absolute() else case[2]/path
    path.write_bytes(path.read_bytes()+b' ')
    result=rebuild(case,registered['data']['artifact_id'])
    assert not result['success'] and not case[-1]

def test_packaged_and_documented_catalogs_have_exact_same_optional_metadata_schema():
    root=Path(__file__).resolve().parents[1]
    catalogs=[json.loads((root/path).read_text()) for path in ['comsol_mcp/data/g2/02_ACTION_CATALOG.json','docs/comsol_mcp_design_v1/02_ACTION_CATALOG.json']]
    entries=[next(row for row in catalog['operations'] if row['operation_id']=='artifact.register') for catalog in catalogs]
    assert entries[0]==entries[1]
    assert set(entries[0]['input_schema']['properties'])=={'project_id','idempotency_key','request_id','path','role','classification','artifact_type','format_version'}
    assert entries[0]['input_schema']['additionalProperties'] is False


def test_published_json_schema_itself_admits_normal_tags_and_refuses_whitespace():
    from jsonschema import validate,ValidationError
    schema=BY_ID['artifact.register'].as_dict()['input_schema']
    body={'project_id':'p','idempotency_key':'i','path':'recipe.json','role':'model_recipe','classification':'project_local',
          'artifact_type':'application/json','format_version':delivery.RECIPE_FORMAT_VERSION}
    validate(body,schema)
    for key in ('artifact_type','format_version'):
        with pytest.raises(ValidationError):validate({**body,key:'  '},schema)
