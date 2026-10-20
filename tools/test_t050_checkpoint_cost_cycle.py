"""T050 source/readback guards and actual public checkpoint route; SDK seam only."""
from pathlib import Path
import json,hashlib,copy,ast
import pytest
from tools import t050_checkpoint_cost_cycle as cost
from tools.run_v3_public_mcp_smoke import public_arguments
from tests.test_g3_managed_models import _Worker,_Java,_runtime_root
from comsol_mcp._control_daemon import ControlDaemon
from comsol_mcp._execution_contract import SessionLedger
from comsol_mcp._execution_service import ExecutionService


def test_cost_scale_batches_and_readback_guards():
    assert cost.PARAMETERS==4096 and cost.RECTANGLES==128
    wanted=cost.parameters_changed();assert len(wanted)==512
    data={'parameters':[{**v,'unit':'m','evaluated':{'kind':'float64','shape':[],'data':(i+11)/1000}} for i,v in enumerate(wanted)]}
    assert cost.verify_parameters(data)==data['parameters']
    data['parameters'][0]['unit']='mm'
    with pytest.raises(RuntimeError):cost.verify_parameters(data)
    data['parameters'][0]['unit']='m';data['parameters'][0]['evaluated']['data']=True
    with pytest.raises(RuntimeError):cost.verify_parameters(data)


def test_disk_samples_and_geometry_guard(tmp_path):
    (tmp_path/'one.mph').write_bytes(b'originalmodel')
    assert cost.sample_disk(tmp_path)['sampled_bytes']==13
    (tmp_path/'alias').symlink_to(tmp_path/'one.mph')
    with pytest.raises(RuntimeError):cost.sample_disk(tmp_path)
    data={'features':[{'tag':f't050_r{i:03d}'} for i in range(128)],'geometry_state':{'dimension':2}}
    cost.verify_geometry(data)
    data['features'].append({'tag':'t050_extra'})
    cost.verify_geometry(data,extra=True)
    with pytest.raises(RuntimeError):cost.verify_geometry(data)


def _environment(tmp_path,monkeypatch,*,bound_project=True,permissions=None):
    projects=tmp_path/'projects';projects.mkdir();worker=_Worker(_runtime_root(tmp_path/'install'),'127.0.0.1:2001');saves=[]
    def save(path):
        import zipfile
        saves.append(str(path))
        with zipfile.ZipFile(path,'w') as archive:archive.writestr('model/software-state.txt','SOFTWARE_SDK_CHECKPOINT_BYTES')
    def typed_model(tag,path=""):
        from tests.test_g3_model_delivery import _actual_recipe_engine_model
        from tests.test_g3_w16 import FList,FNode
        value=_Java(tag,path)
        engine,component,parameters=_actual_recipe_engine_model()
        engine.tag_=tag
        component.collections['geom'].create('geom1',2)
        result=FNode('result',state={'tags':[],'dataset':FList()})
        engine.state['result']=result
        parameters.set('t050_probe','2.5[mm]','restoreprobe')
        from comsol_mcp._model_ops import _model_tree_data
        engine._call=value._call
        value.java=engine;value.save=save;value.param=engine.param;value.component=engine.component
        _model_tree_data(value)
        return value
    worker.models['m']=typed_model('m')
    def load(path,*,tag):
        value=typed_model(tag,str(path));worker.models[tag]=value;return value
    worker._client.load=load
    worker.model=worker._client.model
    # The real selection pointer, service, metadata and follow-up routes run.
    service=ExecutionService(SessionLedger('s','server',permissions=set(permissions or {'inspect','project_write','compute'})),worker,project_root=projects)
    daemon=ControlDaemon(tmp_path/'control',service=service,worker=worker,project_root=projects)
    daemon.backend.endpoint_key=worker.endpoint
    monkeypatch.setattr(daemon.backend,'_require_g2_isolation',lambda:{'SOFTWARE_SDK_SEAM_NOT_NATIVE':True})
    from comsol_mcp._runtime_installation import runtime_id_for_root
    daemon.backend.worker_identity={'runtime_id':runtime_id_for_root(worker.paths.comsol_root),'worker_instance_id':worker.instance_id,'connection_epoch':1,'server_instance_id':'server','endpoint':worker.endpoint}
    project=daemon.dispatch({'operation':'project.create','arguments':{'label':'T050','workspace':'T050','policy':{'permissions':['inspect','project_write','compute']}},'execution':{'request_id':'p','idempotency_key':'p'}})['data']['project'];ref=service.bind_model('m',ownership='mcp_owned')['execution']['model_ref'];daemon.backend._bind_model_project(ref,project['project_id'] if bound_project else None);daemon.backend.persist()
    worker.test_loads=[]
    original_load=worker._client.load
    def recorded_load(*a,**kw):
        worker.test_loads.append((a,kw));return original_load(*a,**kw)
    worker._client.load=recorded_load
    def call(op,body,key,ref):
        execution={'session_id':'s','model_ref':ref,'expected_revision':service.ledger._models[ref['model_tag']].revision,'idempotency_key':key,'request_id':key}
        if bound_project:execution['project_id']=project['project_id']
        arguments=public_arguments(op,body if op=='model.inspect' else {**body,'project_id':project['project_id'],'session_id':'s','model_ref':ref,'expected_revision':execution['expected_revision'],'idempotency_key':'new'},key)
        return daemon.dispatch({'operation':'operation_call','arguments':{'operation_id':op,'arguments':arguments},'execution':execution})
    return daemon,worker,service,project,ref,call,saves


