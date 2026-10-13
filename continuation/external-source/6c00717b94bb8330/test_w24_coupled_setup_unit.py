"""Source-only real setup runner/fence regression; SDK/OS boundary is synthetic."""
import time
import copy
import hashlib
import json
from uuid import uuid4
from pathlib import Path
from types import SimpleNamespace
import pytest
from tools import w24_coupled_setup_unit as unit
from tools.run_native_w24_cure_science import ManagedModelBinding
from tools import run_native_w24_static_shape_setup_campaign as campaign
from test_w24_static_shape_managed import FakeDaemon,PROJECT_ID,SESSION_ID,SERVER_INSTANCE
from comsol_mcp._operation_store import OperationStore
from comsol_mcp._execution_contract import ExecutionContractError
from tools.w24_native_epoch_capability import _mint_from_actual_cure_response,_capture_original_cure_receiver
from comsol_mcp._control_daemon import ControlDaemon
from comsol_mcp._managed_backend import ManagedBackend
from comsol_mcp._session_context import CanonicalSocket
from types import MethodType
from tools.w24_coupled_admission import _new_coupled_admission
SDK_ENDPOINT="127.0.0.1:56388"

class Transport(FakeDaemon):
    """SDK/transport fixture only; actual runner/fence/store/origin checks run."""
    worker_generation=7 # Deliberately distinct from ModelRef generation=1.
    drift=None
    def __init__(self,workspace):
        super().__init__(workspace)
        self.backend.worker=object()
        self.backend.worker_identity={'server_instance_id':SERVER_INSTANCE,'worker_instance_id':'actual-transport-worker',
            'connection_epoch':7,'endpoint':SDK_ENDPOINT,'observed_peer':{'address':'127.0.0.1','port':56388},
            'remote_engine_version':'6.4.0.293'}
        self.backend._session_server_instance_id=ManagedBackend._session_server_instance_id
        self.session_registry._context.worker_epoch=7
        self.session_registry._context.worker_instance_id='actual-transport-worker'
        # Isolated SDK transport tests reuse the production resolver methods.
        # Separate tests exercise actual constructed Controller/ManagedBackend/Store for both receiver kinds.
        self._resolve_job_runtime_context=MethodType(ControlDaemon._resolve_job_runtime_context,self)
        self._execution_session_context=MethodType(ControlDaemon._execution_session_context,self)
        self._runtime_binding_for_request=MethodType(ControlDaemon._runtime_binding_for_request,self)
        self.store=OperationStore(workspace.parent/'operations.sqlite3')

    def persist(self,request,response):
        metadata=copy.deepcopy(request)
        metadata['runtime_binding']={'kind':'registered_session','project_id':PROJECT_ID,'session_id':SESSION_ID,
            'worker_instance_id':'actual-transport-worker','worker_epoch':self.worker_generation}
        request_id=request['execution']['request_id']
        op,_=self.store.begin(request_id=request_id,idempotency_key=request_id,
            request_hash=hashlib.sha256(request_id.encode()).hexdigest(),
            operation=request['operation'],metadata=metadata)
        response['execution'].update(operation_id=op['operation_id'],job_id=op['job_id'])
        java=request['operation']=='operation_call'
        if java:
            ref=request['execution']['model_ref']
            audit={'model_tag':ref['model_tag'],'instance_id':'actual-transport-worker',
                'worker_instance_id':'actual-transport-worker','generation':self.worker_generation,
                'server_instance_id':SDK_ENDPOINT}
            response['data'].update(before=copy.deepcopy(audit),after=copy.deepcopy(audit))
            if self.drift=='generation':response['data']['after']['generation']+=1
            if self.drift=='missing':response['data'].pop('before')
            if self.drift=='model':response['data']['after']['model_tag']='foreign'
        status='SUCCEEDED' if response['success'] else self.build_outcome
        self.store.finish(op['operation_id'],status=status,result=copy.deepcopy(response))
        if self.drift=='origin' and java:response['data']['after']['generation']+=1
        if self.drift=='after_live' and java:self.backend.worker=object()
        return response

    def dispatch(self, request):
        args=request.get('arguments',{}).get('arguments',{})
        if args.get('entrypoint')=='W24StaticShapeFixture#run':
            self._current_case=args['arguments']['case_id']
            self._shape_tag='shape-'+self._current_case
            self._loaded_tag='reopened-'+self._current_case
        response=super().dispatch(request)
        return self.persist(request,response)

    def seed_cure_capability(self):
        req={'operation':'operation_call','arguments':{'operation_id':'code.execute_java',
                'arguments':{'source_artifact':'W24CureCouponFixture.java','entrypoint':'W24CureCouponFixture#run',
                    'arguments':{'phase':'build_v2','mu_scale':1.0}}},
             'execution':{'project_id':PROJECT_ID,'session_id':SESSION_ID,
                          'model_ref':self.parent,'request_id':str(uuid4()),'expected_revision':0}}
        readback={'status':'SYNTHETIC_SDK_CURE_CONTEXT_NO_NATIVE_ACCEPTANCE'}
        response=self._java_response(readback,self.parent,req['execution'],1)
        original_receiver=_capture_original_cure_receiver(self,project_id=PROJECT_ID,model_ref=self.parent)
        response=self.persist(req,response)
        return _mint_from_actual_cure_response(self,response,project_id=PROJECT_ID,
                                               model_ref=self.parent,revision=1,original_receiver=original_receiver)

