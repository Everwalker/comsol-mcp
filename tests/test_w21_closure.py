"""Control regressions for frozen W20/W21 defects; these are not native evidence."""
import json
from types import SimpleNamespace
import pytest
from comsol_mcp._operation_store import OperationStore
from comsol_mcp._observation_store import observation_context, register_observation, resolve_observation
from comsol_mcp._g3_w20_validation import validate_solution
from comsol_mcp._g3_w21 import BoundedOptimizer, ComputationBudget, ParameterCase, ParameterIndexTable, ResultCache, ALIASES
from comsol_mcp._execution_contract import ExecutionContractError


@pytest.fixture(autouse=True)
def controlled_native_identity(monkeypatch):
    monkeypatch.setattr('comsol_mcp._observation_store.solution_identity',
                        lambda *args: {'solution':'sol1','computation_date':123})
    monkeypatch.setattr(OperationStore, 'get_operation', lambda self, producer: {'status':'SUCCEEDED'})


def sample():
    return {'status':{'ok':True},'solution':'sol1','dataset':'dset1','values':[[[[303.0,304.0]]]],
            'field_array':{'axes':['expression','outer','inner','point']},'expressions':['T']}


def test_registered_observation_cannot_be_replaced_or_forged(tmp_path):
    worker=SimpleNamespace(project_root=tmp_path)
    store=OperationStore(tmp_path/'operations.sqlite')
    with observation_context(store, {'model_tag':'m1','generation':1},3,'real-w17-job'):
        ref=register_observation(worker,'m1',sample())
        result=validate_solution(worker,'m1',{'observation_ref':ref,'criteria':{'range':[300,310]},'values':None,'observations':None})
        assert result['numerical_verification_status']=='PASS'
        assert result['physical_validation_status']=='UNVERIFIED'
        overridden=validate_solution(worker,'m1',{'observation_ref':ref,'criteria':{'values':[305]}})
        assert overridden['scope']=='OBSERVATION_OVERRIDE_REFUSED'
        forged=validate_solution(worker,'m1',{'observation_ref':{'model_tag':'m1','observations':{'T':305}},'criteria':{'range':[300,310]}})
        assert forged['numerical_verification_status']=='FAIL'
        with pytest.raises(ExecutionContractError,match='another model'):
            resolve_observation(worker,'other',ref)
    with observation_context(store, {'model_tag':'m1','generation':1},4,'next-job'):
        with pytest.raises(ExecutionContractError,match='revision'):
            resolve_observation(worker,'m1',ref)
    store.close()


def test_changed_observation_artifact_refused(tmp_path):
    worker=SimpleNamespace(project_root=tmp_path);store=OperationStore(tmp_path/'ops.sqlite')
    with observation_context(store,{'model_tag':'m'},1,'producer'):
        ref=register_observation(worker,'m',sample())
        record=store.get_metadata('artifacts',ref['observation_id'])
        from pathlib import Path
        path=Path(record['artifact']['file_path']);path.write_text('{}')
        with pytest.raises(ExecutionContractError,match='changed'):
            resolve_observation(worker,'m',ref)
    store.close()


@pytest.mark.parametrize('raw',[{'status':'FAILED','obj':1},{'status':'UNKNOWN','obj':1},{'status':'COMPLETED'}, {'obj':float('nan')},{'obj':float('inf')}])
def test_invalid_candidate_not_best(raw):
    optimizer=BoundedOptimizer('obj',parameter_bounds={'k':(1,2)})
    candidate=optimizer.evaluate_candidate('bad',{'k':1},lambda p:raw)
    assert not candidate.is_feasible and optimizer.best_candidate is None
    assert candidate.objective_value is None


def test_bounds_rejected_before_evaluation_and_budget_limits_dispatch():
    called=[];o=BoundedOptimizer('obj',parameter_bounds={'k':(1,2)},budget=ComputationBudget(max_cases=1))
    with pytest.raises(ExecutionContractError):o.evaluate_candidate('bad',{'k':3},lambda p:called.append(p))
    assert not called
    o.evaluate_candidate('ok',{'k':1},lambda p:{'obj':1})
    with pytest.raises(ExecutionContractError):o.evaluate_candidate('extra',{'k':2},lambda p:called.append(p))
    assert not called


def test_cache_exact_small_numbers_and_exact_time():
    assert ResultCache.compute_key('m',{'x':1e-10},{},'6.4')!=ResultCache.compute_key('m',{'x':2e-10},{},'6.4')
    table=ParameterIndexTable('s',['x'])
    table.add_case(ParameterCase('a',{'x':1e-10},{'x':'m'},1,1,time_series={'T':[1,2]},time_points=[0,1]))
    assert table.get_by_params({'x':2e-10}) is None
    with pytest.raises(ExecutionContractError):table.query_slice('T',time_val=.5)