def test_public_checkpoint_create_restore_followed_by_current_model_operations(tmp_path,monkeypatch):
    daemon,worker,service,project,ref,call,saves=_environment(tmp_path,monkeypatch)
    try:
        checkpoint=call('checkpoint.create',{'label':'before-structure','include_solution':False},'checkpoint',ref);assert checkpoint['success'],json.dumps(checkpoint)
        assert checkpoint['data']['save_count']==1 and len(saves)==1
        restored=call('checkpoint.restore',{'checkpoint_id':checkpoint['data']['checkpoint_id'],'authorization_ref':'explicitownedT050'},'restore',ref);assert restored['success'],restored
        newref=restored['execution']['model_ref'];assert newref!=ref
        inspected=call('model.inspect',{},'inspect-restored',newref);assert inspected['success'],inspected
        parameters=call('parameter.get',{'names':['t050_probe'],'evaluate':True},'read-restored-parameters',newref);assert parameters['success'],parameters
        geometry=call('geometry.inspect',{'path':cost.GEOMETRY},'read-restored-geometry',newref);assert geometry['success'],geometry
        saved=call('model.save',{'destination':'restored.mph','overwrite':False,'include_solution':False},'save-restored',newref);assert saved['success'],json.dumps(saved)
    finally:daemon.close()

@pytest.mark.parametrize('failure',['persist','bind_callback'])
def test_public_restore_postload_persistence_failure_is_unknown_and_retains_refs(tmp_path,monkeypatch,failure):
    daemon,worker,service,project,ref,call,saves=_environment(tmp_path,monkeypatch)
    try:
        checkpoint=call('checkpoint.create',{'label':'source','include_solution':False},'source',ref);assert checkpoint['success'],checkpoint
        def fail():raise OSError('deliberate persistence failure')
        monkeypatch.setattr(daemon.backend,'persist',fail)
        if failure=='bind_callback':service.on_state_change=lambda event:daemon.backend.persist()
        result=call('checkpoint.restore',{'checkpoint_id':checkpoint['data']['checkpoint_id'],'authorization_ref':'owned'},'restore-failure',ref)
        assert not result['success'] and result['error']['code']=='EXECUTION_STATE_UNKNOWN',result
        assert result['error'].get('safe_retry') is False
        assert len(worker.test_loads)==1 and not worker.removed
        assert len([s for s in service.ledger._models.values() if s.dirty])==2
        assert daemon.store.operation_job(result['execution']['operation_id'])['status']=='UNKNOWN'
    finally:daemon.close()


