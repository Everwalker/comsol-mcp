import copy
import pytest
from tools import w24_stable_shape_transfer as transfer

def source_declaration():
    # Fictional references intentionally are not opened or treated as native proof.
    return {'schema':transfer.SCHEMA,'baselines':{case:{name:{'path':'/synthetic/not-a-native-shape/'+case+'/'+name,'bytes':1,'sha256':'1'*64} for name in transfer.REQUIRED_NATIVE_ARTIFACTS} for case in transfer.REQUIRED_BASELINES},'projection':{'method':'declared projection','error_metric':'declared relative volume error','error_value':0.0,'source_context':{'model_ref':{'synthetic':'source'},'revision':1,'worker_epoch':{'synthetic':'terminated'}},'target_context':{'model_ref':{'synthetic':'newtarget'},'revision':0,'worker_epoch':{'synthetic':'new'}}}}

def test_no_shape_is_invented_by_requirements():
    r=transfer.stable_shape_transfer_requirements()
    assert not r['native_shape_available'] and r['original_sensitivity_slots']==14
    assert r['science_acceptance']=='NOT_ACCEPTED' and r['Study_solver_authorized']==0
    assert 'raw_phase_field_grid' in r['required_artifacts'] and 'wall_segments' in r['required_artifacts']

def test_plausible_caller_declaration_never_mints_native_authority():
    r=transfer.validate_source_transfer_declaration(source_declaration())
    assert r['native_admission']=='REFUSED' and not r['native_shape_available']
    assert r['science_acceptance']=='NOT_ACCEPTED'

@pytest.mark.parametrize('mutation',['missing_step','analytic_profile','missing_volume','missing_origin','no_wall','nan_error','same_epoch','extra_PASS'])
def test_missing_native_contract_or_substitute_is_refused(mutation):
    d=source_declaration();case=d['baselines']['baseline-flat']
    if mutation=='missing_step':d['baselines'].pop('baseline-step')
    elif mutation=='analytic_profile':case['final_native_profile']={'spherical_cap_radius':1}
    elif mutation=='missing_volume':case.pop('full_history_volume')
    elif mutation=='missing_origin':case.pop('immutable_operations_origin')
    elif mutation=='no_wall':case['wall_segments']=[]
    elif mutation=='nan_error':d['projection']['error_value']=float('nan')
    elif mutation=='same_epoch':d['projection']['target_context']=copy.deepcopy(d['projection']['source_context'])
    elif mutation=='extra_PASS':d['science_acceptance']='PASS'
    with pytest.raises(transfer.StableShapeTransferError):transfer.validate_source_transfer_declaration(d)