@pytest.fixture
def harness(tmp_path,monkeypatch):
    workspace=tmp_path/'project'/'workspace';workspace.mkdir(parents=True)
    global SERVER_INSTANCE
    SERVER_INSTANCE=ManagedBackend._session_server_instance_id(CanonicalSocket('127.0.0.1',56388),
        {'instance_id':'actual-transport-worker','generation':7,'engine_version':'6.4.0.293'})
    import test_w24_static_shape_managed as fixture_module
    monkeypatch.setattr(fixture_module,'SERVER_INSTANCE',SERVER_INSTANCE)
    daemon=Transport(workspace)
    birth=int(time.time())
    identity=lambda pid:{'alive':True,'start_epoch_ms':birth*1000,'pid':pid}
    import comsol_mcp._platform_process as process
    monkeypatch.setattr(process,'process_identity',identity)
    real=campaign.SampledRssMonitor
    def monitor(**kwargs):
        root=str(kwargs['worker_search_root'])
        probe=lambda *a,**kw:SimpleNamespace(returncode=0,stderr='',stdout=f'101 1 101 1024 server\n102 1 102 1024 {root}/PersistentComsolWorker\n{__import__("os").getpid()} 1 99999 1 observing-cli\n')
        return real(**kwargs,ps_runner=probe,identity_reader=identity,session_reader=lambda pid:pid)
    monkeypatch.setattr(campaign,'SampledRssMonitor',monitor)
    class Process:
        def __init__(self,pid):self.pid=pid
        def poll(self):return None
    server=SimpleNamespace(proc=Process(101),work=tmp_path)
    owner=_new_coupled_admission(server,tmp_path)
    owner.on_server_birth();owner.bind_child(Process(102),'worker')
    arguments=dict(daemon=daemon,project_id=PROJECT_ID,workspace=workspace,
        project_response={'success':True,'data':{'project':{'project_id':PROJECT_ID,'workspace':str(workspace)}}},
        parent_binding=ManagedModelBinding(PROJECT_ID,SESSION_ID,daemon.parent,0),
        evidence=tmp_path/'evidence',server_pid=101,worker_pid=102,
        server_birth_epoch=owner.birth_epoch,deadline_epoch=owner.deadline_epoch,
        native_epoch_capability=daemon.seed_cure_capability(),admission_owner=owner)
    yield arguments
    owner.monitor.stop()

def test_real_serial_managed_runner_fence_save_reopen_and_no_solve(harness):
    result=unit.build_static_baseline_setups(**harness)
    assert [r['case_id'] for r in result['cases']]==['flat','step']
    assert len({r['new_model_binding']['model_tag'] for r in result['cases']})==2
    assert all(r['readback_comparison']['matches'] for r in result['cases'])
    assert all(r['study_run_submission_count']==0 for r in result['cases'])
    assert result['scientific_acceptance']=='NOT_ACCEPTED'
    assert result['native_stable_shapes']=='NOT_CAPTURED'
    assert len(harness['daemon'].calls)==16

@pytest.mark.parametrize('failure',['UNKNOWN','FAILED'])
def test_first_transport_failure_blocks_second_baseline(harness,failure):
    harness['daemon'].build_outcome=failure
    with pytest.raises(Exception):unit.build_static_baseline_setups(**harness)
    assert len(harness['daemon'].calls)==1