def test_wrong_project_public_restore_never_loads(tmp_path,monkeypatch):
    daemon,worker,service,project,ref,call,saves=_environment(tmp_path,monkeypatch)
    try:
        checkpoint=call('checkpoint.create',{'label':'source','include_solution':False},'source',ref);assert checkpoint['success']
        result=daemon.dispatch({'operation':'operation_call','arguments':{'operation_id':'checkpoint.restore','arguments':{'checkpoint_id':checkpoint['data']['checkpoint_id'],'idempotency_key':'wrong'}},'execution':{'project_id':'other-project','session_id':'s','model_ref':ref,'expected_revision':service.ledger._models['m'].revision,'request_id':'wrong','idempotency_key':'wrong'}})
        assert not result['success'] and result['error']['code']=='PROJECT_IDENTITY_MISMATCH'
        assert not worker.test_loads
    finally:daemon.close()


def test_legacy_unattributed_restore_stays_unattributed(tmp_path,monkeypatch):
    daemon,worker,service,project,ref,call,saves=_environment(tmp_path,monkeypatch,bound_project=False)
    try:
        # Use the original direct legacy interface without a project claim.
        checkpoint=daemon.backend._checkpoint_via_service(__import__('comsol_mcp._execution_contract',fromlist=['model_ref_from_mapping']).model_ref_from_mapping(ref),{'label':'legacy'},'checkpoint_create','legacy-source',{'expected_revision':0,'session_id':'s','request_id':'legacy-source'})
        assert checkpoint['success'],checkpoint
        result=daemon.backend._restore_checkpoint(__import__('comsol_mcp._execution_contract',fromlist=['model_ref_from_mapping']).model_ref_from_mapping(ref),{'checkpoint_id':checkpoint['data']['checkpoint_id'],'authorization_ref':'legacy'},'legacy-restore',None,{'expected_revision':service.ledger._models['m'].revision})
        assert result['success'],result
        assert daemon.backend.model_project_binding(result['execution']['model_ref'])=={'attribution':'UNATTRIBUTED','project_id':None}
    finally:daemon.close()


def production_save_receipts():
    import threading
    from comsol_mcp._java_worker import PersistentJavaWorker
    worker=object.__new__(PersistentJavaWorker);worker._operation_context=threading.local();rows=[]
    worker._on_request_event=rows.append
    for i,method in enumerate(['saveModelPackage','save','saveModelPackage']):
        worker._operation_context.value=('save-operation-'+str(i),None)
        payload={'handle':'original-model','method':method,'args':['task-owned-temp.mph']}
        worker._emit_request_event('submitted',request_id='request-'+str(i),kind='call',payload=payload)
        worker._emit_request_event('observed',request_id='request-'+str(i),kind='call',payload=payload,status='SUCCEEDED',reply={'ok':True})
    return rows


def test_actual_production_worker_receipt_save_pair_consumer():
    value=cost.audit_native_save_events(production_save_receipts(),['batch'])
    assert value['observed_full_model_save_count']==3 and value['grouped_parameter_full_save_count']==0

@pytest.mark.parametrize('damage',['missing','hash','duplicate','batchsave','notterminal','method'])
def test_native_save_trace_consumer_strictly_refuses_missing_tampered_or_per_setter_save(damage):
    rows=production_save_receipts()
    if damage=='missing':rows.pop()
    elif damage=='hash':rows[-1]['request_hash']='0'*64
    elif damage=='duplicate':rows.append(copy.deepcopy(rows[0]))
    elif damage=='batchsave':rows[0]['operation_id']='batch'
    elif damage=='notterminal':rows[-1]['status']='UNKNOWN'
    elif damage=='method':rows[0]['metadata']['method']='save';rows[1]['metadata']['method']='save'
    with pytest.raises(RuntimeError):cost.audit_native_save_events(rows,['batch'])

