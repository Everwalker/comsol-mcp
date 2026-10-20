"""Actual public delivery gateway/storage, original P0 serializer, SDK software boundary."""
from pathlib import Path
import hashlib,json
import pytest
from tools.run_v3_public_mcp_smoke import public_arguments
from tools.p0_package_cycle import action_arguments,recipe
from tests.test_g3_model_delivery import _environment,_execution,_register_recipe,_actual_recipe_engine_model
from comsol_mcp import _g3_model_delivery as delivery
from comsol_mcp._runtime_installation import runtime_id_for_root

@pytest.fixture
def env(tmp_path,monkeypatch):
    daemon,worker,service,project,workspace,ref,install=_environment(tmp_path)
    monkeypatch.setattr(daemon.backend,'_require_g2_isolation',lambda:{'SOFTWARE_SDK_BOUNDARY_NOT_NATIVE':True})
    yield daemon,worker,service,project,workspace,ref,install
    daemon.close()


def request(env,op,body,key='wire'):
    daemon,worker,service,project,workspace,ref,install=env
    execution=_execution(project,ref,revision=service.ledger._models[ref['model_tag']].revision,request=key)
    supplied=action_arguments(op,{**body,'model_ref':ref,'expected_revision':execution['expected_revision'],'idempotency_key':'new'},project['project_id'],ref['session_id'])
    arguments=public_arguments(op,supplied,key)
    return {'operation':'operation_call','arguments':{'operation_id':op,'arguments':arguments},'execution':execution}


def test_actual_public_package_persists_file_and_durable_operation(env):
    daemon,worker,service,project,workspace,ref,install=env
    value=daemon.dispatch(request(env,'model.package',{'destination':'p0-model-package.zip','include_solution':False}))
    assert value['success'],value
    path=workspace/'p0-model-package.zip';assert path.is_file()
    assert hashlib.sha256(path.read_bytes()).hexdigest()==value['data']['sha256']
    assert daemon.store.get_operation(value['execution']['operation_id'])['status']=='SUCCEEDED'
    assert daemon.store.operation_job(value['execution']['operation_id'])['status']=='SUCCEEDED'
    from tools.p0_package_cycle import package_payload
    from tools.run_v3_public_mcp_smoke import verify_artifact
    registered_path=verify_artifact(workspace,value['data']['artifact_registration'])
    assert registered_path.read_bytes()==path.read_bytes()
    manifest,mph=package_payload(registered_path,value['data'],ref)
    assert mph and manifest['model']['model_ref']==ref
    verified=daemon.dispatch({'operation':'operation_call','arguments':{'operation_id':'artifact.verify','arguments':{'project_id':project['project_id'],'artifact_id':value['data']['artifact_registration']['artifact_id']}},'execution':{'project_id':project['project_id']}})
    assert verified['success'],verified
    assert verified['data']['member_integrity']=='PASS'
    assert len(worker.context_calls)==1


def test_actual_public_three_step_rebuild_uses_real_adapters_and_store(env):
    daemon,worker,service,project,workspace,ref,install=env
    model,component,parameters=_actual_recipe_engine_model();worker.models['m']=model
    # The genuine SDK software collection creates the same existing geom1
    # parent as P0 model.create, then the unmodified three-step recipe runs.
    component.collections['geom'].create('geom1',2)
    from tests.test_g3_w16 import FList,FNode
    sequence=component.collections['geom'].items['geom1']
    sequence.declare('feature',FList(arity=2,factory=lambda tag,kind:FNode(tag=tag,type_id=kind,props={'size':{'type':'DoubleArray','value':[1.0,1.0]}})))
    registered=_register_recipe(daemon,project,workspace,worker.endpoint,recipe('6.4.0.293'))
    value=daemon.dispatch(request(env,'model.rebuild_target',{'target_runtime_id':runtime_id_for_root(install),'recipe_artifact':registered['artifact_id']}))
    assert value['success'],json.dumps(value)
    assert value['data']['completed_steps']==3 and value['data']['status']=='APPLIED'
    assert daemon.store.operation_job(value['execution']['operation_id'])['status']=='SUCCEEDED'
    assert len(worker.context_calls)==1

@pytest.mark.parametrize('operation',['model.package','model.rebuild_target'])
@pytest.mark.parametrize('field',['project_id','session_id','model_ref','expected_revision','idempotency_key','request_id'])
def test_nested_identity_refused_by_actual_public_delivery_gate(env,operation,field):
    daemon,worker,service,project,workspace,ref,install=env
    body={'destination':'bad.zip'} if operation=='model.package' else {'target_runtime_id':runtime_id_for_root(install),'recipe_artifact':'a'*64}
    req=request(env,operation,body);req['arguments']['arguments'][field]=req['execution'][field]
    result=daemon.dispatch(req);assert not result['success'],result
    assert result['error']['code']=='INVALID_REQUEST'
    assert not worker.context_calls and not (workspace/'bad.zip').exists()


def test_all_package_cycle_actual_callsite_bodies_use_production_schema(env):
    import ast
    from tools import p0_package_cycle as cycle
    from comsol_mcp._g2_registry import validate_call
    daemon,worker,service,project,workspace,ref,install=env
    namespace={**vars(cycle),'path':'p0-model-recipe.json','role':'model_recipe',
        'metadata':{'artifact_type':'application/json','format_version':'comsol-mcp-model-recipe/1'},
        'revision':0,'created':{'execution':{'revision':0}},'registration':{'artifact_id':'a'*64},
        'image':{'artifact_id':'a'*64},'recipe_source':{'artifact_id':'a'*64},'restored':{'artifact_id':'a'*64},
        'java':{'path':'g2_artifacts/registered/'+('b'*64)+'.java'},'workspace':workspace,
        'runtime_id':runtime_id_for_root(install),'model_ref':ref,'rebuilt_ref':ref,'restored_ref':ref}
    tree=ast.parse(Path(cycle.__file__).read_text());checked=[]
    # Read actual callsite AST dictionary expressions; no independently copied
    # shape plan and no mocked schema/admission. Production
    # registry validates every remaining operation's serialized wire. This
    # is schema preflight, not simulated whole-native dispatch acceptance.
    for node in ast.walk(tree):
        if not isinstance(node,ast.Await) or not isinstance(node.value,ast.Call):continue
        call=node.value
        if not isinstance(call.func,ast.Name) or call.func.id!='action':continue
        op=ast.literal_eval(call.args[0]);body=eval(compile(ast.Expression(call.args[1]),str(cycle.__file__),'eval'),namespace)
        supplied=action_arguments(op,body,project['project_id'],ref['session_id'])
        if any(k.arg=='ref' for k in call.keywords):supplied['model_ref']=ref
        outer=_execution(project,ref,request='schema-'+str(len(checked)))
        serialized=public_arguments(op,supplied,outer['request_id'])
        scoped={**serialized,**{k:v for k,v in outer.items() if k in {'project_id','session_id','model_ref','expected_revision','request_id','idempotency_key'}}}
        if op in {'artifact.preview','artifact.verify'}:scoped=serialized
        validate_call(op,scoped)
        checked.append(op)
    assert {'model.package','model.rebuild_target','model.load','model.save','code.execute_java','artifact.register','artifact.preview','artifact.verify'} <= set(checked)