def test_birth_inclusive_cleanup_reserve_blocks_all_dispatch(harness):
    owner=harness['admission_owner'];owner.deadline_epoch=time.time()+599
    owner.deadline_monotonic=time.monotonic()+599
    harness['deadline_epoch']=owner.deadline_epoch
    with pytest.raises((RuntimeError,TimeoutError),match='budget|reserve'):unit.build_static_baseline_setups(**harness)
    assert harness['daemon'].calls==[]

def test_reopen_physical_drift_blocks_second_baseline(harness):
    harness['daemon'].mutate_reopen=True
    with pytest.raises(Exception):unit.build_static_baseline_setups(**harness)
    assert all(req.get('arguments',{}).get('arguments',{}).get('arguments',{}).get('case_id')!='step'
               for _,req in harness['daemon'].calls)

@pytest.mark.parametrize('change',[{'server_birth_budget_seconds_including_cleanup':900},
 {'Study_solver_PhaseInitialization_submissions':1},{'original_sensitivity_slots_required':2},
 {'static_baselines':['flat']},{'extra':True},
 {'owned_servers':True},{'GUI':False},{'mu_scale':float('nan')}])
def test_closed_setup_contract_refuses_scope_drift(change):
    contract=unit.setup_contract();contract.update(change)
    with pytest.raises(ValueError):unit.validate_setup_contract(contract)

@pytest.mark.parametrize('value',[None,{}, {'instance_id':'actual-transport-worker','generation':7,'server':SERVER_INSTANCE}])
def test_coupled_branch_cannot_accept_caller_json_epoch(harness,value):
    harness['native_epoch_capability']=value
    with pytest.raises(ValueError,match='capability'):unit.build_static_baseline_setups(**harness)
    assert harness['daemon'].calls==[]
    assert not harness['evidence'].exists()

@pytest.mark.parametrize('drift',['generation','missing','model','origin','after_live'])
def test_actual_each_java_audit_or_origin_failure_blocks_next_route(harness,drift):
    harness['daemon'].drift=drift
    with pytest.raises(Exception):unit.build_static_baseline_setups(**harness)
    assert len(harness['daemon'].calls)==1
    route_results=list(harness['evidence'].glob('managed_routes/*_result.json'))
    assert len(route_results)==1 # Actual terminal response preserved before guard.
    assert json.loads(route_results[0].read_bytes())['response']['success'] is True

def test_changed_live_worker_blocks_first_route(harness):
    harness['daemon'].backend.worker=object()
    with pytest.raises(ValueError,match='identity'):unit.build_static_baseline_setups(**harness)
    assert harness['daemon'].calls==[]

def test_actual_epoch_is_distinct_from_managed_model_generation(harness):
    cap=harness['native_epoch_capability']
    assert cap.as_evidence()['generation']==7
    assert harness['parent_binding'].model_ref['generation']==1
    result=unit.build_static_baseline_setups(**harness)
    assert result['actual_cure_worker_epoch']['generation']==7
    assert all(row['new_model_binding']['model_ref']['generation']==1 for row in result['cases'])

@pytest.mark.parametrize('change',['revision','model_ref','request_id','project','runtime_epoch','operation_type','old_v1_build'])
def test_operation_store_exact_origin_or_identity_is_required(harness,change):
    daemon=harness['daemon']
    original_receiver=_capture_original_cure_receiver(daemon,project_id=PROJECT_ID,model_ref=daemon.parent)
    op=daemon.store.db.execute("select operation_id,result from operations limit 1").fetchone()
    response=json.loads(op[1])
    execution=response['execution']
    if change=='revision':execution['revision']+=1
    elif change=='model_ref':execution['model_ref']['model_tag']='foreign'
    elif change=='request_id':execution['request_id']='foreign'
    elif change=='project':execution['project_id']='foreign'
    else:
        row=daemon.store.get_operation(op[0]);metadata=row['metadata']
        if change=='runtime_epoch':metadata['runtime_binding']['worker_epoch']=8
        elif change=='old_v1_build':metadata['arguments']['arguments']['arguments']['phase']='build'
        else:metadata['arguments']['operation_id']='result.at_points'
        daemon.store.db.execute('update operations set metadata=? where operation_id=?',
                               (json.dumps(metadata),op[0]));daemon.store.db.commit()
    with pytest.raises((ValueError,ExecutionContractError)):
        _mint_from_actual_cure_response(daemon,response,project_id=PROJECT_ID,
                                       model_ref=daemon.parent,revision=1,original_receiver=original_receiver)
    assert daemon.calls==[]