@pytest.mark.parametrize('field',['unit_error','evaluate_errors'])
def test_parameter_readback_errors_cannot_be_promoted(field):
    wanted=cost.parameters_changed()
    data={'parameters':[{**v,'unit':'m','evaluated':{'kind':'float64','shape':[],'data':(i+11)/1000}} for i,v in enumerate(wanted)]}
    data['parameters'][0][field]={'message':'actual SDK read failed'}
    with pytest.raises(RuntimeError):cost.verify_parameters(data)


def test_all_t050_actual_callsite_bodies_use_original_serializer_and_production_schema(tmp_path,monkeypatch):
    from comsol_mcp._g2_registry import validate_call
    daemon,worker,service,project,ref,call,saves=_environment(tmp_path,monkeypatch)
    common={'project_id':project['project_id'],'session_id':'s','model_ref':ref}
    namespace={**vars(cost),'fixture_path':project['workspace'] if False else Path('T050CostModelFixture.java'),
        'common':common,'project_id':project['project_id'],'revision':0,'i':0,
        'rows':cost.parameters_changed(),'model_ref':ref,'ref':ref,'cp':{'checkpoint_id':'cp'},
        'registered':{'data':{'path':'g2_artifacts/registered/'+('b'*64)+'.java'}}}
    namespace['model_body']=lambda body:{**common,**body}
    checked=[]
    try:
        for node in ast.walk(ast.parse(Path(cost.__file__).read_text())):
            if not isinstance(node,ast.Await) or not isinstance(node.value,ast.Call):continue
            value=node.value
            if not isinstance(value.func,ast.Name) or value.func.id!='action':continue
            operation=ast.literal_eval(value.args[0]);body=eval(compile(ast.Expression(value.args[1]),cost.__file__,'eval'),namespace)
            serialized=public_arguments(operation,body,'schema'+str(len(checked)))
            # These identity claims are the existing public ExecutionService
            # injection, not extra client inner fields or a mock admission.
            scoped={**serialized,**common,'expected_revision':0,'request_id':'shape','idempotency_key':'shape'}
            if operation in {'artifact.register','checkpoint.list'}:scoped=serialized
            validate_call(operation,scoped);checked.append(operation)
        assert len(checked)==17 and {'code.execute_java','checkpoint.create','checkpoint.restore','geometry.feature_create','model.save'}<=set(checked)
    finally:daemon.close()

@pytest.mark.parametrize('cost_mode',[True,False])
def test_actual_t050_project_callsite_grants_only_selected_fixed_code_permission(tmp_path,monkeypatch,cost_mode):
    from types import SimpleNamespace
    from tools import run_v3_public_mcp_smoke as client
    from comsol_mcp._execution_contract import ExecutionContractError
    monkeypatch.setenv('COMSOL_MCP_TRUSTED_CODE','1')
    monkeypatch.setenv('COMSOL_MCP_HOST_CONTROL','1')
    daemon,worker,service,project,ref,call,saves=_environment(tmp_path,monkeypatch,permissions={'inspect','project_write','compute','host_control','trusted_code'})
    try:
        nodes=[n.value for n in ast.walk(ast.parse(Path(client.__file__).read_text())) if isinstance(n,ast.Await) and isinstance(n.value,ast.Call) and isinstance(n.value.func,ast.Name) and n.value.func.id=='call' and n.value.args and isinstance(n.value.args[0],ast.Constant) and n.value.args[0].value=='project.create']
        assert len(nodes)==1
        body=eval(compile(ast.Expression(nodes[0].args[1]),client.__file__,'eval'),{'root':Path('T050policy'),'cost':cost_mode,'science':False,'thermal':False,'args':SimpleNamespace(runtime_observation=False,package_cycle=False)})
        value=daemon.dispatch({'operation':'operation_call','arguments':{'operation_id':'project.create','arguments':public_arguments('project.create',body,'cost-project')},'execution':{'request_id':'cost-project','idempotency_key':'cost-project'}})
        assert value['success'],value
        pid=value['data']['project']['project_id']
        if cost_mode:
            persisted=daemon.project_authority.authorize_operation(pid,'trusted_code')
            assert 'trusted_code' in persisted['policy']['permissions']
        else:
            with pytest.raises(ExecutionContractError) as error:daemon.project_authority.authorize_operation(pid,'trusted_code')
            assert error.value.code=='PERMISSION_DENIED'
        assert not worker.context_calls if hasattr(worker,'context_calls') else True
    finally:daemon.close()