def test_stage_alias_does_not_replace_w12_checkpoint():
    from comsol_mcp._g3_ops import DISPATCH
    assert 'checkpoint.create' not in DISPATCH
    assert ALIASES['stage_checkpoint_create']=='stage.checkpoint_create'
    assert ALIASES['optimization.bounded_run']=='experiment.run'


def test_w13_applied_change_is_not_partial_failure():
    from comsol_mcp._w21_execution import successful
    assert successful({'ok':True,'status':'APPLIED','partial_change':True,'failed':[],'not_executed':[]})
    assert not successful({'ok':False,'status':'PARTIAL_FAILURE','partial_change':True,'failed':[{'error':'x'}]})


def test_missing_producer_and_replaced_solution_refused(tmp_path, monkeypatch):
    worker=SimpleNamespace(project_root=tmp_path);store=OperationStore(tmp_path/'ops.sqlite')
    with observation_context(store, {'model_tag':'m'}, 1, 'producer'):
        ref=register_observation(worker, 'm', sample())
        monkeypatch.setattr(OperationStore, 'get_operation', lambda *args: None)
        with pytest.raises(ExecutionContractError, match='producer'):
            resolve_observation(worker, 'm', ref)
        monkeypatch.setattr(OperationStore, 'get_operation', lambda *args: {'status':'SUCCEEDED'})
        monkeypatch.setattr('comsol_mcp._observation_store.solution_identity', lambda *args: {'computation_date':124})
        with pytest.raises(ExecutionContractError, match='replaced'):
            resolve_observation(worker, 'm', ref)
        resolve_observation(worker, 'm', ref, allow_historical=True)
    store.close()


def test_durable_cache_reuses_identical_record_and_rejects_corruption(tmp_path):
    store=OperationStore(tmp_path/'cache.sqlite')
    key=ResultCache.compute_key('configuration',{'k':400},{'rtol':1e-6},'6.4.0.293')
    ResultCache(store).store(key,{'status':'COMPLETED','objective':1})
    assert ResultCache(store).get(key)['objective']==1
    assert ResultCache(store).get(ResultCache.compute_key('configuration',{'k':400},{'rtol':1e-7},'6.4.0.293')) is None
    record=store.get_metadata('artifacts','w21cache:'+key)
    record['data']['objective']=0
    store.persist_artifact('w21cache:'+key,record)
    with pytest.raises(ExecutionContractError,match='envelope'):
        ResultCache(store).get(key)
    store.close()


def test_convergence_caller_arrays_never_certify_native():
    from comsol_mcp._g3_w20_validation import validate_convergence
    rows=[{'level':i+1,'error':e,'mesh_size_metric':1/(i+1)} for i,e in enumerate([.065,.028,.009])]
    result=validate_convergence(None,'nonexistent',{'cases':rows,'criteria':{'target_error':.1}})
    assert result['numerical_verification_status']=='UNVERIFIED'
    assert result['scope']=='EXTERNAL_DATA_ONLY'
    assert result['status']=='FAIL'
    from comsol_mcp._domain_outcome import classify
    assert classify('validate.convergence',result).state=='failed'
    assert not result['model_validated']


def test_convergence_references_must_have_distinct_native_settings(tmp_path,monkeypatch):
    from comsol_mcp import _convergence_store as convergence
    store=OperationStore(tmp_path/'convergence.sqlite')
    model_ref={'model_tag':'m','server_instance_id':'server','session_id':'session'}
    monkeypatch.setattr(convergence,'resolve_observation',lambda *a,**kw: ({},{}))
    refs=[]
    with observation_context(store,model_ref,1,'review-call'):
        for i in range(3):
            record={'kind':'w20_convergence','convergence_id':f'conv_{i}','producer':'real-sample',
                'model_ref':model_ref,'revision':1,'observation_ref':{},
                'elements':10,'max_element_volume':.001,'dofs':50,'tolerance':1e-5,
                'tlist':'0 .01 .03 .1','oracle':'transient_sine_diffusion','sample_points':[],
                'coordinate_unit':'m','source_identity':{'times':[0,.01,.03,.1],'computation_version':'6.4.0.293'}}
            record['sha256']=convergence.digest(record)
            store.persist_artifact(record['convergence_id'],record)
            refs.append({'convergence_id':record['convergence_id'],'sha256':record['sha256']})
        with pytest.raises(ExecutionContractError,match='Repeated native refinement settings'):
            convergence.resolve_cases(None,refs)
        with pytest.raises(ExecutionContractError,match='Repeated convergence case'):
            convergence.resolve_cases(None,[refs[0]]*3)
        with pytest.raises(ExecutionContractError,match='overrides refused'):
            convergence.resolve_cases(None,[dict(ref,error=0) for ref in refs])
        corrupted=[dict(refs[0],sha256='0'*64),*refs[1:]]
        with pytest.raises(ExecutionContractError,match='hash mismatch'):
            convergence.resolve_cases(None,corrupted)
    store.close()
