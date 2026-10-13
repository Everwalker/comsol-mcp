"""Actual production binding/parser, real SessionLedger, zero JVM controls."""
import copy
import json
from pathlib import Path
from types import SimpleNamespace
import pytest
from tools import run_native_w24_static_shape_setup_campaign as c
from tools import run_native_w24_cure_science as science
from tools.w24_native_epoch_capability import _capture_live_worker_binding
from tests.w24_live_binding_fixture import configure_backend,bind_ledger

R6=Path('/private/tmp/cv3n-20261014-r6-423e078d/evidence/SUMMARY.json')

def _case():
    backend=configure_backend(SimpleNamespace(),project_id='p',session_id='s',server_id='server',epoch=2)
    ref={'session_id':'s','server_instance_id':'server','generation':1,'model_tag':'m'}
    bind_ledger(backend,ref)
    anchor=_capture_live_worker_binding(backend,project_id='p',session_id='s')
    record={'project_id':'p','session_id':'s','model_ref':ref,'revision':0}
    args={'project_id':'p','session_id':'s','server_instance_id':'server','worker_epoch':2,
          'worker_instance_id':'synthetic-w24-worker','receiver_anchor':anchor,'backend':backend}
    return backend,anchor,record,args

def test_model_incarnation_one_connection_two_real_ledger_positive():
    backend,anchor,record,args=_case()
    assert c.validate_current_epoch_binding(record,**args)==record
    session={'session_id':'s','server_instance_id':'server','worker_epoch':2,'worker_instance_id':'synthetic-w24-worker'}
    result=c._model_create_binding({'success':True,'execution':record},project_id='p',session=session,receiver_anchor=anchor,backend=backend)
    assert result.model_ref['generation']==1
    assert science._model_ref_matches_worker_epoch(result,backend.worker_identity,anchor=anchor,backend=backend)

@pytest.mark.parametrize('fault',['modelgeneration','workerobject','backendobject','instance','epoch','endpoint','server','project','session','boolgeneration','boolrevision','jsonanchor','runtime_only','coordinated_rewrite','during_lookup','ledger_swap','during_model_rewrite'])
def test_original_worker_and_model_identity_fail_closed_even_coordinated(fault):
    backend,anchor,record,args=_case()
    if fault=='modelgeneration':record['model_ref']['generation']=2
    elif fault=='boolgeneration':record['model_ref']['generation']=True
    elif fault=='boolrevision':record['revision']=True
    elif fault=='workerobject':backend.worker=SimpleNamespace(runtime_metadata=backend.worker.runtime_metadata)
    elif fault=='backendobject':args['backend']=copy.copy(backend)
    elif fault in ('instance','epoch','endpoint','server'):
        key={'instance':'worker_instance_id','epoch':'connection_epoch','endpoint':'endpoint','server':'server_instance_id'}[fault]
        backend.worker_identity[key]='changed'
    elif fault=='project':record['project_id']='other'
    elif fault=='session':record['session_id']='other'
    elif fault=='jsonanchor':args['receiver_anchor']=dict(anchor._identity)
    elif fault=='runtime_only':backend.worker.runtime_metadata=lambda:{'instance_id':'other','generation':2,'server':'127.0.0.1:56388','connected':True}
    elif fault=='coordinated_rewrite':
        backend.worker_identity['connection_epoch']=3
        args['worker_epoch']=3
        backend.worker.runtime_metadata=lambda:{'instance_id':'synthetic-w24-worker','generation':3,'server':'127.0.0.1:56388','connected':True}
    elif fault=='ledger_swap':
        backend.service.ledger=copy.copy(backend.service.ledger)
    elif fault=='during_model_rewrite':
        def rewrite_model(ref):
            record['model_ref']['generation']=2
            backend.service.ledger.rebind_model('m')
            return {'attribution':'PROJECT_BOUND','project_id':'p'}
        backend.model_project_binding=rewrite_model
    elif fault=='during_lookup':
        def rewrite(ref):
            backend.worker_identity['connection_epoch']=3
            return {'attribution':'PROJECT_BOUND','project_id':'p'}
        backend.model_project_binding=rewrite
    with pytest.raises(c.CandidateError):c.validate_current_epoch_binding(record,**args)


def test_exact_R6_observed_identity_parser_and_ref1_epoch2_reproduction():
    if not R6.is_file():pytest.skip('actual preserved R6 input unavailable')
    original=R6.read_bytes();summary=json.loads(original);session=summary['actual_attach']
    observed=c._session_identity({'success':True,'data':session},project_id=session['project_id'],expected_port=session['endpoint']['port'])
    assert observed['remote_engine_build'] is None
    assert observed['remote_engine_build_source']=='NOT_REPORTED'
    assert observed['observed_engine_identity']['normalized_build']=='293'
    assert observed['observed_engine_identity']['build_source']=='remote_version_text'
    backend=configure_backend(SimpleNamespace(),project_id=session['project_id'],session_id=session['session_id'],server_id=session['server_instance_id'],epoch=session['worker_epoch'],instance=session['worker_instance_id'],endpoint='127.0.0.1:'+str(session['endpoint']['port']))
    ref=summary['saved_model']['model_ref'];assert ref['generation']==1 and session['worker_epoch']==2
    bind_ledger(backend,ref)
    anchor=_capture_live_worker_binding(backend,project_id=session['project_id'],session_id=session['session_id'])
    record={'project_id':session['project_id'],'session_id':session['session_id'],'model_ref':ref,'revision':summary['saved_model']['source_revision']}
    assert c.validate_current_epoch_binding(record,project_id=session['project_id'],session_id=session['session_id'],server_instance_id=session['server_instance_id'],worker_epoch=session['worker_epoch'],worker_instance_id=session['worker_instance_id'],receiver_anchor=anchor,backend=backend)==record
    assert R6.read_bytes()==original

@pytest.mark.parametrize('version,build,source',[
    ('COMSOL Multiphysics 6.3 (开发版本: 293)',None,'NOT_REPORTED'),
    ('COMSOL Multiphysics 6.4 (开发版本: 292)',None,'NOT_REPORTED'),
    ('COMSOL Multiphysics 6.4',None,'NOT_REPORTED'),
    ('6.4',None,'NOT_REPORTED'),
    ('COMSOL Multiphysics 6.4 (Build: 293) 6.3',None,'NOT_REPORTED'),
    ('COMSOL Multiphysics 6.4 (开发版本: 293)','292','Worker.getBuild'),
    ('COMSOL Multiphysics 6.4 (开发版本: 293)','293','NOT_REPORTED'),
])
def test_remote_identity_wrong_missing_or_conflicting_build_rejected(version,build,source):
    data={'project_id':'p','session_id':'s','server_instance_id':'server','worker_instance_id':'worker','worker_epoch':2,
          'endpoint':{'host':'127.0.0.1','port':56388},'observed_peer':{'address':'127.0.0.1','port':56388},
          'remote_engine_version':version,'remote_engine_build':build,'remote_engine_build_source':source}
    with pytest.raises(c.CandidateError):c._session_identity({'success':True,'data':data},project_id='p',expected_port=56388)


def test_captured_model_incarnation_cannot_be_coordinately_rebound():
    backend,anchor,record,args=_case()
    c.validate_current_epoch_binding(record,**args)
    backend.service.ledger.rebind_model('m')
    record['model_ref']['generation']=2
    with pytest.raises(c.CandidateError):c.validate_current_epoch_binding(record,**args)