@pytest.mark.parametrize('lost_save_revision',[False,True])
def test_original_client_save_to_batches_revision_flow_uses_actual_public_gateway(tmp_path,monkeypatch,lost_save_revision):
    import asyncio
    daemon,worker,service,project,ref,call,saves=_environment(tmp_path,monkeypatch)
    workspace=Path(project['workspace']);events=[]
    common={'project_id':project['project_id'],'session_id':'s','model_ref':ref}
    async def action(operation,arguments,ref=ref,rev=None):
        key='actual-prefix-'+str(len(events));execution={'project_id':project['project_id'],'session_id':'s','model_ref':ref,'request_id':key,'idempotency_key':key}
        if rev is not None:execution['expected_revision']=rev
        value=daemon.dispatch({'operation':'operation_call','arguments':{'operation_id':operation,'arguments':public_arguments(operation,arguments,key)},'execution':execution})
        events.append((operation,value))
        if not value['success']:raise RuntimeError(value['error']['code'])
        return value
    body=next(n for n in ast.parse(Path(cost.__file__).read_text()).body if isinstance(n,ast.AsyncFunctionDef) and n.name=='run_cycle').body
    def assignment(n,name):return isinstance(n,ast.Assign) and any(isinstance(t,ast.Name) and t.id==name for t in n.targets)
    start=next(i for i,n in enumerate(body) if assignment(n,'baseline'));end=next(i for i,n in enumerate(body[start:],start) if assignment(n,'observed'))
    segment=copy.deepcopy(body[start:end])
    if lost_save_revision:segment=[n for n in segment if not (assignment(n,'revision') and isinstance(n.value,ast.Subscript))]
    # Execute the original client prefix AST, not a copied state machine or
    # test wrapper that silently fetches the latest ledger revision. The
    # production save/package and parameter adapters, revision gate, files and
    # operation/job store run; model SDK seam is the explicitly software model.
    tree=ast.Module(body=[ast.AsyncFunctionDef(name='prefix',args=ast.arguments(posonlyargs=[],args=[ast.arg(arg='revision')],kwonlyargs=[],kw_defaults=[],defaults=[ast.Constant(value=0)]),body=segment+[ast.Return(value=ast.Name(id='revision',ctx=ast.Load()))],decorator_list=[])],type_ignores=[])
    namespace={**vars(cost),'revision':0,'workspace':workspace,'action':action,'digest':lambda p:hashlib.sha256(p.read_bytes()).hexdigest(),'common':common,'project_id':project['project_id'],'model_ref':ref,'model_body':lambda b:{**common,**b}}
    exec(compile(ast.fix_missing_locations(tree),cost.__file__,'exec'),namespace)
    try:
        if lost_save_revision:
            with pytest.raises(RuntimeError,match='REVISION_CONFLICT'):asyncio.run(namespace['prefix']())
            assert len(events)==3 and events[-1][1]['error']['safe_retry'] is False
        else:
            assert asyncio.run(namespace['prefix']())==5
            assert [op for op,value in events]==['model.save','checkpoint.list']+['parameter.set']*4
            assert all(value['success'] for op,value in events) and (workspace/'t050-baseline.mph').is_file()
            rows=call('parameter.get',{'names':[v['name'] for v in cost.parameters_changed()],'evaluate':False},'actual512read',ref)['data']['parameters']
            assert [{k:v[k] for k in ('name','expression','description')} for v in rows]==cost.parameters_changed()
            assert len([x for x in service.ledger._models.values() if not x.retired])==1
    finally:daemon.close()
